"""Research chart and search endpoints (2026-10-09).

* Both refuse a browser read started by another site, so another tab cannot
  spend QUANTA's provider budget or push it into a rate-limit cooldown.
* Input is validated before any provider call; nothing is reflected.
* The chart serves cached bars without a provider call while they are fresh.
No test here reaches the network: the provider call is replaced.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402


def _client():
    return webapp.app.test_client()


def test_cross_site_reads_are_refused():
    c = _client()
    for path in ("/api/chart?symbol=AAPL&range=1d", "/api/symbol-search?q=apple"):
        resp = c.get(path, headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403, path
        assert "Cross-site request refused" in resp.get_data(as_text=True)


def test_chart_validates_symbol_and_range_before_any_provider_call(monkeypatch):
    calls = []
    monkeypatch.setattr(webapp, "_chart_payload", lambda *a: calls.append(a) or {"available": True})
    c = _client()
    assert c.get("/api/chart?symbol=<script>&range=1d").status_code == 400
    assert c.get("/api/chart?range=1d").status_code == 400
    bad = c.get("/api/chart?symbol=AAPL&range=10y")
    assert bad.status_code == 400 and "<" not in bad.get_data(as_text=True)
    assert calls == []


def test_chart_serves_fresh_cached_bars_without_a_provider_call(monkeypatch):
    calls = []

    def fake(symbol, range_key):
        calls.append((symbol, range_key))
        return {"available": True, "symbol": symbol, "range": range_key, "points": [[0, 1.0, None]]}

    monkeypatch.setattr(webapp, "_chart_payload", fake)
    monkeypatch.setattr(webapp, "_provider_in_cooldown", lambda: False)
    webapp._chart_cache.clear()
    c = _client()
    first = c.get("/api/chart?symbol=TEST&range=1y", headers={"Sec-Fetch-Site": "same-origin"}).get_json()
    second = c.get("/api/chart?symbol=TEST&range=1y").get_json()
    assert first["cached"] is False and second["cached"] is True
    assert calls == [("TEST", "1y")]


def test_search_rejects_unusable_queries_without_a_provider_call():
    c = _client()
    for q in ("", "<img>", "x" * 41):
        resp = c.get("/api/symbol-search", query_string={"q": q})
        assert resp.status_code == 200 and resp.get_json()["results"] == [], q
