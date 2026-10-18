#!/usr/bin/env python3
"""
QUANTA Elite v6.0 - Quantitative Trading Intelligence
"""


import sys
import asyncio
import warnings
import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, TYPE_CHECKING
import json
import logging
from logging.handlers import RotatingFileHandler
import textwrap
import csv
import os
import time
import random
import math
import re
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf

# Ensure sibling module directories are importable (core/ -> ../modules)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MODULES_DIR = PROJECT_ROOT / "modules"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if MODULES_DIR.exists() and str(MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(MODULES_DIR))

if TYPE_CHECKING:
    import requests as requests_type


def _import_optional(*module_candidates: str):
    """Try importing from multiple module paths and return the imported module or None."""
    for module_name in module_candidates:
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    return None

# Try to import optional dependencies
try:
    tqdm = importlib.import_module('tqdm').tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    # Fallback progress indicator when tqdm is not installed
    class tqdm:
        def __init__(self, iterable=None, total=None, desc="", **kwargs):
            self.iterable = iterable
            self.total = total or (len(iterable) if hasattr(iterable, '__len__') else None)
            self.desc = desc or "Progress"
            self.n = 0
        def __iter__(self):
            for item in self.iterable:
                self.n += 1
                if self.total:
                    step = max(1, self.total // 20)
                    if self.n % step == 0 or self.n == self.total:
                        pct = self.n / self.total * 100
                        bar = '█' * int(pct // 5) + '░' * (20 - int(pct // 5))
                        print(f"\r  {self.desc}: |{bar}| {self.n}/{self.total} ({pct:.0f}%)", end="", flush=True)
                yield item
            if self.total:
                print()  # New line after completion
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def update(self, n=1):
            self.n += n

# [CRITICAL FIX 1.1] Walk-Forward Backtesting Engine
_backtest_module = _import_optional("backtest_engine", "modules.backtest_engine")
if _backtest_module:
    WalkForwardBacktest = getattr(_backtest_module, 'WalkForwardBacktest', None)
    check_factor_vif = getattr(_backtest_module, 'check_factor_vif', None)
    calculate_true_factor_loadings = getattr(_backtest_module, 'calculate_true_factor_loadings', None)
    HAS_BACKTEST = WalkForwardBacktest is not None
else:
    WalkForwardBacktest = None
    check_factor_vif = None
    calculate_true_factor_loadings = None
    HAS_BACKTEST = False
    logging.getLogger("QUANTA").debug("backtest_engine not available - backtesting disabled")

# [DECISION-CORE] Shared validation / decision / consistency engine
_SP_CORE_DIR = Path(__file__).resolve().parent
if str(_SP_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_SP_CORE_DIR))
try:
    from engine import (
        FinalAction,
        DecisionInputs,
        resolve_final_action,
        signal_to_final_action,
        validate_ticker_data,
        dividend_yield_from_info,
        validate_dividend_yield,
        BACKTEST_DIAGNOSTIC_BANNER,
        backtest_disclaimer,
    )
    from engine.data_status import DataStatus, is_data_failure, is_true_invalid
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
        DEEP_CONFIRMATION_FAILED = "deep_confirmation_failed"
        NOT_INDEPENDENTLY_CONFIRMED = "not_independently_confirmed"

    _DATA_FAILURE = {
        "insufficient_history", "provider_rate_limited", "provider_data_unavailable",
        "deep_confirmation_failed", "not_independently_confirmed",
    }

    def is_data_failure(status):  # type: ignore
        return str(status or "").strip().lower() in _DATA_FAILURE

    def is_true_invalid(status):  # type: ignore
        return str(status or "").strip().lower() == "invalid_ticker"

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False
    def tabulate(data, headers=None, tablefmt=None):
        # Simple fallback
        return str(data)

# [CATALYST SCANNER] News-driven catalyst detection with SEC EDGAR integration
_catalyst_module = _import_optional("catalyst_scanner", "modules.catalyst_scanner")
if _catalyst_module:
    CatalystScanner = getattr(_catalyst_module, 'CatalystScanner', None)
    CatalystSignal = getattr(_catalyst_module, 'CatalystSignal', None)
    CATALYST_RISK_RULES = getattr(_catalyst_module, 'CATALYST_RISK_RULES', None)
    CatalystAwareSignalGenerator = getattr(_catalyst_module, 'CatalystAwareSignalGenerator', None)
    fetch_sec_catalysts = getattr(_catalyst_module, 'fetch_sec_catalysts', None)
    fetch_polygon_news = getattr(_catalyst_module, 'fetch_polygon_news', None)
    HAS_CATALYST = CatalystScanner is not None
else:
    HAS_CATALYST = False
    CatalystScanner = None
    CatalystSignal = None
    CATALYST_RISK_RULES = None
    CatalystAwareSignalGenerator = None
    fetch_sec_catalysts = None
    fetch_polygon_news = None

# [COMPLIANCE] Regulatory compliance (SEBI/SEC/FINRA)
_compliance_module = _import_optional("compliance_manager", "modules.compliance_manager")
if _compliance_module:
    ComplianceManager = getattr(_compliance_module, 'ComplianceManager', None)
    HAS_COMPLIANCE = ComplianceManager is not None
else:
    HAS_COMPLIANCE = False
    ComplianceManager = None

# [PORTFOLIO RISK] VaR and correlation enforcement
_portfolio_risk_module = _import_optional("portfolio_risk_manager", "modules.portfolio_risk_manager")
if _portfolio_risk_module:
    PortfolioRiskManager = getattr(_portfolio_risk_module, 'PortfolioRiskManager', None)
    HAS_PORTFOLIO_RISK = PortfolioRiskManager is not None
else:
    # Fallback: create inline class
    HAS_PORTFOLIO_RISK = False
    class PortfolioRiskManager:
        def check_position_limits(self, pos, existing): return True, ""
        def calculate_portfolio_var(self, *args, **kwargs): return 0.0

# [EPS REVISIONS] Zacks-style earnings-estimate-revision factor
_revision_module = _import_optional("earnings_revision_scoring", "modules.earnings_revision_scoring")
if _revision_module:
    compute_earnings_revision_signal = getattr(_revision_module, 'compute_earnings_revision_signal', None)
    HAS_EARNINGS_REVISIONS = compute_earnings_revision_signal is not None
else:
    HAS_EARNINGS_REVISIONS = False
    compute_earnings_revision_signal = None

# [INSTITUTIONAL CATALYST ENGINE] Professional 4-tier catalyst detection
_institutional_catalyst_module = _import_optional(
    "catalyst_intelligence_engine", "modules.catalyst_intelligence_engine"
)
if _institutional_catalyst_module:
    InstitutionalCatalystEngine = getattr(_institutional_catalyst_module, 'InstitutionalCatalystEngine', None)
    InstitutionalCatalystSignal = getattr(_institutional_catalyst_module, 'InstitutionalCatalystSignal', None)
    HAS_INSTITUTIONAL_CATALYST = InstitutionalCatalystEngine is not None
else:
    HAS_INSTITUTIONAL_CATALYST = False
    InstitutionalCatalystEngine = None
    InstitutionalCatalystSignal = None

# ============================================================================
# FIX #1: CSV-Based Stock Universe Loader
# ============================================================================

def load_stocks_from_csv(csv_path: str, fallback_list: List[str]) -> List[str]:
    """
    **FIX #1: Load stock symbols from CSV file with fallback**
    
    CSV Format: Must have 'symbol' column
    Example:
        symbol,name,sector
        BGRENERGY,BG Energy Systems,Energy
        RELIANCE,Reliance Industries,Energy
    
    Args:
        csv_path: Path to CSV file (relative or absolute)
        fallback_list: Hardcoded list to use if CSV not found
    
    Returns:
        List of stock symbols
    """
    # Make path relative to script directory
    if not os.path.isabs(csv_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(script_dir, csv_path)
    
    # Check if file exists
    if not os.path.exists(csv_path):
        logging.debug(f"CSV not found: {csv_path}, using hardcoded list ({len(fallback_list)} stocks)")
        return fallback_list
    
    try:
        stocks = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Validate CSV has 'symbol' column
            if 'symbol' not in reader.fieldnames:
                logging.error(f"CSV {csv_path} missing 'symbol' column, using fallback")
                return fallback_list
            
            # Read symbols
            for row in reader:
                symbol = row['symbol'].strip()
                if symbol:  # Skip empty rows
                    stocks.append(symbol)
        
        logging.info(f"✓ Loaded {len(stocks)} stocks from {os.path.basename(csv_path)}")
        return stocks
        
    except Exception as e:
        logging.error(f"Error loading CSV {csv_path}: {e}, using fallback")
        return fallback_list


# Additional imports for full functionality
# (tabulate already imported above with fallback)

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    requests = None

try:
    from scipy import stats
except ImportError:
    stats = None

try:
    from scipy.optimize import minimize
except ImportError:
    minimize = None


# Enhanced UX Libraries
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.markdown import Markdown
    from rich.layout import Layout
    from rich.align import Align
    from rich import box
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("📦 Install 'rich' for enhanced terminal output: pip install rich")

try:
    questionary = importlib.import_module('questionary')

    QUESTIONARY_AVAILABLE = True
except ImportError:
    questionary = None
    QUESTIONARY_AVAILABLE = False

from contextlib import contextmanager

# Disable yfinance logging to prevent error spam
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

warnings.filterwarnings('ignore')

# ============================================================================
# CRITICAL FIX #1: Enhancement availability flag
# ============================================================================
ENHANCEMENTS_AVAILABLE = False  # Set to True if rich/questionary installed

# Initialize Rich Console
console = Console() if RICH_AVAILABLE else None

# Import Enhancement Modules
try:
    _qe = importlib.import_module('quanta_enhancements')
    InvestorProfile = _qe.InvestorProfile
    StockNarrative = _qe.StockNarrative
    RecommendationNarrator = _qe.RecommendationNarrator
    MarketContextAnalyzer = _qe.MarketContextAnalyzer

    _qro = importlib.import_module('quanta_rich_output')
    RichDisplay = _qro.RichDisplay

    ENHANCEMENTS_AVAILABLE = True
except ImportError as e:
    ENHANCEMENTS_AVAILABLE = False
    # Optional modules not found - using built-in fallbacks (works perfectly)


    # Create stub classes for compatibility
    class InvestorProfile:
        def __init__(self, **kwargs):
            self.risk_tolerance = kwargs.get('risk_tolerance', 'moderate')
            self.capital = kwargs.get('capital', 2500.0)

        def get_risk_multiplier(self):
            return 1.0

        def get_max_position_pct(self):
            return 0.30


    class RichDisplay:
        def __init__(self):
            self.enabled = False

        def print_success(self, msg):
            print(f"✓ {msg}")

        def print_header(self, title, subtitle=""):
            print(f"\n{'=' * 80}\n{title}\n{subtitle}\n{'=' * 80}")


    class RecommendationNarrator:
        @staticmethod
        def create_narrative(analysis, profile=None):
            return None


    class MarketContextAnalyzer:
        @staticmethod
        def describe_market_regime(vix=0):
            return "Monitor market conditions."


# ============================================================================
# QUANTA ELITE v6.0 - CORE UTILITIES AND INFRASTRUCTURE
# ============================================================================

def _json_safe(obj: Any) -> Any:
    """Recursively make ``obj`` strict-JSON serialisable.

    numpy scalars become Python numbers (default=str turned them into
    strings), non-finite floats become null (bare NaN tokens break strict
    parsers such as JSON.parse), and Enums become their value.
    """
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, Enum):
        return _json_safe(obj.value)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    return obj


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    **v6.0 FIX #1.3: Safe division utility to prevent ZeroDivisionError**
    
    Handles zero, NaN, and infinity cases gracefully.
    
    Args:
        numerator: The dividend
        denominator: The divisor
        default: Value to return if division is unsafe (default: 0.0)
    
    Returns:
        Result of division or default value if unsafe
    """
    try:
        # Check for None
        if numerator is None or denominator is None:
            return default
        
        # Check for NaN
        if np.isnan(numerator) or np.isnan(denominator):
            return default
        
        # Check for zero denominator
        if denominator == 0:
            return default
        
        result = numerator / denominator
        
        # Check for infinity result
        if np.isinf(result):
            return default
        
        # Check for NaN result
        if np.isnan(result):
            return default
            
        return float(result)
    except (ZeroDivisionError, TypeError, ValueError):
        return default


# ── Shared momentum definition ──────────────────────────────────────────────
# [W4 FIX] The 12-2 momentum factor had two implementations that disagreed:
# TechnicalAnalysisEngine used iloc[-44] while EliteQuantEngine used iloc[-42],
# and the inline comment documented a 22-day skip that matched neither. One
# named factor must have one value, so both now call this.
MOMENTUM_LOOKBACK_DAYS = 252   # ~12 months of trading days
MOMENTUM_SKIP_DAYS = 42        # ~2 months, the "2" in 12-2
# yfinance ``period="1y"`` returns 250-252 bars depending on holidays (US
# tickers came back with 251 on 2026-09-23), so a hard ``len >= 252`` made the
# factor 0.0 for every US name while Indian names happened to get it. Accept
# anything within ~2 weeks of a full year and anchor on the oldest bar.
MOMENTUM_MIN_BARS = 240


def _twelve_month_anchor(closes: Any) -> Optional[int]:
    """Negative iloc index of the ~12-month-ago bar, or None if too short."""
    try:
        n = len(closes)
    except TypeError:
        return None
    if n < MOMENTUM_MIN_BARS:
        return None
    return -min(n, MOMENTUM_LOOKBACK_DAYS)


# [O6 FIX] The conviction scale is 1-5; the display capped stars at 4.
CONVICTION_MAX = 5


def fmt_shares(value: Any) -> str:
    """Render a share count: plain integer when whole, trimmed when fractional."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "0"
    if not np.isfinite(v):
        return "0"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.4f}".rstrip('0').rstrip('.')


def fmt_price(value: Any, currency: str = "$") -> str:
    """Format a price with precision appropriate to its magnitude.

    [O5 FIX] The summary table and the detail cards formatted every price with
    ``:.0f`` — entry zone, stop, target, add level, T1, moving averages. An
    $11.40 stop rendered as "$11": a 3.5% error in the single most important
    number on a page explicitly framed as placeable orders. The precision
    existed on the object and was discarded at the last step. Anything a user
    types into a broker keeps at least two decimals.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{currency}—"
    if not np.isfinite(v):
        return f"{currency}—"
    if abs(v) >= 1000:
        return f"{currency}{v:,.2f}"
    if abs(v) >= 100:
        return f"{currency}{v:.2f}"
    return f"{currency}{v:.2f}"


def describe_horizon(months: float) -> str:
    """Render a holding horizon in months as one consistent human label.

    [O4 FIX] The single place a horizon becomes words. Previously three
    independent renderings existed and disagreed with each other.
    """
    try:
        m = float(months)
    except (TypeError, ValueError):
        return "unknown"
    if not np.isfinite(m) or m <= 0:
        return "unknown"
    if m < 1.0:
        return "2-4 weeks"
    if m < 2.0:
        return "1-2 months"
    if m < 4.0:
        return "2-4 months"
    if m < 7.0:
        return "3-6 months"
    if m < 10.0:
        return "6-9 months"
    if m < 13.0:
        return "9-12 months"
    return "12-18 months"


def compute_momentum_12_2(closes: Any) -> float:
    """Return the 12-2 momentum: the return from t-12 months to t-2 months.

    Skipping the most recent stretch is what separates this from plain
    momentum — recent returns mean-revert, so the academic factor excludes
    them. Returns 0.0 when there is not enough history to compute it.
    """
    try:
        anchor = _twelve_month_anchor(closes) if closes is not None else None
        if anchor is None:
            return 0.0
        start = float(closes.iloc[anchor])
        end = float(closes.iloc[-MOMENTUM_SKIP_DAYS])
        if start <= 0 or not np.isfinite(start) or not np.isfinite(end):
            return 0.0
        return float(end / start - 1.0)
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return 0.0


def _clean_float(value: Any, default: float = 0.0) -> float:
    """Convert provider values to finite floats without leaking NaN/Inf."""
    try:
        if value is None:
            return default
        result = float(value)
        if np.isnan(result) or np.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _detect_bearish_reversal(hist: Any) -> Tuple[bool, str]:
    """Detect a high-reliability bearish reversal candle after an uptrend.

    Lightweight counterpart to the deep analyzer's candlestick engine so the
    scanner can feed the SAME `bearish_reversal` flag into the decision-core.
    Recognises a Shooting Star and a Bearish Engulfing that appear *after* a
    short-term uptrend (last close above the 10-bar SMA and a positive 10-bar
    run). Returns ``(is_bearish_reversal, pattern_name)``.
    """
    try:
        if hist is None or len(hist) < 12:
            return (False, "")
        o = float(hist['Open'].iloc[-1]); h = float(hist['High'].iloc[-1])
        l = float(hist['Low'].iloc[-1]);  c = float(hist['Close'].iloc[-1])
        po = float(hist['Open'].iloc[-2]); pc = float(hist['Close'].iloc[-2])
        rng = h - l
        if rng <= 0:
            return (False, "")
        # Require a prior short-term uptrend so we only flag *reversals*.
        sma10 = float(hist['Close'].iloc[-11:-1].mean())
        ten_bars_ago = float(hist['Close'].iloc[-11])
        uptrend = (c > sma10) and (pc > ten_bars_ago)
        if not uptrend:
            return (False, "")
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        # Shooting Star: small body in lower third, long upper wick.
        if (body <= 0.35 * rng and upper_wick >= 2.0 * max(body, 1e-9)
                and lower_wick <= 0.20 * rng):
            return (True, "Shooting Star")
        # Bearish Engulfing: red candle whose body engulfs the prior green body.
        prev_green = pc > po
        curr_red = c < o
        if prev_green and curr_red and o >= pc and c <= po and body > 0:
            return (True, "Bearish Engulfing")
        return (False, "")
    except Exception:
        return (False, "")


def _extract_news_headlines(news: Any, limit: int = 20) -> List[str]:
    """Normalize yfinance news payloads across old and new schemas."""
    if not isinstance(news, list):
        return []

    headlines: List[str] = []
    for item in news[:limit]:
        if not isinstance(item, dict):
            continue
        title = item.get('title')
        content = item.get('content')
        if not title and isinstance(content, dict):
            title = content.get('title') or content.get('headline')
        if not title and isinstance(item.get('summary'), str):
            title = item.get('summary')
        title = str(title or '').strip()
        if title:
            headlines.append(title)
    return headlines


def _extract_news_items(news: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """Title, publisher, publish time (ISO UTC) and link of each news item,
    across yfinance's old flat and new ``content`` schemas."""
    if not isinstance(news, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in news[:limit]:
        if not isinstance(item, dict):
            continue
        c = item.get('content') if isinstance(item.get('content'), dict) else {}
        title = str(item.get('title') or c.get('title') or c.get('headline') or '').strip()
        if not title:
            continue
        provider = c.get('provider') if isinstance(c.get('provider'), dict) else {}
        url_obj = c.get('canonicalUrl') or c.get('clickThroughUrl') or {}
        url = str(item.get('link') or (url_obj.get('url') if isinstance(url_obj, dict) else '') or '')
        when = ''
        raw = c.get('pubDate') or c.get('displayTime') or item.get('providerPublishTime')
        try:
            if isinstance(raw, (int, float)) and raw > 0:
                when = datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
            elif raw:
                when = pd.Timestamp(str(raw)).tz_convert('UTC').isoformat() if pd.Timestamp(str(raw)).tzinfo \
                    else pd.Timestamp(str(raw)).tz_localize('UTC').isoformat()
        except Exception:
            when = ''
        out.append({"title": title[:160], "publisher": str(item.get('publisher') or provider.get('displayName') or '')[:60],
                    "published": when, "url": url if url.startswith('http') else ''})
    return out


YAHOO_TICKER_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


def _rss_news_items(ticker: str, get: Optional[Callable[[str], Any]] = None,
                    limit: int = 20) -> List[Dict[str, Any]]:
    """A ticker's headlines from Yahoo Finance's per-symbol RSS feed.

    yfinance 1.7's news call posts to an endpoint Yahoo now answers with 404,
    so ``Ticker.news`` is empty for every name and the scan read no company
    news (and no headline catalysts) at all. The RSS feed still serves dated,
    linked headlines; same shape as :func:`_extract_news_items`.
    """
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from urllib.parse import quote
    resp = (get or NetworkSession.rate_limited_get)(YAHOO_TICKER_RSS.format(symbol=quote(ticker)))
    resp.raise_for_status()
    out: List[Dict[str, Any]] = []
    for item in ET.fromstring(resp.content).iter('item'):
        title = (item.findtext('title') or '').strip()
        if not title:
            continue
        when = ''
        try:
            when = parsedate_to_datetime(item.findtext('pubDate') or '').astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            when = ''
        desc = (item.findtext('description') or '').strip()
        publisher = desc.split(' -- ', 1)[0].strip() if ' -- ' in desc[:60] else 'Yahoo Finance'
        link = (item.findtext('link') or '').strip()
        out.append({"title": title[:160], "publisher": publisher[:60], "published": when,
                    "url": link if link.startswith('http') else ''})
        if len(out) >= limit:
            break
    return out


def _news_about(ticker: str, info: Optional[Dict[str, Any]],
                items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[Why picked] News items whose subject is this company.

    The per-symbol feed carries stories that only mention the company in
    passing ("... with Webex by Cisco", a market report listing ten names).
    Keep an item when its title carries "(TICKER)" or names the company or
    the ticker within its first ``WHY.NEWS_SUBJECT_WORDS`` words.
    """
    info = info or {}
    root = str(ticker).split('.')[0].upper()
    stop_words = {'inc', 'corp', 'corporation', 'company', 'ltd', 'limited', 'plc',
                  'the', 'group', 'holdings', 'class', 'common', 'stock'}
    names = [w.lower() for w in re.split(r"[^A-Za-z0-9&]+", str(info.get('shortName') or info.get('longName') or ''))
             if len(w) >= 3 and w.lower() not in stop_words][:1]
    window = int(_T.WHY.NEWS_SUBJECT_WORDS)
    out = []
    for item in items or []:
        title = str(item.get('title') or '')
        if f"({root})" in title.upper():
            out.append(item)
            continue
        words = [w for w in re.split(r"[^A-Za-z0-9&.']+", title) if w][:window]
        lowered = [re.sub(r"'s$|'$", "", w.lower()) for w in words]
        if root.lower() in lowered or any(n in lowered for n in names):
            out.append(item)
    return out


def _headlines_about(ticker: str, info: Optional[Dict[str, Any]],
                     headlines: List[str]) -> List[str]:
    """Keep only headlines that name the company.

    yfinance's news feed for a ticker is full of market-wide and competitor
    stories (BA scored a catalyst on "NASA's $946 Million SpaceX Award"; the
    GOOG card showed "This is Humanity's Last Decision"). Only a headline that
    names the company can be ITS news.
    """
    info = info or {}
    root = str(ticker).split('.')[0].upper()
    stop_words = {
        'inc', 'corp', 'corporation', 'company', 'ltd', 'limited', 'plc',
        'the', 'group', 'holdings', 'class', 'common', 'stock',
    }
    name_tokens = [
        w for w in re.split(r"[^A-Za-z0-9&]+", str(info.get('shortName') or info.get('longName') or ''))
        if len(w) >= 3 and w.lower() not in stop_words
    ][:2]
    out = []
    for h in headlines or []:
        text = str(h)
        if re.search(rf"(?<![A-Za-z]){re.escape(root)}(?![A-Za-z])", text):
            out.append(h)
        elif name_tokens and name_tokens[0].lower() in text.lower():
            out.append(h)
    return out


def _extract_next_earnings_date(calendar: Any) -> Optional[pd.Timestamp]:
    """Extract the next earnings date from yfinance DataFrame/Series/dict shapes."""
    if calendar is None:
        return None

    candidates: List[Any] = []

    try:
        # Match the DATE key only. A substring match on 'earnings' also took
        # 'Earnings High/Low/Average' — EPS estimates — which pd.to_datetime
        # turns into 1970-01-01.
        def _is_date_key(key: Any) -> bool:
            return str(key).strip().lower() in {'earnings date', 'earnings_date', 'earningsdate'}

        if isinstance(calendar, dict):
            for key, value in calendar.items():
                if _is_date_key(key):
                    candidates.append(value)
        elif isinstance(calendar, pd.Series):
            for key, value in calendar.items():
                if _is_date_key(key):
                    candidates.append(value)
        elif isinstance(calendar, pd.DataFrame):
            if calendar.empty:
                return None
            if 'Earnings Date' in calendar.index:
                candidates.append(calendar.loc['Earnings Date'])
            if 'Earnings Date' in calendar.columns:
                candidates.extend(calendar['Earnings Date'].dropna().tolist())
            for idx in calendar.index:
                if _is_date_key(idx) and idx != 'Earnings Date':
                    row = calendar.loc[idx]
                    if hasattr(row, 'tolist'):
                        candidates.extend(row.tolist())
                    else:
                        candidates.append(row)
        else:
            return None
    except Exception:
        return None

    flattened: List[Any] = []
    for candidate in candidates:
        if isinstance(candidate, (list, tuple, set)):
            flattened.extend(candidate)
        elif hasattr(candidate, 'tolist') and not isinstance(candidate, (str, bytes, datetime, pd.Timestamp)):
            value = candidate.tolist()
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        else:
            flattened.append(candidate)

    dates: List[pd.Timestamp] = []
    for value in flattened:
        try:
            if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
                continue
            if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
                continue  # a number is never a calendar date here
            ts = pd.to_datetime(value, errors='coerce')
            if pd.notna(ts):
                dates.append(pd.Timestamp(ts).tz_localize(None) if getattr(ts, 'tzinfo', None) else pd.Timestamp(ts))
        except Exception:
            continue

    if not dates:
        return None

    today = pd.Timestamp(datetime.now().date())
    future_dates = [d for d in dates if d.normalize() >= today]
    # Only a FUTURE date is information. A past-only calendar used to return
    # that past date, making days_to_earnings negative and calendar_known
    # True — full size, instead of the unknown-calendar haircut.
    return min(future_dates) if future_dates else None


class CircuitBreaker:
    """
    **v6.0 FIX #5.2: Circuit Breaker Pattern for API Resilience**
    **FIX #14: Thread-safe implementation with locks**
    **FIX #8: Atomic acquire/release to eliminate TOCTOU race**
    
    States: CLOSED (normal), OPEN (failing), HALF-OPEN (testing)
    Opens after threshold failures, auto-resets after timeout.
    
    Usage:
        if cb.acquire_permit():
            try:
                result = api_call()
                cb.release_permit(success=True)
            except Exception:
                cb.release_permit(success=False)
        else:
            # Circuit is open, skip request
    """
    
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
    
    def __init__(self, failure_threshold: int = 10, reset_timeout: int = 60, 
                 consecutive_threshold: int = 20, pause_duration: int = 30):
        import threading
        self._lock = threading.Lock()  # FIX #14: Thread safety
        
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.consecutive_threshold = consecutive_threshold
        self.pause_duration = pause_duration
        
        self.state = self.CLOSED
        self.failure_count = 0
        self.consecutive_failures = 0
        self.last_failure_time = None
        self.last_success_time = None
        self._half_open_in_flight = False  # Track HALF_OPEN test request
    
    def acquire_permit(self) -> bool:
        """
        Atomically check state and acquire a permit to make a request.
        Eliminates TOCTOU race by combining check + state transition under one lock.
        Returns True if caller may proceed with the request.
        """
        with self._lock:
            if self.state == self.CLOSED:
                return True
            
            if self.state == self.OPEN:
                # Check if reset timeout has passed
                if self.last_failure_time and \
                   (time.time() - self.last_failure_time) >= self.reset_timeout:
                    self.state = self.HALF_OPEN
                    self._half_open_in_flight = True
                    logger.info("⚡ Circuit breaker HALF_OPEN - testing recovery")
                    return True
                return False
            
            # HALF_OPEN: Only allow ONE concurrent test request
            if self._half_open_in_flight:
                return False  # Another thread is already testing
            self._half_open_in_flight = True
            return True
    
    def release_permit(self, success: bool) -> bool:
        """
        Release the permit after a request completes.
        Updates state based on outcome. Returns True if caller should pause
        (consecutive failure threshold hit).
        """
        with self._lock:
            self._half_open_in_flight = False
            
            if success:
                self.failure_count = 0
                self.consecutive_failures = 0
                self.state = self.CLOSED
                self.last_success_time = time.time()
                return False
            else:
                self.failure_count += 1
                self.consecutive_failures += 1
                self.last_failure_time = time.time()
                
                if self.failure_count >= self.failure_threshold:
                    self.state = self.OPEN
                    logger.warning(f"⚡ Circuit breaker OPEN after {self.failure_count} failures")
                
                if self.consecutive_failures >= self.consecutive_threshold:
                    logger.warning(f"⚡ {self.consecutive_failures} consecutive failures - pausing for {self.pause_duration}s")
                    return True
                
                return False
    
    # === Backward-compatible wrappers (used by scan_market result loop) ===
    def should_allow_request(self) -> bool:
        """Legacy check — prefer acquire_permit() for new code."""
        return self.acquire_permit()
    
    def record_success(self):
        """Legacy — prefer release_permit(success=True)."""
        self.release_permit(success=True)
    
    def record_failure(self) -> bool:
        """Legacy — prefer release_permit(success=False)."""
        return self.release_permit(success=False)
    
    def reset(self):
        """Reset the circuit breaker to initial state (thread-safe)"""
        with self._lock:
            self.state = self.CLOSED
            self.failure_count = 0
            self.consecutive_failures = 0
            self.last_failure_time = None
            self._half_open_in_flight = False


# ============================================================================
# DATA VALIDATION MODULE [v7.0 PORT]
# ============================================================================

@dataclass
class ValidationResult:
    """Result of data validation"""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cleaned_data: Optional[pd.DataFrame] = None


class DataValidator:
    """
    [v7.0 FIX] Comprehensive data validation and cleaning
    Prevents NaN propagation, catches price spikes, validates OHLC relationships
    """
    
    MAX_SINGLE_DAY_MOVE = 0.50  # 50% max single-day move
    MAX_GAP_DAYS = 3  # Max consecutive missing days to forward-fill

    @staticmethod
    def completed_daily_history(hist: pd.DataFrame, market: str = "US",
                                now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """Exclude the still-forming daily candle before strict validation."""
        if hist is None or hist.empty:
            return hist
        zone = 'Asia/Kolkata' if market == 'IN' else 'America/New_York'
        current = pd.Timestamp.now(tz=zone) if now is None else pd.Timestamp(now)
        current = current.tz_localize(zone) if current.tzinfo is None else current.tz_convert(zone)
        close_minutes = 15 * 60 + 30 if market == 'IN' else 16 * 60
        if current.hour * 60 + current.minute >= close_minutes:
            return hist
        dates = pd.DatetimeIndex(hist.index)
        if dates.tz is not None:
            dates = dates.tz_convert(zone)
        keep = dates.date != current.date()
        return hist.loc[keep].copy() if not keep.all() else hist
    
    # Split / bonus ratios seen in practice (forward and reverse are both
    # covered by testing the ratio and its inverse). 1.5, 4/3 and 5/4 are the
    # 3:2, 4:3 and 5:4 bonus issues that are common on NSE.
    SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 20.0, 1.5, 4.0 / 3.0, 1.25)
    SPLIT_SCREEN_MOVE = 0.19   # a 5:4 bonus is a -20% day

    @classmethod
    def split_like_days(cls, close: pd.Series, volume: Optional[pd.Series],
                        require_volume: bool = True) -> List[int]:
        """Positional indices of days that look like an UNADJUSTED split.

        Signature: the close-to-close price ratio lands on a common split
        ratio (or its reciprocal) AND volume scales by roughly the same
        factor. The previous screen required a move strictly above 50%, which
        missed an exact 2:1 split (-50.0%) and every 3:2 / 5:4 bonus issue,
        and matched only integer ratios.

        ``require_volume=False`` (analytics callers without trustworthy
        volume) accepts the price signature alone, but only for moves beyond
        MAX_SINGLE_DAY_MOVE — a -20% day landing near 5:4 is too common to
        call a split on price alone.
        """
        out: List[int] = []
        try:
            close = pd.to_numeric(close, errors='coerce')
            volume = pd.to_numeric(volume, errors='coerce') if volume is not None else None
            returns = close.pct_change(fill_method=None)
            screen = (returns.abs() >= cls.SPLIT_SCREEN_MOVE).fillna(False).to_numpy()
            big = (returns.abs() > cls.MAX_SINGLE_DAY_MOVE).fillna(False).to_numpy()
            for idx in np.flatnonzero(screen):
                if idx == 0:
                    continue
                prev_close = float(close.iloc[idx - 1])
                curr_close = float(close.iloc[idx])
                if not (prev_close > 0 and curr_close > 0):
                    continue
                ratio = prev_close / curr_close          # >1 for a forward split
                near = min(
                    min(abs(ratio - r), abs(1.0 / ratio - r)) / r
                    for r in cls.SPLIT_RATIOS
                )
                if near >= 0.02:
                    continue
                if not require_volume and big[idx]:
                    out.append(int(idx))
                    continue
                if volume is None:
                    continue
                prev_vol = float(volume.iloc[idx - 1]) if np.isfinite(volume.iloc[idx - 1]) else 0.0
                curr_vol = float(volume.iloc[idx]) if np.isfinite(volume.iloc[idx]) else 0.0
                vol_ratio = (curr_vol / prev_vol) if prev_vol > 0 else 0.0
                # A split multiplies share count, so volume scales inversely
                # by roughly the price factor. A news gap does not.
                if vol_ratio > 0 and abs(vol_ratio - ratio) / max(ratio, 1e-9) < 0.35:
                    out.append(int(idx))
        except Exception as e:
            logger.debug(f"split_like_days failed: {e}")
        return out

    @classmethod
    def validate_history(cls, hist: pd.DataFrame, ticker: str = "",
                         max_age_days: Optional[int] = None) -> ValidationResult:
        """Validate historical price data quality"""
        errors = []
        warnings = []
        
        if hist is None or hist.empty:
            return ValidationResult(is_valid=False, errors=["Empty or None dataframe"])
        
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        missing_cols = [c for c in required_cols if c not in hist.columns]
        if missing_cols:
            return ValidationResult(is_valid=False, errors=[f"Missing columns: {missing_cols}"])

        numeric_hist = hist[required_cols].apply(pd.to_numeric, errors='coerce')

        try:
            timestamps = pd.to_datetime(hist.index, errors='coerce')
            if timestamps.isna().any():
                errors.append(f"{ticker}: history index contains invalid timestamps")
            if hist.index.has_duplicates:
                warnings.append(f"{ticker}: history index contains duplicate timestamps")
            if not hist.index.is_monotonic_increasing:
                warnings.append(f"{ticker}: history index is not chronological")
        except Exception:
            errors.append(f"{ticker}: history index cannot be interpreted as timestamps")
        
        # Check for NaN values
        nan_counts = numeric_hist.isna().sum()
        total_rows = len(hist)
        for col, count in nan_counts.items():
            if count > 0:
                pct = (count / total_rows) * 100
                if pct > 20:
                    errors.append(f"{ticker}: {col} has {pct:.1f}% NaN values")
                elif pct > 5:
                    warnings.append(f"{ticker}: {col} has {pct:.1f}% NaN values")
        
        # Prices must be finite and strictly positive.  Zero is not a tradable
        # price and can otherwise create infinite returns or divide-by-zero
        # artifacts downstream.
        for col in ['Open', 'High', 'Low', 'Close']:
            numeric = numeric_hist[col]
            invalid_count = ((numeric <= 0) | np.isinf(numeric)).sum()
            if invalid_count > 0:
                errors.append(f"{ticker}: {col} has {invalid_count} non-positive/non-finite values")

        volume = numeric_hist['Volume']
        invalid_volume = ((volume < 0) | np.isinf(volume)).sum()
        if invalid_volume > 0:
            errors.append(f"{ticker}: Volume has {invalid_volume} negative/non-finite values")
        
        # Check OHLC relationship
        invalid_ohlc = (
            (numeric_hist['High'] < numeric_hist['Low']) |
            (numeric_hist['High'] < numeric_hist['Open']) |
            (numeric_hist['High'] < numeric_hist['Close']) |
            (numeric_hist['Low'] > numeric_hist['Open']) |
            (numeric_hist['Low'] > numeric_hist['Close'])
        ).sum()
        if invalid_ohlc > 0:
            errors.append(f"{ticker}: {invalid_ohlc} rows with invalid OHLC relationships")
        
        # [M8 FIX] Any single day above MAX_SINGLE_DAY_MOVE used to be an
        # ERROR, disqualifying the whole ticker. But yfinance is already split-
        # and dividend-adjusted, so most such moves are REAL — M&A, biotech
        # readouts, guidance resets — and the filter silently removed exactly
        # the highest-catalyst names, biasing the universe toward the placid.
        # The rejection was also indistinguishable from a genuine data fault in
        # the funnel stats.
        #
        # An unadjusted split has a signature: the price ratio lands near a
        # simple integer or its reciprocal (2:1, 3:1, 1:10 ...) WITHOUT a
        # matching inverse move in volume. A real news gap does not. Only the
        # split signature is treated as an error; a large clean move is a
        # warning that travels with the analysis.
        if len(hist) > 1:
            close = numeric_hist['Close']
            volume = numeric_hist['Volume']
            returns = close.pct_change(fill_method=None)
            split_idx = set(cls.split_like_days(close, volume))
            big = returns.abs() > cls.MAX_SINGLE_DAY_MOVE
            split_days = len(split_idx)
            news_days = int(sum(
                1 for idx in np.flatnonzero(big.fillna(False).to_numpy())
                if idx not in split_idx
            ))

            if split_days:
                errors.append(
                    f"{ticker}: {split_days} day(s) with a near-integer price ratio and "
                    "matching volume scaling; probable unadjusted split/corporate action"
                )
            if news_days:
                warnings.append(
                    f"{ticker}: {news_days} day(s) with a >{cls.MAX_SINGLE_DAY_MOVE*100:.0f}% "
                    "move that does not look like a split (event risk, not a data fault)"
                )

        # Only the live scanner supplies max_age_days.  Keeping this optional
        # lets deterministic historical/backtest fixtures remain valid while
        # preventing stale provider snapshots from becoming current trades.
        if max_age_days is not None:
            try:
                last_timestamp = pd.Timestamp(hist.index.max())
                if pd.isna(last_timestamp):
                    raise ValueError("missing latest timestamp")
                # datetime.now, the clock every other freshness rule reads (and
                # the one tests can freeze), not pandas' separate one.
                now = (pd.Timestamp(datetime.now(timezone.utc)).tz_convert(last_timestamp.tz)
                       if last_timestamp.tzinfo else pd.Timestamp(datetime.now()))
                age_days = (now.normalize() - last_timestamp.normalize()).days
                if age_days > max_age_days:
                    errors.append(
                        f"{ticker}: latest bar is {age_days} days old (maximum {max_age_days})"
                    )
            except Exception:
                errors.append(f"{ticker}: history index has no valid market timestamp")
        
        is_valid = len(errors) == 0
        return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)
    
    @classmethod
    def clean_history(cls, hist: pd.DataFrame) -> pd.DataFrame:
        """Clean historical data - remove bad rows, forward-fill small gaps"""
        if hist is None or hist.empty:
            return hist
        
        df = hist.copy()
        try:
            df = df[~df.index.duplicated(keep='last')].sort_index()
        except Exception:
            return df.iloc[0:0]
        
        # Remove rows that cannot represent a real OHLC bar.
        price_cols = ['Open', 'High', 'Low', 'Close']
        for col in price_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df[np.isfinite(df[col]) & (df[col] > 0)]
        if 'Volume' in df.columns:
            df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce')
            df = df[np.isfinite(df['Volume']) & (df['Volume'] >= 0)]
        
        # Forward-fill small gaps (max 3 days)
        df = df.ffill(limit=cls.MAX_GAP_DAYS)
        
        # Drop remaining NaN rows
        df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])

        if all(col in df.columns for col in price_cols):
            valid_ohlc = (
                (df['High'] >= df['Low']) &
                (df['High'] >= df['Open']) &
                (df['High'] >= df['Close']) &
                (df['Low'] <= df['Open']) &
                (df['Low'] <= df['Close'])
            )
            df = df[valid_ohlc]
        
        return df


# ============================================================================
# RISK LIMITS MODULE [NEW]
# ============================================================================

class RiskLimits:
    """
    Institutional-grade risk limits and checks.
    Implements VaR limits, correlation checks, liquidity filters.
    """
    
    # VaR Settings
    VAR_CONFIDENCE = 0.99  # 99% VaR
    VAR_LIMIT_PCT = 0.02   # 2% of account max daily VaR
    
    # Correlation Settings
    MAX_PAIRWISE_CORRELATION = 0.70  # Block if > 0.7
    MIN_CORRELATION_PERIOD = 60  # Days for correlation calculation
    
    # Liquidity Settings
    MIN_ADV_MULTIPLIER = 20  # Position must be < 5% of ADV (ADV > 20× position)
    
    # Drawdown Scaling
    DRAWDOWN_THRESHOLDS = {
        0.05: 0.75,  # 5% DD → 75% exposure
        0.10: 0.50,  # 10% DD → 50% exposure
        0.15: 0.00,  # 15% DD → halt trading
    }
    
    @classmethod
    def check_var_limit(cls, position_size: float, volatility: float, 
                        capital: float, holding_period_days: int = 1,
                        daily_returns: Optional[np.ndarray] = None) -> Tuple[bool, float]:
        """
        [FIX #12] Historical CVaR replaces parametric VaR.

        If *daily_returns* (a numpy array of historical daily log or simple
        returns) is supplied, we compute the 1st-percentile VaR and CVaR
        (Expected Shortfall) from the empirical distribution.  The CVaR is
        the average of losses beyond the VaR cutoff, which better captures
        tail risk than the parametric 2.33σ assumption.

        Falls back to the old parametric formula when no return series is
        available.

        Returns:
            (passes: bool, var_pct: float as % of capital)
        """
        try:
            position_size = float(position_size)
            volatility = float(volatility)
            capital = float(capital)
            holding_period_days = int(holding_period_days)
            if (not np.isfinite(position_size) or position_size <= 0 or
                    not np.isfinite(volatility) or volatility < 0 or
                    not np.isfinite(capital) or capital <= 0 or
                    holding_period_days <= 0):
                return False, float('inf')

            if daily_returns is not None and len(daily_returns) >= 30:
                # --- Historical CVaR (Expected Shortfall at 99%) ---
                return_array = np.asarray(daily_returns, dtype=float)
                clean = return_array[np.isfinite(return_array)]
                if len(clean) >= 30:
                    var_cutoff = float(np.percentile(clean, 1))        # 1st percentile
                    tail = clean[clean <= var_cutoff]
                    cvar = float(np.mean(tail)) if len(tail) > 0 else var_cutoff
                    # Scale to holding period and position
                    position_cvar = abs(cvar) * np.sqrt(holding_period_days) * position_size
                    var_pct = position_cvar / capital if capital > 0 else 1.0
                    passes = var_pct <= cls.VAR_LIMIT_PCT
                    return passes, var_pct

            # --- Fallback: parametric VaR at 99% ---
            z_score = 2.33
            daily_vol = max(volatility, 0.05) / np.sqrt(252)  # [FIX #18] floor vol
            position_var = position_size * daily_vol * z_score * np.sqrt(holding_period_days)
            var_pct = position_var / capital if capital > 0 else 1.0
            passes = var_pct <= cls.VAR_LIMIT_PCT
            return passes, var_pct
        except Exception:
            return False, float('inf')
    
    @classmethod
    def check_correlation(cls, new_ticker: str, existing_tickers: List[str],
                          hist_data: Dict[str, pd.DataFrame]) -> Tuple[bool, float, str]:
        """
        Check if new position is too correlated with existing holdings.
        
        Returns:
            (passes: bool, max_corr: float, most_correlated_ticker: str)
        """
        try:
            if not existing_tickers:
                return True, 0.0, ""
            if new_ticker not in hist_data or 'Close' not in hist_data[new_ticker]:
                return False, 1.0, "UNAVAILABLE"
            
            new_returns = hist_data[new_ticker]['Close'].pct_change(fill_method=None).dropna()
            max_corr = 0.0
            most_corr_ticker = ""
            
            assessed_pairs = 0
            for ticker in existing_tickers:
                if ticker in hist_data and ticker != new_ticker:
                    if 'Close' not in hist_data[ticker]:
                        continue
                    existing_returns = hist_data[ticker]['Close'].pct_change(fill_method=None).dropna()
                    
                    # Align dates
                    aligned = pd.DataFrame({
                        'new': new_returns,
                        'existing': existing_returns
                    }).dropna()
                    
                    if len(aligned) >= cls.MIN_CORRELATION_PERIOD:
                        corr = aligned['new'].corr(aligned['existing'])
                        if not np.isfinite(corr):
                            continue
                        assessed_pairs += 1
                        if abs(corr) > max_corr:
                            max_corr = abs(corr)
                            most_corr_ticker = ticker
            
            if assessed_pairs == 0:
                return False, 1.0, "UNAVAILABLE"
            passes = max_corr < cls.MAX_PAIRWISE_CORRELATION
            return passes, max_corr, most_corr_ticker
        except Exception:
            return False, 1.0, "UNAVAILABLE"
    
    @classmethod
    def check_liquidity(cls, position_size: float, avg_daily_volume: float,
                        current_price: float) -> Tuple[bool, float]:
        """
        Check if position is small enough relative to daily volume.
        
        Returns:
            (passes: bool, position_as_pct_of_adv: float)
        """
        try:
            position_size = float(position_size)
            avg_daily_volume = float(avg_daily_volume)
            current_price = float(current_price)
            if (not np.isfinite(position_size) or position_size <= 0 or
                    not np.isfinite(avg_daily_volume) or avg_daily_volume <= 0 or
                    not np.isfinite(current_price) or current_price <= 0):
                return False, 100.0
            avg_dollar_volume = avg_daily_volume * current_price
            min_required_adv = position_size * cls.MIN_ADV_MULTIPLIER
            
            passes = avg_dollar_volume >= min_required_adv
            pct_of_adv = (position_size / avg_dollar_volume * 100) if avg_dollar_volume > 0 else 100
            
            return passes, pct_of_adv
        except Exception:
            return False, 100.0
    
    @classmethod
    def get_drawdown_multiplier(cls, current_drawdown: float) -> float:
        """
        Get exposure multiplier based on current drawdown level.
        
        Args:
            current_drawdown: Positive float (e.g., 0.05 = 5% drawdown)
        
        Returns:
            Exposure multiplier (0.0 to 1.0)
        """
        for threshold, multiplier in sorted(cls.DRAWDOWN_THRESHOLDS.items(), reverse=True):
            if current_drawdown >= threshold:
                return multiplier
        return 1.0
    
    @classmethod
    def calculate_atr_trailing_stop(cls, current_high: float, atr: float,
                                     atr_multiplier: float = 1.75) -> float:
        """
        Calculate ATR-based trailing stop.
        
        Args:
            current_high: Highest price since entry
            atr: Average True Range
            atr_multiplier: 1.5-2.0 range recommended
        
        Returns:
            Trailing stop price
        """
        return current_high - (atr * atr_multiplier)


def _align_returns_safely(stock_returns: pd.Series, 
                          benchmark_returns: pd.Series,
                          min_points: int = 30) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """
    **v6.0 FIX #1.2: Safely align stock and benchmark returns**
    
    Handles timezone mismatches and date normalization for accurate beta/alpha.
    
    Args:
        stock_returns: Stock daily returns series
        benchmark_returns: Benchmark daily returns series
        min_points: Minimum aligned points required (default: 30)
    
    Returns:
        Tuple of (aligned_stock, aligned_benchmark) or (None, None) if alignment fails
    """
    try:
        if stock_returns is None or benchmark_returns is None:
            return None, None
        
        if len(stock_returns) < min_points or len(benchmark_returns) < min_points:
            return None, None
        
        # Convert both to timezone-naive
        stock_idx = stock_returns.index
        bench_idx = benchmark_returns.index
        
        if hasattr(stock_idx, 'tz') and stock_idx.tz is not None:
            stock_returns = stock_returns.copy()
            stock_returns.index = stock_idx.tz_localize(None)
        
        if hasattr(bench_idx, 'tz') and bench_idx.tz is not None:
            benchmark_returns = benchmark_returns.copy()
            benchmark_returns.index = bench_idx.tz_localize(None)
        
        # Normalize to date-only (remove time component)
        stock_returns = stock_returns.copy()
        benchmark_returns = benchmark_returns.copy()
        
        stock_returns.index = pd.to_datetime(stock_returns.index).date
        benchmark_returns.index = pd.to_datetime(benchmark_returns.index).date
        
        # Remove duplicates (keep last)
        stock_returns = stock_returns[~stock_returns.index.duplicated(keep='last')]
        benchmark_returns = benchmark_returns[~benchmark_returns.index.duplicated(keep='last')]
        
        # Perform inner join on common dates
        common_dates = set(stock_returns.index) & set(benchmark_returns.index)
        
        if len(common_dates) < min_points:
            logger.debug(f"Alignment failed: only {len(common_dates)} common dates (need {min_points})")
            return None, None
        
        common_dates = sorted(common_dates)
        aligned_stock = stock_returns.loc[common_dates]
        aligned_bench = benchmark_returns.loc[common_dates]
        
        return aligned_stock, aligned_bench
        
    except Exception as e:
        logger.debug(f"Return alignment failed: {e}")
        return None, None


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

class _SharedRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that tolerates a log file held by another process.

    Windows refuses to rename an open file, so when a second QUANTA process is
    running (the webapp alongside the CLI, or two scans at once) the rollover
    raises ``PermissionError: [WinError 32]`` and the logging machinery prints a
    full traceback to stderr — mid-scan, interleaved with the progress bar. The
    record itself is written either way; only the rename fails.

    A failed rollover is not worth a traceback and must never look like a scan
    error, so absorb it and keep writing to the current file. The size cap is
    then advisory for as long as the other process holds the handle, which is
    the right trade: losing the cap is recoverable, losing the log line is not.
    """

    def doRollover(self) -> None:  # noqa: N802 - stdlib naming
        try:
            super().doRollover()
        except (PermissionError, OSError):
            # Reopen the current file so logging continues even though the
            # rename failed; without this the handler can be left with a
            # closed stream.
            if self.stream is None:
                self.stream = self._open()


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure professional-grade logging with rotation and UTF-8 support"""
    logger = logging.getLogger("QUANTA")
    logger.setLevel(getattr(logging, log_level))

    # [FIX] Prevent messages from propagating to root logger
    # Root logger may have handlers added by yfinance/polygon/other libs,
    # causing cascading duplicate output with INFO:QUANTA: format.
    logger.propagate = False

    # Prevent duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # File handler with rotation (UTF-8 encoding for emojis)
    file_handler = _SharedRotatingFileHandler(
        log_dir / "quanta_trading.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(funcName)-20s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)

    # Console handler — use sys.stderr to avoid buffer conflicts with print()
    # [FIX] Do NOT wrap sys.stdout.buffer in a second TextIOWrapper — that
    # creates two wrappers on the same raw buffer causing cascading duplicate
    # output.  stderr is safer and won't interleave with Rich/tqdm output.
    import io
    console_stream = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(logging.INFO)  # Show progress on console
    console_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logging()


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to ``default`` on any error."""
    try:
        raw = (os.getenv(name) or "").strip()
        return int(raw) if raw else int(default)
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to ``default`` on any error."""
    try:
        raw = (os.getenv(name) or "").strip()
        return float(raw) if raw else float(default)
    except (TypeError, ValueError):
        return float(default)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True if ``exc`` looks like a data-provider throttle (429 / YFRateLimitError).

    Detects yfinance's ``YFRateLimitError`` (by class name, so we never need a
    hard import that could break on version drift) plus the common textual
    signatures ('429', 'Too Many Requests', 'rate limit') seen across requests,
    urllib3 and yfinance.
    """
    name = type(exc).__name__.lower()
    if "ratelimit" in name:  # yfinance.exceptions.YFRateLimitError
        return True
    low = (str(exc) or "").lower()
    return ("429" in low or "too many request" in low or "rate limit" in low
            or "rate-limit" in low or "ratelimit" in low or "throttle" in low)


# Every ``error_type`` ``_classify_scan_error`` can return. These share the
# ``_filter_stats`` dict with the business filters so the funnel can show them,
# but they are infrastructure failures, not filter decisions, and the scan
# summary already reports them as errors. Kept here so both readers of the dict
# agree on which keys are which. ``test_scan_health`` pins this to the
# classifier so the two cannot drift apart.
_SCAN_ERROR_TYPES = frozenset({
    "provider_rate_limited", "data_provider_error", "missing_price_data",
    "import_error", "serialization_error", "timeout", "analysis_exception",
})


def _first_float_in(lines: List[str], pattern: str) -> Optional[float]:
    """First capture group in ``lines`` matching ``pattern``, parsed as a float.

    Used to read a figure back out of a human-readable reason string. Returns
    None when nothing matches, so callers can tell "not quoted" from "zero".
    """
    import re
    for line in lines or ():
        match = re.search(pattern, str(line))
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _extract_retry_after(exc: BaseException) -> Optional[float]:
    """Best-effort parse of a ``Retry-After`` value (seconds) from an exception."""
    import re
    match = re.search(r"retry[-\s]?after[:=\s]+(\d+(?:\.\d+)?)", str(exc or ""), re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


class ProviderRateLimitTracker:
    """Process-wide, thread-safe view of data-provider (yfinance) throttling.

    Both the scanner and the webapp ``/api/health`` probe read this so the
    dashboard can show an honest ``RATE_LIMITED`` state with a cooldown
    countdown instead of a generic ``DEGRADED``. Intentionally lightweight:
    counters, a cooldown deadline and the last successful-fetch timestamp.

    This tracker NEVER touches scoring, selection or trading logic — it only
    governs how aggressively the data layer is allowed to hit the provider.
    """

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self.rate_limit_hits = 0
        self.consecutive_rate_limits = 0
        self.total_failures = 0
        self.last_rate_limit_time: Optional[float] = None
        self.last_success_time: Optional[float] = None
        self.cooldown_until: float = 0.0
        self.slow_responses = 0
        self.provider = "yfinance"

    def note_success(self) -> None:
        with self._lock:
            self.consecutive_rate_limits = 0
            self.last_success_time = time.time()

    def note_rate_limit(self, retry_after: Optional[float] = None) -> None:
        with self._lock:
            now = time.time()
            self.rate_limit_hits += 1
            self.consecutive_rate_limits += 1
            self.total_failures += 1
            self.last_rate_limit_time = now
            cooldown = max(float(retry_after or 0.0),
                           float(TradingConfig.RATE_LIMIT_COOLDOWN_SECONDS))
            # Extend (never shorten) the existing cooldown window.
            self.cooldown_until = max(self.cooldown_until, now + cooldown)

    def note_slow(self) -> None:
        """Record a call that was too slow to wait for — NOT a rate limit.

        A request we abandoned on our own budget proves nothing about the
        provider's quota: no 429 was seen and no ``Retry-After`` was given.
        Recording it as a rate limit opened a process-wide cooldown that made
        every subsequent request answer "Provider rate-limited" and the health
        probe report throttling that had never been observed. Counted for
        telemetry only; it never opens a cooldown.
        """
        with self._lock:
            self.slow_responses += 1

    def note_failure(self) -> None:
        with self._lock:
            self.total_failures += 1

    def in_cooldown(self) -> bool:
        with self._lock:
            return time.time() < self.cooldown_until

    def cooldown_remaining(self) -> float:
        with self._lock:
            return max(0.0, self.cooldown_until - time.time())

    def reset(self) -> None:
        """Clear cooldown + consecutive counter (e.g. on an explicit user retry)."""
        with self._lock:
            self.cooldown_until = 0.0
            self.consecutive_rate_limits = 0

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            return {
                "provider": self.provider,
                "rate_limited": now < self.cooldown_until,
                "rate_limit_hits": self.rate_limit_hits,
                "consecutive_rate_limits": self.consecutive_rate_limits,
                "slow_responses": self.slow_responses,
                "total_failures": self.total_failures,
                "cooldown_remaining_seconds": round(max(0.0, self.cooldown_until - now), 1),
                "last_rate_limit_time": self.last_rate_limit_time,
                "last_success_time": self.last_success_time,
            }


# Module-level singleton shared by the scanner and the webapp health probe.
PROVIDER_STATE = ProviderRateLimitTracker()


class AdaptivePacer:
    """Global AIMD pacer for provider request rate.

    Why this exists: the SEC EDGAR catalyst lookup used to stall ~10s on every
    scored ticker, which accidentally throttled yfinance to a rate it tolerated.
    Removing that stall (the endpoint was deprecated and timing out) made the
    scanner ~3.5x faster and it immediately started collecting
    ``YFRateLimitError`` — the provider circuit tripped 251 names into a
    669-name universe.

    A fixed sleep would just re-introduce the stall at a hand-guessed value.
    Instead this converges on whatever the provider currently tolerates:
    additive decrease of the inter-ticker gap while requests succeed,
    multiplicative increase the moment one is rate-limited. Shared across the
    scan's worker threads, so the gap is a global dispatch rate, not per-thread.
    """

    def __init__(self, start: float, minimum: float, maximum: float,
                 backoff: float, recover: float, recover_after: int):
        self._interval = max(minimum, min(maximum, start))
        self._min = minimum
        self._max = maximum
        self._backoff = backoff
        self._recover = recover
        self._recover_after = max(1, recover_after)
        self._ok_streak = 0
        self._next_slot = 0.0
        self._lock = threading.Lock()

    @property
    def interval(self) -> float:
        return self._interval

    def wait(self) -> None:
        """Block until this thread's dispatch slot, then reserve the next."""
        with self._lock:
            now = time.time()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
            delay = slot - now
        if delay > 0:
            time.sleep(delay)

    def note_rate_limit(self) -> None:
        with self._lock:
            self._ok_streak = 0
            before = self._interval
            self._interval = min(self._max, self._interval * self._backoff)
            if self._interval > before:
                logger.debug(
                    "Pacer: rate limit observed, dispatch gap %.2fs -> %.2fs",
                    before, self._interval)

    def note_success(self) -> None:
        with self._lock:
            self._ok_streak += 1
            if self._ok_streak >= self._recover_after and self._interval > self._min:
                self._ok_streak = 0
                self._interval = max(self._min, self._interval - self._recover)

    def reset(self) -> None:
        with self._lock:
            self._interval = max(self._min, min(self._max, TradingConfig.PACER_START_INTERVAL))
            self._ok_streak = 0
            self._next_slot = 0.0


# [F9] Thresholds live in engine/thresholds.py; TradingConfig keeps its names as
# read-through aliases so existing call sites and tests keep working.
from engine import thresholds as _T  # noqa: E402


def _listing_currency(ticker: Any) -> str:
    """[Picks revamp] The symbol a level is quoted in: rupees for NSE/BSE listings."""
    return "₹" if str(ticker or "").upper().endswith((".NS", ".BO")) else "$"
from engine.style_screen import STYLE_OF_SCAN, screen as style_screen  # noqa: E402
from engine import scan_filters  # noqa: E402


class _ScannerTicker:
    """Observe provider reads without changing retries, pacing or eligibility.

    Unlike Main's wrapper, no values/exceptions are cached and no cooldown is
    imposed here. History calls with different arguments remain independent;
    fast_info is optional because PRO can obtain market cap from info.
    """

    def __init__(self, ticker: Any, owner: Any, symbol: str):
        self._ticker, self._owner, self._symbol = ticker, owner, symbol

    def _read(self, name: str, fetch: Any) -> Any:
        try:
            value = fetch()
        except Exception as exc:
            self._owner._record_provider_read(self._symbol, name, "failed", type(exc).__name__)
            raise
        if not callable(value):
            empty = (value is None or
                     isinstance(value, (dict, list, tuple, pd.DataFrame, pd.Series)) and len(value) == 0)
            self._owner._record_provider_read(self._symbol, name, "empty" if empty else "available")
        return value

    def __getattr__(self, name: str) -> Any:
        value = self._read(name, lambda: getattr(self._ticker, name))
        if callable(value):
            @wraps(value)
            def call(*args: Any, **kwargs: Any) -> Any:
                return self._read(name, lambda: value(*args, **kwargs))
            return call
        return value

    def history(self, *args: Any, **kwargs: Any) -> Any:
        return self._read("history", lambda: self._ticker.history(*args, **kwargs))

    def fast_market_cap(self) -> Any:
        # fast_info is lazy: the network failure may occur on .market_cap.
        return self._read("fast_info", lambda: self._ticker.fast_info.market_cap)

def _quote_time_iso(info: Dict[str, Any]) -> str:
    """[A13] The provider's quote time (``regularMarketTime``, epoch seconds) as ISO UTC."""
    try:
        ts = (info or {}).get('regularMarketTime')
        if ts is None or ts == '':
            return ""
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


# [A04] Outcome classes for the scan accounting. These classify reason codes;
# they are not thresholds. A data gap means the name could not be analysed; a
# warning is noted on the name and never decides its outcome.
SCAN_DATA_GAP_REASONS = frozenset({
    "insufficient_data", "data_validation_failed", "info_unavailable",
    "tech_indicators_failed", "no_market_cap", "provider_circuit_open", "circuit_breaker_open",
})
SCAN_WARNING_REASONS = frozenset({"earnings_calendar_missing"})
SCAN_BOOK_STAGES = ("actionable", "event", "risk_grade", "score", "risk_reward",
                    "conviction", "cost", "selection", "final_review")


def india_min_market_cap_for(scan_type: Optional[str]) -> float:
    """The India market-cap floor for a scan type (INR): UNIVERSE.INDIA_SCAN_MIN_MARKET_CAP
    for India Small-Cap, else UNIVERSE.INDIA_MIN_MARKET_CAP."""
    return float(_T.UNIVERSE.INDIA_SCAN_MIN_MARKET_CAP.get(str(scan_type or ""), _T.UNIVERSE.INDIA_MIN_MARKET_CAP))


def min_market_cap_for(scan_type: Optional[str]) -> float:
    """The US market-cap floor for a scan type: its own UNIVERSE.SCAN_MIN_MARKET_CAP
    entry when it has one (small caps), else UNIVERSE.MIN_MARKET_CAP."""
    return float(_T.UNIVERSE.SCAN_MIN_MARKET_CAP.get(str(scan_type or ""), TradingConfig.MIN_MARKET_CAP))


def earnings_size_multiplier(days_to_earnings: int) -> float:
    """[P7] The size multiplier for a known report ``days_to_earnings`` away, from
    the SIZING bands (inclusive day bounds); 1.0 outside them or for a past date."""
    S = _T.SIZING
    d = int(days_to_earnings)
    if d < 0:
        return 1.0
    if d <= S.EARNINGS_IMMINENT_DAYS:
        return S.EARNINGS_IMMINENT_SIZE_MULT
    if d <= S.EARNINGS_NEAR_DAYS:
        return S.EARNINGS_NEAR_SIZE_MULT
    if d <= S.EARNINGS_ELEVATED_DAYS:
        return S.EARNINGS_ELEVATED_SIZE_MULT
    return 1.0


# analyze_stock keeps a local named earnings_size_multiplier; it calls this alias.
earnings_size_multiplier_for = earnings_size_multiplier


class TradingConfig:
    """Enhanced configuration with validation and dynamic adjustment"""

    # Capital & Risk Management
    DEFAULT_CAPITAL: float = _T.alias(_T.CAPITAL, "DEFAULT_US")

    # [B4 FIX] A *ceiling* and a *target* are different policies, and conflating
    # them is why sizing never differentiated. With MAX_POSITION_PCT at 10%, a
    # 2% risk budget is only reachable on a stop wider than 20% — so on every
    # realistic stop (3-20%) the position cap bound first, flattening every
    # conviction and volatility difference back to a single uniform size.
    #
    #   TARGET_*  drives the size. Set so it binds inside the position cap on a
    #             normal stop, which is what makes sizes actually vary.
    #   MAX_*     is the hard ceiling, enforced after sizing. Never exceeded.
    TARGET_RISK_PER_TRADE: float = _T.alias(_T.SIZING, "TARGET_RISK_PER_TRADE")
    TARGET_RISK_STRONG_BUY: float = _T.alias(_T.SIZING, "TARGET_RISK_STRONG_BUY")
    MAX_RISK_PER_TRADE: float = _T.alias(_T.SIZING, "MAX_RISK_PER_TRADE")
    MAX_RISK_STRONG_BUY: float = _T.alias(_T.SIZING, "MAX_RISK_STRONG_BUY")
    MAX_PORTFOLIO_RISK: float = _T.alias(_T.SIZING, "MAX_PORTFOLIO_RISK")
    MIN_POSITION_SIZE: float = _T.alias(_T.SIZING, "MIN_POSITION_SIZE")
    # [BATCH 2 FIX] Reduced from 15% to 10% max
    MAX_POSITION_PCT: float = _T.alias(_T.SIZING, "MAX_POSITION_PCT")
    # [BATCH 2 FIX] Reduced from 40% to 25% max
    MAX_SECTOR_PCT: float = _T.alias(_T.BOOK, "MAX_SECTOR_PCT")

    # [ITEM 6] Adaptive sector cap for small accounts (< SMALL_ACCOUNT_THRESHOLD).
    # A strict sector cap (BOOK.MAX_SECTOR_PCT) forces a small account to hold many sectors, which
    # is often impossible from a thin same-day opportunity set. The profile lets
    # the user choose how much single-sector concentration to tolerate.
    #   "strict"     → 25%   (institutional default, also used for large accounts)
    #   "balanced"   → 35%
    #   "aggressive" → 40%
    SECTOR_CAP_PROFILE: str = _T.alias(_T.BOOK, "SECTOR_CAP_PROFILE")
    # [F08] Opt in to concentrating a thin opportunity set into one sector.
    # Default False keeps the diversification mandate binding: the honest
    # answer to "the only A-grade setups today are all Tech" is a smaller book
    # plus cash. But the opt-in previously existed only as a keyword argument
    # that NO caller passed, so it was unreachable configuration — a user who
    # wanted the concentrated book had to edit the source. Set
    # QUANTA_ALLOW_SECTOR_CONCENTRATION=1 to allow it.
    ALLOW_SECTOR_CONCENTRATION: bool = os.environ.get(
        "QUANTA_ALLOW_SECTOR_CONCENTRATION", "").strip().lower() in ("1", "true", "yes")
    SECTOR_CAP_PROFILE_PCT: Dict[str, float] = _T.alias(_T.BOOK, "SECTOR_CAP_PROFILE_PCT")

    # [FIX] Buffer (decimal) the validator allows over the sizer's target cap
    # to absorb whole-share rounding. Sizer + validator MUST use the same value.
    POSITION_CAP_BUFFER: float = _T.alias(_T.SIZING, "POSITION_CAP_BUFFER")

    # [FIX] Fractional shares: when True, sizers allocate exact dollar amounts
    # (no int() truncation). Most modern US brokers support this. Indian
    # exchanges do not — keep False for NSE/BSE workflows.
    FRACTIONAL_SHARES: bool = False
    FRACTIONAL_SHARES_PRECISION: int = 4  # decimals for fractional share counts

    # [FIX] Auto-scale per-position cap for small accounts. With a hard 10%
    # cap, a $1,500 account cannot buy any stock priced > $150 — a structural
    # impossibility for any mega-cap focus list. Below SMALL_ACCOUNT_THRESHOLD
    # the per-position cap is relaxed up to SMALL_ACCOUNT_MAX_POSITION_PCT.
    SMALL_ACCOUNT_THRESHOLD: float = _T.alias(_T.SIZING, "SMALL_ACCOUNT_THRESHOLD")
    SMALL_ACCOUNT_MAX_POSITION_PCT: float = _T.alias(_T.SIZING, "SMALL_ACCOUNT_MAX_POSITION_PCT")
    RISK_GRADE_SIZE_MULTIPLIERS: Dict[str, float] = _T.alias(_T.SIZING, "RISK_GRADE_SIZE_MULTIPLIERS")

    # Market Filters - Quality Control
    # Tightened to institutional-grade liquidity/quality floors: sub-$2B
    # caps, sub-$10 prices and thin tape carry far higher gap/slippage and
    # manipulation risk and weaker analyst coverage (degrading the EPS-revision
    # and quality factors), so they are screened out of new-money scans.
    MIN_MARKET_CAP: float = _T.alias(_T.UNIVERSE, "MIN_MARKET_CAP")
    MIN_PRICE: float = _T.alias(_T.UNIVERSE, "MIN_PRICE")
    MAX_PRICE: float = _T.alias(_T.UNIVERSE, "MAX_PRICE")
    MIN_AVG_VOLUME: int = _T.alias(_T.UNIVERSE, "MIN_AVG_VOLUME")
    MIN_DAILY_DOLLAR_VOLUME: float = _T.alias(_T.UNIVERSE, "MIN_DAILY_DOLLAR_VOLUME")

    # Investment committee thresholds for new-money recommendations.
    # Scanner output must stay aligned with the deeper TradingAnalysis engine:
    # lower-scoring names can be watchlist ideas, but they are not BUY picks.
    # MIN_SCORE is the candidate-generation floor; the committee policy applies
    # the actionable hurdle (ACCUMULATE_SCORE) before a pick is published.
    #
    # ACCUMULATE / GOOD / EXCELLENT mirror the decision core's 55 / 65 / 75
    # (engine/decision_engine._BUY_THRESHOLDS), which ratchets every scanner
    # verdict downstream. They used to be 5.0 / 6.0 / 7.5, so a 5.0-5.5 name
    # was labelled ACCUMULATE and then silently killed, and a 6.0-6.5 BUY was
    # sized as a BUY and published as an ACCUMULATE. validate_config checks
    # the two stay in step.
    MIN_SCORE: float = _T.alias(_T.SCORE, "MIN_SCORE")
    ACCUMULATE_SCORE: float = _T.alias(_T.SCORE, "ACCUMULATE")
    GOOD_SCORE: float = _T.alias(_T.SCORE, "GOOD")
    EXCELLENT_SCORE: float = _T.alias(_T.SCORE, "EXCELLENT")

    # Risk Metrics
    RISK_REWARD_MIN: float = _T.alias(_T.RR, "SCANNER_MIN")
    RISK_REWARD_OPTIMAL: float = _T.alias(_T.RR, "OPTIMAL")
    MAX_DRAWDOWN_THRESHOLD: float = -0.20  # -20%
    MIN_SHARPE_RATIO: float = 0.5

    # [B2/O13 FIX] The single trailing-stop definition in the engine. Three
    # incompatible ones used to coexist: 40% of the way to target, 0.5% below
    # entry, and a flat "Trail 5%" print. Everything now reads this constant.
    TRAILING_ATR_MULTIPLIER: float = _T.alias(_T.LEVELS, "TRAILING_ATR_MULTIPLIER")

    # [M9 FIX] Stop distance must match the holding horizon. A 1.8x ATR stop is
    # a day-trade stop; held against a multi-week/multi-month target it is hit
    # by ordinary noise long before the thesis resolves. Sizing is derived from
    # the stop (see _calculate_position_size), so a wider stop costs shares,
    # not risk budget.
    STOP_ATR_MULTIPLIER: float = _T.alias(_T.LEVELS, "STOP_ATR_MULTIPLIER")
    # Fallback only — used when a name has no structure above spot and no
    # analyst coverage, so the target cannot be anchored to anything external.
    TARGET_ATR_MULTIPLIER: float = _T.alias(_T.LEVELS, "TARGET_ATR_MULTIPLIER")
    MIN_STOP_DISTANCE_PCT: float = _T.alias(_T.LEVELS, "MIN_STOP_DISTANCE_PCT")
    MAX_STOP_DISTANCE_PCT: float = _T.alias(_T.LEVELS, "MAX_STOP_DISTANCE_PCT")

    # [M5 FIX] yfinance does not report effectiveTaxRate, so ROIC's NOPAT
    # needs a stated assumption rather than a silent default buried in a
    # min/max clamp. 21% is the US federal statutory rate.
    ASSUMED_TAX_RATE: float = 0.21

    # [W2 FIX] Realized vol trades below implied (the variance risk premium),
    # so a realized-vol substitute must be scaled up before it can stand in for
    # VIX — otherwise a missing quote reads as a calm market.
    VIX_PROXY_PREMIUM: float = 1.25

    # [W13 FIX] Weight given to the elite-factor composite (QMJ, BAB, style
    # factors, 12-2 momentum) in the final blend. It used to be 0 in practice:
    # elite_composite_score was computed and never read. Kept deliberately
    # modest because the factors are heuristic proxies, not estimated loadings.
    ELITE_FACTOR_WEIGHT: float = 0.15

    # [M3 FIX] Weight on the ABSOLUTE heuristic sub-scores when blending with
    # the cross-sectional z-score component. The gates downstream (MIN_SCORE,
    # ACCUMULATE_SCORE, GOOD_SCORE, EXCELLENT_SCORE and the decision core's
    # 55/65/75) are absolute, so the score they judge has to stay absolute in
    # character. Was effectively 0.30, which made a percentile the dominant
    # input to a fixed threshold.
    ABSOLUTE_SCORE_WEIGHT: float = 0.65

    # [M12 FIX] ONE window for every correlation input, so the pairwise
    # numbers in a matrix are comparable. Scanned names used to contribute 1y
    # while the fallback fetch contributed 6mo.
    CORRELATION_PERIOD: str = "1y"
    CORRELATION_WINDOW_DAYS: int = 180
    # Order-independent portfolio gate. The old greedy pairwise veto meant the
    # highest-scoring name in a sector arbitrarily blocked its peers.
    MAX_AVERAGE_CORRELATION: float = 0.60

    # [M10 FIX] Earnings proximity scales the position instead of excluding
    # the name. A MISSING calendar is a data gap, not an all-clear — the old
    # bare `except` let unknown-calendar names through at full size.
    EARNINGS_IMMINENT_SIZE_MULT: float = _T.alias(_T.SIZING, "EARNINGS_IMMINENT_SIZE_MULT")
    EARNINGS_NEAR_SIZE_MULT: float = _T.alias(_T.SIZING, "EARNINGS_NEAR_SIZE_MULT")
    EARNINGS_ELEVATED_SIZE_MULT: float = _T.alias(_T.SIZING, "EARNINGS_ELEVATED_SIZE_MULT")
    EARNINGS_UNKNOWN_SIZE_MULT: float = _T.alias(_T.SIZING, "EARNINGS_UNKNOWN_SIZE_MULT")

    # [O12 FIX] Threshold for the "stand down" warning, so the printed advice
    # references the same constant the reader sees in the header.
    VIX_SKIP_THRESHOLD: float = _T.alias(_T.REGIME, "VIX_SKIP")

    # [W10 FIX] Total budget for all additive score adjustments (catalyst,
    # insider, EPS revisions, chase penalty, analyst consensus) combined.
    # The catalyst boost alone used to be able to move the composite by 2.0 on
    # a 0-10 scale — 20% of the range from headline NLP, the least reliable
    # input in the model, applied after the minimum-score gate.
    SCORE_ADJUSTMENT_CAP: float = _T.alias(_T.SCORE, "ADJUSTMENT_CAP")

    # [B7 FIX] Applied SYMMETRICALLY to the bull and bear scenario legs. The
    # old code used 1.5 up against 0.75 down, which made the scenario
    # "expected value" positive for every stock and rising in volatility.
    SCENARIO_SIGMA_MULTIPLIER: float = 1.0

    # Technical Analysis Parameters
    RSI_OVERSOLD: int = 30
    RSI_OVERBOUGHT: int = _T.alias(_T.REGIME, "RSI_OVERBOUGHT")
    RSI_OPTIMAL_LOW: int = 40
    RSI_OPTIMAL_HIGH: int = 60

    # Volatility Classification
    LOW_VOLATILITY: float = 0.15  # 15% annualized
    MEDIUM_VOLATILITY: float = 0.30  # 30% annualized
    HIGH_VOLATILITY: float = 0.50  # 50% annualized

    # Performance & Processing
    # [PROVIDER-RESILIENCE] Concurrency is env-tunable so a throttled host can
    # dial workers down without a code change. yfinance rate-limits aggressively
    # under high parallelism, so the effective worker count for a yfinance scan
    # is the lower of MAX_WORKERS and YF_MAX_WORKERS (see scan_market).
    MAX_WORKERS: int = _env_int("QUANTA_MAX_WORKERS", 4)  # general thread-pool size
    YF_MAX_WORKERS: int = _env_int("QUANTA_YF_MAX_WORKERS", 3)  # cap while using yfinance
    REQUEST_TIMEOUT: int = 30  # seconds (HTTP timeout per request)
    SCAN_FUTURE_TIMEOUT: int = 90  # seconds (max time to wait for a single stock analysis)
    # Minimum pool for cross-sectional z-scores and percentiles. A z-score or
    # percentile over a handful of survivors is noise presented as a
    # market-wide rank; 30 is the conventional floor for a usable sample mean
    # and SD. Below it the ranking still applies the regime weights, without
    # z-scores. This only orders picks; gates and sizing never read it.
    ZSCORE_MIN_UNIVERSE: int = _T.alias(_T.UNIVERSE, "ZSCORE_MIN_UNIVERSE")
    # Wall-clock budget for the one bulk download the liquidity pre-filter
    # makes. yfinance offers no timeout here, so a throttled provider simply
    # blocked the whole scan (5-8 min observed against an advertised 30-60s).
    PREFILTER_TIMEOUT_SECONDS: float = _T.alias(_T.UNIVERSE, "PREFILTER_TIMEOUT_SECONDS")
    MAX_RETRIES: int = 2  # Legacy alias; provider retries use PROVIDER_RETRY_COUNT
    CACHE_DURATION: int = 300  # 5 minutes
    RATE_LIMIT_DELAY: float = 0.0  # No pre-request sleep — rely on backoff only on errors
    BACKOFF_BASE_SECONDS: float = 1.5
    BACKOFF_MAX_SECONDS: float = 15.0
    BACKOFF_JITTER_SECONDS: float = 0.3  # Minimal jitter

    # [PROVIDER-RESILIENCE] Rate-limit handling knobs (all env-overridable).
    PROVIDER_RETRY_COUNT: int = _env_int("QUANTA_PROVIDER_RETRY_COUNT", 3)
    PROVIDER_BACKOFF_SECONDS: float = _env_float("QUANTA_PROVIDER_BACKOFF_SECONDS", 1.5)
    RATE_LIMIT_COOLDOWN_SECONDS: float = _env_float("QUANTA_RATE_LIMIT_COOLDOWN_SECONDS", 60.0)
    # Trip the provider circuit (halt the scan) when this many rate-limit hits
    # accumulate, OR when their share of processed tickers exceeds the ratio
    # after a minimum sample. Prevents firing hundreds of doomed requests.
    PROVIDER_RATE_LIMIT_TRIP_COUNT: int = _env_int("QUANTA_PROVIDER_RL_TRIP_COUNT", 12)
    PROVIDER_RATE_LIMIT_TRIP_RATIO: float = _env_float("QUANTA_PROVIDER_RL_TRIP_RATIO", 0.35)
    PROVIDER_RATE_LIMIT_MIN_SAMPLE: int = _env_int("QUANTA_PROVIDER_RL_MIN_SAMPLE", 20)

    # --- Adaptive pacing (see AdaptivePacer) ---------------------------------
    # Gap between ticker dispatches, in seconds, shared across scan workers.
    # A scored ticker costs ~7 provider requests and a screened-out one ~3, so
    # a 0.30s gap is roughly 10-20 req/s: optimistic on purpose, because the
    # pacer backs off the moment the provider objects and settles wherever the
    # quota actually is.
    PACER_START_INTERVAL: float = _env_float("QUANTA_PACER_START", 0.5)
    PACER_MIN_INTERVAL: float = _env_float("QUANTA_PACER_MIN", 0.25)
    PACER_MAX_INTERVAL: float = _env_float("QUANTA_PACER_MAX", 5.0)
    PACER_BACKOFF_FACTOR: float = _env_float("QUANTA_PACER_BACKOFF", 1.35)
    PACER_RECOVER_STEP: float = _env_float("QUANTA_PACER_RECOVER_STEP", 0.06)
    PACER_RECOVER_AFTER: int = _env_int("QUANTA_PACER_RECOVER_AFTER", 8)

    # --- Bulk history prefetch (see prefetch_history) ------------------------
    # One request per ticker for daily bars is the scan's most frequent
    # provider call; yfinance serves the same bars in batches.
    PREFETCH_CHUNK_SIZE: int = _env_int("QUANTA_PREFETCH_CHUNK", 120)
    PREFETCH_MIN_UNIVERSE: int = _env_int("QUANTA_PREFETCH_MIN_UNIVERSE", 20)
    PREFETCH_TIMEOUT_SECONDS: float = _env_float("QUANTA_PREFETCH_TIMEOUT", 180.0)

    # Minimum rows a scraped S&P 500 constituent table must yield before it is
    # believed. Anything less means the page layout changed under us, not that
    # the index shrank — fall back rather than cache a truncated universe.
    SP500_MIN_CONSTITUENTS: int = 400

    # After the circuit trips, wait out the cooldown and resume the unscanned
    # and throttled names this many times before giving up.
    PROVIDER_MAX_RESUMES: int = _env_int("QUANTA_PROVIDER_MAX_RESUMES", 2)
    # Deep-confirm runs straight after the scan, which is exactly when Yahoo is
    # most likely to be throttling. Wait this long (at most) before retrying a
    # pick whose deep analysis failed for a provider reason.
    DEEP_CONFIRM_RETRY_WAIT: float = _env_float("QUANTA_DEEP_CONFIRM_RETRY_WAIT", 20.0)

    # Data Requirements
    MIN_HISTORY_DAYS: int = _T.alias(_T.UNIVERSE, "MIN_HISTORY_DAYS")
    MAX_LIVE_DATA_AGE_DAYS: int = _T.alias(_T.UNIVERSE, "MAX_LIVE_DATA_AGE_DAYS")
    VWAP_WINDOW: int = 20  # daily-bar rolling VWAP proxy (not intraday session VWAP)
    LOOKBACK_PERIOD: str = "1y"  # 1 year historical data
    
    # Transaction Costs [FIX #6: Tiered by market cap]
    # Mega-cap ($200B+): tighter spreads, more liquidity
    COMMISSION_MEGA_CAP: float = _T.alias(_T.COSTS, "COMMISSION_MEGA_CAP")
    SLIPPAGE_MEGA_CAP: float = _T.alias(_T.COSTS, "SLIPPAGE_MEGA_CAP")
    # Large-cap ($10B+)
    COMMISSION_LARGE_CAP: float = _T.alias(_T.COSTS, "COMMISSION_LARGE_CAP")
    SLIPPAGE_LARGE_CAP: float = _T.alias(_T.COSTS, "SLIPPAGE_LARGE_CAP")
    # Mid-cap ($2B+)
    COMMISSION_MID_CAP: float = _T.alias(_T.COSTS, "COMMISSION_MID_CAP")
    SLIPPAGE_MID_CAP: float = _T.alias(_T.COSTS, "SLIPPAGE_MID_CAP")
    # Small-cap (<$2B)
    COMMISSION_SMALL_CAP: float = _T.alias(_T.COSTS, "COMMISSION_SMALL_CAP")
    SLIPPAGE_SMALL_CAP: float = _T.alias(_T.COSTS, "SLIPPAGE_SMALL_CAP")
    
    # ── Currency ─────────────────────────────────────────────────────────
    # Every size / liquidity / cost threshold in this class is denominated in
    # USD. Indian amounts were compared to them raw, so a ₹20,000 cr (~$2.3B)
    # company was billed as a $200B mega-cap and a ₹2,00,000 account got the
    # strict institutional caps meant for $5,000+. The system sets these once
    # at start-up (see QuantaEliteTradingSystem.__init__).
    INR_PER_USD: float = 88.0          # refreshed from INR=X at start-up
    ACCOUNT_TO_USD: float = 1.0        # multiply an account-currency amount by this

    # Indian delivery-trade statutory costs, per side: STT 0.10% (both
    # sides), stamp duty 0.015% (buy), exchange ~0.003%, 18% GST on exchange
    # fees and SEBI fees. ~0.12% per side before slippage (~25 bps round trip).
    INDIA_STATUTORY_COST_PER_SIDE: float = _T.alias(_T.COSTS, "INDIA_STATUTORY_COST_PER_SIDE")

    @classmethod
    def to_usd(cls, amount: float, market: str = "US") -> float:
        amount = float(amount or 0.0)
        return amount / cls.INR_PER_USD if str(market).upper() == "INDIA" else amount

    @classmethod
    def effective_min_position_size(cls, capital: Optional[float] = None) -> float:
        """[A06] The one minimum position, in the account currency.

        min(SIZING.MIN_POSITION_SIZE, max(SIZING.MIN_POSITION_FLOOR,
        SIZING.MIN_POSITION_PCT x capital)), whole or fractional shares. A flat
        $100 floor is 4% of a $2,500 account: it rejected ordinary risk-sized
        positions after a legitimate haircut (audit F11), while $100 stays the
        floor for large accounts.
        """
        fx = max(cls.ACCOUNT_TO_USD, 1e-9)
        cap = _T.SIZING.MIN_POSITION_SIZE / fx
        if not capital or capital <= 0:
            return cap
        return min(cap, max(_T.SIZING.MIN_POSITION_FLOOR / fx, _T.SIZING.MIN_POSITION_PCT * float(capital)))

    @classmethod
    def min_position_size(cls, capital: Optional[float] = None) -> float:
        """Minimum trade size in the account currency (see effective_min_position_size)."""
        return cls.effective_min_position_size(capital)

    @classmethod
    def max_position_dollars(cls, capital: float) -> float:
        """[A06] The largest position any write may leave: effective cap + buffer."""
        return float(capital) * (cls.effective_max_position_pct(capital) + cls.POSITION_CAP_BUFFER)

    @classmethod
    def one_share_allowed(cls, rec: Any, capital: float) -> bool:
        """[A06] One whole share is the smallest expressible position. It is
        allowed only when that share fits the position cap and the per-trade
        risk ceiling (stop distance x 1 share). Whole-share accounts only."""
        entry = float(getattr(rec, 'entry_price', 0.0) or 0.0)
        stop = float(getattr(rec, 'stop_loss', 0.0) or 0.0)
        if cls.FRACTIONAL_SHARES or entry <= 0 or capital <= 0 or not (0 < stop < entry):
            return False
        max_risk = (cls.MAX_RISK_STRONG_BUY if getattr(rec, 'signal', None) == SignalType.STRONG_BUY.value
                    else cls.MAX_RISK_PER_TRADE)
        return entry <= cls.max_position_dollars(capital) and (entry - stop) <= capital * max_risk

    @classmethod
    def get_transaction_costs(cls, market_cap: float, market: str = "US") -> Tuple[float, float]:
        """Per-side (commission, slippage) by market-cap tier.

        ``market_cap`` is in the listing currency; it is converted to USD
        before tiering. Indian names also carry statutory levies.
        """
        is_india = str(market).upper() == "INDIA"
        market_cap = cls.to_usd(market_cap, market)
        commission, slippage = cls._tier_costs(market_cap)
        if is_india:
            commission += cls.INDIA_STATUTORY_COST_PER_SIDE
        return commission, slippage

    @classmethod
    def _tier_costs(cls, market_cap: float) -> Tuple[float, float]:
        if market_cap >= _T.COSTS.MEGA_CAP_MIN:
            return cls.COMMISSION_MEGA_CAP, cls.SLIPPAGE_MEGA_CAP
        elif market_cap >= _T.COSTS.LARGE_CAP_MIN:
            return cls.COMMISSION_LARGE_CAP, cls.SLIPPAGE_LARGE_CAP
        elif market_cap >= _T.COSTS.MID_CAP_MIN:
            return cls.COMMISSION_MID_CAP, cls.SLIPPAGE_MID_CAP
        else:
            return cls.COMMISSION_SMALL_CAP, cls.SLIPPAGE_SMALL_CAP

    @classmethod
    def get_transaction_cost_bps(cls, market_cap: float, market: str = "US") -> float:
        """Round-trip transaction cost in basis points for a given market cap."""
        commission, slippage = cls.get_transaction_costs(float(market_cap or 0), market)
        # Round-trip: pay both sides of commission + slippage
        return (commission + slippage) * 2 * 10_000

    @classmethod
    def effective_max_position_pct(cls, capital: float) -> float:
        """Return the per-position cap that should actually apply to `capital`.

        For sub-`SMALL_ACCOUNT_THRESHOLD` accounts, scale the cap up so that the
        portfolio is mathematically buildable from realistic stock prices. Above
        the threshold, the strict institutional cap applies.
        """
        try:
            cap = float(capital or 0) * cls.ACCOUNT_TO_USD
        except (TypeError, ValueError):
            cap = 0.0
        if cap <= 0:
            return cls.MAX_POSITION_PCT
        if cap >= cls.SMALL_ACCOUNT_THRESHOLD:
            return cls.MAX_POSITION_PCT
        # Linear scale: at $500 → ~SMALL_ACCOUNT_MAX, at threshold → MAX_POSITION_PCT
        scale = max(0.0, min(1.0, (cls.SMALL_ACCOUNT_THRESHOLD - cap)
                              / max(cls.SMALL_ACCOUNT_THRESHOLD - _T.SIZING.SMALL_ACCOUNT_SCALE_FLOOR, 1.0)))
        scaled = cls.MAX_POSITION_PCT + scale * (
            cls.SMALL_ACCOUNT_MAX_POSITION_PCT - cls.MAX_POSITION_PCT)
        return min(cls.SMALL_ACCOUNT_MAX_POSITION_PCT, max(cls.MAX_POSITION_PCT, scaled))

    @classmethod
    def effective_max_sector_pct(cls, capital: float) -> float:
        """[ITEM 6] Return the sector cap that should apply to `capital`.

        Large accounts (>= SMALL_ACCOUNT_THRESHOLD) always use the strict 25%
        institutional cap. Small accounts use the configured SECTOR_CAP_PROFILE
        so a thin, single-sector opportunity set isn't filtered down to nothing.
        """
        try:
            cap = float(capital or 0) * cls.ACCOUNT_TO_USD
        except (TypeError, ValueError):
            cap = 0.0
        if cap >= cls.SMALL_ACCOUNT_THRESHOLD:
            return cls.MAX_SECTOR_PCT
        profile = (cls.SECTOR_CAP_PROFILE or "strict").strip().lower()
        return cls.SECTOR_CAP_PROFILE_PCT.get(profile, cls.MAX_SECTOR_PCT)

    @staticmethod
    def size_shares(allocation: float, price: float,
                    fractional: Optional[bool] = None) -> float:
        """Convert a dollar allocation into a share count under the active
        share-granularity policy. Returns a float (whole or fractional).
        """
        if price is None or price <= 0 or allocation is None or allocation <= 0:
            return 0.0
        use_fractional = TradingConfig.FRACTIONAL_SHARES if fractional is None else fractional
        if use_fractional:
            return round(allocation / price, TradingConfig.FRACTIONAL_SHARES_PRECISION)
        # The epsilon absorbs float error: int(70.49 / 10.07) is 6, not 7,
        # so a stored allocation of exactly 7 shares came back as 6.
        return float(math.floor(allocation / price + 1e-9))

    # Advanced Features
    ENABLE_ML_SCORING: bool = False  # Machine learning enhancement
    ENABLE_BACKTESTING: bool = True
    ENABLE_OPTIMIZATION: bool = True

    @classmethod
    def validate_config(cls) -> bool:
        """Validate configuration parameters (safe under python -O)"""
        errors = []
        if HAS_DECISION_CORE:
            try:
                from engine.decision_engine import _BUY_THRESHOLDS
                core = {a.value: t / 10.0 for t, a in _BUY_THRESHOLDS}
                for name, ours in (("ACCUMULATE", cls.ACCUMULATE_SCORE),
                                   ("BUY", cls.GOOD_SCORE),
                                   ("STRONG_BUY", cls.EXCELLENT_SCORE)):
                    if name in core and abs(core[name] - ours) > 1e-9:
                        errors.append(
                            f"{name} threshold {ours} disagrees with the decision "
                            f"core's {core[name]}"
                        )
            except Exception:
                pass
        if not (0 < cls.MAX_RISK_PER_TRADE < 0.10):
            errors.append("Risk per trade must be 0-10%")
        if not (0 < cls.MAX_POSITION_PCT <= 0.25):
            errors.append("Max position must be 0-25%")
        if not (0 < cls.MAX_SECTOR_PCT <= 0.50):
            errors.append("Max sector must be 0-50%")
        if not (cls.MIN_PRICE > 0):
            errors.append("Minimum price must be positive")
        if not (cls.RISK_REWARD_MIN >= 1.5):
            errors.append("Risk/reward ratio must be >= 1.5")
        if errors:
            for e in errors:
                logger.error(f"✗ Configuration validation failed: {e}")
            return False
        logger.info("✓ Configuration validated successfully")
        return True


# Shared across every scan worker in the process. Declared here (not next to
# AdaptivePacer) because its bounds come from TradingConfig.
PROVIDER_PACER = AdaptivePacer(
    start=TradingConfig.PACER_START_INTERVAL,
    minimum=TradingConfig.PACER_MIN_INTERVAL,
    maximum=TradingConfig.PACER_MAX_INTERVAL,
    backoff=TradingConfig.PACER_BACKOFF_FACTOR,
    recover=TradingConfig.PACER_RECOVER_STEP,
    recover_after=TradingConfig.PACER_RECOVER_AFTER,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ETF SECTOR MAPPING - For proper classification instead of "Unknown"
# ═══════════════════════════════════════════════════════════════════════════════
ETF_SECTORS = {
    # Broad Market
    'SPY': 'Broad Market', 'VOO': 'Broad Market', 'IVV': 'Broad Market',
    'VTI': 'Broad Market', 'QQQ': 'Technology', 'DIA': 'Broad Market',
    'IWM': 'Small Cap', 'IWF': 'Growth', 'IWD': 'Value',
    # Sector ETFs
    'XLK': 'Technology', 'VGT': 'Technology', 'SMH': 'Semiconductors',
    'SOXX': 'Semiconductors', 'XLF': 'Financial', 'VFH': 'Financial',
    'XLE': 'Energy', 'VDE': 'Energy', 'XLV': 'Healthcare',
    'VHT': 'Healthcare', 'IBB': 'Biotech', 'XBI': 'Biotech',
    'XLI': 'Industrials', 'VIS': 'Industrials', 'XLY': 'Consumer Cyclical',
    'VCR': 'Consumer Cyclical', 'XLP': 'Consumer Defensive', 'VDC': 'Consumer Defensive',
    'XLU': 'Utilities', 'VPU': 'Utilities', 'XLRE': 'Real Estate',
    'VNQ': 'Real Estate', 'XLB': 'Basic Materials', 'VAW': 'Basic Materials',
    # Dividend/Income
    'SCHD': 'Dividend', 'VIG': 'Dividend', 'VYM': 'Dividend',
    'DVY': 'Dividend', 'HDV': 'Dividend', 'DGRO': 'Dividend',
    # Commodities
    'GLD': 'Commodities', 'IAU': 'Commodities', 'SLV': 'Commodities',
    'USO': 'Commodities', 'UNG': 'Commodities', 'DBC': 'Commodities',
    # Fixed Income
    'TLT': 'Fixed Income', 'BND': 'Fixed Income', 'AGG': 'Fixed Income',
    'LQD': 'Fixed Income', 'HYG': 'High Yield', 'TIP': 'Fixed Income',
    # Innovation/Thematic
    'ARKK': 'Innovation', 'ARKG': 'Healthcare', 'ARKF': 'Financial',
}

def get_etf_sector(ticker: str, default_sector: str = 'Unknown') -> str:
    """Get sector for ETF tickers, falls back to default for stocks"""
    return ETF_SECTORS.get(ticker.upper(), default_sector)


def validate_recommendation(rec: Any, capital: float) -> Dict[str, Any]:
    """
    **INSTITUTIONAL QUALITY GATE**
    Performs final pre-trade validation on recommendations.
    
    Checks:
    1. Logical Consistency (Stop < Entry < Target for longs)
    2. Risk Management (R:R >= 2.0, Risk Grade A-C for new-money picks)
    3. Position Sizing (Max allocation limit)
    4. Conviction (Min 2/5 stars)
    
    Args:
        rec: StockAnalysis object or dict
        capital: Total available capital (for position sizing checks)
        
    Returns:
        Dict with 'is_valid' (bool), 'errors' (list), 'warnings' (list)
    """
    errors = []
    warnings = []
    
    # Helper to safely get attribute or dict key
    def get_val(key, default=None):
        if hasattr(rec, key):
            val = getattr(rec, key)
            return val if val is not None else default
        elif isinstance(rec, dict):
            return rec.get(key, default)
        return default
    
    # Extract values
    entry = get_val('entry_price', 0.0)
    stop = get_val('stop_loss', 0.0)
    target = get_val('price_target', 0.0)
    signal = str(get_val('signal', '')).upper()
    rr_ratio = get_val('risk_reward_ratio', 0.0)
    risk_grade = str(get_val('risk_grade', 'C')).upper()
    conviction = get_val('conviction', 0)
    shares = get_val('shares', 0)
    allocation = get_val('allocation', 0.0)
    
    # 0. Every level must be a real number. NaN compares False against
    # everything, so a NaN entry/stop/target/R:R used to sail through every
    # check below as valid.
    def _finite(v: Any) -> bool:
        try:
            return math.isfinite(float(v))
        except (TypeError, ValueError):
            return False
    for _name, _v in (('entry', entry), ('stop', stop), ('target', target),
                      ('risk/reward', rr_ratio), ('allocation', allocation), ('shares', shares)):
        if not _finite(_v):
            errors.append(f"{_name} is not a finite number ({_v!r})")
    if errors:
        return {'is_valid': False, 'errors': errors, 'warnings': warnings}

    # 1. Entry price validation
    if entry <= 0:
        errors.append("Entry price is not set or invalid")
    
    # 2. Stop loss validation
    # Note: Only for Long signals currently
    is_buy_signal = signal in ['STRONG_BUY', 'BUY', 'ACCUMULATE']
    
    if stop <= 0:
        errors.append("Stop loss is not set")
    elif is_buy_signal and stop >= entry:
        errors.append(f"Stop loss ({stop:.2f}) is above entry ({entry:.2f}) for {signal}")
    
    # 3. Target validation
    if target <= 0:
        errors.append("Price target is not set")
    elif is_buy_signal and target <= entry:
        errors.append(f"Target ({target:.2f}) is not above entry ({entry:.2f}) for {signal}")
    
    # 4. R:R ratio validation
    if is_buy_signal and rr_ratio < TradingConfig.RISK_REWARD_MIN:
        errors.append(
            f"R:R ratio ({rr_ratio:.1f}) is below new-money threshold "
            f"({TradingConfig.RISK_REWARD_MIN:.1f})"
        )
    elif rr_ratio < TradingConfig.RISK_REWARD_MIN:
        warnings.append(
            f"R:R ratio ({rr_ratio:.1f}) is below preferred threshold "
            f"({TradingConfig.RISK_REWARD_MIN:.1f})"
        )
    
    # 5. Risk grade validation
    #    F is disqualifying for institutional new-money picks.
    #    D is elevated, not disqualifying: the committee policy already trims
    #    D-grade STRONG_BUY/BUY down to ACCUMULATE (reduced size), so D here is
    #    a size warning rather than a hard reject (keeps scanner consistent with
    #    the analyzer's graduated guardrail).
    if risk_grade == 'F':
        errors.append("Risk grade F is not acceptable for institutional portfolios")
    elif risk_grade == 'D':
        warnings.append("Risk grade D is high-risk - verify reduced position size")
    
    # 6. Conviction validation (FIX: downgraded to warning — Quality Gate #5 handles conviction
    #    filtering with relaxation fallback; keeping this as error caused double-rejection)
    if conviction < 2:
        warnings.append(f"Conviction ({conviction}/5) is below preferred threshold (2)")
    
    # 7. Position size validation
    if shares <= 0:
        pass # Might be pre-allocation check
    
    # Only check max size if allocation is set
    if allocation > 0 and capital > 0:
        position_pct = allocation / capital
        # [FIX] Use the same effective cap + buffer the sizer enforces.
        effective_cap = TradingConfig.effective_max_position_pct(capital)
        max_allowed = effective_cap + TradingConfig.POSITION_CAP_BUFFER
        if position_pct > max_allowed:
            errors.append(
                f"Position size ({position_pct:.1%}) exceeds {effective_cap:.0%} limit"
                + (f" (auto-scaled for ${capital:,.0f} account)"
                   if effective_cap > TradingConfig.MAX_POSITION_PCT else "")
            )
        elif position_pct < 0.01:
            warnings.append(f"Position size ({position_pct:.1%}) is very small (<1%)")

    # 8. Dollar risk at the stop must respect the per-trade ceiling. The
    # whole-share override could buy 1 share whose stop risk exceeded
    # MAX_RISK_PER_TRADE, and this validator never looked.
    if shares > 0 and entry > stop > 0 and capital > 0:
        risk_pct = shares * (entry - stop) / capital
        ceiling = (TradingConfig.MAX_RISK_STRONG_BUY if signal == 'STRONG_BUY'
                   else TradingConfig.MAX_RISK_PER_TRADE)
        if risk_pct > ceiling + 1e-9:
            errors.append(
                f"Risk at stop ({risk_pct:.2%} of capital) exceeds the "
                f"{ceiling:.1%} per-trade ceiling"
            )

    return {
        'is_valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings
    }

def apply_allocation(rec: Any, allocation: float, capital: float,
                     price: Optional[float] = None, one_share: bool = False) -> float:
    """Set ``allocation``, ``shares`` and ``position_pct`` as one atomic unit.

    [W11 FIX] These three fields describe the same position and used to be
    written independently. The greedy allocation pass set ``allocation`` and
    ``shares`` but never refreshed ``position_pct``, so whenever the
    mean-variance step was skipped — fewer than two picks, no scipy, or a
    non-converging solve — the card and the rationale printed a dollar figure
    and a "% of portfolio" that did not correspond to each other, the
    percentage still reflecting a discarded earlier sizing.

    Routing every write through here makes that class of drift impossible.
    Returns the allocation actually applied (0.0 when no whole position fits).

    [A06] It is also the one final sizing step, applied after every transform
    (risk sizing, book caps, Research resize and halving, client mandate): the
    position is capped at ``TradingConfig.max_position_dollars``, rounded to
    the share policy, and dropped when the result is under
    ``TradingConfig.effective_min_position_size``. ``one_share`` lets an
    allocation that buys no whole share become one share when
    ``TradingConfig.one_share_allowed``; only the book's sizing pass sets it,
    so a later scale-down is never rounded back up.
    """
    entry = float(price if price is not None else getattr(rec, 'entry_price', 0.0) or 0.0)
    try:
        allocation = float(allocation or 0.0)
    except (TypeError, ValueError):
        allocation = 0.0

    if allocation <= 0 or entry <= 0:
        rec.allocation = 0.0
        rec.shares = 0
        rec.position_pct = 0.0
        return 0.0

    def _none() -> float:
        rec.allocation = 0.0
        rec.shares = 0
        rec.position_pct = 0.0
        return 0.0

    if capital and capital > 0:
        allocation = min(allocation, TradingConfig.max_position_dollars(capital))
    minimum = TradingConfig.effective_min_position_size(capital)
    if allocation < minimum - 1e-9:
        return _none()
    shares = TradingConfig.size_shares(allocation, entry)
    if shares <= 0 and one_share and TradingConfig.one_share_allowed(rec, capital):
        shares = 1.0
    if shares <= 0:
        return _none()

    actual = round(shares * entry, 2)
    if actual < minimum - 1e-9:
        return _none()
    rec.shares = shares
    rec.allocation = actual
    rec.position_pct = round((actual / capital) * 100, 2) if capital > 0 else 0.0
    return actual


def _fund_score(parts: Dict[str, float], weights: Dict[str, float]) -> float:
    """[ETF] A fund's composite: its scored factors at the regime's weights,
    rescaled to sum to one."""
    keys = [k for k in _T.ETF.SCORED_FACTORS if k in parts]
    total = sum(float(weights.get(k, 0.0)) for k in keys)
    if total <= 0:
        return float(np.mean([parts[k] for k in keys])) if keys else 0.0
    return sum(parts[k] * float(weights.get(k, 0.0)) for k in keys) / total


# ETFs seen in the exchange symbol directory this session (audit B-87): a fund
# the directory flags is classified as one even when the sector registry above
# does not list it.
_DIRECTORY_ETFS: set = set()


def register_directory_etfs(tickers) -> None:
    _DIRECTORY_ETFS.update(str(t).upper() for t in tickers or ())


def is_etf(ticker: str) -> bool:
    """Check if ticker is a known ETF. Used for ETF-specific quality gate relaxation."""
    t = ticker.upper()
    return t in ETF_SECTORS or t in _DIRECTORY_ETFS


# Leveraged / inverse / volatility products: structurally unsuitable for the
# multi-day swing horizon this engine targets (daily-reset decay, path
# dependency, negative expected drift). Hard-excluded from new-money scans.
_LEVERAGED_INVERSE_TICKERS = frozenset({
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "UPRO", "SPXU", "SDOW", "UDOW",
    "TNA", "TZA", "FAS", "FAZ", "SOXL", "SOXS", "TECL", "TECS", "LABU", "LABD",
    "YINN", "YANG", "NUGT", "DUST", "JNUG", "JDST", "ERX", "ERY", "DRN", "DRV",
    "CURE", "TMF", "TMV", "UVXY", "SVXY", "VIXY", "VXX", "UVIX", "SVIX",
    "BOIL", "KOLD", "UCO", "SCO", "AGQ", "ZSL", "UGL", "GLL", "DDM", "QID",
    "SSO", "SDS", "QLD", "DXD", "MIDU", "WEBL", "WEBS", "BULZ", "BERZ",
    "TSLL", "TSLQ", "NVDL", "NVDD", "CONL", "MSTU", "MSTX", "AMDL", "FNGU", "FNGD",
})

_LEVERAGED_NAME_TOKENS = (
    "2x", "3x", "-1x", "ultra", "ultrapro", "leveraged", "inverse",
    "bull 3x", "bear 3x", "bull 2x", "bear 2x", "daily", "-1.5x",
)


def is_leveraged_or_inverse(ticker: str, info: Optional[dict] = None) -> bool:
    """True for leveraged/inverse/volatility ETPs (unsuitable for swing scans).

    Combines a curated ticker set with name-token heuristics so newly listed
    products are still caught even if absent from the static list.
    """
    if ticker.upper() in _LEVERAGED_INVERSE_TICKERS:
        return True
    if info:
        name = str(info.get("longName") or info.get("shortName") or "").lower()
        if any(tok in name for tok in _LEVERAGED_NAME_TOKENS):
            # Avoid false positives on ordinary names containing "daily"
            if any(k in name for k in ("etf", "etn", "trust", "fund", "shares")):
                return True
    return False


# ── DISPLAY ACTION CLASSIFIER (single source of truth) ──────────────────────
# The CLI summary table and the detail cards must never disagree. Both derive
# their label from this one function, which routes the legacy Signal enum name
# through the decision-core FinalAction enum. A bare substring check on the raw
# signal (e.g. `"BUY" in signal`) silently mislabels ACCUMULATE/REDUCE/AVOID.
_FINAL_ACTION_DISPLAY = {
    "STRONG_BUY":    ("\u2605 STRONG BUY", "bold green"),
    "BUY":           ("BUY", "green"),
    "ACCUMULATE":    ("ACCUMULATE", "green"),
    "HOLD_EXISTING": ("HOLD", "yellow"),
    "WATCHLIST":     ("MONITOR", "yellow"),
    "AVOID":         ("SELL", "red"),
    "INVALID":       ("\u2014", "dim"),
}

# Fallback map used only when the decision-core engine is unavailable.
_RAW_SIGNAL_DISPLAY_FALLBACK = {
    "STRONG_BUY":  ("\u2605 STRONG BUY", "bold green", "STRONG_BUY"),
    "BUY":         ("BUY", "green", "BUY"),
    "ACCUMULATE":  ("ACCUMULATE", "green", "ACCUMULATE"),
    "HOLD":        ("HOLD", "yellow", "HOLD_EXISTING"),
    "REDUCE":      ("SELL", "red", "AVOID"),
    "SELL":        ("SELL", "red", "AVOID"),
    "STRONG_SELL": ("SELL", "red", "AVOID"),
}


def classify_display_action(signal_name: Optional[str],
                            has_existing_position: bool = False,
                            final_action: Optional[str] = None):
    """Map a recommendation to a single, consistent CLI label.

    Returns a 4-tuple: (display_label, color, final_action, reason).

    - ``display_label``  user-facing text shown in both the table and cards.
    - ``color``          rich style string.
    - ``final_action``   decision-core FinalAction value (the source field).
    - ``reason``         human-readable classification trace for verbose mode.

    When ``final_action`` is supplied (the value the decision-core already
    resolved on the recommendation) it is the authoritative source — the
    display reads it directly so the scanner can never show BUY for a ticker
    the engine downgraded to ACCUMULATE/WATCHLIST. Only when no resolved
    action is available do we fall back to routing the raw signal through the
    decision-core, and finally to the static raw-signal map.
    """
    raw = (signal_name or "").upper() or "NONE"

    fa = (final_action or "").upper()
    if HAS_DECISION_CORE and fa in _FINAL_ACTION_DISPLAY:
        label, color = _FINAL_ACTION_DISPLAY[fa]
        reason = f"final_action={fa} \u2192 display={label} (engine-resolved)"
        return label, color, fa, reason

    if HAS_DECISION_CORE and signal_to_final_action is not None:
        final = signal_to_final_action(signal_name, has_existing_position)
        final_value = final.value
        label, color = _FINAL_ACTION_DISPLAY.get(final_value, ("MONITOR", "yellow"))
    else:
        label, color, final_value = _RAW_SIGNAL_DISPLAY_FALLBACK.get(
            raw, ("MONITOR", "yellow", "WATCHLIST")
        )

    reason = f"signal={raw} \u2192 final_action={final_value} \u2192 display={label}"
    return label, color, final_value, reason


class SignalType(Enum):
    """Enhanced trading signals with metadata"""
    STRONG_BUY = ("STRONG_BUY", 5, "🔥", "High conviction buy signal")
    BUY = ("BUY", 4, "✅", "Solid buy opportunity")
    ACCUMULATE = ("ACCUMULATE", 3, "📈", "Gradual accumulation zone")
    HOLD = ("HOLD", 2, "⏸️", "Hold current position")
    REDUCE = ("REDUCE", 1, "⚠️", "Consider reducing exposure")
    AVOID = ("AVOID", 0, "🚫", "Avoid or exit position")

    def __init__(self, signal_name: str, priority: int, emoji: str, description: str):
        self.signal_name = signal_name
        self.priority = priority
        self.emoji = emoji
        self.description = description

    @property
    def value(self) -> str:
        return self.signal_name


# [W6 FIX] One FinalAction -> legacy-signal map, previously redefined inline in
# both analyze_stock and _deep_confirm_final_picks with slightly different keys.
_FINAL_ACTION_TO_SIGNAL = {
    "STRONG_BUY":    SignalType.STRONG_BUY.value,
    "BUY":           SignalType.BUY.value,
    "ACCUMULATE":    SignalType.ACCUMULATE.value,
    "HOLD_EXISTING": SignalType.HOLD.value,
    "WATCHLIST":     SignalType.HOLD.value,
    "AVOID":         SignalType.AVOID.value,
    "INVALID":       SignalType.HOLD.value,
}

# Verdicts that must never carry a live position size or entry order.
_NON_ALLOCATING_ACTIONS = frozenset({
    "WATCHLIST", "HOLD_EXISTING", "HOLD", "AVOID", "REDUCE", "INVALID",
})


# [W9 FIX] Single source for signal ordering, derived from SignalType.priority
# so a newly added signal can never silently default to "neutral".
_SIGNAL_PRIORITY = {st.value: st.priority for st in SignalType}
# Legacy/alias spellings that appear in saved CSVs and provider payloads.
_SIGNAL_PRIORITY_ALIASES = {
    "SELL": SignalType.AVOID.priority,
    "STRONG_SELL": SignalType.AVOID.priority,
    "HOLD_EXISTING": SignalType.HOLD.priority,
    "WATCHLIST": SignalType.HOLD.priority,
    "NOT_FOUND": SignalType.HOLD.priority,
}


def _signal_priority(signal: Optional[str]) -> int:
    """Rank a signal name. Higher is more bullish. Unknown names log and sort neutral."""
    key = str(signal or "").strip().upper()
    if key in _SIGNAL_PRIORITY:
        return _SIGNAL_PRIORITY[key]
    if key in _SIGNAL_PRIORITY_ALIASES:
        return _SIGNAL_PRIORITY_ALIASES[key]
    logger.warning(
        f"Unrecognised signal '{signal}' in priority comparison; treating as HOLD. "
        f"Known: {sorted(_SIGNAL_PRIORITY) + sorted(_SIGNAL_PRIORITY_ALIASES)}"
    )
    return SignalType.HOLD.priority


# ============================================================================
# DATA STRUCTURES
# ============================================================================

# GICS Sector Classification (Global Industry Classification Standard)
GICS_SECTORS = {
    'Energy': ['XOM', 'CVX', 'COP', 'SLB', 'EOG', 'MPC', 'PSX', 'VLO', 'OXY', 'HAL'],
    'Materials': ['LIN', 'APD', 'ECL', 'SHW', 'NEM', 'FCX', 'NUE', 'VMC', 'MLM'],
    'Industrials': ['BA', 'UNP', 'UPS', 'HON', 'RTX', 'CAT', 'DE', 'LMT', 'GE', 'MMM', 'NOC', 'GD'],
    'Consumer Discretionary': ['AMZN', 'TSLA', 'HD', 'NKE', 'MCD', 'SBUX', 'TGT', 'LOW', 'TJX', 'BKNG', 'ABNB'],
    'Consumer Staples': ['WMT', 'PG', 'KO', 'PEP', 'COST', 'PM', 'EL', 'MDLZ', 'CL', 'GIS'],
    'Health Care': ['UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'TMO', 'ABT', 'DHR', 'AMGN', 'ISRG', 'VRTX', 'REGN'],
    'Financials': ['BRK-B', 'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'SPGI', 'BLK', 'C', 'AXP'],
    'Information Technology': ['AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL', 'ACN', 'CSCO', 'ADBE', 'CRM', 'AMD', 'INTC',
                               'QCOM', 'TXN', 'INTU', 'AMAT', 'MU', 'PLTR', 'SNOW', 'NOW'],
    'Communication Services': ['GOOGL', 'META', 'NFLX', 'DIS', 'CMCSA', 'T', 'VZ', 'TMUS'],
    'Utilities': ['NEE', 'DUK', 'SO', 'D', 'AEP', 'EXC', 'SRE', 'XEL'],
    'Real Estate': ['AMT', 'PLD', 'CCI', 'EQIX', 'PSA', 'SPG', 'O', 'WELL']
}

# **v6.0 PROPHET FIX #7.4: Crypto-Correlated Stocks**
# These stocks move violently with Bitcoin - require special risk management
CRYPTO_CORRELATED_STOCKS = {
    'MARA', 'RIOT', 'COIN', 'MSTR', 'HIVE', 'BITF', 'HUT', 'CLSK',
    'GBTC', 'BITO', 'CORZ', 'CIFR', 'BTBT', 'SOS', 'CAN', 'WULF'
}

# **v6.0 PROPHET FIX #7.5: 2025 FOMC Meeting Dates**
# Fed week = smaller positions, higher cash allocation
# Decision (second) day of each FOMC meeting, per the Federal Reserve's
# published calendar (federalreserve.gov/monetarypolicy/fomccalendars.htm,
# checked 2026-09-23). The previous 2025/2026 lists were extrapolated from a
# cadence and had four wrong dates — including Oct 28 2026, the next meeting,
# which would have been haircut a week AFTER the event.
FOMC_DATES_2025 = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"
]

FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"
]

FOMC_DATES_2027 = [
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08"
]
# ============================================================================
# INDIAN STOCK INDEXES - Comprehensive Coverage
# ============================================================================

# Nifty 50 - Top 50 companies on NSE
NIFTY_50_STOCKS = [
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'HINDUNILVR', 'ICICIBANK', 'KOTAKBANK', 'SBIN',
    'BHARTIARTL', 'ITC', 'BAJFINANCE', 'LT', 'HCLTECH', 'ASIANPAINT', 'MARUTI', 'TITAN',
    'AXISBANK', 'WIPRO', 'ULTRACEMCO', 'NESTLEIND', 'SUNPHARMA', 'TECHM', 'ONGC', 'NTPC',
    'M&M', 'ADANIPORTS', 'POWERGRID', 'TMPV', 'COALINDIA', 'TATASTEEL', 'BAJAJFINSV',
    'BAJAJ-AUTO', 'INDUSINDBK', 'DRREDDY', 'GRASIM', 'HINDALCO', 'JSWSTEEL', 'DIVISLAB',
    'TATACONSUM', 'BRITANNIA', 'APOLLOHOSP', 'EICHERMOT', 'BPCL', 'CIPLA', 'SHREECEM',
    'HEROMOTOCO', 'SBILIFE', 'ADANIENT', 'HDFCLIFE', 'LTM'
]

# BSE Sensex - Top 30 on Bombay Stock Exchange
SENSEX_30_STOCKS = [
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'HINDUNILVR', 'ICICIBANK', 'KOTAKBANK', 'SBIN',
    'BHARTIARTL', 'ITC', 'BAJFINANCE', 'LT', 'HCLTECH', 'ASIANPAINT', 'MARUTI', 'TITAN',
    'AXISBANK', 'WIPRO', 'ULTRACEMCO', 'NESTLEIND', 'SUNPHARMA', 'TECHM', 'NTPC', 'M&M',
    'POWERGRID', 'TMPV', 'TATASTEEL', 'BAJAJFINSV', 'INDUSINDBK', 'ADANIPORTS'
]

# Nifty Bank - Banking Sector Leaders
NIFTY_BANK_STOCKS = [
    'HDFCBANK', 'ICICIBANK', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'INDUSINDBK', 'BANDHANBNK',
    'FEDERALBNK', 'IDFCFIRSTB', 'PNB', 'AUBANK', 'BANKBARODA'
]

# Nifty IT - Information Technology Sector
NIFTY_IT_STOCKS = [
    'TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTM', 'COFORGE', 'PERSISTENT', 'MPHASIS'
]

# **EXPANDED: Nifty Next 50** - The next 50 largest companies after Nifty 50
NIFTY_NEXT_50 = [
    'ADANIPOWER', 'ADANIGREEN', 'ADANIENSOL', 'VEDL', 'HAL', 'BEL', 'IRCTC',
    'GAIL', 'PETRONET', 'SAIL', 'NMDC', 'COALINDIA', 'OIL', 'IOC',
    'TATAPOWER', 'TORNTPOWER', 'NHPC', 'SJVN',
    'GODREJCP', 'COLPAL', 'DABUR', 'MARICO', 'PIDILITIND', 'BERGEPAINT',
    'DMART', 'TRENT', 'APLAPOLLO', 'DIXON', 'POLYCAB', 'CROMPTON', 'HAVELLS',
    'MOTHERSON', 'TVSMOTOR', 'ESCORTS', 'ASHOKLEY', 'BALKRISIND', 'MRF', 'APOLLOTYRE',
    'CHOLAFIN', 'M&MFIN', 'LICHSGFIN', 'MUTHOOTFIN', 'HDFCAMC',
    'OFSS', 'TATAELXSI', 'LTTS',
    'ALKEM', 'BIOCON', 'LICI'
]

# **EXPANDED: Quality Mid-Caps & Sectoral Leaders**
NIFTY_MIDCAP_SELECT = [
    # Mid-Cap IT & Tech
    'HAPPSTMNDS', 'INTELLECT', 'KPITTECH', 'ZENSAR', 'CYIENT',
    
    # Defense & Aerospace 
    'HAL', 'BEL', 'COCHINSHIP', 'GRSE', 'BEML',
    
    # PSU Banks
    'CANBK', 'BANKBARODA', 'BOB', 'UNIONBANK', 'INDIANB', 'IOB', 'MAHABANK',
    
    # NBFCs & Finance
    'BAJAJHLDNG', 'CHOLAFIN', 'M&MFIN', 'LICHSGFIN', 'MUTHOOTFIN', 'HDFCAMC',
    'CREDITACCESS', 'APTUS', 'ICICIGI', 'ICICIPRULI', 'SBICARD',
    
    # Infrastructure & Construction
    'IRCON', 'RVNL', 'RAILTEL', 'IRFC', 'PFC', 'RECLTD', 'NHPC',
    
    # Chemicals & Materials
    'AARTI', 'DEEPAKNTR', 'VINATI', 'SPANDANA', 'SRF', 'ATUL',
    
    # Consumer & Retail
    'TATACOMMUN', 'ETERNAL', 'NYKAA', 'PAYTM', 'POLICYBZR',
    'JUBLFOOD', 'PIIND', 'RELAXO', 'VBL', 'VAIBHAVGBL',
    
    # Realty
    'DLF', 'GODREJPROP', 'OBEROIRLTY', 'BRIGADE', 'PRESTIGE', 'SOBHA', 'LODHA',
    
    # Auto Components  
    'BHARATFORG', 'BOSCHLTD', 'MOTHERSON', 'ARE_M', 'EXIDEIND',
    
    # Metals & Mining
    'JSW', 'JINDALSTEL', 'VEDL', 'SAIL', 'NMDC', 'HINDZINC',
    
    # Pharmaceuticals
    'MANKIND', 'GRANULES', 'AJANTA', 'NATCOPHARM', 'IPCALAB',
    
    # Textiles & Apparel
    'PAGEIND', 'ADITBIRLA', 'TRENT', 'SHOPSTOP',
    
    # Capital Goods
    'CUMMINSIND', 'THERMAX', 'ABB', 'SIEMENS', 'VOLTAS',
    
    # Media & Entertainment
    'PVRINOX', 'ZEEL', 'SUNTV', 'NETWEB18'
]

# Additional high-quality Indian stocks for comprehensive coverage
# **FIX #1: Now supports CSV-based loading**
# Create config/custom_indian_stocks.csv to add stocks like BGRENERGY
NIFTY_500_ADDITIONAL_HARDCODED = [
    # Large Cap
    'VEDL', 'GODREJCP', 'PIDILITIND', 'BERGEPAINT', 'DABUR', 'MARICO', 'HAVELLS', 
    'SIEMENS', 'ABB', 'BOSCHLTD', 'MOTHERSON', 'AMBUJACEM', 'DMART', 'GLAND',
    'ZYDUSLIFE', 'LUPIN', 'TORNTPHARM', 'ALKEM', 'BIOCON', 'JINDALSTEL',
    # Mid Cap  
    'GAIL', 'OIL', 'IOC', 'TATAPOWER', 'ADANIGREEN', 'ADANIENSOL', 'SAIL', 'NMDC',
    'PAGEIND', 'UNITDSPR', 'PGHH', 'COLPAL', 'GODREJPROP', 'DLF', 'OBEROIRLTY',
    'TRENT', 'APLAPOLLO', 'CANBK', 'BATAINDIA', 'DIXON', 'VOLTAS', 'CUMMINSIND',
    'ESCORTS', 'ASHOKLEY', 'TVSMOTOR', 'BALKRISIND', 'MRF', 'APOLLOTYRE',
    # IT & Tech
    'OFSS', 'TATAELXSI', 'LTTS', 'HAPPSTMNDS', 'INTELLECT',
    # Pharma & Healthcare
    'AUROPHARMA', 'IPCALAB', 'NATCOPHARM', 'ABBOTINDIA', 'SANOFI',
    # NBFC & Finance
    'CHOLAFIN', 'M&MFIN', 'LICHSGFIN', 'HDFCAMC', 'MUTHOOTFIN', 'ICICIGI', 'ICICIPRULI',
    # Consumption
    'JUBLFOOD', 'PIIND', 'RELAXO', 'VBL', 'TATACOMMUN', 'ETERNAL', 'NYKAA'
]

# Load from CSV with fallback to hardcoded list
NIFTY_500_ADDITIONAL = load_stocks_from_csv(
    'config/custom_indian_stocks.csv',
    NIFTY_500_ADDITIONAL_HARDCODED
)



# Market Cap Classification
class MarketCapTier(Enum):
    MEGA_CAP = ("MEGA", 200_000_000_000, float('inf'))  # $200B+
    LARGE_CAP = ("LARGE", 10_000_000_000, 200_000_000_000)  # $10B - $200B
    MID_CAP = ("MID", 2_000_000_000, 10_000_000_000)  # $2B - $10B
    SMALL_CAP = ("SMALL", 300_000_000, 2_000_000_000)  # $300M - $2B

    def __init__(self, tier_name: str, min_cap: float, max_cap: float):
        self.tier_name = tier_name
        self.min_cap = min_cap
        self.max_cap = max_cap


@dataclass
class MarketRegime:
    """Market regime classification and characteristics"""
    regime_type: str = "NEUTRAL"  # BULL, BEAR, VOLATILE, RANGING
    vix_level: float = 0.0
    trend_strength: float = 0.0
    volatility_regime: str = "NORMAL"  # LOW, NORMAL, HIGH, EXTREME
    recommended_exposure: float = 1.0  # Portfolio exposure multiplier
    # **v6.0 PROPHET FIX #7.3: Regime-based cash recommendations**
    recommended_cash_pct: float = 0.20  # Cash allocation guidance
    factor_weights: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    # [W2 FIX] True when vix_level came from a realized-volatility proxy
    # rather than a real implied-vol quote. Caps exposure and is surfaced in
    # the report header so the reader knows the input is a substitute.
    vix_is_proxy: bool = False
    # Where vix_level came from: "quote" | "realized_vol_proxy" |
    # "last_good" | "default". Anything but "quote" is not a measurement.
    vix_source: str = "quote"


# [BATCH 4 #12] Bear/Base/Bull Price Scenarios
@dataclass
class PriceScenarios:
    """
    Multi-scenario price targets with probability weights.
    More realistic than single point target.
    """
    # Price targets
    bull_target: float = 0.0    # 75th percentile / optimistic
    base_target: float = 0.0    # 50th percentile / expected
    bear_target: float = 0.0    # 25th percentile / pessimistic
    
    # Probability weights (should sum to 1.0)
    bull_prob: float = 0.25
    base_prob: float = 0.50
    bear_prob: float = 0.25
    
    # Expected value = sum(target * probability)
    expected_value: float = 0.0
    
    def calculate_expected(self):
        """Calculate probability-weighted expected value"""
        self.expected_value = (
            self.bull_target * self.bull_prob +
            self.base_target * self.base_prob +
            self.bear_target * self.bear_prob
        )
        return self.expected_value


@dataclass
class MarketMetrics:
    """Market-wide metrics and indicators"""
    vix: float = 0.0
    spy_trend: float = 0.0
    market_regime: MarketRegime = field(default_factory=MarketRegime)
    sector_rotation: Dict[str, float] = field(default_factory=dict)
    market_breadth: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RiskMetrics:
    """Comprehensive risk analytics"""
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    var_95: float = 0.0  # Value at Risk 95%
    cvar_95: float = 0.0  # Conditional VaR
    beta: float = 1.0
    alpha: float = 0.0
    correlation_spy: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TechnicalIndicators:
    """Complete technical analysis indicators"""
    # Price & Moving Averages
    current_price: float = 0.0
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0
    ema_12: float = 0.0
    ema_26: float = 0.0

    # Momentum Indicators
    rsi: float = 50.0
    rsi_14: float = 50.0
    rsi_9: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    roc: float = 0.0
    cci: float = 0.0  # Commodity Channel Index
    williams_r: float = -50.0

    # Volatility Indicators
    atr: float = 0.0
    atr_percent: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    bb_position: float = 0.5
    keltner_upper: float = 0.0
    keltner_lower: float = 0.0

    # Volume Indicators
    volume_sma: float = 0.0
    volume_ratio: float = 1.0
    obv: float = 0.0
    obv_ema: float = 0.0
    vwap: float = 0.0

    # Trend Indicators
    adx: float = 0.0  # Average Directional Index
    trend_strength: float = 0.0

    # Support & Resistance
    support: float = 0.0
    resistance: float = 0.0
    pivot_point: float = 0.0

    # Momentum Metrics
    momentum_1w: float = 0.0
    momentum_1m: float = 0.0
    momentum_3m: float = 0.0
    momentum_6m: float = 0.0
    # [BATCH 3 #4] Academic momentum: t-12 to t-2 (skip most recent month)
    momentum_12_2: float = 0.0  # 12-month return excluding last month

    # Price Levels
    # Simple-mean true range over 20 and 120 sessions, as a fraction of price.
    # A 20-day range far below the 120-day one is the corporate-event gate's
    # secondary signal of a price pinned by a pending deal (audit F1).
    atr_pct_20: Optional[float] = None
    atr_pct_120: Optional[float] = None
    high_52w: float = 0.0
    # Completed sessions since the 52-week high printed (0 = today). A high older
    # than LEVELS.TARGET_STRUCT_MAX_AGE_SESSIONS is not used as a target (F1).
    high_52w_age_sessions: Optional[int] = None
    low_52w: float = 0.0
    distance_from_high: float = 0.0
    distance_from_low: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class FundamentalMetrics:
    """Comprehensive fundamental analysis"""
    # Valuation Metrics
    market_cap: float = 0.0
    enterprise_value: float = 0.0
    pe_ratio: float = 0.0
    forward_pe: float = 0.0
    peg_ratio: float = 0.0
    pb_ratio: float = 0.0
    ps_ratio: float = 0.0
    ev_ebitda: float = 0.0

    # Profitability Metrics
    roe: float = 0.0  # Return on Equity
    roa: float = 0.0  # Return on Assets
    roic: float = 0.0  # Return on Invested Capital
    gross_margin: float = 0.0
    operating_margin: float = 0.0
    profit_margin: float = 0.0
    ebitda_margin: float = 0.0

    # Financial Health
    current_ratio: float = 0.0
    quick_ratio: float = 0.0
    debt_to_equity: float = 0.0
    debt_to_assets: float = 0.0
    interest_coverage: float = 0.0

    # Growth Metrics
    revenue_growth: float = 0.0
    earnings_growth: float = 0.0
    revenue_growth_3y: float = 0.0
    earnings_growth_3y: float = 0.0

    # Cash Flow
    operating_cash_flow: float = 0.0
    free_cash_flow: float = 0.0
    fcf_margin: float = 0.0

    # Dividend Metrics
    dividend_yield: float = 0.0
    payout_ratio: float = 0.0
    dividend_growth_rate: float = 0.0

    # Efficiency
    asset_turnover: float = 0.0
    inventory_turnover: float = 0.0

    # Statements reported in a different currency than the quote (ADRs,
    # INFY.NS). Ratios mixing the two are marked unavailable.
    currency_mismatch: bool = False

    # Float fields retain 0.0 defaults for serialization compatibility, while
    # this map preserves the critical distinction between a real zero and a
    # provider field that was absent or invalid.
    data_available: Dict[str, bool] = field(default_factory=dict)

    def has_metric(self, name: str) -> bool:
        """A reported finite value, including a real zero; missing stays unknown."""
        if not isinstance(self.data_available, dict) or not self.data_available.get(name, False):
            return False
        value = getattr(self, name, None)
        return (isinstance(value, (int, float, np.integer, np.floating))
                and not isinstance(value, (bool, np.bool_)) and bool(np.isfinite(value)))

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class EliteFactorScores:
    """Elite quantitative factor scores"""
    # Quantitative style-factor heuristics (SMB/HML-inspired; not canonical FF factors)
    size_factor: float = 0.0  # SMB (Small Minus Big)
    value_factor: float = 0.0  # HML (High Minus Low)
    profitability_factor: float = 0.0  # RMW (Robust Minus Weak)
    investment_factor: float = 0.0  # CMA (Conservative Minus Aggressive)
    market_beta: float = 1.0  # Market factor

    # Quality-Minus-Junk (QMJ)
    quality_score: float = 0.0  # Profitability, growth, safety, payout
    junk_score: float = 0.0
    qmj_spread: float = 0.0

    # Multi-Horizon Momentum
    momentum_1m: float = 0.0
    momentum_3m: float = 0.0
    momentum_6m: float = 0.0
    momentum_12m: float = 0.0
    momentum_12_2: float = 0.0  # 12m momentum excluding most recent ~2 months
    momentum_composite: float = 0.0

    # Betting-Against-Beta (BAB)
    bab_score: float = 0.0  # Low beta = higher score
    beta_adjusted_return: float = 0.0

    # Combined Elite Score
    elite_composite_score: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TimeframeProjection:
    """Statistical timeframe projections.

    [O4 FIX] ``horizon_months`` is the single numeric horizon. Three different
    holding periods used to be shown for the same pick in one run: the card
    printed ``"4-8 weeks" if vol < 0.30 else "2-4 weeks"``, the projection
    printed e.g. "3-6 months", and the rationale substring-matched
    ``target_timeframe`` against only "3-6", "6-9" and "6-12" — so a
    "1-2 months" projection fell through to "longer-term hold / 12+ months".
    Holding period sets the stop, the target and the tax treatment; it cannot
    be ambiguous. Every display now reads these two fields.
    """
    horizon_months: float = 4.5           # single numeric source of truth
    review_date: str = ""                 # concrete date, not a vague band
    target_timeframe: str = "3-6 months"  # human label derived from the above
    expected_return: float = 0.0
    probability_success: float = 0.0  # deprecated; intentionally uncalibrated/zero
    confidence_basis: str = "Scenario estimate only; no calibrated target-hit probability."
    volatility_adjusted_timeline: str = ""
    sector_rally_pattern: str = ""
    momentum_acceleration: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


# ============================================================================
# [ELITE FIX] Historical Track Record & Performance Tracking
# ============================================================================

@dataclass
class SignalRecord:
    """Historical signal tracking for performance validation"""
    ticker: str
    signal_date: str  # ISO format: 2026-01-01
    signal_type: str  # BUY, STRONG_BUY
    entry_price: float
    target_price: float
    stop_loss: float
    position_size: float
    conviction: int  # 1-5 stars
    
    # Outcome tracking
    outcome: str = "OPEN"  # OPEN, HIT_TARGET, HIT_STOP, MANUAL_EXIT
    exit_price: float = 0.0
    exit_date: str = ""
    hold_days: int = 0
    return_pct: float = 0.0
    
    # Metadata
    overall_score: float = 0.0
    risk_grade: str = "C"
    sector: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)


class SignalTracker:
    """Track historical signals and calculate performance metrics"""
    
    def __init__(self, storage_file: str = "signal_history.json"):
        self.storage_file = Path(storage_file)
        self.signals: List[SignalRecord] = []
        self._load_history()
    
    def _load_history(self):
        """Load historical signals from JSON file"""
        try:
            if self.storage_file.exists():
                with open(self.storage_file, 'r') as f:
                    data = json.load(f)
                    self.signals = [SignalRecord(**record) for record in data]
                logger.debug(f"Loaded {len(self.signals)} historical signals")
        except Exception as e:
            logger.warning(f"Could not load signal history: {e}")
            self.signals = []
    
    def _save_history(self):
        """Save signals to JSON file"""
        try:
            with open(self.storage_file, 'w') as f:
                json.dump([s.to_dict() for s in self.signals], f, indent=2)
        except Exception as e:
            logger.error(f"Could not save signal history: {e}")
    
    def add_signal(self, analysis: 'StockAnalysis', conviction: int):
        """Record a new signal"""
        record = SignalRecord(
            ticker=analysis.ticker,
            signal_date=datetime.now().strftime('%Y-%m-%d'),
            signal_type=analysis.signal,
            entry_price=analysis.entry_price,
            target_price=analysis.price_target,
            stop_loss=analysis.stop_loss,
            position_size=analysis.allocation,
            conviction=conviction,
            overall_score=analysis.overall_score,
            risk_grade=analysis.risk_grade,
            sector=analysis.sector
        )
        self.signals.append(record)
        self._save_history()
    
    def update_outcome(self, ticker: str, outcome: str, exit_price: float):
        """Update signal outcome when position is closed"""
        for signal in reversed(self.signals):  # Most recent first
            if signal.ticker == ticker and signal.outcome == "OPEN":
                signal.outcome = outcome
                signal.exit_price = exit_price
                signal.exit_date = datetime.now().strftime('%Y-%m-%d')
                
                # Calculate returns
                entry_date = datetime.strptime(signal.signal_date, '%Y-%m-%d')
                exit_date = datetime.now()
                signal.hold_days = (exit_date - entry_date).days
                signal.return_pct = ((exit_price / signal.entry_price) - 1) * 100
                
                self._save_history()
                break
    
    def get_performance_stats(self, days: int = 90) -> Dict:
        """Calculate performance statistics for recent signals"""
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        recent_signals = [s for s in self.signals if s.signal_date >= cutoff_date and s.outcome != "OPEN"]
        
        if not recent_signals:
            return {
                'total_signals': 0,
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'profit_factor': 0.0,
                'best_pick': None,
                'worst_pick': None
            }
        
        wins = [s for s in recent_signals if s.return_pct > 0]
        losses = [s for s in recent_signals if s.return_pct < 0]
        
        win_rate = (len(wins) / len(recent_signals)) * 100 if recent_signals else 0
        avg_win = np.mean([s.return_pct for s in wins]) if wins else 0
        avg_loss = np.mean([abs(s.return_pct) for s in losses]) if losses else 0
        
        total_gains = sum(s.return_pct for s in wins)
        total_losses = sum(abs(s.return_pct) for s in losses)
        profit_factor = total_gains / total_losses if total_losses > 0 else 0
        
        best = max(recent_signals, key=lambda s: s.return_pct)
        worst = min(recent_signals, key=lambda s: s.return_pct)
        
        return {
            'total_signals': len(recent_signals),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'best_pick': f"{best.ticker} (+{best.return_pct:.1f}%)",
            'worst_pick': f"{worst.ticker} ({worst.return_pct:.1f}%)"
        }


@dataclass
class PortfolioSnapshot:
    """Current portfolio state for correlation and risk analysis"""
    tickers: List[str] = field(default_factory=list)
    allocations: Dict[str, float] = field(default_factory=dict)
    sectors: Dict[str, float] = field(default_factory=dict)
    hist_data: Dict[str, pd.DataFrame] = field(default_factory=dict)
    
    def add_position(self, ticker: str, allocation: float, sector: str, hist: pd.DataFrame):
        """Add a position to the snapshot"""
        self.tickers.append(ticker)
        self.allocations[ticker] = allocation
        self.hist_data[ticker] = hist
        
        # Aggregate sector exposure
        if sector in self.sectors:
            self.sectors[sector] += allocation
        else:
            self.sectors[sector] = allocation
    
    def get_sector_pct(self, sector: str, total_capital: float) -> float:
        """Get sector allocation as percentage of total capital"""
        if total_capital == 0:
            return 0.0
        return (self.sectors.get(sector, 0) / total_capital) * 100


# ============================================================================
# [ELITE FIX] Setup Type Classification
# ============================================================================

class SetupType(Enum):
    """Trading setup classification for better understanding"""
    BREAKOUT = ("Breakout", "🚀", "Price breaking above resistance with volume")
    VALUE_PLAY = ("Value Play", "💎", "Undervalued stock with strong fundamentals")
    MOMENTUM = ("Momentum", "📈", "Strong upward price momentum")
    MEAN_REVERSION = ("Mean Reversion", "↩️", "Oversold bounce opportunity")
    GROWTH = ("Growth", "🌱", "High growth company at reasonable valuation")
    DIVIDEND_INCOME = ("Dividend Income", "💰", "Stable dividend payer")
    QUALITY_COMPOUNDER = ("Quality Compounder", "⭐", "High-quality business compounding returns")
    
    def __init__(self, display_name: str, emoji: str, description: str):
        self.display_name = display_name
        self.emoji = emoji
        self.description = description


@dataclass
class StockAnalysis:
    """Complete stock analysis with all metrics"""
    # Identification
    ticker: str
    company: str
    sector: str
    industry: str
    exchange: str = "UNKNOWN"

    # Signal & Scores
    signal: str = SignalType.HOLD.value
    overall_score: float = 0.0
    technical_score: float = 0.0
    fundamental_score: float = 0.0
    momentum_score: float = 0.0
    quality_score: float = 0.0
    sentiment_score: float = 0.0
    value_score: float = 0.0

    # Trading Levels
    entry_price: float = 0.0
    price_target: float = 0.0
    stop_loss: float = 0.0
    trailing_stop: float = 0.0

    # Position Sizing
    allocation: float = 0.0
    shares: int = 0
    position_pct: float = 0.0

    # Risk Metrics
    risk_amount: float = 0.0
    reward_amount: float = 0.0
    risk_reward_ratio: float = 0.0
    # [FIX #4] Confidence tier replaces the uncalibrated tanh "win probability".
    # A deterministic tier (HIGH/MEDIUM/LOW) is honest about what the system
    # can actually claim, instead of printing "72%" with no backing data.
    signal_strength: float = 0.0  # kept for backward compat; see confidence_tier
    confidence_tier: str = "MEDIUM"  # HIGH / MEDIUM / LOW
    signal_strength_caveat: str = "Heuristic confidence tier, NOT a validated win probability."
    confidence_confirmations: int = 0
    confidence_factors_total: int = 6
    expected_value: float = 0.0  # deprecated; requires a calibrated probability model

    # Detailed Metrics
    technical_indicators: TechnicalIndicators = field(default_factory=TechnicalIndicators)
    fundamental_metrics: FundamentalMetrics = field(default_factory=FundamentalMetrics)
    risk_metrics: RiskMetrics = field(default_factory=RiskMetrics)
    elite_factors: EliteFactorScores = field(default_factory=EliteFactorScores)
    timeframe_projection: TimeframeProjection = field(default_factory=TimeframeProjection)

    # Market Classification
    market_cap_tier: str = "UNKNOWN"
    gics_sector: str = "Unknown"

    # Metadata
    analysis_timestamp: datetime = field(default_factory=lambda: datetime.now().astimezone())
    data_provider: str = "yfinance"
    retrieval_timestamp: str = ""
    market_timestamp: str = ""
    quote_time: str = ""            # [A13] provider quote time (company info), ISO UTC; "" when absent
    currency: str = "UNKNOWN"
    adjustment_status: str = "split_dividend_adjusted"
    data_quality_status: str = "validated"
    data_quality_score: float = 1.0
    confidence_level: float = 0.0
    risk_grade: str = "C"  # A-F grade based on risk metrics

    # [DECISION-CORE] Single source of truth for the action the user should take.
    # Always derived from the shared engine — summary and detail both read this.
    final_action: str = ""              # FinalAction.value
    final_action_reasons: List[str] = field(default_factory=list)
    decision: Optional[Dict[str, Any]] = None  # final published contract
    # [F1] Corporate-event gate result (engine.corporate_events.EventCheck).
    event_check: Optional[Dict[str, Any]] = None
    # [F2] The ONE level set a page shows (engine.levels.publish_levels), and the
    # scanner's own set kept only as a labelled reference when Research binds.
    binding_levels: Optional[Dict[str, Any]] = None
    scanner_levels: Optional[Dict[str, Any]] = None
    # [F3] What each factor added to the risk grade, and the window it read.
    risk_grade_drivers: List[Dict[str, Any]] = field(default_factory=list)
    risk_grade_lookback_days: int = 0
    # Reason codes the Research check attached when it bound this call.
    deep_reason_codes: List[str] = field(default_factory=list)
    has_existing_position: bool = False
    is_invalid: bool = False
    invalid_reasons: List[str] = field(default_factory=list)
    # [M3 FIX] Cross-sectional standing within the scanned universe (0-100).
    # Kept separate from overall_score so relative rank and absolute level are
    # never conflated: ranking can use this, absolute gates use the score.
    # None until the stock has been ranked against a real cross-section; a
    # default of 50.0 asserted "exactly median" for names never ranked.
    universe_percentile: Optional[float] = None
    # Cross-sectional ordering key (see ScoringEngine.normalize_scores). The
    # gates, signal and sizing all use overall_score; this only ranks.
    ranking_score: float = 0.0
    # Bounded net of chase / consensus / catalyst / insider / revision
    # adjustments already inside overall_score, carried for the ranking blend.
    score_adjustment: float = 0.0

    # [DATA-RELIABILITY] Set when deep confirmation could not validate this pick
    # for a DATA reason (rate-limit / provider outage / too few bars). Lets the
    # webapp/UI render "Not Confirmed" instead of "Analyzer disagrees".
    data_status: str = "ok"
    not_independently_confirmed: bool = False

    # Insider activity (smart-money signal)
    insider_sentiment: str = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL / UNKNOWN
    insider_buys_90d: int = 0
    insider_sells_90d: int = 0
    insider_net_shares_90d: float = 0.0
    insider_net_value_90d: float = 0.0   # USD net (when price available)
    insider_note: str = ""

    # Earnings / EPS estimate revisions (Zacks-style factor)
    revision_sentiment: str = "UNKNOWN"  # UPGRADING / STABLE / DOWNGRADING / UNKNOWN
    revision_score: float = 5.0          # 0..10, 5 == neutral
    net_revisions_30d: int = 0           # net analyst up - down (30d)
    eps_trend_pct_30d: float = 0.0       # forward EPS estimate % change (30d)
    avg_surprise_pct: float = 0.0        # mean earnings surprise %
    revision_note: str = ""

    # [ELITE FIX] Enhanced decision support
    conviction: int = 3  # 1-5 star rating
    setup_type: str = "MOMENTUM"  # Trading setup classification
    transaction_cost_bps: float = 15.0  # Transaction cost in basis points (spread + slippage)
    investment_committee_notes: List[str] = field(default_factory=list)

    # [PRE-MOMENTUM] Early-momentum classifier output
    # early_momentum_score: number of N-of-M confirmations (0..6)
    # early_momentum_signals: human-readable list of which confirmations fired
    early_momentum_score: int = 0
    early_momentum_signals: List[str] = field(default_factory=list)

    # [Why picked] Evidence the scan read for this name, kept for the Picks
    # page instead of being folded into the score and discarded.
    analyst_rating: str = ""             # provider consensus key (buy, hold, ...); "" = none
    analyst_rec_mean: float = 0.0        # 1 = strong buy .. 5 = sell; 0 = not reported
    analyst_count: int = 0
    analyst_target: float = 0.0          # mean 12-month target; 0 = none
    analyst_target_low: float = 0.0
    analyst_target_high: float = 0.0
    catalysts: List[Dict[str, Any]] = field(default_factory=list)
    news: List[Dict[str, Any]] = field(default_factory=list)
    days_to_earnings: Optional[int] = None
    # [ETF] A fund is scored on SCORED_FACTORS only; company factors stay 0.
    is_fund: bool = False
    scored_factors: List[str] = field(default_factory=list)
    # [Style scans] The screen a Growth / Value / Dividend scan applied.
    style_screen: Optional[Dict[str, Any]] = None
    
    # [PERF FIX] Cached display data — avoids N+1 API calls in display_elite_minimal
    _cached_news_headline: str = ""
    _cached_earnings_days: int = -1  # -1 means unknown
    _hist_cache: Optional[Any] = field(default=None, repr=False)  # Cached hist for correlation reuse
    # [Analysis chart] The full fetch (two years) the risk grade read, so the
    # page can draw a 200-day average across its whole chart window.
    _chart_hist: Optional[Any] = field(default=None, repr=False)
    
    # [FIX #7] Multi-scenario price targets (bear/base/bull)
    price_scenarios: Optional[Any] = field(default=None, repr=False)

    provider_reads: List[Dict[str, Any]] = field(default_factory=list)

    def research_evidence(self) -> Dict[str, Any]:
        """Measured research inputs, separate from the final trade permission.

        This is a read-only view: no independent rating, admission or sizing.
        """
        fm = self.fundamental_metrics
        metrics = ("pe_ratio", "forward_pe", "pb_ratio", "revenue_growth",
                   "earnings_growth", "profit_margin", "roe", "debt_to_equity", "free_cash_flow")
        return {
            "source": "QUANTA heuristic model",
            "factor_scores": {**{k: getattr(self, f"{k}_score") for k in
                                  ("technical", "fundamental", "momentum", "quality", "value")},
                              "eps_revisions": self.revision_score if self.revision_sentiment != "UNKNOWN" else None},
            "fundamentals": {k: getattr(fm, k) if fm.has_metric(k) else None for k in metrics},
            "provider_reads": [dict(r) for r in self.provider_reads],
            "market_timestamp": self.market_timestamp,
            "retrieval_timestamp": self.retrieval_timestamp,
        }

    def to_dict(self) -> Dict:
        """Convert to dictionary for export.

        Private caches (``_hist_cache`` — a full price DataFrame — and the
        ``_cached_*`` display fields) are runtime state, not results; asdict
        deep-copied the DataFrame and the JSON export wrote its repr into
        every record.
        """
        from dataclasses import fields as _fields, is_dataclass as _is_dc
        result: Dict[str, Any] = {}
        for f in _fields(self):
            if f.name.startswith('_'):
                continue
            v = getattr(self, f.name)
            result[f.name] = asdict(v) if _is_dc(v) else v
        result['analysis_timestamp'] = self.analysis_timestamp.isoformat()
        result['research_evidence'] = self.research_evidence()
        return result

    def get_summary(self) -> None:
        """Get one-line summary"""
        currency_symbol = "$"
        print(f"Action: {self.signal} | Score: {self.overall_score}/10")
        print(
            f"Entry: {currency_symbol}{self.entry_price:.2f} | Target: {currency_symbol}{self.price_target:.2f} | "
            f"Stop: {currency_symbol}{self.stop_loss:.2f} | R/R: {self.risk_reward_ratio:.1f}")


def indicator_window(hist: pd.DataFrame, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """[F3] The one-year window the scanner's indicators always used.

    The scanner fetches ``RISK_GRADE.FETCH_PERIOD`` (two years) so the risk
    grade sees ``RISK_GRADE.LOOKBACK_DAYS``; every other metric keeps reading
    the bars a one-year request returns: those on or after the same calendar
    date a year ago.
    """
    if hist is None or hist.empty:
        return hist
    tz = getattr(hist.index, "tz", None)
    now = pd.Timestamp.now(tz=tz) if now is None else pd.Timestamp(now)
    if tz is not None and now.tzinfo is None:
        now = now.tz_localize(tz)
    elif tz is not None:
        now = now.tz_convert(tz)
    return hist.loc[hist.index >= now.normalize() - pd.DateOffset(years=1)]


def decision_for_scan_result(rec: StockAnalysis):
    """Freeze a scanner call after all risk and deep-confirmation gates."""
    from engine.decision import Decision, Horizon, SourceRef, assess_price_freshness
    from engine.evidence import ELITE_PRO, get_evidence

    try:
        action = FinalAction(rec.final_action)
    except (TypeError, ValueError):
        action = signal_to_final_action(rec.signal, rec.has_existing_position)
    if action.allocates_new_capital and float(rec.allocation or 0) <= 0:
        action = FinalAction.WATCHLIST
        rec.final_action = action.value
        rec.final_action_reasons.append("No position remains after final risk gates.")
    if rec.data_status != DataStatus.OK or rec.not_independently_confirmed:
        action = FinalAction.WATCHLIST
        rec.final_action = action.value
    if not action.allocates_new_capital:
        rec.signal = _FINAL_ACTION_TO_SIGNAL.get(action.value, rec.signal)
        rec.allocation = 0.0
        rec.shares = 0
        rec.position_pct = 0.0
        rec.risk_amount = 0.0
    months = float(getattr(rec.timeframe_projection, "horizon_months", 0) or 0)
    days = max(1, round(months * 21)) if months > 0 else _T.LEVELS.RESEARCH_HORIZON_DAYS
    try:
        as_of = pd.Timestamp(rec.market_timestamp).date() if rec.market_timestamp else None
    except (TypeError, ValueError):
        as_of = None
    decision = Decision(
        ticker=rec.ticker, engine="elite_pro",
        horizon=Horizon("position", days, f"{days} trading sessions"),
        action=action, evidence=get_evidence(ELITE_PRO),
        as_of=as_of.isoformat() if as_of else None,
        score=float(rec.overall_score or 0) * 10,
        score_basis="Scanner composite (0-10 scale shown x10); not comparable with the Research score",
        factor_scores={
            "technical": rec.technical_score,
            "fundamental": rec.fundamental_score,
            "momentum": rec.momentum_score,
            "quality": rec.quality_score,
            "sentiment": rec.sentiment_score,
            "value": rec.value_score,
        },
        risk_metrics={"volatility": rec.risk_metrics.volatility,
                      "beta": rec.risk_metrics.beta},
        risk_grade=rec.risk_grade,
        risk_grade_drivers=list(rec.risk_grade_drivers or []),
        risk_grade_lookback_days=int(rec.risk_grade_lookback_days or 0),
        reference_price=rec.entry_price, entry=rec.entry_price,
        stop=rec.stop_loss, target=rec.price_target,
        reward_risk=rec.risk_reward_ratio,
        reasons=list(rec.final_action_reasons),
        sizing={"allocation_usd": rec.allocation if action.allocates_new_capital else 0.0,
                "shares": rec.shares if action.allocates_new_capital else 0},
        sources=[SourceRef("px", "price", rec.data_provider or "unknown",
                           as_of=as_of.isoformat() if as_of else None,
                           retrieved_at=rec.retrieval_timestamp or None)],
        freshness=assess_price_freshness(as_of),
        # The scanner scores on its own scale, so only the action-derived
        # new-money field is published here; the score stance is Research's.
        new_money=_new_money_for(action),
    )
    # [F3] The rules that bound this call, in the order they fired, and what
    # each reader should do: the same contract the Research Decision carries.
    from engine.blockers import action_contract, blocker_info
    from engine.ladder import LadderInputs, decide as ladder_decide
    # [F7] The same ladder as the Research Decision and My Portfolio.
    lad = ladder_decide(LadderInputs(action, decision.score, str(rec.risk_grade or ""),
                                     list(rec.deep_reason_codes or []),
                                     str(decision.evidence.status.value), rec.event_check or None))
    if lad.action is not action:
        decision.action = action = lad.action
        decision.reasons = list(decision.reasons) + lad.reasons
        rec.final_action = action.value
        decision.new_money = _new_money_for(action)
        if not action.allocates_new_capital:
            rec.allocation, rec.shares, rec.position_pct, rec.risk_amount = 0.0, 0, 0.0, 0.0
            decision.sizing = {"allocation_usd": 0.0, "shares": 0}
    decision.blocking_codes = lad.codes
    decision.blockers = [blocker_info(c, rec.event_check) for c in decision.blocking_codes]
    contract = action_contract(action, decision.score, decision.blocking_codes,
                               holdings_known=bool(rec.has_existing_position), event=rec.event_check)
    decision.model_stance = contract["model_stance"]
    decision.holder_action = lad.holder_action
    decision.event_check = rec.event_check or None
    decision.holdings_known = contract["holdings_known"]
    decision.primary_blocker = contract["primary_blocker"]
    return decision


def _new_money_for(action: "FinalAction") -> str:
    from engine.blockers import new_money
    return new_money(action)


# ============================================================================
# SELL / EXIT TRIGGER EVALUATION
# ============================================================================

@dataclass
class SellEvaluation:
    """Result of evaluating an existing holding for deterioration / exit.

    Most stock-pickers are asymmetric — strong at finding entries, silent on
    exits. This makes the SELL side explicit and deterministic so a holding
    can be reviewed on the same factor framework that bought it.
    """
    ticker: str
    action: str            # HOLD / TRIM / SELL
    urgency: str           # NONE / LOW / MEDIUM / HIGH
    reasons: List[str] = field(default_factory=list)
    score_now: float = 0.0
    score_at_entry: Optional[float] = None
    score_drop: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Deterioration thresholds (kept module-level so tests can reference them).
SELL_SCORE_DROP_TRIM = 1.5    # composite drop vs entry → trim one notch
SELL_SCORE_DROP_EXIT = 3.0    # composite collapse vs entry → full exit
SELL_BREAKDOWN_VOL_MULT = 1.5  # close < SMA50 on > this × avg volume → distribution


def evaluate_sell_triggers(analysis: "StockAnalysis",
                           entry_score: Optional[float] = None,
                           entry_price: Optional[float] = None,
                           entry_stop: Optional[float] = None) -> SellEvaluation:
    """Evaluate an existing holding against deterministic deterioration triggers.

    Triggers (most severe wins):
      • Price breached stop-loss                         → SELL (HIGH)
      • Signal degraded to REDUCE/AVOID                  → SELL (HIGH)
      • Risk grade fell to F                             → SELL (HIGH); D → TRIM
      • Composite score collapsed ≥3.0 vs entry         → SELL (HIGH)
      • Composite score dropped ≥1.5 vs entry           → TRIM (MEDIUM)
      • EPS estimates DOWNGRADING                        → TRIM (MEDIUM)
      • Close < SMA50 on >1.5× avg volume (distribution) → TRIM (MEDIUM)
      • Lost the 200-day trend (close < SMA200)          → TRIM (LOW)

    Pure function: no I/O, safe to unit-test. Caller supplies a freshly
    re-analysed `StockAnalysis` plus the original entry context.
    """
    reasons: List[str] = []
    tech = analysis.technical_indicators
    price = float(getattr(tech, "current_price", 0.0) or analysis.entry_price or 0.0)
    score_now = float(analysis.overall_score or 0.0)

    score_drop = 0.0
    if entry_score is not None:
        score_drop = max(0.0, float(entry_score) - score_now)

    # Severity is tracked as an integer rank so the worst trigger dominates.
    severity = 0  # 0 HOLD, 1 TRIM, 2 SELL

    def escalate(level: int, reason: str) -> None:
        nonlocal severity
        severity = max(severity, level)
        reasons.append(reason)

    # --- Hard exits (HIGH urgency) ---------------------------------------
    # The stop that governs a holding is the one set AT ENTRY. A fresh
    # analysis always places its stop >= 3% below today's price, so testing
    # against it could never fire: bought at 100 with a 92 stop and now at 85,
    # the fresh stop is ~80 and the result was HOLD.
    stop = float(entry_stop or 0.0) or float(analysis.stop_loss or 0.0)
    if stop > 0 and price > 0 and price <= stop:
        escalate(2, f"Price {price:.2f} breached stop-loss {stop:.2f}")
    elif (entry_stop is None and entry_price and price > 0
          and price <= float(entry_price) * (1.0 - TradingConfig.MAX_STOP_DISTANCE_PCT)):
        escalate(2, f"Price {price:.2f} is {1 - price / float(entry_price):.0%} below entry "
                    f"{float(entry_price):.2f} (beyond the widest stop the engine sets)")

    degraded = {SignalType.REDUCE.value, SignalType.AVOID.value}
    if analysis.signal in degraded or str(analysis.final_action or '').upper() in {'REDUCE', 'AVOID', 'SELL'}:
        escalate(2, f"Signal degraded to {analysis.final_action or analysis.signal}")

    grade = (analysis.risk_grade or "").upper()
    if grade == "F":
        escalate(2, "Risk grade fell to F (uninvestable)")
    elif grade == "D":
        escalate(1, "Risk grade fell to D — trim one notch")

    if entry_score is not None and score_drop >= SELL_SCORE_DROP_EXIT:
        escalate(2, f"Composite collapsed {score_drop:.1f} pts vs entry "
                    f"({entry_score:.1f}→{score_now:.1f})")
    elif entry_score is not None and score_drop >= SELL_SCORE_DROP_TRIM:
        escalate(1, f"Composite dropped {score_drop:.1f} pts vs entry "
                    f"({entry_score:.1f}→{score_now:.1f})")

    # --- Factor / technical deterioration (MEDIUM-LOW) -------------------
    if analysis.revision_sentiment == "DOWNGRADING":
        escalate(1, f"EPS estimates downgrading ({analysis.revision_note or 'analyst cuts'})")

    sma_50 = float(getattr(tech, "sma_50", 0.0) or 0.0)
    sma_200 = float(getattr(tech, "sma_200", 0.0) or 0.0)
    vol_ratio = float(getattr(tech, "volume_ratio", 1.0) or 1.0)
    if sma_50 > 0 and price > 0 and price < sma_50 and vol_ratio > SELL_BREAKDOWN_VOL_MULT:
        escalate(1, f"Close below SMA50 on {vol_ratio:.1f}× volume (distribution)")
    if sma_200 > 0 and price > 0 and price < sma_200:
        escalate(max(severity, 1) if severity else 1,
                 "Lost the 200-day trend (close < SMA200)")

    action_map = {0: "HOLD", 1: "TRIM", 2: "SELL"}
    urgency_map = {0: "NONE", 1: "MEDIUM", 2: "HIGH"}
    # A lone SMA200 loss with nothing else is only a LOW-urgency heads-up.
    urgency = urgency_map[severity]
    if severity == 1 and len(reasons) == 1 and reasons[0].startswith("Lost the 200-day"):
        urgency = "LOW"

    return SellEvaluation(
        ticker=analysis.ticker,
        action=action_map[severity],
        urgency=urgency,
        reasons=reasons,
        score_now=score_now,
        score_at_entry=float(entry_score) if entry_score is not None else None,
        score_drop=score_drop,
    )


# ============================================================================
# NETWORK & DATA FETCHING
# ============================================================================

class NetworkSession:
    """Robust HTTP session with retry logic and rate limiting"""

    _session: Optional["requests_type.Session"] = None
    _last_request_time: float = 0.0

    @classmethod
    def get_session(cls) -> "requests_type.Session":
        """Get or create session with advanced retry strategy"""
        if requests is None:
            raise RuntimeError("requests library is required for network operations")

        if cls._session is None:
            cls._session = requests.Session()

            retry_strategy = Retry(
                total=TradingConfig.MAX_RETRIES,
                backoff_factor=1.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"],
                raise_on_status=False
            )

            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=20,
                pool_maxsize=40
            )

            cls._session.mount("http://", adapter)
            cls._session.mount("https://", adapter)

            cls._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            })

            logger.info("✓ Network session initialized with retry strategy")

        return cls._session

    @classmethod
    def rate_limited_get(cls, url: str, **kwargs) -> "requests_type.Response":
        """Rate-limited HTTP GET request"""
        import time
        current_time = time.time()
        time_since_last = current_time - cls._last_request_time

        if time_since_last < TradingConfig.RATE_LIMIT_DELAY:
            time.sleep(TradingConfig.RATE_LIMIT_DELAY - time_since_last)

        session = cls.get_session()
        kwargs.setdefault('timeout', TradingConfig.REQUEST_TIMEOUT)
        response = session.get(url, **kwargs)
        cls._last_request_time = time.time()

        return response


class MarketDataProvider:
    """Enhanced market data provider with caching and fallbacks"""

    def __init__(self):
        self.session = NetworkSession.get_session()
        self._cache: Dict[str, Tuple[Any, datetime]] = {}
        
        # [CRITICAL FIX 1.2] Initialize Polygon.io hybrid provider
        self.hybrid_provider = None
        try:
            polygon_module = _import_optional("polygon_provider", "modules.polygon_provider")
            if polygon_module:
                HybridDataProvider = polygon_module.HybridDataProvider
                self.hybrid_provider = HybridDataProvider()
            if self.hybrid_provider and self.hybrid_provider.use_polygon:
                logger.info("MarketDataProvider initialized with Polygon.io (primary)")
            else:
                logger.info("MarketDataProvider initialized with yfinance (Polygon unavailable)")
        except ImportError:
            logger.info("MarketDataProvider initialized (polygon_provider not available)")
        except Exception as e:
            logger.warning(f"HybridDataProvider init failed: {e}")
            logger.info("MarketDataProvider initialized with yfinance fallback")

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cached data is still valid"""
        if key not in self._cache:
            return False
        _, timestamp = self._cache[key]
        age = (datetime.now() - timestamp).total_seconds()
        return age < TradingConfig.CACHE_DURATION

    @staticmethod
    def _read_html_tables(url: str) -> List[pd.DataFrame]:
        """Fetch a page through the shared session and parse its tables.

        pandas 3 no longer accepts a raw HTML string in ``read_html`` (it is
        treated as a path and raises FileNotFoundError), and Wikipedia rejects
        the default urllib user agent. Both failures used to be swallowed at
        debug level, so every "live" constituent list was silently the
        hardcoded fallback. Wrapping the text in StringIO fixes the former;
        going through NetworkSession (browser UA, retries) fixes the latter.
        """
        from io import StringIO
        response = NetworkSession.rate_limited_get(url)
        response.raise_for_status()
        return pd.read_html(StringIO(response.text))


    # ------------------------------------------------------------------
    # [B-87] US universes from data: live constituent tables, else the dated
    # snapshot (engine/index_snapshot.json), else nothing and the cause. No
    # source method returns a hand-typed list.
    # ------------------------------------------------------------------
    def _index_tickers(self, key: str) -> List[str]:
        idx = self.get_index_constituents(key)
        if not hasattr(self, "last_index_sources"):
            self.last_index_sources = {}
            self.last_index_attrs = {}
        self.last_index_sources[key] = {"label": idx.get("label"), "source": idx.get("source"),
                                        "as_of": idx.get("as_of"), "cause": idx.get("cause") or None}
        self.last_index_attrs[key] = dict(idx.get("attrs") or {})
        return list(idx.get("tickers") or [])

    def get_complete_sp500(self) -> List[str]:
        """S&P 500 constituents (live, else snapshot, else none)."""
        tickers = sorted(self._index_tickers("sp500"))
        src = self.last_index_sources["sp500"]
        # [Picks revamp] Which copy of the list a scan read, for the selection line.
        self.last_sp500_source = {"index_source": src["source"], "index_as_of": src["as_of"],
                                  "index_cause": src["cause"]}
        logger.info(f"S&P 500 source universe: {len(tickers)} stocks ({src['source']})")
        return tickers

    def get_sp500_tickers(self) -> List[str]:
        return self.get_complete_sp500()

    def get_nasdaq100_tickers(self) -> List[str]:
        """NASDAQ-100 constituents (live, else snapshot, else none)."""
        return self._index_tickers("nasdaq100")

    def get_dow30_tickers(self) -> List[str]:
        """Dow Jones Industrial Average constituents (live, else snapshot, else none)."""
        return self._index_tickers("dow30")

    def get_sp400_midcap_tickers(self) -> List[str]:
        """S&P 400 mid-cap constituents (live, else snapshot, else none)."""
        return self._index_tickers("sp400")

    def get_sp600_smallcap_tickers(self) -> List[str]:
        """S&P 600 small-cap constituents (live, else snapshot, else none)."""
        return self._index_tickers("sp600")

    def get_us_etf_directory(self) -> Dict[str, Any]:
        """US-listed ETFs from the Nasdaq Trader symbol directory, with names."""
        tickers = self._index_tickers("us_etfs")
        return {"tickers": tickers, "names": dict(self.last_index_attrs.get("us_etfs", {}).get("name") or {}),
                **self.last_index_sources["us_etfs"]}

    def index_sub_industries(self, keys: List[str]) -> Dict[str, str]:
        """GICS sub-industry per symbol from the live tables already read."""
        out: Dict[str, str] = {}
        for key in keys:
            out.update((getattr(self, "last_index_attrs", {}).get(key) or {}).get("sub_industry") or {})
        return out

    def get_sp500_sector_map(self) -> Dict[str, str]:
        """Fetch S&P 500 ticker -> GICS sector metadata from Wikipedia."""
        cache_key = "sp500_sector_map"

        if self._is_cache_valid(cache_key):
            return self._cache[cache_key][0]

        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            tables = self._read_html_tables(url)
            sp500_df = tables[0]
            sector_map = {
                str(row['Symbol']).replace('.', '-'): str(row.get('GICS Sector', '')).strip()
                for _, row in sp500_df.iterrows()
                if self._validate_ticker(str(row['Symbol']).replace('.', '-'))
            }
            # A partial sector map is worse than none: sector caps and the
            # sector-relative PE/PB populations would apply to a fraction of
            # the book. A short parse means the page layout changed.
            if len(sector_map) < TradingConfig.SP500_MIN_CONSTITUENTS:
                logger.warning(
                    f"S&P 500 sector map parsed only {len(sector_map)} symbols "
                    f"(< {TradingConfig.SP500_MIN_CONSTITUENTS}); discarding rather "
                    "than caching a partial map."
                )
                return {}
            self._cache[cache_key] = (sector_map, datetime.now())
            logger.info(f"✓ Fetched S&P 500 sector map for {len(sector_map)} tickers")
            return sector_map
        except Exception as e:
            logger.warning(f"S&P 500 sector map fetch failed: {e}")
            return {}

    def get_index_constituents(self, key: str) -> Dict[str, Any]:
        """[Scan types] Live constituents of an index the scans use (S&P 400,
        S&P 600, Dividend Aristocrats), or the dated snapshot (engine.index_lists)."""
        from engine.index_lists import constituents
        return constituents(key, self._read_index_tables)

    @classmethod
    def _read_index_tables(cls, url: str) -> List[pd.DataFrame]:
        """An index source as tables: NSE publishes CSV files, Nasdaq Trader a
        pipe-delimited text directory, Wikipedia HTML.

        The ETF directory (.txt) used to go to the HTML reader, which found "no
        tables", so the ETF Sleeve scan never had a single name to analyse.
        """
        lower = url.lower()
        if lower.endswith(".csv") or lower.endswith(".txt"):
            from io import StringIO
            response = NetworkSession.rate_limited_get(url)
            response.raise_for_status()
            if lower.endswith(".txt"):
                # Last line is a "File Creation Time" footer, not a security.
                frame = pd.read_csv(StringIO(response.text), sep="|", dtype=str, keep_default_na=False)
                first = frame.columns[0]
                frame = frame[~frame[first].astype(str).str.startswith("File Creation Time")]
                return [frame]
            return [pd.read_csv(StringIO(response.text))]
        return cls._read_html_tables(url)



    def _validate_ticker(self, ticker: str) -> bool:
        """Validate ticker symbol format"""
        if not ticker or not isinstance(ticker, str):
            return False

        ticker = ticker.strip().upper()

        # Basic validation
        if not (1 <= len(ticker) <= 5):
            return False

        # Must be alphanumeric (allow dash for class B shares)
        if not ticker.replace('-', '').replace('.', '').isalnum():
            return False

        # Avoid invalid patterns
        if ticker.startswith(('_', '-', '.', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9')):
            return False

        return True



    # ========================================================================
    # INDIAN STOCK MARKET - Comprehensive Index Methods
    # ========================================================================

    def get_nifty50_tickers(self) -> List[str]:
        """Get Nifty 50 stocks with both NSE and BSE suffixes"""
        tickers = []
        # NSE only. The .BO twin of each name doubled the request count and
        # counted every company twice in the cross-sectional statistics
        # (most .BO symbols also return a single bar from yfinance).
        for ticker in NIFTY_50_STOCKS:
            tickers.append(f"{ticker}.NS")
        logger.info(f"✓ Fetched {len(NIFTY_50_STOCKS)} Nifty 50 stocks (NSE)")
        return tickers

    def get_sensex_tickers(self) -> List[str]:
        """Get Sensex 30 stocks with both NSE and BSE suffixes"""
        tickers = []
        # NSE only. The .BO twin of each name doubled the request count and
        # counted every company twice in the cross-sectional statistics
        # (most .BO symbols also return a single bar from yfinance).
        for ticker in SENSEX_30_STOCKS:
            tickers.append(f"{ticker}.NS")
        logger.info(f"✓ Fetched {len(SENSEX_30_STOCKS)} Sensex stocks (NSE)")
        return tickers

    def get_nifty_bank_tickers(self) -> List[str]:
        """Get Nifty Bank sector stocks"""
        tickers = []
        # NSE only. The .BO twin of each name doubled the request count and
        # counted every company twice in the cross-sectional statistics
        # (most .BO symbols also return a single bar from yfinance).
        for ticker in NIFTY_BANK_STOCKS:
            tickers.append(f"{ticker}.NS")
        logger.info(f"✓ Fetched {len(NIFTY_BANK_STOCKS)} Nifty Bank stocks (NSE)")
        return tickers

    def get_nifty_it_tickers(self) -> List[str]:
        """Get Nifty IT sector stocks"""
        tickers = []
        # NSE only. The .BO twin of each name doubled the request count and
        # counted every company twice in the cross-sectional statistics
        # (most .BO symbols also return a single bar from yfinance).
        for ticker in NIFTY_IT_STOCKS:
            tickers.append(f"{ticker}.NS")
        logger.info(f"✓ Fetched {len(NIFTY_IT_STOCKS)} Nifty IT stocks (NSE)")
        return tickers

    @staticmethod
    def get_nifty500_tickers() -> List[str]:
        """Get comprehensive Nifty 500 tickers (both NSE and BSE) - DEDUPLICATED"""
        # **CRITICAL BUG FIX #1: Use set() for proper deduplication**
        all_stocks = set()  # Auto-deduplicates HINDALCO, RELIANCE, etc.
        
        # Add all index components
        all_stocks.update(NIFTY_50_STOCKS)
        all_stocks.update(NIFTY_NEXT_50)
        all_stocks.update(NIFTY_MIDCAP_SELECT)
        all_stocks.update(NIFTY_500_ADDITIONAL)
        
        # **CORRECTED LOGIC: Check suffix properly & Prefer NSE**
        tickers = []
        for stock in sorted(all_stocks):  # Now deduplicated
            if not stock.endswith(('.NS', '.BO')):
                # No suffix - add ONLY .NS (NSE is primary/liquid)
                tickers.append(f"{stock}.NS")
            else:
                # Already has suffix - add as-is
                tickers.append(stock)
        
        return tickers

    @staticmethod
    def get_complete_indian_universe() -> List[str]:
        """Get complete Indian stock universe across all indexes - DEDUPLICATED"""
        # **CRITICAL BUG FIX #1: Use set() for proper deduplication**
        all_stocks = set()
        
        # Add all index lists
        all_stocks.update(NIFTY_50_STOCKS)
        all_stocks.update(SENSEX_30_STOCKS)
        all_stocks.update(NIFTY_BANK_STOCKS)
        all_stocks.update(NIFTY_IT_STOCKS)
        all_stocks.update(NIFTY_NEXT_50)
        all_stocks.update(NIFTY_MIDCAP_SELECT)
        all_stocks.update(NIFTY_500_ADDITIONAL)
        
        # **CORRECTED LOGIC**
        tickers = []
        for stock in sorted(all_stocks):  # Deduplicated
            if not stock.endswith(('.NS', '.BO')):
                tickers.append(f"{stock}.NS")
            else:
                tickers.append(stock)
        
        return tickers










    @staticmethod
    def classify_gics_sector(ticker: str, yf_sector: str = "") -> str:
        """Classify stock into GICS sector"""
        # First check if ticker is in our predefined GICS mapping
        for sector, tickers in GICS_SECTORS.items():
            if ticker in tickers:
                return sector

        # Fallback to yfinance sector with mapping
        sector_mapping = {
            'Technology': 'Information Technology',
            'Financial Services': 'Financials',
            'Healthcare': 'Health Care',
            'Consumer Cyclical': 'Consumer Discretionary',
            'Consumer Defensive': 'Consumer Staples',
            'Communication': 'Communication Services',
            'Basic Materials': 'Materials',
        }

        return sector_mapping.get(yf_sector, yf_sector or 'Unknown')

    @staticmethod
    def classify_market_cap_tier(market_cap: float, market: str = "US") -> str:
        """Classify stock by market capitalization tier (USD tier bounds)."""
        market_cap = TradingConfig.to_usd(market_cap, market)
        for tier in MarketCapTier:
            if tier.min_cap <= market_cap < tier.max_cap:
                return tier.tier_name
        return "UNKNOWN"


# ============================================================================
# TECHNICAL ANALYSIS ENGINE
# ============================================================================

class TechnicalAnalysisEngine:
    """Advanced technical analysis with 50+ indicators"""

    @staticmethod
    def calculate_all_indicators(hist: pd.DataFrame) -> Optional[TechnicalIndicators]:
        """Calculate comprehensive technical indicators"""
        try:
            if hist.empty or len(hist) < TradingConfig.MIN_HISTORY_DAYS:
                logger.debug(f"Insufficient data: {len(hist)} days")
                return None

            # Ensure numeric data types
            hist = hist.copy()
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in hist.columns:
                    hist[col] = pd.to_numeric(hist[col], errors='coerce')

            hist = hist.dropna(subset=['Close'])

            if len(hist) < TradingConfig.MIN_HISTORY_DAYS:
                return None

            indicators = TechnicalIndicators()

            # Current price
            indicators.current_price = float(hist['Close'].iloc[-1])

            # Moving Averages
            indicators.sma_20 = float(hist['Close'].rolling(20, min_periods=20).mean().iloc[-1])
            indicators.sma_50 = float(hist['Close'].rolling(50, min_periods=50).mean().iloc[-1]
                                      if len(hist) >= 50 else indicators.current_price)
            indicators.sma_200 = float(hist['Close'].rolling(200, min_periods=200).mean().iloc[-1])

            # Exponential Moving Averages
            indicators.ema_12 = float(hist['Close'].ewm(span=12, adjust=False, min_periods=12).mean().iloc[-1])
            indicators.ema_26 = float(hist['Close'].ewm(span=26, adjust=False, min_periods=26).mean().iloc[-1])

            # MACD
            macd_line = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26,
                                                                                            adjust=False).mean()
            macd_signal = macd_line.ewm(span=9, adjust=False).mean()
            indicators.macd = float(macd_line.iloc[-1])
            indicators.macd_signal = float(macd_signal.iloc[-1])
            indicators.macd_histogram = indicators.macd - indicators.macd_signal

            # RSI (Multiple periods)
            indicators.rsi_14 = TechnicalAnalysisEngine._calculate_rsi(hist['Close'], 14)
            indicators.rsi = indicators.rsi_14
            indicators.rsi_9 = TechnicalAnalysisEngine._calculate_rsi(hist['Close'], 9)

            # Stochastic Oscillator - v6.0: Using safe_divide for robustness
            low_14 = hist['Low'].rolling(14, min_periods=14).min()
            high_14 = hist['High'].rolling(14, min_periods=14).max()
            stoch_range = high_14.iloc[-1] - low_14.iloc[-1]
            indicators.stoch_k = 100 * safe_divide(
                hist['Close'].iloc[-1] - low_14.iloc[-1], stoch_range, default=0.5
            )
            # Stochastic D (3-period SMA of K)
            stoch_k_1 = 100 * safe_divide(
                hist['Close'].iloc[-2] - low_14.iloc[-2], 
                high_14.iloc[-2] - low_14.iloc[-2], default=0.5
            )
            stoch_k_2 = 100 * safe_divide(
                hist['Close'].iloc[-3] - low_14.iloc[-3], 
                high_14.iloc[-3] - low_14.iloc[-3], default=0.5
            )
            indicators.stoch_d = (indicators.stoch_k + stoch_k_1 + stoch_k_2) / 3

            # Bollinger Bands
            bb_sma = hist['Close'].rolling(20, min_periods=20).mean()
            bb_std = hist['Close'].rolling(20, min_periods=20).std()
            indicators.bb_upper = float((bb_sma + 2 * bb_std).iloc[-1])
            indicators.bb_middle = float(bb_sma.iloc[-1])
            indicators.bb_lower = float((bb_sma - 2 * bb_std).iloc[-1])
            indicators.bb_width = indicators.bb_upper - indicators.bb_lower
            # v6.0: Using safe_divide for Bollinger Band position
            indicators.bb_position = safe_divide(
                indicators.current_price - indicators.bb_lower, 
                indicators.bb_width, default=0.5
            )

            # ATR (Average True Range)
            high_low = hist['High'] - hist['Low']
            high_close = abs(hist['High'] - hist['Close'].shift())
            low_close = abs(hist['Low'] - hist['Close'].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            # Wilder's ATR (RMA, alpha = 1/14), the standard definition and the
            # one _calculate_adx already uses. The 14-day SMA differed from it
            # by -25%..+30% on live names, moving every stop and share count.
            indicators.atr = float(
                tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean().iloc[-1]
            )
            # v6.0: Safe division for ATR percentage
            indicators.atr_percent = safe_divide(indicators.atr, indicators.current_price, default=0.03)
            if indicators.current_price > 0 and len(tr) >= 120:
                indicators.atr_pct_20 = float(tr.tail(20).mean()) / indicators.current_price
                indicators.atr_pct_120 = float(tr.tail(120).mean()) / indicators.current_price

            # Volume Analysis
            indicators.volume_sma = float(hist['Volume'].rolling(20, min_periods=20).mean().iloc[-1])
            # v6.0: Safe division for volume ratio
            indicators.volume_ratio = safe_divide(hist['Volume'].iloc[-1], indicators.volume_sma, default=1.0)

            # OBV (On-Balance Volume)
            obv = (np.sign(hist['Close'].diff()) * hist['Volume']).fillna(0).cumsum()
            indicators.obv = float(obv.iloc[-1])
            indicators.obv_ema = float(obv.ewm(span=20, adjust=False).mean().iloc[-1])

            # Rolling daily-bar VWAP proxy.  A full-lookback cumulative value
            # becomes increasingly stale and is not comparable across listings.
            # Typical price is used because true session VWAP requires intraday
            # trades, which this daily history does not contain.
            vwap_window = hist.tail(TradingConfig.VWAP_WINDOW)
            typical_price = (
                vwap_window['High'] + vwap_window['Low'] + vwap_window['Close']
            ) / 3.0
            volume_sum = float(vwap_window['Volume'].sum())
            indicators.vwap = safe_divide(
                float((typical_price * vwap_window['Volume']).sum()),
                volume_sum,
                default=indicators.current_price,
            )

            # ROC (Rate of Change)
            indicators.roc = float(((hist['Close'].iloc[-1] - hist['Close'].iloc[-13]) /
                                    hist['Close'].iloc[-13]) * 100 if len(hist) >= 13 else 0)

            # CCI (Commodity Channel Index)
            tp = (hist['High'] + hist['Low'] + hist['Close']) / 3
            sma_tp = tp.rolling(20).mean()
            mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean())
            cci = (tp - sma_tp) / (0.015 * mad)
            indicators.cci = float(cci.iloc[-1]) if not cci.empty else 0.0

            # Williams %R (FIX #9: use safe_divide instead of epsilon hack)
            wr_range = high_14.iloc[-1] - low_14.iloc[-1]
            indicators.williams_r = float(-100 * safe_divide(
                high_14.iloc[-1] - hist['Close'].iloc[-1], wr_range, default=0.5))

            # Support & Resistance
            indicators.support = float(hist['Low'].rolling(20, min_periods=20).min().iloc[-1])
            indicators.resistance = float(hist['High'].rolling(20, min_periods=20).max().iloc[-1])
            indicators.pivot_point = float((hist['High'].iloc[-1] + hist['Low'].iloc[-1] + hist['Close'].iloc[-1]) / 3)

            # Momentum across timeframes
            indicators.momentum_1w = float(hist['Close'].pct_change(5).iloc[-1] if len(hist) >= 6 else 0)
            indicators.momentum_1m = float(hist['Close'].pct_change(20).iloc[-1] if len(hist) >= 21 else 0)
            indicators.momentum_3m = float(hist['Close'].pct_change(60).iloc[-1] if len(hist) >= 61 else 0)
            indicators.momentum_6m = float(hist['Close'].pct_change(126).iloc[-1] if len(hist) >= 127 else 0)
            
            # [W4 FIX] Academic 12-2 momentum, from ONE shared definition.
            # This function used iloc[-44] while EliteQuantEngine used
            # iloc[-42] for the same named factor (0.5994 vs 0.6052 on the same
            # series), and the comment claimed a 22-day skip while the code
            # skipped 42-44. Both call sites now use compute_momentum_12_2.
            indicators.momentum_12_2 = compute_momentum_12_2(hist['Close'])

            # 52-week high/low
            period_252 = min(252, len(hist))
            _highs = hist['High'].tail(period_252)
            indicators.high_52w = float(_highs.max())
            try:
                indicators.high_52w_age_sessions = int(len(_highs) - 1 - int(np.nanargmax(_highs.to_numpy())))
            except (ValueError, TypeError):
                indicators.high_52w_age_sessions = None
            indicators.low_52w = float(hist['Low'].tail(period_252).min())
            indicators.distance_from_high = (indicators.current_price - indicators.high_52w) / indicators.high_52w
            indicators.distance_from_low = (indicators.current_price - indicators.low_52w) / indicators.low_52w

            # ADX (Average Directional Index) - Trend Strength
            indicators.adx = TechnicalAnalysisEngine._calculate_adx(hist)
            indicators.trend_strength = indicators.adx / 100.0

            return indicators

        except Exception as e:
            logger.debug(f"Error calculating technical indicators: {e}")
            return None

    @staticmethod
    def _calculate_rsi(prices: pd.Series, period: int = 14) -> float:
        """
        **FIX #1 CORRECTED: RSI using Wilder's Smoothing**
        Uses alpha=1/period which is the TRUE Wilder's smoothing.
        Previous version used com=period-1 which is mathematically equivalent
        but we now use explicit alpha for clarity.
        """
        try:
            if len(prices) < period + 1:
                return 50.0
                
            delta = prices.diff()
            
            # Separate gains and losses
            up = delta.clip(lower=0)
            down = -1 * delta.clip(upper=0)
            
            # Wilder's Smoothing: alpha = 1/period (NOT com=period-1, adjust=True)
            alpha = 1.0 / period
            ma_up = up.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
            ma_down = down.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

            rs = ma_up / (ma_down + 1e-10)
            rsi = 100 - (100 / (1 + rs))
            
            return float(rsi.iloc[-1]) if not rsi.empty else 50.0
        except Exception:
            return 50.0

    @staticmethod
    def _calculate_adx(hist: pd.DataFrame, period: int = 14) -> float:
        """
        **FIX #2 IMPROVED: ADX with data validation**
        ADX needs 150+ data points for reliable trend strength measurement.
        Returns 0.0 if insufficient data.
        """
        try:
            # FIX #2: ADX needs more data for accuracy
            if len(hist) < 150:
                logger.debug(f"ADX: Insufficient data ({len(hist)} < 150), returning 0")
                return 0.0
                
            high = hist['High']
            low = hist['Low']
            close = hist['Close']

            up_move = high.diff()
            down_move = -low.diff()

            # [W3 FIX] Wilder's directional movement is MUTUALLY EXCLUSIVE:
            # only the larger of the two moves counts on any given bar. The
            # old code merely clipped negatives, so on an outside bar (higher
            # high AND lower low) both +DM and -DM stayed positive, inflating
            # both DI legs and corrupting DX/ADX. ADX feeds technical_score,
            # momentum_score and trend_strength, so the error propagated into
            # one of the most heavily weighted inputs in the model.
            plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
            minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

            tr = pd.concat([
                high - low,
                abs(high - close.shift()),
                abs(low - close.shift())
            ], axis=1).max(axis=1)

            # Wilder's Smoothing: EMA with alpha = 1/period
            alpha = 1.0 / period
            atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

            plus_di = 100 * (plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr)
            minus_di = 100 * (minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr)

            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
            adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

            return float(adx.iloc[-1]) if not adx.empty else 0.0
        except Exception:
            return 0.0


# ============================================================================
# FUNDAMENTAL ANALYSIS ENGINE
# ============================================================================

class FundamentalAnalysisEngine:
    """Comprehensive fundamental analysis"""

    @staticmethod
    def analyze_fundamentals(info: Dict) -> FundamentalMetrics:
        """Extract and analyze fundamental metrics with validation"""
        metrics = FundamentalMetrics()
        try:
            info = info or {}

            def available(provider_key: str) -> bool:
                value = info.get(provider_key)
                try:
                    return value is not None and np.isfinite(float(value))
                except (TypeError, ValueError):
                    return False

            def set_metric(name: str, provider_key: str) -> float:
                present = available(provider_key)
                metrics.data_available[name] = present
                return _clean_float(info.get(provider_key)) if present else 0.0
            
            metrics.market_cap = set_metric('market_cap', 'marketCap')
            metrics.enterprise_value = set_metric('enterprise_value', 'enterpriseValue')
            
            raw_pe = set_metric('pe_ratio', 'trailingPE')
            metrics.pe_ratio = raw_pe if 0 < raw_pe < 300 else 0.0
            metrics.data_available['pe_ratio'] = metrics.data_available['pe_ratio'] and metrics.pe_ratio > 0
            
            raw_fpe = set_metric('forward_pe', 'forwardPE')
            metrics.forward_pe = raw_fpe if 0 < raw_fpe < 300 else 0.0
            metrics.data_available['forward_pe'] = metrics.data_available['forward_pe'] and metrics.forward_pe > 0
                
            peg_key = 'pegRatio' if available('pegRatio') else 'trailingPegRatio'
            metrics.peg_ratio = set_metric('peg_ratio', peg_key)
            metrics.pb_ratio = set_metric('pb_ratio', 'priceToBook')
            metrics.ps_ratio = set_metric('ps_ratio', 'priceToSalesTrailing12Months')
            metrics.ev_ebitda = set_metric('ev_ebitda', 'enterpriseToEbitda')

            # Profitability Metrics
            metrics.roe = set_metric('roe', 'returnOnEquity')
            metrics.roa = set_metric('roa', 'returnOnAssets')
            metrics.gross_margin = set_metric('gross_margin', 'grossMargins')
            metrics.operating_margin = set_metric('operating_margin', 'operatingMargins')
            metrics.profit_margin = set_metric('profit_margin', 'profitMargins')
            metrics.ebitda_margin = set_metric('ebitda_margin', 'ebitdaMargins')
            # [M5 FIX] ROIC previously read `totalStockholderEquity`, `ebit`
            # and `effectiveTaxRate` — none of which yfinance returns in
            # `info`. So `invested_capital` collapsed to (debt - cash), often
            # negative, and ROIC was silently 0.0 for essentially every
            # ticker. Derive it from keys that ARE present:
            #   equity = bookValue x sharesOutstanding
            #   EBIT   = operatingMargins x totalRevenue
            total_debt = _clean_float(info.get('totalDebt'))
            cash = _clean_float(info.get('totalCash'))
            book_value_per_share = _clean_float(info.get('bookValue'))
            shares_out = _clean_float(info.get('sharesOutstanding'))
            equity = book_value_per_share * shares_out
            total_revenue = _clean_float(info.get('totalRevenue'))
            operating_margin_raw = _clean_float(info.get('operatingMargins'))
            ebit = operating_margin_raw * total_revenue

            invested_capital = total_debt + equity - cash
            nopat = ebit * (1.0 - TradingConfig.ASSUMED_TAX_RATE)
            roic_inputs_present = (
                available('bookValue') and available('sharesOutstanding')
                and available('operatingMargins') and available('totalRevenue')
            )
            metrics.roic = (
                safe_divide(nopat, invested_capital, 0.0)
                if roic_inputs_present and invested_capital > 0 else 0.0
            )
            metrics.data_available['roic'] = bool(
                roic_inputs_present and invested_capital > 0
            )

            # Financial Health
            metrics.current_ratio = set_metric('current_ratio', 'currentRatio')
            metrics.quick_ratio = set_metric('quick_ratio', 'quickRatio')
            raw_de = set_metric('debt_to_equity', 'debtToEquity')
            # Yahoo reports this field as a percentage (5 means 0.05x), even
            # for values below 10.  Conditional conversion inflated low-debt
            # companies by 100x.
            metrics.debt_to_equity = raw_de / 100.0 if raw_de >= 0 else 0.0
            metrics.data_available['debt_to_equity'] = (
                metrics.data_available['debt_to_equity'] and raw_de >= 0
            )
            # [M5 FIX] `totalAssets` and `interestExpense` are likewise absent
            # from the yfinance `info` payload, so both of these were
            # permanently 0.0 while being reported as if measured. They are not
            # derivable from what `info` does return, so mark them explicitly
            # unavailable — a scorer that checks `data_available` will now skip
            # them instead of treating 0.0 as a real reading.
            metrics.debt_to_assets = safe_divide(
                total_debt, _clean_float(info.get('totalAssets')), 0.0
            )
            metrics.data_available['debt_to_assets'] = available('totalAssets')
            interest_expense = abs(_clean_float(info.get('interestExpense')))
            metrics.interest_coverage = safe_divide(
                _clean_float(info.get('ebitda')), interest_expense, 0.0
            )
            metrics.data_available['interest_coverage'] = (
                available('interestExpense') and available('ebitda')
            )

            # Growth Metrics
            metrics.revenue_growth = set_metric('revenue_growth', 'revenueGrowth')
            metrics.earnings_growth = set_metric('earnings_growth', 'earningsGrowth')

            # Cash Flow
            metrics.operating_cash_flow = set_metric('operating_cash_flow', 'operatingCashflow')
            metrics.free_cash_flow = set_metric('free_cash_flow', 'freeCashflow')
            metrics.fcf_margin = safe_divide(metrics.free_cash_flow, _clean_float(info.get('totalRevenue')), 0.0)

            # yfinance reports `dividendYield` in PERCENT (AAPL 0.32 = 0.32%,
            # SPY 0.98 = 0.98%); ETFs carry the fraction in `yield` and have no
            # `dividendRate`. The normaliser's magnitude heuristic ("<= 1.0 is
            # already a fraction") turned every sub-1% percent value into a
            # 30-98% yield, so resolve the unit HERE and hand it a fraction.
            if available('yield'):
                raw_div_yield = _clean_float(info.get('yield'))
            elif available('dividendYield'):
                raw_div_yield = _clean_float(info.get('dividendYield')) / 100.0
            else:
                raw_div_yield = None
            if HAS_DECISION_CORE:
                # One resolver for the scanner and Research (engine.dividend).
                metrics.dividend_yield = dividend_yield_from_info(info)
            else:
                metrics.dividend_yield = max(_clean_float(raw_div_yield), 0.0)
            metrics.data_available['dividend_yield'] = metrics.dividend_yield > 0
            
            metrics.payout_ratio = set_metric('payout_ratio', 'payoutRatio')

            # Statement currency vs trading currency. yfinance quotes price and
            # marketCap in the listing currency but the statements in
            # `financialCurrency` (SONY/TM: JPY, TSM: TWD, INFY.NS: USD). Any
            # ratio that mixes the two — ROIC (statement NOPAT over INR/USD
            # book), FCF yield, P/B, P/S — is off by the exchange rate: SONY
            # showed a 2,471% FCF yield, TSM a P/B of 91. Mark them unknown.
            fin_ccy = str(info.get('financialCurrency') or '').upper()
            px_ccy = str(info.get('currency') or '').upper()
            metrics.currency_mismatch = bool(fin_ccy and px_ccy and fin_ccy != px_ccy)
            if metrics.currency_mismatch:
                for name in ('roic', 'pb_ratio', 'ps_ratio', 'free_cash_flow'):
                    metrics.data_available[name] = False
                metrics.roic = 0.0
                metrics.pb_ratio = 0.0
                metrics.ps_ratio = 0.0

            # ROIC is not meaningful for banks/insurers: debt is their raw
            # material and cash their inventory (JPM computed a 43.5% "ROIC").
            if str(info.get('sector') or '') == 'Financial Services':
                metrics.roic = 0.0
                metrics.data_available['roic'] = False

            logger.debug(f"Fundamental metrics extracted successfully")



        except Exception as e:
            logger.debug(f"Error extracting fundamentals: {e}")

        return metrics


# ============================================================================
# RISK ANALYTICS ENGINE
# ============================================================================

class RiskAnalyticsEngine:
    """Advanced risk analytics and portfolio optimization"""

    @staticmethod
    def calculate_risk_metrics(hist: pd.DataFrame, risk_free_rate: float = 0.04) -> RiskMetrics:
        """Calculate comprehensive risk metrics"""
        metrics = RiskMetrics()

        try:
            if hist.empty or len(hist) < 30:
                return metrics

            closes = pd.to_numeric(hist['Close'], errors='coerce')
            returns = closes.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna()

            # Risk statistics must never absorb an unadjusted split. Only the
            # split SIGNATURE is rejected: the old guard returned an all-zero
            # RiskMetrics for ANY >50% day, which the validator (M8) now lets
            # through as a real event. A -55% failed-trial name then reported
            # vol 0 / max drawdown 0 and was graded risk "A".
            if DataValidator.split_like_days(
                    closes, hist['Volume'] if 'Volume' in hist.columns else None,
                    require_volume=False):
                logger.debug("Risk metrics rejected probable unadjusted corporate action")
                return metrics

            if returns.empty or len(returns) < 10:
                return metrics

            # Basic statistics
            mean_return = returns.mean()
            std_dev = returns.std()

            # Annualized metrics
            trading_days = 252
            annual_return = mean_return * trading_days
            annual_volatility = std_dev * np.sqrt(trading_days)

            metrics.volatility = float(annual_volatility)

            # Sharpe Ratio
            excess_return = annual_return - risk_free_rate
            metrics.sharpe_ratio = float(excess_return / annual_volatility) if annual_volatility > 0 else 0.0

            # Sortino uses lower partial deviation across *all* observations,
            # not the standard deviation of the negative-only subset.
            daily_target = risk_free_rate / trading_days
            downside = np.minimum(returns - daily_target, 0.0)
            downside_deviation = float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(trading_days))
            metrics.sortino_ratio = (
                float(excess_return / downside_deviation) if downside_deviation > 0 else 0.0
            )

            # Maximum Drawdown
            # Measured on the price path itself so the first close counts as a
            # peak; cumprod of returns started at 1+r1 and ignored a day-one drop.
            path = closes.dropna()
            drawdown = path / path.cummax() - 1.0
            metrics.max_drawdown = float(drawdown.min())

            # Calmar Ratio
            if metrics.max_drawdown < 0:
                metrics.calmar_ratio = float(annual_return / abs(metrics.max_drawdown))

            # Value at Risk (VaR) - 95% confidence
            metrics.var_95 = float(np.percentile(returns, 5))

            # Conditional VaR (CVaR/Expected Shortfall)
            metrics.cvar_95 = float(returns[returns <= metrics.var_95].mean())

            logger.debug(
                f"Risk metrics calculated: Sharpe={metrics.sharpe_ratio:.2f}, MaxDD={metrics.max_drawdown:.2%}")

        except Exception as e:
            logger.debug(f"Error calculating risk metrics: {e}")

        return metrics

    @staticmethod
    def calculate_beta_alpha(stock_returns: pd.Series, market_returns: pd.Series,
                             risk_free_rate: float = 0.04) -> Tuple[float, float]:
        """Calculate Blume-adjusted beta and Jensen's alpha relative to market.

        *risk_free_rate* defaults to the same 0.04 used by
        ``calculate_risk_metrics`` so Sharpe and alpha share one assumption.
        """
        try:
            if len(stock_returns) < 30 or len(market_returns) < 30:
                return 1.0, 0.0

            # Align indices
            aligned = pd.DataFrame({
                'stock': stock_returns,
                'market': market_returns
            }).dropna()

            if len(aligned) < 30:
                return 1.0, 0.0

            # Calculate beta using covariance
            covariance = aligned['stock'].cov(aligned['market'])
            market_variance = aligned['market'].var()

            beta = covariance / market_variance if market_variance > 0 else 1.0
            
            # FIX #3: Blume Beta Adjustment - regress toward 1.0
            # Academic research shows betas mean-revert over time
            adjusted_beta = 0.33 + 0.67 * beta

            # [M7 FIX] Jensen's alpha is (Rs - Rf) - beta*(Rm - Rf). The old
            # formula omitted the risk-free rate entirely, biasing every alpha
            # by (beta - 1) * Rf: at the 4% rate used elsewhere in this file a
            # 0.5-beta name picked up a spurious -2%/yr and a 1.5-beta name a
            # spurious +2%/yr. The Blume adjustment above was applied
            # correctly, which made the omission easy to miss.
            stock_mean = aligned['stock'].mean() * 252
            market_mean = aligned['market'].mean() * 252
            # Alpha is the intercept of THIS regression, so it uses the fitted
            # beta. The Blume beta is a forecast for the next period; using it
            # here credited a 2.0-beta name with +6.6%/yr of phantom alpha.
            alpha = (stock_mean - risk_free_rate) - beta * (market_mean - risk_free_rate)

            return float(adjusted_beta), float(alpha)

        except Exception as e:
            logger.debug(f"Error calculating beta/alpha: {e}")
            return 1.0, 0.0


# ============================================================================
# ELITE QUANTITATIVE FACTOR ENGINES
# ============================================================================

class EliteQuantEngine:
    """Elite quantitative factor models - institutional heuristics"""

    @staticmethod
    def calculate_style_factor_heuristics(hist: pd.DataFrame, info: Dict,
                                          market_returns: Optional[pd.Series] = None) -> EliteFactorScores:
        """Calculate style-factor heuristics (SMB/HML/RMW/CMA-inspired, non-canonical)."""
        factors = EliteFactorScores()

        try:
            market_cap = info.get('marketCap', 0) or 0  # FIX: Correct yfinance key
            book_value = info.get('bookValue', 0) or 0
            shares = info.get('sharesOutstanding', 0) or 0

            # Market Factor (Beta)
            if market_returns is not None and len(hist) > 30:
                stock_returns = hist['Close'].pct_change().dropna()
                from scipy.stats import linregress
                aligned = pd.DataFrame({'stock': stock_returns, 'market': market_returns}).dropna()
                if len(aligned) > 30:
                    slope, _, _, _, _ = linregress(aligned['market'], aligned['stock'])
                    factors.market_beta = float(slope)

            # Size heuristic (SMB-inspired). Tier bounds are USD; an INR market
            # cap is converted first (every Indian name used to read as large).
            if str(info.get('currency') or '').upper() == 'INR':
                market_cap = TradingConfig.to_usd(market_cap, "INDIA")
            if market_cap > 0:
                if market_cap < 2_000_000_000:  # Small cap
                    factors.size_factor = 2.0
                elif market_cap < 10_000_000_000:  # Mid cap
                    factors.size_factor = 1.0
                elif market_cap < 200_000_000_000:  # Large cap
                    factors.size_factor = -0.5
                else:  # Mega cap
                    factors.size_factor = -1.5

            # Value heuristic (HML-inspired)
            pb_ratio = info.get('priceToBook', 0) or 0
            if pb_ratio > 0:
                if pb_ratio < 1.0:  # Deep value
                    factors.value_factor = 3.0
                elif pb_ratio < 2.0:  # Value
                    factors.value_factor = 2.0
                elif pb_ratio < 3.0:  # Fair value
                    factors.value_factor = 0.5
                elif pb_ratio < 5.0:  # Growth
                    factors.value_factor = -1.0
                else:  # High growth
                    factors.value_factor = -2.0

            # Profitability heuristic (RMW-inspired)
            roe = info.get('returnOnEquity', 0) or 0
            operating_margin = info.get('operatingMargins', 0) or 0
            if roe > 0.25 and operating_margin > 0.20:
                factors.profitability_factor = 3.0
            elif roe > 0.15 and operating_margin > 0.10:
                factors.profitability_factor = 2.0
            elif roe > 0.10:
                factors.profitability_factor = 1.0
            elif roe < 0:
                factors.profitability_factor = -2.0

            # Investment heuristic (CMA-inspired)
            revenue_growth = info.get('revenueGrowth', 0) or 0
            if revenue_growth < 0.05:  # Conservative growth
                factors.investment_factor = 1.5
            elif revenue_growth < 0.15:  # Moderate growth
                factors.investment_factor = 0.5
            elif revenue_growth < 0.30:  # Aggressive growth
                factors.investment_factor = -0.5
            else:  # Very aggressive
                factors.investment_factor = -1.5

        except Exception as e:
            logger.debug(f"Error in style-factor heuristic calculation: {e}")

        return factors

    @staticmethod
    def calculate_fama_french_factors(hist: pd.DataFrame, info: Dict,
                                      market_returns: Optional[pd.Series] = None) -> EliteFactorScores:
        """Backward-compatible wrapper for style-factor heuristics."""
        return EliteQuantEngine.calculate_style_factor_heuristics(hist, info, market_returns)

    @staticmethod
    def calculate_qmj_score(info: Dict, hist: pd.DataFrame,
                            metrics: Optional[FundamentalMetrics] = None
                            ) -> Tuple[float, float, float]:
        """Calculate Quality-Minus-Junk (QMJ) score.

        [W5 FIX] This used to re-derive debt/equity and dividend yield straight
        from the raw provider payload with its own conditional conversions:
        ``raw/100 if raw > 10 else raw`` for D/E and ``/100 if > 1.0`` for
        yield. FundamentalAnalysisEngine already normalizes both — always
        dividing D/E by 100, because Yahoo reports it as a percentage — so the
        two paths disagreed. A company reporting ``debtToEquity: 5.0`` (0.05x,
        an almost debt-free balance sheet) was read here as 5.0x, earning
        ``junk_score = 3.0`` and a negative QMJ spread.

        Conditional unit conversion is always a latent bug: the condition
        eventually matches a legitimate value. Normalization now happens once,
        at ingestion, and this function reads the normalized metrics.
        """
        quality_score = 0.0
        junk_score = 0.0

        try:
            if metrics is None:
                metrics = FundamentalAnalysisEngine.analyze_fundamentals(info)

            # Quality components
            profitability_quality = 0.0
            roe = metrics.roe
            roa = metrics.roa
            if roe > 0.20: profitability_quality += 2.0
            if roa > 0.10: profitability_quality += 1.5

            # Growth quality
            growth_quality = 0.0
            revenue_growth = metrics.revenue_growth
            earnings_growth = metrics.earnings_growth
            if 0.10 < revenue_growth < 0.30: growth_quality += 2.0
            if earnings_growth > 0.15: growth_quality += 1.5

            # Safety quality — reads the single normalized D/E.
            safety_quality = 0.0
            debt_to_equity = metrics.debt_to_equity
            de_known = bool(metrics.data_available.get('debt_to_equity', False))
            current_ratio = metrics.current_ratio
            if de_known and debt_to_equity < 0.5: safety_quality += 2.0
            if current_ratio > 1.5: safety_quality += 1.5

            # Payout quality — reads the single normalized dividend yield.
            payout_quality = 0.0
            div_yield = metrics.dividend_yield
            payout_ratio = metrics.payout_ratio
            if 0.02 < div_yield < 0.06: payout_quality += 1.5
            if 0.20 < payout_ratio < 0.60: payout_quality += 1.0

            quality_score = (profitability_quality + growth_quality +
                             safety_quality + payout_quality) / 4.0

            # Junk score (inverse of quality). An UNKNOWN D/E is not
            # evidence of leverage — the old default of 100 (read as 100x)
            # convicted every name with no debt data reported.
            if roe < 0 or (de_known and debt_to_equity > 2.0):
                junk_score = 3.0
            elif roe < 0.05 or (de_known and debt_to_equity > 1.0):
                junk_score = 2.0

            qmj_spread = quality_score - junk_score

        except Exception as e:
            logger.debug(f"Error in QMJ calculation: {e}")

        return quality_score, junk_score, qmj_spread

    @staticmethod
    def calculate_multi_horizon_momentum(hist: pd.DataFrame) -> Dict[str, float]:
        """Calculate multi-horizon momentum scores"""
        momentum = {
            'momentum_1m': 0.0,
            'momentum_3m': 0.0,
            'momentum_6m': 0.0,
            'momentum_12m': 0.0,
            'momentum_12_2': 0.0,
            'momentum_composite': 0.0
        }

        try:
            if len(hist) < 20:
                return momentum

            closes = hist['Close']

            # 1-month momentum (20 trading days)
            if len(closes) >= 21:
                momentum['momentum_1m'] = float(closes.iloc[-1] / closes.iloc[-21] - 1)

            # 3-month momentum (60 trading days)
            if len(closes) >= 61:
                momentum['momentum_3m'] = float(closes.iloc[-1] / closes.iloc[-61] - 1)

            # 6-month momentum (126 trading days)
            if len(closes) >= 127:
                momentum['momentum_6m'] = float(closes.iloc[-1] / closes.iloc[-127] - 1)

            # [W4 FIX] Same shared definition as TechnicalAnalysisEngine.
            momentum['momentum_12_2'] = compute_momentum_12_2(closes)
            # A true 12-month total return, no longer an alias of 12-2. The
            # alias meant a field labelled "12-month return" was not one.
            anchor = _twelve_month_anchor(closes)
            if anchor is not None:
                momentum['momentum_12m'] = float(closes.iloc[-1] / closes.iloc[anchor] - 1)

            # Composite score (academic tilt toward longer-horizon momentum)
            weights = {'1m': 0.10, '3m': 0.20, '6m': 0.30, '12_2': 0.40}
            momentum['momentum_composite'] = (
                    momentum['momentum_1m'] * weights['1m'] +
                    momentum['momentum_3m'] * weights['3m'] +
                    momentum['momentum_6m'] * weights['6m'] +
                    momentum['momentum_12_2'] * weights['12_2']
            )

        except Exception as e:
            logger.debug(f"Error in multi-horizon momentum: {e}")

        return momentum

    @staticmethod
    def calculate_betting_against_beta(beta: float, returns: float,
                                       risk_free_rate: float = 0.04) -> Tuple[float, float]:
        """Calculate Betting-Against-Beta (BAB) score"""
        bab_score = 0.0
        beta_adjusted_return = 0.0

        try:
            # BAB: Low beta stocks tend to outperform high beta stocks
            # on a risk-adjusted basis
            if beta < 0.7:  # Low beta
                bab_score = 3.0
            elif beta < 0.9:
                bab_score = 2.0
            elif beta <= 1.1:  # Market beta
                bab_score = 1.0
            elif beta <= 1.5:  # High beta
                bab_score = -1.0
            else:  # Very high beta
                bab_score = -2.0

            # Beta-adjusted return
            if beta > 0:
                beta_adjusted_return = (returns - risk_free_rate) / beta

        except Exception as e:
            logger.debug(f"Error in BAB calculation: {e}")

        return bab_score, beta_adjusted_return


class MarketRegimeDetector:
    """Detect and classify market regime for dynamic strategy adjustment"""
    # Only a real quote may enter this cache (see detect_regime). It used to
    # start pre-seeded with US 20.0 / INDIA 18.0, so a failed fetch published
    # those invented figures as a "last good" VIX reading and the "default"
    # placeholder branch was unreachable (audit 2026-10-17 #1).
    _last_good_vix_by_market: Dict[str, float] = {}

    @staticmethod
    def detect_regime(vix: float = 0.0, spy_hist: Optional[pd.DataFrame] = None, market: str = "US") -> MarketRegime:
        """
        **FIX #2: Market-aware regime detection**
        Uses SPY for US market, NIFTY (^NSEI) for India market
        
        Args:
            vix: VIX value (optional)
            spy_hist: SPY/NIFTY historical data (optional)
            market: "US" or "INDIA"
        """
        regime = MarketRegime()
        vix_value = vix
        # [W2 FIX] Tracks whether vix_value came from a realized-vol proxy
        # rather than a real implied-vol quote. A proxy must never be allowed
        # to justify above-1.0x exposure.
        vix_is_proxy = False
        vix_source = "quote"
        
        # **Market-specific index selection**
        if market == "INDIA":
            index_ticker = "^NSEI"  # NIFTY 50
            index_name = "NIFTY"
        else:
            index_ticker = "SPY"
            index_name = "SPY"
        
        try:
            # [CRITICAL FIX] Use HybridDataProvider to avoid rate limiting
            try:
                polygon_module = _import_optional("polygon_provider", "modules.polygon_provider")
                if polygon_module:
                    HybridDataProvider = polygon_module.HybridDataProvider
                    hybrid = HybridDataProvider()
                else:
                    hybrid = None
            except ImportError:
                hybrid = None
            
            # **Fetch VIX with multiple fallbacks**
            # **FIX: Market-aware VIX symbol**
            if vix_value == 0.0:
                vix_symbol = "^INDIAVIX" if market == "INDIA" else "^VIX"
                
                try:
                    # Try HybridDataProvider first (Polygon.io)
                    if hybrid:
                        hist = hybrid.get_historical(vix_symbol, '1m')
                        if not hist.empty:
                            vix_value = float(hist['Close'].iloc[-1])
                            logger.info(f"✓ Market VIX ({vix_symbol}) fetched via Polygon: {vix_value:.2f}")
                    
                    # Fallback to yfinance
                    if vix_value == 0.0:
                        vix_ticker = yf.Ticker(vix_symbol)
                        hist = vix_ticker.history(period="5d", auto_adjust=True, actions=False)
                        if not hist.empty:
                            vix_value = float(hist['Close'].iloc[-1])
                            logger.info(f"✓ Market VIX ({vix_symbol}) fetched: {vix_value:.2f}")
                except Exception as e:
                    logger.warning(f"Could not fetch VIX ({vix_symbol}): {e}")
                
                # **Fallback 1: Calculate implied volatility from market index**
                if vix_value == 0.0:
                    try:
                        if spy_hist is None or len(spy_hist) < 30:
                            # Try HybridDataProvider first
                            if hybrid:
                                spy_hist = hybrid.get_historical(index_ticker, '3m')
                            if spy_hist is None or spy_hist.empty:
                                index = yf.Ticker(index_ticker)
                                spy_hist = index.history(period="30d", auto_adjust=True, actions=False)
                        
                        if spy_hist is not None and len(spy_hist) >= 20:
                            returns = spy_hist['Close'].pct_change(fill_method=None)
                            # [W2 FIX] Realized volatility is NOT implied
                            # volatility. Realized runs below implied most of
                            # the time (the variance risk premium), so feeding
                            # it in raw biased the regime toward LOW vol —
                            # where recommended_exposure is 1.2x. The proxy was
                            # levering the book up precisely when the real
                            # signal was missing. Scale by a documented premium
                            # and flag the regime so exposure stays capped at
                            # 1.0 (see vix_is_proxy below).
                            daily_vol = returns.std()
                            raw_proxy = daily_vol * np.sqrt(252) * 100
                            vix_value = raw_proxy * TradingConfig.VIX_PROXY_PREMIUM
                            vix_is_proxy = True
                            vix_source = "realized_vol_proxy"
                            logger.warning(
                                f"⚠️  VIX unavailable; using {index_name} realized-vol proxy "
                                f"{raw_proxy:.1f} x {TradingConfig.VIX_PROXY_PREMIUM:.2f} premium "
                                f"= {vix_value:.1f}. Exposure will be capped at 1.0x."
                            )
                    except Exception as e:
                        logger.warning(f"⚠️  {index_name} volatility calculation failed: {e}")
                
                # **Fallback 2: use last known good VIX, then conservative default**
                if vix_value == 0.0 or vix_value < 5 or vix_value > 100:
                    cached_vix = MarketRegimeDetector._last_good_vix_by_market.get(market, 20.0)
                    # Neither is a current measurement: both are flagged, so
                    # exposure stays capped and no surface prints them as VIX.
                    vix_is_proxy = True
                    if market in MarketRegimeDetector._last_good_vix_by_market and 5 <= cached_vix <= 100:
                        vix_value = cached_vix
                        vix_source = "last_good"
                        logger.warning(f"⚠️  Using cached last-good VIX for {market}: {vix_value:.2f}")
                    else:
                        vix_value = 20.0  # Conservative placeholder for the regime rules only
                        vix_source = "default"
                        logger.warning(f"⚠️  Using conservative default VIX: {vix_value:.2f}")

            if vix_source == "quote" and 5 <= vix_value <= 100:
                # Only a real quote may become the "last good" value.
                MarketRegimeDetector._last_good_vix_by_market[market] = float(vix_value)

            regime.vix_level = vix_value
            regime.vix_is_proxy = vix_is_proxy
            regime.vix_source = vix_source

            # **Fetch market index data with fallback**
            # [W1 FIX] This used to request period="6mo" (~126 bars), which is
            # fewer than the 200 the trend test needs. Combined with the
            # `else sma_50` alias below, that made `price > sma_50 > sma_200`
            # evaluate `sma_50 > sma_50` — always False — so BULL and BEAR were
            # literally unreachable and the engine sat permanently in
            # RANGING/VOLATILE. Two years gives a genuine 200-day average.
            if spy_hist is None or len(spy_hist) < 200:
                try:
                    index = yf.Ticker(index_ticker)
                    spy_hist = index.history(period="2y", auto_adjust=True, actions=False)
                    logger.info(
                        f"✓ Fetched {index_name} data for regime detection "
                        f"({len(spy_hist)} bars)"
                    )
                except Exception as e:
                    logger.warning(f"⚠️  {index_name} data unavailable: {e}")
                    spy_hist = None

            # After the close Yahoo returns today's bar with volume but no close
            # yet. That NaN made both moving averages NaN, the trend check
            # failed and the regime read UNKNOWN (exposure capped at 60%) on
            # every evening scan. A bar without a close is not a session.
            if spy_hist is not None and 'Close' in getattr(spy_hist, 'columns', ()):
                _rows = len(spy_hist)
                spy_hist = spy_hist[spy_hist['Close'].notna()]
                if len(spy_hist) < _rows:
                    logger.info(f"{index_name}: dropped {_rows - len(spy_hist)} bar(s) without a close")

            # **Determine volatility regime** (always succeeds)
            R = _T.REGIME
            if vix_value < R.VIX_LOW:
                regime.volatility_regime = "LOW"
                # A proxy-derived reading may not lever the book up.
                regime.recommended_exposure = R.EXPOSURE_NORMAL if vix_is_proxy else R.EXPOSURE_LOW_VOL
            elif vix_value < R.VIX_NORMAL_MAX:
                regime.volatility_regime = "NORMAL"
                regime.recommended_exposure = R.EXPOSURE_NORMAL
            elif vix_value < R.VIX_HIGH_MAX:
                regime.volatility_regime = "HIGH"
                regime.recommended_exposure = R.EXPOSURE_HIGH
            else:
                regime.volatility_regime = "EXTREME"
                regime.recommended_exposure = R.EXPOSURE_EXTREME

            # **Determine trend regime** (graceful degradation if index unavailable)
            # [W1 FIX] The 200-bar requirement is now honest. Previously a
            # short series silently substituted sma_50 for sma_200, which
            # made both trend branches unreachable rather than reporting that
            # the trend could not be determined.
            if spy_hist is not None and len(spy_hist) >= 200:
                try:
                    closes = spy_hist['Close']
                    sma_50 = float(closes.rolling(50).mean().iloc[-1])
                    sma_200 = float(closes.rolling(200).mean().iloc[-1])
                    current_price = float(closes.iloc[-1])

                    # Trend strength
                    if len(closes) >= 20:
                        returns_20d = closes.pct_change(20, fill_method=None).iloc[-1]
                        regime.trend_strength = float(abs(returns_20d))

                    if not (np.isfinite(sma_50) and np.isfinite(sma_200)):
                        raise ValueError("moving averages are not finite")

                    # Regime classification. Price must clear the 50-day by
                    # TREND_BAND_PCT, so a close a hair either side of the
                    # average does not flip every weight in the scan.
                    band = float(R.TREND_BAND_PCT)
                    if (current_price > sma_50 * (1 + band) and sma_50 > sma_200
                            and vix_value < R.VIX_VOLATILE):
                        regime.regime_type = "BULL"
                        regime.confidence = 0.8
                    elif current_price < sma_50 * (1 - band) and sma_50 < sma_200:
                        regime.regime_type = "BEAR"
                        regime.confidence = 0.8
                    elif vix_value > R.VIX_VOLATILE:
                        regime.regime_type = "VOLATILE"
                        regime.confidence = 0.7
                    else:
                        regime.regime_type = "RANGING"
                        regime.confidence = 0.6
                except Exception as e:
                    logger.warning(f"⚠️  {index_name} trend analysis failed: {e}")
                    # Use volatility-based classification only
                    if vix_value > R.VIX_VOLATILE:
                        regime.regime_type = "VOLATILE"
                        regime.confidence = 0.5
                    else:
                        regime.regime_type = "UNKNOWN"
                        regime.confidence = 0.2
            else:
                # Not enough history to judge trend. Say so rather than
                # inventing a classification from an aliased moving average.
                bars = 0 if spy_hist is None else len(spy_hist)
                logger.warning(
                    f"⚠️  {index_name} history is {bars} bars (need 200 for a "
                    f"200-day trend); trend regime reported as UNKNOWN."
                )
                if vix_value > R.VIX_VOLATILE:
                    regime.regime_type = "VOLATILE"
                    regime.confidence = 0.5
                else:
                    regime.regime_type = "UNKNOWN"
                    regime.confidence = 0.2

        except Exception as e:
            logger.error(f"❌ Critical error in regime detection: {e}")
            # Ultimate fallback - conservative regime
            regime.vix_level = 20.0
            regime.volatility_regime = "NORMAL"
            regime.regime_type = "NEUTRAL"
            regime.recommended_exposure = _T.REGIME.EXPOSURE_ERROR_FALLBACK  # Conservative
            regime.confidence = 0.3
            detection_failed = True
        else:
            detection_failed = False

        # Cash by regime, from config. A failed detection keeps its own (larger)
        # share; it used to be set to 35% and then overwritten by the NEUTRAL
        # branch's 25% (audit 2026-10-17 #2).
        cash = _T.REGIME.CASH_PCT
        regime.recommended_cash_pct = cash["ERROR"] if detection_failed else cash.get(
            regime.regime_type, cash["NEUTRAL"])

        # A cash account cannot hold more than its capital (audit #3).
        regime.recommended_exposure = min(regime.recommended_exposure, _T.REGIME.EXPOSURE_MAX)

        # The weights scoring actually uses. This used to be a second,
        # different table (momentum/quality/growth/value) that nothing read.
        regime.factor_weights = dict(_REGIME_WEIGHTS.get(regime.regime_type, _REGIME_WEIGHTS["NEUTRAL"]))

        return regime


class TimeframeProjector:
    """Project statistical timeframes for price targets"""

    @staticmethod
    def project_timeframe(current_price: float, target_price: float,
                          volatility: float, momentum: float,
                          sector: str) -> TimeframeProjection:
        """Calculate projected timeframe to reach target"""
        projection = TimeframeProjection()

        try:
            expected_return = (target_price / current_price - 1) if current_price > 0 else 0
            projection.expected_return = expected_return

            # Base timeframe calculation using volatility
            # Higher volatility = faster moves
            if volatility > 0:
                # [M6 FIX] For a diffusion, the expected time to travel a
                # distance X at per-period volatility sigma scales with
                # (X/sigma)**2, not X/sigma. The old linear form projected
                # every target far too soon, and increasingly so for larger
                # moves: a 3-sigma target was dated at 3 months when the same
                # volatility assumption implies about 9. Still a scenario
                # horizon, not a promise the target is reached.
                monthly_volatility = volatility / np.sqrt(12)
                sigma_multiples = abs(expected_return) / monthly_volatility
                base_months = sigma_multiples ** 2
                base_months = max(1, min(base_months, 24))  # Cap between 1-24 months
            else:
                base_months = 6

            # Momentum adjustment
            if momentum > 0.10:  # Strong upward momentum
                base_months *= 0.7  # Faster timeline
            elif momentum > 0.05:
                base_months *= 0.85
            elif momentum < -0.05:  # Negative momentum
                base_months *= 1.3  # Slower timeline

            # Sector-specific patterns
            sector_multipliers = {
                'Information Technology': 0.8,  # Faster moves
                'Communication Services': 0.85,
                'Consumer Discretionary': 0.9,
                'Health Care': 1.0,
                'Financials': 0.95,
                'Industrials': 1.05,
                'Energy': 0.9,
                'Materials': 1.0,
                'Consumer Staples': 1.15,  # Slower, stable moves
                'Utilities': 1.2,
                'Real Estate': 1.1
            }

            sector_mult = sector_multipliers.get(sector, 1.0)
            base_months *= sector_mult

            projection.horizon_months = float(base_months)
            projection.review_date = (
                datetime.now() + timedelta(days=int(base_months * 30.44))
            ).strftime('%Y-%m-%d')
            projection.target_timeframe = describe_horizon(base_months)

            # Volatility-adjusted timeline
            if volatility < 0.15:
                projection.volatility_adjusted_timeline = "Low volatility - steady progression expected"
            elif volatility < 0.30:
                projection.volatility_adjusted_timeline = "Moderate volatility - normal timeline"
            elif volatility < 0.50:
                projection.volatility_adjusted_timeline = "High volatility - accelerated moves likely"
            else:
                projection.volatility_adjusted_timeline = "Extreme volatility - wide timeline range"

            # Sector rally pattern
            tech_sectors = ['Information Technology', 'Communication Services']
            defensive_sectors = ['Utilities', 'Consumer Staples', 'Health Care']
            cyclical_sectors = ['Energy', 'Materials', 'Industrials', 'Financials']

            if sector in tech_sectors:
                projection.sector_rally_pattern = "Tech momentum - potential for rapid appreciation"
            elif sector in defensive_sectors:
                projection.sector_rally_pattern = "Defensive - gradual, steady gains typical"
            elif sector in cyclical_sectors:
                projection.sector_rally_pattern = "Cyclical - market-dependent timing"
            else:
                projection.sector_rally_pattern = "Balanced sector dynamics"

            # Momentum acceleration factor
            projection.momentum_acceleration = momentum

            # No probability is emitted without an out-of-sample calibrated
            # target-hit model.  The old 40/55/70-style numbers were subjective
            # thresholds, not probabilities.
            projection.probability_success = 0.0

        except Exception as e:
            logger.debug(f"Error in timeframe projection: {e}")
            projection.horizon_months = 4.5
            projection.review_date = (
                datetime.now() + timedelta(days=137)
            ).strftime('%Y-%m-%d')
            projection.target_timeframe = describe_horizon(4.5)
            projection.probability_success = 0.0

        return projection


# ============================================================================
# CROSS-SECTIONAL UNIVERSE STATISTICS (Fix #1)
# ============================================================================

class UniverseStats:
    """Collects per-metric distributions across the scanned universe so that
    individual stock metrics can be converted to z-scores / percentile ranks
    rather than evaluated against absolute thresholds.

    Usage:
        1. After scan_market() collects all StockAnalysis objects, call
           ``UniverseStats.from_analyses(opportunities)`` to build stats.
        2. Call ``engine.normalize_scores(analysis, stats)`` on each stock to
           replace heuristic scores with z-score-based scores.
    """

    def __init__(self):
        # Fundamental distributions  (metric_name -> list of raw values)
        self.pe_ratios: List[float] = []
        self.forward_pe: List[float] = []
        self.peg_ratios: List[float] = []
        self.pb_ratios: List[float] = []
        self.ps_ratios: List[float] = []
        self.revenue_growth: List[float] = []
        self.earnings_growth: List[float] = []
        self.profit_margins: List[float] = []
        self.roe_values: List[float] = []
        self.debt_to_equity: List[float] = []
        self.dividend_yields: List[float] = []
        self.fcf_yields: List[float] = []

        # Technical distributions
        self.rsi_values: List[float] = []
        self.volume_ratios: List[float] = []
        self.momentum_1m: List[float] = []
        self.momentum_3m: List[float] = []
        self.momentum_6m: List[float] = []
        self.momentum_12_2: List[float] = []
        self.roc_values: List[float] = []
        self.adx_values: List[float] = []
        self.sharpe_ratios: List[float] = []
        self.max_drawdowns: List[float] = []
        self.betas: List[float] = []

        # Sector-bucketed PE distributions
        self.sector_pe: Dict[str, List[float]] = defaultdict(list)
        self.sector_pb: Dict[str, List[float]] = defaultdict(list)
        self.sector_ps: Dict[str, List[float]] = defaultdict(list)

        # Raw score distributions for final normalization
        self.raw_tech_scores: List[float] = []
        self.raw_fund_scores: List[float] = []
        self.raw_mom_scores: List[float] = []
        self.raw_quality_scores: List[float] = []
        self.raw_value_scores: List[float] = []
        self.raw_overall_scores: List[float] = []

    # ------------------------------------------------------------------
    @classmethod
    def from_analyses(cls, analyses: List[StockAnalysis]) -> 'UniverseStats':
        """Build universe stats from a list of completed analyses."""
        stats = cls()
        for a in analyses:
            fm = a.fundamental_metrics
            ti = a.technical_indicators
            rm = a.risk_metrics
            sector = a.sector or a.gics_sector or 'Unknown'

            # Fundamental. Missing provider fields default to 0.0, so every
            # population gates on data_available — a missing D/E used to enter
            # as a real 0.0 and earn the BEST leverage z-score, and the zeros
            # distorted everyone else's.
            if fm.has_metric('pe_ratio') and fm.pe_ratio > 0:
                stats.pe_ratios.append(fm.pe_ratio)
                stats.sector_pe[sector].append(fm.pe_ratio)
            if fm.has_metric('forward_pe') and fm.forward_pe > 0:
                stats.forward_pe.append(fm.forward_pe)
            if fm.has_metric('peg_ratio') and fm.peg_ratio > 0:
                stats.peg_ratios.append(fm.peg_ratio)
            if fm.has_metric('pb_ratio') and fm.pb_ratio > 0:
                stats.pb_ratios.append(fm.pb_ratio)
                stats.sector_pb[sector].append(fm.pb_ratio)
            if fm.has_metric('ps_ratio') and fm.ps_ratio > 0:
                stats.ps_ratios.append(fm.ps_ratio)
                stats.sector_ps[sector].append(fm.ps_ratio)
            if fm.has_metric('revenue_growth'):
                stats.revenue_growth.append(fm.revenue_growth)
            if fm.has_metric('earnings_growth'):
                stats.earnings_growth.append(fm.earnings_growth)
            if fm.has_metric('profit_margin'):
                stats.profit_margins.append(fm.profit_margin)
            if fm.has_metric('roe'):
                stats.roe_values.append(fm.roe)
            if fm.has_metric('debt_to_equity'):
                stats.debt_to_equity.append(fm.debt_to_equity)
            if fm.dividend_yield and fm.dividend_yield > 0:
                stats.dividend_yields.append(fm.dividend_yield)
            if fm.has_metric('free_cash_flow') and fm.market_cap and fm.market_cap > 0:
                stats.fcf_yields.append(fm.free_cash_flow / fm.market_cap)

            # Technical
            if ti.rsi is not None and not np.isnan(ti.rsi):
                stats.rsi_values.append(ti.rsi)
            if ti.volume_ratio is not None and not np.isnan(ti.volume_ratio):
                stats.volume_ratios.append(ti.volume_ratio)
            if ti.momentum_1m is not None:
                stats.momentum_1m.append(ti.momentum_1m)
            if ti.momentum_3m is not None:
                stats.momentum_3m.append(ti.momentum_3m)
            if getattr(ti, 'momentum_6m', None) is not None:
                stats.momentum_6m.append(ti.momentum_6m)
            if getattr(ti, 'momentum_12_2', 0.0):
                stats.momentum_12_2.append(ti.momentum_12_2)
            if ti.roc is not None:
                stats.roc_values.append(ti.roc)
            if ti.adx is not None:
                stats.adx_values.append(ti.adx)

            # Risk (a zero-vol RiskMetrics is the "could not compute" default)
            if getattr(rm, 'volatility', 0.0):
                stats.sharpe_ratios.append(rm.sharpe_ratio)
                stats.max_drawdowns.append(rm.max_drawdown)
            if rm.beta is not None:
                stats.betas.append(rm.beta)

            # Raw sub-scores
            stats.raw_tech_scores.append(a.technical_score)
            stats.raw_fund_scores.append(a.fundamental_score)
            stats.raw_mom_scores.append(a.momentum_score)
            stats.raw_quality_scores.append(a.quality_score)
            stats.raw_value_scores.append(a.value_score)
            stats.raw_overall_scores.append(a.overall_score)

        return stats

    # ------------------------------------------------------------------
    @staticmethod
    def _zscore(value: float, population: List[float], invert: bool = False) -> float:
        """Compute z-score of *value* within *population*.

        If *invert* is True, a lower raw value yields a higher z-score
        (useful for P/E, debt-to-equity where lower is better).

        Returns 0.0 when population is too small (< 5) or std == 0.
        """
        if len(population) < 5:
            return 0.0
        arr = np.asarray(population, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 5 or not np.isfinite(value):
            return 0.0
        # Robust z: median and MAD (scaled to sigma), clipped to +/-3. One
        # 2,500% earnings-growth name used to drag a 30%-growth stock's
        # mean/std z from +2.7 to -0.3 and collapse every score toward the
        # middle; percentile winsorising does not help at the 10-50 name
        # populations a scan produces.
        center = float(np.median(arr))
        scale = 1.4826 * float(np.median(np.abs(arr - center)))
        if scale < 1e-9:
            scale = float(np.std(arr))
        if scale < 1e-9:
            return 0.0
        z = max(-3.0, min(3.0, (float(value) - center) / scale))
        return -z if invert else z

    @staticmethod
    def _percentile_rank(value: float, population: List[float]) -> Optional[float]:
        """Percentile rank (0-100) of *value*, or None for a population too small to rank."""
        if len(population) < 5:
            return None
        arr = np.array(population)
        return float(np.sum(arr <= value) / len(arr) * 100)

    def zscore_to_score(self, z: float, scale: float = 10.0) -> float:
        """Map a z-score to a 0-*scale* score using the CDF (percentile-based).

        z = 0  → 50th percentile → score 5.0
        z = +2 → ~97th percentile → score ~9.7
        z = -2 → ~2nd percentile  → score ~0.3
        """
        from scipy.stats import norm
        pct = norm.cdf(z)  # 0-1
        return round(min(scale, max(0.0, pct * scale)), 2)


# ============================================================================
# SCORING ENGINES
# ============================================================================

class ScoringEngine:

    # Neutral value a factor shrinks toward in proportion to its missing inputs.
    MISSING_DATA_NEUTRAL = 4.0

    @staticmethod
    def _coverage_scaled(earned: float, max_present: float, max_total: float) -> float:
        """Put a factor's earned points on the 0-10 scale, judged only on the
        inputs that were actually measured.

        A missing input is unknown, not bad: the earned points are rescaled to
        the points that were available, then shrunk toward a neutral value in
        proportion to what is missing, so thin data can never outscore complete
        data and absent data never scores as a weak company (audit #10).
        """
        if max_present <= 0 or max_total <= 0:
            return 0.0
        coverage = max_present / max_total
        scaled = earned * 10.0 / max_present
        return coverage * scaled + (1.0 - coverage) * ScoringEngine.MISSING_DATA_NEUTRAL

    @staticmethod
    def calculate_technical_score(indicators: TechnicalIndicators) -> float:
        """Enhanced technical scoring (0-10 scale)"""
        score = 0.0

        try:
            # RSI Analysis (2.0 points)
            # [M2 FIX] This used to pay 1.7 for deeply oversold (a
            # mean-reversion thesis) AND up to 1.0 for strong ADX trend (a
            # trend-following thesis) inside the same number. A score that
            # rewards a condition and its opposite is not measuring anything —
            # it cannot be interpreted, attributed or improved, and it
            # compresses toward the middle so cross-sectional ranking degrades.
            # This sub-score now has ONE thesis: constructive trend structure
            # that is not yet stretched. Oversold is no longer paid for here;
            # the oversold-bounce setup is handled explicitly by
            # _determine_signal_regime_aware, which is where that thesis lives.
            rsi = indicators.rsi
            if rsi is None or (isinstance(rsi, float) and np.isnan(rsi)):
                rsi = 50.0  # Neutral default
            if TradingConfig.RSI_OPTIMAL_LOW <= rsi <= TradingConfig.RSI_OPTIMAL_HIGH:
                score += 2.0
            elif TradingConfig.RSI_OPTIMAL_HIGH < rsi <= TradingConfig.RSI_OVERBOUGHT:
                score += 1.5   # trending up, not yet extended
            elif TradingConfig.RSI_OVERSOLD <= rsi < TradingConfig.RSI_OPTIMAL_LOW:
                score += 1.0   # weak but not broken
            elif rsi < TradingConfig.RSI_OVERSOLD:
                score += 0.3   # downtrend; not a structural positive
            else:
                score += 0.2   # overbought

            # Moving Average Confluence (2.5 points)
            price = indicators.current_price
            if price > indicators.sma_20:
                score += 0.9
            if price > indicators.sma_50:
                score += 0.9
            if price > indicators.sma_200:
                score += 0.7

            # Golden Cross bonus
            if indicators.sma_50 > indicators.sma_200:
                score += 0.5

            # MACD Analysis (2.0 points)
            if indicators.macd > indicators.macd_signal:
                score += 1.0
            if indicators.macd_histogram > 0:
                score += 0.6
            if indicators.macd > 0:
                score += 0.4

            # Stochastic Oscillator (1.0 point). [Audit #7] Oversold no longer
            # earns 0.8: like the RSI and Bollinger legs, this score has one
            # thesis (constructive trend), and oversold is the opposite.
            if 20 <= indicators.stoch_k <= 80:
                score += 1.0

            # Volume Confirmation (1.5 points). [Audit #7] Heavy volume only
            # confirms a move UP. It used to score the same on a selloff, so
            # distribution read as accumulation.
            vol_ratio = indicators.volume_ratio
            rising = float(getattr(indicators, 'momentum_1w', 0.0) or 0.0) > 0
            if rising and vol_ratio > 2.0:
                score += 1.5
            elif rising and vol_ratio > 1.5:
                score += 1.2
            elif rising and vol_ratio > 1.0:
                score += 0.7

            # Bollinger Bands Position (1.0 point)
            # [M2 FIX] Was 0.8 for "near the lower band" — again rewarding the
            # opposite of the trend thesis this sub-score measures.
            bb_pos = indicators.bb_position
            if 0.4 <= bb_pos <= 0.8:
                score += 1.0   # upper half of the channel, not pinned to the top
            elif 0.2 <= bb_pos < 0.4:
                score += 0.5
            elif bb_pos > 0.8:
                score += 0.3   # extended
            else:
                score += 0.1   # at the lower band

            # [M1 FIX] ADX intentionally NOT scored here. It was counted in
            # both technical_score and momentum_score — a combined 0.35 weight
            # on one indicator — so the nominal weights were not the effective
            # weights and the composite was a bigger trend bet than the
            # 15/20 split suggested. Trend strength now lives solely in
            # momentum_score, where it belongs.

            # === BEARISH DEDUCTIONS (P2 FIX: scoring was bullish-only) ===
            # Death cross penalty
            if indicators.sma_50 < indicators.sma_200:
                score -= 0.8

            # Price below all major MAs = bearish structure
            if price < indicators.sma_20 and price < indicators.sma_50 and price < indicators.sma_200:
                score -= 1.0

            # MACD bearish divergence
            if indicators.macd < indicators.macd_signal and indicators.macd_histogram < 0:
                score -= 0.5

            # Overbought stochastic penalty
            if indicators.stoch_k > 80:
                score -= 0.3

        except Exception as e:
            # [Audit #23] A partial sum is not a score: the factor is unknown.
            logger.warning(f"technical scoring failed ({type(e).__name__}: {e}); scored neutral")
            return ScoringEngine.MISSING_DATA_NEUTRAL

        return min(10.0, max(0.0, score))

    @staticmethod
    def calculate_fundamental_score(metrics: FundamentalMetrics) -> float:
        """Score based on fundamental metrics (0-10 scale)"""
        score = 0.0

        try:
            availability = getattr(metrics, 'data_available', {}) or {}

            # Maximum points each field can earn below. Coverage is normalised
            # by the points AVAILABLE, not the field count (see _coverage_scaled).
            # [Audit #6] ROIC, gross margin and debt/equity are scored ONCE, in
            # quality_score. They used to be paid here as well, so a profitable,
            # low-debt company was paid two or three times for the same fact and
            # the stated regime weights were not the effective weights. This
            # sub-score is growth and profitability.
            MAX_POINTS = {
                'revenue_growth': 1.5, 'earnings_growth': 1.0, 'profit_margin': 1.0,
                'roe': 1.0,
            }
            present_fields: List[str] = []

            def _present_raw(name: str, value: Any, positive_only: bool) -> bool:
                # Engine-produced metrics always carry explicit provenance.
                # For legacy/manual instances, non-zero finite values are the
                # safest available approximation; a default zero is not data.
                if availability:
                    return bool(availability.get(name, False))
                try:
                    numeric = float(value)
                    return np.isfinite(numeric) and (numeric > 0 if positive_only else numeric != 0)
                except (TypeError, ValueError):
                    return False

            def present(name: str, value: Any, *, positive_only: bool = False) -> bool:
                ok = _present_raw(name, value, positive_only)
                if ok:
                    present_fields.append(name)
                return ok

            # [M1 FIX] Valuation multiples (P/E, forward P/E, PEG) are NOT
            # scored here. They were counted in both fundamental_score and
            # value_score — a combined 0.45 weight double-counting the same
            # information — so cheapness was paid for twice while business
            # quality was paid for once. Valuation now lives solely in
            # value_score. This sub-score measures BUSINESS quality: growth,
            # margins, returns on capital and balance-sheet strength.

            # 1. Revenue Growth
            rev_growth = metrics.revenue_growth
            if present('revenue_growth', rev_growth):
                if rev_growth > 0.30:
                    score += 1.5
                elif rev_growth > 0.20:
                    score += 1.2
                elif rev_growth > 0.10:
                    score += 0.8
                elif rev_growth < -0.10:
                    score -= 1.0  # Bearish: revenue declining

            # 2. Earnings Growth
            earn_growth = metrics.earnings_growth
            if present('earnings_growth', earn_growth):
                if earn_growth > 0.25:
                    score += 1.0
                elif earn_growth > 0.15:
                    score += 0.5
                elif earn_growth < -0.15:
                    score -= 0.8  # Bearish: earnings declining

            # 3. Profit Margin
            margin = metrics.profit_margin
            if present('profit_margin', margin):
                if margin > 0.20:
                    score += 1.0
                elif margin > 0.10:
                    score += 0.5

            # 4. ROE (Return on Equity)
            roe = metrics.roe
            if present('roe', roe):
                if roe > 0.20:
                    score += 1.0
                elif roe > 0.15:
                    score += 0.5

            # **Scale by data completeness** onto the full 0-10 range.
            if not present_fields:
                return 0.0
            score = ScoringEngine._coverage_scaled(
                score, sum(MAX_POINTS[f] for f in present_fields), sum(MAX_POINTS.values()))

        except Exception as e:
            # [Audit #23] A partial sum is not a score: the factor is unknown.
            logger.warning(f"fundamental scoring failed ({type(e).__name__}: {e}); scored neutral")
            return ScoringEngine.MISSING_DATA_NEUTRAL

        return min(10.0, max(0.0, score))

    @staticmethod
    def calculate_momentum_score(indicators: TechnicalIndicators) -> float:
        """Momentum and trend strength scoring (0-10 scale).

        Horizon weights follow the evidence rather than recency:

          * 12-2 momentum  3.5  the documented factor (Jegadeesh-Titman,
                                Asness et al.): 12-month return skipping the
                                latest ~2 months.
          * 6-month        1.5
          * 3-month        2.0
          * 1-month        1.0  short-horizon returns partially REVERSE, so a
                                sharp 1-month spike earns less, not more.
          * ADX            2.0  trend strength, counted only when the trend
                                points up (price above the 50-day). ADX is
                                direction-blind; the old code paid +2.0 for a
                                strong DOWNtrend.

        Previously 12-2 was not scored at all (it was also 0.0 for every US
        name, see MOMENTUM_MIN_BARS), while 1-month return plus a 12-day ROC —
        the reversal horizon — carried half the points. In a bull tape with a
        routine 1-3 month pullback that zeroed the 35%-weighted factor for most
        of the large-cap universe.
        """
        score = 0.0

        try:
            # Intermediate-horizon momentum: 12-2 (3.5 points)
            mom_12_2 = float(getattr(indicators, 'momentum_12_2', 0.0) or 0.0)
            if mom_12_2 > 0.50:
                score += 3.5
            elif mom_12_2 > 0.30:
                score += 3.0
            elif mom_12_2 > 0.15:
                score += 2.3
            elif mom_12_2 > 0.05:
                score += 1.5
            elif mom_12_2 > 0:
                score += 0.8
            elif mom_12_2 < -0.20:
                score -= 1.5
            elif mom_12_2 < -0.05:
                score -= 0.8

            # 6-month momentum (1.5 points)
            mom_6m = float(getattr(indicators, 'momentum_6m', 0.0) or 0.0)
            if mom_6m > 0.30:
                score += 1.5
            elif mom_6m > 0.15:
                score += 1.1
            elif mom_6m > 0.05:
                score += 0.7
            elif mom_6m > 0:
                score += 0.3
            elif mom_6m < -0.15:
                score -= 1.0
            elif mom_6m < -0.05:
                score -= 0.5

            # 3-month momentum (2.0 points)
            mom_3m = indicators.momentum_3m
            if mom_3m > 0.25:
                score += 2.0
            elif mom_3m > 0.15:
                score += 1.6
            elif mom_3m > 0.05:
                score += 1.0
            elif mom_3m > 0:
                score += 0.5
            elif mom_3m < -0.15:
                score -= 1.0
            elif mom_3m < -0.05:
                score -= 0.5

            # 1-month momentum (1.0 point) — reversal-aware
            mom_1m = indicators.momentum_1m
            if mom_1m > 0.20:
                score += 0.3   # extended; short-term reversal risk
            elif mom_1m > 0.10:
                score += 0.7
            elif mom_1m > 0:
                score += 1.0
            elif mom_1m >= -0.05:
                score += 0.3   # shallow pullback, not a breakdown
            elif mom_1m < -0.10:
                score -= 0.8

            # Trend strength via ADX (2.0 points), only for an UP-trend
            adx = indicators.adx
            uptrend = (
                float(indicators.sma_50 or 0.0) > 0
                and float(indicators.current_price or 0.0) > float(indicators.sma_50)
            )
            if uptrend:
                if adx > 40:  # Very strong trend
                    score += 2.0
                elif adx > 30:
                    score += 1.7
                elif adx > 25:
                    score += 1.3
                elif adx > 20:
                    score += 0.8

            # **ELITE ENHANCEMENT #2: Momentum Crash Protection**
            # Penalize extreme short-term momentum to reduce crash risk
            # Research shows momentum crashes occur after extreme runs
            if mom_1m > 0.50:  # >50% in 1 month = extreme momentum
                crash_penalty = 0.7  # 30% penalty
                score *= crash_penalty
                logger.debug(f"Applied momentum crash protection: {mom_1m:.1%} momentum, score reduced by {(1-crash_penalty)*100:.0f}%")
            elif mom_1m > 0.40:  # >40% in 1 month = high momentum
                crash_penalty = 0.85  # 15% penalty
                score *= crash_penalty
                logger.debug(f"Applied momentum crash protection: {mom_1m:.1%} momentum, score reduced by {(1-crash_penalty)*100:.0f}%")

        except Exception as e:
            # [Audit #23] A partial sum is not a score: the factor is unknown.
            logger.warning(f"momentum scoring failed ({type(e).__name__}: {e}); scored neutral")
            return ScoringEngine.MISSING_DATA_NEUTRAL

        return min(10.0, max(0.0, score))

    @staticmethod
    def calculate_quality_score(metrics: FundamentalMetrics, risk_metrics: RiskMetrics) -> float:
        """Business and risk quality (0-10 scale).

        [M4 FIX] The old allocation of the 10 points was: Sharpe 3.0, max
        drawdown 2.0, **market cap 2.0**, beta 1.0, **dividend yield 2.0** —
        with no ROIC, no margin stability, no leverage and no earnings
        variability anywhere in it. That is a low-volatility + large-cap +
        payout factor wearing a quality label. Two of the five inputs were
        past price behaviour rather than business quality, it structurally
        penalised every non-dividend compounder, and its size tilt directly
        opposed the SMB size factor computed elsewhere in the same run
        (mega-caps scored +2.0 here and -1.5 there).

        Size and payout now live in their own factors, where their sign can be
        argued explicitly, and this sub-score measures what its name claims.
        """
        score = 0.0
        # [Audit #10] Points available per input; only measured inputs count.
        MAX_POINTS = {'roic': 2.5, 'gross_margin': 1.5, 'operating_margin': 1.0,
                      'debt_to_equity': 1.0, 'sharpe': 2.0, 'max_drawdown': 2.0}
        max_present = 0.0

        try:
            # ── Business quality (6.0 points) ──────────────────────────────
            avail = getattr(metrics, 'data_available', {}) or {}

            # Return on invested capital — the core quality metric.
            if avail.get('roic', False):
                max_present += MAX_POINTS['roic']
                roic = metrics.roic
                if roic > 0.25:
                    score += 2.5
                elif roic > 0.15:
                    score += 1.8
                elif roic > 0.08:
                    score += 1.0
                elif roic < 0:
                    score -= 1.0

            # Gross margin — pricing power, and the most persistent margin line.
            # Gated on availability: a missing margin used to score as 0%.
            if avail.get('gross_margin', False) if avail else bool(metrics.gross_margin):
                max_present += MAX_POINTS['gross_margin']
                gm = metrics.gross_margin
                if gm > 0.50:
                    score += 1.5
                elif gm > 0.35:
                    score += 1.0
                elif gm > 0.20:
                    score += 0.4

            # Operating margin — operating leverage actually realised.
            if avail.get('operating_margin', False) if avail else bool(metrics.operating_margin):
                max_present += MAX_POINTS['operating_margin']
                om = metrics.operating_margin
                if om > 0.25:
                    score += 1.0
                elif om > 0.15:
                    score += 0.7
                elif om > 0.05:
                    score += 0.3
                elif om < 0:
                    score -= 0.8

            # Balance-sheet strength.
            if avail.get('debt_to_equity', False):
                max_present += MAX_POINTS['debt_to_equity']
                de = metrics.debt_to_equity
                if de < 0.3:
                    score += 1.0
                elif de < 0.7:
                    score += 0.6
                elif de > 2.0:
                    score -= 1.0

            # ── Risk quality (4.0 points) ─────────────────────────────────
            # Risk-adjusted return still belongs in quality — a business that
            # compounds without wild equity swings is genuinely higher quality —
            # but it no longer dominates the sub-score. A zero-volatility
            # RiskMetrics is the "could not compute" default (UniverseStats uses
            # the same test): its 0.0 drawdown used to earn the full 2.0.
            if float(getattr(risk_metrics, 'volatility', 0.0) or 0.0) > 0:
                max_present += MAX_POINTS['sharpe'] + MAX_POINTS['max_drawdown']
                sharpe = risk_metrics.sharpe_ratio
                if sharpe > 2.0:
                    score += 2.0
                elif sharpe > 1.5:
                    score += 1.6
                elif sharpe > 1.0:
                    score += 1.1
                elif sharpe > 0.5:
                    score += 0.6
                elif sharpe < 0:
                    score -= 1.0

                max_dd = risk_metrics.max_drawdown
                if max_dd > -0.15:
                    score += 2.0
                elif max_dd > -0.25:
                    score += 1.3
                elif max_dd > -0.35:
                    score += 0.6
                elif max_dd < -0.50:
                    score -= 1.0

            # [M2/M4 FIX] Beta is deliberately NOT scored here. The old code
            # paid for beta near 1.0 while calculate_betting_against_beta paid
            # for LOW beta in the same run — the model contradicting itself on
            # one input. Beta exposure is a portfolio-construction decision,
            # not a mark of business quality, so it is left to the BAB factor
            # and the risk grade.

            score = ScoringEngine._coverage_scaled(score, max_present, sum(MAX_POINTS.values()))

        except Exception as e:
            # [Audit #23] Unknown, not worst-case: it used to return 0.0.
            logger.warning(f"quality scoring failed ({type(e).__name__}: {e}); scored neutral")
            return ScoringEngine.MISSING_DATA_NEUTRAL

        return min(10.0, max(0.0, score))

    @staticmethod
    def calculate_value_score(metrics: FundamentalMetrics) -> float:
        """Valuation score (0-10 scale): what the market charges for the business.

        [Audit #6] ROE is profitability and is scored once, in
        fundamental_score; it used to be paid here too. [Audit #10] Each input
        counts only when the provider supplied it, and the score is judged on
        the inputs present (see _coverage_scaled).
        """
        score = 0.0
        MAX_POINTS = {'peg_ratio': 3.0, 'pb_ratio': 2.0, 'ps_ratio': 2.0, 'free_cash_flow': 1.5}
        max_present = 0.0
        avail = getattr(metrics, 'data_available', {}) or {}

        def present(name: str, value: float) -> bool:
            # Engine metrics carry provenance; a legacy/manual instance falls
            # back to "non-zero means supplied".
            if avail:
                return bool(avail.get(name, False))
            return bool(value)

        try:
            # P/E Relative to Growth
            if (present('pe_ratio', metrics.pe_ratio) and present('peg_ratio', metrics.peg_ratio)
                    and metrics.pe_ratio > 0 and metrics.peg_ratio > 0):
                max_present += MAX_POINTS['peg_ratio']
                if metrics.peg_ratio < 0.7:
                    score += 3.0
                elif metrics.peg_ratio < 1.0:
                    score += 2.5
                elif metrics.peg_ratio < 1.5:
                    score += 1.5
                elif metrics.peg_ratio < 2.0:
                    score += 0.8

            # Price to Book (negative book value is supplied and earns nothing)
            pb = metrics.pb_ratio
            if present('pb_ratio', pb):
                max_present += MAX_POINTS['pb_ratio']
                if 0 < pb < 1.5:
                    score += 2.0
                elif 1.5 <= pb < 3.0:
                    score += 1.5
                elif 3.0 <= pb < 5.0:
                    score += 0.8

            # Price to Sales
            ps = metrics.ps_ratio
            if present('ps_ratio', ps):
                max_present += MAX_POINTS['ps_ratio']
                if 0 < ps < 2.0:
                    score += 2.0
                elif 2.0 <= ps < 4.0:
                    score += 1.5
                elif 4.0 <= ps < 8.0:
                    score += 0.8

            # Free Cash Flow
            if present('free_cash_flow', metrics.free_cash_flow):
                max_present += MAX_POINTS['free_cash_flow']
                if metrics.free_cash_flow > 0:
                    score += 1.5

            score = ScoringEngine._coverage_scaled(score, max_present, sum(MAX_POINTS.values()))

        except Exception as e:
            # [Audit #23] A partial sum is not a score: the factor is unknown.
            logger.warning(f"value scoring failed ({type(e).__name__}: {e}); scored neutral")
            return ScoringEngine.MISSING_DATA_NEUTRAL

        return min(10.0, max(0.0, score))

    # ========================================================================================
    # [FIX #1] Cross-Sectional Z-Score Normalization
    # ========================================================================================

    @staticmethod
    def normalize_scores(analysis: StockAnalysis, stats: UniverseStats,
                         regime_weights: Dict[str, float]) -> StockAnalysis:
        """Attach a cross-sectional RANKING score to an analysed stock.

        The absolute composite (``overall_score``) is what the signal, the
        investment-committee policy, position sizing and the decision core
        were all derived from inside ``analyze_stock``. This function used to
        overwrite it — and the five sub-scores — after all of that had run,
        rebuilding the composite from the raw sub-scores. That silently
        discarded the bounded score adjustments (chase penalty, bearish
        consensus, catalyst, insider, EPS revisions) and left the published
        score and the published action derived from different numbers again,
        undoing W10: a STRONG_BUY at 8.0 could be republished at 7.2.

        It now leaves every gate input untouched and writes
        ``ranking_score``: the 65/35 absolute/z-score blend, plus the elite
        factor composite at its stated weight, plus the same bounded
        adjustment analyze_stock applied. Picks are ORDERED by it; they are
        never admitted or sized by it.
        """
        fm = analysis.fundamental_metrics
        ti = analysis.technical_indicators
        rm = analysis.risk_metrics
        sector = analysis.sector or analysis.gics_sector or 'Unknown'

        HEURISTIC_WT = TradingConfig.ABSOLUTE_SCORE_WEIGHT
        ZSCORE_WT = 1.0 - HEURISTIC_WT

        tech = analysis.technical_score
        fund = analysis.fundamental_score
        mom = analysis.momentum_score
        qual = analysis.quality_score
        val = analysis.value_score

        def blend(heuristic: float, components: List[float]) -> float:
            if not components:
                return heuristic
            z = float(np.mean(components))
            return HEURISTIC_WT * heuristic + ZSCORE_WT * stats.zscore_to_score(z)

        try:
            # --- Fundamental: growth, margins, returns, leverage. P/E and
            # PEG are valuation and live in the value block only (M1). ---
            fund_z: List[float] = []
            if fm.has_metric('revenue_growth') and len(stats.revenue_growth) >= 5:
                fund_z.append(stats._zscore(fm.revenue_growth, stats.revenue_growth))
            if fm.has_metric('earnings_growth') and len(stats.earnings_growth) >= 5:
                fund_z.append(stats._zscore(fm.earnings_growth, stats.earnings_growth))
            if fm.has_metric('profit_margin') and len(stats.profit_margins) >= 5:
                fund_z.append(stats._zscore(fm.profit_margin, stats.profit_margins))
            if fm.has_metric('roe') and len(stats.roe_values) >= 5:
                fund_z.append(stats._zscore(fm.roe, stats.roe_values))
            # Leverage is a quality input (audit #6): scored below, once.
            fund = blend(fund, fund_z)

            # --- Technical: volume and RSI distance from neutral. ADX is
            # scored in momentum only (M1). ---
            tech_z: List[float] = []
            if ti.volume_ratio and len(stats.volume_ratios) >= 5:
                tech_z.append(stats._zscore(ti.volume_ratio, stats.volume_ratios))
            if ti.rsi is not None and len(stats.rsi_values) >= 5:
                rsi_optimal_dist = -abs(ti.rsi - 50)
                rsi_pop_dists = [-abs(r - 50) for r in stats.rsi_values]
                tech_z.append(stats._zscore(rsi_optimal_dist, rsi_pop_dists))
            tech = blend(tech, tech_z)

            # --- Momentum: cross-sectional rank on the same horizons the
            # absolute score uses (12-2, 6m, 3m). ---
            mom_z: List[float] = []
            if len(stats.momentum_12_2) >= 5 and getattr(ti, 'momentum_12_2', 0.0):
                mom_z.append(stats._zscore(ti.momentum_12_2, stats.momentum_12_2))
            if len(stats.momentum_6m) >= 5:
                mom_z.append(stats._zscore(ti.momentum_6m, stats.momentum_6m))
            if len(stats.momentum_3m) >= 5:
                mom_z.append(stats._zscore(ti.momentum_3m, stats.momentum_3m))
            mom = blend(mom, mom_z)

            # --- Quality: risk-adjusted return and drawdown. "Beta close to
            # 1.0" contradicted the BAB factor and is dropped. ---
            qual_z: List[float] = []
            if getattr(rm, 'volatility', 0.0) and len(stats.sharpe_ratios) >= 5:
                qual_z.append(stats._zscore(rm.sharpe_ratio, stats.sharpe_ratios))
            if getattr(rm, 'volatility', 0.0) and len(stats.max_drawdowns) >= 5:
                qual_z.append(stats._zscore(rm.max_drawdown, stats.max_drawdowns))
            if fm.has_metric('debt_to_equity') and len(stats.debt_to_equity) >= 5:
                qual_z.append(stats._zscore(fm.debt_to_equity, stats.debt_to_equity, invert=True))
            qual = blend(qual, qual_z)

            # --- Value: P/E (sector-relative), P/B, P/S, FCF yield ---
            val_z: List[float] = []
            pe_pop = stats.sector_pe.get(sector) or []
            if len(pe_pop) < 5:
                pe_pop = stats.pe_ratios
            if fm.has_metric('pe_ratio') and fm.pe_ratio > 0 and len(pe_pop) >= 5:
                val_z.append(stats._zscore(fm.pe_ratio, pe_pop, invert=True))
            pb_pop = stats.sector_pb.get(sector) or []
            if len(pb_pop) < 5:
                pb_pop = stats.pb_ratios
            if fm.has_metric('pb_ratio') and fm.pb_ratio > 0 and len(pb_pop) >= 5:
                val_z.append(stats._zscore(fm.pb_ratio, pb_pop, invert=True))
            # Sales multiples differ by an order of magnitude across sectors
            # (software vs retail), so P/S ranks against sector peers when there
            # are enough of them, like P/E and P/B (audit #9).
            ps_pop = stats.sector_ps.get(sector) or []
            if len(ps_pop) < 5:
                ps_pop = stats.ps_ratios
            if fm.has_metric('ps_ratio') and fm.ps_ratio > 0 and len(ps_pop) >= 5:
                val_z.append(stats._zscore(fm.ps_ratio, ps_pop, invert=True))
            if (fm.has_metric('free_cash_flow') and fm.market_cap and fm.market_cap > 0
                    and len(stats.fcf_yields) >= 5):
                val_z.append(stats._zscore(fm.free_cash_flow / fm.market_cap, stats.fcf_yields))
            val = blend(val, val_z)

            try:
                pct = stats._percentile_rank(analysis.overall_score, stats.raw_overall_scores)
                analysis.universe_percentile = round(pct, 1) if pct is not None else None
            except Exception:
                analysis.universe_percentile = None

            if getattr(analysis, 'is_fund', False):
                # [ETF] Company factors and the elite composite (value,
                # profitability, QMJ) do not describe a fund.
                base = _fund_score({"technical": tech, "momentum": mom}, regime_weights)
                elite = 0.0
            else:
                base = (
                    tech * regime_weights.get("technical", 0.15) +
                    fund * regime_weights.get("fundamental", 0.25) +
                    mom * regime_weights.get("momentum", 0.20) +
                    qual * regime_weights.get("quality", 0.20) +
                    val * regime_weights.get("value", 0.20)
                )
                # [W13] The elite-factor composite gets its stated weight here.
                elite = float(getattr(analysis.elite_factors, 'elite_composite_score', 0.0) or 0.0)
            w_elite = TradingConfig.ELITE_FACTOR_WEIGHT if elite > 0 else 0.0
            adj = float(getattr(analysis, 'score_adjustment', 0.0) or 0.0)
            analysis.ranking_score = round(
                min(10.0, max(0.0, base * (1.0 - w_elite) + elite * w_elite + adj)), 2
            )

        except Exception as e:
            logger.debug(f"Z-score normalization failed for {analysis.ticker}: {e}")
            analysis.ranking_score = analysis.overall_score

        return analysis

    # ========================================================================================
    # [ELITE FIX] Conviction Scoring & Setup Classification
    # ========================================================================================
    
    @staticmethod
    def calculate_conviction(analysis: StockAnalysis, portfolio_snapshot: Optional[PortfolioSnapshot] = None) -> int:
        """Calculate conviction level (1-5) based on multiple factors - RECALIBRATED"""
        try:
            # Base conviction from overall score - FIXED THRESHOLDS
            # Scores typically range 4-8, so thresholds adjusted accordingly
            if analysis.overall_score >= 7.0:
                base_stars = 5
            elif analysis.overall_score >= 6.0:
                base_stars = 4
            elif analysis.overall_score >= 5.0:
                base_stars = 3
            elif analysis.overall_score >= 4.0:
                base_stars = 2
            else:
                base_stars = 1
            
            conviction = base_stars
            
            # === DOWNGRADES ===
            
            # Risk grade penalty (FIX: C grade is moderate risk, only penalize D/F)
            if analysis.risk_grade in ['D', 'F']:
                conviction -= 2
            # C grade: no penalty (moderate risk is normal for growth stocks)
            
            # Sector concentration/risk penalty
            if analysis.sector in ['Financial Services', 'Energy']:
                conviction -= 0  # Just flag, don't penalize
            
            # High volatility penalty
            if analysis.risk_metrics and analysis.risk_metrics.volatility > 0.40:
                conviction -= 1
            
            # === UPGRADES ===
            
            # Perfect technical setup (not overbought, good momentum)
            tech = analysis.technical_indicators
            if (40 <= tech.rsi <= 65 and tech.macd > tech.macd_signal and 
                tech.current_price > tech.sma_50 and tech.volume_ratio > 1.2):
                conviction += 1
            
            # Strong fundamentals
            fund = analysis.fundamental_metrics
            if (fund.revenue_growth and fund.revenue_growth > 0.10 and 
                fund.profit_margin and fund.profit_margin > 0.10 and 
                fund.pe_ratio and 0 < fund.pe_ratio < 25):
                conviction += 1
            
            # Exceptional risk/reward
            if analysis.risk_reward_ratio >= 5.0:
                conviction += 1
            elif analysis.risk_reward_ratio >= 3.5:
                pass  # Neutral
            
            # Quality score bonus
            if analysis.quality_score >= 7.5:
                conviction += 1
            
            # Risk grade bonus
            if analysis.risk_grade == 'A':
                conviction += 1
            
            # Clamp to 1-5 range
            return max(1, min(5, conviction))
            
        except Exception as e:
            logger.warning(f"Error calculating conviction: {e}")
            return 3

    
    @staticmethod
    def classify_setup_type(analysis: StockAnalysis) -> str:
        """Classify the trading setup type with relaxed conditions"""
        try:
            tech = analysis.technical_indicators
            fund = analysis.fundamental_metrics
            
            # BREAKOUT - Price above key MAs with volume
            if (tech.current_price > tech.sma_50 and tech.current_price > tech.sma_200 and
                tech.volume_ratio > 1.3 and tech.macd > tech.macd_signal):
                return "Breakout"
            
            # VALUE PLAY - Low valuation with quality
            if (fund.pe_ratio and 0 < fund.pe_ratio < 20 and 
                # PEG defaults to 0.0 when missing and a NEGATIVE PEG (falling
                # earnings) passed "< 1.5": P/E 15 with EPS -40% read "Value Play".
                (0 < fund.peg_ratio < 1.5 or not fund.data_available.get('peg_ratio')) and
                fund.profit_margin and fund.profit_margin > 0.08):
                return "Value Play"
            
            # GROWTH - Strong revenue/earnings growth
            if ((fund.revenue_growth and fund.revenue_growth > 0.15) or 
                (fund.earnings_growth and fund.earnings_growth > 0.12)):
                return "Growth"
            
            # DIVIDEND INCOME — yield must be EARNINGS-COVERED, not just high.
            # [M14 FIX] `payout_ratio is None or ...` let an unknown payout
            # ratio pass as if it were sustainable, which is exactly how a
            # yield trap gets classified as income.
            if (fund.dividend_yield and fund.dividend_yield > 0.02
                    and fund.payout_ratio and 0 < fund.payout_ratio < 0.80
                    and fund.free_cash_flow > 0):
                return "Dividend Income"
            
            # QUALITY COMPOUNDER - High ROE and margins
            if (fund.roe and fund.roe > 0.15 and 
                fund.profit_margin and fund.profit_margin > 0.12):
                return "Quality Compounder"
            
            # MOMENTUM - Positive trend
            if (tech.momentum_1m and tech.momentum_1m > 0.05 and 
                tech.current_price > tech.sma_20 and tech.rsi > 45):
                return "Momentum"
            
            # MEAN REVERSION - Oversold with quality
            if (tech.rsi < 40 and tech.bb_position < 0.35):
                return "Mean Reversion"
            
            # Fallback based on strongest characteristic
            if (fund.dividend_yield and fund.dividend_yield > 0.015
                    and fund.payout_ratio and 0 < fund.payout_ratio < 0.80):
                return "Dividend Income"
            elif fund.pe_ratio and 0 < fund.pe_ratio < 25:
                return "Value Play"
            else:
                return "Momentum"
            
        except Exception as e:
            logger.debug(f"Error classifying setup: {e}")
            return "Momentum"

    @staticmethod
    def classify_early_momentum(analysis: StockAnalysis) -> Tuple[int, List[str]]:
        """[PRE-MOMENTUM] Deterministic N-of-M early-momentum classifier.

        Fires when smart money is plausibly accumulating BEFORE the move becomes
        obvious. Uses only data already collected during scan — no extra API
        calls, no fabricated signals. Returns (score 0..6, list of fired signals).

        Confirmations (each worth 1 point):
          1. RSI in 50-65 — trending up but not yet overbought
          2. Price within 5% of 20DMA AND above 50DMA — controlled pullback in uptrend
          3. Volume thrust: volume_ratio ≥ 1.5 — institutional footprint
          4. MACD positive AND above signal — trend confirmation
          5. Bullish insider sentiment (90d net buying)
          6. 1-month momentum positive but < 15% — building, not extended

        A score ≥ 4 is the "early expansion phase" target. A score of 5-6 with
        no CHASE ALERT is the highest-probability asymmetric setup.
        """
        signals: List[str] = []
        try:
            tech = analysis.technical_indicators
            insider = (analysis.insider_sentiment or "").upper()

            # 1. RSI in trending-up band
            if 50.0 <= tech.rsi <= 65.0:
                signals.append(f"RSI {tech.rsi:.0f} in 50-65 trend band")

            # 2. Controlled pullback in uptrend
            if tech.sma_20 > 0 and tech.sma_50 > 0:
                dist_20 = abs(tech.current_price - tech.sma_20) / tech.sma_20
                if dist_20 <= 0.05 and tech.current_price > tech.sma_50:
                    signals.append(f"Within {dist_20*100:.1f}% of 20DMA, above 50DMA")

            # 3. Volume thrust
            if tech.volume_ratio >= 1.5:
                signals.append(f"Volume {tech.volume_ratio:.1f}× avg (institutional footprint)")

            # 4. MACD positive + above signal
            if tech.macd > 0 and tech.macd > tech.macd_signal:
                signals.append("MACD positive & above signal")

            # 5. Insider net buying
            if insider == "BULLISH":
                signals.append("Insiders net buyers (90d)")

            # 6. Momentum building but not extended
            mom_1m = float(tech.momentum_1m or 0)
            if 0.0 < mom_1m < 0.15:
                signals.append(f"1M momentum +{mom_1m*100:.1f}% (building)")

        except Exception as e:
            logger.debug(f"Early-momentum classifier error: {e}")

        return len(signals), signals


# ============================================================================
# [PERF] Module-level constant — avoids dict recreation per analyze_stock call
# ============================================================================

_REGIME_WEIGHTS = {
    "BULL": {
        "technical": 0.15, "fundamental": 0.20, "momentum": 0.35,
        "quality": 0.15, "value": 0.15
    },
    "BEAR": {
        "technical": 0.10, "fundamental": 0.20, "momentum": 0.15,
        "quality": 0.20, "value": 0.35
    },
    "VOLATILE": {
        "technical": 0.10, "fundamental": 0.15, "momentum": 0.15,
        "quality": 0.40, "value": 0.20
    },
    "RANGING": {
        "technical": 0.15, "fundamental": 0.25, "momentum": 0.20,
        "quality": 0.20, "value": 0.20
    },
    "NEUTRAL": {
        "technical": 0.15, "fundamental": 0.25, "momentum": 0.20,
        "quality": 0.20, "value": 0.20
    },
    # [W1 FIX] Trend undetermined. Lean on quality and fundamentals rather
    # than the trend-sensitive factors we currently cannot contextualise.
    "UNKNOWN": {
        "technical": 0.10, "fundamental": 0.25, "momentum": 0.15,
        "quality": 0.30, "value": 0.20
    },
}


# ============================================================================
# MAIN QUANTA TRADING SYSTEM
# ============================================================================

class QuantaEliteTradingSystem:
    """
    QUANTA Elite v5.0 PRO - Professional Grade Trading System
    Supports: US Stocks + India Stocks (NSE & BSE)
    """

    def __init__(self, capital: float = TradingConfig.DEFAULT_CAPITAL, market: str = "US"):
        """Initialize the trading system"""
        if not math.isfinite(float(capital)) or float(capital) <= 0:
            raise ValueError('Trading capital must be a finite positive number')
        capital = float(capital)
        self.capital = capital
        self.market = market.upper()  # "US" or "INDIA"
        self.currency_symbol = "$" if self.market == "US" else "₹"
        # Account currency → USD, so the USD-denominated caps, cost tiers and
        # minimum trade size mean the same thing for a rupee account.
        if self.market == "INDIA":
            try:
                fx = yf.Ticker("INR=X").history(period="5d", auto_adjust=True, actions=False)
                rate = float(fx['Close'].dropna().iloc[-1]) if not fx.empty else 0.0
                if 50.0 < rate < 200.0:
                    TradingConfig.INR_PER_USD = rate
            except Exception as e:
                logger.debug(f"INR=X fetch failed, using {TradingConfig.INR_PER_USD}: {e}")
            TradingConfig.ACCOUNT_TO_USD = 1.0 / TradingConfig.INR_PER_USD
        else:
            TradingConfig.ACCOUNT_TO_USD = 1.0
        self.config = TradingConfig
        self.analysis_timestamp = datetime.now()

        # Components
        self.market_data = MarketDataProvider()
        self.technical_engine = TechnicalAnalysisEngine()
        self.fundamental_engine = FundamentalAnalysisEngine()
        self.risk_engine = RiskAnalyticsEngine()
        self.scoring_engine = ScoringEngine()

        # **FIX #1: Fetch Benchmark Data for Real Beta/Alpha Calculation**
        self.benchmark_ticker = "^NSEI" if self.market == "INDIA" else "SPY"
        self.benchmark_hist = self._fetch_benchmark_data()

        # Elite Quantitative Engines
        self.elite_quant_engine = EliteQuantEngine()
        self.regime_detector = MarketRegimeDetector()
        self.timeframe_projector = TimeframeProjector()

        # Market Regime — [P0 FIX] pass market param so India uses ^INDIAVIX + NIFTY
        logger.info("Detecting current market regime...")
        self.market_regime = self.regime_detector.detect_regime(market=self.market)
        logger.info(
            f"Market Regime: {self.market_regime.regime_type} | VIX: {self.market_regime.vix_level:.1f} | Volatility: {self.market_regime.volatility_regime}")

        # **ELITE ENHANCEMENT #1: Dynamic Exposure Adjustment Based on Regime**
        # [P1 FIX] Use min() to never INCREASE exposure above the VIX-calibrated value
        # detect_regime() already sets recommended_exposure based on VIX (e.g. 0.5 for VIX>30).
        # These overrides should only reduce exposure further, never increase it.
        vix_exposure = self.market_regime.recommended_exposure
        if self.market_regime.regime_type == "RANGING":
            self.market_regime.recommended_exposure = min(vix_exposure, _T.REGIME.EXPOSURE_CAP_RANGING)
            logger.info(f"💡 ELITE ENHANCEMENT: Exposure capped at {self.market_regime.recommended_exposure*100:.0f}% for RANGING market")
        elif self.market_regime.regime_type == "VOLATILE":
            self.market_regime.recommended_exposure = min(vix_exposure, _T.REGIME.EXPOSURE_CAP_VOLATILE)
            logger.info(f"💡 ELITE ENHANCEMENT: Exposure capped at {self.market_regime.recommended_exposure*100:.0f}% for VOLATILE market")
        elif self.market_regime.regime_type == "BEAR":
            self.market_regime.recommended_exposure = min(vix_exposure, _T.REGIME.EXPOSURE_CAP_BEAR)
            logger.info(f"💡 ELITE ENHANCEMENT: Exposure capped at {self.market_regime.recommended_exposure*100:.0f}% for BEAR market")
        elif self.market_regime.regime_type == "UNKNOWN":
            # [W1 FIX] Trend undetermined — size down rather than defaulting to
            # a full-exposure "neutral" stance we have not earned.
            self.market_regime.recommended_exposure = min(vix_exposure, _T.REGIME.EXPOSURE_CAP_UNKNOWN)
            logger.info(
                f"💡 Trend regime UNKNOWN: exposure capped at "
                f"{self.market_regime.recommended_exposure*100:.0f}% until the "
                f"benchmark has 200 bars of history"
            )
        # BULL markets keep VIX-calibrated exposure (default)

        # [W2 FIX] A realized-vol proxy must never justify leverage above 1.0x.
        if getattr(self.market_regime, 'vix_is_proxy', False):
            self.market_regime.recommended_exposure = min(
                self.market_regime.recommended_exposure, 1.0
            )

        # Results storage
        self.scan_results: List[StockAnalysis] = []
        self.failed_tickers: List[str] = []
        # Cross-sectional distributions from the last scan (see [O7 FIX]).
        self._universe_stats: Optional[UniverseStats] = None
        # Deep-confirmation state (populated by _deep_confirm_final_picks).
        # Initialized here so exports always have a defined value even if the
        # reconciliation never ran.
        self._deep_confirm_enabled: bool = True
        self._deep_confirm_records: List[Dict[str, Any]] = []
        self._failed_tickers_lock = threading.Lock()
        self._diagnostics_lock = threading.Lock()
        self._provider_reads: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._candidate_rejections: Dict[str, Dict[str, Any]] = {}
        # [PRE-MOMENTUM] Track chase-alert tickers separately so they can be
        # surfaced as a "too-late-to-chase" watchlist alongside actionable picks.
        self.chase_alerts: List[Dict[str, Any]] = []
        self.gap_filled_alerts: List[Dict[str, Any]] = []   # [I6] informational, not chase alerts
        self._chase_alerts_lock = threading.Lock()
        self.current_drawdown: float = 0.0
        self.drawdown_multiplier: float = 1.0
        self.portfolio_risk_manager = PortfolioRiskManager()

        # Validate configuration
        if not TradingConfig.validate_config():
            raise ValueError("Invalid configuration parameters")

        logger.info(f"{'=' * 80}")
        logger.info(f"QUANTA Elite Trading System v6.0 PRO - INITIALIZED")
        logger.info(f"Market: {self.market} | Currency: {self.currency_symbol}")
        logger.info(f"Capital: {self.currency_symbol}{capital:,.2f} | Timestamp: {self.analysis_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Recommended Exposure: {self.market_regime.recommended_exposure * 100:.0f}% (Regime-Adjusted)")
        logger.info(f"{'=' * 80}")

    def _record_provider_read(self, ticker: str, name: str, status: str,
                              error_type: str = "") -> None:
        """Per-field observations; never count an enrichment gap as a rejection.

        Store only field/status/error class, never responses or exception text.
        A successful retry clears the active failure but retains its count.
        """
        lock = getattr(self, '_diagnostics_lock', None)
        if lock is None:  # lightweight legacy callers have no diagnostic store
            return
        with lock:
            fields = self._provider_reads.setdefault(ticker, {})
            old = fields.get(name, {})
            fields[name] = {"field": name, "required": name in {"history", "info"},
                            "status": status, "error_type": error_type,
                            "failures": int(old.get("failures", 0)) + (status == "failed")}

    def provider_diagnostics(self) -> List[Dict[str, Any]]:
        lock = getattr(self, '_diagnostics_lock', None)
        if lock is None:
            return []
        with lock:
            return [{"ticker": ticker, "reads": [dict(fields[k]) for k in sorted(fields)]}
                    for ticker, fields in sorted(self._provider_reads.items())]

    def _record_candidate_rejection(self, ticker: str, reason: str,
                                    score: float, detail: str) -> None:
        """Explain the first rejecting rule without incrementing outcome counts."""
        lock = getattr(self, '_diagnostics_lock', None)
        if lock is None:
            return
        with lock:
            self._candidate_rejections.setdefault(ticker, {
                "ticker": ticker, "reason": reason, "score": round(float(score), 4), "detail": detail,
            })

    def candidate_rejections(self) -> List[Dict[str, Any]]:
        """Stable per-name explanations for scans, exports and empty-book output."""
        lock = getattr(self, '_diagnostics_lock', None)
        if lock is None:
            return []
        with lock:
            # Research-mode survivors are not rejected even if a gate was observed.
            survived = {r.ticker for r in self.scan_results}
            return [dict(r) for ticker, r in sorted(self._candidate_rejections.items()) if ticker not in survived]

    def _record_failed_ticker(self, ticker: str) -> None:
        if not ticker:
            return
        with self._failed_tickers_lock:
            self.failed_tickers.append(ticker)

    @staticmethod
    def _classify_scan_error(exc: BaseException, stage: str = "analyze_stock") -> Dict[str, Any]:
        """Map a raw exception into a structured, UI-friendly error descriptor.

        Returns a dict with ``error_type`` (one canonical category), ``retryable``
        (bool), and ``suggested_fix`` (one actionable line). This is what lets the
        dashboard distinguish a genuine scan failure (data-provider/network) from a
        normal "no setups" result instead of collapsing everything into
        ``analysis_exception``.
        """
        name = type(exc).__name__
        msg = str(exc) or name
        low = msg.lower()

        # Provider rate limiting / throttling — its own category so the UI can
        # show a cooldown + safe-mode retry instead of a generic failure.
        if _is_rate_limit_error(exc):
            return {"error_type": "provider_rate_limited", "retryable": True,
                    "suggested_fix": "Data provider rate-limited the scan. Wait ~60s, then retry with "
                                     "Quick Scan or lower concurrency (Safe Mode)."}
        # Network / connectivity — recoverable.
        if any(k in low for k in ('connection', 'timed out', 'timeout', 'getaddrinfo',
                                  'network', 'ssl', 'temporarily unavailable',
                                  'max retries', 'connectionerror', 'readtimeout',
                                  'remote end closed', 'name resolution')):
            return {"error_type": "data_provider_error", "retryable": True,
                    "suggested_fix": "Network/data-provider unreachable. Check internet and provider status, then retry."}
        # Missing price history (empty frame / KeyError on Close etc.).
        if any(k in low for k in ('no price data', 'no data found', 'empty', 'no timezone',
                                  "'close'", 'delisted', 'possibly delisted')):
            return {"error_type": "missing_price_data", "retryable": False,
                    "suggested_fix": "No usable price history. Verify the ticker is valid and currently listed."}
        # Import / dependency problems — never retryable, needs env fix.
        if name in ('ImportError', 'ModuleNotFoundError'):
            return {"error_type": "import_error", "retryable": False,
                    "suggested_fix": "A backend dependency is missing. Reinstall requirements.txt in the active venv."}
        # JSON / serialization problems.
        if name in ('JSONDecodeError', 'TypeError') and ('serializ' in low or 'json' in low):
            return {"error_type": "serialization_error", "retryable": True,
                    "suggested_fix": "Provider returned an unexpected payload. Retry; if it persists, the API schema changed."}
        if name == 'TimeoutError' or 'future' in low and 'timeout' in low:
            return {"error_type": "timeout", "retryable": True,
                    "suggested_fix": "Per-ticker analysis exceeded the time budget. Retry or reduce universe size."}
        # Data shape errors from inside the pipeline.
        if name in ('ValueError', 'KeyError', 'IndexError'):
            return {"error_type": "analysis_exception", "retryable": False,
                    "suggested_fix": "Data was malformed mid-analysis. Inspect the backend traceback for this ticker."}
        return {"error_type": "analysis_exception", "retryable": False,
                "suggested_fix": "Unexpected backend error. See the stack trace in the rotating debug log."}

    def _record_scan_error(self, ticker: str, exc: BaseException,
                           stage: str = "analyze_stock") -> str:
        """Persist a structured per-ticker error so the UI can show the real cause.

        Also bumps a typed counter in ``_filter_stats`` (e.g. ``data_provider_error``)
        so the funnel histogram separates infrastructure failures from business
        filters. Returns the canonical ``error_type``.
        """
        info = self._classify_scan_error(exc, stage)
        import traceback as _tb
        record = {
            "ticker": ticker or "",
            "error_type": info["error_type"],
            "error_message": (str(exc) or type(exc).__name__)[:300],
            "exception_class": type(exc).__name__,
            "stage": stage,
            "retryable": bool(info["retryable"]),
            "suggested_fix": info["suggested_fix"],
            "traceback": _tb.format_exc()[-1200:],
        }
        if hasattr(self, '_error_records'):
            with self._filter_stats_lock:
                if len(self._error_records) < 500:
                    self._error_records.append(record)
        # Typed counter for the funnel (kept separate from generic counts).
        self._note_outcome(ticker, info["error_type"], category="failed")
        self._record_filter(info["error_type"], outcome=False)
        # Enrichment calls (info / news / insider / revisions) can be throttled
        # too, and raise straight past the history retry loop. Widen the
        # dispatch gap once per observed 429 instead of burning the universe.
        if info["error_type"] == "provider_rate_limited":
            if not getattr(exc, '_quanta_throttle_recorded', False):
                PROVIDER_STATE.note_rate_limit(_extract_retry_after(exc))
            PROVIDER_PACER.note_rate_limit()
        return info["error_type"]

    def get_error_summary(self) -> Dict[str, Any]:
        """Aggregate structured scan errors for the dashboard.

        Reports analyzed-vs-failed counts and the exception rate so the UI can
        decide between "no qualified setups" (healthy scan, zero picks) and
        "scan failed" (most tickers errored out).
        """
        records = list(getattr(self, '_error_records', []) or [])
        universe = int(getattr(self, '_last_universe_size', 0) or 0)
        # failed = tickers that errored or timed out (infrastructure), not
        # business-filtered names.
        failed = len(getattr(self, 'failed_tickers', []) or [])
        analyzed_success = max(0, universe - failed)
        by_type: Dict[str, int] = {}
        for r in records:
            by_type[r["error_type"]] = by_type.get(r["error_type"], 0) + 1
        exception_rate = (failed / universe) if universe else 0.0
        provider_rate_limited_count = int(by_type.get("provider_rate_limited", 0))
        provider_status = PROVIDER_STATE.snapshot()
        return {
            "records": records,
            "by_type": by_type,
            "universe_size": universe,
            "analyzed_success_count": analyzed_success,
            "analysis_failed_count": failed,
            "exception_rate": round(exception_rate, 4),
            # [PROVIDER-RESILIENCE] Provider-throttle telemetry so the UI can
            # show a RATE_LIMITED state + cooldown instead of a generic failure.
            "provider_rate_limited_count": provider_rate_limited_count,
            "provider_circuit_open": bool(getattr(self, "_provider_circuit_open", False)),
            "provider_degraded": bool(getattr(self, "_provider_degraded", False)),
            "provider_status": provider_status,
            "provider_reads": self.provider_diagnostics(),
        }

    def explain_empty_scan(self) -> str:
        """Explain an empty recommendation set from observed scan diagnostics."""
        universe = int(getattr(self, '_last_universe_size', 0) or 0)
        if universe == 0:
            return "No tickers entered the scan. Check the selected universe."

        lines = [
            "No new-money candidates passed this scan's rules; this is not a market-wide conclusion."
        ]
        # [A04] The same reconciled counts the stage table shows, in words; the
        # per-reason and per-stage detail lives in the table, not in key=value text.
        acc = self.scan_accounting()
        failed = acc["could_not_analyse"]["count"]
        sentence = (f"Of {universe} names, {acc['reached_book']} reached the book, "
                    f"{acc['rule_rejected']['count']} were screened out by per-name rules and "
                    f"{failed} could not be analysed")
        if acc["not_analysed"]:
            sentence += f"; {acc['not_analysed']} were not analysed"
        lines.append(sentence + ".")
        summary = self.get_error_summary()
        if failed or acc["not_analysed"] or summary['provider_degraded']:
            lines.append("Scan coverage was incomplete; the result may change when data recovers.")
        if getattr(self, 'drawdown_multiplier', 1.0) == 0:
            lines.append("New positions are halted by the account drawdown guardrail.")
        for row in self.candidate_rejections()[:5]:
            lines.append(f"{row['ticker']}: {row['detail']}")
        return "\n".join(lines)

    def _record_filter(self, reason: str, outcome: bool = True) -> None:
        """[FIX #15] Record reason a stock was filtered out for aggregated diagnostics."""
        if not hasattr(self, '_filter_stats'):
            return
        with self._filter_stats_lock:
            self._filter_stats[reason] = self._filter_stats.get(reason, 0) + 1
        if outcome:
            self._note_outcome(None, reason)

    def _note_outcome(self, ticker: Optional[str], reason: str,
                      category: Optional[str] = None) -> None:
        """[A04] Keep the first terminal outcome of a scanned name.

        ``ticker`` None means the name ``analyze_stock`` is running for on this
        thread. Warnings never become an outcome; data gaps are "could not be
        analysed", every other reason is a per-name rule.
        """
        outcomes = getattr(self, '_outcomes', None)
        if outcomes is None:
            return
        if ticker is None:
            ticker = getattr(getattr(self, '_outcome_tls', None), 'ticker', None)
        if not ticker or ticker not in getattr(self, '_outcome_universe', ()):
            return
        with self._filter_stats_lock:
            if reason in SCAN_WARNING_REASONS:
                self._outcome_warnings.setdefault(ticker, set()).add(reason)
                return
            if category is None:
                category = "failed" if reason in SCAN_DATA_GAP_REASONS else "rejected"
            outcomes.setdefault(ticker, (category, reason))

    @staticmethod
    def _progress_iter(futures, total: int):
        """Completed futures, with a console progress bar only when stderr is a
        working terminal.

        The bar used to wrap ``as_completed`` directly. In the web app, a server
        whose stderr could not be written (a console the process can no longer
        write to) raised from the bar's first draw, before the loop handled one
        result: every scan died "at 0 of N" once its workers finished, and the
        page showed only an internal-error reference. The page has its own
        progress; the bar is now optional and any failure to draw it is dropped.
        """
        it = as_completed(futures)
        stream = getattr(sys, "stderr", None)
        if not HAS_TQDM or stream is None:
            return it
        try:
            if not stream.isatty():
                return it
            bar = tqdm(total=total, desc="Scanning", unit="stock", ncols=80, leave=False,
                       dynamic_ncols=True, file=stream)
        except Exception:
            return it

        def _gen():
            nonlocal bar
            try:
                for fut in it:
                    yield fut
                    if bar is not None:
                        try:
                            bar.update(1)
                        except Exception:
                            bar = None
            finally:
                if bar is not None:
                    try:
                        bar.close()
                    except Exception:
                        pass
        return _gen()

    def _report_progress(self, stage: str, done: int, total: int) -> None:
        """[A13] Tell the caller which stage the scan is in, with counts.
        A failing callback never affects the scan."""
        cb = getattr(self, '_progress_cb', None)
        if cb is None:
            return
        try:
            cb(stage, int(done), int(total))
        except Exception as exc:  # noqa: BLE001 - progress is advisory
            logger.debug(f"Progress callback failed: {type(exc).__name__}: {exc}")

    def _note_survivor(self, ticker: str) -> None:
        """[A04] A returned analysis row is the name's terminal outcome."""
        outcomes = getattr(self, '_outcomes', None)
        if outcomes is None or not ticker:
            return
        with self._filter_stats_lock:
            prev = outcomes.get(ticker)
            if prev and prev[0] != "analyzed":
                self._outcome_warnings.setdefault(ticker, set()).add(prev[1])
            outcomes[ticker] = ("analyzed", "analyzed")

    def _book_stage(self, key: str, entered: int, passed: int, **threshold: Any) -> None:
        """[A04] Record one book gate: names entering it and names passing it."""
        counts = getattr(self, '_book_counts', None)
        if counts is None:
            return
        counts[key] = (int(entered), int(passed))
        if threshold:
            self._book_thresholds[key] = threshold

    def closest_to_qualifying(self, published: set, uncapped: bool = False) -> List[Dict[str, Any]]:
        """[A12] Book names nearest to passing, by distance to their first
        failing measurable gate (book order). ``needed`` is the applied
        threshold (or the configured one where the stage never ran); reward to
        risk also carries the entry and target prices that clear it, from the
        row's own levels and round-trip cost. Corporate-event and banned-grade
        blocks are rules with no distance and are left out.
        """
        from engine.levels import rr_triggers
        thr = dict(getattr(self, '_book_thresholds', {}) or {})
        event_held = {e.get('ticker') for e in (getattr(self, '_event_rejections', []) or [])}
        event_held |= {getattr(r, 'ticker', None) for r in (getattr(self, '_event_watch', []) or [])}
        buy = {SignalType.STRONG_BUY.value, SignalType.BUY.value, SignalType.ACCUMULATE.value}
        banned = set(_T.BOOK.BANNED_GRADES)
        min_score = float((thr.get('score') or {}).get('value') or _T.SCORE.ACCUMULATE)
        min_rr = float((thr.get('risk_reward') or {}).get('value') or _T.RR.SCANNER_MIN)
        min_conv = float((thr.get('conviction') or {}).get('value') or _T.BOOK.CONVICTION_FLOOR_SMALL)
        out: List[Dict[str, Any]] = []
        for r in self.scan_results or []:
            if r.ticker in published or r.ticker in event_held or r.is_invalid:
                continue
            score = float(r.overall_score or 0.0)
            item = {"ticker": r.ticker, "company": r.company}
            if r.signal not in buy or (r.final_action and r.final_action not in buy):
                if score >= _T.SCORE.ACCUMULATE:
                    continue        # held by a rule, not by a measurable distance
                item.update(gate="actionable", now=score, needed=float(_T.SCORE.ACCUMULATE), unit="score /10")
            elif r.risk_grade in banned:
                continue
            elif score < min_score:
                item.update(gate="score", now=score, needed=min_score, unit="score /10")
            elif float(r.risk_reward_ratio or 0.0) < min_rr:
                e, st, tg = float(r.entry_price or 0), float(r.stop_loss or 0), float(r.price_target or 0)
                if not (e > 0 and 0 < st < e and tg > 0):
                    continue
                cost = float(getattr(r, 'transaction_cost_bps', _T.BOOK.DEFAULT_ROUND_TRIP_BPS)) / 10_000
                trig = rr_triggers(e, st, tg, cost, min_rr)
                item.update(gate="risk_reward", now=float(r.risk_reward_ratio), needed=min_rr, unit="net R:R",
                            entry=e, stop=st, target=tg,
                            entry_needed=round(trig["entry_needed"], 2), target_needed=round(trig["target_needed"], 2))
            elif float(getattr(r, 'conviction', 0) or 0) < min_conv:
                item.update(gate="conviction", now=float(r.conviction), needed=min_conv, unit="conviction /5")
            else:
                e, tg = float(r.entry_price or 0), float(r.price_target or 0)
                cost = float(getattr(r, 'transaction_cost_bps', _T.BOOK.DEFAULT_ROUND_TRIP_BPS)) / 10_000
                need = _T.BOOK.COST_VIABILITY_MULTIPLE * cost
                exp = (tg - e) / e if e > 0 else 0.0
                if exp >= need or e <= 0:
                    continue        # passed every measurable gate (selection or Research held it)
                item.update(gate="cost", now=exp, needed=need, unit="expected return",
                            target_needed=round(e * (1 + need), 2))
            item["shortfall"] = round(max(0.0, (item["needed"] - item["now"]) / item["needed"]), 6) if item["needed"] else 1.0
            out.append(item)
        out.sort(key=lambda x: (x["shortfall"], x["ticker"]))
        return out if uncapped else out[:int(_T.BOOK.CLOSEST_ROWS)]

    def _note_selection_skip(self, ticker: str, reason: str, text: str, **extra: Any) -> None:
        """[P3] Why selection turned down a name that passed every rule (the latest try wins)."""
        skips = getattr(self, '_selection_skips', None)
        if skips is not None:
            skips[ticker] = {"reason": reason, "text": text, **extra}

    def _capital_for_one_share(self, analysis: "StockAnalysis") -> Optional[float]:
        """[P3] The smallest capital (rounded up to 100) at which one share fits the
        one-share policy (position cap and per-trade risk ceiling), or None."""
        price = float(getattr(analysis, 'entry_price', 0) or 0)
        if price <= 0:
            return None
        lo, hi = float(self.capital or 0), max(float(self.capital or 0), price) * 2
        while not TradingConfig.one_share_allowed(analysis, hi):
            hi *= 2
            if hi > price * 1_000:
                return None
        while hi - lo > 1.0:
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if TradingConfig.one_share_allowed(analysis, mid) else (mid, hi)
        need = math.ceil(hi / 100.0) * 100.0
        while not TradingConfig.one_share_allowed(analysis, need):
            need += 100.0
        return need

    def passed_not_funded(self, published: set) -> List[Dict[str, Any]]:
        """[P3] Names that passed every book rule but are not among the published
        rows, each with one plain reason from selection."""
        skips = dict(getattr(self, '_selection_skips', {}) or {})
        stop = getattr(self, '_selection_stop', None)
        out: List[Dict[str, Any]] = []
        for r in getattr(self, '_selection_pool', []) or []:
            if r.ticker in published:
                continue
            item = skips.get(r.ticker)
            if item is None and stop:
                why, n = stop
                item = {"reason": why, "text": (f"Not reached: the book already held its {n} positions"
                                                if why == "book_full" else "Not reached: all capital was allocated")}
            if item is None:
                continue
            out.append({"ticker": r.ticker, "company": r.company, "price": float(r.entry_price or 0), **item})
        return out

    def event_held_diagnostics(self) -> List[Dict[str, Any]]:
        """[I3] Diagnostic only, never funded: how each name the corporate-event
        gate held at Watch fares on the later book gates (risk grade, score,
        net R:R, conviction, cost) had its check cleared.

        The event gate runs first, so those stages read "not evaluated" for a
        held name. Here the pool those gates would have seen (the event-clear
        candidates plus the held names) sets the score and conviction floors
        by the book's own formulas. Rows are only read: nothing is sized,
        nothing enters the book, and selection limits and the Research check
        are not assessed.
        """
        from engine.levels import rr_triggers
        held = list(getattr(self, '_event_watch', []) or [])
        if not held:
            return []
        pool = list(getattr(self, '_event_kept', []) or []) + held
        banned = set(_T.BOOK.BANNED_GRADES)
        safe = [r for r in pool if r.risk_grade not in banned]
        if len(safe) <= _T.BOOK.SMALL_POOL_FOR_SCORE_FLOOR:
            min_score = float(TradingConfig.ACCUMULATE_SCORE)
        else:
            scores = sorted((r.overall_score for r in safe), reverse=True)
            min_score = max(float(TradingConfig.ACCUMULATE_SCORE), float(scores[len(scores) // 2]))
        min_rr = float(TradingConfig.RISK_REWARD_MIN)
        rr_pool = [r for r in safe if r.overall_score >= min_score and r.risk_reward_ratio >= min_rr]
        min_conv = (_T.BOOK.CONVICTION_FLOOR_LARGE if len(rr_pool) >= _T.BOOK.LARGE_POOL_FOR_CONVICTION
                    else _T.BOOK.CONVICTION_FLOOR_SMALL)
        mult = float(_T.BOOK.COST_VIABILITY_MULTIPLE)
        out: List[Dict[str, Any]] = []
        for r in held:
            fails: List[Dict[str, Any]] = []
            if r.risk_grade in banned:
                fails.append({"gate": "risk_grade", "now": r.risk_grade, "needed": sorted(banned),
                              "unit": "excluded grades"})
            score = float(r.overall_score or 0.0)
            if score < min_score:
                fails.append({"gate": "score", "now": score, "needed": min_score, "unit": "score /10"})
            rr = float(r.risk_reward_ratio or 0.0)
            if rr < min_rr:
                item = {"gate": "risk_reward", "now": rr, "needed": min_rr, "unit": "net R:R"}
                e, st, tg = float(r.entry_price or 0), float(r.stop_loss or 0), float(r.price_target or 0)
                if e > 0 and 0 < st < e and tg > 0:
                    cost = float(getattr(r, 'transaction_cost_bps', _T.BOOK.DEFAULT_ROUND_TRIP_BPS)) / 10_000
                    trig = rr_triggers(e, st, tg, cost, min_rr)
                    item.update(entry_needed=round(trig["entry_needed"], 2),
                                target_needed=round(trig["target_needed"], 2))
                fails.append(item)
            conv = getattr(r, 'conviction', 3)
            if conv < min_conv:
                # The book keeps and flags names below this floor when none clears it.
                fails.append({"gate": "conviction", "now": float(conv), "needed": float(min_conv),
                              "unit": "conviction /5", "kept_if_none_clear": True})
            e, tg = float(r.entry_price or 0), float(r.price_target or 0)
            exp = safe_divide(tg - e, e, 0.0)
            need = float(getattr(r, 'transaction_cost_bps', _T.BOOK.DEFAULT_ROUND_TRIP_BPS)) / 10_000 * mult
            if exp < need:
                fails.append({"gate": "cost", "now": exp, "needed": need, "unit": "expected return"})
            ev = dict(getattr(r, 'event_check', None) or {})
            out.append({
                "ticker": r.ticker, "company": r.company,
                "held_by": {k: ev.get(k) for k in ("code", "reason", "text", "fix")},
                "verdict": "also_fails" if fails else "event_only",
                "also_fails": fails,
                "not_assessed": ["selection", "final_review"],
                "diagnostic_only": True, "funded": False,
            })
        return out

    def scan_accounting(self) -> Dict[str, Any]:
        """[A04] Where every scanned name went, in counts that reconcile.

        ``universe = could_not_analyse + rule_rejected + reached_book +
        not_analysed``, and along ``book_stages`` each stage enters with the
        names the previous one passed, ending at ``funded``. Warnings are
        counted per name and overlap the outcomes. A stage the scan never
        reached has ``entered`` None; one nobody entered is not evaluated.
        """
        from collections import Counter
        universe = int(getattr(self, '_last_universe_size', 0) or 0)
        lock = getattr(self, '_filter_stats_lock', None) or threading.Lock()
        with lock:
            outcomes = dict(getattr(self, '_outcomes', {}) or {})
            warn_sets = [set(v) for v in (getattr(self, '_outcome_warnings', {}) or {}).values()]
        failed, rejected = Counter(), Counter()
        failed_tickers: Dict[str, List[str]] = {}
        rejected_tickers: Dict[str, List[str]] = {}
        reached = 0
        for ticker, (category, reason) in sorted(outcomes.items()):
            if category == "analyzed":
                reached += 1
            elif category == "failed":
                failed[reason] += 1
                failed_tickers.setdefault(reason, []).append(ticker)
            else:
                rejected[reason] += 1
                rejected_tickers.setdefault(reason, []).append(ticker)
        warnings = Counter(r for ws in warn_sets for r in ws)
        not_reached = sum(1 for t in (getattr(self, '_scan_not_reached', []) or []) if t not in outcomes)
        not_analysed = max(0, universe - sum(failed.values()) - sum(rejected.values()) - reached)

        counts = dict(getattr(self, '_book_counts', {}) or {})
        thresholds = dict(getattr(self, '_book_thresholds', {}) or {})
        configured = {
            "risk_grade": {"configured": list(_T.BOOK.BANNED_GRADES), "unit": "excluded grades"},
            "score": {"configured": _T.SCORE.ACCUMULATE, "unit": "score /10"},
            "risk_reward": {"configured": _T.RR.SCANNER_MIN, "unit": "net R:R"},
            "conviction": {"configured": _T.BOOK.CONVICTION_FLOOR_SMALL,
                           "configured_large_pool": _T.BOOK.CONVICTION_FLOOR_LARGE,
                           "large_pool": _T.BOOK.LARGE_POOL_FOR_CONVICTION, "unit": "conviction /5"},
            "cost": {"configured": _T.BOOK.COST_VIABILITY_MULTIPLE, "unit": "x round-trip cost"},
        }
        order = list(SCAN_BOOK_STAGES)
        mode = getattr(self, '_scan_mode_filter', None)
        if mode:
            counts.setdefault("scan_mode", mode)
            order.insert(0, "scan_mode")
        stages = []
        for key in order:
            c = counts.get(key)
            stage = {"key": key, "entered": c[0] if c else None, "passed": c[1] if c else None,
                     "evaluated": bool(c and c[0] > 0)}
            if key in configured:
                applied = thresholds.get(key, {})
                stage["threshold"] = dict(
                    configured[key],
                    value=applied.get("value") if stage["evaluated"] else None,
                    **{k: v for k, v in applied.items() if k != "value"})
            if key == "selection" and stage["evaluated"]:
                skips = dict(getattr(self, '_same_issuer_skips', {}) or {})
                stage["same_issuer"] = {"count": len(skips), "tickers": list(skips), "as": skips}
            if key == "event" and stage["evaluated"]:
                # [I2] Names out of the event stage are held at Watch (published,
                # never funded) or AVOID (pending deal or delisting).
                held = [r.ticker for r in (getattr(self, '_event_watch', []) or [])]
                stage["held"] = {"count": len(held), "tickers": held}
                out_n = max(0, int(stage["entered"]) - int(stage["passed"]) - len(held))
                cands = {r.ticker for r in (getattr(self, '_event_candidates', []) or [])}
                stage["avoided"] = {"count": out_n, "tickers": [x.get("ticker") for x in
                                                               (getattr(self, '_event_rejections', []) or [])
                                                               if x.get("ticker") in cands][:out_n]}
            stages.append(stage)
        gs = getattr(self, '_gate_stats', {}) or {}
        return {
            "version": 1,
            "universe": universe,
            "could_not_analyse": {"count": sum(failed.values()), "by_reason": dict(failed),
                                  "tickers": failed_tickers},
            # [Picks revamp] Every scanned name has one named outcome, not only a count.
            "rule_rejected": {"count": sum(rejected.values()), "by_reason": dict(rejected),
                              "tickers": rejected_tickers},
            "reached_book": reached,
            "not_analysed": not_analysed,
            "not_analysed_detail": {"not_reached": min(not_reached, not_analysed),
                                    "other": max(0, not_analysed - not_reached)},
            "warnings": dict(warnings),
            "book_stages": stages,
            "funded": int(gs.get("funded", 0) or 0),
        }

    def _record_zero_allocation(self, ticker: str, sub_reason: str) -> None:
        """[ITEM 7] Record WHY a scored stock received zero position size.

        ``sub_reason`` is one of the canonical categories:
        ``stop_distance``, ``risk_budget``, ``sector_cap``, ``position_cap``,
        ``min_trade_size``, ``whole_share_constraint``, ``missing_price_or_atr``,
        ``signal_not_buy``.  The reason and ticker are stashed so the scan
        summary can print the top rejected names with an explicit cause.
        """
        if not hasattr(self, '_zero_alloc_reasons'):
            return
        key = f"zero_allocation_due_to_{sub_reason}"
        with self._filter_stats_lock:
            self._zero_alloc_reasons[key] = self._zero_alloc_reasons.get(key, 0) + 1
            # Keep the per-ticker list bounded so memory stays flat on big scans.
            if len(self._zero_alloc_tickers) < 500:
                self._zero_alloc_tickers.append((ticker, key))
    
    def _drawdown_size_multiplier(self) -> float:
        """A zero throttle means stop; invalid account state must also fail closed."""
        try:
            value = float(getattr(self, 'drawdown_multiplier', 1.0))
        except (TypeError, ValueError):
            return 0.0
        return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0

    def _log_factor_collinearity(self, analyses: List[StockAnalysis]) -> None:
        """Log the sub-score correlation matrix (and VIF when available)."""
        if len(analyses) < 10:
            return
        try:
            frame = pd.DataFrame({
                'technical': [a.technical_score for a in analyses],
                'fundamental': [a.fundamental_score for a in analyses],
                'momentum': [a.momentum_score for a in analyses],
                'quality': [a.quality_score for a in analyses],
                'value': [a.value_score for a in analyses],
            })
            corr = frame.corr().abs()
            offenders = [
                (a, b, float(corr.loc[a, b]))
                for i, a in enumerate(corr.columns)
                for b in corr.columns[i + 1:]
                if np.isfinite(corr.loc[a, b]) and corr.loc[a, b] >= 0.80
            ]
            if offenders:
                logger.warning(
                    "⚠️  Sub-score collinearity above 0.80 — these are effectively "
                    "one factor carrying two weights: "
                    + ", ".join(f"{a}~{b} {v:.2f}" for a, b, v in offenders)
                )
            else:
                worst = max(
                    (
                        (a, b, float(corr.loc[a, b]))
                        for i, a in enumerate(corr.columns)
                        for b in corr.columns[i + 1:]
                        if np.isfinite(corr.loc[a, b])
                    ),
                    key=lambda t: t[2],
                    default=None,
                )
                if worst:
                    logger.info(
                        f"Sub-score collinearity OK (max |r| = {worst[2]:.2f} "
                        f"between {worst[0]} and {worst[1]})"
                    )

            if check_factor_vif is not None:
                try:
                    vif = check_factor_vif(frame)
                    logger.info(f"Factor VIF: {vif}")
                except Exception as e:
                    logger.debug(f"check_factor_vif failed: {e}")
        except Exception as e:
            logger.debug(f"Collinearity check skipped: {e}")

    def _log_filter_summary(self) -> None:
        """[FIX #15] Log aggregated filter rejection summary after scan."""
        if not hasattr(self, '_filter_stats') or not self._filter_stats:
            return
        exclusions = {key: value for key, value in self._filter_stats.items()
                      if key != 'earnings_calendar_missing'}
        # `_record_scan_error` files exception categories into the same dict, so
        # they were being added into a figure captioned "total rejections" — and
        # the scan header reports those very same names as "Errored (exception)".
        # One run showed 344 "rejections" against 326 rejected-by-filter names,
        # 15 of the difference being rate-limit errors counted in both places.
        # Keep infrastructure failures visible, but out of the business total.
        infra = {key: value for key, value in exclusions.items()
                 if key in _SCAN_ERROR_TYPES}
        business = {key: value for key, value in exclusions.items()
                    if key not in _SCAN_ERROR_TYPES}
        total = sum(business.values())
        logger.info(f"\n{'=' * 60}")
        # Deliberately "filter hits", not "rejections": one ticker can trip
        # several filters before it is dropped, so this sums events, not names.
        logger.info(f"FILTER REJECTION SUMMARY ({total} filter hits across the scan)")
        logger.info(f"{'=' * 60}")
        for reason, count in sorted(business.items(), key=lambda x: -x[1]):
            pct = count / total * 100 if total > 0 else 0
            logger.info(f"  {reason:40s} {count:4d} ({pct:5.1f}%)")
        if infra:
            logger.info(f"{'-' * 60}")
            logger.info("  Infrastructure failures (counted as errors, not filters):")
            for reason, count in sorted(infra.items(), key=lambda x: -x[1]):
                logger.info(f"  {reason:40s} {count:4d}")
        logger.info(f"{'=' * 60}\n")
        missing_calendar = self._filter_stats.get('earnings_calendar_missing', 0)
        if missing_calendar:
            logger.info(f"Earnings calendar unavailable: {missing_calendar} names sized down (not a rejection).")
        self._log_zero_allocation_summary()

    def _log_zero_allocation_summary(self) -> None:
        """[ITEM 7] Break down zero-allocation rejections by precise cause and
        print the top 20 rejected tickers each with its explicit reason."""
        reasons = getattr(self, '_zero_alloc_reasons', None)
        if not reasons:
            return
        z_total = sum(reasons.values())
        logger.info(f"{'=' * 60}")
        # "Sized to 0" was doing too much work. The tally mixes two different
        # stages — names the scanner never wanted to buy (signal_not_buy),
        # names it wanted but could not size (min_trade_size, risk_budget), and
        # names dropped later during portfolio construction (sector_cap,
        # position_cap) — so the header invited the reader to blame position
        # sizing for all of them. On one run 93 of 129 were simply HOLD.
        _not_wanted = reasons.get('zero_allocation_due_to_signal_not_buy', 0)
        logger.info(
            f"ZERO-ALLOCATION BREAKDOWN ({z_total} names carried no capital: "
            f"{_not_wanted} the model did not want, "
            f"{z_total - _not_wanted} it could not size)"
        )
        logger.info(f"{'=' * 60}")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            pct = count / z_total * 100 if z_total > 0 else 0
            logger.info(f"  {reason:40s} {count:4d} ({pct:5.1f}%)")
        tickers = getattr(self, '_zero_alloc_tickers', [])
        if tickers:
            logger.info(f"{'-' * 60}")
            logger.info("  Top rejected tickers (zero allocation):")
            for ticker, reason in tickers[:20]:
                logger.info(f"    {ticker:10s} → {reason}")
        logger.info(f"{'=' * 60}\n")

    # ── Position ledger vs signal log ────────────────────────────────────
    # [W8 FIX] `save_portfolio` used to write the current SCAN OUTPUT to
    # portfolio.csv, stamped with today's date and scan-time prices — and
    # `check_downgrades` and `_estimate_current_drawdown` both read that same
    # file. So the "portfolio" became whatever was last *suggested* rather than
    # what was actually bought: drawdown read ~0 forever, and downgrade
    # detection compared against the previous scan instead of real entries. The
    # state file corrupted itself on every run.
    #
    # The two concerns are now separate files with separate ownership:
    #   POSITIONS_PATH  actual fills. NEVER written by a scan.
    #   SIGNALS_PATH    scan output, rewritten freely every run.
    #   EQUITY_CURVE    one row per day, appended; the basis for drawdown.
    POSITIONS_PATH = "positions.csv"
    SIGNALS_PATH = "signals.csv"
    EQUITY_CURVE_PATH = "equity_curve.csv"

    def _record_equity_point(self, equity: float,
                             path: Optional[str] = None) -> None:
        """Append today's mark-to-market equity to the curve (one row per day)."""
        curve_path = Path(path or self.EQUITY_CURVE_PATH)
        today = datetime.now().strftime('%Y-%m-%d')
        try:
            rows: List[Dict[str, str]] = []
            if curve_path.exists():
                with open(curve_path, 'r', encoding='utf-8', newline='') as f:
                    rows = [r for r in csv.DictReader(f) if r.get('date') != today]
            rows.append({'date': today, 'equity': f"{float(equity):.2f}"})
            rows.sort(key=lambda r: r.get('date', ''))
            with open(curve_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['date', 'equity'])
                writer.writeheader()
                writer.writerows(rows)
        except Exception as e:
            logger.debug(f"Could not record equity point: {e}")

    def _estimate_current_drawdown(self, portfolio_path: Optional[str] = None) -> float:
        """Current drawdown from the equity high-water mark.

        [W7 FIX] This used to return ``max(0, -(current_value/entry_value - 1))``
        — unrealized P&L against cost basis, not decline from a peak, and with
        cash ignored entirely. A book up 25% and then back to +5% reported 0%
        drawdown after giving back a fifth of its gains, so the 5/10/15%
        exposure throttle (including the hard trading halt at 15%) fired on the
        wrong quantity and in practice almost never fired at all.

        Drawdown is now ``1 - equity / running_max`` over a persisted daily
        equity curve. With no curve yet there is no measurable drawdown, and we
        say so rather than inferring one from cost basis.
        """
        curve_path = Path(portfolio_path or self.EQUITY_CURVE_PATH)
        try:
            if not curve_path.exists():
                logger.debug(
                    f"No equity curve at {curve_path}; drawdown unmeasurable (0.0). "
                    f"Call _record_equity_point() after each session to build one."
                )
                return 0.0

            curve = pd.read_csv(curve_path)
            if curve.empty or 'equity' not in curve.columns:
                return 0.0

            equity = pd.to_numeric(curve['equity'], errors='coerce').dropna()
            equity = equity[equity > 0]
            if len(equity) < 2:
                return 0.0

            running_max = equity.cummax()
            drawdown = 1.0 - (equity / running_max)
            current = float(drawdown.iloc[-1])
            if not np.isfinite(current):
                return 0.0
            return float(min(max(current, 0.0), 1.0))
        except Exception as e:
            logger.debug(f"Drawdown estimation failed: {e}")
            return 0.0

    def mark_positions_to_market(self, positions_path: Optional[str] = None,
                                 cash: float = 0.0) -> Optional[float]:
        """Mark open positions to market, append to the equity curve, return equity.

        Reads the POSITIONS ledger (actual fills), never the signal log. This
        is the input the drawdown throttle needs, and it is the caller's job to
        run it — a scan must not mutate position state.
        """
        path = Path(positions_path or self.POSITIONS_PATH)
        if not path.exists():
            logger.debug(f"No position ledger at {path}; nothing to mark.")
            return None
        try:
            positions = pd.read_csv(path)
            if positions.empty:
                return None
            if not {'ticker', 'shares'}.issubset(positions.columns):
                logger.warning(
                    f"{path} needs at least 'ticker' and 'shares' columns; found "
                    f"{list(positions.columns)}"
                )
                return None

            equity = float(cash)
            for _, row in positions.iterrows():
                ticker = str(row.get('ticker', '')).strip()
                shares = _clean_float(row.get('shares'))
                if not ticker or shares <= 0:
                    continue
                price = 0.0
                try:
                    hist = yf.Ticker(ticker).history(
                        period="5d", auto_adjust=True, actions=False
                    )
                    if not hist.empty:
                        price = float(hist['Close'].iloc[-1])
                except Exception as e:
                    logger.debug(f"{ticker}: mark-to-market fetch failed - {e}")
                if price > 0:
                    equity += shares * price
                else:
                    # Fall back to the recorded cost so one bad quote does not
                    # manufacture a drawdown.
                    equity += shares * _clean_float(row.get('entry_price'))

            self._record_equity_point(equity)
            logger.info(f"📈 Marked {len(positions)} position(s) to market: ${equity:,.2f}")
            return equity
        except Exception as e:
            logger.warning(f"Mark-to-market failed: {e}")
            return None

    def update_trailing_stops(self, recommendations: List[StockAnalysis],
                              highs_since_entry: Optional[Dict[str, float]] = None) -> None:
        """Ratchet trailing stops upward for positions you already hold.

        [B2 FIX] This previously used ``high_52w`` as a stand-in for "highest
        price since entry" and then clamped the result with
        ``min(candidate, entry * 0.995)``. For any name more than ~1.75 ATR
        below its 52-week high — which is most of them — the clamp always won,
        so the published trailing stop landed 0.5% under the entry price: a
        guaranteed same-day stop-out that also silently RAISED the stop above
        the hard stop the analysis had derived. It shipped to the CSV and the
        markdown report.

        A trailing stop is only meaningful against a running high recorded
        since the position was opened, which is state this module does not
        own. So the caller must supply it via *highs_since_entry* (ticker ->
        highest price seen since entry). Tickers absent from that mapping keep
        the initial ATR stop set at analysis time and are left untouched —
        never ratcheted from an unrelated 52-week high.
        """
        highs = highs_since_entry or {}
        if not highs:
            logger.debug(
                "update_trailing_stops: no highs-since-entry supplied; keeping the "
                "initial ATR trailing stop (nothing to ratchet)."
            )
            return

        for rec in recommendations:
            try:
                if not rec or not rec.ticker:
                    continue
                running_high = float(highs.get(rec.ticker, 0) or 0)
                if running_high <= 0:
                    continue

                atr = float(getattr(rec.technical_indicators, 'atr', 0) or 0)
                if atr <= 0:
                    atr = max(float(rec.entry_price or 0) * 0.02, 0.01)

                candidate = RiskLimits.calculate_atr_trailing_stop(
                    running_high, atr, atr_multiplier=TradingConfig.TRAILING_ATR_MULTIPLIER
                )
                # A trailing stop only ever ratchets UP, and never above the
                # running high itself.
                floor_stop = max(float(rec.stop_loss or 0), float(rec.trailing_stop or 0))
                new_trail = max(floor_stop, candidate)
                if new_trail >= running_high:
                    logger.debug(
                        f"{rec.ticker}: trailing stop {new_trail:.2f} would sit at or above "
                        f"the running high {running_high:.2f}; leaving it unchanged."
                    )
                    continue
                rec.trailing_stop = round(new_trail, 2)
            except Exception as e:
                logger.debug(f"Trailing stop update failed for {getattr(rec, 'ticker', '?')}: {e}")

    def _fetch_benchmark_data(self) -> Optional[pd.Series]:
        """
        **FIX #1: Fetch benchmark returns for real beta/alpha calculation**
        Fetches SPY for US market, ^NSEI (Nifty 50) for India market.
        
        [CRITICAL FIX] Now uses HybridDataProvider (Polygon.io primary) to avoid rate limiting.
        """
        try:
            logger.info(f"📊 Fetching benchmark data ({self.benchmark_ticker})...")
            
            # Try HybridDataProvider first (uses Polygon.io)
            try:
                polygon_module = _import_optional("polygon_provider", "modules.polygon_provider")
                if polygon_module:
                    HybridDataProvider = polygon_module.HybridDataProvider
                    provider = HybridDataProvider()
                    hist = provider.get_historical(self.benchmark_ticker, '1y')
                    if not hist.empty:
                        benchmark_returns = hist['Close'].pct_change().dropna()
                        # HybridDataProvider falls back to yfinance when
                        # Polygon is not configured; name the real source.
                        src = "Polygon" if getattr(provider, 'use_polygon', False) else "yfinance"
                        logger.info(f"✓ Benchmark data loaded via {src}: {len(benchmark_returns)} trading days")
                        return benchmark_returns
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"HybridDataProvider benchmark fetch failed: {e}")
            
            # Fallback to yfinance with retry
            bm = yf.Ticker(self.benchmark_ticker)
            hist = bm.history(period="1y", auto_adjust=True, actions=False)
            if hist.empty:
                logger.warning(f"⚠️ Benchmark {self.benchmark_ticker} returned no data")
                return None
            benchmark_returns = hist['Close'].pct_change().dropna()
            logger.info(f"✓ Benchmark data loaded: {len(benchmark_returns)} trading days")
            return benchmark_returns
        except Exception as e:
            logger.error(f"❌ Failed to fetch benchmark {self.benchmark_ticker}: {e}")
            return None

    # ============================================================================
    # PORTFOLIO STATE PERSISTENCE (v6.0 Feature)
    # ============================================================================
    
    def load_portfolio(self, portfolio_path: str = "portfolio.csv") -> pd.DataFrame:
        """
        **PORTFOLIO TRACKING: Load existing holdings for downgrade detection**
        
        CSV Format:
            ticker,signal,score,entry_price,shares,entry_date
            AAPL,STRONG_BUY,8.5,175.00,10,2024-12-20
        """
        try:
            path = Path(portfolio_path)
            if path.exists():
                portfolio = pd.read_csv(path)
                logger.info(f"📂 Portfolio loaded: {len(portfolio)} positions from {portfolio_path}")
                return portfolio
            else:
                logger.info(f"📂 No existing portfolio found at {portfolio_path}")
                return pd.DataFrame(columns=['ticker', 'signal', 'score', 'entry_price', 'shares', 'entry_date'])
        except Exception as e:
            logger.error(f"❌ Failed to load portfolio: {e}")
            return pd.DataFrame(columns=['ticker', 'signal', 'score', 'entry_price', 'shares', 'entry_date'])
    
    def save_signals(self, recommendations: List[StockAnalysis],
                     signals_path: Optional[str] = None) -> bool:
        """Persist this scan's output to the SIGNAL log.

        [W8 FIX] This was ``save_portfolio``, and it wrote scan output to
        portfolio.csv — the same file the drawdown estimate and the downgrade
        check read as the record of what was actually held. Writing suggestions
        into the position ledger meant the ledger described the last scan, not
        the book, so drawdown read ~0 forever and downgrade detection compared
        today's scan against yesterday's scan. A scan can rewrite this file as
        often as it likes; it must never touch the position ledger.
        """
        path = Path(signals_path or self.SIGNALS_PATH)
        try:
            data = [{
                'ticker': rec.ticker,
                'signal': rec.signal,
                'final_action': getattr(rec, 'final_action', '') or rec.signal,
                'score': rec.overall_score,
                'suggested_entry': rec.entry_price,
                'stop_loss': rec.stop_loss,
                'price_target': rec.price_target,
                'suggested_shares': rec.shares,
                'suggested_allocation': rec.allocation,
                'conviction': getattr(rec, 'conviction', 0),
                'risk_grade': rec.risk_grade,
                'sector': rec.sector,
                'scan_date': datetime.now().strftime('%Y-%m-%d'),
            } for rec in recommendations]

            pd.DataFrame(data).to_csv(path, index=False)
            logger.info(f"💾 Signal log saved: {len(data)} idea(s) to {path}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save signal log: {e}")
            return False

    def save_portfolio(self, recommendations: List[StockAnalysis],
                       portfolio_path: str = "portfolio.csv") -> bool:
        """Deprecated. Writes the SIGNAL log, never the position ledger.

        Kept so existing callers do not break, but it no longer writes to a
        file that anything treats as a record of real holdings.
        """
        logger.warning(
            "save_portfolio() is deprecated: a scan must not write the position "
            "ledger. Writing to the signal log instead — record actual fills in "
            f"{self.POSITIONS_PATH} yourself."
        )
        return self.save_signals(recommendations)

    def load_positions(self, positions_path: Optional[str] = None) -> pd.DataFrame:
        """Load the POSITION ledger: actual fills, owned by the user.

        Expected columns: ticker, shares, entry_price, entry_date
        (optionally entry_score, to power the exit-deterioration triggers).
        """
        path = Path(positions_path or self.POSITIONS_PATH)
        columns = ['ticker', 'shares', 'entry_price', 'entry_date', 'entry_score']
        try:
            if not path.exists():
                logger.info(f"📂 No position ledger at {path} (nothing held, or not recorded yet)")
                return pd.DataFrame(columns=columns)
            positions = pd.read_csv(path)
            logger.info(f"📂 Position ledger loaded: {len(positions)} holding(s) from {path}")
            return positions
        except Exception as e:
            logger.error(f"❌ Failed to load position ledger: {e}")
            return pd.DataFrame(columns=columns)

    def check_downgrades(self, current_recommendations: List[StockAnalysis],
                         portfolio_path: Optional[str] = None) -> List[Dict]:
        """Check for rating downgrades on positions you actually hold.

        [W8 FIX] Reads the POSITION ledger (real fills). It used to read the
        file a scan had just overwritten with its own output, so it compared
        this scan against the previous scan rather than against real entries.
        """
        alerts = []

        try:
            portfolio = self.load_positions(portfolio_path)
            if portfolio.empty:
                return alerts
            if 'signal' not in portfolio.columns:
                portfolio = portfolio.assign(signal=SignalType.HOLD.value)
            if 'score' not in portfolio.columns:
                portfolio = portfolio.assign(
                    score=pd.to_numeric(
                        portfolio.get('entry_score', pd.Series(dtype=float)), errors='coerce'
                    ).fillna(0.0)
                )
            
            # Create lookup of current recommendations
            current_lookup = {rec.ticker: rec for rec in current_recommendations}
            
            for _, holding in portfolio.iterrows():
                ticker = holding['ticker']
                old_signal = holding['signal']
                old_score = holding['score']
                
                if ticker in current_lookup:
                    new_rec = current_lookup[ticker]
                    new_signal = new_rec.signal
                    new_score = new_rec.overall_score
                    
                    # [W9 FIX] The hand-written map keyed 'SELL', but
                    # SignalType emits 'REDUCE' — unmapped, so it fell back to
                    # 2, the same priority as HOLD, and a HOLD -> REDUCE
                    # deterioration raised no alert at all. Exits are where the
                    # money is lost, so a silent gap on the sell side is the
                    # expensive direction to be wrong in. Derive from the enum,
                    # which already carries a priority, and fail loudly on an
                    # unrecognised value rather than defaulting to neutral.
                    old_priority = _signal_priority(old_signal)
                    new_priority = _signal_priority(new_signal)
                    
                    if new_priority < old_priority or new_score < old_score - 1.0:
                        alert = {
                            'ticker': ticker,
                            'old_signal': old_signal,
                            'new_signal': new_signal,
                            'old_score': old_score,
                            'new_score': new_score,
                            'action': 'TRIM' if new_priority >= 2 else 'SELL'
                        }
                        alerts.append(alert)
                        logger.warning(f"📉 DOWNGRADE: {ticker} {old_signal}→{new_signal} (Score: {old_score:.1f}→{new_score:.1f})")
                else:
                    # Stock no longer in recommendations - potential concern
                    alerts.append({
                        'ticker': ticker,
                        'old_signal': old_signal,
                        'new_signal': 'NOT_FOUND',
                        'old_score': old_score,
                        'new_score': 0,
                        'action': 'REVIEW'
                    })
                    logger.warning(f"⚠️ {ticker}: No longer in scan results - REVIEW position")
            
            return alerts
            
        except Exception as e:
            logger.error(f"❌ Error checking downgrades: {e}")
            return alerts
    
    def display_downgrade_alerts(self, alerts: List[Dict]):
        """Display portfolio downgrade alerts prominently"""
        if not alerts:
            return
        
        if not RICH_AVAILABLE:
            print("\n" + "⚠️" * 20)
            print(" PORTFOLIO DOWNGRADE ALERTS ")
            print("⚠️" * 20)
            for alert in alerts:
                print(f"  {alert['ticker']}: {alert['old_signal']} → {alert['new_signal']} "
                      f"(Score: {alert['old_score']:.1f} → {alert.get('new_score', 0):.1f}) "
                      f"- Action: {alert['action']}")
            print()
            return
        
        console = Console()
        from rich.panel import Panel
        from rich import box
        
        print("\n" + "🚨" * 40)
        console.print("[bold red on white] 📉 PORTFOLIO DOWNGRADE ALERTS 📉 [/]", justify="center")
        print("🚨" * 40 + "\n")
        
        for alert in alerts:
            action_color = "red" if alert['action'] == 'SELL' else "yellow"
            console.print(Panel(
                f"[bold]{alert['ticker']}[/]\n"
                f"Signal: {alert['old_signal']} → [bold {action_color}]{alert['new_signal']}[/]\n"
                f"Score: {alert['old_score']:.1f} → {alert['new_score']:.1f}\n"
                f"[bold]Recommendation: {alert['action']}[/]",
                title=f"⚠️ {alert['action']}",
                border_style=action_color,
                box=box.HEAVY
            ))
        
        print("\n")

    def build_stock_universe(self, scan_type: str = "standard") -> List[str]:
        """Build comprehensive stock universe based on selected market"""
        
        if self.market == "INDIA":
            return self.build_indian_stock_universe(scan_type)
        else:
            return self.build_us_stock_universe(scan_type)

    # [India universes] The indexes each India scan type reads. Membership comes
    # from NSE's constituent files (Sensex from Wikipedia), live with a dated
    # snapshot fallback (engine.index_lists), never from a hand-typed list.
    INDIA_SCAN_INDEXES = {
        "india_quick": ("nifty50",),
        "india_standard": ("nifty50", "sensex", "niftybank"),
        "india_sectoral": ("nifty50", "niftybank", "niftyit"),
        "india_comprehensive": ("nifty500",),
        "india_full": ("nifty50", "sensex", "niftybank", "niftyit", "nifty500"),
        # Presets: a style screen over the Nifty 500, or a cap index.
        "india_growth": ("nifty500",), "india_value": ("nifty500",), "india_dividend": ("nifty500",),
        "india_momentum": ("nifty500",),
        "india_large": ("nifty100",), "india_mid": ("niftymidcap150",), "india_small": ("niftysmallcap250",),
    }

    def build_indian_stock_universe(self, scan_type: str = "india_standard") -> List[str]:
        """India universe: the current constituents of the scan type's indexes (NSE)."""
        keys = self.INDIA_SCAN_INDEXES.get(scan_type, self.INDIA_SCAN_INDEXES["india_standard"])
        universe: List[str] = []
        labels, sources, as_of, causes = [], set(), set(), []
        for key in keys:
            idx = self.market_data.get_index_constituents(key)
            names = list(idx.get("tickers") or [])
            universe.extend(t for t in names if t not in universe)
            labels.append(str(idx.get("label") or key))
            sources.add(str(idx.get("source") or "none"))
            if idx.get("as_of"):
                as_of.add(str(idx["as_of"]))
            if idx.get("cause"):
                causes.append(f"{idx.get('label')}: {idx['cause']}")
            logger.info(f"  ✓ {idx.get('label')}: {len(names)} names ({idx.get('source')})")
        source = "live" if sources == {"live"} else ("snapshot" if "none" not in sources else "none")
        self._universe_selection = {
            "scan_type": scan_type, "method": "index_list", "cause": None, "seconds": None,
            "budget_seconds": None, "candidates": len(universe), "selected": len(universe),
            "universe_total": len(universe), "pool_label": " + ".join(labels) + " names",
            "index_source": source, "index_as_of": max(as_of) if as_of else None,
            "index_cause": "; ".join(causes) or None,
        }
        if not universe:
            self._note_selection(cause="the index list could not be read and no snapshot was found")
        limit = int(_T.UNIVERSE.INDIA_SCAN_LIMITS.get(scan_type, 0) or 0)
        if limit and len(universe) > limit:
            # The most liquid names of the index, as the US style and cap scans do.
            universe = self._select_broad_universe(sorted(universe), limit)
            self._note_selection(selected=len(universe))
        logger.info(f"  ✓ Total Indian stock universe: {len(universe)} tickers ({scan_type.upper()})")
        return universe

    def build_us_stock_universe(self, scan_type: str = "standard") -> List[str]:
        """Build US stock universe across broad and focused scan types.

        Focused scan types are ranked dynamically from market data. They do not
        inject ticker-specific recommendation lists.
        """
        md = self.market_data
        logger.info("Building US stock universe from index constituents...")
        # [B-87] Every source is data: live constituent tables, else the dated
        # snapshot, else nothing (stated). No hand-typed list is added.
        sp500 = md.get_complete_sp500()
        nasdaq = md.get_nasdaq100_tickers()
        dow = md.get_dow30_tickers()
        sp400 = md.get_sp400_midcap_tickers()
        sp600 = md.get_sp600_smallcap_tickers()
        universe = set(sp500) | set(nasdaq) | set(dow) | set(sp400) | set(sp600)
        index_sources = {k: dict(v) for k, v in (getattr(md, "last_index_sources", {}) or {}).items()
                         if k in {"sp500", "nasdaq100", "dow30", "sp400", "sp600"}}
        for key, src in index_sources.items():
            logger.info(f"  ✓ {src.get('label') or key}: {src.get('source')}"
                        + (f" ({src.get('cause')})" if src.get("cause") else ""))
        universe_list = sorted(universe)
        logger.info(f"  ✓ Total universe: {len(universe_list)} unique stocks")

        # Interactive scan limits. yfinance throttles aggressively, so the
        # dashboard must avoid firing hundreds of quote/history requests at once.
        # `full` intentionally exceeds the assembled universe so a full-market
        # scan really does cover every assembled name.
        # The per-preset limits live in engine/thresholds.py (UNIVERSE), which the
        # scan form also reads, so the menu and the scan never disagree.
        scan_limits = {**_T.UNIVERSE.US_SCAN_LIMITS, "full": _T.UNIVERSE.FULL_SCAN_LIMIT}

        limit = scan_limits.get(scan_type, len(universe_list))
        # [A01] How this universe was chosen. The selectors below overwrite the
        # method when they rank or fall back; the page shows the record.
        self._universe_selection = {
            "scan_type": scan_type, "method": "listed_order", "cause": None,
            "seconds": None, "budget_seconds": None, "candidates": len(universe_list), "selected": None,
            "index_sources": index_sources,
        }
        if not universe_list and scan_type != "focused_etf":
            # Fail closed: no index could be read and no snapshot was found.
            self._note_selection(method="none", selected=0,
                                 cause="no index constituent list could be read and no snapshot was found")
            logger.warning("US universe is empty: no index list could be read and no snapshot was found")
            return []

        # `quick` ranks the large-cap index pool by liquidity, not an alphabetical slice.
        if scan_type == "pre_momentum":
            # [B-87] Semis / AI infrastructure from data: the index names whose
            # GICS sub-industry is in UNIVERSE.PRE_MOMENTUM_SUB_INDUSTRIES (live
            # tables only), screened for momentum, then capped.
            subs = md.index_sub_industries(["sp500", "nasdaq100", "sp400", "sp600"])
            wanted = set(_T.UNIVERSE.PRE_MOMENTUM_SUB_INDUSTRIES)
            pool = sorted(t for t in universe_list if subs.get(t) in wanted)
            self._note_selection(method="sub_industry", candidates=len(pool), universe_total=len(pool),
                                 pool_label="semiconductor and AI-infrastructure names")
            if not subs:
                self._note_selection(cause="the index lists carry no sub-industry data (live lists unavailable)")
                final_universe = []
            elif not pool:
                self._note_selection(cause="no index name is in the configured sub-industries")
                final_universe = []
            else:
                final_universe = self._select_broad_universe(pool, limit, screen_style="momentum")
        elif scan_type == "focused_etf":
            final_universe = self._select_focused_universe(universe_list, scan_type, limit)
        elif scan_type in {"focused_growth", "focused_value", "focused_momentum"}:
            # [Style scans] The most liquid S&P 500 names; analyze_stock then
            # rates only those that pass the style's fundamental screen.
            pool = sorted(set(sp500))
            self._universe_selection.update(method="index_list")
            self._note_selection(candidates=len(pool), universe_total=len(pool), pool_label="S&P 500 names",
                                 **dict(getattr(getattr(self, "market_data", None), "last_sp500_source", None) or {}))
            # Owner decision 2026-10-06: the momentum screen reads price history
            # only, so every S&P 500 name is screened before the cap; Growth and
            # Value need fundamentals and stay cap-then-screen.
            final_universe = self._select_broad_universe(
                pool, limit, screen_style="momentum" if scan_type == "focused_momentum" else None)
        elif scan_type in {"large_cap", "mid_cap", "small_cap", "focused_dividend"}:
            final_universe = self._select_index_universe(scan_type, sp500, dow, limit)
        elif scan_type == "quick":
            # The menu promises "Top 100 most-traded stocks". It used to be
            # the 30 Dow names followed by the head of a hand-curated list,
            # with nothing ranked by volume. Rank the large-cap pool (S&P 500 +
            # NASDAQ-100 + Dow) on average daily dollar volume instead; the
            # curated seed is only the offline fallback.
            large_cap_pool = sorted(
                (set(sp500) | set(nasdaq) | set(dow)) & universe
            )
            ranked = self.smart_prefilter(large_cap_pool, max_candidates=limit) if large_cap_pool else []
            if ranked:
                final_universe = ranked[:limit]
            elif large_cap_pool:
                # Not a curated seed: an even spread across the same index pool.
                self._universe_selection.update(method="even_spread", cause="liquidity ranking returned no names")
                final_universe = self._even_sample(large_cap_pool, limit)
            else:
                self._universe_selection.update(method="none", cause="the S&P 500, NASDAQ-100 and Dow lists could not be read")
                final_universe = []
        else:
            # [B1 FIX] Broad scans must NEVER take a lexicographic prefix of a
            # symbol-sorted list. `universe_list[:limit]` meant a "standard"
            # scan of 669 names only ever examined A-BSY, making NVDA/MSFT/
            # META/JPM/XOM structurally unreachable and the selected subset a
            # function of the alphabet. Rank on liquidity instead — the same
            # eligibility gates analyze_stock enforces anyway — so the
            # reduction costs no selection breadth.
            final_universe = self._select_broad_universe(universe_list, limit)
            if len(final_universe) == len(universe_list):
                # Whole universe: scan the index constituents first. The list
                # is symbol-sorted, so a scan cut short by provider throttling
                # used to lose everything after "R" — TSLA, UNH, V, WMT, XOM.
                core_pool = set(sp500) | set(nasdaq) | set(dow)
                final_universe = (
                    [t for t in final_universe if t in core_pool]
                    + [t for t in final_universe if t not in core_pool]
                )

        logger.info(f"  ✓ Scan type: {scan_type.upper()} ({len(final_universe)} stocks)")
        self._universe_selection["selected"] = len(final_universe)
        return final_universe

    # [Scan types] The index each cap-focus / dividend scan draws from.
    _INDEX_OF_SCAN = {"mid_cap": "sp400", "small_cap": "sp600", "focused_dividend": "aristocrats"}

    def _select_index_universe(self, scan_type: str, sp500: List[str], dow: List[str],
                               limit: int) -> List[str]:
        """[Scan types] A cap-focus or dividend scan's universe: its index's
        constituents, the ``limit`` most liquid when the index is larger.

        Large-Cap reads the S&P 500 and Dow, and analyze_stock keeps only names
        at or above its own market-cap floor (UNIVERSE.SCAN_MIN_MARKET_CAP).
        """
        if scan_type == "large_cap":
            pool, label = sorted(set(sp500) | set(dow)), "S&P 500 and Dow names"
            src = dict(getattr(getattr(self, "market_data", None), "last_sp500_source", None) or {})
        else:
            idx = self.market_data.get_index_constituents(self._INDEX_OF_SCAN[scan_type])
            pool, label = list(idx.get("tickers") or []), f"{idx.get('label')} names"
            src = {"index_source": idx.get("source"), "index_as_of": idx.get("as_of"),
                   "index_cause": idx.get("cause") or None}
            if scan_type == "focused_dividend":
                self._style_index_members = set(pool)
        self._universe_selection.update(method="index_list")
        self._note_selection(candidates=len(pool), universe_total=len(pool), pool_label=label, **src)
        if not pool:
            self._note_selection(cause="the index list could not be read and no snapshot was found")
            return []
        return self._select_broad_universe(pool, limit)

    def _us_floor(self, key: str) -> float:
        """A US liquidity floor for this scan: UNIVERSE.SCAN_FLOORS overrides the
        default for a scan that has its own (Small-Cap), else UNIVERSE.<key>."""
        own = (_T.UNIVERSE.SCAN_FLOORS or {}).get(getattr(self, 'current_scan_type', None) or '', {})
        return float(own.get(key, getattr(_T.UNIVERSE, key)))

    def _note_selection(self, **fields: Any) -> None:
        """[A01] Update the universe-selection record (created per build)."""
        rec = getattr(self, '_universe_selection', None)
        if not isinstance(rec, dict):
            rec = self._universe_selection = {"method": None, "cause": None, "seconds": None,
                                              "budget_seconds": None, "candidates": None, "selected": None}
        rec.update(fields)

    def _bounded_bulk_download(self, tickers: List[str], period: str, budget: float):
        """[A01] One bulk download in an owned child process, killed and reaped
        on timeout before the caller falls back. After a timeout no further
        bulk stage runs in this scan (``_bulk_timed_out``)."""
        from engine import bounded
        res = bounded.run(bounded.bulk_target(),
                          {"tickers": list(tickers), "period": period,
                           "threads": max(1, TradingConfig.YF_MAX_WORKERS)}, budget)
        if res.status == "timeout":
            self._bulk_timed_out = True
            # Slowness is not a 429: record it as slowness only.
            PROVIDER_STATE.note_slow()
        return res

    def _select_broad_universe(self, universe_list: List[str], limit: int,
                               screen_style: Optional[str] = None) -> List[str]:
        """Reduce a broad universe to `limit` names without alphabet bias.

        Ranks by average daily dollar volume, which is how a desk defines an
        investable universe: it pre-applies the liquidity floors
        ``analyze_stock`` would enforce regardless, so nothing is lost to the
        reduction beyond names that could not have been traded anyway.

        Falls back to a deterministic even spread across the sorted list when
        the bulk quote fetch is unavailable — still unbiased, unlike a prefix.
        """
        if limit <= 0 or len(universe_list) <= limit:
            return list(universe_list)

        ranked = self.smart_prefilter(universe_list, max_candidates=limit, screen_style=screen_style)
        if not ranked and (getattr(self, '_universe_selection', None) or {}).get("passed_screen") == 0:
            return []          # nothing passed the screen: an empty scan, not a fallback
        # smart_prefilter already falls back to _even_sample on failure, but
        # guard against an empty return so a broad scan is never silently void.
        if ranked:
            return ranked
        self._note_selection(method="even_spread", cause="liquidity ranking returned no names")
        logger.warning(
            "Liquidity ranking returned nothing; falling back to an even spread "
            "across the universe (never an alphabetical prefix)."
        )
        return self._even_sample(universe_list, limit)

    def _even_sample(self, tickers: List[str], sample_size: int) -> List[str]:
        """Return a deterministic spread across a sorted ticker list."""
        if sample_size <= 0 or len(tickers) <= sample_size:
            return list(tickers)
        step = (len(tickers) - 1) / max(sample_size - 1, 1)
        return [tickers[round(i * step)] for i in range(sample_size)]

    def _select_focused_universe(self, universe_list: List[str], scan_type: str, limit: int) -> List[str]:
        """The ETF Sleeve's universe: US-listed ETFs from the symbol directory,
        because ETFs are not part of the index constituents."""
        scan_type = str(scan_type or "").lower()

        if scan_type == "focused_etf":
            # [B-87] The US-listed ETFs from the Nasdaq Trader symbol directory,
            # leveraged / inverse products removed, then the most liquid.
            directory = self.market_data.get_us_etf_directory()
            names = directory.get("names") or {}
            etfs = sorted(t for t in directory.get("tickers") or []
                          if not is_leveraged_or_inverse(t, {"longName": names.get(t, "")}))
            register_directory_etfs(etfs)
            self._note_selection(method="etf_directory", candidates=len(etfs), universe_total=len(etfs),
                                 pool_label="US-listed ETFs", index_sources={"us_etfs": {
                                     k: directory.get(k) for k in ("label", "source", "as_of", "cause")}})
            if not etfs:
                self._note_selection(cause=f"the ETF directory could not be read ({directory.get('cause') or 'no ETFs listed'})")
                return []
            logger.info(f"Focused ETF universe from the symbol directory: {len(etfs)} ETFs")
            return self._select_broad_universe(etfs, limit)

        # Growth, Value and Dividend no longer come here: they read index
        # lists and apply a fundamental screen (build_stock_universe).
        self._note_selection(method="even_spread", cause=f"no focused list for {scan_type}")
        return self._even_sample(universe_list, limit)


    # [M14 FIX] Renames and delistings. `BLOCK -> SQ` was itself stale: Block
    # changed its ticker to XYZ in 2025, so the "correction" mapped a bad
    # symbol onto another bad symbol. Dead tickers consume scan slots and
    # inflate the failure count, so prefer live index constituents and keep
    # this map only for known renames.
    TICKER_CORRECTIONS = {
        "BLOCK": "XYZ",   # Block Inc: SQ -> XYZ (2025)
        "SQ": "XYZ",
        "FB": "META",     # Facebook -> Meta
        "TWTR": None,     # delisted (acquired 2022)
        "SPLK": None,     # delisted (acquired by Cisco, 2024)
        "PXD": None,      # delisted (acquired by Exxon, 2024)
        "ORCC": "OBDC",   # Owl Rock -> Blue Owl Capital Corp
        "CADILAHC": "ZYDUSLIFE",
        "MCDOWELL-N": "UNITDSPR",
        "ZOMATO": "ETERNAL",      # renamed 2025
        "TATAMOTORS": "TMPV",     # passenger-vehicle entity after the demerger
        "LTIM": "LTM",
        "INDIANBANK": "INDIANB",
        "REC": "RECLTD",
        "WBA": None,              # taken private (2025)
        "GOLD": "B",              # Barrick now trades as B; GOLD is Gold.com
        "BRK.B": "BRK-B",
        "HEI.A": "HEI-A",
    }

    def _sanitize_ticker(self, ticker: str) -> Optional[str]:
        """Clean and validate ticker symbol. Returns cleaned ticker or None."""
        if not ticker or not isinstance(ticker, str): return None
        
        # Remove $ if present (common CSV error)
        clean_ticker = ticker.replace("$", "").strip().upper()
        
        # Split off an exchange suffix first, so a correction also applies
        # to the ".NS" form the India universe actually carries.
        suffix = ""
        for sfx in (".NS", ".BO"):
            if clean_ticker.endswith(sfx):
                clean_ticker, suffix = clean_ticker[:-len(sfx)], sfx
                break

        # Apply corrections. A None mapping means "delisted" — drop it rather
        # than firing a request that is guaranteed to fail.
        if clean_ticker in self.TICKER_CORRECTIONS:
            mapped = self.TICKER_CORRECTIONS[clean_ticker]
            if mapped is None:
                logger.debug(f"{clean_ticker}: known delisted symbol, skipping")
                return None
            clean_ticker = mapped

        # Share-class suffix: BF.B -> BF-B (Yahoo's form). Only a single
        # trailing letter, so exchange suffixes are never touched.
        if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", clean_ticker):
            clean_ticker = clean_ticker.replace(".", "-")

        # '&' is legal on NSE (M&M, M&MFIN); isalnum() used to drop Mahindra &
        # Mahindra, a Nifty 50 / Sensex constituent, from every India scan.
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9&\-]*", clean_ticker):
            return None
        clean_ticker = clean_ticker + suffix
            
        # Blacklist check
        blacklist = {'VIX', 'INDIAVIX', 'NIFTY', 'SENSEX', 'LTI', 'MINDTREE'}
        if clean_ticker.split('.')[0] in blacklist:
            return None
            
        return clean_ticker

    def analyze_stock(self, ticker: str,
                      research_mode: bool = False) -> Optional[StockAnalysis]:
        """Comprehensive multi-dimensional stock analysis.

        ``research_mode`` is intended for deep-dive screens. The market scanner
        keeps its conservative default and rejects symbols that fail entry
        eligibility gates. A deep-dive still returns their analysis, while the
        final action and zero allocation preserve those risk guardrails.
        """
        _tls = getattr(self, '_outcome_tls', None)
        if _tls is not None:
            _tls.ticker = ticker
        # [PROVIDER-RESILIENCE] If the provider circuit tripped earlier in this
        # scan, stop firing network calls — return immediately so the remaining
        # universe doesn't generate hundreds of useless rate-limit errors. This
        # is a skip, NOT a failure, so it isn't counted against the scan.
        if getattr(self, '_provider_circuit_open', False):
            self._record_provider_read(ticker, "history", "deferred", "provider_circuit_open")
            self._record_provider_read(ticker, "info", "deferred", "provider_circuit_open")
            self._record_filter('provider_circuit_open')
            return None
        # Check circuit breaker before making API calls
        if hasattr(self, 'circuit_breaker') and not self.circuit_breaker.should_allow_request():
            self._record_provider_read(ticker, "history", "deferred", "circuit_breaker_open")
            self._record_provider_read(ticker, "info", "deferred", "circuit_breaker_open")
            self._record_filter('circuit_breaker_open')
            logger.debug(f"Circuit breaker OPEN - skipping {ticker}")
            return None
            
        try:
            # Retry with exponential backoff + jitter ONLY on provider rate-limit
            # errors. Respects a parsed Retry-After when the provider supplies one
            # and feeds the shared PROVIDER_STATE tracker so the dashboard can
            # surface an honest RATE_LIMITED state with a cooldown countdown.
            max_retries = max(1, TradingConfig.PROVIDER_RETRY_COUNT)
            stock = None
            hist = None
            for attempt in range(max_retries):
                try:
                    stock = _ScannerTicker(yf.Ticker(ticker), self, ticker)
                    # Global dispatch pacing for every ticker, including those
                    # served from the bulk prefetch: a scored name still makes
                    # the info / news / calendar / insider / revision requests.
                    PROVIDER_PACER.wait()
                    hist = self._bulk_history_for(ticker)
                    if hist is None:
                        hist = stock.history(
                            period=_T.RISK_GRADE.FETCH_PERIOD,
                            auto_adjust=True,
                            actions=False,
                        )
                        # Only a real request that came back clean is evidence
                        # the provider is healthy; a prefetch hit is not.
                        PROVIDER_STATE.note_success()
                        PROVIDER_PACER.note_success()
                    hist = DataValidator.completed_daily_history(
                        hist, "IN" if self.market == "INDIA" else "US"
                    )
                    break  # Success, exit retry loop
                except Exception as e:
                    if _is_rate_limit_error(e):
                        retry_after = _extract_retry_after(e)
                        PROVIDER_STATE.note_rate_limit(retry_after)
                        setattr(e, '_quanta_throttle_recorded', True)
                        if attempt < max_retries - 1:
                            # One pacer notification per observed 429; the final
                            # attempt's exception is reported by _record_scan_error.
                            PROVIDER_PACER.note_rate_limit()
                            base = TradingConfig.PROVIDER_BACKOFF_SECONDS
                            wait_time = min(
                                TradingConfig.BACKOFF_MAX_SECONDS,
                                (retry_after if retry_after else base * (2 ** attempt))
                                + random.uniform(0, TradingConfig.BACKOFF_JITTER_SECONDS)
                            )
                            logger.warning(
                                f"{ticker}: provider rate-limited — backing off {wait_time:.1f}s "
                                f"(attempt {attempt + 1}/{max_retries}"
                                + (f", retry-after={retry_after:.0f}s" if retry_after else "") + ")"
                            )
                            time.sleep(wait_time)
                            continue
                    raise  # Re-raise if not rate limit or max retries reached

            # [F3] Two years for the risk grade; the indicators keep one year.
            hist_full = hist
            hist = indicator_window(hist_full)
            if hist.empty or len(hist) < TradingConfig.MIN_HISTORY_DAYS:
                logger.debug(f"{ticker}: Insufficient data ({len(hist)} days)")
                self._record_filter('insufficient_data')
                return None

            # **[P1 FIX] Validate & clean historical data before analysis**
            validation = DataValidator.validate_history(
                hist, ticker, max_age_days=TradingConfig.MAX_LIVE_DATA_AGE_DAYS
            )
            if not validation.is_valid:
                # [I8] The page names this ticker under "could not be analysed".
                logger.warning(f"{ticker}: data validation failed - {validation.errors}")
                self._record_filter('data_validation_failed')
                return None
            for w in validation.warnings:
                logger.debug(w)
            hist = DataValidator.clean_history(hist)

            # **ENHANCEMENT #3: Fast Info Pre-Screening (10x faster than .info)**
            # Use fast_info for initial price/market cap checks before slow .info call
            is_indian_stock = ticker.endswith('.NS') or ticker.endswith('.BO')

            # **HARD EXCLUSION: leveraged / inverse / volatility ETPs**
            # Daily-reset decay and path dependency make these unsuitable for the
            # multi-day swing horizon — exclude outright rather than score-penalize.
            if is_leveraged_or_inverse(ticker):
                logger.debug(f"{ticker}: Excluded - leveraged/inverse/volatility ETP")
                self._record_filter('leveraged_inverse_excluded')
                return None

            # Levels, indicators and the price all come from the same completed
            # session. fast_info.last_price is an intraday print, and mixing it
            # with completed-bar stops/targets distorted the published R:R.
            current_price = float(hist['Close'].iloc[-1])
            fast_market_cap = None
            try:
                fast_market_cap = stock.fast_market_cap()
                
                # Quick market cap filter using fast_info (funds have no market cap)
                if fast_market_cap and fast_market_cap > 0 and not is_etf(ticker):
                    if is_indian_stock:
                        if fast_market_cap < india_min_market_cap_for(getattr(self, 'current_scan_type', None)):
                            logger.debug(f"{ticker}: Fast filter - market cap ₹{fast_market_cap/1e7:.0f}Cr below min")
                            self._record_filter('market_cap_too_low')
                            return None
                    else:
                        if fast_market_cap < min_market_cap_for(getattr(self, 'current_scan_type', None)):
                            logger.debug(f"{ticker}: Fast filter - market cap ${fast_market_cap/1e9:.1f}B below min")
                            self._record_filter('market_cap_too_low')
                            return None
            except Exception:
                pass  # market cap is re-checked from .info below

            # **ENHANCEMENT #1: Earnings Risk Filter (Hedge Fund Risk Management)**
            # Professional desks avoid opening new positions right before earnings
            # [M10 FIX] The blackout used to DROP any name with earnings in
            # 0-14 days outright, so ~15% of the universe was unavailable at
            # any moment and *which* 15% depended on the calendar date the scan
            # happened to run — results were not comparable week to week. And
            # the date came from `stock.calendar`, which is frequently empty,
            # with a bare `except` that silently disabled the filter: it failed
            # OPEN precisely on the names where it mattered most.
            #
            # Now: size down through the blackout rather than excluding, and
            # treat a missing calendar as a data gap that also reduces size —
            # never as an all-clear.
            days_to_earnings = -1
            earnings_size_multiplier = 1.0
            earnings_note = ""
            calendar_known = False
            # [ETF] A fund reports no earnings, so it takes no earnings size cut.
            fund_hint = is_etf(ticker)
            try:
                next_earnings = None if fund_hint else _extract_next_earnings_date(stock.calendar)
                if next_earnings is not None:
                    calendar_known = True
                    days_to_earnings = (next_earnings.date() - datetime.now().date()).days
                    earnings_size_multiplier = earnings_size_multiplier_for(days_to_earnings)
                    if earnings_size_multiplier < 1.0:
                        earnings_note = f"earnings in {days_to_earnings}d"
                    if earnings_note:
                        logger.info(
                            f"⚠️ {ticker}: {earnings_note} — position scaled to "
                            f"{earnings_size_multiplier:.0%}"
                        )
            except Exception as e:
                logger.debug(f"{ticker}: Could not verify earnings date - {e}")
            if not calendar_known and not fund_hint:
                earnings_size_multiplier = TradingConfig.EARNINGS_UNKNOWN_SIZE_MULT
                earnings_note = "earnings date unknown"
                self._record_filter('earnings_calendar_missing')
                logger.debug(
                    f"{ticker}: no earnings calendar — treating as a data gap, "
                    f"position scaled to {earnings_size_multiplier:.0%}"
                )

            # ONLY after fast checks pass, fetch full info (slower API call)
            info = stock.info
            if not info or not isinstance(info, dict):
                logger.debug(f"{ticker}: stock.info returned None or invalid data")
                self._record_filter('info_unavailable')
                return None

            is_fund = fund_hint or str(info.get('quoteType') or '').upper() == 'ETF'
            if is_fund and not fund_hint and not calendar_known:
                earnings_size_multiplier, earnings_note = 1.0, ""

            # Secondary leveraged/inverse catch using the fund's long name
            if is_leveraged_or_inverse(ticker, info):
                logger.debug(f"{ticker}: Excluded - leveraged/inverse/volatility ETP (name match)")
                self._record_filter('leveraged_inverse_excluded')
                return None

            # Pre-screening filters

            # **QUICK WIN #4: INR-specific filters for Indian market**
            # (is_indian_stock already computed above)
            
            if is_indian_stock:
                # India-specific filters (prices in INR), from UNIVERSE.INDIA_*.
                if not (_T.UNIVERSE.INDIA_MIN_PRICE <= current_price <= _T.UNIVERSE.INDIA_MAX_PRICE):
                    logger.debug(f"{ticker}: Price ₹{current_price:.2f} outside Indian range")
                    self._record_filter('price_outside_range')
                    return None
            else:
                # US market filters (prices in USD)
                if not (self._us_floor("MIN_PRICE") <= current_price <= TradingConfig.MAX_PRICE):
                    logger.debug(f"{ticker}: Price ${current_price:.2f} outside range")
                    self._record_filter('price_outside_range')
                    return None

            # **Volume filter - Market-specific**
            avg_volume = float(hist['Volume'].tail(20).mean())
            
            if is_indian_stock:
                # India: Lower volume threshold (less liquid market)
                if avg_volume < _T.UNIVERSE.INDIA_MIN_AVG_VOLUME:
                    logger.debug(f"{ticker}: Low volume {avg_volume:,.0f} (India threshold)")
                    self._record_filter('low_volume')
                    return None
            else:
                # US: Standard volume threshold
                if avg_volume < self._us_floor("MIN_AVG_VOLUME"):
                    logger.debug(f"{ticker}: Low volume {avg_volume:,.0f}")
                    self._record_filter('low_volume')
                    return None

            # **Market cap filter - CRITICAL FIX #3: Market-specific**
            # ETFs bypass market cap check (they're diversified funds, not individual stocks)
            ticker_is_etf = is_fund
            # Yahoo's .info sometimes omits marketCap for a listed company (on
            # 2026-10-06 RELIANCE.NS and TCS.NS) while fast_info still has it.
            # Use that measurement rather than drop the name; every later reader
            # of info sees the same figure.
            if not info.get('marketCap') and fast_market_cap and fast_market_cap > 0 and not ticker_is_etf:
                info = {**info, 'marketCap': float(fast_market_cap)}
            market_cap = info.get('marketCap', 0)
            
            if not ticker_is_etf:
                if market_cap == 0 or market_cap is None:
                    logger.debug(f"{ticker}: No market cap data")
                    self._record_filter('no_market_cap')
                    return None

                if is_indian_stock:
                    # India: Market cap already in INR from yfinance
                    india_floor = india_min_market_cap_for(getattr(self, 'current_scan_type', None))
                    if market_cap < india_floor:
                        logger.debug(f"{ticker}: Market cap ₹{market_cap / 1e7:.0f} crore below "
                                     f"₹{india_floor / 1e7:,.0f} crore")
                        self._record_filter('market_cap_too_low')
                        return None
                else:
                    # US: Market cap in USD
                    if market_cap < min_market_cap_for(getattr(self, 'current_scan_type', None)):
                        logger.debug(f"{ticker}: Market cap ${market_cap / 1e9:.1f}B below minimum")
                        self._record_filter('market_cap_too_low')
                        return None

            # **Dollar/Rupee volume filter - Market-specific**
            currency_volume = avg_volume * current_price
            
            if is_indian_stock:
                # India: Rupee volume threshold (adjusted for INR)
                if currency_volume < _T.UNIVERSE.INDIA_MIN_DAILY_VALUE:
                    logger.debug(f"{ticker}: Low ₹ volume {currency_volume / 1e7:.2f} crore")
                    self._record_filter('low_dollar_volume')
                    return None
            else:
                # US: Dollar volume threshold
                if currency_volume < self._us_floor("MIN_DAILY_DOLLAR_VOLUME"):
                    logger.debug(f"{ticker}: Low $ volume ${currency_volume / 1e6:.1f}M")
                    self._record_filter('low_dollar_volume')
                    return None

            # [Scan filters] The user's optional narrowing (sector, market cap, price,
            # traded value). It never replaces a built-in rule above.
            _uf_reason = None if research_mode else scan_filters.miss(
                getattr(self, 'user_filters', None) or {}, sector=info.get('sector'),
                market_cap=market_cap, price=current_price, daily_value=currency_volume,
                is_fund=ticker_is_etf)
            if _uf_reason:
                logger.debug(f"{ticker}: outside the scan filters ({_uf_reason})")
                self._record_filter(_uf_reason)
                return None

            # Market cap filter (Removed redundant check that conflicted with India logic)
            # market_cap = info.get('marketCap', 0) or 0
            # if market_cap > 0 and market_cap < TradingConfig.MIN_MARKET_CAP:
            #     return None

            # Calculate all indicators
            tech_indicators = self.technical_engine.calculate_all_indicators(hist)
            if tech_indicators is None:
                logger.debug(f"{ticker}: Failed to calculate technical indicators")
                self._record_filter('tech_indicators_failed')
                return None

            # Fundamental analysis
            fundamental_metrics = self.fundamental_engine.analyze_fundamentals(info)

            # [Style scans] Growth / Value / Dividend rate only names that pass
            # the style's fundamental screen (engine.style_screen).
            style_result = None
            _style = STYLE_OF_SCAN.get(str(getattr(self, 'current_scan_type', '') or ''))
            if _style and not is_fund and not research_mode:
                style_result = style_screen(
                    _style, fundamental_metrics, str(info.get('sector') or ''),
                    on_list=(ticker in (getattr(self, '_style_index_members', None) or set()))
                    if _style == "dividend" and not is_indian_stock else None,
                    closes=hist['Close'].tolist() if _style == "momentum" else None)
                if not style_result["passed"]:
                    logger.debug(f"{ticker}: {style_result['label']} not passed - {style_result['reason']}")
                    self._record_filter('style_screen_failed')
                    return None

            # **v6.0 FIX #1.2: Align stock returns with benchmark using safe alignment**
            stock_returns = hist['Close'].pct_change().dropna()
            aligned_stock = None
            aligned_market = None
            
            if self.benchmark_hist is not None and len(stock_returns) > 0:
                # Use v6.0 safe alignment with timezone/date normalization
                aligned_stock, aligned_market = _align_returns_safely(
                    stock_returns, self.benchmark_hist, min_points=30
                )

            # Risk analytics (now with real beta/alpha if benchmark available)
            # One risk-free assumption per market for Sharpe, Sortino and
            # alpha: Indian T-bills yield ~6.5%, not the US 4%.
            _rf = _T.UNIVERSE.RISK_FREE_RATE_INDIA if is_indian_stock else _T.UNIVERSE.RISK_FREE_RATE_US
            risk_metrics = self.risk_engine.calculate_risk_metrics(hist, risk_free_rate=_rf)
            
            # **v6.0 FIX #1.2: Calculate real beta/alpha using aligned returns with fallback**
            if aligned_stock is not None and aligned_market is not None:
                real_beta, real_alpha = RiskAnalyticsEngine.calculate_beta_alpha(
                    aligned_stock, aligned_market, risk_free_rate=_rf
                )
                risk_metrics.beta = real_beta
                risk_metrics.alpha = real_alpha
            else:
                # Fallback to defaults if alignment failed
                risk_metrics.beta = 1.0
                risk_metrics.alpha = 0.0
                logger.debug(f"{ticker}: Using default beta/alpha (alignment failed)")

            # Calculate scores
            tech_score = self.scoring_engine.calculate_technical_score(tech_indicators)
            fundamental_score = self.scoring_engine.calculate_fundamental_score(fundamental_metrics)
            momentum_score = self.scoring_engine.calculate_momentum_score(tech_indicators)
            quality_score = self.scoring_engine.calculate_quality_score(fundamental_metrics, risk_metrics)
            value_score = self.scoring_engine.calculate_value_score(fundamental_metrics)

            # [BATCH 3 #5] Regime-dependent factor weights
            # Different factors work better in different market conditions
            regime_type = self.market_regime.regime_type
            
            # [PERF] Weights defined as module constant _REGIME_WEIGHTS (avoid dict creation per stock)
            weights = _REGIME_WEIGHTS.get(regime_type, _REGIME_WEIGHTS["NEUTRAL"])
            
            
            # Calculate weighted overall score. The elite-factor composite is
            # blended in later (see [W13 FIX] in normalize_scores) because the
            # factors are computed further down this function.
            if is_fund:
                # [ETF] A fund has no earnings, margins or balance sheet: score
                # it on ETF.SCORED_FACTORS at the regime's weights, rescaled.
                # The company factors are not measured (0, hidden on the page).
                fundamental_score = quality_score = value_score = 0.0
                overall_score = _fund_score({"technical": tech_score, "momentum": momentum_score},
                                            weights)
            else:
                overall_score = (
                    tech_score * weights["technical"] +
                    fundamental_score * weights["fundamental"] +
                    momentum_score * weights["momentum"] +
                    quality_score * weights["quality"] +
                    value_score * weights["value"]
                )

            # **QUICK WIN #1: Normalize score to prevent scores >10**
            overall_score = min(10.0, max(0.0, overall_score))

            # The minimum-score gate runs once, on the FINAL score below, so
            # positive evidence (catalyst, insider buying, upward revisions)
            # can count exactly as negative evidence already did. When even the
            # maximum bounded adjustment cannot lift the base score to the
            # floor the outcome is already decided: reject now (outcome-identical
            # to the final gate) and skip the most expensive provider calls.
            base_score = overall_score
            _skip_enrichment = (
                base_score + TradingConfig.SCORE_ADJUSTMENT_CAP < TradingConfig.MIN_SCORE
            )
            if _skip_enrichment:
                self._record_candidate_rejection(ticker, 'score_below_min', base_score,
                    f"Base score {base_score:.2f} cannot reach {TradingConfig.MIN_SCORE:.2f} "
                    f"even with the maximum +{TradingConfig.SCORE_ADJUSTMENT_CAP:.2f} adjustment")
                logger.debug(
                    f"{ticker}: base score {base_score:.2f} cannot reach "
                    f"{TradingConfig.MIN_SCORE:.2f} even with the maximum "
                    f"+{TradingConfig.SCORE_ADJUSTMENT_CAP:.2f} adjustment"
                )
                self._record_filter('score_below_min')
                if not research_mode:
                    return None

            # ══════════════════════════════════════════════════════════════
            # [W10 FIX] ALL score adjustments are gathered FIRST, then the
            # signal is derived ONCE from the final score.
            #
            # Previously the signal was set from the raw composite and the
            # score then kept moving underneath it: a chase penalty (-1.5), a
            # contrarian penalty (-1.0), a consensus bonus (+0.3), a catalyst
            # boost (up to +2.0), an insider adjustment (+/-0.8) and an
            # earnings-revision adjustment — with only a single STRONG_BUY ->
            # BUY trim in response. The published score and the published
            # action were therefore derived from different numbers and could
            # openly disagree. The catalyst boost alone was worth up to 20% of
            # the whole 0-10 scale, handing the single largest lever in the
            # model to its least reliable input, and it landed after the
            # minimum-score gate had already been cleared.
            #
            # Adjustments are now summed, bounded by SCORE_ADJUSTMENT_CAP, and
            # logged component-by-component so the composite is auditable.
            # ══════════════════════════════════════════════════════════════
            adjustments: List[Tuple[str, float]] = []
            # Downgrade-only flags: applied to the signal AFTER it is derived.
            # These may lower conviction but never raise it.
            block_strong_buy = False

            # ── Chase prevention ───────────────────────────────────────────
            chase = self._chase_trigger(hist, ticker)
            if chase is not None and not chase.get('active', True):
                # [I6] A filled gap is published apart, with no score penalty.
                with self._chase_alerts_lock:
                    self.gap_filled_alerts.append(dict(chase, ticker=ticker, company=str(
                        info.get('longName') or info.get('shortName') or ticker)[:60]))
                chase = None
            is_chasing = chase is not None
            if is_chasing:
                adjustments.append(
                    ('chase', -(_T.CHASE.GROWTH_SCORE_PENALTY if chase['wide_thresholds']
                                else _T.CHASE.SCORE_PENALTY))
                )
                block_strong_buy = True
                logger.info(chase['reason'])
                # [PRE-MOMENTUM] Track for "too-late-to-chase" watchlist.
                # [A07] The alert carries its trigger, dates and distance from
                # the 20-day average, so the page states only what was measured.
                price_now = float(tech_indicators.current_price or 0.0)
                sma20 = float(getattr(tech_indicators, 'sma_20', 0.0) or 0.0)
                alert = dict(chase)
                alert.update({
                    'ticker': ticker,
                    'company': str(info.get('longName') or info.get('shortName') or ticker)[:60],
                    'price': price_now,
                    'rsi': float(tech_indicators.rsi),
                    'momentum_1m': float(tech_indicators.momentum_1m or 0),
                    # A fraction, like momentum_1m (0.042 = 4.2% above the average).
                    'distance_from_20dma': (round(price_now / sma20 - 1.0, 6)
                                            if price_now > 0 and sma20 > 0 else None),
                })
                with self._chase_alerts_lock:
                    self.chase_alerts.append(alert)

            # ── Wall St. consensus ─────────────────────────────────────────
            analyst_target = info.get('targetMeanPrice', 0) or 0
            analyst_recommendation = (info.get('recommendationKey') or 'none').upper()
            # A bearish consensus is a negative factor whatever WE think, so
            # this no longer keys off our own (not yet derived) signal via a
            # substring test on the signal name.
            if analyst_recommendation in ('SELL', 'UNDERPERFORM', 'STRONG_SELL'):
                adjustments.append(('analyst_bearish', -1.0))
                logger.debug(f"{ticker}: Wall St. consensus {analyst_recommendation}")
            elif analyst_recommendation in ('BUY', 'STRONG_BUY'):
                adjustments.append(('analyst_bullish', +0.3))

            # ── Catalyst / insider / earnings revisions ────────────────────
            # These used to be applied much later, after the signal, the
            # committee policy and the minimum-score gate had all run.
            catalyst_boost, institutional_signals = 0.0, []
            insider_info: Dict[str, Any] = {}
            if not _skip_enrichment:
                catalyst_boost, institutional_signals = self._fetch_catalyst_boost(
                    ticker=ticker, stock=stock, info=info, hist=hist,
                    current_price=current_price, market_cap=market_cap,
                    avg_volume=avg_volume, is_indian_stock=is_indian_stock,
                )
                if catalyst_boost:
                    adjustments.append(('catalyst', catalyst_boost))

                insider_info = self._fetch_insider_signal(
                    ticker, stock, current_price, market_cap=market_cap
                )
                insider_adj = float(insider_info.get("score_adjustment", 0.0) or 0.0)
                if insider_adj:
                    adjustments.append(('insider', insider_adj))
                    logger.info(
                        f"🕵️ {ticker}: Insider {insider_info['sentiment']} "
                        f"({insider_adj:+.1f} pts) — {insider_info.get('note','')}"
                    )

            revision_info: Dict[str, Any] = {}
            if (not _skip_enrichment and HAS_EARNINGS_REVISIONS
                    and compute_earnings_revision_signal is not None and not is_fund):
                try:
                    revision_info = compute_earnings_revision_signal(stock, info, ticker) or {}
                    revision_adj = float(revision_info.get("score_adjustment", 0.0) or 0.0)
                    if revision_adj:
                        adjustments.append(('eps_revisions', revision_adj))
                        logger.info(
                            f"📈 {ticker}: EPS revisions {revision_info.get('sentiment','UNKNOWN')} "
                            f"({revision_adj:+.1f} pts) — {revision_info.get('note','')}"
                        )
                    # Estimate cuts are a strong sell/avoid tell.
                    if revision_info.get("sentiment") == "DOWNGRADING":
                        block_strong_buy = True
                except Exception as e:
                    logger.debug(f"{ticker}: earnings-revision scoring failed - {e}")

            # ── Risk events are not netted away ───────────────────────────
            # [Audit #11] A bearish catalyst (contract loss, guidance cut,
            # investigation) or a run of estimate cuts is a risk EVENT, not a
            # score point that insider buying or a bullish consensus can cancel:
            # "contract terminated" -1.0 plus insider +0.8 used to net to -0.2.
            # When one is present the positive adjustments are dropped, and the
            # name cannot be a STRONG_BUY.
            bearish_event = (
                any(name == 'catalyst' and delta < 0 for name, delta in adjustments)
                or revision_info.get("sentiment") == "DOWNGRADING"
            )
            cap = TradingConfig.SCORE_ADJUSTMENT_CAP
            net_adjustment, adjustments, dropped = self.bounded_adjustment(
                adjustments, cap, bearish_event)
            if bearish_event:
                block_strong_buy = True
                if dropped:
                    logger.info(
                        f"{ticker}: bearish event present; positive adjustments not applied "
                        f"({', '.join(f'{n} {d:+.2f}' for n, d in dropped)})"
                    )

            # ── Apply the bounded net adjustment ──────────────────────────
            raw_adjustment = sum(delta for _name, delta in adjustments)
            if adjustments:
                detail = ', '.join(f"{n} {d:+.2f}" for n, d in adjustments)
                clipped = " (clipped to cap)" if abs(raw_adjustment) > cap else ""
                logger.debug(
                    f"{ticker}: base {overall_score:.2f} {net_adjustment:+.2f} "
                    f"[{detail}]{clipped} → {overall_score + net_adjustment:.2f}"
                )
            overall_score = min(10.0, max(0.0, overall_score + net_adjustment))

            # ── Crypto correlation: an eligibility gate, not a score nudge ──
            root_ticker = ticker.replace('.NS', '').replace('.BO', '')
            crypto_warning = ""
            if root_ticker in CRYPTO_CORRELATED_STOCKS:
                crypto_warning = "⚠️ HIGH CRYPTO CORRELATION - Bitcoin moves cause violent swings"
                logger.warning(f"⚠️ {ticker}: High crypto correlation - extreme volatility risk")
                if overall_score < TradingConfig.MIN_SCORE + 2.0:
                    self._record_candidate_rejection(ticker, 'crypto_score_low', overall_score,
                        f"Score {overall_score:.2f} below the crypto-correlation floor "
                        f"{TradingConfig.MIN_SCORE + 2.0:.2f}")
                    logger.debug(f"{ticker}: Crypto stock below elevated threshold")
                    self._record_filter('crypto_score_low')
                    if not research_mode:
                        return None

            # ── Minimum-score gate, on the FINAL score ────────────────────
            if overall_score < TradingConfig.MIN_SCORE:
                self._record_candidate_rejection(ticker,
                    'score_below_min' if base_score < TradingConfig.MIN_SCORE else 'score_below_min_post_penalty',
                    overall_score, f"Adjusted score {overall_score:.2f} below the entry floor {TradingConfig.MIN_SCORE:.2f}")
                logger.debug(
                    f"{ticker}: Score {overall_score:.1f} below minimum "
                    f"{TradingConfig.MIN_SCORE} after adjustments"
                )
                self._record_filter(
                    'score_below_min' if base_score < TradingConfig.MIN_SCORE
                    else 'score_below_min_post_penalty'
                )
                if not research_mode:
                    return None

            # ── Derive the signal ONCE, from the final score ──────────────
            signal = self._determine_signal_regime_aware(
                overall_score, tech_indicators, risk_metrics
            )

            # Downgrade-only rules. These can lower conviction, never raise it.
            if block_strong_buy and signal == SignalType.STRONG_BUY.value:
                signal = SignalType.BUY.value
                logger.info(
                    f"{ticker}: STRONG_BUY trimmed to BUY "
                    f"({'extended move' if is_chasing else 'negative EPS revisions'})"
                )

            # Only skip AVOID signals
            if signal == SignalType.AVOID.value:
                self._record_candidate_rejection(ticker, 'signal_avoid', overall_score,
                    self._signal_rejection_detail(signal, overall_score, tech_indicators))
                logger.debug(f"{ticker}: Signal {signal} - avoiding")
                self._record_filter('signal_avoid')
                if not research_mode:
                    return None

            # [B4 FIX] Trade levels are now derived BEFORE sizing, because
            # sizing is derived FROM the stop. Previously _calculate_position_size
            # ran first and never saw the stop at all, so the dollar risk of a
            # position was whatever fell out of volatility targeting — and
            # MAX_RISK_PER_TRADE was referenced nowhere except validate_config.
            entry, target, stop = self._calculate_price_targets(
                current_price, tech_indicators, risk_metrics, analyst_target=analyst_target
            )

            # The trailing stop STARTS at the hard stop. It used to start at
            # price - 1.75 ATR, i.e. ABOVE the 2.75 ATR hard stop (M9), so the
            # level a user would actually place on day one was the day-trade
            # stop M9 removed, while R:R and sizing assumed the wider one.
            # update_trailing_stops() ratchets it up from here, using
            # TRAILING_ATR_MULTIPLIER below the running high, once a real
            # high-since-entry exists — and never below this stop.
            trailing = stop

            # Calculate position sizing from the actual stop distance
            allocation = self._calculate_position_size(
                signal=signal, score=overall_score, entry=entry, stop=stop,
                volatility=risk_metrics.volatility
            )
            sized_signal = signal

            # FIX: Skip stocks with zero allocation (HOLD signals get 0 allocation)
            if allocation <= 0:
                logger.debug(f"{ticker}: Signal {signal} with zero allocation - skipping")
                # [ITEM 7] Classify the precise cause so the scan summary can
                # explain WHY a scored stock was sized to zero.
                if entry <= 0 or stop <= 0 or stop >= entry:
                    _z_sub = 'stop_distance'
                elif signal in (SignalType.HOLD.value, SignalType.AVOID.value, None):
                    _z_sub = 'signal_not_buy'
                elif self._drawdown_size_multiplier() <= 0:
                    _z_sub = 'risk_budget'
                else:
                    _z_sub = 'min_trade_size'
                # A HOLD/AVOID name was never sized down: the model did not
                # want it. Filing it under "zero_allocation" blamed sizing for
                # what was a lack of conviction.
                self._record_filter(
                    'signal_not_actionable' if _z_sub == 'signal_not_buy' else 'zero_allocation'
                )
                self._record_zero_allocation(ticker, _z_sub)
                self._record_candidate_rejection(ticker,
                    'signal_not_actionable' if _z_sub == 'signal_not_buy' else 'zero_allocation', overall_score,
                    self._signal_rejection_detail(signal, overall_score, tech_indicators)
                    if _z_sub == 'signal_not_buy' else f"No executable position: {_z_sub.replace('_', ' ')}")
                if not research_mode:
                    return None
                allocation = 0.0

            # **[P1 FIX] Event risk adjustment (FOMC weeks = smaller positions)**
            event_risk_mult, event_warning = self._get_event_risk_factor()
            if event_risk_mult < 1.0:
                allocation *= event_risk_mult
                logger.info(f"{ticker}: {event_warning}")

            def _size_for(sig: str) -> float:
                """Pre-multiplier size for ``sig`` (risk budget and caps)."""
                return self._calculate_position_size(
                    signal=sig, score=overall_score, entry=entry, stop=stop,
                    volatility=risk_metrics.volatility,
                ) * event_risk_mult

            # === BATCH 1: RISK LIMITS INTEGRATION ===
            
            # [BATCH 1 #13] Liquidity Filter - Position < 5% of ADV
            avg_volume = float(hist['Volume'].tail(20).mean())
            liquidity_ok, pct_of_adv = RiskLimits.check_liquidity(
                allocation, avg_volume, current_price
            )
            if not liquidity_ok:
                logger.debug(f"{ticker}: Failed liquidity check ({pct_of_adv:.1f}% of ADV)")
                self._record_filter('failed_liquidity')
                if not research_mode:
                    return None
                allocation = 0.0
            
            # [BATCH 1 #11] VaR Limit - No trade if VaR > 2% of account
            # [FIX #12] Pass daily returns for historical CVaR when available
            _daily_rets = None
            try:
                _close = hist['Close'].dropna()
                if len(_close) >= 30:
                    _daily_rets = _close.pct_change().dropna().values
            except Exception:
                pass
            var_ok, var_pct = RiskLimits.check_var_limit(
                allocation, risk_metrics.volatility, self.capital,
                daily_returns=_daily_rets
            )
            if not var_ok:
                logger.debug(f"{ticker}: Failed VaR limit ({var_pct*100:.1f}% > 2%)")
                self._record_filter('failed_var_limit')
                if not research_mode:
                    return None
                allocation = 0.0

            # **v6.0 FIX #2.4: Realistic Risk/Reward with TIERED Execution Costs**
            risk_amount = entry - stop
            reward_amount = target - entry
            
            # [BATCH 2 #6] Tiered transaction costs by market cap
            # ETFs have no marketCap in yfinance, which dropped every ETF into
            # the <$2B tier (100 bps round trip; SPY really costs ~1 bp) and
            # failed QQQ on the R:R gate on phantom costs alone. Tier ETFs on
            # fund assets instead.
            cost_mcap = float(fundamental_metrics.market_cap or 0.0)
            if cost_mcap <= 0 and (ticker_is_etf or str(info.get('quoteType', '')).upper() == 'ETF'):
                cost_mcap = _clean_float(info.get('totalAssets'))
            commission, slippage = TradingConfig.get_transaction_costs(
                cost_mcap, market="INDIA" if is_indian_stock else "US"
            )
            execution_cost_pct = (commission + slippage) * 2  # Round-trip
            execution_cost = entry * execution_cost_pct
            
            # Adjust reward and risk for execution costs
            adjusted_reward = reward_amount - execution_cost  # Costs eat into reward
            adjusted_risk = risk_amount + execution_cost      # Costs add to risk
            
            # Calculate realistic risk/reward
            risk_reward = safe_divide(adjusted_reward, adjusted_risk, default=0.0)

            if risk_reward < TradingConfig.RISK_REWARD_MIN:
                self._record_candidate_rejection(ticker, 'risk_reward_low', overall_score,
                    f"After-cost R:R {_T.fmt_rr(risk_reward)} below {_T.fmt_rr(TradingConfig.RISK_REWARD_MIN)}")
                logger.debug(f"{ticker}: R/R {risk_reward:.2f} below minimum (after costs)")
                self._record_filter('risk_reward_low')
                if not research_mode:
                    return None
                allocation = 0.0

            # Calculate shares (respects FRACTIONAL_SHARES config)
            shares = TradingConfig.size_shares(allocation, entry)

            # **[FIX #4] Confidence Tier (replaces uncalibrated tanh win probability)**
            # Instead of pretending to compute a probability via tanh, assign a
            # deterministic tier based on how many independent factors confirm the signal.
            confirmations = 0

            # 1. Score above median
            if overall_score >= 5.0:
                confirmations += 1

            # 2. Favorable regime
            regime_type = self.market_regime.regime_type
            if regime_type in ("BULL", "RANGING"):
                confirmations += 1

            # 3. Low volatility
            vol = risk_metrics.volatility
            if vol < 0.25:
                confirmations += 1

            # 4. Positive Sharpe
            if risk_metrics.sharpe_ratio > 0.5:
                confirmations += 1

            # 5. Technical confirmation (MACD + volume aligned)
            if (tech_indicators.macd_histogram > 0 and tech_indicators.volume_ratio > 1.2):
                confirmations += 1

            # 6. Momentum aligned (1-month and 1-week both positive)
            if (tech_indicators.momentum_1m > 0 and tech_indicators.momentum_1w > 0):
                confirmations += 1

            # Map confirmation count to tier
            if confirmations >= 5:
                confidence_tier = "HIGH"
            elif confirmations >= 3:
                confidence_tier = "MEDIUM"
            else:
                confidence_tier = "LOW"

            # === ELITE QUANTITATIVE FACTORS ===
            elite_factors = EliteFactorScores()
            try:
                # Style-factor heuristics (SMB/HML/RMW/CMA-inspired)
                elite_factors = EliteQuantEngine.calculate_fama_french_factors(hist, info, aligned_market)

                # Quality-Minus-Junk (QMJ) scoring
                qmj_quality, qmj_junk, qmj_spread = EliteQuantEngine.calculate_qmj_score(
                    info, hist, metrics=fundamental_metrics
                )
                elite_factors.quality_score = qmj_quality
                elite_factors.junk_score = qmj_junk
                elite_factors.qmj_spread = qmj_spread

                # Multi-Horizon Momentum
                momentum_scores = EliteQuantEngine.calculate_multi_horizon_momentum(hist)
                elite_factors.momentum_1m = momentum_scores['momentum_1m']
                elite_factors.momentum_3m = momentum_scores['momentum_3m']
                elite_factors.momentum_6m = momentum_scores['momentum_6m']
                elite_factors.momentum_12m = momentum_scores['momentum_12m']
                elite_factors.momentum_12_2 = momentum_scores['momentum_12_2']
                elite_factors.momentum_composite = momentum_scores['momentum_composite']

                # Betting-Against-Beta (BAB)
                annual_return = (1 + tech_indicators.momentum_6m) ** 2 - 1  # Proper annualization
                elite_factors.bab_score, elite_factors.beta_adjusted_return = \
                    EliteQuantEngine.calculate_betting_against_beta(
                        risk_metrics.beta, annual_return
                    )

                # [W13 FIX] elite_composite_score was computed here and then
                # NEVER READ — along with check_factor_vif,
                # calculate_true_factor_loadings, MarketRegime.factor_weights
                # and UniverseStats._percentile_rank. QMJ, betting-against-beta,
                # the style factors and 12-2 momentum were computed, displayed
                # and exported while influencing neither ranking nor sizing,
                # which made the output read as far more quantitatively
                # grounded than it was.
                #
                # The raw components live on very different scales (style
                # factors roughly -2..+3, qmj_spread roughly -3..+3.25,
                # momentum_composite a raw return). Normalise each to 0-10
                # before weighting, so the composite is interpretable on the
                # same scale as every other sub-score and can be given a real
                # weight in the blend below.
                def _to_ten(value: float, lo: float, hi: float) -> float:
                    if hi <= lo:
                        return 5.0
                    return max(0.0, min(10.0, (float(value) - lo) / (hi - lo) * 10.0))

                elite_factors.elite_composite_score = round(
                    _to_ten(elite_factors.value_factor, -2.0, 3.0) * 0.20
                    + _to_ten(elite_factors.profitability_factor, -2.0, 3.0) * 0.20
                    + _to_ten(elite_factors.qmj_spread, -3.0, 3.25) * 0.25
                    + _to_ten(elite_factors.momentum_composite, -0.30, 0.50) * 0.25
                    + _to_ten(elite_factors.bab_score, -2.0, 3.0) * 0.10,
                    2,
                )
            except Exception as e:
                logger.debug(f"{ticker}: Elite factor calculation warning - {e}")

            # === MARKET CLASSIFICATION ===
            gics_sector = MarketDataProvider.classify_gics_sector(
                ticker, info.get('sector', '')
            )

            # === TIMEFRAME PROJECTION ===
            # The projector's sector patterns are keyed on GICS names
            # ("Information Technology"); yfinance says "Technology", so most
            # names fell through to the generic default horizon.
            timeframe_projection = TimeframeProjector.project_timeframe(
                current_price=entry,
                target_price=target,
                volatility=risk_metrics.volatility,
                momentum=tech_indicators.momentum_1m,
                sector=gics_sector or info.get('sector', 'Unknown')
            )
            market_cap_tier = MarketDataProvider.classify_market_cap_tier(
                fundamental_metrics.market_cap, "INDIA" if is_indian_stock else "US"
            )

            # === RISK GRADE CALCULATION (A-F) ===
            # [F3] One grade, measured from the same bars the same way as the
            # Research engine (engine.risk_grade.grade_from_history, two years).
            _grade = self._risk_grade_for(hist_full, ticker, risk_metrics)
            risk_grade = _grade["grade"]
            risk_grade_drivers = _grade["drivers"]
            risk_grade_lookback_days = int(_grade["lookback_days"] or 0)

            signal, investment_committee_notes = self._apply_investment_committee_policy(
                signal=signal,
                score=overall_score,
                entry=entry,
                target=target,
                stop=stop,
                risk_reward=risk_reward,
                risk_grade=risk_grade,
                analyst_target=analyst_target,
                rsi=tech_indicators.rsi,
                insider_sentiment=insider_info.get("sentiment", "UNKNOWN"),
                insider_note=insider_info.get("note", ""),
                insider_relax_strength=insider_info.get("relax_strength", 0.0),
            )
            # [F7] Owner decision Q4: no Strong Buy until the scanner's evidence
            # is VALIDATED. Folding here, before the re-size below, sizes the
            # name at the BUY tier it is published as.
            if signal == SignalType.STRONG_BUY.value:
                from engine.evidence import ELITE_PRO as _EP, get_evidence as _ge
                if str(_ge(_EP).status.value).upper() != _T.LADDER.STRONG_BUY_EVIDENCE:
                    signal = SignalType.BUY.value
                    investment_committee_notes = list(investment_committee_notes or []) + [
                        "Shown as Buy: a Strong Buy needs a validated model (owner decision Q4)"]
            if signal in [SignalType.HOLD.value, SignalType.AVOID.value]:
                note = investment_committee_notes[0] if investment_committee_notes else "No new-money edge"
                logger.info(f"{ticker}: Committee HOLD - {note}")
                self._record_filter('committee_hold')
                self._record_candidate_rejection(ticker, 'committee_hold', overall_score, note)
                if not research_mode:
                    return None
                allocation = 0.0
            elif signal != sized_signal and allocation > 0:
                # The committee can trim (e.g. Grade D: BUY -> ACCUMULATE).
                # The size was derived from the pre-trim signal, so a trimmed
                # Grade-D name ended up LARGER than a clean Grade-A ACCUMULATE.
                allocation = min(allocation, _size_for(signal))
                sized_signal = signal

            # Risk grade must affect actual dollars, not only the displayed
            # action label.  This also fulfils the committee's Grade-D
            # "reduced size" contract.  Grade F is already blocked above.
            risk_size_multiplier = TradingConfig.RISK_GRADE_SIZE_MULTIPLIERS.get(
                str(risk_grade).upper(), _T.SIZING.UNKNOWN_GRADE_SIZE_MULTIPLIER
            )
            # [M10 FIX] Earnings proximity now scales the position instead of
            # excluding the name (or silently failing open when the calendar
            # is missing).
            allocation = round(allocation * risk_size_multiplier * earnings_size_multiplier, 2)
            if allocation < TradingConfig.min_position_size(self.capital) * max(
                    self._drawdown_size_multiplier(), 0.0):
                logger.info(f"{ticker}: Risk-grade allocation is below minimum trade size")
                self._record_filter('risk_grade_size_below_minimum')
                self._record_candidate_rejection(ticker, 'risk_grade_size_below_minimum', overall_score,
                    "Risk and earnings adjustments leave less than the minimum position size")
                self._record_zero_allocation(ticker, 'risk_budget')
                if not research_mode:
                    return None
                allocation = 0.0
            shares = TradingConfig.size_shares(allocation, entry)

            # Create comprehensive analysis
            analysis = StockAnalysis(
                ticker=ticker,
                company=str(info.get('longName') or info.get('shortName') or ticker)[:60],
                sector=str(get_etf_sector(ticker, info.get('sector') or (info.get('category') if is_etf(ticker) else None) or 'Unknown'))[:30],
                industry=str(info.get('industry') or 'Unknown')[:40],

                exchange=info.get('exchange') or 'UNKNOWN',

                signal=signal,
                overall_score=round(overall_score, 2),
                technical_score=round(tech_score, 2),
                fundamental_score=round(fundamental_score, 2),
                momentum_score=round(momentum_score, 2),
                quality_score=round(quality_score, 2),
                value_score=round(value_score, 2),

                entry_price=round(entry, 2),
                price_target=round(target, 2),
                stop_loss=round(stop, 2),
                trailing_stop=round(trailing, 2),

                allocation=round(allocation, 2),
                shares=shares,
                position_pct=round((allocation / self.capital) * 100, 2),

                risk_amount=round(risk_amount, 2),
                reward_amount=round(reward_amount, 2),
                risk_reward_ratio=round(risk_reward, 2),
                signal_strength=0.0,
                confidence_tier=confidence_tier,
                confidence_confirmations=confirmations,
                confidence_factors_total=6,
                expected_value=0.0,

                technical_indicators=tech_indicators,
                fundamental_metrics=fundamental_metrics,
                risk_metrics=risk_metrics,
                elite_factors=elite_factors,
                timeframe_projection=timeframe_projection,

                market_cap_tier=market_cap_tier,
                gics_sector=gics_sector,

                confidence_level=round(overall_score / 10.0, 2),
                risk_grade=risk_grade,
                risk_grade_drivers=risk_grade_drivers,
                risk_grade_lookback_days=risk_grade_lookback_days,
                retrieval_timestamp=datetime.now().astimezone().isoformat(),
                market_timestamp=pd.Timestamp(hist.index[-1]).isoformat(),
                quote_time=_quote_time_iso(info),
                currency=str(info.get('currency') or info.get('financialCurrency') or 'UNKNOWN'),
                insider_sentiment=insider_info.get("sentiment", "UNKNOWN"),
                insider_buys_90d=int(insider_info.get("buys_90d", 0) or 0),
                insider_sells_90d=int(insider_info.get("sells_90d", 0) or 0),
                insider_net_shares_90d=float(insider_info.get("net_shares_90d", 0.0) or 0.0),
                insider_net_value_90d=float(insider_info.get("net_value_90d", 0.0) or 0.0),
                insider_note=insider_info.get("note", "") or "",
                revision_sentiment=str(revision_info.get("sentiment", "UNKNOWN")),
                revision_score=_clean_float(revision_info.get("revision_score"), 5.0),
                net_revisions_30d=int(revision_info.get("net_revisions_30d", 0) or 0),
                eps_trend_pct_30d=float(revision_info.get("eps_trend_pct_30d", 0.0) or 0.0),
                avg_surprise_pct=float(revision_info.get("avg_surprise_pct", 0.0) or 0.0),
                revision_note=str(revision_info.get("note", "") or ""),
                investment_committee_notes=investment_committee_notes,
                _hist_cache=hist  # Cache for correlation checks (avoids extra API calls)
            )

            logger.debug(f"✓ {ticker}: Score={overall_score:.1f}, Signal={signal}, R/R={risk_reward:.1f}")
            analysis.score_adjustment = float(net_adjustment)
            analysis.ranking_score = analysis.overall_score
            # [Why picked] Keep what the scan read, for the page.
            analysis.analyst_rating = str(info.get('recommendationKey') or '').lower().replace('none', '')
            analysis.analyst_rec_mean = _clean_float(info.get('recommendationMean'))
            analysis.analyst_count = int(_clean_float(info.get('numberOfAnalystOpinions')))
            analysis.analyst_target = _clean_float(analyst_target)
            analysis.analyst_target_low = _clean_float(info.get('targetLowPrice'))
            analysis.analyst_target_high = _clean_float(info.get('targetHighPrice'))
            analysis.catalysts = [
                {"type": str(getattr(sig, 'catalyst_type', '') or ''),
                 "summary": str(getattr(sig, 'catalyst_summary', '') or '')[:160],
                 "action": str(getattr(sig, 'action', '') or ''),
                 "confidence": round(_clean_float(getattr(sig, 'confidence', 0.0)), 2)}
                for sig in (institutional_signals or [])[:_T.WHY.MAX_ITEMS]]
            analysis.days_to_earnings = days_to_earnings if calendar_known and not is_fund else None
            analysis.is_fund = bool(is_fund)
            analysis.scored_factors = list(_T.ETF.SCORED_FACTORS) if is_fund else []
            analysis.style_screen = style_result
            analysis._chart_hist = hist_full
            
            # [ELITE FIX] Calculate conviction & setup type
            analysis.conviction = self.scoring_engine.calculate_conviction(analysis, None)
            analysis.setup_type = self.scoring_engine.classify_setup_type(analysis)
            # [PRE-MOMENTUM] Run the N-of-M early-momentum classifier on every
            # analysed stock; downstream scan modes / display layer can filter
            # or rank by `early_momentum_score`.
            em_score, em_signals = self.scoring_engine.classify_early_momentum(analysis)
            analysis.early_momentum_score = em_score
            analysis.early_momentum_signals = em_signals
            # [FIX] Use tiered transaction-cost model based on market cap instead
            # of a flat 15 bps default that systematically understates small-cap costs.
            analysis.transaction_cost_bps = round(execution_cost_pct * 10_000, 1)
            
            # [FIX #7] Wire up multi-scenario price targets (was dead code)
            analysis.price_scenarios = self._calculate_price_scenarios(
                current_price, tech_indicators, risk_metrics, info
            )
            
            # [PERF FIX] Cache news headline & earnings days on analysis object
            # Avoids N+1 API calls in display_elite_minimal (each yf.Ticker call = ~0.5s)
            try:
                items = self._company_news(ticker, stock)
                headlines = _headlines_about(ticker, info, [i["title"] for i in items])[:1]
                if headlines:
                    title = headlines[0][:80]
                    analysis._cached_news_headline = title
                analysis.news = _news_about(ticker, info, items)[:_T.WHY.NEWS_ITEMS]
            except Exception:
                pass
            analysis._cached_earnings_days = days_to_earnings

            # ══════════════════════════════════════════════════════════════
            # [FIX #18] NaN / Inf sanitization before returning analysis
            # ══════════════════════════════════════════════════════════════
            for attr in ('overall_score', 'technical_score', 'fundamental_score',
                         'momentum_score', 'quality_score', 'value_score',
                         'entry_price', 'price_target', 'stop_loss', 'trailing_stop',
                         'allocation', 'risk_amount', 'reward_amount',
                         'risk_reward_ratio', 'signal_strength', 'expected_value',
                         'position_pct', 'confidence_level'):
                val = getattr(analysis, attr, None)
                if val is not None and (np.isnan(val) or np.isinf(val)):
                    setattr(analysis, attr, 0.0)
                    logger.debug(f"{ticker}: sanitized NaN/Inf in {attr}")

            # [DECISION-CORE] Resolve a single FinalAction that summary and
            # detail layers both read from. This is the fix for
            # 'BP/ADM/ADI show HOLD in summary but BUY in detail'.
            if HAS_DECISION_CORE:
                try:
                    # SP_ELITE_PRO scores stocks on a 0–10 scale; the shared
                    # decision engine expects 0–100. Always scale up—the source
                    # range here is fixed, so a guarded comparison is fragile
                    # at the boundary (e.g. a score of exactly 10.0).
                    _composite_100 = float(analysis.overall_score or 0.0) * _T.SCORE.TO_100
                    # ── Qualitative red flags shared with the deep analyzer via
                    #    the decision-core (cheap proxies computed here so the
                    #    scanner cannot publish a BUY the deep analyzer would
                    #    veto). ──
                    _bear_rev, _bear_name = _detect_bearish_reversal(hist)
                    # The scanner has no DCF. It checks the analyst mean target
                    # instead and reports it as what it is (ANALYST_DOWNSIDE,
                    # audit F9), against LADDER.ANALYST_DOWNSIDE_TRIM_PCT.
                    _analyst_down = None
                    try:
                        _at = float(analyst_target or 0.0)
                        _entry = float(analysis.entry_price or 0.0)
                        if _at > 0 and _entry > 0 and _at < _entry:
                            _analyst_down = (_entry - _at) / _entry * 100.0
                    except Exception:
                        _analyst_down = None
                    _heavy_insider = (
                        str(insider_info.get("sentiment", "")).upper() == "BEARISH"
                        and "HEAVY" in str(insider_info.get("note", "")).upper()
                    )
                    di = DecisionInputs(
                        ticker=analysis.ticker,
                        composite_score=_composite_100,
                        risk_grade=str(analysis.risk_grade or 'C').upper(),
                        risk_reward_ratio=float(analysis.risk_reward_ratio or 0.0),
                        current_price=float(analysis.entry_price or 0.0),
                        stop_loss=float(analysis.stop_loss or 0.0),
                        target_price=float(analysis.price_target or 0.0),
                        has_existing_position=bool(getattr(analysis, 'has_existing_position', False)),
                        is_valid=not bool(getattr(analysis, 'is_invalid', False)),
                        bearish_reversal=_bear_rev,
                        bearish_reversal_name=_bear_name,
                        analyst_downside_pct=_analyst_down,
                        heavy_insider_selling=_heavy_insider,
                    )
                    dec = resolve_final_action(di)

                    # [W6 FIX] The decision core is a CONSERVATISM RATCHET, not
                    # a replacement verdict. `_candidate_from_score` maps score
                    # alone, so adopting its answer unconditionally let it
                    # PROMOTE names past the confirmations
                    # `_determine_signal_regime_aware` deliberately requires for
                    # STRONG_BUY — MACD > 0, volume > 1.3x, positive momentum,
                    # Sharpe > 0.5. A 7.6 score with none of them became
                    # STRONG_BUY, even though the comment claimed the core could
                    # only make things more conservative. It now can only ever
                    # move the recommendation DOWN the conviction ladder, using
                    # the same _ACTION_RANK ordering deep-confirm already uses.
                    _core_action = dec.final_action.value
                    _scanner_action = signal_to_final_action(
                        analysis.signal,
                        bool(getattr(analysis, 'has_existing_position', False)),
                    ).value if signal_to_final_action is not None else _core_action

                    _core_rank = self._rank_action(_core_action)
                    _scanner_rank = self._rank_action(_scanner_action)

                    if _core_rank <= _scanner_rank:
                        analysis.final_action = _core_action
                        analysis.final_action_reasons = list(dec.reasons)
                    else:
                        analysis.final_action = _scanner_action
                        analysis.final_action_reasons = list(dec.reasons) + [
                            f"Decision core proposed {_core_action} on score alone; held at "
                            f"{_scanner_action} because the scanner's technical "
                            f"confirmations were not met (never upgrade)."
                        ]
                        logger.debug(
                            f"{ticker}: decision core would have upgraded "
                            f"{_scanner_action} → {_core_action}; suppressed."
                        )

                    # Keep the legacy `signal` aligned with the resolved action
                    # so the summary table, detail card AND the opportunity
                    # gates in get_top_recommendations all read the same
                    # verdict (single source of truth).
                    _new_sig = _FINAL_ACTION_TO_SIGNAL.get(analysis.final_action)
                    if _new_sig is not None and _new_sig != analysis.signal:
                        logger.info(
                            f"{ticker}: signal {analysis.signal} → final action "
                            f"{analysis.final_action} ({'; '.join(analysis.final_action_reasons) or 'gated'})"
                        )
                        analysis.signal = _new_sig
                    # Zero the allocation immediately if action doesn't allocate
                    if analysis.final_action in _NON_ALLOCATING_ACTIONS:
                        apply_allocation(analysis, 0.0, self.capital)
                    elif analysis.signal != sized_signal and analysis.allocation > 0:
                        # The core ratcheted the action down (e.g. BUY at a
                        # 6.0-6.5 composite -> ACCUMULATE). Resize to the
                        # final action's risk budget; never size up.
                        resized = round(
                            _size_for(analysis.signal)
                            * risk_size_multiplier * earnings_size_multiplier, 2
                        )
                        apply_allocation(
                            analysis, min(analysis.allocation, resized), self.capital
                        )
                except Exception as _e:
                    # The engine drives the authoritative recommendation—a silent
                    # failure here means summary/detail can disagree again.
                    logger.warning(f"{ticker}: FinalAction resolve failed: {_e}")

            # A buy-tier action with no money behind it is not an order. In
            # research mode a risk gate (liquidity, VaR, R:R, min size) zeroes
            # the allocation but used to leave BUY in place — the deep-dive
            # published "BUY, 0 shares" with "Passed new-money policy".
            if (float(analysis.allocation or 0.0) <= 0
                    and analysis.final_action in {"STRONG_BUY", "BUY", "ACCUMULATE"}):
                analysis.final_action = "WATCHLIST"
                analysis.signal = SignalType.HOLD.value
                reason = ("A risk gate (liquidity, VaR, risk/reward or minimum trade "
                          "size) sized this to zero; watch, do not buy.")
                if reason not in analysis.final_action_reasons:
                    analysis.final_action_reasons.append(reason)

            # A deep-dive may inspect a symbol during the earnings blackout,
            # but it must never turn that research view into a new-money order.
            if research_mode and 0 <= days_to_earnings <= 14:
                earnings_reason = (
                    f"Earnings in {days_to_earnings} days: research only; "
                    "wait until the event risk clears before opening a position."
                )
                # Earnings can downgrade an otherwise actionable result to
                # WATCHLIST, but must never upgrade a more conservative AVOID.
                if analysis.final_action in {"STRONG_BUY", "BUY", "ACCUMULATE", ""}:
                    analysis.final_action = "WATCHLIST"
                    analysis.signal = SignalType.HOLD.value
                analysis.allocation = 0.0
                analysis.shares = 0
                analysis.position_pct = 0.0
                if earnings_reason not in analysis.final_action_reasons:
                    analysis.final_action_reasons.append(earnings_reason)

            lock = getattr(self, '_diagnostics_lock', None)
            if lock is not None:
                with lock:
                    fields = self._provider_reads.get(ticker, {})
                    analysis.provider_reads = [dict(fields[k]) for k in sorted(fields)]
            return analysis

        except Exception as e:
            logger.warning(f"✗ {ticker}: Analysis failed - {type(e).__name__}: {str(e)}")
            # Emit full traceback at DEBUG level (captured by rotating file handler)
            # so root-cause is recoverable without re-running with --debug.
            import traceback as _tb
            logger.debug(f"{ticker}: Analysis failed traceback:\n{_tb.format_exc()}")
            # [SCAN-HEALTH] Classify + persist the REAL exception so the UI never
            # hides a data-provider/network failure behind a generic counter.
            self._record_scan_error(ticker, e, stage="analyze_stock")
            self._record_failed_ticker(ticker)
            return None

    def _determine_signal(self, score: float, indicators: TechnicalIndicators,
                          risk_metrics: RiskMetrics) -> str:
        """Determine trading signal with multi-factor confirmation (legacy - still used by older code)"""

        rsi = indicators.rsi
        macd_hist = indicators.macd_histogram
        vol_ratio = indicators.volume_ratio
        momentum = indicators.momentum_1m
        sharpe = risk_metrics.sharpe_ratio
        volatility = risk_metrics.volatility

        # STRONG_BUY: Excellent score + strong technical confirmation
        if (score > TradingConfig.EXCELLENT_SCORE and
                rsi < 70 and
                macd_hist > 0 and
                momentum > 0 and
                vol_ratio > 1.3 and
                sharpe > TradingConfig.MIN_SHARPE_RATIO and
                volatility < TradingConfig.HIGH_VOLATILITY):
            return SignalType.STRONG_BUY.value

        # BUY: Good score + reasonable confirmation
        elif (score > TradingConfig.GOOD_SCORE and
              rsi < 75 and
              momentum > -0.05 and
              sharpe > 0):
            return SignalType.BUY.value

        # ACCUMULATE: Decent score
        elif (score > TradingConfig.MIN_SCORE and
              rsi < 80):
            return SignalType.ACCUMULATE.value

        # HOLD: Marginal score (lowered from 5.5 to catch more borderline stocks)
        elif score > 4.5:
            return SignalType.HOLD.value

        # AVOID: Low score
        else:
            return SignalType.AVOID.value

    def _entry_rsi_limit(self) -> float:
        growth = 'growth' in str(getattr(self, 'current_scan_type', 'standard')).lower()
        return _T.REGIME.RSI_OVERBOUGHT_GROWTH if growth else _T.REGIME.RSI_OVERBOUGHT

    def _signal_rejection_detail(self, signal: str, score: float,
                                 indicators: TechnicalIndicators) -> str:
        limit = self._entry_rsi_limit()
        if indicators.rsi > limit:
            return f"{signal}: RSI {indicators.rsi:.1f} exceeds the entry limit {limit:g}; wait for a pullback"
        if score < TradingConfig.ACCUMULATE_SCORE:
            return f"{signal}: score {score:.2f} below the entry floor {TradingConfig.ACCUMULATE_SCORE:.2f}"
        return f"{signal}: entry confirmations were not met"

    def _determine_signal_regime_aware(self, score: float, indicators: TechnicalIndicators,
                                     risk_metrics: RiskMetrics) -> str:
        """
        **v6.0 FIX #2.2: Regime-aware signal generation with RSI oversold paradox fix**
        **QUICK WIN #2: Adaptive thresholds based on market regime**
        **PRIORITY 3: Block overbought entries**
        """
        
        # Get current market regime
        regime = self.market_regime
        vix = regime.vix_level
        
        rsi = indicators.rsi
        macd_hist = indicators.macd_histogram
        momentum_1w = indicators.momentum_1w
        vol_ratio = indicators.volume_ratio
        
        # CRITICAL BUG FIX #3: Reject overbought entries (regime & scan-type aware)
        overbought_threshold = self._entry_rsi_limit()
        
        if rsi > overbought_threshold:
            # Too overbought - wait for pullback
            logger.debug(f"RSI {rsi:.0f} too high (threshold {overbought_threshold}) - waiting for pullback")
            if score > _T.REGIME.OVERBOUGHT_HOLD_SCORE:
                return SignalType.HOLD.value  # Quality stock but wait
            else:
                return SignalType.AVOID.value  # Skip entirely
        
        # **Adaptive thresholds based on market regime (regime takes priority over VIX)**
        if regime.regime_type == "BULL":
            # Bull markets can be constructive, but BUY still needs a true edge.
            excellent_threshold = TradingConfig.EXCELLENT_SCORE
            good_threshold = TradingConfig.GOOD_SCORE
        elif regime.regime_type in _T.REGIME.SIGNAL_THRESHOLDS:
            # BEAR / VOLATILE / RANGING raise the bars (engine.thresholds.REGIME).
            # A None bar means "keep the base SCORE threshold".
            _ex, _good = _T.REGIME.SIGNAL_THRESHOLDS[regime.regime_type]
            excellent_threshold = TradingConfig.EXCELLENT_SCORE if _ex is None else _ex
            good_threshold = TradingConfig.GOOD_SCORE if _good is None else _good
        else:  # NEUTRAL or unknown
            # VIX-based fallback only when regime is not clearly classified
            if vix < _T.REGIME.NEUTRAL_VIX_CALM:
                excellent_threshold, good_threshold = _T.REGIME.NEUTRAL_CALM_THRESHOLDS
            elif vix > _T.REGIME.NEUTRAL_VIX_STRESSED:
                excellent_threshold, good_threshold = _T.REGIME.NEUTRAL_STRESSED_THRESHOLDS
            else:
                excellent_threshold = TradingConfig.EXCELLENT_SCORE
                good_threshold = TradingConfig.GOOD_SCORE
        
        # **v6.0 FIX #2.2: RSI OVERSOLD PARADOX** — a quality name that is
        # deeply oversold but turning up. It now runs AFTER the regime
        # thresholds are known: it used to run first against a flat
        # GOOD_SCORE, skipping Sharpe, the volatility cap and the regime bar,
        # so a 6.1 score with Sharpe -0.8 and 70% vol became STRONG_BUY in a
        # BEAR market (whose normal STRONG_BUY bar is 9.0).
        if rsi < 35:  # Deeply oversold
            is_recovering = (momentum_1w > 0) or (macd_hist > 0)  # Showing recovery
            has_quality = score > good_threshold
            trend_confirmed = indicators.current_price >= indicators.sma_20

            # [Audit #13] The bounce path now needs the same risk-adjusted floor
            # as the regular BUY (Sharpe > 0). It used to skip it, so a falling,
            # negative-Sharpe name could reach BUY through the oversold door.
            if is_recovering and has_quality and trend_confirmed and risk_metrics.sharpe_ratio > 0:
                sharpe_ok = risk_metrics.sharpe_ratio > TradingConfig.MIN_SHARPE_RATIO
                vol_ok = risk_metrics.volatility < TradingConfig.HIGH_VOLATILITY
                if vol_ratio > 1.3 and sharpe_ok and vol_ok and score > excellent_threshold:
                    logger.debug(f"RSI {rsi:.0f} oversold bounce with volume confirmation → STRONG_BUY")
                    return SignalType.STRONG_BUY.value
                logger.debug(f"RSI {rsi:.0f} oversold bounce → BUY")
                return SignalType.BUY.value
            elif is_recovering and has_quality and not trend_confirmed:
                logger.debug(f"RSI {rsi:.0f} recovering but below 20SMA - delaying buy signal")

        macd_hist = indicators.macd_histogram
        vol_ratio = indicators.volume_ratio
        momentum = indicators.momentum_1m
        sharpe = risk_metrics.sharpe_ratio
        volatility = risk_metrics.volatility

        # STRONG_BUY: Excellent score + strong technical confirmation (regime-adjusted)
        if (score > excellent_threshold and
                rsi < overbought_threshold and
                macd_hist > 0 and
                momentum > 0 and
                vol_ratio > 1.3 and
                sharpe > TradingConfig.MIN_SHARPE_RATIO and
                volatility < TradingConfig.HIGH_VOLATILITY):
            return SignalType.STRONG_BUY.value

        # BUY: Good score + reasonable confirmation (regime-adjusted)
        elif (score > good_threshold and
              rsi < overbought_threshold and  # FIX: Consistent with overbought threshold
              momentum > -0.05 and
              sharpe > 0):
            return SignalType.BUY.value

        # ACCUMULATE: Decent score + not overbought.
        elif score >= TradingConfig.ACCUMULATE_SCORE and rsi <= overbought_threshold:
            return SignalType.ACCUMULATE.value

        # HOLD: Marginal score or quality name without a new-money edge.
        elif score >= TradingConfig.MIN_SCORE:
            return SignalType.HOLD.value

        # AVOID: Low score
        else:
            return SignalType.AVOID.value

    def _fetch_insider_signal(self, ticker: str, stock: Any, current_price: float,
                              market_cap: float = 0.0) -> Dict[str, Any]:
        """
        Open-market insider activity from yfinance ``insider_transactions``.

        Returns a dict with sentiment, transaction counts, net shares/value over the
        last ~90 days, a one-line note, and a small score adjustment (-0.8..+0.6)
        to be added to the 0-10 composite score.

        Only OPEN-MARKET trades carry information. yfinance's ``Shares`` column
        is always positive and ``Transaction`` is usually empty; the direction
        lives in ``Text`` ("Purchase at price ...", "Sale at price ...").
        The old test — ``shares > 0 and not a sale`` counts as a buy — scored
        stock awards, gifts, option exercises and blank rows as purchases
        (MSFT: 9 awards + 1 gift + 4 sales read "Insiders net buyers $78.7M",
        BULLISH, which also relaxed the committee's hurdles). A single NaN
        ``Value`` made the whole net NaN and silently neutralised the signal
        (AAPL's 6 sales / 0 buys read NEUTRAL).
        """
        result = {
            "sentiment": "UNKNOWN",
            "buys_90d": 0,
            "sells_90d": 0,
            "net_shares_90d": 0.0,
            "net_value_90d": 0.0,
            "note": "",
            "score_adjustment": 0.0,
            "relax_strength": 0.0,
        }
        try:
            txns = getattr(stock, 'insider_transactions', None)
            if txns is None or getattr(txns, 'empty', True):
                return result

            df = txns.copy()
            I = _T.INSIDER
            cutoff = datetime.now() - timedelta(days=int(I.WINDOW_DAYS))

            # Identify columns we care about (yfinance schema can vary)
            date_col = next((c for c in df.columns if 'date' in str(c).lower()), None)
            shares_col = next((c for c in df.columns if str(c).lower() in {'shares', 'share'}), None)
            value_col = next((c for c in df.columns if 'value' in str(c).lower()), None)
            text_cols = [
                c for c in df.columns
                if str(c).lower() in {'text', 'transaction', 'description'}
            ]
            who_col = next((c for c in df.columns if str(c).lower() in {'insider', 'filer', 'name'}), None)
            # Newest first, so the 90-day window is not cut by an arbitrary head().
            if date_col is not None:
                df['_ts'] = pd.to_datetime(df[date_col], errors='coerce')
                df = df.sort_values('_ts', ascending=False)

            def _num(v: Any) -> float:
                try:
                    f = float(v)
                    return f if np.isfinite(f) else 0.0
                except (TypeError, ValueError):
                    return 0.0

            net_shares = 0.0
            net_value = 0.0
            buys = 0
            sells = 0
            buyers: set = set()      # distinct buying insiders (unnamed rows count once)

            if date_col is None:
                # Without dates nothing can be placed inside the 90-day window,
                # and an undated row is not evidence of recent buying or selling.
                return result
            for _, row in df.iterrows():
                ts = row.get('_ts')
                if pd.isna(ts):
                    continue
                ts = ts.to_pydatetime().replace(tzinfo=None)
                if not (cutoff <= ts <= datetime.now()):
                    continue

                txn_text = " ".join(
                    str(row.get(c) or '') for c in text_cols
                ).strip().lower()
                if txn_text.startswith('purchase') or ' purchase at' in txn_text:
                    direction = 1
                elif txn_text.startswith('sale') or ' sale at' in txn_text:
                    direction = -1
                else:
                    continue  # award / gift / exercise / conversion / blank

                shares = abs(_num(row.get(shares_col))) if shares_col else 0.0
                value = abs(_num(row.get(value_col))) if value_col else 0.0
                if value <= 0 and shares > 0 and current_price > 0:
                    value = shares * current_price

                net_shares += direction * shares
                net_value += direction * value
                if direction > 0:
                    buys += 1
                    buyers.add(str(row.get(who_col) or '').strip().lower() if who_col else '')
                else:
                    sells += 1

            result["buys_90d"] = buys
            result["sells_90d"] = sells
            result["net_shares_90d"] = round(net_shares, 0)
            result["net_value_90d"] = round(net_value, 0)

            # Thresholds are USD; "heavy" selling scales with company size so
            # routine 10b5-1 sales at a $4T company (NVDA: $575M, ~0.01% of
            # the float) do not force a HOLD the way they would at a $10B one.
            market = "INDIA" if ticker.endswith(('.NS', '.BO')) else "US"
            sym = "₹" if market == "INDIA" else "$"
            net_usd = TradingConfig.to_usd(net_value, market)
            mcap_usd = TradingConfig.to_usd(market_cap, market)
            heavy_usd = max(float(I.HEAVY_SELL_FLOOR_USD), float(I.HEAVY_SELL_MCAP_FRACTION) * mcap_usd)

            # [B-82] How far this buying may relax the committee hurdles: size
            # (net buying against the full-strength amount) times breadth
            # (distinct buying insiders). One small purchase relaxes nothing.
            strength = 0.0
            if buys > sells and net_usd >= float(I.RELAX_MIN_NET_USD):
                size = min(1.0, net_usd / float(I.RELAX_FULL_NET_USD))
                breadth = min(1.0, len(buyers) / float(I.RELAX_FULL_BUYERS))
                strength = round(size * breadth, 4)
            result["relax_strength"] = strength

            adj = 0.0
            days = int(I.WINDOW_DAYS)
            if buys + sells == 0:
                sentiment = "UNKNOWN"
                note = f"No open-market insider trades in {days}d"
            elif net_usd >= float(I.STRONG_BUY_NET_USD) and buys >= int(I.STRONG_BUY_MIN_BUYS):
                sentiment = "BULLISH"
                adj = float(I.SCORE_ADJ_STRONG_BUY) * strength
                note = f"Insiders net buyers {sym}{net_value/1e6:.1f}M ({buys} buys vs {sells} sells, {days}d)"
            elif net_usd > 0 and buys > sells:
                sentiment = "BULLISH"
                adj = float(I.SCORE_ADJ_NET_BUY) * strength
                note = f"Net insider buying ({sym}{net_value/1e6:.2f}M, {buys}/{sells})"
            elif net_usd <= -heavy_usd:
                sentiment = "BEARISH"
                adj = float(I.SCORE_ADJ_HEAVY_SELL)
                note = f"Heavy insider selling ({sym}{net_value/1e6:.1f}M net, {sells} sells)"
            elif net_usd < 0 and sells > buys:
                # Routine selling (diversification, taxes, 10b5-1 plans) is
                # weakly informative next to purchases (Lakonishok & Lee 2001),
                # so it only nudges; HEAVY selling above carries the override.
                sentiment = "BEARISH"
                adj = float(I.SCORE_ADJ_NET_SELL)
                note = f"Net insider selling ({sym}{net_value/1e6:.2f}M, {buys}/{sells})"
            else:
                sentiment = "NEUTRAL"
                note = f"Mixed insider activity ({buys} buys / {sells} sells)"

            result["sentiment"] = sentiment
            result["note"] = note
            result["score_adjustment"] = adj
        except Exception as e:
            logger.debug(f"{ticker}: insider fetch failed - {e}")
        return result

    def _company_news(self, ticker: str, stock: Any) -> List[Dict[str, Any]]:
        """[Why picked] The name's news items: yfinance's, or the Yahoo RSS feed
        when yfinance returns none. Read once per name per scan."""
        cache = self.__dict__.setdefault('_news_cache', {})
        if ticker in cache:
            return cache[ticker]
        items = _extract_news_items(getattr(stock, 'news', None) or [])
        if not items:
            try:
                items = _rss_news_items(ticker)
            except Exception as exc:  # noqa: BLE001 - news is optional evidence
                logger.debug(f"{ticker}: RSS news unavailable - {exc}")
                items = []
        cache[ticker] = items
        return items

    @staticmethod
    def bounded_adjustment(adjustments: List[Tuple[str, float]], cap: float,
                           bearish_event: bool) -> Tuple[float, List[Tuple[str, float]],
                                                         List[Tuple[str, float]]]:
        """Net score adjustment, bounded to +/-``cap``: (net, kept, dropped).

        [Audit #11] With a bearish risk event present the positive adjustments
        are dropped, not netted against it.
        """
        dropped = [(n, d) for n, d in adjustments if d > 0] if bearish_event else []
        kept = [(n, d) for n, d in adjustments if not (bearish_event and d > 0)]
        raw = sum(d for _n, d in kept)
        return max(-cap, min(cap, raw)), kept, dropped

    def _fetch_catalyst_boost(self, ticker: str, stock: Any, info: Dict,
                              hist: pd.DataFrame, current_price: float,
                              market_cap: float, avg_volume: float,
                              is_indian_stock: bool) -> Tuple[float, List[Any]]:
        """Detect news/filing catalysts and return (score_boost, signals).

        [W10 FIX] Extracted from the middle of ``analyze_stock``, where it used
        to run *after* the signal had been derived and the minimum-score gate
        had already been cleared — then add up to +2.0 to the composite, 20% of
        the entire scale, from headline NLP. The boost is now one bounded
        component of the pre-signal adjustment budget like any other.
        """
        catalyst_boost = 0.0
        institutional_signals: List[Any] = []
        # [Audit #12] Each tier now falls through to the next when it is
        # unavailable OR raises. It used to be an if/elif chain, so an installed
        # institutional engine that threw left the name with no catalyst read
        # at all (logged at debug), and "no news" looked like "news feed down".
        done = False
        _headlines_cache: List[List[str]] = []

        def _headlines() -> List[str]:
            if not _headlines_cache:     # one fetch, shared by every tier
                _headlines_cache.append(_headlines_about(
                    ticker, info, [i["title"] for i in self._company_news(ticker, stock)]))
            return _headlines_cache[0]
        # [INSTITUTIONAL CATALYST ENGINE] Professional 4-tier catalyst detection
        # Tier 1: SEC EDGAR, News Wires, FDA | Tier 2: NLP Entity Extraction
        # Tier 3: Multi-Source Validation | Tier 4: Signal Generation

        # Priority 1: Use Institutional Catalyst Engine (professional-grade)
        if HAS_INSTITUTIONAL_CATALYST and InstitutionalCatalystEngine:
            try:
                engine = InstitutionalCatalystEngine()

                # Get news headlines from yfinance
                headlines = _headlines()

                # Run full 4-tier institutional analysis
                institutional_signals = engine.analyze(
                    ticker=ticker,
                    current_price=current_price,
                    market_cap=market_cap,
                    revenue=info.get('totalRevenue', 0) or 0,
                    current_volume=hist['Volume'].iloc[-1] if len(hist) > 0 else 0,
                    avg_volume=avg_volume,
                    sector=info.get('sector', ''),
                    headlines=headlines
                )
                
                if institutional_signals:
                    top_signal = institutional_signals[0]

                    # Direction matters: the engine emits AVOID for negative
                    # news, and the old boost was confidence x 2 regardless —
                    # "contract terminated, shares plunge" scored +1.9. Bearish
                    # catalysts now subtract; bullish ones get a modest, capped
                    # tilt (headline NLP is the least reliable input here).
                    def _conf(action: str) -> float:
                        return max(
                            (float(getattr(sig, 'confidence', 0.0) or 0.0)
                             for sig in institutional_signals
                             if str(getattr(sig, 'action', '')).upper() == action),
                            default=0.0,
                        )
                    bear, bull = _conf('AVOID'), _conf('BUY')
                    if bear >= bull and bear > 0:
                        catalyst_boost = -min(1.0, bear)
                    elif bull > 0:
                        catalyst_boost = min(0.5, bull * 0.5)
                    else:
                        catalyst_boost = 0.0

                    # Log with institutional-grade detail
                    logger.info(
                        f"🏛️ {ticker}: INSTITUTIONAL CATALYST - {top_signal.catalyst_type} "
                        f"({top_signal.action}) Confidence: {top_signal.confidence:.0%}"
                    )
                done = True

            except Exception as e:
                logger.warning(f"{ticker}: institutional catalyst engine failed, falling back - {e}")

        # Priority 2: Fallback to CatalystAwareSignalGenerator
        if not done and HAS_CATALYST and CatalystAwareSignalGenerator:
            try:
                generator = CatalystAwareSignalGenerator()
                headlines = _headlines()
                # [P2 FIX] Use today's volume vs 20-day avg, not avg/avg which is always ~1.0
                volume_ratio = float(hist['Volume'].iloc[-1]) / (avg_volume + 1) if avg_volume > 0 else 1.0
                
                catalyst_signal, catalyst_boost = generator.generate_catalyst_signal(
                    ticker=ticker,
                    headlines=headlines,
                    volume_ratio=volume_ratio,
                    current_price=current_price,
                    include_sec=not is_indian_stock,
                    include_polygon=True
                )
                
                catalyst_boost = max(-1.0, min(0.5, float(catalyst_boost or 0.0)))
                if catalyst_signal and catalyst_boost:
                    logger.info(f"📰 {ticker}: Catalyst detected ({catalyst_boost:+.1f} pts)")
                done = True
            except Exception as e:
                logger.warning(f"{ticker}: catalyst signal generator failed, falling back - {e}")

        # Priority 3: Basic scanner fallback
        if not done and HAS_CATALYST and CatalystScanner:
            try:
                scanner = CatalystScanner()
                headlines = _headlines()
                
                if headlines:
                    catalysts = scanner.scan_headlines(ticker, headlines)
                    if catalysts:
                        volume_ratio = float(hist['Volume'].iloc[-1]) / (avg_volume + 1)
                        catalyst_signal = scanner.generate_signal(ticker, catalysts, volume_ratio)
                        
                        weight = max(0.0, float(getattr(catalyst_signal, 'aggregate_weight', 0.0) or 0.0))
                        action = str(getattr(catalyst_signal, 'recommended_action', '') or '')
                        if action == "BUY":
                            catalyst_boost = min(0.5, weight * 2.5)
                        elif action == "AVOID":
                            # Bearish headlines used to be ignored here: the
                            # basic tier could only ever add points.
                            catalyst_boost = -min(1.0, weight * 2.5)
                        if catalyst_boost:
                            logger.info(f"📰 {ticker}: Basic catalyst detected ({catalyst_boost:+.1f} pts)")
            except Exception as e:
                logger.debug(f"{ticker}: Basic catalyst scan failed - {e}")
        
        return catalyst_boost, institutional_signals

    def _apply_investment_committee_policy(self, signal: str, score: float,
                                           entry: float, target: float, stop: float,
                                           risk_reward: float, risk_grade: str,
                                           analyst_target: float = 0.0,
                                           rsi: float = 50.0,
                                           insider_sentiment: str = "UNKNOWN",
                                           insider_note: str = "",
                                           insider_relax_strength: float = 0.0) -> Tuple[str, List[str]]:
        """Final new-money policy used by the scanner before a pick can be published.

        [B-82] Bullish insider buying relaxes the hurdles in proportion to
        ``insider_relax_strength`` (0-1, from the insider read). At 1 the
        hurdles equal the old full bullish relaxation; at 0 they are the base
        hurdles. A read with no strength relaxes nothing."""
        notes: List[str] = []
        actionable = {
            SignalType.STRONG_BUY.value,
            SignalType.BUY.value,
            SignalType.ACCUMULATE.value,
        }

        if signal not in actionable:
            return signal, notes

        def downgrade(reason: str) -> str:
            notes.append(reason)
            return SignalType.HOLD.value

        upside_pct = safe_divide(target - entry, entry, 0.0) * 100 if entry > 0 else 0.0
        analyst_upside_pct = safe_divide(float(analyst_target or 0.0) - entry, entry, 0.0) * 100 if entry > 0 and analyst_target else None

        bullish_insider = insider_sentiment == "BULLISH"
        bearish_insider = insider_sentiment == "BEARISH"

        # HARD OVERRIDE: heavy insider selling forces HOLD regardless of score
        if bearish_insider and "Heavy" in (insider_note or ""):
            return downgrade(f"Insider override: {insider_note}"), notes

        # Bullish insider buying relaxes the hurdles, scaled by its strength.
        # Every hurdle comes from engine.thresholds (COMMITTEE / RR / SCORE / INSIDER).
        C = _T.COMMITTEE
        try:
            relax = min(1.0, max(0.0, float(insider_relax_strength or 0.0))) if bullish_insider else 0.0
        except (TypeError, ValueError):
            relax = 0.0
        upside_hurdle = C.UPSIDE_HURDLE_PCT - (C.UPSIDE_HURDLE_PCT - C.UPSIDE_HURDLE_BULLISH_INSIDER_PCT) * relax
        rr_hurdle = max(_T.RR.COMMITTEE_FLOOR,
                        TradingConfig.RISK_REWARD_MIN - _T.RR.COMMITTEE_INSIDER_RELAX * relax)
        score_floor = TradingConfig.ACCUMULATE_SCORE - C.SCORE_FLOOR_BULLISH_RELAX * relax
        analyst_limit = (C.ANALYST_DOWNSIDE_LIMIT_PCT
                         + (C.ANALYST_DOWNSIDE_LIMIT_BULLISH_PCT - C.ANALYST_DOWNSIDE_LIMIT_PCT) * relax)
        skip_rsi_veto = bullish_insider and relax >= float(_T.INSIDER.RSI_VETO_SKIP_MIN_STRENGTH)
        if bullish_insider:
            notes.append(f"Insider buying relaxes the hurdles at {relax:.0%} of full strength")

        if score < score_floor:
            return downgrade(
                f"Score {score * _T.SCORE.TO_100:.0f}/100 is below the {score_floor * _T.SCORE.TO_100:.0f}/100 new-money floor"
            ), notes

        if signal in [SignalType.STRONG_BUY.value, SignalType.BUY.value] and score < TradingConfig.GOOD_SCORE - C.BUY_TIER_BULLISH_RELAX * relax:
            return downgrade(
                f"Score {score * _T.SCORE.TO_100:.0f}/100 is below the BUY-tier threshold"
            ), notes

        if str(risk_grade).upper() == "F":
            return downgrade(f"Risk Grade F blocks new-money recommendations"), notes
        # Risk Grade D is elevated, NOT disqualifying. Mirror the analyzer's
        # graduated guardrail (TradingAnalysis_ELITE): trim conviction one notch
        # (STRONG_BUY/BUY → ACCUMULATE) so a strong name stays actionable at
        # reduced size instead of being blanket-forced to HOLD. ACCUMULATE
        # still has to clear the upside / R-R hurdles below.
        if str(risk_grade).upper() == "D" and signal in [SignalType.STRONG_BUY.value, SignalType.BUY.value]:
            notes.append("Risk Grade D trims conviction → ACCUMULATE (reduced size)")
            signal = SignalType.ACCUMULATE.value

        if entry <= 0 or target <= 0 or stop <= 0:
            return downgrade("Entry, target, or stop loss is missing"), notes

        if stop >= entry:
            return downgrade("Stop loss is not below entry"), notes

        if upside_pct < upside_hurdle:
            return downgrade(f"Target upside {upside_pct:.1f}% is below the {upside_hurdle:.0f}% new-money hurdle"), notes

        if risk_reward < rr_hurdle:
            return downgrade(
                f"Risk/reward {_T.fmt_rr(risk_reward)} is below the {_T.fmt_rr(rr_hurdle)} minimum"
            ), notes

        if analyst_upside_pct is not None and analyst_upside_pct < analyst_limit:
            return downgrade(f"Analyst consensus target implies {analyst_upside_pct:.1f}% downside"), notes

        if rsi > C.RSI_EXTENDED and signal in [SignalType.STRONG_BUY.value, SignalType.BUY.value] and not skip_rsi_veto:
            return downgrade(f"RSI {rsi:.0f} is extended; wait for a cleaner entry"), notes

        notes.append(
            f"Passed new-money policy: score {score * _T.SCORE.TO_100:.0f}/100, upside {upside_pct:.1f}%, R/R {_T.fmt_rr(risk_reward)}, risk {risk_grade}"
        )
        if insider_note:
            notes.append(f"Insider read: {insider_note}")
        return signal, notes

    def _calculate_position_size(self, signal: str, score: float,
                                 entry: float, stop: float,
                                 volatility: float = 0.0) -> float:
        """Size the position from its stop distance, honouring the risk budget.

        [B4 FIX] ``MAX_RISK_PER_TRADE`` (2%) and ``MAX_RISK_STRONG_BUY`` (2.5%)
        were referenced in exactly one place — ``validate_config``, which only
        checked they were in range. Nothing ever tied share count to
        ``entry - stop``, so per-trade dollar risk was an accident of whatever
        stop the target function happened to produce.

        The rule is now the standard one:

            allocation x (stop distance %) = risk budget

        so dollar risk at the stop is the budget by construction. Because the
        stop is ATR-derived, this *is* volatility targeting — a higher-vol name
        gets a wider stop and therefore fewer shares, automatically. The
        separate ``TARGET_POSITION_VOL / vol`` weighting the old code applied
        was not only redundant but inert: ``0.04 / vol`` exceeds the 10%
        position cap for any name under ~27% annualized vol, so nearly every
        position pinned to the cap regardless of its risk.
        """
        # HOLD and AVOID get zero allocation
        if signal in [SignalType.HOLD.value, SignalType.AVOID.value, None]:
            return 0.0

        try:
            entry = float(entry)
            stop = float(stop)
        except (TypeError, ValueError):
            return 0.0

        if not (np.isfinite(entry) and np.isfinite(stop)) or entry <= 0 or stop <= 0:
            return 0.0

        risk_per_share = entry - stop
        if risk_per_share <= 0:
            logger.debug(
                f"Stop {stop:.2f} is not below entry {entry:.2f}; cannot size from risk"
            )
            return 0.0
        stop_distance_pct = risk_per_share / entry

        # Drawdown-based exposure throttle (institutional risk control)
        drawdown_mult = self._drawdown_size_multiplier()
        if drawdown_mult <= 0:
            return 0.0

        is_strong = signal == SignalType.STRONG_BUY.value

        # Target risk for THIS trade, as a fraction of capital.
        target_risk_pct = (
            TradingConfig.TARGET_RISK_STRONG_BUY if is_strong
            else TradingConfig.TARGET_RISK_PER_TRADE
        )
        signal_mult = _T.SIZING.SIGNAL_SIZE_MULT.get(signal, _T.SIZING.OTHER_SIGNAL_SIZE_MULT)

        # Smooth conviction scaling on top of the discrete signal tier.
        score_mult = _T.SIZING.SCORE_MULT_BASE + (
            max(0.0, min(float(score or 0.0), 10.0)) / 10.0) * _T.SIZING.SCORE_MULT_SPAN

        risk_budget = self.capital * target_risk_pct * signal_mult * score_mult * drawdown_mult

        # Hard ceiling: dollar risk may never exceed the MAX_* policy, whatever
        # the target and multipliers work out to.
        max_risk_pct = (
            TradingConfig.MAX_RISK_STRONG_BUY if is_strong
            else TradingConfig.MAX_RISK_PER_TRADE
        )
        risk_ceiling = self.capital * max_risk_pct * drawdown_mult
        risk_budget = min(risk_budget, risk_ceiling)

        # The sizing identity: allocation x stop_distance = risk_budget.
        allocation = risk_budget / stop_distance_pct

        # Cap by the capital-aware per-position limit. When the cap binds, the
        # trade simply carries LESS than its risk budget — never more.
        # The drawdown throttle scales the cap too: when the cap binds (most
        # low-vol names), scaling only the risk budget left the size unchanged
        # at 0.5x/0.75x — "10% drawdown -> half exposure" never happened.
        effective_cap = TradingConfig.effective_max_position_pct(self.capital)
        max_position = self.capital * effective_cap * drawdown_mult
        if allocation > max_position:
            logger.debug(
                f"Position cap bound before the risk budget: "
                f"${allocation:,.0f} → ${max_position:,.0f} "
                f"({effective_cap:.0%} of capital); realised risk "
                f"{max_position * stop_distance_pct / self.capital:.2%} of capital"
            )
            allocation = max_position

        min_position = TradingConfig.min_position_size(self.capital) * drawdown_mult
        if max_position < min_position or allocation < min_position:
            logger.debug(
                f"Risk-sized allocation ${allocation:.2f} (cap ${max_position:.2f}) "
                f"is below the minimum trade size ${min_position:.2f}; no trade"
            )
            return 0.0

        return round(allocation, 2)


    def _is_chasing_momentum(self, hist: pd.DataFrame, ticker: str) -> Tuple[bool, str]:
        """Chase prevention: (flagged, reason). See ``_chase_trigger``."""
        trig = self._chase_trigger(hist, ticker)
        return (True, trig["reason"]) if trig and trig.get("active", True) else (False, "")

    def _chase_trigger(self, hist: pd.DataFrame, ticker: str) -> Optional[Dict[str, Any]]:
        """[A07] The first chase rule that fires, or None.

        Rules (``engine.thresholds.CHASE``; a stock whose own volatility reaches
        ``WIDE_THRESHOLD_MIN_VOL`` uses the GROWTH_ values, in any scan type),
        each strictly above its threshold: an opening gap over the prior close on
        one of the last ``GAP_LOOKBACK_BARS`` bars, then a 5-session and a
        10-session close-to-close run. A gap can fire inside a long decline, so
        it is reported as a gap, not as an extended trend. Dates are the bars'
        own session dates, never "today".

        [I6] A gap whose latest close is back at or below the pre-gap close
        (x 1 + ``GAP_FILL_TOLERANCE``) has filled: it is returned with
        ``active`` False and ``state`` "gap_filled" (informational, no penalty)
        unless another rule fires. A partly filled gap carries its fill share
        and the fade since the gap-day close; a run carries how far the latest
        close sits below the window's peak.
        """
        C = _T.CHASE
        try:
            if hist is None or len(hist) < C.MIN_BARS:
                return None
            closes = hist['Close']
            opens = hist['Open']
            # [P6] Per-stock, never per-universe: the stock's own realised
            # volatility picks the wide thresholds, and only when config enables it.
            vol = None
            rets = np.log(closes.astype(float)).diff().dropna().tail(int(C.VOL_LOOKBACK_BARS))
            if len(rets) >= 20:
                vol = float(rets.std() * np.sqrt(252))
            growth = bool(C.WIDE_THRESHOLD_MIN_VOL and vol is not None and vol >= float(C.WIDE_THRESHOLD_MIN_VOL))
            gap_t = C.GROWTH_GAP_PCT if growth else C.GAP_PCT
            run5_t = C.GROWTH_MOVE_5D_PCT if growth else C.MOVE_5D_PCT
            run10_t = C.GROWTH_MOVE_10D_PCT if growth else C.MOVE_10D_PCT

            def _day(i: int) -> str:
                ts = pd.Timestamp(hist.index[i])
                return ts.date().isoformat()

            session = _day(-1)

            def _hit(trigger: str, move: float, threshold: float, bar: int, start: Optional[int],
                     what: str, **extra: Any) -> Dict[str, Any]:
                out = {
                    'trigger': trigger, 'move_pct': round(move * 100.0, 4),
                    'threshold_pct': round(threshold * 100.0, 4), 'bar_date': _day(bar),
                    'from_date': _day(start) if start is not None else None,
                    'session_date': session, 'wide_thresholds': growth, 'active': True,
                    'volatility': round(vol, 4) if vol is not None else None,
                    'reason': (f"Chase alert: {ticker} {what} (threshold {threshold * 100:.0f}%). "
                               f"Wait for a pullback or consolidation."),
                }
                out.update(extra)
                return out

            def _pct(x: float) -> float:
                return round(float(x) * 100.0, 4)

            last = closes.iloc[-1]
            filled: Optional[Dict[str, Any]] = None
            for back in range(1, int(C.GAP_LOOKBACK_BARS) + 1):
                if len(hist) < back + 1:
                    break
                prev_close = closes.iloc[-back - 1]
                gap_open = opens.iloc[-back]
                gap = safe_divide(gap_open - prev_close, prev_close, 0)
                if not gap > gap_t:
                    continue
                gap_close = closes.iloc[-back]
                what = f"opened with a {gap * 100:.1f}% gap above the prior close on {_day(-back)}"
                if pd.notna(last) and prev_close > 0 and last <= prev_close * (1 + float(C.GAP_FILL_TOLERANCE)):
                    # [I6] Back at or below the pre-gap close: the gap has filled.
                    filled = filled or _hit(
                        'gap', gap, gap_t, -back, None, what, active=False, state='gap_filled',
                        close_vs_pre_gap_pct=_pct(last / prev_close - 1.0),
                        fade_since_gap_close_pct=_pct(last / gap_close - 1.0) if gap_close > 0 else None,
                        gap_fill_pct=100.0)
                    filled['reason'] = (f"Gap filled: {ticker} {what}, and closed {last:.2f} on {session}, "
                                        f"at or below the pre-gap close {prev_close:.2f}. Informational, "
                                        f"not a chase alert.")
                    continue
                span = gap_open - prev_close
                fill = (max(0.0, min(1.0, float((gap_open - last) / span))) if span > 0 and pd.notna(last) else None)
                fade = float(last / gap_close - 1.0) if gap_close > 0 and pd.notna(last) else None
                partly = bool(fill and fill > 0)
                hit = _hit('gap', gap, gap_t, -back, None, what,
                           state='gap_partly_filled' if partly else 'gap_open',
                           gap_fill_pct=round(fill * 100.0, 2) if fill is not None else None,
                           fade_since_gap_close_pct=_pct(fade) if fade is not None else None)
                if partly:
                    hit['reason'] = (f"Chase alert: {ticker} {what} (threshold {gap_t * 100:.0f}%), "
                                     f"partly filled: {fill * 100:.0f}% of the gap given back, "
                                     f"{fade * 100:+.1f}% since the gap-day close. "
                                     f"Wait for a pullback or consolidation.")
                return hit
            for days, trigger, threshold in ((5, 'run_5d', run5_t), (10, 'run_10d', run10_t)):
                if len(closes) >= days + 1:
                    move = safe_divide(closes.iloc[-1] - closes.iloc[-days - 1], closes.iloc[-days - 1], 0)
                    if move > threshold:
                        window = closes.iloc[-days - 1:]
                        peak_at = int(np.nanargmax(window.to_numpy()))
                        peak = float(window.iloc[peak_at])
                        return _hit(trigger, move, threshold, -1, -days - 1,
                                    f"rose {move * 100:.1f}% in {days} sessions, "
                                    f"{_day(-days - 1)} to {session}",
                                    off_peak_pct=_pct(last / peak - 1.0) if peak > 0 else None,
                                    peak_date=_day(-days - 1 + peak_at))
            return filled
        except Exception as e:
            logger.debug(f"Chase detection error for {ticker}: {e}")
            return None

    def _get_event_risk_factor(self) -> Tuple[float, str]:
        """
        **v6.0 PROPHET FIX #7.5: Event Calendar Risk Adjustment**
        
        Fed week = smaller positions. Returns (multiplier, reason).
        """
        from datetime import datetime
        from engine.market_calendar import now_new_york

        # FOMC dates are New York dates. The server's local date used to be
        # used, so an evening scan in Asia ran a day ahead (audit #19).
        today = now_new_york().date()

        # [M11 FIX] A hardcoded calendar silently expires. Once `today` passed
        # the last known date this function returned 1.0 forever with no
        # warning — a risk control that stops firing while still being
        # believed. Extend FOMC_DATES_* from the Fed's published calendar.
        all_dates = sorted(FOMC_DATES_2025 + FOMC_DATES_2026 + FOMC_DATES_2027)
        try:
            last_known = datetime.strptime(all_dates[-1], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            last_known = today

        if today > last_known:
            logger.warning(
                f"⚠️ FOMC calendar is EXHAUSTED (last known meeting "
                f"{last_known.isoformat()}). Event-risk sizing is INACTIVE until "
                f"the calendar is extended — update FOMC_DATES_* or move it to a "
                f"data file."
            )
            return 1.0, ""

        for fomc_date_str in all_dates:
            try:
                fomc_date = datetime.strptime(fomc_date_str, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"Malformed FOMC date in calendar: {fomc_date_str!r}")
                continue
            days_until = (fomc_date - today).days

            if 0 <= days_until <= 3:
                return 0.70, f"⚠️ FOMC in {days_until} days - positions reduced 30%"
            elif 4 <= days_until <= 7:
                return 0.85, f"⚠️ FOMC in {days_until} days - positions reduced 15%"

        return 1.0, ""


    def _calculate_price_targets(self, price: float, indicators: TechnicalIndicators,
                                 risk_metrics: RiskMetrics, analyst_target: float = 0.0) -> Tuple[float, float, float]:
        """Derive entry, target and stop from two independent, coherent methods.

        [B6 FIX] The previous implementation inflated risk/reward on both sides
        at once. The stop was ``max()`` of three candidates, which selects the
        *tightest* (so the volatility and support stops almost never bound).
        The target was ``max()`` of four candidates, which selects the *most
        optimistic*, then blended 75% to the sell-side mean target — and only
        when that target sat ABOVE spot, so bearish analyst views were silently
        discarded. When R:R still fell short, the target was simply pushed out
        to ``entry + risk * 1.8``, making the R:R gate unfailable. A $100 name
        at 30% vol produced stop $96.40 / target $130.00 — 8.3:1.

        The two sides now come from genuinely different information, which is
        what makes R:R carry a signal at all:

          * **Stop** — volatility. Where the thesis is wrong, at
            ``STOP_ATR_MULTIPLIER`` ATRs, extended to clear nearby structural
            support, with the distance clamped to a sane band.
          * **Target** — structure and consensus, averaged (never maximised),
            and used *symmetrically*: an analyst target below spot pulls the
            target down and lets the name fail the R:R gate honestly.

        Both ATR multiples come from config, so R:R is a property of the setup
        rather than an artefact of mixing methods.
        """
        entry = round(price, 2)
        atr = float(indicators.atr or 0.0)
        if atr <= 0:
            # No usable ATR: fall back to a volatility-implied daily range so
            # the stop is still risk-derived rather than an arbitrary percent.
            daily_vol = max(float(risk_metrics.volatility or 0.0), 0.10) / math.sqrt(252)
            atr = max(price * daily_vol, price * 0.005)

        # ── STOP: volatility, extended to clear structure ──────────────────
        stop_loss = price - atr * TradingConfig.STOP_ATR_MULTIPLIER

        # If recent structural support sits just below the volatility stop,
        # place the stop under the level instead. Same method, extended to
        # respect the level — not a competing candidate that can tighten it.
        support = float(indicators.support or 0.0)
        if 0 < support < stop_loss and support >= stop_loss - atr:
            stop_loss = support * 0.99

        # Clamp the stop DISTANCE to a sane band. The floor stops a
        # near-zero-ATR name getting an untradeable stop; the ceiling stops a
        # 60%-vol name getting a 40% stop that no sizing can make sensible.
        min_stop_distance = price * TradingConfig.MIN_STOP_DISTANCE_PCT
        max_stop_distance = price * TradingConfig.MAX_STOP_DISTANCE_PCT
        distance = price - stop_loss
        distance = max(min_stop_distance, min(distance, max_stop_distance))
        stop_loss = round(price - distance, 2)

        # ── TARGET: structure + consensus, averaged and symmetric ──────────
        target_candidates: List[float] = []

        # Structural: the next meaningful level up. The 20-bar high only
        # qualifies when it is genuinely above spot (for a name at new highs
        # it equals spot and carries no information).
        _above = 1.0 + _T.LEVELS.STRUCT_MIN_ABOVE_PCT
        resistance = float(indicators.resistance or 0.0)
        if resistance > price * _above:
            target_candidates.append(resistance)

        # Structural: the 52-week high as a measured-move anchor -- unless it is
        # older than the horizon. A high set before a cash deal was signed would
        # otherwise keep a target above the deal price alive (audit 5.1).
        high_52w = float(getattr(indicators, 'high_52w', 0.0) or 0.0)
        _age = getattr(indicators, 'high_52w_age_sessions', None)
        _stale = _age is not None and _age > _T.LEVELS.TARGET_STRUCT_MAX_AGE_SESSIONS
        if high_52w > price * _above and not _stale:
            target_candidates.append(high_52w)

        # Consensus, used SYMMETRICALLY. The old guard `analyst_target > price
        # * 1.02` meant only bullish targets were ever admitted, baking
        # sell-side optimism into every reward figure. A target below spot is
        # information too, and should be allowed to sink the trade.
        try:
            at = float(analyst_target or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        # A name at new highs has no structure above it, which used to leave
        # the lagging sell-side mean as the ONLY input — so the strongest
        # trends (the ones the 35% momentum weight favours) were judged on a
        # consensus number that trails price, and the top three scores in a
        # live bull-tape scan all read R:R < 0.5. With no overhead level, the
        # measured-move ATR projection IS the structural estimate; average it
        # with consensus exactly as a level would be.
        has_structure = bool(target_candidates)
        _lo, _hi = _T.LEVELS.ANALYST_TARGET_SANE_RANGE
        if price * _lo < at < price * _hi:
            # Consensus is a 12-month number, so this pre-filter R:R is
            # optimistic. The PUBLISHED levels are the Research engine's, whose
            # target is pro-rated to its horizon and must clear RR.RESEARCH_MIN
            # before a pick is funded (_deep_confirm_final_picks, audit
            # 2026-10-17 #5). Pro-rating here too empties the book (1 of 50
            # large caps clears 1.8) and is an owner funding decision.
            target_candidates.append(at)
            if not has_structure:
                target_candidates.append(price + atr * TradingConfig.TARGET_ATR_MULTIPLIER)

        if target_candidates:
            # Average, never maximise. One optimistic candidate can no longer
            # set the whole reward side of the trade.
            raw_target = float(np.mean(target_candidates))
        else:
            # No structure above and no coverage: project the move at the
            # configured ATR multiple. Explicitly a fallback, not a preference.
            raw_target = price + atr * TradingConfig.TARGET_ATR_MULTIPLIER

        # Safety cap as a circuit breaker on absurd inputs, not a target.
        _caps = _T.LEVELS.TARGET_SAFETY_CAP
        safety_cap = price * _caps.get(self.market_regime.regime_type, _caps["OTHER"])
        target = min(raw_target, safety_cap)

        # [B6 FIX] The R:R floor is NOT enforced here. It used to be, by
        # rewriting the target to `entry + risk * RISK_REWARD_MIN`, which meant
        # the downstream `risk_reward < RISK_REWARD_MIN` gate could never
        # legitimately reject anything. The gate in analyze_stock now sees the
        # real ratio and is free to fail the name.

        target = round(target, 2)

        # [O13 FIX] This used to return a fourth value,
        # `entry + (target - entry) * 0.40`, labelled "trailing stop" — a level
        # ABOVE the entry price, i.e. a profit-taking trigger, not a stop. It
        # was the third mutually inconsistent trailing-stop concept in the
        # engine. There is now exactly one: the ATR trail computed by
        # RiskLimits.calculate_atr_trailing_stop.
        return entry, target, stop_loss

    def _calculate_price_scenarios(self, price: float, indicators: TechnicalIndicators,
                                    risk_metrics: RiskMetrics, info: Dict) -> PriceScenarios:
        """Bear / base / bull price scenarios and their probability-weighted value.

        [B7 FIX] The scenario spread used to be hardcoded asymmetric — the bull
        leg ran at +1.5 sigma while the bear leg ran at only -0.75 sigma, half
        the multiple — and three ordering clamps then floored the base at
        1.05x spot and the bull at 1.155x. The result was an "expected value"
        that was mechanically positive for every stock in the universe and that
        *rose with volatility*: +5.8% at 15% vol, +12.4% at 50%. A field named
        expected value that rewards risk and can never be negative is worse
        than no field at all.

        The sigma multiple is now symmetric, so a name can genuinely carry
        negative expected value, and ordering is enforced by sorting the three
        legs rather than by flooring two of them above spot.
        """
        scenarios = PriceScenarios()

        try:
            vol = max(float(risk_metrics.volatility or 0.0), 0.05)

            # Analyst targets, when covered
            analyst_target = _clean_float(info.get('targetMeanPrice'))
            analyst_low = _clean_float(info.get('targetLowPrice'))
            analyst_high = _clean_float(info.get('targetHighPrice'))

            # Regime scales the horizon, and therefore BOTH tails equally.
            horizon_mult = 1.0
            if self.market_regime.regime_type == "BULL":
                horizon_mult = 1.2
            elif self.market_regime.regime_type == "BEAR":
                horizon_mult = 0.8

            # Symmetric sigma band around spot. Same multiple up and down.
            sigma_move = vol * TradingConfig.SCENARIO_SIGMA_MULTIPLIER * horizon_mult

            # === BULL (upper case) ===
            if analyst_high > 0:
                scenarios.bull_target = analyst_high
            else:
                scenarios.bull_target = price * (1 + sigma_move)

            # === BASE (central case) ===
            # With no coverage the honest central case is "unchanged", not a
            # forced +3 ATR advance.
            scenarios.base_target = analyst_target if analyst_target > 0 else price

            # === BEAR (lower case) ===
            # The old guard `analyst_low < price` discarded the analyst low
            # whenever the whole range sat above spot, substituting a
            # volatility bear case instead — another one-way filter.
            if analyst_low > 0:
                scenarios.bear_target = analyst_low
            else:
                scenarios.bear_target = price * (1 - sigma_move)

            # Enforce ordering by SORTING, not by flooring legs above spot.
            # If the inputs imply bear > base, the inputs are what change.
            ordered = sorted([
                scenarios.bear_target, scenarios.base_target, scenarios.bull_target
            ])
            scenarios.bear_target = round(ordered[0], 2)
            scenarios.base_target = round(ordered[1], 2)
            scenarios.bull_target = round(ordered[2], 2)

            scenarios.calculate_expected()

        except Exception as e:
            logger.debug(f"Price scenario calculation error: {e}")
            # Symmetric fallback — no built-in upward drift.
            scenarios.bear_target = round(price * 0.85, 2)
            scenarios.base_target = round(price, 2)
            scenarios.bull_target = round(price * 1.15, 2)
            scenarios.calculate_expected()

        return scenarios

    def _bulk_history_for(self, ticker: str) -> Optional[pd.DataFrame]:
        """The prefetched daily frame for `ticker`, or None to fetch it live."""
        store = getattr(self, '_bulk_history', None)
        if not store:
            return None
        frame = store.get(ticker)
        if frame is None or frame.empty:
            return None
        return frame.copy(deep=False)

    def prefetch_history(self, tickers: List[str]) -> int:
        """Bulk-download the daily history the scan needs, in chunks.

        `analyze_stock` opens with one `Ticker.history()` per name. Across a
        669-name universe that is 669 separate requests and, once the SEC stall
        that used to pace them was removed, the largest single contributor to
        provider rate limiting. yfinance can serve the same bars in a handful
        of batched calls.

        Best-effort: any ticker missing from the result simply falls back to
        its own request, so a partial or failed prefetch costs nothing but the
        time it took.
        """
        self._bulk_history: Dict[str, pd.DataFrame] = {}
        symbols = [t for t in dict.fromkeys(tickers) if t]
        if PROVIDER_STATE.in_cooldown():
            return 0
        if len(symbols) < TradingConfig.PREFETCH_MIN_UNIVERSE:
            return 0
        if getattr(self, '_bulk_timed_out', False):
            # [A01] A bulk call already timed out in this scan: a second bulk
            # stage would wait on the same slow provider. Fetch per name.
            logger.info("Bulk history prefetch skipped: an earlier bulk call timed out in this scan.")
            return 0

        chunk_size = max(1, TradingConfig.PREFETCH_CHUNK_SIZE)
        budget = TradingConfig.PREFETCH_TIMEOUT_SECONDS
        started = time.time()
        logger.info(
            f"📦 Bulk history prefetch: {len(symbols)} names in "
            f"{(len(symbols) + chunk_size - 1) // chunk_size} chunk(s), "
            f"budget {budget:.0f}s"
        )

        for offset in range(0, len(symbols), chunk_size):
            if PROVIDER_STATE.in_cooldown():
                break
            if time.time() - started >= budget:
                logger.warning(
                    "Bulk history prefetch exceeded its %.0fs budget; the "
                    "remaining names fall back to per-ticker fetches.", budget)
                break
            chunk = symbols[offset:offset + chunk_size]
            PROVIDER_PACER.wait()
            remaining = budget - (time.time() - started)
            if remaining <= 0:
                logger.warning(
                    "Bulk history prefetch exceeded its %.0fs budget; the "
                    "remaining names fall back to per-ticker fetches.", budget)
                break

            # [A01] Each chunk runs in an owned child process, killed and reaped
            # when the remaining budget runs out.
            res = self._bounded_bulk_download(chunk, _T.RISK_GRADE.FETCH_PERIOD, remaining)
            if res.status == "timeout":
                logger.warning(
                    "Bulk history prefetch exceeded its %.0fs budget during a "
                    "provider call; remaining names will fetch individually.", budget)
                break
            result: Dict[str, Any] = {'data': res.value} if res.status == "ok" else {'error': RuntimeError(res.error)}

            if 'error' in result:
                e = result['error']
                if _is_rate_limit_error(e):
                    PROVIDER_STATE.note_rate_limit(_extract_retry_after(e))
                    PROVIDER_PACER.note_rate_limit()
                logger.debug("Bulk history chunk failed: %s", e)
                continue
            data = result.get('data')
            if data is None or data.empty:
                continue

            multi = isinstance(data.columns, pd.MultiIndex)
            for symbol in chunk:
                try:
                    if multi:
                        if symbol not in data.columns.get_level_values(0):
                            continue
                        frame = data[symbol]
                    elif len(chunk) == 1:
                        frame = data
                    else:
                        continue
                    frame = frame.dropna(how='all')
                    # Only keep frames deep enough to analyse; anything shorter
                    # would trip the insufficient_data gate on stale bars when
                    # a live fetch might still succeed.
                    if frame.empty or len(frame) < TradingConfig.MIN_HISTORY_DAYS:
                        continue
                    self._bulk_history[symbol] = frame
                except Exception:
                    continue
            PROVIDER_PACER.note_success()

        logger.info(
            f"📦 Prefetched {len(self._bulk_history)}/{len(symbols)} histories "
            f"in {time.time() - started:.1f}s "
            f"({len(symbols) - len(self._bulk_history)} will fetch individually)"
        )
        return len(self._bulk_history)

    def smart_prefilter(self, tickers: List[str], max_candidates: int = 200,
                        screen_style: Optional[str] = None) -> List[str]:
        """
        LIQUIDITY PRE-FILTER: one bulk yfinance download screens the universe
        before expensive per-ticker analysis, reducing ~700 calls to
        `max_candidates`.

        [B1/M2 FIX] This used to apply momentum *hard cuts* (above the 20-day
        SMA, within 25% of the period high) and rank on a sum of a percentage
        change and a percentage-of-high level. That imposed a trend tilt on the
        universe itself — silently deleting every mean-reversion candidate the
        scorer's oversold-bounce path is built to find — and mixed two
        incommensurate units in the ranking key.

        Hard filters are now *eligibility only*: the price and liquidity floors
        `analyze_stock` enforces regardless, so screening them here costs no
        selection breadth. Ranking is by average daily dollar volume, the
        neutral criterion a desk uses to define an investable universe.
        """
        logger.info("🧠 LIQUIDITY PRE-FILTER: Screening universe with bulk download...")
        if max_candidates <= 0 or len(tickers) <= max_candidates:
            return list(tickers)

        try:
            # The bulk download takes at most PREFILTER_MAX_TICKERS names; past
            # that an even spread, never an alphabetical prefix, is downloaded.
            _cap = int(_T.UNIVERSE.PREFILTER_MAX_TICKERS)
            ticker_str = list(tickers) if len(tickers) <= _cap else self._even_sample(tickers, _cap)
            if len(ticker_str) < len(tickers):
                self._note_selection(prefilter_sampled=len(ticker_str))
            budget = TradingConfig.PREFILTER_TIMEOUT_SECONDS
            logger.info(
                "  Downloading bulk data for pre-screening "
                f"({len(tickers)} names, budget {budget:.0f}s)..."
            )
            # [B8][A01] Bound the wall clock in an owned child process: past the
            # budget the download is killed and reaped, then the scan falls back
            # to a deterministic even spread (unbiased, just not liquidity-ranked).
            self._note_selection(method="liquidity_ranked", cause=None, budget_seconds=budget,
                                 candidates=len(tickers))
            # A price-history screen before the cap needs 200+ sessions.
            res = self._bounded_bulk_download(ticker_str, '1y' if screen_style else '6mo', budget)
            _after_cap = "; the uptrend screen runs after the cap instead" if screen_style else ""
            self._note_selection(seconds=round(res.seconds, 2), seconds_measure="price_download")
            if res.status == "timeout":
                logger.warning(
                    f"Pre-filter bulk download exceeded its {budget:.0f}s budget; the "
                    f"download was stopped and the scan uses an even spread across the "
                    f"universe. Set QUANTA_PREFILTER_TIMEOUT to raise the budget."
                )
                self._note_selection(method="even_spread",
                                     cause=f"liquidity download exceeded its {budget:.0f}s budget{_after_cap}")
                return self._even_sample(tickers, max_candidates)
            if res.status == "error":
                raise RuntimeError(res.error)
            data = res.value
            if data is None:
                logger.warning(
                    "Pre-filter data download returned nothing; falling back to an "
                    "even spread across the universe."
                )
                self._note_selection(method="even_spread", cause="liquidity download returned nothing")
                return self._even_sample(tickers, max_candidates)
            logger.info(f"  Bulk pre-screen download finished in {res.seconds:.1f}s")

            if data.empty:
                logger.warning(
                    "Pre-filter data download returned nothing; falling back to an "
                    "even spread across the universe."
                )
                self._note_selection(method="even_spread", cause="liquidity download returned nothing")
                return self._even_sample(tickers, max_candidates)

            # [P2 FIX] Handle both single-ticker (flat columns) and multi-ticker (MultiIndex)
            is_multi = isinstance(data.columns, pd.MultiIndex)
            
            promising = []
            floors_ok = 0
            
            for ticker in tickers:
                try:
                    if is_multi:
                        if ticker not in data.columns.get_level_values(0):
                            continue
                        ticker_data = data[ticker]
                    else:
                        # Single ticker — data is already the ticker's DataFrame
                        if len(tickers) > 1:
                            continue  # Can't distinguish tickers in flat format
                        ticker_data = data
                    if ticker_data.empty or len(ticker_data) < 10:
                        continue
                    
                    close = ticker_data['Close'].dropna()
                    volume = ticker_data['Volume'].dropna()
                    
                    if len(close) < 10:
                        continue
                    
                    current_price = float(close.iloc[-1])
                    avg_volume = float(volume.tail(20).mean())

                    # --- Eligibility only: the floors analyze_stock enforces ---
                    if not np.isfinite(current_price) or not np.isfinite(avg_volume):
                        continue
                    # The listing's own market floors: an NSE/BSE name is priced
                    # in rupees, so the US dollar floors would drop any stock above
                    # Rs 5,000 and most mid-caps (UNIVERSE.INDIA_* as analyze_stock).
                    if str(ticker).upper().endswith(('.NS', '.BO')):
                        lo, hi = _T.UNIVERSE.INDIA_MIN_PRICE, _T.UNIVERSE.INDIA_MAX_PRICE
                        min_vol, min_value = _T.UNIVERSE.INDIA_MIN_AVG_VOLUME, _T.UNIVERSE.INDIA_MIN_DAILY_VALUE
                    else:
                        lo, hi = self._us_floor("MIN_PRICE"), TradingConfig.MAX_PRICE
                        min_vol, min_value = self._us_floor("MIN_AVG_VOLUME"), self._us_floor("MIN_DAILY_DOLLAR_VOLUME")
                    if not (lo <= current_price <= hi):
                        continue
                    if avg_volume < min_vol:
                        continue
                    dollar_volume = avg_volume * current_price
                    if dollar_volume < min_value:
                        continue

                    # --- Neutral ranking key: average daily dollar volume ---
                    floors_ok += 1
                    if screen_style:
                        verdict = style_screen(screen_style, None, closes=close.tolist())
                        if not verdict.get("passed"):
                            continue
                    promising.append((ticker, dollar_volume))

                except Exception:
                    continue

            # Most liquid first. Ties broken by symbol so the result is
            # deterministic run-to-run.
            promising.sort(key=lambda x: (-x[1], x[0]))
            filtered_tickers = [t[0] for t in promising[:max_candidates]]
            self._note_selection(passed_floors=floors_ok, rank_by="20-day average daily traded value")
            if screen_style:
                self._note_selection(screened_before_cap=screen_style, passed_screen=len(promising))
                if floors_ok and not promising:
                    logger.warning(f"No name passed the {screen_style} screen; the scan has nothing to analyse.")
                    return []

            if not filtered_tickers:
                logger.warning(
                    "Pre-filter eliminated every name; falling back to an even "
                    "spread across the universe."
                )
                self._note_selection(method="even_spread", cause="no name passed the liquidity floors")
                return self._even_sample(tickers, max_candidates)

            logger.info(
                f"✓ Liquidity pre-filter: {len(tickers)} names → {len(filtered_tickers)} "
                f"most-liquid candidates ({len(promising)} passed the eligibility floors)"
            )
            return filtered_tickers

        except Exception as e:
            logger.warning(
                f"Pre-filter failed ({e}); falling back to an even spread across "
                f"the universe (never an alphabetical prefix)."
            )
            self._note_selection(method="even_spread", cause=f"liquidity download failed: {str(e)[:160]}")
            return self._even_sample(tickers, max_candidates)

    def evaluate_holding(self, ticker: str,
                         entry_score: Optional[float] = None,
                         entry_price: Optional[float] = None,
                         entry_stop: Optional[float] = None) -> Optional[SellEvaluation]:
        """Re-analyse an existing holding and apply SELL/TRIM deterioration triggers.

        Intended for a daily/periodic exit-monitoring pass over current
        positions — the offensive scanner finds entries, this guards exits.
        Returns None only if the ticker can no longer be analysed (itself a
        data-quality red flag the caller should surface).
        """
        # research_mode: a holding that now FAILS the entry gates is exactly
        # the one to sell. Scan mode returned None for it ("could not
        # re-analyse"), so the degraded-signal exit could never fire.
        analysis = self.analyze_stock(ticker, research_mode=True)
        if analysis is None:
            logger.warning(f"{ticker}: could not re-analyse holding for exit review")
            return None
        evaluation = evaluate_sell_triggers(
            analysis, entry_score=entry_score, entry_price=entry_price, entry_stop=entry_stop
        )
        if evaluation.action != "HOLD":
            logger.info(
                f"🔻 {ticker}: {evaluation.action} ({evaluation.urgency}) — "
                f"{'; '.join(evaluation.reasons)}"
            )
        return evaluation

    def scan_market(self, scan_type: str = "standard", custom_tickers: Optional[List[str]] = None,
                    safe_mode: bool = False) -> List[StockAnalysis]:
        """
        Execute comprehensive market scan with parallel processing
        
        **v6.0 FIX #1.4: Memory leak prevention and circuit breaker**
        **v6.1: SMART PRE-FILTER for large scans (800 stocks → 200 top candidates)**
        **[PROVIDER-RESILIENCE]: yfinance rate-limit aware concurrency + circuit
          breaker. ``safe_mode`` forces minimal concurrency for a manual retry
          after a provider throttled the previous scan.**
        """
        
        # **v6.0 FIX #1.4: Reset results at START to prevent memory leak**
        self.scan_results = []
        self.failed_tickers = []
        self._bulk_history = {}
        # Start each scan from the optimistic dispatch gap, and from a clean
        # provider state: PROVIDER_STATE is process-wide, so a cooldown opened
        # near the end of one webapp scan would otherwise blank the next one.
        # A genuine 429 re-opens it within a few requests.
        PROVIDER_PACER.reset()
        PROVIDER_STATE.reset()
        # [PRE-MOMENTUM] Reset per-scan chase-alert watchlist
        self.chase_alerts = []
        self.gap_filled_alerts = []
        self._news_cache = {}
        self.current_scan_type = scan_type  # Store for filter adjustments
        
        # [FIX #15] Filter stats: track WHY stocks are rejected for diagnostic visibility
        self._filter_stats: Dict[str, int] = {}
        self._filter_stats_lock = __import__('threading').Lock()
        self._diagnostics_lock = threading.Lock()
        self._provider_reads = {}
        self._candidate_rejections = {}
        # [SCAN-HEALTH] Structured per-ticker error log (exceptions/timeouts only)
        # so the dashboard can separate a real scan failure from a clean no-picks
        # result. Guarded by _filter_stats_lock.
        self._error_records: List[Dict[str, Any]] = []
        # [A04] One terminal outcome per scanned name (the first failure wins)
        # plus non-exclusive warnings, so every published count reconciles to
        # the universe. Guarded by _filter_stats_lock.
        self._outcomes: Dict[str, Tuple[str, str]] = {}
        self._outcome_warnings: Dict[str, set] = {}
        self._outcome_tls = threading.local()
        self._outcome_universe: set = set()
        self._scan_mode_filter: Optional[Tuple[int, int]] = None
        # [ITEM 7] Zero-allocation sub-breakdown: WHY a stock that otherwise
        # passed scoring ended up with 0 position size, plus the tickers so we
        # can print the top rejected names with an explicit reason.
        self._zero_alloc_reasons: Dict[str, int] = {}
        self._zero_alloc_tickers: List[Tuple[str, str]] = []
        # [PROVIDER-RESILIENCE] Per-scan provider circuit state. When yfinance
        # throttling crosses the trip threshold we set _provider_circuit_open so
        # analyze_stock fails fast and we return partial results.
        self._safe_mode = bool(safe_mode)
        self._provider_circuit_open = False
        self._provider_degraded = False
        self.current_drawdown = self._estimate_current_drawdown()
        self.drawdown_multiplier = RiskLimits.get_drawdown_multiplier(self.current_drawdown)

        if self.drawdown_multiplier <= 0:
            logger.warning("🛑 Drawdown guardrail active (>=15%): new positions are halted")
            return []
        elif self.drawdown_multiplier < 1.0:
            logger.warning(
                f"⚠️ Drawdown guardrail: current drawdown {self.current_drawdown:.1%}, "
                f"position sizes scaled to {self.drawdown_multiplier:.0%}"
            )
        scan_start_time = time.time()

        # Use custom tickers if provided (for strategy-based/focused scans)
        self._bulk_timed_out = False
        _sel_started = time.time()
        self._report_progress("selecting", 0, 0)
        if custom_tickers:
            # One entry per symbol, in the order given (any caller, not only the webapp).
            custom_tickers = list(dict.fromkeys(str(t).strip().upper() for t in custom_tickers if str(t).strip()))
            universe = custom_tickers
            self._universe_selection = {"scan_type": scan_type, "method": "custom_list", "cause": None,
                                        "seconds": None, "budget_seconds": None,
                                        "candidates": len(custom_tickers), "selected": len(custom_tickers)}
        else:
            universe = self.build_stock_universe(scan_type)
        self._note_selection(total_seconds=round(time.time() - _sel_started, 2))
        _sel = getattr(self, "_universe_selection", None) or {}
        if _sel.get("selected") is None:
            self._note_selection(selected=len(universe))
        self._report_progress("selecting", len(universe), int(_sel.get("candidates") or len(universe)))

        # [FUNNEL] Capture original universe size before any pre-filter so the
        # webapp can show the true \"100 scanned\" number, not the post-filter one.
        self._last_universe_size = len(universe)
        # [F1] Every scanned name gets the corporate-event check. Start the
        # EDGAR pass now, in the background, so the gate reads a warm cache.
        self._scanned_universe = list(universe)
        self._start_event_warmup(self._scanned_universe)

        # [W12 FIX] The pre-filter used to live here behind `len(universe) > 200`,
        # but every built-in scan limit was <= 200, so it never once executed —
        # "PHASE 1: SMART PRE-SCREENING" was unreachable code. Universe
        # reduction now happens inside build_stock_universe (see
        # _select_broad_universe), which is the single place that decides which
        # names get scanned. A custom ticker list is scanned in full: if the
        # user named the symbols, we do not second-guess the selection.
        original_size = len(universe)

        # Initialize circuit breaker for this scan
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=10, 
            reset_timeout=60,
            consecutive_threshold=20, 
            pause_duration=30
        )

        logger.info(f"\n{'=' * 80}")
        logger.info("INITIATING COMPREHENSIVE MARKET SCAN")
        logger.info(f"{'=' * 80}")
        logger.info(f"Analyzing {len(universe)} stocks with multi-factor scoring:")
        # [O1 FIX] These five lines used to be hardcoded literals reading
        # 30/25/20/15/10 — none of which matched _REGIME_WEIGHTS. The first
        # thing the report asserted about the model was false. Print the
        # weights actually in force, and name the regime they came from.
        _active_regime = getattr(self.market_regime, 'regime_type', 'NEUTRAL')
        _active_weights = _REGIME_WEIGHTS.get(_active_regime, _REGIME_WEIGHTS["NEUTRAL"])
        for _factor, _weight in _active_weights.items():
            logger.info(f"  • {_factor.capitalize():<12} {_weight:.0%}")
        logger.info(f"  (regime: {_active_regime})")
        logger.info(f"{'=' * 80}\n")


        opportunities = []
        processed = 0

        # [PROVIDER-RESILIENCE] yfinance is the analyze_stock data source, so the
        # effective worker count is capped at YF_MAX_WORKERS. Safe-mode retries
        # and large (>200) universes drop concurrency further to avoid tripping
        # the provider's rate limiter in the first place.
        max_workers = min(TradingConfig.MAX_WORKERS, TradingConfig.YF_MAX_WORKERS)
        if len(universe) > 200:
            max_workers = min(max_workers, TradingConfig.YF_MAX_WORKERS)
        if self._safe_mode:
            max_workers = min(max_workers, 2)
        max_workers = max(1, max_workers)
        logger.info(
            f"Provider: yfinance | Scan workers: {max_workers} "
            f"(MAX_WORKERS={TradingConfig.MAX_WORKERS}, YF_MAX_WORKERS={TradingConfig.YF_MAX_WORKERS}"
            + (", SAFE_MODE" if self._safe_mode else "") + ")"
        )

        # Parallel analysis with ThreadPoolExecutor.
        #
        # [PROVIDER-RESILIENCE] When yfinance throttles, the scan used to HALT
        # and return partial results. The universe is symbol-ordered, so a
        # full scan throttled at ~627/836 never looked at anything after "R"
        # (TSLA, UNH, V, WMT, XOM ...), and the names that were rate-limited
        # were simply dropped. It now waits out the provider cooldown and
        # RESUMES — the not-yet-scanned names plus the rate-limited ones — at
        # one worker, for a bounded number of rounds.
        pending: List[str] = []
        seen_clean: set = set()
        for ticker in universe:
            clean_ticker = self._sanitize_ticker(ticker)
            if clean_ticker and clean_ticker not in seen_clean:
                seen_clean.add(clean_ticker)
                pending.append(clean_ticker)
        self._scan_not_reached = []
        self._outcome_universe = set(pending)
        _progress_total = len(pending)
        _progress_done = 0
        self._report_progress("analysing", 0, _progress_total)
        # Batch the daily bars before fanning out: a handful of chunked
        # requests instead of one history call per name. Best-effort; any
        # missing ticker falls back to its own request.
        try:
            self.prefetch_history(pending)
        except Exception as e:  # never fatal
            logger.debug("History prefetch skipped: %s", e)
            self._bulk_history = {}
        resume_round = 0
        max_resumes = TradingConfig.PROVIDER_MAX_RESUMES

        while pending:
            not_started: List[str] = []
            rl_baseline = self._filter_stats.get('provider_rate_limited', 0)
            err_baseline = len(getattr(self, '_error_records', []) or [])
            round_processed = 0
            tripped = False
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_ticker = {
                    executor.submit(self.analyze_stock, t): t for t in pending
                }

                # The console bar is a convenience for a terminal run; it never
                # decides whether the scan finishes (see _progress_iter).
                iterator = self._progress_iter(future_to_ticker, len(future_to_ticker))

                handled: set = set()
                for future in iterator:
                    ticker = future_to_ticker[future]
                    if future.cancelled():
                        continue
                    handled.add(future)
                    processed += 1
                    round_processed += 1
                    _progress_done = min(_progress_total, max(_progress_done, processed))
                    self._report_progress("analysing", _progress_done, _progress_total)

                    try:
                        analysis = future.result()
                        if analysis:
                            opportunities.append(analysis)
                            self._note_survivor(ticker)
                            self.circuit_breaker.record_success()
                    except (ValueError, KeyError, IndexError) as e:
                        logger.debug(f"Data error processing {ticker}: {type(e).__name__}")
                        self._record_scan_error(ticker, e, stage="scan_future")
                        self._record_failed_ticker(ticker)
                        self.circuit_breaker.record_failure()
                    except Exception as e:
                        logger.debug(f"Error processing {ticker}: {type(e).__name__}: {str(e)[:100]}")
                        self._record_scan_error(ticker, e, stage="scan_future")
                        self._record_failed_ticker(ticker)
                        if self.circuit_breaker.record_failure():
                            logger.warning(f"⏸️ Pausing for {self.circuit_breaker.pause_duration}s due to consecutive failures")
                            time.sleep(self.circuit_breaker.pause_duration)
                            self.circuit_breaker.record_success()

                    # Trip on THIS round's rate-limit errors (the counter is
                    # cumulative across rounds).
                    if not self._provider_circuit_open:
                        rl = self._filter_stats.get('provider_rate_limited', 0) - rl_baseline
                        ratio = (rl / round_processed) if round_processed >= TradingConfig.PROVIDER_RATE_LIMIT_MIN_SAMPLE else 0.0
                        if (rl >= TradingConfig.PROVIDER_RATE_LIMIT_TRIP_COUNT
                                or ratio >= TradingConfig.PROVIDER_RATE_LIMIT_TRIP_RATIO):
                            self._provider_circuit_open = True
                            self._provider_degraded = True
                            tripped = True
                            PROVIDER_STATE.note_rate_limit(TradingConfig.RATE_LIMIT_COOLDOWN_SECONDS)
                            logger.warning(
                                f"🛑 PROVIDER CIRCUIT OPEN — {rl} rate-limit errors after "
                                f"{processed} tickers ({len(opportunities)} opportunities so far)."
                            )
                            for pending_future, t in future_to_ticker.items():
                                if not pending_future.done() and pending_future.cancel():
                                    not_started.append(t)
                            break

            # Calls already running when the circuit tripped finish as the
            # executor shuts down; keep their results instead of dropping them.
            for fut, t in future_to_ticker.items():
                if fut in handled or fut.cancelled() or not fut.done():
                    continue
                processed += 1
                try:
                    late = fut.result()
                    if late:
                        opportunities.append(late)
                        self._note_survivor(t)
                except Exception as e:
                    self._record_scan_error(t, e, stage="scan_future")
                    self._record_failed_ticker(t)

            if not tripped:
                break

            # Re-queue what was never scanned plus what the provider refused.
            new_errors = (getattr(self, '_error_records', []) or [])[err_baseline:]
            throttled = [
                e.get('ticker') for e in new_errors
                if e.get('error_type') == 'provider_rate_limited' and e.get('ticker')
            ]
            retry = list(dict.fromkeys(not_started + throttled))
            if not retry:
                break
            if resume_round >= max_resumes:
                self._scan_not_reached = retry
                logger.warning(
                    f"Provider still throttling after {resume_round} resume(s); "
                    f"{len(retry)} name(s) NOT scanned. Re-run later with safe mode."
                )
                break
            resume_round += 1
            # Exponential: in a live 836-name scan a flat 60s pause re-tripped
            # within a few tickers; the second, longer pause held.
            cooldown = max(
                float(TradingConfig.RATE_LIMIT_COOLDOWN_SECONDS) * (2 ** (resume_round - 1)),
                float(PROVIDER_STATE.cooldown_remaining() or 0.0),
            )
            logger.warning(
                f"⏳ Waiting {cooldown:.0f}s for the provider cooldown, then resuming "
                f"{len(retry)} name(s) at 1 worker (resume {resume_round}/{max_resumes})."
            )
            time.sleep(cooldown)
            # The rate-limited tickers are retried, so they are not failures.
            with self._filter_stats_lock:
                self._error_records = [
                    e for e in (self._error_records or [])
                    if not (e.get('error_type') == 'provider_rate_limited'
                            and e.get('ticker') in set(retry))
                ]
            self.failed_tickers = [t for t in self.failed_tickers if t not in set(retry)]
            with self._filter_stats_lock:
                for t in retry:
                    self._outcomes.pop(t, None)
            processed -= len(set(throttled))
            self._provider_circuit_open = False
            max_workers = 1
            pending = retry

        _unreached = sum(1 for t in set(getattr(self, '_scan_not_reached', []) or [])
                         if t not in (getattr(self, '_outcomes', None) or {}))
        self._report_progress("analysing", _progress_total - _unreached, _progress_total)
        # **v6.0: Calculate scan duration and throughput**
        scan_duration = time.time() - scan_start_time
        throughput = processed / scan_duration if scan_duration > 0 else 0

        logger.info(f"{'=' * 80}")
        logger.info("SCAN COMPLETE")
        logger.info(f"  • Stocks analyzed: {processed}")
        logger.info(f"  • Opportunities found: {len(opportunities)}")
        logger.info(f"  • Failed: {len(self.failed_tickers)}")
        _not_reached = len(getattr(self, '_scan_not_reached', []) or [])
        if _not_reached:
            logger.info(f"  • NOT scanned (provider throttling): {_not_reached}")

        if processed > 0:
            success_rate = safe_divide(processed - len(self.failed_tickers), processed, 0) * 100
            logger.info(f"  • Success rate: {success_rate:.1f}%")

        # v6.0: Log scan duration and throughput
        logger.info(f"  • Scan duration: {scan_duration:.1f}s")
        logger.info(f"  • Throughput: {throughput:.1f} stocks/sec")
        
        # Backtest validation status. Walk-forward (if any) is executed in the
        # block further below and logs its own header; do not pre-announce it here.
        if not HAS_BACKTEST:
            logger.info("  • Backtest: Engine not available (install backtest_engine for validation)")

        logger.info(f"{'=' * 80}\n")
        
        # [FIX #15] Log aggregated filter rejection summary
        self._log_filter_summary()

        # ══════════════════════════════════════════════════════════════════════
        # [FIX #1] Cross-sectional z-score normalization
        # Re-score every stock relative to the scanned universe so that a
        # "cheap P/E" means cheap *versus peers*, not versus an arbitrary
        # constant threshold.
        # ══════════════════════════════════════════════════════════════════════
        regime_type = getattr(self.market_regime, 'regime_type', 'NEUTRAL')
        regime_weights = _REGIME_WEIGHTS.get(regime_type, _REGIME_WEIGHTS["NEUTRAL"])
        _zscore_applied = False
        self._universe_stats = None
        if len(opportunities) >= TradingConfig.ZSCORE_MIN_UNIVERSE:
            try:
                universe_stats = UniverseStats.from_analyses(opportunities)
                # [O7 FIX] Kept on the instance so the display layer can quote
                # a real cross-sectional percentile instead of inventing one
                # from a hardcoded threshold.
                self._universe_stats = universe_stats
                for opp in opportunities:
                    ScoringEngine.normalize_scores(opp, universe_stats, regime_weights)
                _zscore_applied = True
                logger.info(f"✓ Cross-sectional z-score normalization applied to {len(opportunities)} stocks")

                # [M1/W13 FIX] Report sub-score collinearity. If two sub-scores
                # correlate above ~0.8 they are one factor wearing two weights,
                # and the nominal weights are not the effective weights.
                self._log_factor_collinearity(opportunities)
            except Exception as e:
                logger.warning(f"Z-score normalization skipped: {e}")
        if not _zscore_applied and opportunities:
            # Regime weighting is per-stock arithmetic and needs no population,
            # so a thin pool still gets the regime tilt in its ORDERING. An
            # empty UniverseStats makes every z-score blend fall back to the
            # absolute sub-score and publishes no percentile.
            empty_stats = UniverseStats()
            for opp in opportunities:
                ScoringEngine.normalize_scores(opp, empty_stats, regime_weights)
            logger.info(
                f"Cross-sectional z-scores skipped: {len(opportunities)} scored names "
                f"(< {TradingConfig.ZSCORE_MIN_UNIVERSE}); ranking uses regime weights "
                f"for {regime_type} without percentiles."
            )

        # Order by the cross-sectional ranking score (overall_score when the
        # universe was too small to normalise). Ties broken by symbol so the
        # order never depends on thread completion order.
        for opp in opportunities:
            if not getattr(opp, 'ranking_score', 0.0):
                opp.ranking_score = opp.overall_score
        self.scan_results = sorted(
            opportunities, key=lambda x: (-x.ranking_score, -x.overall_score, x.ticker)
        )

        # ══════════════════════════════════════════════════════════════════════
        # [ITEM 8] Universe pipeline counts — make the funnel from "names looked
        # at" to "final picks" explicit so an empty/small recommendation list is
        # never mysterious. Z-score normalization runs on the full valid-scoring
        # universe ABOVE, before any final opportunity gating below.
        # ══════════════════════════════════════════════════════════════════════
        _zscore_universe = len(opportunities) if _zscore_applied else 0
        _actionable = sum(
            1 for o in opportunities
            if getattr(o, 'signal', None) in (
                SignalType.STRONG_BUY.value, SignalType.BUY.value, SignalType.ACCUMULATE.value
            )
        )
        self._universe_counts = {
            'analyzed_universe': processed,
            'valid_scoring_universe': len(opportunities),
            'zscore_universe': _zscore_universe,
            'actionable_opportunities': _actionable,
        }
        logger.info(f"{'=' * 60}")
        logger.info("UNIVERSE PIPELINE")
        logger.info(f"  Analyzed universe count   : {processed}")
        logger.info(f"  Valid scoring universe    : {len(opportunities)}")
        logger.info(f"  Z-score universe          : {_zscore_universe}")
        logger.info(f"  Actionable opportunities  : {_actionable}")
        logger.info("  Final recommendation count: (decided at get_top_recommendations)")
        logger.info(f"{'=' * 60}\n")

        # [PRE-MOMENTUM] Pre-momentum scan mode: filter to asymmetric setups
        # (early-momentum classifier hit ≥3 of 6 confirmations) AND not extended,
        # then re-rank primarily by classifier score, with overall_score as tiebreak.
        # Tickers that triggered the CHASE ALERT during analyze_stock are
        # automatically excluded here because they live in self.chase_alerts
        # and we surface them separately as a "too-late-to-chase" watchlist.
        if scan_type == "pre_momentum":
            chase_set = {c.get('ticker') for c in self.chase_alerts}
            filtered: List[StockAnalysis] = []
            for r in self.scan_results:
                if r.ticker in chase_set:
                    continue
                if r.early_momentum_score < _T.CHASE.PREMOMENTUM_MIN_SIGNALS:
                    continue
                # Not yet extended: 1M momentum must stay under CHASE.PREMOMENTUM_MAX_1M
                mom_1m = float(getattr(r.technical_indicators, 'momentum_1m', 0) or 0)
                if mom_1m >= _T.CHASE.PREMOMENTUM_MAX_1M:
                    continue
                filtered.append(r)
            filtered.sort(
                key=lambda x: (-x.early_momentum_score, -x.ranking_score, x.ticker)
            )
            logger.info(
                f"🚀 PRE-MOMENTUM filter: {len(filtered)} asymmetric setups "
                f"(from {len(self.scan_results)} analysed, {len(chase_set)} chase-alerts excluded)"
            )
            self._scan_mode_filter = (len(self.scan_results), len(filtered))
            self.scan_results = filtered

        # ══════════════════════════════════════════════════════════════════════
        # [FIX #1] Walk-Forward Validation (out-of-sample signal credibility)
        # Runs a quick walk-forward backtest on the top candidates to attach
        # out-of-sample metrics.  This does NOT alter the recommendation list;
        # it adds transparency about historical signal accuracy.
        # ══════════════════════════════════════════════════════════════════════
        # Diagnostic only and opt-in: it replays two years of bars for up to
        # 20 names on every scan (provider load that competes with deep
        # confirmation), and a replay on names that already survived this
        # scan's selection is not out-of-sample evidence. The evidence gate
        # (engine/evidence.py) is the authority on validation.
        if getattr(self, '_provider_circuit_open', False):
            logger.info("Walk-forward replay skipped: provider circuit is open.")
        elif (os.environ.get('QUANTA_RUN_DIAGNOSTIC_REPLAY') == '1'
              and HAS_BACKTEST and WalkForwardBacktest and len(self.scan_results) >= 3):
            try:
                top_tickers = [r.ticker for r in self.scan_results[:20]]
                bt = WalkForwardBacktest(analyzer=self)
                bt_results = bt.run_backtest(
                    tickers=top_tickers,
                    start_date=(datetime.now() - pd.DateOffset(years=2)).strftime('%Y-%m-%d'),
                    holding_period=30,
                    initial_capital=self.capital,
                )
                if bt_results and bt_results.total_trades >= 10:
                    # [DECISION-CORE] Truthful labelling — these numbers are a
                    # diagnostic replay, not a validated backtest. Do NOT
                    # present as predictive performance. The raw diagnostic
                    # figures (annualised return / drawdown) are now shown ONLY
                    # in developer/debug mode, because in normal output they
                    # were being read as real, achievable performance.
                    _debug_mode = (
                        os.environ.get("QUANTA_DEBUG", "").strip() in ("1", "true", "True")
                        or bool(getattr(self, "debug", False))
                    )
                    # Stash on instance so the display layer can reference it.
                    self._last_backtest = bt_results
                    if _debug_mode:
                        _banner = (
                            BACKTEST_DIAGNOSTIC_BANNER
                            if HAS_DECISION_CORE
                            else "DIAGNOSTIC REPLAY — NOT A VALIDATED BACKTEST"
                        )
                        logger.info("=" * 60)
                        for _line in _banner.splitlines():
                            logger.info(_line)
                        logger.info("-" * 60)
                        logger.info(f"  Trades: {bt_results.total_trades}  |  Win Rate: {bt_results.win_rate:.0%}")
                        logger.info(f"  Sharpe (in-sample): {bt_results.sharpe_ratio:.2f}  |  Max DD: {bt_results.max_drawdown:.1%}")
                        logger.info(f"  Diagnostic Ann. Return: {bt_results.annualized_return:.1%}")
                        if HAS_DECISION_CORE:
                            logger.info("-" * 60)
                            for _line in backtest_disclaimer().splitlines():
                                if _line.strip():
                                    logger.info("  " + _line)
                        logger.info("=" * 60)
                    else:
                        # Normal output: a single honest status line, no numbers.
                        logger.info(
                            "Backtest status: Not validated. Use forward paper trading "
                            "or walk-forward validation before relying on performance. "
                            "(Set QUANTA_DEBUG=1 to see diagnostic replay figures.)"
                        )
                else:
                    logger.info("Walk-forward: insufficient trades for statistical significance")
            except Exception as e:
                logger.debug(f"Walk-forward validation skipped: {e}")
        elif not HAS_BACKTEST:
            logger.debug("Walk-forward validation unavailable (backtest_engine not installed)")

        return self.scan_results

    # Conservatism ordering shared by the deep-confirmation reconciliation.
    # Higher = more bullish/aggressive. A deep verdict that ranks LOWER than the
    # scanner's is "more conservative" and wins, so the published list can never
    # be more aggressive than the authoritative deep analyzer.
    @classmethod
    def _rank_action(cls, action: Optional[str]) -> int:
        """Rank an action, resolving anything unrecognised conservatively.

        The two call sites used opposite defaults for the same lookup —
        `.get(x, 0)` (treat unknown as AVOID) in one place and `.get(x, 99)`
        (treat unknown as better than STRONG_BUY) in the other. An unknown
        action means we do not know what the engine decided, and the safe
        reading of that is always the conservative one.
        """
        return cls._ACTION_RANK.get(action, cls._ACTION_RANK['AVOID'])

    _ACTION_RANK = {
        'STRONG_BUY': 5, 'BUY': 4, 'ACCUMULATE': 3,
        'HOLD_EXISTING': 2, 'HOLD': 2, 'WATCHLIST': 1, 'REDUCE': 1, 'AVOID': 0,
        # INVALID = no valid data; treat as the MOST conservative verdict so a
        # deep INVALID always forces a downgrade and zeroes any allocation.
        'INVALID': -1,
    }

    def _mark_not_confirmed(self, pick: "StockAnalysis", status: str, reason: str) -> None:
        """Move a pick to the not-confirmed watchlist (zero allocation).

        Used when the deep analyzer could not independently validate a scanner
        pick for a DATA reason (rate-limit, provider outage, too few bars,
        confirmation error). The pick is NOT bearish-rejected and NOT invalid —
        it simply must not carry capital until it can be confirmed.
        """
        pick.final_action = 'WATCHLIST'
        if HAS_DECISION_CORE:
            try:
                pick.signal = SignalType.HOLD.value
            except Exception:
                pass
        apply_allocation(pick, 0.0, self.capital)
        # Surface the data status so the webapp/UI can label it accurately.
        setattr(pick, 'data_status', status)
        setattr(pick, 'not_independently_confirmed', True)
        existing = list(getattr(pick, 'final_action_reasons', []) or [])
        pick.final_action_reasons = existing + [reason]

    @staticmethod
    def _bind_scanner_levels(pick: "StockAnalysis", label: str) -> None:
        """[F2] Publish the scanner's own levels as the binding set, labelled."""
        from engine.levels import publish_levels
        ti = getattr(pick, 'technical_indicators', None)
        months = float(getattr(getattr(pick, 'timeframe_projection', None), 'horizon_months', 0) or 0)
        num = lambda name: float(getattr(pick, name, 0.0) or 0.0)  # noqa: E731 - stubs lack fields
        lv = publish_levels(
            "scanner", num('entry_price'), num('stop_loss'), num('price_target'), num('risk_reward_ratio'),
            atr=getattr(ti, 'atr', None), cost_frac=float(getattr(pick, 'transaction_cost_bps', 0.0) or 0.0) / 10000,
            horizon_days=max(1, round(months * 21)) if months > 0 else None,
            target_basis="mean of structure above spot and the analyst consensus",
            label=label, min_rr=_T.RR.SCANNER_MIN, currency=_listing_currency(getattr(pick, 'ticker', '')))
        # [P5] The price these levels were built on: the scan fetch's latest close.
        lv["price_basis"] = {"price": num('entry_price'),
                             "session": str(getattr(pick, 'market_timestamp', '') or '')[:10] or None,
                             "fetch": "scan"}
        pick.binding_levels = lv
        pick.scanner_levels = dict(lv)

    def _fund_at_least_one_share(self, rec: "StockAnalysis", allocation: float) -> float:
        """[A06] Size ``rec`` at ``allocation``, rounding a sub-one-share amount
        up to one share when ``TradingConfig.one_share_allowed``. Returns the
        dollars applied (0.0 when nothing fits)."""
        return apply_allocation(rec, allocation, self.capital, one_share=True)

    def _bind_research_levels(self, pick: "StockAnalysis", res: Any, alloc_before: float) -> float:
        """[F2] Make the Research engine's trade setup the pick's published levels.

        The scanner's set moves to ``pick.scanner_levels`` (reference only). A
        funded pick is re-sized on the Research stop: allocation x stop distance
        is the risk budget, so the size scales with the ratio of the two stop
        distances, then the position cap applies. Returns the allocation the
        rest of deep confirmation should treat as "before".
        """
        from engine.levels import publish_levels
        ts = getattr(res, 'trade_setup', None)
        try:
            entry = float(getattr(ts, 'entry_price', 0) or 0)
            stop = float(getattr(ts, 'stop_loss', 0) or 0)
            target = float(getattr(ts, 'horizon_target', 0) or 0)
        except (TypeError, ValueError):
            return alloc_before
        if ts is None or not getattr(ts, 'levels_available', True) or not (0 < stop < entry) or target <= 0:
            return alloc_before
        horizon = int(getattr(ts, 'horizon_days', 0) or _T.LEVELS.RESEARCH_HORIZON_DAYS)
        base = float(getattr(ts, 'target_base', 0) or 0)
        cur = _listing_currency(getattr(pick, 'ticker', ''))
        basis = (f"12-month valuation target {cur}{base:,.2f} pro-rated to {horizon} sessions"
                 if base > 0 else f"valuation target pro-rated to {horizon} sessions")
        cost_frac = (_T.LEVELS.RESEARCH_SPREAD_BPS + 2 * _T.LEVELS.RESEARCH_SLIPPAGE_BPS) / 10000
        lv = publish_levels(
            "research", entry, stop, target, float(getattr(ts, 'risk_reward_ratio', 0) or 0),
            atr=getattr(getattr(res, 'technical', None), 'atr', None), cost_frac=cost_frac,
            horizon_days=horizon, target_basis=basis, min_rr=_T.RR.RESEARCH_MIN, currency=cur)
        # [P5] The Research engine reads its own daily bars: its latest close and
        # session are the basis of these levels (and of the size, which uses them).
        try:
            spot = float(getattr(res, 'current_price', 0) or 0) or None
        except (TypeError, ValueError):
            spot = None
        lv["price_basis"] = {"price": spot,
                             "session": str(getattr(res, 'market_timestamp', '') or '')[:10] or None,
                             "fetch": "Research"}
        scan_entry = float(getattr(pick, 'entry_price', 0) or 0)
        scan_stop = float(getattr(pick, 'stop_loss', 0) or 0)
        if not getattr(pick, 'scanner_levels', None):
            self._bind_scanner_levels(pick, "Scanner reference levels")
        pick.scanner_levels = dict(pick.scanner_levels, label="Scanner reference levels")
        pick.binding_levels = lv
        pick.entry_price, pick.stop_loss, pick.price_target = lv['entry'], lv['stop'], lv['target']
        pick.risk_reward_ratio = lv['risk_reward']
        if alloc_before > 0 and scan_entry > scan_stop > 0:
            scan_pct = (scan_entry - scan_stop) / scan_entry
            research_pct = (entry - stop) / entry
            resized = alloc_before * scan_pct / research_pct
            cap = TradingConfig.effective_max_position_pct(self.capital) * float(self.capital)
            alloc_before = apply_allocation(pick, round(min(resized, cap), 2), self.capital)
        return alloc_before

    def _risk_grade_for(self, hist: pd.DataFrame, ticker: str, rm: Any = None) -> Dict[str, Any]:
        """[F3] The scanner's risk grade: engine.risk_grade.grade_from_history on
        the full fetch (``RISK_GRADE.LOOKBACK_DAYS``) against the market
        benchmark, exactly as the Research engine computes it."""
        from engine.risk_grade import grade_from_history
        try:
            g = grade_from_history(hist['Close'], hist['Volume'] if 'Volume' in hist.columns else None,
                                   getattr(self, 'benchmark_hist', None))
        except Exception as e:  # noqa: BLE001 - surfaced as the neutral-input fallback below
            logger.debug(f"{ticker}: risk grade from history failed: {e}")
            g = {"grade": None, "drivers": [], "lookback_days": 0}
        if g.get("grade"):
            return g
        # Too little history for the measured grade: the shared rule on the
        # scanner's own measures, tail and liquidity neutral, and it says so.
        from engine import RiskGradeInputs, compute_risk_grade, risk_grade_drivers
        inputs = RiskGradeInputs(annualized_volatility=float(getattr(rm, 'volatility', 0.0) or 0.0),
                                 beta=float(getattr(rm, 'beta', 1.0) or 1.0),
                                 max_drawdown=float(getattr(rm, 'max_drawdown', 0.0) or 0.0))
        points, grade = compute_risk_grade(inputs)
        return {"grade": grade, "points": points, "drivers": risk_grade_drivers(inputs),
                "lookback_days": int(g.get("lookback_days") or 0), "partial": True}

    def _deep_confirm_final_picks(self, picks: List[StockAnalysis]) -> List[StockAnalysis]:
        """[ITEM 13] Reconcile the scanner's FINAL published picks against the
        authoritative deep analyzer (TradingAnalysis_ELITE).

        The fast scanner and the deep analyzer use different valuation/risk
        models, so they can legitimately disagree on whether a name carries red
        flags (e.g. the deep DCF flags 40% overvaluation that the scanner's
        analyst-target proxy misses). To guarantee the scanner never publishes a
        recommendation MORE bullish than the deep analyzer, we run the deep
        engine on the handful of final picks only and adopt its verdict whenever
        it is more conservative. Disabled with QUANTA_NO_DEEP_CONFIRM=1.
        """
        if not picks:
            return picks
        # [F2] Until the Research engine supplies levels, the scanner's bind,
        # and the label says the Research check has not run.
        for _p in picks:
            self._bind_scanner_levels(_p, "Scanner levels (Research check not run)")
        if os.environ.get('QUANTA_NO_DEEP_CONFIRM', '').strip() in ('1', 'true', 'True'):
            logger.warning(
                "WARNING: Deep confirmation is disabled (QUANTA_NO_DEEP_CONFIRM). "
                "This is a developer-only override. Published picks may contradict "
                "the deep analyzer and are NOT safe for live/paper trading."
            )
            self._console_print("\n" + "!" * 80)
            self._console_print("⚠️  DEEP CONFIRMATION DISABLED — DEVELOPER-ONLY OVERRIDE")
            self._console_print("    Published picks were NOT independently confirmed and may")
            self._console_print("    contradict the deep analyzer. Do not trade from this output.")
            self._console_print("!" * 80 + "\n")
            self._deep_confirm_enabled = False
            self._deep_confirm_records = [{
                'ticker': p.ticker,
                'scanner_action': getattr(p, 'final_action', None) or p.signal,
                'deep_action': 'SKIPPED',
                'final_action': getattr(p, 'final_action', None) or p.signal,
                'reason': 'deep confirmation disabled (QUANTA_NO_DEEP_CONFIRM)',
                'allocation_before': float(getattr(p, 'allocation', 0.0) or 0.0),
                'allocation_after': float(getattr(p, 'allocation', 0.0) or 0.0),
                'downgraded': False,
            } for p in picks]
            return picks
        self._deep_confirm_enabled = True
        try:
            _ta = importlib.import_module('TradingAnalysis_ELITE')
            DeepAnalyzer = getattr(_ta, 'QuantaEliteAnalyzer', None)
            if DeepAnalyzer is None:
                raise ImportError("TradingAnalysis_ELITE has no QuantaEliteAnalyzer")
            deep = DeepAnalyzer()
        except Exception as e:  # pragma: no cover - environment dependent
            logger.warning(
                f"Deep-confirm unavailable ({type(e).__name__}: {e}); published "
                f"picks were NOT independently confirmed."
            )
            # A missing analyzer cannot confirm any pick. Use the same
            # zero-allocation watchlist path as a per-ticker data failure.
            self._deep_confirm_enabled = False
            self._deep_confirm_records = []
            for p in picks:
                scan_fa = getattr(p, 'final_action', None) or p.signal
                alloc_before = float(getattr(p, 'allocation', 0.0) or 0.0)
                note = "Deep analyzer unavailable; not independently confirmed."
                self._mark_not_confirmed(
                    p, status=DataStatus.DEEP_CONFIRMATION_FAILED, reason=note
                )
                self._deep_confirm_records.append({
                    'ticker': p.ticker, 'scanner_action': scan_fa,
                    'deep_action': 'NOT_CONFIRMED', 'final_action': 'WATCHLIST',
                    'reason': note, 'data_status': DataStatus.DEEP_CONFIRMATION_FAILED,
                    'not_independently_confirmed': True,
                    'allocation_before': alloc_before, 'allocation_after': 0.0,
                    'downgraded': True,
                })
            return picks

        # [W6 FIX] Shared maps — these were duplicated here and in
        # analyze_stock with slightly different keys.
        _fa_to_signal = _FINAL_ACTION_TO_SIGNAL
        _non_allocating = _NON_ALLOCATING_ACTIONS

        # Reconciliation audit trail consumed by _print_deep_confirmation_summary
        # and exposed for tests / the webapp.
        records: List[Dict[str, Any]] = []

        # Which analyzer pass produced each verdict. Fast mode skips news and
        # analyst modules, so the reader must be able to see a preliminary
        # confirmation for what it is. Recorded per ticker, never carried over.
        deep_modes: Dict[str, str] = {}
        deep_grades: Dict[str, str] = {}

        def _deep_analyze(ticker: str):
            # [F7] One research call per (ticker, session, mode), shared with My
            # Portfolio (engine.research_cache), in the one configured mode
            # (RESEARCH.MODE). The verdict, levels and grade do not depend on
            # the account size; deep confirmation re-sizes on its own capital.
            from engine import research_cache
            mode = _T.RESEARCH.MODE
            deep_modes[ticker] = 'unknown'

            def _run():
                try:
                    return deep.analyze(ticker, account_size=self.capital, fast_mode=(mode != 'full'))
                except TypeError:
                    return deep.analyze(ticker, account_size=self.capital)

            res = research_cache.get_or_compute(ticker, mode, _run)
            deep_modes[ticker] = mode
            return res

        retry_wait = max(0.0, float(TradingConfig.DEEP_CONFIRM_RETRY_WAIT))
        # If the scan itself was throttled, let the provider recover first;
        # otherwise every deep fetch lands inside the same 429 window.
        if retry_wait > 0 and PROVIDER_STATE.in_cooldown():
            wait = min(float(PROVIDER_STATE.cooldown_remaining() or 0.0), 3 * retry_wait)
            if wait > 0:
                logger.info(f"⏳ Provider cooling down; waiting {wait:.0f}s before deep confirmation")
                time.sleep(wait)

        _retryable = {DataStatus.PROVIDER_RATE_LIMITED, DataStatus.PROVIDER_DATA_UNAVAILABLE}

        # [P1] Counts run across the first pass and the backfill, and end at n of n.
        rc = getattr(self, '_rc_progress', None)
        if rc is None:
            rc = self._rc_progress = [0, 0]
        rc[1] += len(picks)
        for _i, pick in enumerate(picks):
            self._report_progress("research_check", rc[0], rc[1])
            rc[0] += 1
            scan_fa = getattr(pick, 'final_action', None) or pick.signal
            alloc_before = float(getattr(pick, 'allocation', 0.0) or 0.0)
            try:
                res = _deep_analyze(pick.ticker)
                # One retry after a pause when the failure is the PROVIDER's.
                if str(getattr(res, 'data_status', '') or '') in _retryable:
                    if retry_wait > 0:
                        logger.info(
                            f"{pick.ticker}: deep analyzer hit a provider failure; "
                            f"retrying once in {retry_wait:.0f}s"
                        )
                        time.sleep(retry_wait)
                    res = _deep_analyze(pick.ticker)
            except Exception as e:
                logger.warning(
                    f"Deep-confirm FAILED for {pick.ticker} ({type(e).__name__}: {e}); "
                    f"recommendation not independently confirmed."
                )
                # A confirmation FAILURE is a DATA problem, not a bearish
                # disagreement. The pick must not stay funded: move it to the
                # not-confirmed watchlist with zero allocation.
                reason_txt = (
                    "Deep analyzer could not independently validate due to a data "
                    f"issue ({type(e).__name__})."
                )
                self._mark_not_confirmed(
                    pick, status=DataStatus.DEEP_CONFIRMATION_FAILED, reason=reason_txt
                )
                records.append({
                    'ticker': pick.ticker, 'scanner_action': scan_fa,
                    'deep_action': 'NOT_CONFIRMED', 'final_action': 'WATCHLIST',
                    'reason': reason_txt,
                    'data_status': DataStatus.DEEP_CONFIRMATION_FAILED,
                    'not_independently_confirmed': True,
                    'allocation_before': alloc_before, 'allocation_after': 0.0,
                    'downgraded': True,
                })
                continue

            # [DATA-RELIABILITY] A real ticker whose history could not be
            # fetched (rate-limit / provider outage / too few bars) is NOT a
            # disagreement and NOT invalid. Treat as "not independently
            # confirmed": move to watchlist, no allocation, honest reason.
            deep_data_status = str(getattr(res, 'data_status', DataStatus.OK) or DataStatus.OK)
            if is_data_failure(deep_data_status):
                reason_txt = (
                    getattr(res, 'data_status_reason', '')
                    or "Deep analyzer could not independently validate due to a data issue."
                )
                logger.info(
                    "%s: deep-confirm NOT CONFIRMED (data_status=%s)",
                    pick.ticker, deep_data_status,
                )
                self._mark_not_confirmed(pick, status=deep_data_status, reason=reason_txt)
                records.append({
                    'ticker': pick.ticker, 'scanner_action': scan_fa,
                    'deep_action': 'NOT_CONFIRMED', 'final_action': 'WATCHLIST',
                    'reason': reason_txt,
                    'data_status': deep_data_status,
                    'not_independently_confirmed': True,
                    'allocation_before': alloc_before, 'allocation_after': 0.0,
                    'downgraded': True,
                })
                continue

            deep_fa = getattr(res, 'final_action', None)
            if not deep_fa:
                # The deep analyzer catches its own exceptions and returns an
                # EMPTY verdict with data_status "ok", so a crash landed here
                # and the pick stayed fully funded as if confirmed. No verdict
                # is not a confirmation.
                reason_txt = "Deep analyzer returned no verdict; not independently confirmed."
                self._mark_not_confirmed(
                    pick, status=DataStatus.NOT_INDEPENDENTLY_CONFIRMED, reason=reason_txt
                )
                records.append({
                    'ticker': pick.ticker, 'scanner_action': scan_fa,
                    'deep_action': 'NOT_CONFIRMED', 'final_action': 'WATCHLIST',
                    'reason': reason_txt,
                    'data_status': DataStatus.NOT_INDEPENDENTLY_CONFIRMED,
                    'not_independently_confirmed': True,
                    'allocation_before': alloc_before, 'allocation_after': 0.0,
                    'downgraded': True,
                })
                continue

            raw_deep_fa = deep_fa
            # [F2] Owner decision Q1: the Research engine's levels govern. Publish
            # them as the pick's levels and size on THEIR stop, so the R:R a user
            # reads is the one the verdict was decided on.
            alloc_before = self._bind_research_levels(pick, res, alloc_before)
            deep_grades[pick.ticker] = str(getattr(getattr(res, 'risk', None), 'risk_grade', '') or '')
            deep_reasons = list(getattr(res, 'final_action_reasons', []) or [])

            # [OPTION-A RECONCILIATION] The deep analyzer's OWN final_action
            # was derived from ITS OWN stop/target/R:R/composite score — a
            # second, independently-computed set of numbers. Adopting it
            # wholesale meant two engines merely disagreeing on where to put
            # a stop (or on a differently-weighted score) could withdraw a
            # pick the scanner's own gates had already cleared (observed
            # live: four large caps withdrawn on "R/R below min" /
            # "Target <= entry" / "Score below min" computed from the deep
            # engine's own levels, not the scanner's).
            #
            # Re-resolve using ONLY this engine's QUALITATIVE red flags (data
            # validity, risk grade, insider activity, bearish reversal, DCF
            # overvaluation) against the SCANNER's own action as the starting
            # point. A true INVALID verdict (the ticker itself failed data
            # validation) is never reconciled away — it stays a hard veto.
            recon_codes: List[str] = []
            if deep_fa != FinalAction.INVALID.value:
                try:
                    if not HAS_DECISION_CORE:
                        raise RuntimeError('decision reconciliation unavailable')
                    _build_recon = getattr(_ta, 'build_reconciliation_inputs', None)
                    _recon_inputs = _build_recon(res) if _build_recon else None
                    if _recon_inputs is None:
                        raise ValueError('qualitative risk inputs unavailable')
                    _recon = resolve_final_action(
                        _recon_inputs,
                        starting_candidate=FinalAction(scan_fa),
                        enforce_price_gates=False,
                    )
                    deep_fa = _recon.final_action.value
                    # Keep only the genuine risk overrides, not bookkeeping.
                    deep_reasons = [
                        r for r in _recon.reasons if not r.startswith('Reconciling against')
                    ]
                    # Stable rule codes of the overrides that fired, so the UI
                    # states the rule's own remedy instead of parsing prose.
                    recon_codes = list(getattr(_recon, 'override_codes', []) or [])
                except Exception as e:
                    logger.debug(
                        f"{pick.ticker}: qualitative risk reconciliation failed - {e}"
                    )
                    reason_txt = 'Additional risk checks could not be reconciled; not confirmed.'
                    self._mark_not_confirmed(
                        pick, status=DataStatus.NOT_INDEPENDENTLY_CONFIRMED, reason=reason_txt
                    )
                    records.append({
                        'ticker': pick.ticker, 'scanner_action': scan_fa,
                        'deep_action': 'NOT_CONFIRMED', 'final_action': 'WATCHLIST',
                        'reason': reason_txt,
                        'data_status': DataStatus.NOT_INDEPENDENTLY_CONFIRMED,
                        'not_independently_confirmed': True,
                        'allocation_before': alloc_before, 'allocation_after': 0.0,
                        'downgraded': True,
                    })
                    continue

            # [ONE DECISION] The scan may rank and size a name, but it may never
            # publish a call MORE bullish than the Research Decision the same
            # engine produces for it (the tear sheet a user opens next). The
            # qualitative reconciliation above is kept for its reasons; the
            # Research verdict itself is the ceiling.
            if (raw_deep_fa and deep_fa != FinalAction.INVALID.value
                    and self._rank_action(raw_deep_fa) < self._rank_action(deep_fa)):
                deep_fa = raw_deep_fa
                deep_reasons = deep_reasons + list(
                    getattr(res, 'final_action_reasons', []) or []) + [
                    f"Capped at the Research Decision ({raw_deep_fa}) for this name"]
                recon_codes = recon_codes + [
                    c for c in (getattr(res, 'final_action_codes', []) or []) if c not in recon_codes
                ] + ["RESEARCH_DECISION_CAP"]

            # [Audit 2026-10-17 #5] The Research levels govern (F2), so they must
            # clear the Research hurdle they are published against. The book
            # admitted this name on the scanner's levels; a funded call beside a
            # "needs 1.50" remedy is the page contradicting itself. Fail closed.
            _bind = getattr(pick, 'binding_levels', None) or {}
            if (_bind.get('source') == 'research' and deep_fa not in _non_allocating
                    and deep_fa != FinalAction.INVALID.value
                    and float(_bind.get('risk_reward') or 0.0) < _T.RR.RESEARCH_MIN):
                deep_fa = FinalAction.WATCHLIST.value
                deep_reasons = deep_reasons + [
                    _bind.get('remedy') or (
                        f"Research R:R {float(_bind.get('risk_reward') or 0.0):.2f} is below "
                        f"the {_T.RR.RESEARCH_MIN:.2f} hurdle on the published levels")]
                if 'LOW_REWARD_RISK' not in recon_codes:
                    recon_codes = recon_codes + ['LOW_REWARD_RISK']

            if self._rank_action(deep_fa) < self._rank_action(scan_fa):
                reason_txt = '; '.join(deep_reasons) or 'deep analyzer more conservative'
                logger.info(
                    f"{pick.ticker}: deep-confirm downgrade {scan_fa} → {deep_fa} ({reason_txt})"
                )
                pick.final_action = deep_fa
                existing = list(getattr(pick, 'final_action_reasons', []) or [])
                pick.final_action_reasons = existing + [
                    f"Deep-analyzer confirmation → {deep_fa}"
                ] + deep_reasons
                new_sig = _fa_to_signal.get(deep_fa)
                if new_sig is not None:
                    pick.signal = new_sig
                # Non-allocating verdicts must not show a live position size /
                # entry order. ACCUMULATE keeps capital but at reduced size.
                if deep_fa in _non_allocating:
                    alloc_after = apply_allocation(pick, 0.0, self.capital)
                elif deep_fa == 'ACCUMULATE':
                    # Reduced-size accumulation: halve the planned allocation so
                    # the published size reflects the deep analyzer's caution.
                    alloc_after = apply_allocation(
                        pick, round(alloc_before * _T.SIZING.DEEP_ACCUMULATE_SCALE, 2), self.capital
                    )
                elif deep_fa == 'BUY' and scan_fa == 'STRONG_BUY':
                    # A STRONG_BUY -> BUY trim kept the full STRONG_BUY size.
                    # Scale by the ratio of the two tiers' risk budgets.
                    ratio = (TradingConfig.TARGET_RISK_PER_TRADE * 0.80) / (
                        TradingConfig.TARGET_RISK_STRONG_BUY * 1.00
                    )
                    alloc_after = apply_allocation(
                        pick, round(alloc_before * ratio, 2), self.capital
                    )
                else:
                    alloc_after = apply_allocation(pick, alloc_before, self.capital)

                pick.deep_reason_codes = list(recon_codes or [])
                records.append({
                    'ticker': pick.ticker, 'scanner_action': scan_fa,
                    'deep_action': raw_deep_fa, 'final_action': deep_fa,
                    'reason': reason_txt,
                    'reason_codes': recon_codes,
                    'allocation_before': alloc_before, 'allocation_after': alloc_after,
                    'downgraded': True,
                })
            else:
                reason_txt = (
                    'Research Decision is at least as constructive; no downgrade'
                    if raw_deep_fa != scan_fa else 'Research agrees; no downgrade'
                )
                records.append({
                    'ticker': pick.ticker, 'scanner_action': scan_fa,
                    'deep_action': raw_deep_fa, 'final_action': scan_fa,
                    'reason': reason_txt,
                    'allocation_before': alloc_before, 'allocation_after': alloc_before,
                    'downgraded': False,
                })

        for _p in picks:
            if (getattr(_p, 'binding_levels', None) or {}).get('source') != 'research':
                self._bind_scanner_levels(_p, "Scanner levels (Research check not confirmed)")
        for rec in records:
            rec.setdefault('deep_mode', deep_modes.get(rec['ticker'], 'unknown'))
            rec.setdefault('scanner_risk_grade', str(getattr(
                next((p for p in picks if p.ticker == rec['ticker']), None), 'risk_grade', '') or ''))
            rec.setdefault('deep_risk_grade', deep_grades.get(rec['ticker'], ''))
        self._deep_confirm_records = records
        self._print_deep_confirmation_summary(records)
        if picks:
            self._report_progress("research_check", rc[0], rc[1])
        return picks

    @staticmethod
    def _console_print(*parts: Any) -> None:
        """print() that survives a non-UTF-8 console.

        The webapp runs the scanner on a worker thread whose stdout may be a
        cp1252 pipe (Windows service / redirected log). A single glyph such as
        the downgrade arrow then raised UnicodeEncodeError from INSIDE the
        scan, and the whole portfolio job reported the encoding error instead
        of its picks. Console output is decoration; it must never fail a scan.
        """
        text = " ".join(str(p) for p in parts)
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
        except Exception:
            pass

    def _print_deep_confirmation_summary(self, records: List[Dict[str, Any]]) -> None:
        """Render the Deep Confirmation Summary table for the final picks."""
        if not records:
            return
        print = self._console_print  # noqa: A001 - a cp1252 console must not fail the scan
        headers = ['Ticker', 'Scanner', 'Deep Analyzer', 'Mode', 'Final',
                   'Reason', 'Alloc Before', 'Alloc After']
        rows = []
        for r in records:
            reason = r['reason']
            if len(reason) > 60:
                reason = reason[:57] + '...'
            mark = ' ⬇' if r.get('downgraded') else ''
            rows.append([
                r['ticker'] + mark,
                r['scanner_action'],
                r['deep_action'],
                r.get('deep_mode', 'unknown'),
                r['final_action'],
                reason,
                f"{getattr(self, 'currency_symbol', '$')}{r['allocation_before']:,.0f}",
                f"{getattr(self, 'currency_symbol', '$')}{r['allocation_after']:,.0f}",
            ])
        n_down = sum(1 for r in records if r.get('downgraded'))
        print("\n" + "=" * 80)
        print("DEEP CONFIRMATION SUMMARY  (scanner picks reconciled vs deep analyzer)")
        print("=" * 80)
        try:
            print(tabulate(rows, headers=headers, tablefmt='fancy_grid'))
        except Exception:
            print(tabulate(rows, headers=headers))
        print(f"{n_down} of {len(records)} final pick(s) downgraded by deep confirmation.")
        print("Final actions reconcile deep red flags with scanner picks; "
              "WATCHLIST/HOLD/AVOID receive no new capital, ACCUMULATE is reduced size.")
        print("=" * 80 + "\n")

    @staticmethod
    def _resolve_sector_cap(base_cap: float, qualified_count: int,
                            diversify_sectors: bool,
                            allow_relaxation: bool) -> Tuple[float, bool]:
        """Return (effective sector cap, whether it was relaxed).

        [F08] A thin candidate pool used to raise the cap to 100% on its own,
        even though the caller had asked for diversification. Recording the
        change in `sector_cap_relaxed` made it visible but did not make it
        authorised: the mandate stopped binding without anyone choosing that.
        The honest answer to "the only A-grade setups today are all Tech" is a
        smaller book plus cash, so concentration is now an explicit opt-in.
        """
        if not diversify_sectors:
            # The caller explicitly declined a diversification mandate, so the
            # sector cap must not keep binding behind their back.
            return 1.0, False
        if allow_relaxation and qualified_count <= 4:
            return 1.0, True
        return base_cap, False

    def _start_event_warmup(self, tickers: List[str]) -> None:
        """[F1] Run the event check over the universe on a background thread."""
        self._event_warm = None
        if not tickers:
            return
        from engine import corporate_events as ce
        checker = getattr(self, '_event_checker', None) or ce.check_many

        def _warm():
            try:
                checker(list(tickers))
            except Exception as exc:  # noqa: BLE001 - the gate re-checks and fails closed itself
                logger.debug(f"Event-check warm-up failed: {type(exc).__name__}: {exc}")

        self._event_warm = threading.Thread(target=_warm, name="event-warmup", daemon=True)
        self._event_warm.start()

    def _apply_event_gate(self, candidates: List["StockAnalysis"],
                          scanned: Optional[List["StockAnalysis"]] = None,
                          universe: Optional[List[str]] = None) -> List["StockAnalysis"]:
        """[F1] Check every new-money candidate for a pending acquisition or delisting.

        Returns the candidates that may still be funded. Flagged AVOID names go to
        ``self._event_rejections`` (published as rejected rows, no levels).
        Names whose check is unavailable, or whose range collapsed with no filing,
        go to ``self._event_watch`` at $0 with the reason: never funded, but still
        shown. ``self._event_checker`` may be injected (tests); by default the
        shared SEC EDGAR client is used.

        Every other scanned name is checked too: the analysed rows
        (``scanned``) and the whole scanned universe (``universe``), since an
        early stage (score, committee) drops names before they become rows. A
        flagged one is still published as AVOID with its filing's reason, so the
        reader learns why it is not a pick. Those names were never fundable, so
        an unavailable check on them changes nothing. ``scan_market`` starts
        the EDGAR pass over the universe in the background when the scan
        begins, so this mostly reads a warm cache.
        """
        from engine import corporate_events as ce
        self._event_rejections = []
        self._event_watch = []
        self._event_summary = ce.summarize({})
        self._event_candidates = list(candidates)
        cand_ids = {id(r) for r in candidates}
        others = [r for r in (scanned or []) if id(r) not in cand_ids
                  and getattr(r, 'ticker', None) not in {c.ticker for c in candidates}]
        everyone = list(candidates) + others
        known = {r.ticker for r in everyone}
        bare = [t for t in dict.fromkeys(universe or []) if t and t not in known]
        if not everyone and not bare:
            return candidates
        warm = getattr(self, '_event_warm', None)
        if warm is not None and warm.is_alive():
            warm.join(timeout=_T.EVENTS.WARMUP_JOIN_SECONDS)
            if warm.is_alive():
                logger.warning(f"Event-check warm-up still running after "
                               f"{_T.EVENTS.WARMUP_JOIN_SECONDS:g}s; the gate checks the names itself.")
        checker = getattr(self, '_event_checker', None) or ce.check_many
        prices = {r.ticker: float(r.entry_price or 0.0) for r in everyone}
        atr = {}
        for r in everyone:
            ti = getattr(r, 'technical_indicators', None)
            atr[r.ticker] = (getattr(ti, 'atr_pct_20', None), getattr(ti, 'atr_pct_120', None))
        try:
            checks = checker(list(dict.fromkeys([r.ticker for r in everyone] + bare)), prices=prices, atr=atr)
        except Exception as exc:  # noqa: BLE001 - the whole check failing is "unavailable" for all
            checks = {r.ticker: ce._unavailable(f"{type(exc).__name__} in the event check") for r in everyone}
        self._event_summary = ce.summarize(checks, [r.ticker for r in candidates])
        for t in bare:
            ev = checks.get(t)
            if ev is not None and ev.blocks_new_money_outright:
                self._event_rejections.append({
                    "ticker": t, "company": getattr(ev, "registrant", "") or t, "sector": "",
                    "price": None, "final_action": FinalAction.AVOID.value,
                    "code": ev.code, "reason": ev.text, "event_check": ev.to_dict(),
                })
                logger.info(f"Corporate-event gate: {t} AVOID ({ev.code}), dropped before analysis: {ev.text}")
        for r in others:
            ev = checks.get(r.ticker)
            if ev is None:
                continue
            r.event_check = ev.to_dict()
            if ev.blocks_new_money_outright:
                self._event_rejections.append({
                    "ticker": r.ticker, "company": r.company, "sector": r.sector,
                    "price": float(r.entry_price or 0.0), "final_action": FinalAction.AVOID.value,
                    "code": ev.code, "reason": ev.text, "event_check": ev.to_dict(),
                })
                logger.info(f"Corporate-event gate: {r.ticker} AVOID ({ev.code}), not a candidate: {ev.text}")
        kept: List[StockAnalysis] = []
        for r in candidates:
            ev = checks.get(r.ticker) or ce._unavailable(f"no result for {r.ticker}")
            r.event_check = ev.to_dict()
            if ev.status == "clear":
                kept.append(r)
                continue
            if ev.blocks_new_money_outright:
                self._event_rejections.append({
                    "ticker": r.ticker, "company": r.company, "sector": r.sector,
                    "price": float(r.entry_price or 0.0), "final_action": FinalAction.AVOID.value,
                    "code": ev.code, "reason": ev.text, "event_check": ev.to_dict(),
                })
                logger.info(f"Corporate-event gate: {r.ticker} AVOID ({ev.code}): {ev.text}")
                continue
            apply_allocation(r, 0.0, self.capital)
            r.final_action = FinalAction.WATCHLIST.value
            r.final_action_reasons = list(r.final_action_reasons or []) + [ev.text]
            self._event_watch.append(r)
            logger.info(f"Corporate-event gate: {r.ticker} held at Watch ({ev.code}): {ev.text}")
        self._event_kept = list(kept)
        self._gate_stats['gate1b_event_clear'] = len(kept)
        self._gate_stats['event_avoid'] = len(self._event_rejections)
        self._gate_stats['event_watch'] = len(self._event_watch)
        return kept

    def _publish_event_held(self) -> List["StockAnalysis"]:
        """[I2] The book exits before selection with nothing funded: the names the
        event gate held at Watch are still the published rows, at $0, with their
        decision frozen exactly as on the normal exit. They used to be appended
        only at the end of the book, so an empty book dropped them."""
        held = list(getattr(self, '_event_watch', []) or [])
        n = len(self.scan_results or [])
        self._report_progress("book", n, n)                 # [P1] the book is finished
        decisions = [decision_for_scan_result(rec) for rec in held]
        for rec, decision in zip(held, decisions):
            rec.decision = decision.to_dict()
        if isinstance(getattr(self, '_gate_stats', None), dict):
            self._gate_stats['final_recommendations'] = len(held)
            self._gate_stats['funded'] = 0
        if isinstance(getattr(self, '_universe_counts', None), dict):
            self._universe_counts['final_recommendation_count'] = len(held)
        if decisions:
            logger.info(f"Published {len(held)} name(s) held at Watchlist by the corporate-event gate; no Buy or Accumulate.")
            from tracking.ledger import record_decisions
            record_decisions(decisions, context={"publication": "final_scan"})
        return held

    def get_top_recommendations(self, max_positions: int = 5,
                                diversify_sectors: bool = True,
                                allow_sector_cap_relaxation: Optional[bool] = None
                                ) -> List[StockAnalysis]:
        """
        Get top trading recommendations with quality gates
        
        **QUALITY GATES (Investment Committee Standard):** every cut-off below
        is read from ``engine.thresholds`` and published at /api/thresholds.

        - G2 risk grade: ``BOOK.BANNED_GRADES`` excluded.
        - G3 score: at least max(``SCORE.ACCUMULATE``, pool median); the median
          is skipped when the pool is at or below ``BOOK.SMALL_POOL_FOR_SCORE_FLOOR``.
        - G4 net R:R: at least ``RR.SCANNER_MIN`` on the scanner's own levels.
        - G5 conviction: ``BOOK.CONVICTION_FLOOR_LARGE`` stars when the pool has
          ``BOOK.LARGE_POOL_FOR_CONVICTION`` or more names, else ``_SMALL``.
        - G6 cost: expected return at least ``BOOK.COST_VIABILITY_MULTIPLE`` x
          the round-trip cost.
        - Sector cap: ``TradingConfig.effective_max_sector_pct(capital)``.

        ``allow_sector_cap_relaxation`` opts into concentrating a thin
        opportunity set into one sector. None follows
        ``TradingConfig.ALLOW_SECTOR_CONCENTRATION`` (default False): the
        diversification mandate binds and a thin pool returns fewer positions,
        leaving the balance in cash.
        """

        # [FUNNEL-STATS] Track per-gate survivor counts so the webapp can render
        # a transparent funnel when the final recommendation set is small/empty.
        self._gate_stats = {
            'opportunities': len(self.scan_results or []),
            'gate1_actionable': 0,
            'gate2_risk_safe': 0,
            'gate3_score_qualified': 0,
            'gate4_rr_qualified': 0,
            'gate5_conviction': 0,
            'gate6_cost_viable': 0,
            'gate7_sector_cap': 0,
            'final_recommendations': 0,
            'dynamic_min_score': None,
            'min_rr': None,
            'min_conviction': None,
            'sector_cap_relaxed': False,
            # True when a thin pool met the old auto-relax condition but the
            # cap was held because nobody opted in.
            'sector_cap_binding_on_thin_pool': False,
            # True when the conviction floor emptied the pool and the survivors
            # were published flagged instead (see Gate #5).
            'conviction_relaxed': False,
            'score_floor_relaxed': False,
            'backfilled': 0,
            'funded': 0,
        }
        self._book_counts: Dict[str, Tuple[int, int]] = {}
        self._book_thresholds: Dict[str, Dict[str, Any]] = {}
        # [I2] Held names belong to this book only: never a previous call's.
        self._event_watch = []
        self._rc_progress = [0, 0]                 # [P1] Research checks done / total
        self._selection_skips = {}                 # [P3] why selection turned a name down
        self._book_entrants = []
        self._selection_pool = []
        self._selection_stop = None
        self._event_kept = []
        self._same_issuer_skips = {}

        self._report_progress("book", 0, len(self.scan_results or []))
        if allow_sector_cap_relaxation is None:
            allow_sector_cap_relaxation = TradingConfig.ALLOW_SECTOR_CONCENTRATION
        if max_positions <= 0:
            self._report_progress("book", 0, 0)
            return []
        if not self.scan_results:
            logger.warning("No scan results available")
            # [A03] Event coverage is published for the scanned universe even
            # when no name survived to become a row.
            self._apply_event_gate([], scanned=[], universe=getattr(self, '_scanned_universe', None))
            self._report_progress("book", 0, 0)
            return []

        # ═══════════════════════════════════════════════════════════════
        # QUALITY GATE #1: Filter to actionable signals
        # ═══════════════════════════════════════════════════════════════
        actionable_signals = [SignalType.STRONG_BUY.value, SignalType.BUY.value, SignalType.ACCUMULATE.value]
        # The resolved action binds: a scanner BUY the decision core vetoed
        # (WATCHLIST / AVOID) or an invalid row is never new-money eligible.
        actionable_results = [
            r for r in self.scan_results
            if r.signal in actionable_signals
            and (not r.final_action or r.final_action in actionable_signals)
            and not r.is_invalid
        ]
        self._gate_stats['gate1_actionable'] = len(actionable_results)
        self._book_entrants = list(actionable_results)     # [Picks table] every name that reached the book
        self._book_stage('actionable', len(self.scan_results), len(actionable_results))

        logger.info(f"Quality Gate #1: {len(actionable_results)} actionable from {len(self.scan_results)} total")

        # QUALITY GATE #1b [F1]: corporate events. A pending acquisition or a
        # delisting is AVOID (listed with its reason, no levels); a check that
        # cannot be completed keeps the name out of funding (fail closed).
        _n_actionable = len(actionable_results)
        actionable_results = self._apply_event_gate(actionable_results, scanned=list(self.scan_results or []),
                                                    universe=getattr(self, '_scanned_universe', None))
        self._book_stage('event', _n_actionable, len(actionable_results))

        # ═══════════════════════════════════════════════════════════════
        # QUALITY GATE #2: Risk Grade Filter (BOOK.BANNED_GRADES excluded)
        # ═══════════════════════════════════════════════════════════════
        BANNED_GRADES = set(_T.BOOK.BANNED_GRADES)
        safe_results = [r for r in actionable_results if r.risk_grade not in BANNED_GRADES]
        self._gate_stats['gate2_risk_safe'] = len(safe_results)
        self._book_stage('risk_grade', len(actionable_results), len(safe_results))

        excluded_risky = len(actionable_results) - len(safe_results)
        if excluded_risky > 0:
            logger.info(f"Quality Gate #2: Excluded {excluded_risky} high-risk stocks "
                        f"(Grade {'/'.join(sorted(BANNED_GRADES))})")

        
        # ═══════════════════════════════════════════════════════════════
        # QUALITY GATE #3: Minimum Score (Adaptive - Top 50% of scores)
        # ═══════════════════════════════════════════════════════════════
        # Use percentile-based threshold that adapts to market conditions,
        # with an absolute new-money floor aligned to TradingAnalysis.
        # [TINY-POOL RELAX] If the safe-pool is already small (≤8), the median
        # is statistically meaningless and tends to over-cut. Fall back to the
        # absolute floor only — the floor itself is the institutional minimum.
        if safe_results:
            scores = sorted([r.overall_score for r in safe_results], reverse=True)
            logger.info(f"Score Distribution: Min={min(scores):.1f}, Avg={sum(scores)/len(scores):.1f}, Max={max(scores):.1f}")

            if len(safe_results) <= _T.BOOK.SMALL_POOL_FOR_SCORE_FLOOR:
                MIN_SCORE = TradingConfig.ACCUMULATE_SCORE
                self._gate_stats['score_floor_relaxed'] = True
                logger.info(
                    f"Dynamic MIN_SCORE relaxed to floor {MIN_SCORE:.1f} "
                    f"(pool small: {len(safe_results)} ≤ {_T.BOOK.SMALL_POOL_FOR_SCORE_FLOOR}, percentile unreliable)"
                )
            else:
                median_idx = len(scores) // 2
                percentile_threshold = scores[median_idx] if median_idx < len(scores) else scores[-1]
                MIN_SCORE = max(TradingConfig.ACCUMULATE_SCORE, percentile_threshold)
                logger.info(f"Dynamic MIN_SCORE set to {MIN_SCORE:.1f} (50th percentile, floor {TradingConfig.ACCUMULATE_SCORE:.1f})")
        else:
            MIN_SCORE = TradingConfig.ACCUMULATE_SCORE

        self._gate_stats['dynamic_min_score'] = float(MIN_SCORE)
        qualified_results = [r for r in safe_results if r.overall_score >= MIN_SCORE]
        self._gate_stats['gate3_score_qualified'] = len(qualified_results)
        self._book_stage('score', len(safe_results), len(qualified_results), value=float(MIN_SCORE),
                         relaxed_to_floor=bool(self._gate_stats['score_floor_relaxed']))
        
        excluded_score = len(safe_results) - len(qualified_results)
        if excluded_score > 0:
            logger.info(f"Quality Gate #3: Excluded {excluded_score} stocks with score <{MIN_SCORE:.1f}, keeping top {len(qualified_results)}")
        
        # ═══════════════════════════════════════════════════════════════
        # QUALITY GATE #4: Minimum Risk/Reward (RR.SCANNER_MIN after execution costs)
        # ═══════════════════════════════════════════════════════════════
        MIN_RR = TradingConfig.RISK_REWARD_MIN

        
        if qualified_results:
            rr_values = [r.risk_reward_ratio for r in qualified_results]
            logger.info(f"R:R Distribution: Min={min(rr_values):.1f}, Avg={sum(rr_values)/len(rr_values):.1f}, Max={max(rr_values):.1f}")
        
        _n_score = len(qualified_results)
        qualified_results = [r for r in qualified_results if r.risk_reward_ratio >= MIN_RR]
        self._gate_stats['gate4_rr_qualified'] = len(qualified_results)
        self._book_stage('risk_reward', _n_score, len(qualified_results), value=float(MIN_RR))
        self._gate_stats['min_rr'] = float(MIN_RR)

        logger.info(f"Quality Gate #4: {len(qualified_results)} stocks with R:R ≥{MIN_RR}:1")        
        # ═══════════════════════════════════════════════════════════════
        # QUALITY GATE #5: Minimum Conviction (ADAPTIVE based on scan size)
        # ═══════════════════════════════════════════════════════════════
        # FIX: Adaptive conviction threshold with FLOOR of 2 (never allow 1-star)
        num_candidates = len(qualified_results)
        if num_candidates >= _T.BOOK.LARGE_POOL_FOR_CONVICTION:
            MIN_CONVICTION = _T.BOOK.CONVICTION_FLOOR_LARGE  # large pool
        else:
            MIN_CONVICTION = _T.BOOK.CONVICTION_FLOOR_SMALL  # small/medium pool (floor, never 1)
        
        conviction_filtered = [r for r in qualified_results if getattr(r, 'conviction', 3) >= MIN_CONVICTION]
        self._gate_stats['gate5_conviction'] = len(conviction_filtered)
        self._gate_stats['min_conviction'] = int(MIN_CONVICTION)

        logger.info(f"Quality Gate #5: {len(conviction_filtered)} stocks with conviction ≥{MIN_CONVICTION}/5 (adaptive threshold)")
        
        # [Audit #14] The conviction floor binds whether it removes some names or
        # all of them. When it emptied the pool the survivors used to be funded
        # anyway ("published flagged"), so the gate only ever bound when it did
        # not matter, and a weak day still produced funded picks. A funding
        # threshold is never loosened (PRODUCT.md): an empty book is the honest
        # answer, and the gate stage records how many names it held back.
        if not conviction_filtered and qualified_results:
            logger.warning(
                f"Conviction floor ≥{MIN_CONVICTION}/5 removed all "
                f"{len(qualified_results)} remaining candidate(s); nothing is funded."
            )
        qualified_results = conviction_filtered

        self._book_stage('conviction', num_candidates, len(qualified_results), value=int(MIN_CONVICTION),
                         relaxed=bool(self._gate_stats['conviction_relaxed']))
        if not qualified_results:
            logger.warning(f"❌ NO STOCKS PASSED ALL QUALITY GATES")
            logger.warning(f"   Gates: Score≥{MIN_SCORE}, R:R≥{MIN_RR}:1, Risk=A-C, Conviction≥{MIN_CONVICTION}")
            return self._publish_event_held()


        # Sort by score descending for best picks first
        # [S5-5] Pre-momentum mode ranks on the early-momentum classifier;
        # re-sorting on score alone discarded that ordering. Everything else
        # ranks on ranking_score (see normalize_scores).
        if getattr(self, 'current_scan_type', '') == 'pre_momentum':
            qualified_results = sorted(
                qualified_results,
                key=lambda x: (-x.early_momentum_score, -x.ranking_score, x.ticker),
            )
        else:
            qualified_results = sorted(
                qualified_results,
                key=lambda x: (-(x.ranking_score or x.overall_score), -x.overall_score, x.ticker),
            )

        recommendations = []
        sector_allocations = {}  # Track $ per sector
        ticker_roots_used = set()
        self._same_issuer_skips = {}
        issuer_first: Dict[str, str] = {}      # [I7] issuer key -> the first ticker that took it
        issuer_held = set()                    # [D2] issuers whose class the Research check held back

        def _issuer_key(r: StockAnalysis) -> str:
            """[I7] One key per issuer: the SEC CIK the event check resolved, so
            two share classes count once against every cap. Without a CIK the
            ticker (less its exchange suffix) is its own issuer, as before."""
            cik = (getattr(r, 'event_check', None) or {}).get('cik')
            return f"cik:{cik}" if cik else r.ticker.replace('.NS', '').replace('.BO', '')
        total_allocated = 0.0
        
        # ═══════════════════════════════════════════════════════════════
        # CAPITAL ALLOCATION FIX: Percentage-based sector limits
        # ═══════════════════════════════════════════════════════════════
        available_capital = self.capital * self.market_regime.recommended_exposure
        target_per_position = available_capital / max_positions
        # [FIX] Use effective cap (auto-scales for small accounts) so a $1,500
        # account isn't structurally locked out of $200+ mega-caps.
        effective_max_pct = TradingConfig.effective_max_position_pct(self.capital)
        max_position_dollars = self.capital * effective_max_pct
        if effective_max_pct > TradingConfig.MAX_POSITION_PCT:
            logger.info(
                f"💡 Small-account cap scaling: per-position cap raised "
                f"{TradingConfig.MAX_POSITION_PCT:.0%} → {effective_max_pct:.0%} "
                f"(capital ${self.capital:,.0f} < ${TradingConfig.SMALL_ACCOUNT_THRESHOLD:,.0f})"
            )
        MAX_SECTOR_PCT = TradingConfig.effective_max_sector_pct(self.capital)
        if MAX_SECTOR_PCT != TradingConfig.MAX_SECTOR_PCT:
            logger.info(
                f"💡 Adaptive sector cap: {MAX_SECTOR_PCT:.0%} "
                f"(profile='{TradingConfig.SECTOR_CAP_PROFILE}', "
                f"capital ${self.capital:,.0f} < ${TradingConfig.SMALL_ACCOUNT_THRESHOLD:,.0f})"
            )
        correlation_hist_cache: Dict[str, pd.DataFrame] = {}

        # [M13 FIX] Sector limits are expressed against TOTAL capital, not
        # exposure-adjusted capital. Dividing by `available_capital` meant a
        # documented "25% sector cap" was really 21.25% of the account at 0.85
        # exposure — and it silently tightened and loosened with the regime, so
        # the stated limit was never the enforced one.
        sector_cap_base = self.capital

        def remaining_sector_capacity(sector: str) -> float:
            """Dollars still available in `sector` before hitting the cap."""
            current = sector_allocations.get(sector, 0.0)
            cap_dollars = MAX_SECTOR_PCT * sector_cap_base
            return max(0.0, cap_dollars - current)

        def check_sector_limit(sector, new_allocation, phase="normal"):
            """Check if adding new allocation would exceed the sector limit."""
            current_sector_allocation = sector_allocations.get(sector, 0)
            new_total = current_sector_allocation + new_allocation
            sector_pct = new_total / sector_cap_base if sector_cap_base > 0 else 0
            return sector_pct <= MAX_SECTOR_PCT

        def _get_hist_for_correlation(ticker: str) -> pd.DataFrame:
            """Return a FIXED-LENGTH return window for correlation work.

            [M12 FIX] Scanned names supplied their cached 1y history while the
            fallback fetched 6mo, so the same correlation matrix mixed two
            different sample windows — the pairwise numbers were not comparable
            with each other. Every input is now truncated to the same trailing
            window, whatever its source.
            """
            if ticker in correlation_hist_cache:
                return correlation_hist_cache[ticker]

            hist = pd.DataFrame()
            # Reuse data already fetched during the scan instead of a new call.
            for r in self.scan_results:
                if r.ticker == ticker and getattr(r, '_hist_cache', None) is not None:
                    hist = r._hist_cache
                    break
            if hist is None or hist.empty:
                try:
                    hist = yf.Ticker(ticker).history(
                        period=TradingConfig.CORRELATION_PERIOD,
                        auto_adjust=True, actions=False,
                    )
                except Exception:
                    hist = pd.DataFrame()

            if hist is None or hist.empty or 'Close' not in hist.columns:
                hist = pd.DataFrame()
            else:
                # One window for everyone.
                hist = hist.tail(TradingConfig.CORRELATION_WINDOW_DAYS)

            correlation_hist_cache[ticker] = hist
            return hist

        def _passes_correlation(candidate_ticker: str, selected: List[StockAnalysis]) -> bool:
            """Portfolio-level correlation gate.

            [M12 FIX] This was a greedy PAIRWISE veto evaluated in score order,
            so whichever name in a sector scored highest arbitrarily blocked
            its peers and portfolio composition depended on scan ordering
            rather than on portfolio risk. It also handled missing history two
            different ways: `return True` (pass) here, while
            check_correlation returned (False, 1.0) (block).

            The gate is now order-independent: it caps the candidate's AVERAGE
            correlation to the existing book, and keeps the pairwise ceiling
            only as a backstop against a near-duplicate holding.
            """
            if not selected:
                return True
            existing_tickers = [r.ticker for r in selected]

            cand_hist = _get_hist_for_correlation(candidate_ticker)
            if cand_hist.empty:
                # No data is a data gap, not an all-clear. Be explicit and
                # consistent: block, and say why.
                logger.info(
                    f"Skipping {candidate_ticker}: no return history available for the "
                    f"correlation check (cannot assess portfolio overlap)"
                )
                self._note_selection_skip(candidate_ticker, "correlation_unknown",
                                          "No price history to check how it moves with the book")
                return False

            cand_returns = cand_hist['Close'].pct_change(fill_method=None).dropna()
            correlations: List[Tuple[str, float]] = []
            for t in existing_tickers:
                other = _get_hist_for_correlation(t)
                if other.empty:
                    continue
                other_returns = other['Close'].pct_change(fill_method=None).dropna()
                aligned = pd.DataFrame(
                    {'a': cand_returns, 'b': other_returns}
                ).dropna()
                if len(aligned) < RiskLimits.MIN_CORRELATION_PERIOD:
                    continue
                corr = aligned['a'].corr(aligned['b'])
                if np.isfinite(corr):
                    correlations.append((t, float(corr)))

            if not correlations:
                logger.info(
                    f"Skipping {candidate_ticker}: not enough overlapping history with the "
                    f"existing book to assess correlation"
                )
                self._note_selection_skip(candidate_ticker, "correlation_unknown",
                                          "Not enough shared price history to check how it moves with the book")
                return False

            avg_corr = float(np.mean([abs(c) for _t, c in correlations]))
            worst_ticker, worst_corr = max(correlations, key=lambda kv: abs(kv[1]))
            worst_corr = abs(worst_corr)

            if avg_corr > TradingConfig.MAX_AVERAGE_CORRELATION:
                logger.info(
                    f"Skipping {candidate_ticker}: average correlation {avg_corr:.2f} to the "
                    f"{len(correlations)}-name book exceeds "
                    f"{TradingConfig.MAX_AVERAGE_CORRELATION:.2f}"
                )
                self._note_selection_skip(
                    candidate_ticker, "correlation",
                    f"Moves with {worst_ticker} in the book (average correlation {avg_corr:.2f}, "
                    f"limit {TradingConfig.MAX_AVERAGE_CORRELATION:.2f})",
                    value=round(avg_corr, 2), limit=TradingConfig.MAX_AVERAGE_CORRELATION, with_ticker=worst_ticker)
                return False
            if worst_corr > RiskLimits.MAX_PAIRWISE_CORRELATION:
                logger.info(
                    f"Skipping {candidate_ticker}: near-duplicate of {worst_ticker} "
                    f"(corr {worst_corr:.2f} > {RiskLimits.MAX_PAIRWISE_CORRELATION:.2f})"
                )
                self._note_selection_skip(
                    candidate_ticker, "correlation",
                    f"Moves almost exactly with {worst_ticker} in the book (correlation {worst_corr:.2f}, "
                    f"limit {RiskLimits.MAX_PAIRWISE_CORRELATION:.2f})",
                    value=round(worst_corr, 2), limit=RiskLimits.MAX_PAIRWISE_CORRELATION, with_ticker=worst_ticker)
                return False
            return True

        def _passes_portfolio_var(candidate: StockAnalysis, candidate_allocation: float,
                                  selected: List[StockAnalysis]) -> bool:
            """Projected aggregate portfolio VaR check including correlation structure."""
            try:
                projected_positions = [r for r in selected if getattr(r, 'allocation', 0) > 0]
                projected_positions.append(candidate)

                allocations = {r.ticker: float(getattr(r, 'allocation', 0) or 0) for r in selected}
                allocations[candidate.ticker] = float(candidate_allocation)

                tickers = [r.ticker for r in projected_positions if allocations.get(r.ticker, 0) > 0]
                if not tickers:
                    return True

                returns_map: Dict[str, pd.Series] = {}
                for t in tickers:
                    hist = _get_hist_for_correlation(t)
                    if not hist.empty and 'Close' in hist.columns:
                        rets = hist['Close'].pct_change().dropna()
                        if len(rets) >= 30:
                            returns_map[t] = rets

                portfolio_var_dollars = 0.0
                if len(returns_map) >= 2:
                    common_df = pd.DataFrame({t: returns_map[t] for t in tickers if t in returns_map}).dropna()
                    if len(common_df) >= 20:
                        cov = common_df.cov().values
                        weights = np.array([allocations[t] for t in common_df.columns], dtype=float)
                        total_dollars = np.sum(weights)
                        if total_dollars > 0:
                            w_frac = weights / total_dollars
                            portfolio_std_pct = float(np.sqrt(np.dot(w_frac, np.dot(cov, w_frac))))
                            portfolio_var_dollars = total_dollars * 2.33 * portfolio_std_pct

                if portfolio_var_dollars <= 0:
                    indiv_vars = []
                    for r in projected_positions:
                        alloc = allocations.get(r.ticker, 0)
                        if alloc <= 0:
                            continue
                        annual_vol = float(getattr(r.risk_metrics, 'volatility', 0) or 0)
                        if annual_vol <= 0:
                            hist = _get_hist_for_correlation(r.ticker)
                            if not hist.empty and 'Close' in hist.columns:
                                annual_vol = float(hist['Close'].pct_change().dropna().std() * np.sqrt(252))
                        daily_vol = annual_vol / np.sqrt(252) if annual_vol > 0 else 0
                        indiv_vars.append(alloc * daily_vol * 2.33)

                    if indiv_vars:
                        portfolio_var_dollars = float(np.sqrt(np.sum(np.square(indiv_vars))))

                portfolio_var_pct = portfolio_var_dollars / self.capital if self.capital > 0 else 1.0
                passes = portfolio_var_pct <= TradingConfig.MAX_PORTFOLIO_RISK
                if not passes:
                    logger.info(
                        f"Skipping {candidate.ticker}: projected portfolio VaR {portfolio_var_pct:.1%} "
                        f"> {TradingConfig.MAX_PORTFOLIO_RISK:.0%}"
                    )
                return passes
            except Exception as e:
                logger.debug(f"Portfolio VaR check failed for {candidate.ticker}: {e}")
                return True

        def _passes_portfolio_stop_risk(candidate: StockAnalysis, candidate_allocation: float,
                                        selected: List[StockAnalysis]) -> bool:
            """[B4 FIX] Cap the book's total risk-if-every-stop-fills.

            This is the loss the portfolio actually takes in the scenario the
            stops are there to define, and until now nothing bounded it.
            """
            try:
                def stop_risk(rec: StockAnalysis, allocation: float) -> float:
                    entry = float(getattr(rec, 'entry_price', 0.0) or 0.0)
                    stop = float(getattr(rec, 'stop_loss', 0.0) or 0.0)
                    if entry <= 0 or stop <= 0 or stop >= entry or allocation <= 0:
                        return 0.0
                    return allocation * ((entry - stop) / entry)

                total = sum(
                    stop_risk(r, float(getattr(r, 'allocation', 0.0) or 0.0)) for r in selected
                ) + stop_risk(candidate, candidate_allocation)

                risk_pct = total / self.capital if self.capital > 0 else 1.0
                if risk_pct > TradingConfig.MAX_PORTFOLIO_RISK:
                    logger.info(
                        f"Skipping {candidate.ticker}: total stop risk would reach "
                        f"{risk_pct:.1%} of capital, above the "
                        f"{TradingConfig.MAX_PORTFOLIO_RISK:.0%} portfolio budget"
                    )
                    self._note_selection_skip(
                        candidate.ticker, "stop_risk",
                        f"Would bring the book's loss at stops to {risk_pct:.1%} of capital, over the "
                        f"{TradingConfig.MAX_PORTFOLIO_RISK:.0%} limit")
                    return False
                return True
            except Exception as e:
                logger.debug(f"Portfolio stop-risk check failed for {candidate.ticker}: {e}")
                return True

        # ═══════════════════════════════════════════════════════════════
        # [FIX #12] Transaction cost viability filter
        # Standard practice: expected return must exceed 2x round-trip cost
        # to justify opening the position (covers slippage + commission).
        # ═══════════════════════════════════════════════════════════════
        cost_viable = []
        for r in qualified_results:
            # Expected return from entry to target
            expected_return_pct = safe_divide(r.price_target - r.entry_price, r.entry_price, 0.0)
            # Round-trip cost in percent
            rt_cost_pct = getattr(r, 'transaction_cost_bps', _T.BOOK.DEFAULT_ROUND_TRIP_BPS) / 10000
            _mult = _T.BOOK.COST_VIABILITY_MULTIPLE
            if expected_return_pct >= rt_cost_pct * _mult:
                cost_viable.append(r)
            else:
                logger.debug(
                    f"Skipping {r.ticker}: expected return {expected_return_pct:.2%} "
                    f"< {_mult:g}x round-trip cost {rt_cost_pct * _mult:.2%}"
                )
        
        _n_conviction = len(qualified_results)
        # Owner decision 2026-10-04: the cost gate fails closed. It used to keep
        # every name when none covered its costs.
        excluded_cost = len(qualified_results) - len(cost_viable)
        if excluded_cost > 0:
            logger.info(f"Transaction cost filter: excluded {excluded_cost} stocks "
                        f"(return < {_T.BOOK.COST_VIABILITY_MULTIPLE:g}x costs)")
        qualified_results = cost_viable
        self._gate_stats['gate6_cost_viable'] = len(qualified_results)
        self._book_stage('cost', _n_conviction, len(qualified_results),
                         value=float(_T.BOOK.COST_VIABILITY_MULTIPLE))
        if not qualified_results:
            logger.warning("No candidate's expected return covers its trading costs; no Buy or Accumulate.")
            return self._publish_event_held()

        # A thin pool no longer lifts the cap by itself: concentration is an
        # explicit opt-in. Without it the mandate binds and the shortfall stays
        # in cash. Diversification OFF lifts the cap (the only selection loop
        # runs either way).
        MAX_SECTOR_PCT, _cap_relaxed = self._resolve_sector_cap(
            base_cap=MAX_SECTOR_PCT,
            qualified_count=len(qualified_results),
            diversify_sectors=diversify_sectors,
            allow_relaxation=allow_sector_cap_relaxation,
        )
        self._gate_stats['sector_cap_relaxed'] = _cap_relaxed
        if _cap_relaxed:
            logger.info(
                f"Sector cap relaxed to 100% by explicit request (qualified pool "
                f"only {len(qualified_results)} ≤ 4)."
            )
        elif diversify_sectors and len(qualified_results) <= 4:
            self._gate_stats['sector_cap_binding_on_thin_pool'] = True
            logger.info(
                f"Sector cap held at {MAX_SECTOR_PCT:.0%} on a thin pool of "
                f"{len(qualified_results)}; expect fewer positions and unallocated cash."
            )

        def _try_add(analysis: StockAnalysis) -> bool:
            """Size `analysis` against every portfolio limit and add it to
            the book. Returns True when it was added."""
            nonlocal total_allocated
            root_ticker = _issuer_key(analysis)

            if root_ticker in ticker_roots_used:
                # [I7] A second share class of an issuer already in the book.
                if root_ticker.startswith('cik:'):
                    first = issuer_first.get(root_ticker, '')
                    held = root_ticker in issuer_held
                    self._same_issuer_skips[analysis.ticker] = first
                    self._note_selection_skip(
                        analysis.ticker, "same_issuer",
                        (f"Held back with {first}: same company, and the Research check held {first} back, "
                         f"so no share class of it gets new money") if held else
                        f"Same company as {first}, which ranked ahead; one share class per company",
                        as_ticker=first, held_by_research=held)
                    self._record_zero_allocation(analysis.ticker, 'same_issuer')
                    logger.info(f"Skipping {analysis.ticker}: same issuer ({root_ticker}) as {first} in the book")
                return False

            if not _passes_correlation(analysis.ticker, recommendations):
                return False

            remaining = available_capital - total_allocated
            # [ITEM 6] Cap the target by remaining SECTOR capacity too, so we
            # resize the position to fit instead of skipping the name when its
            # sector is nearly full.
            sector_room = remaining_sector_capacity(analysis.sector)

            # [B3 FIX] Start from the RISK-BASED size the analysis derived,
            # not from `available_capital / max_positions`. The equal-weight
            # figure used to overwrite `analysis.allocation` outright, so
            # the volatility targeting, signal and score multipliers, ATR
            # haircut, risk-grade multiplier, FOMC haircut and drawdown
            # throttle all collapsed into a pass/fail gate and every
            # published position was the same size — a 5-star 18%-vol
            # grade-A name got identical dollars to a 2-star 45%-vol
            # grade-C name. The portfolio caps below are ceilings on that
            # size, never a replacement for it.
            risk_sized = float(getattr(analysis, 'allocation', 0.0) or 0.0)
            if risk_sized <= 0:
                self._note_selection_skip(analysis.ticker, "risk_budget", "Its risk budget sized it to nothing")
                self._record_zero_allocation(analysis.ticker, 'risk_budget')
                return False
            position_size = min(
                risk_sized, target_per_position, remaining,
                max_position_dollars, sector_room,
            )
            shares = TradingConfig.size_shares(position_size, analysis.entry_price)

            # [ITEM 6] If the sector cap squeezed the position below one whole
            # share (non-fractional) or below the minimum trade size, skip with
            # an explicit reason rather than a silent zero.
            if sector_room < remaining and position_size < TradingConfig.min_position_size(self.capital):
                logger.info(
                    f"Skipping {analysis.ticker}: only ${sector_room:.0f} of "
                    f"{analysis.sector} sector capacity left (< min trade "
                    f"{self.currency_symbol}{TradingConfig.min_position_size(self.capital):.0f}); "
                    f"sector cap {MAX_SECTOR_PCT:.0%}"
                )
                self._note_selection_skip(
                    analysis.ticker, "sector_cap",
                    f"Only {self.currency_symbol}{sector_room:,.0f} of {analysis.sector} room left under the "
                    f"{MAX_SECTOR_PCT:.0%} sector cap, below the minimum position")
                self._record_zero_allocation(analysis.ticker, 'sector_cap')
                return False

            # [FIX] Removed unconditional "1-share override". The previous
            # behaviour silently bought 1 share even when its dollar value
            # exceeded MAX_POSITION_PCT, only to have the validator reject
            # it downstream — guaranteeing 0-position outcomes when any
            # candidate's price exceeded `capital * MAX_POSITION_PCT`.
            # Now we override to 1 share ONLY if that share also respects
            # the (effective) position-size cap.
            if shares == 0 and analysis.entry_price > 0 and not TradingConfig.FRACTIONAL_SHARES:
                # [A06] One share, under the shared policy (position cap and
                # per-trade risk ceiling), and only if it fits the remaining
                # capital and sector room.
                if (analysis.entry_price <= remaining and analysis.entry_price <= sector_room
                        and TradingConfig.one_share_allowed(analysis, self.capital)):
                    shares = 1
                else:
                    cap_pct = analysis.entry_price / self.capital if self.capital > 0 else 1.0
                    logger.info(
                        f"Skipping {analysis.ticker}: 1-share cost ${analysis.entry_price:.2f} "
                        f"= {cap_pct:.1%} of capital does not fit the per-position cap "
                        f"({effective_max_pct:.0%}), the per-trade risk ceiling or the "
                        f"remaining sector room ${sector_room:.0f}."
                    )
                    if analysis.entry_price > remaining:
                        self._note_selection_skip(
                            analysis.ticker, "capital_used",
                            f"One share ({self.currency_symbol}{analysis.entry_price:,.2f}) costs more than the "
                            f"{self.currency_symbol}{remaining:,.0f} of capital left")
                    elif analysis.entry_price > sector_room:
                        self._note_selection_skip(
                            analysis.ticker, "sector_cap",
                            f"One share ({self.currency_symbol}{analysis.entry_price:,.2f}) does not fit the "
                            f"{self.currency_symbol}{sector_room:,.0f} of {analysis.sector} room left")
                    else:
                        self._note_selection_skip(
                            analysis.ticker, "one_share_over_cap",
                            f"One share costs {self.currency_symbol}{analysis.entry_price:,.2f}, {cap_pct:.1%} of "
                            f"capital: over the {effective_max_pct:.0%} position cap or the per-trade risk ceiling",
                            capital_needed=self._capital_for_one_share(analysis))
                    self._record_zero_allocation(
                        analysis.ticker,
                        'whole_share_constraint' if analysis.entry_price <= remaining else 'position_cap',
                    )
                    return False

            if shares > 0:
                actual_allocation = shares * analysis.entry_price

                # [ITEM 6] Sector limit is now enforced by sizing above; this
                # is a final guard. If a whole-share rounding still nudges the
                # sector slightly over, try to resize down one share before
                # giving up.
                if not check_sector_limit(analysis.sector, actual_allocation, "normal"):
                    if not TradingConfig.FRACTIONAL_SHARES and shares > 1:
                        shares -= 1
                        actual_allocation = shares * analysis.entry_price
                    if not check_sector_limit(analysis.sector, actual_allocation, "normal"):
                        current_pct = sector_allocations.get(analysis.sector, 0) / sector_cap_base * 100
                        logger.info(
                            f"Skipping {analysis.ticker}: {analysis.sector} at {current_pct:.0f}% "
                            f"would exceed sector limit {MAX_SECTOR_PCT*100:.0f}%"
                        )
                        self._record_zero_allocation(analysis.ticker, 'sector_cap')
                        return False

                if not _passes_portfolio_var(analysis, actual_allocation, recommendations):
                    self._note_selection_skip(analysis.ticker, "portfolio_var",
                                              "Would push the book's value at risk over its limit")
                    return False

                # [B4 FIX] Aggregate stop risk gate. MAX_PORTFOLIO_RISK
                # previously governed only VaR; nothing capped the sum of
                # `shares x (entry - stop)` — the money actually at risk if
                # every stop fills. The summary panel printed that number
                # but no gate read it.
                if not _passes_portfolio_stop_risk(analysis, actual_allocation, recommendations):
                    self._record_zero_allocation(analysis.ticker, 'risk_budget')
                    return False

                # [W11 FIX] allocation / shares / position_pct written together.
                # [A06] The final sizing step: an under-minimum result is no trade.
                if apply_allocation(analysis, actual_allocation, self.capital, one_share=True) <= 0:
                    self._record_zero_allocation(analysis.ticker, 'min_trade_size')
                    return False

                # Final Quality Gate Check
                validation = validate_recommendation(analysis, self.capital)
                if not validation['is_valid']:
                    logger.warning(f"Skipping {analysis.ticker} due to validation errors: {validation['errors']}")
                    self._note_selection_skip(analysis.ticker, "validation",
                                              f"Failed the final order check: {'; '.join(map(str, validation['errors']))}")
                    return False
                if validation['warnings']:
                    logger.debug(f"Warnings for {analysis.ticker}: {validation['warnings']}")

                recommendations.append(analysis)
                ticker_roots_used.add(root_ticker)
                issuer_first.setdefault(root_ticker, analysis.ticker)
                total_allocated += actual_allocation
                sector_allocations[analysis.sector] = sector_allocations.get(analysis.sector, 0) + actual_allocation

                sector_pct = sector_allocations[analysis.sector] / sector_cap_base * 100
                logger.info(
                    f"✓ Added {analysis.ticker}: {fmt_shares(shares)} shares @ "
                    f"{fmt_price(analysis.entry_price, self.currency_symbol)} = "
                    f"{self.currency_symbol}{actual_allocation:,.0f} "
                    f"({analysis.sector} {sector_pct:.0f}%)"
                )
                return True
            else:
                logger.info(
                    f"Skipping {analysis.ticker}: insufficient capital for 1 share "
                    f"(price ${analysis.entry_price:.2f} > remaining ${remaining:.2f})"
                )
                self._note_selection_skip(
                    analysis.ticker, "capital_used",
                    f"One share ({self.currency_symbol}{analysis.entry_price:,.2f}) costs more than the "
                    f"{self.currency_symbol}{remaining:,.0f} of capital left")
                self._record_zero_allocation(analysis.ticker, 'risk_budget')
                return False

        self._selection_pool = list(qualified_results)
        for analysis in qualified_results:
            if len(recommendations) >= max_positions or total_allocated >= available_capital:
                self._selection_stop = ("book_full" if len(recommendations) >= max_positions else "capital_used",
                                        max_positions)
                break
            _try_add(analysis)
        # [A04] The first selection pass; backfill below is counted on its own.
        self._gate_stats['gate7_sector_cap'] = len(recommendations)
        _backfilled = 0

        # ═══════════════════════════════════════════════════════════════
        # [FIX #3] Removed pass 2 (relaxed sector limits) and pass 3 (fill pass).
        # A "constraint relaxation" loop that overrides diversification defeats the
        # purpose of risk management.  Under-deployment is an acceptable outcome
        # when the opportunity set is thin.
        # ═══════════════════════════════════════════════════════════════

        # ═══════════════════════════════════════════════════════════════
        # [B5 FIX] No optimizer pass over the risk-based sizes.
        #
        # Markowitz on trailing sample means (error maximisation) was replaced
        # by an inverse-volatility "refinement" — which had the same structural
        # flaw in a milder form: it kept only the TOTAL dollars and re-split
        # them by 1/vol, discarding each name's own risk-based size (stop
        # distance, signal, score). It ran AFTER the sector cap, the portfolio
        # stop-risk gate and the VaR gate and re-checked none of them, and its
        # cap added POSITION_CAP_BUFFER — so it produced 10.5% positions
        # against a 10% cap, a 31.5% sector against 25%, an ACCUMULATE
        # carrying twice its risk budget, and a $900 stock sized to 0 shares
        # but still published as BUY.
        #
        # The risk-based size already IS volatility targeting (the stop is
        # ATR-derived, and allocation x stop distance = risk budget), so the
        # sizes checked above are the sizes published.
        # ═══════════════════════════════════════════════════════════════

        # [B2 FIX] Deployment is reported AFTER deep-confirm below, because
        # that step can withdraw capital from a pick. Reporting it here logged
        # "Deployed: $6,395" on a run where reconciliation then zeroed both
        # positions to $0 — the same class of defect as every other number
        # this pass fixed: a figure printed before a later step changed it.

        # [B2 FIX] update_trailing_stops() used to be called here on brand-new
        # recommendations. Ratcheting a trailing stop is meaningless before a
        # position exists — at entry the initial ATR trail set during analysis
        # IS the trailing stop. Ratcheting belongs to a position-monitoring
        # pass that can supply a real high-since-entry.

        final = recommendations[:max_positions]

        # [ITEM 13] Reconcile the published picks with the authoritative deep
        # analyzer so the scanner can never show a BUY where the deep engine
        # (DCF / risk-grade / candlestick) says ACCUMULATE / WATCHLIST.
        # [P1] The book rules are finished; the Research check comes next.
        self._report_progress("book", len(self.scan_results or []), len(self.scan_results or []))
        final = self._deep_confirm_final_picks(final)

        # [S5-3] Capital withdrawn by deep-confirm used to sit idle while the
        # log said "Cash position is appropriate given the opportunity set" —
        # and names skipped only because a now-withdrawn pick occupied their
        # sector / correlation slot were never reconsidered. Rebuild the book
        # from the picks still carrying capital and backfill from the ranked
        # qualified list (bounded: two rounds, each deep-confirmed).
        all_records = list(getattr(self, '_deep_confirm_records', []) or [])
        considered = {r.ticker for r in final}
        for _round in range(2):
            funded = [r for r in final if float(getattr(r, 'allocation', 0.0) or 0.0) > 0]
            withdrawn = [r for r in final if r not in funded]
            if not withdrawn or len(funded) >= max_positions:
                break
            # Rebuild book state from funded picks only.
            recommendations[:] = funded
            total_allocated = sum(float(r.allocation) for r in funded)
            sector_allocations.clear()
            for r in funded:
                sector_allocations[r.sector] = sector_allocations.get(r.sector, 0.0) + float(r.allocation)
            # [D2] Share classes fail closed: an issuer whose class the Research
            # check held back keeps blocking its other classes, so a twin is not
            # funded in its place (each class is the same company and the same
            # risk). Two classes are never funded together.
            ticker_roots_used.clear()
            ticker_roots_used.update(_issuer_key(r) for r in funded + withdrawn)
            issuer_first.clear()
            for r in funded + withdrawn:
                issuer_first.setdefault(_issuer_key(r), r.ticker)
            issuer_held.update(_issuer_key(r) for r in withdrawn)
            added: List[StockAnalysis] = []
            for cand in qualified_results:
                if cand.ticker in considered:
                    continue
                if len(recommendations) >= max_positions or total_allocated >= available_capital:
                    break
                considered.add(cand.ticker)
                if _try_add(cand):
                    added.append(cand)
                    # It is in the book now: drop any earlier "same company" note.
                    self._same_issuer_skips.pop(cand.ticker, None)
                    (getattr(self, '_selection_skips', None) or {}).pop(cand.ticker, None)
            if not added:
                break
            _backfilled += len(added)
            logger.info(
                f"Backfilling {len(added)} pick(s) for capital withdrawn by deep "
                f"confirmation: {', '.join(a.ticker for a in added)}"
            )
            added = self._deep_confirm_final_picks(added)
            all_records.extend(getattr(self, '_deep_confirm_records', []) or [])
            # Withdrawn picks stay published (at $0, as watchlist context).
            final = funded + added + withdrawn
        self._deep_confirm_records = all_records
        # [F1] Names held out of funding by the event gate are published at $0
        # with the reason, after the funded and withdrawn rows.
        final = final + [r for r in getattr(self, '_event_watch', []) if r not in final]

        # Freeze the published verdict before counting deployed capital. This
        # also clears any residual allocation on a non-fundable final action.
        decisions = [decision_for_scan_result(rec) for rec in final]
        for rec, decision in zip(final, decisions):
            rec.decision = decision.to_dict()

        if hasattr(self, '_gate_stats') and isinstance(self._gate_stats, dict):
            self._gate_stats['backfilled'] = _backfilled
            self._gate_stats['final_recommendations'] = len(final)

        if hasattr(self, '_universe_counts') and isinstance(self._universe_counts, dict):
            self._universe_counts['final_recommendation_count'] = len(final)
            logger.info(f"  Final recommendation count: {len(final)}")

        # Deployment as PUBLISHED — after deep-confirm has had its say.
        deployed = sum(float(getattr(r, 'allocation', 0.0) or 0.0) for r in final)
        funded = sum(1 for r in final if float(getattr(r, 'allocation', 0.0) or 0.0) > 0)
        _selected = int(self._gate_stats.get('gate7_sector_cap', 0)) + _backfilled
        self._book_stage('selection', len(qualified_results), _selected)
        self._book_stage('final_review', _selected, funded)
        self._gate_stats['funded'] = funded
        utilization = deployed / available_capital if available_capital > 0 else 0.0
        logger.info(
            f"Published {len(final)} recommendation(s), {funded} carrying capital | "
            f"Deployed: ${deployed:,.0f} of ${available_capital:,.0f} ({utilization:.0%})"
        )
        withdrawn_now = [
            r for r in final if float(getattr(r, 'allocation', 0.0) or 0.0) <= 0
        ]
        if withdrawn_now:
            logger.info(
                f"{len(withdrawn_now)} pick(s) withdrawn by deep confirmation "
                f"({', '.join(r.ticker for r in withdrawn_now)}); their capital is held as cash."
            )
        elif utilization < 0.70:
            logger.info(
                "Cash position is appropriate given the current opportunity set."
            )

        # The scanner publishes after deep confirmation and portfolio gates.
        # Earlier scan rows are provisional and must not become ledger calls.
        from tracking.ledger import record_decisions
        record_decisions(decisions, context={"publication": "final_scan"})
        return final


    def generate_smart_rationale(self, rec: StockAnalysis) -> Dict[str, str]:
        """Generates detailed, human-like investment analysis."""
        
        # [O4 FIX] Read the ONE numeric horizon rather than substring-matching
        # the label. The old chain tested only "3-6", "6-9" and "6-12", so a
        # 1-2 month or 2-4 month projection fell through to "longer-term hold /
        # 12+ months" — the same pick was simultaneously a swing trade and a
        # multi-year hold.
        months = float(getattr(rec.timeframe_projection, 'horizon_months', 0.0) or 0.0)
        hold_period = describe_horizon(months)
        if months < 2.0:
            timeframe_desc = "short-term swing trade"
        elif months < 7.0:
            timeframe_desc = "medium-term position"
        else:
            timeframe_desc = "longer-term hold"
        
        # Build comprehensive narrative
        narrative_parts = []
        
        # 1. Opening Hook - Why This Stock Now. The decision-core's final
        # action is authoritative; never turn WATCHLIST/HOLD/AVOID into an
        # accumulation pitch merely because it is not a BUY.
        resolved_action = str(getattr(rec, 'final_action', '') or rec.signal or 'WATCHLIST').upper()
        if "STRONG_BUY" in resolved_action:
            signal_label = "STRONG_BUY"
        elif resolved_action == "BUY":
            signal_label = "BUY"
        elif resolved_action == "ACCUMULATE":
            signal_label = "ACCUMULATE"
        elif resolved_action in {"AVOID", "REDUCE"}:
            signal_label = "AVOID"
        else:
            signal_label = "WATCHLIST"
        is_actionable = signal_label in {"STRONG_BUY", "BUY", "ACCUMULATE"}
        
        narrative_parts.append("[bold cyan]WHY NOW[/]\n")
        if signal_label == "STRONG_BUY":
            narrative_parts.append(f"This is a high-conviction {timeframe_desc} opportunity. ")
        elif signal_label == "BUY":
            narrative_parts.append(f"Solid {timeframe_desc} setup with attractive risk/reward. ")
        elif signal_label == "ACCUMULATE":
            narrative_parts.append(f"Patient accumulation opportunity for a {timeframe_desc}. ")
        elif signal_label == "AVOID":
            narrative_parts.append("This stock does not currently meet the new-money risk criteria. ")
        else:
            narrative_parts.append("Research watchlist only; there is no new-money entry recommendation. ")
        
        # **ELITE ENHANCEMENT #4: Add Critical Trading Levels & Position Sizing**
        entry_for_pct = float(rec.entry_price or 0.0)
        upside_pct = ((rec.price_target / entry_for_pct - 1) * 100) if entry_for_pct > 0 else 0.0
        risk_pct = ((rec.stop_loss / entry_for_pct - 1) * 100) if entry_for_pct > 0 else 0.0
        narrative_parts.append(
            f"\n\n[bold cyan]{'📊 TRADING PLAN' if is_actionable else '📊 REFERENCE LEVELS (NOT AN ORDER)'}[/]\n"
        )
        narrative_parts.append(f"• Entry Price:  {self.currency_symbol}{rec.entry_price:.2f}\n")
        narrative_parts.append(f"• Price Target: {self.currency_symbol}{rec.price_target:.2f} ({upside_pct:.1f}% upside)\n")
        narrative_parts.append(f"• Stop Loss:    {self.currency_symbol}{rec.stop_loss:.2f} ({risk_pct:.1f}% risk)\n")
        narrative_parts.append(f"• Risk/Reward:  {rec.risk_reward_ratio:.1f}:1\n")
        narrative_parts.append(f"• Position Size: {self.currency_symbol}{rec.allocation:,.0f} ({rec.position_pct:.1f}% of portfolio)\n")
        narrative_parts.append(
            f"• {'Shares to Buy' if is_actionable else 'New Shares'}: {fmt_shares(rec.shares)} shares\n"
        )
        narrative_parts.append(f"• Confidence: {rec.confidence_tier}")
        if not is_actionable and getattr(rec, 'final_action_reasons', None):
            narrative_parts.append("\n• Decision: " + "; ".join(rec.final_action_reasons))
        
        # 2. Factor Score Breakdown
        narrative_parts.append(f"\n\n[bold cyan]🎯 FACTOR ANALYSIS[/]\n")
        narrative_parts.append(f"• Overall Score:   {rec.overall_score:.1f}/10 {'🔥' if rec.overall_score > 8 else '✅' if rec.overall_score > 7 else '📈'}\n")
        narrative_parts.append(f"• Technical:       {rec.technical_score:.1f}/10 ")
        narrative_parts.append(f"({'Strong' if rec.technical_score > 7 else 'Moderate' if rec.technical_score > 5 else 'Weak'})\n")
        narrative_parts.append(f"• Fundamental:     {rec.fundamental_score:.1f}/10 ")
        narrative_parts.append(f"({'Strong' if rec.fundamental_score > 7 else 'Fair' if rec.fundamental_score > 5 else 'Limited'})\n")
        narrative_parts.append(f"• Momentum:        {rec.momentum_score:.1f}/10 ")
        narrative_parts.append(f"({'Hot' if rec.momentum_score > 7 else 'Building' if rec.momentum_score > 5 else 'Weak'})\n")
        narrative_parts.append(f"• Quality:         {rec.quality_score:.1f}/10 ")
        narrative_parts.append(f"({'Excellent' if rec.quality_score > 7 else 'Good' if rec.quality_score > 5 else 'Average'})\n")
        narrative_parts.append(f"• Value:           {rec.value_score:.1f}/10 ")
        narrative_parts.append(f"({'Cheap' if rec.value_score > 7 else 'Fair' if rec.value_score > 5 else 'Expensive'})")
        
        # 3. Technical Story  
        rsi = rec.technical_indicators.rsi
        macd = rec.technical_indicators.macd
        momentum = rec.technical_indicators.momentum_1m
        
        narrative_parts.append(f"\n\n[bold cyan]📈 TECHNICAL PICTURE[/]\n")
        # [O9 FIX] The narrative used to argue the OPPOSITE of the decision on
        # the same input: RSI > 70 read "signals institutional buying" while
        # _determine_signal_regime_aware was rejecting RSI > 70 as overbought
        # and returning HOLD/AVOID. RSI also carries no information about WHO
        # is trading. The oversold branch asserted "historically precedes
        # mean-reversion bounces" with nothing behind it. Indicator values are
        # now stated as facts, against the thresholds the engine actually uses.
        if rsi < TradingConfig.RSI_OVERSOLD:
            narrative_parts.append(
                f"RSI {rsi:.0f} is below the {TradingConfig.RSI_OVERSOLD} oversold line. "
            )
        elif TradingConfig.RSI_OPTIMAL_LOW <= rsi <= TradingConfig.RSI_OPTIMAL_HIGH:
            narrative_parts.append(
                f"RSI {rsi:.0f} sits in the {TradingConfig.RSI_OPTIMAL_LOW}-"
                f"{TradingConfig.RSI_OPTIMAL_HIGH} band the entry logic prefers. "
            )
        elif rsi > TradingConfig.RSI_OVERBOUGHT:
            narrative_parts.append(
                f"RSI {rsi:.0f} is above the {TradingConfig.RSI_OVERBOUGHT} threshold "
                f"at which the entry logic waits for a pullback. "
            )
        
        if macd > 0 and momentum > 0:
            narrative_parts.append("MACD + momentum both positive = confirmed uptrend. ")
        elif macd > 0:
            narrative_parts.append("MACD turning bullish = early-stage trend. ")
        
        # Price vs moving averages
        price = rec.technical_indicators.current_price
        sma50 = rec.technical_indicators.sma_50
        sma200 = rec.technical_indicators.sma_200
        if price > sma50 and price > sma200:
            narrative_parts.append("Trading above 50-day & 200-day MAs (bullish structure).")
        elif price > sma50:
            narrative_parts.append("Above 50-day MA (short-term uptrend).")
        
        # 4. Fundamental Case with Key Metrics
        pe = rec.fundamental_metrics.pe_ratio
        revenue_growth = rec.fundamental_metrics.revenue_growth
        roe = rec.fundamental_metrics.roe
        profit_margin = rec.fundamental_metrics.profit_margin
        debt_to_equity = rec.fundamental_metrics.debt_to_equity
        div_yield = rec.fundamental_metrics.dividend_yield
        
        narrative_parts.append(f"\n\n[bold cyan]💼 FUNDAMENTAL CASE[/]\n")
        
        if pe > 0:
            if pe < 15:
                narrative_parts.append(f"P/E of {pe:.1f} = undervalued, market hasn't priced in the opportunity. ")
            elif 15 <= pe <= 25:
                narrative_parts.append(f"P/E of {pe:.1f} = fair valuation for growth profile. ")
            elif pe > 40:
                narrative_parts.append(f"P/E of {pe:.1f} = growth premium (market pricing in future). ")
        
        if revenue_growth > 0.20:
            narrative_parts.append(f"Revenue growth: {revenue_growth*100:.0f}% YoY (exceptional). ")
        elif revenue_growth > 0.10:
            narrative_parts.append(f"Revenue growth: {revenue_growth*100:.0f}% (solid). ")
        
        if roe > 0.15:
            # [O3 FIX] roe is a decimal fraction. This printed "ROE: 0.2%" for a
            # 22% ROE while asserting "strong capital deployment" in the same
            # sentence — a metric contradicting its own interpretation on the
            # flagship narrative for the top pick.
            narrative_parts.append(f"ROE: {roe * 100:.1f}% (strong capital deployment). ")
        
        if profit_margin > 0.15:
            narrative_parts.append(f"Profit margin: {profit_margin*100:.1f}% (healthy profitability). ")
        
        # 0.0 is also the value stored for a MISSING D/E and for negative
        # equity, so "0.00 (conservative balance sheet)" was printed for both.
        de_known = bool(rec.fundamental_metrics.data_available.get('debt_to_equity'))
        if not de_known:
            pass
        elif debt_to_equity < 0.5:
            narrative_parts.append(f"Debt/Equity: {debt_to_equity:.2f} (conservative balance sheet). ")
        elif debt_to_equity > 2.0:
            narrative_parts.append(f"Debt/Equity: {debt_to_equity:.2f} (higher leverage - monitor). ")
        
        if div_yield > 0.02:
            narrative_parts.append(f"Dividend yield: {div_yield*100:.2f}% (income + growth).")
        
        # 5. Risk Metrics
        volatility = rec.risk_metrics.volatility
        beta = rec.risk_metrics.beta
        sharpe = rec.risk_metrics.sharpe_ratio
        max_dd = rec.risk_metrics.max_drawdown
        
        narrative_parts.append(f"\n\n[bold yellow]⚠️ RISK ASSESSMENT[/]\n")
        narrative_parts.append(f"• Volatility: {volatility*100:.1f}% ")
        if volatility < 0.25:
            narrative_parts.append("(Low - defensive)\n")
        elif volatility < 0.40:
            narrative_parts.append("(Moderate)\n")
        else:
            narrative_parts.append("(High - size accordingly)\n")
        
        narrative_parts.append(f"• Beta: {beta:.2f} ")
        if beta < 0.8:
            narrative_parts.append("(Low market sensitivity)\n")
        elif beta < 1.2:
            narrative_parts.append("(Market-like)\n")
        else:
            narrative_parts.append("(High market sensitivity)\n")
        
        narrative_parts.append(f"• Sharpe Ratio: {sharpe:.2f} ")
        if sharpe > 1.0:
            narrative_parts.append("(Excellent risk-adjusted returns)\n")
        elif sharpe > 0.5:
            narrative_parts.append("(Good)\n")
        else:
            narrative_parts.append("(Below average)\n")
        
        # [O8 FIX] Realized drawdown is a LOWER bound on future drawdown, not
        # an expectation and certainly not a worst case. "Expect this
        # worst-case drop" was the single most likely sentence on the card to
        # make a reader under-hedge, and it was stated with more confidence
        # than anything else there.
        narrative_parts.append(
            f"• Worst 12-month decline observed: {max_dd*100:.1f}% "
            f"(realized, not a forecast — future drawdowns can exceed it)"
        )
        
        # 6. Sector Context & Catalysts
        sector_pattern = rec.timeframe_projection.sector_rally_pattern
        narrative_parts.append(f"\n\n[bold magenta]🌍 MARKET CONTEXT[/]\n")
        narrative_parts.append(f"Sector: {rec.sector} - {sector_pattern}. ")
        
        # Timeframe projection
        narrative_parts.append(f"Timeline: {rec.timeframe_projection.target_timeframe}. ")
        narrative_parts.append(f"{rec.timeframe_projection.volatility_adjusted_timeline}.")
        
        # 7. Action Steps
        narrative_parts.append(f"\n\n[bold green]✅ ACTION PLAN[/]\n")
        if signal_label == "STRONG_BUY":
            narrative_parts.append(f"1. BUY {fmt_shares(rec.shares)} shares at {fmt_price(rec.entry_price, self.currency_symbol)} or better\n")
            narrative_parts.append(f"2. Set STOP LOSS at {self.currency_symbol}{rec.stop_loss:.2f} (mandatory!)\n")
            narrative_parts.append(f"3. Set PRICE ALERT at {self.currency_symbol}{rec.price_target:.2f} for target\n")
            narrative_parts.append(f"4. Use LIMIT ORDER for better execution\n")
            narrative_parts.append(f"5. Consider scaling in 2-3 tranches if volatile")
        elif signal_label == "BUY":
            narrative_parts.append(f"1. Accumulate {fmt_shares(rec.shares)} shares near {fmt_price(rec.entry_price, self.currency_symbol)}\n")
            narrative_parts.append(f"2. Stop loss: {self.currency_symbol}{rec.stop_loss:.2f}\n")
            narrative_parts.append(f"3. Target: {self.currency_symbol}{rec.price_target:.2f}\n")
            narrative_parts.append(f"4. Build position over 1-2 weeks")
        elif signal_label == "ACCUMULATE":
            narrative_parts.append(f"1. Patient accumulation near {self.currency_symbol}{rec.entry_price:.2f}\n")
            narrative_parts.append(f"2. Dollar-cost average over 2-4 weeks\n")
            narrative_parts.append(f"3. Strict stop: {self.currency_symbol}{rec.stop_loss:.2f}")
        else:
            narrative_parts.append("1. Do not open a new position at this time.\n")
            earn_days = int(getattr(rec, '_cached_earnings_days', -1) or 0)
            if 0 <= earn_days <= 14:
                narrative_parts.append(f"2. Wait for the earnings event in {earn_days} days to clear.\n")
                narrative_parts.append("3. Re-run the analysis after results and guidance are published.")
            else:
                narrative_parts.append("2. Monitor the reference levels and risk factors above.\n")
                narrative_parts.append("3. Re-run the analysis only after the decision blockers improve.")
        
        return {
            "full_analysis": "".join(narrative_parts),
            "timeframe": hold_period,
            "conviction": signal_label
        }

    def _margin_percentile_label(self, margin: float) -> str:
        """[O7 FIX] Real cross-sectional percentile, or no claim at all."""
        stats = getattr(self, '_universe_stats', None)
        population = list(getattr(stats, 'profit_margins', []) or []) if stats else []
        if len(population) < 20:
            return ""   # too small a sample to rank against; say nothing
        pct = UniverseStats._percentile_rank(margin, population)
        return f" (top {max(1, round(100 - pct))}% of {len(population)} scanned)"

    def _risk_gauge(self, rec: StockAnalysis) -> Tuple[str, str, str]:
        """[O10 FIX] Risk from volatility, beta and grade — never conviction."""
        vol = float(getattr(rec.risk_metrics, 'volatility', 0.0) or 0.0)
        beta = float(getattr(rec.risk_metrics, 'beta', 1.0) or 1.0)
        grade = str(getattr(rec, 'risk_grade', 'C') or 'C').upper()

        points = 0
        if vol >= 0.45: points += 2
        elif vol >= 0.30: points += 1
        if abs(beta - 1.0) >= 0.6: points += 1
        if grade in ('D', 'F'): points += 2
        elif grade == 'C': points += 1

        filled = max(1, min(points + 1, 5))
        gauge = "●" * filled + "○" * (5 - filled)
        if points <= 1:
            return "LOW", gauge, "[green]LOW[/]"
        if points <= 3:
            return "MODERATE", gauge, "[yellow]MOD[/]"
        return "HIGH", gauge, "[red]HIGH[/]"

    def _live_warnings(self) -> List[str]:
        """[O12 FIX] Only warnings whose condition is actually true right now.

        A fixed line used to print "SKIP if: Gap >2% at open | SPY down >1% |
        VIX >25" regardless of state, so it could render directly beneath a
        header showing VIX 28 — telling the reader to skip the very trades the
        same page was recommending. The conditions are known at render time.
        """
        out: List[str] = []
        regime = self.market_regime
        vix = float(getattr(regime, 'vix_level', 0.0) or 0.0)
        if vix > TradingConfig.VIX_SKIP_THRESHOLD:
            out.append(
                f"VIX {vix:.1f} is above the {TradingConfig.VIX_SKIP_THRESHOLD:.0f} "
                f"skip threshold — consider standing down"
            )
        if getattr(regime, 'vix_is_proxy', False):
            out.append(
                "VIX is a realized-volatility proxy, not a live implied quote — "
                "treat the regime read as provisional"
            )
        if regime.regime_type in ("BEAR", "VOLATILE"):
            out.append(
                f"{regime.regime_type} regime: exposure capped at "
                f"{regime.recommended_exposure * 100:.0f}%"
            )
        if regime.regime_type == "UNKNOWN":
            out.append(
                "Trend regime UNKNOWN (benchmark history too short) — "
                "factor weights defaulted to the quality-tilted set"
            )
        dd = float(getattr(self, 'current_drawdown', 0.0) or 0.0)
        if dd > 0.05:
            out.append(
                f"Account drawdown {dd:.1%} — position sizes scaled to "
                f"{getattr(self, 'drawdown_multiplier', 1.0):.0%}"
            )
        out.append("Re-check the entry if the open gaps more than 2% against you")
        return out

    def display_elite_minimal(self, recommendations: List[StockAnalysis],
                              verbose: bool = None):
        """Elite Professional Display - Clean, Structured & Organized

        Set ``verbose=True`` (or env ``QUANTA_VERBOSE=1``) to append a debug
        table showing each pick's raw signal, decision-core final_action, the
        rendered display label, and the classification reason.
        """

        if verbose is None:
            verbose = os.getenv("QUANTA_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")

        if not RICH_AVAILABLE:
            self.display_recommendations(recommendations)
            return

        console = Console(width=120)
        # Collected during the summary loop, rendered at the end if verbose.
        debug_rows = []

        if not recommendations:
            console.print(Panel(
                "[bold red]NO HIGH-QUALITY OPPORTUNITIES FOUND[/]\n"
                "[dim]Try adjusting filters or scanning a different market segment.[/]",
                border_style="red", box=box.ROUNDED, padding=(1, 2)
            ))
            return

        # ── HEADER PANEL ────────────────────────────────────────────
        regime = self.market_regime
        rc = "green" if "BULL" in regime.regime_type else "red" if "BEAR" in regime.regime_type else "yellow"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        header = Text()
        header.append("QUANTA ELITE v6.0", style="bold gold1")
        header.append(f"  │  {ts}  │  {self.market}  │  ", style="white")
        header.append(f"Capital: {self.currency_symbol}{self.capital:,.0f}", style="bold white")
        header.append("\n")
        header.append("Regime: ", style="white")
        header.append(regime.regime_type, style=f"bold {rc}")
        header.append(f"  │  VIX: ", style="white")
        header.append(f"{regime.vix_level:.1f}", style="bold cyan")
        header.append(f"  │  Exposure: ", style="white")
        header.append(f"{regime.recommended_exposure * 100:.0f}%", style=f"bold {rc}")
        header.append("  │  yfinance", style="dim")

        console.print(Panel(header, border_style="gold1", box=box.DOUBLE, padding=(0, 1)))

        # ── PORTFOLIO SUMMARY ───────────────────────────────────────
        # Portfolio statistics describe the FUNDED book. Picks that deep
        # confirmation moved to watchlist at $0 are published as context, but
        # used to count toward "Picks", the average R:R and the concentration
        # warning.
        funded = [r for r in recommendations if float(getattr(r, 'allocation', 0) or 0) > 0]
        deployed = sum(float(getattr(r, 'allocation', 0) or 0) for r in funded)
        available = self.capital * regime.recommended_exposure
        util_pct = deployed / available * 100 if available > 0 else 0
        # Money at risk if every stop fills — now a gated quantity (see [B4 FIX]).
        max_dd = sum(getattr(r, 'shares', 0) * (r.entry_price - r.stop_loss) for r in funded)
        dd_pct = max_dd / self.capital * 100 if self.capital > 0 else 0
        avg_rr = (
            sum(r.risk_reward_ratio * r.allocation for r in funded) / deployed
            if deployed > 0 else 0.0
        )

        # Sector breakdown
        sectors = {}
        for r in funded:
            s = r.sector[:14] if len(r.sector) > 14 else r.sector
            sectors[s] = sectors.get(s, 0) + getattr(r, 'allocation', 0)
        sector_str = "  │  ".join(
            f"{s} {v / deployed * 100:.0f}%"
            for s, v in sorted(sectors.items(), key=lambda x: -x[1])
        ) if deployed > 0 else "—"

        sum_tbl = Table(box=None, show_header=False, padding=(0, 1), expand=True)
        sum_tbl.add_column(ratio=1)
        sum_tbl.add_column(ratio=1)
        sum_tbl.add_column(ratio=1)
        sum_tbl.add_column(ratio=1)
        sum_tbl.add_row(
            f"[bold]Deployed[/]\n{self.currency_symbol}{deployed:,.0f} "
            f"({util_pct:.0f}% of {regime.recommended_exposure:.0%} exposure)",
            f"[bold]Risk if all stops fill[/]\n[red]{self.currency_symbol}{max_dd:,.0f} "
            f"({dd_pct:.1f}% of {TradingConfig.MAX_PORTFOLIO_RISK:.0%} budget)[/]",
            f"[bold]Avg R:R (net, $-weighted)[/]\n[cyan]{avg_rr:.1f}x[/]",
            f"[bold]Picks[/]\n{len(funded)} funded"
            + (f" / {len(recommendations)}" if len(funded) != len(recommendations) else "")
        )

        console.print(Panel(
            sum_tbl,
            title="[bold white]PORTFOLIO SUMMARY[/]",
            subtitle=f"[dim]{sector_str}[/]",
            border_style="blue", box=box.ROUNDED, padding=(0, 1)
        ))

        # ── QUICK-REFERENCE TABLE ───────────────────────────────────
        table = Table(
            box=box.SIMPLE_HEAD, header_style="bold white on grey23",
            padding=(0, 1), show_edge=False, expand=True, show_lines=False
        )
        # Price and size columns never truncate: a fixed 120-col console
        # used to render a $4,712.50 stop as "$4,712.…" and RELIANCE.NS as
        # "RELIAN…", and picks beyond the top-5 cards had no share count or
        # dollar size anywhere. Text columns absorb the squeeze instead.
        table.add_column("#", style="dim", justify="center", no_wrap=True)
        table.add_column("TICKER", style="bold cyan", min_width=6, no_wrap=True)
        table.add_column("ACTION", justify="center", no_wrap=True)
        table.add_column("LIMIT", justify="right", no_wrap=True)
        table.add_column("STOP", justify="right", style="red", no_wrap=True)
        table.add_column("TARGET", justify="right", style="green", no_wrap=True)
        table.add_column("R:R", justify="center", no_wrap=True)
        table.add_column("SHARES", justify="right", no_wrap=True)
        table.add_column("SIZE", justify="right", no_wrap=True)
        table.add_column("CONF", justify="center", no_wrap=True)
        table.add_column("SECTOR", style="dim", overflow="ellipsis", no_wrap=True, max_width=14)
        table.add_column("SETUP", overflow="ellipsis", no_wrap=True, max_width=12)

        for i, rec in enumerate(recommendations, 1):
            # Action label — derived from the shared decision-core classifier so
            # the table and the detail cards can never disagree (the published
            # picks are all buy-tier: STRONG_BUY / BUY / ACCUMULATE).
            has_pos = getattr(rec, 'has_existing_position', False)
            label, color, final_value, reason = classify_display_action(
                rec.signal, has_pos, getattr(rec, 'final_action', None))
            action = f"[{color}]{label}[/]"
            debug_rows.append((str(i), rec.ticker, rec.signal, final_value, label, reason))

            is_buy_row = final_value in ("STRONG_BUY", "BUY", "ACCUMULATE") and \
                float(getattr(rec, 'allocation', 0) or 0) > 0

            # [O6 FIX] `min(conviction, 4)` on a 1-5 scale made a 5-star pick
            # indistinguishable from a 4-star one, hiding the top of the
            # conviction scale — exactly the part that should drive attention
            # and size.
            conviction = max(0, min(int(getattr(rec, 'conviction', 3) or 0), CONVICTION_MAX))
            conf_label = f"{conviction}/{CONVICTION_MAX}"

            # [O11 FIX] This column claimed to show CATALYSTS but fell through
            # `"MACD cross" if macd > macd_signal` — a persistent STATE that can
            # hold for months, not a crossover event — then ">200MA", then a
            # P/E. None are catalysts. A catalyst is a dated, identifiable
            # event, and the engine detects real ones (SEC filings, news,
            # earnings) which it then did not show here. The column is now
            # honestly named SETUP and carries the classifier's own label.
            setup_label = str(getattr(rec, 'setup_type', 'Momentum') or 'Momentum')
            cs = self.currency_symbol
            dash = "[dim]—[/]"
            table.add_row(
                str(i), rec.ticker, action,
                fmt_price(rec.entry_price, cs) if is_buy_row else dash,
                fmt_price(rec.stop_loss, cs) if is_buy_row else dash,
                fmt_price(rec.price_target, cs) if is_buy_row else dash,
                f"{rec.risk_reward_ratio:.1f}" if is_buy_row else dash,
                fmt_shares(rec.shares) if is_buy_row else dash,
                f"{cs}{rec.allocation:,.0f}" if is_buy_row else dash,
                conf_label, rec.sector or "—", setup_label,
            )

        console.print(table)

        caveat_text = recommendations[0].signal_strength_caveat if recommendations else "Heuristic indicator, NOT a validated win probability."
        console.print(f"[bold yellow]  ⚠ Confidence metric caveat:[/] [yellow]{caveat_text}[/]")

        # Warnings — only the ones that are actually live. See [O12 FIX].
        for _w in self._live_warnings():
            console.print(f"[yellow]  ⚠ {_w}[/]")
        if len(funded) >= 2:
            sec_cnt = {}
            for rec in funded:
                sec_cnt[rec.sector] = sec_cnt.get(rec.sector, 0) + 1
            conc = [s for s, c in sec_cnt.items() if c >= 2]
            if conc:
                console.print(f"[yellow]  ⚠ CONCENTRATION: {', '.join(conc)} — 2+ positions, correlation risk[/]")
        console.print()
        # ── DETAILED ANALYSIS CARDS (every published pick) ─────────
        console.print(Panel(
            Align.center("[bold white]DETAILED ANALYSIS — TOP PICKS[/]"),
            border_style="bright_white", box=box.HORIZONTALS, padding=(0, 0)
        ))
        console.print()

        for idx, rec in enumerate(recommendations, 1):
            tech = rec.technical_indicators
            current_price = tech.current_price
            conviction_num = max(0, min(int(getattr(rec, 'conviction', 3) or 0), CONVICTION_MAX))
            setup_type = getattr(rec, 'setup_type', 'Momentum')

            upside = ((rec.price_target / rec.entry_price) - 1) * 100
            downside = abs(((rec.stop_loss / rec.entry_price) - 1) * 100)

            # ── Thesis bullets ──
            bullets = []
            rev_g = rec.fundamental_metrics.revenue_growth or 0
            pe = rec.fundamental_metrics.pe_ratio or 0
            margin = rec.fundamental_metrics.profit_margin or 0
            mom = tech.momentum_1m or 0
            if rev_g > 0.10:
                bullets.append(f"Revenue +{rev_g * 100:.0f}% YoY")
            if 0 < pe < 20:
                bullets.append(f"Attractive {pe:.1f}x P/E")
            elif 20 <= pe < 35:
                bullets.append(f"Reasonable {pe:.1f}x P/E for growth")
            if margin > 0.15:
                # [O7 FIX] This used to read
                #   tier = "top 10%" if margin > 0.25 else "top 25%"
                # and render "34% margin (top 10%)". A percentile is a factual
                # claim about a population; asserting one from a hardcoded
                # threshold is a fabricated statistic in an investment
                # document — while UniverseStats._percentile_rank, which can
                # produce a real one, sat unused two hundred lines away.
                pct_label = self._margin_percentile_label(margin)
                bullets.append(f"{margin * 100:.0f}% margin{pct_label}")
            if mom > 0.05:
                bullets.append(f"+{mom * 100:.0f}% momentum this month")
            if rec.fundamental_metrics.dividend_yield and rec.fundamental_metrics.dividend_yield > 0.02:
                bullets.append(f"Dividend {rec.fundamental_metrics.dividend_yield * 100:.1f}%")
            if (rec.quality_score or 0) > 7:
                bullets.append(f"Quality {rec.quality_score * _T.SCORE.TO_100:.0f}/100")
            if not bullets:
                bullets.append("Solid technical setup")

            # ── Setup evidence ──
            # [O11 FIX] Described as CATALYSTS but these are trend STATES.
            # Named for what they are, with the real earnings date surfaced
            # separately below when one is known.
            evidence = []
            if tech.macd > tech.macd_signal:
                evidence.append("MACD above signal")
            if current_price > tech.sma_200:
                evidence.append(f"Above 200MA ({fmt_price(tech.sma_200, self.currency_symbol)})")
            if tech.volume_ratio and tech.volume_ratio > 1.3:
                evidence.append(f"Volume {tech.volume_ratio:.1f}x avg")
            if not evidence:
                evidence.append(f"Holding above 50MA ({fmt_price(tech.sma_50, self.currency_symbol)})")
            catalyst_line = " │ ".join(evidence[:2])

            # ── Execution levels ──
            atr = tech.atr if hasattr(tech, 'atr') and tech.atr else rec.entry_price * 0.02
            add_lvl = rec.entry_price - atr
            t1 = rec.entry_price + (rec.price_target - rec.entry_price) * 0.5
            t1_pct = ((t1 / rec.entry_price) - 1) * 100

            # A pullback level that is actually BELOW today's price: the
            # 20-day MA when it sits within one ATR under entry, else entry -
            # 1 ATR. "Pullback to <today's price>" was not an instruction.
            sma20 = float(getattr(tech, 'sma_20', 0.0) or 0.0)
            pullback_lvl = sma20 if (rec.entry_price - atr) <= sma20 < rec.entry_price else add_lvl

            # ── Position sizing ──
            shares = getattr(rec, 'shares', 0)
            # Laddering 1-2 whole shares into halves/thirds is not executable.
            single_lot = (not TradingConfig.FRACTIONAL_SHARES) and float(shares or 0) < 3
            alloc = getattr(rec, 'allocation', 0)
            max_loss = shares * (rec.entry_price - rec.stop_loss)
            alloc_pct = alloc / self.capital * 100 if self.capital > 0 else 0
            loss_pct = max_loss / self.capital * 100 if self.capital > 0 else 0

            # ── Risk assessment ──
            # [O10 FIX] The gauge used to be driven by CONVICTION
            # (`"LOW" if conviction_num >= 4`), so a 5-star, 45%-volatility,
            # grade-C name rendered as RISK [●●●●●] LOW. Conviction is a
            # confidence measure and risk is a dispersion measure; they are
            # orthogonal and must be able to disagree on screen. Risk now
            # comes from volatility, beta and the risk grade; conviction is
            # shown separately at full scale.
            risk_level, gauge, rlabel = self._risk_gauge(rec)

            risks = []
            if rec.risk_grade in ('C', 'D'):
                risks.append(f"Grade {rec.risk_grade}")
            rsi = tech.rsi or 0
            if rsi > 70:
                risks.append(f"RSI overbought ({rsi:.0f})")
            elif rsi > 65:
                risks.append(f"RSI elevated ({rsi:.0f})")
            vol = rec.risk_metrics.volatility if rec.risk_metrics else 0
            if vol > 0.35:
                risks.append(f"High vol ({vol * 100:.0f}%)")
            beta = rec.risk_metrics.beta if rec.risk_metrics else None
            if beta and beta > 1.3:
                risks.append(f"Beta {beta:.1f}x")
            risk_line = " │ ".join(risks) if risks else "Standard market risk"

            # ── News & earnings (CACHED — zero API calls) ──
            news_str = ""
            earn_str = ""
            headline = getattr(rec, '_cached_news_headline', '')
            earn_days = getattr(rec, '_cached_earnings_days', -1)
            if headline:
                news_str = f"\n[dim]  📰 {headline}[/]"
            if 0 <= earn_days <= 14:
                earn_str = f"\n[bold red]  ⚠ EARNINGS IN {earn_days} DAYS[/]"
            elif 0 <= earn_days <= 30:
                earn_str = f"\n[yellow]  📅 Earnings in {earn_days} days[/]"

            # ── Hold estimate ──
            # [O4 FIX] Was an independent volatility guess that contradicted
            # both the projection and the rationale. Now the same horizon.
            hold = describe_horizon(
                float(getattr(rec.timeframe_projection, 'horizon_months', 0.0) or 0.0)
            )
            review_date = getattr(rec.timeframe_projection, 'review_date', '') or ''

            # ── Classification (shared source of truth) ──
            # Determined BEFORE assembling the card so execution / position
            # blocks are only shown for actionable buy-tier picks. A true
            # HOLD / MONITOR / SELL never gets entry orders or sizing.
            has_pos = getattr(rec, 'has_existing_position', False)
            sig, clr, final_value, _reason = classify_display_action(
                rec.signal, has_pos, getattr(rec, 'final_action', None))
            is_buy_tier = final_value in ("STRONG_BUY", "BUY", "ACCUMULATE")

            # Override reasons resolved by the decision-core (Buy-Now=NO,
            # bearish reversal, DCF overvaluation, heavy insider selling …).
            # These drive "wait for entry" wording and a visible warning line
            # so the card never shows a clean BUY thesis while hiding the
            # red flags the deep analyzer would surface.
            # Only genuine overrides belong in a CAUTION line. The routine
            # "Composite score N → candidate X" trace (and the equivalent
            # "Reconciling against X ..." trace the reconciliation resolver
            # emits in place of a composite-score line) is how every decision
            # is recorded, so surfacing it as a caution fired a warning on
            # every single card and drowned out the real red flags.
            _fa_reasons = [
                r for r in (getattr(rec, 'final_action_reasons', []) or [])
                if 'candidate' not in r and 'Composite score' not in r
                and not r.startswith('Reconciling against')
            ]
            _wait_kw = ("wait", "Buy Now", "Timing", "bearish", "Bearish",
                        "overvaluation", "insider")
            _wait = any(any(k in r for k in _wait_kw) for r in _fa_reasons)

            # ── Conditional EXECUTION / POSITION blocks ──
            if is_buy_tier:
                cs = self.currency_symbol
                if single_lot:
                    # One whole-share lot: one order, one exit.
                    if final_value == "ACCUMULATE" and _wait:
                        entry_line = (
                            f"  Entry  [yellow]WAIT[/] — buy {fmt_shares(shares)} sh only on a pullback "
                            f"to {fmt_price(pullback_lvl, cs)} or a close above today's high on volume\n"
                        )
                    else:
                        entry_line = f"  Entry  LIMIT {fmt_price(rec.entry_price, cs)} — {fmt_shares(shares)} sh\n"
                    exit_line = (
                        f"  Exit   Target {fmt_price(rec.price_target, cs)} (+{upside:.1f}%), or trail "
                        f"{TradingConfig.TRAILING_ATR_MULTIPLIER:g}×ATR below the high once T1 "
                        f"{fmt_price(t1, cs)} prints\n"
                    )
                else:
                    if final_value == "ACCUMULATE" and _wait:
                        # Timing/bearish/valuation override: scale in only after
                        # confirmation — do not present an immediate limit entry.
                        entry_line = (
                            f"  Entry  [yellow]WAIT[/] — start a ⅓ starter only after a "
                            f"pullback to {fmt_price(pullback_lvl, cs)} or a "
                            f"confirmed breakout (close above today's high on volume); "
                            f"add the rest on confirmation\n"
                        )
                    elif final_value == "ACCUMULATE":
                        entry_line = (
                            f"  Entry  SCALE-IN ⅓ now near {fmt_price(rec.entry_price, cs)}"
                            f" → ⅓ at {fmt_price(add_lvl, cs)} → last ⅓ on a close above "
                            f"today's high\n"
                        )
                    else:
                        entry_line = (
                            f"  Entry  LIMIT {fmt_price(rec.entry_price, cs)} (50%)"
                            f" → Add {fmt_price(add_lvl, cs)} (50%)\n"
                        )
                    # [O13] One trailing rule. The trail starts AT the hard stop
                    # and only ratchets up, TRAILING_ATR_MULTIPLIER x ATR below
                    # the running high; printing a "now" level above the hard
                    # stop invited a day-one stop-out.
                    exit_line = (
                        f"  Exit   T1 {fmt_price(t1, cs)} (+{t1_pct:.1f}%) sell ⅓"
                        f" → T2 {fmt_price(rec.price_target, cs)} (+{upside:.1f}%) sell ⅓"
                        f" → trail the last ⅓ {TradingConfig.TRAILING_ATR_MULTIPLIER:g}×ATR "
                        f"below the high\n"
                    )
                review_line = f" │ Review {review_date}" if review_date else ""
                execution_block = (
                    f"\n"
                    f"[bold yellow]EXECUTION[/]\n"
                    f"{entry_line}"
                    f"  Stop   {fmt_price(rec.stop_loss, cs)} HARD → Sell 100%\n"
                    f"{exit_line}"
                    f"  Hold   {hold}{review_line}\n"
                )
                position_block = (
                    f"\n"
                    f"[bold]POSITION[/]  {fmt_shares(shares)} shares │ "
                    f"{self.currency_symbol}{alloc:,.0f} ({alloc_pct:.1f}%) │ "
                    f"Risk at stop {self.currency_symbol}{max_loss:,.0f} ({loss_pct:.2f}% of capital)\n"
                )
            else:
                # Non-actionable: no new entry, no sizing, no execution plan.
                note = ("Own it → hold per your original plan"
                        if final_value == "HOLD_EXISTING" else
                        "Reduce / exit on strength" if final_value == "AVOID" else
                        "On watch — wait for a valid setup before buying")
                execution_block = (
                    f"\n[bold yellow]EXECUTION[/]\n"
                    f"  [dim]No new entry — {note}.[/]\n"
                )
                position_block = (
                    f"\n[bold]POSITION[/]  [dim]Not sized (no new capital allocated)[/]\n"
                )

            # ── Assemble card ──
            warning_block = ""
            if _fa_reasons:
                _shown = "; ".join(_fa_reasons[:3])
                warning_block = f"[bold yellow]⚠ CAUTION[/]   {_shown}\n"
            # For a non-buy card these are the SCANNER's levels, which the deep
            # analyzer rejected; label them so they are not read as an order.
            levels_label = "" if is_buy_tier else "[dim]Scanner reference levels (not an order):[/] "
            ma50_clause = (
                f"loses the 50MA ({fmt_price(tech.sma_50, self.currency_symbol)})"
                if current_price >= tech.sma_50 else
                f"fails to reclaim the 50MA ({fmt_price(tech.sma_50, self.currency_symbol)})"
            )
            card = (
                f"{levels_label}"
                f"[bold white]{fmt_price(current_price, self.currency_symbol)}[/] → "
                f"[red]Stop {fmt_price(rec.stop_loss, self.currency_symbol)}[/] → "
                f"[green]Target {fmt_price(rec.price_target, self.currency_symbol)}[/]  │  "
                f"R:R [bold]{rec.risk_reward_ratio:.2f}x[/] net of "
                f"{float(getattr(rec, 'transaction_cost_bps', 0) or 0):.0f}bp costs  │  "
                f"+{upside:.1f}% / -{downside:.1f}%\n"
                f"\n"
                f"[bold cyan]THESIS[/]\n"
                + "\n".join(f"  • {b}" for b in bullets[:3])
                + f"\n  • Setup: {catalyst_line}\n"
                + execution_block
                + position_block
                + warning_block
                + f"[bold]RISK[/]      [{gauge}] {rlabel} — {risk_line}\n"
                + f"[bold]CONVICTION[/] {'★' * conviction_num}{'☆' * (CONVICTION_MAX - conviction_num)} "
                f"{conviction_num}/{CONVICTION_MAX}\n"
                f"[bold red]THESIS FAILS IF[/] "
                f"close below {fmt_price(rec.stop_loss, self.currency_symbol)} │ "
                f"{ma50_clause} │ "
                f"VIX above {TradingConfig.VIX_SKIP_THRESHOLD:.0f} │ "
                f"guidance cut at earnings"
                f"{news_str}{earn_str}"
            )

            console.print(Panel(
                card,
                title=f"[bold white]#{idx} {rec.ticker}[/] │ {rec.company[:25]} │ [{clr}]{sig}[/]",
                border_style=clr, box=box.ROUNDED, padding=(0, 2)
            ))
            console.print()

        # ── VERBOSE / DEBUG TABLE ───────────────────────────────────
        # Shows the full classification trace so the rendered label can be
        # audited against the decision-core. Toggle via verbose=True or
        # the QUANTA_VERBOSE env var.
        if verbose and debug_rows:
            dbg = Table(
                title="[bold]DEBUG — ACTION CLASSIFICATION[/]",
                box=box.SIMPLE_HEAD, header_style="bold white on grey23",
                padding=(0, 1), show_edge=False, expand=True,
            )
            dbg.add_column("#", style="dim", justify="center", no_wrap=True)
            dbg.add_column("TICKER", style="bold cyan", no_wrap=True)
            dbg.add_column("RAW SIGNAL", no_wrap=True)
            dbg.add_column("FINAL_ACTION", style="magenta", no_wrap=True)
            dbg.add_column("DISPLAY", style="green", no_wrap=True)
            dbg.add_column("REASON", style="dim", overflow="fold")
            for row in debug_rows:
                dbg.add_row(*row)
            console.print(dbg)
            console.print()

        # ── FOOTER ──────────────────────────────────────────────────
        console.print(Panel(
            "[dim]Past performance ≠ future results. Use stop losses. "
            "Verify data independently before trading.[/]",
            border_style="dim", box=box.SIMPLE, padding=(0, 1)
        ))
        console.print()

        # ── [PRE-MOMENTUM] TOO-LATE-TO-CHASE WATCHLIST ─────────────
        # Surface tickers that triggered the CHASE ALERT during this scan
        # as a *separate* bucket — visible context, NOT actionable picks.
        # A name that is ALSO a published pick is not "too late to chase" —
        # it passed the gates with the chase penalty applied.
        _published = {r.ticker for r in recommendations}
        chase_alerts = [
            c for c in (getattr(self, 'chase_alerts', []) or [])
            if c.get('ticker') not in _published
        ]
        if chase_alerts:
            try:
                # De-dupe by ticker (keeps the first/most-recent alert reason)
                seen = set()
                deduped = []
                for c in chase_alerts:
                    t = c.get('ticker')
                    if t and t not in seen:
                        seen.add(t)
                        deduped.append(c)
                # Sort by 1M momentum desc — most-extended first
                deduped.sort(key=lambda c: c.get('momentum_1m', 0) or 0, reverse=True)

                table = Table(
                    title=f"⚠ TOO LATE TO CHASE — {len(deduped)} extended names (watch for pullback)",
                    box=box.SIMPLE_HEAVY, border_style="yellow", header_style="bold yellow",
                    show_lines=False, padding=(0, 1),
                )
                table.add_column("Ticker", style="bold white", no_wrap=True)
                table.add_column("Price", justify="right")
                table.add_column("RSI", justify="right")
                table.add_column("1M %", justify="right")
                table.add_column("Reason", style="dim", overflow="fold")
                for c in deduped[:15]:
                    mom_pct = (c.get('momentum_1m', 0) or 0) * 100
                    table.add_row(
                        str(c.get('ticker', '')),
                        f"{self.currency_symbol}{c.get('price', 0):.2f}",
                        f"{c.get('rsi', 0):.0f}",
                        f"{mom_pct:+.1f}%",
                        str(c.get('reason', '')).replace('⚠️ CHASE ALERT: ', ''),
                    )
                console.print(table)
                console.print(
                    "[dim yellow]These are not BUY signals. They show where the crowd "
                    "already crowded in. Wait for a pullback to a key MA or volume "
                    "contraction before considering entry.[/]\n"
                )
            except Exception as e:
                logger.debug(f"Chase-alert display error: {e}")

        # ── [PRE-MOMENTUM] EARLY-MOMENTUM HIGHLIGHTS ───────────────
        # When the user ran a non-pre-momentum scan but some names still
        # show a high early-momentum classifier score (≥4 of 6), call them
        # out as a watchlist. The pre_momentum scan itself already filters
        # the main recommendation list by this score, so skip this section
        # there to avoid duplication.
        if getattr(self, 'current_scan_type', '') != 'pre_momentum':
            try:
                em_picks = [
                    r for r in (getattr(self, 'scan_results', []) or [])
                    if getattr(r, 'early_momentum_score', 0) >= 4
                    and r.ticker not in {c.get('ticker') for c in chase_alerts}
                    and r.ticker not in _published
                ]
                em_picks.sort(key=lambda r: (r.early_momentum_score, r.overall_score), reverse=True)
                if em_picks:
                    table = Table(
                        title=f"🚀 EARLY-MOMENTUM WATCHLIST — {len(em_picks[:10])} setups building (not yet extended)",
                        box=box.SIMPLE_HEAVY, border_style="cyan", header_style="bold cyan",
                        show_lines=False, padding=(0, 1),
                    )
                    table.add_column("Ticker", style="bold white", no_wrap=True)
                    table.add_column("Score", justify="right")
                    table.add_column("EM /6", justify="right")
                    table.add_column("RSI", justify="right")
                    table.add_column("Vol×", justify="right")
                    table.add_column("Confirmations", style="dim", overflow="fold")
                    for r in em_picks[:10]:
                        table.add_row(
                            r.ticker,
                            f"{r.overall_score:.1f}",
                            f"{r.early_momentum_score}",
                            f"{r.technical_indicators.rsi:.0f}",
                            f"{r.technical_indicators.volume_ratio:.1f}",
                            " · ".join(r.early_momentum_signals[:3]),
                        )
                    console.print(table)
                    console.print()
            except Exception as e:
                logger.debug(f"Early-momentum display error: {e}")

    def display_recommendations(self, recommendations: List[StockAnalysis]):
        """Display recommendations in professional format"""

        if not recommendations:
            print("\n" + "=" * 100)
            print("❌ NO TRADING OPPORTUNITIES FOUND")
            print("=" * 100)
            return

        print("\n" + "=" * 120)
        print(" " * 45 + "QUANTA ELITE TRADING SIGNALS v3.0")
        print(" " * 40 + f"{self.analysis_timestamp.strftime('%B %d, %Y at %H:%M:%S')}")
        print("=" * 120)

        print("\n🎯 TOP TRADING OPPORTUNITIES\n")

        # Main recommendations table
        display_data = []
        for rec in recommendations:
            signal_type = SignalType[rec.signal]
            display_data.append([
                f"{signal_type.emoji} {rec.ticker}",
                rec.company[:28],
                rec.signal,
                f"{self.currency_symbol}{rec.entry_price:.2f}",
                f"{self.currency_symbol}{rec.price_target:.2f}",
                rec.timeframe_projection.target_timeframe,  # NEW: TimeFrame column
                f"{self.currency_symbol}{rec.stop_loss:.2f}",
                f"{self.currency_symbol}{rec.allocation:,.0f}",
                f"{rec.risk_reward_ratio:.1f}:1",
                f"{rec.overall_score:.1f}/10",
                f"{rec.confidence_tier}"
            ])

        headers = ['Symbol', 'Company', 'Signal', 'Entry', 'Target', 'TimeFrame', 'Stop',
                   'Position', 'R/R', 'Score', 'Conf']
        print(tabulate(display_data, headers=headers, tablefmt='fancy_grid'))

        # Technical & Quality Metrics
        print("\n📊 TECHNICAL & QUALITY METRICS\n")

        tech_data = []
        for rec in recommendations:
            rsi_status = "🟢" if 30 <= rec.technical_indicators.rsi <= 70 else \
                "🟡" if rec.technical_indicators.rsi < 30 else "🔴"
            mom_status = "📈" if rec.technical_indicators.momentum_1m > 0.05 else \
                "➡️" if rec.technical_indicators.momentum_1m > 0 else "📉"

            tech_data.append([
                rec.ticker,
                f"{rsi_status} {rec.technical_indicators.rsi:.0f}",
                f"{mom_status} {rec.technical_indicators.momentum_1m * 100:.1f}%",
                f"{rec.fundamental_metrics.pe_ratio:.1f}" if rec.fundamental_metrics.pe_ratio > 0 else "N/A",
                f"{rec.risk_metrics.sharpe_ratio:.2f}",
                f"{rec.risk_metrics.volatility * 100:.0f}%",
                rec.sector[:18]
            ])

        tech_headers = ['Ticker', 'RSI', '1M Mom', 'P/E', 'Sharpe', 'Vol', 'Sector']
        print(tabulate(tech_data, headers=tech_headers, tablefmt='fancy_grid'))

        # Score Breakdown
        print("\n🏆 SCORE BREAKDOWN\n")

        score_data = []
        for rec in recommendations:
            score_data.append([
                rec.ticker,
                f"{rec.technical_score:.1f}",
                f"{rec.fundamental_score:.1f}",
                f"{rec.momentum_score:.1f}",
                f"{rec.quality_score:.1f}",
                f"{rec.value_score:.1f}",
                f"{rec.overall_score:.1f}"
            ])

        score_headers = ['Ticker', 'Tech(30%)', 'Fund(25%)', 'Mom(20%)', 'Qual(15%)', 'Val(10%)', 'Total']
        print(tabulate(score_data, headers=score_headers, tablefmt='fancy_grid'))
        print("   Note: percentages are the base (neutral-regime) factor weights; the")
        print("   live weights are regime-adaptive. EPS revisions are applied as a")
        print("   bounded score adjustment (±1.5 pts), NOT part of the base weights.")

        # Portfolio Summary
        total_allocation = sum(r.allocation for r in recommendations)
        remaining = self.capital - total_allocation

        print("\n" + "═" * 100)
        print("💼 PORTFOLIO ALLOCATION SUMMARY")
        print("═" * 100)

        allocation_data = [
            ['Total Capital', f"{self.currency_symbol}{self.capital:,.2f}"],
            ['Allocated to Positions', f"{self.currency_symbol}{total_allocation:,.2f} ({total_allocation / self.capital * 100:.1f}%)"],
            ['Cash Reserve', f"{self.currency_symbol}{remaining:,.2f} ({remaining / self.capital * 100:.1f}%)"],
            ['Number of Positions', str(len(recommendations))],
            ['Average Position Size', f"{self.currency_symbol}{total_allocation / len(recommendations):,.2f}"],
            ['Portfolio Diversity', f"{len(set(r.sector for r in recommendations))} sectors"],
        ]

        print(tabulate(allocation_data, tablefmt='fancy_grid'))

        # Signal Distribution
        print("\n📊 SIGNAL DISTRIBUTION: ", end="")
        signal_counts = {}
        for rec in recommendations:
            signal_counts[rec.signal] = signal_counts.get(rec.signal, 0) + 1

        for signal, count in signal_counts.items():
            signal_type = SignalType[signal]
            print(f"{signal_type.emoji} {signal}: {count}  ", end="")
        print()

        # Risk Metrics Summary
        avg_rr = sum(r.risk_reward_ratio for r in recommendations) / len(recommendations)
        avg_sharpe = sum(r.risk_metrics.sharpe_ratio for r in recommendations) / len(recommendations)
        print("\n📈 PORTFOLIO RISK METRICS:")
        print(f"   • Average Risk/Reward: {avg_rr:.1f}:1")
        print(f"   • Average Sharpe Ratio: {avg_sharpe:.2f}")
        tier_counts = {}
        for r in recommendations:
            tier_counts[r.confidence_tier] = tier_counts.get(r.confidence_tier, 0) + 1
        tier_summary = ", ".join(f"{t}: {c}" for t, c in sorted(tier_counts.items()))
        print(f"   • Confidence Tiers: {tier_summary}")

        print("\n" + "=" * 120 + "\n")

    def export_tradingview_list(self, recommendations: List[StockAnalysis], 
                                 output_dir: str = "exports") -> Optional[str]:
        """
        **NEW FEATURE: Export simple comma-separated list for TradingView import**
        
        Creates a clean text file with tickers formatted for TradingView:
        - India: "NSE:RELIANCE,NSE:TCS,NSE:INFY"
        - US: "AAPL,MSFT,NVDA"
        """
        if not recommendations:
            logger.warning("No recommendations to export for TradingView")
            return None
            
        try:
            path = Path(output_dir)
            path.mkdir(exist_ok=True)
            
            # Format tickers based on market
            if self.market == "INDIA":
                # [O14 FIX] This prefixed NSE: onto every symbol including
                # .BO ones, so BSE tickers would not resolve in TradingView.
                # Map the exchange from the suffix instead.
                tickers = []
                for rec in recommendations:
                    symbol = rec.ticker
                    exchange = "BSE" if symbol.endswith('.BO') else "NSE"
                    clean_ticker = symbol.replace('.NS', '').replace('.BO', '')
                    tickers.append(f"{exchange}:{clean_ticker}")
            else:
                # US tickers are used as-is
                tickers = [rec.ticker for rec in recommendations]
                
            file_path = path / f"tradingview_watchlist_{self.analysis_timestamp.strftime('%Y%m%d')}.txt"
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(",".join(tickers))
                
            print(f"✅ TradingView watchlist saved: {file_path}")
            print(f"   📋 Copy and paste into TradingView's 'Add Symbol' dialog")
            logger.info(f"TradingView watchlist exported: {len(tickers)} symbols to {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"TradingView watchlist export failed: {e}")
            return None

    def export_results(self, recommendations: List[StockAnalysis],
                       output_dir: str = "exports") -> bool:
        """Export results to multiple formats"""

        if not recommendations:
            logger.warning("No recommendations to export")
            return False

        try:
            # Create export directory
            export_path = Path(output_dir)
            export_path.mkdir(parents=True, exist_ok=True)

            timestamp = self.analysis_timestamp.strftime('%Y%m%d_%H%M%S')

            # [DEEP-CONFIRM] Join the reconciliation audit trail by ticker so
            # every export format can surface scanner vs deep verdicts.
            dc_by_ticker = {
                r.get('ticker'): r for r in getattr(self, '_deep_confirm_records', []) or []
            }
            deep_confirm_enabled = bool(getattr(self, '_deep_confirm_enabled', True))

            def _dc(ticker: str) -> Dict[str, Any]:
                return dc_by_ticker.get(ticker, {})

            # Export to CSV
            csv_data = []
            for rec in recommendations:
                dc = _dc(rec.ticker)
                reasons = getattr(rec, 'final_action_reasons', None) or []
                csv_data.append({
                    'Ticker': rec.ticker,
                    'Company': rec.company,
                    'Sector': rec.sector,
                    'Signal': rec.signal,
                    'Scanner_Action': dc.get('scanner_action', getattr(rec, 'final_action', '') or rec.signal),
                    'Deep_Action': dc.get('deep_action', 'N/A'),
                    'Final_Action': getattr(rec, 'final_action', '') or rec.signal,
                    'Downgrade_Flag': bool(dc.get('downgraded', False)),
                    'Deep_Confirmation_Reason': dc.get('reason', '') or ' | '.join(reasons),
                    'Final_Action_Reasons': ' | '.join(reasons),
                    'Deep_Confirm_Enabled': deep_confirm_enabled,
                    'Overall_Score': rec.overall_score,
                    'Technical_Score': rec.technical_score,
                    'Fundamental_Score': rec.fundamental_score,
                    'Momentum_Score': rec.momentum_score,
                    'Quality_Score': rec.quality_score,
                    # [O14 FIX] Value_Score was the one sub-score omitted,
                    # which made the composite unreproducible from the export.
                    'Value_Score': rec.value_score,
                    'Conviction': getattr(rec, 'conviction', 0),
                    'Setup_Type': getattr(rec, 'setup_type', ''),
                    'Risk_Grade': rec.risk_grade,
                    'Currency': rec.currency,
                    'Data_Status': getattr(rec, 'data_status', 'ok'),
                    'Not_Independently_Confirmed': bool(
                        getattr(rec, 'not_independently_confirmed', False)
                    ),
                    'Horizon_Months': round(
                        float(getattr(rec.timeframe_projection, 'horizon_months', 0.0) or 0.0), 2
                    ),
                    'Review_Date': getattr(rec.timeframe_projection, 'review_date', ''),
                    'Position_Pct': rec.position_pct,
                    'Risk_At_Stop': round(
                        rec.shares * max(0.0, rec.entry_price - rec.stop_loss), 2
                    ),
                    'Entry_Price': rec.entry_price,
                    'Price_Target': rec.price_target,
                    'Stop_Loss': rec.stop_loss,
                    'Trailing_Stop': rec.trailing_stop,
                    'Allocation_Before': dc.get('allocation_before', rec.allocation),
                    'Allocation_After': dc.get('allocation_after', rec.allocation),
                    'Allocation': rec.allocation,
                    'Shares': rec.shares,
                    'Risk_Reward': rec.risk_reward_ratio,
                    'Confidence_Tier': rec.confidence_tier,
                    'Confidence_Confirmations': rec.confidence_confirmations,
                    'Confidence_Factors_Total': rec.confidence_factors_total,
                    'Confidence_Caveat': rec.signal_strength_caveat,
                    'RSI': rec.technical_indicators.rsi,
                    'MACD': rec.technical_indicators.macd,
                    'ATR': rec.technical_indicators.atr,
                    'Momentum_1M': rec.technical_indicators.momentum_1m,
                    'PE_Ratio': rec.fundamental_metrics.pe_ratio,
                    'PEG_Ratio': rec.fundamental_metrics.peg_ratio,
                    'ROE': rec.fundamental_metrics.roe,
                    'Sharpe_Ratio': rec.risk_metrics.sharpe_ratio,
                    'Max_Drawdown': rec.risk_metrics.max_drawdown,
                    'Volatility': rec.risk_metrics.volatility,
                })

            csv_file = export_path / f"quanta_signals_{timestamp}.csv"
            pd.DataFrame(csv_data).to_csv(csv_file, index=False)
            logger.info(f"✓ CSV exported: {csv_file}")

            # Export to JSON (detailed)
            def _rec_json(rec: StockAnalysis) -> Dict[str, Any]:
                d = rec.to_dict()
                dc = _dc(rec.ticker)
                d['deep_confirmation'] = {
                    'scanner_action': dc.get('scanner_action', getattr(rec, 'final_action', '') or rec.signal),
                    'deep_action': dc.get('deep_action', 'N/A'),
                    'final_action': getattr(rec, 'final_action', '') or rec.signal,
                    'allocation_before': dc.get('allocation_before', rec.allocation),
                    'allocation_after': dc.get('allocation_after', rec.allocation),
                    'downgrade_flag': bool(dc.get('downgraded', False)),
                    'deep_confirmation_reason': dc.get('reason', ''),
                    'deep_confirm_enabled': deep_confirm_enabled,
                }
                return d

            # [O14 FIX] `total_analyzed` was
            # `len(scan_results) + len(failed_tickers)` — survivors plus
            # failures, NOT the analyzed universe. Since scan_results holds
            # only names that passed every gate, the figure understated the
            # denominator badly and made hit rates look far better than they
            # were. `_universe_counts` and `_gate_stats` already hold the real
            # numbers and were simply never exported.
            _counts = dict(getattr(self, '_universe_counts', {}) or {})
            # Export must not require a fully-initialised system (the webapp and
            # tests both build partial instances), so read the regime defensively.
            _regime = getattr(self, 'market_regime', None) or MarketRegime()
            _regime_type = getattr(_regime, 'regime_type', 'NEUTRAL')
            json_data = {
                'metadata': {
                    'version': '3.1',
                    'timestamp': self.analysis_timestamp.isoformat(),
                    'capital': getattr(self, 'capital', 0.0),
                    'currency': getattr(self, 'currency_symbol', '$'),
                    'market': getattr(self, 'market', 'US'),
                    'scan_type': getattr(self, 'current_scan_type', ''),
                    'universe_size': int(getattr(self, '_last_universe_size', 0) or 0),
                    'total_analyzed': int(_counts.get('analyzed_universe', 0) or 0),
                    'valid_scoring_universe': int(_counts.get('valid_scoring_universe', 0) or 0),
                    'actionable_opportunities': int(_counts.get('actionable_opportunities', 0) or 0),
                    'analysis_failed_count': len(getattr(self, 'failed_tickers', []) or []),
                    'opportunities_found': len(getattr(self, 'scan_results', []) or []),
                    'recommendations': len(recommendations),
                    'deep_confirm_enabled': deep_confirm_enabled,
                    'regime': _regime_type,
                    'vix_level': getattr(_regime, 'vix_level', 0.0),
                    'vix_is_proxy': bool(getattr(_regime, 'vix_is_proxy', False)),
                    'recommended_exposure': getattr(_regime, 'recommended_exposure', 1.0),
                    'factor_weights': _REGIME_WEIGHTS.get(
                        _regime_type, _REGIME_WEIGHTS['NEUTRAL']
                    ),
                    'current_drawdown': float(getattr(self, 'current_drawdown', 0.0) or 0.0),
                    'drawdown_multiplier': self._drawdown_size_multiplier(),
                },
                'universe_counts': _counts,
                'gate_stats': dict(getattr(self, '_gate_stats', {}) or {}),
                'candidate_rejections': self.candidate_rejections(),
                'provider_reads': self.provider_diagnostics(),
                'recommendations': [_rec_json(rec) for rec in recommendations]
            }

            json_file = export_path / f"quanta_detailed_{timestamp}.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(_json_safe(json_data), f, indent=2, allow_nan=False, default=str)
            logger.info(f"✓ JSON exported: {json_file}")

            # Export summary report
            summary_file = export_path / f"quanta_summary_{timestamp}.txt"
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write("QUANTA ELITE TRADING SYSTEM v3.0 - SUMMARY REPORT\n")
                f.write(f"Generated: {self.analysis_timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")

                f.write(f"Total Capital: {self.currency_symbol}{self.capital:,.2f}\n")
                f.write(f"Recommendations: {len(recommendations)}\n")
                if not deep_confirm_enabled:
                    f.write("\n⚠️  DEEP CONFIRMATION DISABLED (developer-only override) — "
                            "picks were NOT independently confirmed and may contradict "
                            "the deep analyzer.\n")
                f.write("\n")

                for i, rec in enumerate(recommendations, 1):
                    dc = _dc(rec.ticker)
                    # [FIX] Use the engine-resolved final_action, not the raw
                    # signal, so a WATCHLIST downgrade is not mislabeled HOLD.
                    action = getattr(rec, 'final_action', '') or rec.signal
                    f.write(f"\n{i}. {rec.ticker} - {rec.company}\n")
                    f.write(f"   Action: {action} | Score: {rec.overall_score}/10\n")
                    if dc.get('downgraded'):
                        f.write(f"   ⬇ Deep-confirm downgrade: {dc.get('scanner_action')} "
                                f"→ {dc.get('deep_action')} | {dc.get('reason', '')}\n")
                    f.write(
                        f"   Entry: {self.currency_symbol}{rec.entry_price:.2f} | Target: {self.currency_symbol}{rec.price_target:.2f} | Stop: {self.currency_symbol}{rec.stop_loss:.2f}\n")
                    f.write("-" * 40 + "\n")
                    alloc_before = dc.get('allocation_before', rec.allocation)
                    # [O14 FIX] Hardcoded '$' rendered INR positions as dollars.
                    f.write(
                        f"   Position: {self.currency_symbol}{rec.allocation:,.0f} "
                        f"({rec.position_pct:.1f}%) [before deep-confirm: "
                        f"{self.currency_symbol}{alloc_before:,.0f}] | "
                        f"R/R: {rec.risk_reward_ratio:.2f}:1\n"
                    )

            logger.info(f"✓ Summary exported: {summary_file}")
            logger.info(f"\n✅ All results exported successfully to {export_path}")

            return True

        except Exception as e:
            logger.error(f"✗ Export failed: {e}")
            return False

    def display_recommendations_enhanced(self, recommendations: List[StockAnalysis],
                                         profile: Optional[InvestorProfile] = None):
        """Enhanced display with rich output and narratives"""

        if not recommendations:
            print("\n" + "=" * 100)
            print("❌ NO TRADING OPPORTUNITIES FOUND")
            print("=" * 100)
            return

        # Initialize display
        display = getattr(self, 'display', RichDisplay())

        # Header
        display.print_header(
            "📊 QUANTA ELITE TRADING RECOMMENDATIONS",
            f"Generated on {self.analysis_timestamp.strftime('%B %d, %Y at %H:%M:%S')}"
        )

        # Show investor profile if available
        if profile and display.enabled:
            panel = display.create_investor_profile_panel(profile)
            if panel:
                console.print(panel)

        # Market context
        if ENHANCEMENTS_AVAILABLE:
            market_context = MarketContextAnalyzer.describe_market_regime()
            display.print_info(f"Market Context: {market_context}")

        print()

        # Main recommendations table
        if display.enabled:
            table = display.create_recommendations_table(recommendations)
            if table:
                console.print(table)
        else:
            # Fallback to standard table
            self.display_recommendations(recommendations)
            return

        print()

        # Portfolio summary
        if display.enabled:
            panel = display.create_portfolio_summary_panel(recommendations, self.capital)
            if panel:
                console.print(panel)

        # Detailed narratives for top 3 picks
        display.print_section("\n🎯 DETAILED ANALYSIS - TOP PICKS\n", style="bold cyan")

        for i, rec in enumerate(recommendations[:min(3, len(recommendations))], 1):
            narrative = getattr(rec, 'narrative', None)
            if narrative and ENHANCEMENTS_AVAILABLE:
                display.display_stock_narrative(narrative, rec)
            else:
                # Fallback summary
                print(f"\n{i}. {rec.ticker} - {rec.company}")
                print(f"   Action: {rec.signal} | Score: {rec.overall_score}/10")
                print(
                    f"   Entry: {self.currency_symbol}{rec.entry_price:.2f} | Target: {self.currency_symbol}{rec.price_target:.2f} | Stop: {self.currency_symbol}{rec.stop_loss:.2f}")
                print(f"   Position: {self.currency_symbol}{rec.allocation:,.0f} | R/R: {rec.risk_reward_ratio:.1f}:1\n")

        # Disclaimers
        print("\n" + "=" * 100 + "\n")

    def export_markdown_report(self, recommendations: List[StockAnalysis],
                               output_dir: str = "exports") -> bool:
        """Export comprehensive markdown report with narratives"""

        if not recommendations:
            return False

        try:
            export_path = Path(output_dir)
            export_path.mkdir(parents=True, exist_ok=True)

            timestamp = self.analysis_timestamp.strftime('%Y%m%d_%H%M%S')
            md_file = export_path / f"quanta_report_{timestamp}.md"

            with open(md_file, 'w', encoding='utf-8') as f:
                # Header
                f.write(f"# QUANTA Elite Trading Report\n\n")
                f.write(f"**Generated:** {self.analysis_timestamp.strftime('%B %d, %Y at %H:%M:%S')}\n\n")
                f.write(f"**Capital:** {self.currency_symbol}{self.capital:,.2f}\n\n")
                f.write("---\n\n")

                # Executive Summary. [O14] The export fix never reached this
                # file: "analyzed" was survivors + failures ("We analyzed 4
                # stocks"), $0 watchlist names counted as opportunities, and
                # WATCHLIST picks printed as HOLD.
                funded = [r for r in recommendations if float(r.allocation or 0) > 0]
                analyzed = (getattr(self, '_universe_counts', {}) or {}).get(
                    'analyzed_universe',
                    len(self.scan_results) + len(self.failed_tickers),
                )
                f.write("## 📊 Executive Summary\n\n")
                f.write(f"We analyzed {analyzed} stocks and published ")
                f.write(f"**{len(funded)}** funded trade(s)")
                if len(funded) != len(recommendations):
                    f.write(f" plus {len(recommendations) - len(funded)} watchlist name(s) at $0")
                f.write(".\n\n")

                avg_score = sum(r.overall_score for r in recommendations) / len(recommendations)
                f.write(f"- **Average Composite Score:** {avg_score:.1f}/10\n")

                total_allocation = sum(r.allocation for r in funded)
                f.write(
                    f"- **Total Allocation:** {self.currency_symbol}{total_allocation:,.2f} ({total_allocation / self.capital * 100:.1f}% of capital)\n")
                f.write(f"- **Cash Reserve:** {self.currency_symbol}{self.capital - total_allocation:,.2f}\n")
                f.write(f"- **Number of Sectors:** {len(set(r.sector for r in recommendations))}\n\n")

                f.write("---\n\n")

                # Top Recommendations Table
                f.write("## 🎯 Top Recommendations\n\n")
                f.write("| Symbol | Signal | Score | Entry | Target | Stop | Position | R/R |\n")
                f.write("|--------|--------|-------|-------|--------|------|----------|-----|\n")

                for rec in recommendations:
                    f.write(f"| **{rec.ticker}** | {getattr(rec, 'final_action', '') or rec.signal} | {rec.overall_score:.1f}/10 | ")
                    f.write(f"{self.currency_symbol}{rec.entry_price:.2f} | {self.currency_symbol}{rec.price_target:.2f} | {self.currency_symbol}{rec.stop_loss:.2f} | ")
                    f.write(f"{self.currency_symbol}{rec.allocation:,.0f} | {rec.risk_reward_ratio:.1f}:1 |\n")

                f.write("\n---\n\n")

                # Detailed Analysis for each stock
                f.write("## 📈 Detailed Stock Analysis\n\n")

                for i, rec in enumerate(recommendations, 1):
                    f.write(f"### {i}. {rec.ticker} - {rec.company}\n\n")

                    # Get narrative if available
                    narrative = getattr(rec, 'narrative', None)

                    if narrative:
                        f.write(f"**{narrative.headline}**\n\n")

                        f.write(f"**Sector:** {rec.sector} | **Industry:** {rec.industry}\n\n")

                        if narrative.why_now:
                            f.write(f"#### ⏰ Why This Entry Point?\n\n{narrative.why_now}\n\n")

                        if narrative.technical_story:
                            f.write(f"#### 📊 Technical Setup\n\n{narrative.technical_story}\n\n")

                        if narrative.fundamental_thesis:
                            f.write(f"#### 💼 Fundamental Thesis\n\n{narrative.fundamental_thesis}\n\n")

                        if narrative.risk_factors:
                            f.write(f"#### ⚠️ Risk Factors\n\n{narrative.risk_factors}\n\n")

                        # Trading levels
                        f.write(f"#### 📍 Trading Levels\n\n")
                        f.write(f"- **Entry Price:** {self.currency_symbol}{rec.entry_price:.2f}\n")
                        f.write(
                            f"- **Price Target:** {self.currency_symbol}{rec.price_target:.2f} (+{((rec.price_target / rec.entry_price - 1) * 100):.1f}%)\n")
                        f.write(
                            f"- **Stop Loss:** {self.currency_symbol}{rec.stop_loss:.2f} ({((rec.stop_loss / rec.entry_price - 1) * 100):.1f}%)\n")
                        f.write(f"- **Trailing Stop:** {self.currency_symbol}{rec.trailing_stop:.2f}\n")
                        f.write(f"- **Position Size:** {self.currency_symbol}{rec.allocation:,.0f} ({rec.position_pct:.1f}% of portfolio)\n")
                        f.write(f"- **Risk/Reward Ratio:** {rec.risk_reward_ratio:.1f}:1\n\n")

                        if narrative.monitoring_tips:
                            f.write(f"#### 👁️ What to Monitor\n\n{narrative.monitoring_tips}\n\n")

                        if narrative.exit_strategy:
                            f.write(f"#### 🎯 Exit Strategy\n\n{narrative.exit_strategy}\n\n")
                    else:
                        # Basic info if no narrative
                        f.write(f"- Action: **{getattr(rec, 'final_action', '') or rec.signal}** (Score: {rec.overall_score:.1f}/10)\n")
                        f.write(f"- Entry: {self.currency_symbol}{rec.entry_price:.2f}\n")
                        f.write(f"- Target: {self.currency_symbol}{rec.price_target:.2f}\n")
                        f.write(f"- Stop Loss: {self.currency_symbol}{rec.stop_loss:.2f}\n")
                        f.write(f"- Position: {self.currency_symbol}{rec.allocation:,.0f}\n\n")

                    f.write("---\n\n")

                # Risk Disclaimer
                f.write("## ⚠️ Important Disclaimers\n\n")
                f.write(
                    "- This analysis is for **educational purposes only** and does not constitute financial advice.\n")
                f.write("- Past performance does not guarantee future results.\n")
                f.write("- Always conduct your own due diligence and consult with a qualified financial advisor.\n")
                f.write("- Trading involves substantial risk of loss.\n")
                f.write("- Never invest more than you can afford to lose.\n\n")

                f.write("---\n\n")
                f.write(f"*Report generated by QUANTA ⚡ ELITE v6.0*\n")

            logger.info(f"✓ Markdown report exported: {md_file}")
            print(f"✅ Detailed markdown report saved to: {md_file}")
            return True

        except Exception as e:
            logger.error(f"✗ Markdown export failed: {e}")
            return False


# ============================================================================
# INTERACTIVE INTERFACE
# ============================================================================

def create_investor_profile() -> InvestorProfile:
    """Create personalized investor profile with smart defaults"""

    display = RichDisplay()

    print("\n" + "=" * 80)
    print("👤 INVESTOR PROFILE")
    print("=" * 80)

    # Just ask for capital - use smart defaults for everything else
    capital_input = input("\n💰 Enter your trading capital [default: $2,500]: $").strip()
    if capital_input:
        try:
            capital = float(capital_input.replace(',', ''))
            capital = max(100, capital)  # Minimum $100
        except Exception:
            capital = 2500.0
    else:
        capital = 2500.0

    # Smart defaults - moderate/balanced approach
    risk_tolerance = "moderate"
    investment_timeline = "medium"
    experience_level = "intermediate"

    # Create profile with defaults
    profile = InvestorProfile(
        risk_tolerance=risk_tolerance,
        investment_timeline=investment_timeline,
        experience_level=experience_level,
        capital=capital
    )

    # Show simple summary
    print(f"\n✓ Profile Created: ${capital:,.2f} capital | Moderate risk | Balanced approach")
    print("=" * 80 + "\n")

    return profile


def parse_capital_input(text: str) -> Optional[float]:
    """Parse a capital amount typed by a person.

    Accepts currency symbols, commas (including Indian grouping), spaces and
    k / m / l(akh) / cr suffixes. Returns None for anything unreadable,
    non-finite or not positive — the caller re-prompts instead of silently
    falling back to a default ("$10,000" used to become 2,500).
    """
    if text is None:
        return None
    t = str(text).strip().lower().replace(',', '').replace(' ', '')
    for sym in ('$', '₹', 'rs.', 'rs', 'inr', 'usd'):
        t = t.replace(sym, '')
    mult = 1.0
    for suffix, m in (('cr', 1e7), ('lakh', 1e5), ('l', 1e5), ('k', 1e3), ('m', 1e6)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)], m
            break
    try:
        value = float(t) * mult
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def india_menu_counts() -> Dict[str, int]:
    """Company counts behind each India scan option, from the current index lists."""
    md = MarketDataProvider()
    lists = {k: set(md.get_index_constituents(k).get("tickers") or [])
             for k in ("nifty50", "sensex", "niftybank", "niftyit", "nifty500")}
    return {scan: len(set().union(*(lists[k] for k in keys)))
            for scan, keys in QuantaEliteTradingSystem.INDIA_SCAN_INDEXES.items()}


def run_interactive_session():
    """Enhanced interactive trading analysis session with personalized guidance"""

    display = RichDisplay()
    
    # 1. Clean Header (no boxes)
    print("\n")
    print("═" * 80)
    print("QUANTA ELITE TRADING SYSTEM v6.0 PRO".center(80))
    print("Architecting Financial Destiny".center(80))
    print("═" * 80)

    try:
        # =========================================================================
        # STEP 1: MARKET SELECTION - US or INDIA (FIRST!)
        # =========================================================================
        
        # 2. Market Selection (clean, no boxes)
        print("\n")
        print("🌍 SELECT MARKET".center(80))
        print("-" * 80)
        print()
        print("     [1] 🇺🇸  United States   (NYSE, NASDAQ, AMEX)")
        print("     [2] 🇮🇳  India           (NSE, BSE)")
        print()
        print("-" * 80)
        
        if console:
            market_choice = console.input("\nEnter choice (1-2) [default: 1]: ").strip() or "1"
        else:
            market_choice = input("\nEnter choice (1-2) [default: 1]: ").strip() or "1"
        market = "INDIA" if market_choice == "2" else "US"
        
        market_display = "United States 🇺🇸" if market == "US" else "India 🇮🇳"
        currency_symbol = "$" if market == "US" else "₹"
        
        print(f"\n✓ Selected Market: {market_display}")

        # [FIX] Fractional shares are supported by virtually every major US
        # broker (Schwab, Fidelity, Robinhood, IBKR, M1) and are essential for
        # small accounts that cannot fit a whole share of mega-caps within the
        # per-position cap. NSE/BSE do not support fractional shares.
        # The session used to force True for every US run, so the printed
        # advice to "disable in TradingConfig" could never take effect.
        # QUANTA_FRACTIONAL=0 now opts out.
        if market == "US":
            _frac_env = os.environ.get("QUANTA_FRACTIONAL", "").strip().lower()
            TradingConfig.FRACTIONAL_SHARES = _frac_env not in ("0", "false", "no", "off")
            if TradingConfig.FRACTIONAL_SHARES:
                print("ℹ  Fractional shares ENABLED (default for US). "
                      "Set QUANTA_FRACTIONAL=0 if your broker requires whole shares.")
            else:
                print("ℹ  Whole shares only (QUANTA_FRACTIONAL=0).")
        else:
            TradingConfig.FRACTIONAL_SHARES = False

        print("=" * 80)

        # Create investor profile (now knows the market/currency)
        print(f"\n💰 CAPITAL ALLOCATION ({currency_symbol})")
        print("=" * 80)
        # Market-aware defaults: ₹2,500 (~$30) could not buy one share of
        # most Nifty names, which silently turned the scan into a low-price
        # filter.
        default_capital = 2_500.0 if market == "US" else 2_00_000.0
        min_capital = 100.0 if market == "US" else 10_000.0
        capital = None
        for _attempt in range(3):
            capital_input = input(
                f"Enter your trading capital [default: {currency_symbol}{default_capital:,.0f}]: "
                f"{currency_symbol}"
            ).strip()
            if not capital_input:
                capital = default_capital
                break
            parsed = parse_capital_input(capital_input)
            if parsed is None:
                print(f"   ⚠️ Could not read '{capital_input}'. Enter a number such as 10000, 10,000 or 10k.")
                continue
            if parsed < min_capital:
                print(f"   ⚠️ Minimum capital is {currency_symbol}{min_capital:,.0f}.")
                continue
            capital = parsed
            break
        if capital is None:
            capital = default_capital
            print(f"   Using the default {currency_symbol}{default_capital:,.0f}.")

        # Create simplified profile
        profile = InvestorProfile(
            risk_tolerance="moderate",
            investment_timeline="medium",
            experience_level="intermediate",
            capital=capital
        )

        print(f"\n✓ Capital Set: {currency_symbol}{capital:,.2f}")
        print("=" * 80)

        # Market-specific scan options
        if market == "US":
            # =========================================================================
            # SIMPLIFIED SCAN MENU - 4 SMART OPTIONS
            # =========================================================================
            print("\n")
            print("═" * 75)
            print("🎯 SELECT SCAN TYPE".center(75))
            print("═" * 75)
            print()
            print("   [1] 🏆  FULL MARKET     Deep-scans EVERY assembled name (~670) | ~12-15 min")
            print("                          S&P 500 + DOW 30 + NASDAQ + Russell 2000 + Mid-Caps")
            print()

            print("   [2] ⚡  QUICK SCAN      100 most-traded large caps by $ volume (~2 min)")
            print()
            print("   [3] 🎯  FOCUSED         Growth | Value | Dividend | ETF (~1 min)")
            print()
            print("   [4] ✏️   CUSTOM          Enter your own tickers")
            print()
            print("   [5] 🚀  PRE-MOMENTUM    Asymmetric setups before the breakout (~1 min)")
            print("                          Semi / AI infra / hyperscaler-adjacent")
            print("                          Needs ≥3 of 6: RSI 50-65 | near 20DMA | vol ≥1.5× |")
            print("                          MACD turn | insider buying | 1M mom 0-15%; no chase alerts")
            print()
            print("═" * 75)

            scan_choice = input("\n🎯 Enter choice (1-5) [default: 1 - Full Market]: ").strip() or "1"
            
            if scan_choice == "1":  # FULL MARKET - All indexes
                scan_type = "full"
                custom_tickers = None
                print("\n✓ FULL MARKET SCAN")
                print("  Universe: S&P 500 + DOW 30 + NASDAQ-100 + Russell 2000 + Mid-Caps")
                print("  Every assembled name is deep-scanned; nothing is pre-pruned.")
                print("  Expect ~12-15 minutes — the request throttle is deliberate.")
                
            elif scan_choice == "2":  # QUICK SCAN
                scan_type = "quick"
                custom_tickers = None
                print("\n✓ QUICK SCAN (Top 100 stocks)")
                
            elif scan_choice == "3":  # FOCUSED - Sub-menu for strategy
                print("\n   📈 FOCUSED SCAN - Select Strategy:")
                print("   [a] 📈 Growth      High-growth tech & innovation (60 stocks)")
                print("   [b] 💎 Value       Undervalued quality stocks (60 stocks)")
                print("   [c] 💰 Dividend    High-yield income stocks (60 stocks)")
                print("   [d] 📊 ETFs        Top ETFs across all sectors (45 ETFs)")
                
                focus_choice = input("\n   Enter (a/b/c/d) [default: a]: ").strip().lower() or "a"
                
                # EXPANDED LISTS: 60 stocks each for proper diversification
                GROWTH_STOCKS = [
                    # Mega-cap tech leaders
                    'NVDA', 'TSLA', 'AMD', 'AMZN', 'META', 'GOOGL', 'MSFT', 'AAPL', 'AVGO', 'NFLX',
                    # Cloud & SaaS
                    # [M14 FIX] SPLK delisted (Cisco acquisition, 2024).
                    'CRM', 'NOW', 'SNOW', 'DDOG', 'MDB', 'WDAY', 'ZM', 'TEAM', 'OKTA',
                    # Cybersecurity
                    'PANW', 'CRWD', 'ZS', 'FTNT', 'NET', 'S', 'CYBR', 'QLYS',
                    # Fintech & Payments  ([M14 FIX] SQ -> XYZ after the 2025 rename)
                    'XYZ', 'COIN', 'AFRM', 'PYPL', 'V', 'MA', 'AXP',
                    # E-commerce & Consumer
                    'SHOP', 'MELI', 'SE', 'ETSY', 'W', 'CHWY',
                    # Mobility & Travel
                    'UBER', 'ABNB', 'DASH', 'LYFT', 'BKNG',
                    # Gaming & Entertainment
                    'RBLX', 'TTWO', 'EA', 'DIS', 'SPOT', 'ROKU',
                    # Biotech & Healthcare Innovation
                    'MRNA', 'ISRG', 'DXCM', 'VEEV', 'ALGN',
                    # AI & Semiconductors
                    'ARM', 'SMCI', 'MRVL', 'ANET', 'LRCX', 'KLAC'
                ]
                
                VALUE_STOCKS = [
                    # Financials - Banks
                    'BRK-B', 'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'USB', 'PNC', 'TFC',
                    # Financials - Insurance
                    'AIG', 'MET', 'PRU', 'ALL', 'TRV', 'CB',
                    # Healthcare - Pharma
                    'JNJ', 'PFE', 'MRK', 'ABBV', 'BMY', 'LLY', 'AMGN', 'GILD',
                    # Healthcare - Services
                    'CVS', 'CI', 'UNH', 'HUM', 'ELV',
                    # Retail
                    'WBA', 'KR', 'TGT', 'WMT', 'COST', 'HD', 'LOW', 'DG', 'DLTR',
                    # Industrials
                    'CAT', 'DE', 'HON', 'GE', 'MMM', 'EMR', 'ETN',
                    # Autos
                    'F', 'GM', 'STLA',
                    # Telecom
                    'T', 'VZ', 'TMUS',
                    # Tech Value
                    'INTC', 'IBM', 'CSCO', 'HPQ', 'DELL', 'HPE',
                    # Energy
                    'XOM', 'CVX', 'COP', 'PSX', 'VLO', 'MPC'
                ]
                
                # [M14 FIX] This list used to fold mortgage REITs (AGNC, NLY),
                # BDCs (ARCC, HTGC, ORCC) and MLPs (EPD, ET, MPLX) in with the
                # aristocrats, in a screen with NO payout-sustainability gate —
                # a yield-trap list presented as dividend income. Those are
                # pass-through structures whose distributions are not earnings-
                # covered in the way an equity dividend is, and several carry
                # K-1 tax treatment. They are separated out below, and the
                # equity screen now gates on payout sustainability.
                DIVIDEND_STOCKS = [
                    # Dividend Aristocrats
                    'JNJ', 'PG', 'KO', 'PEP', 'MO', 'PM', 'CL', 'MMM', 'EMR', 'SWK',
                    # Telecom
                    'VZ', 'T', 'TMUS',
                    # Tech
                    'IBM', 'CSCO', 'TXN', 'AVGO', 'QCOM',
                    # Energy  ([M14 FIX] PXD delisted, Exxon acquisition 2024)
                    'XOM', 'CVX', 'COP', 'EOG', 'DVN',
                    # Equity REITs (earnings-covered, corporate structure)
                    'O', 'SPG', 'AMT', 'PLD', 'EQIX', 'DLR', 'PSA', 'WELL', 'AVB',
                    # Utilities
                    'ED', 'DUK', 'SO', 'NEE', 'AEP', 'D', 'XEL', 'WEC', 'ES', 'EXC',
                    # Midstream C-corps (no K-1)
                    'WMB', 'KMI',
                    # Dividend ETFs
                    'SCHD', 'VYM', 'DVY', 'HDV', 'DGRO', 'VIG',
                ]

                # Kept separate so a user opts in deliberately. High headline
                # yields here often reflect return of capital, leverage or
                # NAV erosion rather than earnings.
                HIGH_YIELD_PASSTHROUGH = [
                    'AGNC', 'NLY', 'STWD',            # mortgage REITs
                    'ARCC', 'HTGC', 'OBDC', 'MAIN',   # BDCs
                    'EPD', 'ET', 'MPLX', 'ENB',       # MLPs / midstream partnerships
                ]
                
                ETF_LIST = [
                    # Broad Market
                    'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'IVV',
                    # Dividend ETFs
                    'SCHD', 'VIG', 'VYM', 'DVY', 'HDV', 'DGRO',
                    # Sector ETFs
                    'XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'XLRE', 'XLB', 'XLC',
                    # Tech & Growth
                    'VGT', 'ARKK', 'ARKG', 'ARKW', 'IGV',
                    # Semiconductors
                    'SMH', 'SOXX', 'PSI',
                    # Biotech
                    'IBB', 'XBI',
                    # Commodities
                    'GLD', 'SLV', 'GDX', 'SLX', 'USO',
                    # Bonds
                    'TLT', 'BND', 'AGG', 'LQD', 'HYG',
                    # International
                    'EFA', 'EEM', 'VEU', 'IEMG'
                ]
                
                if focus_choice == "b":
                    scan_type = "focused_value"
                    custom_tickers = VALUE_STOCKS
                    print(f"\n✓ VALUE FOCUS ({len(VALUE_STOCKS)} undervalued stocks)")
                elif focus_choice == "c":
                    scan_type = "focused_dividend"
                    custom_tickers = DIVIDEND_STOCKS
                    print(f"\n✓ DIVIDEND FOCUS ({len(DIVIDEND_STOCKS)} income stocks)")
                elif focus_choice == "d":
                    scan_type = "focused_etf"
                    custom_tickers = ETF_LIST
                    print(f"\n✓ ETF FOCUS ({len(ETF_LIST)} top ETFs)")
                else:
                    scan_type = "focused_growth"
                    custom_tickers = GROWTH_STOCKS
                    print(f"\n✓ GROWTH FOCUS ({len(GROWTH_STOCKS)} high-growth stocks)")
                    
            elif scan_choice == "4":  # CUSTOM
                scan_type = "custom"
                ticker_input = input("\n🎯 Enter tickers (comma-separated): ").strip().upper()
                custom_tickers = [t.strip() for t in ticker_input.split(',') if t.strip()]
                if custom_tickers:
                    print(f"\n✓ CUSTOM: {len(custom_tickers)} tickers")
                else:
                    # No typed tickers: run the Quick scan's data-driven universe.
                    scan_type, custom_tickers = "quick", None
                    print("\n✓ No tickers entered: running the Quick scan instead")

            elif scan_choice == "5":  # PRE-MOMENTUM - Asymmetric setups
                scan_type = "pre_momentum"
                custom_tickers = None
                print("\n✓ PRE-MOMENTUM SCAN (semis / AI-infrastructure index names)")
                print("  Hunting asymmetric setups: trend forming, smart money active, NOT yet extended")

            else:  # Default: Full Market
                scan_type = "full"
                custom_tickers = None
                print("\n✓ FULL MARKET SCAN (default)")
                print("  Every assembled name is deep-scanned; nothing is pre-pruned.")


        else:
            # INDIAN SCAN OPTIONS
            if display.enabled:
                display.print_section("📊 SELECT SCAN TYPE (INDIA MARKET)")
            else:
                print("\n📊 SELECT SCAN TYPE (INDIA MARKET):")

            # Sizes are counted from the lists actually scanned (NSE symbols);
            # the old labels claimed ~100/~150/~500/~600 for 56/59/184/192.
            _n = india_menu_counts()
            print(f"   1. Nifty 50 Quick ({_n['india_quick']} stocks)")
            print(f"   2. Standard (Nifty 50 + Sensex + Bank, {_n['india_standard']} stocks) [Recommended]")
            print(f"   3. Sectoral Focus (Nifty 50 + Bank + IT, {_n['india_sectoral']} stocks)")
            print(f"   4. Comprehensive (Nifty 50 / Next 50 / Midcap / extended list, {_n['india_comprehensive']} stocks)")
            print(f"   5. Full Coverage (all lists combined, {_n['india_full']} stocks)")

            scan_choice = input("\n🔍 Enter choice (1-5) [default: 2]: ").strip() or "2"

            india_scan_map = {
                "1": "india_quick",
                "2": "india_standard",
                "3": "india_sectoral",
                "4": "india_comprehensive",
                "5": "india_full"
            }
            scan_type = india_scan_map.get(scan_choice, "india_standard")
            custom_tickers = None  # India uses preset stock lists

        # Max positions
        if display.enabled:
            display.print_section("📊 PORTFOLIO SIZE")
        else:
            print("\n📊 PORTFOLIO SIZE")

        max_pos_input = input("   Max positions [1-15, default: 5]: ").strip()

        if max_pos_input:
            try:
                max_positions = int(max_pos_input)
                max_positions = max(1, min(15, max_positions))
            except ValueError:
                print(f"   ⚠️ Invalid input '{max_pos_input}', using default of 5")
                max_positions = 5
        else:
            max_positions = 5

        # Sector diversification
        diversify_input = input("\n🌐 Diversify across sectors? [Y/n]: ").strip().lower()
        diversify_sectors = diversify_input != 'n'

        # Summary
        if display.enabled:
            display.print_header("CONFIGURATION SUMMARY", "")
        else:
            print("\n" + "=" * 100)
            print("   CONFIGURATION SUMMARY")
            print("=" * 100)

        currency_symbol = "$" if market == "US" else "₹"
        market_display = "United States (NYSE, NASDAQ)" if market == "US" else "India (NSE, BSE)"
        
        print(f"   Market: {market_display}")
        print(f"   Capital: {currency_symbol}{capital:,.2f}")
        print(f"   Risk Profile: {profile.risk_tolerance.title()}")
        timeline = getattr(profile, 'investment_timeline', 'medium')  # default to 'medium'
        print(f"   Timeline: {timeline.title()}-term")
        scan_display_names = {
            'full': 'FULL MARKET', 'quick': 'QUICK SCAN', 'custom': 'CUSTOM',
            'focused_growth': 'FOCUSED (Growth)', 'focused_value': 'FOCUSED (Value)',
            'focused_dividend': 'FOCUSED (Dividend)', 'focused_etf': 'FOCUSED (ETF)',
            'india_quick': 'INDIA QUICK', 'india_standard': 'INDIA STANDARD',
            'india_sectoral': 'INDIA SECTORAL', 'india_comprehensive': 'INDIA COMPREHENSIVE',
            'india_full': 'INDIA FULL'
        }
        print(f"   Scan Type: {scan_display_names.get(scan_type, scan_type.upper())}")
        print(f"   Max Positions: {max_positions}")
        print(f"   Sector Diversification: {'Yes' if diversify_sectors else 'No'}")

        if not display.enabled:
            print("=" * 100)

        proceed = input("\n🚀 Proceed with analysis? [Y/n]: ").strip().lower()

        if proceed == 'n':
            print("\n👋 Analysis cancelled.")
            return None

        # Initialize system with market parameter
        print(f"\n⚙️  Initializing QUANTA Elite Trading System for {market_display}...\n")
        quanta = QuantaEliteTradingSystem(capital=capital, market=market)
        quanta.investor_profile = profile  # Attach profile
        quanta.display = display  # Attach display

        # Run market scan (pass custom_tickers if strategy-based)
        scan_results = quanta.scan_market(scan_type=scan_type, custom_tickers=custom_tickers)

        if not scan_results:
            print("\n❌ No opportunities found in current market conditions.")
            return None

        # Get recommendations
        recommendations = quanta.get_top_recommendations(
            max_positions=max_positions,
            diversify_sectors=diversify_sectors
        )

        # Generate narratives for each recommendation
        if ENHANCEMENTS_AVAILABLE:
            for rec in recommendations:
                rec.narrative = RecommendationNarrator.create_narrative(rec, profile)

        # Display results - Elite Minimal format (best output)
        quanta.display_elite_minimal(recommendations)

        # Export option
        export_choice = input("\n📁 Export results? (Saves CSV, JSON, and Markdown report) [Y/n]: ").strip().lower()

        if export_choice != 'n':
            quanta.export_results(recommendations)
            # Also export markdown report
            quanta.export_markdown_report(recommendations)

        return recommendations

    except KeyboardInterrupt:
        print("\n\n⚠️  Analysis interrupted by user.")
        return None

    except Exception as e:
        logger.error(f"\n❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def _ensure_console_encoding() -> None:
    """Make stdout/stderr able to carry the console decoration this module prints.

    Every banner, menu and table in the interactive session is drawn with box
    characters and emoji. On Windows a plain `python core/SP_ELITE_PRO.py`
    gets a cp1252 stdout whenever the stream is a pipe or a redirected log,
    and the very first banner line raised UnicodeEncodeError before the market
    menu was ever shown. Decoration must never be able to kill a session, so
    re-encode as UTF-8 with a replacement fallback. ``start.ps1`` sets
    PYTHONUTF8=1 and ``webapp/app.py`` does the same reconfigure, so this only
    closes the direct-CLI gap; TradingAnalysis_ELITE.main() already does it.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - stream refuses reconfiguration
            pass


def main() -> int:
    """Main entry point. Returns a process exit code (0 ok, 1 fatal error)."""

    _ensure_console_encoding()

    try:
        # Validate configuration
        if not TradingConfig.validate_config():
            print("❌ Configuration validation failed. Exiting.")
            return 1

        # Run interactive session
        recommendations = run_interactive_session()

        if recommendations:
            print("\n" + "=" * 100)
            print("✅ ANALYSIS COMPLETE")
            print("=" * 100)

            print("\n💡 BEST PRACTICES:")
            print("   ✓ Execute trades during market hours "
                  "(US 9:30 AM - 4:00 PM ET | NSE/BSE 9:15 AM - 3:30 PM IST)")
            print("   ✓ Set stop-loss orders immediately after entry")
            print("   ✓ Use limit orders for better execution")
            print("   ✓ Monitor positions daily")
            print("   ✓ Respect position sizing recommendations")
            print("   ✓ Maintain portfolio diversification")
            print("   ✓ Check stops daily; re-run the scan on each card's review date")

            print("\n⚠️  RISK DISCLAIMER:")
            print("   This software is for educational and research purposes ONLY.")
            print("   Not financial advice. Trading involves substantial risk of loss.")
            print("   Past performance does not guarantee future results.")
            print("   Always conduct your own due diligence and consult a financial advisor.")

            print("\n" + "=" * 100)

        print("\n   QUANTA ⚡ ELITE")
        print("   " + "═" * 80)
        print("   Architecting Financial Destiny | Global Markets: US + India\n")
        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.")
        return 0

    except Exception as e:
        # A fatal error must not exit 0: wrappers and CI would read a crashed
        # run as a successful one.
        logger.error(f"Fatal error in main: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
