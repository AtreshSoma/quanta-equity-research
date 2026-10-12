7
#!/usr/bin/env python3
"""
QUANTA Elite Trading System v2.0
================================
Institutional-Grade Stock Analysis Platform

Features:
- Multi-factor scoring with regime awareness
- Real-time news sentiment integration
- Monte Carlo price projections
- Advanced risk metrics (VaR, CVaR, Factor Analysis)
- Risk-budget position sizing
- Comprehensive fundamental analysis with Piotroski/Altman scores

Author: Enhanced by Claude
Version: 2.0.0
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date, timezone
from typing import Dict, List, Tuple, Optional, Any, Set, Union, Callable
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import warnings
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from concurrent.futures import (ThreadPoolExecutor, as_completed,
                                TimeoutError as FuturesTimeoutError)
import time
from collections import defaultdict, OrderedDict
import threading
import os
import json
from functools import lru_cache
import hashlib
from abc import ABC, abstractmethod
import textwrap
import sys
import importlib
from dataclasses import asdict
from pathlib import Path

# [DECISION-CORE] Shared validation / decision / consistency engine
_SCRIPT_DIR_FOR_ENGINE = Path(__file__).resolve().parent
if str(_SCRIPT_DIR_FOR_ENGINE) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR_FOR_ENGINE))
# [F9] Every decision threshold is read from engine/thresholds.py.
from engine import thresholds as _T  # noqa: E402
try:
    from engine import (
        FinalAction,
        DecisionInputs,
        resolve_final_action,
        signal_to_final_action,
        validate_ticker_data,
        dividend_yield_from_info,
        validate_dividend_yield,
        validate_consistency,
        BACKTEST_DIAGNOSTIC_BANNER,
        backtest_disclaimer,
    )
    from engine.data_status import DataStatus
    from engine.consistency_validator import TickerView
    HAS_DECISION_CORE = True
except ImportError:
    HAS_DECISION_CORE = False
    FinalAction = None  # type: ignore

    class DataStatus:  # type: ignore
        """Fallback mirror of engine.data_status.DataStatus when engine missing."""
        OK = "ok"
        INVALID_TICKER = "invalid_ticker"
        INSUFFICIENT_HISTORY = "insufficient_history"
        PROVIDER_RATE_LIMITED = "provider_rate_limited"
        PROVIDER_DATA_UNAVAILABLE = "provider_data_unavailable"
        STALE_DATA = "stale_data"
        DEEP_CONFIRMATION_FAILED = "deep_confirmation_failed"
        NOT_INDEPENDENTLY_CONFIRMED = "not_independently_confirmed"
        UNSUPPORTED_INSTRUMENT = "unsupported_instrument"

# [MCP] `--mcp` runs mcp_server/quanta_mcp_server.py; only availability is checked here.
import importlib.util as _importlib_util
HAS_MCP = _importlib_util.find_spec("mcp") is not None

# Ensure sibling module directories are importable (core/ -> ../modules)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MODULES_DIR = PROJECT_ROOT / "modules"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if MODULES_DIR.exists() and str(MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(MODULES_DIR))


def _import_optional(*module_candidates: str):
    """Try importing from multiple module paths and return the imported module or None."""
    for module_name in module_candidates:
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    return None


# [POLYGON.IO] Import HybridDataProvider for institutional-grade data
_polygon_module = _import_optional("polygon_provider", "modules.polygon_provider")
if _polygon_module:
    HybridDataProvider = _polygon_module.HybridDataProvider
    HAS_POLYGON = True
else:
    HybridDataProvider = None
    HAS_POLYGON = False

# [COMPLIANCE] Import regulatory compliance module
_compliance_module = _import_optional("compliance_manager", "modules.compliance_manager")
if _compliance_module:
    ComplianceManager = _compliance_module.ComplianceManager
    Market = getattr(_compliance_module, 'Market', None)
    HAS_COMPLIANCE = True
else:
    HAS_COMPLIANCE = False
    ComplianceManager = None
    Market = None

# [CATALYST SCANNER] News-driven catalyst detection with SEC EDGAR + Polygon.io
_catalyst_module = _import_optional("catalyst_scanner", "modules.catalyst_scanner")
_institutional_catalyst_module = _import_optional(
    "catalyst_intelligence_engine", "modules.catalyst_intelligence_engine"
)
if _catalyst_module and _institutional_catalyst_module:
    CatalystScanner = _catalyst_module.CatalystScanner
    InstitutionalCatalystEngine = _institutional_catalyst_module.InstitutionalCatalystEngine
    InstitutionalSignalGenerator = getattr(_institutional_catalyst_module, 'InstitutionalSignalGenerator', None)
    HAS_CATALYST = True
else:
    CatalystScanner = None
    InstitutionalCatalystEngine = None
    InstitutionalSignalGenerator = None
    HAS_CATALYST = False

# [PORTFOLIO RISK] VaR and correlation enforcement
_portfolio_risk_module = _import_optional("portfolio_risk_manager", "modules.portfolio_risk_manager")
if _portfolio_risk_module:
    PortfolioRiskManager = _portfolio_risk_module.PortfolioRiskManager
    HAS_PORTFOLIO_RISK = True
else:
    PortfolioRiskManager = None
    HAS_PORTFOLIO_RISK = False

# Technical Analysis imports
from ta.momentum import RSIIndicator, StochasticOscillator, WilliamsRIndicator
from ta.trend import MACD, ADXIndicator, IchimokuIndicator, AroonIndicator, CCIIndicator, EMAIndicator, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange, KeltnerChannel
from ta.volume import VolumeWeightedAveragePrice, OnBalanceVolumeIndicator, MFIIndicator
from ta.others import DailyReturnIndicator

# Suppress noisy warnings
warnings.filterwarnings('ignore')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('QUANTA')

# Suppress noisy third-party loggers
for noisy_logger in ("yfinance", "urllib3", "requests", "httpx", "polygon_provider", "compliance_manager"):
    logging.getLogger(noisy_logger).setLevel(logging.CRITICAL)


# =============================================================================
# CONFIGURATION & CONSTANTS
# =============================================================================

def _drop_unusable_bars(df):
    """Remove bars that carry no usable close.

    Providers can append a placeholder row for the current session (all-NaN
    OHLC) before the first print of the day or after the close. That row is
    not data: it makes ``Close.iloc[-1]`` NaN, shifts ``previous_close`` by a
    day, and counts toward the minimum-history gate. Dropping it at the fetch
    boundary keeps every downstream consumer honest.

    Returns the frame unchanged when there is nothing to clean, and an empty
    frame when no bar has a close — the caller's invalid/no-data path should
    handle that, not a silent NaN.
    """
    try:
        if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
            return df
        usable = df["Close"].notna()
        if bool(usable.all()):
            return df
        return df[usable]
    except Exception:
        return df


class Config:
    """Centralized configuration management"""
    
    # Technical Analysis Thresholds
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    RSI_EXTREME_OVERSOLD = 20
    RSI_EXTREME_OVERBOUGHT = 80
    
    # Scoring Weights (must sum to 1.0)
    WEIGHT_TECHNICAL = 0.30
    WEIGHT_FUNDAMENTAL = 0.25
    WEIGHT_VALUATION = 0.15
    WEIGHT_QUALITY = 0.10
    WEIGHT_SENTIMENT = 0.10
    
    # DCF Parameters
    DCF_PROJECTION_YEARS = 5
    DCF_TERMINAL_GROWTH = 0.025
    DCF_MARGIN_OF_SAFETY = 0.15
    RISK_FREE_RATE = 0.045
    MARKET_RISK_PREMIUM = 0.055
    
    # Risk Management
    MAX_POSITION_PCT = 0.10
    KELLY_FRACTION = 0.25
    MIN_RISK_REWARD = _T.alias(_T.RR, "RESEARCH_MIN")
    
    # Data Quality
    MIN_DATA_POINTS = 50
    MAX_DAILY_BAR_AGE_DAYS = 7
    CACHE_TTL_SECONDS = 300
    MAX_CACHE_SIZE = 100
    
    # Monte Carlo
    MC_SIMULATIONS = 3000
    MC_DAYS_FORWARD = 30
    MC_MAX_ANNUAL_DRIFT = 0.25
    MC_TARGET_TOLERANCE = 0.05

    # Data-integrity / evidence gates
    SPLIT_ARTIFACT_RETURN_THRESHOLD = _T.alias(_T.RISK_GRADE, "SPLIT_ARTIFACT_RETURN")
    UNEXPLAINED_MOVE_THRESHOLD_PCT = 4.0
    SENTIMENT_DIRECT_WEIGHT = 0.80
    SENTIMENT_PEER_WEIGHT = 0.20
    SENTIMENT_NO_DIRECT_DAMPENER = 0.30
    VWAP_WINDOW = 14

    # Reproducible scoring methodology
    GRADE_CUTOFFS = (
        (80, "A"), (70, "B+"), (60, "B"), (50, "C+"),
        (40, "C"), (30, "D"),
    )
    # The shared band table (engine.score_bands); a local copy only when the
    # decision core cannot be imported.
    try:
        from engine.score_bands import SCORE_BANDS as SIGNAL_CUTOFFS
    except ImportError:  # pragma: no cover - decision core always ships
        SIGNAL_CUTOFFS = (
            (75, "STRONG_BUY"), (65, "BUY"), (55, "ACCUMULATE"),
            (45, "HOLD"), (35, "REDUCE"), (25, "SELL"),
        )
    REGIME_WEIGHTS = {
        "SIDEWAYS": {"technical": .3333333333, "fundamental": .2777777778, "valuation": .1666666667,
                     "quality": .1111111111, "sentiment": .1111111111},
        "BULL": {"technical": .3888888889, "fundamental": .2222222222, "valuation": .1333333333,
                 "quality": .0888888889, "sentiment": .1666666667},
        "BEAR": {"technical": .275, "fundamental": .3125, "valuation": .1875,
                 "quality": .1625, "sentiment": .0625},
        "HIGH_VOL": {"technical": .3125, "fundamental": .275, "valuation": .1875,
                     "quality": .125, "sentiment": .1},
    }

    # Twelve-month target blend; absent sources are renormalized.
    BLEND_WEIGHT_ANALYST = 0.50
    BLEND_WEIGHT_DCF = 0.30
    THIN_COVERAGE_ANALYSTS = 3
    THIN_COVERAGE_ANALYST_WEIGHT = 0.25

    # Hard portfolio-weight caps by risk grade. Beta further reduces the cap.
    MAX_WEIGHT_BY_RISK_GRADE = {
        "A": 0.10, "B": 0.08, "C": 0.06, "D": 0.035, "F": 0.02,
    }
    
    # API Rate Limits
    RATE_LIMIT_PER_MINUTE = 60


# Currency formatting
CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "CHF": "CHF", "INR": "₹", "KRW": "₩",
    "HKD": "HK$", "SGD": "S$", "NZD": "NZ$", "BRL": "R$", "MXN": "MX$"
}


# =============================================================================
# ENUMS & DATA CLASSES
# =============================================================================

class Signal(Enum):
    """Trading signal classification"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    ACCUMULATE = "ACCUMULATE"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"
    NO_POSITION = "NO_POSITION"


# [T5 FIX] Conviction ordering shared by the decision-core ratchet. Higher is
# more bullish. A core verdict ranking LOWER than the signal generator's is more
# conservative and wins; one ranking higher is an upgrade and is suppressed.
_ACTION_RANK = {
    'STRONG_BUY': 5, 'BUY': 4, 'ACCUMULATE': 3,
    'HOLD_EXISTING': 2, 'HOLD': 2, 'WATCHLIST': 1, 'REDUCE': 1, 'AVOID': 0,
    # INVALID means no usable data — the most conservative verdict there is.
    'INVALID': -1,
}

# [T4 FIX] One FinalAction -> Signal map. WATCHLIST and HOLD_EXISTING both had
# to collapse onto Signal.HOLD because the legacy enum has no separate member,
# which is exactly why the display layer must read `final_action` and NOT
# `signal` when deciding whether to address a holder or a watcher.
_FINAL_ACTION_TO_SIGNAL = {
    'STRONG_BUY':    Signal.STRONG_BUY,
    'BUY':           Signal.BUY,
    'ACCUMULATE':    Signal.ACCUMULATE,
    'HOLD_EXISTING': Signal.HOLD,
    'WATCHLIST':     Signal.HOLD,
    'AVOID':         Signal.SELL,
    'INVALID':       Signal.NO_POSITION,
}

# Verdicts that must never be rendered as an entry order.
_NON_ACTIONABLE_ACTIONS = frozenset({
    'HOLD_EXISTING', 'WATCHLIST', 'AVOID', 'REDUCE', 'INVALID', 'HOLD',
})


class MarketRegime(Enum):
    """Market regime classification"""
    BULL_TRENDING = "BULL_TRENDING"
    BULL_VOLATILE = "BULL_VOLATILE"
    BEAR_TRENDING = "BEAR_TRENDING"
    BEAR_VOLATILE = "BEAR_VOLATILE"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    CRISIS = "CRISIS"


class ConfidenceLevel(Enum):
    """Confidence level for recommendations"""
    VERY_HIGH = "VERY_HIGH"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


class TrendDirection(Enum):
    """Price trend direction"""
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    SIDEWAYS = "SIDEWAYS"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"


class VolatilityRegime(Enum):
    """Volatility classification"""
    EXTREMELY_LOW = "EXTREMELY_LOW"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class TechnicalIndicators:
    """Comprehensive technical analysis data"""
    # Momentum
    rsi: float = 50.0
    rsi_divergence: str = "NONE"  # BULLISH, BEARISH, NONE
    stochastic_k: float = 50.0
    stochastic_d: float = 50.0
    williams_r: float = -50.0
    cci: float = 0.0
    
    # Trend
    macd_value: float = 0.0
    macd_signal_line: float = 0.0
    macd_histogram: float = 0.0
    macd_signal: str = "NEUTRAL"
    adx: float = 25.0
    plus_di: float = 25.0
    minus_di: float = 25.0
    trend_strength: str = "WEAK"  # WEAK, MODERATE, STRONG, VERY_STRONG
    trend_direction: str = "SIDEWAYS"
    
    # Moving Averages
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0
    ema_12: float = 0.0
    ema_26: float = 0.0
    price_vs_sma20: float = 0.0  # % distance
    price_vs_sma50: float = 0.0
    price_vs_sma200: float = 0.0
    golden_cross: bool = False
    death_cross: bool = False
    
    # Volatility
    atr: float = 0.0
    atr_percent: float = 0.0
    bollinger_upper: float = 0.0
    bollinger_middle: float = 0.0
    bollinger_lower: float = 0.0
    bollinger_width: float = 0.0
    bollinger_signal: str = "NEUTRAL"
    keltner_upper: float = 0.0
    keltner_lower: float = 0.0
    volatility_regime: str = "NORMAL"
    
    # Volume
    volume_sma: float = 0.0
    volume_ratio: float = 1.0
    obv_trend: str = "NEUTRAL"
    mfi: float = 50.0
    vwap: float = 0.0
    vwap_distance: float = 0.0
    volume_confirmation: bool = False
    
    # Ichimoku
    ichimoku_signal: str = "NEUTRAL"
    tenkan_sen: float = 0.0
    kijun_sen: float = 0.0
    senkou_span_a: float = 0.0
    senkou_span_b: float = 0.0
    chikou_span: float = 0.0
    price_above_cloud: bool = False
    
    # Aroon
    aroon_up: float = 50.0
    aroon_down: float = 50.0
    aroon_oscillator: float = 0.0
    
    # Support/Resistance
    support_level: float = 0.0
    resistance_level: float = 0.0
    pivot_point: float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    s1: float = 0.0
    s2: float = 0.0
    
    # Market Structure
    higher_highs: int = 0
    higher_lows: int = 0
    lower_highs: int = 0
    lower_lows: int = 0
    market_structure: str = "RANGING"
    
    # Price Action
    week_52_high: float = 0.0
    week_52_low: float = 0.0
    distance_from_high: float = 0.0
    distance_from_low: float = 0.0
    
    # Breakout Analysis
    breakout_probability: float = 0.5
    squeeze_detected: bool = False

    # Candlestick Patterns (added)
    # Each pattern: {"name": str, "bias": "BULLISH"|"BEARISH"|"NEUTRAL",
    #                "strength": int (1-3), "reliability": "LOW"|"MEDIUM"|"HIGH",
    #                "type": "REVERSAL"|"CONTINUATION"|"INDECISION",
    #                "context": str  (e.g., "after downtrend")}
    candlestick_patterns: List[Dict] = field(default_factory=list)
    candlestick_bias: str = "NEUTRAL"           # BULLISH / BEARISH / NEUTRAL
    candlestick_score: float = 50.0             # 0-100 (50 neutral)
    candlestick_summary: str = "No notable patterns"
    prior_trend: str = "SIDEWAYS"               # UP / DOWN / SIDEWAYS (used for pattern context)

    # Composite Scores
    momentum_score: float = 50.0
    trend_score: float = 50.0
    volatility_score: float = 50.0


@dataclass
class FundamentalData:
    """Comprehensive fundamental analysis data"""
    # Valuation Ratios
    pe_ratio: float = 0.0
    forward_pe: float = 0.0
    pe_to_growth: float = 0.0  # PEG
    pb_ratio: float = 0.0
    ps_ratio: float = 0.0
    pcf_ratio: float = 0.0  # Price to Cash Flow
    ev_ebitda: float = 0.0
    ev_revenue: float = 0.0
    ev_fcf: float = 0.0
    
    # Market Data
    market_cap: float = 0.0
    shares_outstanding: float = 0.0
    
    # Profitability
    gross_margin: float = 0.0
    operating_margin: float = 0.0
    net_margin: float = 0.0
    roe: float = 0.0
    roa: float = 0.0
    roic: float = 0.0
    roce: float = 0.0
    
    # Growth
    revenue_growth_yoy: float = 0.0
    revenue_growth_3y_cagr: float = 0.0
    eps_growth_yoy: float = 0.0
    eps_growth_3y_cagr: float = 0.0
    fcf_growth_yoy: float = 0.0
    dividend_growth_5y: float = 0.0
    
    # Financial Health
    current_ratio: float = 0.0
    quick_ratio: float = 0.0
    cash_ratio: float = 0.0
    debt_to_equity: float = 0.0
    debt_to_assets: float = 0.0
    interest_coverage: float = 0.0
    debt_to_ebitda: float = 0.0
    
    # Cash Flow
    operating_cash_flow: float = 0.0
    free_cash_flow: float = 0.0
    fcf_yield: float = 0.0
    fcf_per_share: float = 0.0
    capex_ratio: float = 0.0  # CapEx as % of revenue
    
    # Per Share Data
    eps_ttm: float = 0.0
    eps_forward: float = 0.0
    book_value_per_share: float = 0.0
    revenue_per_share: float = 0.0
    
    # Dividends
    dividend_yield: float = 0.0
    dividend_payout_ratio: float = 0.0
    dividend_coverage: float = 0.0
    
    # Quality Scores
    piotroski_f_score: int = 0
    # How many of the nine Piotroski tests had a real input. A score built on
    # fewer than PIOTROSKI_MIN_INPUTS is reported as unavailable, not as 0/9.
    piotroski_components_available: int = 0
    altman_z_score: float = 0.0
    altman_z_status: str = "unavailable"  # ok / unavailable / not_applicable
    altman_z_variant: str = ""  # "public" (market-cap X4) / "private" (book-equity Z')
    beneish_m_score: float = 0.0
    quality_score: float = 50.0
    
    # Growth Consistency
    revenue_consistency: float = 0.5
    earnings_consistency: float = 0.5
    
    # Earnings Quality
    accruals_ratio: float = 0.0
    earnings_quality_score: float = 50.0
    
    # Asset Type
    is_etf: bool = False
    expense_ratio: float = 0.0
    net_assets: float = 0.0
    
    # Derived scores (used for ETF analysis and scoring)
    growth_score: float = 50.0
    valuation_score: float = 50.0

    # Which scored inputs the provider actually reported. A missing ROE or
    # debt/equity is stored as 0.0, which would otherwise read as a measured
    # weak ROE (penalised) or a measured debt-free balance sheet (rewarded).
    # An empty mapping means "not tracked" (hand-built data): every input counts.
    reported: Dict[str, bool] = field(default_factory=dict)

    # Analyst consensus price targets as reported in the same provider payload
    # (Yahoo ``info``). Fast preview reuses them instead of dropping the
    # highest-weight target source it has already downloaded.
    consensus: Dict[str, float] = field(default_factory=dict)

    def has(self, name: str) -> bool:
        """True when ``name`` was reported, or availability is not tracked."""
        return (not self.reported) or bool(self.reported.get(name, False))


@dataclass
class RiskMetrics:
    """Comprehensive risk analysis data"""
    # Volatility
    daily_volatility: float = 0.02
    annualized_volatility: float = 0.30
    volatility_percentile: float = 50.0
    
    # Beta & Correlation
    beta: float = 1.0
    r_squared: float = 0.5
    correlation_spy: float = 0.5
    # A numeric 1.0 is retained internally as the neutral fallback so legacy
    # risk/sizing code remains conservative. Consumers must use beta_status to
    # distinguish that fallback from an estimate.
    # [F3] Daily returns the grade was measured on (RISK_GRADE.LOOKBACK_DAYS).
    risk_grade_lookback_days: int = 0
    # What each factor added to the risk points (engine.risk_grade_drivers).
    grade_drivers: List[Dict[str, Any]] = field(default_factory=list)
    beta_status: str = "unavailable"  # measured | unavailable
    beta_benchmark: str = ""
    beta_observations: int = 0
    
    # Return Metrics
    expected_return: float = 0.08
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    treynor_ratio: float = 0.0
    information_ratio: float = 0.0
    
    # Drawdown
    max_drawdown: float = -0.10
    current_drawdown: float = 0.0
    avg_drawdown: float = -0.05
    drawdown_duration: int = 0
    
    # Value at Risk
    var_95_daily: float = -0.02
    var_99_daily: float = -0.03
    cvar_95: float = -0.03  # Expected Shortfall
    
    # Tail Risk
    skewness: float = 0.0
    kurtosis: float = 0.0  # Excess kurtosis (pandas convention: Normal=0)
    tail_risk_score: float = 50.0
    
    # Liquidity
    avg_volume: float = 0.0
    avg_dollar_volume: float = 0.0
    bid_ask_spread_est: float = 0.001
    liquidity_score: float = 5.0
    
    # Risk Scores
    overall_risk_score: int = 5  # 1-10
    risk_grade: str = "B"  # A, B, C, D, F


@dataclass
class SentimentData:
    """News and sentiment analysis data"""
    # Overall Sentiment
    sentiment_score: float = 0.0  # -1 to +1
    sentiment_label: str = "NEUTRAL"
    sentiment_confidence: float = 0.5
    
    # News Provenance (added for data quality)
    last_news_timestamp: str = ""
    key_headline_sources: List[str] = field(default_factory=list)
    sentiment_source: str = "none"  # company / peer / macro / none
    has_direct_news: bool = False
    direct_news_count: int = 0
    peer_news_count: int = 0
    macro_news_count: int = 0
    weighted_sentiment_score: float = 0.0
    relevance_detail: Dict[str, Any] = field(default_factory=dict)
    provenance_computed: bool = False

    # Why this reading is what it is. An all-default SentimentData used to be
    # returned for "no news exists", "no API keys configured" and "every
    # provider errored" alike, so a non-measurement was indistinguishable from
    # a measured NEUTRAL. `measured` is False whenever no article was scored.
    #   OK                   - articles fetched and scored
    #   PARTIAL              - scored, but at least one provider failed
    #   NO_NEWS              - providers responded, none had articles
    #   PROVIDER_UNAVAILABLE - every provider errored
    #   NOT_CONFIGURED       - every provider lacks credentials
    status: str = "NO_NEWS"
    measured: bool = False
    sources_attempted: int = 0
    sources_succeeded: int = 0
    source_errors: List[str] = field(default_factory=list)

    # Sentiment Momentum
    sentiment_7d_avg: float = 0.0
    sentiment_trend: str = "STABLE"  # IMPROVING, STABLE, DETERIORATING
    
    # News Metrics
    news_volume: int = 0
    news_velocity: float = 0.0  # Articles per day
    positive_articles: int = 0
    negative_articles: int = 0
    neutral_articles: int = 0
    
    # Topic Analysis
    earnings_mentions: int = 0
    analyst_mentions: int = 0
    product_mentions: int = 0
    legal_mentions: int = 0
    regulatory_mentions: int = 0
    merger_mentions: int = 0
    executive_mentions: int = 0
    
    # Headlines
    key_headlines: List[str] = field(default_factory=list)
    top_articles: List[Dict[str, Any]] = field(default_factory=list)
    
    # Social Sentiment
    social_sentiment_score: float = 0.0
    social_volume: int = 0
    
    # Controversy
    controversy_score: float = 0.0
    
    # Media Coverage
    media_coverage_score: float = 0.0

    def company_sentiment_score(self) -> float:
        """Return only the sentiment evidence eligible for company scoring."""
        if not self.provenance_computed:
            return float(self.sentiment_score or 0.0)
        return float(self.weighted_sentiment_score or 0.0)


@dataclass
class InsiderData:
    """Insider trading and institutional ownership data"""
    # Insider Activity
    insider_sentiment: str = "NEUTRAL"
    insider_confidence: float = 5.0
    recent_buys: int = 0
    recent_sells: int = 0
    buy_value: float = 0.0
    sell_value: float = 0.0
    net_flow: float = 0.0
    
    # Key Transactions
    key_transactions: List[str] = field(default_factory=list)
    key_transactions_detail: List[Dict[str, Any]] = field(default_factory=list)
    
    # Institutional
    institutional_ownership: float = 0.0
    institutional_change_qoq: float = 0.0
    top_holders_increasing: int = 0
    top_holders_decreasing: int = 0
    
    # Short Interest
    short_interest: float = 0.0
    short_ratio: float = 0.0
    days_to_cover: float = 0.0
    short_change_mom: float = 0.0
    
    # Smart Money
    smart_money_flow: str = "NEUTRAL"

    # Provenance. The zero counts above are only a measurement when a source
    # actually answered: "measured" | "not_fetched" (module skipped, e.g. fast
    # preview) | "unavailable" (no source responded). ``sources_ok`` counts the
    # insider-transaction sources that returned data.
    data_status: str = "measured"
    sources_ok: int = 0


@dataclass
class MarketIntelligence:
    """Analyst and market intelligence data"""
    # Analyst Ratings
    # "" = no consensus reported. A "HOLD" default published a Street rating
    # for names no analyst covers.
    analyst_rating: str = ""
    rating_score: float = 3.0  # 1-5 scale
    buy_ratings: int = 0
    hold_ratings: int = 0
    sell_ratings: int = 0
    rating_change_30d: str = "STABLE"
    
    # Price Targets
    target_low: float = 0.0
    target_mean: float = 0.0
    target_median: float = 0.0
    target_high: float = 0.0
    implied_upside: float = 0.0
    analyst_count: int = 0
    
    # Earnings
    next_earnings_date: str = "Unknown"
    earnings_estimate: float = 0.0
    revenue_estimate: float = 0.0
    last_earnings_surprise: float = 0.0
    earnings_beat_rate: float = 0.5
    
    # Revisions
    eps_revision_trend: str = "STABLE"
    revenue_revision_trend: str = "STABLE"
    
    # Sector/Industry
    sector_performance_30d: float = 0.0
    relative_strength_sector: float = 0.0
    industry_rank: int = 0


@dataclass
class CatalystData:
    """Upcoming catalysts and events with earnings history"""
    # Earnings - Enhanced with past/future detection
    earnings_date: str = "Unknown"
    earnings_time: str = "Unknown"
    days_to_earnings: int = -1
    is_past_earnings: bool = False  # NEW: Track if earnings date has passed
    
    # Last Earnings Results (NEW)
    last_earnings_date: str = "Unknown"
    last_earnings_eps_actual: float = 0.0
    last_earnings_eps_estimate: float = 0.0
    last_earnings_surprise_pct: float = 0.0
    last_earnings_result: str = "Unknown"  # BEAT, MISS, IN-LINE
    post_earnings_move: float = 0.0  # Price change post-earnings
    
    # Dividends
    ex_dividend_date: str = "Unknown"
    dividend_amount: float = 0.0
    dividend_yield: float = 0.0
    
    # Events
    upcoming_events: List[str] = field(default_factory=list)
    
    # Catalyst Score
    catalyst_score: int = 0
    near_term_catalyst: bool = False
    catalyst_type: str = "UNKNOWN"


@dataclass
class OwnershipMetrics:
    """Institutional ownership and positioning data"""
    # Institutional Ownership
    institutional_ownership_pct: float = 0.0
    institutional_change_qoq: float = 0.0  # Quarter-over-quarter change
    num_institutions: int = 0
    top_holders: List[str] = field(default_factory=list)  # Top 5 holders
    
    # Short Interest
    short_interest_pct: float = 0.0
    short_interest_shares: float = 0.0
    days_to_cover: float = 0.0
    short_change_pct: float = 0.0  # Change in short interest
    
    # Options Positioning
    put_call_oi_ratio: float = 0.0  # Open Interest ratio
    put_call_volume_ratio: float = 0.0  # Volume ratio
    
    # Trading Activity
    dark_pool_pct: float = 0.0  # % of volume in dark pools
    
    # Overall metrics
    insider_ownership_pct: float = 0.0
    float_short_pct: float = 0.0  # Short interest / float
    catalyst_type: str = "NONE"


@dataclass
class MacroEconomicData:
    """Macroeconomic environment data for institutional-grade analysis"""
    # Fed Policy
    fed_funds_rate: float = 5.25
    fed_outlook: str = "NEUTRAL"  # HAWKISH, NEUTRAL, DOVISH
    fed_outlook_reason: str = ""
    days_to_fomc: int = -1
    fomc_date: str = "Unknown"
    rate_change_probability: float = 0.0  # Probability of rate cut
    
    # Inflation & Economy
    cpi_yoy: float = 0.0
    inflation_trend: str = "STABLE"  # RISING, STABLE, FALLING
    gdp_growth: float = 0.0
    unemployment_rate: float = 0.0
    economic_outlook: str = "STABLE"  # EXPANSION, STABLE, CONTRACTION, RECESSION_RISK
    
    # Bond Market Signals
    treasury_10y: float = 0.0
    treasury_2y: float = 0.0
    yield_curve_spread: float = 0.0  # 10Y - 2Y, negative = inverted
    yield_curve_signal: str = "NORMAL"  # NORMAL, FLAT, INVERTED
    
    # Market-Wide Risk
    macro_risk_score: int = 5  # 1-10 scale
    macro_risk_factors: List[str] = field(default_factory=list)
    
    # Macro News Sentiment
    macro_news_sentiment: float = 0.0  # -1 to +1
    macro_headlines: List[str] = field(default_factory=list)


@dataclass
class MegaCapEarningsCalendar:
    """Market-moving earnings events tracker"""
    upcoming_earnings: List[Dict[str, Any]] = field(default_factory=list)
    earnings_this_week: List[str] = field(default_factory=list)
    earnings_next_week: List[str] = field(default_factory=list)
    high_impact_earnings: bool = False
    market_volatility_warning: str = ""
    days_to_next_mega_earnings: int = -1
    next_mega_ticker: str = ""


@dataclass
class ValuationMetrics:
    """DCF and valuation model outputs"""
    # DCF
    dcf_fair_value: float = 0.0
    dcf_upside: float = 0.0
    dcf_margin_of_safety: float = 0.0
    dcf_status: str = "unavailable"  # measured | unavailable | not_applicable
    dcf_method: str = ""
    
    # Graham Number
    graham_number: float = 0.0
    graham_upside: float = 0.0
    graham_status: str = "unavailable"  # measured | unavailable | rejected
    
    # Relative Valuation
    peer_avg_pe: float = 0.0
    relative_pe: float = 1.0
    peer_avg_pb: float = 0.0
    relative_pb: float = 1.0
    
    # Historical
    historical_pe_avg: float = 0.0
    pe_percentile: float = 50.0
    
    # Composite
    fair_value_estimate: float = 0.0
    valuation_score: float = 50.0
    valuation_label: str = "UNAVAILABLE"
    valuation_status: str = "unavailable"  # measured | unavailable | not_applicable
    fair_value_sources: List[str] = field(default_factory=list)
    
    # [ENHANCED] Composite Price Targets
    target_conservative: float = 0.0   # 25th percentile / low estimate
    target_base: float = 0.0           # Weighted composite
    target_aggressive: float = 0.0     # 75th percentile / high estimate
    # How much the contributing target estimates AGREE (0-100), or None when
    # there is only one and agreement is undefined. This replaces the old
    # a source-count "confidence": counting correlated sources — an
    # analyst target, a DCF that anchors to price and blends analyst targets,
    # and technical resistance -- measured neither independence nor consensus.
    target_agreement_pct: Optional[float] = None
    target_source_count: int = 0
    target_sources: List[str] = field(default_factory=list)
    # Each method's own estimate and its normalized weight in the blend, so
    # the page can show what the target is made of rather than a low/high.
    target_components: List[Dict[str, Any]] = field(default_factory=list)
    target_confidence: float = 0.0     # DEPRECATED: retained for old readers; mirrors agreement
    target_probability: float = 0.0    # Short-term Monte Carlo proxy, stored as percentage points
    target_timeframe: str = "12 months"


@dataclass
class MonteCarloResults:
    """Monte Carlo simulation results"""
    simulations_run: int = 0
    median_price: float = 0.0
    mean_price: float = 0.0
    percentile_5: float = 0.0
    percentile_25: float = 0.0
    percentile_75: float = 0.0
    percentile_95: float = 0.0
    probability_profit: float = 0.5
    probability_10pct_gain: float = 0.25
    probability_10pct_loss: float = 0.25
    expected_return: float = 0.0  # Percentage points, e.g. 4.1 means +4.1%
    var_95: float = 0.0  # Percentage points, e.g. -24.0 means -24.0%
    # The horizon every probability above is conditional on. `probability_*`
    # are TERMINAL comparisons at this horizon — P(price above X on day N) —
    # not path events, so they are NOT the chance of touching a level, and not
    # the chance of a target being reached before a stop.
    days_simulated: int = 0


@dataclass
class PositionSizing:
    """Position sizing recommendation"""
    recommended_shares: int = 0
    position_value: float = 0.0
    portfolio_weight: float = 0.0
    dollar_risk: float = 0.0
    risk_percent: float = 0.0
    kelly_optimal: float = 0.0
    is_viable: bool = False
    sizing_method: str = "RISK_PARITY"
    warnings: List[str] = field(default_factory=list)
    recommendation: str = ""
    max_portfolio_weight: float = 0.0


@dataclass
class TradeSetup:
    """Complete trade setup with entry/exit levels.

    [T1 FIX] ``generate()`` used to populate this only for STRONG_BUY / BUY /
    ACCUMULATE, and to explicitly zero it for SELL / STRONG_SELL. HOLD, REDUCE
    and NO_POSITION fell through BOTH branches and kept the all-zero defaults —
    and since the decision core derives its candidate from ``composite_score``
    independently of the signal, any HOLD scoring >= 55 became a buy-tier
    candidate with no stop. The consequences cascaded:

      * ``risk_reward_ratio`` stayed 0.0, so the report asserted "Poor R:R
        setup (0.0:1 below the RR.RESEARCH_MIN minimum) — risking 7% to gain 0%" and rejected
        the name on reasoning derived entirely from the empty struct;
      * ``apply_risk_overrides`` saw ``stop_loss=0.0`` and downgraded every
        such candidate to WATCHLIST, so the engine published watchlist entries
        instead of orders;
      * position sizing was skipped outright (it is gated on
        ``entry_price > 0 and stop_loss > 0``);
      * the decision snapshot printed ``UPSIDE: +0%``.

    Levels are now computed for any non-bearish stance, because a HOLD on a
    stock you already own still needs a stop. ``is_actionable_entry`` marks
    whether they constitute a new-money order or reference levels only, so no
    consumer has to infer intent from whether the numbers happen to be zero.
    """
    # Entry
    entry_price: float = 0.0
    entry_type: str = "LIMIT"  # LIMIT, MARKET, STOP_LIMIT

    # Provenance — see the class docstring.
    is_actionable_entry: bool = False   # True only for a buy-tier signal
    setup_basis: str = ""               # how the levels were derived
    levels_available: bool = False      # False means genuinely not computed
    # [T13 FIX] False when the valuation blend lands at or below the entry
    # reference: the engine's own DCF/analyst work says there is no long
    # reward here. The stop is still published — a holder needs one — but no
    # profit-taking targets are invented to fill the gap.
    has_long_reward: bool = True
    
    # Targets. ``target_base`` is the 12-month valuation target (context).
    # ``horizon_target`` is the level the Decision's own horizon can plausibly
    # reach -- the 12-month upside pro-rated to ``horizon_days`` -- and is the
    # ONLY target reward/risk is measured to, so reward and risk share one
    # horizon (the stop is sized for the same holding period).
    target_conservative: float = 0.0
    target_base: float = 0.0
    target_aggressive: float = 0.0
    horizon_target: float = 0.0
    horizon_days: int = field(default_factory=lambda: _T.LEVELS.RESEARCH_HORIZON_DAYS)
    valuation_horizon_days: int = field(default_factory=lambda: _T.LEVELS.VALUATION_HORIZON_DAYS)
    
    # Risk Management
    stop_loss: float = 0.0
    stop_type: str = "HARD"  # HARD, TRAILING, ATR
    trailing_stop_pct: float = 0.0
    
    # Risk/Reward
    risk_amount: float = 0.0
    reward_amount: float = 0.0
    risk_reward_ratio: float = 0.0
    
    # [T6 FIX] Both retained at 0.0 for serialization compatibility and
    # deliberately NOT emitted. `win_probability` was
    # `0.4 + confidence/100 * 0.2` — an arbitrary figure presented as a
    # probability — and `expected_value` was computed from it, so a made-up
    # win rate drove a dollar number on the report.
    expected_value: float = 0.0
    win_probability: float = 0.0
    confidence_tier: str = "MEDIUM"   # HIGH / MEDIUM / LOW — what is supportable
    win_probability_caveat: str = (
        "No calibrated win rate is claimed. Use the confidence tier."
    )

    # Timing
    time_horizon: str = "SWING"
    estimated_days: int = 30
    review_date: str = ""             # concrete date, not a vague band
    
    # Conditions
    entry_conditions: List[str] = field(default_factory=list)
    exit_conditions: List[str] = field(default_factory=list)


@dataclass
class ScoreBreakdown:
    """Multi-factor score breakdown with confidence intervals"""
    technical_score: float = 50.0
    fundamental_score: float = 50.0
    valuation_score: float = 50.0
    quality_score: float = 50.0
    sentiment_score: float = 50.0
    risk_score: float = 50.0
    composite_score: float = 50.0
    
    # Retained at 0.0 for serialization compatibility and never emitted: the
    # spread of heuristic sub-scores is not a confidence interval.
    score_confidence_margin: float = 0.0
    data_quality_note: str = ""
    
    # Weights used
    weights: Dict[str, float] = field(default_factory=dict)
    
    # Component details
    component_details: Dict[str, str] = field(default_factory=dict)


@dataclass
class SignalExplanation:
    """Explanation for why a particular signal was generated"""
    signal: str = "HOLD"
    primary_reason: str = ""
    composite_score: float = 50.0
    score_threshold: str = ""  # e.g., "below 65 BUY threshold"
    overrides_triggered: List[str] = field(default_factory=list)
    # Stable rule code of every downgrade the generator applied (including the
    # ones inside _determine_signal), so the Decision can attribute them.
    override_codes: List[str] = field(default_factory=list)
    supporting_factors: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    
    def get_summary(self) -> str:
        """Get a one-line summary of why this signal"""
        if self.overrides_triggered:
            return f"{self.signal}: {self.overrides_triggered[0]}"
        return f"{self.signal}: {self.primary_reason}"


@dataclass
class PeerComparison:
    """Comparison of stock metrics vs sector peers"""
    sector: str = "Unknown"
    sector_etf: str = ""
    peer_tickers: List[str] = field(default_factory=list)
    # Relative metrics (stock value, sector avg, pct difference)
    pe_stock: float = 0.0
    pe_sector_avg: float = 0.0
    pe_vs_sector: float = 0.0  # % difference
    roe_stock: float = 0.0
    roe_sector_avg: float = 0.0
    roe_vs_sector: float = 0.0
    pb_stock: float = 0.0
    pb_sector_avg: float = 0.0
    pb_vs_sector: float = 0.0
    # Ranking
    sector_rank: int = 0
    sector_total: int = 0
    overall_assessment: str = ""  # "Outperforming", "In-line", "Underperforming"


@dataclass 
class InvestmentThesis:
    """Investment thesis summary"""
    summary: str = ""
    bull_case: List[str] = field(default_factory=list)
    bear_case: List[str] = field(default_factory=list)
    catalysts: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    key_metrics: Dict[str, Any] = field(default_factory=dict)
    competitive_position: str = ""
    recommendation_rationale: str = ""


@dataclass
class AnalysisResult:
    """Complete analysis result"""
    # Basic Info
    ticker: str = ""
    company_name: str = ""
    sector: str = "Unknown"
    industry: str = "Unknown"
    market_cap: float = 0.0
    currency: str = "USD"
    currency_symbol: str = "$"
    exchange: str = "UNKNOWN"

    # Traceable top-level market-data lineage. Derived/AI interpretation must
    # never be presented as if it were a provider fact.
    data_provider: str = "yfinance"
    retrieval_timestamp: str = ""
    market_timestamp: str = ""
    adjustment_status: str = "split_dividend_adjusted"
    data_quality_status: str = "unknown"

    # [DECISION-CORE] Hard-gate / consistency flags
    is_invalid: bool = False
    invalid_reasons: List[str] = field(default_factory=list)
    final_action: str = ""            # FinalAction.value — single source of truth
    final_action_reasons: List[str] = field(default_factory=list)
    final_action_codes: List[str] = field(default_factory=list)
    final_blocking_reasons: List[str] = field(default_factory=list)
    final_action_transitions: List[Dict[str, Any]] = field(default_factory=list)
    final_stance: str = ""            # pre-ownership directional view for outcome tracking
    decision: Optional[Dict[str, Any]] = None  # published single-horizon contract
    has_existing_position: bool = False
    # [F7] Corporate-event check (engine.corporate_events.EventCheck.to_dict()).
    event_check: Optional[Dict[str, Any]] = None
    # Portfolio-only fund policy; the shared Research/Picks decision stays unchanged.
    portfolio_fund_decision: Optional[Dict[str, Any]] = None

    # [DATA-RELIABILITY] Why the analyzer could (not) produce a verdict. Lets the
    # reconciler / UI tell a genuine invalid ticker apart from a transient
    # provider failure. One of engine.data_status.DataStatus.* values.
    data_status: str = "ok"
    data_status_reason: str = ""

    # [ETF SUPPORT] Top-level fund flags so display & decision engine can
    # treat ETFs / funds differently from individual equities. The
    # FundamentalData.is_etf flag is the per-section copy; this one is the
    # canonical, instrument-level identifier.
    is_etf: bool = False
    quote_type: str = "EQUITY"        # EQUITY / ETF / MUTUALFUND / INDEX / CRYPTO / ...
    net_assets: float = 0.0           # alias for ETFs (totalAssets)
    expense_ratio: float = 0.0        # surfaced for ETFs in the report header

    # [ASSET-TYPE] Structural instrument classification so the UI can make
    # sections conditional (e.g. hide company DCF / insider / Piotroski for
    # ETFs, show ADR caveats for foreign issuers). `asset_type` is the
    # PRIMARY structural class (invalid > etf > adr > financial >
    # common_stock); `is_high_volatility` is an independent overlay flag.
    asset_type: str = ""              # common_stock / etf / adr / financial / high_volatility / invalid
    is_adr: bool = False              # American Depositary Receipt / foreign issuer
    is_financial: bool = False        # Financial-sector issuer (bank/insurer/etc.)
    is_high_volatility: bool = False  # elevated realized vol / beta overlay
    country: str = ""                 # issuer country (drives ADR detection)
    business_summary: str = ""        # provider's business description (fact)
    # Stock vs local benchmark over standard windows (engine.relative_performance).
    relative_performance: Dict[str, Any] = field(default_factory=dict)

    # [PROFILING] Per-stage timing records for the deep-dive flow.
    # Each entry: {stage, duration_ms, cached, provider, status}.
    stage_timings: List[Dict[str, Any]] = field(default_factory=list)

    # [F02] Outcome of the pre-trade portfolio exposure check, as returned by
    # PortfolioExposureManager.check_position_limits. `holdings_known` is False
    # when no account snapshot was declared, in which case the sizing above is
    # a hypothetical allocation rather than a portfolio-checked order.
    portfolio_risk_check: Dict[str, Any] = field(default_factory=dict)

    # [PERF-FASTMODE] True when the tear sheet was produced in Fast Mode —
    # the network-heavy optional modules (news/sentiment, insider, analyst
    # consensus, catalysts, peer comparison) were SKIPPED and replaced with
    # neutral defaults. The resulting recommendation is PRELIMINARY and must
    # be labelled as such in the UI; the full analysis re-runs every module.
    fast_mode: bool = False

    # Price Data
    current_price: float = 0.0
    previous_close: float = 0.0
    day_change: float = 0.0
    day_change_pct: float = 0.0
    
    # Extended Hours Pricing (Pre-Market / After-Hours)
    pre_market_price: float = 0.0
    pre_market_change: float = 0.0
    pre_market_change_pct: float = 0.0
    after_hours_price: float = 0.0
    after_hours_change: float = 0.0
    after_hours_change_pct: float = 0.0
    market_state: str = "REGULAR"  # PRE, REGULAR, POST, CLOSED
    last_trade_time: str = ""
    
    # Signal
    signal: Signal = Signal.HOLD
    confidence_score: float = 50.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    
    # Market Regime
    market_regime: MarketRegime = MarketRegime.SIDEWAYS
    
    # Analysis Components
    technical: Optional[TechnicalIndicators] = None
    fundamental: Optional[FundamentalData] = None
    risk: Optional[RiskMetrics] = None
    sentiment: Optional[SentimentData] = None
    insider: Optional[InsiderData] = None
    market_intel: Optional[MarketIntelligence] = None
    catalysts: Optional[CatalystData] = None
    valuation: Optional[ValuationMetrics] = None
    monte_carlo: Optional[MonteCarloResults] = None
    ownership: Optional[OwnershipMetrics] = None  # NEW: Ownership & positioning data
    
    # Trade Setup
    trade_setup: Optional[TradeSetup] = None
    position_sizing: Optional[PositionSizing] = None
    
    # Scores
    scores: Optional[ScoreBreakdown] = None
    
    # Thesis
    thesis: Optional[InvestmentThesis] = None
    
    # Signal Explanation (Why This Signal)
    signal_explanation: Optional[SignalExplanation] = None
    
    # Peer Comparison
    peer_comparison: Optional[PeerComparison] = None
    
    # Key Outputs
    recommendation_text: str = ""
    key_insights: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)
    unexplained_move: bool = False
    unexplained_move_note: str = ""
    
    # Timestamps
    analysis_timestamp: str = ""
    # Provider-reported 52-week range (unadjusted traded extremes), the fact
    # shown to readers; the technical engine's own range is dividend-adjusted.
    week_52_high_reported: float = 0.0
    week_52_low_reported: float = 0.0
    # Symbol resolution: what the user typed, what was analysed, and why they
    # differ (share-class spelling, market routing, NSE/BSE sibling listing).
    requested_symbol: str = ""
    symbol_note: str = ""
    # History actually available at the provider (also set on failures).
    history_bars: int = 0
    first_bar_date: str = ""
    last_bar_date: str = ""
    data_freshness: str = ""
    
    def __post_init__(self):
        """Initialize nested dataclasses if None"""
        if self.technical is None:
            self.technical = TechnicalIndicators()
        if self.fundamental is None:
            self.fundamental = FundamentalData()
        if self.risk is None:
            self.risk = RiskMetrics()
        if self.sentiment is None:
            self.sentiment = SentimentData()
        if self.insider is None:
            self.insider = InsiderData()
        if self.market_intel is None:
            self.market_intel = MarketIntelligence()
        if self.catalysts is None:
            self.catalysts = CatalystData()
        if self.valuation is None:
            self.valuation = ValuationMetrics()
        if self.trade_setup is None:
            self.trade_setup = TradeSetup()
        if self.position_sizing is None:
            self.position_sizing = PositionSizing()
        if self.scores is None:
            self.scores = ScoreBreakdown()
        if self.thesis is None:
            self.thesis = InvestmentThesis()
        if self.signal_explanation is None:
            self.signal_explanation = SignalExplanation()
        if self.peer_comparison is None:
            self.peer_comparison = PeerComparison()


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers"""
    try:
        if denominator is None or abs(denominator) < 1e-12 or np.isnan(denominator):
            return default
        result = numerator / denominator
        return default if np.isnan(result) or np.isinf(result) else result
    except Exception:
        return default


def _is_positive_number(value: Any) -> bool:
    """True only for a finite, strictly positive number.

    Guards the market-cap fallback chain: None, "", NaN and 0 must all read as
    "absent" so a bad value is never preferred over the next source in line.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def safe_get(dictionary: Dict, key: str, default: Any = 0.0, multiplier: float = 1.0) -> Any:
    """Safely get value from dictionary with optional multiplier"""
    try:
        value = dictionary.get(key)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value) * multiplier if isinstance(value, (int, float)) else value
    except Exception:
        return default


def _reported(info: Dict, *keys: str) -> bool:
    """True when at least one of the provider keys carries a real number."""
    for key in keys:
        value = (info or {}).get(key)
        if value is None:
            continue
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return True
    return False


# Fewer reported inputs than this and the F-score is a guess, not a score.
PIOTROSKI_MIN_INPUTS = 5


def _piotroski_from_fundamentals(result: Any, info: Dict) -> Tuple[int, int]:
    """Piotroski F-score with an honest input count.

    Returns ``(score, components_available)``. Each of the nine tests is only
    evaluated when its raw provider field exists; an absent field neither
    passes nor fails, so the caller can report N/A when too few were present.
    The tests are the standard nine, using the single-period proxies the
    provider exposes (there is no prior-year statement in the info payload):

      profitability : ROA > 0, CFO > 0, net margin > 0, CFO > net income
      leverage      : debt/equity < 1, current ratio > 1.5
      efficiency    : gross margin > 20%, revenue growth > 0, EPS growth > 0
    """
    info = info or {}
    tests = [
        (_reported(info, 'returnOnAssets'), result.roa > 0),
        (_reported(info, 'operatingCashflow'), result.operating_cash_flow > 0),
        (_reported(info, 'profitMargins'), result.net_margin > 0),
        (_reported(info, 'operatingCashflow') and _reported(info, 'netIncomeToCommon', 'netIncome'),
         result.operating_cash_flow > safe_get(info, 'netIncomeToCommon', safe_get(info, 'netIncome', 0))),
        (_reported(info, 'debtToEquity') and result.has('debt_to_equity'), result.debt_to_equity < 1),
        (_reported(info, 'currentRatio'), result.current_ratio > 1.5),
        (_reported(info, 'grossMargins'), result.gross_margin > 20),
        (_reported(info, 'revenueGrowth'), result.revenue_growth_yoy > 0),
        (_reported(info, 'earningsGrowth', 'earningsQuarterlyGrowth'), result.eps_growth_yoy > 0),
    ]
    available = sum(1 for reported, _ in tests if reported)
    score = sum(1 for reported, passed in tests if reported and passed)
    return score, available


def piotroski_display_value(score: int, available: int) -> Optional[int]:
    """The score to publish, or None when too few inputs were reported."""
    if int(available or 0) < PIOTROSKI_MIN_INPUTS:
        return None
    return int(score)


def piotroski_is_reportable(fundamental: Any) -> bool:
    """True when enough Piotroski inputs were reported to act on the score.

    Every threshold read of ``piotroski_f_score`` (">= 7 means quality",
    "<= 3 means weak") must pass through this first. The raw attribute is a
    count over the *reported* tests only, so on a ticker with two reported
    inputs it is structurally small — reading it as "weak fundamentals" turns
    missing data into a bearish signal.
    """
    if fundamental is None:
        return False
    available = int(getattr(fundamental, 'piotroski_components_available', 0) or 0)
    return available >= PIOTROSKI_MIN_INPUTS


def piotroski_display_text(fundamental: Any) -> str:
    """Terminal/table rendering: a real score, or an honest N/A with coverage."""
    if fundamental is None:
        return "N/A"
    available = int(getattr(fundamental, 'piotroski_components_available', 0) or 0)
    score = int(getattr(fundamental, 'piotroski_f_score', 0) or 0)
    if available < PIOTROSKI_MIN_INPUTS:
        return f"N/A ({available}/9 inputs)"
    return f"{score}/9 [{'Quality' if score >= 7 else 'Weak'}]"


# Plausibility window per variant. Both models are only ever read against
# their distress/safe thresholds (public: 1.81 / 2.99, private Z': 1.23 /
# 2.90), so the upper bound exists purely to reject malformed inputs. The
# public bound is wide because 0.6·(market cap / liabilities) has no natural
# ceiling: a debt-light mega-cap prices out in the tens or low hundreds.
ALTMAN_Z_BOUNDS = {
    "public": (-20.0, 300.0),
    "private": (-10.0, 20.0),
}


def compute_altman_z(*, total_assets: float, current_assets: float,
                     current_liabilities: float, retained_earnings: float,
                     ebit: float, revenue: float, liabilities: float,
                     book_equity: float, market_cap: float = 0.0
                     ) -> Tuple[Optional[float], str]:
    """Altman Z with the coefficient set that matches the equity input.

    The original (public-company) model is
        Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5
    with X4 = MARKET value of equity / total liabilities. The previous
    implementation fed BOOK equity into that X4 — mixing the public-model
    coefficients with the private-model input. When market cap is available
    the public model is used as published; otherwise the Z' private-firm
    variant (0.717 / 0.847 / 3.107 / 0.420 / 0.998, book equity) is used and
    the variant is reported so the reader knows which scale applies.

    Returns ``(z, variant)`` with ``variant`` in {"public", "private"}, or
    ``(None, "")`` when the inputs cannot support a score.
    """
    try:
        vals = (total_assets, current_assets, current_liabilities, retained_earnings,
                ebit, revenue, liabilities, book_equity, market_cap)
        vals = tuple(float(v or 0.0) for v in vals)
    except (TypeError, ValueError):
        return None, ""
    (total_assets, current_assets, current_liabilities, retained_earnings,
     ebit, revenue, liabilities, book_equity, market_cap) = vals
    if total_assets <= 0 or liabilities <= 0 or not all(math.isfinite(v) for v in vals):
        return None, ""
    x1 = (current_assets - current_liabilities) / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x5 = revenue / total_assets
    if market_cap > 0:
        x4 = market_cap / liabilities
        z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
        variant = "public"
    elif book_equity > 0:
        x4 = book_equity / liabilities
        z = 0.717 * x1 + 0.847 * x2 + 3.107 * x3 + 0.420 * x4 + 0.998 * x5
        variant = "private"
    else:
        return None, ""
    # Sanity bounds catch unit errors (liabilities reported in the wrong
    # scale), NOT healthy balance sheets. They must be per-variant: X4 is
    # market cap / liabilities in the public model, so a cash-rich, low-debt
    # mega-cap legitimately scores far above the private model's ceiling —
    # a single -10..20 window silently reported the *safest* companies as
    # "unavailable", and flipped the same firm between N/A and a Z' number
    # depending only on whether marketCap happened to resolve.
    lo, hi = ALTMAN_Z_BOUNDS[variant]
    if not math.isfinite(z) or not (lo <= z <= hi):
        return None, ""
    return float(z), variant


def format_currency(value: float, symbol: str = "$", decimals: int = 2) -> str:
    """Format number as currency"""
    try:
        if abs(value) >= 1e12:
            return f"{symbol}{value/1e12:.2f}T"
        elif abs(value) >= 1e9:
            return f"{symbol}{value/1e9:.2f}B"
        elif abs(value) >= 1e6:
            return f"{symbol}{value/1e6:.2f}M"
        elif abs(value) >= 1e3:
            return f"{symbol}{value/1e3:.2f}K"
        else:
            return f"{symbol}{value:,.{decimals}f}"
    except Exception:
        return f"{symbol}0.00"


def format_percent(value: float, decimals: int = 1) -> str:
    """Format number as percentage"""
    try:
        return f"{value:+.{decimals}f}%" if value >= 0 else f"{value:.{decimals}f}%"
    except Exception:
        return "0.0%"


def _format_yield_cell(div_yield_fraction: float, market_cap: float = 0.0) -> str:
    """Safe dividend-yield cell formatter (input is a FRACTION, e.g. 0.0088).

    Uses the shared validator when available so the display can never show
    things like '23.00%' or '111%' for normal large caps.
    """
    try:
        y = float(div_yield_fraction or 0.0)
    except (TypeError, ValueError):
        y = 0.0
    # Tolerate legacy callers that already pre-multiplied by 100
    if y > 1.0:
        y = y / 100.0
    if HAS_DECISION_CORE:
        try:
            ok, _ = validate_dividend_yield(y, market_cap=market_cap)
        except Exception:
            ok = (y <= 0.50)
    else:
        ok = (y <= 0.50)
    if y <= 0:
        return "   0.00%"
    if not ok:
        return "  n/a   "
    return f"{y * 100:>8.2f}%"


def parse_datetime(value: Any) -> Optional[datetime]:
    """Parse various datetime formats"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        elif isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
    except Exception:
        pass
    return None


def get_currency_symbol(currency_code: str) -> str:
    """Get currency symbol from code"""
    return CURRENCY_SYMBOLS.get(currency_code.upper(), "$") if currency_code else "$"


def get_composite_grade(score: float) -> str:
    """Convert composite score to letter grade"""
    for cutoff, grade in Config.GRADE_CUTOFFS:
        if score >= cutoff:
            return grade
    return "F"


def arrow_for(value: Any) -> str:
    """Return a direction glyph for the sign of the displayed number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "─"
    if not math.isfinite(number) or number == 0:
        return "─"
    return "▲" if number > 0 else "▼"


def direction_style(value: Any) -> str:
    """Return the Rich color corresponding to :func:`arrow_for`."""
    glyph = arrow_for(value)
    return "green" if glyph == "▲" else "red" if glyph == "▼" else "yellow"


def days_until(target: Any, today: Any = None) -> Optional[int]:
    """Calendar-day difference, independent of the current time of day."""
    if target is None:
        return None
    try:
        target_ts = pd.Timestamp(target)
        today_ts = pd.Timestamp(today if today is not None else datetime.now())
        if pd.isna(target_ts) or pd.isna(today_ts):
            return None
        return (target_ts.date() - today_ts.date()).days
    except (TypeError, ValueError, OverflowError):
        return None


# =============================================================================
# CACHE MANAGER
# =============================================================================

class CacheManager:
    """Thread-safe LRU cache with TTL"""
    
    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.cache: OrderedDict = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        with self.lock:
            if key in self.cache:
                timestamp, value = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    self.cache.move_to_end(key)
                    self.hits += 1
                    return value
                else:
                    del self.cache[key]
            self.misses += 1
            return None
    
    def set(self, key: str, value: Any):
        """Set value in cache"""
        with self.lock:
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = (time.time(), value)
            self.cache.move_to_end(key)
    
    def clear(self):
        """Clear the cache"""
        with self.lock:
            self.cache.clear()
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:  # Thread-safe stats access
            total = self.hits + self.misses
            return {
                'size': len(self.cache),
                'max_size': self.max_size,
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': self.hits / total if total > 0 else 0
            }


# =============================================================================
# API CLIENT
# =============================================================================

class APIClient:
    """Unified API client with rate limiting and error handling"""
    
    def __init__(self, config: Dict[str, str]):
        self.news_api_key = config.get('news_api_key') or os.getenv('NEWS_API_KEY', '')
        self.finnhub_key = config.get('finnhub_key') or os.getenv('FINNHUB_API_KEY', '')
        self.fmp_key = config.get('fmp_key') or os.getenv('FMP_API_KEY', '')
        self.polygon_key = config.get('polygon_key') or os.getenv('POLYGON_API_KEY', '')
        self.alpha_vantage_key = config.get('alpha_vantage_key') or os.getenv('ALPHA_VANTAGE_KEY', '')
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'QUANTA Elite v2.0',
            'Accept': 'application/json'
        })
        
        self.rate_limiter: Dict[str, List[float]] = defaultdict(list)
        self.disabled_sources: Set[str] = set()
        self.lock = threading.Lock()
        
        # [POLYGON.IO] Initialize HybridDataProvider for institutional-grade data
        self.hybrid_data = None
        if HAS_POLYGON:
            try:
                self.hybrid_data = HybridDataProvider(self.polygon_key if self.polygon_key else None)
                if self.hybrid_data.use_polygon:
                    logger.info("✓ Polygon.io data provider active")
            except Exception as e:
                logger.debug(f"HybridDataProvider init failed: {e}")
        
        # Log available APIs
        available = []
        if self.news_api_key: available.append("NewsAPI")
        if self.finnhub_key: available.append("Finnhub")
        if self.fmp_key: available.append("FMP")
        if self.polygon_key or (self.hybrid_data and self.hybrid_data.use_polygon): available.append("Polygon")
        logger.info(f"API Client initialized with: {', '.join(available) or 'No external APIs'}")
    
    def _check_rate_limit(self, source: str, limit: int = 60):
        """Check and enforce rate limiting without blocking other threads"""
        sleep_time = 0
        with self.lock:
            now = time.time()
            minute_ago = now - 60
            self.rate_limiter[source] = [t for t in self.rate_limiter[source] if t > minute_ago]
            
            if len(self.rate_limiter[source]) >= limit:
                sleep_time = 60 - (now - self.rate_limiter[source][0])
        
        # Sleep OUTSIDE the lock so other threads/sources aren't blocked
        if sleep_time > 0:
            logger.warning(f"Rate limiting {source}: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        
        # Re-acquire lock to record the timestamp
        with self.lock:
            self.rate_limiter[source].append(time.time())
    
    def fetch_json(self, url: str, params: Optional[Dict] = None, 
                   source: str = "generic", timeout: int = 15,
                   rate_limit: int = 60) -> Optional[Any]:
        """Fetch JSON from URL with error handling"""
        with self.lock:
            if source in self.disabled_sources:
                return None
        
        try:
            self._check_rate_limit(source, rate_limit)
            response = self.session.get(url, params=params, timeout=timeout)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code in [401, 403]:
                with self.lock:
                    if source not in self.disabled_sources:
                        logger.warning(f"{source}: Authentication failed ({response.status_code})")
                        self.disabled_sources.add(source)
                return None
            elif response.status_code == 429:
                logger.warning(f"{source}: Rate limited")
                time.sleep(2)
                return None
            else:
                logger.debug(f"{source}: Request failed ({response.status_code})")
                return None
        except requests.Timeout:
            logger.warning(f"{source}: Request timeout")
            return None
        except Exception as e:
            logger.debug(f"{source}: Request error - {e}")
            return None
    
    def get_historical(self, ticker: str, period: str = '1y') -> pd.DataFrame:
        """Get historical data using Polygon.io (primary) or yfinance (fallback)"""
        # Try HybridDataProvider first
        if self.hybrid_data:
            try:
                hist = self.hybrid_data.get_historical(ticker, period)
                if not hist.empty:
                    return hist
            except Exception:
                pass
        
        # Fallback to yfinance
        try:
            stock = yf.Ticker(ticker)
            return stock.history(period=period, auto_adjust=True)
        except Exception as e:
            logger.debug(f"Historical data fetch failed for {ticker}: {e}")
            return pd.DataFrame()

    def fetch_news(self, query: str, limit: int = 10) -> List[Dict]:
        """Fetch news articles from NewsAPI for a given query"""
        if not self.news_api_key:
            return []
        
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': query,
                'apiKey': self.news_api_key,
                'pageSize': limit,
                'sortBy': 'publishedAt',
                'language': 'en'
            }
            data = self.fetch_json(url, params=params, source="newsapi", rate_limit=30)
            if data and data.get('status') == 'ok':
                articles = data.get('articles', [])
                return [
                    {
                        'title': a.get('title', ''),
                        'description': a.get('description', ''),
                        'source': a.get('source', {}).get('name', 'Unknown'),
                        'published': a.get('publishedAt', ''),
                        'url': a.get('url', '')
                    }
                    for a in articles[:limit]
                ]
        except Exception as e:
            logger.debug(f"NewsAPI fetch error: {e}")
        
        return []


# =============================================================================
# TECHNICAL ANALYSIS ENGINE
# =============================================================================

class TechnicalAnalyzer:
    """Advanced technical analysis with multi-indicator scoring"""
    
    def __init__(self):
        self.min_data_points = Config.MIN_DATA_POINTS
    
    def analyze(self, data: pd.DataFrame) -> TechnicalIndicators:
        """Perform comprehensive technical analysis"""
        result = TechnicalIndicators()
        
        if data is None or len(data) < self.min_data_points:
            logger.warning("Insufficient data for technical analysis")
            return result
        
        try:
            close = data['Close'].dropna()
            high = data['High'].dropna()
            low = data['Low'].dropna()
            volume = data['Volume'].dropna() if 'Volume' in data.columns else pd.Series([1e6] * len(close))
            
            current_price = float(close.iloc[-1])
            
            # Momentum Indicators
            result = self._calculate_momentum(close, high, low, result)
            
            # Trend Indicators
            result = self._calculate_trend(close, high, low, result)
            
            # Moving Averages
            result = self._calculate_moving_averages(close, current_price, result)
            
            # Volatility Indicators
            result = self._calculate_volatility(close, high, low, current_price, result)
            
            # Volume Indicators
            result = self._calculate_volume(close, high, low, volume, current_price, result)
            
            # Ichimoku Cloud
            result = self._calculate_ichimoku(close, high, low, current_price, result)
            
            # Support/Resistance
            result = self._calculate_support_resistance(data, current_price, result)
            
            # Market Structure
            result = self._analyze_market_structure(close, high, low, result)
            
            # 52-Week Range
            result = self._calculate_52week_range(close, current_price, result, high=high, low=low)

            # Candlestick Patterns (must run before composite so score can use it)
            result = self._detect_candlestick_patterns(data, result)

            # Composite Scores
            result = self._calculate_composite_scores(result)
            
        except Exception as e:
            logger.error(f"Technical analysis error: {e}")
        
        return result
    
    def _calculate_momentum(self, close: pd.Series, high: pd.Series, low: pd.Series, 
                           result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate momentum indicators"""
        try:
            # RSI
            rsi_indicator = RSIIndicator(close, window=14)
            result.rsi = float(rsi_indicator.rsi().iloc[-1])
            if np.isnan(result.rsi):
                result.rsi = 50.0
            
            # RSI Divergence Detection
            result.rsi_divergence = self._detect_rsi_divergence(close, rsi_indicator.rsi())
            
            # Stochastic
            stoch = StochasticOscillator(high, low, close)
            result.stochastic_k = float(stoch.stoch().iloc[-1]) if not np.isnan(stoch.stoch().iloc[-1]) else 50.0
            result.stochastic_d = float(stoch.stoch_signal().iloc[-1]) if not np.isnan(stoch.stoch_signal().iloc[-1]) else 50.0
            
            # Williams %R
            williams = WilliamsRIndicator(high, low, close)
            result.williams_r = float(williams.williams_r().iloc[-1]) if not np.isnan(williams.williams_r().iloc[-1]) else -50.0
            
            # CCI
            cci = CCIIndicator(high, low, close)
            result.cci = float(cci.cci().iloc[-1]) if not np.isnan(cci.cci().iloc[-1]) else 0.0
            
        except Exception as e:
            logger.debug(f"Momentum calculation error: {e}")
        
        return result
    
    def _detect_rsi_divergence(self, price: pd.Series, rsi: pd.Series) -> str:
        """Detect bullish or bearish RSI divergence"""
        try:
            if len(price) < 20 or len(rsi) < 20:
                return "NONE"
            
            price_tail = price.tail(20).values
            rsi_tail = rsi.tail(20).dropna().values
            
            if len(rsi_tail) < 15:
                return "NONE"
            
            # Find local minima for bullish divergence
            price_lows = []
            rsi_lows = []
            for i in range(2, len(price_tail) - 2):
                if price_tail[i] < price_tail[i-1] and price_tail[i] < price_tail[i+1]:
                    if i < len(rsi_tail) - 2:
                        price_lows.append((i, price_tail[i]))
                        rsi_lows.append((i, rsi_tail[i]))
            
            if len(price_lows) >= 2:
                # Bullish: Price making lower lows, RSI making higher lows
                if price_lows[-1][1] < price_lows[-2][1] and rsi_lows[-1][1] > rsi_lows[-2][1]:
                    return "BULLISH"
            
            # Find local maxima for bearish divergence
            price_highs = []
            rsi_highs = []
            for i in range(2, len(price_tail) - 2):
                if price_tail[i] > price_tail[i-1] and price_tail[i] > price_tail[i+1]:
                    if i < len(rsi_tail) - 2:
                        price_highs.append((i, price_tail[i]))
                        rsi_highs.append((i, rsi_tail[i]))
            
            if len(price_highs) >= 2:
                # Bearish: Price making higher highs, RSI making lower highs
                if price_highs[-1][1] > price_highs[-2][1] and rsi_highs[-1][1] < rsi_highs[-2][1]:
                    return "BEARISH"
            
        except Exception:
            pass
        
        return "NONE"
    
    def _calculate_trend(self, close: pd.Series, high: pd.Series, low: pd.Series,
                        result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate trend indicators"""
        try:
            # MACD
            macd = MACD(close)
            result.macd_value = float(macd.macd().iloc[-1]) if not np.isnan(macd.macd().iloc[-1]) else 0.0
            result.macd_signal_line = float(macd.macd_signal().iloc[-1]) if not np.isnan(macd.macd_signal().iloc[-1]) else 0.0
            result.macd_histogram = float(macd.macd_diff().iloc[-1]) if not np.isnan(macd.macd_diff().iloc[-1]) else 0.0
            
            # MACD Signal
            if result.macd_value > result.macd_signal_line and result.macd_histogram > 0:
                result.macd_signal = "BULLISH"
            elif result.macd_value < result.macd_signal_line and result.macd_histogram < 0:
                result.macd_signal = "BEARISH"
            else:
                result.macd_signal = "NEUTRAL"
            
            # ADX
            adx = ADXIndicator(high, low, close)
            result.adx = float(adx.adx().iloc[-1]) if not np.isnan(adx.adx().iloc[-1]) else 25.0
            result.plus_di = float(adx.adx_pos().iloc[-1]) if not np.isnan(adx.adx_pos().iloc[-1]) else 25.0
            result.minus_di = float(adx.adx_neg().iloc[-1]) if not np.isnan(adx.adx_neg().iloc[-1]) else 25.0
            
            # Trend Strength
            if result.adx >= 40:
                result.trend_strength = "VERY_STRONG"
            elif result.adx >= 30:
                result.trend_strength = "STRONG"
            elif result.adx >= 20:
                result.trend_strength = "MODERATE"
            else:
                result.trend_strength = "WEAK"
            
            # Trend Direction from DI
            if result.plus_di > result.minus_di and result.adx > 20:
                result.trend_direction = "UP"
            elif result.minus_di > result.plus_di and result.adx > 20:
                result.trend_direction = "DOWN"
            else:
                result.trend_direction = "SIDEWAYS"
            
            # Aroon
            aroon = AroonIndicator(high, low)
            result.aroon_up = float(aroon.aroon_up().iloc[-1]) if not np.isnan(aroon.aroon_up().iloc[-1]) else 50.0
            result.aroon_down = float(aroon.aroon_down().iloc[-1]) if not np.isnan(aroon.aroon_down().iloc[-1]) else 50.0
            result.aroon_oscillator = result.aroon_up - result.aroon_down
            
        except Exception as e:
            logger.debug(f"Trend calculation error: {e}")
        
        return result
    
    def _calculate_moving_averages(self, close: pd.Series, current_price: float,
                                   result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate moving averages and crossovers"""
        try:
            # SMAs
            if len(close) >= 20:
                result.sma_20 = float(close.rolling(20).mean().iloc[-1])
                result.price_vs_sma20 = safe_divide(current_price - result.sma_20, result.sma_20, 0) * 100
            
            if len(close) >= 50:
                result.sma_50 = float(close.rolling(50).mean().iloc[-1])
                result.price_vs_sma50 = safe_divide(current_price - result.sma_50, result.sma_50, 0) * 100
            
            if len(close) >= 200:
                result.sma_200 = float(close.rolling(200).mean().iloc[-1])
                result.price_vs_sma200 = safe_divide(current_price - result.sma_200, result.sma_200, 0) * 100
            
            # EMAs
            ema12 = EMAIndicator(close, window=12)
            ema26 = EMAIndicator(close, window=26)
            result.ema_12 = float(ema12.ema_indicator().iloc[-1]) if not np.isnan(ema12.ema_indicator().iloc[-1]) else current_price
            result.ema_26 = float(ema26.ema_indicator().iloc[-1]) if not np.isnan(ema26.ema_indicator().iloc[-1]) else current_price
            
            # Golden/Death Cross Detection
            if result.sma_50 > 0 and result.sma_200 > 0:
                sma50_prev = float(close.rolling(50).mean().iloc[-10]) if len(close) >= 60 else result.sma_50
                sma200_prev = float(close.rolling(200).mean().iloc[-10]) if len(close) >= 210 else result.sma_200
                
                # Golden Cross: SMA50 crosses above SMA200
                if sma50_prev < sma200_prev and result.sma_50 > result.sma_200:
                    result.golden_cross = True
                # Death Cross: SMA50 crosses below SMA200
                elif sma50_prev > sma200_prev and result.sma_50 < result.sma_200:
                    result.death_cross = True
            
        except Exception as e:
            logger.debug(f"Moving average calculation error: {e}")
        
        return result
    
    def _calculate_volatility(self, close: pd.Series, high: pd.Series, low: pd.Series,
                              current_price: float, result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate volatility indicators"""
        try:
            # ATR
            atr = AverageTrueRange(high, low, close)
            result.atr = float(atr.average_true_range().iloc[-1]) if not np.isnan(atr.average_true_range().iloc[-1]) else current_price * 0.02
            result.atr_percent = safe_divide(result.atr, current_price, 0.02) * 100
            
            # Bollinger Bands
            bb = BollingerBands(close)
            result.bollinger_upper = float(bb.bollinger_hband().iloc[-1]) if not np.isnan(bb.bollinger_hband().iloc[-1]) else current_price * 1.02
            result.bollinger_middle = float(bb.bollinger_mavg().iloc[-1]) if not np.isnan(bb.bollinger_mavg().iloc[-1]) else current_price
            result.bollinger_lower = float(bb.bollinger_lband().iloc[-1]) if not np.isnan(bb.bollinger_lband().iloc[-1]) else current_price * 0.98
            result.bollinger_width = safe_divide(result.bollinger_upper - result.bollinger_lower, result.bollinger_middle, 0.04) * 100
            
            # Bollinger Signal
            if current_price > result.bollinger_upper:
                result.bollinger_signal = "OVERBOUGHT"
            elif current_price < result.bollinger_lower:
                result.bollinger_signal = "OVERSOLD"
            else:
                result.bollinger_signal = "NEUTRAL"
            
            # Keltner Channel
            keltner = KeltnerChannel(high, low, close)
            result.keltner_upper = float(keltner.keltner_channel_hband().iloc[-1]) if not np.isnan(keltner.keltner_channel_hband().iloc[-1]) else current_price * 1.02
            result.keltner_lower = float(keltner.keltner_channel_lband().iloc[-1]) if not np.isnan(keltner.keltner_channel_lband().iloc[-1]) else current_price * 0.98
            
            # Squeeze Detection (BB inside Keltner)
            if result.bollinger_upper < result.keltner_upper and result.bollinger_lower > result.keltner_lower:
                result.squeeze_detected = True
            
            # Volatility Regime
            returns = close.pct_change().dropna()
            if len(returns) >= 20:
                current_vol = returns.tail(20).std() * np.sqrt(252)
                historical_vol = returns.std() * np.sqrt(252)
                vol_ratio = safe_divide(current_vol, historical_vol, 1.0)
                
                if vol_ratio > 1.5:
                    result.volatility_regime = "EXTREME"
                elif vol_ratio > 1.2:
                    result.volatility_regime = "HIGH"
                elif vol_ratio < 0.5:
                    result.volatility_regime = "EXTREMELY_LOW"
                elif vol_ratio < 0.7:
                    result.volatility_regime = "LOW"
                else:
                    result.volatility_regime = "NORMAL"
            
        except Exception as e:
            logger.debug(f"Volatility calculation error: {e}")
        
        return result
    
    def _calculate_volume(self, close: pd.Series, high: pd.Series, low: pd.Series,
                         volume: pd.Series, current_price: float, 
                         result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate volume indicators"""
        try:
            # Volume SMA and Ratio
            if len(volume) >= 20:
                result.volume_sma = float(volume.rolling(20).mean().iloc[-1])
                current_vol = float(volume.iloc[-1])
                result.volume_ratio = safe_divide(current_vol, result.volume_sma, 1.0)
                result.volume_confirmation = result.volume_ratio > 1.2
            
            # OBV
            obv = OnBalanceVolumeIndicator(close, volume)
            obv_series = obv.on_balance_volume()
            if len(obv_series) >= 5:
                obv_current = float(obv_series.iloc[-1])
                obv_prev = float(obv_series.iloc[-5])
                if obv_current > obv_prev * 1.02:
                    result.obv_trend = "BULLISH"
                elif obv_current < obv_prev * 0.98:
                    result.obv_trend = "BEARISH"
                else:
                    result.obv_trend = "NEUTRAL"
            
            # MFI
            mfi = MFIIndicator(high, low, close, volume)
            result.mfi = float(mfi.money_flow_index().iloc[-1]) if not np.isnan(mfi.money_flow_index().iloc[-1]) else 50.0
            
            # VWAP
            vwap = VolumeWeightedAveragePrice(
                high, low, close, volume, window=Config.VWAP_WINDOW
            )
            result.vwap = float(vwap.volume_weighted_average_price().iloc[-1]) if not np.isnan(vwap.volume_weighted_average_price().iloc[-1]) else current_price
            result.vwap_distance = safe_divide(current_price - result.vwap, result.vwap, 0) * 100
            
        except Exception as e:
            logger.debug(f"Volume calculation error: {e}")
        
        return result
    
    def _calculate_ichimoku(self, close: pd.Series, high: pd.Series, low: pd.Series,
                           current_price: float, result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate Ichimoku Cloud indicators"""
        try:
            ichimoku = IchimokuIndicator(high, low, window1=9, window2=26, window3=52)
            
            result.tenkan_sen = float(ichimoku.ichimoku_conversion_line().iloc[-1]) if not np.isnan(ichimoku.ichimoku_conversion_line().iloc[-1]) else current_price
            result.kijun_sen = float(ichimoku.ichimoku_base_line().iloc[-1]) if not np.isnan(ichimoku.ichimoku_base_line().iloc[-1]) else current_price
            result.senkou_span_a = float(ichimoku.ichimoku_a().iloc[-1]) if not np.isnan(ichimoku.ichimoku_a().iloc[-1]) else current_price
            result.senkou_span_b = float(ichimoku.ichimoku_b().iloc[-1]) if not np.isnan(ichimoku.ichimoku_b().iloc[-1]) else current_price
            
            # Cloud analysis
            cloud_top = max(result.senkou_span_a, result.senkou_span_b)
            cloud_bottom = min(result.senkou_span_a, result.senkou_span_b)
            
            result.price_above_cloud = current_price > cloud_top
            
            # Ichimoku Signal
            if current_price > cloud_top and result.tenkan_sen > result.kijun_sen:
                result.ichimoku_signal = "BULLISH"
            elif current_price < cloud_bottom and result.tenkan_sen < result.kijun_sen:
                result.ichimoku_signal = "BEARISH"
            else:
                result.ichimoku_signal = "NEUTRAL"
            
        except Exception as e:
            logger.debug(f"Ichimoku calculation error: {e}")
        
        return result
    
    def _calculate_support_resistance(self, data: pd.DataFrame, current_price: float,
                                      result: TechnicalIndicators) -> TechnicalIndicators:
        """Calculate support and resistance levels"""
        try:
            high = data['High']
            low = data['Low']
            close = data['Close']
            
            # Pivot Points (Standard)
            if len(data) >= 2:
                prev_high = float(high.iloc[-2])
                prev_low = float(low.iloc[-2])
                prev_close = float(close.iloc[-2])
                
                result.pivot_point = (prev_high + prev_low + prev_close) / 3
                result.r1 = 2 * result.pivot_point - prev_low
                result.r2 = result.pivot_point + (prev_high - prev_low)
                result.s1 = 2 * result.pivot_point - prev_high
                result.s2 = result.pivot_point - (prev_high - prev_low)
            
            # Dynamic Support/Resistance from recent price action
            recent_data = data.tail(60)
            
            # Find swing highs and lows
            swing_highs = []
            swing_lows = []
            
            for i in range(5, len(recent_data) - 5):
                # Swing High
                if all(recent_data['High'].iloc[i] >= recent_data['High'].iloc[i-j] for j in range(1, 4)) and \
                   all(recent_data['High'].iloc[i] >= recent_data['High'].iloc[i+j] for j in range(1, 4)):
                    swing_highs.append(float(recent_data['High'].iloc[i]))
                
                # Swing Low
                if all(recent_data['Low'].iloc[i] <= recent_data['Low'].iloc[i-j] for j in range(1, 4)) and \
                   all(recent_data['Low'].iloc[i] <= recent_data['Low'].iloc[i+j] for j in range(1, 4)):
                    swing_lows.append(float(recent_data['Low'].iloc[i]))
            
            # Find nearest support and resistance
            resistance_levels = [h for h in swing_highs if h > current_price]
            support_levels = [l for l in swing_lows if l < current_price]
            
            # No swing level on one side (price at a 60-day high, or at a low)
            # is reported as unavailable (0.0). A default of spot x1.03 / x0.97
            # used to be published as "resistance"/"support" and then fed the
            # 12-month target blend and the stop as if it were observed.
            result.resistance_level = min(resistance_levels) if resistance_levels else 0.0
            result.support_level = max(support_levels) if support_levels else 0.0
            
        except Exception as e:
            logger.debug(f"Support/Resistance calculation error: {e}")
            result.support_level = 0.0
            result.resistance_level = 0.0
        
        return result
    
    def _analyze_market_structure(self, close: pd.Series, high: pd.Series, low: pd.Series,
                                  result: TechnicalIndicators) -> TechnicalIndicators:
        """Analyze market structure (HH, HL, LH, LL)"""
        try:
            if len(close) < 30:
                return result
            
            # Analyze recent 30 bars
            recent_high = high.tail(30).values
            recent_low = low.tail(30).values
            
            # Find swing points
            swing_highs = []
            swing_lows = []
            
            for i in range(2, len(recent_high) - 2):
                if recent_high[i] > recent_high[i-1] and recent_high[i] > recent_high[i+1]:
                    swing_highs.append((i, recent_high[i]))
                if recent_low[i] < recent_low[i-1] and recent_low[i] < recent_low[i+1]:
                    swing_lows.append((i, recent_low[i]))
            
            # Count HH, HL, LH, LL
            if len(swing_highs) >= 2:
                for i in range(1, len(swing_highs)):
                    if swing_highs[i][1] > swing_highs[i-1][1]:
                        result.higher_highs += 1
                    else:
                        result.lower_highs += 1
            
            if len(swing_lows) >= 2:
                for i in range(1, len(swing_lows)):
                    if swing_lows[i][1] > swing_lows[i-1][1]:
                        result.higher_lows += 1
                    else:
                        result.lower_lows += 1
            
            # Determine market structure
            if result.higher_highs >= 2 and result.higher_lows >= 2:
                result.market_structure = "UPTREND"
            elif result.lower_highs >= 2 and result.lower_lows >= 2:
                result.market_structure = "DOWNTREND"
            else:
                result.market_structure = "RANGING"
            
        except Exception as e:
            logger.debug(f"Market structure analysis error: {e}")
        
        return result
    
    def _calculate_52week_range(self, close: pd.Series, current_price: float,
                                result: TechnicalIndicators,
                                high: Optional[pd.Series] = None,
                                low: Optional[pd.Series] = None) -> TechnicalIndicators:
        """52-week high/low and position, from daily highs and lows.

        A 52-week high is the highest traded price, not the highest close:
        closes put AAPL's high at today's close, $4 under the session high.
        """
        try:
            hi = high if high is not None and len(high) == len(close) else close
            lo = low if low is not None and len(low) == len(close) else close
            window = 252 if len(close) >= 252 else len(close)
            result.week_52_high = float(hi.tail(window).max())
            result.week_52_low = float(lo.tail(window).min())
            
            range_52w = result.week_52_high - result.week_52_low
            if range_52w > 0:
                result.distance_from_high = safe_divide(result.week_52_high - current_price, range_52w, 0.5) * 100
                result.distance_from_low = safe_divide(current_price - result.week_52_low, range_52w, 0.5) * 100
            
        except Exception as e:
            logger.debug(f"52-week range calculation error: {e}")
        
        return result

    # -------------------------------------------------------------------------
    # CANDLESTICK PATTERN DETECTION
    # -------------------------------------------------------------------------
    def _detect_candlestick_patterns(self, data: pd.DataFrame,
                                     result: TechnicalIndicators) -> TechnicalIndicators:
        """Detect classical candlestick patterns on the most recent bars.

        Patterns are bias-weighted and reliability-weighted into a 0-100 score
        where 50 is neutral. Reversal patterns only count when the *prior trend*
        is opposite to the pattern's bias (a Hammer mid-uptrend is meaningless).
        Continuation patterns require an aligned prior trend.
        """
        try:
            if data is None or len(data) < 5:
                return result

            o = data['Open'].to_numpy(dtype=float)
            h = data['High'].to_numpy(dtype=float)
            l = data['Low'].to_numpy(dtype=float)
            c = data['Close'].to_numpy(dtype=float)
            n = len(c)

            # --- Prior trend (look back 10 bars BEFORE the most recent candle) ---
            lookback = min(10, n - 1)
            prior_close = c[-lookback - 1:-1] if lookback > 0 else c
            if len(prior_close) >= 3:
                slope = (prior_close[-1] - prior_close[0]) / max(abs(prior_close[0]), 1e-9)
                if slope > 0.02:
                    prior_trend = "UP"
                elif slope < -0.02:
                    prior_trend = "DOWN"
                else:
                    prior_trend = "SIDEWAYS"
            else:
                prior_trend = "SIDEWAYS"
            result.prior_trend = prior_trend

            # --- Helpers ---
            def body(i): return abs(c[i] - o[i])
            def rng(i):  return max(h[i] - l[i], 1e-9)
            def upper(i): return h[i] - max(o[i], c[i])
            def lower(i): return min(o[i], c[i]) - l[i]
            def bull(i): return c[i] > o[i]
            def bear(i): return c[i] < o[i]
            def body_pct(i): return body(i) / rng(i)
            # Average body of last N bars (excluding current) for size comparisons.
            # `n - 1` prior bars are available; cap at 10.
            avg_body_window = max(1, min(10, n - 1))
            avg_body = float(np.mean([body(idx) for idx in range(-avg_body_window - 1, -1)])) or 1e-9

            patterns: List[Dict] = []

            i = n - 1   # last bar index (use positive index for clarity)
            j = n - 2   # previous bar
            k = n - 3   # two bars back

            # ---------------- Single-candle patterns ----------------
            bp = body_pct(i)
            uw, lw, bd = upper(i), lower(i), body(i)

            # Doji family (very small body)
            if bp < 0.1:
                if lw > 2 * (uw + bd) and uw < bd * 0.5:
                    patterns.append({"name": "Dragonfly Doji", "bias": "BULLISH",
                                     "strength": 2, "reliability": "MEDIUM",
                                     "type": "REVERSAL",
                                     "context": "Strong reversal signal if after downtrend"})
                elif uw > 2 * (lw + bd) and lw < bd * 0.5:
                    patterns.append({"name": "Gravestone Doji", "bias": "BEARISH",
                                     "strength": 2, "reliability": "MEDIUM",
                                     "type": "REVERSAL",
                                     "context": "Strong reversal signal if after uptrend"})
                else:
                    patterns.append({"name": "Doji", "bias": "NEUTRAL",
                                     "strength": 1, "reliability": "LOW",
                                     "type": "INDECISION",
                                     "context": "Market indecision - watch next bar"})

            # Marubozu (no wicks, large body)
            if bp > 0.95 and bd > avg_body * 1.2:
                if bull(i):
                    patterns.append({"name": "Bullish Marubozu", "bias": "BULLISH",
                                     "strength": 3, "reliability": "HIGH",
                                     "type": "CONTINUATION",
                                     "context": "Strong buying pressure, no rejection"})
                elif bear(i):
                    patterns.append({"name": "Bearish Marubozu", "bias": "BEARISH",
                                     "strength": 3, "reliability": "HIGH",
                                     "type": "CONTINUATION",
                                     "context": "Strong selling pressure, no relief"})

            # Hammer / Hanging Man (small body at top, long lower wick)
            if bp < 0.35 and lw > 2 * bd and uw < bd:
                if prior_trend == "DOWN":
                    patterns.append({"name": "Hammer", "bias": "BULLISH",
                                     "strength": 3, "reliability": "HIGH",
                                     "type": "REVERSAL",
                                     "context": "Bullish reversal after downtrend"})
                elif prior_trend == "UP":
                    patterns.append({"name": "Hanging Man", "bias": "BEARISH",
                                     "strength": 2, "reliability": "MEDIUM",
                                     "type": "REVERSAL",
                                     "context": "Caution - distribution at top"})

            # Inverted Hammer / Shooting Star (small body at bottom, long upper wick)
            if bp < 0.35 and uw > 2 * bd and lw < bd:
                if prior_trend == "DOWN":
                    patterns.append({"name": "Inverted Hammer", "bias": "BULLISH",
                                     "strength": 2, "reliability": "MEDIUM",
                                     "type": "REVERSAL",
                                     "context": "Possible bullish reversal - needs confirmation"})
                elif prior_trend == "UP":
                    patterns.append({"name": "Shooting Star", "bias": "BEARISH",
                                     "strength": 3, "reliability": "HIGH",
                                     "type": "REVERSAL",
                                     "context": "Bearish reversal at top - sellers in control"})

            # Spinning Top (small body, comparable wicks)
            if 0.1 <= bp < 0.3 and uw > bd * 0.5 and lw > bd * 0.5 \
               and abs(uw - lw) < bd:
                patterns.append({"name": "Spinning Top", "bias": "NEUTRAL",
                                 "strength": 1, "reliability": "LOW",
                                 "type": "INDECISION",
                                 "context": "Indecision - momentum may be stalling"})

            # ---------------- Two-candle patterns ----------------
            if n >= 2:
                # Bullish Engulfing
                if bear(j) and bull(i) and o[i] <= c[j] and c[i] >= o[j] \
                   and body(i) > body(j):
                    rel = "HIGH" if prior_trend == "DOWN" else "MEDIUM"
                    patterns.append({"name": "Bullish Engulfing", "bias": "BULLISH",
                                     "strength": 3, "reliability": rel,
                                     "type": "REVERSAL",
                                     "context": "Buyers overwhelmed sellers"})
                # Bearish Engulfing
                if bull(j) and bear(i) and o[i] >= c[j] and c[i] <= o[j] \
                   and body(i) > body(j):
                    rel = "HIGH" if prior_trend == "UP" else "MEDIUM"
                    patterns.append({"name": "Bearish Engulfing", "bias": "BEARISH",
                                     "strength": 3, "reliability": rel,
                                     "type": "REVERSAL",
                                     "context": "Sellers overwhelmed buyers"})
                # Piercing Line (bullish)
                if bear(j) and bull(i) and o[i] < l[j] \
                   and c[i] > (o[j] + c[j]) / 2 and c[i] < o[j]:
                    if prior_trend == "DOWN":
                        patterns.append({"name": "Piercing Line", "bias": "BULLISH",
                                         "strength": 2, "reliability": "MEDIUM",
                                         "type": "REVERSAL",
                                         "context": "Bullish reversal - bulls reclaiming control"})
                # Dark Cloud Cover (bearish)
                if bull(j) and bear(i) and o[i] > h[j] \
                   and c[i] < (o[j] + c[j]) / 2 and c[i] > o[j]:
                    if prior_trend == "UP":
                        patterns.append({"name": "Dark Cloud Cover", "bias": "BEARISH",
                                         "strength": 2, "reliability": "MEDIUM",
                                         "type": "REVERSAL",
                                         "context": "Bearish reversal - bears taking over"})
                # Tweezer Bottom / Top
                if abs(l[i] - l[j]) / max(c[j], 1e-9) < 0.003 \
                   and bear(j) and bull(i) and prior_trend == "DOWN":
                    patterns.append({"name": "Tweezer Bottom", "bias": "BULLISH",
                                     "strength": 2, "reliability": "MEDIUM",
                                     "type": "REVERSAL",
                                     "context": "Double rejection of lows - support holding"})
                if abs(h[i] - h[j]) / max(c[j], 1e-9) < 0.003 \
                   and bull(j) and bear(i) and prior_trend == "UP":
                    patterns.append({"name": "Tweezer Top", "bias": "BEARISH",
                                     "strength": 2, "reliability": "MEDIUM",
                                     "type": "REVERSAL",
                                     "context": "Double rejection of highs - resistance holding"})
                # Bullish Harami (small body inside prior large bear body)
                if bear(j) and bull(i) and body(j) > 2 * body(i) \
                   and o[i] > c[j] and c[i] < o[j]:
                    if prior_trend == "DOWN":
                        patterns.append({"name": "Bullish Harami", "bias": "BULLISH",
                                         "strength": 2, "reliability": "MEDIUM",
                                         "type": "REVERSAL",
                                         "context": "Downtrend losing momentum"})
                # Bearish Harami
                if bull(j) and bear(i) and body(j) > 2 * body(i) \
                   and o[i] < c[j] and c[i] > o[j]:
                    if prior_trend == "UP":
                        patterns.append({"name": "Bearish Harami", "bias": "BEARISH",
                                         "strength": 2, "reliability": "MEDIUM",
                                         "type": "REVERSAL",
                                         "context": "Uptrend losing momentum"})

            # ---------------- Three-candle patterns ----------------
            if n >= 3:
                # Morning Star (bullish reversal)
                if bear(k) and body(k) > avg_body * 0.8 \
                   and body(j) < body(k) * 0.4 \
                   and bull(i) and c[i] > (o[k] + c[k]) / 2:
                    rel = "HIGH" if prior_trend == "DOWN" else "MEDIUM"
                    patterns.append({"name": "Morning Star", "bias": "BULLISH",
                                     "strength": 3, "reliability": rel,
                                     "type": "REVERSAL",
                                     "context": "Strong 3-bar bullish reversal"})
                # Evening Star (bearish reversal)
                if bull(k) and body(k) > avg_body * 0.8 \
                   and body(j) < body(k) * 0.4 \
                   and bear(i) and c[i] < (o[k] + c[k]) / 2:
                    rel = "HIGH" if prior_trend == "UP" else "MEDIUM"
                    patterns.append({"name": "Evening Star", "bias": "BEARISH",
                                     "strength": 3, "reliability": rel,
                                     "type": "REVERSAL",
                                     "context": "Strong 3-bar bearish reversal"})
                # Three White Soldiers
                if bull(k) and bull(j) and bull(i) \
                   and c[i] > c[j] > c[k] \
                   and o[j] > o[k] and o[i] > o[j] \
                   and o[j] < c[k] and o[i] < c[j] \
                   and body(k) > avg_body * 0.6:
                    patterns.append({"name": "Three White Soldiers", "bias": "BULLISH",
                                     "strength": 3, "reliability": "HIGH",
                                     "type": "CONTINUATION",
                                     "context": "Sustained bullish momentum"})
                # Three Black Crows
                if bear(k) and bear(j) and bear(i) \
                   and c[i] < c[j] < c[k] \
                   and o[j] < o[k] and o[i] < o[j] \
                   and o[j] > c[k] and o[i] > c[j] \
                   and body(k) > avg_body * 0.6:
                    patterns.append({"name": "Three Black Crows", "bias": "BEARISH",
                                     "strength": 3, "reliability": "HIGH",
                                     "type": "CONTINUATION",
                                     "context": "Sustained bearish momentum"})
                # Three Inside Up (Harami confirmation)
                if bear(k) and bull(j) and bull(i) \
                   and o[j] > c[k] and c[j] < o[k] \
                   and c[i] > c[j] and c[i] > o[k]:
                    if prior_trend == "DOWN":
                        patterns.append({"name": "Three Inside Up", "bias": "BULLISH",
                                         "strength": 2, "reliability": "MEDIUM",
                                         "type": "REVERSAL",
                                         "context": "Harami confirmed by follow-through"})
                # Three Inside Down
                if bull(k) and bear(j) and bear(i) \
                   and o[j] < c[k] and c[j] > o[k] \
                   and c[i] < c[j] and c[i] < o[k]:
                    if prior_trend == "UP":
                        patterns.append({"name": "Three Inside Down", "bias": "BEARISH",
                                         "strength": 2, "reliability": "MEDIUM",
                                         "type": "REVERSAL",
                                         "context": "Harami confirmed by follow-through"})

            # ---------------- Aggregate score & bias ----------------
            # Weight by strength * reliability_weight
            rel_w = {"HIGH": 1.0, "MEDIUM": 0.66, "LOW": 0.33}
            bull_w = sum(p["strength"] * rel_w[p["reliability"]]
                         for p in patterns if p["bias"] == "BULLISH")
            bear_w = sum(p["strength"] * rel_w[p["reliability"]]
                         for p in patterns if p["bias"] == "BEARISH")

            total = bull_w + bear_w
            if total == 0:
                score = 50.0
                bias = "NEUTRAL"
            else:
                # Map net weight to 0-100 score (cap each side at ±6 weight ~ ±30 pts)
                net = bull_w - bear_w
                score = 50.0 + max(min(net, 6.0), -6.0) * (40.0 / 6.0)
                if net > 1.0:
                    bias = "BULLISH"
                elif net < -1.0:
                    bias = "BEARISH"
                else:
                    bias = "NEUTRAL"

            # Sort patterns by strength*reliability descending for display
            patterns.sort(
                key=lambda p: p["strength"] * rel_w[p["reliability"]],
                reverse=True
            )

            result.candlestick_patterns = patterns
            result.candlestick_bias = bias
            result.candlestick_score = float(score)

            if not patterns:
                result.candlestick_summary = "No notable patterns on most recent bars"
            else:
                top = patterns[0]
                result.candlestick_summary = (
                    f"{top['name']} ({top['bias']}, {top['reliability']} reliability)"
                )

        except Exception as e:
            logger.debug(f"Candlestick pattern detection error: {e}")

        return result

    def _calculate_composite_scores(self, result: TechnicalIndicators) -> TechnicalIndicators:
        """
        Calculate composite technical scores using ORTHOGONAL indicators only.
        
        VIF FIX: RSI, Stochastic, Williams %R, and MFI are 90%+ correlated.
        Using all of them inflates signal strength without adding information.
        
        SOLUTION: Use only one momentum indicator (RSI - most researched)
        and combine with truly orthogonal signals (volume, trend).
        """
        try:
            # Momentum Score (0-100)
            # VIF FIX: Use RSI ONLY instead of averaging correlated indicators
            # Academic research supports RSI as the most predictive single oscillator
            
            # RSI is the primary momentum signal
            if result.rsi < 30:
                rsi_score = 75 + (30 - result.rsi)  # Max 80 at RSI=25
            elif result.rsi > 70:
                rsi_score = 25 - (result.rsi - 70)  # Min 20 at RSI=75
            else:
                rsi_score = 50 + (50 - result.rsi) * 0.5  # Neutral zone
            
            # MACD histogram (orthogonal to RSI - measures trend momentum, not overbought/oversold)
            macd_score = 50
            if result.macd_histogram > 0:
                macd_score = 55 + min(result.macd_histogram * 100, 20)  # Cap at 75
            elif result.macd_histogram < 0:
                macd_score = 45 + max(result.macd_histogram * 100, -20)  # Floor at 25
            
            # Weighted average: RSI 70%, MACD momentum 30%
            # (MACD is less correlated with RSI than Stoch/Williams)
            result.momentum_score = rsi_score * 0.70 + macd_score * 0.30
            
            # Trend Score (0-100)
            trend_factors = []
            
            # MACD contribution
            if result.macd_signal == "BULLISH":
                trend_factors.append(70)
            elif result.macd_signal == "BEARISH":
                trend_factors.append(30)
            else:
                trend_factors.append(50)
            
            # ADX + Direction contribution
            if result.trend_direction == "UP" and result.adx > 25:
                trend_factors.append(70 + min(result.adx - 25, 20))
            elif result.trend_direction == "DOWN" and result.adx > 25:
                trend_factors.append(30 - min(result.adx - 25, 20))
            else:
                trend_factors.append(50)
            
            # MA alignment contribution
            if result.sma_20 > result.sma_50 > result.sma_200:
                trend_factors.append(80)
            elif result.sma_20 < result.sma_50 < result.sma_200:
                trend_factors.append(20)
            else:
                trend_factors.append(50)
            
            result.trend_score = sum(trend_factors) / len(trend_factors)

            # Candlestick adjustment: blend ±5 pts max into trend_score
            # (small weight — patterns confirm rather than override macro trend)
            cs = result.candlestick_score
            adj = (cs - 50.0) * 0.10   # ±5 pts when score is at extremes
            result.trend_score = max(0.0, min(100.0, result.trend_score + adj))

            # Volatility Score (0-100, higher = better for trading)
            if result.volatility_regime == "EXTREME":
                result.volatility_score = 30  # High risk
            elif result.volatility_regime == "HIGH":
                result.volatility_score = 45
            elif result.volatility_regime == "NORMAL":
                result.volatility_score = 60
            elif result.volatility_regime == "LOW":
                result.volatility_score = 70
            else:
                result.volatility_score = 50
            
            # Breakout probability
            breakout_factors = 0
            if result.squeeze_detected:
                breakout_factors += 0.3
            if result.volume_confirmation:
                breakout_factors += 0.2
            if result.adx > 20 and result.adx < 30:
                breakout_factors += 0.2
            if abs(result.bollinger_width) < 3:  # Tight BB
                breakout_factors += 0.15
            
            result.breakout_probability = min(0.9, 0.3 + breakout_factors)
            
        except Exception as e:
            logger.debug(f"Composite score calculation error: {e}")
        
        return result




# =============================================================================
# FUNDAMENTAL ANALYSIS ENGINE
# =============================================================================

class FundamentalAnalyzer:
    """Comprehensive fundamental analysis with quality scoring"""

    @staticmethod
    def _statement_value(statement: Any, row_names: List[str]) -> float:
        """Read the newest finite value for the first matching statement row."""
        if statement is None or not isinstance(statement, pd.DataFrame) or statement.empty:
            return 0.0
        for name in row_names:
            if name not in statement.index:
                continue
            values = pd.to_numeric(statement.loc[name], errors="coerce").dropna()
            if not values.empty and math.isfinite(float(values.iloc[0])):
                return float(values.iloc[0])
        return 0.0
    
    def _analyze_etf(self, info: Dict, result: FundamentalData) -> FundamentalData:
        """Perform ETF-specific fundamental analysis"""
        try:
            # Valuation
            result.pe_ratio = float(info.get('trailingPE', 0.0) or 0.0)
            # [DECISION-CORE] normalise ETF yield consistently
            if HAS_DECISION_CORE:
                result.dividend_yield = dividend_yield_from_info(info)
            else:
                raw = float(info.get('yield', 0.0) or info.get('trailingAnnualDividendYield', 0.0) or 0.0)
                if raw <= 0:
                    result.dividend_yield = 0.0
                elif raw <= 1.0:
                    result.dividend_yield = raw
                elif raw <= 100.0:
                    result.dividend_yield = raw / 100.0
                else:
                    result.dividend_yield = 0.0
            
            # Market Data
            result.market_cap = float(info.get('totalAssets', 0.0) or info.get('marketCap', 0.0) or 0.0)
            
            # ETF Specifics
            # Try multiple keys for expense ratio
            result.expense_ratio = float(info.get('annualReportExpenseRatio', 0.0) or 
                                       info.get('expenseRatio', 0.0) or 0.0)
            result.net_assets = result.market_cap # Alias for convenience
            
            # Set default/neutral scores for metrics that don't apply to ETFs
            # Not measured for a fund: neutral, and excluded from the composite
            # (SignalGenerator). A constant 70 used to lift every ETF's score.
            result.quality_score = 50.0
            result.growth_score = 50.0   # Neutral growth assumption
            
            # Simple Valuation Score logic for ETFs based on PE
            if result.pe_ratio > 0:
                if result.pe_ratio > 35:
                    result.valuation_score = 30.0
                elif result.pe_ratio > 25:
                    result.valuation_score = 45.0
                elif result.pe_ratio < 15:
                    result.valuation_score = 70.0
                else:
                    result.valuation_score = 60.0 # 15-25 is healthy
            else:
                 result.valuation_score = 50.0

            return result
        except Exception as e:
            logger.error(f"ETF analysis error: {e}")
            return result

    def analyze(self, ticker: str) -> FundamentalData:
        """Perform comprehensive fundamental analysis"""
        result = FundamentalData()
        
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            
            if not info or len(info) < 5:
                logger.warning(f"Insufficient fundamental data for {ticker}")
                return result
            
            # Check if ETF
            quote_type = info.get('quoteType', '').upper()
            if quote_type == 'ETF':
                result.is_etf = True
                logger.info(f"{ticker} identified as ETF")
                return self._analyze_etf(info, result)
            
            result.reported = {
                'roe': _reported(info, 'returnOnEquity'),
                'net_margin': _reported(info, 'profitMargins'),
                'revenue_growth': _reported(info, 'revenueGrowth'),
                'debt_to_equity': _reported(info, 'debtToEquity'),
                'current_ratio': _reported(info, 'currentRatio'),
            }

            result.consensus = {
                'target_mean': safe_get(info, 'targetMeanPrice', 0),
                'target_high': safe_get(info, 'targetHighPrice', 0),
                'target_low': safe_get(info, 'targetLowPrice', 0),
                'target_median': safe_get(info, 'targetMedianPrice', 0),
                'analyst_count': safe_get(info, 'numberOfAnalystOpinions', 0),
            }

            # Valuation Ratios
            result = self._calculate_valuation(info, result)
            
            # Profitability
            result = self._calculate_profitability(info, result)
            
            # Growth
            result = self._calculate_growth(info, stock, result)
            
            # Financial Health
            result = self._calculate_financial_health(info, result)
            
            # Cash Flow
            result = self._calculate_cash_flow(info, stock, result)
            
            # Per Share Data
            result = self._calculate_per_share(info, result)
            
            # Dividends
            result = self._calculate_dividends(info, stock, result)
            
            # Quality Scores
            result = self._calculate_quality_scores(info, stock, result)
            
        except Exception as e:
            logger.error(f"Fundamental analysis error for {ticker}: {e}")
        
        return result
    
    def _calculate_valuation(self, info: Dict, result: FundamentalData) -> FundamentalData:
        """Calculate valuation ratios"""
        result.pe_ratio = safe_get(info, 'trailingPE', 0)
        result.forward_pe = safe_get(info, 'forwardPE', 0)
        result.pb_ratio = safe_get(info, 'priceToBook', 0)
        result.ps_ratio = safe_get(info, 'priceToSalesTrailing12Months', 0)
        result.ev_ebitda = safe_get(info, 'enterpriseToEbitda', 0)
        result.ev_revenue = safe_get(info, 'enterpriseToRevenue', 0)
        
        # PEG Ratio
        eps_growth = safe_get(info, 'earningsGrowth', 0) * 100
        if result.pe_ratio > 0 and eps_growth > 0:
            result.pe_to_growth = result.pe_ratio / eps_growth
        
        return result
    
    def _calculate_profitability(self, info: Dict, result: FundamentalData) -> FundamentalData:
        """Calculate profitability metrics"""
        result.gross_margin = safe_get(info, 'grossMargins', 0, 100)
        result.operating_margin = safe_get(info, 'operatingMargins', 0, 100)
        result.net_margin = safe_get(info, 'profitMargins', 0, 100)
        result.roe = safe_get(info, 'returnOnEquity', 0, 100)
        result.roa = safe_get(info, 'returnOnAssets', 0, 100)
        
        # ROIC estimation (if not available directly)
        if result.roe > 0 and result.roa > 0:
            result.roic = (result.roe + result.roa) / 2
        
        return result
    
    def _calculate_growth(self, info: Dict, stock, result: FundamentalData) -> FundamentalData:
        """Calculate growth metrics"""
        result.revenue_growth_yoy = safe_get(info, 'revenueGrowth', 0, 100)
        result.eps_growth_yoy = safe_get(info, 'earningsGrowth', 0, 100)
        
        # Try to calculate CAGR from historical data
        try:
            financials = stock.quarterly_financials
            if financials is not None and not financials.empty:
                if 'Total Revenue' in financials.index:
                    revenue = financials.loc['Total Revenue']
                    if len(revenue) >= 8:
                        recent = revenue.iloc[0]
                        old = revenue.iloc[7]
                        if old > 0 and recent > 0:
                            # 8 quarters ≈ 2 years; annualize correctly
                            years = 2.0
                            result.revenue_growth_3y_cagr = ((recent / old) ** (1 / years) - 1) * 100
        except Exception:
            pass
        
        return result
    
    def _calculate_financial_health(self, info: Dict, result: FundamentalData) -> FundamentalData:
        """Calculate financial health metrics"""
        result.current_ratio = safe_get(info, 'currentRatio', 0)
        result.quick_ratio = safe_get(info, 'quickRatio', 0)
        
        # Debt to Equity - Yahoo returns as percentage (e.g., 85.4 means 0.854x)
        d_e = safe_get(info, 'debtToEquity', 0)
        if d_e < 0 or d_e > 5000:
            # Negative equity (buyback-funded or distressed) has no meaningful
            # D/E, and >50x is a provider artefact. Neither is "no debt": the
            # figure is marked unreported so no score, veto or vote reads it.
            result.debt_to_equity = 0.0
            if isinstance(result.reported, dict):
                result.reported['debt_to_equity'] = False
        else:
            result.debt_to_equity = min(10.0, d_e / 100.0)  # Always convert from %
        
        # Interest coverage. Signed: an operating loss is NOT covered interest.
        ebit = safe_get(info, 'ebitda', 0) - safe_get(info, 'depreciation', 0)
        interest = abs(safe_get(info, 'interestExpense', 0))
        if interest > 0 and _reported(info, 'ebitda'):
            result.interest_coverage = ebit / interest
        
        return result
    
    def _calculate_cash_flow(self, info: Dict, stock, result: FundamentalData) -> FundamentalData:
        """Calculate cash flow metrics"""
        result.operating_cash_flow = safe_get(info, 'operatingCashflow', 0)
        result.free_cash_flow = safe_get(info, 'freeCashflow', 0)
        
        # FCF Yield
        market_cap = safe_get(info, 'marketCap', 1)
        if market_cap > 0 and result.free_cash_flow != 0:
            result.fcf_yield = (result.free_cash_flow / market_cap) * 100
        
        # FCF per Share
        shares = safe_get(info, 'sharesOutstanding', 1)
        if shares > 0:
            result.fcf_per_share = result.free_cash_flow / shares
        
        return result
    
    def _calculate_per_share(self, info: Dict, result: FundamentalData) -> FundamentalData:
        """Calculate per share metrics"""
        result.eps_ttm = safe_get(info, 'trailingEps', 0)
        result.eps_forward = safe_get(info, 'forwardEps', 0)
        result.book_value_per_share = safe_get(info, 'bookValue', 0)
        
        # Revenue per share
        revenue = safe_get(info, 'totalRevenue', 0)
        shares = safe_get(info, 'sharesOutstanding', 1)
        if shares > 0:
            result.revenue_per_share = revenue / shares
        
        return result
    
    def _calculate_dividends(self, info: Dict, stock, result: FundamentalData) -> FundamentalData:
        """Calculate dividend metrics.

        Uses the shared `dividend_yield_from_info` so we cannot mis-display
        yfinance's inconsistent dividendYield field. Stored as FRACTION
        (e.g. 0.0088 = 0.88%). Multiply by 100 only at display time.
        """
        if HAS_DECISION_CORE:
            # Units come from the field, not the magnitude (shared with the scanner).
            result.dividend_yield = dividend_yield_from_info(info)
        else:
            raw = info.get('dividendYield') or 0
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                raw = 0.0
            # yfinance returns inconsistently — normalise to a fraction
            if raw <= 0:
                result.dividend_yield = 0.0
            elif raw <= 1.0:
                result.dividend_yield = raw
            elif raw <= 100.0:
                result.dividend_yield = raw / 100.0
            else:
                result.dividend_yield = 0.0
        result.dividend_payout_ratio = safe_get(info, 'payoutRatio', 0, 100)
        
        # Dividend coverage
        if result.dividend_payout_ratio > 0:
            result.dividend_coverage = 100 / result.dividend_payout_ratio
        
        # Dividend growth
        try:
            dividends = stock.dividends
            if len(dividends) >= 20:
                annual_divs = dividends.groupby(dividends.index.year).sum()
                if len(annual_divs) >= 5:
                    recent = annual_divs.iloc[-1]
                    old = annual_divs.iloc[-5]
                    if old > 0:
                        result.dividend_growth_5y = ((recent / old) ** 0.2 - 1) * 100
        except Exception:
            pass
        
        return result
    
    @staticmethod
    def _independent_quality_score(info: Dict, result: FundamentalData) -> float:
        """Quality (0-100) from measures the fundamental leg does NOT score.

        ROE, net margin, revenue growth and debt/equity are already scored in
        the fundamental component (and used to be scored again here, then a
        third time as signal "confirmations"). Quality now rests on accounting
        and solvency evidence only:

        * Piotroski F-score, as a share of the tests the provider reported;
        * Altman Z (industrials only; not applicable to financials);
        * cash conversion: operating cash flow relative to net income.

        Each leg counts only when measured; with none measured the score is
        the neutral midpoint, because absence of data is not evidence.
        """
        legs: List[float] = []
        available = int(getattr(result, 'piotroski_components_available', 0) or 0)
        if available >= PIOTROSKI_MIN_INPUTS:
            legs.append(100.0 * result.piotroski_f_score / available)
        if getattr(result, 'altman_z_status', '') == 'ok':
            z = float(result.altman_z_score)
            legs.append(90.0 if z >= 3.0 else (55.0 if z >= 1.8 else 15.0))
        net_income = safe_get(info, 'netIncomeToCommon', safe_get(info, 'netIncome', 0))
        if _reported(info, 'operatingCashflow') and net_income > 0:
            conversion = float(result.operating_cash_flow) / net_income
            legs.append(85.0 if conversion >= 1.0 else (55.0 if conversion >= 0.5 else 25.0))
        if not legs:
            return 50.0
        return float(sum(legs) / len(legs))

    def _calculate_quality_scores(self, info: Dict, stock, result: FundamentalData) -> FundamentalData:
        """Calculate quality scores (Piotroski, Altman Z, etc.)"""
        
        # Piotroski F-Score (0-9). Each test only counts when its input was
        # actually reported: a missing field used to score as a silent FAIL
        # (0.0 < threshold), so a ticker with no fundamentals read "2/9" —
        # a fabricated weak score rather than an honest N/A.
        f_score, available = _piotroski_from_fundamentals(result, info)
        result.piotroski_components_available = available
        result.piotroski_f_score = min(9, f_score)
        
        # Altman Z is not meaningful for banks/insurers because their balance
        # sheets and leverage are structurally different from industrial firms.
        sector = str(info.get('sector') or '').lower()
        if any(token in sector for token in ('financial', 'bank', 'insurance')):
            result.altman_z_status = 'not_applicable'
        else:
            try:
                balance_sheet = getattr(stock, 'balance_sheet', None)
                financials = getattr(stock, 'financials', None)
                total_assets = self._statement_value(balance_sheet, ['Total Assets'])
                current_assets = self._statement_value(
                    balance_sheet, ['Current Assets', 'Total Current Assets'])
                current_liabilities = self._statement_value(
                    balance_sheet, ['Current Liabilities', 'Total Current Liabilities'])
                retained_earnings = self._statement_value(balance_sheet, ['Retained Earnings'])
                equity = self._statement_value(
                    balance_sheet, ['Stockholders Equity', 'Total Stockholder Equity'])
                liabilities = self._statement_value(
                    balance_sheet,
                    ['Total Liabilities Net Minority Interest', 'Total Liabilities'],
                )
                ebit = self._statement_value(financials, ['EBIT', 'Operating Income'])
                revenue = self._statement_value(financials, ['Total Revenue', 'Operating Revenue'])

                # Fall back to explicit metadata only; never manufacture a
                # denominator or use market cap for book equity.
                total_assets = total_assets or safe_get(info, 'totalAssets', 0)
                current_assets = current_assets or safe_get(info, 'totalCurrentAssets', 0)
                current_liabilities = current_liabilities or safe_get(info, 'totalCurrentLiabilities', 0)
                retained_earnings = retained_earnings or safe_get(info, 'retainedEarnings', 0)
                equity = equity or safe_get(info, 'totalStockholderEquity', 0)
                liabilities = liabilities or safe_get(info, 'totalLiab', 0)
                ebit = ebit or safe_get(info, 'ebit', 0)
                revenue = revenue or safe_get(info, 'totalRevenue', 0)

                market_cap_for_z = safe_get(info, 'marketCap', 0)
                z_score, variant = compute_altman_z(
                    total_assets=total_assets, current_assets=current_assets,
                    current_liabilities=current_liabilities,
                    retained_earnings=retained_earnings, ebit=ebit,
                    revenue=revenue, liabilities=liabilities,
                    book_equity=equity, market_cap=market_cap_for_z,
                )
                if z_score is not None:
                    result.altman_z_score = float(z_score)
                    result.altman_z_status = 'ok'
                    result.altman_z_variant = variant
                else:
                    result.altman_z_status = 'unavailable'
            except Exception as exc:
                logger.debug("Altman Z unavailable: %s", exc)
                result.altman_z_status = 'unavailable'

        # Store market fields independently of Altman availability.
        result.market_cap = safe_get(info, 'marketCap', 0)
        result.shares_outstanding = safe_get(info, 'sharesOutstanding', 0)
        
        result.quality_score = self._independent_quality_score(info, result)
        
        return result


# =============================================================================
# RISK ANALYSIS ENGINE  
# =============================================================================

class RiskAnalyzer:
    """Comprehensive risk analysis with VaR, CVaR, and factor analysis"""

    def __init__(self):
        self._spy_returns_cache: Optional[pd.Series] = None
        self._spy_returns_cache_ts: float = 0.0
        self._benchmark_returns_cache: Dict[str, Tuple[pd.Series, float]] = {}

    @staticmethod
    def _sanitize_returns(returns: pd.Series, ticker: str = "") -> pd.Series:
        """Drop non-economic one-day jumps typical of unadjusted splits.

        Adjusted prices are still required at ingestion. This defensive gate
        keeps a provider/default regression from contaminating volatility,
        beta, drawdown, VaR, and position sizing.
        """
        clean = pd.to_numeric(returns, errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        threshold = Config.SPLIT_ARTIFACT_RETURN_THRESHOLD
        mask = clean.abs() >= threshold
        if mask.any():
            logger.warning(
                "%s: removed %d probable split/corporate-action return(s) >= %.0f%%",
                ticker or "series", int(mask.sum()), threshold * 100,
            )
            clean = clean.loc[~mask]
        return clean

    def _get_spy_returns(self) -> pd.Series:
        """Get cached SPY returns to avoid repeated network calls."""
        now = time.time()
        cache_ttl = 300  # 5 minutes

        if self._spy_returns_cache is not None and (now - self._spy_returns_cache_ts) < cache_ttl:
            return self._spy_returns_cache

        spy_returns = yf.Ticker("SPY").history(
            period="1y", auto_adjust=True
        )['Close'].pct_change().dropna()
        spy_returns = self._sanitize_returns(spy_returns, "SPY")
        self._spy_returns_cache = spy_returns
        self._spy_returns_cache_ts = now
        return spy_returns

    @staticmethod
    def _benchmark_symbol(ticker: str) -> str:
        upper = str(ticker or "").upper()
        if upper.endswith((".NS", ".BO")):
            return "^NSEI"
        return "SPY"

    def _get_benchmark_returns(self, ticker: str) -> pd.Series:
        """Return the local-market benchmark used for beta estimation."""
        symbol = self._benchmark_symbol(ticker)
        if symbol == "SPY":
            return self._get_spy_returns()

        now = time.time()
        cached = self._benchmark_returns_cache.get(symbol)
        if cached is not None and now - cached[1] < 300:
            return cached[0]
        history = yf.Ticker(symbol).history(period="1y", auto_adjust=True)
        returns = self._sanitize_returns(history["Close"].pct_change().dropna(), symbol)
        self._benchmark_returns_cache[symbol] = (returns, now)
        return returns

    @staticmethod
    def _session_index(series: pd.Series) -> pd.Series:
        """Normalize exchange-local timestamps to their labelled session date."""
        normalized = series.copy()
        normalized.index = pd.DatetimeIndex(
            [pd.Timestamp(value).date() for value in normalized.index]
        )
        if normalized.index.has_duplicates:
            normalized = normalized.groupby(level=0).last()
        return normalized.sort_index()
    
    def analyze(self, data: pd.DataFrame, ticker: str) -> RiskMetrics:
        """Perform comprehensive risk analysis"""
        result = RiskMetrics()
        
        if data is None or len(data) < 30:
            logger.warning("Insufficient data for risk analysis")
            return result
        
        try:
            returns = self._sanitize_returns(
                data['Close'].pct_change().dropna(), ticker
            )
            
            if len(returns) < 30:
                return result
            
            # Volatility
            result = self._calculate_volatility(returns, result)
            
            # Beta and Correlation
            result = self._calculate_beta(returns, result, ticker)
            
            # Return Metrics
            result = self._calculate_return_metrics(returns, result)
            
            # Drawdown Analysis
            result = self._calculate_drawdown(data, result)
            
            # Value at Risk
            result = self._calculate_var(returns, result)
            
            # Tail Risk
            result = self._calculate_tail_risk(returns, result)
            
            # Liquidity
            result = self._calculate_liquidity(data, result)

            # Overall Risk Score: [F3] the one grade both engines measure the
            # same way (engine.risk_grade.grade_from_history).
            result = self._grade_from_history(data, ticker, result)
            
        except Exception as e:
            logger.error(f"Risk analysis error: {e}")
        
        return result
    
    def _calculate_volatility(self, returns: pd.Series, result: RiskMetrics) -> RiskMetrics:
        """Calculate volatility metrics"""
        result.daily_volatility = float(returns.std())
        result.annualized_volatility = result.daily_volatility * np.sqrt(252)
        
        # Volatility percentile
        rolling_vol = returns.rolling(20).std() * np.sqrt(252)
        current_vol = rolling_vol.iloc[-1] if len(rolling_vol) > 0 else result.annualized_volatility
        result.volatility_percentile = (rolling_vol <= current_vol).mean() * 100 if len(rolling_vol) > 20 else 50.0
        
        return result
    
    def _calculate_beta(self, returns: pd.Series, result: RiskMetrics,
                        ticker: str = "SPY") -> RiskMetrics:
        """Calculate beta against a local benchmark on normalized sessions."""
        benchmark_symbol = self._benchmark_symbol(ticker)
        result.beta_benchmark = benchmark_symbol
        result.beta_status = "unavailable"
        result.beta_observations = 0
        try:
            benchmark = self._get_benchmark_returns(ticker)
            stock_returns = self._session_index(returns)
            benchmark_returns = self._session_index(benchmark)

            # Align dates
            common_dates = stock_returns.index.intersection(benchmark_returns.index)
            result.beta_observations = len(common_dates)
            if len(common_dates) < 30:
                return result

            stock_aligned = stock_returns.loc[common_dates]
            benchmark_aligned = benchmark_returns.loc[common_dates]

            # Calculate beta
            # Same degrees of freedom for both moments (np.cov is ddof=1).
            covariance = np.cov(stock_aligned, benchmark_aligned)[0][1]
            benchmark_variance = np.var(benchmark_aligned, ddof=1)

            if benchmark_variance > 0 and np.isfinite(covariance):
                # Published as measured, so it is not clamped: a [0.1, 3.0]
                # bound printed 3.00 for a beta of 3.4 and 0.10 for a negative
                # one. Consumers that need a bound apply their own (sizing uses
                # max(1, beta); the DCF discount rate clamps to [0.8, 2.0]).
                result.beta = float(covariance / benchmark_variance)
            else:
                return result

            # Correlation
            result.correlation_spy = np.corrcoef(stock_aligned, benchmark_aligned)[0][1]
            if np.isnan(result.correlation_spy):
                return result

            # R-squared
            result.r_squared = result.correlation_spy ** 2
            result.beta_status = "measured"
            
        except Exception as e:
            logger.debug(f"Beta calculation error: {e}")
        
        return result
    
    def _calculate_return_metrics(self, returns: pd.Series, result: RiskMetrics) -> RiskMetrics:
        """Calculate risk-adjusted return metrics"""
        mean_return = returns.mean() * 252
        result.expected_return = mean_return
        
        risk_free_rate = Config.RISK_FREE_RATE
        excess_return = mean_return - risk_free_rate
        
        # Sharpe Ratio
        if result.annualized_volatility > 0:
            result.sharpe_ratio = excess_return / result.annualized_volatility
        
        # Sortino Ratio (using proper downside deviation with all observations)
        # Standard: sqrt(mean(min(R_i - T, 0)^2)) where T = risk-free rate
        target_return = risk_free_rate / 252  # Daily target return
        downside_diff = np.minimum(returns - target_return, 0)
        downside_dev = np.sqrt(np.mean(downside_diff ** 2)) * np.sqrt(252)
        if downside_dev > 0:
            result.sortino_ratio = excess_return / downside_dev
        
        # Treynor Ratio
        if result.beta_status == "measured" and result.beta > 0:
            result.treynor_ratio = excess_return / result.beta
        
        return result
    
    def _calculate_drawdown(self, data: pd.DataFrame, result: RiskMetrics) -> RiskMetrics:
        """Calculate drawdown metrics"""
        try:
            close = data['Close']
            returns = self._sanitize_returns(close.pct_change().dropna(), "drawdown")
            cumulative = (1 + returns).cumprod()
            if cumulative.empty:
                return result
            running_max = cumulative.expanding().max()
            drawdown = (cumulative - running_max) / running_max
            
            result.max_drawdown = float(drawdown.min())
            result.current_drawdown = float(drawdown.iloc[-1])
            result.avg_drawdown = float(drawdown[drawdown < 0].mean()) if len(drawdown[drawdown < 0]) > 0 else 0
            
            # Calmar Ratio
            if result.max_drawdown < 0:
                result.calmar_ratio = result.expected_return / abs(result.max_drawdown)
            
        except Exception as e:
            logger.debug(f"Drawdown calculation error: {e}")
        
        return result
    
    def _calculate_var(self, returns: pd.Series, result: RiskMetrics) -> RiskMetrics:
        """Calculate Value at Risk metrics"""
        try:
            # Historical VaR
            result.var_95_daily = float(np.percentile(returns, 5))
            result.var_99_daily = float(np.percentile(returns, 1))
            
            # CVaR (Expected Shortfall)
            var_threshold = result.var_95_daily
            tail_returns = returns[returns <= var_threshold]
            if len(tail_returns) > 0:
                result.cvar_95 = float(tail_returns.mean())
            else:
                result.cvar_95 = result.var_95_daily
            
        except Exception as e:
            logger.debug(f"VaR calculation error: {e}")
        
        return result
    
    def _calculate_tail_risk(self, returns: pd.Series, result: RiskMetrics) -> RiskMetrics:
        """Skew and kurtosis as measured; the tail SCORE from winsorized returns
        (engine.risk_grade.tail_score; bands in RISK_GRADE)."""
        try:
            from engine.risk_grade import tail_score
            result.skewness = float(returns.skew())
            result.kurtosis = float(returns.kurtosis())
            result.tail_risk_score = tail_score(returns)[0]
        except Exception as e:
            logger.debug(f"Tail risk calculation error: {e}")

        return result

    def _grade_from_history(self, data: pd.DataFrame, ticker: str, result: RiskMetrics) -> RiskMetrics:
        """[F3] The grade both engines compute from the same bars."""
        try:
            from engine.risk_grade import grade_from_history
            try:
                bench = self._get_benchmark_returns(ticker)
            except Exception:  # noqa: BLE001 - beta then reads "not measured"
                bench = None
            g = grade_from_history(data['Close'], data['Volume'] if 'Volume' in data.columns else None, bench)
            if g.get("grade"):
                result.overall_risk_score = int(g["points"])
                result.risk_grade = g["grade"]
                result.grade_drivers = g["drivers"]
                result.risk_grade_lookback_days = int(g["lookback_days"])
                result.tail_risk_score = float(g["inputs"]["tail_risk_score"])
                return result
        except Exception as e:
            logger.debug(f"Risk grade from history failed, using measured fields: {e}")
        return self._calculate_risk_score(result)
    
    def _calculate_liquidity(self, data: pd.DataFrame, result: RiskMetrics) -> RiskMetrics:
        """Calculate liquidity metrics"""
        try:
            if 'Volume' in data.columns:
                volume = data['Volume']
                result.avg_volume = float(volume.tail(20).mean())
                
                current_price = float(data['Close'].iloc[-1])
                result.avg_dollar_volume = result.avg_volume * current_price
                
                # Bid-ask spread estimation based on dollar volume
                # (bands: engine.thresholds.RISK_GRADE.LIQUIDITY_BANDS).
                result.liquidity_score, result.bid_ask_spread_est = _T.RISK_GRADE.LIQUIDITY_FLOOR_SCORE
                for _floor, _score, _spread in _T.RISK_GRADE.LIQUIDITY_BANDS:
                    if result.avg_dollar_volume > _floor:
                        result.liquidity_score, result.bid_ask_spread_est = _score, _spread
                        break
            
        except Exception as e:
            logger.debug(f"Liquidity calculation error: {e}")
        
        return result
    
    def _calculate_risk_score(self, result: RiskMetrics) -> RiskMetrics:
        """Calculate overall risk score (1-10, 10 = highest risk).

        [UNIFIED] Delegates to engine.compute_risk_grade so the scanner and
        the analyzer cannot disagree on the same ticker's grade.
        """
        if HAS_DECISION_CORE:
            try:
                from engine import RiskGradeInputs, compute_risk_grade, risk_grade_drivers
                _rgi = RiskGradeInputs(
                    annualized_volatility=result.annualized_volatility,
                    beta=(result.beta if result.beta_status == "measured" else 1.0),
                    max_drawdown=result.max_drawdown,
                    tail_risk_score=result.tail_risk_score,
                    liquidity_score=result.liquidity_score,
                )
                overall, grade = compute_risk_grade(_rgi)
                result.overall_risk_score = overall
                result.risk_grade = grade
                result.grade_drivers = risk_grade_drivers(_rgi)
                if result.beta_status != "measured":
                    for _d in result.grade_drivers:
                        if _d["factor"] == "beta":
                            _d["detail"] = "not measured; the neutral 1.0 placeholder adds no points"
                return result
            except Exception as e:
                logger.debug(f"Unified risk grade failed, falling back: {e}")

        # ---- Fallback: original inline calculation (kept for safety) ----
        risk_points = 0

        # Volatility contribution
        if result.annualized_volatility > 0.5:
            risk_points += 3
        elif result.annualized_volatility > 0.35:
            risk_points += 2
        elif result.annualized_volatility > 0.25:
            risk_points += 1

        # Beta contribution
        if result.beta_status == "measured" and result.beta > 1.5:
            risk_points += 2
        elif result.beta_status == "measured" and result.beta > 1.2:
            risk_points += 1

        # Drawdown contribution
        if result.max_drawdown < -0.4:
            risk_points += 2
        elif result.max_drawdown < -0.25:
            risk_points += 1

        # Tail risk contribution
        if result.tail_risk_score > 70:
            risk_points += 2
        elif result.tail_risk_score > 50:
            risk_points += 1

        # Liquidity contribution
        if result.liquidity_score < 5:
            risk_points += 1

        result.overall_risk_score = min(10, max(1, 3 + risk_points))

        # Risk grade
        if result.overall_risk_score <= 3:
            result.risk_grade = "A"
        elif result.overall_risk_score <= 5:
            result.risk_grade = "B"
        elif result.overall_risk_score <= 7:
            result.risk_grade = "C"
        elif result.overall_risk_score <= 8:
            result.risk_grade = "D"
        else:
            result.risk_grade = "F"

        return result


# =============================================================================
# SENTIMENT ANALYSIS ENGINE
# =============================================================================

class MissingCredentialsError(RuntimeError):
    """A provider is not configured. Distinct from a provider that failed."""


_sentiment_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="sentiment")
_sentiment_slots = threading.BoundedSemaphore(10)


class SentimentAnalyzer:
    """Real-time news sentiment analysis with multiple sources"""

    BATCH_TIMEOUT_SECONDS = 25.0
    
    def __init__(self, api_client: APIClient):
        self.api = api_client
        self.vader = SentimentIntensityAnalyzer()
    
    def analyze(self, ticker: str) -> SentimentData:
        """Perform comprehensive sentiment analysis"""
        result = SentimentData()

        # Fetch from multiple sources in parallel for faster analysis.
        # `_fetchers` is overridable so the provider set can be substituted in
        # tests without patching five private methods.
        fetchers: List[Callable[[str, Set[str]], List[Dict]]] = getattr(
            self, "_fetchers", None
        ) or [
            self._fetch_newsapi,
            self._fetch_finnhub,
            self._fetch_polygon,
            self._fetch_fmp,
            self._fetch_yahoo,
        ]

        all_articles: List[Dict[str, Any]] = []
        # Track HOW the fetch went, not just what came back: "no articles" and
        # "nothing was asked" must not produce the same neutral result.
        result.sources_attempted = len(fetchers)
        succeeded = 0
        unconfigured = 0
        errors: List[str] = []
        # [PERF] A batch deadline, so a stalled provider cannot hold the pool
        # open indefinitely. `as_completed` without a timeout used to wait
        # forever and the per-future timeout below could then never fire.
        futures = []
        try:
            for fetcher in fetchers:
                if not _sentiment_slots.acquire(blocking=False):
                    errors.append("provider capacity busy")
                    continue
                try:
                    future = _sentiment_executor.submit(fetcher, ticker, set())
                except Exception:
                    _sentiment_slots.release()
                    raise
                future.add_done_callback(lambda _: _sentiment_slots.release())
                futures.append(future)
            try:
                for future in as_completed(futures, timeout=self.BATCH_TIMEOUT_SECONDS):
                    try:
                        articles = future.result(timeout=5)
                        succeeded += 1
                        if articles:
                            all_articles.extend(articles)
                    except MissingCredentialsError as e:
                        unconfigured += 1
                        errors.append(f"not configured: {e}")
                    except Exception as e:
                        errors.append(type(e).__name__)
                        logger.debug("Sentiment source failed for %s (%s)", ticker, type(e).__name__)
            except FuturesTimeoutError:
                errors.append("batch timeout: some providers did not respond")
        finally:
            # Running calls retain their bounded slots until they actually end.
            # Exiting a per-request executor context would wait past the deadline.
            for future in futures:
                future.cancel()
        result.sources_succeeded = succeeded
        result.source_errors = sorted(errors)[:5]

        # Stable ordering also makes duplicate selection independent of which
        # provider completed first. Pandas normalizes mixed aware/naive dates.
        def article_order(article):
            try:
                published = pd.to_datetime(article.get('published'), utc=True)
                timestamp = int(published.value) if pd.notna(published) else -2**63
            except (TypeError, ValueError, OverflowError):
                timestamp = -2**63
            return (timestamp, str(article.get('title') or ''),
                    str(article.get('source') or ''), str(article.get('url') or ''))

        all_articles.sort(key=article_order, reverse=True)
        # Deduplicate after parallel fetch
        seen_titles = set()
        articles = []
        for article in all_articles:
            title = (article.get('title') or '').strip()
            if not title:
                continue
            title_key = title.lower()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            articles.append(article)
        
        if not articles:
            if succeeded:
                result.status = "PARTIAL" if errors and succeeded + unconfigured < len(fetchers) else "NO_NEWS"
            elif unconfigured and unconfigured == result.sources_attempted:
                result.status = "NOT_CONFIGURED"
            else:
                result.status = "PROVIDER_UNAVAILABLE"
            result.measured = False
            logger.info(
                "No news articles for %s (%s; %d/%d providers responded)",
                ticker, result.status, succeeded, result.sources_attempted,
            )
            return result
        
        # Analyze sentiment
        _attempted, _succeeded, _errors = (
            result.sources_attempted, result.sources_succeeded, result.source_errors,
        )
        result = self._analyze_articles(articles, result)
        result.sources_attempted, result.sources_succeeded = _attempted, _succeeded
        result.source_errors = _errors
        result.measured = True
        result.status = "OK" if succeeded == result.sources_attempted else "PARTIAL"
        
        # Store top articles
        result.top_articles = articles[:10]
        result.key_headlines = [a.get('title', '')[:100] for a in articles[:5]]
        # Provenance (date + source of the freshest article)
        try:
            top = articles[0] if articles else {}
            pub = top.get('published')
            if pub:
                result.last_news_timestamp = pub.strftime('%Y-%m-%d') if hasattr(pub, 'strftime') else str(pub)[:10]
            result.key_headline_sources = [str(a.get('source', '')).strip() for a in articles[:5] if a.get('source')]
        except Exception:
            pass
        
        return result
    
    def _fetch_newsapi(self, ticker: str, seen: Set[str]) -> List[Dict]:
        """Fetch from NewsAPI"""
        articles = []
        
        if not self.api.news_api_key:
            raise MissingCredentialsError("NewsAPI")
        
        try:
            data = self.api.fetch_json(
                "https://newsapi.org/v2/everything",
                params={
                    "q": ticker,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 50,
                    "apiKey": self.api.news_api_key
                },
                source="newsapi"
            )
            
            if not isinstance(data, dict) or 'articles' not in data:
                raise RuntimeError("NewsAPI response unavailable")
            if data and 'articles' in data:
                for article in data['articles'][:30]:
                    title = article.get('title', '')
                    if title and title not in seen:
                        seen.add(title)
                        articles.append({
                            'title': title,
                            'summary': article.get('description', ''),
                            'published': parse_datetime(article.get('publishedAt')),
                            'source': article.get('source', {}).get('name', 'NewsAPI'),
                            'url': article.get('url', '')
                        })
        except Exception as e:
            raise RuntimeError("NewsAPI news unavailable") from e
        
        return articles
    
    def _fetch_finnhub(self, ticker: str, seen: Set[str]) -> List[Dict]:
        """Fetch from Finnhub"""
        articles = []
        
        if not self.api.finnhub_key:
            raise MissingCredentialsError("Finnhub")
        
        try:
            today = datetime.now()
            from_date = (today - timedelta(days=14)).strftime('%Y-%m-%d')
            to_date = today.strftime('%Y-%m-%d')
            
            data = self.api.fetch_json(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": ticker.upper(),
                    "from": from_date,
                    "to": to_date,
                    "token": self.api.finnhub_key
                },
                source="finnhub"
            )
            
            if not isinstance(data, list):
                raise RuntimeError("Finnhub response unavailable")
            if data and isinstance(data, list):
                for article in data[:40]:
                    title = article.get('headline', '')
                    if title and title not in seen:
                        seen.add(title)
                        articles.append({
                            'title': title,
                            'summary': article.get('summary', ''),
                            'published': parse_datetime(article.get('datetime')),
                            'source': article.get('source', 'Finnhub'),
                            'url': article.get('url', '')
                        })
        except Exception as e:
            raise RuntimeError("Finnhub news unavailable") from e
        
        return articles
    
    def _fetch_polygon(self, ticker: str, seen: Set[str]) -> List[Dict]:
        """Fetch from Polygon"""
        articles = []
        
        if not self.api.polygon_key:
            raise MissingCredentialsError("Polygon")
        
        try:
            data = self.api.fetch_json(
                "https://api.polygon.io/v2/reference/news",
                params={
                    "ticker": ticker.upper(),
                    "limit": 40,
                    "order": "desc",
                    "apiKey": self.api.polygon_key
                },
                source="polygon"
            )
            
            if not isinstance(data, dict) or 'results' not in data:
                raise RuntimeError("Polygon response unavailable")
            if data and 'results' in data:
                for article in data['results'][:40]:
                    title = article.get('title', '')
                    if title and title not in seen:
                        seen.add(title)
                        articles.append({
                            'title': title,
                            'summary': article.get('description', ''),
                            'published': parse_datetime(article.get('published_utc')),
                            'source': article.get('publisher', {}).get('name', 'Polygon'),
                            'url': article.get('article_url', '')
                        })
        except Exception as e:
            raise RuntimeError("Polygon news unavailable") from e
        
        return articles
    
    def _fetch_fmp(self, ticker: str, seen: Set[str]) -> List[Dict]:
        """Fetch from Financial Modeling Prep - DISABLED (endpoint deprecated)"""
        # FMP has deprecated the v3/stock_news endpoint for free tier
        # Return empty to avoid 403 errors
        raise MissingCredentialsError("FMP news endpoint disabled")
    
    def _fetch_yahoo(self, ticker: str, seen: Set[str]) -> List[Dict]:
        """Fetch from Yahoo Finance (fallback)"""
        articles = []
        
        try:
            stock = yf.Ticker(ticker)
            news = getattr(stock, 'news', []) or []
            
            for article in news[:20]:
                content = article.get('content', {})
                title = content.get('title', article.get('title', ''))
                
                if title and title not in seen:
                    seen.add(title)
                    
                    pub_date = content.get('pubDate', article.get('providerPublishTime'))
                    published = parse_datetime(pub_date)
                    
                    articles.append({
                        'title': title,
                        'summary': content.get('summary', ''),
                        'published': published,
                        'source': content.get('provider', {}).get('displayName', 'Yahoo Finance'),
                        'url': article.get('link', '')
                    })
        except Exception as e:
            raise RuntimeError("Yahoo news unavailable") from e
        
        return articles
    
    def _analyze_articles(self, articles: List[Dict], result: SentimentData) -> SentimentData:
        """Analyze article sentiments"""
        if not articles:
            return result
        
        sentiments = []
        now = datetime.now(timezone.utc)
        recent_sentiments = []
        
        for article in articles:
            title = article.get('title', '')
            summary = article.get('summary', '')
            text = f"{title} {summary}".strip()
            
            if text:
                scores = self.vader.polarity_scores(text)
                compound = scores['compound']
                sentiments.append(compound)
                
                # Track recent (48h) sentiment
                published = article.get('published')
                if published:
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    if (now - published).total_seconds() < 48 * 3600:
                        recent_sentiments.append(compound)
                
                # Topic detection
                text_lower = text.lower()
                if any(w in text_lower for w in ['earnings', 'revenue', 'profit', 'eps']):
                    result.earnings_mentions += 1
                if any(w in text_lower for w in ['upgrade', 'downgrade', 'analyst', 'rating']):
                    result.analyst_mentions += 1
                if any(w in text_lower for w in ['product', 'launch', 'release', 'new']):
                    result.product_mentions += 1
                if any(w in text_lower for w in ['lawsuit', 'legal', 'court', 'sue']):
                    result.legal_mentions += 1
                if any(w in text_lower for w in ['sec', 'fda', 'regulat', 'compliance']):
                    result.regulatory_mentions += 1
                if any(w in text_lower for w in ['merger', 'acquisition', 'buyout', 'deal']):
                    result.merger_mentions += 1
                if any(w in text_lower for w in ['ceo', 'cfo', 'executive', 'resign']):
                    result.executive_mentions += 1
        
        result.news_volume = len(articles)
        
        if sentiments:
            result.sentiment_score = float(np.mean(sentiments))
            
            # Sentiment label
            if result.sentiment_score > 0.15:
                result.sentiment_label = "POSITIVE"
            elif result.sentiment_score < -0.15:
                result.sentiment_label = "NEGATIVE"
            else:
                result.sentiment_label = "NEUTRAL"
            
            # Count by sentiment
            result.positive_articles = sum(1 for s in sentiments if s > 0.05)
            result.negative_articles = sum(1 for s in sentiments if s < -0.05)
            result.neutral_articles = result.news_volume - result.positive_articles - result.negative_articles
        
        if recent_sentiments:
            result.sentiment_7d_avg = float(np.mean(recent_sentiments))
            
            # Sentiment trend
            if result.sentiment_7d_avg > result.sentiment_score + 0.1:
                result.sentiment_trend = "IMPROVING"
            elif result.sentiment_7d_avg < result.sentiment_score - 0.1:
                result.sentiment_trend = "DETERIORATING"
            else:
                result.sentiment_trend = "STABLE"
        
        # News velocity
        if articles:
            oldest = min((a.get('published') for a in articles if a.get('published')), default=now)
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            days = max(1, (now - oldest).days)
            result.news_velocity = len(articles) / days
        
        # Controversy score
        result.controversy_score = min(10.0, sum(1 for s in sentiments if s < -0.3) * 1.5)
        
        # Media coverage score
        result.media_coverage_score = min(10.0, len(articles) / 5)
        
        return result


# =============================================================================
# MARKET INTELLIGENCE ENGINE
# =============================================================================

class MarketIntelligenceAnalyzer:
    """Analyst ratings, price targets, and market intelligence"""
    
    def __init__(self, api_client: APIClient):
        self.api = api_client
    
    def analyze(self, ticker: str) -> MarketIntelligence:
        """Gather market intelligence"""
        result = MarketIntelligence()
        
        # Get analyst data from multiple sources
        result = self._get_finnhub_data(ticker, result)
        result = self._get_yahoo_data(ticker, result)
        
        return result
    
    def _get_finnhub_data(self, ticker: str, result: MarketIntelligence) -> MarketIntelligence:
        """Get analyst data from Finnhub"""
        if not self.api.finnhub_key:
            return result
        
        try:
            # Recommendations
            rec_data = self.api.fetch_json(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": ticker.upper(), "token": self.api.finnhub_key},
                source="finnhub"
            )
            
            if rec_data and isinstance(rec_data, list) and rec_data:
                latest = rec_data[0]
                result.buy_ratings = latest.get('strongBuy', 0) + latest.get('buy', 0)
                result.hold_ratings = latest.get('hold', 0)
                result.sell_ratings = latest.get('sell', 0) + latest.get('strongSell', 0)
                
                total = result.buy_ratings + result.hold_ratings + result.sell_ratings
                result.analyst_count = int(total)
                if total > 0:
                    # Weighted score (1-5)
                    weighted = (
                        5 * latest.get('strongBuy', 0) +
                        4 * latest.get('buy', 0) +
                        3 * latest.get('hold', 0) +
                        2 * latest.get('sell', 0) +
                        1 * latest.get('strongSell', 0)
                    ) / total
                    result.rating_score = weighted
                    
                    if weighted >= 4.3:
                        result.analyst_rating = "STRONG_BUY"
                    elif weighted >= 3.6:
                        result.analyst_rating = "BUY"
                    elif weighted >= 2.6:
                        result.analyst_rating = "HOLD"
                    elif weighted >= 1.8:
                        result.analyst_rating = "SELL"
                    else:
                        result.analyst_rating = "STRONG_SELL"
            
            # Price Targets - DISABLED (premium endpoint, use Yahoo fallback)
            # pt_data = self.api.fetch_json(
            #     "https://finnhub.io/api/v1/stock/price-target",
            #     params={"symbol": ticker.upper(), "token": self.api.finnhub_key},
            #     source="finnhub"
            # )
            # 
            # if pt_data and isinstance(pt_data, dict):
            #     result.target_low = pt_data.get('targetLow', 0) or 0
            #     result.target_mean = pt_data.get('targetMean', 0) or 0
            #     result.target_median = pt_data.get('targetMedian', 0) or 0
            #     result.target_high = pt_data.get('targetHigh', 0) or 0
            
        except Exception as e:
            logger.debug(f"Finnhub intelligence error: {e}")
        
        return result
    
    def _get_yahoo_data(self, ticker: str, result: MarketIntelligence) -> MarketIntelligence:
        """Get analyst data from Yahoo Finance"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            result.analyst_count = int(
                safe_get(info, 'numberOfAnalystOpinions', 0)
                or result.analyst_count
                or (result.buy_ratings + result.hold_ratings + result.sell_ratings)
            )
            
            # Price targets (fallback)
            if result.target_mean == 0:
                result.target_mean = safe_get(info, 'targetMeanPrice', 0)
                result.target_low = safe_get(info, 'targetLowPrice', 0)
                result.target_high = safe_get(info, 'targetHighPrice', 0)
                result.target_median = safe_get(info, 'targetMedianPrice', result.target_mean)
            
            # Implied upside
            current_price = safe_get(info, 'currentPrice', 0) or safe_get(info, 'regularMarketPrice', 0)
            if current_price > 0 and result.target_mean > 0:
                result.implied_upside = ((result.target_mean - current_price) / current_price) * 100
            
            # Analyst recommendation
            if result.analyst_rating in ("", "HOLD"):
                rec = str(info.get('recommendationKey') or '').upper()
                if rec in ['STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL']:
                    result.analyst_rating = rec
            
            # Earnings data
            calendar = getattr(stock, 'calendar', None)
            if isinstance(calendar, dict):
                earnings_date = calendar.get('Earnings Date')
                if earnings_date:
                    if isinstance(earnings_date, list) and earnings_date:
                        ed = earnings_date[0]
                    else:
                        ed = earnings_date
                    
                    if isinstance(ed, (datetime, pd.Timestamp)):
                        # Only show future earnings dates
                        ed_dt = ed if isinstance(ed, datetime) else ed.to_pydatetime()
                        if ed_dt.replace(tzinfo=None) > datetime.now():
                            result.next_earnings_date = ed.strftime('%Y-%m-%d')
                    elif isinstance(ed, date):
                        if ed > date.today():
                            result.next_earnings_date = ed.isoformat()
                
                result.earnings_estimate = calendar.get('Earnings Average', 0) or 0
                result.revenue_estimate = calendar.get('Revenue Average', 0) or 0
            
            # Earnings surprise
            earnings = getattr(stock, 'earnings_history', None)
            if earnings is not None and not earnings.empty:
                if 'surprisePercent' in earnings.columns:
                    last_surprise = earnings['surprisePercent'].iloc[-1]
                    if not pd.isna(last_surprise):
                        result.last_earnings_surprise = float(last_surprise)
            
        except Exception as e:
            logger.debug(f"Yahoo intelligence error: {e}")
        
        return result


# =============================================================================
# INSIDER ANALYSIS ENGINE
# =============================================================================

class InsiderAnalyzer:
    """Insider trading and institutional ownership analysis"""
    
    def __init__(self, api_client: APIClient):
        self.api = api_client
    
    def analyze(self, ticker: str) -> InsiderData:
        """Analyze insider activity"""
        result = InsiderData()
        
        result = self._get_finnhub_insider(ticker, result)
        result = self._get_yahoo_insider(ticker, result)
        result = self._calculate_sentiment(result)
        # No source answered: zero buys / zero sells is then an absence of data,
        # not a measured lack of activity.
        result.data_status = "measured" if result.sources_ok > 0 else "unavailable"
        
        return result
    
    def _get_finnhub_insider(self, ticker: str, result: InsiderData) -> InsiderData:
        """Get insider data from Finnhub"""
        if not self.api.finnhub_key:
            return result
        
        try:
            data = self.api.fetch_json(
                "https://finnhub.io/api/v1/stock/insider-transactions",
                params={"symbol": ticker.upper(), "token": self.api.finnhub_key},
                source="finnhub"
            )
            
            if data and 'data' in data:
                result.sources_ok += 1
                cutoff = datetime.now() - timedelta(days=90)
                notable: List[Dict[str, Any]] = []
                
                for txn in data['data']:
                    txn_date = parse_datetime(txn.get('transactionDate'))
                    if not txn_date:
                        continue
                    txn_date = txn_date.replace(tzinfo=None)
                    if not (cutoff <= txn_date <= datetime.now()):
                        continue
                    
                    change = pd.to_numeric(txn.get('change') or txn.get('share') or 0, errors='coerce')
                    price = pd.to_numeric(txn.get('transactionPrice') or 0, errors='coerce')
                    change = float(change) if pd.notna(change) else 0.0
                    price = float(price) if pd.notna(price) else 0.0
                    value = abs(change * price) if price > 0 else 0.0
                    
                    # Classify transaction
                    txn_code = str(txn.get('transactionCode', '')).upper()
                    
                    if txn_code == 'P':
                        result.recent_buys += 1
                        result.buy_value += value
                    elif txn_code == 'S':
                        result.recent_sells += 1
                        result.sell_value += value
                    else:
                        continue  # Awards, exercises and unknown codes are not open-market trades.
                    
                    if value > 100000:
                        notable.append({
                            'name': str(txn.get('name') or 'Insider').strip(),
                            'action': 'bought' if txn_code == 'P' else 'sold',
                            'shares': abs(float(change)),
                            'price': float(price),
                            'value': float(value),
                            'date': txn_date.date().isoformat() if txn_date else '',
                            'code': txn_code,
                        })

                # Largest transactions first, but show distinct people before
                # allowing one filer's tranches to crowd out everyone else.
                notable.sort(key=lambda item: item['value'], reverse=True)
                selected: List[Dict[str, Any]] = []
                seen = set()
                for item in notable:
                    key = item['name'].casefold()
                    if key not in seen:
                        selected.append(item)
                        seen.add(key)
                    if len(selected) >= 5:
                        break
                if len(selected) < 5:
                    selected_ids = {id(item) for item in selected}
                    selected.extend(
                        item for item in notable
                        if id(item) not in selected_ids
                    )
                result.key_transactions_detail = selected[:5]
                result.key_transactions = [
                    (f"{item['name']} {item['action']} {item['shares']:,.0f} shares "
                     f"@ ${item['price']:,.2f} on {item['date']} "
                     f"(${item['value']:,.0f})")
                    for item in result.key_transactions_detail
                ]
            
        except Exception as e:
            logger.debug(f"Finnhub insider error: {e}")
        
        return result
    
    def _get_yahoo_insider(self, ticker: str, result: InsiderData) -> InsiderData:
        """Get insider/institutional data from Yahoo"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            
            # Institutional ownership
            result.institutional_ownership = safe_get(info, 'heldPercentInstitutions', 0, 100)
            
            # Short interest
            result.short_interest = safe_get(info, 'shortPercentOfFloat', 0, 100)
            result.short_ratio = safe_get(info, 'shortRatio', 0)
            result.days_to_cover = result.short_ratio
            
            # Fallback insider transactions
            if result.recent_buys == 0 and result.recent_sells == 0:
                insider_txns = getattr(stock, 'insider_transactions', None)
                if insider_txns is not None:
                    result.sources_ok += 1
                if insider_txns is not None and not insider_txns.empty:
                    cutoff = datetime.now() - timedelta(days=90)
                    for _, row in insider_txns.iterrows():
                        txn_date = parse_datetime(row.get('Start Date') or row.get('Date'))
                        if not txn_date:
                            continue
                        txn_date = txn_date.replace(tzinfo=None)
                        if not (cutoff <= txn_date <= datetime.now()):
                            continue
                        # Yahoo commonly reports Shares as an unsigned
                        # quantity. Classifying every positive number as a buy
                        # turns routine sales into fake purchase activity.
                        transaction_text = " ".join(
                            str(row.get(field, '') or '')
                            for field in ('Transaction', 'Text')
                        ).casefold()
                        if any(word in transaction_text for word in
                               ('sale', 'sell', 'sold', 'disposition')):
                            action = 'sold'
                        elif any(word in transaction_text for word in
                                 ('purchase', 'buy', 'bought', 'acquisition')):
                            action = 'bought'
                        else:
                            # Grants, gifts, option exercises, and unlabelled
                            # rows are not open-market directional evidence.
                            continue

                        shares_raw = pd.to_numeric(row.get('Shares', 0), errors='coerce')
                        value_raw = pd.to_numeric(row.get('Value', 0), errors='coerce')
                        shares = abs(float(shares_raw)) if pd.notna(shares_raw) else 0.0
                        value = abs(float(value_raw)) if pd.notna(value_raw) else 0.0
                        if action == 'bought':
                            result.recent_buys += 1
                            result.buy_value += value
                        else:
                            result.recent_sells += 1
                            result.sell_value += value

                        if value > 100_000:
                            price = value / shares if shares > 0 else 0.0
                            detail = {
                                'name': str(row.get('Insider') or 'Insider').strip(),
                                'action': action,
                                'shares': shares,
                                'price': price,
                                'value': value,
                                'date': txn_date.date().isoformat(),
                                'code': 'YAHOO',
                            }
                            result.key_transactions_detail.append(detail)
                            result.key_transactions.append(
                                f"{detail['name']} {action} {shares:,.0f} shares "
                                f"@ ${price:,.2f} on {detail['date']} (${value:,.0f})"
                            )
            
        except Exception as e:
            logger.debug(f"Yahoo insider error: {e}")
        
        return result
    
    def _calculate_sentiment(self, result: InsiderData) -> InsiderData:
        """Calculate insider sentiment"""
        result.net_flow = result.buy_value - result.sell_value
        
        total_activity = result.buy_value + result.sell_value
        if total_activity > 0:
            flow_ratio = result.net_flow / total_activity
            
            if flow_ratio > 0.3 and result.net_flow > 1_000_000:
                result.insider_sentiment = "BULLISH"
                result.insider_confidence = min(9.0, 5 + flow_ratio * 5)
            elif flow_ratio < -0.3 and result.net_flow < -1_000_000:
                result.insider_sentiment = "BEARISH"
                result.insider_confidence = max(1.0, 5 + flow_ratio * 5)
            else:
                result.insider_sentiment = "NEUTRAL"
                result.insider_confidence = 5.0
            
            # Hard override: significant selling (> $10M) is always BEARISH
            if result.net_flow < -10_000_000:
                result.insider_sentiment = "BEARISH"
                result.insider_confidence = 1.0  # Low confidence signal
        
        # Smart money flow
        if result.net_flow > 5_000_000:
            result.smart_money_flow = "POSITIVE"
        elif result.net_flow < -5_000_000:
            result.smart_money_flow = "NEGATIVE"
        else:
            result.smart_money_flow = "NEUTRAL"
        
        return result


# =============================================================================
# CATALYST ANALYZER
# =============================================================================

class CatalystAnalyzer:
    """
    Comprehensive catalyst detection for trading signals.
    
    Detects ALL material events that can move stock prices:
    - Earnings dates (from yfinance calendar)
    - Dividend dates
    - Product launches and technology adoption
    - Contract announcements and enterprise deals
    - M&A activity (acquisitions, mergers, takeovers)
    - FDA approvals and regulatory clearances
    - Partnership deals and joint ventures
    - SEC 8-K material events (from SEC EDGAR)
    - Management changes (CEO, CFO appointments)
    - Activist investor activity
    """
    
    # Catalyst keyword patterns for news headline scanning
    CATALYST_PATTERNS = {
        'product_launch': {
            'keywords': ['launch', 'launches', 'unveil', 'unveiled', 'introduce', 'rollout',
                        'new product', 'next-gen', 'AI chip', 'breakthrough', 'revolutionary',
                        'game-changing', 'first-of-its-kind', 'release', 'debut'],
            'weight': 8,
            'type': 'PRODUCT_LAUNCH'
        },
        'contract': {
            'keywords': ['contract', 'deal', 'agreement', 'awarded', 'wins', 'secures',
                        'enterprise', 'billion', 'million', 'government', 'defense', 'DOD',
                        'Pentagon', 'multi-year', 'exclusive', 'partnership'],
            'weight': 7,
            'type': 'CONTRACT'
        },
        'mna': {
            'keywords': ['acquire', 'acquisition', 'merger', 'takeover', 'buyout', 'purchase',
                        'bid', 'offer', 'strategic investment', 'stake', 'all-cash deal',
                        'premium', 'per share', 'to buy', 'agrees to acquire'],
            'weight': 10,
            'type': 'MNA'
        },
        'fda_regulatory': {
            'keywords': ['FDA', 'approved', 'approval', 'clearance', 'patent', 'granted',
                        'regulatory', 'license', 'certification', 'PDUFA', 'NDA accepted',
                        'breakthrough therapy', 'fast track', 'priority review', 'EUA'],
            'weight': 9,
            'type': 'FDA_APPROVAL'
        },
        'partnership': {
            'keywords': ['partner', 'collaboration', 'joint venture', 'alliance', 'team up',
                        'strategic', 'licensing', 'co-develop', 'ecosystem', 'agreement with'],
            'weight': 6,
            'type': 'PARTNERSHIP'
        },
        'management': {
            'keywords': ['CEO', 'CFO', 'executive', 'appointment', 'appoints', 'hire', 'hired',
                        'activist', 'board', 'restructuring', 'cost cutting', 'turnaround',
                        'resignation', 'steps down', 'names new'],
            'weight': 5,
            'type': 'MANAGEMENT'
        },
        'earnings_beat': {
            'keywords': ['beats', 'exceeds', 'surpasses', 'better than expected', 'blowout',
                        'record revenue', 'record profit', 'raised guidance', 'raises outlook'],
            'weight': 7,
            'type': 'EARNINGS_BEAT'
        },
        'negative': {
            'keywords': ['lawsuit', 'sued', 'investigation', 'fraud', 'recall', 'warning',
                        'downgrade', 'miss', 'missed', 'disappoints', 'cuts', 'layoff',
                        'layoffs', 'bankruptcy', 'default', 'fails', 'probe'],
            'weight': -8,
            'type': 'NEGATIVE'
        }
    }
    
    def __init__(self, api_client: APIClient):
        self.api = api_client
        self._sec_parser = None
    
    def analyze(self, ticker: str) -> CatalystData:
        """
        Comprehensive catalyst analysis from multiple sources.
        
        Sources:
        1. yfinance calendar (earnings, dividends)
        2. News headlines (product launches, contracts, M&A, etc.)
        3. SEC EDGAR 8-K filings (material events)
        """
        result = CatalystData()
        today = datetime.now().date()
        
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            
            # === SOURCE 1: yfinance Calendar (Earnings & Dividends) ===
            result = self._analyze_calendar(stock, result, today)
            
            # === SOURCE 2: News Headlines (All Other Catalysts) ===
            result = self._analyze_news_headlines(stock, ticker, result)
            
            # === SOURCE 3: SEC EDGAR 8-K Filings (Material Events) ===
            if not ticker.endswith(('.NS', '.BO')):  # US stocks only
                result = self._analyze_sec_filings(ticker, result)
            
            # Calculate overall catalyst score
            self._calculate_catalyst_score(result)
            
        except Exception as e:
            logger.debug(f"Catalyst analysis error for {ticker}: {e}")
        
        return result
    
    def _analyze_calendar(self, stock, result: CatalystData, today) -> CatalystData:
        """Analyze yfinance calendar for earnings and dividends."""
        try:
            calendar = getattr(stock, 'calendar', None)
            info = stock.info or {}
            
            if isinstance(calendar, dict):
                # Earnings date
                earnings_date = calendar.get('Earnings Date')
                if earnings_date:
                    if isinstance(earnings_date, list) and earnings_date:
                        ed = earnings_date[0]
                    else:
                        ed = earnings_date
                    
                    if isinstance(ed, (datetime, pd.Timestamp)):
                        result.earnings_date = ed.strftime('%Y-%m-%d')
                        result.days_to_earnings = (ed.date() - today).days
                    elif isinstance(ed, date):
                        result.earnings_date = ed.isoformat()
                        result.days_to_earnings = (ed - today).days
                    
                    if 0 <= result.days_to_earnings <= 14:
                        result.upcoming_events.append(f"📅 Earnings in {result.days_to_earnings} days")
                        result.near_term_catalyst = True
                        if result.catalyst_type == "UNKNOWN":
                            result.catalyst_type = "EARNINGS"
                        result.catalyst_score += 8
                
                # Dividend date
                ex_div = calendar.get('Ex-Dividend Date')
                if ex_div:
                    if isinstance(ex_div, list) and ex_div:
                        ex_div = ex_div[0]
                    
                    ex_date = None
                    if isinstance(ex_div, (datetime, pd.Timestamp)):
                        ex_date = ex_div.date()
                    elif isinstance(ex_div, date):
                        ex_date = ex_div
                    
                    if ex_date and ex_date >= today:
                        result.ex_dividend_date = ex_date.isoformat()
                        days_to_div = (ex_date - today).days
                        if days_to_div <= 30:
                            result.upcoming_events.append(f"💰 Ex-dividend in {days_to_div} days")
                            result.catalyst_score += 3
            
            result.dividend_amount = safe_get(info, 'dividendRate', 0)
            # [DECISION-CORE] use the shared normaliser — stores as FRACTION
            if HAS_DECISION_CORE:
                result.dividend_yield = dividend_yield_from_info(info)
            else:
                raw = info.get('dividendYield') or 0
                try:
                    raw = float(raw)
                except (TypeError, ValueError):
                    raw = 0.0
                if raw <= 0:
                    result.dividend_yield = 0.0
                elif raw <= 1.0:
                    result.dividend_yield = raw
                elif raw <= 100.0:
                    result.dividend_yield = raw / 100.0
                else:
                    result.dividend_yield = 0.0

        except Exception as e:
            logger.debug(f"Calendar analysis error: {e}")
        
        return result
    
    def _analyze_news_headlines(self, stock, ticker: str, result: CatalystData) -> CatalystData:
        """Scan news headlines for product launches, contracts, M&A, FDA approvals, etc."""
        try:
            news = getattr(stock, 'news', []) or []
            
            if not news:
                return result
            
            detected_catalysts = []
            
            for article in news[:20]:  # Check last 20 articles
                title = (article.get('title') or '').lower()
                published = article.get('providerPublishTime', 0)
                
                # Check against each catalyst pattern
                for cat_name, config in self.CATALYST_PATTERNS.items():
                    keywords = config['keywords']
                    weight = config['weight']
                    cat_type = config['type']
                    
                    matched_keywords = [kw for kw in keywords if kw.lower() in title]
                    
                    if matched_keywords:
                        # Calculate confidence based on keyword matches
                        confidence = min(1.0, len(matched_keywords) * 0.25)
                        
                        detected_catalysts.append({
                            'type': cat_type,
                            'headline': article.get('title', '')[:80],
                            'weight': weight,
                            'confidence': confidence,
                            'keywords': matched_keywords[:3]
                        })
                        break  # One category per headline
            
            # Add top catalysts to result
            positive_catalysts = [c for c in detected_catalysts if c['weight'] > 0]
            negative_catalysts = [c for c in detected_catalysts if c['weight'] < 0]
            
            # Sort by weight (importance)
            positive_catalysts.sort(key=lambda x: x['weight'], reverse=True)
            
            for cat in positive_catalysts[:3]:  # Top 3 positive catalysts
                icon = self._get_catalyst_icon(cat['type'])
                result.upcoming_events.append(f"{icon} {cat['type']}: {cat['headline'][:50]}...")
                result.catalyst_score += cat['weight'] * cat['confidence']
                result.near_term_catalyst = True
                
                # Set primary catalyst type if not already set
                if result.catalyst_type == "UNKNOWN" or result.catalyst_type == "EARNINGS":
                    result.catalyst_type = cat['type']
            
            # Negative catalysts reduce score
            for cat in negative_catalysts[:2]:
                result.upcoming_events.append(f"⚠️ {cat['type']}: {cat['headline'][:50]}...")
                result.catalyst_score += cat['weight'] * cat['confidence']  # Negative weight
            
        except Exception as e:
            logger.debug(f"News headline analysis error: {e}")
        
        return result
    
    def _analyze_sec_filings(self, ticker: str, result: CatalystData) -> CatalystData:
        """Analyze SEC EDGAR 8-K filings for material events."""
        try:
            # Import SEC parser
            if self._sec_parser is None:
                _sec_module = _import_optional("sec_edgar_parser", "modules.sec_edgar_parser")
                if _sec_module:
                    self._sec_parser = _sec_module.SECEdgarParser()
                else:
                    return result
            
            filings = self._sec_parser.get_material_filings(ticker, days=14)
            
            for filing in filings[:3]:  # Top 3 material filings
                icon = '📋'
                if filing.catalyst_type == 'MNA':
                    icon = '🤝'
                elif filing.catalyst_type == 'CONTRACT':
                    icon = '📝'
                elif filing.catalyst_type == 'MANAGEMENT':
                    icon = '👔'
                
                event_text = f"{icon} SEC 8-K: {filing.catalyst_type}"
                if filing.description:
                    event_text += f" - {filing.description[:40]}..."
                
                result.upcoming_events.append(event_text)
                result.catalyst_score += filing.catalyst_weight * 10
                result.near_term_catalyst = True
                
                if result.catalyst_type == "UNKNOWN":
                    result.catalyst_type = f"SEC_{filing.catalyst_type}"
                    
        except Exception as e:
            logger.debug(f"SEC filing analysis error: {e}")
        
        return result
    
    def _get_catalyst_icon(self, cat_type: str) -> str:
        """Get emoji icon for catalyst type."""
        icons = {
            'PRODUCT_LAUNCH': '🚀',
            'CONTRACT': '📝',
            'MNA': '🤝',
            'FDA_APPROVAL': '✅',
            'PARTNERSHIP': '🔗',
            'MANAGEMENT': '👔',
            'EARNINGS_BEAT': '💰',
            'EARNINGS': '📅',
            'NEGATIVE': '⚠️'
        }
        return icons.get(cat_type, '📰')
    
    def _calculate_catalyst_score(self, result: CatalystData):
        """Calculate final catalyst score with caps."""
        # Cap the score
        result.catalyst_score = max(-10, min(30, result.catalyst_score))
        
        # Set catalyst flag
        if result.catalyst_score >= 5:
            result.near_term_catalyst = True


# =============================================================================
# VALUATION ENGINE
# =============================================================================

class ValuationAnalyzer:
    """DCF, Graham Number, and relative valuation"""

    # One plausibility band for every intrinsic-value method, symmetric in log
    # terms: an estimate more than 2x above OR below the price is treated as a
    # model/data artefact (a single-stage FCF DCF or a Graham number that far
    # from the market is measuring its own assumptions). The band used to be asymmetric (0.5x-0.7x floor, 5x
    # ceiling, plus Graham only when P/E <= 20), which discarded bearish
    # estimates while keeping bullish ones -- a filter that could only raise
    # fair value.
    PLAUSIBLE_RATIO = 2.0

    @classmethod
    def _plausible(cls, value: float, price: float) -> bool:
        return (value > 0 and price > 0
                and price / cls.PLAUSIBLE_RATIO <= value <= price * cls.PLAUSIBLE_RATIO)
    
    def analyze(self, ticker: str, current_price: float, 
                fundamental: FundamentalData, risk: RiskMetrics,
                analyst_target: float = 0.0) -> ValuationMetrics:
        """Calculate intrinsic value estimates"""
        result = ValuationMetrics()
        
        if current_price <= 0:
            return result
        
        # [NEW] ETF Bypass
        if hasattr(fundamental, 'is_etf') and fundamental.is_etf:
           # Company DCF/Graham methods do not apply to a pooled vehicle. Do not
           # turn that absence into a neutral fair-value estimate at spot.
           result.dcf_status = "not_applicable"
           result.valuation_status = "not_applicable"
           result.valuation_score = fundamental.valuation_score # Calculated in FundamentalAnalyzer based on PE
           result.valuation_label = "NOT_APPLICABLE"
           
           # If valuation score from fundamental is low/high, adjust label
           if result.valuation_score >= 70:
               result.valuation_label = "RELATIVELY_INEXPENSIVE"
           elif result.valuation_score <= 30:
               result.valuation_label = "RELATIVELY_EXPENSIVE"
               
           return result

        
        # DCF Valuation. A free-cash-flow DCF does not describe a bank, insurer
        # or broker-dealer: their operating cash flow carries deposits, float
        # and trading assets (BRK-A's FCF DCF came out at 3.3x its price). It is
        # not applicable there, as Altman Z already is.
        industry = str(getattr(fundamental, '_industry', '') or '').lower()
        if any(k in industry for k in ('bank', 'insurance', 'capital markets', 'mortgage')):
            result.dcf_status = "not_applicable"
            result.dcf_method = "not_applicable_financial"
        else:
            result = self._calculate_dcf(fundamental, risk, current_price, result, analyst_target)
        
        # Graham Number
        result = self._calculate_graham(fundamental, current_price, result)
        
        # Composite fair value. Both methods pass the SAME symmetric band (see
        # PLAUSIBLE_RATIO, applied where each is computed); nothing is admitted
        # or dropped by which side of the price it lands on.
        fair_values = []
        fair_value_sources = []

        if result.graham_status == "measured" and result.graham_number > 0:
            fair_values.append(result.graham_number)
            fair_value_sources.append("graham")

        if result.dcf_status == "measured" and result.dcf_fair_value > 0:
            fair_values.append(result.dcf_fair_value)
            fair_value_sources.append("dcf")
        
        if fair_values:
            result.fair_value_estimate = sum(fair_values) / len(fair_values)
            result.fair_value_sources = fair_value_sources
            result.valuation_status = "measured"
            
            # Valuation score and label
            margin_of_safety = (result.fair_value_estimate - current_price) / result.fair_value_estimate * 100
            
            if margin_of_safety > 30:
                result.valuation_score = 90
                result.valuation_label = "SIGNIFICANTLY_UNDERVALUED"
            elif margin_of_safety > 15:
                result.valuation_score = 75
                result.valuation_label = "UNDERVALUED"
            elif margin_of_safety > 0:
                result.valuation_score = 60
                result.valuation_label = "SLIGHTLY_UNDERVALUED"
            elif margin_of_safety > -15:
                result.valuation_score = 50
                result.valuation_label = "FAIRLY_VALUED"
            elif margin_of_safety > -30:
                result.valuation_score = 35
                result.valuation_label = "OVERVALUED"
            else:
                result.valuation_score = 20
                result.valuation_label = "SIGNIFICANTLY_OVERVALUED"
        else:
            result.fair_value_estimate = 0.0
            result.fair_value_sources = []
            result.valuation_status = "unavailable"
            result.valuation_label = "UNAVAILABLE"
        
        return result
    
    def _calculate_dcf(self, fundamental: FundamentalData, risk: RiskMetrics,
                       current_price: float, result: ValuationMetrics,
                       analyst_target: float = 0.0) -> ValuationMetrics:
        """Calculate DCF fair value"""
        try:
            # Base cash flow with growth stock detection
            fcf = fundamental.fcf_per_share
            eps = fundamental.eps_ttm
            growth = fundamental.revenue_growth_yoy / 100 if fundamental.revenue_growth_yoy > 0 else 0
            pe = fundamental.pe_ratio if hasattr(fundamental, 'pe_ratio') and fundamental.pe_ratio else 0
            
            # Growth stock detection
            is_high_growth = growth > 0.15 or pe > 25
            
            # For growth stocks with negative FCF, use earnings-based approach
            if is_high_growth and fcf <= 0 and eps > 0:
                # Use owner earnings approximation: EPS * (1 - reinvestment rate)
                reinvestment_rate = 0.4 if growth > 0.3 else 0.3  # Higher growth = more reinvestment
                base_cf = eps * (1 - reinvestment_rate)
                result.dcf_method = "earnings_based"
            elif fcf > 0:
                base_cf = fcf
                result.dcf_method = "fcf_based"
            elif eps > 0:
                base_cf = eps * 0.7  # Conservative EPS fallback
                result.dcf_method = "eps_fallback"
            else:
                result.dcf_method = "no_positive_cash_flow"
                return result
            
            if base_cf <= 0:
                result.dcf_method = "no_positive_cash_flow"
                return result
            
            # Growth rate with high-growth adjustment. Without a reported growth
            # rate there is no stage-1 assumption to project, and a shrinking
            # business is held at the 2% floor -- not assumed to grow 5%.
            if not fundamental.has('revenue_growth'):
                result.dcf_method = f"{result.dcf_method}:growth_not_reported"
                return result
            growth_rate = fundamental.revenue_growth_yoy / 100
            growth_rate = max(0.02, min(0.25, growth_rate))  # 2% to 25%
            
            # For high-growth companies, ensure minimum growth rate
            if fundamental.revenue_growth_yoy > 20:
                growth_rate = max(growth_rate, 0.15)  # Floor at 15% for high-growth
            
            # Discount rate (WACC proxy)
            risk_free = Config.RISK_FREE_RATE
            beta = max(0.8, min(2.0, risk.beta if risk.beta_status == "measured" else 1.0))
            equity_risk_premium = Config.MARKET_RISK_PREMIUM
            discount_rate = risk_free + beta * equity_risk_premium
            
            # Terminal growth — sector-adjusted for more realistic DCF
            # Growth stocks: higher terminal growth (tech/healthcare innovation)
            # Mature/cyclical: lower terminal growth (utilities, materials)
            SECTOR_TERMINAL_GROWTH = {
                'Technology': 0.035,
                'Communication Services': 0.030,
                'Healthcare': 0.030,
                'Consumer Discretionary': 0.025,
                'Consumer Cyclical': 0.025,
                'Industrials': 0.025,
                'Financial Services': 0.025,
                'Financials': 0.025,
                'Consumer Defensive': 0.020,
                'Consumer Staples': 0.020,
                'Real Estate': 0.020,
                'Energy': 0.020,
                'Basic Materials': 0.020,
                'Materials': 0.020,
                'Utilities': 0.018,
            }
            # Get sector from fundamental data (may be available via caller)
            sector = getattr(fundamental, '_sector', '') or ''
            terminal_growth = SECTOR_TERMINAL_GROWTH.get(sector, Config.DCF_TERMINAL_GROWTH)
            
            # For high-growth companies, use slightly higher terminal (they retain edge longer)
            if is_high_growth and terminal_growth < 0.030:
                terminal_growth = 0.030
            
            # Project cash flows with growth-aware decay
            total_pv = 0
            cf = base_cf
            is_high_growth = fundamental.revenue_growth_yoy > 15
            
            for year in range(1, Config.DCF_PROJECTION_YEARS + 1):
                # High-growth companies: slower decay (maintain 60% of growth rate)
                # Standard companies: faster decay (down to 30%)
                if is_high_growth:
                    year_growth = growth_rate * max(0.6, 1 - (year - 1) * 0.08)
                else:
                    year_growth = growth_rate * max(0.3, 1 - (year - 1) * 0.12)
                cf *= (1 + year_growth)
                pv = cf / ((1 + discount_rate) ** year)
                total_pv += pv
            
            # Terminal value
            terminal_cf = cf * (1 + terminal_growth)
            terminal_value = terminal_cf / (discount_rate - terminal_growth)
            terminal_pv = terminal_value / ((1 + discount_rate) ** Config.DCF_PROJECTION_YEARS)
            
            # Fair value is the present value itself. A margin of safety is a
            # purchase discipline applied by the rules that read this number
            # (the 25% overvaluation veto, the valuation-score bands); cutting it
            # here as well made the "25%" veto fire at ~6% over intrinsic value
            # and pulled a haircut into the 12-month target blend.
            fair_value = total_pv + terminal_pv
            
            result.dcf_fair_value = max(0, fair_value)
            
            # DCF plausibility: the symmetric band shared with Graham.
            if not self._plausible(result.dcf_fair_value, current_price):
                logger.debug(f"DCF sanity check failed: {result.dcf_fair_value:.2f} vs price {current_price:.2f}")
                result.dcf_fair_value = 0.0
                result.dcf_upside = 0.0
                result.dcf_status = "unavailable"
                result.dcf_method = f"{result.dcf_method}:sanity_rejected"
            else:
                result.dcf_upside = ((result.dcf_fair_value - current_price) / current_price) * 100 if current_price > 0 else 0
                result.dcf_status = "measured"
            
            result.dcf_margin_of_safety = (result.dcf_fair_value - current_price) / result.dcf_fair_value * 100 if result.dcf_fair_value > 0 else 0
            
        except Exception as e:
            logger.debug(f"DCF calculation error: {e}")
            result.dcf_fair_value = 0.0
            result.dcf_upside = 0.0
            result.dcf_status = "unavailable"
            result.dcf_method = "error"
        
        return result
    
    def _calculate_graham(self, fundamental: FundamentalData, 
                          current_price: float, result: ValuationMetrics) -> ValuationMetrics:
        """Calculate Graham Number"""
        try:
            eps = fundamental.eps_ttm
            bvps = fundamental.book_value_per_share
            
            if eps > 0 and bvps > 0:
                graham = math.sqrt(22.5 * eps * bvps)
                # Same plausibility band as the DCF. Outside it the inputs are a
                # data artefact (e.g. per-A-share EPS/book reported for a B
                # share: BRK-B read 43x its price), not a valuation.
                if self._plausible(graham, current_price):
                    result.graham_number = graham
                    result.graham_upside = ((graham - current_price) / current_price) * 100
                    result.graham_status = "measured"
                else:
                    result.graham_status = "rejected"
            
        except Exception as e:
            logger.debug(f"Graham calculation error: {e}")
        
        return result
    
    def calculate_composite_targets(self, current_price: float, 
                                     valuation: ValuationMetrics,
                                     market_intel: 'MarketIntelligence',
                                     monte_carlo: 'MonteCarloResults',
                                     technical: 'TechnicalIndicators') -> ValuationMetrics:
        """
        Calculate enhanced composite price targets using weighted methodology:
        - Analyst Consensus (50%; 25% under thin coverage) - third-party view
        - Intrinsic value (30%) - the engine's DCF/Graham fair value
        Weights are renormalised over the sources that exist.
        Note: Monte Carlo is 30-day projection, kept separate from 12-month targets
        """
        if current_price <= 0:
            return valuation
        
        targets = []
        weights = []
        
        # 1. Analyst consensus. Thin coverage is down-weighted so a single
        # outlier estimate cannot dominate a purportedly diversified model.
        target_sources: List[str] = []
        if market_intel and market_intel.target_mean > 0:
            targets.append(market_intel.target_mean)
            target_sources.append("analyst_consensus")
            coverage = int(getattr(market_intel, 'analyst_count', 0) or 0)
            analyst_weight = (
                Config.THIN_COVERAGE_ANALYST_WEIGHT
                if coverage < Config.THIN_COVERAGE_ANALYSTS
                else Config.BLEND_WEIGHT_ANALYST
            )
            weights.append(analyst_weight)
        
        # 2. The engine's own intrinsic value (DCF and/or Graham, each inside
        # the symmetric plausibility band). This is the SAME fair value the
        # valuation score reads, so the target can no longer ignore an
        # "overvalued" verdict the tear sheet prints beside it.
        if (getattr(valuation, 'valuation_status', '') == "measured"
                and valuation.fair_value_estimate > 0):
            targets.append(valuation.fair_value_estimate)
            srcs = list(getattr(valuation, 'fair_value_sources', []) or [])
            target_sources.append("dcf" if srcs == ["dcf"] else "intrinsic_value")
            weights.append(Config.BLEND_WEIGHT_DCF)

        # Technical resistance is NOT a 12-month valuation input: it was only
        # ever admitted when above the price (an upside-only source) and it is
        # a tactical level the trade setup already uses for its partial exit.

        # NOTE: Monte Carlo is 30-day short-term projection
        # Keep it separate for probability analysis, don't mix with 12-month targets
        # The Monte Carlo section displays this separately in the output

        # A nearby swing high is a tactical level, not a 12-month valuation. On
        # its own (no analyst consensus, no valid DCF) it would be published as
        # the "valuation target" and set the reward/risk, so no target is formed.
        if not ({"analyst_consensus", "dcf", "intrinsic_value"} & set(target_sources)):
            valuation.target_base = 0.0
            valuation.target_conservative = 0.0
            valuation.target_aggressive = 0.0
            valuation.target_components = []
            _apply_target_quality(valuation, [], source_names=[])
            valuation.target_probability = 0.0
            return valuation

        if targets and weights:
            # Normalize weights
            total_weight = sum(weights)
            weights = [w / total_weight for w in weights]
            
            # Calculate weighted base target
            valuation.target_base = sum(t * w for t, w in zip(targets, weights))
            valuation.target_components = [
                {"source": src, "value": float(t), "weight": round(float(w), 4)}
                for src, t, w in zip(target_sources, targets, weights)
            ]
            
            # Preserve the observed method range. Fixed multipliers are not
            # operating scenarios and must not be labelled bear/base/bull.
            valuation.target_conservative = min(targets)
            valuation.target_aggressive = max(targets)
            
            # How many sources contributed, and how much they agreed. A count
            # is not confidence: the analyst target, the DCF (which anchors
            # extreme estimates back to price and blends analyst targets for
            # some names) and technical resistance all lean on the same price
            # and the same sell-side estimates, so a fourth number is not a
            # fourth independent confirmation.
            _apply_target_quality(valuation, targets, source_names=target_sources)

            # No target-hit probability is published. It used to be Monte
            # Carlo's TERMINAL profit probability relabelled as the chance of
            # reaching a 12-month target — a different event, over a different
            # horizon, from a 30-day simulation. The Monte Carlo block reports
            # its own probability with its own event stated instead.
            valuation.target_probability = 0.0
        
        return valuation


# =============================================================================
# MONTE CARLO SIMULATOR
# =============================================================================

class MonteCarloSimulator:
    """Monte Carlo price simulation with fat-tailed distribution for realistic risk assessment"""
    
    def simulate(self, current_price: float, risk: RiskMetrics, 
                 days: int = 30, simulations: int = None,
                 regime: MarketRegime = None, has_catalyst: bool = False,
                 annual_drift_cap: Optional[float] = None,
                 seed: Optional[int] = None) -> MonteCarloResults:
        """Run enhanced Monte Carlo simulation with fat tails, regime awareness, and convergence check"""
        result = MonteCarloResults()
        
        if simulations is None:
            simulations = Config.MC_SIMULATIONS
        
        if current_price <= 0 or risk.daily_volatility <= 0:
            return result
        
        try:
            # A local seeded generator makes the calculation reproducible and
            # auditable. Callers should derive the seed from ticker + market
            # timestamp so the distribution changes only when its inputs do.
            rng = np.random.default_rng(0 if seed is None else seed)
            
            # Parameters
            daily_vol = risk.daily_volatility
            annual_return = float(risk.expected_return)
            if annual_drift_cap is not None:
                cap = abs(float(annual_drift_cap))
                annual_return = max(-cap, min(cap, annual_return))
            base_daily_return = annual_return / 252
            
            # Regime-aware drift adjustment
            drift_adjustment = self._get_regime_drift(regime)
            # Itô correction: for GBM via exp(cumsum(log-returns)),
            # the correct log-space drift is (μ - 0.5σ²)dt
            # Without this, the simulation has systematic upward price bias
            daily_return = base_daily_return + drift_adjustment - 0.5 * daily_vol ** 2
            
            # Calculate degrees of freedom from kurtosis for Student-t distribution
            # Higher kurtosis = fatter tails = lower df
            # Note: pandas .kurtosis() returns EXCESS kurtosis (Normal=0), so use directly
            excess_kurtosis = max(0, risk.kurtosis)  # Already excess kurtosis from pandas
            df = max(4, 30 / (1 + excess_kurtosis * 0.5))  # Degrees of freedom (min 4 for finite variance)
            
            # Generate fat-tailed returns using Student-t distribution
            # This better captures real market behavior with extreme moves
            t_samples = rng.standard_t(df, (simulations, days))
            # Scale to unit variance: std_t(df) has variance df/(df-2),
            # so multiply by sqrt((df-2)/df) to normalize
            scale_factor = np.sqrt((df - 2) / df) if df > 2 else 1.0
            random_returns = (t_samples * scale_factor) * daily_vol + daily_return
            
            # Add jump diffusion for catalyst events (earnings, etc.)
            if has_catalyst:
                random_returns = self._add_jump_diffusion(random_returns, daily_vol, rng)
            
            # Calculate ending prices using geometric Brownian motion
            price_paths = current_price * np.exp(np.cumsum(random_returns, axis=1))
            ending_prices = price_paths[:, -1]
            
            # Convergence check: compare full run vs half run
            # If median differs by >2%, results may not be stable
            half_median = float(np.median(ending_prices[:simulations // 2]))
            full_median = float(np.median(ending_prices))
            convergence_pct = abs(full_median - half_median) / full_median * 100 if full_median > 0 else 0
            
            # Results
            result.simulations_run = simulations
            result.days_simulated = days
            result.mean_price = float(np.mean(ending_prices))
            result.median_price = full_median
            result.percentile_5 = float(np.percentile(ending_prices, 5))
            result.percentile_25 = float(np.percentile(ending_prices, 25))
            result.percentile_75 = float(np.percentile(ending_prices, 75))
            result.percentile_95 = float(np.percentile(ending_prices, 95))
            
            # Probabilities
            result.probability_profit = float((ending_prices > current_price).mean())
            result.probability_10pct_gain = float((ending_prices > current_price * 1.1).mean())
            result.probability_10pct_loss = float((ending_prices < current_price * 0.9).mean())
            
            # Expected return is the arithmetic mean across terminal outcomes.
            # The median remains available separately and is labelled as such.
            result.expected_return = (result.mean_price - current_price) / current_price * 100
            
            # VaR
            result.var_95 = (result.percentile_5 - current_price) / current_price * 100
            
        except Exception as e:
            logger.debug(f"Monte Carlo error: {e}")
        
        return result
    
    def _get_regime_drift(self, regime: MarketRegime) -> float:
        """Adjust drift based on market regime"""
        if regime is None:
            return 0.0
        
        drift_adjustments = {
            MarketRegime.BULL_TRENDING: 0.0003,    # +7.5% annualized
            MarketRegime.BULL_VOLATILE: 0.0002,   # +5% annualized
            MarketRegime.BEAR_TRENDING: -0.0004,  # -10% annualized
            MarketRegime.BEAR_VOLATILE: -0.0003,  # -7.5% annualized
            MarketRegime.SIDEWAYS: 0.0,
            MarketRegime.HIGH_VOLATILITY: -0.0001,
            MarketRegime.CRISIS: -0.0006          # -15% annualized
        }
        return drift_adjustments.get(regime, 0.0)
    
    def _add_jump_diffusion(self, returns: np.ndarray, daily_vol: float,
                            rng: Optional[np.random.Generator] = None) -> np.ndarray:
        """Add random jumps for catalyst events (Merton jump-diffusion model)"""
        simulations, days = returns.shape
        
        # Jump parameters: ~5% chance of jump per day with 3x volatility impact
        jump_prob = 0.05
        jump_size_mean = 0.0  # Symmetric jumps
        jump_size_std = daily_vol * 3.0
        
        # Generate jump mask and sizes
        rng = rng or np.random.default_rng(0)
        jump_mask = rng.random((simulations, days)) < jump_prob
        jump_sizes = rng.normal(jump_size_mean, jump_size_std, (simulations, days))
        
        # Apply jumps
        returns = returns + jump_mask * jump_sizes
        
        return returns


# =============================================================================
# PEER ANALYZER
# =============================================================================

class PeerAnalyzer:
    """Analyze stock relative to sector peers"""

    @staticmethod
    def _rank_score(metrics: Dict[str, float]) -> Tuple[Optional[float], int]:
        """Return an availability-normalized peer score.

        Missing/zero P/E or P/B used to hit denominator floors and receive the
        best possible valuation points. A row now needs at least two measured
        components and is scored only on those components.
        """
        components: List[float] = []
        roe = float(metrics.get('roe', 0.0) or 0.0)
        pe = float(metrics.get('pe', 0.0) or 0.0)
        pb = float(metrics.get('pb', 0.0) or 0.0)
        if roe != 0.0:
            components.append(max(-100.0, min(100.0, roe * 2.0)))
        if pe > 0.0:
            components.append(min(100.0, 100.0 / pe))
        if pb > 0.0:
            components.append(min(100.0, 50.0 / pb))
        if len(components) < 2:
            return None, len(components)
        return sum(components) / len(components), len(components)
    
    # Sector to ETF and representative peer mappings
    SECTOR_MAPPINGS = {
        'Technology': {
            'etf': 'XLK',
            'peers': ['AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL', 'CRM', 'ADBE', 'AMD', 'INTC', 'CSCO']
        },
        'Healthcare': {
            'etf': 'XLV', 
            'peers': ['UNH', 'JNJ', 'LLY', 'PFE', 'ABBV', 'MRK', 'TMO', 'ABT', 'DHR', 'BMY']
        },
        'Financials': {
            'etf': 'XLF',
            'peers': ['BRK-B', 'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'BLK', 'C']
        },
        'Consumer Discretionary': {
            'etf': 'XLY',
            'peers': ['AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'SBUX', 'LOW', 'TJX', 'BKNG', 'CMG']
        },
        'Consumer Staples': {
            'etf': 'XLP',
            'peers': ['PG', 'KO', 'PEP', 'COST', 'WMT', 'PM', 'MO', 'CL', 'MDLZ', 'KHC']
        },
        'Energy': {
            'etf': 'XLE',
            'peers': ['XOM', 'CVX', 'COP', 'SLB', 'EOG', 'MPC', 'PSX', 'VLO', 'OXY', 'PXD']
        },
        'Industrials': {
            'etf': 'XLI',
            'peers': ['CAT', 'RTX', 'HON', 'UNP', 'BA', 'GE', 'LMT', 'UPS', 'DE', 'MMM']
        },
        'Materials': {
            'etf': 'XLB',
            'peers': ['LIN', 'APD', 'SHW', 'ECL', 'NEM', 'FCX', 'NUE', 'DD', 'DOW', 'PPG']
        },
        'Real Estate': {
            'etf': 'XLRE',
            'peers': ['PLD', 'AMT', 'EQIX', 'CCI', 'PSA', 'O', 'SPG', 'WELL', 'DLR', 'AVB']
        },
        'Utilities': {
            'etf': 'XLU',
            'peers': ['NEE', 'DUK', 'SO', 'D', 'AEP', 'SRE', 'EXC', 'XEL', 'PEG', 'ED']
        },
        'Communication Services': {
            'etf': 'XLC',
            'peers': ['GOOGL', 'META', 'NFLX', 'DIS', 'CMCSA', 'VZ', 'T', 'TMUS', 'CHTR', 'EA']
        }
    }

    # Narrow industry overrides. When Yahoo's `industry` matches one of these,
    # we use a tighter peer set than the broad sector mapping above.
    INDUSTRY_MAPPINGS = {
        'Semiconductor Equipment & Materials': {
            'etf': 'SOXX',
            'peers': ['AMAT', 'KLAC', 'ASML', 'TER', 'ENTG', 'ACLS', 'MKSI', 'ONTO', 'AEIS', 'COHU']
        },
        'Semiconductors': {
            'etf': 'SOXX',
            'peers': ['NVDA', 'AMD', 'AVGO', 'INTC', 'QCOM', 'TXN', 'MU', 'MRVL', 'ADI', 'NXPI']
        },
        'Software—Infrastructure': {
            'etf': 'IGV',
            'peers': ['MSFT', 'ORCL', 'CRM', 'NOW', 'PLTR', 'SNOW', 'DDOG', 'MDB', 'NET', 'TEAM']
        },
        'Software—Application': {
            'etf': 'IGV',
            'peers': ['CRM', 'ADBE', 'INTU', 'NOW', 'WDAY', 'TEAM', 'HUBS', 'ZS', 'OKTA', 'PANW']
        },
        'Biotechnology': {
            'etf': 'IBB',
            'peers': ['AMGN', 'GILD', 'REGN', 'VRTX', 'BIIB', 'MRNA', 'BMRN', 'ALNY', 'INCY', 'SGEN']
        },
        'Banks—Diversified': {
            'etf': 'KBE',
            'peers': ['JPM', 'BAC', 'WFC', 'C', 'USB', 'PNC', 'TFC', 'MTB', 'KEY', 'CFG']
        },
    }
    
    def analyze(self, ticker: str, sector: str, fundamental: FundamentalData,
                industry: str = "") -> PeerComparison:
        """Analyze stock relative to sector / industry peers.

        If a narrow `industry` mapping exists, it takes precedence over the
        broad sector mapping so that e.g. LRCX is benchmarked against
        Semiconductor Equipment peers rather than the whole Technology sector.
        """
        result = PeerComparison()
        result.sector = sector

        mapping = {}
        if industry:
            mapping = self.INDUSTRY_MAPPINGS.get(industry, {})
            if mapping:
                # Reflect the narrower group in the displayed label
                result.sector = industry
        if not mapping:
            mapping = self.SECTOR_MAPPINGS.get(sector, {})
        if not mapping:
            result.overall_assessment = "Sector not mapped"
            return result
        
        result.sector_etf = mapping.get('etf', '')
        peers = mapping.get('peers', [])
        result.peer_tickers = [p for p in peers if p != ticker.upper()][:10]
        
        # Stock's own metrics
        result.pe_stock = fundamental.pe_ratio
        result.roe_stock = fundamental.roe
        result.pb_stock = fundamental.pb_ratio
        
        # Fetch peer metrics
        peer_pes = []
        peer_roes = []
        peer_pbs = []
        stock_metrics = {'pe': result.pe_stock, 'roe': result.roe_stock, 'pb': result.pb_stock}
        all_stocks = [(ticker.upper(), stock_metrics)]

        def _fetch_peer_metrics(peer: str) -> Optional[Tuple[str, Dict[str, float]]]:
            try:
                p_ticker = yf.Ticker(peer)
                p_info = p_ticker.info or {}

                pe = safe_get(p_info, 'trailingPE', 0)
                roe_raw = safe_get(p_info, 'returnOnEquity', 0)
                roe = roe_raw * 100 if roe_raw else 0
                pb = safe_get(p_info, 'priceToBook', 0)

                return peer, {'pe': pe, 'roe': roe, 'pb': pb}
            except Exception:
                return None
        
        try:
            peers_to_fetch = result.peer_tickers[:8]  # Limit to avoid too many API calls
            with ThreadPoolExecutor(max_workers=min(8, len(peers_to_fetch) or 1)) as executor:
                futures = [executor.submit(_fetch_peer_metrics, peer) for peer in peers_to_fetch]
                for future in as_completed(futures):
                    metrics_result = future.result()
                    if not metrics_result:
                        continue

                    peer, metrics = metrics_result
                    pe = metrics.get('pe', 0)
                    roe = metrics.get('roe', 0)
                    pb = metrics.get('pb', 0)

                    if pe > 0 and pe < 200:
                        peer_pes.append(pe)
                    if roe > 0:
                        peer_roes.append(roe)
                    if pb > 0:
                        peer_pbs.append(pb)

                    # For ranking
                    all_stocks.append((peer, metrics))
            
            # Calculate sector averages
            if peer_pes:
                result.pe_sector_avg = sum(peer_pes) / len(peer_pes)
                if result.pe_stock > 0 and result.pe_sector_avg > 0:
                    result.pe_vs_sector = ((result.pe_stock - result.pe_sector_avg) / result.pe_sector_avg) * 100
            
            if peer_roes:
                result.roe_sector_avg = sum(peer_roes) / len(peer_roes)
                if result.roe_stock > 0 and result.roe_sector_avg > 0:
                    result.roe_vs_sector = ((result.roe_stock - result.roe_sector_avg) / result.roe_sector_avg) * 100
            
            if peer_pbs:
                result.pb_sector_avg = sum(peer_pbs) / len(peer_pbs)
                if result.pb_stock > 0 and result.pb_sector_avg > 0:
                    result.pb_vs_sector = ((result.pb_stock - result.pb_sector_avg) / result.pb_sector_avg) * 100
            
            # Calculate sector ranking based on composite score
            # Higher ROE is better, lower P/E and P/B is better (for value)
            rankings = []
            for stock, metrics in all_stocks:
                score, _available = self._rank_score(metrics)
                if score is not None:
                    rankings.append((stock, score))
            
            rankings.sort(key=lambda x: x[1], reverse=True)
            result.sector_total = len(rankings)
            
            for i, (stock, score) in enumerate(rankings, 1):
                if stock == ticker.upper():
                    result.sector_rank = i
                    break
            
            # Overall assessment
            if result.sector_rank <= result.sector_total * 0.25:
                result.overall_assessment = "Top Quartile - Outperforming"
            elif result.sector_rank <= result.sector_total * 0.5:
                result.overall_assessment = "Above Average"
            elif result.sector_rank <= result.sector_total * 0.75:
                result.overall_assessment = "Below Average"
            else:
                result.overall_assessment = "Bottom Quartile - Underperforming"
                
        except Exception as e:
            logger.debug(f"Peer analysis error: {e}")
            result.overall_assessment = "Peer data unavailable"
        
        return result


# =============================================================================
# SIGNAL GENERATOR
# =============================================================================

class SignalGenerator:
    """Generate trading signals from multi-factor analysis with regime-adaptive weights"""
    
    def generate(self, technical: TechnicalIndicators, fundamental: FundamentalData,
                 risk: RiskMetrics, sentiment: SentimentData, insider: InsiderData,
                 market_intel: MarketIntelligence, valuation: ValuationMetrics,
                 regime: MarketRegime = None, monte_carlo: 'MonteCarloResults' = None,
                 current_price: float = 0) -> Tuple[Signal, float, ScoreBreakdown, SignalExplanation]:
        """Generate signal with confidence score, breakdown, and explanation"""
        
        # Initialize explanation tracking
        explanation = SignalExplanation()
        overrides_triggered = []
        supporting_factors = []
        risk_factors = []
        
        # Calculate component scores
        scores = self._calculate_component_scores(
            technical, fundamental, risk, sentiment, insider, market_intel, valuation
        )
        
        # Get regime-adaptive weights
        weights = self._get_regime_weights(regime)
        if getattr(fundamental, 'is_etf', False):
            # Fund: company fundamentals and quality are not applicable. Their
            # weight is removed and the rest renormalised by _calculate_composite.
            weights = dict(weights, fundamental=0.0, quality=0.0)
        
        # No "high-growth" reweighting. Cutting the valuation weight whenever
        # the provider's (quarterly, noisy) revenue growth exceeded 20% moved
        # weight away from valuation exactly when it read "overvalued" (a bank
        # printing +30% YoY lost two thirds of its valuation weight). An
        # unreliable growth DCF is handled by the plausibility band instead.

        # Calculate composite score with adaptive weights
        composite = self._calculate_composite(scores, weights)
        explanation.composite_score = composite
        
        # Count signal confirmations for reliability check
        bullish_count, bearish_count = self._count_confirmations(technical, fundamental, sentiment)
        
        # Generate signal from composite with confirmation requirements
        override_codes: List[str] = []
        signal, confidence = self._determine_signal(
            composite, technical, fundamental, risk, bullish_count, bearish_count,
            codes=override_codes,
        )
        original_signal = signal  # Track for explanation
        
        # Build primary reason from the SAME band table the resolver uses.
        base_signal = Signal.STRONG_SELL
        _band_text = {
            "STRONG_BUY": ("≥75 STRONG_BUY threshold", "meets STRONG_BUY threshold"),
            "BUY": ("≥65 BUY threshold", "meets BUY threshold"),
            "ACCUMULATE": ("≥55 ACCUMULATE threshold", "meets ACCUMULATE threshold"),
            "HOLD": ("45-55 HOLD range", "in HOLD range"),
            "REDUCE": ("35-45 REDUCE range", "triggers REDUCE"),
            "SELL": ("25-35 SELL range", "triggers SELL"),
        }
        for _floor, _band in Config.SIGNAL_CUTOFFS:
            if composite >= _floor:
                base_signal = Signal[_band]
                explanation.score_threshold, _verb = _band_text[_band]
                explanation.primary_reason = f"Composite score {composite:.0f} {_verb}"
                break
        else:
            explanation.score_threshold = "<25 STRONG_SELL range"
            explanation.primary_reason = f"Composite score {composite:.0f} below the SELL range"

        # If internal guardrails downgraded the score-implied signal, explain that directly
        if signal != base_signal and not overrides_triggered:
            if risk.risk_grade in ['D', 'F'] and base_signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
                explanation.primary_reason = (
                    f"Composite score {composite:.0f} suggests {base_signal.value}, "
                    f"but Risk Grade {risk.risk_grade} guardrail downgrades to {signal.value}"
                )
            else:
                explanation.primary_reason = (
                    f"Composite score {composite:.0f} suggests {base_signal.value}, "
                    f"but confirmation/risk guardrails set {signal.value}"
                )
        
        # Track supporting factors
        if scores['technical'] >= 60:
            supporting_factors.append(f"Technical score {scores['technical']:.0f} (bullish)")
        if scores['fundamental'] >= 60:
            supporting_factors.append(f"Fundamental score {scores['fundamental']:.0f} (strong)")
        if scores['sentiment'] >= 60:
            supporting_factors.append(f"Sentiment score {scores['sentiment']:.0f} (positive)")
        if bullish_count >= 4:
            supporting_factors.append(f"{bullish_count} bullish confirmations")
        
        # Track risk factors
        if scores['technical'] < 40:
            risk_factors.append(f"Technical score {scores['technical']:.0f} (bearish)")
        if scores['risk'] < 40:
            # Name what drives the risk, not the inverted 0-100 score.
            _drv = [d for d in (getattr(risk, 'grade_drivers', []) or []) if d.get("points")]
            _drv.sort(key=lambda d: -int(d["points"]))
            risk_factors.append(
                f"Risk grade {risk.risk_grade}: " + (", ".join(
                    f"{str(d['label']).lower()} {str(d['detail']).split(' (')[0]}" for d in _drv[:3])
                    or "elevated volatility and drawdown"))
        if bearish_count >= 3:
            risk_factors.append(f"{bearish_count} bearish confirmations")
        
        # ═══════════════════════════════════════════════════════════════════════
        # POST-SIGNAL HARD OVERRIDES (NON-REVERSIBLE - FIDUCIARY COMPLIANCE)
        # ═══════════════════════════════════════════════════════════════════════
        
        # 1. RISK GRADE ENFORCEMENT (single source of truth = _determine_signal)
        #    _determine_signal already applies the graduated guardrail:
        #      F → HOLD, D → trim ONE notch (STRONG_BUY→BUY, BUY→ACCUMULATE).
        #    We keep ONLY a defensive F backstop here in case the resolver is
        #    ever bypassed. It is idempotent (no-op once already HOLD) and must
        #    NOT re-trim D — doing so would double-downgrade a D-grade name
        #    (e.g. STRONG_BUY → BUY → ACCUMULATE).
        if risk.risk_grade == 'F' and signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
            logger.warning(f"⛔ RISK OVERRIDE: {signal.value} → HOLD (Risk Grade F)")
            overrides_triggered.append("Risk Grade F override → HOLD (fiduciary rule)")
            override_codes.append("RISK_GRADE_F")
            signal = Signal.HOLD
            confidence = min(confidence, 45)  # Cap confidence
        
        # Insider selling is resolved once, after the ticker/currency context is
        # available, by the shared Decision resolver. Keeping a second threshold
        # here previously made identical facts produce different decisions.

        # Monte Carlo shape is diagnostic until the percentile pair and cutoff
        # are calibrated against prospective outcomes. It must not create the
        # non-monotonic 64=ACCUMULATE / 66=HOLD boundary that existed when the
        # veto applied only to BUY tiers.
        if monte_carlo and current_price > 0 and monte_carlo.percentile_5 > 0 and monte_carlo.percentile_75 > 0:
            mc_upside = (monte_carlo.percentile_75 - current_price) / current_price * 100
            mc_downside = (current_price - monte_carlo.percentile_5) / current_price * 100
            if mc_downside > 0 and mc_upside / mc_downside < 0.5:
                risk_factors.append(
                    f"Monte Carlo tail shape is unfavorable ({mc_upside:.1f}% 75th-percentile "
                    f"upside vs {mc_downside:.1f}% 5th-percentile downside); diagnostic only"
                )

        # 4. PRICE-TARGET DISCIPLINE - graduated, NOT a blanket HOLD gate.
        #    The base target is a 12-month blend (50% analyst / 30% DCF / 20%
        #    technical), so it lags price in a strong tape. A negative target
        #    (below price) is a real red flag → HOLD. A modest-but-positive
        #    upside below the 5% hurdle only TRIMS conviction one notch
        #    (STRONG_BUY→BUY, BUY→ACCUMULATE) so a strong composite isn't
        #    blanket-forced to HOLD. ACCUMULATE stays (gradual build is fine).
        if current_price > 0 and signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
            target_base = getattr(valuation, 'target_base', 0.0) or 0.0
            if target_base > 0:
                target_upside = (target_base - current_price) / current_price * 100
                if target_upside < 0:
                    logger.warning(f"⛔ TARGET OVERRIDE: base target {target_upside:.1f}% (below price)")
                    overrides_triggered.append(
                        f"Base target {target_upside:.1f}% below current price → HOLD"
                    )
                    override_codes.append("TARGET_BELOW_ENTRY")
                    signal = Signal.HOLD
                    confidence = max(35, confidence - 15)
                elif target_upside < _T.RESEARCH.TARGET_UPSIDE_THIN_PCT:
                    if signal == Signal.STRONG_BUY:
                        overrides_triggered.append(
                            f"Base target upside {target_upside:.1f}% (<{_T.RESEARCH.TARGET_UPSIDE_THIN_PCT:g}%) trims STRONG BUY → BUY"
                        )
                        override_codes.append("TARGET_UPSIDE_THIN")
                        signal = Signal.BUY
                        confidence = min(confidence, 60)
                    elif signal == Signal.BUY:
                        overrides_triggered.append(
                            f"Base target upside {target_upside:.1f}% (<{_T.RESEARCH.TARGET_UPSIDE_THIN_PCT:g}%) trims BUY → ACCUMULATE"
                        )
                        override_codes.append("TARGET_UPSIDE_THIN")
                        signal = Signal.ACCUMULATE
                        confidence = min(confidence, 55)
                    # ACCUMULATE stays ACCUMULATE — modest upside still supports a gradual build.

            analyst_target = getattr(market_intel, 'target_mean', 0.0) or 0.0
            if analyst_target > 0 and signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
                analyst_upside = (analyst_target - current_price) / current_price * 100
                if analyst_upside < _T.RESEARCH.ANALYST_DOWNSIDE_LIMIT_PCT:
                    logger.warning(f"⛔ ANALYST TARGET OVERRIDE: consensus upside {analyst_upside:.1f}%")
                    overrides_triggered.append(
                        f"Analyst consensus target implies {analyst_upside:.1f}% downside → HOLD"
                    )
                    override_codes.append("ANALYST_CONSENSUS_DOWNSIDE")
                    signal = Signal.HOLD
                    confidence = max(35, confidence - 15)
        
        # Finalize explanation
        explanation.signal = signal.value
        explanation.overrides_triggered = overrides_triggered
        explanation.override_codes = list(dict.fromkeys(override_codes))
        explanation.supporting_factors = supporting_factors
        explanation.risk_factors = risk_factors
        
        # If signal was changed by override, update primary reason
        if signal != original_signal and overrides_triggered:
            explanation.primary_reason = overrides_triggered[0]
        
        # Data coverage: how many component legs rest on measured inputs. No
        # "95% confidence interval" is published: the dispersion of five
        # heuristic sub-scores is not a sampling error of the composite.
        score_values = [scores['technical'], scores['fundamental'], scores['valuation'],
                       scores['quality'], scores['sentiment']]
        data_sources = sum(1 for s in score_values if s != 50.0)  # Non-default scores
        if data_sources >= 5:
            data_note = "HIGH - All data sources available"
        elif data_sources >= 3:
            data_note = "MODERATE - Some data sources missing"
        else:
            data_note = "LOW - Limited data sources"

        breakdown = ScoreBreakdown(
            technical_score=scores['technical'],
            fundamental_score=scores['fundamental'],
            valuation_score=scores['valuation'],
            quality_score=scores['quality'],
            sentiment_score=scores['sentiment'],
            risk_score=scores['risk'],
            composite_score=composite,
            data_quality_note=data_note,
            weights=weights
        )
        
        return signal, confidence, breakdown, explanation
    
    def _get_regime_weights(self, regime: MarketRegime) -> Dict[str, float]:
        """Return normalized alpha weights; risk is assessed separately."""
        if regime is None or regime == MarketRegime.SIDEWAYS:
            raw = Config.REGIME_WEIGHTS['SIDEWAYS']
        elif regime in [MarketRegime.BULL_TRENDING, MarketRegime.BULL_VOLATILE]:
            raw = Config.REGIME_WEIGHTS['BULL']
        elif regime in [MarketRegime.BEAR_TRENDING, MarketRegime.BEAR_VOLATILE, MarketRegime.CRISIS]:
            raw = Config.REGIME_WEIGHTS['BEAR']
        elif regime == MarketRegime.HIGH_VOLATILITY:
            raw = Config.REGIME_WEIGHTS['HIGH_VOL']
        else:
            raw = Config.REGIME_WEIGHTS['SIDEWAYS']
        total = sum(raw.values())
        return {key: value / total for key, value in raw.items()}
    
    def _count_confirmations(self, technical: TechnicalIndicators,
                              fundamental: FundamentalData,
                              sentiment: SentimentData) -> Tuple[int, int]:
        """Count bullish and bearish confirmations for signal reliability"""
        bullish = 0
        bearish = 0
        
        # Technical confirmations
        if technical.macd_signal == "BULLISH": bullish += 1
        elif technical.macd_signal == "BEARISH": bearish += 1
        
        # Oversold RSI is a mean-reversion argument, not a confirmation of a
        # trend-structure buy; only a stretched reading votes (bearish).
        if technical.rsi > 70: bearish += 1
        
        if technical.price_vs_sma200 > 0: bullish += 1
        elif technical.price_vs_sma200 < 0: bearish += 1
        
        if technical.golden_cross: bullish += 1
        if technical.death_cross: bearish += 1
        
        if technical.obv_trend == "BULLISH": bullish += 1
        elif technical.obv_trend == "BEARISH": bearish += 1
        
        # Fundamental confirmations. An unreported figure is stored as 0.0 and
        # must not vote (0.0 debt/equity would read as a debt-free balance sheet).
        if fundamental.has('roe') and fundamental.roe > 15: bullish += 1
        if fundamental.has('debt_to_equity') and fundamental.debt_to_equity < 1: bullish += 1
        # Only vote when the F-score rests on enough reported inputs; an
        # unreported balance sheet must not read as "weak fundamentals".
        if piotroski_is_reportable(fundamental):
            if fundamental.piotroski_f_score >= 7: bullish += 1
            if fundamental.piotroski_f_score <= 3: bearish += 1
        
        # Sentiment confirmations
        sentiment_value = sentiment.company_sentiment_score()
        if sentiment_value > 0.15: bullish += 1
        elif sentiment_value < -0.15: bearish += 1
        
        return bullish, bearish
    
    def _calculate_component_scores(self, technical: TechnicalIndicators, 
                                    fundamental: FundamentalData, risk: RiskMetrics,
                                    sentiment: SentimentData, insider: InsiderData,
                                    market_intel: MarketIntelligence, 
                                    valuation: ValuationMetrics) -> Dict[str, float]:
        """Calculate individual component scores (0-100)"""
        scores = {}
        
        # Technical Score
        tech_score = 50
        
        # RSI contribution. One thesis per score: this leg rewards trend
        # structure (ADX trend, MA alignment below), so it no longer ALSO pays
        # a mean-reversion bonus for oversold RSI -- a score that rewards a
        # condition and its opposite measures nothing. Stretched readings are
        # still a timing penalty.
        if technical.rsi > 70:
            tech_score -= 15
        elif technical.rsi > 60:
            tech_score -= 8
        
        # MACD contribution
        if technical.macd_signal == "BULLISH":
            tech_score += 10
        elif technical.macd_signal == "BEARISH":
            tech_score -= 10
        
        # Trend contribution
        if technical.trend_direction == "UP" and technical.adx > 25:
            tech_score += 12
        elif technical.trend_direction == "DOWN" and technical.adx > 25:
            tech_score -= 12
        
        # MA alignment
        if technical.price_vs_sma200 > 0 and technical.price_vs_sma50 > 0:
            tech_score += 8
        elif technical.price_vs_sma200 < 0 and technical.price_vs_sma50 < 0:
            tech_score -= 8
        
        # Volume confirmation
        if technical.volume_confirmation and technical.obv_trend == "BULLISH":
            tech_score += 5
        
        scores['technical'] = max(0, min(100, tech_score))
        
        # Fundamental Score
        if hasattr(fundamental, 'is_etf') and fundamental.is_etf:
            # Company fundamentals do not apply to a fund. Neutral here and
            # zero-weighted in the composite; a constant 60 (+15 for yield)
            # used to be counted as if it were a measurement.
            scores['fundamental'] = 50.0
        else:
            fund_score = 50
            
            # Only inputs the provider reported move the score; a missing
            # figure is stored as 0.0 and must not read as a measured one.
            # ROE contribution
            if not fundamental.has('roe'):
                pass
            elif fundamental.roe > 20:
                fund_score += 15
            elif fundamental.roe > 15:
                fund_score += 10
            elif fundamental.roe > 10:
                fund_score += 5
            elif fundamental.roe < 5:
                fund_score -= 10
            
            # Growth contribution
            if not fundamental.has('revenue_growth'):
                pass
            elif fundamental.revenue_growth_yoy > 20:
                fund_score += 10
            elif fundamental.revenue_growth_yoy > 10:
                fund_score += 5
            elif fundamental.revenue_growth_yoy < 0:
                fund_score -= 10
            
            # Margin contribution
            if not fundamental.has('net_margin'):
                pass
            elif fundamental.net_margin > 15:
                fund_score += 8
            elif fundamental.net_margin > 10:
                fund_score += 5
            elif fundamental.net_margin < 0:
                fund_score -= 15
            
            # Financial health
            if not (fundamental.has('debt_to_equity') and fundamental.has('current_ratio')):
                pass
            elif fundamental.debt_to_equity < 0.5 and fundamental.current_ratio > 2:
                fund_score += 8
            elif fundamental.debt_to_equity > 2:
                fund_score -= 10
            
            scores['fundamental'] = max(0, min(100, fund_score))
        
        # Valuation Score
        val_score = valuation.valuation_score

        # No analyst-upside substitute for a measured valuation. The consensus
        # target already carries half the target blend, the analyst rating
        # already moves sentiment, and a broken growth DCF is excluded (and
        # scored neutral) upstream; replacing a measured valuation with it
        # counted the same opinion a third time.
        
        # Every component is clamped to the 0-100 scale the weights assume;
        # valuation / quality come from other analyzers and were the only two
        # legs that could leave the range and silently over-weight themselves.
        scores['valuation'] = max(0, min(100, float(val_score or 0.0)))

        # Quality Score
        scores['quality'] = max(0, min(100, float(fundamental.quality_score or 0.0)))
        
        # Sentiment Score
        sent_score = 50
        
        company_sentiment = sentiment.company_sentiment_score()
        if company_sentiment > 0.2:
            sent_score += 20
        elif company_sentiment > 0.1:
            sent_score += 10
        elif company_sentiment < -0.2:
            sent_score -= 20
        elif company_sentiment < -0.1:
            sent_score -= 10
        
        # Sentiment trend
        if sentiment.sentiment_trend == "IMPROVING":
            sent_score += 10
        elif sentiment.sentiment_trend == "DETERIORATING":
            sent_score -= 10
        
        # No analyst-rating term: sell-side opinion already carries half the
        # 12-month target and has its own downside veto. Counting it a third
        # time here inflated every consensus-Buy name.

        # Insider activity
        if insider.insider_sentiment == "BULLISH":
            sent_score += 10
        elif insider.insider_sentiment == "BEARISH":
            sent_score -= 15  # Stronger penalty for insider selling
        
        scores['sentiment'] = max(0, min(100, sent_score))
        
        # Risk Score (higher = better/lower risk)
        risk_score = 100 - (risk.overall_risk_score * 10)
        
        # Sharpe bonus
        if risk.sharpe_ratio > 1.5:
            risk_score += 10
        elif risk.sharpe_ratio > 1.0:
            risk_score += 5
        elif risk.sharpe_ratio < 0:
            risk_score -= 15
        
        # Drawdown penalty
        if risk.max_drawdown < -0.3:
            risk_score -= 10
        
        scores['risk'] = max(20, min(100, risk_score))
        
        return scores
    
    def _calculate_composite(self, scores: Dict[str, float], 
                              weights: Dict[str, float] = None) -> float:
        """Alpha-only heuristic composite; risk remains a separate sizing input."""
        if weights is None:
            weights = {
                'technical': Config.WEIGHT_TECHNICAL,
                'fundamental': Config.WEIGHT_FUNDAMENTAL,
                'valuation': Config.WEIGHT_VALUATION,
                'quality': Config.WEIGHT_QUALITY,
                'sentiment': Config.WEIGHT_SENTIMENT,
            }
        alpha = ('technical', 'fundamental', 'valuation', 'quality', 'sentiment')
        total = sum(max(0.0, weights.get(k, 0.0)) for k in alpha)
        composite = (sum(scores[k] * max(0.0, weights.get(k, 0.0)) for k in alpha)
                     / total) if total > 0 else 50.0
        return max(0, min(100, composite))
    
    def _determine_signal(self, composite: float, technical: TechnicalIndicators,
                          fundamental: FundamentalData, risk: RiskMetrics,
                          bullish_count: int = 0, bearish_count: int = 0,
                          codes: Optional[List[str]] = None) -> Tuple[Signal, float]:
        """Determine trading signal with multi-factor confirmation requirements.

        ``codes`` (optional) receives the stable code of every downgrade applied.
        """
        codes = codes if codes is not None else []
        
        # Base confidence from composite
        confidence = composite
        
        # Signal determination
        signal = Signal.STRONG_SELL
        for cutoff, signal_name in Config.SIGNAL_CUTOFFS:
            if composite >= cutoff:
                signal = Signal[signal_name]
                break
        
        # Multi-factor confirmation requirements
        # STRONG signals require 3+ confirmations
        if signal == Signal.STRONG_BUY and bullish_count < _T.RESEARCH.STRONG_CONFIRMATIONS:
            signal = Signal.BUY
            codes.append("STRONG_BUY_UNCONFIRMED")
            confidence = max(confidence - 8, 55)
        elif signal == Signal.STRONG_SELL and bearish_count < _T.RESEARCH.STRONG_CONFIRMATIONS:
            signal = Signal.SELL
            confidence = max(confidence - 8, 25)
        
        # BUY/SELL signals get confidence boost with strong confirmations
        if signal == Signal.BUY and bullish_count >= 4:
            confidence = min(confidence + 8, 85)
        elif signal == Signal.SELL and bearish_count >= 4:
            confidence = min(confidence + 8, 40)
        
        # Safety overrides
        if technical.rsi > _T.RESEARCH.RSI_EXHAUSTION and technical.macd_signal == "BEARISH":
            if signal in [Signal.STRONG_BUY, Signal.BUY]:
                signal = Signal.HOLD
                codes.append("RSI_EXHAUSTION")
                confidence = min(confidence, 60)
        
        if technical.rsi < _T.RESEARCH.RSI_CAPITULATION and technical.macd_signal == "BULLISH":
            if signal in [Signal.SELL, Signal.STRONG_SELL]:
                signal = Signal.HOLD
                confidence = min(confidence, 60)
        
        # Quality bonus for conviction
        if fundamental.quality_score > 70 and signal in [Signal.STRONG_BUY, Signal.BUY]:
            confidence = min(95, confidence + 5)
        
        # High risk penalty. Conviction only: the grade itself (D/F below)
        # moves the signal, so a D-grade STRONG BUY is trimmed once, not twice.
        if risk.overall_risk_score >= 8:
            confidence = max(30, confidence - 10)
        
        # D/F risk grade adjusts conviction (high volatility/drawdown)
        #   F (risk score 9-10) is genuinely dangerous → no new-money conviction.
        #   D (risk score 8) is elevated, not disqualifying → trim ONE notch so
        #   strong names stay actionable (smaller size) instead of all → HOLD.
        if risk.risk_grade == 'F':
            if signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
                logger.info("Signal downgraded to HOLD due to F risk grade")
                codes.append("RISK_GRADE_F")
                signal = Signal.HOLD
                confidence = max(25, confidence - 15)
        elif risk.risk_grade == 'D':
            if signal == Signal.STRONG_BUY:
                signal = Signal.BUY
                codes.append("RISK_GRADE_D_CAP")
                confidence = max(45, confidence - 8)
            elif signal == Signal.BUY:
                signal = Signal.ACCUMULATE
                codes.append("RISK_GRADE_D_CAP")
                confidence = max(40, confidence - 8)
            # ACCUMULATE stays ACCUMULATE — a D grade means smaller size, not no entry.
        
        return signal, confidence


# =============================================================================
# PORTFOLIO EXPOSURE MANAGER
# =============================================================================
#
# [F02] This class used to be named `PortfolioRiskManager`, which silently
# rebound the `modules.portfolio_risk_manager.PortfolioRiskManager` imported at
# the top of this module: the import ran first, this `class` statement ran
# second and overwrote the global name. The analyzer therefore always used this
# implementation while `HAS_PORTFOLIO_RISK` reported on a different one, and
# the imported class — whose `check_position_limits` takes a completely
# different signature — was unreachable dead code. The distinct name makes the
# ownership explicit.

class PortfolioExposureManager:
    """
    Portfolio-level risk aggregation with correlation-adjusted VaR.
    
    CRITICAL FIX: Previously, position sizing was done independently per stock.
    This class adds portfolio-level checks:
    - Correlation-adjusted VaR
    - Sector concentration limits
    - Factor exposure limits
    - Position correlation clustering
    
    Professional Standard Reference:
    - Max sector concentration: 25%
    - Max factor exposure (beta): 1.3
    - Max correlated positions: 3 stocks with r > 0.7
    """
    
    # Limits based on institutional risk management standards
    MAX_SECTOR_PCT = 0.25         # 25% max in any sector
    MAX_CORRELATED_POSITIONS = 3  # Max 3 stocks with correlation > 0.7
    MAX_PORTFOLIO_BETA = 1.3
    CORRELATION_THRESHOLD = 0.70
    
    def __init__(self, api_client=None):
        self.api = api_client
        self._returns_cache = {}
    
    @staticmethod
    def _position_weight(position: Dict, portfolio_value: float) -> float:
        """Weight of one position, from an explicit weight or from dollars.

        [F02] Callers supply dollar ``value`` fields. The old code read a
        ``weight`` key that this schema never carries, so ``.get('weight', 0)``
        made every sector exposure exactly zero and no limit could ever bind.
        """
        if not math.isfinite(portfolio_value) or portfolio_value <= 0:
            raise ValueError("Account value must be positive and finite")
        if 'value' in position:
            weight = float(position['value']) / portfolio_value
        elif 'weight' in position:
            weight = float(position['weight'])
        else:
            raise ValueError("Holdings require a value or weight")
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Long-only holdings require finite nonnegative exposure")
        return weight

    def check_position_limits(self, new_position: Dict,
                              existing_positions: List[Dict],
                              portfolio_value: float) -> Dict:
        """
        Check whether admitting ``new_position`` would breach portfolio limits.

        Args:
            new_position: Proposed order — 'ticker', 'sector' and either
                'value' (dollars) or 'weight' (fraction of the account).
            existing_positions: Current holdings in the same schema. An empty
                list means holdings are UNKNOWN, not that the account is empty.
            portfolio_value: Account value the weights are measured against.

        Returns:
            Dict with:
              approved        - False when the order cannot be admitted as proposed
              adjustments     - {'max_position_value': dollars} when a smaller
                                order would fit; absent when nothing fits
              sector_exposure - post-trade sector weights, INCLUDING this order
              holdings_known  - False when no account snapshot was supplied, so
                                the verdict is hypothetical rather than verified
        """
        result = {
            'approved': True,
            'warnings': [],
            'adjustments': {},
            'sector_exposure': {},
            'correlation_risk': 0.0,
            'holdings_known': bool(existing_positions),
        }

        new_ticker = new_position.get('ticker', 'UNKNOWN')
        new_sector = new_position.get('sector') or 'Unknown'
        try:
            new_value = self._position_weight(new_position, portfolio_value) * portfolio_value
            held_weights = [self._position_weight(pos, portfolio_value) for pos in existing_positions]
        except (TypeError, ValueError, OverflowError) as exc:
            result['approved'] = False
            result['warnings'].append(f"Exposure check unavailable: {exc}")
            return result

        # 1. Sector concentration — measured AFTER the proposed trade, so an
        #    order large enough to breach the cap on its own is caught even
        #    against an empty book.
        sector_weights: Dict[str, float] = {}
        for pos, weight in zip(existing_positions, held_weights):
            sector = pos.get('sector') or 'Unknown'
            sector_weights[sector] = (
                sector_weights.get(sector, 0.0)
                + weight
            )

        held_sector_weight = sector_weights.get(new_sector, 0.0)
        proposed_weight = (
            new_value / portfolio_value if portfolio_value > 0 else 0.0
        )
        sector_weights[new_sector] = held_sector_weight + proposed_weight
        result['sector_exposure'] = sector_weights

        if sector_weights[new_sector] > self.MAX_SECTOR_PCT:
            headroom_weight = max(0.0, self.MAX_SECTOR_PCT - held_sector_weight)
            headroom_value = headroom_weight * portfolio_value
            result['warnings'].append(
                f"⚠️ SECTOR LIMIT: {new_sector} would reach "
                f"{sector_weights[new_sector]:.0%} "
                f"(max {self.MAX_SECTOR_PCT:.0%})"
            )
            # The constraint binds: refuse the order at the proposed size and
            # publish the largest size that still fits (possibly zero).
            result['approved'] = False
            if headroom_value > 0:
                result['adjustments']['max_position_value'] = headroom_value

        available_value = max(0.0, 1.0 - sum(held_weights)) * portfolio_value
        if new_value > available_value:
            result['approved'] = False
            result['warnings'].append("Proposed allocation exceeds unallocated account capital")
            cap = min(available_value, result['adjustments'].get('max_position_value', available_value))
            if cap > 0 and held_sector_weight < self.MAX_SECTOR_PCT:
                result['adjustments']['max_position_value'] = cap
            else:
                result['adjustments'].pop('max_position_value', None)

        # All holdings count toward exposure. Bound network work without
        # pretending a truncated portfolio passed the correlation check.
        if len(existing_positions) > 15:
            result['approved'] = False
            result['correlation_checked'] = False
            result['adjustments'].clear()
            result['warnings'].append("Correlation review required for portfolios above 15 holdings")
            return result

        # 2. Correlation clustering.
        correlated_count = self._count_correlated_positions(
            new_ticker, [p.get('ticker', '') for p in existing_positions]
        )

        if correlated_count >= self.MAX_CORRELATED_POSITIONS:
            result['warnings'].append(
                f"⚠️ CORRELATION LIMIT: {correlated_count} existing positions "
                f"highly correlated with {new_ticker}"
            )
            result['approved'] = False
            # Nothing fits on correlation grounds — drop any sector headroom.
            result['adjustments'].pop('max_position_value', None)

        result['correlation_risk'] = correlated_count / max(1, len(existing_positions))

        return result
    
    def calculate_portfolio_var(self, positions: List[Dict], 
                                 confidence: float = 0.95) -> Dict:
        """
        Calculate correlation-adjusted portfolio VaR.
        
        Unlike per-stock VaR, this accounts for diversification benefits
        (negative correlations reduce risk) and concentration risks
        (positive correlations increase risk).
        
        Args:
            positions: List of dicts with 'ticker', 'weight', 'daily_volatility'
            confidence: VaR confidence level (default 95%)
            
        Returns:
            Dict with 'portfolio_var', 'diversification_benefit', 'undiversified_var'
        """
        from scipy.stats import norm
        
        if not positions:
            return {'portfolio_var': 0, 'diversification_benefit': 0, 'undiversified_var': 0}
        
        n = len(positions)
        weights = np.array([p.get('weight', 1/n) for p in positions])
        vols = np.array([p.get('daily_volatility', 0.02) for p in positions])
        
        # Undiversified VaR (assumes perfect correlation)
        undiversified_var = abs(norm.ppf(1 - confidence)) * np.sum(weights * vols)
        
        # Get correlation matrix
        try:
            corr_matrix = self._get_correlation_matrix([p['ticker'] for p in positions])
        except Exception:
            # Fallback: assume moderate correlation of 0.3
            corr_matrix = np.full((n, n), 0.3)
            np.fill_diagonal(corr_matrix, 1.0)
        
        # Build covariance matrix
        cov_matrix = np.outer(vols, vols) * corr_matrix
        
        # Portfolio variance
        portfolio_variance = weights @ cov_matrix @ weights
        portfolio_vol = np.sqrt(portfolio_variance)
        
        # Diversified VaR
        portfolio_var = abs(norm.ppf(1 - confidence)) * portfolio_vol
        
        # Diversification benefit
        diversification_benefit = undiversified_var - portfolio_var
        
        return {
            'portfolio_var': portfolio_var,
            'undiversified_var': undiversified_var,
            'diversification_benefit': diversification_benefit,
            'diversification_ratio': diversification_benefit / undiversified_var if undiversified_var > 0 else 0
        }
    
    def _count_correlated_positions(self, ticker: str, existing_tickers: List[str]) -> int:
        """Count how many existing positions are highly correlated with ticker."""
        count = 0
        for existing in existing_tickers:
            try:
                corr = self._get_pairwise_correlation(ticker, existing)
                if corr > self.CORRELATION_THRESHOLD:
                    count += 1
            except Exception:
                # If we can't compute correlation, assume moderate correlation
                pass
        return count
    
    def _get_pairwise_correlation(self, ticker1: str, ticker2: str) -> float:
        """Get correlation between two tickers (cached)."""
        cache_key = tuple(sorted([ticker1, ticker2]))
        if cache_key in self._returns_cache:
            return self._returns_cache[cache_key]
        
        try:
            # Fetch historical returns
            if self.api and hasattr(self.api, 'get_historical'):
                data1 = self.api.get_historical(ticker1, '1y')
                data2 = self.api.get_historical(ticker2, '1y')
            else:
                data1 = yf.Ticker(ticker1).history(period='1y')
                data2 = yf.Ticker(ticker2).history(period='1y')
            
            returns1 = data1['Close'].pct_change().dropna()
            returns2 = data2['Close'].pct_change().dropna()
            
            # Align indexes
            combined = pd.concat([returns1, returns2], axis=1, join='inner')
            if len(combined) < 50:
                return 0.3  # Default moderate correlation
            
            corr = combined.iloc[:, 0].corr(combined.iloc[:, 1])
            self._returns_cache[cache_key] = corr
            return corr
        except Exception:
            return 0.3  # Default moderate correlation
    
    def _get_correlation_matrix(self, tickers: List[str]) -> np.ndarray:
        """Build correlation matrix for a list of tickers."""
        n = len(tickers)
        corr_matrix = np.eye(n)
        
        for i in range(n):
            for j in range(i + 1, n):
                corr = self._get_pairwise_correlation(tickers[i], tickers[j])
                corr_matrix[i, j] = corr
                corr_matrix[j, i] = corr
        
        return corr_matrix


# =============================================================================
# POSITION SIZING ENGINE
# =============================================================================

class PositionSizer:
    """Smart position sizing with dynamic win probability and risk management"""

    # Base risk percentage by signal (the Research engine's own sizing model).
    BASE_RISK = {
        Signal.STRONG_BUY: 0.04,
        Signal.BUY: 0.03,
        Signal.ACCUMULATE: 0.02,
        Signal.SELL: 0.02,
        Signal.STRONG_SELL: 0.025,
    }

    def tier_ratio(self, signal: "Signal", reference: "Signal") -> float:
        """Size of ``signal``'s tier relative to ``reference``'s (Q4 fold re-size)."""
        ref = self.BASE_RISK.get(reference, 0.0)
        return self.BASE_RISK.get(signal, 0.0) / ref if ref else 1.0

    
    def calculate(self, signal: Signal, entry: float, stop_loss: float,
                  confidence: float, risk: RiskMetrics,
                  account_size: float = 100000,
                  monte_carlo: MonteCarloResults = None,
                  technical: TechnicalIndicators = None,
                  target: float = 0.0) -> PositionSizing:
        """Calculate the position size from a risk budget.

        Sizing is risk-budget based (stop distance, volatility, drawdown and
        liquidity adjustments). No Kelly figure is published: it would rest on
        an uncalibrated win probability. ``monte_carlo``, ``technical``,
        and ``target`` are accepted for call compatibility.
        """
        result = PositionSizing()
        
        if signal in [Signal.HOLD, Signal.NO_POSITION, Signal.REDUCE]:
            result.recommendation = "No new position recommended for this signal"
            return result
        
        if entry <= 0 or stop_loss <= 0:
            result.recommendation = "Invalid price levels"
            return result
        
        # Risk per share
        risk_per_share = abs(entry - stop_loss)
        if risk_per_share < 0.01:
            result.recommendation = "Entry and stop too close"
            return result
        
        # Base risk percentage by signal
        base_risk = self.BASE_RISK.get(signal, 0.02)
        
        # Adjust for confidence
        confidence_adj = 0.7 + (confidence / 100) * 0.6  # 0.7 to 1.3
        
        # Adjust for volatility
        vol_adj = max(0.5, min(1.5, 0.25 / max(0.1, risk.annualized_volatility)))
        
        # Adjust for liquidity
        liq_adj = min(1.0, risk.liquidity_score / 8)
        
        # Final risk percentage
        adjusted_risk = base_risk * confidence_adj * vol_adj * liq_adj
        adjusted_risk = max(0.005, min(0.06, adjusted_risk))
        
        # Apply risk grade multiplier - reduce position size for risky stocks.
        # [F7] One table for both engines (SIZING.RISK_GRADE_SIZE_MULTIPLIERS):
        # grade D at half size everywhere, grade F takes no new money.
        from engine.ladder import size_multiplier as _grade_size
        grade_mult = _grade_size(risk.risk_grade)
        adjusted_risk *= grade_mult
        
        # [ENHANCED] DRAWDOWN-BASED POSITION SCALING
        # Reduce position sizes when portfolio is in drawdown (capital preservation)
        # This is a critical risk management feature missing from the original
        portfolio_drawdown = getattr(risk, 'portfolio_drawdown', 0)  # 0 = no drawdown
        if portfolio_drawdown < -0.20:
            drawdown_mult = 0.25  # 25% of normal size in severe drawdown
            result._drawdown_reduction = "SEVERE (75% reduction)"
        elif portfolio_drawdown < -0.15:
            drawdown_mult = 0.50  # 50% of normal size
            result._drawdown_reduction = "MODERATE (50% reduction)"
        elif portfolio_drawdown < -0.10:
            drawdown_mult = 0.75  # 75% of normal size
            result._drawdown_reduction = "MINOR (25% reduction)"
        else:
            drawdown_mult = 1.0
            result._drawdown_reduction = None
        
        adjusted_risk *= drawdown_mult
        
        # No Kelly ceiling is published. The available win probability is a
        # heuristic, not a prospectively calibrated estimate, and it did not
        # constrain the actual allocation. Risk-budget sizing below remains the
        # authoritative and auditable sizing method.
        result.kelly_optimal = 0.0
        
        # Calculate position
        dollar_risk = account_size * adjusted_risk
        shares = int(dollar_risk / risk_per_share)
        
        # Apply a risk-grade and beta-scaled hard exposure cap. Risk budgeting
        # alone is insufficient when a tight stop would otherwise permit a
        # very large notional position in a high-beta security.
        grade = str(getattr(risk, 'risk_grade', 'C') or 'C').strip().upper()
        grade_cap = Config.MAX_WEIGHT_BY_RISK_GRADE.get(
            grade, Config.MAX_WEIGHT_BY_RISK_GRADE['C']
        )
        beta = max(1.0, float(getattr(risk, 'beta', 1.0) or 1.0))
        result.max_portfolio_weight = min(Config.MAX_POSITION_PCT, grade_cap / beta)
        max_position_value = account_size * result.max_portfolio_weight
        max_shares = int(max_position_value / entry)
        shares = min(shares, max_shares)
        
        # Liquidity constraint (10% of average volume)
        if risk.avg_volume > 0:
            vol_limit = int(risk.avg_volume * 0.1)
            shares = min(shares, vol_limit)
        
        # Minimum viable position
        # FIX: Allow at least 1 share if we have enough capital, even if below 'min_value' threshold
        # This prevents 0 share recommendations for high priced stocks/ETFs
        min_value = 500
        min_shares = max(1, int(min_value / entry))
        
        # If calculating strictly by risk results in 0 shares, but we have capital, allow 1 share
        if shares == 0 and account_size >= entry and max_position_value >= entry:
            shares = 1
        
        if shares < min_shares:
            result.is_viable = False
            result.recommendation = f"Position too small (min: {min_shares} shares)"
            return result
        
        # Final results
        result.recommended_shares = shares
        result.position_value = shares * entry
        result.portfolio_weight = result.position_value / account_size
        result.dollar_risk = shares * risk_per_share
        result.risk_percent = result.dollar_risk / account_size
        result.is_viable = True
        result.sizing_method = "RISK_ADJUSTED"
        
        # Warnings
        if result.portfolio_weight > 0.08:
            result.warnings.append("Position exceeds 8% of portfolio")
        if risk.annualized_volatility > 0.4:
            result.warnings.append("High volatility - consider smaller size")
        
        result.recommendation = (
            f"Buy {shares:,} shares at {entry:.2f} | "
            f"Risk: {result.dollar_risk:,.0f} ({result.risk_percent:.2%})"
        )
        
        return result


# =============================================================================
# TRADE SETUP GENERATOR
# =============================================================================

class TradeSetupGenerator:
    """Generate complete trade setups with entry, targets, and stops"""
    
    # Transaction cost model: spread + slippage + commission
    # Conservative estimates for retail execution
    SPREAD_BPS = _T.alias(_T.LEVELS, "RESEARCH_SPREAD_BPS")
    SLIPPAGE_BPS = _T.alias(_T.LEVELS, "RESEARCH_SLIPPAGE_BPS")
    COMMISSION_PER_SHARE = 0.0  # Most brokers commission-free

    # [T7 FIX] The one trailing-stop definition in this file. A hardcoded 7%
    # appeared in one panel, 9% in another, and an ATR multiple in a third.
    TRAILING_ATR_MULT = 2.0
    # Stop distance band, in ATRs, for a multi-week holding period.
    STOP_ATR_MIN = 1.5
    STOP_ATR_DEFAULT = 2.0
    STOP_ATR_MAX = 3.0
    STOP_MAX_PCT = 0.25
    
    @classmethod
    def round_trip_cost_pct(cls) -> float:
        """Total round-trip transaction cost as a percentage."""
        return (cls.SPREAD_BPS + cls.SLIPPAGE_BPS * 2) / 10000 * 100
    
    # Signals for which a long position is either being opened or already held.
    # Both need levels: an entry needs a stop, and so does a holding.
    _LONG_STANCE = (Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE,
                    Signal.HOLD, Signal.NO_POSITION)
    _ENTRY_STANCE = (Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE)
    _EXIT_STANCE = (Signal.SELL, Signal.STRONG_SELL, Signal.REDUCE)

    def generate(self, current_price: float, signal: Signal,
                 technical: TechnicalIndicators, risk: RiskMetrics,
                 confidence: float, currency_symbol: str = '$',
                 valuation: Optional['ValuationMetrics'] = None) -> TradeSetup:
        """Generate a trade setup: entry, stop, targets and honest R:R.

        See :class:`TradeSetup` for why this now covers HOLD and NO_POSITION
        rather than only the buy tier.

        [T11 FIX] Targets used to be pure R-multiples of the stop
        (1.5R / 2.5R / 4R), which makes ``risk_reward_ratio`` a CONSTANT by
        construction — 2.42:1 for every ticker in the universe, once the
        round-trip cost is deducted. So the "R:R below the RR.RESEARCH_MIN minimum" gate
        could never bind on a real setup: it either passed at 2.42 or the
        struct was empty at 0.0. A ratio that cannot vary carries no
        information and cannot reject anything.

        Meanwhile this engine already computes a blended 12-month price target
        from DCF, analyst consensus and multiples — ``ValuationMetrics``
        target_conservative / target_base / target_aggressive — and the trade
        setup ignored all of it. Anchoring to that valuation work is what makes
        R:R vary with the actual opportunity: for a name the engine's own DCF
        says has 1.4% upside, R:R now comes out below 1 and the gate rejects
        it, instead of reporting a manufactured 2.42.
        """
        result = TradeSetup()

        if current_price <= 0:
            result.setup_basis = "no valid price"
            return result

        atr = technical.atr if technical.atr > 0 else current_price * 0.02

        # ── Exit stance: no long levels to publish ────────────────────────
        if signal in self._EXIT_STANCE:
            result.is_actionable_entry = False
            result.levels_available = False
            result.setup_basis = "exit signal — no long entry levels"
            result.entry_conditions = ["Not applicable — the signal is to reduce or exit"]
            result.exit_conditions = [
                "Scale out into strength rather than at the market",
                "Re-assess only after the trend or the thesis repairs",
            ]
            return result

        if signal not in self._LONG_STANCE:
            result.setup_basis = f"unhandled signal {getattr(signal, 'value', signal)}"
            return result

        is_entry = signal in self._ENTRY_STANCE
        result.is_actionable_entry = is_entry

        # ── Entry reference ───────────────────────────────────────────────
        if is_entry:
            # A stretched tape gets a pullback limit; otherwise a small premium
            # to improve the odds of a fill.
            if technical.rsi > 60:
                result.entry_price = current_price * 0.99
                entry_basis = "limit below spot (RSI extended)"
            else:
                result.entry_price = current_price * 1.002
                entry_basis = "limit at a slight premium to spot"
        else:
            # Not a new-money order. Spot is the reference the stop and the
            # targets are measured from, for a holder or a watcher.
            result.entry_price = current_price
            result.entry_type = "REFERENCE"
            entry_basis = "spot (reference only, not an order)"

        # ── Stop ──────────────────────────────────────────────────────────
        # Sized for the SAME multi-week horizon the target is measured over:
        # never inside 1.5xATR (a normal day's noise would take it out, which
        # used to happen with support 0.8 ATR away and a blanket 5% cap), never
        # beyond 3xATR. Position sizing absorbs the distance, not the stop.
        support = technical.support_level
        if support > 0 and support < result.entry_price:
            raw_dist = result.entry_price - support * 0.99
            stop_basis = "below support"
        else:
            raw_dist = self.STOP_ATR_DEFAULT * atr
            stop_basis = f"{self.STOP_ATR_DEFAULT:g}xATR below entry"
        dist = min(max(raw_dist, self.STOP_ATR_MIN * atr), self.STOP_ATR_MAX * atr)
        if dist > raw_dist + 1e-9:
            stop_basis += f" (widened to {self.STOP_ATR_MIN:g}xATR)"
        elif dist < raw_dist - 1e-9:
            stop_basis += f" (capped at {self.STOP_ATR_MAX:g}xATR)"
        # Floor against a micro-stop; cap so an extreme ATR cannot put the stop
        # at or below zero (beyond 25% it is no longer a trade stop).
        dist = min(max(dist, result.entry_price * 0.005), result.entry_price * self.STOP_MAX_PCT)
        result.stop_loss = result.entry_price - dist

        # ── Targets ───────────────────────────────────────────────────────
        # The base target is the blended valuation reference (a 12-month view);
        # it is what reward/risk is measured against and it is labelled as such
        # below. Partial-exit levels (T1/T3) are tactical: they are placed
        # between the entry and that reference, and T1 cannot pass structural
        # resistance. Nothing is fabricated when no valuation source exists.
        val_base = float(getattr(valuation, 'target_base', 0.0) or 0.0) if valuation else 0.0
        val_aggr = float(getattr(valuation, 'target_aggressive', 0.0) or 0.0) if valuation else 0.0

        # Sanity-bound the valuation targets: a blend that lands more than 3x
        # spot or below half of it is a data artefact, not a view.
        def _usable(v: float) -> bool:
            return current_price * 0.5 < v < current_price * 3.0

        if _usable(val_base) and val_base <= result.entry_price:
            # Fair value is at or below spot: there is no long reward to
            # publish, and partial-exit levels above a target that sits below
            # the entry would be incoherent.
            result.has_long_reward = False
            result.target_base = val_base
            # Published so the Decision names the real rule (target below the
            # entry), not "no valuation target"; it carries no reward.
            frac = result.horizon_days / float(result.valuation_horizon_days)
            result.horizon_target = result.entry_price + (val_base - result.entry_price) * frac
            result.target_conservative = 0.0
            result.target_aggressive = 0.0
            target_basis = (
                f"12-month valuation target {currency_symbol}{val_base:,.2f} "
                f"({(val_base / current_price - 1) * 100:+.0f}%) is at or below spot — "
                f"no long reward; stop shown for holders only"
            )
        elif _usable(val_base):
            result.target_base = val_base
            # The reward this horizon can plausibly carry: the 12-month upside
            # accrued linearly over the Decision's holding period.
            frac = result.horizon_days / float(result.valuation_horizon_days)
            result.horizon_target = result.entry_price + (val_base - result.entry_price) * frac
            result.target_conservative = result.horizon_target
            if technical.resistance_level > result.entry_price:
                result.target_conservative = min(
                    result.target_conservative, technical.resistance_level
                )
            result.target_conservative = max(result.target_conservative, result.entry_price)
            result.target_aggressive = (
                val_aggr if _usable(val_aggr) and val_aggr > val_base
                else val_base
            )
            target_basis = (
                f"{result.horizon_days}-session target pro-rated from the 12-month valuation "
                f"target {currency_symbol}{val_base:,.2f}; Target 1 is a tactical partial exit"
            )
        else:
            # No usable valuation source. A stop multiple is a payoff design,
            # not evidence that price can reach it, so no target and no
            # reward/risk is published (and none can gate a buy).
            result.has_long_reward = False
            result.target_conservative = 0.0
            result.target_base = 0.0
            result.target_aggressive = 0.0
            target_basis = "no valuation blend available — no target or reward/risk published"

        result.levels_available = True
        result.setup_basis = f"{entry_basis}; stop {stop_basis}; {target_basis}"

        # ── Risk / reward, net of round-trip execution cost ───────────────
        # [T12 FIX] `reward_amount = abs(target_base - entry_price)` was
        # harmless while targets were always R-multiples ABOVE entry. Now that
        # they come from the valuation blend they can legitimately land BELOW
        # spot — and `abs()` then reported a 27% overvaluation as 27% of upside,
        # producing R:R 6.15 on a name the engine's own DCF called overvalued.
        # Reward is directional: a target at or below entry is not reward.
        result.risk_amount = abs(result.entry_price - result.stop_loss)
        # Reward to the horizon target only: a 12-month target against a
        # multi-week stop compared two different holding periods.
        raw_reward = (result.horizon_target - result.entry_price) if result.horizon_target > 0 else 0.0
        cost_per_share = result.entry_price * (
            self.SPREAD_BPS + self.SLIPPAGE_BPS * 2
        ) / 10000
        result.reward_amount = max(0.0, raw_reward - cost_per_share)
        result.risk_amount = result.risk_amount + cost_per_share
        result.risk_reward_ratio = safe_divide(result.reward_amount, result.risk_amount, 0.0)


        # ── Trailing stop: ONE definition, ATR-derived ────────────────────
        # [T7 FIX] Four incompatible trailing rules used to coexist in this
        # file — a hardcoded 7% in one panel, 9% in another, this unset
        # `trailing_stop_pct` field, and an ATR multiple elsewhere. All of
        # them now read this.
        result.stop_type = "ATR"
        result.trailing_stop_pct = round(
            safe_divide(self.TRAILING_ATR_MULT * atr, current_price, 0.0) * 100, 1
        )

        # ── Confidence, not a probability ─────────────────────────────────
        # [T6 FIX] `win_probability = 0.4 + confidence/100 * 0.2` was an
        # uncalibrated number presented as a probability, and `expected_value`
        # was computed from it — so a made-up win rate drove a dollar figure.
        # SP_ELITE_PRO deprecated exactly this pattern. Neither is emitted now;
        # the deterministic tier below is what the engine can actually support.
        result.win_probability = 0.0
        result.expected_value = 0.0
        if confidence >= 70:
            result.confidence_tier = "HIGH"
        elif confidence >= 50:
            result.confidence_tier = "MEDIUM"
        else:
            result.confidence_tier = "LOW"

        # ── Horizon ───────────────────────────────────────────────────────
        # One horizon: the Decision's. It no longer varies with ADX or
        # conviction, which gave the levels a different clock from the call.
        result.time_horizon = "POSITION"
        result.estimated_days = int(round(result.horizon_days * 365 / 252))
        result.review_date = (
            datetime.now() + timedelta(days=result.estimated_days)
        ).strftime('%Y-%m-%d')

        sym = currency_symbol

        if is_entry:
            result.entry_conditions = [
                f"Entry limit {sym}{result.entry_price:.2f} — do not chase above it",
                f"Invalidated on a close below {sym}{result.stop_loss:.2f}",
                "Volume at or above 1.5x the 20-day average on the entry day",
                "No earnings inside the next 5 sessions",
            ]
            result.exit_conditions = [
                f"Take a third at {sym}{result.target_conservative:.2f} (tactical partial exit)",
                f"Move the stop to breakeven once {sym}{result.entry_price + result.risk_amount:.2f} trades",
                f"Take a third at {sym}{result.horizon_target:.2f} ({result.horizon_days}-session target; "
                f"12-month valuation target {sym}{result.target_base:.2f})",
                f"Trail the balance by {result.trailing_stop_pct:.1f}% ({self.TRAILING_ATR_MULT:g}xATR)",
            ]
        else:
            result.entry_conditions = [
                "Reference levels only — this is not a new-money entry",
                f"Would become actionable on a close above {sym}{technical.resistance_level:.2f}"
                if technical.resistance_level > current_price else
                "Would become actionable once the setup and risk grade improve",
            ]
            result.exit_conditions = [
                f"If held: protective stop at {sym}{result.stop_loss:.2f}",
                f"If held: trim into {sym}{result.target_conservative:.2f}",
                f"If held: trail by {result.trailing_stop_pct:.1f}% ({self.TRAILING_ATR_MULT:g}xATR) once green",
            ]

        return result


# =============================================================================
# MACRO ENVIRONMENT ANALYZER
# =============================================================================

class MacroEnvironmentAnalyzer:
    """
    Fetch and analyze macroeconomic environment for institutional-grade analysis.
    Provides Fed policy outlook, mega-cap earnings calendar, and macro news sentiment.
    """
    
    # Market-moving mega-cap stocks whose earnings affect broader market
    MEGA_CAP_TICKERS = [
        'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B',
        'JPM', 'V', 'MA', 'UNH', 'JNJ', 'WMT', 'XOM', 'PG', 'HD', 'CVX',
        'BAC', 'ABBV', 'COST', 'MRK', 'AVGO', 'PEP', 'KO', 'TMO', 'LLY',
        'MCD', 'CSCO', 'ACN', 'ADBE', 'CRM', 'AMD', 'NFLX', 'INTC', 'ORCL'
    ]
    
    # Macro news keywords for sentiment analysis
    MACRO_KEYWORDS = {
        'hawkish': ['rate hike', 'hawkish', 'tightening', 'inflation rising', 
                    'higher for longer', 'sticky inflation', 'more hikes'],
        'dovish': ['rate cut', 'dovish', 'easing', 'pause', 'pivot', 
                   'soft landing', 'disinflation', 'cuts expected'],
        'recession': ['recession', 'slowdown', 'contraction', 'layoffs', 
                      'hard landing', 'economic weakness', 'yield curve inversion'],
        'expansion': ['strong growth', 'expansion', 'robust economy', 'hiring',
                      'consumer spending', 'gdp growth', 'economic momentum']
    }
    
    # ==========================================================================
    # FOMC CALENDAR - 100% ALGORITHMIC (NO HARDCODED DATES)
    # ==========================================================================
    # The Federal Reserve follows a predictable pattern:
    # - 8 meetings per year
    # - Meetings are typically 2-day events ending on Wednesday
    # - Pattern: Late Jan, Mid-Mar, Early May, Mid-Jun, Late Jul, Mid-Sep, Early Nov, Mid-Dec
    # 
    # This algorithm generates FOMC dates for any year without requiring manual updates.
    # Reference: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    # ==========================================================================
    
    # FOMC meeting pattern: (month, week_of_month) - meetings end on Wednesday
    # Week 1 = first full week, Week 3 = third week, etc.
    FOMC_MEETING_PATTERN = [
        (1, 4),   # January: 4th Wednesday (late January)
        (3, 3),   # March: 3rd Wednesday (mid-March)
        (5, 1),   # May: 1st Wednesday (early May)
        (6, 2),   # June: 2nd/3rd Wednesday (mid-June) - using 2nd for better fit
        (7, 4),   # July: 4th Wednesday (late July)
        (9, 3),   # September: 3rd Wednesday (mid-September)
        (11, 1),  # November: 1st Wednesday (early November)
        (12, 3),  # December: 3rd Wednesday (mid-December)
    ]
    
    @classmethod
    def _get_nth_weekday(cls, year: int, month: int, weekday: int, n: int) -> date:
        """
        Get the nth occurrence of a weekday in a given month.
        
        Args:
            year: The year
            month: The month (1-12)
            weekday: Day of week (0=Monday, 2=Wednesday, 6=Sunday)
            n: Which occurrence (1=first, 2=second, etc.)
        
        Returns:
            The date of the nth occurrence of that weekday
        """
        first_day = date(year, month, 1)
        # Find the first occurrence of the target weekday
        days_until_target = (weekday - first_day.weekday()) % 7
        first_occurrence = first_day + timedelta(days=days_until_target)
        # Get the nth occurrence
        return first_occurrence + timedelta(weeks=n - 1)
    
    @classmethod
    def _generate_fomc_calendar(cls) -> List[str]:
        """
        Generate FOMC calendar using official dates when available, algorithmic fallback otherwise.
        
        Official 2025 FOMC Dates from Federal Reserve:
        Jan 28-29, Mar 18-19, May 6-7, Jun 17-18, Jul 29-30, Sep 16-17, Nov 4-5, Dec 16-17
        """
        today = date.today()
        current_year = today.year
        
        # Official FOMC meeting end dates (announcement day)
        FOMC_OFFICIAL = {
            2025: ['2025-01-29', '2025-03-19', '2025-05-07', '2025-06-18',
                   '2025-07-30', '2025-09-17', '2025-11-05', '2025-12-17'],
            2026: ['2026-01-28', '2026-03-18', '2026-05-06', '2026-06-17',
                   '2026-07-29', '2026-09-16', '2026-11-04', '2026-12-16'],
        }
        
        all_dates = []
        
        for year in range(current_year - 1, current_year + 3):
            if year in FOMC_OFFICIAL:
                # Use official dates when available
                all_dates.extend(FOMC_OFFICIAL[year])
            else:
                # Algorithmic fallback for years without official dates
                for month, week_num in cls.FOMC_MEETING_PATTERN:
                    # FOMC meetings end on Wednesday (weekday=2)
                    fomc_date = cls._get_nth_weekday(year, month, weekday=2, n=week_num)
                    all_dates.append(fomc_date.strftime('%Y-%m-%d'))
        
        # Sort chronologically
        all_dates.sort()
        
        return all_dates
    
    def __init__(self, api_client: Optional['APIClient'] = None):
        self.api = api_client
        self.cache = {}
        # Generate FOMC calendar dynamically
        self.fomc_dates = self._generate_fomc_calendar()
    
    def analyze(self) -> Tuple[MacroEconomicData, MegaCapEarningsCalendar]:
        """Run complete macro environment analysis"""
        macro_data = self.fetch_macro_data()
        mega_cap_earnings = self.fetch_mega_cap_earnings()
        
        # Cross-reference for risk assessment
        self._calculate_macro_risk(macro_data, mega_cap_earnings)
        
        return macro_data, mega_cap_earnings
    
    def fetch_macro_data(self) -> MacroEconomicData:
        """Fetch macroeconomic indicators"""
        result = MacroEconomicData()
        
        try:
            # --- FOMC Calendar (now dynamic) ---
            today = date.today()
            for fomc_date_str in self.fomc_dates:
                fomc_date = datetime.strptime(fomc_date_str, '%Y-%m-%d').date()
                if fomc_date >= today:
                    result.fomc_date = fomc_date_str
                    result.days_to_fomc = (fomc_date - today).days
                    break
            
            # --- Treasury Yields (for yield curve & Fed outlook) ---
            try:
                # 10-Year Treasury
                tlt = yf.Ticker("^TNX")  # 10-Year Treasury Yield Index
                tlt_data = tlt.history(period="5d")
                if len(tlt_data) > 0:
                    result.treasury_10y = float(tlt_data['Close'].iloc[-1])
                
                # 2-Year Treasury  
                two_year = yf.Ticker("^IRX")  # 13-week Treasury as proxy
                two_data = two_year.history(period="5d")
                if len(two_data) > 0:
                    # Estimate 2Y from 3-month + spread
                    result.treasury_2y = float(two_data['Close'].iloc[-1]) + 0.5
            except Exception:
                # Fallback estimates based on current market conditions
                result.treasury_10y = 4.40
                result.treasury_2y = 4.30
            
            # Yield curve spread
            result.yield_curve_spread = result.treasury_10y - result.treasury_2y
            
            if result.yield_curve_spread < -0.2:
                result.yield_curve_signal = "INVERTED"
                result.macro_risk_factors.append("Yield curve inverted - recession indicator")
            elif result.yield_curve_spread < 0.2:
                result.yield_curve_signal = "FLAT"
                result.macro_risk_factors.append("Flat yield curve - economic uncertainty")
            else:
                result.yield_curve_signal = "NORMAL"
            
            # --- Fed Outlook based on market signals ---
            result = self._calculate_fed_outlook(result)
            
            # --- Economic Outlook from VIX and market signals ---
            result = self._calculate_economic_outlook(result)
            
            # --- Macro News Sentiment ---
            result = self._fetch_macro_news_sentiment(result)
            
        except Exception as e:
            logger.debug(f"Macro data fetch error: {e}")
        
        return result
    
    def _calculate_fed_outlook(self, data: MacroEconomicData) -> MacroEconomicData:
        """Determine Fed outlook from market signals"""
        try:
            # Get VIX for market stress
            vix = yf.Ticker("^VIX")
            vix_data = vix.history(period="5d")
            vix_level = float(vix_data['Close'].iloc[-1]) if len(vix_data) > 0 else 20
            
            # Decision logic based on yields & VIX
            if data.treasury_10y > 4.5 and vix_level < 20:
                # High yields, low fear = market expects hawkish Fed
                data.fed_outlook = "HAWKISH"
                data.fed_outlook_reason = "Elevated yields suggest tight policy continues"
                data.rate_change_probability = 0.1  # Low chance of cut
            elif data.treasury_10y < 4.0 or vix_level > 25:
                # Lower yields or high fear = market expects dovish Fed
                data.fed_outlook = "DOVISH"
                data.fed_outlook_reason = "Market pricing in rate cuts ahead"
                data.rate_change_probability = 0.6  # Higher chance of cut
            else:
                data.fed_outlook = "NEUTRAL"
                data.fed_outlook_reason = "Fed likely to hold rates steady"
                data.rate_change_probability = 0.3
            
            # FOMC proximity adjustment
            if 0 < data.days_to_fomc <= 7:
                data.macro_risk_factors.append(f"FOMC meeting in {data.days_to_fomc} days - expect volatility")
                
        except Exception as e:
            logger.debug(f"Fed outlook calculation error: {e}")
            data.fed_outlook = "NEUTRAL"
            data.fed_outlook_reason = "Unable to determine market expectations"
        
        return data
    
    def _calculate_economic_outlook(self, data: MacroEconomicData) -> MacroEconomicData:
        """Calculate economic outlook from market signals"""
        try:
            # Use sector performance as economic proxy
            xlf = yf.Ticker("XLF")  # Financials - sensitive to economy
            xly = yf.Ticker("XLY")  # Consumer Discretionary
            xlu = yf.Ticker("XLU")  # Utilities - defensive
            
            cyclical_strength = 0
            
            for ticker, weight in [(xlf, 1), (xly, 1), (xlu, -1)]:
                try:
                    hist = ticker.history(period="1mo")
                    if len(hist) >= 2:
                        pct_change = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]
                        cyclical_strength += pct_change * weight
                except Exception:
                    continue
            
            if cyclical_strength > 0.03:
                data.economic_outlook = "EXPANSION"
            elif cyclical_strength < -0.03:
                data.economic_outlook = "CONTRACTION"
                if data.yield_curve_signal == "INVERTED":
                    data.economic_outlook = "RECESSION_RISK"
                    data.macro_risk_factors.append("Economic indicators suggest recession risk")
            else:
                data.economic_outlook = "STABLE"
                
        except Exception as e:
            logger.debug(f"Economic outlook error: {e}")
            data.economic_outlook = "STABLE"
        
        return data
    
    def _fetch_macro_news_sentiment(self, data: MacroEconomicData) -> MacroEconomicData:
        """Fetch and analyze macro-related news sentiment"""
        try:
            if not self.api:
                return data
            
            # Fetch general market news
            news = self.api.fetch_news("Fed OR economy OR inflation OR interest rates", limit=10)
            
            if not news:
                return data
            
            hawkish_count = 0
            dovish_count = 0
            recession_count = 0
            expansion_count = 0
            
            for article in news:
                text = f"{article.get('title', '')} {article.get('description', '')}".lower()
                
                for keyword in self.MACRO_KEYWORDS['hawkish']:
                    if keyword in text:
                        hawkish_count += 1
                        break
                        
                for keyword in self.MACRO_KEYWORDS['dovish']:
                    if keyword in text:
                        dovish_count += 1
                        break
                        
                for keyword in self.MACRO_KEYWORDS['recession']:
                    if keyword in text:
                        recession_count += 1
                        break
                        
                for keyword in self.MACRO_KEYWORDS['expansion']:
                    if keyword in text:
                        expansion_count += 1
                        break
                
                # Store headline
                title = article.get('title', '')
                if title and len(data.macro_headlines) < 3:
                    data.macro_headlines.append(title[:80])
            
            # Calculate net sentiment
            total = hawkish_count + dovish_count + recession_count + expansion_count + 1
            data.macro_news_sentiment = (
                (dovish_count + expansion_count - hawkish_count - recession_count) / total
            )
            
            # Adjust Fed outlook if strong news signal
            if dovish_count >= 3 and hawkish_count <= 1:
                if data.fed_outlook != "DOVISH":
                    data.fed_outlook_reason += " (news supports dovish shift)"
            elif hawkish_count >= 3 and dovish_count <= 1:
                if data.fed_outlook != "HAWKISH":
                    data.fed_outlook_reason += " (news supports hawkish stance)"
            
            if recession_count >= 2:
                data.macro_risk_factors.append("Elevated recession mentions in news")
                
        except Exception as e:
            logger.debug(f"Macro news sentiment error: {e}")
        
        return data
    
    def fetch_mega_cap_earnings(self) -> MegaCapEarningsCalendar:
        """Fetch earnings calendar for market-moving mega-cap stocks"""
        result = MegaCapEarningsCalendar()
        today = date.today()
        week_later = today + timedelta(days=7)
        two_weeks = today + timedelta(days=14)
        
        earnings_data = []
        
        # Fetch earnings for mega-caps (limit to top 15 for speed)
        for ticker in self.MEGA_CAP_TICKERS[:15]:
            try:
                stock = yf.Ticker(ticker)
                calendar = stock.calendar
                
                if calendar is None or calendar.empty:
                    continue
                
                earnings_date = calendar.get('Earnings Date')
                if earnings_date is None:
                    continue
                
                # Handle list format
                if isinstance(earnings_date, list) and earnings_date:
                    ed = earnings_date[0]
                else:
                    ed = earnings_date
                
                # Convert to date
                if hasattr(ed, 'date'):
                    ed = ed.date()
                elif isinstance(ed, datetime):
                    ed = ed.date()
                
                if ed >= today:
                    days_away = (ed - today).days
                    earnings_data.append({
                        'ticker': ticker,
                        'date': ed.isoformat(),
                        'days_away': days_away
                    })
                    
            except Exception:
                continue
        
        # Sort by date
        earnings_data.sort(key=lambda x: x['days_away'])
        result.upcoming_earnings = earnings_data[:10]
        
        # Categorize
        for item in earnings_data:
            days = item['days_away']
            ticker = item['ticker']
            
            if days <= 7:
                result.earnings_this_week.append(ticker)
            elif days <= 14:
                result.earnings_next_week.append(ticker)
        
        # High impact flag
        high_impact_tickers = {'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA'}
        this_week_high_impact = set(result.earnings_this_week) & high_impact_tickers
        
        if this_week_high_impact:
            result.high_impact_earnings = True
            result.market_volatility_warning = (
                f"⚠️ HIGH IMPACT: {', '.join(this_week_high_impact)} reporting this week - "
                "expect elevated market volatility"
            )
        
        if earnings_data:
            result.days_to_next_mega_earnings = earnings_data[0]['days_away']
            result.next_mega_ticker = earnings_data[0]['ticker']
        
        return result
    
    def _calculate_macro_risk(self, macro: MacroEconomicData, 
                               earnings: MegaCapEarningsCalendar) -> None:
        """Calculate overall macro risk score"""
        risk_score = 5  # Start neutral
        
        # Fed risk
        if macro.fed_outlook == "HAWKISH":
            risk_score += 1
        if 0 < macro.days_to_fomc <= 3:
            risk_score += 1
        
        # Yield curve risk
        if macro.yield_curve_signal == "INVERTED":
            risk_score += 2
        elif macro.yield_curve_signal == "FLAT":
            risk_score += 1
        
        # Economic risk
        if macro.economic_outlook == "RECESSION_RISK":
            risk_score += 2
        elif macro.economic_outlook == "CONTRACTION":
            risk_score += 1
        
        # Earnings volatility
        if earnings.high_impact_earnings:
            risk_score += 1
        
        # News sentiment
        if macro.macro_news_sentiment < -0.3:
            risk_score += 1
        
        macro.macro_risk_score = min(10, max(1, risk_score))


# =============================================================================
# THESIS GENERATOR
# =============================================================================

class ThesisGenerator:
    """Generate investment thesis summary"""
    
    def generate(self, result: AnalysisResult) -> InvestmentThesis:
        """Generate comprehensive investment thesis"""
        thesis = InvestmentThesis()
        
        # Summary
        signal_desc = {
            Signal.STRONG_BUY: "represents a high-conviction buying opportunity",
            Signal.BUY: "offers an attractive risk/reward for accumulation",
            Signal.ACCUMULATE: "can be gradually accumulated on dips",
            Signal.HOLD: "should be held at current levels",
            Signal.REDUCE: "warrants position reduction",
            Signal.SELL: "should be sold to manage risk",
            Signal.STRONG_SELL: "requires immediate exit"
        }
        
        thesis.summary = (
            f"{result.company_name} ({result.ticker}) {signal_desc.get(result.signal, 'needs further analysis')} "
            f"with a {result.confidence_score:.0f}% confidence score. "
            f"Composite score of {result.scores.composite_score:.0f}/100."
        )
        
        # Bull case
        # [SANITIZER] Each bull-case bullet must agree with the live metrics
        # struct. Avoid claims that contradict another panel in the same report
        # (e.g. "momentum confirmation" while MACD prints BEARISH).
        if result.fundamental.roe > 15:
            thesis.bull_case.append(f"Strong profitability with {result.fundamental.roe:.1f}% ROE")
        if result.fundamental.revenue_growth_yoy > 10:
            thesis.bull_case.append(f"Growing revenues at {result.fundamental.revenue_growth_yoy:.1f}% YoY")
        if result.valuation.dcf_upside > 15:
            thesis.bull_case.append(f"DCF suggests {result.valuation.dcf_upside:.1f}% upside potential")
        # Only claim momentum confirmation if MACD is NOT actively bearish.
        if (result.technical.trend_direction == "UP"
                and result.technical.adx > 25
                and result.technical.macd_signal != "BEARISH"):
            thesis.bull_case.append("Strong uptrend with momentum confirmation")
        # Positive news bullet must not be contradicted by a strongly bearish
        # sentiment score (sentiment_label can lag the score).
        if (result.sentiment.sentiment_label == "POSITIVE"
                and result.sentiment.sentiment_score > 0.1):
            thesis.bull_case.append("Positive news sentiment supporting price action")
        if result.insider.insider_sentiment == "BULLISH":
            thesis.bull_case.append("Insider buying indicates management confidence")

        # Bear case
        if result.fundamental.debt_to_equity > 1.5:
            thesis.bear_case.append(f"Elevated leverage at {result.fundamental.debt_to_equity:.1f}x D/E")
        if result.valuation.valuation_label in ["OVERVALUED", "SIGNIFICANTLY_OVERVALUED"]:
            thesis.bear_case.append(f"Valuation stretched - {result.valuation.valuation_label.replace('_', ' ').lower()}")
        # [SANITIZER] Mirror the bull-side DCF check so a deeply negative DCF
        # upside is acknowledged in the bear case instead of being silent.
        if result.valuation.dcf_upside < -15:
            thesis.bear_case.append(f"DCF suggests {abs(result.valuation.dcf_upside):.1f}% overvaluation")
        if result.risk.max_drawdown < -0.30:
            thesis.bear_case.append(f"History of significant drawdowns ({result.risk.max_drawdown:.1%})")
        if result.risk.annualized_volatility > 0.40:
            thesis.bear_case.append(f"High volatility ({result.risk.annualized_volatility:.1%}) increases risk")
        if result.sentiment.sentiment_label == "NEGATIVE":
            thesis.bear_case.append("Negative news flow creating headwinds")
        # [SANITIZER] Mirror the bull-side insider check.
        if result.insider.insider_sentiment == "BEARISH":
            # "Heavy" only when the Decision would also treat it as material.
            thesis.bear_case.append(
                "Heavy insider selling signals management caution"
                if _insider_selling_is_material(result)
                else "Net insider selling in the last 90 days (small relative to company size)")
        # Flag MACD/trend conflict explicitly so the reader sees the mixed read.
        if (result.technical.trend_direction == "UP"
                and result.technical.macd_signal == "BEARISH"):
            thesis.bear_case.append("Trend up but MACD turning bearish - momentum fading")
        
        # Catalysts
        if result.catalysts.near_term_catalyst:
            thesis.catalysts.append(f"Earnings in {result.catalysts.days_to_earnings} days")
        if result.market_intel.implied_upside > 10:
            thesis.catalysts.append(f"Analyst target implies {result.market_intel.implied_upside:.1f}% upside")
        
        # Risks
        # No generic filler: an empty list reads as "no specific warning".
        thesis.risks = result.risk_warnings[:5] if result.risk_warnings else []
        
        # Key metrics
        thesis.key_metrics = {
            'P/E': result.fundamental.pe_ratio,
            'ROE': f"{result.fundamental.roe:.1f}%",
            'Debt/Equity': result.fundamental.debt_to_equity,
            'Revenue Growth': f"{result.fundamental.revenue_growth_yoy:.1f}%",
            'Volatility': f"{result.risk.annualized_volatility:.1%}",
            'Beta': result.risk.beta
        }
        
        # Recommendation rationale
        thesis.recommendation_rationale = (
            f"Based on composite analysis scoring {result.scores.composite_score:.0f}/100 "
            f"(Technical: {result.scores.technical_score:.0f}, Fundamental: {result.scores.fundamental_score:.0f}, "
            f"Valuation: {result.scores.valuation_score:.0f}, Sentiment: {result.scores.sentiment_score:.0f}), "
            f"the final Decision is {result.final_action or result.signal.value}."
        )
        
        return thesis


# =============================================================================
# SIGNAL TRACKER (Historical Accuracy)
# =============================================================================

# The legacy JSON ``SignalTracker`` was removed (2026-09-27). It scored an
# N-day outcome against whatever price was current when its updater ran,
# collapsed bearish calls to HOLD and wrote to the working directory. The
# SQLite ledger (``tracking.ledger``) is the only record of published calls.

TRACKED_POSITIONS_FILE = ".tracked_positions.json"


def _extract_qualitative_red_flags(result: "AnalysisResult") -> Dict[str, Any]:
    """Data-derived red flags: bearish reversal, DCF overvaluation, heavy
    insider selling. Never raises; a failed sub-check just reads as "no flag".
    """
    bear_rev, bear_name, timing_wait, buy_now = False, "", False, None
    try:
        tech = result.technical
        pats = list(getattr(tech, 'candlestick_patterns', []) or [])
        ptrend = getattr(tech, 'prior_trend', 'SIDEWAYS')
        if pats:
            tp = pats[0]
            if (tp.get('bias') == 'BEARISH'
                    and tp.get('type') == 'REVERSAL'
                    and tp.get('reliability') in ('HIGH', 'MEDIUM')
                    and ptrend == 'UP'):
                bear_rev = True
                bear_name = tp.get('name', 'bearish reversal')
                timing_wait = True
                buy_now = False
    except Exception:
        pass
    dcf_over = None
    try:
        if result.valuation and result.valuation.dcf_upside < 0:
            dcf_over = abs(float(result.valuation.dcf_upside))
    except Exception:
        pass
    return {
        "bearish_reversal": bear_rev,
        "bearish_reversal_name": bear_name,
        "timing_wait": timing_wait,
        "buy_now": buy_now,
        "dcf_overvaluation_pct": dcf_over,
        "heavy_insider_selling": _insider_selling_is_material(result),
    }


def _insider_selling_is_material(result: "AnalysisResult") -> bool:
    """Net insider selling large enough to matter: max($25M, 0.05% of market cap).

    Matches the scanner's materiality test, so routine selling is neither a
    veto nor described as "heavy". Indian tickers are converted to USD first.
    """
    try:
        if not result.insider:
            return False
        net_flow = float(getattr(result.insider, 'net_flow', 0.0) or 0.0)
        market_cap = float(getattr(result, 'market_cap', 0.0) or 0.0)
        if result.ticker.endswith(('.NS', '.BO')):
            scanner = sys.modules.get('SP_ELITE_PRO') or sys.modules.get('core.SP_ELITE_PRO')
            fx = float(getattr(getattr(scanner, 'TradingConfig', None), 'INR_PER_USD', 88.0))
            fx = fx if fx > 0 else 88.0
            net_flow /= fx
            market_cap /= fx
        return net_flow <= -max(25_000_000.0, 0.0005 * market_cap)
    except Exception:
        return False


def build_reconciliation_inputs(result: "AnalysisResult") -> Optional["DecisionInputs"]:
    """DecisionInputs for reconciling a SCANNER pick against this engine.

    Carries only this engine's QUALITATIVE red flags (data validity, risk
    grade, insider activity, bearish reversal, DCF overvaluation) — never
    this engine's own price targets, R:R or composite score, which are a
    second, independently-computed set of numbers. The caller is expected to
    pass the scanner's own action as ``starting_candidate`` and
    ``enforce_price_gates=False`` to ``resolve_final_action``, so this
    engine's price/score gates cannot re-litigate a decision the scanner's
    own gates already made — only a real red flag about the company can move
    the scanner's pick down the conviction ladder.

    Returns None if the decision core is unavailable.
    """
    if not HAS_DECISION_CORE:
        return None
    flags = _extract_qualitative_red_flags(result)
    return DecisionInputs(
        ticker=result.ticker,
        composite_score=0.0,           # unused: caller supplies starting_candidate
        risk_grade=(result.risk.risk_grade if result.risk else 'C'),
        risk_reward_ratio=0.0,         # unused: caller passes enforce_price_gates=False
        current_price=float(result.current_price or 0.0),
        stop_loss=0.0,                 # unused: enforce_price_gates=False
        target_price=0.0,              # unused: enforce_price_gates=False
        has_existing_position=result.has_existing_position,
        is_valid=not result.is_invalid,
        **flags,
    )


# =============================================================================
# MAIN ANALYZER
# =============================================================================

def _fail_closed_decision(result: "AnalysisResult", why: str) -> None:
    """Publish WATCHLIST (no new money) when the decision rules could not run."""
    result.final_action = "WATCHLIST"
    result.final_stance = "NONE"
    result.final_action_codes = ["DECISION_CORE_ERROR"]
    result.final_action_reasons = [f"Decision rules could not be evaluated ({why}); no new money."]
    result.final_blocking_reasons = list(result.final_action_reasons)
    new_sig = _FINAL_ACTION_TO_SIGNAL.get(result.final_action)
    if new_sig is not None:
        result.signal = new_sig


def enforce_final_decision_contract(result: "AnalysisResult") -> "AnalysisResult":
    """Make legacy executable fields obey the resolved FinalAction.

    `Decision.sizing` was already zeroed for non-allocating actions, but API/UI
    consumers also receive the legacy `position_sizing` object. Normalize the
    source object so every surface sees the same no-new-money contract.
    """
    try:
        action = FinalAction(result.final_action)
    except (TypeError, ValueError):
        return result
    if action.allocates_new_capital:
        return result

    sizing = result.position_sizing
    if sizing is not None:
        sizing.recommended_shares = 0
        sizing.position_value = 0.0
        sizing.portfolio_weight = 0.0
        sizing.dollar_risk = 0.0
        sizing.risk_percent = 0.0
        sizing.kelly_optimal = 0.0
        sizing.is_viable = False
        sizing.recommendation = f"No new position: final Decision is {action.value}."
    if result.trade_setup is not None:
        result.trade_setup.is_actionable_entry = False
        if result.trade_setup.entry_type != "REFERENCE":
            result.trade_setup.entry_type = "REFERENCE"
    return result


def _valuation_scenarios(result: "AnalysisResult") -> list:
    """Low / weighted / high of the valuation methods that actually contributed.

    These are the observed range of the methods, not operating scenarios: no
    probability is attached and no fixed multiplier is applied.
    """
    from engine.decision import Scenario

    val = result.valuation
    price = float(result.current_price or 0.0)
    if val is None or price <= 0 or not float(getattr(val, "target_base", 0) or 0):
        return []
    sources = list(getattr(val, "target_sources", []) or [])
    method = "range of " + (", ".join(s.replace("_", " ") for s in sources) if sources else "available methods")
    out = []
    seen = set()
    for name, level, how in (
        ("low_method", val.target_conservative, method + " (lowest)"),
        ("weighted", val.target_base, "weighted blend of " + ", ".join(
            s.replace("_", " ") for s in sources) if sources else "weighted blend"),
        ("high_method", val.target_aggressive, method + " (highest)"),
    ):
        level = float(level or 0.0)
        if level <= 0 or round(level, 4) in seen:
            continue
        seen.add(round(level, 4))
        out.append(Scenario(name, level, (level / price - 1) * 100, how, list(sources)))
    return out


def _decision_conflicts(result: "AnalysisResult", stance: str) -> List[str]:
    """Measured signals that disagree with the Decision's direction."""
    out: List[str] = []
    val = result.valuation
    label = str(getattr(val, "valuation_label", "") or "") if val else ""
    fair = float(getattr(val, "fair_value_estimate", 0) or 0) if val else 0.0
    sym = result.currency_symbol or ""
    if stance == "BULLISH" and "OVERVALUED" in label:
        out.append(f"Valuation reads {label.replace('_', ' ').lower()}"
                   + (f" (intrinsic value {sym}{fair:,.2f})" if fair > 0 else ""))
    if stance == "BEARISH" and "UNDERVALUED" in label:
        out.append(f"Valuation reads {label.replace('_', ' ').lower()}"
                   + (f" (intrinsic value {sym}{fair:,.2f})" if fair > 0 else ""))
    mi = result.market_intel
    price = float(result.current_price or 0)
    tgt = float(getattr(mi, "target_mean", 0) or 0) if mi else 0.0
    if price > 0 and tgt > 0:
        up = (tgt / price - 1) * 100
        if stance == "BULLISH" and up < 0:
            out.append(f"Analyst consensus target is {up:+.1f}% from the price")
        if stance == "BEARISH" and up > 15:
            out.append(f"Analyst consensus target implies {up:+.1f}% upside")
    return out


def decision_for_analysis(result: "AnalysisResult"):
    """Build the one published deep-analysis decision from the resolved action."""
    from engine.decision import Decision, POSITION, SourceRef, assess_price_freshness
    from engine.evidence import ELITE, get_evidence

    try:
        action = FinalAction(result.final_action)
    except (TypeError, ValueError):
        return None
    try:
        as_of = pd.Timestamp(result.market_timestamp).date() if result.market_timestamp else None
    except (TypeError, ValueError):
        as_of = None
    scores = result.scores
    setup = result.trade_setup
    risk = result.risk
    freshness = assess_price_freshness(as_of)
    blocking = list(getattr(result, "final_blocking_reasons", []) or [])
    blocking_codes = list(getattr(result, "final_action_codes", []) or [])
    transitions = list(getattr(result, "final_action_transitions", []) or [])
    # Defensive cache/presentation gate. The live analyzer rejects stale input
    # before scoring, but a serialized result can age while retained in cache.
    if freshness.status != "fresh" and action.allocates_new_capital:
        prior_action = action
        action = FinalAction.WATCHLIST
        stale_reason = freshness.notes[0] if freshness.notes else "Price freshness is unknown."
        blocking.append(stale_reason)
        blocking_codes.append("STALE_PRICE_DATA")
        transitions.append({
            "from": prior_action.value, "to": action.value,
            "code": "STALE_PRICE_DATA", "reason": stale_reason,
        })

    scenarios = _valuation_scenarios(result)
    has_target = bool(getattr(setup, "horizon_target", 0) or 0)
    valuation_target = float(getattr(setup, "target_base", 0) or 0) or None
    decision = Decision(
        ticker=result.ticker, engine="elite", horizon=POSITION,
        action=action, evidence=get_evidence(ELITE),
        as_of=as_of.isoformat() if as_of else None,
        score=getattr(scores, "composite_score", None),
        score_basis="Research composite (alpha factors only), 0-100",
        factor_scores={
            k: v for k, v in {
                "technical": getattr(scores, "technical_score", None),
                "fundamental": getattr(scores, "fundamental_score", None),
                "valuation": getattr(scores, "valuation_score", None),
                "quality": getattr(scores, "quality_score", None),
                "sentiment": getattr(scores, "sentiment_score", None),
            }.items()
            # A fund's company-fundamental and quality legs are not applicable.
            if not (getattr(result.fundamental, "is_etf", False) and k in ("fundamental", "quality"))
        } if scores else {},
        risk_metrics={
            "risk_score": getattr(scores, "risk_score", None),
            "volatility": getattr(risk, "annualized_volatility", None),
            # The 1.0 fallback is a placeholder, not a measurement.
            "beta": (getattr(risk, "beta", None)
                     if getattr(risk, "beta_status", "unavailable") == "measured" else None),
        },
        risk_grade=getattr(risk, "risk_grade", ""),
        risk_grade_drivers=list(getattr(risk, "grade_drivers", None) or []),
        risk_grade_lookback_days=int(getattr(risk, "risk_grade_lookback_days", 0) or 0),
        reference_price=result.current_price,
        # An exit call publishes no long levels: null, never a 0.0 "level".
        entry=(getattr(setup, "entry_price", None) or None),
        stop=(getattr(setup, "stop_loss", None) or None),
        target=(getattr(setup, "horizon_target", None) if has_target else None),
        reward_risk=getattr(setup, "risk_reward_ratio", None),
        scenarios=scenarios,
        target_basis=(f"{POSITION.trading_days}-session target, pro-rated from the 12-month valuation target"
                      if has_target else ""),
        target_horizon_days=(POSITION.trading_days if has_target else None),
        valuation_target=valuation_target,
        valuation_target_horizon_days=(_T.LEVELS.VALUATION_HORIZON_DAYS if valuation_target else None),
        reasons=list(result.final_action_reasons),
        blocking=blocking,
        blocking_codes=blocking_codes,
        transitions=transitions,
        sizing={
            "shares": getattr(result.position_sizing, "recommended_shares", 0)
            if action.allocates_new_capital else 0,
            "recommended_shares": getattr(result.position_sizing, "recommended_shares", 0)
            if action.allocates_new_capital else 0,
            "position_value": getattr(result.position_sizing, "position_value", 0)
            if action.allocates_new_capital else 0,
            "portfolio_weight": getattr(result.position_sizing, "portfolio_weight", 0)
            if action.allocates_new_capital else 0,
            "dollar_risk": getattr(result.position_sizing, "dollar_risk", 0)
            if action.allocates_new_capital else 0,
        },
        sources=[SourceRef("px", "price", result.data_provider or "unknown",
                           as_of=as_of.isoformat() if as_of else None,
                           retrieved_at=result.retrieval_timestamp or None)],
        freshness=freshness,
        stance_override=result.final_stance or None,
    )
    if (action.allocates_new_capital and result.position_sizing is not None
            and int(getattr(result.position_sizing, "recommended_shares", 0) or 0) <= 0):
        decision.blocking.append(
            "No executable position size: one share or the stop distance exceeds the risk budget at this account size.")
        decision.blocking_codes.append("SIZE_NOT_EXECUTABLE")
    if result.fast_mode:
        decision.blocking.append("Preliminary analysis; full checks were skipped.")
        decision.blocking_codes.append("PRELIMINARY_FAST_MODE")
    if result.data_status != DataStatus.OK:
        decision.blocking.append(result.data_status_reason or result.data_status)
        decision.blocking_codes.append(str(result.data_status).upper())

    # Evidence that argues against the published call is stated on the
    # Decision itself, not left for the reader to find further down (JPM
    # replay: ACCUMULATE beside "Significantly overvalued").
    decision.conflicts = _decision_conflicts(result, decision.stance)

    # Per-reader meaning, from the final action, the score band and the codes.
    from engine.blockers import action_contract, blocker_info, dedupe
    from engine.ladder import LadderInputs, decide as ladder_decide
    _score = getattr(scores, "composite_score", None) if scores else None
    # [F7] One ladder for both pages: grade policy (F counts for the holder at
    # any score), the Strong Buy fold, and the holder rule.
    lad = ladder_decide(LadderInputs(
        action, _score, getattr(result.risk, "risk_grade", "") if result.risk else "",
        dedupe(decision.blocking_codes), str(decision.evidence.status.value),
        getattr(result, "event_check", None)))
    if lad.action is not action:
        decision.action = lad.action
        decision.reasons = list(decision.reasons) + lad.reasons
        action = lad.action
    decision.blocking_codes = lad.codes
    decision.blockers = [blocker_info(c, getattr(result, "event_check", None)) for c in decision.blocking_codes]
    contract = action_contract(
        action, _score, decision.blocking_codes, holdings_known=bool(result.has_existing_position),
        event=getattr(result, "event_check", None),
    )
    contract["holder_action"] = lad.holder_action
    decision.event_check = getattr(result, "event_check", None)
    # Measurable conditions that would change a funded call, straight from the
    # resolver's own rules (never a narrative "monitor conditions").
    if action.allocates_new_capital:
        sym = result.currency_symbol or ""
        inv = []
        if setup is not None and float(getattr(setup, "stop_loss", 0) or 0) > 0:
            inv.append(f"A daily close below the stop, {sym}{setup.stop_loss:,.2f}")
        inv.append(f"Reward/risk to the target falling below {_T.fmt_rr(_T.RR.RESEARCH_MIN)} "
                   "(target cut or price run-up)")
        inv.append("Risk grade deteriorating to F")
        inv.append(f"Overall score falling below {_T.LADDER.MIN_SCORE_FOR_BUY:g}")
        decision.invalidation = inv
    decision.model_stance = contract["model_stance"]
    decision.new_money = contract["new_money"]
    decision.holder_action = contract["holder_action"]
    decision.holdings_known = contract["holdings_known"]
    decision.primary_blocker = contract["primary_blocker"]
    return decision


def _apply_corporate_events(result: Any, ticker: str) -> None:
    """[F7] The corporate-event gate on the Research verdict (engine.corporate_events).

    A pending acquisition or delisting is AVOID; an unavailable check or a
    possible pending deal takes no new money (fail closed). The check is kept
    on the result so the Decision, deep confirmation and My Portfolio read it.
    A failure here never raises: it becomes "unavailable".
    """
    if not HAS_DECISION_CORE or getattr(result, "is_invalid", False):
        return
    from portfolio.fund_events import FUND_INSTRUMENT_TYPES, NOT_APPLICABLE
    result.portfolio_fund_decision = None
    if str(getattr(result, "quote_type", "") or "").strip().upper() in FUND_INSTRUMENT_TYPES:
        # Capture the real resolver verdict BEFORE the shared event gate changes
        # it or clears sizing. Rebuilding from WATCHLIST later would guess which
        # action the gate suppressed. The ladder still applies every other rule.
        from copy import copy
        portfolio_result = copy(result)
        portfolio_result.event_check = dict(NOT_APPLICABLE)
        portfolio_decision = decision_for_analysis(portfolio_result)
        if portfolio_decision is not None:
            result.portfolio_fund_decision = portfolio_decision.to_dict()
    from engine import corporate_events as ce
    try:
        price = float(getattr(result, "current_price", 0) or 0)
        ev = ce.check_many([ticker], prices={ticker: price}).get(ticker) or ce._unavailable(f"no result for {ticker}")
    except Exception as exc:  # noqa: BLE001 - fail closed for new money, never break the analysis
        ev = ce._unavailable(f"{type(exc).__name__} in the event check")
    result.event_check = ev.to_dict()
    if ev.status == "clear":
        return
    try:
        current = FinalAction(str(result.final_action or ""))
    except ValueError:
        return
    if ev.blocks_new_money_outright:
        new = FinalAction.AVOID
    elif current.allocates_new_capital:
        new = FinalAction.WATCHLIST
    else:
        return
    if new is current:
        return
    result.final_action = new.value
    result.final_action_codes = [ev.code] + list(result.final_action_codes or [])
    result.final_action_reasons = list(result.final_action_reasons or []) + [ev.text]
    new_sig = _FINAL_ACTION_TO_SIGNAL.get(result.final_action)
    if new_sig is not None:
        result.signal = new_sig


def _fold_strong_buy(result: "AnalysisResult", sizer: Any, account_size: float) -> None:
    """[F7] Owner decision Q4: no Strong Buy until the model's evidence is VALIDATED.

    The call is published as BUY with the STRONG_BUY_FOLDED code, and the
    proposal is re-sized to the BUY tier (the sizer's own base-risk ratio), so
    the size matches the label.
    """
    if str(result.final_action or "") != FinalAction.STRONG_BUY.value or not HAS_DECISION_CORE:
        return
    from engine.evidence import ELITE, get_evidence
    from engine.thresholds import LADDER
    if str(get_evidence(ELITE).status.value).upper() == LADDER.STRONG_BUY_EVIDENCE:
        return
    result.final_action = FinalAction.BUY.value
    result.final_action_codes = list(result.final_action_codes or []) + ["STRONG_BUY_FOLDED"]
    result.final_action_reasons = list(result.final_action_reasons or []) + [
        "Shown as Buy: a Strong Buy needs a validated model (owner decision Q4)"]
    new_sig = _FINAL_ACTION_TO_SIGNAL.get(result.final_action)
    if new_sig is not None:
        result.signal = new_sig
    ps = result.position_sizing
    ratio = getattr(sizer, "tier_ratio", lambda a, b: 1.0)(Signal.BUY, Signal.STRONG_BUY)
    price = float(getattr(result.trade_setup, "entry_price", 0) or result.current_price or 0)
    if ps is not None and price > 0 and float(getattr(ps, "position_value", 0) or 0) > 0 and ratio < 1.0:
        QuantaEliteAnalyzer._rescale_position_sizing(ps, float(ps.position_value) * ratio, price, account_size)


def record_authoritative_signal(result: "AnalysisResult", ledger=None) -> int:
    """Record the full, final decision at 5/10/30 completed-session horizons.

    The legacy tracker used the request time and today's quote for every
    maturity. This path uses the date of the actual market bar and leaves
    outcome resolution to the ledger CLI, which fetches each exact exit bar.
    """
    from engine.market_calendar import last_completed_session
    from tracking.ledger import default_ledger, record_decisions

    if os.environ.get("QUANTA_TRACKING_DISABLED") == "1":
        return 0
    if (not HAS_DECISION_CORE or result.is_invalid
            or result.data_status != DataStatus.OK or not result.market_timestamp):
        return 0
    decision = decision_for_analysis(result)
    if decision is None or not decision.as_of:
        return 0
    as_of = pd.Timestamp(decision.as_of).date()
    # Today's live, unfinished bar cannot be the entry of a daily forecast.
    if as_of > last_completed_session() or decision.freshness.status != "fresh":
        return 0
    if decision.stance == "NONE":
        return 0
    try:
        ledger = ledger or default_ledger()
        context = {
            "risk_grade": decision.risk_grade,
            "regime": str(getattr(result.market_regime, "value", result.market_regime) or ""),
            "provider": result.data_provider,
            "decision_horizon_days": decision.horizon.trading_days,
        }
        from dataclasses import replace
        from engine.decision import Horizon

        decisions = [replace(
            decision,
            horizon=Horizon(f"{days}d", days, f"{days} trading sessions"),
        ) for days in (5, 10, 30, 63)]
        return record_decisions(decisions, context=context, ledger=ledger)
    except Exception as exc:
        logger.warning("Signal ledger could not record %s: %s", result.ticker, exc)
        return 0


class QuantaEliteAnalyzer:
    """Main orchestrator for comprehensive stock analysis"""
    
    # [PERF] Upper bound on how many tracked positions the portfolio-risk
    # correlation check will fetch 1y history for, so it stays bounded even
    # as the signal history (and derived position list) grows with usage.
    _MAX_RISK_CHECK_POSITIONS = 15
    
    def __init__(self, config: Optional[Dict[str, str]] = None):
        self.config = config or {}
        
        # Initialize components (minimal logging)
        self.cache = CacheManager()
        self.api = APIClient(self.config)
        
        # Analyzers
        self.technical_analyzer = TechnicalAnalyzer()
        self.fundamental_analyzer = FundamentalAnalyzer()
        self.risk_analyzer = RiskAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer(self.api)
        self.market_intel_analyzer = MarketIntelligenceAnalyzer(self.api)
        self.insider_analyzer = InsiderAnalyzer(self.api)
        self.catalyst_analyzer = CatalystAnalyzer(self.api)
        self.valuation_analyzer = ValuationAnalyzer()
        self.monte_carlo = MonteCarloSimulator()
        self.signal_generator = SignalGenerator()
        self.position_sizer = PositionSizer()
        self.trade_setup_generator = TradeSetupGenerator()
        self.thesis_generator = ThesisGenerator()
        self.peer_analyzer = PeerAnalyzer()
        # Historical position context still reads the legacy JSON history.
        # New forecasts are written only to the exact-maturity SQLite ledger.
        
        # Initialize optional modules
        modules = []
        
        # [COMPLIANCE] Initialize regulatory compliance manager
        if HAS_COMPLIANCE and ComplianceManager:
            self.compliance = ComplianceManager()
            modules.append("Compliance")
        else:
            self.compliance = None
        
        # [CATALYST] Initialize news-driven catalyst scanner with SEC EDGAR
        if HAS_CATALYST and InstitutionalSignalGenerator:
            self.catalyst_signal_generator = InstitutionalSignalGenerator()
            self.catalyst_scanner = CatalystScanner()
            modules.append("Catalyst Scanner")
        elif HAS_CATALYST and CatalystScanner:
            self.catalyst_scanner = CatalystScanner()
            self.catalyst_signal_generator = None
            modules.append("Catalyst Scanner")
        else:
            self.catalyst_scanner = None
            self.catalyst_signal_generator = None
        
        # [PORTFOLIO RISK] Initialize portfolio-level exposure enforcement.
        # [F02] This is a local class, always available — the old
        # `HAS_PORTFOLIO_RISK` gate reported on modules.portfolio_risk_manager,
        # a different implementation that this module's class definition had
        # already shadowed and which was never actually constructed.
        self.portfolio_risk_manager = PortfolioExposureManager(self.api)
        modules.append("Portfolio Risk")
        
        # Single concise startup message
        modules_str = " | ".join(modules) if modules else "Core"
        logger.info(f"✓ QUANTA Elite ready [{modules_str}]")
        
        # [BACKTEST] Signal accuracy tracking for calibration
        self._historical_accuracy = {
            'BUY': {'total': 0, 'correct': 0},
            'SELL': {'total': 0, 'correct': 0},
            'HOLD': {'total': 0, 'correct': 0}
        }
        
        # Position tracking for portfolio risk management
        self._tracked_positions: List[Dict] = []
        self._load_tracked_positions()
        
        logger.info("QUANTA Elite Analyzer v2.0 initialized")

    def _reconcile_sentiment_provenance(self, result: AnalysisResult) -> None:
        """Persist the headline relevance facts used by every scoring consumer."""
        sentiment = result.sentiment or SentimentData()
        articles = list(getattr(sentiment, 'top_articles', []) or [])
        sector_map = PeerAnalyzer.SECTOR_MAPPINGS.get(result.sector, {})
        peers = list(sector_map.get('peers', []) or [])
        detail = _classify_news_relevance(
            articles, result.ticker, result.company_name, peer_tickers=peers
        )
        sentiment.relevance_detail = detail
        sentiment.provenance_computed = True
        sentiment.has_direct_news = bool(detail.get('has_direct_news'))
        sentiment.direct_news_count = int(detail.get('direct_count', 0) or 0)
        sentiment.peer_news_count = int(detail.get('peer_count', 0) or 0)
        sentiment.macro_news_count = int(detail.get('macro_count', 0) or 0)

        if sentiment.has_direct_news:
            source = 'company'
        elif sentiment.peer_news_count:
            source = 'peer'
        elif sentiment.macro_news_count:
            source = 'macro'
        else:
            source = 'none'
        sentiment.sentiment_source = source

        weighted = float(detail.get('weighted_sentiment_score', 0.0) or 0.0)
        # If no direct score could be computed, retain only a deliberately
        # dampened remnant of the aggregate feed sentiment.
        if not sentiment.has_direct_news and weighted == 0.0:
            weighted = float(sentiment.sentiment_score or 0.0) * Config.SENTIMENT_NO_DIRECT_DAMPENER
        sentiment.weighted_sentiment_score = max(-1.0, min(1.0, weighted))
        result.sentiment = sentiment

        result.unexplained_move = bool(
            abs(float(result.day_change_pct or 0.0)) >= Config.UNEXPLAINED_MOVE_THRESHOLD_PCT
            and not sentiment.has_direct_news
        )
        result.unexplained_move_note = (
            f"{result.ticker} moved {result.day_change_pct:+.1f}% with no direct "
            "company headline found; cause is not independently verified."
            if result.unexplained_move else ""
        )

    @staticmethod
    def _price_data_age_days(data: pd.DataFrame, now: Any = None) -> Optional[int]:
        """Age of the newest daily bar in calendar days, or None if unknown."""
        if data is None or data.empty:
            return None
        try:
            latest = pd.Timestamp(data.index[-1])
            reference = pd.Timestamp(now if now is not None else datetime.now(timezone.utc))
            return max(0, (reference.date() - latest.date()).days)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

    @staticmethod
    def _price_data_freshness(data: pd.DataFrame, now: Any = None):
        """Apply the one market-session freshness policy used by Decisions."""
        from engine.decision import assess_price_freshness

        if data is None or data.empty:
            return assess_price_freshness(None, now=now)
        try:
            last_bar = pd.Timestamp(data.index[-1]).date()
        except (TypeError, ValueError, IndexError, OverflowError):
            last_bar = None
        return assess_price_freshness(last_bar, now=now)
    
    def _load_tracked_positions(self) -> None:
        """Load tracked positions from signal history for portfolio risk checks."""
        try:
            stats_path = TRACKED_POSITIONS_FILE
            if os.path.exists(stats_path):
                with open(stats_path, 'r') as f:
                    self._tracked_positions = json.load(f)
            else:
                self._tracked_positions = []
        except Exception as e:
            logger.debug(f"Error loading tracked positions: {e}")
            self._tracked_positions = []
    
    def _save_tracked_positions(self) -> None:
        """Persist tracked positions to disk."""
        try:
            stats_path = TRACKED_POSITIONS_FILE
            with open(stats_path, 'w') as f:
                json.dump(self._tracked_positions, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"Error saving tracked positions: {e}")
    
    def _get_tracked_positions(self) -> List[Dict]:
        """Return the explicitly declared holdings, or an empty list.

        [F02] This used to reconstruct a portfolio from the signal tracker's
        BUY records at an assumed 100 shares per name. A research observation
        is not an execution and a guessed share count is not a holding, so
        that produced a fabricated account state which then drove real sizing
        decisions. Holdings now come only from `update_tracked_position` (or a
        persisted snapshot); an empty list means "holdings unknown", which
        `check_position_limits` reports as `holdings_known=False` so the
        result is labelled hypothetical rather than passing a portfolio check
        it never performed.
        """
        return self._tracked_positions
    
    def update_tracked_position(self, ticker: str, value: float, sector: str) -> None:
        """Add or update a tracked position."""
        for pos in self._tracked_positions:
            if pos['ticker'] == ticker:
                pos['value'] = value
                pos['sector'] = sector
                self._save_tracked_positions()
                return
        self._tracked_positions.append({'ticker': ticker, 'value': value, 'sector': sector})
        self._save_tracked_positions()
    
    def remove_tracked_position(self, ticker: str) -> None:
        """Remove a tracked position."""
        self._tracked_positions = [p for p in self._tracked_positions if p['ticker'] != ticker]
        self._save_tracked_positions()

    @staticmethod
    def _rescale_position_sizing(sizing: 'PositionSizing', max_position_value: float,
                                 price: float, account_size: float) -> None:
        """Resize a proposal to ``max_position_value``, in place and coherently.

        [F02] The old enforcement multiplied `recommended_shares` and
        `position_value` by 0.5 and left `portfolio_weight`, `dollar_risk` and
        `risk_percent` describing the original, larger position — so the
        report contradicted itself. Every derived field is recomputed here from
        the share count that actually fits, and a position that can no longer
        buy a whole share is marked non-viable rather than silently rounded.
        """
        if price <= 0:
            return

        old_shares = int(sizing.recommended_shares or 0)
        new_shares = min(old_shares, int(max_position_value / price))
        if new_shares >= old_shares:
            return

        scale = (new_shares / old_shares) if old_shares > 0 else 0.0
        sizing.recommended_shares = new_shares
        sizing.position_value = new_shares * price
        sizing.dollar_risk = (sizing.dollar_risk or 0.0) * scale
        if account_size > 0:
            sizing.portfolio_weight = sizing.position_value / account_size
            sizing.risk_percent = sizing.dollar_risk / account_size
        else:
            sizing.portfolio_weight = 0.0
            sizing.risk_percent = 0.0
        sizing.kelly_optimal = (sizing.kelly_optimal or 0.0) * scale

        if new_shares < 1:
            sizing.is_viable = False
        sizing.recommendation = (
            f"Proposed allocation: {new_shares} shares ({sizing.position_value:,.2f}); reduced by portfolio limits."
            if new_shares else "No new position: portfolio exposure limits leave no whole-share allocation."
        )

    def _apply_portfolio_exposure_limits(self, result: 'AnalysisResult', ticker: str,
                                         account_size: float) -> None:
        """Enforce sector and correlation limits on the proposed position.

        [F02] The proposed order is now part of the exposure calculation, the
        verdict binds (rather than only being consulted when `approved` is
        already False), and an unknown account is reported as hypothetical
        instead of silently passing.
        """
        sizing = result.position_sizing
        proposed = {
            'ticker': ticker,
            'value': sizing.position_value,
            'sector': result.sector or 'Unknown',
        }

        held = self._get_tracked_positions()

        check = self.portfolio_risk_manager.check_position_limits(
            new_position=proposed,
            existing_positions=held,
            portfolio_value=account_size,
        )
        result.portfolio_risk_check = check

        if not check.get('holdings_known', False):
            sizing.warnings.append(
                "Holdings unknown — sizing is a hypothetical allocation, not a "
                "portfolio-checked order."
            )

        if check.get('approved', True):
            return

        for warning in check.get('warnings', []):
            result.key_insights.append(f"⚠️ Portfolio Risk: {warning}")
            sizing.warnings.append(warning)

        max_value = check.get('adjustments', {}).get('max_position_value')
        entry_price = result.trade_setup.entry_price or result.current_price
        if max_value is None:
            # Nothing fits: refuse the position rather than half-sizing it.
            sizing.recommended_shares = 0
            sizing.position_value = 0.0
            sizing.portfolio_weight = 0.0
            sizing.dollar_risk = 0.0
            sizing.risk_percent = 0.0
            sizing.kelly_optimal = 0.0
            sizing.recommendation = "No new position: portfolio exposure limits prohibit this allocation."
            sizing.is_viable = False
            logger.info(f"{ticker}: Position refused by portfolio exposure limits")
        else:
            self._rescale_position_sizing(
                sizing, max_value, entry_price, account_size
            )
            logger.info(
                f"{ticker}: Position reduced to ${sizing.position_value:,.0f} "
                f"by portfolio exposure limits"
            )
    
    def analyze(self, ticker: str, account_size: float = 100000,
                fast_mode: bool = False, include_insider: bool = False,
                market: Optional[str] = None) -> AnalysisResult:
        """Run comprehensive stock analysis.

        [PERF-FASTMODE] When ``fast_mode`` is True the network-heavy optional
        modules (news/sentiment, insider activity, analyst consensus,
        catalysts, peer comparison) are SKIPPED and replaced with neutral
        defaults so the first visible tear sheet renders in seconds — critical
        for slow international symbols (e.g. ``.NS`` / ``.L``) where those Yahoo
        endpoints are empty yet take 20-30s each to time out. The result is
        flagged ``fast_mode=True`` and the recommendation is PRELIMINARY; the
        caller re-runs with ``fast_mode=False`` for the validated verdict.
        ``include_insider=True`` fetches insider activity in fast mode for
        scanner reconciliation without running the other optional modules.
        """
        logger.info(f"Starting analysis for {ticker}{' (fast mode)' if fast_mode else ''}")
        start_time = time.time()
        
        result = AnalysisResult()
        result.ticker = ticker.upper()
        result.requested_symbol = ticker.upper()
        result.fast_mode = bool(fast_mode)
        result.retrieval_timestamp = datetime.now(timezone.utc).isoformat()
        result.analysis_timestamp = result.retrieval_timestamp

        # [PROFILING] Lightweight per-stage timer. Records into
        # result.stage_timings as {stage, duration_ms, cached, provider, status}
        # so the deep-dive flow can be profiled without external tooling.
        def _stage(name, t0, *, cached=False, provider="-", status="ok"):
            result.stage_timings.append({
                "stage": name,
                "duration_ms": round((time.time() - t0) * 1000.0, 1),
                "cached": bool(cached),
                "provider": provider,
                "status": status,
            })
        
        try:
            # 1. Fetch price data
            _t = time.time()
            _price_cached = self.cache.get(f"price_{ticker}") is not None
            data, data_status, fetch_meta = (
                self._fetch_price_data_classified(ticker, market=market) if market
                else self._fetch_price_data_classified(ticker)
            )
            result.data_status = data_status
            # Every later fetch (profile, fundamentals, analysts, news) must use
            # the listing the prices came from. Using the typed symbol after
            # "RELIANCE" resolved to RELIANCE.NS scored the NSE prices against
            # an empty US profile and gave the same company a different call.
            _resolved = str(fetch_meta.get("variant") or "").upper()
            if _resolved and _resolved != ticker.upper():
                ticker = _resolved
                result.ticker = _resolved
            result.symbol_note = fetch_meta.get("resolution_note") or ""
            result.history_bars = int(fetch_meta.get("rows") or 0)
            result.first_bar_date = fetch_meta.get("first_bar") or ""
            result.last_bar_date = fetch_meta.get("last_bar") or ""
            result.data_quality_status = (
                "ok" if data_status == DataStatus.OK else str(data_status)
            )
            _stage("price_history", _t, cached=bool(fetch_meta.get("from_cache")),
                   provider="yfinance",
                   status="ok" if data_status == DataStatus.OK else data_status)

            # [DATA-RELIABILITY] Log a structured diagnostic for every fetch so
            # C / FFBC-style mislabels can be traced (provider, range, rows,
            # cache, rate-limit, exception).
            logger.info(
                "fetch %s -> status=%s rows=%s variant=%s range=%s cache=%s "
                "rate_limited=%s exception=%s",
                ticker, data_status, fetch_meta.get("rows"), fetch_meta.get("variant"),
                fetch_meta.get("range"), fetch_meta.get("from_cache"),
                fetch_meta.get("rate_limited"), fetch_meta.get("exception"),
            )

            if data is None or len(data) < Config.MIN_DATA_POINTS:
                # Separate a TRUE invalid ticker from a transient data failure.
                # Only a genuinely non-existent symbol is marked INVALID; every
                # other case is "not independently confirmed / data unavailable"
                # so a valid name (e.g. C, FFBC) is never falsely binned.
                if data_status == DataStatus.INVALID_TICKER:
                    result.is_invalid = True
                    reason = (
                        f"No valid historical data for {ticker} "
                        f"(need ≥{Config.MIN_DATA_POINTS} bars)"
                    )
                    result.invalid_reasons.append(reason)
                    result.data_status_reason = reason
                    if HAS_DECISION_CORE:
                        result.final_action = FinalAction.INVALID.value
                        result.final_action_reasons = list(result.invalid_reasons)
                    result.key_insights.append(
                        f"INVALID TICKER / NO DATA — {ticker} did not pass the "
                        f"data-validation gate."
                    )
                    logger.warning(f"INVALID ticker {ticker}: no historical data")
                    return result

                # Real ticker but the deep analyzer could not validate it for a
                # DATA reason. NOT invalid, NOT a bearish disagreement.
                rows = fetch_meta.get("rows") or 0
                if data_status == DataStatus.PROVIDER_RATE_LIMITED:
                    reason = (
                        f"Deep analyzer could not fetch historical bars for {ticker} "
                        f"(Yahoo Finance rate-limited the request)."
                    )
                elif data_status == DataStatus.INSUFFICIENT_HISTORY:
                    since = f" since {result.first_bar_date}" if result.first_bar_date else ""
                    reason = (
                        f"Only {rows} daily bar(s) of history{since} for {ticker}; the model "
                        f"needs at least {Config.MIN_DATA_POINTS} to score trend, volatility and risk."
                    )
                    # The instrument is real: keep its identity and last price so
                    # the page can show what is known instead of a blank error.
                    try:
                        _stock = yf.Ticker(ticker)
                        data.attrs['info'] = self._normalize_info(_stock.info, _stock, data, ticker)
                        result = self._get_company_info(ticker, data, result)
                    except Exception as _ie:  # noqa: BLE001
                        logger.debug(f"{ticker}: profile for short-history listing failed: {_ie}")
                else:
                    data_status = DataStatus.PROVIDER_DATA_UNAVAILABLE
                    result.data_status = data_status
                    reason = (
                        f"Deep analyzer could not fetch enough historical bars for "
                        f"{ticker} (data provider issue)."
                    )
                result.is_invalid = False
                result.data_status_reason = reason
                # No verdict — leave final_action empty so the reconciler treats
                # this as "not independently confirmed", not INVALID.
                result.final_action = ""
                result.final_action_reasons = [reason]
                result.invalid_reasons = []
                result.key_insights.append(
                    f"NOT CONFIRMED — {reason} The scanner signal was not "
                    f"independently validated."
                )
                logger.warning(
                    "NOT CONFIRMED %s: status=%s (%s)", ticker, data_status, reason
                )
                return result


            # 2. Get company info
            _t = time.time()
            result = self._get_company_info(ticker, data, result)
            _stage("company_info", _t, cached=True, provider="yfinance")

            # A real, priced instrument the equity model does not describe.
            from engine.symbols import unsupported_reason
            _why_unsupported = unsupported_reason(result.quote_type)
            if _why_unsupported:
                result.data_status = DataStatus.UNSUPPORTED_INSTRUMENT
                result.data_quality_status = str(DataStatus.UNSUPPORTED_INSTRUMENT)
                result.data_status_reason = _why_unsupported
                result.final_action = ""
                result.final_action_reasons = [_why_unsupported]
                return result

            # Bars exist but the company-info fetch failed: a data failure,
            # NOT an invalid ticker (see _fetch_price_data_classified).
            if getattr(data, 'attrs', {}).get('info_unavailable'):
                data_status = (
                    DataStatus.PROVIDER_RATE_LIMITED if fetch_meta.get("rate_limited")
                    else DataStatus.PROVIDER_DATA_UNAVAILABLE
                )
                reason = (
                    f"Deep analyzer fetched price history for {ticker} but the company "
                    f"profile request failed"
                    + (" (Yahoo Finance rate-limited it)." if fetch_meta.get("rate_limited") else ".")
                )
                result.data_status = data_status
                result.data_quality_status = str(data_status)
                result.is_invalid = False
                result.invalid_reasons = []
                result.data_status_reason = reason
                result.final_action = ""
                result.final_action_reasons = [reason]
                result.key_insights.append(
                    f"NOT CONFIRMED — {reason} The scanner signal was not independently validated."
                )
                logger.warning("NOT CONFIRMED %s: status=%s (%s)", ticker, data_status, reason)
                return result

            # A long-enough history can still be unusably stale (suspended,
            # delisted, or a stale cache). Use the same completed-session policy
            # that is published on Decision; calendar-day cutoffs disagree over
            # weekends and holidays and previously let four-session-old BUYs pass.
            price_freshness = self._price_data_freshness(data)
            if price_freshness.status != "fresh":
                result.data_status = DataStatus.STALE_DATA
                result.data_quality_status = "stale"
                result.data_status_reason = (
                    price_freshness.notes[0]
                    if price_freshness.notes else "Latest daily bar date is unknown."
                )
                result.final_action = ""
                result.final_action_reasons = [result.data_status_reason]
                result.key_insights.append(
                    "NOT CONFIRMED — stale market data; no recommendation generated."
                )
                return result

            # [DECISION-CORE] hard validation gate — must pass before any scoring
            if HAS_DECISION_CORE:
                vrep = validate_ticker_data(
                    ticker=result.ticker,
                    current_price=result.current_price,
                    history_length=len(data),
                    market_cap=result.market_cap,
                    company_name=result.company_name,
                    min_history=Config.MIN_DATA_POINTS,
                    quote_type=result.quote_type,
                    is_etf=result.is_etf,
                    net_assets=result.net_assets,
                )
                if not vrep.is_valid:
                    result.is_invalid = True
                    result.invalid_reasons = list(vrep.reasons)
                    result.final_action = FinalAction.INVALID.value
                    result.final_action_reasons = list(vrep.reasons)
                    result.key_insights.append(
                        "INVALID TICKER / NO DATA — " + "; ".join(vrep.reasons)
                    )
                    logger.warning(f"INVALID ticker {ticker}: {vrep.reasons}")
                    return result
            
            # 2.5. [COMPLIANCE] Check circuit breaker for NSE stocks
            if self.compliance and ticker.upper().endswith(('.NS', '.BO')):
                prev_close = data['Close'].iloc[-2] if len(data) > 1 else result.current_price
                circuit_breaker = self.compliance.check_circuit_breaker(
                    ticker, result.current_price, prev_close
                )
                if circuit_breaker:
                    result.key_insights.append(
                        f"⚠️ SEBI Circuit Breaker L{circuit_breaker.value} triggered - trading may be halted"
                    )
            
            # 3. Run parallel analysis
            _t = time.time()
            result = self._run_parallel_analysis(
                ticker, data, result, fast_mode=fast_mode, include_insider=include_insider
            )
            _stage("parallel_analysis", _t, provider="yfinance")

            # Relevance is resolved before scoring so macro/peer news can never
            # masquerade as company-specific evidence.
            self._reconcile_sentiment_provenance(result)

            # Benchmark-relative context (reuses the benchmark series the beta
            # estimate already fetched; no extra provider call).
            try:
                from engine.relative_performance import relative_performance
                _bench = self.risk_analyzer._get_benchmark_returns(ticker)
                result.relative_performance = relative_performance(
                    data['Close'], _bench, self.risk_analyzer._benchmark_symbol(ticker))
            except Exception as _rpe:  # noqa: BLE001
                logger.debug(f"{ticker}: relative performance unavailable: {_rpe}")

            # [ASSET-TYPE] Classify now that sector / risk (volatility, beta)
            # are populated. Drives conditional UI sections downstream.
            try:
                _vol = getattr(result.risk, 'annualized_volatility', None) if result.risk else None
                _beta = getattr(result.risk, 'beta', None) if result.risk else None
                _cls = classify_asset_type(
                    is_invalid=result.is_invalid,
                    is_etf=result.is_etf,
                    quote_type=result.quote_type,
                    sector=result.sector,
                    industry=getattr(result, 'industry', '') or '',
                    country=getattr(result, 'country', '') or '',
                    company_name=result.company_name,
                    ticker=result.ticker,
                    annualized_volatility=_vol,
                    beta=_beta,
                )
                result.asset_type = _cls['asset_type']
                result.is_adr = _cls['is_adr']
                result.is_financial = _cls['is_financial']
                result.is_high_volatility = _cls['is_high_volatility']
            except Exception as _ce:
                logger.debug(f"asset-type classification failed for {ticker}: {_ce}")
            
            # 3.5. [CATALYST] Scan news for actionable catalysts
            if self.catalyst_scanner and result.sentiment:
                try:
                    # Get headlines from sentiment data
                    headlines = getattr(result.sentiment, 'recent_headlines', [])
                    if not headlines and hasattr(result.sentiment, 'news_items'):
                        headlines = [n.get('title', '') for n in result.sentiment.news_items[:20]]
                    
                    if headlines:
                        catalysts = self.catalyst_scanner.scan_headlines(ticker, headlines)
                        actionable = [c for c in catalysts if c.is_actionable]
                        
                        if actionable:
                            # Add to key insights
                            for cat in actionable[:3]:  # Top 3 catalysts
                                result.key_insights.append(
                                    f"📰 {cat.type.value.upper()}: {cat.headline[:50]}..."
                                )
                            logger.info(f"📰 {ticker}: {len(actionable)} catalyst(s) detected")
                except Exception as e:
                    logger.debug(f"{ticker}: Catalyst scan failed - {e}")
            
            
            # 4. Determine market regime FIRST (needed for Monte Carlo)
            result.market_regime = self._determine_regime(result.technical, result.risk)
            
            # 5. Run Monte Carlo simulation BEFORE signal generation (for R/R check)
            _t = time.time()
            has_catalyst = (result.catalysts.near_term_catalyst if result.catalysts else False)
            mc_seed_material = (
                f"{result.ticker}|{result.market_timestamp}|"
                f"{result.current_price:.8f}|{Config.MC_DAYS_FORWARD}"
            )
            mc_seed = int(hashlib.sha256(
                mc_seed_material.encode('utf-8')
            ).hexdigest()[:8], 16)
            result.monte_carlo = self.monte_carlo.simulate(
                result.current_price, result.risk,
                regime=result.market_regime,
                has_catalyst=has_catalyst,
                annual_drift_cap=Config.MC_MAX_ANNUAL_DRIFT,
                seed=mc_seed,
            )
            _stage("monte_carlo", _t, provider="local")
            
            # 5.5. [ENHANCED] Calculate composite price targets with all data available
            _t = time.time()
            result.valuation = self.valuation_analyzer.calculate_composite_targets(
                result.current_price, result.valuation, result.market_intel,
                result.monte_carlo, result.technical
            )

            # Reconcile the short-horizon distribution with the independently
            # derived 12-month base case. Hot trailing momentum may not imply a
            # 30-day median whose annualized return exceeds the base target by
            # more than the configured tolerance.
            if (result.current_price > 0 and result.valuation.target_base > 0
                    and result.monte_carlo.median_price > 0):
                mc_ann = (
                    (result.monte_carlo.median_price / result.current_price)
                    ** (252 / Config.MC_DAYS_FORWARD) - 1
                )
                target_ann = result.valuation.target_base / result.current_price - 1
                allowed_ann = target_ann + Config.MC_TARGET_TOLERANCE
                if mc_ann > allowed_ann:
                    result.monte_carlo = self.monte_carlo.simulate(
                        result.current_price, result.risk,
                        days=Config.MC_DAYS_FORWARD,
                        regime=result.market_regime,
                        has_catalyst=has_catalyst,
                        annual_drift_cap=max(0.0, allowed_ann),
                        seed=mc_seed,
                    )
                    result.valuation = self.valuation_analyzer.calculate_composite_targets(
                        result.current_price, result.valuation, result.market_intel,
                        result.monte_carlo, result.technical,
                    )
            _stage("valuation_targets", _t, provider="local")
            
            # 6. Generate signal WITH Monte Carlo data for R/R asymmetry check
            _t = time.time()
            signal, confidence, scores, signal_explanation = self.signal_generator.generate(
                result.technical, result.fundamental, result.risk,
                result.sentiment, result.insider, result.market_intel, result.valuation,
                regime=result.market_regime,
                monte_carlo=result.monte_carlo, current_price=result.current_price
            )
            _stage("signal_generation", _t, provider="local")
            
            result.signal = signal
            result.confidence_score = confidence
            result.scores = scores
            result.signal_explanation = signal_explanation
            
            # 6.5. Set confidence level
            if confidence >= 80:
                result.confidence_level = ConfidenceLevel.VERY_HIGH
            elif confidence >= 65:
                result.confidence_level = ConfidenceLevel.HIGH
            elif confidence >= 50:
                result.confidence_level = ConfidenceLevel.MEDIUM
            elif confidence >= 35:
                result.confidence_level = ConfidenceLevel.LOW
            else:
                result.confidence_level = ConfidenceLevel.VERY_LOW
            
            # 7. Generate trade setup
            _t = time.time()
            result.trade_setup = self.trade_setup_generator.generate(
                result.current_price, signal, result.technical, result.risk, confidence,
                currency_symbol=result.currency_symbol,
                valuation=result.valuation,
            )
            _stage("trade_setup", _t, provider="local")

            # [DECISION-CORE] R/R / risk-grade / score gating is handled
            # centrally by `apply_risk_overrides` (called from
            # `resolve_final_action` further down). The previous inline
            # "R/R below RR.RESEARCH_MIN → HOLD" override was duplicating that logic with
            # different semantics and rewriting `signal_explanation` with
            # text that the final FinalAction no longer matched.
            # Keep `signal_explanation` informational; the engine output
            # is the source of truth.

            # 8. Calculate position sizing
            _t = time.time()
            if result.trade_setup.entry_price > 0 and result.trade_setup.stop_loss > 0:
                result.position_sizing = self.position_sizer.calculate(
                    signal, result.trade_setup.entry_price, result.trade_setup.stop_loss,
                    confidence, result.risk, account_size,
                    monte_carlo=result.monte_carlo,
                    technical=result.technical,
                    target=(result.trade_setup.target_base
                            if getattr(result.trade_setup, 'has_long_reward', True) else 0.0),
                )
            _stage("position_sizing", _t, provider="local")
            
            # 8.5. [PORTFOLIO RISK] Enforce VaR and correlation limits
            # [PERF-FASTMODE] The correlation check fetches a full 1y price
            # history for EVERY tracked position (derived from accumulated BUY
            # signal history, so it grows with usage) — under the global yf
            # throttle this dominated deep-dive wall-time (~11s) and got slower
            # the more the app was used. Skip it in fast mode (consistent with
            # the other network-heavy optional modules) and bound the position
            # list in full mode so it can never explode.
            if self.portfolio_risk_manager and result.position_sizing and not fast_mode:
                try:
                    self._apply_portfolio_exposure_limits(
                        result, ticker, account_size
                    )
                except Exception as e:
                    logger.debug(f"{ticker}: Portfolio risk check failed - {e}")

            # 8.6. [F02] Research does NOT write executed-trade audit records.
            # `record_trade` asserts that a fill happened; an analysis run has
            # no order and no fill to cite. The call that used to live here
            # fabricated SEBI/FINRA audit-trail entries for trades nobody
            # placed (and, because it passed `signal_type=`/`shares=` to a
            # function taking `signal=`/`quantity=`, it had been raising
            # TypeError into a debug log the whole time). Recording belongs to
            # an execution path with a broker order id.

            # [DECISION-CORE] resolve a single FinalAction before generating any
            # sizing, plan or narrative that a user could act on.
            _dt = time.time()
            if HAS_DECISION_CORE:
                try:
                    rr = result.trade_setup.risk_reward_ratio if result.trade_setup else 0.0
                    stop = result.trade_setup.stop_loss if result.trade_setup else 0.0
                    # `TradeSetup` exposes `target_base` (the blended 12-month
                    # price target). The previous reference to `target_1` did
                    # not exist on the dataclass, which raised `AttributeError`
                    # and silently bypassed the decision-core resolver for
                    # every ticker (leaving `final_action` blank). Use the
                    # real field so the shared resolver actually runs.
                    # Reward/risk and the price gates read the HORIZON target
                    # (the 12-month valuation target pro-rated to the
                    # Decision's holding period), never the 12-month level.
                    target = result.trade_setup.horizon_target if result.trade_setup else 0.0
                    # `result.scores` is a ScoreBreakdown dataclass, not a dict.
                    # The composite is on a 0–100 scale (see ScoreBreakdown).
                    if result.scores is not None and hasattr(result.scores, 'composite_score'):
                        _composite = float(result.scores.composite_score or 50.0)
                    elif isinstance(result.scores, dict):
                        _composite = float(result.scores.get('composite_score',
                                            result.scores.get('composite', 50.0)) or 50.0)
                    else:
                        _composite = 50.0
                    # ── Qualitative red-flag overrides (shared with the scanner
                    #    through the decision-core so both engines agree). ──
                    _flags = _extract_qualitative_red_flags(result)
                    _bear_rev = _flags["bearish_reversal"]
                    _bear_name = _flags["bearish_reversal_name"]
                    _timing_wait = _flags["timing_wait"]
                    _buy_now = _flags["buy_now"]
                    _dcf_over = _flags["dcf_overvaluation_pct"]
                    _heavy_insider = _flags["heavy_insider_selling"]
                    di = DecisionInputs(
                        ticker=result.ticker,
                        composite_score=_composite,
                        risk_grade=(result.risk.risk_grade if result.risk else 'C'),
                        risk_reward_ratio=float(rr or 0.0),
                        current_price=float(result.current_price or 0.0),
                        stop_loss=float(stop or 0.0),
                        target_price=float(target or 0.0),
                        has_existing_position=result.has_existing_position,
                        is_valid=not result.is_invalid,
                        buy_now=_buy_now,
                        timing_wait=_timing_wait,
                        bearish_reversal=_bear_rev,
                        bearish_reversal_name=_bear_name,
                        dcf_overvaluation_pct=_dcf_over,
                        heavy_insider_selling=_heavy_insider,
                    )
                    dec = resolve_final_action(di)

                    # [T5 FIX] The core's verdict used to be adopted
                    # unconditionally. `_candidate_from_score` maps
                    # composite_score alone, so that let the core PROMOTE a
                    # name past the SignalGenerator's own multi-factor
                    # judgement — a HOLD scoring 65+ silently became a BUY with
                    # none of the technical confirmation the signal generator
                    # requires. The core is a conservatism ratchet: it may move
                    # the recommendation DOWN the conviction ladder, never up.
                    _core_action = dec.final_action.value
                    _scanner_action = (
                        signal_to_final_action(
                            result.signal.value, result.has_existing_position
                        ).value
                        if signal_to_final_action is not None else _core_action
                    )
                    _core_rank = _ACTION_RANK.get(_core_action, 99)
                    _sig_rank = _ACTION_RANK.get(_scanner_action, 99)

                    # Codes of the generator's own downgrades (target below
                    # price, analyst downside, RSI exhaustion, risk grade...).
                    _gen_codes = list(getattr(result.signal_explanation, 'override_codes', []) or [])
                    if _core_rank <= _sig_rank:
                        result.final_action = _core_action
                        result.final_action_reasons = list(dec.reasons)
                        result.final_action_codes = list(dec.blocking_codes)
                        result.final_blocking_reasons = list(dec.blocking_reasons)
                        result.final_stance = dec.stance
                    else:
                        result.final_action = _scanner_action
                        from engine.evidence import stance_for
                        result.final_stance = stance_for(FinalAction(_scanner_action))
                        # Attribute the downgrade to the generator rule that
                        # applied it; the generic code only when none did.
                        _extra = [c for c in _gen_codes if c not in dec.override_codes]
                        _generator_texts = list(getattr(result.signal_explanation,
                                                        'overrides_triggered', []) or [])
                        if not _extra:
                            _extra = ["SIGNAL_CONFIRMATION_NOT_MET"]
                            _generator_texts = [
                                "Signal generator confirmations were not met; Decision was not upgraded."
                            ]
                        result.final_action_reasons = list(dec.reasons) + [
                            f"Decision core proposed {_core_action} on composite score "
                            f"alone; held at {_scanner_action} by the signal generator "
                            f"({', '.join(_extra)}; never upgrade)."
                        ]
                        result.final_action_codes = list(dec.override_codes) + _extra
                        result.final_blocking_reasons = list(dec.overrides_applied) + _generator_texts
                        logger.debug(
                            f"{ticker}: suppressed core upgrade "
                            f"{_scanner_action} → {_core_action}"
                        )

                    # Keep legacy `signal` aligned with the resolved action
                    # so summary tables and detail panels cannot disagree.
                    new_sig = _FINAL_ACTION_TO_SIGNAL.get(result.final_action)
                    if new_sig is not None and new_sig != result.signal:
                        logger.info(
                            f"{ticker}: legacy signal {result.signal.value} → final action "
                            f"{result.final_action} (reasons: {result.final_action_reasons})"
                        )
                        result.signal = new_sig
                    if _scanner_action != result.final_action:
                        result.final_action_transitions.append({
                            "from": _scanner_action,
                            "to": result.final_action,
                            "codes": list(result.final_action_codes),
                        })
                except Exception as _e:
                    # Fail CLOSED. A resolver error used to leave final_action
                    # blank (logged at DEBUG), so no Decision was published
                    # while the legacy signal and its sizing stayed on the
                    # result. No evaluated rules means no new money.
                    logger.error(f"FinalAction resolve failed for {ticker}: {type(_e).__name__}: {_e}")
                    _fail_closed_decision(result, f"{type(_e).__name__}: {_e}")
            elif not result.final_action:
                _fail_closed_decision(result, "decision core unavailable")
            _fold_strong_buy(result, self.position_sizer, account_size)
            _apply_corporate_events(result, ticker)
            _stage("decision_core", _dt, provider="local")

            # FinalAction is now authoritative. Zero legacy executable fields
            # before deriving commentary so WATCHLIST can never retain a funded
            # size or a prose instruction to build a position.
            enforce_final_decision_contract(result)

            # 9-11. Generate interpretation and guidance from the final state.
            _t = time.time()
            result.risk_warnings = self._generate_warnings(result)
            result.thesis = _sanitize_thesis_text(self.thesis_generator.generate(result), result)
            result.key_insights = self._generate_insights(result)
            result.action_items = self._generate_action_items(result)
            result.recommendation_text = self._generate_recommendation(result)
            _stage("narrative", _t, provider="local")
            
            # 12. Run peer comparison analysis
            # [PERF-FASTMODE] Peer comparison fetches `.info` for every peer
            # (a separate throttled Yahoo round-trip each) — by far the most
            # expensive optional module. Skip it entirely in fast mode.
            if not fast_mode and result.sector and result.sector != "Unknown":
                result.peer_comparison = self.peer_analyzer.analyze(
                    ticker, result.sector, result.fundamental,
                    industry=getattr(result, 'industry', '') or '',
                )
            
            # 13. Record only the authoritative, full-mode decision. The
            # ledger rejects live partial or stale bars and resolves exact
            # T+N prices later, outside the request path.
            _t = time.time()
            if not fast_mode:
                record_authoritative_signal(result)
            decision = decision_for_analysis(result)
            if decision is not None:
                result.decision = decision.to_dict()
            _stage("record_signal", _t, provider="local")

            if fast_mode:
                result.data_quality_status = "preliminary"
            elif (result.scores and str(result.scores.data_quality_note).upper().startswith("LOW")):
                result.data_quality_status = "warning"
            else:
                result.data_quality_status = "ok"
            
            # Outcome backfill is explicit: ``python -m tracking resolve``.
            # No background request thread can use a later spot quote as T+N.

            elapsed = time.time() - start_time
            result.stage_timings.append({
                "stage": "TOTAL",
                "duration_ms": round(elapsed * 1000.0, 1),
                "cached": False,
                "provider": "-",
                "status": "ok",
            })
            # [PROFILING] Emit a compact stage table at DEBUG so slow stages
            # are visible without external profilers.
            try:
                _hdr = f"{'Stage':<20}{'Duration':>11}  {'Cached':<7}{'Provider':<10}Status"
                _lines = [_hdr, "-" * len(_hdr)]
                for s in result.stage_timings:
                    _lines.append(
                        f"{s['stage']:<20}{s['duration_ms']:>9.1f}ms  "
                        f"{('yes' if s['cached'] else 'no'):<7}"
                        f"{s['provider']:<10}{s['status']}"
                    )
                logger.debug("Deep-dive profile for %s:\n%s", ticker, "\n".join(_lines))
            except Exception:
                pass
            logger.info(f"Analysis complete for {ticker} in {elapsed:.1f}s: {signal.value}")
            
        except Exception as e:
            logger.error(f"Analysis error for {ticker}: {e}")
            result.key_insights.append(f"Analysis error: {str(e)}")
        
        return result
    
    def _fetch_price_data(self, ticker: str) -> Optional[pd.DataFrame]:
        """Fetch historical price data with caching (back-compat wrapper).

        Returns the DataFrame on success (>= MIN_DATA_POINTS bars) or None. The
        richer classification (rate-limit vs invalid ticker vs transient) is
        available via :meth:`_fetch_price_data_classified`.
        """
        data, status, _meta = self._fetch_price_data_classified(ticker)
        if status == DataStatus.OK:
            return data
        return None

    @staticmethod
    def _looks_like_rate_limit(exc: BaseException) -> bool:
        """True if ``exc`` looks like a provider throttle (429 / YFRateLimitError)."""
        name = type(exc).__name__.lower()
        if "ratelimit" in name:
            return True
        low = (str(exc) or "").lower()
        return (
            "429" in low or "too many request" in low or "rate limit" in low
            or "rate-limit" in low or "ratelimit" in low or "throttle" in low
        )

    def _ticker_metadata_exists(self, stock: "yf.Ticker") -> bool:
        """Best-effort check that a symbol is a *real*, listed instrument even
        when its price history could not be fetched. A real ticker exposes
        identifying metadata (shortName / longName / a market price / quoteType)
        via ``.info``; a bogus symbol returns an empty/sentinel payload.
        """
        try:
            info = stock.info or {}
        except Exception:
            return False
        if not isinstance(info, dict) or not info:
            return False
        # Yahoo answers an unknown or delisted symbol with a sentinel payload
        # (quoteType "NONE", no name, no price). That is not evidence the
        # instrument exists, so quoteType alone never counts.
        for key in ("shortName", "longName", "regularMarketPrice",
                    "previousClose", "marketCap"):
            val = info.get(key)
            if val not in (None, "", 0):
                return True
        return False

    @staticmethod
    def _normalize_info(
        raw_info: Any,
        stock: "yf.Ticker",
        data: pd.DataFrame,
        ticker: str,
    ) -> Dict[str, Any]:
        """Fill a missing ``marketCap`` from other sources on the same quote.

        Yahoo intermittently returns a complete ``.info`` payload (180+ keys,
        correct name, price and quoteType) with ``marketCap`` absent. Because
        the hard validation gate treats a zero market cap as INVALID, a single
        missing optional field made large, liquid, unambiguously real equities
        unanalysable -- observed live on MU, where ``.info['marketCap']`` and
        ``sharesOutstanding`` were both None while ``fast_info`` carried the
        value.

        Rejecting a valid instrument because one provider field is absent, when
        the same object exposes the number elsewhere, is a data-plumbing failure
        being reported to the user as a verdict about their ticker. So the value
        is resolved in order of directness:

          1. ``info['marketCap']`` -- authoritative when present.
          2. ``totalAssets`` / ``netAssets`` -- funds report these instead; left
             for the ETF branch downstream, so no fallback is attempted.
          3. ``fast_info`` -- a separate, lighter Yahoo endpoint that is often
             populated when ``.info`` is not.
          4. shares outstanding x last close -- arithmetic, from whichever share
             count is available.

        The source is recorded in ``info['_market_cap_source']`` so the value is
        never silently synthetic. Returns the (possibly enriched) dict; never
        raises, because failing to enrich must degrade to current behaviour
        rather than break the fetch.
        """
        info: Dict[str, Any] = dict(raw_info) if isinstance(raw_info, dict) else {}
        if not info:
            return info

        if _is_positive_number(info.get("marketCap")):
            info["_market_cap_source"] = "info.marketCap"
            return info

        # Funds legitimately report assets rather than capitalisation.
        if _is_positive_number(info.get("totalAssets")) or _is_positive_number(
            info.get("netAssets")
        ):
            info["_market_cap_source"] = "info.totalAssets"
            return info

        # 3. fast_info -- a different endpoint, frequently populated when
        #    .info is not. Both the mapping and attribute spellings exist
        #    across yfinance versions.
        try:
            fast = getattr(stock, "fast_info", None)
            if fast is not None:
                candidate = None
                if hasattr(fast, "get"):
                    candidate = fast.get("marketCap") or fast.get("market_cap")
                if candidate is None:
                    candidate = getattr(fast, "market_cap", None)
                if _is_positive_number(candidate):
                    info["marketCap"] = float(candidate)
                    info["_market_cap_source"] = "fast_info"
                    logger.info(
                        "%s: marketCap absent from .info; recovered %.4g from fast_info",
                        ticker, float(candidate),
                    )
                    return info
        except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
            logger.debug("%s: fast_info market cap lookup failed: %s", ticker, exc)

        # 4. Derive from a share count and the last close.
        try:
            shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or (float(data["Close"].iloc[-1]) if data is not None and len(data) else None)
            )
            if _is_positive_number(shares) and _is_positive_number(price):
                derived = float(shares) * float(price)
                info["marketCap"] = derived
                info["_market_cap_source"] = "derived:shares*price"
                logger.info(
                    "%s: marketCap absent from .info; derived %.4g from "
                    "shares outstanding x last close",
                    ticker, derived,
                )
                return info
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s: derived market cap failed: %s", ticker, exc)

        info["_market_cap_source"] = "unavailable"
        logger.warning(
            "%s: market cap unavailable from .info, fast_info or share count; "
            "the validation gate will treat this ticker as INVALID",
            ticker,
        )
        return info

    def _fetch_price_data_classified(
        self, ticker: str, market: Optional[str] = None,
    ) -> Tuple[Optional[pd.DataFrame], str, Dict[str, Any]]:
        """Fetch history and classify the outcome.

        Returns ``(data, status, meta)`` where ``status`` is one of
        ``DataStatus.*`` and ``meta`` carries provider diagnostics, including
        ``variant`` (the provider symbol actually analysed) and
        ``resolution_note`` when that differs from the input.

        Symbol resolution (engine.symbols.plan_symbol): an explicit suffix is
        used as given, ``BRK.B`` becomes ``BRK-B``, and a bare symbol follows
        the selected market. The FIRST candidate that returns any bars is the
        instrument: a new listing with a short history is reported as
        insufficient history for that listing -- never replaced by a longer
        history of a different security on another exchange. The only
        substitution allowed is the same company's NSE/BSE sibling listing.
        """
        from engine.symbols import INDIA_SUFFIXES, plan_symbol, resolution_note

        plan = plan_symbol(ticker, market)
        cache_key = f"price_{ticker}" if not plan.market else f"price_{ticker}|{plan.market}"
        meta: Dict[str, Any] = {
            "ticker": ticker,
            "provider": "yfinance",
            "from_cache": False,
            "rows": 0,
            "variant": None,
            "range": None,
            "rate_limited": False,
            "exception": None,
            "candidates": list(plan.candidates),
            "resolution_note": "",
            "first_bar": None,
            "last_bar": None,
        }

        def _bar_dates(df):
            try:
                meta["first_bar"] = pd.Timestamp(df.index[0]).date().isoformat()
                meta["last_bar"] = pd.Timestamp(df.index[-1]).date().isoformat()
            except (IndexError, TypeError, ValueError):
                pass

        cached = self.cache.get(cache_key)
        if cached is not None and len(cached) >= Config.MIN_DATA_POINTS:
            meta["from_cache"] = True
            meta["rows"] = int(len(cached))
            meta["variant"] = (getattr(cached, 'attrs', {}) or {}).get('ticker') or plan.normalized
            meta["resolution_note"] = resolution_note(plan, meta["variant"])
            _bar_dates(cached)
            return cached, DataStatus.OK, meta

        def _same_company(a: str, b: str) -> bool:
            """NSE and BSE listings of one issuer share the base symbol."""
            if "." not in a or "." not in b:
                return False
            (ba, sa), (bb, sb) = a.rsplit(".", 1), b.rsplit(".", 1)
            return ba == bb and sa in INDIA_SUFFIXES and sb in INDIA_SUFFIXES

        rate_limited = False
        saw_exception = False
        any_metadata = False
        rl_retry_used = False            # cap the backoff sleep to ONCE per fetch
        provider_answered = False        # any non-empty profile, even a sentinel
        best_short: Optional[pd.DataFrame] = None
        short_symbol: Optional[str] = None
        metadata_symbol: Optional[str] = None

        for candidate in plan.candidates:
            if short_symbol and not _same_company(short_symbol, candidate):
                # A real listing already answered (with a short history). Other
                # exchanges are different securities, not a better copy of it.
                break
            stock = None
            for period, label in (("2y", "2y/1d"), ("6mo", "6mo/1d")):
                data = None
                while True:  # at most one rate-limit backoff retry per fetch
                    try:
                        stock = stock or yf.Ticker(candidate)
                        data = stock.history(period=period, interval="1d", auto_adjust=True)
                        data = _drop_unusable_bars(data)
                        break
                    except Exception as exc:  # provider / network error
                        saw_exception = True
                        meta["exception"] = f"{type(exc).__name__}: {exc}"
                        if self._looks_like_rate_limit(exc):
                            rate_limited = True
                            meta["rate_limited"] = True
                            if not rl_retry_used:
                                rl_retry_used = True
                                time.sleep(1.5)
                                continue
                        data = None
                        break
                if rate_limited and data is None:
                    break
                rows = 0 if data is None else len(data)
                if rows >= Config.MIN_DATA_POINTS:
                    meta["rows"] = int(rows)
                    meta["variant"] = candidate
                    meta["range"] = label
                    meta["resolution_note"] = resolution_note(plan, candidate)
                    _bar_dates(data)
                    # The bars prove the symbol is real. A profile that answered
                    # with a name is a profile, even without a market cap (Yahoo
                    # omits it for some ETFs and indices); only an info request
                    # that failed outright is a provider failure.
                    info_ok = False
                    try:
                        info = self._normalize_info(stock.info, stock, data, candidate)
                        info_ok = bool(info.get('shortName') or info.get('longName'))
                        data.attrs = {'info': info, 'ticker': candidate}
                    except Exception as info_exc:
                        meta["info_exception"] = f"{type(info_exc).__name__}: {info_exc}"
                        if self._looks_like_rate_limit(info_exc):
                            meta["rate_limited"] = True
                        data.attrs = {'ticker': candidate}
                    if info_ok:
                        self.cache.set(cache_key, data)
                    else:
                        data.attrs['info_unavailable'] = True
                    return data, DataStatus.OK, meta
                if rows > 0:
                    if best_short is None or rows > len(best_short):
                        best_short, short_symbol = data, candidate
                        meta["range"] = label
                    break               # a short 2y history will not grow at 6mo
                # Empty long-range answer: probe metadata once. No metadata
                # means no such listing, so its short-range pull is skipped
                # (each costs a throttled call plus yfinance's own retries).
                if period == "2y" and data is not None and stock is not None:
                    try:
                        exists = self._ticker_metadata_exists(stock)
                        # A sentinel profile ({"quoteType": "NONE"}) is still an
                        # answer: the provider is up and says "no such listing".
                        _inf = stock.info
                        provider_answered = provider_answered or (isinstance(_inf, dict) and bool(_inf))
                    except Exception:
                        exists = False
                    if exists:
                        any_metadata = True
                        metadata_symbol = metadata_symbol or candidate
                    else:
                        break
            if rate_limited:
                break

        if best_short is not None:
            meta["rows"] = int(len(best_short))
            meta["variant"] = short_symbol
            meta["resolution_note"] = resolution_note(plan, short_symbol)
            _bar_dates(best_short)
            best_short.attrs = {'ticker': short_symbol}
            logger.warning(
                "Insufficient history for %s: %d bars (<%d) [variant=%s range=%s]",
                ticker, len(best_short), Config.MIN_DATA_POINTS, short_symbol, meta.get("range"),
            )
            return best_short, DataStatus.INSUFFICIENT_HISTORY, meta

        if rate_limited:
            logger.warning("Provider rate-limited while fetching %s: %s",
                           ticker, meta.get("exception"))
            return None, DataStatus.PROVIDER_RATE_LIMITED, meta

        if any_metadata or saw_exception:
            meta["variant"] = metadata_symbol or plan.normalized
            logger.warning(
                "Provider data unavailable for %s (metadata=%s, exception=%s)",
                ticker, any_metadata, meta.get("exception"),
            )
            return None, DataStatus.PROVIDER_DATA_UNAVAILABLE, meta

        meta["variant"] = plan.normalized
        # yfinance swallows transport failures (proxy 403, DNS, TLS) and hands
        # back an empty frame with no metadata -- exactly what an unknown symbol
        # looks like. "Not found" is only a conclusion when the provider is
        # demonstrably answering for a control instrument right now.
        if not provider_answered and not self._provider_reachable():
            meta["provider_unreachable"] = True
            logger.warning("No data for %s and the provider is not answering a control "
                           "symbol — provider unavailable, not an invalid ticker", ticker)
            return None, DataStatus.PROVIDER_DATA_UNAVAILABLE, meta
        logger.warning("No data and no metadata for %s — treating as invalid ticker", ticker)
        return None, DataStatus.INVALID_TICKER, meta

    PROVIDER_PROBE_SYMBOL = "SPY"
    PROVIDER_PROBE_TTL_SECONDS = 60.0

    def _provider_reachable(self) -> bool:
        """Does the price provider return bars for a control symbol right now?

        Cached briefly so a batch of lookups costs one probe. Any exception or
        an empty answer counts as unreachable: the caller then reports a
        retryable provider failure rather than a permanent "not found".
        """
        now = time.monotonic()
        cached = getattr(self, "_provider_probe", None)
        if cached is not None and now - cached[0] < self.PROVIDER_PROBE_TTL_SECONDS:
            return cached[1]
        try:
            bars = yf.Ticker(self.PROVIDER_PROBE_SYMBOL).history(period="5d", interval="1d")
            ok = bars is not None and len(bars) > 0
        except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
            logger.debug("Provider probe failed: %s", exc)
            ok = False
        self._provider_probe = (now, ok)
        return ok

    
    def _get_company_info(self, ticker: str, data: pd.DataFrame, 
                         result: AnalysisResult) -> AnalysisResult:
        """Extract company information including extended hours pricing.

        Also detects whether the symbol is an ETF / fund / index so the
        rest of the pipeline (validator, scoring, display) can adapt.
        """
        try:
            info = data.attrs.get('info', {}) if hasattr(data, 'attrs') else {}

            # [ETF SUPPORT] Detect fund-like instruments via yfinance quoteType.
            qt = str(info.get('quoteType') or '').strip().upper()
            result.quote_type = qt or 'EQUITY'
            result.is_etf = qt in ('ETF', 'MUTUALFUND', 'INDEX', 'CLOSED-END-FUND', 'CEF')

            result.company_name = info.get('shortName', info.get('longName', ticker))
            # ETFs don't have a sector — use the fund category instead so the
            # report doesn't read 'Sector: Unknown'.
            if result.is_etf:
                result.sector = (
                    info.get('category')
                    or info.get('fundFamily')
                    or 'Exchange-Traded Fund'
                )
                result.industry = info.get('category', 'Fund')
            else:
                result.sector = info.get('sector', 'Unknown')
                result.industry = info.get('industry', 'Unknown')

            # ETFs report totalAssets / netAssets; equities report marketCap.
            raw_mcap = safe_get(info, 'marketCap', 0)
            raw_assets = safe_get(info, 'totalAssets', 0) or safe_get(info, 'netAssets', 0)
            if result.is_etf:
                result.net_assets = float(raw_assets or raw_mcap or 0.0)
                result.market_cap = float(raw_mcap or raw_assets or 0.0)
                result.expense_ratio = float(
                    info.get('annualReportExpenseRatio') or info.get('expenseRatio') or 0.0
                )
            else:
                result.market_cap = float(raw_mcap or 0.0)
                result.net_assets = float(raw_assets or 0.0)

            result.currency = info.get('currency', 'USD')
            result.currency_symbol = get_currency_symbol(result.currency)
            result.week_52_high_reported = float(safe_get(info, 'fiftyTwoWeekHigh', 0) or 0)
            result.week_52_low_reported = float(safe_get(info, 'fiftyTwoWeekLow', 0) or 0)
            # [ASSET-TYPE] Issuer country drives ADR / foreign-issuer detection.
            result.country = str(info.get('country') or '').strip()
            result.business_summary = str(info.get('longBusinessSummary') or '').strip()
            
            # Regular market price. Read the last FINITE close rather than the
            # last row: a provider placeholder bar would otherwise make the
            # price NaN and the hard gate would bin a live ticker as invalid.
            _closes = data['Close'].dropna()
            if len(_closes):
                result.current_price = float(_closes.iloc[-1])
                result.previous_close = (
                    float(_closes.iloc[-2]) if len(_closes) > 1 else result.current_price
                )
            else:
                # No usable bar at all — fall back to the live quote before
                # giving up, so "no history" alone never reads as "delisted".
                _quote = (safe_get(info, 'currentPrice', 0)
                          or safe_get(info, 'regularMarketPrice', 0) or 0.0)
                result.current_price = float(_quote or 0.0)
                result.previous_close = float(
                    safe_get(info, 'regularMarketPreviousClose', 0) or result.current_price
                )
            if not math.isfinite(result.current_price):
                result.current_price = 0.0
            if not math.isfinite(result.previous_close):
                result.previous_close = result.current_price
            result.day_change = result.current_price - result.previous_close
            result.day_change_pct = safe_divide(result.day_change, result.previous_close, 0) * 100
            
            # Extended hours pricing from yfinance info
            # Pre-market price
            pre_price = safe_get(info, 'preMarketPrice', 0)
            if pre_price and pre_price > 0:
                result.pre_market_price = float(pre_price)
                result.pre_market_change = result.pre_market_price - result.current_price
                result.pre_market_change_pct = safe_divide(result.pre_market_change, result.current_price, 0) * 100
            
            # Post-market / After-hours price
            post_price = safe_get(info, 'postMarketPrice', 0)
            if post_price and post_price > 0:
                result.after_hours_price = float(post_price)
                result.after_hours_change = result.after_hours_price - result.current_price
                result.after_hours_change_pct = safe_divide(result.after_hours_change, result.current_price, 0) * 100
            
            # Market state (based on yfinance marketState or time-based)
            market_state = info.get('marketState', 'REGULAR')
            result.market_state = market_state
            result.exchange = str(info.get('exchange') or info.get('fullExchangeName') or 'UNKNOWN')
            try:
                last_bar = pd.Timestamp(data.index[-1])
                result.market_timestamp = last_bar.isoformat()
            except Exception:
                result.market_timestamp = ""
            
            # Get last trade time if available
            reg_time = info.get('regularMarketTime', None)
            if reg_time:
                try:
                    from datetime import datetime
                    trade_dt = datetime.fromtimestamp(reg_time)
                    result.last_trade_time = trade_dt.strftime('%b %d at %I:%M:%S %p ET')
                except:
                    result.last_trade_time = ""
            
        except Exception as e:
            logger.debug(f"Company info error: {e}")
        
        return result
    
    def _run_parallel_analysis(self, ticker: str, data: pd.DataFrame,
                               result: AnalysisResult,
                               fast_mode: bool = False,
                               include_insider: bool = False) -> AnalysisResult:
        """Run analysis components in parallel.

        [PERF-FASTMODE] In fast mode only the modules that read the ALREADY
        FETCHED price frame (technical, risk) plus the cached-`.info`
        fundamental pull are run. The network-heavy optional modules
        (sentiment/news, insider, analyst consensus, catalysts) are replaced
        with neutral defaults unless ``include_insider`` is requested for
        scanner confirmation. Scoring then uses neutral inputs for the skipped
        factors and the result is flagged PRELIMINARY.
        """
        if fast_mode:
            # Only run the modules that are cheap (operate on local `data`) or
            # already cached. Substitute neutral defaults for the rest so the
            # downstream signal generator never sees a None.
            result.sentiment = SentimentData()
            result.insider = InsiderData(data_status="not_fetched")
            result.market_intel = MarketIntelligence()
            result.catalysts = CatalystData()
            # [PERF-FASTMODE] NOTE: a plain `with ThreadPoolExecutor()` block calls
            # shutdown(wait=True) on exit, which BLOCKS until every worker finishes —
            # so a module that hangs on a slow Yahoo call (e.g. risk's SPY beta fetch)
            # would defeat the per-module timeout and stall the whole request until the
            # 60s lock-watchdog fires. Manage the executor manually and shut it down
            # WITHOUT waiting on a hung worker.
            executor = ThreadPoolExecutor(max_workers=4 if include_insider else 3)
            try:
                futures = {
                    'technical': executor.submit(self.technical_analyzer.analyze, data),
                    'risk': executor.submit(self.risk_analyzer.analyze, data, ticker),
                    'fundamental': executor.submit(self.fundamental_analyzer.analyze, ticker),
                }
                if include_insider:
                    futures['insider'] = executor.submit(self.insider_analyzer.analyze, ticker)
                for name, future in futures.items():
                    try:
                        # Stricter per-module ceiling than full mode (8s vs 30s).
                        value = future.result(timeout=8)
                        setattr(result, name, value)
                    except Exception as e:
                        # TimeoutError stringifies to '' — log the type so the
                        # cause isn't a mystery blank line in the logs.
                        logger.warning(f"[fast] {name} analysis failed: {type(e).__name__}: {e}")
                        if name == 'insider' and result.insider is not None:
                            result.insider.data_status = "unavailable"
            finally:
                # Don't wait on a hung worker; cancel anything not yet started.
                executor.shutdown(wait=False, cancel_futures=True)
            if result.fundamental is None:
                result.fundamental = FundamentalData()
            # Consensus targets arrived with the fundamentals payload; use them
            # so the preview's target blend is the full model's, not a
            # resistance-only stand-in. Ratings, news and insider data stay
            # unfetched (they need separate calls).
            _cons = getattr(result.fundamental, 'consensus', {}) or {}
            if float(_cons.get('target_mean') or 0) > 0:
                mi = result.market_intel
                mi.target_mean = float(_cons['target_mean'])
                mi.target_high = float(_cons.get('target_high') or 0)
                mi.target_low = float(_cons.get('target_low') or 0)
                mi.target_median = float(_cons.get('target_median') or 0) or mi.target_mean
                mi.analyst_count = int(_cons.get('analyst_count') or 0)
                if result.current_price > 0:
                    mi.implied_upside = (mi.target_mean - result.current_price) / result.current_price * 100
        else:
            executor = ThreadPoolExecutor(max_workers=8)
            try:
                futures = {
                    'technical': executor.submit(self.technical_analyzer.analyze, data),
                    'fundamental': executor.submit(self.fundamental_analyzer.analyze, ticker),
                    'risk': executor.submit(self.risk_analyzer.analyze, data, ticker),
                    'sentiment': executor.submit(self.sentiment_analyzer.analyze, ticker),
                    'market_intel': executor.submit(self.market_intel_analyzer.analyze, ticker),
                    'insider': executor.submit(self.insider_analyzer.analyze, ticker),
                    'catalysts': executor.submit(self.catalyst_analyzer.analyze, ticker),
                }

                for name, future in futures.items():
                    try:
                        value = future.result(timeout=30)
                        setattr(result, name, value)
                    except Exception as e:
                        logger.warning(f"{name} analysis failed: {type(e).__name__}: {e}")
            finally:
                # Same rationale as fast mode: never block on a hung worker.
                executor.shutdown(wait=False, cancel_futures=True)

        # [ETF SUPPORT] Reconcile ETF flags between top-level detection
        # (via yfinance quoteType) and the fundamentals analyzer. The
        # top-level flag is authoritative; mirror it down so the display's
        # `f.is_etf` branch and other ETF gates fire reliably.
        if getattr(result, 'is_etf', False) and result.fundamental is not None:
            result.fundamental.is_etf = True
            if not getattr(result.fundamental, 'expense_ratio', 0.0):
                result.fundamental.expense_ratio = getattr(result, 'expense_ratio', 0.0) or 0.0
            if not getattr(result.fundamental, 'net_assets', 0.0):
                result.fundamental.net_assets = getattr(result, 'net_assets', 0.0) or result.market_cap
        
        # Valuation (depends on fundamental and risk)
        # Pass sector info to fundamental for sector-adjusted DCF terminal growth
        if result.sector:
            result.fundamental._sector = result.sector
        if getattr(result, 'industry', ''):
            result.fundamental._industry = result.industry
        # Extract analyst consensus target for DCF growth-stock anchoring
        analyst_target = 0.0
        if result.market_intel and hasattr(result.market_intel, 'target_mean'):
            analyst_target = result.market_intel.target_mean or 0.0
        result.valuation = self.valuation_analyzer.analyze(
            ticker, result.current_price, result.fundamental, result.risk,
            analyst_target=analyst_target
        )
        
        # [ENHANCED] Calculate composite price targets after all data is available
        # This will be finalized in analyze() after Monte Carlo simulation
        
        return result
    
    def _determine_regime(self, technical: TechnicalIndicators, 
                          risk: RiskMetrics) -> MarketRegime:
        """Determine current market regime using multi-factor analysis.
        
        Factors considered:
        - Annualized volatility (high vol threshold)
        - Trend direction and strength (ADX)
        - Price vs moving averages (SMA50, SMA200)
        - Momentum persistence (RSI trend)
        - VIX-equivalent implied vol level
        """
        vol = risk.annualized_volatility
        trend = technical.trend_direction
        adx = technical.adx
        price_vs_sma50 = technical.price_vs_sma50 if hasattr(technical, 'price_vs_sma50') else 0
        price_vs_sma200 = technical.price_vs_sma200 if hasattr(technical, 'price_vs_sma200') else 0
        rsi = technical.rsi
        
        # Crisis: extreme volatility with bearish trend
        if vol > 0.6 and trend in ["DOWN", "STRONG_DOWN"]:
            return MarketRegime.CRISIS
        
        # High volatility regimes (>35% annualized)
        if vol > 0.35:
            if trend in ["UP", "STRONG_UP"] and adx > 20:
                return MarketRegime.BULL_VOLATILE
            elif trend in ["DOWN", "STRONG_DOWN"]:
                return MarketRegime.BEAR_VOLATILE
            return MarketRegime.HIGH_VOLATILITY
        
        # Trending regimes: ADX > 25 indicates strong trend
        if adx > 25:
            # Confirm with price vs SMAs for robustness
            bullish_sma = price_vs_sma200 > 0 or price_vs_sma50 > 0
            bearish_sma = price_vs_sma200 < 0 and price_vs_sma50 < 0
            
            if trend in ["UP", "STRONG_UP"] and bullish_sma:
                return MarketRegime.BULL_TRENDING
            elif trend in ["DOWN", "STRONG_DOWN"] and bearish_sma:
                return MarketRegime.BEAR_TRENDING
            # ADX strong but conflicting signals — check RSI for tie-breaking
            elif rsi > 55 and trend in ["UP", "STRONG_UP"]:
                return MarketRegime.BULL_TRENDING
            elif rsi < 45 and trend in ["DOWN", "STRONG_DOWN"]:
                return MarketRegime.BEAR_TRENDING
        
        # Weak trend / range-bound
        return MarketRegime.SIDEWAYS
    
    def _generate_insights(self, result: AnalysisResult) -> List[str]:
        """Generate key insights"""
        insights = []
        
        # Signal insight
        signal_emoji = {
            Signal.STRONG_BUY: "🚀", Signal.BUY: "📈", Signal.ACCUMULATE: "📊",
            Signal.HOLD: "⏸️", Signal.REDUCE: "📉", Signal.SELL: "💨", Signal.STRONG_SELL: "🛑"
        }
        insights.append(
            f"{signal_emoji.get(result.signal, '📊')} {result.signal.value} signal "
            f"with {result.confidence_score:.0f}% confidence"
        )
        
        # Technical insights
        if result.technical and result.technical.rsi < 30:
            insights.append(f"📉 RSI at {result.technical.rsi:.0f} - oversold territory, potential bounce")
        elif result.technical and result.technical.rsi > 70:
            insights.append(f"📈 RSI at {result.technical.rsi:.0f} - overbought, watch for pullback")
        
        if result.technical and result.technical.golden_cross:
            insights.append("✨ Golden Cross detected - bullish long-term signal")
        elif result.technical and result.technical.death_cross:
            insights.append("⚠️ Death Cross detected - bearish long-term signal")
        
        if result.technical and result.technical.squeeze_detected:
            insights.append("🎯 Volatility squeeze - breakout may be imminent")

        # Candlestick pattern insight (only HIGH/MEDIUM reliability with directional bias)
        if result.technical and result.technical.candlestick_patterns:
            top = result.technical.candlestick_patterns[0]
            if top['reliability'] in ("HIGH", "MEDIUM") and top['bias'] != "NEUTRAL":
                emoji = "🟢" if top['bias'] == "BULLISH" else "🔴"
                insights.append(
                    f"{emoji} Candlestick: {top['name']} ({top['bias'].title()}, "
                    f"{top['reliability'].lower()} reliability)"
                )
        
        # Fundamental insights
        if result.fundamental.roe > 20:
            insights.append(f"💪 Strong ROE of {result.fundamental.roe:.1f}% - excellent profitability")
        
        if (piotroski_is_reportable(result.fundamental)
                and result.fundamental.piotroski_f_score >= 7):
            insights.append(f"⭐ High Piotroski F-Score ({result.fundamental.piotroski_f_score}/9) - strong fundamentals")
        
        # Valuation insights
        if result.valuation.dcf_upside > 20:
            insights.append(f"💰 DCF analysis suggests {result.valuation.dcf_upside:.0f}% upside potential")
        elif result.valuation.dcf_upside < -20:
            insights.append(f"⚠️ DCF suggests {abs(result.valuation.dcf_upside):.0f}% overvaluation")
        
        # Sentiment insights
        if (result.sentiment.sentiment_label == "POSITIVE"
                and result.sentiment.news_volume > 10
                and result.sentiment.has_direct_news):
            insights.append(f"📰 Positive news sentiment ({result.sentiment.news_volume} articles)")
        elif result.sentiment.sentiment_label == "NEGATIVE" and result.sentiment.has_direct_news:
            insights.append("📰 Negative news flow - monitor headlines")
        elif result.sentiment.provenance_computed and not result.sentiment.has_direct_news:
            insights.append(
                "📰 No direct company headlines found; peer/macro sentiment is not "
                "treated as company evidence"
            )
        
        # Monte Carlo insights
        if result.monte_carlo.probability_profit > 0.6:
            insights.append(f"📊 Monte Carlo: {result.monte_carlo.probability_profit:.0%} chance of profit in 30 days")
        
        return insights[:8]
    
    def _generate_warnings(self, result: AnalysisResult) -> List[str]:
        """Generate risk warnings"""
        warnings = []

        if result.unexplained_move:
            warnings.append(f"⚠️ UNEXPLAINED MOVE: {result.unexplained_move_note}")
        
        if result.risk.annualized_volatility > 0.45:
            warnings.append(f"⚠️ HIGH VOLATILITY: {result.risk.annualized_volatility:.0%} annual vol")
        
        if result.risk.max_drawdown < -0.35:
            warnings.append(f"⚠️ SEVERE DRAWDOWN HISTORY: {result.risk.max_drawdown:.0%}")
        
        # Leverage check with exceptions for Tech and high-coverage companies
        # (by sector and coverage, never by ticker).
        is_tech = result.sector == "Technology"
        strong_coverage = result.fundamental.interest_coverage > 10
        
        if result.fundamental.debt_to_equity > 2 and not (is_tech or strong_coverage):
            warnings.append(f"⚠️ HIGH LEVERAGE: {result.fundamental.debt_to_equity:.1f}x debt/equity")
        
        if result.risk.overall_risk_score >= 8:
            warnings.append(f"🚨 HIGH RISK SCORE: {result.risk.overall_risk_score}/10")
        
        if result.catalysts.days_to_earnings is not None and 0 <= result.catalysts.days_to_earnings <= 7:
            warnings.append(f"📅 EARNINGS IMMINENT: {result.catalysts.days_to_earnings} days away")
        
        if result.technical and result.technical.rsi > 80:
            warnings.append("📈 EXTREME OVERBOUGHT: RSI above 80")
        elif result.technical and result.technical.rsi < 20:
            warnings.append("📉 EXTREME OVERSOLD: RSI below 20")
        
        if result.risk and result.risk.liquidity_score < 4:
            warnings.append("⚠️ LOW LIQUIDITY: May impact execution")
        
        return warnings[:5]
    
    def _generate_action_items(self, result: AnalysisResult) -> List[str]:
        """Generate actionable next steps"""
        actions = []
        sym = result.currency_symbol
        final_action = str(result.final_action or "").upper()
        unsized = (
            final_action in (FinalAction.STRONG_BUY.value, FinalAction.BUY.value,
                             FinalAction.ACCUMULATE.value)
            and result.position_sizing is not None
            and int(getattr(result.position_sizing, 'recommended_shares', 0) or 0) <= 0
        )

        if unsized:
            # A funded Decision with no executable size must not read as an order.
            actions.extend([
                "No position fits the risk budget at this account size (a single share or the stop distance exceeds it)",
                "Do not place an order; the model view is positive but not executable here",
            ])
        elif final_action == FinalAction.WATCHLIST.value:
            actions.extend([
                "Do not open a new position while the blocking conditions remain",
                "Re-run the Decision after the primary blocker clears",
            ])
        elif final_action == FinalAction.HOLD_EXISTING.value:
            actions.extend([
                "Maintain only the existing position",
                "Do not add new money until the Decision improves",
            ])
        elif final_action == FinalAction.AVOID.value:
            from engine.blockers import holder_action
            _held = holder_action(FinalAction.AVOID, getattr(result.scores, 'composite_score', None)
                                  if result.scores else None,
                                  getattr(result, 'final_action_codes', []) or [])
            actions.extend([
                "Do not open a new position: the overall score is bearish",
                "If you hold it: exit on strength" if _held == "EXIT"
                else "If you hold it: trim into strength and keep the stop",
            ])
        elif final_action == FinalAction.INVALID.value:
            actions.append("Do not act until valid current data is available")

        elif result.signal in [Signal.STRONG_BUY, Signal.BUY]:
            if result.trade_setup.entry_price > 0:
                actions.append(f"Set limit buy at {sym}{result.trade_setup.entry_price:.2f}")
                actions.append(f"Place stop loss at {sym}{result.trade_setup.stop_loss:.2f}")
            if result.position_sizing and result.position_sizing.is_viable:
                actions.append(f"Size position: {result.position_sizing.recommended_shares} shares")
        
        elif result.signal == Signal.ACCUMULATE:
            actions.append("Start building position in tranches")
            actions.append("Add on dips to support levels")
        
        elif result.signal == Signal.HOLD:
            actions.append("Maintain current position")
            actions.append("Set alerts at support/resistance levels")
        
        elif result.signal in [Signal.REDUCE, Signal.SELL]:
            actions.append("Consider taking profits on strength")
            actions.append("Tighten stop losses")
        
        elif result.signal == Signal.STRONG_SELL:
            actions.append("Exit position immediately")
            actions.append("Reassess after stabilization")
        
        # Add catalyst-related actions
        if result.catalysts.near_term_catalyst:
            actions.append(f"Review position before {result.catalysts.earnings_date}")
        
        return actions[:5]
    
    def _generate_recommendation(self, result: AnalysisResult) -> str:
        """Generate human-readable recommendation"""
        action_text = {
            FinalAction.STRONG_BUY.value: "STRONG BUY - High conviction opportunity",
            FinalAction.BUY.value: "BUY - Favorable risk/reward",
            FinalAction.ACCUMULATE.value: "ACCUMULATE - Build only the sized position gradually",
            FinalAction.HOLD_EXISTING.value: "HOLD EXISTING - No new money",
            FinalAction.WATCHLIST.value: "WATCHLIST - No new position",
            FinalAction.AVOID.value: "AVOID - No new exposure",
            FinalAction.INVALID.value: "INVALID - No actionable analysis",
        }
        signal_text = {
            Signal.STRONG_BUY: "STRONG BUY - High conviction opportunity",
            Signal.BUY: "BUY - Favorable risk/reward",
            Signal.ACCUMULATE: "ACCUMULATE - Build position gradually",
            Signal.HOLD: "HOLD - Maintain current position",
            Signal.REDUCE: "REDUCE - Take some profits",
            Signal.SELL: "SELL - Exit position",
            Signal.STRONG_SELL: "STRONG SELL - Immediate exit recommended"
        }
        
        rec = action_text.get(
            str(result.final_action or "").upper(),
            signal_text.get(result.signal, result.signal.value),
        )
        
        return (
            f"{rec} | Confidence: {result.confidence_score:.0f}% | "
            f"Score: {result.scores.composite_score:.0f}/100 | "
            f"Risk: {result.risk.risk_grade}"
        )


# =============================================================================
# MARKET ENVIRONMENT ANALYSIS
# =============================================================================

def fetch_market_environment(api_client: Optional['APIClient'] = None) -> Dict[str, Any]:
    """
    Fetch current market environment data:
    - SPY trend with support/resistance
    - VIX level and interpretation
    - Market sentiment indicators
    - Sector performance
    - Fed policy outlook (dynamic)
    - Mega-cap earnings calendar
    """
    env = {
        'spy_price': 0.0,
        'spy_change_pct': 0.0,
        'spy_trend': 'NEUTRAL',
        'spy_support': 0.0,
        'spy_resistance': 0.0,
        'spy_sma_50': 0.0,
        'spy_sma_200': 0.0,
        'vix': 0.0,
        'vix_signal': 'NEUTRAL',
        'fear_greed': 50,
        'fear_greed_label': 'Neutral',
        'put_call_ratio': 1.0,
        'put_call_signal': 'NEUTRAL',
        'fed_outlook': 'Analyzing...',  # Will be dynamic
        'fed_outlook_emoji': '🟡',
        'days_to_fomc': -1,
        'fomc_date': 'Unknown',
        'yield_curve': 'NORMAL',
        'treasury_10y': 0.0,
        'rate_cut_probability': 0.0,
        'inflation_trend': 'Moderating',
        'economic_outlook': 'Analyzing...',  # Will be dynamic
        'sector_leaders': [],
        'sector_laggards': [],
        'market_breadth': 'NEUTRAL',
        'macro_risk_score': 5,
        'macro_risk_factors': [],
        # Mega-cap earnings
        'mega_earnings_this_week': [],
        'mega_earnings_warning': '',
        'high_impact_earnings': False,
        # Macro news
        'macro_headlines': [],
        'macro_news_sentiment': 0.0,
        'fetch_success': False
    }
    
    try:
        # Fetch SPY data
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="6mo")
        
        if len(spy_hist) > 0:
            env['spy_price'] = float(spy_hist['Close'].iloc[-1])
            if len(spy_hist) > 1:
                prev_close = float(spy_hist['Close'].iloc[-2])
                env['spy_change_pct'] = ((env['spy_price'] - prev_close) / prev_close) * 100
            
            # Calculate SMAs
            if len(spy_hist) >= 50:
                env['spy_sma_50'] = float(spy_hist['Close'].tail(50).mean())
            if len(spy_hist) >= 200:
                env['spy_sma_200'] = float(spy_hist['Close'].tail(200).mean())
            
            # Determine trend
            if env['spy_sma_50'] > 0 and env['spy_sma_200'] > 0:
                if env['spy_price'] > env['spy_sma_50'] > env['spy_sma_200']:
                    env['spy_trend'] = 'UPTREND (Bullish)'
                elif env['spy_price'] < env['spy_sma_50'] < env['spy_sma_200']:
                    env['spy_trend'] = 'DOWNTREND (Bearish)'
                elif env['spy_sma_50'] > env['spy_sma_200']:
                    env['spy_trend'] = 'UPTREND (Pullback)'
                elif env['spy_sma_50'] < env['spy_sma_200']:
                    env['spy_trend'] = 'DOWNTREND (Rally)'
                else:
                    env['spy_trend'] = 'CONSOLIDATION'
            
            # Calculate support/resistance (simplified)
            recent_lows = spy_hist['Low'].tail(20)
            recent_highs = spy_hist['High'].tail(20)
            env['spy_support'] = float(recent_lows.min())
            env['spy_resistance'] = float(recent_highs.max())
            
            env['fetch_success'] = True
        
        # Fetch VIX
        try:
            vix = yf.Ticker("^VIX")
            vix_hist = vix.history(period="5d")
            if len(vix_hist) > 0:
                env['vix'] = float(vix_hist['Close'].iloc[-1])
                if env['vix'] < 15:
                    env['vix_signal'] = 'LOW - Market complacency, potential volatility spike ahead'
                elif env['vix'] < 20:
                    env['vix_signal'] = 'NORMAL - Healthy market conditions'
                elif env['vix'] < 30:
                    env['vix_signal'] = 'ELEVATED - Increased uncertainty, caution advised'
                else:
                    env['vix_signal'] = 'HIGH - Fear in markets, potential buying opportunity for contrarians'
        except Exception:
            pass
        
        # Sector Performance (fetch major sector ETFs)
        sector_etfs = {
            'XLK': 'Technology',
            'XLF': 'Financials',
            'XLV': 'Healthcare',
            'XLY': 'Consumer Disc.',
            'XLP': 'Consumer Staples',
            'XLE': 'Energy',
            'XLI': 'Industrials',
            'XLB': 'Materials',
            'XLRE': 'Real Estate',
            'XLU': 'Utilities',
            'XLC': 'Communication'
        }
        
        sector_perf = []
        for etf, sector_name in sector_etfs.items():
            try:
                s = yf.Ticker(etf)
                hist = s.history(period="1mo")
                if len(hist) >= 2:
                    first_price = float(hist['Close'].iloc[0])
                    last_price = float(hist['Close'].iloc[-1])
                    pct_change = ((last_price - first_price) / first_price) * 100
                    sector_perf.append((sector_name, pct_change))
            except Exception:
                continue
        
        if sector_perf:
            sector_perf.sort(key=lambda x: x[1], reverse=True)
            env['sector_leaders'] = sector_perf[:3]
            env['sector_laggards'] = sector_perf[-3:]
        
        # Put/Call Ratio - compute from actual SPY options chain
        try:
            spy_options_dates = spy.options
            if spy_options_dates:
                # Use nearest expiry for most liquid options
                nearest_expiry = spy_options_dates[0]
                opt_chain = spy.option_chain(nearest_expiry)
                total_put_oi = opt_chain.puts['openInterest'].sum() if 'openInterest' in opt_chain.puts.columns else 0
                total_call_oi = opt_chain.calls['openInterest'].sum() if 'openInterest' in opt_chain.calls.columns else 0
                
                if total_call_oi > 0:
                    env['put_call_ratio'] = round(total_put_oi / total_call_oi, 2)
                    if env['put_call_ratio'] > 1.2:
                        env['put_call_signal'] = 'HIGH - Bearish hedging activity, potential contrarian buy signal'
                    elif env['put_call_ratio'] < 0.7:
                        env['put_call_signal'] = 'LOW - Bullish sentiment, some complacency'
                    elif env['put_call_ratio'] < 0.9:
                        env['put_call_signal'] = 'NORMAL-LOW - Leaning bullish'
                    else:
                        env['put_call_signal'] = 'NEUTRAL - Balanced put/call activity'
                else:
                    env['put_call_ratio'] = 0.95
                    env['put_call_signal'] = 'UNAVAILABLE - Open interest data not loaded'
            else:
                env['put_call_ratio'] = 0.95
                env['put_call_signal'] = 'UNAVAILABLE - No options data for SPY'
        except Exception as e:
            logger.debug(f"Put/Call ratio fetch error: {e}")
            env['put_call_ratio'] = 0.95
            env['put_call_signal'] = 'UNAVAILABLE - Options data fetch failed'
        
        # Fear & Greed composite (multi-factor instead of pure VIX derivative)
        # Factors: VIX level, SPY momentum, market breadth, put/call ratio, safe haven demand
        if env['vix'] > 0:
            fg_score = 50  # Start neutral
            
            # Factor 1: VIX contribution (40% weight) - inverted
            vix_val = env['vix']
            vix_component = max(0, min(100, 100 - (vix_val - 10) * 3.33))  # 10=100, 40=0
            fg_score = vix_component * 0.40
            
            # Factor 2: SPY momentum (25% weight) - price vs SMA50
            if env['spy_price'] > 0 and env['spy_sma_50'] > 0:
                spy_momentum = ((env['spy_price'] / env['spy_sma_50']) - 1) * 100
                momentum_component = max(0, min(100, 50 + spy_momentum * 5))  # +-10% maps to 0-100
                fg_score += momentum_component * 0.25
            else:
                fg_score += 50 * 0.25
            
            # Factor 3: Put/Call ratio (20% weight) - contrarian
            pcr = env.get('put_call_ratio', 0.95)
            pcr_component = max(0, min(100, 100 - (pcr - 0.5) * 100))  # 0.5=100, 1.5=0
            fg_score += pcr_component * 0.20
            
            # Factor 4: SPY 1-month change (15% weight)
            spy_change = env.get('spy_change_pct', 0)
            change_component = max(0, min(100, 50 + spy_change * 5))
            fg_score += change_component * 0.15
            
            env['fear_greed'] = int(max(0, min(100, fg_score)))
            
            if env['fear_greed'] >= 80:
                env['fear_greed_label'] = 'Extreme Greed'
            elif env['fear_greed'] >= 60:
                env['fear_greed_label'] = 'Greed'
            elif env['fear_greed'] >= 40:
                env['fear_greed_label'] = 'Neutral'
            elif env['fear_greed'] >= 20:
                env['fear_greed_label'] = 'Fear'
            else:
                env['fear_greed_label'] = 'Extreme Fear'
        
    except Exception as e:
        logger.debug(f"Market environment fetch error: {e}")
    
    # ==========================================================================
    # MACRO ENVIRONMENT INTEGRATION (Fed, Earnings, Economic Outlook)
    # ==========================================================================
    try:
        macro_analyzer = MacroEnvironmentAnalyzer(api_client)
        macro_data, mega_cap_earnings = macro_analyzer.analyze()
        
        # Fed Policy
        env['fed_outlook'] = f"{macro_data.fed_outlook}: {macro_data.fed_outlook_reason}"
        if macro_data.fed_outlook == "HAWKISH":
            env['fed_outlook_emoji'] = "🔴"
        elif macro_data.fed_outlook == "DOVISH":
            env['fed_outlook_emoji'] = "🟢"
        else:
            env['fed_outlook_emoji'] = "🟡"
        
        env['days_to_fomc'] = macro_data.days_to_fomc
        env['fomc_date'] = macro_data.fomc_date
        env['yield_curve'] = macro_data.yield_curve_signal
        env['treasury_10y'] = macro_data.treasury_10y
        env['rate_cut_probability'] = macro_data.rate_change_probability
        
        # Economic Outlook
        env['economic_outlook'] = macro_data.economic_outlook
        
        # Macro Risk
        env['macro_risk_score'] = macro_data.macro_risk_score
        env['macro_risk_factors'] = macro_data.macro_risk_factors
        env['macro_headlines'] = macro_data.macro_headlines
        env['macro_news_sentiment'] = macro_data.macro_news_sentiment
        
        # Mega-Cap Earnings
        env['mega_earnings_this_week'] = mega_cap_earnings.earnings_this_week
        env['mega_earnings_warning'] = mega_cap_earnings.market_volatility_warning
        env['high_impact_earnings'] = mega_cap_earnings.high_impact_earnings
        
        if mega_cap_earnings.upcoming_earnings:
            env['next_mega_earnings'] = mega_cap_earnings.upcoming_earnings[0]
        
    except Exception as e:
        logger.debug(f"Macro integration error: {e}")
        env['fed_outlook'] = "Fed outlook unavailable"
        env['economic_outlook'] = "Economic data unavailable"
    
    return env


from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.layout import Layout
from rich.align import Align
from rich import box
from typing import Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.layout import Layout
from rich.align import Align
from rich import box
from typing import Dict, Any

import unicodedata as _ud


def _display_width(text: str) -> int:
    """Terminal cell width of *text*, counting emoji and CJK as two columns.

    [T10 FIX] Every boxed panel in this file padded with ``:<12``-style format
    specs, which count Python characters. Emoji occupy TWO terminal cells, so
    each one pushed its row a column short and the right-hand border of every
    box drifted out of alignment — visible in the header rows of the decision
    snapshot and the what-to-do panel.
    """
    width = 0
    for ch in str(text):
        if _ud.combining(ch):
            continue
        width += 2 if _ud.east_asian_width(ch) in ('W', 'F') or ord(ch) >= 0x1F300 else 1
    return width


def _pad(text: str, width: int, align: str = '<') -> str:
    """Pad *text* to *width* TERMINAL COLUMNS (not characters)."""
    text = str(text)
    deficit = max(0, width - _display_width(text))
    if align == '>':
        return ' ' * deficit + text
    if align == '^':
        left = deficit // 2
        return ' ' * left + text + ' ' * (deficit - left)
    return text + ' ' * deficit


def _wrap_cells(text: str, width: int) -> List[str]:
    """Wrap *text* to *width* terminal columns, honouring emoji width."""
    if not text:
        return [""]
    words, lines, cur = str(text).split(), [], ""
    for word in words:
        candidate = f"{cur} {word}".strip()
        if _display_width(candidate) <= width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _box(rows: List[str], width: int = 74, indent: str = "  ") -> None:
    """Print a box whose right border actually lines up, wrapping long rows."""
    print(f"{indent}┌{'─' * width}┐")
    for row in rows:
        for line in _wrap_cells(row, width - 2):
            print(f"{indent}│ {_pad(line, width - 2)} │")
    print(f"{indent}└{'─' * width}┘")


def _rule(title: str = "", width: int = 74, indent: str = "  ") -> None:
    if title:
        pad = max(0, width - _display_width(title) - 2)
        print(f"{indent}{'─' * 2} {title} {'─' * pad}")
    else:
        print(f"{indent}{'─' * width}")


def trade_levels(result: 'AnalysisResult') -> Dict[str, Any]:
    """Read the trade levels for display. Never invents any.

    [T3 FIX] Three display panels each carried their own fallbacks for an empty
    setup — ``stop = current * 0.95``, ``t1/t2/t3 = current * 1.05/1.10/1.15``,
    a hardcoded ``rr = 1.5`` in one place and ``rr = 2.0`` in another, and
    ``win_prob = 0.5``. So when the setup was empty (which, before the T1 fix,
    was the common case) the report printed a -5% stop, +5/10/15% targets and
    an R:R that differed depending on which panel rendered — all invented, none
    computed, and formatted identically to real ones.

    This returns what was actually computed plus an ``available`` flag. Callers
    render "—" when it is False rather than substituting a plausible number.
    """
    ts = getattr(result, 'trade_setup', None)
    current = float(getattr(result, 'current_price', 0.0) or 0.0)
    available = bool(ts and getattr(ts, 'levels_available', False))

    if not available:
        return {
            'available': False,
            'actionable': False,
            'current': current,
            'entry': 0.0, 'stop': 0.0,
            't1': 0.0, 't2': 0.0, 't3': 0.0, 'valuation_target': 0.0,
            'rr': 0.0, 'risk_amount': 0.0,
            'trailing_pct': 0.0,
            'confidence_tier': getattr(ts, 'confidence_tier', 'LOW') if ts else 'LOW',
            'has_long_reward': False,
            'basis': getattr(ts, 'setup_basis', 'levels not computed') if ts else 'no trade setup',
            'review_date': '',
            'horizon_days': 0,
        }

    return {
        'available': True,
        'actionable': bool(getattr(ts, 'is_actionable_entry', False)),
        'current': current,
        'entry': float(ts.entry_price or 0.0),
        'stop': float(ts.stop_loss or 0.0),
        't1': float(ts.target_conservative or 0.0),
        't2': float(getattr(ts, 'horizon_target', 0.0) or 0.0),
        'valuation_target': float(ts.target_base or 0.0),
        't3': float(ts.target_aggressive or 0.0),
        'rr': float(ts.risk_reward_ratio or 0.0),
        'risk_amount': float(ts.risk_amount or 0.0),
        'trailing_pct': float(ts.trailing_stop_pct or 0.0),
        'confidence_tier': getattr(ts, 'confidence_tier', 'MEDIUM'),
        'has_long_reward': bool(getattr(ts, 'has_long_reward', True)),
        'basis': getattr(ts, 'setup_basis', ''),
        'review_date': getattr(ts, 'review_date', ''),
        'horizon_days': int(getattr(ts, 'estimated_days', 0) or 0),
    }


def _entry_timing_is_clear(result: 'AnalysisResult') -> bool:
    """Whether the tape confirms entering NOW, as opposed to the thesis holding.

    Reads the same candlestick/timing evidence the BEGINNER GUIDANCE panel uses
    for its "Buy Now" line, so the two can no longer disagree.
    """
    try:
        cs = getattr(result, 'candlestick', None)
        if cs is not None:
            bias = str(getattr(cs, 'net_bias', '') or '').upper()
            if 'BEAR' in bias:
                return False
        expl = getattr(result, 'signal_explanation', None)
        if expl is not None:
            timing = str(getattr(expl, 'timing', '') or '').upper()
            if 'WAIT' in timing or 'NO CLEAR' in timing or 'AVOID' in timing:
                return False
        t = getattr(result, 'technical', None)
        if t is not None:
            # An unconfirmed tape (thin volume) is not a green light either.
            vr = float(getattr(t, 'volume_ratio', 1.0) or 1.0)
            if vr < 0.8:
                return False
    except Exception:
        return True
    return True


def audience_framing(result: 'AnalysisResult') -> Dict[str, str]:
    """Who is this recommendation addressed to, and in what words.

    [T4 FIX] The legacy ``Signal`` enum has no WATCHLIST member, so
    ``_FINAL_ACTION_TO_SIGNAL`` maps both WATCHLIST and HOLD_EXISTING onto
    ``Signal.HOLD``. Three panels branched on ``signal`` and therefore told a
    reader who owns nothing to "keep existing positions", "maintain existing,
    set stops" and "hold existing position, no new entries". The BEGINNER
    GUIDANCE panel already got this right by addressing the two audiences
    separately; this makes that available everywhere.
    """
    action = str(getattr(result, 'final_action', '') or '').upper()
    owned = bool(getattr(result, 'has_existing_position', False))

    if action == 'WATCHLIST':
        return {
            'action': 'WATCHLIST',
            'headline': '👁  WATCHLIST — NO POSITION',
            'one_liner': 'Not a buy today. Track it and wait for the setup to earn an entry.',
            'new_buyer': 'Do not open a position at these levels',
            'holder': ('If you already own it: the levels below are your risk plan'
                       if not owned else 'Hold what you own to the stop below'),
        }
    if action == 'HOLD_EXISTING':
        return {
            'action': 'HOLD_EXISTING',
            'headline': '🔵 HOLD — EXISTING POSITION ONLY',
            'one_liner': 'Keep what you own. This is not a new-money entry.',
            'new_buyer': 'No new entry at these levels',
            'holder': 'Hold to the stop below; trim into the first target',
        }
    if action == 'AVOID':
        return {
            'action': 'AVOID',
            'headline': '🔴 AVOID / REDUCE',
            'one_liner': 'Exit or reduce. Do not add.',
            'new_buyer': 'Do not open a position',
            'holder': 'Scale out into strength rather than at the market',
        }
    if action == 'INVALID':
        return {
            'action': 'INVALID',
            'headline': '⚪ NO VERDICT — DATA INSUFFICIENT',
            'one_liner': 'The data did not support an analysis. No recommendation is made.',
            'new_buyer': 'No recommendation',
            'holder': 'No recommendation',
        }
    if action in ('STRONG_BUY', 'BUY', 'ACCUMULATE'):
        label = {'STRONG_BUY': '🟢 STRONG BUY', 'BUY': '🟢 BUY',
                 'ACCUMULATE': '🟡 ACCUMULATE'}[action]
        # [T15 FIX] The thesis verdict and the ENTRY TIMING are separate
        # questions, and the report answered them in different panels without
        # reconciling them: the snapshot said "Entry is actionable" while the
        # guidance panel said "Buy Now: NO / New Buyer: Wait" for the same
        # ticker. A reader cannot act on both. The verdict is stated first, and
        # the timing qualifier travels with it everywhere.
        timing_ok = _entry_timing_is_clear(result)
        one_liner = {
            'STRONG_BUY': 'High-conviction thesis. Size to the risk plan below.',
            'BUY': 'Actionable thesis. Size to the risk plan below.',
            'ACCUMULATE': 'Scale in. Start partial and add on confirmation.',
        }[action]
        if not timing_ok:
            one_liner += " Timing is not confirmed — stage the entry, do not chase."
        return {
            'action': action,
            'headline': label + ("" if timing_ok else "  (timing: stage in)"),
            'one_liner': one_liner,
            'new_buyer': ('Entry is actionable — see the risk plan below' if timing_ok
                          else 'Thesis qualifies, but wait for the pullback or the '
                               'breakout confirmation below'),
            'holder': 'Add only on the terms below; keep the same stop',
        }
    return {
        'action': action or 'UNKNOWN',
        'headline': '⚪ NO CLEAR VERDICT',
        'one_liner': 'The factors do not line up. No action recommended.',
        'new_buyer': 'No new entry',
        'holder': 'Review against your own plan',
    }


def display_market_environment(env: Dict[str, Any], sym: str = "$") -> None:
    """Display compact market environment summary using rich"""
    console = Console()
    
    if not env.get('fetch_success', False):
        console.print(Panel("[bold red]Market data unavailable[/bold red]", title="[bold]MARKET ENVIRONMENT[/bold]", border_style="red", box=box.ROUNDED))
        return
    
    # ── Market snapshot: SPY + VIX + Sentiment ──
    trend_sym = arrow_for(env.get('spy_change_pct'))
    trend_color = "green" if "UPTREND" in env['spy_trend'] else ("red" if "DOWNTREND" in env['spy_trend'] else "yellow")
    day_color = direction_style(env.get('spy_change_pct'))
    trend_label = env['spy_trend'].split('(')[0].strip() if '(' in env['spy_trend'] else env['spy_trend']
    vix_label = "Low" if env['vix'] < 15 else ("Normal" if env['vix'] < 20 else ("Elevated" if env['vix'] < 30 else "High"))
    vix_color = "green" if env['vix'] < 15 else ("yellow" if env['vix'] < 20 else "red")
    
    market_table = Table(show_header=False, box=None, expand=True)
    market_table.add_column("Metric", style="bold")
    market_table.add_column("Value")
    market_table.add_column("Metric2", style="bold")
    market_table.add_column("Value2")
    
    market_table.add_row("SPY:", f"{sym}{env['spy_price']:.2f} [{day_color}]{trend_sym} {env['spy_change_pct']:+.2f}%[/{day_color}]", "VIX:", f"[{vix_color}]{env['vix']:.1f} ({vix_label})[/{vix_color}]")
    market_table.add_row("Fear/Greed:", f"{env['fear_greed']} ({env['fear_greed_label']})", "Trend:", f"[{trend_color}]{trend_label}[/{trend_color}]")
    
    # ── Fed & Macro ──
    fed_outlook = env.get('fed_outlook', 'Unknown')
    if len(fed_outlook) > 55:
        fed_outlook = fed_outlook[:52] + "..."
    
    days_to_fomc = env.get('days_to_fomc', -1)
    fomc_date = env.get('fomc_date', 'Unknown')
    treasury_10y = env.get('treasury_10y', 0)
    yield_curve = env.get('yield_curve', 'NORMAL')
    
    fomc_str = f"{fomc_date}" + (f" ({days_to_fomc}d)" if days_to_fomc > 0 else "")
    if 0 < days_to_fomc <= 14:
        fomc_str = f"[bold red]in {days_to_fomc}d ({fomc_date})[/bold red]"
        
    market_table.add_row("Fed Outlook:", f"{fed_outlook}", "FOMC:", f"{fomc_str}")
    market_table.add_row("10Y Treasury:", f"{treasury_10y:.2f}%", "Yield Curve:", f"{yield_curve}")
    
    # ── Alerts ──
    macro_risks = env.get('macro_risk_factors', [])
    macro_risk_score = env.get('macro_risk_score', 5)
    if macro_risk_score >= 7 and macro_risks:
        market_table.add_row("Macro Risk:", f"[bold red]{macro_risk_score}/10 - {macro_risks[0][:60]}[/bold red]", "", "")
    
    mega_warning = env.get('mega_earnings_warning', '')
    mega_earnings = env.get('mega_earnings_this_week', [])
    if mega_warning:
        market_table.add_row("Mega-Cap Warn:", f"[bold yellow]{mega_warning[:72]}[/bold yellow]", "", "")
    elif mega_earnings:
        market_table.add_row("Mega Earnings:", f"{', '.join(mega_earnings[:5])}", "", "")
    
    # ── Sectors ──
    leaders = env.get('sector_leaders', [])
    laggards = env.get('sector_laggards', [])
    if leaders or laggards:
        parts = []
        for n, p in (leaders[:2] if leaders else []):
            style = direction_style(p)
            parts.append(f"[{style}]{arrow_for(p)} {n} {p:+.1f}%[/{style}]")
        for n, p in (laggards[:2] if laggards else []):
            style = direction_style(p)
            parts.append(f"[{style}]{arrow_for(p)} {n} {p:+.1f}%[/{style}]")
        market_table.add_row("Sectors:", f"{'   '.join(parts)}", "", "")
        
    console.print(Panel(market_table, title="[bold]MARKET ENVIRONMENT[/bold]", border_style="blue", box=box.ROUNDED))


def display_position_deep_dive(result: 'AnalysisResult', sym: str = "$") -> None:
    """Display Position Recommendation with modern clean layout"""
    
    print(f"\n\n  POSITION RECOMMENDATION")
    print(f"  {'─'*76}")
    
    # Determine action
    signal = result.signal
    confidence = result.confidence_score
    
    if signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
        action_emoji = "🟢"
        action_text = "ACCUMULATE"
        action_desc = "Strong setup, add on dips"
    elif signal == Signal.HOLD:
        action_emoji = "🔵"
        action_text = "HOLD"
        action_desc = "Position sound, no changes"
    elif signal == Signal.REDUCE:
        action_emoji = "🟡"
        action_text = "TRIM"
        action_desc = "Take partial profits"
    else:
        action_emoji = "🔴"
        action_text = "SELL"
        action_desc = "Exit position"
    
    # Confidence bar
    conf_bar = "█" * int(confidence / 10) + "░" * (10 - int(confidence / 10))
    conf_label = "Very High" if confidence >= 80 else ("High" if confidence >= 65 else ("Medium" if confidence >= 50 else ("Low" if confidence >= 35 else "Very Low")))
    
    # Price targets
    current = result.current_price
    # [T3 FIX] No fabricated fallbacks — see trade_levels().
    _lv = trade_levels(result)
    entry = _lv['entry'] or current
    stop = _lv['stop']
    pt1 = _lv['t1']
    pt2 = _lv['t2']
    rr = _lv['rr']
    
    pt1_pct = ((pt1 - current) / current) * 100 if current > 0 else 0
    pt2_pct = ((pt2 - current) / current) * 100 if current > 0 else 0
    
    # Main signal display
    print(f"\n  {action_emoji} {action_text:<12}                           Confidence  {confidence:.0f}%  {conf_bar}")
    print(f"  {action_desc}")
    
    # Price levels
    print(f"\n  Entry       {sym}{entry:<10.2f}                    Stop-Loss   {sym}{stop:.2f}")
    print(f"  Target 1    {sym}{pt1:<10.2f} ({pt1_pct:+.1f}%)            Target 2    {sym}{pt2:.2f} ({pt2_pct:+.1f}%)")
    # [T6 FIX] Was "Win Prob 50%" — an uncalibrated figure printed as a
    # probability. The tier is what the engine can actually support.
    print(f"  Risk/Reward {rr:.2f}:1                           Confidence  {_lv['confidence_tier']}")
    
    # Thesis
    thesis_text = ""
    if result.key_insights and len(result.key_insights) > 0:
        # Clean up insight text
        insight = str(result.key_insights[0]).replace("⏸️ ", "").replace("🟢 ", "").replace("📈 ", "").replace("📊 ", "")
        thesis_text = insight[:70]
    
    risk_text = ""
    if result.risk_warnings and len(result.risk_warnings) > 0:
        risk = str(result.risk_warnings[0]).replace("⚠️ ", "").replace("🚨 ", "")
        risk_text = risk[:60]
    
    if thesis_text:
        print(f"\n  Thesis: {thesis_text}")
    if risk_text:
        print(f"  Risk: {risk_text}")


def display_decision_snapshot(result: 'AnalysisResult', sym: str = "$") -> None:
    """The one panel that has to be right: verdict, levels, size, and who it is for.

    [T4/T9/T10 FIX] This used to branch on the legacy ``signal`` enum, where
    WATCHLIST and HOLD_EXISTING both collapse onto ``Signal.HOLD`` — so a
    reader who owns nothing was told "Hold existing position. Not conviction
    for new entries." It read upside from ``market_intel.implied_upside``,
    which is frequently 0, and printed "UPSIDE: +0%" beside a real target. It
    showed no position size at all, despite ``account_size`` being a required
    argument and ``PositionSizer`` having already run. And its box borders were
    padded by character count, so every emoji knocked a row out of alignment.
    """
    def make_bar(score, width=10):
        filled = int(max(0.0, min(float(score or 0), 100.0)) / (100 / width))
        return "█" * filled + "░" * (width - filled)

    framing = audience_framing(result)
    lv = trade_levels(result)

    confidence = result.confidence_score
    score = result.scores.composite_score if result.scores else 50
    risk_score = result.risk.overall_risk_score if result.risk else 5
    risk_grade = result.risk.risk_grade if result.risk else "C"
    conf_label = "HIGH" if confidence >= 65 else ("MEDIUM" if confidence >= 45 else "LOW")

    # Upside now comes from the SAME target the levels panel prints, so the two
    # can no longer disagree. Analyst-implied upside is shown separately.
    upside = ((lv['t2'] - lv['current']) / lv['current'] * 100) if (lv['available'] and lv['current'] > 0) else 0.0
    downside = ((lv['stop'] - lv['current']) / lv['current'] * 100) if (lv['available'] and lv['current'] > 0) else 0.0
    analyst_upside = getattr(result.market_intel, 'implied_upside', 0.0) if result.market_intel else 0.0

    price_emoji = "🟢" if result.day_change_pct >= 0 else "🔴"

    print("\n")
    print("  " + "═" * 74)
    print("  " + _pad("DECISION SNAPSHOT", 74, '^'))
    print("  " + "═" * 74)

    print(f"\n  {result.company_name} ({result.ticker})")
    print(f"  {sym}{result.current_price:,.2f}  {price_emoji} {result.day_change:+.2f} "
          f"({result.day_change_pct:+.2f}%)")

    if result.pre_market_price > 0:
        pre_emoji = "🟢" if result.pre_market_change_pct >= 0 else "🔴"
        print(f"  Pre-Market: {sym}{result.pre_market_price:,.2f} {pre_emoji} "
              f"{result.pre_market_change_pct:+.1f}%")
    elif result.after_hours_price > 0:
        ah_emoji = "🟢" if result.after_hours_change_pct >= 0 else "🔴"
        print(f"  After-Hours: {sym}{result.after_hours_price:,.2f} {ah_emoji} "
              f"{result.after_hours_change_pct:+.1f}%")

    print()
    score_txt = f"{score:>3.0f}"
    rows = [
        f"{framing['headline']}",
        "",
        f"Confidence  {confidence:>3.0f}%  {make_bar(confidence)}  {conf_label}",
        f"Composite   {score_txt:<8} {make_bar(score)}  Risk grade {risk_grade} ({risk_score}/10)",
    ]
    if lv['available']:
        rows += [
            "",
            f"To target   {upside:+.1f}%   (T2 {sym}{lv['t2']:,.2f})"
            + (f"   ·   analyst {analyst_upside:+.0f}%" if analyst_upside else ""),
            f"To stop     {downside:+.1f}%   ({sym}{lv['stop']:,.2f})   ·   R:R {lv['rr']:.2f}:1",
            f"Horizon     {lv['horizon_days']} days   ·   review {lv['review_date']}",
        ]
    else:
        rows += ["", f"Levels      not computed — {lv['basis']}"]
    _box(rows)

    print(f"\n  ⚡ {framing['one_liner']}")

    # [T9 FIX] The size. This is what the reader came for and it was absent.
    ps = getattr(result, 'position_sizing', None)
    if ps and getattr(ps, 'recommended_shares', 0) > 0:
        verb = "Buy" if lv['actionable'] else "If entered"
        print(f"  📐 {verb} {ps.recommended_shares:,} shares "
              f"≈ {sym}{ps.position_value:,.0f} ({ps.portfolio_weight:.1%} of account) "
              f"· risk {sym}{ps.dollar_risk:,.0f} ({ps.risk_percent:.2%})")
        if not lv['actionable']:
            print("     (size shown for reference — the verdict above is not an entry)")
        for w in (getattr(ps, 'warnings', None) or [])[:2]:
            print(f"     ⚠ {w}")
    elif lv['actionable']:
        print("  📐 Position size not viable at this account size and stop distance.")

    print("  " + "═" * 74)


def display_signal_explanation(result: 'AnalysisResult', sym: str = "$") -> None:
    """Display Why This Signal section explaining the reasoning behind the signal"""
    
    explanation = result.signal_explanation
    if not explanation:
        return
    
    signal = result.signal
    
    # Get signal emoji
    if signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
        signal_emoji = "🟢"
    elif signal == Signal.HOLD:
        signal_emoji = "🔵"
    elif signal == Signal.REDUCE:
        signal_emoji = "🟡"
    else:
        signal_emoji = "🔴"
    
    print(f"\n\n  WHY THIS SIGNAL: {signal_emoji} {signal.value}")
    print(f"  {'─'*76}")
    
    # Primary reason (composite score or override)
    if explanation.overrides_triggered:
        # Show override as the main reason
        print(f"\n  ⛔ OVERRIDE ACTIVE:")
        for override in explanation.overrides_triggered:
            print(f"     • {override}")
        print(f"\n  Original signal based on composite score {explanation.composite_score:.0f}")
    else:
        # Show composite score reasoning
        print(f"\n  • Composite Score: {explanation.composite_score:.0f}/100 ({explanation.score_threshold})")
    
    # Supporting factors
    if explanation.supporting_factors:
        print(f"\n  ✅ Supporting Factors:")
        for factor in explanation.supporting_factors[:3]:
            print(f"     • {factor}")
    
    # Risk factors
    if explanation.risk_factors:
        print(f"\n  ⚠️ Risk Factors:")
        for factor in explanation.risk_factors[:3]:
            print(f"     • {factor}")
    
    print(f"  {'─'*76}")


def display_what_to_do(result: 'AnalysisResult', sym: str = "$") -> None:
    """The single execution panel: what to do, at what price, in what size.

    [T4/T7/T9 FIX] This branched on the legacy ``signal`` enum, so a WATCHLIST
    verdict — which maps onto ``Signal.HOLD`` because the enum has no
    WATCHLIST member — printed "HOLD / NO NEW ENTRIES", "Maintain existing,
    set stops" and "Keep existing positions" to a reader who owns nothing. It
    also hardcoded a 7% trailing stop here and a 9% one in the reconciled
    panel, neither derived from ATR or from the ``trailing_stop_pct`` the
    setup computes. And it never showed a position size.
    """
    framing = audience_framing(result)
    lv = trade_levels(result)
    confidence = result.confidence_score
    current = result.current_price
    risk_grade = result.risk.risk_grade if result.risk else "C"
    ps = getattr(result, 'position_sizing', None)

    print("\n\n  " + "═" * 74)
    print("  " + _pad("WHAT TO DO", 74, '^'))
    print("  " + "═" * 74)

    conv_label = "HIGH" if confidence >= 70 else ("MEDIUM" if confidence >= 55 else "LOW")
    print()
    _box([
        framing['headline'],
        framing['one_liner'],
        "",
        f"Conviction {conv_label} ({confidence:.0f}%)   ·   Risk grade {risk_grade}"
        + (f"   ·   R:R {lv['rr']:.2f}:1" if lv['available'] else ""),
    ])

    # ── Levels ────────────────────────────────────────────────────────────
    if not lv['available']:
        print(f"\n  LEVELS")
        _rule()
        print(f"  Not computed — {lv['basis']}")
        print("  No stop, target or size is shown, because none was derived.")
    else:
        heading = "TRADE SETUP" if lv['actionable'] else "REFERENCE LEVELS (not an entry order)"
        print(f"\n  {heading}")
        _rule()
        stop_pct = ((lv['stop'] - lv['entry']) / lv['entry'] * 100) if lv['entry'] else 0
        t1_pct = ((lv['t1'] - current) / current * 100) if current else 0
        t2_pct = ((lv['t2'] - current) / current * 100) if current else 0

        if lv['actionable']:
            print(f"  Entry limit    {sym}{lv['entry']:,.2f}   (do not chase above it)")
        else:
            print(f"  Reference      {sym}{lv['entry']:,.2f}   (spot — no entry recommended)")
        print(f"  Stop-loss      {sym}{lv['stop']:,.2f} ({stop_pct:+.1f}%)   "
              f"risk {sym}{lv['risk_amount']:,.2f}/share")
        if lv['has_long_reward']:
            print(f"  Target 1       {sym}{lv['t1']:,.2f} ({t1_pct:+.1f}%)   → take a third")
            print(f"  Target 2       {sym}{lv['t2']:,.2f} ({t2_pct:+.1f}%)   → take a third")
            print(f"  Trail          {lv['trailing_pct']:.1f}% "
                  f"({TradeSetupGenerator.TRAILING_ATR_MULT:g}xATR) on the balance")
        else:
            # [T13 FIX] No profit targets are printed when the engine's own
            # valuation says fair value is at or below spot. Previously the
            # display would have shown a target BELOW the stop as
            # "take a third".
            _vt = lv.get('valuation_target') or 0.0
            _vt_pct = ((_vt - current) / current * 100) if current else 0
            print(f"  Fair value     {sym}{_vt:,.2f} ({_vt_pct:+.1f}%)   "
                  f"← at or below spot: no long reward")
            print(f"  Targets        none — the valuation does not support a long here")
        _rule()
        print(f"  R:R {lv['rr']:.2f}:1   ·   confidence tier {lv['confidence_tier']}   ·   "
              f"horizon {lv['horizon_days']}d, review {lv['review_date']}")
        print(f"  Basis: {lv['basis']}")

    # ── Size and cost, in dollars ─────────────────────────────────────────
    # [T9 FIX] `account_size` is a required argument and PositionSizer runs on
    # every analysis, yet no panel ever displayed a size.
    if lv['available']:
        print(f"\n  POSITION SIZE")
        _rule()
        if ps and getattr(ps, 'recommended_shares', 0) > 0:
            verb = "Buy" if lv['actionable'] else "Would be"
            print(f"  {verb:<10}     {ps.recommended_shares:,} shares "
                  f"≈ {sym}{ps.position_value:,.0f} ({ps.portfolio_weight:.1%} of account)")
            print(f"  Risk at stop   {sym}{ps.dollar_risk:,.0f} ({ps.risk_percent:.2%} of account)")
            print(f"  Method         {ps.sizing_method}"
                  + (f"   ·   cap {ps.max_portfolio_weight:.0%}" if ps.max_portfolio_weight else ""))
            cost_pct = TradeSetupGenerator.round_trip_cost_pct()
            cost_dollars = ps.position_value * cost_pct / 100
            print(f"  Round-trip cost {sym}{cost_dollars:,.2f} ({cost_pct:.2f}%) "
                  f"— already deducted from the R:R above")
            if not lv['actionable']:
                print("  (reference only — the verdict above is not an entry)")
            for w in (getattr(ps, 'warnings', None) or []):
                print(f"  ⚠ {w}")
        else:
            reason = ("the verdict is not an entry" if not lv['actionable']
                      else "the risk budget cannot fund one whole share at this stop")
            print(f"  Not sized — {reason}.")

    # ── Audience split ────────────────────────────────────────────────────
    print(f"\n  WHO THIS IS FOR")
    _rule()
    print(f"  New buyer         {framing['new_buyer']}")
    print(f"  Existing holder   {framing['holder']}")

    print(f"\n  INVESTOR SUITABILITY")
    _rule()
    if framing['action'] in ('STRONG_BUY', 'BUY', 'ACCUMULATE'):
        if confidence >= 65 and risk_grade in ('A', 'B'):
            print("  Conservative: ✅   Growth: ✅   Aggressive: ✅")
        elif confidence >= 55:
            print("  Conservative: ⚠️    Growth: ✅   Aggressive: ✅ (no leverage)")
        else:
            print("  Conservative: ❌   Growth: ✅   Aggressive: ✅")
    elif framing['action'] == 'WATCHLIST':
        print("  Conservative: ❌   Growth: ❌   Aggressive: ❌  (no entry for anyone today)")
    elif framing['action'] == 'HOLD_EXISTING':
        print("  Holders only — no new entry for any profile")
    else:
        print("  Conservative: ❌   Growth: ❌   Aggressive: ❌  (exit recommended)")


def display_execution_context(result: 'AnalysisResult', sym: str = "$") -> None:
    """Everything that decides whether the trade is executable, not just attractive.

    [NEW] The report analysed the thesis thoroughly and said nothing about
    whether the position could actually be put on: no liquidity check against
    the proposed size, no earnings-event proximity, no cost drag in dollars,
    and no provenance for the numbers. All four inputs were already computed
    or available; none reached the page.
    """
    lv = trade_levels(result)
    ps = getattr(result, 'position_sizing', None)
    t = result.technical
    cat = getattr(result, 'catalysts', None)

    print(f"\n\n  " + "═" * 74)
    print("  " + _pad("EXECUTION CONTEXT", 74, '^'))
    print("  " + "═" * 74)

    # ── Liquidity ─────────────────────────────────────────────────────────
    print(f"\n  LIQUIDITY")
    _rule()
    adv_shares = float(getattr(t, 'volume_sma', 0.0) or 0.0)
    price = float(result.current_price or 0.0)
    adv_dollars = adv_shares * price
    if adv_dollars > 0:
        print(f"  20d average volume   {adv_shares:,.0f} shares "
              f"≈ {sym}{adv_dollars/1e6:,.1f}M/day")
        if ps and getattr(ps, 'position_value', 0) > 0:
            pct_adv = ps.position_value / adv_dollars * 100
            verdict = ("fine" if pct_adv < 1 else
                       "watch the fill" if pct_adv < 5 else
                       "TOO LARGE — expect material slippage")
            pct_txt = "<0.01%" if 0 < pct_adv < 0.01 else f"{pct_adv:.2f}%"
            print(f"  This position         {pct_txt} of a day's volume — {verdict}")
        vr = float(getattr(t, 'volume_ratio', 0.0) or 0.0)
        if vr > 0:
            tone = ("thin — conviction unconfirmed" if vr < 0.8 else
                    "normal" if vr < 1.5 else "elevated — something is happening")
            print(f"  Today vs 20d avg      {vr:.2f}x — {tone}")
    else:
        print("  Volume data unavailable — liquidity not assessed")

    # ── Event risk ────────────────────────────────────────────────────────
    print(f"\n  EVENT RISK")
    _rule()
    days = int(getattr(cat, 'days_to_earnings', -1) or -1) if cat else -1
    edate = str(getattr(cat, 'earnings_date', 'Unknown') or 'Unknown') if cat else 'Unknown'
    if days >= 0:
        if days <= 3:
            note = "IMMINENT — a gap through the stop is likely; size down or wait"
        elif days <= 14:
            note = "inside the horizon — the event will decide the trade, not the setup"
        elif days <= 30:
            note = "elevated — plan to be flat or reduced into it"
        else:
            note = "clear of the near horizon"
        print(f"  Next earnings         {edate} — {days} day(s) away: {note}")
    else:
        # A missing calendar is a data gap, not an all-clear.
        print(f"  Next earnings         UNKNOWN — treat as a data gap, not an all-clear")
    exd = str(getattr(cat, 'ex_dividend_date', 'Unknown') or 'Unknown') if cat else 'Unknown'
    if exd and exd != 'Unknown':
        print(f"  Ex-dividend           {exd}")
    if lv['available'] and lv['horizon_days']:
        print(f"  Planned horizon       {lv['horizon_days']} days "
              f"(review {lv['review_date']})")

    # ── Cost drag ─────────────────────────────────────────────────────────
    print(f"\n  COST OF THE ROUND TRIP")
    _rule()
    cost_pct = TradeSetupGenerator.round_trip_cost_pct()
    print(f"  Modelled cost         {cost_pct:.2f}% "
          f"({TradeSetupGenerator.SPREAD_BPS}bps spread + "
          f"{TradeSetupGenerator.SLIPPAGE_BPS}bps slippage each way)")
    if ps and getattr(ps, 'position_value', 0) > 0:
        cost_dollars = ps.position_value * cost_pct / 100
        print(f"  On this position      {sym}{cost_dollars:,.2f}")
        if lv['available'] and lv['has_long_reward'] and lv['t2'] > lv['entry'] > 0:
            gross = (lv['t2'] - lv['entry']) / lv['entry'] * 100
            print(f"  Gross move to T2      {gross:+.1f}%  →  net {gross - cost_pct:+.1f}% "
                  f"after costs")
    print(f"  Already deducted from the R:R shown above — not an extra haircut.")

    # ── Provenance ────────────────────────────────────────────────────────
    print(f"\n  DATA PROVENANCE")
    _rule()
    provider = getattr(result, 'data_provider', 'unknown')
    status = str(getattr(result, 'data_status', 'ok') or 'ok')
    reason = str(getattr(result, 'data_status_reason', '') or '')
    mkt_ts = str(getattr(result, 'market_timestamp', '') or '')
    ret_ts = str(getattr(result, 'retrieval_timestamp', '') or '')
    print(f"  Provider              {provider}   ·   currency {getattr(result, 'currency', '?')}")
    print(f"  Last market bar       {mkt_ts[:19] or 'unknown'}")
    print(f"  Retrieved             {ret_ts[:19] or 'unknown'}")
    flag = "OK" if status == 'ok' else f"{status.upper()}"
    print(f"  Status                {flag}" + (f" — {reason}" if reason else ""))
    if getattr(result, 'is_fast_mode', False):
        print("  Mode                  FAST — peer comparison and some modules were skipped")
    print("  " + "═" * 74)


def display_peer_comparison(result: 'AnalysisResult', sym: str = "$") -> None:
    """Display comparison vs sector peers"""
    
    peer = result.peer_comparison
    if not peer or not peer.sector or peer.sector == "Unknown":
        return
    
    # Check if we have any meaningful data
    if peer.pe_sector_avg == 0 and peer.roe_sector_avg == 0:
        return
    
    print(f"\n\n  VS SECTOR PEERS ({peer.sector})")
    print(f"  {'─'*76}")
    
    # P/E comparison
    if peer.pe_stock > 0 and peer.pe_sector_avg > 0:
        pe_emoji = "🟢" if peer.pe_vs_sector < -10 else ("🔴" if peer.pe_vs_sector > 10 else "🟡")
        pe_label = "discount" if peer.pe_vs_sector < 0 else "premium"
        print(f"\n  P/E:   {peer.pe_stock:.1f} vs sector avg {peer.pe_sector_avg:.1f}  ({peer.pe_vs_sector:+.0f}% {pe_label})  {pe_emoji}")
    
    # ROE comparison
    if peer.roe_stock > 0 and peer.roe_sector_avg > 0:
        roe_emoji = "🟢" if peer.roe_vs_sector > 10 else ("🔴" if peer.roe_vs_sector < -10 else "🟡")
        roe_label = "outperforming" if peer.roe_vs_sector > 0 else "underperforming"
        print(f"  ROE:   {peer.roe_stock:.1f}% vs sector avg {peer.roe_sector_avg:.1f}%  ({roe_label})  {roe_emoji}")
    
    # P/B comparison
    if peer.pb_stock > 0 and peer.pb_sector_avg > 0:
        pb_emoji = "🟢" if peer.pb_vs_sector < -10 else ("🔴" if peer.pb_vs_sector > 10 else "🟡")
        pb_label = "discount" if peer.pb_vs_sector < 0 else "premium"
        print(f"  P/B:   {peer.pb_stock:.1f} vs sector avg {peer.pb_sector_avg:.1f}  ({peer.pb_vs_sector:+.0f}% {pb_label})  {pb_emoji}")
    
    # Sector ranking
    if peer.sector_rank > 0 and peer.sector_total > 0:
        print(f"  {'─'*76}")
        rank_emoji = "🏆" if peer.sector_rank <= 3 else ("🥈" if peer.sector_rank <= peer.sector_total * 0.5 else "")
        print(f"  Ranking: #{peer.sector_rank} of {peer.sector_total} in sector  {rank_emoji} {peer.overall_assessment}")
    
    print(f"  {'─'*76}")


def display_signal_track_record(analyzer: 'QuantaEliteAnalyzer' = None) -> None:
    """Benchmarked outcomes of published deep-analysis Decisions (SQLite ledger).

    Replaces the legacy JSON tracker's raw "win rate", which was scored
    against whatever price was current when its updater ran and was never
    compared with a benchmark.
    """
    try:
        from tracking.ledger import default_ledger
        groups = [g for g in default_ledger().summarize(engine="elite") if g.n_resolved]
    except Exception:
        return
    if not groups:
        return
    print("\n\n  TRACK RECORD (published Decisions vs benchmark, next-open entry)")
    print(f"  {'─'*76}")
    for g in groups:
        k = g.key
        mean = f"{g.mean_excess_pct:+.2f}%" if g.mean_excess_pct is not None else "n/a"
        print(f"  {k.get('stance', ''):<8} {k.get('horizon_days', '')}d  resolved {g.n_resolved:<4} "
              f"mean excess {mean:<8} {g.verdict}")
    print(f"  {'─'*76}")


def display_company_news(result: 'AnalysisResult', sym: str = "$") -> None:
    """Display recent company news with source, date, and sentiment"""
    
    sentiment = result.sentiment
    if not sentiment or not sentiment.top_articles:
        return
    
    articles = sentiment.top_articles[:8]  # Show up to 5 recent articles
    
    print(f"\n\n  📰 RECENT COMPANY NEWS")
    print(f"  {'─'*76}")
    
    # Overview line
    sentiment_emoji = "🟢" if sentiment.sentiment_label == "POSITIVE" else ("🔴" if sentiment.sentiment_label == "NEGATIVE" else "🟡")
    print(f"\n  Sentiment: {sentiment_emoji} {sentiment.sentiment_label} ({sentiment.sentiment_score:+.2f})  |  {sentiment.news_volume} articles  |  Trend: {sentiment.sentiment_trend}")
    
    # News topic flags
    flags = []
    if sentiment.earnings_mentions > 0:
        flags.append(f"💰 Earnings ({sentiment.earnings_mentions})")
    if sentiment.analyst_mentions > 0:
        flags.append(f"📊 Analyst ({sentiment.analyst_mentions})")
    if sentiment.merger_mentions > 0:
        flags.append(f"🤝 M&A ({sentiment.merger_mentions})")
    if sentiment.legal_mentions > 0:
        flags.append(f"⚖️ Legal ({sentiment.legal_mentions})")
    if sentiment.regulatory_mentions > 0:
        flags.append(f"📋 Regulatory ({sentiment.regulatory_mentions})")
    if sentiment.executive_mentions > 0:
        flags.append(f"👔 Executive ({sentiment.executive_mentions})")
    
    if flags:
        print(f"  Topics: {' | '.join(flags[:4])}")
    
    print(f"  {'─'*76}")
    
    # Initialize VADER for per-article sentiment
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        vader = SentimentIntensityAnalyzer()
    except ImportError:
        vader = None
    
    for i, article in enumerate(articles, 1):
        title = article.get('title', 'No title')[:80]
        source = article.get('source', 'Unknown')[:15]
        published = article.get('published')
        
        # Format date
        if published:
            try:
                if isinstance(published, datetime):
                    date_str = published.strftime('%b %d')
                else:
                    date_str = str(published)[:10]
            except:
                date_str = "Recent"
        else:
            date_str = "Recent"
        
        # Per-article sentiment
        if vader:
            text = f"{title} {article.get('summary', '')}"
            score = vader.polarity_scores(text)['compound']
            if score > 0.1:
                sent_emoji = "🟢"
            elif score < -0.1:
                sent_emoji = "🔴"
            else:
                sent_emoji = "🟡"
        else:
            sent_emoji = "⚪"
        
        # Print article
        print(f"\n  {sent_emoji} {title}")
        print(f"     └─ {source} • {date_str}")
    
    # Controversy warning if applicable
    if sentiment.controversy_score > 3:
        print(f"\n  ⚠️  CONTROVERSY ALERT: Elevated negative coverage detected ({sentiment.controversy_score:.1f}/10)")
    
    print(f"  {'─'*76}")


def display_catalyst_alerts(result: 'AnalysisResult', sym: str = "$") -> None:
    """
    Display actionable news-driven catalysts detected by the CatalystScanner.
    
    Professional hedge funds act on catalysts within hours/days of detection.
    This section provides the same actionable intelligence.
    """
    # Check if catalyst scanner is available and has detected catalysts
    if not HAS_CATALYST or not CatalystScanner:
        return
    
    try:
        scanner = CatalystScanner()
        
        # Get headlines from multiple sources (robust fallback chain)
        headlines = []
        
        # Source 1: sentiment.key_headlines
        if result.sentiment and hasattr(result.sentiment, 'key_headlines') and result.sentiment.key_headlines:
            headlines = list(result.sentiment.key_headlines[:20])
        
        # Source 2: sentiment.top_articles
        if not headlines and result.sentiment and hasattr(result.sentiment, 'top_articles') and result.sentiment.top_articles:
            headlines = [a.get('title', '') for a in result.sentiment.top_articles[:20] if a.get('title')]
        
        if not headlines:
            return
        
        # Scan for catalysts
        catalysts = scanner.scan_headlines(result.ticker, headlines)
        actionable = [c for c in catalysts if c.is_actionable]
        
        if not actionable:
            return
        
        # Calculate volume ratio for validation
        volume_ratio = 1.0
        if result.technical and hasattr(result.technical, 'volume_ratio'):
            volume_ratio = result.technical.volume_ratio or 1.0
        
        # Generate signal
        catalyst_signal = scanner.generate_signal(result.ticker, catalysts, volume_ratio)
        
        if not catalyst_signal:
            return
        
        # Display catalyst section
        print(f"\n\n  🎯 ACTIONABLE CATALYST ALERTS")
        print(f"  {'─'*76}")
        
        # Catalyst summary
        volume_status = "✅ CONFIRMED" if catalyst_signal.volume_confirmed else "⚠️ UNCONFIRMED"
        print(f"\n  Catalysts Detected: {len(actionable)}  |  Volume: {volume_status} ({volume_ratio:.1f}x avg)")
        
        # Trading implications
        action_color = "🟢" if catalyst_signal.recommended_action == "BUY" else ("🔴" if catalyst_signal.recommended_action == "AVOID" else "🟡")
        print(f"  Action: {action_color} {catalyst_signal.recommended_action}  |  Entry: {catalyst_signal.entry_timing}  |  Hold: {catalyst_signal.hold_period_days} days")
        print(f"  {'─'*76}")
        
        # Individual catalysts
        for i, cat in enumerate(actionable[:5], 1):
            type_icons = {
                'product_launch': '🚀',
                'contract': '📝',
                'mna': '🤝',
                'regulatory': '✅',
                'earnings': '💰',
                'partnership': '🔗',
                'management': '👔'
            }
            icon = type_icons.get(cat.type.value, '📰')
            sentiment_icon = "🟢" if cat.sentiment > 0 else "🔴"
            
            print(f"\n  {icon} [{cat.type.value.upper()}] {sentiment_icon} Confidence: {cat.confidence:.0%}")
            print(f"     {cat.headline[:70]}...")
            if cat.keywords_matched:
                print(f"     Keywords: {', '.join(cat.keywords_matched[:5])}")
        
        # Trading guidance box
        print(f"\n  {'─'*76}")
        print(f"  ┌{'─'*72}┐")
        
        if catalyst_signal.recommended_action == "BUY":
            if catalyst_signal.entry_timing == "IMMEDIATE":
                print(f"  │  💡 TRADING IMPLICATION: Enter NOW - catalyst-driven momentum active      │")
            elif catalyst_signal.entry_timing == "PULLBACK":
                print(f"  │  💡 TRADING IMPLICATION: Wait for 2-3% pullback before entry              │")
            else:
                print(f"  │  💡 TRADING IMPLICATION: Monitor for volume confirmation before entry     │")
            
            print(f"  │  📊 Stop: {catalyst_signal.stop_loss_pct:.0%}  |  Target: +{catalyst_signal.target_pct:.0%}  |  Max Position: 5% of portfolio      │")
        elif catalyst_signal.recommended_action == "AVOID":
            print(f"  │  ⚠️ TRADING IMPLICATION: Negative catalyst detected - AVOID new positions   │")
            print(f"  │  📊 Consider reducing existing exposure or tightening stops                 │")
        else:
            print(f"  │  💡 TRADING IMPLICATION: HOLD - catalyst present but not actionable         │")
            print(f"  │  📊 Wait for volume confirmation or additional catalyst triggers            │")
        
        print(f"  └{'─'*72}┘")
        print(f"  {'─'*76}")
        
    except Exception as e:
        # Silently fail - catalyst display is enhancement, not critical
        pass

def display_reconciled_recommendation(result: 'AnalysisResult', sym: str = "$") -> None:
    """Display Elite Portfolio Strategist recommendation - Hedge Fund Grade"""
    
    signal = result.signal
    confidence = result.confidence_score
    current = result.current_price
    
    # Calculate trade levels
    # [T3 FIX] +5/10/15% targets were invented here, then printed as an
    # "EXISTING HOLDER PROFIT PLAN".
    _lv = trade_levels(result)
    stop_loss = _lv['stop']
    entry_price = _lv['entry'] or current
    t1 = _lv['t1']
    t2 = _lv['t2']
    t3 = _lv['t3']
    
    # Calculate position risk (must be before rr calculation)
    # [T3 FIX] The `else 2.0` tail here invented an R:R whenever the computed
    # one was <= 0.1 — precisely the cases where there is no reward to report.
    risk_per_share = abs(entry_price - stop_loss)
    reward_per_share = max(0.0, t2 - entry_price)
    rr = _lv['rr']
    
    print(f"\n\n  ═══════════════════════════════════════════════════════════════════════════")
    print(f"                          ELITE PORTFOLIO STRATEGIST")
    print(f"  ═══════════════════════════════════════════════════════════════════════════")
    
    # -------------------------------------------------------------------------
    # STRATEGIC VERDICT
    # -------------------------------------------------------------------------
    if signal in [Signal.STRONG_BUY, Signal.BUY]:
        print(f"\n  ┌────────────────────────────────────────────────────────────────────────┐")
        print(f"  │  VERDICT:  🟢 INITIATE LONG POSITION                                   │")
        print(f"  │  CONVICTION: {'HIGH' if confidence >= 70 else ('MEDIUM' if confidence >= 55 else 'LOW'):<8}  ({confidence:.0f}% confidence)                              │")
        print(f"  └────────────────────────────────────────────────────────────────────────┘")
        
        # Trade Plan for BUY
        print(f"\n  TRADE PLAN")
        print(f"  {'─'*72}")
        print(f"  Entry Zone:     {sym}{entry_price * 0.99:,.2f} - {sym}{entry_price * 1.01:,.2f}")
        print(f"  Stop-Loss:      {sym}{stop_loss:,.2f}  ({((stop_loss - entry_price) / entry_price) * 100:+.1f}%)")
        print(f"  {'─'*72}")
        print(f"  Target 1 (T1):  {sym}{t1:,.2f}  ({((t1 - entry_price) / entry_price) * 100:+.1f}%)  │  Take 33% profit")
        print(f"  Target 2 (T2):  {sym}{t2:,.2f}  ({((t2 - entry_price) / entry_price) * 100:+.1f}%)  │  Take 33% profit")
        print(f"  Target 3 (T3):  {sym}{t3:,.2f}  ({((t3 - entry_price) / entry_price) * 100:+.1f}%)  │  Final exit")
        print(f"  {'─'*72}")
        print(f"  Risk/Reward:    {rr:.2f}:1       Confidence tier: {_lv['confidence_tier']}")
        
    elif signal == Signal.ACCUMULATE:
        print(f"\n  ┌────────────────────────────────────────────────────────────────────────┐")
        print(f"  │  VERDICT:  🟢 ACCUMULATE ON WEAKNESS                                   │")
        print(f"  │  CONVICTION: {'HIGH' if confidence >= 70 else ('MEDIUM' if confidence >= 55 else 'LOW'):<8}  ({confidence:.0f}% confidence)                              │")
        print(f"  └────────────────────────────────────────────────────────────────────────┘")
        
        print(f"\n  ACCUMULATION STRATEGY")
        print(f"  {'─'*72}")
        print(f"  Ideal Entry:    {sym}{current * 0.97:,.2f} - {sym}{current * 0.99:,.2f}  (on 2-3% pullback)")
        print(f"  Aggressive Buy: {sym}{current:,.2f}                (current level)")
        print(f"  Stop-Loss:      {sym}{stop_loss:,.2f}  ({((stop_loss - entry_price) / entry_price) * 100:+.1f}%)")
        print(f"  {'─'*72}")
        print(f"  Target 1:       {sym}{t1:,.2f}  ({((t1 - current) / current) * 100:+.1f}%)")
        print(f"  Target 2:       {sym}{t2:,.2f}  ({((t2 - current) / current) * 100:+.1f}%)")
        
    elif signal == Signal.HOLD:
        # [T4 FIX] WATCHLIST and HOLD_EXISTING both arrive here as Signal.HOLD.
        # This used to print "MAINTAIN POSITION" and "Hold existing position"
        # to a reader who may own nothing at all.
        _fr = audience_framing(result)
        print()
        _box([
            f"VERDICT:  {_fr['headline']}",
            f"CONVICTION: {'HIGH' if confidence >= 70 else ('MEDIUM' if confidence >= 55 else 'LOW')}"
            f"  ({confidence:.0f}% confidence)",
            _fr['one_liner'],
        ], width=72)

        print(f"\n  {'POSITION MANAGEMENT' if _fr['action'] == 'HOLD_EXISTING' else 'IF YOU ALREADY OWN IT'}")
        print(f"  {'─'*72}")
        print(f"  New buyer:       {_fr['new_buyer']}")
        print(f"  Existing holder: {_fr['holder']}")
        if _lv['available']:
            # [T7 FIX] Was a hardcoded 7% here and 9% in the holder plan below,
            # neither derived from anything the engine computed.
            print(f"  Protective stop: {sym}{stop_loss:,.2f} "
                  f"({((stop_loss - current) / current) * 100:+.1f}% from spot)")
            print(f"  Trailing stop:   {_lv['trailing_pct']:.1f}% "
                  f"({TradeSetupGenerator.TRAILING_ATR_MULT:g}xATR) once the position is green")
        else:
            print(f"  Levels:          not computed — {_lv['basis']}")
        
    elif signal == Signal.REDUCE:
        print(f"\n  ┌────────────────────────────────────────────────────────────────────────┐")
        print(f"  │  VERDICT:  🟡 REDUCE EXPOSURE                                          │")
        print(f"  │  ACTION:   Trim 25-50% of position                                     │")
        print(f"  └────────────────────────────────────────────────────────────────────────┘")
        
        print(f"\n  EXIT STRATEGY")
        print(f"  {'─'*72}")
        print(f"  Immediate:      Sell 25% at market")
        print(f"  On Rally:       Sell additional 25% at {sym}{current * 1.02:,.2f}")
        print(f"  Stop-Loss:      {sym}{current * 0.92:,.2f}  (for remaining position)")
        
    else:  # SELL, STRONG_SELL
        print(f"\n  ┌────────────────────────────────────────────────────────────────────────┐")
        print(f"  │  VERDICT:  🔴 EXIT POSITION                                            │")
        print(f"  │  ACTION:   Full position liquidation recommended                       │")
        print(f"  └────────────────────────────────────────────────────────────────────────┘")
        
        print(f"\n  EXIT EXECUTION")
        print(f"  {'─'*72}")
        print(f"  Execute:        Sell entire position at market")
        print(f"  Target:         {sym}{current:,.2f} (do not wait for higher)")
        print(f"  Reasoning:      Risk/reward no longer favorable")
    
    # -------------------------------------------------------------------------
    # RISK MANAGEMENT
    # -------------------------------------------------------------------------
    risk_flags = []
    if result.risk and result.risk.overall_risk_score >= 8:
        risk_flags.append(f"High overall risk score ({result.risk.overall_risk_score}/10)")
    if result.fundamental and result.fundamental.debt_to_equity > 3:
        risk_flags.append(f"Elevated leverage ({result.fundamental.debt_to_equity:.1f}x D/E)")
    if result.insider and result.insider.insider_sentiment == "BEARISH":
        risk_flags.append("Material insider selling detected")
    if result.valuation and "OVERVALUED" in result.valuation.valuation_label:
        risk_flags.append("Premium valuation relative to intrinsic value")
    if result.risk and result.risk.annualized_volatility > 0.4:
        risk_flags.append(f"High volatility ({result.risk.annualized_volatility:.0%} annualized)")
    
    if risk_flags:
        print(f"\n  RISK FACTORS")
        print(f"  {'─'*72}")
        for i, flag in enumerate(risk_flags[:4], 1):
            print(f"  {i}. {flag}")
    
    # -------------------------------------------------------------------------
    # INVESTOR PROFILE GUIDANCE
    # -------------------------------------------------------------------------
    print(f"\n  INVESTOR GUIDANCE")
    print(f"  {'─'*72}")
    
    # Conservative investors
    if signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
        if confidence >= 65 and len(risk_flags) <= 1:
            print(f"  Conservative:   ✅ Suitable - Strong conviction with manageable risk")
        else:
            print(f"  Conservative:   ⚠️ Wait for pullback or improved risk profile")
    else:
        print(f"  Conservative:   ❌ Avoid new positions at current levels")
    
    # Growth investors  
    if signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
        print(f"  Growth:         ✅ Initiate or add to position")
    elif signal == Signal.HOLD:
        print(f"  Growth:         ⏸️ Hold existing, avoid new entries")
    else:
        print(f"  Growth:         ❌ Reduce or exit position")
    
    # Aggressive investors - WITH RISK AWARENESS
    risk_grade = result.risk.risk_grade if result.risk else "C"
    volatility = result.risk.annualized_volatility if result.risk else 0.3
    
    if signal in [Signal.STRONG_BUY, Signal.BUY]:
        if risk_grade in ['A', 'B'] and volatility < 0.35:
            print(f"  Aggressive:     ✅ Full position with leverage acceptable")
        elif risk_grade == 'C':
            print(f"  Aggressive:     ✅ Full position, NO leverage (moderate risk)")
        else:
            print(f"  Aggressive:     ⚠️ Reduced position only, NO leverage ({risk_grade} risk, {volatility*100:.0f}% vol)")
    elif signal == Signal.ACCUMULATE:
        print(f"  Aggressive:     ✅ Accumulate aggressively on dips (no leverage)")
    else:
        print(f"  Aggressive:     ⚠️ Reallocate capital to stronger opportunities")
    
    # -------------------------------------------------------------------------
    # TRIGGER CONDITIONS
    # -------------------------------------------------------------------------
    print(f"\n  CONVICTION TRIGGERS")
    print(f"  {'─'*72}")
    
    print(f"  📈 UPGRADE to BUY if:")
    if result.technical and result.technical.resistance_level:
        print(f"     • Price closes above {sym}{result.technical.resistance_level:,.2f} on 1.5x volume")
    print(f"     • Composite score exceeds 65 with improving momentum")
    if result.catalysts and result.catalysts.earnings_date != "Unknown":
        print(f"     • Earnings beat on {result.catalysts.earnings_date}")
    
    print(f"\n  📉 DOWNGRADE to SELL if:")
    if result.technical and result.technical.support_level:
        print(f"     • Price breaks below {sym}{result.technical.support_level:,.2f} on volume")
    print(f"     • Stop-loss at {sym}{stop_loss:,.2f} is triggered")
    print(f"     • Fundamental deterioration (earnings miss, guidance cut)")


# =============================================================================
# DISPLAY FUNCTIONS
# =============================================================================

# Rich imports (consolidated - used by all display functions below)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.layout import Layout
from rich.align import Align
from rich.console import Group
from rich import box
import textwrap


# =============================================================================
# CONCISE BEGINNER GUIDANCE / CONSISTENCY HELPERS
# =============================================================================

def _conviction_label(confidence: float) -> str:
    """Return conviction prefix based on confidence score."""
    if confidence < 50:
        return "LOW-CONVICTION"
    if confidence < 65:
        return "MODERATE-CONVICTION"
    return ""


def _format_rr(rr_ratio: float, target: float, stop: float, current_price: float) -> str:
    """Concise R/R rendering. Never returns '0.0:1'.

    - 'N/A'                when target or stop is missing.
    - 'Unfavorable'        when target <= current price (no upside).
    - 'Unfavorable, X.XX:1' when computed R/R is positive but below RR.RESEARCH_MIN.
    - 'X.XX:1'              when R/R is acceptable.
    """
    if target <= 0 or stop <= 0:
        return "N/A"
    if target <= current_price:
        return "Unfavorable"
    if rr_ratio <= 0:
        return "N/A"
    if rr_ratio < _T.RR.RESEARCH_MIN:
        return f"Unfavorable, {_T.fmt_rr(rr_ratio)}"
    return _T.fmt_rr(rr_ratio)


def _contextual_recommendation_label(signal_display: str, conv: str,
                                     risk_grade: str, buy_now: str) -> str:
    """Context-aware headline action label.

    A plain 'HOLD' on a Risk-Grade-F name with Buy Now = NO reads like an
    endorsement to new buyers. Split the headline into existing-holder vs
    new-buyer context so it never implies a fresh buy. Explicit sells are
    left untouched.
    """
    base = f"{conv} {signal_display}".strip() if conv else (signal_display or "")
    sig_u = (signal_display or "").upper()
    bn = (buy_now or "").upper()
    rg = (risk_grade or "").upper()
    if "SELL" in sig_u or "REDUCE" in sig_u:
        return base
    if bn == "NO" and rg == "F":
        return "HOLD EXISTING / AVOID NEW BUY"
    if bn == "NO" and rg == "D":
        return "HOLD EXISTING / WAIT FOR ENTRY"
    return base


def _compute_pullback_entry(t: Any, current_price: float) -> Tuple[float, float, bool, str]:
    """Return (entry, stop, is_deep, basis) for a realistic pullback level.

    Picks the *nearest* technical support below the current price among the
    20-day MA, 50-day MA, VWAP and the detected swing-support level. If the
    only support available sits more than 25% below price, the level is flagged
    as a deep pullback (``is_deep=True``) so callers do not present it as a
    normal pullback entry.
    """
    if not t or not current_price or current_price <= 0:
        return 0.0, 0.0, False, ""
    candidates = []  # (level, label)
    for lvl, label in (
        (getattr(t, 'sma_20', 0.0), "20-day MA"),
        (getattr(t, 'sma_50', 0.0), "50-day MA"),
        (getattr(t, 'vwap', 0.0), f"VWAP ({Config.VWAP_WINDOW}d)"),
        (getattr(t, 'support_level', 0.0), "support zone"),
    ):
        try:
            lvl = float(lvl)
        except (TypeError, ValueError):
            continue
        if 0 < lvl < current_price:
            candidates.append((lvl, label))
    if not candidates:
        return 0.0, 0.0, False, ""
    # Nearest support below price = the highest qualifying level.
    entry, basis = max(candidates, key=lambda x: x[0])
    drop_pct = (current_price - entry) / current_price
    is_deep = drop_pct > 0.25
    atr = getattr(t, 'atr', 0.0) or 0.0
    atr_pct = (atr / current_price) if (atr and current_price) else 0.02
    atr_pct = max(0.015, min(0.05, atr_pct))
    stop = entry * (1 - atr_pct)
    if stop >= entry:
        stop = entry * 0.985
    return entry, stop, is_deep, basis


def _compute_tactical_stop(t: Any, current_price: float,
                           long_term_stop: float) -> Tuple[float, str]:
    """Return (tactical_stop, basis) — a tighter short-term stop for extended,
    high-volatility names.

    Anchors below the nearest of the 20-day MA or detected swing low, falling
    back to a ~1.0x ATR stop. The tactical stop must sit *above* the wider
    long-term holder stop (tighter), otherwise it adds no value and ``0.0`` is
    returned.
    """
    if not t or not current_price or current_price <= 0:
        return 0.0, ""
    atr = getattr(t, 'atr', 0.0) or 0.0
    atr_pct = (atr / current_price) if (atr and current_price) else 0.025
    atr_pct = max(0.02, min(0.06, atr_pct))
    atr_stop = current_price * (1 - atr_pct)
    candidates = []  # (level, label)
    sma20 = getattr(t, 'sma_20', 0.0) or 0.0
    support = getattr(t, 'support_level', 0.0) or 0.0
    if 0 < sma20 < current_price:
        candidates.append((float(sma20) * (1 - 0.005), "below 20-day MA"))
    if 0 < support < current_price:
        candidates.append((float(support) * (1 - 0.005), "below recent swing low"))
    candidates.append((atr_stop, "~1x ATR"))
    # Tactical = tightest meaningful stop that is still above the long-term stop.
    valid = [(lvl, lab) for lvl, lab in candidates
             if lvl < current_price and (long_term_stop <= 0 or lvl > long_term_stop)]
    if not valid:
        return 0.0, ""
    tactical, basis = max(valid, key=lambda x: x[0])
    return tactical, basis


_DISPLAY_VADER = None


def _get_display_vader():
    """Lazily build a shared VADER analyzer for display-time news scoring."""
    global _DISPLAY_VADER
    if _DISPLAY_VADER is None:
        try:
            _DISPLAY_VADER = SentimentIntensityAnalyzer()
        except Exception:
            _DISPLAY_VADER = False
    return _DISPLAY_VADER or None


def _company_name_token(company_name: str) -> str:
    """Extract the salient company token (e.g. 'dell' from 'Dell Technologies Inc')."""
    cname = (company_name or "").strip().lower()
    if not cname:
        return ""
    stop = {'the', 'inc', 'inc.', 'corp', 'corp.', 'co', 'co.', 'ltd', 'plc',
            'company', 'technologies', 'technology', 'group', 'holdings',
            'international', 'systems', 'corporation'}
    for w in re.split(r'[\s,]+', cname):
        if w and w not in stop and len(w) > 2:
            return w
    return ""


def _classify_news_relevance(articles: List[Dict[str, Any]], ticker: str,
                             company_name: str = "",
                             peer_tickers: Optional[List[str]] = None,
                             peer_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Split headlines into direct / peer / macro buckets and compute a
    direct-weighted sentiment score.

    - Direct = the company name token or ticker appears in the headline.
    - Peer   = a peer ticker / peer name appears (sector competitor).
    - Macro  = neither — broad market / thematic news.

    Direct news dominates the weighted score (0.8); peer news contributes
    lightly (0.2). When there is no direct news at all, the peer/macro signal is
    heavily dampened so it does not masquerade as company sentiment.
    """
    peer_tickers = [str(p).upper() for p in (peer_tickers or []) if p]
    peer_names = [str(p).lower() for p in (peer_names or []) if p]
    tkr = (ticker or "").upper()
    cname_l = (company_name or "").strip().lower()
    cname_token = _company_name_token(company_name)
    vader = _get_display_vader()

    direct, peer, macro = [], [], []
    d_scores, p_scores = [], []

    for a in (articles or []):
        title = (a.get('title') or a.get('headline') or "").strip()
        if not title:
            continue
        summary = a.get('summary') or ""
        text = f"{title} {summary}"
        # Bucket strictly by the HEADLINE so a passing mention of the company
        # inside another story's body does not get counted as direct news.
        title_l = title.lower()

        is_direct = bool(
            (tkr and re.search(rf'\b{re.escape(tkr)}\b', title))
            or (cname_token and re.search(rf'\b{re.escape(cname_token)}\b', title_l))
            or (cname_l and len(cname_l) > 3 and cname_l in title_l)
        )
        is_peer = False
        if not is_direct:
            for pt in peer_tickers:
                if re.search(rf'\b{re.escape(pt)}\b', title):
                    is_peer = True
                    break
            if not is_peer:
                for pn in peer_names:
                    if pn and len(pn) > 3 and pn in title_l:
                        is_peer = True
                        break

        comp = None
        if vader:
            try:
                comp = vader.polarity_scores(text)['compound']
            except Exception:
                comp = None

        if is_direct:
            direct.append(title)
            if comp is not None:
                d_scores.append(comp)
        elif is_peer:
            peer.append(title)
            if comp is not None:
                p_scores.append(comp)
        else:
            macro.append(title)

    direct_score = float(np.mean(d_scores)) if d_scores else 0.0
    peer_score = float(np.mean(p_scores)) if p_scores else 0.0
    if d_scores:
        weighted = (0.8 * direct_score + 0.2 * peer_score) if p_scores else direct_score
    elif p_scores:
        weighted = 0.3 * peer_score  # No direct news -> low-confidence, dampened.
    else:
        weighted = 0.0

    def _lab(x: float) -> str:
        return "POSITIVE" if x > 0.15 else "NEGATIVE" if x < -0.15 else "NEUTRAL"

    return {
        'direct_headlines': direct[:5],
        'peer_headlines': peer[:5],
        'macro_headlines': macro[:5],
        'direct_count': len(direct),
        'peer_count': len(peer),
        'macro_count': len(macro),
        'direct_sentiment_score': round(direct_score, 3),
        'direct_sentiment_label': _lab(direct_score),
        'peer_sentiment_score': round(peer_score, 3),
        'weighted_sentiment_score': round(weighted, 3),
        'weighted_sentiment_label': _lab(weighted),
        'has_direct_news': bool(direct),
    }


# ── [F03] One decision drives every actionable row ─────────────────────────
# Ranked from least to most permissive. `buy_now` may never exceed the ceiling
# the resolved decision supports.
_BUY_NOW_RANK = {"NO": 0, "ONLY AFTER CONFIRMATION": 1, "YES": 2}

# FinalAction -> the signal vocabulary the guidance rows below branch on, so a
# resolved decision (not the raw SignalGenerator output) drives the audience
# text. WATCHLIST folds into the HOLD/wait tier; AVOID and INVALID fold into
# the sell/avoid tier, which is the conservative direction in both cases.
_FINAL_ACTION_TO_GUIDANCE_SIGNAL = {
    "STRONG_BUY": "STRONG_BUY",
    "BUY": "BUY",
    "ACCUMULATE": "ACCUMULATE",
    "HOLD_EXISTING": "HOLD",
    "WATCHLIST": "HOLD",
    "AVOID": "SELL",
    "INVALID": "SELL",
}


def _guidance_signal(result: 'AnalysisResult') -> str:
    """The single action every guidance row must be derived from.

    Prefers the resolved `final_action` — the decision core's single source of
    truth — and falls back to the raw signal only while it is still unset.
    """
    action = str(getattr(result, 'final_action', '') or '').upper()
    if action in _FINAL_ACTION_TO_GUIDANCE_SIGNAL:
        return _FINAL_ACTION_TO_GUIDANCE_SIGNAL[action]
    return result.signal.value if hasattr(result.signal, 'value') else str(result.signal)


def _buy_now_ceiling(guidance_signal: str) -> str:
    """Most permissive `buy_now` the resolved decision can support."""
    if guidance_signal in ("STRONG_BUY", "BUY", "ACCUMULATE"):
        return "YES"
    if guidance_signal in ("SELL", "STRONG_SELL", "REDUCE"):
        return "NO"
    return "ONLY AFTER CONFIRMATION"


def _target_agreement_pct(targets: List[float]) -> Optional[float]:
    """How closely the contributing price targets agree, 0-100.

    Dispersion, not source count. Uses the coefficient of variation so the
    answer is scale-free (three targets around $10 and three around $1,000 with
    the same relative spread agree equally well). Returns None for fewer than
    two targets, because a single estimate has nothing to agree with — that is
    a different statement from "low agreement" and must not be rendered as 0.
    """
    clean = [float(t) for t in (targets or []) if t and float(t) > 0]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    if mean <= 0:
        return None
    variance = sum((t - mean) ** 2 for t in clean) / len(clean)
    cv = (variance ** 0.5) / mean
    # cv 0 -> 100 (identical); cv >= 0.5 -> 0 (wildly dispersed).
    return round(max(0.0, min(100.0, (1.0 - cv / 0.5) * 100.0)), 1)


def _apply_target_quality(valuation: 'ValuationMetrics', targets: List[float],
                          source_names: Optional[List[str]] = None) -> None:
    """Record how many target sources there were and how much they agreed.

    Replaces the old source-count confidence, which awarded a fixed 25 points
    per contributing source regardless of whether those sources were
    independent or whether they pointed anywhere near each other.
    """
    clean = [float(t) for t in (targets or []) if t and float(t) > 0]
    valuation.target_source_count = len(clean)
    valuation.target_sources = list(source_names or [])
    agreement = _target_agreement_pct(clean)
    valuation.target_agreement_pct = agreement
    # Legacy mirror for readers that still expect a 0-100 number. It is the
    # agreement measure now, never a source count.
    valuation.target_confidence = float(agreement) if agreement is not None else 0.0


def monte_carlo_event_label(mc: 'MonteCarloResults') -> str:
    """State exactly which event the Monte Carlo probability refers to.

    `probability_profit` is P(simulated price is above the current price at the
    END of the horizon). It is not the probability of touching a target, and it
    is not the probability of reaching a target before a stop — which is how a
    bare "Prob Profit 62%" is read on a trade plan.
    """
    days = int(getattr(mc, 'days_simulated', 0) or 0)
    horizon = f"{days}-day" if days else "simulated"
    return (
        f"P(price above today's close at the end of the {horizon} horizon), "
        f"model-conditional. Terminal outcome only — not the chance of "
        f"touching a target, and not target-vs-stop."
    )


def _compute_beginner_guidance(result: 'AnalysisResult') -> Dict[str, Any]:
    """Derive concise beginner-friendly guidance from the full result.

    Returns a dict with: timing, buy_now, entry_quality, beginner_action,
    rr_label, new_buyer, existing_holder, aggressive, short_tf, mid_tf, long_tf,
    warnings (list of strings), data_quality.

    [F03] Every actionable field is derived from ONE resolved decision. The
    candlestick block below may still describe what the tape is doing, but its
    verdict is clamped to `_buy_now_ceiling` before it reaches any position or
    timeframe row — a SELL/AVOID result can no longer emit "Buy Now: YES".
    """
    t = result.technical
    ts = result.trade_setup
    risk_grade = result.risk.risk_grade if result.risk else "C"
    signal = _guidance_signal(result)
    confidence = float(result.confidence_score or 50.0)

    patterns = list(getattr(t, 'candlestick_patterns', []) or [])
    cs_bias = getattr(t, 'candlestick_bias', 'NEUTRAL')
    prior_trend = getattr(t, 'prior_trend', 'SIDEWAYS')
    top_pattern = patterns[0] if patterns else None
    top_name = (top_pattern['name'] if top_pattern else '').lower()

    macd_bull = (t.macd_signal == "BULLISH")
    macd_bear = (t.macd_signal == "BEARISH")
    above_50 = t.price_vs_sma50 > 0
    volume_strong = t.volume_ratio >= 1.5
    volume_ok = t.volume_ratio >= 1.0

    # ---- Candlestick Timing & Buy Now ----
    timing = "NO CLEAR EDGE"
    buy_now = "NO"
    entry_quality = "Weak"
    action = "Wait for a clean setup before committing capital."

    if top_pattern:
        bias = top_pattern['bias']
        rel = top_pattern['reliability']
        ptype = top_pattern['type']
        name = top_pattern['name']

        if 'doji' in top_name and prior_trend == "UP":
            timing = "WAIT / NO CLEAR EDGE"
            buy_now = "NO"
            entry_quality = "Poor"
            action = ("Doji shows indecision after an uptrend. Do not buy now. "
                      "Wait for a close above resistance with strong volume or "
                      "a pullback to support.")
        elif bias == "BEARISH" and prior_trend == "UP":
            timing = "WAIT"
            buy_now = "NO"
            entry_quality = "Poor"
            action = (f"Do not buy now. {name} after an uptrend warns of a top. "
                      "Wait for breakout confirmation or pullback to support.")
        elif bias == "BEARISH" and ptype == "CONTINUATION":
            timing = "AVOID"
            buy_now = "NO"
            entry_quality = "Poor"
            action = "Avoid entry. Selling pressure is persisting; wait for a bullish reversal candle."
        elif bias == "BULLISH" and rel == "HIGH" and macd_bull and above_50 and volume_ok:
            timing = "GOOD BUY SETUP"
            buy_now = "YES"
            entry_quality = "Strong"
            action = (f"{name} with confluence (MACD bullish, above 50-SMA). "
                      "Consider a starter entry; place stop below today's low.")
        elif bias == "BULLISH" and rel in ("HIGH", "MEDIUM"):
            timing = "WAIT"
            buy_now = "ONLY AFTER CONFIRMATION"
            entry_quality = "Moderate"
            action = (f"{name} is encouraging. Wait for a close above today's high on rising volume "
                      "before adding.")
        else:
            timing = "NO CLEAR EDGE"
            buy_now = "NO"
            entry_quality = "Weak"
            action = "Pattern detected but low conviction. Defer to trend and risk metrics."
    else:
        if prior_trend == "UP" and macd_bull and above_50:
            timing = "HOLD WITH STOP"
            buy_now = "ONLY AFTER CONFIRMATION"
            entry_quality = "Moderate"
            action = ("Trend is intact. Hold existing longs with a trailing stop; "
                      "wait for a pullback or bullish reversal candle before adding.")
        elif prior_trend == "DOWN" and macd_bear and not above_50:
            timing = "AVOID"
            buy_now = "NO"
            entry_quality = "Poor"
            action = "Downtrend intact. Do not catch the knife; wait for a bullish reversal candle."
        elif prior_trend == "SIDEWAYS":
            timing = "WAIT / NO CLEAR EDGE"
            buy_now = "NO"
            entry_quality = "Weak"
            action = "Price is consolidating. Wait for a decisive breakout or breakdown candle."
        else:
            timing = "NO CLEAR EDGE"
            buy_now = "NO"
            entry_quality = "Weak"
            action = "Signals are mixed. Wait for a clean reversal/continuation pattern."

    # ---- R/R label (to the horizon target the ratio is measured to) ----
    target = (ts.horizon_target or 0.0) if ts else 0.0
    stop = ts.stop_loss if ts else 0.0
    rr_ratio = ts.risk_reward_ratio if ts else 0.0
    rr_label = _format_rr(rr_ratio, target, stop, result.current_price)

    # A risk/reward below RR.RESEARCH_MIN (or target at/below price) is never a clean fresh
    # entry — a "Buy Now: YES" must be downgraded to confirmation-only so the
    # headline never invites a new buyer into an unfavorable setup.
    rr_unfavorable = rr_label == "Unfavorable" or (0 < rr_ratio < _T.RR.RESEARCH_MIN)
    if rr_unfavorable and buy_now == "YES":
        buy_now = "ONLY AFTER CONFIRMATION"

    # ---- [F03] Clamp the candlestick verdict to the resolved decision ----
    # The block above reads only the tape, so a bullish reversal with MACD and
    # 50-SMA confluence used to set "GOOD BUY SETUP" / "Buy Now: YES" even on a
    # SELL result. Timing may describe conditions; it may not promote an action
    # past what the decision core resolved. This runs BEFORE the position and
    # timeframe rows so they inherit the clamped values rather than
    # contradicting them.
    ceiling = _buy_now_ceiling(signal)
    if _BUY_NOW_RANK.get(buy_now, 0) > _BUY_NOW_RANK[ceiling]:
        buy_now = ceiling
        entry_quality = "Poor" if ceiling == "NO" else "Moderate"
        if ceiling == "NO":
            timing = "AVOID"
            action = (
                "The overall verdict is negative, so this is not a buy — "
                "regardless of the bullish candle. Existing holders should "
                "follow the exit plan; new buyers should stay out."
            )
        else:
            timing = "WAIT"
            action = (
                "The candle is encouraging but the overall verdict does not "
                "support a fresh entry yet. Wait for confirmation of the "
                "decision, not just the pattern."
            )

    # A non-allocating resolved decision is stricter than the signal
    # ceiling above: WATCHLIST folds into the HOLD tier there, but it still
    # funds nothing, so a bullish candle cannot make it "Buy now".
    final_action = str(getattr(result, 'final_action', '') or '').upper()
    if final_action in {'WATCHLIST', 'HOLD_EXISTING', 'REDUCE', 'AVOID', 'INVALID'}:
        buy_now = "NO"
        action = "Do not open a new position; review the resolved decision and its risks."

    # ---- Position guidance ----
    if timing in ("AVOID", "WAIT", "WAIT / NO CLEAR EDGE", "NO CLEAR EDGE"):
        if risk_grade == "F":
            new_buyer = "Avoid new buy — watchlist only"
            existing_holder = "Hold existing with strict stop"
        elif risk_grade == "D":
            new_buyer = "Wait"
            existing_holder = "Hold with strict stop-loss"
        else:
            new_buyer = "Wait"
            existing_holder = "Hold"
        aggressive = "Add only above confirmation level on strong volume"
    elif timing == "GOOD BUY SETUP" and risk_grade not in ("D", "F"):
        new_buyer = "Starter position"
        existing_holder = "Hold or add on confirmation"
        aggressive = "Add on breakout with > 1.5x avg volume"
    elif timing == "HOLD WITH STOP":
        new_buyer = "Wait for pullback"
        existing_holder = "Hold with trailing stop"
        aggressive = "Add only on pullback to support"
    else:
        new_buyer = "Wait"
        existing_holder = "Hold" if signal not in ("SELL", "STRONG_SELL", "REDUCE") else "Trim"
        aggressive = "Add only above confirmation"

    # Override for explicit sell signals
    if signal in ("SELL", "STRONG_SELL"):
        new_buyer = "Avoid"
        existing_holder = "Trim or exit"
        aggressive = "Avoid"
    if final_action in {'WATCHLIST', 'HOLD_EXISTING', 'REDUCE', 'AVOID', 'INVALID'}:
        new_buyer = ("Avoid new buy" if final_action in {'REDUCE', 'AVOID', 'INVALID'}
                     else "Wait; no new position")
        aggressive = "Do not add until the final decision changes"
    # The holder line follows the Decision's published holder action, so the
    # guidance cannot say "Hold" where the Decision says trim or exit.
    try:
        from engine.blockers import holder_action as _holder_action
        _ha = _holder_action(FinalAction(final_action),
                             getattr(result.scores, 'composite_score', None) if result.scores else None,
                             getattr(result, 'final_action_codes', []) or [])
    except (ImportError, ValueError):
        _ha = ""
    if _ha == "TRIM":
        existing_holder = "Trim into strength; keep the stop"
    elif _ha == "EXIT":
        existing_holder = "Exit on strength"

    # ---- Timeframe table ----
    fund_strong = (piotroski_is_reportable(result.fundamental)
                   and result.fundamental.piotroski_f_score >= 7)
    low_conviction = confidence < 50
    risk_grade_bad = risk_grade in ("D", "F")
    # When the timing signal explicitly requires confirmation, no horizon may
    # promise unconditional Accumulate — surface a conditional/hold path instead.
    requires_confirmation = buy_now == "ONLY AFTER CONFIRMATION"
    if signal in ("STRONG_BUY", "BUY"):
        short_tf = "Buy only after confirmation" if entry_quality != "Strong" else "Buy"
        mid_tf = "Accumulate"
        long_tf = "Accumulate" if (fund_strong and not requires_confirmation) else (
            "Conditional Accumulate after confirmation" if requires_confirmation else "Hold")
    elif signal in ("ACCUMULATE", "HOLD"):
        short_tf = "Wait" if timing in ("WAIT", "AVOID", "NO CLEAR EDGE") else "Buy only after confirmation"
        mid_tf = "Hold"
        # Do not promise Accumulate when conviction is low or risk grade is poor
        if low_conviction and risk_grade_bad:
            long_tf = "Hold / Wait for better entry"
        elif risk_grade_bad:
            long_tf = "Conditional Accumulate only on better entry"
        elif requires_confirmation:
            long_tf = "Conditional Accumulate after confirmation"
        else:
            long_tf = "Accumulate" if fund_strong else "Hold"
    else:
        short_tf = "Avoid"
        mid_tf = "Avoid"
        long_tf = "Avoid"
    if final_action in {'WATCHLIST', 'HOLD_EXISTING', 'REDUCE', 'AVOID', 'INVALID'}:
        avoid = final_action in {'REDUCE', 'AVOID', 'INVALID'}
        short_tf = "Avoid" if avoid else "Wait"
        mid_tf = "Avoid" if avoid else "Hold existing / wait"
        long_tf = "Avoid" if avoid else "Review before adding"

    # ---- Warnings (data quality + conflicts) ----
    warnings: List[str] = []
    mi = result.market_intel
    cp = result.current_price
    analyst_target = getattr(mi, 'target_mean', 0.0) or 0.0

    # ETFs and funds do not carry analyst price targets, insider transactions,
    # or single-name fundamentals. Those checks must not count against an ETF's
    # data quality, or every ETF tear sheet falsely fails. Treat them as N/A.
    is_etf = bool(result.fundamental and getattr(result.fundamental, 'is_etf', False))

    # Concise data-quality checks. Each maps to a one-line warning when it
    # cannot be verified. Do not add more checks here — keep beginner-friendly.
    if not (cp and cp > 0):
        warnings.append("Price feed unverified.")
    if not is_etf:
        if analyst_target <= 0:
            warnings.append("Analyst target freshness not verified.")
        elif mi and mi.analyst_rating in ("BUY", "STRONG_BUY") and analyst_target < cp:
            warnings.append("Analyst BUY but target below current price (stale or limited upside).")

    mc = result.monte_carlo
    mc_bullish = bool(mc and mc.probability_profit > 0.55)

    if result.sentiment and result.sentiment.news_volume > 0 \
       and not getattr(result.sentiment, 'last_news_timestamp', ''):
        warnings.append("News date unavailable — sentiment freshness cannot be verified.")

    # Insider sale context check (item 2) — N/A for ETFs (no insiders).
    ins = result.insider
    if not is_etf and ins and ins.insider_sentiment in ("BEARISH", "BULLISH") and not ins.key_transactions:
        warnings.append("Insider sale type unavailable — net flow only.")

    fund = result.fundamental
    if fund and not getattr(fund, 'is_etf', False) and fund.pe_ratio <= 0 \
       and fund.roe == 0 and fund.revenue_growth_yoy == 0:
        warnings.append("Limited fundamentals available — treat composite cautiously.")

    # ---- Hard data-quality failures (always escalate to FAIL) ----
    hard_fail: List[str] = []
    # Stop-loss validity: a buy/accumulate setup without a valid stop is invalid.
    if signal in ("STRONG_BUY", "BUY", "ACCUMULATE") and not (stop and stop > 0):
        hard_fail.append("Stop-loss is missing or invalid (0 / null).")
    # [T16 FIX] Risk/reward genuinely uncomputable — as opposed to computed
    # and legitimately zero.
    #
    # This used to hard-fail whenever `rr_ratio` was 0 with an entry, target
    # and stop all present, and mark `data_quality = FAIL`. That was written
    # when a zero R:R could only mean the empty-TradeSetup bug. Now that
    # targets come from the valuation blend they can legitimately land at or
    # below the entry — meaning there is no long reward — and a zero R:R is a
    # real finding, not a computation failure. Reporting it as a DATA problem
    # misattributes a valuation verdict to the data layer.
    cp_for_rr = result.current_price or 0.0
    entry_for_rr = (ts.entry_price if ts else 0.0) or cp_for_rr
    _target_above_entry = target > entry_for_rr > 0
    if _target_above_entry and stop > 0 and not (rr_ratio and rr_ratio > 0):
        # A target only a fraction of a percent above entry is eaten by the
        # round-trip cost, so R:R legitimately nets to zero. That is a
        # valuation finding ("no meaningful reward"), not a data fault, and
        # must not be reported as DATA QUALITY FAIL.
        _gap_pct = (target - entry_for_rr) / entry_for_rr * 100.0
        if _gap_pct < 0.5:
            warnings.append(
                f"Valuation target {target:,.2f} sits only {_gap_pct:.2f}% above the entry "
                f"reference {entry_for_rr:,.2f} — reward is consumed by execution cost, so R:R is 0."
            )
        else:
            hard_fail.append("Risk/reward could not be computed despite valid entry, target, and stop.")
    elif entry_for_rr > 0 and target > 0 and target <= entry_for_rr:
        # Informational, not a data fault.
        warnings.append(
            f"Valuation target {target:,.2f} is at or below the entry reference "
            f"{entry_for_rr:,.2f} — no long reward, so R:R is 0 by definition."
        )
    # Analyst rating inferred from zero analysts — N/A for ETFs (no analyst coverage).
    # Yahoo may provide an aggregate recommendation and a valid coverage count
    # without the buy/hold/sell bucket breakdown, so bucket zeros alone are not
    # evidence of zero coverage.
    if not is_etf and mi and mi.analyst_rating in ("STRONG_BUY", "BUY", "SELL", "STRONG_SELL") \
       and int(getattr(mi, 'analyst_count', 0) or 0) <= 0:
        hard_fail.append(f"Analyst rating '{mi.analyst_rating}' reported with 0 analysts.")
    warnings.extend(hard_fail)

    # Data quality status: PASS only when every check is verified. A hard
    # failure (missing stop on a buy, uncomputable R:R, rating with no
    # coverage) forces FAIL. Soft warnings — optional data such as analyst
    # target freshness, news dating or insider sale context — can only WARN.
    # Previously three soft warnings escalated to FAIL, which is how every
    # fast-preview tear sheet read "DATA QUALITY FAIL — NOT CONFIRMED" on
    # nothing more than the optional modules being skipped.
    if hard_fail:
        data_quality = "FAIL"
    elif not warnings:
        data_quality = "PASS"
    else:
        data_quality = "WARN"

    # ---- Main reason (single short sentence for Executive Summary) ----
    fund_score = result.scores.fundamental_score if result.scores else 50
    if low_conviction and risk_grade_bad and (rr_ratio == 0 or rr_ratio < _T.RR.RESEARCH_MIN):
        main_reason = ("Strong fundamentals, but poor risk/reward and high volatility "
                       "make new entry unattractive.")
    elif signal in ("STRONG_BUY", "BUY") and entry_quality == "Strong":
        main_reason = "Bullish setup confirmed by momentum, trend, and fundamentals."
    elif signal in ("SELL", "STRONG_SELL"):
        main_reason = "Bearish technicals and weak setup outweigh other factors."
    elif risk_grade_bad:
        main_reason = f"Risk Grade {risk_grade} caps upside; wait for a cleaner entry."
    elif rr_ratio and rr_ratio < _T.RR.RESEARCH_MIN:
        main_reason = (f"Risk/reward of {_T.fmt_rr(rr_ratio)} is below the "
                       f"{_T.fmt_rr(_T.RR.RESEARCH_MIN)} minimum.")
    elif fund_score >= 70 and timing in ("WAIT", "WAIT / NO CLEAR EDGE", "AVOID"):
        main_reason = "Fundamentals are strong but timing is unclear; defer entry."
    else:
        main_reason = "Mixed signals; wait for a clearer setup."

    # ---- Risk-conflict warning (strong business, poor entry setup) ----
    risk_conflict_warning = ""
    strong_business = (fund_score >= 70) or bool(
        piotroski_is_reportable(fund) and getattr(fund, 'piotroski_f_score', 0) >= 7)
    if strong_business and risk_grade == "F":
        risk_conflict_warning = (
            "Strong business momentum, but poor entry setup. "
            "Do not chase after a post-earnings move.")

    # ---- Realistic pullback entry + tactical vs long-term holder stop ----
    pullback_entry, pullback_stop, pullback_is_deep, pullback_basis = \
        _compute_pullback_entry(t, cp)
    long_term_stop = stop if (stop and stop > 0) else 0.0
    tactical_stop, tactical_stop_basis = _compute_tactical_stop(t, cp, long_term_stop)

    return {
        "timing": timing,
        "buy_now": buy_now,
        "entry_quality": entry_quality,
        "beginner_action": action,
        "rr_label": rr_label,
        "new_buyer": new_buyer,
        "existing_holder": existing_holder,
        "aggressive": aggressive,
        "short_tf": short_tf,
        "mid_tf": mid_tf,
        "long_tf": long_tf,
        "warnings": warnings,
        "data_quality": data_quality,
        "mc_bullish": mc_bullish,
        "analyst_target": analyst_target,
        "main_reason": main_reason,
        "risk_conflict_warning": risk_conflict_warning,
        "pullback_entry": pullback_entry,
        "pullback_stop": pullback_stop,
        "pullback_is_deep": pullback_is_deep,
        "pullback_basis": pullback_basis,
        "long_term_stop": long_term_stop,
        "tactical_stop": tactical_stop,
        "tactical_stop_basis": tactical_stop_basis,
    }


def _sanitize_thesis_text(text, result: 'AnalysisResult'):
    """Remove contradictory phrases from generated thesis text.

    Accepts either a plain string OR an :class:`InvestmentThesis` instance.
    For a thesis object, sanitises every user-facing string field
    (``summary``, ``recommendation_rationale``, ``competitive_position`` and
    every entry in ``bull_case``/``bear_case``/``catalysts``/``risks``) in
    place and returns the same object. Returning the original container
    preserves ``AnalysisResult.thesis: Optional[InvestmentThesis]`` typing.

    Examples of phrases dropped:
      - 'strong momentum' if MACD is bearish.
      - 'good entry' if candlestick timing says WAIT/AVOID.
      - 'analyst support is bullish' if target < current price.
    """
    if not text:
        return text

    t = result.technical
    mi = result.market_intel
    cp = result.current_price
    analyst_target = getattr(mi, 'target_mean', 0.0) or 0.0
    macd_bear = (t.macd_signal == "BEARISH")
    weak_volume = t.volume_ratio < 1.0
    cs_bearish = getattr(t, 'candlestick_bias', 'NEUTRAL') == "BEARISH"

    bad_phrases: List[str] = []
    if macd_bear and weak_volume:
        bad_phrases += ["strong momentum", "strong uptrend with momentum confirmation"]
    if cs_bearish:
        bad_phrases += ["good entry", "attractive entry"]
    if analyst_target > 0 and analyst_target <= cp:
        bad_phrases += ["analyst support is bullish", "bullish analyst support"]

    def _scrub(s: str) -> str:
        if not isinstance(s, str) or not s:
            return s
        out = s
        for ph in bad_phrases:
            out = out.replace(ph, "").replace("  ", " ")
        return out

    # InvestmentThesis object: scrub each text field, return same object
    if isinstance(text, InvestmentThesis):
        text.summary = _scrub(text.summary)
        text.recommendation_rationale = _scrub(text.recommendation_rationale)
        text.competitive_position = _scrub(text.competitive_position)
        text.bull_case = [_scrub(x) for x in (text.bull_case or [])]
        text.bear_case = [_scrub(x) for x in (text.bear_case or [])]
        text.catalysts = [_scrub(x) for x in (text.catalysts or [])]
        text.risks = [_scrub(x) for x in (text.risks or [])]
        return text

    # Plain-string back-compat path
    return _scrub(text)


def display_analysis(result: 'AnalysisResult', detailed: bool = True):
    """
    Display analysis results with restructured professional layout using rich
    """
    console = Console()
    sym = result.currency_symbol

    # [DECISION-CORE] hard guard — invalid tickers produce no fake report
    if getattr(result, 'is_invalid', False) or getattr(result, 'final_action', '') == 'INVALID':
        reasons = getattr(result, 'invalid_reasons', None) or ["No valid data"]
        body = Text()
        body.append("INVALID TICKER / NO DATA\n\n", style="bold red")
        body.append(f"Ticker: {result.ticker or 'UNKNOWN'}\n", style="bold")
        body.append("Reasons:\n")
        for r in reasons:
            body.append(f"  • {r}\n")
        body.append("\nNo signal, stop loss, target, confidence score, or allocation "
                    "has been produced for this ticker.\n", style="dim")
        console.print(Panel(body, title="[bold red]INVALID TICKER[/bold red]",
                            border_style="red", box=box.DOUBLE))
        return

    # [DECISION-CORE] soft pre-render consistency check. Logs warnings if
    # the resolved action somehow conflicts with the rendered setup
    # (e.g. BUY with stop above entry, HOLD_EXISTING without position).
    if HAS_DECISION_CORE and getattr(result, 'final_action', ''):
        try:
            ts = result.trade_setup
            div_y = float(getattr(result.fundamental, 'dividend_yield', 0.0) or 0.0) \
                if result.fundamental else 0.0
            view = TickerView(
                ticker=result.ticker,
                summary_action=result.final_action,
                detail_action=result.final_action,
                risk_grade=(result.risk.risk_grade if result.risk else 'C'),
                current_price=float(result.current_price or 0.0),
                stop_loss=float(ts.stop_loss if ts else 0.0),
                # [T2 FIX] This read `ts.target_1`, which TradeSetup has never
                # had — the fields are target_conservative / target_base /
                # target_aggressive. The resulting AttributeError was swallowed
                # by the `except Exception` below and logged at DEBUG, so
                # validate_consistency() — the guard for "BUY with a stop above
                # entry" and "HOLD_EXISTING without a position" — threw on
                # every single ticker and NEVER ONCE RAN.
                target_price=float(ts.horizon_target if ts else 0.0),
                risk_reward_ratio=float(ts.risk_reward_ratio if ts else 0.0),
                has_existing_position=bool(getattr(result, 'has_existing_position', False)),
                dividend_yield_fraction=div_y,
            )
            rep = validate_consistency([view])
            if not rep.ok:
                for issue in rep.issues:
                    logger.warning(f"[consistency] {issue}")
        except AttributeError as _e:
            # A missing field is a code defect, not a runtime condition. Do not
            # let it hide again behind a debug line.
            logger.error(
                f"[consistency] check could not run for {result.ticker} — "
                f"{_e}. This is a bug; the pre-render guard is disabled."
            )
        except Exception as _e:
            logger.warning(f"[consistency] check skipped for {result.ticker}: {_e}")

    def fmt(val, dec=2):
        return format_currency(val, sym, dec)
    
    def make_bar(score, width=20):
        filled = int(score / 5)  # 100/5 = 20 blocks max
        return "█" * filled + "░" * (width - filled)
    
    # =========================================================================
    # SECTION 1: HEADER
    # =========================================================================
    price_arrow = "▲" if result.day_change_pct >= 0 else "▼"
    price_color = "green" if result.day_change_pct >= 0 else "red"
    # [ETF SUPPORT] use net assets / AUM label for funds, market cap for equities
    is_fund = bool(getattr(result, 'is_etf', False))
    instrument_tag = result.quote_type if is_fund else "EQUITY"
    if is_fund:
        size_value = result.net_assets if result.net_assets > 0 else result.market_cap
        size_label = "AUM"
    else:
        size_value = result.market_cap
        size_label = "Mkt Cap"
    mc_str = format_currency(size_value, sym) if size_value > 0 else ""

    header_text = Text()
    header_text.append(f"{result.company_name} ({result.ticker})", style="bold cyan")
    if is_fund:
        header_text.append(f"  [{instrument_tag}]", style="bold magenta")
    header_text.append(f" | {result.sector}\n", style="dim")
    header_text.append(f"{fmt(result.current_price)} ", style="bold white")
    header_text.append(f"{price_arrow} {result.day_change:+.2f} ({result.day_change_pct:+.2f}%)", style=f"bold {price_color}")
    if mc_str:
        header_text.append(f" | {size_label}: {mc_str}", style="dim")
    if is_fund and getattr(result, 'expense_ratio', 0.0) > 0:
        header_text.append(f" | Expense: {result.expense_ratio * 100:.2f}%", style="dim")

    title_text = (
        "[bold yellow]QUANTA ELITE ANALYSIS \u2014 ETF / FUND[/bold yellow]"
        if is_fund else
        "[bold yellow]QUANTA ELITE ANALYSIS[/bold yellow]"
    )
    console.print(Panel(header_text, title=title_text, border_style="yellow", box=box.DOUBLE))
    
    # Extended hours if available
    if result.pre_market_price > 0:
        pre_arrow = "▲" if result.pre_market_change_pct >= 0 else "▼"
        pre_color = "green" if result.pre_market_change_pct >= 0 else "red"
        console.print(f"  [dim]Pre-Market:[/dim] {fmt(result.pre_market_price)} [{pre_color}]{pre_arrow} {result.pre_market_change_pct:+.1f}%[/{pre_color}]")
    elif result.after_hours_price > 0:
        ah_arrow = "▲" if result.after_hours_change_pct >= 0 else "▼"
        ah_color = "green" if result.after_hours_change_pct >= 0 else "red"
        console.print(f"  [dim]After-Hours:[/dim] {fmt(result.after_hours_price)} [{ah_color}]{ah_arrow} {result.after_hours_change_pct:+.1f}%[/{ah_color}]")
    
    # Signal box - Format signal properly
    signal_str = result.signal.value if hasattr(result.signal, 'value') else str(result.signal)
    signal_display = signal_str.replace('_', ' ')  # Remove underscores for display
    confidence = result.confidence_score
    score = result.scores.composite_score if result.scores else 50
    risk_grade = result.risk.risk_grade if result.risk else "C"
    
    # =========================================================================
    # USE TRADE SETUP VALUES (already computed by TradeSetupGenerator)
    # Avoids duplication and ensures display matches the actual recommendation
    # =========================================================================
    ts = result.trade_setup
    
    # Target: prefer trade_setup base target, then valuation composite, then fallback
    if ts and ts.target_base > 0:
        target = ts.target_base
    elif result.valuation and result.valuation.target_base > 0:
        target = result.valuation.target_base
    elif result.market_intel and result.market_intel.target_mean > 0:
        target = result.market_intel.target_mean
    else:
        target = result.current_price * 1.10  # Default fallback
    
    # Stop: use trade_setup stop loss
    if ts and ts.stop_loss > 0:
        stop = ts.stop_loss
    else:
        # Fallback ATR-based stop if trade_setup wasn't generated
        vol = result.risk.annualized_volatility if result.risk and result.risk.annualized_volatility > 0 else 0.30
        if result.technical and result.technical.atr_percent > 0:
            stop_pct = min(0.10, max(0.03, 2 * result.technical.atr_percent / 100))
        else:
            stop_pct = min(0.08, max(0.03, vol / 4))
        stop = result.current_price * (1 - stop_pct)
    
    # Entry zone from trade_setup
    if ts and ts.entry_price > 0:
        aggressive_entry = ts.entry_price
        buy_zone_high = ts.entry_price * 0.99
        buy_zone_low = ts.entry_price * 0.97
    else:
        buy_zone_low = result.current_price * 0.95
        buy_zone_high = result.current_price * 0.98
        aggressive_entry = result.current_price * 0.99
    
    # Take-profit targets from trade_setup
    if ts and ts.target_conservative > 0:
        tp1 = ts.target_conservative
        tp2 = ts.target_base if ts.target_base > 0 else 0.0
        tp3 = ts.target_aggressive if ts.target_aggressive > 0 else 0.0
    else:
        # [T3 FIX] These were current_price * 1.05 / 1.10 / 1.15 — invented
        # round percentages printed under the heading "EXISTING HOLDER PROFIT
        # PLAN". A profit plan built from three arbitrary multiples of spot is
        # not a plan. Zero means "not computed"; the renderer omits the row.
        tp1 = tp2 = tp3 = 0.0

    # [T7 FIX] Trailing stop from the ONE definition in this file, rather than
    # `stop_pct + 0.02` (which produced the "9% below peak" line) or the
    # hardcoded 7% used by two other panels.
    trailing_stop_pct = (
        (ts.trailing_stop_pct / 100.0) if (ts and ts.trailing_stop_pct > 0)
        else 0.0
    )
    
    # Support-based stop for reference
    support_stop = result.technical.support_level if result.technical and result.technical.support_level > 0 else 0
    
    # =========================================================================
    # RISK/REWARD from trade_setup (single source of truth)
    # =========================================================================
    if ts and ts.risk_reward_ratio > 0:
        rr_ratio = ts.risk_reward_ratio
        rr_valid_setup = rr_ratio >= Config.MIN_RISK_REWARD
    elif target > result.current_price and stop < result.current_price:
        reward = target - result.current_price
        risk_amt = result.current_price - stop
        rr_ratio = reward / risk_amt if risk_amt > 0 else 0
        rr_valid_setup = rr_ratio >= Config.MIN_RISK_REWARD
    else:
        rr_ratio = 0
        rr_valid_setup = False
    
    target_upside = ((target / result.current_price) - 1) * 100 if result.current_price > 0 and target > 0 else 0
    stop_downside = ((stop / result.current_price) - 1) * 100 if result.current_price > 0 and stop > 0 else -3
    
    # ── Signal Box ──
    # Determine best horizon
    _horizon = "Mid-Term"
    if result.monte_carlo and result.monte_carlo.probability_profit > 0.6:
        _horizon = "Short-Term"
    if (piotroski_is_reportable(result.fundamental)
            and result.fundamental.piotroski_f_score >= 7):
        _horizon = "Long-Term"
    
    # Concise R/R label (no '0.0:1', never misleading)
    rr_label = _format_rr(rr_ratio, target, stop, result.current_price)
    rr_valid_setup = rr_ratio >= Config.MIN_RISK_REWARD
    grade = get_composite_grade(score)

    signal_color = "green" if "BUY" in signal_str else "red" if "SELL" in signal_str else "yellow"

    # Conviction prefix based on confidence
    _conv = _conviction_label(confidence)
    signal_display_full = f"{_conv} {signal_display}".strip() if _conv else signal_display

    # Pre-compute beginner guidance early so Executive Summary, Beginner
    # Guidance, and Action Plan share the same Buy Now / Timing / Reason.
    g = _compute_beginner_guidance(result)
    g["rr_label"] = rr_label

    # Candlestick context vars (reused by Candlestick Patterns panel below)
    t_cs = result.technical
    cs_patterns = list(getattr(t_cs, 'candlestick_patterns', []) or [])
    cs_bias = getattr(t_cs, 'candlestick_bias', 'NEUTRAL')
    cs_score = getattr(t_cs, 'candlestick_score', 50.0)
    prior_trend = getattr(t_cs, 'prior_trend', 'SIDEWAYS')
    bias_color = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(cs_bias, "yellow")
    bias_arrow = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "→"}.get(cs_bias, "→")

    _timing_color = {
        "GOOD BUY SETUP": "green",
        "HOLD WITH STOP": "yellow",
        "WAIT": "yellow",
        "WAIT / NO CLEAR EDGE": "yellow",
        "AVOID": "red",
        "NO CLEAR EDGE": "white",
    }.get(g["timing"], "white")
    _buy_color = {"YES": "green", "NO": "red", "ONLY AFTER CONFIRMATION": "yellow"}.get(g["buy_now"], "white")

    signal_table = Table(show_header=False, box=None, expand=True)
    signal_table.add_column("Metric", style="bold")
    signal_table.add_column("Value")
    signal_table.add_column("Metric2", style="bold")
    signal_table.add_column("Value2")

    signal_table.add_row("SIGNAL:", f"[{signal_color} bold]{signal_display_full}[/{signal_color} bold]", "Confidence:", f"{confidence:>3.0f}%")

    # Show score with confidence interval if available
    score_str = f"{score:>3.0f}/100 ({grade})"

    signal_table.add_row("Score:", score_str, "Risk Grade:", f"{risk_grade}")
    signal_table.add_row("Buy Now:", f"[{_buy_color}]{g['buy_now']}[/{_buy_color}]", "R/R (to base target, net costs):", f"{rr_label}")
    signal_table.add_row("Timing:", f"[{_timing_color}]{g['timing']}[/{_timing_color}]", "Horizon:", f"{_horizon}")
    # Main reason spans the row (full width via second column)
    signal_table.add_row("Reason:", f"[dim]{g['main_reason']}[/dim]", "", "")

    console.print(Panel(signal_table, title="[bold]EXECUTIVE SUMMARY[/bold]", border_style="blue", box=box.ROUNDED))
    
    # =========================================================================
    # BEGINNER GUIDANCE  (placed BEFORE Action Plan so a new buyer reads the
    # "Buy Now / Wait" verdict first, then sees the holder-oriented plan.)
    # =========================================================================
    _eq_color = {"Strong": "green", "Moderate": "yellow", "Weak": "yellow", "Poor": "red"}.get(g["entry_quality"], "white")
    _rr_color = "green" if g["rr_label"] not in ("Unfavorable", "N/A") else "red"

    # Context-aware final action: a Risk-F / Buy Now = NO name never shows a
    # bare "HOLD" that reads like a fresh-buy endorsement.
    _rec_label = _contextual_recommendation_label(signal_display, _conv, risk_grade, g["buy_now"])

    # Entry / stop levels (reused by Action Plan below)
    _break_lvl = t_cs.week_52_high if t_cs.week_52_high > result.current_price else 0.0
    # Realistic pullback entry: nearest support below price (20/50-day MA, VWAP,
    # swing support). Flagged "deep pullback only" when >25% below current.
    _pull_lvl = g["pullback_entry"]
    _pull_stop = g["pullback_stop"]
    _pull_deep = g["pullback_is_deep"]
    _pull_basis = g["pullback_basis"]
    _tac_stop = g["tactical_stop"]
    _tac_basis = g["tactical_stop_basis"]

    # Compact two-column grid: label / value pairs for fast scanning
    beg_table = Table(show_header=False, box=None, expand=True)
    beg_table.add_column("Field", style="bold", width=22)
    beg_table.add_column("Value")
    beg_table.add_column("Field2", style="bold", width=22)
    beg_table.add_column("Value2")

    beg_table.add_row("Recommendation:", f"[bold {signal_color}]{_rec_label}[/bold {signal_color}]",
                      "Candlestick Timing:", f"[{_timing_color}]{g['timing']}[/{_timing_color}]")
    beg_table.add_row("Buy Now:", f"[{_buy_color}]{g['buy_now']}[/{_buy_color}]",
                      "Entry Quality:", f"[{_eq_color}]{g['entry_quality']}[/{_eq_color}]")
    beg_table.add_row("Risk/Reward:", f"[{_rr_color}]{g['rr_label']}[/{_rr_color}]",
                      "Volume Requirement:", "≥ 1.5x avg on entry day")
    beg_table.add_row("New Buyer:", g["new_buyer"], "Existing Holder:", g["existing_holder"])
    beg_table.add_row("Aggressive Buyer:", g["aggressive"], "Data Quality:",
                      f"[{'green' if g['data_quality']=='PASS' else 'yellow' if g['data_quality']=='WARN' else 'red'}]{g['data_quality']}[/]")
    beg_table.add_row("Long-Term Holder Stop:", f"{fmt(stop)}" if stop > 0 else "N/A",
                      "Breakout Entry:", f"above {fmt(_break_lvl)}" if _break_lvl > 0 else "N/A")
    _tac_disp = (f"{fmt(_tac_stop)}" + (f" ({_tac_basis})" if _tac_basis else "")) if _tac_stop > 0 else "N/A"
    if _pull_lvl > 0:
        _pull_disp = (f"deep pullback only — {fmt(_pull_lvl)}"
                      if _pull_deep else f"near {fmt(_pull_lvl)}")
        if _pull_basis:
            _pull_disp += f" [{_pull_basis}]"
    else:
        _pull_disp = "N/A"
    beg_table.add_row("Tactical Stop (short-term):", _tac_disp,
                      "Pullback Stop:", f"{fmt(_pull_stop)}" if _pull_stop > 0 else "N/A")
    beg_table.add_row("Pullback Entry:", _pull_disp, "", "")

    action_line = Text()
    action_line.append("Beginner Action: ", style="bold")
    action_line.append(g["beginner_action"])

    beg_parts = [beg_table, Text(""), action_line]

    # Strong-business / poor-entry conflict gets a dedicated, highly visible line.
    if g.get("risk_conflict_warning"):
        conflict_text = Text()
        conflict_text.append("\n⚠ ", style="bold red")
        conflict_text.append(g["risk_conflict_warning"], style="bold yellow")
        beg_parts.append(conflict_text)

    # Inline warnings near Beginner Guidance (item 15: warnings near relevant
    # section). Keep concise — max 3 lines.
    if g["warnings"]:
        warn_text = Text()
        warn_text.append("\nWarnings: ", style="bold yellow")
        warn_text.append("; ".join(g["warnings"][:3]), style="yellow")
        beg_parts.append(warn_text)

    console.print(Panel(Group(*beg_parts),
                        title="[bold]BEGINNER GUIDANCE[/bold]",
                        border_style=bias_color, box=box.ROUNDED))

    # =========================================================================
    # ACTION PLAN  (renamed to "EXISTING HOLDER PROFIT PLAN" when Buy Now = NO
    # so a new buyer does not mistake exit targets for entry targets.)
    # =========================================================================
    action_text = Text()
    signal_lower = signal_str.lower()
    is_holder_plan = (g["buy_now"] == "NO")

    if is_holder_plan:
        action_text.append(
            "These targets are for existing holders, not new entry recommendations.\n\n",
            style="dim italic",
        )
        resistance_str = fmt(result.technical.resistance_level) if result.technical and result.technical.resistance_level > 0 else "N/A"
        action_text.append(f"Hold / Trim plan. Stop at {fmt(stop)}. Watch {resistance_str} resistance.\n", style="bold yellow")
    elif 'buy' in signal_lower or 'accumulate' in signal_lower:
        action_text.append(
            "Profit plan is hypothetical — assumes entry at current price.\n\n",
            style="dim italic",
        )
        action_text.append(f"Entry Zone:      {fmt(buy_zone_low)} - {fmt(buy_zone_high)} (pullback -2% to -5%)\n", style="bold green")
        _cap = 10000
        _pct = 0.03 if risk_grade in ['A', 'B'] else (0.025 if risk_grade == 'C' else 0.02)
        _val = _cap * _pct
        _sh = max(1, int(_val / result.current_price)) if result.current_price > 0 else 0
        _val = _sh * result.current_price
        action_text.append(f"Position Size:   {sym}{_val:,.0f} of {sym}10K ({_val/100:.1f}%) = {_sh} share{'s' if _sh != 1 else ''}\n")
    elif 'hold' in signal_lower:
        resistance_str = fmt(result.technical.resistance_level) if result.technical and result.technical.resistance_level > 0 else "N/A"
        action_text.append(f"Hold existing. Stop at {fmt(stop)}. Watch {resistance_str} resistance.\n", style="bold yellow")
    elif 'sell' in signal_lower or 'reduce' in signal_lower:
        action_text.append("Exit or reduce exposure. Risk/reward unfavorable.\n", style="bold red")

    # Exit levels (always for existing holders)
    action_text.append(f"\nStop Loss:       {fmt(stop):<14} [{stop_downside:+.1f}%]   ")
    action_text.append("EXIT ALL", style="bold red")
    action_text.append("\n")
    # [T3/T13 FIX] Only print targets that were actually computed and that sit
    # above spot. The old `if target > current_price` guard let the fabricated
    # tp1/tp2/tp3 through whenever ANY analyst target existed, and would have
    # printed a target below the stop once valuation-anchored targets could
    # legitimately land there.
    _tps = [(lbl, tp, act) for lbl, tp, act in (
        ("Target 1", tp1, "Sell 33%"),
        ("Target 2", tp2, "Sell 33%"),
        ("Target 3", tp3, "Sell rest"),
    ) if tp > result.current_price]
    if _tps:
        for lbl, tp, act in _tps:
            action_text.append(
                f"{lbl + ':':<17}{fmt(tp):<14} [{((tp/result.current_price)-1)*100:+.1f}%]   ")
            action_text.append(act, style="bold green")
            action_text.append("\n")
    else:
        action_text.append(
            "Targets:         none above spot — the valuation does not support "
            "a profit plan here\n", style="dim")
    if trailing_stop_pct > 0:
        action_text.append(
            f"Trailing Stop:   {trailing_stop_pct*100:.1f}% below peak "
            f"({TradeSetupGenerator.TRAILING_ATR_MULT:g}xATR) once +1R\n", style="dim")

    action_title = "EXISTING HOLDER PROFIT PLAN" if is_holder_plan else "ACTION PLAN"
    console.print(Panel(action_text, title=f"[bold]{action_title}[/bold]", border_style="green", box=box.ROUNDED))

    # =========================================================================
    # CANDLESTICK PATTERNS  (uses cs_bias / patterns precomputed above)
    # =========================================================================
    t = result.technical  # keep `t` alias available for sections below
    patterns = cs_patterns

    summary_line = Text()
    summary_line.append("Net Bias: ", style="bold")
    summary_line.append(f"{bias_arrow} {cs_bias}  ", style=f"bold {bias_color}")
    summary_line.append(f"│  Score: {cs_score:>4.0f}/100  │  ", style="dim")
    summary_line.append(f"Prior Trend: {prior_trend}  │  ", style="dim")
    summary_line.append(
        f"{len(patterns)} pattern(s) on last 3 bars" if patterns
        else "no patterns on last 3 bars",
        style="dim",
    )

    if patterns:
        cs_table = Table(show_header=True, box=box.SIMPLE, expand=True,
                         header_style="bold cyan")
        cs_table.add_column("Pattern", style="bold", width=22)
        cs_table.add_column("Bias", width=12)
        cs_table.add_column("Type", width=14)
        cs_table.add_column("Reliability", width=12)
        cs_table.add_column("Meaning", overflow="fold")
        for p in patterns[:6]:
            b = p['bias']
            col = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}[b]
            stars = "★" * p['strength']
            cs_table.add_row(
                p['name'],
                f"[{col}]{b} {stars}[/{col}]",
                p['type'],
                p['reliability'],
                p['context'],
            )
        cs_group = Group(summary_line, Text(""), cs_table)
    else:
        empty_line = Text(
            "No reversal/continuation candlestick patterns triggered on the "
            "most recent 3 bars. This is itself a signal: the market is not "
            "showing decisive buyer/seller exhaustion — defer to trend & momentum.",
            style="dim italic",
        )
        cs_group = Group(summary_line, Text(""), empty_line)

    console.print(Panel(cs_group,
                        title="[bold]CANDLESTICK PATTERNS[/bold]",
                        border_style=bias_color, box=box.ROUNDED))

    # ─── Section 3: Analysis Breakdown ───
    # (Multi-Factor Scores + Why/Risks are rendered AFTER Signal Conflict /
    # Upgrade Triggers so the section order matches the desired layout.)

    # Signal Conflict Resolution + Upgrade Triggers (COMBINED)
    show_upgrade_section = False
    
    if result.scores.fundamental_score >= 70 or result.market_intel.analyst_rating == "BUY":
        # Check for conflicts
        has_conflict = False
        conflict_reasons = []
        
        if signal_str in ['HOLD', 'REDUCE', 'SELL'] and result.scores.fundamental_score >= 70:
            has_conflict = True
            conflict_reasons.append(f"Strong fundamentals ({result.scores.fundamental_score:.0f}/100) but signal is {signal_display}")
        
        if signal_str in ['HOLD', 'REDUCE', 'SELL'] and result.market_intel.analyst_rating == "BUY":
            has_conflict = True
            conflict_reasons.append(f"Analyst consensus is BUY but signal is {signal_display}")
        
        if has_conflict:
            conflict_text = Text()
            for conflict in conflict_reasons:
                conflict_text.append(f"⚡ {conflict}\n", style="bold yellow")
            
            # Explain the override logic
            conflict_text.append(f"\nWhy {signal_display} despite strong signals:\n", style="bold")
            
            if risk_grade in ['D', 'F']:
                conflict_text.append(f"• Risk Grade {risk_grade} - Extreme risk overrides bullish factors\n")
            
            if not rr_valid_setup and rr_ratio > 0:
                conflict_text.append(f"• Poor R:R setup ({_T.fmt_rr(rr_ratio)} < {_T.fmt_rr(_T.RR.RESEARCH_MIN)} minimum threshold)\n")
                conflict_text.append(f"  Risking {abs(stop_downside):.0f}% to gain {target_upside:.0f}% is unfavorable\n", style="dim")
            
            if result.scores.valuation_score < 40:
                conflict_text.append(f"• Overvaluation concern (valuation score: {result.scores.valuation_score:.0f}/100)\n")
            
            if result.risk.annualized_volatility > 0.40:
                conflict_text.append(f"• High volatility ({result.risk.annualized_volatility:.0%}) increases risk\n")
            
            conflict_text.append(f"\n→ Capital preservation prioritized over potential upside\n", style="bold cyan")
            console.print(Panel(conflict_text, title="[bold]SIGNAL CONFLICT RESOLUTION[/bold]", border_style="yellow", box=box.ROUNDED))
            show_upgrade_section = True  # Show upgrade triggers since there's potential
    
    # UPGRADE TRIGGERS (NEW - Actionable entry improvements)
    if show_upgrade_section or signal_str in ['HOLD', 'REDUCE']:
        triggers_text = Text()
        triggers_text.append("What would upgrade to BUY:\n", style="bold")
        
        triggers = []
        
        # Price-based triggers - FIXED: Use support as entry target, NOT stop
        if result.technical and result.technical.support_level > 0 and result.technical.support_level < result.current_price:
            pullback_pct = ((result.current_price - result.technical.support_level) / result.current_price) * 100
            
            # Calculate improved R:R if entering at support instead of current price
            improved_rr = 0
            if stop > 0 and stop < result.technical.support_level:
                new_reward = target - result.technical.support_level
                new_risk = result.technical.support_level - stop
                improved_rr = new_reward / new_risk if new_risk > 0 else 0
            
            # Only show if pullback is meaningful AND improves R:R
            if pullback_pct > 3:
                rr_text = f" improves R:R to {improved_rr:.1f}:1" if improved_rr >= 1.5 else ""
                triggers.append(f"• Pullback to support {sym}{result.technical.support_level:.2f} (-{pullback_pct:.0f}%){rr_text}")
        
        # Breakout trigger
        if result.technical and result.technical.week_52_high > result.current_price:
            breakout_pct = ((result.technical.week_52_high / result.current_price) - 1) * 100
            if breakout_pct < 10:
                triggers.append(f"• Breakout above {sym}{result.technical.week_52_high:.2f} (+{breakout_pct:.0f}%) confirms momentum")
        
        # Risk reduction trigger
        if risk_grade in ['D', 'F']:
            triggers.append(f"• Risk grade improves to C or better (address leverage/volatility)")
        
        # Stock volatility trigger (uses stock vol, NOT VIX — fixed wording)
        if result.risk and result.risk.annualized_volatility > 0.30:
            _vol_pct = result.risk.annualized_volatility * 100
            triggers.append(f"• Stock volatility cools below 30% (currently {_vol_pct:.0f}% annualized)")
        
        # R:R improvement
        _rr_min = _T.RR.RESEARCH_MIN
        if rr_ratio < _rr_min and rr_ratio > 0:
            target_price_for_good_rr = result.current_price + (_rr_min * (result.current_price - stop))
            triggers.append(f"• Better entry improves R:R to ≥{_T.fmt_rr(_rr_min)} (need {abs(stop_downside)*_rr_min:.0f}% upside)")
        
        if triggers:
            for trigger in triggers[:4]:
                triggers_text.append(f"{trigger}\n")
            triggers_text.append(f"\nWaiting for better setup improves risk-adjusted returns\n", style="dim italic")
        else:
            triggers_text.append(f"Signal already near optimal - main risks are systemic/macro\n")
            
        console.print(Panel(triggers_text, title="[bold]UPGRADE TRIGGERS[/bold]", border_style="blue", box=box.ROUNDED))
    
    # ─── Multi-Factor Scores (moved here per requested layout) ───
    # [T14 FIX] `ScoreBreakdown.weights` carries the real weights the composite
    # was built from and was never displayed, so a reader could not tell that
    # Technical carries 35% and Quality 8% — nor reconcile the sub-scores with
    # the composite. The Quality row was also missing from the table entirely,
    # so one of the six weighted inputs was invisible. Both are shown now, with
    # each factor's contribution in composite points.
    _w = dict(getattr(result.scores, 'weights', None) or {})
    scores_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    # Bar dropped: Score + Weight + Contribution already carry the magnitude,
    # and seven columns cramped every label into an ellipsis.
    scores_table.add_column("Factor", no_wrap=True)
    scores_table.add_column("Score", justify="right", no_wrap=True)
    scores_table.add_column("Weight", justify="right", no_wrap=True)
    scores_table.add_column("Contrib", justify="right", no_wrap=True)
    scores_table.add_column("Rating", no_wrap=True)
    scores_table.add_column("Key Drivers", style="dim")

    scores_data = [
        ("Technical", result.scores.technical_score, 'technical', "RSI, MACD, trend"),
        ("Fundamental", result.scores.fundamental_score, 'fundamental', "ROE, margins, growth"),
        ("Valuation", result.scores.valuation_score, 'valuation', "P/E, DCF, targets"),
        ("Quality", result.scores.quality_score, 'quality', "Balance sheet, F-score"),
        ("Sentiment", result.scores.sentiment_score, 'sentiment', "News, analyst ratings"),
        ("Risk-Adjusted", result.scores.risk_score, 'risk', "Volatility, drawdown"),
    ]
    _contrib_total = 0.0
    for name, score_val, wkey, desc in scores_data:
        label = "Strong" if score_val >= 70 else ("Good" if score_val >= 55 else ("Fair" if score_val >= 40 else "Weak"))
        color = "green" if score_val >= 70 else ("yellow" if score_val >= 40 else "red")
        wt = float(_w.get(wkey, 0.0) or 0.0)
        contrib = score_val * wt
        _contrib_total += contrib
        scores_table.add_row(
            name, f"{score_val:>3.0f}",
            f"{wt:.0%}" if wt else "—",
            f"{contrib:.1f}" if wt else "—",
            f"[{color}]{label}[/{color}]", desc)

    comp = result.scores.composite_score
    comp_bar = make_bar(comp, 16)
    comp_grade = get_composite_grade(comp)
    comp_color = "green" if comp >= 70 else ("yellow" if comp >= 40 else "red")
    scores_table.add_row(
        "COMPOSITE", f"[bold]{comp:>3.0f}[/bold]",
        f"{sum(_w.values()):.0%}" if _w else "—",
        f"[bold]{_contrib_total:.1f}[/bold]" if _w else "—",
        f"[bold {comp_color}]Grade {comp_grade}[/bold {comp_color}]",
        "")
    _title = "MULTI-FACTOR SCORES"
    if _w and abs(sum(_w.values()) - 1.0) > 0.01:
        _title += f"  ⚠ weights sum to {sum(_w.values()):.0%}, not 100%"
    console.print(Panel(scores_table, title=f"[bold]{_title}[/bold]", border_style="magenta", box=box.ROUNDED))

    # [NEW] Whether the trade is executable, not merely attractive: liquidity
    # against the proposed size, earnings proximity, cost drag in dollars and
    # data provenance. All were computed or available and none reached the page.
    try:
        display_execution_context(result, sym)
    except Exception as _e:
        logger.warning(f"execution-context panel failed: {_e}")

    # Why / Key Risks side-by-side
    why_text = Text()
    if result.key_insights:
        for insight in result.key_insights[:4]:
            clean = str(insight).replace("⏸️ ", "").replace("🟢 ", "• ").replace("📈 ", "• ").replace("📊 ", "• ").replace("💪 ", "• ").replace("⭐ ", "• ").replace("⚠️ ", "").replace("📰 ", "• ")
            if not clean.startswith("• "):
                clean = "• " + clean
            why_text.append(f"{clean[:75]}\n")
    risks_text = Text()
    if result.risk_warnings:
        for warning in result.risk_warnings[:3]:
            clean_warning = str(warning).replace("⚠️ ", "⚠ ").replace("🚨 ", "⚠ ")
            risks_text.append(f"{clean_warning[:75]}\n", style="red")
    else:
        # An empty bordered panel reads as a rendering failure. Say that no
        # warning fired, and say what that does and does not mean.
        risks_text.append(
            "No risk warning triggered.\nThis is the absence of a flag, not a "
            "clean bill of health — see the risk metrics below.", style="dim")
    console.print(Columns([
        Panel(why_text, title=f"[bold]WHY {signal_display.upper()}[/bold]", border_style="cyan", expand=True),
        Panel(risks_text, title="[bold]KEY RISKS[/bold]", border_style="red", expand=True),
    ]))

    # ─── Section 4: Market Intelligence ───
    
    # News & Sentiment
    s = result.sentiment
    # Separate direct-company news from peer/sector and macro noise so that
    # only direct headlines drive the company sentiment read.
    _peer_tk = []
    _pc = getattr(result, 'peer_comparison', None)
    if _pc is not None:
        _peer_tk = list(getattr(_pc, 'peer_tickers', []) or [])
    _news = _classify_news_relevance(
        getattr(s, 'top_articles', None) or [],
        result.ticker, getattr(result, 'company_name', ''), _peer_tk)
    news_text = Text()
    if _news['has_direct_news']:
        news_text.append(
            f"Direct {result.ticker} Sentiment: {_news['direct_sentiment_label']} "
            f"({_news['direct_sentiment_score']:+.2f}) | Trend: {s.sentiment_trend}\n",
            style="bold")
    else:
        news_text.append(
            f"Sentiment: {s.sentiment_label} ({s.sentiment_score:+.2f}) | Trend: {s.sentiment_trend}\n",
            style="bold")
        news_text.append(
            "No direct company headlines found — score driven by peer/macro news; confidence reduced.\n",
            style="dim yellow")
    # Provenance line (only shown when available)
    _ts = getattr(s, 'last_news_timestamp', '') or ''
    _srcs = getattr(s, 'key_headline_sources', []) or []
    _src_str = ', '.join(dict.fromkeys(_srcs[:3]))
    if _ts or _src_str:
        news_text.append(
            f"Latest: {_ts or 'unknown date'} | Source: {_src_str or 'unknown source'}\n",
            style="dim",
        )
    else:
        news_text.append(
            "Source/date unavailable — sentiment confidence reduced.\n",
            style="dim yellow",
        )
    news_text.append("\n")

    def _add_news_group(label, headlines, style):
        if not headlines:
            return
        news_text.append(f"{label}:\n", style=style)
        for headline in headlines[:3]:
            wrapped = textwrap.fill(headline, width=72, initial_indent="  • ", subsequent_indent="    ")
            news_text.append(f"{wrapped}\n")

    if _news['direct_headlines'] or _news['peer_headlines'] or _news['macro_headlines']:
        _add_news_group(f"Direct {result.ticker} news", _news['direct_headlines'], "bold cyan")
        _add_news_group("Peer / sector news (weighted lower)", _news['peer_headlines'], "bold")
        _add_news_group("Macro / market news", _news['macro_headlines'], "dim")
    elif s.key_headlines:
        for headline in s.key_headlines[:3]:
            wrapped = textwrap.fill(headline, width=75, initial_indent="• ", subsequent_indent="  ")
            news_text.append(f"{wrapped}\n")
    else:
        news_text.append("• No recent headlines\n")

    console.print(Panel(news_text, title="[bold]NEWS & SENTIMENT[/bold]", border_style="cyan", box=box.ROUNDED))
    
    # Catalyst Alerts
    if HAS_CATALYST and hasattr(result, 'catalyst_signals') and result.catalyst_signals:
        cat_text = Text()
        for catalyst in result.catalyst_signals[:2]:
            cat_text.append(f"• {catalyst.catalyst_type}: {catalyst.headline[:65]}\n", style="bold")
            cat_text.append(f"  Impact: {catalyst.price_impact_estimate:+.1f}% | Confidence: {catalyst.confidence:.0%}\n", style="dim")
        console.print(Panel(cat_text, title="[bold red]ACTIVE CATALYST DETECTED[/bold red]", border_style="red", box=box.ROUNDED))
    elif result.catalysts and result.catalysts.earnings_date != "Unknown":
        cat_text = Text()
        # Earnings Timeline with Context Awareness
        cat = result.catalysts # Alias for brevity
        if cat.earnings_date and cat.earnings_date != "Unknown":
            try:
                from datetime import datetime, timedelta
                from dateutil import parser
                
                current_date = datetime.now()
                earnings_dt = parser.parse(cat.earnings_date)
                delta_days = (earnings_dt - current_date).days
                
                if delta_days < 0:  # Past earnings
                    days_ago = abs(delta_days)
                    next_est = earnings_dt + timedelta(days=90)  # Quarterly estimate
                    next_delta = (next_est - current_date).days
                    cat_text.append(f"Last Earnings: {cat.earnings_date} ({days_ago}d ago)\n")
                    cat_text.append(f"Next Est: ~{next_est.strftime('%Y-%m-%d')} (in ~{next_delta}d)\n")
                elif delta_days <= 7:  # Imminent earnings
                    cat_text.append(f"!! EARNINGS RISK: {cat.earnings_date} (in {delta_days}d)\n", style="bold red")
                    cat_text.append(f"   High volatility expected around earnings\n", style="red")
                else:  # Future earnings
                    cat_text.append(f"Earnings: {cat.earnings_date} (in {delta_days}d)\n")
            except:
                cat_text.append(f"Earnings: {cat.earnings_date}\n")
        
        # Dividends — cat.dividend_yield is stored as FRACTION (0.0088 = 0.88%)
        if cat.ex_dividend_date and cat.ex_dividend_date != "Unknown":
            div_text = f"{sym}{cat.dividend_amount:.2f}" if cat.dividend_amount > 0 else "TBD"
            _y = cat.dividend_yield if cat.dividend_yield <= 1.0 else cat.dividend_yield / 100.0
            if HAS_DECISION_CORE:
                _display_ok, _note = validate_dividend_yield(_y, market_cap=getattr(result.fundamental, 'market_cap', 0.0))
            else:
                _display_ok, _note = (_y <= 0.50, "")
            if _y > 0 and _display_ok:
                yield_text = f", {_y * 100:.2f}%"
            elif _y > 0:
                yield_text = ", data validation failed"
            else:
                yield_text = ""
            cat_text.append(f"Ex-Dividend: {cat.ex_dividend_date} ({div_text}{yield_text})\n")
            
        console.print(Panel(cat_text, title="[bold]CATALYST ALERTS[/bold]", border_style="yellow", box=box.ROUNDED))
    
    # Analyst Consensus
    mi = result.market_intel
    if mi.target_mean > 0:
        analyst_text = Text()
        analyst_text.append(f"Rating: {mi.analyst_rating or 'N/A'} | Target: {fmt(mi.target_mean)} ({mi.implied_upside:+.1f}%)\n", style="bold")
        rating_total = int(mi.buy_ratings + mi.hold_ratings + mi.sell_ratings)
        if rating_total > 0:
            analyst_text.append(
                f"Buy: {mi.buy_ratings} | Hold: {mi.hold_ratings} | "
                f"Sell: {mi.sell_ratings} | Coverage: {mi.analyst_count} | "
                f"Price Range: {fmt(mi.target_low)}-{fmt(mi.target_high)}\n"
            )
        else:
            analyst_text.append(
                f"Coverage: {mi.analyst_count or 'N/A'} | Rating breakdown unavailable | "
                f"Price Range: {fmt(mi.target_low)}-{fmt(mi.target_high)}\n"
            )
        console.print(Panel(analyst_text, title="[bold]ANALYST CONSENSUS[/bold]", border_style="blue", box=box.ROUNDED))
    
    # Insider Activity
    ins = result.insider
    # Skip insider for ETFs (check fundamental flag)
    is_etf = hasattr(result.fundamental, 'is_etf') and result.fundamental.is_etf
    if not is_etf:
        # Determine display label: only mark BEARISH when we have transaction
        # context (sale type / dates). Otherwise downgrade to CAUTIOUS so the
        # user does not over-interpret an unlabelled net-outflow.
        _has_context = bool(ins.key_transactions)
        if ins.insider_sentiment == "BEARISH" and not _has_context:
            ins_label = "CAUTIOUS"
            ins_sym = "○"
            ins_color = "yellow"
        elif ins.insider_sentiment == "BULLISH":
            ins_label = "BULLISH"; ins_sym = "▲"; ins_color = "green"
        elif ins.insider_sentiment == "BEARISH":
            ins_label = "BEARISH"; ins_sym = "▼"; ins_color = "red"
        else:
            ins_label = ins.insider_sentiment; ins_sym = "○"; ins_color = "yellow"

        ins_text = Text()
        ins_text.append(f"Sentiment: {ins_sym} {ins_label}\n", style=f"bold {ins_color}")
        ins_text.append(f"Recent Buys: {ins.recent_buys} ({sym}{ins.buy_value/1000000:.1f}M) | Recent Sells: {ins.recent_sells} ({sym}{ins.sell_value/1000000:.1f}M) | Net: {sym}{ins.net_flow:,.0f}\n")
        if ins.key_transactions:
            ins_text.append(f"Notable Transactions (with sale type/date when available):\n", style="bold")
            for txn in ins.key_transactions[:3]:
                ins_text.append(f"  • {txn}\n")
        elif ins_label == "CAUTIOUS":
            ins_text.append(
                "Reason: Net selling detected, but sale context is incomplete "
                "(sale type and time window not available).\n",
                style="dim yellow",
            )

        console.print(Panel(ins_text, title="[bold]INSIDER ACTIVITY[/bold]", border_style="magenta", box=box.ROUNDED))
    
    # ─── Section 5: Price Targets ───
    
    v = result.valuation
    mc = result.monte_carlo
    current = result.current_price
    
    # Weighted Price Targets
    if v.target_base > 0:
        upside_cons = ((v.target_conservative / current) - 1) * 100 if current > 0 else 0
        upside_base = ((v.target_base / current) - 1) * 100 if current > 0 else 0
        upside_aggr = ((v.target_aggressive / current) - 1) * 100 if current > 0 else 0
        
        pt_table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE)
        pt_table.add_column("Scenario")
        pt_table.add_column("Target", justify="right")
        pt_table.add_column("Upside", justify="right")
        pt_table.add_column("Description", style="dim")
        
        pt_table.add_row("Conservative", fmt(v.target_conservative), f"[{'green' if upside_cons > 0 else 'red'}]{upside_cons:+.0f}%[/{'green' if upside_cons > 0 else 'red'}]", "Bearish case, low estimates")
        pt_table.add_row("Base Target", fmt(v.target_base), f"[{'green' if upside_base > 0 else 'red'}]{upside_base:+.0f}%[/{'green' if upside_base > 0 else 'red'}]", f"Most likely - {v.target_probability:.0f}% 30d proxy")
        pt_table.add_row("Aggressive", fmt(v.target_aggressive), f"[{'green' if upside_aggr > 0 else 'red'}]{upside_aggr:+.0f}%[/{'green' if upside_aggr > 0 else 'red'}]", "Bull case, high estimates")
        
        # [T8 FIX] The caveat lived in the panel TITLE, which Rich truncates to
        # the panel width — it rendered as "Target Confidence: Heuristic (not a
        # va" and the disclaimer was cut off mid-word. A caveat that only
        # survives on wide terminals is not a caveat. It goes in the body.
        pt_caveat = Text()
        pt_caveat.append(
            "Heuristic blend of DCF, analyst consensus and multiples. "
            "Not a validated probability and not a promise the target is reached.",
            style="dim italic")
        console.print(Panel(
            Group(pt_table, pt_caveat),
            title="[bold]WEIGHTED PRICE TARGETS (12 months)[/bold]",
            border_style="cyan", box=box.ROUNDED))
    
    # Monte Carlo Projections
    if mc and mc.simulations_run > 0:
        median_upside = ((mc.median_price / current) - 1) * 100 if current > 0 else 0
        upside_95 = ((mc.percentile_95 / current) - 1) * 100 if current > 0 else 0
        downside_5 = ((mc.percentile_5 / current) - 1) * 100 if current > 0 else 0
        
        mc_table = Table(show_header=True, header_style="bold blue", box=box.SIMPLE)
        mc_table.add_column("Scenario")
        mc_table.add_column("Target", justify="right")
        mc_table.add_column("Upside", justify="right")
        mc_table.add_column("Description", style="dim")
        
        mc_table.add_row("Median Price", fmt(mc.median_price), f"[{'green' if median_upside > 0 else 'red'}]{median_upside:+.0f}%[/{'green' if median_upside > 0 else 'red'}]", "")
        mc_table.add_row("Upside (95th)", fmt(mc.percentile_95), f"[{'green' if upside_95 > 0 else 'red'}]{upside_95:+.0f}%[/{'green' if upside_95 > 0 else 'red'}]", "Best case scenario")
        mc_table.add_row("Downside (5th)", fmt(mc.percentile_5), f"[{'green' if downside_5 > 0 else 'red'}]{downside_5:+.0f}%[/{'green' if downside_5 > 0 else 'red'}]", "Worst case scenario")
        
        mc_text = Text()
        mc_text.append(f"Probability Analysis:\n", style="bold")
        mc_text.append(f"• Profit: {mc.probability_profit:.0%}\n")
        mc_text.append(f"• +10% gain: {mc.probability_10pct_gain:.0%}\n")
        mc_text.append(f"• -10% loss: {mc.probability_10pct_loss:.0%}\n")
        
        console.print(Panel(Columns([mc_table, mc_text]), title=f"[bold]MONTE CARLO PROJECTIONS (30 days, {mc.simulations_run} simulations)[/bold]", border_style="blue", box=box.ROUNDED))

        # Item 3: concise note when Monte Carlo disagrees with valuation targets.
        _val_target = v.target_base if v and v.target_base > 0 else 0.0
        _analyst_t = mi.target_mean if mi and mi.target_mean > 0 else 0.0
        _ref_target = max(_val_target, _analyst_t)
        if _ref_target > 0 and mc.median_price > _ref_target * 1.02:
            console.print(
                "[yellow]Model Note: Monte Carlo is more bullish short-term than "
                "valuation targets. Treat upside as momentum-driven, not "
                "valuation-confirmed.[/yellow]"
            )
    
    # Price Target Breakdown Table
    pt_breakdown = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    pt_breakdown.add_column("Method")
    pt_breakdown.add_column("Target", justify="right")
    pt_breakdown.add_column("Upside", justify="right")
    pt_breakdown.add_column("Source", style="dim")
    
    # Collect targets
    targets = []
    
    # Only show analyst targets if they exist (non-zero)
    if mi.target_low > 0:
        targets.append(("Analyst Low", mi.target_low, "Conservative analyst estimate"))
    if mi.target_mean > 0:
        targets.append(("Analyst Consensus", mi.target_mean, "Wall Street analysts"))
    if mi.target_high > 0:
        targets.append(("Analyst High", mi.target_high, "Optimistic analyst estimate"))
        
    # Graham Number: only include for value stocks (P/E <= 20)
    # Growth stocks are better valued with DCF/analyst targets
    pe_ratio = result.fundamental.pe_ratio if hasattr(result, 'fundamental') and result.fundamental else 999
    if v.graham_number > 0 and pe_ratio <= 20:
        targets.append(("Graham Number", v.graham_number, "Benjamin Graham formula"))
    
    # Sort by target price and display with proper alignment
    targets_sorted = sorted(targets, key=lambda x: x[1])
    for method, target_val, source in targets_sorted[:11]:  # Limit to 11 rows
        upside_pct = ((target_val / current) - 1) * 100 if current > 0 else 0
        color = "green" if upside_pct > 0 else "red"
        arrow = "▲" if upside_pct > 0 else "▼"
        pt_breakdown.add_row(method, fmt(target_val), f"[{color}]{arrow} {upside_pct:+.1f}%[/{color}]", source)
    
    # Model blend: use the same coverage-aware deterministic target that the
    # recommendation engine consumed, never a second display-only average.
    if targets:
        avg_target = v.target_base if v.target_base > 0 else mi.target_mean
        avg_upside = ((avg_target / current) - 1) * 100 if current > 0 else 0
        color = "green" if avg_upside > 0 else "red"
        coverage = int(getattr(mi, 'analyst_count', 0) or 0)
        pt_breakdown.add_row(
            "BLENDED TARGET (model)", f"[bold]{fmt(avg_target)}[/bold]",
            f"[bold {color}]{arrow_for(avg_upside)} {avg_upside:+.1f}%[/bold {color}]",
            f"Coverage-aware analyst/DCF/technical blend ({coverage} analysts)",
        )
        
        console.print(Panel(pt_breakdown, title="[bold]PRICE TARGET BREAKDOWN[/bold]", border_style="cyan", box=box.ROUNDED))
    else:
        no_targets = Text("No analyst price targets available for this ticker.", style="dim italic")
        console.print(Panel(no_targets, title="[bold]PRICE TARGET BREAKDOWN[/bold]", border_style="dim", box=box.ROUNDED))
    
    # ─── Section 6: Detailed Metrics ───
    if detailed:
        t = result.technical
        f = result.fundamental
        r = result.risk
        
        # Technical Indicators Table
        tech_table = Table(show_header=False, box=box.SIMPLE, expand=True)
        tech_table.add_column("Metric1", style="bold")
        tech_table.add_column("Value1")
        tech_table.add_column("Metric2", style="bold")
        tech_table.add_column("Value2")
        
        tech_table.add_row("RSI (14):", f"{t.rsi:>6.1f} [{'Overbought' if t.rsi > 70 else 'Oversold' if t.rsi < 30 else 'Neutral'}]", "MACD:", f"{t.macd_signal:>10}")
        tech_table.add_row("ADX:", f"{t.adx:>6.1f} [{'Trending' if t.adx > 25 else 'Weak'}]", "Stochastic:", f"{t.stochastic_k:>10.1f}")
        tech_table.add_row("SMA 20/50/200:", f"{'Above all ✓' if t.price_vs_sma20 > 0 and t.price_vs_sma50 > 0 and t.price_vs_sma200 > 0 else 'Mixed'}", "Bollinger:", f"{t.bollinger_signal:>10}")
        vol_str = f"{t.volume_ratio:>6.1f}x avg" if t.volume_ratio >= 0.05 else "   N/A"
        tech_table.add_row("Volume:", vol_str, f"VWAP ({Config.VWAP_WINDOW}d):", f"{fmt(t.vwap):>10}")
        
        console.print(Panel(tech_table, title="[bold]TECHNICAL INDICATORS[/bold]", border_style="blue", box=box.ROUNDED))

        # Fundamental Metrics Table
        fund_table = Table(show_header=False, box=box.SIMPLE, expand=True)
        fund_table.add_column("Metric1", style="bold")
        fund_table.add_column("Value1")
        fund_table.add_column("Metric2", style="bold")
        fund_table.add_column("Value2")
        
        if hasattr(f, 'is_etf') and f.is_etf:
            # ETF Specific Display
            fund_table.add_row("P/E Ratio:", f"{f.pe_ratio:>6.1f}x [{'Fair' if 15 < f.pe_ratio < 30 else 'High' if f.pe_ratio > 30 else 'Low'}]", "Yield:", _format_yield_cell(f.dividend_yield, getattr(f, 'market_cap', 0.0)))
            fund_table.add_row("Net Assets:", f"{fmt(f.net_assets):>10}", "Expense Ratio:", f"{f.expense_ratio*100:>9.2f}%")
            fund_table.add_row("Structure:", f"Exchange Traded Fund (ETF)", "", "")
        else:
            # Standard Stock Display - show N/A for unavailable metrics
            pe_str = f"{f.pe_ratio:>6.1f}x [{'Fair' if 15 < f.pe_ratio < 30 else 'High' if f.pe_ratio > 30 else 'Low'}]" if f.pe_ratio > 0 else "   N/A"
            pb_str = f"{f.pb_ratio:>10.1f}x" if f.pb_ratio > 0 else "       N/A"
            roe_str = f"{f.roe:>6.1f}% [{'Strong' if f.roe > 15 else 'Weak'}]" if f.roe != 0 else "   N/A"
            de_str = f"{f.debt_to_equity:>10.2f}" if f.debt_to_equity > 0 else "       N/A"
            margin_str = f"{f.net_margin:>6.1f}% [{'Healthy' if f.net_margin > 15 else 'Weak'}]" if f.net_margin != 0 else "   N/A"
            altman_str = f"{f.altman_z_score:>10.1f}" if f.altman_z_score > 0 else "       N/A"
            fund_table.add_row("P/E:", pe_str, "P/B:", pb_str)
            fund_table.add_row("ROE:", roe_str, "Debt/Equity:", de_str)
            fund_table.add_row("Margins:", margin_str, "Rev Growth:", f"{f.revenue_growth_yoy:>9.1f}% YoY")
            fund_table.add_row("Piotroski:", f"{piotroski_display_text(f):>18}", "Altman Z:", altman_str)
        
        console.print(Panel(fund_table, title="[bold]FUNDAMENTAL METRICS[/bold]", border_style="green", box=box.ROUNDED))
        
        # Risk Metrics Table
        risk_table = Table(show_header=False, box=box.SIMPLE, expand=True)
        risk_table.add_column("Metric1", style="bold")
        risk_table.add_column("Value1")
        risk_table.add_column("Metric2", style="bold")
        risk_table.add_column("Value2")
        
        risk_table.add_row("Volatility:", f"{r.annualized_volatility:>6.0%} [{'High' if r.annualized_volatility > 0.30 else 'Normal'}]", "Beta:", f"{r.beta:>10.2f}")
        risk_table.add_row("Max Drawdown:", f"{r.max_drawdown:>6.0%} [{'Moderate' if abs(r.max_drawdown) < 0.25 else 'High'}]", "Sharpe:", f"{r.sharpe_ratio:>10.2f}")
        risk_table.add_row("VaR (95%):", f"{r.var_95_daily:>6.2%} [Daily risk]", "Risk Grade:", f"{r.risk_grade:>10}")
        
        console.print(Panel(risk_table, title="[bold]RISK METRICS[/bold]", border_style="red", box=box.ROUNDED))
        
        # Peer Comparison (Skip for ETFs for now as data quality is mixed)
        is_etf = hasattr(f, 'is_etf') and f.is_etf
        if hasattr(result, 'peer_comparison') and result.peer_comparison and not is_etf:
            pc = result.peer_comparison
            peer_table = Table(show_header=False, box=box.SIMPLE, expand=True)
            peer_table.add_column("Metric", style="bold")
            peer_table.add_column("Value")
            
            pe_comp = f"{pc.pe_stock:>6.1f}x vs {pc.pe_sector_avg:>6.1f}x [{pc.pe_vs_sector:+.0f}% {'cheaper' if pc.pe_vs_sector < 0 else 'pricier'}]" if pc.pe_stock > 0 else f"   N/A vs {pc.pe_sector_avg:>6.1f}x [Insufficient data]"
            roe_comp = f"{pc.roe_stock:>6.1f}% vs {pc.roe_sector_avg:>6.1f}% [{abs(pc.roe_vs_sector):+.0f}% {'better' if pc.roe_vs_sector > 0 else 'worse'}]" if pc.roe_stock != 0 else f"   N/A vs {pc.roe_sector_avg:>6.1f}% [Insufficient data]"
            peer_table.add_row("P/E:", pe_comp)
            peer_table.add_row("ROE:", roe_comp)
            if pc.overall_assessment:
                peer_table.add_row("Overall:", f"{pc.overall_assessment}")
                
            console.print(Panel(peer_table, title=f"[bold]PEER COMPARISON (vs {pc.sector} sector)[/bold]", border_style="magenta", box=box.ROUNDED))
    
    # Investment Thesis
    if result.thesis:
        thesis = result.thesis
        thesis_text = Text()
        thesis_text.append(f"{thesis.summary}\n\n", style="bold")
        
        if thesis.bull_case:
            thesis_text.append("Bull Case:\n", style="bold green")
            for point in thesis.bull_case[:3]:
                thesis_text.append(f"  + {point}\n", style="green")
        
        if thesis.bear_case:
            thesis_text.append("\nBear Case:\n", style="bold red")
            for point in thesis.bear_case[:3]:
                thesis_text.append(f"  - {point}\n", style="red")
                
        console.print(Panel(thesis_text, title="[bold]INVESTMENT THESIS[/bold]", border_style="yellow", box=box.ROUNDED))

    # =========================================================================
    # PORTFOLIO ACTION GUIDANCE
    # =========================================================================
    # Ensure variables are available outside the detailed metrics block
    t = result.technical
    f = result.fundamental
    r = result.risk
    
    # Calculate decision thresholds
    if targets: # Use the 'targets' list from the Price Target Breakdown section
        avg_target = sum(t[1] for t in targets) / len(targets)
        upside_to_avg = ((avg_target - current) / current) * 100 if current > 0 else 0

        # Determine action based on multiple factors
        action = result.signal.value.replace('_', ' ')
        # Preserve conviction prefix used in EXECUTIVE SUMMARY / BEGINNER GUIDANCE
        _conv = _conviction_label(result.confidence_score or 50.0)
        if _conv and _conv not in action:
            action = f"{_conv} {action}"

        # Bottom line: short, decisive, beginner-friendly.
        if g["buy_now"] == "NO":
            bottom_line = ("Existing holders can hold with a strict stop. "
                           "New buyers should wait for pullback or breakout confirmation.")
        elif g["buy_now"] == "YES":
            bottom_line = ("Setup is favorable. Starter entries acceptable with disciplined stops; "
                           "existing holders may add on confirmation.")
        elif g["buy_now"] == "ONLY AFTER CONFIRMATION":
            bottom_line = ("Wait for confirmation candle and volume before adding. "
                           "Existing holders may hold with a trailing stop.")
        else:
            bottom_line = ("Mixed signals — defer new buying until a clearer setup emerges.")

        outlook_text = Text()
        outlook_text.append(f"RECOMMENDATION: {action}\n", style="bold cyan")
        outlook_text.append(f"Bottom Line: {bottom_line}\n")
        outlook_text.append(f"Reason: {g['main_reason']}\n", style="dim")

        console.print(Panel(outlook_text, title="[bold]PORTFOLIO OUTLOOK[/bold]", border_style="cyan", box=box.DOUBLE))


def display_position_analysis(result: 'AnalysisResult', shares: int, avg_cost: float, 
                               sym: str = "$", market_env: Dict[str, Any] = None) -> None:
    """Display Elite Portfolio Manager personalized position analysis with macro awareness"""
    
    current = result.current_price
    signal = result.signal
    confidence = result.confidence_score
    
    # =========================================================================
    # CALCULATE POSITION METRICS
    # =========================================================================
    market_value = shares * current
    cost_basis = shares * avg_cost
    unrealized_pnl = market_value - cost_basis
    pnl_pct = ((current - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0
    
    # Key price levels
    stop_loss = result.trade_setup.stop_loss if result.trade_setup and result.trade_setup.stop_loss else current * 0.92
    target = result.market_intel.target_mean if result.market_intel and result.market_intel.target_mean > 0 else current * 1.10
    target_upside = ((target - current) / current) * 100
    
    # Risk metrics
    risk_score = result.risk.overall_risk_score if result.risk else 5
    risk_grade = result.risk.risk_grade if result.risk else "C"
    
    # =========================================================================
    # MACRO ENVIRONMENT CONTEXT
    # =========================================================================
    macro_risk_score = 5
    fed_outlook = "NEUTRAL"
    has_mega_earnings = False
    macro_warnings = []
    
    if market_env:
        macro_risk_score = market_env.get('macro_risk_score', 5)
        fed_outlook_str = market_env.get('fed_outlook', '')
        if 'HAWKISH' in fed_outlook_str.upper():
            fed_outlook = "HAWKISH"
        elif 'DOVISH' in fed_outlook_str.upper():
            fed_outlook = "DOVISH"
        else:
            fed_outlook = "NEUTRAL"
        
        has_mega_earnings = market_env.get('high_impact_earnings', False)
        macro_warnings = market_env.get('macro_risk_factors', [])
        
        # Add mega-cap earnings warning
        if has_mega_earnings:
            mega_warning = market_env.get('mega_earnings_warning', '')
            if mega_warning:
                macro_warnings.insert(0, mega_warning)
    
    # =========================================================================
    # MACRO-ADJUSTED OUTLOOK
    # =========================================================================
    # Determine base outlook from signal
    if signal in [Signal.STRONG_BUY, Signal.BUY, Signal.ACCUMULATE]:
        base_outlook = "BULLISH"
        outlook_emoji = "🟢"
    elif signal in [Signal.SELL, Signal.STRONG_SELL]:
        base_outlook = "BEARISH"
        outlook_emoji = "🔴"
    else:
        base_outlook = "NEUTRAL"
        outlook_emoji = "🟡"
    
    # Adjust outlook based on macro environment
    outlook = base_outlook
    macro_adjustment = ""
    
    if macro_risk_score >= 8:
        # High macro risk - be more defensive
        if base_outlook == "BULLISH":
            outlook = "NEUTRAL"  # Downgrade bullish to neutral
            macro_adjustment = " (macro headwinds)"
            outlook_emoji = "🟡"
    elif fed_outlook == "HAWKISH" and base_outlook == "BULLISH":
        # Hawkish Fed environment - reduce bullish conviction for growth stocks
        if result.sector in ['Technology', 'Consumer Discretionary', 'Real Estate']:
            macro_adjustment = " (rate-sensitive sector)"
    elif fed_outlook == "DOVISH" and base_outlook == "NEUTRAL":
        # Dovish Fed - more supportive for equities
        macro_adjustment = " (Fed supportive)"
    
    pnl_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
    
    # =========================================================================
    # POSITION SUMMARY
    # =========================================================================
    print(f"\n\n  ═══════════════════════════════════════════════════════════════════════════")
    print(f"                     ELITE PORTFOLIO MANAGER")
    print(f"  ═══════════════════════════════════════════════════════════════════════════")
    
    print(f"\n  ┌─────────────────────────────────────────────────────────────────────────┐")
    print(f"  │  POSITION:  {shares:,} shares @ {sym}{avg_cost:,.2f}                                   │")
    print(f"  │  VALUE:     {sym}{market_value:,.2f}   P/L: {pnl_emoji} {sym}{unrealized_pnl:+,.2f} ({pnl_pct:+.1f}%)              │")
    print(f"  │  OUTLOOK:   {outlook_emoji} {outlook:<10}{macro_adjustment:<20}                    │")
    print(f"  │  CONFIDENCE: {confidence:.0f}%    RISK: {risk_grade}    MACRO RISK: {macro_risk_score}/10              │")
    print(f"  └─────────────────────────────────────────────────────────────────────────┘")
    
    # =========================================================================
    # MACRO WARNINGS (if any)
    # =========================================================================
    if macro_warnings:
        print(f"\n  ⚠️  MACRO FACTORS AFFECTING YOUR POSITION")
        print(f"  {'─'*72}")
        for warning in macro_warnings[:3]:
            print(f"     • {warning[:70]}")
    
    # =========================================================================
    # DECISION MATRIX: P/L Status × Market Outlook × Macro Context
    # =========================================================================
    # This creates a 3D decision matrix for clearer logic
    
    # Categorize P/L status
    if pnl_pct >= 30:
        pnl_status = "LARGE_GAIN"
    elif pnl_pct >= 10:
        pnl_status = "MODERATE_GAIN"
    elif pnl_pct >= 0:
        pnl_status = "SMALL_GAIN"
    elif pnl_pct >= -10:
        pnl_status = "SMALL_LOSS"
    elif pnl_pct >= -25:
        pnl_status = "MODERATE_LOSS"
    else:
        pnl_status = "LARGE_LOSS"
    
    # =========================================================================
    # VERDICT + ACTION PLAN
    # =========================================================================
    print(f"\n  VERDICT")
    print(f"  {'─'*72}")
    
    # Decision logic based on P/L + Outlook + Target comparison
    # Calculate upside to analyst target
    target_above_current = target > current * 1.05  # 5% buffer for fees/slippage
    analyst_upside_pct = ((target - current) / current) * 100 if current > 0 else 0
    
    if pnl_status == "LARGE_GAIN":
        # 30%+ profit - but check if target still shows upside
        if target_above_current and analyst_upside_pct > 5:
            # Target is significantly above current - consider holding
            if risk_grade in ['A', 'B']:
                # Strong fundamentals + target higher = WAIT
                verdict = "HOLD TO TARGET"
                action_emoji = "🎯"
                print(f"  {action_emoji} {verdict}")
                print(f"     +{pnl_pct:.0f}% gain but analyst target {sym}{target:,.2f} ({analyst_upside_pct:+.1f}%) still ahead.")
                print(f"\n  ACTION PLAN")
                print(f"  {'─'*72}")
                print(f"  ▸ Hold for target       {sym}{target:,.2f} ({analyst_upside_pct:+.1f}%)")
                print(f"  ▸ Raise stop to         {sym}{avg_cost * 1.20:,.2f} (lock +20% profit)")
                print(f"  ▸ Time limit            Review in 30 days if target not reached")
            else:
                # Good target but risky - partial take
                verdict = "PARTIAL PROFITS + WAIT"
                action_emoji = "💵"
                print(f"  {action_emoji} {verdict}")
                print(f"     +{pnl_pct:.0f}% gain. Target is {analyst_upside_pct:+.1f}% ahead but Risk Grade {risk_grade} warrants caution.")
                print(f"\n  ACTION PLAN")
                print(f"  {'─'*72}")
                print(f"  ▸ Sell 33% now          {shares//3:,} shares → {sym}{(shares//3) * current:,.2f}")
                print(f"  ▸ Hold rest to target   {sym}{target:,.2f} ({analyst_upside_pct:+.1f}%)")
                print(f"  ▸ Trailing stop         {sym}{current * 0.92:,.2f} on remainder")
        else:
            # Target exhausted or below current - BOOK PROFITS
            verdict = "BOOK PROFITS"
            action_emoji = "💰"
            print(f"  {action_emoji} {verdict}")
            if analyst_upside_pct <= 0:
                print(f"     +{pnl_pct:.0f}% gain and target ({sym}{target:,.2f}) already reached. Take profits.")
            else:
                print(f"     +{pnl_pct:.0f}% unrealized gains. Protect your capital.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            print(f"  ▸ Sell 50% now         {shares//2:,} shares → {sym}{(shares//2) * current:,.2f}")
            print(f"  ▸ Trailing stop        {sym}{current * 0.90:,.2f} on remainder")
            if outlook == "BULLISH":
                print(f"  ▸ Re-enter on dip      If drops to {sym}{current * 0.85:,.2f}")
        
    elif pnl_status == "MODERATE_GAIN":
        # 10-30% profit
        if outlook == "BULLISH":
            verdict = "HOLD WITH PROTECTION"
            action_emoji = "✅"
            print(f"  {action_emoji} {verdict}")
            print(f"     +{pnl_pct:.0f}% gain with bullish outlook. Let profits run.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            print(f"  ▸ Hold position        {shares:,} shares")
            print(f"  ▸ Set trailing stop    {sym}{avg_cost * 1.05:,.2f} (lock +5%)")
            print(f"  ▸ Target               {sym}{target:,.2f} ({target_upside:+.1f}%)")
        else:
            verdict = "TAKE PARTIAL PROFITS"
            action_emoji = "💵"
            print(f"  {action_emoji} {verdict}")
            print(f"     +{pnl_pct:.0f}% gain but outlook neutral/bearish. De-risk.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            print(f"  ▸ Sell 33-50%          {shares//3:,} to {shares//2:,} shares")
            print(f"  ▸ Protect stop         {sym}{avg_cost:,.2f} (breakeven)")
            
    elif pnl_status == "SMALL_GAIN":
        # 0-10% profit
        if outlook == "BULLISH":
            verdict = "ACCUMULATE"
            action_emoji = "📈"
            print(f"  {action_emoji} {verdict}")
            print(f"     Small profit with bullish outlook. Consider adding.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            add_amt = shares // 2
            new_avg = ((shares * avg_cost) + (add_amt * current)) / (shares + add_amt) if add_amt > 0 else avg_cost
            print(f"  ▸ Add on dip to        {sym}{current * 0.97:,.2f}")
            print(f"  ▸ Stop-loss            {sym}{stop_loss:,.2f}")
            print(f"  ▸ Target               {sym}{target:,.2f} ({target_upside:+.1f}%)")
        else:
            verdict = "HOLD - SET STOPS"
            action_emoji = "⏸️"
            print(f"  {action_emoji} {verdict}")
            print(f"     Small profit, neutral outlook. Protect capital.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            print(f"  ▸ Hold position        {shares:,} shares")
            print(f"  ▸ Stop-loss            {sym}{avg_cost * 0.95:,.2f}")
            print(f"  ▸ Exit trigger         Below {sym}{stop_loss:,.2f}")
            
    elif pnl_status == "SMALL_LOSS":
        # -10% to 0% loss
        if outlook == "BULLISH":
            # Risk Check: Don't average down on high-risk assets blindly
            if risk_grade in ['D', 'F']:
                verdict = "HOLD - HIGH RISK"
                action_emoji = "⚠️"
                print(f"  {action_emoji} {verdict}")
                print(f"     {pnl_pct:+.1f}% loss. Outlook bullish but Risk Grade is {risk_grade}.")
                print(f"\n  ACTION PLAN")
                print(f"  {'─'*72}")
                print(f"  ▸ Hold                 Do not add to high-risk position")
                print(f"  ▸ Tight Stop           {sym}{avg_cost * 0.90:,.2f}")
                print(f"  ▸ Exit on rally to     {sym}{avg_cost * 1.05:,.2f}")
            else:
                verdict = "AVERAGE DOWN"
                action_emoji = "📉"
                print(f"  {action_emoji} {verdict}")
                print(f"     {pnl_pct:+.1f}% but outlook bullish. Improve cost basis.")
                print(f"\n  ACTION PLAN")
                print(f"  {'─'*72}")
                add_amt = shares
                new_avg = ((shares * avg_cost) + (add_amt * current)) / (shares + add_amt)
                print(f"  ▸ Add shares           {add_amt:,} at {sym}{current:,.2f}")
                print(f"  ▸ New avg cost         {sym}{new_avg:,.2f}")
                print(f"  ▸ Breakeven at         {sym}{avg_cost:,.2f} (+{((avg_cost - current) / current) * 100:.1f}%)")
                print(f"  ▸ Hard stop            {sym}{new_avg * 0.88:,.2f}")
        else:
            verdict = "HOLD - DEFENSIVE"
            action_emoji = "⚠️"
            print(f"  {action_emoji} {verdict}")
            print(f"     {pnl_pct:+.1f}% loss, neutral outlook. Don't add.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            print(f"  ▸ Hold                 Do not add to position")
            print(f"  ▸ Stop-loss            {sym}{avg_cost * 0.85:,.2f}")
            print(f"  ▸ Exit on rally to     {sym}{avg_cost * 0.98:,.2f}")
            
    elif pnl_status == "MODERATE_LOSS":
        # -25% to -10% loss
        if outlook == "BULLISH":
            if risk_grade in ['D', 'F']:
                verdict = "HOLD - SPECULATIVE RECOVERY"
                action_emoji = "🎲"
                print(f"  {action_emoji} {verdict}")
                print(f"     {pnl_pct:+.1f}% loss. Bullish signal but High Risk ({risk_grade}).")
                print(f"\n  ACTION PLAN")
                print(f"  {'─'*72}")
                print(f"  ▸ Hold                 Wait for recovery, do not add capital")
                print(f"  ▸ Exit Target          {sym}{avg_cost * 0.95:,.2f} (cut loss on rally)")
                print(f"  ▸ Hard stop            {sym}{current * 0.85:,.2f}")
            else:
                verdict = "EVALUATE RECOVERY"
                action_emoji = "🔄"
                print(f"  {action_emoji} {verdict}")
                print(f"     {pnl_pct:+.1f}% loss but outlook improving.")
                print(f"\n  ACTION PLAN")
                print(f"  {'─'*72}")
                print(f"  ▸ Option A: Average    Add equal shares to lower basis")
                add_amt = shares
                new_avg = ((shares * avg_cost) + (add_amt * current)) / (shares + add_amt)
                print(f"                         New avg: {sym}{new_avg:,.2f}")
                print(f"  ▸ Option B: Hold       Wait for recovery to {sym}{avg_cost * 0.90:,.2f}")
                print(f"  ▸ Hard stop            {sym}{current * 0.85:,.2f}")
        else:
            verdict = "CUT LOSSES"
            action_emoji = "🛑"
            print(f"  {action_emoji} {verdict}")
            print(f"     {pnl_pct:+.1f}% loss + weak outlook. Protect capital.")
            print(f"\n  ACTION PLAN")
            print(f"  {'─'*72}")
            print(f"  ▸ Sell 50-100%         Exit at current {sym}{current:,.2f}")
            print(f"  ▸ Realized loss        {sym}{unrealized_pnl:,.2f}")
            print(f"  ▸ Reallocate to        Stronger opportunities")
            
    else:  # LARGE_LOSS (> 25%)
        verdict = "CRITICAL REVIEW"
        action_emoji = "🚨"
        print(f"  {action_emoji} {verdict}")
        print(f"     {pnl_pct:+.1f}% loss. Thesis may be broken.")
        print(f"\n  ACTION PLAN")
        print(f"  {'─'*72}")
        if outlook == "BULLISH" and risk_grade not in ['D', 'F']:
            print(f"  ▸ Analysis shows       Bullish outlook (contrarian play?)")
            print(f"  ▸ If high conviction   Consider averaging down (Aggressive)")
            print(f"  ▸ If uncertain         Exit and re-evaluate")
        else:
            print(f"  ▸ Recommendation       Exit position")
            print(f"  ▸ Realized loss        {sym}{unrealized_pnl:,.2f}")
            print(f"  ▸ Tax benefit          Use loss to offset gains")
    
    # =========================================================================
    # KEY LEVELS
    # =========================================================================
    print(f"\n  KEY LEVELS")
    print(f"  {'─'*72}")
    print(f"  Your Cost:      {sym}{avg_cost:,.2f}")
    print(f"  Stop-Loss:      {sym}{stop_loss:,.2f}  ({((stop_loss - current) / current) * 100:+.1f}%)")
    print(f"  Analyst Target: {sym}{target:,.2f}  ({target_upside:+.1f}%)")


# █████████████████████████████████████████████████████████████████████████████
# ███  MCP (MODEL CONTEXT PROTOCOL) INTEGRATION                            ███
# ███  Exposes QUANTA Elite as AI-callable tools for VS Code Agent Mode    ███
# █████████████████████████████████████████████████████████████████████████████

# ─── MCP Globals (lazy-initialized, thread-safe) ────────────────────────────
import threading

_mcp_lock = threading.Lock()
_mcp_analyzer: Optional[QuantaEliteAnalyzer] = None
_mcp_api_client: Optional[APIClient] = None
_mcp_config: Optional[Dict[str, str]] = None


def _mcp_get_config() -> Dict[str, str]:
    """Load API configuration from environment or defaults."""
    global _mcp_config
    if _mcp_config is None:
        _mcp_config = {
            'news_api_key': os.getenv('NEWS_API_KEY', ''),
            'finnhub_key': os.getenv('FINNHUB_API_KEY', ''),
            'fmp_key': os.getenv('FMP_API_KEY', ''),
            'polygon_key': os.getenv('POLYGON_API_KEY', ''),
            'alpha_vantage_key': os.getenv('ALPHA_VANTAGE_KEY', ''),
        }
    return _mcp_config


def _mcp_get_analyzer() -> QuantaEliteAnalyzer:
    """Lazy-initialize the QUANTA analyzer for MCP tools (thread-safe)."""
    global _mcp_analyzer
    if _mcp_analyzer is None:
        with _mcp_lock:
            if _mcp_analyzer is None:  # Double-check after acquiring lock
                logger.info("[MCP] Initializing QUANTA Elite Analyzer...")
                _mcp_analyzer = QuantaEliteAnalyzer(_mcp_get_config())
                logger.info("[MCP] QUANTA Elite Analyzer ready")
    return _mcp_analyzer


def _mcp_get_api_client() -> APIClient:
    """Lazy-initialize the API client for MCP tools (thread-safe)."""
    global _mcp_api_client
    if _mcp_api_client is None:
        with _mcp_lock:
            if _mcp_api_client is None:  # Double-check after acquiring lock
                _mcp_api_client = APIClient(_mcp_get_config())
    return _mcp_api_client


# ─── Serialization Helpers ───────────────────────────────────────────────────

def _mcp_safe_serialize(obj: Any) -> Any:
    """Recursively convert dataclass/enum/float objects to JSON-safe dicts."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return None
        if abs(obj) == float('inf'):
            return None
        return round(obj, 4)
    if isinstance(obj, (list, tuple)):
        return [_mcp_safe_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _mcp_safe_serialize(v) for k, v in obj.items()}
    if hasattr(obj, '__dataclass_fields__'):
        return {k: _mcp_safe_serialize(v) for k, v in asdict(obj).items()}
    if hasattr(obj, 'value'):  # Enum
        return obj.value
    return str(obj)


def classify_asset_type(
    *,
    is_invalid: bool = False,
    is_etf: bool = False,
    quote_type: str = "EQUITY",
    sector: str = "",
    industry: str = "",
    country: str = "",
    company_name: str = "",
    ticker: str = "",
    annualized_volatility: Optional[float] = None,
    beta: Optional[float] = None,
) -> Dict[str, Any]:
    """Classify a security into a structural asset type + volatility overlay.

    Pure / side-effect free so it can be unit-tested in isolation.

    Returns a dict:
      {asset_type, is_adr, is_financial, is_high_volatility, country}

    Precedence for the primary `asset_type` is structural:
        invalid > etf > adr > financial > common_stock
    `is_high_volatility` is an INDEPENDENT overlay (a financial or ADR can
    also be high-volatility) so structural information is never lost.
    """
    qt = str(quote_type or "").strip().upper()
    sec = str(sector or "").strip().lower()
    ind = str(industry or "").strip().lower()
    ctry = str(country or "").strip()
    name = str(company_name or "").lower()

    # --- ETF / fund detection ---
    etf = bool(is_etf) or qt in ("ETF", "MUTUALFUND", "INDEX", "CLOSED-END-FUND", "CEF")

    # --- ADR / foreign-issuer detection (equities only) ---
    # yfinance does not flag ADRs explicitly; infer from a non-US issuer
    # country or an explicit "ADR / American Depositary" name marker.
    adr = False
    if not etf:
        name_marks_adr = (
            "american depositary" in name
            or "depositary receipt" in name
            or name.endswith(" adr")
            or " adr " in name
        )
        non_us_country = bool(ctry) and ctry.lower() not in (
            "united states", "usa", "us", "u.s.", "united states of america",
        )
        # A locally listed foreign share is not a US depositary receipt.
        # Preserve the existing US-symbol inference, but exclude explicit
        # international exchange suffixes (.NS, .BO, .L, .TO, ...).
        local_foreign_listing = bool(re.search(r"\.[A-Z]{1,3}$", str(ticker).upper()))
        adr = name_marks_adr or (non_us_country and not local_foreign_listing)

    # --- Financial-sector detection (equities only) ---
    financial = False
    if not etf:
        financial = (
            "financ" in sec  # "Financial Services" / "Financials"
            or sec in ("banks", "banking", "insurance", "capital markets")
            or any(tok in ind for tok in (
                "bank", "insurance", "capital market", "asset management",
                "credit service", "financial",
            ))
        )

    # --- High-volatility overlay (structural-agnostic) ---
    high_vol = False
    try:
        vol = float(annualized_volatility) if annualized_volatility is not None else None
    except (TypeError, ValueError):
        vol = None
    try:
        bta = float(beta) if beta is not None else None
    except (TypeError, ValueError):
        bta = None
    # Volatility may arrive as a fraction (0.55) or a percent (55.0); normalize.
    if vol is not None:
        vol_frac = vol / 100.0 if vol > 3.0 else vol
        if vol_frac >= 0.50:
            high_vol = True
    if bta is not None and bta >= 1.8:
        high_vol = True

    # --- Resolve primary structural type ---
    if is_invalid:
        asset_type = "invalid"
    elif etf:
        asset_type = "etf"
    elif adr:
        asset_type = "adr"
    elif financial:
        asset_type = "financial"
    else:
        asset_type = "common_stock"

    return {
        "asset_type": asset_type,
        "is_adr": bool(adr),
        "is_financial": bool(financial),
        "is_high_volatility": bool(high_vol),
        "country": ctry,
    }


def _mcp_result_to_dict(result: AnalysisResult) -> Dict[str, Any]:
    """Convert a full AnalysisResult to a serializable dictionary."""
    from engine.evidence import ELITE, get_evidence

    d = {}
    decision = decision_for_analysis(result)
    decision_data = result.decision or (decision.to_dict() if decision else None)
    d['decision'] = decision_data
    if getattr(result, 'portfolio_fund_decision', None) is not None:
        d['portfolio_fund_decision'] = result.portfolio_fund_decision
    evidence = decision.evidence if decision else get_evidence(ELITE)
    d['evidence'] = evidence.to_dict()

    # Basic info
    d['ticker'] = result.ticker
    d['company_name'] = result.company_name
    d['sector'] = result.sector
    d['industry'] = result.industry
    d['currency'] = result.currency
    d['currency_symbol'] = result.currency_symbol
    d['exchange'] = getattr(result, 'exchange', 'UNKNOWN')
    d['data_provider'] = getattr(result, 'data_provider', 'yfinance')
    d['retrieval_timestamp'] = getattr(result, 'retrieval_timestamp', '')
    d['market_timestamp'] = getattr(result, 'market_timestamp', '')
    d['adjustment_status'] = getattr(result, 'adjustment_status', '')
    d['data_quality_status'] = getattr(result, 'data_quality_status', 'unknown')

    # Price
    d['current_price'] = _mcp_safe_serialize(result.current_price)
    d['previous_close'] = _mcp_safe_serialize(result.previous_close)
    d['day_change'] = _mcp_safe_serialize(result.day_change)
    d['day_change_pct'] = _mcp_safe_serialize(result.day_change_pct)
    d['market_cap'] = _mcp_safe_serialize(result.market_cap)

    # Extended hours
    d['pre_market_price'] = _mcp_safe_serialize(result.pre_market_price)
    d['pre_market_change_pct'] = _mcp_safe_serialize(result.pre_market_change_pct)
    d['after_hours_price'] = _mcp_safe_serialize(result.after_hours_price)
    d['after_hours_change_pct'] = _mcp_safe_serialize(result.after_hours_change_pct)
    d['market_state'] = result.market_state

    # Signal
    d['signal'] = result.signal.value if hasattr(result.signal, 'value') else str(result.signal)
    d['confidence_score'] = _mcp_safe_serialize(result.confidence_score)
    d['confidence_level'] = result.confidence_level.value if hasattr(result.confidence_level, 'value') else str(result.confidence_level)
    d['market_regime'] = result.market_regime.value if hasattr(result.market_regime, 'value') else str(result.market_regime)

    # [F02] Pre-trade portfolio exposure verdict. The UI needs `holdings_known`
    # to tell a passed check from an unverifiable one, so this must not stay
    # trapped inside the AnalysisResult.
    d['portfolio_risk_check'] = _mcp_safe_serialize(
        getattr(result, 'portfolio_risk_check', {}) or {}
    )

    # Engine-level authoritative recommendation (FinalAction) + ETF / validation flags
    d['final_action'] = decision_data['action'] if decision_data else ''
    d['is_invalid'] = bool(getattr(result, 'is_invalid', False))
    d['has_existing_position'] = bool(getattr(result, 'has_existing_position', False))
    d['is_etf'] = bool(getattr(result, 'is_etf', False))
    d['quote_type'] = getattr(result, 'quote_type', 'EQUITY') or 'EQUITY'
    d['net_assets'] = _mcp_safe_serialize(getattr(result, 'net_assets', 0.0))
    d['expense_ratio'] = _mcp_safe_serialize(getattr(result, 'expense_ratio', 0.0))

    # [ASSET-TYPE] Structural classification + overlays for conditional UI.
    d['asset_type'] = getattr(result, 'asset_type', '') or (
        'invalid' if bool(getattr(result, 'is_invalid', False))
        else ('etf' if bool(getattr(result, 'is_etf', False)) else 'common_stock')
    )
    d['is_adr'] = bool(getattr(result, 'is_adr', False))
    d['is_financial'] = bool(getattr(result, 'is_financial', False))
    d['is_high_volatility'] = bool(getattr(result, 'is_high_volatility', False))
    d['country'] = getattr(result, 'country', '') or ''

    # [PROFILING] Per-stage timing table for the deep-dive flow.
    d['stage_timings'] = list(getattr(result, 'stage_timings', []) or [])

    # [PERF-FASTMODE] Flag a fast-mode (preliminary) tear sheet so the UI can
    # show the "preliminary — load full analysis" banner. The optional modules
    # below were skipped, so their cards must be rendered as "not loaded".
    d['fast_mode'] = bool(getattr(result, 'fast_mode', False))

    # Scores
    if result.scores:
        d['scores'] = _mcp_safe_serialize(result.scores)

    # Technical summary
    if result.technical:
        t = result.technical
        d['technical'] = {
            'rsi': _mcp_safe_serialize(t.rsi),
            'macd_signal': t.macd_signal,
            'adx': _mcp_safe_serialize(t.adx),
            'trend_direction': t.trend_direction,
            'trend_strength': t.trend_strength,
            'golden_cross': t.golden_cross,
            'death_cross': t.death_cross,
            'bollinger_signal': t.bollinger_signal,
            'volume_ratio': _mcp_safe_serialize(t.volume_ratio),
            'squeeze_detected': t.squeeze_detected,
            'support_level': _mcp_safe_serialize(t.support_level),
            'resistance_level': _mcp_safe_serialize(t.resistance_level),
            # The fact shown is the provider's traded 52-week range when it
            # reports one; otherwise the engine's own (dividend-adjusted).
            'week_52_high': _mcp_safe_serialize(
                getattr(result, 'week_52_high_reported', 0) or t.week_52_high),
            'week_52_low': _mcp_safe_serialize(
                getattr(result, 'week_52_low_reported', 0) or t.week_52_low),
            'week_52_basis': ('provider-reported traded range'
                              if getattr(result, 'week_52_high_reported', 0)
                              else 'daily highs/lows, dividend-adjusted'),
            'momentum_score': _mcp_safe_serialize(t.momentum_score),
            'trend_score': _mcp_safe_serialize(t.trend_score),
            'volatility_score': _mcp_safe_serialize(t.volatility_score),
        }

    # Fundamental summary
    if result.fundamental:
        f = result.fundamental
        d['fundamental'] = {
            'pe_ratio': _mcp_safe_serialize(f.pe_ratio),
            'forward_pe': _mcp_safe_serialize(f.forward_pe),
            'peg_ratio': _mcp_safe_serialize(f.pe_to_growth),
            'pb_ratio': _mcp_safe_serialize(f.pb_ratio),
            'ev_ebitda': _mcp_safe_serialize(f.ev_ebitda),
            'roe': (_mcp_safe_serialize(f.roe) if f.has('roe') else None),
            'roa': _mcp_safe_serialize(f.roa),
            'roic': _mcp_safe_serialize(f.roic),
            'gross_margin': _mcp_safe_serialize(f.gross_margin),
            'operating_margin': _mcp_safe_serialize(f.operating_margin),
            'net_margin': (_mcp_safe_serialize(f.net_margin) if f.has('net_margin') else None),
            'revenue_growth_yoy': (_mcp_safe_serialize(f.revenue_growth_yoy) if f.has('revenue_growth') else None),
            'eps_growth_yoy': _mcp_safe_serialize(f.eps_growth_yoy),
            'debt_to_equity': (_mcp_safe_serialize(f.debt_to_equity) if f.has('debt_to_equity') else None),
            'current_ratio': (_mcp_safe_serialize(f.current_ratio) if f.has('current_ratio') else None),
            'free_cash_flow': _mcp_safe_serialize(f.free_cash_flow),
            'fcf_yield': _mcp_safe_serialize(f.fcf_yield),
            'dividend_yield': _mcp_safe_serialize(f.dividend_yield),
            # None (→ "N/A" in the UI) when too few inputs were reported.
            'piotroski_f_score': piotroski_display_value(
                f.piotroski_f_score, getattr(f, 'piotroski_components_available', 9)),
            'piotroski_components_available': int(getattr(f, 'piotroski_components_available', 0) or 0),
            'altman_z_score': (_mcp_safe_serialize(f.altman_z_score)
                               if getattr(f, 'altman_z_status', 'ok') == 'ok' else None),
            'altman_z_status': getattr(f, 'altman_z_status', 'unavailable'),
            'altman_z_variant': getattr(f, 'altman_z_variant', ''),
            # Provider coverage of the scored inputs; an unreported input is null above, not 0.
            'reported_inputs': dict(f.reported),
            'quality_score': _mcp_safe_serialize(f.quality_score),
            'is_etf': f.is_etf,
        }

    # Risk
    if result.risk:
        r = result.risk
        d['risk'] = {
            'annualized_volatility': _mcp_safe_serialize(r.annualized_volatility),
            'grade_drivers': [dict(x) for x in (getattr(r, 'grade_drivers', []) or [])],
            'risk_points': int(getattr(r, 'overall_risk_score', 0) or 0),
            'beta': (_mcp_safe_serialize(r.beta)
                     if getattr(r, 'beta_status', 'unavailable') == 'measured' else None),
            'beta_status': getattr(r, 'beta_status', 'unavailable'),
            'beta_benchmark': getattr(r, 'beta_benchmark', ''),
            'beta_observations': int(getattr(r, 'beta_observations', 0) or 0),
            'sharpe_ratio': _mcp_safe_serialize(r.sharpe_ratio),
            'sortino_ratio': _mcp_safe_serialize(r.sortino_ratio),
            'max_drawdown': _mcp_safe_serialize(r.max_drawdown),
            'var_95_daily': _mcp_safe_serialize(r.var_95_daily),
            'cvar_95': _mcp_safe_serialize(r.cvar_95),
            'overall_risk_score': r.overall_risk_score,
            'risk_grade': r.risk_grade,
            'liquidity_score': _mcp_safe_serialize(r.liquidity_score),
        }

    # Sentiment
    if result.sentiment:
        s = result.sentiment
        d['sentiment'] = {
            'sentiment_score': _mcp_safe_serialize(s.sentiment_score),
            'sentiment_label': s.sentiment_label,
            'sentiment_trend': s.sentiment_trend,
            'news_volume': s.news_volume,
            'positive_articles': s.positive_articles,
            'negative_articles': s.negative_articles,
            'key_headlines': s.key_headlines[:5] if s.key_headlines else [],
            # Why the reading is what it is. Without this a NEUTRAL score is
            # ambiguous between "measured neutral" and "nothing was measured".
            'status': getattr(s, 'status', 'NO_NEWS'),
            'measured': bool(getattr(s, 'measured', False)),
            'sources_attempted': int(getattr(s, 'sources_attempted', 0) or 0),
            'sources_succeeded': int(getattr(s, 'sources_succeeded', 0) or 0),
        }

    # Market intelligence
    if result.market_intel:
        mi = result.market_intel
        d['market_intel'] = {
            'analyst_rating': mi.analyst_rating,
            'buy_ratings': mi.buy_ratings,
            'hold_ratings': mi.hold_ratings,
            'sell_ratings': mi.sell_ratings,
            'target_low': _mcp_safe_serialize(mi.target_low),
            'target_mean': _mcp_safe_serialize(mi.target_mean),
            'target_median': _mcp_safe_serialize(mi.target_median),
            'target_high': _mcp_safe_serialize(mi.target_high),
            'implied_upside': _mcp_safe_serialize(mi.implied_upside),
            'analyst_count': int(getattr(mi, 'analyst_count', 0) or 0),
            'next_earnings_date': mi.next_earnings_date,
        }

    # Valuation
    if result.valuation:
        v = result.valuation
        d['valuation'] = {
            'dcf_fair_value': _mcp_safe_serialize(v.dcf_fair_value),
            'dcf_upside': _mcp_safe_serialize(v.dcf_upside),
            'dcf_status': getattr(v, 'dcf_status', 'unavailable'),
            'dcf_method': getattr(v, 'dcf_method', ''),
            'graham_number': _mcp_safe_serialize(v.graham_number),
            'graham_status': getattr(v, 'graham_status', 'unavailable'),
            'valuation_label': v.valuation_label,
            'valuation_status': getattr(v, 'valuation_status', 'unavailable'),
            'fair_value_sources': list(getattr(v, 'fair_value_sources', []) or []),
            'target_components': [dict(c) for c in (getattr(v, 'target_components', []) or [])],
            'valuation_score': _mcp_safe_serialize(v.valuation_score),
            'target_conservative': _mcp_safe_serialize(v.target_conservative),
            'target_base': _mcp_safe_serialize(v.target_base),
            'target_aggressive': _mcp_safe_serialize(v.target_aggressive),
            'target_confidence': _mcp_safe_serialize(v.target_confidence),
            'target_probability': _mcp_safe_serialize(v.target_probability),
            # How many target sources contributed and how closely they agreed,
            # instead of a score that rose purely with the source count.
            'target_agreement_pct': _mcp_safe_serialize(v.target_agreement_pct),
            'target_source_count': int(v.target_source_count or 0),
            'target_sources': list(v.target_sources or []),
        }

    # Monte Carlo
    if result.monte_carlo:
        mc = result.monte_carlo
        d['monte_carlo'] = {
            'simulations_run': mc.simulations_run,
            'median_price': _mcp_safe_serialize(mc.median_price),
            'mean_price': _mcp_safe_serialize(mc.mean_price),
            'percentile_5': _mcp_safe_serialize(mc.percentile_5),
            'percentile_25': _mcp_safe_serialize(mc.percentile_25),
            'percentile_75': _mcp_safe_serialize(mc.percentile_75),
            'percentile_95': _mcp_safe_serialize(mc.percentile_95),
            'probability_profit': _mcp_safe_serialize(mc.probability_profit),
            'probability_10pct_gain': _mcp_safe_serialize(mc.probability_10pct_gain),
            'probability_10pct_loss': _mcp_safe_serialize(mc.probability_10pct_loss),
            # Mean terminal return (percent). The median is `median_price`;
            # for a skewed distribution the two can differ in sign.
            'expected_return': _mcp_safe_serialize(mc.expected_return),
            'expected_return_basis': 'mean',
            'var_95': _mcp_safe_serialize(mc.var_95),
            # The horizon and the exact event every probability above refers
            # to, so "62%" cannot be read as a chance of the trade working.
            'days_simulated': int(mc.days_simulated or 0),
            'probability_event': monte_carlo_event_label(mc),
        }

    # Trade setup
    if result.trade_setup:
        ts = result.trade_setup
        d['trade_setup'] = {
            'entry_price': _mcp_safe_serialize(ts.entry_price),
            'stop_loss': _mcp_safe_serialize(ts.stop_loss),
            'target_conservative': _mcp_safe_serialize(ts.target_conservative),
            'target_base': _mcp_safe_serialize(ts.target_base),
            'target_aggressive': _mcp_safe_serialize(ts.target_aggressive),
            'risk_reward_ratio': _mcp_safe_serialize(ts.risk_reward_ratio),
            'win_probability': _mcp_safe_serialize(ts.win_probability),
            'time_horizon': ts.time_horizon,
            # Setup provenance so the UI can say WHY R:R is zero instead of
            # recomputing one from a different target.
            'has_long_reward': bool(getattr(ts, 'has_long_reward', True)),
            'levels_available': bool(getattr(ts, 'levels_available', False)),
            'is_actionable_entry': bool(getattr(ts, 'is_actionable_entry', False)),
            'entry_type': getattr(ts, 'entry_type', ''),
            'setup_basis': getattr(ts, 'setup_basis', ''),
            'trailing_stop': _mcp_safe_serialize(getattr(ts, 'trailing_stop', 0.0)),
            'trailing_stop_pct': _mcp_safe_serialize(getattr(ts, 'trailing_stop_pct', 0.0)),
        }

    # Position sizing
    if result.position_sizing:
        ps = result.position_sizing
        allows_allocation = bool(
            decision_data
            and str(decision_data.get('action') or '') in {
                FinalAction.STRONG_BUY.value,
                FinalAction.BUY.value,
                FinalAction.ACCUMULATE.value,
            }
        )
        d['position_sizing'] = {
            'recommended_shares': ps.recommended_shares if allows_allocation else 0,
            'position_value': _mcp_safe_serialize(ps.position_value if allows_allocation else 0.0),
            'portfolio_weight': _mcp_safe_serialize(ps.portfolio_weight if allows_allocation else 0.0),
            'kelly_optimal': 0.0,
            'is_viable': bool(ps.is_viable and allows_allocation),
            'sizing_method': ps.sizing_method,
        }

    # Insider
    if result.insider:
        ins = result.insider
        d['insider'] = {
            'insider_sentiment': ins.insider_sentiment,
            'recent_buys': ins.recent_buys,
            'recent_sells': ins.recent_sells,
            'net_flow': _mcp_safe_serialize(ins.net_flow),
            'key_transactions': ins.key_transactions[:5] if ins.key_transactions else [],
            'data_status': getattr(ins, 'data_status', 'measured'),
        }

    # Signal explanation
    if result.signal_explanation:
        se = result.signal_explanation
        d['signal_explanation'] = {
            'primary_reason': se.primary_reason,
            'composite_score': _mcp_safe_serialize(se.composite_score),
            'overrides_triggered': se.overrides_triggered or [],
            'supporting_factors': se.supporting_factors or [],
            'risk_factors': se.risk_factors or [],
        }

    # Thesis
    if result.thesis:
        th = result.thesis
        d['thesis'] = {
            'summary': th.summary,
            'bull_case': th.bull_case[:4] if th.bull_case else [],
            'bear_case': th.bear_case[:4] if th.bear_case else [],
            'catalysts': th.catalysts[:3] if th.catalysts else [],
            'risks': th.risks[:3] if th.risks else [],
        }

    # Peer comparison
    if result.peer_comparison:
        pc = result.peer_comparison
        d['peer_comparison'] = {
            'sector': pc.sector,
            'pe_stock': _mcp_safe_serialize(pc.pe_stock),
            'pe_sector_avg': _mcp_safe_serialize(pc.pe_sector_avg),
            'pe_vs_sector': _mcp_safe_serialize(pc.pe_vs_sector),
            'roe_stock': _mcp_safe_serialize(pc.roe_stock),
            'roe_sector_avg': _mcp_safe_serialize(pc.roe_sector_avg),
            'roe_vs_sector': _mcp_safe_serialize(pc.roe_vs_sector),
            'pb_stock': _mcp_safe_serialize(getattr(pc, 'pb_stock', 0.0)),
            'pb_sector_avg': _mcp_safe_serialize(getattr(pc, 'pb_sector_avg', 0.0)),
            'pb_vs_sector': _mcp_safe_serialize(getattr(pc, 'pb_vs_sector', 0.0)),
            'overall_assessment': pc.overall_assessment,
            'sector_rank': pc.sector_rank,
            'sector_total': pc.sector_total,
        }

    # Catalysts
    if result.catalysts:
        cat = result.catalysts
        d['catalysts'] = {
            'earnings_date': cat.earnings_date,
            'days_to_earnings': cat.days_to_earnings,
            'last_earnings_result': cat.last_earnings_result,
            'last_earnings_surprise_pct': _mcp_safe_serialize(cat.last_earnings_surprise_pct),
            'ex_dividend_date': cat.ex_dividend_date,
            'dividend_yield': _mcp_safe_serialize(cat.dividend_yield),
            'catalyst_score': cat.catalyst_score,
            'near_term_catalyst': cat.near_term_catalyst,
        }

    # Key outputs
    d['recommendation_text'] = result.recommendation_text
    d['key_insights'] = result.key_insights[:6] if result.key_insights else []
    d['risk_warnings'] = result.risk_warnings[:4] if result.risk_warnings else []
    d['action_items'] = result.action_items[:5] if result.action_items else []
    d['analysis_timestamp'] = result.analysis_timestamp
    # Which listing was analysed, when it differs from what was typed.
    d['requested_symbol'] = getattr(result, 'requested_symbol', '') or result.ticker
    d['symbol_note'] = getattr(result, 'symbol_note', '') or ''
    d['business_summary'] = getattr(result, 'business_summary', '') or ''
    d['relative_performance'] = dict(getattr(result, 'relative_performance', {}) or {})
    d['history_bars'] = int(getattr(result, 'history_bars', 0) or 0)
    d['first_bar_date'] = getattr(result, 'first_bar_date', '') or None
    d['last_bar_date'] = getattr(result, 'last_bar_date', '') or None

    # =========================================================================
    # WEB-UI EXTENSIONS — surface the same fields the CLI display now uses so
    # the dashboard renders identical Executive Summary / Beginner Guidance /
    # Existing-Holder Profit Plan / Candlestick / Portfolio-Outlook sections.
    # =========================================================================
    try:
        confidence = float(result.confidence_score or 50.0)
        signal_str = result.signal.value if hasattr(result.signal, 'value') else str(result.signal)
        signal_display = signal_str.replace('_', ' ')
        conv = _conviction_label(confidence)
        _risk_grade_s = result.risk.risk_grade if result.risk else 'C'
        _bg_early = _compute_beginner_guidance(result)
        recommendation_label = _contextual_recommendation_label(
            signal_display, conv, _risk_grade_s, _bg_early['buy_now'])
        if decision_data:
            recommendation_label = decision_data['label']
            d['model_action'] = decision_data['action']
            d['stance'] = decision_data['stance']
            d['sizing_basis'] = decision_data['sizing_basis']
            d['recommendation_caveat'] = decision_data['caveat']
            d['recommendation_qualifier'] = decision_data.get('qualifier', '')
        else:
            d['model_action'] = ''
            d['stance'] = 'NONE'
            d['sizing_basis'] = 'none'
            d['recommendation_caveat'] = ''
            d['recommendation_qualifier'] = ''
        d['conviction_label'] = conv
        d['recommendation_label'] = recommendation_label

        ts = result.trade_setup
        cp = float(result.current_price or 0.0)
        target_v = float(ts.target_base) if ts and ts.target_base else 0.0
        stop_v = float(ts.stop_loss) if ts and ts.stop_loss else 0.0
        # Fallback ATR-based stop when trade_setup didn't compute one (mirrors
        # the CLI's display fallback in display_analysis around line 8722).
        if stop_v <= 0 and cp > 0:
            vol = float(result.risk.annualized_volatility) if result.risk and result.risk.annualized_volatility else 0.30
            atr_pct_pct = float(getattr(result.technical, 'atr_percent', 0.0)) if result.technical else 0.0
            if atr_pct_pct > 0:
                stop_pct = min(0.10, max(0.03, 2 * atr_pct_pct / 100))
            else:
                stop_pct = min(0.08, max(0.03, vol / 4))
            stop_v = cp * (1 - stop_pct)
        rr = float(ts.risk_reward_ratio) if ts and ts.risk_reward_ratio else 0.0
        rr_label = _format_rr(rr, target_v, stop_v, cp)

        # Candlestick block (added to technical dict if it exists)
        t = result.technical
        if t is not None and 'technical' in d:
            d['technical']['candlestick_bias'] = getattr(t, 'candlestick_bias', 'NEUTRAL')
            d['technical']['candlestick_score'] = _mcp_safe_serialize(getattr(t, 'candlestick_score', 50.0))
            d['technical']['candlestick_patterns'] = [
                {
                    'name': p.get('name'),
                    'bias': p.get('bias'),
                    'type': p.get('type'),
                    'reliability': p.get('reliability'),
                    'strength': p.get('strength'),
                    'context': p.get('context'),
                }
                for p in (getattr(t, 'candlestick_patterns', []) or [])[:6]
            ]
            d['technical']['prior_trend'] = getattr(t, 'prior_trend', 'SIDEWAYS')
            d['technical']['atr'] = _mcp_safe_serialize(getattr(t, 'atr', 0.0))
            d['technical']['atr_percent'] = _mcp_safe_serialize(getattr(t, 'atr_percent', 0.0))

        # Sentiment provenance
        if result.sentiment is not None and 'sentiment' in d:
            d['sentiment']['last_news_timestamp'] = getattr(result.sentiment, 'last_news_timestamp', '') or ''
            d['sentiment']['key_headline_sources'] = list(
                getattr(result.sentiment, 'key_headline_sources', []) or []
            )[:5]
            # Separate direct-company vs peer/sector vs macro news so the UI
            # weights direct headlines heavily and peer/macro lower.
            _peer_tk_s = []
            _pc_s = getattr(result, 'peer_comparison', None)
            if _pc_s is not None:
                _peer_tk_s = list(getattr(_pc_s, 'peer_tickers', []) or [])
            _news_s = _classify_news_relevance(
                getattr(result.sentiment, 'top_articles', None) or [],
                result.ticker, getattr(result, 'company_name', ''), _peer_tk_s)
            d['sentiment']['news_relevance'] = _news_s

        # Insider — surface display label after CAUTIOUS downgrade
        if result.insider is not None and 'insider' in d:
            raw_label = result.insider.insider_sentiment
            has_context = bool(result.insider.key_transactions)
            if raw_label == 'BEARISH' and not has_context:
                d['insider']['display_label'] = 'CAUTIOUS'
                d['insider']['context_missing'] = True
            else:
                d['insider']['display_label'] = raw_label
                d['insider']['context_missing'] = False

        # Beginner Guidance + entry levels (one block, easy for the UI)
        g = _bg_early
        g['rr_label'] = rr_label

        break_lvl = float(t.week_52_high) if t and t.week_52_high and t.week_52_high > cp else 0.0
        # Realistic pullback + tactical stop come straight from the guidance helper.
        pull_lvl = float(g.get('pullback_entry') or 0.0)
        pull_stop = float(g.get('pullback_stop') or 0.0)
        pull_deep = bool(g.get('pullback_is_deep'))
        pull_basis = g.get('pullback_basis') or ''
        tactical_stop = float(g.get('tactical_stop') or 0.0)
        tactical_stop_basis = g.get('tactical_stop_basis') or ''

        d['beginner_guidance'] = {
            'recommendation_label': recommendation_label,
            'signal': signal_str,
            'confidence': _mcp_safe_serialize(confidence),
            'buy_now': g['buy_now'],
            'candlestick_timing': g['timing'],
            'entry_quality': g['entry_quality'],
            'rr_label': rr_label,
            'risk_grade': result.risk.risk_grade if result.risk else 'C',
            'new_buyer': g['new_buyer'],
            'existing_holder': g['existing_holder'],
            'aggressive_buyer': g['aggressive'],
            'short_tf': g['short_tf'],
            'mid_tf': g['mid_tf'],
            'long_tf': g['long_tf'],
            'beginner_action': g['beginner_action'],
            'main_reason': g['main_reason'],
            'data_quality': g['data_quality'],
            'warnings': list(g['warnings']),
            'risk_conflict_warning': g.get('risk_conflict_warning', ''),
            'long_term_holder_stop': _mcp_safe_serialize(stop_v),
            'existing_holder_stop': _mcp_safe_serialize(stop_v),
            'tactical_stop': _mcp_safe_serialize(tactical_stop),
            'tactical_stop_basis': tactical_stop_basis,
            'breakout_entry': _mcp_safe_serialize(break_lvl),
            'pullback_entry': _mcp_safe_serialize(pull_lvl),
            'pullback_stop': _mcp_safe_serialize(pull_stop),
            'pullback_is_deep': pull_deep,
            'pullback_basis': pull_basis,
            'volume_requirement': '>= 1.5x avg on entry day',
        }

        # Monte Carlo vs valuation model note
        mc = result.monte_carlo
        v = result.valuation
        mi = result.market_intel
        val_target = float(v.target_base) if v and v.target_base else 0.0
        analyst_t = float(mi.target_mean) if mi and mi.target_mean else 0.0
        ref_target = max(val_target, analyst_t)
        mc_median = float(mc.median_price) if mc and mc.median_price else 0.0
        if ref_target > 0 and mc_median > ref_target * 1.02:
            d['mc_model_note'] = (
                'Monte Carlo is more bullish short-term than valuation targets. '
                'Treat upside as momentum-driven, not valuation-confirmed.'
            )
        else:
            d['mc_model_note'] = ''

        # Action plan title + portfolio outlook block. "Buy now: NO" is candle
        # timing; when the resolved decision still funds new money it means
        # "enter gradually at a better level", not "holders only".
        decision_funds = str((decision_data or {}).get('action') or result.final_action or '').upper() in (
            'STRONG_BUY', 'BUY', 'ACCUMULATE')
        is_holder_plan = (g['buy_now'] == 'NO') and not decision_funds
        wait_for_entry = (g['buy_now'] == 'NO') and decision_funds
        d['action_plan_title'] = (
            'EXISTING HOLDER PROFIT PLAN' if is_holder_plan
            else ('SCALE-IN PLAN' if wait_for_entry else 'ACTION PLAN'))
        d['action_plan_note'] = (
            'These targets are for existing holders, not new entry recommendations.'
            if is_holder_plan else (
                'The Decision allows a small position, but today\'s timing is weak: '
                'prefer the pullback entry or a confirmed breakout to buying at the current price.'
                if wait_for_entry else ''))

        if wait_for_entry:
            bottom_line = (
                'New buyers may build a small position gradually, preferably near the pullback '
                'entry rather than all at once today. Existing holders can hold with the stop.'
            )
        elif g['buy_now'] == 'NO':
            bottom_line = (
                'Existing holders can hold with a strict stop. '
                'New buyers should wait for pullback or breakout confirmation.'
            )
        elif g['buy_now'] == 'YES':
            bottom_line = (
                'Setup is favorable. Starter entries acceptable with disciplined stops; '
                'existing holders may add on confirmation.'
            )
        elif g['buy_now'] == 'ONLY AFTER CONFIRMATION':
            bottom_line = (
                'Wait for confirmation candle and volume before adding. '
                'Existing holders may hold with a trailing stop.'
            )
        else:
            bottom_line = 'Mixed signals — defer new buying until a clearer setup emerges.'

        d['portfolio_outlook'] = {
            'recommendation': recommendation_label,
            'bottom_line': bottom_line,
            'reason': g['main_reason'],
        }
    except Exception as _ext_err:  # pragma: no cover - defensive
        logger.debug(f"web-ui extension serialization failed: {_ext_err}")

    return d


def _mcp_format_analysis_text(d: Dict[str, Any]) -> str:
    """Format analysis dict into a rich professional-grade text report for the LLM."""
    sym = d.get('currency_symbol', '$')
    lines = []

    # ═══════════════════════════════════════════════════════════════════════
    # HEADER
    # ═══════════════════════════════════════════════════════════════════════
    lines.append(f"# {d['company_name']} ({d['ticker']}) — QUANTA Elite Analysis")
    lines.append(f"**Sector:** {d['sector']} | **Industry:** {d['industry']}")
    price = d.get('current_price', 0)
    change_pct = d.get('day_change_pct', 0)
    arrow = "▲" if change_pct >= 0 else "▼"
    lines.append(f"**Price:** {sym}{price:,.2f} {arrow} {change_pct:+.2f}%")
    if d.get('market_cap'):
        lines.append(f"**Market Cap:** {format_currency(d['market_cap'], sym)}")
    lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # SIGNAL & VERDICT
    # ═══════════════════════════════════════════════════════════════════════
    _qual = d.get('recommendation_qualifier')
    lines.append(f"## Research decision: {d.get('recommendation_label') or 'NO VERDICT'}"
                 + (f" ({_qual.lower()})" if _qual else ''))
    evidence = d.get('evidence') or {}
    lines.append(f"- **Evidence:** {evidence.get('status_text', 'Never validated')}")
    lines.append(f"- **Model action:** {d.get('model_action') or d['signal']} (heuristic)")
    lines.append(f"- **Confidence:** {d.get('confidence_score', 0):.0f}% ({d.get('confidence_level', 'N/A')})")
    lines.append(f"- **Market Regime:** {d.get('market_regime', 'N/A')}")
    if d.get('recommendation_text') and evidence.get('permits_recommendation'):
        lines.append(f"- **Recommendation:** {d['recommendation_text']}")
    elif d.get('recommendation_caveat'):
        lines.append(f"- **Caveat:** {d['recommendation_caveat']}")
    lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # SIGNAL EXPLANATION — WHY THIS SIGNAL
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('signal_explanation'):
        se = d['signal_explanation']
        lines.append("## Why This Signal")
        if se.get('primary_reason'):
            lines.append(f"**Primary Reason:** {se['primary_reason']}")
        if se.get('overrides_triggered'):
            lines.append("**Overrides:**")
            for o in se['overrides_triggered']:
                lines.append(f"  - {o}")
        if se.get('supporting_factors'):
            lines.append("**Supporting Factors:**")
            for sf in se['supporting_factors']:
                lines.append(f"  - {sf}")
        if se.get('risk_factors'):
            lines.append("**Risk Factors:**")
            for rf in se['risk_factors']:
                lines.append(f"  - {rf}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # MULTI-FACTOR SCORECARD
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('scores'):
        sc = d['scores']
        lines.append("## Multi-Factor Scores")
        lines.append("| Factor | Score | Grade |")
        lines.append("|--------|-------|-------|")
        for key in ['technical_score', 'fundamental_score', 'valuation_score',
                     'sentiment_score', 'risk_score']:
            name = key.replace('_score', '').title()
            val = sc.get(key, 50)
            grade = get_composite_grade(val)
            lines.append(f"| {name} | {val:.0f}/100 | {grade} |")
        comp = sc.get('composite_score', 50)
        lines.append(f"| **COMPOSITE** | **{comp:.0f}/100** | **{get_composite_grade(comp)}** |")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # TRADE SETUP
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('trade_setup'):
        ts = d['trade_setup']
        entry = ts.get('entry_price', 0) or 0
        stop = ts.get('stop_loss', 0) or 0
        t_cons = ts.get('target_conservative', 0) or 0
        t_base = ts.get('target_base', 0) or 0
        t_aggr = ts.get('target_aggressive', 0) or 0

        if entry > 0 and stop > 0:
            lines.append("## Trade Setup")
            lines.append(f"- **Entry:** {sym}{entry:,.2f}")
            lines.append(f"- **Stop Loss:** {sym}{stop:,.2f}")
            lines.append(f"- **Target (Conservative):** {sym}{t_cons:,.2f}")
            lines.append(f"- **Target (Base):** {sym}{t_base:,.2f}")
            lines.append(f"- **Target (Aggressive):** {sym}{t_aggr:,.2f}")
            lines.append(f"- **Risk/Reward:** {ts.get('risk_reward_ratio', 0):.2f}:1")
            # [T6 FIX] Was "**Win Probability:** {win_probability:.0%}". Now
            # that no calibrated win rate is claimed the field is always 0.0,
            # so printing it would render "0%" — worse than omitting it.
            lines.append(
                f"- **Confidence tier:** {ts.get('confidence_tier', 'MEDIUM')} "
                f"(no calibrated win rate is claimed)")
            if ts.get('setup_basis'):
                lines.append(f"- **Basis:** {ts.get('setup_basis')}")
            if ts.get('review_date'):
                lines.append(f"- **Review by:** {ts.get('review_date')}")
        else:
            # Fallback: derive levels from technical data
            tech = d.get('technical', {})
            support = tech.get('support_level', 0) or 0
            resistance = tech.get('resistance_level', 0) or 0
            lines.append("## Derived Trade Levels")
            lines.append("*Trade setup guardrail active (e.g. imminent earnings or high risk). Using technical levels:*")
            if support > 0:
                lines.append(f"- **Support (potential entry):** {sym}{support:,.2f}")
            if resistance > 0:
                lines.append(f"- **Resistance (target):** {sym}{resistance:,.2f}")
            if support > 0 and price > 0:
                lines.append(f"- **Suggested Stop:** {sym}{support * 0.97:,.2f} (3% below support)")
            if resistance > 0 and support > 0 and price > 0:
                reward = resistance - price
                risk = price - support * 0.97
                if risk > 0:
                    lines.append(f"- **Implied R:R:** {reward / risk:.2f}:1")
            lines.append(
                f"- **Confidence tier:** {ts.get('confidence_tier', 'MEDIUM')} "
                f"(no calibrated win rate is claimed)")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # VALUATION & PRICE TARGETS
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('valuation'):
        v = d['valuation']
        lines.append("## Valuation & Price Targets")
        if v.get('dcf_fair_value'):
            lines.append(f"- **DCF Fair Value:** {sym}{v['dcf_fair_value']:,.2f} ({v.get('dcf_upside', 0):+.1f}%)")
        if v.get('target_base'):
            _agree = v.get('target_agreement_pct')
            _nsrc = v.get('target_source_count') or 0
            _qual = (f"{_nsrc} sources, {_agree:.0f}% agreement" if _agree is not None
                     else f"{_nsrc} source" + ("s" if _nsrc != 1 else "") + ", agreement n/a")
            lines.append(f"- **Composite Target:** {sym}{v['target_base']:,.2f} ({_qual})")
        lines.append(f"- **Valuation:** {v.get('valuation_label', 'N/A')} (score: {v.get('valuation_score', 50):.0f}/100)")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # MONTE CARLO SIMULATION
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('monte_carlo'):
        mc = d['monte_carlo']
        lines.append(f"## Monte Carlo (30-day, {mc.get('simulations_run', 0)} sims)")
        lines.append(f"- **Median Price:** {sym}{mc.get('median_price', 0):,.2f}")
        lines.append(f"- **5th Percentile:** {sym}{mc.get('percentile_5', 0):,.2f}")
        lines.append(f"- **95th Percentile:** {sym}{mc.get('percentile_95', 0):,.2f}")
        lines.append(f"- **P(profit):** {mc.get('probability_profit', 0):.0%}")
        lines.append(f"- **P(+10%):** {mc.get('probability_10pct_gain', 0):.0%}")
        lines.append(f"- **P(-10%):** {mc.get('probability_10pct_loss', 0):.0%}")
        lines.append(f"- **Expected Return:** {mc.get('expected_return', 0):+.2f}%")
        lines.append(f"- **VaR (95%):** {mc.get('var_95', 0):+.2f}%")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # KEY INSIGHTS
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('key_insights'):
        lines.append("## Key Insights")
        for insight in d['key_insights']:
            lines.append(f"- {insight}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # RISK WARNINGS
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('risk_warnings'):
        lines.append("## Risk Warnings")
        for w in d['risk_warnings']:
            lines.append(f"- {w}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # INVESTMENT THESIS
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('thesis'):
        th = d['thesis']
        lines.append("## Investment Thesis")
        if th.get('summary'):
            lines.append(th['summary'])
        if th.get('bull_case'):
            lines.append("\n**Bull Case:**")
            for b in th['bull_case']:
                lines.append(f"  + {b}")
        if th.get('bear_case'):
            lines.append("\n**Bear Case:**")
            for b in th['bear_case']:
                lines.append(f"  - {b}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════
    # ACTION ITEMS
    # ═══════════════════════════════════════════════════════════════════════
    if d.get('action_items'):
        lines.append("## Action Items")
        for a in d['action_items']:
            lines.append(f"- {a}")
        lines.append("")

    lines.append(f"*Analysis timestamp: {d.get('analysis_timestamp', 'N/A')}*")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MCP SERVER FACTORY — creates the FastMCP instance with all tools
# ─────────────────────────────────────────────────────────────────────────────

def _run_mcp_server() -> None:
    """Run THE QUANTA MCP server (``mcp_server/quanta_mcp_server.py``).

    This module used to embed a second, diverging FastMCP server with its own
    tool implementations. ``--mcp`` now launches the one maintained server, so
    every MCP client gets the same tools and the same published Decision.
    """
    import runpy
    server = Path(__file__).resolve().parent.parent / "mcp_server" / "quanta_mcp_server.py"
    runpy.run_path(str(server), run_name="__main__")


def main():
    """Main entry point for interactive analysis"""
    # Ensure UTF-8 output for emoji support on Windows terminals
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass  # Fallback: some terminals don't support reconfigure
    
    # Configuration with correct key names for APIClient
    config = {
        'news_api_key': os.getenv('NEWS_API_KEY', ''),
        'finnhub_key': os.getenv('FINNHUB_API_KEY', ''),
        'fmp_key': os.getenv('FMP_API_KEY', ''),
        'polygon_key': os.getenv('POLYGON_API_KEY', ''),
        'alpha_vantage_key': os.getenv('ALPHA_VANTAGE_KEY', '')
    }
    
    # Suppress logger during initialization (prevent duplicate logs)
    temp_level = logger.level
    logger.setLevel(logging.WARNING)
    
    # Initialize analyzer (SINGLE instance)
    analyzer = QuantaEliteAnalyzer(config)
    
    # Restore logging
    logger.setLevel(temp_level)
    
    # Display header
    print("\n" + "="*60)
    print("QUANTA ELITE PORTFOLIO ADVISOR")
    print("   Your Personal Hedge Fund Manager")
    print("="*60)
    
    # Create API client for macro analysis
    api_client = APIClient(config)
    
    while True:
        try:
            ticker = input("\nEnter ticker (or 'quit' to exit): ").strip().upper()
            
            if ticker.lower() in ['quit', 'exit', 'q', '']:
                print("\n👋 Thank you for using QUANTA Elite. Good luck trading!")
                break
            
            print(f"\nAnalyzing {ticker}...")
            start = time.time()
            
            # Fetch macro environment in parallel with stock analysis
            print("   Fetching market environment...")
            market_env = fetch_market_environment(api_client)
            
            result = analyzer.analyze(ticker)
            sym = result.currency_symbol
            
            # Display market environment first
            display_market_environment(market_env, sym)
            
            # Display standard analysis
            display_analysis(result, detailed=True)
            
            # Display signal track record (if enough history)
            display_signal_track_record(analyzer)
            
            elapsed = time.time() - start
            print(f"\nAnalysis completed in {elapsed:.1f} seconds")
            
            # =========================================================================
            # POSITION TRACKING - Ask about existing position
            # =========================================================================
            print("\n" + "─" * 60)
            has_position = input(f"Do you have an existing position in {ticker}? (y/n): ").strip().lower()
            
            if has_position in ['y', 'yes']:
                try:
                    # Get position details
                    shares_input = input("   Enter your position size (shares): ").strip()
                    shares = int(shares_input.replace(',', ''))
                    
                    avg_cost_input = input(f"   Enter your average cost per share ({sym}): ").strip()
                    # Remove currency symbol if present
                    avg_cost_input = avg_cost_input.replace('$', '').replace(',', '')
                    avg_cost = float(avg_cost_input)
                    
                    if shares > 0 and avg_cost > 0:
                        # Display personalized position analysis WITH MACRO CONTEXT
                        display_position_analysis(result, shares, avg_cost, sym, market_env)
                        
                        print("\n" + "═" * 80)
                        print("Personalized analysis complete (with macro environment context)")
                        print("═" * 80)
                    else:
                        print("   ⚠️ Invalid input - skipping position analysis")
                        
                except ValueError:
                    print("   ⚠️ Invalid number format - skipping position analysis")
            else:
                print("\nTip: Enter your position details for personalized recommendations!")
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            logger.exception("Main loop error")


if __name__ == "__main__":
    # ─── MCP Mode: run as MCP server when --mcp flag is passed ───────
    if '--mcp' in sys.argv:
        if not HAS_MCP:
            print("ERROR: MCP SDK not installed. Run: pip install mcp[cli]")
            sys.exit(1)
        # Suppress all console output — MCP uses stdio JSON-RPC
        logging.getLogger().setLevel(logging.CRITICAL)
        for noisy in ("yfinance", "urllib3", "requests", "httpx",
                      "polygon_provider", "compliance_manager", "peewee", "matplotlib"):
            logging.getLogger(noisy).setLevel(logging.CRITICAL)
        logger.info("[MCP] Starting QUANTA Elite MCP Server...")
        _run_mcp_server()
    else:
        # ─── Interactive CLI Mode (original) ─────────────────────────
        try:
            main()
        except Exception as e:
            logger.critical(f"Fatal error: {e}")
            sys.exit(1)
