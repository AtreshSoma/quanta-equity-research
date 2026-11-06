# 08 — Implementation roadmap (P0-P3)

**Status (2026-11-06):** P0 and most of P1 are implemented on branch `audit/roadmap-p0-p1`; see
[11-IMPLEMENTATION-STATUS.md](11-IMPLEMENTATION-STATUS.md) for what was done, what changed from
this plan, and what is pending.

Effort: S ≤ 2 days · M 3-7 days · L 2-4 weeks (one developer, part-time realism not applied).
Every task keeps the golden master and both unit suites green; any task touching
`core/engine/*.py` or an engine file starts a new fingerprint period, which is stated in its
commit message.

Note on ordering: P0 tasks change the fingerprint. Doing them **together, once**, costs one
fingerprint period instead of several.

## P0 — Foundation and correctness

| ID | Title | Files | Implementation | Ref | Effort | Value / measure | Acceptance tests | Rollback |
|---|---|---|---|---|---|---|---|---|
| P0-1 | Complete fingerprint coverage | `core/tracking/fingerprint.py`, `tests/test_signal_ledger.py` (new cases) | Add `elite_v4_shadow` to `ENGINE_SOURCES`; derive shadow keys from one list so a new version cannot be missed; add each engine's imported `modules/*.py` files | Orig (02 F-1, F-2) | S | Closes a reproducibility hole in the pre-registered v4 test | v4 source list includes `TradingAnalysis_ELITE.py` and `modules/earnings_revision_scoring.py`; a test fails if any engine key seen by `shadow_engine()` lacks a source list | Revert commit; ledger rows keep their recorded fingerprints |
| P0-2 | Record optional-module provenance | `core/TradingAnalysis_ELITE.py` (`_import_optional`), `core/SP_ELITE_PRO.py`, `core/engine/decision.py` (`sources`) | Each Decision lists optional modules loaded (name + file hash) | Orig (W-8) | S | A call made without a module is distinguishable | Test: simulate a missing module; Decision provenance shows it and the model fingerprint differs | Revert |
| P0-3 | Model fingerprint separate from full-source fingerprint | `core/tracking/fingerprint.py`, `core/tracking/ledger.py` (new column, schema bump), `core/tracking/prospective_ic.py` | `model_fingerprint` hashes the scoring/decision modules + `modules/` deps + registry; `code_fingerprint` unchanged. Prospective IC groups by `model_fingerprint`. Start by hashing named functions' modules; move report strings out of hashed modules over time (D3) | Orig (02 F-3) | M | Evidence accumulates across wording edits. Measure: fingerprints per 10 publication dates falls from 19 (23 over 12) to ≤ 2 | Editing a report string leaves `model_fingerprint` unchanged; editing a weight changes it; `verify` passes on old and new rows | Keep column; switch grouping back to `code_fingerprint` |
| P0-4 | Record every call input | `core/tracking/ledger.py` (`context`), `core/engine/decision.py` | Store valuation target + method, analyst mean/count/dispersion, revision read, sentiment, insider net, each with source id and retrieval time | QL PIT idea; OPEN_ITEMS #6 | M | Makes the R/R-gate question and later replay testable | New rows carry fields; ledger CSV export includes a hash of them; size growth < 2 KB/row | Fields are additive; ignore on rollback |
| P0-5 | Patch dependency advisories | `webapp/constraints.txt`, `webapp/requirements.txt`, `pyproject.toml` | `requests>=2.33.0`, `curl_cffi>=0.15.0` | pip-audit | S | 0 known advisories | `pip-audit` clean; both suites green | Re-pin previous versions |
| P0-6 | Fix doc drift | `README.md`, `pyproject.toml` comment | Replace hard-coded counts (746 JS tests, 107 F401) with current values or a CI-produced badge | Orig | S | Docs match measurements | A test compares README's JS count to `package.json` file list, or the count is removed | Revert |

**Quick wins:** P0-1, P0-5, P0-6 (each under a day).
**Critical dependency:** P0-3 and P0-4 before any new shadow model (P1-1), so its record starts clean.

## P1 — High-impact product upgrades

| ID | Title | Files | Implementation | Ref | Effort | Value / measure | Acceptance tests | Rollback |
|---|---|---|---|---|---|---|---|---|
| P1-1 | Factor-baseline shadow | new `core/engine/factor_baseline.py`; `core/tracking/ledger.py` (`record_shadow_calls`); `core/TradingAnalysis_ELITE.py` adapter | 12-1 momentum + quality leg (same quality code as Research), ranked, recorded under `factor_baseline_shadow` | QL baselines; `apex_quant/factor_baselines.py` | S | Prospective comparison with the free factor the composite has not beaten | Rows recorded per Research call; `prospective_ic --compare` reports both | Stop recording; rows remain |
| P1-2 | Provider gateway, phase 1 | new `core/data/{envelope,gateway,adapters/*}.py`; move throttle/single-flight from `webapp/app.py:315-357` | Wrap existing calls; PriceBars + Quote + Profile first; ordered fallback yfinance → Stooq | OBB `Fetcher`, `OBBject` | L | One provenance path; measure: direct `yf.` call sites outside adapters 86 → ≤ 40 after phase 1 | Golden unchanged; `test_yf_single_flight.py`, `test_provider_resilience.py` pass against gateway; a forced yfinance failure serves Stooq with a warning in the envelope | Engines keep old call path behind a flag until phase 2 |
| P1-3 | Ledoit-Wolf covariance | `core/portfolio/analytics.py` | Shrinkage to constant-correlation target; used by vol, risk shares, correlated pairs | QL `model/riskmodel/shrink.py` (MIT) | S | Stabler risk numbers; measure: week-to-week change in risk shares on an unchanged book | PSD; shares sum to 1; converges to sample cov as n grows (synthetic test) | Flag to use sample cov |
| P1-4 | Fama-French attribution | new `core/portfolio/factor_attribution.py`, `core/data/adapters/french.py`, `webapp/portfolio_routes.py`, `webapp/static/mp-insights.js` | Daily FF5 + momentum from the Ken French library; OLS with HAC CIs (reuse `apex_quant/stats.py` NW), 1y and 3y windows | OBB `extensions/quantitative/factors.py` | M | Owner sees real exposures (size, value, momentum) | Synthetic series with known betas recovered within CI; hidden below 126 shared days | Hide card |
| P1-5 | Grounded research brief | new `core/ai/brief.py`, optional extra `ai` in `pyproject.toml`, `webapp/app.py` route, `webapp/static/app.js` panel, `mcp_server` tool | One structured call over the Decision; every sentence cites fields/source ids; `check_narrative` + cite resolution must pass; cache by (ticker, as_of, model_fingerprint); off by default | TA analysts (pattern), QUANTA `interpretation.py` | M | Readable summary that cannot invent numbers; measure: % of generated briefs passing the gate, latency, cost per brief | Seeded fabricated number rejected; label repeated verbatim; panel absent when gate fails or key missing; no network in unit tests (stubbed client) | Feature flag off |
| P1-6 | Evidence clock in UI | `webapp/app.py` (`/api/track-record`), `webapp/static/app.js` | Show resolved calls and dates for the current model fingerprint against the pre-registered minimum | Orig | S | Owner sees how far each model is from a verdict | Numbers equal `prospective_ic` output on fixture ledger | Hide |
| P1-7 | Consolidate indicators | `core/SP_ELITE_PRO.py:4642`, `core/apex_quant/indicators.py`, `core/engine/plan_simulator.py:86` → `core/engine/indicators.py` | Delete duplicates; import one implementation | Orig | M | Fewer places to fix; parity by construction | `test_indicator_parity.py` passes; golden unchanged | Revert |
| P1-8 | Clear unused imports, gate F401 | repo-wide | `ruff --fix --select F401` reviewed by hand for re-exports; add F401 to the gate | Orig | S | Lint signal restored | `ruff check .` with F401 clean | Revert |

## P2 — Advanced AI and quantitative capabilities

| ID | Title | Files | Implementation | Ref | Effort | Value / measure | Acceptance tests | Rollback |
|---|---|---|---|---|---|---|---|---|
| P2-1 | Grounded bull/bear | `core/ai/brief.py` | Extend P1-5 schema with `bull[]`, `bear[]`; no rating slot | TA researchers | S | Two-sided reading of the same Decision | Buy/sell words that contradict the label are flagged; label unchanged | Drop fields |
| P2-2 | EV calibration | new `core/tracking/calibration.py`, Evidence page | Reliability buckets and Brier score for v4's P(target first) vs realised first touch, Wilson CIs | Orig | M | Tests the v4 gate's central assumption | Refuses below pre-registered n; on synthetic calibrated data the slope ≈ 1 | Hide |
| P2-3 | PIT universe | `core/apex_quant/universe.py`, new `core/apex_quant/membership.py` | Historical S&P 500 membership with add/remove dates; delisted price history from free sources; coverage reported | QL `scripts/data_collector/us_index` | L | Removes survivorship bias; more names → more IC power | At date D, members exclude later additions and include later removals; harness prints coverage | Keep static universe as an option |
| P2-4 | Top-k dropout evaluation | `core/apex_quant/simulator.py` | Strategy mode: hold top k, replace ≤ n per rebalance, costs applied | QL `TopkDropoutStrategy` | M | Turnover-bounded test of any ranking | Toy example hand-computed; turnover bound holds | Mode unused |
| P2-5 | Live-path replay | new `core/apex_quant/replay.py` | Rebuild Decisions for past dates from PIT prices, PIT fundamentals and recorded inputs (P0-4); unrecorded inputs neutral and counted | Orig | L | First measure of what following calls would have returned | Replaying a recorded day reproduces its ledger actions for recorded inputs | Tool only |
| P2-6 | Constrained what-if weights | new `core/portfolio/construct.py`, Plan Lab | Min-variance or risk parity inside sector/position/exposure caps using P1-3 covariance | QL enhanced-indexing optimiser (idea) | M | Better-diversified suggestions, never orders | Every cap respected; deterministic; turnover shown | Hide |
| P2-7 | Split `app.py` and the engines | `webapp/app.py` → blueprints; `TradingAnalysis_ELITE.py` → `core/research/`; `SP_ELITE_PRO.py` → `core/picks/` | Mechanical extraction behind the golden master | Orig | L | No `core/` file > 3,000 lines; narrower model fingerprint | Golden unchanged; all suites green | Per-step reverts |
| P2-8 | FRED macro panel | `core/data/adapters/fred.py`, Overview | Rates, curve, CPI, unemployment, HY spread, dated | OBB/TA FRED | S | Context | Missing key shows "not configured" | Hide |
| P2-9 | One regime module | `core/engine/regime.py` | Replace three `MarketRegime` definitions | Orig | M | Consistency | Regime tests pass | Revert |

## P3 — Differentiation and scale

| ID | Title | Effort | Notes |
|---|---|---|---|
| P3-1 | Gradient-boosted ranker as a registered trial | L | Only on the P2-3 universe; ships only if it beats the composite and the factor baseline with deflated Sharpe and BH-adjusted p < 0.05 |
| P3-2 | Thesis ledger | M | User theses with measurable conditions, hash-chained like calls, monitored by alerts |
| P3-3 | Pre-registration UI | M | Registered versions, rules and progress on the Evidence page |
| P3-4 | Filing text deltas | M | Year-over-year risk-factor additions/removals from 10-K; context only |
| P3-5 | Historical-episode stress | S | 2008, 2020, 2022 windows on current weights |
| P3-6 | Front-end per-page bundles and minification | M | Measure first-visit transfer before and after |
| P3-7 | Package `core` as `quanta` | L | After P2-7 |
| P3-8 | Fund look-through | M | Only with a dated issuer-holdings source (OPEN_ITEMS #7) |
| P3-9 | Crypto research basics | M | Risk grade and trend for held crypto; no buy decisions without a tested model |

## Highest-return investments

1. **P0-3 model fingerprint** — without it, no prospective test ever completes, and every other
   model improvement stays unprovable.
2. **P0-4 input recording** — every day not recorded is a day of history that can never be backtested.
3. **P1-1 factor-baseline shadow** — cheapest possible answer to "is the composite worth its complexity?"
4. **P1-2 provider gateway** — unlocks failover, provenance and every later data source.
5. **P1-5 grounded brief** — the visible AI upgrade, built on guard rails QUANTA already has.

## Dependencies

```
P0-1 ─┬─► P0-3 ─► P1-1 ─► P3-1
P0-2 ─┘     │
P0-4 ───────┴─► P2-2 ─► P2-5
P1-2 ─► P1-4, P2-8, P2-3
P1-3 ─► P2-6
P1-5 ─► P2-1
P1-7, P2-9 ─► P2-7 ─► P3-7
```
