"""Regression coverage for the WATCHLIST root-cause remediation."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

import TradingAnalysis_ELITE as ta  # noqa: E402
from engine.types import FinalAction  # noqa: E402


def _analysis_result(action: str = "WATCHLIST") -> ta.AnalysisResult:
    result = ta.AnalysisResult(ticker="TLT")
    result.current_price = 83.0
    result.market_timestamp = "2026-09-25"
    result.final_action = action
    result.final_action_reasons = ["R/R below minimum"]
    result.final_action_codes = ["LOW_REWARD_RISK"]
    result.position_sizing = ta.PositionSizing(
        recommended_shares=96,
        position_value=7_968.0,
        portfolio_weight=0.07968,
        kelly_optimal=10_000.0,
        is_viable=True,
        recommendation="Start building position in tranches",
    )
    result.trade_setup = ta.TradeSetup(
        entry_price=83.0, stop_loss=79.0, target_base=84.0,
        risk_reward_ratio=0.24,
    )
    return result


def test_non_allocating_decision_zeroes_every_executable_size():
    result = _analysis_result()
    ta.enforce_final_decision_contract(result)

    assert result.position_sizing.recommended_shares == 0
    assert result.position_sizing.position_value == 0
    assert result.position_sizing.portfolio_weight == 0
    assert result.position_sizing.kelly_optimal == 0
    assert result.position_sizing.is_viable is False
    assert "No new position" in result.position_sizing.recommendation

    payload = ta._mcp_result_to_dict(result)
    assert payload["position_sizing"]["recommended_shares"] == 0
    assert payload["decision"]["sizing"]["position_value"] == 0
    assert payload["decision"]["blocking_codes"] == ["LOW_REWARD_RISK"]


def test_mc_shape_is_diagnostic_not_a_tier_specific_veto(monkeypatch):
    generator = ta.SignalGenerator()
    monkeypatch.setattr(generator, "_calculate_component_scores", lambda *a: {
        "technical": 60.0, "fundamental": 60.0, "valuation": 60.0,
        "quality": 60.0, "sentiment": 60.0, "risk": 60.0,
    })
    monkeypatch.setattr(generator, "_get_regime_weights", lambda _r: {
        "technical": 1.0, "fundamental": 0.0, "valuation": 0.0,
        "quality": 0.0, "sentiment": 0.0,
    })
    monkeypatch.setattr(generator, "_count_confirmations", lambda *a: (5, 0))

    mc = ta.MonteCarloResults(percentile_5=80.0, percentile_75=108.0)
    common = (
        ta.TechnicalIndicators(), ta.FundamentalData(), ta.RiskMetrics(),
        ta.SentimentData(), ta.InsiderData(), ta.MarketIntelligence(),
        ta.ValuationMetrics(), None, mc, 100.0,
    )

    outputs = []
    for composite, signal in ((64.0, ta.Signal.ACCUMULATE), (66.0, ta.Signal.BUY)):
        monkeypatch.setattr(generator, "_calculate_composite", lambda *_a, c=composite: c)
        monkeypatch.setattr(generator, "_determine_signal", lambda *_a, s=signal, **_k: (s, 60.0))
        outputs.append(generator.generate(*common)[0])

    assert outputs == [ta.Signal.ACCUMULATE, ta.Signal.BUY]


def test_session_freshness_rejects_four_session_old_bar():
    frame = pd.DataFrame(
        {"Close": [100.0]}, index=pd.DatetimeIndex(["2026-09-18"])
    )
    fresh = ta.QuantaEliteAnalyzer._price_data_freshness(
        frame, now=datetime(2026, 9, 25, 22, tzinfo=timezone.utc)
    )
    assert fresh.status == "stale"
    assert fresh.lag_sessions >= 4


def test_failed_dcf_is_unavailable_and_excluded_from_target_blend():
    analyzer = ta.ValuationAnalyzer()
    fundamental = ta.FundamentalData(fcf_per_share=0.0, eps_ttm=0.0)
    valuation = analyzer.analyze("X", 100.0, fundamental, ta.RiskMetrics(), 180.0)

    assert valuation.dcf_status == "unavailable"
    assert valuation.dcf_fair_value == 0.0
    assert valuation.valuation_status == "unavailable"
    assert valuation.valuation_label == "UNAVAILABLE"

    out = analyzer.calculate_composite_targets(
        100.0, valuation, ta.MarketIntelligence(), ta.MonteCarloResults(),
        ta.TechnicalIndicators(),
    )
    assert out.target_source_count == 0
    assert out.target_base == 0.0


def test_dcf_does_not_double_count_analyst_consensus():
    analyzer = ta.ValuationAnalyzer()
    fundamental = ta.FundamentalData(
        fcf_per_share=4.0, eps_ttm=5.0, pe_ratio=45.0,
        revenue_growth_yoy=25.0,
    )
    low = analyzer.analyze("X", 100.0, fundamental, ta.RiskMetrics(), 120.0)
    high = analyzer.analyze("X", 100.0, fundamental, ta.RiskMetrics(), 250.0)
    assert low.dcf_status == "measured"
    assert low.dcf_fair_value == pytest.approx(high.dcf_fair_value)


def test_india_beta_uses_local_benchmark_and_normalized_sessions(monkeypatch):
    analyzer = ta.RiskAnalyzer()
    stock_idx = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=40, freq="B"), tz="Asia/Kolkata")
    bench_idx = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=40, freq="B"), tz="America/New_York")
    stock = pd.Series([i / 10_000 for i in range(40)], index=stock_idx)
    bench = pd.Series([i / 20_000 for i in range(40)], index=bench_idx)
    seen = []
    monkeypatch.setattr(analyzer, "_get_benchmark_returns", lambda ticker: (seen.append(ticker) or bench))

    result = analyzer._calculate_beta(stock, ta.RiskMetrics(), "RELIANCE.NS")
    assert seen == ["RELIANCE.NS"]
    assert result.beta_status == "measured"
    assert result.beta_benchmark == "^NSEI"
    assert result.beta_observations == 40


def test_missing_peer_multiples_do_not_receive_valuation_points():
    complete, complete_count = ta.PeerAnalyzer._rank_score({"roe": 10.0, "pe": 20.0, "pb": 2.0})
    missing, missing_count = ta.PeerAnalyzer._rank_score({"roe": 10.0, "pe": 0.0, "pb": 0.0})
    assert complete_count == 3
    assert missing_count == 1
    assert missing is None
    assert complete is not None


def test_serialized_market_intel_keeps_analyst_count():
    result = _analysis_result(FinalAction.BUY.value)
    result.market_intel = ta.MarketIntelligence(analyst_count=17)
    assert ta._mcp_result_to_dict(result)["market_intel"]["analyst_count"] == 17


# --- target provenance / no fabricated reward --------------------------------

def _technical(**over):
    kw = dict(atr=2.0, support_level=90.0, resistance_level=200.0, rsi=50.0)
    kw.update(over)
    return ta.TechnicalIndicators(**kw)


def _valuation(base=0.0, cons=0.0, aggr=0.0):
    v = ta.ValuationMetrics()
    v.target_base, v.target_conservative, v.target_aggressive = base, cons, aggr
    return v


def test_no_valuation_source_publishes_no_target_or_reward_risk():
    setup = ta.TradeSetupGenerator().generate(
        100.0, ta.Signal.BUY, _technical(), ta.RiskMetrics(), 60.0, valuation=None)
    assert setup.has_long_reward is False
    assert setup.target_base == setup.target_conservative == setup.target_aggressive == 0.0
    assert setup.risk_reward_ratio == 0.0
    assert "no valuation blend available" in setup.setup_basis


def test_valuation_target_is_labelled_and_partial_exit_is_tactical():
    setup = ta.TradeSetupGenerator().generate(
        100.0, ta.Signal.BUY, _technical(resistance_level=108.0), ta.RiskMetrics(), 60.0,
        valuation=_valuation(base=140.0, aggr=170.0))
    assert setup.target_base == pytest.approx(140.0)
    assert "12-month valuation target" in setup.setup_basis
    # T1 is a partial exit: between entry and target, and not through resistance.
    assert setup.entry_price <= setup.target_conservative <= 108.0
    assert setup.risk_reward_ratio > 1.5


def test_decision_states_target_basis_horizon_and_method_range():
    result = _analysis_result(FinalAction.BUY.value)
    result.trade_setup.target_base = 130.0
    result.trade_setup.horizon_target = 107.5
    result.valuation = ta.ValuationMetrics(
        target_base=130.0, target_conservative=105.0, target_aggressive=150.0,
        target_sources=["analyst_consensus", "dcf"],
    )
    decision = ta.decision_for_analysis(result)
    levels = decision.to_dict()["levels"]
    # One horizon: the Decision's target is its own holding period; the
    # 12-month valuation level is published beside it as context only.
    assert "pro-rated from the 12-month valuation target" in levels["target_basis"]
    assert levels["target_horizon_days"] == 63
    assert levels["target"] == 107.5
    assert levels["valuation_target"] == 130.0
    assert levels["valuation_target_horizon_days"] == 252
    names = [s.name for s in decision.scenarios]
    assert names == ["low_method", "weighted", "high_method"]
    assert all("analyst consensus" in s.method or "dcf" in s.method for s in decision.scenarios)


# --- missing data is not a measurement --------------------------------------

def test_unreported_fundamentals_do_not_move_the_fundamental_score(monkeypatch):
    generator = ta.SignalGenerator()
    args = (
        ta.TechnicalIndicators(), ta.RiskMetrics(), ta.SentimentData(),
        ta.InsiderData(), ta.MarketIntelligence(), ta.ValuationMetrics(),
    )
    sparse = ta.FundamentalData(reported={"roe": False, "net_margin": False,
                                          "revenue_growth": False, "debt_to_equity": False,
                                          "current_ratio": False})
    scores = generator._calculate_component_scores(args[0], sparse, *args[1:])
    assert scores["fundamental"] == 50          # neutral, not 50 - 10 - 10 ...
    measured_bad = ta.FundamentalData(roe=0.0, net_margin=0.0, revenue_growth_yoy=0.0)
    assert generator._calculate_component_scores(args[0], measured_bad, *args[1:])["fundamental"] < 50


def test_unreported_inputs_are_null_in_the_payload_not_zero():
    result = _analysis_result(FinalAction.BUY.value)
    result.fundamental = ta.FundamentalData(reported={"roe": False, "debt_to_equity": False})
    payload = ta._mcp_result_to_dict(result)["fundamental"]
    assert payload["roe"] is None and payload["debt_to_equity"] is None
    assert payload["reported_inputs"]["roe"] is False


# --- cache serves the same freshness policy ---------------------------------

def test_cached_funded_decision_is_downgraded_once_it_goes_stale():
    from freezegun import freeze_time
    import app as webapp

    payload = {
        "market_timestamp": "2026-09-18",
        "final_action": "BUY",
        "decision": {"action": "BUY", "label": "BUY", "as_of": "2026-09-18",
                     "sizing": {"shares": 15, "recommended_shares": 15, "position_value": 7000.0},
                     "blocking": [], "blocking_codes": [], "transitions": []},
        "position_sizing": {"recommended_shares": 15, "position_value": 7000.0,
                            "portfolio_weight": 0.07, "kelly_optimal": 0.0, "is_viable": True},
        "trade_setup": {"is_actionable_entry": True, "entry_type": "LIMIT"},
    }
    with freeze_time("2026-09-25 18:00:00"):
        webapp._refresh_cached_decision_freshness(payload)
    assert payload["final_action"] == "WATCHLIST"
    assert payload["decision"]["action"] == "WATCHLIST"
    assert payload["decision"]["blocking_codes"] == ["STALE_PRICE_DATA"]
    assert set(payload["decision"]["sizing"].values()) == {0}
    assert payload["position_sizing"]["recommended_shares"] == 0
    assert payload["trade_setup"]["is_actionable_entry"] is False


def test_funded_action_with_no_executable_size_does_not_read_as_an_order():
    # The fixture bar is dated 2026-09-25. Pin the clock to the next day so the
    # bar stays the latest completed session and STALE_PRICE_DATA cannot mask
    # the SIZE_NOT_EXECUTABLE code this test is about.
    from freezegun import freeze_time
    with freeze_time("2026-09-26 16:00:00"):
        _funded_action_with_no_executable_size()


def _funded_action_with_no_executable_size():
    result = _analysis_result(FinalAction.BUY.value)
    result.position_sizing.recommended_shares = 0
    result.position_sizing.position_value = 0.0
    result.signal = ta.Signal.BUY
    result.catalysts = ta.CatalystData()
    items = ta.QuantaEliteAnalyzer._generate_action_items(object.__new__(ta.QuantaEliteAnalyzer), result)
    assert not any("limit buy" in i.lower() for i in items)
    assert any("Do not place an order" in i for i in items)
    decision = ta.decision_for_analysis(result)
    assert "SIZE_NOT_EXECUTABLE" in decision.blocking_codes


# --- action contract: stance / new money / holder action / primary blocker ---

def test_blocked_bullish_call_publishes_its_contract():
    from engine import DecisionInputs, resolve_final_action
    res = resolve_final_action(DecisionInputs(
        ticker="X", composite_score=68.0, risk_grade="F", risk_reward_ratio=3.0,
        current_price=100.0, stop_loss=92.0, target_price=130.0))
    assert res.final_action is FinalAction.WATCHLIST
    c = res.contract
    # Risk grade F: no new money, and a holder should review size (TRIM).
    assert (c["model_stance"], c["new_money"], c["holder_action"]) == ("BULLISH", "WATCH", "TRIM")
    assert c["primary_blocker"]["code"] == "RISK_GRADE_F"
    assert c["primary_blocker"]["remedy"]


def test_neutral_and_bearish_scores_no_longer_share_one_label():
    from engine import DecisionInputs, resolve_final_action
    kw = dict(ticker="X", risk_grade="B", risk_reward_ratio=3.0,
              current_price=100.0, stop_loss=92.0, target_price=130.0)
    neutral = resolve_final_action(DecisionInputs(composite_score=50.0, **kw))
    trim = resolve_final_action(DecisionInputs(composite_score=40.0, **kw))
    exit_ = resolve_final_action(DecisionInputs(composite_score=30.0, **kw))
    assert neutral.final_action is FinalAction.WATCHLIST
    assert neutral.contract["primary_blocker"]["code"] == "HOLD_NOT_OWNED"
    assert trim.final_action is FinalAction.AVOID and trim.contract["holder_action"] == "TRIM"
    # A bearish score alone never says "exit" to a holder; a measured red flag must agree.
    assert exit_.final_action is FinalAction.AVOID and exit_.contract["holder_action"] == "TRIM"
    assert trim.contract["new_money"] == "AVOID"
    from engine.blockers import holder_action
    assert holder_action(FinalAction.AVOID, 30.0, ["HEAVY_INSIDER_SELLING"]) == "EXIT"
    assert holder_action(FinalAction.AVOID, 30.0, ["TARGET_BELOW_ENTRY"]) == "EXIT"
    assert holder_action(FinalAction.AVOID, 40.0, ["TARGET_BELOW_ENTRY"]) == "TRIM"


def test_research_decision_carries_the_contract_and_every_blocker():
    result = _analysis_result(FinalAction.WATCHLIST.value)
    result.scores = ta.ScoreBreakdown(composite_score=67.0)
    result.final_action_codes = ["RISK_GRADE_F", "LOW_REWARD_RISK"]
    d = ta.decision_for_analysis(result).to_dict()
    assert d["model_stance"] == "BULLISH" and d["new_money"] == "WATCH"
    assert d["holder_action"] == "TRIM" and d["holdings_known"] is False
    assert d["primary_blocker"]["code"] == "RISK_GRADE_F"
    assert [b["code"] for b in d["blockers"]] == ["RISK_GRADE_F", "LOW_REWARD_RISK"]
    assert all(b["text"] and b["category"] for b in d["blockers"])


def test_generator_downgrade_is_attributed_to_its_rule(monkeypatch):
    generator = ta.SignalGenerator()
    monkeypatch.setattr(generator, "_calculate_component_scores", lambda *a: {
        k: 70.0 for k in ("technical", "fundamental", "valuation", "quality", "sentiment", "risk")})
    monkeypatch.setattr(generator, "_count_confirmations", lambda *a: (5, 0))
    mi = ta.MarketIntelligence(target_mean=80.0)          # consensus 20% below price
    val = ta.ValuationMetrics(target_base=120.0)
    sig, _c, _s, expl = generator.generate(
        ta.TechnicalIndicators(), ta.FundamentalData(), ta.RiskMetrics(risk_grade="B"),
        ta.SentimentData(), ta.InsiderData(), mi, val, None, None, 100.0)
    assert sig == ta.Signal.HOLD
    assert "ANALYST_CONSENSUS_DOWNSIDE" in expl.override_codes


def test_holder_guidance_follows_the_published_holder_action():
    result = _analysis_result(FinalAction.AVOID.value)
    result.signal = ta.Signal.SELL
    result.scores = ta.ScoreBreakdown(composite_score=40.0)
    result.risk = ta.RiskMetrics(risk_grade="B")
    result.technical = ta.TechnicalIndicators()
    result.fundamental = ta.FundamentalData()
    g = ta._compute_beginner_guidance(result)
    assert g["existing_holder"] == "Trim into strength; keep the stop"


# --- no fabricated levels or targets -----------------------------------------

def test_no_swing_high_means_no_resistance_not_spot_plus_3pct():
    n = 80
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    closes = [100 + i for i in range(n)]                  # monotone rise: no swing high above
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                       "Volume": 1_000_000}, index=idx)
    tech = ta.TechnicalAnalyzer().analyze(df)
    assert tech.resistance_level == 0.0


def test_technical_level_alone_is_not_a_valuation_target():
    out = ta.ValuationAnalyzer().calculate_composite_targets(
        100.0, ta.ValuationMetrics(), ta.MarketIntelligence(), ta.MonteCarloResults(),
        ta.TechnicalIndicators(resistance_level=104.0))
    assert out.target_base == 0.0 and out.target_source_count == 0


def test_fcf_dcf_is_not_applicable_to_an_insurer_or_bank():
    fund = ta.FundamentalData(fcf_per_share=40_000.0, eps_ttm=60_000.0, pe_ratio=13.0,
                              revenue_growth_yoy=5.0, book_value_per_share=500_000.0)
    fund._industry = "Insurance - Diversified"
    v = ta.ValuationAnalyzer().analyze("BRK-A", 756_000.0, fund, ta.RiskMetrics())
    assert v.dcf_status == "not_applicable" and v.dcf_fair_value == 0.0


def test_implausible_graham_number_is_rejected():
    # Per-A-share EPS/book on a B share: Graham ~43x the price.
    fund = ta.FundamentalData(eps_ttm=30_000.0, book_value_per_share=700_000.0, pe_ratio=15.0,
                              revenue_growth_yoy=5.0)
    fund._industry = "Insurance - Diversified"
    v = ta.ValuationAnalyzer().analyze("BRK-B", 504.0, fund, ta.RiskMetrics())
    assert v.graham_status == "rejected" and v.graham_number == 0.0
    assert v.valuation_status == "unavailable"


def test_dcf_fair_value_carries_no_margin_of_safety_haircut(monkeypatch):
    fund = ta.FundamentalData(fcf_per_share=5.0, eps_ttm=6.0, pe_ratio=18.0, revenue_growth_yoy=6.0)
    base = ta.ValuationAnalyzer().analyze("X", 100.0, fund, ta.RiskMetrics()).dcf_fair_value
    monkeypatch.setattr(ta.Config, "DCF_MARGIN_OF_SAFETY", 0.5)
    assert ta.ValuationAnalyzer().analyze("X", 100.0, fund, ta.RiskMetrics()).dcf_fair_value == \
        pytest.approx(base)


def test_shrinking_revenue_is_not_projected_at_five_percent_growth():
    def dcf(growth, reported=True):
        fund = ta.FundamentalData(fcf_per_share=5.0, eps_ttm=6.0, pe_ratio=18.0,
                                  revenue_growth_yoy=growth,
                                  reported={"revenue_growth": reported})
        return ta.ValuationAnalyzer().analyze("X", 100.0, fund, ta.RiskMetrics())
    assert dcf(-10.0).dcf_fair_value == pytest.approx(dcf(2.0).dcf_fair_value)
    assert dcf(-10.0).dcf_fair_value < dcf(5.0).dcf_fair_value
    unreported = dcf(0.0, reported=False)
    assert unreported.dcf_status == "unavailable" and "growth_not_reported" in unreported.dcf_method


def test_a_dcf_too_low_to_value_with_cannot_veto_either():
    fund = ta.FundamentalData(fcf_per_share=1.0, eps_ttm=4.0, pe_ratio=24.0, revenue_growth_yoy=3.0)
    v = ta.ValuationAnalyzer().analyze("KO", 89.0, fund, ta.RiskMetrics())
    assert v.dcf_status == "unavailable" and v.dcf_upside == 0.0


def test_fast_preview_uses_the_consensus_already_in_the_fundamentals_payload():
    analyzer = object.__new__(ta.QuantaEliteAnalyzer)
    fund = ta.FundamentalData(consensus={"target_mean": 120.0, "target_high": 140.0,
                                         "target_low": 100.0, "target_median": 118.0,
                                         "analyst_count": 12})
    analyzer.technical_analyzer = SimpleNamespace(analyze=lambda d: ta.TechnicalIndicators())
    analyzer.risk_analyzer = SimpleNamespace(analyze=lambda d, t: ta.RiskMetrics())
    analyzer.fundamental_analyzer = SimpleNamespace(analyze=lambda t: fund)
    analyzer.valuation_analyzer = ta.ValuationAnalyzer()
    result = ta.AnalysisResult(ticker="X")
    result.current_price = 100.0
    idx = pd.date_range("2024-01-01", periods=260, freq="B")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)
    out = analyzer._run_parallel_analysis("X", df, result, fast_mode=True)
    assert out.market_intel.target_mean == 120.0 and out.market_intel.analyst_count == 12
    assert out.market_intel.implied_upside == pytest.approx(20.0)
    assert out.sentiment.company_sentiment_score() == 0.0   # still not fetched
