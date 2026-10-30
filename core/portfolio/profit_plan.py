"""Profit plan: when to book gains, how much of them is protected, and which losses offset them.

For every holding this module answers four questions a portfolio manager asks of a
book with large winners in it:

* **How much of the gain is protected?** A trailing *profit stop* sits a few average
  daily moves under the highest recent close (a close-only Chandelier exit). The gain
  between the cost and the stop is *locked* by the rule; the gain above the stop is
  *exposed*: a fall to the stop gives it back.
* **Is it time to book some?** Three booking rules, each with its size: the position
  is past the concentration review level (sell to the trim target); it has doubled
  (sell the original cost back out, so what stays rides on gains only); the price is at
  or past QUANTA's 12-month valuation target (book a third). Below the profit stop with
  a gain, the rule books half and resets the stop on the rest.
* **Which losses pay for it?** Holdings with an unrealised loss are tax-loss candidates,
  split short/long term from the broker's lots, with the wash-sale window flagged when a
  lot was bought within it.
* **How does the book get back inside its sector limit?** Two paths to the limit: the
  tax-cheapest (losses first, then the smallest gains per dollar sold) and pro rata.

Every rule is a stated, standard rule of thumb with its parameter in
``engine.thresholds.PORTFOLIO``. None has been validated by QUANTA as an edge, and none
is an order: the page says so. Prices are closes, split-adjusted; the move is the
average absolute close-to-close change (no intraday highs and lows are used).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from engine import thresholds as _T

_PF = _T.PORTFOLIO
SIGNAL_ORDER = ("STOP_HIT", "BOOK", "PROTECT", "HARVEST", "RUN", "HOLD", "NO_DATA")
SIGNALS = {
    "STOP_HIT": ("Below profit stop", "bear"),
    "BOOK": ("Book profit", "warn"),
    "PROTECT": ("Near profit stop", "warn"),
    "HARVEST": ("Tax-loss candidate", "info"),
    "RUN": ("Let it run", "bull"),
    "HOLD": ("Hold", "neutral"),
    "NO_DATA": ("Not enough history", "muted"),
}
UNLIMITED_SECTORS = ("Cash", "Unclassified", "")


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _money(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def _iso(d: Any) -> str:
    return d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10]


def rules() -> Dict[str, Any]:
    """The parameters the page quotes beside the plan."""
    return {
        "trail_sessions": _PF.PROFIT_TRAIL_SESSIONS, "trail_moves": _PF.PROFIT_TRAIL_MOVES,
        "protect_moves": _PF.PROFIT_PROTECT_MOVES, "stop_scale_out": _PF.PROFIT_STOP_SCALE_OUT,
        "recover_cost_gain": _PF.PROFIT_RECOVER_COST_GAIN, "valuation_scale_out": _PF.PROFIT_VALUATION_SCALE_OUT,
        "min_sale_value": _PF.PROFIT_MIN_SALE_VALUE, "harvest_min_loss": _PF.HARVEST_MIN_LOSS,
        "harvest_min_loss_pct": _PF.HARVEST_MIN_LOSS_PCT, "wash_sale_days": _PF.WASH_SALE_DAYS,
    }


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def _series(closes: Any, symbol: str) -> Optional[Dict[str, List[Any]]]:
    """The symbol's closes with their dates, gaps dropped (never bridged by a guess)."""
    if closes is None or getattr(closes, "empty", True) or symbol not in getattr(closes, "columns", ()):
        return None
    dates, values = [], []
    for d, v in zip(closes.index, closes[symbol].tolist()):
        x = _num(v)
        if x is not None and x > 0:
            dates.append(_iso(d))
            values.append(x)
    return {"dates": dates, "values": values} if values else None


def trailing_stop(values: Sequence[float], sessions: int, moves: float) -> Optional[Dict[str, float]]:
    """Highest close over ``sessions`` minus ``moves`` average absolute daily moves.

    None until there are ``sessions + 1`` closes (one move needs two closes)."""
    if len(values) < sessions + 1:
        return None
    window = values[-(sessions + 1):]
    move = sum(abs(b - a) for a, b in zip(window, window[1:])) / sessions
    high = max(window[1:])
    return {"stop": high - moves * move, "high": high, "move": move}


def stop_path(values: Sequence[float], sessions: int, moves: float, last: int) -> List[Optional[float]]:
    """The profit stop as it stood on each of the last ``last`` sessions (for the chart)."""
    out: List[Optional[float]] = []
    n = len(values)
    for end in range(max(0, n - last), n):
        t = trailing_stop(values[:end + 1], sessions, moves)
        out.append(round(t["stop"], 4) if t else None)
    return out


def _sma(values: Sequence[float], n: int) -> Optional[float]:
    return sum(values[-n:]) / n if len(values) >= n else None


def trend(values: Sequence[float], price: float) -> Dict[str, Any]:
    """Price against its 50- and 200-session averages: up, down or mixed."""
    s50, s200 = _sma(values, 50), _sma(values, 200)
    if s50 is None or s200 is None:
        return {"sma50": s50, "sma200": s200, "state": None}
    state = ("up" if price > s200 and s50 > s200 else
             "down" if price < s200 and s50 < s200 else "mixed")
    return {"sma50": s50, "sma200": s200, "state": state}


# ---------------------------------------------------------------------------
# Tax lots
# ---------------------------------------------------------------------------

def _term(lot: Dict[str, Any], today: date) -> Optional[str]:
    term = lot.get("term")
    if term in ("short", "long"):
        return term
    try:
        return "long" if (today - date.fromisoformat(str(lot.get("acquired_on"))[:10])).days > 365 else "short"
    except (TypeError, ValueError):
        return None


def sale_gain(shares: float, price: float, avg_cost: Optional[float], lots: Optional[Sequence[Dict[str, Any]]],
              today: date) -> Dict[str, Any]:
    """Gain realised by selling ``shares`` at ``price``: from the lots, oldest first (FIFO,
    Robinhood's default), split short/long term; else from the average cost, unsplit."""
    out: Dict[str, Any] = {"gain": None, "gain_long": None, "gain_short": None, "basis": "unknown"}
    usable = [x for x in (lots or []) if _num(x.get("cost_per_share")) is not None and _num(x.get("quantity"))]
    if usable and all(_term(x, today) for x in usable) and sum(float(x["quantity"]) for x in usable) >= shares - 1e-6:
        left, short, long_ = shares, 0.0, 0.0
        for lot in sorted(usable, key=lambda x: str(x.get("acquired_on") or "")):
            take = min(left, float(lot["quantity"]))
            g = take * (price - float(lot["cost_per_share"]))
            if _term(lot, today) == "long":
                long_ += g
            else:
                short += g
            left -= take
            if left <= 1e-9:
                break
        return {"gain": short + long_, "gain_long": long_, "gain_short": short, "basis": "lots, oldest first"}
    if avg_cost is not None:
        out.update(gain=shares * (price - avg_cost), basis="average cost")
    return out


def harvest(price: float, quantity: float, avg_cost: Optional[float], lots: Optional[Sequence[Dict[str, Any]]],
            today: date) -> Optional[Dict[str, Any]]:
    """The unrealised loss a sale would realise, by term, and whether a recent buy risks a wash sale."""
    if avg_cost is None or price >= avg_cost:
        return None
    loss = quantity * (price - avg_cost)
    pct = price / avg_cost - 1
    if -loss < _PF.HARVEST_MIN_LOSS or -pct < _PF.HARVEST_MIN_LOSS_PCT:
        return None
    short = long_ = 0.0
    recent: List[str] = []
    split = True
    for lot in lots or []:
        q, c = _num(lot.get("quantity")), _num(lot.get("cost_per_share"))
        term = _term(lot, today)
        if q is None or c is None or term is None:
            split = False
            continue
        g = q * (price - c)
        if term == "long":
            long_ += g
        else:
            short += g
        try:
            if (today - date.fromisoformat(str(lot.get("acquired_on"))[:10])).days <= _PF.WASH_SALE_DAYS:
                recent.append(str(lot.get("acquired_on"))[:10])
        except (TypeError, ValueError):
            pass
    out: Dict[str, Any] = {"loss": loss, "loss_pct": pct, "wash_risk": bool(recent), "recent_buys": recent,
                           "no_rebuy_before": (today + timedelta(days=_PF.WASH_SALE_DAYS + 1)).isoformat()}
    if lots and split:
        # A lot bought above today's price is a loss; one bought below it is a gain. Selling
        # the whole position realises both, so the split is reported net, per term.
        out.update(loss_short=short, loss_long=long_)
    return out


# ---------------------------------------------------------------------------
# One holding
# ---------------------------------------------------------------------------

def _trigger(code: str, text: str, shares: float, price: float, quantity: float) -> Optional[Dict[str, Any]]:
    shares = max(0.0, min(shares, quantity))
    value = shares * price
    if value < _PF.PROFIT_MIN_SALE_VALUE:
        return None
    return {"code": code, "text": text, "shares": shares, "value": value}


def plan_holding(row: Dict[str, Any], closes: Any, total: float, policy: Any, today: date) -> Dict[str, Any]:
    b = row.get("broker") or {}
    sym = row["symbol"]
    price = _num(b.get("last_price"))
    qty = _num(b.get("quantity")) or 0.0
    avg = _num(b.get("average_cost"))
    value = _num(b.get("market_value")) or ((price or 0) * qty)
    weight = _num(b.get("weight"))
    is_fund = bool(row.get("is_fund"))
    lots = b.get("tax_lots")
    dec = ((row.get("analysis") or {}).get("decision") or {})
    out: Dict[str, Any] = {
        "symbol": sym, "name": b.get("name") or (row.get("analysis") or {}).get("company_name") or "",
        "is_fund": is_fund, "sector": (row.get("market") or {}).get("sector") or "",
        "price": price, "quantity": qty, "average_cost": avg, "value": value, "weight": weight,
        "earnings_date": (row.get("market") or {}).get("earnings_date"),
        "action": (row.get("guidance") or {}).get("action"),
    }
    if price is None or price <= 0 or not qty:
        out["signal"] = {"code": "NO_DATA", "label": "No price", "tone": "muted"}
        return out
    gain = qty * (price - avg) if avg is not None else None
    out.update(gain=gain, gain_pct=(price / avg - 1) if avg else None, cost_basis=avg * qty if avg is not None else None)

    hist = _series(closes, sym)
    values = (hist or {}).get("values") or []
    ts = trailing_stop(values, _PF.PROFIT_TRAIL_SESSIONS, _PF.PROFIT_TRAIL_MOVES) if values else None
    if hist:
        n = _PF.PROFIT_CHART_SESSIONS
        out["history"] = {"dates": hist["dates"][-n:], "closes": [round(v, 4) for v in values[-n:]],
                          "stops": stop_path(values, _PF.PROFIT_TRAIL_SESSIONS, _PF.PROFIT_TRAIL_MOVES, n)}
        out["trend"] = trend(values, price)
    if ts:
        stop = ts["stop"]
        out.update(stop=stop, high=ts["high"], move=ts["move"], move_pct=ts["move"] / price,
                   stop_distance_pct=price / stop - 1 if stop > 0 else None)
        if gain is not None and gain > 0 and avg is not None:
            # A stop the price is already under protects nothing: the whole gain is exposed.
            locked = min(gain, max(0.0, (stop - avg) * qty)) if price >= stop else 0.0
            out.update(locked_gain=locked, exposed_gain=gain - locked)
    target = None if is_fund else _num((dec.get("levels") or {}).get("valuation_target"))
    out["valuation_target"] = target

    # Booking rules, each with its own size; the suggestion covers the largest.
    triggers: List[Dict[str, Any]] = []
    review = policy.trim_review_position
    if weight is not None and total and weight > review:
        to = policy.trim_target_position
        t = _trigger("CONCENTRATION", f"{weight:.1%} of the account, past the {review:.0%} review level: "
                                      f"sell down to {to:.0%}", (value - to * total) / price, price, qty)
        if t:
            triggers.append(t)
    gp = out.get("gain_pct")
    # Funds are sized by allocation: only the concentration rule applies to them.
    if not is_fund and gp is not None and gp >= _PF.PROFIT_RECOVER_COST_GAIN and out.get("cost_basis"):
        t = _trigger("RECOVER_COST", f"Up {gp:.0%}: selling {_money(out['cost_basis'])} takes your original cost "
                                     "back out; what stays rides on gains only", out["cost_basis"] / price, price, qty)
        if t:
            triggers.append(t)
    if target is not None and price >= target and gain is not None and gain > 0:
        t = _trigger("VALUATION", f"At or past QUANTA's 12-month valuation target (${target:,.2f}): book a third",
                     qty * _PF.PROFIT_VALUATION_SCALE_OUT, price, qty)
        if t:
            triggers.append(t)
    out["triggers"] = triggers
    if triggers:
        top = max(triggers, key=lambda x: x["shares"])
        sale = dict(top, **sale_gain(top["shares"], price, avg, lots, today))
        sale["weight_after"] = (value - top["value"]) / total if total else None
        out["sale"] = sale
    # Below the stop is conditional on the close, so its sale is kept apart from "book now".
    stop_hit = bool(not is_fund and ts and gain is not None and gain > 0 and price < ts["stop"])
    if stop_hit:
        t = _trigger("STOP_HIT", f"Trading below its profit stop (${ts['stop']:,.2f}): if it closes there, the rule "
                                 f"books {_PF.PROFIT_STOP_SCALE_OUT:.0%} and resets the stop on the rest",
                     qty * _PF.PROFIT_STOP_SCALE_OUT, price, qty)
        if t:
            out["stop_sale"] = dict(t, **sale_gain(t["shares"], price, avg, lots, today))

    out["harvest"] = harvest(price, qty, avg, lots, today)

    near = bool(not is_fund and ts and gain is not None and gain > 0 and not stop_hit
                and price - ts["stop"] <= _PF.PROFIT_PROTECT_MOVES * ts["move"])
    if stop_hit:
        code = "STOP_HIT"
    elif triggers:
        code = "BOOK"
    elif near:
        code = "PROTECT"
    elif out["harvest"]:
        code = "HARVEST"
    elif not ts:
        code = "NO_DATA"
    elif is_fund:
        code = "HOLD"
    elif gain is not None and gain > 0 and (out.get("trend") or {}).get("state") == "up":
        code = "RUN"
    else:
        code = "HOLD"
    label, tone = SIGNALS[code]
    out["signal"] = {"code": code, "label": label, "tone": tone, "text": _signal_text(code, out)}
    return out


def _signal_text(code: str, h: Dict[str, Any]) -> str:
    stop = h.get("stop")
    if code == "STOP_HIT":
        return (h.get("stop_sale") or {}).get("text") or f"Trading below its profit stop (${stop:,.2f})."
    if code == "BOOK":
        return max(h["triggers"], key=lambda x: x["shares"])["text"]
    if code == "PROTECT":
        return (f"Within {_PF.PROFIT_PROTECT_MOVES:g} average daily move of its profit stop (${stop:,.2f}); "
                f"{_money(h.get('exposed_gain') or 0)} of gain sits above it.")
    if code == "HARVEST":
        hv = h["harvest"]
        return (f"Down {abs(hv['loss_pct']):.0%} ({_money(hv['loss'])}): selling realises a loss that offsets gains"
                + (". A lot was bought within 30 days, so a sale now risks a wash sale." if hv["wash_risk"] else "."))
    if code == "RUN":
        return (f"Uptrend (above its 50- and 200-session averages) and inside every limit; the profit stop at "
                f"${stop:,.2f} locks {_money(h.get('locked_gain') or 0)} of the gain.")
    if code == "NO_DATA":
        return f"Fewer than {_PF.PROFIT_TRAIL_SESSIONS + 1} closes, so no profit stop is drawn."
    if h.get("is_fund"):
        return "A fund is sized by allocation: no profit stop or valuation rule applies, only the concentration limit."
    return "No booking rule fires and the gain is not near its stop." if (h.get("gain") or 0) > 0 \
        else "No rule fires: no gain to protect and the loss is under the tax-loss thresholds."


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------

def _sector_paths(holdings: List[Dict[str, Any]], total: float, policy: Any, today: date) -> List[Dict[str, Any]]:
    """For each sector still over its limit after the suggested sales: two ways to the limit."""
    out = []
    after: Dict[str, float] = {}
    for h in holdings:
        if h.get("is_fund") or h.get("sector") in UNLIMITED_SECTORS or h.get("price") is None:
            continue
        sold = (h.get("sale") or {}).get("value") or 0.0
        after[h["sector"]] = after.get(h["sector"], 0.0) + (h.get("value") or 0.0) - sold
    for sector, val in sorted(after.items(), key=lambda x: -x[1]):
        w = val / total if total else 0.0
        if w < policy.max_sector:
            continue
        excess = val - policy.max_sector * total
        names = [h for h in holdings if h.get("sector") == sector and not h.get("is_fund") and h.get("price")]
        left = {h["symbol"]: (h.get("value") or 0.0) - ((h.get("sale") or {}).get("value") or 0.0) for h in names}

        def leg(h: Dict[str, Any], amount: float) -> Dict[str, Any]:
            shares = amount / h["price"]
            return dict({"symbol": h["symbol"], "value": amount, "shares": shares,
                         "wash_risk": bool((h.get("harvest") or {}).get("wash_risk"))},
                        **sale_gain(shares, h["price"], h.get("average_cost"), (h.get("_lots") or None), today))

        # Tax-cheapest: losses first, then the smallest gain per dollar sold.
        def cost_ratio(h: Dict[str, Any]) -> float:
            avg = h.get("average_cost")
            return (h["price"] - avg) / h["price"] if avg is not None else 1.0
        cheap, need = [], excess
        for h in sorted(names, key=cost_ratio):
            if need <= _PF.PROFIT_MIN_SALE_VALUE:
                break
            amt = min(need, left[h["symbol"]])
            if amt >= _PF.PROFIT_MIN_SALE_VALUE:
                cheap.append(leg(h, amt))
                need -= amt
        pro = [leg(h, excess * left[h["symbol"]] / val) for h in names
               if val and excess * left[h["symbol"]] / val >= _PF.PROFIT_MIN_SALE_VALUE]

        def total_gain(legs: List[Dict[str, Any]]) -> Optional[float]:
            gs = [x.get("gain") for x in legs]
            return sum(gs) if gs and all(g is not None for g in gs) else None
        out.append({"sector": sector, "weight_after_sales": w, "limit": policy.max_sector, "excess_value": excess,
                    "cheapest": {"legs": cheap, "gain": total_gain(cheap)},
                    "pro_rata": {"legs": pro, "gain": total_gain(pro)}})
    return out


def build_profit_plan(rows: Sequence[Dict[str, Any]], closes: Any, policy: Any, total: Optional[float],
                      cash: Optional[float], today: date) -> Dict[str, Any]:
    """The whole plan. ``rows`` are the view's positions; ``closes`` a date x symbol frame."""
    if not rows:
        return {"available": False, "reason": "No holdings to plan for."}
    total = total or sum((_num((r.get("broker") or {}).get("market_value")) or 0.0) for r in rows) + (cash or 0.0)
    holdings = []
    for r in rows:
        h = plan_holding(r, closes, total, policy, today)
        h["_lots"] = (r.get("broker") or {}).get("tax_lots")
        holdings.append(h)
    paths = _sector_paths(holdings, total, policy, today)
    for h in holdings:
        h.pop("_lots", None)

    gains = [h for h in holdings if (h.get("gain") or 0) > 0]
    locked = sum(h.get("locked_gain") or 0.0 for h in gains)
    exposed = sum(h.get("exposed_gain") or 0.0 for h in gains if h.get("exposed_gain") is not None)
    unprotected = sum(h["gain"] for h in gains if h.get("exposed_gain") is None)  # no stop drawn
    sales = [h for h in holdings if h.get("sale")]
    sold = sum(h["sale"]["value"] for h in sales)
    stops = [h for h in holdings if h.get("stop_sale")]

    def part(key: str) -> Optional[float]:
        vals = [h["sale"].get(key) for h in sales]
        return sum(vals) if vals and all(v is not None for v in vals) else None
    harvests = [h for h in holdings if h.get("harvest")]
    loss = sum(h["harvest"]["loss"] for h in harvests)
    gain_booked = part("gain")
    order = {c: i for i, c in enumerate(SIGNAL_ORDER)}
    holdings.sort(key=lambda h: (order.get(h["signal"]["code"], 99), -(h.get("gain") or 0)))
    return {
        "available": True,
        "as_of": today.isoformat(),
        "rules": rules(),
        "total_value": total,
        "totals": {
            "gain": sum(h["gain"] for h in gains),
            "losses": sum(h["gain"] for h in holdings if (h.get("gain") or 0) < 0),
            "locked": locked, "exposed": exposed, "unprotected": unprotected,
            "winners": len(gains),
        },
        "book": {
            "count": len(sales), "value": sold, "gain": gain_booked,
            "gain_long": part("gain_long"), "gain_short": part("gain_short"),
            "cash_after": (cash or 0.0) + sold if cash is not None else None,
            "cash_after_pct": ((cash or 0.0) + sold) / total if cash is not None and total else None,
        },
        "stops": {
            "count": len(stops), "value": sum(h["stop_sale"]["value"] for h in stops),
            "symbols": [h["symbol"] for h in stops],
        },
        "harvest": {
            "count": len(harvests), "loss": loss,
            "wash_risk": [h["symbol"] for h in harvests if h["harvest"]["wash_risk"]],
            "net_gain": (gain_booked + loss) if gain_booked is not None else None,
        },
        "sector_paths": paths,
        "signals": {c: sum(1 for h in holdings if h["signal"]["code"] == c) for c in SIGNAL_ORDER},
        "holdings": holdings,
    }
