"""Regression (owner report, 2026-10-08): the live Overview read "Data Incomplete".

G6 gave the market read one budget for quotes *then* index histories. On a live
feed the quotes could use nearly all of it, so the 50/200-DMA histories never
ran: trend, participation and momentum went unmeasured and the regime fell back
to "Data Incomplete". Now:

* quotes and histories start together, each with its own MARKET budget;
* a history that finishes after the read stopped waiting is cached, so the next
  read has it at once (MARKET.TREND_CACHE_SECONDS);
* with no live quote the read does not wait for histories at all;
* a dashboard built while an input was missing is rebuilt after the short retry
  time instead of being held for the full cache period.

Synthetic quotes and closes only; no provider is called.
"""
from __future__ import annotations

import threading
import time

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402
from engine import thresholds as T  # noqa: E402


def _closes(n=260, start=100.0, step=0.2):
    return [start + i * step for i in range(n)]


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(webapp, "_trend_cache", {})
    monkeypatch.setattr(webapp, "_trend_inflight", {})
    monkeypatch.setattr(T.MARKET, "QUOTE_BUDGET_SECONDS", 0.8)
    monkeypatch.setattr(T.MARKET, "TREND_BUDGET_SECONDS", 0.8)
    return monkeypatch


def _quotes(delay, price=100.0):
    def quote(symbol, label):
        time.sleep(delay)
        return {"symbol": symbol, "label": label, "price": price, "change_pct": 0.5}
    return quote


def _trends(delay, calls=None):
    def trend(symbol):
        if calls is not None:
            calls.append(symbol)
        time.sleep(delay)
        return webapp._trend_metrics_from_closes(symbol, _closes())
    return trend


def _read(market="US"):
    with webapp.app.test_request_context("/api/dashboard"):
        return webapp._market_snapshot(market, news={}, intel={})


def _missing(snapshot):
    return set(snapshot["market_regime_engine"]["dataFreshness"]["missingInputs"])


def test_slow_quotes_no_longer_starve_the_trend_read(fresh):
    # Quotes use most of their budget; under the old single budget the
    # histories (0.3 s) had no time left at all.
    fresh.setattr(webapp, "_quote_snapshot", _quotes(0.7))
    fresh.setattr(webapp, "_trend_metrics", _trends(0.3))
    snap = _read()
    assert not _missing(snap) & {"trend", "trend_history", "participation", "momentum"}, _missing(snap)
    assert snap["market_regime"] != "Data Incomplete"


def test_both_markets_read_their_configured_basket(fresh):
    calls = []
    fresh.setattr(webapp, "_quote_snapshot", _quotes(0.0))
    fresh.setattr(webapp, "_trend_metrics", _trends(0.0, calls))
    for market in ("US", "IN"):
        calls.clear()
        _read(market)
        basket = T.MARKET.TREND_BASKETS[market]
        assert set(calls) == {basket["bench"], *basket["breadth"]}, market
        webapp._trend_cache.clear()


def test_a_late_history_is_cached_for_the_next_read(fresh):
    calls = []
    fresh.setattr(webapp, "_quote_snapshot", _quotes(0.0))
    fresh.setattr(webapp, "_trend_metrics", _trends(1.2, calls))      # slower than the budget
    first = _read()
    assert "participation" in _missing(first)
    deadline = time.time() + 5
    while webapp._trend_inflight and time.time() < deadline:
        time.sleep(0.05)
    calls.clear()
    t0 = time.time()
    second = _read()
    assert time.time() - t0 < 0.5, "served from the trend cache"
    assert calls == [], "no second fetch while the cache is fresh"
    assert not _missing(second) & {"trend", "participation", "momentum"}


def test_a_running_fetch_is_shared_not_repeated(fresh):
    calls = []
    gate = threading.Event()

    def trend(symbol):
        calls.append(symbol)
        gate.wait(2)
        return webapp._trend_metrics_from_closes(symbol, _closes())
    fresh.setattr(webapp, "_trend_metrics", trend)
    a = webapp._trend_future("SPY")
    b = webapp._trend_future("SPY")
    gate.set()
    assert a is b
    assert a.result(timeout=3)["symbol"] == "SPY"
    assert calls == ["SPY"]


def test_with_no_live_quote_the_read_does_not_wait_for_histories(fresh):
    fresh.setattr(webapp, "_quote_snapshot", _quotes(0.0, price=None))
    fresh.setattr(webapp, "_trend_metrics", _trends(5.0))
    fresh.setattr(webapp, "_tape_quote_cache", {})
    t0 = time.time()
    _read()
    assert time.time() - t0 < T.MARKET.QUOTE_BUDGET_SECONDS + 0.5


def test_an_incomplete_dashboard_is_rebuilt_after_the_retry_time(monkeypatch):
    builds = []

    def snapshot(market, news=None, intel=None):
        builds.append(market)
        missing = ["earnings_calendar"] if len(builds) == 1 else []
        return {"quotes": [{"symbol": "SPY", "price": 1.0}],
                "market_regime_engine": {"dataFreshness": {"missingInputs": missing}}}
    monkeypatch.setattr(webapp, "_market_snapshot", snapshot)
    monkeypatch.setattr(webapp, "_build_news_digest", lambda market: {})
    monkeypatch.setattr(webapp, "_market_intel", lambda market, blocking=True: {"loading": False})
    monkeypatch.setattr(webapp, "_engine_payload", lambda: [])
    monkeypatch.setattr(webapp, "_dashboard_cache", {})
    with webapp.app.test_request_context("/api/dashboard"):
        first = webapp._dashboard_payload(market="US")
        assert first["retry_seconds"] == webapp.MARKET_INTEL_RETRY_SECONDS
        webapp._dashboard_payload(market="US")
        assert len(builds) == 1, "inside the retry time the cached read is served"
        key = "timestamp_US_v2"
        webapp._dashboard_cache[key] -= webapp.MARKET_INTEL_RETRY_SECONDS + 1
        webapp._dashboard_payload(market="US")
        assert len(builds) == 2, "an incomplete read is rebuilt after the retry time"
        webapp._dashboard_cache[key] -= webapp.MARKET_INTEL_RETRY_SECONDS + 1
        webapp._dashboard_payload(market="US")
        assert len(builds) == 2, "a complete read keeps the full cache period"
