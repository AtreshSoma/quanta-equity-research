"""One published level set per Decision (audit F2).

A pick used to carry three level sets at once: the scanner's, a JavaScript entry
zone, and the Research engine's, so the card could show R:R 2.25 next to a
binding reason quoting 1.23. Owner decision Q1: the Research engine's levels
govern and are the ones shown. This module builds the one set a page prints:

* entry, stop, target, R:R (2 dp), horizon and target basis;
* the entry zone, computed here (``LEVELS.ENTRY_ZONE_ATR_MULT`` x ATR below
  the entry), so no page invents one;
* the price triggers that would lift R:R to a hurdle, solved from the same
  net-of-cost formula the engines use.

R:R is net of a round-trip cost ``c = cost_frac x entry`` charged to both legs:
``rr = (target - entry - c) / (entry - stop + c)``. It is quoted at the TOP of
the entry zone (the worst fill).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .thresholds import LEVELS, UI, floor_rr

RR_BASIS = "net of costs at the top of the entry zone"


def _r2(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 2)


def net_rr(entry: float, stop: float, target: float, cost_frac: float) -> float:
    c = entry * cost_frac
    risk = entry - stop + c
    return max(0.0, target - entry - c) / risk if risk > 0 else 0.0


def rr_triggers(entry: float, stop: float, target: float, cost_frac: float, min_rr: float) -> Dict[str, float]:
    """Prices at which net R:R reaches ``min_rr``, holding the other legs fixed.

    ``target_needed``: the target that lifts R:R to the hurdle at this entry.
    ``entry_needed``: the highest entry (spot) that clears the hurdle with the
    stop and target unchanged. Both reproduce :func:`net_rr` exactly.
    """
    k = float(cost_frac)
    c = entry * k
    target_needed = entry + c + min_rr * (entry - stop + c)
    entry_needed = (target + min_rr * stop) / ((1.0 + k) * (1.0 + min_rr))
    return {"min_rr": float(min_rr), "target_needed": target_needed, "entry_needed": entry_needed}


def entry_to_spot(entry_price: float, entry: float, spot: float) -> float:
    """Convert an ENTRY price to the SPOT that produces it.

    The engine sets entry = spot x 1.002 for a new buy (a limit at a slight
    premium), so a price solved as an entry is ``entry x spot / entry_price``
    in spot terms. When the entry is the spot itself (RSI extended, or a
    reference only) the ratio is 1 and nothing changes.
    """
    return float(entry_price) * float(spot) / float(entry)


def breakout_entry(price: float, week_52_high: Optional[float], resistance_level: Optional[float],
                   atr: Optional[float]) -> float:
    """The level a breakout entry is priced at, or 0.0 when none applies.

    The nearest overhead level is the lower of the 52-week high and the nearest
    swing resistance, counting only levels above the price. It is offered only
    when it sits within ``LEVELS.BREAKOUT_MAX_ATR`` ATRs of the price. A 52-week
    high 45% overhead is not a breakout entry for a call made today, and an
    ATR that is unknown means the distance cannot be judged, so nothing is shown.
    """
    price = float(price or 0.0)
    atr = float(atr or 0.0)
    overhead = [float(x) for x in (week_52_high, resistance_level) if x and float(x) > price]
    if price <= 0 or atr <= 0 or not overhead:
        return 0.0
    level = min(overhead)
    return level if (level - price) <= LEVELS.BREAKOUT_MAX_ATR * atr else 0.0


def research_rr_clear_entry(valuation_target: float, horizon_frac: float, stop_frac: float,
                            cost_frac: float, min_rr: float) -> Optional[float]:
    """The highest ENTRY price at which a Research Decision's net R:R reaches ``min_rr``.

    This solves for the entry, not the spot: the engine's entry can sit above
    spot (a limit at a premium), so a caller that shows this to a reader as a
    spot price must convert it with :func:`entry_to_spot` first.

    Unlike :func:`rr_triggers`, the Research levels move with the entry: the
    stop sits ``stop_frac`` of the entry below it and the horizon target is the
    entry plus ``horizon_frac`` of the gap to the valuation target. The stop is
    a FRACTION, not a dollar distance: it is an ATR multiple clamped to
    LEVELS.MIN/MAX_STOP_DISTANCE_PCT, and ATR scales with price. Holding the
    dollar distance fixed put MU's answer 60% below spot, with a 43% stop the
    engine can never set (2026-11-04). Solving ``net_rr(p, p (1 - s), p + f (V - p), k) = m`` for ``p`` gives
    ``p = f V / (f + k (1 + m) + m s)``.
    """
    f, k, m, s, v = float(horizon_frac), float(cost_frac), float(min_rr), float(stop_frac), float(valuation_target)
    denom = f + k * (1.0 + m) + m * s
    if f <= 0 or not (0 < s < 1) or v <= 0 or denom <= 0:
        return None
    return f * v / denom


def publish_levels(source: str, entry: float, stop: float, target: float, rr: float,
                   atr: Optional[float], cost_frac: float, horizon_days: Optional[int],
                   target_basis: str, label: Optional[str] = None,
                   min_rr: Optional[float] = None, currency: str = "$") -> Dict[str, Any]:
    """The one level set a page shows for a pick. ``source``: research | scanner."""
    atr = float(atr or 0.0)
    zone_low = entry - LEVELS.ENTRY_ZONE_ATR_MULT * atr if atr > 0 else entry
    out: Dict[str, Any] = {
        "source": source,
        "label": label or ("Research levels (binding)" if source == "research" else "Scanner levels"),
        "entry": _r2(entry),
        "entry_zone_low": _r2(min(zone_low, entry)),
        "entry_zone_high": _r2(entry),
        "stop": _r2(stop),
        "target": _r2(target),
        # Floored, not rounded: a true 1.4988 must not be stored as 1.50 and so
        # pass a 1.50 hurdle it does not meet (see thresholds.floor_rr).
        "risk_reward": floor_rr(float(rr or 0.0)),
        "rr_basis": RR_BASIS,
        "cost_frac": float(cost_frac),
        "horizon_days": int(horizon_days) if horizon_days else None,
        "target_basis": target_basis,
    }
    if min_rr is not None and entry > 0 and stop > 0 and target > 0 and out["risk_reward"] < min_rr:
        out["triggers"] = {k: (round(v, 2) if k != "min_rr" else v)
                           for k, v in rr_triggers(entry, stop, target, cost_frac, min_rr).items()}
        # The remedy names the gate that fired (Research 1.5 or scanner 1.8)
        # and the exact prices that would clear it, from THESE levels.
        out["remedy"] = rr_remedy(out, min_rr, gate="Research" if source == "research" else "Scanner",
                                  currency=currency)
    return out


def rr_remedy(levels: Dict[str, Any], min_rr: float, gate: str, currency: str = "$") -> str:
    """What would clear the R:R gate that fired, from the published levels."""
    rr = float(levels.get("risk_reward") or 0.0)
    trig = levels.get("triggers") or rr_triggers(levels["entry"], levels["stop"], levels["target"],
                                                 levels.get("cost_frac") or 0.0, min_rr)
    horizon = levels.get("horizon_days")
    tgt = f"a {horizon}-session target" if horizon else "a target"
    return (f"{gate} R:R {rr:.{UI.RR_DECIMALS}f} needs {min_rr:.{UI.RR_DECIMALS}f}: reached at a "
            f"spot at or below {currency}{trig['entry_needed']:,.2f} with the stop unchanged, or at "
            f"{tgt} at or above {currency}{trig['target_needed']:,.2f}.")
