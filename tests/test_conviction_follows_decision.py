"""Conviction describes the published call, not the score before the rules ran.

A live MSFT tear sheet (2026-11-09) read "WATCHLIST ... Conviction 79/100, High":
the signal generator set conviction from a Buy-range score, the decision rules
then held the call back on reward/risk 0.50:1, and nothing lowered the
conviction the page printed beside it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from _paths import ensure_paths

ensure_paths()

import TradingAnalysis_ELITE as elite  # noqa: E402
from engine import thresholds as T  # noqa: E402

CAP = T.RESEARCH.BLOCKED_CALL_CONVICTION_CAP


def _result(action: str, confidence: float):
    return SimpleNamespace(
        final_action=action, confidence_score=confidence,
        confidence_level=elite._confidence_level(confidence),
        position_sizing=elite.PositionSizing(), trade_setup=elite.TradeSetup())


@pytest.mark.parametrize("action", ["WATCHLIST", "HOLD_EXISTING", "AVOID"])
def test_a_held_back_call_never_reads_high_conviction(action):
    out = elite.enforce_final_decision_contract(_result(action, 79.3))
    assert out.confidence_score == CAP
    assert out.confidence_level not in (elite.ConfidenceLevel.HIGH, elite.ConfidenceLevel.VERY_HIGH)
    assert out.confidence_level == elite._confidence_level(CAP)


@pytest.mark.parametrize("action", ["BUY", "ACCUMULATE", "STRONG_BUY"])
def test_a_funded_call_keeps_its_conviction(action):
    out = elite.enforce_final_decision_contract(_result(action, 79.3))
    assert out.confidence_score == 79.3
    assert out.confidence_level == elite.ConfidenceLevel.HIGH


def test_a_conviction_already_under_the_cap_is_not_raised():
    out = elite.enforce_final_decision_contract(_result("WATCHLIST", 41.0))
    assert out.confidence_score == 41.0
    assert out.confidence_level == elite.ConfidenceLevel.LOW


def test_the_cap_sits_below_the_high_label():
    assert elite._confidence_level(CAP) == elite.ConfidenceLevel.MEDIUM
