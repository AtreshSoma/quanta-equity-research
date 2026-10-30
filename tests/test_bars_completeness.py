"""The shared completed-bar rule: which daily bar a score may read (engine.bars).

Every clock below is pinned with ``now``, so nothing depends on when the test runs.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from engine import bars
from engine.market_calendar import early_close_time
from engine.thresholds import RESEARCH

# New York is on EDT (UTC-4) until 1 Nov 2026, and EST (UTC-5) afterwards.
NY_1000_OCT27 = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)   # 10:00 EDT
NY_1605_OCT27 = datetime(2026, 10, 27, 20, 5, tzinfo=timezone.utc)   # 16:05 EDT, inside settle
NY_1600_OCT27 = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)   # 16:00 EDT, the close itself
NY_1630_OCT27 = datetime(2026, 10, 27, 20, 30, tzinfo=timezone.utc)  # 16:30 EDT, settled
# 27 Nov 2026 is the day after Thanksgiving: NYSE closes at 13:00 EST.
NY_1310_NOV27 = datetime(2026, 11, 27, 18, 10, tzinfo=timezone.utc)  # 13:10 EST, inside settle
NY_1330_NOV27 = datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)  # 13:30 EST, settled
NY_1605_NOV27 = datetime(2026, 11, 27, 21, 5, tzinfo=timezone.utc)   # 16:05 EST, after a half-day
NY_SAT_OCT31 = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)    # Saturday noon EDT
NY_LABOR_DAY = datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc)      # Labor Day, 10:00 EDT
IST_1000_OCT27 = datetime(2026, 10, 27, 4, 30, tzinfo=timezone.utc)  # 10:00 IST
IST_1540_OCT27 = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)  # 15:40 IST, inside settle
IST_1530_OCT27 = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)  # 15:30 IST, the close itself
IST_1555_OCT27 = datetime(2026, 10, 27, 10, 25, tzinfo=timezone.utc)  # 15:55 IST, settled
IST_1000_OCT20 = datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc)   # 10:00 IST, Dussehra holiday


def _frame(last: str, n: int = 40, tz=None, close_last: float | None = None) -> pd.DataFrame:
    """n business days ending on *last*; the newest close can be overridden."""
    rng = np.random.default_rng(11)
    index = pd.bdate_range(end=last, periods=n, tz=tz)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0002, 0.01, n))
    if close_last is not None:
        close[-1] = close_last
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": np.full(n, 1_000_000.0),
    }, index=index)


# --- live versus settled, US ----------------------------------------------------

def test_us_mid_session_bar_is_live():
    df = _frame("2026-10-27")
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1000_OCT27)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1000_OCT27)
    assert len(frame) == len(df) - 1
    assert basis["live_bar_excluded"] is True
    assert basis["scored_through"] == "2026-10-26"
    assert "still trading" in basis["reason"]


def test_us_inside_the_settle_window_is_still_excluded():
    df = _frame("2026-10-27")
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1605_OCT27)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1605_OCT27)
    assert len(frame) == len(df) - 1
    assert "settles at 16:20" in basis["reason"]


def test_us_the_close_itself_is_not_yet_settled():
    assert bars.last_bar_is_live(_frame("2026-10-27"), "AAPL", now=NY_1600_OCT27)


def test_us_settled_bar_is_kept():
    df = _frame("2026-10-27")
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1630_OCT27)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1630_OCT27)
    assert frame is df
    assert basis == {"scored_through": "2026-10-27", "live_bar_excluded": False,
                     "reason": "newest bar is a completed, settled session"}


def test_settle_window_is_configurable_and_zero_means_complete_at_the_close():
    df = _frame("2026-10-27")
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1605_OCT27, settle_minutes=20)
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1605_OCT27, settle_minutes=0)
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1600_OCT27, settle_minutes=0)


def test_default_settle_comes_from_thresholds():
    assert RESEARCH.BAR_SETTLE_MINUTES == 20


# --- early close, weekend, holiday ----------------------------------------------

def test_early_close_day_settles_from_one_pm_not_four_pm():
    assert early_close_time(date(2026, 11, 27)) is not None
    df = _frame("2026-11-27")
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1310_NOV27)     # 13:10: inside settle
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1330_NOV27)  # 13:30: settled
    # Long after the half-day close the bar is complete, not waiting for 16:20.
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1605_NOV27)


def test_ordinary_day_has_no_early_close():
    assert early_close_time(date(2026, 10, 27)) is None
    assert early_close_time(date(2026, 12, 24)) is not None  # Christmas Eve (a Thursday)


def test_weekend_nothing_to_drop():
    df = _frame("2026-10-30")  # Friday's bar is the newest
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_SAT_OCT31)
    assert frame is df
    assert basis["live_bar_excluded"] is False
    assert basis["scored_through"] == "2026-10-30"


def test_holiday_nothing_to_drop():
    df = _frame("2026-09-04")  # Friday before Labor Day
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_LABOR_DAY)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_LABOR_DAY)
    assert frame is df and basis["scored_through"] == "2026-09-04"


# --- India ------------------------------------------------------------------------

def test_india_mid_session_bar_is_live():
    df = _frame("2026-10-27", tz="Asia/Kolkata")
    assert bars.last_bar_is_live(df, "RELIANCE.NS", now=IST_1000_OCT27)
    assert bars.last_bar_is_live(df, "", now=IST_1000_OCT27)  # market read from the index zone
    frame, basis = bars.scoring_bars(df, "RELIANCE.NS", now=IST_1000_OCT27)
    assert len(frame) == len(df) - 1 and basis["scored_through"] == "2026-10-26"
    assert "IST" in basis["reason"]


def test_india_after_the_close_inside_settle_is_excluded():
    df = _frame("2026-10-27", tz="Asia/Kolkata")
    assert bars.last_bar_is_live(df, "RELIANCE.NS", now=IST_1540_OCT27)
    assert bars.last_bar_is_live(df, "RELIANCE.NS", now=IST_1530_OCT27)  # the 15:30 close itself


def test_india_after_close_plus_settle_is_kept():
    df = _frame("2026-10-27", tz="Asia/Kolkata")
    assert not bars.last_bar_is_live(df, "RELIANCE.NS", now=IST_1555_OCT27)
    frame, basis = bars.scoring_bars(df, "RELIANCE.NS", now=IST_1555_OCT27)
    assert frame is df and basis["scored_through"] == "2026-10-27"


def test_india_holiday_nothing_to_drop():
    df = _frame("2026-10-19", tz="Asia/Kolkata")  # the bar before Dussehra
    assert not bars.last_bar_is_live(df, "RELIANCE.NS", now=IST_1000_OCT20)


def test_market_is_read_from_the_suffix_or_the_index_zone():
    us = _frame("2026-10-27", tz="America/New_York")
    india = _frame("2026-10-27", tz="Asia/Kolkata")
    assert bars.market_of(us.index, "AAPL") == "US"
    assert bars.market_of(us.index, "RELIANCE.NS") == "IN"
    assert bars.market_of(india.index, "") == "IN"
    assert bars.market_of(us.index, "") == "US"


# --- index shapes and edge cases ----------------------------------------------------

def test_tz_naive_index_is_read_as_the_exchange_date():
    df = _frame("2026-10-27")  # no tz
    assert df.index.tz is None
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1000_OCT27)
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1630_OCT27)


def test_tz_aware_index_in_utc_is_dated_by_the_new_york_day():
    df = _frame("2026-10-27", tz="America/New_York")
    df.index = df.index.tz_convert("UTC")  # midnight New York is 04:00 UTC of the same date
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1000_OCT27)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1630_OCT27)
    assert basis["scored_through"] == "2026-10-27"


def test_empty_frame():
    df = _frame("2026-10-27").iloc[:0]
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1000_OCT27)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1000_OCT27)
    assert frame is df
    assert basis["scored_through"] is None and basis["live_bar_excluded"] is False


def test_none_frame():
    assert not bars.last_bar_is_live(None, "AAPL", now=NY_1000_OCT27)
    frame, basis = bars.scoring_bars(None, "AAPL", now=NY_1000_OCT27)
    assert frame is None and basis["scored_through"] is None


def test_one_row_live_frame_leaves_nothing_to_score():
    df = _frame("2026-10-27").iloc[-1:]
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1000_OCT27)
    assert len(frame) == 0
    assert basis["live_bar_excluded"] is True
    assert basis["scored_through"] is None
    assert "no completed bar" in basis["reason"]


def test_one_row_completed_frame_is_kept():
    df = _frame("2026-10-26").iloc[-1:]
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1000_OCT27)
    assert frame is df and basis["scored_through"] == "2026-10-26"


def test_yesterdays_last_bar_is_never_live():
    df = _frame("2026-10-26")
    assert not bars.last_bar_is_live(df, "AAPL", now=NY_1000_OCT27)
    frame, basis = bars.scoring_bars(df, "AAPL", now=NY_1000_OCT27)
    assert frame is df and basis["live_bar_excluded"] is False


def test_bar_dated_after_today_is_treated_as_live():
    df = _frame("2026-10-28")  # a bar from the future (clock or timezone mismatch)
    assert bars.last_bar_is_live(df, "AAPL", now=NY_1000_OCT27)


def test_bar_on_a_closed_day_is_dropped_as_before():
    # A calendar index, because bdate_range never yields a Saturday.
    df = _frame("2026-10-30")
    df.index = pd.date_range(end="2026-10-31", periods=len(df), freq="D")  # ends on Saturday
    assert bars.last_bar_is_live(df, "AAPL", now=NY_SAT_OCT31)


def test_naive_now_is_read_on_the_market_clock():
    df = _frame("2026-10-27")
    # 10:00 on the New York clock, handed over without a zone.
    assert bars.last_bar_is_live(df, "AAPL", now=datetime(2026, 10, 27, 10, 0))


def test_slicing_never_mutates_the_input():
    df = _frame("2026-10-27")
    before = df.copy()
    bars.scoring_bars(df, "AAPL", now=NY_1000_OCT27)
    pd.testing.assert_frame_equal(df, before)


# --- the ELITE names are thin wrappers over the shared rule -----------------------

def test_elite_wrappers_agree_with_the_shared_rule():
    import TradingAnalysis_ELITE as elite

    cases = [
        (_frame("2026-10-27"), "AAPL", NY_1000_OCT27),
        (_frame("2026-10-27"), "AAPL", NY_1605_OCT27),
        (_frame("2026-10-27"), "AAPL", NY_1630_OCT27),
        (_frame("2026-10-27", tz="Asia/Kolkata"), "RELIANCE.NS", IST_1540_OCT27),
        (_frame("2026-10-26"), "AAPL", NY_1000_OCT27),
    ]
    for df, ticker, now in cases:
        assert elite._last_bar_is_live(df, ticker, now) == bars.last_bar_is_live(df, ticker, now)
        assert elite._completed_bars(df, ticker, now).equals(bars.scoring_bars(df, ticker, now)[0])
