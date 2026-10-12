"""Price/volume technical features with documented formulas.

All smoothing uses Wilder's method where Wilder defined it (RSI, ATR), matching
the canonical definitions rather than a simple rolling mean. Every function is
causal: the value at bar t uses only bars <= t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    r"""Relative Strength Index using Wilder's smoothing.

    delta_t      = close_t - close_{t-1}
    gain_t       = max(delta_t, 0),  loss_t = max(-delta_t, 0)
    The first avg gain/loss is the simple mean of the first ``period`` values;
    thereafter Wilder smoothing:
        avg_t = (avg_{t-1} * (period - 1) + value_t) / period
    RS  = avg_gain / avg_loss
    RSI = 100 - 100 / (1 + RS)
    Implemented via EMA with alpha = 1/period (== Wilder smoothing).
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 (only gains), RSI is 100 by definition.
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    return rsi


def wilder_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    r"""Average True Range using Wilder's smoothing.

    TR_t = max( high_t - low_t,
                |high_t - close_{t-1}|,
                |low_t  - close_{t-1}| )
    ATR  = Wilder-smoothed TR (EMA with alpha = 1/period).
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average over ``period`` bars."""
    return series.rolling(period, min_periods=period).mean()


def dollar_volume(close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    """Rolling mean of close * volume (a liquidity proxy in dollars)."""
    return (close * volume).rolling(period, min_periods=period).mean()


def realized_vol(close: pd.Series, period: int = 20) -> pd.Series:
    """Annualized realized volatility from daily log returns (252 trading days)."""
    logret = np.log(close / close.shift(1))
    return logret.rolling(period, min_periods=period).std() * np.sqrt(252.0)


def roc(close: pd.Series, period: int = 63) -> pd.Series:
    """Rate of change over ``period`` bars (e.g. 63 ~ one quarter)."""
    return close / close.shift(period) - 1.0


def feature_frame(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Compute the standard causal feature set used by the strategy.

    Returns a frame aligned to ``ohlcv.index``. Rows before warmup are NaN.
    """
    close, high, low, vol = (
        ohlcv["Close"],
        ohlcv["High"],
        ohlcv["Low"],
        ohlcv["Volume"],
    )
    feats = pd.DataFrame(index=ohlcv.index)
    feats["close"] = close
    feats["rsi14"] = wilder_rsi(close, 14)
    feats["atr14"] = wilder_atr(high, low, close, 14)
    feats["atr_pct"] = feats["atr14"] / close
    feats["sma50"] = sma(close, 50)
    feats["sma200"] = sma(close, 200)
    feats["dollar_vol20"] = dollar_volume(close, vol, 20)
    feats["mom63"] = roc(close, 63)
    feats["vol_ratio"] = vol / vol.rolling(20, min_periods=20).mean()
    return feats
