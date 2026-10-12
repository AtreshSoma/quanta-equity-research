"""[PERF-FASTMODE] Tests for the tear-sheet Fast Mode path.

Validates the structural pieces — without hitting the network — that:

  1. ``AnalysisResult`` exposes a ``fast_mode`` flag (default False).
  2. ``_run_parallel_analysis(fast_mode=True)`` skips the four optional
     network-heavy modules (sentiment / insider / market_intel / catalysts)
     and substitutes neutral defaults, while still running the
     local-data modules (technical / risk) and the cached fundamental call.
  3. The MCP serializer (``_mcp_result_to_dict``) round-trips ``fast_mode``.
  4. The skipped-module substitutes match the default-constructed dataclasses
     so the downstream signal generator never sees a None.

The peer-comparison skip and the cache-write skip are guarded in
``analyze()`` and ``webapp/app.py`` respectively; both branches are simple
boolean gates that the integration test in ``tests/uat_asset_types.py``
exercises end-to-end against a live ticker.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

import pandas as pd

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import core.TradingAnalysis_ELITE as ta  # noqa: E402


def _build_price_frame(rows: int = 260) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    base = 100.0
    closes = [base + i * 0.1 for i in range(rows)]
    df = pd.DataFrame({
        "Open": closes, "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes], "Close": closes,
        "Volume": [1_000_000] * rows,
    }, index=idx)
    return df


class TestFastModeFlag(unittest.TestCase):
    def test_analysis_result_has_fast_mode_default_false(self) -> None:
        r = ta.AnalysisResult()
        self.assertFalse(r.fast_mode)
        self.assertIsInstance(r.fast_mode, bool)

    def test_mcp_serializer_round_trips_fast_mode(self) -> None:
        r = ta.AnalysisResult()
        r.ticker = "AAPL"
        r.fast_mode = True
        d = ta._mcp_result_to_dict(r)
        self.assertIn("fast_mode", d)
        self.assertTrue(d["fast_mode"])

        r.fast_mode = False
        d2 = ta._mcp_result_to_dict(r)
        self.assertFalse(d2["fast_mode"])


class TestFastModeParallelAnalysis(unittest.TestCase):
    """Verify ``_run_parallel_analysis(fast_mode=True)`` skips the four
    optional network-heavy analyzers and seeds neutral defaults."""

    def _make_analyzer(self) -> ta.QuantaEliteAnalyzer:
        # Bypass __init__ — we only need the analyzer slots to attach mocks.
        analyzer = object.__new__(ta.QuantaEliteAnalyzer)
        analyzer.technical_analyzer = MagicMock(
            analyze=MagicMock(return_value=ta.TechnicalIndicators()))
        analyzer.risk_analyzer = MagicMock(
            analyze=MagicMock(return_value=ta.RiskMetrics()))
        analyzer.fundamental_analyzer = MagicMock(
            analyze=MagicMock(return_value=ta.FundamentalData()))
        # These four MUST NOT be called in fast mode. We track that.
        analyzer.sentiment_analyzer = MagicMock(analyze=MagicMock())
        analyzer.insider_analyzer = MagicMock(analyze=MagicMock())
        analyzer.market_intel_analyzer = MagicMock(analyze=MagicMock())
        analyzer.catalyst_analyzer = MagicMock(analyze=MagicMock())
        analyzer.valuation_analyzer = MagicMock(
            analyze=MagicMock(return_value=ta.ValuationMetrics()))
        return analyzer

    def test_fast_mode_skips_optional_modules(self) -> None:
        analyzer = self._make_analyzer()
        result = ta.AnalysisResult()
        result.current_price = 100.0
        result.sector = ""  # block downstream peer pull just in case

        df = _build_price_frame()
        out = analyzer._run_parallel_analysis("AAPL", df, result, fast_mode=True)

        # Optional modules NOT invoked.
        analyzer.sentiment_analyzer.analyze.assert_not_called()
        analyzer.insider_analyzer.analyze.assert_not_called()
        analyzer.market_intel_analyzer.analyze.assert_not_called()
        analyzer.catalyst_analyzer.analyze.assert_not_called()

        # Local-data modules WERE invoked.
        analyzer.technical_analyzer.analyze.assert_called_once()
        analyzer.risk_analyzer.analyze.assert_called_once()
        analyzer.fundamental_analyzer.analyze.assert_called_once()

        # Skipped modules are seeded with neutral defaults so the signal
        # generator never sees a None.
        self.assertIsInstance(out.sentiment, ta.SentimentData)
        self.assertIsInstance(out.insider, ta.InsiderData)
        self.assertIsInstance(out.market_intel, ta.MarketIntelligence)
        self.assertIsInstance(out.catalysts, ta.CatalystData)
        # Neutral defaults match a freshly constructed dataclass.
        self.assertEqual(out.sentiment.sentiment_label,
                         ta.SentimentData().sentiment_label)
        self.assertEqual(out.insider.insider_sentiment,
                         ta.InsiderData().insider_sentiment)

    def test_full_mode_runs_every_module(self) -> None:
        analyzer = self._make_analyzer()
        analyzer.sentiment_analyzer.analyze.return_value = ta.SentimentData()
        analyzer.insider_analyzer.analyze.return_value = ta.InsiderData()
        analyzer.market_intel_analyzer.analyze.return_value = (
            ta.MarketIntelligence())
        analyzer.catalyst_analyzer.analyze.return_value = ta.CatalystData()

        result = ta.AnalysisResult()
        result.current_price = 100.0
        df = _build_price_frame()
        analyzer._run_parallel_analysis("AAPL", df, result, fast_mode=False)

        # All 7 modules must be hit when fast_mode is off (existing contract).
        analyzer.sentiment_analyzer.analyze.assert_called_once()
        analyzer.insider_analyzer.analyze.assert_called_once()
        analyzer.market_intel_analyzer.analyze.assert_called_once()
        analyzer.catalyst_analyzer.analyze.assert_called_once()


if __name__ == "__main__":
    unittest.main()
