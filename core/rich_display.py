from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.layout import Layout
from rich.align import Align
from rich import box
import textwrap

from engine import thresholds as _T

# Mirrors TradingAnalysis_ELITE.PIOTROSKI_MIN_INPUTS. Duplicated rather than
# imported: this module is a standalone renderer and must not pull the whole
# analyzer in just to format one cell.
PIOTROSKI_MIN_INPUTS = 5


def _piotroski_reportable(fundamental) -> bool:
    """True when the F-score rests on enough reported provider fields."""
    if fundamental is None:
        return False
    return int(getattr(fundamental, 'piotroski_components_available', 0) or 0) >= PIOTROSKI_MIN_INPUTS


def _piotroski_text(fundamental) -> str:
    """A real score, or an honest N/A naming the coverage that was available."""
    if fundamental is None:
        return "N/A"
    available = int(getattr(fundamental, 'piotroski_components_available', 0) or 0)
    score = int(getattr(fundamental, 'piotroski_f_score', 0) or 0)
    if available < PIOTROSKI_MIN_INPUTS:
        return f"N/A ({available}/9 inputs)"
    return f"{score}/9 [{'Quality' if score >= 7 else 'Weak'}]"

def display_analysis(result: 'AnalysisResult', detailed: bool = True):
    """
    Display analysis results with restructured professional layout using rich
    """
    console = Console()
    sym = result.currency_symbol
    
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
    mc_str = format_currency(result.market_cap, sym) if result.market_cap > 0 else ""
    
    header_text = Text()
    header_text.append(f"{result.company_name} ({result.ticker})", style="bold cyan")
    header_text.append(f" | {result.sector}\n", style="dim")
    header_text.append(f"{fmt(result.current_price)} ", style="bold white")
    header_text.append(f"{price_arrow} {result.day_change:+.2f} ({result.day_change_pct:+.2f}%)", style=f"bold {price_color}")
    if mc_str:
        header_text.append(f" | Mkt Cap: {mc_str}", style="dim")
    
    console.print(Panel(header_text, title="[bold yellow]QUANTA ELITE ANALYSIS[/bold yellow]", border_style="yellow", box=box.DOUBLE))
    
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
    # SMART TARGET CALCULATION - Use multiple sources for better accuracy
    # =========================================================================
    targets = []
    if result.market_intel and result.market_intel.target_mean > 0:
        targets.append(result.market_intel.target_mean)
    if result.valuation and result.valuation.dcf_fair_value > 0:
        targets.append(result.valuation.dcf_fair_value)
    if result.monte_carlo and result.monte_carlo.percentile_75 > 0:
        targets.append(result.monte_carlo.percentile_75)
    if result.valuation and result.valuation.target_base > 0:
        targets.append(result.valuation.target_base)
    
    # Pick the best target (median of available, or 10% default)
    if targets:
        targets.sort()
        target = targets[len(targets)//2]  # Median
    else:
        target = result.current_price * 1.10  # Default 10% upside
    
    # Ensure target is at least 5% above current for actionable trade
    min_target = result.current_price * 1.05
    if target < min_target:
        target = min_target
    
    # =========================================================================
    # SMART STOP-LOSS - ATR-based (3-8%) instead of distant support levels
    # =========================================================================
    # Calculate volatility-adjusted stop (not too tight, not too far)
    vol = result.risk.annualized_volatility if result.risk and result.risk.annualized_volatility > 0 else 0.30
    
    # ATR-based stop if available
    if result.technical and result.technical.atr_percent > 0:
        atr_pct = result.technical.atr_percent
        # Use 2x ATR but cap between 3% and 10%
        stop_pct = min(0.10, max(0.03, 2 * atr_pct / 100))
    else:
        # Fallback: volatility-based stop (3-8% based on annualized vol)
        stop_pct = min(0.08, max(0.03, vol / 4))
    
    stop = result.current_price * (1 - stop_pct)
    
    # Also calculate support-based stop for reference
    support_stop = result.technical.support_level if result.technical.support_level > 0 else 0
    
    # =========================================================================
    # ENTRY ZONE CALCULATION - Optimal pullback levels
    # =========================================================================
    # Buy zone: 2-5% pullback from current price
    buy_zone_low = result.current_price * 0.95   # -5%
    buy_zone_high = result.current_price * 0.98  # -2%
    aggressive_entry = result.current_price * 0.99  # -1%
    
    # =========================================================================
    # EXIT LEVELS - Multiple take-profit targets
    # =========================================================================
    tp1 = result.current_price * 1.05  # +5%
    tp2 = result.current_price * 1.10  # +10%
    tp3 = target if target > tp2 else result.current_price * 1.15  # +15% or target
    trailing_stop_pct = stop_pct + 0.02  # Trailing is 2% looser than initial
    
    # =========================================================================
    # RISK/REWARD CALCULATION
    # =========================================================================
    rr_valid_setup = False
    if target > result.current_price and stop < result.current_price:
        reward = target - result.current_price
        risk = result.current_price - stop
        rr_ratio = reward / risk if risk > 0 else 0
        rr_valid_setup = rr_ratio >= 1.5
    else:
        rr_ratio = 0
    
    target_upside = ((target / result.current_price) - 1) * 100 if result.current_price > 0 and target > 0 else 0
    stop_downside = ((stop / result.current_price) - 1) * 100 if result.current_price > 0 and stop > 0 else -3
    
    # ── Signal Box ──
    # Determine best horizon
    _horizon = "Mid-Term"
    if result.monte_carlo and result.monte_carlo.probability_profit > 0.6:
        _horizon = "Short-Term"
    if (_piotroski_reportable(result.fundamental)
            and result.fundamental.piotroski_f_score >= 7):
        _horizon = "Long-Term"
    
    rr_note = ("" if rr_ratio >= _T.RR.RESEARCH_MIN or rr_ratio == 0
               else f" (below {_T.fmt_rr(_T.RR.RESEARCH_MIN)} min)")
    grade = get_composite_grade(score)
    
    signal_color = "green" if "BUY" in signal_str else "red" if "SELL" in signal_str else "yellow"
    
    signal_table = Table(show_header=False, box=None, expand=True)
    signal_table.add_column("Metric", style="bold")
    signal_table.add_column("Value")
    signal_table.add_column("Metric2", style="bold")
    signal_table.add_column("Value2")
    
    signal_table.add_row("SIGNAL:", f"[{signal_color} bold]{signal_display}[/{signal_color} bold]", "Confidence:", f"{confidence:>3.0f}%")
    signal_table.add_row("Score:", f"{score:>3.0f}/100 ({grade})", "Risk Grade:", f"{risk_grade}")
    signal_table.add_row("R/R Ratio:", f"{rr_ratio:>4.1f}:1[dim]{rr_note}[/dim]", "Horizon:", f"{_horizon}")
    
    console.print(Panel(signal_table, title="[bold]EXECUTIVE SUMMARY[/bold]", border_style="blue", box=box.ROUNDED))
    
    # =========================================================================
    # ACTION PLAN
    # =========================================================================
    action_text = Text()
    
    # WAIT CHECK: If setup quality is poor, show prominent WAIT message
    signal_lower = signal_str.lower()
    poor_setup = False
    wait_reasons = []
    
    # Check for poor setup conditions - USE THE ALREADY CALCULATED rr_ratio
    if rr_ratio > 0 and rr_ratio < _T.RR.RESEARCH_MIN:
        poor_setup = True
        wait_reasons.append(f"R/R ratio {_T.fmt_rr(rr_ratio)} < minimum {_T.fmt_rr(_T.RR.RESEARCH_MIN)}")
    
    if result.technical.macd_value < result.technical.macd_signal_line:
        poor_setup = True  # MACD bearish should also flag poor setup
        wait_reasons.append("MACD bearish - momentum not confirmed")
    
    if hasattr(result.technical, 'vwap') and result.technical.vwap and result.current_price < result.technical.vwap:
        wait_reasons.append(f"Trading below VWAP ({fmt(result.technical.vwap)})")
    
    if risk_grade in ['D', 'F']:
        poor_setup = True
        wait_reasons.append(f"Risk Grade {risk_grade} - elevated risk")
    
    # If buy signal but poor setup, show WAIT instead
    if poor_setup and ('buy' in signal_lower or 'accumulate' in signal_lower):
        action_text.append("WAIT - Setup below quality standards\n", style="bold yellow")
        for reason in wait_reasons:
            action_text.append(f"  • {reason}\n", style="yellow")
        action_text.append(f"\nWait for pullback to {fmt(result.current_price * 0.97)} (-3%) or R/R > {_T.fmt_rr(_T.RR.RESEARCH_MIN)}\n", style="bold")
    elif 'buy' in signal_lower or 'accumulate' in signal_lower:
        action_text.append(f"Entry Zone:      {fmt(buy_zone_low)} - {fmt(buy_zone_high)} (pullback -2% to -5%)\n", style="bold green")
    elif 'hold' in signal_lower:
        action_text.append(f"Hold existing. Stop at {fmt(stop)}. Watch {fmt(result.technical.resistance_level)} resistance.\n", style="bold yellow")
    elif 'sell' in signal_lower or 'reduce' in signal_lower:
        action_text.append(f"Exit or reduce exposure. Risk/reward unfavorable.\n", style="bold red")
    
    # Position sizing
    if 'buy' in signal_lower or 'accumulate' in signal_lower:
        _cap = 10000
        _pct = 0.03 if risk_grade in ['A', 'B'] else (0.025 if risk_grade == 'C' else 0.02)
        _val = _cap * _pct
        _sh = max(1, int(_val / result.current_price)) if result.current_price > 0 else 0
        _val = _sh * result.current_price
        action_text.append(f"Position Size:   ${_val:,.0f} of $10K ({_val/100:.1f}%) = {_sh} share{'s' if _sh != 1 else ''}\n")
    
    # Exit levels
    action_text.append(f"\nStop Loss:       {fmt(stop):<14} [{stop_downside:+.1f}%]   [bold red]EXIT ALL[/bold red]\n")
    if target > result.current_price:
        action_text.append(f"Target 1:        {fmt(tp1):<14} [{((tp1/result.current_price)-1)*100:+.1f}%]   [bold green]Sell 33%[/bold green]\n")
        action_text.append(f"Target 2:        {fmt(tp2):<14} [{((tp2/result.current_price)-1)*100:+.1f}%]   [bold green]Sell 33%[/bold green]\n")
        action_text.append(f"Target 3:        {fmt(tp3):<14} [{((tp3/result.current_price)-1)*100:+.1f}%]   [bold green]Sell rest[/bold green]\n")
    action_text.append(f"Trailing Stop:   {trailing_stop_pct*100:.0f}% below peak after +5% profit\n", style="dim")
    
    console.print(Panel(action_text, title="[bold]ACTION PLAN[/bold]", border_style="green", box=box.ROUNDED))
    
    # ─── Section 3: Analysis Breakdown ───
    
    # Multi-Factor Scores
    scores_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    scores_table.add_column("Factor")
    scores_table.add_column("Score", justify="right")
    scores_table.add_column("Bar")
    scores_table.add_column("Rating")
    scores_table.add_column("Key Drivers", style="dim")
    
    scores_data = [
        ("Technical", result.scores.technical_score, "RSI, MACD, trend"),
        ("Fundamental", result.scores.fundamental_score, "ROE, margins, growth"),
        ("Valuation", result.scores.valuation_score, "P/E, DCF, targets"),
        ("Sentiment", result.scores.sentiment_score, "News, analyst ratings"),
        ("Risk-Adjusted", result.scores.risk_score, "Volatility, drawdown"),
    ]
    
    for name, score_val, desc in scores_data:
        bar = make_bar(score_val, 20)
        label = "Strong" if score_val >= 70 else ("Good" if score_val >= 55 else ("Fair" if score_val >= 40 else "Weak"))
        color = "green" if score_val >= 70 else ("yellow" if score_val >= 40 else "red")
        scores_table.add_row(name, f"{score_val:>3.0f}", f"[{color}]{bar}[/{color}]", f"[{color}]{label}[/{color}]", desc)
    
    # Composite
    comp = result.scores.composite_score
    comp_bar = make_bar(comp, 20)
    comp_grade = get_composite_grade(comp)
    comp_color = "green" if comp >= 70 else ("yellow" if comp >= 40 else "red")
    scores_table.add_row("COMPOSITE", f"[bold]{comp:>3.0f}[/bold]", f"[{comp_color}]{comp_bar}[/{comp_color}]", f"[bold {comp_color}]Grade: {comp_grade}[/bold {comp_color}]", "")
    
    console.print(Panel(scores_table, title="[bold]MULTI-FACTOR SCORES[/bold]", border_style="magenta", box=box.ROUNDED))
    
    # Why BUY/HOLD/SELL - Format signal properly
    why_text = Text()
    if result.key_insights:
        for insight in result.key_insights[:4]:
            clean = str(insight).replace("⏸️ ", "").replace("🟢 ", "• ").replace("📈 ", "• ").replace("📊 ", "• ").replace("💪 ", "• ").replace("⭐ ", "• ").replace("⚠️ ", "").replace("📰 ", "• ")
            if not clean.startswith("• "):
                clean = "• " + clean
            why_text.append(f"{clean[:75]}\n")
    
    # Key Risks
    risks_text = Text()
    if result.risk_warnings:
        for warning in result.risk_warnings[:3]:
            clean_warning = str(warning).replace("⚠️ ", "⚠ ").replace("🚨 ", "⚠ ")
            risks_text.append(f"{clean_warning[:75]}\n", style="red")
            
    columns = Columns([
        Panel(why_text, title=f"[bold]WHY {signal_display.upper()}[/bold]", border_style="cyan", expand=True),
        Panel(risks_text, title="[bold]KEY RISKS[/bold]", border_style="red", expand=True)
    ])
    console.print(columns)
    
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
                triggers.append(f"• Pullback to support ${result.technical.support_level:.2f} (-{pullback_pct:.0f}%){rr_text}")
        
        # Breakout trigger
        if result.technical and result.technical.week_52_high > result.current_price:
            breakout_pct = ((result.technical.week_52_high / result.current_price) - 1) * 100
            if breakout_pct < 10:
                triggers.append(f"• Breakout above ${result.technical.week_52_high:.2f} (+{breakout_pct:.0f}%) confirms momentum")
        
        # Risk reduction trigger
        if risk_grade in ['D', 'F']:
            triggers.append(f"• Risk grade improves to C or better (address leverage/volatility)")
        
        # Market condition trigger
        if result.risk and result.risk.annualized_volatility > 0.30:
            triggers.append(f"• Market volatility cools (VIX < 18, currently elevated)")
        
        # R:R improvement
        if rr_ratio < _T.RR.RESEARCH_MIN and rr_ratio > 0:
            target_price_for_good_rr = result.current_price + (1.5 * (result.current_price - stop))
            triggers.append(f"• Better entry improves R:R to ≥{_T.fmt_rr(_T.RR.RESEARCH_MIN)} (need {abs(stop_downside)*_T.RR.RESEARCH_MIN:.0f}% upside)")
        
        if triggers:
            for trigger in triggers[:4]:
                triggers_text.append(f"{trigger}\n")
            triggers_text.append(f"\nWaiting for better setup improves risk-adjusted returns\n", style="dim italic")
        else:
            triggers_text.append(f"Signal already near optimal - main risks are systemic/macro\n")
            
        console.print(Panel(triggers_text, title="[bold]UPGRADE TRIGGERS[/bold]", border_style="blue", box=box.ROUNDED))
    
    # ─── Section 4: Market Intelligence ───
    
    # News & Sentiment
    s = result.sentiment
    news_text = Text()
    news_text.append(f"Sentiment: {s.sentiment_label} ({s.sentiment_score:+.2f}) | Trend: {s.sentiment_trend}\n\n", style="bold")
    if s.key_headlines:
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
        
        # Dividends
        if cat.ex_dividend_date and cat.ex_dividend_date != "Unknown":
            div_text = f"${cat.dividend_amount:.2f}" if cat.dividend_amount > 0 else "TBD"
            yield_text = f", {cat.dividend_yield:.2f}%" if cat.dividend_yield > 0 else ""
            cat_text.append(f"Ex-Dividend: {cat.ex_dividend_date} ({div_text}{yield_text})\n")
            
        console.print(Panel(cat_text, title="[bold]CATALYST ALERTS[/bold]", border_style="yellow", box=box.ROUNDED))
    
    # Analyst Consensus
    mi = result.market_intel
    if mi.target_mean > 0:
        analyst_text = Text()
        analyst_text.append(f"Rating: {mi.analyst_rating} | Target: {fmt(mi.target_mean)} ({mi.implied_upside:+.1f}%)\n", style="bold")
        analyst_text.append(f"Buy: {mi.buy_ratings} | Hold: {mi.hold_ratings} | Sell: {mi.sell_ratings} | Price Range: {fmt(mi.target_low)}-{fmt(mi.target_high)}\n")
        console.print(Panel(analyst_text, title="[bold]ANALYST CONSENSUS[/bold]", border_style="blue", box=box.ROUNDED))
    
    # Insider Activity
    ins = result.insider
    # Skip insider for ETFs (check fundamental flag)
    is_etf = hasattr(result.fundamental, 'is_etf') and result.fundamental.is_etf
    if not is_etf:
        ins_sym = "▲" if ins.insider_sentiment == "BULLISH" else "▼" if ins.insider_sentiment == "BEARISH" else "○"
        ins_color = "green" if ins.insider_sentiment == "BULLISH" else "red" if ins.insider_sentiment == "BEARISH" else "yellow"
        
        ins_text = Text()
        ins_text.append(f"Sentiment: {ins_sym} {ins.insider_sentiment}\n", style=f"bold {ins_color}")
        ins_text.append(f"Recent Buys: {ins.recent_buys} (${ins.buy_value/1000000:.1f}M) | Recent Sells: {ins.recent_sells} (${ins.sell_value/1000000:.1f}M) | Net: ${ins.net_flow:,.0f}\n")
        if ins.key_transactions:
            ins_text.append(f"Notable Transactions:\n", style="bold")
            for txn in ins.key_transactions[:3]:
                ins_text.append(f"  • {txn}\n")
                
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
        pt_table.add_row("Base Target", fmt(v.target_base), f"[{'green' if upside_base > 0 else 'red'}]{upside_base:+.0f}%[/{'green' if upside_base > 0 else 'red'}]", f"Most likely - {v.target_probability:.0f}% probability")
        pt_table.add_row("Aggressive", fmt(v.target_aggressive), f"[{'green' if upside_aggr > 0 else 'red'}]{upside_aggr:+.0f}%[/{'green' if upside_aggr > 0 else 'red'}]", "Bull case, high estimates")
        
        console.print(Panel(pt_table, title=f"[bold]WEIGHTED PRICE TARGETS (12 months) - Confidence: {v.target_confidence:.0f}%[/bold]", border_style="cyan", box=box.ROUNDED))
    
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
    
    # Consensus
    if targets:
        avg_target = sum(t[1] for t in targets) / len(targets)
        avg_upside = ((avg_target / current) - 1) * 100 if current > 0 else 0
        color = "green" if avg_upside > 0 else "red"
        pt_breakdown.add_row("CONSENSUS TARGET", f"[bold]{fmt(avg_target)}[/bold]", f"[bold {color}]{avg_upside:+.1f}%[/bold {color}]", "")
        
    console.print(Panel(pt_breakdown, title="[bold]PRICE TARGET BREAKDOWN[/bold]", border_style="cyan", box=box.ROUNDED))
    
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
        tech_table.add_row("Volume:", f"{t.volume_ratio:>6.1f}x avg", "VWAP:", f"{fmt(t.vwap):>10}")
        
        console.print(Panel(tech_table, title="[bold]TECHNICAL INDICATORS[/bold]", border_style="blue", box=box.ROUNDED))
        
        # Fundamental Metrics Table
        fund_table = Table(show_header=False, box=box.SIMPLE, expand=True)
        fund_table.add_column("Metric1", style="bold")
        fund_table.add_column("Value1")
        fund_table.add_column("Metric2", style="bold")
        fund_table.add_column("Value2")
        
        if hasattr(f, 'is_etf') and f.is_etf:
            # ETF Specific Display
            fund_table.add_row("P/E Ratio:", f"{f.pe_ratio:>6.1f}x [{'Fair' if 15 < f.pe_ratio < 30 else 'High' if f.pe_ratio > 30 else 'Low'}]", "Yield:", f"{f.dividend_yield*100:>9.2f}%")
            fund_table.add_row("Net Assets:", f"{fmt(f.net_assets):>10}", "Expense Ratio:", f"{f.expense_ratio*100:>9.2f}%")
            fund_table.add_row("Structure:", f"Exchange Traded Fund (ETF)", "", "")
        else:
            # Standard Stock Display
            fund_table.add_row("P/E:", f"{f.pe_ratio:>6.1f}x [{'Fair' if 15 < f.pe_ratio < 30 else 'High' if f.pe_ratio > 30 else 'Low'}]", "P/B:", f"{f.pb_ratio:>10.1f}x")
            fund_table.add_row("ROE:", f"{f.roe:>6.1f}% [{'Strong' if f.roe > 15 else 'Weak'}]", "Debt/Equity:", f"{f.debt_to_equity:>10.2f}")
            fund_table.add_row("Margins:", f"{f.net_margin:>6.1f}% [{'Healthy' if f.net_margin > 15 else 'Weak'}]", "Rev Growth:", f"{f.revenue_growth_yoy:>9.1f}% YoY")
            fund_table.add_row("Piotroski:", f"{_piotroski_text(f):>18}", "Altman Z:", f"{f.altman_z_score:>10.1f}")
        
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
            
            peer_table.add_row("P/E:", f"{pc.pe_stock:>6.1f}x vs {pc.pe_sector_avg:>6.1f}x [{pc.pe_vs_sector:+.0f}% {'cheaper' if pc.pe_vs_sector < 0 else 'pricier'}]")
            peer_table.add_row("ROE:", f"{pc.roe_stock:>6.1f}% vs {pc.roe_sector_avg:>6.1f}% [{abs(pc.roe_vs_sector):+.0f}% {'better' if pc.roe_vs_sector > 0 else 'worse'}]")
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
        
        outlook_text = Text()
        outlook_text.append(f"RECOMMENDATION: {action}\n", style="bold cyan")
        
        if result.signal_explanation and result.signal_explanation.primary_reason:
            outlook_text.append(f"Rationale: {result.signal_explanation.primary_reason}\n")
            
        console.print(Panel(outlook_text, title="[bold]PORTFOLIO OUTLOOK[/bold]", border_style="cyan", box=box.DOUBLE))
