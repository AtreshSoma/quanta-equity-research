"""F3/G3: a completed Picks scan survives a server restart, per market."""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402


def test_saved_scan_is_served_per_market(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "_LAST_SCAN_DIR", tmp_path / "last_scan")
    client = webapp.app.test_client()
    none = client.get("/api/stock-picks/last?market=US")
    assert none.status_code == 200 and none.get_json()["result"] is None

    webapp._save_last_scan({"market": "US", "capital": 2500.0},
                           {"scan_status": "ok", "recommendations": [{"ticker": "AAPL"}]})
    body = client.get("/api/stock-picks/last?market=US").get_json()
    assert body["result"]["recommendations"][0]["ticker"] == "AAPL"
    assert body["params"]["capital"] == 2500.0 and body["saved_at"]
    assert client.get("/api/stock-picks/last?market=IN").get_json()["result"] is None


def test_a_corrupt_save_reads_as_no_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "_LAST_SCAN_DIR", tmp_path)
    (tmp_path / "US.json.gz").write_bytes(b"not gzip")
    assert webapp.app.test_client().get("/api/stock-picks/last").get_json()["result"] is None


def test_the_suite_never_writes_the_production_last_scan():
    assert "quanta-test-cache" in str(webapp._LAST_SCAN_DIR)
