"""Shared technical-indicator maths (pure pandas/numpy).

Why this module exists: Wilder RSI/ATR/ADX were implemented separately in
SP_ShortTerm.py, SP_ELITE_PRO.py and (via the ``ta`` library) in
TradingAnalysis_ELITE.py. This is the single reference, built from the two
hand-rolled Wilder implementations, with explicit ``min_periods`` so the
warm-up is visible in the output.

NaN semantics (the contract every caller can rely on):
  * Not enough history -> NaN. Never a neutral placeholder (RSI 50, ADX 25,
    price*1.02 bands). A caller that wants to treat NaN as "unmeasured" can
    simply test ``np.isfinite``.
  * Undefined by the maths (flat series: RSI 0/0, DI sum of 0) -> NaN.
  * A pure up-streak (zero average loss, positive gain) is RSI 100.

All functions take and return pandas objects aligned on the input index, so a
caller picks ``.iloc[-1]`` for the latest reading. Inputs are expected to be
aligned already (same index for High/Low/Close).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

__all__ = ["wilder_rma", "rsi", "true_range", "atr", "adx", "macd", "bollinger"]


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing: EMA with alpha = 1/period, NaN until ``period`` obs."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI. NaN during warm-up and when the series is perfectly flat."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder_rma(gain, period)
    avg_loss = wilder_rma(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    zero_loss = avg_loss.notna() & (avg_loss <= 0)
    out = out.mask(zero_loss & (avg_gain > 0), 100.0)
    out = out.mask(zero_loss & (avg_gain <= 0), np.nan)   # flat: undefined, not 50
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                     axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR (RMA of True Range). NaN until ``period`` bars."""
    return wilder_rma(true_range(high, low, close), period)


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder ADX with +DI / -DI. Returns ``(adx, plus_di, minus_di)``.
    A window with zero directional movement is DX 0 (measured no-trend).

    Directional movement is mutually exclusive (only the larger move on a bar
    counts). ADX needs roughly ``2 * period`` bars before it is meaningful; the
    series is NaN until the first ``period`` DX values exist plus the ADX
    smoothing window.
    """
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_s = atr(high, low, close, period).replace(0.0, np.nan)
    plus_di = 100.0 * wilder_rma(plus_dm, period) / atr_s
    minus_di = 100.0 * wilder_rma(minus_dm, period) / atr_s
    raw_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / raw_sum.replace(0.0, np.nan)
    # No directional movement at all over a valid ATR window is a measured
    # "no trend" (DX 0), not a missing reading. (SP_ShortTerm leaves this NaN;
    # the two agree everywhere else.) A zero/NaN ATR stays NaN.
    dx = dx.mask((raw_sum == 0.0) & atr_s.notna(), 0.0)
    return wilder_rma(dx, period), plus_di, minus_di


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram. NaN until each EMA has its window."""
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def bollinger(close: pd.Series, window: int = 20,
              num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger ``(upper, middle, lower)``; sample std (ddof=1). NaN until ``window`` bars."""
    mid = close.rolling(window=window, min_periods=window).mean()
    std = close.rolling(window=window, min_periods=window).std()
    return mid + num_std * std, mid, mid - num_std * std
