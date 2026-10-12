"""Audit F3: one risk grade, measured one way, with its drivers published.

The scanner graded on three inputs over one year (tail and liquidity left at
neutral defaults); the Research engine graded on five inputs over two years.
The same bars could get two grades, and the page had to say "the restrictive
result controls". Both now call engine.risk_grade.grade_from_history over
RISK_GRADE.LOOKBACK_DAYS, and the Decision carries the drivers.

Bars here are synthetic (seeded random walks); nothing touches the network.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
import TradingAnalysis_ELITE as ta  # noqa: E402
from engine import thresholds as T  # noqa: E402
from engine.risk_grade import grade_from_history  # noqa: E402

N = 560


def _market(seed=11, gap_at=None, beta=1.3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-09-30", periods=N, tz="America/New_York")
    bench = pd.Series(rng.normal(0.0004, 0.010, N), index=idx)
    r = beta * bench.values + rng.normal(0.0, 0.012, N)
    if gap_at is not None:
        r[gap_at] = -0.40
    close = 50.0 * np.cumprod(1.0 + r)
    vol = rng.integers(400_000, 600_000, N).astype(float)
    df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": vol},
                      index=idx)
    return df, bench


def _scanner_grade(df, bench):
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng.benchmark_hist = bench
    return eng._risk_grade_for(df, "SYNTH")


def _research_grade(df, bench, monkeypatch):
    ra = ta.RiskAnalyzer()
    monkeypatch.setattr(ra, "_get_benchmark_returns", lambda ticker: bench)
    return ra.analyze(df, "SYNTH")


def test_grade_from_history_reads_the_configured_window():
    df, bench = _market()
    g = grade_from_history(df["Close"], df["Volume"], bench)
    assert g["lookback_days"] == T.RISK_GRADE.LOOKBACK_DAYS
    assert [d["factor"] for d in g["drivers"]] == ["volatility", "beta", "drawdown", "tail", "liquidity"]
    pts = sum(int(d["points"]) for d in g["drivers"])
    assert g["points"] == min(10, max(1, T.RISK_GRADE.BASELINE_POINTS + pts))
    # Older bars than the window do not count: a crash before it changes nothing.
    crashed = df.copy()
    crashed.iloc[:N - T.RISK_GRADE.LOOKBACK_DAYS - 5, crashed.columns.get_loc("Close")] *= 3.0
    assert grade_from_history(crashed["Close"], crashed["Volume"], bench)["drivers"] == g["drivers"]


def test_single_risk_grade(monkeypatch):
    """Audit 9.2: the same bars give the same grade and drivers on both engines."""
    for seed, beta in ((11, 1.3), (5, 0.6), (23, 2.2)):
        df, bench = _market(seed=seed, beta=beta)
        scan = _scanner_grade(df, bench)
        research = _research_grade(df, bench, monkeypatch)
        assert scan["grade"] == research.risk_grade
        assert scan["drivers"] == research.grade_drivers
        assert scan["points"] == research.overall_risk_score
        assert research.risk_grade_lookback_days == T.RISK_GRADE.LOOKBACK_DAYS


def test_one_gap_day_does_not_add_tail_points():
    """Skew and kurtosis are read on winsorized returns: one -40% day is a
    drawdown (counted there), not a fat-tailed distribution."""
    calm, bench = _market(seed=3)
    gapped, _ = _market(seed=3, gap_at=N - 100)
    tail = lambda g: next(d for d in g["drivers"] if d["factor"] == "tail")  # noqa: E731
    g_calm = grade_from_history(calm["Close"], calm["Volume"], bench)
    g_gap = grade_from_history(gapped["Close"], gapped["Volume"], bench)
    assert tail(g_gap)["points"] == tail(g_calm)["points"]
    # Unwinsorized, that one day would have pushed kurtosis past the top band.
    raw = gapped["Close"].pct_change().dropna().tail(T.RISK_GRADE.LOOKBACK_DAYS)
    assert raw.kurtosis() > max(b for b, _ in T.RISK_GRADE.TAIL_KURT_STEPS)
    dd = lambda g: next(d for d in g["drivers"] if d["factor"] == "drawdown")  # noqa: E731
    assert dd(g_gap)["points"] > dd(g_calm)["points"], "the loss itself still counts as drawdown"


def test_unmeasured_beta_is_neutral_and_says_so():
    df, _ = _market()
    g = grade_from_history(df["Close"], df["Volume"], None)
    beta = next(d for d in g["drivers"] if d["factor"] == "beta")
    assert beta["points"] == 0 and "not measured" in beta["detail"]


def test_scanner_indicators_keep_one_calendar_year():
    """The scanner fetches two years for the grade but its indicators keep the
    one-year window they always used (cut by date, as the provider's 1y range)."""
    df, _ = _market()
    now = pd.Timestamp("2026-10-01 10:00", tz="America/New_York")
    cut = sp.indicator_window(df, now)
    assert cut.index[0] >= now.normalize() - pd.DateOffset(years=1)
    assert cut.index[-1] == df.index[-1]
    assert len(df.loc[df.index >= now.normalize() - pd.DateOffset(years=1)]) == len(cut)


def test_drivers_and_blockers_are_on_the_scanner_decision():
    from test_elite_pro_review2_fixes import _pick
    p = _pick("SYNTH")
    df, bench = _market(seed=23, beta=2.2)
    g = _scanner_grade(df, bench)
    p.risk_grade, p.risk_grade_drivers, p.risk_grade_lookback_days = g["grade"], g["drivers"], g["lookback_days"]
    p.final_action = "WATCHLIST"
    p.deep_reason_codes = ["LOW_REWARD_RISK", "RESEARCH_DECISION_CAP"]
    d = sp.decision_for_scan_result(p).to_dict()
    assert d["risk_grade_drivers"] == g["drivers"]
    assert d["risk_grade_lookback_days"] == T.RISK_GRADE.LOOKBACK_DAYS
    assert [b["code"] for b in d["blockers"]][:2] == ["LOW_REWARD_RISK", "RESEARCH_DECISION_CAP"]
    assert d["primary_blocker"]["code"] == "LOW_REWARD_RISK"
    assert d["holder_action"] and d["holder_action"] != "N/A"
    from engine.blockers import new_money
    from engine.types import FinalAction
    assert d["new_money"] == new_money(FinalAction.WATCHLIST)
