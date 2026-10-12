"""I2 and I3: names the corporate-event gate holds at Watch are published on every
exit of the book, counted as on watch, and diagnosed against the later gates
without any chance of funding.

The 2026-10-04 scan held CSCO, GOOGL, MSFT, GOOG and AMZN with an unavailable
check; the book then returned early with nothing funded and the five names never
reached the page ("On watch 0"). Synthetic names only.
"""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import corporate_events as ce  # noqa: E402

from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick  # noqa: E402

HELD = ["HA", "HB", "HC", "HD", "HE"]


def _unavailable(symbols, **_):
    return {s: ce._unavailable("", "contact_missing") for s in symbols}


def _book(monkeypatch, picks, checker=_unavailable, max_positions=5):
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    sysm._event_checker = checker
    sysm.scan_results = picks
    return sysm, sysm.get_top_recommendations(max_positions=max_positions)


def _five():
    return [_pick(t, f"S{i}", 8.0 - i * 0.1, seed=i + 1) for i, t in enumerate(HELD)]


# ── I2 ──────────────────────────────────────────────────────────────────────

def test_all_names_held_by_the_event_gate_are_published(monkeypatch):
    sysm, out = _book(monkeypatch, _five())
    assert [r.ticker for r in out] == HELD, "held names reach the published rows"
    for r in out:
        assert r.allocation == 0 and r.final_action == "WATCHLIST"
        assert r.decision and "EVENT_CHECK_UNAVAILABLE" in r.decision["blocking_codes"]
        assert r.decision["new_money"] != "ELIGIBLE"
    assert sysm._gate_stats["final_recommendations"] == 5 and sysm._gate_stats["funded"] == 0


def test_held_names_survive_the_cost_gate_exit(monkeypatch):
    clear = _pick("OK1", "S9", 8.5, seed=9)
    clear.transaction_cost_bps = 1000.0               # cost gate keeps nothing
    held = _pick("HX", "S8", 8.0, seed=8)

    def checker(symbols, **_):
        return {s: (ce.EventCheck("clear") if s == "OK1" else ce._unavailable("", "timeout")) for s in symbols}
    _sysm, out = _book(monkeypatch, [clear, held], checker)
    assert [r.ticker for r in out] == ["HX"]
    assert out[0].allocation == 0


def test_the_worker_counts_held_names_as_on_watch(monkeypatch):
    from test_scan_accounting import _run_worker
    _sysm, out = _book(monkeypatch, _five())
    res = _run_worker(monkeypatch, out, max_positions=5)
    assert res["funded_count"] == 0 and res["watch_count"] == 5
    assert res["accounting"]["watch"] == 5
    rows = res["recommendations"]
    assert sorted(r["ticker"] for r in rows) == sorted(HELD)
    assert all(not r["is_funded"] for r in rows)
    for r in rows:
        ev = r.get("event_check") or {}
        assert ev.get("code") == "EVENT_CHECK_UNAVAILABLE" and ev.get("fix")


def test_a_stale_held_list_never_leaks_into_the_next_book(monkeypatch):
    sysm, _ = _book(monkeypatch, _five())
    assert sysm.get_top_recommendations(max_positions=0) == []
    assert sysm._event_watch == []


def test_the_event_stage_reports_the_held_names(monkeypatch):
    sysm, _ = _book(monkeypatch, _five())
    event = {s["key"]: s for s in sysm.scan_accounting()["book_stages"]}["event"]
    assert event["entered"] == 5 and event["passed"] == 0
    assert event["held"] == {"count": 5, "tickers": HELD}
    assert event["avoided"]["count"] == 0


# ── I3: diagnostic only, never funded ───────────────────────────────────────

def test_otherwise_passing_held_names_would_pass_but_stay_unfunded(monkeypatch):
    sysm, out = _book(monkeypatch, _five())
    assert all(r.allocation == 0 for r in out) and sysm._gate_stats["funded"] == 0
    diag = sysm.event_held_diagnostics()
    assert [d["ticker"] for d in diag] == HELD
    for d in diag:
        assert d["diagnostic_only"] is True and d["funded"] is False
        assert d["verdict"] == "event_only", d
        assert d["also_fails"] == []
        assert d["held_by"]["code"] == "EVENT_CHECK_UNAVAILABLE" and d["held_by"]["reason"] == "contact_missing"
        assert set(d["not_assessed"]) == {"selection", "final_review"}


def test_a_held_name_that_also_fails_a_later_gate_says_which(monkeypatch):
    picks = _five()
    low_rr = _pick("HLOW", "S7", 7.7, stop=90.0, seed=7)          # (115-100)/(100-90) = 1.5
    sysm, _ = _book(monkeypatch, picks + [low_rr])
    by = {d["ticker"]: d for d in sysm.event_held_diagnostics()}
    d = by["HLOW"]
    assert d["verdict"] == "also_fails"
    (rr,) = [f for f in d["also_fails"] if f["gate"] == "risk_reward"]
    assert rr["now"] == pytest.approx(1.5) and rr["needed"] == pytest.approx(sp.TradingConfig.RISK_REWARD_MIN)
    assert by["HA"]["verdict"] == "event_only"


def test_the_diagnostic_changes_nothing_in_the_book(monkeypatch):
    import copy
    sysm, out = _book(monkeypatch, _five())
    fields = ("allocation", "shares", "position_pct", "risk_amount", "final_action", "final_action_reasons",
              "decision", "event_check", "overall_score", "risk_reward_ratio", "conviction", "risk_grade")
    snap = lambda: [copy.deepcopy({f: getattr(r, f, None) for f in fields}) for r in out]  # noqa: E731
    before_rows = snap()
    before_stats = copy.deepcopy(sysm._gate_stats)
    before_counts = copy.deepcopy(sysm._book_counts)
    sysm.event_held_diagnostics()
    assert snap() == before_rows
    assert sysm._gate_stats == before_stats and sysm._book_counts == before_counts


def test_the_worker_publishes_the_diagnostic(monkeypatch):
    from test_scan_accounting import _run_worker, _fake_engine_module
    sysm, out = _book(monkeypatch, _five())
    diag = sysm.event_held_diagnostics()
    mod = _fake_engine_module(out)
    mod.QuantaEliteTradingSystem.event_held_diagnostics = lambda self: diag
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    job = "i3-test"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "started_at": 0, "worker_active": True}
    params = dict(capital=2500.0, market="US", scan_type="custom", custom_tickers=None, risk_profile="balanced",
                  max_positions=5, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job, params)
        res = webapp._jobs[job]["result"]
    finally:
        webapp._jobs.pop(job, None)
    assert res["funded_count"] == 0
    assert [d["ticker"] for d in res["event_held_diagnostics"]] == HELD
