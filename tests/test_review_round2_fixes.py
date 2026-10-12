"""Second review round (September 2026). One test per defect fixed.

Offline (no network). Each case pins behaviour a reviewer found wrong:

  * a scan job that wedged held the single scan slot until the process
    restarted -- nothing aged it out and there was no way to cancel it;
  * a cancelled/abandoned job could be resurrected by its own late worker;
  * the client mandate could scale a position below one whole share, which
    silently removed it from the portfolio while reporting it as "trimmed";
  * the pre-filter's own wall-clock budget was reported as a provider rate
    limit, opening a process-wide cooldown no 429 had ever justified;
  * a 404 from SEC EDGAR (a company with no such filing) counted toward the
    circuit breaker, so five of them blacked out filing catalysts for all
    tickers.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
import SP_ELITE_PRO as sp  # noqa: E402
from sec_edgar_parser import SECEdgarParser  # noqa: E402

# Every state-changing request must carry the app's CSRF header.
QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


@pytest.fixture
def clean_jobs():
    with webapp._jobs_lock:
        saved = dict(webapp._jobs)
        webapp._jobs.clear()
    yield
    with webapp._jobs_lock:
        webapp._jobs.clear()
        webapp._jobs.update(saved)


# ---------------------------------------------------------------------------
# Wedged scans must not hold the concurrency slot forever
# ---------------------------------------------------------------------------
def test_stale_running_job_is_abandoned_past_the_runtime_budget(clean_jobs):
    now = time.time()
    with webapp._jobs_lock:
        webapp._jobs["wedged"] = {
            "type": "stock-picks", "status": "running", "progress": 40,
            "message": "Scanning…", "result": None, "error": None,
            "started_at": now - webapp.SCAN_MAX_RUNTIME_SECONDS - 1,
        }
    assert webapp._expire_stale_jobs(now) == 1
    job = webapp._jobs["wedged"]
    assert job["status"] == "error"
    assert job["error_detail"]["reason"] == "timeout"
    assert job["finished_at"] == now


def test_a_job_inside_its_budget_keeps_running(clean_jobs):
    now = time.time()
    with webapp._jobs_lock:
        webapp._jobs["healthy"] = {
            "type": "stock-picks", "status": "running",
            "started_at": now - webapp.SCAN_MAX_RUNTIME_SECONDS + 60,
        }
    assert webapp._expire_stale_jobs(now) == 0
    assert webapp._jobs["healthy"]["status"] == "running"


def test_timed_out_scan_blocks_replacement_until_worker_returns(monkeypatch, clean_jobs):
    """Abandonment changes the response, not the live worker's ownership."""
    class _NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    with webapp._jobs_lock:
        webapp._jobs["wedged"] = {
            "type": "stock-picks", "status": "running", "scan_key": "other",
            "scan_type": "full", "started": "old",
            "started_at": time.time() - webapp.SCAN_MAX_RUNTIME_SECONDS - 10,
            # The timed-out thread may still be calling providers.
            "worker_active": True,
        }
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS, json={"scan_type": "quick"})
    assert resp.status_code == 409
    webapp._release_job_worker("wedged")
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS, json={"scan_type": "quick"})
    assert resp.status_code == 202


def test_a_live_scan_still_blocks_and_offers_a_cancel_route(monkeypatch, clean_jobs):
    with webapp._jobs_lock:
        webapp._jobs["live"] = {
            "type": "stock-picks", "status": "running", "scan_key": "other",
            "scan_type": "full", "started": "now", "started_at": time.time() - 5,
            # [F07] Admission counts live WORKERS, not "running" statuses.
            "worker_active": True,
        }
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS, json={"scan_type": "quick"})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["running_job_id"] == "live"
    assert body["cancel_url"] == "/api/jobs/live/cancel"


# ---------------------------------------------------------------------------
# Cancelling, and the terminal-state guarantee
# ---------------------------------------------------------------------------
def test_cancel_marks_a_running_job_failed(clean_jobs):
    with webapp._jobs_lock:
        webapp._jobs["j"] = {"type": "stock-picks", "status": "running",
                             "started_at": time.time()}
    resp = webapp.app.test_client().post("/api/jobs/j/cancel", headers=QUANTA_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["cancelled"] is True
    assert webapp._jobs["j"]["status"] == "error"


def test_cancel_of_an_unknown_job_is_a_404(clean_jobs):
    assert webapp.app.test_client().post("/api/jobs/nope/cancel", headers=QUANTA_HEADERS).status_code == 404


def test_a_late_worker_cannot_resurrect_a_cancelled_job(clean_jobs):
    with webapp._jobs_lock:
        webapp._jobs["j"] = {"type": "stock-picks", "status": "running",
                             "result": None, "started_at": time.time()}
    webapp.app.test_client().post("/api/jobs/j/cancel", headers=QUANTA_HEADERS)
    # The daemon thread keeps going and eventually reports success.
    webapp._update_job("j", status="complete", progress=100, result={"picks": []})
    assert webapp._jobs["j"]["status"] == "error", "terminal is terminal"
    assert webapp._jobs["j"]["result"] is None


# ---------------------------------------------------------------------------
# A mandate that sizes a position out of existence must say so
# ---------------------------------------------------------------------------
class _Rec:
    def __init__(self, ticker, allocation, entry):
        self.ticker = ticker
        self.allocation = allocation
        self.entry_price = entry
        self.shares = 0
        self.position_pct = 0.0


def test_mandate_scaling_below_one_share_is_reported_as_dropped():
    # $100 at $90/share is one share; scaled to $70 it is none at all.
    recs = [_Rec("EXPENSIVE", 100.0, 90.0)]
    audit = webapp._apply_risk_profile(
        recs, "conservative", 10_000.0, sp.apply_allocation)
    assert recs[0].allocation == 0.0
    assert getattr(recs[0], "mandate_unfunded", False) is True
    assert recs[0].mandate_unfunded_reason
    assert [d["ticker"] for d in audit["dropped"]] == ["EXPENSIVE"]
    assert audit["adjusted"][0]["dropped"] is True


def test_a_real_trim_is_not_reported_as_dropped():
    recs = [_Rec("CHEAP", 1000.0, 50.0)]
    audit = webapp._apply_risk_profile(
        recs, "conservative", 10_000.0, sp.apply_allocation)
    assert recs[0].allocation == 700.0
    assert audit["dropped"] == []
    assert audit["adjusted"][0]["dropped"] is False
    assert not getattr(recs[0], "mandate_unfunded", False)


# ---------------------------------------------------------------------------
# Slowness is not a rate limit
# ---------------------------------------------------------------------------
def test_note_slow_records_telemetry_without_opening_a_cooldown():
    tracker = sp.ProviderRateLimitTracker()
    tracker.note_slow()
    snapshot = tracker.snapshot()
    assert snapshot["slow_responses"] == 1
    assert snapshot["rate_limited"] is False
    assert snapshot["rate_limit_hits"] == 0
    assert tracker.in_cooldown() is False


def test_note_rate_limit_still_opens_a_cooldown():
    tracker = sp.ProviderRateLimitTracker()
    tracker.note_rate_limit(30)
    assert tracker.in_cooldown() is True
    assert tracker.snapshot()["rate_limit_hits"] == 1


# ---------------------------------------------------------------------------
# SEC EDGAR: a 404 is an answer, not an outage
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return {}


class _Session:
    def __init__(self, status):
        self._status = status

    def get(self, *args, **kwargs):
        return _Resp(self._status)


@pytest.fixture
def sec_circuit_reset():
    SECEdgarParser._note_success()
    SECEdgarParser._circuit_open_until = 0.0
    yield
    SECEdgarParser._note_success()
    SECEdgarParser._circuit_open_until = 0.0


@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_sec_document_does_not_trip_the_circuit(monkeypatch, sec_circuit_reset, status):
    monkeypatch.setattr(SECEdgarParser, "_get_session", classmethod(lambda cls: _Session(status)))
    monkeypatch.setattr(SECEdgarParser, "_rate_limit", classmethod(lambda cls: None))
    for _ in range(SECEdgarParser.CIRCUIT_TRIP_FAILURES + 2):
        assert SECEdgarParser._get_json("https://example.test/x.json", host="www.sec.gov") is None
    assert SECEdgarParser._circuit_is_open() is False, "a company with no filings is not an outage"


def test_repeated_server_errors_still_trip_the_circuit(monkeypatch, sec_circuit_reset):
    monkeypatch.setattr(SECEdgarParser, "_get_session", classmethod(lambda cls: _Session(503)))
    monkeypatch.setattr(SECEdgarParser, "_rate_limit", classmethod(lambda cls: None))
    for _ in range(SECEdgarParser.CIRCUIT_TRIP_FAILURES):
        SECEdgarParser._get_json("https://example.test/x.json", host="www.sec.gov")
    assert SECEdgarParser._circuit_is_open() is True
