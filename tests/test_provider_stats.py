"""Every Yahoo request is counted, timed and classified for /api/health."""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402


class _Resp:
    def __init__(self, code):
        self.status_code = code


@pytest.fixture
def wrapper(monkeypatch):
    monkeypatch.setattr(webapp, "_throttled_yf", lambda: 0.05)
    webapp._yf_events.clear()
    return webapp._throttled_make_request


def test_requests_are_counted_by_outcome_with_latency_and_wait(monkeypatch, wrapper):
    replies = iter([_Resp(200), _Resp(429), _Resp(500)])
    monkeypatch.setattr(webapp, "_orig_make_request", lambda self, *a, **k: next(replies))
    for _ in range(3):
        wrapper(None, "https://query2.finance.yahoo.com/v8/finance/chart/AAA")

    def boom(self, *a, **k):
        raise RuntimeError("YFRateLimitError: Too Many Requests")

    monkeypatch.setattr(webapp, "_orig_make_request", boom)
    with pytest.raises(RuntimeError):
        wrapper(None, "https://example")
    stats = webapp._yf_request_stats()
    assert stats["requests"] == 4 and stats["errors"] == 1 and stats["rate_limited"] == 2
    assert stats["latency_ms_p50"] is not None and stats["throttle_wait_ms"] == pytest.approx(200.0)


def test_health_reports_the_request_stats(monkeypatch):
    payload = webapp._backend_health(probe=False)
    assert set(payload["yahoo_requests"]) >= {"requests", "errors", "rate_limited", "latency_ms_p95", "since_start"}
