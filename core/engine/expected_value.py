"""Probability-weighted expected value for a stop/target trade (shadow model v4).

The Research reward/risk gate compares a stop 2.75 x ATR away with a target that
is only 63/252 of the 12-month valuation upside, so almost nothing passes. It
also ignores that a trade has three outcomes, not two. This module replaces the
ratio with an expected value.

Model: the log price X_t is Brownian motion with drift ``mu`` and volatility
``sigma`` (both per session), X_0 = 0, watched for ``horizon`` sessions between
two absorbing barriers, up at ``a = ln(target/entry)`` and down at
``-b = ln(stop/entry)``. A trade ends one of three ways: the target is hit
first (gain ``target/entry - 1``), the stop is hit first (loss
``stop/entry - 1``), or neither is hit by the horizon (marked at
``exp(X_H) - 1``, averaged over the paths that stayed inside).

The probabilities are exact, not simulated. Shift the strip to (0, L), L = a+b.
The surviving density is an eigenfunction series in sin(n pi y / L), and the
integrals over y are closed form, so ``p_neither`` and the inside terminal
value are short sums. ``p_up`` is the infinite-horizon gambler-ruin answer
minus a series of fast-decaying terms (summing the flux directly converges
too slowly). Terms stop once exp(-rate x H) < 1e-12.

Every policy number (shrink, ATR-to-sigma ratio, cost, EV hurdle) is a
parameter. Only numerical tolerances live here.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

_FAR_SD = 8.5              # barriers this many sd away: nothing can reach them
_MAX_TERMS = 200_000
_EXP_CAP = 700.0           # keep np.exp finite


def _exp(x: np.ndarray) -> np.ndarray:
    return np.exp(np.minimum(x, _EXP_CAP))


def _ruin_up(a: float, b: float, theta: float) -> float:
    """P(hit +a before -b) with no time limit; theta = mu / sigma^2."""
    length = a + b
    if abs(2.0 * theta * length) < 1e-12:
        return b / length
    if theta < 0.0:
        return 1.0 - _ruin_up(b, a, -theta)
    return math.expm1(-2.0 * theta * b) / math.expm1(-2.0 * theta * length)


def _no_diffusion(a: float, b: float, mu: float, horizon: int) -> Dict[str, float]:
    """sigma == 0: the price just drifts, so the outcome is deterministic."""
    end = mu * horizon
    if end >= a:
        return {"p_up": 1.0, "p_down": 0.0, "p_neither": 0.0, "e_terminal_inside": 0.0}
    if end <= -b:
        return {"p_up": 0.0, "p_down": 1.0, "p_neither": 0.0, "e_terminal_inside": 0.0}
    return {"p_up": 0.0, "p_down": 0.0, "p_neither": 1.0, "e_terminal_inside": math.expm1(end)}


def barrier_outcomes(a: float, b: float, mu: float, sigma: float, horizon: int) -> Dict[str, float]:
    """Exact outcome probabilities for drifted Brownian motion in a strip.

    Returns ``p_up`` (target first), ``p_down`` (stop first), ``p_neither`` and
    ``e_terminal_inside`` = E[exp(X_H) - 1 | neither] (0.0 when p_neither ~ 0).
    """
    a, b, mu, sigma = float(a), float(b), float(mu), float(sigma)
    if horizon <= 0:
        return {"p_up": 0.0, "p_down": 0.0, "p_neither": 1.0, "e_terminal_inside": 0.0}
    if sigma <= 0.0:
        return _no_diffusion(a, b, mu, horizon)

    sd = sigma * math.sqrt(horizon)
    drift_total = mu * horizon
    # Both barriers many sd away: staying inside is certain, X_H is plain normal.
    if (a - drift_total) / sd > _FAR_SD and (b + drift_total) / sd > _FAR_SD:
        return {"p_up": 0.0, "p_down": 0.0, "p_neither": 1.0,
                "e_terminal_inside": math.expm1(drift_total + 0.5 * sd * sd)}

    length = a + b
    theta = mu / (sigma * sigma)
    c = mu * mu / (2.0 * sigma * sigma)
    n_terms = min(_MAX_TERMS, int(2.4 * length / sd) + 8)
    n = np.arange(1, n_terms + 1, dtype=float)
    k = n * math.pi / length
    sin_kb = np.sin(k * b)
    sign = np.where(n % 2 == 0, 1.0, -1.0)
    rate = c + 0.5 * sigma * sigma * k * k
    decay = -rate * horizon

    # p_neither = sum (2/L) sin(kb) k (e^{-theta b} - (-1)^n e^{theta a}) / (theta^2 + k^2) x e^decay
    w0 = (2.0 / length) * sin_kb * k / (theta * theta + k * k)
    p_neither = float(np.sum(w0 * (_exp(decay - theta * b) - sign * _exp(decay + theta * a))))

    # E[exp(X_H); neither]: the same sum with theta -> theta + 1 (exp(X) = e^{y-b}).
    t1 = theta + 1.0
    w1 = (2.0 / length) * sin_kb * k / (t1 * t1 + k * k)
    mass = float(np.sum(w1 * (_exp(decay - t1 * b) - sign * _exp(decay + t1 * a))))

    # p_up = gambler-ruin limit minus the flux that has not arrived by H.
    tail = (sigma * sigma * k / length) * sign * sin_kb * _exp(decay + theta * a) / rate
    p_up = _ruin_up(a, b, theta) + float(np.sum(tail))

    p_neither = min(1.0, max(0.0, p_neither))
    p_up = min(1.0 - p_neither, max(0.0, p_up))
    p_down = max(0.0, 1.0 - p_up - p_neither)
    e_inside = (mass / p_neither - 1.0) if p_neither > 1e-9 else 0.0
    if not math.isfinite(e_inside):
        e_inside = 0.0
    return {"p_up": p_up, "p_down": p_down, "p_neither": p_neither, "e_terminal_inside": e_inside}


def drift_from_valuation(entry: float, valuation_target: float, valuation_horizon: int,
                         shrink: float) -> float:
    """Per-session log drift implied by a valuation target, haircut by ``shrink``.

    ``shrink`` in [0, 1] discounts optimistic 12-month targets. Missing or
    non-positive inputs give 0.0 (no drift).
    """
    try:
        vals = [float(entry), float(valuation_target), float(valuation_horizon), float(shrink)]
    except (TypeError, ValueError):
        return 0.0
    if not all(map(math.isfinite, vals)) or not (vals[0] > 0 and vals[1] > 0 and vals[2] > 0):
        return 0.0
    return min(1.0, max(0.0, vals[3])) * math.log(vals[1] / vals[0]) / vals[2]


def sigma_from_atr(atr: float, price: float, atr_to_sigma: float) -> float:
    """Per-session sigma from ATR. ATR overstates close-to-close sigma, so the
    caller passes the ratio. Invalid inputs give 0.0."""
    try:
        vals = [float(atr), float(price), float(atr_to_sigma)]
    except (TypeError, ValueError):
        return 0.0
    if not all(map(math.isfinite, vals)) or not all(v > 0 for v in vals):
        return 0.0
    return (vals[0] / vals[1]) / vals[2]


def _invalid(reason: str) -> Dict[str, Any]:
    keys = ("p_target", "p_stop", "p_neither", "expected_return", "risk", "ev_r", "inputs")
    out: Dict[str, Any] = {k: None for k in keys}
    out.update({"passes": False, "reason": reason})
    return out


def assess(entry: float, stop: float, target: float, valuation_target: Optional[float], atr: float, *,
           horizon: int = 63, valuation_horizon: int = 252, shrink: float, atr_to_sigma: float,
           cost_frac: float, min_ev_r: float) -> Dict[str, Any]:
    """Expected return, risk and EV-in-R for one stop/target setup.

    ``expected_return`` is a fraction of entry net of the round-trip cost;
    ``risk`` is the stop distance plus that cost; ``ev_r`` is their ratio.
    """
    try:
        entry, stop, target = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return _invalid("entry, stop or target is missing")
    if not all(map(math.isfinite, (entry, stop, target))) or entry <= 0:
        return _invalid("entry price is not positive")
    if not (0 < stop < entry < target):
        return _invalid("levels must satisfy stop < entry < target")
    sigma = sigma_from_atr(atr, entry, atr_to_sigma)
    if sigma <= 0.0:
        return _invalid("volatility is unknown (ATR missing or zero)")

    # The valuation sets the expected growth of the PRICE; the barriers live in
    # log price, whose drift is that growth less sigma^2/2. Without the
    # correction a volatile name earns expected value from volatility alone; with
    # it a name with no upside is a fair game, and any stop and target nets -cost.
    mu = drift_from_valuation(entry, valuation_target, valuation_horizon, shrink) - 0.5 * sigma * sigma
    a, b = math.log(target / entry), -math.log(stop / entry)
    out = barrier_outcomes(a, b, mu, sigma, horizon)

    expected = (out["p_up"] * (target / entry - 1.0) + out["p_down"] * (stop / entry - 1.0)
                + out["p_neither"] * out["e_terminal_inside"] - cost_frac)
    risk = (entry - stop) / entry + cost_frac
    ev_r = expected / risk
    return {
        "p_target": out["p_up"], "p_stop": out["p_down"], "p_neither": out["p_neither"],
        "expected_return": expected, "risk": risk, "ev_r": ev_r,
        "passes": bool(ev_r >= min_ev_r and expected > 0),
        "inputs": {"mu": mu, "sigma": sigma, "a": a, "b": b, "horizon": horizon},
    }
