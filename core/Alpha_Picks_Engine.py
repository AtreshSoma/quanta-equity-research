"""
Alpha Picks Replication Engine
===============================
A quantitative stock screening system that generates high-conviction stock recommendations
based on analyst ratings, fundamentals, sentiment, and technical indicators.

Author: Financial Data Engineering Team
Version: 1.0
Last Updated: November 2025

Dependencies:
- pandas, numpy, requests, yfinance, ta-lib, schedule
- APIs: Alpha Vantage, Finnhub, Financial Modeling Prep
"""

import pandas as pd
import numpy as np
import requests
import yfinance as yf
from datetime import datetime, timedelta
import json
import logging
from typing import Dict, List, Tuple, Optional
import time
from dataclasses import dataclass, asdict
import os
import sys
import warnings

# Scope warning suppression to noisy third-party libraries only, instead of
# silencing *all* warnings globally (which hides real pandas/yfinance issues).
warnings.filterwarnings('ignore', category=FutureWarning, module='yfinance')
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Shared Zacks-style EPS-estimate-revision factor (one of the strongest
# cross-sectional alpha signals). Imported from the modules/ package; degrades
# gracefully to a neutral signal if unavailable.
_MODULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules")
if _MODULES_DIR not in sys.path:
    sys.path.insert(0, _MODULES_DIR)
try:
    from earnings_revision_scoring import compute_earnings_revision_signal  # type: ignore
    HAS_EARNINGS_REVISIONS = True
except Exception:  # pragma: no cover - optional dependency
    compute_earnings_revision_signal = None  # type: ignore
    HAS_EARNINGS_REVISIONS = False


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('alpha_picks.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def safe_float(value, default=0.0):
    """Safely convert a value to float, handling 'None' strings and null values"""
    if value in [None, 'None', '', 'null', 'NULL']:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


@dataclass
class StockRecommendation:
    """Data class for stock recommendations"""
    ticker: str
    company_name: str
    current_price: float
    entry_price: float
    target_price: float
    stop_loss: float
    score: float
    analyst_score: float
    quant_score: float
    sentiment_score: float
    technical_score: float
    timeframe: str
    thesis: str
    risk_reward_ratio: float
    market_cap: float
    sector: str
    recommendation_date: str

    def to_dict(self):
        return asdict(self)


class APIManager:
    """Manages API calls with rate limiting and fallback logic"""

    def __init__(self, config: Dict[str, str]):
        self.alpha_vantage_key = config.get('alpha_vantage_api_key', 'YOUR_AV_KEY')
        self.finnhub_key = config.get('finnhub_api_key', 'YOUR_FINNHUB_KEY')
        self.fmp_key = config.get('fmp_api_key', 'YOUR_FMP_KEY')

        # Rate limiting counters (free tier limits)
        self.av_calls = 0
        self.av_limit = 5  # 5 calls per minute for free tier
        self.finnhub_calls = 0
        self.finnhub_limit = 60  # 60 calls per minute
        self.fmp_calls = 0
        self.fmp_limit = 250  # 250 calls per day

        self.last_reset = datetime.now()

    def _check_rate_limit(self, api: str):
        """Check and enforce rate limits"""
        now = datetime.now()
        if (now - self.last_reset).seconds >= 60:
            self.av_calls = 0
            self.finnhub_calls = 0
            self.last_reset = now

        if api == 'av' and self.av_calls >= self.av_limit:
            logger.warning("Alpha Vantage rate limit reached, waiting...")
            time.sleep(12)  # Wait 12 seconds
            self.av_calls = 0
        elif api == 'finnhub' and self.finnhub_calls >= self.finnhub_limit:
            logger.warning("Finnhub rate limit reached, waiting...")
            time.sleep(1)
        elif api == 'fmp' and self.fmp_calls >= self.fmp_limit:
            logger.error("FMP daily limit reached")
            return False
        return True

    def get_with_retry(self, url: str, params: Dict = None, max_retries: int = 3) -> Optional[Dict]:
        """GET request with retry logic"""
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 403:
                    logger.debug(f"API 403 for {url}: Likely invalid API key or access denied")
                    return None  # Don't retry on 403, it won't change
                elif response.status_code == 429:
                    logger.warning(f"Rate limit hit, attempt {attempt + 1}/{max_retries}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"API error: {response.status_code}")
                    return None
            except Exception as e:
                logger.error(f"Request failed: {e}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        return None

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        """Fetch fundamental data from Alpha Vantage with yfinance fallback"""
        if not self._check_rate_limit('av'):
            return self._get_fundamentals_from_yfinance(ticker)

        url = "https://www.alphavantage.co/query"
        params = {
            'function': 'OVERVIEW',
            'symbol': ticker,
            'apikey': self.alpha_vantage_key
        }

        self.av_calls += 1
        data = self.get_with_retry(url, params)

        if data and 'Symbol' in data:
            return {
                'pe_ratio': safe_float(data.get('PERatio'), 0),
                'eps': safe_float(data.get('EPS'), 0),
                'roe': safe_float(data.get('ReturnOnEquityTTM'), 0),
                'debt_to_equity': safe_float(data.get('DebtToEquity'), 0),
                'market_cap': safe_float(data.get('MarketCapitalization'), 0),
                'sector': data.get('Sector', 'Unknown'),
                'industry': data.get('Industry', 'Unknown'),
                'beta': safe_float(data.get('Beta'), 1),
                'dividend_yield': safe_float(data.get('DividendYield'), 0),
                '52_week_high': safe_float(data.get('52WeekHigh'), 0),
                '52_week_low': safe_float(data.get('52WeekLow'), 0)
            }

        # Fallback to yfinance if Alpha Vantage fails
        logger.debug(f"Alpha Vantage failed for {ticker}, using yfinance fallback")
        return self._get_fundamentals_from_yfinance(ticker)

    def _get_fundamentals_from_yfinance(self, ticker: str) -> Optional[Dict]:
        """Fallback: Get fundamentals from yfinance"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            return {
                'pe_ratio': safe_float(info.get('forwardPE', info.get('trailingPE', 0)), 0),
                'eps': safe_float(info.get('trailingEps', 0), 0),
                'roe': safe_float(info.get('returnOnEquity', 0), 0),
                'debt_to_equity': safe_float(info.get('debtToEquity', 0), 0),
                'market_cap': safe_float(info.get('marketCap', 0), 0),
                'sector': info.get('sector', 'Unknown'),
                'industry': info.get('industry', 'Unknown'),
                'beta': safe_float(info.get('beta', 1), 1),
                'dividend_yield': safe_float(info.get('dividendYield', 0), 0),
                '52_week_high': safe_float(info.get('fiftyTwoWeekHigh', 0), 0),
                '52_week_low': safe_float(info.get('fiftyTwoWeekLow', 0), 0)
            }
        except Exception as e:
            logger.debug(f"yfinance fundamentals failed for {ticker}: {e}")
            return None

    def get_analyst_data(self, ticker: str) -> Optional[Dict]:
        """Fetch analyst ratings from Financial Modeling Prep"""
        if not self._check_rate_limit('fmp'):
            return None

        # Get analyst recommendations
        url = f"https://financialmodelingprep.com/api/v3/analyst-stock-recommendations/{ticker}"
        params = {'apikey': self.fmp_key}

        self.fmp_calls += 1
        data = self.get_with_retry(url, params)

        if data and len(data) > 0:
            recent = data[0]

            # Get price target
            target_url = f"https://financialmodelingprep.com/api/v4/price-target-consensus"
            target_params = {'symbol': ticker, 'apikey': self.fmp_key}
            target_data = self.get_with_retry(target_url, target_params)

            avg_target = 0
            if target_data and len(target_data) > 0:
                avg_target = float(target_data[0].get('targetConsensus', 0) or 0)

            return {
                'consensus_rating': recent.get('analystRatingsrecommend', 'hold'),
                'strong_buy': recent.get('analystRatingsstrongBuy', 0),
                'buy': recent.get('analystRatingsBuy', 0),
                'hold': recent.get('analystRatingsHold', 0),
                'sell': recent.get('analystRatingsSell', 0),
                'strong_sell': recent.get('analystRatingsStrongSell', 0),
                'target_price': avg_target
            }
        return None

    def get_sentiment_data(self, ticker: str) -> Optional[Dict]:
        """Fetch sentiment and recommendation trends from Finnhub"""
        if not self._check_rate_limit('finnhub'):
            return None

        # Get recommendation trends
        url = f"https://finnhub.io/api/v1/stock/recommendation"
        params = {'symbol': ticker, 'token': self.finnhub_key}

        self.finnhub_calls += 1
        rec_data = self.get_with_retry(url, params)

        # Get news sentiment
        news_url = f"https://finnhub.io/api/v1/news-sentiment"
        news_params = {'symbol': ticker, 'token': self.finnhub_key}
        news_data = self.get_with_retry(news_url, news_params)

        sentiment_score = 0.5
        if news_data and 'sentiment' in news_data:
            sentiment_score = float(news_data['sentiment'].get('bullishPercent', 0.5))

        rec_score = 3.0
        if rec_data and len(rec_data) > 0:
            latest = rec_data[0]
            total = latest.get('strongBuy', 0) + latest.get('buy', 0) + latest.get('hold', 0) + \
                    latest.get('sell', 0) + latest.get('strongSell', 0)
            if total > 0:
                weighted = (latest.get('strongBuy', 0) * 5 + latest.get('buy', 0) * 4 +
                           latest.get('hold', 0) * 3 + latest.get('sell', 0) * 2 +
                           latest.get('strongSell', 0) * 1)
                rec_score = weighted / total

        return {
            'sentiment_score': sentiment_score,
            'recommendation_trend': rec_score,
            'news_buzz': news_data.get('buzz', {}).get('articlesInLastWeek', 0) if news_data else 0
        }


class TechnicalAnalyzer:
    """Performs technical analysis on stock data"""

    @staticmethod
    def get_technical_indicators(ticker: str, period: str = '6mo') -> Optional[Dict]:
        """Calculate technical indicators using yfinance"""
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)

            if hist.empty:
                return None

            # Calculate moving averages
            hist['SMA_50'] = hist['Close'].rolling(window=50).mean()
            hist['SMA_200'] = hist['Close'].rolling(window=200).mean()

            # Calculate RSI
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            hist['RSI'] = 100 - (100 / (1 + rs))

            # Volume analysis
            hist['Volume_MA'] = hist['Volume'].rolling(window=20).mean()

            latest = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else latest

            # Support and resistance (simplified)
            recent_data = hist.tail(60)
            support = recent_data['Low'].min()
            resistance = recent_data['High'].max()

            # Trend detection
            sma_crossover = latest['SMA_50'] > latest['SMA_200'] if not pd.isna(latest['SMA_50']) else False
            volume_surge = latest['Volume'] > latest['Volume_MA'] * 1.5 if not pd.isna(latest['Volume_MA']) else False

            return {
                'current_price': float(latest['Close']),
                'sma_50': float(latest['SMA_50']) if not pd.isna(latest['SMA_50']) else 0,
                'sma_200': float(latest['SMA_200']) if not pd.isna(latest['SMA_200']) else 0,
                'rsi': float(latest['RSI']) if not pd.isna(latest['RSI']) else 50,
                'volume': float(latest['Volume']),
                'avg_volume': float(latest['Volume_MA']) if not pd.isna(latest['Volume_MA']) else 0,
                'support': float(support),
                'resistance': float(resistance),
                'sma_crossover': sma_crossover,
                'volume_surge': volume_surge,
                'price_change_pct': ((latest['Close'] - prev['Close']) / prev['Close']) * 100
            }

        except Exception as e:
            logger.error(f"Technical analysis failed for {ticker}: {e}")
            return None

    @staticmethod
    def calculate_volatility(ticker: str, period: str = '6mo') -> float:
        """Calculate historical volatility"""
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)
            returns = hist['Close'].pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100  # Annualized
            return float(volatility)
        except Exception as e:
            logger.debug(f"{ticker}: volatility calculation failed - {e}")
            return 0


class AlphaPicksEngine:
    """Main engine for generating stock recommendations"""

    def __init__(self, config: Dict[str, str]):
        self.api_manager = APIManager(config)
        self.config = config
        self.results = []

    def get_screener_universe(self) -> List[str]:
        """
        Get initial universe of stocks to screen
        For production: Use FMP screener or maintain a custom list
        For demo: Using a curated list of liquid large-caps
        """
        # Semiconductor & Tech Focus (matching user context with MU holdings)
        semiconductor_stocks = [
            'NVDA', 'AMD', 'MU', 'AVGO', 'QCOM', 'TXN', 'INTC', 'AMAT', 'LRCX',
            'KLAC', 'MCHP', 'ADI', 'NXPI', 'ON', 'ASML', 'TSM'
        ]

        # Major Tech (AI, Cloud, Growth)
        tech_stocks = [
            'AAPL', 'MSFT', 'GOOGL', 'META', 'AMZN', 'TSLA', 'CRM', 'ORCL',
            'ADBE', 'NOW', 'SNOW', 'PLTR', 'NET', 'DDOG', 'CRWD', 'PANW'
        ]

        # Growth Leaders
        growth_stocks = [
            'SHOP', 'SQ', 'COIN', 'UBER', 'ABNB', 'RBLX', 'ROKU', 'ZS'
        ]

        universe = semiconductor_stocks + tech_stocks + growth_stocks

        # Filter by market cap > $2B using yfinance (institutional liquidity floor)
        logger.info(f"Screening {len(universe)} stocks...")
        filtered = []

        for ticker in universe:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                market_cap = info.get('marketCap', 0)

                if market_cap > 2_000_000_000:  # > $2B

                    filtered.append(ticker)

            except Exception as e:
                logger.warning(f"Failed to screen {ticker}: {e}")
                continue

        logger.info(f"Filtered to {len(filtered)} stocks meeting criteria")
        return filtered

    def calculate_analyst_score(self, analyst_data: Dict, current_price: float) -> float:
        """
        Score based on analyst consensus (0-100)
        Weight: 40% of total score
        """
        if not analyst_data:
            return 50  # Neutral

        # Calculate consensus rating (1-5 scale)
        total = (analyst_data.get('strong_buy', 0) + analyst_data.get('buy', 0) +
                analyst_data.get('hold', 0) + analyst_data.get('sell', 0) +
                analyst_data.get('strong_sell', 0))

        if total == 0:
            return 50

        weighted_rating = (
            analyst_data.get('strong_buy', 0) * 5 +
            analyst_data.get('buy', 0) * 4 +
            analyst_data.get('hold', 0) * 3 +
            analyst_data.get('sell', 0) * 2 +
            analyst_data.get('strong_sell', 0) * 1
        ) / total

        # Convert to 0-100 scale (4.0+ = strong buy)
        rating_score = (weighted_rating - 1) / 4 * 100

        # Price target upside
        target_price = analyst_data.get('target_price', current_price)
        upside = ((target_price - current_price) / current_price) * 100 if target_price > 0 else 0
        upside_score = min(upside * 2, 100)  # Cap at 100

        # Combined analyst score (70% rating, 30% upside potential)
        score = rating_score * 0.7 + upside_score * 0.3
        return max(0, min(100, score))

    def calculate_quant_score(self, fundamentals: Dict, sector_avg_pe: float = 25) -> float:
        """
        Score based on quantitative factors (0-100)
        Weight: 20% of total score
        """
        if not fundamentals:
            return 50

        score = 0

        # P/E Ratio (undervalued vs sector)
        pe = fundamentals.get('pe_ratio', sector_avg_pe)
        if pe > 0:
            pe_score = max(0, (1 - (pe / sector_avg_pe)) * 100)
            score += pe_score * 0.3
        else:
            score += 25  # Neutral for negative P/E

        # ROE (>15% is good)
        roe = fundamentals.get('roe', 0) * 100
        roe_score = min(roe / 15 * 100, 100)
        score += roe_score * 0.3

        # Debt to Equity (<1 is healthy)
        de = fundamentals.get('debt_to_equity', 1)
        de_score = max(0, (2 - de) * 50) if de > 0 else 50
        score += min(de_score, 100) * 0.2

        # Growth proxy: EPS
        eps = fundamentals.get('eps', 0)
        eps_score = min(max(eps, 0) * 10, 100)  # Simple scaling
        score += eps_score * 0.2

        return max(0, min(100, score))

    def calculate_sentiment_score(self, sentiment_data: Dict) -> float:
        """
        Score based on market sentiment (0-100)
        Weight: 20% of total score
        """
        if not sentiment_data:
            return 50

        # Sentiment score (0-1 scale from Finnhub)
        sentiment = sentiment_data.get('sentiment_score', 0.5)
        sentiment_score = sentiment * 100

        # Recommendation trend (1-5 scale)
        rec_trend = sentiment_data.get('recommendation_trend', 3)
        rec_score = ((rec_trend - 1) / 4) * 100

        # News buzz (activity indicator)
        buzz = sentiment_data.get('news_buzz', 0)
        buzz_score = min(buzz / 10 * 100, 100)  # 10+ articles = max score

        # Combined (50% sentiment, 30% recommendations, 20% buzz)
        score = sentiment_score * 0.5 + rec_score * 0.3 + buzz_score * 0.2
        return max(0, min(100, score))

    def calculate_technical_score(self, technical_data: Dict) -> float:
        """
        Score based on technical indicators (0-100)
        Weight: 20% of total score
        """
        if not technical_data:
            return 50

        score = 0

        # SMA Crossover (bullish if 50 > 200)
        if technical_data.get('sma_crossover', False):
            score += 30
        else:
            score += 10

        # RSI (30-70 is ideal for entry)
        rsi = technical_data.get('rsi', 50)
        if 30 <= rsi <= 70:
            rsi_score = 100 - abs(50 - rsi)  # Closer to 50 is better
            score += rsi_score * 0.25
        else:
            score += 10  # Penalize overbought/oversold

        # Volume surge (bullish)
        if technical_data.get('volume_surge', False):
            score += 25
        else:
            score += 10

        # Price above support
        current = technical_data.get('current_price', 0)
        support = technical_data.get('support', 0)
        if current > support * 1.02:  # 2% above support
            score += 20
        else:
            score += 5

        return max(0, min(100, score))

    def determine_timeframe(self, technical_data: Dict, volatility: float) -> str:
        """Determine investment timeframe based on indicators"""
        if not technical_data:
            return "medium-term"

        rsi = technical_data.get('rsi', 50)
        momentum = technical_data.get('price_change_pct', 0)

        # Short-term (1-3 months): Strong momentum, not overbought
        if momentum > 5 and 30 <= rsi <= 70 and volatility < 40:
            return "short-term"
        # Medium-term (3-12 months): Stable growth pattern
        else:
            return "medium-term"

    def generate_thesis(self, ticker: str, analyst_data: Dict, fundamentals: Dict,
                       sentiment_data: Dict, technical_data: Dict) -> str:
        """Generate investment thesis"""
        thesis_parts = []

        # Analyst view
        if analyst_data:
            rating = analyst_data.get('consensus_rating', 'hold').upper()
            target = analyst_data.get('target_price', 0)
            if target > 0:
                thesis_parts.append(f"Analyst consensus {rating} with ${target:.2f} target")

        # Fundamental strength
        if fundamentals:
            sector = fundamentals.get('sector', 'Tech')
            roe = fundamentals.get('roe', 0) * 100
            if roe > 15:
                thesis_parts.append(f"strong {sector} fundamentals (ROE {roe:.1f}%)")

        # Momentum
        if technical_data:
            if technical_data.get('sma_crossover'):
                thesis_parts.append("bullish technical setup with golden cross")
            elif technical_data.get('volume_surge'):
                thesis_parts.append("volume breakout confirming momentum")

        # Sentiment
        if sentiment_data and sentiment_data.get('sentiment_score', 0) > 0.6:
            thesis_parts.append("positive market sentiment")

        thesis = "; ".join(thesis_parts) if thesis_parts else "Quantitative factors align for potential upside"
        return thesis[:200]  # Limit length

    def screen_stock(self, ticker: str) -> Optional[StockRecommendation]:
        """Screen individual stock and generate recommendation if qualified"""
        try:
            logger.info(f"Screening {ticker}...")

            # Fetch all data
            fundamentals = self.api_manager.get_fundamentals(ticker)
            analyst_data = self.api_manager.get_analyst_data(ticker)
            sentiment_data = self.api_manager.get_sentiment_data(ticker)
            technical_data = TechnicalAnalyzer.get_technical_indicators(ticker)
            volatility = TechnicalAnalyzer.calculate_volatility(ticker)

            if not technical_data:
                logger.warning(f"Insufficient technical data for {ticker}")
                return None

            current_price = technical_data.get('current_price', 0)
            if current_price == 0:
                return None

            # Calculate component scores
            analyst_score = self.calculate_analyst_score(analyst_data, current_price)
            quant_score = self.calculate_quant_score(fundamentals)
            sentiment_score = self.calculate_sentiment_score(sentiment_data)
            technical_score = self.calculate_technical_score(technical_data)

            # EPS estimate-revision factor (Zacks-style), scaled to 0-100.
            # Defaults to a neutral 50 when the module or data is unavailable.
            revision_score = 50.0
            revision_sentiment = "UNKNOWN"
            if HAS_EARNINGS_REVISIONS and compute_earnings_revision_signal is not None:
                try:
                    rev = compute_earnings_revision_signal(yf.Ticker(ticker), None, ticker) or {}
                    revision_score = float(rev.get("revision_score", 5.0) or 5.0) * 10.0
                    revision_sentiment = str(rev.get("sentiment", "UNKNOWN"))
                except Exception as e:
                    logger.debug(f"{ticker}: revision factor unavailable - {e}")

            # Overall score (weighted average).
            # [FIX] Analyst consensus is a lagging, herd-prone signal — historically
            # weak alpha and prone to look-ahead bias in target prices. Its weight is
            # cut from 40% to 10%; the freed weight is redistributed to fundamentals,
            # technicals, and the forward-looking EPS-revision factor.
            total_score = (
                analyst_score * 0.10 +
                quant_score * 0.30 +
                sentiment_score * 0.10 +
                technical_score * 0.25 +
                revision_score * 0.25
            )

            # Apply filters for high-conviction picks
            beta = fundamentals.get('beta', 1) if fundamentals else 1

            # Exclusion criteria
            if total_score < 80:
                logger.debug(f"{ticker} score {total_score:.1f} below threshold")
                return None

            if volatility > 50:
                logger.debug(f"{ticker} volatility {volatility:.1f}% too high")
                return None

            if beta > 2:
                logger.debug(f"{ticker} beta {beta:.2f} too high")
                return None

            # Estimate cuts are a strong avoid tell — never recommend a name whose
            # forward earnings estimates are being actively downgraded.
            if revision_sentiment == "DOWNGRADING":
                logger.debug(f"{ticker} excluded - EPS estimates downgrading")
                return None

            # Calculate entry, target, stop-loss
            support = technical_data.get('support', current_price * 0.95)
            entry_price = current_price if current_price > support * 1.02 else support * 1.02

            # Target: Analyst target +10% or 20% above current
            analyst_target = analyst_data.get('target_price', 0) if analyst_data else 0
            if analyst_target > current_price:
                target_price = analyst_target * 1.10
            else:
                target_price = current_price * 1.20

            stop_loss = support * 0.98  # 2% below support

            # Risk/reward ratio
            risk = current_price - stop_loss
            reward = target_price - current_price
            risk_reward = reward / risk if risk > 0 else 0

            # Timeframe
            timeframe = self.determine_timeframe(technical_data, volatility)

            # Thesis
            thesis = self.generate_thesis(ticker, analyst_data, fundamentals,
                                        sentiment_data, technical_data)

            # Company info
            stock = yf.Ticker(ticker)
            info = stock.info
            company_name = info.get('longName', ticker)
            market_cap = fundamentals.get('market_cap', 0) if fundamentals else info.get('marketCap', 0)
            sector = fundamentals.get('sector', 'Unknown') if fundamentals else info.get('sector', 'Unknown')

            recommendation = StockRecommendation(
                ticker=ticker,
                company_name=company_name,
                current_price=current_price,
                entry_price=entry_price,
                target_price=target_price,
                stop_loss=stop_loss,
                score=total_score,
                analyst_score=analyst_score,
                quant_score=quant_score,
                sentiment_score=sentiment_score,
                technical_score=technical_score,
                timeframe=timeframe,
                thesis=thesis,
                risk_reward_ratio=risk_reward,
                market_cap=market_cap,
                sector=sector,
                recommendation_date=datetime.now().strftime('%Y-%m-%d')
            )

            logger.info(f"✓ {ticker} qualified with score {total_score:.1f}")
            return recommendation

        except Exception as e:
            logger.error(f"Error screening {ticker}: {e}")
            return None

    def run_screening(self, max_picks: int = 5) -> List[StockRecommendation]:
        """Run full screening process"""
        logger.info("=" * 60)
        logger.info("ALPHA PICKS ENGINE - SCREENING STARTED")
        logger.info("=" * 60)

        universe = self.get_screener_universe()
        recommendations = []

        for ticker in universe:
            recommendation = self.screen_stock(ticker)
            if recommendation:
                recommendations.append(recommendation)

            # Rate limiting pause
            time.sleep(0.5)

            # Stop if we have enough picks
            if len(recommendations) >= max_picks * 2:  # Screen more to get top picks
                break

        # Sort by score and take top picks
        recommendations.sort(key=lambda x: x.score, reverse=True)
        top_picks = recommendations[:max_picks]

        logger.info(f"\nGenerated {len(top_picks)} high-conviction picks from {len(universe)} stocks screened")
        self.results = top_picks
        return top_picks

    def display_results(self):
        """Display recommendations in formatted table"""
        if not self.results:
            print("\nNo recommendations generated.")
            return

        df = pd.DataFrame([rec.to_dict() for rec in self.results])

        # Format display columns
        display_cols = [
            'ticker', 'company_name', 'current_price', 'entry_price',
            'target_price', 'stop_loss', 'score', 'timeframe', 'risk_reward_ratio'
        ]

        display_df = df[display_cols].copy()
        display_df['current_price'] = display_df['current_price'].apply(lambda x: f"${x:.2f}")
        display_df['entry_price'] = display_df['entry_price'].apply(lambda x: f"${x:.2f}")
        display_df['target_price'] = display_df['target_price'].apply(lambda x: f"${x:.2f}")
        display_df['stop_loss'] = display_df['stop_loss'].apply(lambda x: f"${x:.2f}")
        display_df['score'] = display_df['score'].apply(lambda x: f"{x:.1f}")
        display_df['risk_reward_ratio'] = display_df['risk_reward_ratio'].apply(lambda x: f"{x:.2f}x")

        print("\n" + "=" * 120)
        print("ALPHA PICKS - TOP RECOMMENDATIONS")
        print("=" * 120)
        print(display_df.to_string(index=False))
        print("=" * 120)

        # Detailed view
        print("\nDETAILED ANALYSIS:")
        print("-" * 120)
        for rec in self.results:
            print(f"\n{rec.ticker} - {rec.company_name}")
            print(f"  Score: {rec.score:.1f} (Analyst: {rec.analyst_score:.1f}, Quant: {rec.quant_score:.1f}, "
                  f"Sentiment: {rec.sentiment_score:.1f}, Technical: {rec.technical_score:.1f})")
            print(f"  Entry: ${rec.entry_price:.2f} | Target: ${rec.target_price:.2f} | Stop: ${rec.stop_loss:.2f}")
            print(f"  Timeframe: {rec.timeframe} | Risk/Reward: {rec.risk_reward_ratio:.2f}x")
            print(f"  Thesis: {rec.thesis}")

    def export_results(self, output_dir: str = '/mnt/user-data/outputs'):
        """Export results to CSV and JSON for Power BI integration"""
        if not self.results:
            logger.warning("No results to export")
            return

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # CSV export
        df = pd.DataFrame([rec.to_dict() for rec in self.results])
        csv_path = f"{output_dir}/alpha_picks_{timestamp}.csv"
        df.to_csv(csv_path, index=False)
        logger.info(f"Results exported to {csv_path}")

        # JSON export (for API integration)
        json_path = f"{output_dir}/alpha_picks_{timestamp}.json"
        with open(json_path, 'w') as f:
            json.dump([rec.to_dict() for rec in self.results], f, indent=2)
        logger.info(f"Results exported to {json_path}")

        return csv_path, json_path


class Backtester:
    """Backtest picks against historical performance.

    ⚠️ IMPORTANT — methodological warning:
    ``backtest_performance`` replays *today's* picks backwards over a historical
    window. Because the picks were selected using present-day data, the result
    suffers from severe **look-ahead and survivorship bias** and systematically
    overstates returns. It is retained ONLY as an in-sample sanity check and is
    explicitly flagged as NON-PREDICTIVE. For a credible, out-of-sample read use
    the walk-forward engine in ``core/apex_quant`` or track picks forward in a
    paper account from the recommendation date.
    """

    LOOKAHEAD_WARNING = (
        "LOOK-AHEAD/SURVIVORSHIP BIASED DIAGNOSTIC — NOT A VALID BACKTEST. "
        "Picks were chosen with present-day data then replayed into the past; "
        "returns are inflated. Use apex_quant walk-forward or forward paper "
        "tracking for any decision-grade performance estimate."
    )

    @staticmethod
    def backtest_performance(picks: List[StockRecommendation], period_days: int = 180) -> Dict:
        """In-sample diagnostic replay (NOT a valid backtest — see class docstring).

        Returns the historical numbers for transparency but tags them
        ``valid=False`` and attaches an explicit bias warning so no caller can
        mistake them for predictive, out-of-sample performance.
        """
        logger.warning(Backtester.LOOKAHEAD_WARNING)
        logger.info(f"\n[DIAGNOSTIC REPLAY] {len(picks)} picks over {period_days} days...")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)

        # Get S&P 500 benchmark
        spy = yf.Ticker('SPY')
        spy_hist = spy.history(start=start_date, end=end_date)
        spy_return = ((spy_hist['Close'].iloc[-1] - spy_hist['Close'].iloc[0]) /
                     spy_hist['Close'].iloc[0] * 100)

        results = []
        wins = 0

        for pick in picks:
            try:
                stock = yf.Ticker(pick.ticker)
                hist = stock.history(start=start_date, end=end_date)

                if hist.empty:
                    continue

                entry_price = hist['Close'].iloc[0]
                exit_price = hist['Close'].iloc[-1]
                return_pct = ((exit_price - entry_price) / entry_price) * 100

                # Check if target hit
                max_price = hist['Close'].max()
                target_hit = max_price >= pick.target_price

                if return_pct > 0:
                    wins += 1

                results.append({
                    'ticker': pick.ticker,
                    'entry': entry_price,
                    'exit': exit_price,
                    'return': return_pct,
                    'target_hit': target_hit
                })

            except Exception as e:
                logger.error(f"Backtest failed for {pick.ticker}: {e}")
                continue

        if not results:
            return {'valid': False, 'warning': Backtester.LOOKAHEAD_WARNING}

        avg_return = np.mean([r['return'] for r in results])
        win_rate = (wins / len(results)) * 100
        alpha = avg_return - spy_return

        backtest_summary = {
            'valid': False,
            'warning': Backtester.LOOKAHEAD_WARNING,
            'period_days': period_days,
            'picks_tested': len(results),
            'win_rate': win_rate,
            'avg_return': avg_return,
            'spy_return': spy_return,
            'alpha': alpha,
            'outperformance': avg_return / spy_return if spy_return != 0 else 0
        }

        print(f"\n{'='*60}")
        print("DIAGNOSTIC REPLAY (LOOK-AHEAD BIASED — NOT A VALID BACKTEST)")
        print(f"{'='*60}")
        print(f"Period: {period_days} days")
        print(f"Picks Tested: {len(results)}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Avg Return: {avg_return:.2f}%")
        print(f"S&P 500 Return: {spy_return:.2f}%")
        print(f"Alpha: {alpha:.2f}%")
        print(f"Outperformance: {backtest_summary['outperformance']:.2f}x")
        print(f"{'='*60}\n")

        return backtest_summary


def main():
    """Main execution function"""

    # Configuration
    config = {
        # API keys are loaded from the environment; unavailable optional
        # providers fail closed instead of shipping credentials in source.
        'alpha_vantage_api_key': os.getenv('ALPHA_VANTAGE_KEY', ''),
        'finnhub_api_key': os.getenv('FINNHUB_API_KEY', ''),
        'fmp_api_key': os.getenv('FMP_API_KEY', ''),
        'polygon_api_key': os.getenv('POLYGON_API_KEY', ''),

        # User preferences
        'max_picks': 5,
        'focus_sectors': ['Technology', 'Semiconductors', 'AI'],
        'output_dir': '/mnt/user-data/outputs',

        # Portfolio context (optional)
        'current_holdings': {
            'MU': {'shares': 5, 'avg_cost': 197.0}
        },
    }

    # Initialize engine
    engine = AlphaPicksEngine(config)

    # Run screening
    picks = engine.run_screening(max_picks=config['max_picks'])

    # Display results
    engine.display_results()

    # Export for Power BI
    if picks:
        csv_path, json_path = engine.export_results(config['output_dir'])
        print(f"\n✓ Results exported to {config['output_dir']}")
        print(f"  - CSV: {csv_path}")
        print(f"  - JSON: {json_path}")

    # Backtest (optional)
    if picks and len(picks) > 0:
        backtest_results = Backtester.backtest_performance(picks, period_days=180)

    print("\n" + "="*60)
    print("DISCLAIMER")
    print("="*60)
    print("This tool is for educational and research purposes only.")
    print("Not financial advice. Always do your own research and consult")
    print("a licensed financial advisor before making investment decisions.")
    print("="*60)

    return picks


if __name__ == "__main__":
    main()
