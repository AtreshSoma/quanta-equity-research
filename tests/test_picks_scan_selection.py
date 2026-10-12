"""Picks revamp, stage 1: how a scan chose its names is stated, and stated early.

- An unknown scan type is refused instead of silently running as Standard
  (a page newer than its server asked for Momentum Leaders and got 250 names).
- Past PREFILTER_MAX_TICKERS the liquidity download reads an even spread,
  never an alphabetical prefix, and the record says so.
- The record carries how many names passed the floors and the ranking rule.
- The engine reports the kept universe before analysis starts.
Synthetic providers only.
"""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
import SP_ELITE_PRO as sp  # noqa: E402
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


@pytest.mark.parametrize("market", ["US", "INDIA"])
def test_an_unknown_scan_type_is_refused_not_run_as_standard(started, market):
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS,
                                         json={"scan_type": "focused_tomorrow", "market": market})
    assert resp.status_code == 400
    assert "Unknown scan type" in resp.get_json()["error"] and "Restart" in resp.get_json()["error"]
    assert started == []


def test_momentum_leaders_runs_as_itself(started):
    resp = webapp.app.test_client().post("/api/stock-picks", headers=QUANTA_HEADERS,
                                         json={"scan_type": "focused_momentum"})
    assert resp.status_code == 202
    assert started[0]["args"][1]["scan_type"] == "focused_momentum"


@pytest.fixture
def system(monkeypatch):
    monkeypatch.setattr(sp.TradingConfig, "PREFILTER_TIMEOUT_SECONDS", 60.0)
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:frame")
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj._bulk_timed_out = False
    obj._universe_selection = {"method": None}
    return obj, [f"T{i:03d}" for i in range(300)]


def test_the_ranked_record_names_the_rule_and_the_names_that_passed_the_floors(system):
    obj, tickers = system
    out = obj.smart_prefilter(tickers, max_candidates=10)
    sel = obj._universe_selection
    assert len(out) == 10 and sel["method"] == "liquidity_ranked"
    assert sel["rank_by"] == "20-day average daily traded value"
    assert sel["passed_floors"] >= 10 and "prefilter_sampled" not in sel


def test_past_the_download_limit_an_even_spread_is_read_not_a_prefix(system, monkeypatch):
    obj, tickers = system
    monkeypatch.setattr(T.UNIVERSE, "PREFILTER_MAX_TICKERS", 50)
    out = obj.smart_prefilter(tickers, max_candidates=10)
    sel = obj._universe_selection
    assert sel["prefilter_sampled"] == 50 and sel["candidates"] == 300
    assert set(out) <= set(obj._even_sample(tickers, 50))
    assert not set(out) <= set(tickers[:50]), "an alphabetical prefix would hold every name"


def test_the_engine_reports_the_kept_universe_before_analysis(monkeypatch):
    import test_scan_accounting as acc
    acc._install(monkeypatch)
    seen = []
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng._progress_cb = lambda stage, done, total: seen.append((stage, done, total))
    eng.scan_market(scan_type="custom", custom_tickers=list(acc._NAMES))
    n = len(acc._NAMES)
    selecting = [(d, t) for s, d, t in seen if s == "selecting"]
    assert selecting[-1] == (n, n), selecting
    first_analysis = next(i for i, (s, _, _) in enumerate(seen) if s == "analysing")
    assert max(i for i, (s, _, _) in enumerate(seen) if s == "selecting") < first_analysis
    assert eng._universe_selection["selected"] == n


def test_the_selecting_stage_carries_the_selection_to_the_page():
    stages = webapp._terminal_stage("complete", {
        "selecting": {"done": 150, "total": 503, "selection": {"method": "liquidity_ranked", "selected": 150}},
        "analysing": {"done": 150, "total": 150}})["stages"]
    assert stages[0]["selection"]["selected"] == 150
    assert "selection" not in stages[1]


def test_a_throttled_scan_never_reads_n_of_n_when_names_were_not_reached(monkeypatch):
    """S1-13: the final progress call reported (total, total) even when the
    provider left names unreached; it now reports only the names reached."""
    import threading
    import time
    from test_elite_pro_review2_fixes import _bare_system
    monkeypatch.setattr(sp.TradingConfig, "RATE_LIMIT_COOLDOWN_SECONDS", 0.0)
    sysm = _bare_system(25_000.0, regime="BULL")
    sysm.failed_tickers = []
    sysm._chase_alerts_lock = threading.Lock()
    sysm._estimate_current_drawdown = lambda *a, **k: 0.0
    seen = []
    sysm._progress_cb = lambda stage, done, total: seen.append((stage, done, total))
    calls = {"n": 0}
    lock = threading.Lock()

    def fake_analyze(ticker, research_mode=False):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        time.sleep(0.005)               # keep the pool from draining before the circuit trips
        if n > 20:                      # the provider never recovers
            sysm._record_scan_error(ticker, Exception("YFRateLimitError: Too Many Requests"))
            return None
        a = sp.StockAnalysis(ticker=ticker, company=ticker, sector="S", industry="I")
        a.overall_score, a.signal = 6.0, "BUY"
        return a

    sysm.analyze_stock = fake_analyze
    tickers = [f"T{i:03d}" for i in range(200)]
    sysm.scan_market(scan_type="custom", custom_tickers=tickers)
    unreached = {t for t in sysm._scan_not_reached if t not in sysm._outcomes}
    assert unreached, "the fake provider leaves names unreached"
    done, total = [(d, t) for s, d, t in seen if s == "analysing"][-1]
    assert total == 200 and done == 200 - len(unreached) < total


def test_momentum_screens_every_name_before_the_cap(system, monkeypatch):
    """Decision (a): the uptrend screen reads closes only, so it runs on every
    name past the floors, and the cap keeps the most liquid of those that pass."""
    obj, tickers = system
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:trend_frame")
    out = obj.smart_prefilter(tickers, max_candidates=10, screen_style="momentum")
    evens = [t for i, t in enumerate(tickers) if i % 2 == 0]
    assert out == list(reversed(evens))[:10], "only uptrend names, most liquid first"
    sel = obj._universe_selection
    assert sel["screened_before_cap"] == "momentum"
    assert sel["passed_floors"] == 300 and sel["passed_screen"] == 150


def test_when_no_name_passes_the_screen_the_scan_is_empty_not_a_fallback(system, monkeypatch):
    obj, tickers = system                       # the flat 40-session frame fails the screen
    assert obj._select_broad_universe(tickers, 10, screen_style="momentum") == []
    assert obj._universe_selection["passed_screen"] == 0


def test_a_timed_out_screen_download_falls_back_and_says_the_screen_runs_after_the_cap(system, monkeypatch):
    obj, tickers = system
    monkeypatch.setattr(sp.TradingConfig, "PREFILTER_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:slow")
    out = obj.smart_prefilter(tickers, max_candidates=10, screen_style="momentum")
    assert out == obj._even_sample(tickers, 10)
    assert "runs after the cap" in obj._universe_selection["cause"]


def test_small_cap_has_its_own_floors_and_other_scans_keep_the_default(system):
    obj, _ = system
    obj.current_scan_type = "small_cap"
    own = T.UNIVERSE.SCAN_FLOORS["small_cap"]
    assert [obj._us_floor(k) for k in ("MIN_PRICE", "MIN_AVG_VOLUME", "MIN_DAILY_DOLLAR_VOLUME")] == [
        own["MIN_PRICE"], own["MIN_AVG_VOLUME"], own["MIN_DAILY_DOLLAR_VOLUME"]] == [5.0, 250_000, 5_000_000.0]
    obj.current_scan_type = "standard"
    assert obj._us_floor("MIN_AVG_VOLUME") == T.UNIVERSE.MIN_AVG_VOLUME
