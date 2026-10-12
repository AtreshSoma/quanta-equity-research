"""Walk-forward analysis (the core validation deliverable).

Procedure
---------
1. Split the timeline into consecutive (train -> strictly-later test) folds.
2. On each TRAIN window, select the best parameter set from a small grid by
   in-sample risk-adjusted return (Sharpe). Test data is never used for tuning.
3. Apply the chosen params to the TEST window only and collect out-of-sample,
   cost-adjusted daily returns and closed-trade returns.
4. Concatenate the non-overlapping OOS test windows into one continuous OOS
   equity curve.
5. Run the SAME OOS windows for: buy-and-hold SPY, random-entry baseline, and
   random-exit baseline. The strategy must beat all three on a risk-adjusted
   basis to claim an edge.

Multiple-testing control: the grid is intentionally tiny, selection is on train
data only, and each OOS window is evaluated exactly once (one-touch held-out),
so the concatenated OOS curve is honestly out-of-sample.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import baselines, indicators, metrics
from .costs import CostModel
from .data_provider import DataProvider, DataQualityError
from .simulator import SimConfig, SimResult, simulate
from .strategy import Signal, StrategyParams, generate_signals
from .universe import BENCHMARK


@dataclass
class FoldResult:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    chosen_params: Any
    train_sharpe: float
    oos: SimResult


@dataclass
class WalkForwardResult:
    folds: List[FoldResult]
    strategy: metrics.PerformanceReport
    spy: metrics.PerformanceReport
    random_entry: metrics.PerformanceReport
    random_exit: metrics.PerformanceReport
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    data_hash: str
    param_stability: Dict[str, List[float]] = field(default_factory=dict)
    validation_inputs: Dict[str, Any] = field(default_factory=dict)
    coverage_complete: bool = False

    def beats_benchmarks(self) -> bool:
        s = self.strategy.sharpe
        return (
            s > self.spy.sharpe
            and s > self.random_entry.sharpe
            and s > self.random_exit.sharpe
            and self.strategy.expectancy_pct > 0.0
        )


def _filter_window(signals: List[Signal], start, end) -> List[Signal]:
    return [s for s in signals if start <= s.signal_date < end]


def _sharpe(result: SimResult) -> float:
    dr = result.daily_returns
    sd = dr.std(ddof=0)
    if sd <= 0 or len(dr) == 0:
        return float("-inf")
    return float(dr.mean() / sd * np.sqrt(metrics.TRADING_DAYS))


def run_walk_forward(
    provider: DataProvider,
    tickers: List[str],
    start: str,
    end: str,
    train_years: int = 3,
    test_years: int = 1,
    cfg: Optional[SimConfig] = None,
    cost_model: Optional[CostModel] = None,
    seed: int = 7,
    log: Callable[[str], None] = print,
    # --- pluggable strategy (Phase 2) ----------------------------------
    # Defaults reproduce the original rule-based APEX strategy exactly, so
    # existing callers and the apex_quant tests are unaffected. Supplying all
    # four lets a different model -- e.g. the ELITE composite via
    # elite_strategy.py -- reuse this harness, and in particular reuse the
    # random-entry and random-exit baselines it must beat.
    param_grid: Optional[List[Any]] = None,
    signal_fn: Optional[Callable[..., List[Signal]]] = None,
    feature_fn: Optional[Callable[[pd.DataFrame, str], pd.DataFrame]] = None,
    stability_keys: Optional[Sequence[str]] = None,
) -> WalkForwardResult:
    cfg = cfg or SimConfig()
    grid_fn = param_grid if param_grid is not None else StrategyParams.grid()
    make_signals = signal_fn or generate_signals
    make_features = feature_fn or (lambda df, tk: indicators.feature_frame(df))
    stability_fields = tuple(
        stability_keys or ("atr_stop_mult", "atr_target_mult", "rsi_max")
    )
    cost_model = cost_model or CostModel()
    end_ts = pd.Timestamp(end)

    # --- load + featurize (point-in-time through `end`) -------------------
    data: Dict[str, pd.DataFrame] = {}
    feats_by_ticker: Dict[str, pd.DataFrame] = {}
    for tk in tickers:
        try:
            df = provider.get_history(tk, end_ts, min_bars=cfg.max_hold_days + 250)
        except DataQualityError as e:
            log(f"SKIP {tk}: {e}")
            continue
        data[tk] = df
        feats_by_ticker[tk] = make_features(df, tk)
    if not data:
        raise RuntimeError("no tradeable tickers passed the data-quality gate")

    # Benchmark for alpha/beta and the buy-and-hold comparison.
    try:
        spy = provider.get_history(BENCHMARK, end_ts, min_bars=250)
    except DataQualityError as e:
        raise RuntimeError(f"benchmark {BENCHMARK} unavailable: {e}")

    # --- precompute signals per parameter set (across all tickers) --------
    grid = grid_fn
    signals_by_param: Dict[int, List[Signal]] = {}
    for pi, params in enumerate(grid):
        sigs: List[Signal] = []
        for tk, df in data.items():
            sigs.extend(make_signals(df, feats_by_ticker[tk], params, tk))
        signals_by_param[pi] = sigs
        log(f"grid[{pi}] {params}: {len(sigs)} raw signals")

    master_index = pd.DatetimeIndex(
        sorted(set().union(*[df.index for df in data.values()]))
    )
    master_index = master_index[
        (master_index >= pd.Timestamp(start)) & (master_index <= end_ts)
    ]

    # --- build folds ------------------------------------------------------
    folds: List[FoldResult] = []
    cursor = pd.Timestamp(start) + pd.DateOffset(years=train_years)
    while cursor + pd.DateOffset(years=test_years) <= end_ts:
        train_start = cursor - pd.DateOffset(years=train_years)
        train_end = cursor
        test_start = cursor
        test_end = cursor + pd.DateOffset(years=test_years)

        train_cal = master_index[
            (master_index >= train_start) & (master_index < train_end)
        ]
        test_cal = master_index[
            (master_index >= test_start) & (master_index < test_end)
        ]

        # Select best params on TRAIN only.
        best_pi, best_sharpe = 0, float("-inf")
        for pi, sigs in signals_by_param.items():
            train_sigs = _filter_window(sigs, train_start, train_end)
            res = simulate(data, train_sigs, train_cal, cost_model, cfg)
            sh = _sharpe(res)
            if sh > best_sharpe:
                best_pi, best_sharpe = pi, sh
        chosen = grid[best_pi]

        # Apply to TEST only.
        test_sigs = _filter_window(signals_by_param[best_pi], test_start, test_end)
        oos = simulate(data, test_sigs, test_cal, cost_model, cfg)
        folds.append(
            FoldResult(
                train_start, train_end, test_start, test_end,
                chosen, best_sharpe, oos,
            )
        )
        chosen_desc = "/".join(
            f"{k}={getattr(chosen, k, '?')}" for k in stability_fields
        )
        log(
            f"fold {test_start.date()}..{test_end.date()}: "
            f"params={chosen_desc} train_sharpe={best_sharpe:.2f} "
            f"oos_trades={oos.n_trades}"
        )
        cursor = test_end

    if not folds:
        raise RuntimeError("no folds produced; widen the date range")

    # --- assemble OOS series (strategy) -----------------------------------
    strat_daily = pd.concat([f.oos.daily_returns for f in folds])
    strat_trades = [r for f in folds for r in f.oos.trade_returns]
    turnover = float(np.mean([f.oos.turnover for f in folds]))
    oos_start = folds[0].test_start
    oos_end = folds[-1].test_end

    spy_daily_full = metrics.buy_and_hold_returns(spy["Close"])
    spy_daily = spy_daily_full.loc[
        (spy_daily_full.index >= oos_start) & (spy_daily_full.index < oos_end)
    ]
    spy_trades = list(spy_daily.values)  # for trade-stat fields (approx)

    # --- baselines over the same OOS windows ------------------------------
    re_daily_parts, re_trades = [], []
    rx_daily_parts, rx_trades = [], []
    for k, f in enumerate(folds):
        test_cal = master_index[
            (master_index >= f.test_start) & (master_index < f.test_end)
        ]
        real_test = _filter_window(
            signals_by_param[grid.index(f.chosen_params)], f.test_start, f.test_end
        )
        re_sigs = baselines.random_entry_signals(
            real_test, feats_by_ticker, f.chosen_params, seed=seed + k,
            start=f.test_start, end=f.test_end,
        )
        re_sigs = _filter_window(re_sigs, f.test_start, f.test_end)
        re_res = simulate(data, re_sigs, test_cal, cost_model, cfg)
        re_daily_parts.append(re_res.daily_returns)
        re_trades.extend(re_res.trade_returns)

        rx_sigs = baselines.random_exit_signals(real_test, seed=seed + k)
        rx_res = simulate(data, rx_sigs, test_cal, cost_model, cfg)
        rx_daily_parts.append(rx_res.daily_returns)
        rx_trades.extend(rx_res.trade_returns)

    re_daily = pd.concat(re_daily_parts) if re_daily_parts else pd.Series(dtype=float)
    rx_daily = pd.concat(rx_daily_parts) if rx_daily_parts else pd.Series(dtype=float)

    strat_report = metrics.equity_metrics(strat_daily, strat_trades, turnover, spy_daily)
    spy_report = metrics.equity_metrics(spy_daily, spy_trades, 1.0, spy_daily)
    re_report = metrics.equity_metrics(re_daily, re_trades, turnover, spy_daily)
    rx_report = metrics.equity_metrics(rx_daily, rx_trades, turnover, spy_daily)

    stability = {
        key: [getattr(f.chosen_params, key, float("nan")) for f in folds]
        for key in stability_fields
    }

    data_hash = provider.run_data_hash() if hasattr(provider, "run_data_hash") else "n/a"

    return WalkForwardResult(
        folds=folds,
        strategy=strat_report,
        spy=spy_report,
        random_entry=re_report,
        random_exit=rx_report,
        oos_start=oos_start,
        oos_end=oos_end,
        data_hash=data_hash,
        param_stability=stability,
        coverage_complete=set(data) == set(tickers),
        validation_inputs={
            "params": {
                "grid": [asdict(p) for p in grid],
                "selected": [asdict(f.chosen_params) for f in folds],
                "signal_function": f"{make_signals.__module__}.{make_signals.__qualname__}",
                "simulation": asdict(cfg), "seed": seed,
                "start": start, "end": end,
                "train_years": train_years, "test_years": test_years,
                "requested_universe": sorted(set(tickers)),
            },
            "universe": sorted(data),
            "costs": asdict(cost_model),
        },
    )
