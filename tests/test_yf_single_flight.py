"""The yfinance cache: one network call per key at a time, stale beats empty."""
from __future__ import annotations

import threading
import time

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402


def _usable(v):
    return v is not None


def test_concurrent_misses_share_one_fetch():
    cache = {}
    calls = []
    gate = threading.Event()

    def fetch():
        calls.append(1)
        gate.wait(2)
        return "bars"

    out = []
    threads = [threading.Thread(target=lambda: out.append(
        webapp._yf_single_flight(cache, "k1", 60, fetch, _usable)[0])) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert out == ["bars"] * 8


def test_a_failed_refresh_serves_the_expired_copy():
    cache = {"k2": (time.time() - 3600, "old bars")}

    def boom():
        raise ConnectionError("provider down")

    value, cached = webapp._yf_single_flight(cache, "k2", 60, boom, _usable)
    assert value == "old bars" and cached


def test_an_empty_answer_is_never_cached():
    cache = {}
    value, cached = webapp._yf_single_flight(cache, "k3", 60, lambda: None, _usable)
    assert value is None and not cached and "k3" not in cache


def test_too_old_a_copy_is_not_served(monkeypatch):
    monkeypatch.setattr(webapp, "_YF_STALE_GRACE", 100.0)
    cache = {"k4": (time.time() - 1000, "ancient")}
    value, _ = webapp._yf_single_flight(cache, "k4", 60, lambda: None, _usable)
    assert value is None
