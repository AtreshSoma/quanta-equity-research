"""The live chart's payload: OHLC bars, indicator warm-up and corporate actions.

No test here reaches the network.
"""
from __future__ import annotations

import sys
import types

import pandas as pd

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402


def _fake(monkeypatch, frame, meta=None, seen=None):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.history_metadata = meta or {}

        def history(self, **kw):
            if seen is not None:
                seen.append(kw)
            return frame

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    monkeypatch.setattr(webapp, "_orig_history", None, raising=False)


def _daily(n, start="2024-10-01"):
    idx = pd.bdate_range(start, periods=n, tz="America/New_York")
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    frame = pd.DataFrame({"Open": close - 0.5, "High": close + 1, "Low": close - 1, "Close": close,
                          "Volume": 1000, "Dividends": 0.0, "Stock Splits": 0.0}, index=idx)
    return frame


def test_daily_ranges_download_warm_up_and_send_it_apart(monkeypatch):
    frame = _daily(520)
    seen = []
    _fake(monkeypatch, frame, seen=seen)
    out = webapp._chart_payload("AAA", "1y")
    assert seen[0]["period"] == "2y", "a 1-year chart downloads two years"
    # The plotted window is the last year; the bars before it are warm-up only.
    first = pd.Timestamp(out["points"][0][0], unit="ms", tz="UTC")
    last = pd.Timestamp(out["points"][-1][0], unit="ms", tz="UTC")
    assert 360 <= (last - first).days <= 366
    assert len(out["lead"]) == webapp.CHART_LEAD_BARS
    assert out["lead"][-1][0] < out["points"][0][0]


def test_each_bar_carries_open_high_low_after_the_old_fields(monkeypatch):
    _fake(monkeypatch, _daily(30))
    bar = webapp._chart_payload("AAA", "max")["points"][0]
    assert bar[1:] == [100.0, 1000, 99.5, 101.0, 99.0]


def test_a_bar_whose_range_misses_its_close_is_drawn_flat(monkeypatch):
    frame = _daily(30)
    frame.iloc[5, frame.columns.get_loc("High")] = 50.0   # below the close: a provider glitch
    _fake(monkeypatch, frame)
    bar = webapp._chart_payload("AAA", "max")["points"][5]
    assert bar[3] == bar[4] == bar[5] == bar[1]


def test_dividends_and_splits_inside_the_window_become_events(monkeypatch):
    frame = _daily(520)
    frame.iloc[-10, frame.columns.get_loc("Dividends")] = 0.26
    frame.iloc[-5, frame.columns.get_loc("Stock Splits")] = 4.0
    frame.iloc[5, frame.columns.get_loc("Dividends")] = 0.25   # in the warm-up: not shown
    _fake(monkeypatch, frame)
    events = webapp._chart_payload("AAA", "1y")["events"]
    assert [(e["kind"], e["value"]) for e in events] == [("dividend", 0.26), ("split", 4.0)]


def test_five_day_window_keeps_exactly_five_sessions(monkeypatch):
    idx = pd.date_range("2026-10-01 09:30", periods=20 * 78, freq="5min", tz="America/New_York")
    idx = idx[(idx.hour >= 9) & (idx.hour < 16)]
    frame = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)
    _fake(monkeypatch, frame)
    out = webapp._chart_payload("AAA", "5d")
    days = {pd.Timestamp(p[0], unit="ms", tz="UTC").tz_convert("America/New_York").date() for p in out["points"]}
    assert len(days) == 5
