"""Position sizing must name the account it sized against.

Audit section 15 (Backend Problems, "Account context"): `_run_analysis`
submitted `analyzer.analyze` with no `account_size`, so the deep dive silently
fell back to the analyzer's 100,000 default while the portfolio scanner used
its own configurable capital. The same ticker could therefore be sized against
two different accounts in one session with nothing on screen saying so.

F02 made the exposure check consume `account_size`; this closes the other half
by letting the request supply it and labelling the result when it does not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402

# Every state-changing request must carry the app's CSRF header.
QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


@pytest.fixture(autouse=True)
def _clean_jobs():
    with webapp._jobs_lock:
        webapp._jobs.clear()
    yield
    with webapp._jobs_lock:
        webapp._jobs.clear()


class _NoopThread:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def start(self):
        return None


# ---------------------------------------------------------------------------
# Parsing and validation of the requested account size
# ---------------------------------------------------------------------------

def test_a_supplied_account_size_is_used():
    assert webapp._resolve_account_size({"account_size": 250000}) == 250_000.0


def test_a_missing_account_size_falls_back_to_the_documented_default():
    assert webapp._resolve_account_size({}) == webapp.DEFAULT_ACCOUNT_SIZE


def test_a_nonsense_account_size_falls_back_rather_than_sizing_on_garbage():
    for bad in ("abc", -5, 0, None, float("inf")):
        assert webapp._resolve_account_size({"account_size": bad}) == (
            webapp.DEFAULT_ACCOUNT_SIZE
        )


def test_an_absurd_account_size_is_capped():
    got = webapp._resolve_account_size({"account_size": 1e15})
    assert got == webapp.MAX_ACCOUNT_SIZE


# ---------------------------------------------------------------------------
# "Assumed" must mean "the placeholder was substituted" -- nothing else
# ---------------------------------------------------------------------------
# The flag drives the on-screen note that tells the user whether the share
# counts came from their mandate or from a hypothetical account. Reporting a
# declared account as assumed is as misleading as the reverse.

def test_a_declared_account_is_never_reported_as_assumed():
    # Including the two cases the original call-site comparison got wrong: an
    # account that happens to equal the default, and a size arriving as a JSON
    # string (what an un-coerced form field serialises to).
    for declared in (250000, 100000, 100000.0, "100000", "250000", 100):
        size, assumed = webapp._resolve_account_context({"account_size": declared})
        assert assumed is False, f"{declared!r} was declared but reported as assumed"
        assert size == float(declared)


def test_a_missing_or_unusable_account_size_is_reported_as_assumed():
    for missing in ({}, {"account_size": None}, {"account_size": "abc"},
                    {"account_size": 0}, {"account_size": -5},
                    {"account_size": 50}, {"account_size": float("nan")}):
        size, assumed = webapp._resolve_account_context(missing)
        assert assumed is True, f"{missing!r} should fall back to the placeholder"
        assert size == webapp.DEFAULT_ACCOUNT_SIZE


def test_the_capped_absurd_size_still_counts_as_declared():
    size, assumed = webapp._resolve_account_context({"account_size": 1e15})
    assert (size, assumed) == (webapp.MAX_ACCOUNT_SIZE, False)


# ---------------------------------------------------------------------------
# The account reaches the worker, and the job records which one it was
# ---------------------------------------------------------------------------

def test_the_request_account_size_is_passed_to_the_worker(monkeypatch):
    captured = {}

    class _Capture(_NoopThread):
        def __init__(self, *args, **kwargs):
            captured.update(kwargs.get("kwargs") or {})

    monkeypatch.setattr(webapp.threading, "Thread", _Capture)
    webapp.app.test_client().post(
        "/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL", "account_size": 50000}
    )

    assert captured["account_size"] == 50_000.0


def test_the_job_records_the_account_it_was_sized_against(monkeypatch):
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    resp = webapp.app.test_client().post(
        "/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL", "account_size": 50000}
    )
    job_id = resp.get_json()["job_id"]

    with webapp._jobs_lock:
        assert webapp._jobs[job_id]["account_size"] == 50_000.0


def test_an_explicit_account_is_not_flagged_as_assumed(monkeypatch):
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    resp = webapp.app.test_client().post(
        "/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL", "account_size": 50000}
    )

    assert resp.get_json()["account_size_assumed"] is False


def test_an_omitted_account_is_flagged_as_assumed(monkeypatch):
    """The user must be able to tell a real mandate from a placeholder."""
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    resp = webapp.app.test_client().post("/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "AAPL"})

    body = resp.get_json()
    assert body["account_size_assumed"] is True
    assert body["account_size"] == webapp.DEFAULT_ACCOUNT_SIZE


# ---------------------------------------------------------------------------
# Cache identity includes the account: sizing differs between accounts
# ---------------------------------------------------------------------------

def test_results_sized_for_different_accounts_do_not_share_a_cache_entry():
    with webapp._analysis_result_lock:
        webapp._analysis_result_cache.clear()
    try:
        webapp._set_cached_analysis("AAPL", {"n": 1}, mode="full",
                                    account_size=100_000.0)
        assert webapp._get_cached_analysis("AAPL", mode="full",
                                           account_size=250_000.0) is None
        assert webapp._get_cached_analysis("AAPL", mode="full",
                                           account_size=100_000.0)["n"] == 1
    finally:
        with webapp._analysis_result_lock:
            webapp._analysis_result_cache.clear()
