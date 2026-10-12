"""Evidence gate, the Decision object, freshness and the shared calendar."""
from __future__ import annotations

import json

from freezegun import freeze_time
from datetime import date, datetime, timedelta, timezone

import pytest

from engine import FinalAction
from engine.decision import SWING, Decision, assess_price_freshness
from engine.decision_engine import resolve_final_action
from engine.evidence import (
    Evidence,
    EvidenceStatus,
    get_evidence,
    present_action,
)
from engine.market_calendar import (
    add_trading_days,
    is_trading_day,
    last_completed_session,
    trading_days_between,
)
from engine.types import DecisionInputs


def _validated(days_old=1):
    gen = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {"strategies": {"x": {"status": "VALIDATED", "generated_utc": gen, "summary": "ok"}}}


def test_unknown_strategy_is_untested():
    ev = get_evidence("nope", registry={})
    assert ev.status is EvidenceStatus.UNTESTED
    assert not ev.status.permits_recommendation


def test_validated_goes_stale():
    assert get_evidence("x", _validated(1)).status is EvidenceStatus.VALIDATED
    assert get_evidence("x", _validated(400)).status is EvidenceStatus.STALE


def test_missing_registry_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTA_EVIDENCE_PATH", str(tmp_path / "missing.json"))
    assert get_evidence("shortterm.composite").status is EvidenceStatus.UNTESTED


def test_changed_live_model_does_not_inherit_old_validation():
    from engine.evidence import ELITE

    previous = {"strategies": {"elite.composite": {
        "status": "VALIDATED", "summary": "Old model only",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }}}
    assert ELITE != "elite.composite"
    assert get_evidence(ELITE, previous).status is EvidenceStatus.UNTESTED


def test_deep_alpha_composite_is_independent_of_risk_score():
    from TradingAnalysis_ELITE import MarketRegime, SignalGenerator

    generator = SignalGenerator()
    weights = generator._get_regime_weights(MarketRegime.SIDEWAYS)
    assert "risk" not in weights
    assert sum(weights.values()) == pytest.approx(1.0)
    scores = {"technical": 70, "fundamental": 60, "valuation": 50,
              "quality": 65, "sentiment": 55, "risk": 20}
    first = generator._calculate_composite(scores, weights)
    scores["risk"] = 100
    assert generator._calculate_composite(scores, weights) == first


def test_registry_file_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"strategies": {"s": {"status": "NO_EDGE", "summary": "IC -0.02"}}}))
    monkeypatch.setenv("QUANTA_EVIDENCE_PATH", str(p))
    ev = get_evidence("s")
    assert ev.status is EvidenceStatus.NO_EDGE and "IC" in ev.summary


@pytest.mark.parametrize("action", [FinalAction.STRONG_BUY, FinalAction.BUY, FinalAction.ACCUMULATE])
def test_unvalidated_buys_keep_their_name_but_always_carry_a_qualifier(action):
    for status in (EvidenceStatus.UNTESTED, EvidenceStatus.NO_EDGE,
                   EvidenceStatus.PARTIAL, EvidenceStatus.STALE):
        p = present_action(action, Evidence("s", status))
        assert p.label == action.value.replace("_", " ")
        assert p.qualifier and p.qualifier in p.caveat
        assert p.sizing_basis == "risk-budget-only"
        assert "not a recommendation" in p.caveat
    assert present_action(action, Evidence("s", EvidenceStatus.UNTESTED)).qualifier == "Unproven model"


def test_validated_buy_keeps_its_label():
    p = present_action(FinalAction.BUY, Evidence("s", EvidenceStatus.VALIDATED))
    assert p.label == "BUY" and p.sizing_basis == "validated" and p.qualifier == ""


def test_bearish_stance_survives_ownership_softening():
    inputs = DecisionInputs(ticker="X", composite_score=20, risk_grade="C",
                            risk_reward_ratio=0, current_price=10, stop_loss=9,
                            target_price=11, has_existing_position=False)
    res = resolve_final_action(inputs)
    assert res.final_action is FinalAction.AVOID          # "do not buy" for a non-holder
    assert res.stance == "BEARISH"                         # and recorded as a bearish call


def test_decision_to_dict_is_json_safe_and_consistent():
    d = Decision(ticker="ABC", engine="shortterm", horizon=SWING, action=FinalAction.BUY,
                 evidence=Evidence("s", EvidenceStatus.NO_EDGE), score=float("nan"),
                 as_of="2026-09-23", reference_price=10.0, stop=9.0, target=12.0)
    out = d.to_dict()
    json.dumps(out)                                        # no NaN, no dates
    assert out["score"] is None
    assert out["label"] == "BUY" and out["qualifier"] == "No proven edge"
    assert out["stance"] == "BULLISH"
    assert out["evidence"]["status"] == "NO_EDGE"
    assert "BUY (no proven edge)" in d.headline()
    assert "Tested out of sample" in d.headline()


def test_calendar_and_freshness():
    assert not is_trading_day(date(2026, 9, 7))            # Labor Day 2026
    assert add_trading_days(date(2026, 9, 4), 1) == date(2026, 9, 8)
    assert trading_days_between(date(2026, 9, 4), date(2026, 9, 8)) == 1
    # 10:00 ET Wednesday -> Tuesday is the last completed session.
    wed_morning = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)
    assert last_completed_session(wed_morning) == date(2026, 9, 22)
    assert assess_price_freshness(date(2026, 9, 22), wed_morning).status == "fresh"
    stale = assess_price_freshness(date(2026, 9, 15), wed_morning)
    assert stale.status == "stale" and stale.lag_sessions == 5
    assert assess_price_freshness(None).status == "unknown"


def test_deep_timing_cannot_overrule_nonallocating_final_action():
    from TradingAnalysis_ELITE import AnalysisResult, _compute_beginner_guidance

    result = AnalysisResult(ticker="ABC", current_price=100.0, final_action="WATCHLIST")
    result.technical.candlestick_patterns = [{
        "name": "bullish engulfing", "bias": "BULLISH",
        "reliability": "HIGH", "type": "REVERSAL",
    }]
    result.technical.macd_signal = "BULLISH"
    result.technical.price_vs_sma50 = 3.0
    result.technical.volume_ratio = 1.6
    result.trade_setup.target_base = 120.0
    result.trade_setup.stop_loss = 95.0
    result.trade_setup.risk_reward_ratio = 4.0
    guidance = _compute_beginner_guidance(result)
    assert guidance["buy_now"] == "NO"
    assert "starter" not in guidance["new_buyer"].lower()


def test_mcp_payload_and_text_gate_unvalidated_buy(tmp_path, monkeypatch):
    from TradingAnalysis_ELITE import (
        AnalysisResult, Signal, _mcp_format_analysis_text, _mcp_result_to_dict,
    )

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"strategies": {
        "elite.research.v2": {"status": "NO_EDGE", "summary": "No edge in OOS study"}
    }}))
    monkeypatch.setenv("QUANTA_EVIDENCE_PATH", str(registry))
    # A funded action needs a dated, current price (undated data fails closed).
    result = AnalysisResult(ticker="ABC", company_name="Example", current_price=100.0,
                            final_action="BUY", signal=Signal.BUY,
                            recommendation_text="BUY - invest now",
                            market_timestamp="2026-09-25")
    with freeze_time("2026-09-25 18:00:00"):
        payload = _mcp_result_to_dict(result)
    assert payload["model_action"] == "BUY"
    assert payload["recommendation_label"] == "BUY"
    assert payload["recommendation_qualifier"] == "No proven edge"
    assert payload["evidence"]["status"] == "NO_EDGE"
    rendered = _mcp_format_analysis_text(payload)
    assert "Research decision: BUY (no proven edge)" in rendered
    assert "- **Recommendation:** BUY" not in rendered
