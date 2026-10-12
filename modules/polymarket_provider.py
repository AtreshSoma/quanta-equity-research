#!/usr/bin/env python3
"""
Polymarket Prediction Market Provider
=====================================

Provides "wisdom of the crowd" signals from prediction markets for:
- Federal Reserve rate decisions
- Recession probability
- Market-moving macro events

Uses Polymarket's free Gamma API (no auth required for read-only access)

Author: QUANTA Elite Team
Date: January 2026
"""

import requests
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 15  # seconds
CACHE_TTL_MINUTES = 15  # How long to cache results

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class PredictionSignal:
    """Individual prediction market signal"""
    market_id: str
    market_name: str
    probability: float  # 0.0 to 1.0 (e.g., 0.65 = 65% probability)
    volume_24h: float   # USD volume in last 24 hours
    liquidity: float    # Current liquidity in USD
    last_updated: datetime
    source: str = "Polymarket"
    
    @property
    def confidence_level(self) -> str:
        """Assess confidence based on volume/liquidity"""
        if self.volume_24h > 100_000:
            return "HIGH"
        elif self.volume_24h > 25_000:
            return "MEDIUM"
        else:
            return "LOW"

@dataclass 
class MarketSentiment:
    """Aggregated market sentiment from prediction markets"""
    fed_rate_cut_prob: float = 0.5      # Probability of Fed cutting rates
    fed_rate_hike_prob: float = 0.1     # Probability of Fed hiking rates
    recession_prob: float = 0.15        # Probability of recession
    sp500_up_prob: float = 0.5          # Probability S&P ends higher
    overall_risk: str = "NEUTRAL"       # LOW, NEUTRAL, ELEVATED, HIGH
    signals: List[PredictionSignal] = field(default_factory=list)
    last_updated: Optional[datetime] = None
    data_quality: str = "UNKNOWN"       # GOOD, PARTIAL, STALE, UNAVAILABLE

@dataclass
class StockPredictionSignal:
    """
    Company-specific prediction market signals.
    Used for earnings beat/miss probabilities and price predictions.
    """
    ticker: str
    company_name: str = ""
    
    # Earnings predictions
    earnings_beat_prob: Optional[float] = None  # Probability of beating EPS estimate
    earnings_miss_prob: Optional[float] = None  # Probability of missing EPS estimate
    earnings_consensus_eps: Optional[float] = None  # Street consensus EPS
    earnings_date: Optional[str] = None  # Expected earnings release date
    
    # Stock price predictions
    price_up_prob: Optional[float] = None  # Probability stock goes up
    price_target_prob: Optional[float] = None  # Probability of hitting target
    
    # Metadata
    signal_count: int = 0  # Number of markets found
    confidence: str = "NONE"  # HIGH, MEDIUM, LOW, NONE
    signals: List[PredictionSignal] = field(default_factory=list)
    last_updated: Optional[datetime] = None
    
    @property
    def has_data(self) -> bool:
        """Check if any meaningful data was found"""
        return self.signal_count > 0
    
    @property
    def earnings_outlook(self) -> str:
        """Summarize earnings outlook"""
        if self.earnings_beat_prob is None:
            return "Unknown"
        elif self.earnings_beat_prob >= 0.70:
            return "Strong Beat Expected"
        elif self.earnings_beat_prob >= 0.55:
            return "Likely Beat"
        elif self.earnings_beat_prob >= 0.45:
            return "Uncertain"
        elif self.earnings_beat_prob >= 0.30:
            return "Likely Miss"
        else:
            return "Strong Miss Expected"

# ============================================================================
# POLYMARKET PROVIDER
# ============================================================================

class PolymarketProvider:
    """
    Fetch prediction market data from Polymarket Gamma API.
    
    Provides market intelligence signals for:
    - Fed rate decisions
    - Recession probability
    - Market direction sentiment
    
    Usage:
        provider = PolymarketProvider()
        sentiment = provider.get_market_sentiment()
        print(f"Fed Rate Cut Probability: {sentiment.fed_rate_cut_prob:.1%}")
    """
    
    # Keyword filters for finding relevant markets
    FED_RATE_KEYWORDS = ["fed", "rate", "fomc", "powell", "interest rate", "fed funds"]
    RECESSION_KEYWORDS = ["recession", "gdp", "economic downturn", "nber"]
    MARKET_KEYWORDS = ["s&p", "sp500", "stock market", "nasdaq", "dow"]
    EARNINGS_KEYWORDS = ["earnings", "eps", "beat", "miss", "revenue"]
    
    def __init__(self, cache_ttl_minutes: int = CACHE_TTL_MINUTES):
        """
        Initialize Polymarket provider.
        
        Args:
            cache_ttl_minutes: How long to cache API results (default 15 min)
        """
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._last_request_time = 0.0
        self._min_request_interval = 1.0  # Rate limiting: 1 request per second
    
    def get_market_sentiment(self) -> MarketSentiment:
        """
        Get aggregated market sentiment from all tracked prediction markets.
        
        Returns:
            MarketSentiment object with probabilities and signals
        """
        sentiment = MarketSentiment()
        all_signals = []
        
        try:
            # Fetch Fed rate signals
            fed_signals = self.get_fed_rate_signals()
            all_signals.extend(fed_signals)
            
            for sig in fed_signals:
                name_lower = sig.market_name.lower()
                if any(word in name_lower for word in ['cut', 'lower', 'decrease']):
                    sentiment.fed_rate_cut_prob = max(sentiment.fed_rate_cut_prob, sig.probability)
                elif any(word in name_lower for word in ['hike', 'raise', 'increase']):
                    sentiment.fed_rate_hike_prob = max(sentiment.fed_rate_hike_prob, sig.probability)
            
            # Fetch recession signals
            recession_sig = self.get_recession_probability()
            if recession_sig:
                sentiment.recession_prob = recession_sig.probability
                all_signals.append(recession_sig)
            
            # Fetch market direction signals
            market_signals = self.get_market_direction_signals()
            all_signals.extend(market_signals)
            
            for sig in market_signals:
                name_lower = sig.market_name.lower()
                if any(word in name_lower for word in ['up', 'higher', 'bull', 'positive']):
                    sentiment.sp500_up_prob = sig.probability
            
            # Determine overall risk level
            sentiment.overall_risk = self._calculate_risk_level(sentiment)
            
            # Set metadata
            sentiment.signals = all_signals
            sentiment.last_updated = datetime.now()
            sentiment.data_quality = "GOOD" if len(all_signals) >= 3 else "PARTIAL" if all_signals else "UNAVAILABLE"
            
        except Exception as e:
            logger.warning(f"Polymarket sentiment fetch failed: {e}")
            sentiment.data_quality = "UNAVAILABLE"
        
        return sentiment
    
    def get_fed_rate_signals(self) -> List[PredictionSignal]:
        """Get Federal Reserve rate decision probabilities."""
        return self._fetch_markets_by_keywords(self.FED_RATE_KEYWORDS)
    
    def get_recession_probability(self) -> Optional[PredictionSignal]:
        """Get recession probability signal (highest volume market)."""
        signals = self._fetch_markets_by_keywords(self.RECESSION_KEYWORDS)
        if signals:
            # Return highest volume signal for most reliable probability
            return max(signals, key=lambda s: s.volume_24h)
        return None
    
    def get_market_direction_signals(self) -> List[PredictionSignal]:
        """Get market direction prediction signals."""
        return self._fetch_markets_by_keywords(self.MARKET_KEYWORDS)
    
    def get_company_earnings_signals(self, ticker: str) -> List[PredictionSignal]:
        """
        Get earnings prediction signals for a specific company.
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            
        Returns:
            List of earnings-related prediction signals
        """
        # Map ticker to company name for better search
        ticker_to_company = {
            'AAPL': 'apple', 'MSFT': 'microsoft', 'GOOGL': 'google', 'GOOG': 'google',
            'AMZN': 'amazon', 'META': 'meta', 'NVDA': 'nvidia', 'TSLA': 'tesla',
            'AMD': 'amd', 'NFLX': 'netflix', 'CRM': 'salesforce', 'ORCL': 'oracle',
            'INTC': 'intel', 'IBM': 'ibm', 'CSCO': 'cisco', 'ADBE': 'adobe',
            'JPM': 'jpmorgan', 'BAC': 'bank of america', 'GS': 'goldman',
            'V': 'visa', 'MA': 'mastercard', 'PYPL': 'paypal',
            'DIS': 'disney', 'WMT': 'walmart', 'KO': 'coca-cola', 'PEP': 'pepsi',
            'MCD': 'mcdonald', 'NKE': 'nike', 'SBUX': 'starbucks',
            'XOM': 'exxon', 'CVX': 'chevron', 'BA': 'boeing', 'CAT': 'caterpillar'
        }
        
        company_name = ticker_to_company.get(ticker.upper(), ticker.lower())
        company_keywords = self.EARNINGS_KEYWORDS + [company_name, ticker.lower()]
        return self._fetch_markets_by_keywords(company_keywords)
    
    def get_stock_prediction(self, ticker: str) -> StockPredictionSignal:
        """
        Get comprehensive prediction market signals for a stock.
        
        Searches for:
        - Earnings beat/miss probabilities
        - Stock price predictions
        - Company-specific markets
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            
        Returns:
            StockPredictionSignal with earnings and price predictions
        """
        ticker_upper = ticker.upper()
        
        # Map ticker to company name
        ticker_to_company = {
            'AAPL': 'Apple', 'MSFT': 'Microsoft', 'GOOGL': 'Google', 'GOOG': 'Alphabet',
            'AMZN': 'Amazon', 'META': 'Meta', 'NVDA': 'NVIDIA', 'TSLA': 'Tesla',
            'AMD': 'AMD', 'NFLX': 'Netflix', 'CRM': 'Salesforce', 'ORCL': 'Oracle',
            'INTC': 'Intel', 'IBM': 'IBM', 'CSCO': 'Cisco', 'ADBE': 'Adobe',
            'JPM': 'JPMorgan', 'BAC': 'Bank of America', 'GS': 'Goldman Sachs',
            'V': 'Visa', 'MA': 'Mastercard', 'PYPL': 'PayPal',
            'DIS': 'Disney', 'WMT': 'Walmart', 'KO': 'Coca-Cola', 'PEP': 'PepsiCo'
        }
        
        company_name = ticker_to_company.get(ticker_upper, ticker_upper)
        
        result = StockPredictionSignal(
            ticker=ticker_upper,
            company_name=company_name,
            last_updated=datetime.now()
        )
        
        try:
            # Fetch company-specific signals
            signals = self.get_company_earnings_signals(ticker)
            result.signals = signals
            result.signal_count = len(signals)
            
            # Parse earnings signals
            for sig in signals:
                name_lower = sig.market_name.lower()
                
                # Earnings beat probability
                if any(word in name_lower for word in ['beat', 'exceed', 'above', 'higher']):
                    result.earnings_beat_prob = sig.probability
                elif any(word in name_lower for word in ['miss', 'below', 'lower', 'under']):
                    result.earnings_miss_prob = sig.probability
                    # Infer beat prob from miss prob
                    if result.earnings_beat_prob is None:
                        result.earnings_beat_prob = 1.0 - sig.probability
                
                # Stock price direction
                if any(word in name_lower for word in ['stock', 'price', 'close']):
                    if any(word in name_lower for word in ['up', 'rise', 'higher', 'above']):
                        result.price_up_prob = sig.probability
            
            # Calculate miss prob if we have beat prob
            if result.earnings_beat_prob is not None and result.earnings_miss_prob is None:
                result.earnings_miss_prob = 1.0 - result.earnings_beat_prob
            
            # Set confidence based on signal volume
            if result.signal_count > 0:
                max_volume = max(s.volume_24h for s in signals) if signals else 0
                if max_volume > 100_000:
                    result.confidence = "HIGH"
                elif max_volume > 25_000:
                    result.confidence = "MEDIUM"
                else:
                    result.confidence = "LOW"
                    
        except Exception as e:
            logger.debug(f"Stock prediction fetch failed for {ticker}: {e}")
        
        return result
    
    # ========================================================================
    # PRIVATE METHODS
    # ========================================================================
    
    def _fetch_markets_by_keywords(self, keywords: List[str]) -> List[PredictionSignal]:
        """
        Fetch markets matching any of the given keywords.
        
        Args:
            keywords: List of keywords to search for
            
        Returns:
            List of matching prediction signals
        """
        cache_key = "_".join(sorted(keywords[:3]))  # Use first 3 keywords as cache key
        
        # Check cache
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        signals = []
        
        try:
            # Rate limiting
            self._rate_limit()
            
            # Fetch events from Gamma API
            response = requests.get(
                f"{GAMMA_API_BASE}/events",
                params={
                    "limit": 100,
                    "active": True,
                    "closed": False
                },
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            events = response.json()
            
            # Filter events by keywords
            for event in events:
                title = (event.get('title', '') or '').lower()
                description = (event.get('description', '') or '').lower()
                
                # Check if any keyword matches
                if any(kw in title or kw in description for kw in keywords):
                    # Extract markets from event
                    for market in event.get('markets', []):
                        try:
                            signal = self._parse_market(market, event)
                            if signal:
                                signals.append(signal)
                        except Exception as e:
                            logger.debug(f"Failed to parse market: {e}")
            
            # Sort by volume (most liquid first)
            signals.sort(key=lambda s: s.volume_24h, reverse=True)
            
            # Cache results
            self._set_cached(cache_key, signals)
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"Polymarket API request failed: {e}")
        except Exception as e:
            logger.warning(f"Polymarket fetch error: {e}")
        
        return signals
    
    def _parse_market(self, market: dict, event: dict) -> Optional[PredictionSignal]:
        """Parse a market object into a PredictionSignal."""
        try:
            import json
            
            # Get probability from bestAsk or outcomePrices
            probability = 0.5  # Default neutral
            
            if 'outcomePrices' in market:
                prices = market['outcomePrices']
                # API returns outcomePrices as JSON string, not list
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except json.JSONDecodeError:
                        prices = None
                if isinstance(prices, list) and len(prices) > 0:
                    probability = float(prices[0])
            elif 'bestAsk' in market:
                probability = float(market.get('bestAsk', 0.5))
            
            # Get volume and liquidity
            volume_24h = float(market.get('volume24hr', 0) or market.get('volume', 0) or 0)
            liquidity = float(market.get('liquidity', 0) or 0)
            
            return PredictionSignal(
                market_id=str(market.get('id', '')),
                market_name=market.get('question', '') or event.get('title', 'Unknown'),
                probability=probability,
                volume_24h=volume_24h,
                liquidity=liquidity,
                last_updated=datetime.now()
            )
        except Exception as e:
            logger.debug(f"Market parse error: {e}")
            return None
    
    def _calculate_risk_level(self, sentiment: MarketSentiment) -> str:
        """Calculate overall risk level from sentiment signals."""
        risk_score = 0
        
        # Recession factor (heaviest weight)
        if sentiment.recession_prob > 0.50:
            risk_score += 3
        elif sentiment.recession_prob > 0.35:
            risk_score += 2
        elif sentiment.recession_prob > 0.20:
            risk_score += 1
        
        # Fed hawkish factor
        if sentiment.fed_rate_hike_prob > 0.50:
            risk_score += 2
        elif sentiment.fed_rate_hike_prob > 0.30:
            risk_score += 1
        
        # Market direction factor
        if sentiment.sp500_up_prob < 0.35:
            risk_score += 2
        elif sentiment.sp500_up_prob < 0.45:
            risk_score += 1
        
        # Map score to risk level
        if risk_score >= 5:
            return "HIGH"
        elif risk_score >= 3:
            return "ELEVATED"
        elif risk_score >= 1:
            return "MODERATE"
        else:
            return "LOW"
    
    def _rate_limit(self):
        """Apply rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        if key in self._cache:
            timestamp = self._cache_timestamps.get(key)
            if timestamp and (datetime.now() - timestamp) < self._cache_ttl:
                return self._cache[key]
        return None
    
    def _set_cached(self, key: str, value: Any):
        """Set cached value with timestamp."""
        self._cache[key] = value
        self._cache_timestamps[key] = datetime.now()


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_market_intelligence() -> MarketSentiment:
    """
    Quick access to market intelligence from prediction markets.
    
    Returns:
        MarketSentiment with current prediction market signals
    """
    provider = PolymarketProvider()
    return provider.get_market_sentiment()


def display_market_intelligence():
    """Display formatted market intelligence to console."""
    sentiment = get_market_intelligence()
    
    print("\n" + "=" * 60)
    print("📊 PREDICTION MARKET INTELLIGENCE")
    print("=" * 60)
    
    if sentiment.data_quality == "UNAVAILABLE":
        print("⚠️  Prediction market data unavailable")
        return
    
    print(f"\n🏛️  FED RATE OUTLOOK:")
    print(f"   Rate Cut Probability:  {sentiment.fed_rate_cut_prob:.1%}")
    print(f"   Rate Hike Probability: {sentiment.fed_rate_hike_prob:.1%}")
    
    print(f"\n📉 RECESSION RISK:")
    print(f"   Recession Probability: {sentiment.recession_prob:.1%}")
    
    print(f"\n📈 MARKET DIRECTION:")
    print(f"   S&P 500 Up Probability: {sentiment.sp500_up_prob:.1%}")
    
    risk_colors = {
        "LOW": "🟢", "MODERATE": "🟡", 
        "ELEVATED": "🟠", "HIGH": "🔴"
    }
    risk_icon = risk_colors.get(sentiment.overall_risk, "⚪")
    print(f"\n{risk_icon} OVERALL RISK LEVEL: {sentiment.overall_risk}")
    
    print(f"\n📊 Data Quality: {sentiment.data_quality}")
    print(f"🕐 Last Updated: {sentiment.last_updated.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60 + "\n")


# ============================================================================
# STANDALONE TEST
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    display_market_intelligence()
