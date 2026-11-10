"""What would holding the top-ranked names have earned, after costs?

The out-of-sample report measures rank correlation (IC). It says whether a
higher score went with a better forward return, not what a portfolio built on
the ranking returned, how far it fell, how much it traded or whether it beat
simply holding everything. This module answers those from the same saved score
panel, with no new data.

One configuration, fixed here before any result was read (2026-11-09):

* Signal dates: every ``STEP`` panel dates (the panel is weekly, so about every
  20 sessions).
* Execution: the close of the NEXT panel date after the signal (``ENTRY_LAG``),
  held to the next rebalance's execution close. A score built from a close is
  never traded at that same close.
* Portfolios: the top fifth and the bottom fifth by score, equal weight, and
  the top fifth by 12-1 momentum as a free control.
* Benchmark: every scored name, equal weight, on the same dates, with no cost
  charged to it (the harder comparison for the strategy).
* Cost: each side pays half the Research round-trip cost
  (``engine.costs.round_trip_cost_frac``) on the weight it trades; a stress run
  charges ``STRESS_COST_PER_SIDE``.

Two further tables use the 63-session forward return already in the panel:

* Band calibration: the mean return, relative to the same date's average name,
  of scores in each live decision band (``LADDER.SCORE_BANDS``). If the bands
  mean anything, it rises from Sell to Strong Buy.
* Regime weights: the per-date IC of the panel's composite (live regime
  tables) minus the IC of the same legs under one fixed table, paired by date.

What this cannot show: the panel is a static list of present-day large caps, so
absolute returns carry survivorship bias and only the comparisons against the
equal-weight benchmark of the same names are informative. The live engine's
gates, stops and sizes are not simulated; sentiment and analyst inputs are
neutral in the panel. Drawdown is measured at rebalance dates, so it understates
the worst intra-period fall. A result here is a description of a ranking on one
sample, not a validated strategy.

    python -m core.apex_quant.run portfolio --panel research_v2_pit_oos
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import stats as S
from .elite_oos import REPORT_DIR, _spearman, load_panel
from .factor_baselines import add_baselines, ic_series

STEP = 4                        # panel dates between rebalances
ENTRY_LAG = 1                   # panel dates between the signal and its execution close
QUANTILE = 0.20                 # top / bottom share of names held
MIN_NAMES = 10                  # fewer scored names on a signal date: no portfolio that period
STRESS_COST_PER_SIDE = 0.0025   # 25 bps a side
BAND_HORIZON = 63               # sessions, the Research Decision's horizon
FIXED_REGIME = "SIDEWAYS"       # the fixed table the live regime weights are compared with
LEGS = ("technical", "fundamental", "valuation", "quality", "sentiment")


def base_cost_per_side() -> float:
    """Half the Research engine's US round-trip cost: the cost of one side."""
    try:
        from engine.costs import round_trip_cost_frac
    except ImportError:  # run as ``python -m core.apex_quant.run`` without core/ on the path
        from core.engine.costs import round_trip_cost_frac
    return float(round_trip_cost_frac("")) / 2.0


def _thresholds():
    try:
        from engine import thresholds
    except ImportError:
        from core.engine import thresholds
    return thresholds


# ── Portfolio returns ────────────────────────────────────────────────────────

@dataclass
class PeriodReturns:
    """Per-period returns of one portfolio and its benchmark, on execution dates."""
    entry_dates: List[pd.Timestamp]
    exit_dates: List[pd.Timestamp]
    gross: np.ndarray
    traded: np.ndarray              # weight bought plus weight sold at each entry (0..2)
    benchmark: np.ndarray
    holdings: List[List[str]]


def _target_weights(scores: pd.Series, side: str, quantile: float) -> Dict[str, float]:
    """Equal weights on the top (or bottom) ``quantile`` of names; ties break by ticker."""
    ranked = scores.dropna().reset_index()
    ranked.columns = ["ticker", "score"]
    ranked = ranked.sort_values(["score", "ticker"], ascending=[side == "bottom", True], kind="mergesort")
    n = max(1, int(round(len(ranked) * quantile)))
    held = ranked["ticker"].head(n).tolist()
    return {t: 1.0 / n for t in held}


STALE_TOLERANCE = pd.Timedelta(days=7)   # an off-grid name's last value may be this old


def grid_dates(panel: pd.DataFrame, min_names: int = MIN_NAMES) -> pd.DatetimeIndex:
    """The panel's shared evaluation dates.

    Most names are scored on one weekly grid, but a name listed later (META,
    ABBV) runs on its own, a day off. Those stray dates hold one or two names
    and are not rebalance dates.
    """
    counts = panel.groupby(level="date").size()
    return pd.DatetimeIndex(counts[counts >= min_names].index).sort_values()


def on_grid(panel: pd.DataFrame, column: str, grid: pd.DatetimeIndex) -> pd.DataFrame:
    """``column`` as a date x ticker table on ``grid``.

    A name scored off the grid contributes its latest value from at or before
    each grid date, at most ``STALE_TOLERANCE`` old: older information, never
    later information.
    """
    wide = panel[column].unstack("ticker").sort_index()
    return pd.DataFrame({t: wide[t].dropna().reindex(grid, method="ffill", tolerance=STALE_TOLERANCE)
                         for t in wide.columns}, index=grid)


def period_returns(panel: pd.DataFrame, column: str = "composite", side: str = "top",
                   step: int = STEP, entry_lag: int = ENTRY_LAG, quantile: float = QUANTILE,
                   min_names: int = MIN_NAMES) -> PeriodReturns:
    """Returns of the ``side`` quantile of ``column``, rebalanced every ``step`` panel dates.

    A name enters only if it has a close on the execution date, and its period
    return needs a close on the exit date too; a name with no exit close leaves
    the period (its weight is re-spread), which on this survivor panel is rare
    and is one more reason absolute returns are not trusted.
    """
    dates = grid_dates(panel, min_names)
    closes = on_grid(panel, "close", dates)
    scores = on_grid(panel, column, dates)
    signal_pos = list(range(0, len(dates) - entry_lag, step))
    entries = [p + entry_lag for p in signal_pos]
    out = PeriodReturns([], [], np.array([]), np.array([]), np.array([]), [])
    gross, traded, bench = [], [], []
    previous: Dict[str, float] = {}
    for k in range(len(entries) - 1):
        sig, ent, ext = signal_pos[k], entries[k], entries[k + 1]
        px_in, px_out = closes.iloc[ent], closes.iloc[ext]
        row = scores.iloc[sig]
        tradable = row[px_in.notna() & row.notna()]
        if len(tradable) < min_names:
            previous = {}
            continue
        weights = _target_weights(tradable, side, quantile)
        rets = (px_out / px_in - 1.0)
        held = [t for t in weights if np.isfinite(rets.get(t, np.nan))]
        universe = [t for t in tradable.index if np.isfinite(rets.get(t, np.nan))]
        if not held or not universe:
            previous = {}
            continue
        gross.append(float(np.mean([rets[t] for t in held])))
        bench.append(float(np.mean([rets[t] for t in universe])))
        names = set(weights) | set(previous)
        traded.append(float(sum(abs(weights.get(t, 0.0) - previous.get(t, 0.0)) for t in names)))
        previous = weights
        out.entry_dates.append(dates[ent])
        out.exit_dates.append(dates[ext])
        out.holdings.append(sorted(weights))
    out.gross, out.traded, out.benchmark = np.array(gross), np.array(traded), np.array(bench)
    return out


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass
class PortfolioStats:
    name: str
    description: str
    cost_per_side_bps: float
    periods: int
    years: float
    cagr: Optional[float]
    volatility: Optional[float]
    sharpe: Optional[float]             # cash rate deducted (risk_free)
    sortino: Optional[float]
    max_drawdown: Optional[float]       # at rebalance dates
    turnover_per_year: Optional[float]  # one-way: share of the portfolio replaced a year
    cost_drag_per_year: Optional[float]
    benchmark_cagr: Optional[float]
    active_return_per_year: Optional[float]
    active_t: Optional[float]
    active_ci95_per_period: Optional[List[float]]
    information_ratio: Optional[float]
    alpha_per_year: Optional[float]
    beta: Optional[float]
    hit_rate: Optional[float]           # share of periods ahead of the benchmark
    hit_rate_ci95: Optional[List[float]]


def _max_drawdown(returns: np.ndarray) -> float:
    equity = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return float(np.min(equity / peak - 1.0))


def portfolio_stats(name: str, description: str, pr: PeriodReturns, cost_per_side: float,
                    risk_free: float = 0.0, seed: int = 0) -> PortfolioStats:
    n = len(pr.gross)
    empty = PortfolioStats(name, description, cost_per_side * 1e4, n, 0.0, *([None] * 16))
    if n < 5:
        return empty
    years = (pr.exit_dates[-1] - pr.entry_dates[0]).days / 365.25
    if years <= 0:
        return empty
    ppy = n / years
    cost = pr.traded * cost_per_side
    net = pr.gross - cost
    active = net - pr.benchmark
    sd = float(np.std(net, ddof=1))
    rf_period = risk_free / ppy
    downside = np.minimum(net - rf_period, 0.0)
    dd = float(np.sqrt(np.mean(downside ** 2)))
    inf = S.infer_mean(active, seed=seed)
    active_sd = float(np.std(active, ddof=1))
    bvar = float(np.var(pr.benchmark, ddof=1))
    beta = float(np.cov(net, pr.benchmark, ddof=1)[0, 1] / bvar) if bvar > 0 else None
    alpha = (float(np.mean(net)) - beta * float(np.mean(pr.benchmark))) * ppy if beta is not None else None
    wins = int(np.sum(active > 0))
    wil = S.wilson_interval(wins, n)

    def cagr(r: np.ndarray) -> Optional[float]:
        end = float(np.prod(1.0 + r))
        return end ** (1.0 / years) - 1.0 if end > 0 else None

    return PortfolioStats(
        name=name, description=description, cost_per_side_bps=cost_per_side * 1e4,
        periods=n, years=years, cagr=cagr(net),
        volatility=sd * math.sqrt(ppy),
        sharpe=(float(np.mean(net)) - rf_period) / sd * math.sqrt(ppy) if sd > 0 else None,
        sortino=(float(np.mean(net)) - rf_period) / dd * math.sqrt(ppy) if dd > 0 else None,
        max_drawdown=_max_drawdown(net),
        turnover_per_year=float(np.sum(pr.traded)) / 2.0 / years,
        cost_drag_per_year=float(np.sum(cost)) / years,
        benchmark_cagr=cagr(pr.benchmark),
        active_return_per_year=float(np.mean(active)) * ppy,
        active_t=inf.t_newey_west if inf else None,
        active_ci95_per_period=list(inf.ci95_bootstrap) if inf and inf.ci95_bootstrap else None,
        information_ratio=float(np.mean(active)) / active_sd * math.sqrt(ppy) if active_sd > 0 else None,
        alpha_per_year=alpha, beta=beta,
        hit_rate=wins / n, hit_rate_ci95=list(wil) if wil else None,
    )


# ── Band calibration ─────────────────────────────────────────────────────────

@dataclass
class BandRow:
    band: str
    floor: Optional[float]
    rows: int
    dates: int                         # independent (non-overlapping) dates with the band present
    mean_excess: Optional[float]       # vs the same date's equal-weight mean, 63 sessions
    t_stat: Optional[float]
    ci95: Optional[List[float]]


def band_calibration(panel: pd.DataFrame, horizon: int = BAND_HORIZON, column: str = "composite",
                     seed: int = 0) -> List[BandRow]:
    """Mean forward return of each live decision band, relative to the date's average name.

    Each date contributes one number per band (the band's mean excess that day),
    and only dates at least ``horizon`` sessions apart are kept, so the t-statistic
    does not count one overlapping quarter many times.
    """
    bands = tuple(_thresholds().LADDER.SCORE_BANDS)
    label = f"fwd_{horizon}d"
    usable = panel[[column, label]].dropna()
    usable = usable[usable.index.get_level_values("date").isin(grid_dates(panel))]
    floors = [f for f, _ in bands]
    names = [b for _, b in bands] + ["STRONG_SELL"]

    def band_of(score: float) -> str:
        for f, b in bands:
            if score >= f:
                return b
        return "STRONG_SELL"

    usable = usable.assign(excess=usable[label] - usable.groupby(level="date")[label].transform("mean"),
                           band=usable[column].map(band_of))
    all_dates = usable.index.get_level_values("date").unique().sort_values()
    spacing = max(1.0, float(np.median(np.diff(all_dates.values).astype("timedelta64[D]").astype(float))) * 5 / 7)
    stride = max(1, int(math.ceil(horizon / spacing)))
    keep = set(all_dates[::stride])
    per_date = usable.groupby([usable.index.get_level_values("date"), "band"])["excess"].mean()
    rows: List[BandRow] = []
    for i, name in enumerate(names):
        counts = int((usable["band"] == name).sum())
        series = per_date.xs(name, level="band") if name in per_date.index.get_level_values("band") else pd.Series(dtype=float)
        series = series[series.index.isin(keep)]
        inf = S.infer_mean(series.to_numpy(), lags=0, seed=seed) if len(series) >= 5 else None
        rows.append(BandRow(name, floors[i] if i < len(floors) else None, counts, int(len(series)),
                            inf.mean if inf else (float(series.mean()) if len(series) else None),
                            inf.t_newey_west if inf else None,
                            list(inf.ci95_bootstrap) if inf and inf.ci95_bootstrap else None))
    return rows


def band_monotonicity(rows: List[BandRow]) -> Optional[float]:
    """Spearman correlation between band order and mean excess return (bands with data only)."""
    have = [(i, r.mean_excess) for i, r in enumerate(rows) if r.mean_excess is not None and r.dates >= 5]
    if len(have) < 3:
        return None
    order = pd.Series([-i for i, _ in have], dtype=float)     # index 0 is the strongest band
    value = pd.Series([v for _, v in have], dtype=float)
    rho = _spearman(order, value)
    return None if np.isnan(rho) else float(rho)


# ── Regime weights vs one fixed table ────────────────────────────────────────

def _regime_tables() -> Dict[str, Dict[str, float]]:
    try:
        import TradingAnalysis_ELITE as elite
    except ImportError:
        from core import TradingAnalysis_ELITE as elite
    return {k: dict(v) for k, v in elite.Config.REGIME_WEIGHTS.items()}


def fixed_weight_composite(panel: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    """The composite the panel's legs give under one weight table (renormalised)."""
    total = sum(max(0.0, float(weights.get(k, 0.0))) for k in LEGS)
    acc = sum(panel[f"score_{k}"] * max(0.0, float(weights.get(k, 0.0))) for k in LEGS)
    return acc / total


def regime_weight_test(panel: pd.DataFrame, horizons: Tuple[int, ...] = (21, 63),
                       seed: int = 0) -> Dict[str, Dict[str, object]]:
    """IC of the panel's composite minus IC under the fixed table, paired by date."""
    missing = [k for k in LEGS if f"score_{k}" not in panel.columns]
    if missing:
        return {}
    work = panel.copy()
    work["fixed_composite"] = fixed_weight_composite(work, _regime_tables()[FIXED_REGIME])
    out: Dict[str, Dict[str, object]] = {}
    for h in horizons:
        live = ic_series(work, "composite", h)
        fixed = ic_series(work, "fixed_composite", h)
        both = pd.concat([live, fixed], axis=1, keys=["live", "fixed"]).dropna()
        inf = S.infer_mean((both["live"] - both["fixed"]).to_numpy(), seed=seed)
        out[f"{h}d"] = {
            "dates": int(len(both)),
            "ic_live_weights": float(both["live"].mean()) if len(both) else None,
            "ic_fixed_weights": float(both["fixed"].mean()) if len(both) else None,
            "difference": inf.to_dict() if inf else None,
        }
    return out


# ── Report ───────────────────────────────────────────────────────────────────

@dataclass
class PortfolioReport:
    generated_at: str
    panel: str
    panel_rows: int
    tickers: int
    start: str
    end: str
    step_panel_dates: int
    entry_lag_panel_dates: int
    quantile: float
    names_held: int
    regime_weights_differ: bool
    portfolios: List[PortfolioStats] = field(default_factory=list)
    bands: List[BandRow] = field(default_factory=list)
    band_monotonicity: Optional[float] = None
    regime_weights: Dict[str, Dict[str, object]] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


CAVEATS = (
    "Static list of present-day large caps: absolute returns carry survivorship and selection "
    "bias. Read each portfolio against the equal-weight benchmark of the same names.",
    "The live engine's gates, stops, sizes and corporate-event checks are not simulated. This "
    "measures the ranking, not the calls.",
    "Sentiment and analyst inputs are neutral in the panel (no point-in-time source).",
    "Drawdown is measured at rebalance dates and understates the worst fall inside a period.",
    "With sentiment neutral at 50 the panel's scores sit closer to 50 than live scores do, so the "
    "band table tests the live cut-offs on a compressed scale (few rows reach 75).",
    "A portfolio that needs a year of history (12-1 momentum) starts later, so its benchmark "
    "figure covers a slightly shorter period than the others.",
    "One fixed configuration was run. Its result is a description of this sample, not a "
    "validated strategy, and it does not change any evidence status.",
)


def run_portfolio_report(panel: Optional[pd.DataFrame] = None, panel_name: str = "elite",
                         seed: int = 0) -> PortfolioReport:
    panel = load_panel() if panel is None else panel
    panel = add_baselines(panel, seed=seed)
    dates = panel.index.get_level_values("date")
    base, stress = base_cost_per_side(), STRESS_COST_PER_SIDE
    specs = (
        ("composite_top", "Top fifth by composite score", "composite", "top"),
        ("composite_bottom", "Bottom fifth by composite score", "composite", "bottom"),
        ("momentum_top", "Top fifth by 12-1 momentum (free control)", "momentum_12_1", "top"),
    )
    portfolios: List[PortfolioStats] = []
    held = 0
    for name, desc, column, side in specs:
        pr = period_returns(panel, column=column, side=side)
        if name == "composite_top" and pr.holdings:
            held = int(round(float(np.mean([len(h) for h in pr.holdings]))))
        portfolios.append(portfolio_stats(name, desc, pr, base, seed=seed))
        if name == "composite_top":
            portfolios.append(portfolio_stats(f"{name}_stress", f"{desc}, {stress * 1e4:.0f} bps a side",
                                              pr, stress, seed=seed))
    bands = band_calibration(panel, seed=seed)
    regime = regime_weight_test(panel, seed=seed)
    differ = bool(regime) and bool(
        (panel["composite"] - fixed_weight_composite(panel, _regime_tables()[FIXED_REGIME])).abs().max() > 1e-6)
    return PortfolioReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        panel=panel_name, panel_rows=int(len(panel)),
        tickers=int(panel.index.get_level_values("ticker").nunique()),
        start=str(dates.min().date()), end=str(dates.max().date()),
        step_panel_dates=STEP, entry_lag_panel_dates=ENTRY_LAG, quantile=QUANTILE, names_held=held,
        regime_weights_differ=differ,
        portfolios=portfolios, bands=bands, band_monotonicity=band_monotonicity(bands),
        regime_weights=regime, caveats=list(CAVEATS),
    )


def _pct(x: Optional[float], digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def _num(x: Optional[float], digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:+.{digits}f}"


def format_report(r: PortfolioReport) -> str:
    lines = [
        "PORTFOLIO STUDY OF THE SCORE PANEL",
        f"panel {r.panel}: {r.panel_rows:,} rows, {r.tickers} tickers, {r.start} to {r.end}",
        f"rebalance every {r.step_panel_dates} panel dates, executed {r.entry_lag_panel_dates} panel date "
        f"after the signal; {r.quantile:.0%} of names held (about {r.names_held}), equal weight",
        "",
        f"{'portfolio':<24}{'cost':>6}{'CAGR':>8}{'bench':>8}{'active/yr':>10}{'t':>7}"
        f"{'Sharpe':>8}{'Sortino':>8}{'maxDD':>8}{'alpha/yr':>9}{'beta':>6}{'turn/yr':>8}{'hit':>6}",
    ]
    for p in r.portfolios:
        lines.append(
            f"{p.name:<24}{p.cost_per_side_bps:>4.1f}bp{_pct(p.cagr):>8}{_pct(p.benchmark_cagr):>8}"
            f"{_pct(p.active_return_per_year):>10}{_num(p.active_t):>7}{_num(p.sharpe):>8}{_num(p.sortino):>8}"
            f"{_pct(p.max_drawdown):>8}{_pct(p.alpha_per_year):>9}{_num(p.beta):>6}"
            f"{'n/a' if p.turnover_per_year is None else f'{p.turnover_per_year:.1f}x':>8}"
            f"{'n/a' if p.hit_rate is None else f'{p.hit_rate:.0%}':>6}")
    lines += ["", f"Sharpe and Sortino deduct no cash rate. Benchmark: all scored names, equal weight, no cost. "
                  f"Periods: {r.portfolios[0].periods if r.portfolios else 0}.", "",
              f"DECISION BANDS, {BAND_HORIZON}-session return vs the same date's average name",
              f"{'band':<14}{'floor':>7}{'rows':>8}{'dates':>7}{'mean excess':>13}{'t':>7}   95% CI"]
    for b in r.bands:
        ci = "n/a" if not b.ci95 else f"{b.ci95[0] * 100:+.2f}% to {b.ci95[1] * 100:+.2f}%"
        lines.append(f"{b.band:<14}{'' if b.floor is None else f'{b.floor:.0f}':>7}{b.rows:>8}{b.dates:>7}"
                     f"{_pct(b.mean_excess, 2):>13}{_num(b.t_stat):>7}   {ci}")
    lines.append(f"band order vs mean excess (Spearman): {_num(r.band_monotonicity)}")
    if r.regime_weights:
        lines += ["", f"REGIME WEIGHTS vs the fixed {FIXED_REGIME} table (per-date IC, paired)"]
        if not r.regime_weights_differ:
            lines.append("this panel was scored with the fixed table: the comparison is not available here")
        for h, v in r.regime_weights.items():
            d = v.get("difference") or {}
            ci = d.get("ci95_bootstrap")
            lines.append(
                f"{h}: live {_num(v.get('ic_live_weights'), 4)}, fixed {_num(v.get('ic_fixed_weights'), 4)}, "
                f"difference {_num(d.get('mean'), 4)} (t {_num(d.get('t_newey_west'))}, "
                f"CI {'n/a' if not ci else f'{ci[0]:+.4f} to {ci[1]:+.4f}'}, {v.get('dates')} dates)")
    lines += ["", "CAVEATS"] + [f"- {c}" for c in r.caveats]
    return "\n".join(lines)


def save_report(report: PortfolioReport, name: str = "portfolio_study") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}.json"
    path.write_text(report.to_json() + "\n", encoding="utf-8")
    path.with_suffix(".txt").write_text(format_report(report) + "\n", encoding="utf-8")
    return path
