"""Every decision threshold QUANTA applies, in one place.

The scanner (SP_ELITE_PRO), the Research engine (TradingAnalysis_ELITE), the
decision core (engine/*) and the web app all read their cut-offs from here, and
``GET /api/thresholds`` publishes :func:`as_dict` so the page can show the rule
that actually decided. Reason and remedy text is formatted from these values at
the moment it is written, never typed as a number.

Rules for this file:

* A value lives here once. Older names (``TradingConfig.RISK_REWARD_MIN``,
  ``DecisionInputs.min_rr_for_buy`` ...) are read-through aliases (:class:`alias`),
  so patching either the group or the alias changes behaviour, and the two can
  never hold different numbers.
* Groups are plain classes of UPPER_CASE attributes so tests can monkeypatch one
  value without rebuilding anything.
* Values were moved here unchanged (2026-10 Picks remediation, audit F9). Where
  an environment variable already overrode a value, it still does.
* Factor weights are model parameters, not thresholds, and are not here.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class alias:
    """A class attribute that reads ``getattr(group, name)`` on every access.

    ``TradingConfig.RISK_REWARD_MIN = alias(RR, "SCANNER_MIN")`` keeps the old
    name working while the value lives only in ``RR``. Assigning to the alias on
    the owning class (as a test's monkeypatch does) replaces it for that class;
    monkeypatch restores it afterwards.
    """

    def __init__(self, group: type, name: str):
        self.group, self.name = group, name

    def __get__(self, obj: Any, owner: Any = None) -> Any:
        return getattr(self.group, self.name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"alias({self.group.__name__}.{self.name})"


class _Group:
    """Base for threshold groups: UPPER_CASE attributes, exported by :func:`as_dict`."""

    @classmethod
    def values(cls) -> Dict[str, Any]:
        out = {}
        for klass in reversed(cls.__mro__):
            for k, raw in vars(klass).items():
                if k.isupper() and (isinstance(raw, alias) or not callable(raw)):
                    out[k] = copy.deepcopy(getattr(cls, k))
        return out


# ── Capital and the scan form ────────────────────────────────────────────────

class CAPITAL(_Group):
    DEFAULT_US = 2500.0             # form default when no account cash is known
    MIN_US = 100.0                  # form minimum, US
    DEFAULT_INDIA = 200000.0        # form default for an INR budget
    MIN_INDIA = 10000.0             # form minimum, India
    MAX = 1_000_000_000.0           # API upper bound
    MAX_POSITIONS_DEFAULT = 5
    MAX_POSITIONS_LIMIT = 100       # API upper bound
    MAX_CUSTOM_TICKERS = 500
    MAX_HOLDINGS_ROWS = 500


# ── Universe, data and liquidity gates (scanner) ─────────────────────────────

class UNIVERSE(_Group):
    MIN_MARKET_CAP = 2_000_000_000.0
    # A scan type whose universe sits below MIN_MARKET_CAP by design uses its own
    # floor, or its names are filtered out before scoring (Russell 2000 members
    # are mostly under $2B). Micro-caps below this stay out.
    # Large-Cap Bluechips keeps only companies of at least $10B, the usual
    # large-cap line.
    SCAN_MIN_MARKET_CAP = {"small_cap": 300_000_000.0, "large_cap": 10_000_000_000.0}
    # Live index lists (engine/index_lists.py): a parse shorter than this means
    # the page changed, and the dated snapshot is used instead.
    INDEX_MIN_CONSTITUENTS = {"sp500": 400, "nasdaq100": 90, "dow30": 28, "us_etfs": 500,
                              "sp400": 380, "sp600": 560, "aristocrats": 50,
                              "nifty50": 48, "niftynext50": 48, "nifty500": 480,
                              "niftybank": 10, "niftyit": 8, "sensex": 28,
                              "nifty100": 95, "niftymidcap150": 140, "niftysmallcap250": 235}
    INDEX_CACHE_SECONDS = 6 * 3600
    MIN_PRICE = 10.0
    MAX_PRICE = 5000.0
    MIN_AVG_VOLUME = 750_000
    MIN_DAILY_DOLLAR_VOLUME = 20_000_000.0
    # Owner decision 2026-10-06: Small-Cap reads the S&P 600, whose members trade
    # far less than the large-cap floors above, which dropped most of the index
    # before ranking. Applied in the pre-filter and in analyze_stock alike.
    SCAN_FLOORS = {"small_cap": {"MIN_PRICE": 5.0, "MIN_AVG_VOLUME": 250_000,
                                 "MIN_DAILY_DOLLAR_VOLUME": 5_000_000.0}}
    MIN_HISTORY_DAYS = 200
    # The latest daily bar may be at most this many completed sessions behind the
    # last completed session (US: the NYSE calendar; India: weekdays on the India
    # clock, as engine.market_calendar has no NSE holiday list). Was 10 calendar days.
    MAX_LIVE_DATA_AGE_SESSIONS = 3
    PREFILTER_TIMEOUT_SECONDS = _env_float("QUANTA_PREFILTER_TIMEOUT", 90.0)
    PREFILTER_MAX_TICKERS = 1000
    # India listings (.NS / .BO), in INR (audit A08; were inline in analyze_stock).
    INDIA_MIN_PRICE = 50.0
    INDIA_MAX_PRICE = 100_000.0
    INDIA_MIN_MARKET_CAP = 50_000_000_000.0     # Rs 5,000 crore
    INDIA_MIN_AVG_VOLUME = 100_000
    INDIA_MIN_DAILY_VALUE = 50_000_000.0        # Rs 5 crore traded per day
    # India Small-Cap reads the Nifty Smallcap 250, whose members sit well under
    # the Rs 5,000 crore default floor; it keeps companies of Rs 1,000 crore and up.
    INDIA_SCAN_MIN_MARKET_CAP = {"india_small": 10_000_000_000.0}
    # India preset scans rank their index by daily traded value and analyse the
    # most liquid names, as the US style and cap scans do.
    # US interactive scans analyse this many names, most liquid first (yfinance
    # throttles hard, so each preset is capped). Full exceeds the assembled
    # universe on purpose.
    US_SCAN_LIMITS = {"quick": 100, "standard": 250, "comprehensive": 400,
                      "focused_growth": 150, "focused_value": 150, "focused_momentum": 150,
                      "focused_dividend": 100, "focused_etf": 80,
                      "large_cap": 120, "mid_cap": 100, "small_cap": 80, "pre_momentum": 48}
    # Pre-Momentum (semis / AI infrastructure): index names in these GICS
    # sub-industries (from the live constituent tables), momentum-screened.
    PRE_MOMENTUM_SUB_INDUSTRIES = (
        "Semiconductors", "Semiconductor Materials & Equipment",
        "Communications Equipment", "Technology Hardware, Storage & Peripherals",
        "Electronic Components", "Electronic Equipment & Instruments",
        "Electrical Components & Equipment", "Heavy Electrical Equipment",
        "Construction & Engineering", "Internet Services & Infrastructure",
        "Data Center REITs", "Independent Power Producers & Energy Traders",
    )
    FULL_SCAN_LIMIT = 10_000
    INDIA_SCAN_LIMITS = {"india_growth": 150, "india_value": 150, "india_dividend": 150,
                         "india_momentum": 150, "india_large": 100, "india_mid": 100, "india_small": 100}
    # The sectors a scan can be narrowed to (the provider's sector names, both markets).
    SECTORS = ("Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive",
               "Energy", "Financial Services", "Healthcare", "Industrials", "Real Estate",
               "Technology", "Utilities")
    # Annual risk-free rate for Sharpe, Sortino and alpha, per market.
    RISK_FREE_RATE_US = 0.04
    RISK_FREE_RATE_INDIA = 0.065
    ZSCORE_MIN_UNIVERSE = _env_int("QUANTA_ZSCORE_MIN_UNIVERSE", 30)


# ── Scanner score (0-10) ─────────────────────────────────────────────────────

class SCORE(_Group):
    MIN_SCORE = 3.5                 # candidate-generation floor
    ACCUMULATE = 5.5                # mirrors the core's 55 band
    GOOD = 6.5                      # BUY tier, mirrors 65
    EXCELLENT = 7.5                 # STRONG_BUY tier, mirrors 75
    ADJUSTMENT_CAP = 1.0
    # Names that move with bitcoin must clear MIN_SCORE plus this premium.
    CRYPTO_SCORE_PREMIUM = 2.0
    CRYPTO_CORRELATED_TICKERS = (
        'MARA', 'RIOT', 'COIN', 'MSTR', 'HIVE', 'BITF', 'HUT', 'CLSK',
        'GBTC', 'BITO', 'CORZ', 'CIFR', 'BTBT', 'SOS', 'CAN', 'WULF',
    )
    # The scanner scores 0-10; the decision ladder and every page read it out of
    # 100. The mapping is linear (LADDER.SCORE_BANDS = these tiers x this factor).
    TO_100 = 10.0


# ── Reward / risk ─────────────────────────────────────────────────────────────

class RR(_Group):
    SCANNER_MIN = 1.8               # scanner gate on its own net levels
    RESEARCH_MIN = 1.5              # Research engine / decision core hurdle
    OPTIMAL = 3.0


# ── Investment committee (scanner, before publication) ───────────────────────

class INSIDER(_Group):
    """Open-market insider activity: its read, score nudge and how far it may
    relax the committee hurdles (audit B-82). USD; other currencies convert."""
    WINDOW_DAYS = 90                    # look-back for purchases and sales
    STRONG_BUY_NET_USD = 5_000_000.0    # net buying for the "strong" read ...
    STRONG_BUY_MIN_BUYS = 2             # ... with at least this many purchases
    HEAVY_SELL_FLOOR_USD = 25_000_000.0  # heavy selling: at least this much ...
    HEAVY_SELL_MCAP_FRACTION = 0.0005   # ... or this share of market cap, if larger
    SCORE_ADJ_STRONG_BUY = 0.6          # score nudges (0-10 scale); buying ones
    SCORE_ADJ_NET_BUY = 0.3             # are scaled by the relaxation strength
    SCORE_ADJ_HEAVY_SELL = -0.8
    SCORE_ADJ_NET_SELL = -0.1
    # Relaxation strength (0-1) = size x breadth; full strength = the committee's
    # full bullish relaxation, so nothing is ever looser than that.
    RELAX_MIN_NET_USD = 250_000.0       # below this net buying: no relaxation
    RELAX_FULL_NET_USD = 5_000_000.0    # net buying for full size
    RELAX_FULL_BUYERS = 2               # distinct buying insiders for full breadth
    RSI_VETO_SKIP_MIN_STRENGTH = 1.0    # the RSI-extended veto is skipped only at this strength


class COMMITTEE(_Group):
    UPSIDE_HURDLE_PCT = 8.0
    UPSIDE_HURDLE_BULLISH_INSIDER_PCT = 6.0
    SCORE_FLOOR_BULLISH_RELAX = 0.3
    BUY_TIER_BULLISH_RELAX = 0.2
    ANALYST_DOWNSIDE_LIMIT_PCT = -5.0
    ANALYST_DOWNSIDE_LIMIT_BULLISH_PCT = -10.0
    RSI_EXTENDED = 75.0


# ── Market regime ────────────────────────────────────────────────────────────

class REGIME(_Group):
    VIX_LOW = 12.0                  # below: LOW volatility
    VIX_NORMAL_MAX = 20.0           # below: NORMAL
    VIX_HIGH_MAX = 30.0             # below: HIGH, else EXTREME
    VIX_VOLATILE = 25.0             # trend classifier: BULL needs VIX below, VOLATILE above
    # The book is a cash account: exposure is a fraction of capital and may
    # never exceed it. 1.2 here used to let a calm BULL tape propose 120% of
    # the user's capital (audit 2026-10-17 #3).
    EXPOSURE_MAX = 1.0
    EXPOSURE_LOW_VOL = 1.0
    EXPOSURE_NORMAL = 1.0
    EXPOSURE_HIGH = 0.7
    EXPOSURE_EXTREME = 0.5
    EXPOSURE_ERROR_FALLBACK = 0.8
    EXPOSURE_CAP_RANGING = 0.85
    EXPOSURE_CAP_VOLATILE = 0.70
    EXPOSURE_CAP_BEAR = 0.75
    EXPOSURE_CAP_UNKNOWN = 0.60
    # Recommended cash share by trend regime. ERROR is a failed detection: it
    # holds more cash than NEUTRAL, and is never overwritten by it.
    CASH_PCT = {
        "BULL": 0.15,
        "NEUTRAL": 0.25,
        "RANGING": 0.35,
        "ERROR": 0.35,
        "UNKNOWN": 0.40,
        "VOLATILE": 0.50,
        "BEAR": 0.60,
    }
    # Trend hysteresis: price must clear the 50-day average by this fraction
    # before the trend reads BULL or BEAR, so one close a hair either side of
    # the average cannot flip every weight and threshold in the scan.
    TREND_BAND_PCT = 0.01
    # (STRONG_BUY, BUY) scanner-score bars by regime. None = use SCORE.GOOD/EXCELLENT.
    SIGNAL_THRESHOLDS = {
        "BEAR": (9.0, 8.0),
        "VOLATILE": (8.7, 7.7),
        "RANGING": (8.5, None),
    }
    # NEUTRAL / unknown regime: VIX fallback bars.
    NEUTRAL_VIX_CALM = 15.0
    NEUTRAL_CALM_THRESHOLDS = (8.0, 7.0)
    NEUTRAL_VIX_STRESSED = 30.0
    NEUTRAL_STRESSED_THRESHOLDS = (9.0, 8.0)
    RSI_OVERBOUGHT = 70.0
    RSI_OVERBOUGHT_GROWTH = 80.0
    OVERBOUGHT_HOLD_SCORE = 6.5     # above: HOLD and wait; below: AVOID
    VIX_SKIP = 25.0


# ── Market read (Overview and Discover) ──────────────────────────────────────
# Bands the market page uses to colour and label its read. The server's regime
# engine and the browser both take them from here, so the page never carries a
# number of its own.

class MARKET(_Group):
    # Discover's analyst list: a consensus target counts only with enough analysts
    # behind it and an upside inside a believable band. Wider upside is usually a
    # stale or extreme target, which ranking by upside alone would put first.
    ANALYST_PICK_MIN_ANALYSTS = 10
    ANALYST_PICK_MIN_UPSIDE_PCT = 2.0
    ANALYST_PICK_MAX_UPSIDE_PCT = 50.0
    # Gross exposure bands (% of capital) for each Overview posture, and the caps
    # applied while confidence is low or data is stale. A Picks scan deploys no
    # more than the upper end of the band in force (owner decision 2026-10-19).
    # An untested heuristic, not a backtested allocation.
    EXPOSURE_BANDS = {
        "offensive": (65, 95),
        "lean_long": (50, 70),
        "neutral": (45, 65),
        "defensive": (20, 50),
        "cap_low_confidence": (30, 55),
        "cap_stale": (20, 45),
    }
    VIX_CONTAINED_MAX = 16.0        # below, with an aligned bullish model: risk Contained
    VIX_CALM_MAX = alias(REGIME, "VIX_NORMAL_MAX")   # at or above: elevated (warn)
    VIX_STRESS = alias(REGIME, "VIX_VOLATILE")       # at or above: stressed (bear), sizing reduced
    VIX_EXTREME = alias(REGIME, "VIX_HIGH_MAX")      # at or above: posture capped at Lean Defensive
    # India VIX runs on its own scale: the Volatility factor's calm / elevated /
    # stressed bands and the Macro factor's elevated-volatility flag.
    INDIA_VIX_CALM_MAX = 13.0       # below: calm (+1)
    INDIA_VIX_ELEVATED = 20.0       # at or above: elevated (-1)
    INDIA_VIX_STRESS = 24.0         # at or above: a Macro / Catalyst stress flag
    INDIA_VIX_EXTREME = 28.0        # at or above: stressed (-2)
    # Participation is four index proxies, so it only reads 0/25/50/75/100%.
    PARTICIPATION_BROAD_PCT = 70.0  # share of index proxies above their 50-DMA: broad
    PARTICIPATION_WEAK_PCT = 35.0   # at or below: weak
    PARTICIPATION_NARROW_PCT = 45.0 # below: narrow (Opportunity brief market type)
    PARTICIPATION_NEUTRAL_PCT = 50.0  # below (and above weak): below neutral (-1)
    SESSION_GREEN_UP_PCT = 65.0     # legacy tone fallback: share of quotes up today at or above: +1
    SESSION_GREEN_DOWN_PCT = 35.0   # at or below: -1
    CONFIDENCE_HIGH = 70            # regime confidence at or above: High
    CONFIDENCE_MODERATE = 50        # at or above: Moderate; below: Low
    # Regime confidence (0-100) = signal strength + input coverage - conflicts,
    # clamped, then lowered by the penalties below (never under the floor).
    CONFIDENCE_SIGNAL_POINTS = 60   # points for |factor sum| / factor count
    CONFIDENCE_SIGNAL_MIN_FACTORS = 8  # that ratio's denominator is at least this
    CONFIDENCE_COVERAGE_POINTS = 30  # points for full measured-input coverage
    CONFIDENCE_CONFLICT_POINTS = 5  # points off per supportive/negative factor pair
    CONFIDENCE_MIN = 15             # clamp of the computed score
    CONFIDENCE_MAX = 95
    CONFIDENCE_FLOOR = 12           # penalties never take confidence below this
    CONFIDENCE_NEWS_BEARISH_PENALTY = 8   # one or more high-impact bearish headlines
    CONFIDENCE_NEWS_CONFLICT_PENALTY = 12  # price tape and news direction disagree
    CONFIDENCE_MISSING_PENALTY = 15  # this many market inputs missing (below) or more
    DEGRADED_MISSING_INPUTS = 3     # missing market inputs at or above: penalty and degraded data
    CONFIDENCE_STALE_CAP = 35       # stale quotes: confidence at most this
    # News Impact factor score is clipped to +/- this. The tape is read by a
    # keyword lexicon, so it never carries a measured factor's -2, and it never
    # caps the posture on its own.
    NEWS_FACTOR_MAX_ABS = 1
    SIGNAL_SCORE_BULL = 6.5         # factor score (0-10) at or above: bullish tone
    SIGNAL_SCORE_NEUTRAL = 5.5      # at or above: neutral; below: warn
    SIGNAL_SCORE_BEAR = 3.5         # at or below: bearish tone
    NEWS_IMPACT_HIGH = 8            # headline impact score at or above: High
    NEWS_IMPACT_MARKET_MOVING = 6   # at or above: market-moving (shown on the tape)
    NEWS_IMPACT_MEDIUM = 3          # at or above: Medium relevance
    HIGH_IMPACT_NEWS_WARN = 3       # this many live high-impact headlines or more: warn
    VIX_SESSION_JUMP_PCT = 10.0     # a one-session VIX move this large is named beside its level (wording only)
    # Rates / Credit factor and the Macro "rate shock" flag: a yield's day move in
    # basis points (yield points x 100), never a percent of the yield's level
    # (4.10 -> 4.13 is +0.7% of the level but only 3 bp).
    YIELD_SHARP_BP = 10.0           # 10Y yield up this much or more: tightening sharply; either way: rate shock
    YIELD_RISING_BP = 5.0           # up this much or more: tightening, rate pressure
    YIELD_EASING_BP = -5.0          # at or below: easing, relief
    OIL_MOVE_PCT = 2.0              # oil fund move beyond +/- this: spiking / falling (also a Macro stress flag)
    GOLD_MOVE_PCT = 1.0             # gold fund move beyond +/- this: haven bid / risk appetite (gain: a Macro stress flag)
    CRYPTO_MOVE_PCT = 2.0           # bitcoin move beyond +/- this: risk appetite / risk-off
    # USD/INR (India's rates proxy) is a currency: its day move stays in percent.
    FX_MOVE_PCT = 0.3               # USD/INR move beyond +/- this: rupee weaker / firmer (factor -1 / +1)
    FX_SHARP_PCT = 0.6              # up this much or more: rupee weakening sharply (-2); either way: FX shock
    INDEX_LEADING_PCT = 1.0         # index move beyond +/- this: leading / under pressure
    INDEX_FLAT_PCT = 0.1            # index move within +/- this: flat
    INDEX_TONE_PCT = 0.4            # legacy v1 tone: benchmark move beyond +/- this: risk-on / risk-off
    # Discover movers: a move this large, an RSI this high or a stretch this far
    # above the 20-day average reads Too Extended.
    EXTENDED_MOVE_PCT = 15.0
    EXTENDED_RSI = 72.0
    EXTENDED_FROM_20DMA = 0.12
    # Impact chain: which narrative the tape supports, and when it counts as confirmed.
    CHAIN_OIL_SHOCK_PCT = 2.0       # oil fund day move at or above: energy shock
    CHAIN_VIX_REGIME = 22.0         # VIX at or above: volatility regime
    CHAIN_YIELD_JUMP_PCT = 0.5      # 10Y yield day move at or above: rates pressure
    CHAIN_LEADERSHIP_SPREAD_PCT = 0.3  # Nasdaq vs S&P gap below: no clear leadership
    CHAIN_CONFIRM_SHARE = 0.75      # share of links the tape must corroborate to say Confirmed
    # Overview opportunities: an analyst-target gap this wide is shown with a
    # "check" note (a stale target or a price that fell away from it), not as a plain upside.
    TARGET_GAP_CHECK_PCT = 50.0
    # The market read fetches index quotes and 1-year index histories (50/200-DMA
    # trend, participation, momentum) in parallel, each wave with its own budget.
    QUOTE_BUDGET_SECONDS = 12.0     # wall-clock wait for the index quotes
    TREND_BUDGET_SECONDS = 12.0     # wall-clock wait for the histories (same start time)
    TREND_CACHE_SECONDS = 1800.0    # reuse a symbol's trend metrics this long (daily bars)
    # Benchmark and participation proxies per market for the 50/200-DMA read.
    TREND_BASKETS = {
        "US": {"bench": "SPY", "breadth": ("SPY", "QQQ", "DIA", "IWM")},
        "IN": {"bench": "^NSEI", "breadth": ("^NSEI", "^BSESN", "^NSEBANK", "^CNXIT")},
    }
    # Overview market context (/api/market-context): sector funds, cross-asset
    # gauges and index futures per market, each as (symbol, label).
    SECTOR_BASKETS = {
        "US": (("XLK", "Technology"), ("XLC", "Communication"), ("XLY", "Consumer disc."),
               ("XLF", "Financials"), ("XLI", "Industrials"), ("XLV", "Health care"),
               ("XLE", "Energy"), ("XLB", "Materials"), ("XLP", "Staples"),
               ("XLU", "Utilities"), ("XLRE", "Real estate")),
        "IN": (("^CNXIT", "IT"), ("^NSEBANK", "Bank"), ("^CNXAUTO", "Auto"),
               ("^CNXPHARMA", "Pharma"), ("^CNXFMCG", "FMCG"), ("^CNXMETAL", "Metal"),
               ("^CNXENERGY", "Energy"), ("^CNXREALTY", "Realty"), ("^CNXMEDIA", "Media"),
               ("^CNXPSUBANK", "PSU bank"), ("^CNXINFRA", "Infra")),
    }
    # Heatmap tile size only (never a calculation input): approximate share of
    # the S&P 500 held by each sector fund's sector, reviewed quarterly. A
    # market without weights draws equal tiles.
    SECTOR_WEIGHTS = {
        "US": {"XLK": 33.0, "XLF": 13.0, "XLY": 10.5, "XLC": 9.5, "XLV": 9.0, "XLI": 8.5,
               "XLP": 5.0, "XLE": 3.0, "XLU": 2.5, "XLRE": 2.0, "XLB": 2.0},
        "IN": {},
    }
    PULSE_SERIES_SESSIONS = 22      # daily closes behind each pulse sparkline (about one month)
    BASE_RATE_YEARS = 10            # history behind the next-session base rate
    BASE_RATE_MOVE_PCT = 1.0        # a session this far up or down is a "1% day" in the base rate
    CROSS_ASSET = {
        "US": (("DX-Y.NYB", "Dollar index"), ("^IRX", "3-month T-bill"), ("^TNX", "10Y yield"),
               ("HYG", "High-yield bonds"), ("LQD", "Investment-grade bonds"),
               ("HG=F", "Copper"), ("GC=F", "Gold")),
        "IN": (("USDINR=X", "USD/INR"), ("BZ=F", "Brent crude"), ("GC=F", "Gold"), ("^TNX", "US 10Y yield")),
    }
    FUTURES = {
        "US": (("ES=F", "S&P 500"), ("NQ=F", "Nasdaq 100"), ("YM=F", "Dow"), ("RTY=F", "Russell 2000")),
        "IN": (),
    }
    VOL_INDEX = {"US": "^VIX", "IN": "^INDIAVIX"}
    CONTEXT_BUDGET_SECONDS = 20.0   # wall-clock wait for the market-context quotes
    CONTEXT_CACHE_SECONDS = 300.0   # reuse a market's context this long
    CONTEXT_DISK_MAX_SECONDS = 259200.0  # a saved last good read (kept on disk) serves this long, so a weekend restart still has sparklines
    TRADING_DAYS_PER_YEAR = 252     # annualised implied volatility -> one session
    ATR_SESSIONS = 14               # average true range window for key levels
    CALENDAR_DAYS_AHEAD = 7         # dated events shown on the Overview calendar
    SECTOR_HEAT_FULL_PCT = 2.0      # a sector move this large takes the fullest heat colour
    REGIME_HISTORY_SESSIONS = 30    # sessions shown on the regime history strip
    # Earnings calendar (Yahoo's own, US): companies at or above this market cap,
    # up to this many rows, reused this long. It completes the intel screen's list.
    EARNINGS_MIN_MARKET_CAP = 10_000_000_000.0
    EARNINGS_CALENDAR_LIMIT = 100
    EARNINGS_CALENDAR_CACHE_SECONDS = 1800.0
    EARNINGS_PER_DAY_SHOWN = 3      # reports listed per calendar day before "+N more"
    # Overview regime engine (app.py _build_market_regime_engine): each factor
    # scores +1 / 0 / -1 / -2 against these bands on its own input.
    # Trend factor fallback when the benchmark history is unavailable (one session's move, %).
    TREND_FALLBACK_UP_PCT = 0.75        # at or above: +1
    TREND_FALLBACK_DOWN_PCT = -1.0      # at or below: -2
    TREND_FALLBACK_WEAK_PCT = -0.25     # below (and above DOWN): -1
    # Session Move factor: the benchmark's one-session move (%).
    SESSION_MOVE_UP_PCT = 0.7           # at or above: +1
    SESSION_MOVE_DOWN_PCT = -1.2        # at or below: -2
    SESSION_MOVE_WEAK_PCT = -0.35       # at or below: -1
    # Momentum factor: the benchmark's 21-session total return (%).
    MOMENTUM_UP_PCT = 3.0               # at or above: +1
    MOMENTUM_DOWN_PCT = -6.0            # at or below: -2
    MOMENTUM_WEAK_PCT = -2.0            # at or below: -1
    CATALYST_HEAVY_REPORTS = 4          # Catalyst Risk: earnings reports in the next 7 days at or above: heavy week (-1)
    # Posture from the factor-score sum, symmetric (at or above +N / at or below -N).
    POSTURE_OFFENSIVE_SUM = 7           # Offensive / Defensive
    POSTURE_SELECTIVE_SUM = 4           # Selective Offensive / Selective Defensive; also a bullish / bearish price tape for the news-conflict check
    POSTURE_LEAN_SUM = 2                # Lean Long / Lean Defensive
    RULE2_QQQ_DROP_PCT = -1.0           # Rule 2: QQQ below this with VIX rising caps the posture at Neutral
    OFFENSIVE_MIN_CONFIRMED = 5         # Rule 4: full Offensive needs at least this many +1 factors
    CONFLICT_REASON_MIN = 2             # supportive/negative factor pairs at or above: named as a confidence reason
    CONFLICT_HIGH = 3                   # at or above: a "conflicted" read (guidance capped) and a risk warning
    LEGACY_TONE_TREND_PCT = 0.5         # 4-factor tone, trend fallback: benchmark move at or beyond +/- this: +1 / -1
    LEGACY_TONE_NET_SUM = 2             # 4-factor tone: factor sum at or beyond +/- this: Risk-on / Risk-off
    NEWS_HALF_LIFE_DAYS = 4.0           # a headline's impact halves every this many days in the regime read
    NEWS_DRIVER_MEDIUM = 4              # regime drivers / catalysts: impact at or above reads Medium (High is NEWS_IMPACT_MARKET_MOVING)


# ── Book construction (get_top_recommendations) ──────────────────────────────

class BOOK(_Group):
    BANNED_GRADES = ("F",)          # G2. Owner decision: grade D is allowed at half size
                                    # (SIZING.RISK_GRADE_SIZE_MULTIPLIERS["D"])
    SMALL_POOL_FOR_SCORE_FLOOR = 8  # pool at or below: score floor = SCORE.ACCUMULATE
    LARGE_POOL_FOR_CONVICTION = 20  # pool at or above: conviction floor 3
    CONVICTION_FLOOR_LARGE = 3
    CONVICTION_FLOOR_SMALL = 2
    COST_VIABILITY_MULTIPLE = 2.0   # expected return must cover N x round trip
    DEFAULT_ROUND_TRIP_BPS = 15.0
    MAX_SECTOR_PCT = 0.25
    SECTOR_CAP_PROFILE = "balanced"
    SECTOR_CAP_PROFILE_PCT = {"strict": 0.25, "balanced": 0.35, "aggressive": 0.40}
    MAX_WATCH_ROWS = 10             # watch / withdrawn rows published beside funded ones
    CLOSEST_ROWS = 5                # "closest to qualifying" names shown after a scan (A12)
    MAX_PAIRWISE_CORRELATION = 0.70  # a candidate this correlated with one book name is blocked
    MAX_AVERAGE_CORRELATION = 0.60  # ... or this correlated with the book on average


# ── Sizing ───────────────────────────────────────────────────────────────────

class SIZING(_Group):
    TARGET_RISK_PER_TRADE = 0.006
    TARGET_RISK_STRONG_BUY = 0.0075
    MAX_RISK_PER_TRADE = 0.02
    MAX_RISK_STRONG_BUY = 0.025
    ONE_SHARE_MAX_RISK = 0.01       # one whole share may risk at most this share of capital (owner, 2026-10-19)
    MAX_PORTFOLIO_RISK = 0.10
    MIN_POSITION_SIZE = 100.0       # account currency (USD); INR via ACCOUNT_TO_USD
    MIN_POSITION_PCT = 0.02         # capital-relative floor (audit 5.5)
    MIN_POSITION_FLOOR = 25.0       # USD: the minimum never falls below this (audit A06)
    MAX_POSITION_PCT = 0.10
    SMALL_ACCOUNT_THRESHOLD = 5_000.0
    SMALL_ACCOUNT_MAX_POSITION_PCT = 0.25
    SMALL_ACCOUNT_SCALE_FLOOR = 500.0  # capital at which the scaled cap reaches its ceiling
    POSITION_CAP_BUFFER = 0.01
    RISK_GRADE_SIZE_MULTIPLIERS = {"A": 1.00, "B": 1.00, "C": 0.80, "D": 0.50, "F": 0.00}
    UNKNOWN_GRADE_SIZE_MULTIPLIER = 0.50
    # Risk budget = capital x TARGET_RISK x signal mult x score mult (audit 5.5)
    SIGNAL_SIZE_MULT = {"STRONG_BUY": 1.00, "BUY": 0.80, "ACCUMULATE": 0.60}
    OTHER_SIGNAL_SIZE_MULT = 0.50
    # score mult = BASE + score/10 x SPAN. Flat (1.0, 0.0) while the evidence
    # registry reports elite_pro.composite as unvalidated: sizing by a score
    # with no demonstrated edge adds risk without adding expected return
    # (audit 2026-10-17 #15). Was 0.7 / 0.5; restore once the ledger shows the
    # score is calibrated. The call tier (SIGNAL_SIZE_MULT) still applies.
    SCORE_MULT_BASE = 1.0
    SCORE_MULT_SPAN = 0.0
    DEEP_ACCUMULATE_SCALE = 0.5     # Research ACCUMULATE on a scanner BUY
    EARNINGS_IMMINENT_SIZE_MULT = 0.25
    EARNINGS_NEAR_SIZE_MULT = 0.50
    EARNINGS_ELEVATED_SIZE_MULT = 0.80
    EARNINGS_UNKNOWN_SIZE_MULT = 0.70
    # Days to the next report for each band (inclusive): within IMMINENT_DAYS the
    # IMMINENT multiplier applies, within NEAR_DAYS the NEAR one, within
    # ELEVATED_DAYS the ELEVATED one; further out, full size (P7).
    EARNINGS_IMMINENT_DAYS = 3
    EARNINGS_NEAR_DAYS = 14
    EARNINGS_ELEVATED_DAYS = 30
    # FOMC event risk (scanner _get_event_risk_factor): new positions shrink
    # ahead of a Fed decision. Days to the decision day (New York date), both
    # ends inclusive: 0 to FOMC_NEAR_DAYS takes the NEAR multiplier, the days
    # after that up to FOMC_WEEK_DAYS take the WEEK one; further out, full size.
    FOMC_NEAR_DAYS = 3
    FOMC_NEAR_SIZE_MULT = 0.70
    FOMC_WEEK_DAYS = 7
    FOMC_WEEK_SIZE_MULT = 0.85
    # Client mandate: a multiplier on funded allocations only. The engine's
    # risk budget is the ceiling, so no mandate scales above 1.0. Audit F14:
    # "aggressive" (x1.00, identical to balanced) is no longer offered; a
    # legacy request is sized as the alias below and the response says so.
    MANDATE_SCALE = {"conservative": 0.70, "balanced": 1.00}
    MANDATE_LEGACY_ALIASES = {"aggressive": "balanced"}
    MANDATE_DEFAULT = "balanced"


# ── Scanner transaction costs (per side, by market-cap tier) ─────────────────

class COSTS(_Group):
    MEGA_CAP_MIN = 200_000_000_000.0
    LARGE_CAP_MIN = 10_000_000_000.0
    MID_CAP_MIN = 2_000_000_000.0
    COMMISSION_MEGA_CAP = 0.0005
    SLIPPAGE_MEGA_CAP = 0.0003
    COMMISSION_LARGE_CAP = 0.001
    SLIPPAGE_LARGE_CAP = 0.0005
    COMMISSION_MID_CAP = 0.0015
    SLIPPAGE_MID_CAP = 0.001
    COMMISSION_SMALL_CAP = 0.003
    SLIPPAGE_SMALL_CAP = 0.002
    # NSE/BSE delivery equity levies, per side (about 24bps round trip):
    # STT 0.1% on buy and on sell; stamp duty 0.015% on buy; exchange
    # transaction charge about 0.003% and SEBI fee 0.0001% each side; 18% GST
    # on brokerage and exchange charges; a flat DP charge on the sell.
    INDIA_STATUTORY_COST_PER_SIDE = 0.0012


# ── Levels (entry zone, stop, target) ────────────────────────────────────────

class LEVELS(_Group):
    STOP_ATR_MULTIPLIER = 2.75
    TARGET_ATR_MULTIPLIER = 5.5
    MIN_STOP_DISTANCE_PCT = 0.03
    MAX_STOP_DISTANCE_PCT = 0.20
    TRAILING_ATR_MULTIPLIER = 1.75
    ENTRY_ZONE_ATR_MULT = 1.0       # zone = [entry - k x ATR, entry]
    TARGET_STRUCT_MAX_AGE_SESSIONS = 63  # a 52-week high older than this is not a target
    STRUCT_MIN_ABOVE_PCT = 0.02     # a structural level counts only this far above spot
    ANALYST_TARGET_SANE_RANGE = (0.5, 3.0)   # consensus outside price x this range is ignored
    # A consensus target nearer price x the ratio of a split made within this many
    # days than price itself was set before the split: it is stale, not upside.
    ANALYST_TARGET_SPLIT_LOOKBACK_DAYS = 365
    TARGET_SAFETY_CAP = {"BULL": 2.50, "VOLATILE": 2.20, "OTHER": 2.00}   # x price, circuit breaker
    RESEARCH_HORIZON_DAYS = 63      # the Research engine's Decision horizon
    VALUATION_HORIZON_DAYS = 252    # its 12-month valuation target
    RESEARCH_SPREAD_BPS = 5         # Research engine cost model (per side)
    RESEARCH_SLIPPAGE_BPS = 3
    # US round trip = spread + 2 x slippage (11bps). An .NS / .BO listing adds the
    # statutory levies on each side (COSTS.INDIA_STATUTORY_COST_PER_SIDE, about 35bps in all).
    RESEARCH_INDIA_STATUTORY_PER_SIDE = alias(COSTS, "INDIA_STATUTORY_COST_PER_SIDE")


# ── Risk grade ───────────────────────────────────────────────────────────────

class RISK_GRADE(_Group):
    BASELINE_POINTS = 3
    MAX_POINTS = 10                          # baseline + drivers is capped here
    VOL_BANDS = (0.25, 0.35, 0.50)          # +1 / +2 / +3 above each
    BETA_BANDS = (1.2, 1.5)                  # +1 / +2 above each
    DRAWDOWN_BANDS = (-0.25, -0.40)          # +1 / +2 beyond each
    TAIL_BANDS = (50.0, 70.0)                # +1 / +2 above each (0-100 score)
    LIQUIDITY_FLOOR = 5.0                    # +1 below (0-10 score)
    GRADE_CUTOFFS = ((3, "A"), (5, "B"), (7, "C"), (8, "D"))   # else F
    LOOKBACK_DAYS = 504                      # one window for both engines (audit 5.3)
    FETCH_PERIOD = "2y"                      # the scanner's daily fetch, so the grade sees LOOKBACK_DAYS
    BETA_LOOKBACK_DAYS = 252                 # beta vs the market benchmark on the last year of sessions
    BETA_MIN_OBS = 30                        # fewer common sessions: beta not measured (neutral 1.0)
    MIN_RETURNS = 30                         # fewer daily returns: no grade from history
    SPLIT_ARTIFACT_RETURN = 0.50             # |1-day return| at or above: unadjusted split, dropped
    TAIL_WINSOR_PCT = 0.01                   # skew/kurtosis on 1%/99%-winsorized returns
    TAIL_BASE = 50.0                         # tail score before skew / kurtosis steps (0-100)
    TAIL_SKEW_STEPS = ((-0.5, 20.0), (0.0, 10.0))    # skew below -> add (first match)
    TAIL_KURT_STEPS = ((5.0, 20.0), (3.0, 10.0))     # excess kurtosis above -> add (first match)
    LIQUIDITY_AVG_DAYS = 20                  # average daily volume window for dollar volume
    # Dollar-volume liquidity bands -> (score 0-10, est. spread)
    LIQUIDITY_BANDS = ((50_000_000.0, 10.0, 0.0002), (10_000_000.0, 8.0, 0.0005),
                       (1_000_000.0, 6.0, 0.001))
    LIQUIDITY_FLOOR_SCORE = (4.0, 0.002)


# ── Decision ladder (0-100 composite; audit section 6) ───────────────────────

class LADDER(_Group):
    SCORE_BANDS = (
        (75.0, "STRONG_BUY"),
        (65.0, "BUY"),
        (55.0, "ACCUMULATE"),
        (45.0, "HOLD"),
        (35.0, "REDUCE"),
        (25.0, "SELL"),
    )
    MIN_SCORE_FOR_BUY = 55.0        # DecisionInputs default (= ACCUMULATE band)
    DCF_OVERVALUATION_CAP_PCT = 25.0
    ANALYST_DOWNSIDE_TRIM_PCT = 25.0  # scanner: consensus this far below entry trims one notch
    STRONG_BUY_EVIDENCE = "VALIDATED"  # owner decision Q4: below this, STRONG_BUY folds into BUY


# ── Research engine overrides (TradingAnalysis_ELITE) ────────────────────────

class RESEARCH(_Group):
    MIN_RISK_REWARD = alias(RR, "RESEARCH_MIN")
    TARGET_UPSIDE_THIN_PCT = 5.0        # base-target upside below this trims one notch
    ANALYST_DOWNSIDE_LIMIT_PCT = -5.0   # consensus below this vs price -> HOLD
    RSI_EXHAUSTION = 85.0               # with a bearish MACD -> no fresh BUY
    RSI_CAPITULATION = 15.0             # with a bullish MACD -> no fresh SELL
    STRONG_CONFIRMATIONS = 3            # STRONG_BUY / STRONG_SELL need this many
    MODE = "full"                       # F7: one research mode for deep confirmation and My Portfolio
    TARGET_AGREEMENT_STRONG_PCT = 70.0  # target sources agree this closely or better: strong
    TARGET_AGREEMENT_WEAK_PCT = 40.0    # at or below: weak agreement
    # Research "Signal conflict" card: a 0-100 score this high beside a Hold, Reduce
    # or Sell (or this high beside a Hold on a grade D/F name) is flagged.
    CONFLICT_SCORE = 65.0
    CONFLICT_SCORE_HIGH_RISK = 60.0
    SCORE_TONE_BULL = 60.0              # score at or above reads "suggests upside"
    SCORE_TONE_BEAR = 40.0              # at or below reads "suggests caution"
    PEER_VALUATION_GAP_PCT = 5.0        # premium beyond +/- this vs sector average: rich / cheap
    # Trade setup entry: RSI above this is stretched. Reward/risk and size are
    # still measured from spot; a limit this % below spot is offered as a better fill.
    ENTRY_RSI_EXTENDED = 60.0
    PULLBACK_ENTRY_PCT = 1.0
    # NSE/BSE regular session close (hour, minute IST): before it, today's daily
    # bar is still forming. The US close is engine.market_calendar.SESSION_CLOSE.
    INDIA_SESSION_CLOSE_IST = (15, 30)


# ── My Portfolio policy (portfolio.analytics.PortfolioPolicy defaults) ──────

class PORTFOLIO(_Group):
    MAX_SINGLE_POSITION = 0.10      # no new money at or above this weight
    TRIM_REVIEW_POSITION = 0.20     # a HOLD this large is sent to trim review
    MAX_SECTOR = 0.30               # no new money into a sector at or above this
    CORRELATION_THRESHOLD = 0.70
    EARNINGS_WINDOW_DAYS = 7
    STOP_RISK_BUDGET = 0.06
    MIN_ADD_VALUE = 50.0
    OUTCOME_MIN_SAMPLE = 20         # completed outcomes needed before a hit rate is drawn
    OUTCOME_HORIZON_DAYS = 63       # trading days after a call before its outcome counts
    JOURNAL_RECENT_DAYS = 45        # Review lists call changes from this many calendar days
    DRAWDOWN_COMPARE_MARGIN = 0.02  # deepest-fall gap vs the benchmark worth calling out


# ── Chase alerts (audit A07) ─────────────────────────────────────────────────

class CHASE(_Group):
    # A name is flagged when ANY of these fires (strictly above the threshold):
    # an opening gap on either of the last GAP_LOOKBACK_BARS bars, or a close-to-
    # close run over 5 or 10 sessions. The thresholds are a per-stock rule, never
    # the scan type's (P6): the wider GROWTH_ values (and GROWTH_SCORE_PENALTY)
    # apply only to a stock whose own annualised volatility over the last
    # VOL_LOOKBACK_BARS sessions is at least WIDE_THRESHOLD_MIN_VOL. None (the
    # default) gives every stock the standard thresholds in every universe.
    GAP_PCT = 0.03
    MOVE_5D_PCT = 0.10
    MOVE_10D_PCT = 0.20
    GROWTH_GAP_PCT = 0.05
    GROWTH_MOVE_5D_PCT = 0.15
    GROWTH_MOVE_10D_PCT = 0.30
    WIDE_THRESHOLD_MIN_VOL = None   # owner decision: e.g. 0.35 (2026-10-04 cache medians: 0.39 growth sectors, 0.25 others)
    VOL_LOOKBACK_BARS = 60
    GAP_LOOKBACK_BARS = 2
    # A gap is filled (informational, no penalty) when the latest close is at or
    # below the pre-gap close x (1 + this). 0 = back to the pre-gap close itself.
    GAP_FILL_TOLERANCE = 0.0
    MIN_BARS = 10
    SCORE_PENALTY = 1.5             # points off the /10 score when flagged
    GROWTH_SCORE_PENALTY = 0.75
    # Early-momentum watchlist and the pre-momentum scan (classifier of 6 checks).
    EARLY_MOMENTUM_MIN_SIGNALS = 4
    EARLY_MOMENTUM_MAX_ROWS = 10
    EARLY_MOMENTUM_SCAN_ROWS = 120  # top-ranked analysed names the watchlist reads
    PREMOMENTUM_MIN_SIGNALS = 3
    PREMOMENTUM_MAX_1M = 0.20       # pre-momentum: 1-month move must stay under this


# ── Corporate events (audit F1) ──────────────────────────────────────────────

class EVENTS(_Group):
    # Deals can stay pending well past a year (antitrust reviews), so a signed deal
    # is looked for over two years; it ends with a termination 8-K or a delisting.
    LOOKBACK_DAYS = 730
    # A heavy filer's EDGAR "recent" list can cover less than the window; older
    # filing pages are read until the window is covered, up to this many, and a list
    # still short of it is "unavailable". Measured 2026-10-05 for the 730-day window:
    # JPM needs 11 pages, MS 9, C 6, GS and BAC 5 (about 0.4 MB each).
    FILING_PAGES_MAX = 16
    # Older pages never change: up to this many are kept in memory across scans.
    FILING_PAGE_CACHE_PAGES = 512
    # Target-side forms only: SC TO-T is left out because it also appears in the
    # bidder's own filings; the target always answers a tender with SC 14D9.
    # SC 14D9F is the same answer from a Canadian (MJDS) target.
    STRONG_FORMS = ("DEFM14A", "PREM14A", "SC 14D9", "SC 14D9F")
    DELISTING_FORMS = ("25-NSE", "15-12B")
    # A foreign private issuer files no 8-K or merger proxy: it announces a deal in
    # a free-form 6-K, which the check does not classify. A 6-K in the window makes
    # the check "unavailable", never "clear". (F-4 and SC TO-T are the buyer's.)
    FOREIGN_CURRENT_FORMS = ("6-K",)
    # A successor issuer (holding-company reorganization) files these; the 25-NSE
    # that follows removes the predecessor's shares, not the listing.
    SUCCESSION_FORMS = ("8-K12B", "8-K12G3")
    MERGER_8K_ITEM = "1.01"
    TERMINATION_8K_ITEM = "1.02"
    VOL_COLLAPSE_RATIO = 0.35       # 20d ATR% / 120d ATR% below this: possible pending deal
    ARB_SPREAD_MIN = 0.02           # holders: Sell when spread to deal price is below this
    DOC_FETCH_MAX_BYTES = 3_000_000
    DOC_CACHE_SECONDS = 24 * 3600
    DOC_CACHE_MAX_BYTES = 64_000_000    # filing documents kept in memory, least recently used dropped first
    # The gate waits at most this long for the scan-start warm-up, then checks the
    # names itself (A03); an unanswered name is "unavailable" and fails closed.
    WARMUP_JOIN_SECONDS = 60.0
    # Listings no event source covers (owner decision: fail closed, never funded).
    UNCOVERED_LISTINGS = {".NS": "NSE", ".BO": "BSE"}


# ── Track record / forward evidence (tracking/ledger.py) ─────────────────────

class EVIDENCE(_Group):
    MIN_SAMPLE = 30                 # resolved calls before any result is shown
    MIN_DATES = 10                  # ... spread over at least this many dates


# ── Page (header clocks, panels) ─────────────────────────────────────────────

class UI(_Group):
    HEADER_AGING_MINUTES = 5
    HEADER_STALE_MINUTES = 10
    NEAR_MISS_N = 10
    NEAR_MISS_SHOWN = 5
    RR_DECIMALS = 2
    COMPACT_LIST_AFTER = 20          # Picks: longer result lists render compact rows first (A12)
    NAME_LIST_SHOWN = 5              # Picks: ticker lists show this many, the rest behind Show all
    # Research Signals card: bands that colour each technical reading. RSI's
    # overbought line is REGIME.RSI_OVERBOUGHT; these are the display-only rest.
    TECH_RSI_OVERSOLD = 30.0         # RSI at or below: oversold (green: a rebound setup)
    TECH_ADX_TREND = 25.0            # ADX at or above: a trend is in force
    TECH_ADX_WEAK = 20.0             # ADX below: no clear trend
    TECH_VOLUME_HEAVY = 1.5          # volume vs average at or above: heavy
    TECH_VOLUME_LIGHT = 0.7          # ... at or below: light
    TECH_NEAR_LEVEL_PCT = 2.0        # price within this % of support/resistance: near it
    # Research search: example listings shown per index when the field is empty,
    # each as [ticker, company]. Examples to start from, not recommendations.
    RESEARCH_SUGGESTIONS = {
        "US": [
            ["S&P 500", [["AAPL", "Apple"], ["MSFT", "Microsoft"], ["NVDA", "NVIDIA"]]],
            ["Nasdaq-100", [["GOOGL", "Alphabet"], ["AMZN", "Amazon"], ["META", "Meta Platforms"]]],
            ["Dow", [["JPM", "JPMorgan Chase"], ["WMT", "Walmart"], ["KO", "Coca-Cola"]]],
        ],
        "IN": [
            ["Nifty 50", [["RELIANCE.NS", "Reliance Industries"], ["TCS.NS", "Tata Consultancy Services"], ["HDFCBANK.NS", "HDFC Bank"]]],
            ["Sensex", [["INFY.BO", "Infosys"], ["ICICIBANK.BO", "ICICI Bank"], ["ITC.BO", "ITC"]]],
            ["Nifty Bank", [["SBIN.NS", "State Bank of India"], ["KOTAKBANK.NS", "Kotak Mahindra Bank"], ["AXISBANK.NS", "Axis Bank"]]],
        ],
    }


# ── Manual execution checklist (P7) ───────────────────────────────────────────
# Shown on the Picks pre-trade review as conditions to check by hand when placing
# an order. QUANTA does not evaluate them.

class CHECKLIST(_Group):
    PREMARKET_GAP_PCT = 0.02        # a pre-market gap past this resets the planned entry
    SPY_OPEN_DROP_PCT = 0.01        # SPY down more than this at the open: wait
    SECTOR_ETF_DROP_PCT = 0.01      # the name's sector ETF down more than this: wait


# ── Style scans (Growth / Value / Dividend) ──────────────────────────────────
# A style scan only rates names that pass its screen, read from the same
# company data every scan fetches. Ratios are fractions (0.10 = 10%).

class STYLE(_Group):
    # Growth Leaders: revenue growth at or above the floor, plus at least one
    # of earnings growth at or above its floor or a forward EPS above trailing
    # (forward P/E below trailing P/E).
    GROWTH_MIN_REVENUE_GROWTH = 0.10
    GROWTH_MIN_EARNINGS_GROWTH = 0.10
    # Value Compounders: profitable (positive earnings and free cash flow) and
    # cheap on at least VALUE_MIN_PASSES of the four measures below.
    VALUE_MAX_FORWARD_PE = 16.0
    VALUE_MAX_EV_EBITDA = 11.0
    VALUE_MIN_FCF_YIELD = 0.05
    VALUE_MAX_PB = 2.5
    VALUE_MIN_PASSES = 2
    # Dividend Aristocrats: on the S&P 500 Dividend Aristocrats list (25+ years
    # of increases), paying now, and a payout ratio at or under the cap
    # (earnings-based payout is not meaningful for REITs, so they skip it).
    DIVIDEND_MAX_PAYOUT = 1.0
    # A market with no dividend-growth list (India) asks for a real yield instead.
    DIVIDEND_MIN_YIELD_NO_LIST = 0.015
    # Momentum Leaders: an established uptrend (price above the 50-day average,
    # which is above the 200-day) and a 3-month return of at least this much.
    MOMENTUM_MIN_RETURN_3M = 0.05
    MOMENTUM_SESSIONS_3M = 63


# ── ETF Sleeve ───────────────────────────────────────────────────────────────
# Funds have no earnings, margins or balance sheet, so an ETF is scored on its
# trend and momentum only (the regime's technical and momentum weights,
# rescaled to sum to one) and takes no earnings-date size cut.

class ETF(_Group):
    SCORED_FACTORS = ("technical", "momentum")


# ── "Why this stock was picked" (Picks table detail) ─────────────────────────

class WHY(_Group):
    STRONG_SUBSCORE = 6.5           # a sub-score (0-10) at or above this counts in its favour
    WEAK_SUBSCORE = 4.0             # ... at or below this counts against it
    ANALYST_MIN_COUNT = 3           # a consensus needs at least this many analysts
    EARNINGS_NEAR_DAYS = 14         # earnings this close is listed as a risk
    MAX_ITEMS = 5                   # reasons shown per side
    NEWS_ITEMS = 3                  # company headlines kept per name
    # A headline is the company's own news when it carries "(TICKER)" or names
    # the company or ticker within its first N words (its subject); a name
    # that only appears late in a list of companies is someone else's story.
    NEWS_SUBJECT_WORDS = 8
    RSI_OVERSOLD = 30.0             # RSI at or below this reads oversold on the gauge
    CHART_SESSIONS = 126            # sessions the Analysis price chart shows (about six months)
    COMPARE_MAX = 3                 # stocks the Analysis comparison holds at once
    REVENUE_GROWTH_NOTABLE = 0.10   # revenue growth at or above this is a reason for
    DIVIDEND_YIELD_NOTABLE = 0.02   # a yield at or above this is a reason for (outside the dividend scan)
    RSI_STRETCHED = 70.0            # RSI at or above this is listed against
    SURPRISE_MIN_PCT = 1.0          # average earnings surprise (percent) worth stating
    EARLY_MOMENTUM_MIN = 3          # early-momentum confirmations worth stating
    # Analysis cards: how many funded / watch picks get a full card, and the
    # risk gauge (volatility, beta and grade; never conviction).
    ANALYSIS_CARDS = 5
    GAUGE_VOL_HIGH = 0.45           # annualised volatility worth two gauge points
    GAUGE_VOL_ELEVATED = 0.30       # ... worth one
    GAUGE_BETA_GAP = 0.6            # |beta - 1| at or above this adds a point


# ── Saved scans (the Recent scans switcher) ─────────────────────────────────
# Every completed Picks scan is kept, one per distinct setting, so switching
# between Standard, Growth and ETF Sleeve does not mean running them again.

class SAVED_SCANS(_Group):
    RETENTION_HOURS = 24            # a saved scan can be reopened for this long
    MAX_SAVED = 12                  # newest kept per market; older ones are removed


GROUPS = (CAPITAL, UNIVERSE, CHASE, SCORE, RR, INSIDER, COMMITTEE, REGIME, MARKET, BOOK, SIZING, COSTS, LEVELS,
          RISK_GRADE, LADDER, RESEARCH, PORTFOLIO, EVENTS, EVIDENCE, UI, CHECKLIST, STYLE, ETF, WHY, SAVED_SCANS)


def as_dict() -> Dict[str, Dict[str, Any]]:
    """Every group's current values, JSON-safe (tuples become lists)."""
    def _jsonable(v: Any) -> Any:
        if isinstance(v, tuple):
            return [_jsonable(x) for x in v]
        if isinstance(v, dict):
            return {str(k): _jsonable(x) for k, x in v.items()}
        return v
    return {g.__name__: {k: _jsonable(v) for k, v in g.values().items()} for g in GROUPS}


def fmt_rr(value: float) -> str:
    """A reward/risk ratio as the page prints it, e.g. ``1.50:1``."""
    return f"{float(value):.{UI.RR_DECIMALS}f}:1"


if __name__ == "__main__":  # pragma: no cover - regenerates the JS test fixture
    # python -m engine.thresholds > ../webapp/tests/fixtures/thresholds.json   (run from core/)
    import json
    import sys
    json.dump(as_dict(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
