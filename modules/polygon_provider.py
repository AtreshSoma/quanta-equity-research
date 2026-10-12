"""
Polygon.io Data Provider for QUANTA Elite Trading System

Provides institutional-grade market data with:
- 99.9% uptime SLA
- Accurate corporate actions adjustments
- No rate limiting issues
- Historical data including delisted securities

Usage:
    provider = PolygonDataProvider()
    hist = provider.get_historical(ticker, period='1y')
    info = provider.get_stock_info(ticker)
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import time

logger = logging.getLogger(__name__)

# Try to import polygon-api-client
try:
    from polygon import RESTClient
    HAS_POLYGON = True
except ImportError:
    HAS_POLYGON = False
    logger.warning("polygon-api-client not installed. Run: pip install polygon-api-client")


class PolygonDataProvider:
    """
    Polygon.io data provider - institutional-grade market data.
    
    Advantages over yfinance:
    - SLA-backed uptime (99.9%)
    - Accurate corporate actions
    - No rate limiting for paid tiers
    - Includes delisted securities (no survivorship bias)
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize with API key.
        
        Args:
            api_key: Polygon.io API key. If None, reads from:
                     1. POLYGON_API_KEY environment variable
                     2. .polygon_key file in project directory
        """
        self.api_key = api_key or self._load_api_key()
        
        if not self.api_key:
            raise ValueError("Polygon API key required. Set POLYGON_API_KEY env var or pass to constructor.")
        
        if HAS_POLYGON:
            self.client = RESTClient(self.api_key)
        else:
            self.client = None
            logger.error("polygon-api-client not installed")
        
        self._cache: Dict[str, Tuple[pd.DataFrame, datetime]] = {}
        self._cache_ttl = timedelta(hours=1)
    
    def _load_api_key(self) -> Optional[str]:
        """Load API key from environment or file"""
        # Try environment variable
        key = os.environ.get('POLYGON_API_KEY')
        if key:
            return key
        
        # Try .polygon_key file
        key_file = os.path.join(os.path.dirname(__file__), '.polygon_key')
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                return f.read().strip()
        
        return None
    
    def get_historical(self, 
                       ticker: str, 
                       period: str = '1y',
                       interval: str = '1d') -> pd.DataFrame:
        """
        Get historical OHLCV data.
        
        Args:
            ticker: Stock symbol
            period: Time period ('1m', '3m', '6m', '1y', '2y', '5y')
            interval: Data interval ('1m', '5m', '15m', '1h', '1d')
            
        Returns:
            DataFrame with Open, High, Low, Close, Volume columns
        """
        if not self.client:
            logger.error("Polygon client not initialized")
            return pd.DataFrame()
        
        # Check cache
        cache_key = f"{ticker}_{period}_{interval}"
        if cache_key in self._cache:
            cached_data, cached_time = self._cache[cache_key]
            if datetime.now() - cached_time < self._cache_ttl:
                return cached_data
        
        try:
            # Parse period to date range
            period_days = {
                '1m': 30, '3m': 90, '6m': 180,
                '1y': 365, '2y': 730, '5y': 1825
            }
            days = period_days.get(period, 365)
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Map interval to Polygon timespan
            timespan_map = {
                '1m': 'minute', '5m': 'minute', '15m': 'minute',
                '1h': 'hour', '1d': 'day'
            }
            multiplier_map = {'1m': 1, '5m': 5, '15m': 15, '1h': 1, '1d': 1}
            
            timespan = timespan_map.get(interval, 'day')
            multiplier = multiplier_map.get(interval, 1)
            
            # Fetch from Polygon
            aggs = self.client.get_aggs(
                ticker=ticker,
                multiplier=multiplier,
                timespan=timespan,
                from_=start_date.strftime('%Y-%m-%d'),
                to=end_date.strftime('%Y-%m-%d'),
                adjusted=True,
                sort='asc',
                limit=50000
            )
            
            if not aggs:
                logger.warning(f"No data returned for {ticker}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            data = []
            for bar in aggs:
                data.append({
                    'Date': pd.to_datetime(bar.timestamp, unit='ms'),
                    'Open': bar.open,
                    'High': bar.high,
                    'Low': bar.low,
                    'Close': bar.close,
                    'Volume': bar.volume
                })
            
            df = pd.DataFrame(data)
            df.set_index('Date', inplace=True)
            
            # Cache result
            self._cache[cache_key] = (df, datetime.now())
            
            logger.debug(f"Fetched {len(df)} bars for {ticker} from Polygon")
            return df
            
        except Exception as e:
            logger.debug(f"Polygon API error for {ticker}: {e}")
            return pd.DataFrame()
    
    def get_stock_info(self, ticker: str) -> Dict:
        """
        Get stock fundamentals and metadata.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            Dictionary with stock info (similar to yfinance .info)
        """
        if not self.client:
            return {}
        
        try:
            # Get ticker details
            details = self.client.get_ticker_details(ticker)
            
            if not details:
                return {}
            
            # Get latest quote for current price
            try:
                quote = self.client.get_last_quote(ticker)
                current_price = (quote.ask_price + quote.bid_price) / 2 if quote else 0
            except Exception:
                current_price = 0
            
            # Map to yfinance-compatible format
            info = {
                'shortName': details.name if hasattr(details, 'name') else ticker,
                'longName': details.name if hasattr(details, 'name') else ticker,
                'sector': details.sic_description if hasattr(details, 'sic_description') else 'Unknown',
                'industry': details.sic_description if hasattr(details, 'sic_description') else 'Unknown',
                'marketCap': details.market_cap if hasattr(details, 'market_cap') else 0,
                'currentPrice': current_price,
                'regularMarketPrice': current_price,
                'currency': details.currency_name if hasattr(details, 'currency_name') else 'USD',
                'exchange': details.primary_exchange if hasattr(details, 'primary_exchange') else 'Unknown',
                'sharesOutstanding': details.share_class_shares_outstanding if hasattr(details, 'share_class_shares_outstanding') else 0,
                'totalRevenue': 0,  # Not available in basic Polygon
                'netIncomeToCommon': 0,
                'trailingPE': 0,
                'forwardPE': 0,
            }
            
            # Get financials if available (requires higher tier)
            try:
                financials = self.client.get_ticker_financials(ticker)
                if financials:
                    latest = financials[0] if isinstance(financials, list) else financials
                    if hasattr(latest, 'financials'):
                        fin = latest.financials
                        info['totalRevenue'] = getattr(fin.income_statement, 'revenues', {}).get('value', 0)
                        info['netIncomeToCommon'] = getattr(fin.income_statement, 'net_income_loss', {}).get('value', 0)
            except Exception:
                pass
            
            return info
            
        except Exception as e:
            logger.error(f"Error getting info for {ticker}: {e}")
            return {}
    
    def get_realtime_quote(self, ticker: str) -> Dict:
        """Get real-time quote (requires paid tier)"""
        if not self.client:
            return {}
        
        try:
            trade = self.client.get_last_trade(ticker)
            quote = self.client.get_last_quote(ticker)
            
            return {
                'price': trade.price if trade else 0,
                'bid': quote.bid_price if quote else 0,
                'ask': quote.ask_price if quote else 0,
                'bid_size': quote.bid_size if quote else 0,
                'ask_size': quote.ask_size if quote else 0,
                'timestamp': trade.timestamp if trade else None
            }
        except Exception as e:
            logger.error(f"Error getting quote for {ticker}: {e}")
            return {}
    
    def search_tickers(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for tickers by name or symbol"""
        if not self.client:
            return []
        
        try:
            results = self.client.list_tickers(
                search=query,
                active=True,
                limit=limit
            )
            
            return [
                {
                    'ticker': r.ticker,
                    'name': r.name,
                    'market': r.market,
                    'type': r.type
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error searching tickers: {e}")
            return []


class HybridDataProvider:
    """
    Hybrid data provider that uses Polygon.io with yfinance fallback.
    
    Best of both worlds:
    - Primary: Polygon.io for accurate, SLA-backed data
    - Fallback: yfinance for missing data or when Polygon unavailable
    """
    
    def __init__(self, polygon_key: Optional[str] = None):
        self.polygon = None
        self.use_polygon = False
        
        # Check for API key from multiple sources
        api_key = polygon_key or os.environ.get('POLYGON_API_KEY')
        
        # Also check .polygon_key file if no key provided
        if not api_key:
            key_file = os.path.join(os.path.dirname(__file__), '.polygon_key')
            if os.path.exists(key_file):
                with open(key_file, 'r') as f:
                    api_key = f.read().strip()
        
        # Try to initialize Polygon
        if api_key:
            try:
                self.polygon = PolygonDataProvider(api_key)
                self.use_polygon = True
                logger.info("✓ Polygon.io data provider initialized")
            except Exception as e:
                logger.warning(f"Polygon initialization failed: {e}")
        
        # yfinance always available as fallback
        try:
            import yfinance as yf
            self.yf = yf
            self.has_yfinance = True
        except ImportError:
            self.has_yfinance = False
    
    def get_historical(self, ticker: str, period: str = '1y') -> pd.DataFrame:
        """Get historical data from best available source"""
        
        # Try Polygon first - always preferred
        if self.use_polygon and self.polygon:
            try:
                df = self.polygon.get_historical(ticker, period)
                if not df.empty:
                    return df
            except Exception:
                pass
        
        # Fallback to yfinance with retry
        if self.has_yfinance:
            import time
            for attempt in range(2):  # Max 2 attempts
                try:
                    stock = self.yf.Ticker(ticker)
                    hist = stock.history(period=period)
                    if not hist.empty:
                        return hist
                except Exception as e:
                    if "Rate limit" in str(e) or "Too Many" in str(e):
                        if attempt == 0:
                            time.sleep(1)  # Brief pause before retry
                            continue
                    # Suppress error logging for expected failures
                    logger.debug(f"yfinance fallback for {ticker}: {e}")
        
        return pd.DataFrame()
    
    def get_stock_info(self, ticker: str) -> Dict:
        """Get stock info from best available source"""
        
        # Try Polygon first
        if self.use_polygon and self.polygon:
            info = self.polygon.get_stock_info(ticker)
            if info:
                return info
        
        # Fallback to yfinance
        if self.has_yfinance:
            try:
                stock = self.yf.Ticker(ticker)
                return stock.info
            except Exception:
                pass
        
        return {}


# Store API key securely
def save_polygon_key(api_key: str):
    """Save Polygon API key to file"""
    key_file = os.path.join(os.path.dirname(__file__), '.polygon_key')
    with open(key_file, 'w') as f:
        f.write(api_key)
    logger.info(f"Polygon API key saved to {key_file}")


if __name__ == "__main__":
    # Test the provider
    print("Polygon.io Data Provider")
    print("========================")
    
    # Check if API key is available
    key = os.environ.get('POLYGON_API_KEY')
    if not key:
        print("Set POLYGON_API_KEY environment variable to test")
    else:
        provider = PolygonDataProvider(key)
        
        # Test with AAPL
        print("\nTesting AAPL data fetch...")
        hist = provider.get_historical('AAPL', period='1m')
        print(f"Historical data: {len(hist)} bars")
        
        info = provider.get_stock_info('AAPL')
        print(f"Company: {info.get('longName', 'N/A')}")
        print(f"Market Cap: ${info.get('marketCap', 0):,.0f}")
