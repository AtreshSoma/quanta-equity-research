#!/usr/bin/env python3
"""
APEX Short-Term Stock Picks Analyzer
Rule-Based Multi-Factor Scanner with Risk-First Position Sizing

Features:
- Multi-horizon daily analysis (5D, 1M, 3M, 6M)
- Wilder-standard indicator set (RSI, ATR, ADX/DI) + pattern recognition
- Risk-budgeted position sizing anchored on the actual execution price
- Liquidity, data-quality and earnings-proximity filters
- Sector- and correlation-aware pick selection
- Per-pick execution tickets, JSON / CSV / Markdown export
- Interactive prompts or a fully non-interactive CLI (``--help``)

Author: APEX Trading Systems
Date: 2026

DISCLAIMER: Educational purposes only. Not financial advice.
"""

__version__ = "3.2"

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta, date as _date
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo('America/New_York')
except Exception:  # zoneinfo unavailable
    _ET = None
import warnings
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    TimeoutError as FuturesTimeoutError,
)
from tabulate import tabulate
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from collections import defaultdict, Counter
from io import StringIO
import time
import os
import sys
import threading
import argparse
import contextlib
import textwrap

warnings.filterwarnings('ignore')

# `engine` (shared decision core) and `tracking` (signal ledger) live beside
# this file; make them importable however the script is launched.
_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

# Configure logging. The log goes to a fixed location (override with
# APEX_LOG_FILE) instead of whatever directory the process happened to start in.
_LOG_FILE = os.environ.get(
    'APEX_LOG_FILE',
    os.path.join(os.path.dirname(_CORE_DIR), 'logs', 'apex_trading.log'),
)
os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(_LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# ENHANCED CONFIGURATION
# ============================================================================

class Config:
    """Enhanced configuration with advanced parameters"""
    # Capital Management
    DEFAULT_CAPITAL = 2500
    MAX_RISK_PER_TRADE = 0.015  # 1.5% per trade
    MAX_RISK_AGGRESSIVE = 0.025  # 2.5% for high-conviction
    MAX_PORTFOLIO_RISK = 0.06  # 6% total portfolio risk
    MIN_POSITION = 150
    MAX_POSITION_PCT = 0.35  # Max 35% in single position

    # Quality Filters
    MIN_MARKET_CAP = 1_000_000_000  # $1B minimum (relaxed from $2B)
    MIN_PRICE = 5.0  # Avoid penny stocks (relaxed from $8)
    MAX_PRICE = 3000.0
    MIN_AVG_VOLUME = 500_000  # Liquidity requirement (relaxed from 750k)
    MIN_DOLLAR_VOLUME = 10_000_000  # $10M daily dollar volume (relaxed from $15M)

    # Scoring Thresholds
    MIN_SCORE = 60  # Out of 100
    STRONG_BUY_THRESHOLD = 80
    BUY_THRESHOLD = 70
    ACCUMULATE_THRESHOLD = 60

    # Risk/Reward (enforced NET of friction — see COMMISSION_BPS / SLIPPAGE_BPS)
    MIN_RISK_REWARD = 2.0  # Minimum 2:1 NET
    TARGET_WIN_RATE = 0.55  # 55% target

    # Execution friction (round-trip cost model used to debit the reward leg
    # before the R/R gate, per backtest-expert: "add friction everywhere").
    COMMISSION_BPS = 5.0   # 5 bps per side -> 10 bps round-trip (e.g. Alpaca-style)
    SLIPPAGE_BPS = 10.0    # 10 bps per side -> 20 bps round-trip (large-cap liquid)
    # Effective round-trip drag in % of entry price applied to the reward leg
    # when computing net R/R. Stop side is already executed at the stop, so we
    # apply the full round-trip to the upside leg as a conservative approximation.
    FRICTION_ROUND_TRIP_PCT = 2 * (COMMISSION_BPS + SLIPPAGE_BPS) / 10_000.0

    # Technical Thresholds
    RSI_OVERSOLD = 35
    RSI_OVERBOUGHT = 65
    RSI_OPTIMAL_LOW = 45
    RSI_OPTIMAL_HIGH = 55

    # Volatility Management
    LOW_VOLATILITY = 0.15
    MEDIUM_VOLATILITY = 0.30
    HIGH_VOLATILITY = 0.50

    # Timeframes
    SHORT_TERM_DAYS = 7  # 1 week
    MEDIUM_TERM_DAYS = 21  # 3 weeks
    LONG_TERM_DAYS = 63  # 3 months

    # Performance
    MAX_WORKERS = 3
    REQUEST_TIMEOUT = 12
    MAX_RETRIES = 3
    # Stooq fallback (often unreachable): fail fast and disable after repeated
    # failures so a dead host cannot stall the whole scan.
    STOOQ_TIMEOUT = 6
    STOOQ_FAILURE_LIMIT = 5

    # Yahoo request throttle. yfinance issues one un-throttled HTTP request per
    # .history()/.info call; under a ThreadPoolExecutor this bursts dozens of
    # requests at once and Yahoo replies 429, collapsing large scans (a Full
    # scan can lose half its universe to rate limiting). A small process-wide
    # minimum interval between Yahoo requests smooths the burst and is widened
    # automatically when a 429 is seen, then decays back toward baseline.
    YF_MIN_REQUEST_INTERVAL = 0.35    # seconds between Yahoo requests (global)
    YF_RATE_LIMIT_BACKOFF = 2.0       # multiply the interval after a 429
    YF_RATE_LIMIT_MAX_INTERVAL = 2.5  # cap for the adaptive interval
    YF_RATE_LIMIT_COOLDOWN = 15.0     # pause before retrying throttled tickers
    PROVIDER_RETRY_STORM_THRESHOLD = 20  # cool down first when this many fail

    # Advanced Filters
    MIN_INSTITUTIONAL_OWNERSHIP = 0.20  # Relaxed from 0.30
    MAX_INSTITUTIONAL_OWNERSHIP = 0.90  # Relaxed from 0.85
    MIN_ANALYST_COVERAGE = 2  # Relaxed from 3
    MIN_PROFIT_MARGIN = 0.05  # Relaxed from 0.10

    # Intraday scalping playbook. Yahoo does NOT support a 3-minute interval;
    # valid intraday intervals are 1m/2m/5m/15m/30m/60m/90m. Using an
    # unsupported interval makes yfinance return empty and emit a
    # "possibly delisted" warning for perfectly valid tickers, so the playbook
    # uses a supported 5-minute bar.
    SCALP_INTERVAL = "5m"
    SCALP_PERIOD = "5d"
    SCALP_OPENING_RANGE_BARS = 3  # 3 x 5m = first 15 minutes

    # Ticker hygiene: alias map for renamed symbols and a stale/delisted
    # exclusion list. Both are optional JSON/YAML files; sensible built-in
    # defaults are used when the files are absent.
    TICKER_ALIASES_FILE = "ticker_aliases.json"
    STALE_TICKERS_FILE = "stale_tickers.json"

    # Low-conviction overlay: thresholds that downgrade a pick to
    # "confirmation only" and halve its suggested size.
    LOW_CONVICTION_SCORE_BUFFER = 8.0   # within this many pts of MIN_SCORE
    LOW_CONVICTION_RS_FLOOR = 40.0      # RS rating below this is weak
    LOW_CONVICTION_RSI_OVERBOUGHT = 70.0
    LOW_CONVICTION_HIGH_VOL_PCT = 45.0  # 20D annualized vol % considered high
    LOW_CONVICTION_SIZE_FACTOR = 0.5    # halve size when low-conviction

    # Share accounting mode (single source of truth for the allocation engine).
    #   False -> WHOLE-SHARE mode: shares = floor(allocation / entry). A pick
    #            that cannot afford one whole share is funded with $0 and moved
    #            to the watchlist (never shown as a funded 0-share position).
    #   True  -> FRACTIONAL mode: shares may be decimal (e.g. 0.50), and the
    #            intended dollar allocation is deployed as-is.
    # Default is WHOLE-SHARE: most retail brokers fill whole shares, and it
    # eliminates the "$121 / 0 shares / $0 risk" ghost-position inconsistency.
    ALLOW_FRACTIONAL_SHARES = False
    FRACTIONAL_SHARE_DECIMALS = 2  # display precision for fractional shares

    # Portfolio construction limits (previously hard-coded inside the
    # selection routine; surfaced here so they can be tuned in one place).
    MAX_SECTOR_POSITIONS = 2      # max funded picks per GICS sector
    CORRELATION_THRESHOLD = 0.78  # reject a pick this correlated to a holding
    RS_PREFERRED_THRESHOLD = 50.0  # first-pass RS floor (top half of universe)
    SCALE_OUT_T1_FRACTION = 0.5   # fraction of the position taken off at T1

    # Trend strength thresholds. Trend_Strength is now a real Wilder ADX(14)
    # (the previous "range / ATR" proxy was mathematically bounded at 1.0, so
    # its >1.2 / >1.5 score bands were unreachable dead code).
    ADX_TRENDING = 20.0
    ADX_STRONG_TREND = 25.0

    # Scan watchdog. ThreadPoolExecutor futures are collected with an overall
    # deadline so one wedged provider socket cannot hang the entire run.
    SCAN_TIMEOUT_PER_TICKER = 8.0   # seconds of budget contributed per ticker
    SCAN_TIMEOUT_FLOOR = 300.0      # never give the scan less than this

    # Earnings proximity. <= BLOCK days rejects the setup outright; <= WARN
    # days applies a score haircut and raises the flag on the ticket. Beyond
    # WARN the date is recorded but NOT surfaced as a warning — an "earnings in
    # 78 days" banner is noise that trains the reader to ignore the real ones.
    EARNINGS_BLOCK_DAYS = 4
    EARNINGS_WARN_DAYS = 10

    # Index constituent sources, tried in order. Wikipedia moved the
    # NASDAQ-100 membership table out of the index article and into a dedicated
    # "List of ..." page, so the index page alone now yields NO table at all —
    # hence a list of candidates rather than a single URL.
    SP500_URLS = (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    )
    NASDAQ100_URLS = (
        "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        "https://en.wikipedia.org/wiki/Nasdaq-100",
    )
    MIN_SP500_CONSTITUENTS = 100    # sanity floor before trusting a parse
    MIN_NASDAQ100_CONSTITUENTS = 50

    # Sector-relative factor grading. A P/E of 30 is expensive for a utility
    # and cheap for a semiconductor, so every factor is graded as a PERCENTILE
    # against peers rather than against a fixed band. A sector needs at least
    # this many scored peers in the scan before it is used as the comparison
    # set; below that the grade falls back to the whole analysed universe.
    MIN_SECTOR_PEERS = 8

    # Reporting
    EXPORT_MARKDOWN = True          # also write a human-readable .md report


# ============================================================================
# NUMERIC SAFETY HELPERS
# ============================================================================
#
# Provider payloads are hostile in two specific ways that used to leak through
# this module:
#
#   1. ``pandas.Series.get(key, default)`` returns the DEFAULT only when the KEY
#      is absent — a present-but-NaN value (an indicator that has not warmed up,
#      e.g. Momentum_6M on a 110-bar history) is returned as NaN. That NaN then
#      silently propagates: every ``> threshold`` comparison is False, and
#      ``np.searchsorted`` places NaN at the END of the distribution, which
#      handed such names a perfect 99 RS rating.
#   2. Yahoo's ``.info`` dict frequently contains a key whose value is ``None``.
#      ``info.get('marketCap', 0)`` therefore returns ``None``, and the next
#      comparison raises ``TypeError: '>' not supported between NoneType and
#      int`` — aborting the whole ticker.
#
# ``_num`` / ``_pick`` / ``_info_num`` / ``_info_str`` close both holes.


def _num(value, default: float = 0.0) -> float:
    """Coerce *value* to a finite float, returning *default* for None/NaN/inf."""
    if value is None:
        return float(default)
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(out):
        return float(default)
    return out


def _pick(row, key: str, default: float = 0.0) -> float:
    """NaN-safe ``row.get(key, default)`` for a pandas Series of indicators."""
    try:
        return _num(row.get(key, default), default)
    except Exception:
        return float(default)


# ============================================================================
# US EQUITY TRADING CALENDAR
# ============================================================================
#
# A holding period stated as "9 days" is not actionable — a trader needs the
# DATE to be out by. Converting one to the other means counting sessions, not
# calendar days, which means knowing when the market is shut. The rules below
# are computed rather than hardcoded so they do not silently go stale.


# Single implementation shared with the signal ledger and the freshness gate;
# re-exported here because callers and tests import these names from this module.
from engine.decision import (  # noqa: E402
    SWING,
    Decision,
    Horizon,
    Scenario,
    SourceRef,
    assess_price_freshness,
)
from engine.evidence import SHORTTERM, get_evidence  # noqa: E402
from engine.types import FinalAction  # noqa: E402
from tracking.ledger import record_decisions  # noqa: E402
from engine.market_calendar import (  # noqa: E402,F401
    _HOLIDAY_CACHE,
    _nth_weekday,
    _observed,
    add_trading_days,
    easter_sunday,
    format_horizon,
    is_trading_day,
    us_market_holidays,
)


def validate_setup(setup, capital: float, tolerance: float = 0.02) -> List[str]:
    """Re-derive every number on a setup and report anything that disagrees.

    The report is a set of orders someone may actually place, so the arithmetic
    behind it is checked independently at the point of display rather than
    trusted because the code that produced it looked right. Each rule restates
    a relationship from first principles — if a future change breaks one, the
    pick is pulled from the report instead of quietly printing a wrong stop.

    Returns a list of human-readable problems; empty means consistent.
    """
    problems: List[str] = []
    entry = _num(getattr(setup, 'execution_entry', 0.0)) or _num(setup.entry_price)
    stop = _num(setup.stop_loss)
    t1, t2, t3 = (_num(setup.primary_target), _num(setup.secondary_target),
                  _num(setup.aggressive_target))
    shares = _num(setup.shares)

    # --- price geometry ---
    if entry <= 0:
        problems.append("entry price is not positive")
        return problems
    if not 0 < stop < entry:
        problems.append(f"stop ${stop:,.2f} is not below entry ${entry:,.2f}")
    if not entry < t1 < t2 < t3:
        problems.append(
            f"targets out of order: entry ${entry:,.2f} -> "
            f"T1 ${t1:,.2f} / T2 ${t2:,.2f} / T3 ${t3:,.2f}")
    if not (_num(setup.entry_zone_low) <= entry <= _num(setup.entry_zone_high)):
        problems.append(
            f"limit ${entry:,.2f} sits outside its own zone "
            f"${setup.entry_zone_low:,.2f}-${setup.entry_zone_high:,.2f}")

    # --- reward:risk, recomputed net of the same friction model ---
    risk_per_share = entry - stop
    if risk_per_share > 0:
        friction = entry * Config.FRICTION_ROUND_TRIP_PCT
        net_rr = max(0.0, (t1 - entry) - friction) / risk_per_share
        if abs(net_rr - _num(setup.risk_reward_ratio)) > 0.05:
            problems.append(
                f"reported R/R {setup.risk_reward_ratio:.2f} != recomputed "
                f"{net_rr:.2f}")
        if net_rr < Config.MIN_RISK_REWARD - 0.01:
            problems.append(
                f"net R/R {net_rr:.2f} is below the "
                f"{Config.MIN_RISK_REWARD:.1f} floor")

    # --- money: only meaningful once the pick is funded ---
    if getattr(setup, 'is_funded', False) and shares > 0:
        expected_alloc = shares * entry
        if abs(expected_alloc - _num(setup.executable_allocation_usd)) > tolerance:
            problems.append(
                f"capital ${setup.executable_allocation_usd:,.2f} != "
                f"{shares:g} shares x ${entry:,.2f} = ${expected_alloc:,.2f}")
        expected_risk = shares * risk_per_share
        if abs(expected_risk - _num(setup.risk_amount)) > tolerance:
            problems.append(
                f"risk ${setup.risk_amount:,.2f} != {shares:g} shares x "
                f"${risk_per_share:,.2f} = ${expected_risk:,.2f}")
        cap = capital * Config.MAX_RISK_AGGRESSIVE
        if capital > 0 and expected_risk > cap + tolerance:
            problems.append(
                f"risk ${expected_risk:,.2f} exceeds the per-trade cap "
                f"${cap:,.2f}")
        if capital > 0:
            expected_pct = expected_risk / capital * 100
            if abs(expected_pct - _num(setup.risk_pct)) > 0.05:
                problems.append(
                    f"risk% {setup.risk_pct:.2f} != {expected_pct:.2f}")
    elif getattr(setup, 'is_funded', False) and shares <= 0:
        problems.append("marked funded but holds no shares")

    # --- schedule ---
    etas = getattr(setup, 'eta_sessions', {}) or {}
    ordered = [etas[k] for k in ("T1", "T2", "T3") if k in etas]
    if ordered != sorted(ordered):
        problems.append(f"target ETAs are not in order: {etas}")
    if setup.min_hold_days > setup.expected_hold_days > setup.max_hold_days:
        problems.append(
            f"holding window is inconsistent: min {setup.min_hold_days} / "
            f"expected {setup.expected_hold_days} / max {setup.max_hold_days}")

    return problems


def _brief(exc: object, limit: int = 200) -> str:
    """Render an exception for a log line, bounded in length.

    Some exceptions carry an entire HTTP payload in their message. pandas 3.0,
    for instance, no longer accepts a literal HTML string in ``read_html`` — it
    treats the string as a path and raises
    ``FileNotFoundError: [Errno 2] No such file or directory: <html>...``,
    embedding the whole document. Interpolating that straight into a
    ``logger.warning`` dumps an entire Wikipedia page onto the user's console.
    Every exception rendered into a message goes through here.
    """
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + f"... [+{len(text) - limit} chars]"


def _info_num(info: Dict, *keys: str, default: float = 0.0) -> float:
    """First finite numeric value among *keys* in a Yahoo ``.info`` payload."""
    if not info:
        return float(default)
    for key in keys:
        if key in info:
            value = _num(info.get(key), float("nan"))
            if np.isfinite(value):
                return value
    return float(default)


def _info_str(info: Dict, key: str, default: str = "", limit: Optional[int] = None) -> str:
    """Non-None string from a Yahoo ``.info`` payload, optionally truncated."""
    value = (info or {}).get(key)
    text = str(value).strip() if value not in (None, "") else str(default)
    return text[:limit] if limit else text


# Percentile -> letter grade. The bands are evenly spaced so a grade is a
# direct, legible restatement of "where in the peer group does this sit".
_GRADE_BANDS = (
    (95.0, "A+"), (88.0, "A"), (80.0, "A-"),
    (72.0, "B+"), (64.0, "B"), (56.0, "B-"),
    (48.0, "C+"), (40.0, "C"), (32.0, "C-"),
    (24.0, "D+"), (16.0, "D"), (8.0, "D-"),
)


def percentile_of(value: float, population: List[float]) -> float:
    """Percentile rank (0-100) of *value* within *population*.

    Uses the midpoint convention so ties land in the middle of their run
    rather than all at the top, and returns a neutral 50 when there is nothing
    to compare against.
    """
    if not population:
        return 50.0
    arr = np.asarray([p for p in population if np.isfinite(p)], dtype=float)
    if arr.size == 0:
        return 50.0
    below = float(np.count_nonzero(arr < value))
    equal = float(np.count_nonzero(arr == value))
    return max(0.0, min(100.0, (below + 0.5 * equal) / arr.size * 100.0))


def grade_for(percentile: float) -> str:
    """Letter grade for a 0-100 percentile."""
    for floor, letter in _GRADE_BANDS:
        if percentile >= floor:
            return letter
    return "F"


def grade_rank(letter: str) -> int:
    """Ordinal for a letter grade (0 = F, 12 = A+), for sorting/averaging."""
    order = ["F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]
    return order.index(letter) if letter in order else 0


class _ScoreCard:
    """Score a metric whose inputs may be missing, by IMPUTING the gaps.

    Scoring an absent provider field as zero conflates "no data" with "bad" and
    pushes any ticker whose Yahoo ``.info`` call was blocked below the composite
    threshold for reasons that have nothing to do with the company.

    The obvious alternative — renormalising over just the components that were
    measurable — is worse: it extrapolates the observed components onto the
    missing ones, so a name with only its (strong) price-derived risk metrics
    available can be handed a perfect 100 while a fully-measured peer scores 89.
    Missing data must never be an advantage.

    So ``result`` imputes each unmeasured component at ``neutral_rate`` of its
    own maximum (0.5 = "no opinion") and divides by the FULL point total:

        score = (earned + neutral_rate * unmeasured_points) / total * 100

    A completely measured card is therefore scored exactly as before; a card
    with gaps is pulled toward the middle in proportion to how much is missing.
    """

    __slots__ = ("earned", "available")

    def __init__(self) -> None:
        self.earned = 0.0
        self.available = 0.0

    def add(self, earned: float, maximum: float) -> None:
        """Record a component that WAS evaluated."""
        self.earned += float(earned)
        self.available += float(maximum)

    def result(self, total: float = 100.0, neutral_rate: float = 0.5) -> float:
        total = float(total)
        if total <= 0:
            return 50.0
        unmeasured = max(0.0, total - self.available)
        score = (self.earned + neutral_rate * unmeasured) / total * 100.0
        return max(0.0, min(100.0, score))


def whole_shares(dollars: float, price: float) -> int:
    """Whole shares affordable for *dollars* at *price*, tolerant of FP error.

    ``int(994.0 // 99.40)`` is 9, not 10: 994/99.4 evaluates to
    9.999999999999998 in binary floating point. A plain floor therefore drops a
    share whenever the intended allocation happens to be an exact multiple of
    the entry price. The 1e-6 tolerance only ever absorbs that representation
    error — it can overshoot the budget by at most a hundredth of a cent.
    """
    if price <= 0 or dollars <= 0:
        return 0
    return int(np.floor(dollars / price + 1e-6))


def scoring_volume_ratio(indicators: Dict) -> float:
    """Volume ratio to SCORE with: the last completed bar when one is known.

    ``Volume_Ratio`` is today's (possibly partial, intraday) bar over its
    20-day average, which makes the same ticker score differently at 10:00 and
    at 16:00. ``analyze_stock`` therefore publishes ``Volume_Ratio_Adj`` from
    the last fully-closed bar. Everything that SCORES must read this helper;
    only the liquidity gate should look at the raw partial ratio, because
    rejecting genuinely dead tape early is the point of that gate.
    """
    return _num(
        indicators.get('Volume_Ratio_Adj', indicators.get('Volume_Ratio', 1.0)), 1.0
    )


# ============================================================================
# TICKER OUTCOME CLASSIFICATION
# ============================================================================

class Outcome:
    """Canonical per-ticker outcome classifications.

    A single ticker resolves to exactly one terminal state. Provider/data
    failures are NEVER counted as a successful analysis.
    """
    ACTIVE = "active"                       # analysed and produced a setup
    MODEL_FILTERED = "model_filtered"       # valid data, rejected by the model
    PROVIDER_AUTH_ERROR = "provider_auth_error"      # Yahoo Invalid Crumb / 401
    PROVIDER_RATE_LIMITED = "provider_rate_limited"  # Yahoo 429
    PROVIDER_TIMEOUT = "provider_timeout"            # Stooq / network timeout
    UNSUPPORTED_INTERVAL = "unsupported_interval"    # bad intraday interval
    NO_PRICE_DATA = "no_price_data"                  # truly empty after all providers
    INSUFFICIENT_HISTORY = "insufficient_history"    # < 100 daily bars
    INVALID_TICKER = "invalid_ticker"                # failed validation / delisted
    STALE_PRICE_DATA = "stale_price_data"            # last bar older than 1 session
    ANALYSIS_ERROR = "analysis_error"                # valid data, our code raised

    PROVIDER_FAILURES = {
        PROVIDER_AUTH_ERROR,
        PROVIDER_RATE_LIMITED,
        PROVIDER_TIMEOUT,
        UNSUPPORTED_INTERVAL,
    }
    DATA_MISSING = {NO_PRICE_DATA, INSUFFICIENT_HISTORY, INVALID_TICKER, STALE_PRICE_DATA}
    # Provider failures worth retrying after a cooldown (transient throttling /
    # auth-crumb / network), as opposed to a genuinely dead or invalid symbol.
    RETRYABLE_PROVIDER = {PROVIDER_AUTH_ERROR, PROVIDER_RATE_LIMITED, PROVIDER_TIMEOUT}

    LABELS = {
        ACTIVE: "Analyzed (opportunity)",
        MODEL_FILTERED: "Analyzed (filtered by model)",
        PROVIDER_AUTH_ERROR: "Provider auth error (Invalid Crumb / 401)",
        PROVIDER_RATE_LIMITED: "Provider rate limited (429)",
        PROVIDER_TIMEOUT: "Provider timeout (Stooq / network)",
        UNSUPPORTED_INTERVAL: "Unsupported interval",
        NO_PRICE_DATA: "No price data",
        INSUFFICIENT_HISTORY: "Insufficient history (<100 bars)",
        INVALID_TICKER: "Invalid / delisted ticker",
        STALE_PRICE_DATA: "Stale price data (last bar > 1 session old)",
        ANALYSIS_ERROR: "Analysis error (valid data, model raised)",
    }


def classify_provider_error(text: str) -> Optional[str]:
    """Map a provider error/log message to a canonical Outcome.

    Returns None when the text does not look like a known provider failure
    (so callers can fall back to ``no_price_data``).
    """
    if not text:
        return None
    t = str(text).lower()
    if "invalid crumb" in t or "unauthorized" in t or "401" in t:
        return Outcome.PROVIDER_AUTH_ERROR
    if ("429" in t or "too many request" in t or "rate limit" in t
            or "rate-limit" in t or "throttle" in t):
        return Outcome.PROVIDER_RATE_LIMITED
    if "timed out" in t or "timeout" in t or "connection broken" in t:
        return Outcome.PROVIDER_TIMEOUT
    if ("interval" in t and ("not supported" in t or "invalid" in t
                             or "valid intervals" in t)):
        return Outcome.UNSUPPORTED_INTERVAL
    return None


# Thread-local channel so the yfinance log-capture handler can communicate the
# most recent provider error back to the worker thread that triggered it
# (yfinance logs synchronously on the calling thread but swallows the error and
# returns an empty DataFrame instead of raising).
_provider_ctx = threading.local()


def _reset_provider_ctx() -> None:
    _provider_ctx.last_status = None


def _provider_ctx_status() -> Optional[str]:
    return getattr(_provider_ctx, "last_status", None)


class _YahooThrottle:
    """Process-wide, thread-safe throttle that spaces out Yahoo requests.

    yfinance has no built-in rate limiting, so a ``ThreadPoolExecutor`` fan-out
    bursts requests and Yahoo answers 429, silently returning empty frames and
    collapsing large scans. This throttle enforces a minimum interval between
    requests across *all* worker threads and widens that interval adaptively
    when a 429 is observed, then decays it back toward the baseline once clean
    responses resume.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._interval = Config.YF_MIN_REQUEST_INTERVAL

    def wait(self) -> None:
        """Block until this thread is allowed to issue the next Yahoo request."""
        with self._lock:
            now = time.monotonic()
            target = self._next_allowed if self._next_allowed > now else now
            wait_for = target - now
            self._next_allowed = target + self._interval
        if wait_for > 0:
            time.sleep(wait_for)

    def penalize(self) -> None:
        """Widen request spacing after a rate-limit (429) response."""
        with self._lock:
            self._interval = min(
                self._interval * Config.YF_RATE_LIMIT_BACKOFF,
                Config.YF_RATE_LIMIT_MAX_INTERVAL,
            )

    def relax(self) -> None:
        """Decay spacing back toward baseline after a clean response."""
        with self._lock:
            if self._interval > Config.YF_MIN_REQUEST_INTERVAL:
                self._interval = max(
                    Config.YF_MIN_REQUEST_INTERVAL, self._interval * 0.9
                )


# Single shared throttle for every Yahoo request the scan makes.
_YF_THROTTLE = _YahooThrottle()


# Human-readable labels for the model-filter rejection funnel surfaced in the
# scan summary, so a "0 candidates" result explains itself instead of looking
# like a bug. Keys match the reason strings passed to record_filter_reason().
FILTER_REASON_LABELS = {
    "price_out_of_range": "Price outside allowed band",
    "low_avg_volume": "Below minimum average volume",
    "low_dollar_volume": "Below minimum $ volume (illiquid)",
    "below_min_market_cap": "Below minimum market cap",
    "indicator_calc_failed": "Indicator calculation failed",
    "low_volume_ratio": "Volume too light vs 20-day average",
    "bearish_pattern_block": "Bearish pattern (death cross)",
    "earnings_proximity": "Earnings inside the blackout window",
    "score_below_min": "Composite score below threshold",
    "score_below_min_regime": "Score below threshold after regime cut",
    "invalid_risk_reward": "No valid stop/target geometry",
    "reward_risk_below_min": "Reward:risk below minimum (net of cost)",
    # Retained for older exports; sizing no longer rejects — an unfundable
    # candidate is routed to the watchlist instead (see analyze_stock).
    "below_min_position_size": "Cannot fund one whole share at this capital",
}


class ScanDiagnostics:
    """Thread-safe per-ticker outcome ledger for honest scan reporting.

    Outcomes are keyed by ticker so retries simply overwrite the prior state;
    all aggregate counts are derived from the final ledger and are therefore
    idempotent across the retry pass.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._status: Dict[str, str] = {}
        self._data_ok: set = set()
        self._filter_reasons: Dict[str, int] = defaultdict(int)

    def record(self, ticker: str, status: str) -> None:
        with self._lock:
            self._status[ticker] = status
            if status == Outcome.ACTIVE:
                self._data_ok.add(ticker)

    def mark_data_ok(self, ticker: str) -> None:
        with self._lock:
            self._data_ok.add(ticker)
            # Provisionally model_filtered until a setup is recorded.
            if self._status.get(ticker) not in (Outcome.ACTIVE,):
                self._status[ticker] = Outcome.MODEL_FILTERED

    def has_data(self, ticker: str) -> bool:
        """True once valid price data was obtained for *ticker*."""
        with self._lock:
            return ticker in self._data_ok

    def record_filter_reason(self, reason: str) -> None:
        with self._lock:
            self._filter_reasons[reason] += 1

    def summary(self, universe_count: int) -> Dict:
        with self._lock:
            counts: Dict[str, int] = defaultdict(int)
            for status in self._status.values():
                counts[status] += 1
            data_ok = len(self._data_ok)
            active = counts.get(Outcome.ACTIVE, 0)
            analysis_errors = counts.get(Outcome.ANALYSIS_ERROR, 0)
            # A crash after the data arrived is neither a data failure nor a
            # model rejection; keep it out of the "filtered by model" bucket so
            # that number stays a true statement about the strategy.
            model_filtered = max(0, data_ok - active - analysis_errors)
            provider_failures = sum(
                counts.get(s, 0) for s in Outcome.PROVIDER_FAILURES
            )
            invalid = counts.get(Outcome.INVALID_TICKER, 0)
            no_data = (counts.get(Outcome.NO_PRICE_DATA, 0)
                       + counts.get(Outcome.INSUFFICIENT_HISTORY, 0))
            stale = counts.get(Outcome.STALE_PRICE_DATA, 0)
            # True success = fraction of the universe we obtained valid data for
            # and ran the model on (whether or not it passed the filters).
            true_success_rate = (
                (data_ok / universe_count * 100.0) if universe_count else 0.0
            )
            provider_breakdown = {
                s: counts.get(s, 0)
                for s in Outcome.PROVIDER_FAILURES if counts.get(s, 0)
            }
            return {
                "universe_count": universe_count,
                "successfully_analyzed": data_ok,
                "opportunities_found": active,
                "model_filtered": model_filtered,
                "provider_failures": provider_failures,
                "provider_breakdown": provider_breakdown,
                "invalid_tickers": invalid,
                "no_data_tickers": no_data,
                "stale_data_tickers": stale,
                "analysis_errors": analysis_errors,
                "true_success_rate": round(true_success_rate, 1),
                "filter_reasons": dict(self._filter_reasons),
            }


class _ProviderLogClassifier(logging.Handler):
    """Capture yfinance/urllib3 error logs, classify them, and keep them off
    the console so the progress bar stays clean.

    The classified status is stashed on the calling thread's provider context
    so :meth:`EnhancedMarketData.get_price_history` can attribute an empty
    result to the right provider failure category.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            status = classify_provider_error(record.getMessage())
            if status is not None:
                _provider_ctx.last_status = status
        except Exception:
            pass


_PROVIDER_LOGGING_LOCK = threading.Lock()
_PROVIDER_LOGGING_READY = False


def configure_provider_logging() -> None:
    """Route noisy third-party provider logs to file only and attach the
    classifier. Idempotent.

    Guarded by a module flag: every ``StrategicStockAnalyzer`` construction used
    to open a fresh ``FileHandler`` on apex_trading.log even when the loggers
    were already wired, leaking one file descriptor per instance.
    """
    global _PROVIDER_LOGGING_READY
    with _PROVIDER_LOGGING_LOCK:
        if _PROVIDER_LOGGING_READY:
            return
        _PROVIDER_LOGGING_READY = True

    classifier = _ProviderLogClassifier()
    classifier.setLevel(logging.WARNING)
    try:
        file_handler = logging.FileHandler('apex_trading.log')
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
    except Exception:
        file_handler = None

    for name in ("yfinance", "urllib3", "urllib3.connectionpool", "peewee"):
        lg = logging.getLogger(name)
        # Avoid duplicate handlers on re-import.
        if any(isinstance(h, _ProviderLogClassifier) for h in lg.handlers):
            continue
        lg.setLevel(logging.WARNING)
        lg.propagate = False  # keep these errors off the root console handler
        lg.addHandler(classifier)
        if file_handler is not None:
            lg.addHandler(file_handler)


class TickerRegistry:
    """Universe hygiene: alias mapping for renamed tickers and a stale/delisted
    exclusion list.

    Aliases and stale lists are loaded from optional JSON files (configurable
    via :class:`Config`); built-in defaults cover the well-known cases so the
    scanner is correct out of the box.
    """

    # Renamed symbols (old -> new). Block Inc. changed its NYSE ticker from
    # SQ to XYZ in 2025; FB -> META and TWTR delisting are classic examples.
    DEFAULT_ALIASES = {
        "SQ": "XYZ",
        "FB": "META",
    }

    # Known delisted / acquired symbols that should never be scanned.
    DEFAULT_STALE = {
        "TWTR",   # taken private 2022
        "ATVI",   # acquired by Microsoft 2023
        "FRC",    # First Republic, failed 2023
        "SIVB",   # SVB Financial, failed 2023
        "FISV",   # renamed to FI (Fiserv) 2023
    }

    def __init__(self, aliases: Optional[Dict[str, str]] = None,
                 stale: Optional[set] = None):
        self.aliases = {k.upper(): v.upper() for k, v in
                        (aliases if aliases is not None else self.DEFAULT_ALIASES).items()}
        self.stale = {s.upper() for s in
                      (stale if stale is not None else self.DEFAULT_STALE)}
        # Hygiene counters for the universe summary.
        self.aliases_replaced = 0
        self.stale_excluded = 0
        self.invalid = 0
        self._alias_applied: Dict[str, str] = {}

    @classmethod
    def load(cls, base_dir: Optional[str] = None) -> "TickerRegistry":
        """Build a registry, overlaying optional on-disk config files onto the
        built-in defaults. Never raises — falls back to defaults on any error.
        """
        aliases = dict(cls.DEFAULT_ALIASES)
        stale = set(cls.DEFAULT_STALE)
        search_dirs = []
        if base_dir:
            search_dirs.append(base_dir)
        try:
            search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            pass
        search_dirs.append(os.getcwd())

        for directory in search_dirs:
            try:
                ap = os.path.join(directory, Config.TICKER_ALIASES_FILE)
                if os.path.isfile(ap):
                    with open(ap, "r") as fh:
                        data = json.load(fh)
                    if isinstance(data, dict):
                        aliases.update({str(k).upper(): str(v).upper()
                                        for k, v in data.items()})
            except Exception as exc:
                logger.debug(f"Could not load ticker aliases from {directory}: {_brief(exc)}")
            try:
                sp = os.path.join(directory, Config.STALE_TICKERS_FILE)
                if os.path.isfile(sp):
                    with open(sp, "r") as fh:
                        data = json.load(fh)
                    if isinstance(data, list):
                        stale.update(str(s).upper() for s in data)
                    elif isinstance(data, dict):
                        stale.update(str(s).upper() for s in data.keys())
            except Exception as exc:
                logger.debug(f"Could not load stale tickers from {directory}: {_brief(exc)}")

        return cls(aliases=aliases, stale=stale)

    def is_stale(self, ticker: str) -> bool:
        return ticker.upper() in self.stale

    def resolve(self, ticker: str) -> Tuple[Optional[str], str]:
        """Resolve a raw ticker to (symbol, classification).

        classification is one of:
          - ``active``           : usable as-is
          - ``alias_replaced``   : renamed; symbol is the new ticker
          - ``delisted_excluded``: on the stale list; symbol is None
        """
        t = (ticker or "").strip().upper()
        if not t:
            return None, "invalid"
        if t in self.stale:
            return None, "delisted_excluded"
        if t in self.aliases:
            new = self.aliases[t]
            # Guard against an alias that points at a stale symbol.
            if new in self.stale:
                return None, "delisted_excluded"
            self._alias_applied[new] = t
            return new, "alias_replaced"
        return t, "active"

    def original_for(self, resolved_ticker: str) -> Optional[str]:
        """Return the pre-alias symbol for a resolved ticker, if any."""
        return self._alias_applied.get(resolved_ticker.upper())


class TradingSignal(Enum):
    """Trading signals and the composite score each one requires.

    ``min_score`` used to read 100/85/70/55 while ``_determine_signal`` tested
    80/70 — two contradictory sets of thresholds, with the enum's simply unused
    (a STRONG_BUY needing a score of 100 is unreachable by construction). The
    values below are now the REAL thresholds and _determine_signal reads them,
    so there is one place to change them.

    ACCUMULATE mirrors ``Config.MIN_SCORE``: it is the default tier, and every
    caller already enforces that floor upstream, which is why WATCH and AVOID
    are unreachable from the scanner. They are retained because callers and
    tests refer to them by name.
    """
    STRONG_BUY = ("STRONG_BUY", 80, "🔥")
    BUY = ("BUY", 70, "✅")
    ACCUMULATE = ("ACCUMULATE", 60, "📈")
    WATCH = ("WATCH", 50, "👁️")
    AVOID = ("AVOID", 0, "⛔")

    def __init__(self, name: str, score: int, emoji: str):
        self.signal_name = name
        self.min_score = score
        self.emoji = emoji


class TimeFrame(Enum):
    """Trading timeframes with typical holding periods"""
    SWING_TRADE = ("3-7 days", 7, "⚡")
    SHORT_TERM = ("1-3 weeks", 21, "📊")
    MEDIUM_TERM = ("3-8 weeks", 56, "📈")
    POSITION = ("2-4 months", 120, "🎯")

    def __init__(self, description: str, days: int, emoji: str):
        self.description = description
        self.days = days
        self.emoji = emoji


class MarketRegime(Enum):
    """Market regime classification"""
    BULL_STRONG = "Strong Bull"
    BULL_WEAK = "Weak Bull"
    SIDEWAYS = "Sideways/Ranging"
    BEAR_WEAK = "Weak Bear"
    BEAR_STRONG = "Strong Bear"
    VOLATILE = "High Volatility"


# ============================================================================
# ENHANCED DATA CLASSES
# ============================================================================

@dataclass
class PriceLevel:
    """Price levels with probability and strength"""
    price: float
    level_type: str  # 'support', 'resistance', 'pivot'
    strength: float  # 0-1
    touches: int
    distance_pct: float


@dataclass
class TradingSetup:
    """Complete trading setup with all parameters"""
    ticker: str
    company: str
    sector: str
    industry: str

    # Signal and Scoring
    signal: str
    confidence: float  # 0-100
    overall_score: float

    # Multi-component scores
    technical_score: float
    fundamental_score: float
    momentum_score: float
    quality_score: float
    analyst_score: float
    pattern_score: float

    # Entry Strategy
    entry_price: float
    entry_zone_low: float
    entry_zone_high: float
    optimal_entry: float

    # Exit Strategy
    primary_target: float
    secondary_target: float
    aggressive_target: float
    stop_loss: float
    trailing_stop_pct: float

    # Timeframe and Holding
    timeframe: str
    expected_hold_days: int
    min_hold_days: int
    max_hold_days: int

    # Position Sizing
    allocation_usd: float
    allocation_pct: float
    shares: float  # whole shares, or decimal in Config.ALLOW_FRACTIONAL_SHARES mode
    position_score: float  # Quality of position sizing

    # Risk Metrics
    risk_amount: float
    risk_pct: float
    reward_amount: float
    risk_reward_ratio: float
    expected_return_pct: float
    max_loss_pct: float

    # Technical Indicators
    rsi: float
    rsi_trend: str
    macd: float
    macd_signal: float
    macd_histogram: float
    stochastic: float
    bb_position: float
    atr: float
    atr_pct: float
    volume_ratio: float      # completed-bar ratio — the one the scores used
    obv_trend: str

    # Price Action
    current_price: float
    price_vs_sma20: float
    price_vs_sma50: float
    price_vs_sma200: float
    high_52w: float
    low_52w: float
    distance_from_high_pct: float
    distance_from_low_pct: float

    # Support/Resistance
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    pivot_point: float = 0.0

    # Fundamentals
    pe_ratio: float = 0.0
    peg_ratio: float = 0.0
    forward_pe: float = 0.0
    price_to_sales: float = 0.0
    price_to_book: float = 0.0
    roe: float = 0.0
    roa: float = 0.0
    profit_margin: float = 0.0
    operating_margin: float = 0.0
    revenue_growth: float = 0.0
    earnings_growth: float = 0.0
    debt_to_equity: float = 0.0
    current_ratio: float = 0.0
    free_cash_flow: float = 0.0

    # Market Metrics
    market_cap: float = 0.0
    market_cap_category: str = ""
    avg_volume: float = 0.0
    avg_dollar_volume: float = 0.0
    institutional_ownership: float = 0.0
    insider_ownership: float = 0.0
    short_interest: float = 0.0

    # Momentum Metrics
    momentum_1w: float = 0.0
    momentum_1m: float = 0.0
    momentum_3m: float = 0.0
    momentum_6m: float = 0.0
    relative_strength: float = 0.0

    # Volatility and Risk
    volatility_20d: float = 0.0
    volatility_60d: float = 0.0
    beta: float = 1.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0

    # Pattern Recognition
    detected_patterns: List[str] = field(default_factory=list)
    breakout_potential: float = 0.0
    trend_strength: str = ""
    adx: float = 0.0  # Wilder ADX(14): trend strength, direction-agnostic
    # Today's (possibly partial) volume bar over its 20-day average. Kept for
    # reference only; `volume_ratio` above carries the completed-bar value the
    # scores were actually computed from.
    volume_ratio_raw: float = 0.0

    # Catalysts and News
    earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    analyst_rating: str = ""
    analyst_target: float = 0.0
    num_analysts: int = 0

    # Meta
    analysis_timestamp: datetime = field(default_factory=datetime.now)
    last_updated: str = ""

    # Conviction overlay (set by _apply_conviction_overlay).
    conviction: str = "NORMAL"  # NORMAL | LOW
    conviction_reasons: List[str] = field(default_factory=list)

    # Allocation engine accounting (set by _finalize_share_accounting).
    #   intended_allocation_usd   -> dollars the sizing engine wanted to deploy
    #   executable_allocation_usd -> dollars actually deployable as whole/frac
    #                                shares (== allocation_usd for funded picks)
    #   is_funded                 -> True when the pick holds >0 shares
    #   moved_to_watchlist        -> True when whole-share rounding blocked it
    intended_allocation_usd: float = 0.0
    executable_allocation_usd: float = 0.0
    is_funded: bool = True
    moved_to_watchlist: bool = False
    # Price the plan actually executes at (the limit price = optimal_entry).
    # Share counts, deployed capital and dollar risk are all derived from THIS
    # price, not from entry_price (the last close), so the reported risk
    # matches the risk budget the position was sized against. Left at 0.0 for
    # setups constructed outside analyze_stock, which then fall back to
    # entry_price.
    execution_entry: float = 0.0
    # Why the allocation engine could not fund the pick (watchlist rows).
    watchlist_reason: str = ""

    # --- Sector-relative factor model (set by _assign_factor_grades) ---
    # Raw 0-100 sub-scores...
    value_score: float = 50.0
    growth_score: float = 50.0
    profitability_score: float = 50.0
    # ...their percentile rank against sector peers, and the letter grade for
    # each. Keys: Value, Growth, Profitability, Momentum, Technicals.
    factor_percentiles: Dict[str, float] = field(default_factory=dict)
    factor_grades: Dict[str, str] = field(default_factory=dict)
    grade_basis: str = ""          # "sector" or "universe"
    sector_rank: int = 0
    sector_peers: int = 0
    composite_percentile: float = 50.0
    quant_rating: str = ""         # headline grade-derived rating
    thesis: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)

    # --- Dated plan (set in analyze_stock) ---
    # Sessions expected to reach each target, and the actual calendar dates a
    # trader works to. "9 days" is not actionable; "by Fri 11 Sep" is.
    eta_sessions: Dict[str, int] = field(default_factory=dict)
    eta_dates: Dict[str, str] = field(default_factory=dict)
    plan_date: str = ""          # session the plan was generated for
    review_date: str = ""        # reassess here even if nothing has triggered
    time_stop_date: str = ""     # close the position by this date regardless
    breakeven_win_rate: float = 0.0   # hit-rate needed for this R/R to break even

    # Data-quality / hygiene provenance (set in analyze_stock).
    info_missing: bool = False     # Yahoo fundamentals endpoint was blocked
    alias_applied: bool = False    # ticker was remapped from a renamed symbol
    source_ticker: str = ""        # original symbol before alias resolution

    # How T1 was derived ("nearest multi-week resistance" / "ATR projection"),
    # and any multi-week level sitting within 1.5% above entry (the trade must
    # clear it first; it is a risk, not a target).
    target_method: str = ""
    at_resistance: List[float] = field(default_factory=list)
    # Absolute-band fundamental/quality legs before sector-relative
    # re-expression, and the regime x earnings multiplier applied to the score.
    fundamental_abs: float = 50.0
    quality_abs: float = 50.0
    score_multiplier: float = 1.0
    # Random-walk baselines over the time-stop window. Not forecasts: the odds
    # a driftless path touches each level, i.e. what "no edge" looks like.
    t1_touch_probability: Optional[float] = None
    stop_touch_probability: Optional[float] = None
    # Provenance of the price the levels were computed from.
    price_as_of: str = ""          # market date of the last daily bar
    price_source: str = ""         # yahoo / stooq
    data_freshness: str = "unknown"
    # The authoritative decision (engine.decision.Decision.to_dict()) and the
    # label every surface shows (CANDIDATE unless the strategy is validated).
    label: str = ""
    decision: Optional[Dict] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in self.__dict__.items()}


def decision_for_setup(setup: TradingSetup) -> Decision:
    """Capture the final, post-peer-scoring and post-funding short-term call."""
    try:
        action = FinalAction(str(setup.signal).upper())
    except ValueError:
        action = FinalAction.WATCHLIST
    if action.allocates_new_capital and not (setup.is_funded and setup.shares > 0):
        action = FinalAction.WATCHLIST
    days = max(1, int(setup.expected_hold_days or SWING.trading_days))
    price_date = _date.fromisoformat(setup.price_as_of) if setup.price_as_of else None
    decision = Decision(
        ticker=setup.ticker, engine="shortterm",
        horizon=Horizon("shortterm", days, f"{days} trading sessions"),
        action=action, evidence=get_evidence(SHORTTERM),
        as_of=setup.price_as_of or None, score=setup.overall_score,
        factor_scores={
            "technical": setup.technical_score,
            "fundamental": setup.fundamental_score,
            "momentum": setup.momentum_score,
            "quality": setup.quality_score,
            "analyst": setup.analyst_score,
            "pattern": setup.pattern_score,
        },
        risk_metrics={
            "volatility_20d": setup.volatility_20d,
            "beta": setup.beta,
            "max_drawdown": setup.max_drawdown,
        },
        reference_price=setup.current_price, entry=setup.execution_entry,
        stop=setup.stop_loss, target=setup.primary_target,
        reward_risk=setup.risk_reward_ratio,
        sizing={"allocation_usd": setup.allocation_usd if action.allocates_new_capital else 0.0,
                "shares": setup.shares if action.allocates_new_capital else 0},
        sources=[SourceRef("px", "price", setup.price_source or "unknown",
                           as_of=setup.price_as_of)],
        freshness=assess_price_freshness(price_date, ticker=setup.ticker),
    )
    if not setup.info_missing:
        decision.sources.append(SourceRef("info", "fundamentals", "yfinance"))
    if action is FinalAction.WATCHLIST and setup.signal in {
            "STRONG_BUY", "BUY", "ACCUMULATE"}:
        decision.blocking.append("Setup passed model screens but has no fundable position.")
    return decision


# ============================================================================
# NETWORK SESSION
# ============================================================================

class RobustSession:
    """Enhanced HTTP session with retry logic"""

    _session = None

    @classmethod
    def get_session(cls) -> requests.Session:
        """Get or create robust session"""
        if cls._session is None:
            cls._session = requests.Session()

            retry_strategy = Retry(
                total=Config.MAX_RETRIES,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"]
            )

            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=15,
                pool_maxsize=25
            )

            cls._session.mount("http://", adapter)
            cls._session.mount("https://", adapter)

            cls._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

        return cls._session


# ============================================================================
# ENHANCED MARKET DATA FETCHER
# ============================================================================

class EnhancedMarketData:
    """Enhanced market data with multiple sources"""

    _sp500_cache: List[str] = []
    _nasdaq100_cache: List[str] = []
    # True when an index list came from the built-in fallback rather than the
    # live constituent page, so the scan can report reduced coverage instead of
    # printing a universe size that looks deliberate.
    sp500_degraded: bool = False
    nasdaq100_degraded: bool = False
    _stooq_disabled: bool = False
    _stooq_failures: int = 0
    # The breaker counter is mutated from every worker thread; += on a class
    # attribute is a read-modify-write and loses increments without a lock.
    _stooq_lock = threading.Lock()

    def __init__(self):
        self.session = RobustSession.get_session()
        self._cache = {}

    def _scrape_constituents(self, urls, minimum: int) -> List[str]:
        """Pull a ticker column out of the first usable Wikipedia table.

        Tries each URL in turn and, within a page, every table that exposes a
        ticker/symbol column, accepting the first one that clears *minimum*
        rows. Returning a short list is treated as a FAILURE rather than a
        result: a page-layout change that yields three parsed symbols must not
        masquerade as an index.
        """
        for url in urls:
            try:
                response = self.session.get(url, timeout=Config.REQUEST_TIMEOUT)
                response.raise_for_status()
                # StringIO is REQUIRED: pandas deprecated literal-HTML input in
                # 2.1 and removed it in 3.0, where a bare string is treated as
                # a file path. The resulting FileNotFoundError embeds the ENTIRE
                # document, so interpolating it into a log line dumps a whole
                # Wikipedia page onto the console.
                tables = pd.read_html(StringIO(response.text))
            except Exception as exc:
                logger.debug("Constituent fetch failed for %s: %s", url, _brief(exc))
                continue

            for table in tables:
                column = next(
                    (c for c in table.columns
                     if any(k in str(c).lower() for k in ("ticker", "symbol"))),
                    None,
                )
                if column is None:
                    continue
                tickers = [
                    # Wikipedia writes class shares as BRK.B; Yahoo wants BRK-B.
                    str(v).strip().upper().replace(".", "-")
                    for v in table[column].tolist()
                ]
                tickers = [t for t in tickers if self._validate_ticker(t)]
                if len(tickers) >= minimum:
                    logger.info("✅ Fetched %d constituents from %s", len(tickers), url)
                    return tickers
        return []

    def get_sp500_tickers(self) -> List[str]:
        """S&P 500 constituents, with a loud fallback."""
        if EnhancedMarketData._sp500_cache:
            return EnhancedMarketData._sp500_cache

        tickers = self._scrape_constituents(
            Config.SP500_URLS, Config.MIN_SP500_CONSTITUENTS)
        if tickers:
            EnhancedMarketData._sp500_cache = tickers
            EnhancedMarketData.sp500_degraded = False
            return tickers

        fallback = self._get_fallback_sp500()
        logger.warning(
            "S&P 500 constituents unavailable — falling back to a built-in "
            "list of only %d names; index coverage is REDUCED.", len(fallback),
        )
        EnhancedMarketData._sp500_cache = fallback
        EnhancedMarketData.sp500_degraded = True
        return fallback

    def get_nasdaq100_tickers(self) -> List[str]:
        """NASDAQ-100 constituents, with a loud fallback.

        The membership table used to live on the Nasdaq-100 article; Wikipedia
        moved it to "List of NASDAQ-100 companies" and left the index page with
        no parseable table at all, so both are tried in order.
        """
        if EnhancedMarketData._nasdaq100_cache:
            return EnhancedMarketData._nasdaq100_cache

        tickers = self._scrape_constituents(
            Config.NASDAQ100_URLS, Config.MIN_NASDAQ100_CONSTITUENTS)
        if tickers:
            EnhancedMarketData._nasdaq100_cache = tickers
            EnhancedMarketData.nasdaq100_degraded = False
            return tickers

        fallback = self._get_fallback_nasdaq100()
        logger.warning(
            "NASDAQ-100 constituents unavailable — falling back to a built-in "
            "list of only %d names; index coverage is REDUCED.", len(fallback),
        )
        EnhancedMarketData._nasdaq100_cache = fallback
        EnhancedMarketData.nasdaq100_degraded = True
        return fallback

    def get_high_momentum_stocks(self) -> List[str]:
        """Curated list of high-momentum and trending stocks"""
        return [
            # Mega-cap leaders
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B',

            # High-growth tech
            'PLTR', 'SNOW', 'CRWD', 'NET', 'DDOG', 'ZS', 'PANW', 'FTNT', 'S',
            'TEAM', 'MNDY', 'CFLT', 'DOMO',

            # Cloud & SaaS
            'CRM', 'NOW', 'WDAY', 'ZM', 'DOCN', 'ESTC', 'MDB',

            # Fintech & Payments (XYZ = Block Inc., renamed from SQ in 2025)
            'SOFI', 'HOOD', 'COIN', 'XYZ', 'PYPL', 'V', 'MA', 'AFRM', 'UPST',

            # EV & Clean Energy
            'RIVN', 'LCID', 'NIO', 'XPEV', 'LI', 'ENPH', 'SEDG', 'RUN',

            # AI & Quantum
            'IONQ', 'RGTI', 'QBTS', 'AI', 'SMCI', 'PATH', 'BBAI',

            # Semiconductors
            'AMD', 'AVGO', 'QCOM', 'AMAT', 'LRCX', 'KLAC', 'ASML', 'TSM',
            'MRVL', 'MU', 'INTC',

            # Consumer & E-commerce
            'ABNB', 'DASH', 'UBER', 'LYFT', 'SHOP', 'MELI', 'SE',

            # Healthcare Tech
            'TDOC', 'DOCS', 'VEEV', 'DXCM', 'ISRG', 'ILMN',

            # Gaming & Entertainment
            'RBLX', 'U', 'DKNG', 'PENN', 'TTWO', 'EA',

            # Cybersecurity
            'OKTA', 'CYBR', 'RPD', 'TENB',

            # Data Analytics (PLTR already listed under high-growth tech)
            'GTLB', 'BILL', 'PCTY',

            # Growth Leaders
            'SPOT', 'ROKU', 'PINS', 'SNAP', 'TWLO',

            # 2024-2026 momentum leaders (AI infrastructure, power, recent IPOs)
            'ARM', 'APP', 'ANET', 'VST', 'CEG', 'GEV', 'DELL', 'RDDT',
            'MSTR', 'CVNA',
        ]

    def get_price_history(self, ticker: str, stock: yf.Ticker) -> Tuple[pd.DataFrame, str]:
        """Fetch daily history, falling back to Stooq when Yahoo is throttled.

        Returns ``(dataframe, status)`` where status is an :class:`Outcome`
        value. Yahoo can intermittently return 401 "Invalid Crumb" errors under
        concurrent load even though single requests succeed, so retry a couple
        of times with a short backoff before falling back to Stooq. The status
        lets the caller report provider failures honestly instead of silently
        treating them as "no data".
        """
        last_exc = None
        for attempt in range(Config.MAX_RETRIES):
            _reset_provider_ctx()
            # Space this request out across all worker threads so the scan does
            # not burst-trigger Yahoo's 429 rate limiter.
            _YF_THROTTLE.wait()
            try:
                # auto_adjust=True is yfinance's default, but state it: the
                # entire indicator set assumes a split/dividend-adjusted series,
                # and the Stooq fallback below has to be checked against the
                # same assumption.
                hist = stock.history(period="1y", auto_adjust=True)
                if not hist.empty:
                    _YF_THROTTLE.relax()
                    hist.attrs['source'] = 'yahoo'
                    return hist, Outcome.ACTIVE
            except Exception as exc:
                last_exc = exc
                captured = classify_provider_error(str(exc))
                if captured is not None:
                    _provider_ctx.last_status = captured
            # A 429 (raised or captured from the yfinance log) means we are
            # being throttled: widen the global spacing and back off harder so
            # the remaining workers stop hammering Yahoo.
            if _provider_ctx_status() == Outcome.PROVIDER_RATE_LIMITED:
                _YF_THROTTLE.penalize()
                if attempt < Config.MAX_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
                continue
            # Brief backoff lets Yahoo's crumb/rate-limit window reset.
            if attempt < Config.MAX_RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))

        # The yfinance log-capture handler (or the exception above) records the
        # most recent provider error for this worker thread.
        yahoo_status = _provider_ctx_status()
        if last_exc is not None:
            logger.debug(f"Yahoo history unavailable for {ticker}: {_brief(last_exc, 120)}")

        stooq_hist, stooq_status = self._get_stooq_history(ticker)
        if not stooq_hist.empty:
            stooq_hist.attrs['source'] = 'stooq'
            return stooq_hist, Outcome.ACTIVE

        # No data from either provider. Prefer the specific provider failure
        # (auth/rate-limit/timeout) over a generic "no data" classification so
        # the scan summary can distinguish a blocked provider from a truly
        # dead symbol.
        status = yahoo_status or stooq_status or Outcome.NO_PRICE_DATA
        return pd.DataFrame(), status

    def get_stock_info(self, ticker: str, stock: yf.Ticker) -> Dict:
        """Fetch Yahoo fundamentals; return an empty dict when the endpoint is blocked."""
        try:
            # Share the same global throttle so the fundamentals call does not
            # re-trigger Yahoo's rate limiter right after the history fetch.
            _YF_THROTTLE.wait()
            info = stock.info
            if isinstance(info, dict) and info:
                _YF_THROTTLE.relax()
                return info
            return {}
        except Exception as exc:
            if classify_provider_error(str(exc)) == Outcome.PROVIDER_RATE_LIMITED:
                _YF_THROTTLE.penalize()
            logger.debug(f"Yahoo info unavailable for {ticker}: {_brief(exc, 120)}")
            return {}

    def _get_stooq_history(self, ticker: str) -> Tuple[pd.DataFrame, str]:
        """Fetch one year of daily OHLCV data from Stooq as a Yahoo fallback.

        Returns ``(dataframe, status)``. Stooq is frequently unreachable from
        some networks. A connection timeout there would otherwise hang each
        ticker through the session's full retry chain, stalling the whole scan.
        A class-level circuit breaker disables Stooq for the rest of the run
        after repeated failures, and requests are made with a short fixed
        timeout and no internal retries so each attempt fails fast.
        """
        if EnhancedMarketData._stooq_disabled:
            return pd.DataFrame(), Outcome.PROVIDER_TIMEOUT

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=370)
            stooq_ticker = ticker.lower().replace('-', '.')
            url = (
                "https://stooq.com/q/d/l/"
                f"?s={stooq_ticker}.us&i=d"
                f"&d1={start_date:%Y%m%d}&d2={end_date:%Y%m%d}"
            )
            # Use a bare request (not self.session) so the retry adapter does
            # not multiply the wait, and cap the timeout tightly.
            response = requests.get(
                url,
                timeout=Config.STOOQ_TIMEOUT,
                headers={'User-Agent': 'Mozilla/5.0'},
            )
            response.raise_for_status()

            hist = pd.read_csv(StringIO(response.text))
            if hist.empty or 'Date' not in hist.columns:
                return pd.DataFrame(), Outcome.NO_PRICE_DATA

            hist['Date'] = pd.to_datetime(hist['Date'], errors='coerce')
            hist = hist.dropna(subset=['Date']).set_index('Date').sort_index()
            required_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            if any(column not in hist.columns for column in required_columns):
                return pd.DataFrame(), Outcome.NO_PRICE_DATA

            # Stooq's daily CSV is UNADJUSTED, while the Yahoo path above is
            # split/dividend adjusted. Dividends only shift the series ~1-2%
            # over a year, but an unadjusted split leaves a 2:1-style cliff in
            # the middle of the window that corrupts every moving average, the
            # 52-week range, ATR and all the momentum lookbacks. Rather than
            # score a ticker off a broken series, reject the fallback frame
            # when it contains a split-sized discontinuity.
            closes = pd.to_numeric(hist['Close'], errors='coerce').dropna()
            step = (closes / closes.shift(1)).dropna()
            if len(step) and (step.max() > 1.8 or step.min() < 0.55):
                logger.debug(
                    "Stooq series for %s looks unadjusted across a split "
                    "(max step %.2fx, min step %.2fx); discarding.",
                    ticker, float(step.max()), float(step.min()),
                )
                return pd.DataFrame(), Outcome.NO_PRICE_DATA

            with EnhancedMarketData._stooq_lock:
                EnhancedMarketData._stooq_failures = 0
            return hist[required_columns], Outcome.ACTIVE

        except Exception as exc:
            with EnhancedMarketData._stooq_lock:
                EnhancedMarketData._stooq_failures += 1
                failures = EnhancedMarketData._stooq_failures
                trip = (not EnhancedMarketData._stooq_disabled
                        and failures >= Config.STOOQ_FAILURE_LIMIT)
                if trip:
                    EnhancedMarketData._stooq_disabled = True
            if trip:
                logger.warning(
                    "Stooq fallback disabled for this run after "
                    f"{failures} consecutive failures "
                    "(host unreachable); relying on Yahoo only."
                )
            logger.debug(f"Stooq history unavailable for {ticker}: {_brief(exc, 120)}")
            return pd.DataFrame(), Outcome.PROVIDER_TIMEOUT

    def _validate_ticker(self, ticker: str) -> bool:
        """Validate ticker format"""
        if not ticker or not isinstance(ticker, str):
            return False
        ticker = ticker.strip().upper()
        return (len(ticker) <= 5 and
                ticker.replace('-', '').replace('.', '').isalnum() and
                not ticker.startswith(('_', '-', '.')))

    def _get_fallback_sp500(self) -> List[str]:
        """Fallback S&P 500"""
        return ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B',
                'V', 'JPM', 'JNJ', 'WMT', 'MA', 'PG', 'UNH', 'HD', 'DIS', 'BAC',
                'NFLX', 'ADBE', 'CRM', 'ABBV', 'TMO', 'CVX', 'PEP', 'AVGO',
                'NKE', 'MCD', 'AMD', 'INTC', 'CSCO', 'ORCL', 'ACN', 'TXN', 'QCOM']

    def _get_fallback_nasdaq100(self) -> List[str]:
        """Fallback NASDAQ-100"""
        return ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO',
                'COST', 'NFLX', 'ADBE', 'PEP', 'CSCO', 'CMCSA', 'INTC', 'QCOM',
                'TXN', 'AMAT', 'INTU', 'AMD', 'MU', 'ISRG', 'LRCX', 'ADI']


# ============================================================================
# ADVANCED TECHNICAL ANALYZER
# ============================================================================

class AdvancedTechnicalAnalyzer:
    """Enhanced technical analysis with pattern recognition"""

    @staticmethod
    def calculate_comprehensive_indicators(hist: pd.DataFrame) -> Dict:
        """Calculate all technical indicators with advanced metrics"""
        try:
            if hist.empty or len(hist) < 50:
                return {}

            indicators = {}
            hist = hist.copy()

            # Ensure numeric types
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in hist.columns:
                    hist[col] = pd.to_numeric(hist[col], errors='coerce')

            # === MOVING AVERAGES ===
            hist['SMA_10'] = hist['Close'].rolling(window=10, min_periods=10).mean()
            hist['SMA_20'] = hist['Close'].rolling(window=20, min_periods=20).mean()
            hist['SMA_50'] = hist['Close'].rolling(window=50, min_periods=50).mean()
            hist['SMA_200'] = hist['Close'].rolling(window=200, min_periods=100).mean()

            hist['EMA_9'] = hist['Close'].ewm(span=9, adjust=False, min_periods=9).mean()
            hist['EMA_12'] = hist['Close'].ewm(span=12, adjust=False, min_periods=12).mean()
            hist['EMA_26'] = hist['Close'].ewm(span=26, adjust=False, min_periods=26).mean()
            hist['EMA_50'] = hist['Close'].ewm(span=50, adjust=False, min_periods=50).mean()

            # === MACD ===
            hist['MACD'] = hist['EMA_12'] - hist['EMA_26']
            hist['MACD_Signal'] = hist['MACD'].ewm(span=9, adjust=False, min_periods=9).mean()
            hist['MACD_Histogram'] = hist['MACD'] - hist['MACD_Signal']

            # === RSI (Wilder) ===
            # Wilder's RSI smooths average gain/loss with an RMA (alpha = 1/14),
            # not a simple moving average. The SMA form used previously reacts
            # roughly twice as fast and prints several points away from every
            # mainstream charting package, so bands calibrated as "RSI 50-65"
            # did not describe the same state a trader sees on their chart.
            delta = hist['Close'].diff()
            gain = delta.clip(lower=0.0)
            loss = (-delta).clip(lower=0.0)
            avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            rs = avg_gain / avg_loss.replace(0.0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            # A zero average loss is a pure up-streak (RSI 100), or a completely
            # flat series (undefined -> neutral 50). Warm-up bars stay NaN.
            flat = avg_loss.notna() & (avg_loss <= 0)
            rsi = rsi.mask(flat & (avg_gain > 0), 100.0)
            rsi = rsi.mask(flat & (avg_gain <= 0), 50.0)
            hist['RSI'] = rsi

            # RSI Trend
            hist['RSI_SMA'] = hist['RSI'].rolling(window=14, min_periods=14).mean()

            # === STOCHASTIC ===
            low_14 = hist['Low'].rolling(window=14, min_periods=14).min()
            high_14 = hist['High'].rolling(window=14, min_periods=14).max()
            hist['Stoch_K'] = 100 * (hist['Close'] - low_14) / (high_14 - low_14 + 1e-10)
            hist['Stoch_D'] = hist['Stoch_K'].rolling(window=3, min_periods=3).mean()

            # === BOLLINGER BANDS ===
            bb_std = hist['Close'].rolling(window=20, min_periods=20).std()
            hist['BB_Middle'] = hist['SMA_20']
            hist['BB_Upper'] = hist['BB_Middle'] + (bb_std * 2)
            hist['BB_Lower'] = hist['BB_Middle'] - (bb_std * 2)
            hist['BB_Width'] = hist['BB_Upper'] - hist['BB_Lower']
            hist['BB_Position'] = (hist['Close'] - hist['BB_Lower']) / (hist['BB_Width'] + 1e-10)
            hist['BB_Squeeze'] = bb_std / hist['BB_Middle']

            # === ATR (Average True Range) ===
            hist['High-Low'] = hist['High'] - hist['Low']
            hist['High-PrevClose'] = abs(hist['High'] - hist['Close'].shift(1))
            hist['Low-PrevClose'] = abs(hist['Low'] - hist['Close'].shift(1))
            hist['TR'] = hist[['High-Low', 'High-PrevClose', 'Low-PrevClose']].max(axis=1)
            # Wilder's ATR is an RMA of True Range, not an SMA. Every stop and
            # target in this module is ATR-scaled, so using the textbook form
            # keeps those levels consistent with the charts a trader checks.
            hist['ATR'] = hist['TR'].ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            hist['ATR_Pct'] = hist['ATR'] / hist['Close'].replace(0.0, np.nan)

            # === VOLUME INDICATORS ===
            hist['Volume_SMA'] = hist['Volume'].rolling(window=20, min_periods=20).mean()
            hist['Volume_Ratio'] = hist['Volume'] / (hist['Volume_SMA'] + 1)

            # On-Balance Volume (OBV)
            hist['OBV'] = (np.sign(hist['Close'].diff()) * hist['Volume']).fillna(0).cumsum()
            hist['OBV_SMA'] = hist['OBV'].rolling(window=20, min_periods=20).mean()

            # Volume-Weighted Average Price (VWAP) - 20-day rolling
            rolling_window = 20
            hist['VWAP'] = (
                (hist['Close'] * hist['Volume']).rolling(rolling_window).sum() /
                hist['Volume'].rolling(rolling_window).sum()
            )

            # === MOMENTUM INDICATORS ===
            hist['Momentum_5D'] = hist['Close'].pct_change(periods=5)
            hist['Momentum_10D'] = hist['Close'].pct_change(periods=10)
            hist['Momentum_1M'] = hist['Close'].pct_change(periods=20)
            hist['Momentum_3M'] = hist['Close'].pct_change(periods=60)
            hist['Momentum_6M'] = hist['Close'].pct_change(periods=126)

            # Rate of Change
            hist['ROC_12'] = ((hist['Close'] - hist['Close'].shift(12)) /
                              hist['Close'].shift(12)) * 100

            # === VOLATILITY ===
            hist['Returns'] = hist['Close'].pct_change()
            hist['Volatility_20D'] = hist['Returns'].rolling(window=20).std() * np.sqrt(252)
            hist['Volatility_60D'] = hist['Returns'].rolling(window=60).std() * np.sqrt(252)

            # === TREND INDICATORS: Wilder ADX(14) + directional movement ===
            # The previous "Trend_Strength" was mean(High-Low) / ATR. Because
            # True Range is by definition >= (High - Low), that ratio is bounded
            # at 1.0 — so the score bands that tested it for > 1.2 and > 1.5
            # could never fire and 5 of the 100 technical points were dead.
            # This is the real thing: +DI / -DI smoothed by an RMA, DX, and ADX.
            up_move = hist['High'].diff()
            down_move = -hist['Low'].diff()
            plus_dm = pd.Series(
                np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                index=hist.index,
            )
            minus_dm = pd.Series(
                np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                index=hist.index,
            )
            atr_safe = hist['ATR'].replace(0.0, np.nan)
            plus_di = 100 * plus_dm.ewm(
                alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
            minus_di = 100 * minus_dm.ewm(
                alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
            di_sum = (plus_di + minus_di).replace(0.0, np.nan)
            dx = 100 * (plus_di - minus_di).abs() / di_sum
            hist['Plus_DI'] = plus_di
            hist['Minus_DI'] = minus_di
            hist['ADX'] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            # Legacy key kept so existing callers keep working; it now carries
            # ADX, and the score bands below were re-calibrated to ADX units.
            hist['Trend_Strength'] = hist['ADX']

            # === SUPPORT & RESISTANCE ===
            hist['Resistance_20'] = hist['High'].rolling(window=20, min_periods=20).max()
            hist['Support_20'] = hist['Low'].rolling(window=20, min_periods=20).min()
            # Prior-bar support/resistance (today excluded). Support_20 includes
            # today's own low and Resistance_20 today's own high, so
            # "close < Support_20" and "close > Resistance_20" are both
            # arithmetically impossible: the Support-Breakdown and
            # Resistance-Breakout patterns could never fire, and the exit
            # logic's breakout test compared price with its own session high.
            hist['Support_20_Prev'] = hist['Low'].shift(1).rolling(
                window=20, min_periods=20).min()
            hist['Resistance_20_Prev'] = hist['High'].shift(1).rolling(
                window=20, min_periods=20).max()
            hist['Resistance_50'] = hist['High'].rolling(window=50, min_periods=50).max()
            hist['Support_50'] = hist['Low'].rolling(window=50, min_periods=50).min()

            # === PIVOT POINTS ===
            hist['Pivot'] = (hist['High'] + hist['Low'] + hist['Close']) / 3
            hist['R1'] = 2 * hist['Pivot'] - hist['Low']
            hist['S1'] = 2 * hist['Pivot'] - hist['High']
            hist['R2'] = hist['Pivot'] + (hist['High'] - hist['Low'])
            hist['S2'] = hist['Pivot'] - (hist['High'] - hist['Low'])

            # === GET LATEST VALUES ===
            latest = hist.iloc[-1]
            previous = hist.iloc[-2] if len(hist) >= 2 else latest
            current_price = _num(latest.get('Close'), 0.0)
            if current_price <= 0:
                return {}

            # 52-Week Range
            high_52w = _num(
                hist['High'].tail(252).max() if len(hist) >= 252 else hist['High'].max(),
                current_price,
            )
            low_52w = _num(
                hist['Low'].tail(252).min() if len(hist) >= 252 else hist['Low'].min(),
                current_price,
            )
            high_52w_prev = _num(
                hist['High'].shift(1).tail(252).max() if len(hist) >= 252
                else hist['High'].shift(1).max(),
                high_52w,
            )

            # Every read below goes through _pick, which substitutes the default
            # for a present-but-NaN value. Series.get() does NOT: it only honours
            # the default when the KEY is missing, so a not-yet-warmed indicator
            # (Momentum_6M needs 127 bars; a 110-bar history yields NaN) used to
            # leak NaN into the scores, the setup record and the RS percentile —
            # where np.searchsorted sorts NaN to the top and awarded a perfect
            # RS 99 to the names with the LEAST history.
            atr_default = current_price * 0.02

            # === COMPILE INDICATORS ===
            indicators = {
                'current_price': current_price,
                'bars': int(len(hist)),

                # Moving Averages
                'SMA_10': _pick(latest, 'SMA_10', current_price),
                'SMA_20': _pick(latest, 'SMA_20', current_price),
                'SMA_50': _pick(latest, 'SMA_50', current_price),
                'SMA_200': _pick(latest, 'SMA_200', current_price),
                'SMA_50_Prev': _pick(previous, 'SMA_50', current_price),
                'SMA_200_Prev': _pick(previous, 'SMA_200', current_price),
                'EMA_9': _pick(latest, 'EMA_9', current_price),
                'EMA_12': _pick(latest, 'EMA_12', current_price),
                'EMA_26': _pick(latest, 'EMA_26', current_price),

                # Oscillators
                'RSI': _pick(latest, 'RSI', 50.0),
                'RSI_SMA': _pick(latest, 'RSI_SMA', 50.0),
                'Stoch_K': _pick(latest, 'Stoch_K', 50.0),
                'Stoch_D': _pick(latest, 'Stoch_D', 50.0),

                # MACD
                'MACD': _pick(latest, 'MACD', 0.0),
                'MACD_Signal': _pick(latest, 'MACD_Signal', 0.0),
                'MACD_Histogram': _pick(latest, 'MACD_Histogram', 0.0),

                # Bollinger Bands
                'BB_Upper': _pick(latest, 'BB_Upper', current_price * 1.05),
                'BB_Middle': _pick(latest, 'BB_Middle', current_price),
                'BB_Lower': _pick(latest, 'BB_Lower', current_price * 0.95),
                'BB_Position': _pick(latest, 'BB_Position', 0.5),
                'BB_Squeeze': _pick(latest, 'BB_Squeeze', 0.02),

                # Volatility
                'ATR': _pick(latest, 'ATR', atr_default),
                'ATR_Pct': _pick(latest, 'ATR_Pct', 0.02),
                'Volatility_20D': _pick(latest, 'Volatility_20D', 0.20),
                'Volatility_60D': _pick(latest, 'Volatility_60D', 0.20),

                # Volume
                'Volume': _pick(latest, 'Volume', 0.0),
                'Volume_SMA': _pick(latest, 'Volume_SMA', 0.0),
                'Volume_Ratio': _pick(latest, 'Volume_Ratio', 1.0),
                'OBV': _pick(latest, 'OBV', 0.0),
                'OBV_SMA': _pick(latest, 'OBV_SMA', 0.0),
                'VWAP': _pick(latest, 'VWAP', current_price),

                # Momentum. A NaN here means the lookback has not warmed up, and
                # 0.0 ("no move") is the only honest substitute — it neither
                # rewards nor punishes a short history.
                'Momentum_5D': _pick(latest, 'Momentum_5D', 0.0),
                'Momentum_10D': _pick(latest, 'Momentum_10D', 0.0),
                'Momentum_1M': _pick(latest, 'Momentum_1M', 0.0),
                'Momentum_3M': _pick(latest, 'Momentum_3M', 0.0),
                'Momentum_6M': _pick(latest, 'Momentum_6M', 0.0),
                'ROC_12': _pick(latest, 'ROC_12', 0.0),

                # Trend (Wilder ADX / directional movement)
                'ADX': _pick(latest, 'ADX', 0.0),
                'Plus_DI': _pick(latest, 'Plus_DI', 0.0),
                'Minus_DI': _pick(latest, 'Minus_DI', 0.0),
                'Trend_Strength': _pick(latest, 'Trend_Strength', 0.0),

                # Support/Resistance
                'Support_20': _pick(latest, 'Support_20', current_price * 0.95),
                'Support_20_Prev': _pick(
                    latest, 'Support_20_Prev', _pick(latest, 'Support_20', current_price * 0.95)
                ),
                'Resistance_20': _pick(latest, 'Resistance_20', current_price * 1.05),
                'Resistance_20_Prev': _pick(
                    latest, 'Resistance_20_Prev',
                    _pick(latest, 'Resistance_20', current_price * 1.05),
                ),
                'Support_50': _pick(latest, 'Support_50', current_price * 0.90),
                'Resistance_50': _pick(latest, 'Resistance_50', current_price * 1.10),

                # Pivots
                'Pivot': _pick(latest, 'Pivot', current_price),
                'R1': _pick(latest, 'R1', current_price * 1.02),
                'S1': _pick(latest, 'S1', current_price * 0.98),
                'R2': _pick(latest, 'R2', current_price * 1.04),
                'S2': _pick(latest, 'S2', current_price * 0.96),

                # 52-Week Range
                'High_52W': high_52w,
                'Low_52W': low_52w,
                'High_52W_Prev': high_52w_prev,
                'Distance_From_High': (
                    (current_price - high_52w) / high_52w if high_52w > 0 else 0.0
                ),
                'Distance_From_Low': (
                    (current_price - low_52w) / low_52w if low_52w > 0 else 0.0
                ),

                # Historical data for advanced calculations
                'hist_close': hist['Close'].to_numpy(dtype=float),
                'hist_volume': hist['Volume'].to_numpy(dtype=float),
                'hist_returns': hist['Returns'].dropna().to_numpy(dtype=float),
            }

            return indicators

        except Exception as e:
            logger.error(f"Error calculating indicators: {_brief(e)}")
            return {}

    @staticmethod
    def detect_patterns(hist: pd.DataFrame, indicators: Dict) -> List[str]:
        """Detect chart patterns"""
        patterns = []

        try:
            if hist.empty or len(hist) < 20:
                return patterns

            current_price = indicators.get('current_price', hist['Close'].iloc[-1])
            sma_50 = indicators.get('SMA_50', current_price)
            sma_200 = indicators.get('SMA_200', current_price)

            resistance_20_prev = indicators.get('Resistance_20_Prev',
                                                indicators.get('Resistance_20', current_price))
            # Prior-bar support: Support_20 includes today's own low and the
            # close can never be below it, so comparing against it made the
            # breakdown pattern unreachable.
            support_20_prev = indicators.get('Support_20_Prev',
                                             indicators.get('Support_20', current_price))

            # Golden Cross / Death Cross with 10-bar lookback.
            # `hist` here is the RAW OHLCV frame — calculate_comprehensive_indicators
            # enriches a private copy, so hist['SMA_50'] does not exist and the old
            # lookup silently produced empty series (the Death-Cross hard block could
            # never fire). Recompute the 50/200-day SMAs locally with the same
            # windows as the indicator engine so the crossover detection works.
            lookback_bars = min(10, len(hist) - 2)
            if len(hist) >= 60:
                sma50_series = hist['Close'].rolling(window=50, min_periods=50).mean().values
                sma200_series = hist['Close'].rolling(window=200, min_periods=100).mean().values
            else:
                sma50_series = np.array([])
                sma200_series = np.array([])

            recent_golden = False
            recent_death = False
            if len(sma50_series) > 1 and len(sma200_series) > 1:
                for i in range(1, lookback_bars + 1):
                    try:
                        if (pd.notna(sma50_series[-i]) and pd.notna(sma200_series[-i]) and
                                pd.notna(sma50_series[-i - 1]) and pd.notna(sma200_series[-i - 1])):
                            if (not recent_golden and
                                    sma50_series[-i] > sma200_series[-i] and
                                    sma50_series[-i - 1] <= sma200_series[-i - 1]):
                                recent_golden = True
                            if (not recent_death and
                                    sma50_series[-i] < sma200_series[-i] and
                                    sma50_series[-i - 1] >= sma200_series[-i - 1]):
                                recent_death = True
                        if recent_golden and recent_death:
                            break
                    except IndexError:
                        break

            if recent_golden:
                patterns.append("Golden Cross (Recent)")
            elif sma_50 > sma_200:
                patterns.append("Above 50/200 MA")

            if recent_death:
                patterns.append("Death Cross (Recent)")
            elif sma_50 < sma_200:
                patterns.append("Below 50/200 MA")

            # Breakout
            if pd.notna(resistance_20_prev) and current_price > resistance_20_prev:
                patterns.append("Resistance Breakout")

            # Breakdown (vs the prior 20-bar low, today excluded)
            if pd.notna(support_20_prev) and current_price < support_20_prev:
                patterns.append("Support Breakdown")

            # Bollinger Squeeze
            if indicators.get('BB_Squeeze', 0.02) < 0.015:
                patterns.append("Bollinger Squeeze")

            # Volume Surge (session-adjusted so an intraday surge with light
            # raw bar volume still flags when pacing is exceptional).
            vr_surge = scoring_volume_ratio(indicators)
            if vr_surge > 2.0:
                patterns.append("Volume Surge")

            # RSI Divergence (simple version)
            rsi = indicators.get('RSI', 50)
            if rsi < 30:
                patterns.append("RSI Oversold")
            elif rsi > 70:
                patterns.append("RSI Overbought")

            # Cup and Handle (simplified)
            recent_prices = hist['Close'].tail(60)
            if len(recent_prices) >= 60:
                mid_low = recent_prices.iloc[20:40].min()
                recent_high = recent_prices.iloc[-10:].max()
                if current_price > recent_high * 0.98 and mid_low < recent_high * 0.90:
                    patterns.append("Cup Pattern")

        except Exception as e:
            logger.debug(f"Pattern detection error: {_brief(e)}")

        return patterns

    @staticmethod
    def calculate_volume_profile(hist: pd.DataFrame, current_price: float) -> Dict:
        """Compute Point of Control over the recent history using 25 price bins."""
        if len(hist) < 30:
            return {'point_of_control': current_price, 'poc_distance_pct': 0.0}
        try:
            price_min = float(hist['Low'].min())
            price_range = float(hist['High'].max() - price_min)
            if price_range <= 0:
                return {'point_of_control': current_price, 'poc_distance_pct': 0.0}
            bin_size = price_range / 25
            hist = hist.copy()
            hist['_price_bin'] = (
                ((hist['Close'] - price_min) / bin_size).astype(int).clip(0, 24)
            )
            vol_by_bin = hist.groupby('_price_bin')['Volume'].sum()
            poc_bin = int(vol_by_bin.idxmax())
            poc_price = price_min + (poc_bin * bin_size) + (bin_size / 2)
            poc_distance = (current_price - poc_price) / poc_price * 100
            return {
                'point_of_control': round(poc_price, 2),
                'poc_distance_pct': round(poc_distance, 2),
            }
        except Exception:
            return {'point_of_control': current_price, 'poc_distance_pct': 0.0}

    @staticmethod
    def detect_gap(hist: pd.DataFrame) -> Dict:
        """Detect intraday gap based on last two bars."""
        if len(hist) < 2:
            return {'gap_pct': 0.0, 'gap_type': 'none'}
        try:
            prev_close = float(hist['Close'].iloc[-2])
            today_open = float(hist['Open'].iloc[-1])
            gap_pct = (today_open - prev_close) / prev_close * 100
            if gap_pct > 2.0:
                gap_type = 'gap_up'
            elif gap_pct < -2.0:
                gap_type = 'gap_down'
            else:
                gap_type = 'none'
            return {'gap_pct': round(gap_pct, 2), 'gap_type': gap_type}
        except Exception:
            return {'gap_pct': 0.0, 'gap_type': 'none'}

    @staticmethod
    def calculate_risk_metrics(returns: np.ndarray, prices: np.ndarray) -> Dict:
        """Calculate advanced risk metrics"""
        metrics = {
            'sharpe_ratio': 0.0,
            'sortino_ratio': 0.0,
            'max_drawdown': 0.0,
            'calmar_ratio': 0.0,
            'win_rate': 0.5,
        }

        try:
            # A single bad tick (a zero price) makes pct_change emit inf, and a
            # -100% print drives the equity curve to zero — which turns the
            # drawdown division into 0/0. Drop non-finite observations up front
            # and floor the compounding factor so the curve stays positive.
            returns = np.asarray(returns, dtype=float)
            returns = returns[np.isfinite(returns)]
            if len(returns) < 20:
                return metrics

            # Sharpe Ratio
            risk_free_rate = 0.04 / 252  # Daily risk-free rate
            excess_returns = returns - risk_free_rate
            std = float(np.std(returns))
            if std > 0:
                metrics['sharpe_ratio'] = float(
                    np.mean(excess_returns) / std * np.sqrt(252)
                )

            # Sortino Ratio
            downside_returns = returns[returns < 0]
            downside_std = float(np.std(downside_returns)) if len(downside_returns) else 0.0
            if downside_std > 0:
                metrics['sortino_ratio'] = float(
                    np.mean(excess_returns) / downside_std * np.sqrt(252)
                )

            # Maximum Drawdown
            cumulative = np.cumprod(1 + np.clip(returns, -0.99, None))
            running_max = np.maximum.accumulate(cumulative)
            drawdown = (cumulative - running_max) / running_max
            if drawdown.size and np.all(np.isfinite(drawdown)):
                metrics['max_drawdown'] = float(np.min(drawdown))

            # Calmar Ratio
            if metrics['max_drawdown'] < 0:
                annual_return = float(np.mean(returns)) * 252
                metrics['calmar_ratio'] = annual_return / abs(metrics['max_drawdown'])

            # Win Rate
            total_days = len(returns)
            metrics['win_rate'] = (
                float(np.sum(returns > 0)) / total_days if total_days > 0 else 0.5
            )

        except Exception as e:
            logger.debug(f"Risk metrics error: {_brief(e)}")

        # Belt and braces: a non-finite risk metric silently zeroes a scoring
        # component downstream, so never let one escape this function.
        defaults = {'sharpe_ratio': 0.0, 'sortino_ratio': 0.0, 'max_drawdown': 0.0,
                    'calmar_ratio': 0.0, 'win_rate': 0.5}
        return {k: _num(v, defaults[k]) for k, v in metrics.items()}


# ============================================================================
# STRATEGIC STOCK ANALYZER - MAIN CLASS
# ============================================================================

class StrategicStockAnalyzer:
    """
    APEX Short-Term Stock Picks Analyzer
    Rule-based daily scanner with risk-first position sizing
    """

    def __init__(self, capital: float = Config.DEFAULT_CAPITAL):
        # Keep third-party provider errors (401/429/timeout) off the console
        # and route them into the classifier so the scan summary is honest.
        configure_provider_logging()

        self.capital = capital
        self.config = Config()
        self.analysis_date = datetime.now()
        self.market_data = EnhancedMarketData()
        self.tech_analyzer = AdvancedTechnicalAnalyzer()
        self.results = []
        self.failed_tickers = []
        # Per-run cache of daily returns for the correlation gate (avoids an
        # O(n^2) Yahoo refetch of already-selected picks during final ranking).
        self._returns_cache: Dict[str, np.ndarray] = {}
        # Weighted momentum of every analysed (liquid) name, used to percentile-
        # rank RS ratings against the whole universe rather than just the final
        # picks. Guarded by a lock because the scan runs under a thread pool.
        self._universe_momentum: List[float] = []
        self._universe_momentum_lock = threading.Lock()
        # Factor readings for EVERY name that got as far as fundamentals —
        # including ones the model later rejected. Grading a pick against only
        # the other picks would compress the scale into meaninglessness; the
        # whole analysed cohort is the honest comparison set.
        self._factor_samples: List[Dict] = []
        self._factor_lock = threading.Lock()

        # Per-ticker outcome ledger and universe-hygiene registry.
        self.diagnostics = ScanDiagnostics()
        self.registry = TickerRegistry.load()
        self.hygiene_summary: Dict = {}

        logger.info(
            f"APEX Short-Term Stock Picks Analyzer v{__version__} "
            f"initialized with ${capital:,.2f}"
        )

        # SPY drives BOTH the relative-strength benchmark and the regime model.
        # Fetch it once: the previous code pulled '3mo' and then '6mo' from the
        # same endpoint back to back, doubling the chance of tripping Yahoo's
        # rate limiter before the scan had even started.
        self.regime_degraded = False
        self._load_market_context()
        logger.info(f"Market Regime detected: {self.market_regime.value}")

        self._print_header()

    def _load_market_context(self) -> None:
        """Fetch SPY once, then derive the RS benchmark and the market regime."""
        spy_hist = None
        try:
            _YF_THROTTLE.wait()
            spy_hist = yf.Ticker('SPY').history(period='1y')
            if spy_hist is not None and spy_hist.empty:
                spy_hist = None
        except Exception as exc:
            logger.debug(f"SPY context fetch failed: {_brief(exc, 120)}")
        self._load_spy_benchmark(spy_hist)
        self.market_regime = self._detect_market_regime(spy_hist)

    def _load_spy_benchmark(self, spy_hist: Optional[pd.DataFrame] = None) -> None:
        """Compute SPY's ~3-month return for relative-strength scoring.

        Accepts an already-downloaded frame (see :meth:`_load_market_context`)
        and falls back to fetching one itself. On failure, ``spy_3m_return`` is
        set to None and ``benchmark_degraded`` to True so downstream RS math can
        fall back to neutral instead of treating "no benchmark" as a flat market.
        """
        self.spy_3m_return: Optional[float] = None
        self.benchmark_degraded = False
        self.benchmark_meta: Dict = {}
        try:
            if spy_hist is None:
                spy = yf.Ticker('SPY')
                spy_hist = spy.history(period='3mo')
            if spy_hist is not None and not spy_hist.empty and len(spy_hist) > 60:
                val = float(spy_hist['Close'].pct_change(60).iloc[-1])
                if np.isfinite(val):
                    self.spy_3m_return = val
                    self.benchmark_meta = {
                        'rows': int(len(spy_hist)),
                        'provider': 'yahoo',
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    }
        except Exception as exc:
            logger.debug(f"SPY benchmark unavailable: {_brief(exc, 120)}")

        if self.spy_3m_return is None:
            self.benchmark_degraded = True
            logger.warning(
                "SPY benchmark unavailable; relative-strength scoring will use "
                "a neutral fallback (degraded mode)."
            )

    @staticmethod
    def _bar_is_complete(session_fraction: float) -> bool:
        """Is the most recent daily bar a FINISHED session?

        ``_session_fraction_elapsed`` returns 1.0 outside regular trading hours
        (pre-market, after the close, weekends), so "complete" is simply
        "not mid-session".

        The threshold used to be 0.95, which reintroduced the very problem the
        completed-bar logic exists to prevent: at ~15:41 ET the scoring volume
        ratio flipped from yesterday's finished bar to today's still-partial
        one, so the same ticker on the same tape could qualify at 15:40 and
        drop out at 15:42. Today's bar is authoritative only once it is closed.
        """
        return session_fraction >= 1.0

    @staticmethod
    def _session_fraction_elapsed() -> float:
        """Fraction of the US regular trading session (9:30-16:00 ET) elapsed.

        Returns 1.0 outside session hours (weekends, pre/post-market) so the
        adaptive volume floor falls back to the full-day threshold.
        """
        if _ET is None:
            return 1.0
        try:
            now = datetime.now(_ET)
        except Exception:
            return 1.0
        # Weekend: completed prior session, treat as full day
        if now.weekday() >= 5:
            return 1.0
        open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
        close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
        if now <= open_dt:
            # Pre-market: last bar is yesterday's full session
            return 1.0
        if now >= close_dt:
            return 1.0
        total = (close_dt - open_dt).total_seconds()
        elapsed = (now - open_dt).total_seconds()
        return max(0.05, min(1.0, elapsed / total))

    @staticmethod
    def _days_to_next_earnings(info: Dict) -> Optional[int]:
        """Whole days until the next scheduled earnings date, or None if unknown.

        yfinance exposes the next earnings date only via the SINGULAR
        ``earningsTimestamp(Start|End)`` Unix-second fields — there is NO
        ``earningsTimestamps`` (plural) key, so collect those, keep the soonest
        FUTURE one, and convert to days. Estimated dates are included (a safety
        filter should be conservative around a likely report).
        """
        if not info:
            return None
        now_ts = datetime.now().timestamp()
        candidates = [
            info.get('earningsTimestampStart'),
            info.get('earningsTimestamp'),
            info.get('earningsTimestampEnd'),
        ]
        future = [ts for ts in candidates
                  if isinstance(ts, (int, float)) and ts > now_ts]
        if not future:
            return None
        return int((min(future) - now_ts) / 86400)

    def _detect_market_regime(self, spy_hist: Optional[pd.DataFrame] = None) -> MarketRegime:
        """Classify the broad market regime using SPY trend + VIX.

        Accepts an already-downloaded SPY frame so the constructor does not have
        to hit the same endpoint twice.
        """
        try:
            if spy_hist is None:
                spy = yf.Ticker('SPY')
                spy_hist = spy.history(period='1y')
            vix = yf.Ticker('^VIX')
            vix_hist = vix.history(period='5d')
            if spy_hist is None or spy_hist.empty or len(spy_hist) < 21:
                self.regime_degraded = True
                return MarketRegime.SIDEWAYS
            spy_20d_return = _num(spy_hist['Close'].pct_change(20).iloc[-1], float('nan'))
            if not np.isfinite(spy_20d_return):
                self.regime_degraded = True
                return MarketRegime.SIDEWAYS
            spy_price = _num(spy_hist['Close'].iloc[-1])
            spy_sma200 = spy_hist['Close'].rolling(200, min_periods=100).mean().iloc[-1]
            spy_above_200 = spy_price > spy_sma200 if pd.notna(spy_sma200) else True
            current_vix = (
                _num(vix_hist['Close'].iloc[-1], 18.0)
                if vix_hist is not None and not vix_hist.empty else 18.0
            )
            if current_vix > 30:
                return MarketRegime.VOLATILE
            elif spy_20d_return > 0.04 and spy_above_200:
                return MarketRegime.BULL_STRONG
            elif spy_20d_return > 0 and spy_above_200:
                return MarketRegime.BULL_WEAK
            elif spy_20d_return < -0.04:
                return MarketRegime.BEAR_STRONG
            elif spy_20d_return < 0:
                return MarketRegime.BEAR_WEAK
            else:
                return MarketRegime.SIDEWAYS
        except Exception as exc:
            logger.debug(f"Market regime detection failed: {_brief(exc, 120)}")
            self.regime_degraded = True
            return MarketRegime.SIDEWAYS

    def _print_header(self):
        """Print professional header"""
        print(f"\n{'=' * 100}")
        print(f"{'APEX SHORT-TERM STOCK PICKS ANALYZER v' + __version__:^100}")
        print(f"{'Rule-Based Multi-Factor Scanner with Risk-First Sizing':^100}")
        print(f"{'=' * 100}")
        print(f"  💰 Capital: ${self.capital:,.2f}")
        print(f"  📅 Analysis Date: {self.analysis_date.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  🎯 Strategy: Short-term swing & position trading")
        print(f"{'=' * 100}\n")

    def build_universe(self, scan_type: str = "comprehensive") -> List[str]:
        """Build comprehensive stock universe with ticker hygiene.

        Raw tickers are passed through the :class:`TickerRegistry`, which:
          - remaps renamed symbols to their current ticker (alias_replaced),
          - drops known delisted/acquired symbols (delisted_excluded),
        and only then validated. Counts are retained for a hygiene summary so
        no ticker is silently dropped — each is classified.
        """
        universe = []
        seen = set()
        raw_count = 0
        aliases_replaced = 0
        stale_excluded = 0
        invalid = 0

        def add_tickers(tickers: List[str]):
            nonlocal raw_count, aliases_replaced, stale_excluded, invalid
            for raw_ticker in tickers:
                raw_count += 1
                resolved, classification = self.registry.resolve(raw_ticker)
                if classification == "delisted_excluded":
                    stale_excluded += 1
                    self.diagnostics.record(
                        (raw_ticker or "").strip().upper(), Outcome.INVALID_TICKER
                    )
                    continue
                if resolved is None:
                    invalid += 1
                    continue
                if classification == "alias_replaced":
                    aliases_replaced += 1
                if resolved in seen:
                    continue
                if not self.market_data._validate_ticker(resolved):
                    invalid += 1
                    self.diagnostics.record(resolved, Outcome.INVALID_TICKER)
                    continue
                universe.append(resolved)
                seen.add(resolved)

        print("🔍 Building Dynamic Stock Universe...")
        print(f"{'─' * 100}\n")

        # Order matters: the scan-type caps below truncate the TAIL of this
        # list, so the curated high-momentum names (the entire point of a
        # short-term scanner) are added FIRST. Otherwise quick/standard/
        # comprehensive caps would only ever reach the alphabetical head of the
        # S&P 500 and never scan the momentum leaders unique to this list.
        print("   [1/3] Adding high-momentum stocks...", end=" ", flush=True)
        momentum = self.market_data.get_high_momentum_stocks()
        add_tickers(momentum)
        print(f"✅ {len(momentum)} stocks")

        print("   [2/3] Fetching S&P 500 constituents...", end=" ", flush=True)
        sp500 = self.market_data.get_sp500_tickers()
        add_tickers(sp500)
        sp500_degraded = EnhancedMarketData.sp500_degraded
        print(f"{'⚠️ ' if sp500_degraded else '✅'} {len(sp500)} stocks"
              f"{' (FALLBACK — reduced coverage)' if sp500_degraded else ''}")

        print("   [3/3] Fetching NASDAQ-100 constituents...", end=" ", flush=True)
        nasdaq = self.market_data.get_nasdaq100_tickers()
        add_tickers(nasdaq)
        nasdaq_degraded = EnhancedMarketData.nasdaq100_degraded
        print(f"{'⚠️ ' if nasdaq_degraded else '✅'} {len(nasdaq)} stocks"
              f"{' (FALLBACK — reduced coverage)' if nasdaq_degraded else ''}")

        universe_list = universe
        print(f"\n   📊 Total Universe: {len(universe_list)} unique stocks")
        print(
            "   🧹 Hygiene: "
            f"raw {raw_count} | aliases replaced {aliases_replaced} | "
            f"stale excluded {stale_excluded} | invalid {invalid} | "
            f"final {len(universe_list)}"
        )

        # Apply scan type limits
        scan_limits = {
            "quick": 75,
            "standard": 250,
            "comprehensive": 400,
            "full": len(universe_list)
        }

        limit = scan_limits.get(scan_type, 250)
        universe_list = universe_list[:limit]

        self.hygiene_summary = {
            "raw": raw_count,
            "aliases_replaced": aliases_replaced,
            "stale_excluded": stale_excluded,
            "invalid": invalid,
            "final": len(universe_list),
            "sp500_degraded": sp500_degraded,
            "nasdaq100_degraded": nasdaq_degraded,
        }
        self.universe_degraded = sp500_degraded or nasdaq_degraded

        print(f"   🎯 Scan Configuration: {scan_type.upper()} - "
              f"Analyzing {len(universe_list)} stocks")
        if self.universe_degraded:
            # A silently-shrunken universe is the worst kind of failure: the
            # scan still "succeeds", just over a fraction of the market.
            print("   ⚠️  UNIVERSE DEGRADED — one or more index constituent lists "
                  "could not be fetched.")
            print("      The scan below covers materially fewer names than "
                  f"{scan_type.upper()} implies. See apex_trading.log.")
        print(f"{'─' * 100}\n")

        return universe_list

    def analyze_stock(self, ticker: str) -> Optional[TradingSetup]:
        """Comprehensive stock analysis with strategic setup"""
        try:
            # Fetch data
            stock = yf.Ticker(ticker)
            hist, fetch_status = self.market_data.get_price_history(ticker, stock)

            if hist.empty:
                # Distinguish a blocked provider from a genuinely dead symbol so
                # the scan summary never reports a provider failure as success.
                status = fetch_status or Outcome.NO_PRICE_DATA
                self.diagnostics.record(ticker, status)
                # Transient provider failures (429 / auth crumb / timeout) are
                # worth a second pass after a cooldown; queue them for retry.
                if status in Outcome.RETRYABLE_PROVIDER:
                    self.failed_tickers.append(ticker)
                return None
            if len(hist) < 100:
                self.diagnostics.record(ticker, Outcome.INSUFFICIENT_HISTORY)
                return None

            # Levels computed from a bar several sessions old are wrong today.
            # Nothing previously checked the date of the last bar, so a stale
            # Stooq fallback or a halted name produced "current" entry prices.
            last_bar = pd.Timestamp(hist.index[-1]).date()
            freshness = assess_price_freshness(last_bar, ticker=ticker)
            if freshness.status == "stale":
                logger.warning("%s: %s", ticker, "; ".join(freshness.notes))
                self.diagnostics.record(ticker, Outcome.STALE_PRICE_DATA)
                return None

            # We have valid price data: from here on, any rejection is a genuine
            # model filter, not a data problem.
            self.diagnostics.mark_data_ok(ticker)

            current_price = hist['Close'].iloc[-1]

            # Records WHY a name is dropped so the scan can present an honest
            # rejection funnel — a bare "0 candidates" is otherwise a mystery
            # and looks like a bug when it is really the market state + filters.
            def reject(reason: str) -> None:
                self.diagnostics.record_filter_reason(reason)
                return None

            # === CHEAP PRICE-ONLY FILTERS (no fundamentals needed) ===
            # Run these BEFORE the expensive .info fetch so price/liquidity
            # rejects never pay for a fundamentals scrape.
            if not (Config.MIN_PRICE <= current_price <= Config.MAX_PRICE):
                return reject("price_out_of_range")

            # 30-bar average volume measured on COMPLETED sessions. Including a
            # partially-filled intraday bar drags the average down and rejects
            # perfectly liquid names purely because the scan ran at 10:00.
            session_fraction = self._session_fraction_elapsed()
            bar_complete = self._bar_is_complete(session_fraction)
            vol_hist = hist['Volume']
            if not bar_complete and len(vol_hist) >= 31:
                avg_volume = _num(vol_hist.iloc[-31:-1].mean())
            else:
                avg_volume = _num(vol_hist.tail(30).mean())
            if avg_volume < Config.MIN_AVG_VOLUME:
                return reject("low_avg_volume")

            avg_dollar_volume = current_price * avg_volume
            if avg_dollar_volume < Config.MIN_DOLLAR_VOLUME:
                return reject("low_dollar_volume")

            # === CALCULATE INDICATORS (CPU only, no network) ===
            indicators = self.tech_analyzer.calculate_comprehensive_indicators(hist)

            if not indicators:
                return reject("indicator_calc_failed")

            # Record weighted momentum for universe-wide RS ranking BEFORE the
            # remaining model filters can reject this name, so the RS percentile
            # reflects the whole liquid universe and not just the final picks.
            self._record_universe_momentum(indicators)

            # Adaptive volume filter. Volume_Ratio = today's volume / 20-day avg.
            # During the session, today's bar has only accumulated a fraction
            # of full-day volume, so the floor must scale with session progress.
            # Outside market hours (weekend, after close), the last bar is a
            # completed day and we use the full 0.5x floor.
            vol_ratio_check = _num(indicators.get('Volume_Ratio', 1.0), 1.0)
            min_vol_floor = max(0.15, 0.5 * session_fraction)
            if vol_ratio_check < min_vol_floor:
                return reject("low_volume_ratio")

            # Volume ratio for SCORING — uses the LAST FULLY-COMPLETED bar to
            # avoid look-ahead bias. The old session-projection (dividing the
            # partial intraday bar by elapsed-session fraction) caused the same
            # ticker to score differently at 10am vs 4pm and could promote
            # sleepy 0.3x readings into fake 1.5x "volume confirmation" pattern
            # bonuses. The liquidity gate above still uses the raw (partial)
            # ratio because rejecting truly illiquid tape early is desired;
            # only the score input is locked to completed-bar history.
            try:
                vol_series = hist['Volume']
                if not bar_complete and len(vol_series) >= 22:
                    prior_vol = float(vol_series.iloc[-2])
                    prior_avg = float(vol_series.iloc[-22:-2].mean())
                    prior_ratio = prior_vol / prior_avg if prior_avg > 0 else 1.0
                    indicators['Volume_Ratio_Adj'] = max(0.0, min(5.0, prior_ratio))
                else:
                    indicators['Volume_Ratio_Adj'] = vol_ratio_check
            except Exception:
                indicators['Volume_Ratio_Adj'] = vol_ratio_check

            # Prime the correlation cache from the history already in hand. The
            # selection pass otherwise re-downloads 3 months of bars for every
            # (candidate, holding) pair — an O(n^2) burst of Yahoo requests at
            # the very end of a scan, exactly when the 429 limiter is armed.
            self._cache_returns_from_history(ticker, hist)

            # === RISK METRICS ===
            returns = indicators.get('hist_returns', np.array([]))
            prices = indicators.get('hist_close', np.array([]))
            risk_metrics = self.tech_analyzer.calculate_risk_metrics(returns, prices)

            # === PATTERN DETECTION ===
            patterns = self.tech_analyzer.detect_patterns(hist, indicators)

            # Hard-block known-bearish setups before further scoring
            HARD_BLOCK_PATTERNS = {'Death Cross', 'Death Cross (Recent)'}
            if any(p in HARD_BLOCK_PATTERNS for p in patterns):
                return reject("bearish_pattern_block")

            # Volume profile (Point of Control)
            vp = self.tech_analyzer.calculate_volume_profile(hist, current_price)
            poc_price = vp['point_of_control']
            if abs(vp.get('poc_distance_pct', 0)) > 5:
                logger.debug(
                    f"{ticker}: POC {poc_price:.2f} "
                    f"({vp['poc_distance_pct']:+.1f}% from price)"
                )

            # Gap detection (uses session-adjusted volume so an early-session
            # gap-up with light raw volume still qualifies if pacing is heavy).
            gap_info = self.tech_analyzer.detect_gap(hist)
            if gap_info['gap_type'] == 'gap_up' and scoring_volume_ratio(indicators) > 1.5:
                patterns.append("Gap Up + Volume")
            elif gap_info['gap_type'] == 'gap_down':
                patterns.append("Gap Down")

            # === PRICE-ONLY SCORES + UPPER-BOUND PRE-SCREEN ===
            # technical/momentum/pattern are computable from price alone. If the
            # BEST CASE total (assuming the not-yet-fetched fundamentals were
            # perfect) still cannot reach MIN_SCORE, skip the expensive .info
            # fetch: a provably-safe early reject (it can never drop a name that
            # could have passed) and the main scan-time saver in weak tape.
            technical_score = self._calculate_technical_score(indicators, current_price)
            momentum_score = self._calculate_momentum_score(indicators, returns)
            pattern_score = self._calculate_pattern_score(patterns, indicators)

            price_only_weighted = (
                technical_score * 0.28 + momentum_score * 0.22 + pattern_score * 0.08
            )
            # 0.42 = combined weight of the fundamentals-dependent components
            # (fundamental 0.20 + quality 0.12 + analyst 0.10), assumed at 100.
            max_possible_score = price_only_weighted + 0.42 * 100.0
            if max_possible_score < Config.MIN_SCORE:
                return reject("score_below_min")

            # === FUNDAMENTALS (fetched only for price-screen survivors) ===
            info = self.market_data.get_stock_info(ticker, stock)
            info_missing = not info  # Yahoo fundamentals endpoint was blocked

            # Market cap/data-quality filter. Yahoo can intermittently omit
            # marketCap during crumb/401 issues, so only reject known
            # sub-threshold caps.
            # ``info.get('marketCap', 0)`` returns None when the key is present
            # but null — which Yahoo does regularly during crumb/401 trouble —
            # and ``None > 0`` raises TypeError, killing the whole ticker.
            market_cap = _info_num(info, 'marketCap')
            if 0 < market_cap < Config.MIN_MARKET_CAP:
                return reject("below_min_market_cap")

            # === EARNINGS PROXIMITY FILTER ===
            # Reads the SINGULAR earningsTimestamp(Start|End) fields via the
            # helper — the old info['earningsTimestamps'] (plural) key never
            # existed, so this safety filter previously never fired.
            days_to_earnings = self._days_to_next_earnings(info)
            if (days_to_earnings is not None
                    and days_to_earnings <= Config.EARNINGS_BLOCK_DAYS):
                # Hard block: no new positions this close to a report.
                return reject("earnings_proximity")

            # === REMAINING (FUNDAMENTALS-DEPENDENT) SCORES ===
            # _calculate_fundamental_score neutralises an empty/blocked payload
            # to 50 internally, and the quality/analyst cards renormalise over
            # whatever fields DID arrive, so a provider outage no longer costs a
            # name ~7 composite points for reasons unrelated to the company.
            fundamental_score = self._calculate_fundamental_score(info)
            quality_score = self._calculate_quality_score(info)
            analyst_score = self._calculate_analyst_score(info)

            # Factor sub-scores. These do NOT feed the composite (that would
            # double-count the fundamentals already in fundamental_score) —
            # they exist to be graded against sector peers after the scan, so
            # a pick can be explained as "A- growth, D value" instead of a
            # bare number.
            sector = _info_str(info, 'sector', 'Unknown')
            value_score = self._calculate_value_score(info)
            growth_score = self._calculate_growth_score(info)
            profitability_score = self._calculate_profitability_score(info)

            # === OVERALL SCORE (0-100) ===
            components = {
                'technical': technical_score,
                'fundamental': fundamental_score,
                'momentum': momentum_score,
                'quality': quality_score,
                'analyst': analyst_score,
                'pattern': pattern_score,
            }

            # Earnings-proximity haircut and market-regime multiplier. When
            # regime detection is degraded (benchmark fetch blocked) use a
            # neutral 1.0 so we don't penalize on an unknown regime.
            score_multiplier = 1.0
            if (days_to_earnings is not None
                    and days_to_earnings <= Config.EARNINGS_WARN_DAYS):
                score_multiplier *= 0.90
            if not getattr(self, 'regime_degraded', False):
                score_multiplier *= self.REGIME_MULTIPLIERS.get(self.market_regime, 0.93)

            overall_score = self.composite_score(components) * score_multiplier

            # Recorded BEFORE the score gate so rejected names still widen the
            # peer distribution the survivors are graded against.
            self._record_factor_sample(sector, {
                'Value': value_score,
                'Growth': growth_score,
                'Profitability': profitability_score,
                'Momentum': momentum_score,
                'Technicals': technical_score,
                'Fundamental': fundamental_score,
                'Quality': quality_score,
                'Analyst': analyst_score,
                'Pattern': pattern_score,
                'Multiplier': score_multiplier,
                'Composite': overall_score,
            })

            # The fundamental and quality legs are re-expressed against sector
            # peers after the scan (:meth:`_apply_sector_relative_scoring`), so
            # the definitive MIN_SCORE gate runs there. Here, reject only names
            # that could not reach MIN_SCORE even with the best possible
            # sector-relative standing — provably safe, and it keeps the
            # adjustment symmetric (it can lift a name as well as sink it).
            best_case = self.composite_score(
                dict(components, fundamental=100.0, quality=100.0)) * score_multiplier
            if best_case < Config.MIN_SCORE:
                return reject("score_below_min")

            # === DETERMINE SIGNAL ===
            signal, confidence = self._determine_signal(
                overall_score, indicators, technical_score, momentum_score
            )

            # === CALCULATE ENTRY/EXIT STRATEGY ===
            entry_strategy = self._calculate_entry_strategy(current_price, indicators)
            exit_strategy = self._calculate_exit_strategy(
                entry_strategy['optimal_entry'], indicators, signal, overall_score
            )

            # Validate risk/reward
            risk_per_share = entry_strategy['optimal_entry'] - exit_strategy['stop_loss']
            reward_per_share = exit_strategy['primary_target'] - entry_strategy['optimal_entry']

            if risk_per_share <= 0 or reward_per_share <= 0:
                return reject("invalid_risk_reward")

            # NET of friction R/R (per backtest-expert: "add friction
            # everywhere"). Round-trip cost is debited from the reward leg.
            entry_px = entry_strategy['optimal_entry']
            friction_per_share = entry_px * Config.FRICTION_ROUND_TRIP_PCT
            net_reward = max(0.0, reward_per_share - friction_per_share)
            risk_reward_ratio = net_reward / risk_per_share

            if risk_reward_ratio < Config.MIN_RISK_REWARD:
                return reject("reward_risk_below_min")

            # === TIMEFRAME DETERMINATION ===
            timeframe_info = self._determine_timeframe(
                signal, overall_score, indicators, risk_metrics,
                entry_price=entry_strategy['optimal_entry'],
                target_price=exit_strategy['primary_target'],
            )

            # === POSITION SIZING ===
            position_info = self._calculate_position_sizing(
                signal, overall_score, entry_strategy['optimal_entry'],
                risk_per_share, indicators['Volatility_20D']
            )

            # A name that clears every model gate but cannot be funded at this
            # account size is NOT a rejected setup — it is a watchlist entry.
            # Dropping it here made the whole watchlist mechanism below
            # (moved_to_watchlist / watchlist_reason / _display_watchlist)
            # unreachable from sizing: the scan could report "N could not fund
            # one whole share" as a bare count while never naming a single one.
            # _finalize_share_accounting flags it unfunded further down, and
            # get_top_setups already refuses to fund a zero-share pick.
            position_risk = position_info['risk_dollars']
            position_reward = reward_per_share * position_info['shares']

            # Relative strength vs SPY 3M benchmark. When the benchmark is
            # unavailable (degraded mode), fall back to a neutral 1.0 ratio so
            # we neither reward nor penalize on phantom benchmark data.
            if self.spy_3m_return is None:
                rs_ratio = 1.0
            else:
                spy_3m = self.spy_3m_return if self.spy_3m_return != 0 else 0.001
                stock_3m = indicators.get('Momentum_3M', 0)
                rs_ratio = round((1 + stock_3m) / (1 + spy_3m), 3)

            # === BUILD TRADING SETUP ===
            setup = TradingSetup(
                ticker=ticker,
                # _info_str, not info.get(...)[:n]: Yahoo frequently returns a
                # PRESENT key whose value is None, and slicing None raises.
                company=_info_str(info, 'longName', ticker, limit=50),
                sector=sector,
                industry=_info_str(info, 'industry', 'Unknown', limit=40),

                # Signal & Scores
                signal=signal,
                confidence=confidence,
                overall_score=round(overall_score, 1),
                technical_score=round(technical_score, 1),
                fundamental_score=round(fundamental_score, 1),
                momentum_score=round(momentum_score, 1),
                quality_score=round(quality_score, 1),
                analyst_score=round(analyst_score, 1),
                pattern_score=round(pattern_score, 1),

                # Entry Strategy
                entry_price=current_price,
                entry_zone_low=entry_strategy['entry_zone_low'],
                entry_zone_high=entry_strategy['entry_zone_high'],
                optimal_entry=entry_strategy['optimal_entry'],

                # Exit Strategy
                primary_target=exit_strategy['primary_target'],
                secondary_target=exit_strategy['secondary_target'],
                aggressive_target=exit_strategy['aggressive_target'],
                stop_loss=exit_strategy['stop_loss'],
                trailing_stop_pct=exit_strategy['trailing_stop_pct'],

                # Timeframe
                timeframe=timeframe_info['description'],
                expected_hold_days=timeframe_info['expected_days'],
                min_hold_days=timeframe_info['min_days'],
                max_hold_days=timeframe_info['max_days'],

                # Position Sizing
                allocation_usd=position_info['allocation_usd'],
                allocation_pct=position_info['allocation_pct'],
                shares=position_info['shares'],
                position_score=position_info['quality_score'],

                # Risk Metrics
                risk_amount=round(position_risk, 2),
                # self.capital can legitimately be 0 for a library caller (the
                # CLI floors it at $500). Sizing returns all-zeros in that case
                # and no longer rejects the ticker, so this division has to be
                # guarded or a zero-capital run raises here.
                risk_pct=round((position_risk / self.capital) * 100, 2)
                if self.capital else 0.0,
                reward_amount=round(position_reward, 2),
                risk_reward_ratio=round(risk_reward_ratio, 2),
                expected_return_pct=round((reward_per_share / entry_strategy['optimal_entry']) * 100, 2),
                max_loss_pct=round((risk_per_share / entry_strategy['optimal_entry']) * 100, 2),

                # Technical Indicators
                rsi=round(indicators.get('RSI', 50), 1),
                rsi_trend=self._get_rsi_trend(indicators),
                macd=round(indicators.get('MACD', 0), 3),
                macd_signal=round(indicators.get('MACD_Signal', 0), 3),
                macd_histogram=round(indicators.get('MACD_Histogram', 0), 3),
                stochastic=round(indicators.get('Stoch_K', 50), 1),
                bb_position=round(indicators.get('BB_Position', 0.5), 2),
                atr=round(indicators.get('ATR', 0), 2),
                atr_pct=round(indicators.get('ATR_Pct', 0) * 100, 2),
                # Report the ratio the SCORES were computed from (the last
                # completed bar), not the partial intraday bar — otherwise the
                # table can show "0.8x" beside a "Volume Surge" pattern that
                # fired on the completed-bar value of 2.4x.
                volume_ratio=round(scoring_volume_ratio(indicators), 2),
                volume_ratio_raw=round(_num(indicators.get('Volume_Ratio', 1.0), 1.0), 2),
                obv_trend=self._get_obv_trend(indicators),

                # Price Action
                current_price=current_price,
                price_vs_sma20=round(((current_price / indicators.get('SMA_20', current_price)) - 1) * 100, 2),
                price_vs_sma50=round(((current_price / indicators.get('SMA_50', current_price)) - 1) * 100, 2),
                price_vs_sma200=round(((current_price / indicators.get('SMA_200', current_price)) - 1) * 100, 2),
                high_52w=round(indicators.get('High_52W', current_price), 2),
                low_52w=round(indicators.get('Low_52W', current_price), 2),
                distance_from_high_pct=round(indicators.get('Distance_From_High', 0) * 100, 2),
                distance_from_low_pct=round(indicators.get('Distance_From_Low', 0) * 100, 2),

                # Support/Resistance
                support_levels=[
                    round(indicators.get('S2', current_price * 0.96), 2),
                    round(indicators.get('S1', current_price * 0.98), 2),
                    round(indicators.get('Support_20', current_price * 0.97), 2),
                ],
                resistance_levels=[
                    round(indicators.get('R1', current_price * 1.02), 2),
                    round(indicators.get('R2', current_price * 1.04), 2),
                    round(indicators.get('Resistance_20', current_price * 1.03), 2),
                ],
                pivot_point=round(indicators.get('Pivot', current_price), 2),

                # Fundamentals
                pe_ratio=round(_info_num(info, 'trailingPE'), 2),
                peg_ratio=round(_info_num(info, 'pegRatio', 'trailingPegRatio'), 2),
                forward_pe=round(_info_num(info, 'forwardPE'), 2),
                price_to_sales=round(_info_num(info, 'priceToSalesTrailing12Months'), 2),
                price_to_book=round(_info_num(info, 'priceToBook'), 2),
                roe=round(_info_num(info, 'returnOnEquity') * 100, 2),
                roa=round(_info_num(info, 'returnOnAssets') * 100, 2),
                profit_margin=round(_info_num(info, 'profitMargins') * 100, 2),
                operating_margin=round(_info_num(info, 'operatingMargins') * 100, 2),
                revenue_growth=round(_info_num(info, 'revenueGrowth') * 100, 2),
                earnings_growth=round(_info_num(info, 'earningsGrowth') * 100, 2),
                debt_to_equity=round(_info_num(info, 'debtToEquity'), 2),
                current_ratio=round(_info_num(info, 'currentRatio'), 2),
                free_cash_flow=_info_num(info, 'freeCashflow'),

                # Market Metrics
                market_cap=market_cap,
                market_cap_category=self._categorize_market_cap(market_cap),
                avg_volume=int(avg_volume),
                avg_dollar_volume=int(avg_dollar_volume),
                institutional_ownership=round(_info_num(info, 'heldPercentInstitutions') * 100, 2),
                insider_ownership=round(_info_num(info, 'heldPercentInsiders') * 100, 2),
                short_interest=round(_info_num(info, 'shortPercentOfFloat') * 100, 2),

                # Momentum
                momentum_1w=round(indicators.get('Momentum_5D', 0) * 100, 2),
                momentum_1m=round(indicators.get('Momentum_1M', 0) * 100, 2),
                momentum_3m=round(indicators.get('Momentum_3M', 0) * 100, 2),
                momentum_6m=round(indicators.get('Momentum_6M', 0) * 100, 2),
                relative_strength=rs_ratio,

                # Volatility & Risk
                volatility_20d=round(indicators.get('Volatility_20D', 0) * 100, 2),
                volatility_60d=round(indicators.get('Volatility_60D', 0) * 100, 2),
                beta=round(_info_num(info, 'beta', default=1.0), 2),
                sharpe_ratio=round(risk_metrics.get('sharpe_ratio', 0), 2),
                sortino_ratio=round(risk_metrics.get('sortino_ratio', 0), 2),
                max_drawdown=round(risk_metrics.get('max_drawdown', 0) * 100, 2),

                # Patterns
                detected_patterns=patterns,
                breakout_potential=self._calculate_breakout_potential(indicators, patterns),
                trend_strength=self._determine_trend_strength(indicators),
                adx=round(_num(indicators.get('ADX', 0.0)), 1),

                # Meta
                analyst_rating=_info_str(info, 'recommendationKey'),
                analyst_target=round(_info_num(info, 'targetMeanPrice'), 2),
                num_analysts=int(_info_num(info, 'numberOfAnalystOpinions')),
                days_to_earnings=days_to_earnings,
                last_updated=datetime.now().strftime('%Y-%m-%d %H:%M'),
            )

            # Carry data-quality / hygiene provenance for the PM review table.
            setup.info_missing = info_missing
            setup.target_method = exit_strategy.get('target_method', '')
            setup.at_resistance = list(exit_strategy.get('at_resistance', []))
            setup.price_as_of = last_bar.isoformat()
            setup.price_source = hist.attrs.get('source', 'yahoo')
            setup.data_freshness = freshness.status
            resolved_src = self.registry.original_for(ticker) if self.registry else None
            if resolved_src:
                setup.alias_applied = True
                setup.source_ticker = resolved_src

            # The ticket is placed at optimal_entry, and _calculate_position_sizing
            # sized the risk budget off that same price — record it so the
            # allocation engine derives shares, capital and dollar risk from the
            # execution price rather than from the last close.
            setup.execution_entry = entry_strategy['optimal_entry']
            self._attach_schedule(setup, indicators, signal)
            setup.value_score = round(value_score, 1)
            # Peer samples retain full precision. Rounding these first can put
            # a ticker below its own singleton peer sample (75.2 < 75.25),
            # assigning a false 0th percentile and filtering a valid setup.
            setup.fundamental_abs = fundamental_score
            setup.quality_abs = quality_score
            setup.score_multiplier = score_multiplier
            setup.growth_score = round(growth_score, 1)
            setup.profitability_score = round(profitability_score, 1)

            # Resolve executable shares/capital/risk through the single
            # allocation engine so intended vs executable figures stay
            # consistent before the conviction overlay can downsize the pick.
            self._finalize_share_accounting(
                setup, position_info.get('intended_allocation', position_info['allocation_usd'])
            )

            self.diagnostics.record(ticker, Outcome.ACTIVE)
            return setup

        except Exception as e:
            msg = str(e)
            status = classify_provider_error(msg) or _provider_ctx_status()
            if status is None:
                if self.diagnostics.has_data(ticker):
                    # Valid data was already in hand, so this is OUR failure,
                    # not the provider's. Filing it as "no price data" would
                    # quietly inflate the data-failure count in a summary whose
                    # whole purpose is to separate provider problems from model
                    # outcomes — and would hide the bug. Log it loudly.
                    status = Outcome.ANALYSIS_ERROR
                    logger.warning(
                        "Analysis error for %s (data was valid): %s",
                        ticker, msg[:200], exc_info=True,
                    )
                else:
                    status = Outcome.NO_PRICE_DATA
            self.diagnostics.record(ticker, status)
            logger.debug(f"Failed to analyze {ticker}: {msg[:150]}")
            # Only transient provider trouble is worth a second pass; retrying a
            # deterministic code error just burns the retry budget twice.
            if status in Outcome.RETRYABLE_PROVIDER:
                self.failed_tickers.append(ticker)
            return None

    def _calculate_technical_score(self, indicators: Dict, price: float) -> float:
        """Enhanced technical scoring (0-100)"""
        score = 0.0

        # RSI Analysis (15 points)
        rsi = indicators.get('RSI', 50)
        if 50 <= rsi <= 65:
            score += 15  # ideal: confirmed trend, not extended
        elif 45 <= rsi <= 70:
            score += 11
        elif 35 <= rsi < 45:
            score += 9  # oversold recovery opportunity
        elif rsi > 70:
            score += 5  # extended but strong trend
        else:
            score += 2  # below 35: distressed

        # Moving Average Confluence (20 points)
        sma_20 = indicators.get('SMA_20', price)
        sma_50 = indicators.get('SMA_50', price)
        sma_200 = indicators.get('SMA_200', price)

        if price > sma_20:
            score += 6
        if price > sma_50:
            score += 7
        if price > sma_200:
            score += 7

        # MACD Analysis (15 points)
        macd = indicators.get('MACD', 0)
        macd_signal = indicators.get('MACD_Signal', 0)
        macd_hist = indicators.get('MACD_Histogram', 0)

        if macd > macd_signal:
            score += 7
        if macd_hist > 0:
            score += 4
        if macd > 0:
            score += 4

        # Stochastic (10 points)
        stoch_k = indicators.get('Stoch_K', 50)
        stoch_d = indicators.get('Stoch_D', 50)

        if 30 <= stoch_k <= 70:
            score += 6
        if stoch_k > stoch_d:
            score += 4

        # Volume Confirmation (15 points). Uses the completed-bar ratio so an
        # intraday scan and an end-of-day scan score the same tape identically.
        vol_ratio = scoring_volume_ratio(indicators)
        if vol_ratio > 2.0:
            score += 15
        elif vol_ratio > 1.5:
            score += 12
        elif vol_ratio > 1.2:
            score += 8
        elif vol_ratio > 1.0:
            score += 4

        # Bollinger Position (10 points)
        bb_pos = indicators.get('BB_Position', 0.5)
        if 0.3 <= bb_pos <= 0.7:
            score += 10
        elif 0.2 <= bb_pos <= 0.8:
            score += 7
        elif bb_pos < 0.2:
            score += 5

        # OBV Trend (10 points)
        obv = indicators.get('OBV', 0)
        obv_sma = indicators.get('OBV_SMA', 0)
        if obv > obv_sma:
            score += 10
        elif obv > obv_sma * 0.98:
            score += 6

        # Trend Strength (5 points) — Wilder ADX(14), directionally qualified.
        # ADX measures trend STRENGTH without direction, so an equally strong
        # downtrend would otherwise earn the same points as an uptrend; +DI must
        # lead -DI for the bonus to apply.
        adx = _num(indicators.get('ADX', indicators.get('Trend_Strength', 0.0)), 0.0)
        rising = _num(indicators.get('Plus_DI', 0.0)) >= _num(indicators.get('Minus_DI', 0.0))
        if adx >= Config.ADX_STRONG_TREND and rising:
            score += 5
        elif adx >= Config.ADX_TRENDING and rising:
            score += 3

        return min(100.0, score)

    # Fields whose presence shows Yahoo actually returned fundamentals. An empty
    # or blocked payload is pure data loss and scores neutral, not "bad".
    _FUNDAMENTAL_KEYS = ('trailingPE', 'forwardPE', 'returnOnEquity', 'revenueGrowth',
                         'profitMargins', 'freeCashflow', 'priceToSalesTrailing12Months')

    def _calculate_fundamental_score(self, info: Dict) -> float:
        """Valuation + growth leg of the return score (0-100).

        Each fundamental input now enters the composite exactly once:

        * valuation and growth  -> here (via the value and growth factor cards);
        * profitability and balance-sheet strength -> the QUALITY leg;
        * volatility, drawdown, beta, market cap -> risk inputs for sizing and
          the risk grade, NOT the return score. (The project's own OOS study
          found that ranking on inverted volatility actively hurt the ELITE
          composite; risk belongs in sizing, not in "expected return".)

        Previously this card re-scored ROE, margins, D/E and FCF that the
        quality card also scored, so one strong margin earned points twice.

        Absolute bands here; :meth:`_apply_sector_relative_fundamentals`
        re-expresses the leg against sector peers once the scan has them.
        """
        if not info or not any(info.get(k) is not None for k in self._FUNDAMENTAL_KEYS):
            return 50.0
        return 0.5 * self._calculate_value_score(info) + 0.5 * self._calculate_growth_score(info)

    def _calculate_momentum_score(self, indicators: Dict, returns: np.ndarray) -> float:
        """Enhanced momentum scoring (0-100)"""
        score = 0.0

        # Short-term (25 points)
        mom_1w = indicators.get('Momentum_5D', 0)
        if mom_1w > 0.08:
            score += 20
        elif mom_1w > 0.05:
            score += 16
        elif mom_1w > 0.03:
            score += 12
        elif mom_1w > 0:
            score += 7

        mom_1m = indicators.get('Momentum_1M', 0)
        if mom_1m > 0.15:
            score += 5
        elif mom_1m > 0.10:
            score += 4
        elif mom_1m > 0:
            score += 2

        # Medium-term (30 points)
        mom_3m = indicators.get('Momentum_3M', 0)
        if mom_3m > 0.30:
            score += 20
        elif mom_3m > 0.20:
            score += 16
        elif mom_3m > 0.15:
            score += 12
        elif mom_3m > 0.10:
            score += 8
        elif mom_3m > 0:
            score += 4

        mom_6m = indicators.get('Momentum_6M', 0)
        if mom_6m > 0.40:
            score += 10
        elif mom_6m > 0.25:
            score += 7
        elif mom_6m > 0:
            score += 3

        # Rate of Change (15 points)
        roc = indicators.get('ROC_12', 0)
        if roc > 15:
            score += 15
        elif roc > 10:
            score += 12
        elif roc > 5:
            score += 8
        elif roc > 0:
            score += 4

        # Consistency (30 points)
        if len(returns) > 20:
            positive_days = np.sum(returns > 0)
            total_days = len(returns)
            win_rate = positive_days / total_days

            if win_rate > 0.60:
                score += 20
            elif win_rate > 0.55:
                score += 15
            elif win_rate > 0.50:
                score += 10
            elif win_rate > 0.45:
                score += 5

            # Consistency of returns
            if len(returns) > 60:
                recent_momentum = np.mean(returns[-20:])
                older_momentum = np.mean(returns[-60:-20])

                if recent_momentum > older_momentum and recent_momentum > 0:
                    score += 10
                elif recent_momentum > 0:
                    score += 5

        return min(100.0, score)

    def _calculate_value_score(self, info: Dict) -> float:
        """Valuation attractiveness (0-100, higher = cheaper).

        Absolute bands only: the SECTOR-RELATIVE comparison happens later in
        :meth:`_assign_factor_grades`, which is where a P/E of 30 stops being
        "expensive" for a semiconductor and starts being expensive only if its
        semiconductor peers are cheaper.
        """
        card = _ScoreCard()

        pe = _info_num(info, 'trailingPE')
        if pe > 0:
            card.add(25 if pe < 12 else 20 if pe < 18 else 14 if pe < 25
                     else 8 if pe < 35 else 3 if pe < 50 else 0, 25)

        fwd_pe = _info_num(info, 'forwardPE')
        if fwd_pe > 0:
            card.add(20 if fwd_pe < 12 else 16 if fwd_pe < 18 else 11 if fwd_pe < 25
                     else 6 if fwd_pe < 35 else 0, 20)

        peg = _info_num(info, 'pegRatio', 'trailingPegRatio')
        if peg > 0:
            card.add(25 if peg < 1.0 else 18 if peg < 1.5 else 11 if peg < 2.0
                     else 5 if peg < 3.0 else 0, 25)

        ps = _info_num(info, 'priceToSalesTrailing12Months')
        if ps > 0:
            card.add(15 if ps < 2 else 11 if ps < 4 else 6 if ps < 8
                     else 2 if ps < 15 else 0, 15)

        pb = _info_num(info, 'priceToBook')
        if pb > 0:
            card.add(15 if pb < 2 else 11 if pb < 4 else 5 if pb < 8 else 0, 15)

        # Components total 100: P/E 25 + fwd P/E 20 + PEG 25 + P/S 15 + P/B 15.
        return card.result(total=100.0)

    def _calculate_growth_score(self, info: Dict) -> float:
        """Top- and bottom-line growth (0-100)."""
        card = _ScoreCard()

        g = _info_num(info, 'revenueGrowth', default=float('nan'))
        if np.isfinite(g):
            card.add(40 if g > 0.30 else 32 if g > 0.20 else 24 if g > 0.15
                     else 16 if g > 0.10 else 8 if g > 0.05 else 4 if g > 0 else 0, 40)

        g = _info_num(info, 'earningsGrowth', default=float('nan'))
        if np.isfinite(g):
            card.add(35 if g > 0.30 else 28 if g > 0.20 else 18 if g > 0.10
                     else 8 if g > 0 else 0, 35)

        g = _info_num(info, 'earningsQuarterlyGrowth', default=float('nan'))
        if np.isfinite(g):
            card.add(25 if g > 0.25 else 17 if g > 0.10 else 8 if g > 0 else 0, 25)

        # Components total 100: revenue 40 + earnings 35 + quarterly EPS 25.
        return card.result(total=100.0)

    def _calculate_profitability_score(self, info: Dict) -> float:
        """Margin structure, returns on capital and cash generation (0-100)."""
        card = _ScoreCard()

        m = _info_num(info, 'profitMargins', default=float('nan'))
        if np.isfinite(m):
            card.add(30 if m > 0.25 else 22 if m > 0.15 else 13 if m > 0.08
                     else 5 if m > 0 else 0, 30)

        m = _info_num(info, 'operatingMargins', default=float('nan'))
        if np.isfinite(m):
            card.add(25 if m > 0.25 else 18 if m > 0.15 else 10 if m > 0.08
                     else 4 if m > 0 else 0, 25)

        r = _info_num(info, 'returnOnEquity', default=float('nan'))
        if np.isfinite(r):
            card.add(25 if r > 0.25 else 18 if r > 0.15 else 10 if r > 0.08
                     else 4 if r > 0 else 0, 25)

        fcf = _info_num(info, 'freeCashflow', default=float('nan'))
        market_cap = _info_num(info, 'marketCap')
        if np.isfinite(fcf) and market_cap > 0:
            # Absolute FCF rewarded large companies for their size. Compare
            # cash generation with equity value; without market cap it is
            # unmeasurable rather than a negative quality signal.
            fcf_yield = fcf / market_cap
            card.add(20 if fcf_yield >= 0.05 else 12 if fcf_yield >= 0.02
                     else 6 if fcf_yield > 0 else 0, 20)

        # Components total 100: net 30 + operating 25 + ROE 25 + FCF 20.
        return card.result(total=100.0)

    def _calculate_quality_score(self, info: Dict) -> float:
        """Business quality (0-100): profitability 70% + balance sheet 30%.

        Price-derived risk statistics (Sharpe, Sortino, drawdown) used to make
        up half of this card. They are functions of past returns — i.e. the
        momentum leg again — so they were double-counting momentum while
        claiming to measure the business. They now feed ``risk_metrics`` only.
        """
        if not info or not any(info.get(k) is not None for k in self._FUNDAMENTAL_KEYS):
            return 50.0
        profitability = self._calculate_profitability_score(info)

        balance = _ScoreCard()
        de = _info_num(info, 'debtToEquity', default=float('nan'))
        if np.isfinite(de):
            # yfinance reports D/E in percent (45.0 == 0.45x).
            balance.add(20 if de < 30 else 16 if de < 60 else 10 if de < 100
                        else 4 if de < 200 else 0, 20)
        cr = _info_num(info, 'currentRatio', default=float('nan'))
        if np.isfinite(cr):
            balance.add(10 if cr > 2.0 else 8 if cr > 1.5 else 5 if cr > 1.0 else 1, 10)
        # Components total 30: D/E 20 + current ratio 10.
        return 0.7 * profitability + 0.3 * balance.result(total=30.0)

    def _calculate_analyst_score(self, info: Dict) -> float:
        """Sell-side and short-seller positioning (0-100), renormalised.

        This card was called "sentiment", but it never read a word of news: it
        scored the 52-week range position (which also penalised exactly the
        strength the momentum leg rewards), the volume ratio (already in the
        technical leg), institutional ownership (already in quality) and two
        genuine positioning inputs. Only the genuine inputs remain.
        """
        card = _ScoreCard()
        recommendation = _info_str(info, 'recommendationKey').lower()
        if recommendation and recommendation != 'none':
            card.add({
                'strong_buy': 60.0,
                'buy': 48.0,
                'hold': 24.0,
                'underperform': 9.0,
                'sell': 0.0,
            }.get(recommendation, 24.0), 60)

        short_pct = _info_num(info, 'shortPercentOfFloat', default=float('nan'))
        if np.isfinite(short_pct):
            card.add(40 if short_pct < 0.03 else 32 if short_pct < 0.05
                     else 24 if short_pct < 0.08 else 13 if short_pct < 0.15 else 0, 40)
        return card.result(total=100.0)

    # Composite weights. Hand-set, NOT fitted: the evidence registry records
    # whether this composite has been validated out of sample, and every
    # surface labels its output accordingly. Each input enters exactly once.
    COMPOSITE_WEIGHTS = {
        'technical': 0.28,
        'fundamental': 0.20,
        'momentum': 0.22,
        'quality': 0.12,
        'analyst': 0.10,
        'pattern': 0.08,
    }
    REGIME_MULTIPLIERS = {
        MarketRegime.BULL_STRONG: 1.0,
        MarketRegime.BULL_WEAK:   0.97,
        MarketRegime.SIDEWAYS:    0.93,
        MarketRegime.BEAR_WEAK:   0.88,
        MarketRegime.BEAR_STRONG: 0.80,
        MarketRegime.VOLATILE:    0.85,
    }

    @classmethod
    def composite_score(cls, components: Dict[str, float]) -> float:
        """Weighted composite (0-100) of the six return-score legs."""
        return float(sum(_num(components.get(k), 50.0) * w
                         for k, w in cls.COMPOSITE_WEIGHTS.items()))

    # Chart patterns that carry information NOT already in the technical leg.
    # "Above/Below 50/200 MA", "RSI Oversold/Overbought" and "Volume Surge"
    # restate moving-average, RSI and volume inputs the technical card already
    # scores, so they are displayed but not scored again.
    _BULLISH_PATTERNS = frozenset({
        'Golden Cross', 'Golden Cross (Recent)', 'Resistance Breakout',
        'Cup Pattern', 'Bollinger Squeeze', 'Gap Up + Volume',
    })
    _BEARISH_PATTERNS = frozenset({'Support Breakdown', 'Gap Down'})

    def _calculate_pattern_score(self, patterns: List[str], indicators: Dict) -> float:
        """Chart-pattern leg (0-100); 50 means no pattern either way.

        The old card added a Bollinger-squeeze bonus on top of the squeeze
        pattern, a volume bonus on top of the volume already in the technical
        leg, and a price > SMA20 > SMA50 bonus that the technical card's
        moving-average confluence also scored. Each input now counts once.
        ``indicators`` is kept in the signature for callers; it is unused.
        """
        bullish = sum(1 for p in set(patterns) if p in self._BULLISH_PATTERNS)
        bearish = sum(1 for p in set(patterns) if p in self._BEARISH_PATTERNS)
        return float(min(100.0, max(0.0, 50.0 + 15.0 * bullish - 20.0 * bearish)))

    def _determine_signal(self, overall_score: float, indicators: Dict,
                          technical_score: float, momentum_score: float) -> Tuple[str, float]:
        """Determine trading signal with confidence"""

        rsi = _num(indicators.get('RSI', 50), 50.0)
        macd_hist = _num(indicators.get('MACD_Histogram', 0))
        # Completed-bar ratio: the raw one is a partial intraday bar, so a live
        # scan could essentially never clear the 1.2x STRONG_BUY volume test
        # and the top tier was unreachable before the close.
        vol_ratio = scoring_volume_ratio(indicators)

        # Confidence calculation
        confidence = overall_score

        # Strong Buy conditions
        if (overall_score >= TradingSignal.STRONG_BUY.min_score and
                technical_score >= 70 and
                momentum_score >= 70 and
                rsi < 70 and
                macd_hist > 0 and
                vol_ratio > 1.2):
            return TradingSignal.STRONG_BUY.signal_name, min(95, confidence + 5)

        # Buy conditions
        elif (overall_score >= TradingSignal.BUY.min_score and
              technical_score >= 60 and
              momentum_score >= 55 and
              rsi < 75):
            return TradingSignal.BUY.signal_name, confidence

        # Accumulate (default tier — callers enforce overall_score >= MIN_SCORE
        # upstream, so WATCH/AVOID are intentionally unreachable here).
        else:
            return TradingSignal.ACCUMULATE.signal_name, confidence - 5

    def _calculate_entry_strategy(self, current_price: float, indicators: Dict) -> Dict:
        """Calculate optimal entry zones"""

        atr = indicators.get('ATR', current_price * 0.02)
        support_20 = indicators.get('Support_20', current_price * 0.97)
        bb_lower = indicators.get('BB_Lower', current_price * 0.96)

        entry_zone_low = max(
            current_price - (atr * 0.5),
            support_20 * 1.005,
            bb_lower * 1.01,
        )
        entry_zone_high = current_price + (atr * 0.5)
        optimal_entry = max(current_price - (atr * 0.15), entry_zone_low)

        # A name that has just bounced off support can have support * 1.005
        # sitting ABOVE the last close, which produced a zone whose low was
        # above its high and a limit ticket priced above the market. Clamp so
        # low <= price <= high and the ticket always sits inside the zone.
        entry_zone_low = min(entry_zone_low, current_price)
        entry_zone_high = max(entry_zone_high, current_price)
        optimal_entry = min(max(optimal_entry, entry_zone_low), entry_zone_high)

        return {
            'entry_zone_low': round(entry_zone_low, 2),
            'entry_zone_high': round(entry_zone_high, 2),
            'optimal_entry': round(optimal_entry, 2),
        }

    # Multi-week structure closer than this above entry is treated as the level
    # being tested right now (a breakout zone), not as a profit target.
    AT_RESISTANCE_PCT = 0.015

    def _calculate_exit_strategy(self, entry_price: float, indicators: Dict,
                                 signal: str, score: float) -> Dict:
        """Stop and targets from market structure, never from the R/R gate.

        The previous version solved for its own gate: T1 was "the first level
        at least 2R away, else entry + 2R", so the 2:1 reward/risk check that
        followed passed ~97% of the time by construction, and any resistance
        closer than 2R was silently discarded instead of being recognised as a
        ceiling in the way. T2/T3 were fixed 3R/4R multiples.

        Now:
          * stop  — the wider of a 2-ATR stop and structural support, clamped
            to a volatility-scaled band (unchanged);
          * T1    — the NEAREST multi-week resistance above entry (prior 20-bar
            high, 50-bar high, prior 52-week high). One-session pivot levels
            (R1/R2) are not swing targets and are not used. If price is above
            all of them there is no overhead supply, and T1 is an ATR
            projection, labelled as such;
          * T2/T3 — the next structural levels, else +1.5 ATR steps.

        The R/R gate downstream is then a real test: a setup whose nearest
        overhead resistance is too close is rejected, which is the point.
        """
        atr = indicators.get('ATR', entry_price * 0.02)
        resistance_20 = indicators.get('Resistance_20_Prev', indicators.get('Resistance_20', entry_price * 1.03))
        resistance_50 = indicators.get('Resistance_50', entry_price * 1.08)
        high_52w_prev = indicators.get('High_52W_Prev', indicators.get('High_52W', entry_price * 1.10))
        s1 = indicators.get('S1', entry_price * 0.98)
        volume_ratio = scoring_volume_ratio(indicators)

        # Stop Loss: use the wider of ATR-based and structural support.
        # Stop floor is volatility-scaled, NOT a flat 8%: a high-vol name
        # (NVDA-class, ATR%>4%) needs a wider stop or noise alone will hit it
        # within days. We clamp the floor to a sensible band [6%, 14%] so we
        # neither choke calm names with absurdly wide stops nor get whipsawed
        # out of volatile ones.
        atr_floor = entry_price - max(atr * 2.0, entry_price * 0.025)
        structural_stop = s1 * 0.995
        stop_loss = min(atr_floor, structural_stop)
        atr_pct = (atr / entry_price) if entry_price > 0 else 0.02
        vol_scaled_floor_pct = max(0.06, min(0.14, 3.0 * atr_pct))
        stop_loss = max(stop_loss, entry_price * (1.0 - vol_scaled_floor_pct))

        # Volatility-scaled ceiling on how far a swing target may sit: a 1%-ATR
        # name caps near +15%, a 5%-ATR name near +45%.
        max_primary_pct = max(0.15, min(0.50, 8.0 * atr_pct))
        max_target = entry_price * (1.0 + max_primary_pct)

        levels = sorted({
            round(float(level), 2)
            for level in (resistance_20, resistance_50, high_52w_prev)
            if pd.notna(level) and float(level) > entry_price * (1.0 + self.AT_RESISTANCE_PCT)
        })
        at_resistance = [
            round(float(level), 2)
            for level in (resistance_20, resistance_50, high_52w_prev)
            if pd.notna(level)
            and entry_price < float(level) <= entry_price * (1.0 + self.AT_RESISTANCE_PCT)
        ]
        breakout_confirmed = (
            pd.notna(resistance_20) and entry_price >= float(resistance_20) * 0.995
            and volume_ratio >= 1.2
        )

        if levels:
            primary_target = levels[0]
            target_method = "nearest multi-week resistance"
        else:
            primary_target = entry_price + atr * (3.0 if breakout_confirmed else 2.5)
            target_method = ("ATR projection (above all multi-week highs"
                             + (", confirmed breakout)" if breakout_confirmed else ")"))
        primary_target = min(primary_target, max_target)

        step = max(atr * 1.5, entry_price * 0.01)
        secondary_target = levels[1] if len(levels) > 1 else primary_target + step
        aggressive_target = levels[2] if len(levels) > 2 else secondary_target + step
        secondary_target = min(max(secondary_target, primary_target + 0.01), max_target * 1.15)
        aggressive_target = min(max(aggressive_target, secondary_target + 0.01), max_target * 1.30)

        # Trailing Stop
        if signal == TradingSignal.STRONG_BUY.signal_name:
            trailing_stop_pct = 4.0
        elif signal == TradingSignal.BUY.signal_name:
            trailing_stop_pct = 5.0
        else:
            trailing_stop_pct = 6.0

        return {
            'stop_loss': round(stop_loss, 2),
            'primary_target': round(primary_target, 2),
            'secondary_target': round(secondary_target, 2),
            'aggressive_target': round(aggressive_target, 2),
            'trailing_stop_pct': trailing_stop_pct,
            'target_method': target_method,
            'at_resistance': at_resistance,
        }

    @staticmethod
    def _daily_sigma(entry: float, atr: float, volatility: float) -> float:
        """Daily return standard deviation: realised vol if known, else from ATR.

        ATR/price overstates the daily close-to-close sigma (it includes the
        intraday range); ~1.4x is the usual ratio for liquid US equities.
        """
        if volatility and volatility > 0:
            return float(volatility) / np.sqrt(252.0)
        if entry > 0 and atr > 0:
            return (atr / entry) / 1.4
        return 0.0

    @staticmethod
    def sessions_to_target(entry_price: Optional[float], target_price: Optional[float],
                           atr: float, volatility: float = 0.25,
                           signal: str = "", momentum_1m: float = 0.0) -> Optional[int]:
        """Median sessions for price to first TOUCH *target* under a random walk.

        This is a stated statistical baseline, not a forecast. For a driftless
        random walk in log price with daily sigma s, the first time T at which
        a level a log-distance d away is touched satisfies
            P(T <= n) = 2 * (1 - Phi(d / (s * sqrt(n)))),
        so the median is n = (d / (0.6745 * s))**2.

        The old estimate (distance / ATR x 1.8, with uncalibrated tweaks) grew
        linearly with distance; time to cover a distance grows with its
        SQUARE, so it was systematically optimistic for distant targets.
        ``signal``/``momentum_1m`` are accepted for backwards compatibility and
        deliberately unused: no drift is assumed without evidence of one.

        Returns None when the geometry is unusable.
        """
        entry = _num(entry_price)
        target = _num(target_price)
        sigma = StrategicStockAnalyzer._daily_sigma(entry, _num(atr), _num(volatility))
        if not (entry > 0 and target > entry and sigma > 0):
            return None
        d = np.log(target / entry)
        n = (d / (0.6745 * sigma)) ** 2
        return int(max(1, min(250, round(n))))

    @staticmethod
    def touch_probability(entry_price: float, level: float, sessions: int,
                          atr: float = 0.0, volatility: float = 0.25) -> Optional[float]:
        """P(price touches *level* within *sessions*) under a driftless random walk."""
        from math import erf, sqrt
        entry, level = _num(entry_price), _num(level)
        sigma = StrategicStockAnalyzer._daily_sigma(entry, _num(atr), _num(volatility))
        if not (entry > 0 and level > 0 and sigma > 0 and sessions > 0):
            return None
        z = abs(np.log(level / entry)) / (sigma * sqrt(sessions))
        return float(min(1.0, 2.0 * (1.0 - 0.5 * (1.0 + erf(z / sqrt(2.0))))))

    def _determine_timeframe(self, signal: str, score: float,
                             indicators: Dict, risk_metrics: Dict,
                             entry_price: Optional[float] = None,
                             target_price: Optional[float] = None) -> Dict:
        """Estimate the holding window from how far the target actually is.

        The previous model bucketed the horizon off the COMPOSITE SCORE, which
        is not a statement about time: anything under 75 fell into the
        "2-4 months / 75-day" bucket, so a short-term scanner whose entry gate
        is a score of 60 recommended a quarter-long hold for nearly every pick,
        while the ATR-scaled targets it had just computed were typically 6-12%
        away — days of travel, not months.

        The horizon is now derived from the trade's own geometry:

            bars ~= (target - entry) / ATR * TRAVEL_FACTOR

        ATR is the average distance covered in a session, but only a fraction
        of it is NET progress in one direction, so the raw ratio is optimistic;
        1.8x is the customary correction. High realised volatility widens the
        window again because the path wanders further before it resolves.

        ``entry_price``/``target_price`` are optional so the older score-based
        buckets remain the fallback for callers that do not have a target yet.
        """
        volatility = _num(indicators.get('Volatility_20D', 0.20), 0.20)
        momentum_1m = _num(indicators.get('Momentum_1M', 0))
        atr = _num(indicators.get('ATR', 0.0))

        expected_days = self.sessions_to_target(
            entry_price, target_price, atr, volatility, signal, momentum_1m)

        if expected_days is not None:
            min_days = max(2, expected_days // 3)
            max_days = int(min(120, max(expected_days * 2, expected_days + 10)))
            if expected_days <= 7:
                timeframe = TimeFrame.SWING_TRADE
            elif expected_days <= 21:
                timeframe = TimeFrame.SHORT_TERM
            elif expected_days <= 56:
                timeframe = TimeFrame.MEDIUM_TERM
            else:
                timeframe = TimeFrame.POSITION
        else:
            # Fallback: no target geometry available.
            if (signal == TradingSignal.STRONG_BUY.signal_name and
                    momentum_1m > 0.10 and volatility > 0.25):
                timeframe, expected_days, min_days, max_days = (
                    TimeFrame.SWING_TRADE, 5, 3, 10)
            elif score >= 80 and momentum_1m > 0.05:
                timeframe, expected_days, min_days, max_days = (
                    TimeFrame.SHORT_TERM, 14, 7, 25)
            elif score >= 75:
                timeframe, expected_days, min_days, max_days = (
                    TimeFrame.MEDIUM_TERM, 35, 21, 60)
            else:
                timeframe, expected_days, min_days, max_days = (
                    TimeFrame.POSITION, 75, 45, 120)

            # High-volatility override: widen the window so stops have room.
            if volatility > 0.40:
                expected_days = max(expected_days, 21)
                min_days = max(min_days, 10)
                max_days = max(max_days, 45)
            elif volatility > 0.30:
                expected_days = max(expected_days, 14)
                min_days = max(min_days, 7)

        return {
            'description': timeframe.description,
            'expected_days': int(expected_days),
            'min_days': int(min_days),
            'max_days': int(max_days),
        }

    def _calculate_position_sizing(self, signal: str, score: float, entry_price: float,
                                   risk_amount: float, volatility: float) -> Dict:
        """Size positions from signal quality while enforcing dollar risk caps."""

        if entry_price <= 0 or risk_amount <= 0 or self.capital <= 0:
            return {
                'intended_allocation': 0.0,
                'allocation_usd': 0.0,
                'allocation_pct': 0.0,
                'shares': 0,
                'risk_dollars': 0.0,
                'is_funded': False,
                'quality_score': 0.0,
            }

        # Base allocation by signal
        base_allocations = {
            TradingSignal.STRONG_BUY.signal_name: 0.30,
            TradingSignal.BUY.signal_name: 0.22,
            TradingSignal.ACCUMULATE.signal_name: 0.15,
        }

        base_pct = base_allocations.get(signal, 0.10)

        # Score multiplier: exponential lift above the 60 score threshold,
        # clamped to a reasonable band.
        score_mult = 0.5 * np.exp(np.log(4.0) * (score - 60) / 40)
        score_mult = min(2.0, max(0.4, score_mult))

        # Volatility adjustment
        if volatility < 0.15:
            vol_mult = 1.25
        elif volatility < 0.25:
            vol_mult = 1.0
        elif volatility < 0.35:
            vol_mult = 0.75
        else:
            vol_mult = 0.55

        if signal == TradingSignal.STRONG_BUY.signal_name:
            max_position_risk = Config.MAX_RISK_AGGRESSIVE
        else:
            max_position_risk = Config.MAX_RISK_PER_TRADE

        # Calculate target allocation (soft preference)
        target_allocation = self.capital * base_pct * score_mult * vol_mult

        # Apply bounds. Risk budget governs (risk-first sizing); max_position is
        # the hard ceiling; target_allocation is a soft preference that must not
        # be allowed to zero out an otherwise risk-affordable single share.
        max_position = self.capital * Config.MAX_POSITION_PCT
        risk_budget = self.capital * max_position_risk

        # --- Continuous intended dollar allocation (risk-first, pre-rounding) ---
        # Dollars the engine wants to deploy before any share rounding. This is
        # the single "intended" figure the rest of the pipeline reasons about.
        risk_limited_dollars = (risk_budget / risk_amount) * entry_price
        intended_dollars = min(target_allocation, max_position, risk_limited_dollars)

        # Preserve a tradable floor: if the soft target wiped us out but the risk
        # budget and the position cap can each carry at least one whole share,
        # deploy at least one share's worth of capital.
        risk_limited_shares = int(risk_budget / risk_amount)
        position_cap_shares = int(max_position / entry_price)
        can_afford_one = risk_limited_shares > 0 and position_cap_shares > 0
        if can_afford_one and intended_dollars < entry_price:
            intended_dollars = entry_price

        # --- Mode-aware share rounding (single source of truth) ---
        if Config.ALLOW_FRACTIONAL_SHARES:
            shares = round(intended_dollars / entry_price, Config.FRACTIONAL_SHARE_DECIMALS)
            executable_allocation = shares * entry_price
        else:
            shares = whole_shares(intended_dollars, entry_price)
            executable_allocation = shares * entry_price
            # Enforce the minimum-position floor only in whole-share mode: a name
            # that cannot reach MIN_POSITION with whole shares is not funded.
            if 0 < executable_allocation < Config.MIN_POSITION:
                min_position_shares = int(np.ceil(Config.MIN_POSITION / entry_price))
                if min_position_shares > shares:
                    shares = 0
                    executable_allocation = 0.0

        actual_risk = shares * risk_amount

        # Position quality score
        quality_score = min(100, (
                (score / 100) * 40 +
                (vol_mult / 1.25) * 30 +
                (min(executable_allocation / max_position, 1.0) if max_position > 0 else 0) * 30
        ))

        return {
            'intended_allocation': round(intended_dollars, 2),
            'allocation_usd': round(executable_allocation, 2),
            'allocation_pct': round((executable_allocation / self.capital) * 100, 2),
            'shares': shares,
            'risk_dollars': round(actual_risk, 2),
            'is_funded': shares > 0,
            'quality_score': round(quality_score, 1),
        }

    def _get_rsi_trend(self, indicators: Dict) -> str:
        """Get RSI trend direction"""
        rsi = indicators.get('RSI', 50)
        rsi_sma = indicators.get('RSI_SMA', 50)

        if rsi > rsi_sma + 3:
            return "Rising"
        elif rsi < rsi_sma - 3:
            return "Falling"
        else:
            return "Neutral"

    def _get_obv_trend(self, indicators: Dict) -> str:
        """Get OBV trend"""
        obv = indicators.get('OBV', 0)
        obv_sma = indicators.get('OBV_SMA', 0)

        if obv > obv_sma * 1.02:
            return "Strong Accumulation"
        elif obv > obv_sma:
            return "Accumulation"
        elif obv < obv_sma * 0.98:
            return "Distribution"
        else:
            return "Neutral"

    def _categorize_market_cap(self, market_cap: float) -> str:
        """Categorize market cap. Tolerates a None/NaN cap from the provider."""
        market_cap = _num(market_cap, 0.0)
        if market_cap <= 0:
            return "Unknown"
        if market_cap >= 200_000_000_000:
            return "Mega Cap"
        elif market_cap >= 50_000_000_000:
            return "Large Cap"
        elif market_cap >= 10_000_000_000:
            return "Mid Cap"
        elif market_cap >= 2_000_000_000:
            return "Small Cap"
        else:
            return "Micro Cap"

    def _calculate_breakout_potential(self, indicators: Dict, patterns: List[str]) -> float:
        """Calculate breakout potential (0-100)"""
        score = 0.0

        # Volume (completed bar)
        vol_ratio = scoring_volume_ratio(indicators)
        if vol_ratio > 2.0:
            score += 30
        elif vol_ratio > 1.5:
            score += 20
        elif vol_ratio > 1.2:
            score += 10

        # Bollinger Squeeze
        bb_squeeze = indicators.get('BB_Squeeze', 0.02)
        if bb_squeeze < 0.015:
            score += 25
        elif bb_squeeze < 0.020:
            score += 15

        # Patterns
        breakout_patterns = ['Resistance Breakout', 'Cup Pattern', 'Bollinger Squeeze']
        for pattern in patterns:
            if pattern in breakout_patterns:
                score += 15

        # Price near resistance
        current_price = indicators.get('current_price', 100)
        resistance = indicators.get('Resistance_20', current_price * 1.05)

        distance_to_resistance = abs(current_price - resistance) / current_price
        if distance_to_resistance < 0.02:
            score += 20
        elif distance_to_resistance < 0.04:
            score += 10

        return min(100.0, score)

    def _determine_trend_strength(self, indicators: Dict) -> str:
        """Classify the trend from moving-average stacking, gated by ADX.

        Stacking alone says which way price is leaning; ADX says whether that
        lean is a trend at all. A name can sit above all its averages inside a
        drifting range, so "Strong Uptrend" additionally requires ADX to confirm
        a real trend (>= Config.ADX_STRONG_TREND) — otherwise it is reported as
        a plain uptrend and the extended-target logic downstream stays honest.
        """
        current_price = _num(indicators.get('current_price', 100), 100.0)
        sma_20 = _num(indicators.get('SMA_20', current_price), current_price)
        sma_50 = _num(indicators.get('SMA_50', current_price), current_price)
        sma_200 = _num(indicators.get('SMA_200', current_price), current_price)
        adx = _num(indicators.get('ADX', 0.0))

        above_count = sum([
            current_price > sma_20,
            current_price > sma_50,
            current_price > sma_200,
            sma_20 > sma_50,
            sma_50 > sma_200
        ])

        if above_count >= 4:
            return "Strong Uptrend" if adx >= Config.ADX_STRONG_TREND else "Uptrend"
        elif above_count == 3:
            return "Uptrend"
        elif above_count == 2:
            return "Neutral/Mixed"
        elif above_count == 1:
            return "Downtrend"
        else:
            return "Strong Downtrend" if adx >= Config.ADX_STRONG_TREND else "Downtrend"

    def scan_market(self, scan_type: str = "comprehensive") -> List[TradingSetup]:
        """Execute comprehensive market scan"""

        universe = self.build_universe(scan_type)

        # Fresh momentum sample per scan so RS percentiles never carry over
        # between runs on a reused analyzer instance.
        with self._universe_momentum_lock:
            self._universe_momentum = []
        with self._factor_lock:
            self._factor_samples = []

        print("🚀 INITIATING MARKET SCAN")
        print(f"{'=' * 100}")
        print(f"   📊 Analyzing {len(universe)} stocks with multi-factor scoring")
        print(f"   🎯 Filters: Score ≥ {Config.MIN_SCORE}, R/R ≥ {Config.MIN_RISK_REWARD}:1")
        print(f"   💡 Strategy: Short-term swing & position trading")
        print(f"{'=' * 100}\n")

        opportunities = []
        processed = 0

        print("   Progress: ", end="", flush=True)

        # Whole-scan deadline. The previous code passed a timeout to
        # ``future.result()``, which is a no-op: ``as_completed`` only ever
        # yields futures that have ALREADY finished. The real exposure is
        # ``as_completed`` itself blocking forever on a wedged provider socket,
        # so the budget belongs there — and the executor is then shut down
        # without waiting, instead of the implicit ``with``-block join that
        # would re-block on the very task that hung.
        deadline = max(
            Config.SCAN_TIMEOUT_FLOOR,
            Config.SCAN_TIMEOUT_PER_TICKER * max(1, len(universe)),
        )
        executor = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS)
        future_to_ticker = {
            executor.submit(self.analyze_stock, ticker): ticker
            for ticker in universe
        }
        abandoned: List[str] = []
        try:
            for future in as_completed(future_to_ticker, timeout=deadline):
                processed += 1

                if processed % 15 == 0:
                    pct = (processed / len(universe)) * 100
                    print(f"\n   [{processed}/{len(universe)}] {pct:.1f}% ", end="", flush=True)

                try:
                    setup = future.result()

                    if setup:
                        opportunities.append(setup)
                        print("✓", end="", flush=True)
                    else:
                        print("•", end="", flush=True)

                except Exception as e:
                    print("✗", end="", flush=True)
                    logger.debug(f"Error: {future_to_ticker[future]} - {str(e)[:100]}")
        except FuturesTimeoutError:
            abandoned = [t for f, t in future_to_ticker.items() if not f.done()]
            for ticker in abandoned:
                self.diagnostics.record(ticker, Outcome.PROVIDER_TIMEOUT)
            logger.warning(
                "Scan deadline of %.0fs reached with %d ticker(s) still in "
                "flight; recorded as provider timeouts.", deadline, len(abandoned)
            )
        finally:
            executor.shutdown(wait=not abandoned, cancel_futures=True)

        print("\n")
        if abandoned:
            print(f"   ⏱️  Scan deadline reached — {len(abandoned)} ticker(s) "
                  "abandoned and counted as provider timeouts.\n")
        # Retry failed tickers sequentially (Yahoo throttling recovery) BEFORE
        # printing the summary, so the diagnostics reflect the recovered state.
        if self.failed_tickers:
            # De-duplicate: the same ticker can be queued by both an empty
            # rate-limited fetch and a later exception.
            retry_list = list(dict.fromkeys(self.failed_tickers))
            self.failed_tickers.clear()
            # A large failure count means Yahoo throttled the scan; give its
            # rate-limit window time to reset before the sequential retry so we
            # don't immediately hit 429 again.
            if len(retry_list) >= Config.PROVIDER_RETRY_STORM_THRESHOLD:
                cooldown = Config.YF_RATE_LIMIT_COOLDOWN
                print(f"   \u23f3 Provider throttled \u2014 cooling down {cooldown:.0f}s "
                      f"before retrying {len(retry_list)} tickers...")
                time.sleep(cooldown)
            print(f"   \U0001f504 Retrying {len(retry_list)} failed tickers sequentially...")
            recovered = 0
            for ticker in retry_list:
                # Reuse the shared throttle so the retry pass stays gentle.
                _YF_THROTTLE.wait()
                try:
                    setup = self.analyze_stock(ticker)
                    if setup:
                        opportunities.append(setup)
                        recovered += 1
                        print(f"   \u2713 Recovered: {ticker}")
                except Exception:
                    pass
            # Clear any tickers re-queued by the retry pass; one recovery pass
            # is enough and a second loop risks an unbounded wait.
            self.failed_tickers.clear()
            if recovered:
                print(f"   \u2713 Recovered {recovered}/{len(retry_list)} tickers on retry.")
            print()

        # === HONEST SCAN SUMMARY (diagnostics-derived) ===
        diag = self.diagnostics.summary(len(universe))
        print(f"{'=' * 100}")
        print(f"✅ SCAN COMPLETE")
        print(f"   • Universe size:            {diag['universe_count']}")
        print(f"   • Successfully analyzed:    {diag['successfully_analyzed']} "
              f"(valid data + model run)")
        print(f"   • Opportunities found:      {diag['opportunities_found']}")
        print(f"   • Filtered by model:        {diag['model_filtered']} "
              f"(valid data, did not pass filters)")
        print(f"   • Provider failures:        {diag['provider_failures']}")
        if diag['provider_breakdown']:
            for status, count in diag['provider_breakdown'].items():
                print(f"        - {Outcome.LABELS.get(status, status)}: {count}")
        print(f"   • Invalid / delisted:       {diag['invalid_tickers']}")
        print(f"   • No price data:            {diag['no_data_tickers']}")
        if diag.get('analysis_errors'):
            print(f"   • ⚠️  Analysis errors:       {diag['analysis_errors']} "
                  "(valid data, model raised — see apex_trading.log)")
        print(f"   • True data-success rate:   {diag['true_success_rate']:.1f}% "
              f"(clean data / universe, provider failures excluded)")
        if getattr(self, 'universe_degraded', False):
            print("   • ⚠️  Universe: index constituent fetch fell back to a "
                  "built-in list (reduced coverage)")
        if self.benchmark_degraded:
            print("   • ⚠️  Benchmark: SPY 3M return unavailable (RS scoring degraded)")
        if getattr(self, 'regime_degraded', False):
            print("   • ⚠️  Market regime: degraded (neutral multiplier applied)")

        # Rejection funnel: WHY model-valid names did not become opportunities.
        filter_reasons = diag.get('filter_reasons', {})
        if filter_reasons:
            print("   • Rejection funnel (why valid names were filtered):")
            for reason, count in sorted(
                filter_reasons.items(), key=lambda kv: kv[1], reverse=True
            ):
                label = FILTER_REASON_LABELS.get(reason, reason)
                print(f"        - {label}: {count}")

        # When nothing qualified, explain that this is a RESULT, not an error,
        # and point at the dominant cause (regime score cut vs. capital sizing).
        if diag['opportunities_found'] == 0 and diag['successfully_analyzed'] > 0:
            # NOTE: there is deliberately no "could not fund a share" case
            # here. Sizing no longer rejects those names — they are carried
            # through as watchlist rows — so reaching this block at all means
            # nothing cleared the MODEL, and the funding breakdown printed
            # further down owns the capital story.
            score_blocked = (filter_reasons.get('score_below_min', 0)
                             + filter_reasons.get('score_below_min_regime', 0))
            rr_blocked = filter_reasons.get('reward_risk_below_min', 0)
            print("\n   ℹ️  0 setups is a RESULT, not an error — every analyzed name")
            print("       was filtered for a concrete reason above. Main drivers:")
            if score_blocked > 0:
                print(f"       • {score_blocked} scored below the {Config.MIN_SCORE} "
                      f"threshold (regime: {self.market_regime.value}). A weak/")
                print("         defensive regime applies a score haircut by design.")
            if rr_blocked > 0:
                print(f"       • {rr_blocked} had reward:risk below "
                      f"{Config.MIN_RISK_REWARD:.0f}:1 net of costs.")

        print(f"{'=' * 100}\n")
        self.scan_diagnostics = diag

        # RS ratings first, then rank. Ordering matters: the sort's tie-breaks
        # read relative_strength, which is only a true universe percentile after
        # _assign_rs_ratings has run.
        self._assign_rs_ratings(opportunities)
        opportunities = self._apply_sector_relative_scoring(opportunities)
        self._assign_factor_grades(opportunities)

        # Deterministic ranking. Sorting on the score alone leaves ties to be
        # resolved by scan-completion order — i.e. by thread scheduling — so two
        # runs over the same market could return different top-5 lists. The
        # primary criterion is unchanged; ties now fall to the payoff geometry
        # (net reward:risk), then relative strength, then the symbol.
        opportunities.sort(
            key=lambda s: (
                -_num(s.overall_score),
                -_num(s.risk_reward_ratio),
                -_num(s.relative_strength),
                s.ticker,
            )
        )

        # Low-conviction overlay (runs AFTER RS ratings are assigned so the
        # overlay can read each pick's RS rating). Labels weak/chase setups and
        # halves their suggested size instead of presenting them as clean buys.
        self._apply_conviction_overlay(opportunities)

        # Adaptive conviction advisory. Regime-aware: be picky in strong bull
        # markets, relax in defensive regimes. Always surface picks (the user
        # decides); only annotate when conviction is below the regime bar so
        # the user can size down or skip.
        regime_thresholds = {
            MarketRegime.BULL_STRONG: (72.0, 55.0),
            MarketRegime.BULL_WEAK:   (68.0, 50.0),
            MarketRegime.SIDEWAYS:    (65.0, 45.0),
            MarketRegime.BEAR_WEAK:   (63.0, 40.0),
            MarketRegime.BEAR_STRONG: (62.0, 35.0),
            MarketRegime.VOLATILE:    (65.0, 40.0),
        }
        min_score, min_rs = regime_thresholds.get(self.market_regime, (65.0, 45.0))
        if opportunities:
            best = opportunities[0]  # already sorted descending by overall_score
            if best.overall_score < min_score and best.relative_strength < min_rs:
                logger.warning(
                    f"Low-conviction day: best score {best.overall_score:.1f} / "
                    f"RS {best.relative_strength:.0f} in regime {self.market_regime.value} "
                    f"(prefer score>={min_score:.0f} or RS>={min_rs:.0f}). "
                    "Surfacing picks anyway; consider smaller size or sitting out."
                )
                print(
                    "\nℹ️  LOW-CONVICTION DAY\n"
                    f"   Regime: {self.market_regime.value}\n"
                    f"   Best setup: score {best.overall_score:.1f}/100, "
                    f"RS {best.relative_strength:.0f}/99\n"
                    f"   Preferred bar: score >= {min_score:.0f} or RS >= {min_rs:.0f}\n"
                    "   Picks shown below — consider reducing size or skipping.\n"
                )

        # === CANDIDATE FUNDING BREAKDOWN ===
        # is_funded reflects the post-conviction-overlay state (a downsized
        # single-share pick may now be unfunded), so this is computed last.
        share_mode = "FRACTIONAL" if Config.ALLOW_FRACTIONAL_SHARES else "WHOLE-SHARE"
        funded = [s for s in opportunities if s.is_funded and s.shares > 0]
        watchlist = [s for s in opportunities if not (s.is_funded and s.shares > 0)]
        self.watchlist_setups = watchlist
        self.position_count_eligible = len(funded)
        print(f"{'─' * 100}")
        print("📋 CANDIDATE FUNDING BREAKDOWN  "
              f"(mode: {share_mode}, capital ${self.capital:,.0f})")
        print(f"   • Raw candidates (passed model):   {len(opportunities)}")
        print(f"   • Fundable setups (≥1 share):       {len(funded)}")
        print(f"   • Watchlist / unfunded (<1 share):  {len(watchlist)}")
        print("     (final funded positions are selected after risk/sector/correlation limits)")
        if opportunities and not funded:
            print(f"   ⚠️  Every candidate cleared the model but none can be funded at "
                  f"${self.capital:,.0f}.")
            print(f"      Raise capital, lower Config.MIN_POSITION (${Config.MIN_POSITION:,.0f}), "
                  "or set Config.ALLOW_FRACTIONAL_SHARES = True.")
            print("      They are listed by name in the watchlist table below.")
        print(f"{'─' * 100}\n")

        self.results = opportunities

        # Publish only after peer rescoring and the funding overlay have fixed
        # the final call. Repeated scans on one market date retain the first
        # immutable ledger row.
        decisions = [decision_for_setup(s) for s in opportunities]
        for setup, decision in zip(opportunities, decisions):
            setup.decision = decision.to_dict()
            setup.label = decision.label
        record_decisions(decisions, context={"scan_type": scan_type})

        return opportunities

    def _apply_conviction_overlay(self, setups: List[TradingSetup]) -> None:
        """Flag low-conviction / chase setups and halve their suggested size.

        A pick is downgraded to LOW conviction when it shows any combination of
        weakness that a discretionary PM would treat as "confirmation only":
        a marginal score, an overbought RSI, weak relative strength, elevated
        volatility, or a defensive/volatile market regime. The trading model is
        NOT changed — picks still appear, but sized down and clearly labeled so
        they are never presented as clean, full-size buys.
        """
        defensive_regimes = {
            MarketRegime.SIDEWAYS,
            MarketRegime.BEAR_WEAK,
            MarketRegime.BEAR_STRONG,
            MarketRegime.VOLATILE,
        }
        regime_is_defensive = self.market_regime in defensive_regimes

        for s in setups:
            reasons: List[str] = []

            if s.overall_score < Config.MIN_SCORE + Config.LOW_CONVICTION_SCORE_BUFFER:
                reasons.append(
                    f"marginal score {s.overall_score:.0f} "
                    f"(<{Config.MIN_SCORE + Config.LOW_CONVICTION_SCORE_BUFFER:.0f})"
                )
            if s.rsi >= Config.LOW_CONVICTION_RSI_OVERBOUGHT:
                reasons.append(f"overbought RSI {s.rsi:.0f}")
            if s.relative_strength and s.relative_strength < Config.LOW_CONVICTION_RS_FLOOR:
                reasons.append(f"weak RS rating {s.relative_strength:.0f}")
            if s.volatility_20d >= Config.LOW_CONVICTION_HIGH_VOL_PCT:
                reasons.append(f"high volatility {s.volatility_20d:.0f}%")
            if regime_is_defensive:
                reasons.append(f"defensive regime ({self.market_regime.value})")
            if s.info_missing:
                reasons.append("fundamentals unavailable (provider)")

            # Require at least two independent weakness signals to avoid
            # over-flagging a single borderline metric. A blocked-fundamentals
            # data-quality issue on its own is enough to warrant caution.
            if len(reasons) >= 2 or (s.info_missing and len(reasons) >= 1):
                s.conviction = "LOW"
                s.conviction_reasons = reasons
                self._downsize_setup(s, Config.LOW_CONVICTION_SIZE_FACTOR)

    def _finalize_share_accounting(self, s: TradingSetup, intended_allocation: float) -> None:
        """Resolve a setup's executable shares/capital/risk from one clear mode.

        This is the single source of truth for the allocation engine. Given the
        dollars the engine *intends* to deploy, it derives whole or fractional
        shares per ``Config.ALLOW_FRACTIONAL_SHARES`` and recomputes every
        dependent field so that, for any funded pick, the displayed capital,
        shares and risk are mutually consistent:

            executable_capital = shares * entry_price
            risk_amount        = shares * (entry_price - stop_loss)

        In whole-share mode a pick that cannot afford one whole share is funded
        with $0, flagged ``is_funded = False`` and moved to the watchlist rather
        than shown as a ``$X / 0 shares / $0 risk`` ghost position.
        """
        # Size against the price the plan actually executes at. ``entry_price``
        # is the last close, but the ticket is placed at ``optimal_entry`` and
        # the per-trade risk budget upstream was computed from
        # (optimal_entry - stop). Deriving shares and dollar risk from the close
        # instead inflates the reported risk above the budget that authorised
        # the position. ``execution_entry`` carries that basis explicitly and is
        # 0.0 for setups built outside analyze_stock, which fall back cleanly.
        entry = _num(getattr(s, 'execution_entry', 0.0)) or _num(s.entry_price)
        intended = max(0.0, _num(intended_allocation))
        risk_per_share = abs(entry - s.stop_loss)
        reward_per_share = abs(s.primary_target - entry)

        reason = ""
        if entry <= 0 or intended <= 0:
            shares: float = 0
            funded = False
            reason = "no intended allocation"
        elif Config.ALLOW_FRACTIONAL_SHARES:
            shares = round(intended / entry, Config.FRACTIONAL_SHARE_DECIMALS)
            funded = shares > 0
            if not funded:
                reason = "allocation rounds to zero fractional shares"
        else:
            shares = whole_shares(intended, entry)
            funded = shares >= 1
            if not funded:
                reason = (f"needs ${entry:,.2f} for one whole share, "
                          f"intended ${intended:,.2f}")
            elif shares * entry < Config.MIN_POSITION:
                # The dust-trade floor belongs HERE, not only in the initial
                # sizing call: the conviction overlay re-enters through
                # _downsize_setup, and a halved position that lands below the
                # floor must be treated the same way a fresh one would be.
                reason = (f"below ${Config.MIN_POSITION:,.0f} minimum position "
                          f"(${shares * entry:,.2f})")
                shares = 0
                funded = False

        s.intended_allocation_usd = round(intended, 2)
        s.watchlist_reason = reason

        if not funded:
            s.shares = 0
            s.is_funded = False
            s.moved_to_watchlist = True
            s.executable_allocation_usd = 0.0
            s.allocation_usd = 0.0
            s.allocation_pct = 0.0
            s.risk_amount = 0.0
            s.risk_pct = 0.0
            s.reward_amount = 0.0
            # Logged at DEBUG (file only) so it never interleaves with the live
            # progress bar. Unfunded names are surfaced after the scan in the
            # "Watchlist / Unfunded Candidates" table.
            logger.debug(
                "%s: not fundable, moved to watchlist (%s)", s.ticker, reason
            )
            return

        executable = shares * entry
        s.shares = shares
        s.is_funded = True
        s.moved_to_watchlist = False
        s.watchlist_reason = ""
        s.executable_allocation_usd = round(executable, 2)
        s.allocation_usd = round(executable, 2)
        s.allocation_pct = round(executable / self.capital * 100, 2) if self.capital else 0.0
        s.risk_amount = round(risk_per_share * shares, 2)
        s.risk_pct = round(s.risk_amount / self.capital * 100, 2) if self.capital else 0.0
        s.reward_amount = round(reward_per_share * shares, 2)

    def _downsize_setup(self, s: TradingSetup, factor: float) -> None:
        """Scale a setup's intended size and re-resolve executable accounting.

        Downsizing reduces the *intended* dollar allocation and then re-runs the
        share-accounting engine so whole-share rounding is applied consistently.
        Halving a single-share pick therefore routes it to the watchlist with $0
        capital and $0 risk instead of leaving a 0-share funded position.
        """
        # Fall back to the executable allocation when the intended figure has
        # not been populated yet (e.g. a setup constructed directly in tests).
        base = s.intended_allocation_usd if s.intended_allocation_usd > 0 else s.allocation_usd
        self._finalize_share_accounting(s, base * factor)

    def _validate_final_picks(self, setups: List[TradingSetup]) -> List[Dict]:
        """Hedge-fund-style PM review of the final selected picks.

        Returns a list of row dicts (one per pick) flagging quality issues a
        portfolio manager would catch before allocating capital: overbought
        entries, weak relative strength, thin liquidity, poor reward/risk,
        unreliable provider data, stale/aliased symbols, and weak conviction.
        """
        rows: List[Dict] = []
        sector_counts: Dict[str, int] = defaultdict(int)
        for s in setups:
            sector_counts[s.sector] += 1

        for s in setups:
            issues: List[str] = []
            fixes: List[str] = []

            if s.rsi >= 70:
                issues.append(f"Overbought (RSI {s.rsi:.0f})")
                fixes.append("Wait for pullback / partial size")
            if s.relative_strength and s.relative_strength < 40:
                issues.append(f"Weak RS ({s.relative_strength:.0f})")
                fixes.append("Prefer stronger leaders")
            if s.avg_dollar_volume and s.avg_dollar_volume < 20_000_000:
                issues.append(
                    f"Thin liquidity (${s.avg_dollar_volume/1e6:.1f}M/day)"
                )
                fixes.append("Reduce size / use limit orders")
            if s.risk_reward_ratio and s.risk_reward_ratio < Config.MIN_RISK_REWARD:
                issues.append(f"Poor R/R ({s.risk_reward_ratio:.1f}:1)")
                fixes.append("Tighten stop or raise target")
            if s.info_missing:
                issues.append("Provider fundamentals missing")
                fixes.append("Re-fetch / verify before sizing")
            if s.alias_applied:
                issues.append(f"Ticker remapped ({s.source_ticker}→{s.ticker})")
                fixes.append("Confirm correct entity")
            if sector_counts.get(s.sector, 0) > 2:
                issues.append(f"Sector concentration ({s.sector})")
                fixes.append("Cap sector exposure")
            if s.conviction == "LOW":
                issues.append("Low conviction")
                fixes.append("Confirmation only / half size")

            passed = len(issues) == 0
            if passed:
                # Kept within the PM-table column width so the report never
                # truncates mid-word (e.g. "...and red").
                pm_comment = "Clean setup — full size acceptable."
            elif s.conviction == "LOW" or s.info_missing:
                pm_comment = "Confirm + reduce size before entry."
            else:
                pm_comment = "OK with caveats; size conservatively."

            rows.append({
                "ticker": s.ticker,
                "status": "PASS" if passed else "REVIEW",
                "issue": "; ".join(issues) if issues else "None",
                "fix": "; ".join(fixes) if fixes else "None",
                "pm_comment": pm_comment,
            })
        return rows

    def get_top_setups(self, max_positions: int = 5) -> List[TradingSetup]:
        """Get top trading setups with risk, sector, and correlation diversification"""

        self.watchlist_setups: List[TradingSetup] = []
        if not self.results:
            return []

        selected: List[TradingSetup] = []
        selected_tickers: set = set()
        total_risk_budget = self.capital * Config.MAX_PORTFOLIO_RISK
        max_sector_concentration = Config.MAX_SECTOR_POSITIONS

        def can_add(setup: TradingSetup) -> bool:
            # Unfunded candidates belong on the watchlist, never in the funded
            # portfolio.  Letting a zero-share setup through here makes the
            # summary report phantom allocated capital and position counts.
            if not setup.is_funded or setup.shares <= 0:
                return False
            if setup.ticker in selected_tickers:
                return False
            current_risk = sum(p.risk_amount for p in selected)
            sector_count = sum(1 for p in selected if p.sector == setup.sector)
            return (current_risk + setup.risk_amount <= total_risk_budget and
                    sector_count < max_sector_concentration)

        def take(setup: TradingSetup) -> None:
            selected.append(setup)
            selected_tickers.add(setup.ticker)

        # First pass: only picks with RS >= threshold
        for setup in self.results:
            if len(selected) >= max_positions:
                break
            if (setup.relative_strength >= Config.RS_PREFERRED_THRESHOLD
                    and can_add(setup)
                    and self._is_acceptable_correlation(setup.ticker, selected)):
                take(setup)

        # Second pass: fill remaining slots with the best available regardless of
        # RS (only reached when too few RS >= threshold candidates exist).
        # Membership is tested on the SYMBOL: `setup not in selected` used the
        # dataclass __eq__, which compares all ~90 fields on every probe.
        if len(selected) < max_positions:
            for setup in self.results:
                if len(selected) >= max_positions:
                    break
                if (can_add(setup)
                        and self._is_acceptable_correlation(setup.ticker, selected)):
                    take(setup)

        # Names that the allocation engine could not fund as whole shares (e.g.
        # downsized to <1 share by the conviction overlay) are surfaced as a
        # watchlist rather than counted as funded positions.
        self.watchlist_setups = [
            s for s in self.results if not s.is_funded or s.shares <= 0
        ]

        # Report the book in rank order. Selection runs an RS-preferred pass
        # first, so the accepted list came out ordered by WHICH PASS admitted a
        # name rather than by how good it is — which put a marginal RS-50 pick
        # ahead of the highest-scoring one in every table and ticket.
        selected.sort(
            key=lambda s: (
                -_num(s.overall_score),
                -_num(s.risk_reward_ratio),
                -_num(s.relative_strength),
                s.ticker,
            )
        )

        return selected

    @staticmethod
    def _weighted_momentum_pct(momentum_3m: float, momentum_6m: float,
                               momentum_1m: float) -> float:
        """Blend 1M/3M/6M momentum (in percent) into one RS-ranking score.

        Non-finite inputs collapse to 0.0. A NaN here used to survive all the
        way into ``np.searchsorted``, which places NaN at the END of a sorted
        array — so a freshly-listed name with too little history to have a 6M
        return was handed a perfect RS 99.
        """
        return (_num(momentum_3m) * 0.4
                + _num(momentum_6m) * 0.35
                + _num(momentum_1m) * 0.25)

    def _record_universe_momentum(self, indicators: Dict) -> None:
        """Thread-safely record an analysed name's weighted momentum (percent).

        Captured for EVERY name with valid indicators (the broad liquid
        universe), so RS ratings can be a true universe percentile.
        """
        wm = self._weighted_momentum_pct(
            indicators.get('Momentum_3M', 0.0) * 100.0,
            indicators.get('Momentum_6M', 0.0) * 100.0,
            indicators.get('Momentum_1M', 0.0) * 100.0,
        )
        if not np.isfinite(wm):
            return
        lock = getattr(self, '_universe_momentum_lock', None)
        if lock is None:
            return
        with lock:
            self._universe_momentum.append(wm)

    def _record_factor_sample(self, sector: str, factors: Dict[str, float]) -> None:
        """Thread-safely record one analysed name's factor readings."""
        lock = getattr(self, '_factor_lock', None)
        if lock is None:
            return
        sample = {'sector': sector or 'Unknown'}
        sample.update({k: _num(v, 50.0) for k, v in factors.items()})
        with lock:
            self._factor_samples.append(sample)

    def _peer_set(self, sector: str, samples: List[Dict]) -> Tuple[List[Dict], str]:
        by_sector = [p for p in samples if p.get('sector') == (sector or 'Unknown')]
        if len(by_sector) >= Config.MIN_SECTOR_PEERS:
            return by_sector, "sector"
        return samples, "universe"

    def _apply_sector_relative_scoring(self, setups: List[TradingSetup]) -> List[TradingSetup]:
        """Re-express the fundamental and quality legs against sector peers.

        Valuation, growth, margins and leverage are structurally different
        across sectors — a 5% net margin is poor for software and strong for a
        grocer — so absolute bands reward or punish whole sectors. Each name's
        fundamental and quality legs are replaced by their percentile among
        sector peers analysed in this scan (the whole cohort, rejected names
        included; universe-wide when a sector has too few peers), the composite
        is recomputed, and the one definitive MIN_SCORE gate is applied.

        Names whose fundamentals were unavailable keep a neutral 50 rather than
        being ranked on an imputed value.
        """
        lock = getattr(self, '_factor_lock', None)
        with lock if lock is not None else contextlib.nullcontext():
            samples = list(getattr(self, '_factor_samples', []) or [])
        if not samples:
            return setups

        def relative(sample_or_setup_sector: str, fund: float, qual: float) -> Tuple[float, float]:
            peers, _ = self._peer_set(sample_or_setup_sector, samples)
            return (percentile_of(fund, [p.get('Fundamental', 50.0) for p in peers]),
                    percentile_of(qual, [p.get('Quality', 50.0) for p in peers]))

        # Peer composites on the same (relative) basis, so the composite
        # percentile and sector rank compare like with like.
        for p in samples:
            if 'Fundamental' not in p:
                continue
            f_rel, q_rel = relative(p['sector'], p['Fundamental'], p['Quality'])
            p['Composite'] = self.composite_score({
                'technical': p.get('Technicals', 50.0), 'fundamental': f_rel,
                'momentum': p.get('Momentum', 50.0), 'quality': q_rel,
                'analyst': p.get('Analyst', 50.0), 'pattern': p.get('Pattern', 50.0),
            }) * p.get('Multiplier', 1.0)

        kept: List[TradingSetup] = []
        for st in setups:
            if st.info_missing:
                f_rel, q_rel = 50.0, 50.0
            else:
                f_rel, q_rel = relative(st.sector, st.fundamental_abs, st.quality_abs)
            new_score = self.composite_score({
                'technical': st.technical_score, 'fundamental': f_rel,
                'momentum': st.momentum_score, 'quality': q_rel,
                'analyst': st.analyst_score, 'pattern': st.pattern_score,
            }) * _num(st.score_multiplier, 1.0)
            st.fundamental_score = round(f_rel, 1)
            st.quality_score = round(q_rel, 1)
            if new_score < Config.MIN_SCORE:
                self.diagnostics.record(st.ticker, Outcome.MODEL_FILTERED)
                self.diagnostics.record_filter_reason("score_below_min")
                continue
            self._rescore(st, new_score)
            kept.append(st)
        return kept

    def _rescore(self, s: TradingSetup, new_score: float) -> None:
        """Re-derive everything that depends on the composite after it changed.

        Signal tier, trailing stop and position size all read the score, so a
        score changed after analyze_stock must flow through them again — the
        TradingAnalysis engine's "signal changed after sizing was computed"
        defect is exactly what this prevents here.
        """
        s.overall_score = round(new_score, 1)
        signal, confidence = self._determine_signal(
            new_score,
            {'RSI': s.rsi, 'MACD_Histogram': s.macd_histogram,
             'Volume_Ratio_Adj': s.volume_ratio},
            s.technical_score, s.momentum_score,
        )
        s.signal, s.confidence = signal, confidence
        s.trailing_stop_pct = {
            TradingSignal.STRONG_BUY.signal_name: 4.0,
            TradingSignal.BUY.signal_name: 5.0,
        }.get(signal, 6.0)
        entry = _num(s.execution_entry) or _num(s.optimal_entry) or _num(s.entry_price)
        risk_per_share = entry - _num(s.stop_loss)
        if risk_per_share <= 0:
            return
        position_info = self._calculate_position_sizing(
            signal, new_score, entry, risk_per_share, _num(s.volatility_20d) / 100.0)
        s.position_score = position_info['quality_score']
        self._finalize_share_accounting(
            s, position_info.get('intended_allocation', position_info['allocation_usd']))

    FACTOR_NAMES = ('Value', 'Growth', 'Profitability', 'Momentum', 'Technicals')

    def _assign_factor_grades(self, setups: List[TradingSetup]) -> None:
        """Grade every pick's factors as a PERCENTILE against its sector peers.

        Absolute thresholds punish and reward whole sectors for their structural
        characteristics rather than for anything about the individual company: a
        P/E of 30 is expensive for a utility and unremarkable for a
        semiconductor, and a 5% net margin is poor for software and strong for a
        grocer. Ranking within the sector removes that bias.

        A sector needs Config.MIN_SECTOR_PEERS scored names in this scan before
        it is trustworthy as a comparison set; below that the grade falls back
        to the whole analysed universe, and ``grade_basis`` records which was
        used so the report never implies a precision it does not have.
        """
        if not setups:
            return
        lock = getattr(self, '_factor_lock', None)
        with lock if lock is not None else contextlib.nullcontext():
            samples = list(getattr(self, '_factor_samples', []) or [])
        if not samples:
            return

        by_sector: Dict[str, List[Dict]] = defaultdict(list)
        for sample in samples:
            by_sector[sample['sector']].append(sample)

        for setup in setups:
            peers = by_sector.get(setup.sector, [])
            if len(peers) >= Config.MIN_SECTOR_PEERS:
                basis = "sector"
            else:
                peers, basis = samples, "universe"

            own = {
                'Value': setup.value_score,
                'Growth': setup.growth_score,
                'Profitability': setup.profitability_score,
                'Momentum': setup.momentum_score,
                'Technicals': setup.technical_score,
            }
            percentiles = {
                name: round(percentile_of(own[name], [p[name] for p in peers]), 1)
                for name in self.FACTOR_NAMES
            }
            setup.factor_percentiles = percentiles
            setup.factor_grades = {n: grade_for(v) for n, v in percentiles.items()}
            setup.grade_basis = basis
            setup.sector_peers = len(peers)
            setup.composite_percentile = round(
                percentile_of(setup.overall_score, [p['Composite'] for p in peers]), 1
            )
            setup.sector_rank = 1 + sum(
                1 for p in peers if p['Composite'] > setup.overall_score
            )
            setup.quant_rating = self._quant_rating(
                percentiles, setup.composite_percentile
            )

    @staticmethod
    def _quant_rating(percentiles: Dict[str, float], composite_percentile: float) -> str:
        """Headline rating from the factor grades.

        This is a SECOND OPINION, deliberately computed from peer-relative
        factor standing rather than from the absolute composite that gated the
        pick. Where the two disagree the report says so — that disagreement is
        the useful signal, not something to paper over.
        """
        if not percentiles:
            return "UNRATED"
        mean_factor = sum(percentiles.values()) / len(percentiles)
        blended = 0.6 * mean_factor + 0.4 * _num(composite_percentile, 50.0)
        if blended >= 85:
            return "STRONG BUY"
        if blended >= 68:
            return "BUY"
        if blended >= 45:
            return "HOLD"
        if blended >= 28:
            return "WEAK"
        return "AVOID"

    def _assign_rs_ratings(self, all_setups: List[TradingSetup]) -> None:
        """Percentile-rank each setup's momentum into a 1-99 IBD-style RS rating.

        The rank is taken against the WHOLE liquid universe's momentum
        distribution (every analysed name), not just the qualifying setups.
        Ranking only the few survivors degenerates the scale (e.g. 1/50/99 for
        three picks) and mislabels market-strong names as weak, which then
        unfairly penalises them in the conviction overlay and selection.
        """
        if not all_setups:
            return

        momentum = getattr(self, '_universe_momentum', []) or []
        lock = getattr(self, '_universe_momentum_lock', None)
        if lock is not None:
            with lock:
                universe = sorted(momentum)
        else:
            universe = sorted(momentum)

        universe = [m for m in universe if np.isfinite(m)]
        if len(universe) >= 10:
            universe_arr = np.asarray(universe, dtype=float)
            total = len(universe_arr)
            for setup in all_setups:
                wm = self._weighted_momentum_pct(
                    setup.momentum_3m, setup.momentum_6m, setup.momentum_1m
                )
                rank = int(np.searchsorted(universe_arr, wm, side='right'))
                setup.relative_strength = round((rank / total) * 98 + 1, 1)
            return

        # Fallback for a tiny universe (e.g. seeded tests): rank within the set,
        # using MIDPOINT percentiles. Ranking 0..n-1 over n put the best name of
        # a pair at 50.5 and the other at 1.0 — labelling a perfectly strong
        # leader as the weakest possible stock purely because the sample was
        # small, which then dragged it through the conviction overlay.
        scored = sorted(
            all_setups,
            key=lambda s: self._weighted_momentum_pct(
                s.momentum_3m, s.momentum_6m, s.momentum_1m
            ),
        )
        n = len(scored)
        for rank, setup in enumerate(scored):
            setup.relative_strength = (
                round(((rank + 0.5) / n) * 98 + 1, 1) if n > 1 else 50.0
            )

    def _attach_schedule(self, setup: TradingSetup, indicators: Dict,
                         signal: str) -> None:
        """Convert the trade's geometry into a dated plan.

        Each target sits a different distance away, so each gets its own ETA
        from the same ATR-travel model that sizes the holding window — a
        stretch target is not expected on the same day as the first scale-out.
        The time stop and review date are real sessions, holidays excluded.
        """
        entry = setup.execution_entry or setup.optimal_entry or setup.entry_price
        atr = _num(indicators.get('ATR', 0.0))
        volatility = _num(indicators.get('Volatility_20D', 0.25), 0.25)
        momentum_1m = _num(indicators.get('Momentum_1M', 0.0))
        today = self.analysis_date.date()

        sessions: Dict[str, int] = {}
        for label, target in (("T1", setup.primary_target),
                              ("T2", setup.secondary_target),
                              ("T3", setup.aggressive_target)):
            eta = self.sessions_to_target(
                entry, target, atr, volatility, signal, momentum_1m)
            if eta is not None:
                # A later target can never be reached sooner than an earlier
                # one; the model is monotonic in distance but rounding is not.
                previous = max(sessions.values()) if sessions else 0
                sessions[label] = max(eta, previous + 1 if previous else eta)

        setup.eta_sessions = sessions
        setup.eta_dates = {
            label: add_trading_days(today, n).strftime('%Y-%m-%d')
            for label, n in sessions.items()
        }
        setup.plan_date = add_trading_days(today, 0).strftime('%Y-%m-%d')
        setup.time_stop_date = add_trading_days(
            today, setup.max_hold_days).strftime('%Y-%m-%d')
        # Reassess at the expected T1 date, or half the window if we have no ETA.
        review = sessions.get("T1", max(1, setup.expected_hold_days))
        setup.review_date = add_trading_days(today, review).strftime('%Y-%m-%d')
        # Pure arithmetic, not a forecast: the T1 hit-rate at which this
        # reward:risk breaks even. Anything below it and the trade only works
        # if you are right more often than the geometry requires.
        rr = _num(setup.risk_reward_ratio)
        setup.breakeven_win_rate = round(100.0 / (1.0 + rr), 1) if rr > 0 else 0.0

    def _cache_returns_from_history(self, ticker: str, hist: pd.DataFrame) -> None:
        """Memoise ~3 months of daily returns for the correlation gate.

        Called from ``analyze_stock`` with the 1-year frame that was already
        downloaded, so the gate normally needs no network at all.
        """
        cache = getattr(self, '_returns_cache', None)
        if cache is None:
            return
        try:
            returns = hist['Close'].pct_change().dropna().to_numpy(dtype=float)
        except Exception:
            return
        if returns.size:
            cache[ticker] = returns[-63:]

    def _get_recent_returns(self, ticker: str) -> np.ndarray:
        """Fetch ~3 months of daily returns for correlation checks.

        Memoised per run: the pairwise correlation gate re-requests every
        already-selected pick for each new candidate, so without a cache this is
        an O(n^2) burst of Yahoo calls that can re-trip the 429 rate limiter at
        the very end of a scan. The shared throttle spaces out cache misses.
        """
        cache = getattr(self, '_returns_cache', None)
        if cache is None:
            cache = self._returns_cache = {}
        if ticker in cache:
            return cache[ticker]
        result = np.array([])
        try:
            _YF_THROTTLE.wait()
            stock = yf.Ticker(ticker)
            hist = stock.history(period='3mo')
            if not hist.empty:
                _YF_THROTTLE.relax()
                result = hist['Close'].pct_change().dropna().values
        except Exception as exc:
            if classify_provider_error(str(exc)) == Outcome.PROVIDER_RATE_LIMITED:
                _YF_THROTTLE.penalize()
            logger.debug(f"Correlation returns unavailable for {ticker}: {_brief(exc, 120)}")
        cache[ticker] = result
        return result

    def _is_acceptable_correlation(self, new_ticker: str,
                                    existing: List[TradingSetup],
                                    threshold: Optional[float] = None) -> bool:
        """Reject a candidate that is too correlated with any already-selected name."""
        if not existing:
            return True
        if threshold is None:
            threshold = Config.CORRELATION_THRESHOLD
        new_ret = self._get_recent_returns(new_ticker)
        if len(new_ret) < 20:
            return True
        for es in existing:
            ex_ret = self._get_recent_returns(es.ticker)
            if len(ex_ret) < 20:
                continue
            min_len = min(len(new_ret), len(ex_ret))
            corr = float(np.corrcoef(new_ret[-min_len:], ex_ret[-min_len:])[0, 1])
            if corr > threshold:
                logger.info(
                    f"Correlation skip: {new_ticker} vs {es.ticker} = {corr:.2f}"
                )
                return False
        return True

    # ------------------------------------------------------------------
    # 5-MIN SCALPING PLAYBOOK (supplementary intraday context)
    # ------------------------------------------------------------------
    # NOTE: This is *advisory only*. yfinance intraday bars are ~15min delayed
    # and bar-based (no ticks). Use a real-time feed for execution. Yahoo does
    # not support a 3-minute interval (valid: 1m/2m/5m/15m/30m/60m/90m); using
    # one returns empty data and mislabels valid tickers as delisted, so the
    # playbook uses Config.SCALP_INTERVAL (5m).

    @staticmethod
    def _compute_scalping_playbook(ticker: str) -> Optional[Dict]:
        """Fetch intraday bars and compute a scalping setup.

        Returns dict with EMA9/21, VWAP, RSI(7), ATR(14), opening-range,
        a concrete pattern verdict and entry/stop/target levels — or None
        if data is unavailable / insufficient.
        """
        try:
            # yfinance supports ~60 days of 5m bars; SCALP_PERIOD covers one session
            bars = yf.Ticker(ticker).history(
                period=Config.SCALP_PERIOD,
                interval=Config.SCALP_INTERVAL,
                auto_adjust=False,
            )
            if bars is None or bars.empty or len(bars) < 30:
                return None

            bars = bars.dropna(subset=['Close', 'High', 'Low', 'Volume']).copy()
            if bars.empty:
                return None

            close = bars['Close']
            high = bars['High']
            low = bars['Low']
            volume = bars['Volume'].replace(0, np.nan)

            # EMAs
            bars['EMA9'] = close.ewm(span=9, adjust=False).mean()
            bars['EMA21'] = close.ewm(span=21, adjust=False).mean()

            # Session-anchored VWAP (resets each trading day)
            session_keys = bars.index.tz_convert('America/New_York').date \
                if bars.index.tz is not None else bars.index.date
            session_series = pd.Series(session_keys, index=bars.index)
            typical = (high + low + close) / 3.0
            pv = (typical * volume).fillna(0)
            v = volume.fillna(0)
            bars['VWAP'] = (
                pv.groupby(session_series).cumsum() /
                v.groupby(session_series).cumsum().replace(0, np.nan)
            )

            # RSI(7)
            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1/7, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1/7, adjust=False).mean()
            rs = gain / loss.replace(0, np.nan)
            bars['RSI7'] = 100 - (100 / (1 + rs))

            # ATR(14)
            prev_close = close.shift(1)
            tr = pd.concat([
                (high - low),
                (high - prev_close).abs(),
                (low - prev_close).abs()
            ], axis=1).max(axis=1)
            bars['ATR14'] = tr.ewm(alpha=1/14, adjust=False).mean()

            # Opening range = first N bars of the latest session
            latest_session = session_series.iloc[-1]
            session_bars = bars[session_series == latest_session]
            if session_bars.empty:
                return None
            or_bars = session_bars.head(Config.SCALP_OPENING_RANGE_BARS)
            or_high = float(or_bars['High'].max())
            or_low = float(or_bars['Low'].min())

            # Latest snapshot
            last = bars.iloc[-1]
            price = float(last['Close'])
            ema9 = float(last['EMA9'])
            ema21 = float(last['EMA21'])
            vwap = float(last['VWAP']) if pd.notna(last['VWAP']) else price
            rsi7 = float(last['RSI7']) if pd.notna(last['RSI7']) else 50.0
            atr = float(last['ATR14']) if pd.notna(last['ATR14']) else price * 0.003

            # Volume confirmation
            vol_avg20 = float(volume.tail(20).mean()) if volume.tail(20).notna().any() else 0.0
            last_vol = float(last['Volume']) if pd.notna(last['Volume']) else 0.0
            vol_ratio = last_vol / vol_avg20 if vol_avg20 > 0 else 0.0

            # ---- Pattern verdict ----
            bullish_trend = ema9 > ema21 and price > vwap
            bearish_trend = ema9 < ema21 and price < vwap

            pattern = "NO SETUP"
            direction = "NONE"

            if bullish_trend and price <= ema9 * 1.002 and 40 <= rsi7 <= 65 and vol_ratio >= 1.2:
                pattern = "VWAP/EMA9 Pullback (Long)"
                direction = "LONG"
            elif bullish_trend and price > or_high and vol_ratio >= 1.5:
                pattern = "Opening Range Breakout (Long)"
                direction = "LONG"
            elif bearish_trend and price >= ema9 * 0.998 and 35 <= rsi7 <= 60 and vol_ratio >= 1.2:
                pattern = "VWAP/EMA9 Bounce (Short)"
                direction = "SHORT"
            elif bearish_trend and price < or_low and vol_ratio >= 1.5:
                pattern = "Opening Range Breakdown (Short)"
                direction = "SHORT"
            elif bullish_trend:
                pattern = "Bull trend — wait for pullback to EMA9/VWAP"
                direction = "WAIT"
            elif bearish_trend:
                pattern = "Bear trend — avoid longs"
                direction = "WAIT"
            else:
                pattern = "Choppy / no edge — stand aside"
                direction = "WAIT"

            # ---- Concrete scalp levels (only if directional) ----
            scalp_entry = scalp_stop = scalp_t1 = scalp_t2 = None
            rr = None
            if direction == "LONG":
                scalp_entry = price
                scalp_stop = round(min(price - atr, vwap * 0.998, ema21 * 0.998), 2)
                risk = scalp_entry - scalp_stop
                if risk > 0:
                    scalp_t1 = round(scalp_entry + risk * 1.5, 2)
                    scalp_t2 = round(scalp_entry + risk * 2.5, 2)
                    rr = 1.5
            elif direction == "SHORT":
                scalp_entry = price
                scalp_stop = round(max(price + atr, vwap * 1.002, ema21 * 1.002), 2)
                risk = scalp_stop - scalp_entry
                if risk > 0:
                    scalp_t1 = round(scalp_entry - risk * 1.5, 2)
                    scalp_t2 = round(scalp_entry - risk * 2.5, 2)
                    rr = 1.5

            return {
                'price': round(price, 2),
                'ema9': round(ema9, 2),
                'ema21': round(ema21, 2),
                'vwap': round(vwap, 2),
                'rsi7': round(rsi7, 1),
                'atr': round(atr, 3),
                'vol_ratio': round(vol_ratio, 2),
                'or_high': round(or_high, 2),
                'or_low': round(or_low, 2),
                'pattern': pattern,
                'direction': direction,
                'entry': round(scalp_entry, 2) if scalp_entry else None,
                'stop': scalp_stop,
                'target1': scalp_t1,
                'target2': scalp_t2,
                'rr': rr,
            }
        except Exception as exc:
            logger.debug(f"5m scalp playbook failed for {ticker}: {_brief(exc)}")
            return None

    def _display_scalping_playbook(self, setups: List[TradingSetup]):
        """Render the 5-min scalping playbook for the given setups."""

        if not setups:
            return

        print("   Source: yfinance 5m bars (~15 min delayed). Advisory only — "
              "use a real-time feed for execution.")

        rows = []
        notes = []
        for setup in setups:
            play = self._compute_scalping_playbook(setup.ticker)
            if play is None:
                rows.append([setup.ticker, "—", "—", "—", "—", "—", "—",
                             f"no {Config.SCALP_INTERVAL} data "
                             "(closed market / weekend / illiquid)"])
                continue

            dir_emoji = {'LONG': '🟢', 'SHORT': '🔴', 'WAIT': '🟡', 'NONE': '⚪'}.get(play['direction'], '•')
            entry_str = f"${play['entry']:.2f}" if play['entry'] is not None else "—"
            stop_str = f"${play['stop']:.2f}" if play['stop'] is not None else "—"
            t1_str = f"${play['target1']:.2f}" if play['target1'] is not None else "—"
            t2_str = f"${play['target2']:.2f}" if play['target2'] is not None else "—"

            rows.append([
                setup.ticker,
                f"{dir_emoji} {play['direction']}",
                f"${play['vwap']:.2f}",
                f"{play['rsi7']:.0f}",
                f"{play['vol_ratio']:.1f}x",
                entry_str,
                stop_str,
                f"{t1_str} / {t2_str}",
            ])

            notes.append(f"  • {setup.ticker}: {play['pattern']} "
                         f"(EMA9 {play['ema9']:.2f} / EMA21 {play['ema21']:.2f}, "
                         f"OR {play['or_low']:.2f}-{play['or_high']:.2f}, ATR {play['atr']:.2f})")

        headers = ['Ticker', 'Bias', 'VWAP', 'RSI7', 'Vol', 'Scalp Entry', 'Scalp Stop', 'T1 / T2 (1.5R/2.5R)']
        print()
        print(tabulate(rows, headers=headers, tablefmt='simple'))

        if notes:
            print("\n  Scalp pattern read:")
            for note in notes:
                print(note)

        print("\n  📜 Scalping Rules of Engagement:")
        print("     • Trade ONLY in the direction of the daily swing thesis above (no fighting the trend).")
        print("     • Skip if spread > 0.05% or daily $ volume < $50M (slippage will eat the edge).")
        print("     • Avoid first 5 min after open, lunch 11:30–13:30 ET, and 15 min around FOMC / earnings.")
        print("     • Max 3–5 scalps per day per ticker; hard daily loss cap = 2% of account.")
        print("     • Scale 50% off at T1 (+1.5R), trail rest with EMA9; never widen the initial stop.")
        print("     • This is NOT a replacement for the swing setup — it's a "
              "tactical entry refinement.")

    def _display_watchlist(self) -> bool:
        """Render unfunded (sub-one-share) candidates as a watchlist table.

        Pulls from ``self.watchlist_setups`` (populated by ``scan_market`` /
        ``get_top_setups``). Returns True when at least one watchlist row was
        printed. The signal label includes the conviction tag so a downsized
        low-conviction pick reads e.g. ``ACCUMULATE LOW``.
        """
        watchlist = [
            s for s in getattr(self, 'watchlist_setups', []) or []
            if not (s.is_funded and s.shares > 0)
        ]
        if not watchlist:
            return False

        print("\n👀 WATCHLIST / UNFUNDED CANDIDATES — not fundable at this capital\n")
        rows = []
        for s in watchlist:
            signal_label = f"{s.signal} LOW" if s.conviction == "LOW" else s.signal
            # The allocation engine records exactly why it could not fund the
            # pick (one-share shortfall vs. the minimum-position floor); fall
            # back to the conviction reasons only when it did not.
            reason = (s.watchlist_reason
                      or "; ".join(s.conviction_reasons)
                      or "allocation below one share")
            # The funding decision is made at the EXECUTION price (the limit
            # the ticket would use), so "required for 1 share" must quote the
            # same number the allocation engine compared against.
            execution = s.execution_entry or s.optimal_entry or s.entry_price
            shortfall = max(0.0, execution - s.intended_allocation_usd)
            rows.append([
                s.ticker,
                signal_label,
                f"${execution:,.2f}",
                f"${s.intended_allocation_usd:,.2f}",
                f"${execution:,.2f}",
                f"${shortfall:,.2f}",
                reason,
            ])
        headers = ['Ticker', 'Signal', 'Entry', 'Intended Allocation',
                   'Required For 1 Share', 'Shortfall', 'Reason']
        print(tabulate(rows, headers=headers, tablefmt='simple'))
        return True

    # ------------------------------------------------------------------
    # REPORTING HELPERS
    # ------------------------------------------------------------------

    def _fmt_shares(self, setup: "TradingSetup") -> str:
        """Render a share count in whichever accounting mode is active."""
        if Config.ALLOW_FRACTIONAL_SHARES:
            return f"{setup.shares:.{Config.FRACTIONAL_SHARE_DECIMALS}f}"
        return f"{int(setup.shares)}"

    def _scale_out_plan(self, setup: "TradingSetup") -> Tuple[float, float]:
        """Split a position into (take-at-T1, trail-the-rest) share counts.

        In whole-share mode a 1-share position cannot be scaled out of, so the
        whole line is carried to T1 rather than pretending half a share exists.
        """
        shares = _num(setup.shares)
        if shares <= 0:
            return 0.0, 0.0
        if Config.ALLOW_FRACTIONAL_SHARES:
            first = round(shares * Config.SCALE_OUT_T1_FRACTION,
                          Config.FRACTIONAL_SHARE_DECIMALS)
        else:
            first = float(int(shares * Config.SCALE_OUT_T1_FRACTION))
        if first <= 0:
            return shares, 0.0
        return first, shares - first

    def _strong_grades(self, setup: "TradingSetup", floor: str = "B-",
                       limit: Optional[int] = None) -> List[str]:
        """Factor names graded at or above *floor*, best first.

        ``limit`` trims to the top N: naming all five factors as "strongest"
        says nothing, so prose callers ask for two or three.
        """
        cutoff = grade_rank(floor)
        names = [n for n, g in (setup.factor_grades or {}).items()
                 if grade_rank(g) >= cutoff]
        names.sort(key=lambda n: grade_rank(setup.factor_grades[n]), reverse=True)
        return names[:limit] if limit else names

    def _weak_grades(self, setup: "TradingSetup", ceiling: str = "D+") -> List[str]:
        """Factor names graded at or below *ceiling*."""
        cutoff = grade_rank(ceiling)
        return [n for n, g in (setup.factor_grades or {}).items()
                if grade_rank(g) <= cutoff]

    def _peer_label(self, setup: "TradingSetup") -> str:
        """How to describe the comparison set in prose."""
        if setup.grade_basis == "sector":
            return f"{setup.sector_peers} {setup.sector} peers"
        return f"{setup.sector_peers} analysed names"

    def build_thesis(self, setup: "TradingSetup") -> List[str]:
        """The case FOR the trade, stated from values actually computed.

        Every bullet is conditional on the underlying reading, so a thesis is
        never padded with claims the data does not support — a setup with
        nothing going for it produces a short thesis, which is itself the
        signal.
        """
        entry = setup.execution_entry or setup.optimal_entry or setup.entry_price
        bullets: List[str] = []

        strong = self._strong_grades(setup, "B")
        if strong:
            detail = ", ".join(f"{n} {setup.factor_grades[n]}" for n in strong)
            bullets.append(f"Grades {detail} against {self._peer_label(setup)}.")

        if setup.trend_strength in ("Strong Uptrend", "Uptrend"):
            adx = (f", ADX {setup.adx:.0f} confirming a real trend"
                   if setup.adx >= Config.ADX_TRENDING else "")
            bullets.append(
                f"{setup.trend_strength}: price {setup.price_vs_sma50:+.1f}% vs its "
                f"50-day and {setup.price_vs_sma200:+.1f}% vs its 200-day{adx}.")

        if setup.relative_strength >= 70:
            bullets.append(
                f"Relative strength {setup.relative_strength:.0f}/99 — outpacing "
                "most of the scanned universe over 1-6 months.")

        if setup.volume_ratio >= 1.5:
            bullets.append(
                f"{setup.volume_ratio:.1f}x average volume on the last completed "
                "session — the move is being funded, not drifting.")

        bullish = [p for p in setup.detected_patterns
                   if p not in ('RSI Overbought', 'Support Breakdown', 'Gap Down',
                                'Below 50/200 MA', 'Death Cross (Recent)')]
        if bullish:
            bullets.append(f"Setup: {', '.join(bullish[:3])}.")

        if entry > 0:
            bullets.append(
                f"Risking {setup.max_loss_pct:.1f}% to make "
                f"{setup.expected_return_pct:.1f}% — {setup.risk_reward_ratio:.2f}:1 "
                "net of round-trip costs.")

        return bullets[:5]

    def build_risks(self, setup: "TradingSetup") -> List[str]:
        """The case AGAINST — what actually breaks this trade."""
        risks: List[str] = []

        weak = self._weak_grades(setup, "D+")
        if weak:
            detail = ", ".join(f"{n} {setup.factor_grades[n]}" for n in weak)
            risks.append(f"Poor {detail} vs {self._peer_label(setup)}.")

        if setup.rsi >= 70:
            risks.append(
                f"RSI {setup.rsi:.0f} is overbought — you are buying into "
                "strength, so a pullback to the entry zone is the better fill.")

        if setup.adx and setup.adx < Config.ADX_TRENDING:
            # Targets are ATR-scaled, so a wide one implies directional travel
            # that a non-trending tape has no history of delivering.
            risks.append(
                f"ADX {setup.adx:.0f} is below {Config.ADX_TRENDING:.0f} — no "
                f"measurable trend, so the {setup.expected_return_pct:.0f}% target "
                "assumes travel this tape is not currently producing.")

        if setup.volatility_20d >= Config.LOW_CONVICTION_HIGH_VOL_PCT:
            risks.append(
                f"{setup.volatility_20d:.0f}% annualised volatility forces a wide "
                f"{setup.max_loss_pct:.1f}% stop; size is already cut for it.")

        if (setup.days_to_earnings is not None
                and setup.days_to_earnings <= Config.EARNINGS_WARN_DAYS):
            risks.append(
                f"Earnings in {setup.days_to_earnings} day(s) — a gap can clear "
                "the stop without ever trading at it.")

        if setup.distance_from_high_pct > -3:
            risks.append(
                f"Only {abs(setup.distance_from_high_pct):.1f}% below the 52-week "
                "high: little overhead room before it has to make new ones.")

        if setup.avg_dollar_volume and setup.avg_dollar_volume < 20_000_000:
            risks.append(
                f"Thin tape (${setup.avg_dollar_volume / 1e6:.0f}M/day) — use "
                "limits, expect slippage on the exit.")

        if setup.info_missing:
            risks.append(
                "Fundamentals unavailable from the provider; the factor grades "
                "here are imputed at neutral, not measured.")

        if setup.quant_rating in ("WEAK", "AVOID", "HOLD"):
            risks.append(
                f"Factor grades read {setup.quant_rating} even though the setup "
                "cleared the technical gate — the two disagree.")

        if self.market_regime in (MarketRegime.BEAR_STRONG, MarketRegime.BEAR_WEAK,
                                  MarketRegime.VOLATILE):
            risks.append(
                f"{self.market_regime.value} regime: long setups are swimming "
                "against the tape.")

        return risks[:4]

    def _setup_thesis(self, setup: "TradingSetup") -> str:
        """One line explaining WHY this setup scored, in plain trader terms."""
        bits = [f"RSI {setup.rsi:.0f} {setup.rsi_trend.lower()}"]
        bits.append("MACD+" if setup.macd_histogram > 0 else "MACD-")
        if setup.adx:
            bits.append(f"ADX {setup.adx:.0f}")
        bits.append(setup.trend_strength or "trend n/a")
        if setup.relative_strength:
            bits.append(f"RS {setup.relative_strength:.0f}/99")
        bits.append(f"vol {setup.volume_ratio:.1f}x")
        if setup.detected_patterns:
            bits.append(", ".join(setup.detected_patterns[:3]))
        return " · ".join(bits)

    # -- consistent section chrome so the report reads as one document ------
    REPORT_WIDTH = 122

    def _section(self, number: int, title: str, subtitle: str = "") -> None:
        """Uniform numbered section header."""
        bar = "─" * self.REPORT_WIDTH
        print(f"\n{bar}")
        print(f" {number}.  {title.upper()}")
        if subtitle:
            print(f"     {subtitle}")
        print(bar)

    def _display_at_a_glance(self, setups: List["TradingSetup"]) -> None:
        """The whole report in five lines, for a reader who stops here."""
        capital = self.capital or 1.0
        deployed = sum(s.executable_allocation_usd for s in setups)
        heat = sum(s.risk_amount for s in setups)
        budget = capital * Config.MAX_PORTFOLIO_RISK
        full = [s for s in setups if s.conviction != "LOW"]
        lead = (full or setups)[0]
        entry = lead.execution_entry or lead.optimal_entry or lead.entry_price

        self._section(1, "At a glance")
        print(f"   • {len(setups)} position{'s' if len(setups) != 1 else ''} "
              f"to open — {', '.join(s.ticker for s in setups)}")
        print(f"   • Deploy ${deployed:,.2f} of ${self.capital:,.2f} "
              f"({deployed / capital * 100:.1f}%); keep "
              f"${self.capital - deployed:,.2f} in cash")
        print(f"   • Total risk if every stop is hit: ${heat:,.2f} "
              f"({heat / capital * 100:.2f}% of capital) against a "
              f"{Config.MAX_PORTFOLIO_RISK * 100:.0f}% budget (${budget:,.2f})")
        print(f"   • Lead idea: {lead.ticker} — buy ${entry:,.2f}, "
              f"stop ${lead.stop_loss:,.2f}, first target ${lead.primary_target:,.2f} "
              f"({lead.risk_reward_ratio:.1f}:1)")
        horizon = (f"{lead.eta_sessions.get('T1')} sessions "
                   f"(~{lead.eta_dates.get('T1', 'n/a')})"
                   if lead.eta_sessions.get('T1') else
                   f"{lead.expected_hold_days} sessions")
        print(f"   • Horizon: {horizon}; close everything by "
              f"{lead.time_stop_date or 'the time stop'} at the latest")
        low = [s.ticker for s in setups if s.conviction == "LOW"]
        if low:
            print(f"   • ⚠ Half size only on {', '.join(low)} — "
                  "these need confirmation before a full position")

    def _display_timing(self, setups: List["TradingSetup"]) -> None:
        """When each level is expected — the schedule behind the prices."""
        if not setups:
            return
        rows = []
        for s in setups:
            eta, dates = s.eta_sessions or {}, s.eta_dates or {}

            def cell(label: str) -> str:
                if label not in eta:
                    return "—"
                when = datetime.strptime(dates[label], "%Y-%m-%d")
                return f"{eta[label]}d · {when:%a %d %b}"

            rows.append([
                s.ticker,
                s.timeframe,
                f"{s.expected_hold_days}d",
                cell("T1"), cell("T2"), cell("T3"),
                s.review_date or "—",
                s.time_stop_date or "—",
            ])
        headers = ['Ticker', 'Style', 'Expected hold', 'T1 due', 'T2 due',
                   'T3 due', 'Review on', 'Close by']
        print()
        print(tabulate(rows, headers=headers, tablefmt='simple'))
        print("\n   ETAs come from the distance to each target measured in ATR "
              "(average daily range), so a")
        print("   further target is dated later. They are estimates of PACE, not "
              "predictions that the level")
        print("   will be reached. 'Close by' is a hard time stop: exit even if "
              "neither target nor stop has hit.")

    def _execution_steps(self, setup: "TradingSetup") -> List[str]:
        """The literal sequence of orders for one position."""
        entry = setup.execution_entry or setup.optimal_entry or setup.entry_price
        first, runner = self._scale_out_plan(setup)
        shares = self._fmt_shares(setup)
        steps = [
            f"Place a BUY LIMIT for {shares} sh at ${entry:,.2f} "
            f"(good anywhere in ${setup.entry_zone_low:,.2f}-"
            f"${setup.entry_zone_high:,.2f}). Do not chase above the zone.",
            f"The moment it fills, enter a STOP at ${setup.stop_loss:,.2f}. "
            f"Never hold this overnight without one.",
        ]
        if first > 0 and runner > 0:
            steps.append(
                f"Set a SELL LIMIT for {first:g} sh at "
                f"${setup.primary_target:,.2f} (Target 1).")
            steps.append(
                f"When Target 1 fills, raise the stop on the remaining "
                f"{runner:g} sh to your entry (${entry:,.2f}) — the trade is "
                f"now risk-free — and trail by {setup.trailing_stop_pct:.0f}%.")
            steps.append(
                f"Let the runner work toward ${setup.secondary_target:,.2f} and "
                f"${setup.aggressive_target:,.2f}. Never widen the stop.")
        elif first > 0:
            steps.append(
                f"Single-share position: exit the whole line at "
                f"${setup.primary_target:,.2f} (Target 1).")
        if setup.time_stop_date:
            steps.append(
                f"If neither level has triggered by {setup.time_stop_date}, "
                "close it anyway — the setup has expired.")
        return steps

    def _display_legend(self) -> None:
        """One place explaining every symbol used above."""
        print("   R            one unit of this trade's own risk (entry − stop). "
              "A 2R target pays twice what you risk.")
        print("   R/R          reward-to-risk to Target 1, NET of the "
              f"{Config.FRICTION_ROUND_TRIP_PCT * 100:.2f}% round-trip cost model.")
        print("   Break-even   the Target-1 hit rate this R/R needs just to stop "
              "losing money. Pure arithmetic.")
        print("   Grades       percentile rank against sector peers in THIS scan, "
              "A+ (top 5%) to F (bottom 8%).")
        print("   ⚠ LOW        conviction downgrade: position size already halved. "
              "Treat as confirmation-only.")
        print("   Sessions     trading days, holidays excluded — not calendar days.")

    def _display_market_context(self) -> None:
        """Banner describing the tape the picks were generated in."""
        diag = getattr(self, 'scan_diagnostics', {}) or {}
        regime = self.market_regime.value + (
            " (degraded)" if getattr(self, 'regime_degraded', False) else "")
        spy = ("n/a (degraded)" if self.spy_3m_return is None
               else f"{self.spy_3m_return * 100:+.1f}%")
        print(f"   Regime: {regime}   |   SPY 3M: {spy}   |   "
              f"Share mode: {'FRACTIONAL' if Config.ALLOW_FRACTIONAL_SHARES else 'WHOLE-SHARE'}")
        if diag:
            print(f"   Breadth: {diag.get('opportunities_found', 0)} qualified of "
                  f"{diag.get('successfully_analyzed', 0)} analysed "
                  f"({diag.get('universe_count', 0)} in universe)")
            if diag.get('provider_failures'):
                print(f"   ⚠️  {diag['provider_failures']} ticker(s) lost to provider "
                      "errors — coverage is incomplete for this run.")

    @staticmethod
    def _r_multiple(price: float, entry: float, risk_per_share: float) -> float:
        """How many units of risk a level sits away from the entry."""
        if risk_per_share <= 0:
            return 0.0
        return (price - entry) / risk_per_share

    def _display_trade_levels(self, setups: List["TradingSetup"]) -> None:
        """The prices to work: entry, stop and all three targets in one place.

        Every level carries its distance in percent AND in R (units of the
        trade's own risk), because "+14%" means something different on a name
        stopped 4% away than on one stopped 12% away.
        """
        if not setups:
            return
        rows = []
        for s in setups:
            entry = s.execution_entry or s.optimal_entry or s.entry_price
            risk_ps = max(1e-9, entry - s.stop_loss)

            def level(price: float) -> str:
                pct = (price / entry - 1) * 100 if entry else 0.0
                return f"${price:,.2f} ({pct:+.1f}% {self._r_multiple(price, entry, risk_ps):.1f}R)"

            rows.append([
                f"{s.ticker}{' ⚠' if s.conviction == 'LOW' else ''}",
                f"${entry:,.2f}",
                f"${s.entry_zone_low:,.2f}–${s.entry_zone_high:,.2f}",
                f"${s.stop_loss:,.2f} ({(s.stop_loss / entry - 1) * 100:+.1f}%)",
                level(s.primary_target),
                level(s.secondary_target),
                level(s.aggressive_target),
                f"{s.trailing_stop_pct:.0f}%",
            ])

        headers = ['Ticker', 'Buy Limit', 'Entry Zone', 'Stop',
                   'Target 1 (take 50%)', 'Target 2 (trail)', 'Target 3 (stretch)', 'Trail']
        print()
        print(tabulate(rows, headers=headers, tablefmt='simple'))
        print("\n   Work the buy limit inside the zone — never chase above it. "
              "R = entry − stop (see section 12).")

    def _display_factor_grades(self, setups: List["TradingSetup"]) -> None:
        """Sector-relative factor grades — the 'why this name' at a glance."""
        if not setups or not any(s.factor_grades for s in setups):
            return
        rows = []
        for s in setups:
            grades = s.factor_grades or {}
            rows.append([
                s.ticker,
                s.sector[:18],
                *[grades.get(name, "—") for name in self.FACTOR_NAMES],
                s.quant_rating or "—",
                f"#{s.sector_rank} of {s.sector_peers}" if s.sector_peers else "—",
                "sector" if s.grade_basis == "sector" else "universe*",
            ])
        headers = ['Ticker', 'Sector', *self.FACTOR_NAMES,
                   'Quant Rating', 'Rank', 'Graded vs']
        print()
        print(tabulate(rows, headers=headers, tablefmt='simple'))
        print("\n   Percentile ranks within the peer set — A+ (top 5%) to F "
              "(bottom 8%), not absolute quality.")
        if any(s.grade_basis != "sector" for s in setups):
            print(f"   * fewer than {Config.MIN_SECTOR_PEERS} scored peers in that "
                  "sector, so the whole analysed universe was used instead.")
        print("   An internal factor model — not a validated alpha signal, and not "
              "any third-party rating.")

    def _display_execution_tickets(self, setups: List["TradingSetup"]) -> None:
        """Per-pick execution ticket: the orders to place and the case for them.

        Everything needed to put the position on lives in one block — limit,
        share count, stop, all three targets with the shares to sell and the
        dollars earned at each, the time stop, the invalidation level, the
        thesis and the risks — instead of being spread across eight tables.
        """
        if not setups:
            return
        capital = self.capital or 1.0
        for setup in setups:
            entry = setup.execution_entry or setup.optimal_entry or setup.entry_price
            risk_ps = max(1e-9, entry - setup.stop_loss)
            t1_shares, runner_shares = self._scale_out_plan(setup)
            conviction = ("⚠ LOW — confirmation only, half size"
                          if setup.conviction == "LOW" else "NORMAL — full size")
            # Structural invalidation: the highest support still below the
            # entry, i.e. the level whose loss breaks the thesis before the hard
            # stop is even reached.
            below = [lvl for lvl in setup.support_levels if 0 < lvl < entry]
            structural = max(below) if below else setup.stop_loss
            grades = " ".join(f"{n[:3]} {g}" for n, g in (setup.factor_grades or {}).items())

            print(f"\n┌─ {setup.ticker}  ·  {setup.signal}  ·  score "
                  f"{setup.overall_score:.0f}/100  ·  {conviction}")
            print(f"│  {setup.company[:58]}  ({setup.sector[:26]})")
            if grades:
                print(f"│  Quant {setup.quant_rating}   {grades}"
                      f"   #{setup.sector_rank} of {setup.sector_peers} "
                      f"{'in sector' if setup.grade_basis == 'sector' else 'analysed'}")
            print("│")
            print(f"│  BUY LIMIT  {self._fmt_shares(setup):>8} sh @ ${entry:,.2f}"
                  f"   zone ${setup.entry_zone_low:,.2f}–${setup.entry_zone_high:,.2f}"
                  f"   = ${setup.executable_allocation_usd:,.2f}"
                  f" ({setup.executable_allocation_usd / capital * 100:.1f}% of capital)")
            print(f"│  STOP       ${setup.stop_loss:,.2f} "
                  f"({(setup.stop_loss / entry - 1) * 100:+.1f}%)"
                  f"   −${setup.risk_amount:,.2f} ({setup.risk_pct:.2f}% of capital)"
                  f"   ${risk_ps:,.2f}/share = 1R")

            def target_line(label: str, price: float, shares: float, action: str) -> str:
                pct = (price / entry - 1) * 100 if entry else 0.0
                r = self._r_multiple(price, entry, risk_ps)
                money = (price - entry) * shares
                due = setup.eta_dates.get(label.replace("TARGET ", "T"), "")
                when = f"   due ~{due}" if due else ""
                return (f"│  {label:<10} ${price:,.2f} ({pct:+.1f}%, {r:.1f}R)"
                        f"   {action}"
                        + (f"   +${money:,.2f}" if shares > 0 else "") + when)

            if t1_shares > 0:
                action = (f"sell {t1_shares:g} sh" if runner_shares > 0
                          else f"close all {t1_shares:g} sh")
                print(target_line("TARGET 1", setup.primary_target, t1_shares, action))
            if runner_shares > 0:
                print(target_line("TARGET 2", setup.secondary_target, runner_shares,
                                  f"trail {runner_shares:g} sh by "
                                  f"{setup.trailing_stop_pct:.0f}%"))
                print(target_line("TARGET 3", setup.aggressive_target, runner_shares,
                                  "stretch — only if the trend keeps paying"))
            print(f"│  R/R        {setup.risk_reward_ratio:.2f}:1 to Target 1, net of "
                  f"{Config.FRICTION_ROUND_TRIP_PCT * 100:.2f}% round-trip cost"
                  f"   ·  break-even hit rate {setup.breakeven_win_rate:.0f}%")
            print(f"│  HOLD       {setup.expected_hold_days} sessions expected "
                  f"(min {setup.min_hold_days}) · review {setup.review_date or 'n/a'}"
                  f" · CLOSE BY {setup.time_stop_date or 'the time stop'}")
            print(f"│  INVALIDATE close < ${setup.stop_loss:,.2f}"
                  f"   ·  early warning: close < ${structural:,.2f} (structure)")
            if (setup.days_to_earnings is not None
                    and setup.days_to_earnings <= Config.EARNINGS_WARN_DAYS):
                print(f"│  ⚠ EARNINGS in {setup.days_to_earnings} day(s) — a gap can "
                      "clear the stop without trading at it. Consider skipping.")

            thesis = setup.thesis or self.build_thesis(setup)
            risks = setup.risks or self.build_risks(setup)
            if thesis:
                print("│")
                print("│  WHY THIS TRADE")
                for line in thesis:
                    print(f"│    + {line}")
            if risks:
                print("│  WHAT COULD GO WRONG")
                for line in risks:
                    print(f"│    − {line}")
            print("│")
            print("│  HOW TO EXECUTE")
            for index, instruction in enumerate(self._execution_steps(setup), 1):
                wrapped = textwrap.wrap(instruction, width=104)
                print(f"│    {index}. {wrapped[0]}")
                for continuation in wrapped[1:]:
                    print(f"│       {continuation}")
            print(f"└─ READINGS: {self._setup_thesis(setup)}")

    def _display_action_plan(self, setups: List["TradingSetup"]) -> None:
        """Synthesise the picks into a single ordered plan."""
        if not setups:
            return
        capital = self.capital or 1.0
        deployed = sum(s.executable_allocation_usd for s in setups)
        heat = sum(s.risk_amount for s in setups)
        budget = capital * Config.MAX_PORTFOLIO_RISK
        full_size = [s for s in setups if s.conviction != "LOW"]
        half_size = [s for s in setups if s.conviction == "LOW"]

        print()
        step = 1
        print(f"   {step}. Deploy ${deployed:,.2f} ({deployed / capital * 100:.1f}% of capital) "
              f"across {len(setups)} position(s); hold ${capital - deployed:,.2f} in cash.")
        step += 1
        if full_size:
            lead = full_size[0]
            entry = lead.execution_entry or lead.optimal_entry or lead.entry_price
            strengths = self._strong_grades(lead, "B", limit=3)
            because = (f" Strongest on {', '.join(strengths)}." if strengths else "")
            print(f"   {step}. Lead idea — {lead.ticker} "
                  f"(score {lead.overall_score:.0f}, RS {lead.relative_strength:.0f}, "
                  f"quant {lead.quant_rating or 'n/a'}, R/R {lead.risk_reward_ratio:.1f}:1)."
                  f"{because}")
            print(f"      Buy ${entry:,.2f} · stop ${lead.stop_loss:,.2f} · "
                  f"T1 ${lead.primary_target:,.2f}. Place this ticket first.")
            step += 1
            if len(full_size) > 1:
                rest = ", ".join(s.ticker for s in full_size[1:])
                print(f"   {step}. Full size also acceptable: {rest}.")
                step += 1
        if half_size:
            print(f"   {step}. Confirmation only (size already halved): "
                  f"{', '.join(s.ticker for s in half_size)}. "
                  "Wait for an intraday reclaim of the entry zone before adding.")
            step += 1
        headroom = budget - heat
        print(f"   {step}. Portfolio heat: ${heat:,.2f} ({heat / capital * 100:.2f}% of capital) "
              f"against a {Config.MAX_PORTFOLIO_RISK * 100:.0f}% budget "
              f"(${budget:,.2f}) — ${headroom:,.2f} of room left.")
        step += 1
        print(f"   {step}. Working orders: {len(setups)} buy limit(s) + "
              f"{len(setups)} protective stop(s). Place the stop in the same "
              "session the fill occurs — never overnight without one.")
        step += 1
        disagree = [s for s in setups if s.quant_rating in ("HOLD", "WEAK", "AVOID")]
        if disagree:
            print(f"   {step}. ⚠ Model and factor grades disagree on "
                  f"{', '.join(s.ticker for s in disagree)} — the setup cleared the "
                  "technical gate but")
            print("      the fundamentals rank poorly against peers. Treat these as "
                  "trades, not investments.")
            step += 1
        if getattr(self, 'regime_degraded', False) or self.benchmark_degraded:
            print(f"   {step}. ⚠ Degraded inputs this run "
                  f"({'regime' if self.regime_degraded else ''}"
                  f"{' & ' if self.regime_degraded and self.benchmark_degraded else ''}"
                  f"{'benchmark' if self.benchmark_degraded else ''}). "
                  "Treat scores as provisional and size down.")
            step += 1
        elif self.market_regime in (MarketRegime.BEAR_STRONG, MarketRegime.BEAR_WEAK,
                                    MarketRegime.VOLATILE):
            print(f"   {step}. ⚠ {self.market_regime.value} regime: long setups are "
                  "swimming upstream. Half size and honour time stops strictly.")
            step += 1

    def display_results(self, setups: List[TradingSetup]):
        """Display comprehensive results"""

        if not setups:
            print("\n" + "=" * 100)
            print("⚠️  NO FUNDED POSITIONS AT THIS CAPITAL LEVEL")
            print("=" * 100)
            eligible = getattr(self, 'position_count_eligible', 0)
            raw = len(getattr(self, 'results', []) or [])
            print(f"   • Raw candidates found:     {raw}")
            print(f"   • Fundable setups:          {eligible}")
            print(f"   • Final funded positions:   0")
            if self._display_watchlist():
                print("\n   Every candidate needs more than the available per-position "
                      "capital to buy one whole share.")
                print("   Increase capital, or set Config.ALLOW_FRACTIONAL_SHARES = True "
                      "to trade fractional shares.")
            else:
                print("   No candidates passed the model filters with the current scan.")
            print("=" * 100)
            return

        # Independent re-derivation of every number before anything is shown.
        # A pick whose arithmetic does not reconcile is a bug, and printing a
        # wrong stop is worse than printing nothing, so it is pulled out here
        # and reported rather than displayed.
        checked, rejected = [], []
        for setup in setups:
            problems = validate_setup(setup, self.capital)
            (rejected if problems else checked).append((setup, problems))
        if rejected:
            for setup, problems in rejected:
                logger.error("Self-check rejected %s: %s", setup.ticker,
                             "; ".join(problems))
        setups = [s for s, _ in checked]
        if not setups:
            print("\n⚠️  Every pick failed the internal consistency check — "
                  "see apex_trading.log. Nothing is shown rather than showing "
                  "levels that do not reconcile.")
            return

        # Build the narrative once here rather than in each renderer, so the
        # console report and the Markdown export cannot drift apart.
        for setup in setups:
            if not setup.thesis:
                setup.thesis = self.build_thesis(setup)
            if not setup.risks:
                setup.risks = self.build_risks(setup)

        bar = "═" * self.REPORT_WIDTH
        print("\n" + bar)
        print(f"{'🎯  APEX SHORT-TERM STOCK PICKS':^{self.REPORT_WIDTH}}")
        print(f"{self.analysis_date.strftime('%A %d %B %Y · %H:%M'):^{self.REPORT_WIDTH}}")
        strapline = (f"{len(setups)} position{'s' if len(setups) != 1 else ''} · "
                     f"${self.capital:,.0f} capital · "
                     f"{self.market_regime.value} regime")
        print(f"{strapline:^{self.REPORT_WIDTH}}")
        print(bar)

        self._display_at_a_glance(setups)

        self._section(2, "Market context", "the tape these picks were found in")
        self._display_market_context()
        if rejected:
            print(f"   • ⚠️  {len(rejected)} pick(s) withheld by the internal "
                  "consistency self-check (see apex_trading.log)")

        self._section(3, "The picks", "sorted by composite score")
        print()

        overview_data = []
        for setup in setups:
            signal_emoji = {
                'STRONG_BUY': '🔥',
                'BUY': '✅',
                'ACCUMULATE': '📈'
            }.get(setup.signal, '•')

            entry = setup.execution_entry or setup.optimal_entry or setup.entry_price
            overview_data.append([
                f"{signal_emoji} {setup.ticker}",
                setup.company[:22],
                (setup.signal + " ⚠LOW") if setup.conviction == "LOW" else setup.signal,
                setup.quant_rating or "—",
                f"{setup.overall_score:.0f}",
                f"{setup.relative_strength:.0f}",
                f"${entry:.2f}",
                f"${setup.stop_loss:.2f}",
                f"${setup.primary_target:.2f}",
                f"{setup.risk_reward_ratio:.1f}:1",
                f"{setup.expected_hold_days}d"
            ])

        headers1 = ['Symbol', 'Company', 'Signal', 'Quant', 'Score', 'RS',
                    'Entry', 'Stop', 'Target 1', 'R/R', 'Hold']
        print(tabulate(overview_data, headers=headers1, tablefmt='grid'))

        # Low-conviction legend: explain the ⚠LOW marker and why size was cut.
        low_conv = [s for s in setups if s.conviction == "LOW"]
        if low_conv:
            print("\n⚠️  LOW-CONVICTION PICKS (confirmation only — size halved):")
            for s in low_conv:
                print(f"   • {s.ticker}: {', '.join(s.conviction_reasons)}")

        # === THE ACTIONABLE CORE ===
        self._section(4, "Trade levels", "the prices to work")
        self._display_trade_levels(setups)

        self._section(5, "Timing", "when each level is due, in trading sessions")
        self._display_timing(setups)

        self._section(6, "Factor grades", "percentile rank against sector peers")
        self._display_factor_grades(setups)

        self._section(7, "Execution tickets", "the orders to place, per position")
        self._display_execution_tickets(setups)

        self._section(8, "Supporting data", "the readings behind the scores")

        # (The old exit-strategy table lived here; _display_trade_levels
        # supersedes it — same levels plus R-multiples and the entry zone.)

        print("\n   Position sizing & allocation\n")

        position_data = []
        for setup in setups:
            executable = setup.executable_allocation_usd
            executable_pct = (executable / self.capital * 100) if self.capital else 0.0
            position_data.append([
                setup.ticker,
                f"${executable:,.0f}",
                f"{executable_pct:.1f}%",
                self._fmt_shares(setup),
                f"${setup.risk_amount:.2f}",
                f"{setup.risk_pct:.2f}%",
                f"{setup.position_score:.0f}/100"
            ])

        headers3 = ['Ticker', 'Capital', '% Port', 'Shares',
                'Risk $', 'Risk % Cap', 'Pos Score']
        print(tabulate(position_data, headers=headers3, tablefmt='simple'))

        print("\n   Technical indicators\n")

        tech_data = []
        for setup in setups:
            rsi_emoji = "🟢" if 35 <= setup.rsi <= 65 else ("🔴" if setup.rsi > 70 else "🟡")
            macd_emoji = "✅" if setup.macd > setup.macd_signal else "⚠️"

            tech_data.append([
                setup.ticker,
                f"{rsi_emoji} {setup.rsi:.0f}",
                setup.rsi_trend[:6],
                f"{macd_emoji} {setup.macd_histogram:.2f}",
                f"{setup.volume_ratio:.1f}x",
                setup.obv_trend[:12],
                f"{setup.atr_pct:.2f}%",
                f"{setup.price_vs_sma50:+.1f}%",
                f"{setup.relative_strength:.0f}/99"
            ])

        headers4 = ['Ticker', 'RSI', 'Trend', 'MACD', 'Vol', 'OBV', 'ATR%', 'vs MA50', 'RS Rating']
        print(tabulate(tech_data, headers=headers4, tablefmt='simple'))

        print("\n   Momentum & risk-adjusted quality\n")

        momentum_data = []
        for setup in setups:
            mom_emoji = "🔥" if setup.momentum_1m > 10 else ("📈" if setup.momentum_1m > 5 else "➡️")

            momentum_data.append([
                setup.ticker,
                f"{mom_emoji} {setup.momentum_1w:+.1f}%",
                f"{setup.momentum_1m:+.1f}%",
                f"{setup.momentum_3m:+.1f}%",
                f"{setup.sharpe_ratio:.2f}",
                f"{setup.volatility_20d:.1f}%",
                setup.trend_strength[:12],
                f"{setup.breakout_potential:.0f}"
            ])

        headers5 = ['Ticker', '1W Mom', '1M Mom', '3M Mom',
                    'Sharpe', 'Vol%', 'Trend', 'Breakout']
        print(tabulate(momentum_data, headers=headers5, tablefmt='simple'))

        print("\n   Fundamentals\n")

        fund_data = []
        for setup in setups:
            fund_data.append([
                setup.ticker,
                setup.market_cap_category[:10],
                f"{setup.pe_ratio:.1f}" if setup.pe_ratio > 0 else "N/A",
                f"{setup.peg_ratio:.2f}" if setup.peg_ratio > 0 else "N/A",
                f"{setup.profit_margin:.1f}%",
                f"{setup.roe:.1f}%",
                f"{setup.revenue_growth:+.1f}%",
                setup.sector[:15]
            ])

        headers6 = ['Ticker', 'Cap', 'P/E', 'PEG', 'Margin', 'ROE', 'Rev Gr', 'Sector']
        print(tabulate(fund_data, headers=headers6, tablefmt='simple'))

        # === PORTFOLIO SUMMARY ===
        # Portfolio accounting is executable accounting. Intended allocations
        # for unfunded candidates are shown only in the watchlist.
        total_allocation = sum(s.executable_allocation_usd for s in setups)
        total_risk = sum(s.risk_amount for s in setups)
        avg_rr = (sum(s.risk_reward_ratio for s in setups) / len(setups)) if setups else 0.0
        avg_score = (sum(s.overall_score for s in setups) / len(setups)) if setups else 0.0

        self._section(9, "Portfolio", "aggregate exposure and review")
        print()

        capital = self.capital or 1.0  # a zero-capital run must not divide by zero
        risk_budget = capital * Config.MAX_PORTFOLIO_RISK
        summary_data = [
            ['Total Capital', f"${self.capital:,.2f}"],
            ['Allocated Capital',
             f"${total_allocation:,.2f} ({total_allocation / capital * 100:.1f}%)"],
            ['Cash Reserve',
             f"${self.capital - total_allocation:,.2f} "
             f"({(1 - total_allocation / capital) * 100:.1f}%)"],
            ['Total Risk Amount',
             f"${total_risk:,.2f} ({total_risk / capital * 100:.2f}%)"],
            ['Portfolio Risk Budget',
             f"${risk_budget:,.2f} ({Config.MAX_PORTFOLIO_RISK * 100:.0f}%) — "
             f"${risk_budget - total_risk:,.2f} unused"],
            ['Number of Positions', str(len(setups))],
            ['Average R/R Ratio', f"{avg_rr:.2f}:1 (net of costs)"],
            ['Average Setup Score', f"{avg_score:.1f}/100"],
            ['Sectors Represented', str(len(set(s.sector for s in setups)))],
            ['Market Regime', self.market_regime.value
             + (' (degraded)' if getattr(self, 'regime_degraded', False) else '')],
            ['SPY 3M Return',
             "N/A (degraded)" if self.spy_3m_return is None
             else f"{self.spy_3m_return * 100:+.1f}%"],
        ]

        print(tabulate(summary_data, tablefmt='simple'))

        # === WATCHLIST (allocation below one whole share) ===
        self._display_watchlist()

        # === HEDGE-FUND PM REVIEW OF FINAL PICKS ===
        review_rows = self._validate_final_picks(setups)
        print("\n   Portfolio manager review\n")
        pm_table = [
            [r['ticker'], r['status'], r['issue'][:42],
             r['fix'][:32], r['pm_comment'][:40]]
            for r in review_rows
        ]
        pm_headers = ['Ticker', 'Pass/Review', 'Issue', 'Fix Needed', 'PM Comment']
        print(tabulate(pm_table, headers=pm_headers, tablefmt='grid'))
        flagged = [r for r in review_rows if r['status'] != 'PASS']
        if flagged:
            print(f"\n   ⚠️  {len(flagged)}/{len(review_rows)} picks need review "
                  "before full-size allocation (see Issue / Fix columns).")

        # === SIGNAL DISTRIBUTION ===
        print("\n📊 SIGNAL DISTRIBUTION: ", end="")
        signal_counts = Counter(s.signal for s in setups)
        for signal, count in signal_counts.most_common():
            emoji = {
                'STRONG_BUY': '🔥',
                'BUY': '✅',
                'ACCUMULATE': '📈'
            }.get(signal, '•')
            print(f"{emoji} {signal}: {count}  ", end="")
        print()

        # === DETECTED PATTERNS ===
        all_patterns = []
        for setup in setups:
            all_patterns.extend(setup.detected_patterns)

        if all_patterns:
            pattern_counts = Counter(all_patterns)
            print("\n🔍 DETECTED PATTERNS: ", end="")
            for pattern, count in pattern_counts.most_common(5):
                print(f"{pattern} ({count})  ", end="")
            print()

        # === 5-MIN SCALPING PLAYBOOK (Top 5 setups) ===
        self._section(10, "Intraday refinement", "optional — for timing the entry only")
        self._display_scalping_playbook(setups[:5])

        # === SYNTHESIS: what to actually do ===
        self._section(11, "Action plan", "in order")
        self._display_action_plan(setups)

        self._section(12, "How to read this report")
        print()
        self._display_legend()

        print("\n" + "─" * self.REPORT_WIDTH)
        print(" STANDING RULES")
        print("   • Work the buy limit inside the entry zone — never chase above it.")
        print("   • The protective stop goes in the same session as the fill.")
        print(f"   • Scale {Config.SCALE_OUT_T1_FRACTION * 100:.0f}% off at Target 1, "
              "trail the runner; never widen the initial stop.")
        print("   • Close the position at the time stop even if neither level is hit —")
        print("     a setup that has not worked in its window is no longer the setup.")
        print("   • Re-run the scan daily: entries, stops and targets are all ATR-scaled")
        print("     and move with the tape.")
        print("=" * 120)

        print("\n⚠️  DISCLAIMER: For educational purposes only. Not financial advice.")
        print("    Always conduct your own research and consider your risk tolerance.")
        print("=" * 120 + "\n")

    def _build_markdown_report(self, setups: List[TradingSetup]) -> str:
        """Render the run as a self-contained Markdown briefing.

        The JSON/CSV exports are machine-readable snapshots of the dataclass;
        this is the artefact a human actually reads or pastes into a journal —
        the plan, the tickets, and the caveats, in that order.
        """
        capital = self.capital or 1.0
        deployed = sum(s.executable_allocation_usd for s in setups)
        heat = sum(s.risk_amount for s in setups)
        diag = getattr(self, 'scan_diagnostics', {}) or {}
        out: List[str] = []
        add = out.append

        add(f"# APEX Short-Term Picks — {self.analysis_date:%Y-%m-%d %H:%M}")
        add("")
        add(f"*Generated by APEX v{__version__}. Educational use only — not financial advice.*")
        add("")
        add("## Market context")
        add("")
        add(f"- **Regime:** {self.market_regime.value}"
            f"{' _(degraded)_' if getattr(self, 'regime_degraded', False) else ''}")
        add(f"- **SPY 3M:** " + ("n/a _(degraded)_" if self.spy_3m_return is None
                                 else f"{self.spy_3m_return * 100:+.1f}%"))
        add(f"- **Capital:** ${self.capital:,.2f} · "
            f"**Deployed:** ${deployed:,.2f} ({deployed / capital * 100:.1f}%) · "
            f"**Cash:** ${self.capital - deployed:,.2f}")
        add(f"- **Portfolio heat:** ${heat:,.2f} ({heat / capital * 100:.2f}%) "
            f"of a {Config.MAX_PORTFOLIO_RISK * 100:.0f}% budget")
        if diag:
            add(f"- **Breadth:** {diag.get('opportunities_found', 0)} qualified / "
                f"{diag.get('successfully_analyzed', 0)} analysed / "
                f"{diag.get('universe_count', 0)} universe")
            if diag.get('provider_failures'):
                add(f"- **⚠ Provider failures:** {diag['provider_failures']} ticker(s) "
                    "— coverage incomplete")
        add("")

        add("## At a glance")
        add("")
        add(f"- **{len(setups)} position{'s' if len(setups) != 1 else ''} to open:** "
            + ", ".join(f"`{s.ticker}`" for s in setups))
        lead = ([s for s in setups if s.conviction != "LOW"] or setups)[0]
        lead_entry = lead.execution_entry or lead.optimal_entry or lead.entry_price
        add(f"- **Lead idea:** {lead.ticker} — buy ${lead_entry:,.2f}, "
            f"stop ${lead.stop_loss:,.2f}, first target ${lead.primary_target:,.2f} "
            f"({lead.risk_reward_ratio:.1f}:1)")
        add(f"- **Close everything by:** {lead.time_stop_date or 'the time stop'}")
        low = [s.ticker for s in setups if s.conviction == "LOW"]
        if low:
            add(f"- **⚠ Half size only:** {', '.join(low)} — confirmation required")
        add("")

        add("## Trade levels")
        add("")
        add("| # | Ticker | Buy limit | Entry zone | Stop | Target 1 | Target 2 |"
            " Target 3 | R/R | Shares | Capital | Risk |")
        add("|---|--------|-----------|------------|------|----------|----------|"
            "----------|-----|--------|---------|------|")
        for i, s in enumerate(setups, 1):
            entry = s.execution_entry or s.optimal_entry or s.entry_price
            risk_ps = max(1e-9, entry - s.stop_loss)

            def lvl(price: float) -> str:
                pct = (price / entry - 1) * 100 if entry else 0.0
                return (f"${price:,.2f}<br>{pct:+.1f}% · "
                        f"{self._r_multiple(price, entry, risk_ps):.1f}R")

            signal = s.signal + (" ⚠LOW" if s.conviction == "LOW" else "")
            add(f"| {i} | **{s.ticker}**<br>{signal} | ${entry:,.2f} | "
                f"${s.entry_zone_low:,.2f}–${s.entry_zone_high:,.2f} | "
                f"${s.stop_loss:,.2f}<br>{(s.stop_loss / entry - 1) * 100:+.1f}% | "
                f"{lvl(s.primary_target)} | {lvl(s.secondary_target)} | "
                f"{lvl(s.aggressive_target)} | {s.risk_reward_ratio:.2f}:1 | "
                f"{self._fmt_shares(s)} | ${s.executable_allocation_usd:,.2f} | "
                f"${s.risk_amount:,.2f} |")
        add("")
        add("*R = one unit of this trade's own risk (entry − stop).*")
        add("")

        add("## Timing")
        add("")
        add("| Ticker | Style | Expected hold | T1 due | T2 due | T3 due |"
            " Review on | Close by |")
        add("|--------|-------|---------------|--------|--------|--------|"
            "-----------|----------|")
        for s in setups:
            eta, dates = s.eta_sessions or {}, s.eta_dates or {}

            def due(label: str) -> str:
                return (f"{eta[label]}d<br>{dates[label]}" if label in eta else "—")

            add(f"| **{s.ticker}** | {s.timeframe} | {s.expected_hold_days}d | "
                f"{due('T1')} | {due('T2')} | {due('T3')} | "
                f"{s.review_date or '—'} | **{s.time_stop_date or '—'}** |")
        add("")
        add("*ETAs measure the distance to each target in ATR (average daily range) — "
            "estimates of PACE, not predictions that the level will be reached. "
            "\"Close by\" is a hard time stop. Sessions are trading days, holidays "
            "excluded.*")
        add("")

        if any(s.factor_grades for s in setups):
            add("## Quant factor grades")
            add("")
            add("| Ticker | Sector | " + " | ".join(self.FACTOR_NAMES)
                + " | Rating | Rank | Graded vs |")
            add("|--------|--------|" + "|".join(["---"] * len(self.FACTOR_NAMES))
                + "|--------|------|-----------|")
            for s in setups:
                g = s.factor_grades or {}
                add(f"| **{s.ticker}** | {s.sector} | "
                    + " | ".join(g.get(n, "—") for n in self.FACTOR_NAMES)
                    + f" | {s.quant_rating or '—'} | #{s.sector_rank} of "
                    f"{s.sector_peers} | {s.grade_basis or '—'} |")
            add("")
            add("*Percentile ranks within the peer set, A+ (top 5%) to F (bottom 8%) — "
                "an internal factor model, not a validated alpha signal and not any "
                "third-party rating.*")
            add("")

        add("## Execution tickets")
        add("")
        for s in setups:
            entry = s.execution_entry or s.optimal_entry or s.entry_price
            t1_shares, runner = self._scale_out_plan(s)
            add(f"### {s.ticker} — {s.company}")
            add("")
            add(f"- **Buy limit:** {self._fmt_shares(s)} sh @ ${entry:,.2f} "
                f"(zone ${s.entry_zone_low:,.2f}–${s.entry_zone_high:,.2f})")
            add(f"- **Stop:** ${s.stop_loss:,.2f} "
                f"({(s.stop_loss / entry - 1) * 100:+.1f}%) — risk ${s.risk_amount:,.2f} "
                f"({s.risk_pct:.2f}% of capital)")
            add(f"- **Target 1:** ${s.primary_target:,.2f} "
                f"({(s.primary_target / entry - 1) * 100:+.1f}%) — take {t1_shares:g} sh")
            if runner > 0:
                add(f"- **Target 2:** ${s.secondary_target:,.2f} "
                    f"({(s.secondary_target / entry - 1) * 100:+.1f}%) — "
                    f"trail {runner:g} sh by {s.trailing_stop_pct:.1f}%")
            add(f"- **Target 3:** ${s.aggressive_target:,.2f} "
                f"({(s.aggressive_target / entry - 1) * 100:+.1f}%) — stretch")
            add(f"- **Net R/R:** {s.risk_reward_ratio:.2f}:1 after "
                f"{Config.FRICTION_ROUND_TRIP_PCT * 100:.2f}% round-trip cost "
                f"(break-even hit rate {s.breakeven_win_rate:.0f}%)")
            add(f"- **Timing:** {s.expected_hold_days} sessions expected · "
                f"review {s.review_date or 'n/a'} · "
                f"**close by {s.time_stop_date or 'the time stop'}**")
            add(f"- **Invalidation:** close below ${s.stop_loss:,.2f}")
            if (s.days_to_earnings is not None
                    and s.days_to_earnings <= Config.EARNINGS_WARN_DAYS):
                add(f"- **⚠ Earnings in {s.days_to_earnings} day(s)**")
            if s.conviction == "LOW":
                add(f"- **⚠ Low conviction:** {', '.join(s.conviction_reasons)}")
            add("")
            for line in (s.thesis or self.build_thesis(s)):
                add(f"- ✅ {line}")
            for line in (s.risks or self.build_risks(s)):
                add(f"- ⚠️ {line}")
            add("")
            add("**How to execute**")
            add("")
            for index, instruction in enumerate(self._execution_steps(s), 1):
                add(f"{index}. {instruction}")
            add("")
            add(f"*Readings: {self._setup_thesis(s)}*")
            add("")

        review = self._validate_final_picks(setups)
        flagged = [r for r in review if r['status'] != 'PASS']
        if flagged:
            add("## PM review flags")
            add("")
            add("| Ticker | Issue | Fix |")
            add("|--------|-------|-----|")
            for r in flagged:
                add(f"| {r['ticker']} | {r['issue']} | {r['fix']} |")
            add("")

        watchlist = [s for s in getattr(self, 'watchlist_setups', []) or []
                     if not (s.is_funded and s.shares > 0)]
        if watchlist:
            add("## Watchlist (not fundable at this capital)")
            add("")
            add("| Ticker | Signal | Entry | Intended | Reason |")
            add("|--------|--------|-------|----------|--------|")
            for s in watchlist:
                reason = (s.watchlist_reason or "; ".join(s.conviction_reasons)
                          or "below one share")
                add(f"| {s.ticker} | {s.signal} | ${s.entry_price:,.2f} | "
                    f"${s.intended_allocation_usd:,.2f} | {reason} |")
            add("")

        add("---")
        add("")
        add("**Disclaimer:** educational purposes only. Not financial advice. "
            "Levels are ATR-scaled and move with the tape — re-run before acting.")
        add("")
        return "\n".join(out)

    def export_results(self, setups: List[TradingSetup],
                       filename_prefix: str = "apex_setups",
                       output_dir: str = "."):
        """Export the run to JSON, CSV and (optionally) a Markdown briefing."""

        if not setups:
            print("⚠️  No setups to export")
            return

        if output_dir and output_dir != ".":
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as exc:
                print(f"⚠️  Could not create {output_dir}: {_brief(exc)} — writing to CWD")
                output_dir = "."
        filename_prefix = os.path.join(output_dir, filename_prefix)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        allocation_mode = "fractional" if Config.ALLOW_FRACTIONAL_SHARES else "whole_share"
        position_count_eligible = getattr(
            self, 'position_count_eligible',
            sum(1 for s in setups if s.is_funded and s.shares > 0)
        )

        try:
            # JSON Export
            json_data = {
                'metadata': {
                    'timestamp': self.analysis_date.isoformat(),
                    'version': __version__,
                    'capital': self.capital,
                    'num_setups': len(setups),
                    'allocation_mode': allocation_mode,
                    'position_count_eligible': position_count_eligible,
                },
                'setups': [s.to_dict() for s in setups]
            }

            json_file = f"{filename_prefix}_{timestamp}.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, default=str)

            print(f"✅ JSON exported: {json_file}")

            # CSV Export — broadcast the run-level allocation metadata onto every
            # row so the file is self-describing without the JSON sidecar.
            csv_file = f"{filename_prefix}_{timestamp}.csv"
            rows = []
            for s in setups:
                row = s.to_dict()
                row['allocation_mode'] = allocation_mode
                row['position_count_eligible'] = position_count_eligible
                rows.append(row)
            df = pd.DataFrame(rows)
            df.to_csv(csv_file, index=False)

            print(f"✅ CSV exported: {csv_file}")

            # Markdown briefing — the human-readable artefact.
            if Config.EXPORT_MARKDOWN:
                md_file = f"{filename_prefix}_{timestamp}.md"
                with open(md_file, 'w', encoding='utf-8') as f:
                    f.write(self._build_markdown_report(setups))
                print(f"✅ Markdown report exported: {md_file}")

        except Exception as e:
            print(f"⚠️  Export error: {_brief(e)}")
            logger.debug("Export failed", exc_info=True)


# ============================================================================
# MAIN EXECUTION
# ============================================================================

SCAN_CHOICES = ("quick", "standard", "comprehensive", "full")


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI for non-interactive runs (cron, CI, notebooks).

    With no arguments the original interactive prompts are used, so existing
    workflows are unchanged; supplying any of these switches skips the matching
    prompt, and ``--yes`` skips all of them.
    """
    parser = argparse.ArgumentParser(
        prog="SP_ShortTerm",
        description=(f"APEX Short-Term Stock Picks Analyzer v{__version__} — "
                     "rule-based multi-factor scanner with risk-first sizing."),
        epilog="Educational purposes only. Not financial advice.",
    )
    parser.add_argument("--capital", type=float,
                        help=f"trading capital in USD (default {Config.DEFAULT_CAPITAL})")
    parser.add_argument("--scan", choices=SCAN_CHOICES,
                        help="universe size to scan (default standard)")
    parser.add_argument("--max-positions", type=int, metavar="N",
                        help="maximum funded positions, 1-10 (default 5)")
    parser.add_argument("--fractional", dest="fractional", action="store_true",
                        default=None,
                        help="allow decimal share quantities")
    parser.add_argument("--whole-shares", dest="fractional", action="store_false",
                        help="force whole-share accounting (default)")
    parser.add_argument("--export", dest="export", action="store_true", default=None,
                        help="write JSON/CSV/Markdown without prompting")
    parser.add_argument("--no-export", dest="export", action="store_false",
                        help="skip the export step")
    parser.add_argument("--output-dir", default=".", metavar="DIR",
                        help="directory for exported files (default: current)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="run non-interactively using defaults for anything not given")
    return parser


def _prompt_capital(default: float) -> float:
    try:
        raw = input(f"\n💰 Trading capital [default: ${default:,.0f}]: $").strip()
        return max(500.0, float(raw.replace(',', ''))) if raw else default
    except (ValueError, EOFError):
        print(f"   Using default: ${default:,.2f}")
        return default


def _prompt_scan_type() -> str:
    print("\n📊 SELECT SCAN TYPE:")
    print("   1. Quick (75 stocks, ~2-3 min)")
    print("   2. Standard (250 stocks, ~5-8 min) [Recommended]")
    print("   3. Comprehensive (400 stocks, ~10-15 min)")
    print("   4. Full Market (entire universe, ~20-30 min)")
    try:
        choice = input("\n🔍 Enter choice (1-4) [default: 2]: ").strip() or "2"
    except EOFError:
        choice = "2"
    return {"1": "quick", "2": "standard",
            "3": "comprehensive", "4": "full"}.get(choice, "standard")


def _prompt_max_positions(default: int = 5) -> int:
    try:
        raw = input(f"\n📊 Maximum positions [1-10, default: {default}]: ").strip()
        return max(1, min(10, int(raw))) if raw else default
    except (ValueError, EOFError):
        return default


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns a process exit code.

    ``--help`` / usage errors exit through argparse before the run banner, so
    the sign-off footer below is only printed for an actual analysis run.
    """
    args = build_arg_parser().parse_args(argv)
    try:
        return _run(args)
    finally:
        print("\n" + "=" * 100)
        print(f"Thank you for using APEX Short-Term Stock Picks Analyzer v{__version__}")
        print("Remember: This is for educational purposes only, not financial advice")
        print("=" * 100 + "\n")


def _run(args: argparse.Namespace) -> int:
    """Execute one configured analysis run."""
    non_interactive = args.yes

    print("\n" + "🌟" * 50)
    print(" " * 35 + f"WELCOME TO APEX v{__version__}")
    print(" " * 24 + "Short-Term Stock Picks Analyzer")
    print("🌟" * 50)

    print("\n" + "=" * 100)
    print("CONFIGURATION")
    print("=" * 100)

    if args.capital is not None:
        capital = max(500.0, args.capital)
    elif non_interactive:
        capital = float(Config.DEFAULT_CAPITAL)
    else:
        capital = _prompt_capital(float(Config.DEFAULT_CAPITAL))

    if args.scan:
        scan_type = args.scan
    elif non_interactive:
        scan_type = "standard"
    else:
        scan_type = _prompt_scan_type()

    if args.max_positions is not None:
        max_positions = max(1, min(10, args.max_positions))
    elif non_interactive:
        max_positions = 5
    else:
        max_positions = _prompt_max_positions()

    if args.fractional is not None:
        Config.ALLOW_FRACTIONAL_SHARES = args.fractional

    share_mode = "FRACTIONAL" if Config.ALLOW_FRACTIONAL_SHARES else "WHOLE-SHARE"
    print("\n" + "=" * 100)
    print("CONFIGURATION SUMMARY:")
    print(f"   💰 Capital: ${capital:,.2f}")
    print(f"   📊 Scan Type: {scan_type.upper()}")
    print(f"   🎯 Max Positions: {max_positions}")
    print(f"   🧮 Share Mode: {share_mode}")
    print(f"   ⏱️  Strategy: Short-term swing & position trading")
    print("=" * 100)

    if not non_interactive:
        try:
            if input("\n🚀 Start analysis? [Y/n]: ").strip().lower() == 'n':
                print("\n👋 Analysis cancelled. Goodbye!")
                return 0
        except EOFError:
            pass  # piped stdin: treat as confirmation

    try:
        analyzer = StrategicStockAnalyzer(capital=capital)

        all_setups = analyzer.scan_market(scan_type=scan_type)

        if not all_setups:
            print("\n❌ No candidates passed the model filters with current settings")
            print("   The rejection funnel above shows exactly which gate stopped each")
            print("   name — try a broader scan type, or run again after the close.")
            return 0

        # Funded, risk/sector/correlation-limited final selection.
        top_setups = analyzer.get_top_setups(max_positions=max_positions)

        # display_results handles the empty (no funded positions) case by
        # showing the funding breakdown + watchlist instead of a bare message.
        analyzer.display_results(top_setups)

        if args.export is not None:
            do_export = args.export
        elif non_interactive:
            do_export = True
        else:
            try:
                do_export = input("\n📁 Export results to files? [Y/n]: ").strip().lower() != 'n'
            except EOFError:
                do_export = True

        if do_export:
            # Fall back to the watchlist when nothing was funded so a run is
            # never silently dropped.
            to_export = top_setups or getattr(analyzer, 'watchlist_setups', [])
            analyzer.export_results(to_export, output_dir=args.output_dir)

        print("\n✅ Analysis complete!")
        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️  Analysis interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Critical error: {_brief(e)}")
        logger.exception("Critical error in main execution")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n👋 Exiting APEX. Thank you!")
        sys.exit(130)
