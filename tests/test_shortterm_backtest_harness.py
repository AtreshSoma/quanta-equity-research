"""Replay mechanics of the short-term backtest harness, on synthetic bars."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import sp_shortterm_backtest as bt  # noqa: E402


def _bars(opens, highs, lows, closes, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                         "Volume": 1e6}, index=idx)


def _engine(plan, friction=0.0, max_hold=3):
    """A harness whose scorer returns ``plan`` (no analyzer, no network)."""
    eng = bt.ShortTermBacktest.__new__(bt.ShortTermBacktest)
    eng.max_hold_days, eng.warmup_bars, eng.friction_pct = max_hold, 0, friction
    eng._score_slice = lambda hist_slice, gated=True: dict(plan)
    return eng


PLAN = {"signal": "BUY", "confidence": 1.0, "overall_score": 70.0, "current_price": 100.0,
        "planned_entry": 100.0, "stop_loss": 95.0, "primary_target": 110.0}


def test_a_gap_through_the_stop_fills_at_the_open():
    hist = _bars([100, 100, 90, 90], [101, 101, 91, 91], [99, 99, 88, 88], [100, 100, 90, 90])
    trades = _engine(PLAN).replay_ticker("X", hist, forced_entries=[0])
    assert trades[0].exit_reason == "stop" and trades[0].exit_price == pytest.approx(90.0)


def test_a_time_exit_is_dated_and_benchmarked_at_the_next_open():
    flat = [100.0] * 8
    hist = _bars(flat, [101] * 8, [99] * 8, flat)
    spy = _bars([400, 400, 400, 400, 400, 404, 404, 404], [410] * 8, [390] * 8,
                [400, 400, 400, 400, 402, 408, 408, 408])
    t = _engine(PLAN, max_hold=3).replay_ticker("X", hist, forced_entries=[0], benchmark=spy)[0]
    assert t.exit_reason == "time" and t.bars_held == 3
    assert t.exit_date == str(hist.index[5].date())  # sold at bar 5's open
    assert t.benchmark_return_pct == pytest.approx(1.0)  # 400 open -> 404 open, not 408 close


def test_levels_keep_their_distance_from_the_planned_entry():
    plan = dict(PLAN, planned_entry=98.0, stop_loss=93.1, primary_target=107.8)  # -5% / +10%
    hist = _bars([100] + [102] * 7, [103] * 8, [101] * 8, [102] * 8)
    t = _engine(plan).replay_ticker("X", hist, forced_entries=[0])[0]
    assert t.stop_loss == pytest.approx(102 * 0.95) and t.primary_target == pytest.approx(102 * 1.10)


def test_random_entries_are_seeded_distinct_and_inside_the_window():
    eng = _engine(PLAN)
    eng.warmup_bars = 5
    hist = _bars([100.0] * 60, [101] * 60, [99] * 60, [100.0] * 60)
    a = eng.random_entries("X", hist, str(hist.index[20].date()), 10, seed=1)
    assert a == eng.random_entries("X", hist, str(hist.index[20].date()), 10, seed=1)
    assert len(set(a)) == 10 and min(a) >= 20 and max(a) <= 58
    assert a != eng.random_entries("Y", hist, str(hist.index[20].date()), 10, seed=1)


def _trades(kind, excess, dates):
    return pd.DataFrame({"kind": kind, "excess_return_pct": excess, "entry_date": dates})


def test_the_signal_minus_random_interval_finds_a_real_gap_and_not_a_fake_one():
    rng = np.random.default_rng(0)
    dates = [str(d.date()) for d in pd.bdate_range("2020-01-01", periods=300)]
    market = np.repeat(rng.normal(0, 3, 60), 5)  # a shared weekly shock
    sig = _trades("signal", market + rng.normal(1.0, 1, 300), dates)
    rnd = _trades("random", market + rng.normal(0.0, 1, 300), dates)
    lo, hi = bt._diff_ci(sig, rnd)
    assert lo > 0.5 and hi < 1.5
    same = _trades("random", market + rng.normal(1.0, 1, 300), dates)
    lo, hi = bt._diff_ci(sig, same)
    assert lo < 0 < hi


def test_weekly_cohorts_collapse_same_week_trades():
    df = _trades("signal", [1.0, 3.0, 5.0], ["2024-01-01", "2024-01-03", "2024-01-09"])
    assert list(bt._weekly(df)) == [2.0, 5.0]


def test_a_gap_through_the_target_fills_at_the_better_open():
    hist = _bars([100, 100, 115, 115], [101, 101, 116, 116], [99, 99, 114, 114], [100, 100, 115, 115])
    t = _engine(PLAN).replay_ticker("X", hist, forced_entries=[0])[0]
    assert t.exit_reason == "target" and t.exit_price == pytest.approx(115.0)


def test_a_trade_open_when_the_data_ends_is_kept_not_dropped():
    hist = _bars([100, 100, 101, 102], [101, 101, 102, 103], [99, 99, 100, 101], [100, 100, 101, 102])
    trades = _engine(PLAN, max_hold=10).replay_ticker("X", hist, forced_entries=[0])
    assert [t.exit_reason for t in trades] == ["end"]
    assert trades[0].exit_price == pytest.approx(102.0) and trades[0].bars_held == 2
