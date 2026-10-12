"""Audit F14: the Aggressive mandate is no longer offered.

It sized exactly like Balanced (the engine's risk budget is a hard ceiling), so
it was a choice that changed nothing. The owner's decision (Q6) is to remove it
from the UI. Old clients and saved forms that still send ``aggressive`` are
accepted and sized as Balanced, and the response says so; nothing is silently
reinterpreted.
"""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
from engine import thresholds as T  # noqa: E402

QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


@pytest.fixture
def started(monkeypatch):
    calls = []

    class _NoopThread:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def start(self):
            return None

    with webapp._jobs_lock:
        saved = dict(webapp._jobs)
        webapp._jobs.clear()
    monkeypatch.setattr(webapp.threading, "Thread", _NoopThread)
    yield calls
    with webapp._jobs_lock:
        webapp._jobs.clear()
        webapp._jobs.update(saved)


def test_config_offers_only_mandates_that_change_sizing():
    assert set(T.SIZING.MANDATE_SCALE) == {"conservative", "balanced"}
    assert T.SIZING.MANDATE_SCALE["conservative"] < T.SIZING.MANDATE_SCALE["balanced"]


def test_legacy_aggressive_request_is_sized_as_balanced_and_says_so(started):
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS,
                                         json={"risk_profile": "aggressive"})
    assert resp.status_code == 202
    params = started[0]["args"][1]
    assert params["risk_profile"] == T.SIZING.MANDATE_DEFAULT == "balanced"
    assert "aggressive" in params["risk_profile_notice"].lower()


def test_unknown_mandate_is_still_rejected():
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS,
                                         json={"risk_profile": "yolo"})
    assert resp.status_code == 400
    assert "conservative" in resp.get_json()["error"] and "aggressive" not in resp.get_json()["error"]


def test_no_aggressive_note_or_narrative_remains():
    assert "aggressive" not in webapp.RISK_PROFILE_NOTE
    assert "aggressive" not in webapp.RISK_PROFILE_NARRATIVE_TOLERANCE
