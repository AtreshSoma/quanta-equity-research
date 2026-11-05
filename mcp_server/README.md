# QUANTA Elite MCP Server

Institutional-grade market intelligence exposed via **Model Context Protocol** for VS Code Agent Mode.

## 22 MCP Tools

### Text reports

| # | Tool | Description |
|---|------|-------------|
| 1 | `analyze_stock` | Full multi-factor analysis (technical + fundamental + sentiment + risk + thesis) |
| 2 | `quick_scan` | Rapid multi-ticker screening with ranked comparison table |
| 3 | `market_environment` | Real-time macro dashboard (SPY, VIX, Fed, sectors, mega-cap earnings) |
| 4 | `portfolio_risk_check` | Portfolio VaR, sector concentration, correlation risk analysis |
| 5 | `compliance_check` | SEBI circuit breakers, wash-sale rules; PDT is account-equity only (QUANTA never reads your orders) |
| 6 | `catalyst_scan` | News-driven catalyst detection with volume confirmation |
| 7 | `price_targets` | DCF + analyst + Monte Carlo composite price targets |
| 8 | `monte_carlo_sim` | Monte Carlo price simulation with probability distributions |
| 9 | `compare_stocks` | Side-by-side multi-stock comparison across all metrics |
| 10 | `position_advisor` | Personalized P&L-aware position management advice |

### Structured JSON (preferred by agents)

| # | Tool | Description |
|---|------|-------------|
| 11 | `get_decision` | The unified Decision object with a grounded interpretation |
| 12 | `explain_decision` | Plain-language explanation built only from Decision fields |
| 13 | `verify_narrative` | Flags numbers or claims in a summary that the Decision does not support |
| 14 | `plan_check` | Pre-trade checklist for a user's own entry/stop/target |
| 15 | `plan_simulate` | Bootstrap and historical replay of a plan's stop/target path |
| 16 | `material_filings` | Recent material SEC filings (8-K items) for a ticker |
| 17 | `portfolio_tail_risk` | VaR / CVaR for a set of positions |

### My list and alerts (the only tools that write, to the local SQLite list)

| # | Tool | Description |
|---|------|-------------|
| 18 | `my_list` | Show the saved list and its alerts |
| 19 | `my_list_add` | Add a symbol |
| 20 | `my_list_remove` | Remove a symbol |
| 21 | `set_price_alert` | Alert when a price crosses above or below a level |
| 22 | `delete_price_alert` | Remove an alert |

QUANTA never places orders; no tool here can.

## Setup

### 1. Install MCP SDK
```bash
pip install "mcp[cli]"
```

### 2. VS Code Configuration
`.vscode/` is git-ignored, so create `.vscode/mcp.json` yourself, pointing a stdio server at
`python mcp_server/quanta_mcp_server.py`. Then, in VS Code:
1. Open Agent mode (Copilot Chat → Agent)
2. The QUANTA tools will appear automatically
3. Ask: *"Analyze NVDA using the QUANTA system"*

### 3. Test Standalone
```bash
cd mcp_server
python quanta_mcp_client.py
```

## Usage Examples (VS Code Agent Mode)

**Single Stock Analysis:**
> "Run a full QUANTA analysis on AAPL"

**Watchlist Scan:**
> "Scan NVDA, AMD, AVGO, QCOM, MRVL and rank them"

**Market Check:**
> "What's the current market environment?"

**Portfolio Review:**
> "Check my portfolio risk: I hold 50 AAPL at $175, 30 MSFT at $380, 100 AMD at $120"

**Position Advice:**
> "I hold 200 shares of TSLA at $220. What should I do?"

**Compare:**
> "Compare AAPL vs MSFT vs GOOGL side by side"

**Catalyst Hunt:**
> "Scan NVDA for any recent catalysts or news events"

## Architecture

```
VS Code Agent Mode
       │
       ▼ (stdio / JSON-RPC)
┌──────────────────────────┐
│  QUANTA MCP Server       │
│  ┌────────────────────┐  │
│  │  22 MCP Tools      │  │
│  │  2 Resources       │  │
│  │  2 Prompts         │  │
│  └────────┬───────────┘  │
│           │              │
│  ┌────────▼───────────┐  │
│  │ QuantaEliteAnalyzer │  │
│  │  (lazy-loaded)      │  │
│  └────────┬───────────┘  │
│           │              │
│  ┌────────▼───────────┐  │
│  │  Sub-Analyzers     │  │
│  │  • Technical       │  │
│  │  • Fundamental     │  │
│  │  • Risk            │  │
│  │  • Sentiment       │  │
│  │  • Valuation       │  │
│  │  • Monte Carlo     │  │
│  │  • Catalyst        │  │
│  │  • Compliance      │  │
│  │  • Portfolio Risk  │  │
│  └────────────────────┘  │
└──────────────────────────┘
       │
       ▼
  Yahoo Finance / Polygon.io / FMP / Finnhub APIs
```
