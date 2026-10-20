"""My Portfolio price data (2026-10-19 review, items 1 to 4).

* A "daily" frame that is not daily is discarded, so intraday bars can never be drawn as a
  30-day trend or feed the performance chart.
* The three Yahoo downloads take turns (yfinance keeps shared state between downloads).
* A failed price read is retried within seconds, not replayed for fifteen minutes.
"""
from __future__ import annotations

import time

import pandas as pd

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
from portfolio import history as hs  # noqa: E402
from portfolio import insights as ins  # noqa: E402


def _daily(n=30):
    return pd.DataFrame({"AAA": range(100, 100 + n)}, index=pd.bdate_range("2026-08-03", periods=n))


def test_recent_paths_refuses_a_frame_whose_days_repeat():
    ok = hs.recent_paths(_daily(30), ["AAA"], 22)
    assert ok["AAA"]["start"] < ok["AAA"]["end"] and len(ok["AAA"]["values"]) == 22
    # 5-minute bars on one date: the same date 22 times, start == end.
    stamps = pd.date_range("2026-10-19 09:30", periods=30, freq="5min")
    intraday = pd.DataFrame({"AAA": range(100, 130)}, index=stamps)
    assert hs.recent_paths(intraday, ["AAA"], 22) == {}


def test_daily_frame_check():
    assert webapp._is_daily_frame(pd.bdate_range("2026-09-01", periods=20))
    five_min = pd.date_range("2026-10-19 09:30", periods=40, freq="5min")
    assert not webapp._is_daily_frame(five_min), "one date, many rows"
    assert not webapp._is_daily_frame(pd.bdate_range("2026-09-01", periods=3)), "too short to be a trend"
    shuffled = list(pd.bdate_range("2026-09-01", periods=10))[::-1]
    assert not webapp._is_daily_frame(shuffled), "not oldest first"


def test_a_mixed_frequency_download_is_discarded(monkeypatch):
    import types
    stamps = pd.date_range("2026-10-19 09:30", periods=40, freq="5min", tz="America/New_York")
    wrong = pd.concat({"Close": pd.DataFrame({"AAA": range(40), "SPY": range(40)}, index=stamps)}, axis=1)
    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        types.SimpleNamespace(download=lambda *a, **k: wrong))
    monkeypatch.setattr(webapp, "_provider_in_cooldown", lambda: False)
    assert webapp._portfolio_prices(["AAA", "SPY"], "1y") is None
    assert webapp._portfolio_returns(["AAA", "SPY"]) is None


def test_the_three_downloads_take_turns(monkeypatch):
    import sys
    import threading
    import types
    active, peak, lock = [0], [0], threading.Lock()

    def slow(*a, **k):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return None
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=slow))
    monkeypatch.setattr(webapp, "_provider_in_cooldown", lambda: False)
    jobs = [lambda: webapp._portfolio_prices(["A", "B"], "1y"), lambda: webapp._portfolio_intraday(["A", "B"]),
            lambda: webapp._portfolio_returns(["A", "B"])]
    threads = [threading.Thread(target=j) for j in jobs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1, "downloads overlapped"


def test_a_failed_price_read_is_retried_quickly_not_after_fifteen_minutes():
    assert ins.PRICE_RETRY_TTL < 60 < ins.PRICE_TTL
    calls = []

    class Src:
        prices = staticmethod(lambda symbols, period: calls.append(1) or (None if len(calls) == 1 else "FRAME"))
        intraday = None
    obj = ins.Insights.__new__(ins.Insights)
    obj.src = Src()
    obj._closes = {}
    import threading
    obj._lock = threading.RLock()
    assert obj._prices(["A"], "1y") is None
    assert obj._prices(["A"], "1y") is None and len(calls) == 1, "inside the retry window the failure is reused"
    key = (("A",), "1y")
    obj._closes[key] = (time.time() - ins.PRICE_RETRY_TTL - 1, None)
    assert obj._prices(["A"], "1y") == "FRAME" and len(calls) == 2, "after it the provider is asked again"


def test_risk_level_says_which_inputs_it_could_not_measure():
    """Item 7: a missing volatility adds no points, so 4 of 9 is a floor, not a full reading."""
    from portfolio import analytics as an
    conc = {"top": {"weight": 0.22}}
    sectors = [{"sector": "Technology", "weight": 0.47}]
    beta = {"equity_beta": 1.07, "coverage": 0.99}
    stops = {"loss_pct": 0.06}
    partial = an.risk_level(conc, sectors, beta, {"volatility": None, "coverage": 0.0}, stops)
    assert partial["points"] == 4 and partial["level"] == "Elevated"
    assert partial["partial"] is True and partial["unmeasured"] == ["volatility"]
    full = an.risk_level(conc, sectors, beta, {"volatility": 0.18, "coverage": 0.95}, stops)
    assert full["partial"] is False and full["unmeasured"] == []
    none = an.risk_level({"top": None}, [], {"equity_beta": None, "coverage": 0}, {"volatility": None, "coverage": 0}, {})
    assert none["points"] is None and none["partial"] is True


def test_holding_events_lead_each_bucket_and_market_items_are_labelled():
    """Item 29: general market stories never crowd out an item that names a holding."""
    from datetime import datetime, timezone
    from portfolio import events as ev
    now = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    rows = [{"symbol": "AAA", "broker": {"weight": 0.2, "earnings_date": "2026-10-21", "name": "Alpha"}, "market": {}}]
    arts = [{"title": "Markets wobble as yields climb", "link": "https://example.com/a", "source": "Wire",
             "published": "2026-10-19T14:00:00+00:00", "summary": ""}]
    out = ev.build_events(rows, now=now, policy={"earnings_window_days": 7}, filings={}, articles=arts,
                          calendar={"status": "ok", "events": []})
    items = out["upcoming"]
    assert items, "feed is empty"
    seen_market = False
    for it in items:
        assert it["relevance"] in ("holding", "market")
        if it["bucket"] == "today":
            if it["relevance"] == "market":
                seen_market = True
            else:
                assert not seen_market, "a holding item sits after a general market item"


def test_policy_trims_are_not_scored_as_forecasts():
    """Item 28: a concentration or risk-grade trim is not 'right' only if the stock falls."""
    assert hs._is_directional({"action": "Trim", "basis_code": "SCORE_REDUCE_BAND"})
    assert not hs._is_directional({"action": "Trim", "basis_code": "CONCENTRATION"})
    assert not hs._is_directional({"action": "Trim", "basis_code": "RISK_GRADE_F"})
    assert hs._is_directional({"action": "Accumulate", "basis_code": None})
    assert not hs._is_directional({"action": "Hold", "basis_code": None})
