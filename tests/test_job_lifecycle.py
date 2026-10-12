"""Cancelling or timing out a job must not create overlapping heavy work.

Audit finding F07.

  * `job_cancel` set the job's status to "error". The scan admission gate
    counted jobs with status == "running", so cancelling immediately freed a
    slot while the daemon thread kept scanning — a second scan was admitted on
    top of the first, doubling load on one rate-limited provider. Cancellation
    is an abandonment, not a kill, so the slot must stay held until the worker
    actually returns.
  * The worker had no way to notice it had been cancelled, so it ran every
    remaining stage regardless.
  * An analysis wall-clock timeout released `_analyze_lock` while the abandoned
    thread was still running against the SHARED, mutable analyzer in
    `_analysis_runtime`. The next request picked up that same object, so two
    live analyses mutated one analyzer.
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


@pytest.fixture(autouse=True)
def _clean_jobs():
    with webapp._jobs_lock:
        webapp._jobs.clear()
    yield
    with webapp._jobs_lock:
        webapp._jobs.clear()


def _register(job_id: str = "job-1", scan_key: str = "k") -> str:
    with webapp._jobs_lock:
        webapp._jobs[job_id] = {
            "type": "stock-picks",
            "status": "running",
            "progress": 0,
            "message": "",
            "result": None,
            "error": None,
            "scan_key": scan_key,
            "scan_type": "sp500",
            "started": webapp._now_iso(),
            "started_at": time.time(),
            "worker_active": True,
        }
    return job_id


# ---------------------------------------------------------------------------
# Worker ownership outlives the job's terminal status
# ---------------------------------------------------------------------------

def test_a_running_job_holds_a_worker_slot():
    _register()
    assert webapp._active_scan_workers() == ["job-1"]


def test_cancelling_does_not_release_the_worker_slot():
    """The scan is still running against the provider; the slot must stay held."""
    job_id = _register()
    with webapp._jobs_lock:
        webapp._jobs[job_id].update({"status": "error", "cancelled": True})

    assert webapp._active_scan_workers() == [job_id]


def test_the_slot_is_released_only_when_the_worker_returns():
    job_id = _register()
    with webapp._jobs_lock:
        webapp._jobs[job_id].update({"status": "error", "cancelled": True})

    webapp._release_job_worker(job_id)
    assert webapp._active_scan_workers() == []


def test_a_normally_completed_job_releases_its_slot():
    job_id = _register()
    webapp._update_job(job_id, status="complete", progress=100)
    webapp._release_job_worker(job_id)

    assert webapp._active_scan_workers() == []


def test_expiration_and_pruning_cannot_release_a_live_worker(monkeypatch):
    job_id = _register()
    with webapp._jobs_lock:
        webapp._jobs[job_id]["started_at"] = time.time() - webapp.SCAN_MAX_RUNTIME_SECONDS - 10
    webapp._expire_stale_jobs()
    assert webapp._job_is_cancelled(job_id)
    monkeypatch.setattr(webapp, "JOB_RETENTION_MAX", 0)
    webapp._prune_jobs(time.time() + webapp.JOB_RETENTION_SECONDS + 100)
    assert webapp._active_scan_workers() == [job_id]
    webapp._release_job_worker(job_id)
    assert webapp._prune_jobs(time.time() + webapp.JOB_RETENTION_SECONDS + 100) == 1


def test_releasing_an_unknown_job_is_harmless():
    webapp._release_job_worker("no-such-job")
    assert webapp._active_scan_workers() == []


# ---------------------------------------------------------------------------
# Cooperative cancellation
# ---------------------------------------------------------------------------

def test_worker_can_observe_that_it_was_cancelled():
    job_id = _register()
    assert webapp._job_is_cancelled(job_id) is False

    with webapp._jobs_lock:
        webapp._jobs[job_id].update({"status": "error", "cancelled": True})

    assert webapp._job_is_cancelled(job_id) is True


def test_a_pruned_job_reads_as_cancelled_so_its_worker_stops():
    """If the job record is gone, nothing can consume the result — stop early."""
    assert webapp._job_is_cancelled("vanished") is True


# ---------------------------------------------------------------------------
# An abandoned analysis must not share the analyzer with the next request
# ---------------------------------------------------------------------------

def test_timeout_retires_the_shared_analyzer_runtime():
    sentinel = object()
    webapp._analysis_runtime.update({
        "module": sentinel, "analyzer": sentinel,
        "api_client": sentinel, "config_key": "k",
    })

    webapp._retire_analysis_runtime()

    assert webapp._analysis_runtime["analyzer"] is None
    assert webapp._analysis_runtime["config_key"] is None


def test_retiring_an_already_empty_runtime_is_harmless():
    webapp._retire_analysis_runtime()
    webapp._retire_analysis_runtime()
    assert webapp._analysis_runtime["analyzer"] is None
