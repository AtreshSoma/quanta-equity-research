"""Does the model beat the factors anyone can compute for free? (audit A3/A4)

The out-of-sample report asks whether the composite predicts forward returns.
A reviewer's next question is sharper: does it predict them *better than plain
momentum, short-term reversal or low volatility*, and better than a random
ranking? A composite that merely re-expresses one of those adds nothing.

Every baseline is rebuilt point-in-time from the panel's own closes (no new
data, no look-ahead) and evaluated with the same per-date Spearman IC as the
composite. Inference uses every evaluation date with a Newey-West t and a block
bootstrap sized to the forward-return overlap; the composite-minus-baseline
difference is tested on the same dates (paired); Benjamini-Hochberg controls
the false discovery rate across the baselines tested at each horizon.

    python -m core.apex_quant.run baselines         # reads _reports/elite_panel.parquet
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import stats as S
from .elite_oos import REPORT_DIR, _median_spacing_days, _spearman, load_panel

MODEL_COLUMNS = ("composite", "score_technical")
BASELINES = ("momentum_12_1", "reversal_1m", "low_volatility", "random")
DESCRIPTIONS = {
    "composite": "QUANTA composite (the model)",
    "score_technical": "QUANTA technical leg",
    "momentum_12_1": "12-month return skipping the last month",
    "reversal_1m": "minus the last month's return",
    "low_volatility": "minus annualised volatility",
    "random": "a seeded random ranking (should be ~0)",
}


def add_baselines(panel: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Add point-in-time baseline factor columns, computed from each ticker's closes."""
    out = panel.copy()
    pieces = []
    for ticker, rows in out.groupby(level="ticker"):
        close = rows["close"].droplevel("ticker").sort_index()
        dates = close.index

        def past(days: int) -> np.ndarray:
            when = dates - pd.Timedelta(days=days)
            vals = np.array(close.asof(when), dtype=float)
            vals[when < dates[0]] = np.nan  # asof clamps to the first close
            return vals

        c = close.to_numpy(dtype=float)
        m1, m12 = past(30), past(365)
        idx = pd.MultiIndex.from_arrays([dates, [ticker] * len(dates)], names=["date", "ticker"])
        pieces.append(pd.DataFrame({"momentum_12_1": m1 / m12 - 1.0, "reversal_1m": -(c / m1 - 1.0)},
                                   index=idx))
    out = out.join(pd.concat(pieces)) if pieces else out
    if "annualized_volatility" in out.columns:
        out["low_volatility"] = -out["annualized_volatility"]
    out["random"] = np.random.default_rng(seed).random(len(out))
    return out


def ic_series(panel: pd.DataFrame, column: str, horizon: int, min_names: int = 8) -> pd.Series:
    """Per-date Spearman IC of ``column`` against the ``horizon``-day forward return."""
    label = f"fwd_{horizon}d"
    usable = panel[[column, label]].dropna()
    out: Dict[pd.Timestamp, float] = {}
    for date, group in usable.groupby(level="date"):
        if len(group) >= min_names:
            ic = _spearman(group[column], group[label])
            if not np.isnan(ic):
                out[date] = ic
    return pd.Series(out, dtype=float).sort_index()


@dataclass
class FactorResult:
    factor: str
    description: str
    n_dates: int
    mean_ic: Optional[float]
    t_newey_west: Optional[float]
    p_value: Optional[float]
    ci95_bootstrap: Optional[List[float]]
    hit_rate: Optional[float]
    # Paired against the composite on shared dates (composite minus this factor).
    composite_minus: Optional[Dict[str, object]] = None
    significant_fdr: Optional[bool] = None
    # The composite-minus-factor difference survives FDR control across baselines.
    difference_significant_fdr: Optional[bool] = None
    # A baseline that itself predicts significantly NEGATIVELY (often survivorship):
    # beating it is not evidence of skill.
    inverted: bool = False


@dataclass
class BaselineReport:
    generated_at: str
    panel_rows: int
    tickers: int
    start: str
    end: str
    horizons: Dict[str, List[FactorResult]] = field(default_factory=dict)
    verdicts: Dict[str, str] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _inference(series: pd.Series, step: int, seed: int):
    return S.infer_mean(series.to_numpy(), lags=step, block=max(step, S.default_block(len(series))),
                        seed=seed)


def _verdict(results: List[FactorResult]) -> str:
    model = next(r for r in results if r.factor == "composite")
    if model.mean_ic is None:
        return "Not enough dates to evaluate."
    def sig_diff(r, sign):
        cm = r.composite_minus or {}
        return (r.factor in BASELINES and r.difference_significant_fdr
                and cm.get("mean") is not None and (cm["mean"] > 0) == (sign > 0))

    beaten = [r.factor for r in results if sig_diff(r, -1)]
    better = [r.factor for r in results if sig_diff(r, +1) and not r.inverted]
    hollow = [r.factor for r in results if sig_diff(r, +1) and r.inverted]
    sig = (("significantly NEGATIVE: the ranking is inverted" if model.mean_ic < 0
            else "significantly positive") if model.significant_fdr else "not significant")
    parts = [f"Composite mean IC {model.mean_ic:+.4f} ({sig} after FDR control)."]
    if better:
        parts.append(f"Reliably better than: {', '.join(better)}.")
    if beaten:
        parts.append(f"Reliably WORSE than: {', '.join(beaten)}.")
    if hollow:
        parts.append(f"Beats {', '.join(hollow)} only because that baseline is itself inverted "
                     "(significantly negative IC); not evidence of skill.")
    if not better and not beaten and not hollow:
        parts.append("Not distinguishable from any free baseline.")
    return " ".join(parts)


def run_baselines(panel: Optional[pd.DataFrame] = None, horizons=(21, 63),
                  seed: int = 0) -> BaselineReport:
    panel = add_baselines(panel if panel is not None else load_panel(), seed=seed)
    dates = panel.index.get_level_values("date")
    report = BaselineReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        panel_rows=int(len(panel)),
        tickers=int(panel.index.get_level_values("ticker").nunique()),
        start=str(dates.min().date()), end=str(dates.max().date()),
        caveats=[
            "Same panel as the out-of-sample report: a static present-day universe, so "
            "survivorship bias applies to every factor alike. Compare factors with each "
            "other, not with an absolute bar.",
            "Inference uses every evaluation date: Newey-West t with lags equal to the "
            "forward-return overlap, and a circular block bootstrap with blocks at least "
            "that long.",
            "Every factor is ranked on the same rows: those where all factors and the forward "
            "return exist, so paired differences compare identical cross-sections.",
            "Benjamini-Hochberg at q=0.05, separately across the factor ICs and across the "
            "composite-minus-baseline differences at each horizon.",
        ],
    )
    for h in horizons:
        cols = [c for c in MODEL_COLUMNS + BASELINES if c in panel.columns]
        common = panel.dropna(subset=cols + [f"fwd_{h}d"])
        series = {c: ic_series(common, c, h) for c in cols}
        spacing = _median_spacing_days(next(iter(series.values())).index) if series else 1.0
        step = max(1, int(math.ceil(h / spacing)))
        results: List[FactorResult] = []
        for name, s in series.items():
            inf = _inference(s, step, seed)
            res = FactorResult(
                factor=name, description=DESCRIPTIONS.get(name, name), n_dates=int(len(s)),
                mean_ic=inf.mean if inf else None, t_newey_west=inf.t_newey_west if inf else None,
                p_value=inf.p_value if inf else None,
                ci95_bootstrap=list(inf.ci95_bootstrap) if inf and inf.ci95_bootstrap else None,
                hit_rate=float((s > 0).mean()) if len(s) else None,
            )
            if name != "composite" and "composite" in series:
                diff = (series["composite"] - s).dropna()
                d_inf = _inference(diff, step, seed)
                res.composite_minus = d_inf.to_dict() if d_inf else None
            results.append(res)
        for res, ok in zip(results, S.benjamini_hochberg([r.p_value for r in results])):
            res.significant_fdr = ok
            res.inverted = bool(ok and res.mean_ic is not None and res.mean_ic < 0
                                and res.factor in BASELINES)
        diffs = [(r.composite_minus or {}).get("p_value") for r in results]
        for res, ok in zip(results, S.benjamini_hochberg(diffs)):
            res.difference_significant_fdr = ok
        report.horizons[f"{h}d"] = results
        report.verdicts[f"{h}d"] = _verdict(results)
    return report


def format_report(report: BaselineReport) -> str:
    def f(x, spec="+.4f"):
        return "n/a" if x is None else format(x, spec)

    lines = [
        "QUANTA composite vs free baseline factors (cross-sectional IC)",
        f"panel {report.panel_rows} rows, {report.tickers} tickers, {report.start}..{report.end}",
        "",
    ]
    for h, results in report.horizons.items():
        lines.append(f"--- {h} forward return ---")
        lines.append(f"{'factor':<16}{'mean IC':>9}{'NW t':>7}{'95% CI':>20}{'FDR':>5}"
                     f"{'composite minus':>17}{'95% CI':>20}")
        for r in results:
            ci = r.ci95_bootstrap
            ci_s = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "n/a"
            cm = r.composite_minus or {}
            dci = cm.get("ci95_bootstrap")
            dci_s = f"[{dci[0]:+.3f}, {dci[1]:+.3f}]" if dci else ""
            fdr = {True: "yes", False: "no", None: "-"}[r.significant_fdr]
            lines.append(f"{r.factor:<16}{f(r.mean_ic):>9}{f(r.t_newey_west, '+.2f'):>7}"
                         f"{ci_s:>20}{fdr:>5}{(f(cm.get('mean')) if cm else ''):>17}{dci_s:>20}")
        lines.append(f"verdict: {report.verdicts[h]}")
        lines.append("")
    lines.append("Caveats:")
    lines.extend(f"  - {c}" for c in report.caveats)
    return "\n".join(lines)


def save_report(report: BaselineReport, name: str = "factor_baselines") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}.json"
    path.write_text(report.to_json() + "\n", encoding="utf-8")
    (REPORT_DIR / f"{name}.txt").write_text(format_report(report) + "\n", encoding="utf-8")
    return path
