"""
Earnings / EPS Estimate Revision Scoring
========================================

Zacks-style earnings-estimate-revision factor — historically one of the
strongest cross-sectional alpha signals in US equities and the single biggest
gap in the SP_ELITE_PRO / Alpha_Picks composite scorers.

This module is intentionally self-contained and defensive: it reads whatever
yfinance happens to expose (schemas drift between versions) and ALWAYS returns
a neutral result rather than raising, so a flaky data feed can never break a
scan.

Signals combined (all optional, gracefully skipped when missing):
  1. Analyst EPS revision counts  (yfinance `eps_revisions`):
        upLast30days vs downLast30days  -> net revision breadth
  2. Forward EPS estimate trend    (yfinance `eps_trend`):
        current estimate vs the 30/90-days-ago estimate -> revision magnitude
  3. Earnings surprise history     (yfinance `earnings_history`):
        average `surprisePercent` over recent quarters -> beat/miss tendency

Output (dict):
  sentiment        : "UPGRADING" / "STABLE" / "DOWNGRADING" / "UNKNOWN"
  revision_score   : 0..10  (5.0 == neutral; used for reporting/diagnostics)
  score_adjustment : bounded additive delta for the 0..10 composite score
                     (REVISION_ADJ_FLOOR .. REVISION_ADJ_CAP)
  net_revisions_30d: up30 - down30 (analyst count breadth)
  eps_trend_pct_30d: % change in the forward estimate over the last 30 days
  avg_surprise_pct : mean earnings surprise % over recent quarters
  avg_surprise_source_unit : provider unit that % was normalised from
                     ("fraction" / "percent"), or None when no recognised
                     surprise schema was present
  note             : one-line human-readable summary
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Bounds for the additive composite-score adjustment. Kept symmetric and modest
# so revisions inform — but never single-handedly dominate — the 0..10 score.
REVISION_ADJ_CAP: float = 1.5
REVISION_ADJ_FLOOR: float = -1.5

# Forward-looking periods we prefer, in priority order. yfinance indexes the
# estimate frames by these period codes; the next-quarter ("0q") estimate is the
# most timely revision signal.
_PREFERRED_PERIODS = ("0q", "+1q", "0y", "+1y")


def _neutral_result() -> Dict[str, Any]:
    return {
        "sentiment": "UNKNOWN",
        "revision_score": 5.0,
        "score_adjustment": 0.0,
        "net_revisions_30d": 0,
        "eps_trend_pct_30d": 0.0,
        "avg_surprise_pct": 0.0,
        # [F06] Provider unit the surprise was normalised FROM ("fraction" /
        # "percent"), or None when no recognised schema was present. Kept so a
        # downstream consumer can tell "no data" from "0% surprise".
        "avg_surprise_source_unit": None,
        "note": "",
    }


def _coerce_float(value: Any) -> Optional[float]:
    """Best-effort float conversion that rejects NaN/None/garbage."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _row_for_period(frame: Any) -> Optional[Dict[str, Any]]:
    """
    Return a single row (as a label->value dict) from a yfinance estimate frame,
    preferring the nearest-term period. Tolerates DataFrame or dict inputs.
    """
    if frame is None:
        return None

    # pandas DataFrame path
    index = getattr(frame, "index", None)
    if index is not None and hasattr(frame, "loc"):
        try:
            if getattr(frame, "empty", False):
                return None
        except Exception:
            pass
        labels = [str(i) for i in list(index)]
        for period in _PREFERRED_PERIODS:
            if period in labels:
                pos = labels.index(period)
                try:
                    return frame.iloc[pos].to_dict()
                except Exception:
                    return None
        # No preferred label matched — fall back to the first row.
        try:
            return frame.iloc[0].to_dict()
        except Exception:
            return None

    # plain dict-of-dicts path
    if isinstance(frame, dict):
        for period in _PREFERRED_PERIODS:
            if period in frame and isinstance(frame[period], dict):
                return frame[period]
        for val in frame.values():
            if isinstance(val, dict):
                return val
    return None


def _get_field(row: Dict[str, Any], *names: str) -> Optional[float]:
    """Case-insensitive lookup across candidate column names."""
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered:
            f = _coerce_float(lowered[name.lower()])
            if f is not None:
                return f
    return None


def _revision_breadth(stock: Any) -> Optional[int]:
    """Net analyst revision count over the last 30 days (up - down)."""
    frame = getattr(stock, "eps_revisions", None)
    row = _row_for_period(frame)
    if not row:
        return None
    up30 = _get_field(row, "upLast30days", "upLast30Days", "up_last_30days")
    down30 = _get_field(row, "downLast30days", "downLast30Days", "down_last_30days")
    if up30 is None and down30 is None:
        return None
    return int((up30 or 0.0) - (down30 or 0.0))


def _estimate_trend_pct(stock: Any) -> Optional[float]:
    """Percent change in the forward EPS estimate over the last ~30 days."""
    frame = getattr(stock, "eps_trend", None)
    row = _row_for_period(frame)
    if not row:
        return None
    current = _get_field(row, "current")
    ago_30 = _get_field(row, "30daysAgo", "30DaysAgo", "days30Ago")
    ago_90 = _get_field(row, "90daysAgo", "90DaysAgo", "days90Ago")
    baseline = ago_30 if ago_30 not in (None, 0.0) else ago_90
    if current is None or baseline in (None, 0.0):
        return None
    # Guard against sign flips around zero making the % meaningless.
    if baseline < 0 and current < 0:
        return ((abs(baseline) - abs(current)) / abs(baseline)) * 100.0
    return ((current - baseline) / abs(baseline)) * 100.0


# [F06] Known earnings-surprise column schemas and the unit each one carries.
# The unit is a property of the provider's schema, NOT of a value's magnitude:
# a 0.4% beat and a 0.004 fraction are indistinguishable by size, so guessing
# from magnitude is unrecoverable. Matched against the lower-cased column name;
# first match wins, so the more specific patterns come first.
_SURPRISE_SCHEMAS = (
    # yfinance Ticker.earnings_history — fractions (0.0512 == +5.12%).
    ("surprisepercent", "fraction", 100.0),
    # yfinance Ticker.earnings_dates — already percent ("Surprise(%)").
    ("surprise(%)", "percent", 1.0),
    ("surprise (%)", "percent", 1.0),
    ("surprise%", "percent", 1.0),
    ("surprise_pct", "percent", 1.0),
)


def _resolve_surprise_column(columns: Any) -> Optional[tuple]:
    """Return (column, unit, scale) for the first recognised schema, else None."""
    for col in columns:
        name = str(col).lower().replace(" ", "")
        for pattern, unit, scale in _SURPRISE_SCHEMAS:
            if pattern.replace(" ", "") == name:
                return col, unit, scale
    return None


def _avg_surprise_pct_with_unit(stock: Any) -> tuple:
    """Mean earnings surprise, normalised to percent, plus its source unit.

    Returns ``(percent, source_unit)``; ``(None, None)`` when no recognised
    surprise column is present. An unknown schema deliberately yields no value
    rather than a guessed one — a wrong unit is worse than a missing field,
    because it silently reorders the whole cross-sectional ranking.
    """
    frame = getattr(stock, "earnings_history", None)
    if frame is None or not hasattr(frame, "columns"):
        return None, None
    try:
        if getattr(frame, "empty", False):
            return None, None
        resolved = _resolve_surprise_column(frame.columns)
        if resolved is None:
            return None, None
        col, unit, scale = resolved
        vals = [
            v for v in (_coerce_float(x) for x in frame[col].tolist())
            if v is not None
        ]
        if not vals:
            return None, None
        return sum(v * scale for v in vals) / len(vals), unit
    except Exception:
        return None, None


def _avg_surprise_pct(stock: Any) -> Optional[float]:
    """Mean earnings surprise % over the recent reported quarters."""
    return _avg_surprise_pct_with_unit(stock)[0]


def compute_earnings_revision_signal(
    stock: Any,
    info: Optional[Dict[str, Any]] = None,
    ticker: str = "",
) -> Dict[str, Any]:
    """
    Compute the EPS-estimate-revision signal for a single stock.

    Parameters
    ----------
    stock : yfinance.Ticker-like
        Object exposing (optionally) ``eps_revisions``, ``eps_trend`` and
        ``earnings_history`` attributes.
    info : dict, optional
        ``stock.info`` payload (unused today, accepted for forward-compat).
    ticker : str
        For logging only.

    Returns
    -------
    dict
        See module docstring. Always returns a usable (neutral-on-failure) dict.
    """
    result = _neutral_result()
    if stock is None:
        return result

    try:
        net_rev = _revision_breadth(stock)
        trend_pct = _estimate_trend_pct(stock)
        surprise, surprise_unit = _avg_surprise_pct_with_unit(stock)
    except Exception as exc:  # pragma: no cover - pure defensive guard
        logger.debug("%s: earnings-revision signal failed: %s", ticker, exc)
        return result

    if net_rev is None and trend_pct is None and surprise is None:
        return result  # No usable estimate data at all.

    result["net_revisions_30d"] = int(net_rev or 0)
    result["eps_trend_pct_30d"] = round(float(trend_pct or 0.0), 2)
    result["avg_surprise_pct"] = round(float(surprise or 0.0), 2)
    result["avg_surprise_source_unit"] = surprise_unit

    # --- Convert each component to a contribution on a -1..+1 scale ----------
    # 1) Analyst revision breadth (net up - down over 30d).
    breadth = 0.0
    if net_rev is not None:
        breadth = max(-1.0, min(1.0, net_rev / 3.0))  # +/-3 net revisions saturates

    # 2) Forward estimate trend magnitude (% change over 30d).
    trend = 0.0
    if trend_pct is not None:
        trend = max(-1.0, min(1.0, trend_pct / 5.0))  # +/-5% move saturates

    # 3) Earnings surprise tendency.
    surp = 0.0
    if surprise is not None:
        surp = max(-1.0, min(1.0, surprise / 10.0))  # +/-10% avg surprise saturates

    # Weight the timeliest signals highest: trend (estimate cuts/raises) and
    # breadth matter most; surprise history is a slower-moving confirmation.
    composite = (trend * 0.45) + (breadth * 0.40) + (surp * 0.15)  # -1..+1

    adjustment = composite * REVISION_ADJ_CAP
    adjustment = max(REVISION_ADJ_FLOOR, min(REVISION_ADJ_CAP, adjustment))

    result["score_adjustment"] = round(adjustment, 2)
    result["revision_score"] = round(5.0 + composite * 5.0, 2)  # 0..10, 5 == neutral

    # --- Sentiment label + note ---------------------------------------------
    if composite >= 0.20:
        result["sentiment"] = "UPGRADING"
    elif composite <= -0.20:
        result["sentiment"] = "DOWNGRADING"
    else:
        result["sentiment"] = "STABLE"

    parts = []
    if net_rev:
        parts.append(f"{net_rev:+d} net analyst revisions (30d)")
    if trend_pct is not None and abs(trend_pct) >= 0.5:
        parts.append(f"fwd EPS est {trend_pct:+.1f}% (30d)")
    if surprise is not None and abs(surprise) >= 1.0:
        parts.append(f"avg surprise {surprise:+.1f}%")
    result["note"] = "; ".join(parts) if parts else "Estimates stable"

    return result
