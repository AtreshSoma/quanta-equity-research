"""Out-of-sample evaluation of the ELITE composite. Closes plan blocker H-09.

The question
------------
Does the composite score predict forward returns? Nothing in the codebase has
ever asked. `SignalTracker` records outcomes but no weight is derived from them,
so every threshold in `_calculate_component_scores` is an assertion, not a
finding.

Method
------
The primary statistic is the **cross-sectional information coefficient**: on
each date, rank every ticker by composite, rank the same tickers by their
subsequent return, and take the Spearman correlation across the cross-section.
Averaging those per-date correlations gives mean IC -- the standard factor
research measure. It is preferred over a pooled correlation because it is
immune to the market-wide moves that dominate a pooled sample (in a month when
everything rises, a pooled correlation rewards any score at all).

Interpreting the magnitude: a mean IC of 0.02-0.05 is a genuinely useful equity
factor; 0.10+ is exceptional and usually indicates a bug or leakage; ~0.00 means
no signal. The sign matters as much as the size -- a reliably negative IC means
the model is inverted, which is information.

Overlapping-sample correction
-----------------------------
A 21-day forward return sampled daily overlaps ~21x, so naive significance is
inflated by roughly sqrt(21) ~ 4.6. The headline t-statistic is therefore
computed on **non-overlapping** dates only (every h-th evaluation). The
overlapping figure is reported alongside it purely to show the size of that
inflation, and must not be quoted as the result.

Known limits, stated plainly
----------------------------
* Only technical (0.30) and risk (0.10) are point-in-time reconstructible --
  40% of composite weight. See `elite_scorer` for why the rest cannot be.
* The universe is a static present-day list, so results carry survivorship and
  selection bias. See `universe.py`. Trust the *relative* comparisons.
* Long-only, no shorting, no costs in the IC statistic. Costs enter the
  walk-forward trade simulation, which is a separate report.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .data_provider import (
    _CACHE_DIR_RESEARCH,
    CachedPriceProvider,
    DataProvider,
    DataQualityError,
)
from .elite_scorer import ScorerConfig, forward_returns, score_history
from .universe import BENCHMARK, get_universe

logger = logging.getLogger(__name__)

DEFAULT_HORIZONS = (21, 63)
REPORT_DIR = Path(__file__).resolve().parent / "_reports"
# The scored panel is the expensive artifact: ~33k rows costs ~27 minutes of
# CPU because every row re-runs the real analyzers on an expanding window.
# Phase 3 fits its model on this panel, so it is persisted rather than
# recomputed. Parquet, not pickle: this outlives a pandas upgrade.
PANEL_PATH = REPORT_DIR / "elite_panel.parquet"


@dataclass
class ICResult:
    """Information coefficient for one horizon."""

    horizon_days: int
    n_dates: int
    n_observations: int
    mean_ic: float
    median_ic: float
    ic_std: float
    # Non-overlapping: the figure to quote.
    t_stat: float
    n_independent_dates: int
    # Overlapping: reported only to expose the inflation factor.
    t_stat_overlapping: float
    hit_rate: float  # share of dates with IC > 0
    decile_spread: float  # mean per-date top-minus-bottom quintile spread
    # Significance of that spread, on non-overlapping dates.
    spread_t_stat: float = 0.0
    spread_negative_share: float = 0.0


@dataclass
class BucketRow:
    bucket: int
    composite_low: float
    composite_high: float
    n: float
    mean_fwd_return: float
    median_fwd_return: float
    win_rate: float


@dataclass
class OOSReport:
    """A reproducible evaluation run."""

    generated_at: str
    data_hash: str
    universe: List[str]
    n_tickers_scored: int
    start: str
    end: str
    eval_every_bars: int
    composite_weight_covered: float
    composite_min: float
    composite_max: float
    n_panel_rows: int
    ic: Dict[str, ICResult] = field(default_factory=dict)
    component_ic: Dict[str, Dict[str, float]] = field(default_factory=dict)
    buckets: Dict[str, List[BucketRow]] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


# ---------------------------------------------------------------------------
# Data-quality gate
# ---------------------------------------------------------------------------

# Below this 0-100 score a ticker is excluded from the panel entirely: a bad
# price series produces confident-looking scores from corrupt inputs, which is
# worse than a smaller universe.
MIN_QUALITY_SCORE = 70.0


def _load_quality_validator(log: Callable[[str], None]):
    """Return a `DataQualityValidator`, or None if the module is unavailable.

    `modules/data_quality_validator.py` had zero importers before this. Note
    carefully what it does and does not provide:

    IMPLEMENTED and used here -- OHLC relationship checks, stuck-price
    detection, extreme-return detection, zero-volume runs, missing-data gaps,
    and a 0-100 quality score.

    NOT IMPLEMENTED, despite the docstrings -- survivorship-bias correction.
    `get_historical_sp500_constituents()` logs a warning and returns an empty
    list; the delisted-stock database holds two hardcoded entries and has no
    backing file. So wiring this module does *not* address survivorship bias,
    and the caveat in `universe.py` still stands in full. Closing that gap needs
    a point-in-time membership vendor, which is out of scope here.
    """
    try:
        from data_quality_validator import DataQualityValidator

        return DataQualityValidator()
    except Exception as exc:  # noqa: BLE001 - optional dependency
        log(f"data-quality gate unavailable ({type(exc).__name__}); proceeding without it")
        return None


def _check_quality(
    validator, ticker: str, history: pd.DataFrame, log: Callable[[str], None]
) -> Optional[str]:
    """Return a rejection reason, or None if the series is fit to score."""
    try:
        report = validator.validate_data(ticker, history)
    except Exception as exc:  # noqa: BLE001 - never let the gate abort a run
        log(f"{ticker}: quality check errored ({type(exc).__name__}); allowing")
        return None

    if getattr(report, "is_delisted", False):
        return "delisted"
    score = float(getattr(report, "quality_score", 100.0))
    if score < MIN_QUALITY_SCORE:
        issues = getattr(report, "issues", []) or []
        detail = ", ".join(str(getattr(i, "issue_type", i)) for i in issues[:3])
        return f"score {score:.0f} < {MIN_QUALITY_SCORE:.0f} ({detail})"
    return None


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def build_panel(
    provider: DataProvider,
    tickers: List[str],
    as_of: pd.Timestamp,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    cfg: Optional[ScorerConfig] = None,
    log: Callable[[str], None] = logger.info,
) -> pd.DataFrame:
    """Score every ticker's history and attach forward-return labels.

    Returns a long panel indexed by (date, ticker). Rows whose label is not yet
    observable are dropped per horizon at analysis time, not here, so the same
    panel serves every horizon.
    """
    cfg = cfg or ScorerConfig()

    try:
        benchmark = provider.get_history(BENCHMARK, as_of, min_bars=250)
    except DataQualityError as exc:
        raise RuntimeError(f"benchmark {BENCHMARK} unavailable: {exc}") from exc
    benchmark_returns = benchmark["Close"].pct_change().dropna()

    quality_gate = _load_quality_validator(log)

    frames: List[pd.DataFrame] = []
    for ticker in tickers:
        try:
            history = provider.get_history(ticker, as_of, min_bars=cfg.warmup_bars + 60)
        except DataQualityError as exc:
            log(f"SKIP {ticker}: {exc}")
            continue

        if quality_gate is not None:
            verdict = _check_quality(quality_gate, ticker, history, log)
            if verdict is not None:
                log(f"SKIP {ticker}: data quality {verdict}")
                continue

        scored = score_history(history, ticker, benchmark_returns, cfg)
        if scored.empty:
            log(f"SKIP {ticker}: no scoreable bars")
            continue

        labels = forward_returns(history["Close"], horizons)
        merged = scored.join(labels, how="left")
        merged["ticker"] = ticker
        frames.append(merged)
        log(f"{ticker}: {len(merged)} scored bars")

    if not frames:
        raise RuntimeError("no tickers produced a scoreable history")

    panel = pd.concat(frames)
    panel.index.name = "date"
    return panel.reset_index().set_index(["date", "ticker"]).sort_index()


# ---------------------------------------------------------------------------
# Information coefficient
# ---------------------------------------------------------------------------


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation. NaN when undefined (constant input)."""
    if len(a) < 3:
        return np.nan
    ar, br = a.rank(), b.rank()
    if ar.std(ddof=0) == 0 or br.std(ddof=0) == 0:
        return np.nan
    return float(np.corrcoef(ar, br)[0, 1])


def compute_ic(panel: pd.DataFrame, horizon: int, min_names: int = 8) -> ICResult:
    """Cross-sectional IC of composite vs forward return at one horizon."""
    label = f"fwd_{horizon}d"
    usable = panel[["composite", label]].dropna()

    per_date: Dict[pd.Timestamp, float] = {}
    for date, group in usable.groupby(level="date"):
        if len(group) < min_names:
            continue
        ic = _spearman(group["composite"], group[label])
        if not np.isnan(ic):
            per_date[date] = ic

    if not per_date:
        raise RuntimeError(f"no dates had >= {min_names} names with a {horizon}d label")

    series = pd.Series(per_date).sort_index()
    mean_ic = float(series.mean())
    std_ic = float(series.std(ddof=1))

    # Overlapping t-stat: inflated, shown only for comparison.
    t_overlap = mean_ic / (std_ic / np.sqrt(len(series))) if std_ic > 0 else 0.0

    # Non-overlapping: take every horizon-th date so samples are independent.
    dates = series.index
    step = max(1, int(np.ceil(horizon / _median_spacing_days(dates))))
    independent = series.iloc[::step]
    if len(independent) > 2 and independent.std(ddof=1) > 0:
        t_indep = float(
            independent.mean() / (independent.std(ddof=1) / np.sqrt(len(independent)))
        )
    else:
        t_indep = 0.0

    return ICResult(
        horizon_days=horizon,
        n_dates=len(series),
        n_observations=int(len(usable)),
        mean_ic=mean_ic,
        median_ic=float(series.median()),
        ic_std=std_ic,
        t_stat=t_indep,
        n_independent_dates=int(len(independent)),
        t_stat_overlapping=float(t_overlap),
        hit_rate=float((series > 0).mean()),
        decile_spread=_decile_spread(usable, label),
        spread_t_stat=_spread_t_stat(usable, label, step),
        spread_negative_share=_spread_negative_share(usable, label),
    )


def _spread_t_stat(usable: pd.DataFrame, label: str, step: int) -> float:
    """t-statistic of the quintile spread, on non-overlapping dates only."""
    series = _spread_series(usable, label)
    if len(series) < 3:
        return 0.0
    independent = series.iloc[:: max(1, step)]
    if len(independent) < 3 or independent.std(ddof=1) == 0:
        return 0.0
    return float(
        independent.mean() / (independent.std(ddof=1) / np.sqrt(len(independent)))
    )


def _spread_negative_share(usable: pd.DataFrame, label: str) -> float:
    """Share of dates on which the top quintile UNDERPERFORMED the bottom."""
    series = _spread_series(usable, label)
    return float((series < 0).mean()) if len(series) else float("nan")


def _median_spacing_days(index: pd.DatetimeIndex) -> float:
    """Median trading-day spacing between evaluation dates (>=1)."""
    if len(index) < 2:
        return 1.0
    gaps = pd.Series(index).diff().dropna().dt.days
    # Calendar days -> approximate trading days.
    return max(1.0, float(gaps.median()) * (252.0 / 365.0))


def _spread_series(
    usable: pd.DataFrame, label: str, column: str = "composite"
) -> pd.Series:
    """Per-date top-minus-bottom quintile forward-return spread.

    Returned as a series rather than a mean so its significance can be tested.
    The bucket profile is stronger evidence than the IC t-statistic: a spread
    that is monotone in the *same* direction at two independent horizons is hard
    to attribute to noise even when |t| on the mean IC is below 2.
    """
    out: Dict[pd.Timestamp, float] = {}
    for date, group in usable.groupby(level="date"):
        if len(group) < 10:
            continue
        try:
            buckets = pd.qcut(
                group[column].rank(method="first"), 5, labels=False, duplicates="drop"
            )
        except ValueError:
            continue
        means = group.groupby(buckets)[label].mean()
        if len(means) >= 2:
            out[date] = float(means.iloc[-1] - means.iloc[0])
    return pd.Series(out).sort_index()


def _decile_spread(usable: pd.DataFrame, label: str, column: str = "composite") -> float:
    """Top-minus-bottom quintile forward return, ranked WITHIN each date.

    Within-date ranking for the same reason the IC is per-date: pooling lets the
    market's own moves masquerade as score skill.
    """
    if len(usable) < 100:
        return float("nan")
    series = _spread_series(usable, label, column)
    return float(series.mean()) if len(series) else float("nan")


def compute_component_ic(
    panel: pd.DataFrame, horizon: int, min_names: int = 8
) -> Dict[str, float]:
    """Mean cross-sectional IC for each score component and raw technical input.

    This is the most diagnostic output in the report. The composite is a weighted
    blend, so a poor composite IC could come from any leg -- and knowing *which*
    leg is inverted is what makes the finding actionable rather than merely
    discouraging.
    """
    label = f"fwd_{horizon}d"
    candidates = [
        c
        for c in (
            "composite",
            "score_technical",
            "score_risk",
            "rsi",
            "adx",
            "beta",
            "annualized_volatility",
        )
        if c in panel.columns
    ]

    out: Dict[str, float] = {}
    for column in candidates:
        usable = panel[[column, label]].dropna()
        ics: List[float] = []
        for _, group in usable.groupby(level="date"):
            if len(group) < min_names:
                continue
            ic = _spearman(group[column], group[label])
            if not np.isnan(ic):
                ics.append(ic)
        out[column] = float(np.mean(ics)) if ics else float("nan")
    return out


def compute_buckets(
    panel: pd.DataFrame, horizon: int, n_buckets: int = 5, min_names: int = 8
) -> List[BucketRow]:
    """Forward return by composite bucket, bucketed WITHIN each date.

    Monotonicity is the practical test: a usable score should show forward
    return rising with the bucket. A flat or inverted profile means the score is
    not ordering outcomes, whatever its mean IC happens to be.

    Bucketing is by within-date rank, not by pooled score. Pooling is actively
    misleading here: composite level drifts with the market, so a pooled
    top-bucket is over-weighted toward bull-market dates and its "outperformance"
    is partly just those dates being good for everything. Ranking within the
    cross-section removes the shared market move, which is the same reason the
    IC is measured per-date. (An earlier pooled version of this function showed a
    monotonic profile that reversed sign once dates were controlled for.)
    """
    label = f"fwd_{horizon}d"
    usable = panel[["composite", label]].dropna()
    if len(usable) < n_buckets * 20:
        return []

    parts: List[pd.DataFrame] = []
    for _, group in usable.groupby(level="date"):
        if len(group) < max(min_names, n_buckets):
            continue
        try:
            ranks = pd.qcut(
                group["composite"].rank(method="first"),
                n_buckets,
                labels=False,
                duplicates="drop",
            )
        except ValueError:
            continue
        chunk = group.copy()
        chunk["bucket"] = ranks
        parts.append(chunk)

    if not parts:
        return []
    ranked = pd.concat(parts)

    rows: List[BucketRow] = []
    for bucket, group in ranked.groupby("bucket"):
        rows.append(
            BucketRow(
                bucket=int(bucket),
                composite_low=float(group["composite"].min()),
                composite_high=float(group["composite"].max()),
                n=float(len(group)),
                mean_fwd_return=float(group[label].mean()),
                median_fwd_return=float(group[label].median()),
                win_rate=float((group[label] > 0).mean()),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_oos_report(
    tickers: Optional[List[str]] = None,
    as_of: Optional[str] = None,
    eval_every: int = 5,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    provider: Optional[DataProvider] = None,
    log: Callable[[str], None] = print,
    panel_path: Optional[Path] = None,
) -> OOSReport:
    """Build the panel and evaluate it. `eval_every=5` scores weekly."""
    # Deliberately NOT the default `_cache`: thirteen files there are committed
    # test fixtures frozen at 2023-12-29 (see data_provider._CACHE_DIR_RESEARCH).
    # A research run needs current data, so it gets its own directory.
    provider = provider or CachedPriceProvider(cache_dir=_CACHE_DIR_RESEARCH)
    tickers = tickers or get_universe()
    as_of_ts = pd.Timestamp(as_of) if as_of else pd.Timestamp.today().normalize()

    cfg = ScorerConfig(eval_every=eval_every)
    log(f"scoring {len(tickers)} tickers through {as_of_ts.date()} (every {eval_every} bars)")
    panel = build_panel(provider, tickers, as_of_ts, horizons, cfg, log=lambda m: None)

    n_tickers = panel.index.get_level_values("ticker").nunique()
    dates = panel.index.get_level_values("date")
    log(f"panel: {len(panel)} rows, {n_tickers} tickers, {dates.min().date()}..{dates.max().date()}")

    saved_panel = save_panel(panel, panel_path)
    log(f"panel cached: {saved_panel}")

    ic = {f"{h}d": compute_ic(panel, h) for h in horizons}
    component_ic = {f"{h}d": compute_component_ic(panel, h) for h in horizons}
    buckets = {f"{h}d": compute_buckets(panel, h) for h in horizons}

    import TradingAnalysis_ELITE as elite

    # Share of the composite the scorer can rebuild point-in-time, computed the
    # way the live composite weighs its legs. The original model also ranked on
    # a risk leg; the alpha-only composite does not, so only technical remains.
    raw = elite.Config.REGIME_WEIGHTS["SIDEWAYS"]
    legs = ("technical", "fundamental", "valuation", "quality", "sentiment")
    if "risk" in raw:  # a pre-alpha-only weight table
        legs += ("risk",)
    used = {k: max(0.0, raw.get(k, 0.0)) for k in legs}
    covered = (used.get("technical", 0.0) + used.get("risk", 0.0)) / (sum(used.values()) or 1.0)

    report = OOSReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        data_hash=provider.run_data_hash() if hasattr(provider, "run_data_hash") else "n/a",
        universe=list(tickers),
        n_tickers_scored=int(n_tickers),
        start=str(dates.min().date()),
        end=str(dates.max().date()),
        eval_every_bars=eval_every,
        composite_weight_covered=float(covered),
        composite_min=float(panel["composite"].min()),
        composite_max=float(panel["composite"].max()),
        n_panel_rows=int(len(panel)),
        ic=ic,
        component_ic=component_ic,
        buckets=buckets,
        caveats=[
            "Only the price-based legs (technical, and risk where the model has one) are "
            f"point-in-time reconstructible: {covered:.0%} of SIDEWAYS composite weight. The other four components are "
            "held constant because no as-of-date source exists; varying them with "
            "present-day values would be look-ahead bias.",
            "STRONG_BUY (>=75) is unreachable in this configuration, so no result here "
            "validates the full model.",
            "Universe is a static present-day list: results carry survivorship and "
            "selection bias. Absolute numbers are optimistic; relative comparisons are "
            "the trustworthy part.",
            "t_stat is computed on non-overlapping dates. t_stat_overlapping is shown "
            "only to expose the inflation from overlapping forward returns and must not "
            "be quoted.",
            "IC statistics exclude transaction costs. Costs enter the walk-forward trade "
            "simulation, which is a separate report.",
        ],
    )
    return report


def format_report(report: OOSReport) -> str:
    """Human-readable summary."""
    lines: List[str] = []
    add = lines.append

    add("=" * 78)
    add("ELITE COMPOSITE - OUT-OF-SAMPLE EVALUATION")
    add("=" * 78)
    add(f"generated      {report.generated_at}")
    add(f"data hash      {report.data_hash[:32]}...")
    add(f"period         {report.start} .. {report.end}")
    add(f"panel          {report.n_panel_rows:,} rows / {report.n_tickers_scored} tickers"
        f" / every {report.eval_every_bars} bars")
    add(f"composite      range [{report.composite_min:.1f}, {report.composite_max:.1f}]"
        f"  covering {report.composite_weight_covered:.0%} of model weight")
    add("")
    add("-" * 78)
    add("INFORMATION COEFFICIENT  (cross-sectional Spearman, composite vs fwd return)")
    add("-" * 78)
    add(f"{'horizon':>8} {'mean IC':>9} {'median':>8} {'t-stat':>8} {'n_indep':>8}"
        f" {'IC>0':>7} {'Q5-Q1':>8} {'t(Q5-Q1)':>9} {'Q5<Q1':>7}")
    for key, res in report.ic.items():
        add(
            f"{key:>8} {res.mean_ic:>9.4f} {res.median_ic:>8.4f} {res.t_stat:>8.2f}"
            f" {res.n_independent_dates:>8} {res.hit_rate:>6.1%}"
            f" {res.decile_spread:>7.2%} {res.spread_t_stat:>9.2f}"
            f" {res.spread_negative_share:>6.1%}"
        )
    add("")
    add("  reference: |IC| 0.02-0.05 = useful factor; ~0.00 = no signal;")
    add("             >0.10 = suspect leakage. Sign matters: negative = inverted.")
    add(f"  |t| > 2.0 is the conventional significance bar.")
    add("")

    if report.component_ic:
        add("-" * 78)
        add("IC BY COMPONENT  (which leg carries or destroys the signal)")
        add("-" * 78)
        cols = sorted({k for d in report.component_ic.values() for k in d})
        header = f"{'input':>24}" + "".join(f"{h:>12}" for h in report.component_ic)
        add(header)
        for col in cols:
            row = f"{col:>24}"
            for h in report.component_ic:
                v = report.component_ic[h].get(col, float('nan'))
                row += f"{v:>+12.4f}"
            add(row)
        add("")

    for key, rows in report.buckets.items():
        if not rows:
            continue
        add("-" * 78)
        add(f"FORWARD {key} RETURN BY COMPOSITE BUCKET  (monotonicity test)")
        add("-" * 78)
        add(f"{'bucket':>7} {'composite range':>20} {'n':>7} {'mean':>9} {'median':>9} {'win%':>7}")
        for row in rows:
            rng = f"{row.composite_low:.1f}-{row.composite_high:.1f}"
            add(
                f"{row.bucket:>7} {rng:>20} {int(row.n):>7,} {row.mean_fwd_return:>8.2%}"
                f" {row.median_fwd_return:>8.2%} {row.win_rate:>6.1%}"
            )
        means = [r.mean_fwd_return for r in rows]
        monotone = all(b >= a for a, b in zip(means, means[1:]))
        add(f"  monotonically increasing: {monotone}")
        add("")

    add("-" * 78)
    add("CAVEATS")
    add("-" * 78)
    for i, caveat in enumerate(report.caveats, 1):
        add(f"{i}. {caveat}")
    add("=" * 78)
    return "\n".join(lines)


def save_panel(panel: pd.DataFrame, path: Optional[Path] = None) -> Path:
    """Persist the scored panel so downstream work need not recompute it."""
    path = path or PANEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.reset_index().to_parquet(path, index=False)
    return path


def load_panel(path: Optional[Path] = None) -> pd.DataFrame:
    """Load a previously saved panel, restoring the (date, ticker) index."""
    path = path or PANEL_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"No cached panel at {path}. Build one with "
            "`python -m core.apex_quant.run elite-oos`."
        )
    frame = pd.read_parquet(path)
    return frame.set_index(["date", "ticker"]).sort_index()


def save_report(report: OOSReport, name: str = "elite_oos") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}.json"
    path.write_text(report.to_json() + "\n", encoding="utf-8")
    (REPORT_DIR / f"{name}.txt").write_text(format_report(report) + "\n", encoding="utf-8")
    return path
