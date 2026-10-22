"""Discover: the reads behind the ranked ideas and the movers board.

Two kinds of function live here:

  * Pure reads (no provider, fully testable): how a session traded
    (`tape_read`), why a name is moving (`move_reason`), the odds of a price
    target under a lognormal (`target_odds`), analyst revision counts
    (`revisions_summary`), past earnings reactions (`earnings_moves`) and the
    option market's implied move (`implied_move`).
  * Provider reads with their own caches (`idea_read`, `mover_read`), run by
    the /api/discover/enrich endpoint after the intel payload has rendered, so
    the first paint never waits on an option chain.

Every figure is either measured from a provider response or returned as None.
Nothing is estimated to fill a gap. Every band comes from engine.thresholds.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from engine import thresholds as _T

logger = logging.getLogger("quanta.discover")

M = _T.MARKET
_TZ = {"US": "America/New_York", "IN": "Asia/Kolkata"}
# Session open in exchange time: a report stamped before it moves the same session.
_SESSION_OPEN = {"US": (9, 30), "IN": (9, 15)}


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _testing() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and os.environ.get("QUANTA_TEST_CACHE") != "1"


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
class TTLStore:
    """A small keyed cache whose entries expire after `ttl()` seconds. With a
    path it is also kept on disk, so a restart serves the last read while the
    first refresh runs. Disabled under pytest so one test's mock never answers
    another's (QUANTA_TEST_CACHE=1 turns it back on for the cache's own tests)."""

    def __init__(self, ttl: Callable[[], float], path: Optional[Path] = None):
        self._ttl = ttl
        self._path = path
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._saved = 0.0
        self._loaded = False

    def _load(self) -> None:
        if self._loaded or not self._path or _testing():
            self._loaded = True
            return
        self._loaded = True
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for k, pair in (raw or {}).items():
                self._data[k] = (float(pair[0]), pair[1])
        except Exception:
            pass

    def get(self, key: str) -> Optional[Any]:
        if _testing():
            return None
        with self._lock:
            self._load()
            hit = self._data.get(key)
        if hit and time.time() - hit[0] < float(self._ttl()):
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        if _testing():
            return
        with self._lock:
            self._load()
            self._data[key] = (time.time(), value)
        self._save()

    def _save(self) -> None:
        if not self._path or time.time() - self._saved < 30:
            return
        self._saved = time.time()
        try:
            with self._lock:
                data = {k: [ts, v] for k, (ts, v) in self._data.items()}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:
            logger.debug("Discover cache not saved: %s", exc)


_CACHE_DIR = Path(os.environ.get("QUANTA_CACHE_DIR", str(Path(__file__).resolve().parent.parent / "data" / "cache")))
FUNDAMENTALS = TTLStore(lambda: M.FUNDAMENTALS_CACHE_SECONDS, _CACHE_DIR / "discover_fundamentals.json")
_HISTORY = TTLStore(lambda: M.ENRICH_HISTORY_CACHE_SECONDS)
_SLOW = TTLStore(lambda: M.ENRICH_HISTORY_CACHE_SECONDS)     # EPS trend, earnings dates
_FAST = TTLStore(lambda: M.ENRICH_OPTIONS_CACHE_SECONDS)     # option chains, analyst actions
_QUOTES = TTLStore(lambda: M.CONTEXT_CACHE_SECONDS)          # sector fund and bill quotes


def cached(store: TTLStore, key: str, fetch: Callable[[], Any]) -> Any:
    hit = store.get(key)
    if hit is not None:
        return hit
    value = fetch()
    if value is not None:
        store.put(key, value)
    return value


# ---------------------------------------------------------------------------
# Pure reads: the session and why it moved
# ---------------------------------------------------------------------------
def tape_read(row: Dict[str, Any]) -> Dict[str, Any]:
    """How the session traded, from its own open, high, low, last and previous
    close. Returns the gap, the share of it the last price keeps, where the last
    sits in the day's range (0 at the low, 1 at the high), the range as a share
    of price, relative volume and one plain tag. A field is None when its inputs
    are missing; the tag is None when the session cannot be read."""
    o, h, lo = _num(row.get("open")), _num(row.get("day_high")), _num(row.get("day_low"))
    prev, last = _num(row.get("prev_close")), _num(row.get("price"))
    vol, avg = _num(row.get("volume")), _num(row.get("avg_volume"))
    out: Dict[str, Any] = {"gap_pct": None, "held_share": None, "clv": None, "range_pct": None,
                           "tag": None, "rvol": None}
    if vol is not None and avg and avg > 0:
        out["rvol"] = round(vol / avg, 2)
    if None in (o, h, lo, prev, last) or prev <= 0 or h < lo:
        return out
    gap = (o / prev - 1) * 100
    out["gap_pct"] = round(gap, 2)
    out["range_pct"] = round((h - lo) / last * 100, 2) if last > 0 else None
    out["clv"] = round((last - lo) / (h - lo), 2) if h > lo else None
    move = last - prev
    if abs(gap) >= float(M.GAP_MIN_PCT):
        held = move / (o - prev) if o != prev else None
        out["held_share"] = round(held, 2) if held is not None else None
        if held is None:
            tag = None
        elif held < 0:
            tag = "reversal"
        elif held >= 1 + (1 - float(M.GAP_HOLD_SHARE)):
            tag = "gap_extended"
        elif held >= float(M.GAP_HOLD_SHARE):
            tag = "gap_held"
        elif held < float(M.GAP_FADE_SHARE):
            tag = "gap_faded"
        else:
            tag = "gap_partly_held"
    else:
        clv = out["clv"]
        if clv is not None and move > 0 and clv >= float(M.TREND_DAY_CLV):
            tag = "trend_up"
        elif clv is not None and move < 0 and clv <= 1 - float(M.TREND_DAY_CLV):
            tag = "trend_down"
        else:
            tag = "two_way"
    out["tag"] = tag
    return out


TAPE_LABELS = {
    "gap_extended": "Gap extended", "gap_held": "Gap held", "gap_partly_held": "Gap partly held", "gap_faded": "Gap faded",
    "reversal": "Reversal", "trend_up": "Trend up", "trend_down": "Trend down", "two_way": "Two-way",
}


def _previous_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def earnings_in_session(ts: Any, market: str = "US", now: Optional[datetime] = None) -> bool:
    """True when a report time stamp (epoch seconds) drives today's session: a
    report before today's open, or one after the previous session's open (so
    after its close too). Weekends roll back to Friday; exchange holidays are
    not modelled, so a Monday-after-holiday report reads one session late."""
    t = _num(ts)
    if t is None or t <= 0:
        return False
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(_TZ.get(market, _TZ["US"]))
    except Exception:
        return False
    now = now.astimezone(tz) if now else datetime.now(tz)
    when = datetime.fromtimestamp(t, tz)
    today = now.date()
    if today.weekday() >= 5:
        today = _previous_weekday(today + timedelta(days=1))
    oh, om = _SESSION_OPEN.get(market, _SESSION_OPEN["US"])
    before_open = (when.hour, when.minute) < (oh, om)
    if when.date() == today and before_open:
        return True
    prev = _previous_weekday(today)
    if prev < when.date() < today:      # a report on a weekend or holiday between sessions
        return True
    return when.date() == prev and not before_open


def deal_signature(change_pct: Any, tape: Dict[str, Any]) -> bool:
    """A large gap up that then barely trades: the pattern of a stock pinned to
    an announced acquisition price. A pattern, not a confirmed deal."""
    gap, rng, chg = _num(tape.get("gap_pct")), _num(tape.get("range_pct")), _num(change_pct)
    return (gap is not None and rng is not None and chg is not None and chg > 0
            and gap >= float(M.MOVE_DEAL_MIN_GAP_PCT) and rng <= float(M.MOVE_DEAL_MAX_RANGE_PCT))


def move_reason(row: Dict[str, Any], read: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The most specific reason a name is moving, in a fixed order: deal
    pattern, earnings, an analyst action, its sector. With the enrichment read
    in hand and none of them found, the reason is "none" (no clear cause); with
    it not yet in hand and no same-payload reason, None (still being checked)."""
    tape = row.get("tape") or tape_read(row)
    chg = _num(row.get("change_pct"))
    if deal_signature(chg, tape):
        return {"key": "deal", "label": "Likely deal",
                "detail": f"Gapped {tape['gap_pct']:+.1f}% and traded a {tape['range_pct']:.1f}% range: the pattern of a price pinned to an offer."}
    if row.get("earnings_today"):
        return {"key": "earnings", "label": "Earnings", "detail": "Reported results since the last session."}
    if read is None:
        return None
    action = read.get("analyst")
    if action:
        return {"key": "analyst", "label": action.get("label") or "Analyst action", "detail": action.get("detail") or ""}
    fund_chg, share = _num(read.get("fund_change_pct")), float(M.MOVE_SECTOR_SHARE)
    if fund_chg is not None and chg and fund_chg * chg > 0 and abs(fund_chg) >= share * abs(chg):
        return {"key": "sector", "label": "Sector move",
                "detail": f"{read.get('fund')} moved {fund_chg:+.1f}%, at least {share:.0%} of this move."}
    return {"key": "none", "label": "No clear cause",
            "detail": "No report, deal pattern, analyst action or sector move explains it."}


# ---------------------------------------------------------------------------
# Pure reads: targets, revisions and earnings
# ---------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def target_odds(spot: Any, target: Any, sigma: Any, rate: Any = 0.0, years: float = 1.0) -> Optional[float]:
    """P(price at or above target after `years`) for a lognormal price with
    volatility `sigma` and risk-neutral drift `rate`: N(d2). Risk-neutral odds
    are the option market's pricing, not a forecast of the real-world chance."""
    s, k, v, r = _num(spot), _num(target), _num(sigma), _num(rate) or 0.0
    if not s or not k or not v or s <= 0 or k <= 0 or v <= 0 or years <= 0:
        return None
    d2 = (math.log(s / k) + (r - v * v / 2) * years) / (v * math.sqrt(years))
    return round(_norm_cdf(d2), 4)


def realized_vol(closes: List[float], sessions: int = 252) -> Optional[float]:
    xs = [c for c in closes if c and c > 0][-(sessions + 1):]
    if len(xs) < 60:
        return None
    rets = [math.log(b / a) for a, b in zip(xs, xs[1:])]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(float(M.TRADING_DAYS_PER_YEAR)), 4)


def momentum_12_1(closes: List[float]) -> Optional[float]:
    """Return from 12 months to 1 month ago, in percent: the momentum read that
    skips the most recent month, which tends to reverse."""
    if len(closes) < 253:
        return None
    a, b = closes[-253], closes[-22]
    return round((b / a - 1) * 100, 2) if a and b else None


def revisions_summary(rows: Iterable[Dict[str, Any]], now: datetime, days: int) -> Dict[str, Any]:
    """Target raises and cuts, upgrades and downgrades over the last `days`,
    counting each firm's latest action once. `rows` are dicts with Firm,
    Action, currentPriceTarget, priorPriceTarget and a `when` datetime."""
    since = now - timedelta(days=days)
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        when = r.get("when")
        if when is None or when < since:
            continue
        firm = str(r.get("Firm") or "").strip() or f"#{len(latest)}"
        if firm not in latest or when > latest[firm]["when"]:
            latest[firm] = r
    out = {"raises": 0, "cuts": 0, "upgrades": 0, "downgrades": 0, "firms": len(latest), "days": days}
    for r in latest.values():
        cur, pri = _num(r.get("currentPriceTarget")), _num(r.get("priorPriceTarget"))
        if cur and pri:
            if cur > pri:
                out["raises"] += 1
            elif cur < pri:
                out["cuts"] += 1
        act = str(r.get("Action") or "").lower()
        if act == "up":
            out["upgrades"] += 1
        elif act == "down":
            out["downgrades"] += 1
    return out


def earnings_moves(reports: List[Tuple[datetime, bool]], closes: List[Tuple[date, float]],
                   market: str = "US", limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """The close-to-close reaction to each past report: a report before the
    open moves that session; one after it moves the next. `reports` are
    (exchange-time datetime, reported) pairs; unreported ones are skipped."""
    limit = int(limit or M.EARNINGS_HISTORY_REPORTS)
    if not closes:
        return []
    days = [d for d, _ in closes]
    px = [c for _, c in closes]
    oh, om = _SESSION_OPEN.get(market, _SESSION_OPEN["US"])
    out: List[Dict[str, Any]] = []
    for when, reported in sorted(reports, key=lambda x: x[0], reverse=True):
        if not reported:
            continue
        d = when.date()
        before_open = (when.hour, when.minute) < (oh, om)
        try:
            if before_open:
                i = days.index(d) if d in days else None
            else:
                i = next((k for k, dd in enumerate(days) if dd > d), None)
        except ValueError:
            i = None
        if i is None or i == 0:
            continue
        move = (px[i] / px[i - 1] - 1) * 100
        out.append({"date": d.isoformat(), "move_pct": round(move, 2), "timing": "bmo" if before_open else "amc"})
        if len(out) >= limit:
            break
    return out


def _mid(row: Dict[str, Any]) -> Optional[float]:
    bid, ask, last = _num(row.get("bid")), _num(row.get("ask")), _num(row.get("lastPrice"))
    if bid and ask and ask >= bid > 0:
        return (bid + ask) / 2
    return last if last and last > 0 else None


def _nearest(rows: List[Dict[str, Any]], strike: float) -> Optional[Dict[str, Any]]:
    rows = [r for r in rows if _num(r.get("strike"))]
    return min(rows, key=lambda r: abs(float(r["strike"]) - strike)) if rows else None


def implied_move(calls: List[Dict[str, Any]], puts: List[Dict[str, Any]], spot: float) -> Optional[float]:
    """The at-the-money straddle as a share of price, in percent: what the
    option market charges for a move of either sign through that expiry."""
    c, p = _nearest(calls, spot), _nearest(puts, spot)
    if not c or not p or spot <= 0:
        return None
    mc, mp = _mid(c), _mid(p)
    return round((mc + mp) / spot * 100, 2) if mc and mp else None


def strike_iv(calls: List[Dict[str, Any]], puts: List[Dict[str, Any]], strike: float, spot: float) -> Optional[float]:
    """Implied volatility at the strike nearest `strike` (the out-of-the-money
    side, which trades more), falling back to the money; None when the quote is
    outside the sane band."""
    lo, hi = float(M.IMPLIED_VOL_MIN), float(M.IMPLIED_VOL_MAX)
    side = calls if strike >= spot else puts
    for k in (strike, spot):
        row = _nearest(side, k)
        iv = _num(row.get("impliedVolatility")) if row else None
        if iv is not None and lo <= iv <= hi:
            return round(iv, 4)
    return None


def pick_expiry(expiries: Iterable[str], on_or_after: date, prefer_days: Optional[int] = None) -> Optional[str]:
    """The first expiry on or after a date or, with `prefer_days`, the one
    nearest that many days out among those at least half as far."""
    parsed = []
    for e in expiries or []:
        try:
            parsed.append((datetime.strptime(str(e)[:10], "%Y-%m-%d").date(), str(e)))
        except ValueError:
            continue
    parsed.sort()
    if prefer_days:
        target = on_or_after + timedelta(days=prefer_days)
        far = [p for p in parsed if (p[0] - on_or_after).days >= prefer_days // 2]
        return min(far, key=lambda p: abs((p[0] - target).days))[1] if far else None
    return next((e for d, e in parsed if d >= on_or_after), None)


# ---------------------------------------------------------------------------
# Provider reads (cached)
# ---------------------------------------------------------------------------
def _yf():
    import yfinance as yf_local
    return yf_local


def _history(sym: str) -> Optional[List[List[Any]]]:
    def fetch():
        h = _yf().Ticker(sym).history(period="3y", interval="1d", auto_adjust=True, timeout=10)
        if h is None or h.empty:
            return None
        s = h["Close"].dropna()
        return [[d.date().isoformat(), round(float(v), 4)] for d, v in s.items()]
    return cached(_HISTORY, f"h|{sym}", fetch)


def _revisions(sym: str) -> Optional[List[Dict[str, Any]]]:
    def fetch():
        df = _yf().Ticker(sym).upgrades_downgrades
        if df is None or df.empty:
            return []
        rows = []
        for when, r in df.head(200).iterrows():
            rows.append({"when": str(when), "Firm": r.get("Firm"), "Action": r.get("Action"),
                         "ToGrade": r.get("ToGrade"), "FromGrade": r.get("FromGrade"),
                         "currentPriceTarget": _num(r.get("currentPriceTarget")),
                         "priorPriceTarget": _num(r.get("priorPriceTarget"))})
        return rows
    return cached(_FAST, f"ud|{sym}", fetch)


def _parse_when(text: Any) -> Optional[datetime]:
    try:
        w = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return w.replace(tzinfo=None) if w.tzinfo else w
    except Exception:
        return None


def _eps_trend(sym: str) -> Optional[Dict[str, Any]]:
    def fetch():
        df = _yf().Ticker(sym).eps_trend
        if df is None or df.empty or "0y" not in df.index:
            return {}
        return {k: _num(v) for k, v in df.loc["0y"].items()}
    return cached(_SLOW, f"eps|{sym}", fetch)


def _earnings_dates(sym: str) -> Optional[List[List[Any]]]:
    def fetch():
        df = _yf().Ticker(sym).get_earnings_dates(limit=int(M.EARNINGS_HISTORY_REPORTS) + 4)
        if df is None or df.empty:
            return []
        col = "Reported EPS" if "Reported EPS" in df.columns else None
        return [[w.isoformat(), bool(col and _num(r.get(col)) is not None)] for w, r in df.iterrows()]
    return cached(_SLOW, f"ed|{sym}", fetch)


def _options(sym: str) -> Optional[Dict[str, Any]]:
    def fetch():
        return {"expiries": list(_yf().Ticker(sym).options or [])}
    return cached(_FAST, f"ox|{sym}", fetch)


def _chain(sym: str, expiry: str) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    def fetch():
        ch = _yf().Ticker(sym).option_chain(expiry)
        keep = ["strike", "bid", "ask", "lastPrice", "impliedVolatility"]
        pack = lambda df: [{k: _num(v) for k, v in r.items() if k in keep} for _, r in df[keep].iterrows()] if df is not None and not df.empty else []
        return {"calls": pack(ch.calls), "puts": pack(ch.puts)}
    return cached(_FAST, f"oc|{sym}|{expiry}", fetch)


def quote_change(sym: str) -> Optional[Dict[str, float]]:
    def fetch():
        fi = _yf().Ticker(sym).fast_info
        last, prev = _num(fi["lastPrice"]), _num(fi["previousClose"])
        if not last or not prev:
            return None
        return {"last": last, "change_pct": round((last / prev - 1) * 100, 2)}
    try:
        return cached(_QUOTES, f"q|{sym}", fetch)
    except Exception as exc:
        logger.debug("quote %s failed: %s", sym, exc)
        return None


def risk_free(market: str) -> Tuple[float, str]:
    if market == "US":
        q = quote_change("^IRX")
        if q and q.get("last"):
            return round(q["last"] / 100.0, 4), "3-month T-bill"
    return float(dict(M.RISK_FREE_FALLBACK).get(market, 0.04)), "set rate"


def _safe(fn: Callable[[], Any], what: str) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.debug("discover %s failed: %s", what, exc)
        return None


def idea_read(sym: str, market: str, price: Any, target: Any, earnings_date: Any = None,
              earnings_timing: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything Discover adds to a Street candidate: volatility, momentum,
    revisions, the odds of its target and, when it reports soon, the move the
    option market prices and the moves of its past reports."""
    now = now or datetime.now()
    out: Dict[str, Any] = {"symbol": sym}
    s, k = _num(price), _num(target)
    hist = _safe(lambda: _history(sym), f"history {sym}") or []
    closes = [c for _, c in hist]
    out["vol_1y"] = realized_vol(closes)
    out["momentum_12_1"] = momentum_12_1(closes)
    out["return_3m"] = round((closes[-1] / closes[-64] - 1) * 100, 2) if len(closes) > 64 else None
    # About three months of daily closes for the price line beside the quote.
    out["spark"] = [round(c, 2) for c in closes[-64:]] if len(closes) >= 20 else None

    ud = _safe(lambda: _revisions(sym), f"revisions {sym}")
    if ud is not None:
        rows = [{**r, "when": _parse_when(r.get("when"))} for r in ud]
        out["revisions"] = revisions_summary(rows, now, int(M.REVISION_WINDOW_DAYS))
    eps = _safe(lambda: _eps_trend(sym), f"eps {sym}") or {}
    cur, ago = eps.get("current"), eps.get(str(M.EPS_REVISION_WINDOW))
    out["eps_revision_pct"] = round((cur / ago - 1) * 100, 2) if cur and ago and ago > 0 else None

    sigma, source, expiry = None, None, None
    ox = _safe(lambda: _options(sym), f"options {sym}") if market == "US" else None
    expiries = (ox or {}).get("expiries") or []
    if s and k and expiries:
        expiry = pick_expiry(expiries, now.date(), int(M.TARGET_ODDS_HORIZON_DAYS))
        ch = _safe(lambda: _chain(sym, expiry), f"chain {sym}") if expiry else None
        if ch:
            sigma = strike_iv(ch["calls"], ch["puts"], k, s)
            source = "options" if sigma else None
    if sigma is None and out["vol_1y"]:
        sigma, source, expiry = out["vol_1y"], "history", None
    rate, rate_src = risk_free(market)
    years = float(M.TARGET_ODDS_HORIZON_DAYS) / 365.0
    out["odds"] = {"p": target_odds(s, k, sigma, rate, years), "sigma": sigma, "source": source,
                   "expiry": expiry, "rate": rate, "rate_source": rate_src, "horizon_days": int(M.TARGET_ODDS_HORIZON_DAYS)} if sigma else None

    # Earnings inside the near window: the option market's move and past reactions.
    ed = None
    try:
        ed = datetime.strptime(str(earnings_date)[:10], "%Y-%m-%d").date() if earnings_date else None
    except ValueError:
        ed = None
    near = int(_T.WHY.EARNINGS_NEAR_DAYS)
    if ed and 0 <= (ed - now.date()).days <= near:
        er: Dict[str, Any] = {"date": ed.isoformat(), "implied_move_pct": None, "expiry": None}
        if s and expiries:
            first = ed + timedelta(days=1) if earnings_timing == "amc" else ed
            ex = pick_expiry(expiries, first)
            ch = _safe(lambda: _chain(sym, ex), f"chain {sym}") if ex else None
            if ch:
                er["implied_move_pct"] = implied_move(ch["calls"], ch["puts"], s)
                er["expiry"] = ex
        reps = _safe(lambda: _earnings_dates(sym), f"earnings dates {sym}") or []
        reports = []
        for w, reported in reps:
            dt = _parse_when_local(w, market)
            if dt is not None:
                reports.append((dt, reported))
        moves = earnings_moves(reports, [(date.fromisoformat(d), c) for d, c in hist], market)
        er["history"] = moves
        er["avg_abs_move_pct"] = round(sum(abs(m["move_pct"]) for m in moves) / len(moves), 2) if moves else None
        out["earnings"] = er
    return out


def _parse_when_local(text: Any, market: str) -> Optional[datetime]:
    try:
        w = datetime.fromisoformat(str(text))
    except Exception:
        return None
    try:
        from zoneinfo import ZoneInfo
        if w.tzinfo:
            w = w.astimezone(ZoneInfo(_TZ.get(market, _TZ["US"])))
    except Exception:
        pass
    return w.replace(tzinfo=None)


def analyst_action_today(rows: List[Dict[str, Any]], now: datetime) -> Optional[Dict[str, Any]]:
    """The newest rating change or target change in the last day, as a reason."""
    since = now - timedelta(days=1)
    recent = [r for r in rows if r.get("when") and r["when"] >= since]
    if not recent:
        return None
    r = max(recent, key=lambda x: x["when"])
    act = str(r.get("Action") or "").lower()
    firm = str(r.get("Firm") or "An analyst")
    cur, pri = _num(r.get("currentPriceTarget")), _num(r.get("priorPriceTarget"))
    if act == "up":
        label, verb = "Upgrade", f"upgraded it to {r.get('ToGrade')}"
    elif act == "down":
        label, verb = "Downgrade", f"downgraded it to {r.get('ToGrade')}"
    elif act == "init":
        label, verb = "New coverage", f"started coverage at {r.get('ToGrade')}"
    elif cur and pri and cur != pri:
        label = "Target raised" if cur > pri else "Target cut"
        verb = f"moved its target from {pri:,.2f} to {cur:,.2f}"
    else:
        return None
    return {"label": label, "detail": f"{firm} {verb}.", "firm": firm}


def mover_read(sym: str, market: str, change_pct: Any, sector: Optional[str] = None,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """A mover's sector fund move and any analyst action in the last day."""
    now = now or datetime.now()
    out: Dict[str, Any] = {"symbol": sym, "sector": sector}
    if not sector:
        f = fundamentals_cached(sym)
        out["sector"] = sector = (f or {}).get("sector") or None
        if f is None:
            info = _safe(lambda: _yf().Ticker(sym).info or {}, f"info {sym}") or {}
            out["sector"] = sector = info.get("sector") or None
    fund = dict(dict(M.SECTOR_FUND_BY_SECTOR).get(market, {})).get(sector or "")
    out["fund"] = fund
    q = quote_change(fund) if fund else None
    out["fund_change_pct"] = q.get("change_pct") if q else None
    chg = _num(change_pct)
    out["excess_pct"] = round(chg - out["fund_change_pct"], 2) if chg is not None and out["fund_change_pct"] is not None else None
    ud = _safe(lambda: _revisions(sym), f"revisions {sym}") or []
    out["analyst"] = analyst_action_today([{**r, "when": _parse_when(r.get("when"))} for r in ud], now)
    return out


def fundamentals_cached(sym: str) -> Optional[Dict[str, Any]]:
    return FUNDAMENTALS.get(sym)


# ---------------------------------------------------------------------------
# Enrichment jobs: one future per (kind, symbol), reused while running or fresh.
# ---------------------------------------------------------------------------
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="discover-enrich")
_jobs: Dict[str, Tuple[float, Any]] = {}
_jobs_lock = threading.Lock()


def submit(key: str, ttl: float, fn: Callable[..., Any], *args: Any):
    with _jobs_lock:
        hit = _jobs.get(key)
        if hit is not None:
            started, fut = hit
            if not fut.done() or (time.time() - started < ttl and fut.exception() is None):
                return fut
        fut = _executor.submit(fn, *args)
        _jobs[key] = (time.time(), fut)
        return fut


def collect(futures: Dict[str, Any], wait: float = 0.0) -> Tuple[Dict[str, Any], List[str]]:
    """Results of the finished futures and the keys still running, after waiting
    up to `wait` seconds for stragglers."""
    deadline = time.time() + max(0.0, wait)
    for fut in futures.values():
        left = deadline - time.time()
        if left <= 0:
            break
        try:
            fut.result(timeout=left)
        except Exception:
            pass
    done, pending = {}, []
    for k, fut in futures.items():
        if fut.done():
            try:
                done[k] = fut.result()
            except Exception as exc:
                logger.debug("enrich %s failed: %s", k, exc)
                done[k] = None
        else:
            pending.append(k)
    return done, pending
