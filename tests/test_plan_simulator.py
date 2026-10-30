"""Plan Simulator: determinism, drift and break-even behaviour, path rules,
history limits, volatility rescaling and the Wilson verdict rule."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from engine import plan_simulator as ps


def _frame(closes, seed=0, wick=0.004, open_noise=0.002):
    """Daily bars from a close path. Opens and wicks are small noise around it."""
    rng = np.random.default_rng(seed)
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, open_noise, len(c)))
    hi = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, wick, len(c))))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, wick, len(c))))
    idx = pd.bdate_range("2020-01-01", periods=len(c))
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c}, index=idx)


def _random_walk(n=1000, sd=0.012, drift=0.0, seed=3):
    rng = np.random.default_rng(seed)
    return _frame(100.0 * np.exp(np.cumsum(rng.normal(drift, sd, n))), seed=seed + 1)


# ---------------------------------------------------------------------------
# Determinism and output shape
# ---------------------------------------------------------------------------

def test_same_seed_gives_identical_output():
    bars = _random_walk()
    kw = dict(entry=100.0, stop=95.0, target=106.0, horizon_sessions=63, paths=2000, ticker="TEST")
    a = ps.simulate_plan(bars, seed=42, **kw)
    b = ps.simulate_plan(bars, seed=42, **kw)
    assert a == b
    assert a["seed"] == 42


def test_different_seed_changes_the_paths():
    bars = _random_walk()
    kw = dict(entry=100.0, stop=95.0, target=106.0, paths=2000, ticker="TEST")
    a = ps.simulate_plan(bars, seed=1, **kw)
    b = ps.simulate_plan(bars, seed=2, **kw)
    assert a["simulation"]["r"] != b["simulation"]["r"]


def test_outcome_shares_sum_to_one_and_output_is_explained():
    out = ps.simulate_plan(_random_walk(), entry=100.0, stop=95.0, target=106.0, paths=2000, ticker="TEST")
    sim = out["simulation"]
    assert sim["p_target"] + sim["p_stop"] + sim["p_timeout"] == pytest.approx(1.0, abs=1e-3)
    assert out["summary"] and isinstance(out["notes"], list) and out["notes"]
    assert any("not a forecast" in n for n in out["notes"])
    h = out["historical"]
    assert h["target"] + h["stop"] + h["timeout"] == h["sessions_tested"]
    assert out["verdict"]["label"]


# ---------------------------------------------------------------------------
# Break-even behaviour
# ---------------------------------------------------------------------------

def test_drift_free_symmetric_plan_sits_near_break_even():
    # Stop and target equally far from entry: a 1R plan of about 1:1 gross.
    bars = _random_walk(n=1200, sd=0.012, drift=0.0, seed=11)
    out = ps.simulate_plan(bars, entry=100.0, stop=95.0, target=105.0, drift="zero",
                           paths=5000, seed=7, ticker="TEST")
    share = out["simulation"]["target_share_decided"]
    be = out["simulation"]["break_even_share"]
    assert be == pytest.approx(0.5, abs=0.03)
    # Stop-first intrabar and gap rules are conservative, so allow a little below.
    assert abs(share - be) < 0.06, (share, be)


def test_strong_positive_drift_reads_above_break_even():
    bars = _random_walk(n=1200, sd=0.010, drift=0.004, seed=5)
    out = ps.simulate_plan(bars, entry=100.0, stop=96.0, target=104.0, paths=5000, seed=9,
                           drift="historical", ticker="TEST")
    sim = out["simulation"]
    assert sim["target_share_decided"] > sim["break_even_share"] + ps.CLOSE_BAND
    assert out["verdict"]["simulated_band"] == ps.BAND_ABOVE
    assert out["history"]["drift_ann"] > 0


def test_break_even_is_one_over_one_plus_net_rr():
    out = ps.simulate_plan(_random_walk(), entry=100.0, stop=95.0, target=110.0, paths=1000, ticker="TEST")
    rr = out["plan"]["rr_net"]
    assert out["simulation"]["break_even_share"] == pytest.approx(1.0 / (1.0 + rr), abs=1e-3)


# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------

def test_gap_through_stop_exits_at_the_open_worse_than_minus_one_r():
    # Every bar opens and closes 8% or 10% below the prior close: the first bar
    # of every path opens below a stop 5% under entry.
    rng = np.random.default_rng(0)
    factors = rng.choice([0.90, 0.92], size=800)
    closes = 100.0 * np.cumprod(factors)
    bars = pd.DataFrame({"Open": np.r_[100.0, closes[:-1]] * factors,
                         "High": np.r_[100.0, closes[:-1]] * factors * 1.0005,
                         "Low": np.r_[100.0, closes[:-1]] * factors * 0.9995,
                         "Close": closes},
                        index=pd.bdate_range("2020-01-01", periods=800))
    # The sample's own (strongly negative) drift is kept, so every path opens
    # through the stop on its first bar.
    out = ps.simulate_plan(bars, entry=100.0, stop=95.0, target=110.0, paths=1000, drift="historical",
                           ticker="TEST")
    assert out["simulation"]["p_stop"] == pytest.approx(1.0)
    assert out["simulation"]["r"]["p95"] < -1.0


def test_resolve_stop_and_target_in_one_bar_counts_stop():
    Opn = np.array([[100.0]])
    H = np.array([[108.0]])
    L = np.array([[94.0]])
    C = np.array([[100.0]])
    exit_p, outcome = ps.resolve_paths(Opn, H, L, C, 100.0, 95.0, 105.0)
    assert outcome[0] == 1 and exit_p[0] == 95.0


def test_open_gapping_above_target_fills_at_the_open():
    Opn = np.array([[107.0]])
    H = np.array([[108.0]])
    L = np.array([[106.0]])
    C = np.array([[107.0]])
    exit_p, outcome = ps.resolve_paths(Opn, H, L, C, 100.0, 95.0, 105.0)
    assert outcome[0] == 0 and exit_p[0] == 107.0


def test_net_r_charges_costs_on_both_legs():
    r = ps.net_r(np.array([105.0]), 100.0, 95.0, 0.001)
    assert r[0] == pytest.approx((105 - 100 - 0.1) / (5 + 0.1))
    assert ps.net_r(np.array([100.0]), 100.0, 95.0, 0.001)[0] < 0


def test_round_trip_cost_adds_statutory_levies_for_india():
    us = ps.round_trip_cost_frac("AAPL")
    india = ps.round_trip_cost_frac("RELIANCE.NS")
    assert us == pytest.approx(11 / 10000)
    assert india > us


# ---------------------------------------------------------------------------
# History limits and refusals
# ---------------------------------------------------------------------------

def test_insufficient_history_is_refused_with_a_reason():
    bars = _random_walk(n=200)
    with pytest.raises(ps.PlanSimulationError, match="sessions of history"):
        ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, paths=1000)


def test_stop_must_sit_below_entry_and_target_above():
    bars = _random_walk()
    with pytest.raises(ps.PlanSimulationError):
        ps.simulate_plan(bars, entry=100.0, stop=101.0, target=106.0)
    with pytest.raises(ps.PlanSimulationError):
        ps.simulate_plan(bars, entry=100.0, stop=95.0, target=99.0)


def test_horizon_and_paths_are_bounded():
    bars = _random_walk()
    with pytest.raises(ps.PlanSimulationError):
        ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, horizon_sessions=4)
    with pytest.raises(ps.PlanSimulationError):
        ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, horizon_sessions=127)
    with pytest.raises(ps.PlanSimulationError):
        ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, paths=100)


def test_only_long_plans_are_supported():
    with pytest.raises(ps.PlanSimulationError, match="long"):
        ps.simulate_plan(_random_walk(), entry=100.0, stop=95.0, target=106.0, side="short")


# ---------------------------------------------------------------------------
# Volatility rescaling
# ---------------------------------------------------------------------------

def test_volatility_rescales_toward_recent_volatility():
    rng = np.random.default_rng(21)
    calm = rng.normal(0, 0.005, 500)
    wild = rng.normal(0, 0.03, 300)
    bars = _frame(100.0 * np.exp(np.cumsum(np.r_[calm, wild])), seed=22)
    out = ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, paths=1000, ticker="TEST")
    hist = out["history"]
    assert hist["vol_ewma_ann"] > hist["vol_sample_ann"]
    assert hist["vol_scale"] > 1.0


def test_steady_volatility_gives_a_scale_near_one():
    bars = _random_walk(n=1000, sd=0.01, seed=31)
    out = ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, paths=1000, ticker="TEST")
    assert 0.7 < out["history"]["vol_scale"] < 1.4
    assert out["history"]["vol_sample_ann"] > 0 and out["history"]["vol_ewma_ann"] > 0


# ---------------------------------------------------------------------------
# Historical replay and the Wilson rule
# ---------------------------------------------------------------------------

def test_wilson_interval_matches_the_textbook_value():
    lo, hi = ps.wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3)
    assert hi == pytest.approx(0.5962, abs=1e-3)
    assert ps.wilson_interval(0, 0) is None


def test_historical_replay_uses_effective_sample_size():
    out = ps.simulate_plan(_random_walk(n=756), entry=100.0, stop=95.0, target=106.0,
                           horizon_sessions=63, paths=1000, ticker="TEST")
    h = out["historical"]
    assert h["effective_n"] == max(1, round(h["decided"] / 63))
    assert any("effective" in n for n in out["notes"])
    assert h["wilson95_effective"][0] <= h["wilson95_effective"][1]


def test_verdict_says_not_distinguishable_when_history_interval_holds_break_even():
    v = ps._verdict(0.60, 0.50, (0.40, 0.70))
    assert v["verdict"]["label"] == ps.BAND_NOT_DISTINGUISHABLE
    assert v["verdict"]["historical_distinguishable"] is False
    assert v["simulated_band"] == ps.BAND_ABOVE


def test_verdict_keeps_band_when_history_is_clear_of_break_even():
    v = ps._verdict(0.60, 0.50, (0.55, 0.70))
    assert v["verdict"]["label"] == ps.BAND_ABOVE
    assert v["verdict"]["history_read"] == "above"
    assert ps._verdict(0.40, 0.50, (0.30, 0.45))["verdict"]["label"] == ps.BAND_BELOW


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def test_756_bars_by_63_sessions_by_5000_paths_runs_quickly():
    bars = _random_walk(n=760, seed=41)
    start = time.perf_counter()
    out = ps.simulate_plan(bars, entry=100.0, stop=95.0, target=106.0, horizon_sessions=63,
                           lookback_sessions=756, paths=5000, seed=1, ticker="TEST")
    elapsed = time.perf_counter() - start
    assert out["paths"] == 5000
    assert elapsed < 1.5, f"took {elapsed:.2f}s"
