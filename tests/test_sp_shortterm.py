from __future__ import annotations

import contextlib
import datetime as datetime_module
import importlib.util
import re
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
from engine.market_calendar import last_completed_session


_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE.parent / "core" / "SP_ShortTerm.py"
_SPEC = importlib.util.spec_from_file_location("sp_shortterm", _MODULE_PATH)
sp_shortterm = importlib.util.module_from_spec(_SPEC)
sys.modules["sp_shortterm"] = sp_shortterm
assert _SPEC.loader is not None
_SPEC.loader.exec_module(sp_shortterm)


def _sample_history(rows: int = 260) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = np.linspace(80, 120, rows)
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.25
    volume = np.full(rows, 1_000_000)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


class TestShortTermAnalyzerCore(unittest.TestCase):
    def test_watch_and_avoid_are_filtered_by_signal_name(self):
        self.assertEqual(sp_shortterm.TradingSignal.WATCH.signal_name, "WATCH")
        self.assertNotEqual(sp_shortterm.TradingSignal.WATCH.value, "WATCH")
        blocked = {sp_shortterm.TradingSignal.AVOID.signal_name, sp_shortterm.TradingSignal.WATCH.signal_name}
        self.assertIn("WATCH", blocked)
        self.assertIn("AVOID", blocked)

    def test_pattern_detection_uses_indicator_values_not_missing_hist_columns(self):
        hist = _sample_history()
        indicators = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(hist)
        patterns = sp_shortterm.AdvancedTechnicalAnalyzer.detect_patterns(hist, indicators)
        self.assertIsInstance(patterns, list)

        breakout_indicators = dict(indicators)
        breakout_indicators["current_price"] = breakout_indicators["Resistance_20_Prev"] * 1.01
        patterns = sp_shortterm.AdvancedTechnicalAnalyzer.detect_patterns(hist, breakout_indicators)
        self.assertIn("Resistance Breakout", patterns)

    def test_position_sizing_enforces_true_dollar_risk_cap(self):
        analyzer = sp_shortterm.StrategicStockAnalyzer(capital=10_000)
        position = analyzer._calculate_position_sizing(
            sp_shortterm.TradingSignal.BUY.signal_name,
            score=80,
            entry_price=100,
            risk_amount=5,
            volatility=0.20,
        )
        self.assertGreater(position["shares"], 0)
        self.assertLessEqual(position["risk_dollars"], 10_000 * sp_shortterm.Config.MAX_RISK_PER_TRADE)
        self.assertEqual(position["risk_dollars"], position["shares"] * 5)

    def test_exit_strategy_exposes_nearby_resistance_instead_of_inventing_rr(self):
        analyzer = sp_shortterm.StrategicStockAnalyzer(capital=10_000)
        exit_strategy = analyzer._calculate_exit_strategy(
            entry_price=100,
            indicators={
                "ATR": 2,
                "S1": 98,
                "Resistance_20_Prev": 101.5,
                "Resistance_50": 103,
                "R1": 101.2,
                "R2": 102.5,
                "High_52W_Prev": 104,
                "Volume_Ratio": 1.0,
            },
            signal=sp_shortterm.TradingSignal.ACCUMULATE.signal_name,
            score=62,
        )
        risk = 100 - exit_strategy["stop_loss"]
        reward = exit_strategy["primary_target"] - 100
        self.assertLess(reward / risk, sp_shortterm.Config.MIN_RISK_REWARD)
        self.assertEqual(exit_strategy["target_method"], "nearest multi-week resistance")

    def test_price_history_falls_back_when_yahoo_is_blocked(self):
        market_data = sp_shortterm.EnhancedMarketData()
        stock = Mock()
        stock.history.side_effect = RuntimeError("Too Many Requests")
        fallback = _sample_history()
        # _get_stooq_history now returns (dataframe, status).
        market_data._get_stooq_history = Mock(
            return_value=(fallback, sp_shortterm.Outcome.ACTIVE)
        )

        hist, status = market_data.get_price_history("AAPL", stock)

        self.assertFalse(hist.empty)
        self.assertEqual(status, sp_shortterm.Outcome.ACTIVE)
        market_data._get_stooq_history.assert_called_once_with("AAPL")

    def test_stock_info_returns_empty_dict_when_yahoo_is_blocked(self):
        market_data = sp_shortterm.EnhancedMarketData()
        stock = Mock()
        type(stock).info = property(lambda self: (_ for _ in ()).throw(RuntimeError("Too Many Requests")))

        self.assertEqual(market_data.get_stock_info("AAPL", stock), {})


def _trending_history(rows: int = 260, drift: float = 0.0022, vol: float = 0.013,
                      seed: int = 7) -> pd.DataFrame:
    """Synthetic OHLCV with a genuine trend, for indicator/pattern assertions."""
    gen = np.random.default_rng(seed)
    returns = gen.normal(drift, vol, rows)
    close = 100 * np.exp(np.cumsum(returns))
    return pd.DataFrame(
        {
            "Open": close * 0.998,
            "High": close * (1 + np.abs(gen.normal(0, 0.005, rows))),
            "Low": close * (1 - np.abs(gen.normal(0, 0.005, rows))),
            "Close": close,
            "Volume": gen.integers(2_000_000, 6_000_000, rows).astype(float),
        },
        index=pd.date_range("2024-01-01", periods=rows, freq="B"),
    )


def _bare_analyzer(capital: float = 25_000.0):
    """An analyzer with no network side effects (skips __init__ entirely)."""
    analyzer = object.__new__(sp_shortterm.StrategicStockAnalyzer)
    analyzer.capital = capital
    analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
    analyzer.regime_degraded = False
    analyzer.benchmark_degraded = False
    analyzer.spy_3m_return = 0.03
    analyzer._returns_cache = {}
    analyzer._universe_momentum = []
    analyzer._universe_momentum_lock = None
    return analyzer


def _minimal_setup(**overrides):
    defaults = dict(
        ticker="TEST", company="Test Co", sector="Technology", industry="Software",
        signal="BUY", confidence=80.0, overall_score=75.0,
        technical_score=70.0, fundamental_score=60.0, momentum_score=70.0,
        quality_score=65.0, analyst_score=60.0, pattern_score=55.0,
        entry_price=100.0, entry_zone_low=99.0, entry_zone_high=101.0,
        optimal_entry=99.5, primary_target=110.0, secondary_target=115.0,
        aggressive_target=120.0, stop_loss=95.0, trailing_stop_pct=5.0,
        timeframe="swing", expected_hold_days=10, min_hold_days=3, max_hold_days=20,
        allocation_usd=0.0, allocation_pct=0.0, shares=0, position_score=80.0,
        risk_amount=0.0, risk_pct=0.0, reward_amount=0.0,
        risk_reward_ratio=2.0, expected_return_pct=10.0, max_loss_pct=5.0,
        rsi=55.0, rsi_trend="Rising", macd=0.5, macd_signal=0.3, macd_histogram=0.2,
        stochastic=50.0, bb_position=0.5, atr=2.0, atr_pct=2.0,
        volume_ratio=1.2, obv_trend="Accumulation",
        current_price=100.0, price_vs_sma20=1.0, price_vs_sma50=2.0,
        price_vs_sma200=5.0, high_52w=120.0, low_52w=70.0,
        distance_from_high_pct=-16.0, distance_from_low_pct=42.0,
    )
    defaults.update(overrides)
    return sp_shortterm.TradingSetup(**defaults)


class TestIndicatorCorrectness(unittest.TestCase):
    """The indicator engine must agree with the textbook (Wilder) definitions."""

    def setUp(self):
        self.hist = _trending_history()
        self.ind = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(
            self.hist
        )

    def test_rsi_and_atr_match_wilder_reference(self):
        import ta.momentum
        import ta.volatility

        rsi_ref = ta.momentum.RSIIndicator(self.hist["Close"], window=14).rsi().iloc[-1]
        atr_ref = ta.volatility.AverageTrueRange(
            self.hist["High"], self.hist["Low"], self.hist["Close"], window=14
        ).average_true_range().iloc[-1]
        self.assertAlmostEqual(self.ind["RSI"], float(rsi_ref), places=4)
        self.assertAlmostEqual(self.ind["ATR"], float(atr_ref), places=4)

    def test_trend_strength_is_a_real_adx_not_a_bounded_proxy(self):
        # The old proxy was mean(High-Low)/ATR, which is <= 1.0 by construction,
        # so the >1.2 / >1.5 score bands were unreachable dead code.
        import ta.trend

        adx_ref = ta.trend.ADXIndicator(
            self.hist["High"], self.hist["Low"], self.hist["Close"], window=14
        ).adx().iloc[-1]
        self.assertAlmostEqual(self.ind["ADX"], float(adx_ref), delta=3.0)
        self.assertEqual(self.ind["Trend_Strength"], self.ind["ADX"])
        self.assertGreater(self.ind["ADX"], 1.0)
        self.assertIn("Plus_DI", self.ind)
        self.assertIn("Minus_DI", self.ind)

    def test_adx_band_awards_points_only_to_an_uptrend(self):
        analyzer = _bare_analyzer()
        base = dict(self.ind)
        base["ADX"] = sp_shortterm.Config.ADX_STRONG_TREND + 5
        up = dict(base, Plus_DI=30.0, Minus_DI=10.0)
        down = dict(base, Plus_DI=10.0, Minus_DI=30.0)
        price = base["current_price"]
        self.assertGreater(
            analyzer._calculate_technical_score(up, price),
            analyzer._calculate_technical_score(down, price),
        )

    def test_warmup_gaps_never_leak_nan_into_the_indicator_dict(self):
        # 110 bars is too short for Momentum_6M (needs 127). Series.get() would
        # have returned NaN here, which then scored as a perfect RS rating.
        short = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(
            _trending_history(rows=110)
        )
        numeric = {k: v for k, v in short.items() if isinstance(v, float)}
        self.assertTrue(numeric)
        for key, value in numeric.items():
            self.assertTrue(np.isfinite(value), f"{key} is not finite: {value}")

    def test_support_breakdown_uses_prior_bar_support(self):
        # Support_20 includes today's own low and the close can never be below
        # it, so the pattern was unreachable. It must key off Support_20_Prev.
        hist = _trending_history(drift=-0.004, seed=3)
        ind = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(hist)
        self.assertIn("Support_20_Prev", ind)
        forced = dict(ind, current_price=ind["Support_20_Prev"] * 0.98)
        patterns = sp_shortterm.AdvancedTechnicalAnalyzer.detect_patterns(hist, forced)
        self.assertIn("Support Breakdown", patterns)


class TestProviderPayloadSafety(unittest.TestCase):
    """A present-but-null Yahoo field must never abort a ticker."""

    HOSTILE = {
        "marketCap": None, "longName": None, "sector": None, "industry": None,
        "beta": None, "trailingPE": None, "returnOnEquity": None,
        "revenueGrowth": None, "numberOfAnalystOpinions": None,
        "recommendationKey": None, "heldPercentInstitutions": None,
        "shortPercentOfFloat": None, "freeCashflow": None,
    }

    def test_null_valued_fields_do_not_raise(self):
        analyzer = _bare_analyzer()
        ind = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(
            _trending_history()
        )
        for value in (
            analyzer._calculate_fundamental_score(self.HOSTILE),
            analyzer._calculate_quality_score(self.HOSTILE),
            analyzer._calculate_analyst_score(self.HOSTILE),
        ):
            self.assertTrue(0.0 <= value <= 100.0)
        self.assertEqual(analyzer._categorize_market_cap(self.HOSTILE["marketCap"]), "Unknown")
        self.assertIsNone(sp_shortterm.StrategicStockAnalyzer._days_to_next_earnings({}))

    def test_missing_data_is_never_better_than_complete_data(self):
        # Neutral imputation, not renormalisation: a blocked payload must not be
        # able to out-score the same name with every field present.
        analyzer = _bare_analyzer()
        complete = {
            "marketCap": 2.5e11, "heldPercentInstitutions": 0.60,
            "numberOfAnalystOpinions": 25, "beta": 1.0,
            "trailingAnnualDividendYield": 0.02, "recommendationKey": "strong_buy",
            "shortPercentOfFloat": 0.01,
            "returnOnEquity": 0.28, "profitMargins": 0.24,
            "operatingMargins": 0.21, "freeCashflow": 2e9,
            "debtToEquity": 20, "currentRatio": 2.0,
        }
        self.assertGreaterEqual(
            analyzer._calculate_quality_score(complete),
            analyzer._calculate_quality_score({}),
        )
        self.assertGreaterEqual(
            analyzer._calculate_analyst_score(complete),
            analyzer._calculate_analyst_score({}),
        )

    def test_blocked_payload_is_not_scored_as_zero(self):
        analyzer = _bare_analyzer()
        self.assertEqual(analyzer._calculate_quality_score({}), 50.0)
        self.assertEqual(analyzer._calculate_fundamental_score({}), 50.0)

    def test_null_financial_fields_are_missing_not_zero_growth(self):
        analyzer = _bare_analyzer()
        assert analyzer._calculate_growth_score({"revenueGrowth": None,
                                                  "earningsGrowth": float("nan")}) == 50.0
        assert analyzer._calculate_profitability_score({"profitMargins": None,
                                                         "freeCashflow": None}) == 50.0

    def test_fcf_quality_uses_yield_not_absolute_company_size(self):
        analyzer = _bare_analyzer()
        small = {"freeCashflow": 50_000_000, "marketCap": 1_000_000_000}
        large = {"freeCashflow": 5_000_000_000, "marketCap": 100_000_000_000}
        assert analyzer._calculate_profitability_score(small) == \
            analyzer._calculate_profitability_score(large)


class TestExecutionConsistency(unittest.TestCase):
    """Money maths must be anchored on the price the plan actually executes at."""

    def test_accounting_uses_execution_entry_not_the_last_close(self):
        analyzer = _bare_analyzer(capital=10_000)
        setup = _minimal_setup(entry_price=100.0, execution_entry=99.40,
                               stop_loss=94.0, primary_target=110.0)
        analyzer._finalize_share_accounting(setup, 994.0)
        self.assertEqual(setup.shares, 10)
        self.assertAlmostEqual(setup.executable_allocation_usd, 994.0, places=2)
        self.assertAlmostEqual(setup.risk_amount, 10 * (99.40 - 94.0), places=2)

    def test_accounting_falls_back_to_entry_price_when_unset(self):
        analyzer = _bare_analyzer(capital=10_000)
        setup = _minimal_setup(entry_price=150.0, stop_loss=140.0, primary_target=180.0)
        analyzer._finalize_share_accounting(setup, 500.0)
        self.assertEqual(setup.shares, 3)
        self.assertAlmostEqual(setup.risk_amount, 3 * 10.0, places=2)

    def test_minimum_position_floor_survives_a_conviction_downsize(self):
        original = sp_shortterm.Config.ALLOW_FRACTIONAL_SHARES
        sp_shortterm.Config.ALLOW_FRACTIONAL_SHARES = False
        try:
            analyzer = _bare_analyzer(capital=10_000)
            setup = _minimal_setup(entry_price=40.0, execution_entry=40.0,
                                   stop_loss=37.0, primary_target=50.0)
            analyzer._finalize_share_accounting(setup, 280.0)  # 7 shares = $280
            self.assertTrue(setup.is_funded)
            analyzer._downsize_setup(setup, 0.5)               # $140 < $150 floor
            self.assertFalse(setup.is_funded)
            self.assertEqual(setup.shares, 0)
            self.assertIn("minimum position", setup.watchlist_reason)
        finally:
            sp_shortterm.Config.ALLOW_FRACTIONAL_SHARES = original

    def test_entry_zone_is_ordered_and_contains_the_ticket(self):
        analyzer = _bare_analyzer()
        # Support above the last close: the un-clamped zone inverted and priced
        # the buy limit above the market.
        indicators = {"ATR": 1.0, "Support_20": 105.0, "BB_Lower": 104.0}
        strategy = analyzer._calculate_entry_strategy(100.0, indicators)
        self.assertLessEqual(strategy["entry_zone_low"], 100.0)
        self.assertGreaterEqual(strategy["entry_zone_high"], 100.0)
        self.assertLessEqual(strategy["entry_zone_low"], strategy["optimal_entry"])
        self.assertLessEqual(strategy["optimal_entry"], strategy["entry_zone_high"])


class TestHoldingWindow(unittest.TestCase):
    """The horizon must come from the trade's geometry, not from its score."""

    def test_nearby_target_yields_a_short_horizon(self):
        analyzer = _bare_analyzer()
        indicators = {"ATR": 2.0, "Volatility_20D": 0.22, "Momentum_1M": 0.03}
        info = analyzer._determine_timeframe(
            "ACCUMULATE", 62, indicators, {}, entry_price=100.0, target_price=107.0
        )
        # Driftless first-touch time grows with squared log-distance; seven
        # percent is not necessarily a one-month move at 22% annual volatility.
        self.assertTrue(21 <= info["expected_days"] <= 60)
        self.assertLess(info["min_days"], info["expected_days"])
        self.assertGreater(info["max_days"], info["expected_days"])

    def test_distant_target_yields_a_longer_horizon(self):
        analyzer = _bare_analyzer()
        near = analyzer._determine_timeframe(
            "ACCUMULATE", 62, {"ATR": 2.0, "Volatility_20D": 0.22}, {},
            entry_price=100.0, target_price=105.0)
        far = analyzer._determine_timeframe(
            "ACCUMULATE", 62, {"ATR": 2.0, "Volatility_20D": 0.22}, {},
            entry_price=100.0, target_price=130.0)
        self.assertGreater(far["expected_days"], near["expected_days"])

    def test_score_based_fallback_still_available(self):
        analyzer = _bare_analyzer()
        info = analyzer._determine_timeframe(
            "ACCUMULATE", 62, {"ATR": 2.0, "Volatility_20D": 0.22}, {}
        )
        self.assertEqual(info["description"], sp_shortterm.TimeFrame.POSITION.description)


class TestSelectionOrdering(unittest.TestCase):
    """Selection must be reproducible and presented best-first."""

    def test_final_picks_are_returned_in_rank_order(self):
        analyzer = _bare_analyzer(capital=100_000)
        analyzer._is_acceptable_correlation = lambda ticker, existing: True
        weak = _minimal_setup(ticker="WEAK", sector="Energy", overall_score=61.0,
                              relative_strength=80.0, execution_entry=100.0)
        strong = _minimal_setup(ticker="STRG", sector="Technology", overall_score=88.0,
                                relative_strength=10.0, execution_entry=100.0)
        for setup in (weak, strong):
            analyzer._finalize_share_accounting(setup, 1_000.0)
        # RS-preferred pass admits WEAK first; the report must still lead with STRG.
        analyzer.results = [weak, strong]
        picks = analyzer.get_top_setups(max_positions=5)
        self.assertEqual([p.ticker for p in picks], ["STRG", "WEAK"])

    def test_rs_fallback_uses_midpoint_percentiles(self):
        # rank/n put the better of two names at 50.5 and the other at 1.0.
        analyzer = _bare_analyzer()
        low = _minimal_setup(ticker="LOW", momentum_3m=1.0, momentum_6m=1.0, momentum_1m=1.0)
        high = _minimal_setup(ticker="HIGH", momentum_3m=40.0, momentum_6m=40.0,
                              momentum_1m=40.0)
        analyzer._assign_rs_ratings([low, high])
        self.assertGreater(high.relative_strength, 50.0)
        self.assertLess(low.relative_strength, 50.0)

    def test_weighted_momentum_treats_nan_as_flat(self):
        weighted = sp_shortterm.StrategicStockAnalyzer._weighted_momentum_pct(
            10.0, float("nan"), 5.0
        )
        self.assertTrue(np.isfinite(weighted))
        self.assertAlmostEqual(weighted, 10.0 * 0.4 + 5.0 * 0.25)


class TestUnfundableCandidatesReachTheWatchlist(unittest.TestCase):
    """A pick that clears the model but not the wallet must still be named.

    Position sizing used to ``return None`` for such a name, which made the
    whole watchlist mechanism unreachable from sizing: the scan could report
    "N could not fund one whole share" as a bare count while never telling the
    user which N.
    """

    @staticmethod
    def _strong_history(rows: int = 300, seed: int = 7) -> pd.DataFrame:
        """A high-priced name in a clean, volume-confirmed uptrend."""
        gen = np.random.default_rng(seed)
        close = 300 * np.exp(np.cumsum(gen.normal(0.0030, 0.012, rows)))
        volume = gen.integers(2_000_000, 6_000_000, rows).astype(float)
        volume[-3:] *= 2.6  # accumulation into the signal bar
        high = close * (1 + np.abs(gen.normal(0, 0.005, rows)))
        # A prior swing high outside the ATR window supplies a genuine T1
        # above 2R; otherwise the new structure-based R/R gate correctly
        # rejects this synthetic trend before funding/report paths run.
        high[-20] = max(high[-20], close[-1] * 1.15)
        high[-45] = max(high[-45], close[-1] * 1.25)
        return pd.DataFrame(
            {
                "Open": close * 0.998,
                "High": high,
                "Low": close * (1 - np.abs(gen.normal(0, 0.005, rows))),
                "Close": close,
                "Volume": volume,
            },
            index=pd.bdate_range(end=last_completed_session(), periods=rows),
        )

    INFO = {
        "longName": "High Priced Co", "sector": "Technology", "industry": "Software",
        "marketCap": 2.4e11, "trailingPE": 16.0, "pegRatio": 0.9,
        "returnOnEquity": 0.31, "revenueGrowth": 0.33, "earningsGrowth": 0.28,
        "profitMargins": 0.27, "operatingMargins": 0.24, "debtToEquity": 15.0,
        "currentRatio": 2.4, "freeCashflow": 5.0e9,
        "heldPercentInstitutions": 0.62, "numberOfAnalystOpinions": 22,
        "beta": 1.05, "recommendationKey": "strong_buy",
        "shortPercentOfFloat": 0.015, "trailingAnnualDividendYield": 0.015,
    }

    def _run_scan(self, capital):
        history = self._strong_history()
        info = self.INFO

        class _Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, *args, **kwargs):
                if kwargs.get("interval"):
                    return pd.DataFrame()   # no intraday bars in the fixture
                return history

            @property
            def info(self):
                return {} if self.symbol in ("SPY", "^VIX") else info

        with patch.object(sp_shortterm.yf, "Ticker", side_effect=_Ticker):
            analyzer = sp_shortterm.StrategicStockAnalyzer(capital=capital)
            analyzer.build_universe = lambda scan_type="quick": ["EXPENSIVE"]
            analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
            analyzer.regime_degraded = False
            with contextlib.redirect_stdout(io.StringIO()):
                setups = analyzer.scan_market("quick")
        return analyzer, setups

    def test_named_on_the_watchlist_instead_of_silently_dropped(self):
        # A ~$400 stock cannot be bought in a $600 account under the 35%
        # single-position cap, but the idea itself is still worth showing.
        analyzer, setups = self._run_scan(capital=600)
        self.assertEqual([s.ticker for s in setups], ["EXPENSIVE"])
        pick = setups[0]
        self.assertFalse(pick.is_funded)
        self.assertEqual(pick.shares, 0)
        self.assertEqual(pick.allocation_usd, 0.0)
        self.assertEqual(pick.risk_amount, 0.0)
        self.assertTrue(pick.moved_to_watchlist)
        self.assertTrue(pick.watchlist_reason)
        self.assertIn("EXPENSIVE", [s.ticker for s in analyzer.watchlist_setups])
        # ...and it must never be presented as a funded position.
        self.assertEqual(analyzer.get_top_setups(max_positions=5), [])

    def test_same_name_is_funded_when_capital_allows(self):
        analyzer, setups = self._run_scan(capital=50_000)
        self.assertEqual([s.ticker for s in setups], ["EXPENSIVE"])
        pick = setups[0]
        self.assertTrue(pick.is_funded)
        self.assertGreater(pick.shares, 0)
        self.assertAlmostEqual(
            pick.executable_allocation_usd, pick.shares * pick.execution_entry, places=2
        )
        self.assertAlmostEqual(
            pick.risk_amount,
            pick.shares * (pick.execution_entry - pick.stop_loss), places=2
        )
        self.assertIn("EXPENSIVE", [p.ticker for p in analyzer.get_top_setups(5)])


class TestRiskMetricsAreAlwaysFinite(unittest.TestCase):
    """A non-finite risk metric silently zeroes a scoring component."""

    def _assert_all_finite(self, metrics):
        for key, value in metrics.items():
            self.assertTrue(np.isfinite(value), f"{key} is not finite: {value}")

    def test_infinite_return_from_a_zero_price_bar(self):
        # A zero price in the feed makes pct_change emit inf.
        returns = np.array([0.01] * 30 + [np.inf] + [0.01] * 30)
        self._assert_all_finite(
            sp_shortterm.AdvancedTechnicalAnalyzer.calculate_risk_metrics(
                returns, np.ones(61))
        )

    def test_leading_total_loss_bar(self):
        # -100% on the first bar drove the equity curve to zero, making the
        # drawdown division 0/0 -> NaN.
        rest = np.random.default_rng(0).normal(0.001, 0.02, 60)
        returns = np.concatenate([[-1.0], rest])
        self._assert_all_finite(
            sp_shortterm.AdvancedTechnicalAnalyzer.calculate_risk_metrics(
                returns, np.ones(61))
        )

    def test_normal_series_is_unchanged_in_character(self):
        returns = np.random.default_rng(1).normal(0.001, 0.015, 250)
        metrics = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_risk_metrics(
            returns, np.cumprod(1 + returns) * 100)
        self._assert_all_finite(metrics)
        self.assertLessEqual(metrics["max_drawdown"], 0.0)
        self.assertTrue(0.0 <= metrics["win_rate"] <= 1.0)


class TestFailureAttribution(unittest.TestCase):
    """The scan summary's whole purpose is separating provider faults from
    model outcomes, so a bug in our own code must not be filed as either."""

    def _analyzer_with_data(self):
        history = TestUnfundableCandidatesReachTheWatchlist._strong_history()

        class _Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, *args, **kwargs):
                return pd.DataFrame() if kwargs.get("interval") else history

            @property
            def info(self):
                return {} if self.symbol in ("SPY", "^VIX") else \
                    TestUnfundableCandidatesReachTheWatchlist.INFO

        return patch.object(sp_shortterm.yf, "Ticker", side_effect=_Ticker)

    def test_internal_error_is_not_reported_as_missing_data(self):
        with self._analyzer_with_data():
            with contextlib.redirect_stdout(io.StringIO()):
                analyzer = sp_shortterm.StrategicStockAnalyzer(capital=10_000)
            analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
            analyzer.regime_degraded = False
            analyzer._calculate_technical_score = Mock(side_effect=RuntimeError("boom"))
            with self.assertLogs(sp_shortterm.logger, level="WARNING"):
                self.assertIsNone(analyzer.analyze_stock("ZZZ"))

        summary = analyzer.diagnostics.summary(1)
        self.assertEqual(summary["analysis_errors"], 1)
        self.assertEqual(summary["no_data_tickers"], 0)
        self.assertEqual(summary["model_filtered"], 0)
        self.assertEqual(summary["provider_failures"], 0)
        # A deterministic code error must not consume the retry budget.
        self.assertEqual(analyzer.failed_tickers, [])

    def test_zero_capital_does_not_raise(self):
        # The CLI floors capital at $500, but a library caller can pass 0, and
        # sizing no longer rejects first, so the risk-percent division here has
        # to be guarded.
        with self._analyzer_with_data():
            with contextlib.redirect_stdout(io.StringIO()):
                analyzer = sp_shortterm.StrategicStockAnalyzer(capital=0)
            analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
            analyzer.regime_degraded = False
            setup = analyzer.analyze_stock("ZZZ")
        self.assertIsNotNone(setup)
        self.assertFalse(setup.is_funded)
        self.assertEqual(setup.risk_pct, 0.0)
        self.assertEqual(setup.shares, 0)


class TestStooqFallbackIntegrity(unittest.TestCase):
    """Yahoo returns split-adjusted prices; Stooq's daily CSV does not."""

    def _csv(self, closes):
        dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
        lines = ["Date,Open,High,Low,Close,Volume"]
        for day, close in zip(dates, closes):
            lines.append(f"{day:%Y-%m-%d},{close},{close * 1.01},"
                         f"{close * 0.99},{close},1000000")
        return "\n".join(lines)

    def _fetch(self, closes):
        market_data = sp_shortterm.EnhancedMarketData()
        sp_shortterm.EnhancedMarketData._stooq_disabled = False
        response = Mock()
        response.text = self._csv(closes)
        response.raise_for_status = Mock()
        with patch.object(sp_shortterm.requests, "get", return_value=response):
            return market_data._get_stooq_history("TEST")

    def test_clean_series_is_accepted(self):
        closes = list(100 * np.exp(np.cumsum(
            np.random.default_rng(0).normal(0.0005, 0.01, 200))))
        hist, status = self._fetch(closes)
        self.assertEqual(status, sp_shortterm.Outcome.ACTIVE)
        self.assertFalse(hist.empty)

    def test_unadjusted_split_cliff_is_rejected(self):
        # An unadjusted 2:1 split halves the series overnight, which would
        # corrupt every moving average, the 52-week range, ATR and momentum.
        closes = [200.0] * 100 + [100.0] * 100
        hist, status = self._fetch(closes)
        self.assertTrue(hist.empty)
        self.assertEqual(status, sp_shortterm.Outcome.NO_PRICE_DATA)


class TestSignalThresholdsHaveOneSourceOfTruth(unittest.TestCase):
    def test_enum_min_scores_match_the_decision_logic(self):
        self.assertEqual(sp_shortterm.TradingSignal.STRONG_BUY.min_score, 80)
        self.assertEqual(sp_shortterm.TradingSignal.BUY.min_score, 70)
        self.assertEqual(
            sp_shortterm.TradingSignal.ACCUMULATE.min_score,
            sp_shortterm.Config.MIN_SCORE,
        )

    def test_a_score_just_under_the_buy_bar_is_not_a_buy(self):
        analyzer = _bare_analyzer()
        indicators = {"RSI": 55.0, "MACD_Histogram": 0.3, "Volume_Ratio_Adj": 1.5}
        bar = sp_shortterm.TradingSignal.BUY.min_score
        below, _ = analyzer._determine_signal(bar - 0.1, indicators, 65.0, 60.0)
        at, _ = analyzer._determine_signal(bar, indicators, 65.0, 60.0)
        self.assertEqual(below, sp_shortterm.TradingSignal.ACCUMULATE.signal_name)
        self.assertEqual(at, sp_shortterm.TradingSignal.BUY.signal_name)

class TestIntradayScoreStability(unittest.TestCase):
    """The same tape must not score differently at 10:00 and at 15:45."""

    def _score_at(self, fraction):
        history = TestUnfundableCandidatesReachTheWatchlist._strong_history()

        class _Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, *args, **kwargs):
                return pd.DataFrame() if kwargs.get("interval") else history

            @property
            def info(self):
                return {} if self.symbol in ("SPY", "^VIX") else \
                    TestUnfundableCandidatesReachTheWatchlist.INFO

        with patch.object(sp_shortterm.yf, "Ticker", side_effect=_Ticker), \
             patch.object(sp_shortterm.StrategicStockAnalyzer,
                          "_session_fraction_elapsed",
                          staticmethod(lambda: fraction)):
            with contextlib.redirect_stdout(io.StringIO()):
                analyzer = sp_shortterm.StrategicStockAnalyzer(capital=50_000)
            analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
            analyzer.regime_degraded = False
            setup = analyzer.analyze_stock("ZZZ")
        self.assertIsNotNone(setup)
        return setup.overall_score

    def test_bar_is_complete_only_outside_the_session(self):
        complete = sp_shortterm.StrategicStockAnalyzer._bar_is_complete
        for fraction in (0.05, 0.5, 0.94, 0.95, 0.99):
            self.assertFalse(complete(fraction), f"{fraction} treated as complete")
        self.assertTrue(complete(1.0))

    def test_score_is_flat_across_the_whole_session(self):
        # The old 0.95 cutoff flipped the scoring volume ratio from yesterday's
        # finished bar to today's partial one at ~15:41 ET, so a candidate
        # could qualify at 15:40 and vanish at 15:42.
        scores = {f: self._score_at(f) for f in (0.05, 0.5, 0.94, 0.96, 0.99)}
        self.assertEqual(len(set(scores.values())), 1, f"score drifted: {scores}")

class TestReportRendering(unittest.TestCase):
    """The report is the product. It must render, and its numbers must agree."""

    @classmethod
    def setUpClass(cls):
        history = TestUnfundableCandidatesReachTheWatchlist._strong_history()

        class _Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, *args, **kwargs):
                return pd.DataFrame() if kwargs.get("interval") else history

            @property
            def info(self):
                return {} if self.symbol in ("SPY", "^VIX") else \
                    TestUnfundableCandidatesReachTheWatchlist.INFO

        with patch.object(sp_shortterm.yf, "Ticker", side_effect=_Ticker), \
             patch.object(sp_shortterm.StrategicStockAnalyzer,
                          "_session_fraction_elapsed", staticmethod(lambda: 0.5)):
            with contextlib.redirect_stdout(io.StringIO()):
                analyzer = sp_shortterm.StrategicStockAnalyzer(capital=50_000)
            analyzer.build_universe = lambda scan_type="quick": ["AAA", "BBB"]
            analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
            analyzer.regime_degraded = False
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                analyzer.scan_market("quick")
                picks = analyzer.get_top_setups(max_positions=4)
                analyzer.display_results(picks)
        cls.analyzer = analyzer
        cls.picks = picks
        cls.output = buffer.getvalue()

    def test_every_section_renders(self):
        self.assertTrue(self.picks, "fixture produced no funded picks")
        for section in ("AT A GLANCE", "MARKET CONTEXT", "THE PICKS",
                        "TRADE LEVELS", "TIMING", "FACTOR GRADES",
                        "EXECUTION TICKETS", "SUPPORTING DATA", "PORTFOLIO",
                        "ACTION PLAN", "HOW TO READ THIS REPORT",
                        "BUY LIMIT", "TARGET 1", "INVALIDATE", "WHY THIS TRADE",
                        "WHAT COULD GO WRONG", "HOW TO EXECUTE",
                        "Portfolio manager review", "STANDING RULES", "DISCLAIMER"):
            self.assertIn(section, self.output, f"missing report section: {section}")

    def test_sections_are_numbered_in_order(self):
        numbers = [int(m) for m in re.findall(r"^ (\d+)\.  [A-Z]", self.output,
                                              flags=re.M)]
        self.assertEqual(numbers, sorted(numbers), f"sections out of order: {numbers}")
        self.assertEqual(numbers[0], 1)
        self.assertEqual(len(numbers), len(set(numbers)), "duplicate section numbers")

    def test_every_pick_shows_entry_stop_and_all_three_targets(self):
        for pick in self.picks:
            entry = pick.execution_entry or pick.optimal_entry or pick.entry_price
            for label, price in (("entry", entry), ("stop", pick.stop_loss),
                                 ("T1", pick.primary_target),
                                 ("T2", pick.secondary_target),
                                 ("T3", pick.aggressive_target)):
                self.assertIn(f"${price:,.2f}", self.output,
                              f"{pick.ticker} {label} {price} missing from the report")
            # Levels must be ordered and the stop below the entry.
            self.assertLess(pick.stop_loss, entry)
            self.assertLess(entry, pick.primary_target)
            self.assertLess(pick.primary_target, pick.secondary_target)
            self.assertLess(pick.secondary_target, pick.aggressive_target)

    def test_factor_grades_are_assigned_and_legible(self):
        valid = set("A+ A A- B+ B B- C+ C C- D+ D D- F".split())
        for pick in self.picks:
            self.assertEqual(set(pick.factor_grades),
                             set(sp_shortterm.StrategicStockAnalyzer.FACTOR_NAMES))
            for name, grade in pick.factor_grades.items():
                self.assertIn(grade, valid, f"{pick.ticker} {name} -> {grade!r}")
            self.assertIn(pick.grade_basis, ("sector", "universe"))
            self.assertTrue(pick.quant_rating)
            self.assertTrue(pick.thesis, f"{pick.ticker} has no thesis")

    def test_reported_capital_equals_the_sum_of_executable_allocations(self):
        deployed = sum(p.executable_allocation_usd for p in self.picks)
        self.assertIn(f"${deployed:,.2f}", self.output)
        for pick in self.picks:
            self.assertAlmostEqual(
                pick.executable_allocation_usd,
                pick.shares * pick.execution_entry, places=2)

    def test_portfolio_heat_respects_the_budget(self):
        heat = sum(p.risk_amount for p in self.picks)
        self.assertLessEqual(
            heat, self.analyzer.capital * sp_shortterm.Config.MAX_PORTFOLIO_RISK + 0.01)

    def test_scale_out_shares_never_exceed_the_position(self):
        for pick in self.picks:
            first, runner = self.analyzer._scale_out_plan(pick)
            self.assertGreaterEqual(first, 0)
            self.assertGreaterEqual(runner, 0)
            self.assertAlmostEqual(first + runner, pick.shares, places=2)

    def test_exports_are_written_and_the_markdown_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                self.analyzer.export_results(self.picks, output_dir=tmp)
            written = sorted(os.listdir(tmp))
            for suffix in (".json", ".csv", ".md"):
                self.assertTrue(any(f.endswith(suffix) for f in written),
                                f"no {suffix} export in {written}")
            markdown = Path(tmp, next(f for f in written if f.endswith(".md"))).read_text(encoding="utf-8")
        for heading in ("## Market context", "## Trade levels",
                        "## Quant factor grades", "## Execution tickets"):
            self.assertIn(heading, markdown)
        for pick in self.picks:
            self.assertIn(pick.ticker, markdown)
            # Every level a reader would act on must survive into the export.
            entry = pick.execution_entry or pick.optimal_entry or pick.entry_price
            for price in (entry, pick.stop_loss, pick.primary_target,
                          pick.secondary_target, pick.aggressive_target):
                self.assertIn(f"${price:,.2f}", markdown)

    def test_empty_result_set_renders_the_no_positions_branch(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.analyzer.display_results([])
        self.assertIn("NO FUNDED POSITIONS", buffer.getvalue())

def _wiki_page(rows, ticker_header="Ticker"):
    """A minimal Wikipedia-style constituents page."""
    body = "".join(f"<tr><td>{t}</td><td>Co {t}</td></tr>" for t in rows)
    return ("<html><body><table class='wikitable'>"
            f"<tr><th>{ticker_header}</th><th>Company</th></tr>{body}"
            "</table></body></html>")


class TestConstituentScraping(unittest.TestCase):
    """The universe builder is the one path all the other tests stubbed out.

    A silently-shrunken universe is the worst failure mode here: the scan still
    reports success, just over a fraction of the market.
    """

    def setUp(self):
        sp_shortterm.EnhancedMarketData._sp500_cache = []
        sp_shortterm.EnhancedMarketData._nasdaq100_cache = []
        sp_shortterm.EnhancedMarketData.sp500_degraded = False
        sp_shortterm.EnhancedMarketData.nasdaq100_degraded = False

    tearDown = setUp

    def _session_returning(self, pages):
        """Fake session: maps URL -> html, 404s anything else."""
        def get(url, *args, **kwargs):
            response = Mock()
            if url in pages:
                response.text = pages[url]
                response.raise_for_status = Mock()
            else:
                response.text = ""
                response.raise_for_status = Mock(
                    side_effect=RuntimeError(f"404 for {url}"))
            return response
        session = Mock()
        session.get = get
        return session

    def test_literal_html_is_parsed_not_treated_as_a_path(self):
        # pandas >= 3.0 treats a bare HTML string as a FILE PATH and raises
        # FileNotFoundError with the WHOLE document in the message. Without the
        # StringIO wrapper every run fell back to the built-in list and dumped
        # an entire Wikipedia page onto the console.
        rows = [f"AA{i:02d}" for i in range(120)]
        market_data = sp_shortterm.EnhancedMarketData()
        market_data.session = self._session_returning(
            {sp_shortterm.Config.SP500_URLS[0]: _wiki_page(rows)})
        tickers = market_data.get_sp500_tickers()
        self.assertEqual(len(tickers), 120)
        self.assertFalse(sp_shortterm.EnhancedMarketData.sp500_degraded)

    def test_nasdaq_falls_through_to_the_dedicated_list_page(self):
        # Wikipedia moved the membership table off the index article, leaving
        # it with no parseable table at all.
        rows = [f"NN{i:02d}" for i in range(80)]
        pages = {sp_shortterm.Config.NASDAQ100_URLS[0]: _wiki_page(rows)}
        market_data = sp_shortterm.EnhancedMarketData()
        market_data.session = self._session_returning(pages)
        tickers = market_data.get_nasdaq100_tickers()
        self.assertEqual(len(tickers), 80)
        self.assertFalse(sp_shortterm.EnhancedMarketData.nasdaq100_degraded)

    def test_class_share_tickers_are_normalised_for_yahoo(self):
        rows = ["BRK.B", "BF.B"] + [f"AA{i:02d}" for i in range(120)]
        market_data = sp_shortterm.EnhancedMarketData()
        market_data.session = self._session_returning(
            {sp_shortterm.Config.SP500_URLS[0]: _wiki_page(rows)})
        tickers = market_data.get_sp500_tickers()
        self.assertIn("BRK-B", tickers)
        self.assertNotIn("BRK.B", tickers)

    def test_a_short_parse_counts_as_failure_not_as_an_index(self):
        # A page-layout change that yields three symbols must not masquerade
        # as the S&P 500.
        market_data = sp_shortterm.EnhancedMarketData()
        market_data.session = self._session_returning(
            {sp_shortterm.Config.SP500_URLS[0]: _wiki_page(["AAPL", "MSFT", "NVDA"])})
        tickers = market_data.get_sp500_tickers()
        self.assertEqual(tickers, market_data._get_fallback_sp500())
        self.assertTrue(sp_shortterm.EnhancedMarketData.sp500_degraded)

    def test_total_failure_degrades_loudly_and_still_returns_a_universe(self):
        market_data = sp_shortterm.EnhancedMarketData()
        market_data.session = self._session_returning({})
        with self.assertLogs(sp_shortterm.logger, level="WARNING"):
            self.assertEqual(market_data.get_sp500_tickers(),
                             market_data._get_fallback_sp500())
        self.assertTrue(sp_shortterm.EnhancedMarketData.sp500_degraded)

    def test_exception_text_is_bounded_so_a_payload_never_reaches_the_console(self):
        payload = "<html>" + "x" * 200_000 + "</html>"
        rendered = sp_shortterm._brief(RuntimeError(payload))
        self.assertLess(len(rendered), 400)
        self.assertIn("chars]", rendered)

    def test_degradation_is_reported_in_the_universe_summary(self):
        analyzer = _bare_analyzer()
        analyzer.market_data = sp_shortterm.EnhancedMarketData()
        analyzer.market_data.session = self._session_returning({})
        analyzer.registry = sp_shortterm.TickerRegistry.load()
        analyzer.diagnostics = sp_shortterm.ScanDiagnostics()
        analyzer.hygiene_summary = {}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertLogs(sp_shortterm.logger, level="WARNING"):
                universe = analyzer.build_universe("comprehensive")
        self.assertTrue(universe)
        self.assertIn("UNIVERSE DEGRADED", buffer.getvalue())
        self.assertTrue(analyzer.hygiene_summary["sp500_degraded"])
        self.assertTrue(analyzer.universe_degraded)

    def test_healthy_fetch_reports_no_degradation(self):
        analyzer = _bare_analyzer()
        analyzer.market_data = sp_shortterm.EnhancedMarketData()
        analyzer.market_data.session = self._session_returning({
            sp_shortterm.Config.SP500_URLS[0]:
                _wiki_page([f"AA{i:03d}" for i in range(300)]),
            sp_shortterm.Config.NASDAQ100_URLS[0]:
                _wiki_page([f"NN{i:02d}" for i in range(90)], "Symbol"),
        })
        analyzer.registry = sp_shortterm.TickerRegistry.load()
        analyzer.diagnostics = sp_shortterm.ScanDiagnostics()
        analyzer.hygiene_summary = {}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            universe = analyzer.build_universe("comprehensive")
        self.assertNotIn("UNIVERSE DEGRADED", buffer.getvalue())
        self.assertFalse(analyzer.universe_degraded)
        # 300 + 90 index names plus the curated momentum list, capped at 400.
        self.assertEqual(len(universe), 400)

class TestPercentileGrading(unittest.TestCase):
    def test_percentile_is_midpoint_and_bounded(self):
        pop = [10.0, 20.0, 30.0, 40.0]
        self.assertAlmostEqual(sp_shortterm.percentile_of(5.0, pop), 0.0)
        self.assertAlmostEqual(sp_shortterm.percentile_of(50.0, pop), 100.0)
        self.assertAlmostEqual(sp_shortterm.percentile_of(30.0, pop), 62.5)

    def test_empty_or_all_nan_population_is_neutral(self):
        self.assertEqual(sp_shortterm.percentile_of(42.0, []), 50.0)
        self.assertEqual(
            sp_shortterm.percentile_of(42.0, [float("nan"), float("inf")]), 50.0)

    def test_grades_span_the_scale_and_are_monotonic(self):
        self.assertEqual(sp_shortterm.grade_for(99.0), "A+")
        self.assertEqual(sp_shortterm.grade_for(50.0), "C+")
        self.assertEqual(sp_shortterm.grade_for(0.0), "F")
        ranks = [sp_shortterm.grade_rank(sp_shortterm.grade_for(p))
                 for p in range(0, 101, 5)]
        self.assertEqual(ranks, sorted(ranks))


class TestSectorRelativeGrading(unittest.TestCase):
    """A P/E of 30 is expensive for a utility and unremarkable for a chipmaker,
    so factors are graded against peers, never against a fixed band."""

    def _analyzer_with_samples(self, samples):
        analyzer = _bare_analyzer()
        analyzer._factor_samples = samples
        analyzer._factor_lock = None
        return analyzer

    @staticmethod
    def _sample(sector, **scores):
        base = dict(Value=50.0, Growth=50.0, Profitability=50.0,
                    Momentum=50.0, Technicals=50.0, Composite=65.0)
        base.update(scores)
        base["sector"] = sector
        return base

    def test_identical_score_grades_differently_in_different_sectors(self):
        # Same raw Value score, cheap sector vs expensive sector.
        samples = ([self._sample("Utilities", Value=90.0) for _ in range(10)]
                   + [self._sample("Technology", Value=10.0) for _ in range(10)])
        analyzer = self._analyzer_with_samples(samples)
        util = _minimal_setup(ticker="UTIL", sector="Utilities", value_score=50.0)
        tech = _minimal_setup(ticker="TECH", sector="Technology", value_score=50.0)
        analyzer._assign_factor_grades([util, tech])
        self.assertEqual(util.grade_basis, "sector")
        self.assertEqual(tech.grade_basis, "sector")
        self.assertLess(util.factor_percentiles["Value"],
                        tech.factor_percentiles["Value"])
        self.assertLess(sp_shortterm.grade_rank(util.factor_grades["Value"]),
                        sp_shortterm.grade_rank(tech.factor_grades["Value"]))

    def test_thin_sector_falls_back_to_the_universe_and_says_so(self):
        samples = ([self._sample("Technology", Value=20.0) for _ in range(20)]
                   + [self._sample("Energy", Value=20.0) for _ in range(3)])
        analyzer = self._analyzer_with_samples(samples)
        setup = _minimal_setup(ticker="ENER", sector="Energy", value_score=90.0)
        analyzer._assign_factor_grades([setup])
        self.assertEqual(setup.grade_basis, "universe")
        self.assertEqual(setup.sector_peers, len(samples))

    def test_all_five_factors_are_graded(self):
        samples = [self._sample("Technology") for _ in range(12)]
        analyzer = self._analyzer_with_samples(samples)
        setup = _minimal_setup(sector="Technology")
        analyzer._assign_factor_grades([setup])
        self.assertEqual(set(setup.factor_grades),
                         set(sp_shortterm.StrategicStockAnalyzer.FACTOR_NAMES))
        self.assertTrue(setup.quant_rating)

    def test_rank_reflects_composite_standing(self):
        samples = [self._sample("Technology", Composite=90.0) for _ in range(9)]
        analyzer = self._analyzer_with_samples(samples)
        weak = _minimal_setup(ticker="WEAK", sector="Technology", overall_score=61.0)
        analyzer._assign_factor_grades([weak])
        self.assertEqual(weak.sector_rank, 10)  # all nine peers score higher

    def test_grading_is_a_no_op_without_samples(self):
        analyzer = self._analyzer_with_samples([])
        setup = _minimal_setup()
        analyzer._assign_factor_grades([setup])
        self.assertEqual(setup.factor_grades, {})

    def test_quant_rating_tiers_are_ordered(self):
        rate = sp_shortterm.StrategicStockAnalyzer._quant_rating
        top = rate({k: 99.0 for k in sp_shortterm.StrategicStockAnalyzer.FACTOR_NAMES}, 99.0)
        mid = rate({k: 50.0 for k in sp_shortterm.StrategicStockAnalyzer.FACTOR_NAMES}, 50.0)
        low = rate({k: 5.0 for k in sp_shortterm.StrategicStockAnalyzer.FACTOR_NAMES}, 5.0)
        self.assertEqual(top, "STRONG BUY")
        self.assertEqual(mid, "HOLD")
        self.assertEqual(low, "AVOID")
        self.assertEqual(rate({}, 50.0), "UNRATED")


class TestFactorSubScores(unittest.TestCase):
    def test_cheaper_multiples_score_higher_on_value(self):
        analyzer = _bare_analyzer()
        cheap = {"trailingPE": 9.0, "forwardPE": 8.0, "pegRatio": 0.7,
                 "priceToSalesTrailing12Months": 1.2, "priceToBook": 1.4}
        rich = {"trailingPE": 60.0, "forwardPE": 55.0, "pegRatio": 4.0,
                "priceToSalesTrailing12Months": 20.0, "priceToBook": 12.0}
        self.assertGreater(analyzer._calculate_value_score(cheap),
                           analyzer._calculate_value_score(rich))

    def test_faster_growth_and_fatter_margins_score_higher(self):
        analyzer = _bare_analyzer()
        fast = {"revenueGrowth": 0.40, "earningsGrowth": 0.35,
                "earningsQuarterlyGrowth": 0.30}
        slow = {"revenueGrowth": 0.01, "earningsGrowth": -0.10,
                "earningsQuarterlyGrowth": -0.05}
        self.assertGreater(analyzer._calculate_growth_score(fast),
                           analyzer._calculate_growth_score(slow))
        rich = {"profitMargins": 0.30, "operatingMargins": 0.28,
                "returnOnEquity": 0.30, "freeCashflow": 5e9}
        thin = {"profitMargins": 0.01, "operatingMargins": 0.02,
                "returnOnEquity": 0.03, "freeCashflow": -1e8}
        self.assertGreater(analyzer._calculate_profitability_score(rich),
                           analyzer._calculate_profitability_score(thin))

    def test_missing_payload_is_neutral_not_zero(self):
        analyzer = _bare_analyzer()
        for score in (analyzer._calculate_value_score({}),
                      analyzer._calculate_growth_score({}),
                      analyzer._calculate_profitability_score({})):
            self.assertAlmostEqual(score, 50.0)

    def test_null_valued_fields_do_not_raise(self):
        analyzer = _bare_analyzer()
        hostile = {"trailingPE": None, "pegRatio": None, "revenueGrowth": None,
                   "profitMargins": None, "freeCashflow": None}
        for score in (analyzer._calculate_value_score(hostile),
                      analyzer._calculate_growth_score(hostile),
                      analyzer._calculate_profitability_score(hostile)):
            self.assertTrue(0.0 <= score <= 100.0)


class TestNarrative(unittest.TestCase):
    def test_thesis_and_risks_only_state_what_the_data_supports(self):
        analyzer = _bare_analyzer()
        analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
        strong = _minimal_setup(
            ticker="GOOD", trend_strength="Strong Uptrend", adx=30.0,
            relative_strength=88.0, volume_ratio=2.1, rsi=55.0,
            volatility_20d=22.0, distance_from_high_pct=-12.0,
            avg_dollar_volume=500e6, detected_patterns=["Golden Cross (Recent)"])
        strong.factor_grades = {"Value": "A", "Growth": "A-", "Profitability": "B+",
                                "Momentum": "A", "Technicals": "B"}
        strong.sector_peers, strong.grade_basis = 40, "sector"
        thesis = analyzer.build_thesis(strong)
        self.assertTrue(thesis)
        self.assertTrue(any("Relative strength" in t for t in thesis))
        self.assertEqual(analyzer.build_risks(strong), [])

    def test_weak_setup_surfaces_concrete_risks(self):
        analyzer = _bare_analyzer()
        analyzer.market_regime = sp_shortterm.MarketRegime.BEAR_WEAK
        weak = _minimal_setup(
            ticker="BAD", rsi=82.0, adx=9.0, volatility_20d=70.0,
            distance_from_high_pct=-0.4, avg_dollar_volume=8e6,
            days_to_earnings=2, info_missing=True)
        weak.factor_grades = {"Value": "F", "Growth": "D", "Profitability": "D-",
                              "Momentum": "C", "Technicals": "C"}
        weak.sector_peers, weak.grade_basis = 30, "sector"
        risks = analyzer.build_risks(weak)
        self.assertTrue(risks)
        joined = " ".join(risks)
        self.assertIn("overbought", joined)
        self.assertLessEqual(len(risks), 4)  # stays readable

class TestTradingCalendar(unittest.TestCase):
    """Holding periods are quoted as dates, so the calendar has to be right."""

    # Published NYSE full-day closures.
    KNOWN = {
        2025: {(1, 1), (1, 20), (2, 17), (4, 18), (5, 26), (6, 19), (7, 4),
               (9, 1), (11, 27), (12, 25)},
        2026: {(1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3),
               (9, 7), (11, 26), (12, 25)},
        2027: {(1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5),
               (9, 6), (11, 25), (12, 24)},
    }

    def test_holidays_match_the_published_nyse_calendar(self):
        for year, expected in self.KNOWN.items():
            got = {(d.month, d.day) for d in sp_shortterm.us_market_holidays(year)}
            self.assertEqual(got, expected, f"{year} holiday set differs")

    def test_good_friday_is_two_days_before_easter(self):
        for year in (2025, 2026, 2027, 2030):
            easter = sp_shortterm.easter_sunday(year)
            self.assertEqual(easter.weekday(), 6, f"{year} Easter is not a Sunday")
            good_friday = easter - datetime_module.timedelta(days=2)
            self.assertIn(good_friday, sp_shortterm.us_market_holidays(year))

    def test_saturday_new_year_is_not_observed_on_the_friday(self):
        # 2028-01-01 is a Saturday; the NYSE does not close on 2027-12-31.
        holidays = sp_shortterm.us_market_holidays(2028)
        self.assertNotIn(datetime_module.date(2028, 1, 1), holidays)
        self.assertNotIn(datetime_module.date(2027, 12, 31),
                         sp_shortterm.us_market_holidays(2027))

    def test_weekends_and_holidays_are_not_trading_days(self):
        self.assertFalse(sp_shortterm.is_trading_day(datetime_module.date(2026, 9, 5)))
        self.assertFalse(sp_shortterm.is_trading_day(datetime_module.date(2026, 9, 6)))
        self.assertFalse(sp_shortterm.is_trading_day(datetime_module.date(2026, 9, 7)))
        self.assertTrue(sp_shortterm.is_trading_day(datetime_module.date(2026, 9, 8)))

    def test_add_trading_days_skips_weekends_and_holidays(self):
        friday = datetime_module.date(2026, 9, 4)
        self.assertEqual(sp_shortterm.add_trading_days(friday, 1),
                         datetime_module.date(2026, 9, 8))   # Labor Day skipped
        self.assertEqual(sp_shortterm.add_trading_days(friday, 0), friday)
        saturday = datetime_module.date(2026, 9, 5)
        self.assertEqual(sp_shortterm.add_trading_days(saturday, 0),
                         datetime_module.date(2026, 9, 8))

    def test_add_trading_days_is_monotonic_and_lands_on_sessions(self):
        start = datetime_module.date(2026, 8, 31)
        previous = None
        for n in range(0, 60):
            day = sp_shortterm.add_trading_days(start, n)
            self.assertTrue(sp_shortterm.is_trading_day(day))
            if previous is not None:
                self.assertGreaterEqual(day, previous)
            previous = day

    def test_format_horizon_names_a_real_session(self):
        text = sp_shortterm.format_horizon(10, datetime_module.date(2026, 8, 31))
        self.assertIn("10 sessions", text)
        self.assertIn("Sep", text)


class TestScheduleAttachment(unittest.TestCase):
    """Every target gets its own ETA; further targets are never dated sooner."""

    def _analyzer(self):
        analyzer = _bare_analyzer()
        analyzer.analysis_date = datetime_module.datetime(2026, 8, 31, 10, 0)
        return analyzer

    def test_targets_are_dated_in_order(self):
        analyzer = self._analyzer()
        setup = _minimal_setup(execution_entry=100.0, stop_loss=95.0,
                               primary_target=110.0, secondary_target=120.0,
                               aggressive_target=135.0, max_hold_days=20)
        analyzer._attach_schedule(setup, {"ATR": 2.0, "Volatility_20D": 0.25}, "BUY")
        etas = [setup.eta_sessions[k] for k in ("T1", "T2", "T3")]
        self.assertEqual(etas, sorted(etas))
        self.assertEqual(len(set(etas)), 3, "distinct targets need distinct ETAs")
        for label in ("T1", "T2", "T3"):
            day = datetime_module.datetime.strptime(
                setup.eta_dates[label], "%Y-%m-%d").date()
            self.assertTrue(sp_shortterm.is_trading_day(day))

    def test_close_by_date_honours_the_time_stop(self):
        analyzer = self._analyzer()
        setup = _minimal_setup(execution_entry=100.0, stop_loss=95.0,
                               primary_target=110.0, secondary_target=120.0,
                               aggressive_target=135.0, max_hold_days=15)
        analyzer._attach_schedule(setup, {"ATR": 2.0, "Volatility_20D": 0.25}, "BUY")
        expected = sp_shortterm.add_trading_days(
            datetime_module.date(2026, 8, 31), 15).strftime("%Y-%m-%d")
        self.assertEqual(setup.time_stop_date, expected)

    def test_breakeven_win_rate_is_the_reward_risk_arithmetic(self):
        analyzer = self._analyzer()
        for rr, expected in ((1.0, 50.0), (2.0, 33.3), (3.0, 25.0)):
            setup = _minimal_setup(execution_entry=100.0, stop_loss=95.0,
                                   primary_target=110.0, secondary_target=120.0,
                                   aggressive_target=135.0, risk_reward_ratio=rr)
            analyzer._attach_schedule(setup, {"ATR": 2.0}, "BUY")
            self.assertAlmostEqual(setup.breakeven_win_rate, expected, places=1)

    def test_unusable_geometry_produces_no_fabricated_dates(self):
        analyzer = self._analyzer()
        setup = _minimal_setup(execution_entry=100.0, stop_loss=95.0,
                               primary_target=90.0, secondary_target=80.0,
                               aggressive_target=70.0)
        analyzer._attach_schedule(setup, {"ATR": 0.0}, "BUY")
        self.assertEqual(setup.eta_sessions, {})
        self.assertEqual(setup.eta_dates, {})

    def test_further_targets_take_longer(self):
        near = sp_shortterm.StrategicStockAnalyzer.sessions_to_target(100, 105, 2.0)
        far = sp_shortterm.StrategicStockAnalyzer.sessions_to_target(100, 130, 2.0)
        self.assertLess(near, far)
        self.assertIsNone(
            sp_shortterm.StrategicStockAnalyzer.sessions_to_target(100, 90, 2.0))


class TestSetupValidator(unittest.TestCase):
    """Nothing reaches the report whose arithmetic does not reconcile."""

    def _consistent(self):
        setup = _minimal_setup(
            execution_entry=100.0, entry_price=100.0, entry_zone_low=98.0,
            entry_zone_high=102.0, stop_loss=90.0, primary_target=120.3,
            secondary_target=130.0, aggressive_target=140.0,
            shares=10, is_funded=True, executable_allocation_usd=1000.0,
            risk_amount=100.0, risk_pct=1.0, risk_reward_ratio=2.0,
            min_hold_days=3, expected_hold_days=8, max_hold_days=20)
        setup.eta_sessions = {"T1": 5, "T2": 9, "T3": 14}
        return setup

    def test_a_consistent_setup_passes(self):
        self.assertEqual(sp_shortterm.validate_setup(self._consistent(), 10_000.0), [])

    def test_stop_above_entry_is_caught(self):
        setup = self._consistent()
        setup.stop_loss = 105.0
        self.assertTrue(any("not below entry" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_out_of_order_targets_are_caught(self):
        setup = self._consistent()
        setup.secondary_target = 110.0     # now below T1
        self.assertTrue(any("out of order" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_allocation_that_does_not_equal_shares_times_entry_is_caught(self):
        setup = self._consistent()
        setup.executable_allocation_usd = 1234.0
        self.assertTrue(any("capital" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_risk_that_does_not_equal_shares_times_stop_distance_is_caught(self):
        setup = self._consistent()
        setup.risk_amount = 42.0
        self.assertTrue(any("risk $" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_breaching_the_per_trade_risk_cap_is_caught(self):
        setup = self._consistent()
        problems = sp_shortterm.validate_setup(setup, 1_000.0)  # $100 risk on $1k
        self.assertTrue(any("per-trade cap" in p for p in problems))

    def test_misreported_reward_risk_is_caught(self):
        setup = self._consistent()
        setup.risk_reward_ratio = 5.0
        self.assertTrue(any("recomputed" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_limit_outside_its_own_zone_is_caught(self):
        setup = self._consistent()
        setup.entry_zone_high = 99.0
        self.assertTrue(any("outside its own zone" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_unordered_target_etas_are_caught(self):
        setup = self._consistent()
        setup.eta_sessions = {"T1": 12, "T2": 4, "T3": 20}
        self.assertTrue(any("ETAs are not in order" in p
                            for p in sp_shortterm.validate_setup(setup, 10_000.0)))

    def test_a_failing_pick_is_withheld_from_the_report(self):
        analyzer = _bare_analyzer(capital=10_000)
        analyzer.analysis_date = datetime_module.datetime(2026, 8, 31, 10, 0)
        analyzer.market_regime = sp_shortterm.MarketRegime.BULL_STRONG
        analyzer.scan_diagnostics = {}
        bad = self._consistent()
        bad.ticker = "BROKEN"
        bad.stop_loss = 130.0            # above every target
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertLogs(sp_shortterm.logger, level="ERROR"):
                analyzer.display_results([bad])
        self.assertIn("consistency check", buffer.getvalue())
        self.assertNotIn("BUY LIMIT", buffer.getvalue())


class TestReportTimingAndInstructions(unittest.TestCase):
    """The timing section and execution steps are part of the deliverable."""

    def setUp(self):
        self.analyzer = _bare_analyzer(capital=50_000)
        self.analyzer.analysis_date = datetime_module.datetime(2026, 8, 31, 10, 0)
        self.setup = _minimal_setup(
            ticker="ZZZ", execution_entry=100.0, entry_price=100.0,
            entry_zone_low=98.0, entry_zone_high=102.0, stop_loss=90.0,
            primary_target=120.3, secondary_target=130.0, aggressive_target=140.0,
            shares=10, is_funded=True, executable_allocation_usd=1000.0,
            risk_amount=100.0, risk_pct=0.2, risk_reward_ratio=2.0,
            trailing_stop_pct=6.0, max_hold_days=20)
        self.analyzer._attach_schedule(self.setup, {"ATR": 2.0}, "BUY")

    def test_execution_steps_are_ordered_and_actionable(self):
        steps = self.analyzer._execution_steps(self.setup)
        self.assertGreaterEqual(len(steps), 4)
        self.assertIn("BUY LIMIT", steps[0])
        self.assertIn("$100.00", steps[0])
        self.assertIn("STOP", steps[1])
        self.assertIn("$90.00", steps[1])
        self.assertTrue(any("risk-free" in s for s in steps))
        self.assertTrue(any(self.setup.time_stop_date in s for s in steps))

    def test_single_share_position_is_not_told_to_scale_out(self):
        self.setup.shares = 1
        steps = self.analyzer._execution_steps(self.setup)
        self.assertTrue(any("Single-share" in s for s in steps))
        self.assertFalse(any("raise the stop" in s for s in steps))

    def test_timing_table_shows_a_date_for_every_target(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.analyzer._display_timing([self.setup])
        output = buffer.getvalue()
        self.assertIn("Close by", output)
        self.assertIn(self.setup.time_stop_date, output)
        for label in ("T1", "T2", "T3"):
            when = datetime_module.datetime.strptime(
                self.setup.eta_dates[label], "%Y-%m-%d")
            self.assertIn(f"{when:%d %b}", output)

if __name__ == "__main__":
    unittest.main()
