"""
price_target_calculator.py - Advanced Price Target Calculator
Implements DCF valuation, analyst consensus, and volatility adjustments
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta


class AdvancedPriceTargetCalculator:
    """
    Advanced price target calculator with multiple methodologies:
    1. DCF-based intrinsic value
    2. Comparable company analysis
    3. Analyst consensus integration
    4. Volatility-adjusted probabilities
    5. Time-horizon projections
    """

    def __init__(self, ticker: str, current_price: float, info: dict):
        self.ticker = ticker
        self.current_price = current_price
        self.info = info

        # Extract key metrics
        self.market_cap = info.get('marketCap', 0)
        self.free_cash_flow = info.get('freeCashflow', 0)
        self.revenue = info.get('totalRevenue', 0)
        self.eps = info.get('trailingEps', 0)
        self.forward_eps = info.get('forwardEps', 0)
        self.pe = info.get('trailingPE', 0)
        self.revenue_growth = info.get('revenueGrowth', 0)
        self.beta = info.get('beta', 1.0)
        self.shares_outstanding = info.get('sharesOutstanding', 0)

        # Risk-free rate (US 10Y Treasury ~4.5% as of 2025)
        self.risk_free_rate = 0.045

        # Market risk premium
        self.market_risk_premium = 0.08

    def calculate_dcf_target(self) -> Optional[float]:
        """
        Calculate intrinsic value using Discounted Cash Flow (DCF).
        Simplified DCF model suitable for most stocks.
        """
        if self.free_cash_flow <= 0 or self.shares_outstanding == 0:
            return None

        # Calculate WACC (Weighted Average Cost of Capital)
        # CAPM: Cost of Equity = Risk-Free Rate + Beta × Market Risk Premium
        cost_of_equity = self.risk_free_rate + (self.beta * self.market_risk_premium)

        # Assume cost of equity ≈ WACC for simplicity
        wacc = min(cost_of_equity, 0.15)  # Cap at 15%

        # Project 5-year cash flows
        # Year 1-3: Use revenue growth rate
        # Year 4-5: Taper to 3% terminal growth
        growth_rate = min(self.revenue_growth, 0.25) if self.revenue_growth else 0.05

        projected_fcf = []
        current_fcf = self.free_cash_flow

        for year in range(1, 6):
            if year <= 3:
                growth = growth_rate
            elif year == 4:
                growth = (growth_rate + 0.03) / 2  # Taper
            else:
                growth = 0.03  # Terminal growth rate

            current_fcf = current_fcf * (1 + growth)
            projected_fcf.append(current_fcf)

        # Calculate present value of projected cash flows
        pv_cash_flows = sum(
            fcf / ((1 + wacc) ** (i + 1))
            for i, fcf in enumerate(projected_fcf)
        )

        # Terminal value (Gordon Growth Model)
        terminal_fcf = projected_fcf[-1] * (1 + 0.03)
        terminal_value = terminal_fcf / (wacc - 0.03)
        pv_terminal_value = terminal_value / ((1 + wacc) ** 5)

        # Enterprise value
        enterprise_value = pv_cash_flows + pv_terminal_value

        # Equity value (simplified: EV ≈ Equity Value for now)
        equity_value = enterprise_value

        # Price per share
        intrinsic_value = equity_value / self.shares_outstanding

        return round(intrinsic_value, 2)

    def calculate_pe_based_target(self) -> Optional[float]:
        """
        Calculate price target using P/E multiple and forward EPS.
        """
        if not self.forward_eps or not self.pe or self.pe <= 0:
            return None

        # Use current P/E ratio with forward EPS
        # More conservative: Use 85% of current P/E to be prudent
        conservative_pe = self.pe * 0.85

        price_target = self.forward_eps * conservative_pe

        return round(price_target, 2)

    def calculate_revenue_multiple_target(self) -> Optional[float]:
        """
        Calculate price target using Price/Sales ratio.
        """
        if not self.revenue or not self.market_cap or self.shares_outstanding == 0:
            return None

        # Current P/S ratio
        ps_ratio = self.market_cap / self.revenue

        # Project next year revenue
        if self.revenue_growth:
            next_year_revenue = self.revenue * (1 + self.revenue_growth)
        else:
            next_year_revenue = self.revenue * 1.05  # Assume 5% growth

        # Target market cap
        target_market_cap = next_year_revenue * ps_ratio * 0.9  # 10% discount

        # Price per share
        price_target = target_market_cap / self.shares_outstanding

        return round(price_target, 2)

    def get_analyst_consensus(self, enhanced_data: Dict) -> Optional[Dict]:
        """
        Extract analyst consensus from API data and yfinance.
        """
        analyst_info = {}

        # From FMP price target API
        if 'price_target' in enhanced_data:
            pt_data = enhanced_data['price_target']
            if pt_data:
                analyst_info['analyst_target'] = pt_data.get('price_target', None)
                analyst_info['analyst_name'] = pt_data.get('analyst_name', 'Unknown')
                analyst_info['published_date'] = pt_data.get('published_date', 'Unknown')

        # From yfinance info
        if self.info:
            analyst_info['target_high'] = self.info.get('targetHighPrice', None)
            analyst_info['target_low'] = self.info.get('targetLowPrice', None)
            analyst_info['target_mean'] = self.info.get('targetMeanPrice', None)
            analyst_info['target_median'] = self.info.get('targetMedianPrice', None)
            analyst_info['num_analysts'] = self.info.get('numberOfAnalystOpinions', 0)

        return analyst_info if analyst_info else None

    def calculate_comprehensive_targets(
        self,
        recommendation: str,
        technical_metrics: Dict,
        enhanced_data: Dict,
        volatility: float
    ) -> Dict:
        """
        Calculate comprehensive price targets using all methodologies.
        """
        targets_dict = {
            'current_price': self.current_price,
            'currency': '₹' if self.ticker.endswith('.NS') or self.ticker.endswith('.BO') else '$',
            'calculation_methods': [],
            'targets': [],
            'entry_price': 0,
            'stop_loss': 0,
            'time_horizons': {},
            'probabilities_adjusted': False
        }

        # 1. DCF-based intrinsic value
        dcf_value = self.calculate_dcf_target()
        if dcf_value and dcf_value > 0:
            targets_dict['calculation_methods'].append('DCF Intrinsic Value')
            targets_dict['dcf_value'] = dcf_value
            upside_dcf = ((dcf_value - self.current_price) / self.current_price) * 100
            targets_dict['dcf_upside'] = round(upside_dcf, 1)

        # 2. P/E based target
        pe_target = self.calculate_pe_based_target()
        if pe_target and pe_target > 0:
            targets_dict['calculation_methods'].append('P/E Multiple Analysis')
            targets_dict['pe_target'] = pe_target
            upside_pe = ((pe_target - self.current_price) / self.current_price) * 100
            targets_dict['pe_upside'] = round(upside_pe, 1)

        # 3. Revenue multiple target
        rev_target = self.calculate_revenue_multiple_target()
        if rev_target and rev_target > 0:
            targets_dict['calculation_methods'].append('Revenue Multiple Analysis')
            targets_dict['revenue_target'] = rev_target
            upside_rev = ((rev_target - self.current_price) / self.current_price) * 100
            targets_dict['revenue_upside'] = round(upside_rev, 1)

        # 4. Analyst consensus
        analyst_data = self.get_analyst_consensus(enhanced_data)
        if analyst_data:
            targets_dict['analyst_consensus'] = analyst_data
            if analyst_data.get('target_mean'):
                targets_dict['calculation_methods'].append('Analyst Consensus')
                upside_analyst = ((analyst_data['target_mean'] - self.current_price) / self.current_price) * 100
                targets_dict['analyst_upside'] = round(upside_analyst, 1)

        # 5. Technical support/resistance
        support = technical_metrics.get('Support', self.current_price * 0.85)
        resistance = technical_metrics.get('Resistance', self.current_price * 1.15)
        targets_dict['technical_support'] = round(support, 2)
        targets_dict['technical_resistance'] = round(resistance, 2)

        # 6. Calculate final targets based on recommendation
        entry, stop, price_targets = self._calculate_recommendation_targets(
            recommendation, support, resistance, volatility
        )

        targets_dict['entry_price'] = entry
        targets_dict['stop_loss'] = stop
        targets_dict['targets'] = price_targets

        # 7. Add time horizons
        targets_dict['time_horizons'] = self._calculate_time_horizons(recommendation, volatility)

        # 8. Mark probabilities as adjusted
        targets_dict['probabilities_adjusted'] = True

        return targets_dict

    def _calculate_recommendation_targets(
        self,
        recommendation: str,
        support: float,
        resistance: float,
        volatility: float
    ) -> Tuple[float, float, List[Tuple[float, int]]]:
        """
        Calculate entry, stop loss, and price targets with volatility adjustment.
        """
        current = self.current_price

        # Volatility adjustment factor
        # High volatility = wider targets and stops
        vol_factor = 1 + (volatility * 0.5)  # Amplify by volatility
        vol_factor = max(0.8, min(vol_factor, 2.0))  # Clamp between 0.8x and 2.0x

        if recommendation == "STRONG BUY":
            entry = round(current * 1.02, 2)
            stop_loss = round(max(support * 0.95, current * (1 - 0.12 * vol_factor)), 2)

            target_1 = round(current * (1 + 0.25 * vol_factor), 2)
            target_2 = round(current * (1 + 0.40 * vol_factor), 2)
            target_3 = round(current * (1 + 0.60 * vol_factor), 2)

            # Volatility-adjusted probabilities
            prob_1 = self._adjust_probability(75, volatility, "upside")
            prob_2 = self._adjust_probability(50, volatility, "upside")
            prob_3 = self._adjust_probability(25, volatility, "upside")

            price_targets = [
                (target_1, prob_1),
                (target_2, prob_2),
                (target_3, prob_3)
            ]

        elif recommendation == "BUY":
            entry = round(current * 0.97, 2)
            stop_loss = round(max(support * 0.97, current * (1 - 0.10 * vol_factor)), 2)

            target_1 = round(current * (1 + 0.15 * vol_factor), 2)
            target_2 = round(current * (1 + 0.25 * vol_factor), 2)
            target_3 = round(current * (1 + 0.40 * vol_factor), 2)

            prob_1 = self._adjust_probability(70, volatility, "upside")
            prob_2 = self._adjust_probability(45, volatility, "upside")
            prob_3 = self._adjust_probability(20, volatility, "upside")

            price_targets = [
                (target_1, prob_1),
                (target_2, prob_2),
                (target_3, prob_3)
            ]

        elif recommendation == "HOLD":
            entry = round(current, 2)
            stop_loss = round(max(support, current * (1 - 0.08 * vol_factor)), 2)

            target_1 = round(current * (1 + 0.10 * vol_factor), 2)
            target_2 = round(current * (1 + 0.20 * vol_factor), 2)

            prob_1 = self._adjust_probability(50, volatility, "upside")
            prob_2 = self._adjust_probability(25, volatility, "upside")

            price_targets = [
                (target_1, prob_1),
                (target_2, prob_2)
            ]

        elif recommendation == "SELL":
            entry = 0
            stop_loss = round(current * (1 + 0.05 * vol_factor), 2)

            target_1 = round(current * (1 - 0.15 * vol_factor), 2)
            target_2 = round(current * (1 - 0.30 * vol_factor), 2)

            prob_1 = self._adjust_probability(60, volatility, "downside")
            prob_2 = self._adjust_probability(35, volatility, "downside")

            price_targets = [
                (target_1, prob_1),
                (target_2, prob_2)
            ]

        else:  # STRONG SELL
            entry = 0
            stop_loss = round(current * (1 + 0.03 * vol_factor), 2)

            target_1 = round(current * (1 - 0.25 * vol_factor), 2)
            target_2 = round(current * (1 - 0.50 * vol_factor), 2)

            prob_1 = self._adjust_probability(70, volatility, "downside")
            prob_2 = self._adjust_probability(40, volatility, "downside")

            price_targets = [
                (target_1, prob_1),
                (target_2, prob_2)
            ]

        return entry, stop_loss, price_targets

    def _adjust_probability(self, base_prob: int, volatility: float, direction: str) -> int:
        """
        Adjust probability based on volatility.
        High volatility = lower probability of hitting targets (for upside).
        """
        # Volatility categories
        if volatility < 0.25:
            adjustment = 5  # Increase probability (low vol = more predictable)
        elif volatility < 0.35:
            adjustment = 3
        elif volatility < 0.50:
            adjustment = 0  # No change
        elif volatility < 0.70:
            adjustment = -5  # Decrease probability
        else:
            adjustment = -10  # High vol = less predictable

        # For downside targets, inverse the adjustment
        if direction == "downside":
            adjustment = -adjustment

        adjusted_prob = base_prob + adjustment

        # Clamp between 5% and 95%
        adjusted_prob = max(5, min(adjusted_prob, 95))

        return adjusted_prob

    def _calculate_time_horizons(self, recommendation: str, volatility: float) -> Dict:
        """
        Calculate time horizons for reaching targets.
        Higher volatility = faster moves (shorter timeframes).
        """
        # Base timeframes (in months)
        base_horizons = {
            "STRONG BUY": (6, 12),
            "BUY": (6, 18),
            "HOLD": (12, 24),
            "SELL": (1, 3),
            "STRONG SELL": (0, 1)
        }

        min_months, max_months = base_horizons.get(recommendation, (6, 12))

        # Adjust for volatility
        if volatility > 0.60:
            # High volatility = faster moves
            min_months = max(1, int(min_months * 0.7))
            max_months = max(3, int(max_months * 0.7))
        elif volatility < 0.25:
            # Low volatility = slower moves
            min_months = int(min_months * 1.3)
            max_months = int(max_months * 1.3)

        today = datetime.now()
        min_date = (today + timedelta(days=min_months * 30)).strftime('%b %Y')
        max_date = (today + timedelta(days=max_months * 30)).strftime('%b %Y')

        return {
            'min_months': min_months,
            'max_months': max_months,
            'min_date': min_date,
            'max_date': max_date,
            'timeframe': f"{min_months}-{max_months} months",
            'target_date_range': f"{min_date} to {max_date}"
        }

    def format_comprehensive_output(self, targets_dict: Dict, recommendation: str) -> str:
        """
        Format comprehensive price targets for display.
        """
        output = []
        currency = targets_dict['currency']
        current = targets_dict['current_price']

        output.append("\n" + "=" * 90)
        output.append("💰 COMPREHENSIVE PRICE ANALYSIS & TARGETS".center(90))
        output.append("=" * 90)

        # Valuation Methods Used
        if targets_dict['calculation_methods']:
            output.append("\n📊 VALUATION METHODS USED:")
            for i, method in enumerate(targets_dict['calculation_methods'], 1):
                output.append(f"   {i}. {method}")

        # Fundamental Valuations
        output.append("\n" + "-" * 90)
        output.append("🎯 FUNDAMENTAL VALUATIONS")
        output.append("-" * 90)

        output.append(f"\n  Current Market Price: {currency}{current:.2f}")

        if 'dcf_value' in targets_dict:
            dcf = targets_dict['dcf_value']
            upside = targets_dict['dcf_upside']
            indicator = "🟢 UNDERVALUED" if upside > 0 else "🔴 OVERVALUED"
            output.append(f"  DCF Intrinsic Value:  {currency}{dcf:.2f} ({upside:+.1f}%) {indicator}")

        if 'pe_target' in targets_dict:
            pe_t = targets_dict['pe_target']
            pe_up = targets_dict['pe_upside']
            indicator = "🟢" if pe_up > 0 else "🔴"
            output.append(f"  P/E Based Target:     {currency}{pe_t:.2f} ({pe_up:+.1f}%) {indicator}")

        if 'revenue_target' in targets_dict:
            rev_t = targets_dict['revenue_target']
            rev_up = targets_dict['revenue_upside']
            indicator = "🟢" if rev_up > 0 else "🔴"
            output.append(f"  Revenue Multiple:     {currency}{rev_t:.2f} ({rev_up:+.1f}%) {indicator}")

        # Analyst Consensus
        if 'analyst_consensus' in targets_dict:
            analyst = targets_dict['analyst_consensus']
            output.append("\n" + "-" * 90)
            output.append("👥 ANALYST CONSENSUS")
            output.append("-" * 90)

            if analyst.get('target_mean'):
                mean = analyst['target_mean']
                upside = targets_dict.get('analyst_upside', 0)
                indicator = "🟢" if upside > 0 else "🔴"
                output.append(f"\n  Mean Target:          {currency}{mean:.2f} ({upside:+.1f}%) {indicator}")

            if analyst.get('target_high'):
                output.append(f"  High Target:          {currency}{analyst['target_high']:.2f}")

            if analyst.get('target_low'):
                output.append(f"  Low Target:           {currency}{analyst['target_low']:.2f}")

            if analyst.get('num_analysts'):
                output.append(f"  Number of Analysts:   {analyst['num_analysts']}")

        # Technical Levels
        output.append("\n" + "-" * 90)
        output.append("📈 TECHNICAL LEVELS")
        output.append("-" * 90)

        output.append(f"\n  Support Level:        {currency}{targets_dict['technical_support']:.2f}")
        output.append(f"  Resistance Level:     {currency}{targets_dict['technical_resistance']:.2f}")

        # Entry/Exit Strategy
        output.append("\n" + "=" * 90)
        output.append("🎯 RECOMMENDED ENTRY/EXIT STRATEGY".center(90))
        output.append("=" * 90)

        if recommendation not in ["SELL", "STRONG SELL"]:
            entry = targets_dict['entry_price']
            entry_diff = ((entry - current) / current * 100)
            output.append(f"\n  Entry Price:          {currency}{entry:.2f} ({entry_diff:+.1f}% from current)")
        else:
            output.append(f"\n  Recommendation:       EXIT POSITION")

        stop = targets_dict['stop_loss']
        stop_diff = ((stop - current) / current * 100)
        output.append(f"  Stop Loss:            {currency}{stop:.2f} ({stop_diff:+.1f}% from current)")

        # Time Horizon
        horizon = targets_dict['time_horizons']
        output.append(f"\n  Time Horizon:         {horizon['timeframe']}")
        output.append(f"  Target Date Range:    {horizon['target_date_range']}")

        # Price Targets with Probabilities
        output.append("\n" + "-" * 90)
        output.append("🎯 PRICE TARGETS (Volatility-Adjusted Probabilities)")
        output.append("-" * 90)

        for i, (target, prob) in enumerate(targets_dict['targets'], 1):
            diff = ((target - current) / current * 100)
            direction = "upside" if diff > 0 else "downside"

            # Probability bar
            bar_length = 30
            filled = int((prob / 100) * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)

            output.append(f"\n  Target {i}: {currency}{target:.2f} ({diff:+.1f}% {direction})")
            output.append(f"  [{bar}] {prob}% probability")

        # Conviction Summary
        output.append("\n" + "=" * 90)
        output.append("📊 CONVICTION SUMMARY".center(90))
        output.append("=" * 90)

        # Calculate average upside from all methods
        all_upsides = []
        if 'dcf_upside' in targets_dict:
            all_upsides.append(targets_dict['dcf_upside'])
        if 'pe_upside' in targets_dict:
            all_upsides.append(targets_dict['pe_upside'])
        if 'analyst_upside' in targets_dict:
            all_upsides.append(targets_dict['analyst_upside'])

        if all_upsides:
            avg_upside = np.mean(all_upsides)
            output.append(f"\n  Average Upside (All Methods): {avg_upside:+.1f}%")

            if avg_upside > 20:
                conviction = "🟢 HIGH CONVICTION BUY"
            elif avg_upside > 10:
                conviction = "🟢 MODERATE BUY"
            elif avg_upside > 0:
                conviction = "🟡 WEAK BUY / HOLD"
            elif avg_upside > -10:
                conviction = "🟡 WEAK SELL / HOLD"
            else:
                conviction = "🔴 HIGH CONVICTION SELL"

            output.append(f"  Conviction Level:             {conviction}")

        output.append("\n" + "=" * 90)

        return "\n".join(output)


# Helper function for easy integration
def calculate_advanced_targets(
    ticker: str,
    current_price: float,
    info: dict,
    recommendation: str,
    technical_metrics: dict,
    enhanced_data: dict,
    volatility: float
) -> Dict:
    """
    Convenience function to calculate advanced price targets.
    """
    calculator = AdvancedPriceTargetCalculator(ticker, current_price, info)
    targets = calculator.calculate_comprehensive_targets(
        recommendation, technical_metrics, enhanced_data, volatility
    )
    return targets