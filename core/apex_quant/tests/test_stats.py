"""Robust inference helpers: HAC t, block bootstrap, PSR/DSR, Benjamini-Hochberg."""
from __future__ import annotations

import numpy as np
import pytest

from apex_quant import stats as S


def test_normal_quantile_matches_known_values():
    assert S._norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert S._norm_ppf(0.01) == pytest.approx(-2.326348, abs=1e-6)


def test_newey_west_without_lags_is_the_plain_t():
    x = np.random.default_rng(1).normal(0.1, 1.0, 400)
    plain = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert S.newey_west_t(x, lags=0) == pytest.approx(plain)


def test_newey_west_shrinks_t_under_positive_autocorrelation():
    rng = np.random.default_rng(2)
    e = rng.normal(0, 1, 2000)
    x = np.empty_like(e)
    x[0] = e[0]
    for i in range(1, len(e)):
        x[i] = 0.8 * x[i - 1] + e[i]
    x += 0.15
    assert abs(S.newey_west_t(x, lags=20)) < abs(S.newey_west_t(x, lags=0))


def test_block_bootstrap_is_deterministic_and_brackets_the_mean():
    x = np.random.default_rng(3).normal(0.02, 0.1, 300)
    a = S.block_bootstrap_ci(x, seed=7)
    assert a == S.block_bootstrap_ci(x, seed=7)
    assert a[0] < x.mean() < a[1]
    assert S.block_bootstrap_ci([1.0, 2.0]) is None


def test_bigger_blocks_widen_the_interval_for_dependent_data():
    rng = np.random.default_rng(4)
    x = np.repeat(rng.normal(0, 1, 60), 10)  # strongly dependent in runs of 10
    narrow = S.block_bootstrap_ci(x, block=1, seed=0)
    wide = S.block_bootstrap_ci(x, block=20, seed=0)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_probabilistic_sharpe_reads_skill_and_its_absence():
    rng = np.random.default_rng(5)
    assert S.probabilistic_sharpe(rng.normal(0.2, 1.0, 500)) > 0.99
    assert 0.2 < S.probabilistic_sharpe(rng.normal(0.0, 1.0, 500)) < 0.8


def test_deflated_sharpe_falls_as_more_variants_were_tried():
    r = np.random.default_rng(6).normal(0.08, 1.0, 750)
    one, many = S.deflated_sharpe(r, 1), S.deflated_sharpe(r, 200)
    assert one == pytest.approx(S.probabilistic_sharpe(r))
    assert many < one


def test_benjamini_hochberg_controls_the_false_discovery_rate():
    assert S.benjamini_hochberg([0.01, 0.04, 0.03, 0.005]) == [True, True, True, True]
    assert S.benjamini_hochberg([0.01, 0.2, 0.03]) == [True, False, True]
    assert S.benjamini_hochberg([None, 0.5]) == [None, False]


def test_infer_mean_bundles_the_quotable_numbers():
    res = S.infer_mean(np.random.default_rng(8).normal(0.5, 1.0, 200))
    d = res.to_dict()
    assert d["n"] == 200 and d["t_newey_west"] > 2 and d["p_value"] < 0.05
    assert d["ci95_bootstrap"][0] < d["mean"] < d["ci95_bootstrap"][1]


def test_a_block_as_long_as_the_sample_cannot_collapse_the_interval():
    x = np.random.default_rng(9).normal(0, 1, 8)
    lo, hi = S.block_bootstrap_ci(x, block=8)
    assert hi - lo > 0.1
