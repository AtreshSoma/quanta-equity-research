"""tracking.score_snapshots: one scored cross-section per session, kept as first
written, and a rank test of the scan score against 12-1 momentum that reads only
prices before each date for momentum and only later prices for returns."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

from tracking import score_snapshots as ss  # noqa: E402

TICKERS = [f"T{i:02d}" for i in range(20)]


def test_a_session_is_written_once_and_never_rewritten(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANTA_TRACKING_DISABLED", raising=False)
    rows = [{"ticker": "aaa", "score": 7.1, "outcome": "reached_book"},
            {"ticker": "BBB", "score": 4.0, "outcome": "risk_reward_low"},
            {"ticker": "AAA", "score": 1.0}, {"ticker": "CCC", "score": "n/a"}, {"ticker": "", "score": 3}]
    p = ss.record_snapshot("US", "2026-11-06", "standard", rows, base=tmp_path)
    assert p == tmp_path / "US" / "2026-11-06__standard.csv"
    frame = pd.read_csv(p)
    assert list(frame["ticker"]) == ["AAA", "BBB"], "deduplicated, the first score kept, bad rows dropped"
    assert frame.loc[0, "score"] == 7.1
    again = ss.record_snapshot("US", "2026-11-06", "standard", [{"ticker": "ZZZ", "score": 9}], base=tmp_path)
    assert again is None and list(pd.read_csv(p)["ticker"]) == ["AAA", "BBB"]


def test_nothing_is_written_when_tracking_is_off_or_there_is_no_session(tmp_path, monkeypatch):
    rows = [{"ticker": "AAA", "score": 5}]
    assert ss.record_snapshot("US", None, "standard", rows, base=tmp_path) is None
    monkeypatch.setenv("QUANTA_TRACKING_DISABLED", "1")
    assert ss.record_snapshot("US", "2026-11-06", "standard", rows, base=tmp_path) is None
    assert not any(tmp_path.rglob("*.csv"))


def test_file_names_refuse_path_parts(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANTA_TRACKING_DISABLED", raising=False)
    with pytest.raises(ValueError):
        ss.record_snapshot("US", "2026-11-06", "../../etc", [{"ticker": "A", "score": 1}], base=tmp_path)


def test_the_suite_never_writes_real_snapshots():
    assert "quanta-test-cache" in str(ss.snapshot_dir())


# ── evaluation ───────────────────────────────────────────────────────────────

def _prices(drifts, start="2025-01-02", days=520):
    idx = pd.bdate_range(start, periods=days)
    return {t: pd.DataFrame({"Close": 100 * np.cumprod(np.full(days, 1 + d))}, index=idx)
            for t, d in zip(TICKERS, drifts)}


def _loader(prices):
    return lambda t, s, e: prices.get(t, pd.DataFrame())


def _snapshots(prices, score_of, sessions):
    idx = next(iter(prices.values())).index
    return {idx[i].date().isoformat(): pd.DataFrame({"ticker": TICKERS, "score": [score_of(j) for j in range(20)],
                                                     "outcome": ""})
            for i in sessions}


def test_a_score_that_orders_returns_beats_a_flat_momentum_cross_section():
    # Steady drifts: momentum and forward returns share the order, so both rank perfectly.
    prices = _prices(np.linspace(-0.001, 0.001, 20))
    snaps = _snapshots(prices, lambda j: j, range(300, 440, 5))
    rep = ss.evaluate(snaps, _loader(prices), min_dates=2, today=date(2027, 1, 1))
    h21 = next(h for h in rep.horizons if h.horizon == 21)
    assert h21.mean_ic_score == pytest.approx(1.0) and h21.mean_ic_momentum == pytest.approx(1.0)
    assert h21.dates == 28 and h21.independent_dates >= 2
    assert "no significant difference" in h21.verdict


def test_forward_returns_and_momentum_never_cross_the_snapshot_date():
    base = _prices(np.linspace(-0.0005, 0.0005, 20))
    t0 = base["T00"].index[300]
    before = {t: (ss._momentum(base[t]["Close"], t0), ss._forward(base[t]["Close"], t0, 21)) for t in TICKERS}
    # A jump AFTER the snapshot date, largest for the lowest-momentum names.
    moved = {t: f.copy() for t, f in base.items()}
    for j, t in enumerate(TICKERS):
        moved[t].loc[moved[t].index > t0, "Close"] *= (1 + 0.01 * (19 - j))
    for j, t in enumerate(TICKERS):
        mom, fwd = ss._momentum(moved[t]["Close"], t0), ss._forward(moved[t]["Close"], t0, 21)
        assert mom == pytest.approx(before[t][0]), "momentum reads only closes at or before the date"
        assert 1 + fwd == pytest.approx((1 + before[t][1]) * (1 + 0.01 * (19 - j))), "the return reads after it"
    # A score that knew the jump ranks the returns; momentum, which cannot, does not.
    snaps = _snapshots(moved, lambda j: 19 - j, [300])
    rep = ss.evaluate(snaps, _loader(moved), min_dates=1, today=date(2027, 1, 1))
    h = next(h for h in rep.horizons if h.horizon == 21)
    assert h.mean_ic_score == pytest.approx(1.0) and h.mean_ic_momentum < 0


def test_too_few_dates_gives_no_verdict_and_unpriced_names_drop_out():
    prices = _prices(np.linspace(-0.001, 0.001, 20))
    snaps = _snapshots(prices, lambda j: j, [300, 301])
    rep = ss.evaluate(snaps, _loader(prices), min_dates=10, today=date(2027, 1, 1))
    assert all(h.verdict.startswith("not enough data") for h in rep.horizons)
    few = {k: v for k, v in list(prices.items())[:5]}
    rep2 = ss.evaluate(snaps, _loader(few), min_dates=1, today=date(2027, 1, 1))
    assert all(h.dates == 0 for h in rep2.horizons), f"under {ss.MIN_NAMES} priced names a date is skipped"


def test_no_snapshots_reports_so():
    rep = ss.evaluate({}, lambda *a: pd.DataFrame(), min_dates=10)
    assert rep.snapshots == 0 and all(h.verdict == "no snapshots yet" for h in rep.horizons)
    assert "0 session(s)" in ss.format_report(rep)


def test_a_finished_scan_records_its_cross_section(monkeypatch, tmp_path):
    """The web worker writes the snapshot from the engine's own list."""
    import sys
    sys.path.insert(0, str(ss.ROOT / "webapp"))
    monkeypatch.delenv("QUANTA_TRACKING_DISABLED", raising=False)
    monkeypatch.setenv("QUANTA_SNAPSHOT_DIR", str(tmp_path))
    from test_scan_accounting import _run_worker
    from test_elite_pro_review2_fixes import _pick
    recs = [_pick("SYNA", "Tech", 8.0, seed=1), _pick("SYNB", "Health", 7.5, seed=2)]
    res = _run_worker(monkeypatch, recs, max_positions=2)
    files = list(tmp_path.rglob("*.csv"))
    assert res["scan_type"] == "custom" and not files, "a custom list is not a universe"


def test_a_universe_scan_records_every_name_it_scored(monkeypatch, tmp_path):
    import sys
    import time
    sys.path.insert(0, str(ss.ROOT / "webapp"))
    monkeypatch.delenv("QUANTA_TRACKING_DISABLED", raising=False)
    monkeypatch.setenv("QUANTA_SNAPSHOT_DIR", str(tmp_path))
    import app as webapp
    from test_elite_pro_review2_fixes import _pick
    from test_scan_accounting import _fake_engine_module
    recs = [_pick("SYNA", "Tech", 8.0, seed=1), _pick("SYNB", "Health", 7.5, seed=2)]
    fake = _fake_engine_module(recs)
    fake.QuantaEliteTradingSystem.score_snapshot = lambda self: [
        {"ticker": r.ticker, "score": r.overall_score, "outcome": "reached_book"} for r in recs] + [
        {"ticker": "LOW", "score": 2.5, "outcome": "score_below_min"}]
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", fake)
    monkeypatch.setattr(webapp, "_LAST_SCAN_DIR", tmp_path / "last_scan")
    monkeypatch.setattr(webapp, "_data_dates", lambda *a, **k: {"bar_session": "2026-11-06"})
    job_id = "snap-test"
    webapp._jobs[job_id] = {"type": "stock-picks", "status": "running", "progress": 0,
                            "started_at": time.time(), "worker_active": True}
    params = dict(capital=100_000.0, market="US", scan_type="quick", custom_tickers=None,
                  risk_profile="balanced", max_positions=2, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job_id, params)
        assert webapp._jobs[job_id]["status"] == "complete", webapp._jobs[job_id].get("error")
    finally:
        webapp._jobs.pop(job_id, None)
    path = tmp_path / "US" / "2026-11-06__quick.csv"
    assert set(pd.read_csv(path)["ticker"]) == {"SYNA", "SYNB", "LOW"}


def test_the_engine_lists_book_names_and_rejected_names_once():
    import threading
    import SP_ELITE_PRO as sp
    from test_elite_pro_review2_fixes import _pick
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj._diagnostics_lock, obj._candidate_rejections = threading.Lock(), {}
    obj.scan_results = [_pick("SYNA", "Tech", 8.0, seed=1)]
    obj._record_candidate_rejection("LOW", "score_below_min", 2.78, "below")
    obj._record_candidate_rejection("SYNA", "risk_reward_low", 6.0, "survived later; book wins")
    rows = {r["ticker"]: r for r in obj.score_snapshot()}
    assert rows["SYNA"]["outcome"] == "reached_book" and rows["SYNA"]["score"] == 8.0
    assert rows["LOW"] == {"ticker": "LOW", "score": 2.78, "outcome": "score_below_min"}
