"""Shrinkage covariance for portfolio risk (Ledoit-Wolf, constant correlation).

The sample covariance of daily returns over a book's shared window is noisy when
the book holds many names over a short history: individual pairwise covariances
swing from week to week, and so does each holding's share of the risk. Ledoit and
Wolf shrink the sample matrix S towards a structured target F, here every pair at
the book's average correlation with each name's own variance kept:

    S_hat = (1 - alpha) * S + alpha * F

with the intensity ``alpha`` chosen from the data to minimise expected squared
error. A long history drives ``alpha`` towards 0 (the sample matrix is trusted);
a short one towards 1.

Formulas: Ledoit, O. and Wolf, M. (2004), "Honey, I shrunk the sample covariance
matrix", Journal of Portfolio Management 30(4). The arrangement follows
``qlib/model/riskmodel/shrink.py`` (microsoft/qlib, MIT licence, Copyright (c)
Microsoft Corporation), rewritten here so QUANTA takes no Qlib dependency.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

METHOD = "ledoit_wolf_constant_correlation"


def constant_correlation_target(S: np.ndarray) -> Tuple[np.ndarray, float]:
    """Target F (sample variances, every pair at the average correlation) and that correlation."""
    n = len(S)
    sd = np.sqrt(np.diag(S))
    outer = np.outer(sd, sd)
    r_bar = float((np.sum(S / outer) - n) / (n * (n - 1)))
    F = r_bar * outer
    np.fill_diagonal(F, np.diag(S))
    return F, r_bar


def ledoit_wolf(returns: Any) -> Dict[str, Any]:
    """Shrunk covariance of a (T x N) array or DataFrame of returns, no missing values.

    Returns ``cov`` (N x N, same scale as the sample covariance with ddof=1),
    ``shrinkage`` (alpha in [0, 1]), ``avg_correlation`` (the target's r-bar) and
    ``method``. With one asset, fewer than three observations, or a constant
    series, there is nothing to shrink towards and the sample matrix is returned
    with ``shrinkage`` 0.
    """
    X = np.asarray(returns, dtype=float)
    if X.ndim != 2:
        raise ValueError("returns must be two-dimensional (observations x assets)")
    t, n = X.shape
    sample = np.cov(X, rowvar=False, ddof=1) if t > 1 else np.zeros((n, n))
    sample = np.atleast_2d(sample)
    out = {"cov": sample, "shrinkage": 0.0, "avg_correlation": None, "method": METHOD}
    if n < 2 or t < 3 or not np.all(np.isfinite(X)):
        return out
    Xc = X - X.mean(axis=0)
    S = Xc.T @ Xc / t                       # the estimator works on the 1/T covariance
    var = np.diag(S)
    if np.any(var <= 0):
        return out
    F, r_bar = constant_correlation_target(S)
    sd = np.sqrt(var)

    y = Xc ** 2
    phi_mat = y.T @ y / t - S ** 2
    phi = float(np.sum(phi_mat))
    theta = (Xc ** 3).T @ Xc / t - var[:, None] * S
    np.fill_diagonal(theta, 0.0)
    rho = float(np.sum(np.diag(phi_mat)) + r_bar * np.sum(np.outer(1.0 / sd, sd) * theta))
    gamma = float(np.linalg.norm(S - F, "fro") ** 2)
    alpha = 1.0 if gamma <= 0 else max(0.0, min(1.0, (phi - rho) / gamma / t))

    shrunk = (1.0 - alpha) * S + alpha * F
    out.update(cov=shrunk * t / (t - 1),    # back to the ddof=1 scale the callers use
               shrinkage=alpha, avg_correlation=r_bar)
    return out
