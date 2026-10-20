"""Engine fixes, 2026-10-19 review.

1. A funded pick's published (binding) levels clear the funding hurdle the
   product promises: net reward-to-risk at least RR.SCANNER_MIN (1.8), not the
   Research engine's lower RR.RESEARCH_MIN.
2. Insider buying never relaxes the reward-to-risk hurdle ("never loosen a
   funding threshold"); it may still relax the upside and score hurdles.
"""
from __future__ import annotations

import sys

from _paths import ensure_paths

ensure_paths()

import pytest  # noqa: E402
from engine import thresholds as T  # noqa: E402

FUNDING_RR = max(T.RR.SCANNER_MIN, T.RR.RESEARCH_MIN)


def test_funded_pick_needs_the_funding_rr_on_its_published_levels(monkeypatch):
    """R:R 1.60 clears the Research hurdle (1.5) but not the funding hurdle (1.8):
    the pick is not funded, and the remedy names the 1.80 it needs."""
    from test_binding_levels import _install_research, _hst_like_pick
    from test_elite_pro_review2_fixes import _bare_system
    assert T.RR.RESEARCH_MIN < 1.60 < FUNDING_RR
    _install_research(monkeypatch, "BUY", (22.56, 21.86, 23.47, 1.60, 63))
    out = _bare_system()._deep_confirm_final_picks([_hst_like_pick()])[0]
    assert out.allocation == 0
    assert (out.final_action or "").upper() not in ("BUY", "STRONG_BUY", "ACCUMULATE")
    assert f"needs {FUNDING_RR:.2f}" in (out.binding_levels.get("remedy") or "")


def test_insider_buying_does_not_relax_the_rr_hurdle():
    sp = sys.modules.get("SP_ELITE_PRO")
    if sp is None:
        import SP_ELITE_PRO as sp  # noqa: N813
    obj = object.__new__(sp.QuantaEliteTradingSystem)
    entry = 100.0
    out = sp.QuantaEliteTradingSystem._apply_investment_committee_policy(
        obj, signal=sp.SignalType.ACCUMULATE.value, score=7.0, entry=entry,
        target=entry * 1.20, stop=95.0, risk_reward=FUNDING_RR - 0.1, risk_grade="B",
        rsi=50.0, insider_sentiment="BULLISH", insider_note="Net insider buying",
        insider_relax_strength=1.0)
    assert out[0] == sp.SignalType.HOLD.value


# --- one stop rule for both engines (owner decision 2026-10-19) -------------

def _research_setup(atr, support):
    import TradingAnalysis_ELITE as ta
    val = ta.ValuationMetrics(target_base=130.0, target_aggressive=140.0)
    tech = ta.TechnicalIndicators(atr=atr, support_level=support, resistance_level=0.0, rsi=50.0)
    return ta.TradeSetupGenerator().generate(100.0, ta.Signal.BUY, tech, ta.RiskMetrics(), 60.0,
                                             valuation=val)


def test_research_stop_uses_the_scanner_atr_multiple():
    """No nearby support: the stop sits LEVELS.STOP_ATR_MULTIPLIER ATRs below entry,
    the same rule the Picks scanner uses (it used to clamp at 1.5 to 3 ATR)."""
    ts = _research_setup(atr=2.0, support=0.0)
    assert ts.entry_price - ts.stop_loss == pytest.approx(T.LEVELS.STOP_ATR_MULTIPLIER * 2.0, rel=1e-6)


def test_research_stop_extends_under_support_within_one_atr():
    """Support just below the volatility stop: the stop moves under it (support x 0.99),
    as in the scanner; a support far below is ignored."""
    atr = 2.0
    vol_stop = None
    ts = _research_setup(atr=atr, support=0.0)
    vol_stop = ts.stop_loss
    near = vol_stop - 0.5 * atr
    assert _research_setup(atr=atr, support=near).stop_loss == pytest.approx(near * 0.99)
    far = vol_stop - 3 * atr
    assert _research_setup(atr=atr, support=far).stop_loss == pytest.approx(vol_stop)


# --- one share may not risk more than 1% of a small account ------------------

def test_one_share_is_allowed_only_within_the_one_share_risk_cap():
    """$2,500 account, one share at $300. A $20 stop distance risks 0.8% (allowed);
    a $40 distance risks 1.6%: under the old 2% ceiling it was funded, now the
    name is skipped (the page names the capital it would need)."""
    import types as _t
    import SP_ELITE_PRO as sp
    cfg = sp.TradingConfig
    assert T.SIZING.ONE_SHARE_MAX_RISK == 0.01
    ok = _t.SimpleNamespace(entry_price=300.0, stop_loss=280.0, signal="BUY")
    too_risky = _t.SimpleNamespace(entry_price=300.0, stop_loss=260.0, signal="BUY")
    assert cfg.one_share_allowed(ok, 2500.0)
    assert not cfg.one_share_allowed(too_risky, 2500.0)
    # A larger account carries the same share within the cap.
    assert cfg.one_share_allowed(too_risky, 5000.0)


# --- honest evidence labels (owner decision 2026-10-19) ----------------------

def _reg(research_status):
    return {"strategies": {"elite.research.v2": {
        "status": research_status, "summary": "Composite score does not predict forward returns."}}}


def test_picks_evidence_follows_the_research_model_that_confirms_it():
    """The scanner composite has no test of its own; a funded Pick must pass the
    Research model, so the Pick carries that model's NO_EDGE result."""
    from engine.evidence import ELITE_PRO, EvidenceStatus, get_evidence
    ev = get_evidence(ELITE_PRO, _reg("NO_EDGE"))
    assert ev.status is EvidenceStatus.NO_EDGE
    assert ev.strategy == ELITE_PRO
    assert "elite.research.v2" in ev.summary and "does not predict" in ev.summary
    # An untested governor leaves the scanner UNTESTED, never better.
    assert get_evidence(ELITE_PRO, {"strategies": {}}).status is EvidenceStatus.UNTESTED


def test_governed_no_edge_never_permits_a_recommendation():
    """The governing result is carried as-is: a NO_EDGE Research model keeps a
    Pick from showing as a recommendation (and from reaching Strong Buy)."""
    from engine.evidence import ELITE_PRO, get_evidence
    ev = get_evidence(ELITE_PRO, _reg("NO_EDGE"))
    assert not ev.status.permits_recommendation


def test_evidence_text_has_no_em_dash_and_no_invalidated_cagr():
    from engine.evidence import _STATUS_TEXT, load_registry
    assert not any(chr(0x2014) in t for t in _STATUS_TEXT.values())
    reg = load_registry()
    text = str(reg)
    assert "CAGR 0.76%" not in text and chr(0x2014) not in text


def test_scanner_binds_research_levels_with_india_levies():
    """The scanner's published Research levels charge an NSE/BSE listing the same
    statutory levies the Research engine's cost model does."""
    import inspect
    import SP_ELITE_PRO as sp
    import TradingAnalysis_ELITE as ta
    src = inspect.getsource(sp)
    assert "cost_frac += 2 * float(_T.LEVELS.RESEARCH_INDIA_STATUTORY_PER_SIDE)" in src
    us = ta.TradeSetupGenerator.round_trip_cost_pct("AAPL")
    india = ta.TradeSetupGenerator.round_trip_cost_pct("RELIANCE.NS")
    assert india - us == pytest.approx(2 * T.LEVELS.RESEARCH_INDIA_STATUTORY_PER_SIDE * 100)
