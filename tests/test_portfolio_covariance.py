"""P1-3: Ledoit-Wolf shrinkage for portfolio risk shares.

Properties that must hold whatever the data: the shrunk matrix keeps each
name's variance, is symmetric positive semi-definite, the intensity falls as
history grows, and Euler risk shares still sum to one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portfolio.analytics import Holding, PortfolioPolicy, volatility_and_correlation
from portfolio.covariance import constant_correlation_target, ledoit_wolf


def _returns(t, n=6, seed=0):
    """Two-factor returns: correlations differ by pair, so the constant-correlation
    target is wrong and a long history should trust the sample matrix."""
    rng = np.random.default_rng(seed)
    loadings = np.linspace(-0.6, 0.6, 2 * n).reshape(n, 2)  # row norms < 1: a valid correlation
    corr = loadings @ loadings.T
    corr = corr + np.diag(1.0 - np.diag(corr))
    sd = np.linspace(0.01, 0.03, n)
    return rng.multivariate_normal(np.zeros(n), corr * np.outer(sd, sd), size=t)


def test_shrunk_matrix_keeps_variances_and_is_psd():
    X = _returns(60)
    out = ledoit_wolf(X)
    cov = out["cov"]
    assert np.allclose(np.diag(cov), np.diag(np.cov(X, rowvar=False, ddof=1)))
    assert np.allclose(cov, cov.T)
    assert np.linalg.eigvalsh(cov).min() > -1e-12
    assert 0.0 <= out["shrinkage"] <= 1.0


def test_equal_true_correlations_shrink_fully_to_the_target():
    rng = np.random.default_rng(9)
    cov = np.full((5, 5), 0.4) + 0.6 * np.eye(5)
    X = rng.multivariate_normal(np.zeros(5), cov * 1e-4, size=200)
    assert ledoit_wolf(X)["shrinkage"] > 0.5


def test_intensity_falls_as_history_grows():
    short = ledoit_wolf(_returns(40, seed=1))["shrinkage"]
    long = ledoit_wolf(_returns(4000, seed=1))["shrinkage"]
    assert long < short
    assert long < 0.05


def test_long_history_converges_to_the_sample_matrix():
    X = _returns(20000, seed=2)
    assert np.allclose(ledoit_wolf(X)["cov"], np.cov(X, rowvar=False, ddof=1), atol=2e-6)


def test_target_has_the_average_correlation():
    S = np.cov(_returns(500, seed=3), rowvar=False)
    F, r_bar = constant_correlation_target(S)
    sd = np.sqrt(np.diag(S))
    corr = S / np.outer(sd, sd)
    assert r_bar == pytest.approx(corr[~np.eye(len(S), dtype=bool)].mean())
    assert np.allclose(np.diag(F), np.diag(S))


def test_degenerate_inputs_fall_back_to_the_sample_matrix():
    one = ledoit_wolf(_returns(50, n=1))
    assert one["shrinkage"] == 0.0 and one["cov"].shape == (1, 1)
    flat = np.column_stack([np.zeros(50), _returns(50, n=1)[:, 0]])
    assert ledoit_wolf(flat)["shrinkage"] == 0.0
    with pytest.raises(ValueError):
        ledoit_wolf(np.zeros(5))


def test_risk_shares_sum_to_one_and_report_the_method():
    X = _returns(120, n=4, seed=4)
    idx = pd.bdate_range("2026-01-01", periods=len(X))
    frame = pd.DataFrame(X, index=idx, columns=["A", "B", "C", "D"])
    holdings = [Holding(symbol=s, quantity=1.0, value=v) for s, v in zip(frame.columns, (4000, 3000, 2000, 1000))]
    out = volatility_and_correlation(holdings, frame, PortfolioPolicy())
    assert sum(out["risk_share"].values()) == pytest.approx(1.0)
    assert out["covariance"]["method"] == "ledoit_wolf_constant_correlation"
    assert 0.0 <= out["covariance"]["shrinkage"] <= 1.0
