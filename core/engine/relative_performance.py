"""Stock return versus its local benchmark over standard windows.

Both series are reduced to session dates first (an NSE bar and a US bar carry
different time zones), and every window is measured over the SAME sessions for
the stock and the benchmark. A window without enough common sessions is
reported as unavailable, never as zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

WINDOWS = (("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252))


def _by_session(series: pd.Series) -> pd.Series:
    s = series.dropna().copy()
    s.index = pd.DatetimeIndex([pd.Timestamp(v).date() for v in s.index])
    if s.index.has_duplicates:
        s = s.groupby(level=0).last()
    return s.sort_index()


def relative_performance(closes: pd.Series, benchmark_returns: Optional[pd.Series],
                         benchmark: str) -> Dict[str, Any]:
    """Cumulative return of stock and benchmark over each window, in percent."""
    out: Dict[str, Any] = {"benchmark": benchmark, "rows": [], "basis": "total return, dividend-adjusted closes"}
    if closes is None or len(closes) < 2:
        return out
    stock = _by_session(closes)
    bench_level = None
    if benchmark_returns is not None and len(benchmark_returns) > 1:
        bench_level = (1 + _by_session(benchmark_returns)).cumprod()
    rows: List[Dict[str, Any]] = []
    for label, sessions in WINDOWS:
        row: Dict[str, Any] = {"window": label, "sessions": sessions,
                               "stock_pct": None, "benchmark_pct": None, "excess_pct": None}
        if len(stock) > sessions:
            end, start = stock.index[-1], stock.index[-1 - sessions]
            row["stock_pct"] = round(float(stock.iloc[-1] / stock.iloc[-1 - sessions] - 1) * 100, 2)
            if bench_level is not None:
                common = bench_level.loc[(bench_level.index >= start) & (bench_level.index <= end)]
                # The benchmark must have a level ON the window's start session;
                # a series that begins later would measure a shorter window and
                # report a near-miss number as if it were the same period.
                if start in bench_level.index and len(common) >= int(sessions * 0.9):
                    row["benchmark_pct"] = round(float(common.iloc[-1] / common.iloc[0] - 1) * 100, 2)
                    row["excess_pct"] = round(row["stock_pct"] - row["benchmark_pct"], 2)
        rows.append(row)
    out["rows"] = rows
    return out
