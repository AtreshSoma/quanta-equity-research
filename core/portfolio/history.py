"""Performance history, attribution and historical outcomes, as pure functions.

Three rules run through this module:

* Nothing is invented. A holding without prices for the whole window is left out
  and named; it is never held flat. Coverage (the share of today's invested value
  that a figure describes) always travels with the figure.
* What is *actual* and what is *simulated* are different objects with different
  names. :func:`simulate_current_holdings` replays TODAY's share counts over past
  prices; it is a what-if about the current book, never the account's history.
  :func:`ledger_performance` is the only function that may produce an actual
  return, and only from an order ledger that reconciles to the current positions.
* Every rate carries its definition (what counts as a success, the horizon, the
  denominator) and its sample size. Below :data:`MIN_OUTCOMES` it says so instead
  of drawing a percentage.

Price basis: closing prices, split-adjusted, dividends excluded, on both the
holdings and the benchmark, so the two sides are always compared like for like.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from engine import thresholds as _T

TRADING_DAYS = 252
MIN_OUTCOMES = _T.PORTFOLIO.OUTCOME_MIN_SAMPLE  # completed outcomes needed before a hit rate is drawn
MIN_COVERAGE = 0.6           # share of invested value a simulation must describe
MAX_GAP_DAYS = 5             # longest run of missing closes tolerated inside a window
RANGES = ("1W", "1M", "3M", "6M", "YTD", "1Y", "MAX")
_RANGE_DAYS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}

PRICE_BASIS = "Closing prices, split-adjusted, dividends excluded (same basis for holdings and benchmark)."


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _iso(d: Any) -> str:
    return d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10]


# ---------------------------------------------------------------------------
# Small statistics
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = 1.96) -> Optional[Tuple[float, float]]:
    """95% Wilson score interval for a proportion; None when n is 0."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def max_drawdown(values: Sequence[float], dates: Sequence[str]) -> Dict[str, Any]:
    """Largest peak-to-trough fall of a value series, with the dates that bound it."""
    peak, peak_i = -math.inf, 0
    worst, worst_peak, worst_trough = 0.0, 0, 0
    for i, v in enumerate(values):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1 if peak > 0 else 0.0
        if dd < worst:
            worst, worst_peak, worst_trough = dd, peak_i, i
    if not values:
        return {"depth": None, "peak_date": None, "trough_date": None}
    return {"depth": worst, "peak_date": dates[worst_peak] if worst < 0 else None,
            "trough_date": dates[worst_trough] if worst < 0 else None}


def drawdown_series(values: Sequence[float]) -> List[float]:
    peak, out = -math.inf, []
    for v in values:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def annualised_vol(returns: Sequence[float]) -> Optional[float]:
    r = [x for x in returns if x is not None and math.isfinite(x)]
    if len(r) < 20:
        return None
    m = sum(r) / len(r)
    var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


# ---------------------------------------------------------------------------
# Price frames
# ---------------------------------------------------------------------------

def _window_start(dates: List[str], range_key: str) -> int:
    """Index of the first row of the window (inclusive)."""
    if not dates:
        return 0
    if range_key == "MAX":
        return 0
    if range_key == "YTD":
        year = dates[-1][:4]
        for i, d in enumerate(dates):
            if d[:4] == year:
                # The base is the last close of the prior year, when we have it.
                return max(0, i - 1)
        return 0
    n = _RANGE_DAYS.get(range_key, 126)
    return max(0, len(dates) - 1 - n)


def _column(frame: Any, symbol: str) -> Optional[List[Optional[float]]]:
    if frame is None or getattr(frame, "empty", True) or symbol not in frame.columns:
        return None
    return [_num(v) for v in frame[symbol].tolist()]


def _has_gap(col: Sequence[Optional[float]], start: int, end: int) -> bool:
    run = 0
    for v in col[start:end + 1]:
        run = run + 1 if v is None else 0
        if run > MAX_GAP_DAYS:
            return True
    return False


def _fill(col: Sequence[Optional[float]], start: int, end: int) -> List[float]:
    """Forward-fill only the short calendar mismatches _has_gap already allowed."""
    out, last = [], None
    for v in col[start:end + 1]:
        last = v if v is not None else last
        out.append(last)  # type: ignore[arg-type]
    return out  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Current-holdings simulation
# ---------------------------------------------------------------------------

def simulate_current_holdings(holdings: Sequence[Dict[str, Any]], closes: Any, benchmark: str,
                              range_key: str = "6M") -> Dict[str, Any]:
    """Replay today's share counts over past closes, against a benchmark.

    ``holdings``: ``[{symbol, quantity, value}]`` (value = today's market value).
    ``closes``: DataFrame of daily closes, one column per symbol including the
    benchmark. Returns a labelled simulation, or ``available: False`` with the
    reason. This is NOT the account's performance: shares bought after the start
    date are treated as if held throughout, shares sold since are ignored, and
    cash flows, dividends and fees are absent.
    """
    label = "Current holdings simulation"
    base = {"kind": "simulation", "label": label, "range": range_key, "benchmark": benchmark,
            "basis": PRICE_BASIS,
            "method": ("Today's share counts are valued at each past close. Purchases and sales made during "
                       "the window, cash, dividends, fees and deposits are not reflected, so this shows how the "
                       "current book would have behaved, not what the account earned."),
            "available": False, "excluded": []}
    if closes is None or getattr(closes, "empty", True) or benchmark not in closes.columns:
        return dict(base, reason="Price history is unavailable.")
    dates = [_iso(d) for d in closes.index]
    bench_col = _column(closes, benchmark)
    if not bench_col or sum(v is not None for v in bench_col) < 20:
        return dict(base, reason="Benchmark history is too short.")
    s = _window_start(dates, range_key)
    # Trim to the benchmark's own trading days; every series is aligned to them.
    idx = [i for i in range(s, len(dates)) if bench_col[i] is not None]
    if len(idx) < 5:
        return dict(base, reason="Not enough trading days in this range.")
    a, b = idx[0], idx[-1]

    priced = [h for h in holdings if _num(h.get("value")) and _num(h.get("quantity"))]
    total_now = sum(_num(h["value"]) or 0.0 for h in priced)
    used: List[Dict[str, Any]] = []
    excluded: List[Dict[str, str]] = []
    for h in priced:
        col = _column(closes, h["symbol"])
        if col is None or col[a] is None:
            excluded.append({"symbol": h["symbol"], "reason": "No price on the first day of this range"})
            continue
        if col[b] is None and all(v is None for v in col[max(a, b - MAX_GAP_DAYS):b + 1]):
            excluded.append({"symbol": h["symbol"], "reason": "No recent price history"})
            continue
        if _has_gap(col, a, b):
            excluded.append({"symbol": h["symbol"], "reason": "Price history has gaps inside this range"})
            continue
        used.append({"symbol": h["symbol"], "quantity": float(h["quantity"]), "value": float(h["value"]),
                     "col": col})
    if not used:
        return dict(base, reason="No holding has price history for this range.", excluded=excluded)
    covered_now = sum(u["value"] for u in used)
    coverage = covered_now / total_now if total_now else 0.0
    base.update(excluded=excluded, coverage=coverage)
    if coverage < MIN_COVERAGE:
        return dict(base, reason=f"Only {coverage:.0%} of invested value has price history for this range; "
                                 f"a simulation needs at least {MIN_COVERAGE:.0%}.")

    d_sel = [dates[i] for i in idx]
    series = {u["symbol"]: _fill(u["col"], a, b) for u in used}
    # _fill runs over [a, b]; pick the benchmark's trading days out of it.
    offset = {i: i - a for i in idx}
    value = []
    for i in idx:
        v = 0.0
        for u in used:
            px = series[u["symbol"]][offset[i]]
            if px is None:
                v = math.nan
                break
            v += u["quantity"] * px
        value.append(v)
    if any(not math.isfinite(v) for v in value) or value[0] <= 0:
        return dict(base, reason="Price history could not be aligned.")
    bench = [bench_col[i] for i in idx]
    port_idx = [v / value[0] * 100 for v in value]
    bench_idx = [v / bench[0] * 100 for v in bench]
    rets = [value[i] / value[i - 1] - 1 for i in range(1, len(value))]
    brets = [bench[i] / bench[i - 1] - 1 for i in range(1, len(bench))]
    dd = max_drawdown(value, d_sel)
    bdd = max_drawdown(bench, d_sel)
    contrib = []
    for u in used:
        p0, p1 = series[u["symbol"]][0], series[u["symbol"]][-1]
        dollars = u["quantity"] * (p1 - p0)
        contrib.append({"symbol": u["symbol"], "dollars": dollars, "points": dollars / value[0] * 100,
                        "return_pct": (p1 / p0 - 1) * 100 if p0 else None,
                        "start_price": p0, "end_price": p1})
    contrib.sort(key=lambda c: -abs(c["dollars"]))
    # Retrospective what-if, decided with information available on the first day: the same names,
    # equal-weighted at the start, never rebalanced. Survivorship applies: these are names held today.
    eq = [c["end_price"] / c["start_price"] - 1 for c in contrib if c["start_price"]]
    # Weight drift over the window: each name's share of the covered book at the start vs the end.
    start_total = sum(u["quantity"] * series[u["symbol"]][0] for u in used)
    end_total = sum(u["quantity"] * series[u["symbol"]][-1] for u in used)
    drift = [{"symbol": u["symbol"],
              "start": u["quantity"] * series[u["symbol"]][0] / start_total,
              "end": u["quantity"] * series[u["symbol"]][-1] / end_total} for u in used]
    drift.sort(key=lambda d: -abs(d["end"] - d["start"]))
    return dict(
        base, available=True,
        dates=d_sel, portfolio=port_idx, benchmark_series=bench_idx,
        value=value, drawdown=drawdown_series(value), benchmark_drawdown=drawdown_series(bench),
        stats={
            "start": d_sel[0], "end": d_sel[-1], "days": len(d_sel) - 1,
            "portfolio_return": value[-1] / value[0] - 1, "benchmark_return": bench[-1] / bench[0] - 1,
            "excess": (value[-1] / value[0]) - (bench[-1] / bench[0]),
            "max_drawdown": dd, "benchmark_max_drawdown": bdd,
            "volatility": annualised_vol(rets), "benchmark_volatility": annualised_vol(brets),
            "start_value": value[0], "end_value": value[-1],
            "equal_weight_return": (sum(eq) / len(eq)) if eq else None,
        },
        weight_drift=drift,
        contributions=contrib,
        contribution_total_points=sum(c["points"] for c in contrib),
    )


def session_of(stamp: str) -> str:
    """``pre``, ``regular`` or ``post`` for an ISO timestamp in New York time."""
    hhmm = str(stamp)[11:16]
    if hhmm < "09:30":
        return "pre"
    return "regular" if hhmm < "16:00" else "post"


def simulate_intraday(holdings: Sequence[Dict[str, Any]], stamps: Sequence[str],
                      prices: Dict[str, Sequence[Optional[float]]], prev_close: Dict[str, float],
                      benchmark: str) -> Dict[str, Any]:
    """Today's share counts over one day's intraday bars, measured from the previous close.

    ``stamps``: ascending ISO timestamps in New York time, all on one trading date.
    ``prices``: ``{symbol: [last price or None per stamp]}``, benchmark included.
    ``prev_close``: each symbol's previous regular-session close, the base of the day.

    A price carries forward from a symbol's last trade (it is the last price, not an
    interpolation). Before its first trade of the day a holding is valued at the
    previous close. A holding with no bar at all today, or no previous close, is left
    out and named. Like :func:`simulate_current_holdings`, this replays today's shares;
    it is not the account's own record.
    """
    base = {"kind": "simulation", "label": "Current holdings simulation", "range": "1D", "benchmark": benchmark,
            "basis": "Intraday last prices, 5-minute bars including pre- and post-market, measured from the previous close.",
            "method": ("Today's share counts valued at each 5-minute bar, from the previous close. Trades made today, "
                       "cash, dividends and fees are not reflected."),
            "available": False, "excluded": []}
    n = len(stamps)
    bench = list(prices.get(benchmark) or [])
    b0 = _num(prev_close.get(benchmark))
    if n < 2 or len(bench) != n or not b0 or all(v is None for v in bench):
        return dict(base, reason="Intraday prices for the benchmark are unavailable.")

    def carried(col: Sequence[Optional[float]], start: float) -> List[float]:
        out, last = [], start
        for v in col:
            if _num(v):
                last = float(v)
            out.append(last)
        return out

    priced = [h for h in holdings if _num(h.get("value")) and _num(h.get("quantity"))]
    total_now = sum(_num(h["value"]) or 0.0 for h in priced)
    used, excluded = [], []
    for h in priced:
        sym = h["symbol"]
        col = list(prices.get(sym) or [])
        p0 = _num(prev_close.get(sym))
        if not p0:
            excluded.append({"symbol": sym, "reason": "No previous close"})
            continue
        if len(col) != n or all(not _num(v) for v in col):
            excluded.append({"symbol": sym, "reason": "No intraday prices today"})
            continue
        used.append({"symbol": sym, "quantity": float(h["quantity"]), "value": float(h["value"]),
                     "p0": p0, "path": carried(col, p0)})
    coverage = (sum(u["value"] for u in used) / total_now) if total_now else 0.0
    base.update(excluded=excluded, coverage=coverage)
    if not used:
        return dict(base, reason="No holding has intraday prices today.")
    if coverage < MIN_COVERAGE:
        return dict(base, reason=f"Only {coverage:.0%} of invested value has intraday prices; "
                                 f"a simulation needs at least {MIN_COVERAGE:.0%}.")
    start_value = sum(u["quantity"] * u["p0"] for u in used)
    value = [sum(u["quantity"] * u["path"][i] for u in used) for i in range(n)]
    bpath = carried(bench, b0)
    port_idx = [v / start_value * 100 for v in value]
    bench_idx = [v / b0 * 100 for v in bpath]
    return dict(
        base, available=True, dates=list(stamps), sessions=[session_of(s) for s in stamps],
        portfolio=port_idx, benchmark_series=bench_idx, value=value,
        stats={"start": str(stamps[0])[:10], "end": str(stamps[-1])[:10], "start_value": start_value, "end_value": value[-1],
               "portfolio_return": value[-1] / start_value - 1, "benchmark_return": bpath[-1] / b0 - 1,
               "excess": value[-1] / start_value - bpath[-1] / b0},
    )


# ---------------------------------------------------------------------------
# Open-lot attribution (actual purchase dates and prices, surviving lots only)
# ---------------------------------------------------------------------------

def open_lot_attribution(positions: Sequence[Dict[str, Any]], closes: Any, start: str, end: Optional[str] = None
                         ) -> Dict[str, Any]:
    """What the lots still held contributed since ``start``.

    Uses the broker's tax lots: a lot bought before ``start`` is valued from the
    close on ``start``; a lot bought inside the window from its actual cost per
    share. Lots that were SOLD are invisible here (Robinhood reports open lots
    only), so this is survivor-biased by construction and says so. Realized
    results, dividends and fees are separate lines the caller must not fold in.
    """
    note = ("Open lots only. Positions sold during the period, dividends and fees are not in this attribution; "
            "the broker reports realized results separately.")
    out: Dict[str, Any] = {"kind": "open_lots", "start": start, "note": note, "available": False}
    if closes is None or getattr(closes, "empty", True):
        return dict(out, reason="Price history is unavailable.")
    dates = [_iso(d) for d in closes.index]
    end = end or dates[-1]
    rows, unknown = [], []
    deployed = 0.0
    for p in positions:
        sym = p["symbol"]
        lots = p.get("tax_lots")
        col = _column(closes, sym)
        if lots is None or not lots or col is None:
            unknown.append({"symbol": sym, "reason": "No tax lots" if not lots else "No price history"})
            continue
        # Close on/after start; last close on/before end.
        try:
            i0 = next(i for i, d in enumerate(dates) if d >= start and col[i] is not None)
            i1 = max(i for i, d in enumerate(dates) if d <= end and col[i] is not None)
        except (StopIteration, ValueError):
            unknown.append({"symbol": sym, "reason": "No price history in this range"})
            continue
        p_start, p_end = col[i0], col[i1]
        dollars, base_value, used_qty = 0.0, 0.0, 0.0
        for lot in lots:
            q, cost, acq = _num(lot.get("quantity")), _num(lot.get("cost_per_share")), lot.get("acquired_on")
            if not q or q <= 0:
                continue
            if acq and acq > end:
                continue
            if acq and acq > start:
                if cost is None:
                    unknown.append({"symbol": sym, "reason": "A lot bought in this period has no cost"})
                    continue
                entry = cost
            else:
                entry = p_start
            dollars += q * (p_end - entry)
            base_value += q * entry
            used_qty += q
        if used_qty:
            rows.append({"symbol": sym, "dollars": dollars, "base": base_value, "quantity": used_qty,
                         "return_pct": dollars / base_value * 100 if base_value else None})
            deployed += base_value
    if not rows:
        return dict(out, reason="No lots with price history for this range.", unknown=unknown)
    for r in rows:
        r["points"] = r["dollars"] / deployed * 100 if deployed else None
    rows.sort(key=lambda r: -abs(r["dollars"]))
    return dict(out, available=True, rows=rows, unknown=unknown, deployed=deployed,
                total_dollars=sum(r["dollars"] for r in rows),
                total_points=sum(r["points"] or 0 for r in rows),
                symbols_covered=len(rows), symbols_total=len(positions))


# ---------------------------------------------------------------------------
# Ledger reconstruction (the only route to an actual return)
# ---------------------------------------------------------------------------

@dataclass
class Fill:
    symbol: str
    side: str          # "buy" | "sell"
    quantity: float
    price: float
    at: str            # ISO timestamp / date
    fees: float = 0.0
    instrument_type: str = "equity"
    order_id: Optional[str] = None
    execution_id: Optional[str] = None
    reported_price: Optional[float] = None
    notional: Optional[float] = None


def normalize_orders(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Executed equity orders -> fills. Anything unreadable is counted, not guessed.

    Robinhood's order payload is not among the recorded schemas, so field names
    are read from short candidate lists; a fill missing symbol, side, quantity,
    price or time is rejected. The reconciliation against current positions in
    :func:`reconcile_ledger` is what decides whether the result is trusted.
    """
    def pick(rec: Dict[str, Any], keys: Sequence[str]) -> Any:
        for k in keys:
            cur: Any = rec
            for part in k.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur not in (None, ""):
                return cur
        return None

    fills: List[Fill] = []
    rejected = 0
    for rec in records:
        state = str(pick(rec, ("state", "status")) or "").lower()
        done = _num(pick(rec, ("cumulative_quantity", "filled_quantity", "executed_quantity")))
        # A cancelled order that partly filled is a real execution at its filled quantity.
        partial = state in ("cancelled", "canceled") and done is not None and done > 0
        if state and state not in ("filled", "executed", "partially_filled_closed", "complete", "completed") and not partial:
            continue                                   # cancelled / open / rejected orders are not fills
        sym = pick(rec, ("symbol", "instrument.symbol"))
        side = str(pick(rec, ("side", "direction")) or "").lower()
        qty = _num(pick(rec, ("cumulative_quantity", "filled_quantity", "executed_quantity", "quantity")))
        px = _num(pick(rec, ("average_price", "executed_price", "avg_price", "price")))
        at = pick(rec, ("last_transaction_at", "executed_at", "filled_at", "updated_at", "created_at"))
        fee = _num(pick(rec, ("fees", "fee"))) or 0.0
        if not sym or side not in ("buy", "sell") or not qty or qty <= 0 or not px or px <= 0 or not at:
            rejected += 1
            continue
        fills.append(Fill(str(sym).upper(), side, qty, px, str(at), fee,
                          str(pick(rec, ("instrument_type", "instrument.type")) or "equity").lower()))
    fills.sort(key=lambda f: f.at)
    return {"fills": fills, "rejected": rejected}


def normalize_crypto_orders(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Official crypto-order executions, including fills before cancellation.

    currency_code identifies the asset; the source identifies its class. Requested
    quantities, collared order prices and order-update times are never executions.
    effective_price includes venue adjustments; do not subtract fees a second time.
    """
    fills, rejected = [], 0
    reasons: Dict[str, int] = {}

    def reject(reason):
        nonlocal rejected
        rejected += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    for rec in records:
        symbol, side = rec.get("currency_code"), rec.get("side")
        executions = rec.get("executions") or []
        if not isinstance(executions, list):
            reject("invalid_executions_structure")
            continue
        if not executions and (_num(rec.get("cumulative_quantity")) or 0) > 0:
            reject(rec.get("detail_error") or "missing_executions")
        for execution in executions:
            if not isinstance(execution, dict):
                reject("invalid_execution_structure")
                continue
            qty = _num(execution.get("quantity"))
            price = _num(execution.get("effective_price"))
            at = execution.get("timestamp")
            if not symbol or side not in ("buy", "sell") or not qty or qty <= 0 or not price or price <= 0:
                reject("missing_or_invalid_fill_fields")
                continue
            try:
                parsed = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("Execution timestamps must include UTC offset")
            except (ValueError, TypeError):
                reject("invalid_execution_timestamp")
                continue
            fills.append(Fill(str(symbol).upper(), side, qty, price,
                              parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), instrument_type="crypto",
                              order_id=rec.get("id"), execution_id=execution.get("id"),
                              reported_price=_num(execution.get("price")), notional=_num(execution.get("notional"))))
    fills.sort(key=lambda f: f.at)
    return {"fills": fills, "rejected": rejected, "rejection_reasons": reasons}


def reconcile_ledger(fills: Sequence[Fill], positions: Sequence[Dict[str, Any]], tol: float = 1e-4
                     ) -> Dict[str, Any]:
    """Do the fills, replayed, end at exactly today's share counts?"""
    held: Dict[str, float] = {}
    for f in fills:
        held[f.symbol] = held.get(f.symbol, 0.0) + (f.quantity if f.side == "buy" else -f.quantity)
    now = {p["symbol"]: float(p["quantity"]) for p in positions}
    mismatched = []
    for sym in sorted(set(held) | set(now)):
        a, b = held.get(sym, 0.0), now.get(sym, 0.0)
        if abs(a - b) > max(tol, 1e-6 * max(abs(a), abs(b))):
            mismatched.append({"symbol": sym, "ledger": a, "account": b})
    first = fills[0].at[:10] if fills else None
    return {"ok": bool(fills) and not mismatched, "fills": len(fills), "first_fill": first,
            "mismatched": mismatched}


def ledger_performance(fills: Sequence[Fill], closes: Any, benchmark: str, range_key: str = "1Y"
                       ) -> Dict[str, Any]:
    """Time-weighted return of the EQUITY SLEEVE from a reconciled fill ledger.

    Method: daily value V_t = sum(shares_t x close_t). A buy or sell moves money
    between cash and the sleeve, so it is an external flow F_t (net cost of that
    day's fills, at the fill prices). The day's return is (V_t - F_t) / V_(t-1),
    and days are chained. Cash, dividends and deposits are outside the sleeve, so
    the figure is a price return on the stocks and funds held, compared with the
    benchmark's price return over the same dates. It is not a money-weighted
    account return.
    """
    base = {"kind": "ledger", "label": "Equity sleeve return (from order ledger)", "range": range_key,
            "benchmark": benchmark, "basis": PRICE_BASIS, "available": False,
            "method": ("Time-weighted: each day's return is the change in value of the shares held, after "
                       "removing the cost of that day's purchases and the proceeds of that day's sales. "
                       "Cash, dividends, fees and deposits are outside this sleeve.")}
    if not fills:
        return dict(base, reason="No order history.")
    if closes is None or getattr(closes, "empty", True) or benchmark not in closes.columns:
        return dict(base, reason="Price history is unavailable.")
    dates = [_iso(d) for d in closes.index]
    symbols = sorted({f.symbol for f in fills})
    cols = {s: _column(closes, s) for s in symbols}
    missing = [s for s, c in cols.items() if c is None]
    if missing:
        return dict(base, reason="No price history for: " + ", ".join(missing))
    by_day: Dict[str, List[Fill]] = {}
    for f in fills:
        by_day.setdefault(f.at[:10], []).append(f)
    first_fill = min(by_day)
    s = max(_window_start(dates, range_key), next((i for i, d in enumerate(dates) if d >= first_fill), 0))
    shares = {sym: 0.0 for sym in symbols}
    for d, fs in by_day.items():
        if d < dates[s]:
            for f in fs:
                shares[f.symbol] += f.quantity if f.side == "buy" else -f.quantity
    value_prev = None
    chain, out_dates, out_val = [1.0], [], []
    bench_col = _column(closes, benchmark) or []
    for i in range(s, len(dates)):
        d = dates[i]
        if bench_col[i] is None:
            continue
        flow = 0.0
        for f in by_day.get(d, []):
            sign = 1 if f.side == "buy" else -1
            shares[f.symbol] += sign * f.quantity
            flow += sign * f.quantity * f.price
        v = 0.0
        for sym in symbols:
            px = cols[sym][i]
            if px is None:
                # walk back at most MAX_GAP_DAYS for a stale close; beyond that the sleeve is unpriced
                back = [cols[sym][j] for j in range(max(0, i - MAX_GAP_DAYS), i) if cols[sym][j] is not None]
                px = back[-1] if back else None
            if px is None and shares[sym] > 1e-9:
                return dict(base, reason=f"{sym} has no price on {d}.")
            v += shares[sym] * (px or 0.0)
        if value_prev is not None and value_prev > 0:
            chain.append(chain[-1] * ((v - flow) / value_prev))
        out_dates.append(d)
        out_val.append(v)
        value_prev = v
    if len(out_dates) < 5 or out_val[0] <= 0:
        return dict(base, reason="Not enough invested history in this range.")
    bench = [bench_col[dates.index(d)] for d in out_dates]
    bench_idx = [b / bench[0] * 100 for b in bench]
    port_idx = [c * 100 for c in chain]
    if len(port_idx) != len(out_dates):
        return dict(base, reason="Ledger could not be aligned to trading days.")
    dd = max_drawdown(port_idx, out_dates)
    rets = [port_idx[i] / port_idx[i - 1] - 1 for i in range(1, len(port_idx))]
    return dict(base, available=True, dates=out_dates, portfolio=port_idx, benchmark_series=bench_idx,
                value=out_val, drawdown=drawdown_series(port_idx),
                stats={"start": out_dates[0], "end": out_dates[-1], "days": len(out_dates) - 1,
                       "portfolio_return": port_idx[-1] / 100 - 1, "benchmark_return": bench_idx[-1] / 100 - 1,
                       "excess": port_idx[-1] / 100 - bench_idx[-1] / 100,
                       "max_drawdown": dd, "volatility": annualised_vol(rets)})


# ---------------------------------------------------------------------------
# Outcome rates
# ---------------------------------------------------------------------------

def _rate_block(k: int, n: int, minimum: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"successes": k, "n": n, "minimum": minimum}
    if n < minimum:
        out.update(status="insufficient", rate=None, interval=None)
    else:
        out.update(status="ok", rate=k / n, interval=wilson_interval(k, n))
    return out


TRADE_DEFINITION = {
    "unit": "A completed (closed) trade reported by Robinhood's realized profit-and-loss history.",
    "success": "Realized gain greater than zero, after any fees Robinhood includes in that figure.",
    "horizon": "None: a trade counts when it closes, however long it was held.",
    "denominator": "Every closed trade in the window with a readable realized gain. Trades with an unreadable gain are counted and excluded, not assumed.",
    "open_positions": "Open positions are excluded; their unrealized results are shown separately.",
    "not": "This is a record of past trades. It is not a probability that a future trade will succeed.",
}


def trade_outcomes(trades: Iterable[Dict[str, Any]], window: str = "", minimum: int = MIN_OUTCOMES) -> Dict[str, Any]:
    """Hit rate, payoff and expectancy of CLOSED TRADES (never mixed with recommendations)."""
    readable, unreadable = [], 0
    for t in trades:
        g = _num(t.get("realized_gain"))
        if g is None:
            unreadable += 1
            continue
        readable.append(dict(t, realized_gain=g))
    wins = [t["realized_gain"] for t in readable if t["realized_gain"] > 0]
    losses = [t["realized_gain"] for t in readable if t["realized_gain"] <= 0]
    n = len(readable)
    block = _rate_block(len(wins), n, minimum)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    out = dict(block, kind="trades", definition=TRADE_DEFINITION, window=window, unreadable=unreadable,
               average_win=avg_win, average_loss=avg_loss,
               expectancy=sum(t["realized_gain"] for t in readable) / n if n else None,
               total_realized=sum(t["realized_gain"] for t in readable) if n else None,
               profit_factor=(sum(wins) / -sum(losses)) if wins and losses and sum(losses) < 0 else None,
               payoff_ratio=(avg_win / -avg_loss) if avg_win and avg_loss and avg_loss < 0 else None)
    if block["status"] == "insufficient":
        out["missing"] = [f"At least {minimum} completed trades with readable results "
                          f"(found {n}{f', {unreadable} unreadable' if unreadable else ''})."]
    return out


RECOMMENDATION_DEFINITION = {
    "unit": "A dated Accumulate, Trim or Exit Review call recorded by this page when it was shown.",
    "success": ("Accumulate: the stock's price return beat the benchmark's over the horizon. "
                "Trim / Exit Review: the stock's price return trailed the benchmark's."),
    "horizon": (f"{_T.PORTFOLIO.OUTCOME_HORIZON_DAYS} trading days (about {round(_T.PORTFOLIO.OUTCOME_HORIZON_DAYS / 21)} months) "
                "after the call date, on closing prices without dividends."),
    "denominator": "Every directional call whose horizon has fully elapsed. Hold and no-call entries are not directional and are excluded. Calls whose horizon is still running are counted as pending.",
    "open_positions": "Not applicable: outcomes are measured on the price, whether or not the account acted.",
    "not": "This measures whether past model calls beat the benchmark. It is not a forecast, and it is separate from the account's own trades.",
}
HORIZON_DAYS = _T.PORTFOLIO.OUTCOME_HORIZON_DAYS
DIRECTIONAL = {"Accumulate": 1, "Trim": -1, "Exit Review": -1}


def recommendation_outcomes(entries: Sequence[Dict[str, Any]], closes: Any, benchmark: str,
                            minimum: int = MIN_OUTCOMES, horizon: int = HORIZON_DAYS) -> Dict[str, Any]:
    """Evaluate journal entries against forward returns. Entries can only be evaluated once
    ``horizon`` trading days of prices exist after the call; nothing is backfilled."""
    dated = sorted(entries, key=lambda e: e.get("date", ""))
    directional = [e for e in dated if e.get("action") in DIRECTIONAL]
    evaluated, pending, unpriced = [], 0, 0
    dates = [_iso(d) for d in closes.index] if closes is not None and not getattr(closes, "empty", True) else []
    bcol = _column(closes, benchmark) if dates else None
    for e in directional:
        col = _column(closes, e["symbol"]) if dates else None
        if not dates or col is None or bcol is None:
            unpriced += 1
            continue
        try:
            i0 = next(i for i, d in enumerate(dates) if d >= e["date"] and col[i] is not None and bcol[i] is not None)
        except StopIteration:
            pending += 1
            continue
        i1 = i0 + horizon
        if i1 >= len(dates):
            pending += 1
            continue
        if col[i1] is None or bcol[i1] is None:
            unpriced += 1
            continue
        ret = col[i1] / col[i0] - 1
        bret = bcol[i1] / bcol[i0] - 1
        sign = DIRECTIONAL[e["action"]]
        excess = (ret - bret) * sign
        evaluated.append({"symbol": e["symbol"], "action": e["action"], "date": e["date"], "end": dates[i1],
                          "return": ret, "benchmark": bret, "directional_excess": excess, "success": excess > 0})
    n = len(evaluated)
    k = sum(1 for x in evaluated if x["success"])
    block = _rate_block(k, n, minimum)
    wins = [x["directional_excess"] for x in evaluated if x["success"]]
    losses = [x["directional_excess"] for x in evaluated if not x["success"]]
    out = dict(block, kind="recommendations", definition=RECOMMENDATION_DEFINITION, horizon_days=horizon,
               pending=pending, unpriced=unpriced, journal_entries=len(dated), directional=len(directional),
               first_entry=dated[0]["date"] if dated else None,
               average_win=sum(wins) / len(wins) if wins else None,
               average_loss=sum(losses) / len(losses) if losses else None,
               expectancy=sum(x["directional_excess"] for x in evaluated) / n if n else None,
               sample=evaluated[-8:])
    if block["status"] == "insufficient":
        miss = []
        if not dated:
            miss.append("The journal is empty: it starts recording calls from the first refresh and never backfills.")
        else:
            miss.append(f"At least {minimum} directional calls with a full {horizon}-trading-day outcome "
                        f"(have {n}; {pending} still pending; the journal began {dated[0]['date']}).")
        out["missing"] = miss
    return out


def normalize_trades(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Robinhood's closed-trade records -> ``{symbol, realized_gain, closed_at, opened_at}``.

    The payload is not among the recorded schemas, so gain and dates are read from short
    candidate lists. A record whose gain cannot be read keeps ``realized_gain: None`` and is
    counted as unreadable by :func:`trade_outcomes` -- it is never treated as zero.
    """
    def pick(rec: Dict[str, Any], keys: Sequence[str]) -> Any:
        for k in keys:
            cur: Any = rec
            for part in k.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur not in (None, ""):
                return cur
        return None

    out = []
    for rec in records:
        at = str(pick(rec, ("timestamp", "closed_at", "close_date", "sell_date", "date", "end_time")) or "") or None
        side = str(pick(rec, ("side", "direction")) or "").lower() or None
        out.append({
            "symbol": str(pick(rec, ("symbol", "instrument.symbol")) or "").upper() or None,
            # Closing history is not an equity-only source. A symbol alone cannot
            # distinguish a cryptocurrency from a same-symbol listed security.
            "instrument_type": str(pick(rec, ("instrument_type", "instrument.type")) or "unknown").strip().lower(),
            "side": side,
            "quantity": _num(pick(rec, ("quantity", "shares"))),
            "price": _num(pick(rec, ("price", "close_price", "average_price"))),
            "realized_gain": _num(pick(rec, ("realized_gain", "realized_pnl", "realized_profit_loss",
                                             "total_return", "profit_loss", "gain"))),
            "closed_at": at[:10] if at else None,
            # The full closing time (RFC 3339), so the page can show market time.
            "at": at,
            "opened_at": str(pick(rec, ("opened_at", "open_date", "buy_date", "start_time")) or "")[:10] or None,
        })
    return out


def recent_paths(closes: Any, symbols: Sequence[str], days: int = 22) -> Dict[str, Dict[str, Any]]:
    """Each symbol's last ``days`` closes (about a month), for a trend line.

    Only symbols with a close on every one of those days are included; a gap is not
    interpolated. ``change`` is the price change over the window, dividends excluded.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if closes is None or getattr(closes, "empty", True):
        return out
    dates = [_iso(d) for d in closes.index]
    for sym in symbols:
        col = _column(closes, sym)
        if col is None:
            continue
        tail = col[-days:]
        if len(tail) < days or any(v is None for v in tail) or not tail[0]:
            continue
        out[sym] = {"start": dates[-days], "end": dates[-1], "values": [round(v, 4) for v in tail],
                    "change": tail[-1] / tail[0] - 1}
    return out
