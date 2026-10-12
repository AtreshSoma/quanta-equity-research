"""Indicators and breadth must mean what their labels say.

Audit section 13 (Data Quality Risks).

  * `_trend_metrics` set `sma200` to the mean of whatever history existed once
    there were 150 closes, so a name with ~60 valid sessions could publish a
    60-bar average under a "200-day" label — and `above_sma200` /
    `sma50_gt_sma200` (a golden-cross test) were then computed against it.
  * Participation over four index proxies (SPY/QQQ/DIA/IWM, or four Indian
    indices) was labelled "Breadth". 75% there means three of four heavily
    overlapping proxies, not 75% of exchange constituents. A failed member also
    silently shrank the denominator, making the remaining sample look more
    representative than it was.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402


# ---------------------------------------------------------------------------
# A 200-day average requires 200 sessions
# ---------------------------------------------------------------------------

def test_short_history_reports_sma200_unavailable_instead_of_a_shorter_mean():
    metrics = webapp._trend_metrics_from_closes("SPY", [100.0] * 160)

    assert metrics["sma200"] is None
    assert metrics["above_sma200"] is None
    assert metrics["sma50_gt_sma200"] is None


def test_short_history_reports_the_coverage_it_actually_had():
    metrics = webapp._trend_metrics_from_closes("SPY", [100.0] * 160)

    assert metrics["sma200_sessions"] == 160
    assert metrics["sma200_sessions_required"] == 200


def test_full_history_still_publishes_a_real_200_day_average():
    closes = [100.0] * 100 + [200.0] * 150   # 250 sessions
    metrics = webapp._trend_metrics_from_closes("SPY", closes)

    # Mean of the LAST 200 closes: 50 at 100 and 150 at 200.
    assert metrics["sma200"] == pytest.approx(175.0)
    assert metrics["above_sma200"] is True
    assert metrics["sma50_gt_sma200"] is True


def test_sma50_is_unaffected_by_the_sma200_gate():
    metrics = webapp._trend_metrics_from_closes("SPY", [100.0] * 160)
    assert metrics["sma50"] == pytest.approx(100.0)


def test_too_little_history_for_any_trend_returns_nothing():
    assert webapp._trend_metrics_from_closes("SPY", [100.0] * 10) is None


# ---------------------------------------------------------------------------
# Index participation is not exchange breadth
# ---------------------------------------------------------------------------

def test_participation_reports_its_numerator_and_denominator():
    basket = [
        {"symbol": "SPY", "above_sma50": True},
        {"symbol": "QQQ", "above_sma50": True},
        {"symbol": "DIA", "above_sma50": True},
        {"symbol": "IWM", "above_sma50": False},
    ]
    part = webapp._index_participation(basket, ["SPY", "QQQ", "DIA", "IWM"])

    assert part["above"] == 3
    assert part["measured"] == 4
    assert part["pct"] == 75


def test_a_failed_member_is_reported_rather_than_shrinking_the_denominator():
    """3/3 must not be presented as if the missing member never existed."""
    basket = [
        {"symbol": "SPY", "above_sma50": True},
        {"symbol": "QQQ", "above_sma50": True},
        {"symbol": "DIA", "above_sma50": True},
    ]
    part = webapp._index_participation(basket, ["SPY", "QQQ", "DIA", "IWM"])

    assert part["measured"] == 3
    assert part["requested"] == 4
    assert part["missing"] == ["IWM"]


def test_participation_carries_a_label_naming_the_proxies_not_breadth():
    basket = [{"symbol": "SPY", "above_sma50": True}]
    part = webapp._index_participation(basket, ["SPY", "QQQ"])

    assert "breadth" not in part["label"].lower()
    assert "1/1" in part["label"] or "1 of 1" in part["label"]


def test_no_measurable_members_yields_no_participation():
    assert webapp._index_participation([], ["SPY", "QQQ"])["pct"] is None
