"""Free-factor control model: 12-1 momentum plus the Research quality leg.

Pre-registered in docs/preregistration/factor_baseline_v1.md. QUANTA's own
backtests found the Research composite "not distinguishable from 12-1 momentum"
(core/apex_quant/_reports/factor_baselines_research_v2_pit_oos.txt). This model
is that free factor, plus the quality leg that carried the composite's measured
ranking power, recorded in shadow beside every Research call. If the composite
cannot beat it prospectively, its extra complexity has not earned its place.

Pure functions only. Nothing here changes the live call, its levels or gates.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import pandas as pd

from engine.score_bands import band_for
from engine.thresholds import FACTOR_BASELINE as FB

VERSION = "factor_baseline"
KEY = "factor.baseline.v1"
ENGINE = "factor_baseline_shadow"


def momentum_12_1(close: Any, as_of: Any = None) -> Optional[float]:
    """Return from about 12 months ago to about 1 month ago (calendar days, as in apex_quant).

    ``close`` is a date-indexed series of closes. None when history is shorter
    than the lookback or a price is missing or not positive.
    """
    try:
        series = pd.Series(close).dropna().sort_index()
        series.index = pd.to_datetime(series.index)
        if getattr(series.index, "tz", None) is not None:
            series.index = series.index.tz_localize(None)
    except (TypeError, ValueError):
        return None
    if series.empty:
        return None
    end = pd.Timestamp(as_of) if as_of is not None else series.index[-1]
    start_day = end - pd.Timedelta(days=int(FB.LOOKBACK_DAYS))
    skip_day = end - pd.Timedelta(days=int(FB.SKIP_DAYS))
    if start_day < series.index[0]:
        return None
    then, recent = series.asof(start_day), series.asof(skip_day)
    try:
        then, recent = float(then), float(recent)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(then) and math.isfinite(recent)) or then <= 0 or recent <= 0:
        return None
    return recent / then - 1.0


def momentum_leg(momentum: Optional[float]) -> Optional[float]:
    """0..100 from the 12-1 return; None when it was not measured."""
    if momentum is None or not math.isfinite(momentum):
        return None
    sat = float(FB.MOMENTUM_SATURATION)
    return 50.0 + 50.0 * max(-1.0, min(1.0, momentum / sat))


def assess(momentum: Optional[float], quality: Optional[float],
           is_fund: bool = False) -> Optional[Dict[str, Any]]:
    """The control model's score and band; None when momentum is unmeasured.

    Funds have no company quality, so they are scored on momentum alone.
    """
    legs: Dict[str, Optional[float]] = {"momentum": momentum_leg(momentum)}
    if legs["momentum"] is None:
        return None
    q = None if is_fund or quality is None else float(quality)
    legs["quality"] = q if q is not None and math.isfinite(q) else None
    weights = {k: float(w) for k, w in FB.WEIGHTS.items() if legs.get(k) is not None}
    total = sum(weights.values())
    score = sum(legs[k] * w for k, w in weights.items()) / total
    return {"version": VERSION, "engine": ENGINE, "key": KEY, "score": round(score, 4),
            "band_action": band_for(score), "momentum_12_1": momentum,
            "legs": legs, "weights_used": {k: w / total for k, w in weights.items()}}
