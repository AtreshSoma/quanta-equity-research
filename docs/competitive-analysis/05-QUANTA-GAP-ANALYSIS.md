# 05 — QUANTA gap analysis

QUANTA was audited on its own first (02). This file lists what is strong, what is broken, what is
weak and what is missing, with the evidence for each.

## 1. Section-by-section state

| Section | State | Strongest point | Main gap |
|---|---|---|---|
| Overview | Implemented | Market mood, session quote board, live chart with in-page indicators, exposure band that caps Picks deployment | Cold start 9-11 s by deliberate throttle (README; not re-measured) |
| Discover | Implemented | Funnel with earnings and sector chips; every row labelled by the Decision | Default sort is consensus upside, which favours stale targets (OPEN_ITEMS #3) |
| Research | Implemented | One Decision with blockers, levels, scenarios, valuation v4 shadow, earnings card, filings | Reward/risk gate structurally strict for large caps (OPEN_ITEMS #6); no LLM brief |
| Picks | Implemented | Capital-aware sizing, sector/correlation caps, deep confirmation, reason for every unfunded name | Inherits an unvalidated ranking; no optimiser beyond caps |
| My Portfolio | Implemented | Read-only Robinhood, exposure, VaR/CVaR, risk shares, what-if, Activity ledger, Review | Charts replay today's holdings (no actual account return); no factor attribution; no fund look-through |
| Activity | Implemented | Fills, realised P&L, lot dates, tested on nine fixture shapes | Depends on Robinhood order detail; partial coverage is reported, not filled in |
| Evidence | Implemented | Per-model validation status, tamper-evident ledger | Evidence clock resets on almost every engine edit (F-3) |
| Crypto | Partial | Crypto orders read from Robinhood (`crypto_orders`, with per-fill detail) for Activity; crypto classed as its own asset class in exposure (`portfolio/exposure.py:25`) | No crypto positions tool in `READ_TOOLS`; no crypto research, scoring or decisions |
| India | Implemented | NSE/BSE calendars, statutory levies in costs, Nifty benchmark | No PIT fundamentals; 2027 NSE holidays pending (Dec 2026 circular) |
| MCP / skills | Implemented | 20+ tools, write tools refused, narrative checker | Only reachable from an external LLM client |

## 2. What QUANTA already does better than all three competitors

1. **One decision object everywhere** with coded blockers and the condition that clears each.
2. **Evidence status on every call** and a refusal to emit sizes when a strategy is not validated.
3. **Statistical inference for overlapping, fat-tailed returns** (HAC, block bootstrap, deflated
   Sharpe, FDR, trial registry, random-entry and random-exit baselines).
4. **Pre-registered model versions scored in shadow** on the prospective ledger.
5. **Tamper-evident track record** (per-call hash, hash chain, git timestamps, `verify`).
6. **Grounding checker for LLM text** (`check_narrative`).
7. **Live, read-only brokerage integration** with write-tool refusal and reconciliation.
8. **Risk-first sizing** tied to the user's capital and the market's exposure ceiling.

Do not rebuild any of these.

## 3. Broken or incorrect (fix first)

| ID | Problem | Evidence | Impact |
|---|---|---|---|
| B-1 | v4 shadow fingerprint omits `TradingAnalysis_ELITE.py` | `fingerprint.py:28` has no `elite_v4_shadow` key; measured 43 vs 44 files | A v4 period can span changed component code; the v4 pre-registered test could pool two models |
| B-2 | `modules/` dependencies are outside every fingerprint | `_import_optional` loads 7 services; none hashed | Same, for v2/v3/v4 and Picks; a missing optional module changes output under the same fingerprint |
| B-3 | Fingerprint hashes presentation text with model code | 23 fingerprints over 12 publication dates | No model version accumulates the 30 calls on 10 dates the pre-registration needs |
| B-4 | Two pinned dependencies with advisories | `requests 2.32.5` (PYSEC-2026-2275), `curl-cffi 0.13.0` (PYSEC-2026-2431) | Security hygiene |
| B-5 | Documentation counts drifted | README: 746 JS tests (actual 882), 107 F401 (actual 123) | Trust in the docs |

No financial calculation error was found in the code paths checked (barrier EV, VaR/CVaR,
Euler risk shares, beta scenarios, IC sampling). See 09 for what was and was not checked.

## 4. Weak or incomplete

| ID | Weakness | Evidence | Direction |
|---|---|---|---|
| W-1 | No validated edge in any model | `docs/LIVE_MODEL_VALIDATION_STATUS.md` | Add a free-factor shadow; build the PIT universe; keep labels honest |
| W-2 | Validation universe: 42 present-day survivors | `apex_quant/universe.py` | PIT membership + delisted names |
| W-3 | Live decision path never replayed historically | README Known limitations | Replay harness over `Decision` construction |
| W-4 | EV model drift derived from the same target it tests | `expected_value.drift_from_valuation` | Calibration curve from the ledger; fit `shrink` only on pre-registered data |
| W-5 | Valuation and analyst targets not recorded point-in-time | OPEN_ITEMS #6 | Store target, its inputs and source in each ledger row now |
| W-6 | 83 direct yfinance call sites | grep | Provider gateway, migrated one engine at a time |
| W-7 | Monolithic engines, duplicated indicators/regimes | 15K-line files; RSI×3, ATR×3, regime×3 | Extract to `core/engine/indicators.py` and one regime module |
| W-8 | Silent optional imports | `_import_optional` | Record which optional modules loaded in each Decision's provenance |
| W-9 | Sample covariance for risk shares | `analytics.py:139` | Ledoit-Wolf shrinkage |
| W-10 | Discover ranks by consensus upside | OPEN_ITEMS #3 | Offer a composite "street + momentum + revisions" sort; keep upside as an option |
| W-11 | Research R/R gate (1.5 on a quarter of 12-month upside vs 2.75×ATR) | OPEN_ITEMS #6, measured 24% reach | v4's EV gate is the pre-registered alternative; decide after its test |
| W-12 | Front-end weight | `app.js` 820 KB, My Portfolio scripts on every page | Lazy-load per page |
| W-13 | Not a package | `pyproject.toml` | Package `core` as `quanta` after the engines are slimmer |

## 5. Missing entirely — the 20 most important

Ranked by value to the owner's stated job (a short, trustworthy, sized list and an honest view of
holdings).

| # | Missing capability | Closest competitor reference |
|---|---|---|
| 1 | Point-in-time recording of every input a call used (targets, estimates, ratings, sentiment) | Qlib PIT collector |
| 2 | Free-factor shadow model (12-1 momentum + quality) recorded beside the live call | Qlib baselines / QUANTA's own `baselines` |
| 3 | Typed provider gateway with provenance, freshness and failover | OpenBB `Fetcher` + standard models |
| 4 | Point-in-time universe with delisted names for validation | Qlib index collectors |
| 5 | Replay of the live Decision path over history | — (original) |
| 6 | Calibration of predicted probabilities (EV model) against realised outcomes | — (original) |
| 7 | Grounded LLM research brief (one call, cites Decision fields and source ids, machine-checked) | TradingAgents analysts, minus the free text |
| 8 | Two-sided bull/bear summary, grounded and checked | TradingAgents researchers |
| 9 | Factor attribution of the book and of Picks (Fama-French 5 + momentum) | OpenBB `factors()` + Fama-French provider |
| 10 | Shrinkage covariance for portfolio risk | Qlib `ShrinkCovEstimator` |
| 11 | Portfolio construction suggestions under constraints (min-variance or risk-parity within caps) | Qlib `EnhancedIndexingStrategy` optimiser |
| 12 | Actual account performance and attribution (time-weighted return from transfers and fills) | — |
| 13 | Thesis tracker: the user's own thesis with measurable invalidation conditions, monitored by alerts | — (original; uses `Decision.invalidation`) |
| 14 | Macro dashboard from FRED (rates, curve, inflation, unemployment) | OpenBB FRED / TradingAgents FRED |
| 15 | Filing text signals (risk-factor changes, MD&A tone) | OpenBB SEC `risk_factors`, `management_discussion_analysis` |
| 16 | Earnings-call transcript summaries | OpenBB standard model `earnings_call_transcript` |
| 17 | Turnover-bounded rebalancing evaluation (top-k dropout) for any ranking | Qlib `TopkDropoutStrategy` |
| 18 | Historical-episode stress tests (2008, 2020, 2022) on current weights | — |
| 19 | Fund look-through for ETF exposure | OPEN_ITEMS #7 |
| 20 | Crypto research (at least risk grade and trend) for crypto held in the account | — |

Deliberately **not** on the list: options analytics, short setups, multi-user auth, hosted
deployment. They are outside PRODUCT.md's scope (single owner, long-only, loopback).

## 6. Recommendation quality today

| Dimension | Assessment |
|---|---|
| Data inputs | Free Yahoo data, neutral imputation for missing fields, SEC events fail closed. Adequate for research; fundamentals often stale |
| Ranking logic | Weighted composite of 5-6 legs with regime tables; fundamental and quality legs carry the measured ranking power (IC +0.041/+0.061 and +0.037/+0.046), technical and valuation legs carry none |
| Explainability | Best of the four: coded blockers, reasons, risks, conflicts, invalidation, red flags |
| Risk awareness | Strong: ATR stops, risk grade, corporate events, FOMC sizing, sector and correlation caps, exposure ceiling |
| Reproducibility | Strong in intent (fingerprints, hash chain); weakened by B-1, B-2, B-3 |
| Predictive validity | Not demonstrated, and stated as such on every call |

The practical reading: QUANTA's recommendations are well-governed candidates. The highest-value
work is not more features on top of the ranking; it is making the ranking testable and tested.
