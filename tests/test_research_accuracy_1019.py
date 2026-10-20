"""Research engine accuracy fixes, 2026-10-19 review.

1. Extended RSI no longer prices reward-to-risk and size from a pullback entry
   below spot: the entry the R:R and the position size use is the current
   price, and the pullback is offered as an optional better fill.
2. India listings (.NS / .BO) carry the statutory levies (STT, stamp duty,
   exchange and SEBI fees, GST) in the round-trip cost, not the US-only spread
   and slippage.
3. During the session the relative-volume reading compares the last COMPLETED
   session with the 20-session average, not today's partial volume.
4. Return-distribution statistics (volatility, VaR, liquidity, risk grade)
   exclude today's still-forming bar.
6. A failed RSI / ADX / correlation is reported as missing, not as a neutral
   reading (RSI 50, ADX 25, correlation 0.3).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import TradingAnalysis_ELITE as ta  # noqa: E402
from engine import thresholds as T  # noqa: E402

# 2026-10-19 is a Monday (a US and an NSE session).
NY_10AM = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)      # 10:00 New York
NY_AFTER_CLOSE = datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc)  # 16:30 New York
IST_10AM = datetime(2026, 10, 19, 4, 30, tzinfo=timezone.utc)      # 10:00 IST
IST_AFTER_CLOSE = datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc)  # 16:00 IST


def _setup(rsi=50.0, ticker="", atr=2.0):
    val = ta.ValuationMetrics(target_base=130.0, target_aggressive=140.0)
    tech = ta.TechnicalIndicators(atr=atr, support_level=0.0, resistance_level=0.0, rsi=rsi)
    return ta.TradeSetupGenerator().generate(100.0, ta.Signal.BUY, tech, ta.RiskMetrics(), 60.0,
                                             valuation=val, ticker=ticker)


# --- 1. reward-to-risk is measured from an entry a market buyer can get -------

def _rr_from(entry, ts, cost_pct):
    cost = entry * cost_pct / 100.0
    reward = max(0.0, ts.horizon_target - entry - cost)
    risk = entry - ts.stop_loss + cost
    return reward / risk


def test_extended_rsi_entry_is_spot_not_a_pullback():
    """RSI 72: the entry used for R:R and sizing is the current price, so the
    published R:R is what a buyer at market gets; the pullback is optional."""
    ts = _setup(rsi=72.0)
    assert ts.entry_price == pytest.approx(100.0)
    cost_pct = ta.TradeSetupGenerator.round_trip_cost_pct()
    assert ts.risk_reward_ratio == pytest.approx(_rr_from(100.0, ts, cost_pct), rel=1e-9)
    pull = 100.0 * (1 - T.RESEARCH.PULLBACK_ENTRY_PCT / 100.0)
    assert any(f"{pull:.2f}" in c for c in ts.entry_conditions)
    assert "spot" in ts.setup_basis


def test_extended_rsi_rr_is_never_better_than_buying_at_spot():
    for rsi in (55.0, 61.0, 72.0, 85.0):
        ts = _setup(rsi=rsi)
        at_spot = _rr_from(100.0, ts, ta.TradeSetupGenerator.round_trip_cost_pct())
        assert ts.risk_reward_ratio <= at_spot + 1e-9, rsi


# --- 2. India statutory costs -------------------------------------------------

def test_india_round_trip_cost_adds_statutory_levies():
    us = ta.TradeSetupGenerator.round_trip_cost_pct("AAPL")
    for sym in ("RELIANCE.NS", "TCS.BO", "infy.ns"):
        india = ta.TradeSetupGenerator.round_trip_cost_pct(sym)
        assert india == pytest.approx(us + 2 * T.LEVELS.RESEARCH_INDIA_STATUTORY_PER_SIDE * 100)
    assert T.LEVELS.RESEARCH_INDIA_STATUTORY_PER_SIDE == T.COSTS.INDIA_STATUTORY_COST_PER_SIDE
    assert ta.TradeSetupGenerator.round_trip_cost_pct() == pytest.approx(us)


def test_india_reward_to_risk_is_net_of_statutory_costs():
    us, india = _setup(ticker="AAPL"), _setup(ticker="RELIANCE.NS")
    assert india.entry_price == us.entry_price and india.stop_loss == us.stop_loss
    extra = us.entry_price * 2 * T.COSTS.INDIA_STATUTORY_COST_PER_SIDE
    assert us.reward_amount - india.reward_amount == pytest.approx(extra)
    assert india.risk_amount - us.risk_amount == pytest.approx(extra)
    assert india.risk_reward_ratio < us.risk_reward_ratio


# --- shared synthetic bars ------------------------------------------------------

def _bars(tz="America/New_York", n=300, last_volume_frac=0.20, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-19", periods=n, tz=tz)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.015, n))
    vol = rng.integers(900_000, 1_100_000, n).astype(float)
    vol[-1] = vol[:-1][-20:].mean() * last_volume_frac      # a 10 AM partial session
    close[-1] = close[-2] * 1.001
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": vol}, index=idx)


# --- 3. relative volume against a completed session ---------------------------

def test_live_bar_detection_us_and_india():
    us, india = _bars(), _bars(tz="Asia/Kolkata")
    assert ta._last_bar_is_live(us, "AAPL", now=NY_10AM)
    assert not ta._last_bar_is_live(us, "AAPL", now=NY_AFTER_CLOSE)
    assert ta._last_bar_is_live(india, "RELIANCE.NS", now=IST_10AM)
    assert not ta._last_bar_is_live(india, "RELIANCE.NS", now=IST_AFTER_CLOSE)
    # The market is read from the bar index when no ticker is given.
    assert ta._last_bar_is_live(india, "", now=IST_10AM)
    assert not ta._last_bar_is_live(india, "", now=IST_AFTER_CLOSE)
    # Yesterday's bar is never live.
    assert not ta._last_bar_is_live(us.iloc[:-1], "AAPL", now=NY_10AM)


def test_intraday_volume_ratio_uses_the_last_completed_session():
    df = _bars()
    tech = ta.TechnicalAnalyzer().analyze(df, now=NY_10AM)
    done = df["Volume"].iloc[:-1]
    expected = done.iloc[-1] / done.rolling(20).mean().iloc[-1]
    assert tech.volume_ratio == pytest.approx(expected)
    assert tech.volume_ratio > 0.5            # not the partial-session 0.20x
    assert "completed" in tech.volume_ratio_basis


def test_after_the_close_volume_ratio_uses_today():
    df = _bars()
    tech = ta.TechnicalAnalyzer().analyze(df, now=NY_AFTER_CLOSE)
    assert tech.volume_ratio == pytest.approx(
        df["Volume"].iloc[-1] / df["Volume"].rolling(20).mean().iloc[-1])


# --- 4. history statistics exclude the partial bar ----------------------------

def _risk(df, now, monkeypatch):
    ra = ta.RiskAnalyzer()
    rng = np.random.default_rng(9)
    bench = pd.Series(rng.normal(0.0004, 0.01, len(df)), index=df.index)
    monkeypatch.setattr(ra, "_get_benchmark_returns", lambda ticker: bench)
    return ra.analyze(df, "SYNTH", now=now)


def test_risk_statistics_exclude_the_partial_bar(monkeypatch):
    df = _bars()
    df.iloc[-1, df.columns.get_loc("Close")] = df["Close"].iloc[-2] * 0.80   # a live -20% print
    live = _risk(df, NY_10AM, monkeypatch)
    completed = _risk(df.iloc[:-1], NY_10AM, monkeypatch)
    for field in ("daily_volatility", "var_95_daily", "cvar_95", "skewness", "avg_volume",
                  "overall_risk_score", "risk_grade", "beta"):
        assert getattr(live, field) == getattr(completed, field), field
    after_close = _risk(df, NY_AFTER_CLOSE, monkeypatch)
    assert after_close.daily_volatility != completed.daily_volatility


# --- 6. failures are missing, not neutral ---------------------------------------

def test_failed_rsi_and_adx_are_missing_not_neutral():
    short = _bars(n=10)
    tech = ta.TechnicalIndicators()
    an = ta.TechnicalAnalyzer()
    tech = an._calculate_momentum(short["Close"], short["High"], short["Low"], tech)
    tech = an._calculate_trend(short["Close"], short["High"], short["Low"], tech)
    assert not tech.has("rsi") and not tech.has("adx")
    r = ta.AnalysisResult()
    r.ticker = "SYNTH"
    r.technical = tech
    d = ta._mcp_result_to_dict(r)
    assert d["technical"]["rsi"] is None and d["technical"]["adx"] is None
    json.dumps(d, default=str)


def test_measured_rsi_and_adx_are_published():
    tech = ta.TechnicalAnalyzer().analyze(_bars(), now=NY_AFTER_CLOSE)
    assert tech.has("rsi") and tech.has("adx")
    r = ta.AnalysisResult()
    r.ticker = "SYNTH"
    r.technical = tech
    d = ta._mcp_result_to_dict(r)
    assert d["technical"]["rsi"] == pytest.approx(tech.rsi, abs=1e-3)


def test_missing_rsi_counts_against_data_coverage():
    """A technical leg resting on a placeholder RSI is not counted as a
    measured data source."""
    scores = {"technical": 68.0, "fundamental": 61.0, "valuation": 55.0,
              "quality": 50.0, "sentiment": 50.0}
    count = ta.SignalGenerator._data_sources
    assert count(scores, ta.TechnicalIndicators()) == 3
    assert count(scores, ta.TechnicalIndicators(unmeasured=["rsi"])) == 2
    assert count(scores, ta.TechnicalIndicators(unmeasured=["adx"])) == 2
    # A placeholder RSI moves no points itself; the composite still reads neutral.
    gen = ta.SignalGenerator()
    args = (ta.FundamentalData(), ta.RiskMetrics(), ta.SentimentData(), ta.InsiderData(),
            ta.MarketIntelligence(), ta.ValuationMetrics())
    out = gen._calculate_component_scores(ta.TechnicalIndicators(rsi=50.0, unmeasured=["rsi"]), *args)
    assert out["technical"] == gen._calculate_component_scores(ta.TechnicalIndicators(), *args)["technical"]


def test_failed_correlation_is_missing_not_0_3():
    class _Broken:
        def get_historical(self, *_a, **_k):
            raise RuntimeError("provider down")

    mgr = ta.PortfolioExposureManager(api_client=_Broken())
    assert mgr._get_pairwise_correlation("AAA", "BBB") is None
    # The VaR matrix claims no diversification it did not measure.
    m = mgr._get_correlation_matrix(["AAA", "BBB"])
    assert m[0, 1] == 1.0
    check = mgr.check_position_limits(
        {"ticker": "AAA", "sector": "Tech", "value": 1_000},
        [{"ticker": "BBB", "sector": "Health", "value": 1_000}], 100_000)
    assert check["correlation_unmeasured"] == 1
    assert any("not measured" in w for w in check["warnings"])
