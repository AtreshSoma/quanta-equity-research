"""Short-term calls go through the shared resolver as a conservatism ratchet."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.thresholds import SHORTTERM as ST
from engine.types import FinalAction

_MODULE_PATH = Path(__file__).resolve().parent.parent / "core" / "SP_ShortTerm.py"
_SPEC = importlib.util.spec_from_file_location("sp_shortterm_resolver", _MODULE_PATH)
st = importlib.util.module_from_spec(_SPEC)
sys.modules["sp_shortterm_resolver"] = st
_SPEC.loader.exec_module(st)


def _setup(**overrides):
    d = dict(
        ticker="TEST", company="Test Co", sector="Technology", industry="Software",
        signal="BUY", confidence=80.0, overall_score=75.0,
        technical_score=70.0, fundamental_score=60.0, momentum_score=70.0,
        quality_score=65.0, analyst_score=60.0, pattern_score=55.0,
        entry_price=100.0, entry_zone_low=99.0, entry_zone_high=101.0,
        optimal_entry=99.5, primary_target=110.0, secondary_target=115.0,
        aggressive_target=120.0, stop_loss=95.0, trailing_stop_pct=5.0,
        timeframe="swing", expected_hold_days=10, min_hold_days=3, max_hold_days=20,
        allocation_usd=1000.0, allocation_pct=4.0, shares=10, position_score=80.0,
        risk_amount=45.0, risk_pct=0.2, reward_amount=100.0,
        risk_reward_ratio=2.5, expected_return_pct=10.0, max_loss_pct=5.0,
        rsi=55.0, rsi_trend="Rising", macd=0.5, macd_signal=0.3, macd_histogram=0.2,
        stochastic=50.0, bb_position=0.5, atr=2.0, atr_pct=2.0,
        volume_ratio=1.2, obv_trend="Accumulation",
        current_price=100.0, price_vs_sma20=1.0, price_vs_sma50=2.0,
        price_vs_sma200=5.0, high_52w=120.0, low_52w=70.0,
        distance_from_high_pct=-16.0, distance_from_low_pct=42.0,
        execution_entry=99.5, is_funded=True, price_as_of="2026-10-27",
    )
    d.update(overrides)
    return st.TradingSetup(**d)


def test_clean_setup_unchanged():
    dec = st.decision_for_setup(_setup(risk_grade="B"))
    assert dec.action is FinalAction.BUY
    assert dec.blocking_codes == []
    assert dec.new_money == "ELIGIBLE"
    assert dec.risk_grade == "B"


def test_unmeasured_grade_does_not_block():
    dec = st.decision_for_setup(_setup(risk_grade=""))
    assert dec.action is FinalAction.BUY
    assert dec.blocking_codes == []


def test_grade_f_blocks_like_research():
    dec = st.decision_for_setup(_setup(risk_grade="F"))
    assert dec.action is FinalAction.WATCHLIST
    assert "RISK_GRADE_F" in dec.blocking_codes
    # The UI's "Main blocker" / "Would change with" fields are populated.
    assert dec.primary_blocker
    assert [b for b in dec.blockers if b.get("remedy") or b.get("text")]
    assert dec.sizing["shares"] == 0


def test_grade_d_caps_buy_at_accumulate():
    dec = st.decision_for_setup(_setup(risk_grade="D"))
    assert dec.action is FinalAction.ACCUMULATE
    assert "RISK_GRADE_D_CAP" in dec.blocking_codes


def test_rr_below_shortterm_hurdle_blocks_with_shared_code():
    dec = st.decision_for_setup(_setup(risk_reward_ratio=ST.MIN_RISK_REWARD - 0.3))
    assert dec.action is FinalAction.WATCHLIST
    assert "LOW_REWARD_RISK" in dec.blocking_codes
    assert any(f"{ST.MIN_RISK_REWARD:.2f}" in t for t in dec.blocking)


def test_rr_between_research_and_shortterm_hurdles_uses_shortterm():
    # 1.9 would pass Research's gate (1.5) but not the short-term 2.0 hurdle.
    dec = st.decision_for_setup(_setup(risk_reward_ratio=1.9))
    assert "LOW_REWARD_RISK" in dec.blocking_codes


def test_strong_buy_folds_until_evidence_validated():
    dec = st.decision_for_setup(_setup(signal="STRONG_BUY", overall_score=85.0, risk_grade="A"))
    if str(dec.evidence.status.value).upper() == "VALIDATED":
        assert dec.action is FinalAction.STRONG_BUY
    else:
        assert dec.action is FinalAction.BUY
        assert "STRONG_BUY_FOLDED" in dec.blocking_codes


@pytest.mark.parametrize("signal", ["STRONG_BUY", "BUY", "ACCUMULATE", "WATCHLIST"])
@pytest.mark.parametrize("grade", ["", "A", "C", "D", "F"])
@pytest.mark.parametrize("rr", [1.0, 2.0, 4.0])
def test_ratchet_never_upgrades(signal, grade, rr):
    base = _setup(signal=signal, risk_grade=grade, risk_reward_ratio=rr, overall_score=82.0)
    rank = st._ACTION_RANK
    dec = st.decision_for_setup(base)
    original = FinalAction(signal)
    if original.allocates_new_capital and not (base.is_funded and base.shares > 0):
        original = FinalAction.WATCHLIST
    assert rank[dec.action] <= rank[original]


def test_ratchet_helper_cannot_promote_watchlist():
    action, _codes, _why = st._ratchet_with_shared_resolver(
        _setup(risk_grade="A", overall_score=95.0), FinalAction.WATCHLIST, "VALIDATED")
    assert action is FinalAction.WATCHLIST


def test_unfunded_setup_stays_watchlist():
    dec = st.decision_for_setup(_setup(shares=0, is_funded=False))
    assert dec.action is FinalAction.WATCHLIST


def test_risk_grade_measured_from_bars_and_unmeasured_when_short():
    analyzer = object.__new__(st.StrategicStockAnalyzer)
    analyzer.benchmark_returns = None
    gen = np.random.default_rng(3)
    idx = pd.date_range("2025-01-01", periods=250, freq="B")
    close = pd.Series(100 * np.exp(np.cumsum(gen.normal(0.0005, 0.012, 250))), index=idx)
    hist = pd.DataFrame({"Close": close, "Volume": 3_000_000.0}, index=idx)
    s = _setup()
    analyzer._attach_risk_grade(s, hist)
    assert s.risk_grade in {"A", "B", "C", "D", "F"}
    assert s.risk_grade_lookback_days >= 200

    s2 = _setup()
    analyzer._attach_risk_grade(s2, hist.head(10))
    assert s2.risk_grade == ""
