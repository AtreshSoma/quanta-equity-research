"""GET /api/events: loopback guard, input validation, the answer shape, not-covered, the SEC-unavailable
502, caching, and never a traceback. The EDGAR client and the daily bars are replaced; no network."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from flask import Flask

WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

import events_routes  # noqa: E402

TODAY = date.today()
CIK = 320193


class FakeEdgar:
    def __init__(self, res):
        self.res = res
        self.calls = 0

    def recent_filings(self, symbol, since, forms=None):
        self.calls += 1
        return self.res


def _bars():
    idx = pd.bdate_range(TODAY - timedelta(days=5 * 365 + 30), TODAY)
    rng = np.random.default_rng(11)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(idx))))
    return pd.DataFrame({"Close": close}, index=idx)


def _row(days_ago, items, n):
    filed = TODAY - timedelta(days=days_ago)
    return {"form": "8-K", "filed": filed.isoformat(),
            "accepted": f"{filed.isoformat()}T20:05:00.000Z", "items": items,
            "accession": f"0000320193-26-{n:06d}", "url": None}


@pytest.fixture
def ctx(monkeypatch):
    events_routes.clear_cache()
    state = {"edgar": FakeEdgar({"status": "ok", "cik": CIK, "filings": [
        _row(2, ["4.02", "9.01"], 1),
        _row(40, ["2.02", "7.01", "9.01"], 2),
        _row(400, ["5.02"], 3),
    ]})}
    monkeypatch.setattr(events_routes, "_edgar", lambda: state["edgar"])
    monkeypatch.setattr(events_routes, "_bars", lambda symbol: _bars())
    app = Flask(__name__)
    app.register_blueprint(events_routes.bp)
    yield {"client": app.test_client(), "state": state, "app": app}
    events_routes.clear_cache()


def _get(ctx, qs):
    return ctx["client"].get(f"/api/events?{qs}")


# ---------------------------------------------------------------------------
# Guard and validation
# ---------------------------------------------------------------------------

def test_non_loopback_host_is_refused(ctx):
    r = ctx["client"].get("/api/events?symbol=AAPL", environ_overrides={"HTTP_HOST": "evil.example"})
    assert r.status_code == 403
    assert "loopback" in r.get_json()["error"]


@pytest.mark.parametrize("qs,fragment", [
    ("", "symbol is required"),
    ("symbol=", "symbol is required"),
    ("symbol=A%24B", "unsupported characters"),
    ("symbol=AAPL&days=0", "between 1 and 365"),
    ("symbol=AAPL&days=366", "between 1 and 365"),
    ("symbol=AAPL&days=abc", "whole number"),
])
def test_bad_input_is_a_400_with_a_plain_message(ctx, qs, fragment):
    r = _get(ctx, qs)
    assert r.status_code == 400
    assert fragment in r.get_json()["error"]


# ---------------------------------------------------------------------------
# Answer shape and filtering
# ---------------------------------------------------------------------------

def test_success_shape_lists_recent_newest_first_with_study(ctx):
    r = _get(ctx, "symbol=aapl&days=90")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ticker"] == "AAPL" and body["covered"] is True and body["status"] == "ok"
    assert body["days"] == 90 and body["available"] is True
    assert [e["date"] for e in body["recent"]] == [
        (TODAY - timedelta(days=2)).isoformat(), (TODAY - timedelta(days=40)).isoformat()]
    first = body["recent"][0]
    assert first["primary_item"] == "4.02" and first["max_severity"] == "HIGH"
    assert first["items"][0]["title"].startswith("Non-Reliance")
    assert first["url"] is None or first["url"].startswith("https://www.sec.gov/")
    assert "study" in body and body["study_status"] == "ok"
    assert set(body["study"]["items"]) <= {"4.02", "2.02", "5.02"}
    assert any("not a forecast" in n for n in body["notes"])
    assert body["as_of"] == TODAY.isoformat()


def test_days_filters_the_recent_list(ctx):
    body = _get(ctx, "symbol=AAPL&days=30").get_json()
    assert [e["primary_item"] for e in body["recent"]] == ["4.02"]


def test_default_days_is_ninety(ctx):
    assert _get(ctx, "symbol=AAPL").get_json()["days"] == 90


def test_no_filings_in_the_window_is_ok_with_an_empty_list(ctx):
    ctx["state"]["edgar"] = FakeEdgar({"status": "ok", "cik": CIK, "filings": []})
    body = _get(ctx, "symbol=MSFT&days=90").get_json()
    assert body["status"] == "ok" and body["recent"] == []


# ---------------------------------------------------------------------------
# Not covered, unavailable, and caching
# ---------------------------------------------------------------------------

def test_india_is_a_clear_not_covered_answer_with_no_sec_call(ctx):
    r = _get(ctx, "symbol=RELIANCE.NS")
    assert r.status_code == 200
    body = r.get_json()
    assert body["covered"] is False and body["status"] == "not_covered"
    assert "SEC filings exist only for US-registered issuers" in body["message"]
    assert body["recent"] == []
    assert ctx["state"]["edgar"].calls == 0


def test_sec_unavailable_is_a_502_that_says_so_and_is_not_cached(ctx):
    ctx["state"]["edgar"] = FakeEdgar({"status": "error", "reason": "rate_limited", "filings": []})
    r = _get(ctx, "symbol=AAPL")
    assert r.status_code == 502
    body = r.get_json()
    assert body["available"] is False and body["reason"] == "rate_limited"
    assert "rate-limited" in body["error"]
    # A failed read is not cached: the retry reaches SEC again.
    ctx["state"]["edgar"] = FakeEdgar({"status": "ok", "cik": CIK, "filings": []})
    assert _get(ctx, "symbol=AAPL").status_code == 200


def test_results_are_cached_per_symbol(ctx):
    first = _get(ctx, "symbol=AAPL&days=90").get_json()
    second = _get(ctx, "symbol=AAPL&days=30").get_json()
    assert first["cached"] is False and second["cached"] is True
    assert ctx["state"]["edgar"].calls == 1


def test_a_price_failure_keeps_the_filings_and_is_not_cached(ctx, monkeypatch):
    def boom(symbol):
        raise RuntimeError("yahoo is down")

    monkeypatch.setattr(events_routes, "_bars", boom)
    r = _get(ctx, "symbol=AAPL&days=90")
    assert r.status_code == 200
    body = r.get_json()
    assert body["study_status"] == "unavailable"
    assert body["recent"] and body["study"] == {}
    assert "RuntimeError" in body["study_message"]
    assert _get(ctx, "symbol=AAPL").get_json()["cached"] is False


def test_an_unexpected_failure_is_a_plain_502_never_a_traceback(ctx, monkeypatch):
    def explode(*a, **k):
        raise KeyError("internal detail that must not leak")

    monkeypatch.setattr(events_routes._es, "build_report", explode)
    r = _get(ctx, "symbol=AAPL")
    assert r.status_code == 502
    text = r.get_data(as_text=True)
    assert "Traceback" not in text and "internal detail" not in text
    assert r.get_json()["available"] is False


def test_responses_are_not_stored_by_the_browser(ctx):
    r = _get(ctx, "symbol=AAPL")
    assert r.headers["Cache-Control"] == "no-store, max-age=0"


# ---------------------------------------------------------------------------
# Plan Check's 30-day view
# ---------------------------------------------------------------------------

def test_plan_filings_reads_only_the_last_thirty_days(ctx):
    out = events_routes.plan_filings("AAPL", TODAY)
    assert out["status"] == "ok"
    assert [e["primary_item"] for e in out["events"]] == ["4.02"]


def test_plan_filings_india_is_not_covered(ctx):
    out = events_routes.plan_filings("TCS.BO", TODAY)
    assert out["status"] == "not_covered" and out["events"] == []
    assert ctx["state"]["edgar"].calls == 0
