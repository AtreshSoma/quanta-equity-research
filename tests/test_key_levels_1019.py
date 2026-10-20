"""Overview key levels while the session is live (2026-10-19 review).

The expected range for today is set from the prior close (centred on the live
price it would contain that price by construction), and the ATR reads completed
sessions only. No test here reaches the network.
"""
from __future__ import annotations

import sys
import types

import pandas as pd

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402

TZ = "America/New_York"


def _frames(day="2026-10-16"):
    days = pd.bdate_range(end=day, periods=30, tz=TZ)
    daily = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}, index=days)
    # Today's unfinished bar: a huge range that must not reach the ATR.
    daily.loc[days[-1], ["High", "Low", "Close"]] = [130.0, 90.0, 110.0]
    intra = pd.DataFrame({"Open": [100.0, 110.0], "High": [130.0, 130.0], "Low": [90.0, 90.0], "Close": [100.0, 110.0]},
                         index=pd.DatetimeIndex([f"{day} 09:30", f"{day} 11:00"], tz=TZ))
    return daily, intra


def _install(monkeypatch, daily, intra):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kw):
            return intra if kw.get("interval") == "5m" else daily
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    monkeypatch.setattr(webapp, "_trend_metrics", lambda *_a, **_k: {})
    monkeypatch.setattr(webapp, "_quote_snapshot", lambda *_a, **_k: {"price": 16.0})


def test_session_open_check_uses_the_exchange_close():
    bar = pd.Timestamp("2026-10-16 11:00", tz=TZ)
    assert webapp._session_still_open(bar, now=pd.Timestamp("2026-10-16 11:05", tz=TZ))
    assert not webapp._session_still_open(bar, now=pd.Timestamp("2026-10-16 16:01", tz=TZ))
    assert not webapp._session_still_open(bar, now=pd.Timestamp("2026-10-17 10:00", tz=TZ))
    nse = pd.Timestamp("2026-10-16 14:00", tz="Asia/Kolkata")
    assert not webapp._session_still_open(nse, now=pd.Timestamp("2026-10-16 15:31", tz="Asia/Kolkata"))


def test_live_session_range_is_centred_on_the_prior_close_and_atr_skips_the_live_bar(monkeypatch):
    daily, intra = _frames()
    _install(monkeypatch, daily, intra)
    monkeypatch.setattr(webapp, "_session_still_open", lambda *_a, **_k: True)
    out = webapp._key_levels("^GSPC", "^VIX")
    em = out["expected_move"]
    assert em["anchor_basis"] == "prior_close" and em["anchor"] == 100.0
    assert em["low"] < 100.0 < em["high"]
    assert out["atr"] == 2.0, "the live 40-point bar is left out"


def test_after_the_close_the_range_is_for_the_next_session_from_the_last_price(monkeypatch):
    daily, intra = _frames()
    _install(monkeypatch, daily, intra)
    monkeypatch.setattr(webapp, "_session_still_open", lambda *_a, **_k: False)
    em = webapp._key_levels("^GSPC", "^VIX")["expected_move"]
    assert em["anchor_basis"] == "last" and em["anchor"] == 110.0


def test_discover_ranks_only_believable_consensus_targets():
    """Thin coverage and extreme upside (usually a stale target) are left out of the
    analyst list, so ranking by upside cannot put them first."""
    ok = {"upside_pct": 18.0, "analyst_count": 30}
    assert webapp._analyst_pick_eligible(ok)
    assert not webapp._analyst_pick_eligible({**ok, "upside_pct": 83.0}), "ORCL-style +83% is left out"
    assert not webapp._analyst_pick_eligible({**ok, "analyst_count": 3}), "three analysts is too thin"
    assert not webapp._analyst_pick_eligible({**ok, "upside_pct": 1.0})
    assert not webapp._analyst_pick_eligible({"upside_pct": None, "analyst_count": 30})
