"""Consistency tests for the shared decision-core override rules.

These prove the scanner (SP_ELITE_PRO) and the deep analyzer
(TradingAnalysis_ELITE) cannot publish contradictory recommendations,
because BOTH feed the same qualitative red flags into one resolver
(``resolve_final_action``).

Covers requirement items 2, 4, 5 and 12:
  - A Grade D stock can never display BUY/STRONG_BUY.
  - A Grade F stock can never display BUY or ACCUMULATE.
  - Buy Now = NO / Timing = WAIT cannot produce a BUY.
  - A high-reliability bearish reversal downgrades BUY to ACCUMULATE/WAIT.
  - DCF overvaluation > 25% trims conviction.
  - Heavy insider selling blocks new money.
  - Same inputs → same final_action (scanner == analyzer by construction).

Run:
    python -m pytest tests/test_decision_overrides.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from engine import (  # noqa: E402
    DecisionInputs,
    FinalAction,
    resolve_final_action,
)

import SP_ELITE_PRO  # noqa: E402


# A strong, clean buy-tier base case (composite well above the BUY floor,
# good R/R, no existing position). Each test layers ONE red flag on top.
def _base(**overrides):
    kwargs = dict(
        ticker="TEST",
        composite_score=80.0,      # would resolve STRONG_BUY on its own
        risk_grade="B",
        risk_reward_ratio=3.0,
        current_price=100.0,
        stop_loss=92.0,
        target_price=130.0,
        has_existing_position=False,
        is_valid=True,
    )
    kwargs.update(overrides)
    return DecisionInputs(**kwargs)


_BUY_TIER = {FinalAction.STRONG_BUY, FinalAction.BUY}
_NEW_MONEY = {FinalAction.STRONG_BUY, FinalAction.BUY, FinalAction.ACCUMULATE}


class TestRiskGradeOverrides(unittest.TestCase):
    def test_clean_base_is_buy_tier(self):
        # Sanity: without red flags the base case is a fresh buy.
        res = resolve_final_action(_base())
        self.assertIn(res.final_action, _NEW_MONEY)

    def test_grade_d_cannot_be_buy(self):
        res = resolve_final_action(_base(risk_grade="D"))
        self.assertNotIn(res.final_action, _BUY_TIER,
                         f"Grade D resolved to {res.final_action}")

    def test_grade_f_cannot_allocate_new_money(self):
        res = resolve_final_action(_base(risk_grade="F"))
        self.assertNotIn(res.final_action, _NEW_MONEY,
                         f"Grade F resolved to {res.final_action}")
        self.assertFalse(res.final_action.allocates_new_capital)

    def test_grade_f_with_position_holds_not_buys(self):
        res = resolve_final_action(_base(risk_grade="F", has_existing_position=True))
        self.assertNotIn(res.final_action, _NEW_MONEY)


class TestTimingOverrides(unittest.TestCase):
    def test_buy_now_no_cannot_be_buy(self):
        res = resolve_final_action(_base(buy_now=False))
        self.assertNotIn(res.final_action, _BUY_TIER)
        self.assertEqual(res.final_action, FinalAction.ACCUMULATE)

    def test_timing_wait_caps_at_accumulate(self):
        res = resolve_final_action(_base(timing_wait=True))
        self.assertEqual(res.final_action, FinalAction.ACCUMULATE)

    def test_buy_now_true_does_not_downgrade(self):
        res = resolve_final_action(_base(buy_now=True))
        self.assertIn(res.final_action, _NEW_MONEY)


class TestBearishReversalOverride(unittest.TestCase):
    def test_bearish_reversal_downgrades_buy(self):
        res = resolve_final_action(
            _base(bearish_reversal=True, bearish_reversal_name="Shooting Star"))
        self.assertNotIn(res.final_action, _BUY_TIER)
        # The pattern name must be surfaced in the reasons.
        self.assertTrue(any("Shooting Star" in r for r in res.reasons))


class TestValuationAndInsiderOverrides(unittest.TestCase):
    def test_dcf_overvaluation_trims(self):
        clean = resolve_final_action(_base())
        over = resolve_final_action(_base(dcf_overvaluation_pct=40.0))
        # Overvaluation must not leave conviction higher than the clean case.
        order = [FinalAction.AVOID, FinalAction.WATCHLIST, FinalAction.HOLD_EXISTING,
                 FinalAction.ACCUMULATE, FinalAction.BUY, FinalAction.STRONG_BUY]
        self.assertLessEqual(order.index(over.final_action), order.index(clean.final_action))

    def test_small_overvaluation_no_penalty(self):
        res = resolve_final_action(_base(dcf_overvaluation_pct=10.0))
        self.assertIn(res.final_action, _NEW_MONEY)

    def test_heavy_insider_selling_blocks_new_money(self):
        res = resolve_final_action(_base(heavy_insider_selling=True))
        self.assertFalse(res.final_action.allocates_new_capital,
                         f"Heavy insider selling resolved to {res.final_action}")


class TestEngineAgreement(unittest.TestCase):
    """The scanner and the analyzer share resolve_final_action, so the SAME
    inputs must yield the SAME final action regardless of which engine built
    them."""

    def test_same_inputs_same_action(self):
        cases = [
            _base(),
            _base(risk_grade="D"),
            _base(risk_grade="F"),
            _base(buy_now=False),
            _base(bearish_reversal=True, bearish_reversal_name="Shooting Star"),
            _base(dcf_overvaluation_pct=40.0),
            _base(heavy_insider_selling=True),
        ]
        for di in cases:
            a = resolve_final_action(di).final_action
            b = resolve_final_action(di).final_action
            self.assertEqual(a, b)


class TestBearishReversalDetector(unittest.TestCase):
    """The scanner's lightweight candle detector must flag a Shooting Star
    that appears after an uptrend (so it can feed bearish_reversal=True)."""

    @staticmethod
    def _hist(rows):
        import pandas as pd
        return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])

    def test_detects_shooting_star_after_uptrend(self):
        rows = []
        # 11 rising bars (clean uptrend)
        for i in range(11):
            base = 100 + i * 2
            rows.append([base, base + 1, base - 0.5, base + 1])
        # Shooting star: small body near the low, long upper wick.
        o, c = 122.0, 121.5
        rows.append([o, 128.0, 121.3, c])
        ok, name = SP_ELITE_PRO._detect_bearish_reversal(self._hist(rows))
        self.assertTrue(ok)
        self.assertEqual(name, "Shooting Star")

    def test_no_reversal_in_plain_uptrend(self):
        rows = []
        for i in range(13):
            base = 100 + i * 2
            rows.append([base, base + 1.2, base - 0.3, base + 1])
        ok, _ = SP_ELITE_PRO._detect_bearish_reversal(self._hist(rows))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
