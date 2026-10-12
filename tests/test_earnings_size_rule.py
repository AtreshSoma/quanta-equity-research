"""P7: the earnings sizing rule lives in config, in one place, so the page's
pre-trade review can state exactly what the engine applies.

The engine scaled positions to 25% within 3 days, 50% within 14 and 80% within
30 (day bounds were literals); the card said "skip or use 1/3 size within 5
days". The day bounds are now SIZING keys with the same values.
"""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import thresholds as T  # noqa: E402


def test_the_day_bounds_keep_their_values():
    assert (T.SIZING.EARNINGS_IMMINENT_DAYS, T.SIZING.EARNINGS_NEAR_DAYS, T.SIZING.EARNINGS_ELEVATED_DAYS) == (3, 14, 30)


@pytest.mark.parametrize("days,mult", [(0, "IMMINENT"), (3, "IMMINENT"), (4, "NEAR"), (8, "NEAR"), (14, "NEAR"),
                                       (15, "ELEVATED"), (30, "ELEVATED"), (31, None), (-1, None)])
def test_the_multiplier_follows_the_configured_bands(days, mult):
    want = getattr(T.SIZING, f"EARNINGS_{mult}_SIZE_MULT") if mult else 1.0
    assert sp.earnings_size_multiplier(days) == want


def test_the_bands_come_from_config(monkeypatch):
    monkeypatch.setattr(T.SIZING, "EARNINGS_IMMINENT_DAYS", 5)
    assert sp.earnings_size_multiplier(5) == T.SIZING.EARNINGS_IMMINENT_SIZE_MULT
