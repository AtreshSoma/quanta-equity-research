"""A06: one final sizing function, applied after every transform.

``apply_allocation`` is the only writer of allocation / shares / position_pct.
Every write leaves either nothing, or a whole-share (or fractional) position
that is at least the capital-relative minimum and inside the position cap. A
one-share round-up happens only where the book sizes a name, never after a
later scale-down. Synthetic positions only.
"""
from __future__ import annotations

import random

import pytest

import SP_ELITE_PRO as sp
from engine import thresholds as T


class _Rec:
    def __init__(self, entry, stop=None, signal="BUY"):
        self.entry_price, self.stop_loss, self.signal = entry, (stop if stop is not None else entry * 0.92), signal
        self.allocation, self.shares, self.position_pct = 0.0, 0, 0.0


@pytest.fixture(autouse=True)
def whole_shares(monkeypatch):
    monkeypatch.setattr(sp.TradingConfig, "FRACTIONAL_SHARES", False)


def _cap(capital):
    return capital * (sp.TradingConfig.effective_max_position_pct(capital) + sp.TradingConfig.POSITION_CAP_BUFFER)


def test_every_write_is_empty_or_inside_the_minimum_and_the_cap():
    rng = random.Random(7)
    for _ in range(2000):
        capital = rng.choice([500.0, 2_500.0, 25_000.0, 1e6])
        rec = _Rec(entry=round(rng.uniform(5, 900), 2))
        got = sp.apply_allocation(rec, rng.uniform(0, capital * 0.5), capital)
        if got == 0:
            assert rec.shares == 0 and rec.position_pct == 0
            continue
        assert got == pytest.approx(rec.shares * rec.entry_price, abs=0.01)
        assert rec.shares == int(rec.shares) >= 1
        assert got >= sp.TradingConfig.effective_min_position_size(capital) - 0.01
        assert got <= _cap(capital) + 0.01


def test_a_scale_down_below_the_minimum_drops_the_position():
    rec = _Rec(entry=10.0)
    assert sp.apply_allocation(rec, 200.0, 25_000.0) == 200.0
    # e.g. Research halving or the mandate: $60 is under the $100 minimum here
    assert sp.apply_allocation(rec, 60.0, 25_000.0) == 0.0 and rec.shares == 0


def test_a_scale_down_below_one_share_is_never_rounded_back_up():
    rec = _Rec(entry=186.0, stop=168.7)
    assert sp.apply_allocation(rec, 92.0, 2_500.0) == 0.0
    assert sp.apply_allocation(rec, 92.0, 2_500.0, one_share=True) == pytest.approx(186.0)


def test_one_share_respects_the_per_trade_risk_ceiling():
    capital = 2_500.0
    # $450 share, 12% stop: $54 at risk is over the per-trade ceiling on $2,500
    rec = _Rec(entry=450.0, stop=396.0)
    assert 450.0 * 0.12 > capital * T.SIZING.MAX_RISK_PER_TRADE
    assert sp.apply_allocation(rec, 120.0, capital, one_share=True) == 0.0


def test_the_minimum_is_capital_relative_with_an_absolute_floor():
    f = sp.TradingConfig.effective_min_position_size
    assert f(2_500.0) == pytest.approx(T.SIZING.MIN_POSITION_PCT * 2_500.0)
    assert f(500.0) == pytest.approx(T.SIZING.MIN_POSITION_FLOOR)
    assert f(1e6) == pytest.approx(T.SIZING.MIN_POSITION_SIZE)
    assert sp.TradingConfig.min_position_size(2_500.0) == f(2_500.0), "one minimum, whole or fractional"
