"""The Recent scans endpoints: list, reopen one, and what /last now means."""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
import saved_scans  # noqa: E402


def _params(**kw):
    p = {"market": "US", "capital": 2500.0, "scan_type": "standard", "max_positions": 5,
         "diversify": True, "risk_profile": "balanced"}
    p.update(kw)
    return p


def _result(ticker):
    return {"scan_status": "ok", "total_scanned": 250, "stock_picks": [{"ticker": ticker, "call": "buy"}],
            "funded_count": 1, "regime": {"type": "BULL"}}


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "_LAST_SCAN_DIR", tmp_path / "last_scan")
    return webapp.app.test_client()


def test_two_settings_are_listed_and_each_reopens(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/stock-picks/recent?market=US").get_json()["scans"] == []

    webapp._save_last_scan(_params(scan_type="standard"), _result("AAPL"))
    webapp._save_last_scan(_params(scan_type="focused_growth"), _result("MSFT"))

    body = client.get("/api/stock-picks/recent?market=US").get_json()
    assert body["retention_hours"] == 24
    assert [s["scan_type"] for s in body["scans"]] == ["focused_growth", "standard"]
    assert all("result" not in s for s in body["scans"]), "the list carries headline counts, not whole results"
    assert body["scans"][0]["buy_accumulate"] == 1 and body["scans"][0]["scanned"] == 250

    sid = body["scans"][1]["id"]
    full = client.get(f"/api/stock-picks/recent/{sid}?market=US").get_json()
    assert full["result"]["stock_picks"][0]["ticker"] == "AAPL" and full["params"]["scan_type"] == "standard"
    assert full["id"] == sid and full["saved_at"]


def test_last_is_the_newest_saved_scan(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    webapp._save_last_scan(_params(scan_type="standard"), _result("AAPL"))
    webapp._save_last_scan(_params(scan_type="focused_etf"), _result("SPY"))
    last = client.get("/api/stock-picks/last?market=US").get_json()
    assert last["params"]["scan_type"] == "focused_etf" and last["result"]["stock_picks"][0]["ticker"] == "SPY"


def test_an_unknown_expired_or_hostile_id_is_a_404_not_a_path(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    webapp._save_last_scan(_params(), _result("AAPL"))
    for bad in ("0123456789ab", "..%2F..%2Fsecret", "%2e%2e", "x" * 12, "ABCDEF012345"):
        assert client.get(f"/api/stock-picks/recent/{bad}?market=US").status_code == 404, bad
    # A scan saved under US is not reachable through the India market.
    sid = client.get("/api/stock-picks/recent?market=US").get_json()["scans"][0]["id"]
    assert client.get(f"/api/stock-picks/recent/{sid}?market=IN").status_code == 404


def test_the_list_survives_a_broken_store(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(saved_scans, "list_entries", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    res = client.get("/api/stock-picks/recent?market=US")
    assert res.status_code == 200 and res.get_json()["scans"] == []


def test_a_failed_save_never_fails_the_scan(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    monkeypatch.setattr(saved_scans, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    webapp._save_last_scan(_params(), _result("AAPL"))        # must not raise


def test_the_retention_is_published_for_the_page():
    body = webapp.app.test_client().get("/api/thresholds").get_json()
    saved = (body.get("thresholds") or body)["SAVED_SCANS"]
    assert saved["RETENTION_HOURS"] == 24 and saved["MAX_SAVED"] == 12
