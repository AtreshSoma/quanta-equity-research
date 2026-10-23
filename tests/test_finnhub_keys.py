"""Finnhub key pool: a 429 moves to the next key, the key never travels in the URL."""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths()
import TradingAnalysis_ELITE as elite  # noqa: E402


class _Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return _Resp(self.codes.pop(0), {"ok": True})


def _client(monkeypatch, keys, single=""):
    monkeypatch.setenv("FINNHUB_API_KEYS", keys)
    monkeypatch.setenv("FINNHUB_API_KEY", single)
    return elite.APIClient({})


def test_a_rate_limited_request_is_retried_with_the_next_key(monkeypatch):
    api = _client(monkeypatch, "k1, k2,k3")
    api.session = _Session([429, 200])
    out = api.fetch_json("https://finnhub.io/api/v1/stock/recommendation",
                         params={"symbol": "AAA", "token": api.finnhub_key}, source="finnhub")
    assert out == {"ok": True}
    assert [c[2]["X-Finnhub-Token"] for c in api.session.calls] == ["k1", "k2"]
    assert all("token" not in c[1] for c in api.session.calls), "the key never goes in the query"


def test_one_key_is_not_retried(monkeypatch):
    api = _client(monkeypatch, "", single="only")
    api.session = _Session([429])
    monkeypatch.setattr(elite.time, "sleep", lambda s: None)
    assert api.fetch_json("https://finnhub.io/api/v1/x", params={"token": "only"}, source="finnhub") is None
    assert len(api.session.calls) == 1 and api.finnhub_keys == ["only"]


def test_the_single_key_leads_the_pool(monkeypatch):
    api = _client(monkeypatch, "k2,k3", single="k1")
    assert api.finnhub_keys == ["k1", "k2", "k3"] and api.finnhub_key == "k1"
