"""A12: "closest to qualifying" names, by distance to the first failing gate.

Only names that reached the book are candidates (per-name rejects have no
row). Each is placed at its first failing measurable gate in book order, with
the exact value it needs: the applied threshold, and for reward to risk the
entry and target prices from ``engine.levels.rr_triggers`` on its published
levels and costs. Rule-based blocks (corporate events, a banned grade) have no
distance and are left out. Synthetic rows only.
"""
from __future__ import annotations

import pytest

import SP_ELITE_PRO as sp
from engine import levels as L
from engine import thresholds as T


def _system(monkeypatch, rows):
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep
    from engine import corporate_events as ce
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear") for s in symbols}
    sysm.scan_results = rows
    published = sysm.get_top_recommendations(max_positions=5)
    return sysm, published


def _row(ticker, score=8.0, target=115.0, stop=95.0, signal="BUY", conviction=4, grade="B", seed=1):
    from test_elite_pro_review2_fixes import _pick
    p = _pick(ticker, f"S{seed}", score, stop=stop, signal=signal, seed=seed)
    p.price_target = target
    cost = p.transaction_cost_bps / 1e4
    p.risk_reward_ratio = L.net_rr(100.0, stop, target, cost)
    p.conviction, p.risk_grade = conviction, grade
    return p


def test_reward_to_risk_shortfall_names_the_exact_prices(monkeypatch):
    near = _row("NEAR", target=110.0, stop=95.0, seed=1)          # R:R ~1.9 -> ok? keep under 1.8 below
    near.price_target = 108.5
    cost = near.transaction_cost_bps / 1e4
    near.risk_reward_ratio = L.net_rr(100.0, 95.0, 108.5, cost)
    assert near.risk_reward_ratio < T.RR.SCANNER_MIN
    good = _row("GOOD", target=120.0, stop=95.0, seed=2)
    sysm, published = _system(monkeypatch, [good, near])
    rows = sysm.closest_to_qualifying({r.ticker for r in published})
    hit = next(r for r in rows if r["ticker"] == "NEAR")
    assert hit["gate"] == "risk_reward"
    assert hit["needed"] == pytest.approx(T.RR.SCANNER_MIN)
    trig = L.rr_triggers(100.0, 95.0, 108.5, cost, T.RR.SCANNER_MIN)
    assert hit["entry_needed"] == pytest.approx(round(trig["entry_needed"], 2))
    assert hit["target_needed"] == pytest.approx(round(trig["target_needed"], 2))
    assert "GOOD" not in {r["ticker"] for r in rows}, "published names are not 'closest'"


def test_ordering_cap_and_rule_blocks(monkeypatch):
    rows = [_row(f"R{i}", target=100.0 + 7.0 + 0.2 * i, stop=95.0, seed=i) for i in range(T.BOOK.CLOSEST_ROWS + 2)]
    banned = _row("BANF", target=106.0, grade="F", seed=40)
    weak = _row("WEAK", score=4.0, signal="HOLD", seed=41)
    weak.final_action = "WATCHLIST"
    sysm, published = _system(monkeypatch, rows + [banned, weak])
    out = sysm.closest_to_qualifying({r.ticker for r in published})
    assert len(out) == T.BOOK.CLOSEST_ROWS
    gaps = [r["shortfall"] for r in out]
    assert gaps == sorted(gaps), "nearest first"
    assert "BANF" not in {r["ticker"] for r in out}, "a banned grade has no distance"


def test_a_low_score_watch_name_is_measured_against_the_accumulate_floor(monkeypatch):
    weak = _row("WEAK", score=5.0, signal="HOLD", seed=41)
    weak.final_action = "WATCHLIST"
    sysm, published = _system(monkeypatch, [weak])
    hit = sysm.closest_to_qualifying(set())[0]
    assert hit["gate"] == "actionable" and hit["needed"] == pytest.approx(T.SCORE.ACCUMULATE)
    assert hit["now"] == pytest.approx(5.0)
