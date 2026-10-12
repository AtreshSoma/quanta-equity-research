#!/usr/bin/env python3
"""
Intraday Entry Optimizer - Avoid Chasing Momentum Spikes
Determines optimal entry windows after catalyst detection

Key Features:
1. Gap-up detection (reject if spiked >5%)
2. Pullback waiting logic (wait for 2-3% dip from highs)
3. Entry window recommendations (ENTER_NOW, WAIT, LIMIT_ORDER)
4. Cascading limit order suggestions
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import yfinance as yf
import pandas as pd

logger = logging.getLogger("QUANTA.EntryOptimizer")


class EntryStatus(Enum):
    """Entry recommendation status"""
    ENTER_NOW = "ENTER_NOW"          # Immediate market order
    LIMIT_ORDER = "LIMIT_ORDER"      # Place limit order at target price
    WAIT = "WAIT"                    # Wait for pullback
    TOO_LATE = "TOO_LATE"           # Catalyst already priced in
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class EntryWindow:
    """Entry opportunity window"""
    status: EntryStatus
    reason: str
    target_entry_price: Optional[float] = None
    current_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    expiration_time: Optional[datetime] = None
    
    # Additional metrics
    spike_from_catalyst_pct: Optional[float] = None
    distance_from_high_pct: Optional[float] = None
    recommended_allocation_multiplier: float = 1.0  # Adjust position size
    
    def __str__(self):
        if self.status == EntryStatus.ENTER_NOW:
            return f"ENTER NOW at ${self.target_entry_price:.2f} - {self.reason}"
        elif self.status == EntryStatus.LIMIT_ORDER:
            return f"LIMIT ORDER at ${self.target_entry_price:.2f} - {self.reason}"
        elif self.status == EntryStatus.WAIT:
            return f"WAIT for ${self.target_entry_price:.2f} - {self.reason}"
        else:
            return f"{self.status.value} - {self.reason}"


@dataclass
class CascadingOrder:
    """Recommendation for multi-level limit orders"""
    price_levels: List[Tuple[float, float]]  # [(price, allocation_pct), ...]
    total_position_size: float
    expires_at: datetime
    
    def __str__(self):
        levels_str = "\n".join([
            f"  Level {i+1}: ${price:.2f} ({alloc*100:.0f}% of position)"
            for i, (price, alloc) in enumerate(self.price_levels)
        ])
        return f"Cascading Order (${self.total_position_size:,.0f}):\n{levels_str}"


class EntryOptimizer:
    """
    Determine optimal entry timing after catalyst
    Professional approach: NEVER chase spikes, wait for pullbacks
    """
    
    # Thresholds
    SPIKE_THRESHOLD = 0.05      # 5% spike = WAIT
    MODERATE_MOVE = 0.02        # 2-5% = LIMIT ORDER
    MINIMAL_MOVE = 0.01         # <2% = ENTER NOW
    
    PULLBACK_TARGET = 0.02      # Wait for 2% pullback from spike
    MAX_CATALYST_AGE_HOURS = 4  # After 4 hours, catalyst likely priced in
    
    def __init__(self):
        self.price_cache = {}
    
    def calculate_entry_window(self, 
                               ticker: str,
                               catalyst_time: datetime,
                               catalyst_direction: str = "POSITIVE",
                               position_size_usd: float = 1000.0) -> EntryWindow:
        """
        Analyze post-catalyst price action and recommend entry strategy
        
        Args:
            ticker: Stock symbol
            catalyst_time: When catalyst was detected
            catalyst_direction: POSITIVE or NEGATIVE
            position_size_usd: Intended position size
        
        Returns:
            EntryWindow with recommendation
        """
        try:
            # Calculate catalyst age
            time_since_catalyst = (datetime.now() - catalyst_time).total_seconds() / 3600  # hours
            
            # Too old? Likely already priced in
            if time_since_catalyst > self.MAX_CATALYST_AGE_HOURS:
                return EntryWindow(
                    status=EntryStatus.TOO_LATE,
                    reason=f"Catalyst is {time_since_catalyst:.1f} hours old - likely priced in",
                    current_price=None
                )
            
            # Get intraday data
            intraday_data = self._fetch_intraday_data(ticker)
            if intraday_data is None or intraday_data.empty:
                return EntryWindow(
                    status=EntryStatus.INSUFFICIENT_DATA,
                    reason="No intraday data available",
                    current_price=None
                )
            
            # Normalize timezones for comparison
            # Convert both intraday_data index and catalyst_time to timezone-naive
            if hasattr(intraday_data.index, 'tz') and intraday_data.index.tz is not None:
                intraday_data_naive = intraday_data.copy()
                intraday_data_naive.index = intraday_data.index.tz_localize(None)
            else:
                intraday_data_naive = intraday_data
            
            # Ensure catalyst_time is timezone-naive
            if hasattr(catalyst_time, 'tzinfo') and catalyst_time.tzinfo is not None:
                catalyst_time_naive = catalyst_time.replace(tzinfo=None)
            else:
                catalyst_time_naive = catalyst_time
            
            # Split: before vs after catalyst
            pre_catalyst = intraday_data_naive[intraday_data_naive.index < catalyst_time_naive]
            post_catalyst = intraday_data_naive[intraday_data_naive.index >= catalyst_time_naive]
            
            if len(pre_catalyst) == 0 or len(post_catalyst) == 0:
                return EntryWindow(
                    status=EntryStatus.INSUFFICIENT_DATA,
                    reason="Insufficient price data around catalyst time",
                    current_price=None
                )
            
            # Calculate metrics
            price_before = pre_catalyst['Close'].iloc[-1]
            price_current = post_catalyst['Close'].iloc[-1]
            high_since_catalyst = post_catalyst['High'].max()
            low_since_catalyst = post_catalyst['Low'].min()
            
            # Price movement from catalyst
            spike_pct = (price_current / price_before - 1) if price_before > 0 else 0
            distance_from_high = (price_current / high_since_catalyst - 1) if high_since_catalyst > 0 else 0
            
            logger.info(
                f"{ticker}: Catalyst analysis - "
                f"Price before: ${price_before:.2f}, Current: ${price_current:.2f}, "
                f"Spike: {spike_pct:+.2%}, Distance from high: {distance_from_high:+.2%}"
            )
            
            # Decision logic (for POSITIVE catalysts)
            if catalyst_direction == "POSITIVE":
                return self._positive_catalyst_logic(
                    ticker, price_before, price_current, high_since_catalyst,
                    spike_pct, distance_from_high, catalyst_time, position_size_usd
                )
            else:
                # For NEGATIVE catalysts (shorting opportunities)
                return self._negative_catalyst_logic(
                    ticker, price_before, price_current, low_since_catalyst,
                    spike_pct, catalyst_time
                )
        
        except Exception as e:
            logger.error(f"{ticker}: Entry optimization error - {e}")
            return EntryWindow(
                status=EntryStatus.INSUFFICIENT_DATA,
                reason=f"Error: {str(e)}",
                current_price=None
            )
    
    def _positive_catalyst_logic(self, ticker: str, price_before: float,
                                price_current: float, high_since: float,
                                spike_pct: float, distance_from_high: float,
                                catalyst_time: datetime, position_size: float) -> EntryWindow:
        """
        Entry logic for POSITIVE catalysts (buying opportunities)
        """
        
        # === CASE 1: MAJOR SPIKE (>5%) - WAIT FOR PULLBACK ===
        if spike_pct > self.SPIKE_THRESHOLD:
            target_price = price_before * 1.02  # Wait for 2% above pre-catalyst
            
            return EntryWindow(
                status=EntryStatus.WAIT,
                reason=f"Stock spiked {spike_pct:+.1%} from catalyst - wait for pullback",
                target_entry_price=target_price,
                current_price=price_current,
                spike_from_catalyst_pct=spike_pct,
                distance_from_high_pct=distance_from_high,
                expiration_time=catalyst_time + timedelta(hours=4),
                recommended_allocation_multiplier=0.7  # Reduce size if late
            )
        
        # === CASE 2: MODERATE MOVE (2-5%) - LIMIT ORDER ON DIP ===
        elif spike_pct > self.MODERATE_MOVE:
            # Enter on next 0.5-1% pullback
            target_price = price_current * 0.995  # 0.5% below current
            stop_loss = target_price * 0.97  # 3% stop
            
            return EntryWindow(
                status=EntryStatus.LIMIT_ORDER,
                reason=f"Moderate move {spike_pct:+.1%} - enter on small pullback",
                target_entry_price=target_price,
                current_price=price_current,
                stop_loss_price=stop_loss,
                spike_from_catalyst_pct=spike_pct,
                distance_from_high_pct=distance_from_high,
                expiration_time=catalyst_time + timedelta(hours=2),
                recommended_allocation_multiplier=0.9
            )
        
        # === CASE 3: MINIMAL MOVE (<2%) - ENTER NOW ===
        else:
            # Catalyst not yet priced in - optimal entry
            target_price = price_current * 1.001  # Slight above for quick fill
            stop_loss = price_before * 0.98  # Below pre-catalyst level
            
            return EntryWindow(
                status=EntryStatus.ENTER_NOW,
                reason=f"Minimal price impact {spike_pct:+.1%} - optimal entry window",
                target_entry_price=target_price,
                current_price=price_current,
                stop_loss_price=stop_loss,
                spike_from_catalyst_pct=spike_pct,
                distance_from_high_pct=distance_from_high,
                expiration_time=catalyst_time + timedelta(minutes=30),
                recommended_allocation_multiplier=1.0  # Full size
            )
    
    def _negative_catalyst_logic(self, ticker: str, price_before: float,
                                 price_current: float, low_since: float,
                                 drop_pct: float, catalyst_time: datetime) -> EntryWindow:
        """
        Entry logic for NEGATIVE catalysts (shorting or avoiding)
        """
        # For most retail traders: just avoid negative catalysts
        # For advanced: shorting opportunities
        
        if drop_pct < -0.05:  # Dropped >5%
            return EntryWindow(
                status=EntryStatus.WAIT,
                reason=f"Stock dropped {drop_pct:.1%} - wait for bounce to short or avoid entirely",
                target_entry_price=price_current * 1.02,  # Short on bounce
                current_price=price_current,
                expiration_time=catalyst_time + timedelta(hours=4)
            )
        else:
            return EntryWindow(
                status=EntryStatus.WAIT,
                reason="NEGATIVE catalyst detected - AVOID this stock",
                current_price=price_current
            )
    
    def generate_cascading_orders(self, entry_window: EntryWindow,
                                  position_size_usd: float) -> Optional[CascadingOrder]:
        """
        Generate multi-level limit orders for gradual entry
        Improves average entry price and reduces adverse selection
        """
        if entry_window.status == EntryStatus.ENTER_NOW:
            # Single market order
            return None
        
        elif entry_window.status == EntryStatus.LIMIT_ORDER:
            target_price = entry_window.target_entry_price
            
            # 3 levels: target, -0.2%, -0.5%
            levels = [
                (target_price * 1.000, 0.40),  # 40% at target
                (target_price * 0.998, 0.35),  # 35% at -0.2%
                (target_price * 0.995, 0.25),  # 25% at -0.5%
            ]
            
            return CascadingOrder(
                price_levels=levels,
                total_position_size=position_size_usd,
                expires_at=entry_window.expiration_time or (datetime.now() + timedelta(hours=2))
            )
        
        elif entry_window.status == EntryStatus.WAIT:
            target_price = entry_window.target_entry_price
            
            # If waiting for pullback, set deeper levels
            levels = [
                (target_price * 1.000, 0.30),  # 30% at target
                (target_price * 0.990, 0.40),  # 40% at -1%
                (target_price * 0.980, 0.30),  # 30% at -2%
            ]
            
            return CascadingOrder(
                price_levels=levels,
                total_position_size=position_size_usd,
                expires_at=entry_window.expiration_time or (datetime.now() + timedelta(hours=4))
            )
        
        return None
    
    def _fetch_intraday_data(self, ticker: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        """Fetch intraday data (cached)"""
        cache_key = f"{ticker}_{interval}_{datetime.now().strftime('%Y%m%d_%H')}"
        
        if cache_key in self.price_cache:
            return self.price_cache[cache_key]
        
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="2d", interval=interval)
            
            if not hist.empty:
                self.price_cache[cache_key] = hist
                return hist
        except Exception as e:
            logger.debug(f"Intraday fetch error for {ticker}: {e}")
        
        return None


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
    )
    
    optimizer = EntryOptimizer()
    
    # Example: Catalyst detected 30 minutes ago
    catalyst_time = datetime.now() - timedelta(minutes=30)
    
    entry_window = optimizer.calculate_entry_window(
        ticker="AAPL",
        catalyst_time=catalyst_time,
        catalyst_direction="POSITIVE",
        position_size_usd=2500.0
    )
    
    print("\n" + "="*80)
    print("ENTRY OPTIMIZATION RESULT")
    print("="*80)
    print(entry_window)
    
    if entry_window.status in [EntryStatus.LIMIT_ORDER, EntryStatus.WAIT]:
        cascading = optimizer.generate_cascading_orders(entry_window, 2500.0)
        if cascading:
            print("\n" + cascading.__str__())
    
    print("="*80)
