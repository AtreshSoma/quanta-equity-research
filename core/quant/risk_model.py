"""
risk_model.py — Robust covariance + portfolio optimization for small N.

Fixes the visible failure mode from the 2026-05-27 run:
    "Mean-variance optimization skipped: 4 positions cannot sum to 1.0
     under per-position cap 22%"

Root causes:
- Per-position cap × N < 1.0 makes the SLSQP feasibility region empty.
- Sample covariance on ~30 days of returns is noisy → unstable weights.
- Failure mode is binary (works or skipped) — no graceful fallback ladder.

This module provides:
1. `ledoit_wolf_shrinkage(returns)` — analytic shrinkage to identity-scaled
   target. Pure numpy, no sklearn dependency.
2. `RobustOptimizer.optimize(...)` — tries, in order:
     a) Mean-variance with current caps
     b) Mean-variance with auto-relaxed caps (just enough for feasibility)
     c) Risk-parity (inverse-vol) weights
     d) Equal-weight
   …and always returns a valid weight vector, never raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:
    import pandas as pd  # type: ignore
except ImportError:  # pragma: no cover
    pd = None  # type: ignore

try:
    from scipy.optimize import minimize  # type: ignore
    HAS_SCIPY = True
except ImportError:  # pragma: no cover
    minimize = None  # type: ignore
    HAS_SCIPY = False


# ─────────────────────────────────────────────────────────────────────────
# Covariance estimation
# ─────────────────────────────────────────────────────────────────────────
def ledoit_wolf_shrinkage(returns: np.ndarray) -> Tuple[np.ndarray, float]:
    """
    Ledoit-Wolf (2004) analytic shrinkage of sample covariance toward
    F = mean(diag(S)) * I. Pure numpy implementation.

    Parameters
    ----------
    returns : (T, N) array of asset returns.

    Returns
    -------
    Sigma_shrunk : (N, N) shrunk covariance.
    shrinkage    : optimal mixing weight in [0, 1].
    """
    X = np.asarray(returns, dtype=float)
    if X.ndim != 2 or X.shape[0] < 2:
        # Degenerate — return identity-scaled covariance.
        n = X.shape[1] if X.ndim == 2 else 1
        var = float(np.var(X)) if X.size else 1.0
        return np.eye(n) * max(var, 1e-8), 1.0

    T, N = X.shape
    Xc = X - X.mean(axis=0, keepdims=True)
    S = (Xc.T @ Xc) / T  # MLE covariance (not unbiased — matches Ledoit-Wolf)

    mu = float(np.trace(S) / N)
    F = mu * np.eye(N)

    # pi-hat: sum of asymptotic variances of S entries
    Xc2 = Xc ** 2
    pi_mat = (Xc2.T @ Xc2) / T - S ** 2
    pi_hat = float(pi_mat.sum())

    # gamma-hat: misspecification of the prior F
    gamma_hat = float(((S - F) ** 2).sum())

    # rho-hat: covariance between sample variances (diagonal only, common simplification)
    rho_hat = float(np.trace(pi_mat))

    kappa = (pi_hat - rho_hat) / max(gamma_hat, 1e-12)
    shrinkage = max(0.0, min(1.0, kappa / T))

    return shrinkage * F + (1.0 - shrinkage) * S, shrinkage


# ─────────────────────────────────────────────────────────────────────────
# Optimization
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class OptimizationResult:
    weights: np.ndarray
    method: str           # "mvo", "mvo_relaxed", "risk_parity", "equal_weight"
    success: bool
    message: str = ""
    expected_return: float = 0.0
    expected_vol: float = 0.0
    sharpe: float = 0.0
    upper_cap_used: float = 1.0

    def as_list(self) -> List[float]:
        return [float(w) for w in self.weights]


class RobustOptimizer:
    """Always-returns-weights portfolio optimizer."""

    def __init__(
        self,
        risk_aversion: float = 2.5,
        min_obs: int = 20,
        use_shrinkage: bool = True,
    ):
        self.risk_aversion = float(risk_aversion)
        self.min_obs = int(min_obs)
        self.use_shrinkage = bool(use_shrinkage)

    # ---------- internal helpers ----------
    @staticmethod
    def _equal_weight(n: int) -> np.ndarray:
        return np.ones(n) / max(n, 1)

    @staticmethod
    def _risk_parity(cov: np.ndarray) -> np.ndarray:
        """Inverse-volatility weights — fast, stable, no optimizer needed."""
        vol = np.sqrt(np.diag(cov))
        vol = np.where(vol > 0, vol, vol[vol > 0].mean() if (vol > 0).any() else 1.0)
        w = 1.0 / vol
        return w / w.sum()

    def _mvo(
        self,
        mu: np.ndarray,
        cov: np.ndarray,
        upper: float,
        lower: float,
    ) -> Optional[np.ndarray]:
        if not HAS_SCIPY:
            return None
        n = len(mu)
        if n * upper < 1.0 - 1e-9 or n * lower > 1.0 + 1e-9:
            return None  # infeasible

        def neg_util(w):
            return -(w @ mu - self.risk_aversion * (w @ cov @ w))

        bounds = [(lower, upper)] * n
        constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
        w0 = self._equal_weight(n)
        try:
            res = minimize(
                neg_util, w0, method="SLSQP",
                bounds=bounds, constraints=constraints,
                options={"maxiter": 200, "ftol": 1e-9},
            )
        except Exception:
            return None
        if not res.success:
            return None
        return np.asarray(res.x, dtype=float)

    # ---------- public API ----------
    def optimize(
        self,
        returns,
        upper_cap: float = 0.25,
        lower_cap: float = 0.0,
    ) -> OptimizationResult:
        """
        Parameters
        ----------
        returns : (T, N) ndarray or DataFrame of asset returns.
        upper_cap : per-position cap as a fraction of portfolio (0..1).
        lower_cap : per-position floor.

        Returns
        -------
        OptimizationResult — always populated; falls back through the ladder.
        """
        # Coerce input to ndarray
        if pd is not None and hasattr(returns, "values"):
            arr = returns.values
        else:
            arr = np.asarray(returns, dtype=float)

        if arr.ndim != 2 or arr.shape[1] == 0:
            return OptimizationResult(
                weights=np.array([1.0]), method="equal_weight",
                success=False, message="empty returns",
            )

        T, N = arr.shape
        if N == 1:
            return OptimizationResult(
                weights=np.array([1.0]), method="equal_weight",
                success=True, message="single asset",
            )

        # Covariance & expected returns
        if self.use_shrinkage and T >= 2:
            cov_daily, shrink = ledoit_wolf_shrinkage(arr)
        else:
            cov_daily = np.cov(arr, rowvar=False)
            shrink = 0.0
        cov = cov_daily * 252.0
        mu = arr.mean(axis=0) * 252.0 if T >= 2 else np.zeros(N)

        # Auto-feasibility: raise upper cap to 1/N + slack if needed.
        eff_upper = upper_cap
        if N * eff_upper < 1.0:
            eff_upper = min(1.0, (1.0 / N) * 1.25)  # smallest feasible cap + 25% slack
            relaxed = True
        else:
            relaxed = False

        # Tier 1/2: MVO
        w = self._mvo(mu, cov, upper=eff_upper, lower=lower_cap)
        if w is not None:
            method = "mvo_relaxed" if relaxed else "mvo"
            return self._package(w, method, mu, cov, eff_upper, success=True,
                                 message=f"shrinkage={shrink:.2f}")

        # Tier 3: risk parity
        try:
            w = self._risk_parity(cov)
            # Enforce cap by capping + renormalizing (single pass — sufficient
            # because we already widened upper to ≥1/N when infeasible)
            w = np.minimum(w, eff_upper)
            w = w / w.sum() if w.sum() > 0 else self._equal_weight(N)
            return self._package(w, "risk_parity", mu, cov, eff_upper, success=True)
        except Exception:
            pass

        # Tier 4: equal weight
        w = self._equal_weight(N)
        return self._package(w, "equal_weight", mu, cov, eff_upper, success=True,
                             message="all optimizers failed; falling back")

    @staticmethod
    def _package(
        w: np.ndarray,
        method: str,
        mu: np.ndarray,
        cov: np.ndarray,
        cap: float,
        success: bool,
        message: str = "",
    ) -> OptimizationResult:
        port_ret = float(w @ mu)
        port_var = float(w @ cov @ w)
        port_vol = float(np.sqrt(max(port_var, 0.0)))
        sharpe = port_ret / port_vol if port_vol > 1e-9 else 0.0
        return OptimizationResult(
            weights=w, method=method, success=success, message=message,
            expected_return=port_ret, expected_vol=port_vol, sharpe=sharpe,
            upper_cap_used=cap,
        )
