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
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import logging
from statistics import NormalDist

try:
    from scipy.stats import norm
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    norm = None

logger = logging.getLogger(__name__)

MIN_OVERLAP_DAYS = 60  # shared return days needed before a name is measured


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
    portfolio_var: Optional[float] = None       # None = not measured (never 0.0 for unknown)
    var_coverage: float = 0.0                   # share of position value the VaR covers
    notes: List[str] = field(default_factory=list)
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
    ) -> Optional[float]:
        """
        Correlation-adjusted parametric portfolio VaR as a decimal (0.025 = 2.5%).

        Returns None when it cannot be measured (no usable history). It never
        returns 0.0 for "unknown": callers must treat None as "not measured".
        Use :meth:`portfolio_var_report` for coverage, CVaR and exclusions.
        """
        return self.portfolio_var_report(positions, lookback_days, confidence)["parametric_var"]

    def portfolio_var_report(
        self,
        positions: List[Dict],
        lookback_days: int = 252,
        confidence: float = None,
        min_overlap_days: int = MIN_OVERLAP_DAYS,
    ) -> Dict:
        """
        Measure portfolio VaR / CVaR from real, date-aligned returns only.

        Why this exists: the old code filled any name with thin history with
        random synthetic returns (unseeded, on a RangeIndex that never lined up
        with the real dates), which produced NaN, and it returned 0.0 on any
        failure, which reads as "no risk". Here a name that cannot be measured
        is left out and reported, weights are renormalised over the names that
        were measured, and ``coverage`` says how much of the book that is.

        Returns a dict with: parametric_var, historical_var, cvar (expected
        shortfall; positive loss fractions, None when not measurable),
        confidence, horizon_days (1), coverage (share of position value
        measured), measured (tickers used), excluded ({ticker: reason}),
        n_days (shared trading days) and notes (plain-English caveats).
        """
        confidence = confidence or self.limits.var_confidence
        report: Dict = {
            "parametric_var": None, "historical_var": None, "cvar": None,
            "confidence": confidence, "horizon_days": 1, "coverage": 0.0,
            "measured": [], "excluded": {}, "n_days": 0, "notes": [],
        }
        values: Dict[str, float] = {}
        for p in positions or []:
            t = str(p.get('ticker', '')).strip()
            try:
                v = float(p.get('value', 0) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if not t:
                continue
            if v <= 0 or not np.isfinite(v):
                report["excluded"][t] = "no positive value"
                continue
            values[t] = values.get(t, 0.0) + v
        total = sum(values.values())
        if total <= 0:
            report["notes"].append("No positions with a positive value; VaR not measured.")
            return report

        series: Dict[str, pd.Series] = {}
        for t in values:
            try:
                r = self._get_historical_returns(t, lookback_days)
            except Exception as exc:  # a data failure must not look like "no risk"
                logger.debug("returns lookup failed for %s: %s", t, exc)
                r = None
            if r is None:
                report["excluded"][t] = "no price history"
                continue
            r = pd.Series(r).replace([np.inf, -np.inf], np.nan).dropna().tail(lookback_days)
            if len(r) < min_overlap_days:
                report["excluded"][t] = f"only {len(r)} return days (need {min_overlap_days})"
                continue
            series[t] = r

        # Inner-join on dates. If the common window is too short, drop the name
        # with the shortest history and retry, so one stray listing cannot
        # blank out the whole book.
        frame = pd.DataFrame()
        while series:
            frame = pd.concat(series, axis=1, join="inner").dropna()
            if len(frame) >= min_overlap_days:
                break
            shortest = min(series, key=lambda k: len(series[k]))
            report["excluded"][shortest] = f"fewer than {min_overlap_days} days overlapping the other holdings"
            del series[shortest]
            frame = pd.DataFrame()
        if frame.empty or len(frame) < min_overlap_days:
            report["notes"].append("Not enough overlapping price history; VaR not measured.")
            return report

        covered_value = sum(values[t] for t in frame.columns)
        w = np.array([values[t] / covered_value for t in frame.columns])
        port = pd.Series(frame.to_numpy() @ w, index=frame.index)
        sigma = float(port.std(ddof=1))
        if not np.isfinite(sigma):
            report["notes"].append("Return history was not finite; VaR not measured.")
            return report

        z = NormalDist().inv_cdf(confidence)       # stdlib: no SciPy needed
        cutoff = float(port.quantile(1.0 - confidence))
        report.update(
            parametric_var=float(sigma * z),
            historical_var=-cutoff,
            cvar=-float(port[port <= cutoff].mean()),
            coverage=covered_value / total,
            measured=[str(c) for c in frame.columns],
            n_days=int(len(frame)),
        )
        if report["excluded"]:
            report["notes"].append(
                f"Measured on {report['coverage']:.0%} of position value; excluded: "
                + ", ".join(f"{t} ({why})" for t, why in report["excluded"].items()) + ".")
        report["notes"].append(
            "Parametric VaR assumes normal returns; historical VaR and CVaR come from the "
            "realised portfolio return series (current weights, aligned dates).")
        return report

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
        
        # Calculate VaR (None = not measured)
        var_report = self.portfolio_var_report(positions)
        metrics.portfolio_var = var_report["parametric_var"]
        metrics.var_coverage = var_report["coverage"]
        metrics.notes = list(var_report["notes"])
        
        # Sector concentrations
        metrics.sector_concentrations = self.get_sector_concentration(positions)
        
        # Correlated pairs
        metrics.correlated_pairs = self.check_correlation_cluster(positions)
        
        # Check violations
        violations = []
        
        # VaR limit
        if metrics.portfolio_var is None:
            # Unmeasured is not "within limits": flag it so it cannot pass silently.
            violations.append("VaR not measured (insufficient price history); limit not verified")
        elif metrics.portfolio_var > self.limits.max_portfolio_var:
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
        metrics.risk_utilization = (metrics.portfolio_var / self.limits.max_portfolio_var
                                    if metrics.portfolio_var is not None and self.limits.max_portfolio_var > 0 else 0)
        
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
    var_text = "not measured" if metrics.portfolio_var is None else f"{metrics.portfolio_var:.2%}"
    print(f"\nPortfolio VaR: {var_text}")
    print(f"Sector Concentrations: {metrics.sector_concentrations}")
    print(f"Correlated Pairs: {len(metrics.correlated_pairs)}")
    print(f"Within Limits: {'✅' if metrics.is_within_limits else '❌'}")
    
    if metrics.violations:
        print(f"Violations: {metrics.violations}")
