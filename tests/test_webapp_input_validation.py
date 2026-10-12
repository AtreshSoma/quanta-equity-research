"""Offline contract tests for mutation-endpoint input validation."""

from __future__ import annotations

import sys
import pytest
from pathlib import Path


if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402

# Every state-changing request must carry the app's CSRF header.
QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


@pytest.mark.parametrize("body", [
    '[]', '[1]', '"scan"', '17', 'true', 'null', '{broken',
    '{"diversify":"false"}', '{"diversify":0}', '{"diversify":1}',
    '{"safe_mode":"false"}', '{"max_positions":1.8}',
    '{"max_positions":true}', '{"capital":true}',
    '{"market":"IN","capital":9999}',
    '{"scan_type":"custom"}', '{"scan_type":"custom","custom_tickers":[]}',
])
def test_picks_invalid_schema_creates_no_job(monkeypatch, body):
    monkeypatch.setattr(webapp, "_jobs", {})
    def unexpected_thread(*args, **kwargs):
        pytest.fail("Invalid request started a worker")
    monkeypatch.setattr(webapp.threading, "Thread", unexpected_thread)
    response = webapp.app.test_client().post(
        "/api/stock-picks", headers=QUANTA_HEADERS,
        data=body, content_type="application/json",
    )
    assert response.status_code == 400
    assert webapp._jobs == {}


def test_analyze_rejects_unsupported_ticker_characters():
    client = webapp.app.test_client()
    response = client.post("/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL;rm"})
    assert response.status_code == 400
    assert "unsupported" in response.get_json()["error"]


def test_stock_picks_rejects_non_numeric_capital():
    client = webapp.app.test_client()
    response = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json={"capital": "not-a-number"})
    assert response.status_code == 400


def test_stock_picks_rejects_out_of_range_position_count():
    client = webapp.app.test_client()
    response = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json={"max_positions": 1001})
    assert response.status_code == 400


def test_stock_picks_normalizes_valid_custom_tickers(monkeypatch):
    started = []

    class _NoopThread:
        def __init__(self, *args, **kwargs):
            started.append((args, kwargs))

        def start(self):
            return None

    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    client = webapp.app.test_client()
    response = client.post(
        "/api/stock-picks",
        headers=QUANTA_HEADERS, json={"scan_type": "custom", "custom_tickers": ["aapl", "brk-b"]},
    )
    assert response.status_code == 202
    params = started[0][1]["args"][1]
    assert params["custom_tickers"] == ["AAPL", "BRK-B"]
