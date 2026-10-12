"""Earnings-surprise units come from the schema, never from magnitude.

Audit finding F06: `_avg_surprise_pct` multiplied any value with magnitude <= 1
by 100 and left larger ones alone. That cannot separate a small percentage
surprise from a fraction, and it is discontinuous at an arbitrary boundary — an
input of 0.99 became 99.0 while 1.01 stayed 1.01, so two nearly identical
companies could rank a hundred places apart.

The corrected contract: the unit is decided once per frame from the column
name, against known provider schemas. An unrecognised schema yields no value
rather than a guessed one.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

MODULES = Path(__file__).resolve().parents[1] / "modules"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))

ers = importlib.import_module("earnings_revision_scoring")


class _Stock:
    def __init__(self, frame):
        self.earnings_history = frame


def _fraction_schema(values):
    """yfinance Ticker.earnings_history — 'surprisePercent' holds fractions."""
    return _Stock(pd.DataFrame({"surprisePercent": values}))


def _percent_schema(values):
    """yfinance earnings_dates — 'Surprise(%)' already holds percent."""
    return _Stock(pd.DataFrame({"Surprise(%)": values}))


# ---------------------------------------------------------------------------
# The discontinuity
# ---------------------------------------------------------------------------

def test_neighbouring_fractions_stay_neighbours():
    """0.99 and 1.01 must not end up 98 percentage points apart."""
    low = ers._avg_surprise_pct(_fraction_schema([0.99]))
    high = ers._avg_surprise_pct(_fraction_schema([1.01]))

    assert abs(high - low) < 5.0


def test_fraction_schema_is_scaled_to_percent():
    assert ers._avg_surprise_pct(_fraction_schema([0.05])) == pytest.approx(5.0)


def test_percent_schema_is_left_alone():
    assert ers._avg_surprise_pct(_percent_schema([5.0])) == pytest.approx(5.0)


def test_large_fraction_is_still_treated_as_a_fraction():
    """A 150% beat arrives as 1.5 in the fraction schema, not as 1.5%."""
    assert ers._avg_surprise_pct(_fraction_schema([1.5])) == pytest.approx(150.0)


def test_small_percent_is_still_treated_as_percent():
    """A 0.4% beat in the percent schema must not become 40%."""
    assert ers._avg_surprise_pct(_percent_schema([0.4])) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Signs, averaging and unknown schemas
# ---------------------------------------------------------------------------

def test_negative_surprises_keep_their_sign_and_scale():
    assert ers._avg_surprise_pct(_fraction_schema([-0.08])) == pytest.approx(-8.0)


def test_mixed_quarters_average_after_normalisation():
    got = ers._avg_surprise_pct(_fraction_schema([0.10, -0.02, 0.04, 0.00]))
    assert got == pytest.approx(3.0)


def test_unknown_schema_yields_no_value_rather_than_a_guess():
    stock = _Stock(pd.DataFrame({"mysterySurprise": [0.05]}))
    assert ers._avg_surprise_pct(stock) is None


def test_empty_and_missing_history_yield_no_value():
    assert ers._avg_surprise_pct(_Stock(pd.DataFrame({"surprisePercent": []}))) is None
    assert ers._avg_surprise_pct(_Stock(None)) is None


# ---------------------------------------------------------------------------
# The normalised value and its provenance both reach the caller
# ---------------------------------------------------------------------------

def test_signal_reports_the_unit_it_normalised_from():
    out = ers.compute_earnings_revision_signal(_fraction_schema([0.05]))

    assert out["avg_surprise_pct"] == pytest.approx(5.0)
    assert out["avg_surprise_source_unit"] == "fraction"


def test_signal_reports_percent_source_unit():
    out = ers.compute_earnings_revision_signal(_percent_schema([5.0]))

    assert out["avg_surprise_pct"] == pytest.approx(5.0)
    assert out["avg_surprise_source_unit"] == "percent"
