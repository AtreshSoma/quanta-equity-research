"""Validation-statistics correctness: Sortino, N/A trade stats, the statistical
gate (size and power), trial counter, Wilson/BH helpers, India cost model."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from apex_quant import metrics, stats, trials
from apex_quant.costs import (CostModel, IndiaEquityDeliveryCostModel,
                              cost_model_for_symbol)
from apex_quant.walkforward import WalkForwardResult


def test_sortino_uses_downside_deviation_over_all_observations():
    r = pd.Series([0.02, -0.01, 0.01, -0.03])
    rep = metrics.equity_metrics(r, [], 0.0)
    # downside deviation = sqrt((0.01^2 + 0.03^2) / 4) = sqrt(0.00025)
    dd = math.sqrt(0.00025)
    assert rep.sortino == pytest.approx(r.mean() / dd * math.sqrt(252))


def test_buy_and_hold_trade_stats_are_not_applicable():
    r = pd.Series([0.01, -0.005, 0.002, 0.004, -0.001])
    rep = metrics.equity_metrics(r, None, 1.0)
    assert rep.trade_stats_applicable is False
    assert rep.n_trades == 0 and math.isnan(rep.hit_rate_pct)
    assert rep.to_dict()["hit_rate_pct"] is None
    assert metrics.equity_metrics(r, [0.01, -0.02], 1.0).trade_stats_applicable is True


def _result(strat, spy, re_, n_trials=1):
    idx = pd.bdate_range("2020-01-01", periods=len(strat))
    daily = {"strategy": pd.Series(strat, idx), "spy": pd.Series(spy, idx),
             "random_entry": pd.Series(re_, idx), "random_exit": pd.Series(re_, idx)}
    reps = {k: metrics.equity_metrics(v, [0.01] if k == "strategy" else None, 1.0)
            for k, v in daily.items()}
    return WalkForwardResult(
        folds=[], strategy=reps["strategy"], spy=reps["spy"],
        random_entry=reps["random_entry"], random_exit=reps["random_exit"],
        oos_start=idx[0], oos_end=idx[-1], data_hash="x", oos_daily=daily,
        n_trials=n_trials)


def test_gate_fails_for_no_skill_strategy_even_when_sharpe_edges_ahead():
    fails = 0
    for seed in range(10):
        rng = np.random.default_rng(seed)
        n = 750
        spy = rng.normal(0.0004, 0.01, n)
        strat = rng.normal(0.0004, 0.01, n)   # same distribution: no skill
        re_ = rng.normal(0.0004, 0.01, n)
        fails += not _result(strat, spy, re_).beats_benchmarks()
    assert fails >= 9  # size: ~5% false-pass rate at most, allow 1 of 10


def test_gate_passes_for_strong_skill_strategy():
    rng = np.random.default_rng(3)
    n = 750
    spy = rng.normal(0.0004, 0.01, n)
    re_ = rng.normal(0.0003, 0.01, n)
    strat = rng.normal(0.0030, 0.01, n)       # large persistent excess
    res = _result(strat, spy, re_)
    d = res.gate_details()
    assert res.beats_benchmarks(), d
    assert d["lb_vs_spy"] > 0 and d["lb_vs_random_entry"] > 0


def test_gate_requires_deflated_sharpe_when_many_trials():
    rng = np.random.default_rng(5)
    n = 500
    spy = rng.normal(0.0004, 0.01, n)
    re_ = rng.normal(0.0003, 0.01, n)
    strat = rng.normal(0.0030, 0.01, n)
    assert _result(strat, spy, re_, n_trials=1).beats_benchmarks()
    weak = _result(rng.normal(0.0012, 0.01, n), spy, re_, n_trials=100000)
    assert weak.gate_details()["dsr"] is not None
    assert not weak.beats_benchmarks()


def test_gate_without_daily_series_cannot_pass():
    res = _result(np.full(50, 0.01), np.zeros(50), np.zeros(50))
    res.oos_daily = {}
    res.__dict__.pop("_gate_cache", None)
    assert not res.beats_benchmarks()


def test_trial_counter_counts_distinct_variants(tmp_path):
    p = tmp_path / "sub" / "trials.json"
    assert trials.trial_count("s", p) == 0
    assert trials.register_trials("s", ["a", "b"], p, on_date="2026-01-01") == 2
    assert trials.register_trials("s", ["b", "c"], p) == 3   # rerun does not inflate
    assert trials.trial_count("other", p) == 0


def test_wilson_interval_known_value_and_bounds():
    lo, hi = stats.wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert stats.wilson_interval(0, 5)[0] == 0.0
    assert stats.wilson_interval(0, 0) is None


def test_bh_adjusted_matches_hand_calculation():
    adj = stats.benjamini_hochberg_adjusted([0.01, 0.04, 0.03, None])
    assert adj[0] == pytest.approx(0.03) and adj[2] == pytest.approx(0.04)
    assert adj[1] == pytest.approx(0.04) and adj[3] is None


def test_india_round_trip_arithmetic():
    cm = IndiaEquityDeliveryCostModel(half_spread_bps=0.0, base_slippage_bps=0.0, impact_k=0.0)
    price, shares = 100.0, 1000   # notional Rs 100,000
    n = price * shares
    buy = cm.side_cost_pct(price, shares, 0.02, 1e9, "buy") * n
    sell = cm.side_cost_pct(price, shares, 0.02, 1e9, "sell") * n
    exch, sebi = n * 0.00297 / 100, n * 0.0001 / 100
    gst = 0.18 * (exch + sebi)
    assert buy == pytest.approx(100.0 + exch + sebi + gst + n * 0.00015)
    assert sell == pytest.approx(100.0 + exch + sebi + gst + 15.93)
    assert 2 * cm.side_cost_pct(price, shares, 0.02, 1e9) == pytest.approx((buy + sell) / n)


def test_cost_model_selector_by_suffix_and_us_defaults_unchanged():
    assert isinstance(cost_model_for_symbol("TCS.NS"), IndiaEquityDeliveryCostModel)
    assert isinstance(cost_model_for_symbol("RELIANCE.bo"), IndiaEquityDeliveryCostModel)
    us = cost_model_for_symbol("AAPL")
    assert type(us) is CostModel and us.half_spread_bps == 3.0
