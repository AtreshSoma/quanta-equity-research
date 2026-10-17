"""Regression tests for SP_ELITE_PRO production-safety hardening."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


CORE = Path(__file__).resolve().parents[1] / "core"
MODULE_PATH = CORE / "SP_ELITE_PRO.py"

if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

if "SP_ELITE_PRO" in sys.modules:
    elite = sys.modules["SP_ELITE_PRO"]
else:
    spec = importlib.util.spec_from_file_location("SP_ELITE_PRO", MODULE_PATH)
    assert spec and spec.loader
    elite = importlib.util.module_from_spec(spec)
    sys.modules["SP_ELITE_PRO"] = elite
    spec.loader.exec_module(elite)


def price_history(rows: int = 200, *, end=None) -> pd.DataFrame:
    # Anchor on a business day: with a weekend `end` this pandas returns
    # rows - 1 dates, so the fixture failed every Saturday and Sunday.
    end = pd.offsets.BDay().rollback(end or pd.Timestamp.now().normalize())
    index = pd.bdate_range(end=end, periods=rows)
    close = np.linspace(100.0, 120.0, rows)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_200_000, rows),
        },
        index=index,
    )


class TestMarketDataValidation:
    def test_rejects_nonpositive_price_invalid_ohlc_and_negative_volume(self):
        hist = price_history()
        hist.iloc[-1, hist.columns.get_loc("Close")] = 0.0
        hist.iloc[-2, hist.columns.get_loc("High")] = hist.iloc[-2]["Low"] - 1.0
        hist.iloc[-3, hist.columns.get_loc("Volume")] = -1.0

        result = elite.DataValidator.validate_history(hist, "BAD")

        assert not result.is_valid
        assert any("non-positive" in error for error in result.errors)
        assert any("invalid OHLC" in error for error in result.errors)
        assert any("Volume" in error for error in result.errors)

    def test_rejects_probable_unadjusted_split(self):
        """A real unadjusted split scales price down AND volume up by the same factor."""
        hist = price_history()
        row = hist.index[-1]
        prev_close = float(hist.iloc[-2]["Close"])
        prev_volume = float(hist.iloc[-2]["Volume"])
        factor = 3.0
        hist.loc[row, ["Open", "High", "Low", "Close"]] = [
            prev_close / factor, prev_close / factor + 0.5,
            prev_close / factor - 0.5, prev_close / factor,
        ]
        hist.loc[row, "Volume"] = prev_volume * factor

        result = elite.DataValidator.validate_history(hist, "SPLIT")

        assert not result.is_valid
        assert any("corporate action" in error for error in result.errors)

    def test_large_clean_move_is_a_warning_not_a_disqualification(self):
        """[M8] yfinance is already split-adjusted, so most >50% days are real.

        Disqualifying them silently removed the highest-catalyst names (M&A,
        biotech readouts, guidance resets) from every scan.
        """
        hist = price_history()
        row = hist.index[-1]
        prev_close = float(hist.iloc[-2]["Close"])
        # A takeover-style gap: price jumps, volume also jumps (not inversely).
        hist.loc[row, ["Open", "High", "Low", "Close"]] = [
            prev_close * 1.7, prev_close * 1.75, prev_close * 1.65, prev_close * 1.7,
        ]
        hist.loc[row, "Volume"] = float(hist.iloc[-2]["Volume"]) * 8

        result = elite.DataValidator.validate_history(hist, "GAPPER")

        assert result.is_valid, result.errors
        assert any("does not look like a split" in w for w in result.warnings)

    def test_live_freshness_gate_is_optional_but_fail_closed_when_requested(self):
        stale = price_history(end="2020-01-01")
        assert elite.DataValidator.validate_history(stale, "OLD").is_valid
        result = elite.DataValidator.validate_history(stale, "OLD", max_age_days=10)
        assert not result.is_valid
        assert any("latest bar" in error for error in result.errors)


class TestIndicatorsAndRisk:
    def test_technical_engine_requires_true_200_day_sma_and_rolling_vwap(self):
        assert elite.TechnicalAnalysisEngine.calculate_all_indicators(price_history(199)) is None

        hist = price_history(200)
        indicators = elite.TechnicalAnalysisEngine.calculate_all_indicators(hist)
        assert indicators is not None
        assert indicators.sma_200 == pytest.approx(hist["Close"].mean())

        window = hist.tail(elite.TradingConfig.VWAP_WINDOW)
        typical = (window["High"] + window["Low"] + window["Close"]) / 3.0
        expected_vwap = (typical * window["Volume"]).sum() / window["Volume"].sum()
        assert indicators.vwap == pytest.approx(expected_vwap)

    def test_risk_metrics_reject_unadjusted_split(self):
        hist = price_history()
        hist.loc[hist.index[-1], ["Open", "High", "Low", "Close"]] = [40, 41, 39, 40]
        metrics = elite.RiskAnalyticsEngine.calculate_risk_metrics(hist)
        assert metrics.volatility == 0.0

    def test_sortino_uses_lower_partial_deviation_over_all_observations(self):
        returns = np.array([0.01, -0.005, 0.002, -0.01] * 20)
        close = 100 * np.cumprod(np.r_[1.0, 1.0 + returns])
        hist = pd.DataFrame({"Close": close})
        risk_free_rate = 0.04

        metrics = elite.RiskAnalyticsEngine.calculate_risk_metrics(hist, risk_free_rate)

        annual_return = returns.mean() * 252
        daily_target = risk_free_rate / 252
        downside = np.minimum(returns - daily_target, 0.0)
        expected = (annual_return - risk_free_rate) / (
            np.sqrt(np.mean(np.square(downside))) * np.sqrt(252)
        )
        assert metrics.sortino_ratio == pytest.approx(expected)

    @pytest.mark.parametrize(
        "args",
        [
            (0, 0.2, 10_000),
            (1_000, -0.2, 10_000),
            (1_000, 0.2, 0),
            (float("nan"), 0.2, 10_000),
        ],
    )
    def test_var_validation_fails_closed(self, args):
        passed, value = elite.RiskLimits.check_var_limit(*args)
        assert not passed
        assert np.isinf(value)

    def test_liquidity_and_correlation_fail_closed_when_unassessable(self):
        assert elite.RiskLimits.check_liquidity(1000, 0, 100)[0] is False
        result = elite.RiskLimits.check_correlation("NEW", ["OLD"], {})
        assert result == (False, 1.0, "UNAVAILABLE")


class TestFundamentalsAndSizing:
    def test_missing_fundamentals_do_not_earn_low_debt_points(self):
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals({})
        assert metrics.data_available.get("debt_to_equity") is False
        assert elite.ScoringEngine.calculate_fundamental_score(metrics) == 0.0

    def test_yahoo_debt_to_equity_is_always_converted_from_percent(self):
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals({"debtToEquity": 5})
        assert metrics.debt_to_equity == pytest.approx(0.05)
        assert metrics.data_available["debt_to_equity"] is True

    def test_ambiguous_dividend_yield_uses_rate_and_price_cross_check(self):
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals(
            {"dividendYield": 0.23, "dividendRate": 0.88, "currentPrice": 170.0}
        )
        assert 0 < metrics.dividend_yield < 0.02

    def test_small_account_cap_is_used_by_position_sizer(self):
        system = object.__new__(elite.QuantaEliteTradingSystem)
        system.capital = 1_000.0
        system.drawdown_multiplier = 1.0

        # A tight 3% stop means the risk budget buys a large notional, so the
        # per-position cap is what binds — which is the point of this test.
        allocation = system._calculate_position_size(
            signal=elite.SignalType.STRONG_BUY.value, score=10.0,
            entry=100.0, stop=97.0,
        )
        strict_cap_dollars = system.capital * elite.TradingConfig.MAX_POSITION_PCT
        effective_cap_dollars = (
            system.capital * elite.TradingConfig.effective_max_position_pct(system.capital)
        )
        assert strict_cap_dollars < allocation <= effective_cap_dollars

    def test_position_sizer_rejects_a_stop_it_cannot_size_from(self):
        system = object.__new__(elite.QuantaEliteTradingSystem)
        system.capital = 100_000.0
        system.drawdown_multiplier = 1.0
        # Sanity: an unrecognised signal still sizes, at the conservative
        # fallback multiplier, so the zero cases below are meaningful.
        assert system._calculate_position_size(
            signal="UNRECOGNISED_SIGNAL", score=0.0, entry=100.0, stop=80.0,
        ) > 0.0
        # A stop that is not below entry cannot be sized from risk at all.
        assert system._calculate_position_size(
            signal=elite.SignalType.BUY.value, score=5.0, entry=100.0, stop=100.0,
        ) == 0.0
        assert system._calculate_position_size(
            signal=elite.SignalType.BUY.value, score=5.0, entry=100.0, stop=120.0,
        ) == 0.0

    def test_position_size_honours_the_per_trade_risk_budget(self):
        """[B4] Dollar risk at the stop must equal the configured budget.

        MAX_RISK_PER_TRADE used to be referenced only by validate_config;
        nothing tied share count to (entry - stop).
        """
        system = object.__new__(elite.QuantaEliteTradingSystem)
        system.capital = 100_000.0
        system.drawdown_multiplier = 1.0

        for entry, stop in ((100.0, 90.0), (50.0, 46.0), (250.0, 200.0)):
            allocation = system._calculate_position_size(
                signal=elite.SignalType.BUY.value, score=6.0, entry=entry, stop=stop,
            )
            cap = system.capital * elite.TradingConfig.effective_max_position_pct(
                system.capital
            )
            if allocation >= cap:
                continue  # position cap bound before the risk budget
            dollar_risk = allocation * ((entry - stop) / entry)
            expected = (
                system.capital
                * elite.TradingConfig.TARGET_RISK_PER_TRADE
                * 0.80          # BUY signal multiplier
                * (0.7 + 0.6 * 0.5)  # score multiplier at score 6.0
            )
            assert dollar_risk == pytest.approx(expected, rel=1e-6)
            assert dollar_risk <= system.capital * elite.TradingConfig.MAX_RISK_PER_TRADE

    def test_wider_stop_buys_fewer_shares(self):
        """[B4] Risk-based sizing is volatility targeting: wider stop, less size."""
        system = object.__new__(elite.QuantaEliteTradingSystem)
        system.capital = 100_000.0
        system.drawdown_multiplier = 1.0
        sizes = [
            system._calculate_position_size(
                signal=elite.SignalType.BUY.value, score=6.0, entry=100.0, stop=100.0 - d,
            )
            for d in (4.0, 8.0, 16.0)
        ]
        assert sizes[0] > sizes[1] > sizes[2] > 0

    def test_risk_grade_multipliers_are_conservative(self):
        multipliers = elite.TradingConfig.RISK_GRADE_SIZE_MULTIPLIERS
        assert multipliers["A"] >= multipliers["C"] > multipliers["D"] > multipliers["F"]


def test_no_uncalibrated_target_probability_is_emitted():
    projection = elite.TimeframeProjector.project_timeframe(100, 120, 0.25, 0.10, "Industrials")
    assert projection.probability_success == 0.0
    assert "no calibrated" in projection.confidence_basis.lower()


def test_adjusted_prices_are_explicit_on_primary_download_paths():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "auto_adjust=True" in source
    assert "adjustment_status: str = \"split_dividend_adjusted\"" in source
