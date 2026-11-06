# 06 — Feature adoption matrix

Columns: **Src** source (TA = TradingAgents, OBB = OpenBB, QL = Qlib, Orig = original) ·
**Now** current QUANTA state (E existing, P partial, M missing) · **Cx** complexity (L/M/H) ·
**Pri** P0-P3. Costs are in engineering time and running cost; "free" means no paid API.

Mapping to the roadmap tasks in 08: A1→P0-1, A2→P0-3, A3→P0-5, A4→P0-4, A5→P1-1, A6→P0-6,
B1→P1-2, B2→P1-4, B3→P1-3, B4→P2-8, B5→P2-3, B6→P2-4, B7→P3-4, C1→P1-5, C2→P2-1, C3→P2-2,
C4→P2-5, C5→P2-6, C6→P3-1, D1→P1-7, D2→P2-9, D3/D4→P2-7, D5→P3-6, D6→P3-7, D7→P1-8, D8→P0-2,
E1→P1-6, F5→P3-2, F3→P3-3.

## A. Essential fixes and missing capabilities

| ID | Feature | Src | Now | Competitor implementation | Proposed QUANTA design | User benefit | Cx | Dependencies | Cost | Pri | Risk | Acceptance criteria |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | Complete fingerprint coverage | Orig | P | — | Add `elite_v4_shadow` (and any future shadow) to `ENGINE_SOURCES`; hash the `modules/` files each engine imports; record which optional modules loaded | Every call traceable to the code that made it | L | none | <1 day | P0 | Starts a new fingerprint period (expected) | Test: every engine key in the ledger has a source list that includes `TradingAnalysis_ELITE.py` and each imported `modules/` file; a byte change in `earnings_revision_scoring.py` changes the v4 fingerprint |
| A2 | Model-only fingerprint | Orig | P | — | Move report wording out of the hashed modules or hash only the scoring/decision functions' modules; keep the full-source hash as a second column | Evidence accumulates across UI copy edits | M | A1 | 2-4 days | P0 | Too narrow a hash could miss a real change; mitigate with a test that lists hashed modules | Editing a CLI report string leaves `model_fingerprint` unchanged; editing a weight changes it |
| A3 | Patch advisories | Orig | P | — | `requests>=2.33.0`, `curl_cffi>=0.15.0` in pins | Security | L | none | hours | P0 | Upstream behaviour change; run suites | `pip-audit` clean; both suites pass |
| A4 | Record every call input point-in-time | QL (PIT idea) | M | Qlib PIT store | Ledger `context` gets valuation target, its method, analyst mean/count, revisions read, sentiment, source ids and retrieval times | Makes OPEN_ITEMS #6 testable; enables later backtests of live inputs | M | none | 2-3 days | P0 | Ledger growth (small) | Each new ledger row carries the fields; `verify` still passes; schema version bumped |
| A5 | Free-factor shadow | QL / Orig | M | Qlib baselines | Score 12-1 momentum + quality on each Research call; record as `factor_baseline_shadow` | Prospective head-to-head with the factor the composite has not beaten | L | A1 | 1-2 days | P1 | None to live calls | Rows recorded under own engine; prospective IC report compares it with `elite` per fingerprint |
| A6 | Doc count drift | Orig | P | — | Generate counts in CI or remove them | Docs stay true | L | none | hours | P1 | — | README numbers match a CI-computed value or are removed |

## B. High-value competitor features

| ID | Feature | Src | Now | Competitor implementation | Proposed QUANTA design | User benefit | Cx | Dependencies | Cost | Pri | Risk | Acceptance criteria |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | Provider gateway | OBB | M | `Fetcher` 3-step, standard models, `OBBject` | `core/data/` with 7 dataclass models, adapters for yfinance, Stooq, SEC, Finnhub, FRED; ordered fallback per model; envelope carries provider, as_of, retrieved_at, warnings | One place for throttle, cache, provenance, failover | H | none | 2-3 weeks incremental | P1 | Behaviour drift during migration; golden master guards it | Engines migrated one at a time; golden suite unchanged; `grep yf.Ticker` count falls to adapters only |
| B2 | Fama-French factor attribution | OBB | M | `factors()` OLS + Fama-French provider | Download Ken French daily factors (free CSV), regress book and Picks cohort returns; show betas with CIs | Says what the book is really exposed to | M | B1 optional; statsmodels or numpy OLS | 3-5 days | P1 | Over-reading short windows; show CIs and n | Unit test against a synthetic series with known betas; UI shows coefficient, CI, R², window |
| B3 | Shrinkage covariance | QL | M | `ShrinkCovEstimator` | Ledoit-Wolf on the shared window; used for vol, risk shares, correlated pairs | Stabler risk numbers | L | numpy | 1-2 days | P1 | Values change on the page | Shrunk cov is PSD; risk shares sum to 1; test vs sample cov on long synthetic history |
| B4 | FRED macro panel | OBB / TA | M | FRED providers | Small adapter: 2y/10y, curve, CPI YoY, unemployment, HY spread, with dates | Context for regime read | L | FRED key (free) | 2 days | P2 | Key management | Values dated; missing key shows "not configured", never zeros |
| B5 | PIT universe for validation | QL | M | index collectors with start/end | Historical S&P 500 membership + delisted tickers' prices (Stooq/SEC for delisting dates) | Survivorship-free validation | H | data sourcing | 2-3 weeks | P2 | Free sources incomplete; document coverage | Universe at date D excludes names added after D and includes names later delisted; harness reports coverage |
| B6 | Turnover-bounded evaluation | QL | M | `TopkDropoutStrategy` | Add a top-k dropout simulator mode to `apex_quant` | Realistic test of a ranking with bounded trading | M | B5 helps | 3-5 days | P2 | — | Turnover per rebalance ≤ n/k; costs applied; matches hand-computed toy example |
| B7 | Filing text signals | OBB | P | SEC risk factors, MD&A models | Diff year-over-year risk-factor sections; flag new items | Early warning in Research | M | SEC client | 1 week | P3 | Noisy text; label as context, no score | Shows added/removed risk factors with filing dates |
| B8 | Earnings-call transcripts | OBB | M | `earnings_call_transcript` standard model | Only if a free, licensed source exists | Context | M | data source | — | P3 | Licensing | Not started until a source is confirmed |

## C. Advanced AI and quantitative intelligence

| ID | Feature | Src | Now | Competitor implementation | Proposed QUANTA design | User benefit | Cx | Dependencies | Cost | Pri | Risk | Acceptance criteria |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | Grounded research brief | TA (adapted) | P | Analyst LLM reports | One LLM call over `Decision.to_dict()` + `build_interpretation`; output schema: sentences with `cites` (field paths and source ids); `check_narrative` must pass or the brief is not shown | A readable paragraph that cannot invent numbers | M | Anthropic SDK (optional extra), API key | One LLM call per (ticker, as_of, fingerprint), cached; dollar cost depends on the model chosen and is to be measured in the P1 task, not estimated here | P1 | Hallucination (caught by checker), cost, key handling | 100% of shown briefs pass `check_narrative`; a seeded wrong number is rejected; off by default; cached per (ticker, as_of, fingerprint) |
| C2 | Grounded bull/bear summary | TA | M | Bull/Bear researchers | Same call returns `bull` and `bear` arrays, each sentence citing fields; label repeated verbatim; no rating field in the schema | Two-sided view without a second verdict | M | C1 | marginal on C1 | P2 | Model drifts into a recommendation; schema has no slot for one, checker flags buy/sell words | Neither side changes the label; contradiction words flagged; e2e test on fixture Decision |
| C3 | EV calibration | Orig | M | — | From ledger: bucket predicted P(target first) and compare with realised first-touch; reliability plot on Evidence | Tests the v4 gate's core assumption | M | A4, enough resolved v4 calls | 3-4 days | P2 | Small samples; show CIs | Brier score and reliability buckets with Wilson CIs; refuses to draw with < pre-registered n |
| C4 | Live-path replay | Orig | M | — | Rebuild `Decision` for past dates from PIT prices + PIT fundamentals + recorded inputs (A4) | Measures what following calls would have done | H | A4, B5 | 3-4 weeks | P2 | Inputs without history stay neutral; report coverage | Replay of a recorded day reproduces its ledger rows' actions exactly (for inputs that were recorded) |
| C5 | Constrained portfolio suggestion | QL | M | Enhanced-indexing optimiser | Min-variance or risk-parity weights inside existing caps (sector, position, exposure ceiling), shown as a what-if, never an order | Better-diversified book | M | B3 | 1 week | P2 | Optimisers amplify estimation error; keep shrinkage and caps | Weights respect every cap; turnover vs current book shown; deterministic for a fixed input |
| C6 | Gradient-boosted ranker | QL | M | LightGBM on Alpha158 | Only on B5 universe, trained with fit-window discipline, registered as a trial | Possibly better ranking | H | B5, A5 | 2-3 weeks | P3 | Overfitting, false discovery | Ships only if OOS IC beats both the composite and A5's factor baseline with deflated Sharpe and BH-adjusted p < 0.05 |
| C7 | Regime simplification | QL evidence / Orig | P | — | Pre-register fixed weights (v3 already does this) and retire regime tables if v3's test passes | Less complexity | L | v3 test outcome | — | P3 | — | Decided by the pre-registered test, not by judgement |

## D. Architecture, reliability, performance

| ID | Feature | Src | Now | Proposed design | Cx | Pri | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| D1 | One indicator module | Orig | P | `core/engine/indicators.py` becomes the only RSI/ATR/MACD/ADX; engines import it | M | P1 | Parity tests pass; duplicates deleted; golden unchanged |
| D2 | One regime module | Orig | P | Single `MarketRegime` + detector in `core/engine/` | M | P2 | Three classes replaced; regime tests pass |
| D3 | Split the engines | Orig | P | Extract analyzers from `TradingAnalysis_ELITE.py` and scoring from `SP_ELITE_PRO.py` into `core/research/` and `core/picks/` packages | H | P2 | No file over 3,000 lines in `core/`; golden unchanged; fingerprint lists updated |
| D4 | Split `app.py` | Orig | P | Flask blueprints per page (pattern already used for portfolio, plan, my-list, events) | M | P2 | `app.py` < 2,000 lines; route tests pass |
| D5 | Front-end lazy loading | Orig | P | Load My Portfolio scripts only on that page; minify in a build step | M | P3 | First-visit transfer reduced; 882 JS tests pass; e2e pass |
| D6 | Package the code | Orig | P | `quanta` package with entry points; remove `sys.path` inserts | H | P3 | `pip install .` works; CLI entry points run |
| D7 | Unused imports and unwired modules | Orig | P | Clear 123 F401; decide on 6 unwired modules and `core/quant/` | L | P1 | `ruff --select F401` clean and gated |
| D8 | Provenance of optional modules | Orig | M | Each Decision lists optional modules that loaded and their versions | L | P0 (with A1) | Missing module appears in provenance and in the fingerprint record |

## E. UI/UX

| ID | Feature | Pri | Design | Acceptance criteria |
|---|---|---|---|---|
| E1 | Evidence clock on Research and Picks | P1 | "This model version: N resolved calls on D dates; test needs 30 on 10" | Numbers come from the ledger API; matches `prospective_ic` |
| E2 | "Research brief" panel | P1 | Collapsed by default; each sentence's citations open the field it cites | Panel absent when checker fails or feature is off |
| E3 | Factor exposure card on My Portfolio | P1 | Betas with CIs and window | Hidden below minimum days |
| E4 | Discover sort presets | P2 | "Street upside", "Momentum", "Revisions", "Quality" | Default stays an owner decision (OPEN_ITEMS #3) |
| E5 | Calibration chart on Evidence | P2 | Reliability plot for EV probabilities | Shows "too few calls" until minimum |
| E6 | Thesis tracker UI | P3 | User writes thesis + conditions; alerts watch them | Conditions are numbers QUANTA measures |

## F. Original differentiators (not in any competitor)

| ID | Feature | Why it differentiates | Pri |
|---|---|---|---|
| F1 | **Verified debate**: bull/bear prose where every number is machine-checked against the Decision and every claim cites a field or filing id | TradingAgents debates; nobody verifies the debate | P2 (C2) |
| F2 | **Calibrated trade probabilities**: published P(target first) with a public reliability record | Turns the EV model into a falsifiable forecast | P2 (C3) |
| F3 | **Pre-registration as a product feature**: users see which model versions are registered, their test rule and progress | Unique; extends the existing v3/v4 docs into the UI | P2 |
| F4 | **Holder-aware decisions**: the same Decision tells a holder and a non-holder different things (`new_money` vs `holder_action`), already in the schema | Competitors give one rating to everyone | Existing, surface more |
| F5 | **Thesis ledger**: the user's own theses recorded with the same hash chain and resolved like QUANTA's calls | Personal track record against the model's | P3 |
| F6 | **"What would clear it" alerts**: the buy-zone alert at the price where a blocked name's reward/risk clears (`rr_hurdles.funding.clear_entry`, `buyZoneCaveat` in `webapp/static/app.js`) | Turns a Watchlist reason into an actionable trigger | Existing; keep and feature it |
