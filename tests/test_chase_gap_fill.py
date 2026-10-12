"""I6: a gap that has filled is not an active chase alert.

IT gapped 5.17% on 2026-10-01 (open 196.19 over a 186.54 close) and closed
2026-10-02 at 184.80, below the pre-gap close, yet it stayed on the active chase
list with its score penalty. A gap whose latest close is at or below the pre-gap
close (plus CHASE.GAP_FILL_TOLERANCE) is now "gap filled": informational, no
penalty. A partly filled gap reports how much has filled and the fade since the
gap-day close; a run reports how far it sits below its peak. The existing alert
thresholds are unchanged. Synthetic bars, shaped like the cached sessions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import thresholds as T  # noqa: E402

_IDX = pd.bdate_range(end="2026-10-02", periods=30)


def _hist(closes, opens):
    closes, opens = np.asarray(closes, float), np.asarray(opens, float)
    return pd.DataFrame({"Open": opens, "High": np.maximum(opens, closes) * 1.001,
                         "Low": np.minimum(opens, closes) * 0.999, "Close": closes,
                         "Volume": np.full(len(closes), 1e6)}, index=_IDX[-len(closes):])


def _system(scan_type="focused_growth"):
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.current_scan_type = scan_type
    return eng


def _gap(pre, gap_open, gap_close, last):
    """Flat at ``pre``, a gap bar (open ``gap_open``, close ``gap_close``) on
    2026-10-01, and the latest close ``last`` on 2026-10-02."""
    closes = np.r_[np.full(28, pre), gap_close, last]
    opens = np.r_[np.full(28, pre), gap_open, gap_close]
    return _hist(closes, opens)


def test_a_held_gap_stays_an_active_alert():
    trig = _system()._chase_trigger(_gap(100.0, 106.0, 106.5, 107.0), "HELD")
    assert trig["trigger"] == "gap" and trig["active"] is True
    assert trig["gap_fill_pct"] == 0.0
    assert trig["bar_date"] == "2026-10-01"


def test_a_filled_gap_is_informational_and_carries_no_penalty():
    hist = _gap(186.54, 196.19, 192.80, 184.80)                 # the IT sessions
    eng = _system()
    trig = eng._chase_trigger(hist, "FILL")
    assert trig["trigger"] == "gap" and trig["active"] is False and trig["state"] == "gap_filled"
    assert trig["move_pct"] == pytest.approx(5.17, abs=0.01)
    assert trig["close_vs_pre_gap_pct"] == pytest.approx(-0.93, abs=0.01)
    assert "filled" in trig["reason"].lower()
    assert eng._is_chasing_momentum(hist, "FILL") == (False, "")


def test_a_partly_filled_gap_reports_its_fill_and_fade():
    trig = _system()._chase_trigger(_gap(183.37, 215.98, 212.29, 198.90), "PART")   # the ACN sessions
    assert trig["active"] is True and trig["state"] == "gap_partly_filled"
    assert trig["move_pct"] == pytest.approx(17.78, abs=0.01)
    assert trig["gap_fill_pct"] == pytest.approx(52.4, abs=0.1)
    assert trig["fade_since_gap_close_pct"] == pytest.approx(-6.31, abs=0.01)
    assert "partly filled" in trig["reason"]


def test_the_fill_tolerance_comes_from_config(monkeypatch):
    hist = _gap(100.0, 106.0, 103.0, 100.5)                     # 0.5% above the pre-gap close
    assert _system()._chase_trigger(hist, "TOL")["active"] is True
    monkeypatch.setattr(T.CHASE, "GAP_FILL_TOLERANCE", 0.01)
    assert _system()._chase_trigger(hist, "TOL")["active"] is False


def test_missing_bars_never_raise_or_invent_a_fill():
    hist = _gap(100.0, 106.0, 106.5, 107.0)
    hist.iloc[-1, hist.columns.get_loc("Close")] = np.nan
    trig = _system()._chase_trigger(hist, "NAN")
    assert trig is None or trig.get("state") != "gap_filled"
    assert _system()._chase_trigger(hist.iloc[-5:], "SHORT") is None      # under CHASE.MIN_BARS


def test_a_filled_gap_does_not_hide_an_active_run():
    closes = np.r_[np.full(24, 100.0), np.linspace(101, 125, 4), 124.0, 121.0]
    opens = np.r_[closes[0], closes[:-1]].copy()
    opens[-2] = closes[-3] * 1.06                               # gap on 10-01 ...
    closes[-1] = closes[-3] * 0.999                             # ... filled by 10-02
    trig = _system("standard")._chase_trigger(_hist(closes, opens), "RUN")
    assert trig["trigger"] in ("run_5d", "run_10d") and trig["active"] is True


def test_a_run_reports_its_distance_from_the_peak():
    closes = np.r_[np.full(24, 100.0), 104, 110, 118, 126, 124, 121.0]
    trig = _system("standard")._chase_trigger(_hist(closes, np.r_[closes[0], closes[:-1]]), "PEAK")
    assert trig["trigger"] == "run_5d" and trig["active"] is True
    assert trig["off_peak_pct"] == pytest.approx((121 / 126 - 1) * 100, abs=0.01)
    assert trig["peak_date"] == _IDX[-3].date().isoformat()


def test_the_scan_publishes_filled_gaps_apart_from_active_alerts(monkeypatch):
    from test_scan_accounting import _run_worker, _watch, _fake_engine_module
    import sys
    mod = _fake_engine_module([_watch("W1", 1)])
    base = mod.QuantaEliteTradingSystem

    class Eng(base):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.chase_alerts = [{"ticker": "ACTV", "trigger": "gap", "active": True}]
            self.gap_filled_alerts = [{"ticker": "FILL", "trigger": "gap", "active": False, "state": "gap_filled"}]
    mod.QuantaEliteTradingSystem = Eng
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    import test_scan_accounting as acc
    monkeypatch.setattr(acc, "_fake_engine_module", lambda recs, universe=8: mod)
    res = _run_worker(monkeypatch, [_watch("W1", 1)])
    assert [c["ticker"] for c in res["chase_alerts"]] == ["ACTV"]
    assert [c["ticker"] for c in res["chase_filled"]] == ["FILL"]
