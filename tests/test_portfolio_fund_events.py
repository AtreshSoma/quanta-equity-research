"""Fund event exemption is scoped to My Portfolio and never inferred from a ticker."""
from copy import deepcopy

import pytest

from engine import corporate_events as ce
from engine.decision import Freshness
from portfolio.fund_events import FUND_INSTRUMENT_TYPES
from portfolio.service import compact_analysis
from portfolio import guidance as gd
from test_my_portfolio import ctx, pctx

pytestmark = pytest.mark.real_event_gate


def result(monkeypatch, kind="ETF", action="BUY", grade="B", codes=()):
    import TradingAnalysis_ELITE as ta
    from engine import decision as dc
    fresh = Freshness("fresh", "2026-09-25", 0, [])
    monkeypatch.setattr(dc, "assess_price_freshness", lambda _: fresh)
    monkeypatch.setattr(ce, "check_many", lambda symbols, **_: {
        s: ce._unavailable("no SEC registrant found") for s in symbols})
    r = ta.AnalysisResult(ticker="SYNF", quote_type=kind, is_etf=True,
                          current_price=100, final_action=action,
                          final_action_codes=list(codes),
                          scores=ta.ScoreBreakdown(composite_score=68),
                          risk=ta.RiskMetrics(risk_grade=grade))
    ta._apply_corporate_events(r, r.ticker)
    ta.enforce_final_decision_contract(r)
    return ta, r


@pytest.mark.parametrize("kind", sorted(FUND_INSTRUMENT_TYPES))
def test_typed_fund_is_nonblocking_only_in_portfolio(monkeypatch, kind):
    ta, r = result(monkeypatch, kind)
    serialized = ta._mcp_result_to_dict(r)
    original = deepcopy(serialized)
    dec = compact_analysis(serialized)["decision"]
    assert dec["event_check"]["status"] == "not_applicable"
    assert dec["action"] == "BUY"
    assert dec["new_money"] == "ELIGIBLE"
    assert not any(b["code"].startswith("EVENT_") for b in dec["blockers"])
    g = gd.guide_position(pctx(), ctx(), dec)
    assert g.new_money == gd.NM_ACCUMULATE
    assert "Corporate-event check: not applicable to funds." in g.why
    assert serialized["decision"]["action"] == "WATCHLIST"
    assert serialized["decision"]["event_check"]["status"] == "unavailable"
    dec["event_check"]["text"] = "mutated portfolio copy"
    assert serialized == original, "Picks/shared cached results must not change"


@pytest.mark.parametrize("kind", [None, "", "UNKNOWN", "EQUITY", "INDEX"])
def test_unknown_and_nonfund_types_fail_closed_even_with_is_etf(monkeypatch, kind):
    ta, r = result(monkeypatch, kind)
    assert r.portfolio_fund_decision is None
    dec = compact_analysis(ta._mcp_result_to_dict(r))["decision"]
    assert dec["new_money"] == "WATCH"
    assert dec["event_check"]["status"] == "unavailable"


@pytest.mark.parametrize("action,grade,codes,expected", [
    ("WATCHLIST", "B", ["INVALID_TARGET"], "WATCH"),
    ("BUY", "F", [], "WATCH"),
    ("AVOID", "B", ["LOW_REWARD_RISK"], "AVOID"),
])
def test_other_gates_survive_fund_exemption(monkeypatch, action, grade, codes, expected):
    ta, r = result(monkeypatch, action=action, grade=grade, codes=codes)
    dec = compact_analysis(ta._mcp_result_to_dict(r))["decision"]
    assert dec["new_money"] == expected
    assert set(codes).issubset({b["code"] for b in dec["blockers"]})
    if grade == "F":
        assert dec["holder_action"] == "TRIM"


def test_old_cached_fund_without_pregate_verdict_is_not_guessed(monkeypatch):
    ta, r = result(monkeypatch)
    serialized = ta._mcp_result_to_dict(r)
    serialized.pop("portfolio_fund_decision")
    assert compact_analysis(serialized)["decision"] == serialized["decision"]


@pytest.mark.parametrize("kind", ["EQUITY", "INDEX", "", None])
def test_a_non_fund_result_carrying_a_fund_verdict_keeps_the_shared_decision(kind):
    """The exemption is keyed to the instrument type, not to the presence of a
    not-applicable candidate (audit R2: widening it to any type went unnoticed)."""
    from portfolio.fund_events import portfolio_decision
    shared = {"action": "WATCHLIST", "event_check": {"status": "unavailable", "code": "EVENT_CHECK_UNAVAILABLE"}}
    candidate = {"action": "ACCUMULATE", "event_check": {"status": "not_applicable"}}
    out = portfolio_decision({"quote_type": kind, "decision": shared, "portfolio_fund_decision": candidate})
    assert out == shared and out is not shared
