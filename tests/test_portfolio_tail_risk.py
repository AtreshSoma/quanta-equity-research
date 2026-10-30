"""Tail-risk metrics (core/portfolio/analytics.tail_risk) and the repaired
PortfolioRiskManager VaR. Synthetic tickers and deterministic returns only."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

from portfolio import analytics as an  # noqa: E402
from portfolio_risk_manager import PortfolioRiskManager  # noqa: E402


def _dates(n, start="2025-01-01"):
    return pd.bdate_range(start, periods=n)


def _spiky(n=100, calm=0.001, spikes=(-0.02, -0.03, -0.04, -0.05, -0.06), start="2025-01-01"):
    """Calm days plus five known bad days, spread through the sample."""
    vals = np.full(n, calm)
    for i, s in enumerate(spikes):
        vals[10 + i * 15] = s
    return pd.Series(vals, index=_dates(n, start))


def _h(sym, value, beta=None):
    return an.Holding(symbol=sym, quantity=1, value=value, beta=beta)


# ---------------------------------------------------------------- analytics

def test_cvar_is_at_least_var_and_matches_hand_value():
    rets = pd.DataFrame({"AAA": _spiky()})
    t = an.tail_risk([_h("AAA", 1000.0)], 1000.0, rets)
    assert t["reason"] is None and t["days"] == 100
    # five worst days are -2..-6%; tail (<= 5% quantile) is exactly those five.
    assert t["cvar_pct"] == pytest.approx(0.04)
    assert t["cvar_pct"] >= t["var_pct"] > 0
    assert t["cvar_value"] == pytest.approx(40.0)
    assert t["var_value"] == pytest.approx(t["var_pct"] * 1000.0)


def test_worst_21_session_window_compounds():
    vals = np.zeros(100)
    vals[40:61] = -0.01                      # exactly 21 straight down days
    rets = pd.DataFrame({"AAA": pd.Series(vals, index=_dates(100))})
    w = an.tail_risk([_h("AAA", 500.0)], 500.0, rets)["worst_window"]
    assert w["sessions"] == 21
    assert w["return"] == pytest.approx(0.99 ** 21 - 1)
    assert w["value"] == pytest.approx((0.99 ** 21 - 1) * 500.0)
    assert w["end"] == str(_dates(100)[60].date())


def test_misaligned_index_gives_reason_not_nan():
    # BBB sits on a RangeIndex (the shape the old synthetic fallback had).
    rets = pd.concat([_spiky(), pd.Series(np.full(100, 0.001), name="BBB").reset_index(drop=True)], axis=1)
    rets.columns = ["AAA", "BBB"]
    t = an.tail_risk([_h("AAA", 500.0), _h("BBB", 500.0)], 1000.0, rets)
    assert t["var_pct"] is None and t["cvar_pct"] is None and t["worst_window"] is None
    assert "shared trading days" in t["reason"]
    # disjoint but both date-indexed
    a = _spiky(80, start="2024-01-01")
    b = pd.Series(np.full(80, 0.001), index=_dates(80, "2026-01-01"))
    rets2 = pd.DataFrame({"AAA": a, "BBB": b})
    t2 = an.tail_risk([_h("AAA", 1.0 * 500), _h("BBB", 500.0)], 1000.0, rets2)
    assert t2["var_pct"] is None and t2["reason"]


def test_coverage_excludes_thin_history_and_is_reported():
    short = pd.Series(np.full(10, 0.01), index=_dates(10))
    rets = pd.DataFrame({"AAA": _spiky(), "BBB": short})
    t = an.tail_risk([_h("AAA", 600.0), _h("BBB", 400.0)], 1000.0, rets)
    assert t["coverage"] == pytest.approx(0.6)
    assert t["var_value"] == pytest.approx(t["var_pct"] * 600.0)   # dollars on the measured part only
    assert math.isfinite(t["var_pct"])


def test_no_returns_still_gives_beta_scenarios():
    hs = [_h("AAA", 60.0, 1.5), _h("BBB", 20.0, 0.5)]            # 20 of 100 is cash
    t = an.tail_risk(hs, 100.0, None)
    assert t["var_pct"] is None and t["reason"]
    by = {round(s["market_move"], 2): s for s in t["scenarios"]}
    assert by[-0.10]["book_pct"] == pytest.approx(-0.125)           # equity beta 1.25
    assert by[-0.10]["value"] == pytest.approx(-10.0)
    assert by[-0.10]["account_pct"] == pytest.approx(-0.10)         # cash dilutes to 1.0 beta
    assert by[-0.20]["value"] == pytest.approx(-20.0)


def test_no_betas_means_no_scenarios():
    t = an.tail_risk([_h("AAA", 100.0)], 100.0, None)
    assert t["scenarios"] == []


def test_compute_metrics_exposes_tail_risk_additively():
    rets = pd.DataFrame({"AAA": _spiky()})
    m = an.compute_metrics([_h("AAA", 1000.0, 1.0)], 0.0, 1000.0, returns=rets).to_dict()
    assert "tail_risk" in m and m["tail_risk"]["cvar_pct"] == pytest.approx(0.04)
    assert "volatility" in m and "beta" in m           # existing keys untouched
    empty = an.compute_metrics([], 500.0, 500.0).to_dict()
    assert empty["tail_risk"]["var_pct"] is None


# ------------------------------------------------------ PortfolioRiskManager

def _manager(series):
    m = PortfolioRiskManager()
    m._get_historical_returns = lambda t, days=252: series.get(t)   # no network
    return m


def test_var_ignores_thin_history_instead_of_synthesising():
    m = _manager({"AAA": _spiky(), "BBB": pd.Series(np.full(15, 0.01), index=_dates(15))})
    rep = m.portfolio_var_report([{"ticker": "AAA", "value": 600}, {"ticker": "BBB", "value": 400}])
    assert rep["parametric_var"] is not None and math.isfinite(rep["parametric_var"])
    assert rep["coverage"] == pytest.approx(0.6)
    assert "BBB" in rep["excluded"] and rep["measured"] == ["AAA"]
    assert rep["cvar"] >= rep["historical_var"]
    # weights renormalise over covered names, so AAA alone gives the same number
    assert m.calculate_portfolio_var([{"ticker": "AAA", "value": 600}]) == pytest.approx(rep["parametric_var"])


def test_var_is_deterministic():
    s = {"AAA": _spiky(), "BBB": _spiky(spikes=(-0.01,) * 5)}
    pos = [{"ticker": "AAA", "value": 1}, {"ticker": "BBB", "value": 1}]
    assert _manager(s).calculate_portfolio_var(pos) == _manager(s).calculate_portfolio_var(pos)


def test_var_none_when_unmeasurable_never_zero():
    m = _manager({})
    assert m.calculate_portfolio_var([{"ticker": "AAA", "value": 100}]) is None
    assert m.calculate_portfolio_var([]) is None
    # date-misaligned names: shortest is dropped, the rest still measured
    s = {"AAA": _spiky(100), "BBB": pd.Series(np.full(70, 0.001), index=_dates(70, "2020-01-01"))}
    rep = _manager(s).portfolio_var_report([{"ticker": "AAA", "value": 1}, {"ticker": "BBB", "value": 1}])
    assert rep["measured"] == ["AAA"] and "BBB" in rep["excluded"]


def test_unmeasured_var_is_flagged_not_within_limits():
    m = _manager({})
    r = m.get_portfolio_risk_metrics([{"ticker": "AAA", "value": 100, "sector": "X"}])
    # (check_correlation_cluster only touches the network for 2+ tickers)
    assert r.portfolio_var is None
    assert r.is_within_limits is False
    assert any("not measured" in v for v in r.violations)
    assert r.risk_utilization == 0
