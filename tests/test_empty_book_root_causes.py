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
* Defect 3 tests exercise ``_run_scan_workers``, the scan-worker loop rewritten
  on that branch. It is outside the Picks audit and is not ported, so those tests
  are strict xfails recorded as unresolved in IMPLEMENTATION_LOG.md.
"""

from __future__ import annotations

import threading
import time

import pytest

import SP_ELITE_PRO as sp
from engine.decision_engine import minimum_score_for_action

BRANCH_ONLY_SCAN_WORKERS = pytest.mark.xfail(
    strict=True, raises=TypeError,
    reason="_run_scan_workers exists only on quanta-review-redesign; not in the Picks audit scope")


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


@pytest.mark.xfail(strict=True, reason="blue-sky target fix exists only on quanta-review-redesign; not in the Picks audit scope")
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


# ── Defect 3: one 60-second cooldown abandoned 90% of the universe ──────────

class _StubScan:
    """The minimal surface `_run_scan_workers` touches, with no network."""

    def __init__(self):
        self.dispatched: list[str] = []
        self._filter_stats, self._zero_alloc_reasons = {}, {}
        self._incomplete_session_bars = {}
        self._candidate_rejections, self._error_records = [], []
        self._zero_alloc_tickers, self.chase_alerts, self.failed_tickers = [], [], []
        self._provider_skipped = set()
        self._provider_degraded = False
        self._provider_circuit_open = False
        self._scan_stop_reason = ""
        self._filter_stats_lock = threading.Lock()
        self._failed_tickers_lock = threading.Lock()
        self._chase_alerts_lock = threading.Lock()
        self.circuit_breaker = sp.CircuitBreaker()

    TICKER_CORRECTIONS = sp.QuantaEliteTradingSystem.TICKER_CORRECTIONS
    _sanitize_ticker = sp.QuantaEliteTradingSystem._sanitize_ticker
    _record_failed_ticker = sp.QuantaEliteTradingSystem._record_failed_ticker
    _record_scan_error = sp.QuantaEliteTradingSystem._record_scan_error
    _record_filter = sp.QuantaEliteTradingSystem._record_filter
    # Borrowed so a raising worker is classified exactly as production does --
    # a 429 has to reach PROVIDER_STATE through the real path for the circuit
    # tests below to be testing anything.
    _classify_scan_error = staticmethod(sp.QuantaEliteTradingSystem._classify_scan_error)
    # Absent on this line of history; see BRANCH_ONLY_SCAN_WORKERS below.
    _run_scan_workers = getattr(sp.QuantaEliteTradingSystem, '_run_scan_workers', None)

    def analyze_stock(self, ticker):
        # `dispatched` survives the per-worker shallow copy because the list
        # object is shared, unlike the diagnostic buffers that get replaced.
        self.dispatched.append(ticker)
        return None


@BRANCH_ONLY_SCAN_WORKERS
def test_transient_cooldown_pauses_dispatch_but_does_not_abandon_the_scan():
    """A 60s cooldown inside a 30-minute budget is a pause, not an ending.

    The observed run analysed 79 of 808 names and stopped with 27 of its 30
    minutes unused, because the first cooldown latched `_provider_circuit_open`
    permanently. Honouring a Retry-After and abandoning the universe are
    different things, and only the first one was asked for.
    """
    stub = _StubScan()
    names = [f"AA{i:02d}" for i in range(12)]
    sp.PROVIDER_STATE.cooldown_until = time.time() + 0.4

    stub._run_scan_workers(names, max_workers=2, deadline=time.monotonic() + 60.0)

    assert len(stub.dispatched) == len(names), (
        f"only {len(stub.dispatched)}/{len(names)} names were dispatched; a "
        f"transient cooldown must not end the scan"
    )
    assert stub._provider_degraded, "the throttle must still be reported"


class _ThrottledScan(_StubScan):
    """A stub whose every analyze_stock call is rate-limited by the provider.

    The 429 is raised from the worker, so `_record_scan_error` classifies it
    and feeds PROVIDER_STATE exactly as production does. The throttle is
    therefore generated BY the scan rather than pre-loaded into the singleton
    before it starts -- see `test_a_previous_scans_throttle_does_not_abort_the_next_one`
    for why that distinction is the whole point.
    """

    def analyze_stock(self, ticker):
        self.dispatched.append(ticker)
        raise Exception("HTTP 429 Too Many Requests")


@BRANCH_ONLY_SCAN_WORKERS
def test_persistent_throttling_trips_the_circuit_instead_of_waiting():
    """Waiting out a cooldown is right; waiting out a dead provider is not.

    Making a cooldown non-fatal (so one 60s pause stops abandoning the
    universe) removed the only thing that stopped a scan retrying a provider
    that is throttling every request. `PROVIDER_RATE_LIMIT_TRIP_COUNT` and
    `_RATIO` existed for exactly this and were wired to nothing.

    Consecutive hits are the right counter, not total: 14 rate limits across
    480 healthy names is a scan worth finishing, 12 back-to-back is a provider
    that is down.

    The cooldown floor is shortened for the test only. It governs how long the
    scan naps between attempts, not how many consecutive hits are needed to
    trip, so the threshold under test is the real one.
    """
    stub = _ThrottledScan()
    trip = sp.TradingConfig.PROVIDER_RATE_LIMIT_TRIP_COUNT
    cooldown = sp.TradingConfig.RATE_LIMIT_COOLDOWN_SECONDS
    sp.TradingConfig.RATE_LIMIT_COOLDOWN_SECONDS = 0.05
    started = time.monotonic()
    try:
        stub._run_scan_workers(
            [f"CC{i:02d}" for i in range(trip * 4)], max_workers=4,
            deadline=started + 600.0,
        )
    finally:
        sp.TradingConfig.RATE_LIMIT_COOLDOWN_SECONDS = cooldown
    elapsed = time.monotonic() - started

    assert stub._provider_circuit_open, "persistent throttling must stop the scan"
    assert elapsed < 20.0, (
        f"took {elapsed:.0f}s: a persistently throttling provider must not be "
        f"waited out"
    )
    assert 'persistently' in stub._scan_stop_reason.lower(), stub._scan_stop_reason
    assert len(stub.dispatched) < trip * 4, "the circuit must stop further dispatch"


@BRANCH_ONLY_SCAN_WORKERS
def test_a_previous_scans_throttle_does_not_abort_the_next_one():
    """The reported defect: "rate-limited after 0 failures out of 250 tickers".

    PROVIDER_STATE is a process-wide singleton and no production code resets
    it, so a scan that ended throttled leaves `consecutive_rate_limits` parked
    at the trip threshold. The next scan read that as its own evidence, tripped
    the circuit on its first loop iteration and dispatched nothing -- which
    also meant no request could ever succeed to clear the counter. The user saw
    a rate-limit banner citing zero failures, because there had been zero.

    A cooldown the previous run opened is still honoured (that is the point of
    a Retry-After); what must not happen is abandoning the universe untouched.
    """
    stub = _StubScan()
    for _ in range(sp.TradingConfig.PROVIDER_RATE_LIMIT_TRIP_COUNT + 6):
        sp.PROVIDER_STATE.note_rate_limit()
    # A short residual cooldown, as a previous scan would leave behind.
    sp.PROVIDER_STATE.cooldown_until = time.time() + 0.3

    names = [f"EE{i:02d}" for i in range(10)]
    stub._run_scan_workers(names, max_workers=2, deadline=time.monotonic() + 60.0)

    assert not stub._provider_circuit_open, (
        "the previous scan's counters are not this scan's evidence"
    )
    assert len(stub.dispatched) == len(names), (
        f"only {len(stub.dispatched)}/{len(names)} dispatched; a healthy scan "
        f"must not be aborted by a stale counter it never contributed to"
    )


@BRANCH_ONLY_SCAN_WORKERS
def test_isolated_rate_limits_do_not_trip_the_circuit():
    """The user's real full-market run: 14 hits across 480 analysed names, with
    successes in between. That scan is worth finishing."""
    stub = _StubScan()
    for _ in range(3):
        sp.PROVIDER_STATE.note_rate_limit()
        sp.PROVIDER_STATE.note_success()   # a success clears the consecutive run
    sp.PROVIDER_STATE.cooldown_until = time.time() + 0.3

    stub._run_scan_workers(
        [f"DD{i:02d}" for i in range(6)], max_workers=2,
        deadline=time.monotonic() + 60.0,
    )
    assert len(stub.dispatched) == 6, (
        f"only {len(stub.dispatched)}/6 dispatched; isolated rate limits must "
        f"not abandon the universe"
    )


@BRANCH_ONLY_SCAN_WORKERS
def test_cooldown_longer_than_the_remaining_budget_stops_cleanly():
    """Waiting is bounded by the scan budget -- never an unbounded stall."""
    stub = _StubScan()
    sp.PROVIDER_STATE.cooldown_until = time.time() + 3_600.0

    stub._run_scan_workers(
        [f"BB{i:02d}" for i in range(6)],
        max_workers=2,
        deadline=time.monotonic() + 1.0,
    )

    assert stub._scan_stop_reason, "an aborted scan must say why"
    assert stub._provider_degraded


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
