"""Counting correlated sources is not evidence, and a terminal move is not a hit.

Audit section 18 (REMOVE) and section 12 (Confidence and probability).

  * `target_confidence = len(targets) * 25` treated "we had four numbers" as
    "we are 100% confident". The analyst target, the DCF (which anchors extreme
    estimates back to price and blends analyst targets) and technical
    resistance all lean on the same price and the same sell-side estimates, so
    they are not independent confirmations — and a count says nothing about
    whether they agree.
  * `target_probability` was Monte Carlo's terminal profit probability, i.e.
    P(price above entry at the horizon). That is not P(target reached), and it
    is certainly not P(target before stop), which is what a trader reads it as.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
MODULES = Path(__file__).resolve().parents[1] / "modules"
for _p in (CORE, MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ta = importlib.import_module("TradingAnalysis_ELITE")


# ---------------------------------------------------------------------------
# Source count is reported as a count, never as a confidence percentage
# ---------------------------------------------------------------------------

def test_agreement_of_tightly_clustered_targets_is_high():
    got = ta._target_agreement_pct([100.0, 101.0, 99.0])
    assert got > 90


def test_agreement_of_widely_dispersed_targets_is_low():
    got = ta._target_agreement_pct([50.0, 100.0, 200.0])
    assert got < 50


def test_a_single_target_has_no_measurable_agreement():
    """One number cannot agree with anything, so there is nothing to report."""
    assert ta._target_agreement_pct([100.0]) is None


def test_no_targets_have_no_agreement():
    assert ta._target_agreement_pct([]) is None


def test_agreement_ignores_scale():
    tight_cheap = ta._target_agreement_pct([10.0, 10.1, 9.9])
    tight_dear = ta._target_agreement_pct([1000.0, 1010.0, 990.0])
    assert tight_cheap == pytest.approx(tight_dear, abs=1.0)


def test_more_sources_alone_does_not_raise_agreement():
    """The defect in one line: adding a fourth number used to add 25 points."""
    three = ta._target_agreement_pct([100.0, 120.0, 80.0])
    four = ta._target_agreement_pct([100.0, 120.0, 80.0, 100.0])
    assert four <= three + 5


# ---------------------------------------------------------------------------
# The valuation payload describes what it actually has
# ---------------------------------------------------------------------------

def test_valuation_reports_source_count_and_agreement_not_a_confidence_score():
    v = ta.ValuationMetrics()
    ta._apply_target_quality(v, [100.0, 101.0, 99.0], source_names=["analyst", "dcf", "technical"])

    assert v.target_source_count == 3
    assert v.target_sources == ["analyst", "dcf", "technical"]
    assert v.target_agreement_pct > 90


def test_target_confidence_is_no_longer_derived_from_a_source_count():
    src = (Path(ta.__file__)).read_text(encoding="utf-8")
    assert "len(targets) * 25" not in src


# ---------------------------------------------------------------------------
# The Monte Carlo probability names its event
# ---------------------------------------------------------------------------

def test_terminal_probability_is_not_published_as_a_target_hit_probability():
    src = (Path(ta.__file__)).read_text(encoding="utf-8")
    assert "valuation.target_probability = monte_carlo.probability_profit * 100" not in src


def test_monte_carlo_probability_carries_an_explicit_event_definition():
    mc = ta.MonteCarloResults()
    mc.probability_profit = 0.62
    mc.days_simulated = 30
    label = ta.monte_carlo_event_label(mc)

    assert "30" in label
    # It must describe a terminal comparison, not a path/touch event.
    assert "above" in label.lower() or "at or above" in label.lower()
    assert "before" not in label.lower()
