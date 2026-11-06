# 04 — AI and algorithm analysis

## 1. TradingAgents: how the agent system actually works

### 1.1 Flow (from `tradingagents/graph/setup.py` at `9fa1196`)

```
START ─┬─ Market analyst ─┐   each analyst = private sub-graph:
       ├─ Sentiment ──────┤     agent → tools → agent … (≤ max_tool_rounds=20) → forced wrap-up
       ├─ News ───────────┤
       ├─ Fundamentals ───┤
       └─ Memory Log ─────┘   (settles past decisions, produces past_context)
                 │  (join: all reports filed)
                 ▼
     Bull ⇄ Bear  (2 × max_debate_rounds turns; default 1 round = 2 turns)
                 ▼
     Research Manager (deep LLM, structured)  →  Trader (quick LLM, structured)
                 ▼
     Aggressive → Conservative → Neutral  (3 × max_risk_discuss_rounds; default 3 turns)
                 ▼
     Portfolio Manager (deep LLM, structured: Buy/Overweight/Hold/Underweight/Sell) → END
```

Routing is deterministic: `ConditionalLogic.should_continue_debate` alternates speakers by the
prefix of the last message and stops at a turn count (`graph/conditional_logic.py`). There is no
judge deciding when the debate has converged. Path maps are complete so a prompt drift cannot
crash the graph (#1088 comment).

### 1.2 What each piece contributes

| Piece | Implementation | Assessment |
|---|---|---|
| Analysts | Tool-calling LLMs over vendor data; tools clamped to the run date (`dataflows/date_window.py`) | Good engineering: private message histories, tool-round cap, explicit "unavailable" vs "empty" |
| Bull / Bear | Prompted to argue a side over the four reports and the opponent's last turn | Produces two-sided prose; nothing checks that cited numbers exist in the reports |
| Research Manager / Trader | Structured output (`agents/schemas.py`) with coercion of LLM-written numbers | Schemas reduce format failure, not factual failure |
| Risk debate | Three personas | Persona diversity is stylistic; no risk computation (no VaR, no stop, no sizing math) |
| Portfolio Manager | Final rating, executive summary, thesis; reads `past_context` lessons | The LLM, not a model, decides the rating |
| Memory | 5-day alpha vs regional benchmark → LLM writes a 2-4 sentence lesson → injected into future PM prompts | Lessons learned from 5-day noise feed back into decisions; no test shows they help |
| Backtest | Grid of (ticker, date) single-shot runs scored by the memory log | Decision-quality only; **no defence against the LLM already knowing what happened after a historical date** |

### 1.3 Cost, latency and failure handling

- **LLM calls per ticker-date (architectural count, Not Measured):** at defaults, each of 4
  analysts makes ≥ 2 calls (tool request + report) and up to 21; bull + bear 2; Research Manager 1;
  Trader 1; risk debaters 3; Portfolio Manager 1; reflection 1 when a past decision settles.
  **Minimum about 17 calls**, two of them on the deep tier; the 4 analyst reports and the debate
  history are re-sent in later prompts, so input tokens grow with each stage.
- Failure handling: configurable SDK retries (`llm_max_retries`), structured-output fallback to
  free text with a rating parser, SQLite checkpointing (`graph/checkpointer.py`) for resume.
- Determinism: the config comment states that no setting makes runs bit-identical across providers.

### 1.4 Should QUANTA adopt it?

| Capability | Recommendation | Why |
|---|---|---|
| Multi-agent investment research | **Adapt, narrowly** | QUANTA already computes the facts. One LLM pass that *explains* a Decision is enough; parallel tool-using analysts would re-fetch data QUANTA already has |
| Bull/bear thesis generation | **Adopt as explanation, not as decider** | Valuable for the reader: two grounded paragraphs, each sentence citing Decision fields, each run through `check_narrative` |
| AI investment committee | **Do not adopt as a decider** | QUANTA's committee is deterministic thresholds (`thresholds.COMMITTEE`, `RR`, blockers). An LLM vote would add variance and break "one decision per stock" (PRODUCT.md principle 1) |
| Evidence-backed recommendations | **Already better in QUANTA** | Coded blockers, evidence status, ledger. Add citations to filings/news items as `SourceRef` ids |
| Explainable decisions | **Already better in QUANTA**; extend with LLM prose on top of `build_interpretation` |
| Research reports with citations | **Adopt** | Use `Decision.sources` ids as the citation targets; reject any sentence with an unknown id or unmatched number |
| Risk-aware portfolio recommendations | **Build on QUANTA's analytics**, not on persona debate |
| Human-in-the-loop approval | **Already inherent** (read-only, no orders). Add explicit "I acted on this" journal entries (exists: `core/portfolio/journal.py`) |
| LLM memory / reflection | **Avoid** | Replace with the ledger's measured outcomes; never let an LLM-written lesson change a call |

**Licence:** Apache-2.0. Patterns (structured schemas, number coercion, date-window clamps,
tool-round caps, complete path maps) can be reused with attribution and a NOTICE entry if code is
copied. Recommendation: reimplement the patterns; do not take a LangGraph dependency for one
explanation call.

## 2. OpenBB: data-layer patterns worth taking

| Pattern | Where | Use in QUANTA |
|---|---|---|
| Three-step fetcher: `transform_query` → `extract_data` → `transform_data` | `provider/abstract/fetcher.py` | Shape of each QUANTA provider adapter, so throttle, cache and provenance wrap one method |
| Standard models + provider extensions (Pydantic) | `provider/standard_models/*.py` | A small set: `PriceBars`, `QuoteSnapshot`, `CompanyProfile`, `FinancialFacts`, `Filing`, `AnalystConsensus`, `NewsItem` |
| Result envelope with `provider` and `warnings` | `app/model/obbject.py` | Matches QUANTA's `SourceRef` + `Freshness`; add `warnings` so degraded answers are visible |
| Registry of providers and the models each supports | `provider/registry.py` | Lets QUANTA pick a fallback chain per model |
| SEC client with cache + rate limiter | `providers/sec/openbb_sec/utils/` | QUANTA already has its own (`test_sec_rate_limiter.py`); compare, don't replace |
| Fama-French provider + OLS factor regression | `providers/famafrench`, `extensions/quantitative/.../factors.py` | Portfolio and pick factor attribution |

What OpenBB lacks and QUANTA should add itself: **automatic failover** (the executor calls exactly
one named provider) and **per-field freshness**.

**Do not depend on the OpenBB platform.** QUANTA needs ~7 data models from 2-3 free providers; the
platform is ~867K lines across many packages and, at this commit, ships none of the consumer price
vendors QUANTA uses. Copy the pattern (Apache-2.0 permits reuse of code with notice).

## 3. Qlib: quantitative methods worth taking

| Method | Qlib implementation | Recommendation for QUANTA |
|---|---|---|
| Next-day label convention | `Ref($close,-2)/Ref($close,-1)-1` | Already equivalent: QUANTA's ledger enters at the first open after publication |
| Fit-window discipline for processors | `fit_start_time`/`fit_end_time` | Adopt if any normalisation is learned (z-scoring factors across a training window) |
| Point-in-time index membership | `scripts/data_collector/*_index` with start/end dates | **Adopt (P2).** The 42-name survivor universe is QUANTA's biggest validation weakness |
| Alpha158 feature set | `contrib/data/loader.py` | Use as a *feature source for a baseline*, not a production model |
| LightGBM ranker | `contrib/model/gbdt.py` | Only after a PIT universe exists, and only if it beats 12-1 momentum and the current composite out of sample (QUANTA's own `baselines` command) |
| Deep models | `contrib/model/pytorch_*.py` | **Avoid.** Qlib's own table shows Transformer (IC 0.011) and TabNet (0.010) below LightGBM (0.040) on CSI300 |
| Shrinkage / structured covariance | `model/riskmodel/{shrink,structured,poet}.py` | Adopt shrinkage (Ledoit-Wolf) for portfolio vol, risk shares and any optimiser; sample covariance on short windows is noisy |
| Top-k dropout rebalancing | `TopkDropoutStrategy` | A useful *evaluation* strategy for a ranking model: hold top k, replace at most n per rebalance, which bounds turnover |
| Cost model with min fee, impact | `backtest/exchange.py` | QUANTA's cost model is already richer for India (statutory levies) and per-side bps; add a minimum fee and a participation-based impact term |
| `risk_analysis` | `contrib/evaluate.py` | **Avoid defaults**: sum accumulation and 238-day annualisation are China conventions |
| Experiment recorder | `workflow/` (MLflow) | QUANTA's `trials.json` + evidence registry + fingerprints already cover the essentials; no MLflow |

**Licence:** MIT. Code may be copied with the copyright notice.

## 4. QUANTA's algorithms, assessed

### 4.1 What is good, verified

- **Exact barrier probabilities** (`core/engine/expected_value.py:62`). Eigenfunction series for
  drifted Brownian motion between two absorbing barriers. Checked in this audit against a 200,000-path
  Monte Carlo over five parameter sets (largest raw gap 0.025, explained by discrete monitoring);
  re-run with the Broadie-Glasserman continuity correction, that worst case differs by
  0.0013-0.0018 (09, §3). Closed form runs in 0.06-0.16 ms per call.
- **Statistical inference** (`core/apex_quant/stats.py`): Newey-West t for overlapping returns,
  circular block bootstrap, probabilistic and deflated Sharpe with a persistent trial count,
  Benjamini-Hochberg. This is beyond what Qlib or OpenBB ship.
- **IC on non-overlapping dates** (`elite_oos.compute_ic`), quantile monotonicity, comparison
  with 12-1 momentum, 1-month reversal, low vol and random rankings on identical cross-sections.
- **Pre-registration and shadow scoring** (v3, v4), with a per-fingerprint prospective IC test.
- **Portfolio maths** (`core/portfolio/analytics.py`): Euler variance contributions summing to 1,
  historical VaR and CVaR with CVaR ≥ VaR by construction, compounding the worst window in log space.

### 4.2 What is weak

| Issue | Detail | Consequence |
|---|---|---|
| No validated signal | Research v2: IC +0.020 (21d, t 0.87), +0.041 (63d, t 1.42); short-term scanner −0.20% per weekly cohort vs SPY | Every Buy is a candidate, honestly labelled; the ranking is not better than free 12-1 momentum |
| Survivor universe | 42 present-day large caps (`apex_quant/universe.py:30`) | Low cross-sectional power (IC on 42 names has a wide CI) and survivorship bias |
| Live engine not replayed | The harness ranks a composite; it does not simulate Picks' entries, stops, sizes, gates | Cannot say what following the calls would have returned |
| EV drift comes from the target | `drift_from_valuation` (114) sets μ from the 12-month valuation target, haircut by `shrink`; the same target is the upper barrier | P(target first) rises with target upside by construction; the `shrink` parameter is untested. Needs a calibration test: predicted P(target first) vs realised frequency in the ledger |
| Sample covariance | `frame.cov()` on daily returns over the shared window | Noisy risk shares for books with many names and short histories |
| Regime weights | v2 switches weight tables by regime; the 2026-10-22 test shows fixed weights give almost the same IC | Complexity without measured benefit |
| Three indicator stacks | RSI/ATR in `SP_ELITE_PRO.py`, `apex_quant/indicators.py`, `engine/indicators.py` | Parity is tested, but each new indicator must be written three times |
| Evidence clock resets | 23 fingerprints in 12 publication dates (02, F-3), driven by real logic changes | The pre-registered prospective test cannot reach its minimum sample until scoring code is frozen |

### 4.3 What simpler baselines say

QUANTA's own reports already contain the key comparison: on identical cross-sections, the research
composite is "not distinguishable from 12-1 momentum", and the old ELITE composite was "reliably
worse than plain 12-1 momentum". Per the brief's rule (no complex model unless it beats simpler
baselines out of sample), the next model to *add* is not an ML model; it is a recorded **12-1
momentum + quality shadow** in the ledger, so the prospective record compares the composite with
the free factor it has not yet beaten.
