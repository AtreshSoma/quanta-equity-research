"""Single source of truth for dividend yield calculation.

The upstream providers (yfinance especially) are inconsistent — sometimes
they return a fraction (0.0088 = 0.88%), sometimes a percent number
(0.88 meaning 0.88%, or 2.5 meaning 2.5%), sometimes a quarterly figure.

`normalize_dividend_yield` returns a *fraction* (e.g. 0.0088 for 0.88%).
Multiply by 100 for display.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple


# Above this value (as a fraction), we treat the yield as suspect for a
# large-cap equity. Above SUPPRESS_THRESHOLD we refuse to display it.
SUSPICIOUS_THRESHOLD = 0.15   # 15%
SUPPRESS_THRESHOLD = 0.50     # 50%


def _safe_float(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_dividend_yield(
    raw_yield=None,
    dividend_rate=None,
    current_price=None,
    is_quarterly_rate: bool = False,
) -> float:
    """Return annual dividend yield as a *fraction* (0.0088 = 0.88%).

    Resolution priority:
      1. If BOTH ``dividend_rate`` and ``current_price`` are positive, compute
         ``rate * (4 if quarterly else 1) / price``. This is by far the most
         reliable source because the upstream providers (yfinance especially)
         disagree on whether ``dividendYield`` is a fraction or a percent.
      2. Otherwise, normalise ``raw_yield`` heuristically:
            *  raw <= 1.0  -> already a fraction
            *  1 < raw <= 100 -> percent number, divide by 100
            *  raw > 100 -> drop as absurd (return 0)
      3. Reject anything that ends up above ``SUPPRESS_THRESHOLD`` (50%) or
         non-finite.
    """
    # 1. Prefer rate/price when both available
    rate = _safe_float(dividend_rate)
    price = _safe_float(current_price)
    if rate is not None and price is not None and rate > 0 and price > 0:
        annual = rate * (4.0 if is_quarterly_rate else 1.0)
        frac = annual / price
        if 0 < frac <= SUPPRESS_THRESHOLD:
            return frac

    # 2. Heuristic normalisation of raw value
    y = _safe_float(raw_yield)
    if y is None or y <= 0:
        return 0.0
    if y > 100.0:
        return 0.0
    frac = y if y <= 1.0 else y / 100.0
    if 0 < frac <= SUPPRESS_THRESHOLD:
        return frac
    return 0.0


def dividend_yield_from_info(info) -> float:
    """Annual dividend yield (FRACTION) from a Yahoo ``info`` payload.

    Units are resolved from the field, never from the value's magnitude:

    * ``dividendRate`` / price when both exist (most reliable);
    * ``yield`` -- ETFs, already a fraction;
    * ``dividendYield`` -- a PERCENT in current Yahoo payloads (AAPL 0.34
      means 0.34%, SPY 1.01 means 1.01%); the magnitude heuristic read every
      sub-1% value as a fraction, i.e. 100x too high;
    * ``trailingAnnualDividendYield`` -- a fraction.
    """
    info = info or {}
    price = _safe_float(info.get("currentPrice")) or _safe_float(info.get("regularMarketPrice"))
    rate = _safe_float(info.get("dividendRate"))
    if rate and rate > 0 and price and price > 0:
        frac = rate / price
        if 0 < frac <= SUPPRESS_THRESHOLD:
            return frac
    for key, scale in (("yield", 1.0), ("dividendYield", 0.01), ("trailingAnnualDividendYield", 1.0)):
        raw = _safe_float(info.get(key))
        if raw is not None and raw > 0:
            frac = raw * scale
            return frac if frac <= SUPPRESS_THRESHOLD else 0.0
    return 0.0


def validate_dividend_yield(yield_fraction: float, market_cap: float = 0.0) -> Tuple[bool, str]:
    """Return (display_ok, note).

    * display_ok=False means the caller should show "Data validation failed".
    * note is a short reason string ("" if everything fine).
    """
    y = _safe_float(yield_fraction) or 0.0
    if y <= 0:
        return True, ""
    if y > SUPPRESS_THRESHOLD:
        return False, f"dividend yield {y * 100:.1f}% exceeds {SUPPRESS_THRESHOLD * 100:.0f}%: data validation failed"
    if y > SUSPICIOUS_THRESHOLD:
        # large cap >= $10B: suspicious
        if market_cap and market_cap >= 10_000_000_000:
            return True, f"suspicious dividend yield {y * 100:.1f}% for large-cap; verify source"
    return True, ""
