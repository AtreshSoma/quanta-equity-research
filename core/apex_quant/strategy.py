"""Rule-based price/volume strategy.

Philosophy (per the project mandate):
  * Define stop and target by RULE first (ATR multiples off the entry), THEN
    compute reward/risk and let it fail. We do not reverse-engineer targets to
    hit a desired R/R.
  * Entries are a transparent trend + momentum + liquidity filter. No fitted
    "score": each parameter is exposed so the walk-forward harness can select
    it on training data only and report fold-to-fold stability.

A signal at bar t means: enter at the NEXT bar's open (t+1). All features at t
use only bars <= t, so there is no look-ahead in signal generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from . import indicators


@dataclass(frozen=True)
class StrategyParams:
    """Tunable knobs. Kept small and meaningful to limit multiple-testing."""

    sma_long: int = 200
    rsi_min: float = 40.0
    rsi_max: float = 65.0
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 4.0
    mom_min: float = 0.0           # 63-day ROC must exceed this
    min_dollar_vol: float = 5e7    # $50M ADV liquidity floor
    max_hold_days: int = 20

    def grid() -> "List[StrategyParams]":  # type: ignore[misc]
        """A deliberately small grid (multiple-testing control)."""
        out: List[StrategyParams] = []
        for stop_m in (1.5, 2.0, 2.5):
            for tgt_m in (3.0, 4.0):
                for rsi_hi in (60.0, 65.0):
                    out.append(
                        StrategyParams(
                            atr_stop_mult=stop_m,
                            atr_target_mult=tgt_m,
                            rsi_max=rsi_hi,
                        )
                    )
        return out


@dataclass
class Signal:
    ticker: str
    signal_date: pd.Timestamp     # bar t (decision bar)
    entry_index: int              # positional index of t in the feature frame
    stop_pct: float               # stop distance below entry, as a fraction
    target_pct: float             # target distance above entry, as a fraction
    atr_pct: float
    adv_dollars: float


def generate_signals(
    ohlcv: pd.DataFrame,
    feats: pd.DataFrame,
    params: StrategyParams,
    ticker: str,
) -> List[Signal]:
    """Yield entry signals over the full history of one ticker.

    The signal's stop/target are expressed as fractions of the (yet-unknown)
    entry price using the ATR-pct at the decision bar — converted to absolute
    levels by the simulator once the next open is known.
    """
    signals: List[Signal] = []
    sma_long = indicators.sma(ohlcv["Close"], params.sma_long)

    close = feats["close"]
    rsi = feats["rsi14"]
    atr_pct = feats["atr_pct"]
    mom = feats["mom63"]
    advd = feats["dollar_vol20"]

    n = len(feats)
    for i in range(n - 1):  # need a next bar to enter on
        if pd.isna(sma_long.iloc[i]) or pd.isna(atr_pct.iloc[i]):
            continue
        if pd.isna(rsi.iloc[i]) or pd.isna(mom.iloc[i]) or pd.isna(advd.iloc[i]):
            continue

        trend_ok = close.iloc[i] > sma_long.iloc[i]
        rsi_ok = params.rsi_min <= rsi.iloc[i] <= params.rsi_max
        mom_ok = mom.iloc[i] > params.mom_min
        liq_ok = advd.iloc[i] >= params.min_dollar_vol
        if not (trend_ok and rsi_ok and mom_ok and liq_ok):
            continue

        ap = float(atr_pct.iloc[i])
        signals.append(
            Signal(
                ticker=ticker,
                signal_date=feats.index[i],
                entry_index=i,
                stop_pct=params.atr_stop_mult * ap,
                target_pct=params.atr_target_mult * ap,
                atr_pct=ap,
                adv_dollars=float(advd.iloc[i]),
            )
        )
    return signals
