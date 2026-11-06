"""Model freeze (core/tracking/freeze.py): opt-in, enforced by this file once used.

``test_committed_freezes_are_intact`` is the guard: it does nothing until
``track_record/model_freeze.json`` exists, then fails while a frozen engine's
model code differs from the freeze before its end date.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from tracking import fingerprint as fpm
from tracking import freeze as fz

TODAY = date(2026, 11, 6)


def test_committed_freezes_are_intact():
    broken = fz.breaches()
    assert not broken, "\n".join(
        f"{b['engine']}'s scoring code is frozen until {b['until']} ({b['reason']}) and has changed "
        f"({b['frozen_fingerprint'][:12]} -> {b['current_fingerprint'][:12]}). Put the change into a "
        f"new shadow version, or end the freeze on purpose: "
        f"python -m tracking.freeze release {b['engine']} --reason \"...\" (from core/)" for b in broken)


def test_no_file_means_nothing_is_frozen(tmp_path):
    path = tmp_path / "freeze.json"
    assert fz.check(TODAY, path) == [] and fz.breaches(TODAY, path) == []


def test_a_freeze_records_the_current_model_fingerprint(tmp_path):
    path = tmp_path / "freeze.json"
    entry = fz.freeze("elite", 91, "test elite.research.v2", today=TODAY, path=path)
    assert entry == {"model_fingerprint": fpm.model_fingerprint("elite"), "since": "2026-11-06",
                     "until": "2027-02-05", "reason": "test elite.research.v2"}
    assert fz.check(TODAY, path)[0]["intact"] is True
    assert fz.breaches(TODAY, path) == []


def test_a_changed_model_breaches_only_while_the_freeze_lasts(tmp_path, monkeypatch):
    path = tmp_path / "freeze.json"
    fz.freeze("elite", 30, "test", today=TODAY, path=path)
    monkeypatch.setattr(fz, "model_fingerprint", lambda engine: "0" * 64)
    assert [b["engine"] for b in fz.breaches(TODAY, path)] == ["elite"]
    assert fz.breaches(date(2026, 12, 7), path) == []          # the freeze has ended


def test_release_is_recorded_with_its_reason(tmp_path):
    path = tmp_path / "freeze.json"
    fz.freeze("elite", 30, "test", today=TODAY, path=path)
    rec = fz.release("elite", "bug fix in the stop rule", today=date(2026, 11, 20), path=path)
    data = json.loads(path.read_text())
    assert data["frozen"] == {} and data["released"] == [rec]
    assert rec["released_on"] == "2026-11-20" and rec["release_reason"] == "bug fix in the stop rule"


def test_bad_requests_are_refused(tmp_path):
    path = tmp_path / "freeze.json"
    with pytest.raises(ValueError, match="unknown engine"):
        fz.freeze("nope", 30, "x", path=path)
    with pytest.raises(ValueError, match="reason"):
        fz.freeze("elite", 30, " ", path=path)
    with pytest.raises(ValueError, match="not frozen"):
        fz.release("elite", "x", path=path)


def test_cli_status_set_release(tmp_path, capsys):
    path = str(tmp_path / "freeze.json")
    assert fz.main(["status", "--path", path]) == 0 and "No engine is frozen" in capsys.readouterr().out
    assert fz.main(["set", "elite", "--days", "10", "--reason", "v2 test", "--path", path]) == 0
    assert fz.main(["status", "--path", path]) == 0 and "intact" in capsys.readouterr().out
    assert fz.main(["set", "elite", "--path", path]) == 2       # no reason
    assert fz.main(["release", "elite", "--reason", "done", "--path", path]) == 0
