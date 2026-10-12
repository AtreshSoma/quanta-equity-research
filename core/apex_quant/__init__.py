"""APEX Quant - evidence-based, backtested swing/position research pipeline.

This package is a price/volume-only rebuild of the legacy APEX v3.1 screener.
It deliberately excludes fundamentals: no validated point-in-time fundamentals
source is configured, so per the project mandate the fundamental component is
dropped rather than faked with restated yfinance `.info` data.

Design priorities (in order): correctness of data/methodology, demonstrated
out-of-sample expectancy after costs, reproducibility, code quality.

Modules
-------
data_provider : point-in-time OHLCV with caching, hashing, data-quality gate.
universe      : fixed liquid universe + explicit survivorship-bias caveat.
indicators    : Wilder-smoothed RSI/ATR and other documented features.
costs         : commission + half-spread + volatility/liquidity slippage.
strategy      : rule-based entries with ATR/structural stops & targets.
labeling      : forward bar-by-bar trade simulation -> outcome labels.
simulator     : portfolio simulator (sizing, integer shares, kill-switch).
metrics       : CAGR/Sharpe/Sortino/maxDD/Calmar/alpha-beta/expectancy.
baselines     : random-entry and random-exit sanity baselines.
walkforward   : walk-forward analysis with one-touch out-of-sample test.
validation    : validation artifact I/O + refuse-to-size confidence gate.
run           : CLI orchestration.
"""

__all__ = [
    "data_provider",
    "universe",
    "indicators",
    "costs",
    "strategy",
    "labeling",
    "simulator",
    "metrics",
    "baselines",
    "walkforward",
    "validation",
]
