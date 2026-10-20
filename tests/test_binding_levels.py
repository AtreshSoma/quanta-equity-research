"""Audit F2: publish the levels that bind the decision.

HST showed R:R 2.25 (the scanner's levels) next to a binding reason quoting
1.23 (the Research engine's levels). Owner decision Q1: the Research engine's
levels govern and are the ones shown. These tests pin:

* one published level set per pick, computed in the engine (entry zone too);
* the published R:R equals the R:R in the binding reason;
* price triggers that reproduce the engine's own net-of-cost R:R;
* the scanner's levels survive only as a labelled reference.
"""
from __future__ import annotations

import sys
import types

import pytest

from _paths import ensure_paths

ensure_paths()

from engine import levels as L  # noqa: E402
from engine import thresholds as T  # noqa: E402


def net_rr(entry, stop, target, k):
    c = entry * k
    return max(0.0, target - entry - c) / (entry - stop + c)


# ── the level set ────────────────────────────────────────────────────────────

def test_published_levels_carry_an_engine_entry_zone_and_2dp_rr():
    lv = L.publish_levels("research", entry=22.56, stop=21.86, target=23.47, rr=1.2345,
                          atr=0.43, cost_frac=0.0011, horizon_days=63, target_basis="pro-rated")
    assert lv["entry_zone_high"] == 22.56
    assert lv["entry_zone_low"] == pytest.approx(22.56 - T.LEVELS.ENTRY_ZONE_ATR_MULT * 0.43, abs=0.005)
    assert lv["risk_reward"] == 1.23
    assert lv["rr_basis"] == "net of costs at the top of the entry zone"
    assert lv["source"] == "research"


@pytest.mark.parametrize("entry,stop,target,k,m", [
    (22.56, 21.86, 23.47, 0.0011, 1.5),     # HST's Research levels
    (99.65, 96.66, 109.80, 0.005, 1.8),     # LNTH's scanner levels
    (338.24, 313.17, 380.0, 0.003, 1.5),
])
def test_triggers_reproduce_the_engine_rr_exactly(entry, stop, target, k, m):
    trig = L.rr_triggers(entry, stop, target, k, m)
    # Raising the target to target_needed (entry fixed) lands exactly on m.
    assert net_rr(entry, stop, trig["target_needed"], k) == pytest.approx(m, abs=1e-6)
    # Buying at entry_needed (stop and target fixed) lands exactly on m.
    assert net_rr(trig["entry_needed"], stop, target, k) == pytest.approx(m, abs=1e-6)


def test_hst_triggers_from_its_research_levels():
    # The audit quoted these as approximately $22.40 / $23.66. Solved exactly
    # from the engine's net-of-cost formula they are $22.48 / $23.67 (the
    # parametrised test above proves both land on R:R 1.50).
    trig = L.rr_triggers(22.56, 21.86, 23.47, 0.0011, T.RR.RESEARCH_MIN)
    assert trig["entry_needed"] < 22.56 < trig["target_needed"]
    assert round(trig["entry_needed"], 2) == 22.48
    assert round(trig["target_needed"], 2) == 23.67


def test_remedy_names_the_gate_that_fired_and_its_trigger():
    lv = L.publish_levels("research", entry=22.56, stop=21.86, target=23.47, rr=1.23,
                          atr=0.43, cost_frac=0.0011, horizon_days=63, target_basis="pro-rated")
    text = L.rr_remedy(lv, T.RR.RESEARCH_MIN, gate="Research")
    assert text.startswith("Research R:R 1.23 needs 1.50")
    assert "spot at or below $22.48" in text and "63-session target at or above $23.67" in text


# ── the binding set on a deep-confirmed pick ────────────────────────────────

def _hst_like_pick():
    import SP_ELITE_PRO as sp
    from test_elite_pro_review2_fixes import _pick
    p = _pick("HST", "RealEstate", 8.0, seed=2)
    p.entry_price, p.stop_loss, p.price_target = 22.51, 21.33, 25.39
    p.risk_reward_ratio = 2.25
    p.technical_indicators.atr = 0.43
    sp.apply_allocation(p, 157.57, 100_000.0)
    return p


def _install_research(monkeypatch, verdict, setup, codes=(), reasons=()):
    class _TS:
        levels_available = True
        entry_price, stop_loss, horizon_target, risk_reward_ratio, horizon_days = setup
        target_base = 26.21
        valuation_horizon_days = 252

    class _Tech:
        atr = 0.43

    class _Res:
        final_action = verdict
        final_action_reasons = list(reasons)
        final_action_codes = list(codes)
        data_status = "ok"
        data_status_reason = ""
        trade_setup = _TS()
        technical = _Tech()

    class _Deep:
        def analyze(self, ticker, account_size=0, fast_mode=False, include_insider=False):
            return _Res()

    mod = types.ModuleType("TradingAnalysis_ELITE")
    mod.QuantaEliteAnalyzer = _Deep
    from engine.types import DecisionInputs
    mod.build_reconciliation_inputs = lambda res: DecisionInputs(
        ticker="HST", composite_score=0, risk_grade="B", risk_reward_ratio=0,
        current_price=22.5, stop_loss=0, target_price=0)
    mod.TradeSetupGenerator = type("TSG", (), {"round_trip_cost_pct": staticmethod(lambda: 0.11)})
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", mod)


def test_binding_levels_are_published(monkeypatch):
    """Audit 9.2: when RESEARCH_DECISION_CAP binds on LOW_REWARD_RISK, the card's
    published R:R equals the R:R in the binding reason."""
    from test_elite_pro_review2_fixes import _bare_system
    _install_research(monkeypatch, "WATCHLIST", (22.56, 21.86, 23.47, 1.2345, 63),
                      codes=["LOW_REWARD_RISK"],
                      reasons=["Composite score 65.8 → candidate BUY", "R/R 1.23 below min 1.50 → WATCHLIST"])
    sysm = _bare_system()
    pick = _hst_like_pick()
    out = sysm._deep_confirm_final_picks([pick])[0]
    bind = out.binding_levels
    assert bind["source"] == "research"
    reason_rr = float(next(r for r in out.final_action_reasons if "below min" in r).split("R/R ")[1].split()[0])
    assert out.risk_reward_ratio == bind["risk_reward"] == reason_rr == 1.23
    assert (out.entry_price, out.stop_loss, out.price_target) == (22.56, 21.86, 23.47)
    # The scanner's levels survive only as the labelled reference.
    assert out.scanner_levels["risk_reward"] == 2.25 and out.scanner_levels["source"] == "scanner"
    assert bind["triggers"]["entry_needed"] < 22.56 < bind["triggers"]["target_needed"]
    # The remedy is keyed to the gate that fired, with prices from the binding set.
    assert bind["remedy"].startswith("Research R:R 1.23 needs 1.80")
    assert out.allocation == 0


def test_agreed_pick_is_sized_on_the_binding_stop(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system
    _install_research(monkeypatch, "BUY", (22.56, 21.86, 24.60, 2.80, 63))
    sysm = _bare_system()
    pick = _hst_like_pick()
    before = pick.allocation
    out = sysm._deep_confirm_final_picks([pick])[0]
    scan_stop_pct = (22.51 - 21.33) / 22.51
    research_stop_pct = (22.56 - 21.86) / 22.56
    assert out.binding_levels["source"] == "research"
    # allocation x stop distance = risk budget: a tighter stop buys more, a wider
    # one less. Whole shares at the Research entry (the webapp's share policy).
    budget = before * scan_stop_pct / research_stop_pct
    assert out.shares == int(budget // 22.56)
    assert out.allocation == pytest.approx(out.shares * 22.56)
    assert out.stop_loss == 21.86


def test_without_research_levels_the_scanner_set_binds_and_says_so(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system
    monkeypatch.setenv("QUANTA_NO_DEEP_CONFIRM", "1")
    sysm = _bare_system()
    pick = _hst_like_pick()
    out = sysm._deep_confirm_final_picks([pick])[0]
    assert out.binding_levels["source"] == "scanner"
    assert out.binding_levels["label"] == "Scanner levels (Research check not run)"
    assert out.risk_reward_ratio == out.binding_levels["risk_reward"]
