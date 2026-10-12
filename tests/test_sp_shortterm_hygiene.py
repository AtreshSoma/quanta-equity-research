"""Quality-assurance tests for the APEX Short-Term analyzer hardening pass.

Covers ticker hygiene (aliases / stale exclusion), provider-error
classification, scan diagnostics accounting, low-conviction labeling, the
PM-review table, the scalp-interval fix, and degraded benchmark handling.

Loaded via importlib so the test runs against the file on disk without
requiring the package to be importable on sys.path.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE.parent / "core" / "SP_ShortTerm.py"
_SPEC = importlib.util.spec_from_file_location("sp_shortterm_hygiene", _MODULE_PATH)
sp = importlib.util.module_from_spec(_SPEC)
sys.modules["sp_shortterm_hygiene"] = sp
assert _SPEC.loader is not None
_SPEC.loader.exec_module(sp)


def _setup(**overrides):
    """Build a minimally-populated TradingSetup for overlay/review tests."""
    defaults = dict(
        ticker="TEST", company="Test Co", sector="Technology", industry="Software",
        signal="BUY", confidence=80.0, overall_score=75.0,
        technical_score=70.0, fundamental_score=60.0, momentum_score=70.0,
        quality_score=65.0, analyst_score=60.0, pattern_score=55.0,
        entry_price=100.0, entry_zone_low=99.0, entry_zone_high=101.0,
        optimal_entry=100.0, primary_target=110.0, secondary_target=115.0,
        aggressive_target=120.0, stop_loss=95.0, trailing_stop_pct=5.0,
        timeframe="swing", expected_hold_days=10, min_hold_days=3, max_hold_days=20,
        allocation_usd=10000.0, allocation_pct=10.0, shares=100, position_score=80.0,
        risk_amount=500.0, risk_pct=0.5, reward_amount=1000.0,
        risk_reward_ratio=2.0, expected_return_pct=10.0, max_loss_pct=5.0,
        rsi=55.0, rsi_trend="up", macd=0.5, macd_signal=0.3, macd_histogram=0.2,
        stochastic=50.0, bb_position=0.5, atr=2.0, atr_pct=2.0,
        volume_ratio=1.2, obv_trend="up",
        current_price=100.0, price_vs_sma20=1.0, price_vs_sma50=2.0,
        price_vs_sma200=5.0, high_52w=120.0, low_52w=70.0,
        distance_from_high_pct=-16.0, distance_from_low_pct=42.0,
    )
    defaults.update(overrides)
    return sp.TradingSetup(**defaults)


class TestTickerRegistry(unittest.TestCase):
    def test_alias_remaps_renamed_ticker(self):
        reg = sp.TickerRegistry()
        resolved, classification = reg.resolve("SQ")
        self.assertEqual(resolved, "XYZ")
        self.assertEqual(classification, "alias_replaced")
        self.assertEqual(reg.original_for("XYZ"), "SQ")

    def test_stale_ticker_excluded(self):
        reg = sp.TickerRegistry()
        resolved, classification = reg.resolve("TWTR")
        self.assertIsNone(resolved)
        self.assertEqual(classification, "delisted_excluded")
        self.assertTrue(reg.is_stale("twtr"))

    def test_active_ticker_passes_through(self):
        reg = sp.TickerRegistry()
        resolved, classification = reg.resolve("aapl")
        self.assertEqual(resolved, "AAPL")
        self.assertEqual(classification, "active")

    def test_empty_ticker_is_invalid(self):
        reg = sp.TickerRegistry()
        resolved, classification = reg.resolve("  ")
        self.assertIsNone(resolved)
        self.assertEqual(classification, "invalid")


class TestProviderErrorClassification(unittest.TestCase):
    def test_auth_error(self):
        self.assertEqual(
            sp.classify_provider_error("401 Client Error: Invalid Crumb"),
            sp.Outcome.PROVIDER_AUTH_ERROR,
        )

    def test_rate_limited(self):
        self.assertEqual(
            sp.classify_provider_error("429 Too Many Requests"),
            sp.Outcome.PROVIDER_RATE_LIMITED,
        )

    def test_timeout(self):
        self.assertEqual(
            sp.classify_provider_error("Read timed out"),
            sp.Outcome.PROVIDER_TIMEOUT,
        )

    def test_unsupported_interval(self):
        self.assertEqual(
            sp.classify_provider_error("3m data not supported, valid intervals are ..."),
            sp.Outcome.UNSUPPORTED_INTERVAL,
        )

    def test_unknown_returns_none(self):
        self.assertIsNone(sp.classify_provider_error("something unrelated"))


class TestScanDiagnostics(unittest.TestCase):
    def test_provider_failure_not_counted_as_success(self):
        diag = sp.ScanDiagnostics()
        diag.record("AAA", sp.Outcome.PROVIDER_AUTH_ERROR)
        diag.record("BBB", sp.Outcome.PROVIDER_TIMEOUT)
        summary = diag.summary(universe_count=2)
        self.assertEqual(summary["successfully_analyzed"], 0)
        self.assertEqual(summary["provider_failures"], 2)
        self.assertEqual(summary["true_success_rate"], 0.0)

    def test_data_ok_then_active_and_filtered(self):
        diag = sp.ScanDiagnostics()
        diag.mark_data_ok("AAA")   # data ok, provisionally filtered
        diag.mark_data_ok("BBB")
        diag.record("BBB", sp.Outcome.ACTIVE)  # BBB produced a setup
        summary = diag.summary(universe_count=2)
        self.assertEqual(summary["successfully_analyzed"], 2)
        self.assertEqual(summary["opportunities_found"], 1)
        self.assertEqual(summary["model_filtered"], 1)
        self.assertEqual(summary["true_success_rate"], 100.0)

    def test_retry_overwrite_is_idempotent(self):
        diag = sp.ScanDiagnostics()
        diag.record("AAA", sp.Outcome.PROVIDER_AUTH_ERROR)
        # Retry succeeds for the same ticker.
        diag.mark_data_ok("AAA")
        diag.record("AAA", sp.Outcome.ACTIVE)
        summary = diag.summary(universe_count=1)
        self.assertEqual(summary["provider_failures"], 0)
        self.assertEqual(summary["opportunities_found"], 1)


class TestConvictionOverlay(unittest.TestCase):
    def _analyzer(self):
        analyzer = object.__new__(sp.StrategicStockAnalyzer)
        analyzer.capital = 100000.0
        analyzer.market_regime = sp.MarketRegime.BULL_STRONG
        return analyzer

    def test_low_conviction_flags_and_halves_size(self):
        analyzer = self._analyzer()
        weak = _setup(
            overall_score=66.0,        # marginal (< MIN_SCORE + buffer)
            rsi=75.0,                  # overbought
            relative_strength=30.0,    # weak RS
            allocation_usd=10000.0, shares=100, allocation_pct=10.0,
        )
        analyzer._apply_conviction_overlay([weak])
        self.assertEqual(weak.conviction, "LOW")
        self.assertTrue(weak.conviction_reasons)
        self.assertEqual(weak.shares, 50)              # halved
        self.assertAlmostEqual(weak.allocation_usd, 5000.0)

    def test_strong_setup_stays_normal(self):
        analyzer = self._analyzer()
        strong = _setup(
            overall_score=88.0, rsi=55.0, relative_strength=85.0,
            volatility_20d=20.0,
        )
        analyzer._apply_conviction_overlay([strong])
        self.assertEqual(strong.conviction, "NORMAL")
        self.assertEqual(strong.shares, 100)


class TestFinalPickReview(unittest.TestCase):
    def test_overbought_thin_liquidity_flagged(self):
        analyzer = object.__new__(sp.StrategicStockAnalyzer)
        bad = _setup(
            ticker="RISK", rsi=78.0, avg_dollar_volume=5_000_000,
            risk_reward_ratio=1.2, relative_strength=30.0,
        )
        rows = analyzer._validate_final_picks([bad])
        self.assertEqual(rows[0]["status"], "REVIEW")
        self.assertIn("Overbought", rows[0]["issue"])
        self.assertIn("liquidity", rows[0]["issue"].lower())

    def test_clean_pick_passes(self):
        analyzer = object.__new__(sp.StrategicStockAnalyzer)
        good = _setup(
            ticker="CLEAN", rsi=55.0, avg_dollar_volume=200_000_000,
            risk_reward_ratio=3.0, relative_strength=80.0,
        )
        rows = analyzer._validate_final_picks([good])
        self.assertEqual(rows[0]["status"], "PASS")
        self.assertEqual(rows[0]["issue"], "None")


class TestScalpIntervalSupported(unittest.TestCase):
    def test_scalp_interval_is_yahoo_supported(self):
        valid = {"1m", "2m", "5m", "15m", "30m", "60m", "90m"}
        self.assertIn(sp.Config.SCALP_INTERVAL, valid)
        self.assertNotEqual(sp.Config.SCALP_INTERVAL, "3m")

    def test_compute_scalping_method_renamed(self):
        self.assertTrue(hasattr(sp.StrategicStockAnalyzer, "_compute_scalping_playbook"))
        self.assertFalse(hasattr(sp.StrategicStockAnalyzer, "_compute_3m_scalping_playbook"))


class TestDegradedBenchmark(unittest.TestCase):
    def test_blocked_spy_sets_degraded_and_none(self):
        analyzer = object.__new__(sp.StrategicStockAnalyzer)

        class _BlockedTicker:
            def history(self, *args, **kwargs):
                raise RuntimeError("401 Invalid Crumb")

        with patch.object(sp.yf, "Ticker", return_value=_BlockedTicker()):
            analyzer._load_spy_benchmark()

        self.assertIsNone(analyzer.spy_3m_return)
        self.assertTrue(analyzer.benchmark_degraded)

    def test_valid_spy_sets_return(self):
        analyzer = object.__new__(sp.StrategicStockAnalyzer)
        idx = pd.date_range("2024-01-01", periods=70, freq="B")
        hist = pd.DataFrame({"Close": np.linspace(400, 440, 70)}, index=idx)

        class _OkTicker:
            def history(self, *args, **kwargs):
                return hist

        with patch.object(sp.yf, "Ticker", return_value=_OkTicker()):
            analyzer._load_spy_benchmark()

        self.assertIsNotNone(analyzer.spy_3m_return)
        self.assertFalse(analyzer.benchmark_degraded)


class TestAllocationEngine(unittest.TestCase):
    """The portfolio allocation engine must use one clear, consistent mode."""

    def _analyzer(self, capital=2500.0):
        a = object.__new__(sp.StrategicStockAnalyzer)
        a.capital = capital
        a.market_regime = sp.MarketRegime.BULL_STRONG
        return a

    def setUp(self):
        self._orig_frac = sp.Config.ALLOW_FRACTIONAL_SHARES

    def tearDown(self):
        sp.Config.ALLOW_FRACTIONAL_SHARES = self._orig_frac

    def test_orcl_like_below_one_share_moves_to_watchlist(self):
        # Whole-share mode: intended capital < entry price => 0 shares and the
        # name is moved to the watchlist rather than left as a funded ghost.
        sp.Config.ALLOW_FRACTIONAL_SHARES = False
        a = self._analyzer()
        s = _setup(ticker="ORCL", entry_price=240.0, stop_loss=228.0,
                   primary_target=270.0, allocation_usd=242.0,
                   intended_allocation_usd=242.0, shares=1)
        a._finalize_share_accounting(s, 121.0)  # below one share
        self.assertFalse(s.is_funded)
        self.assertTrue(s.moved_to_watchlist)
        self.assertEqual(s.shares, 0)
        self.assertEqual(s.allocation_usd, 0.0)
        self.assertEqual(s.executable_allocation_usd, 0.0)
        self.assertEqual(s.risk_amount, 0.0)

    def test_fractional_mode_decimal_shares_and_capital(self):
        sp.Config.ALLOW_FRACTIONAL_SHARES = True
        a = self._analyzer()
        s = _setup(ticker="ORCL", entry_price=240.0, stop_loss=228.0,
                   primary_target=270.0)
        a._finalize_share_accounting(s, 120.0)
        self.assertTrue(s.is_funded)
        self.assertAlmostEqual(s.shares, 0.5, places=2)
        self.assertAlmostEqual(s.executable_allocation_usd, 120.0, places=2)
        self.assertAlmostEqual(s.allocation_usd, 120.0, places=2)

    def test_portfolio_allocated_capital_equals_sum_executable(self):
        sp.Config.ALLOW_FRACTIONAL_SHARES = False
        a = self._analyzer()
        s1 = _setup(ticker="AAA", entry_price=150.0, stop_loss=140.0, primary_target=180.0)
        s2 = _setup(ticker="BBB", entry_price=200.0, stop_loss=190.0, primary_target=240.0)
        a._finalize_share_accounting(s1, 500.0)  # floor(500/150)=3 -> $450
        a._finalize_share_accounting(s2, 700.0)  # floor(700/200)=3 -> $600
        funded = [s for s in (s1, s2) if s.is_funded]
        total = sum(s.executable_allocation_usd for s in funded)
        self.assertAlmostEqual(s1.executable_allocation_usd, 450.0)
        self.assertAlmostEqual(s2.executable_allocation_usd, 600.0)
        self.assertAlmostEqual(total, 1050.0)

    def test_risk_amount_equals_shares_times_stop_distance(self):
        sp.Config.ALLOW_FRACTIONAL_SHARES = False
        a = self._analyzer()
        s = _setup(entry_price=150.0, stop_loss=140.0, primary_target=180.0)
        a._finalize_share_accounting(s, 500.0)  # 3 shares
        self.assertEqual(s.shares, 3)
        self.assertAlmostEqual(s.risk_amount, 3 * (150.0 - 140.0))

    def test_sub_one_share_names_are_watchlist_only(self):
        # Sub-one-share / conviction-downsized picks remain visible, but only
        # on the watchlist. They must not become phantom funded positions.
        sp.Config.ALLOW_FRACTIONAL_SHARES = False
        a = self._analyzer()
        funded = _setup(ticker="FUND", sector="Technology", entry_price=150.0,
                        stop_loss=140.0, primary_target=180.0, relative_strength=80.0)
        ghost = _setup(ticker="GHST", sector="Energy", entry_price=240.0,
                       stop_loss=228.0, primary_target=270.0, relative_strength=80.0)
        a._finalize_share_accounting(funded, 500.0)   # 3 shares -> funded
        a._finalize_share_accounting(ghost, 120.0)    # < 1 share -> 0 shares
        a.results = [funded, ghost]
        a._is_acceptable_correlation = lambda t, e: True
        picks = a.get_top_setups(max_positions=5)
        self.assertIn("FUND", [p.ticker for p in picks])
        self.assertNotIn("GHST", [p.ticker for p in picks])
        self.assertIn("GHST", [p.ticker for p in a.watchlist_setups])
        self.assertEqual(ghost.shares, 0)
        self.assertAlmostEqual(ghost.risk_amount, 0.0)
        self.assertAlmostEqual(ghost.executable_allocation_usd, 0.0)

    def test_cash_reserve_is_correct(self):
        sp.Config.ALLOW_FRACTIONAL_SHARES = False
        a = self._analyzer(capital=2500.0)
        funded = _setup(ticker="FUND", entry_price=150.0, stop_loss=140.0, primary_target=180.0)
        ghost = _setup(ticker="GHST", entry_price=240.0, stop_loss=228.0, primary_target=270.0)
        a._finalize_share_accounting(funded, 500.0)   # 3 shares = $450
        a._finalize_share_accounting(ghost, 120.0)    # < 1 share -> $0
        funded_only = [s for s in (funded, ghost) if s.is_funded and s.shares > 0]
        total_alloc = sum(s.executable_allocation_usd for s in funded_only)
        cash_reserve = a.capital - total_alloc
        self.assertEqual(len(funded_only), 1)
        self.assertAlmostEqual(total_alloc, 450.0)
        self.assertAlmostEqual(cash_reserve, 2050.0)


class TestEarningsProximity(unittest.TestCase):
    """The earnings safety filter must read the SINGULAR yfinance fields."""

    def test_reads_singular_future_field(self):
        soon = sp.datetime.now().timestamp() + 2.5 * 86400  # ~2.5 days -> floor 2
        d = sp.StrategicStockAnalyzer._days_to_next_earnings({"earningsTimestampStart": soon})
        self.assertEqual(d, 2)

    def test_dead_plural_key_is_ignored(self):
        # The previous code read info['earningsTimestamps'] (plural), which
        # yfinance never returns -> the filter must NOT rely on it.
        soon = sp.datetime.now().timestamp() + 3 * 86400
        self.assertIsNone(
            sp.StrategicStockAnalyzer._days_to_next_earnings({"earningsTimestamps": [soon]})
        )

    def test_past_dates_return_none(self):
        past = sp.datetime.now().timestamp() - 5 * 86400
        self.assertIsNone(
            sp.StrategicStockAnalyzer._days_to_next_earnings({"earningsTimestamp": past})
        )

    def test_picks_soonest_future(self):
        now = sp.datetime.now().timestamp()
        info = {"earningsTimestamp": now + 30 * 86400,
                "earningsTimestampStart": now + 6.5 * 86400}  # ~6.5 days -> floor 6
        self.assertEqual(sp.StrategicStockAnalyzer._days_to_next_earnings(info), 6)


class TestExitStrategyNetRR(unittest.TestCase):
    """Overhead resistance must remain visible even when it fails the R/R gate."""

    def test_nearby_resistance_is_not_replaced_by_constructed_rr(self):
        a = object.__new__(sp.StrategicStockAnalyzer)
        # All resistance is nearby. The returned target should reflect that,
        # leaving the downstream R/R gate to reject the setup.
        ind = {"ATR": 3.0, "S1": 95.0, "Resistance_20_Prev": 100.5,
               "Resistance_50": 100.7, "R1": 100.3, "R2": 100.6,
               "High_52W_Prev": 101.0, "Volume_Ratio": 1.0}
        ex = a._calculate_exit_strategy(entry_price=100.0, indicators=ind,
                                        signal="ACCUMULATE", score=62)
        risk = 100.0 - ex["stop_loss"]
        reward = ex["primary_target"] - 100.0
        friction = 100.0 * sp.Config.FRICTION_ROUND_TRIP_PCT
        net_rr = (reward - friction) / risk
        self.assertLess(net_rr, sp.Config.MIN_RISK_REWARD)
        self.assertLess(ex["primary_target"], ex["secondary_target"])
        self.assertLess(ex["secondary_target"], ex["aggressive_target"])


class TestRSRatingUniverse(unittest.TestCase):
    """RS rating must be a universe percentile, not a within-pick ranking."""

    def test_rs_ranks_against_universe_distribution(self):
        import threading
        a = object.__new__(sp.StrategicStockAnalyzer)
        a._universe_momentum_lock = threading.Lock()
        a._universe_momentum = [float(x) for x in range(100)]  # 0..99
        mid = _setup(ticker="MID", momentum_3m=50.0, momentum_6m=50.0, momentum_1m=50.0)
        strong = _setup(ticker="STR", momentum_3m=95.0, momentum_6m=95.0, momentum_1m=95.0)
        a._assign_rs_ratings([mid, strong])
        # Universe-relative: ~51st and ~96th percentile (NOT a degenerate 1/99).
        self.assertGreater(strong.relative_strength, mid.relative_strength)
        self.assertGreater(strong.relative_strength, 90.0)
        self.assertTrue(40.0 <= mid.relative_strength <= 60.0)


class TestCrossDetection(unittest.TestCase):
    """detect_patterns must compute SMAs from Close (the bug read missing cols)."""

    @staticmethod
    def _hist(close: np.ndarray) -> pd.DataFrame:
        idx = pd.date_range("2023-01-01", periods=len(close), freq="B")
        return pd.DataFrame(
            {"Open": close, "High": close + 0.5, "Low": close - 0.5,
             "Close": close, "Volume": np.full(len(close), 1_000_000)},
            index=idx,
        )

    def test_recent_death_cross_detected(self):
        # Long uptrend then a deep late selloff so SMA50 crosses below SMA200
        # within the last few bars (recent death cross).
        close = np.concatenate([np.linspace(50, 170, 170), np.linspace(170, 25, 45)])
        hist = self._hist(close)
        ind = sp.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(hist)
        patterns = sp.AdvancedTechnicalAnalyzer.detect_patterns(hist, ind)
        self.assertIn("Death Cross (Recent)", patterns)
        # And it must be one of the hard-block patterns the scanner rejects.
        self.assertTrue(
            any("Death Cross" in p for p in patterns),
            f"expected a Death Cross label, got {patterns}",
        )

    def test_uptrend_reports_static_above_not_recent_cross(self):
        close = np.linspace(80, 160, 240)  # clean monotonic uptrend
        hist = self._hist(close)
        ind = sp.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(hist)
        patterns = sp.AdvancedTechnicalAnalyzer.detect_patterns(hist, ind)
        self.assertIn("Above 50/200 MA", patterns)
        self.assertNotIn("Death Cross (Recent)", patterns)


class TestDividendYieldUnits(unittest.TestCase):
    """Dividend yield does not enter the business-quality score."""

    def _quality(self, info):
        a = object.__new__(sp.StrategicStockAnalyzer)
        return a._calculate_quality_score(info)

    def test_percent_and_fraction_yield_score_equally(self):
        percent_form = self._quality({"dividendYield": 3.0})              # 3%
        fraction_form = self._quality({"trailingAnnualDividendYield": 0.03})
        no_dividend = self._quality({})
        self.assertAlmostEqual(percent_form, fraction_form)
        self.assertEqual(percent_form, no_dividend)


if __name__ == "__main__":
    unittest.main()
