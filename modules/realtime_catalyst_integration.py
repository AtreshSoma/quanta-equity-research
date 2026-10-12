#!/usr/bin/env python3
"""
Real-Time Catalyst Trading System - Integration Example
Combines all three new modules into a complete catalyst trading workflow

Usage:
    python realtime_catalyst_integration.py

This demonstrates how to:
1. Monitor stocks in real-time for catalyst news
2. Validate catalysts with volume/price confirmation
3. Determine optimal entry windows
4. Generate trading recommendations
"""

import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict

# Import new modules
from realtime_news_monitor import RealtimeNewsMonitor, NewsArticle
from catalyst_validator import CatalystValidator, ValidationResult
from entry_optimizer import EntryOptimizer, EntryWindow, EntryStatus

# Optional: Import existing catalyst engines if available
try:
    from catalyst_intelligence_engine import InstitutionalCatalystEngine, InstitutionalCatalystSignal
    HAS_INSTITUTIONAL = True
except ImportError:
    HAS_INSTITUTIONAL = False
    print("⚠️  catalyst_intelligence_engine not found - using basic analysis")

try:
    from catalyst_scanner import CatalystScanner
    HAS_BASIC_SCANNER = True
except ImportError:
    HAS_BASIC_SCANNER = False


logger = logging.getLogger("QUANTA.RealtimeCatalyst")


class RealtimeCatalystTradingSystem:
    """
    Complete real-time catalyst trading system
    Integrates monitoring, validation, and entry optimization
    """
    
    def __init__(self, 
                 tickers: List[str],
                 capital: float = 10000.0,
                 finnhub_key: str = None,
                 alphavantage_key: str = None):
        """
        Initialize the real-time catalyst trading system
        
        Args:
            tickers: Stocks to monitor
            capital: Trading capital
            finnhub_key: Optional Finnhub API key
            alphavantage_key: Optional AlphaVantage API key
        """
        self.tickers = tickers
        self.capital = capital
        
        # Initialize components
        self.news_monitor = RealtimeNewsMonitor(
            tickers=tickers,
            polling_interval=60,
            max_news_age_minutes=15,
            finnhub_key=finnhub_key,
            alphavantage_key=alphavantage_key
        )
        
        self.validator = CatalystValidator()
        self.entry_optimizer = EntryOptimizer()
        
        # Optional: Initialize institutional catalyst engine
        if HAS_INSTITUTIONAL:
            self.catalyst_engine = InstitutionalCatalystEngine()
        elif HAS_BASIC_SCANNER:
            self.catalyst_scanner = CatalystScanner()
        else:
            self.catalyst_engine = None
            self.catalyst_scanner = None
        
        # Track active signals
        self.active_signals: Dict[str, Dict] = {}
        
        # Register callback
        self.news_monitor.register_callback(self.on_news_received)
    
    def start(self):
        """Start the real-time monitoring system"""
        logger.info("="*80)
        logger.info("🚀 REAL-TIME CATALYST TRADING SYSTEM")
        logger.info("="*80)
        logger.info(f"Capital: ${self.capital:,.0f}")
        logger.info(f"Monitoring: {len(self.tickers)} tickers")
        logger.info(f"Polling Interval: 60 seconds")
        logger.info(f"Max News Age: 15 minutes")
        logger.info("="*80)
        
        # Start monitoring
        self.news_monitor.start_monitoring()
        
        print("\n✅ System running. Press Ctrl+C to stop.\n")
    
    def stop(self):
        """Stop the system"""
        self.news_monitor.stop_monitoring()
        logger.info("System stopped")
    
    def on_news_received(self, ticker: str, article: NewsArticle):
        """
        Callback triggered when new news article is detected
        
        This is where the magic happens:
        1. Analyze catalyst type
        2. Validate with volume/price
        3. Determine entry window
        4. Generate trading recommendation
        """
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"📰 NEW CATALYST DETECTED: {ticker}")
            logger.info(f"{'='*80}")
            logger.info(f"Headline: {article.headline}")
            logger.info(f"Source: {article.source}")
            logger.info(f"Age: {article.age_minutes:.1f} minutes")
            
            # === STEP 1: CATALYST ANALYSIS ===
            catalyst_type, catalyst_direction, confidence = self._analyze_catalyst(
                ticker, article
            )
            
            if not catalyst_type:
                logger.info(f"❌ {ticker}: No actionable catalyst detected")
                return
            
            logger.info(f"✓ Catalyst Type: {catalyst_type}")
            logger.info(f"✓ Direction: {catalyst_direction}")
            logger.info(f"✓ Initial Confidence: {confidence:.0%}")
            
            # === STEP 2: VALIDATION (Volume & Price Coherence) ===
            validation = self.validator.validate_catalyst(
                ticker=ticker,
                catalyst_direction=catalyst_direction,
                catalyst_time=article.published_time
            )
            
            logger.info(f"\n📊 VALIDATION RESULTS:")
            logger.info(f"   Valid: {validation.is_valid}")
            logger.info(f"   Confidence: {validation.confidence_score:.0%}")
            logger.info(f"   Volume Score: {validation.volume_score:.0%}")
            logger.info(f"   Price Score: {validation.price_score:.0%}")
            logger.info(f"   Direction Match: {validation.direction_match}")
            
            if not validation.is_valid:
                logger.warning(f"⚠️  {ticker}: Validation failed - skipping trade")
                return
            
            # === STEP 3: ENTRY OPTIMIZATION ===
            entry_window = self.entry_optimizer.calculate_entry_window(
                ticker=ticker,
                catalyst_time=article.published_time,
                catalyst_direction=catalyst_direction,
                position_size_usd=self.capital * 0.10  # 10% of capital
            )
            
            logger.info(f"\n🎯 ENTRY RECOMMENDATION:")
            logger.info(f"   Status: {entry_window.status.value}")
            logger.info(f"   {entry_window.reason}")
            if entry_window.target_entry_price:
                logger.info(f"   Target Price: ${entry_window.target_entry_price:.2f}")
            if entry_window.current_price:
                logger.info(f"   Current Price: ${entry_window.current_price:.2f}")
            if entry_window.stop_loss_price:
                logger.info(f"   Stop Loss: ${entry_window.stop_loss_price:.2f}")
            
            # === STEP 4: POSITION SIZING ===
            if entry_window.status in [EntryStatus.ENTER_NOW, EntryStatus.LIMIT_ORDER]:
                position_size = self.capital * 0.10 * entry_window.recommended_allocation_multiplier
                
                logger.info(f"\n💰 POSITION SIZING:")
                logger.info(f"   Recommended Size: ${position_size:,.0f}")
                logger.info(f"   Allocation: {(position_size/self.capital)*100:.1f}% of capital")
                
                # === STEP 5: GENERATE TRADING SIGNAL ===
                self._generate_trading_signal(
                    ticker=ticker,
                    catalyst_type=catalyst_type,
                    entry_window=entry_window,
                    validation=validation,
                    position_size=position_size
                )
            
            logger.info(f"{'='*80}\n")
        
        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}")
    
    def _analyze_catalyst(self, ticker: str, article: NewsArticle) -> tuple:
        """
        Analyze news to determine catalyst type and direction
        Returns: (catalyst_type, direction, confidence)
        """
        headline = article.headline.lower()
        
        # Use institutional engine if available
        if HAS_INSTITUTIONAL and self.catalyst_engine:
            try:
                signals = self.catalyst_engine.analyze(
                    ticker=ticker,
                    headlines=[article.headline],
                    current_price=0,  # Will fetch internally
                    market_cap=0,
                    revenue=0,
                    current_volume=0,
                    avg_volume=0,
                    sector=""
                )
                
                if signals:
                    signal = signals[0]
                    return (
                        signal.catalyst_type,
                        "POSITIVE" if signal.action == "BUY" else "NEGATIVE",
                        signal.confidence
                    )
            except Exception as e:
                logger.debug(f"Institutional engine error: {e}")
        
        # Fallback: Simple keyword detection
        positive_keywords = [
            'beats', 'exceeds', 'approval', 'approved', 'contract', 'deal',
            'partnership', 'acquisition', 'upgrade', 'raises guidance',
            'strong results', 'record', 'launched'
        ]
        
        negative_keywords = [
            'misses', 'below', 'rejection', 'delay', 'lawsuit', 'fine',
            'downgrade', 'lowers guidance', 'weak', 'disappoints', 'loss'
        ]
        
        # Check for positive catalysts
        for keyword in positive_keywords:
            if keyword in headline:
                return ("POSITIVE_CATALYST", "POSITIVE", 0.65)
        
        # Check for negative catalysts
        for keyword in negative_keywords:
            if keyword in headline:
                return ("NEGATIVE_CATALYST", "NEGATIVE", 0.65)
        
        # No clear catalyst
        return (None, None, 0.0)
    
    def _generate_trading_signal(self, ticker: str, catalyst_type: str,
                                 entry_window: EntryWindow, validation: ValidationResult,
                                 position_size: float):
        """
        Generate final trading recommendation
        """
        signal = {
            'ticker': ticker,
            'timestamp': datetime.now(),
            'catalyst_type': catalyst_type,
            'entry_status': entry_window.status.value,
            'target_price': entry_window.target_entry_price,
            'stop_loss': entry_window.stop_loss_price,
            'position_size': position_size,
            'confidence': validation.confidence_score,
            'reason': entry_window.reason
        }
        
        # Store signal
        self.active_signals[ticker] = signal
        
        # Display recommendation
        print("\n" + "="*80)
        print("🎯 TRADING SIGNAL GENERATED")
        print("="*80)
        print(f"Ticker: {ticker}")
        print(f"Action: {entry_window.status.value}")
        print(f"Catalyst: {catalyst_type}")
        print(f"Target Entry: ${entry_window.target_entry_price:.2f}")
        if entry_window.stop_loss_price:
            print(f"Stop Loss: ${entry_window.stop_loss_price:.2f}")
        print(f"Position Size: ${position_size:,.0f}")
        print(f"Confidence: {validation.confidence_score:.0%}")
        print(f"Rationale: {entry_window.reason}")
        print("="*80 + "\n")
        
        # Here you could:
        # - Send alert to user (email, SMS, push notification)
        # - Auto-execute trade (if enabled and confidence high enough)
        # - Add to watchlist
        # - Update dashboard


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Configuration
    TICKERS_TO_MONITOR = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA',  # Mega caps
        'TSLA', 'META', 'AMD', 'NFLX', 'CRM',     # Large caps
        # Add more tickers as needed
    ]
    
    CAPITAL = 10000.0  # $10K trading capital
    
    # Optional: Add your API keys here
    FINNHUB_KEY = None  # Get free key at https://finnhub.io/register
    ALPHAVANTAGE_KEY = None  # Get free key at https://www.alphavantage.co/support/#api-key
    
    # Initialize system
    system = RealtimeCatalystTradingSystem(
        tickers=TICKERS_TO_MONITOR,
        capital=CAPITAL,
        finnhub_key=FINNHUB_KEY,
        alphavantage_key=ALPHAVANTAGE_KEY
    )
    
    # Start monitoring
    try:
        system.start()
        
        # Run indefinitely (Ctrl+C to stop)
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Shutting down system...")
        system.stop()
        print("✅ System stopped cleanly\n")
