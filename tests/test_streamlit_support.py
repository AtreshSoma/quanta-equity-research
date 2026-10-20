from dataclasses import dataclass
from pathlib import Path
import sys
import types

import pytest


@dataclass
class FakeRecommendation:
    ticker: str = "AAPL"
    company: str = "Apple Inc."
    signal: str = "BUY"
    final_action: str = "ACCUMULATE"
    overall_score: float = 7.4
    entry_price: float = 100.0
    price_target: float = 125.0
    stop_loss: float = 92.0
    risk_reward_ratio: float = 3.1
    confidence_tier: str = "HIGH"
    _cached_earnings_days: int = -1


class FakeSystem:
    def __init__(self, capital=10_000, market="US"):
        self.capital = capital
        self.market = market
        self.calls = []
        self.recommendation = FakeRecommendation()

    def build_stock_universe(self, scan_type):
        self.calls.append(("build_stock_universe", scan_type))
        return ["AAPL", "MSFT"]

    def scan_market(self, scan_type, custom_tickers):
        self.calls.append(("scan_market", scan_type, custom_tickers))
        return [self.recommendation]

    def get_top_recommendations(self, max_positions, diversify_sectors):
        self.calls.append(
            ("get_top_recommendations", max_positions, diversify_sectors)
        )
        return [self.recommendation]

    def analyze_stock(self, ticker, research_mode=False):
        self.calls.append(("analyze_stock", ticker, research_mode))
        self.recommendation.ticker = ticker
        return self.recommendation

    def generate_smart_rationale(self, recommendation):
        return {"full_analysis": "[bold cyan]WHY NOW[/]\nA sound setup."}


def fake_deep_payload():
    return {
        "ticker": "LRCX",
        "company_name": "Lam Research Corporation",
        "sector": "Technology",
        "industry": "Semiconductor Equipment",
        "current_price": 314.28,
        "day_change_pct": -2.08,
        "final_action": "WATCHLIST",
        "recommendation_label": "HOLD EXISTING / AVOID NEW BUY",
        "confidence_score": 32.2,
        "scores": {
            "technical_score": 40,
            "fundamental_score": 91,
            "valuation_score": 60,
            "quality_score": 100,
            "sentiment_score": 65,
            "risk_score": 20,
            "composite_score": 57.2,
        },
        "beginner_guidance": {
            "recommendation_label": "HOLD EXISTING / AVOID NEW BUY",
            "buy_now": "NO",
            "new_buyer": "Avoid new buy; watchlist only",
            "existing_holder": "Hold existing with strict risk controls",
            "main_reason": "Strong fundamentals, but weak technical confirmation and high risk.",
            "data_quality": "PASS",
        },
        "portfolio_outlook": {
            "bottom_line": "New buyers should wait; existing holders should manage risk.",
        },
        "technical": {
            "rsi": 42.3,
            "macd_signal": "BEARISH",
            "trend_direction": "SIDEWAYS",
            "trend_strength": "WEAK",
            "volume_ratio": 0.34,
            "support_level": 313.10,
            "resistance_level": 333.10,
        },
        "fundamental": {
            "pe_ratio": 59.3,
            "forward_pe": 38.9,
            "roe": 66.8,
            "net_margin": 30.9,
            "revenue_growth_yoy": 23.8,
            "eps_growth_yoy": 40.8,
            "debt_to_equity": 0.35,
            "current_ratio": 2.54,
        },
        "risk": {
            "annualized_volatility": 0.554,
            "beta": 3.0,
            "max_drawdown": -0.39,
            "risk_grade": "F",
        },
        "valuation": {
            "dcf_fair_value": 345.60,
            "dcf_upside": 10.0,
            "target_conservative": 318.19,
            "target_base": 353.54,
            "target_aggressive": 406.57,
        },
        "market_intel": {
            "target_low": 220.0,
            "target_mean": 366.48,
            "target_high": 500.0,
            "next_earnings_date": "2026-07-29",
        },
        "thesis": {
            "bull_case": ["Strong profitability", "Growing revenue"],
            "bear_case": ["High volatility", "Weak technical confirmation"],
            "catalysts": ["Earnings in 12 days"],
        },
        "risk_warnings": ["HIGH VOLATILITY"],
        "catalysts": {
            "earnings_date": "2026-07-29",
            "days_to_earnings": 12,
        },
        "currency_symbol": "$",
        "data_provider": "yfinance",
        "data_quality_status": "ok",
        "market_timestamp": "2026-07-17T00:00:00-04:00",
        "retrieval_timestamp": "2026-07-17T15:41:16+00:00",
    }


class FakeDeepAnalyzer:
    calls = []

    def analyze(self, ticker, account_size, fast_mode):
        self.calls.append((ticker, account_size, fast_mode))
        return fake_deep_payload()


def test_market_scan_uses_current_engine_api():
    from streamlit_support import run_market_scan

    system = FakeSystem()
    tickers, results, recommendations = run_market_scan(
        system, "STANDARD", max_positions=5, diversify_sectors=True
    )

    assert tickers == ["AAPL", "MSFT"]
    assert results == [system.recommendation]
    assert recommendations == [system.recommendation]
    assert system.calls == [
        ("build_stock_universe", "standard"),
        ("scan_market", "standard", ["AAPL", "MSFT"]),
        ("get_top_recommendations", 5, True),
    ]


def test_recommendation_presentation_uses_current_fields():
    from streamlit_support import (
        confidence_label,
        recommendation_action,
        recommendation_row,
        upside_percent,
    )

    recommendation = FakeRecommendation()
    row = recommendation_row(recommendation, "$")

    assert recommendation_action(recommendation) == "ACCUMULATE"
    assert confidence_label(recommendation) == "HIGH"
    assert upside_percent(recommendation) == 25.0
    assert row["Score"] == "7.4"
    assert row["Confidence"] == "HIGH"
    assert "Win" not in row


def test_upside_handles_zero_entry_and_rationale_strips_rich_markup():
    from streamlit_support import plain_rationale, upside_percent

    recommendation = FakeRecommendation(entry_price=0.0)
    assert upside_percent(recommendation) is None
    assert plain_rationale(FakeSystem(), recommendation) == "WHY NOW\nA sound setup."


def test_single_stock_analysis_includes_event_risk_with_guardrails():
    from streamlit_support import earnings_risk_message, run_single_stock_analysis

    system = FakeSystem()
    result = run_single_stock_analysis(system, " lrcx ")

    assert result.ticker == "LRCX"
    assert system.calls == [("analyze_stock", "LRCX", True)]
    result._cached_earnings_days = 12
    assert "research-only" in earnings_risk_message(result)


def test_deep_advisor_report_is_clear_and_factually_labeled(monkeypatch):
    from streamlit_support import build_advisor_report, run_deep_stock_analysis

    fake_deep = types.ModuleType("TradingAnalysis_ELITE")
    fake_deep.QuantaEliteAnalyzer = FakeDeepAnalyzer
    fake_deep._mcp_result_to_dict = lambda result: result
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", fake_deep)
    FakeDeepAnalyzer.calls = []

    data = run_deep_stock_analysis(" lrcx ", 10_000)
    report = build_advisor_report(data)

    assert FakeDeepAnalyzer.calls == [("LRCX", 10_000.0, False)]
    assert report["action"] == "WATCHLIST"
    assert report["evidence_label"] == "LOW"
    assert any("ROE 66.8%" in item for item in report["fundamental_points"])
    assert all("ROE 0.7%" not in item for item in report["fundamental_points"])
    assert any("observed past decline" in item for item in report["risk_points"])
    assert any("not a probability of profit" in item for item in report["disclosures"])
    assert any("Internal scenario range" in item for item in report["valuation_points"])
    assert any("External analyst mean target" in item for item in report["valuation_points"])


def test_watchlist_rationale_never_contains_buy_instructions():
    from SP_ELITE_PRO import QuantaEliteTradingSystem, StockAnalysis

    system = object.__new__(QuantaEliteTradingSystem)
    system.currency_symbol = "$"
    recommendation = StockAnalysis(
        ticker="LRCX",
        company="Lam Research Corporation",
        sector="Technology",
        industry="Semiconductor Equipment",
        signal="HOLD",
        final_action="WATCHLIST",
        final_action_reasons=["Earnings event risk"],
        entry_price=309.12,
        price_target=398.04,
        stop_loss=289.69,
        risk_reward_ratio=4.4,
        allocation=0.0,
        shares=0,
        position_pct=0.0,
        _cached_earnings_days=12,
    )

    rationale = system.generate_smart_rationale(recommendation)["full_analysis"]

    assert "Do not open a new position" in rationale
    assert "REFERENCE LEVELS (NOT AN ORDER)" in rationale
    assert "Patient accumulation" not in rationale
    assert "Shares to Buy" not in rationale


@pytest.mark.streamlit
def test_streamlit_page_and_both_actions_render_without_exceptions(monkeypatch):
    # Streamlit is the optional `ui` extra. A missing optional dependency is an
    # install problem, so report it as a skip instead of a red test that hides
    # whether anything actually regressed.
    pytest.importorskip("streamlit", reason="needs the .[ui] extra (streamlit)")
    from streamlit.testing.v1 import AppTest

    fake_engine = types.ModuleType("SP_ELITE_PRO")
    fake_engine.QuantaEliteTradingSystem = FakeSystem
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", fake_engine)
    fake_deep = types.ModuleType("TradingAnalysis_ELITE")
    fake_deep.QuantaEliteAnalyzer = FakeDeepAnalyzer
    fake_deep._mcp_result_to_dict = lambda result: result
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", fake_deep)

    app_path = Path(__file__).parents[1] / "core" / "streamlit_app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=10)
    assert not app.exception

    next(b for b in app.button if b.label == "Run market scan").click().run(timeout=10)
    assert not app.exception
    assert any("Found 1 opportunities" in item.value for item in app.success)

    app.text_input[0].input("AAPL")
    next(b for b in app.button if b.label == "Build stock brief").click().run(timeout=10)
    assert not app.exception
    assert any("Analysis complete for AAPL" in item.value for item in app.success)
    assert any(item.label == "Action" and "AVOID NEW BUY" in item.value for item in app.metric)
    markdown = "\n".join(item.value for item in app.markdown)
    assert "Advisor Summary" in markdown
    assert "Why the model reached this conclusion" in markdown


def test_unvalidated_buy_keeps_its_name_and_is_flagged_as_unproven():
    from streamlit_support import display_label, is_unproven_buy, market_recommendation_record

    untested = {"status": "UNTESTED", "permits_recommendation": False}
    validated = {"status": "VALIDATED", "permits_recommendation": True}
    assert display_label("ACCUMULATE", evidence=untested) == "ACCUMULATE"
    assert is_unproven_buy("ACCUMULATE", untested)
    assert not is_unproven_buy("ACCUMULATE", validated)
    assert not is_unproven_buy("WATCHLIST", untested)
    # A published table row never shows a bare buy-tier call from an unproven model.
    row = market_recommendation_record(FakeRecommendation(), evidence=untested)
    assert row["Action"] == "ACCUMULATE · unproven"
