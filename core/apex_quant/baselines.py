"""Sanity baselines.

A strategy must beat BOTH of these on a risk-adjusted basis or it has no
demonstrated edge:

  * random_entry : same number of trades per ticker and the same ATR-based exit
    rule, but entry DATES are randomized. Tests whether entry timing matters.
  * random_exit  : same entry dates as the real strategy, but stop/target
    multiples are randomized within a plausible band. Tests whether the exit
    rule matters.

Both are seeded for reproducibility.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .strategy import Signal, StrategyParams


def _eligible_indices(feats: pd.DataFrame, params: StrategyParams) -> List[int]:
    valid = feats[["close", "rsi14", "atr_pct", "mom63", "dollar_vol20"]].notna().all(axis=1)
    # Leave room to enter on the next bar.
    return [i for i in range(len(feats) - 1) if bool(valid.iloc[i])]


def random_entry_signals(
    real_signals: List[Signal],
    feats_by_ticker: Dict[str, pd.DataFrame],
    params: StrategyParams,
    seed: int = 0,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
) -> List[Signal]:
    """Same trade count per ticker, same ATR exit rule, randomized entry dates."""
    rng = np.random.default_rng(seed)
    counts: Dict[str, int] = {}
    for s in real_signals:
        counts[s.ticker] = counts.get(s.ticker, 0) + 1

    out: List[Signal] = []
    for ticker, k in counts.items():
        feats = feats_by_ticker.get(ticker)
        if feats is None:
            continue
        elig = [i for i in _eligible_indices(feats, params)
                if (start is None or feats.index[i] >= start)
                and (end is None or feats.index[i + 1] < end)]
        if not elig:
            continue
        chosen = rng.choice(elig, size=min(k, len(elig)), replace=False)
        for i in np.atleast_1d(chosen):
            i = int(i)
            ap = float(feats["atr_pct"].iloc[i])
            out.append(
                Signal(
                    ticker=ticker,
                    signal_date=feats.index[i],
                    entry_index=i,
                    stop_pct=params.atr_stop_mult * ap,
                    target_pct=params.atr_target_mult * ap,
                    atr_pct=ap,
                    adv_dollars=float(feats["dollar_vol20"].iloc[i]),
                )
            )
    return out


def random_exit_signals(
    real_signals: List[Signal],
    seed: int = 0,
) -> List[Signal]:
    """Same entry dates, randomized stop/target multiples (random exit rule)."""
    rng = np.random.default_rng(seed)
    out: List[Signal] = []
    for s in real_signals:
        stop_mult = rng.uniform(1.0, 3.0)
        tgt_mult = rng.uniform(1.5, 5.0)
        out.append(
            Signal(
                ticker=s.ticker,
                signal_date=s.signal_date,
                entry_index=s.entry_index,
                stop_pct=stop_mult * s.atr_pct,
                target_pct=tgt_mult * s.atr_pct,
                atr_pct=s.atr_pct,
                adv_dollars=s.adv_dollars,
            )
        )
    return out
