#!/usr/bin/env python3
"""
Technical Signal Generator with AI/ML Prediction Engine
========================================================
Generates high-probability trading signals using technical analysis, 
machine learning forecasts, and Monte Carlo simulations.

Features:
- RSI, MACD, ADX, Bollinger Bands, Stochastic analysis
- Candlestick pattern detection
- Volume confirmation
- Multi-timeframe trend analysis
- Confluence-based signal validation
- 🤖 AI/ML Price Prediction (Monte Carlo + Statistical)
- 📊 Probability Forecasting (up/down/sideways)
- 🎯 Support/Resistance Detection
- 💡 Human-Style Advisory Output
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')

# Statistical/ML imports
try:
    from scipy import stats
    from scipy.signal import argrelextrema
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# Technical Analysis imports
try:
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.trend import MACD, ADXIndicator, EMAIndicator, SMAIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    HAS_TA = True
except ImportError:
    HAS_TA = False
    print("⚠️ Warning: 'ta' library not installed. Run: pip install ta")


# =============================================================================
# AI/ML PREDICTION ENGINE
# =============================================================================

@dataclass
class PriceForecast:
    """Price forecast results from Monte Carlo simulation"""
    current_price: float
    forecast_days: int
    # Price targets
    target_bullish: float      # 75th percentile outcome
    target_bearish: float      # 25th percentile outcome
    target_median: float       # 50th percentile (most likely)
    target_extreme_bull: float # 90th percentile
    target_extreme_bear: float # 10th percentile
    # Probabilities
    prob_up_5pct: float        # Probability of +5% move
    prob_down_5pct: float      # Probability of -5% move
    prob_up_10pct: float       # Probability of +10% move
    prob_down_10pct: float     # Probability of -10% move
    # Direction probabilities
    prob_higher: float         # Probability price will be higher
    prob_lower: float          # Probability price will be lower
    # Confidence
    confidence_score: float    # Overall forecast confidence


class PredictionEngine:
    """AI/ML-powered price prediction and probability forecasting"""
    
    def __init__(self, simulations: int = 5000, forecast_days: int = 20):
        self.simulations = simulations
        self.forecast_days = forecast_days
    
    def monte_carlo_forecast(self, df: pd.DataFrame, current_price: float) -> PriceForecast:
        """
        Run Monte Carlo simulation for price forecasting.
        Uses geometric Brownian motion with historical volatility and drift.
        """
        returns = df['Close'].pct_change().dropna()
        
        # Calculate parameters
        mu = returns.mean()  # Daily drift
        sigma = returns.std()  # Daily volatility
        
        # Adjust for recent momentum (weight recent returns more)
        recent_returns = returns.tail(20)
        recent_mu = recent_returns.mean()
        
        # Blend historical and recent (60% recent, 40% historical)
        adjusted_mu = 0.6 * recent_mu + 0.4 * mu
        
        # Run simulations
        np.random.seed(42)  # Reproducibility
        simulated_prices = np.zeros((self.simulations, self.forecast_days))
        
        for i in range(self.simulations):
            daily_returns = np.random.normal(adjusted_mu, sigma, self.forecast_days)
            price_path = current_price * np.cumprod(1 + daily_returns)
            simulated_prices[i] = price_path
        
        # Get final prices
        final_prices = simulated_prices[:, -1]
        
        # Calculate percentiles
        percentiles = np.percentile(final_prices, [10, 25, 50, 75, 90])
        
        # Calculate probabilities
        prob_higher = np.mean(final_prices > current_price)
        prob_lower = 1 - prob_higher
        
        prob_up_5 = np.mean(final_prices > current_price * 1.05)
        prob_down_5 = np.mean(final_prices < current_price * 0.95)
        prob_up_10 = np.mean(final_prices > current_price * 1.10)
        prob_down_10 = np.mean(final_prices < current_price * 0.90)
        
        # Confidence based on simulation convergence and volatility
        price_std = np.std(final_prices) / current_price
        confidence = max(30, min(90, 100 - price_std * 200))
        
        return PriceForecast(
            current_price=current_price,
            forecast_days=self.forecast_days,
            target_bullish=round(percentiles[3], 2),
            target_bearish=round(percentiles[1], 2),
            target_median=round(percentiles[2], 2),
            target_extreme_bull=round(percentiles[4], 2),
            target_extreme_bear=round(percentiles[0], 2),
            prob_up_5pct=round(prob_up_5 * 100, 1),
            prob_down_5pct=round(prob_down_5 * 100, 1),
            prob_up_10pct=round(prob_up_10 * 100, 1),
            prob_down_10pct=round(prob_down_10 * 100, 1),
            prob_higher=round(prob_higher * 100, 1),
            prob_lower=round(prob_lower * 100, 1),
            confidence_score=round(confidence, 0)
        )
    
    def detect_support_resistance(self, df: pd.DataFrame, current_price: float) -> Dict[str, Any]:
        """Detect key support and resistance levels using price action"""
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        
        # Find local extrema (swing highs/lows)
        if HAS_SCIPY:
            # Use scipy for proper peak detection
            high_idx = argrelextrema(highs, np.greater, order=5)[0]
            low_idx = argrelextrema(lows, np.less, order=5)[0]
        else:
            # Simple alternative
            high_idx = []
            low_idx = []
            for i in range(5, len(highs) - 5):
                if highs[i] == max(highs[i-5:i+6]):
                    high_idx.append(i)
                if lows[i] == min(lows[i-5:i+6]):
                    low_idx.append(i)
            high_idx = np.array(high_idx)
            low_idx = np.array(low_idx)
        
        # Get resistance levels (above current price)
        resistance_levels = []
        if len(high_idx) > 0:
            for idx in high_idx:
                level = highs[idx]
                if level > current_price * 1.01:  # At least 1% above
                    resistance_levels.append(level)
        
        # Get support levels (below current price)
        support_levels = []
        if len(low_idx) > 0:
            for idx in low_idx:
                level = lows[idx]
                if level < current_price * 0.99:  # At least 1% below
                    support_levels.append(level)
        
        # Cluster similar levels and get strongest
        def cluster_levels(levels, tolerance=0.02):
            if not levels:
                return []
            levels = sorted(levels)
            clusters = []
            current_cluster = [levels[0]]
            for level in levels[1:]:
                if (level - current_cluster[0]) / current_cluster[0] < tolerance:
                    current_cluster.append(level)
                else:
                    clusters.append(np.mean(current_cluster))
                    current_cluster = [level]
            clusters.append(np.mean(current_cluster))
            return clusters
        
        resistance = sorted(cluster_levels(resistance_levels))[:3]  # Top 3
        support = sorted(cluster_levels(support_levels), reverse=True)[:3]  # Top 3
        
        # Calculate nearest levels
        nearest_resistance = min(resistance) if resistance else current_price * 1.10
        nearest_support = max(support) if support else current_price * 0.90
        
        # Distance to levels
        upside_to_resistance = ((nearest_resistance / current_price) - 1) * 100
        downside_to_support = (1 - (nearest_support / current_price)) * 100
        
        return {
            'resistance_levels': [round(r, 2) for r in resistance],
            'support_levels': [round(s, 2) for s in support],
            'nearest_resistance': round(nearest_resistance, 2),
            'nearest_support': round(nearest_support, 2),
            'upside_to_resistance': round(upside_to_resistance, 1),
            'downside_to_support': round(downside_to_support, 1),
            'risk_reward_ratio': round(upside_to_resistance / max(downside_to_support, 0.1), 2)
        }
    
    def analyze_market_phase(self, df: pd.DataFrame, indicators: Dict) -> Dict[str, Any]:
        """
        Wyckoff-style market phase analysis.
        Determines if stock is in: Accumulation, Markup, Distribution, or Markdown
        """
        close = df['Close']
        volume = df['Volume']
        
        # Price trend (last 20 days vs last 50 days)
        price_20d = close.tail(20).mean()
        price_50d = close.tail(50).mean()
        current = close.iloc[-1]
        
        # Volume trend
        vol_20d = volume.tail(20).mean()
        vol_50d = volume.tail(50).mean()
        
        # Price position
        high_52w = close.tail(252).max() if len(close) >= 252 else close.max()
        low_52w = close.tail(252).min() if len(close) >= 252 else close.min()
        price_position = (current - low_52w) / (high_52w - low_52w) if high_52w != low_52w else 0.5
        
        # Determine phase
        rsi = indicators.get('RSI', 50)
        adx = indicators.get('ADX', 20)
        
        if price_position < 0.3 and rsi < 40 and vol_20d > vol_50d:
            phase = "ACCUMULATION"
            phase_desc = "Smart money buying at lows"
            outlook = "Potential bottom forming - watch for reversal"
            color = "🟡"
        elif price_position > 0.5 and price_20d > price_50d and adx > 20:
            phase = "MARKUP"
            phase_desc = "Uptrend in progress"
            outlook = "Trend is your friend - look for pullback entries"
            color = "🟢"
        elif price_position > 0.7 and rsi > 60 and vol_20d > vol_50d:
            phase = "DISTRIBUTION"
            phase_desc = "Smart money selling at highs"
            outlook = "Potential top forming - be cautious with new longs"
            color = "🟠"
        elif price_position < 0.5 and price_20d < price_50d and adx > 20:
            phase = "MARKDOWN"
            phase_desc = "Downtrend in progress"
            outlook = "Avoid catching falling knives - wait for stabilization"
            color = "🔴"
        else:
            phase = "RANGING"
            phase_desc = "Sideways consolidation"
            outlook = "Wait for breakout direction before taking position"
            color = "⚪"
        
        return {
            'phase': phase,
            'phase_description': phase_desc,
            'outlook': outlook,
            'color': color,
            'price_position_52w': round(price_position * 100, 1),
            'trend_strength': 'Strong' if adx > 25 else 'Weak' if adx < 15 else 'Moderate'
        }
    
    def generate_human_advice(self, result: Dict[str, Any], forecast: PriceForecast, 
                             sr_levels: Dict, phase: Dict) -> str:
        """Generate human-like advisory text based on all analysis"""
        signal = result['signal']
        current = result['price']
        rsi = result['indicators'].get('RSI', 50)
        trend = result['trend']
        
        advice_parts = []
        
        # Opening assessment
        if 'STRONG BUY' in signal or 'BUY' in signal:
            advice_parts.append(f"🎯 **OPPORTUNITY DETECTED**: {result['ticker']} is showing bullish signals.")
        elif 'STRONG SELL' in signal or 'SELL' in signal:
            advice_parts.append(f"⚠️ **CAUTION**: {result['ticker']} is showing bearish signals.")
        elif 'WAIT' in signal:
            advice_parts.append(f"⏳ **PATIENCE ADVISED**: {result['ticker']} is not ready for entry yet.")
        else:
            advice_parts.append(f"📊 **MONITORING**: {result['ticker']} requires careful observation.")
        
        # Phase context
        advice_parts.append(f"\n{phase['color']} **Market Phase**: {phase['phase']} - {phase['phase_description']}")
        
        # Will it go lower?
        if rsi < 35:
            if forecast.prob_down_5pct > 30:
                advice_parts.append(f"\n📉 **Could it go LOWER?** Yes, there's still a {forecast.prob_down_5pct}% chance of another 5% drop to ~${current * 0.95:.2f}.")
                advice_parts.append(f"   However, with RSI at {rsi:.0f}, the stock is oversold. The selling may be exhausting.")
            else:
                advice_parts.append(f"\n📉 **Could it go LOWER?** Unlikely. Only {forecast.prob_down_5pct}% probability of 5%+ further decline.")
                advice_parts.append(f"   With RSI at {rsi:.0f}, most selling pressure may be done.")
        elif rsi > 65:
            if forecast.prob_up_5pct < 40:
                advice_parts.append(f"\n📈 **Could it go HIGHER?** Limited upside potential ({forecast.prob_up_5pct}% chance of +5%).")
                advice_parts.append(f"   RSI at {rsi:.0f} suggests overbought conditions. Wait for pullback.")
        
        # Bounce prediction
        if phase['phase'] == 'ACCUMULATION' or (rsi < 35 and forecast.prob_higher > 45):
            advice_parts.append(f"\n🔄 **BOUNCE LIKELY?** YES - {forecast.prob_higher}% probability of price being higher in {forecast.forecast_days} days.")
            advice_parts.append(f"   Monte Carlo median target: ${forecast.target_median:.2f}")
        elif phase['phase'] == 'MARKDOWN' and rsi > 35:
            advice_parts.append(f"\n🔄 **BOUNCE LIKELY?** NOT YET - Stock still in markdown phase. {phase['outlook']}")
        
        # Entry timing
        advice_parts.append(f"\n\n💡 **WHEN TO ENTER?**")
        if 'BUY' in signal or 'ACCUMULATE' in signal:
            if sr_levels['downside_to_support'] > 5:
                advice_parts.append(f"   • Aggressive: Enter now at ${current:.2f}")
                advice_parts.append(f"   • Conservative: Wait for pullback to ${sr_levels['nearest_support']:.2f} support")
            else:
                advice_parts.append(f"   • Price near support (${sr_levels['nearest_support']:.2f}) - Good entry zone")
        elif 'WAIT' in signal:
            if rsi < 30:
                advice_parts.append(f"   • Watch for RSI to turn up from oversold (currently {rsi:.0f})")
                advice_parts.append(f"   • Look for bullish candlestick pattern as confirmation")
            elif rsi > 70:
                advice_parts.append(f"   • Wait for RSI pullback from overbought (currently {rsi:.0f})")
                advice_parts.append(f"   • Target entry zone: ${sr_levels['nearest_support']:.2f}")
            else:
                advice_parts.append(f"   • Wait for clearer trend direction")
        else:
            advice_parts.append(f"   • Not recommended for new positions currently")
        
        # Risk/Reward assessment
        advice_parts.append(f"\n\n📊 **RISK/REWARD PROFILE**")
        advice_parts.append(f"   • Upside to resistance: +{sr_levels['upside_to_resistance']:.1f}% (${sr_levels['nearest_resistance']:.2f})")
        advice_parts.append(f"   • Downside to support: -{sr_levels['downside_to_support']:.1f}% (${sr_levels['nearest_support']:.2f})")
        advice_parts.append(f"   • Risk/Reward Ratio: 1:{sr_levels['risk_reward_ratio']:.1f}")
        
        # Final verdict
        advice_parts.append(f"\n\n🎯 **BOTTOM LINE**: {phase['outlook']}")
        
        return "\n".join(advice_parts)





# =============================================================================
# SIGNAL GENERATOR CLASS
# =============================================================================

class SignalGenerator:
    """Generate technical trading signals with confluence validation and AI/ML predictions"""
    
    def __init__(self):
        self.lookback_days = 200
        self.prediction_engine = PredictionEngine(simulations=5000, forecast_days=20)
        
    def analyze(self, ticker: str) -> Dict[str, Any]:
        """
        Analyze a stock and generate trading signals with AI/ML predictions.
        
        Args:
            ticker: Stock symbol (e.g., 'AAPL', 'RELIANCE.NS')
            
        Returns:
            Dictionary containing signal, indicators, patterns, predictions, and recommendations
        """
        # Fetch data
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        
        if df.empty or len(df) < 50:
            return {
                'ticker': ticker.upper(),
                'signal': 'NO_DATA',
                'confidence': 0,
                'error': f"Insufficient data for {ticker}",
                'indicators': {},
                'patterns': [],
                'trend': 'UNKNOWN',
                'entry': None,
                'stop_loss': None,
                'target': None,
                'risk_reward': 0
            }
        
        # Get stock info
        info = stock.info
        current_price = df['Close'].iloc[-1]
        company_name = info.get('shortName', ticker.upper())
        
        # Calculate indicators
        indicators = self._calculate_indicators(df)
        
        # Detect candlestick patterns
        patterns = self._detect_patterns(df)
        
        # Determine trend
        trend = self._analyze_trend(df, indicators)
        
        # Generate signal with confluence
        signal, confidence, reasons = self._generate_signal(indicators, patterns, trend)
        
        # Calculate entry, stop-loss, and targets
        entry, stop_loss, targets, risk_reward = self._calculate_levels(
            df, current_price, signal, indicators
        )
        
        # ═══════════════════════════════════════════════════════════════════════
        # AI/ML PREDICTIONS
        # ═══════════════════════════════════════════════════════════════════════
        
        # Monte Carlo price forecast
        forecast = self.prediction_engine.monte_carlo_forecast(df, current_price)
        
        # Support/Resistance detection
        sr_levels = self.prediction_engine.detect_support_resistance(df, current_price)
        
        # Market phase analysis (Wyckoff)
        phase = self.prediction_engine.analyze_market_phase(df, indicators)
        
        # Build result dict first (needed for human advice)
        result = {
            'ticker': ticker.upper(),
            'company': company_name,
            'price': current_price,
            'signal': signal,
            'confidence': confidence,
            'reasons': reasons,
            'indicators': indicators,
            'patterns': patterns,
            'trend': trend,
            'entry': entry,
            'stop_loss': stop_loss,
            'targets': targets,
            'risk_reward': risk_reward,
            # AI/ML predictions
            'forecast': forecast,
            'support_resistance': sr_levels,
            'market_phase': phase,
        }
        
        # Generate human-style advice
        human_advice = self.prediction_engine.generate_human_advice(result, forecast, sr_levels, phase)
        result['human_advice'] = human_advice
        
        return result
    
    def _calculate_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate technical indicators"""
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        indicators = {}
        
        if HAS_TA:
            # RSI
            rsi = RSIIndicator(close, window=14)
            indicators['RSI'] = rsi.rsi().iloc[-1]
            
            # MACD
            macd = MACD(close)
            macd_line = macd.macd().iloc[-1]
            signal_line = macd.macd_signal().iloc[-1]
            macd_hist = macd.macd_diff().iloc[-1]
            
            if macd_line > signal_line and macd_hist > 0:
                indicators['MACD'] = 'BULLISH'
            elif macd_line < signal_line and macd_hist < 0:
                indicators['MACD'] = 'BEARISH'
            else:
                indicators['MACD'] = 'NEUTRAL'
            
            indicators['MACD_histogram'] = macd_hist
            
            # ADX (Trend Strength)
            adx = ADXIndicator(high, low, close, window=14)
            indicators['ADX'] = adx.adx().iloc[-1]
            indicators['+DI'] = adx.adx_pos().iloc[-1]
            indicators['-DI'] = adx.adx_neg().iloc[-1]
            
            # Stochastic
            stoch = StochasticOscillator(high, low, close)
            indicators['Stoch_K'] = stoch.stoch().iloc[-1]
            indicators['Stoch_D'] = stoch.stoch_signal().iloc[-1]
            
            # Bollinger Bands
            bb = BollingerBands(close)
            indicators['BB_upper'] = bb.bollinger_hband().iloc[-1]
            indicators['BB_middle'] = bb.bollinger_mavg().iloc[-1]
            indicators['BB_lower'] = bb.bollinger_lband().iloc[-1]
            indicators['BB_width'] = (indicators['BB_upper'] - indicators['BB_lower']) / indicators['BB_middle'] * 100
            
            # Price position relative to BB
            current_price = close.iloc[-1]
            if current_price > indicators['BB_upper']:
                indicators['BB_position'] = 'ABOVE_UPPER'
            elif current_price < indicators['BB_lower']:
                indicators['BB_position'] = 'BELOW_LOWER'
            elif current_price > indicators['BB_middle']:
                indicators['BB_position'] = 'UPPER_HALF'
            else:
                indicators['BB_position'] = 'LOWER_HALF'
            
            # ATR (Volatility)
            atr = AverageTrueRange(high, low, close, window=14)
            indicators['ATR'] = atr.average_true_range().iloc[-1]
            indicators['ATR_pct'] = (indicators['ATR'] / current_price) * 100
            
            # Volatility classification
            if indicators['ATR_pct'] > 4:
                indicators['volatility_level'] = 'EXTREME'
            elif indicators['ATR_pct'] > 2.5:
                indicators['volatility_level'] = 'HIGH'
            elif indicators['ATR_pct'] > 1.5:
                indicators['volatility_level'] = 'NORMAL'
            else:
                indicators['volatility_level'] = 'LOW'
            
            # EMAs
            ema20 = EMAIndicator(close, window=20).ema_indicator().iloc[-1]
            ema50 = EMAIndicator(close, window=50).ema_indicator().iloc[-1]
            ema200 = EMAIndicator(close, window=200).ema_indicator().iloc[-1] if len(close) >= 200 else None
            
            indicators['EMA_20'] = ema20
            indicators['EMA_50'] = ema50
            indicators['EMA_200'] = ema200
            
        else:
            # Fallback calculations without ta library
            indicators['RSI'] = self._simple_rsi(close)
            indicators['MACD'] = 'N/A'
            indicators['ADX'] = 0
            indicators['ATR_pct'] = 0
            indicators['volatility_level'] = 'UNKNOWN'
        
        # Volume analysis
        avg_vol_20 = volume.rolling(20).mean().iloc[-1]
        current_vol = volume.iloc[-1]
        vol_ratio = current_vol / avg_vol_20 if avg_vol_20 > 0 else 1
        
        if vol_ratio > 2:
            indicators['Volume'] = f'VERY HIGH ({vol_ratio:.1f}x avg)'
        elif vol_ratio > 1.5:
            indicators['Volume'] = f'HIGH ({vol_ratio:.1f}x avg)'
        elif vol_ratio > 0.7:
            indicators['Volume'] = f'NORMAL ({vol_ratio:.1f}x avg)'
        else:
            indicators['Volume'] = f'LOW ({vol_ratio:.1f}x avg)'
        
        indicators['volume_ratio'] = vol_ratio
        
        return indicators
    
    def _simple_rsi(self, close: pd.Series, period: int = 14) -> float:
        """Calculate RSI without ta library"""
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    
    def _detect_patterns(self, df: pd.DataFrame) -> List[str]:
        """Detect candlestick patterns"""
        patterns = []
        
        if len(df) < 5:
            return patterns
        
        # Get recent candles
        o = df['Open'].values
        h = df['High'].values
        l = df['Low'].values
        c = df['Close'].values
        
        # Helper functions
        def body(i): return abs(c[i] - o[i])
        def upper_wick(i): return h[i] - max(o[i], c[i])
        def lower_wick(i): return min(o[i], c[i]) - l[i]
        def is_bullish(i): return c[i] > o[i]
        def is_bearish(i): return c[i] < o[i]
        def range_(i): return h[i] - l[i]
        
        i = -1  # Last candle
        
        # Doji (small body, roughly equal wicks)
        if range_(i) > 0 and body(i) / range_(i) < 0.1:
            patterns.append("⭐ Doji (Indecision)")
        
        # Hammer (small body at top, long lower wick)
        if range_(i) > 0:
            if body(i) / range_(i) < 0.3 and lower_wick(i) > 2 * body(i) and upper_wick(i) < body(i):
                patterns.append("🔨 Hammer (Bullish Reversal)")
        
        # Inverted Hammer
        if range_(i) > 0:
            if body(i) / range_(i) < 0.3 and upper_wick(i) > 2 * body(i) and lower_wick(i) < body(i):
                patterns.append("🔨 Inverted Hammer")
        
        # Engulfing patterns (need 2 candles)
        if len(df) >= 2:
            i_prev = -2
            # Bullish Engulfing
            if is_bearish(i_prev) and is_bullish(i) and o[i] < c[i_prev] and c[i] > o[i_prev]:
                patterns.append("🟢 Bullish Engulfing (Strong Buy)")
            # Bearish Engulfing
            if is_bullish(i_prev) and is_bearish(i) and o[i] > c[i_prev] and c[i] < o[i_prev]:
                patterns.append("🔴 Bearish Engulfing (Strong Sell)")
        
        # Morning Star / Evening Star (need 3 candles)
        if len(df) >= 3:
            i1, i2, i3 = -3, -2, -1
            # Morning Star
            if (is_bearish(i1) and body(i1) > body(i2) and 
                body(i2) < body(i1) * 0.3 and is_bullish(i3) and c[i3] > (o[i1] + c[i1]) / 2):
                patterns.append("⭐ Morning Star (Bullish Reversal)")
            # Evening Star
            if (is_bullish(i1) and body(i1) > body(i2) and 
                body(i2) < body(i1) * 0.3 and is_bearish(i3) and c[i3] < (o[i1] + c[i1]) / 2):
                patterns.append("⭐ Evening Star (Bearish Reversal)")
        
        # Three White Soldiers / Three Black Crows
        if len(df) >= 3:
            if all(is_bullish(-j) for j in range(1, 4)) and c[-1] > c[-2] > c[-3]:
                patterns.append("🟢 Three White Soldiers (Strong Bullish)")
            if all(is_bearish(-j) for j in range(1, 4)) and c[-1] < c[-2] < c[-3]:
                patterns.append("🔴 Three Black Crows (Strong Bearish)")
        
        return patterns
    
    def _analyze_trend(self, df: pd.DataFrame, indicators: Dict) -> str:
        """Analyze overall trend direction"""
        close = df['Close']
        current_price = close.iloc[-1]
        
        # Check EMAs
        ema20 = indicators.get('EMA_20', current_price)
        ema50 = indicators.get('EMA_50', current_price)
        ema200 = indicators.get('EMA_200')
        
        # Price vs EMAs
        above_20 = current_price > ema20
        above_50 = current_price > ema50
        above_200 = current_price > ema200 if ema200 else None
        
        # EMA alignment
        ema_bullish = ema20 > ema50
        ema_strongly_bullish = ema_bullish and (ema50 > ema200 if ema200 else True)
        
        # ADX for trend strength
        adx = indicators.get('ADX', 0)
        strong_trend = adx > 25
        
        # Determine trend
        if ema_strongly_bullish and above_20 and above_50 and strong_trend:
            return "STRONG UPTREND 📈"
        elif ema_bullish and above_20:
            return "UPTREND 📈"
        elif not ema_bullish and not above_20 and not above_50 and strong_trend:
            return "STRONG DOWNTREND 📉"
        elif not ema_bullish and not above_50:
            return "DOWNTREND 📉"
        else:
            return "SIDEWAYS ↔️"
    
    def _generate_signal(self, indicators: Dict, patterns: List[str], trend: str) -> Tuple[str, int, List[str]]:
        """Generate trading signal based on confluence of factors with context awareness"""
        bullish_score = 0
        bearish_score = 0
        reasons = []
        warnings = []
        
        # Get key indicators for context
        rsi = indicators.get('RSI', 50)
        bb_pos = indicators.get('BB_position', '')
        stoch_k = indicators.get('Stoch_K', 50)
        
        # Determine if already extended (oversold/overbought)
        is_oversold = rsi < 35 or bb_pos == 'BELOW_LOWER' or stoch_k < 20
        is_overbought = rsi > 65 or bb_pos == 'ABOVE_UPPER' or stoch_k > 80
        
        # RSI Analysis
        if rsi < 30:
            bullish_score += 2
            reasons.append(f"RSI deeply oversold ({rsi:.1f}) - reversal likely")
        elif rsi < 40:
            bullish_score += 1
            reasons.append(f"RSI approaching oversold ({rsi:.1f})")
        elif rsi > 70:
            bearish_score += 2
            reasons.append(f"RSI overbought ({rsi:.1f}) - pullback likely")
        elif rsi > 60:
            bearish_score += 1
            reasons.append(f"RSI approaching overbought ({rsi:.1f})")
        
        # MACD Analysis - but context matters!
        macd = indicators.get('MACD', 'NEUTRAL')
        if macd == 'BULLISH':
            bullish_score += 2
            reasons.append("MACD bullish crossover")
        elif macd == 'BEARISH':
            # If already oversold, MACD bearish is less meaningful
            if is_oversold:
                bearish_score += 1  # Reduced weight
                reasons.append("MACD bearish (but oversold - may lag)")
            else:
                bearish_score += 2
                reasons.append("MACD bearish crossover")
        
        # Trend Analysis - but detect extended moves
        if 'STRONG UPTREND' in trend:
            if is_overbought:
                reasons.append("Strong uptrend BUT overbought - wait for pullback")
                warnings.append("Extended upside move")
            else:
                bullish_score += 2
                reasons.append("Strong uptrend confirmed")
        elif 'UPTREND' in trend:
            bullish_score += 1
            reasons.append("Uptrend in place")
        elif 'STRONG DOWNTREND' in trend:
            if is_oversold:
                reasons.append("Strong downtrend BUT oversold - bounce likely")
                warnings.append("Extended downside move - don't chase")
            else:
                bearish_score += 2
                reasons.append("Strong downtrend - caution")
        elif 'DOWNTREND' in trend:
            if is_oversold:
                reasons.append("Downtrend BUT oversold - watch for reversal")
            else:
                bearish_score += 1
                reasons.append("Downtrend - wait for reversal")
        
        # ADX (Trend Strength)
        adx = indicators.get('ADX', 0)
        if adx > 25:
            reasons.append(f"Strong trend (ADX: {adx:.0f})")
        
        # Stochastic
        stoch_d = indicators.get('Stoch_D', 50)
        if stoch_k < 20 and stoch_k > stoch_d:
            bullish_score += 1
            reasons.append("Stochastic bullish from oversold")
        elif stoch_k > 80 and stoch_k < stoch_d:
            bearish_score += 1
            reasons.append("Stochastic bearish from overbought")
        
        # Bollinger Bands position
        if bb_pos == 'BELOW_LOWER':
            bullish_score += 2  # Strong mean reversion signal
            reasons.append("Price below lower BB - mean reversion setup")
        elif bb_pos == 'ABOVE_UPPER':
            bearish_score += 2
            reasons.append("Price above upper BB - mean reversion likely")
        
        # Volume confirmation
        vol_ratio = indicators.get('volume_ratio', 1)
        if vol_ratio > 2.0:
            if is_oversold:
                bullish_score += 1  # Capitulation volume = bullish
                reasons.append(f"Capitulation volume ({vol_ratio:.1f}x) - selling exhaustion")
            else:
                reasons.append(f"High volume confirmation ({vol_ratio:.1f}x)")
        elif vol_ratio > 1.5:
            reasons.append(f"Above average volume ({vol_ratio:.1f}x)")
        
        # Pattern analysis WITH CONTEXT VALIDATION
        for pattern in patterns:
            pattern_name = pattern.split('(')[0].strip()
            
            # Bullish patterns
            if 'Bullish' in pattern or 'Buy' in pattern or 'White Soldiers' in pattern:
                if 'Morning Star' in pattern or 'Hammer' in pattern:
                    # These are bottom reversal patterns - valid if oversold
                    if is_oversold:
                        bullish_score += 3  # Extra weight at bottoms
                        reasons.append(f"{pattern_name} (confirmed at oversold)")
                    else:
                        bullish_score += 1
                        reasons.append(f"{pattern_name} (needs confirmation)")
                else:
                    bullish_score += 2
                    reasons.append(pattern_name)
                    
            # Bearish patterns
            elif 'Bearish' in pattern or 'Sell' in pattern or 'Black Crows' in pattern:
                if 'Evening Star' in pattern:
                    # Evening Star is a TOP reversal - NOT valid at bottoms!
                    if is_overbought:
                        bearish_score += 3
                        reasons.append(f"{pattern_name} (confirmed at overbought)")
                    elif is_oversold:
                        # INVALID at oversold levels
                        warnings.append(f"{pattern_name} invalid - already oversold")
                    else:
                        bearish_score += 1
                        reasons.append(f"{pattern_name} (needs confirmation)")
                elif 'Three Black Crows' in pattern:
                    if is_oversold:
                        # Three Black Crows at oversold = selling exhaustion
                        bullish_score += 1
                        reasons.append("Three Black Crows at oversold - capitulation")
                    else:
                        bearish_score += 2
                        reasons.append(pattern_name)
                else:
                    if not is_oversold:
                        bearish_score += 2
                        reasons.append(pattern_name)
        
        # Calculate net score
        net_score = bullish_score - bearish_score
        
        # ═══════════════════════════════════════════════════════════════════════
        # CRITICAL: Override signals based on extended move detection
        # ═══════════════════════════════════════════════════════════════════════
        
        # If stock is already oversold, DO NOT give SELL signal
        if is_oversold and net_score < 0:
            # Check how oversold
            oversold_severity = 0
            if rsi < 30: oversold_severity += 2
            elif rsi < 35: oversold_severity += 1
            if bb_pos == 'BELOW_LOWER': oversold_severity += 2
            if stoch_k < 20: oversold_severity += 1
            
            if oversold_severity >= 3:
                # Very oversold - override to WAIT
                signal = "WAIT ⏸️"
                confidence = 70
                reasons.insert(0, "⚠️ OVERRIDE: Stock extremely oversold - don't sell here!")
                reasons.append("Wait for bounce or reversal confirmation before action")
                return signal, confidence, reasons
            elif oversold_severity >= 2:
                # Moderately oversold - reduce to HOLD
                signal = "HOLD ⏸️"
                confidence = 60
                reasons.insert(0, "⚠️ Stock oversold - selling here risks catching the bottom")
                return signal, confidence, reasons
        
        # If stock is already overbought, DO NOT give BUY signal
        if is_overbought and net_score > 0:
            overbought_severity = 0
            if rsi > 70: overbought_severity += 2
            elif rsi > 65: overbought_severity += 1
            if bb_pos == 'ABOVE_UPPER': overbought_severity += 2
            if stoch_k > 80: overbought_severity += 1
            
            if overbought_severity >= 3:
                signal = "WAIT ⏸️"
                confidence = 70
                reasons.insert(0, "⚠️ OVERRIDE: Stock extremely overbought - don't buy here!")
                reasons.append("Wait for pullback before entering")
                return signal, confidence, reasons
            elif overbought_severity >= 2:
                signal = "HOLD ⏸️"
                confidence = 60
                reasons.insert(0, "⚠️ Stock overbought - buying here risks catching the top")
                return signal, confidence, reasons
        
        # Standard signal determination
        if net_score >= 5:
            signal = "STRONG BUY 🟢"
            confidence = min(95, 70 + net_score * 3)
        elif net_score >= 3:
            signal = "BUY 🟢"
            confidence = min(85, 60 + net_score * 3)
        elif net_score >= 1:
            signal = "ACCUMULATE 📈"
            confidence = min(70, 50 + net_score * 5)
        elif net_score <= -5:
            signal = "STRONG SELL 🔴"
            confidence = min(95, 70 + abs(net_score) * 3)
        elif net_score <= -3:
            signal = "SELL 🔴"
            confidence = min(85, 60 + abs(net_score) * 3)
        elif net_score <= -1:
            signal = "REDUCE 📉"
            confidence = min(70, 50 + abs(net_score) * 5)
        else:
            signal = "HOLD ⏸️"
            confidence = 50
        
        return signal, confidence, reasons
    
    def _calculate_levels(self, df: pd.DataFrame, current_price: float, 
                         signal: str, indicators: Dict) -> Tuple[float, float, List[float], float]:
        """Calculate entry, stop-loss, and target levels"""
        atr = indicators.get('ATR', current_price * 0.02)
        
        # Entry (current price for buy signals)
        entry = current_price
        
        # Stop-loss (1.5-2x ATR below entry for buys)
        if 'BUY' in signal or 'ACCUMULATE' in signal:
            stop_loss = round(entry - (atr * 2), 2)
            # Targets based on R:R ratios
            risk = entry - stop_loss
            targets = [
                round(entry + risk * 1.5, 2),  # Target 1: 1.5R
                round(entry + risk * 2.5, 2),  # Target 2: 2.5R
                round(entry + risk * 4, 2),    # Target 3: 4R
            ]
            risk_reward = 2.5  # Average target
        elif 'SELL' in signal or 'REDUCE' in signal:
            stop_loss = round(entry + (atr * 2), 2)
            risk = stop_loss - entry
            targets = [
                round(entry - risk * 1.5, 2),
                round(entry - risk * 2.5, 2),
                round(entry - risk * 4, 2),
            ]
            risk_reward = 2.5
        else:
            stop_loss = None
            targets = []
            risk_reward = 0
        
        return entry, stop_loss, targets, risk_reward


# =============================================================================
# DISPLAY FUNCTION
# =============================================================================

def display_signal(result: Dict[str, Any]) -> None:
    """Display the analysis results with AI/ML predictions in a formatted output"""
    
    if result.get('error'):
        print(f"\n❌ {result['error']}")
        return
    
    print("\n" + "="*80)
    print(f"📊 ANALYSIS: {result['company']} ({result['ticker']})")
    print("="*80)
    
    # Price and Signal
    print(f"\n💰 Current Price: ${result['price']:.2f}")
    print(f"🎯 Signal: {result['signal']} (Confidence: {result['confidence']}%)")
    
    # Market Phase
    if result.get('market_phase'):
        phase = result['market_phase']
        print(f"\n{phase['color']} Market Phase: {phase['phase']} ({phase['trend_strength']} trend)")
        print(f"   52-Week Position: {phase['price_position_52w']:.0f}%")
    
    # Trend
    print(f"📈 Trend: {result['trend']}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # AI/ML PRICE FORECAST
    # ═══════════════════════════════════════════════════════════════════════
    if result.get('forecast'):
        forecast = result['forecast']
        print("\n" + "-"*80)
        print("🤖 AI/ML PRICE FORECAST (Monte Carlo Simulation)")
        print("-"*80)
        print(f"   Forecast Horizon: {forecast.forecast_days} trading days")
        print(f"\n   📊 PROBABILITY OF DIRECTION:")
        print(f"      • Price HIGHER: {forecast.prob_higher}%")
        print(f"      • Price LOWER:  {forecast.prob_lower}%")
        print(f"\n   📈 PROBABILITY OF GAINS:")
        print(f"      • +5% or more:  {forecast.prob_up_5pct}%")
        print(f"      • +10% or more: {forecast.prob_up_10pct}%")
        print(f"\n   📉 PROBABILITY OF LOSSES:")
        print(f"      • -5% or more:  {forecast.prob_down_5pct}%")
        print(f"      • -10% or more: {forecast.prob_down_10pct}%")
        print(f"\n   🎯 PRICE TARGETS ({forecast.forecast_days} days):")
        print(f"      • Bull Case (75th %ile):  ${forecast.target_bullish:.2f}  (+{((forecast.target_bullish/forecast.current_price)-1)*100:.1f}%)")
        print(f"      • Base Case (Median):     ${forecast.target_median:.2f}  ({((forecast.target_median/forecast.current_price)-1)*100:+.1f}%)")
        print(f"      • Bear Case (25th %ile):  ${forecast.target_bearish:.2f}  ({((forecast.target_bearish/forecast.current_price)-1)*100:.1f}%)")
        print(f"\n   Forecast Confidence: {forecast.confidence_score:.0f}%")
    
    # ═══════════════════════════════════════════════════════════════════════
    # SUPPORT/RESISTANCE LEVELS
    # ═══════════════════════════════════════════════════════════════════════
    if result.get('support_resistance'):
        sr = result['support_resistance']
        print("\n" + "-"*80)
        print("🎯 SUPPORT & RESISTANCE LEVELS")
        print("-"*80)
        print(f"   Nearest Resistance: ${sr['nearest_resistance']:.2f} (+{sr['upside_to_resistance']:.1f}%)")
        print(f"   Nearest Support:    ${sr['nearest_support']:.2f} (-{sr['downside_to_support']:.1f}%)")
        print(f"   Risk/Reward Ratio:  1:{sr['risk_reward_ratio']:.1f}")
        
        if sr['resistance_levels']:
            print(f"\n   Resistance Levels: {', '.join([f'${r:.2f}' for r in sr['resistance_levels'][:3]])}")
        if sr['support_levels']:
            print(f"   Support Levels:    {', '.join([f'${s:.2f}' for s in sr['support_levels'][:3]])}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # HUMAN-STYLE ADVISORY
    # ═══════════════════════════════════════════════════════════════════════
    if result.get('human_advice'):
        print("\n" + "="*80)
        print("💡 AI ADVISOR INSIGHTS")
        print("="*80)
        # Convert markdown-style **text** to plain text for terminal
        advice = result['human_advice'].replace('**', '')
        print(advice)
    
    # Entry Levels (if applicable)
    if result.get('entry') and result.get('stop_loss'):
        print("\n" + "-"*80)
        print("📍 TRADE SETUP")
        print("-"*80)
        print(f"Entry:     ${result['entry']:.2f}")
        print(f"Stop-Loss: ${result['stop_loss']:.2f}")
        if result.get('targets'):
            print(f"Target 1:  ${result['targets'][0]:.2f} (1.5R)")
            print(f"Target 2:  ${result['targets'][1]:.2f} (2.5R)")
            print(f"Target 3:  ${result['targets'][2]:.2f} (4R)")
        print(f"Risk/Reward: 1:{result.get('risk_reward', 0):.1f}")
    
    # Reasons/Confluence
    if result.get('reasons'):
        print("\n" + "-"*80)
        print("💡 SIGNAL REASONS (Confluence)")
        print("-"*80)
        for i, reason in enumerate(result['reasons'][:8], 1):  # Limit to 8 reasons
            print(f"  {i}. {reason}")
    
    # Technical Summary
    print("\n" + "-"*80)
    print("🔧 TECHNICAL INDICATORS")
    print("-"*80)
    ind = result.get('indicators', {})
    print(f"RSI:    {ind.get('RSI', 'N/A'):.1f}" if isinstance(ind.get('RSI'), (int, float)) else f"RSI:    {ind.get('RSI', 'N/A')}")
    print(f"MACD:   {ind.get('MACD', 'N/A')}")
    print(f"ADX:    {ind.get('ADX', 0):.1f} (Trend Strength)")
    print(f"ATR:    {ind.get('ATR_pct', 0):.2f}% (Volatility: {ind.get('volatility_level', 'NORMAL')})")
    print(f"Volume: {ind.get('Volume', 'N/A')}")
    print(f"Trend:  {result.get('trend', 'N/A')}")
    
    if result.get('patterns'):
        print(f"\n📊 CANDLESTICK PATTERNS:")
        for pattern in result['patterns']:
            print(f"   {pattern}")
    
    print("\n" + "="*80)
    print("⚠️  DISCLAIMER: This is for educational purposes only. Trade at your own risk.")
    print("    AI predictions are based on historical data and probabilities, not guarantees.")
    print("="*80 + "\n")


# =============================================================================
# MAIN PROGRAM
# =============================================================================

def main():
    """Main program loop"""
    print("\n" + "="*80)
    print("📊 TECHNICAL SIGNAL GENERATOR with AI/ML PREDICTION ENGINE")
    print("   High-Probability Trading Signals + Monte Carlo Forecasting")
    print("="*80)
    print("\n🤖 Features:")
    print("   • Technical Analysis (RSI, MACD, ADX, Bollinger Bands)")
    print("   • AI Price Predictions (Monte Carlo Simulation)")
    print("   • Support/Resistance Detection")
    print("   • Market Phase Analysis (Wyckoff)")
    print("   • Human-Style Advisory Output")
    print("\nSupports: US stocks, Indian stocks (.NS/.BO), International markets")
    print("Example tickers: AAPL, TSLA, MSFT, NVDA, RELIANCE.NS, etc.")
    
    generator = SignalGenerator()
    
    while True:
        try:
            ticker = input("\n📈 Enter ticker symbol (or 'quit' to exit): ").strip().upper()
            
            if ticker.lower() in ['quit', 'exit', 'q', '']:
                print("\n👋 Thank you for using Technical Signal Generator!")
                break
            
            print(f"\n🔄 Analyzing {ticker}... (Running AI predictions)")
            
            # Analyze
            result = generator.analyze(ticker)
            
            # Display
            display_signal(result)
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

