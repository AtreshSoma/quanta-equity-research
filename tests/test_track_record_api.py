"""The Track Record endpoint reads the shared ledger and the evidence gate.

It must never compute its own statistics or verdicts: groups come from
SignalLedger.summarize and each engine's label from engine.evidence.
"""
from __future__ import annotations

from datetime import date

import pytest

import app as webapp
from engine.decision import Decision, Horizon
from engine.evidence import ELITE, get_evidence
from engine.types import FinalAction
from tracking.ledger import SignalLedger

QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "signals.sqlite3"
    monkeypatch.setenv("QUANTA_LEDGER_PATH", str(path))
    return SignalLedger(path)


def test_empty_ledger_reports_zero_and_untested_engines(ledger):
    res = webapp.app.test_client().get("/api/track-record")
    assert res.status_code == 200
    body = res.get_json()
    assert body["totals"] == {
        "recorded": 0, "resolved": 0, "pending": 0, "awaiting_maturity": 0,
        "awaiting_resolution": 0, "earliest_expected_exit": None, "legacy_excluded": 0,
    }
    assert body["benchmarks"] == [] and body["benchmark_mixed"] is False
    assert {e["engine"] for e in body["engines"]} == {"elite", "elite_pro", "shortterm"}
    # No registry entry means the gate fails closed.
    assert all(not e["evidence"]["permits_recommendation"] for e in body["engines"])


def test_recorded_call_appears_pending_with_its_published_label(ledger):
    decision = Decision(
        ticker="AAPL", engine="elite", horizon=Horizon("position", 63, "63 sessions"),
        action=FinalAction.BUY, evidence=get_evidence(ELITE),
        as_of=date(2026, 9, 18).isoformat(), score=71.0, reference_price=200.0,
        entry=200.0, stop=190.0, target=230.0,
    )
    assert ledger.record_decision(decision) is not None
    body = webapp.app.test_client().get("/api/track-record").get_json()
    assert body["totals"]["recorded"] == 1
    assert body["totals"]["pending"] == 1
    elite = next(e for e in body["engines"] if e["engine"] == "elite")
    assert len(elite["code_fingerprint"]) == 64 and elite["recorded_on_current_code"] == 1
    assert body["recent"][0]["ticker"] == "AAPL"
    assert body["recent"][0]["label"] == "BUY"   # the call as published; evidence is reported beside it
    group = body["groups"][0]
    assert group["n_pending"] == 1 and group["sufficient"] is False


def test_unknown_engine_filter_is_rejected(ledger):
    assert webapp.app.test_client().get("/api/track-record?engine=nope").status_code == 400


def test_resolve_endpoint_scores_and_surfaces_level_alerts(ledger, monkeypatch):
    import pandas as pd

    import tracking.ledger as tl

    idx = pd.bdate_range("2026-01-05", periods=30)
    stock = pd.DataFrame({"Open": 100.0, "High": [101.0] * 3 + [130.0] * 27,
                          "Low": 99.0, "Close": 100.0}, index=idx)
    spy = pd.DataFrame({"Open": 400.0, "High": 401.0, "Low": 399.0, "Close": 400.0}, index=idx)
    monkeypatch.setattr(tl, "yfinance_loader", lambda t, s, e: spy if t == "SPY" else stock)
    import tracking.alerts as ta
    notified = []
    monkeypatch.setattr(ta, "notify_desktop", lambda alerts: notified.append(list(alerts)))
    ledger.record(engine="shortterm", ticker="ABC", as_of=date(2026, 1, 5), horizon_days=200,
                  stance="BULLISH", action="BUY", target_price=120.0, stop_price=90.0)
    with ledger._connect() as con:
        con.execute("UPDATE signals SET recorded_at='2026-01-05T22:00:00+00:00'")

    res = webapp.app.test_client().post("/api/track-record/resolve", headers=QUANTA_HEADERS)
    assert res.status_code == 200
    body = res.get_json()
    assert body["resolved"] == 0  # a 200-session horizon has not elapsed
    assert [(a["ticker"], a["kind"], a["session"]) for a in body["alerts"]] == [
        ("ABC", "TARGET", "2026-01-08")]
    # Announced by the endpoint means the scheduled job stays silent, so it notifies.
    assert [a.kind for a in notified[0]] == ["TARGET"]

    page = webapp.app.test_client().get("/api/track-record").get_json()
    assert [a["kind"] for a in page["alerts"]] == ["TARGET"]
    # Announced once: a second run reports nothing new.
    assert webapp.app.test_client().post("/api/track-record/resolve", headers=QUANTA_HEADERS).get_json()["alerts"] == []
