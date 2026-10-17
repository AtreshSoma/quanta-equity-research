"""Regression tests for the 2026-10-17 audit: scoring and book items.

#6 one owner per metric, #7 technical thesis, #10 missing data, #11 risk
events, #12 catalyst fallback, #13 oversold door, #14 conviction floor.
None of them touch the network.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import SP_ELITE_PRO as pro


# --- #6 / #10: one owner per metric; missing data is unknown, not bad -------

def _fm(**kw):
    fm = pro.FundamentalMetrics()
    for k, v in kw.items():
        setattr(fm, k, v)
        fm.data_available[k] = True
    return fm


def test_roic_gross_margin_and_leverage_are_not_scored_as_fundamentals():
    base = dict(revenue_growth=0.12, earnings_growth=0.18, profit_margin=0.12, roe=0.16)
    plain = pro.ScoringEngine.calculate_fundamental_score(_fm(**base))
    rich = pro.ScoringEngine.calculate_fundamental_score(
        _fm(**base, roic=0.30, gross_margin=0.70, debt_to_equity=0.1))
    # Those three belong to quality_score; they used to be paid here too.
    assert rich == pytest.approx(plain)


def test_roe_is_not_a_valuation_input():
    cheap = dict(pe_ratio=15.0, peg_ratio=0.9, pb_ratio=2.0, ps_ratio=1.5, free_cash_flow=1e9)
    a = pro.ScoringEngine.calculate_value_score(_fm(**cheap))
    b = pro.ScoringEngine.calculate_value_score(_fm(**cheap, roe=0.40))
    assert a == pytest.approx(b)


def test_a_complete_strong_fundamental_profile_reaches_the_top_of_the_scale():
    top = _fm(revenue_growth=0.40, earnings_growth=0.40, profit_margin=0.30, roe=0.30)
    assert pro.ScoringEngine.calculate_fundamental_score(top) == pytest.approx(10.0)


def test_missing_margins_do_not_score_as_zero_percent_margins():
    rm = pro.RiskMetrics(volatility=0.25, sharpe_ratio=1.2, max_drawdown=-0.20)
    known_zero = _fm(roic=0.18, debt_to_equity=0.5, gross_margin=0.0, operating_margin=0.0)
    unknown = _fm(roic=0.18, debt_to_equity=0.5)            # margins not supplied
    assert (pro.ScoringEngine.calculate_quality_score(unknown, rm)
            > pro.ScoringEngine.calculate_quality_score(known_zero, rm))


def test_uncomputed_risk_metrics_earn_no_drawdown_points():
    fm = _fm(roic=0.10, gross_margin=0.30, operating_margin=0.10, debt_to_equity=1.0)
    blank = pro.RiskMetrics()                 # volatility 0: "could not compute"
    calm = pro.RiskMetrics(volatility=0.20, sharpe_ratio=0.0, max_drawdown=-0.10)
    # A blank RiskMetrics' 0.0 drawdown used to earn the full 2.0 points.
    assert (pro.ScoringEngine.calculate_quality_score(fm, blank)
            < pro.ScoringEngine.calculate_quality_score(fm, calm))


def test_value_score_ignores_unsupplied_ratios():
    s = pro.ScoringEngine.calculate_value_score(_fm(pb_ratio=1.0))
    # Judged on P/B alone and shrunk toward neutral -- not punished for three
    # ratios the provider never sent.
    assert 4.0 < s <= 10.0


# --- #7: technical score has one thesis -------------------------------------

def _ti(**kw):
    ti = pro.TechnicalIndicators()
    ti.current_price, ti.sma_20, ti.sma_50, ti.sma_200 = 100.0, 98.0, 95.0, 90.0
    ti.rsi, ti.stoch_k, ti.bb_position = 55.0, 50.0, 0.6
    for k, v in kw.items():
        setattr(ti, k, v)
    return ti


def test_heavy_volume_on_a_falling_week_is_not_confirmation():
    up = pro.ScoringEngine.calculate_technical_score(_ti(volume_ratio=2.5, momentum_1w=0.03))
    down = pro.ScoringEngine.calculate_technical_score(_ti(volume_ratio=2.5, momentum_1w=-0.03))
    assert up - down == pytest.approx(1.5)


def test_oversold_stochastic_earns_nothing():
    mid = pro.ScoringEngine.calculate_technical_score(_ti(stoch_k=50.0))
    oversold = pro.ScoringEngine.calculate_technical_score(_ti(stoch_k=10.0))
    assert mid - oversold == pytest.approx(1.0)


# --- #11: risk events are not netted away ------------------------------------

def test_a_bearish_event_drops_positive_adjustments():
    adj = [("catalyst", -1.0), ("insider", +0.8), ("analyst_bullish", +0.3)]
    net, _kept, dropped = pro.QuantaEliteTradingSystem.bounded_adjustment(adj, 1.0, True)
    assert net == pytest.approx(-1.0)
    assert dropped == [("insider", 0.8), ("analyst_bullish", 0.3)]
    # Without an event the same positives still net and clip as before.
    net2, _k, d2 = pro.QuantaEliteTradingSystem.bounded_adjustment(
        [("insider", 0.8), ("analyst_bullish", 0.3)], 1.0, False)
    assert net2 == pytest.approx(1.0) and d2 == []


# --- #12: catalyst tiers fall through; bearish news counts in every tier -----

class _Boom:
    def __init__(self, *a, **k):
        raise RuntimeError("engine broke")


class _BearScanner:
    def scan_headlines(self, ticker, headlines):
        return ["c"] if headlines else []

    def generate_signal(self, ticker, catalysts, volume_ratio):
        return SimpleNamespace(recommended_action="AVOID", aggregate_weight=0.3)


def _catalyst(monkeypatch, *, institutional, basic):
    monkeypatch.setattr(pro, "HAS_INSTITUTIONAL_CATALYST", institutional is not None)
    monkeypatch.setattr(pro, "InstitutionalCatalystEngine", institutional)
    monkeypatch.setattr(pro, "HAS_CATALYST", True)
    monkeypatch.setattr(pro, "CatalystAwareSignalGenerator", None)
    monkeypatch.setattr(pro, "CatalystScanner", basic)
    sysm = object.__new__(pro.QuantaEliteTradingSystem)
    sysm._company_news = lambda t, s: [{"title": "Acme contract terminated, shares plunge"}]
    boost, _sig = sysm._fetch_catalyst_boost(
        ticker="ACME", stock=None, info={"shortName": "Acme"},
        hist=pd.DataFrame({"Volume": [1e6] * 30}), current_price=10.0,
        market_cap=1e9, avg_volume=1e6, is_indian_stock=False)
    return boost


def test_a_failing_institutional_engine_falls_through_to_the_basic_scanner(monkeypatch):
    assert _catalyst(monkeypatch, institutional=_Boom, basic=_BearScanner) < 0


def test_the_basic_scanner_scores_bearish_news(monkeypatch):
    assert _catalyst(monkeypatch, institutional=None, basic=_BearScanner) == pytest.approx(-0.75)


# --- #13: the oversold door needs the same risk floor as BUY -----------------

def test_negative_sharpe_oversold_bounce_is_not_a_buy():
    from test_elite_pro_review2_fixes import _bare_system
    sysm = _bare_system(regime="BULL")
    sysm.current_scan_type = "standard"
    ti = _ti(rsi=30.0, momentum_1w=0.02, macd_histogram=0.1, momentum_1m=-0.12,
             volume_ratio=1.0, sma_20=99.0)
    rm = pro.RiskMetrics(volatility=0.35, sharpe_ratio=-0.6)
    sig = sysm._determine_signal_regime_aware(9.5, ti, rm)
    assert sig not in (pro.SignalType.BUY.value, pro.SignalType.STRONG_BUY.value)


# --- #14: the conviction floor binds even when it empties the pool -----------

def test_conviction_floor_that_empties_the_pool_funds_nothing(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    picks = [_pick("AAA", "Tech", 8.0, seed=1), _pick("BBB", "Health", 7.9, seed=2)]
    for p in picks:
        p.conviction = 1                       # below every floor
    sysm.scan_results = picks
    sysm._apply_event_gate = lambda c, **k: list(c)
    recs = sysm.get_top_recommendations(max_positions=5)
    assert not [r for r in recs if float(getattr(r, "allocation", 0) or 0) > 0]
