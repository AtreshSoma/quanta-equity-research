"""The ribbon quote must be today's price after the close.

Yahoo returns the current session's daily bar with a blank close (ETFs
especially). Dropping the blank row made yesterday's close read as today's
price and yesterday's change as today's (2026-10-09: SPY 773.93 -0.42% "Previous
close Oct 8" beside a chart at 778.57 +0.60%)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()
import app as webapp_app  # noqa: E402


def _daily(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York") for d, _ in rows])
    return pd.DataFrame({"Close": [c for _, c in rows]}, index=idx)


class _FakeTicker:
    def __init__(self, daily, intraday=None, meta=None):
        self._daily, self._intraday, self.history_metadata = daily, intraday, meta or {}

    def history(self, period=None, interval=None, timeout=None):
        return self._intraday if interval == "5m" else self._daily


def _fetch(fake, monkeypatch):
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: fake)
    return webapp_app._fetch_tape_quote("SPY", "S&P 500")


def test_blank_session_bar_uses_todays_intraday_price(monkeypatch):
    daily = _daily([("2026-10-07", 777.2), ("2026-10-08", 773.93), ("2026-10-09", float("nan"))])
    intra = pd.DataFrame({"Close": [776.0, 778.53]},
                         index=pd.DatetimeIndex([pd.Timestamp("2026-10-09 15:50", tz="America/New_York"),
                                                 pd.Timestamp("2026-10-09 15:55", tz="America/New_York")]))
    q = _fetch(_FakeTicker(daily, intra), monkeypatch)
    assert q["quote_date"] == "2026-10-09"
    assert q["price"] == 778.53
    assert q["change_pct"] == round((778.53 - 773.93) / 773.93 * 100, 2)


def test_metadata_newer_than_the_last_bar_wins(monkeypatch):
    daily = _daily([("2026-10-07", 777.2), ("2026-10-08", 773.93)])
    meta = {"regularMarketPrice": 778.57, "regularMarketTime": pd.Timestamp("2026-10-09 16:00", tz="America/New_York")}
    q = _fetch(_FakeTicker(daily, None, meta), monkeypatch)
    assert (q["price"], q["quote_date"]) == (778.57, "2026-10-09")


def test_a_complete_daily_bar_is_left_alone(monkeypatch):
    daily = _daily([("2026-10-08", 773.93), ("2026-10-09", 778.57)])
    meta = {"regularMarketPrice": 778.57, "regularMarketTime": pd.Timestamp("2026-10-09 16:00", tz="America/New_York")}
    q = _fetch(_FakeTicker(daily, None, meta), monkeypatch)
    assert (q["price"], q["quote_date"], q["price_basis"]) == (778.57, "2026-10-09", "daily_bar")
