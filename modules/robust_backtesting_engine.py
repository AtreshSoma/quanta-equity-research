"""
═══════════════════════════════════════════════════════════════════════════════
ROBUST BACKTESTING ENGINE - PROFESSIONAL GRADE BACKTESTING SYSTEM
═══════════════════════════════════════════════════════════════════════════════

FEATURES:
✅ Realistic transaction costs (commission + slippage + market impact)
✅ Walk-forward analysis (train/test with rolling windows)
✅ Monte Carlo simulation (10,000+ runs with confidence intervals)
✅ Time-series cross-validation (k-fold with proper ordering)
✅ Liquidity constraints (max 5% of daily volume)
✅ Realistic Sharpe Ratio (adjusted for autocorrelation + risk-free rate)
✅ Maximum Adverse Excursion (MAE) analysis
✅ Maximum Favorable Excursion (MFE) analysis
✅ Trade-by-trade analysis with efficiency metrics
✅ Partial fill simulation
✅ Slippage modeling based on volatility and volume
✅ Market impact estimation
✅ Out-of-sample testing

© 2025 - Professional Backtesting Framework
═══════════════════════════════════════════════════════════════════════════════
"""

import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, List, Tuple, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import logging
from scipy import stats
from concurrent.futures import ProcessPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class OrderSide(Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class FillStatus(Enum):
    """Order fill status"""
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    UNFILLED = "UNFILLED"


@dataclass
class TransactionCosts:
    """Transaction cost configuration"""
    commission_pct: float = 0.0005  # 0.05% per trade (5 basis points)
    slippage_bps: float = 10.0      # 10 basis points average slippage
    market_impact_factor: float = 0.1  # Square-root market impact coefficient
    min_commission: float = 1.0     # Minimum commission per trade


@dataclass
class Order:
    """Trading order"""
    ticker: str
    side: OrderSide
    order_type: OrderType
    shares: float
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    timestamp: Optional[datetime] = None

    # Execution details
    fill_price: Optional[float] = None
    filled_shares: float = 0.0
    fill_status: FillStatus = FillStatus.UNFILLED
    commission: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0


@dataclass
class Trade:
    """Completed trade (entry + exit)"""
    ticker: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    shares: float
    side: OrderSide  # LONG or SHORT

    # P&L
    gross_pnl: float
    commissions: float
    slippage: float
    net_pnl: float
    net_pnl_pct: float

    # Trade characteristics
    holding_period_days: int
    max_adverse_excursion: float  # MAE: worst drawdown during trade
    max_favorable_excursion: float  # MFE: best gain during trade
    efficiency: float  # net_pnl / MFE (how much of potential was captured)


@dataclass
class BacktestResults:
    """Comprehensive backtest results"""
    # Period
    start_date: datetime
    end_date: datetime
    total_days: int
    trading_days: int

    # Capital
    initial_capital: float
    final_capital: float
    peak_capital: float

    # Returns
    total_return_pct: float
    annualized_return_pct: float
    cagr: float

    # Risk metrics
    volatility: float
    downside_deviation: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int

    # Risk-adjusted returns
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    omega_ratio: float
    information_ratio: float

    # Trading statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float  # Gross wins / Gross losses
    avg_win: float
    avg_loss: float
    avg_win_loss_ratio: float
    largest_win: float
    largest_loss: float

    # Costs
    total_commissions: float
    total_slippage: float
    total_market_impact: float
    total_costs: float
    cost_drag_pct: float  # % return lost to costs

    # MAE/MFE analysis
    avg_mae: float
    avg_mfe: float
    avg_efficiency: float

    # Trades
    trades: List[Trade]

    # Equity curve
    equity_curve: pd.Series
    drawdown_curve: pd.Series


@dataclass
class WalkForwardResults:
    """Walk-forward analysis results"""
    windows: List[Dict[str, Any]]
    avg_in_sample_sharpe: float
    avg_out_sample_sharpe: float
    in_sample_out_sample_correlation: float
    is_overfitted: bool  # True if out-sample significantly worse
    overall_results: BacktestResults


@dataclass
class MonteCarloResults:
    """Monte Carlo simulation results"""
    num_simulations: int
    final_capital_mean: float
    final_capital_std: float
    final_capital_median: float
    final_capital_5th_percentile: float
    final_capital_95th_percentile: float
    probability_of_profit: float
    probability_of_loss: float
    expected_value: float
    worst_case_1pct: float
    best_case_99pct: float
    simulation_results: List[float]


# ═══════════════════════════════════════════════════════════════════════════════
# ROBUST BACKTESTING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class RobustBacktestEngine:
    """
    Professional-grade backtesting engine with realistic execution simulation.

    Features:
    - Transaction costs (commission, slippage, market impact)
    - Liquidity constraints
    - Walk-forward analysis
    - Monte Carlo simulation
    - Cross-validation
    - MAE/MFE analysis
    """

    def __init__(self,
                 initial_capital: float = 100000.0,
                 transaction_costs: Optional[TransactionCosts] = None,
                 max_position_pct: float = 10.0,
                 max_volume_participation: float = 0.05,
                 risk_free_rate: float = 0.045):
        """
        Initialize Robust Backtest Engine.

        Args:
            initial_capital: Starting capital
            transaction_costs: Transaction cost configuration
            max_position_pct: Maximum position size as % of portfolio
            max_volume_participation: Max % of daily volume to trade
            risk_free_rate: Risk-free rate for Sharpe ratio
        """
        self.initial_capital = initial_capital
        self.transaction_costs = transaction_costs or TransactionCosts()
        self.max_position_pct = max_position_pct
        self.max_volume_participation = max_volume_participation
        self.risk_free_rate = risk_free_rate

        # State
        self.cash = initial_capital
        self.positions: Dict[str, float] = {}  # ticker -> shares
        self.equity_curve = []
        self.trades: List[Trade] = []

        logger.info(f"RobustBacktestEngine initialized with ${initial_capital:,.2f}")


    # ═══════════════════════════════════════════════════════════════════════════
    # ORDER EXECUTION WITH REALISTIC COSTS
    # ═══════════════════════════════════════════════════════════════════════════

    def execute_order(self,
                     order: Order,
                     current_price: float,
                     daily_volume: float,
                     volatility: float) -> Order:
        """
        Execute order with realistic transaction costs and constraints.

        Args:
            order: Order to execute
            current_price: Current market price
            daily_volume: Average daily volume
            volatility: Current volatility (for slippage estimation)

        Returns:
            Updated order with execution details
        """
        # Check liquidity constraint
        max_shares = daily_volume * self.max_volume_participation

        if order.shares > max_shares:
            # Partial fill
            filled_shares = max_shares
            order.fill_status = FillStatus.PARTIAL
            logger.warning(f"Partial fill: {filled_shares:.0f} / {order.shares:.0f} shares " +
                          f"(liquidity constraint: {self.max_volume_participation*100}% of volume)")
        else:
            filled_shares = order.shares
            order.fill_status = FillStatus.FILLED

        order.filled_shares = filled_shares

        # Calculate slippage based on volatility and order size
        slippage_bps = self._calculate_slippage(
            shares=filled_shares,
            daily_volume=daily_volume,
            volatility=volatility
        )

        # Calculate market impact (square-root model)
        market_impact_bps = self._calculate_market_impact(
            shares=filled_shares,
            daily_volume=daily_volume
        )

        # Total execution cost in bps
        total_cost_bps = slippage_bps + market_impact_bps

        # Apply cost to fill price
        if order.side == OrderSide.BUY:
            # Buy: pay slippage + impact (price goes up)
            order.fill_price = current_price * (1 + total_cost_bps / 10000)
        else:
            # Sell: receive less (price goes down)
            order.fill_price = current_price * (1 - total_cost_bps / 10000)

        order.slippage = abs(order.fill_price - current_price) * filled_shares
        order.market_impact = current_price * (market_impact_bps / 10000) * filled_shares

        # Calculate commission
        order.commission = max(
            order.fill_price * filled_shares * self.transaction_costs.commission_pct,
            self.transaction_costs.min_commission
        )

        logger.debug(f"Executed {order.side.value} {filled_shares:.0f} shares @ ${order.fill_price:.2f} " +
                    f"(slippage: {slippage_bps:.1f}bps, impact: {market_impact_bps:.1f}bps, " +
                    f"commission: ${order.commission:.2f})")

        return order


    def _calculate_slippage(self,
                           shares: float,
                           daily_volume: float,
                           volatility: float) -> float:
        """
        Calculate slippage in basis points.

        Slippage increases with:
        - Order size relative to volume
        - Volatility

        Args:
            shares: Shares to trade
            daily_volume: Average daily volume
            volatility: Current volatility

        Returns:
            Slippage in basis points
        """
        base_slippage = self.transaction_costs.slippage_bps

        # Volume factor: larger orders relative to volume have more slippage
        volume_ratio = shares / daily_volume if daily_volume > 0 else 1.0
        volume_factor = 1.0 + (volume_ratio * 10)  # Up to 10x for very large orders

        # Volatility factor: higher volatility = more slippage
        volatility_factor = 1.0 + (volatility * 2)  # Up to 3x in high volatility

        slippage_bps = base_slippage * volume_factor * volatility_factor

        return min(slippage_bps, 100)  # Cap at 100bps (1%)


    def _calculate_market_impact(self,
                                 shares: float,
                                 daily_volume: float) -> float:
        """
        Calculate market impact in basis points.

        Uses square-root model: Impact ∝ sqrt(order_size / daily_volume)

        Args:
            shares: Shares to trade
            daily_volume: Average daily volume

        Returns:
            Market impact in basis points
        """
        if daily_volume == 0:
            return 0.0

        participation_rate = shares / daily_volume

        # Square-root market impact model
        impact_bps = self.transaction_costs.market_impact_factor * \
                     np.sqrt(participation_rate) * 10000

        return min(impact_bps, 50)  # Cap at 50bps


    # ═══════════════════════════════════════════════════════════════════════════
    # BACKTESTING CORE
    # ═══════════════════════════════════════════════════════════════════════════

    def run_backtest(self,
                     data: pd.DataFrame,
                     strategy_func: Callable,
                     **strategy_params) -> BacktestResults:
        """
        Run backtest with realistic execution.

        Args:
            data: Price data with OHLCV columns
            strategy_func: Strategy function that returns signals
            **strategy_params: Parameters to pass to strategy

        Returns:
            BacktestResults
        """
        self._reset_state()

        start_date = data.index[0]
        end_date = data.index[-1]
        equity_curve = [self.initial_capital]
        peak_capital = self.initial_capital

        # Track open positions for MAE/MFE analysis
        open_positions: Dict[str, Dict[str, Any]] = {}

        logger.info(f"Running backtest from {start_date.date()} to {end_date.date()}")

        # Iterate through data
        for i in range(len(data)):
            current_date = data.index[i]
            current_bar = data.iloc[i]

            # Get strategy signal
            signal = strategy_func(data.iloc[:i+1], **strategy_params)

            if signal is None:
                continue

            # Execute trades based on signal
            ticker = signal.get('ticker')
            action = signal.get('action')  # 'BUY', 'SELL', 'HOLD'
            shares = signal.get('shares', 0)

            if action == 'BUY' and ticker and shares > 0:
                # Create buy order
                order = Order(
                    ticker=ticker,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    shares=shares,
                    timestamp=current_date
                )

                # Execute order
                executed_order = self.execute_order(
                    order,
                    current_bar['Close'],
                    current_bar.get('Volume', 1000000),
                    current_bar.get('Volatility', 0.20)
                )

                if executed_order.fill_status in [FillStatus.FILLED, FillStatus.PARTIAL]:
                    # Update portfolio
                    cost = executed_order.fill_price * executed_order.filled_shares + \
                           executed_order.commission + executed_order.slippage

                    if cost <= self.cash:
                        self.cash -= cost
                        self.positions[ticker] = self.positions.get(ticker, 0) + executed_order.filled_shares

                        # Track position for MAE/MFE
                        open_positions[ticker] = {
                            'entry_date': current_date,
                            'entry_price': executed_order.fill_price,
                            'shares': executed_order.filled_shares,
                            'entry_commission': executed_order.commission,
                            'entry_slippage': executed_order.slippage,
                            'max_price': executed_order.fill_price,
                            'min_price': executed_order.fill_price
                        }

            elif action == 'SELL' and ticker and ticker in self.positions:
                # Create sell order
                shares_to_sell = min(shares, self.positions[ticker])

                order = Order(
                    ticker=ticker,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    shares=shares_to_sell,
                    timestamp=current_date
                )

                # Execute order
                executed_order = self.execute_order(
                    order,
                    current_bar['Close'],
                    current_bar.get('Volume', 1000000),
                    current_bar.get('Volatility', 0.20)
                )

                if executed_order.fill_status in [FillStatus.FILLED, FillStatus.PARTIAL]:
                    # Update portfolio
                    proceeds = executed_order.fill_price * executed_order.filled_shares - \
                              executed_order.commission - executed_order.slippage

                    self.cash += proceeds
                    self.positions[ticker] -= executed_order.filled_shares

                    # Close trade and calculate MAE/MFE
                    if ticker in open_positions:
                        pos = open_positions[ticker]

                        # Calculate trade metrics
                        entry_price = pos['entry_price']
                        exit_price = executed_order.fill_price
                        trade_shares = executed_order.filled_shares

                        gross_pnl = (exit_price - entry_price) * trade_shares
                        total_commission = pos['entry_commission'] + executed_order.commission
                        total_slippage = pos['entry_slippage'] + executed_order.slippage
                        net_pnl = gross_pnl - total_commission - total_slippage

                        # MAE and MFE
                        mae = (pos['min_price'] - entry_price) * trade_shares
                        mfe = (pos['max_price'] - entry_price) * trade_shares

                        efficiency = net_pnl / mfe if mfe > 0 else 0

                        trade = Trade(
                            ticker=ticker,
                            entry_date=pos['entry_date'],
                            exit_date=current_date,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=trade_shares,
                            side=OrderSide.BUY,
                            gross_pnl=gross_pnl,
                            commissions=total_commission,
                            slippage=total_slippage,
                            net_pnl=net_pnl,
                            net_pnl_pct=(net_pnl / (entry_price * trade_shares)) * 100,
                            holding_period_days=(current_date - pos['entry_date']).days,
                            max_adverse_excursion=mae,
                            max_favorable_excursion=mfe,
                            efficiency=efficiency
                        )

                        self.trades.append(trade)

                        # Remove or reduce position tracking
                        if self.positions[ticker] == 0:
                            del open_positions[ticker]
                            del self.positions[ticker]

            # Update MAE/MFE for open positions
            for ticker_pos, pos_info in open_positions.items():
                if ticker_pos in data.columns:  # Assuming multi-ticker data
                    current_price = data.loc[current_date, ticker_pos]
                    pos_info['max_price'] = max(pos_info['max_price'], current_price)
                    pos_info['min_price'] = min(pos_info['min_price'], current_price)

            # Calculate portfolio value
            portfolio_value = self.cash
            for ticker_pos, shares_pos in self.positions.items():
                # Use current bar close as approximation
                portfolio_value += current_bar['Close'] * shares_pos

            equity_curve.append(portfolio_value)
            peak_capital = max(peak_capital, portfolio_value)

        # Calculate results
        results = self._calculate_results(
            equity_curve=pd.Series(equity_curve, index=data.index),
            start_date=start_date,
            end_date=end_date
        )

        logger.info(f"Backtest complete: Return={results.total_return_pct:.2f}%, " +
                   f"Sharpe={results.sharpe_ratio:.2f}, MaxDD={results.max_drawdown_pct:.2f}%")

        return results


    def _reset_state(self):
        """Reset backtest state"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.equity_curve = []


    def _calculate_results(self,
                          equity_curve: pd.Series,
                          start_date: datetime,
                          end_date: datetime) -> BacktestResults:
        """Calculate comprehensive backtest results"""

        # Basic metrics
        initial = equity_curve.iloc[0]
        final = equity_curve.iloc[-1]
        peak = equity_curve.max()

        total_days = (end_date - start_date).days
        trading_days = len(equity_curve)
        years = total_days / 365.25

        # Returns
        total_return_pct = ((final - initial) / initial) * 100
        cagr = (((final / initial) ** (1 / years)) - 1) * 100 if years > 0 else 0
        annualized_return = total_return_pct / years if years > 0 else 0

        # Calculate returns series
        returns = equity_curve.pct_change().dropna()

        # Risk metrics
        volatility = returns.std() * np.sqrt(252) * 100
        downside_returns = returns[returns < 0]
        downside_deviation = downside_returns.std() * np.sqrt(252) * 100

        # Drawdown
        rolling_max = equity_curve.expanding().max()
        drawdown = (equity_curve - rolling_max) / rolling_max * 100
        max_drawdown_pct = drawdown.min()

        # Drawdown duration
        is_underwater = drawdown < 0
        underwater_periods = []
        current_period = 0

        for underwater in is_underwater:
            if underwater:
                current_period += 1
            else:
                if current_period > 0:
                    underwater_periods.append(current_period)
                current_period = 0

        max_drawdown_duration = max(underwater_periods) if underwater_periods else 0

        # Risk-adjusted returns
        sharpe_ratio = self._calculate_adjusted_sharpe(returns, self.risk_free_rate)

        sortino_ratio = ((annualized_return / 100 - self.risk_free_rate) /
                        (downside_deviation / 100)) if downside_deviation > 0 else 0

        calmar_ratio = (cagr / 100 / abs(max_drawdown_pct / 100)) if max_drawdown_pct != 0 else 0

        # Omega ratio
        omega_ratio = self._calculate_omega_ratio(returns, self.risk_free_rate / 252)

        # Information ratio (vs risk-free rate as benchmark)
        excess_returns = returns - (self.risk_free_rate / 252)
        information_ratio = (excess_returns.mean() * 252 / excess_returns.std() /
                           np.sqrt(252)) if excess_returns.std() > 0 else 0

        # Trading statistics
        total_trades = len(self.trades)

        if total_trades > 0:
            winning_trades = len([t for t in self.trades if t.net_pnl > 0])
            losing_trades = len([t for t in self.trades if t.net_pnl <= 0])
            win_rate = (winning_trades / total_trades) * 100

            wins = [t.net_pnl for t in self.trades if t.net_pnl > 0]
            losses = [t.net_pnl for t in self.trades if t.net_pnl <= 0]

            avg_win = np.mean(wins) if wins else 0
            avg_loss = np.mean(losses) if losses else 0
            avg_win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0

            largest_win = max(wins) if wins else 0
            largest_loss = min(losses) if losses else 0

            total_wins = sum(wins)
            total_losses = abs(sum(losses))
            profit_factor = total_wins / total_losses if total_losses > 0 else 0

            # Costs
            total_commissions = sum(t.commissions for t in self.trades)
            total_slippage = sum(t.slippage for t in self.trades)
            total_market_impact = 0  # Included in slippage
            total_costs = total_commissions + total_slippage
            cost_drag_pct = (total_costs / initial) * 100

            # MAE/MFE
            avg_mae = np.mean([t.max_adverse_excursion for t in self.trades])
            avg_mfe = np.mean([t.max_favorable_excursion for t in self.trades])
            avg_efficiency = np.mean([t.efficiency for t in self.trades if t.efficiency > 0])

        else:
            winning_trades = losing_trades = 0
            win_rate = 0
            avg_win = avg_loss = avg_win_loss_ratio = 0
            largest_win = largest_loss = 0
            profit_factor = 0
            total_commissions = total_slippage = total_market_impact = total_costs = 0
            cost_drag_pct = 0
            avg_mae = avg_mfe = avg_efficiency = 0

        results = BacktestResults(
            start_date=start_date.to_pydatetime(),
            end_date=end_date.to_pydatetime(),
            total_days=total_days,
            trading_days=trading_days,
            initial_capital=initial,
            final_capital=final,
            peak_capital=peak,
            total_return_pct=total_return_pct,
            annualized_return_pct=annualized_return,
            cagr=cagr,
            volatility=volatility,
            downside_deviation=downside_deviation,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_duration_days=max_drawdown_duration,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            omega_ratio=omega_ratio,
            information_ratio=information_ratio,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_win_loss_ratio=avg_win_loss_ratio,
            largest_win=largest_win,
            largest_loss=largest_loss,
            total_commissions=total_commissions,
            total_slippage=total_slippage,
            total_market_impact=total_market_impact,
            total_costs=total_costs,
            cost_drag_pct=cost_drag_pct,
            avg_mae=avg_mae,
            avg_mfe=avg_mfe,
            avg_efficiency=avg_efficiency,
            trades=self.trades,
            equity_curve=equity_curve,
            drawdown_curve=drawdown
        )

        return results


    def _calculate_adjusted_sharpe(self,
                                   returns: pd.Series,
                                   risk_free_rate: float) -> float:
        """
        Calculate Sharpe ratio adjusted for autocorrelation.

        Standard Sharpe can be misleading with autocorrelated returns.

        Args:
            returns: Return series
            risk_free_rate: Annual risk-free rate

        Returns:
            Adjusted Sharpe ratio
        """
        if len(returns) < 2:
            return 0.0

        # Excess returns
        daily_rf = risk_free_rate / 252
        excess_returns = returns - daily_rf

        # Mean and std
        mean_excess = excess_returns.mean()
        std_excess = excess_returns.std()

        if std_excess == 0:
            return 0.0

        # Calculate autocorrelation (lag-1)
        autocorr = excess_returns.autocorr(lag=1)

        # Adjust for autocorrelation
        # If positive autocorrelation, returns are smoother than they should be
        # Need to increase volatility estimate
        if not np.isnan(autocorr) and autocorr > 0:
            adjustment_factor = np.sqrt(1 + 2 * autocorr)
            adjusted_std = std_excess * adjustment_factor
        else:
            adjusted_std = std_excess

        # Annualized Sharpe
        sharpe = (mean_excess / adjusted_std) * np.sqrt(252)

        return sharpe


    def _calculate_omega_ratio(self,
                               returns: pd.Series,
                               threshold: float = 0.0) -> float:
        """
        Calculate Omega ratio.

        Omega = (Probability-weighted gains above threshold) /
                (Probability-weighted losses below threshold)

        Args:
            returns: Return series
            threshold: Threshold return (daily)

        Returns:
            Omega ratio
        """
        excess = returns - threshold

        gains = excess[excess > 0].sum()
        losses = -excess[excess < 0].sum()

        if losses == 0:
            return np.inf if gains > 0 else 1.0

        omega = gains / losses

        return omega


    # ═══════════════════════════════════════════════════════════════════════════
    # WALK-FORWARD ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════

    def walk_forward_analysis(self,
                             data: pd.DataFrame,
                             strategy_func: Callable,
                             train_days: int = 252,
                             test_days: int = 63,
                             refit_days: int = 21,
                             **strategy_params) -> WalkForwardResults:
        """
        Run walk-forward analysis.

        Train on in-sample data, test on out-of-sample data, roll forward.

        Args:
            data: Price data
            strategy_func: Strategy function
            train_days: Training period length (default 252 = 1 year)
            test_days: Testing period length (default 63 = 1 quarter)
            refit_days: How often to refit (default 21 = 1 month)
            **strategy_params: Strategy parameters

        Returns:
            WalkForwardResults
        """
        logger.info(f"Running walk-forward analysis: train={train_days}d, test={test_days}d, refit={refit_days}d")

        windows = []
        current_start = 0

        while current_start + train_days + test_days <= len(data):
            # Split data
            train_end = current_start + train_days
            test_end = train_end + test_days

            train_data = data.iloc[current_start:train_end]
            test_data = data.iloc[train_end:test_end]

            # Run backtest on training data
            train_results = self.run_backtest(train_data, strategy_func, **strategy_params)

            # Run backtest on testing data
            test_results = self.run_backtest(test_data, strategy_func, **strategy_params)

            window_result = {
                'window_num': len(windows) + 1,
                'train_start': train_data.index[0],
                'train_end': train_data.index[-1],
                'test_start': test_data.index[0],
                'test_end': test_data.index[-1],
                'train_sharpe': train_results.sharpe_ratio,
                'test_sharpe': test_results.sharpe_ratio,
                'train_return': train_results.total_return_pct,
                'test_return': test_results.total_return_pct,
                'train_max_dd': train_results.max_drawdown_pct,
                'test_max_dd': test_results.max_drawdown_pct
            }

            windows.append(window_result)

            logger.info(f"Window {len(windows)}: Train Sharpe={train_results.sharpe_ratio:.2f}, " +
                       f"Test Sharpe={test_results.sharpe_ratio:.2f}")

            # Roll forward
            current_start += refit_days

        # Calculate aggregate metrics
        train_sharpes = [w['train_sharpe'] for w in windows]
        test_sharpes = [w['test_sharpe'] for w in windows]

        avg_in_sample_sharpe = np.mean(train_sharpes)
        avg_out_sample_sharpe = np.mean(test_sharpes)

        # Check for overfitting
        correlation = np.corrcoef(train_sharpes, test_sharpes)[0, 1]
        is_overfitted = (avg_out_sample_sharpe < avg_in_sample_sharpe * 0.7)  # >30% degradation

        # Run overall backtest
        overall_results = self.run_backtest(data, strategy_func, **strategy_params)

        wf_results = WalkForwardResults(
            windows=windows,
            avg_in_sample_sharpe=avg_in_sample_sharpe,
            avg_out_sample_sharpe=avg_out_sample_sharpe,
            in_sample_out_sample_correlation=correlation,
            is_overfitted=is_overfitted,
            overall_results=overall_results
        )

        logger.info(f"Walk-forward complete: {len(windows)} windows, " +
                   f"Avg IS Sharpe={avg_in_sample_sharpe:.2f}, " +
                   f"Avg OOS Sharpe={avg_out_sample_sharpe:.2f}, " +
                   f"Overfitted={is_overfitted}")

        return wf_results


    # ═══════════════════════════════════════════════════════════════════════════
    # MONTE CARLO SIMULATION
    # ═══════════════════════════════════════════════════════════════════════════

    def monte_carlo_simulation(self,
                              results: BacktestResults,
                              num_simulations: int = 10000) -> MonteCarloResults:
        """
        Run Monte Carlo simulation on backtest results.

        Randomly reorders trades to simulate different scenarios.

        Args:
            results: Backtest results
            num_simulations: Number of simulations to run

        Returns:
            MonteCarloResults
        """
        logger.info(f"Running Monte Carlo simulation with {num_simulations} iterations")

        if not results.trades:
            logger.warning("No trades to simulate")
            return MonteCarloResults(
                num_simulations=0,
                final_capital_mean=results.initial_capital,
                final_capital_std=0,
                final_capital_median=results.initial_capital,
                final_capital_5th_percentile=results.initial_capital,
                final_capital_95th_percentile=results.initial_capital,
                probability_of_profit=0,
                probability_of_loss=1,
                expected_value=results.initial_capital,
                worst_case_1pct=results.initial_capital,
                best_case_99pct=results.initial_capital,
                simulation_results=[]
            )

        # Extract trade P&Ls
        trade_pnls = [trade.net_pnl_pct for trade in results.trades]

        simulation_finals = []

        for sim in range(num_simulations):
            # Randomly reorder trades
            shuffled_pnls = np.random.choice(trade_pnls, size=len(trade_pnls), replace=True)

            # Calculate final capital
            capital = results.initial_capital

            for pnl_pct in shuffled_pnls:
                capital *= (1 + pnl_pct / 100)

            simulation_finals.append(capital)

        # Calculate statistics
        simulation_finals = np.array(simulation_finals)

        final_capital_mean = simulation_finals.mean()
        final_capital_std = simulation_finals.std()
        final_capital_median = np.median(simulation_finals)
        final_capital_5th = np.percentile(simulation_finals, 5)
        final_capital_95th = np.percentile(simulation_finals, 95)

        probability_of_profit = (simulation_finals > results.initial_capital).mean()
        probability_of_loss = (simulation_finals < results.initial_capital).mean()

        expected_value = final_capital_mean
        worst_case_1pct = np.percentile(simulation_finals, 1)
        best_case_99pct = np.percentile(simulation_finals, 99)

        mc_results = MonteCarloResults(
            num_simulations=num_simulations,
            final_capital_mean=final_capital_mean,
            final_capital_std=final_capital_std,
            final_capital_median=final_capital_median,
            final_capital_5th_percentile=final_capital_5th,
            final_capital_95th_percentile=final_capital_95th,
            probability_of_profit=probability_of_profit,
            probability_of_loss=probability_of_loss,
            expected_value=expected_value,
            worst_case_1pct=worst_case_1pct,
            best_case_99pct=best_case_99pct,
            simulation_results=simulation_finals.tolist()
        )

        logger.info(f"Monte Carlo complete: E[Final]=${expected_value:,.0f}, " +
                   f"P(profit)={probability_of_profit:.1%}")

        return mc_results


    # ═══════════════════════════════════════════════════════════════════════════
    # CROSS-VALIDATION
    # ═══════════════════════════════════════════════════════════════════════════

    def time_series_cross_validation(self,
                                     data: pd.DataFrame,
                                     strategy_func: Callable,
                                     k_folds: int = 5,
                                     **strategy_params) -> List[BacktestResults]:
        """
        Time-series cross-validation.

        Splits data into k folds while preserving temporal order.

        Args:
            data: Price data
            strategy_func: Strategy function
            k_folds: Number of folds (default 5)
            **strategy_params: Strategy parameters

        Returns:
            List of BacktestResults for each fold
        """
        logger.info(f"Running time-series cross-validation with {k_folds} folds")

        fold_size = len(data) // k_folds
        fold_results = []

        for fold in range(k_folds):
            # Test set: current fold
            test_start = fold * fold_size
            test_end = test_start + fold_size if fold < k_folds - 1 else len(data)
            test_data = data.iloc[test_start:test_end]

            # Train set: all data before test fold
            if test_start > 0:
                train_data = data.iloc[:test_start]

                # Run backtest on train + test
                combined_data = pd.concat([train_data, test_data])
                results = self.run_backtest(combined_data, strategy_func, **strategy_params)
            else:
                # First fold: just test (no training data before)
                results = self.run_backtest(test_data, strategy_func, **strategy_params)

            fold_results.append(results)

            logger.info(f"Fold {fold+1}/{k_folds}: Return={results.total_return_pct:.2f}%, " +
                       f"Sharpe={results.sharpe_ratio:.2f}")

        # Calculate average metrics
        avg_return = np.mean([r.total_return_pct for r in fold_results])
        avg_sharpe = np.mean([r.sharpe_ratio for r in fold_results])

        logger.info(f"Cross-validation complete: Avg Return={avg_return:.2f}%, " +
                   f"Avg Sharpe={avg_sharpe:.2f}")

        return fold_results


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def print_backtest_results(results: BacktestResults):
    """Print formatted backtest results"""
    print("\n" + "═" * 100)
    print("BACKTEST RESULTS".center(100))
    print("═" * 100)

    print("\n📊 OVERVIEW")
    print(f"Period:          {results.start_date.date()} to {results.end_date.date()} ({results.total_days} days)")
    print(f"Initial Capital: ${results.initial_capital:,.2f}")
    print(f"Final Capital:   ${results.final_capital:,.2f}")
    print(f"Peak Capital:    ${results.peak_capital:,.2f}")

    print("\n📈 RETURNS")
    print(f"Total Return:    {results.total_return_pct:+.2f}%")
    print(f"Annual Return:   {results.annualized_return_pct:+.2f}%")
    print(f"CAGR:            {results.cagr:+.2f}%")

    print("\n⚠️  RISK")
    print(f"Volatility:        {results.volatility:.2f}%")
    print(f"Downside Dev:      {results.downside_deviation:.2f}%")
    print(f"Max Drawdown:      {results.max_drawdown_pct:.2f}%")
    print(f"Max DD Duration:   {results.max_drawdown_duration_days} days")

    print("\n📊 RISK-ADJUSTED RETURNS")
    print(f"Sharpe Ratio:      {results.sharpe_ratio:.2f}")
    print(f"Sortino Ratio:     {results.sortino_ratio:.2f}")
    print(f"Calmar Ratio:      {results.calmar_ratio:.2f}")
    print(f"Omega Ratio:       {results.omega_ratio:.2f}")
    print(f"Information Ratio: {results.information_ratio:.2f}")

    print("\n💼 TRADING")
    print(f"Total Trades:    {results.total_trades}")
    print(f"Winning Trades:  {results.winning_trades}")
    print(f"Losing Trades:   {results.losing_trades}")
    print(f"Win Rate:        {results.win_rate:.1f}%")
    print(f"Profit Factor:   {results.profit_factor:.2f}")
    print(f"Avg Win:         ${results.avg_win:,.2f}")
    print(f"Avg Loss:        ${results.avg_loss:,.2f}")
    print(f"Win/Loss Ratio:  {results.avg_win_loss_ratio:.2f}")
    print(f"Largest Win:     ${results.largest_win:,.2f}")
    print(f"Largest Loss:    ${results.largest_loss:,.2f}")

    print("\n💰 COSTS")
    print(f"Total Commissions:   ${results.total_commissions:,.2f}")
    print(f"Total Slippage:      ${results.total_slippage:,.2f}")
    print(f"Total Costs:         ${results.total_costs:,.2f}")
    print(f"Cost Drag:           {results.cost_drag_pct:.2f}%")

    print("\n📉 MAE/MFE ANALYSIS")
    print(f"Avg MAE:         ${results.avg_mae:,.2f}")
    print(f"Avg MFE:         ${results.avg_mfe:,.2f}")
    print(f"Avg Efficiency:  {results.avg_efficiency:.2%}")

    print("\n" + "═" * 100)


if __name__ == "__main__":
    # Example usage
    print("Robust Backtesting Engine Module")
    print("Import this module to use RobustBacktestEngine class")
