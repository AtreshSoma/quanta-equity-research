""""
AI Stock Analyst - Elite Hedge Fund Edition V4.0 ULTIMATE
Professional-grade implementation with API INTEGRATION

NEW IN V4.0:
- Full API integration (FMP, Finnhub, Polygon)
- Insider trading analysis
- Institutional ownership tracking
- Enhanced news with AI sentiment
- Cross-validation with multiple data sources
- Improved scoring with API data
- All V3.3 enhancements included

CRITICAL FEATURES:
1. Multi-source data validation
2. Insider & institutional activity scoring
3. Enhanced news sentiment from Finnhub
4. Better fundamental analysis with API data
5. Comprehensive risk assessment
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
import importlib
warnings.filterwarnings('ignore')

# Predeclare optional imports so static analysis won't warn
EnhancedAPIManager = None
RATE_LIMITER = None
AdvancedPriceTargetCalculator = None
calculate_advanced_targets = None

# Import API Manager using importlib to avoid try/except import analyzer warnings
if importlib.util.find_spec('api_manager') is not None:
    from api_manager import EnhancedAPIManager
    try:
        from config import RATE_LIMITER
    except Exception:
        RATE_LIMITER = None
    API_AVAILABLE = True
else:
    EnhancedAPIManager = None
    RATE_LIMITER = None
    API_AVAILABLE = False

# Import Advanced Price Target Calculator using importlib
if importlib.util.find_spec('price_target_calculator') is not None:
    from price_target_calculator import AdvancedPriceTargetCalculator, calculate_advanced_targets
    ADVANCED_TARGETS_AVAILABLE = True
else:
    AdvancedPriceTargetCalculator = None
    calculate_advanced_targets = None
    ADVANCED_TARGETS_AVAILABLE = False

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False


class OutputFormatter:
    """Elite formatting for institutional-grade reports."""

    @staticmethod
    def print_header(title, width=90):
        print("\n" + "=" * width)
        print(title.center(width))
        print("=" * width)

    @staticmethod
    def print_subheader(title, width=90):
        print("\n" + "-" * width)
        print(title)
        print("-" * width)

    @staticmethod
    def print_metric(label, value, width=50, indent=2):
        spaces = " " * indent
        dots = "." * (width - len(label) - len(str(value)) - indent)
        print(f"{spaces}{label} {dots} {value}")

    @staticmethod
    def print_score_bar(score, label="Score", width=50):
        filled = int((score / 100) * width)
        bar = "█" * filled + "░" * (width - filled)

        if score >= 80:
            color_indicator = "[🟢 EXCELLENT]"
        elif score >= 60:
            color_indicator = "[🟢 GOOD]"
        elif score >= 40:
            color_indicator = "[🟡 FAIR]"
        elif score >= 20:
            color_indicator = "[🔴 POOR]"
        else:
            color_indicator = "[🔴 CRITICAL]"

        print(f"\n{label}: {score:.1f}/100 {color_indicator}")
        print(f"[{bar}] {score:.1f}%")

    @staticmethod
    def print_table(headers, rows, col_widths=None):
        if col_widths is None:
            col_widths = [20] * len(headers)

        header_str = " | ".join([h.ljust(w) for h, w in zip(headers, col_widths)])
        print(f"\n{header_str}")
        print("-" * len(header_str))

        for row in rows:
            row_str = " | ".join([str(cell).ljust(w) for cell, w in zip(row, col_widths)])
            print(row_str)

    @staticmethod
    def print_bullet_list(items, indent=2):
        spaces = " " * indent
        for item in items:
            print(f"{spaces}• {item}")

    @staticmethod
    def print_alert_box(title, items, severity="WARNING", width=90):
        if severity == "CRITICAL":
            border = "🚨" * 45
            print("\n" + border)
            print(f"⚠️  {title} ⚠️".center(width))
            print(border)
        elif severity == "POSITIVE":
            border = "✨" * 45
            print("\n" + border)
            print(f"🚀 {title} 🚀".center(width))
            print(border)
        else:
            border = "⚠️ " * 45
            print("\n" + border)
            print(f"⚠️  {title}".center(width))
            print(border)

        for item in items:
            print(f"  • {item}")
        print(border[:90])

    @staticmethod
    def print_price_target_box(current, entry, stop_loss, targets, currency="$", width=90):
        """Display professional price targets."""
        print("\n" + "=" * width)
        print("💰 PRICE TARGETS & ENTRY/EXIT LEVELS".center(width))
        print("=" * width)

        print(f"\n  Current Price: {currency}{current:.2f}")
        print(f"  Entry Price:   {currency}{entry:.2f} ({((entry - current) / current * 100):+.1f}% from current)")
        print(f"  Stop Loss:     {currency}{stop_loss:.2f} ({((stop_loss - current) / current * 100):+.1f}% from current)")

        print("\n  Price Targets:")
        for i, (target, probability) in enumerate(targets, 1):
            upside = ((target - current) / current * 100)
            print(f"    Target {i}: {currency}{target:.2f} ({upside:+.1f}% upside) - {probability}% probability")

        print("\n" + "=" * width)


class AIStockAnalyst:
    """Elite AI Stock Analyst with FULL API INTEGRATION."""

    # US Sector P/E ranges
    US_SECTOR_PE_RANGES = {
        'Technology': (20, 40),
        'Healthcare': (15, 30),
        'Industrials': (15, 25),
        'Financials': (10, 18),
        'Financial Services': (10, 18),
        'Consumer Cyclical': (15, 28),
        'Consumer Defensive': (18, 25),
        'Energy': (10, 20),
        'Utilities': (15, 22),
        'Real Estate': (20, 35),
        'Basic Materials': (12, 22),
        'Communication Services': (15, 30),
    }

    # Indian Sector P/E ranges
    INDIA_SECTOR_PE_RANGES = {
        'Technology': (25, 45),
        'Healthcare': (20, 40),
        'Industrials': (20, 35),
        'Financials': (15, 25),
        'Financial Services': (15, 25),
        'Consumer Cyclical': (20, 40),
        'Consumer Defensive': (25, 45),
        'Energy': (12, 25),
        'Utilities': (18, 28),
        'Real Estate': (15, 30),
        'Basic Materials': (15, 30),
        'Communication Services': (20, 35),
    }

    def __init__(self, ticker):
        self.ticker = ticker.upper()
        self.stock = None
        self.data = None
        self.info = None
        self.formatter = OutputFormatter()

        # Detect market
        self.market = self._detect_market()
        self.currency = "₹" if self.market == "INDIA" else "$"
        self.benchmark_ticker = "^NSEI" if self.market == "INDIA" else "^GSPC"
        self.benchmark_name = "NIFTY 50" if self.market == "INDIA" else "S&P 500"
        self.secondary_ticker = "^NSEBANK" if self.market == "INDIA" else "^IXIC"
        self.secondary_name = "Bank NIFTY" if self.market == "INDIA" else "NASDAQ"
        self.sector_pe_ranges = self.INDIA_SECTOR_PE_RANGES if self.market == "INDIA" else self.US_SECTOR_PE_RANGES

        # Analysis results
        self.technical_score = 0
        self.fundamental_score = 0
        self.sentiment_score = 0
        self.market_score = 0
        self.overall_score = 0

        # Metrics storage
        self.technical_metrics = {}
        self.fundamental_metrics = {}
        self.sentiment_metrics = {}
        self.market_metrics = {}

        # Strategic insights
        self.catalysts = []
        self.risks = []
        self.regulatory_risks = []
        self.news_items = []

        # Price targets
        self.current_price = 0
        self.entry_price = 0
        self.stop_loss = 0
        self.price_targets = []

        # Valuation
        self.peg_ratio = None
        self.sector_relative_valuation = None

        # Recent price action
        self.recent_price_action = {}

        # Volatility
        self.volatility_annual = 0
        self.momentum_quality = 0

        # Override tracking
        self.override_active = False
        self.override_reason = []

        # Volume context flags
        self.volume_notes = []

        # API Manager (NEW)
        if API_AVAILABLE:
            self.api_manager = EnhancedAPIManager()
            self.enhanced_data = {}
        else:
            self.api_manager = None
            self.enhanced_data = {}

    def _detect_market(self):
        """Detect if ticker is Indian or US stock."""
        if self.ticker.endswith('.NS') or self.ticker.endswith('.BO'):
            return "INDIA"
        if self.ticker in ['^NSEI', '^BSESN', '^NSEBANK', '^CNXIT', '^CNXPHARMA', '^CNXAUTO']:
            return "INDIA"
        return "US"

    def fetch_stock_data(self, period='1y'):
        """Fetch stock data with enhanced error handling."""
        try:
            print(f"🌍 Market: {self.market} | Ticker: {self.ticker}")
            self.stock = yf.Ticker(self.ticker)
            self.data = self.stock.history(period=period)
            self.info = self.stock.info

            if self.data.empty:
                print(f"\n✗ No data found for '{self.ticker}'")
                if self.market == "INDIA":
                    print(f"\n💡 For Indian stocks, use:")
                    print(f"   NSE: {self.ticker}.NS")
                    print(f"   BSE: {self.ticker}.BO")
                return False

            print(f"✓ Fetched {len(self.data)} days of data for {self.ticker}")
            self.calculate_recent_price_action()
            self.calculate_volatility()
            return True
        except Exception as e:
            print(f"\n✗ Error fetching data: {str(e)}")
            return False

    def fetch_enhanced_data(self):
        """NEW: Fetch data from premium APIs."""
        if not self.api_manager:
            print("⚠️  API Manager not available. Skipping enhanced data fetch.")
            return

        print("\n🚀 Fetching enhanced data from premium APIs...")
        try:
            self.enhanced_data = self.api_manager.get_comprehensive_data(self.ticker)
        except Exception as e:
            print(f"❌ Error fetching enhanced data: {str(e)}")
            self.enhanced_data = {}

    def calculate_recent_price_action(self):
        """Calculate recent price movements."""
        if len(self.data) < 5:
            return

        current = self.data['Close'].iloc[-1]
        self.current_price = current

        yesterday = self.data['Close'].iloc[-2] if len(self.data) >= 2 else current
        five_days = self.data['Close'].iloc[-6] if len(self.data) >= 6 else current
        twenty_days = self.data['Close'].iloc[-21] if len(self.data) >= 21 else current
        sixty_days = self.data['Close'].iloc[-61] if len(self.data) >= 61 else current
        year_ago = self.data['Close'].iloc[0] if len(self.data) >= 250 else current

        self.recent_price_action = {
            'current_price': round(current, 2),
            '1d_change': round(((current - yesterday) / yesterday) * 100, 2),
            '5d_change': round(((current - five_days) / five_days) * 100, 2),
            '20d_change': round(((current - twenty_days) / twenty_days) * 100, 2),
            '60d_change': round(((current - sixty_days) / sixty_days) * 100, 2),
            'ytd_change': round(((current - year_ago) / year_ago) * 100, 2)
        }

    def calculate_volatility(self):
        """Calculate annualized volatility."""
        if len(self.data) < 20:
            self.volatility_annual = 0
            return

        returns = self.data['Close'].pct_change().dropna()
        self.volatility_annual = returns.std() * np.sqrt(252)

    def analyze_momentum_quality(self):
        """ENHANCED: Determine if momentum is sustainable with better logic."""
        ytd_chg = self.recent_price_action.get('ytd_change', 0)
        month_chg = self.recent_price_action.get('20d_change', 0)
        volume_ratio = self.technical_metrics.get('Volume_Ratio', 1)
        rsi = self.technical_metrics.get('RSI', 50)

        quality_score = 50  # Start at neutral

        # CRITICAL: Penalty for overextended moves
        if ytd_chg > 100:
            quality_score -= 15
        elif ytd_chg > 80:
            quality_score -= 10
        elif ytd_chg > 60:
            quality_score -= 5

        # Volatility assessment
        if self.volatility_annual < 0.25:
            quality_score += 20
        elif self.volatility_annual < 0.35:
            quality_score += 12
        elif self.volatility_annual > 0.70:
            quality_score -= 25
        elif self.volatility_annual > 0.50:
            quality_score -= 15

        # Volume confirmation - CRITICAL for large moves
        if ytd_chg > 50:
            if volume_ratio > 2.0:
                quality_score += 20
            elif volume_ratio > 1.5:
                quality_score += 15
            elif volume_ratio > 1.2:
                quality_score += 8
            elif volume_ratio > 0.8:
                quality_score += 0
            elif volume_ratio > 0.5:
                quality_score -= 15
            else:
                quality_score -= 30
        else:
            if volume_ratio > 1.2:
                quality_score += 15
            elif volume_ratio > 0.8:
                quality_score += 5
            elif volume_ratio < 0.4:
                quality_score -= 15

        # RSI positioning
        if 45 <= rsi <= 60:
            quality_score += 15
        elif 35 <= rsi <= 70:
            quality_score += 8
        elif rsi > 80:
            quality_score -= 30
        elif rsi > 75:
            quality_score -= 20
        elif rsi < 25:
            quality_score += 15
        elif rsi < 30:
            quality_score += 10

        # Consistency check
        if ytd_chg > 100 and volume_ratio < 1.0:
            quality_score -= 25
        elif ytd_chg > 80 and month_chg > 15 and volume_ratio > 1.5:
            quality_score += 10
        elif ytd_chg > 100 and month_chg < 5:
            quality_score -= 20

        self.momentum_quality = max(0, min(100, quality_score))

    def analyze_insider_activity(self) -> tuple:
        """NEW: Analyze insider trading and add to fundamental score."""
        if 'insider_trading' not in self.enhanced_data:
            return 0, []

        insider = self.enhanced_data['insider_trading']

        score_impact = insider.get('score_impact', 0)
        signals = []

        sentiment = insider.get('sentiment', 'NEUTRAL')
        buy_count = insider.get('insider_buys', 0)
        sell_count = insider.get('insider_sells', 0)
        net_value = insider.get('net_value', 0)

        if sentiment == "STRONG BUY":
            signals.append(("BULLISH", f"Strong insider buying: {buy_count} buys vs {sell_count} sells"))
            signals.append(("BULLISH", f"Net insider value: ${net_value/1_000_000:.1f}M"))
        elif sentiment == "BULLISH":
            signals.append(("BULLISH", f"Insider buying detected: {buy_count} buys"))
        elif sentiment == "BEARISH":
            signals.append(("BEARISH", f"Heavy insider selling: {sell_count} sells vs {buy_count} buys"))
        elif sentiment == "NEGATIVE":
            signals.append(("WARNING", f"Insider selling: {sell_count} sells"))

        # Show recent transactions
        recent = insider.get('recent_transactions', [])
        if recent:
            print(f"\n📊 Recent Insider Transactions ({insider.get('analysis_period', '6 months')}):")
            for trans in recent[:5]:
                trans_type = "BUY " if 'P-Purchase' in trans.get('type', '') else "SELL"
                name = trans.get('name', 'Unknown')[:30]
                shares = trans.get('shares', 0)
                price = trans.get('price', 0)
                date = trans.get('date', 'Unknown')
                print(f"   {date} | {name:30s} | {trans_type} | {shares:,} shares @ ${price:.2f}")

        return score_impact, signals

    def analyze_institutional_activity(self) -> tuple:
        """NEW: Analyze institutional ownership changes."""
        if 'institutional_ownership' not in self.enhanced_data:
            return 0, []

        inst = self.enhanced_data['institutional_ownership']

        score_impact = inst.get('score_impact', 0)
        signals = []

        sentiment = inst.get('sentiment', 'NEUTRAL')
        increasing = inst.get('institutions_increasing', 0)
        decreasing = inst.get('institutions_decreasing', 0)

        if 'STRONG' in sentiment and 'BUYING' in sentiment:
            signals.append(("BULLISH", f"Strong institutional accumulation: {increasing} institutions increasing"))
        elif 'ACCUMULATION' in sentiment:
            signals.append(("BULLISH", f"Institutional buying: {increasing} increasing positions"))
        elif 'SELLING' in sentiment:
            signals.append(("BEARISH", f"Institutional distribution: {decreasing} institutions reducing"))

        # Show top holders
        top_holders = inst.get('top_holders', [])
        if top_holders:
            print(f"\n🏛️  Top Institutional Holders:")
            for i, holder in enumerate(top_holders[:5], 1):
                name = holder.get('investor', 'Unknown')[:40]
                shares = holder.get('totalShares', 0)
                value = holder.get('totalValue', 0)
                print(f"   {i}. {name:40s} | {shares:,} shares | ${value/1_000_000:.1f}M")

        return score_impact, signals

    def summarize_stock_data(self):
        """Stock overview with critical alerts."""
        if self.data is None or self.data.empty:
            return None

        current_price = self.data['Close'].iloc[-1]
        avg_volume = self.data['Volume'].mean()
        recent_volume = self.data['Volume'].iloc[-1]
        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1

        ma_50 = self.data['Close'].rolling(50).mean().iloc[-1]
        ma_200 = self.data['Close'].rolling(200).mean().iloc[-1] if len(self.data) >= 200 else None

        if ma_200 and current_price > ma_50 > ma_200:
            trend = "Strong Uptrend 📈"
        elif ma_200 and current_price < ma_50 < ma_200:
            trend = "Strong Downtrend 📉"
        elif current_price > ma_50:
            trend = "Uptrend ↗"
        else:
            trend = "Downtrend ↘"

        self.formatter.print_header("📊 STOCK OVERVIEW")

        company_name = self.info.get('longName', self.ticker)
        sector = self.info.get('sector', 'N/A')
        industry = self.info.get('industry', 'N/A')
        market_cap = self.info.get('marketCap', 0)

        print(f"\n🏢 Company: {company_name}")
        if self.market == "INDIA":
            exchange = "NSE" if self.ticker.endswith('.NS') else "BSE" if self.ticker.endswith('.BO') else "INDIA"
            print(f"🌍 Market: INDIA ({exchange})")
        else:
            print(f"🌍 Market: United States")
        print(f"📂 Sector: {sector}")
        print(f"🏭 Industry: {industry}")

        if market_cap > 0:
            if self.market == "INDIA":
                market_cap_cr = market_cap / 10_000_000
                market_cap_b_usd = market_cap / 1_000_000_000
                print(f"💰 Market Cap: ₹{market_cap_cr:,.2f} Cr (${market_cap_b_usd:.2f}B)")
            else:
                market_cap_b = market_cap / 1_000_000_000
                print(f"💰 Market Cap: ${market_cap_b:.2f}B")

        self.formatter.print_metric("Current Price", f"{self.currency}{current_price:.2f}")

        day_change = self.recent_price_action.get('1d_change', 0)
        week_change = self.recent_price_action.get('5d_change', 0)
        month_change = self.recent_price_action.get('20d_change', 0)
        quarter_change = self.recent_price_action.get('60d_change', 0)
        ytd_change = self.recent_price_action.get('ytd_change', 0)

        self.formatter.print_metric("1-Day Change", f"{day_change:+.2f}%")
        self.formatter.print_metric("5-Day Change", f"{week_change:+.2f}%")
        self.formatter.print_metric("20-Day Change", f"{month_change:+.2f}%")
        self.formatter.print_metric("60-Day Change", f"{quarter_change:+.2f}%")
        self.formatter.print_metric("YTD Change", f"{ytd_change:+.2f}%")

        # Critical alerts
        alerts = []
        if day_change < -10:
            alerts.append(f"🚨 CRASHED {abs(day_change):.1f}% TODAY - EXTREME WEAKNESS")
        elif day_change < -5:
            alerts.append(f"⚠️  DOWN {abs(day_change):.1f}% TODAY - SIGNIFICANT DECLINE")

        # Enhanced volume alerts
        if volume_ratio > 3:
            if day_change < -5:
                alerts.append(f"🚨 PANIC SELLING: {volume_ratio:.1f}x VOLUME ON DECLINE")
            elif day_change > 5:
                alerts.append(f"🔥 INSTITUTIONAL BUYING: {volume_ratio:.1f}x VOLUME SURGE")
            else:
                alerts.append(f"📊 High Volume Activity: {volume_ratio:.1f}x average")
        elif volume_ratio > 2:
            alerts.append(f"📊 Elevated Volume: {volume_ratio:.1f}x average")
        elif volume_ratio < 0.3:
            if ytd_change > 100:
                alerts.append(f"⚠️  Low Volume ({volume_ratio:.2f}x) after big move - RED FLAG for retail pump")
                self.volume_notes.append("CRITICAL: Low volume on extreme gains suggests lack of institutional support")
            elif ytd_change > 50:
                alerts.append(f"⚠️  Low Volume ({volume_ratio:.2f}x) - Verify with broker platform")
                self.volume_notes.append("Low volume despite gains - check for genuine institutional participation")
            else:
                alerts.append(f"📉 Low Volume: {volume_ratio:.2f}x - Limited trading interest")

        if alerts:
            print("\n🔔 CRITICAL ALERTS:")
            for alert in alerts:
                print(f"   {alert}")

        self.formatter.print_metric("50-Day MA", f"{self.currency}{ma_50:.2f}")
        if ma_200:
            self.formatter.print_metric("200-Day MA", f"{self.currency}{ma_200:.2f}")
        self.formatter.print_metric("Trend", trend)
        self.formatter.print_metric("Volume Ratio", f"{volume_ratio:.2f}x")
        self.formatter.print_metric("Annualized Volatility", f"{self.volatility_annual*100:.1f}%")

        return {
            'company_name': company_name,
            'sector': sector,
            'current_price': round(current_price, 2),
            'day_change': day_change,
            'week_change': week_change,
            'ytd_change': ytd_change,
            'volume_ratio': round(volume_ratio, 2)
        }

    def technical_analysis(self):
        """ENHANCED: Technical analysis with better weighting and context."""
        self.formatter.print_header("📈 TECHNICAL ANALYSIS")

        # Use pandas Series directly for clearer types
        close_series = self.data['Close'] if isinstance(self.data['Close'], pd.Series) else pd.Series(self.data['Close'])
        close = close_series.values
        volume = self.data['Volume'].values

        score = 50
        signals = []
        technical_data = []

        # Recent price action impact
        day_change = self.recent_price_action.get('1d_change', 0)
        week_change = self.recent_price_action.get('5d_change', 0)
        month_change = self.recent_price_action.get('20d_change', 0)
        ytd_change = self.recent_price_action.get('ytd_change', 0)

        # ENHANCED: Price momentum scoring with better context
        if day_change < -10:
            signals.append(("CRITICAL", f"Crashed {abs(day_change):.1f}% today"))
            score -= 30
        elif day_change < -5:
            signals.append(("BEARISH", f"Down {abs(day_change):.1f}% today"))
            score -= 18
        elif day_change > 5:
            if ytd_change > 100:
                signals.append(("WARNING", f"Up {day_change:.1f}% but overextended"))
                score += 5
            else:
                signals.append(("BULLISH", f"Up {day_change:.1f}% today"))
                score += 12

        if month_change > 20 and ytd_change > 100:
            score += 5
            signals.append(("WARNING", "Parabolic move - overbought risk"))
        elif month_change > 15:
            score += 12
            signals.append(("BULLISH", "Strong monthly momentum"))
        elif month_change < -15:
            score -= 12
            signals.append(("BEARISH", "Weak monthly momentum"))

        # RSI - operate on close_series to keep pandas types
        delta = close_series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rs = pd.Series(rs)
        rsi_series = 100 - (100 / (1 + rs))

        self.technical_metrics['RSI'] = round(rsi_series.iloc[-1], 2)

        # ENHANCED: RSI scoring with context
        if rsi_series.iloc[-1] > 80:
            score -= 18
            rsi_signal = "EXTREME Overbought ⚠⚠⚠"
            signals.append(("CRITICAL", f"RSI {rsi_series.iloc[-1]:.1f} - Extreme overbought, pullback likely"))
        elif rsi_series.iloc[-1] > 75:
            score -= 12
            rsi_signal = "Extremely Overbought ⚠⚠"
            signals.append(("WARNING", f"RSI {rsi_series.iloc[-1]:.1f} - Extremely overbought"))
        elif rsi_series.iloc[-1] > 70:
            score -= 8
            rsi_signal = "Overbought ⚠"
            signals.append(("WARNING", f"RSI {rsi_series.iloc[-1]:.1f} - Overbought"))
        elif rsi_series.iloc[-1] < 25:
            score += 15
            rsi_signal = "EXTREME Oversold 🎯🎯🎯"
            signals.append(("BULLISH", f"RSI {rsi_series.iloc[-1]:.1f} - Extreme oversold opportunity"))
        elif rsi_series.iloc[-1] < 30:
            score += 10
            rsi_signal = "Oversold 🎯"
            signals.append(("BULLISH", f"RSI {rsi_series.iloc[-1]:.1f} - Oversold"))
        else:
            rsi_signal = "Neutral ✓"

        technical_data.append(["RSI (14)", f"{rsi_series.iloc[-1]:.2f}", rsi_signal])

        # MACD
        ema12 = close_series.ewm(span=12).mean()
        ema26 = close_series.ewm(span=26).mean()
        macd = (ema12 - ema26).values
        signal_line = pd.Series(macd).ewm(span=9).mean().values

        if macd[-1] > signal_line[-1] and macd[-1] > 0:
            score += 12
            macd_signal = "Strong Bullish 🚀"
            signals.append(("BULLISH", "MACD strong bullish crossover"))
        elif macd[-1] > signal_line[-1]:
            score += 6
            macd_signal = "Bullish ↗"
        elif macd[-1] < signal_line[-1] and macd[-1] < 0:
            score -= 12
            macd_signal = "Strong Bearish ↘↘"
            signals.append(("BEARISH", "MACD strong bearish crossover"))
        elif macd[-1] < signal_line[-1]:
            score -= 6
            macd_signal = "Bearish ↘"
        else:
            macd_signal = "Neutral →"

        technical_data.append(["MACD", f"{macd[-1]:.4f}", macd_signal])

        # ENHANCED: Moving Averages with better context
        current_price = close[-1]
        ma_50 = close_series.rolling(50).mean().iloc[-1]
        ma_200 = close_series.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

        if ma_200:
            if current_price > ma_50 > ma_200:
                distance_above_ma50 = ((current_price - ma_50) / ma_50) * 100

                if distance_above_ma50 > 20 and ytd_change > 80:
                    score += 8
                    ma_signal = "Golden Cross 🌟 (Overextended)"
                    signals.append(("WARNING", "Golden Cross but overextended from MA50"))
                elif day_change < -5:
                    score += 12
                    ma_signal = "Golden Cross 🌟 (Dip Opportunity)"
                    signals.append(("BULLISH", "Golden Cross - dip buy opportunity"))
                else:
                    score += 18
                    ma_signal = "Golden Cross 🌟"
                    signals.append(("BULLISH", "Golden Cross active"))
            elif current_price < ma_50 < ma_200:
                score -= 18
                ma_signal = "Death Cross ☠️"
                signals.append(("BEARISH", "Death Cross active"))
            elif current_price > ma_50:
                score += 8
                ma_signal = "Above 50MA ↗"
            else:
                score -= 8
                ma_signal = "Below 50MA ↘"
        else:
            if current_price > ma_50:
                score += 8
                ma_signal = "Above 50MA ↗"
            else:
                score -= 8
                ma_signal = "Below 50MA ↘"

        technical_data.append(["Moving Averages", f"{self.currency}{ma_50:.2f}", ma_signal])

        # ENHANCED: Volume Analysis with better context
        volume_ma20 = pd.Series(volume).rolling(20).mean().iloc[-1]
        volume_ratio = volume[-1] / volume_ma20 if volume_ma20 > 0 else 1

        self.technical_metrics['Volume_Ratio'] = round(volume_ratio, 2)

        if volume_ratio > 3:
            if day_change < -5:
                score -= 20
                vol_signal = f"🚨 Panic Selling {volume_ratio:.1f}x"
                signals.append(("CRITICAL", f"Panic selling: {volume_ratio:.1f}x volume"))
            elif day_change > 5:
                score += 15
                vol_signal = f"🔥 Institutions Buying {volume_ratio:.1f}x"
                signals.append(("BULLISH", f"Institutional buying: {volume_ratio:.1f}x volume"))
            else:
                score += 5
                vol_signal = f"High Activity {volume_ratio:.1f}x"
        elif volume_ratio > 2:
            if day_change < -3:
                score -= 8
                vol_signal = f"Selling Pressure {volume_ratio:.1f}x"
            else:
                score += 10
                vol_signal = f"Strong Interest {volume_ratio:.1f}x"
        elif volume_ratio < 0.3:
            market_cap = self.info.get('marketCap', 0) if self.info else 0

            if ytd_change > 100:
                score -= 15
                vol_signal = f"🚨 Low Volume {volume_ratio:.2f}x (RED FLAG)"
                signals.append(("CRITICAL", f"Low volume ({volume_ratio:.2f}x) on extreme move - retail pump risk"))
            elif ytd_change > 50:
                score -= 10
                vol_signal = f"⚠️  Low Volume {volume_ratio:.2f}x"
                signals.append(("WARNING", "Low volume on strong move"))
            else:
                score -= 5
                vol_signal = f"Low {volume_ratio:.2f}x"
        else:
            vol_signal = "Normal ✓"

        technical_data.append(["Volume", f"{volume_ratio:.2f}x", vol_signal])

        # Support/Resistance
        support = self.data['Low'].tail(60).min()
        resistance = self.data['High'].tail(60).max()

        self.technical_metrics['Support'] = round(support, 2)
        self.technical_metrics['Resistance'] = round(resistance, 2)

        distance_to_support = ((current_price - support) / support) * 100
        distance_to_resistance = ((resistance - current_price) / current_price) * 100

        if distance_to_support < 3:
            sr_signal = "Near Support 🎯"
            score += 10
        elif distance_to_resistance < 3:
            sr_signal = "Near Resistance ⚠"
            score -= 8
        else:
            sr_signal = "Mid-Range ✓"

        technical_data.append(["Support/Resist", f"{self.currency}{support:.2f}/{self.currency}{resistance:.2f}", sr_signal])

        # Bollinger Bands
        sma_20 = close_series.rolling(20).mean()
        std_20 = close_series.rolling(20).std()
        upper_band = sma_20 + (2 * std_20)
        lower_band = sma_20 - (2 * std_20)

        bb_position = (current_price - lower_band.iloc[-1]) / (upper_band.iloc[-1] - lower_band.iloc[-1])

        if bb_position > 0.95:
            bb_signal = "Near Upper Band ⚠⚠"
            score -= 10
        elif bb_position > 0.85:
            bb_signal = "Near Upper Band ⚠"
            score -= 5
        elif bb_position < 0.05:
            bb_signal = "Near Lower Band 🎯🎯"
            score += 12
        elif bb_position < 0.15:
            bb_signal = "Near Lower Band 🎯"
            score += 8
        else:
            bb_signal = "Mid-Range ✓"

        technical_data.append(["Bollinger Bands", f"{bb_position*100:.0f}%", bb_signal])

        self.technical_score = max(0, min(100, score))

        # Analyze momentum quality
        self.analyze_momentum_quality()

        self.formatter.print_score_bar(self.technical_score, "Technical Score")
        self.formatter.print_subheader("Key Indicators")
        self.formatter.print_table(["Indicator", "Value", "Signal"], technical_data, [25, 20, 35])

        self.formatter.print_metric("Momentum Quality Score", f"{self.momentum_quality:.0f}/100", width=60)

        # Show volume context notes
        if self.volume_notes:
            print("\n📊 VOLUME CONTEXT NOTES:")
            for note in self.volume_notes:
                print(f"   • {note}")

        bullish = sum(1 for s in signals if s[0] == "BULLISH")
        bearish = sum(1 for s in signals if s[0] in ["BEARISH", "CRITICAL"])

        print(f"\n📊 Signals: 🟢 {bullish} Bullish | 🔴 {bearish} Bearish")

        if signals:
            print("\n🔍 Key Technical Signals:")
            for signal_type, description in signals[:8]:
                if signal_type == "CRITICAL":
                    print(f"   🚨 {description}")
                elif signal_type == "BULLISH":
                    print(f"   🟢 {description}")
                elif signal_type == "WARNING":
                    print(f"   ⚠️  {description}")
                else:
                    print(f"   🔴 {description}")

        return self.technical_score, signals

    def fundamental_analysis(self):
        """ENHANCED: Fundamental analysis with API data integration."""
        self.formatter.print_header("💰 FUNDAMENTAL ANALYSIS")

        if not self.info:
            self.fundamental_score = 50
            return self.fundamental_score, []

        score = 50
        signals = []
        quality_flags = []
        valuation_data = []
        growth_data = []
        health_data = []

        # Get sector
        sector = self.info.get('sector', 'Technology')

        # P/E and EPS
        pe = self.info.get('trailingPE')
        eps = self.info.get('trailingEps')
        eps_forward = self.info.get('forwardEps')

        # IMPROVED EPS GROWTH CALCULATION
        eps_growth = None
        eps_growth_method = "Forward vs Trailing"

        try:
            earnings_history = self.stock.quarterly_earnings
            if earnings_history is not None and len(earnings_history) >= 5:
                current_quarter_eps = earnings_history['Earnings'].iloc[0]
                same_quarter_last_year = earnings_history['Earnings'].iloc[4]

                if same_quarter_last_year != 0:
                    eps_growth = ((current_quarter_eps - same_quarter_last_year) /
                                 abs(same_quarter_last_year)) * 100
                    eps_growth_method = "YoY (Quarterly)"
        except:
            pass

        # Fallback to forward vs trailing
        if eps_growth is None and eps and eps_forward and eps > 0:
            eps_growth = ((eps_forward - eps) / eps) * 100

        if eps_growth is not None:
            self.fundamental_metrics['EPS_Growth'] = f"{eps_growth:.1f}%"
            self.fundamental_metrics['EPS_Method'] = eps_growth_method

        # PEG Ratio
        peg = self.info.get('pegRatio')
        if not peg and eps_growth and eps_growth > 0 and pe:
            peg = pe / eps_growth

        self.peg_ratio = peg

        # Cash flow
        free_cash_flow = self.info.get('freeCashflow', 0)

        # Revenue Growth
        revenue_growth = self.info.get('revenueGrowth')
        if revenue_growth:
            revenue_growth = revenue_growth * 100

        # CRITICAL: Sector-relative P/E Assessment
        sector_assessment = None
        if pe and sector in self.sector_pe_ranges:
            low, high = self.sector_pe_ranges[sector]
            if pe < low * 0.8:
                sector_assessment = f"Deep Value vs {sector} 💎💎"
                score += 15
            elif pe < low:
                sector_assessment = f"Undervalued vs {sector} 💎"
                score += 10
            elif pe > high * 1.3:
                sector_assessment = f"Very Expensive vs {sector} ⚠⚠"
                score -= 20
            elif pe > high:
                sector_assessment = f"Expensive vs {sector} ⚠"
                score -= 12
            else:
                sector_assessment = f"Fair vs {sector} ✓"
                score += 3

            self.sector_relative_valuation = sector_assessment

        # CRITICAL ENHANCEMENT: P/E + EPS Growth Analysis
        if pe and eps_growth is not None:
            if pe > 50 and eps_growth < 0:
                quality_flags.append(f"🚨 CRITICAL: P/E {pe:.1f} + EPS declining {eps_growth:.1f}% = EXTREME RISK")
                signals.append(("CRITICAL", "Overvalued with declining earnings"))
                score -= 40
                pe_assessment = "DANGEROUS ⚠⚠⚠"

            elif pe > 40 and eps_growth < 0:
                quality_flags.append(f"🚨 HIGH RISK: P/E {pe:.1f} with declining EPS {eps_growth:.1f}%")
                signals.append(("CRITICAL", "High valuation + declining earnings"))
                score -= 35
                pe_assessment = "VERY DANGEROUS ⚠⚠⚠"

            elif pe > 40 and eps_growth < 5:
                if eps_growth_method == "Forward vs Trailing":
                    quality_flags.append(f"⚠️  WARNING: P/E {pe:.1f} with conservative guidance {eps_growth:.1f}%")
                    score -= 18
                else:
                    quality_flags.append(f"⚠️  HIGH RISK: P/E {pe:.1f} with weak growth {eps_growth:.1f}%")
                    score -= 25
                pe_assessment = "Expensive ⚠⚠"

            elif pe > 30 and eps_growth < 0:
                score -= 25
                pe_assessment = "Overvalued ⚠⚠"

            elif pe < 15 and eps_growth > 10:
                quality_flags.append("💎 QUALITY: Low P/E + Strong growth = Value opportunity")
                score += 15
                pe_assessment = "Undervalued 💎"

            elif pe < 15:
                score += 12
                pe_assessment = "Undervalued 🎯"

            elif eps_growth > 25 and pe < 30:
                pe_assessment = "Growth at Reasonable Price 📈"
                score += 10

            elif pe < 25:
                pe_assessment = "Fair ✓"

            elif pe < 35:
                score -= 5
                pe_assessment = "Slightly Expensive ⚠"

            else:
                score -= 10
                pe_assessment = "Expensive ⚠"
        elif pe:
            if pe > 60:
                score -= 20
                pe_assessment = "VERY Overvalued ⚠⚠⚠"
            elif pe > 50:
                score -= 15
                pe_assessment = "Overvalued ⚠⚠"
            elif pe > 40:
                score -= 10
                pe_assessment = "Expensive ⚠"
            elif pe < 15:
                score += 10
                pe_assessment = "Undervalued 🎯"
            else:
                pe_assessment = "Fair ✓"
        else:
            pe_assessment = "N/A"

        if pe:
            valuation_data.append(["P/E Ratio", f"{pe:.2f}", pe_assessment])
            if sector_assessment:
                valuation_data.append(["Sector Relative", sector, sector_assessment])

        # PEG Ratio Analysis
        if peg:
            if peg < 0:
                peg_assessment = "Negative Earnings Growth"
                score -= 10
            elif peg < 0.5:
                signals.append(("BULLISH", f"Extremely undervalued (PEG: {peg:.2f})"))
                score += 25
                peg_assessment = "DEEP VALUE 💎💎"
            elif peg < 1:
                signals.append(("BULLISH", f"Undervalued (PEG: {peg:.2f})"))
                score += 18
                peg_assessment = "UNDERVALUED 💎"
            elif peg < 1.5:
                score += 5
                peg_assessment = "Fair Value ✓"
            elif peg < 2:
                score -= 5
                peg_assessment = "Slightly Overvalued ⚠"
            elif peg < 3:
                score -= 12
                peg_assessment = "Overvalued ⚠⚠"
            else:
                score -= 18
                peg_assessment = "VERY Overvalued ⚠⚠⚠"

            valuation_data.append(["⭐ PEG Ratio", f"{peg:.2f}", peg_assessment])

        # EPS GROWTH ANALYSIS
        if eps_growth is not None:
            eps_context = ""

            if eps_growth > 50:
                score += 20
                eps_assessment = "HYPERGROWTH 🚀🚀"
                signals.append(("BULLISH", f"Exceptional growth: {eps_growth:.1f}%"))
            elif eps_growth > 25:
                score += 15
                eps_assessment = "Exceptional 🚀"
            elif eps_growth > 15:
                score += 10
                eps_assessment = "Excellent 📈"
            elif eps_growth > 5:
                score += 5
                eps_assessment = "Moderate ↗"
            elif eps_growth > 0:
                eps_assessment = "Slow Growth →"
            elif eps_growth > -5:
                if eps_growth_method == "Forward vs Trailing":
                    score -= 3
                    eps_assessment = "Conservative Guidance ⚠"
                    eps_context = " (Forward estimate)"
                else:
                    score -= 8
                    eps_assessment = "Declining ⬇"
            elif eps_growth > -10:
                if eps_growth_method == "Forward vs Trailing":
                    score -= 8
                    eps_assessment = "Cautious Outlook ⚠⚠"
                    eps_context = " (Forward estimate - verify actual performance)"
                else:
                    score -= 15
                    eps_assessment = "Sharp Decline ⬇⬇"
                    signals.append(("WARNING", f"EPS declining: {eps_growth:.1f}%"))
            else:
                if eps_growth_method == "Forward vs Trailing":
                    score -= 12
                    eps_assessment = "Very Conservative Guidance ⚠⚠⚠"
                    eps_context = " (Forward estimate - check quarterly reports)"
                else:
                    score -= 25
                    eps_assessment = "Severely Declining ⚠⚠⚠"
                    signals.append(("CRITICAL", f"EPS severely declining: {eps_growth:.1f}%"))

            growth_data.append(["EPS Growth", f"{eps_growth:.1f}%", eps_assessment + eps_context])
            growth_data.append(["Growth Method", eps_growth_method, ""])

            if eps_growth_method == "Forward vs Trailing" and eps_growth < -10:
                quality_flags.append(
                    f"📊 NOTE: EPS {eps_growth:.1f}% is FORWARD guidance. "
                    "Verify recent quarterly earnings for actual YoY performance."
                )

        # Revenue Growth Analysis
        if revenue_growth:
            if revenue_growth > 50:
                score += 15
                rev_assessment = "Hypergrowth 🌟🌟"
            elif revenue_growth > 25:
                score += 12
                rev_assessment = "Exceptional 🌟"
            elif revenue_growth > 15:
                score += 8
                rev_assessment = "Strong 📊"
            elif revenue_growth > 5:
                score += 4
                rev_assessment = "Moderate ↗"
            elif revenue_growth > 0:
                rev_assessment = "Slow ✓"
            else:
                score -= 8
                rev_assessment = "Declining ⬇"

            growth_data.append(["Revenue Growth", f"{revenue_growth:.1f}%", rev_assessment])

            # CRITICAL: Margin compression check
            if revenue_growth > 30 and eps_growth and eps_growth < -5:
                quality_flags.append(
                    f"⚠️  MARGIN COMPRESSION: Revenue up {revenue_growth:.1f}% "
                    f"but EPS guidance down {abs(eps_growth):.1f}%"
                )
                signals.append(("WARNING", "Significant margin compression"))
                score -= 15

        # Profit Margin
        profit_margin = self.info.get('profitMargins')
        if profit_margin:
            profit_margin = profit_margin * 100
            if profit_margin > 25:
                score += 12
                margin_assessment = "Exceptional 💰💰"
            elif profit_margin > 20:
                score += 8
                margin_assessment = "Excellent 💰"
            elif profit_margin > 10:
                score += 4
                margin_assessment = "Healthy ✓"
            elif profit_margin > 5:
                margin_assessment = "Thin ⚠"
            elif profit_margin > 0:
                quality_flags.append(f"⚠️  VERY THIN MARGINS: {profit_margin:.1f}% - Vulnerable")
                score -= 10
                margin_assessment = "Very Thin ⚠⚠"
            else:
                score -= 15
                margin_assessment = "Unprofitable ⚠⚠⚠"

            health_data.append(["Profit Margin", f"{profit_margin:.1f}%", margin_assessment])

        # Free Cash Flow
        if free_cash_flow != 0:
            fcf_billions = free_cash_flow / 1_000_000_000
            if free_cash_flow < 0:
                quality_flags.append(f"🚨 NEGATIVE FREE CASH FLOW: ${fcf_billions:.2f}B - Burning cash")
                signals.append(("CRITICAL", "Negative free cash flow"))
                score -= 25
                fcf_assessment = "NEGATIVE ⚠⚠⚠"
            elif free_cash_flow > 5_000_000_000:
                score += 15
                fcf_assessment = "Excellent 💰💰"
            elif free_cash_flow > 1_000_000_000:
                score += 10
                fcf_assessment = "Strong 💰"
            else:
                score += 5
                fcf_assessment = "Positive ✓"

            health_data.append(["Free Cash Flow", f"${fcf_billions:.2f}B", fcf_assessment])

        # Debt/Equity
        debt_equity = self.info.get('debtToEquity')
        if debt_equity:
            debt_equity = debt_equity / 100

            if debt_equity < 0.3:
                score += 15
                debt_assessment = "Fortress Balance Sheet 🏰"
            elif debt_equity < 0.5:
                score += 10
                debt_assessment = "Very Strong 💪"
            elif debt_equity < 1:
                score += 5
                debt_assessment = "Healthy ✓"
            elif debt_equity < 2:
                debt_assessment = "Moderate ⚠"
            elif debt_equity < 3:
                signals.append(("WARNING", f"High leverage: {debt_equity:.2f}x"))
                score -= 12
                debt_assessment = "HIGH LEVERAGE ⚠⚠"
            else:
                quality_flags.append(f"🚨 EXTREME LEVERAGE: {debt_equity:.2f}x debt/equity")
                signals.append(("CRITICAL", f"Extreme leverage: {debt_equity:.2f}x"))
                score -= 20
                debt_assessment = "EXTREME RISK ⚠⚠⚠"

            health_data.append(["Debt/Equity", f"{debt_equity:.2f}x", debt_assessment])

        # Return on Equity (ROE)
        roe = self.info.get('returnOnEquity')
        if roe:
            roe = roe * 100
            if roe > 30:
                score += 15
                roe_assessment = "Exceptional 🌟🌟"
                signals.append(("BULLISH", f"Exceptional ROE: {roe:.1f}%"))
            elif roe > 20:
                score += 10
                roe_assessment = "Excellent 🌟"
            elif roe > 15:
                score += 5
                roe_assessment = "Strong ✓"
            elif roe > 10:
                roe_assessment = "Adequate →"
            else:
                roe_assessment = "Weak ⬇"
                score -= 5

            health_data.append(["⭐ Return on Equity", f"{roe:.1f}%", roe_assessment])

        # Beta
        beta = self.info.get('beta')
        if beta:
            if beta > 2.5:
                quality_flags.append(f"⚡ EXTREME VOLATILITY: Beta {beta:.2f} - Expect wild swings")
                signals.append(("WARNING", f"Extreme volatility: Beta {beta:.2f}"))
                beta_assessment = "EXTREME VOL ⚡⚡⚡"
            elif beta > 2:
                signals.append(("WARNING", f"High volatility: Beta {beta:.2f}"))
                beta_assessment = "VERY HIGH VOL ⚡⚡"
            elif beta > 1.5:
                beta_assessment = "High Volatility ⚡"
            elif beta > 1:
                beta_assessment = "Above Market ↗"
            else:
                beta_assessment = "Stable ✓"

            health_data.append(["Beta (Volatility)", f"{beta:.2f}", beta_assessment])

        # Dangerous combination check
        if debt_equity and beta and debt_equity > 2 and beta > 1.8:
            quality_flags.append(
                f"🚨 TOXIC COMBO: High leverage ({debt_equity:.2f}x) + "
                f"High volatility ({beta:.2f}) = BLOW-UP RISK"
            )
            score -= 20

        # NEW: Add insider trading impact
        insider_impact, insider_signals = self.analyze_insider_activity()
        score += insider_impact
        signals.extend(insider_signals)

        # NEW: Add institutional impact
        inst_impact, inst_signals = self.analyze_institutional_activity()
        score += inst_impact
        signals.extend(inst_signals)

        self.fundamental_score = max(0, min(100, score))

        self.formatter.print_score_bar(self.fundamental_score, "Fundamental Score")

        # Show API data adjustments
        if insider_impact != 0 or inst_impact != 0:
            print(f"\n📊 API DATA ADJUSTMENTS:")
            if insider_impact != 0:
                print(f"   Insider Trading Impact: {insider_impact:+d} points")
            if inst_impact != 0:
                print(f"   Institutional Impact: {inst_impact:+d} points")
            print(f"   Final Fundamental Score: {self.fundamental_score:.0f}/100")

        # Show quality flags first
        if quality_flags:
            print("\n⚠️  QUALITY CHECKS & NOTES:")
            for flag in quality_flags:
                if len(flag) > 85:
                    words = flag.split()
                    line = "   "
                    for word in words:
                        if len(line) + len(word) + 1 <= 90:
                            line += word + " "
                        else:
                            print(line)
                            line = "   " + word + " "
                    if line.strip():
                        print(line)
                else:
                    print(f"   {flag}")

        if valuation_data:
            self.formatter.print_subheader("💎 Valuation Metrics")
            self.formatter.print_table(["Metric", "Value", "Assessment"], valuation_data, [25, 20, 35])

        if growth_data:
            self.formatter.print_subheader("📈 Growth Profile")
            self.formatter.print_table(["Metric", "Value", "Assessment"], growth_data, [25, 20, 35])

        if health_data:
            self.formatter.print_subheader("🏥 Financial Health")
            self.formatter.print_table(["Metric", "Value", "Assessment"], health_data, [25, 20, 35])

        return self.fundamental_score, signals

    def enhanced_news_analysis(self):
        """ENHANCED: News analysis with Finnhub integration."""
        self.formatter.print_header("📰 NEWS & SENTIMENT ANALYSIS")

        score = 50

        # NEW: Try Finnhub news first if available
        finnhub_news = self.enhanced_data.get('enhanced_news', [])

        if finnhub_news:
            print(f"\n✅ Using Finnhub news data")
            print(f"📊 Analyzing {len(finnhub_news)} articles...\n")

            positive_count = 0
            negative_count = 0
            neutral_count = 0

            # Simple sentiment analysis based on keywords
            positive_keywords = ['surge', 'beat', 'strong', 'growth', 'positive', 'win', 'approved', 'breakthrough']
            negative_keywords = ['plunge', 'miss', 'weak', 'decline', 'concern', 'lawsuit', 'investigation']

            for article in finnhub_news:
                title = article.get('title', '').lower()

                pos_score = sum(1 for kw in positive_keywords if kw in title)
                neg_score = sum(1 for kw in negative_keywords if kw in title)

                if pos_score > neg_score:
                    positive_count += 1
                elif neg_score > pos_score:
                    negative_count += 1
                else:
                    neutral_count += 1

            total = len(finnhub_news)
            if total > 0:
                avg_sentiment = (positive_count - negative_count) / total

                # Enhanced scoring
                if avg_sentiment > 0.4:
                    score += 25
                elif avg_sentiment > 0.2:
                    score += 15
                elif avg_sentiment < -0.4:
                    score -= 25
                elif avg_sentiment < -0.2:
                    score -= 15

                self.sentiment_metrics = {
                    'avg_sentiment': round(avg_sentiment, 3),
                    'positive_count': positive_count,
                    'negative_count': negative_count,
                    'neutral_count': neutral_count,
                    'news_count': total,
                    'source': 'Finnhub API'
                }

                # Display news
                print("📰 Recent News Headlines:")
                print("-" * 90)
                for i, article in enumerate(finnhub_news[:10], 1):
                    title = article.get('title', '')[:75]
                    source = article.get('source', 'Unknown')
                    published = article.get('published', 'Recent')

                    title_lower = article.get('title', '').lower()
                    if any(kw in title_lower for kw in positive_keywords):
                        emoji = "🟢"
                    elif any(kw in title_lower for kw in negative_keywords):
                        emoji = "🔴"
                    else:
                        emoji = "⚪"

                    print(f"{i:2d}. {emoji} {title}")
                    print(f"    {source} | {published}")
                    print()

            self.sentiment_score = max(0, min(100, score))
            self.formatter.print_score_bar(self.sentiment_score, "Sentiment Score")

            if total > 0:
                print(f"\n📊 News Sentiment Summary:")
                self.formatter.print_metric("🟢 Positive", f"{positive_count} ({positive_count/total*100:.0f}%)", width=60)
                self.formatter.print_metric("🔴 Negative", f"{negative_count} ({negative_count/total*100:.0f}%)", width=60)
                self.formatter.print_metric("⚪ Neutral", f"{neutral_count} ({neutral_count/total*100:.0f}%)", width=60)

            return self.sentiment_score, []

        # Fallback to yfinance news
        try:
            news = []

            if hasattr(self.stock, 'news'):
                news = self.stock.news

            if not news and hasattr(self.stock, 'info'):
                news = self.stock.info.get('news', [])

            if not news or len(news) == 0:
                self.sentiment_score = 50
                self.sentiment_metrics = {'news_count': 0}

                print("\n" + "-" * 90)
                print("⚠️  NO RECENT NEWS AVAILABLE")
                print("-" * 90)
                print("\nPossible reasons:")
                print("   • Limited media coverage (smaller or specialized company)")
                print("   • Data provider limitations")
                print("   • Recent IPO with minimal coverage")

                print("\n📋 RECOMMENDED MANUAL CHECKS:")
                company_name = self.info.get('longName', self.ticker) if self.info else self.ticker

                print(f"   1. Search: '{company_name} news' on Google News")
                print(f"   2. Check: '{self.ticker} earnings report'")

                if self.market == "INDIA":
                    print(f"   3. Visit: nseindia.com or bseindia.com")
                    print(f"   4. Check: Economic Times, Moneycontrol")
                else:
                    print(f"   3. Visit: sec.gov/edgar (search: {self.ticker})")
                    print(f"   4. Monitor industry news")

                print("\n💡 SENTIMENT ASSESSMENT:")
                print("   • Defaulting to NEUTRAL (50/100)")
                print("   • No news often indicates stable operations")

                print("\n" + "-" * 90)

                return 50, []

            # Standard yfinance news processing (existing code)
            extremely_positive = ['soars', 'surges', 'explodes', 'skyrockets', 'rockets',
                                'multibillion', 'breakout', 'monster', 'massive', 'stellar',
                                'doubles', 'triples']

            positive_keywords = ['upgrade', 'beat', 'beats', 'strong', 'partnership', 'record',
                               'breakthrough', 'expanding', 'growth', 'positive', 'wins', 'contract',
                               'approved', 'success', 'profit', 'revenue', 'exceeds', 'outperforms']

            regulatory_critical = ['sec probe', 'sec investigation', 'regulatory probe', 'under investigation',
                                  'doj probe', 'ftc investigation', 'class action', 'securities fraud',
                                  'lawsuit', 'sued', 'violation', 'sebi', 'sebi investigation',
                                  'fraud', 'criminal', 'charges']

            critical_keywords = ['plunge', 'plummets', 'tumbles', 'crashes', 'dives', 'slumps', 'downgrade',
                               'slashes', 'misses', 'disappoints', 'warning', 'cut', 'losses', 'weak',
                               'decline', 'falls', 'drops', 'concern', 'fears', 'crisis', 'collapse',
                               'bankruptcy', 'layoffs', 'scandal']

            sentiments = []
            positive_count = 0
            negative_count = 0
            neutral_count = 0
            critical_risks = []
            major_catalysts = []
            regulatory_issues = []
            analyzed_news = []

            ticker_lower = self.ticker.lower()
            company_name = self.info.get('longName', '').lower() if self.info else ''
            company_short = self.info.get('shortName', '').lower() if self.info else ''

            print(f"\n📰 Analyzing {min(len(news), 20)} recent articles...")
            print("-" * 90)

            for article in news[:20]:
                title = ""
                publisher = ""
                link = ""
                publish_time = ""

                if isinstance(article, dict):
                    if 'title' in article:
                        title = article.get('title', '')
                    elif 'headline' in article:
                        title = article.get('headline', '')

                    publisher = article.get('publisher', 'Unknown')
                    link = article.get('link', '')

                    if 'providerPublishTime' in article:
                        try:
                            timestamp = article['providerPublishTime']
                            publish_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
                        except:
                            publish_time = "Recent"
                    else:
                        publish_time = "Recent"

                if not title:
                    continue

                title_lower = title.lower()

                is_about_this_stock = (
                    ticker_lower in title_lower or
                    company_name in title_lower or
                    company_short in title_lower or
                    f"${ticker_lower}" in title_lower
                )

                sentiment_score = 0
                sentiment_label = "NEUTRAL"
                reasoning = ""

                # CRITICAL: Regulatory Issues
                regulatory_match = [reg for reg in regulatory_critical if reg in title_lower]
                if regulatory_match:
                    if is_about_this_stock or len(news) < 5:
                        regulatory_issues.append(title)
                        critical_risks.append(title)
                        sentiment_score = -0.95
                        sentiment_label = "🚨 CRITICAL"
                        reasoning = f"Regulatory: {regulatory_match[0]}"
                        negative_count += 1
                    else:
                        sentiment_score = 0
                        sentiment_label = "⚪ FILTERED"
                        reasoning = "Not about this stock"
                        neutral_count += 1

                elif any(kw in title_lower for kw in critical_keywords):
                    if is_about_this_stock:
                        neg_found = [kw for kw in critical_keywords if kw in title_lower][0]
                        critical_risks.append(title)
                        sentiment_score = -0.75
                        sentiment_label = "🔴 NEGATIVE"
                        reasoning = f"Critical keyword: {neg_found}"
                        negative_count += 1
                    else:
                        sentiment_score = 0
                        sentiment_label = "⚪ FILTERED"
                        reasoning = "Not about this stock"
                        neutral_count += 1

                elif any(kw in title_lower for kw in extremely_positive):
                    if is_about_this_stock:
                        pos_found = [kw for kw in extremely_positive if kw in title_lower][0]
                        major_catalysts.append(title)
                        sentiment_score = 0.85
                        sentiment_label = "🟢 POSITIVE"
                        reasoning = f"Strong catalyst: {pos_found}"
                        positive_count += 1
                    else:
                        sentiment_score = 0
                        sentiment_label = "⚪ FILTERED"
                        reasoning = "Not about this stock"
                        neutral_count += 1

                else:
                    if is_about_this_stock or len(news) < 8:
                        pos_cnt = sum(1 for kw in positive_keywords if kw in title_lower)
                        neg_cnt = sum(1 for kw in critical_keywords if kw in title_lower)

                        if pos_cnt > neg_cnt and pos_cnt > 0:
                            sentiment_score = 0.4
                            sentiment_label = "🟢 POSITIVE"
                            reasoning = "Positive keywords"
                            positive_count += 1
                        elif neg_cnt > pos_cnt and neg_cnt > 0:
                            sentiment_score = -0.4
                            sentiment_label = "🔴 NEGATIVE"
                            reasoning = "Negative keywords"
                            negative_count += 1
                        else:
                            sentiment_score = 0
                            sentiment_label = "⚪ NEUTRAL"
                            reasoning = "No strong signal"
                            neutral_count += 1
                    else:
                        sentiment_score = 0
                        sentiment_label = "⚪ FILTERED"
                        reasoning = "Not about this stock"
                        neutral_count += 1

                sentiments.append(sentiment_score)

                analyzed_news.append({
                    'title': title,
                    'publisher': publisher,
                    'time': publish_time,
                    'sentiment': sentiment_label,
                    'reasoning': reasoning,
                    'link': link
                })

            avg_sentiment = np.mean(sentiments) if sentiments else 0

            self.sentiment_metrics = {
                'avg_sentiment': round(avg_sentiment, 3),
                'positive_count': positive_count,
                'negative_count': negative_count,
                'neutral_count': neutral_count,
                'news_count': len(sentiments),
                'critical_risks': critical_risks,
                'major_catalysts': major_catalysts,
                'regulatory_issues': regulatory_issues
            }

            self.news_items = analyzed_news

            # ENHANCED Scoring
            if regulatory_issues:
                score -= (len(regulatory_issues) * 40)
                self.regulatory_risks = regulatory_issues
                score = max(0, score)

            if critical_risks and not regulatory_issues:
                score -= (len(critical_risks) * 20)

            if major_catalysts:
                score += (len(major_catalysts) * 22)

            # More nuanced sentiment scoring
            if avg_sentiment > 0.5:
                score += 25
            elif avg_sentiment > 0.4:
                score += 18
            elif avg_sentiment > 0.2:
                score += 10
            elif avg_sentiment < -0.5:
                score -= 25
            elif avg_sentiment < -0.4:
                score -= 18
            elif avg_sentiment < -0.2:
                score -= 10

            self.sentiment_score = max(0, min(100, score))
            self.catalysts = major_catalysts
            self.risks = critical_risks

        except Exception as e:
            print(f"\n⚠️  Sentiment analysis error: {e}")
            self.sentiment_score = 50
            self.sentiment_metrics = {'news_count': 0}
            return 50, []

        self.formatter.print_score_bar(self.sentiment_score, "Sentiment Score")

        total = self.sentiment_metrics.get('news_count', 0)
        if total > 0:
            print(f"\n📊 News Analysis Summary:")
            self.formatter.print_metric("🟢 Positive", f"{positive_count} ({positive_count/total*100:.0f}%)", width=60)
            self.formatter.print_metric("🔴 Negative", f"{negative_count} ({negative_count/total*100:.0f}%)", width=60)
            self.formatter.print_metric("⚪ Neutral/Filtered", f"{neutral_count} ({neutral_count/total*100:.0f}%)", width=60)
            self.formatter.print_metric("Average Sentiment", f"{avg_sentiment:.2f}", width=60)

        # Display key news items
        if analyzed_news:
            print("\n" + "-" * 90)
            print("📰 KEY NEWS HEADLINES")
            print("-" * 90)

            positive_news = [n for n in analyzed_news if '🟢' in n['sentiment']]
            if positive_news:
                print("\n🟢 POSITIVE NEWS:")
                for i, news_item in enumerate(positive_news[:5], 1):
                    print(f"\n   {i}. {news_item['title']}")
                    print(f"      Source: {news_item['publisher']} | Time: {news_item['time']}")
                    print(f"      Analysis: {news_item['reasoning']}")

            negative_news = [n for n in analyzed_news if '🔴' in n['sentiment'] or '🚨' in n['sentiment']]
            if negative_news:
                print("\n🔴 NEGATIVE/CRITICAL NEWS:")
                for i, news_item in enumerate(negative_news[:5], 1):
                    print(f"\n   {i}. {news_item['title']}")
                    print(f"      Source: {news_item['publisher']} | Time: {news_item['time']}")
                    print(f"      Analysis: {news_item['reasoning']}")

        # Display findings
        if regulatory_issues:
            self.formatter.print_alert_box("🚨 REGULATORY INVESTIGATION DETECTED", regulatory_issues, "CRITICAL")
            print("\n⚠️  CRITICAL REGULATORY RISK:")
            print("   • Active legal/regulatory investigation")
            print("   • Potential outcomes: Fines, penalties, operational restrictions")
            print("   • Expected downside: 30-60%+ from regulatory overhang")
            print("   • Recommendation: AVOID or EXIT position immediately")

        if major_catalysts:
            self.formatter.print_alert_box("🚀 MAJOR POSITIVE CATALYSTS", major_catalysts[:3], "POSITIVE")

        if critical_risks and not regulatory_issues:
            self.formatter.print_alert_box("⚠️  SIGNIFICANT RISKS IDENTIFIED", critical_risks[:3], "WARNING")

        return self.sentiment_score, []

    def market_trend_analysis(self):
        """Market trend analysis with India/US support."""
        self.formatter.print_header("🌍 MARKET ENVIRONMENT")

        score = 50
        market_data = []

        try:
            benchmark = yf.Ticker(self.benchmark_ticker).history(period='1mo')
            secondary = yf.Ticker(self.secondary_ticker).history(period='1mo')
            vix = yf.Ticker("^VIX").history(period='1mo')

            if len(benchmark) >= 20 and len(secondary) >= 20:
                benchmark_chg = ((benchmark['Close'].iloc[-1] - benchmark['Close'].iloc[-20]) / benchmark['Close'].iloc[-20] * 100)
                secondary_chg = ((secondary['Close'].iloc[-1] - secondary['Close'].iloc[-20]) / secondary['Close'].iloc[-20] * 100)
                vix_val = vix['Close'].iloc[-1] if not vix.empty else 20

                # Benchmark assessment
                if benchmark_chg > 3:
                    score += 15
                    bench_signal = "Strong Bull Market 🐂"
                elif benchmark_chg > 1:
                    score += 8
                    bench_signal = "Bull Market ↗"
                elif benchmark_chg > -1:
                    bench_signal = "Neutral →"
                elif benchmark_chg > -3:
                    score -= 8
                    bench_signal = "Bear Market ↘"
                else:
                    score -= 15
                    bench_signal = "Strong Bear 🐻"

                market_data.append([f"{self.benchmark_name} (20d)", f"{benchmark_chg:+.2f}%", bench_signal])

                # Secondary index
                if secondary_chg > 3:
                    secondary_signal = "Strong Bull 🚀"
                elif secondary_chg > 1:
                    secondary_signal = "Bull ↗"
                elif secondary_chg > -1:
                    secondary_signal = "Neutral →"
                else:
                    secondary_signal = "Bear ↘"

                market_data.append([f"{self.secondary_name} (20d)", f"{secondary_chg:+.2f}%", secondary_signal])

                # VIX
                if vix_val < 15:
                    score += 8
                    vix_signal = "Very Low Fear 😌"
                elif vix_val < 20:
                    score += 4
                    vix_signal = "Low Fear ✓"
                elif vix_val < 30:
                    vix_signal = "Moderate Fear ⚠"
                elif vix_val < 40:
                    score -= 8
                    vix_signal = "High Fear 😰"
                else:
                    score -= 15
                    vix_signal = "EXTREME FEAR 🚨"

                market_data.append(["VIX (Fear Index)", f"{vix_val:.2f}", vix_signal])

                self.market_metrics = {
                    'benchmark_change': round(benchmark_chg, 2),
                    'secondary_change': round(secondary_chg, 2),
                    'VIX': round(vix_val, 2)
                }
            else:
                score = 50
                self.market_metrics = {}

            self.market_score = max(0, min(100, score))

        except Exception as e:
            print(f"⚠️  Market data unavailable: {e}")
            self.market_score = 50
            self.market_metrics = {}

        self.formatter.print_score_bar(self.market_score, "Market Environment Score")

        if market_data:
            self.formatter.print_table(["Indicator", "Value", "Signal"], market_data, [25, 18, 35])

        if self.market == "INDIA":
            print("\n💡 Additional Considerations for Indian Market:")
            print("   • Monitor RBI monetary policy decisions")
            print("   • Track FII/DII flows")
            print("   • Consider rupee movement vs USD")

        return self.market_score, []

    def calculate_price_targets(self, recommendation):
        """Calculate entry, exit, and target prices using advanced methods."""

        # Use advanced calculator if available
        if ADVANCED_TARGETS_AVAILABLE:
            try:
                calculator = AdvancedPriceTargetCalculator(
                    self.ticker,
                    self.current_price,
                    self.info
                )

                self.advanced_targets = calculator.calculate_comprehensive_targets(
                    recommendation,
                    self.technical_metrics,
                    self.enhanced_data,
                    self.volatility_annual
                )

                # Extract for backward compatibility
                self.entry_price = self.advanced_targets['entry_price']
                self.stop_loss = self.advanced_targets['stop_loss']
                self.price_targets = self.advanced_targets['targets']

                return  # Exit early if advanced calculation succeeded

            except Exception as e:
                print(f"⚠️  Advanced targets calculation failed: {e}")
                print("Falling back to basic calculation...")

        # Fallback to original simple calculation
        current = self.current_price
        support = self.technical_metrics.get('Support', current * 0.85)
        resistance = self.technical_metrics.get('Resistance', current * 1.15)

        if recommendation == "STRONG BUY":
            self.entry_price = round(current * 1.02, 2)
            self.stop_loss = round(max(support * 0.95, current * 0.88), 2)
            target_1 = round(current * 1.25, 2)
            target_2 = round(current * 1.40, 2)
            target_3 = round(current * 1.60, 2)
            self.price_targets = [(target_1, 75), (target_2, 50), (target_3, 25)]

        elif recommendation == "BUY":
            self.entry_price = round(current * 0.97, 2)
            self.stop_loss = round(max(support * 0.97, current * 0.90), 2)
            target_1 = round(current * 1.15, 2)
            target_2 = round(current * 1.25, 2)
            target_3 = round(current * 1.40, 2)
            self.price_targets = [(target_1, 70), (target_2, 45), (target_3, 20)]

        elif recommendation == "HOLD":
            self.entry_price = round(current, 2)
            self.stop_loss = round(max(support, current * 0.92), 2)
            target_1 = round(current * 1.10, 2)
            target_2 = round(current * 1.20, 2)
            self.price_targets = [(target_1, 50), (target_2, 25)]

        elif recommendation == "SELL":
            self.entry_price = 0
            self.stop_loss = round(current * 1.05, 2)
            target_1 = round(current * 0.85, 2)
            target_2 = round(current * 0.70, 2)
            self.price_targets = [(target_1, 60), (target_2, 35)]

        else:  # STRONG SELL
            self.entry_price = 0
            self.stop_loss = round(current * 1.03, 2)
            target_1 = round(current * 0.75, 2)
            target_2 = round(current * 0.50, 2)
            self.price_targets = [(target_1, 70), (target_2, 40)]

    def generate_recommendation(self):
        """ENHANCED: Generate recommendation with better thresholds."""
        self.formatter.print_header("🎯 STRATEGIC RECOMMENDATION")

        # Calculate weighted score
        self.overall_score = (
            self.technical_score * 0.25 +
            self.fundamental_score * 0.30 +
            self.sentiment_score * 0.30 +
            self.market_score * 0.15
        )

        # Extract metrics
        regulatory = self.sentiment_metrics.get('regulatory_issues', [])
        risks = self.sentiment_metrics.get('critical_risks', [])
        catalysts = self.sentiment_metrics.get('major_catalysts', [])

        day_chg = self.recent_price_action.get('1d_change', 0)
        month_chg = self.recent_price_action.get('20d_change', 0)
        ytd_chg = self.recent_price_action.get('ytd_change', 0)

        pe = self.info.get('trailingPE', 0) if self.info else 0
        eps_growth_str = self.fundamental_metrics.get('EPS_Growth', '0%')
        try:
            eps_growth = float(eps_growth_str.replace('%', '')) if isinstance(eps_growth_str, str) else 0
        except:
            eps_growth = 0

        debt_equity = (self.info.get('debtToEquity', 0) / 100) if self.info else 0
        beta = self.info.get('beta', 1) if self.info else 1
        rsi = self.technical_metrics.get('RSI', 50)
        volume_ratio = self.technical_metrics.get('Volume_Ratio', 1)
        roe = (self.info.get('returnOnEquity', 0) * 100) if self.info else 0
        free_cash_flow = self.info.get('freeCashflow', 0) if self.info else 0
        profit_margin = self.info.get('profitMargins', 0) if self.info else 0
        if profit_margin:
            profit_margin = profit_margin * 100

        override_rec = None
        override_reason = []
        confidence = "MODERATE"
        risk_level = "MODERATE"

        # ========== CRITICAL OVERRIDES ==========

        # OVERRIDE 1: REGULATORY
        if regulatory:
            override_rec = "STRONG SELL"
            override_reason.append("🚨 REGULATORY INVESTIGATION ACTIVE")
            override_reason.append(f"   → {len(regulatory)} active investigation(s)")
            override_reason.append("   → Legal risk supersedes all positive signals")
            confidence = "VERY HIGH"
            risk_level = "EXTREME"

        # OVERRIDE 2: FUNDAMENTAL SCORE < 30
        elif self.fundamental_score < 30:
            override_rec = "STRONG SELL"
            override_reason.append("🚨 CRITICAL FUNDAMENTAL WEAKNESS")
            override_reason.append(f"   → Fundamental Score: {self.fundamental_score:.0f}/100")
            if pe > 40 and eps_growth < 0:
                override_reason.append(f"   → P/E {pe:.1f} + Declining EPS {eps_growth:.1f}%")
            override_reason.append("   → Valuation risk too high")
            confidence = "VERY HIGH"
            risk_level = "EXTREME"

        # OVERRIDE 3: TOXIC FUNDAMENTALS
        elif free_cash_flow < 0 and profit_margin < 5 and pe > 40:
            override_rec = "STRONG SELL"
            override_reason.append("🚨 TOXIC FUNDAMENTALS")
            override_reason.append(f"   → Negative FCF: ${free_cash_flow/1e9:.2f}B")
            override_reason.append(f"   → Thin margins: {profit_margin:.1f}%")
            override_reason.append(f"   → High P/E: {pe:.1f}")
            confidence = "VERY HIGH"
            risk_level = "EXTREME"

        # OVERRIDE 4: RETAIL PUMP
        elif ytd_chg > 150 and volume_ratio < 0.25 and rsi > 70:
            override_rec = "SELL"
            override_reason.append("⚠️  RETAIL PUMP - TAKE PROFITS")
            override_reason.append(f"   → Up {ytd_chg:.0f}% YTD (parabolic)")
            override_reason.append(f"   → Volume {volume_ratio:.2f}x (no institutional support)")
            override_reason.append(f"   → RSI: {rsi:.1f} (overbought)")
            confidence = "HIGH"
            risk_level = "EXTREME"

        # OVERRIDE 5: DEATH SPIRAL
        elif pe > 40 and eps_growth < -10 and day_chg < -8:
            override_rec = "STRONG SELL"
            override_reason.append("💀 DEATH SPIRAL DETECTED")
            override_reason.append(f"   → Overvalued: P/E {pe:.1f}")
            override_reason.append(f"   → Earnings collapsing: {eps_growth:.1f}%")
            override_reason.append(f"   → Price crashed: {day_chg:.1f}% today")
            confidence = "VERY HIGH"
            risk_level = "EXTREME"

        # OVERRIDE 6: PANIC SELLING
        elif day_chg < -12 and volume_ratio > 3:
            override_rec = "SELL"
            override_reason.append("🚨 PANIC SELLING EVENT")
            override_reason.append(f"   → Crashed {abs(day_chg):.1f}% on {volume_ratio:.1f}x volume")
            confidence = "HIGH"
            risk_level = "HIGH"

        # OVERRIDE 7: PERFECT STORM (BUY)
        elif self.peg_ratio and self.peg_ratio < 0.7 and rsi < 33 and len(catalysts) >= 2:
            override_rec = "STRONG BUY"
            override_reason.append("💎 PERFECT STORM - DEEP VALUE")
            override_reason.append(f"   → PEG: {self.peg_ratio:.2f} (undervalued)")
            override_reason.append(f"   → RSI: {rsi:.1f} (oversold)")
            override_reason.append(f"   → {len(catalysts)} catalyst(s)")
            confidence = "VERY HIGH"
            risk_level = "LOW"

        # OVERRIDE 8: OVERSOLD QUALITY
        elif self.fundamental_score > 75 and rsi < 28 and debt_equity < 0.5:
            override_rec = "BUY"
            override_reason.append("🎯 OVERSOLD QUALITY STOCK")
            override_reason.append(f"   → Fundamentals: {self.fundamental_score:.0f}/100")
            override_reason.append(f"   → RSI: {rsi:.1f} (oversold)")
            override_reason.append(f"   → Strong balance sheet")
            confidence = "HIGH"
            risk_level = "LOW"

        # OVERRIDE 9: HIGH ROE + STRONG TREND
        elif roe > 30 and ytd_chg > 50 and debt_equity < 0.5 and self.fundamental_score > 60:
            if volume_ratio < 0.3:
                override_rec = "HOLD"
                override_reason.append("🎯 QUALITY COMPANY - EXTENDED MOVE")
                override_reason.append(f"   → Exceptional ROE: {roe:.1f}%")
                override_reason.append(f"   → Strong gains: {ytd_chg:.0f}% YTD")
                override_reason.append(f"   → Low volume: Wait for pullback")
                confidence = "HIGH"
                risk_level = "MODERATE"
            else:
                override_rec = "BUY"
                override_reason.append("💎 QUALITY MOMENTUM")
                override_reason.append(f"   → Exceptional ROE: {roe:.1f}%")
                override_reason.append(f"   → Strong trend: {ytd_chg:.0f}% YTD")
                confidence = "HIGH"
                risk_level = "MODERATE"

        # ========== WARNING FLAGS ==========
        warning_flags = []

        if pe > 60 and eps_growth < 5:
            warning_flags.append("⚠️  Extremely expensive valuation with minimal growth")

        if free_cash_flow < 0:
            warning_flags.append("⚠️  Negative free cash flow")

        if ytd_chg > 100 and volume_ratio < 0.3:
            warning_flags.append("⚠️  Extended move on low volume - retail pump risk")

        if debt_equity > 2.5 and beta > 1.8:
            warning_flags.append("⚠️  High leverage + High volatility")

        # ========== DETERMINE FINAL RECOMMENDATION ==========

        if override_rec:
            final_recommendation = override_rec
            action_explanation = "\n".join(override_reason)
            self.override_active = True
        else:
            if self.overall_score >= 75:
                final_recommendation = "STRONG BUY"
                action_explanation = "Excellent metrics across all dimensions"
                confidence = "HIGH"
                risk_level = "LOW"
            elif self.overall_score >= 60:
                final_recommendation = "BUY"
                action_explanation = "Positive outlook with strong fundamentals"
                confidence = "MODERATE"
                risk_level = "MODERATE"
            elif self.overall_score >= 40:
                final_recommendation = "HOLD"
                action_explanation = "Mixed signals - monitor for clearer trend"
                confidence = "MODERATE"
                risk_level = "MODERATE"
            elif self.overall_score >= 25:
                final_recommendation = "SELL"
                action_explanation = "Weak performance across key metrics"
                confidence = "MODERATE"
                risk_level = "HIGH"
            else:
                final_recommendation = "STRONG SELL"
                action_explanation = "Critical weakness in multiple areas"
                confidence = "HIGH"
                risk_level = "HIGH"

        # Calculate price targets
        self.calculate_price_targets(final_recommendation)

        # ========== DISPLAY RECOMMENDATION ==========
        # Display advanced targets if available
        if hasattr(self, 'advanced_targets') and self.advanced_targets:
            calculator = AdvancedPriceTargetCalculator(
                self.ticker,
                self.current_price,
                self.info
            )

            # Print comprehensive analysis
            advanced_output = calculator.format_comprehensive_output(
                self.advanced_targets,
                final_recommendation
            )
            print(advanced_output)
        else:
            # Original price target display
            if final_recommendation not in ["SELL", "STRONG SELL"]:
                self.formatter.print_price_target_box(
                    self.current_price,
                    self.entry_price,
                    self.stop_loss,
                    self.price_targets,
                    self.currency
                )
        self.formatter.print_score_bar(self.overall_score, "Overall Weighted Score")

        print("\n" + "=" * 90)
        if final_recommendation in ["STRONG BUY", "BUY"]:
            symbol = "🟢"
        elif final_recommendation == "HOLD":
            symbol = "🟡"
        else:
            symbol = "🔴"

        print(f"\n{symbol} FINAL RECOMMENDATION: {final_recommendation} {symbol}")
        print(f"Confidence Level: {confidence} | Risk Level: {risk_level}")
        if self.override_active:
            print("⚡ OVERRIDE ACTIVE: Special conditions detected")
        print("=" * 90)

        print(f"\n💡 RATIONALE:")
        print(action_explanation)

        if warning_flags:
            print("\n⚠️  WARNING FLAGS:")
            for flag in warning_flags:
                print(f"   {flag}")

        # Price targets
        if final_recommendation not in ["SELL", "STRONG SELL"]:
            self.formatter.print_price_target_box(
                self.current_price,
                self.entry_price,
                self.stop_loss,
                self.price_targets,
                self.currency
            )
        else:
            print("\n" + "=" * 90)
            print("🚪 EXIT STRATEGY".center(90))
            print("=" * 90)
            print(f"\n  Current Price: {self.currency}{self.current_price:.2f}")
            print(f"  Recommended Exit: Market order (immediate)")
            print(f"  Stop on Bounce: {self.currency}{self.stop_loss:.2f}")
            print(f"\n  Downside Targets:")
            for i, (target, probability) in enumerate(self.price_targets, 1):
                downside = ((target - self.current_price) / self.current_price * 100)
                print(f"    Level {i}: {self.currency}{target:.2f} ({downside:.1f}% downside) - {probability}% probability")
            print("\n" + "=" * 90)

        # Score breakdown
        print("\n📊 SCORE BREAKDOWN:")
        scores_table = [
            ["Technical Analysis", f"{self.technical_score:.0f}/100", "25% weight"],
            ["Fundamental Analysis", f"{self.fundamental_score:.0f}/100", "30% weight"],
            ["Sentiment Analysis", f"{self.sentiment_score:.0f}/100", "30% weight"],
            ["Market Environment", f"{self.market_score:.0f}/100", "15% weight"]
        ]
        self.formatter.print_table(["Category", "Score", "Weight"], scores_table, [25, 15, 15])

        # Key metrics
        print("\n📈 KEY METRICS SUMMARY:")
        if self.peg_ratio:
            self.formatter.print_metric("⭐ PEG Ratio", f"{self.peg_ratio:.2f}", width=60)
        if pe:
            self.formatter.print_metric("P/E Ratio", f"{pe:.2f}", width=60)
        if eps_growth_str != '0%':
            self.formatter.print_metric("EPS Growth", eps_growth_str, width=60)
        if roe:
            self.formatter.print_metric("⭐ ROE (Return on Equity)", f"{roe:.1f}%", width=60)
        if self.sector_relative_valuation:
            self.formatter.print_metric("Sector Valuation", self.sector_relative_valuation, width=60)
        self.formatter.print_metric("RSI (14-day)", f"{rsi:.1f}", width=60)
        self.formatter.print_metric("Momentum Quality", f"{self.momentum_quality:.0f}/100", width=60)
        self.formatter.print_metric("Debt/Equity", f"{debt_equity:.2f}x", width=60)
        self.formatter.print_metric("Beta (Volatility)", f"{beta:.2f}", width=60)
        self.formatter.print_metric("Volume Ratio", f"{volume_ratio:.2f}x", width=60)
        self.formatter.print_metric("YTD Performance", f"{ytd_chg:+.1f}%", width=60)

        # Action plan
        print("\n🎯 ACTION PLAN:")
        if final_recommendation == "STRONG BUY":
            print("   ✅ INITIATE/ADD TO POSITION")
            print(f"   • Entry Price: {self.currency}{self.entry_price:.2f}")
            print("   • Position Size: 3-5% of portfolio")
            print(f"   • Stop-Loss: {self.currency}{self.stop_loss:.2f}")
            print("   • Time Horizon: 6-18 months")

        elif final_recommendation == "BUY":
            print("   ✅ CONSIDER INITIATING POSITION")
            print(f"   • Entry Price: {self.currency}{self.entry_price:.2f}")
            print("   • Position Size: 2-3% of portfolio")
            print(f"   • Stop-Loss: {self.currency}{self.stop_loss:.2f}")
            print("   • Scale in over 2-4 weeks")

        elif final_recommendation == "HOLD":
            print("   ⏸️  MAINTAIN CURRENT POSITION")
            print("   • Action: Hold existing shares, avoid adding")
            print(f"   • Stop-Loss: {self.currency}{self.stop_loss:.2f}")
            if volume_ratio < 0.3 and ytd_chg > 50:
                print(f"   • Consider Adding: On pullback with volume increase")
            print("   • Review in 30-60 days")

        elif final_recommendation == "SELL":
            print("   🔻 REDUCE OR EXIT POSITION")
            print("   • Action: Sell 50-75% of holdings")
            print("   • Timing: Exit over 1-2 weeks")

        elif final_recommendation == "STRONG SELL":
            print("   🚨 EXIT IMMEDIATELY")
            print("   • Action: Liquidate entire position")
            print("   • Timing: Market order within 24-48 hours")

        # Risk management
        print("\n⚖️  RISK MANAGEMENT:")
        if risk_level == "EXTREME":
            print("   🚨 EXTREME RISK")
            print("   • Position Size: 0-1% maximum")
            print("   • Monitoring: Daily")
        elif risk_level == "HIGH":
            print("   ⚠️  HIGH RISK")
            print("   • Position Size: 1-2% maximum")
            print("   • Stop-Loss: Mandatory")
        elif risk_level == "MODERATE":
            print("   ⚖️  MODERATE RISK")
            print("   • Position Size: 2-4% of portfolio")
            print("   • Stop-Loss: Recommended")
        else:
            print("   ✅ LOW RISK")
            print("   • Position Size: 3-5% of portfolio")

        # Catalysts and risks
        if catalysts:
            print("\n🚀 KEY CATALYSTS:")
            for i, catalyst in enumerate(catalysts[:3], 1):
                print(f"   {i}. {catalyst}")

        if risks and not regulatory:
            print("\n⚠️  KEY RISKS:")
            for i, risk in enumerate(risks[:3], 1):
                print(f"   {i}. {risk}")

        return final_recommendation, confidence, risk_level

    def generate_executive_summary(self):
        """Generate executive summary."""
        self.formatter.print_header("📋 EXECUTIVE SUMMARY")

        company_name = self.info.get('longName', self.ticker) if self.info else self.ticker
        sector = self.info.get('sector', 'N/A') if self.info else 'N/A'

        print(f"\nCompany: {company_name} ({self.ticker})")
        print(f"Market: {self.market}")
        print(f"Sector: {sector}")
        print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Current Price: {self.currency}{self.current_price:.2f}")

        print("\n" + "-" * 90)
        print("INVESTMENT THESIS")
        print("-" * 90)

        # ENHANCED: Consider override status
        if self.override_active:
            if self.fundamental_score < 30:
                thesis = "BEARISH - Extreme valuation risk with declining fundamentals"
            elif any('REGULATORY' in str(r) for r in self.override_reason):
                thesis = "BEARISH - Legal/regulatory risks outweigh all positives"
            elif any('RETAIL PUMP' in str(r) for r in self.override_reason):
                thesis = "BEARISH - Unsustainable retail-driven move, take profits"
            elif any('PERFECT STORM' in str(r) for r in self.override_reason):
                thesis = "BULLISH - Deep value opportunity with strong catalysts"
            else:
                thesis = "BEARISH - Critical risks outweigh potential rewards"
        elif self.overall_score >= 70:
            thesis = "BULLISH - Strong buy opportunity with favorable risk/reward"
        elif self.overall_score >= 55:
            thesis = "MODERATELY BULLISH - Positive outlook with manageable risks"
        elif self.overall_score >= 40:
            thesis = "NEUTRAL - Mixed signals, wait for clearer direction"
        elif self.overall_score >= 25:
            thesis = "MODERATELY BEARISH - Concerning signals suggest caution"
        else:
            thesis = "BEARISH - Significant risks outweigh potential rewards"

        print(f"\n{thesis}")

        print("\n" + "-" * 90)
        print("STRENGTHS & WEAKNESSES")
        print("-" * 90)

        strengths = []
        weaknesses = []

        if self.technical_score >= 65:
            strengths.append(f"Strong technical setup (Score: {self.technical_score:.0f}/100)")
        if self.fundamental_score >= 65:
            strengths.append(f"Solid fundamentals (Score: {self.fundamental_score:.0f}/100)")
        if self.sentiment_score >= 65:
            strengths.append(f"Positive sentiment (Score: {self.sentiment_score:.0f}/100)")
        if self.peg_ratio and self.peg_ratio < 1:
            strengths.append(f"Attractive valuation (PEG: {self.peg_ratio:.2f})")
        if self.momentum_quality >= 70:
            strengths.append(f"High-quality momentum (Score: {self.momentum_quality:.0f}/100)")

        roe = (self.info.get('returnOnEquity', 0) * 100) if self.info else 0
        if roe > 25:
            strengths.append(f"Exceptional profitability (ROE: {roe:.1f}%)")

        if self.catalysts:
            strengths.append(f"{len(self.catalysts)} identified catalyst(s)")

        # NEW: Add API-based strengths
        if 'insider_trading' in self.enhanced_data:
            insider = self.enhanced_data['insider_trading']
            if insider.get('sentiment') in ['STRONG BUY', 'BULLISH']:
                strengths.append(f"Insider buying detected ({insider.get('insider_buys', 0)} transactions)")

        if 'institutional_ownership' in self.enhanced_data:
            inst = self.enhanced_data['institutional_ownership']
            if 'BUYING' in inst.get('sentiment', ''):
                strengths.append(f"Institutional accumulation ({inst.get('institutions_increasing', 0)} increasing)")

        if self.technical_score < 40:
            weaknesses.append(f"Weak technical position (Score: {self.technical_score:.0f}/100)")
        if self.fundamental_score < 40:
            weaknesses.append(f"Below-average fundamentals (Score: {self.fundamental_score:.0f}/100)")
        if self.sentiment_score < 40:
            weaknesses.append(f"Negative sentiment (Score: {self.sentiment_score:.0f}/100)")
        if self.regulatory_risks:
            weaknesses.append("Regulatory investigation(s) active")
        if self.risks:
            weaknesses.append(f"{len(self.risks)} identified risk(s)")

        volume_ratio = self.technical_metrics.get('Volume_Ratio', 1)
        if volume_ratio < 0.3 and self.recent_price_action.get('ytd_change', 0) > 50:
            weaknesses.append(f"Low trading volume ({volume_ratio:.2f}x) - retail pump risk")

        # NEW: Add API-based weaknesses
        if 'insider_trading' in self.enhanced_data:
            insider = self.enhanced_data['insider_trading']
            if insider.get('sentiment') in ['BEARISH', 'NEGATIVE']:
                weaknesses.append(f"Insider selling detected ({insider.get('insider_sells', 0)} transactions)")

        if strengths:
            print("\n✅ STRENGTHS:")
            self.formatter.print_bullet_list(strengths)

        if weaknesses:
            print("\n❌ WEAKNESSES:")
            self.formatter.print_bullet_list(weaknesses)

        print("\n" + "-" * 90)
        print("FINAL ASSESSMENT")
        print("-" * 90)
        print(f"\nOverall Score: {self.overall_score:.1f}/100")
        print(f"Technical: {self.technical_score:.0f} | Fundamental: {self.fundamental_score:.0f} | Sentiment: {self.sentiment_score:.0f} | Market: {self.market_score:.0f}")

        if self.override_active:
            print("\n⚡ Special conditions detected - algorithmic override active")

        # NEW: Show API data sources used
        if self.enhanced_data.get('data_sources'):
            print(f"\n📊 Enhanced Data Sources: {len(self.enhanced_data['data_sources'])}")
            for source in self.enhanced_data['data_sources']:
                print(f"   ✓ {source}")

    def run_full_analysis(self):
        """Execute complete analysis pipeline with API integration."""
        # Compact startup line instead of large ASCII banner
        print(f"\nAnalyzing {self.ticker} | Market: {self.market} | API: {'on' if API_AVAILABLE else 'off'}")

        if not self.fetch_stock_data():
            return

        # NEW: Fetch enhanced data from APIs
        if self.api_manager:
            self.fetch_enhanced_data()

        self.summarize_stock_data()
        self.technical_analysis()
        self.fundamental_analysis()
        self.enhanced_news_analysis()
        self.market_trend_analysis()
        self.generate_recommendation()
        self.generate_executive_summary()

        # NEW: Show API usage stats (compact)
        if API_AVAILABLE and RATE_LIMITER:
            stats = RATE_LIMITER.get_stats()
            print(f"API Calls: {stats.get('total_calls', 0)} | Sources: {len(stats.get('breakdown', {}))}")

        # Compact completion message and brief disclaimer
        print("\nAnalysis complete. (Not financial advice.)")

def main():
    """Main execution function (minimal output)."""
    # Compact header
    print(f"AI STOCK ANALYST V4.0 | API: {'Active' if API_AVAILABLE else 'Disabled'}")
    ticker = input("Ticker: ").strip().upper()
    if not ticker:
        print("No ticker provided. Exiting.")
        return
    analyst = AIStockAnalyst(ticker)
    analyst.run_full_analysis()


if __name__ == "__main__":
    main()

