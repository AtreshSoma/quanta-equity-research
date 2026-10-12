"""Unit tests for the CLI action-label classifier in SP_ELITE_PRO.

These guard against the ACCUMULATE-mislabeled-as-HOLD regression and ensure
the summary table and detail cards always derive their label from the same
decision-core source field.

Run from the workspace root with:

    python -m pytest tests/test_display_action.py -q

Or directly:

    python tests/test_display_action.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make ``core`` importable when running this file directly.
_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import SP_ELITE_PRO  # noqa: E402
from SP_ELITE_PRO import classify_display_action  # noqa: E402


class TestDisplayActionLabels(unittest.TestCase):
    """label = classify_display_action(signal)[0]."""

    def _label(self, signal, has_position=False):
        return classify_display_action(signal, has_position)[0]

    def _final(self, signal, has_position=False):
        return classify_display_action(signal, has_position)[2]

    def test_strong_buy_displays_as_strong_buy(self):
        self.assertIn("STRONG BUY", self._label("STRONG_BUY"))

    def test_buy_displays_as_buy(self):
        self.assertEqual(self._label("BUY"), "BUY")

    def test_accumulate_displays_as_accumulate(self):
        # The core regression: ACCUMULATE must NOT fall through to HOLD.
        self.assertEqual(self._label("ACCUMULATE"), "ACCUMULATE")

    def test_hold_displays_as_hold_or_monitor(self):
        # HOLD with no position -> MONITOR (watchlist); with a position -> HOLD.
        self.assertEqual(self._label("HOLD", has_position=False), "MONITOR")
        self.assertEqual(self._label("HOLD", has_position=True), "HOLD")

    def test_sell_reduce_avoid_display_as_sell(self):
        for sig in ("SELL", "REDUCE", "STRONG_SELL"):
            self.assertEqual(
                self._label(sig, has_position=True), "SELL",
                msg=f"{sig} should render as SELL when a position is held",
            )

    def test_case_insensitive(self):
        self.assertEqual(self._label("accumulate"), "ACCUMULATE")
        self.assertEqual(self._label("Buy"), "BUY")

    def test_empty_signal_is_safe(self):
        # No signal -> non-actionable MONITOR, never a buy label.
        label, color, final, reason = classify_display_action("")
        self.assertEqual(label, "MONITOR")
        self.assertNotIn(final, ("STRONG_BUY", "BUY", "ACCUMULATE"))

    def test_final_action_is_source_field(self):
        # The reason trace must reference the same final_action that drives
        # the label, proving table and card share one source.
        label, color, final, reason = classify_display_action("ACCUMULATE")
        self.assertIn(final, reason)
        self.assertIn(label, reason)

    def test_buy_tier_signals_allocate_capital(self):
        for sig in ("STRONG_BUY", "BUY", "ACCUMULATE"):
            self.assertIn(
                self._final(sig),
                ("STRONG_BUY", "BUY", "ACCUMULATE"),
                msg=f"{sig} must remain a buy-tier final_action",
            )

    @unittest.skipUnless(SP_ELITE_PRO.HAS_DECISION_CORE, "decision-core required")
    def test_label_matches_decision_core(self):
        from engine import signal_to_final_action
        for sig in ("STRONG_BUY", "BUY", "ACCUMULATE", "HOLD", "SELL"):
            expected = signal_to_final_action(sig, False).value
            self.assertEqual(classify_display_action(sig, False)[2], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
