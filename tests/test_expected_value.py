"""Tests for the probability-weighted expected-value model (offline, fast)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from engine.expected_value import assess, barrier_outcomes, drift_from_valuation, sigma_from_atr


def test_symmetric_driftless_is_even_and_gambler_ruin():
    r = barrier_outcomes(0.1, 0.1, 0.0, 0.02, 63)
    assert r["p_up"] == pytest.approx(r["p_down"], abs=1e-9)
    r = barrier_outcomes(0.1, 0.3, 0.0, 0.05, 5000)   # H large: someone always exits
    assert r["p_neither"] < 1e-6
    assert r["p_up"] == pytest.approx(0.3 / 0.4, abs=1e-3)


@pytest.mark.parametrize("a,b,mu,sigma,h", [
    (0.12, 0.08, 0.0005, 0.02, 63),
    (0.10, 0.10, -0.0003, 0.015, 63),
    (0.20, 0.05, 0.001, 0.025, 40),
])
def test_matches_monte_carlo(a, b, mu, sigma, h):
    rng = np.random.default_rng(7)
    n, steps = 100_000, 2
    dt = 1.0 / steps
    d = sigma * sigma * dt
    x = np.zeros(n)
    state = np.zeros(n, dtype=int)          # 0 alive, 1 up, -1 down
    for _ in range(h * steps):
        x_new = x + mu * dt + sigma * math.sqrt(dt) * rng.standard_normal(n)
        # Brownian-bridge crossing probability between steps removes discretisation bias.
        pu = np.exp(-2.0 * np.maximum(a - x, 0) * np.maximum(a - x_new, 0) / d)
        pd = np.exp(-2.0 * np.maximum(x + b, 0) * np.maximum(x_new + b, 0) / d)
        alive = state == 0
        hit_up = alive & ((x_new >= a) | (rng.random(n) < pu))
        hit_dn = alive & ~hit_up & ((x_new <= -b) | (rng.random(n) < pd))
        state[hit_up], state[hit_dn] = 1, -1
        x = np.where(alive, x_new, x)
    inside = state == 0
    ex = barrier_outcomes(a, b, mu, sigma, h)
    assert ex["p_up"] == pytest.approx((state == 1).mean(), abs=0.01)
    assert ex["p_down"] == pytest.approx((state == -1).mean(), abs=0.01)
    assert ex["p_neither"] == pytest.approx(inside.mean(), abs=0.01)
    assert ex["e_terminal_inside"] == pytest.approx(np.expm1(x[inside]).mean(), abs=0.002)


@pytest.mark.parametrize("args", [(0.1, 0.1, 0.0, 0.02, 63), (0.3, 0.05, 0.002, 0.03, 100),
                                  (0.01, 0.01, -0.001, 0.05, 252), (0.5, 0.5, 0.0, 0.001, 10)])
def test_probabilities_sum_to_one(args):
    r = barrier_outcomes(*args)
    assert r["p_up"] + r["p_down"] + r["p_neither"] == pytest.approx(1.0, abs=1e-9)
    assert all(math.isfinite(v) for v in r.values())


def test_tiny_sigma_and_zero_drift_do_not_blow_up():
    r = barrier_outcomes(0.1, 0.1, 0.0, 1e-9, 63)
    assert r["p_neither"] == pytest.approx(1.0) and r["e_terminal_inside"] == pytest.approx(0.0, abs=1e-6)
    assert barrier_outcomes(0.1, 0.1, 0.01, 1e-6, 63)["p_up"] == 1.0
    # Series and the far-barrier shortcut agree where they meet.
    near = barrier_outcomes(1.0, 1.0, 0.0, 0.02, 63)
    assert near["e_terminal_inside"] == pytest.approx(math.expm1(0.5 * 0.02 ** 2 * 63), abs=1e-5)


def test_positive_drift_raises_p_up_monotonically():
    ups = [barrier_outcomes(0.12, 0.08, mu, 0.02, 63)["p_up"] for mu in np.linspace(-0.002, 0.002, 9)]
    assert all(x < y for x, y in zip(ups, ups[1:]))


def test_helpers():
    assert drift_from_valuation(100, 120, 252, 0.5) == pytest.approx(0.5 * math.log(1.2) / 252)
    assert drift_from_valuation(100, 120, 252, 0.0) == 0.0
    assert drift_from_valuation(0, 120, 252, 0.5) == 0.0
    assert drift_from_valuation(100, None, 252, 0.5) == 0.0
    assert sigma_from_atr(1.33, 65.27, 1.25) == pytest.approx(1.33 / 65.27 / 1.25)
    assert sigma_from_atr(0, 65.27, 1.25) == 0.0 and sigma_from_atr(1, -1, 1.25) == 0.0


KW = dict(shrink=0.5, atr_to_sigma=1.25, cost_frac=0.0011, min_ev_r=0.3)


@pytest.mark.parametrize("args", [(0, 62, 76, 76, 1.3), (65, 66, 76, 76, 1.3),
                                  (65, 62, 60, 76, 1.3), (65, 62, 76, 76, 0.0), (65, 62, 76, 76, None)])
def test_degenerate_inputs_fail_with_reason(args):
    r = assess(*args, **KW)
    assert r["passes"] is False and isinstance(r["reason"], str) and r["reason"]
    assert r["ev_r"] is None and r["expected_return"] is None


def test_realistic_case():
    r = assess(65.27, 62.22, 76.39, 76.39, 1.33, **KW)
    print(r)
    nums = [r["p_target"], r["p_stop"], r["p_neither"], r["expected_return"], r["risk"], r["ev_r"],
            *r["inputs"].values()]
    assert all(math.isfinite(v) for v in nums)
    assert r["p_target"] + r["p_stop"] + r["p_neither"] == pytest.approx(1.0, abs=1e-9)


def test_no_upside_is_a_fair_game_net_of_cost():
    """Review 2026-10-29: with drift taken as a log-price drift, a volatile name
    earned expected value from volatility alone (NVDA: 3% chance of the target,
    +0.57R). With the valuation at the price, any stop and target nets -cost."""
    for atr in (1.0, 3.0, 6.0):                      # low to very high volatility
        r = assess(100.0, 90.0, 130.0, 100.0, atr, shrink=1.0, atr_to_sigma=1.25,
                   cost_frac=0.0011, min_ev_r=0.0)
        assert r["expected_return"] == pytest.approx(-0.0011, abs=2e-4), atr
        assert r["passes"] is False
