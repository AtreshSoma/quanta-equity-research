"""Portfolio Review, exposure, events and plan endpoints' logic.

Everything here is built from the ONE portfolio view the service already publishes
(``service.view()``), so a weight, an action or a reason is the same object on every
tab and in the export. Market data, SEC and news sources are injected; each may be
absent or fail, and the section that needs it then says so without taking down the rest.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from engine.market_calendar import now_new_york

from . import events as ev
from . import exposure as ex
from . import history as hs
from . import plan as pl
from .activity_config import ACTIVITY
from .journal import DecisionJournal
from .sources import load_official_calendar

BENCHMARK = "SPY"
BENCHMARK_NAME = "S&P 500 (SPY)"
PRICE_TTL = 15 * 60
# A failed price read is remembered only briefly: a recovered provider is picked up on the
# next load instead of replaying the outage for the full PRICE_TTL.
PRICE_RETRY_TTL = 30
PROFILE_TTL = 24 * 3600
INTRADAY_TTL = 120
CANDIDATE_TTL = 10 * 60
MAX_CANDIDATES = 8



# A closing trade and the execution behind it are matched on symbol, side and a
# time within this many seconds (the two records are stamped separately).
EVIDENCE_WINDOW_S = float(ACTIVITY["evidence_window_s"])


def _epoch(at: Any) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.timestamp()


def _execution_index(fills: Any) -> Dict[Any, List[float]]:
    """(symbol, side) -> sorted execution times, from normalised fills."""
    index: Dict[Any, List[float]] = {}
    for f in fills or []:
        t = _epoch(f.at)
        if t is not None:
            index.setdefault((f.symbol, f.side), []).append(t)
    for times in index.values():
        times.sort()
    return index


def _has_execution(index: Dict[Any, List[float]], symbol: Any, side: Any, at: float) -> bool:
    return any(abs(t - at) <= EVIDENCE_WINDOW_S for t in index.get((symbol, side), ()))


def _classify_trade(trade: Dict[str, Any], crypto_index: Dict[Any, List[float]],
                    equity_index: Dict[Any, List[float]]) -> str:
    """crypto or equity when exactly one ledger holds the execution; else unknown."""
    at = _epoch(trade.get("at"))
    if at is None or not trade.get("symbol") or not trade.get("side"):
        return "unknown"
    in_crypto = _has_execution(crypto_index, trade["symbol"], trade["side"], at)
    in_equity = _has_execution(equity_index, trade["symbol"], trade["side"], at)
    if in_crypto != in_equity:
        return "crypto" if in_crypto else "equity"
    return "unknown"


def _utc_iso(at: str) -> str:
    """A fill time as ISO 8601 in UTC ("...Z"), so the page can show it in market time.
    A time without a zone is passed through to the second, as the broker gave it."""
    raw = str(at or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:19]
    if dt.tzinfo is None:
        return dt.isoformat(timespec="seconds")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coverage(every: List[Dict[str, Any]], kept: List[Dict[str, Any]], cap: int,
              read: Optional[Dict[str, Any]], need_span: Optional[str]) -> Dict[str, Any]:
    """What one history source covers. Complete only when nothing says otherwise:
    no row cap cut it, the page limit did not stop the read, and the window the
    broker used is the one asked for. A provider that reports nothing is not complete."""
    times = sorted(x["at"] for x in kept if x.get("at"))
    if len(every) > len(kept):
        capped_by = "rows"
    elif read is None:
        capped_by = "unknown"
    elif read.get("truncated"):
        capped_by = "pages"
    elif need_span and read.get("span") and str(read["span"]).lower() != need_span:
        capped_by = "span"
    else:
        capped_by = None
    return {"read": len(every), "passed": len(kept), "cap": cap, "capped_by": capped_by, "complete": capped_by is None,
            "first": times[0] if times else None, "last": times[-1] if times else None,
            "pages": (read or {}).get("pages"), "span": (read or {}).get("span")}


@dataclass

class MarketSources:
    """Every outside dependency, optional. ``prices(symbols, period)`` -> DataFrame of closes."""
    prices: Optional[Callable[[List[str], str], Any]] = None
    # ``intraday(symbols)`` -> {"date": "YYYY-MM-DD", "stamps": [ISO New York], "prices": {symbol: [...]}}
    intraday: Optional[Callable[[List[str]], Optional[Dict[str, Any]]]] = None
    profiles: Optional[Callable[[List[str]], Dict[str, Dict[str, Any]]]] = None
    articles: Optional[Callable[[], List[Dict[str, Any]]]] = None
    edgar: Optional[Any] = None
    calendar: Callable[[], Dict[str, Any]] = load_official_calendar
    journal: Optional[DecisionJournal] = None


class Insights:
    def __init__(self, service: Any, sources: MarketSources,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.svc = service
        self.src = sources
        self._clock = clock
        self._lock = threading.RLock()
        self._closes: Dict[Any, Any] = {}
        self._profiles: Dict[str, Any] = {}
        self._history: Optional[Dict[str, Any]] = None
        self._history_at = 0.0
        self._crypto_history: Dict[str, Any] = {}
        self._crypto_evidence: Dict[str, Any] = {}
        self._cand: Dict[str, Dict[str, Any]] = {}
        self._cand_job: Dict[str, Any] = {}
        self._cand_corr: Dict[str, Dict[str, float]] = {}

    # -- shared helpers -----------------------------------------------------

    def _pf(self) -> Optional[Dict[str, Any]]:
        v = self.svc.view()
        return v.get("portfolio")

    def _prices(self, symbols: Sequence[str], period: str) -> Any:
        if self.src.prices is None or not symbols:
            return None
        key = (tuple(sorted(set(symbols))), period)
        with self._lock:
            hit = self._closes.get(key)
            if hit and time.time() - hit[0] < (PRICE_TTL if hit[1] is not None else PRICE_RETRY_TTL):
                return hit[1]
        try:
            frame = self.src.prices(sorted(set(symbols)), period)
        except Exception:  # noqa: BLE001 - market data is optional
            frame = None
        with self._lock:
            self._closes[key] = (time.time(), frame)
        return frame

    def _profile_map(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        if self.src.profiles is None:
            return {}
        with self._lock:
            need = [s for s in symbols if s not in self._profiles or time.time() - self._profiles[s][0] > PROFILE_TTL]
        if need:
            try:
                got = self.src.profiles(need) or {}
            except Exception:  # noqa: BLE001
                got = {}
            with self._lock:
                for s in need:
                    self._profiles[s] = (time.time(), got.get(s) or {})
        with self._lock:
            return {s: self._profiles[s][1] for s in symbols if s in self._profiles}

    @staticmethod
    def _holding_rows(pf: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"symbol": r["symbol"], "quantity": r["broker"].get("quantity"),
                 "value": r["broker"].get("market_value"), "tax_lots": r["broker"].get("tax_lots")}
                for r in pf.get("positions", [])]

    # -- journal ------------------------------------------------------------

    def record_journal(self, pf: Dict[str, Any]) -> int:
        if self.src.journal is None or not pf:
            return 0
        if (pf.get("freshness") or {}).get("analysis", {}).get("state") == "running":
            return 0
        session = now_new_york(self._clock()).date().isoformat()
        try:
            return self.src.journal.record(pf.get("positions", []), session, self._clock())
        except OSError:
            return 0

    def journal_info(self) -> Dict[str, Any]:
        j = self.src.journal
        if j is None:
            return {"enabled": False}
        return dict(j.summary(), enabled=True, changes=j.changes())

    # -- performance --------------------------------------------------------

    def _ledger(self) -> Dict[str, Any]:
        """Broker record history, cached; each part reports why it is missing."""
        with self._lock:
            if self._history is not None and time.time() - self._history_at < PRICE_TTL:
                return self._history
        handle = getattr(self.svc, "_account", None)
        out: Dict[str, Any] = {"orders": None, "trades": None, "issues": []}
        if handle:
            try:
                out = self.svc.provider.load_history(handle)
            except Exception as exc:  # noqa: BLE001 - history is optional; auth errors surface on refresh
                out["issues"].append({"code": "HISTORY_FAILED", "message": type(exc).__name__})
        with self._lock:
            self._history, self._history_at = out, time.time()
        return out

    def performance(self) -> Dict[str, Any]:
        pf = self._pf()
        if not pf:
            return {"available": False, "reason": "Not connected."}
        holdings = self._holding_rows(pf)
        symbols = [h["symbol"] for h in holdings]
        closes = self._prices(symbols + [BENCHMARK], "5y")
        base = {"benchmark": {"symbol": BENCHMARK, "name": BENCHMARK_NAME, "basis": hs.PRICE_BASIS,
                              "why": "A broad US large-cap reference. Price return without dividends, like the holdings."},
                "ranges": list(hs.RANGES), "as_of": self._clock().isoformat()}
        if closes is None:
            return dict(base, available=False,
                        reason="Price history could not be loaded, so no performance chart is drawn.")
        sims = {rk: hs.simulate_current_holdings(holdings, closes, BENCHMARK, rk) for rk in hs.RANGES}
        # Actual performance: only from an order ledger that reconciles to today's shares.
        hist = self._ledger()
        actual: Dict[str, Any] = {"available": False, "label": "Actual account return", "missing": []}
        orders = hist.get("orders")
        if orders is None:
            actual["missing"] += [i["message"] for i in hist.get("issues", []) if i["code"].startswith("ORDERS")] \
                or ["Order history was not available from the broker."]
        else:
            norm = hs.normalize_orders(orders)
            rec = hs.reconcile_ledger(norm["fills"], holdings)
            actual.update(ledger={k: rec[k] for k in ("ok", "fills", "first_fill")}, rejected=norm["rejected"])
            if not rec["ok"]:
                actual["missing"].append(
                    "Order history does not add up to today's share counts"
                    + (f" ({', '.join(m['symbol'] for m in rec['mismatched'][:4])})" if rec["mismatched"] else "")
                    + ": transfers in, splits or older trades are missing.")
            else:
                per = {rk: hs.ledger_performance(norm["fills"], closes, BENCHMARK, rk) for rk in hs.RANGES}
                if any(p.get("available") for p in per.values()):
                    actual.update(available=True, kind="ledger", ranges=per,
                                  label="Equity sleeve return (from order ledger)")
                else:
                    actual["missing"].append(next((p.get("reason") for p in per.values() if p.get("reason")),
                                                  "Not enough invested history."))
        actual["missing"] += ["Cash balances, deposits and withdrawals over time are not available, so a "
                              "whole-account return cannot be computed."] if actual.get("available") else \
            ["Daily account valuations and cash flows (deposits, withdrawals, dividends) are not available."]
        attribution = {}
        for rk, s in sims.items():
            if s.get("available"):
                attribution[rk] = hs.open_lot_attribution(
                    [{"symbol": h["symbol"], "tax_lots": h["tax_lots"]} for h in holdings], closes, s["stats"]["start"])
        markers = self._markers(pf, sims)
        sparks = hs.recent_paths(closes, symbols + [BENCHMARK], 22)
        # The simulation's per-range payload is trimmed for the wire: dollars stay, floats are rounded.
        for s in sims.values():
            _round_series(s)
        for a in (actual.get("ranges") or {}).values():
            _round_series(a)
        return dict(base, available=True, actual=actual, simulation=sims, attribution=attribution, markers=markers, sparks=sparks,
                    realized={"total": pf["overview"].get("realized_pnl"), "period": pf["overview"].get("realized_pnl_period")})

    def intraday(self) -> Dict[str, Any]:
        """Today's holdings over the latest session's 5-minute bars, from the previous close.

        A simulation of today's shares, like the daily chart; the previous close comes from
        the daily closes (the same basis as every other range).
        """
        pf = self._pf()
        if not pf:
            return {"available": False, "reason": "Not connected."}
        if self.src.intraday is None:
            return {"available": False, "reason": "Intraday prices are not configured on this server."}
        holdings = self._holding_rows(pf)
        symbols = sorted({h["symbol"] for h in holdings} | {BENCHMARK})
        key = ("intraday", tuple(symbols))
        with self._lock:
            hit = self._closes.get(key)
            if hit and time.time() - hit[0] < INTRADAY_TTL:
                return hit[1]
        try:
            bars = self.src.intraday(symbols)
        except Exception:  # noqa: BLE001 - market data is optional
            bars = None
        closes = self._prices(symbols, "5y") if bars else None
        if not bars or not bars.get("stamps") or closes is None or getattr(closes, "empty", True):
            out = {"available": False, "range": "1D", "reason": "Intraday prices could not be loaded."}
        else:
            day = str(bars.get("date") or bars["stamps"][-1][:10])
            prior = [i for i, d in enumerate(closes.index) if str(d)[:10] < day]
            prev: Dict[str, float] = {}
            for sym in symbols:
                if sym not in closes.columns:
                    continue
                col = closes[sym]
                for i in reversed(prior):
                    v = col.iloc[i]
                    if v == v and v is not None:        # skip NaN
                        prev[sym] = float(v)
                        break
            out = hs.simulate_intraday(holdings, bars["stamps"], bars.get("prices") or {}, prev, BENCHMARK)
            out["date"] = day
            _round_series(out)
        with self._lock:
            self._closes[key] = (time.time(), out)
        return out

    def whatif(self, changes: Any) -> Dict[str, Any]:
        return self.svc.whatif(changes)

    def _markers(self, pf: Dict[str, Any], sims: Dict[str, Any]) -> List[Dict[str, Any]]:
        longest = next((s for s in (sims.get("MAX"), sims.get("1Y")) if s and s.get("available")), None)
        if not longest:
            return []
        first, last = longest["dates"][0], longest["dates"][-1]
        out = []
        for r in pf.get("positions", []):
            for lot in (r["broker"].get("tax_lots") or []):
                d = lot.get("acquired_on")
                if d and first <= d <= last:
                    out.append({"date": d, "kind": "purchase", "symbol": r["symbol"],
                                "label": f"Bought {r['symbol']} (open lot)"})
        j = self.src.journal
        if j is not None:
            for c in j.changes(40):
                if first <= c["date"] <= last:
                    out.append({"date": c["date"], "kind": "call", "symbol": c["symbol"],
                                "label": f"{c['symbol']} call: {c.get('from_action') or '?'} → {c['action']}"})
        out.sort(key=lambda m: m["date"])
        return out[:60]

    # -- outcomes -----------------------------------------------------------

    def outcomes(self) -> Dict[str, Any]:
        hist = self._ledger()
        trades_raw = hist.get("trades")
        if trades_raw is None:
            trades = hs.trade_outcomes([], "")
            trades["missing"] = [i["message"] for i in hist.get("issues", []) if i["code"].startswith("TRADE")] \
                or ["Closed-trade history was not available from the broker."]
            trades["source"] = None
        else:
            trades = hs.trade_outcomes(hs.normalize_trades(trades_raw), "as returned by the broker")
            trades["source"] = "Robinhood realized profit-and-loss trade history"
        j = self.src.journal
        entries = j.entries() if j is not None else []
        syms = sorted({e["symbol"] for e in entries})
        closes = self._prices(syms + [BENCHMARK], "2y") if syms else None
        rec = hs.recommendation_outcomes(entries, closes, BENCHMARK)
        rec["journal"] = self.journal_info()
        return {"trades": trades, "recommendations": rec,
                "separation": "Trade outcomes (what the account did) and recommendation outcomes (what the model said) "
                              "are measured on different records and are never combined."}

    # -- activity: realized P&L by period ----------------------------------

    ACTIVITY_PERIODS = ("day", "week", "month", "3month", "year", "ytd", "all")

    def activity(self, period: str) -> Dict[str, Any]:
        """Realized results for one window, as Robinhood reports them, with plain statistics.

        Realized only: open positions' gains are in Holdings. Buckets whose gain Robinhood
        does not report stay unknown and are counted separately, never as zero.
        """
        if period not in self.ACTIVITY_PERIODS:
            return {"available": False, "reason": "Unknown period."}
        handle = getattr(self.svc, "_account", None)
        if not handle:
            return {"available": False, "reason": "Not connected."}
        key = f"realized:{handle}:{period}"
        with self._lock:
            hit = self._closes.get(key)
            if hit and time.time() - hit[0] < PRICE_TTL:
                return hit[1]
        try:
            r = self.svc.provider.load_realized_series(handle, period)
        except Exception as exc:  # noqa: BLE001 - the tab reports it; nothing else depends on it
            return {"available": False, "period": period,
                    "reason": str(exc) if getattr(exc, "code", "") else f"Realized P&L could not be read ({type(exc).__name__})."}
        known = [b for b in r["buckets"] if b["gain"] is not None]
        trades = [b["trades"] for b in r["buckets"] if isinstance(b.get("trades"), int)]
        cum, run = [], 0.0
        for b in r["buckets"]:
            run += b["gain"] or 0.0
            cum.append(run if b["gain"] is not None or cum else None)
        out = dict(r, available=True, stats={
            "buckets": len(r["buckets"]), "unknown_buckets": len(r["buckets"]) - len(known),
            "positive": sum(1 for b in known if b["gain"] > 0), "negative": sum(1 for b in known if b["gain"] < 0),
            "best": max(known, key=lambda b: b["gain"]) if known else None,
            "worst": min(known, key=lambda b: b["gain"]) if known else None,
            "trades": sum(trades) if trades else None,
            "per_trade": (r["total"] / sum(trades)) if r.get("total") is not None and trades and sum(trades) else None,
        }, cumulative=cum,
            note="Realized results only: gains and losses on positions closed in this window, as Robinhood reports them. "
                 "Open positions, dividends and deposits are not included.")
        with self._lock:
            self._closes[key] = (time.time(), out)
        return out

    # -- records ------------------------------------------------------------

    def _crypto_records(self) -> Dict[str, Any]:
        """Cache only Activity's crypto reads, keyed by selected account."""
        handle = getattr(self.svc, "_account", None)
        loader = getattr(self.svc.provider, "load_crypto_history", None)
        if not handle or not loader:
            return {"available": False, "reason": "Crypto order history is unavailable from this provider."}
        with self._lock:
            hit = self._crypto_history.get(handle)
            if hit and time.time() - hit[0] < PRICE_TTL:
                return hit[1]
        try:
            raw = loader(handle)
            norm = hs.normalize_crypto_orders(raw["orders"])
            self._crypto_evidence[handle] = _execution_index(norm["fills"])
            every = [{"symbol": f.symbol, "instrument_type": "crypto", "side": f.side,
                      "quantity": f.quantity, "price": f.price, "at": _utc_iso(f.at)}
                     for f in reversed(norm["fills"])]
            fills = every[:ACTIVITY["caps"]["fills"]]
            out = {"available": True, "fills": fills, "rejected": norm["rejected"],
                   "coverage": _coverage(every, fills, ACTIVITY["caps"]["fills"], raw.get("coverage"), need_span=None)}
        except Exception:  # optional source: never turn a failed read into an empty history
            return {"available": False, "reason": "Crypto order history could not be read from Robinhood."}
        with self._lock:
            self._crypto_history[handle] = (time.time(), out)
        return out

    def records(self) -> Dict[str, Any]:
        """Transaction history that exists: open lots (always, when reported), plus order
        fills and closed trades when the broker offered them and they were readable.

        ``coverage`` says, per source, what was read and passed on: counts, the first and
        last time, and why it is not complete when it is not (a row cap, the page limit,
        a shorter window than all time, or a provider that does not say). ``config`` is
        the Activity tab's settings (see ``activity_config``).
        """
        pf = self._pf()
        if not pf:
            return {"available": False, "reason": "Not connected."}
        caps = ACTIVITY["caps"]
        lots = []
        for r in pf.get("positions", []):
            b = r["broker"]
            for lot in (b.get("tax_lots") or []):
                q, c, px = lot.get("quantity"), lot.get("cost_per_share"), b.get("last_price")
                lots.append({"symbol": r["symbol"], "acquired_on": lot.get("acquired_on"), "quantity": q,
                             "instrument_type": b.get("instrument_type") or "equity",
                             "cost_per_share": c, "term": lot.get("term"), "price": px,
                             "cost": (q * c) if None not in (q, c) else None,
                             "value": (q * px) if None not in (q, px) else None,
                             "gain": (q * (px - c)) if None not in (q, c, px) else None})
        lots.sort(key=lambda x: (x["acquired_on"] or "", x["symbol"]), reverse=True)
        crypto = self._crypto_records()
        crypto_index = self._crypto_evidence.get(getattr(self.svc, "_account", None), {}) if crypto.get("available") else {}
        equity_index: Dict[Any, List[float]] = {}
        hist = self._ledger()
        reads = hist.get("coverage")
        fills = None
        rejected = 0
        coverage: Dict[str, Any] = {}
        if hist.get("orders") is not None:
            norm = hs.normalize_orders(hist["orders"])
            rejected = norm["rejected"]
            equity_index = _execution_index(norm["fills"])
            every = [{"symbol": f.symbol, "instrument_type": f.instrument_type, "assetType": f.instrument_type,
                      "side": f.side, "quantity": f.quantity, "price": f.price, "at": _utc_iso(f.at)}
                     for f in reversed(norm["fills"])]
            fills = every[:caps["fills"]]
            coverage["fills"] = _coverage(every, fills, caps["fills"], (reads or {}).get("orders") if reads is not None else None,
                                          need_span=None)
        trades = None
        classification = {"crypto": 0, "equity": 0, "unresolved": 0}
        if hist.get("trades") is not None:
            # Robinhood's own closing trades (realized P&L per trade), newest first.
            every = sorted(hs.normalize_trades(hist["trades"]), key=lambda t: t.get("at") or t.get("closed_at") or "",
                           reverse=True)
            # A closing record states no asset class; an execution of the same symbol
            # and side at that time does. Without such evidence it stays "unknown".
            for t in every:
                if t.get("instrument_type") in (None, "", "unknown"):
                    t["instrument_type"] = _classify_trade(t, crypto_index, equity_index)
                t["assetType"] = t["instrument_type"]
            classification = {
                "crypto": sum(1 for t in every if t["instrument_type"] == "crypto"),
                "equity": sum(1 for t in every if t["instrument_type"] not in ("crypto", "unknown")),
                "unresolved": sum(1 for t in every if t["instrument_type"] == "unknown"),
            }
            trades = every[:caps["trades"]]
            coverage["trades"] = _coverage(every, trades, caps["trades"], (reads or {}).get("trades") if reads is not None else None,
                                           need_span="all")
        no_lots = [r["symbol"] for r in pf.get("positions", []) if r["broker"].get("tax_lots") is None]
        return {"available": True, "lots": lots, "fills": fills, "fills_rejected": rejected, "closed_trades": trades,
                "crypto": crypto, "classification": classification,
                "coverage": coverage, "config": ACTIVITY,
                "issues": hist.get("issues", []), "lots_missing_for": no_lots,
                "note": "Open lots are what the broker reports as still held. Sold lots appear only in order or closed-trade history, when the broker provides it."}

    # -- exposure -----------------------------------------------------------

    def exposure(self, prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        pf = self._pf()
        if not pf:
            return {"available": False, "reason": "Not connected."}
        rows = pf.get("positions", [])
        profiles = self._profile_map([r["symbol"] for r in rows])
        out = ex.build_exposures(rows, (pf["overview"] or {}).get("total_value"), (pf["overview"] or {}).get("cash"),
                                 profiles, pf.get("policy") or {}, pf.get("metrics") or {}, prefs)
        out["available"] = True
        out["profile_source"] = ("Sector from the broker/analysis; country and fund type from Yahoo Finance company "
                                 "profiles (undated)." if profiles else
                                 "Company profile data was unavailable; country and fund type are not classified.")
        return out

    # -- events -------------------------------------------------------------

    def events(self) -> Dict[str, Any]:
        pf = self._pf()
        if not pf:
            return {"available": False, "reason": "Not connected."}
        rows = pf.get("positions", [])
        now = self._clock()
        since = now_new_york(now).date() - timedelta(days=21)
        filings: Dict[str, Dict[str, Any]] = {}
        if self.src.edgar is not None:
            for r in rows:
                filings[r["symbol"]] = self.src.edgar.recent_filings(r["symbol"], since)
        try:
            articles = self.src.articles() if self.src.articles else []
        except Exception:  # noqa: BLE001
            articles = []
        beta = ((pf.get("metrics") or {}).get("beta") or {}).get("portfolio_beta")
        out = ev.build_events(rows, now=now, policy=pf.get("policy") or {}, filings=filings, articles=articles,
                              calendar=self.src.calendar(), portfolio_beta=beta)
        out["available"] = True
        out["alerts_delivery"] = {
            "in_page": True, "background": False, "email": False, "push": False,
            "text": "Watch conditions are checked when this page is open and refreshed. There is no background monitoring, "
                    "email or push notification.",
        }
        out["journal_changes"] = self.journal_info().get("changes", []) if self.src.journal else []
        return out

    # -- plan & ideas -------------------------------------------------------

    def _run_candidates(self, job: Dict[str, Any], symbols: List[str], size: Optional[float], holdings: List[str]) -> None:
        for sym in symbols:
            with self._lock:
                if job.get("cancelled"):
                    return
                self._cand[sym]["state"] = "running"
            try:
                res, err = self.svc._analyze(sym, size)
            except Exception as exc:  # noqa: BLE001
                res, err = None, {"title": f"{type(exc).__name__} during analysis"}
            with self._lock:
                e = self._cand.setdefault(sym, {})
                e["finished_at"] = time.time()
                if res and not err:
                    from .service import compact_analysis
                    e.update(state="done", result=compact_analysis(res), error=None)
                else:
                    e.update(state="error", result=None, error=err or {"title": "Analysis failed"})
                job["done"] += 1
        # Correlation of each candidate with the holdings, from one daily-returns download.
        if self.svc._returns_fn is not None:
            try:
                rets = self.svc._returns_fn(sorted(set(holdings) | set(symbols)))
                if rets is not None and not getattr(rets, "empty", True):
                    for sym in symbols:
                        if sym not in rets.columns:
                            continue
                        corr = {}
                        for h in holdings:
                            if h in rets.columns and h != sym:
                                pair = rets[[sym, h]].dropna()
                                if len(pair) >= 60:
                                    c = float(pair[sym].corr(pair[h]))
                                    if c == c:
                                        corr[h] = c
                        with self._lock:
                            self._cand_corr[sym] = corr
            except Exception:  # noqa: BLE001 - correlation is one input among several
                pass
        with self._lock:
            job["finished_at"] = time.time()

    def plan(self, raw_profile: Optional[Dict[str, Any]], candidates: Sequence[str], run: bool) -> Dict[str, Any]:
        pf = self._pf()
        if not pf:
            return {"available": False, "reason": "Not connected."}
        profile = pl.clean_profile(raw_profile)
        status = pl.profile_status(profile)
        syms = []
        for c in candidates or []:
            s = str(c).strip().upper()
            if s and len(s) <= 12 and s.replace(".", "").replace("-", "").isalnum() and s not in syms:
                syms.append(s)
        syms = syms[:MAX_CANDIDATES]
        rows = pf.get("positions", [])
        held = [r["symbol"] for r in rows]
        size = (pf.get("overview") or {}).get("total_value")
        started = False
        if run and syms:
            with self._lock:
                fresh = [s for s in syms if not (self._cand.get(s) and self._cand[s].get("state") in ("done", "running", "pending")
                                                 and time.time() - self._cand[s].get("finished_at", time.time()) < CANDIDATE_TTL)]
                if fresh:
                    for s in fresh:
                        self._cand[s] = {"state": "pending"}
                    if self._cand_job:
                        self._cand_job["cancelled"] = True
                    job = {"done": 0, "total": len(fresh), "finished_at": None, "cancelled": False}
                    self._cand_job = job
                    threading.Thread(target=self._run_candidates, args=(job, fresh, size, held), daemon=True).start()
                    started = True
        today = now_new_york(self._clock()).date()
        from .analytics import PortfolioPolicy
        pol = PortfolioPolicy(**{k: v for k, v in (pf.get("policy") or {}).items() if k in PortfolioPolicy.__dataclass_fields__})
        cards = []
        for s in syms:
            with self._lock:
                e = dict(self._cand.get(s) or {"state": "idle"})
                corr = dict(self._cand_corr.get(s) or {})
            if e["state"] in ("pending", "running"):
                cards.append({"symbol": s, "state": e["state"]})
            elif e["state"] == "idle":
                cards.append({"symbol": s, "state": "idle"})
            else:
                c = pl.evaluate_candidate(s, e.get("result"), e.get("error"), rows, pf.get("metrics") or {},
                                          pf.get("overview") or {}, pol, profile, today, corr)
                c["state"] = "done"
                cards.append(c)
        done = [c for c in cards if c.get("state") == "done"]
        data_ok = (pf.get("reconciliation") or {}).get("status") == "ok"
        scen = pl.build_scenarios(rows, pf.get("overview") or {}, pol, profile, done, data_ok)
        with self._lock:
            job = dict(self._cand_job)
        return {"available": True, "profile": profile, "profile_status": status, "candidates": cards,
                "running": any(c.get("state") in ("pending", "running") for c in cards), "started": started,
                "job": {"done": job.get("done", 0), "total": job.get("total", 0)} if job else None,
                "keep_cash": self._keep_cash(done, scen), **scen,
                "framing": ("Research ideas conditional on the inputs you have not entered yet."
                            if not status["complete"] else "Ideas ranked for the plan you entered."),
                "sizes_basis": "Every size is a proposal for comparison; QUANTA cannot place trades."}

    @staticmethod
    def _keep_cash(cards: Sequence[Dict[str, Any]], scen: Dict[str, Any]) -> Dict[str, Any]:
        suitable = [c for c in cards if c.get("verdict", {}).get("code") == "suitable"]
        contrib = next((s for s in scen["scenarios"] if s["key"] == "contribution"), {})
        buys = [m for m in contrib.get("moves", []) if m.get("action") == "Buy"]
        justified = not suitable and not buys
        return {"justified": justified,
                "text": ("Nothing you hold or have asked about clears QUANTA's rules for new money right now. "
                         "Keeping cash, or making no change, is a legitimate outcome." if justified else
                         "At least one purchase clears QUANTA's rules; compare it with keeping cash below.")}


def _round_series(s: Dict[str, Any]) -> None:
    for k in ("portfolio", "benchmark_series", "value", "drawdown", "benchmark_drawdown"):
        if isinstance(s.get(k), list):
            s[k] = [round(x, 6) if isinstance(x, float) else x for x in s[k]]
