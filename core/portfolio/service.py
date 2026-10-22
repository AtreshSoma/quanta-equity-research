"""Joins broker data, QUANTA analysis, portfolio analytics and guidance into one view.

The web layer injects two callables so this module never imports Flask or the
engine directly:

* ``analyze(symbol, account_size) -> (analysis_dict | None, error_detail | None)``
  runs (or serves from cache) the SAME research analysis the Research page uses.
* ``market_returns(symbols) -> DataFrame | None`` daily returns for volatility
  and correlation.

Each position in the view keeps four separately tagged blocks — ``broker``,
``market``, ``analysis`` (the authoritative Decision and the tear-sheet facts it
rests on) and ``interpretation`` (the Decision's grounded AI layer, if any) —
plus ``guidance``, the portfolio-context layer's reading of that Decision.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import date, datetime, time as _time, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.market_calendar import is_trading_day, last_completed_session, now_new_york
from engine.thresholds import PORTFOLIO as _PF

from . import analytics as an
from . import guidance as gd
from .fund_events import FUND_INSTRUMENT_TYPES
from .models import ConnectionState, DataIssue, Portfolio, finite
from .provider import AuthExpired, PortfolioProvider, ProviderError

AnalyzeFn = Callable[[str, Optional[float]], Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]]
ReturnsFn = Callable[[List[str]], Any]

SOURCE_TAGS = {
    "broker": "Account data",
    "market": "Market data",
    "quanta": "QUANTA analysis",
    "ai": "AI interpretation",
}
ANALYSIS_CACHE_SECONDS = 300

_OPEN, _CLOSE = _time(9, 30), _time(16, 0)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_open(now: Optional[datetime] = None) -> bool:
    """Is the US regular session open at ``now``?"""
    ny_now = now_new_york(now or datetime.now(timezone.utc))
    return is_trading_day(ny_now.date()) and _OPEN <= ny_now.time() < _CLOSE


def prices_moving(now: Optional[datetime] = None) -> bool:
    """Can US quotes still move at ``now`` (regular or extended hours on a trading day)?"""
    ny_now = now_new_york(now or datetime.now(timezone.utc))
    start, end = _PF.PRICES_MOVE_HOURS
    return is_trading_day(ny_now.date()) and _time(start) <= ny_now.time() < _time(end)


def quote_freshness(as_of: Optional[str], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Is a broker quote current for the session we are in?

    "delayed" means the quote is from today's session but was read more than
    ``PORTFOLIO.QUOTE_LIVE_MINUTES`` ago (the page has not refreshed since). It
    still values the book; only a quote from an earlier session, or one with no
    time, is too old to act on (audit 2026-10-21, F3).
    """
    now = now or datetime.now(timezone.utc)
    ts = _parse_iso(as_of)
    if ts is None:
        return {"status": "unknown", "age_minutes": None, "text": "Quote time not reported"}
    age = max(0.0, (now - ts).total_seconds() / 60.0)
    ny_now = now_new_york(now)
    ny_q = now_new_york(ts)
    if session_open(now):
        if age <= _PF.QUOTE_LIVE_MINUTES:
            return {"status": "live", "age_minutes": age, "text": "Live"}
        if ny_q.date() == ny_now.date():
            clock = ny_q.strftime("%I:%M %p").lstrip("0")
            return {"status": "delayed", "age_minutes": age,
                    "text": f"Price from {clock} ET, {age:.0f} min ago"}
        return {"status": "stale", "age_minutes": age, "text": f"From {ny_q.date().isoformat()}"}
    if ny_q.date() >= last_completed_session(now):
        return {"status": "last_close", "age_minutes": age, "text": "Latest session"}
    return {"status": "stale", "age_minutes": age, "text": f"From {ny_q.date().isoformat()}"}


def _sub(d: Optional[Dict[str, Any]], keys: List[str]) -> Dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in keys if k in d}


def compact_analysis(res: Dict[str, Any]) -> Dict[str, Any]:
    """The parts of a research result the portfolio page shows. Decision is kept whole."""
    from .fund_events import portfolio_decision
    return {
        "decision": portfolio_decision(res),
        "quote_type": res.get("quote_type"),
        "company_name": res.get("company_name"),
        "sector": res.get("sector"),
        "industry": res.get("industry"),
        "asset_type": res.get("asset_type"),
        "is_etf": res.get("is_etf"),
        "current_price": res.get("current_price"),
        "analysis_timestamp": res.get("analysis_timestamp"),
        "market_timestamp": res.get("market_timestamp"),
        "data_provider": res.get("data_provider"),
        "fast_mode": bool(res.get("fast_mode")),
        "used_cached_data": bool(res.get("used_cached_data")),
        "cache": _sub(res.get("cache"), ["hit", "age_seconds", "stale"]),
        "confidence_score": res.get("confidence_score"),
        "confidence_level": res.get("confidence_level"),
        "thesis": _sub(res.get("thesis"), ["summary", "bull_case", "bear_case", "catalysts", "risks"]),
        "fundamental": _sub(res.get("fundamental"), [
            "pe_ratio", "forward_pe", "peg_ratio", "roe", "gross_margin", "operating_margin", "net_margin",
            "revenue_growth_yoy", "eps_growth_yoy", "debt_to_equity", "fcf_yield", "dividend_yield",
            "quality_score", "is_etf"]),
        "valuation": _sub(res.get("valuation"), [
            "dcf_fair_value", "dcf_upside", "dcf_status", "valuation_label", "target_base",
            "target_confidence", "target_source_count"]),
        "market_intel": _sub(res.get("market_intel"), [
            "analyst_rating", "target_mean", "implied_upside", "analyst_count", "next_earnings_date"]),
        "technical": _sub(res.get("technical"), [
            "rsi", "trend_direction", "adx", "golden_cross", "death_cross", "support_level",
            "resistance_level", "week_52_high", "week_52_low", "momentum_score"]),
        "risk": _sub(res.get("risk"), [
            "annualized_volatility", "beta", "beta_status", "max_drawdown", "risk_grade", "var_95_daily"]),
        "catalysts": _sub(res.get("catalysts"), [
            "earnings_date", "days_to_earnings", "last_earnings_result", "last_earnings_surprise_pct",
            "ex_dividend_date", "near_term_catalyst"]),
        "position_sizing": _sub(res.get("position_sizing"), ["recommended_shares", "position_value"]),
        "account_context": res.get("account_context"),
        "risk_warnings": list(res.get("risk_warnings") or [])[:5],
    }


class PortfolioService:
    def __init__(self, provider: PortfolioProvider, analyze: AnalyzeFn,
                 market_returns: Optional[ReturnsFn] = None,
                 policy: Optional[an.PortfolioPolicy] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.provider = provider
        self._analyze = analyze
        self._returns_fn = market_returns
        self.policy = policy or an.PortfolioPolicy()
        self._clock = clock
        self._lock = threading.RLock()
        self._account: Optional[str] = None
        self._portfolio: Optional[Portfolio] = None
        self._analysis: Dict[str, Dict[str, Any]] = {}
        self._job: Dict[str, Any] = {}
        self._returns: Any = None
        self._returns_note = ""
        self._returns_at = 0.0
        self._returns_symbols: Tuple[str, ...] = ()
        self._load_error: Optional[Dict[str, str]] = None
        # Set by the web layer: Portfolio Review, exposure, events and plan logic, which
        # read this service's own view. None in tests that only exercise the core.
        self.insights: Any = None

    # -- connection ---------------------------------------------------------

    def disconnect(self) -> None:
        self.provider.disconnect()
        with self._lock:
            self._job["cancelled"] = True
            self._account = None
            self._portfolio = None
            self._analysis = {}
            self._returns = None
            self._returns_at = 0.0
            self._returns_symbols = ()
            self._load_error = None

    def accounts(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self.provider.list_accounts()]

    # -- refresh ------------------------------------------------------------

    def refresh(self, account_handle: str, run_analysis: bool = True,
                start_thread: bool = True) -> Dict[str, Any]:
        try:
            pf = self.provider.load_portfolio(account_handle)
        except ProviderError as exc:
            with self._lock:
                self._load_error = {"code": exc.code, "message": str(exc)}
                if isinstance(exc, AuthExpired):
                    self._portfolio, self._analysis = None, {}
            raise
        symbols = [p.symbol for p in pf.positions]
        size = self._account_size(pf)
        now = time.time()
        with self._lock:
            self._job["cancelled"] = True               # supersede a running job
            old = self._analysis if self._account == account_handle else {}
            reusable = {}
            for sym in symbols:
                entry = old.get(sym) or {}
                age = now - entry.get("finished_at", 0)
                result = entry.get("result") or {}
                decision = result.get("decision") or {}
                stable_error = ((entry.get("error") or {}).get("status") == "insufficient_data"
                                and (entry.get("error") or {}).get("retryable") is False)
                valid_result = (entry.get("state") == "done" and decision.get("action") != "INVALID"
                                and (decision.get("freshness") or {}).get("status") != "stale")
                if (0 <= age < ANALYSIS_CACHE_SECONDS and entry.get("account_size") == size
                        and (valid_result or stable_error)):
                    reusable[sym] = dict(entry)
                    if result:
                        reusable[sym]["result"] = dict(result, used_cached_data=True,
                                                        cache={"hit": True, "age_seconds": age, "stale": False})
            self._account = account_handle
            self._portfolio = pf
            self._load_error = None
            # Keep last results for names still held, flagged until re-analysed.
            self._analysis = {s: reusable.get(s, {"state": "pending"}) for s in symbols}
            pending_symbols = [s for s in symbols if s not in reusable]
            job = {"id": uuid.uuid4().hex[:8], "started_at": time.time(), "finished_at": None,
                   "total": len(symbols), "done": len(reusable), "cancelled": False}
            self._job = job
        if run_analysis and pending_symbols:
            if start_thread:
                threading.Thread(target=self._run_job, args=(job, pending_symbols, pf), daemon=True).start()
            else:
                self._run_job(job, pending_symbols, pf)
        else:
            with self._lock:
                job["finished_at"] = time.time()
            self._record_journal()
        return self.view()

    def _account_size(self, pf: Portfolio) -> Optional[float]:
        total = an.total_value(
            [an.Holding(p.symbol, p.quantity, p.market_value) for p in pf.positions],
            pf.account.cash, pf.account.portfolio_value)
        return total if total and total > 0 else None

    def _run_job(self, job: Dict[str, Any], symbols: List[str], pf: Portfolio) -> None:
        all_symbols = tuple(sorted(p.symbol for p in pf.positions))
        reuse_returns = (all_symbols == self._returns_symbols
                         and time.time() - self._returns_at < ANALYSIS_CACHE_SECONDS)
        if self._returns_fn is not None and not reuse_returns:
            try:
                rets = self._returns_fn(list(all_symbols))
                note = "" if rets is not None else "Daily returns unavailable; volatility and correlation not computed."
            except Exception as exc:  # noqa: BLE001 - analytics are optional, never fatal
                rets, note = None, f"Daily returns unavailable ({type(exc).__name__})."
            with self._lock:
                if not job.get("cancelled"):
                    self._returns, self._returns_note = rets, note
                    self._returns_at = time.time() if rets is not None else 0.0
                    self._returns_symbols = all_symbols
        size = self._account_size(pf)
        for sym in symbols:
            with self._lock:
                if job.get("cancelled"):
                    return
                self._analysis[sym]["state"] = "running"
            try:
                res, err = self._analyze(sym, size)
            except Exception as exc:  # noqa: BLE001 - one holding's failure must not end the job
                res, err = None, {"status": "engine_error", "title": f"{type(exc).__name__} during analysis"}
            with self._lock:
                if job.get("cancelled"):
                    return
                entry = self._analysis.setdefault(sym, {})
                entry["finished_at"] = time.time()
                entry["account_size"] = size
                if res and not err:
                    entry.update(state="done", result=compact_analysis(res), error=None)
                else:
                    entry.update(state="error", result=None,
                                 error=_sub(err, ["status", "category", "title", "error_message",
                                                  "suggested_action", "retryable"]) or
                                 {"title": "Analysis failed"})
                job["done"] += 1
        with self._lock:
            job["finished_at"] = time.time()
        self._record_journal()

    # -- view ---------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        st = self.provider.status()
        with self._lock:
            pf = self._portfolio
            st["account"] = pf.account.to_dict() if pf else None
            st["portfolio_retrieved_at"] = pf.retrieved_at if pf else None
            st["load_error"] = self._load_error
            st["analysis"] = self._job_status()
        return st

    def _job_status(self) -> Dict[str, Any]:
        j = self._job
        if not j:
            return {"state": "idle"}
        state = ("cancelled" if j.get("cancelled") and not j.get("finished_at")
                 else "complete" if j.get("finished_at") else "running")
        return {"state": state, "done": j.get("done", 0), "total": j.get("total", 0),
                "started_at": _iso(j.get("started_at")), "finished_at": _iso(j.get("finished_at"))}

    def holdings_snapshot(self) -> Optional[Dict[str, Any]]:
        """The loaded broker positions, read-only and from memory (audit B-89):
        ``{"retrieved_at", "account_value", "positions": {SYMBOL: {"quantity",
        "market_value"}}}``, or None before any portfolio is loaded. Symbols use
        the scanner's spelling (``BRK.B`` -> ``BRK-B``). Never calls the broker."""
        with self._lock:
            pf = self._portfolio
        if pf is None:
            return None
        positions: Dict[str, Dict[str, Any]] = {}
        for p in pf.positions:
            if not p.quantity:
                continue
            sym = str(p.symbol or "").strip().upper().replace(".", "-")
            mv = p.market_value
            prior = positions.get(sym)
            if prior:
                prior["quantity"] += p.quantity
                prior["market_value"] = (None if prior["market_value"] is None or mv is None
                                         else prior["market_value"] + mv)
            else:
                positions[sym] = {"quantity": p.quantity, "market_value": mv}
        return {"retrieved_at": pf.retrieved_at, "account_value": pf.account.portfolio_value,
                "positions": positions}

    def view(self) -> Dict[str, Any]:
        status = self.status()
        with self._lock:
            pf = self._portfolio
            analysis = {k: dict(v) for k, v in self._analysis.items()}
            returns, returns_note = self._returns, self._returns_note
        if pf is None:
            return {"status": status, "portfolio": None, "source_tags": SOURCE_TAGS}
        last_calls = self.insights.last_calls() if self.insights is not None else {}
        view = build_view(pf, analysis, returns, returns_note, self.policy, status, self._clock(),
                          last_calls=last_calls)
        if self.insights is not None:
            view["portfolio"]["journal"] = self.insights.journal_info()
        return view

    def _record_journal(self) -> None:
        """Journal the calls once an analysis job has finished, not on every read.

        A GET of the view must not write (audit 2026-10-21, F10); the end of a job
        is the one moment the calls are complete and current.
        """
        if self.insights is None:
            return
        try:
            v = self.view()
            if v.get("portfolio"):
                self.insights.record_journal(v["portfolio"])
        except Exception:  # noqa: BLE001 - the journal is a record, never a reason to fail a job
            pass

    def whatif(self, changes: Any) -> Dict[str, Any]:
        """Portfolio metrics after selling part or all of some holdings to cash.

        The same inputs and rules as the view (``compute_metrics``: risk points and
        drivers, concentration, sectors, beta, volatility, stops), re-run on the changed
        book, with the current book computed the same way beside it. Reductions only:
        each change is ``{symbol, delta_value}`` with ``-market_value <= delta_value <= 0``;
        the proceeds go to cash, so the total is unchanged. Arithmetic at today's
        prices, not orders.
        """
        with self._lock:
            pf = self._portfolio
            analysis = {k: dict(v) for k, v in self._analysis.items()}
            returns = self._returns
        if pf is None:
            return {"available": False, "reason": "Not connected."}
        holdings, _ = _join_holdings(pf, analysis, now_new_york(self._clock()).date())
        wanted: Dict[str, float] = {}
        for ch in (changes if isinstance(changes, list) else [])[:100]:
            if not isinstance(ch, dict):
                continue
            h = next((x for x in holdings if x.symbol == ch.get("symbol")), None)
            d = finite(ch.get("delta_value"))
            if h is None or h.value is None or d is None or d > 0:
                continue
            wanted[h.symbol] = max(-h.value, d)
        cash = pf.account.cash
        after: List[an.Holding] = []
        for h in holdings:
            d = wanted.get(h.symbol)
            if not d:
                after.append(h)
                continue
            keep = (h.value + d) / h.value if h.value else 0.0
            after.append(an.Holding(**{**h.__dict__, "quantity": h.quantity * keep, "value": h.value + d,
                                       "unrealized_pnl": None if h.unrealized_pnl is None else h.unrealized_pnl * keep}))
        freed = -sum(wanted.values())
        cash_after = None if cash is None else cash + freed
        today = now_new_york(self._clock()).date()

        def summary(book: List[an.Holding], c: Optional[float]) -> Dict[str, Any]:
            m = an.compute_metrics(book, c, pf.account.portfolio_value, returns, self.policy, today)
            return {"risk": m.risk, "concentration": m.concentration, "sectors": m.sectors,
                    "cash": c, "cash_weight": m.cash_weight, "beta": m.beta, "weights": m.weights,
                    "total_value": m.total_value}
        return {"available": True, "changes": [{"symbol": s, "delta_value": d} for s, d in wanted.items()],
                "freed": freed, "before": summary(holdings, cash), "after": summary(after, cash_after),
                "basis": "Selling at today's prices, proceeds held as cash; the same rules as the portfolio view. "
                         "Arithmetic, not orders."}


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _decision_of(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res = entry.get("result") or {}
    return res.get("decision")


def is_fund_result(res: Optional[Dict[str, Any]]) -> bool:
    """A typed fund (ETF, mutual fund, closed-end fund) in the research result."""
    res = res or {}
    kind = str(res.get("quote_type") or res.get("asset_type") or "").strip().upper()
    return kind in FUND_INSTRUMENT_TYPES or kind == "ETN" or bool(res.get("is_etf") and kind != "INDEX")


def _future_date(value: Any, today: Optional[date]) -> Optional[str]:
    """An ISO date that is today or later; a past "next earnings" date is stale, not upcoming."""
    if not value:
        return None
    iso = str(value)[:10]
    if today is not None:
        try:
            if date.fromisoformat(iso) < today:
                return None
        except ValueError:
            return None
    return iso


def _join_holdings(pf: Portfolio, analysis: Dict[str, Dict[str, Any]], today: Optional[date] = None
                   ) -> Tuple[List[an.Holding], Dict[str, Dict[str, Any]]]:
    """Each position joined with its market facts and QUANTA levels (the metrics' inputs)."""
    holdings: List[an.Holding] = []
    facts: Dict[str, Dict[str, Any]] = {}
    for p in pf.positions:
        entry = analysis.get(p.symbol) or {}
        res = entry.get("result") or {}
        dec = res.get("decision") or {}
        sector, sector_src = (res.get("sector"), "quanta") if res.get("sector") else (p.sector, "broker")
        earnings = _future_date(p.earnings_date, today)
        earnings_src = "broker" if earnings else ""
        if not earnings:
            earnings = (_future_date((res.get("catalysts") or {}).get("earnings_date"), today)
                        or _future_date((res.get("market_intel") or {}).get("next_earnings_date"), today))
            earnings_src = "market" if earnings else ""
        beta = (res.get("risk") or {}).get("beta")
        stop = (dec.get("levels") or {}).get("stop") if dec.get("action") not in (None, "INVALID") else None
        facts[p.symbol] = {"sector": sector or "", "sector_source": sector_src if sector else "",
                           "industry": res.get("industry") or p.industry or "",
                           "earnings_date": str(earnings)[:10] if earnings else None,
                           "earnings_source": earnings_src, "beta": beta,
                           "beta_status": (res.get("risk") or {}).get("beta_status"), "stop": stop,
                           "is_fund": is_fund_result(res),
                           "risk_grade": str(dec.get("risk_grade") or "")}
        holdings.append(an.Holding(
            symbol=p.symbol, quantity=p.quantity, value=p.market_value, price=p.last_price,
            sector=facts[p.symbol]["sector"], industry=facts[p.symbol]["industry"], beta=beta,
            earnings_date=facts[p.symbol]["earnings_date"], stop=stop, unrealized_pnl=p.unrealized_pnl,
            is_fund=facts[p.symbol]["is_fund"]))
    return holdings, facts


def build_view(pf: Portfolio, analysis: Dict[str, Dict[str, Any]], returns: Any, returns_note: str,
               policy: an.PortfolioPolicy, status: Dict[str, Any], now: datetime,
               last_calls: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    today = now_new_york(now).date()
    issues: List[DataIssue] = list(pf.issues)
    holdings, facts = _join_holdings(pf, analysis, today)

    day_changes = [p.day_change for p in pf.positions]
    day_change = sum(day_changes) if day_changes and all(d is not None for d in day_changes) else None
    metrics = an.compute_metrics(holdings, pf.account.cash, pf.account.portfolio_value, returns,
                                 policy, today, day_change)
    total = metrics.total_value or 0.0
    recon = pf.reconciliation(session_open=prices_moving(now))
    if recon["status"] == "mismatch":
        issues.append(DataIssue("TOTALS_MISMATCH",
                                f"Positions plus cash (${recon['computed']:,.2f}) differ from Robinhood's total "
                                f"(${recon['reported']:,.2f}) by ${abs(recon['difference']):,.2f} "
                                f"({recon['difference_pct']:.3g}%).", "portfolio", "error"))
    if returns_note:
        issues.append(DataIssue("RETURNS_UNAVAILABLE", returns_note, "portfolio", "info"))

    sector_w = {s["sector"]: s.get("weight") for s in metrics.sectors}
    pairs = metrics.volatility.get("correlated_pairs") or []
    ctx = gd.PortfolioContext(total_value=metrics.total_value, buying_power=pf.account.buying_power,
                              stop_risk_pct=metrics.stops.get("loss_pct"), policy=policy, today=today,
                              last_calls=dict(last_calls or {}))
    risk_share = metrics.volatility.get("risk_share") or {}
    delayed: List[float] = []
    if status.get("analysis", {}).get("state") == "running":
        ctx.incomplete_reasons.append("Portfolio analysis is still running")
    if recon["status"] != "ok" or any(i.severity == "error" for i in issues):
        ctx.incomplete_reasons.append(gd.ACCOUNT_UNRECONCILED)
    if (metrics.stops.get("coverage") or 0) < 1 - 1e-9:
        ctx.incomplete_reasons.append("Portfolio stop-risk coverage is incomplete")
    if (metrics.volatility.get("coverage") or 0) < 1 - 1e-9:
        ctx.incomplete_reasons.append("Portfolio correlation coverage is incomplete")

    rows: List[Dict[str, Any]] = []
    guidance_list = []
    stale_quotes = 0
    oldest_quote: Optional[str] = None
    for p, h in zip(pf.positions, holdings):
        f = facts[p.symbol]
        entry = analysis.get(p.symbol) or {}
        res = entry.get("result")
        dec = _decision_of(entry)
        weight = metrics.weights.get(p.symbol)
        corr = an.correlated_with(p.symbol, pairs)
        cluster = (weight or 0) + sum(metrics.weights.get(s, 0) for s in corr) if corr else None
        qf = quote_freshness(p.quote_as_of, now)
        if qf["status"] == "stale":
            stale_quotes += 1
            issues.append(DataIssue("QUOTE_STALE", f"{p.symbol}: Robinhood quote is {qf['text'].lower()}.",
                                    p.symbol))
        elif qf["status"] == "delayed" and qf.get("age_minutes") is not None:
            delayed.append(qf["age_minutes"])
        if p.quote_as_of and (oldest_quote is None or p.quote_as_of < oldest_quote):
            oldest_quote = p.quote_as_of
        pctx = gd.PositionContext(
            symbol=p.symbol, quantity=p.quantity, value=p.market_value, weight=weight, price=p.last_price,
            average_cost=p.average_cost, unrealized_pnl=p.unrealized_pnl,
            unrealized_pnl_pct=p.unrealized_pnl_pct, sector=f["sector"],
            sector_weight=sector_w.get(f["sector"] or "Unclassified"),
            correlated_with=corr, cluster_weight=cluster, earnings_date=f["earnings_date"],
            tax_lots=None if p.tax_lots is None else [lot.to_dict() for lot in p.tax_lots],
            is_fund=f["is_fund"], risk_share=risk_share.get(p.symbol), risk_grade=f["risk_grade"])

        analysis_error = entry.get("error") if entry.get("state") == "error" else None
        if entry.get("state") in ("pending", "running") and not res:
            analysis_error = {"title": "analysis in progress"}
        if p.last_price is None:
            analysis_error = {"title": "no Robinhood quote, so the position cannot be valued"}
        elif qf["status"] == "stale":
            analysis_error = {"title": "the Robinhood quote is from an earlier session"}
        elif qf["status"] == "unknown":
            analysis_error = {"title": "Robinhood did not say when its quote was taken"}

        no_call = _no_call_cause(p, qf, entry, res, dec, f["is_fund"])
        impacts = _impacts(holdings, total, pf.account.cash, p, dec, weight, policy, pf.account.buying_power, today)
        thesis = (res or {}).get("thesis") or {}
        g = gd.guide_position(pctx, ctx, dec, analysis_error, impacts,
                              case_for=[str(x) for x in (thesis.get("bull_case") or []) if x],
                              case_against=[str(x) for x in (thesis.get("bear_case") or []) if x]
                              + [str(x) for x in ((res or {}).get("risk_warnings") or []) if x])
        if entry.get("state") in ("pending", "running") and not res:
            g.unavailable_reason = g.key_reason = "Analysis in progress"
        guidance_list.append(g)
        rows.append({
            "symbol": p.symbol,
            "broker": dict(p.to_dict(), weight=weight, quote_freshness=qf),
            "market": {"sector": f["sector"], "sector_source": f["sector_source"], "industry": f["industry"],
                       "beta": f["beta"], "beta_status": f["beta_status"],
                       "earnings_date": f["earnings_date"], "earnings_source": f["earnings_source"],
                       "correlated_with": corr},
            "analysis": {"state": entry.get("state", "pending"), "error": entry.get("error"),
                         "finished_at": _iso(entry.get("finished_at")),
                         **({k: v for k, v in (res or {}).items() if k != "decision"}),
                         "decision": dec},
            "interpretation": (dec or {}).get("interpretation"),
            "guidance": g.to_dict(),
            "no_call": no_call if g.status != "ok" else None,
            "is_fund": f["is_fund"],
            # Share of the portfolio's variance this holding carries (None: not measured).
            "risk_share": risk_share.get(p.symbol),
        })

    rows.sort(key=lambda r: (r["guidance"]["priority"], -(r["broker"]["weight"] or 0)))
    if delayed:
        issues.append(DataIssue("QUOTES_DELAYED",
                                f"Prices were last refreshed {max(delayed):.0f} min ago; weights and today's "
                                "change are as of then. Refresh for current prices.", "portfolio", "info"))
    failed = sum(1 for e in analysis.values() if e.get("state") == "error")
    if failed:
        issues.append(DataIssue("ANALYSIS_FAILED", f"QUANTA could not analyse {failed} holding(s); "
                                                   "they show no call.", "portfolio"))

    errors = [i for i in issues if i.severity == "error"]
    data_state = "partial" if errors else "complete"
    acct = pf.account
    overview = {
        "total_value": metrics.total_value,
        "reported_total": acct.portfolio_value,
        "cash": acct.cash,
        "buying_power": acct.buying_power,
        "invested_value": metrics.invested_value,
        "day_change": day_change,
        "day_change_pct": (day_change / (total - day_change) * 100.0
                           if day_change is not None and total and total != day_change else None),
        "unrealized_pnl": metrics.unrealized_pnl,
        "realized_pnl": acct.realized_pnl,
        "realized_pnl_period": acct.realized_pnl_period,
        "holdings": len(pf.positions),
        "concentration": metrics.concentration,
        "risk": metrics.risk,
    }
    return {
        "status": status,
        "source_tags": SOURCE_TAGS,
        "portfolio": {
            "provider": pf.provider,
            "account": acct.to_dict(),
            "retrieved_at": pf.retrieved_at,
            "data_state": data_state,
            "reconciliation": recon,
            "freshness": {
                "portfolio_retrieved_at": pf.retrieved_at,
                "oldest_quote": oldest_quote,
                "stale_quotes": stale_quotes,
                "analysis": status.get("analysis"),
                "latest_analysis": max((r["analysis"].get("analysis_timestamp") or "" for r in rows), default="") or None,
            },
            "overview": overview,
            "metrics": metrics.to_dict(),
            "groups": gd.summarize_groups(guidance_list),
            "attention": gd.attention_items(rows, metrics.to_dict(), policy, today),
            "positions": rows,
            "issues": [i.to_dict() for i in issues],
            "policy": policy.to_dict(),
        },
    }


def _no_call_cause(p, qf: Dict[str, Any], entry: Dict[str, Any], res: Optional[Dict[str, Any]],
                   dec: Optional[Dict[str, Any]], is_fund: bool = False) -> Dict[str, Any]:
    """Why a holding has no call, as a stable code with what would fix it.

    A missing recommendation has a cause, and the cause decides the remedy: waiting,
    refreshing, or nothing the user can do. Reconnecting the broker fixes none of the
    analysis-side causes, so it is never offered as the remedy.
    """
    if p.last_price is None:
        return {"code": "NO_QUOTE", "text": "Robinhood returned no price for this holding.", "remedy": "refresh"}
    st = qf.get("status")
    if st in ("stale", "unknown"):
        text = {"stale": "The Robinhood quote is from an earlier session.",
                "unknown": "Robinhood did not say when its quote was taken."}[st]
        return {"code": f"QUOTE_{st.upper()}", "text": text, "remedy": "refresh"}
    state = entry.get("state")
    if state in ("pending", "running") and not res:
        return {"code": "ANALYSIS_PENDING", "text": "QUANTA is still analysing this holding.", "remedy": "wait"}
    if state == "error":
        err = entry.get("error") or {}
        return {"code": "ANALYSIS_FAILED", "text": str(err.get("title") or "The analysis failed."),
                "remedy": "refresh" if err.get("retryable") is not False else "none",
                "detail": err.get("suggested_action") or ""}
    if not dec or dec.get("action") == "INVALID":
        return {"code": "DECISION_INVALID", "text": "The research engine could not produce a valid Decision "
                                                     "(usually too little price history or fundamentals).", "remedy": "none"}
    if (dec.get("freshness") or {}).get("status") == "stale":
        return {"code": "ANALYSIS_STALE", "text": "The analysis is older than QUANTA allows for a call.", "remedy": "refresh"}
    codes = set(dec.get("blocking_codes") or [])
    if "PRICE_HISTORY_TRUNCATED" in codes or str(dec.get("history_range") or "").startswith("6mo"):
        return {"code": "HISTORY_TRUNCATED", "text": "The price provider returned only six months of history, too "
                                                      "short to measure risk.", "remedy": "refresh"}
    if (not is_fund and "levels" in dec and (dec.get("levels") or {}).get("valuation_target") is None
            and (dec.get("event_check") or {}).get("status") != "flagged"):
        return {"code": "VALUATION_MISSING", "text": "The analyst consensus and DCF inputs were missing this run.",
                "remedy": "refresh"}
    return {"code": "UNSPECIFIED", "text": "No call was produced; see the data details.", "remedy": "refresh"}


def _impacts(holdings: List[an.Holding], total: float, cash: Optional[float], p, dec: Optional[Dict[str, Any]],
             weight: Optional[float], policy: an.PortfolioPolicy, buying_power: Optional[float],
             today: Optional[date] = None) -> Dict[str, Any]:
    """Portfolio impact previews. Every size is labelled with how it was chosen."""
    if not total or p.market_value is None:
        return {}
    out: Dict[str, Any] = {"remove": an.change_impact(holdings, total, cash, p.symbol, -p.market_value)}
    room = max(0.0, policy.max_single_position * total - p.market_value)
    out["room_to_cap"] = room
    sizing = (dec or {}).get("sizing") or {}
    suggested = sizing.get("position_value") if (dec or {}).get("new_money") == "ELIGIBLE" else None
    if suggested:
        add = min(float(suggested), room, buying_power if buying_power is not None else 0.0)
        if add > 0:
            out["add"] = dict(an.change_impact(holdings, total, cash, p.symbol, add),
                              basis="QUANTA's risk-budget size for this account, capped by the single-name "
                                    "limit and buying power")
    if weight is not None and weight > policy.trim_review_position:
        cut = p.market_value - policy.trim_review_position * total
        out["trim_to_review"] = dict(an.change_impact(holdings, total, cash, p.symbol, -cut),
                                     **_sale_detail(p, cut, today),
                                     basis=f"Selling down to the {policy.trim_review_position:.0%} review level")
    if weight is not None and weight > policy.max_single_position:
        cut = p.market_value - policy.max_single_position * total
        out["trim_to_cap"] = dict(an.change_impact(holdings, total, cash, p.symbol, -cut), **_sale_detail(p, cut, today),
                                  basis=f"Selling down to the {policy.max_single_position:.0%} single-name limit")
    return out


def _sale_detail(p, sell_value: float, today: Optional[date] = None) -> Dict[str, Any]:
    """Shares and gain realised by selling ``sell_value`` of a position at today's price.

    Gain uses the broker's average cost; the long/short split assumes the oldest
    lots go first (FIFO, Robinhood's default). Arithmetic, not tax advice.
    """
    price = p.last_price
    if not price or price <= 0 or sell_value <= 0:
        return {}
    shares = sell_value / price
    out: Dict[str, Any] = {"shares": shares}
    if p.average_cost is not None:
        out["gain"] = shares * (price - p.average_cost)
    lots = [lot for lot in (p.tax_lots or []) if lot.cost_per_share is not None and lot.quantity]
    today = today or date.today()

    def term(lot) -> Optional[str]:
        if lot.term in ("short", "long"):
            return lot.term
        try:
            return "long" if (today - date.fromisoformat(str(lot.acquired_on)[:10])).days > 365 else "short"
        except (TypeError, ValueError):
            return None

    if lots and all(term(lot) for lot in lots):
        left, short, long_ = shares, 0.0, 0.0
        for lot in sorted(lots, key=lambda x: x.acquired_on or ""):
            take = min(left, float(lot.quantity))
            gain = take * (price - float(lot.cost_per_share))
            if term(lot) == "long":
                long_ += gain
            else:
                short += gain
            left -= take
            if left <= 1e-9:
                break
        if left <= 1e-6:
            # The lots are what a sale actually realises, so they set the total too.
            out.update(gain=short + long_, gain_short=short, gain_long=long_,
                       lot_order="oldest lots first (FIFO)")
    return out
