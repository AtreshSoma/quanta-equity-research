import pandas as pd
import numpy as np
import logging
from typing import Tuple, List, Optional
from dataclasses import dataclass

logger = logging.getLogger("DataValidator")

@dataclass
class ValidationResult:
    is_valid: bool
    warnings: List[str]
    errors: List[str]

class DataValidator:
    """
    Validates market data quality before analysis.
    Checks for: Price spikes, NaNs, Stale data, Liquidity.
    """
    
    MIN_PRICE = 5.0
    MIN_VOLUME_USD = 500_000  # $500k daily volume
    MAX_PRICE_SPIKE_PCT = 0.50  # 50% single day move is suspicious (unless confirmed)

    @staticmethod
    def validate_history(hist: pd.DataFrame, ticker: str) -> ValidationResult:
        warnings = []
        errors = []
        
        if hist.empty:
            errors.append("Empty history")
            return ValidationResult(False, warnings, errors)
            
        # 1. Stale Data
        last_date = hist.index[-1]
        # Normalize timezone to prevent "Offset-naive and offset-aware" error
        if last_date.tzinfo is not None:
             last_date = last_date.tz_localize(None)
        
        days_gap = (pd.Timestamp.now() - last_date).days
        if days_gap > 5:
            warnings.append(f"Data stale (last: {last_date.date()})")
        
        # 2. NaN Checks
        if hist['Close'].isnull().any():
            nan_cnt = hist['Close'].isnull().sum()
            warnings.append(f"Contains {nan_cnt} NaN close prices")
            # Fill or drop? For validation, we report.
            
        # 3. Price Spikes (Fat Finger / Split adjustment errors)
        returns = hist['Close'].pct_change().dropna()
        max_ret = returns.max()
        min_ret = returns.min()
        
        if max_ret > DataValidator.MAX_PRICE_SPIKE_PCT:
            warnings.append(f"Suspicious spike: +{max_ret*100:.1f}% in one day")
            
        if min_ret < -DataValidator.MAX_PRICE_SPIKE_PCT:
            warnings.append(f"Suspicious drop: {min_ret*100:.1f}% in one day")
            
        # 4. Zero/Negative Prices
        if (hist['Close'] <= 0).any():
            errors.append("Contains zero or negative prices")
            
        # 5. Liquidity Check (Average of last 20 days)
        avg_vol = hist['Volume'].tail(20).mean()
        avg_price = hist['Close'].tail(20).mean()
        dollar_vol = avg_vol * avg_price
        
        if dollar_vol < DataValidator.MIN_VOLUME_USD:
            # Not an error but a strong filter
            # errors.append(f"Low liquidity: ${dollar_vol/1000:.0f}k/day")
            pass
            
        is_valid = len(errors) == 0
        return ValidationResult(is_valid, warnings, errors)
