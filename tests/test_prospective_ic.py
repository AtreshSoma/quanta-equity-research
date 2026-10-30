"""The pre-registered prospective IC test (core/tracking/prospective_ic.py).

Rows are written straight into a temporary ledger with synthetic tickers, so
the procedure is checked on known answers: a score that ranks returns passes,
noise fails, a thin sample gives no verdict, and fingerprints never pool.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest

from _paths import ensure_paths

ensure_paths()

from tracking.ledger import SignalLedger  # noqa: E402
from tracking import prospective_ic as pic  # noqa: E402

TICKERS = [f"T{i:02d}" for i in range(8)]


def _insert(ledger, *, engine, day, ticker, score, excess, horizon=63, fp="fpA", strategy=None):
    ctx = json.dumps({"strategy": strategy} if strategy else {})
    with ledger._connect() as con:
        con.execute(
            "INSERT INTO signals (signal_key, engine, ticker, as_of_date, recorded_at, horizon_days, "
            "stance, action, benchmark, score, excess_return_pct, outcome, code_fingerprint, context_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{engine}|{ticker}|{horizon}|{day}|{fp}", engine, ticker, day, day + "T21:00:00Z",
             horizon, "BULLISH", "BUY", "SPY", score, excess, "WIN" if excess > 0 else "LOSS", fp, ctx))


def _fill(ledger, engine, n_dates, skill, seed=0, fp="fpA", start=date(2026, 10, 29), gap_days=95):
    rng = np.random.default_rng(seed)
    for k in range(n_dates):
        day = (start + timedelta(days=k * gap_days)).isoformat()
        scores = rng.uniform(30, 80, len(TICKERS))
        noise = rng.normal(0, 1, len(TICKERS))
        excess = skill * (scores - 55) / 10 + noise
        for t, s, e in zip(TICKERS, scores, excess):
            _insert(ledger, engine=engine, day=day, ticker=t, score=float(s), excess=float(e), fp=fp)


@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(tmp_path / "signals.sqlite3")


def test_spearman_matches_known_values():
    assert pic.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert pic.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert pic.spearman([1, 2], [1, 2]) is None            # fewer than three names
    assert pic.spearman([5, 5, 5], [1, 2, 3]) is None      # constant score
    assert pic.spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833, rel=1e-6)


def test_a_score_that_ranks_returns_passes(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=12, skill=1.5)
    out = pic.prospective_ic(ledger, "elite_v3_shadow")
    assert out["primary"]["evaluable"] is True
    assert out["primary"]["n_independent_dates"] == 12
    assert out["verdict"] == "pass" and out["primary"]["t"] >= pic.PASS_T


def test_noise_fails(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=12, skill=0.0, seed=3)
    out = pic.prospective_ic(ledger, "elite_v3_shadow")
    assert out["primary"]["evaluable"] is True
    assert out["verdict"] == "fail"


def test_thin_sample_gives_no_verdict(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=3, skill=3.0)
    assert pic.prospective_ic(ledger, "elite_v3_shadow")["verdict"] == "insufficient sample"


def test_overlapping_dates_are_not_counted_as_independent(ledger):
    # Twelve dates one week apart: a 63-session horizon spans ~13 weeks, so only
    # every 13th or so date is independent.
    _fill(ledger, "elite_v3_shadow", n_dates=12, skill=3.0, gap_days=7)
    p = pic.prospective_ic(ledger, "elite_v3_shadow")["primary"]
    assert p["n_dates_with_ic"] == 12
    assert p["n_independent_dates"] < 3
    assert p["verdict"] == "insufficient sample"


def test_fingerprints_are_never_pooled(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=12, skill=1.5, fp="fpA")
    _fill(ledger, "elite_v3_shadow", n_dates=4, skill=-3.0, fp="fpB", seed=9,
          start=date(2026, 11, 2))
    out = pic.prospective_ic(ledger, "elite_v3_shadow")
    assert set(out["by_fingerprint"]) == {"fpA", "fpB"}
    assert out["primary_fingerprint"] == "fpA"
    assert out["by_fingerprint"]["fpA"]["n_resolved_calls"] == 12 * len(TICKERS)
    assert out["verdict"] == "pass"


def test_rows_before_registration_and_other_horizons_are_ignored(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=6, skill=1.5, start=date(2025, 1, 2))  # all before 2026-10-29
    assert pic.prospective_ic(ledger, "elite_v3_shadow")["primary"] is None
    _insert(ledger, engine="elite_v3_shadow", day="2026-11-03", ticker="AAA", score=60, excess=1.0,
            horizon=5)
    assert pic.prospective_ic(ledger, "elite_v3_shadow")["verdict"] == "insufficient sample"


def test_paired_difference_uses_shared_dates_and_tickers(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=12, skill=1.5, seed=1)
    _fill(ledger, "elite", n_dates=12, skill=0.0, seed=1)
    out = pic.paired_ic_difference(ledger, "elite_v3_shadow", "elite")
    assert out["n_paired_calls"] == 12 * len(TICKERS)
    assert out["n_independent_dates"] == 12
    assert out["mean_ic_difference"] > 0


def test_read_only(ledger):
    _fill(ledger, "elite_v3_shadow", n_dates=2, skill=1.0)
    before = len(ledger.rows())
    pic.prospective_ic(ledger, "elite_v3_shadow")
    pic.paired_ic_difference(ledger, "elite_v3_shadow", "elite")
    assert len(ledger.rows()) == before
