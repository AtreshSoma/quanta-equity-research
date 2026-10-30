"""Ledger uncertainty fields and the future-as-of guard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tracking.ledger import GroupStats, SignalLedger, _group_stats


def _row(i, win):
    return {"outcome": "WIN" if win else "LOSS", "entry_basis": "next_session_open",
            "return_pct": 1.0, "excess_return_pct": 1.0 if win else -1.0,
            "stance": "BULLISH", "as_of_date": f"2026-01-{i + 1:02d}", "horizon_days": 1}


def test_group_stats_reports_wilson_interval_and_p_value():
    g = _group_stats({"engine": "e"}, [_row(i, i % 5 != 0) for i in range(20)])
    assert g.hit_rate == pytest.approx(0.8)
    lo, hi = g.hit_rate_ci95
    assert lo < 0.8 < hi and 0 <= lo and hi <= 1
    assert g.to_dict()["hit_rate_ci95"] == [lo, hi]


def test_summarize_adds_bh_adjusted_p(tmp_path):
    led = SignalLedger(tmp_path / "l.sqlite3")
    assert led.summarize() == []
    g = [GroupStats({}, 1, 0, .5, 0, 0, 0, None, 3.0, 5, None, None, True, "", p_value=0.01)]
    assert g[0].p_value_bh is None


def test_future_as_of_is_rejected(tmp_path):
    led = SignalLedger(tmp_path / "l.sqlite3")
    future = (datetime.now(timezone.utc) + timedelta(days=3)).date()
    with pytest.raises(ValueError, match="in the future"):
        led.record(engine="t", ticker="ABC", as_of=future, horizon_days=5,
                   stance="BULLISH", action="BUY")
    assert led.rows() == []
