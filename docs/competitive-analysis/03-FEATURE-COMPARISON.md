# 03 — Four-way feature comparison and scores

Legend: **✔** implemented and read in source · **◐** partial · **✎** documented or prose-only ·
**✘** absent. "Read" means the implementation was inspected; it does not mean it was executed
(see 09 for what was run).

## 1. Feature matrix

### Data

| Capability | QUANTA | TradingAgents | OpenBB | Qlib |
|---|---|---|---|---|
| Daily OHLCV | ✔ yfinance + Stooq fallback | ✔ yfinance / Alpha Vantage | ✔ cboe, nasdaq, tmx (no yfinance in repo at this commit) | ✔ own binary store; Yahoo collector |
| Intraday | ◐ 5-min, ~15 min delayed | ✘ | ◐ provider-dependent | ✔ 1-min / 5-min support |
| Fundamentals (current) | ✔ Yahoo `info` + statements | ✔ yfinance, Alpha Vantage, SEC | ✔ SEC XBRL (40 SEC models) | ◐ |
| Point-in-time fundamentals | ✔ SEC XBRL "filed before date" (US, validation only) | ◐ statement knowledge-time tests | ✘ as-reported but no as-of API | ✔ PIT for China (baostock) |
| Point-in-time universe | ✘ static 42-name survivor list | ✘ | ✘ | ✔ index membership with dates |
| Corporate events / 8-K | ✔ fails closed; item-level severity | ◐ SEC statements | ✔ filings, full-text search | ✘ |
| Macro | ◐ FOMC calendar, VIX, SPY trend | ✔ FRED | ✔ FRED, BLS, ECB, IMF, OECD, Fed | ✘ |
| News / social | ◐ headlines + VADER, RSS health | ✔ Yahoo, Alpha Vantage, Reddit, StockTwits, date-windowed | ✔ news extension | ✘ |
| Unified provider interface | ✘ 86 direct `yf` call sites | ◐ vendor router per tool category | ✔ typed `Fetcher` + standard models | ✔ `D.features` expression API |
| Provider failover | ◐ Stooq fallback, Finnhub key rotation, stale-grace cache | ◐ optional categories degrade | ✘ (caller picks provider) | ✘ |
| Rate limiting / single-flight | ✔ token bucket, single-flight, killable bulk child | ◐ retries | ✔ per provider (SEC limiter) | n/a (local) |
| Data freshness on output | ✔ `Freshness`, `SourceRef` on every Decision | ◐ coverage-gap notes | ◐ `warnings` | ✘ |

### Research and decisions

| Capability | QUANTA | TradingAgents | OpenBB | Qlib |
|---|---|---|---|---|
| Single-ticker tear sheet | ✔ | ✔ LLM report (Markdown/HTML) | ◐ data only | ✘ |
| Technical indicators | ✔ (3 duplicate stacks) | ✔ stockstats | ✔ technical extension | ✔ Alpha158 operators |
| Valuation / fair value | ✔ DCF + analyst + sector-aware v4 (justified P/B, FCF yield, Graham) | ✎ LLM prose | ✘ | ✘ |
| Peer comparison | ✔ | ✎ | ✔ peers model | ✘ |
| Earnings follow-through | ✔ earnings report card | ◐ | ✔ calendar, transcripts model | ✘ |
| Deterministic decision object | ✔ one `Decision`, every surface | ◐ typed PM rating | ✘ | ✘ |
| Explicit blockers ("why not") | ✔ coded blocker + remedy | ✘ | ✘ | ✘ |
| Evidence status on each call | ✔ NO_EDGE / UNTESTED flags | ✘ | ✘ | ✘ |
| Probability / EV of trade | ✔ exact barrier model (v4 shadow) | ✘ | ✘ | ✘ |
| Scenarios (bear/base/bull) | ✔ | ✎ | ✘ | ✘ |

### AI

| Capability | QUANTA | TradingAgents | OpenBB | Qlib |
|---|---|---|---|---|
| In-app LLM generation | ✘ | ✔ | ✘ | ✘ |
| Multi-agent orchestration | ✘ | ✔ LangGraph, parallel analysts | ✘ | ✘ (RD-Agent is a separate project) |
| Bull/bear debate | ✘ (rule-based "committee" thresholds only) | ✔ | ✘ | ✘ |
| Structured LLM output | n/a | ✔ Pydantic, with coercion | n/a | n/a |
| Grounding / numeric fact-check of narrative | ✔ `check_narrative` | ✘ | ✘ | ✘ |
| MCP server for external LLMs | ✔ 20+ tools, write tools refused | ✘ | ✔ | ✘ |
| Memory / reflection | ◐ ledger outcomes (no LLM lessons) | ✔ LLM lessons into PM prompt | ✘ | ✘ |
| Multi-provider LLM support | n/a | ✔ 9 providers | n/a | n/a |

### Quant, backtesting, portfolio

| Capability | QUANTA | TradingAgents | OpenBB | Qlib |
|---|---|---|---|---|
| Multi-factor composite | ✔ 3 engines | ✘ | ✘ | ✔ |
| ML return model | ✘ | ✘ | ✘ | ✔ 30+ models |
| Factor library | ◐ hand-built legs | ✘ | ✘ | ✔ Alpha158/360 |
| Cross-sectional IC / quantile spreads | ✔ non-overlapping dates | ✘ | ✘ | ✔ |
| Walk-forward / rolling retrain | ✔ train-only param selection | ✘ | ✘ | ✔ rolling + online |
| Random-entry / random-exit baselines | ✔ | ✘ | ✘ | ✘ |
| HAC t-stats, block bootstrap, deflated Sharpe, FDR | ✔ | ✘ | ◐ OLS p-values | ✘ |
| Trial registry for multiple testing | ✔ | ✘ | ✘ | ◐ MLflow runs |
| Pre-registration + shadow models | ✔ v3, v4 | ✘ | ✘ | ✘ |
| Portfolio backtest with costs/limits | ◐ APEX simulator (fixed after F01) | ✘ (decision grid only) | ✘ | ✔ exchange, limits, nested executors |
| Tamper-evident prospective track record | ✔ hash chain + git timestamps | ◐ memory log | ✘ | ✘ |
| Live holdings | ✔ Robinhood read-only | ◐ static portfolio file | ✘ | ✘ |
| Exposure, VaR/CVaR, Euler risk shares, beta stress | ✔ | ✘ | ✔ risk decomposition | ◐ risk models |
| Portfolio optimisation | ✘ | ✘ | ✘ | ✔ enhanced indexing optimiser |
| Factor attribution | ✘ | ✘ | ✔ OLS on Fama-French | ◐ profit attribution |
| Position sizing from risk budget | ✔ ATR stop, 1% forced-share cap, exposure ceiling | ✎ prose | ✘ | ✔ weight strategies |

### Product and engineering

| Capability | QUANTA | TradingAgents | OpenBB | Qlib |
|---|---|---|---|---|
| Web UI | ✔ Flask + vanilla JS, 7 pages, themes, phone | ◐ CLI (Rich) + HTML report | ◐ API/CLI here; Workspace is a separate product | ✘ (notebooks) |
| Accessibility tests | ✔ axe (WCAG 2.1 AA) in Playwright | ✘ | ✘ | ✘ |
| Alerts / watchlist | ✔ desktop + email | ✘ | ✘ | ✘ |
| Package structure | ✘ path hacks, 15K-line modules | ✔ | ✔ extension packages | ✔ |
| Tests (count) | 2,976 Py + 882 JS + e2e + golden | ~1,009 | 802 test files | ~253 |
| Markets | US, India | Global via suffix map, crypto | Global | China first, US |

## 2. Scores (0-10) and weighted totals

| Category (weight) | QUANTA | TradingAgents | OpenBB | Qlib | Winner |
|---|---:|---:|---:|---:|---|
| Financial data quality and integrations (10%) | 5 | 5 | **9** | 5 | OpenBB |
| Fundamental and technical research (10%) | **8** | 5 | 6 | 2 | QUANTA |
| AI intelligence and agent architecture (12%) | 4 | **8** | 4 | 1 | TradingAgents |
| Quantitative models and algorithms (10%) | 6 | 1 | 4 | **9** | Qlib |
| Backtesting and strategy evaluation (10%) | 7 | 3 | 1 | **9** | Qlib |
| Portfolio intelligence and risk (10%) | **8** | 3 | 5 | 6 | QUANTA |
| Stock discovery and recommendations (8%) | **7** | 2 | 6 | 5 | QUANTA |
| UI/UX, accessibility, usability (8%) | **8** | 5 | 6 | 2 | QUANTA |
| Architecture, code quality, maintainability (8%) | 4 | 7 | **8** | 7 | OpenBB |
| Performance, scalability, reliability (6%) | 6 | 5 | 7 | **8** | Qlib |
| Security, testing, production readiness (5%) | **8** | 7 | **8** | 6 | QUANTA / OpenBB |
| Extensibility and integrations (3%) | 4 | 6 | **10** | 8 | OpenBB |
| **Weighted total** | **6.28** | **4.61** | **5.70** | **5.36** | **QUANTA** |

Sensitivity: the rubric puts 36% on end-user categories (research, portfolio, discovery, UI), which
is QUANTA's product category. Re-weighting all twelve categories equally gives QUANTA 6.25,
OpenBB 6.17, Qlib 5.67, TradingAgents 4.75: QUANTA still leads, by much less. QUANTA would lose the
lead to TradingAgents only if AI carried about 38% or more of the total weight (other weights
scaled down in proportion).

## 3. Per-category justification

Each row: score rationale · strongest/weakest implementation · confidence.

**Data (OpenBB 9 · QUANTA 5 · TA 5 · Qlib 5).** OpenBB's typed `Fetcher` + 181 standard models is
the only real provider abstraction; it has no failover and its consumer price vendors are not in
this monorepo. QUANTA's pacing, single-flight and stale-grace are production-grade, and its PIT
SEC rebuild is rarer than anything here except Qlib's China PIT; but it rests on one free vendor
reached from 86 call sites. Weakest: QUANTA's lack of PIT universe and TradingAgents' reliance on
scraped social data. Confidence: high (QUANTA), medium (OpenBB, sampled).

**Research (QUANTA 8).** Deterministic valuation with a sector-aware v4, peers, scenarios, earnings
card, coded blockers. TradingAgents writes richer prose but nothing it says is computed or checked.
Weakest: Qlib (not its purpose). Confidence: high.

**AI (TA 8 · QUANTA 4).** TradingAgents has the only real agent system: parallel tool-using
analysts, two debates, structured manager outputs, nine LLM providers, point-in-time data windows.
It loses points for an unvalidated LLM rating, no confidence, and no defence against LLM memory of
the future in backtests. QUANTA scores 4 for strong guard rails (grounded interpretation, numeric
narrative checker, MCP with write-tool refusal, skills) but no generation. Confidence: high.

**Quant (Qlib 9 · QUANTA 6).** Qlib: factor library, 30+ models, risk models, optimiser. QUANTA:
three composites, exact barrier EV model, PIT fundamentals; no ML and no validated signal (its own
tests: research v2 IC +0.041 at 63d, t +1.42, not distinguishable from 12-1 momentum).
Confidence: high.

**Backtesting (Qlib 9 · QUANTA 7).** Qlib simulates execution with costs, limits and nested
executors. QUANTA's statistical inference is stronger than Qlib's (HAC t, block bootstrap,
deflated Sharpe, FDR, random baselines, trial registry, pre-registration), but its universe is 42
present-day survivors and it does not replay the live decision code (README, Known limitations).
Confidence: high.

**Portfolio and risk (QUANTA 8).** Live read-only holdings, exposure, Euler risk shares, VaR/CVaR,
worst window, beta stress, earnings exposure, what-if, guidance per holding. Missing optimisation
and factor attribution, which OpenBB (attribution) and Qlib (optimiser) have. Confidence: high.

**Discovery (QUANTA 7).** Funnel with regime, liquidity, scoring, book gates and a reason for every
excluded name. Weakest point: Discover's default sort by consensus upside. OpenBB 6 for screener
and discovery endpoints without ranking. Confidence: high.

**UI/UX (QUANTA 8).** Seven pages, light/dark, phone width, axe tests, alerts. Weak: 820 KB
unminified `app.js`, My Portfolio scripts loaded on every page (README). Confidence: medium
(rendered UI not exercised in this audit; e2e Not Verified).

**Architecture (OpenBB 8 · QUANTA 4).** QUANTA's `core/engine` decision core is clean and
well-tested, but it sits beside three 6-15K-line engines with duplicated indicators and regimes,
a 9.5K-line Flask module, a 15K-line `app.js`, no package structure, 123 unused imports and
silently optional imports. Confidence: high.

**Performance (Qlib 8).** Qlib's binary columnar store and expression cache are built for
universes of thousands. QUANTA measured offline: sub-2 ms p95 for config/ledger endpoints (09);
cold dashboard ~9-11 s by deliberate throttle (README, not re-measured). Confidence: medium.

**Security and testing (QUANTA 8 · OpenBB 8).** QUANTA: 3,858 passing unit tests run here,
loopback-only, write-tool refusal, redaction; two pinned dependencies with advisories.
Competitor suites not executed. Confidence: high for QUANTA, low-medium for the others.

**Extensibility (OpenBB 10).** Provider and extension packages are first-class; QUANTA has MCP
and skills but no plugin seams. Confidence: high.

## 4. Scoring by intended product category

| Repository | Category | Judged against its own purpose |
|---|---|---|
| QUANTA | Single-user research and decision workstation | Strong: the only one of the four that turns data into a sized, explained, recorded decision. Its own evidence says the ranking has no demonstrated edge. |
| TradingAgents | LLM multi-agent research framework | Strong as a framework; its outputs are unvalidated opinions, and its backtest is contaminated by LLM memory for dates before the model's cutoff. |
| OpenBB | Data platform and SDK | Best-in-class breadth and abstraction; deliberately no opinions, no decisions. |
| Qlib | Quant ML research platform | Best-in-class research loop; China-market defaults; no product surface. |
