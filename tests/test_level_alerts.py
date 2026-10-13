"""Stop / target alerts: first touch after entry, announced once, ledger untouched."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tracking.alerts import _osa, check_levels
from tracking.ledger import SignalLedger


def _frame(highs, lows, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(highs))
    h = pd.Series(highs, index=idx, dtype=float)
    lo = pd.Series(lows, index=idx, dtype=float)
    mid = (h + lo) / 2
    return pd.DataFrame({"Open": mid, "High": h, "Low": lo, "Close": mid})


@pytest.fixture
def ledger(tmp_path):
    led = SignalLedger(tmp_path / "l.sqlite3")
    original = led.record

    def record(**kwargs):
        row_id = original(**kwargs)
        with led._connect() as con:  # published on its as-of evening
            con.execute("UPDATE signals SET recorded_at=? WHERE id=?",
                        (f"{str(kwargs['as_of'])[:10]}T22:00:00+00:00", row_id))
        return row_id

    led.record = record
    return led


def _loader(frame):
    return lambda ticker, start, end: frame


def test_bullish_target_touch_is_reported_once(ledger):
    bars = _frame([100, 101, 104, 111, 112], [98, 99, 100, 103, 105])
    ledger.record(engine="t", ticker="ABC", as_of=date(2026, 1, 5), horizon_days=20,
                  stance="BULLISH", action="BUY", target_price=110, stop_price=95)
    first = check_levels(ledger, _loader(bars), today=date(2026, 1, 9))
    assert [(a.kind, a.session) for a in first] == [("TARGET", "2026-01-08")]
    assert check_levels(ledger, _loader(bars), today=date(2026, 1, 9)) == []


def test_publication_day_bar_is_ignored(ledger):
    # The as-of session itself traded through the stop, but no position existed yet.
    bars = _frame([100, 101, 102], [90, 99, 100])
    ledger.record(engine="t", ticker="ABC", as_of=date(2026, 1, 5), horizon_days=20,
                  stance="BULLISH", action="BUY", target_price=120, stop_price=95)
    assert check_levels(ledger, _loader(bars), today=date(2026, 1, 7)) == []


def test_bearish_levels_are_mirrored(ledger):
    bars = _frame([100, 101, 108, 100], [98, 95, 99, 97])
    ledger.record(engine="t", ticker="XYZ", as_of=date(2026, 1, 5), horizon_days=20,
                  stance="BEARISH", action="SELL", target_price=90, stop_price=107)
    alerts = check_levels(ledger, _loader(bars), today=date(2026, 1, 8))
    assert [(a.kind, a.session) for a in alerts] == [("STOP", "2026-01-07")]


def test_dry_run_does_not_remember_and_ledger_is_unchanged(ledger):
    bars = _frame([100, 112], [98, 105])
    ledger.record(engine="t", ticker="ABC", as_of=date(2026, 1, 5), horizon_days=20,
                  stance="BULLISH", action="BUY", target_price=110)
    before = ledger.rows()
    assert len(check_levels(ledger, _loader(bars), today=date(2026, 1, 6), announce=False)) == 1
    assert len(check_levels(ledger, _loader(bars), today=date(2026, 1, 6), announce=False)) == 1
    assert ledger.rows() == before


def test_neutral_calls_and_failed_loads_are_skipped(ledger):
    ledger.record(engine="t", ticker="N", as_of=date(2026, 1, 5), horizon_days=20,
                  stance="NEUTRAL", action="HOLD", target_price=1, stop_price=1)
    ledger.record(engine="t", ticker="B", as_of=date(2026, 1, 5), horizon_days=20,
                  stance="BULLISH", action="BUY", target_price=1)

    def boom(*_a):
        raise RuntimeError("provider down")

    assert check_levels(ledger, boom, today=date(2026, 1, 9)) == []


def test_applescript_literal_escapes_quotes():
    assert _osa('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'


def test_touches_after_the_horizon_exit_are_ignored(ledger):
    # 3-session call entered on Jan 6 exits Jan 8; the target trades on Jan 12.
    bars = _frame([100, 101, 102, 103, 104, 115], [98, 99, 100, 101, 102, 110])
    ledger.record(engine="t", ticker="ABC", as_of=date(2026, 1, 5), horizon_days=3,
                  stance="BULLISH", action="BUY", target_price=110)
    assert check_levels(ledger, _loader(bars), today=date(2026, 1, 12)) == []
