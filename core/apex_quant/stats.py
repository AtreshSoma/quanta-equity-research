"""Inference that survives autocorrelation, fat tails and many tries.

The harness already reports t-statistics on non-overlapping dates. Three
questions a reviewer asks next were unanswered:

* Is the confidence interval honest when returns are fat-tailed and serially
  correlated?  -> ``block_bootstrap_ci`` (circular block bootstrap) and
  ``newey_west_t`` (HAC standard error) use every date, not every h-th one.
* How many variants were tried before this one?  -> ``deflated_sharpe`` adjusts
  a Sharpe ratio for the number of trials (Bailey & Lopez de Prado, 2014).
* Several factors were tested at once.  -> ``benjamini_hochberg`` controls the
  false discovery rate across them.

Pure numpy; every function is deterministic for a given seed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

import numpy as np

_EULER = 0.5772156649015329


def _clean(x: Sequence[float]) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    return a[np.isfinite(a)]


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |err| < 1.2e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def default_block(n: int) -> int:
    """Politis-White style rule of thumb: block ~ n^(1/3)."""
    return max(1, int(round(n ** (1.0 / 3.0))))


def block_bootstrap_ci(x: Sequence[float], stat: Callable[[np.ndarray], float] = np.mean,
                       block: Optional[int] = None, n_boot: int = 4000, alpha: float = 0.05,
                       seed: int = 0) -> Optional[tuple]:
    """Percentile CI of ``stat`` from a circular block bootstrap. None below 5 points.

    Resampling contiguous blocks keeps the serial dependence that a plain
    bootstrap destroys, so overlapping-horizon series get honest widths.
    """
    a = _clean(x)
    n = len(a)
    if n < 5:
        return None
    # A block as long as the series resamples it whole: zero width, false certainty.
    block = min(block or default_block(n), max(1, n // 4))
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block))
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n] % n
    draws = np.array([stat(a[row]) for row in idx]) if stat is not np.mean else a[idx].mean(axis=1)
    lo, hi = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def newey_west_t(x: Sequence[float], lags: Optional[int] = None) -> Optional[float]:
    """t-statistic of the mean with a Newey-West (Bartlett) HAC standard error."""
    a = _clean(x)
    n = len(a)
    if n < 5:
        return None
    lags = int(lags if lags is not None else math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    e = a - a.mean()
    s = float(e @ e) / n
    for k in range(1, min(lags, n - 1) + 1):
        w = 1.0 - k / (lags + 1.0)
        s += 2.0 * w * float(e[k:] @ e[:-k]) / n
    if s <= 0:
        return None
    return float(a.mean() / math.sqrt(s / n))


def two_sided_p(t: Optional[float]) -> Optional[float]:
    """Normal-approximation p-value for a t-statistic."""
    return None if t is None else float(2.0 * (1.0 - _norm_cdf(abs(t))))


def _moments(r: np.ndarray):
    mu, sd = r.mean(), r.std(ddof=1)
    z = (r - mu) / sd
    return float(mu / sd), float((z ** 3).mean()), float((z ** 4).mean())


def probabilistic_sharpe(returns: Sequence[float], sr_benchmark: float = 0.0) -> Optional[float]:
    """P(true per-period Sharpe > ``sr_benchmark``), allowing for skew and kurtosis."""
    r = _clean(returns)
    if len(r) < 10 or r.std(ddof=1) == 0:
        return None
    sr, skew, kurt = _moments(r)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return None
    return _norm_cdf((sr - sr_benchmark) * math.sqrt(len(r) - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected best per-period Sharpe among ``n_trials`` skill-less trials."""
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1.0 - _EULER) * z1 + _EULER * z2)


def deflated_sharpe(returns: Sequence[float], n_trials: int,
                    sr_variance: Optional[float] = None) -> Optional[float]:
    """Probabilistic Sharpe against the best result luck alone would produce.

    ``n_trials`` is how many strategy variants were evaluated; ``sr_variance``
    the variance of their per-period Sharpe ratios (defaults to the sampling
    variance of a zero-skill Sharpe, 1/T). Above 0.95 is the usual bar.
    """
    r = _clean(returns)
    if len(r) < 10:
        return None
    var = sr_variance if sr_variance is not None else 1.0 / len(r)
    return probabilistic_sharpe(r, expected_max_sharpe(max(1, int(n_trials)), var))


def benjamini_hochberg(pvals: Sequence[Optional[float]], q: float = 0.05) -> List[Optional[bool]]:
    """Which hypotheses survive a false-discovery-rate of ``q`` (None stays None)."""
    present = [(i, p) for i, p in enumerate(pvals) if p is not None and math.isfinite(p)]
    out: List[Optional[bool]] = [None] * len(pvals)
    if not present:
        return out
    m = len(present)
    ranked = sorted(present, key=lambda ip: ip[1])
    cutoff = 0
    for k, (_, p) in enumerate(ranked, 1):
        if p <= q * k / m:
            cutoff = k
    for k, (i, _) in enumerate(ranked, 1):
        out[i] = k <= cutoff
    return out


@dataclass(frozen=True)
class MeanInference:
    """Everything worth quoting about the mean of a dependent series."""
    n: int
    mean: float
    t_newey_west: Optional[float]
    p_value: Optional[float]
    ci95_bootstrap: Optional[tuple]

    def to_dict(self) -> dict:
        return {"n": self.n, "mean": self.mean, "t_newey_west": self.t_newey_west,
                "p_value": self.p_value,
                "ci95_bootstrap": list(self.ci95_bootstrap) if self.ci95_bootstrap else None}


def infer_mean(x: Sequence[float], lags: Optional[int] = None, block: Optional[int] = None,
               seed: int = 0) -> Optional[MeanInference]:
    a = _clean(x)
    if len(a) < 5:
        return None
    t = newey_west_t(a, lags)
    return MeanInference(len(a), float(a.mean()), t, two_sided_p(t),
                         block_bootstrap_ci(a, block=block, seed=seed))
