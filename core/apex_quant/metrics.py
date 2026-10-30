"""Performance metrics computed from a daily equity curve (net of costs).

All metrics are computed on a daily portfolio-return series so that the
strategy and the buy-and-hold SPY benchmark are measured identically.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class PerformanceReport:
    n_days: int
    n_trades: int
    total_return_pct: float
    cagr_pct: float
    ann_vol_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    calmar: float
    hit_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy_pct: float
    profit_factor: float
    turnover: float
    alpha_ann_pct: Optional[float] = None
    beta: Optional[float] = None
    # False when the trade-level fields (n_trades, hit rate, avg win/loss,
    # expectancy, profit factor) are not meaningful, e.g. buy-and-hold SPY
    # has no discrete trades. Those fields are then NaN and render as N/A.
    trade_stats_applicable: bool = True

    def to_dict(self) -> Dict[str, float]:
        d = asdict(self)
        # Strict JSON has no NaN: emit null for not-applicable fields.
        return {k: (None if isinstance(v, float) and v != v else v) for k, v in d.items()}


def _drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(-dd.min() * 100.0)


def equity_metrics(
    daily_returns: pd.Series,
    trade_returns: Optional[List[float]],
    turnover: float,
    benchmark_returns: Optional[pd.Series] = None,
) -> PerformanceReport:
    """Build a full report from daily returns + per-trade net returns.

    Pass ``trade_returns=None`` when there are no discrete trades (buy-and-hold):
    the trade-level fields become NaN and ``trade_stats_applicable`` is False,
    rather than being faked from daily returns.
    """
    dr = daily_returns.fillna(0.0)
    equity = (1.0 + dr).cumprod()
    n_days = int(len(dr))
    years = max(n_days / TRADING_DAYS, 1e-9)

    total_return = float(equity.iloc[-1] - 1.0) if n_days else 0.0
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if n_days else 0.0
    ann_vol = float(dr.std(ddof=0) * np.sqrt(TRADING_DAYS)) if n_days else 0.0
    sharpe = float(dr.mean() / dr.std(ddof=0) * np.sqrt(TRADING_DAYS)) if dr.std(ddof=0) > 0 else 0.0
    # Sortino: mean return over DOWNSIDE DEVIATION, sqrt(mean(min(r - MAR, 0)^2))
    # taken over ALL observations with MAR = 0. The old code took the std of
    # only the losing days around their own mean, which ignores how often
    # losses occur and understates risk for strategies with few losing days.
    dd_dev = float(np.sqrt(np.mean(np.minimum(dr.to_numpy(dtype=float), 0.0) ** 2))) if n_days else 0.0
    sortino = float(dr.mean() / dd_dev * np.sqrt(TRADING_DAYS)) if dd_dev > 0 else 0.0
    max_dd = _drawdown(equity) if n_days else 0.0
    calmar = float(cagr * 100.0 / max_dd) if max_dd > 0 else 0.0

    trades_na = trade_returns is None
    tr = np.array([] if trades_na else trade_returns, dtype=float)
    wins = tr[tr > 0]
    losses = tr[tr <= 0]
    hit_rate = float(len(wins) / len(tr) * 100.0) if len(tr) else 0.0
    avg_win = float(wins.mean() * 100.0) if len(wins) else 0.0
    avg_loss = float(losses.mean() * 100.0) if len(losses) else 0.0
    expectancy = float(tr.mean() * 100.0) if len(tr) else 0.0
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else 0.0

    if trades_na:
        nan = float("nan")
        hit_rate = avg_win = avg_loss = expectancy = profit_factor = nan

    alpha_ann = beta = None
    if benchmark_returns is not None and n_days:
        aligned = pd.concat([dr, benchmark_returns], axis=1, join="inner").dropna()
        if len(aligned) > 2 and aligned.iloc[:, 1].var() > 0:
            x = aligned.iloc[:, 1].values
            y = aligned.iloc[:, 0].values
            beta = float(np.cov(y, x)[0, 1] / np.var(x))
            alpha_daily = float(y.mean() - beta * x.mean())
            alpha_ann = float(alpha_daily * TRADING_DAYS * 100.0)

    return PerformanceReport(
        n_days=n_days,
        n_trades=len(tr),
        total_return_pct=total_return * 100.0,
        cagr_pct=cagr * 100.0,
        ann_vol_pct=ann_vol * 100.0,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        calmar=calmar,
        hit_rate_pct=hit_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        expectancy_pct=expectancy,
        profit_factor=profit_factor,
        turnover=turnover,
        alpha_ann_pct=alpha_ann,
        beta=beta,
        trade_stats_applicable=not trades_na,
    )


def buy_and_hold_returns(benchmark_close: pd.Series) -> pd.Series:
    """Daily simple returns of a buy-and-hold benchmark."""
    return benchmark_close.pct_change().fillna(0.0)
