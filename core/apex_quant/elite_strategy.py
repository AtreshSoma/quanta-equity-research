"""ELITE composite as a tradeable strategy, for the existing walk-forward harness.

Why this exists alongside the IC report
---------------------------------------
`elite_oos` answers a *cross-sectional* question: on a given day, does a higher
composite rank pick the better stock? This module answers a different one: does
"enter when the composite crosses a threshold, exit on an ATR stop or target"
have positive expectancy after costs?

Those can disagree. A score with no cross-sectional ranking power could still
work as a market-timing filter (only be long when the composite is high), and a
score with real ranking power could still lose money once stops and commissions
are applied. The IC does not settle the trading question and the trade
simulation does not settle the ranking question, so both are reported.

The harness contract
--------------------
`walkforward.run_walk_forward` needs three things, and this module supplies
ELITE-flavoured versions of each while reusing the surrounding machinery
(fold construction, train-only parameter selection, cost model, and -- most
importantly -- the random-entry and random-exit baselines the strategy must
beat):

  * a parameter grid
  * `generate(ohlcv, feats, params, ticker) -> List[Signal]`
  * a feature frame

The feature frame keeps the five columns `baselines` reads (`close`, `rsi14`,
`atr_pct`, `mom63`, `dollar_vol20`) so the baselines work unmodified, and adds
`composite`.

Cost of the composite, and why the panel is reused
--------------------------------------------------
Recomputing the composite means re-running the real analyzers on an expanding
window for every bar: ~27 minutes of CPU for the full universe. So the feature
frame is assembled from the panel `elite_oos` already persisted. Build it once
with `python -m core.apex_quant.run elite-oos`; this module then reads it.

Because the panel is scored every `eval_every` bars (weekly by default), the
`composite` column is NaN between evaluations. Signals therefore only fire on
evaluated bars, which is the honest representation: a weekly-scored model
cannot act daily.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators
from .strategy import Signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EliteParams:
    """Tunables for the composite entry rule.

    `atr_stop_mult` and `atr_target_mult` carry the same names as
    `StrategyParams` because `baselines.random_entry_signals` reads them by
    attribute -- keeping the names identical is what lets the baselines run
    against this strategy without modification.
    """

    min_composite: float = 50.0
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 4.0
    min_dollar_vol: float = 5e7
    require_crossing: bool = True

    @staticmethod
    def grid() -> List["EliteParams"]:
        """A deliberately small grid, for the same multiple-testing reason
        `StrategyParams.grid()` is small.

        The composite thresholds are chosen against the *reachable* band. With
        four components pinned the score spans roughly [29.5, 67.5] and in
        practice [35, 59], so ELITE's own ladder values (55 = ACCUMULATE,
        65 = BUY) sit at or beyond the top of the observed range. Testing 65
        would select almost nothing; these values probe the top of the
        distribution that actually occurs.
        """
        out: List[EliteParams] = []
        for min_comp in (48.0, 50.0, 52.0):
            for stop_m in (1.5, 2.0):
                for tgt_m in (3.0, 4.0):
                    out.append(
                        EliteParams(
                            min_composite=min_comp,
                            atr_stop_mult=stop_m,
                            atr_target_mult=tgt_m,
                        )
                    )
        return out


STABILITY_KEYS = ("min_composite", "atr_stop_mult", "atr_target_mult")


def feature_frame_from_panel(
    ohlcv: pd.DataFrame, panel: pd.DataFrame, ticker: str
) -> pd.DataFrame:
    """Standard causal features plus the composite, aligned to `ohlcv.index`.

    The composite is left NaN on bars the panel did not evaluate. It is
    deliberately NOT forward-filled: carrying a week-old score forward would
    let the strategy act on information at a freshness the scorer never
    claimed, and would quietly change what is being tested.
    """
    feats = indicators.feature_frame(ohlcv)

    if ticker in panel.index.get_level_values("ticker"):
        scored = panel.xs(ticker, level="ticker")
        feats = feats.join(scored[["composite"]], how="left")
    else:
        feats["composite"] = np.nan

    return feats


def generate_elite_signals(
    ohlcv: pd.DataFrame,
    feats: pd.DataFrame,
    params: EliteParams,
    ticker: str,
) -> List[Signal]:
    """Emit an entry signal when the composite crosses above the threshold.

    A signal at bar t means enter at the open of t+1, matching
    `strategy.generate_signals` and `labeling.simulate_forward`.

    With `require_crossing` (the default) only the first evaluated bar at or
    above the threshold fires. Without it, every evaluated bar above the
    threshold fires, which floods the simulator with overlapping entries in the
    same name and measures position-stacking rather than signal quality.
    """
    if "composite" not in feats.columns:
        return []

    composite = feats["composite"]
    atr_pct = feats["atr_pct"]
    dollar_vol = feats["dollar_vol20"]

    evaluated = composite.notna()
    if not evaluated.any():
        return []

    signals: List[Signal] = []
    previous_above: Optional[bool] = None

    for i in range(len(feats) - 1):  # need a next bar to enter on
        if not evaluated.iloc[i]:
            continue
        if pd.isna(atr_pct.iloc[i]) or pd.isna(dollar_vol.iloc[i]):
            continue

        above = bool(composite.iloc[i] >= params.min_composite)

        fires = above and (previous_above is not True or not params.require_crossing)
        previous_above = above

        if not fires:
            continue
        if float(dollar_vol.iloc[i]) < params.min_dollar_vol:
            continue

        ap = float(atr_pct.iloc[i])
        if ap <= 0:
            continue

        signals.append(
            Signal(
                ticker=ticker,
                signal_date=feats.index[i],
                entry_index=i,
                stop_pct=params.atr_stop_mult * ap,
                target_pct=params.atr_target_mult * ap,
                atr_pct=ap,
                adv_dollars=float(dollar_vol.iloc[i]),
            )
        )

    return signals


def build_feature_frames(
    data: Dict[str, pd.DataFrame], panel: pd.DataFrame
) -> Dict[str, pd.DataFrame]:
    """Feature frames for every ticker, from the cached panel."""
    return {
        ticker: feature_frame_from_panel(frame, panel, ticker)
        for ticker, frame in data.items()
    }
