"""Regressions for the four defects that emptied the trade book.

A 2026-09-09 scan at $2,500 capital published zero positions while its own
research board carried NVDA at 7.00/10 with R:R 2.00 and +19% upside. None of
the four causes was a threshold being too strict; each was a logic error that
made a sound candidate structurally unreachable.

Measured on a 99-name liquid sample before the fix:
  * median R:R 2.05, and 55% of names already clear the 1.8 hurdle -- so the
    hurdle itself is sound and is deliberately NOT touched here;
  * the 5 names sitting at their 52-week high had median R:R -1.33, and 3 of
    them carried a price target below their own entry.

Ported from ``quanta-review-redesign`` @ 064f747 (audit F15). That branch is not
an ancestor of this one, so the file was copied and adapted:

* ``minimum_score_for_action`` is imported from the decision core, where it
  lives here, rather than re-exported from SP_ELITE_PRO.
* Defect 2 tests use ``TradingConfig.effective_min_position_size`` and
  ``_fund_at_least_one_share``, added by audit A06 (formerly F11 strict xfails).
* Defect 3 (one cooldown abandoning the universe) is fixed on this line by a
  different design: PROVIDER_STATE.reset() at scan start plus bounded
  wait-and-resume in scan_market. Its regressions live in
  test_elite_pro_review2_fixes.py (test_throttled_scan_resumes_instead_of_halting,
  test_hour_long_retry_after_stops_the_scan_instead_of_sleeping).
"""

from __future__ import annotations

import pytest

import SP_ELITE_PRO as sp
from engine.decision_engine import minimum_score_for_action

# ── Defect 1: a stock at its 52-week high was given no target ───────────────
#
# Both structural candidates are gated on `> price * 1.02`, so a breakout name
# drops the 20-day high AND the 52-week high, leaving a stale analyst target as
# the sole definition of reward. Analyst targets lag price, so for a name at a
# new high that target usually sits BELOW spot -- producing a "trade" whose
# target is under its entry. The ATR fallback exists for exactly this case but
# only fires when the candidate list is completely empty.

def _blue_sky_indicators(price: float, atr: float) -> sp.TechnicalIndicators:
    ind = sp.TechnicalIndicators()
    ind.current_price = price
    ind.atr = atr
    ind.atr_percent = atr / price
    # At a new high there is nothing overhead: both levels sit at spot, so both
    # fall inside the 2% band and are discarded by the candidate filter.
    ind.resistance = price
    ind.high_52w = price
    ind.low_52w = price * 0.6
    # Far enough below that the structural-support adjustment cannot widen the
    # stop; this test is about the reward leg.
    ind.support = price * 0.80
    return ind


def _targets(price, ind, analyst_target):
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.market_regime = sp.MarketRegime(regime_type="BULL")
    return sp.QuantaEliteTradingSystem._calculate_price_targets(
        eng, price, ind, sp.RiskMetrics(volatility=0.28), analyst_target=analyst_target
    )


def test_blue_sky_name_never_gets_a_target_below_its_entry():
    """VLO's exact shape: at an all-time high, analysts 15% below spot.

    Before the fix this returned target 330.21 against entry 388.95 -- a long
    whose target is below its own entry, which is not a trade at all. It
    happened to the highest-scoring name in the scan (8.48/10).
    """
    price = 388.95
    ind = _blue_sky_indicators(price, atr=10.57)
    entry, target, stop = _targets(price, ind, analyst_target=330.21)

    assert stop < entry, "stop must stay below entry"
    assert target >= entry, (
        f"target {target:.2f} is below entry {entry:.2f}: a long cannot have a "
        f"target under its own entry"
    )


def test_blue_sky_target_is_anchored_on_the_same_ruler_as_the_stop():
    """With no overhead structure the measured move IS the objective.

    The stop is 2.75 ATR and TARGET_ATR_MULTIPLIER is 5.5 ATR, so a blue-sky
    name with no analyst coverage must land near 2:1 -- reward and risk
    measured with the same instrument.
    """
    price, atr = 100.0, 3.0
    ind = _blue_sky_indicators(price, atr=atr)
    entry, target, stop = _targets(price, ind, analyst_target=0.0)

    rr = (target - entry) / (entry - stop)
    assert rr == pytest.approx(2.0, abs=0.25), f"blue-sky R:R was {rr:.2f}"


def test_overhead_structure_still_caps_the_target():
    """The fix must be surgical: a name BELOW its 52-week high is untouched.

    94 of the 99 sampled names behave correctly today (median R:R 2.13). Their
    reward is real overhead structure, not a volatility projection, and must
    not be perturbed.
    """
    price = 100.0
    ind = _blue_sky_indicators(price, atr=3.0)
    ind.resistance = 108.0
    ind.high_52w = 112.0
    _, target, _ = _targets(price, ind, analyst_target=110.0)

    assert target == pytest.approx((108.0 + 112.0 + 110.0) / 3.0, abs=0.01), (
        "a name with overhead structure must keep averaging those levels"
    )


# ── Defect 2: an absolute $100 floor inside a percentage-risk sizer ─────────
#
# At $2,500 the per-trade risk budget is ~$12, which at a normal 9% stop buys a
# ~$135 position; any legitimate haircut (risk grade C x0.80, FOMC week x0.85)
# drops it under the flat $100 floor. NVDA was rejected at $92 -- 3.7% of the
# account, and a perfectly ordinary position size.

def test_minimum_position_scales_with_capital():
    small = sp.TradingConfig.effective_min_position_size(2_500.0)
    assert small < sp.TradingConfig.MIN_POSITION_SIZE, (
        "a $100 floor is 4% of a $2,500 account -- a core position, not noise"
    )
    assert small >= 25.0, "the floor must still exclude genuinely trivial trades"


def test_large_accounts_keep_the_absolute_floor():
    """$100 is noise in a six-figure book; that behaviour must not change."""
    for capital in (25_000.0, 100_000.0):
        assert sp.TradingConfig.effective_min_position_size(capital) == pytest.approx(
            sp.TradingConfig.MIN_POSITION_SIZE
        )


def test_small_account_can_fund_a_position_after_legitimate_haircuts():
    """NVDA's exact shape: 9.3% stop, risk grade C, scanned in an FOMC week.

    The risk sizer produced ~$135 and the two haircuts took it to ~$92, which
    the flat floor then rejected -- so a name passing every published gate
    (score 7.00, R:R 2.00, +19.0% upside, grade C) carried no capital.
    """
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.capital = 2_500.0
    eng.drawdown_multiplier = 1.0

    size = sp.QuantaEliteTradingSystem._calculate_position_size(
        eng, sp.SignalType.BUY.value, score=7.0, entry=186.0, stop=168.7
    )
    assert size > 0.0, "the risk sizer itself must produce a position"

    funded = size * 0.85 * sp.TradingConfig.RISK_GRADE_SIZE_MULTIPLIERS["C"]
    assert funded >= sp.TradingConfig.effective_min_position_size(2_500.0), (
        f"${funded:.0f} is {funded / 2500 * 100:.1f}% of the account and must be "
        f"fundable"
    )


def test_whole_share_rounding_does_not_demote_the_action():
    """A $92 budget on a $186 share is 0 shares -- but 1 share still fits.

    Rescoring answered that by setting the ACTION to WATCHLIST, which removed
    the name from portfolio selection altogether -- even though selection
    already carries a 1-share path that checks the per-position cap. Share
    granularity is arithmetic, not a change of thesis.
    """
    rec = sp.StockAnalysis(ticker="NVDA", company="NVIDIA", sector="Technology",
                           industry="Semiconductors")
    rec.entry_price, rec.stop_loss, rec.price_target = 186.0, 168.7, 221.0
    rec.final_action = "BUY"

    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.capital, eng.drawdown_multiplier = 2_500.0, 1.0

    fractional = sp.TradingConfig.FRACTIONAL_SHARES
    sp.TradingConfig.FRACTIONAL_SHARES = False
    try:
        funded = sp.QuantaEliteTradingSystem._fund_at_least_one_share(
            eng, rec, allocation=92.0
        )
    finally:
        sp.TradingConfig.FRACTIONAL_SHARES = fractional

    assert funded == pytest.approx(186.0), (
        "one share is the smallest expressible position and sits inside both "
        "the position cap and the per-trade risk ceiling"
    )
    assert rec.shares == 1


def test_one_share_is_refused_when_it_breaches_the_position_cap():
    """MSFT at $492 against a $458 cap: genuinely unbuyable, and stays so."""
    rec = sp.StockAnalysis(ticker="MSFT", company="Microsoft", sector="Technology",
                           industry="Software")
    rec.entry_price, rec.stop_loss, rec.price_target = 492.0, 465.0, 547.0
    rec.final_action = "BUY"

    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.capital, eng.drawdown_multiplier = 2_500.0, 1.0

    fractional = sp.TradingConfig.FRACTIONAL_SHARES
    sp.TradingConfig.FRACTIONAL_SHARES = False
    try:
        funded = sp.QuantaEliteTradingSystem._fund_at_least_one_share(
            eng, rec, allocation=193.0
        )
    finally:
        sp.TradingConfig.FRACTIONAL_SHARES = fractional

    assert funded == 0.0, "a share costing more than the cap must not be forced in"


# ── Defect 4: two different new-money floors, only one of them advertised ───

def test_scanner_and_decision_core_agree_on_the_new_money_floor():
    """The gate log printed "Score>=5.0" while the core vetoed below 5.5.

    Names scoring 5.0-5.5 were generated as candidates and then silently
    downgraded by the conservatism ratchet -- GOOGL at 5.22 with R:R 3.25,
    +19.7% upside and risk grade B never had a chance, and the log gave the
    reader a floor that was not the one being applied.
    """
    if not sp.HAS_DECISION_CORE:
        pytest.skip("decision core unavailable")
    core_floor = minimum_score_for_action(sp.FinalAction.ACCUMULATE) / 10.0
    assert sp.TradingConfig.ACCUMULATE_SCORE == pytest.approx(core_floor), (
        "the scanner must advertise the floor the decision core enforces"
    )
