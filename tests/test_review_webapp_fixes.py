"""Web-layer fixes from the September 2026 full review.

Offline (no network). Each test pins one defect found in the review:

  * a NaN volume on the last daily bar used to take the whole session quote
    block down (int(NaN) raised inside the try and returned None);
  * the in-memory job store was never pruned;
  * "as of" stamps were naive local time, which the browser parsed as UTC or
    browser-local depending on the machine;
  * the Client Mandate (risk_profile) was accepted and silently ignored;
  * a fast-preview job coalesced a later full-analysis click onto itself;
  * the analysis price and the session LTP could disagree with no explanation.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402

# Every state-changing request must carry the app's CSRF header.
QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


# ---------------------------------------------------------------------------
# Session quote — NaN volume
# ---------------------------------------------------------------------------
def _frame(volume_last):
    idx = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"])
    return pd.DataFrame({
        "Open": [100.0, 101.0, 102.0],
        "High": [101.0, 102.5, 103.0],
        "Low": [99.0, 100.5, 101.0],
        "Close": [100.5, 102.0, 102.5],
        "Volume": [1_000_000, 1_100_000, volume_last],
    }, index=idx)


def test_session_quote_survives_nan_volume():
    session = webapp._session_quote_from_history(_frame(float("nan")))
    assert session is not None, "a NaN volume must not drop the whole session block"
    assert session["ltp"] == 102.5
    assert session["open"] == 102.0
    assert session["volume"] is None


def test_session_quote_reports_real_volume_as_int():
    session = webapp._session_quote_from_history(_frame(2_345_678.0))
    assert session["volume"] == 2_345_678
    assert isinstance(session["volume"], int)


# ---------------------------------------------------------------------------
# Job store pruning
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_jobs():
    with webapp._jobs_lock:
        saved = dict(webapp._jobs)
        webapp._jobs.clear()
    yield
    with webapp._jobs_lock:
        webapp._jobs.clear()
        webapp._jobs.update(saved)


def test_prune_drops_finished_jobs_past_retention(clean_jobs):
    now = time.time()
    with webapp._jobs_lock:
        webapp._jobs["old"] = {"type": "analyze", "status": "complete",
                               "finished_at": now - webapp.JOB_RETENTION_SECONDS - 5}
        webapp._jobs["fresh"] = {"type": "analyze", "status": "complete", "finished_at": now}
        webapp._jobs["running"] = {"type": "analyze", "status": "running",
                                   "started_at": now - webapp.JOB_RETENTION_SECONDS - 500}
    removed = webapp._prune_jobs(now)
    assert removed == 1
    assert set(webapp._jobs) == {"fresh", "running"}, "running jobs are never pruned"


def test_prune_enforces_hard_cap_oldest_first(clean_jobs, monkeypatch):
    monkeypatch.setattr(webapp, "JOB_RETENTION_MAX", 3)
    now = time.time()
    with webapp._jobs_lock:
        for i in range(6):
            webapp._jobs[f"j{i}"] = {"type": "analyze", "status": "complete", "finished_at": now - (10 - i)}
    webapp._prune_jobs(now)
    assert len(webapp._jobs) == 3
    assert set(webapp._jobs) == {"j3", "j4", "j5"}


def test_update_job_stamps_finished_at(clean_jobs):
    with webapp._jobs_lock:
        webapp._jobs["x"] = {"type": "analyze", "status": "running"}
    webapp._update_job("x", status="complete", result={})
    assert webapp._jobs["x"]["finished_at"] > 0


# ---------------------------------------------------------------------------
# Timestamps and market normalisation
# ---------------------------------------------------------------------------
def test_now_iso_carries_a_utc_offset():
    stamp = webapp._now_iso()
    assert stamp.endswith("Z") or stamp[-6] in "+-", stamp


@pytest.mark.parametrize("raw,expected", [
    ("us", "US"), ("IN", "IN"), ("india", "IN"), ("nse", "IN"),
    ("XX", "US"), (None, "US"), ("", "US"),
])
def test_normalize_market(raw, expected):
    assert webapp._normalize_market(raw) == expected


def test_market_intel_endpoint_normalises_unknown_market(monkeypatch):
    calls = []

    def fake_intel(market, blocking=True):
        calls.append(market)
        return {"market": market, "movers_up": [], "movers_down": []}

    monkeypatch.setattr(webapp, "_market_intel", fake_intel)
    client = webapp.app.test_client()
    resp = client.get("/api/market-intel?market=ZZ&blocking=0")
    assert resp.status_code == 200
    assert calls == ["US"]


# ---------------------------------------------------------------------------
# Price reconciliation
# ---------------------------------------------------------------------------
def test_reconcile_prices_agrees_inside_tolerance():
    block = webapp._reconcile_prices(
        {"current_price": 100.0, "market_timestamp": "2026-09-03T00:00:00"},
        {"session": {"ltp": 100.1, "date": "2026-09-03"}},
    )
    assert block["discrepancy"] is False
    assert math.isclose(block["diff_pct"], 0.1, abs_tol=1e-6)
    assert block["analysis_bar"] == "2026-09-03"


def test_reconcile_prices_flags_a_real_gap_with_an_explicit_note():
    block = webapp._reconcile_prices(
        {"current_price": 100.0, "market_timestamp": "2026-09-02T00:00:00"},
        {"session": {"ltp": 103.0, "date": "2026-09-03"}},
    )
    assert block["discrepancy"] is True
    assert "100.00" in block["note"] and "103.00" in block["note"]
    assert "2026-09-02" in block["note"]


def test_reconcile_prices_handles_missing_session():
    block = webapp._reconcile_prices({"current_price": 100.0}, {"session": None})
    assert block["discrepancy"] is False
    assert block["session_ltp"] is None
    assert block["note"]


# ---------------------------------------------------------------------------
# Client mandate (risk_profile)
# ---------------------------------------------------------------------------
class _Rec:
    def __init__(self, ticker, allocation, entry):
        self.ticker = ticker
        self.allocation = allocation
        self.entry_price = entry
        self.shares = int(allocation // entry)
        self.position_pct = 0.0


def _fake_apply_allocation(rec, allocation, capital):
    rec.allocation = allocation
    rec.shares = int(allocation // rec.entry_price)
    rec.position_pct = allocation / capital * 100
    return allocation


def test_conservative_mandate_scales_funded_allocations_down():
    recs = [_Rec("A", 1000.0, 50.0), _Rec("B", 0.0, 20.0)]
    audit = webapp._apply_risk_profile(recs, "conservative", 10_000.0, _fake_apply_allocation)
    assert audit["multiplier"] == 0.7
    assert recs[0].allocation == 700.0 and recs[0].shares == 14
    assert recs[1].allocation == 0.0, "an unfunded pick stays unfunded"
    assert [a["ticker"] for a in audit["adjusted"]] == ["A"]


@pytest.mark.parametrize("profile", ["balanced", "aggressive"])
def test_non_conservative_mandates_never_add_capital(profile):
    recs = [_Rec("A", 1000.0, 50.0)]
    audit = webapp._apply_risk_profile(recs, profile, 10_000.0, _fake_apply_allocation)
    assert recs[0].allocation == 1000.0
    assert audit["adjusted"] == []
    assert audit["multiplier"] == 1.0


def test_stock_picks_rejects_unknown_risk_profile():
    client = webapp.app.test_client()
    resp = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json={"risk_profile": "yolo"})
    assert resp.status_code == 400
    assert "risk_profile" in resp.get_json()["error"]


def test_stock_picks_forwards_risk_profile_to_worker(monkeypatch, clean_jobs):
    started = []

    class _NoopThread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs)

        def start(self):
            return None

    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    client = webapp.app.test_client()
    resp = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json={"risk_profile": "Conservative"})
    assert resp.status_code == 202
    assert started[0]["args"][1]["risk_profile"] == "conservative"


# ---------------------------------------------------------------------------
# Analyze job coalescing respects the mode
# ---------------------------------------------------------------------------
def test_full_analysis_is_not_coalesced_onto_a_running_fast_preview(monkeypatch, clean_jobs):
    class _NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    client = webapp.app.test_client()
    fast = client.post("/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL", "fast_mode": True}).get_json()
    full = client.post("/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL", "fast_mode": False}).get_json()
    again_fast = client.post("/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL", "fast_mode": True}).get_json()
    assert full["job_id"] != fast["job_id"], "a full run must get its own job"
    assert again_fast["job_id"] == fast["job_id"] and again_fast.get("coalesced") is True


# ---------------------------------------------------------------------------
# [B3] Concurrent market scans
# ---------------------------------------------------------------------------
class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


def test_duplicate_scan_click_coalesces_onto_the_running_job(monkeypatch, clean_jobs):
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    client = webapp.app.test_client()
    body = {"market": "US", "scan_type": "standard", "capital": 2500}

    first = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json=body)
    second = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json=body)

    assert first.status_code == 202 and second.status_code == 202
    assert second.get_json()["job_id"] == first.get_json()["job_id"]
    assert second.get_json()["coalesced"] is True


def test_a_second_different_scan_is_refused_while_one_runs(monkeypatch, clean_jobs):
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    client = webapp.app.test_client()

    first = client.post("/api/stock-picks",
                        headers=QUANTA_HEADERS, json={"market": "US", "scan_type": "standard", "capital": 2500})
    assert first.status_code == 202

    other = client.post("/api/stock-picks",
                        headers=QUANTA_HEADERS, json={"market": "US", "scan_type": "quick", "capital": 2500})
    assert other.status_code == 409
    payload = other.get_json()
    assert payload["running_job_id"] == first.get_json()["job_id"]
    assert "already running" in payload["error"]


def test_a_new_scan_starts_once_the_previous_one_finished(monkeypatch, clean_jobs):
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    client = webapp.app.test_client()
    body = {"market": "US", "scan_type": "standard", "capital": 2500}

    first_id = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json=body).get_json()["job_id"]
    webapp._update_job(first_id, status="complete", progress=100)
    # [F07] The scan slot belongs to the WORKER, not to the job status, and is
    # released in `_run_stock_picks`'s `finally`. The stubbed thread above never
    # runs that body, so release it here — this test is about the admission
    # gate, not about the release plumbing.
    webapp._release_job_worker(first_id)

    second = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json=body)
    assert second.status_code == 202
    assert second.get_json()["job_id"] != first_id
    assert second.get_json().get("coalesced") is None


def test_safe_mode_retry_is_not_treated_as_a_duplicate(monkeypatch, clean_jobs):
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    monkeypatch.setattr(webapp, "MAX_CONCURRENT_SCANS", 2)
    client = webapp.app.test_client()
    body = {"market": "US", "scan_type": "standard", "capital": 2500}

    normal = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json=body).get_json()["job_id"]
    safe = client.post("/api/stock-picks", headers=QUANTA_HEADERS, json=dict(body, safe_mode=True))
    assert safe.status_code == 202
    assert safe.get_json()["job_id"] != normal
