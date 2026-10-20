"""Point-in-time reconstruction of the ELITE composite score.

Purpose
-------
`docs/logs/RECALIBRATION_PLAN.md` blocker H-09: the live scoring model has never been
validated out of sample. This module makes that possible by recomputing the
composite as it *would have been seen* on each historical bar, using the real
`TradingAnalysis_ELITE` analyzers rather than a reimplementation. Testing a
reimplementation would validate the wrong thing.

WHAT CAN AND CANNOT BE RECONSTRUCTED
------------------------------------
Only inputs derivable from the price series have point-in-time history
available. yfinance exposes *current* fundamentals, analyst targets, sentiment
and insider flow with no as-of dimension, so using them to score a 2023 bar
would mean scoring the past with facts from the future -- textbook look-ahead
bias, and precisely the error this harness exists to prevent.

So the unavailable components are held at the value the engine itself
assigns to a default (empty) input. Weights are read from the live
``Config.REGIME_WEIGHTS`` at run time, so a rerun measures the current model; the
figures below describe the original ``elite.composite`` (later versions dropped
the risk leg, leaving the technical leg as the only reconstructible input):

    composite = 27.5                      (the four pinned components x 0.60)
              + technical_score  x 0.30
              + risk_score       x 0.10

Note 27.5, not 30. An empty `FundamentalData` does *not* score 50 -- it scores
**40**, because `_calculate_component_scores` starts at 50 and then applies
`roe < 5: -10` to a default ROE of 0.0. The engine therefore treats absent
fundamentals as mildly *bad* rather than unknown, which is defect D8 in the
recalibration plan with a measured magnitude: -10 points of fundamental score,
or -2.5 composite points, for having no data. Reproduced by
`test_pinned_components_hold_the_engines_own_defaults`.

This covers **40% of the SIDEWAYS composite weight** and constrains the score
to [29.5, 67.5]. One consequence worth stating plainly: STRONG_BUY (>=75) is
unreachable in this configuration, so the backtest measures the discriminating
power of the technical-and-risk half of the model, not of the whole model. Any
claim beyond that is unsupported until point-in-time fundamentals exist.

CAUSALITY
---------
Every score at bar t is computed from `ohlcv.iloc[:t+1]`. The benchmark series
used for beta is likewise truncated at t and limited to the trailing window the
live code uses, because the live `_get_spy_returns` fetches `period="1y"`.
`test_elite_scorer.py` asserts both properties directly.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# The engine lives in core/ and resolves its siblings by bare name.
_CORE = Path(__file__).resolve().parent.parent
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

logger = logging.getLogger(__name__)

# Components with no point-in-time source. Pinned, not estimated.
NEUTRAL_SCORE = 50.0
PINNED_COMPONENTS = ("fundamental", "valuation", "quality", "sentiment")
LIVE_COMPONENTS = ("technical", "risk")

# Bars of history the live engine's own trailing window uses for beta.
BETA_WINDOW_BARS = 252

# Minimum history before a score is meaningful. The engine's own gate is
# Config.MIN_DATA_POINTS = 50, but SMA200 needs 200 bars to be non-NaN and the
# technical score reads price-vs-SMA200, so scoring earlier would silently
# evaluate a different (degraded) feature set.
MIN_WARMUP_BARS = 250


@dataclass
class ScorerConfig:
    """Knobs that affect reconstruction, not the strategy."""

    eval_every: int = 1          # bars between evaluations; 1 = every bar
    warmup_bars: int = MIN_WARMUP_BARS
    regime: Optional[str] = None  # None -> Config.REGIME_WEIGHTS['SIDEWAYS']


class _PointInTimeRiskAnalyzer:
    """`RiskAnalyzer` with its benchmark fetch replaced by a truncated series.

    The live analyzer calls `yf.Ticker("SPY").history(period="1y")` inside
    `_calculate_beta`. During a backtest that would (a) hit the network once per
    bar and (b) hand bar t a benchmark series running to the present day, making
    every historical beta look-ahead-contaminated.

    This wrapper serves the trailing `BETA_WINDOW_BARS` of benchmark returns
    ending at the as-of date, matching the live window without the leakage.
    """

    def __init__(self, benchmark_returns: pd.Series):
        import TradingAnalysis_ELITE as elite

        self._inner = elite.RiskAnalyzer()
        self._benchmark = benchmark_returns.sort_index()
        self._asof: Optional[pd.Timestamp] = None
        # Replace the bound method on this instance only; the class is untouched.
        self._inner._get_spy_returns = self._spy_returns  # type: ignore[method-assign]

    def _spy_returns(self) -> pd.Series:
        if self._asof is None:
            return self._benchmark
        window = self._benchmark.loc[self._benchmark.index <= self._asof]
        return window.iloc[-BETA_WINDOW_BARS:]

    def analyze(self, data: pd.DataFrame, ticker: str, asof: pd.Timestamp):
        self._asof = asof
        return self._inner.analyze(data, ticker)


def _neutral_inputs():
    """Neutral instances of the components with no point-in-time source."""
    import TradingAnalysis_ELITE as elite

    return {
        "fundamental": elite.FundamentalData(),
        "sentiment": elite.SentimentData(),
        "insider": elite.InsiderData(),
        "market_intel": elite.MarketIntelligence(),
        "valuation": elite.ValuationMetrics(),
    }


def score_history(
    ohlcv: pd.DataFrame,
    ticker: str,
    benchmark_returns: pd.Series,
    cfg: Optional[ScorerConfig] = None,
) -> pd.DataFrame:
    """Recompute ELITE component scores causally across one ticker's history.

    Args:
        ohlcv: Adjusted OHLCV, ascending date index.
        ticker: Symbol, used only for the engine's logging and sanitisation.
        benchmark_returns: Daily benchmark returns for beta. Truncated per bar.
        cfg: Reconstruction settings.

    Returns:
        Frame indexed by evaluated dates with the component scores, the
        composite, and the auxiliary columns the strategy layer needs
        (`atr_pct`, `dollar_vol20`, `close`). Non-evaluated bars are absent
        rather than forward-filled, so a caller cannot mistake a stale score
        for a fresh one.
    """
    import TradingAnalysis_ELITE as elite

    cfg = cfg or ScorerConfig()

    if ohlcv is None or len(ohlcv) <= cfg.warmup_bars:
        return pd.DataFrame()

    technical_analyzer = elite.TechnicalAnalyzer()
    risk_analyzer = _PointInTimeRiskAnalyzer(benchmark_returns)
    signal_generator = elite.SignalGenerator()
    neutral = _neutral_inputs()

    regime_key = cfg.regime or "SIDEWAYS"
    weights = dict(elite.Config.REGIME_WEIGHTS[regime_key])

    # Auxiliary series are computed once over the whole frame. Every one is
    # causal (rolling / shift only), so a full-series computation and a
    # per-bar recomputation agree at every index -- asserted in the tests.
    close = ohlcv["Close"]
    atr = elite_atr_pct(ohlcv)
    dollar_vol = (close * ohlcv["Volume"]).rolling(20, min_periods=20).mean()

    rows: list[dict] = []
    eval_positions = range(cfg.warmup_bars, len(ohlcv), max(1, cfg.eval_every))

    for i in eval_positions:
        window = ohlcv.iloc[: i + 1]
        asof = ohlcv.index[i]

        try:
            technical = technical_analyzer.analyze(window)
            risk = risk_analyzer.analyze(window, ticker, asof)
        except Exception as exc:  # noqa: BLE001 - one bad bar must not kill the run
            logger.debug("%s @ %s: analyzer failed: %s", ticker, asof, exc)
            continue

        scores = signal_generator._calculate_component_scores(
            technical,
            neutral["fundamental"],
            risk,
            neutral["sentiment"],
            neutral["insider"],
            neutral["market_intel"],
            neutral["valuation"],
        )
        composite = signal_generator._calculate_composite(scores, weights)

        rows.append(
            {
                "date": asof,
                "close": float(close.iloc[i]),
                "composite": float(composite),
                **{f"score_{k}": float(v) for k, v in scores.items()},
                # Raw technicals worth keeping for Phase 3 feature work.
                "rsi": float(technical.rsi),
                "adx": float(technical.adx),
                "macd_signal": technical.macd_signal,
                "trend_direction": technical.trend_direction,
                "beta": float(risk.beta),
                "risk_grade": risk.risk_grade,
                "annualized_volatility": float(risk.annualized_volatility),
                "atr_pct": float(atr.iloc[i]) if not pd.isna(atr.iloc[i]) else np.nan,
                "dollar_vol20": (
                    float(dollar_vol.iloc[i]) if not pd.isna(dollar_vol.iloc[i]) else np.nan
                ),
            }
        )

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows).set_index("date")
    frame.index = pd.DatetimeIndex(frame.index)
    return frame


def elite_atr_pct(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR as a fraction of close, Wilder-smoothed.

    Reuses `apex_quant.indicators.wilder_atr` so the backtest and the existing
    strategy measure volatility identically -- two ATR definitions in one
    comparison would make the results incomparable.
    """
    from . import indicators

    atr = indicators.wilder_atr(ohlcv["High"], ohlcv["Low"], ohlcv["Close"], period)
    return atr / ohlcv["Close"]


def forward_returns(
    close: pd.Series, horizons: tuple[int, ...] = (21, 63)
) -> pd.DataFrame:
    """Forward simple returns over each horizon, in bars.

    Row t holds the return from t to t+h, so it is *not* known at t. These are
    labels, never features; mixing them into a feature frame is the classic
    leakage. The final h rows are NaN by construction.
    """
    out = pd.DataFrame(index=close.index)
    for h in horizons:
        out[f"fwd_{h}d"] = close.shift(-h) / close - 1.0
    return out
