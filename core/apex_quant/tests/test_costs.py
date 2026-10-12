"""Cost-model tests: monotonicity and correct decomposition.

Costs must rise with volatility, with order size relative to ADV, and with the
quoted spread. Round-trip must be twice the single side.
"""

from __future__ import annotations

from core.apex_quant.costs import CostModel


def test_round_trip_is_double_side():
    cm = CostModel()
    side = cm.side_cost_pct(100.0, 100, atr_pct=0.02, adv_dollars=5e7)
    rt = cm.round_trip_pct(100.0, 100, atr_pct=0.02, adv_dollars=5e7)
    assert abs(rt - 2 * side) < 1e-12


def test_cost_increases_with_volatility():
    cm = CostModel()
    low = cm.side_cost_pct(100.0, 1000, atr_pct=0.01, adv_dollars=1e6)
    high = cm.side_cost_pct(100.0, 1000, atr_pct=0.05, adv_dollars=1e6)
    assert high > low


def test_cost_increases_with_participation():
    cm = CostModel()
    liquid = cm.side_cost_pct(100.0, 1000, atr_pct=0.02, adv_dollars=1e9)
    illiquid = cm.side_cost_pct(100.0, 1000, atr_pct=0.02, adv_dollars=1e6)
    assert illiquid > liquid


def test_cost_increases_with_spread():
    tight = CostModel(half_spread_bps=1.0)
    wide = CostModel(half_spread_bps=10.0)
    a = tight.side_cost_pct(100.0, 100, atr_pct=0.02, adv_dollars=5e7)
    b = wide.side_cost_pct(100.0, 100, atr_pct=0.02, adv_dollars=5e7)
    assert b > a


def test_zero_shares_zero_cost():
    cm = CostModel()
    assert cm.side_cost_pct(100.0, 0, atr_pct=0.02, adv_dollars=5e7) == 0.0
