"""Tests for the scan-health classification that stops the dashboard from
hiding a systemic scan failure (e.g. 100% data-provider errors) behind a benign
"no buy-qualified setups" message.

Covers:
  1. ``_classify_scan_status`` (webapp policy) for every outcome state.
  2. ``QuantaEliteTradingSystem._classify_scan_error`` category mapping.
  3. ``QuantaEliteTradingSystem.get_error_summary`` count/rate maths.

Run from the workspace root with:

    python -m pytest tests/test_scan_health.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

from SP_ELITE_PRO import QuantaEliteTradingSystem  # noqa: E402


def _new_engine() -> QuantaEliteTradingSystem:
    """Build an uninitialised engine instance so we can poke the error-record
    plumbing without running the (heavy) real constructor."""
    return object.__new__(QuantaEliteTradingSystem)


class TestClassifyScanStatus(unittest.TestCase):
    """The single field the UI keys its empty/failure messaging off."""

    def setUp(self):
        # Imported lazily so a webapp import error surfaces clearly here.
        from app import _classify_scan_status  # noqa: WPS433
        self.classify = _classify_scan_status

    def test_no_universe_when_nothing_scanned(self):
        self.assertEqual(
            self.classify(universe_size=0, exception_rate=0.0,
                          analysis_failed_count=0, final_pick_count=0),
            "no_universe",
        )

    def test_full_exception_rate_is_failed(self):
        # 100% of the universe errored out -> scan failure, not "no setups".
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=1.0,
                          analysis_failed_count=100, final_pick_count=0),
            "failed",
        )

    def test_half_exception_rate_is_failed(self):
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.5,
                          analysis_failed_count=50, final_pick_count=0),
            "failed",
        )

    def test_some_failures_no_picks_is_partial(self):
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.1,
                          analysis_failed_count=10, final_pick_count=0),
            "partial",
        )

    def test_some_failures_with_picks_is_partial_with_picks(self):
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.1,
                          analysis_failed_count=10, final_pick_count=3),
            "partial_with_picks",
        )

    def test_clean_scan_zero_picks_is_no_picks(self):
        # The ONLY case that should read "no buy-qualified setups".
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.0,
                          analysis_failed_count=0, final_pick_count=0),
            "no_picks",
        )

    def test_clean_scan_with_picks_is_success(self):
        self.assertEqual(
            self.classify(universe_size=100, exception_rate=0.0,
                          analysis_failed_count=0, final_pick_count=5),
            "success",
        )


class TestClassifyScanError(unittest.TestCase):
    """Raw exceptions must map to actionable, UI-friendly categories."""

    def _type(self, exc: BaseException) -> str:
        return QuantaEliteTradingSystem._classify_scan_error(exc)["error_type"]

    def test_rate_limit_is_provider_rate_limited_retryable(self):
        # 429 / throttling is its own category so the UI can show a cooldown +
        # safe-mode retry rather than a generic data-provider failure.
        info = QuantaEliteTradingSystem._classify_scan_error(
            RuntimeError("HTTP Error 429: Too Many Requests"))
        self.assertEqual(info["error_type"], "provider_rate_limited")
        self.assertTrue(info["retryable"])

    def test_network_error_is_provider_error(self):
        self.assertEqual(
            self._type(ConnectionError("Max retries exceeded: connection refused")),
            "data_provider_error",
        )

    def test_missing_price_data(self):
        self.assertEqual(
            self._type(ValueError("No price data found, symbol may be delisted")),
            "missing_price_data",
        )

    def test_import_error(self):
        self.assertEqual(self._type(ModuleNotFoundError("No module named 'foo'")),
                         "import_error")

    def test_timeout(self):
        info = QuantaEliteTradingSystem._classify_scan_error(TimeoutError("timed out"))
        # 'timed out' matches the network branch first (also a provider error);
        # either way it must be retryable and never a silent analysis_exception.
        self.assertIn(info["error_type"], {"timeout", "data_provider_error"})
        self.assertTrue(info["retryable"])

    def test_generic_value_error_is_analysis_exception(self):
        self.assertEqual(self._type(ValueError("unexpected shape")),
                         "analysis_exception")

    def test_unknown_falls_back_to_analysis_exception(self):
        self.assertEqual(self._type(RuntimeError("something weird")),
                         "analysis_exception")


class TestErrorSummary(unittest.TestCase):
    """The aggregate maths that drives analyzed-vs-failed messaging."""

    def test_full_failure_reports_100pct_exception_rate(self):
        eng = _new_engine()
        eng._error_records = [
            {"error_type": "data_provider_error"} for _ in range(100)
        ]
        eng._last_universe_size = 100
        eng.failed_tickers = [f"T{i}" for i in range(100)]
        summary = eng.get_error_summary()
        self.assertEqual(summary["analysis_failed_count"], 100)
        self.assertEqual(summary["analyzed_success_count"], 0)
        self.assertEqual(summary["exception_rate"], 1.0)
        self.assertEqual(summary["by_type"]["data_provider_error"], 100)

    def test_partial_failure_rate_and_success_count(self):
        eng = _new_engine()
        eng._error_records = [{"error_type": "missing_price_data"} for _ in range(10)]
        eng._last_universe_size = 100
        eng.failed_tickers = [f"T{i}" for i in range(10)]
        summary = eng.get_error_summary()
        self.assertEqual(summary["analysis_failed_count"], 10)
        self.assertEqual(summary["analyzed_success_count"], 90)
        self.assertEqual(summary["exception_rate"], 0.1)

    def test_clean_scan_has_zero_rate(self):
        eng = _new_engine()
        eng._error_records = []
        eng._last_universe_size = 100
        eng.failed_tickers = []
        summary = eng.get_error_summary()
        self.assertEqual(summary["analysis_failed_count"], 0)
        self.assertEqual(summary["analyzed_success_count"], 100)
        self.assertEqual(summary["exception_rate"], 0.0)

    def test_missing_attributes_do_not_raise(self):
        # An engine that never ran a scan must still summarise safely.
        eng = _new_engine()
        summary = eng.get_error_summary()
        self.assertEqual(summary["universe_size"], 0)
        self.assertEqual(summary["exception_rate"], 0.0)
        self.assertEqual(summary["records"], [])

    def test_empty_scan_explanation_uses_observed_failures_and_gates(self):
        # A04: the sentence states the reconciled outcome counts in words; the
        # old "price_gate=2" / "action=2" key syntax is gone from the page.
        eng = _new_engine()
        eng._last_universe_size = 5
        eng.failed_tickers = ["BAD"]
        eng._filter_stats = {"price_outside_range": 2}
        eng._outcomes = {"BAD": ("failed", "analysis_exception"), "P1": ("rejected", "price_outside_range"),
                         "P2": ("rejected", "price_outside_range"), "A": ("analyzed", "analyzed"),
                         "B": ("analyzed", "analyzed")}
        eng._gate_stats = {"gate1_actionable": 2, "gate2_risk_safe": 1}
        explanation = eng.explain_empty_scan()
        self.assertIn("Of 5 names, 2 reached the book, 2 were screened out by per-name rules "
                      "and 1 could not be analysed.", explanation)
        self.assertIn("Scan coverage was incomplete", explanation)
        self.assertNotRegex(explanation, r"\w=\d")

    def test_empty_scan_explanation_without_universe(self):
        eng = _new_engine()
        self.assertIn("No tickers entered", eng.explain_empty_scan())


if __name__ == "__main__":
    unittest.main()
