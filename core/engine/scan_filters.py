"""The optional filters a user sets before a scan (sector, market cap, price,
daily traded value).

They only narrow a scan: a name must still pass every built-in rule (the
liquidity, price and market-cap floors in ``thresholds.UNIVERSE``, the event
gate, the book rules). Values are in the scan's own currency (USD for the US,
INR for India). A name outside a filter is "screened out" with the filter as
its reason, so the page's outcome table still reconciles to the universe.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from engine.thresholds import UNIVERSE

# Reason codes, recorded through the scanner's filter stats.
REASONS = ("user_sector", "user_market_cap", "user_price", "user_liquidity")

_RANGES = (("min_market_cap", "max_market_cap"), ("min_price", "max_price"))
_NUMBERS = ("min_market_cap", "max_market_cap", "min_price", "max_price", "min_daily_value")


def _number(name: str, value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    v = float(value)
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"{name} must be zero or more")
    return v or None


def normalize(raw: Any) -> Dict[str, Any]:
    """The filters as the engine reads them, or ValueError with a plain message.

    ``{"sectors": [...], "min_market_cap": .., "max_market_cap": .., "min_price": ..,
    "max_price": .., "min_daily_value": ..}``; an unset filter is left out.
    """
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("filters must be an object")
    unknown = set(raw) - set(_NUMBERS) - {"sectors"}
    if unknown:
        raise ValueError(f"unknown filter: {sorted(unknown)[0]}")
    out: Dict[str, Any] = {}
    sectors = raw.get("sectors") or []
    if not isinstance(sectors, list) or not all(isinstance(s, str) for s in sectors):
        raise ValueError("sectors must be a list of sector names")
    allowed = set(UNIVERSE.SECTORS)
    bad = [s for s in sectors if s not in allowed]
    if bad:
        raise ValueError(f"unknown sector: {bad[0]}")
    if sectors and len(set(sectors)) < len(allowed):
        out["sectors"] = sorted(set(sectors))
    for name in _NUMBERS:
        v = _number(name, raw.get(name))
        if v is not None:
            out[name] = v
    for lo, hi in _RANGES:
        if lo in out and hi in out and out[lo] > out[hi]:
            raise ValueError(f"{lo} is above {hi}")
    return out


def miss(filters: Dict[str, Any], *, sector: Optional[str], market_cap: Optional[float],
         price: float, daily_value: float, is_fund: bool) -> Optional[str]:
    """The first filter a name falls outside, as a reason code, or None.

    A fund has no sector or market cap: those two filters do not apply to it.
    """
    if not filters:
        return None
    if filters.get("sectors") and not is_fund and str(sector or "") not in filters["sectors"]:
        return "user_sector"
    if not is_fund:
        cap = float(market_cap or 0.0)
        if ("min_market_cap" in filters and cap < filters["min_market_cap"]) or \
           ("max_market_cap" in filters and cap > filters["max_market_cap"]):
            return "user_market_cap"
    if ("min_price" in filters and price < filters["min_price"]) or \
       ("max_price" in filters and price > filters["max_price"]):
        return "user_price"
    if "min_daily_value" in filters and daily_value < filters["min_daily_value"]:
        return "user_liquidity"
    return None
