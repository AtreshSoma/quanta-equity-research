"""Deterministic AAPL fixture trace through the recommendation stack."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import TradingAnalysis_ELITE as ta  # noqa: E402
from engine import DecisionInputs, resolve_final_action  # noqa: E402


def _run_aapl_trace():
    # Declared in the `dev` extra. Skip rather than fail so a missing engine
    # reads as "install the extra", not "the trace regressed".
    pytest.importorskip("pyarrow", reason="needs the .[dev] extra (pyarrow)")
    # The price cache is downloaded market data and is not shipped with the
    # repository (provider terms restrict redistribution). Populate it with
    # `python -m core.apex_quant.run` to enable this trace.
    cache = CORE / "apex_quant" / "_cache"
    if not (cache / "AAPL.parquet").exists() or not (cache / "SPY.parquet").exists():
        pytest.skip("price cache not present; run apex_quant to download it")
    prices = pd.read_parquet(cache / "AAPL.parquet")
    spy = pd.read_parquet(cache / "SPY.parquet")

    technical = ta.TechnicalAnalyzer().analyze(prices)
    risk_analyzer = ta.RiskAnalyzer()
    risk_analyzer._get_spy_returns = lambda: spy["Close"].pct_change().dropna()
    risk = risk_analyzer.analyze(prices, "AAPL")

    fundamental = ta.FundamentalData(
        roe=156.0, net_margin=25.3, revenue_growth_yoy=-2.8,
        debt_to_equity=1.79, current_ratio=0.99,
        piotroski_f_score=6, quality_score=70.0, market_cap=2.99e12,
    )
    sentiment = ta.SentimentData(
        sentiment_score=0.10, weighted_sentiment_score=0.10,
        provenance_computed=True, has_direct_news=True,
        sentiment_source="company",
    )
    market_intel = ta.MarketIntelligence(
        analyst_rating="BUY", target_mean=200.0, analyst_count=25,
        buy_ratings=24, hold_ratings=8, sell_ratings=2,
    )
    valuation = ta.ValuationMetrics(dcf_fair_value=175.0, valuation_score=45.0,
                                    dcf_status="measured", valuation_status="measured",
                                    fair_value_estimate=175.0, fair_value_sources=["dcf"])

    monte_carlo = ta.MonteCarloSimulator().simulate(
        float(prices["Close"].iloc[-1]), risk, days=30, simulations=3000,
        regime=ta.MarketRegime.SIDEWAYS, annual_drift_cap=0.10,
        seed=20231229,
    )
    valuation = ta.ValuationAnalyzer().calculate_composite_targets(
        float(prices["Close"].iloc[-1]), valuation, market_intel,
        monte_carlo, technical,
    )
    signal, confidence, scores, _ = ta.SignalGenerator().generate(
        technical, fundamental, risk, sentiment, ta.InsiderData(),
        market_intel, valuation, ta.MarketRegime.SIDEWAYS, monte_carlo,
        float(prices["Close"].iloc[-1]),
    )
    setup = ta.TradeSetupGenerator().generate(
        float(prices["Close"].iloc[-1]), signal, technical, risk, confidence
    )
    sizing = ta.PositionSizer().calculate(
        signal, setup.entry_price, setup.stop_loss, confidence, risk,
        account_size=100_000, monte_carlo=monte_carlo, technical=technical,
    )
    decision = resolve_final_action(DecisionInputs(
        ticker="AAPL", composite_score=scores.composite_score,
        risk_grade=risk.risk_grade, risk_reward_ratio=setup.risk_reward_ratio,
        current_price=float(prices["Close"].iloc[-1]),
        stop_loss=setup.stop_loss, target_price=setup.target_base,
        has_existing_position=False,
    ))
    return prices, technical, risk, monte_carlo, valuation, scores, setup, sizing, decision


def test_fixed_aapl_trace_is_reproducible_and_internally_consistent():
    first = _run_aapl_trace()
    second = _run_aapl_trace()
    prices, technical, risk, mc, valuation, scores, setup, sizing, decision = first

    assert prices.index.max() == pd.Timestamp("2023-12-29")
    assert float(prices["Close"].iloc[-1]) == 192.52999877929688
    assert technical.sma_200 > 0 and technical.atr > 0
    assert 0 < risk.annualized_volatility < 1
    assert risk.max_drawdown < 0
    assert mc.median_price == second[3].median_price
    assert valuation.target_conservative < valuation.target_base < valuation.target_aggressive
    assert 0 <= scores.composite_score <= 100

    if decision.allocation_eligible:
        assert 0 < setup.stop_loss < setup.entry_price < setup.target_base
        assert setup.risk_reward_ratio >= ta.Config.MIN_RISK_REWARD
        assert sizing.position_value <= 100_000 * sizing.max_portfolio_weight + setup.entry_price
    else:
        assert sizing.position_value == 0 or not decision.final_action.allocates_new_capital
