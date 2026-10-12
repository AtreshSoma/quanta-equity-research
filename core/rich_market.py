from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.layout import Layout
from rich.align import Align
from rich import box
from typing import Dict, Any

def display_market_environment(env: Dict[str, Any], sym: str = "$") -> None:
    """Display compact market environment summary using rich"""
    console = Console()
    
    if not env.get('fetch_success', False):
        console.print(Panel("[bold red]Market data unavailable[/bold red]", title="[bold]MARKET ENVIRONMENT[/bold]", border_style="red", box=box.ROUNDED))
        return
    
    # ── Market snapshot: SPY + VIX + Sentiment ──
    trend_sym = "▲" if "UPTREND" in env['spy_trend'] else ("▼" if "DOWNTREND" in env['spy_trend'] else "─")
    trend_color = "green" if "UPTREND" in env['spy_trend'] else ("red" if "DOWNTREND" in env['spy_trend'] else "yellow")
    trend_label = env['spy_trend'].split('(')[0].strip() if '(' in env['spy_trend'] else env['spy_trend']
    vix_label = "Low" if env['vix'] < 15 else ("Normal" if env['vix'] < 20 else ("Elevated" if env['vix'] < 30 else "High"))
    vix_color = "green" if env['vix'] < 15 else ("yellow" if env['vix'] < 20 else "red")
    
    market_table = Table(show_header=False, box=None, expand=True)
    market_table.add_column("Metric", style="bold")
    market_table.add_column("Value")
    market_table.add_column("Metric2", style="bold")
    market_table.add_column("Value2")
    
    market_table.add_row("SPY:", f"{sym}{env['spy_price']:.2f} [{trend_color}]{trend_sym} {env['spy_change_pct']:+.2f}%[/{trend_color}]", "VIX:", f"[{vix_color}]{env['vix']:.1f} ({vix_label})[/{vix_color}]")
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
            parts.append(f"[green]▲ {n} {p:+.1f}%[/green]")
        for n, p in (laggards[:2] if laggards else []):
            parts.append(f"[red]▼ {n} {p:+.1f}%[/red]")
        market_table.add_row("Sectors:", f"{'   '.join(parts)}", "", "")
        
    console.print(Panel(market_table, title="[bold]MARKET ENVIRONMENT[/bold]", border_style="blue", box=box.ROUNDED))
