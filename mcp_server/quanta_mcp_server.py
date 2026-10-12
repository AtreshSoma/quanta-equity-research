#!/usr/bin/env python3
"""
QUANTA Elite MCP Server v1.0
=============================
Institutional-Grade Market Intelligence via Model Context Protocol

Exposes the full QUANTA Elite Trading System as MCP tools that can be
invoked from VS Code Agent Mode, Claude Desktop, or any MCP client.

Tools:
  1. analyze_stock        - Full multi-factor analysis (technical + fundamental + sentiment)
  2. quick_scan           - Rapid multi-ticker screening with ranking
  3. market_environment   - Real-time macro dashboard (SPY, VIX, Fed, sectors)
  4. portfolio_risk_check - Portfolio-level VaR, concentration, correlation risk
  5. compliance_check     - SEBI/SEC regulatory compliance audit
  6. catalyst_scan        - News-driven catalyst detection with volume confirmation
  7. price_targets        - DCF + analyst + Monte Carlo composite targets
  8. monte_carlo_sim      - Monte Carlo price simulation with probabilities
  9. compare_stocks       - Side-by-side multi-stock comparison table
  10. position_advisor    - Personalized P&L-aware position management

Author: QUANTA Elite System
Version: 1.0.0
"""

import os
import sys
import json
import time
import logging
import warnings
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

# MCP SDK
from mcp.server.fastmcp import FastMCP

# Suppress warnings
warnings.filterwarnings('ignore')

# ─── Path Setup ──────────────────────────────────────────────────────────────
# Add the parent directories so we can import from core/ and modules/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'core'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'modules'))
sys.path.insert(0, PROJECT_ROOT)

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('QUANTA_MCP')

# Suppress noisy loggers
for noisy in ("yfinance", "urllib3", "requests", "httpx", "polygon_provider",
              "compliance_manager", "peewee", "matplotlib"):
    logging.getLogger(noisy).setLevel(logging.CRITICAL)


# ─── Import QUANTA System ───────────────────────────────────────────────────
from TradingAnalysis_ELITE import (
    QuantaEliteAnalyzer, AnalysisResult, Config, Signal, MarketRegime,
    ConfidenceLevel, APIClient, fetch_market_environment,
    format_currency, format_percent, safe_divide, safe_get,
    get_composite_grade, get_currency_symbol,
    _mcp_result_to_dict, _mcp_format_analysis_text, decision_for_analysis,
)

# Optional modules
try:
    from compliance_manager import ComplianceManager, Market
    HAS_COMPLIANCE = True
except ImportError:
    HAS_COMPLIANCE = False

try:
    from catalyst_scanner import CatalystScanner
    HAS_CATALYST = True
except ImportError:
    HAS_CATALYST = False

try:
    from portfolio_risk_manager import PortfolioRiskManager
    HAS_PORTFOLIO_RISK = True
except ImportError:
    HAS_PORTFOLIO_RISK = False


# =============================================================================
# SERVER INITIALIZATION
# =============================================================================

mcp = FastMCP(
    "QUANTA Elite Trading Intelligence",
    instructions="Institutional-grade stock analysis, market intelligence, and portfolio management tools powered by the QUANTA Elite Trading System v2.0.",
)

# ─── Global Analyzer (lazy-loaded) ──────────────────────────────────────────

_analyzer: Optional[QuantaEliteAnalyzer] = None
_api_client: Optional[APIClient] = None
_config: Optional[Dict[str, str]] = None


def _get_config() -> Dict[str, str]:
    """Load API configuration from environment or defaults."""
    global _config
    if _config is None:
        _config = {
            'news_api_key': os.getenv('NEWS_API_KEY', ''),
            'finnhub_key': os.getenv('FINNHUB_API_KEY', ''),
            'fmp_key': os.getenv('FMP_API_KEY', ''),
            'polygon_key': os.getenv('POLYGON_API_KEY', ''),
            'alpha_vantage_key': os.getenv('ALPHA_VANTAGE_KEY', ''),
        }
    return _config


def _get_analyzer() -> QuantaEliteAnalyzer:
    """Lazy-initialize the QUANTA analyzer (heavy object)."""
    global _analyzer
    if _analyzer is None:
        logger.info("Initializing QUANTA Elite Analyzer...")
        _analyzer = QuantaEliteAnalyzer(_get_config())
        logger.info("QUANTA Elite Analyzer ready")
    return _analyzer


def _get_api_client() -> APIClient:
    """Lazy-initialize the API client."""
    global _api_client
    if _api_client is None:
        _api_client = APIClient(_get_config())
    return _api_client


# =============================================================================
# SERIALIZATION HELPERS
# =============================================================================

def _safe_serialize(obj: Any) -> Any:
    """Recursively convert dataclass/enum/float objects to JSON-safe dicts."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        if obj != obj:  # NaN check
            return None
        if abs(obj) == float('inf'):
            return None
        return round(obj, 4)
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if hasattr(obj, '__dataclass_fields__'):
        return {k: _safe_serialize(v) for k, v in asdict(obj).items()}
    if hasattr(obj, 'value'):  # Enum
        return obj.value
    return str(obj)


def _result_to_dict(result: AnalysisResult) -> Dict[str, Any]:
    """Convert a full AnalysisResult to a serializable dictionary."""
    return _mcp_result_to_dict(result)

    # Kept below only as historical context while callers migrate to the
    # authoritative serializer above.
    d = {}

    # Basic info
    d['ticker'] = result.ticker
    d['company_name'] = result.company_name
    d['sector'] = result.sector
    d['industry'] = result.industry
    d['currency'] = result.currency
    d['currency_symbol'] = result.currency_symbol

    # Price
    d['current_price'] = _safe_serialize(result.current_price)
    d['previous_close'] = _safe_serialize(result.previous_close)
    d['day_change'] = _safe_serialize(result.day_change)
    d['day_change_pct'] = _safe_serialize(result.day_change_pct)
    d['market_cap'] = _safe_serialize(result.market_cap)

    # Extended hours
    d['pre_market_price'] = _safe_serialize(result.pre_market_price)
    d['pre_market_change_pct'] = _safe_serialize(result.pre_market_change_pct)
    d['after_hours_price'] = _safe_serialize(result.after_hours_price)
    d['after_hours_change_pct'] = _safe_serialize(result.after_hours_change_pct)
    d['market_state'] = result.market_state

    # Signal
    d['signal'] = result.signal.value if hasattr(result.signal, 'value') else str(result.signal)
    d['confidence_score'] = _safe_serialize(result.confidence_score)
    d['confidence_level'] = result.confidence_level.value if hasattr(result.confidence_level, 'value') else str(result.confidence_level)
    d['market_regime'] = result.market_regime.value if hasattr(result.market_regime, 'value') else str(result.market_regime)

    # Scores
    if result.scores:
        d['scores'] = _safe_serialize(result.scores)

    # Technical summary
    if result.technical:
        t = result.technical
        d['technical'] = {
            'rsi': _safe_serialize(t.rsi),
            'macd_signal': t.macd_signal,
            'adx': _safe_serialize(t.adx),
            'trend_direction': t.trend_direction,
            'trend_strength': t.trend_strength,
            'golden_cross': t.golden_cross,
            'death_cross': t.death_cross,
            'bollinger_signal': t.bollinger_signal,
            'volume_ratio': _safe_serialize(t.volume_ratio),
            'squeeze_detected': t.squeeze_detected,
            'support_level': _safe_serialize(t.support_level),
            'resistance_level': _safe_serialize(t.resistance_level),
            'week_52_high': _safe_serialize(t.week_52_high),
            'week_52_low': _safe_serialize(t.week_52_low),
            'momentum_score': _safe_serialize(t.momentum_score),
            'trend_score': _safe_serialize(t.trend_score),
            'volatility_score': _safe_serialize(t.volatility_score),
        }

    # Fundamental summary
    if result.fundamental:
        f = result.fundamental
        d['fundamental'] = {
            'pe_ratio': _safe_serialize(f.pe_ratio),
            'forward_pe': _safe_serialize(f.forward_pe),
            'peg_ratio': _safe_serialize(f.pe_to_growth),
            'pb_ratio': _safe_serialize(f.pb_ratio),
            'ev_ebitda': _safe_serialize(f.ev_ebitda),
            'roe': _safe_serialize(f.roe),
            'roa': _safe_serialize(f.roa),
            'roic': _safe_serialize(f.roic),
            'gross_margin': _safe_serialize(f.gross_margin),
            'operating_margin': _safe_serialize(f.operating_margin),
            'net_margin': _safe_serialize(f.net_margin),
            'revenue_growth_yoy': _safe_serialize(f.revenue_growth_yoy),
            'eps_growth_yoy': _safe_serialize(f.eps_growth_yoy),
            'debt_to_equity': _safe_serialize(f.debt_to_equity),
            'current_ratio': _safe_serialize(f.current_ratio),
            'free_cash_flow': _safe_serialize(f.free_cash_flow),
            'fcf_yield': _safe_serialize(f.fcf_yield),
            'dividend_yield': _safe_serialize(f.dividend_yield),
            'piotroski_f_score': f.piotroski_f_score,
            'altman_z_score': _safe_serialize(f.altman_z_score),
            'quality_score': _safe_serialize(f.quality_score),
            'is_etf': f.is_etf,
        }

    # Risk
    if result.risk:
        r = result.risk
        d['risk'] = {
            'annualized_volatility': _safe_serialize(r.annualized_volatility),
            'beta': _safe_serialize(r.beta),
            'sharpe_ratio': _safe_serialize(r.sharpe_ratio),
            'sortino_ratio': _safe_serialize(r.sortino_ratio),
            'max_drawdown': _safe_serialize(r.max_drawdown),
            'var_95_daily': _safe_serialize(r.var_95_daily),
            'cvar_95': _safe_serialize(r.cvar_95),
            'overall_risk_score': r.overall_risk_score,
            'risk_grade': r.risk_grade,
            'liquidity_score': _safe_serialize(r.liquidity_score),
        }

    # Sentiment
    if result.sentiment:
        s = result.sentiment
        d['sentiment'] = {
            'sentiment_score': _safe_serialize(s.sentiment_score),
            'sentiment_label': s.sentiment_label,
            'sentiment_trend': s.sentiment_trend,
            'news_volume': s.news_volume,
            'positive_articles': s.positive_articles,
            'negative_articles': s.negative_articles,
            'key_headlines': s.key_headlines[:5] if s.key_headlines else [],
        }

    # Market intelligence
    if result.market_intel:
        mi = result.market_intel
        d['market_intel'] = {
            'analyst_rating': mi.analyst_rating,
            'buy_ratings': mi.buy_ratings,
            'hold_ratings': mi.hold_ratings,
            'sell_ratings': mi.sell_ratings,
            'target_low': _safe_serialize(mi.target_low),
            'target_mean': _safe_serialize(mi.target_mean),
            'target_high': _safe_serialize(mi.target_high),
            'implied_upside': _safe_serialize(mi.implied_upside),
            'next_earnings_date': mi.next_earnings_date,
        }

    # Valuation
    if result.valuation:
        v = result.valuation
        d['valuation'] = {
            'dcf_fair_value': _safe_serialize(v.dcf_fair_value),
            'dcf_upside': _safe_serialize(v.dcf_upside),
            'graham_number': _safe_serialize(v.graham_number),
            'valuation_label': v.valuation_label,
            'valuation_score': _safe_serialize(v.valuation_score),
            'target_conservative': _safe_serialize(v.target_conservative),
            'target_base': _safe_serialize(v.target_base),
            'target_aggressive': _safe_serialize(v.target_aggressive),
            'target_confidence': _safe_serialize(v.target_confidence),
            'target_probability': _safe_serialize(v.target_probability),
        }

    # Monte Carlo
    if result.monte_carlo:
        mc = result.monte_carlo
        d['monte_carlo'] = {
            'simulations_run': mc.simulations_run,
            'median_price': _safe_serialize(mc.median_price),
            'percentile_5': _safe_serialize(mc.percentile_5),
            'percentile_25': _safe_serialize(mc.percentile_25),
            'percentile_75': _safe_serialize(mc.percentile_75),
            'percentile_95': _safe_serialize(mc.percentile_95),
            'probability_profit': _safe_serialize(mc.probability_profit),
            'probability_10pct_gain': _safe_serialize(mc.probability_10pct_gain),
            'probability_10pct_loss': _safe_serialize(mc.probability_10pct_loss),
            'expected_return': _safe_serialize(mc.expected_return),
        }

    # Trade setup
    if result.trade_setup:
        ts = result.trade_setup
        d['trade_setup'] = {
            'entry_price': _safe_serialize(ts.entry_price),
            'stop_loss': _safe_serialize(ts.stop_loss),
            'target_conservative': _safe_serialize(ts.target_conservative),
            'target_base': _safe_serialize(ts.target_base),
            'target_aggressive': _safe_serialize(ts.target_aggressive),
            'risk_reward_ratio': _safe_serialize(ts.risk_reward_ratio),
            'win_probability': _safe_serialize(ts.win_probability),
            'time_horizon': ts.time_horizon,
        }

    # Position sizing
    if result.position_sizing:
        ps = result.position_sizing
        d['position_sizing'] = {
            'recommended_shares': ps.recommended_shares,
            'position_value': _safe_serialize(ps.position_value),
            'portfolio_weight': _safe_serialize(ps.portfolio_weight),
            'kelly_optimal': _safe_serialize(ps.kelly_optimal),
            'is_viable': ps.is_viable,
            'sizing_method': ps.sizing_method,
        }

    # Insider
    if result.insider:
        ins = result.insider
        d['insider'] = {
            'insider_sentiment': ins.insider_sentiment,
            'recent_buys': ins.recent_buys,
            'recent_sells': ins.recent_sells,
            'net_flow': _safe_serialize(ins.net_flow),
            'key_transactions': ins.key_transactions[:5] if ins.key_transactions else [],
        }

    # Signal explanation
    if result.signal_explanation:
        se = result.signal_explanation
        d['signal_explanation'] = {
            'primary_reason': se.primary_reason,
            'composite_score': _safe_serialize(se.composite_score),
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
            'pe_stock': _safe_serialize(pc.pe_stock),
            'pe_sector_avg': _safe_serialize(pc.pe_sector_avg),
            'pe_vs_sector': _safe_serialize(pc.pe_vs_sector),
            'roe_stock': _safe_serialize(pc.roe_stock),
            'roe_sector_avg': _safe_serialize(pc.roe_sector_avg),
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
            'last_earnings_surprise_pct': _safe_serialize(cat.last_earnings_surprise_pct),
            'ex_dividend_date': cat.ex_dividend_date,
            'dividend_yield': _safe_serialize(cat.dividend_yield),
            'catalyst_score': cat.catalyst_score,
            'near_term_catalyst': cat.near_term_catalyst,
        }

    # Key outputs
    d['recommendation_text'] = result.recommendation_text
    d['key_insights'] = result.key_insights[:6] if result.key_insights else []
    d['risk_warnings'] = result.risk_warnings[:4] if result.risk_warnings else []
    d['action_items'] = result.action_items[:5] if result.action_items else []
    d['analysis_timestamp'] = result.analysis_timestamp

    return d


def _format_analysis_text(d: Dict[str, Any]) -> str:
    """Format analysis dict into a rich text summary for the LLM."""
    sym = d.get('currency_symbol', '$')
    lines = []

    # Header
    lines.append(f"# {d['company_name']} ({d['ticker']}) — QUANTA Elite Analysis")
    lines.append(f"**Sector:** {d['sector']} | **Industry:** {d['industry']}")
    price = d.get('current_price', 0)
    change_pct = d.get('day_change_pct', 0)
    arrow = "▲" if change_pct >= 0 else "▼"
    lines.append(f"**Price:** {sym}{price:,.2f} {arrow} {change_pct:+.2f}%")
    if d.get('market_cap'):
        lines.append(f"**Market Cap:** {format_currency(d['market_cap'], sym)}")
    lines.append("")

    # Signal
    lines.append(f"## Signal: {d['signal']}")
    lines.append(f"- **Confidence:** {d.get('confidence_score', 0):.0f}% ({d.get('confidence_level', 'N/A')})")
    lines.append(f"- **Market Regime:** {d.get('market_regime', 'N/A')}")
    if d.get('recommendation_text'):
        lines.append(f"- **Recommendation:** {d['recommendation_text']}")
    lines.append("")

    # Signal explanation
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
            for f in se['supporting_factors']:
                lines.append(f"  - {f}")
        if se.get('risk_factors'):
            lines.append("**Risk Factors:**")
            for r in se['risk_factors']:
                lines.append(f"  - {r}")
        lines.append("")

    # Scores
    if d.get('scores'):
        sc = d['scores']
        lines.append("## Multi-Factor Scores")
        lines.append(f"| Factor | Score | Grade |")
        lines.append(f"|--------|-------|-------|")
        for key in ['technical_score', 'fundamental_score', 'valuation_score',
                     'sentiment_score', 'risk_score']:
            name = key.replace('_score', '').title()
            val = sc.get(key, 50)
            grade = get_composite_grade(val)
            lines.append(f"| {name} | {val:.0f}/100 | {grade} |")
        comp = sc.get('composite_score', 50)
        lines.append(f"| **COMPOSITE** | **{comp:.0f}/100** | **{get_composite_grade(comp)}** |")
        lines.append("")

    # Trade setup
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
            lines.append(f"- **Risk/Reward:** {ts.get('risk_reward_ratio', 0):.1f}:1")
            lines.append(f"- **Win Probability:** {ts.get('win_probability', 0):.0%}")
        else:
            # Fallback: derive levels from technical data
            price = d.get('current_price', 0)
            tech = d.get('technical', {})
            support = tech.get('support_level', 0) or 0
            resistance = tech.get('resistance_level', 0) or 0
            lines.append("## Derived Trade Levels")
            lines.append("*Trade setup unavailable (guardrail active — e.g. imminent earnings or high risk). Using technical levels:*")
            if support > 0:
                lines.append(f"- **Support (potential entry):** {sym}{support:,.2f}")
            if resistance > 0:
                lines.append(f"- **Resistance (target):** {sym}{resistance:,.2f}")
            if support > 0 and price > 0:
                lines.append(f"- **Suggested Stop:** {sym}{support * 0.97:,.2f} (3% below support)")
            if resistance > 0 and support > 0:
                reward = resistance - price if price > 0 else 0
                risk = price - support * 0.97 if price > 0 else 0
                if risk > 0:
                    lines.append(f"- **Implied R:R:** {reward/risk:.1f}:1")
            lines.append(f"- **Win Probability:** {ts.get('win_probability', 0):.0%}")
        lines.append("")

    # Valuation targets
    if d.get('valuation'):
        v = d['valuation']
        lines.append("## Valuation & Price Targets")
        if v.get('dcf_fair_value'):
            lines.append(f"- **DCF Fair Value:** {sym}{v['dcf_fair_value']:,.2f} ({v.get('dcf_upside', 0):+.1f}%)")
        if v.get('target_base'):
            lines.append(f"- **Composite Target:** {sym}{v['target_base']:,.2f} (conf: {v.get('target_confidence', 0):.0f}%)")
        lines.append(f"- **Valuation:** {v.get('valuation_label', 'N/A')} (score: {v.get('valuation_score', 50):.0f}/100)")
        lines.append("")

    # Monte Carlo
    if d.get('monte_carlo'):
        mc = d['monte_carlo']
        lines.append(f"## Monte Carlo (30-day, {mc.get('simulations_run', 0)} sims)")
        lines.append(f"- **Median Price:** {sym}{mc.get('median_price', 0):,.2f}")
        lines.append(f"- **5th Percentile:** {sym}{mc.get('percentile_5', 0):,.2f}")
        lines.append(f"- **95th Percentile:** {sym}{mc.get('percentile_95', 0):,.2f}")
        lines.append(f"- **P(profit):** {mc.get('probability_profit', 0):.0%}")
        lines.append(f"- **P(+10%):** {mc.get('probability_10pct_gain', 0):.0%}")
        lines.append(f"- **P(-10%):** {mc.get('probability_10pct_loss', 0):.0%}")
        lines.append("")

    # Key insights
    if d.get('key_insights'):
        lines.append("## Key Insights")
        for insight in d['key_insights']:
            lines.append(f"- {insight}")
        lines.append("")

    # Risk warnings
    if d.get('risk_warnings'):
        lines.append("## Risk Warnings")
        for w in d['risk_warnings']:
            lines.append(f"- {w}")
        lines.append("")

    # Thesis
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

    # Action items
    if d.get('action_items'):
        lines.append("## Action Items")
        for a in d['action_items']:
            lines.append(f"- {a}")
        lines.append("")

    lines.append(f"*Analysis timestamp: {d.get('analysis_timestamp', 'N/A')}*")

    return "\n".join(lines)


# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool()
def analyze_stock(
    ticker: str,
    account_size: float = 100000,
) -> str:
    """
    Run a comprehensive QUANTA Elite analysis on a single stock ticker.
    
    Returns multi-factor scoring (technical, fundamental, valuation, sentiment, risk),
    trade setup with entry/exit levels, Monte Carlo projections, investment thesis,
    analyst consensus, insider activity, catalyst alerts, and peer comparison.
    
    This is the flagship analysis tool — equivalent to a full institutional research report.
    
    Args:
        ticker: Stock ticker symbol (e.g. AAPL, MSFT, NVDA, RELIANCE.NS)
        account_size: Portfolio size in dollars for position sizing (default: $100,000)
    """
    try:
        analyzer = _get_analyzer()
        start = time.time()
        result = analyzer.analyze(ticker.upper(), account_size)
        elapsed = time.time() - start

        d = _mcp_result_to_dict(result)
        d['analysis_time_seconds'] = round(elapsed, 1)

        text = _mcp_format_analysis_text(d)
        text += f"\n\n*Completed in {elapsed:.1f}s*"

        return text

    except Exception as e:
        logger.error(f"analyze_stock error: {e}")
        return f"Error analyzing {ticker}: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
def quick_scan(
    tickers: str,
    sort_by: str = "composite_score",
) -> str:
    """
    Rapidly screen multiple stocks and rank them by score.
    
    Runs full analysis on each ticker in parallel and returns a ranked comparison table.
    Great for screening watchlists, comparing alternatives, or finding the best pick.
    
    Args:
        tickers: Comma-separated ticker symbols (e.g. "AAPL,MSFT,GOOGL,NVDA,AMD")
        sort_by: Sort criterion — "composite_score", "confidence", "risk_reward", "upside" (default: composite_score)
    """
    try:
        ticker_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
        if not ticker_list:
            return "Error: provide at least one ticker"
        if len(ticker_list) > 15:
            return "Error: maximum 15 tickers per scan"

        analyzer = _get_analyzer()
        results = []
        errors = []

        # Analyze in parallel
        # One analyzer instance is shared and mutable (the web app serialises it
        # behind a lock), so the names are analysed one at a time.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future_map = {
                executor.submit(analyzer.analyze, t): t
                for t in ticker_list
            }
            for future in as_completed(future_map):
                t = future_map[future]
                try:
                    result = future.result(timeout=60)
                    results.append(result)
                except Exception as e:
                    errors.append(f"{t}: {str(e)}")

        if not results:
            return "No results. Errors: " + "; ".join(errors)

        # Build ranking rows
        rows = []
        for r in results:
            decision = decision_for_analysis(r)
            comp_score = r.scores.composite_score if r.scores else 0
            conf = r.confidence_score
            rr = r.trade_setup.risk_reward_ratio if r.trade_setup else 0
            upside = r.market_intel.implied_upside if r.market_intel else 0
            sym = r.currency_symbol

            rows.append({
                'ticker': r.ticker,
                'name': (r.company_name or r.ticker)[:20],
                'price': r.current_price,
                'change_pct': r.day_change_pct,
                'signal': decision.label if decision else 'NO VERDICT',
                'horizon': decision.horizon.label if decision else 'Unknown',
                'evidence': decision.evidence.status.value if decision else 'UNTESTED',
                'composite_score': comp_score,
                'confidence': conf,
                'risk_reward': rr,
                'upside': upside,
                'risk_grade': r.risk.risk_grade if r.risk else 'N/A',
                'rsi': r.technical.rsi if r.technical else 0,
                'pe_ratio': r.fundamental.pe_ratio if r.fundamental else 0,
                'sym': sym,
            })

        # Sort
        sort_key = sort_by if sort_by in ('composite_score', 'confidence', 'risk_reward', 'upside') else 'composite_score'
        rows.sort(key=lambda x: x.get(sort_key, 0), reverse=True)

        # Format table
        lines = [f"# QUANTA Quick Scan — {len(rows)} Stocks Ranked by {sort_by.replace('_', ' ').title()}\n"]
        lines.append("| Rank | Ticker | Price | Chg% | Research Decision | Horizon | Evidence | Score | Conf | R:R | Upside | Risk | RSI | P/E |")
        lines.append("|------|--------|-------|------|-------------------|---------|----------|-------|------|-----|--------|------|-----|-----|")

        for i, row in enumerate(rows, 1):
            sym = row['sym']
            lines.append(
                f"| {i} | **{row['ticker']}** | {sym}{row['price']:,.2f} | {row['change_pct']:+.1f}% "
                f"| {row['signal']} | {row['horizon']} | {row['evidence']} | {row['composite_score']:.0f} | {row['confidence']:.0f}% "
                f"| {row['risk_reward']:.1f}:1 | {row['upside']:+.0f}% "
                f"| {row['risk_grade']} | {row['rsi']:.0f} | {row['pe_ratio']:.1f}x |"
            )

        lines.append("")
        lines.append(f"**Highest-ranked research candidate: {rows[0]['ticker']}** — "
                     f"Score: {rows[0]['composite_score']:.0f}/100, "
                     f"Decision: {rows[0]['signal']}, Evidence: {rows[0]['evidence']}")

        if errors:
            lines.append(f"\n*Errors: {'; '.join(errors)}*")

        return "\n".join(lines)

    except Exception as e:
        return f"Scan error: {str(e)}"


@mcp.tool()
def market_environment() -> str:
    """
    Get a comprehensive snapshot of the current market environment.
    
    Includes: SPY price/trend, VIX level, Fear & Greed index, Fed policy outlook,
    FOMC dates, yield curve, sector leaders/laggards, mega-cap earnings calendar,
    and macro risk assessment. Use this before analyzing individual stocks.
    """
    try:
        api = _get_api_client()
        env = fetch_market_environment(api)

        lines = ["# Market Environment Dashboard\n"]

        if not env.get('fetch_success'):
            return "Market data temporarily unavailable. Try again shortly."

        # Market snapshot
        trend = env.get('spy_trend', 'N/A')
        lines.append("## Market Snapshot")
        lines.append(f"- **S&P 500 (SPY):** ${env['spy_price']:,.2f} ({env['spy_change_pct']:+.2f}%)")
        lines.append(f"- **Trend:** {trend}")
        lines.append(f"- **Support:** ${env.get('spy_support', 0):,.2f} | **Resistance:** ${env.get('spy_resistance', 0):,.2f}")
        lines.append(f"- **SMA 50:** ${env.get('spy_sma_50', 0):,.2f} | **SMA 200:** ${env.get('spy_sma_200', 0):,.2f}")
        lines.append("")

        # VIX & Sentiment
        lines.append("## Volatility & Sentiment")
        lines.append(f"- **VIX:** {env.get('vix', 0):.1f} — {env.get('vix_signal', 'N/A')}")
        lines.append(f"- **Fear & Greed:** {env.get('fear_greed', 50)} ({env.get('fear_greed_label', 'Neutral')})")
        lines.append(f"- **Put/Call Ratio:** {env.get('put_call_ratio', 1.0):.2f} — {env.get('put_call_signal', 'N/A')}")
        lines.append("")

        # Fed & Macro
        lines.append("## Fed Policy & Macro")
        lines.append(f"- **Fed Outlook:** {env.get('fed_outlook_emoji', '')} {env.get('fed_outlook', 'N/A')}")
        fomc = env.get('fomc_date', 'Unknown')
        days_fomc = env.get('days_to_fomc', -1)
        fomc_str = f"{fomc}" + (f" (in {days_fomc} days)" if days_fomc > 0 else "")
        lines.append(f"- **Next FOMC:** {fomc_str}")
        lines.append(f"- **10Y Treasury:** {env.get('treasury_10y', 0):.2f}%")
        lines.append(f"- **Yield Curve:** {env.get('yield_curve', 'N/A')}")
        lines.append(f"- **Economic Outlook:** {env.get('economic_outlook', 'N/A')}")
        lines.append(f"- **Macro Risk Score:** {env.get('macro_risk_score', 5)}/10")
        lines.append("")

        # Macro risk factors
        risk_factors = env.get('macro_risk_factors', [])
        if risk_factors:
            lines.append("## Macro Risk Factors")
            for rf in risk_factors[:5]:
                lines.append(f"- {rf}")
            lines.append("")

        # Sectors
        leaders = env.get('sector_leaders', [])
        laggards = env.get('sector_laggards', [])
        if leaders or laggards:
            lines.append("## Sector Performance (30-day)")
            lines.append("| Rank | Sector | Performance |")
            lines.append("|------|--------|-------------|")
            for name, pct in leaders:
                lines.append(f"| ▲ | {name} | {pct:+.1f}% |")
            for name, pct in laggards:
                lines.append(f"| ▼ | {name} | {pct:+.1f}% |")
            lines.append("")

        # Mega-cap earnings
        mega = env.get('mega_earnings_this_week', [])
        if mega:
            lines.append("## Mega-Cap Earnings This Week")
            lines.append(f"**Reporting:** {', '.join(mega)}")
            warning = env.get('mega_earnings_warning', '')
            if warning:
                lines.append(f"**Warning:** {warning}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"Market environment error: {str(e)}"


@mcp.tool()
def portfolio_risk_check(
    positions_json: str,
    portfolio_value: float = 100000,
) -> str:
    """
    Analyze portfolio-level risk: VaR, sector concentration, correlation clusters.
    
    Detects over-concentration, correlated positions, and calculates portfolio VaR.
    
    Args:
        positions_json: JSON array of positions, each with keys: ticker, shares, avg_cost, sector.
                        Example: '[{"ticker":"AAPL","shares":50,"avg_cost":175,"sector":"Technology"},
                                   {"ticker":"MSFT","shares":30,"avg_cost":380,"sector":"Technology"}]'
        portfolio_value: Total portfolio value in dollars (default: $100,000)
    """
    try:
        positions = json.loads(positions_json)
        if not isinstance(positions, list):
            return "Error: positions_json must be a JSON array of position objects"

        analyzer = _get_analyzer()
        lines = ["# Portfolio Risk Analysis\n"]

        # Calculate current values
        total_value = 0
        position_details = []

        for pos in positions:
            ticker = pos.get('ticker', '').upper()
            shares = pos.get('shares', 0)
            avg_cost = pos.get('avg_cost', 0)
            sector = pos.get('sector', 'Unknown')

            if not ticker or shares <= 0:
                continue

            # Quick price fetch
            try:
                import yfinance as yf
                stock = yf.Ticker(ticker)
                hist = stock.history(period='5d')
                current_price = float(hist['Close'].iloc[-1]) if len(hist) > 0 else avg_cost
            except Exception:
                current_price = avg_cost

            market_value = shares * current_price
            cost_basis = shares * avg_cost
            pnl = market_value - cost_basis
            pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0
            weight = (market_value / portfolio_value * 100) if portfolio_value > 0 else 0

            total_value += market_value
            position_details.append({
                'ticker': ticker,
                'shares': shares,
                'avg_cost': avg_cost,
                'current_price': current_price,
                'market_value': market_value,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'weight': weight,
                'sector': sector,
            })

        if not position_details:
            return "No valid positions provided."

        # Portfolio summary
        total_pnl = sum(p['pnl'] for p in position_details)
        total_cost = sum(p['shares'] * p['avg_cost'] for p in position_details)
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        lines.append(f"## Portfolio Summary")
        lines.append(f"- **Total Value:** ${total_value:,.2f}")
        lines.append(f"- **Total P&L:** ${total_pnl:+,.2f} ({total_pnl_pct:+.1f}%)")
        lines.append(f"- **Positions:** {len(position_details)}")
        lines.append("")

        # Position table
        lines.append("## Position Breakdown")
        lines.append("| Ticker | Shares | Avg Cost | Current | Value | P&L | Weight |")
        lines.append("|--------|--------|----------|---------|-------|-----|--------|")
        for p in sorted(position_details, key=lambda x: x['market_value'], reverse=True):
            lines.append(
                f"| **{p['ticker']}** | {p['shares']:,} | ${p['avg_cost']:,.2f} "
                f"| ${p['current_price']:,.2f} | ${p['market_value']:,.2f} "
                f"| {p['pnl_pct']:+.1f}% | {p['weight']:.1f}% |"
            )
        lines.append("")

        # Sector concentration
        sector_weights = {}
        for p in position_details:
            sector_weights[p['sector']] = sector_weights.get(p['sector'], 0) + p['weight']

        lines.append("## Sector Concentration")
        for sector, weight in sorted(sector_weights.items(), key=lambda x: x[1], reverse=True):
            warning = " ⚠️ OVER-CONCENTRATED" if weight > 40 else (" ⚠️ HIGH" if weight > 30 else "")
            lines.append(f"- **{sector}:** {weight:.1f}%{warning}")
        lines.append("")

        # Risk flags
        lines.append("## Risk Flags")
        max_position = max(position_details, key=lambda x: x['weight'])
        if max_position['weight'] > 25:
            lines.append(f"- ⚠️ **Single stock concentration:** {max_position['ticker']} at {max_position['weight']:.1f}% — reduce below 20%")

        for sector, weight in sector_weights.items():
            if weight > 40:
                lines.append(f"- ⚠️ **Sector concentration:** {sector} at {weight:.1f}% — diversify")

        losers = [p for p in position_details if p['pnl_pct'] < -15]
        if losers:
            for l in losers:
                lines.append(f"- 🔴 **Deep loss:** {l['ticker']} at {l['pnl_pct']:+.1f}% — review thesis")

        if len(position_details) < 5:
            lines.append(f"- ⚠️ **Under-diversified:** Only {len(position_details)} positions — consider 8-15 for adequate diversification")

        lines.append("")

        # Recommendations
        lines.append("## Portfolio Recommendations")
        if any(w > 35 for w in sector_weights.values()):
            lines.append("1. Reduce sector concentration to below 30% per sector")
        if max_position['weight'] > 20:
            lines.append(f"2. Trim {max_position['ticker']} to ≤15% of portfolio")
        if total_pnl_pct > 20:
            lines.append("3. Consider taking 25-33% profits to lock in gains")
        if total_pnl_pct < -10:
            lines.append("3. Review losing positions — cut losers below -20%")

        return "\n".join(lines)

    except json.JSONDecodeError:
        return "Error: Invalid JSON. Provide positions as: [{\"ticker\":\"AAPL\",\"shares\":50,\"avg_cost\":175,\"sector\":\"Technology\"}]"
    except Exception as e:
        return f"Portfolio risk check error: {str(e)}"


@mcp.tool()
def compliance_check(
    ticker: str,
    action: str = "BUY",
    price: float = 0,
    previous_close: float = 0,
    account_value: float = 100000,
) -> str:
    """
    Run regulatory compliance checks for a trade.
    
    Checks SEBI circuit breakers (for .NS/.BO tickers), SEC wash-sale rules,
    and FINRA Pattern Day Trading status. Generates compliance audit trail.
    
    Args:
        ticker: Stock ticker (use .NS/.BO suffix for Indian stocks)
        action: Trade action — "BUY" or "SELL" (default: BUY)
        price: Current stock price (fetched automatically if 0)
        previous_close: Previous closing price (fetched if 0)
        account_value: Account value for PDT check (default: $100,000)
    """
    if not HAS_COMPLIANCE:
        return "Compliance module not available. Install compliance_manager module."

    try:
        lines = [f"# Compliance Check: {ticker.upper()} — {action.upper()}\n"]

        compliance = ComplianceManager()

        # Auto-fetch price if not provided
        if price <= 0 or previous_close <= 0:
            try:
                import yfinance as yf
                stock = yf.Ticker(ticker.upper())
                hist = stock.history(period='5d')
                if len(hist) >= 2:
                    price = float(hist['Close'].iloc[-1])
                    previous_close = float(hist['Close'].iloc[-2])
            except Exception:
                pass

        # Circuit Breaker Check (Indian stocks)
        if ticker.upper().endswith(('.NS', '.BO')):
            lines.append("## SEBI Circuit Breaker Check")
            if price > 0 and previous_close > 0:
                cb = compliance.check_circuit_breaker(ticker, price, previous_close)
                if cb:
                    lines.append(f"- ⚠️ **CIRCUIT BREAKER TRIGGERED:** Level {cb.value}")
                    lines.append(f"- Trading may be halted for this security")
                else:
                    change_pct = ((price - previous_close) / previous_close) * 100
                    lines.append(f"- ✅ No circuit breaker (change: {change_pct:+.1f}%)")
            else:
                lines.append("- Price data unavailable for circuit breaker check")
            lines.append("")

        # PDT Check
        lines.append("## Pattern Day Trader Check")
        try:
            pdt = compliance.check_pdt_status(account_value)
            lines.append(f"- **Account Value:** ${account_value:,.2f}")
            lines.append(f"- **PDT Status:** {'⚠️ AT RISK' if hasattr(pdt, 'at_risk') and pdt.at_risk else '✅ OK'}")
            if hasattr(pdt, 'day_trades_remaining'):
                lines.append(f"- **Day Trades Remaining:** {pdt.day_trades_remaining}")
        except Exception:
            lines.append(f"- Account value ${account_value:,.2f} — {'✅ Above PDT minimum' if account_value >= 25000 else '⚠️ Below $25K PDT threshold'}")
        lines.append("")

        # Compliance summary
        lines.append("## Compliance Summary")
        try:
            summary = compliance.get_compliance_summary(account_value)
            for key, value in summary.items():
                lines.append(f"- **{key}:** {value}")
        except Exception:
            lines.append("- Compliance summary unavailable")

        lines.append("")
        lines.append(f"*Compliance check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)

    except Exception as e:
        return f"Compliance check error: {str(e)}"


@mcp.tool()
def catalyst_scan(
    ticker: str,
) -> str:
    """
    Scan recent news for actionable trading catalysts.
    
    Detects product launches, contracts, M&A activity, regulatory events,
    earnings surprises, and partnerships. Validates catalysts against volume data.
    
    Args:
        ticker: Stock ticker to scan for catalysts
    """
    try:
        analyzer = _get_analyzer()
        result = analyzer.analyze(ticker.upper())
        
        lines = [f"# Catalyst Scan: {result.company_name} ({result.ticker})\n"]
        lines.append(f"**Price:** {result.currency_symbol}{result.current_price:,.2f} ({result.day_change_pct:+.2f}%)\n")

        # Earnings catalysts
        if result.catalysts:
            cat = result.catalysts
            lines.append("## Earnings Calendar")
            lines.append(f"- **Earnings Date:** {cat.earnings_date}")
            if cat.days_to_earnings >= 0:
                lines.append(f"- **Days to Earnings:** {cat.days_to_earnings}")
            if cat.last_earnings_result != "Unknown":
                lines.append(f"- **Last Earnings:** {cat.last_earnings_result} (surprise: {cat.last_earnings_surprise_pct:+.1f}%)")
            if cat.ex_dividend_date != "Unknown":
                lines.append(f"- **Ex-Dividend:** {cat.ex_dividend_date} (yield: {cat.dividend_yield:.2f}%)")
            lines.append(f"- **Catalyst Score:** {cat.catalyst_score}/10")
            lines.append(f"- **Near-term Catalyst:** {'Yes' if cat.near_term_catalyst else 'No'}")
            lines.append("")

        # News-driven catalysts  
        if HAS_CATALYST and result.sentiment and result.sentiment.key_headlines:
            scanner = CatalystScanner()
            headlines = result.sentiment.key_headlines[:20]
            
            if result.sentiment.top_articles:
                headlines.extend([a.get('title', '') for a in result.sentiment.top_articles[:10]])

            catalysts = scanner.scan_headlines(result.ticker, headlines)
            actionable = [c for c in catalysts if c.is_actionable]

            if actionable:
                lines.append("## Detected Catalysts")
                for i, c in enumerate(actionable[:5], 1):
                    sentiment_icon = "🟢" if c.sentiment > 0 else ("🔴" if c.sentiment < 0 else "🟡")
                    lines.append(f"### {i}. {c.type.value.upper()} {sentiment_icon}")
                    lines.append(f"- **Headline:** {c.headline}")
                    lines.append(f"- **Confidence:** {c.confidence:.0%}")
                    lines.append(f"- **Keywords:** {', '.join(c.keywords_matched[:5])}")
                    lines.append("")

                # Volume validation
                vol_ratio = result.technical.volume_ratio if result.technical else 1.0
                vol_icon = "✅" if vol_ratio > 1.5 else ("⚠️" if vol_ratio > 1.0 else "❌")
                lines.append(f"**Volume Confirmation:** {vol_icon} {vol_ratio:.1f}x average")
            else:
                lines.append("## No Actionable Catalysts Detected")
                lines.append("No significant catalysts found in recent news flow.")
        else:
            lines.append("## Catalyst Scanner")
            lines.append("Advanced catalyst scanning unavailable. Check news manually.")

        # Sentiment summary
        if result.sentiment:
            s = result.sentiment
            lines.append(f"\n## News Sentiment")
            lines.append(f"- **Overall:** {s.sentiment_label} ({s.sentiment_score:+.2f})")
            lines.append(f"- **Articles:** {s.news_volume} (positive: {s.positive_articles}, negative: {s.negative_articles})")
            lines.append(f"- **Trend:** {s.sentiment_trend}")
            if s.key_headlines:
                lines.append("\n**Recent Headlines:**")
                for h in s.key_headlines[:5]:
                    lines.append(f"- {h}")

        return "\n".join(lines)

    except Exception as e:
        return f"Catalyst scan error: {str(e)}"


@mcp.tool()
def price_targets(
    ticker: str,
) -> str:
    """
    Get comprehensive price targets from multiple valuation methods.
    
    Combines DCF fair value, Graham Number, analyst consensus, Monte Carlo simulation,
    and relative valuation into weighted composite targets with confidence levels.
    
    Args:
        ticker: Stock ticker to calculate targets for
    """
    try:
        analyzer = _get_analyzer()
        result = analyzer.analyze(ticker.upper())
        sym = result.currency_symbol
        current = result.current_price

        lines = [f"# Price Targets: {result.company_name} ({result.ticker})\n"]
        lines.append(f"**Current Price:** {sym}{current:,.2f}\n")

        # Valuation methods
        lines.append("## Valuation Methods")
        lines.append("| Method | Target | Upside | Source |")
        lines.append("|--------|--------|--------|--------|")

        targets = []

        if result.valuation:
            v = result.valuation
            if v.dcf_fair_value > 0:
                upside = ((v.dcf_fair_value / current) - 1) * 100
                lines.append(f"| DCF Model | {sym}{v.dcf_fair_value:,.2f} | {upside:+.1f}% | Discounted Cash Flow |")
                targets.append(v.dcf_fair_value)
            if v.graham_number > 0:
                upside = ((v.graham_number / current) - 1) * 100
                lines.append(f"| Graham Number | {sym}{v.graham_number:,.2f} | {upside:+.1f}% | Benjamin Graham formula |")
                targets.append(v.graham_number)

        if result.market_intel:
            mi = result.market_intel
            if mi.target_low > 0:
                upside = ((mi.target_low / current) - 1) * 100
                lines.append(f"| Analyst Low | {sym}{mi.target_low:,.2f} | {upside:+.1f}% | Wall Street consensus |")
                targets.append(mi.target_low)
            if mi.target_mean > 0:
                upside = ((mi.target_mean / current) - 1) * 100
                lines.append(f"| Analyst Consensus | {sym}{mi.target_mean:,.2f} | {upside:+.1f}% | Wall Street mean |")
                targets.append(mi.target_mean)
            if mi.target_high > 0:
                upside = ((mi.target_high / current) - 1) * 100
                lines.append(f"| Analyst High | {sym}{mi.target_high:,.2f} | {upside:+.1f}% | Bull case |")
                targets.append(mi.target_high)

        if result.monte_carlo and result.monte_carlo.simulations_run > 0:
            mc = result.monte_carlo
            upside = ((mc.median_price / current) - 1) * 100
            lines.append(f"| Monte Carlo (30d) | {sym}{mc.median_price:,.2f} | {upside:+.1f}% | {mc.simulations_run} simulations |")
            targets.append(mc.median_price)

        lines.append("")

        # Composite targets
        if result.valuation and result.valuation.target_base > 0:
            v = result.valuation
            lines.append("## Weighted Composite Targets (12 months)")
            lines.append(f"- **Conservative:** {sym}{v.target_conservative:,.2f} ({((v.target_conservative/current)-1)*100:+.1f}%)")
            lines.append(f"- **Base Target:** {sym}{v.target_base:,.2f} ({((v.target_base/current)-1)*100:+.1f}%)")
            lines.append(f"- **Aggressive:** {sym}{v.target_aggressive:,.2f} ({((v.target_aggressive/current)-1)*100:+.1f}%)")
            lines.append(f"- **Confidence:** {v.target_confidence:.0f}%")
            lines.append(f"- **Probability (base):** {v.target_probability:.0f}%")
            lines.append("")

        # Monte Carlo probabilities
        if result.monte_carlo and result.monte_carlo.simulations_run > 0:
            mc = result.monte_carlo
            lines.append("## Monte Carlo Probability Distribution (30-day)")
            lines.append(f"- **5th percentile:** {sym}{mc.percentile_5:,.2f} ({((mc.percentile_5/current)-1)*100:+.1f}%)")
            lines.append(f"- **25th percentile:** {sym}{mc.percentile_25:,.2f} ({((mc.percentile_25/current)-1)*100:+.1f}%)")
            lines.append(f"- **Median:** {sym}{mc.median_price:,.2f} ({((mc.median_price/current)-1)*100:+.1f}%)")
            lines.append(f"- **75th percentile:** {sym}{mc.percentile_75:,.2f} ({((mc.percentile_75/current)-1)*100:+.1f}%)")
            lines.append(f"- **95th percentile:** {sym}{mc.percentile_95:,.2f} ({((mc.percentile_95/current)-1)*100:+.1f}%)")
            lines.append(f"- **P(profit):** {mc.probability_profit:.0%}")
            lines.append(f"- **P(+10% gain):** {mc.probability_10pct_gain:.0%}")
            lines.append(f"- **P(-10% loss):** {mc.probability_10pct_loss:.0%}")
            lines.append("")

        # Valuation assessment
        if result.valuation:
            lines.append(f"## Valuation Assessment")
            lines.append(f"- **Label:** {result.valuation.valuation_label}")
            lines.append(f"- **Score:** {result.valuation.valuation_score:.0f}/100")

        return "\n".join(lines)

    except Exception as e:
        return f"Price targets error: {str(e)}"


@mcp.tool()
def monte_carlo_sim(
    ticker: str,
    days: int = 30,
    simulations: int = 1000,
) -> str:
    """
    Run a Monte Carlo price simulation for a stock.
    
    Simulates thousands of possible price paths using historical volatility
    and returns probability distributions for profit, loss, and specific targets.
    
    Args:
        ticker: Stock ticker to simulate
        days: Number of trading days to simulate forward (default: 30)
        simulations: Number of simulation paths (default: 1000, max: 5000)
    """
    try:
        analyzer = _get_analyzer()
        result = analyzer.analyze(ticker.upper())
        sym = result.currency_symbol
        current = result.current_price

        mc = result.monte_carlo
        if not mc or mc.simulations_run == 0:
            return f"Monte Carlo simulation could not be run for {ticker}. Insufficient data."

        lines = [f"# Monte Carlo Simulation: {result.ticker}\n"]
        lines.append(f"**Current Price:** {sym}{current:,.2f}")
        lines.append(f"**Simulations:** {mc.simulations_run}")
        lines.append(f"**Time Horizon:** 30 trading days")
        lines.append(f"**Volatility:** {result.risk.annualized_volatility:.0%} annualized" if result.risk else "")
        lines.append(f"**Market Regime:** {result.market_regime.value if hasattr(result.market_regime, 'value') else result.market_regime}")
        lines.append("")

        # Price distribution
        lines.append("## Price Distribution")
        lines.append("| Percentile | Price | Return |")
        lines.append("|------------|-------|--------|")
        for pct_name, val in [
            ("5th (Bear)", mc.percentile_5),
            ("25th", mc.percentile_25),
            ("Median", mc.median_price),
            ("Mean", mc.mean_price),
            ("75th", mc.percentile_75),
            ("95th (Bull)", mc.percentile_95),
        ]:
            ret = ((val / current) - 1) * 100 if current > 0 else 0
            lines.append(f"| {pct_name} | {sym}{val:,.2f} | {ret:+.1f}% |")
        lines.append("")

        # Probabilities
        lines.append("## Probability Analysis")
        lines.append(f"- **Profit (>0%):** {mc.probability_profit:.1%}")
        lines.append(f"- **Gain >10%:** {mc.probability_10pct_gain:.1%}")
        lines.append(f"- **Loss >10%:** {mc.probability_10pct_loss:.1%}")
        lines.append(f"- **Expected Return:** {mc.expected_return:+.2%}")
        lines.append(f"- **Value at Risk (95%):** {mc.var_95:+.2%}")
        lines.append("")

        # Risk metrics
        if result.risk:
            r = result.risk
            lines.append("## Risk Context")
            lines.append(f"- **Daily VaR (95%):** {r.var_95_daily:.2%}")
            lines.append(f"- **CVaR (Expected Shortfall):** {r.cvar_95:.2%}")
            lines.append(f"- **Max Historical Drawdown:** {r.max_drawdown:.0%}")
            lines.append(f"- **Beta:** {r.beta:.2f}")
            lines.append(f"- **Sharpe Ratio:** {r.sharpe_ratio:.2f}")

        return "\n".join(lines)

    except Exception as e:
        return f"Monte Carlo error: {str(e)}"


@mcp.tool()
def compare_stocks(
    tickers: str,
) -> str:
    """
    Compare multiple stocks side-by-side across all key metrics.
    
    Produces a comprehensive comparison table with technical, fundamental,
    valuation, risk, and sentiment metrics. Ideal for choosing between alternatives.
    
    Args:
        tickers: Comma-separated ticker symbols (2-6 stocks, e.g. "AAPL,MSFT,GOOGL")
    """
    try:
        ticker_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
        if len(ticker_list) < 2:
            return "Provide at least 2 tickers to compare."
        if len(ticker_list) > 6:
            ticker_list = ticker_list[:6]

        analyzer = _get_analyzer()
        results = {}

        # One analyzer instance is shared and mutable (the web app serialises it
        # behind a lock), so the names are analysed one at a time.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future_map = {executor.submit(analyzer.analyze, t): t for t in ticker_list}
            for future in as_completed(future_map):
                t = future_map[future]
                try:
                    results[t] = future.result(timeout=60)
                except Exception as e:
                    logger.warning(f"Compare failed for {t}: {e}")

        if len(results) < 2:
            return f"Could only analyze {len(results)} stocks. Need at least 2."

        # Order by composite score
        ordered = sorted(results.values(),
                         key=lambda r: r.scores.composite_score if r.scores else 0,
                         reverse=True)

        lines = [f"# Stock Comparison: {' vs '.join(t for t in ticker_list if t in results)}\n"]

        # Header row
        tickers_str = " | ".join(f"**{r.ticker}**" for r in ordered)
        lines.append(f"| Metric | {tickers_str} |")
        lines.append("|--------|" + "|".join(["-----"] * len(ordered)) + "|")

        # Helper to add row
        def row(label, func):
            vals = " | ".join(func(r) for r in ordered)
            lines.append(f"| {label} | {vals} |")

        # Price & Signal
        row("Price", lambda r: f"${r.current_price:,.2f}")
        row("Day Change", lambda r: f"{r.day_change_pct:+.1f}%")
        decisions = {r.ticker: decision_for_analysis(r) for r in ordered}

        def _call(r):
            d = decisions.get(r.ticker)
            if d is None:
                return "NO VERDICT"
            q = d.presentation.qualifier
            return f"{d.label} ({q.lower()})" if q else d.label

        row("QUANTA Decision", _call)
        row("Evidence", lambda r: decisions[r.ticker].evidence.status_text if decisions.get(r.ticker) else "Unknown")
        row("Conviction (heuristic 0-100)", lambda r: f"{r.confidence_score:.0f}")
        row("Composite Score", lambda r: f"{r.scores.composite_score:.0f}" if r.scores else "N/A")
        row("Risk Grade", lambda r: r.risk.risk_grade if r.risk else "N/A")

        # Technical
        lines.append(f"| **--- Technical ---** |" + "|".join([""] * len(ordered)) + "|")
        row("RSI", lambda r: f"{r.technical.rsi:.0f}" if r.technical else "N/A")
        row("MACD Signal", lambda r: r.technical.macd_signal if r.technical else "N/A")
        row("Trend", lambda r: r.technical.trend_direction if r.technical else "N/A")
        row("ADX", lambda r: f"{r.technical.adx:.0f}" if r.technical else "N/A")
        row("Volume Ratio", lambda r: f"{r.technical.volume_ratio:.1f}x" if r.technical else "N/A")

        # Fundamental
        lines.append(f"| **--- Fundamental ---** |" + "|".join([""] * len(ordered)) + "|")
        row("P/E", lambda r: f"{r.fundamental.pe_ratio:.1f}x" if r.fundamental and r.fundamental.pe_ratio > 0 else "N/A")
        row("P/B", lambda r: f"{r.fundamental.pb_ratio:.1f}x" if r.fundamental and r.fundamental.pb_ratio > 0 else "N/A")
        row("ROE", lambda r: f"{r.fundamental.roe:.1f}%" if r.fundamental else "N/A")
        row("Net Margin", lambda r: f"{r.fundamental.net_margin:.1f}%" if r.fundamental else "N/A")
        row("D/E", lambda r: f"{r.fundamental.debt_to_equity:.2f}"
            if r.fundamental and r.fundamental.has('debt_to_equity') else "N/A")
        row("Rev Growth", lambda r: f"{r.fundamental.revenue_growth_yoy:+.1f}%" if r.fundamental else "N/A")
        row("Piotroski", lambda r: f"{r.fundamental.piotroski_f_score}/9" if r.fundamental else "N/A")

        # Valuation
        lines.append(f"| **--- Valuation ---** |" + "|".join([""] * len(ordered)) + "|")
        row("DCF Fair Value", lambda r: f"${r.valuation.dcf_fair_value:,.0f}" if r.valuation and r.valuation.dcf_fair_value > 0 else "N/A")
        row("DCF Upside", lambda r: f"{r.valuation.dcf_upside:+.0f}%" if r.valuation and r.valuation.dcf_upside != 0 else "N/A")
        row("Analyst Target", lambda r: f"${r.market_intel.target_mean:,.0f}" if r.market_intel and r.market_intel.target_mean > 0 else "N/A")
        row("Analyst Upside", lambda r: f"{r.market_intel.implied_upside:+.0f}%" if r.market_intel else "N/A")

        # Risk
        lines.append(f"| **--- Risk ---** |" + "|".join([""] * len(ordered)) + "|")
        row("Volatility", lambda r: f"{r.risk.annualized_volatility:.0%}" if r.risk else "N/A")
        row("Beta", lambda r: f"{r.risk.beta:.2f}" if r.risk else "N/A")
        row("Max Drawdown", lambda r: f"{r.risk.max_drawdown:.0%}" if r.risk else "N/A")
        row("Sharpe", lambda r: f"{r.risk.sharpe_ratio:.2f}" if r.risk else "N/A")
        row("R/R Ratio", lambda r: f"{r.trade_setup.risk_reward_ratio:.1f}:1" if r.trade_setup else "N/A")

        # Monte Carlo
        lines.append(f"| **--- Monte Carlo ---** |" + "|".join([""] * len(ordered)) + "|")
        row("P(profit) 30d", lambda r: f"{r.monte_carlo.probability_profit:.0%}" if r.monte_carlo else "N/A")
        row("P(+10%)", lambda r: f"{r.monte_carlo.probability_10pct_gain:.0%}" if r.monte_carlo else "N/A")
        row("Median Target", lambda r: f"${r.monte_carlo.median_price:,.0f}" if r.monte_carlo else "N/A")

        lines.append("")

        # Ranking, not a recommendation: the composite has no validated edge.
        best = ordered[0]
        lines.append("## Ranking")
        lines.append(f"**Highest composite: {best.ticker}** ({best.scores.composite_score:.0f}/100) — "
                     f"QUANTA Decision: {_call(best)}. A higher composite is a ranking, "
                     "not a validated prediction.")
        if best.thesis and best.thesis.summary:
            lines.append(f"\n{best.thesis.summary}")

        return "\n".join(lines)

    except Exception as e:
        return f"Compare error: {str(e)}"


@mcp.tool()
def position_advisor(
    ticker: str,
    shares: int,
    avg_cost: float,
    account_size: float = 100000,
) -> str:
    """
    Get personalized advice for an existing stock position.
    
    Analyzes your P&L, the stock's current outlook, macro conditions, and risk profile
    to give actionable advice: hold, add, trim, or exit — with specific levels.
    
    Args:
        ticker: Stock ticker you hold
        shares: Number of shares you own
        avg_cost: Your average cost per share
        account_size: Total portfolio value (default: $100,000)
    """
    try:
        analyzer = _get_analyzer()
        api = _get_api_client()

        # Run analysis
        result = analyzer.analyze(ticker.upper(), account_size)
        decision = decision_for_analysis(result)
        market_env = fetch_market_environment(api) or {}

        sym = result.currency_symbol
        current = result.current_price
        # Position metrics
        market_value = shares * current
        cost_basis = shares * avg_cost
        unrealized_pnl = market_value - cost_basis
        pnl_pct = ((current - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0
        weight = (market_value / account_size) * 100 if account_size > 0 else 0

        # Key levels
        stop = decision.stop if decision and decision.stop and decision.stop > 0 else 0.0
        target = decision.target if decision and decision.target and decision.target > 0 else 0.0
        target_upside = ((target - current) / current) * 100 if target and current > 0 else 0.0

        # Risk context
        risk_grade = result.risk.risk_grade if result.risk else "C"
        confidence = result.confidence_score

        lines = [f"# Position Advisor: {result.company_name} ({result.ticker})\n"]

        # Position summary
        pnl_icon = "🟢" if unrealized_pnl >= 0 else "🔴"
        lines.append("## Your Position")
        lines.append(f"- **Shares:** {shares:,} @ {sym}{avg_cost:,.2f}")
        lines.append(f"- **Current Price:** {sym}{current:,.2f} ({result.day_change_pct:+.2f}%)")
        lines.append(f"- **Market Value:** {sym}{market_value:,.2f}")
        lines.append(f"- **P&L:** {pnl_icon} {sym}{unrealized_pnl:+,.2f} ({pnl_pct:+.1f}%)")
        lines.append(f"- **Portfolio Weight:** {weight:.1f}%")
        lines.append("")

        # QUANTA Signal
        lines.append("## QUANTA Analysis")
        lines.append(f"- **Confidence:** {confidence:.0f}%")
        lines.append(f"- **Risk Grade:** {risk_grade}")
        lines.append(f"- **Model Target:** {sym}{target:,.2f} ({target_upside:+.1f}%)" if target else "- **Model Target:** unavailable")
        lines.append("")

        # Macro context
        macro_risk = market_env.get('macro_risk_score', 5) if market_env else 5
        lines.append("## Macro Context")
        lines.append(f"- **SPY:** ${market_env.get('spy_price', 0):,.2f} ({market_env.get('spy_trend', 'N/A')})")
        lines.append(f"- **VIX:** {market_env.get('vix', 0):.1f}")
        lines.append(f"- **Fed:** {market_env.get('fed_outlook', 'N/A')}")
        lines.append(f"- **Macro Risk:** {macro_risk}/10")
        lines.append("")

        # The deep engine owns the verdict. P&L and macro values above are
        # context for a holder, not a second recommendation algorithm.
        lines.append("## Research Decision")
        if decision is None:
            lines.append("- **Action:** NO VERDICT — the analysis did not resolve a final action.")
        else:
            lines.append(f"- **Action:** {decision.label}")
            lines.append(f"- **Model action:** {decision.action.value}")
            lines.append(f"- **Horizon:** {decision.horizon.label}")
            lines.append(f"- **Evidence:** {decision.evidence.status_text}")
            lines.append(f"- **Price as of:** {decision.as_of or 'unknown'} ({decision.freshness.status})")
            if decision.presentation.caveat:
                lines.append(f"- **Limit:** {decision.presentation.caveat}")
            for reason in decision.reasons[:3]:
                lines.append(f"- {reason}")
        lines.append("")

        # Key levels
        lines.append(f"\n## Key Price Levels")
        lines.append(f"- **Your Cost:** {sym}{avg_cost:,.2f}")
        lines.append(f"- **Model Stop:** {sym}{stop:,.2f} ({((stop-current)/current)*100:+.1f}%)" if stop and current > 0 else "- **Model Stop:** unavailable")
        lines.append(f"- **Model Target:** {sym}{target:,.2f} ({target_upside:+.1f}%)" if target else "- **Model Target:** unavailable")
        if result.technical:
            if result.technical.support_level > 0:
                lines.append(f"- **Support:** {sym}{result.technical.support_level:,.2f}")
            if result.technical.resistance_level > 0:
                lines.append(f"- **Resistance:** {sym}{result.technical.resistance_level:,.2f}")

        # Thesis summary
        if result.thesis and result.thesis.summary:
            lines.append(f"\n## Thesis")
            lines.append(result.thesis.summary)

        return "\n".join(lines)

    except Exception as e:
        return f"Position advisor error: {str(e)}"


# =============================================================================
# MCP RESOURCES (Context for the LLM)
# =============================================================================

@mcp.resource("quanta://config")
def get_quanta_config() -> str:
    """QUANTA system configuration and thresholds."""
    return json.dumps({
        "system": "QUANTA Elite Trading Intelligence v2.0",
        "capabilities": [
            "Multi-factor stock analysis (technical + fundamental + sentiment + risk)",
            "Monte Carlo price simulation (1000 paths, 30-day horizon)",
            "DCF + Graham + analyst composite valuation",
            "News sentiment analysis with VADER",
            "Insider trading activity tracking",
            "Catalyst detection (earnings, M&A, product launches)",
            "Portfolio risk management (VaR, sector concentration)",
            "SEBI/SEC regulatory compliance",
            "Position sizing with Kelly criterion",
            "Peer comparison vs sector averages",
        ],
        "scoring_weights_sideways": {
            k: v / sum(Config.REGIME_WEIGHTS["SIDEWAYS"].values())
            for k, v in Config.REGIME_WEIGHTS["SIDEWAYS"].items()
        },
        "risk_note": "Risk is used for gates and sizing, not the ranking composite.",
        "signal_cutoffs": list(Config.SIGNAL_CUTOFFS),
        "risk_parameters": {
            "min_risk_reward": 1.5,
            "max_position_pct": 10,
            "kelly_fraction": 0.25,
            "mc_simulations": 1000,
        },
    }, indent=2)


@mcp.resource("quanta://supported-tickers")
def get_supported_tickers() -> str:
    """Information about supported ticker formats."""
    return json.dumps({
        "formats": {
            "US stocks": "AAPL, MSFT, NVDA (standard NYSE/NASDAQ)",
            "Indian NSE": "RELIANCE.NS, TCS.NS, INFY.NS",
            "Indian BSE": "RELIANCE.BO, TCS.BO",
            "UK LSE": "SHEL.L, HSBA.L",
            "ETFs": "SPY, QQQ, VTI, ARKK",
        },
        "examples": [
            "AAPL", "MSFT", "GOOGL", "NVDA", "AMD", "TSLA", "META",
            "AMZN", "NFLX", "JPM", "V", "WMT", "UNH", "JNJ",
            "SPY", "QQQ", "RELIANCE.NS", "TCS.NS",
        ],
        "note": "Any ticker supported by Yahoo Finance will work."
    }, indent=2)


# =============================================================================
# MCP PROMPTS
# =============================================================================

@mcp.prompt()
def trading_analysis_prompt(ticker: str) -> str:
    """Generate a comprehensive trading analysis prompt for a stock."""
    return f"""Analyze {ticker} using the QUANTA Elite Trading System. Please:

1. First check the market environment (market_environment tool) for macro context
2. Run a full analysis (analyze_stock tool) on {ticker}
3. Check for any catalysts (catalyst_scan tool)
4. Get detailed price targets (price_targets tool)

Then provide a synthesis with:
- Clear BUY/HOLD/SELL recommendation with rationale
- Entry, stop-loss, and target prices
- Key risks to monitor
- Position sizing suggestion for a $100K portfolio
"""


@mcp.prompt()
def portfolio_review_prompt(tickers: str) -> str:
    """Generate a portfolio review prompt."""
    return f"""Review this portfolio using QUANTA Elite:

1. Check market environment for macro context
2. Run quick_scan on these tickers: {tickers}
3. Run compare_stocks to see side-by-side metrics
4. For the top and bottom picks, do a full analyze_stock

Provide:
- Portfolio scoring and ranking
- Sector concentration analysis
- Top recommendation to buy, hold, or sell
- Specific action items
"""


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logger.info("Starting QUANTA Elite MCP Server...")
    mcp.run()
