"""Plan API: CSRF guard, strict input validation (400), success shapes, and the
422 / 502 paths. Data loaders are mocked, so no network is touched."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from flask import Flask

WEBAPP = __import__("pathlib").Path(__file__).resolve().parent.parent / "webapp"
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

import plan_routes  # noqa: E402

_REAL_RESEARCH_READ = plan_routes.research_read
_REAL_FILINGS_READ = plan_routes.filings_read

HEADERS = {"X-QUANTA-Request": "1"}


def _bars(n=800, seed=4):
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.012, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.004, "Low": np.minimum(o, c) * 0.996,
                         "Close": c}, index=pd.bdate_range("2020-01-01", periods=n))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(plan_routes, "load_bars", lambda symbol: _bars())
    monkeypatch.setattr(plan_routes, "research_read", lambda symbol: None)
    monkeypatch.setattr(plan_routes, "holdings_read", lambda: None)
    monkeypatch.setattr(plan_routes, "filings_read", lambda symbol: None)
    app = Flask(__name__)
    app.register_blueprint(plan_routes.bp)
    return app.test_client()


SIM = {"ticker": "AAPL", "entry": 100.0, "stop": 95.0, "target": 110.0}
CHK = {"ticker": "AAPL", "shares": 10, "entry": 100.0, "stop": 95.0, "target": 112.0,
       "account_value": 50000, "cash": 10000}


# ---------------------------------------------------------------------------
# CSRF and host guard
# ---------------------------------------------------------------------------

def test_post_without_the_custom_header_is_refused(client):
    r = client.post("/api/plan/simulate", json=SIM)
    assert r.status_code == 403
    assert "header" in r.get_json()["error"].lower()


def test_post_with_a_cross_origin_is_refused(client):
    r = client.post("/api/plan/check", json=CHK,
                    headers={**HEADERS, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_non_loopback_host_is_refused(client):
    r = client.post("/api/plan/simulate", json=SIM, headers=HEADERS,
                    environ_overrides={"HTTP_HOST": "evil.example"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Validation: every bad input is a 400 with {"error": ...}
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body, fragment", [
    ({**SIM, "ticker": ""}, "ticker"),
    ({**SIM, "ticker": "A B"}, "unsupported"),
    ({**SIM, "ticker": "AB;DROP"}, "unsupported"),
    ({k: v for k, v in SIM.items() if k != "entry"}, "entry is required"),
    ({**SIM, "entry": True}, "must be a number"),
    ({**SIM, "entry": "100"}, "must be a number"),
    ({**SIM, "entry": -1.0}, "between"),
    ({**SIM, "entry": 0}, "between"),
    ({**SIM, "stop": 101.0}, "stop must be below entry"),
    ({**SIM, "target": 99.0}, "target above"),
    ({**SIM, "side": "short"}, "only long"),
    ({**SIM, "horizon_sessions": 4}, "between"),
    ({**SIM, "horizon_sessions": 127}, "between"),
    ({**SIM, "horizon_sessions": 63.5}, "whole number"),
    ({**SIM, "paths": 10}, "between"),
    ({**SIM, "lookback_sessions": 100}, "between"),
    ({**SIM, "block_mean": 50}, "between"),
    ({**SIM, "drift": "up"}, "drift"),
])
def test_simulate_rejects_bad_input_with_400(client, body, fragment):
    r = client.post("/api/plan/simulate", json=body, headers=HEADERS)
    assert r.status_code == 400
    assert fragment in r.get_json()["error"]


def test_simulate_rejects_non_finite_numbers(client):
    r = client.post("/api/plan/simulate", data='{"ticker":"AAPL","entry":NaN,"stop":95,"target":110}',
                    content_type="application/json", headers=HEADERS)
    assert r.status_code == 400
    assert "finite" in r.get_json()["error"]


def test_simulate_rejects_a_body_that_is_not_an_object(client):
    r = client.post("/api/plan/simulate", data="[1, 2]", content_type="application/json", headers=HEADERS)
    assert r.status_code == 400


@pytest.mark.parametrize("body, fragment", [
    ({**CHK, "shares": 0}, "between"),
    ({**CHK, "shares": "10"}, "must be a number"),
    ({**CHK, "account_value": -5}, "between"),
    ({**CHK, "cash": -1}, "between"),
    ({**CHK, "side": "sell"}, "only buy"),
    ({**CHK, "date": "2026-02-30"}, "real calendar date"),
    ({**CHK, "date": "28/10/2026"}, "YYYY-MM-DD"),
])
def test_check_rejects_bad_input_with_400(client, body, fragment):
    r = client.post("/api/plan/check", json=body, headers=HEADERS)
    assert r.status_code == 400
    assert fragment in r.get_json()["error"]


# ---------------------------------------------------------------------------
# Success shapes
# ---------------------------------------------------------------------------

def test_simulate_success_shape(client):
    r = client.post("/api/plan/simulate", json={**SIM, "paths": 1000, "seed": 5}, headers=HEADERS)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ticker"] == "AAPL" and body["available"] is True
    assert body["seed"] == 5 and body["paths"] == 1000 and body["horizon_sessions"] == 63
    for key in ("plan", "simulation", "historical", "history", "verdict", "summary", "notes"):
        assert key in body
    assert {"p_target", "p_stop", "p_timeout", "break_even_share", "expected_r", "r"} <= set(body["simulation"])
    assert {"mean", "median", "p5", "p95"} <= set(body["simulation"]["r"])
    assert len(body["historical"]["wilson95_effective"]) == 2


def test_simulate_is_deterministic_over_http(client):
    a = client.post("/api/plan/simulate", json={**SIM, "paths": 1000, "seed": 9}, headers=HEADERS).get_json()
    b = client.post("/api/plan/simulate", json={**SIM, "paths": 1000, "seed": 9}, headers=HEADERS).get_json()
    assert a == b


def test_simulate_refuses_short_history_with_a_reason(client, monkeypatch):
    monkeypatch.setattr(plan_routes, "load_bars", lambda symbol: _bars(n=200))
    r = client.post("/api/plan/simulate", json=SIM, headers=HEADERS)
    assert r.status_code == 422
    body = r.get_json()
    assert body["available"] is False
    assert "sessions of history" in body["error"]


def test_simulate_reports_a_failed_price_fetch_as_502(client, monkeypatch):
    def boom(symbol):
        raise plan_routes.PlanDataError("Price bars could not be fetched right now.")
    monkeypatch.setattr(plan_routes, "load_bars", boom)
    r = client.post("/api/plan/simulate", json=SIM, headers=HEADERS)
    assert r.status_code == 502
    assert "could not be fetched" in r.get_json()["error"]


def test_check_success_shape_without_connections_marks_skips(client):
    r = client.post("/api/plan/check", json={**CHK, "date": "2026-10-28"}, headers=HEADERS)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ticker"] == "AAPL" and body["side"] == "buy"
    assert len(body["fingerprint"]) == 16
    ids = [c["id"] for c in body["checks"]]
    assert ids == ["geometry", "rr", "dollar_risk", "position_size", "concentration_name",
                   "concentration_sector", "earnings", "fomc", "decision", "material_filings",
                   "todays_move"]
    assert body["summary"]["counts"]["SKIPPED"] >= 1 and body["summary"]["headline"]
    assert all(c["title"] for c in body["checks"])
    assert all(c["status"] in ("PASS", "WARN", "BLOCK", "SKIPPED") for c in body["checks"])
    assert body["context"] == {"research_cached": False, "earnings_known": False,
                               "holdings_connected": False, "account_value_source": "entered"}
    assert body["incomplete"] is True
    assert "Not connected" in next(c for c in body["checks"] if c["id"] == "concentration_name")["message"]


def test_check_uses_holdings_and_research_when_available(client, monkeypatch):
    monkeypatch.setattr(plan_routes, "holdings_read", lambda: {
        "account_value": 50000.0,
        "positions": {"MSFT": {"market_value": 14000.0, "sector": "Technology"}}})
    decision = {"action": "BUY", "new_money": "ELIGIBLE", "blockers": [],
                "evidence": {"status": "VALIDATED"}}
    res = SimpleNamespace(decision=decision, next_earnings_date="2027-03-01", sector="Technology")
    monkeypatch.setattr(plan_routes, "research_read", lambda symbol: res)
    r = client.post("/api/plan/check", json={**CHK, "shares": 20, "date": "2026-10-28"}, headers=HEADERS)
    body = r.get_json()
    assert body["context"]["holdings_connected"] is True
    assert body["context"]["research_cached"] is True
    assert body["context"]["earnings_known"] is True
    sector = next(c for c in body["checks"] if c["id"] == "concentration_sector")
    assert sector["status"] == "BLOCK"  # 14,000 + 2,000 = 32% of 50,000
    assert next(c for c in body["checks"] if c["id"] == "decision")["status"] == "PASS"


def test_check_reads_the_research_page_payload(client, monkeypatch):
    """The Research page caches a dict payload (app._analysis_result_cache), not
    an analysis object; a live run showed the check missing it ("No Research read
    is cached") right after Research had run."""
    payload = {"decision": {"action": "WATCHLIST", "new_money": "BLOCKED",
                            "blockers": [{"code": "LOW_REWARD_RISK", "text": "R/R below 1.50:1",
                                          "remedy": "R/R of at least 1.50:1"}],
                            "primary_blocker": {"code": "LOW_REWARD_RISK", "text": "R/R below 1.50:1",
                                                "remedy": "R/R of at least 1.50:1"},
                            "evidence": {"status": "NO_EDGE"}},
               "catalysts": {"earnings_date": "2026-11-20"}, "sector": "Consumer Defensive"}
    monkeypatch.setattr(plan_routes, "_TEAR_SHEET_READER", lambda symbol: payload)
    monkeypatch.setattr(plan_routes._research_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(plan_routes, "research_read", _REAL_RESEARCH_READ)
    r = client.post("/api/plan/check", json={**CHK, "shares": 20, "date": "2026-10-28"}, headers=HEADERS)
    body = r.get_json()
    assert body["context"]["research_cached"] is True
    assert body["context"]["earnings_known"] is True
    assert next(c for c in body["checks"] if c["id"] == "decision")["status"] == "WARN"


def test_app_registers_a_tear_sheet_reader():
    import app as webapp_app
    # By name: other suites reload the app module, which re-creates the function.
    assert getattr(plan_routes._TEAR_SHEET_READER, "__name__", "") == "_latest_tear_sheet"
    key = webapp_app._analysis_cache_key("ZZZ", "full", 25000.0)
    webapp_app._analysis_result_cache[key] = {"timestamp": __import__("time").time(),
                                              "payload": {"decision": {"action": "AVOID"}}}
    try:
        assert webapp_app._latest_tear_sheet("zzz")["decision"]["action"] == "AVOID"
        assert webapp_app._latest_tear_sheet("YYY") is None
    finally:
        webapp_app._analysis_result_cache.pop(key, None)


def test_check_fingerprint_matches_the_checker(client):
    from engine import plan_check
    body = client.post("/api/plan/check", json={**CHK, "date": "2026-10-28"}, headers=HEADERS).get_json()
    from datetime import date
    expected = plan_check.plan_fingerprint("AAPL", 10, 100.0, 95.0, 112.0, date(2026, 10, 28))
    assert body["fingerprint"] == expected


# ---------------------------------------------------------------------------
# material_filings wiring: the 8-K input reaches the check; SEC trouble never breaks it
# ---------------------------------------------------------------------------

def test_check_passes_recent_high_filings_into_the_check(client, monkeypatch):
    from datetime import date as _date, timedelta as _td
    filed = (_date.today() - _td(days=3)).isoformat()
    monkeypatch.setattr(plan_routes, "filings_read", lambda symbol: {
        "status": "ok", "message": "",
        "events": [{"date": filed, "max_severity": "HIGH",
                    "items": [{"code": "4.02", "title": "Non-Reliance", "severity": "HIGH"}]}]})
    body = client.post("/api/plan/check", json={**CHK, "date": "2026-10-28"}, headers=HEADERS).get_json()
    check = next(c for c in body["checks"] if c["id"] == "material_filings")
    assert check["status"] == "WARN"
    assert filed in check["message"]


def test_an_unreachable_sec_is_skipped_and_the_checklist_still_answers(client, monkeypatch):
    import events_routes

    def unreachable(symbol, today=None):
        raise OSError("no route to sec")

    monkeypatch.setattr(events_routes, "plan_filings", unreachable)
    monkeypatch.setattr(plan_routes, "filings_read", _REAL_FILINGS_READ)
    r = client.post("/api/plan/check", json={**CHK, "date": "2026-10-28"}, headers=HEADERS)
    assert r.status_code == 200
    check = next(c for c in r.get_json()["checks"] if c["id"] == "material_filings")
    assert check["status"] == "SKIPPED"


def test_filings_reader_never_raises(monkeypatch):
    import events_routes

    def boom(symbol, today=None):
        raise RuntimeError("bad")

    monkeypatch.setattr(events_routes, "plan_filings", boom)
    out = _REAL_FILINGS_READ("AAPL")
    assert out["status"] == "unavailable" and out["events"] == []
