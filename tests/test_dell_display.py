"""Validation tests for the DELL single-stock display fixes in
TradingAnalysis_ELITE.

These guard the data-quality / wording fixes requested for high-volatility,
extended names like DELL (Risk Grade F, RSI overbought, R/R < 1):

  1. A Risk-Grade-F name with Buy Now = NO never shows a bare "HOLD" headline.
  2. A pullback level far below price is labelled a deep pullback, not a normal
     entry; nearer technical support (20/50-day MA, VWAP) is preferred.
  3. Direct-company news is separated from peer/sector and macro news.
  4. The blended/model target is labelled distinctly from the analyst consensus.
  5. Target confidence wording does not imply a validated probability.
  6. A sub-1.5:1 risk/reward downgrades a "Buy Now: YES" headline.

Run from the workspace root with:

    python -m pytest tests/test_dell_display.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

# Make ``core`` importable when running this file directly.
_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import TradingAnalysis_ELITE as ta  # noqa: E402


class TestContextualRecommendationLabel(unittest.TestCase):
    def test_risk_f_buy_no_never_plain_hold(self):
        label = ta._contextual_recommendation_label("HOLD", "", "F", "NO")
        self.assertNotEqual(label.upper(), "HOLD")
        self.assertIn("AVOID NEW BUY", label.upper())
        self.assertIn("HOLD EXISTING", label.upper())

    def test_risk_d_buy_no_waits_for_entry(self):
        label = ta._contextual_recommendation_label("HOLD", "", "D", "NO")
        self.assertIn("HOLD EXISTING", label.upper())
        self.assertIn("WAIT", label.upper())

    def test_normal_setup_keeps_base_label(self):
        label = ta._contextual_recommendation_label("BUY", "", "B", "YES")
        self.assertEqual(label, "BUY")

    def test_conviction_prefix_preserved(self):
        label = ta._contextual_recommendation_label("HOLD", "LOW-CONVICTION", "B", "NO")
        self.assertEqual(label, "LOW-CONVICTION HOLD")

    def test_sell_signal_untouched(self):
        label = ta._contextual_recommendation_label("STRONG SELL", "", "F", "NO")
        self.assertIn("SELL", label.upper())


class TestPullbackEntry(unittest.TestCase):
    def _tech(self, **kw):
        defaults = dict(sma_20=0.0, sma_50=0.0, vwap=0.0,
                        support_level=0.0, atr=0.0)
        defaults.update(kw)
        return SimpleNamespace(**defaults)

    def test_prefers_nearest_support_below_price(self):
        # Price 441; 20DMA 420 (nearest), 50DMA 400, far support 227.
        t = self._tech(sma_20=420.0, sma_50=400.0, support_level=227.0, atr=12.0)
        entry, stop, is_deep, basis = ta._compute_pullback_entry(t, 441.0)
        self.assertEqual(entry, 420.0)
        self.assertEqual(basis, "20-day MA")
        self.assertFalse(is_deep)
        self.assertLess(stop, entry)

    def test_far_only_support_flagged_deep(self):
        # Only a deep support far below price -> deep pullback.
        t = self._tech(support_level=227.0)
        entry, stop, is_deep, basis = ta._compute_pullback_entry(t, 441.0)
        self.assertEqual(entry, 227.0)
        self.assertTrue(is_deep)  # 227 is ~48% below 441

    def test_no_support_returns_zero(self):
        t = self._tech()  # nothing below price
        entry, stop, is_deep, basis = ta._compute_pullback_entry(t, 441.0)
        self.assertEqual(entry, 0.0)
        self.assertEqual(stop, 0.0)
        self.assertFalse(is_deep)

    def test_levels_above_price_ignored(self):
        t = self._tech(sma_20=500.0, support_level=410.0)
        entry, _stop, _deep, basis = ta._compute_pullback_entry(t, 441.0)
        self.assertEqual(entry, 410.0)
        self.assertEqual(basis, "support zone")


class TestTacticalStop(unittest.TestCase):
    def test_tactical_tighter_than_long_term(self):
        t = SimpleNamespace(sma_20=420.0, support_level=400.0, atr=15.0)
        long_term = 350.0  # wide long-term holder stop
        tactical, basis = ta._compute_tactical_stop(t, 441.0, long_term)
        self.assertGreater(tactical, long_term)  # tighter = closer to price
        self.assertLess(tactical, 441.0)
        self.assertTrue(basis)

    def test_no_data_returns_zero(self):
        t = SimpleNamespace(sma_20=0.0, support_level=0.0, atr=0.0)
        tactical, basis = ta._compute_tactical_stop(t, 0.0, 0.0)
        self.assertEqual(tactical, 0.0)


class TestNewsRelevance(unittest.TestCase):
    def _articles(self):
        return [
            {"title": "Dell Technologies beats earnings, raises guidance"},
            {"title": "Super Micro Computer rallies on AI server demand"},
            {"title": "HPE expands enterprise storage lineup"},
            {"title": "Federal Reserve signals interest rate cut amid inflation data"},
        ]

    def test_direct_peer_macro_separated(self):
        out = ta._classify_news_relevance(
            self._articles(), "DELL", company_name="Dell Technologies Inc",
            peer_tickers=["SMCI", "HPE"], peer_names=["Super Micro", "HPE"])
        self.assertTrue(out["has_direct_news"])
        self.assertEqual(out["direct_count"], 1)
        self.assertTrue(any("Dell" in h for h in out["direct_headlines"]))
        # Peer headlines must not leak into direct.
        self.assertGreaterEqual(out["peer_count"], 1)
        self.assertTrue(any("Super Micro" in h for h in out["peer_headlines"]))
        # Macro (Fed) sits in its own bucket.
        self.assertGreaterEqual(out["macro_count"], 1)
        self.assertTrue(any("Federal Reserve" in h for h in out["macro_headlines"]))

    def test_no_direct_news_dampened(self):
        articles = [
            {"title": "Super Micro Computer surges on strong AI guidance"},
            {"title": "Nasdaq rallies broadly on tech optimism"},
        ]
        out = ta._classify_news_relevance(
            articles, "DELL", company_name="Dell Technologies Inc",
            peer_tickers=["SMCI"], peer_names=["Super Micro"])
        self.assertFalse(out["has_direct_news"])
        self.assertEqual(out["direct_count"], 0)
        # Weighted score is dampened relative to the raw peer score.
        self.assertLessEqual(abs(out["weighted_sentiment_score"]),
                             abs(out["peer_sentiment_score"]) + 1e-9)


class TestFormatRR(unittest.TestCase):
    def test_below_1_5_is_unfavorable(self):
        self.assertIn("Unfavorable", ta._format_rr(0.5, 460.0, 430.0, 441.0))

    def test_target_below_price_unfavorable(self):
        self.assertEqual(ta._format_rr(2.0, 430.0, 400.0, 441.0), "Unfavorable")

    def test_acceptable_rr(self):
        # Reward/risk prints at UI.RR_DECIMALS (2 dp) everywhere (audit F2/F9).
        self.assertEqual(ta._format_rr(2.0, 480.0, 420.0, 441.0), "2.00:1")


class TestDisplaySourceContracts(unittest.TestCase):
    """Source-level guards for the wording/label fixes that are otherwise only
    exercised through a full live analysis run."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_CORE / "TradingAnalysis_ELITE.py").read_text(encoding="utf-8")

    def test_blended_target_label_distinct(self):
        self.assertIn("BLENDED TARGET (model)", self.src)
        # The confusing duplicate "CONSENSUS TARGET" breakdown row is gone.
        self.assertNotIn('add_row("CONSENSUS TARGET"', self.src)

    def test_confidence_wording_not_validated_probability(self):
        self.assertIn("Target Confidence: Heuristic", self.src)
        self.assertNotIn("Confidence: {v.target_confidence:.0f}%", self.src)

    def test_rr_downgrades_buy_now(self):
        self.assertIn("rr_unfavorable", self.src)
        self.assertIn('buy_now = "ONLY AFTER CONFIRMATION"', self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
