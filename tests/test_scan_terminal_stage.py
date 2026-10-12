"""I4: the progress stages finish on every terminal state.

After the 2026-10-04 scan completed with nothing funded, the panel still read
"Applying the book rules: 0 of 5" as the current stage and "Running the Research
check" as pending: no terminal update ever set a stage. Complete, failed and
cancelled now each publish a terminal stage record in which every stage is done
(with its final counts), failed (with why), or skipped with a reason (P1). Synthetic names only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from _paths import ensure_paths

ensure_paths()

from test_scan_progress import _worker_with  # noqa: E402

BOOK_CALLS = [("selecting", 0, 0), ("analysing", 0, 8), ("analysing", 8, 8), ("book", 0, 5), ("book", 5, 5)]


def _states(stage):
    return {s["key"]: s for s in stage["stages"]}


def test_complete_with_nothing_funded_finishes_every_stage(monkeypatch):
    from test_scan_accounting import _watch
    _snaps, job = _worker_with(monkeypatch, [_watch("W1", 1)], BOOK_CALLS)
    assert job["status"] == "complete"
    st = job["stage"]
    assert st["key"] == "complete" and st["terminal"] is True
    by = _states(st)
    assert [s["key"] for s in st["stages"]] == ["selecting", "analysing", "book", "research_check"]
    assert all(s["state"] in ("done", "skipped") for s in st["stages"]), st
    assert by["book"]["state"] == "done"
    assert "0 Buy or Accumulate" in by["book"]["note"] and "1 Watchlist" in by["book"]["note"]
    assert by["research_check"]["state"] == "skipped"
    assert by["research_check"]["note"].startswith("skipped: ")
    assert not any(s["state"] in ("current", "running") for s in st["stages"])


def test_a_research_check_that_ran_is_done_with_its_counts(monkeypatch):
    from test_elite_pro_review2_fixes import _pick
    calls = BOOK_CALLS + [("research_check", 0, 1), ("research_check", 1, 1)]
    _snaps, job = _worker_with(monkeypatch, [_pick("SYNA", "Tech", 8.0, seed=1)], calls)
    rc = _states(job["stage"])["research_check"]
    assert rc["state"] == "done" and rc["done"] == 1 and rc["total"] == 1
    for s in job["stage"]["stages"]:
        assert s["state"] in ("done", "skipped", "failed"), s
        if s["state"] == "done" and s["total"]:
            assert s["done"] == s["total"], s


def test_a_failed_scan_stops_the_current_stage_and_skips_the_rest(monkeypatch):
    import test_scan_accounting as acc
    mod = acc._fake_engine_module([])
    base = mod.QuantaEliteTradingSystem

    class Boom(base):
        def scan_market(self, **kw):
            self._progress_cb("selecting", 0, 0)
            self._progress_cb("analysing", 2, 8)
            raise RuntimeError("synthetic failure")

    mod.QuantaEliteTradingSystem = Boom
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    job = "i4-fail"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "message": "",
                         "started_at": time.time(), "worker_active": True}
    params = dict(capital=2500.0, market="US", scan_type="custom", custom_tickers=None,
                  risk_profile="balanced", max_positions=5, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job, params)
        st = webapp._jobs[job]["stage"]
    finally:
        webapp._jobs.pop(job, None)
    assert st["key"] == "failed" and st["terminal"] is True
    by = _states(st)
    assert by["selecting"]["state"] == "done"
    assert by["analysing"]["state"] == "failed" and by["analysing"]["done"] == 2
    assert by["analysing"]["note"] == "failed: the scan failed at 2 of 8"
    assert by["book"]["state"] == "skipped" and by["book"]["note"] == "skipped: the scan failed"


def test_a_cancelled_scan_publishes_a_terminal_stage(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    job = "i4-cancel"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 40, "message": "",
                         "started_at": time.time(), "worker_active": True,
                         "stage": {"key": "analysing", "label": "Analysing names", "done": 3, "total": 8},
                         "stage_log": {"selecting": {"done": 0, "total": 0},
                                       "analysing": {"done": 3, "total": 8}}}
    try:
        r = webapp.app.test_client().post(f"/api/jobs/{job}/cancel",
                                          headers={"X-QUANTA-Request": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        st = webapp._jobs[job]["stage"]
    finally:
        webapp._jobs.pop(job, None)
    assert st["key"] == "cancelled" and st["terminal"] is True
    by = _states(st)
    assert by["analysing"]["state"] == "failed"
    assert by["analysing"]["note"] == "failed: the scan was cancelled at 3 of 8"
    assert by["research_check"]["note"] == "skipped: the scan was cancelled"

