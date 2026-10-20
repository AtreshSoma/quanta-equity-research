"""Offline regression coverage for dashboard feed recovery and request budgets."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(webapp, '_tape_quote_cache', {})
    monkeypatch.setattr(webapp, '_market_intel_cache', {})
    monkeypatch.setattr(webapp, '_market_intel_inflight', {})
    monkeypatch.setattr(webapp, '_dashboard_cache', {})


def test_empty_history_recovers_quote_from_fast_info(monkeypatch):
    ticker = SimpleNamespace(history=lambda **_: pd.DataFrame(),
                             fast_info={'lastPrice': 110, 'previousClose': 100})
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda _: ticker))
    quote = webapp._quote_snapshot('SPY', 'S&P 500')
    assert quote['price'] == 110
    assert quote['change_pct'] == 10
    assert quote['as_of']


def test_quote_cache_preserves_timestamp_and_labels_failed_refresh(monkeypatch):
    calls = []
    def fetch(symbol, label):
        calls.append(symbol)
        return {'symbol': symbol, 'label': label, 'price': 100 if len(calls) == 1 else None}
    monkeypatch.setattr(webapp, '_fetch_tape_quote', fetch)
    original = webapp._quote_snapshot('SPY', 'S&P 500')
    webapp._quote_snapshot('SPY', 'S&P 500')
    assert len(calls) == 1
    webapp._tape_quote_cache['SPY']['timestamp'] = 0
    stale = webapp._quote_snapshot('SPY', 'S&P 500')
    assert stale['price'] == 100 and stale['stale']
    assert stale['as_of'] == original['as_of']


def test_missing_previous_close_does_not_invent_zero_change(monkeypatch):
    ticker = SimpleNamespace(history=lambda **_: pd.DataFrame({'Close': [100.0]}))
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda _: ticker))
    assert webapp._quote_snapshot('SPY', 'S&P 500')['change_pct'] is None


def test_history_cache_retries_empty_response_and_repairs_old_empty_entry(monkeypatch):
    frame = pd.DataFrame({'Close': [100.0, 101.0]})
    results = iter([pd.DataFrame(), frame])
    monkeypatch.setattr(webapp, '_orig_history', lambda *a, **k: next(results))
    monkeypatch.setattr(webapp, '_yf_hist_cache', {})
    monkeypatch.setattr(webapp, '_yf_cache_save_disk', lambda: None)
    ticker = SimpleNamespace(_quanta_symbol='SPY')
    key = ('SPY', (), (('period', '5d'),))
    webapp._yf_hist_cache[key] = (time.time(), pd.DataFrame())
    assert webapp._cached_history(ticker, period='5d').empty
    assert webapp._cached_history(ticker, period='5d').equals(frame)
    # A third read must hit the recovered cache; the provider iterator is empty.
    assert webapp._cached_history(ticker, period='5d').equals(frame)


def test_info_cache_does_not_hold_empty_provider_results(monkeypatch):
    results = iter([{}, {'shortName': 'Apple'}])
    monkeypatch.setattr(webapp, '_orig_info_get', lambda _: next(results))
    monkeypatch.setattr(webapp, '_yf_info_cache', {'AAPL': (time.time(), {})})
    monkeypatch.setattr(webapp, '_yf_cache_save_disk', lambda: None)
    ticker = SimpleNamespace(_quanta_symbol='AAPL')
    assert webapp._cached_info_getter(ticker) == {}
    assert webapp._cached_info_getter(ticker)['shortName'] == 'Apple'
    assert webapp._cached_info_getter(ticker)['shortName'] == 'Apple'


def test_repeated_requests_join_one_market_warmup(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def build(key):
        calls.append(key)
        entered.set()
        release.wait(3)
        webapp._market_intel_cache[key] = {'timestamp': time.time(), 'payload': {'loading': False}}
    monkeypatch.setattr(webapp, '_build_market_intel', build)
    try:
        assert webapp._market_intel('US', blocking=False)['loading']
        assert entered.wait(1)
        event = webapp._market_intel_inflight['US']
        for _ in range(10):
            assert webapp._market_intel('US', blocking=False)['loading']
        assert calls == ['US']
        release.set()
        assert event.wait(1)
        assert webapp._market_intel('US', blocking=True) == {'loading': False}
    finally:
        release.set()
        if 'event' in locals():
            event.wait(1)


def test_slow_universe_does_not_hold_completed_screener_rows(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(webapp, 'MARKET_INTEL_FETCH_SECONDS', 0.05)
    monkeypatch.setattr(webapp, '_broad_universe', lambda _: ['SLOW'])
    monkeypatch.setattr(webapp, '_ticker_intel', lambda _: release.wait(2) and {})
    monkeypatch.setattr(webapp, '_screener_movers', lambda side, _: [
        {'symbol': 'FAST', 'price': 100, 'change_pct': 5}
    ] if side == 'day_gainers' else [])
    try:
        started = time.monotonic()
        result = webapp._market_intel('US', blocking=True)
        assert time.monotonic() - started < 1
        assert result['movers_up'][0]['symbol'] == 'FAST'
        assert result['partial'] and not result['loading']
    finally:
        release.set()


def test_degraded_intel_cache_retries_before_ten_minutes(monkeypatch):
    webapp._market_intel_cache['US'] = {'timestamp': time.time() - 20,
                                      'payload': {'degraded': True, 'loading': False}}
    def build(key):
        webapp._market_intel_cache[key] = {'timestamp': time.time(), 'payload': {'recovered': True}}
    monkeypatch.setattr(webapp, '_build_market_intel', build)
    assert webapp._market_intel('US', blocking=True) == {'recovered': True}


def test_empty_dashboard_cache_does_not_hide_recovered_tape(monkeypatch):
    webapp._dashboard_cache.update(payload_US_v2={'market': {'quotes': [{'price': None}]}},
                                   timestamp_US_v2=time.time() - 20)
    monkeypatch.setattr(webapp, '_market_intel', lambda *a, **k: {})
    monkeypatch.setattr(webapp, '_build_news_digest', lambda _: {})
    monkeypatch.setattr(webapp, '_engine_payload', lambda: [])
    monkeypatch.setattr(webapp, '_market_snapshot', lambda *a, **k: {'quotes': [{'price': 100}]})
    with webapp.app.test_request_context('/'):
        assert webapp._dashboard_payload()['market']['quotes'][0]['price'] == 100


def test_concurrent_dashboard_misses_build_the_payload_once(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    builds = []

    def snapshot(*a, **k):
        builds.append(1)
        entered.set()
        release.wait(3)
        return {'quotes': [{'price': 100}]}

    monkeypatch.setattr(webapp, '_market_intel', lambda *a, **k: {})
    monkeypatch.setattr(webapp, '_build_news_digest', lambda _: {})
    monkeypatch.setattr(webapp, '_engine_payload', lambda: [])
    monkeypatch.setattr(webapp, '_market_snapshot', snapshot)
    results = []

    def visit():
        with webapp.app.test_request_context('/'):
            results.append(webapp._dashboard_payload())

    threads = [threading.Thread(target=visit) for _ in range(4)]
    try:
        threads[0].start()
        assert entered.wait(2)
        for t in threads[1:]:
            t.start()
        time.sleep(0.1)
        release.set()
        for t in threads:
            t.join(3)
    finally:
        release.set()
    assert len(builds) == 1
    assert [r['market']['quotes'][0]['price'] for r in results] == [100] * 4


def test_forced_refresh_still_rebuilds(monkeypatch):
    builds = []
    monkeypatch.setattr(webapp, '_market_intel', lambda *a, **k: {})
    monkeypatch.setattr(webapp, '_build_news_digest', lambda _: {})
    monkeypatch.setattr(webapp, '_engine_payload', lambda: [])
    monkeypatch.setattr(webapp, '_market_snapshot',
                        lambda *a, **k: builds.append(1) or {'quotes': [{'price': 100}]})
    with webapp.app.test_request_context('/'):
        webapp._dashboard_payload()
        webapp._dashboard_payload()
        assert len(builds) == 1
        webapp._dashboard_payload(force_refresh=True)
    assert len(builds) == 2
