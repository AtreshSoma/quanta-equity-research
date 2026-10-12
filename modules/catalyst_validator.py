#!/usr/bin/env python3
"""
Catalyst Validator - Volume & Price Coherence Validation
Validates that market action confirms the catalyst before trading

Key Validations:
1. Volume Surge Detection (is volume spiking?)
2. Price Action Coherence (is price moving as expected?)
3. Multi-Source Confirmation (cross-check across news sources)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
import yfinance as yf
import pandas as pd

logger = logging.getLogger("QUANTA.CatalystValidator")


@dataclass
class ValidationResult:
    """Result of catalyst validation"""
    is_valid: bool
    confidence_score: float  # 0.0 to 1.0
    volume_score: float
    price_score: float
    direction_match: bool
    validation_details: Dict[str, any]
    
    def __str__(self):
        return (f"Valid: {self.is_valid}, Confidence: {self.confidence_score:.0%}, "
                f"Volume: {self.volume_score:.0%}, Price: {self.price_score:.0%}")


class CatalystValidator:
    """
    Validate catalysts using volume and price action confirmation
    Professional standard: Never trade single-source headlines without market confirmation
    """
    
    # Thresholds
    MIN_VOLUME_SURGE = 1.5      # Volume must be >1.5× average
    STRONG_VOLUME_SURGE = 2.5   # >2.5× for high confidence
    MIN_PRICE_MOVE = 0.005      # 0.5% minimum price movement
    COHERENCE_WINDOW_MIN = 30   # Check 30 min after catalyst
    
    def __init__(self):
        self.price_cache = {}  # Cache intraday data to reduce API calls
    
    def validate_catalyst(self, 
                         ticker: str,
                         catalyst_direction: str,  # "POSITIVE" or "NEGATIVE"
                         catalyst_time: datetime,
                         current_price: Optional[float] = None) -> ValidationResult:
        """
        Validate catalyst by checking if volume and price confirm the signal
        
        Args:
            ticker: Stock symbol
            catalyst_direction: Expected direction ("POSITIVE" or "NEGATIVE")
            catalyst_time: When catalyst was detected
            current_price: Optional current price (to avoid extra API call)
        
        Returns:
            ValidationResult with confidence scores
        """
        try:
            # Calculate time since catalyst
            time_since_catalyst = (datetime.now() - catalyst_time).total_seconds() / 60
            
            # Need some time for market to react (but not too much)
            if time_since_catalyst < 2:
                logger.debug(f"{ticker}: Too soon to validate ({time_since_catalyst:.0f}m), waiting")
                return ValidationResult(
                    is_valid=False,
                    confidence_score=0.5,
                    volume_score=0.0,
                    price_score=0.0,
                    direction_match=False,
                    validation_details={"reason": "TOO_SOON"}
                )
            
            # Get intraday data
            intraday_data = self._fetch_intraday_data(ticker)
            if intraday_data is None or intraday_data.empty:
                logger.warning(f"{ticker}: No intraday data available for validation")
                return ValidationResult(
                    is_valid=False,
                    confidence_score=0.3,
                    volume_score=0.0,
                    price_score=0.0,
                    direction_match=False,
                    validation_details={"reason": "NO_DATA"}
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
            
            # Split data: before vs after catalyst
            pre_catalyst = intraday_data_naive[intraday_data_naive.index < catalyst_time_naive]
            post_catalyst = intraday_data_naive[intraday_data_naive.index >= catalyst_time_naive]
            
            if len(pre_catalyst) < 10 or len(post_catalyst) < 2:
                logger.debug(f"{ticker}: Insufficient data for validation")
                return ValidationResult(
                    is_valid=False,
                    confidence_score=0.4,
                    volume_score=0.0,
                    price_score=0.0,
                    direction_match=False,
                    validation_details={"reason": "INSUFFICIENT_DATA"}
                )
            
            # === VOLUME VALIDATION ===
            volume_score, volume_surge = self._validate_volume(pre_catalyst, post_catalyst)
            
            # === PRICE VALIDATION ===
            price_score, price_change, direction_match = self._validate_price(
                pre_catalyst, post_catalyst, catalyst_direction
            )
            
            # === COMBINED CONFIDENCE SCORE ===
            # Weighted: Volume 40%, Price 40%, Direction 20%
            confidence = (volume_score * 0.40) + (price_score * 0.40)
            if direction_match:
                confidence += 0.20
            
            # Validation passes if confidence >60%
            is_valid = confidence >= 0.60
            
            validation_details = {
                "volume_surge": volume_surge,
                "price_change_pct": price_change,
                "direction_match": direction_match,
                "time_since_catalyst_min": time_since_catalyst,
                "pre_catalyst_bars": len(pre_catalyst),
                "post_catalyst_bars": len(post_catalyst)
            }
            
            result = ValidationResult(
                is_valid=is_valid,
                confidence_score=confidence,
                volume_score=volume_score,
                price_score=price_score,
                direction_match=direction_match,
                validation_details=validation_details
            )
            
            logger.info(f"{ticker}: Validation - {result}")
            return result
        
        except Exception as e:
            logger.error(f"{ticker}: Validation error - {e}")
            return ValidationResult(
                is_valid=False,
                confidence_score=0.0,
                volume_score=0.0,
                price_score=0.0,
                direction_match=False,
                validation_details={"error": str(e)}
            )
    
    def _fetch_intraday_data(self, ticker: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        """
        Fetch intraday data (5-minute bars)
        Cache to reduce API calls
        """
        cache_key = f"{ticker}_{interval}_{datetime.now().strftime('%Y%m%d_%H')}"
        
        if cache_key in self.price_cache:
            return self.price_cache[cache_key]
        
        try:
            stock = yf.Ticker(ticker)
            # Get last 2 days of 5-minute data
            hist = stock.history(period="2d", interval=interval)
            
            if not hist.empty:
                self.price_cache[cache_key] = hist
                return hist
        
        except Exception as e:
            logger.debug(f"Intraday fetch error for {ticker}: {e}")
        
        return None
    
    def _validate_volume(self, pre_catalyst: pd.DataFrame, 
                        post_catalyst: pd.DataFrame) -> Tuple[float, float]:
        """
        Check if volume surged after catalyst
        Returns: (score 0-1, surge_ratio)
        """
        try:
            # Average volume before catalyst
            avg_vol_pre = pre_catalyst['Volume'].mean()
            
            # Average volume after catalyst (last 30 minutes ~ 6 bars of 5-min data)
            avg_vol_post = post_catalyst.tail(6)['Volume'].mean()
            
            if avg_vol_pre == 0:
                return 0.5, 1.0  # Neutral if no baseline
            
            volume_surge = avg_vol_post / avg_vol_pre
            
            # Scoring
            if volume_surge >= self.STRONG_VOLUME_SURGE:
                score = 1.0  # Excellent confirmation
            elif volume_surge >= self.MIN_VOLUME_SURGE:
                score = 0.75  # Good confirmation
            elif volume_surge >= 1.2:
                score = 0.50  # Moderate
            else:
                score = 0.25  # Weak volume response
            
            logger.debug(f"Volume validation: surge={volume_surge:.1f}×, score={score:.0%}")
            return score, volume_surge
        
        except Exception as e:
            logger.debug(f"Volume validation error: {e}")
            return 0.5, 1.0
    
    def _validate_price(self, pre_catalyst: pd.DataFrame, 
                       post_catalyst: pd.DataFrame,
                       expected_direction: str) -> Tuple[float, float, bool]:
        """
        Check if price moved in expected direction
        Returns: (score 0-1, price_change_pct, direction_match)
        """
        try:
            # Price before catalyst (last bar)
            price_before = pre_catalyst['Close'].iloc[-1]
            
            # Price after catalyst (latest)
            price_after = post_catalyst['Close'].iloc[-1]
            
            if price_before == 0:
                return 0.5, 0.0, False
            
            price_change_pct = (price_after / price_before) - 1
            
            # Check direction match
            if expected_direction == "POSITIVE":
                direction_match = price_change_pct > 0
            elif expected_direction == "NEGATIVE":
                direction_match = price_change_pct < 0
            else:
                direction_match = False  # Unknown direction
            
            # Scoring based on magnitude
            abs_move = abs(price_change_pct)
            
            if abs_move >= 0.03:  # >3% move
                magnitude_score = 1.0
            elif abs_move >= 0.015:  # 1.5-3%
                magnitude_score = 0.85
            elif abs_move >= 0.01:  # 1-1.5%
                magnitude_score = 0.70
            elif abs_move >= self.MIN_PRICE_MOVE:  # 0.5-1%
                magnitude_score = 0.55
            else:
                magnitude_score = 0.30  # Minimal movement
            
            # Penalize if direction doesn't match
            if not direction_match:
                magnitude_score *= 0.5
            
            logger.debug(
                f"Price validation: change={price_change_pct:+.2%}, "
                f"expected={expected_direction}, match={direction_match}, score={magnitude_score:.0%}"
            )
            
            return magnitude_score, price_change_pct, direction_match
        
        except Exception as e:
            logger.debug(f"Price validation error: {e}")
            return 0.5, 0.0, False
    
    def validate_with_sources(self, ticker: str, headlines: list, 
                             catalyst_type: str) -> Tuple[bool, float, int]:
        """
        Multi-source validation: Check if multiple sources report the same catalyst
        
        Returns:
            (is_validated, confidence, source_count)
        """
        unique_sources = set()
        
        # Extract sources from headlines (if available)
        for headline in headlines:
            # Simple heuristic: look for common source indicators
            headline_lower = headline.lower()
            
            if 'reuters' in headline_lower or 'reuters.com' in headline_lower:
                unique_sources.add('Reuters')
            elif 'bloomberg' in headline_lower:
                unique_sources.add('Bloomberg')
            elif 'cnbc' in headline_lower:
                unique_sources.add('CNBC')
            elif 'marketwatch' in headline_lower:
                unique_sources.add('MarketWatch')
            elif 'seeking alpha' in headline_lower:
                unique_sources.add('SeekingAlpha')
            elif 'barron' in headline_lower:
                unique_sources.add('Barrons')
            else:
                unique_sources.add('Generic')
        
        source_count = len(unique_sources)
        
        # Confidence based on source count
        if source_count >= 3:
            confidence = 0.95  # Strong validation
            is_validated = True
        elif source_count == 2:
            confidence = 0.75  # Moderate validation
            is_validated = True
        else:
            confidence = 0.45  # Weak (single source)
            is_validated = False
        
        # Boost for Tier-1 sources
        tier1_sources = {'Reuters', 'Bloomberg', 'CNBC'}
        if unique_sources & tier1_sources:
            confidence = min(1.0, confidence * 1.1)
        
        logger.info(
            f"{ticker}: Multi-source validation - {source_count} sources, "
            f"confidence={confidence:.0%}, validated={is_validated}"
        )
        
        return is_validated, confidence, source_count


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
    )
    
    validator = CatalystValidator()
    
    # Example: Validate a catalyst from 10 minutes ago
    catalyst_time = datetime.now() - timedelta(minutes=10)
    
    result = validator.validate_catalyst(
        ticker="AAPL",
        catalyst_direction="POSITIVE",
        catalyst_time=catalyst_time
    )
    
    print("\n" + "="*80)
    print("CATALYST VALIDATION RESULT")
    print("="*80)
    print(f"Valid: {result.is_valid}")
    print(f"Overall Confidence: {result.confidence_score:.0%}")
    print(f"Volume Score: {result.volume_score:.0%}")
    print(f"Price Score: {result.price_score:.0%}")
    print(f"Direction Match: {result.direction_match}")
    print(f"\nDetails: {result.validation_details}")
    print("="*80)
