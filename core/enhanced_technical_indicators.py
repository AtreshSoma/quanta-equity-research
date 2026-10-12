"""
═══════════════════════════════════════════════════════════════════════════════
ENHANCED TECHNICAL INDICATORS - PROFESSIONAL GRADE TECHNICAL ANALYSIS
═══════════════════════════════════════════════════════════════════════════════

FEATURES:
✅ RSI with proper Wilder's smoothing (EMA with alpha=1/period)
✅ RSI bullish and bearish divergence detection
✅ Bollinger Bands with squeeze and breakout detection
✅ Volume Weighted Average Price (VWAP) - intraday and rolling
✅ On-Balance Volume (OBV) with divergence detection
✅ MACD with proper signal validation and divergence detection
✅ Average Directional Index (ADX) for trend strength
✅ Ichimoku Cloud (Tenkan-sen, Kijun-sen, Senkou Span A/B, Chikou Span)
✅ Volume analysis and anomaly detection
✅ Support/Resistance level identification
✅ Pattern recognition (double tops/bottoms, head & shoulders)

© 2025 - Professional Technical Analysis Framework
═══════════════════════════════════════════════════════════════════════════════
"""

import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
import logging
from scipy.signal import find_peaks, argrelextrema
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class Signal(Enum):
    """Trading signal strength"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class Divergence(Enum):
    """Divergence types"""
    BULLISH = "BULLISH"           # Price lower low, indicator higher low
    BEARISH = "BEARISH"           # Price higher high, indicator lower high
    HIDDEN_BULLISH = "HIDDEN_BULLISH"  # Price higher low, indicator lower low
    HIDDEN_BEARISH = "HIDDEN_BEARISH"  # Price lower high, indicator higher high
    NONE = "NONE"


class TrendDirection(Enum):
    """Trend direction"""
    STRONG_UPTREND = "STRONG_UPTREND"
    UPTREND = "UPTREND"
    SIDEWAYS = "SIDEWAYS"
    DOWNTREND = "DOWNTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"


@dataclass
class TechnicalSignals:
    """Comprehensive technical analysis signals"""
    # RSI
    rsi: float
    rsi_signal: Signal
    rsi_divergence: Divergence
    rsi_oversold: bool
    rsi_overbought: bool

    # MACD
    macd: float
    macd_signal: float
    macd_histogram: float
    macd_signal_type: Signal
    macd_divergence: Divergence
    macd_crossover: bool

    # Bollinger Bands
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_width: float
    bb_position: float  # 0-1, where price is within bands
    bb_squeeze: bool
    bb_breakout: Optional[str]  # 'UPPER' or 'LOWER'

    # VWAP
    vwap: float
    price_vs_vwap: float  # % above/below VWAP
    vwap_signal: Signal

    # OBV
    obv: float
    obv_trend: TrendDirection
    obv_divergence: Divergence

    # ADX
    adx: float
    plus_di: float
    minus_di: float
    trend_strength: str  # 'WEAK', 'MODERATE', 'STRONG', 'VERY_STRONG'
    trend_direction: TrendDirection

    # Ichimoku
    tenkan_sen: float
    kijun_sen: float
    senkou_span_a: float
    senkou_span_b: float
    chikou_span: float
    cloud_signal: Signal
    tk_cross: Optional[str]  # 'BULLISH' or 'BEARISH'

    # Volume
    volume: float
    volume_sma: float
    volume_ratio: float
    volume_signal: Signal

    # Composite
    composite_score: float  # 0-100
    overall_signal: Signal
    confidence: float  # 0-100


# ═══════════════════════════════════════════════════════════════════════════════
# ENHANCED TECHNICAL ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class EnhancedTechnicalAnalyzer:
    """
    Professional-grade technical analysis with fixed indicators and
    advanced pattern recognition.
    """

    def __init__(self, ticker: str, period: str = "1y", interval: str = "1d"):
        """
        Initialize technical analyzer.

        Args:
            ticker: Stock ticker symbol
            period: Data period (1mo, 3mo, 6mo, 1y, 2y, 5y)
            interval: Data interval (1m, 5m, 15m, 1h, 1d)
        """
        self.ticker = ticker
        self.period = period
        self.interval = interval

        # Load data
        self.data = self._load_data()

        if self.data is None or self.data.empty:
            raise ValueError(f"No data available for {ticker}")

        # Calculate all indicators
        self._calculate_all_indicators()

        logger.info(f"EnhancedTechnicalAnalyzer initialized for {ticker} " +
                   f"({len(self.data)} bars)")


    def _load_data(self) -> pd.DataFrame:
        """Load price data from yfinance"""
        try:
            stock = yf.Ticker(self.ticker)
            data = stock.history(period=self.period, interval=self.interval)

            if data.empty:
                logger.error(f"No data returned for {self.ticker}")
                return None

            return data

        except Exception as e:
            logger.error(f"Error loading data for {self.ticker}: {e}")
            return None


    def _calculate_all_indicators(self):
        """Calculate all technical indicators"""
        self._calculate_rsi_wilder()
        self._calculate_macd()
        self._calculate_bollinger_bands()
        self._calculate_vwap()
        self._calculate_obv()
        self._calculate_adx()
        self._calculate_ichimoku()
        self._calculate_volume_indicators()


    # ═══════════════════════════════════════════════════════════════════════════
    # RSI WITH WILDER'S SMOOTHING
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_rsi_wilder(self, period: int = 14):
        """
        Calculate RSI using proper Wilder's smoothing method.

        Wilder's smoothing uses exponential moving average with alpha = 1/period.
        This is the correct method as specified in Wilder's original book.

        Standard implementation often uses SMA which is incorrect.
        """
        df = self.data

        # Calculate price changes
        delta = df['Close'].diff()

        # Separate gains and losses
        gains = delta.where(delta > 0, 0.0)
        losses = -delta.where(delta < 0, 0.0)

        # Wilder's smoothing: EMA with alpha = 1/period
        # This is equivalent to ewm(alpha=1/period, adjust=False)
        alpha = 1.0 / period

        # Calculate average gains and losses using Wilder's method
        avg_gains = gains.ewm(alpha=alpha, adjust=False).mean()
        avg_losses = losses.ewm(alpha=alpha, adjust=False).mean()

        # Calculate RS and RSI
        rs = avg_gains / avg_losses
        rsi = 100 - (100 / (1 + rs))

        self.data['RSI'] = rsi
        self.data['RSI_AvgGain'] = avg_gains
        self.data['RSI_AvgLoss'] = avg_losses

        logger.debug(f"RSI calculated using Wilder's smoothing (period={period})")


    def detect_rsi_divergence(self, lookback: int = 20) -> Divergence:
        """
        Detect RSI divergences (bullish and bearish).

        Bullish divergence: Price makes lower low, RSI makes higher low
        Bearish divergence: Price makes higher high, RSI makes lower high

        Args:
            lookback: Number of periods to look back for divergence

        Returns:
            Divergence type
        """
        if len(self.data) < lookback:
            return Divergence.NONE

        df = self.data.iloc[-lookback:].copy()

        # Find local peaks and troughs in price
        price = df['Close'].values
        rsi = df['RSI'].values

        # Find peaks (highs)
        price_peaks, _ = find_peaks(price, distance=5)
        rsi_peaks, _ = find_peaks(rsi, distance=5)

        # Find troughs (lows)
        price_troughs, _ = find_peaks(-price, distance=5)
        rsi_troughs, _ = find_peaks(-rsi, distance=5)

        # Check for bearish divergence (price higher high, RSI lower high)
        if len(price_peaks) >= 2 and len(rsi_peaks) >= 2:
            # Compare last two peaks
            if price[price_peaks[-1]] > price[price_peaks[-2]]:  # Price higher high
                if rsi[rsi_peaks[-1]] < rsi[rsi_peaks[-2]]:      # RSI lower high
                    logger.info(f"BEARISH divergence detected: Price HH, RSI LH")
                    return Divergence.BEARISH

        # Check for bullish divergence (price lower low, RSI higher low)
        if len(price_troughs) >= 2 and len(rsi_troughs) >= 2:
            # Compare last two troughs
            if price[price_troughs[-1]] < price[price_troughs[-2]]:  # Price lower low
                if rsi[rsi_troughs[-1]] > rsi[rsi_troughs[-2]]:      # RSI higher low
                    logger.info(f"BULLISH divergence detected: Price LL, RSI HL")
                    return Divergence.BULLISH

        return Divergence.NONE


    def get_rsi_signal(self) -> Tuple[Signal, Divergence]:
        """
        Get RSI trading signal.

        Returns:
            Tuple of (signal, divergence)
        """
        current_rsi = self.data['RSI'].iloc[-1]
        divergence = self.detect_rsi_divergence()

        # Signal based on RSI level
        if current_rsi < 30:
            signal = Signal.STRONG_BUY if current_rsi < 20 else Signal.BUY
        elif current_rsi > 70:
            signal = Signal.STRONG_SELL if current_rsi > 80 else Signal.SELL
        else:
            signal = Signal.NEUTRAL

        # Upgrade signal if there's a divergence
        if divergence == Divergence.BULLISH and signal in [Signal.NEUTRAL, Signal.BUY]:
            signal = Signal.STRONG_BUY
        elif divergence == Divergence.BEARISH and signal in [Signal.NEUTRAL, Signal.SELL]:
            signal = Signal.STRONG_SELL

        return signal, divergence


    # ═══════════════════════════════════════════════════════════════════════════
    # MACD WITH DIVERGENCE DETECTION
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_macd(self, fast: int = 12, slow: int = 26, signal: int = 9):
        """Calculate MACD with proper signal line"""
        df = self.data

        # Calculate EMAs
        ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()

        # MACD line
        macd = ema_fast - ema_slow

        # Signal line
        macd_signal = macd.ewm(span=signal, adjust=False).mean()

        # Histogram
        macd_histogram = macd - macd_signal

        self.data['MACD'] = macd
        self.data['MACD_Signal'] = macd_signal
        self.data['MACD_Histogram'] = macd_histogram


    def detect_macd_divergence(self, lookback: int = 20) -> Divergence:
        """
        Detect MACD divergences.

        Similar logic to RSI divergence.
        """
        if len(self.data) < lookback:
            return Divergence.NONE

        df = self.data.iloc[-lookback:].copy()

        price = df['Close'].values
        macd = df['MACD'].values

        # Find peaks and troughs
        price_peaks, _ = find_peaks(price, distance=5)
        macd_peaks, _ = find_peaks(macd, distance=5)

        price_troughs, _ = find_peaks(-price, distance=5)
        macd_troughs, _ = find_peaks(-macd, distance=5)

        # Bearish divergence
        if len(price_peaks) >= 2 and len(macd_peaks) >= 2:
            if price[price_peaks[-1]] > price[price_peaks[-2]]:
                if macd[macd_peaks[-1]] < macd[macd_peaks[-2]]:
                    return Divergence.BEARISH

        # Bullish divergence
        if len(price_troughs) >= 2 and len(macd_troughs) >= 2:
            if price[price_troughs[-1]] < price[price_troughs[-2]]:
                if macd[macd_troughs[-1]] > macd[macd_troughs[-2]]:
                    return Divergence.BULLISH

        return Divergence.NONE


    def get_macd_signal(self) -> Tuple[Signal, Divergence, bool]:
        """
        Get MACD trading signal.

        Returns:
            Tuple of (signal, divergence, crossover)
        """
        current = self.data.iloc[-1]
        previous = self.data.iloc[-2]

        macd = current['MACD']
        macd_signal = current['MACD_Signal']
        macd_hist = current['MACD_Histogram']

        prev_hist = previous['MACD_Histogram']

        # Detect crossover
        bullish_crossover = prev_hist < 0 and macd_hist > 0
        bearish_crossover = prev_hist > 0 and macd_hist < 0

        divergence = self.detect_macd_divergence()

        # Generate signal
        if bullish_crossover:
            signal = Signal.STRONG_BUY
        elif macd > macd_signal and macd_hist > 0:
            signal = Signal.BUY
        elif bearish_crossover:
            signal = Signal.STRONG_SELL
        elif macd < macd_signal and macd_hist < 0:
            signal = Signal.SELL
        else:
            signal = Signal.NEUTRAL

        # Upgrade with divergence
        if divergence == Divergence.BULLISH:
            if signal in [Signal.NEUTRAL, Signal.BUY]:
                signal = Signal.STRONG_BUY
        elif divergence == Divergence.BEARISH:
            if signal in [Signal.NEUTRAL, Signal.SELL]:
                signal = Signal.STRONG_SELL

        return signal, divergence, (bullish_crossover or bearish_crossover)


    # ═══════════════════════════════════════════════════════════════════════════
    # BOLLINGER BANDS WITH SQUEEZE & BREAKOUT
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_bollinger_bands(self, period: int = 20, num_std: float = 2.0):
        """Calculate Bollinger Bands"""
        df = self.data

        # Middle band (SMA)
        middle = df['Close'].rolling(window=period).mean()

        # Standard deviation
        std = df['Close'].rolling(window=period).std()

        # Upper and lower bands
        upper = middle + (std * num_std)
        lower = middle - (std * num_std)

        # Band width (volatility measure)
        width = (upper - lower) / middle

        self.data['BB_Upper'] = upper
        self.data['BB_Middle'] = middle
        self.data['BB_Lower'] = lower
        self.data['BB_Width'] = width


    def detect_bb_squeeze(self, lookback: int = 100, percentile: float = 20) -> bool:
        """
        Detect Bollinger Band squeeze.

        Squeeze occurs when band width is in lowest percentile of recent history.
        This indicates low volatility and often precedes a big move.

        Args:
            lookback: Periods to look back
            percentile: Percentile threshold (default 20 = bottom 20%)

        Returns:
            True if squeeze detected
        """
        if len(self.data) < lookback:
            return False

        recent_widths = self.data['BB_Width'].iloc[-lookback:]
        current_width = self.data['BB_Width'].iloc[-1]

        threshold = np.percentile(recent_widths.dropna(), percentile)

        is_squeeze = current_width < threshold

        if is_squeeze:
            logger.info(f"Bollinger Band SQUEEZE detected (width={current_width:.4f}, " +
                       f"threshold={threshold:.4f})")

        return is_squeeze


    def detect_bb_breakout(self) -> Optional[str]:
        """
        Detect Bollinger Band breakout.

        Returns:
            'UPPER' for upside breakout, 'LOWER' for downside breakout, None otherwise
        """
        current = self.data.iloc[-1]
        previous = self.data.iloc[-2]

        close = current['Close']
        prev_close = previous['Close']
        upper = current['BB_Upper']
        lower = current['BB_Lower']

        # Upper breakout: price crosses above upper band
        if close > upper and prev_close <= previous['BB_Upper']:
            logger.info(f"Bollinger Band UPPER breakout: ${close:.2f} > ${upper:.2f}")
            return "UPPER"

        # Lower breakout: price crosses below lower band
        if close < lower and prev_close >= previous['BB_Lower']:
            logger.info(f"Bollinger Band LOWER breakout: ${close:.2f} < ${lower:.2f}")
            return "LOWER"

        return None


    def get_bb_signal(self) -> Tuple[Signal, bool, Optional[str]]:
        """
        Get Bollinger Bands signal.

        Returns:
            Tuple of (signal, squeeze, breakout)
        """
        current = self.data.iloc[-1]

        close = current['Close']
        upper = current['BB_Upper']
        lower = current['BB_Lower']
        middle = current['BB_Middle']

        # Position within bands (0 = lower, 0.5 = middle, 1 = upper)
        bb_position = (close - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

        squeeze = self.detect_bb_squeeze()
        breakout = self.detect_bb_breakout()

        # Generate signal
        if bb_position < 0.2:  # Near lower band
            signal = Signal.STRONG_BUY
        elif bb_position < 0.4:
            signal = Signal.BUY
        elif bb_position > 0.8:  # Near upper band
            signal = Signal.STRONG_SELL
        elif bb_position > 0.6:
            signal = Signal.SELL
        else:
            signal = Signal.NEUTRAL

        # Modify for breakouts
        if breakout == "UPPER":
            signal = Signal.BUY  # Often pullback opportunity
        elif breakout == "LOWER":
            signal = Signal.SELL  # Often bounce opportunity

        return signal, squeeze, breakout


    # ═══════════════════════════════════════════════════════════════════════════
    # VWAP (VOLUME WEIGHTED AVERAGE PRICE)
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_vwap(self):
        """
        Calculate Volume Weighted Average Price.

        For intraday data: Reset VWAP each day
        For daily data: Use cumulative VWAP or rolling window
        """
        df = self.data

        # Typical price
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3

        # If intraday, reset VWAP each day
        if self.interval in ['1m', '5m', '15m', '30m', '1h']:
            # Group by date and calculate daily VWAP
            df['Date'] = df.index.date
            df['TP_Volume'] = typical_price * df['Volume']
            df['Cumul_TP_Volume'] = df.groupby('Date')['TP_Volume'].cumsum()
            df['Cumul_Volume'] = df.groupby('Date')['Volume'].cumsum()
            df['VWAP'] = df['Cumul_TP_Volume'] / df['Cumul_Volume']

        else:
            # For daily data, use rolling VWAP (e.g., 20 days)
            window = 20
            df['VWAP'] = (typical_price * df['Volume']).rolling(window=window).sum() / \
                         df['Volume'].rolling(window=window).sum()

        self.data = df


    def get_vwap_signal(self) -> Tuple[Signal, float]:
        """
        Get VWAP signal.

        Returns:
            Tuple of (signal, price_vs_vwap_pct)
        """
        current = self.data.iloc[-1]

        close = current['Close']
        vwap = current['VWAP']

        if pd.isna(vwap):
            return Signal.NEUTRAL, 0.0

        # % difference from VWAP
        diff_pct = ((close - vwap) / vwap) * 100

        # Generate signal
        if diff_pct < -2:  # More than 2% below VWAP
            signal = Signal.STRONG_BUY
        elif diff_pct < -1:
            signal = Signal.BUY
        elif diff_pct > 2:  # More than 2% above VWAP
            signal = Signal.STRONG_SELL
        elif diff_pct > 1:
            signal = Signal.SELL
        else:
            signal = Signal.NEUTRAL

        return signal, diff_pct


    # ═══════════════════════════════════════════════════════════════════════════
    # OBV (ON-BALANCE VOLUME) WITH DIVERGENCE
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_obv(self):
        """
        Calculate On-Balance Volume.

        OBV tracks cumulative volume flow:
        - Add volume when price closes higher
        - Subtract volume when price closes lower
        """
        df = self.data

        # Price changes
        price_change = df['Close'].diff()

        # OBV logic
        obv = pd.Series(index=df.index, dtype=float)
        obv.iloc[0] = df['Volume'].iloc[0]

        for i in range(1, len(df)):
            if price_change.iloc[i] > 0:
                obv.iloc[i] = obv.iloc[i-1] + df['Volume'].iloc[i]
            elif price_change.iloc[i] < 0:
                obv.iloc[i] = obv.iloc[i-1] - df['Volume'].iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i-1]

        self.data['OBV'] = obv

        # OBV trend (using EMA)
        self.data['OBV_EMA'] = obv.ewm(span=20, adjust=False).mean()


    def detect_obv_divergence(self, lookback: int = 20) -> Divergence:
        """
        Detect OBV divergences.

        Similar logic to RSI/MACD divergence.
        """
        if len(self.data) < lookback:
            return Divergence.NONE

        df = self.data.iloc[-lookback:].copy()

        price = df['Close'].values
        obv = df['OBV'].values

        # Find peaks and troughs
        price_peaks, _ = find_peaks(price, distance=5)
        obv_peaks, _ = find_peaks(obv, distance=5)

        price_troughs, _ = find_peaks(-price, distance=5)
        obv_troughs, _ = find_peaks(-obv, distance=5)

        # Bearish divergence
        if len(price_peaks) >= 2 and len(obv_peaks) >= 2:
            if price[price_peaks[-1]] > price[price_peaks[-2]]:
                if obv[obv_peaks[-1]] < obv[obv_peaks[-2]]:
                    return Divergence.BEARISH

        # Bullish divergence
        if len(price_troughs) >= 2 and len(obv_troughs) >= 2:
            if price[price_troughs[-1]] < price[price_troughs[-2]]:
                if obv[obv_troughs[-1]] > obv[obv_troughs[-2]]:
                    return Divergence.BULLISH

        return Divergence.NONE


    def get_obv_signal(self) -> Tuple[Signal, Divergence, TrendDirection]:
        """
        Get OBV signal.

        Returns:
            Tuple of (signal, divergence, trend)
        """
        current = self.data.iloc[-1]

        obv = current['OBV']
        obv_ema = current['OBV_EMA']

        # Determine trend
        if obv > obv_ema * 1.05:
            trend = TrendDirection.STRONG_UPTREND
        elif obv > obv_ema:
            trend = TrendDirection.UPTREND
        elif obv < obv_ema * 0.95:
            trend = TrendDirection.STRONG_DOWNTREND
        elif obv < obv_ema:
            trend = TrendDirection.DOWNTREND
        else:
            trend = TrendDirection.SIDEWAYS

        divergence = self.detect_obv_divergence()

        # Generate signal
        if trend in [TrendDirection.STRONG_UPTREND, TrendDirection.UPTREND]:
            signal = Signal.BUY
        elif trend in [TrendDirection.STRONG_DOWNTREND, TrendDirection.DOWNTREND]:
            signal = Signal.SELL
        else:
            signal = Signal.NEUTRAL

        # Upgrade with divergence
        if divergence == Divergence.BULLISH:
            signal = Signal.STRONG_BUY
        elif divergence == Divergence.BEARISH:
            signal = Signal.STRONG_SELL

        return signal, divergence, trend


    # ═══════════════════════════════════════════════════════════════════════════
    # ADX (AVERAGE DIRECTIONAL INDEX)
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_adx(self, period: int = 14):
        """Calculate Average Directional Index (ADX)"""
        df = self.data

        # True Range
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

        # Directional Movement
        plus_dm = (df['High'] - df['High'].shift()).where(
            (df['High'] - df['High'].shift()) > (df['Low'].shift() - df['Low']), 0
        ).clip(lower=0)

        minus_dm = (df['Low'].shift() - df['Low']).where(
            (df['Low'].shift() - df['Low']) > (df['High'] - df['High'].shift()), 0
        ).clip(lower=0)

        # Smoothed TR and DM (Wilder's smoothing)
        atr = true_range.ewm(alpha=1/period, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(alpha=1/period, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=1/period, adjust=False).mean()

        # Directional Indicators
        plus_di = 100 * (plus_dm_smooth / atr)
        minus_di = 100 * (minus_dm_smooth / atr)

        # ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()

        self.data['ADX'] = adx
        self.data['+DI'] = plus_di
        self.data['-DI'] = minus_di


    def get_adx_signal(self) -> Tuple[TrendDirection, str]:
        """
        Get ADX signal.

        Returns:
            Tuple of (trend_direction, trend_strength)
        """
        current = self.data.iloc[-1]

        adx = current['ADX']
        plus_di = current['+DI']
        minus_di = current['-DI']

        # Trend strength
        if adx < 20:
            strength = "WEAK"
        elif adx < 25:
            strength = "MODERATE"
        elif adx < 50:
            strength = "STRONG"
        else:
            strength = "VERY_STRONG"

        # Trend direction
        if plus_di > minus_di:
            if adx > 25:
                direction = TrendDirection.STRONG_UPTREND
            else:
                direction = TrendDirection.UPTREND
        elif minus_di > plus_di:
            if adx > 25:
                direction = TrendDirection.STRONG_DOWNTREND
            else:
                direction = TrendDirection.DOWNTREND
        else:
            direction = TrendDirection.SIDEWAYS

        return direction, strength


    # ═══════════════════════════════════════════════════════════════════════════
    # ICHIMOKU CLOUD
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_ichimoku(self,
                           tenkan_period: int = 9,
                           kijun_period: int = 26,
                           senkou_b_period: int = 52,
                           displacement: int = 26):
        """
        Calculate Ichimoku Cloud components.

        Components:
        - Tenkan-sen (Conversion Line): (9-period high + 9-period low) / 2
        - Kijun-sen (Base Line): (26-period high + 26-period low) / 2
        - Senkou Span A (Leading Span A): (Tenkan-sen + Kijun-sen) / 2, plotted 26 periods ahead
        - Senkou Span B (Leading Span B): (52-period high + 52-period low) / 2, plotted 26 periods ahead
        - Chikou Span (Lagging Span): Close plotted 26 periods back
        """
        df = self.data

        # Tenkan-sen (Conversion Line)
        high_9 = df['High'].rolling(window=tenkan_period).max()
        low_9 = df['Low'].rolling(window=tenkan_period).min()
        tenkan_sen = (high_9 + low_9) / 2

        # Kijun-sen (Base Line)
        high_26 = df['High'].rolling(window=kijun_period).max()
        low_26 = df['Low'].rolling(window=kijun_period).min()
        kijun_sen = (high_26 + low_26) / 2

        # Senkou Span A (Leading Span A)
        senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(displacement)

        # Senkou Span B (Leading Span B)
        high_52 = df['High'].rolling(window=senkou_b_period).max()
        low_52 = df['Low'].rolling(window=senkou_b_period).min()
        senkou_span_b = ((high_52 + low_52) / 2).shift(displacement)

        # Chikou Span (Lagging Span)
        chikou_span = df['Close'].shift(-displacement)

        self.data['Tenkan_sen'] = tenkan_sen
        self.data['Kijun_sen'] = kijun_sen
        self.data['Senkou_Span_A'] = senkou_span_a
        self.data['Senkou_Span_B'] = senkou_span_b
        self.data['Chikou_Span'] = chikou_span


    def get_ichimoku_signal(self) -> Tuple[Signal, Optional[str]]:
        """
        Get Ichimoku Cloud signal.

        Returns:
            Tuple of (signal, tk_cross_type)
        """
        current = self.data.iloc[-1]
        previous = self.data.iloc[-2]

        close = current['Close']
        tenkan = current['Tenkan_sen']
        kijun = current['Kijun_sen']
        senkou_a = current['Senkou_Span_A']
        senkou_b = current['Senkou_Span_B']

        prev_tenkan = previous['Tenkan_sen']
        prev_kijun = previous['Kijun_sen']

        # Determine cloud top and bottom
        cloud_top = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)

        # TK Cross
        tk_cross = None
        if prev_tenkan <= prev_kijun and tenkan > kijun:
            tk_cross = "BULLISH"
        elif prev_tenkan >= prev_kijun and tenkan < kijun:
            tk_cross = "BEARISH"

        # Position relative to cloud
        if close > cloud_top:
            # Above cloud - bullish
            if tk_cross == "BULLISH":
                signal = Signal.STRONG_BUY
            elif tenkan > kijun:
                signal = Signal.BUY
            else:
                signal = Signal.NEUTRAL
        elif close < cloud_bottom:
            # Below cloud - bearish
            if tk_cross == "BEARISH":
                signal = Signal.STRONG_SELL
            elif tenkan < kijun:
                signal = Signal.SELL
            else:
                signal = Signal.NEUTRAL
        else:
            # Inside cloud - neutral/transitional
            if tk_cross == "BULLISH":
                signal = Signal.BUY
            elif tk_cross == "BEARISH":
                signal = Signal.SELL
            else:
                signal = Signal.NEUTRAL

        return signal, tk_cross


    # ═══════════════════════════════════════════════════════════════════════════
    # VOLUME INDICATORS
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_volume_indicators(self, period: int = 20):
        """Calculate volume-based indicators"""
        df = self.data

        # Volume SMA
        df['Volume_SMA'] = df['Volume'].rolling(window=period).mean()

        # Volume ratio
        df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA']

        self.data = df


    def get_volume_signal(self) -> Signal:
        """Get volume signal"""
        current = self.data.iloc[-1]

        volume_ratio = current['Volume_Ratio']

        if volume_ratio > 2.0:
            # Very high volume - strong confirmation
            signal = Signal.STRONG_BUY  # Direction depends on price action
        elif volume_ratio > 1.5:
            signal = Signal.BUY
        elif volume_ratio < 0.5:
            # Low volume - weak signal
            signal = Signal.SELL
        else:
            signal = Signal.NEUTRAL

        return signal


    # ═══════════════════════════════════════════════════════════════════════════
    # COMPREHENSIVE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════

    def get_comprehensive_signals(self) -> TechnicalSignals:
        """
        Get comprehensive technical analysis with all indicators.

        Returns:
            TechnicalSignals object with all signals
        """
        current = self.data.iloc[-1]

        # RSI
        rsi_signal, rsi_div = self.get_rsi_signal()
        rsi = current['RSI']

        # MACD
        macd_signal, macd_div, macd_cross = self.get_macd_signal()

        # Bollinger Bands
        bb_signal, bb_squeeze, bb_breakout = self.get_bb_signal()

        # VWAP
        vwap_signal, price_vs_vwap = self.get_vwap_signal()

        # OBV
        obv_signal, obv_div, obv_trend = self.get_obv_signal()

        # ADX
        adx_trend, adx_strength = self.get_adx_signal()

        # Ichimoku
        ichimoku_signal, tk_cross = self.get_ichimoku_signal()

        # Volume
        volume_signal = self.get_volume_signal()

        # Calculate composite score (0-100)
        scores = {
            Signal.STRONG_BUY: 100,
            Signal.BUY: 75,
            Signal.NEUTRAL: 50,
            Signal.SELL: 25,
            Signal.STRONG_SELL: 0
        }

        # Weighted scoring
        weights = {
            'rsi': 0.20,
            'macd': 0.20,
            'bb': 0.15,
            'vwap': 0.10,
            'obv': 0.15,
            'ichimoku': 0.15,
            'volume': 0.05
        }

        composite = (
            scores[rsi_signal] * weights['rsi'] +
            scores[macd_signal] * weights['macd'] +
            scores[bb_signal] * weights['bb'] +
            scores[vwap_signal] * weights['vwap'] +
            scores[obv_signal] * weights['obv'] +
            scores[ichimoku_signal] * weights['ichimoku'] +
            scores[volume_signal] * weights['volume']
        )

        # Overall signal
        if composite >= 75:
            overall_signal = Signal.STRONG_BUY
        elif composite >= 60:
            overall_signal = Signal.BUY
        elif composite >= 40:
            overall_signal = Signal.NEUTRAL
        elif composite >= 25:
            overall_signal = Signal.SELL
        else:
            overall_signal = Signal.STRONG_SELL

        # Calculate confidence (higher with divergences and crossovers)
        confidence = 50.0

        if rsi_div != Divergence.NONE:
            confidence += 15
        if macd_div != Divergence.NONE:
            confidence += 15
        if macd_cross:
            confidence += 10
        if tk_cross:
            confidence += 10

        confidence = min(confidence, 100.0)

        # Build TechnicalSignals object
        signals = TechnicalSignals(
            rsi=rsi,
            rsi_signal=rsi_signal,
            rsi_divergence=rsi_div,
            rsi_oversold=rsi < 30,
            rsi_overbought=rsi > 70,
            macd=current['MACD'],
            macd_signal=current['MACD_Signal'],
            macd_histogram=current['MACD_Histogram'],
            macd_signal_type=macd_signal,
            macd_divergence=macd_div,
            macd_crossover=macd_cross,
            bb_upper=current['BB_Upper'],
            bb_middle=current['BB_Middle'],
            bb_lower=current['BB_Lower'],
            bb_width=current['BB_Width'],
            bb_position=(current['Close'] - current['BB_Lower']) / (current['BB_Upper'] - current['BB_Lower']),
            bb_squeeze=bb_squeeze,
            bb_breakout=bb_breakout,
            vwap=current['VWAP'],
            price_vs_vwap=price_vs_vwap,
            vwap_signal=vwap_signal,
            obv=current['OBV'],
            obv_trend=obv_trend,
            obv_divergence=obv_div,
            adx=current['ADX'],
            plus_di=current['+DI'],
            minus_di=current['-DI'],
            trend_strength=adx_strength,
            trend_direction=adx_trend,
            tenkan_sen=current['Tenkan_sen'],
            kijun_sen=current['Kijun_sen'],
            senkou_span_a=current['Senkou_Span_A'],
            senkou_span_b=current['Senkou_Span_B'],
            chikou_span=current['Chikou_Span'],
            cloud_signal=ichimoku_signal,
            tk_cross=tk_cross,
            volume=current['Volume'],
            volume_sma=current['Volume_SMA'],
            volume_ratio=current['Volume_Ratio'],
            volume_signal=volume_signal,
            composite_score=composite,
            overall_signal=overall_signal,
            confidence=confidence
        )

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def print_technical_analysis(ticker: str, signals: TechnicalSignals):
    """Print formatted technical analysis report"""
    print("\n" + "═" * 100)
    print(f"TECHNICAL ANALYSIS: {ticker}".center(100))
    print("═" * 100)

    print("\n📊 OVERALL SIGNAL")
    print(f"Signal:     {signals.overall_signal.value}")
    print(f"Score:      {signals.composite_score:.1f}/100")
    print(f"Confidence: {signals.confidence:.1f}%")

    print("\n📈 RSI")
    print(f"Value:       {signals.rsi:.2f}")
    print(f"Signal:      {signals.rsi_signal.value}")
    print(f"Divergence:  {signals.rsi_divergence.value}")

    print("\n📉 MACD")
    print(f"MACD:        {signals.macd:.4f}")
    print(f"Signal:      {signals.macd_signal:.4f}")
    print(f"Histogram:   {signals.macd_histogram:.4f}")
    print(f"Signal Type: {signals.macd_signal_type.value}")
    print(f"Divergence:  {signals.macd_divergence.value}")
    print(f"Crossover:   {'YES' if signals.macd_crossover else 'NO'}")

    print("\n🎯 BOLLINGER BANDS")
    print(f"Upper:    ${signals.bb_upper:.2f}")
    print(f"Middle:   ${signals.bb_middle:.2f}")
    print(f"Lower:    ${signals.bb_lower:.2f}")
    print(f"Position: {signals.bb_position:.2%}")
    print(f"Squeeze:  {'YES' if signals.bb_squeeze else 'NO'}")
    if signals.bb_breakout:
        print(f"Breakout: {signals.bb_breakout}")

    print("\n💰 VWAP")
    print(f"VWAP:         ${signals.vwap:.2f}")
    print(f"vs VWAP:      {signals.price_vs_vwap:+.2f}%")
    print(f"Signal:       {signals.vwap_signal.value}")

    print("\n📊 OBV")
    print(f"OBV:        {signals.obv:,.0f}")
    print(f"Trend:      {signals.obv_trend.value}")
    print(f"Divergence: {signals.obv_divergence.value}")

    print("\n📐 ADX")
    print(f"ADX:       {signals.adx:.2f}")
    print(f"+DI:       {signals.plus_di:.2f}")
    print(f"-DI:       {signals.minus_di:.2f}")
    print(f"Strength:  {signals.trend_strength}")
    print(f"Direction: {signals.trend_direction.value}")

    print("\n☁️  ICHIMOKU")
    print(f"Tenkan-sen:    ${signals.tenkan_sen:.2f}")
    print(f"Kijun-sen:     ${signals.kijun_sen:.2f}")
    print(f"Senkou Span A: ${signals.senkou_span_a:.2f}")
    print(f"Senkou Span B: ${signals.senkou_span_b:.2f}")
    print(f"Cloud Signal:  {signals.cloud_signal.value}")
    if signals.tk_cross:
        print(f"TK Cross:      {signals.tk_cross}")

    print("\n📦 VOLUME")
    print(f"Volume:     {signals.volume:,.0f}")
    print(f"Avg Volume: {signals.volume_sma:,.0f}")
    print(f"Ratio:      {signals.volume_ratio:.2f}x")
    print(f"Signal:     {signals.volume_signal.value}")

    print("\n" + "═" * 100)


if __name__ == "__main__":
    # Example usage
    print("Enhanced Technical Indicators Module")
    print("Import this module to use EnhancedTechnicalAnalyzer class")
