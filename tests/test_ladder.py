"""Audit F7: one decision ladder for both pages (audit section 6).

engine.ladder.decide composes the event gate, the resolved action, the grade
policy, the Strong Buy fold (owner decision Q4) and the holder rules, so Picks
(holding=None) and My Portfolio (with the position) read the same verdict.
"""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths()

from engine import thresholds as T  # noqa: E402
from engine.ladder import Holding, LadderInputs, decide  # noqa: E402
from engine.types import FinalAction as FA  # noqa: E402

DEAL = {"status": "flagged", "code": "PENDING_ACQUISITION", "text": "Pending cash acquisition at $50.00 (...)",
        "deal_price": 50.0, "spread_pct": 0.05}
DELIST = {"status": "flagged", "code": "DELISTING", "text": "Delisting of Common Stock filed (...)"}
UNAVAILABLE = {"status": "unavailable", "code": "EVENT_CHECK_UNAVAILABLE",
               "text": "Corporate-event check unavailable: SEC EDGAR did not answer."}


def d(action=FA.BUY, score=68.0, grade="B", codes=(), evidence="UNTESTED", event=None, holding=None):
    return decide(LadderInputs(action, score, grade, list(codes), evidence, event), holding)


# ── new money (Picks; holding=None) ──────────────────────────────────────────

@pytest.mark.parametrize("event,code", [(DEAL, "PENDING_ACQUISITION"), (DELIST, "DELISTING")])
def test_an_event_flag_is_avoid_with_its_reason(event, code):
    r = d(event=event)
    assert r.action is FA.AVOID and r.new_money == "AVOID"
    assert r.codes[0] == code and r.reasons[0] == event["text"]


def test_an_unavailable_event_check_is_never_funded():
    r = d(event=UNAVAILABLE)
    assert r.action is FA.WATCHLIST and not r.action.allocates_new_capital
    assert r.codes[0] == "EVENT_CHECK_UNAVAILABLE"


def test_a_score_below_the_avoid_band_stays_avoid():
    assert d(action=FA.AVOID, score=40.0).action is FA.AVOID


def test_grade_f_with_a_measured_red_flag_is_avoid():
    r = d(action=FA.WATCHLIST, grade="F", codes=["RISK_GRADE_F", "DCF_OVERVALUATION"])
    assert r.action is FA.AVOID


def test_grade_f_without_a_red_flag_is_watch_not_funded():
    r = d(action=FA.BUY, grade="F")
    assert r.action is FA.WATCHLIST and "RISK_GRADE_F" in r.codes
    assert r.size_multiplier == 0.0


def test_grade_d_is_allowed_at_half_size():
    r = d(action=FA.ACCUMULATE, grade="D", codes=["RISK_GRADE_D_CAP"])
    assert r.action is FA.ACCUMULATE
    assert r.size_multiplier == T.SIZING.RISK_GRADE_SIZE_MULTIPLIERS["D"] == 0.5


def test_strong_buy_folds_until_validated():
    """Owner decision Q4: no Strong Buy until the evidence status is VALIDATED."""
    folded = d(action=FA.STRONG_BUY, score=80.0, evidence="NO_EDGE")
    assert folded.action is FA.BUY and "STRONG_BUY_FOLDED" in folded.codes
    assert d(action=FA.STRONG_BUY, score=80.0, evidence="UNTESTED").action is FA.BUY
    assert d(action=FA.STRONG_BUY, score=80.0, evidence="VALIDATED").action is FA.STRONG_BUY


def test_a_clean_buy_stays_a_buy():
    r = d()
    assert r.action is FA.BUY and r.new_money == "ELIGIBLE" and r.codes == [] and r.size_multiplier == 1.0


# ── holders (My Portfolio; holding given) ────────────────────────────────────

def test_holder_hold():
    assert d(action=FA.WATCHLIST, score=60.0, holding=Holding(weight=0.05)).holder_action == "HOLD"


def test_holder_trim_on_a_reduce_band_score():
    r = d(action=FA.AVOID, score=40.0, holding=Holding(weight=0.05))
    assert (r.holder_action, r.holder_basis) == ("TRIM", "SCORE_REDUCE_BAND")


def test_holder_exit_needs_a_low_score_and_a_measured_red_flag():
    r = d(action=FA.AVOID, score=30.0, codes=["HEAVY_INSIDER_SELLING"], holding=Holding(weight=0.05))
    assert (r.holder_action, r.holder_basis) == ("EXIT", "SCORE_EXIT_CORROBORATED")
    assert d(action=FA.AVOID, score=30.0, holding=Holding(weight=0.05)).holder_action == "TRIM"


def test_holder_grade_f_is_a_trim_review_at_any_score():
    """A grade-F holding with a hold-band score used to read HOLD: the F code is
    only attached when the candidate would have taken new money."""
    r = d(action=FA.WATCHLIST, score=47.9, grade="F", holding=Holding(weight=0.03))
    assert (r.holder_action, r.holder_basis) == ("TRIM", "RISK_GRADE_F")


def test_holder_concentration_trim():
    r = d(action=FA.WATCHLIST, score=60.0, holding=Holding(weight=T.PORTFOLIO.TRIM_REVIEW_POSITION + 0.01))
    assert (r.holder_action, r.holder_basis) == ("TRIM", "CONCENTRATION")


def test_holder_sell_on_a_pending_cash_deal_with_a_thin_spread():
    thin = dict(DEAL, spread_pct=T.EVENTS.ARB_SPREAD_MIN / 2)
    r = d(score=68.0, event=thin, holding=Holding(weight=0.05))
    assert (r.holder_action, r.holder_basis) == ("EXIT", "PENDING_ACQUISITION_SPREAD")
    assert d(score=68.0, event=DEAL, holding=Holding(weight=0.05)).holder_action == "HOLD", "wide spread: no sell"


def test_an_unavailable_event_check_never_changes_a_holders_action():
    held = d(action=FA.BUY, score=68.0, event=UNAVAILABLE, holding=Holding(weight=0.05))
    assert held.holder_action == "HOLD"
    assert held.action is FA.WATCHLIST, "but no new money while the check is unavailable"


def test_without_a_holding_the_holder_action_is_conditional_only():
    r = d(action=FA.WATCHLIST, score=60.0)
    assert r.holder_action == "HOLD" and r.holder_basis == "SCORE_HOLD"
