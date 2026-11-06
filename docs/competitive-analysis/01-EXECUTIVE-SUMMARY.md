# 01 — Executive summary

**Audit of QUANTA against TradingAgents, OpenBB and Microsoft Qlib.** 2026-11-06. Audit and
planning only; no code was changed.

Commits: QUANTA `ce1e9b4` · TradingAgents `9fa1196` · OpenBB `2a392e8` · Qlib `5435523`.
Licences: QUANTA MIT · TradingAgents Apache-2.0 · OpenBB Apache-2.0 · Qlib MIT. All permit reuse
with notice; the recommendation is to reuse patterns, not dependencies.

## Headline

- **QUANTA scores highest on the requested rubric: 6.28 / 10** (OpenBB 5.70, Qlib 5.36,
  TradingAgents 4.61), because it is the only one that turns data into one sized, explained,
  recorded decision. Each competitor beats it in its own specialty.
- **QUANTA's tests are green here:** 2,976 Python and 882 JavaScript tests pass.
- **New defects found:** the v4 shadow model's code fingerprint misses the engine that computes
  its inputs; no fingerprint covers `modules/`. Separately, the fingerprint is broad enough that
  12 publication dates produced 23 fingerprints, so no model version can reach its pre-registered
  test. Two pinned dependencies (requests, curl-cffi) have published advisories.
- **No financial calculation error** was found in the paths checked. The exact barrier-probability
  model matches Monte Carlo to within 0.2 percentage points after continuity correction.
- **Top recommendation:** fix how evidence accumulates (about two weeks of P0 work) before adding
  features. Then add a free-factor shadow, a thin data gateway and one grounded LLM explanation.

## Answers

### 1. Strongest repository overall, and why

On the weighted rubric, **QUANTA (6.28)**. It leads research, portfolio and risk, discovery and
UI, which together carry 36% of the weight, and ties for security and testing. As engineering
artefacts, **OpenBB** has the best architecture and extensibility and **Qlib** the deepest
quantitative machinery. Scores and per-category evidence: [03](03-FEATURE-COMPARISON.md).

### 2. What each platform is uniquely best at

| Platform | Uniquely best at |
|---|---|
| QUANTA | Governed decisions: one Decision object, coded blockers, evidence flags, pre-registration, shadow models, tamper-evident ledger, read-only live holdings |
| TradingAgents | LLM multi-agent research: parallel tool-using analysts, bull/bear and risk debates, structured manager outputs, nine LLM providers, date-clamped data tools |
| OpenBB | Data breadth and abstraction: typed three-step fetchers, 181 standard models, provider packages, SEC depth, MCP server |
| Qlib | Quant research loop: Alpha158/360 features, 30+ models, risk models, optimiser, cost-aware backtest, PIT index membership, experiment tracking |

### 3. What QUANTA already does better than each competitor

- **vs TradingAgents:** decisions are computed, reproducible and sized from a risk budget; every
  call carries its validation status; LLM text can be fact-checked (`check_narrative`); the track
  record is hash-chained; its backtest statistics (HAC, block bootstrap, deflated Sharpe) are real
  inference, and it has no LLM look-ahead problem.
- **vs OpenBB:** it makes decisions (OpenBB deliberately does not), has portfolio analytics on live
  holdings, a full UI with accessibility tests, and automatic stale-grace and fallback that OpenBB's
  executor lacks.
- **vs Qlib:** an end-user product; stronger significance testing and multiple-testing control;
  random-entry and random-exit baselines; pre-registration; India costs and calendars; live holdings.

### 4. QUANTA's 20 most important missing capabilities

1. Point-in-time recording of every input a call used
2. A free-factor (12-1 momentum + quality) shadow model
3. A typed provider gateway with provenance and failover
4. A point-in-time universe with delisted names for validation
5. Replay of the live Decision path over history
6. Calibration of predicted trade probabilities
7. A grounded LLM research brief
8. A grounded two-sided bull/bear summary
9. Fama-French factor attribution of the book and of Picks
10. Shrinkage covariance for portfolio risk
11. Constrained portfolio construction suggestions
12. Actual account performance and attribution
13. A thesis tracker with measurable invalidation conditions
14. A FRED macro panel
15. Filing text signals (risk-factor changes)
16. Earnings-call transcript summaries (only with a licensed source)
17. Turnover-bounded (top-k dropout) evaluation
18. Historical-episode stress tests
19. Fund look-through
20. Crypto research for held crypto

Detail and references: [05 §5](05-QUANTA-GAP-ANALYSIS.md).

### 5. Algorithms and patterns to adopt

- OpenBB: the `transform_query → extract → transform` fetcher shape, standard data models and a
  result envelope with provider and warnings, plus ordered failover, which OpenBB lacks.
- Qlib: point-in-time index membership; Ledoit-Wolf shrinkage covariance; top-k dropout evaluation;
  fit-window discipline for learned normalisation; a minimum-fee and impact term in costs.
- TradingAgents: Pydantic schemas with coercion of LLM-written numbers; date-window clamps for any
  data an LLM sees; tool-round caps; complete routing maps. Use for one explanation call, not a graph.
- OpenBB quantitative extension: OLS factor regression on Fama-French factors, with CIs.

### 6. Competitor implementations to avoid

| Avoid | Why |
|---|---|
| TradingAgents' LLM Portfolio Manager deciding the rating | Not reproducible, not validated, breaks "one decision per stock" |
| TradingAgents' LLM-written lessons fed into later prompts | Learned from 5-day noise; no evidence they help |
| TradingAgents-style backtests of LLM decisions | The LLM may already know what happened after the test date |
| OpenBB as a dependency | ~867K lines across many packages; the vendors QUANTA uses are not in its monorepo at this commit; no failover |
| Qlib deep models | Qlib's own table: Transformer IC 0.011 and TabNet 0.010 vs LightGBM 0.040 on CSI300 |
| Qlib evaluation defaults | Sum accumulation, 238-day annualisation and A-share costs are China conventions |
| Any ML on QUANTA's 42-name survivor universe | Too few names and survivorship bias |

### 7. Top 10 upgrades by value relative to effort

| # | Task | Effort | Why |
|---|---|---|---|
| 1 | P0-1 Complete fingerprint coverage | S | Closes a reproducibility hole |
| 2 | P0-5 Patch requests and curl-cffi | S | Two advisories |
| 3 | P0-3 Model-only fingerprint | M | Lets evidence accumulate |
| 4 | P0-4 Record every call input | M | Every unrecorded day is lost history |
| 5 | P1-1 Factor-baseline shadow | S | Tests whether the composite earns its complexity |
| 6 | P1-3 Ledoit-Wolf covariance | S | Stabler risk numbers |
| 7 | P1-6 Evidence clock in the UI | S | Shows how far each model is from a verdict |
| 8 | P1-5 Grounded research brief | M | Visible AI upgrade on existing guard rails |
| 9 | P1-4 Fama-French attribution | M | Shows the book's real exposures |
| 10 | P1-2 Provider gateway, phase 1 | L | Unlocks failover, provenance and new sources |

### 8. What would make QUANTA genuinely differentiated

- **Verified debate:** bull and bear prose in which every number is machine-checked and every
  claim cites a Decision field or filing.
- **Calibrated, published trade probabilities** with a public reliability record.
- **Pre-registration as a feature:** registered model versions, their rules and their progress, in the UI.
- **Holder-aware decisions** (already in the schema: `new_money` vs `holder_action`), surfaced further.
- **A personal thesis ledger** resolved and hash-chained like QUANTA's own calls.

### 9. Target architecture

Same stack (Flask on loopback, vanilla JS, Python, SQLite), seven layers: a new thin data gateway
(`core/data/`), research analyzers split out of the 15K-line engine, the existing validation harness
plus PIT universe and shrinkage, an optional `core/ai/` layer that makes one grounded call per
Decision, portfolio analytics with attribution and constrained what-ifs, the Decision core with a
model fingerprint and full input recording, and per-page front-end bundles.
LLMs explain; the engine decides. Details: [07](07-TARGET-ARCHITECTURE.md).

### 10. QUANTA's score now, and how to raise it

**6.28 today.** Completing P0 and P1 would plausibly move data 5→7, AI 4→6, architecture 4→5 and
quant 6→7, about **6.9** (a judgment, not a measurement). Measurable targets: fingerprints per 10
publication dates from ~19 to ≤ 2; 100% of new ledger rows with recorded inputs; direct yfinance
call sites from 83 to ≤ 40; dependency advisories from 2 to 0; unused imports from 123 to 0 and
gated; zero generated text shown without passing the grounding check. See
[08](08-IMPLEMENTATION-ROADMAP.md) and [10](10-FINAL-VERDICT.md).

## Reports

| # | Report |
|---|---|
| 01 | This summary |
| 02 | [Codebase audit](02-CODEBASE-AUDIT.md): traced paths, file references, new findings |
| 03 | [Feature comparison](03-FEATURE-COMPARISON.md): matrix, scores, justification |
| 04 | [AI and algorithm analysis](04-AI-AND-ALGORITHM-ANALYSIS.md) |
| 05 | [QUANTA gap analysis](05-QUANTA-GAP-ANALYSIS.md) |
| 06 | [Feature adoption matrix](06-FEATURE-ADOPTION-MATRIX.md) |
| 07 | [Target architecture](07-TARGET-ARCHITECTURE.md) |
| 08 | [Implementation roadmap](08-IMPLEMENTATION-ROADMAP.md) |
| 09 | [Benchmarks and verification](09-BENCHMARKS-AND-VERIFICATION.md): what was run, what was not |
| 10 | [Final verdict](10-FINAL-VERDICT.md) |

## Limits of this audit

- Competitor code was read, not executed (rule against running unreviewed third-party code).
  OpenBB was sampled (core provider layer, SEC, Fama-French, quantitative extension).
- QUANTA's golden master, browser e2e, live-network latency and validation re-runs were not run.
- Scores are judgments grounded in the cited code; weights are the ones requested.
