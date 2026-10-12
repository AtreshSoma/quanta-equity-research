# QUANTA Alpha: Technical Audit and System Design

**QUANTA Elite has useful research infrastructure, but it does not yet demonstrate a validated investment edge. QUANTA Alpha should be built around evidence provenance, realistic decision evaluation, and an immutable forecast record—not a larger collection of indicators or a more persuasive AI narrator.**

This design assumes a self-hosted, primarily free-data platform for U.S. listed equities, initially long-only, with 21-, 63-, and 126-trading-session research horizons. The product should support market-wide discovery while clearly distinguishing the listed universe, the data-covered universe, and the investable universe. It should never imply that an unobserved stock was evaluated and rejected.

The strongest achievable differentiation is **explainable change detection plus portfolio-aware opportunity selection with a visible record of successes, failures, and uncertainty**. Institutional quality describes controls and measurement discipline; it does not imply institutional data completeness or guaranteed alpha.

## Audit scope and evidence

The reviewed default-branch snapshot is commit `7bb6386d176ca98764641031bab327ad0ab1d647`, committed September 9, 2026. Source references below are pinned to that commit. The repository tree was inventoried, 191 relevant text files were retrieved, and the main execution paths, scoring functions, data adapters, validation harnesses, UI source, tests, and existing recalibration plan were examined. This was a targeted technical audit, not a claim that every line of every file received equal scrutiny.

The GitHub CI run for this commit completed successfully. Locally, **160 targeted existing tests passed**, covering execution semantics, leakage guards, reproducibility, validation identity, decisions, earnings-surprise units, scorer reconstruction, target confidence, and portfolio limits. Four additional isolated offline probes reproduced the outcome-tracking defects, an optimizer fallback cap violation, and the absence of a validation-artifact gate in the shared action resolver. The probes exercise real extracted classes/functions with synthetic inputs; they are not live portfolio simulations.[^2]

The full golden-fixture suite, browser rendering, live provider availability, historical return panels, and a new market-wide backtest were not rerun. UI findings concern source-defined behavior and architecture. No repository code was changed, no GitHub review was posted, and no strategy performance is asserted by this report. Existing audit documents were treated as leads and checked against current code, not accepted as ground truth.

## 1. QUANTA Elite gap analysis

### 1.1 What already works

Elite is more substantial than a ticker lookup dashboard. It contains four independent engines, a Flask dashboard, a Streamlit research interface, MCP access, provider fallbacks, risk checks, and multiple historical evaluation paths.[^1]

| Subsystem | Actual implemented behavior | Consequence for Alpha |
|---|---|---|
| Elite Pro scanner | Builds U.S./India universes, fetches histories and current company data, computes technical/fundamental/momentum/quality/value scores, applies regime tables and cross-sectional normalization, ranks and sizes candidates, then deep-confirms selected picks | Preserve useful acquisition and scan diagnostics; replace the fragmented scoring and final decision flow |
| Short-term scanner | Universe screening, technical patterns, momentum, fundamentals, quality, sentiment-like inputs, SPY/VIX regime, ATR-related levels, portfolio gates, and trade reports | Keep as a separately identified swing-strategy baseline, not the definition of a 1–6 month alpha model |
| Trading Analysis Elite | Single-name technicals, fundamentals, risk, VADER sentiment, analyst/insider data, catalysts, DCF, Monte Carlo scenarios, trade setups, position sizing, and narrative explanations | Extract pure features and verified controls; do not preserve every transformation or label |
| Alpha Picks | Weighted analyst, quantitative, sentiment, technical, and earnings-revision scores; high-score filters; price targets and thesis strings | Treat as another legacy hypothesis, not an independently validated ensemble member |
| Shared decision core | Canonical actions, ownership-aware HOLD/WATCHLIST mapping, data validation, risk overrides, and consistency helpers | Extend into one policy boundary with an immutable decision object |
| APEX harness | Chronological walk-forward tuning, corrected next-session execution, costs, randomized baselines, feature causality tests, strategy identity, coverage checks | Best foundation for consolidated evaluation, after broadening data and statistical controls |
| Web product | Dashboard, Stock Picks, Analyze, market regimes/news, security monitor, progressive jobs, SSE/polling, data-status handling | Retain working presentation components; introduce research-history and portfolio-decision workflows |

The README explicitly states that no strategy has a demonstrated out-of-sample edge. That is an honest starting point. Elite Pro also deliberately deprecates some uncalibrated probability fields, labels heuristic confidence, retains undeployed cash, and documents diagnostic backtests as unvalidated. The audit does not attribute already-removed behavior to those paths.[^1][^3][^24]

### 1.2 Current decision flow

```text
Current constituent lists / curated lists / custom tickers
    → per-name provider acquisition and indicator calculations
    → engine-specific score + regime rules + heuristic adjustments
    → scan-relative normalization and eligibility filters
    → greedy portfolio selection and risk checks
    → inverse-volatility allocation refinement
    → selected-name deep analysis and reconciliation
    → API serialization, cards, narrative, exports

Separate research path:
Cached historical prices → partial ELITE reconstruction
    → forward-return IC diagnostics / APEX walk-forward simulation
    → validation artifact / CLI status
```

The critical separation is between the live product and its research evaluation. Reusing some analyzers does not mean that the complete live selection, missing-data handling, regime, sizing, and deep-confirmation policy has been validated.[^8][^10][^19][^20]

### 1.3 Priority findings

**A01 — Critical: historical performance artifacts cannot establish current edge.** The committed `INVALIDATED.md` says execution chronology, entry-bar exits, gap fills, terminal positions, and the first day's P&L were wrong in the old simulator. Its trade statistics must be regenerated, not adjusted. The corrected simulator and tests are valuable; their existence is not a successful validation run. The existing recalibration document still contains earlier numerical results, so those must not be promoted as current evidence.[^9][^29][^41]

**A02 — Critical: only a partial model is historically reconstructed.** `elite_scorer.py` pins unavailable components and reconstructs the technical/risk portion, representing 40% of the SIDEWAYS composite weight. It does not validate current fundamentals, sentiment, insider flows, valuation, every regime, Elite Pro's final ranking, or the complete portfolio policy. Its own empty fundamental input scores 40 because default ROE is penalized. “Point-in-time” price slicing is also weaker than preserving historical provider vintages and corporate-action semantics.[^8][^26]

**A03 — Critical: outcome tracking corrupts horizon and accuracy semantics.** `SignalTracker.update_outcomes()` uses a current quote when it eventually runs, rather than the quote at the intended horizon. The ten-day branch can reuse `price_5d`. `get_statistics()` includes unresolved actionable signals in its denominator and mixes five- and ten-day outcomes. An offline probe with a stored five-day price of 105 and an available quote of 120 recorded 105 as the ten-day price without fetching. One resolved winner plus one pending signal produced a reported 50% win rate. These statistics cannot calibrate forecasts.[^11]

**A04 — Critical: validation authorization is not a product-wide boundary.** APEX has a fingerprinted, time-limited validation artifact, but the shared action resolver and normal scanner/deep-analysis sizing paths do not consume it. The resolver can emit allocation-eligible STRONG_BUY from score/levels/risk inputs alone. The harness also writes separate generic and Elite artifacts, while its status command reads the generic artifact. Alpha needs an explicit model-status contract, not the implication that one CLI check validates all engines.[^7][^10][^19]

**A05 — High: scenario generation is being asked to do forecasting.** Trading Analysis seeds a Student-t log-return simulation with an annualized trailing sample mean, adds fixed regime drift, and uses a generic 5% daily jump chance when a catalyst exists. It reports terminal probabilities and calls median-price return “expected return.” The scenarios are reproducible but not calibrated predictions of future outcomes. Monte Carlo sampling reduces simulation noise; it cannot establish the correctness of the assumed process.[^12]

There is a deeper mathematical problem: for a non-degenerate untruncated Student-t log return X, E[exp(X)] is infinite because its polynomial positive tail cannot offset exponential growth. Thus the modeled price distribution has no finite population mean even when X has finite variance. A finite simulated mean will still print, but it is not a stable estimate of a finite expected price. The Gaussian −σ²/2 drift correction does not repair this. This is an analytical consequence of the implementation and the Student-t density, not a claim that a particular small simulation must overflow.[^12][^40]

**A06 — High: “calibrated” win probabilities are hand-set.** Trading Analysis builds win probability from confidence, a short signal-tracker history, technical bonuses, and a 70% Monte Carlo blend, then clamps it to 35–65%. Literature-inspired constants are not system-specific calibration, and a blanket assertion that no strategy can reliably win more than 65% confuses payoff structure with profitability. Kelly is computed and exposed as a diagnostic; the actual recommended share count follows risk sizing, not this Kelly value. Separately, the enhanced price-target module emits hand-set target-hit probabilities such as 75/50/25 with volatility adjustments. Elite Pro's improved heuristic labeling does not repair these other paths.[^13][^14]

**A07 — High: valuation partly confirms its own anchor.** The deep analyzer replaces extreme DCF outputs with spot, blends moderate high-P/E DCFs 40% with current price, and blends very high-P/E DCFs 60% with analyst targets. It substitutes positive growth assumptions when revenue growth is nonpositive. These choices may stabilize a display but obscure independent valuation information. The separate advanced target calculator also approximates enterprise value as equity value. A single “DCF fair value” label conceals materially different economic models and fallback behavior.[^15][^14]

**A08 — High: dynamic weighting means lookup tables, not learned conditional alpha.** Elite Pro's regime weight tables are explicit policy choices. Base scores are largely threshold heuristics, with a 65% absolute/35% cross-sectional blend where normalization is available, a 15% elite-factor blend when positive, and additive adjustments. Momentum, technical alignment, QMJ-like measures, quality, and valuation reuse related information. An additional factor display is not an independent source of evidence. The short-term and Alpha Picks weights differ again.[^3][^4][^5]

**A09 — High: discovery is broad scanning but not a historically complete market universe.** Current index lists, expanded curated groups, fallback lists, scan limits, and successful provider responses define the population. The APEX research universe is a fixed present-day list. Delisted firms, ticker changes, IPO seasoning, security classes, failed acquisitions, and historical eligibility are not represented by an authoritative security master. Survivorship bias can alter relative comparisons too; using the same surviving names does not make strategy-versus-baseline comparisons automatically unbiased.[^1][^26]

**A10 — High: risk can change after the checks.** Elite Pro checks sector exposure, covariance-based VaR, and aggregate stop risk during greedy admission, then redistributes allocations through an inverse-volatility step. That step caps individual notional but does not reapply all preceding portfolio constraints. Deep confirmation may reduce positions, but it is not a complete final portfolio constraint solver. A final post-transformation invariant check is needed before publishing any book. Separately, a projected VaR path can compute on the available subset of return histories, and its exception branch permits the candidate; missing risk data can therefore reduce measured risk.[^19]

**A11 — High, disconnected code: “robust” optimizer can violate its cap.** No active application caller of `core/quant/RobustOptimizer` or `FactorExposureModel` was found beyond package exports. Do not describe them as live platform capabilities. The optimizer automatically relaxes position caps and its fallback clips then renormalizes, which can exceed the same cap. A four-asset offline probe requested a 40% cap and returned a 75.9% weight while reporting a 40% cap. Its inverse-volatility fallback is not generally equal-risk-contribution allocation.[^17][^18]

**A12 — High: disconnected factor regression is mislabeled.** The ETF model is not the Fama–French five-factor model: USMV is not CMA, QUAL is not RMW, and value-minus-market is not HML. SHY total return has duration/price risk and is not a risk-free accrual series. Ordinary OLS errors and a 1.96 t cutoff across many names do not establish predictive alpha. Trailing regression intercepts are descriptions conditional on the chosen risk model, not forward alpha forecasts.[^17][^33]

**A13 — High: earnings revisions exist but are not a historical revision database.** The module uses current provider estimate snapshots, raw net analyst counts saturated at ±3, estimate changes saturated at ±5%, and surprise history in a fixed 45/40/15 mixture. Analyst coverage is not normalized. A 90-day fallback is labeled as a 30-day trend. Near-zero EPS denominators remain problematic. One fetch exception can neutralize the entire result. Alpha Picks also turns a valid zero revision score into neutral through `or 5.0`. These are both feature-definition and missingness problems.[^16][^5]

**A14 — High: catalyst “intelligence” includes assertions and stubs.** Historical catalyst returns/completion rates are dictionary constants, not a fitted event study. Entry, stop, target, and holding-period rules are hand-set. Entity confidence is assigned by regex rules. FDA approval acquisition is a placeholder. M&A targets and acquirers require different models; a generic event label cannot justify an immediate BUY or a fixed gain target. The live scanner can use these catalyst signals as score adjustments, so this is not merely a naming concern.[^23]

**A15 — Medium–high: sentiment conflates tone, novelty, and investable surprise.** The deep analyzer averages VADER scores and uses topic keywords; a field named `sentiment_7d_avg` actually uses the recent 48-hour subset. Positive text may repeat old news or describe a result below expectations. Separate source/provenance handling has improved, but headline polarity is not calibrated return information. Multiple republications should not count as independent confirmations.[^6]

**A16 — High: parallel backtest implementations make evidence ambiguous.** `modules/backtest_engine.py` calls itself walk-forward but the inspected main path replays simplified technical signals and enters at the same close used to calculate them. The robust engine also passes the current full bar to the strategy before filling at that close; its cross-validation routine runs on concatenated train-plus-test data and reports that combined result. Its walk-forward routine evaluates fixed supplied parameters in overlapping windows rather than learning them. These paths should not compete with the corrected APEX simulator for authority. Elite Pro already suppresses their diagnostic figures in ordinary output.[^24][^25]

**A17 — Medium–high: engineering scale and auditability are constrained by mutable process state.** The main engines have approximately 13,767, 14,624, and 6,566 lines; Flask has 5,840 and its main JavaScript file 6,174. The package configuration intentionally installs no importable packages. Flask monkey-patches provider behavior, stores jobs in a process-local dictionary, and starts daemon threads. A timed-out worker can remain alive after its job slot is released. JSON/CSV tracking and process-local locks do not provide durable, multi-process transactional behavior. Local-only hosting protection is a useful existing safeguard.[^20][^21]

**A18 — Product gap: explanations exist, but longitudinal conviction accounting does not.** Current thesis generators, downgrade checks, signal records, and market-regime history provide useful fragments. They do not reconstruct a stock's score change from versioned facts, frozen model parameters, universe changes, calibration changes, and portfolio changes. The current interface lacks a unified, immutable forecast-outcome ledger and a full model-performance workflow.[^11][^22][^28]

### 1.4 What to preserve

Preserve the tests, corrected execution semantics, ownership-aware action vocabulary, provenance/status distinctions, provider throttling and circuit breakers, market session handling, scan-health diagnostics, feature computation that passes causal tests, and existing useful UI components. Retire misleading labels and duplicate decision authority. Migration should preserve intentional behavior while documenting every deliberate mathematical change.

## 2. Target QUANTA Alpha architecture

### 2.1 The architectural recommendation

**Current state →** independent engines recalculate and reconcile decisions, largely on user request. **Problem →** repeated I/O, inconsistent model definitions, incomplete historical replay, and non-durable decision state. **Proposed design →** one importable Python domain package, a scheduled research worker, a durable metadata/portfolio database, immutable analytical datasets, and thin API/UI/MCP adapters. **Algorithm/model →** deterministic dependency graph from snapshot to features to forecasts to portfolio policy. **Required data →** timestamped source records, security identity, calendars, position ledger, and model artifacts. **Expected benefit →** the same inputs and versions reproduce the same forecast and decision across every surface. **Risks/limitations →** migration needs explicit compatibility tests; introducing many distributed services immediately would make self-hosting worse.

```text
SEC / exchange directories / price sources / macro vintages / news
                             │
                 Source adapters and quality checks
                             │
         Immutable raw snapshots + security/corporate-action master
                             │
             As-of fact tables → versioned feature panels
                             │
        Alpha models + market scenarios + covariance/risk models
                             │
            Calibrated distribution + eligibility/evidence state
                             │
      Market opportunity ranking ─── Portfolio incremental utility
                             │
              One decision policy + final constraint checks
                             │
             Immutable forecast / decision / thesis-change ledger
                             │
                  API → web / CLI / MCP → Astra

Matured returns and paper fills → evaluation → model registry / alerts
Astra reads evidence and requests approved computations; it cannot alter scores.
```

### 2.2 Minimal self-hosted deployment

Use a **modular monolith with separate processes**, not a microservice estate. Keep Flask initially; a framework rewrite does not improve alpha. Run an API process, a worker process, and one scheduler. Use PostgreSQL for portfolios, source metadata, durable jobs, model registry, and ledgers; store large raw files and columnar panels on local disk as content-addressed files and Parquet. DuckDB is appropriate for local analytical queries. A single-user prototype can use SQLite with one writer, but choose one transactional store for the actual deployment.

Use a PostgreSQL-backed job table with leases, bounded retries, idempotency keys, cancellation state, heartbeat, and worker fencing tokens. A worker that loses its lease must not publish results. Publish a universe snapshot atomically only after quality checks. A completed partial scan is a distinct state from a complete healthy scan. Redis, Kafka, a feature-store service, and Kubernetes are unnecessary initially.

The architecture separates **instrument facts** from **account-specific decisions**. Cache forecasts by security ID, as-of time, feature snapshot, model version, and horizon; cache decisions additionally by portfolio snapshot and mandate. Never reuse a $100,000 account's sizing result for a different account. Never let an LLM explanation cache outlive the quantitative decision it describes.

Suggested operating targets, to measure rather than claim: serve the last completed opportunity snapshot in under one second locally; complete a normal end-of-day update inside a configurable overnight window; preserve the last valid snapshot during provider failures; recover durable job state after a process restart. Provider throughput, not web-serving technology, will usually constrain broad daily research.

### 2.3 Astra's contract

**Current state →** MCP wrappers and deterministic narrative generators exist; no Astra-specific, versioned reasoning layer was found. **Problem →** adding an unconstrained analyst agent could turn weak quantitative inputs into convincing unsupported claims. **Proposed design →** Astra is an evidence reader, research assistant, and orchestrator over approved domain tools. **Algorithm/model →** retrieve a typed evidence packet, request permitted scenario/portfolio computations, generate a cited explanation, then validate every numeric/action claim against the packet. **Required data →** forecast and decision IDs, source spans, feature definitions, uncertainty, historical evaluation, and portfolio constraints. **Expected benefit →** useful synthesis without delegating numerical truth to a language model. **Risks/limitations →** retrieval errors, prompt injection in news, model updates, and narrative hallucination still require controls and evaluation.

Expose tools such as `get_opportunities`, `get_forecast`, `explain_change`, `compare_candidates`, `simulate_portfolio_delta`, `get_model_evidence`, and `fetch_source_excerpt`. Read-only tools are the default. Astra may propose a research experiment or a hypothetical mandate change; it cannot change the production model, overwrite a forecast, turn WATCHLIST into BUY, or authorize an order. A second LLM is not a quantitative verifier.

Use deterministic templates for routine summaries and the language model only when synthesis adds value. Self-hosting the platform does not require self-hosting a large model. Keep the reasoning provider optional and interchangeable; forecasts, rankings, and monitoring must work when it is unavailable. Sensitive portfolio information should be sent to an external reasoning provider only under an explicit deployment policy.

### 2.4 Professional product workflows

**Current state →** Dashboard, Picks, and Analyze organize current scans and individual names. **Problem →** users can see an answer without a reliable view of its opportunity cost, historical validity, or evolution. **Proposed design →** six connected research views sharing the same immutable records. **Algorithm/model →** view-specific projections of the forecast, evidence, change, and portfolio ledgers. **Required data →** timestamped forecasts and actual outcomes, comparable universes, mandate, and risk attribution. **Expected benefit →** faster, better-grounded decisions with less interpretive ambiguity. **Risks/limitations →** too many scores can recreate dashboard clutter; progressive disclosure and clear primary decisions are essential.

| View | Primary question | Required information and interaction |
|---|---|---|
| Best Opportunities | What deserves research or capital now? | Horizon, expected benchmark-relative return, downside range, evidence status, data age, rank stability, next catalyst, and portfolio fit; allow “no suitable opportunities” |
| Rankings | Why is A ahead of B? | Frozen universe and coverage denominator, global/sector ranks, factor families, comparable model coverage, and rank interval under resampling; distinguish movement in stock data from movement in peers |
| Forecasts | What outcomes are plausible? | 1M/3M/6M distributions, mean and median, prediction intervals, P(outperform), P(loss beyond mandate threshold), calibration history, and scenario assumptions; avoid a lone price target |
| Thesis Changes | What changed and what would invalidate the thesis? | Before/after evidence, fact revisions, price reaction, model-version contribution, action consequence, and source links; group repeated publications of the same event |
| Portfolio Intelligence | What is the best next change to this book? | Existing holdings, cash, factor/sector/issuer exposure, marginal risk, proposed additions/reductions, alternatives, transaction costs, and binding constraints |
| Model Performance | Does the system deserve trust? | Locked forecast vintages, matured versus pending counts, net benchmark comparison, calibration/coverage, factor attribution, trial history, ablations, and failures |

An opportunity row must distinguish “top 5% of covered stocks,” “estimated 56% chance of outperforming,” and “limited evidence.” Those are different statements. Illustrative numbers in design mockups must remain labeled as examples. Missing and stale data are visible states, not neutral gray scores that silently enter rankings.

## 3. Recommended algorithms and models

### 3.1 Market-wide discovery

**Current state →** current constituent/curated lists and successful scan responses define coverage. **Problem →** popularity, provider availability, and scan mode influence which opportunities can appear. **Proposed design →** a dated security master and daily eligible universe, followed by broad cheap feature computation and selective expensive evidence collection. **Algorithm/model →** versioned deterministic eligibility; cross-sectional ranking over every covered eligible name; a stratified exploration sample for enrichment. **Required data →** exchange directories, issuer/security mappings, security type, price/volume history, listing changes, and corporate actions. **Expected benefit →** repeatable coverage and discovery beyond familiar names. **Risks/limitations →** current free listings cannot reconstruct all historical delisted securities; incomplete coverage must remain explicit.[^31]

Start with common shares satisfying configurable liquidity, price, and history rules. As an initial operating hypothesis, use median 60-session dollar volume of at least $10 million, price at least $5, and sufficient history for each feature family. These are capacity/risk policies, not optimized alpha thresholds. Keep ETFs, ADRs, banks, insurers, REITs, recent IPOs, and pre-revenue biotech in separate model cohorts where accounting or payoff structure differs. Do not call excluded securities “bad investments.”

Do not fetch rich fundamentals only for yesterday's winners. That creates a self-reinforcing coverage bias. Enrich a rotating random sample stratified by sector, size, and coverage in addition to the leading candidates. Log the sampling probability. Discovery quality includes what the platform failed to observe.

### 3.2 Cross-sectional alpha and factor families

**Current state →** hand-weighted, partly duplicated score families. **Problem →** correlated confirmations, scale discontinuities, and no measured marginal value. **Proposed design →** a small, versioned feature library with economically distinct families and horizon-specific pooled models. **Algorithm/model →** equal-family rank baseline, regularized linear model, then shallow gradient-boosted tree challenger; shrink sector deviations toward a pooled model. **Required data →** as-of features and matured forward returns across securities and dates. **Expected benefit →** measurable nonlinear improvement while preserving a simple comparison model. **Risks/limitations →** low signal-to-noise, correlated observations, missing coverage, and model-selection overfit.

Academic evidence supports testing nonlinear interactions, but it does not establish that QUANTA's smaller free-data panel will reproduce published results. Start with ridge/elastic net or a regularized additive model. Add one shallow boosted-tree family only after the baseline is reproducible. Avoid dozens of algorithms searching the same small sample.[^35]

Use grouped ablations: price momentum/trend, fundamental quality, valuation, operating improvement, revisions/surprise, event information, ownership, and risk. If RSI, MACD, moving-average alignment, and trend all represent the same latent exposure, measure their incremental contribution after the price family is present. Do not award four independent confidence points for four transformations of the same price series.

### 3.3 Dynamic weighting and regime context

**Current state →** SPY/VIX rules select fixed factor weights; several parts of the system have different regime definitions. **Problem →** abrupt switches, noisy state estimates, and repeated application of the same market information. **Proposed design →** one market-state object; conservative, slowly changing conditional effects. **Algorithm/model →** continuous volatility, trend, breadth, credit, and rate features interacting with the pooled alpha model; regularize regime interactions toward zero. **Required data →** contemporaneously available market series, macro vintages, and sufficient matured observations per state. **Expected benefit →** adaptation without pretending a regime label is a known truth. **Risks/limitations →** few independent crises and delayed 6M outcomes make aggressive online learning unreliable.

A regime model should first affect risk budgets and uncertainty, where volatility and correlation are more estimable, before it controls expected return. An HMM with filtered state probabilities is a later challenger; using full-sample smoothed states in historical forecasts leaks future information. State labels such as “crisis” are interpretations, not latent-state ground truth.

For an ensemble, update expert weights only from **matured out-of-sample losses**. Use nonnegative weights summing to one, a penalty for departure from equal weights, and a penalty for rapid turnover in weights. Cap any regime tilt unless it has robust incremental evidence. A six-month expert cannot be judged from the last week's price moves. More frequent reweighting is not inherently more adaptive.

### 3.4 Probabilistic 1M/3M/6M forecasts

**Current state →** analyst/scenario targets, heuristic target timing, and Monte Carlo probabilities. **Problem →** model assumptions are presented too close to empirical forecasting confidence. **Proposed design →** forecast return distributions with explicit benchmark, start time, horizon, and calibration history. **Algorithm/model →** a pooled regularized mean model plus conditional quantile models or a carefully specified distributional model; out-of-time calibration and benchmark-aware scenario composition. **Required data →** forward total returns, execution convention, feature snapshots, and outcome vintages. **Expected benefit →** decisions reflect downside and uncertainty rather than a falsely precise target. **Risks/limitations →** tail probabilities are data-hungry; conformal coverage is not a guarantee for every stock or regime.[^37][^38]

Define the research target as the total return from a specified executable reference price—initially the next session's open—through 21, 63, or 126 sessions. Include dividends and corporate actions. Define benchmark-relative return against a disclosed broad-market total-return benchmark over the same window. For alpha attribution, also retain sector-relative and factor-residual targets. Benchmark-relative return and factor alpha are not interchangeable.

Train separate horizon heads. A six-month forecast is not six times the one-month mean, and its interval is not automatically √6 times the one-month interval. Return horizon intervals need not be nested in every conditional setting; do not cosmetically force coherence that the model cannot support. For jointly simulated paths, ensure the same path generator produces all horizons and test their dependence.

Publish a mean, median, 10th/90th percentiles, P(benchmark-relative return > 0), and P(total return below the mandate's loss threshold). These probabilities should derive from one coherent estimated distribution where feasible. If a separate classifier materially improves an event forecast, version it separately and disclose any inconsistency rather than quietly blending outputs. Never claim that two quantiles alone identify the full CDF or its mean.

For the first distributional baseline, use a regularized location estimate with empirically resampled, volatility-scaled residual blocks from training data. This is a finite empirical scenario distribution; test tail sensitivity and explicitly acknowledge unobserved extreme events. Compare shallow quantile boosting later. Prevent quantile crossing by ordered parameterization or documented rearrangement, then evaluate the resulting distributions.

Fit calibration only on a chronologically later calibration block whose labels are already known. Use sigmoid calibration for sparse binary events, isotonic only with enough independent evidence. Track Brier/log loss and reliability separately: a lower Brier score alone does not prove better calibration. For intervals use pinball loss, empirical coverage, and interval width. Adaptive conformal methods can help monitor and adjust marginal coverage under changing conditions; delayed labels, selection into the top-ranked set, and cross-sectional dependence limit guarantees.[^37][^38]

The endpoint return distribution is **not** a target-before-stop probability. A stock may finish up after first crossing a stop. First-passage decisions need a separately validated path model or observed bar-by-bar policy replay.

### 3.5 Valuation and event interpretation

**Current state →** DCF/analyst blends and event-type lookup returns. **Problem →** targets inherit anchors, and event occurrence is confused with unpriced surprise. **Proposed design →** separate valuation scenarios from return forecasts; model event novelty and the market's response. **Algorithm/model →** peer-relative valuation, reverse DCF, event-study residual returns, and eventually pooled event hazard/impact models. **Required data →** as-filed financials, enterprise/equity bridge, filing/publication timestamps, event type, and price reaction. **Expected benefit →** exposes what must be true for a valuation and whether new information remains unpriced. **Risks/limitations →** sector accounting differs; reliable consensus surprises and event histories are sparse on free feeds.

Reverse DCF is especially useful: solve for the revenue growth, margins, reinvestment, or discount rate needed to justify today's enterprise value. It uses price as the question rather than hiding price inside the answer. Display the assumptions, sensitivity range, and missing accounting inputs. Do not treat its implied growth as a return forecast.

For earnings, distinguish reported operating improvement from analyst surprise. Without a reliable contemporaneous consensus, compute year-over-year seasonally comparable earnings/revenue changes, margin changes, accruals, and cash-flow confirmation. Label these as accounting change signals. For ownership, prioritize well-parsed Form 4 transactions and delayed 13F crowding context; do not call either real-time institutional order flow. Form 13F is generally filed up to 45 days after quarter-end and does not reveal the complete current portfolio.[^39]

### 3.6 Entry, accumulation, hold, reduce, exit, and invalidation

**Current state →** ATR/support levels, threshold actions, fixed catalyst pullbacks, and heuristic targets. **Problem →** these combine investment attractiveness, timing, ownership, and risk into labels that do not measure the best available action. **Proposed design →** select an action by expected incremental utility conditional on the existing position. **Algorithm/model →** discrete candidate position changes evaluated under forecast scenarios, costs, covariance, and hard constraints; add entry-policy models only after separate replay. **Required data →** current holdings, cash, forecast distributions, liquidity/cost assumptions, events, and prices. **Expected benefit →** a good stock can correctly be HOLD or REDUCE when already overweight. **Risks/limitations →** estimated utilities are uncertain and gap losses can exceed stop-based budgets.

Start with the simplest executable policy: evaluate after completed daily data, rebalance weekly at the next eligible open, and compare with monthly rebalancing. Add risk/event-triggered reviews between scheduled rebalances. No universal pullback or profit-taking ladder should be presumed to improve this baseline.

Define actions operationally:

| Action | Quantitative meaning |
|---|---|
| Enter | A positive first position improves conservative expected portfolio utility and passes evidence/data gates |
| Accumulate | Increase an existing or staged position toward a target, subject to remaining exposure and event limits |
| Hold | The current position lies within a transaction-cost-aware no-trade band |
| Reduce | A smaller position improves utility or cures a risk violation |
| Exit | Target weight becomes zero, a hard invalidation is confirmed, or risk constraints require liquidation |
| Watch / abstain | Evidence, data, or incremental utility does not justify a change |

An **entry zone** is a range of hypothetical fill prices over which the proposed order still clears utility and loss constraints, holding non-price evidence fixed. It expires on new material information. At each price recalculate return-to-entry, valuation inputs affected by price, costs, size, and downside. A limit order needs a fill model and adverse-selection analysis; an attractive conditional payoff given a fill does not prove waiting is superior to entering now.

Keep three independent invalidations: factual thesis failure, portfolio-risk breach, and a chosen price-based protective exit. Stops are execution instructions, not guaranteed maximum losses. Test fixed-horizon holding, thesis exits, volatility stops, and time stops as separate policies. ATR is a volatility measure, not a learned optimal stop distance. Reward/risk geometry is useful context but does not establish positive expectancy.

### 3.7 Portfolio optimization

**Current state →** risk-first sizing, sector/correlation checks, and an inverse-volatility refinement. **Problem →** selected-name rankings do not evaluate the best change to the existing book; exposure constraints can be altered by subsequent transformations. **Proposed design →** optimize all proposed changes jointly, preserve cash, and validate final rounded positions. **Algorithm/model →** shrinkage/factor covariance, conservative expected-return utility, transaction-cost penalties, exposure constraints, and stress scenarios. **Required data →** actual positions and cash, correlated return history, issuer/sector/factor exposures, and liquidity. **Expected benefit →** capital goes to diversifying incremental opportunities, not simply the highest standalone scores. **Risks/limitations →** return-estimation error dominates; all fallbacks must preserve hard constraints.[^19][^18]

Use a factor covariance model Σ = BΩBᵀ + D for a large universe, with stable broad-factor exposures and shrunk idiosyncratic variances. For a small selected book, a tested shrinkage covariance estimator is a simpler first implementation. Stress correlation concentration rather than assuming past diversification survives a crisis. Shrinkage addresses estimation noise; it does not make an inaccurate return forecast accurate.[^33][^36]

Never auto-relax a user risk limit to obtain a feasible solution. Cash makes long-only feasibility possible; if existing holdings violate constraints, return a staged remediation proposal and its remaining violations. Whole-share rounding, locked positions, and minimum orders need a final feasibility pass. A failed optimizer should produce a feasible conservative alternative or abstention, not unconstrained normalized weights.

## 4. Proprietary QUANTA Alpha scoring methodology

### 4.1 What “proprietary” should mean

The defensible intellectual property is the reproducible data history, validated feature definitions, calibrated models, decision ledger, and learning process. Renaming a weighted indicator sum is not a moat. The following methodology is a **proposed research specification**, not an already validated proprietary alpha formula.

**Current state →** score, stars, signal strength, confidence, risk grade, and action partially overlap. **Problem →** one number is being used for several incompatible questions. **Proposed design →** separate investment signal, uncertainty, evidence, and portfolio fit. **Algorithm/model →** conditional return distribution plus a conservative utility-based rank; descriptive scores remain display mappings. **Required data →** common-universe predictions, risk scenarios, data quality, calibration, and a portfolio snapshot. **Expected benefit →** each published number has a falsifiable interpretation. **Risks/limitations →** utility parameters encode the mandate; no scoring formula can eliminate model uncertainty.

### 4.2 Feature and model construction

For security i, date t, horizon h, feature j:

1. Resolve the feature strictly as available at the decision cutoff.
2. Flag missing, stale, restated, and economically inapplicable values separately.
3. Normalize using robust cross-sectional ranks or median/MAD transforms within the relevant cohort. Sector normalization is appropriate for some accounting ratios; retain separate sector-allocation information rather than neutralizing all sector effects automatically.
4. Fit imputation, clipping parameters, feature selection, and any learned transformations only within training data. Same-date ranking across an observable eligible universe is permissible; ranking across future dates is not.
5. Reduce duplicate signals through grouped regularization and incremental ablation, not arbitrary bonuses for indicator agreement.

A transparent baseline forecasts benchmark-relative return:

```text
mu(i,t,h) = intercept(h) + sector_effect(i,h)
            + sum_j beta(j,h) * feature(i,t,j)
            + sum_j,k gamma(j,k,h) * feature(i,t,j) * market_state(t,k)
```

Penalize both beta and gamma; shrink sector effects toward zero. Market state is a small vector of continuous variables, not a claim of certainty about tomorrow's regime. Estimate on date-balanced panels so that one date with more covered stocks does not dominate the fit. Compare to a price-only model and simple factor rank model.

For model experts m, form a mixture distribution rather than averaging arbitrary scores:

```text
F_mix(r | x,t,h) = sum_m omega(m,t,h) * F_m(r | x,t,h)
omega >= 0; sum_m omega = 1
```

The weights minimize past matured out-of-sample proper loss with shrinkage toward equal weighting and a temporal stability penalty. Quantiles of a mixture are obtained from its combined CDF, not by averaging expert quantiles. Evaluate and, where justified, recalibrate the final ensemble on a separate matured calibration set; individually calibrated experts do not automatically make their mixture calibrated for a newly selected population. Model diversity is measured by prediction-error correlation and incremental performance, not model names.

### 4.3 The QUANTA Opportunity Score

Use a transparent reference mandate for market rankings and the actual mandate for portfolio recommendations. Fix a reference incremental weight δ, such as 1% of NAV, for comparable standalone risk calculations; this is a display/reference convention, not an automatic order size.

Define reference utility per unit of incremental weight:

```text
u_ref(i,h) = mu_excess(i,h)
             - kappa(h) * se_mu(i,h)
             - 0.5 * lambda(h) * delta * variance(i,h)
             - eta(h) * downside_expected_shortfall(i,h)
             - estimated_round_trip_cost(i,delta,h)
```

All quantities are horizon-consistent returns; uncertainty is estimation uncertainty in the mean, not the stock's raw volatility. Estimate `se_mu` with date-block resampling and model refitting, preserving cross-sectional dependence. Downside expected shortfall is a positive loss measure under the forecast distribution. Lambda, eta, and kappa represent a documented reference mandate and conservatism; avoid fitting a large set of these coefficients on the final test period. Tail and variance penalties may overlap, so compare simpler nested utility forms.

Set **Opportunity Score = 100 × empirical percentile of u_ref among comparable, eligible, covered securities for that horizon and snapshot**. Round for display. Ties should share a rank or use a disclosed deterministic rule. Publish the raw expected return and uncertainty beside the percentile.

A score of 95 means a top-five-percent position in that covered reference universe. It does **not** mean a 95% success probability, a positive expected return, or a mandate to buy. If all utilities are negative, the highest percentile still does not clear the absolute action hurdle. Keep 1M, 3M, and 6M ranks separate; use a chosen mandate horizon rather than averaging correlated horizons into a false consensus.

### 4.4 Portfolio Fit and actions

For current weights w and candidate changes d, solve or enumerate a constrained approximation to:

```text
maximize_d  mu_total' d - kappa * uncertainty_penalty(d)
            - lambda/2 * [(w+d)' Sigma_h (w+d) - w' Sigma_h w]
            - costs(d) - eta * incremental_stress_loss(w,d)

subject to w+d >= 0, sum(w+d) <= 1,
           issuer / sector / factor / liquidity / turnover / event limits,
           actual cash and locked-position constraints.
```

Include cash return explicitly when comparing total-return alternatives. If optimizing active returns relative to a benchmark, use a coherent active-risk objective; do not subtract benchmark return and then silently optimize a different risk definition. Transaction costs refer to actual current-to-target turnover, not every holding's hypothetical round trip.

Portfolio Fit is the predicted incremental utility of the feasible change, with the binding constraints shown. A standalone top-ranked semiconductor may have negative Portfolio Fit in an already semiconductor-heavy book. A moderately ranked diversifier may be more useful. This is the central difference between stock selection and investment decision quality.

### 4.5 Evidence and confidence

Publish four separate dimensions:

| Dimension | Meaning | Forbidden interpretation |
|---|---|---|
| Forecast probability | Calibrated probability of a precisely defined outcome | Probability that the whole thesis is true |
| Forecast uncertainty | Outcome range and estimation uncertainty | A single confidence percentage derived from score |
| Evidence state | Experimental, shadow, validated within scope, degraded, or retired | Universal authorization across all universes/horizons |
| Data quality | Coverage, source freshness, semantic validity, and conflicts | A positive or negative opinion about the business |

A missing-data model is a separately evaluated model, not ad hoc reweighting. Maintain explicit core-price, core-plus-financials, and enriched-event model tiers only if each has enough data to validate. Compare tiers in their shared population before merging ranks; otherwise show coverage-qualified rankings. Downgrade evidence or abstain when the present input mix is outside the validated domain.

### 4.6 Exact thesis-change accounting

**Current state →** textual thesis and signal/downgrade history. **Problem →** a lower score may reflect a price change, vendor outage, peer movement, or model update rather than business deterioration. **Proposed design →** store an additive change decomposition before generating prose. **Algorithm/model →** replay frozen old/new inputs and model versions, with explicit attribution groups; use group Shapley decomposition where order independence is worth the computational cost. **Required data →** immutable old/new feature snapshots, model/calibration versions, ranking population, and portfolios. **Expected benefit →** every conviction change is auditable. **Risks/limitations →** attribution explains model behavior, not causal economic truth; correlated inputs make allocation of interaction effects convention-dependent.

For a raw utility change, use a documented telescoping chain:

```text
old result
 + changed company facts under old prices/model/context
 + changed prices under old model/context
 + changed macro/event context under old model
 + changed model and calibration
 = new raw utility

Then separately reconcile:
 + universe/peer movement → percentile-rank change
 + portfolio/mandate change → action and sizing change
```

The sequential decomposition sums exactly but is order-dependent. Freeze and disclose the ordering or compute group Shapley values over feasible block replacements. Log source corrections separately from newly released economic information. Use the appropriate output scale: linear coefficients are additive in their native prediction scale; logistic coefficients add in log odds, not probability points. Do not reproduce the older plan's suggestion that fitted coefficients automatically yield probability-point attribution.[^29]

An illustrative alert: “3M expected excess return fell 1.2 percentage points: margin revision −0.5, price appreciation −0.4, market context −0.2, model update −0.1. Rank fell from 92 to 78; portfolio recommendation remains HOLD because transaction costs exceed the benefit of trimming.” These numbers are a design example, not a QUANTA forecast.

## 5. Data and feature architecture

### 5.1 A realistic free-data strategy

**Current state →** yfinance-centered acquisition with optional provider adapters, current fundamental snapshots, SEC catalyst metadata, and separate caches.[^26][^27] **Problem →** inconsistent availability/units, no complete historical feature vintage, and repeated requests in expensive analysis paths. **Proposed design →** source-specific adapters feeding immutable snapshots and normalized as-of facts; start collecting forward history immediately. **Algorithm/model →** deterministic validation, deduplication, security resolution, and as-of joins, with separate live and reconstructed-research clocks. **Required data →** prices/actions, filings, listings, macro vintages, and optional estimate/news snapshots. **Expected benefit →** reproducible research with visible coverage limitations. **Risks/limitations →** mostly free sources cannot be assumed to deliver complete delisted histories, historical consensus, full news archives, or production-grade redistribution rights.

| Data family | Practical first source | What it supports | Limit that must stay visible |
|---|---|---|---|
| Listed security inventory | Nasdaq Trader symbol directories, reconciled with issuer metadata | Current broad universe, ETF/test-issue filtering, daily listing snapshots | Current directory is not a survivorship-free historical master; security-type resolution still needs work |
| Daily prices and actions | Existing yfinance adapter; a separately validated fallback | EOD price factors, corporate-action checks, forward collection | Preserve raw and adjusted semantics; no assumed completeness or service guarantee; classify gaps rather than silently switching series |
| Company financials | SEC submissions, Company Facts, original filings | As-filed accounting features and amendments | Company Facts does not normalize all custom tags or segmented contexts; fiscal periods and units require accounting-aware resolution |
| Corporate events | SEC 8-K/10-Q/10-K and issuer releases | Material event evidence and publication timestamps | Filing date is not always the earliest release of the economic fact |
| Macro | FRED/ALFRED series and vintages | Rates, credit, inflation/activity context | Latest revised values must not be inserted into earlier feature rows |
| Risk-factor returns | Kenneth French research factors | Ex-post attribution, stable baseline comparisons | Research factor returns are not directly tradeable; record dataset version and publication availability for any predictive use |
| Earnings estimates | Current provider estimate fields, archived prospectively | Revision experiments from recorded snapshots | No historical consensus backfill claim; preserve fiscal period identity and provider availability |
| Ownership | SEC Form 4, later 13F | Insider transaction context, lagged holdings/crowding | Transactions need type/role/amendment handling; 13F is delayed and incomplete |
| News | Available provider/issuer feeds, stored within permitted use | Forward event novelty and evidence summaries | Missing history, duplicate syndication, source bias, and access rights |

SEC offers unauthenticated submissions and XBRL APIs plus nightly bulk archives; use bulk ingestion to seed the store and incremental requests thereafter. This supports a capable free-data foundation, but it is not a turnkey point-in-time financial database. Nasdaq's directory documents current security fields. ALFRED supports historical information vintages. The yfinance project itself distinguishes its open-source software from rights to the underlying Yahoo data and describes personal-use limitations; self-hosting does not grant redistribution rights.[^30][^31][^32][^34]

Buy data only against a measured bottleneck. The first likely high-value purchase is delisting/corporate-action and historical membership coverage if broad historical alpha validation is the objective. Historical earnings-estimate revisions become worthwhile only after a forward experiment shows incremental value. Premium headlines, options flow, and many redundant feeds should not precede these essentials. Provider choice and entitlements require a fresh evaluation at purchase time.

### 5.2 Canonical data contracts

Every raw observation needs `source`, `source_record_id`, `schema_version`, `payload_hash`, `received_at`, and a link or raw-object reference. Every market fact needs economic time, public-availability time where known, and operational acquisition time. Timestamps are UTC with exchange-calendar interpretation stored explicitly.

| Table / dataset | Essential fields and constraints |
|---|---|
| `issuers` / `securities` | Internal immutable IDs, issuer ID, share class, listing venue, currency, security type; CIK is an issuer identifier, not a sufficient security ID |
| `symbol_history` / `universe_membership` | Symbol, security ID, effective interval, source, first observed time, eligibility reason; no ticker-string joins across the whole history |
| `corporate_actions` | Split/dividend/merger/delisting terms, ex/effective/payment dates, announcement/availability times, source revision |
| `raw_objects` | Immutable payload location and checksum, retrieval timestamps, adapter version, rights/retention metadata |
| `daily_bars` | Security/session, raw OHLCV, adjustment basis, source/vintage, exchange calendar, completion state, quality flags |
| `fundamental_facts` | Accession, taxonomy/tag, units, duration/instant, fiscal period, statement context, publication/acceptance time, amendment linkage, original and normalized values |
| `estimate_snapshots` | Security, estimate metric, fiscal-period end, analyst coverage, estimate value, observation/availability timestamps, provider field meaning |
| `events` / `event_evidence` | Event ID, issuer roles, event type, first publication, source spans, novelty cluster, extraction/model version, confidence in extraction |
| `macro_vintages` | Series, observation period, vintage date, release time, value, source revision |
| `feature_values` | Security, decision cutoff, feature definition/version, value, missingness/status, source lineage, eligible-universe ID |
| `forecast_runs` / `forecasts` | Model/calibrator/data/code hashes, train/calibration cutoffs, forecast timestamp, reference execution rule, horizon, full distribution representation and derived statistics |
| `decisions` / `decision_changes` | Forecast ID, portfolio/mandate snapshot, action, target weight, constraints, reason codes, evidence state, immutable before/after attribution |
| `positions` / `transactions` | Actual holdings, lots if needed, cash, trades, fees, corporate-action adjustments, reconciliation status |
| `outcomes` / `evaluations` | Forecast ID, exact start/end sessions, maturity time, total/benchmark return, realized loss/coverage, source vintage, correction record |
| `experiments` / `model_registry` | Hypothesis, all tried variants, selection rules, artifacts, scope, promotion evidence, status, supersession |

Never overwrite an old forecast or label correction. Append a superseding record, retain the originally reported version, and permit both “as reported” and corrected analyses. Keep production-generated forecasts distinct from retrospective reconstruction. A historical filing acquired today may support a reconstructed observable-information backtest if its original availability can be established; it cannot be represented as a forecast that the platform actually issued then.

Price adjustment deserves its own contract. Back-adjustment by future splits can preserve some scale-invariant return features yet leak into nominal-price eligibility thresholds, per-share accounting joins, and simulated fills. Use period-correct price/share units for those operations. Distinguish price return from total return, split adjustment from dividend reinvestment, and provider fallback from equivalent data.

### 5.3 Feature library

Start with roughly 25–40 well-defined candidate features, not hundreds of indicators. The count is a scope guideline, not a statistical threshold.

| Family | First features | Definition safeguards |
|---|---|---|
| Momentum / trend | 12-month momentum excluding the latest month, 6M momentum, sector-relative momentum, short-term reversal, trend persistence | Explicit session endpoints; distinguish residual momentum from market beta; keep reversal a separate hypothesis |
| Operating quality | Gross/operating profitability, operating cash flow to assets, accruals, margin stability | Use contemporaneously available statements; do not use ROE naively when equity is negative or distorted by buybacks |
| Improvement | Change in margins, cash conversion, revenue growth acceleration, dilution trend | Compare like fiscal periods; derive quarters from YTD only with compatible vintages/context |
| Valuation | Earnings/FCF yield, enterprise-value ratios, sector-relative valuation, reverse-DCF implied assumptions | Separate nonpositive denominators; distinguish equity and enterprise cash flows; industry-specific templates |
| Revisions / surprise | Coverage-normalized revision breadth, price-scaled EPS revisions, operating surprise versus a seasonal baseline | Same fiscal period across snapshots; estimate changes are not surprises; no false 30-day labels for 90-day fallbacks |
| Risk / tradability | Realized/downside volatility, beta, residual volatility, drawdown, turnover, liquidity, event proximity | Dates/currencies aligned; missing covariance cannot become zero risk |
| Macro sensitivity | Rate/credit sensitivity, market breadth context, volatility state | Use available vintages; keep slow exposures distinct from fast scenario shocks |
| Event / sentiment | New material disclosure, numeric guidance change, source-normalized novelty, price-response divergence | Validate entity roles, deduplicate common stories, record text model versions and source spans |
| Ownership | Open-market insider purchase intensity, issuer-level insider clusters, lagged institutional crowding | Separate purchases from grants/options/tax sales; source delays and incomplete coverage are inputs |

Technical indicators earn admission through ablation after the baseline price features. Fundamental models for financial institutions and REITs need their own accounting templates. Avoid penalizing firms simply because a generic industrial-company ratio is inapplicable.

### 5.4 Scheduling, caching, and observability

Use the exchange calendar, not a fixed UTC “market close.” After each completed session: ingest bars/actions, validate, resolve eligible securities, update changed facts, build features, generate forecasts, calculate portfolio deltas, atomically publish the snapshot, then queue explanations. Outcomes mature by their exact trading-session schedule. Poll filing/event sources at a rate compatible with their rules and the research horizon; a 1–6 month model does not need a real-time tick architecture.

Retrain initially monthly or quarterly depending on horizon and data volume; calibrate only as outcomes mature. Material data corrections trigger a new computation with a correction reason, not silent history edits. Cache raw data, normalized facts, features, forecasts, and explanations separately, each with version-aware keys and dataset-specific freshness.

Observe coverage by source/sector/model tier, stale-feature share, provider errors and circuit state, corporate-action discrepancies, job duration/retries, missing predictions, model drift, forecast maturity lag, constraint violations, and publication failures. Add structured run/trace IDs across API, worker, and ledger. Test restart recovery and backup restoration. For wider access, add authenticated access control, stable secret management, and account isolation before exposing the current local-only app.

## 6. Backtesting and validation framework

### 6.1 One evaluator, three kinds of evidence

**Current state →** corrected APEX execution exists alongside simplified and statistically misleading backtest paths. **Problem →** a good-looking metric may measure a different strategy, universe, or timing convention from the live decision. **Proposed design →** consolidate on one execution/evaluation kernel and explicitly distinguish retrospective diagnostics, validated historical experiments, and forward shadow results. **Algorithm/model →** nested chronological selection, purged label intervals, a locked test, realistic portfolio replay, dependence-aware uncertainty, and forward evaluation. **Required data →** historical membership/actions, as-of features, source vintages, costs, benchmark, and complete forecast records. **Expected benefit →** performance claims refer to the actual executable policy. **Risks/limitations →** incomplete historical data remains incomplete after any statistical correction.

**Tier A: engineering diagnostics.** Synthetic fixtures and causal tests verify math, chronology, action consistency, and constraint preservation. The current 160-test pass belongs here. Golden tests protect behavior but can also preserve a bad algorithm; they do not establish economic validity.

**Tier B: retrospective research.** Use reconstructible price/accounting features and document membership, delisting, availability, and corporate-action limits. Historical evaluation with missing delistings remains coverage-limited even if train/test chronology is perfect. The current technical/risk reconstruction is a baseline hypothesis, not authorization for every live feature.

**Tier C: prospective shadow record.** Save forecasts for the full evaluated population before outcomes, including rejected and abstained names. Evaluate every vintage by a fixed maturity rule. This is essential for free-data estimate/news features that lack a reliable archive. Six months of operation cannot prove six-month forecasting skill across regimes: the newest 6M outcomes will barely have matured and successive vintages will overlap heavily.

### 6.2 Splits and leakage prevention

Use date-grouped outer walk-forward folds. An illustrative mature-data configuration is five years of training, one year of calibration, and the next year of testing, rolled forward yearly, with inner chronological selection inside the training period. This is a starting research design, not a promise that the repository has adequate data. Shorter history supports simpler models and weaker claims, not more aggressive fitting.

For each horizon, purge any training row whose outcome interval reaches into a later calibration/test boundary or whose label would not yet be observable at the fit cutoff. Calibration rows also require matured labels before the test starts. Split all securities by common dates; do not scatter rows from the same day between training and testing. A generic row-wise `TimeSeriesSplit` gap is not enough for a stock panel with variable label intervals.

Use embargo where fold construction allows observations after a held-out interval to enter training, and choose dependence buffers through a documented policy. A purely expanding past-only split chiefly needs label maturity and boundary purging; a ritual fixed embargo does not cure stale vintages, current constituents, or future restatements.

Freeze transforms, feature selection, parameters, calibrators, ensemble weights, risk model estimation, universe rules, and action policy before each outer test. Precomputed causal rolling features can be reused; precomputed full-sample fitted transforms cannot. Keep a registry of all experiments and manual changes informed by previous test results. A small parameter grid alone does not account for repeated human inspection or research iteration. Deflated Sharpe and related selection-bias diagnostics are useful supplements, not substitutes for a genuinely untouched test and a prospective record.[^42]

### 6.3 Execution and capital accounting

Retain the corrected APEX semantics and broaden them into an event-based portfolio replay. A close-derived signal is first executable at the next specified tradable time. Entry sizing cannot use the later day's high, low, volume, or closing equity. Stops are active from entry when the policy says so; gap-through stops fill at an executable price, not at the stop. If daily OHLC touches both stop and target and order is unknown, use a conservative tie rule and show sensitivity to alternatives.[^41]

Include cash, dividends, splits, mergers, delistings, halted trading, stale marks, fees, slippage, partial fills, capacity, and residual holdings at the end. Do not omit unclosed losers from trade statistics. Do not zero out illiquid positions because a quote is missing. Isolate adjusted research returns from actual share-and-cash accounting to avoid counting dividends twice.

Use liquidity-bucket spread/impact assumptions and multiple cost scenarios. A simple initial cost function may combine half-spread, commission, and impact proportional to volatility × square root of participation; calibrate when actual execution evidence is available. With only daily volume, use a conservative lagged liquidity estimate for admission, not future realized daily volume. Run at baseline, doubled, and more severe stressed costs. These multipliers are sensitivity scenarios, not measured execution costs.

Carry portfolio state continuously across model refreshes. If research folds liquidate at boundaries, explicitly include liquidation costs and identify that as a different policy. Never concatenate overlapping test-window returns as if they were a single investable equity curve.

### 6.4 Benchmarks and ablations

Compare against broad-market total return, cash, the eligible-universe equal-weight portfolio, a sector/size-matched baseline, simple momentum/quality/value rules, and a price-only model. For a discretionary long-only product, use both absolute risk-adjusted performance and benchmark-relative performance. A strategy holding large cash balances should not claim stock-selection alpha merely from lower volatility in a falling market.

Random-entry and random-exit baselines are diagnostics, not a complete validation standard. Run multiple seeds with matched exposure, liquidity, entry counts, holding periods, and costs; a single lucky or unlucky random path is not a hurdle. Equal-universe baselines help diagnose selection effects but do not remove survivorship bias.

Required ablations: remove each feature family; static versus dynamic weights; pooled versus regime-conditioned models; mean model versus quantile challenger; with/without event enrichment; rank-only versus portfolio-aware selection; next-open versus pullback entry; hold-to-horizon versus stop/thesis exits; normal versus degraded data coverage. Report turnover and complexity costs with every claimed gain.

### 6.5 Metrics and uncertainty

| Question | Metrics | Statistical treatment |
|---|---|---|
| Are stocks ranked well? | Per-date Spearman IC, top-minus-bottom spread, top-k benchmark-relative return, precision among actionable outcomes | Block resample common dates and retain cross-sectional dependence; evaluate liquidity/sector cohorts |
| Are return forecasts useful? | Out-of-sample R² versus a disclosed baseline, MAE/RMSE, pinball loss, CRPS where a full CDF exists | Retain magnitude errors and tail errors; do not rely solely on direction accuracy |
| Are probabilities calibrated? | Brier/log loss, reliability curves with counts, calibration slope/intercept | Separate calibration from discrimination; evaluate the selected opportunity population too |
| Are intervals honest? | 50/80/90% empirical coverage, width, conditional slices | Show uncertainty and effective observation count; wide intervals can cover well while offering little decision value |
| Does the portfolio work? | Net CAGR, excess-return Sharpe, Sortino, max drawdown, drawdown duration, Calmar, information ratio, turnover, capacity | Use the actual daily equity curve; compare aligned benchmarks and account for serial dependence |
| Is there alpha? | Factor-adjusted intercept and uncertainty, active return, beta/style exposures | Distinguish realized attribution from forecast alpha; sensitivity to factor specification |
| Do trade rules help? | Net expectancy, payoff ratio, hit rate, MAE/MFE, realized stop slippage, fill rate, opportunity cost | Define a trade and horizon precisely; count only matured outcomes in accuracy denominators |
| Is the model degrading? | Rolling proper loss, IC, calibration, coverage, drift, missingness, realized utility | Distinguish data failures from predictive failure; use matured labels and avoid repeated uncorrected significance tests |

For overlapping 126-session forecasts, thousands of stock-date rows are not thousands of independent observations. Use common date blocks at least comparable to the horizon as a starting point, examine sensitivity to block length, and report conservative uncertainty. Non-overlapping subsampling helps but does not automatically remove regime persistence. The 1M and 3M outcomes of the same names are not independent confirmations.

### 6.6 Promotion, degradation, and retirement

A model progresses through `experimental → shadow → validated_in_scope → degraded/retired`. Every state change cites the universe, horizons, feature availability, execution policy, dataset/model/code hashes, and evidence. A new model version cannot inherit an old model's record invisibly.

Require: no known leakage/accounting defects; reproducible runs; acceptable coverage for its declared scope; comparison with predeclared simple baselines; net economic improvement under reasonable cost sensitivity; calibrated outputs within predeclared tolerances; and stable behavior outside a single favorable cohort. Statistical thresholds and economic hurdles should be specified before evaluation. No universal Sharpe number proves a model is institutional grade. Failure to demonstrate improvement means retain the simpler model or remain a research screener.

For degradation, first test source freshness and semantic changes. If coverage is broken, stop affected new recommendations immediately. If predictive losses worsen on enough matured vintages, widen uncertainty, reduce the model's influence, revert to the validated baseline, or abstain. Do not mechanically retrain on every drawdown; that can chase noise and erase accountability. Preserve the failed model and its forecast record for diagnosis.

## 7. Top 10 highest-value innovations

These are ranked by expected decision value under the free-data constraint, not by novelty of the algorithm. Their benefits are hypotheses to measure.

| Rank | Innovation | Current state → proposed implementation | Benefit and principal limitation | Priority |
|---|---|---|---|---|
| 1 | Immutable prediction accountability | Mutable signal histories → every issued forecast linked to exact horizon, data, model, and matured outcome | Makes honest model improvement possible; evidence takes calendar time to accumulate | P0 |
| 2 | Portfolio opportunity cost | Top-stock list → compare each feasible change with keeping cash, holding, trimming, and buying alternatives | Finds the best next action for the actual book; utility remains sensitive to estimated returns and risk | P1 |
| 3 | Exact thesis-change accounting | Narrative/downgrade fragments → frozen counterfactual recomputation and reconciled contribution ledger | Explains why conviction changed and whether it matters; decomposition is not causal proof | P1 |
| 4 | Coverage-aware discovery with exploration | Scanned/successful names → broad inventory, explicit coverage denominator, stratified random enrichment | Reduces popularity and provider-selection bias; cannot recover unknown historical delistings | P0/P1 |
| 5 | Abstention as a modeled outcome | Always-present relative ranks → positive utility hurdle plus evidence/domain checks | Avoids forced trades in weak or unobservable conditions; may reduce activity more than users expect | P1 |
| 6 | Decision stability under perturbation | Exact rank display → rank/selection frequency across date-block model refits and plausible cost/data variations | Exposes fragile winners and score cliffs; stability can still belong to a consistently biased model | P1 |
| 7 | Event versus price-response divergence | Headline sentiment → novel disclosure paired with abnormal market reaction | Tests whether favorable information is already priced or being ignored; requires event timestamps and matched controls | P2 |
| 8 | Shadow alternatives and regret accounting | Outcomes only for viewed/funded names → evaluate all eligible candidates and predefined alternative actions | Separates discovery, selection, sizing, and exit errors; avoid hindsight-optimal counterfactual policies | P1/P2 |
| 9 | Value of additional information | Fetch everything or just top names → prioritize enrichment where plausible new evidence could change the decision | Saves provider requests and research time; information-value estimates themselves need validation | P2 |
| 10 | Fragility and common-thesis exposure | Sector/pairwise correlation checks → factor exposures plus evidence-backed customer/product/theme concentration | Detects portfolios that are different tickers but one economic bet; inferred links must not replace measured risk | P2/Future |

The last three should begin as deterministic, inspectable research tools. Event divergence can use a residual event-window return conditional on sector/beta and lagged momentum. Information value can initially be the difference between decisions under bounded plausible missing-feature scenarios, before attempting a learned acquisition policy. Common-thesis exposure can begin with verified filing relationships and manually reviewed tags; a graph neural network is not required.

For every innovation, use the relevant data/model contracts in Sections 3–6, add a predeclared evaluation, and retain an ordinary baseline. A new product feature may be useful by saving research time or revealing risk even if it adds no return alpha. Measure that benefit separately rather than forcing every tool to claim stock-picking skill.

## 8. What NOT to build

1. **An LLM price predictor or investment committee whose confidence determines scores.** Narrative sophistication cannot create predictive evidence. Astra orchestrates and explains.
2. **A universal 0–100 “chance of success.”** Relative rank, direction probability, evidence reliability, and portfolio suitability are different quantities.
3. **A 100-factor score before validating a compact baseline.** Correlated features and research degrees of freedom can rise faster than information.
4. **Per-ticker deep neural networks on a few years of daily data.** Pooled cross-sectional learning is the sensible initial unit. More complex models must earn their cost.
5. **A historical earnings-revision or sentiment backtest built from today's snapshots.** Begin prospective collection or acquire reliable historical data.
6. **A “survivorship corrected” switch backed only by current ticker lists.** No adjustment factor restores missing dead companies and their returns.
7. **Intraday order-flow, options-flow, high-frequency, or real-time institutional-flow products on EOD/free data.** The information and execution requirements are different.
8. **Universal Kelly sizing from heuristic or Monte Carlo win rates.** A calibrated distribution still needs uncertainty-aware portfolio constraints; approximate binary win/loss payoffs are often the wrong model.
9. **An optimizer that must fully invest or relax caps when infeasible.** Cash and explicit abstention are valid outputs.
10. **Generic precise entry ladders and stop/target dates for every stock.** Test each execution policy; do not turn ATR multiples into forecasts.
11. **Automatic online retraining in response to recent losses.** Distinguish ordinary risk, data failures, and model deterioration using matured evidence.
12. **A complete framework/UI rewrite before fixing records and contracts.** Preserve useful Flask, CLI, and UI components. Build a new frontend only where the research workflows demand it.
13. **A microservice, Kafka, Kubernetes, vector-database, or graph-model estate from day one.** Modular boundaries and reliable local persistence solve the initial scale problem.
14. **A single generic fundamental formula for industrials, banks, REITs, ETFs, and pre-revenue firms.** Inapplicable data must not masquerade as weak fundamentals.
15. **A retrospective explanation that silently rewrites the original thesis.** Store what the system actually believed, including mistakes and missing evidence.

## 9. Prioritized implementation roadmap

Implementation order follows evidence dependencies. Calendar estimates would be misleading without knowing available engineering time and deployment hardware. Forward 3M/6M evidence has an irreducible waiting period, even if all engineering is completed quickly.

### P0 — Make outputs and records trustworthy

**Current state →** green tests coexist with broken horizon records, fragmented policies, and unvalidated outputs. **Problem →** more models would compound an untrustworthy measurement loop. **Proposed design →** repair outcome semantics, freeze current models as named baselines, establish immutable records, and add final constraint/evidence checks. **Algorithm/model →** exact session-maturity labels, deterministic policy adapter, invariant validation, durable snapshots/jobs. **Required data →** existing sources plus prospective snapshots; no new paid feed required. **Expected benefit →** an honest baseline and a forward record begin immediately. **Risks/limitations →** the platform may initially make fewer recommendations and show more unavailable states.

Deliver in this order:

1. Version and quarantine invalidated performance artifacts. Label model scope and unvalidated outputs consistently in CLI, API, MCP, and UI.
2. Fix `SignalTracker`: trading-session horizons, historical target-session prices, matured-only denominators, no mixed horizons, and immutable correction records.
3. Add a single serializable `Forecast`/`Decision` contract and versioned legacy adapters. Preserve old model scores under explicit legacy IDs.
4. Add final book invariants after all deep confirmation, allocation refinement, mandate scaling, and rounding. Eliminate fail-open unknown risk and prevent missing assets from disappearing from covariance risk.
5. Create source/feature snapshot persistence and a broad current security inventory. Begin archiving all eligible forecasts, estimate snapshots, and material disclosures prospectively.
6. Make the Python domain importable. Extract provider I/O and job persistence from Flask without changing the frontend framework.
7. Add a minimal Model Performance page showing evidence status, coverage, pending/matured counts, and the original forecast record.

**P0 acceptance:** a stopped/restarted process loses no forecast or job identity; a forecast replays from its exact inputs; unresolved observations cannot lower hit rate; all final books obey cash/exposure rules; unvalidated models cannot masquerade as validated advice; data outages have visible status. Manual position-risk calculations can remain available, clearly separated from model-endorsed allocations.

### P1 — Deliver a credible daily research engine

**Current state →** partial research harness and heuristic market scans. **Problem →** no coherent market-wide probabilistic/portfolio workflow. **Proposed design →** daily features, pooled baseline models, calibrated forecast records, portfolio deltas, and thesis changes. **Algorithm/model →** price-only and accounting-augmented regularized models, conservative empirical distributions, shrinkage risk, and cost-aware target weights. **Required data →** validated historical subset plus continuing forward data. **Expected benefit →** a useful market discovery and investment-decision platform with measured scope. **Risks/limitations →** historical coverage may keep a model in shadow despite useful software.

Build eligibility/coverage snapshots, one feature registry, exact return labels, the consolidated APEX evaluator, simple benchmark portfolios, one linear model, and one optional shallow tree challenger. Ship Best Opportunities, Rankings, Forecasts, Thesis Changes, and Portfolio Intelligence on the shared API. Add the Astra evidence interface and numeric/action verification after the deterministic outputs exist.

**P1 acceptance:** complete daily snapshots over the declared covered universe; frozen train/calibration/test artifacts; next-open policy replay; all benchmark and ablation results retained; probability and interval metrics available by horizon; stock rank and portfolio action can be traced independently. Promote only models that meet their predeclared criteria; lack of edge is an acceptable research conclusion.

### P2 — Add differentiated information and controlled adaptation

**Current state →** event/revision heuristics and a growing forward archive. **Problem →** costly enrichment and adaptation remain untested. **Proposed design →** admit enriched signals by measured incremental value. **Algorithm/model →** revision normalization, event novelty/response models, Form 4 parsing, lagged ownership context, rank stability, and slowly adaptive expert weights. **Required data →** sufficient timestamped event/estimate histories and audited labels. **Expected benefit →** differentiation beyond standard price/fundamental screens. **Risks/limitations →** selection effects, sparse events, extraction errors, and insufficient independent regimes.

Prioritize event/change evidence, model degradation controls, shadow alternatives, and information-value scheduling. Compare regime interactions with static models before adding an HMM. Test entry/exit policies separately. Add cost and data perturbation stress tests and an optional data-budget decision based on observed gaps.

**P2 acceptance:** each enriched family improves predefined predictive, decision, or research-efficiency metrics without unacceptable cost/fragility; factor weighting changes are attributable; model retirement and rollback are exercised; no enriched feature claims a historical record it does not have.

### Future — Conditional expansion

Consider licensed survivorship-complete histories and consensus vintages, international markets with currency/accounting models, tax-aware lot decisions, deeper event specialization, options-implied scenarios, or human-approved execution integration. Each requires a separate data and validation case. Neither multi-asset support nor trading automation is necessary to deliver the core QUANTA Alpha intelligence product.

## 10. Exact repository-level migration map

New paths below are proposed; they do not exist in the reviewed repository. “Replace” means migrate callers and tests, then retire the old implementation—not delete it before parity and deliberate behavior changes are understood.

### 10.1 Existing files to retain, refactor, or retire

| Existing repository path | Action | Exact responsibility after migration | Priority |
|---|---|---|---|
| `core/SP_ELITE_PRO.py` | Refactor, then legacy adapter | Extract acquisition, feature calculations, universe selection, ranking, and sizing; replace duplicated decision authority with domain services | P0/P1 |
| `core/TradingAnalysis_ELITE.py` | Refactor | Extract pure technical/accounting/risk features; replace MC forecasting, signal tracking, heuristic probability, and valuation blending; make presentation consume a Decision object | P0/P1 |
| `core/SP_ShortTerm.py` | Isolate | Preserve as `legacy_shortterm` strategy with its own horizon/execution/evidence scope; reuse verified indicators | P1 |
| `core/Alpha_Picks_Engine.py` | Retire independent scorer after baseline capture | Preserve a legacy strategy configuration for comparison; remove autonomous target/probability authority | P1 |
| `core/engine/types.py` | Replace with typed domain contracts | One Forecast, Decision, EvidenceState, DataStatus, PortfolioSnapshot, and reason-code vocabulary | P0 |
| `core/engine/decision_engine.py` | Refactor | Policy resolver over forecast, ownership, evidence state, and portfolio delta, rather than composite thresholds alone | P0/P1 |
| `core/engine/risk_overrides.py` | Refactor | Separate hard safety constraints from empirical signal effects; preserve auditable reasons | P0 |
| `core/engine/allocation.py`, allocation helpers in `SP_ELITE_PRO.py` | Consolidate | Atomic position updates followed by complete final-book invariant checks | P0 |
| `core/engine/data_validator.py`, `modules/data_validator.py`, `modules/data_quality_validator.py`, `core/quant/data_quality.py` | Consolidate | One semantic quality contract with source-specific validators; keep verified checks, remove duplicated scoring meanings | P0/P1 |
| `core/engine/dividend.py`, `core/engine/data_status.py`, `core/engine/consistency_validator.py` | Retain and adapt | Verified normalization, status distinctions, and serialized-output consistency checks | P0 |
| `core/apex_quant/simulator.py`, `costs.py`, `labeling.py`, `indicators.py` | Retain corrected core and extend | Canonical chronology, costs, horizon/path labels, and shared causal indicators | P1 |
| `core/apex_quant/data_provider.py`, `universe.py` | Replace internals behind interfaces | Versioned as-of queries over persisted data and historical eligibility; eliminate static-universe assumptions | P0/P1 |
| `core/apex_quant/elite_scorer.py`, `elite_strategy.py`, `strategy.py` | Preserve as explicit baselines | Clearly named partial/legacy strategies; new models use the same evaluation kernel | P1 |
| `core/apex_quant/walkforward.py`, `elite_oos.py`, `metrics.py`, `baselines.py` | Refactor/extend | Date-grouped purging, model/calibrator fitting, trial registry, dependence-aware intervals, matched baselines, coherent OOS portfolio state | P1 |
| `core/apex_quant/validation.py`, `run.py` | Extend and wire to product | Evidence state and identity gate for the specific model/portfolio policy being invoked; one unambiguous artifact lookup | P0/P1 |
| `core/apex_quant/_reports/*`, `docs/RECALIBRATION_PLAN.md` | Quarantine/relabel | Historical invalidated outputs retained for audit; no old metrics promoted as current strategy evidence | P0 |
| `modules/backtest_engine.py`, `modules/robust_backtesting_engine.py` | Retire as authoritative evaluators | Migrate needed analytics to canonical evaluation; retain explicit legacy diagnostic adapters only if required | P1 |
| `core/sp_shortterm_backtest.py` | Adapt | Short-term policy replay on canonical simulator and label contracts | P1 |
| `core/quant/factor_model.py` | Replace/rename | Real factor attribution or honestly named ETF exposure regression; HAC/robust inference and versioned inputs | P1 |
| `core/quant/risk_model.py` | Replace before wiring | Tested covariance estimation and constraint-preserving optimization; no automatic cap relaxation | P0/P1 |
| `modules/portfolio_risk_manager.py`, `PortfolioExposureManager` and `PositionSizer` in `TradingAnalysis_ELITE.py` | Consolidate | Portfolio state, covariance/stress risk, mandate constraints, and feasible target weights | P0/P1 |
| `modules/price_target_calculator.py` | Replace | Separate valuation scenarios and probabilistic return forecasts; remove heuristic target-hit probabilities | P0/P1 |
| `modules/entry_optimizer.py` | Refactor after baseline | Explicit, separately tested next-open/limit/staged-entry policies with expiry and fill assumptions | P2 |
| `modules/earnings_revision_scoring.py` | Refactor | Timestamped fiscal-period-aligned raw revision features, coverage normalization, missingness, and unit semantics | P0/P2 |
| `modules/sec_edgar_parser.py` | Retain transport controls; extend ingestion | Filing metadata and original-source ingestion into persistent accession-linked evidence; add accounting pipeline separately | P0/P1 |
| `modules/catalyst_intelligence_engine.py`, `catalyst_scanner.py`, `catalyst_validator.py`, `realtime_catalyst_integration.py`, `realtime_news_monitor.py` | Consolidate | One event store, extraction/novelty pipeline, and tested event features; remove unsupported historical-pattern return dictionaries | P2 |
| `modules/api_manager.py`, `polygon_provider.py`, `polymarket_provider.py` | Adapter boundary | Shared typed provenance/status/rate-limit interface; Polymarket remains optional context with a separate evidence case | P1/P2 |
| `modules/TechnicalSignalGenerator.py`, `core/enhanced_technical_indicators.py`, `core/enhanced_analyzer.py` | Deduplicate/legacy adapters | Use canonical feature definitions and decision outputs; prevent fallback routes from restoring fake precision | P1 |
| `webapp/app.py` | Thin transport adapter | Routes, authentication if needed, serialization, durable job dispatch, and snapshot serving; remove provider monkey patches and domain scoring | P0/P1 |
| `webapp/static/app.js`, `elite-pro.js`, `templates/index.html` | Incremental workflow refactor | Six research views over the same APIs; frontend cannot recalculate action or confidence | P1 |
| `core/streamlit_app.py`, `streamlit_support.py`, `rich_display.py`, `rich_market.py` | Thin presentation adapters | Reuse shared API/domain contracts; no alternative investment policy | P1 |
| `mcp_server/quanta_mcp_server.py`, `quanta_mcp_client.py`, embedded MCP in `TradingAnalysis_ELITE.py` | Consolidate | One read-only tool contract over domain services; Astra uses immutable evidence IDs | P1 |
| `pyproject.toml`, `.github/workflows/ci.yml` | Refactor | Importable package, locked reproducible environment, existing tests plus semantic/accounting/ledger checks | P0 |

### 10.2 Proposed package and service structure

```text
src/quanta/
  domain/
    security.py             # issuer/security identity and histories
    observation.py          # provenance, units, effective/available/received time
    forecast.py             # distributions, horizons, evidence status
    decision.py             # action, target weights, reasons, immutable IDs
    portfolio.py            # holdings, cash, mandate, snapshots
  data/
    adapters/{base,yahoo,sec,nasdaq,fred,news}.py
    security_master.py
    corporate_actions.py
    raw_store.py
    asof_repository.py
    financials.py           # accession/context/fiscal-period normalization
    quality.py
  universe/{eligibility,coverage,enrichment}.py
  features/{registry,prices,fundamentals,revisions,events,ownership,macro}.py
  models/
    baselines.py
    linear_alpha.py
    quantile_alpha.py
    distributions.py
    ensemble.py
    regime.py
    calibration.py
    registry.py
  risk/{covariance,factor_exposures,stress,constraints}.py
  portfolio/{ledger,optimizer,rounding,final_checks}.py
  decisions/{policy,entry,exits,valuation_scenarios}.py
  thesis/{evidence,diff,attribution}.py
  evaluation/
    labels.py
    splits.py
    simulator.py
    costs.py
    benchmarks.py
    metrics.py
    uncertainty.py
    experiments.py
    promotion.py
  monitoring/{data_health,model_health,outcome_resolver}.py
  orchestration/{jobs,scheduler,worker,publish}.py
  astra/{tools,evidence_packet,narrative,claim_validator}.py
  adapters/{flask_api,cli,mcp,legacy_elite,legacy_shortterm}.py

config/{universe,features,mandates,models,data_policies}.yaml
migrations/                  # transactional DB schema changes
tests/{unit,integration,causality,accounting,forecasting,policy}/
webapp/static/views/         # opportunity/ranking/forecast/change/portfolio/performance
deploy/compose.yaml         # API, worker, scheduler, PostgreSQL; local volumes
```

These are logical boundaries; do not turn each file into a network service. Start with the contracts and the smallest vertical slice. Defer unused modules until their feature is implemented. Raw data and generated run artifacts belong in configured storage, not source control; retain only deliberate test fixtures and manifests in Git.

### 10.3 API contracts

| Endpoint | Contract |
|---|---|
| `GET /api/v1/opportunities?as_of=&horizon=&universe=` | Completed snapshot, coverage, model evidence state, common-mandate ranks; never launches thousands of provider requests implicitly |
| `GET /api/v1/securities/{id}/forecasts` | Immutable forecast vintages, distributions, horizons, reference price rules, and outcome maturity |
| `GET /api/v1/securities/{id}/thesis-changes` | Reconciled input/model/universe changes and supporting source references |
| `POST /api/v1/portfolios/{id}/proposals` | Hypothetical target book from an explicit portfolio/mandate snapshot; idempotency key and full constraints |
| `GET /api/v1/models/{version}/performance` | Scope, tests, benchmarks, calibration, pending/matured counts, trial lineage, and current status |
| `POST /api/v1/research-runs` | Explicit asynchronous research/enrichment request with resource budget |
| `GET /api/v1/jobs/{id}` | Durable state and progress; retries/cancellation do not create duplicate published results |
| `GET /api/v1/evidence/{id}` | Frozen source/evidence packet used by humans and Astra |

### 10.4 Required new tests

Add `test_outcome_session_maturity.py`, `test_pending_outcomes_excluded.py`, `test_no_reuse_of_horizon_price.py`, `test_forecast_immutability.py`, `test_asof_filing_revisions.py`, `test_security_symbol_changes.py`, `test_corporate_action_accounting.py`, `test_label_interval_purge.py`, `test_training_transform_boundaries.py`, `test_quantile_ordering.py`, `test_distribution_probability_consistency.py`, `test_final_portfolio_invariants.py`, `test_missing_asset_risk.py`, `test_job_lease_fencing.py`, `test_model_scope_authorization.py`, and `test_astra_numeric_grounding.py`.

Keep statistical validation in reproducible research runs with stored outputs, not fragile unit tests asserting that one sample must achieve Sharpe > X. Unit tests establish contracts and arithmetic. Research evaluates predictive and economic hypotheses.

### 10.5 The first complete vertical slice

Build **one daily covered-universe snapshot → one price/fundamental baseline → one 3M forecast record → one portfolio proposal → one exact thesis-change explanation → one matured-outcome evaluation**. Generate the other requested horizons through the same contracts, but do not delay the accountability loop until every signal family exists.

This slice exposes the platform's hard problems: identity, timing, coverage, uncertainty, accounting, and action consistency. Solving them produces a credible foundation even if the first model fails to beat a simple benchmark. Adding more indicators before solving them produces a more elaborate assertion engine.

## Sources and reproducibility notes

Repository citations refer to the pinned commit above and may require access to the private repository. External sources are primary documentation or original research checked for this audit. Model and product designs are recommendations, not performance claims from those sources.

The companion evidence file records the targeted test command, CI URL, and synthetic probe outputs. Historical performance artifacts were deliberately not used as valid current performance evidence. No live-data or backtest result was invented to fill the gap.

[^1]: QUANTA repository. [README: scope, limitations, and engine inventory (from line 17)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/README.md#L17)

[^2]: GitHub Actions. [Successful CI run for reviewed commit](https://github.com/AtreshSoma/quanta-stock-analysis/actions/runs/34356189592). Local targeted tests are documented in the companion evidence file.

[^3]: QUANTA repository. [Scoring, normalization, and elite-factor blend (from line 5787)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L5787); [Regime weight tables (from line 6177)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L6177); [Heuristic confidence labeling (from line 3104)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L3104)

[^4]: QUANTA repository. [Short-term component scoring and gates (from line 2659)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ShortTerm.py#L2659)

[^5]: QUANTA repository. [Alpha Picks scoring, revision fallback, and price targets (from line 594)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/Alpha_Picks_Engine.py#L594)

[^6]: QUANTA repository. [Sentiment processing (from line 3743)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L3743); [Component scoring (from line 5494)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L5494)

[^7]: QUANTA repository. [Shared action resolver (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/engine/decision_engine.py#L1); [Risk overrides (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/engine/risk_overrides.py#L1)

[^8]: QUANTA repository. [Partial point-in-time ELITE reconstruction (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/elite_scorer.py#L1)

[^9]: QUANTA repository. [Invalidated historical reports (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/_reports/INVALIDATED.md#L1)

[^10]: QUANTA repository. [Validation identity and gate (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/validation.py#L1); [Benchmark passing criterion (from line 62)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/walkforward.py#L62); [Artifact writing and status lookup (from line 179)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/run.py#L179)

[^11]: QUANTA repository. [SignalTracker storage, outcomes, and statistics (from line 7131)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L7131)

[^12]: QUANTA repository. [Historical mean stored as expected return (from line 3241)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L3241); [Monte Carlo simulation (from line 4842)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L4842)

[^13]: QUANTA repository. [PositionSizer and heuristic win probability (from line 6041)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L6041)

[^14]: QUANTA repository. [Valuation assumptions (from line 50)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/price_target_calculator.py#L50); [Target probabilities (from line 250)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/price_target_calculator.py#L250); [Target-calculator caller (from line 1802)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/enhanced_analyzer.py#L1802)

[^15]: QUANTA repository. [DCF assumptions, fallbacks, and anchoring (from line 4595)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L4595)

[^16]: QUANTA repository. [Revision fields and estimate-period fallback (from line 133)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/earnings_revision_scoring.py#L133); [Revision combination and missingness (from line 242)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/earnings_revision_scoring.py#L242)

[^17]: QUANTA repository. [ETF exposure regression (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/quant/factor_model.py#L1); [Package exports (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/quant/__init__.py#L1)

[^18]: QUANTA repository. [RobustOptimizer constraints and fallback (from line 109)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/quant/risk_model.py#L109)

[^19]: QUANTA repository. [Portfolio VaR and stop-risk checks (from line 11380)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L11380); [Subsequent allocation refinement and deep confirmation (from line 11666)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L11666)

[^20]: QUANTA repository. [Provider patching (from line 115)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/webapp/app.py#L115); [Process-local jobs (from line 416)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/webapp/app.py#L416); [Scan, selection, and deep-confirmation consumption (from line 4660)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/webapp/app.py#L4660); [Local hosting guard (from line 5816)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/webapp/app.py#L5816)

[^21]: QUANTA repository. [Packaging and dependency configuration (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/pyproject.toml#L1)

[^22]: QUANTA repository. [Primary views (from line 51)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/webapp/templates/index.html#L51); [Security monitor (from line 269)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/webapp/templates/index.html#L269)

[^23]: QUANTA repository. [FDA placeholder (from line 78)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/catalyst_intelligence_engine.py#L78); [Entity heuristics (from line 157)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/catalyst_intelligence_engine.py#L157); [Historical patterns and signal rules (from line 405)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/catalyst_intelligence_engine.py#L405); [Catalyst score adjustment (from line 9206)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L9206)

[^24]: QUANTA repository. [Simplified historical replay (from line 94)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/backtest_engine.py#L94); [Diagnostic-output suppression (from line 10512)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L10512)

[^25]: QUANTA repository. [Current-bar signal and close execution (from line 425)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/robust_backtesting_engine.py#L425); [Walk-forward windows (from line 828)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/robust_backtesting_engine.py#L828); [Combined train-plus-test metrics (from line 1035)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/robust_backtesting_engine.py#L1035)

[^26]: QUANTA repository. [Historical data interface and unimplemented PIT fundamentals (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/data_provider.py#L1); [Static-universe caveat (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/universe.py#L1)

[^27]: QUANTA repository. [SEC transport, metadata, and rate limits (from line 75)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/modules/sec_edgar_parser.py#L75)

[^28]: QUANTA repository. [ThesisGenerator (from line 7027)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/TradingAnalysis_ELITE.py#L7027); [Downgrade checking (from line 6993)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/SP_ELITE_PRO.py#L6993)

[^29]: QUANTA repository. [Existing recalibration plan (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/docs/RECALIBRATION_PLAN.md#L1); [Proposed fitting and attribution approach (from line 617)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/docs/RECALIBRATION_PLAN.md#L617)

[^30]: U.S. Securities and Exchange Commission. [EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), last reviewed April 8, 2025.

[^31]: Nasdaq Trader. [Symbol Directory Definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs).

[^32]: Federal Reserve Bank of St. Louis. [FRED API Real-Time Periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html).

[^33]: Kenneth R. French. [Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html), including current research-factor dataset-format notes.

[^34]: Ran Aroussi / yfinance project. [Official documentation and data-use notice](https://ranaroussi.github.io/yfinance/).

[^35]: Shihao Gu, Bryan Kelly, and Dacheng Xiu. [Empirical Asset Pricing via Machine Learning](https://dachxiu.chicagobooth.edu/download/ML_BKP.pdf), September 13, 2019 version, published in Review of Financial Studies (2020).

[^36]: Olivier Ledoit and Michael Wolf. [Honey, I Shrunk the Sample Covariance Matrix](https://ledoit.net/honey.pdf), Journal of Portfolio Management 30(4), 2004. Author-hosted paper; recommendation is shrinkage as an estimation tool, not evidence of QUANTA returns.

[^37]: Isaac Gibbs and Emmanuel Candès. [Adaptive Conformal Inference Under Distribution Shift](https://arxiv.org/abs/2106.00170), 2021.

[^38]: scikit-learn developers. [Probability Calibration](https://scikit-learn.org/stable/modules/calibration.html), official documentation.

[^39]: U.S. Securities and Exchange Commission. [Frequently Asked Questions About Form 13F](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f).

[^40]: SciPy developers. [Student-t distribution density](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html). The infinite exponential-moment conclusion in this report is an analytical deduction from the documented polynomial tail.

[^41]: QUANTA repository. [Corrected execution simulator (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/simulator.py#L1); [Execution-semantics regression tests (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/tests/test_execution_semantics.py#L1); [Cost model (from line 1)](https://github.com/AtreshSoma/quanta-stock-analysis/blob/7bb6386d176ca98764641031bab327ad0ab1d647/core/apex_quant/costs.py#L1)

[^42]: David H. Bailey and Marcos López de Prado. [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), 2014.
