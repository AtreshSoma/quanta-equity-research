# 02 — Codebase audit

Audit date: 2026-11-06. Every claim below was read in source at these commits unless marked
**Inferred** or **Not Verified**.

| Repository | Commit | Commit date | Licence | Size inspected |
|---|---|---|---|---|
| QUANTA (`AtreshSoma/quanta-equity-research`) | `ce1e9b42ab55` | 2026-11-04 (track-record publish) | MIT | ~177K lines (Python + JS + HTML/CSS, incl. tests) |
| TradingAgents (`TauricResearch/TradingAgents`) | `9fa1196fffbc` | 2026-11-04 | Apache-2.0 | ~13.4K lines non-test Python, 90 test files |
| OpenBB (`OpenBB-finance/OpenBB`) | `2a392e890f6e` | 2026-11-02 | Apache-2.0 (whole repo, per `LICENSE`) | 2,967 `.py` files, ~867K lines; read the core provider layer, SEC/Fama-French providers, quantitative extension |
| Qlib (`microsoft/qlib`) | `543552324638` | 2026-10-08 | MIT | ~57K lines in `qlib/`; read data handlers, backtest exchange, strategies, risk models, evaluate, benchmarks |

Competitor clones were shallow (`--depth 1`); history-based claims about them are out of scope.

**Coverage gaps.** OpenBB was sampled, not read in full (867K lines): the provider abstraction,
query executor, OBBject, standard models, SEC and Fama-French providers, and the quantitative
extension were read; the CLI, desktop app, charting and most of the 20+ providers were not.
Qlib's RL module (`qlib/rl`), meta-learning and most neural model files were not read beyond
their names and the benchmark table.

---

## 1. QUANTA

### 1.1 Shape of the system

```
webapp/app.py (9,553 lines, Flask)  ─┐
webapp/static/app.js (15,022 lines) ─┤  UI → /api/*
webapp/*_routes.py, my_list.py      ─┘
        │
        ├─ core/TradingAnalysis_ELITE.py (14,989)  Research engine ("elite")
        ├─ core/SP_ELITE_PRO.py (15,362)           Picks engine ("elite_pro")
        ├─ core/SP_ShortTerm.py (6,650)            Short-term scanner / CLI
        ├─ core/Alpha_Picks_Engine.py (986)        Analyst/fundamental screen (standalone)
        │
        ├─ core/engine/       (≈45 files) shared decision core: Decision, thresholds, blockers,
        │                     levels, risk grade, corporate events, calendars, EV, fair value v4
        ├─ core/portfolio/ + core/brokers/   read-only Robinhood via MCP, analytics, guidance
        ├─ core/tracking/     signal ledger, code fingerprint, publish/verify, prospective IC
        ├─ core/apex_quant/   walk-forward harness, OOS IC, baselines, PIT fundamentals, stats
        ├─ modules/           20 services (6 unwired, per docs/OPEN_ITEMS.md)
        └─ mcp_server/        MCP server: 20+ tools for LLM clients
```

### 1.2 Traced execution paths

**Research tear sheet** — UI → `POST /api/analyze` ([webapp/app.py:8251](../../webapp/app.py#L8251))
→ job runner (`/api/jobs/<id>`, `/stream` at [app.py:8921-8989](../../webapp/app.py#L8921)) →
`QuantaEliteAnalyzer` ([core/TradingAnalysis_ELITE.py:8327](../../core/TradingAnalysis_ELITE.py#L8327))
→ analyzers (Technical 2079, Fundamental 3056, Risk 3464, Sentiment 3907, Insider 4467,
Catalyst 4688, Valuation 5013, MonteCarlo 5384, Peer 5518) → `SignalGenerator.generate`
([5753](../../core/TradingAnalysis_ELITE.py#L5753)) with regime weights
([`_get_regime_weights` 6002](../../core/TradingAnalysis_ELITE.py#L6002)) → shared
`Decision` ([core/engine/decision.py:127](../../core/engine/decision.py#L127)) → shadow models v3
and v4 ([core/engine/research_v4.py](../../core/engine/research_v4.py)) → ledger
(`core/tracking/ledger.py`) → JSON to `app.js`. Data: yfinance through the rate-limited,
single-flight cache ([app.py:315-357](../../webapp/app.py#L315)); SEC EDGAR for 8-K events
(`core/engine/corporate_events.py`). **Implemented and tested.**

**Picks scan** — UI → `POST /api/stock-picks` ([app.py:6482](../../webapp/app.py#L6482)) →
`QuantaEliteTradingSystem` ([core/SP_ELITE_PRO.py:6750](../../core/SP_ELITE_PRO.py#L6750)) →
`ScoringEngine` (5811), `MarketRegimeDetector` (5252) → book gates (sector cap, correlation,
max positions) → *deep confirmation*: every Buy/Accumulate re-run through the Research engine →
one `Decision` per name, with a primary blocker for every unfunded name
([core/engine/blockers.py](../../core/engine/blockers.py)). Bulk downloads run in a killable child
process ([core/engine/bounded.py](../../core/engine/bounded.py)). **Implemented and tested.**

**Discover** — `GET /api/discover/enrich` ([app.py:5512](../../webapp/app.py#L5512)) →
[webapp/discover_enrich.py](../../webapp/discover_enrich.py) (`TTLStore`, tape reads, earnings
session detection) → ranked Street candidates by consensus upside, floors of 10 analysts and a
50% upside cap. **Implemented.** Ranking by consensus upside is an owner-acknowledged weakness
(stale targets rise to the top; `docs/OPEN_ITEMS.md` #3).

**My Portfolio** — `webapp/portfolio_routes.py` → `core/portfolio/service.py` →
`RobinhoodProvider` ([core/brokers/robinhood.py](../../core/brokers/robinhood.py)) over the
official Robinhood MCP endpoint (`MCP_URL`, line 41) with OAuth PKCE (`core/brokers/oauth.py`);
only tools in `READ_TOOLS` (line 46) can be called and `is_write_tool`
([core/brokers/mcp_http.py:54](../../core/brokers/mcp_http.py#L54)) refuses anything that looks
like an order. Analytics in [core/portfolio/analytics.py](../../core/portfolio/analytics.py):
value-weighted beta (123), annualised vol + Euler risk shares + correlated pairs (139),
historical 1-day 95% VaR/CVaR, worst 21-session window and beta stress (193). **Implemented.**
Charts replay today's holdings over past prices (stated in the README; not account returns).

**Evidence / track record** — `GET /api/track-record` ([app.py:6332](../../webapp/app.py#L6332))
→ ledger summaries; `core/tracking/publish.py` exports `track_record/ledger.csv` and the
hash chain `chain.jsonl`; `python -m tracking verify` detects edits.
`core/tracking/prospective_ic.py` implements the pre-registered per-date rank-IC test.
**Implemented.**

**Validation harness** — `core/apex_quant/run.py` →
`run_walk_forward` ([walkforward.py:143](../../core/apex_quant/walkforward.py#L143)): parameter
grid chosen on train window only, applied to test, compared with SPY buy-and-hold, random entries
and random exits over the same windows; trials registered for deflated Sharpe
(`trials.py`). Statistics in [stats.py](../../core/apex_quant/stats.py): circular block bootstrap
(65), Newey-West t (88), deflated Sharpe (137), Benjamini-Hochberg (152). Cross-sectional IC with
non-overlapping dates in `elite_oos.compute_ic` (286). Point-in-time SEC XBRL fundamentals,
"filed before date" only, in `pit_fundamentals.py`. **Implemented.**

**LLM integration** — There is **no in-app LLM call** (no Anthropic/OpenAI client in `core/`,
`modules/`, `webapp/`). The AI surface is:
- `mcp_server/quanta_mcp_server.py`: 20+ `@mcp.tool()` functions (`get_decision` 1646,
  `explain_decision` 1677, `verify_narrative` 1696, `plan_check`, `plan_simulate`,
  `material_filings`, `portfolio_tail_risk`, `monte_carlo_sim`, `compare_stocks`, My list and alerts).
- `core/engine/interpretation.py`: `build_interpretation` (55) writes sentences that each cite the
  Decision fields they came from; `check_narrative` (211) flags any number in external text that
  the payload does not contain and any buy/sell word that contradicts the label.
- `skills/quanta-research`, `quanta-trade-plan`, `quanta-portfolio-risk`: Claude skills that
  require `verify_narrative` before showing a summary.

This is a grounding and guard-rail layer for an external LLM, not an agent system.

### 1.3 Data layer

- Primary: yfinance (Yahoo), Stooq fallback; SEC EDGAR (`QUANTA_SEC_CONTACT` required; fails
  closed); optional Finnhub (multi-key rotation), Polygon, FMP, Alpha Vantage.
- Pacing: process-wide token bucket (8 burst, 1 per 0.35 s), single-flight per symbol, 6-hour
  stale grace, disk cache (info 1 h, history 15 min).
- **No provider abstraction.** Measured: 83 direct `yf.Ticker(`/`yf.download(` call sites across
  24 files (25 in `TradingAnalysis_ELITE.py`, 8 each in `SP_ELITE_PRO.py` and
  `Alpha_Picks_Engine.py`, 7 in `webapp/app.py`, 7 in `SP_ShortTerm.py`). Throttling works because
  it is installed at the HTTP layer, not because calls go through one gateway.
- Provenance: `Decision.sources` (`SourceRef` with provider, as_of, retrieved_at) and
  `Freshness` exist in the schema ([decision.py:50-80](../../core/engine/decision.py#L50)).

### 1.4 Code quality

| Measure | Value | Source |
|---|---|---|
| Largest files | `SP_ELITE_PRO.py` 15,362; `app.js` 15,022; `TradingAnalysis_ELITE.py` 14,989; `app.py` 9,553; `quanta.css` 8,820 | `wc -l` |
| Duplicate implementations | RSI ×3 (`SP_ELITE_PRO.py:4642`, `apex_quant/indicators.py:14`, `engine/indicators.py:35`); ATR ×3; `MarketRegime` ×3 + `MarketRegimeDetector` | grep; parity is pinned by `tests/test_indicator_parity.py` |
| Packaging | Not a package; `sys.path` inserts and `pyproject` `pythonpath` | `pyproject.toml` header |
| Lint gate | `ruff check .` passes (errors-only rule set) | run 2026-11-06 |
| Unused imports | **123** F401 (README and `pyproject.toml` say 107: the count has drifted) | `ruff --select F401` |
| Optional imports | `_import_optional` ([TradingAnalysis_ELITE.py:99](../../core/TradingAnalysis_ELITE.py#L99)) loads 7 `modules/` services; a missing module silently changes engine behaviour | read |
| Unwired code | 6 modules + `core/quant/` (~700 lines) imported by nothing | `docs/OPEN_ITEMS.md` |

### 1.5 Testing and CI

Measured on this machine (macOS, Python 3.12.4, isolated venv with `webapp/constraints.txt` pins):

- `pytest -m "not golden"`: **2,976 passed**, 18 skipped, 4 xfailed (owner-pending insider
  calibration), 104 s. (A first run showed 6 ledger failures; they were caused by my exporting
  `QUANTA_TRACKING_DISABLED=1`, which disables recording. The 63 tests in those files pass without it.)
- `npm test` (node:test + jsdom): **882 / 882 passed**, 11.8 s. The README says 746: stale.
- Golden master, Playwright e2e: **Not Verified** (see 09).
- CI (`.github/workflows/ci.yml`): python, javascript, golden (pinned), advisory audit, browser subset.

### 1.6 Security

- Loopback-only server (non-loopback host refused), security headers
  (`tests/test_web_security_headers.py`), request guard (`webapp/request_guard.py`), credential
  redaction (`core/brokers/redact.py`), host allow-list for broker calls (`core/brokers/security.py`),
  no-netrc auth, Parquet/JSON instead of pickle across the process boundary (`bounded.py`).
- `pip-audit` on the pinned set: **requests 2.32.5 (PYSEC-2026-2275, fixed 2.33.0)** and
  **curl-cffi 0.13.0 (PYSEC-2026-2431, fixed 0.15.0)**. `npm audit`: 0 vulnerabilities.
- Git history still holds a fixture modelled on a real account (`docs/OPEN_ITEMS.md` #1, owner decision).

### 1.7 Findings verified in this audit (new)

| ID | Finding | Evidence |
|---|---|---|
| F-1 | **The v4 shadow model's code fingerprint does not cover the code that computes its inputs.** `ENGINE_SOURCES` ([core/tracking/fingerprint.py:28](../../core/tracking/fingerprint.py#L28)) has no `elite_v4_shadow` key, so its fingerprint hashes only `core/engine/*.py` + the evidence registry (43 files) and omits `TradingAnalysis_ELITE.py`. `elite` and `elite_v3_shadow` hash 44 files including it. A change to how components are scored would not start a new v4 period. | Ran `_sources()` for each engine key |
| F-2 | **No fingerprint covers `modules/`.** `earnings_revision_scoring` (the v4 revisions leg), `catalyst_scanner`, `portfolio_risk_manager`, `compliance_manager`, `sec_edgar_parser`, `polygon_provider` all feed engine output via `_import_optional`, and none is hashed. Whether an optional module imported at all is also not recorded. | `fingerprint.py:28-45`, `TradingAnalysis_ELITE.py:110-149` |
| F-3 | **The fingerprint is so broad that the evidence clock rarely runs.** It hashes all 15K lines of `TradingAnalysis_ELITE.py` including report wording. The published record has 435 rows over 12 publication dates (2026-10-16 to 2026-11-04) split across **23 distinct fingerprints**; no fingerprint can approach the pre-registered minimum (30 resolved calls on 10 non-overlapping dates). | `track_record/ledger.csv` |
| F-4 | Two pinned dependencies have published advisories (above). | `pip-audit` |
| F-5 | Documentation drift: JS test count (746 vs 882), F401 count (107 vs 123). | measured |

---

## 2. TradingAgents (`9fa1196`)

**Purpose:** a LangGraph multi-agent pipeline that writes an LLM investment decision for one
ticker on one date.

**Graph** ([tradingagents/graph/setup.py](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/setup.py)):
4 analysts in parallel (market, sentiment/social, news, fundamentals), each a private sub-graph
with a tool loop capped by `max_tool_rounds` (default 20) and a forced wrap-up turn → Bull ↔ Bear
debate (`max_debate_rounds`, default 1) → Research Manager (deep model) → Trader → Aggressive →
Conservative → Neutral risk debate (`max_risk_discuss_rounds`, default 1) → Portfolio Manager
(deep model) → END. A "Memory Log" node runs beside the analysts and settles past decisions.

**Agents.** Prompts are f-strings over the analysts' prose
(e.g. [`researchers/bull_researcher.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/researchers/bull_researcher.py)).
The Research Manager, Trader and Portfolio Manager use Pydantic structured output
([`agents/schemas.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/schemas.py)) with a
free-text fallback; the PM emits one of Buy / Overweight / Hold / Underweight / Sell
([`managers/portfolio_manager.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/managers/portfolio_manager.py)).
Schemas coerce LLM-written numbers ("N/A", "$1,234.50") and drop percentages written where a price
was asked for.

**Memory.** `memory/settlement.py` scores a past decision's raw and alpha return over
`holding_days` (default 5) from the first close after the decision; `memory/reflection.py` asks the
LLM for a 2-4 sentence lesson, stored in a Markdown log and injected into the PM's prompt as
`past_context`. Point-in-time filtering of lessons exists (#1251).

**Data.** `dataflows/router.py` maps tool categories to vendors (yfinance, Alpha Vantage, SEC
EDGAR, FRED, Polymarket, Reddit, StockTwits). `dataflows/date_window.py` clamps every tool call to
the run date and trims dated news to the window; undated items are kept only for live runs.

**LLM providers.** OpenAI, Anthropic, Google, Bedrock, Azure, Ollama, OpenRouter, DeepSeek,
MiniMax (`llm_clients/`), with separate quick and deep tiers.

**Backtest.** [`tradingagents/backtest.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/backtest.py) runs
the graph over a (ticker × date) grid and aggregates settled ratings. Its docstring states it "is
not a portfolio simulator": no fills, fees or equity curve.

**Gaps verified by reading:** no confidence/probability on the final rating; no quantitative score
behind the rating; no handling of **LLM training-data look-ahead** (a backtest dated before the
model's knowledge cutoff can be answered from memory; grep for "cutoff"/"training data" finds only
data-date cutoffs); position sizing is prose from the Trader, not a computed risk budget.

**Tests:** 90 files, ~1,009 `def test_` functions, including look-ahead tests for news,
fundamentals and social data. **Not executed** (see 09).

---

## 3. OpenBB (`2a392e8`)

**Purpose:** a provider-agnostic financial data platform (Python SDK, REST API, CLI, MCP server).

**Provider abstraction** —
[`core/openbb_core/provider/abstract/fetcher.py`](https://github.com/OpenBB-finance/OpenBB/blob/2a392e890f6eb1f0f4e3a71742720af8f056333a/openbb_platform/core/openbb_core/provider/abstract/fetcher.py):
`Fetcher[Q, R]` with three steps, `transform_query` → `extract_data`/`aextract_data` →
`transform_data`, plus optional streaming. Query and result types are Pydantic **standard models**
(181 files in `provider/standard_models/`, e.g. `equity_historical.py`: `EquityHistoricalQueryParams`,
`EquityHistoricalData`), which each provider extends with its own fields. Results are wrapped in
`OBBject` (`results`, `provider`, `warnings`, `chart`, `extra`).

**Query executor** — `QueryExecutor.execute(provider_name, model_name, params, credentials)`
(`provider/query_executor.py:66`) resolves one named provider and calls its fetcher. **There is
no automatic failover**: the caller chooses the provider; a failure raises.

**Providers in this monorepo:** bls, cboe, cftc, deribit, ecb, eia, famafrench, federal_reserve,
finra, fred, government_us, imf, jodi, nasdaq, oecd, sec, tmx. **yfinance, FMP, Polygon,
Intrinio, Benzinga are not in this tree at this commit** (the platform `pyproject.toml` lists none).
The SEC provider is broad (40 models: XBRL financials, filings, 13F, insider trading, risk factors,
MD&A, segment revenue, full-text search) with a cache and a rate limiter
(`openbb_sec/utils/{cache,ratelimit}.py`).

**Quantitative extension** — `extensions/quantitative/openbb_quantitative/`: `factors()` runs OLS
of a return series on a factor matrix over named look-back periods with p-values and CIs
(`factors.py:95`, pairs with the Fama-French provider); `attribution.py`, `risk_decomposition.py`,
`performance.py`, `rolling.py`, `stats.py`. Technical extension: indicators, relative rotation.
**No backtester** (grep finds only incidental mentions).

**MCP server** — `extensions/mcp_server/` exposes platform endpoints as MCP tools, with a `skills/` folder.

---

## 4. Qlib (`5435523`)

**Purpose:** an AI-oriented quant research platform: data → features → model → signal → strategy →
backtest → analysis, with experiment tracking.

- **Features:** `contrib/data/handler.py` `Alpha158` (98) and `Alpha360` (48) built from an
  expression engine (`$close`, `Ref`, `Mean`, …). **Label** `Ref($close, -2)/Ref($close, -1) - 1`
  (lines 89-91, 151-153): the return from T+1 close to T+2 close, so a signal computed at T's close
  is traded at T+1, a sound convention.
- **Leakage guards:** processors that learn statistics take `fit_start_time`/`fit_end_time`
  (`check_transform_proc`, handler.py:12; `data/dataset/processor.py:197-201` warns the fit window
  must not include test data). Benchmark configs use contiguous train/valid/test segments
  (e.g. LightGBM: 2008-2014 / 2015-2016 / 2017-2020-08) **with no gap**, so the last two training
  labels overlap the validation window (minor, Inferred from the label definition).
- **Universe:** index collectors (`scripts/data_collector/{cn_index,us_index,br_index}`) produce
  instrument lists with start/end dates, which supports point-in-time membership. PIT financials
  exist for China via baostock (`scripts/data_collector/pit`).
- **Models:** LightGBM, XGBoost, CatBoost, Linear, DoubleEnsemble and ~25 PyTorch models
  (`contrib/model/`).
- **Backtest:** `backtest/exchange.py` with `open_cost` 0.15%, `close_cost` 0.25%, `min_cost` 5,
  `impact_cost`, price-limit thresholds, trade unit 100 (China A-share defaults); nested executors.
  `TopkDropoutStrategy` (`contrib/strategy/signal_strategy.py:74`) reads the previous step's
  prediction (`shift=1`, line 141). `EnhancedIndexingStrategy` (374) uses an optimizer with a risk model.
- **Risk models:** `model/riskmodel/` shrinkage (Ledoit-Wolf family), POET, structured (factor) covariance.
- **Evaluation:** `contrib/evaluate.py:26` `risk_analysis` accumulates by **sum by default** and
  annualises daily data with **238** sessions (China), both of which must be changed for US use.
- **Experiment tracking:** `workflow/` recorder on MLflow.
- **Reported benchmark (Qlib's own table, not reproduced):** on Alpha360/CSI300, LightGBM IC 0.0400,
  annualised excess 5.6%; Transformer IC 0.0114, −2.7%; TabNet IC 0.0099, −3.7%; MLP IC 0.0273,
  +0.3%; HIST IC 0.0522, +9.9%. Several deep models underperform gradient-boosted trees.
