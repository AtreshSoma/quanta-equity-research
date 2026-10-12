"""Tests for the data-provider rate-limit resilience layer.

These cover the *reliability* plumbing only — none of the stock-selection,
scoring, or threshold logic is exercised or changed here.

Covers:
  1. ``_is_rate_limit_error`` / ``_extract_retry_after`` detection helpers.
  2. ``ProviderRateLimitTracker`` cooldown + circuit/snapshot behaviour.
  3. ``QuantaEliteTradingSystem._classify_scan_error`` -> ``provider_rate_limited``.
  4. ``_classify_scan_status`` provider-throttle precedence rules (webapp).
  5. ``_backend_health`` reporting a ``rate_limited`` overall state.

Run from the workspace root with:

    python -m pytest tests/test_provider_resilience.py -q
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import SP_ELITE_PRO as sp  # noqa: E402
from SP_ELITE_PRO import QuantaEliteTradingSystem  # noqa: E402

from TradingAnalysis_ELITE import classify_asset_type  # noqa: E402


class _FakeRateLimit(Exception):
    """Mimics yfinance's ``YFRateLimitError`` (matched by class name)."""


class TestRateLimitDetection(unittest.TestCase):
    def test_detects_by_class_name(self):
        self.assertTrue(sp._is_rate_limit_error(_FakeRateLimit("slow down")))

    def test_detects_by_message_429(self):
        self.assertTrue(sp._is_rate_limit_error(Exception("HTTP 429 returned")))

    def test_detects_too_many_requests(self):
        self.assertTrue(sp._is_rate_limit_error(Exception("Too Many Requests")))

    def test_detects_throttle_and_rate_limit_text(self):
        self.assertTrue(sp._is_rate_limit_error(Exception("request was throttled")))
        self.assertTrue(sp._is_rate_limit_error(Exception("rate limit exceeded")))

    def test_ignores_unrelated_error(self):
        self.assertFalse(sp._is_rate_limit_error(ValueError("bad ticker symbol")))

    def test_extract_retry_after_seconds(self):
        self.assertEqual(sp._extract_retry_after(Exception("retry after 30 seconds")), 30.0)

    def test_extract_retry_after_absent(self):
        self.assertIsNone(sp._extract_retry_after(Exception("nope")))


class TestProviderRateLimitTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = sp.ProviderRateLimitTracker()

    def test_starts_clear(self):
        self.assertFalse(self.tracker.in_cooldown())
        snap = self.tracker.snapshot()
        self.assertEqual(snap["rate_limit_hits"], 0)
        self.assertFalse(snap["rate_limited"])

    def test_note_rate_limit_enters_cooldown(self):
        self.tracker.note_rate_limit(retry_after=5)
        self.assertTrue(self.tracker.in_cooldown())
        self.assertGreater(self.tracker.cooldown_remaining(), 0)
        snap = self.tracker.snapshot()
        self.assertEqual(snap["rate_limit_hits"], 1)
        self.assertTrue(snap["rate_limited"])

    def test_cooldown_never_shortened(self):
        self.tracker.note_rate_limit(retry_after=120)
        long_remaining = self.tracker.cooldown_remaining()
        # A subsequent smaller retry-after must not shrink the cooldown window.
        self.tracker.note_rate_limit(retry_after=1)
        self.assertGreaterEqual(self.tracker.cooldown_remaining(), long_remaining - 1)

    def test_consecutive_counter_resets_on_success(self):
        self.tracker.note_rate_limit()
        self.tracker.note_rate_limit()
        self.assertEqual(self.tracker.snapshot()["consecutive_rate_limits"], 2)
        self.tracker.note_success()
        self.assertEqual(self.tracker.snapshot()["consecutive_rate_limits"], 0)

    def test_reset_clears_cooldown_keeps_lifetime_hits(self):
        self.tracker.note_rate_limit(retry_after=30)
        self.tracker.reset()
        # Reset clears the active cooldown + consecutive streak so an explicit
        # user retry can proceed, but keeps the lifetime hit counter for telemetry.
        self.assertFalse(self.tracker.in_cooldown())
        self.assertEqual(self.tracker.snapshot()["consecutive_rate_limits"], 0)
        self.assertEqual(self.tracker.snapshot()["rate_limit_hits"], 1)

    def test_expired_cooldown_reports_not_limited(self):
        # Directly age the cooldown into the past to exercise the boundary
        # (note_rate_limit enforces a configured minimum cooldown floor).
        self.tracker.note_rate_limit()
        self.tracker.cooldown_until = time.time() - 1.0
        self.assertFalse(self.tracker.in_cooldown())
        self.assertEqual(self.tracker.cooldown_remaining(), 0.0)


class TestScanErrorClassification(unittest.TestCase):
    def test_rate_limit_classified_as_provider_rate_limited(self):
        info = QuantaEliteTradingSystem._classify_scan_error(_FakeRateLimit("429 Too Many Requests"))
        self.assertEqual(info["error_type"], "provider_rate_limited")
        self.assertTrue(info["retryable"])

    def test_plain_429_text_classified(self):
        info = QuantaEliteTradingSystem._classify_scan_error(Exception("HTTP 429"))
        self.assertEqual(info["error_type"], "provider_rate_limited")


class TestClassifyScanStatusRateLimit(unittest.TestCase):
    def setUp(self):
        from app import _classify_scan_status  # noqa: WPS433
        self.classify = _classify_scan_status

    def test_circuit_open_with_no_picks_is_rate_limited(self):
        self.assertEqual(
            self.classify(universe_size=200, exception_rate=0.1,
                          analysis_failed_count=20, final_pick_count=0,
                          provider_rate_limited_count=18, provider_circuit_open=True),
            "provider_rate_limited",
        )

    def test_rate_limit_with_surviving_picks_is_partial_with_picks(self):
        self.assertEqual(
            self.classify(universe_size=200, exception_rate=0.1,
                          analysis_failed_count=20, final_pick_count=3,
                          provider_rate_limited_count=18, provider_circuit_open=True),
            "partial_with_picks",
        )

    def test_rate_limit_majority_overrides_failed(self):
        # Even with a high exception rate, a rate-limit majority must report the
        # throttle (so the UI shows cooldown/retry) rather than generic "failed".
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.6,
                          analysis_failed_count=60, final_pick_count=0,
                          provider_rate_limited_count=40, provider_circuit_open=False),
            "provider_rate_limited",
        )

    def test_non_rate_limit_failure_still_failed(self):
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.8,
                          analysis_failed_count=80, final_pick_count=0,
                          provider_rate_limited_count=0, provider_circuit_open=False),
            "failed",
        )

    def test_backwards_compatible_without_provider_args(self):
        # Existing callers that omit the new kwargs must behave as before.
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.0,
                          analysis_failed_count=0, final_pick_count=5),
            "success",
        )


class TestBackendHealthRateLimited(unittest.TestCase):
    def test_health_reports_rate_limited_overall(self):
        import app  # noqa: WPS433

        # Simulate an active provider cooldown via the shared tracker.
        sp.PROVIDER_STATE.reset()
        sp.PROVIDER_STATE.note_rate_limit(retry_after=60)
        try:
            health = app._backend_health(probe=False)
            self.assertTrue(health["rate_limited"])
            self.assertEqual(health["overall"], "rate_limited")
            self.assertIn("provider_status", health)
        finally:
            sp.PROVIDER_STATE.reset()


class TestDeepDiveRateLimitDetail(unittest.TestCase):
    """Structured rate-limit payload built for the Stock Deep-Dive tear sheet."""

    def setUp(self):
        import app  # noqa: WPS433
        self.app = app
        sp.PROVIDER_STATE.reset()

    def tearDown(self):
        sp.PROVIDER_STATE.reset()

    def test_detail_shape_when_rate_limited(self):
        sp.PROVIDER_STATE.note_rate_limit(retry_after=60)
        detail = self.app._analyze_rate_limit_detail("TSM", attempts=3, cached_payload=None)
        self.assertEqual(detail["status"], "provider_rate_limited")
        self.assertEqual(detail["error_type"], "provider_rate_limited")
        self.assertEqual(detail["ticker"], "TSM")
        self.assertEqual(detail["provider"], "yfinance")
        self.assertTrue(detail["retryable"])
        self.assertEqual(detail["attempts"], 3)
        self.assertGreater(detail["cooldown_remaining_seconds"], 0)
        self.assertFalse(detail["used_cached_data"])
        self.assertIn("price", detail["failed_sections"])

    def test_detail_marks_cached_when_payload_present(self):
        sp.PROVIDER_STATE.note_rate_limit(retry_after=60)
        cached = {"ticker": "TSM", "cache_timestamp": 123.0}
        detail = self.app._analyze_rate_limit_detail("TSM", attempts=1, cached_payload=cached)
        self.assertTrue(detail["used_cached_data"])
        self.assertTrue(detail["partial_data_available"])
        self.assertEqual(detail["failed_sections"], [])
        self.assertEqual(detail["cache_timestamp"], 123.0)


class TestProbeRespectsCooldown(unittest.TestCase):
    """The liveness probe must not call yfinance while a cooldown is active."""

    def setUp(self):
        import app  # noqa: WPS433
        self.app = app
        sp.PROVIDER_STATE.reset()

    def tearDown(self):
        sp.PROVIDER_STATE.reset()

    def test_probe_skipped_during_cooldown(self):
        sp.PROVIDER_STATE.note_rate_limit(retry_after=60)
        probe = self.app._probe_price_provider()
        self.assertTrue(probe.get("skipped_due_to_cooldown"))
        self.assertTrue(probe.get("rate_limited"))
        self.assertFalse(probe.get("price_fetch_ok"))


class TestChartCacheServesDuringCooldown(unittest.TestCase):
    """Cached chart points are served (labelled) instead of hitting yfinance."""

    def setUp(self):
        import app  # noqa: WPS433
        self.app = app
        sp.PROVIDER_STATE.reset()
        with app._price_chart_cache_lock:
            app._price_chart_cache.clear()

    def tearDown(self):
        sp.PROVIDER_STATE.reset()
        with self.app._price_chart_cache_lock:
            self.app._price_chart_cache.clear()

    def test_cooldown_with_cache_returns_degraded_cached(self):
        payload = {"period": "6mo", "points": [{"date": "2026-01-02", "close": 100.0, "volume": 1}]}
        self.app._price_chart_cache_set("TSM", "6mo", payload)
        sp.PROVIDER_STATE.note_rate_limit(retry_after=60)
        out = self.app._price_chart_snapshot("TSM", "6mo")
        self.assertTrue(out.get("from_cache"))
        self.assertTrue(out.get("degraded"))
        self.assertEqual(out["points"], payload["points"])

    def test_cooldown_without_cache_returns_empty_degraded(self):
        sp.PROVIDER_STATE.note_rate_limit(retry_after=60)
        out = self.app._price_chart_snapshot("NOCACHE", "6mo")
        self.assertEqual(out["points"], [])
        self.assertTrue(out.get("degraded"))
        self.assertGreater(out.get("cooldown_remaining_seconds", 0), 0)


class TestStaleAnalysisCacheFallback(unittest.TestCase):
    """A stale tear-sheet cache is retrievable only with allow_stale=True."""

    def setUp(self):
        import app  # noqa: WPS433
        self.app = app
        self.app._analysis_result_cache.clear()

    def tearDown(self):
        self.app._analysis_result_cache.clear()

    def test_stale_entry_requires_allow_stale(self):
        # [F04] Entries are keyed by ticker AND analysis mode, and an expired
        # entry is no longer destroyed by the plain lookup — so this no longer
        # has to re-seed the cache between the two assertions.
        key = self.app._analysis_cache_key("TSM", "full")
        self.app._set_cached_analysis("TSM", {"ticker": "TSM"}, mode="full")
        self.app._analysis_result_cache[key]["timestamp"] = (
            time.time() - self.app.ANALYSIS_RESULT_CACHE_SECONDS - 10
        )
        self.assertIsNone(self.app._get_cached_analysis("TSM"))
        # allow_stale should still return it, flagged stale.
        stale = self.app._get_cached_analysis("TSM", allow_stale=True)
        self.assertIsNotNone(stale)
        self.assertTrue(stale["cache"]["stale"])


class TestAssetTypeClassification(unittest.TestCase):
    """Structural asset-type classification (display/data-mapping only)."""

    def test_common_stock(self):
        r = classify_asset_type(
            quote_type="EQUITY", sector="Technology",
            industry="Consumer Electronics", country="United States",
            company_name="Apple Inc.", ticker="AAPL",
        )
        self.assertEqual(r["asset_type"], "common_stock")
        self.assertFalse(r["is_adr"])
        self.assertFalse(r["is_financial"])

    def test_high_volatility_overlay_keeps_common_type(self):
        r = classify_asset_type(
            quote_type="EQUITY", sector="Technology",
            industry="Semiconductors", country="United States",
            company_name="NVIDIA Corporation", ticker="NVDA",
            beta=2.1,
        )
        self.assertEqual(r["asset_type"], "common_stock")
        self.assertTrue(r["is_high_volatility"])

    def test_financial_issuer(self):
        r = classify_asset_type(
            quote_type="EQUITY", sector="Financial Services",
            industry="Banks - Diversified", country="United States",
            company_name="Citigroup Inc.", ticker="C",
        )
        self.assertEqual(r["asset_type"], "financial")
        self.assertTrue(r["is_financial"])

    def test_adr_by_country(self):
        r = classify_asset_type(
            quote_type="EQUITY", sector="Technology",
            industry="Semiconductors", country="Taiwan",
            company_name="Taiwan Semiconductor Manufacturing Company Limited",
            ticker="TSM",
        )
        self.assertEqual(r["asset_type"], "adr")
        self.assertTrue(r["is_adr"])
        self.assertEqual(r["country"], "Taiwan")

    def test_etf(self):
        r = classify_asset_type(
            is_etf=True, quote_type="ETF", company_name="Technology Select Sector SPDR Fund",
            ticker="XLK",
        )
        self.assertEqual(r["asset_type"], "etf")
        self.assertFalse(r["is_adr"])
        self.assertFalse(r["is_financial"])

    def test_etf_precedence_over_financial(self):
        # A financial-sector ETF must still classify as ETF (structural wins).
        r = classify_asset_type(
            is_etf=True, quote_type="ETF", sector="Financial Services",
            company_name="Financial Select Sector SPDR Fund", ticker="XLF",
        )
        self.assertEqual(r["asset_type"], "etf")

    def test_invalid_ticker(self):
        r = classify_asset_type(is_invalid=True, ticker="FAKE123")
        self.assertEqual(r["asset_type"], "invalid")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
