"""Production audit (October 2026) G6: transfer speed of the web app.

* Text bodies (page, API JSON, scripts, styles) are gzipped for clients that
  accept it; the decompressed body is the original byte for byte.
* Versioned static URLs (asset_url's ?v=mtime) are cacheable for a year.
* A server-sent event stream is never buffered or compressed.
* The market read's time budgets come from MARKET config (see test_market_read_budget.py).
"""
from __future__ import annotations

import gzip
import re

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402
from engine import thresholds as T  # noqa: E402

GZ = {"Accept-Encoding": "gzip, deflate, br"}


def _client():
    return webapp.app.test_client()


def test_static_scripts_are_gzipped_and_identical_when_decompressed():
    c = _client()
    plain = c.get("/static/app.js").get_data()
    resp = c.get("/static/app.js", headers=GZ)
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in resp.headers.get("Vary", "")
    body = resp.get_data()
    assert len(body) < len(plain) / 2
    assert gzip.decompress(body) == plain
    assert int(resp.headers["Content-Length"]) == len(body)


def test_api_json_and_the_page_are_gzipped_for_clients_that_accept_it():
    c = _client()
    for path in ("/api/thresholds", "/"):
        resp = c.get(path, headers=GZ)
        assert resp.headers.get("Content-Encoding") == "gzip", path
        assert gzip.decompress(resp.get_data())
    assert c.get("/api/thresholds").headers.get("Content-Encoding") is None


def test_versioned_static_urls_are_cached_for_a_year():
    html = _client().get("/").get_data(as_text=True)
    url = re.search(r'src="(/static/app\.js\?v=\d+)"', html).group(1)
    assert "immutable" in _client().get(url).headers.get("Cache-Control", "")
    assert "immutable" not in (_client().get("/static/app.js").headers.get("Cache-Control") or "")


def test_an_event_stream_is_never_compressed():
    job_id = "gztest01"
    with webapp._jobs_lock:
        webapp._jobs[job_id] = {"status": "complete", "progress": 100, "result": {"x": 1}, "error": None,
                                "type": "analyze", "message": "done", "created_at": 0, "finished_at": 0}
    try:
        resp = _client().get(f"/api/jobs/{job_id}/stream", headers=GZ)
        assert resp.mimetype == "text/event-stream"
        assert resp.headers.get("Content-Encoding") is None
    finally:
        with webapp._jobs_lock:
            webapp._jobs.pop(job_id, None)


def test_the_market_read_budgets_come_from_config():
    assert T.MARKET.QUOTE_BUDGET_SECONDS > 0 and T.MARKET.TREND_BUDGET_SECONDS > 0
    src = (webapp.WEBAPP_DIR / "app.py").read_text(encoding="utf-8")
    assert "_T.MARKET.QUOTE_BUDGET_SECONDS" in src and "_T.MARKET.TREND_BUDGET_SECONDS" in src
    assert "_completed_snapshot_jobs(futures, 12)" not in src and "_completed_snapshot_jobs(futures, 8)" not in src
