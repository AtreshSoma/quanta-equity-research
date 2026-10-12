"""Forward trade simulation (labeling).

Given an entry bar and absolute stop/target levels, walk forward bar-by-bar to
determine the outcome. This produces the empirical P(target before stop) and
realized returns that the expectancy gate consumes.

Conservative same-bar rule: if a bar's range touches BOTH stop and target, we
assume the STOP filled first (worst case). This avoids optimistic labeling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class TradeOutcome:
    outcome: str            # "target" | "stop" | "time"
    entry_price: float
    exit_price: float
    bars_held: int
    gross_return_pct: float


def long_bar_exit(open_price: float, low: float, high: float,
                  stop: float, target: float):
    """Long stop-market / limit-target fills, before execution costs.

    Opening gaps are observed before the intraday range. When neither level
    crosses at the open, ambiguous bars use the conservative stop-first rule.
    """
    if open_price <= stop:
        return "stop", open_price
    if open_price >= target:
        return "target", open_price
    if low <= stop:
        return "stop", stop
    if high >= target:
        return "target", target
    return None


def simulate_forward(
    ohlcv: pd.DataFrame,
    entry_index: int,
    stop_pct: float,
    target_pct: float,
    max_hold_days: int,
) -> Optional[TradeOutcome]:
    """Simulate a single long trade entered at the open of ``entry_index + 1``.

    Returns None if there is no next bar to enter on.
    """
    n = len(ohlcv)
    entry_bar = entry_index + 1
    if entry_bar >= n:
        return None

    entry_price = float(ohlcv["Open"].iloc[entry_bar])
    if entry_price <= 0:
        return None
    stop_price = entry_price * (1.0 - stop_pct)
    target_price = entry_price * (1.0 + target_pct)

    last_bar = min(entry_bar + max_hold_days, n - 1)
    for j in range(entry_bar, last_bar + 1):
        low = float(ohlcv["Low"].iloc[j])
        high = float(ohlcv["High"].iloc[j])
        fill = long_bar_exit(float(ohlcv["Open"].iloc[j]), low, high,
                             stop_price, target_price)
        if fill:
            outcome, exit_price = fill
            return TradeOutcome(
                outcome, entry_price, exit_price, j - entry_bar,
                exit_price / entry_price - 1.0,
            )

    exit_price = float(ohlcv["Close"].iloc[last_bar])
    return TradeOutcome(
        "time", entry_price, exit_price, last_bar - entry_bar,
        exit_price / entry_price - 1.0,
    )
