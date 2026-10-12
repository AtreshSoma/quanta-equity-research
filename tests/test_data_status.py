"""Tests for data-acquisition status classification in the deep analyzer.

Covers the "Scanner liked, analyzer disagrees" fix: a valid ticker whose
history cannot be fetched (rate-limit / provider outage / too few bars) must be
classified as a DATA failure, NOT as an INVALID ticker.

Run:
    python -m pytest tests/test_data_status.py -q
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from engine.data_status import (  # noqa: E402
    DataStatus,
    is_data_failure,
    is_true_invalid,
    status_label,
)
import TradingAnalysis_ELITE as TA  # noqa: E402


class _DictCache:
    """Minimal cache with the get/set interface the analyzer expects."""

    def __init__(self):
        self._d = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v):
        self._d[k] = v


class _FakeHistory(list):
    """A stand-in for a pandas DataFrame: only ``len`` and ``.attrs`` are used
    by the fetch path under test."""

    def __init__(self, n):
        super().__init__(range(n))
        self.attrs = {}


class _FakeTicker:
    """Configurable yfinance.Ticker stand-in."""

    def __init__(self, rows=0, info=None, raise_exc=None):
        self._rows = rows
        self._info = info or {}
        self._raise = raise_exc

    def history(self, period="2y", interval="1d", auto_adjust=True):
        if self._raise is not None:
            raise self._raise
        return _FakeHistory(self._rows)

    @property
    def info(self):
        return self._info


def _make_analyzer():
    obj = TA.QuantaEliteAnalyzer.__new__(TA.QuantaEliteAnalyzer)
    obj.cache = _DictCache()
    return obj


class _RateLimitError(Exception):
    pass


class DataStatusModuleTest(unittest.TestCase):
    def test_is_data_failure_classification(self):
        self.assertTrue(is_data_failure(DataStatus.PROVIDER_RATE_LIMITED))
        self.assertTrue(is_data_failure(DataStatus.PROVIDER_DATA_UNAVAILABLE))
        self.assertTrue(is_data_failure(DataStatus.DEEP_CONFIRMATION_FAILED))
        self.assertTrue(is_data_failure(DataStatus.INSUFFICIENT_HISTORY))
        self.assertTrue(is_data_failure(DataStatus.STALE_DATA))
        self.assertFalse(is_data_failure(DataStatus.OK))
        self.assertFalse(is_data_failure(DataStatus.INVALID_TICKER))

    def test_is_true_invalid(self):
        self.assertTrue(is_true_invalid(DataStatus.INVALID_TICKER))
        self.assertFalse(is_true_invalid(DataStatus.PROVIDER_DATA_UNAVAILABLE))

    def test_status_label(self):
        self.assertEqual(status_label(DataStatus.OK), "Confirmed")
        self.assertIn("provider", status_label(DataStatus.PROVIDER_DATA_UNAVAILABLE).lower())
        self.assertIn("stale", status_label(DataStatus.STALE_DATA).lower())

    def test_price_bar_age_is_calendar_based(self):
        data = pd.DataFrame(
            {"Close": [100.0]}, index=[pd.Timestamp("2026-07-03 16:00:00")]
        )
        age = TA.QuantaEliteAnalyzer._price_data_age_days(
            data, datetime(2026, 7, 10, 9, 30)
        )
        self.assertEqual(age, 7)


class FetchClassificationTest(unittest.TestCase):
    def setUp(self):
        self._saved_yf = TA.yf

    def tearDown(self):
        TA.yf = self._saved_yf

    def _patch_yf(self, factory):
        class _FakeYF:
            Ticker = staticmethod(factory)
        TA.yf = _FakeYF

    def test_valid_ticker_with_history_ok(self):
        """Case A: a real ticker (C) with >=50 bars => OK, data returned."""
        self._patch_yf(lambda variant: _FakeTicker(rows=120, info={"shortName": "Citigroup"}))
        obj = _make_analyzer()
        data, status, meta = obj._fetch_price_data_classified("C")
        self.assertEqual(status, DataStatus.OK)
        self.assertIsNotNone(data)
        self.assertGreaterEqual(meta["rows"], 50)

    def test_real_ticker_empty_history_is_provider_unavailable(self):
        """Case B: real ticker (has metadata) but 0 bars => provider data
        unavailable, NOT invalid_ticker."""
        self._patch_yf(lambda variant: _FakeTicker(rows=0, info={"shortName": "Citigroup",
                                                                  "regularMarketPrice": 65.0}))
        obj = _make_analyzer()
        data, status, _meta = obj._fetch_price_data_classified("C")
        self.assertEqual(status, DataStatus.PROVIDER_DATA_UNAVAILABLE)
        self.assertNotEqual(status, DataStatus.INVALID_TICKER)
        self.assertIsNone(data)

    def test_rate_limited_history_is_rate_limited(self):
        """A 429/rate-limit during fetch => provider_rate_limited (after retry)."""
        self._patch_yf(lambda variant: _FakeTicker(raise_exc=_RateLimitError("429 Too Many Requests")))
        obj = _make_analyzer()
        data, status, meta = obj._fetch_price_data_classified("FFBC")
        self.assertEqual(status, DataStatus.PROVIDER_RATE_LIMITED)
        self.assertTrue(meta["rate_limited"])
        self.assertIsNone(data)

    def test_insufficient_history_for_real_short_ticker(self):
        """A real ticker that returns some but <50 bars => insufficient_history."""
        self._patch_yf(lambda variant: _FakeTicker(rows=10, info={"shortName": "NewCo"}))
        obj = _make_analyzer()
        data, status, meta = obj._fetch_price_data_classified("NEWIPO")
        self.assertEqual(status, DataStatus.INSUFFICIENT_HISTORY)
        self.assertIsNotNone(data)
        self.assertEqual(meta["rows"], 10)

    def test_bogus_ticker_no_metadata_is_invalid(self):
        """Case C: no bars AND no metadata, while the provider answers a
        control symbol => invalid_ticker."""
        self._patch_yf(lambda variant: _FakeTicker(rows=120 if variant == "SPY" else 0, info={}))
        obj = _make_analyzer()
        data, status, _meta = obj._fetch_price_data_classified("FAKE123")
        self.assertEqual(status, DataStatus.INVALID_TICKER)
        self.assertIsNone(data)

    def test_outage_is_provider_unavailable_not_invalid(self):
        """Audit C1: a proxy/transport outage makes yfinance return empty frames
        with no metadata for EVERY symbol. That is a retryable provider failure,
        never "AAPL was not found"."""
        self._patch_yf(lambda variant: _FakeTicker(rows=0, info={}))
        obj = _make_analyzer()
        data, status, meta = obj._fetch_price_data_classified("AAPL")
        self.assertEqual(status, DataStatus.PROVIDER_DATA_UNAVAILABLE)
        self.assertTrue(meta.get("provider_unreachable"))
        self.assertIsNone(data)

    def test_probe_exception_counts_as_unreachable(self):
        def factory(variant):
            if variant == "SPY":
                return _FakeTicker(raise_exc=ConnectionError("CONNECT tunnel failed, response 403"))
            return _FakeTicker(rows=0, info={})
        self._patch_yf(factory)
        obj = _make_analyzer()
        _data, status, _meta = obj._fetch_price_data_classified("AAPL")
        self.assertEqual(status, DataStatus.PROVIDER_DATA_UNAVAILABLE)


class AnalyzeStatusMappingTest(unittest.TestCase):
    """analyze() must map fetch status onto is_invalid / final_action correctly."""

    def test_provider_failure_is_not_invalid(self):
        obj = _make_analyzer()
        obj._fetch_price_data_classified = lambda t: (
            None, DataStatus.PROVIDER_DATA_UNAVAILABLE,
            {"rows": 0, "from_cache": False, "variant": "C", "range": "2y/1d",
             "rate_limited": False, "exception": None},
        )
        res = TA.QuantaEliteAnalyzer.analyze(obj, "C")
        self.assertFalse(res.is_invalid)
        self.assertEqual(res.data_status, DataStatus.PROVIDER_DATA_UNAVAILABLE)
        self.assertEqual(res.final_action, "")
        self.assertTrue(res.data_status_reason)

    def test_rate_limited_is_not_invalid(self):
        obj = _make_analyzer()
        obj._fetch_price_data_classified = lambda t: (
            None, DataStatus.PROVIDER_RATE_LIMITED,
            {"rows": 0, "from_cache": False, "variant": "FFBC", "range": "2y/1d",
             "rate_limited": True, "exception": "429"},
        )
        res = TA.QuantaEliteAnalyzer.analyze(obj, "FFBC")
        self.assertFalse(res.is_invalid)
        self.assertEqual(res.data_status, DataStatus.PROVIDER_RATE_LIMITED)
        self.assertEqual(res.final_action, "")

    def test_invalid_ticker_is_invalid(self):
        obj = _make_analyzer()
        obj._fetch_price_data_classified = lambda t: (
            None, DataStatus.INVALID_TICKER,
            {"rows": 0, "from_cache": False, "variant": None, "range": None,
             "rate_limited": False, "exception": None},
        )
        res = TA.QuantaEliteAnalyzer.analyze(obj, "FAKE123")
        self.assertTrue(res.is_invalid)
        self.assertEqual(res.data_status, DataStatus.INVALID_TICKER)
        if TA.HAS_DECISION_CORE:
            self.assertEqual(res.final_action, "INVALID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
