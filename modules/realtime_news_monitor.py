#!/usr/bin/env python3
"""
Real-Time News Monitor - Polling-Based Architecture
Replaces batch scanning with continuous 60-second interval monitoring

Features:
- Multi-source news aggregation (yfinance, Finnhub, AlphaVantage)
- News deduplication cache
- Age filtering (reject stale news >15 minutes)
- Callback-based event notifications
"""

import time
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Callable, Optional, Set
from dataclasses import dataclass
from collections import defaultdict
import hashlib

logger = logging.getLogger("QUANTA.RealtimeMonitor")


@dataclass
class NewsArticle:
    """Standardized news article structure"""
    ticker: str
    headline: str
    source: str
    published_time: datetime
    url: str
    summary: Optional[str] = None
    
    @property
    def age_minutes(self) -> float:
        """Calculate article age in minutes"""
        return (datetime.now() - self.published_time).total_seconds() / 60
    
    @property
    def content_hash(self) -> str:
        """Generate unique hash for deduplication"""
        content = f"{self.ticker}:{self.headline}:{self.published_time.isoformat()}"
        return hashlib.md5(content.encode()).hexdigest()


class NewsCache:
    """
    Deduplicate news articles to avoid processing same event multiple times
    """
    def __init__(self, ttl_minutes: int = 120):
        self.cache: Set[str] = set()
        self.ttl_minutes = ttl_minutes
        self.timestamps: Dict[str, datetime] = {}
        self._lock = threading.Lock()
    
    def exists(self, content_hash: str) -> bool:
        """Check if article already processed"""
        with self._lock:
            self._cleanup_expired()
            return content_hash in self.cache
    
    def add(self, content_hash: str):
        """Add article to cache"""
        with self._lock:
            self.cache.add(content_hash)
            self.timestamps[content_hash] = datetime.now()
    
    def _cleanup_expired(self):
        """Remove entries older than TTL"""
        now = datetime.now()
        expired = [
            h for h, t in self.timestamps.items()
            if (now - t).total_seconds() / 60 > self.ttl_minutes
        ]
        for h in expired:
            self.cache.discard(h)
            self.timestamps.pop(h, None)


class YFinanceNewsSource:
    """Fetch news from yfinance (existing source)"""
    
    def __init__(self):
        import yfinance as yf
        self.yf = yf
    
    def fetch_news(self, ticker: str, since_minutes: int = 60) -> List[NewsArticle]:
        """Fetch recent news for ticker"""
        articles = []
        try:
            stock = self.yf.Ticker(ticker)
            news = getattr(stock, 'news', []) or []
            
            cutoff_time = datetime.now() - timedelta(minutes=since_minutes)
            
            for item in news[:20]:  # Limit to recent 20
                timestamp = item.get('providerPublishTime')
                if not timestamp:
                    continue
                
                pub_time = datetime.fromtimestamp(timestamp)
                if pub_time < cutoff_time:
                    continue  # Too old
                
                article = NewsArticle(
                    ticker=ticker,
                    headline=item.get('title', ''),
                    source='yfinance',
                    published_time=pub_time,
                    url=item.get('link', ''),
                    summary=item.get('summary')
                )
                articles.append(article)
        
        except Exception as e:
            logger.debug(f"YFinance fetch error for {ticker}: {e}")
        
        return articles


class FinnhubNewsSource:
    """
    Fetch news from Finnhub (free tier: 60 calls/min)
    Sign up: https://finnhub.io/register
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or "YOUR_FINNHUB_API_KEY"  # Replace with actual key
        self.base_url = "https://finnhub.io/api/v1"
        self.enabled = api_key and api_key != "YOUR_FINNHUB_API_KEY"
        
        if self.enabled:
            import requests
            self.requests = requests
    
    def fetch_news(self, ticker: str, since_minutes: int = 60) -> List[NewsArticle]:
        """Fetch company news from Finnhub"""
        if not self.enabled:
            return []
        
        articles = []
        try:
            # Calculate date range
            to_date = datetime.now()
            from_date = to_date - timedelta(minutes=since_minutes)
            
            params = {
                'symbol': ticker,
                'from': from_date.strftime('%Y-%m-%d'),
                'to': to_date.strftime('%Y-%m-%d'),
                'token': self.api_key
            }
            
            response = self.requests.get(
                f"{self.base_url}/company-news",
                params=params,
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                for item in data[:20]:  # Limit to 20 most recent
                    pub_time = datetime.fromtimestamp(item.get('datetime', 0))
                    
                    article = NewsArticle(
                        ticker=ticker,
                        headline=item.get('headline', ''),
                        source='Finnhub',
                        published_time=pub_time,
                        url=item.get('url', ''),
                        summary=item.get('summary')
                    )
                    articles.append(article)
        
        except Exception as e:
            logger.debug(f"Finnhub fetch error for {ticker}: {e}")
        
        return articles


class AlphaVantageNewsSource:
    """
    Fetch news from AlphaVantage (free tier: 5 calls/min, 500/day)
    Sign up: https://www.alphavantage.co/support/#api-key
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or "YOUR_ALPHAVANTAGE_API_KEY"
        self.base_url = "https://www.alphavantage.co/query"
        self.enabled = api_key and api_key != "YOUR_ALPHAVANTAGE_API_KEY"
        
        if self.enabled:
            import requests
            self.requests = requests
    
    def fetch_news(self, ticker: str, since_minutes: int = 60) -> List[NewsArticle]:
        """Fetch news from AlphaVantage News Sentiment API"""
        if not self.enabled:
            return []
        
        articles = []
        try:
            params = {
                'function': 'NEWS_SENTIMENT',
                'tickers': ticker,
                'apikey': self.api_key
            }
            
            response = self.requests.get(self.base_url, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                cutoff_time = datetime.now() - timedelta(minutes=since_minutes)
                
                for item in data.get('feed', [])[:20]:
                    # Parse time_published: "20231215T150000"
                    time_str = item.get('time_published', '')
                    try:
                        pub_time = datetime.strptime(time_str, '%Y%m%dT%H%M%S')
                    except:
                        continue
                    
                    if pub_time < cutoff_time:
                        continue
                    
                    article = NewsArticle(
                        ticker=ticker,
                        headline=item.get('title', ''),
                        source='AlphaVantage',
                        published_time=pub_time,
                        url=item.get('url', ''),
                        summary=item.get('summary')
                    )
                    articles.append(article)
        
        except Exception as e:
            logger.debug(f"AlphaVantage fetch error for {ticker}: {e}")
        
        return articles


class RealtimeNewsMonitor:
    """
    Continuous news monitoring with 60-second polling intervals
    Aggregates from multiple sources and filters stale news
    """
    
    def __init__(self, tickers: List[str], 
                 polling_interval: int = 60,
                 max_news_age_minutes: int = 15,
                 finnhub_key: Optional[str] = None,
                 alphavantage_key: Optional[str] = None):
        """
        Args:
            tickers: List of stock symbols to monitor
            polling_interval: Seconds between checks (default: 60)
            max_news_age_minutes: Reject news older than this (default: 15)
            finnhub_key: Optional Finnhub API key
            alphavantage_key: Optional AlphaVantage API key
        """
        self.tickers = tickers
        self.polling_interval = polling_interval
        self.max_news_age_minutes = max_news_age_minutes
        self.cache = NewsCache(ttl_minutes=120)
        
        # Initialize news sources
        self.sources = {
            'yfinance': YFinanceNewsSource(),
        }
        
        # Add optional premium sources if keys provided
        if finnhub_key:
            self.sources['finnhub'] = FinnhubNewsSource(finnhub_key)
            logger.info("✓ Finnhub news source enabled")
        
        if alphavantage_key:
            self.sources['alphavantage'] = AlphaVantageNewsSource(alphavantage_key)
            logger.info("✓ AlphaVantage news source enabled")
        
        self.running = False
        self.thread = None
        self.callbacks: List[Callable[[str, NewsArticle], None]] = []
    
    def register_callback(self, callback: Callable[[str, NewsArticle], None]):
        """
        Register callback function to be called when new article detected
        Signature: callback(ticker: str, article: NewsArticle)
        """
        self.callbacks.append(callback)
    
    def start_monitoring(self):
        """Start continuous monitoring in background thread"""
        if self.running:
            logger.warning("Monitor already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.thread.start()
        
        logger.info(f"🔴 LIVE: Monitoring {len(self.tickers)} tickers "
                   f"(polling every {self.polling_interval}s, "
                   f"max news age: {self.max_news_age_minutes} min)")
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("⚫ Monitoring stopped")
    
    def _monitoring_loop(self):
        """Main monitoring loop - runs continuously"""
        cycle_count = 0
        
        while self.running:
            cycle_count += 1
            cycle_start = time.time()
            
            logger.debug(f"Monitoring cycle #{cycle_count} - checking {len(self.tickers)} tickers")
            
            # Fetch news for all tickers
            for ticker in self.tickers:
                if not self.running:
                    break
                
                try:
                    self._fetch_and_process_ticker(ticker)
                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
            
            # Sleep until next cycle
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.polling_interval - elapsed)
            
            if sleep_time > 0:
                logger.debug(f"Cycle completed in {elapsed:.1f}s, sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
    
    def _fetch_and_process_ticker(self, ticker: str):
        """Fetch news from all sources and process new articles"""
        articles_found = 0
        
        # Fetch from each source
        for source_name, source in self.sources.items():
            try:
                articles = source.fetch_news(ticker, since_minutes=self.max_news_age_minutes * 2)
                
                for article in articles:
                    # Age filter
                    if article.age_minutes > self.max_news_age_minutes:
                        continue
                    
                    # Deduplication
                    if self.cache.exists(article.content_hash):
                        continue
                    
                    # New article detected!
                    self.cache.add(article.content_hash)
                    articles_found += 1
                    
                    logger.info(
                        f"📰 {ticker}: NEW from {article.source} "
                        f"({article.age_minutes:.0f}m old): {article.headline[:80]}"
                    )
                    
                    # Notify callbacks
                    for callback in self.callbacks:
                        try:
                            callback(ticker, article)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
            
            except Exception as e:
                logger.debug(f"Source {source_name} error for {ticker}: {e}")
        
        if articles_found > 0:
            logger.info(f"✓ {ticker}: {articles_found} new articles processed")


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

def example_callback(ticker: str, article: NewsArticle):
    """Example callback function"""
    print(f"\n🚨 CATALYST ALERT: {ticker}")
    print(f"   Headline: {article.headline}")
    print(f"   Source: {article.source}")
    print(f"   Age: {article.age_minutes:.0f} minutes")
    print(f"   URL: {article.url}")
    
    # Here you would trigger your catalyst analysis
    # from catalyst_intelligence_engine import InstitutionalCatalystEngine
    # engine = InstitutionalCatalystEngine()
    # signal = engine.analyze_article(ticker, article)


if __name__ == "__main__":
    # Example: Monitor top tech stocks for catalysts
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
    )
    
    # Create monitor
    monitor = RealtimeNewsMonitor(
        tickers=['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META'],
        polling_interval=60,
        max_news_age_minutes=15,
        # Optionally add API keys:
        # finnhub_key="your_key_here",
        # alphavantage_key="your_key_here"
    )
    
    # Register callback
    monitor.register_callback(example_callback)
    
    # Start monitoring
    monitor.start_monitoring()
    
    # Run for 10 minutes as demo
    try:
        print("\n" + "="*80)
        print("Real-Time News Monitor - Running for 10 minutes")
        print("Press Ctrl+C to stop")
        print("="*80 + "\n")
        
        time.sleep(600)  # 10 minutes
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    finally:
        monitor.stop_monitoring()
