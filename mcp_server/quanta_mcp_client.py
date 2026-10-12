#!/usr/bin/env python3
"""
QUANTA MCP Client — Interactive CLI
====================================
Standalone client that connects to the QUANTA MCP Server and exposes
all 10 tools via an interactive command-line interface.

Usage:
    python quanta_mcp_client.py

This is useful for testing the MCP server without VS Code.
"""

import asyncio
import json
import re
import sys
import os

# ── ANSI color helpers (work on Windows Terminal / PowerShell) ────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
MAGENTA = "\033[35m"
WHITE   = "\033[97m"
BG_DARK = "\033[48;5;236m"

# Box-drawing constants
BOX_H  = "─"
BOX_V  = "│"
BOX_TL = "┌"
BOX_TR = "┐"
BOX_BL = "└"
BOX_BR = "┘"
BOX_LT = "├"
BOX_RT = "┤"
BOX_HD = "┬"
BOX_HU = "┴"
BOX_X  = "┼"

WIDTH = 72  # Default terminal panel width


def _strip_ansi(text: str) -> int:
    """Return visible length of a string (ignoring ANSI codes)."""
    return len(re.sub(r'\033\[[0-9;]*m', '', text))


def _box_line(left: str, fill: str, right: str, width: int = WIDTH) -> str:
    return f"{DIM}{left}{fill * (width - 2)}{right}{RESET}"


def _center(text: str, width: int = WIDTH) -> str:
    vis = _strip_ansi(text)
    pad = max(0, width - 2 - vis)
    left_pad = pad // 2
    right_pad = pad - left_pad
    return f"{DIM}{BOX_V}{RESET}{' ' * left_pad}{text}{' ' * right_pad}{DIM}{BOX_V}{RESET}"


def _row(text: str, width: int = WIDTH) -> str:
    vis = _strip_ansi(text)
    pad = max(0, width - 4 - vis)
    return f"{DIM}{BOX_V}{RESET}  {text}{' ' * pad}{DIM}{BOX_V}{RESET}"


def _table_row(cells: list, col_widths: list) -> str:
    """Render a row of a table with fixed column widths."""
    parts = []
    for cell, w in zip(cells, col_widths):
        vis = _strip_ansi(cell)
        pad = max(0, w - vis)
        parts.append(f" {cell}{' ' * pad} ")
    inner = f"{DIM}{BOX_V}{RESET}".join(parts)
    return f"{DIM}{BOX_V}{RESET}{inner}{DIM}{BOX_V}{RESET}"


def _table_separator(col_widths: list, left: str = BOX_LT, mid: str = BOX_X,
                     right: str = BOX_RT) -> str:
    segs = [BOX_H * (w + 2) for w in col_widths]
    return f"{DIM}{left}{mid.join(segs)}{right}{RESET}"


def _colorize_pct(text: str) -> str:
    """Color a percentage string green/red based on sign."""
    if not text:
        return text
    clean = text.strip().replace('%', '').replace('+', '')
    try:
        v = float(clean)
        color = GREEN if v >= 0 else RED
        return f"{color}{text}{RESET}"
    except ValueError:
        return text


def _colorize_label(label: str) -> str:
    """Color recommendation/valuation labels."""
    upper = label.upper()
    if any(w in upper for w in ("STRONG_BUY", "UNDERVALUED", "BUY")):
        return f"{GREEN}{BOLD}{label}{RESET}"
    elif any(w in upper for w in ("SELL", "AVOID", "OVERVALUED")):
        return f"{RED}{BOLD}{label}{RESET}"
    elif any(w in upper for w in ("HOLD", "FAIRLY")):
        return f"{YELLOW}{label}{RESET}"
    return f"{CYAN}{label}{RESET}"


def render_markdown(md: str) -> str:
    """
    Convert the Markdown output from MCP tool calls into a clean,
    box-drawn terminal display with colors and aligned tables.
    """
    output_lines: list[str] = []
    md_lines = md.strip().split('\n')
    i = 0

    while i < len(md_lines):
        line = md_lines[i].rstrip()

        # ── H1: Title banner ─────────────────────────────────────────────
        if line.startswith('# '):
            title = line[2:].strip()
            output_lines.append("")
            output_lines.append(_box_line(BOX_TL, BOX_H, BOX_TR))
            output_lines.append(_center(f"{BOLD}{CYAN}{title}{RESET}"))
            output_lines.append(_box_line(BOX_BL, BOX_H, BOX_BR))
            i += 1
            continue

        # ── H2: Section header ───────────────────────────────────────────
        if line.startswith('## '):
            section = line[3:].strip()
            output_lines.append("")
            output_lines.append(_box_line(BOX_TL, BOX_H, BOX_TR))
            output_lines.append(_row(f"{BOLD}{YELLOW}{section}{RESET}"))
            output_lines.append(_box_line(BOX_LT, BOX_H, BOX_RT))
            # Peek ahead to collect section body
            i += 1
            section_body: list[str] = []
            while i < len(md_lines):
                nxt = md_lines[i].rstrip()
                if nxt.startswith('## ') or nxt.startswith('# '):
                    break
                section_body.append(nxt)
                i += 1

            in_table = False
            table_rows: list[list[str]] = []

            for bline in section_body:
                # Table row
                if bline.startswith('|'):
                    cells = [c.strip() for c in bline.split('|')[1:-1]]
                    # Skip separator rows ( |---|---|--- )
                    if cells and all(set(c) <= set('-: ') for c in cells):
                        in_table = True
                        continue
                    in_table = True
                    table_rows.append(cells)
                    continue

                # Flush any pending table
                if in_table and table_rows:
                    output_lines.extend(_render_table(table_rows))
                    table_rows = []
                    in_table = False

                # Bold key-value:  **Key:** value
                kv = re.match(r'^\*\*(.+?)\*\*[:\s]*(.*)$', bline.strip())
                if kv:
                    key, val = kv.group(1).rstrip(':'), kv.group(2).strip()
                    val = _colorize_pct(val) if '%' in val else val
                    output_lines.append(_row(f"{WHITE}{key}:{RESET}  {val}"))
                    continue

                # Bullet: - **Key:** Value
                bullet = re.match(r'^[-*]\s+\*\*(.+?)\*\*[:\s]*(.*)$', bline.strip())
                if bullet:
                    key, val = bullet.group(1).rstrip(':'), bullet.group(2).strip()
                    # Colorize percentages in parentheses
                    val = re.sub(r'\(([+-][\d.]+%)\)',
                                 lambda m: f"({_colorize_pct(m.group(1))})", val)
                    val = _colorize_pct(val) if re.match(r'^[+-]?[\d.]+%$', val) else val
                    if any(w in key.upper() for w in ("LABEL",)):
                        val = _colorize_label(val)
                    output_lines.append(_row(f"  {CYAN}•{RESET} {WHITE}{key}:{RESET}  {val}"))
                    continue

                # Plain bullet
                plain_bullet = re.match(r'^[-*]\s+(.+)$', bline.strip())
                if plain_bullet:
                    content = plain_bullet.group(1)
                    content = re.sub(r'\*\*(.+?)\*\*', f'{BOLD}\\1{RESET}', content)
                    output_lines.append(_row(f"  {DIM}•{RESET} {content}"))
                    continue

                # Blank line
                if not bline.strip():
                    continue

                # Italics / misc
                content = bline.strip()
                content = re.sub(r'\*\*(.+?)\*\*', f'{BOLD}\\1{RESET}', content)
                content = re.sub(r'\*(.+?)\*', f'{DIM}\\1{RESET}', content)
                if content:
                    output_lines.append(_row(content))

            # Flush trailing table
            if table_rows:
                output_lines.extend(_render_table(table_rows))

            output_lines.append(_box_line(BOX_BL, BOX_H, BOX_BR))
            continue

        # ── Bold standalone (like **Current Price:** $264.18) ────────────
        kv_top = re.match(r'^\*\*(.+?)\*\*[:\s]*(.+)$', line.strip())
        if kv_top:
            key, val = kv_top.group(1).rstrip(':'), kv_top.group(2).strip()
            output_lines.append(f"  {WHITE}{key}:{RESET}  {CYAN}{val}{RESET}")
            i += 1
            continue

        # Skip blank lines at top level
        if not line.strip():
            i += 1
            continue

        # Fallback: print as-is
        output_lines.append(f"  {line}")
        i += 1

    output_lines.append("")
    return "\n".join(output_lines)


def _render_table(rows: list[list[str]]) -> list[str]:
    """Render collected table rows with box-drawing and colors."""
    if not rows:
        return []

    # Determine column widths (from raw text, before coloring)
    num_cols = max(len(r) for r in rows)
    col_widths = [0] * num_cols
    for r in rows:
        for ci, cell in enumerate(r):
            col_widths[ci] = max(col_widths[ci], len(cell))

    # Clamp total width
    total = sum(w + 3 for w in col_widths) + 1
    if total > WIDTH - 4:
        # Scale columns down proportionally
        scale = (WIDTH - 6) / total
        col_widths = [max(4, int(w * scale)) for w in col_widths]

    out: list[str] = []
    out.append(f"  {_table_separator(col_widths, BOX_TL, BOX_HD, BOX_TR)}")

    for ri, row in enumerate(rows):
        styled: list[str] = []
        for ci, cell in enumerate(row):
            # Header row: bold
            if ri == 0:
                styled.append(f"{BOLD}{WHITE}{cell}{RESET}")
            else:
                # Colorize percentages
                if re.match(r'^[+-][\d.]+%$', cell.strip()):
                    styled.append(_colorize_pct(cell))
                elif cell.strip().upper() in ("STRONG_BUY", "BUY", "SELL",
                    "AVOID", "HOLD", "ACCUMULATE", "UNDERVALUED",
                    "OVERVALUED", "FAIRLY_VALUED"):
                    styled.append(_colorize_label(cell))
                else:
                    styled.append(cell)
        # Pad row if fewer cells
        while len(styled) < num_cols:
            styled.append("")

        out.append(f"  {_table_row(styled, col_widths)}")

        # Separator after header
        if ri == 0:
            out.append(f"  {_table_separator(col_widths, BOX_LT, BOX_X, BOX_RT)}")

    out.append(f"  {_table_separator(col_widths, BOX_BL, BOX_HU, BOX_BR)}")
    return out


def _print_result(md: str) -> None:
    """Render and print an MCP tool result."""
    print(render_markdown(md))


async def test_server():
    """Interactive CLI loop to test all QUANTA MCP Server tools."""
    # Add paths
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from quanta_mcp_server import (
        analyze_stock, quick_scan, market_environment,
        catalyst_scan, price_targets, monte_carlo_sim,
        compare_stocks, position_advisor, compliance_check,
        portfolio_risk_check,
    )

    print("")
    print(_box_line(BOX_TL, BOX_H, BOX_TR))
    print(_center(f"{BOLD}{CYAN}QUANTA MCP Server — Interactive Client{RESET}"))
    print(_box_line(BOX_BL, BOX_H, BOX_BR))

    tools = {
        '1':  ('analyze_stock',       'Full stock analysis'),
        '2':  ('quick_scan',          'Multi-ticker scan'),
        '3':  ('market_environment',  'Market dashboard'),
        '4':  ('catalyst_scan',       'Catalyst detection'),
        '5':  ('price_targets',       'Price targets'),
        '6':  ('monte_carlo_sim',     'Monte Carlo simulation'),
        '7':  ('compare_stocks',      'Compare stocks'),
        '8':  ('position_advisor',    'Position advice'),
        '9':  ('compliance_check',    'Compliance check'),
        '10': ('portfolio_risk_check','Portfolio risk'),
    }

    while True:
        print(f"\n{BOLD}Available tools:{RESET}")
        for key, (name, desc) in tools.items():
            print(f"  {CYAN}[{key:>2}]{RESET}  {WHITE}{name:<25}{RESET} {DIM}{desc}{RESET}")
        print(f"  {CYAN}[ q]{RESET}  {WHITE}{'Quit':<25}{RESET}")

        choice = input(f"\n{YELLOW}▸ Select tool:{RESET} ").strip()

        if choice.lower() in ('q', 'quit', 'exit'):
            print(f"\n{DIM}Goodbye!{RESET}\n")
            break

        try:
            if choice == '1':
                ticker = input(f"{YELLOW}  Ticker:{RESET} ").strip().upper()
                print(f"\n{DIM}Analyzing {ticker}…{RESET}")
                _print_result(analyze_stock(ticker))

            elif choice == '2':
                tickers = input(f"{YELLOW}  Tickers (comma-separated):{RESET} ").strip()
                print(f"\n{DIM}Scanning…{RESET}")
                _print_result(quick_scan(tickers))

            elif choice == '3':
                print(f"\n{DIM}Fetching market environment…{RESET}")
                _print_result(market_environment())

            elif choice == '4':
                ticker = input(f"{YELLOW}  Ticker:{RESET} ").strip().upper()
                print(f"\n{DIM}Scanning catalysts for {ticker}…{RESET}")
                _print_result(catalyst_scan(ticker))

            elif choice == '5':
                ticker = input(f"{YELLOW}  Ticker:{RESET} ").strip().upper()
                print(f"\n{DIM}Calculating targets for {ticker}…{RESET}")
                _print_result(price_targets(ticker))

            elif choice == '6':
                ticker = input(f"{YELLOW}  Ticker:{RESET} ").strip().upper()
                print(f"\n{DIM}Running Monte Carlo for {ticker}…{RESET}")
                _print_result(monte_carlo_sim(ticker))

            elif choice == '7':
                tickers = input(f"{YELLOW}  Tickers to compare (comma-separated):{RESET} ").strip()
                print(f"\n{DIM}Comparing…{RESET}")
                _print_result(compare_stocks(tickers))

            elif choice == '8':
                ticker = input(f"{YELLOW}  Ticker:{RESET} ").strip().upper()
                shares = int(input(f"{YELLOW}  Shares:{RESET} ").strip())
                avg_cost = float(input(f"{YELLOW}  Avg cost:{RESET} ").strip())
                print(f"\n{DIM}Analyzing position…{RESET}")
                _print_result(position_advisor(ticker, shares, avg_cost))

            elif choice == '9':
                ticker = input(f"{YELLOW}  Ticker:{RESET} ").strip().upper()
                print(f"\n{DIM}Running compliance check…{RESET}")
                _print_result(compliance_check(ticker))

            elif choice == '10':
                print(f"  {DIM}Enter positions as JSON array:{RESET}")
                print(f'  {DIM}Example: [{{"ticker":"AAPL","shares":50,"avg_cost":175,"sector":"Technology"}}]{RESET}')
                positions = input(f"{YELLOW}  Positions JSON:{RESET} ").strip()
                print(f"\n{DIM}Evaluating portfolio risk…{RESET}")
                _print_result(portfolio_risk_check(positions))

            else:
                print(f"{RED}Invalid choice{RESET}")

        except KeyboardInterrupt:
            print(f"\n{DIM}Cancelled.{RESET}")
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")


if __name__ == "__main__":
    # Enable ANSI on Windows
    if sys.platform == "win32":
        os.system("")  # triggers VT100 mode
    asyncio.run(test_server())
