"""One result must not recommend buying and avoiding at the same time.

Audit finding F03: `_compute_beginner_guidance` derived its candlestick timing
verdict independently of the resolved decision. A bullish confluence branch set
``buy_now = "YES"`` and ``timing = "GOOD BUY SETUP"``, and the sell override
further down rewrote only the audience rows (``new_buyer``, ``existing_holder``,
``aggressive``) — leaving a single tear sheet that said "Buy Now: YES / GOOD BUY
SETUP" beside "New buyer: Avoid".

The rule pinned here: candlestick timing may explain conditions, but it may
never promote an action beyond the resolved final decision.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
MODULES = Path(__file__).resolve().parents[1] / "modules"
for _p in (CORE, MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ta = importlib.import_module("TradingAnalysis_ELITE")

# buy_now values ordered from most to least permissive.
_PERMISSIVE = "YES"
_CONFIRM = "ONLY AFTER CONFIRMATION"
_NO = "NO"


def _bullish_confluence_result(signal, final_action: str) -> "ta.AnalysisResult":
    """A result whose candles scream 'buy' while the decision says otherwise."""
    r = ta.AnalysisResult(ticker="TEST")
    r.signal = signal
    r.final_action = final_action
    r.confidence_score = 70.0
    r.current_price = 100.0

    t = r.technical
    t.candlestick_patterns = [{
        "name": "Bullish Engulfing",
        "bias": "BULLISH",
        "reliability": "HIGH",
        "type": "REVERSAL",
    }]
    t.candlestick_bias = "BULLISH"
    t.prior_trend = "DOWN"
    t.macd_signal = "BULLISH"
    t.price_vs_sma50 = 2.0
    t.volume_ratio = 1.8

    # A clean 2:1 setup, so the reward/risk downgrade cannot be what saves us.
    ts = r.trade_setup
    ts.entry_price = 100.0
    ts.stop_loss = 95.0
    ts.target_base = 110.0
    ts.risk_reward_ratio = 2.0

    r.risk.risk_grade = "B"
    return r


# ---------------------------------------------------------------------------
# The reproduction from the audit
# ---------------------------------------------------------------------------

def test_sell_result_never_says_buy_now_yes():
    r = _bullish_confluence_result(ta.Signal.SELL, "AVOID")
    g = ta._compute_beginner_guidance(r)

    assert g["buy_now"] == _NO
    assert g["timing"] != "GOOD BUY SETUP"


def test_sell_result_action_sentence_does_not_invite_an_entry():
    r = _bullish_confluence_result(ta.Signal.SELL, "AVOID")
    g = ta._compute_beginner_guidance(r)

    assert "starter entry" not in g["beginner_action"].lower()


def test_sell_result_guidance_rows_stay_mutually_consistent():
    r = _bullish_confluence_result(ta.Signal.SELL, "AVOID")
    g = ta._compute_beginner_guidance(r)

    # The resolved AVOID wording names what is forbidden: a NEW buy.
    assert g["new_buyer"] == "Avoid new buy"
    assert g["short_tf"] == "Avoid"
    # The headline and the audience rows must agree.
    assert g["buy_now"] != _PERMISSIVE


# ---------------------------------------------------------------------------
# The general invariant, across every final action
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("final_action", ["AVOID", "INVALID"])
def test_bearish_final_actions_forbid_any_buy_now(final_action):
    r = _bullish_confluence_result(ta.Signal.HOLD, final_action)
    g = ta._compute_beginner_guidance(r)

    assert g["buy_now"] == _NO


@pytest.mark.parametrize("final_action", ["WATCHLIST", "HOLD_EXISTING"])
def test_non_allocating_final_actions_cap_buy_now_at_confirmation(final_action):
    r = _bullish_confluence_result(ta.Signal.HOLD, final_action)
    g = ta._compute_beginner_guidance(r)

    assert g["buy_now"] in (_CONFIRM, _NO)


@pytest.mark.parametrize("final_action", ["STRONG_BUY", "BUY", "ACCUMULATE"])
def test_allocating_final_actions_still_allow_a_confirmed_buy(final_action):
    """The clamp must not flatten genuine buy setups into permanent 'NO'."""
    r = _bullish_confluence_result(ta.Signal.BUY, final_action)
    g = ta._compute_beginner_guidance(r)

    assert g["buy_now"] == _PERMISSIVE
    assert g["timing"] == "GOOD BUY SETUP"


def test_buy_now_yes_implies_the_new_buyer_row_is_not_a_refusal():
    """The forbidden combination, asserted directly over every final action."""
    forbidden = {"Avoid", "Avoid new buy; watchlist only", f"Avoid new buy {chr(0x2014)} watchlist only"}
    for final_action in ("STRONG_BUY", "BUY", "ACCUMULATE", "HOLD_EXISTING",
                         "WATCHLIST", "AVOID", "INVALID"):
        for signal in (ta.Signal.STRONG_BUY, ta.Signal.BUY, ta.Signal.HOLD,
                       ta.Signal.SELL, ta.Signal.STRONG_SELL):
            r = _bullish_confluence_result(signal, final_action)
            g = ta._compute_beginner_guidance(r)
            if g["buy_now"] == _PERMISSIVE:
                assert g["new_buyer"] not in forbidden, (
                    f"{final_action}/{signal} produced Buy Now: YES "
                    f"alongside '{g['new_buyer']}'"
                )
                assert g["short_tf"] != "Avoid", (
                    f"{final_action}/{signal} produced Buy Now: YES "
                    f"alongside short-timeframe 'Avoid'"
                )


def test_guidance_without_a_resolved_final_action_falls_back_to_the_signal():
    """An unresolved decision must not re-open the contradiction."""
    r = _bullish_confluence_result(ta.Signal.STRONG_SELL, "")
    g = ta._compute_beginner_guidance(r)

    assert g["buy_now"] == _NO
