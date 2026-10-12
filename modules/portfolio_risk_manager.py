"""
Portfolio Risk Manager Module

Institutional-grade portfolio-level risk management including:
- Correlation-adjusted Value at Risk (VaR)
- Sector concentration limits
- Correlated position enforcement
- Drawdown-based exposure reduction

Author: Quanta Elite System
Date: 2025-12-29
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import logging

try:
    from scipy.stats import norm
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    norm = None

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """Portfolio risk limits configuration"""
    max_sector_concentration: float = 0.30      # 30% max in any sector
    max_single_position: float = 0.10           # 10% max single position
    max_correlated_positions: int = 3           # Max 3 stocks with >0.7 correlation
    correlation_threshold: float = 0.70         # Correlation threshold
    max_portfolio_var: float = 0.05             # 5% daily VaR limit
    max_drawdown_threshold: float = 0.15        # 15% drawdown triggers reduction
    var_confidence: float = 0.95                # 95% confidence for VaR


@dataclass 
class PortfolioRiskMetrics:
    """Portfolio-level risk metrics"""
    portfolio_var: float = 0.0
    sector_concentrations: Dict[str, float] = field(default_factory=dict)
    correlated_pairs: List[Tuple[str, str, float]] = field(default_factory=list)
    current_drawdown: float = 0.0
    risk_utilization: float = 0.0  # % of risk budget used
    is_within_limits: bool = True
    violations: List[str] = field(default_factory=list)


class PortfolioRiskManager:
    """
    Portfolio-level risk management with VaR and correlation enforcement.
    
    KEY FUNCTIONS:
    1. check_position_limits() - Verify new position doesn't breach limits
    2. calculate_portfolio_var() - Correlation-adjusted VaR
    3. get_sector_concentration() - Sector exposure analysis
    4. check_correlation_cluster() - Correlated position detection
    5. calculate_exposure_adjustment() - Drawdown-based sizing
    """
    
    def __init__(self, limits: RiskLimits = None):
        self.limits = limits or RiskLimits()
        self._correlation_cache: Dict[Tuple[str, str], float] = {}
        self._returns_cache: Dict[str, pd.Series] = {}
    
    def check_position_limits(
        self,
        new_position: Dict,
        existing_positions: List[Dict],
        portfolio_value: float = 100000
    ) -> Tuple[bool, str]:
        """
        Check if a new position would breach portfolio limits.
        
        Args:
            new_position: Dict with 'ticker', 'value', 'sector'
            existing_positions: List of existing position dicts
            portfolio_value: Total portfolio value
            
        Returns:
            Tuple of (is_allowed, rejection_reason)
        """
        ticker = new_position.get('ticker', 'UNKNOWN')
        position_value = new_position.get('value', 0)
        sector = new_position.get('sector', 'Unknown')
        
        # Check 1: Single position size limit
        position_pct = position_value / portfolio_value if portfolio_value > 0 else 0
        if position_pct > self.limits.max_single_position:
            return False, f"Position {position_pct:.0%} exceeds max {self.limits.max_single_position:.0%}"
        
        # Check 2: Sector concentration
        sector_total = position_value
        for pos in existing_positions:
            if pos.get('sector') == sector:
                sector_total += pos.get('value', 0)
        
        sector_pct = sector_total / portfolio_value if portfolio_value > 0 else 0
        if sector_pct > self.limits.max_sector_concentration:
            return False, f"Sector {sector} at {sector_pct:.0%} exceeds limit {self.limits.max_sector_concentration:.0%}"
        
        # Check 3: Correlated positions
        correlated_count = 0
        for pos in existing_positions:
            existing_ticker = pos.get('ticker', '')
            correlation = self._get_correlation(ticker, existing_ticker)
            if correlation > self.limits.correlation_threshold:
                correlated_count += 1
        
        if correlated_count >= self.limits.max_correlated_positions:
            return False, f"Already {correlated_count} positions with correlation > {self.limits.correlation_threshold:.0%}"
        
        return True, ""
    
    def calculate_portfolio_var(
        self,
        positions: List[Dict],
        lookback_days: int = 252,
        confidence: float = None
    ) -> float:
        """
        Calculate correlation-adjusted portfolio Value at Risk.
        
        Uses variance-covariance method with historical correlations.
        
        Args:
            positions: List of {'ticker': str, 'value': float}
            lookback_days: Historical period for correlation/volatility
            confidence: VaR confidence level (default 95%)
            
        Returns:
            Portfolio VaR as decimal (e.g., 0.025 = 2.5%)
        """
        if not positions or not HAS_SCIPY:
            return 0.0
        
        confidence = confidence or self.limits.var_confidence
        
        try:
            tickers = [p['ticker'] for p in positions]
            weights = np.array([p['value'] for p in positions])
            weights = weights / weights.sum()  # Normalize to portfolio weights
            
            # Get historical returns
            returns_data = []
            for ticker in tickers:
                returns = self._get_historical_returns(ticker, lookback_days)
                if returns is not None and len(returns) > 20:
                    returns_data.append(returns)
                else:
                    # Fallback: assume 25% annualized volatility
                    synthetic = pd.Series(np.random.normal(0, 0.25/np.sqrt(252), lookback_days))
                    returns_data.append(synthetic)
            
            # Build correlation matrix
            returns_df = pd.concat(returns_data, axis=1)
            returns_df.columns = tickers
            
            cov_matrix = returns_df.cov() * 252  # Annualized
            
            # Portfolio variance
            portfolio_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
            portfolio_std = np.sqrt(portfolio_variance)
            
            # VaR using normal distribution
            z_score = norm.ppf(confidence)
            daily_var = portfolio_std / np.sqrt(252) * z_score
            
            return float(daily_var)
            
        except Exception as e:
            logger.debug(f"VaR calculation failed: {e}")
            return 0.0
    
    def get_sector_concentration(self, positions: List[Dict]) -> Dict[str, float]:
        """Calculate sector concentration percentages."""
        total_value = sum(p.get('value', 0) for p in positions)
        if total_value <= 0:
            return {}
        
        sector_values = {}
        for pos in positions:
            sector = pos.get('sector', 'Unknown')
            sector_values[sector] = sector_values.get(sector, 0) + pos.get('value', 0)
        
        return {s: v / total_value for s, v in sector_values.items()}
    
    def check_correlation_cluster(
        self,
        positions: List[Dict]
    ) -> List[Tuple[str, str, float]]:
        """Find pairs of positions with high correlation."""
        correlated_pairs = []
        tickers = [p['ticker'] for p in positions]
        
        for i, t1 in enumerate(tickers):
            for t2 in tickers[i+1:]:
                corr = self._get_correlation(t1, t2)
                if corr > self.limits.correlation_threshold:
                    correlated_pairs.append((t1, t2, corr))
        
        return correlated_pairs
    
    def calculate_exposure_adjustment(
        self,
        current_drawdown: float,
        base_position_size: float
    ) -> float:
        """
        Calculate position size adjustment based on drawdown.
        
        Uses tiered reduction:
        - 0-10% drawdown: 100% position
        - 10-15% drawdown: 75% position  
        - 15-20% drawdown: 50% position
        - >20% drawdown: 25% position
        
        Args:
            current_drawdown: Current portfolio drawdown (positive value)
            base_position_size: Normal position size
            
        Returns:
            Adjusted position size
        """
        drawdown = abs(current_drawdown)
        
        if drawdown > 0.20:
            multiplier = 0.25
        elif drawdown > 0.15:
            multiplier = 0.50
        elif drawdown > 0.10:
            multiplier = 0.75
        else:
            multiplier = 1.0
        
        return base_position_size * multiplier
    
    def get_portfolio_risk_metrics(
        self,
        positions: List[Dict],
        portfolio_value: float = 100000
    ) -> PortfolioRiskMetrics:
        """Get comprehensive portfolio risk metrics."""
        metrics = PortfolioRiskMetrics()
        
        # Calculate VaR
        metrics.portfolio_var = self.calculate_portfolio_var(positions)
        
        # Sector concentrations
        metrics.sector_concentrations = self.get_sector_concentration(positions)
        
        # Correlated pairs
        metrics.correlated_pairs = self.check_correlation_cluster(positions)
        
        # Check violations
        violations = []
        
        # VaR limit
        if metrics.portfolio_var > self.limits.max_portfolio_var:
            violations.append(f"VaR {metrics.portfolio_var:.1%} exceeds limit {self.limits.max_portfolio_var:.1%}")
        
        # Sector concentration
        for sector, pct in metrics.sector_concentrations.items():
            if pct > self.limits.max_sector_concentration:
                violations.append(f"Sector {sector} at {pct:.0%} exceeds limit")
        
        # Correlation clusters
        if len(metrics.correlated_pairs) > self.limits.max_correlated_positions:
            violations.append(f"{len(metrics.correlated_pairs)} correlated positions exceeds limit")
        
        metrics.violations = violations
        metrics.is_within_limits = len(violations) == 0
        metrics.risk_utilization = metrics.portfolio_var / self.limits.max_portfolio_var if self.limits.max_portfolio_var > 0 else 0
        
        return metrics
    
    def _get_correlation(self, ticker1: str, ticker2: str) -> float:
        """Get correlation between two tickers (cached)."""
        key = tuple(sorted([ticker1, ticker2]))
        
        if key in self._correlation_cache:
            return self._correlation_cache[key]
        
        try:
            import yfinance as yf
            
            t1 = yf.Ticker(ticker1)
            t2 = yf.Ticker(ticker2)
            
            h1 = t1.history(period="3mo")['Close']
            h2 = t2.history(period="3mo")['Close']
            
            if len(h1) < 20 or len(h2) < 20:
                return 0.0
            
            r1 = h1.pct_change().dropna()
            r2 = h2.pct_change().dropna()
            
            # Align indices
            common = r1.index.intersection(r2.index)
            if len(common) < 20:
                return 0.0
            
            corr = r1.loc[common].corr(r2.loc[common])
            self._correlation_cache[key] = float(corr) if pd.notna(corr) else 0.0
            
            return self._correlation_cache[key]
            
        except Exception as e:
            logger.debug(f"Correlation calc failed for {ticker1}/{ticker2}: {e}")
            return 0.0
    
    def _get_historical_returns(self, ticker: str, days: int = 252) -> Optional[pd.Series]:
        """Get historical returns for a ticker (cached)."""
        if ticker in self._returns_cache:
            return self._returns_cache[ticker]
        
        try:
            import yfinance as yf
            
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1y")['Close']
            
            if len(hist) < 20:
                return None
            
            returns = hist.pct_change().dropna()
            self._returns_cache[ticker] = returns
            
            return returns
            
        except Exception:
            return None


def get_portfolio_risk_manager(limits: RiskLimits = None) -> PortfolioRiskManager:
    """Get portfolio risk manager instance."""
    return PortfolioRiskManager(limits)


if __name__ == "__main__":
    # Demo
    prm = PortfolioRiskManager()
    
    print("📊 PORTFOLIO RISK MANAGER")
    print("=" * 60)
    
    # Test positions
    positions = [
        {'ticker': 'AAPL', 'value': 25000, 'sector': 'Technology'},
        {'ticker': 'MSFT', 'value': 20000, 'sector': 'Technology'},
        {'ticker': 'GOOGL', 'value': 15000, 'sector': 'Technology'},
        {'ticker': 'JPM', 'value': 20000, 'sector': 'Financials'},
        {'ticker': 'XOM', 'value': 20000, 'sector': 'Energy'},
    ]
    
    # Check limits for new position
    new_pos = {'ticker': 'NVDA', 'value': 15000, 'sector': 'Technology'}
    allowed, reason = prm.check_position_limits(new_pos, positions)
    print(f"\nNew position NVDA: {'✅ Allowed' if allowed else f'❌ Rejected: {reason}'}")
    
    # Get metrics
    metrics = prm.get_portfolio_risk_metrics(positions)
    print(f"\nPortfolio VaR: {metrics.portfolio_var:.2%}")
    print(f"Sector Concentrations: {metrics.sector_concentrations}")
    print(f"Correlated Pairs: {len(metrics.correlated_pairs)}")
    print(f"Within Limits: {'✅' if metrics.is_within_limits else '❌'}")
    
    if metrics.violations:
        print(f"Violations: {metrics.violations}")
