"""My list and background price alerts (webapp/my_list.py)."""
from __future__ import annotations

import threading

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)
import my_list as ml  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return ml.MyListStore(tmp_path / "list.sqlite3")


def test_symbols_are_validated_deduplicated_and_capped(store, monkeypatch):
    assert ml.clean_symbol(" reliance.ns ") == "RELIANCE.NS"
    assert ml.clean_symbol("^VIX") == "^VIX"
    assert ml.clean_symbol("DROP TABLE") is None
    assert store.add_symbol("AAPL") and not store.add_symbol("AAPL")
    monkeypatch.setattr(ml, "MAX_SYMBOLS", 1)
    with pytest.raises(ValueError):
        store.add_symbol("MSFT")


def test_an_alert_fires_once_and_waits_in_the_inbox(store):
    store.add_alert("AAA", "above", 105.0, label="3-month target", source="decision")
    store.add_alert("AAA", "below", 90.0, label="Stop", source="decision")
    sent = []
    quote = lambda s: {"price": 106.0, "market_open": True}  # noqa: E731
    fired = ml.check_alerts(store, quote, lambda t, b: sent.append((t, b)) or True)
    assert [f["label"] for f in fired] == ["3-month target"]
    assert "AAA rose to 106.00: 3-month target 105.00" in sent[0][1]
    # A second check finds nothing new: the alert was claimed.
    assert ml.check_alerts(store, quote, lambda t, b: True) == []
    assert [a["label"] for a in store.inbox()] == ["3-month target"]
    assert [a["label"] for a in store.alerts(active_only=True)] == ["Stop"]
    store.dismiss(None)
    assert store.inbox() == []


def test_a_closed_market_is_not_judged_on_its_stale_close(store):
    store.add_alert("AAA", "below", 90.0)
    fired = ml.check_alerts(store, lambda s: {"price": 80.0, "market_open": False}, lambda t, b: True)
    assert fired == [] and store.alerts(active_only=True)


def test_a_failed_notification_keeps_the_alert_in_the_inbox(store):
    store.add_alert("AAA", "above", 1.0)
    fired = ml.check_alerts(store, lambda s: {"price": 2.0, "market_open": True}, lambda t, b: False)
    assert len(fired) == 1
    row = store.inbox()[0]
    assert row["notified"] == 0 and row["triggered_price"] == 2.0


def test_two_checkers_cannot_announce_one_alert_twice(store):
    store.add_alert("AAA", "above", 1.0)
    alert_id = store.alerts(active_only=True)[0]["id"]
    wins = []
    threads = [threading.Thread(target=lambda: wins.append(store.claim(alert_id, 2.0))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1


def test_the_same_level_twice_is_one_alert(store):
    a = store.add_alert("AAA", "above", 105.0)
    b = store.add_alert("AAA", "above", 105.0)
    assert a == b and len(store.alerts(active_only=True)) == 1
    with pytest.raises(ValueError):
        store.add_alert("AAA", "sideways", 1.0)
    with pytest.raises(ValueError):
        store.add_alert("AAA", "above", -3.0)


def test_routes_need_the_request_header_and_return_the_whole_state(monkeypatch, tmp_path):
    import app as webapp

    ml.init_my_list(webapp.app, lambda s: {"price": 10.0, "market_open": True, "change_pct": 1.0},
                    lambda t, b: True, store=ml.MyListStore(tmp_path / "routes.sqlite3"),
                    last_call=lambda s: {"label": "Accumulate", "engine": "Research", "as_of": "2026-10-21", "price": 9.5})
    client = webapp.app.test_client()
    hdr = {"X-QUANTA-Request": "1"}
    assert client.post("/api/my-list", json={"symbol": "AAPL"}).status_code == 403
    out = client.post("/api/my-list", json={"symbol": "aapl"}, headers=hdr).get_json()
    assert [r["symbol"] for r in out["symbols"]] == ["AAPL"]
    assert client.post("/api/my-list", json={"symbol": "no way"}, headers=hdr).status_code == 400
    out = client.post("/api/alerts", json={"symbol": "AAPL", "direction": "below", "level": 9.5, "label": "Stop"},
                      headers=hdr).get_json()
    assert out["alerts"][0]["level"] == 9.5
    quotes = client.get("/api/my-list/quotes").get_json()
    assert quotes["quotes"]["AAPL"]["price"] == 10.0
    assert quotes["calls"]["AAPL"]["label"] == "Accumulate"
    out = client.post("/api/alerts/check", headers=hdr).get_json()
    assert out["fired"] == [] and out["unread"] == 0
    out = client.delete(f"/api/alerts/{out['alerts'][0]['id']}", headers=hdr).get_json()
    assert out["alerts"] == []
    out = client.delete("/api/my-list/AAPL", headers=hdr).get_json()
    assert out["symbols"] == []
