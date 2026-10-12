"""
Regulatory Compliance Module for TradingAnalysis_ELITE

This module provides compliance features for:
- SEBI/NSE (India): Circuit breakers, algo ID tracking, audit trail
- SEC/FINRA (US): Wash sale detection, PDT rules, best execution

Author: Quanta Elite System
Date: 2025-12-29
"""

import json
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Set
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class Market(Enum):
    """Supported markets"""
    US = "US"           # NYSE, NASDAQ
    INDIA = "INDIA"     # NSE, BSE


class CircuitBreakerLevel(Enum):
    """SEBI circuit breaker levels"""
    LEVEL_1 = 10   # 10% - 45 min halt
    LEVEL_2 = 15   # 15% - 1h45m halt (before 1pm) or day halt (after 2:30pm)
    LEVEL_3 = 20   # 20% - Trading halted for day


@dataclass
class TradeRecord:
    """Record of a single trade for audit trail"""
    trade_id: str
    ticker: str
    market: Market
    signal: str
    action: str           # BUY, SELL
    quantity: int
    price: float
    timestamp: datetime
    algo_id: Optional[str] = None
    order_id: Optional[str] = None
    execution_venue: Optional[str] = None
    total_value: float = 0.0
    
    def __post_init__(self):
        self.total_value = self.quantity * self.price


@dataclass
class WashSaleAlert:
    """Wash sale rule violation alert"""
    ticker: str
    sell_date: datetime
    sell_price: float
    buy_date: datetime
    buy_price: float
    loss_disallowed: float
    days_between: int


@dataclass
class PDTStatus:
    """Pattern Day Trader status"""
    is_pdt: bool = False
    day_trades_last_5_days: int = 0
    account_value: float = 0.0
    pdt_threshold: float = 25000.0
    warning_message: Optional[str] = None


class ComplianceManager:
    """
    Regulatory compliance manager for multi-market trading.
    
    Features:
    - SEBI/NSE: Circuit breaker detection, audit trail, algo ID
    - SEC/FINRA: Wash sale tracking, PDT detection
    """
    
    # SEBI Circuit Breaker Halt Durations (minutes)
    SEBI_HALT_DURATIONS = {
        CircuitBreakerLevel.LEVEL_1: 45,    # 10% move
        CircuitBreakerLevel.LEVEL_2: 105,   # 15% move (1h45m before 1pm)
        CircuitBreakerLevel.LEVEL_3: 999,   # 20% move (day halt)
    }
    
    # SEC Wash Sale Window (days)
    WASH_SALE_WINDOW = 30
    
    # PDT Rules
    PDT_DAY_TRADE_LIMIT = 3      # Max day trades in 5 days for non-PDT
    PDT_ACCOUNT_MINIMUM = 25000  # Minimum account value for PDT
    
    def __init__(self, audit_file: str = None):
        """
        Initialize compliance manager.
        
        Args:
            audit_file: Path to audit trail JSON file
        """
        self.audit_file = audit_file or "compliance_audit.json"
        self.trade_history: List[TradeRecord] = []
        self.wash_sale_alerts: List[WashSaleAlert] = []
        self.algo_id = self._generate_algo_id()
        
        # Load existing audit trail
        self._load_audit_trail()
    
    def _generate_algo_id(self) -> str:
        """Generate unique algorithm ID for SEBI compliance."""
        # Format: ALGO-YYYYMMDD-XXXXXX
        return f"ALGO-{datetime.now().strftime('%Y%m%d')}-{os.getpid():06d}"
    
    # =========================================================================
    # SEBI/NSE COMPLIANCE (India)
    # =========================================================================
    
    def check_circuit_breaker(self, ticker: str, current_price: float, 
                               previous_close: float) -> Optional[CircuitBreakerLevel]:
        """
        Check if a stock has hit SEBI circuit breaker limits.
        
        SEBI mandates circuit breakers at 10%, 15%, and 20% for indices
        and individual stocks based on their group classification.
        
        Args:
            ticker: Stock ticker (e.g., "RELIANCE.NS")
            current_price: Current trading price
            previous_close: Previous day's close
            
        Returns:
            CircuitBreakerLevel if breaker triggered, None otherwise
        """
        if previous_close <= 0:
            return None
        
        price_change_pct = abs((current_price - previous_close) / previous_close) * 100
        
        if price_change_pct >= 20:
            logger.warning(f"🚨 CIRCUIT BREAKER L3: {ticker} moved {price_change_pct:.1f}% - TRADING HALTED")
            return CircuitBreakerLevel.LEVEL_3
        elif price_change_pct >= 15:
            logger.warning(f"⚠️ CIRCUIT BREAKER L2: {ticker} moved {price_change_pct:.1f}% - 1h45m halt")
            return CircuitBreakerLevel.LEVEL_2
        elif price_change_pct >= 10:
            logger.info(f"⚠️ CIRCUIT BREAKER L1: {ticker} moved {price_change_pct:.1f}% - 45min halt")
            return CircuitBreakerLevel.LEVEL_1
        
        return None
    
    def is_nse_ticker(self, ticker: str) -> bool:
        """Check if ticker is an NSE (India) stock."""
        return ticker.upper().endswith('.NS') or ticker.upper().endswith('.BO')
    
    def get_sebi_halt_duration(self, level: CircuitBreakerLevel, 
                                current_time: datetime = None) -> int:
        """
        Get halt duration in minutes based on SEBI rules.
        
        For Level 2, duration depends on time of day:
        - Before 1pm: 1h45m halt
        - 1pm-2:30pm: 45min halt
        - After 2:30pm: Day halt
        """
        if current_time is None:
            current_time = datetime.now()
        
        if level == CircuitBreakerLevel.LEVEL_2:
            hour = current_time.hour
            if hour < 13:
                return 105  # 1h45m
            elif hour < 14 or (hour == 14 and current_time.minute < 30):
                return 45
            else:
                return 999  # Day halt
        
        return self.SEBI_HALT_DURATIONS.get(level, 0)
    
    # =========================================================================
    # SEC/FINRA COMPLIANCE (US)
    # =========================================================================
    
    def check_wash_sale(self, ticker: str, sell_date: datetime, 
                         sell_price: float, quantity: int) -> Optional[WashSaleAlert]:
        """
        Check for wash sale rule violations (IRS Section 1091).
        
        A wash sale occurs when you sell a security at a loss and buy
        substantially identical security within 30 days before or after.
        
        Args:
            ticker: Stock ticker
            sell_date: Date of sale
            sell_price: Sale price
            quantity: Shares sold
            
        Returns:
            WashSaleAlert if violation detected
        """
        window_start = sell_date - timedelta(days=self.WASH_SALE_WINDOW)
        window_end = sell_date + timedelta(days=self.WASH_SALE_WINDOW)
        
        # Find recent buys of same ticker
        for trade in self.trade_history:
            if (trade.ticker.upper() == ticker.upper() and 
                trade.action == "BUY" and
                window_start <= trade.timestamp <= window_end and
                trade.timestamp != sell_date):
                
                # Calculate disallowed loss
                if sell_price < trade.price:
                    loss_disallowed = (trade.price - sell_price) * min(quantity, trade.quantity)
                    days_between = abs((trade.timestamp - sell_date).days)
                    
                    alert = WashSaleAlert(
                        ticker=ticker,
                        sell_date=sell_date,
                        sell_price=sell_price,
                        buy_date=trade.timestamp,
                        buy_price=trade.price,
                        loss_disallowed=loss_disallowed,
                        days_between=days_between
                    )
                    
                    self.wash_sale_alerts.append(alert)
                    logger.warning(
                        f"⚠️ WASH SALE ALERT: {ticker} - ${loss_disallowed:.2f} loss disallowed "
                        f"(repurchased {days_between} days {'before' if trade.timestamp < sell_date else 'after'})"
                    )
                    return alert
        
        return None
    
    def check_pdt_status(self, account_value: float, 
                          day_trades: List[datetime] = None) -> PDTStatus:
        """
        Check Pattern Day Trader (PDT) status under FINRA Rule 4210.
        
        PDT rules:
        - 4+ day trades in 5 business days = PDT designation
        - PDT accounts require $25,000 minimum equity
        - Non-PDT with <$25k limited to 3 day trades per 5 days
        
        Args:
            account_value: Current account equity
            day_trades: List of day trade timestamps
            
        Returns:
            PDTStatus with current status and warnings
        """
        if day_trades is None:
            # Count day trades from history
            day_trades = self._get_day_trades_last_5_days()
        
        day_trade_count = len(day_trades)
        
        status = PDTStatus(
            day_trades_last_5_days=day_trade_count,
            account_value=account_value,
            pdt_threshold=self.PDT_ACCOUNT_MINIMUM
        )
        
        # Check if already PDT
        if day_trade_count >= 4:
            status.is_pdt = True
            if account_value < self.PDT_ACCOUNT_MINIMUM:
                status.warning_message = (
                    f"⚠️ PDT VIOLATION: {day_trade_count} day trades but account "
                    f"(${account_value:,.0f}) below ${self.PDT_ACCOUNT_MINIMUM:,.0f} minimum"
                )
                logger.warning(status.warning_message)
        elif day_trade_count == 3 and account_value < self.PDT_ACCOUNT_MINIMUM:
            status.warning_message = (
                f"⚠️ PDT WARNING: At day trade limit ({day_trade_count}/3). "
                f"One more triggers PDT with account below minimum."
            )
        
        return status
    
    def _get_day_trades_last_5_days(self) -> List[datetime]:
        """Get list of day trades in last 5 business days."""
        cutoff = datetime.now() - timedelta(days=7)  # ~5 business days
        day_trades = []
        
        # Group trades by ticker and date
        trades_by_day = {}
        for trade in self.trade_history:
            if trade.timestamp >= cutoff:
                key = (trade.ticker, trade.timestamp.date())
                if key not in trades_by_day:
                    trades_by_day[key] = {'BUY': [], 'SELL': []}
                trades_by_day[key][trade.action].append(trade)
        
        # Day trade = buy and sell same security same day
        for (ticker, date), actions in trades_by_day.items():
            if actions['BUY'] and actions['SELL']:
                day_trades.append(datetime.combine(date, datetime.min.time()))
        
        return day_trades
    
    # =========================================================================
    # AUDIT TRAIL (Both Markets)
    # =========================================================================
    
    def record_trade(self, ticker: str, signal: str, action: str,
                      quantity: int, price: float,
                      market: Market = Market.US,
                      order_id: str = None,
                      execution_venue: str = None) -> TradeRecord:
        """
        Record an EXECUTED trade for the audit trail (SEBI 5-year requirement).

        Args:
            ticker: Stock ticker
            signal: Signal that triggered trade (STRONG_BUY, etc.)
            action: BUY or SELL
            quantity: Number of shares
            price: Execution price
            market: Market (US or INDIA)
            order_id: Broker order/execution reference. REQUIRED — an audit
                record asserts that a fill happened, so it must cite one.
            execution_venue: Venue the fill printed on, when known.

        Returns:
            TradeRecord

        Raises:
            ValueError: if no ``order_id`` is supplied.

        [F02] The research path used to call this after every BUY/SELL
        recommendation, manufacturing audit-trail entries — and downstream
        wash-sale and pattern-day-trader findings — for trades that were never
        placed. Requiring an execution reference makes that mistake fail loudly
        instead of silently corrupting the trade history.
        """
        if not order_id:
            raise ValueError(
                f"record_trade({ticker}, {action}) requires an execution "
                f"reference (order_id). Recommendations are not executions and "
                f"must not be written to the compliance audit trail."
            )
        trade = TradeRecord(
            trade_id=f"TRD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(self.trade_history):04d}",
            ticker=ticker,
            market=market,
            signal=signal,
            action=action,
            quantity=quantity,
            price=price,
            timestamp=datetime.now(),
            algo_id=self.algo_id if market == Market.INDIA else None,
            order_id=order_id,
            execution_venue=execution_venue,
        )
        
        self.trade_history.append(trade)
        self._save_audit_trail()
        
        logger.info(f"📝 Trade recorded: {trade.trade_id} - {action} {quantity} {ticker} @ ${price:.2f}")
        
        # Check compliance
        if action == "SELL":
            self.check_wash_sale(ticker, trade.timestamp, price, quantity)
        
        return trade
    
    def _save_audit_trail(self):
        """Save audit trail to JSON file (SEBI 5-year requirement)."""
        try:
            records = []
            for trade in self.trade_history:
                record = asdict(trade)
                record['timestamp'] = trade.timestamp.isoformat()
                record['market'] = trade.market.value
                records.append(record)
            
            with open(self.audit_file, 'w') as f:
                json.dump({
                    'algo_id': self.algo_id,
                    'last_updated': datetime.now().isoformat(),
                    'trade_count': len(records),
                    'trades': records
                }, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save audit trail: {e}")
    
    def _load_audit_trail(self):
        """Load existing audit trail from file."""
        try:
            if os.path.exists(self.audit_file):
                with open(self.audit_file, 'r') as f:
                    data = json.load(f)
                
                for record in data.get('trades', []):
                    trade = TradeRecord(
                        trade_id=record['trade_id'],
                        ticker=record['ticker'],
                        market=Market(record['market']),
                        signal=record['signal'],
                        action=record['action'],
                        quantity=record['quantity'],
                        price=record['price'],
                        timestamp=datetime.fromisoformat(record['timestamp']),
                        algo_id=record.get('algo_id')
                    )
                    self.trade_history.append(trade)
                
                logger.info(f"Loaded {len(self.trade_history)} trades from audit trail")
                
        except Exception as e:
            logger.warning(f"Could not load audit trail: {e}")
    
    def get_compliance_summary(self, account_value: float = 100000) -> Dict:
        """
        Get comprehensive compliance status summary.
        
        Returns:
            Dict with compliance status for all regulations
        """
        pdt = self.check_pdt_status(account_value)
        
        return {
            'algo_id': self.algo_id,
            'total_trades_recorded': len(self.trade_history),
            'audit_trail_file': self.audit_file,
            'pdt_status': {
                'is_pdt': pdt.is_pdt,
                'day_trades_5_days': pdt.day_trades_last_5_days,
                'account_value': account_value,
                'warning': pdt.warning_message
            },
            'wash_sales': {
                'alerts_count': len(self.wash_sale_alerts),
                'total_disallowed': sum(w.loss_disallowed for w in self.wash_sale_alerts)
            },
            'sebi_compliance': {
                'algo_id_registered': bool(self.algo_id),
                'audit_trail_active': os.path.exists(self.audit_file)
            }
        }
    
    def print_compliance_report(self, account_value: float = 100000):
        """Print formatted compliance report."""
        summary = self.get_compliance_summary(account_value)
        
        print("\n" + "=" * 60)
        print("📋 REGULATORY COMPLIANCE REPORT")
        print("=" * 60)
        
        print(f"\n🔑 Algo ID: {summary['algo_id']}")
        print(f"📁 Audit Trail: {summary['audit_trail_file']}")
        print(f"📊 Total Trades: {summary['total_trades_recorded']}")
        
        print("\n--- SEC/FINRA (US) ---")
        pdt = summary['pdt_status']
        pdt_icon = "🔴" if pdt['is_pdt'] and account_value < 25000 else "🟢"
        print(f"{pdt_icon} PDT Status: {'Yes' if pdt['is_pdt'] else 'No'}")
        print(f"   Day Trades (5 days): {pdt['day_trades_5_days']}/3")
        if pdt['warning']:
            print(f"   ⚠️ {pdt['warning']}")
        
        ws = summary['wash_sales']
        ws_icon = "🔴" if ws['alerts_count'] > 0 else "🟢"
        print(f"{ws_icon} Wash Sales: {ws['alerts_count']} alerts (${ws['total_disallowed']:,.2f} disallowed)")
        
        print("\n--- SEBI/NSE (India) ---")
        sebi = summary['sebi_compliance']
        print(f"{'🟢' if sebi['algo_id_registered'] else '🔴'} Algo ID Registered: {sebi['algo_id_registered']}")
        print(f"{'🟢' if sebi['audit_trail_active'] else '🔴'} Audit Trail Active: {sebi['audit_trail_active']}")
        
        print("=" * 60)


# Convenience function
def get_compliance_manager(audit_file: str = None) -> ComplianceManager:
    """Get or create global compliance manager instance."""
    return ComplianceManager(audit_file)


if __name__ == "__main__":
    # The report below is drawn with emoji. On Windows a direct
    # `python modules/compliance_manager.py` gets a cp1252 stdout whenever the
    # stream is a pipe or a redirected log, and the first banner line raised
    # UnicodeEncodeError before any compliance output appeared. Same fix the
    # other CLI entry points use (see SP_ELITE_PRO._force_utf8_streams).
    for _stream_name in ("stdout", "stderr"):
        _reconfigure = getattr(getattr(sys, _stream_name, None), "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # Demo
    cm = ComplianceManager()
    
    # Record some EXECUTED trades. Every audit entry must cite the broker
    # execution reference that proves a fill happened -- see record_trade.
    cm.record_trade("AAPL", "STRONG_BUY", "BUY", 100, 175.50,
                    order_id="DEMO-0001", execution_venue="DEMO")
    cm.record_trade("TSLA", "ACCUMULATE", "BUY", 50, 250.00,
                    order_id="DEMO-0002", execution_venue="DEMO")
    
    # Check circuit breaker
    cb = cm.check_circuit_breaker("RELIANCE.NS", 2950.0, 2650.0)  # 11.3% move
    
    # Print report
    cm.print_compliance_report(account_value=50000)
