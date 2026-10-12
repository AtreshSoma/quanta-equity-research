"""Single source of truth for the A/B/C/D/F risk grade.

Both SP_ELITE_PRO.py (scanner) and TradingAnalysis_ELITE.py (analyzer) must call
this so they cannot disagree on the same ticker's risk classification.

The methodology is the analyzer's existing 1-10 risk_points scale (the
stricter, more conservative of the two prior implementations). Inputs the
scanner does not collect (tail_risk_score, liquidity_score) default to
neutral values, so the grade still falls back gracefully.

Contract: this function is pure. Given the same inputs it returns the same
(risk_points, grade) every time. It does not log, raise, or read globals.

Audit F3: the two engines used to MEASURE the inputs differently (the scanner
three over one year with tail and liquidity left neutral, the Research engine
five over two years). :func:`grade_from_history` measures all five from daily
bars over ``RISK_GRADE.LOOKBACK_DAYS``, and both engines call it, so the same
bars give the same grade and the same drivers.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from .thresholds import RISK_GRADE


@dataclass
class RiskGradeInputs:
    """All inputs needed to compute a unified risk grade.

    Defaults are chosen so callers that lack tail/liquidity data still get
    a sensible grade dominated by vol/beta/drawdown (the scanner's case).
    """
    annualized_volatility: float = 0.0   # 0.30 == 30% annualised
    beta: float = 1.0
    max_drawdown: float = 0.0            # negative number, e.g. -0.27
    tail_risk_score: float = 50.0        # 0-100, higher = worse tails
    liquidity_score: float = 5.0         # 0-10, higher = more liquid


def _points_above(value: float, bands) -> int:
    """1 point per band the value exceeds (bands ascending)."""
    return sum(1 for b in bands if value > b)


def _points_below(value: float, bands) -> int:
    """1 point per band the value falls under (bands descending, e.g. drawdowns)."""
    return sum(1 for b in bands if value < b)


def _pct_list(bands) -> str:
    return " / ".join(f"{b * 100:.0f}%" for b in bands)


def risk_grade_drivers(inputs: RiskGradeInputs) -> List[Dict[str, object]]:
    """Each measured factor's contribution to the risk points, in order.

    The grade is exactly the baseline (``RISK_GRADE.BASELINE_POINTS``) plus the
    sum of these points, so the page can say WHY a name is grade D rather than
    print an inverted score. Bands and text come from ``engine.thresholds``.
    """
    G = RISK_GRADE
    drivers: List[Dict[str, object]] = []

    def add(key: str, label: str, points: int, detail: str) -> None:
        drivers.append({"factor": key, "label": label, "points": points, "detail": detail})

    vol = float(inputs.annualized_volatility or 0.0)
    add("volatility", "Volatility", _points_above(vol, G.VOL_BANDS),
        f"{vol * 100:.0f}% annualised (points above {_pct_list(G.VOL_BANDS)})")
    beta = float(inputs.beta or 1.0)
    add("beta", "Beta", _points_above(beta, G.BETA_BANDS),
        f"{beta:.2f} vs its benchmark (points above {' / '.join(f'{b:g}' for b in G.BETA_BANDS)})")
    dd = float(inputs.max_drawdown or 0.0)
    add("drawdown", "Max drawdown", _points_below(dd, G.DRAWDOWN_BANDS),
        f"{dd * 100:.0f}% peak to trough (points beyond {_pct_list(G.DRAWDOWN_BANDS)})")
    tail = float(inputs.tail_risk_score or 50.0)
    add("tail", "Tail risk", _points_above(tail, G.TAIL_BANDS),
        f"{tail:.0f}/100 from skew and fat tails (points above {' / '.join(f'{b:g}' for b in G.TAIL_BANDS)})")
    liq = float(inputs.liquidity_score or 5.0)
    add("liquidity", "Liquidity", 1 if liq < G.LIQUIDITY_FLOOR else 0,
        f"{liq:.0f}/10 dollar-volume score (a point below {G.LIQUIDITY_FLOOR:g})")
    return drivers


def grade_for_points(overall: int) -> str:
    for ceiling, grade in RISK_GRADE.GRADE_CUTOFFS:
        if overall <= ceiling:
            return grade
    return "F"


def compute_risk_grade(inputs: RiskGradeInputs) -> Tuple[int, str]:
    """Return (risk_points 1-10, grade A-F).

    Points start at ``RISK_GRADE.BASELINE_POINTS`` and add one per band each
    input crosses (:func:`risk_grade_drivers`). The grade comes from
    ``RISK_GRADE.GRADE_CUTOFFS`` (inclusive ceilings; above the last is F).
    """
    risk_points = sum(int(d["points"]) for d in risk_grade_drivers(inputs))
    overall = min(RISK_GRADE.MAX_POINTS, max(1, RISK_GRADE.BASELINE_POINTS + risk_points))
    return overall, grade_for_points(overall)


# ── measuring the inputs from daily bars (audit F3) ─────────────────────────

def _clean_returns(series) -> "Any":
    import numpy as np
    import pandas as pd
    r = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return r.loc[r.abs() < RISK_GRADE.SPLIT_ARTIFACT_RETURN]


def _by_session(series) -> "Any":
    import pandas as pd
    out = series.copy()
    out.index = pd.DatetimeIndex([pd.Timestamp(v).date() for v in out.index])
    if out.index.has_duplicates:
        out = out.groupby(level=0).last()
    return out.sort_index()


def tail_score(returns) -> Tuple[float, float, float]:
    """(score 0-100, skew, excess kurtosis) on winsorized daily returns.

    Winsorizing at ``TAIL_WINSOR_PCT`` keeps one gap day (already counted as
    drawdown) from reading as a fat-tailed distribution.
    """
    q = RISK_GRADE.TAIL_WINSOR_PCT
    w = returns.clip(returns.quantile(q), returns.quantile(1.0 - q))
    skew = float(w.skew()) if len(w) > 2 else 0.0
    kurt = float(w.kurtosis()) if len(w) > 3 else 0.0
    skew = 0.0 if math.isnan(skew) else skew
    kurt = 0.0 if math.isnan(kurt) else kurt
    score = RISK_GRADE.TAIL_BASE
    score += next((add for limit, add in RISK_GRADE.TAIL_SKEW_STEPS if skew < limit), 0.0)
    score += next((add for limit, add in RISK_GRADE.TAIL_KURT_STEPS if kurt > limit), 0.0)
    return min(100.0, score), skew, kurt


def liquidity_from_dollar_volume(avg_dollar_volume: float) -> Tuple[float, float]:
    """(liquidity score 0-10, estimated spread) from ``RISK_GRADE.LIQUIDITY_BANDS``."""
    score, spread = RISK_GRADE.LIQUIDITY_FLOOR_SCORE
    for floor, band_score, band_spread in RISK_GRADE.LIQUIDITY_BANDS:
        if avg_dollar_volume > floor:
            return band_score, band_spread
    return score, spread


def grade_from_history(close, volume=None, benchmark_returns=None) -> Dict[str, Any]:
    """The one risk grade, measured from daily bars (both engines call this).

    ``close`` / ``volume``: daily series (older bars are ignored beyond
    ``RISK_GRADE.LOOKBACK_DAYS``). ``benchmark_returns``: the market
    benchmark's daily returns (SPY, or ^NSEI for India); beta uses the last
    ``BETA_LOOKBACK_DAYS`` common sessions. Returns ``grade`` None when there
    are fewer than ``MIN_RETURNS`` usable returns.
    """
    import numpy as np
    import pandas as pd

    G = RISK_GRADE
    closes = pd.to_numeric(close, errors="coerce").dropna().tail(G.LOOKBACK_DAYS + 1)
    rets = _clean_returns(closes.pct_change(fill_method=None))
    out: Dict[str, Any] = {"grade": None, "points": None, "drivers": [], "lookback_days": int(len(rets))}
    if len(rets) < G.MIN_RETURNS:
        return out

    vol = float(rets.std() * math.sqrt(252))
    beta, beta_obs = None, 0
    if benchmark_returns is not None and len(benchmark_returns):
        s = _by_session(rets)
        b = _by_session(_clean_returns(benchmark_returns))
        common = s.index.intersection(b.index)[-G.BETA_LOOKBACK_DAYS:]
        beta_obs = int(len(common))
        if beta_obs >= G.BETA_MIN_OBS:
            sv, bv = s.loc[common].to_numpy(), b.loc[common].to_numpy()
            var = float(np.var(bv, ddof=1))
            cov = float(np.cov(sv, bv)[0][1])
            if var > 0 and math.isfinite(cov):
                beta = cov / var
    cum = (1.0 + rets).cumprod()
    max_dd = float((cum / cum.cummax() - 1.0).min())
    tail, skew, kurt = tail_score(rets)
    avg_dv = None
    liq = 5.0
    if volume is not None:
        v = pd.to_numeric(volume, errors="coerce").dropna()
        if len(v):
            avg_dv = float(v.tail(G.LIQUIDITY_AVG_DAYS).mean()) * float(closes.iloc[-1])
            liq, _ = liquidity_from_dollar_volume(avg_dv)

    inputs = RiskGradeInputs(annualized_volatility=vol, beta=(beta if beta is not None else 1.0),
                             max_drawdown=max_dd, tail_risk_score=tail, liquidity_score=liq)
    points, grade = compute_risk_grade(inputs)
    drivers = risk_grade_drivers(inputs)
    for d in drivers:
        if d["factor"] == "beta" and beta is None:
            d["detail"] = "not measured; the neutral 1.0 placeholder adds no points"
        if d["factor"] == "liquidity" and avg_dv is None:
            d["detail"] = "not measured (no volume); neutral, adds no points"
    out.update(grade=grade, points=points, drivers=drivers, inputs=asdict(inputs),
               beta_measured=beta is not None, beta_observations=beta_obs,
               skewness=skew, kurtosis=kurt, avg_dollar_volume=avg_dv)
    return out
