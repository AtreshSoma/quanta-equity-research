"""A07: chase alerts are dated, typed and built from config.

Each OR branch (gap on either of the last two bars, 5-day run, 10-day run) has
a boundary test on both sides of its configured threshold, with the growth
overrides. An alert names its trigger, its move, the threshold, the bar that
triggered and the latest bar in the data, and never says "today" or
"yesterday". Synthetic bars only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import thresholds as T  # noqa: E402

# Friday 2026-10-02: a scan run on the weekend still reads this session.
_IDX = pd.bdate_range(end="2026-10-02", periods=30)


def _hist(closes, opens=None):
    closes = np.asarray(closes, dtype=float)
    if opens is None:
        opens = np.r_[closes[0], closes[:-1]]              # no gaps
    return pd.DataFrame({"Open": opens, "High": np.maximum(opens, closes) * 1.001,
                         "Low": np.minimum(opens, closes) * 0.999, "Close": closes,
                         "Volume": np.full(len(closes), 1e6)}, index=_IDX[-len(closes):])


def _flat(n=30, price=100.0):
    return np.full(n, price)


def _system(scan_type="standard"):
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.current_scan_type = scan_type
    return eng


def _gap_hist(gap, on_last=True):
    closes = _flat()
    i = -1 if on_last else -2
    level = closes[i - 1] * (1 + gap)
    closes[i:] = level              # [I6] the gap holds: a gap closed back to the prior close has filled
    opens = np.r_[closes[0], closes[:-1]].copy()
    opens[i] = level
    return _hist(closes, opens)


def _run_hist(days, move):
    closes = _flat()
    closes[-days:] = np.linspace(100.0, 100.0 * (1 + move), days + 1)[1:]
    return _hist(closes)


@pytest.mark.parametrize("on_last,bar", [(True, "2026-10-02"), (False, "2026-10-01")])
def test_a_gap_above_the_threshold_is_a_dated_gap_trigger(on_last, bar):
    g = T.CHASE.GAP_PCT
    trig = _system()._chase_trigger(_gap_hist(g + 0.001, on_last), "SYNT")
    assert trig["trigger"] == "gap" and trig["active"] is True
    assert trig["bar_date"] == bar and trig["session_date"] == "2026-10-02"
    assert trig["threshold_pct"] == pytest.approx(g * 100)
    assert trig["move_pct"] == pytest.approx((g + 0.001) * 100, abs=1e-6)
    assert _system()._chase_trigger(_gap_hist(g - 0.001, on_last), "SYNT") is None


@pytest.mark.parametrize("days,attr,key", [(5, "MOVE_5D_PCT", "run_5d"), (10, "MOVE_10D_PCT", "run_10d")])
def test_a_run_above_the_threshold_is_a_run_trigger(days, attr, key):
    m = getattr(T.CHASE, attr)
    trig = _system()._chase_trigger(_run_hist(days, m + 0.002), "SYNT")
    assert trig["trigger"] == key
    assert trig["bar_date"] == "2026-10-02"
    assert trig["from_date"] == _IDX[-(days + 1)].date().isoformat()
    assert _system()._chase_trigger(_run_hist(days, m - 0.002), "SYNT") is None


def test_a_stock_gets_the_same_thresholds_in_every_scan_type():
    """P6: the threshold is a per-stock rule, never the universe's: the same bars
    give the same trigger in a Growth and a Quick scan (default: standard)."""
    between = (T.CHASE.GAP_PCT + T.CHASE.GROWTH_GAP_PCT) / 2
    for scan in ("standard", "quick", "focused_growth", "growth"):
        trig = _system(scan)._chase_trigger(_gap_hist(between), "SYNT")
        assert trig is not None and trig["threshold_pct"] == pytest.approx(T.CHASE.GAP_PCT * 100), scan
        assert trig["wide_thresholds"] is False


def test_the_optional_volatility_rule_is_per_stock(monkeypatch):
    """With CHASE.WIDE_THRESHOLD_MIN_VOL set, a stock whose own realized volatility
    reaches it gets the wide thresholds, in every scan type."""
    rng = np.random.default_rng(3)
    calm = _flat() * np.exp(np.cumsum(rng.normal(0, 0.004, 30)))
    wild = _flat() * np.exp(np.cumsum(rng.normal(0, 0.05, 30)))
    def gapped(closes, gap):
        closes = closes.copy(); closes[-1] = closes[-2] * (1 + gap)
        opens = np.r_[closes[0], closes[:-1]].copy(); opens[-1] = closes[-1]
        return _hist(closes, opens)
    between = (T.CHASE.GAP_PCT + T.CHASE.GROWTH_GAP_PCT) / 2
    monkeypatch.setattr(T.CHASE, "WIDE_THRESHOLD_MIN_VOL", 0.35)
    for scan in ("quick", "focused_growth"):
        assert _system(scan)._chase_trigger(gapped(calm, between), "CALM")["threshold_pct"] == pytest.approx(T.CHASE.GAP_PCT * 100)
        assert _system(scan)._chase_trigger(gapped(wild, between), "WILD") is None, "wide gap threshold applies"



def test_thresholds_come_from_config(monkeypatch):
    monkeypatch.setattr(T.CHASE, "GAP_PCT", 0.10)
    assert _system()._chase_trigger(_gap_hist(0.07), "SYNT") is None


def test_short_history_raises_no_alert():
    assert _system()._chase_trigger(_gap_hist(0.2).tail(T.CHASE.MIN_BARS - 1), "SYNT") is None


def test_an_oversold_rebound_gap_is_labelled_a_gap_not_extended():
    """FICO-like: down about 40% over the month, RSI low, a 7% opening gap."""
    closes = np.r_[np.full(9, 170.0), np.linspace(170.0, 100.0, 21)]
    closes[-2:] = closes[-3] * 1.072        # [I6] the gap holds through the latest close
    opens = np.r_[closes[0], closes[:-1]].copy()
    opens[-2] = closes[-3] * 1.072
    ok, reason = _system()._is_chasing_momentum(_hist(closes, opens), "SYNT")
    assert ok
    assert "gap" in reason.lower() and "2026-10-01" in reason
    assert "extended" not in reason.lower()
    assert not {"today", "yesterday"} & set(reason.lower().replace(".", " ").split())


# ── the alert payload from a real analysis ─────────────────────────────────

def test_the_alert_carries_company_distance_trigger_and_dates(monkeypatch):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    monkeypatch.setitem(acc._NAMES, "SYNJ", ({}, dict(acc._GOOD), True, False))
    orig = acc._frame
    j = list(acc._NAMES).index("SYNJ")

    def gapped(i, **kw):
        f = orig(i, **kw)
        if i == j:
            f = f.copy()
            o = float(f["Close"].iloc[-2]) * 1.072
            f.iloc[-1, f.columns.get_loc("Open")] = o
            f.iloc[-1, f.columns.get_loc("High")] = max(o, float(f["High"].iloc[-1]))
            f.iloc[-1, f.columns.get_loc("Low")] = min(o, float(f["Low"].iloc[-1]))
        return f

    monkeypatch.setattr(acc, "_frame", gapped)
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {})
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type="custom", custom_tickers=["SYNJ"])
    alerts = {a["ticker"]: a for a in eng.chase_alerts}
    assert "SYNJ" in alerts, "a 7.2% gap must raise a chase alert"
    a = alerts["SYNJ"]
    assert a["company"] == "Synthetic SYNJ"
    assert a["trigger"] == "gap" and a["move_pct"] == pytest.approx(7.2, abs=0.05)
    assert a["bar_date"] == "2026-10-02" and a["session_date"] == "2026-10-02"
    assert a["threshold_pct"] == pytest.approx(T.CHASE.GAP_PCT * 100)
    f = gapped(j)
    sma20 = float(f["Close"].tail(20).mean())
    assert a["distance_from_20dma"] == pytest.approx(float(f["Close"].iloc[-1]) / sma20 - 1, abs=1e-4)
