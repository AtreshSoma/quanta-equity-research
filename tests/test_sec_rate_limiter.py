"""The SEC rate limiter must survive a clock that moves backwards.

`SECEdgarParser._rate_limit` slept for `RATE_LIMIT_DELAY - elapsed`, where
`elapsed = time.time() - _last_request_time`. Both are wall-clock reads of a
process-wide class attribute, so if the clock ever moves BACKWARDS between two
calls — an NTP step correction, a manual clock change, or a test that freezes
time to an earlier date — `elapsed` goes negative and the sleep becomes
`0.11 + |elapsed|`. A backwards jump of eleven days stalls the caller for
eleven days while holding `_cik_map_lock`, so every other thread that needs a
CIK blocks behind it.

That is exactly what hung the golden replay suite: fixtures are frozen to
different recording dates, AAPL to 2026-09-07 and JPM to 2026-08-27.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

MODULES = Path(__file__).resolve().parents[1] / "modules"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))

from sec_edgar_parser import SECEdgarParser  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_limiter():
    SECEdgarParser.reset_transient_state()
    yield
    SECEdgarParser.reset_transient_state()


def _elapsed_of(monkeypatch, last_request_time: float, now: float) -> float:
    """Run _rate_limit with a controlled clock; return how long it slept."""
    slept: list[float] = []
    monkeypatch.setattr(SECEdgarParser, "_last_request_time", last_request_time)
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    SECEdgarParser._rate_limit()
    return sum(slept)


def test_a_backwards_clock_never_sleeps_longer_than_the_delay(monkeypatch):
    """The golden-suite hang: an 11-day backwards jump slept for 11 days."""
    eleven_days = 11 * 24 * 3600
    slept = _elapsed_of(monkeypatch, last_request_time=eleven_days, now=0.0)

    assert slept <= SECEdgarParser.RATE_LIMIT_DELAY


def test_a_normal_gap_does_not_sleep(monkeypatch):
    slept = _elapsed_of(monkeypatch, last_request_time=0.0, now=10.0)
    assert slept == 0


def test_a_rapid_call_still_paces(monkeypatch):
    """The limiter must keep doing its job for an ordinary forward clock."""
    slept = _elapsed_of(monkeypatch, last_request_time=100.0, now=100.01)
    assert slept == pytest.approx(SECEdgarParser.RATE_LIMIT_DELAY - 0.01, abs=1e-6)


def test_the_clock_is_recorded_even_on_a_backwards_jump(monkeypatch):
    monkeypatch.setattr(SECEdgarParser, "_last_request_time", 1_000_000.0)
    monkeypatch.setattr(time, "time", lambda: 5.0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    SECEdgarParser._rate_limit()

    assert SECEdgarParser._last_request_time == 5.0


# ---------------------------------------------------------------------------
# Cross-test isolation of the process-wide clock state
# ---------------------------------------------------------------------------

def test_reset_clears_the_rate_limiter_clock():
    SECEdgarParser._last_request_time = 12345.0
    SECEdgarParser.reset_transient_state()
    assert SECEdgarParser._last_request_time == 0.0


def test_reset_closes_an_open_circuit_breaker():
    SECEdgarParser._circuit_open_until = time.time() + 10_000
    SECEdgarParser._consecutive_failures = 5
    SECEdgarParser.reset_transient_state()

    assert SECEdgarParser._circuit_is_open() is False
    assert SECEdgarParser._consecutive_failures == 0


def test_reset_clears_the_cik_map_so_a_replay_starts_cold():
    SECEdgarParser._cik_cache["ZZZZ"] = "0000000001"
    SECEdgarParser._cik_map_loaded = True
    SECEdgarParser.reset_transient_state()

    assert SECEdgarParser._cik_cache == {}
    assert SECEdgarParser._cik_map_loaded is False


# ---------------------------------------------------------------------------
# The golden replay harness must neutralise the same state
# ---------------------------------------------------------------------------

def test_replay_isolation_neutralises_the_rate_limiter_clock():
    """Fixtures freeze to different dates, so the stamp cannot carry over."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "golden"))
    import importlib

    recorder = importlib.import_module("_recorder")
    elite = importlib.import_module("TradingAnalysis_ELITE")

    SECEdgarParser._last_request_time = 9_999_999_999.0
    patches = recorder._isolated_account_patches(elite)
    for p in patches:
        p.start()
    try:
        assert SECEdgarParser._last_request_time == 0.0
    finally:
        for p in reversed(patches):
            p.stop()
