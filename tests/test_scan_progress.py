"""A13: staged progress with counts and elapsed time, and separate data clocks.

The engine reports each stage (universe selection, per-name analysis with
done/total, book, Research check) through a callback; the worker turns that
into job fields and a message with counts and elapsed seconds. The result
names the daily-bar session, the provider quote time and the scan time
separately. Synthetic providers only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402


def test_the_engine_reports_each_stage_with_counts(monkeypatch):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {})
    seen = []
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng._progress_cb = lambda stage, done, total: seen.append((stage, done, total))
    eng.scan_market(scan_type="custom", custom_tickers=list(acc._NAMES))
    eng.get_top_recommendations(max_positions=5)
    stages = [s for s, _, _ in seen]
    for want in ("selecting", "analysing", "book", "research_check"):
        assert want in stages, (want, sorted(set(stages)))
    analysing = [(d, t) for s, d, t in seen if s == "analysing"]
    assert analysing[0] == (0, len(acc._NAMES)) and analysing[-1] == (len(acc._NAMES), len(acc._NAMES))
    assert all(b[0] >= a[0] for a, b in zip(analysing, analysing[1:])), "counts only move forward"


def test_a_broken_callback_never_breaks_the_scan(monkeypatch):
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng._progress_cb = lambda *a: 1 / 0
    eng._report_progress("analysing", 1, 2)       # must not raise


def _worker_with(monkeypatch, recs, calls):
    import test_scan_accounting as acc
    mod = acc._fake_engine_module(recs)
    base = mod.QuantaEliteTradingSystem

    class Eng(base):
        def scan_market(self, **kw):
            cb = getattr(self, "_progress_cb", None)
            for stage, done, total in calls:
                cb(stage, done, total)
                time.sleep(0.01)
            return list(recs)

    mod.QuantaEliteTradingSystem = Eng
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    snapshots = []
    real = webapp._update_job

    def spy(job_id, **kw):
        real(job_id, **kw)
        if "stage" in kw:
            snapshots.append(dict(webapp._jobs[job_id]))

    monkeypatch.setattr(webapp, "_update_job", spy)
    job = "a13-test"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "message": "",
                         "started_at": time.time(), "worker_active": True}
    params = dict(capital=100_000.0, market="US", scan_type="custom", custom_tickers=None,
                  risk_profile="balanced", max_positions=5, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job, params)
        return snapshots, webapp._jobs[job]
    finally:
        webapp._jobs.pop(job, None)


def test_the_job_carries_stage_counts_and_elapsed_time(monkeypatch):
    from test_elite_pro_review2_fixes import _pick
    snaps, job = _worker_with(monkeypatch, [_pick("SYNA", "Tech", 8.0, seed=1)],
                              [("selecting", 0, 0), ("analysing", 0, 8), ("analysing", 3, 8), ("analysing", 8, 8)])
    assert job["status"] == "complete", job.get("error")
    mid = next(s for s in snaps if s["stage"]["key"] == "analysing" and s["stage"]["done"] == 3)
    assert mid["stage"]["total"] == 8 and mid["stage"]["elapsed_s"] >= 0
    assert "3 of 8" in mid["message"] and "elapsed" in mid["message"]
    assert 20 <= mid["progress"] <= 80
    assert [s["progress"] for s in snaps] == sorted(s["progress"] for s in snaps)


def test_the_result_separates_bar_quote_and_scan_clocks(monkeypatch):
    from test_elite_pro_review2_fixes import _pick
    a, b = _pick("SYNA", "Tech", 8.0, seed=1), _pick("SYNB", "Health", 7.9, seed=2)
    a.market_timestamp, b.market_timestamp = "2026-10-02T00:00:00", "2026-10-01T00:00:00"
    a.quote_time, b.quote_time = "2026-10-02T20:00:00+00:00", ""
    _, job = _worker_with(monkeypatch, [a, b], [])
    dd = job["result"]["data_dates"]
    assert dd["bar_session"] == "2026-10-02" and dd["bar_sessions"] == {"2026-10-02": 1, "2026-10-01": 1}
    assert dd["quote_time"] == "2026-10-02T20:00:00+00:00"
    assert dd["scan_completed"] == job["result"]["timestamp"]


def test_the_quote_time_is_read_from_company_info():
    assert sp._quote_time_iso({"regularMarketTime": 1_759_435_200}) == "2025-10-02T20:00:00+00:00"
    assert sp._quote_time_iso({}) == "" and sp._quote_time_iso({"regularMarketTime": "x"}) == ""
