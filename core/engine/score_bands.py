"""The one score-band table for the 0-100 composite.

The decision resolver's candidate action, the signal generator's legacy
ladder and the blocker/holder bands all read these floors, so no two layers
can hold different cut-offs for the same score.
"""
from __future__ import annotations

from .thresholds import LADDER

SCORE_BANDS = LADDER.SCORE_BANDS


def band_floor(name: str) -> float:
    return next(floor for floor, band in SCORE_BANDS if band == name)


def band_for(score: float) -> str:
    for floor, band in SCORE_BANDS:
        if score >= floor:
            return band
    return "STRONG_SELL"
