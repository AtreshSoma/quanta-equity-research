# QUANTA Stock Analysis — Production Readiness Audit

Audit date: 2026-07-10
Scope: live `stock-analysis` core engines, shared decision core, data providers,
Flask API, browser UI, MCP surface, backtesting modules, configuration, and tests.
Archived scripts and unrelated workspace projects are excluded.

**24 September 2026 update:** This is a dated audit. The working tree now uses a
prospective SQLite ledger for full deep-analysis calls, with exact trading-session
maturities and matched benchmark closes. The historical JSON tracker remains only
for legacy position context and is unsuitable for calibration. The full live
model still lacks point-in-time out-of-sample validation; `H-09` remains open.

## Executive summary

The platform now fails safely for stale/invalid market data, prevents several
material signal distortions, and has strong deterministic regression coverage.
It is suitable for research, paper trading, and controlled local use.

It is **not production-ready for public deployment or capital-allocation
automation**. The remaining blockers are: no authenticated production web
deployment, no field-level point-in-time data lineage across every provider,
and no out-of-sample validation of the full live recommendation engine
(fundamentals, analyst estimates, news, and revisions together). The existing
APEX walk-forward framework is sounder than the legacy backtester, but it tests
a separate price-based strategy rather than the complete live recommendation
stack.

## Architecture and data flow

1. Flask `webapp/app.py` accepts scan or deep-analysis requests and runs jobs in
   background threads.
2. `SP_ELITE_PRO.py` performs broad-universe screening and allocation.
3. `TradingAnalysis_ELITE.py` fetches adjusted OHLCV and metadata, then runs
   technical, fundamental, risk, sentiment, analyst, insider, catalyst,
   valuation, and Monte Carlo components.
4. `core/engine` validates instruments and resolves one authoritative final
   action after stop/target, risk-grade, R/R, and position-state gates.
5. The deep analyzer confirms scanner picks; unconfirmed or provider-failed
   picks receive no allocation.
6. The Flask serializer exposes facts, calculations, provenance, preliminary
   status, and final action to the JavaScript UI.
7. Signal history is stored in JSON; caches and job state are process-local.

## Issue register

| ID | Severity | Component | Root cause and impact | Correction / status | Coverage |
|---|---|---|---|---|---|
| C-01 | Critical | Secrets | Live source files contained provider credentials. Anyone with repository access could use them. | Removed from all live entry points; environment variables only. **Fixed. Rotate the exposed keys externally.** | Secret-pattern scan |
| H-01 | High | Risk analytics | A provider/default regression could feed unadjusted split returns into volatility, beta, VaR, drawdown, risk grade, Monte Carlo, and sizing. | Explicit adjusted history plus a conservative ≥50% corporate-action return gate. **Fixed.** | Synthetic 10:1 split regression |
| H-02 | High | Sentiment/scoring | Macro and peer headlines were scored as company evidence; display relevance and scoring relevance differed. | Persisted direct/peer/macro provenance before scoring; company score is gated/dampened; unexplained ≥4% moves are flagged. **Fixed.** | Provenance and move tests |
| H-03 | High | Freshness | A long but stale price history could still produce a current recommendation. | Latest daily bar older than seven calendar days returns `stale_data` and no verdict; provider/retrieval/market timestamps and adjustment status are serialized. **Fixed at top-level.** | Data-status/age tests |
| H-04 | High | Position sizing | Tight stops could permit a 10% position even for F-grade/high-beta securities. | Hard cap = min(global cap, grade cap / max(beta,1)). **Fixed.** | F-grade beta-3 sizing test |
| H-05 | High | Targets | One or two analysts could dominate the 12-month target; display also recomputed a different equal-weight target. | Coverage-aware analyst/DCF/technical blend; one shared target in logic and display. **Fixed.** | Thin vs broad coverage test |
| H-06 | High | Monte Carlo | Extreme trailing return drift could make a 30-day median contradict the 12-month valuation case. | Optional annual drift cap added and used in deterministic validation. **Fixed.** | Drift-cap regression |
| H-07 | High | Calibration | Fast/preliminary and pre-override signals polluted the track record used to calibrate win probability. | Record only full-mode, post-resolution authoritative signals. **Fixed prospectively. Existing history should be reset or quarantined.** | Code-path review/full suite |
| H-08 | High | Public deployment | Flask has no user authentication/authorization and uses an in-process development server. | Non-loopback startup is refused. **Safely contained, not a production deployment solution.** Add authenticated reverse proxy/WSGI, identity, CSRF protection, rate limits, and durable jobs before deployment. | Startup guard; input tests |
| H-09 | High | Model validation | Full live multi-factor recommendation has no point-in-time, survivorship-aware OOS validation. Legacy backtests do not reproduce the live engine. | **Open production blocker.** Use point-in-time fundamentals/estimates/news, delisted constituents, corporate actions, and the exact live decision code in walk-forward tests. | APEX leakage/cost/repro tests cover only its separate strategy |
| M-01 | Medium | Fundamentals | Altman Z used sparse metadata/proxies and treated missing data as numeric zero; financial firms were not excluded. | Statement-sourced inputs, explicit `ok/unavailable/not_applicable`. **Fixed.** | Altman tests |
| M-02 | Medium | Insider analysis | Feed order and repeated filer tranches could crowd out distinct insiders; price/date were discarded. | Value-sorted structured records with distinct-insider-first selection. **Fixed.** | Insider tests |
| M-03 | Medium | Decision consistency | Grade-D ACCUMULATE was permitted by the resolver but rejected by consistency validation. | Validator now matches the documented D-grade cap. **Fixed.** | Decision-core regression |
| M-04 | Medium | API security/reliability | Capital, position count, custom symbols, and ticker input were weakly validated. | Numeric bounds, list bounds, and symbol allow-list added. **Fixed.** | Flask contract tests |
| M-05 | Medium | Explainability/UI | Arrows followed rank/trend instead of the displayed number; date countdowns used inconsistent time-of-day math; VWAP/R:R/profit-plan labels were ambiguous. | Shared sign/date helpers and explicit methodology labels. **Fixed.** | Display/date/template tests |
| M-06 | Medium | Durability | Jobs, caches, rate-limit state, and most signal state are local-process memory/JSON. Restarts lose operational state; multiple workers diverge. | **Open.** Move jobs/cache/locks to durable shared services and version signal records. | Not covered |
| M-07 | Medium | Code quality | Two large live engines and several legacy modules duplicate thresholds/calculations; `TradingAnalysis_ELITE.py` and `SP_ELITE_PRO.py` remain monolithic. | Shared decision/risk gates reduce contradictions, but decomposition remains **open**. | Cross-engine tests |
| L-01 | Low | Browser validation | Playwright package exists but the Chromium executable is absent in this environment. | **Environment blocker.** Unit/DOM suites pass; install the pinned browser and rerun all viewport projects. | 106 UI tests pass; E2E assertions not reached |

## Logic and algorithm assessment

### Revised deterministic methodology

- Price/risk: adjusted daily close; returns are finite and split-artifact
  sanitized; annualized volatility is daily standard deviation × √252;
  drawdown is the minimum decline from the running cumulative-return peak.
- Technicals: explicit periods; VWAP is labeled as a 14-day rolling VWAP.
- Sentiment: only direct company headlines receive full company evidence
  weight. Peer/macro evidence is classified and dampened.
- Composite: six 0–100 components with regime weights that sum to 1.0.
- Targets: 12-month analyst/DCF/technical blend. Analyst weight is 50% with
  adequate coverage and 25% when coverage is below three, then available
  sources are renormalized. Monte Carlo remains a separate, input-seeded
  reproducible 30-day distribution with drift capped against the 12-month case.
- Trade setup: long stop must be below entry; base target must be above entry;
  reward/risk is calculated to the base target net of spread/slippage.
- Sizing: loss-budget sizing, liquidity limit, global notional cap, and a
  risk-grade/beta cap. Non-allocating final actions receive zero new capital.
- Final action: the shared resolver is authoritative; unsupported BUY-like
  states become WATCHLIST/HOLD_EXISTING/INVALID as appropriate.

### Assumptions and limitations

- Yahoo Finance is a convenient public source, not an exchange-grade feed and
  has no contractual SLA in this application.
- Currency is displayed, but cross-currency portfolio aggregation/conversion is
  not implemented.
- Daily bars are the primary timeframe; intraday/pre/post-market fields are
  informational and are not mixed into daily indicators.
- The 50% artifact gate is deliberately conservative. A genuine ≥50% session
  will be excluded from return-risk statistics and must be reviewed manually.
- DCF output remains assumption-sensitive and should be presented as a range,
  never certainty. Financial-sector valuation requires sector-specific models.
- Confidence is a deterministic heuristic score, not a calibrated probability
  of profit. No performance guarantee is supported.

## Validation results

### Automated tests

- Python: **296 passed, 2 skipped**. The skips are explicitly obsolete cache/
  constant tests, not silent failures.
- JavaScript/DOM/UI: **106 passed, 0 failed**.
- Browser smoke: Flask started on `127.0.0.1:5500`, but all nine desktop smoke
  cases stopped before their first assertion because the Playwright Chromium
  executable was not installed. The in-app browser was also unavailable.
- Static compilation: all changed Python runtime files compiled successfully.

Baseline before corrections: **247 passed, 42 failed, 2 skipped** in Python;
the 42 failures were the material accuracy/explainability regression matrix.

### Reproducible fixed-fixture trace

Fixture: AAPL adjusted daily OHLCV through **2023-12-29**. This is deliberately
historical and is not represented as a current recommendation.

| Calculation | Reproduced value |
|---|---:|
| Last adjusted close | $192.53 |
| RSI(14) | 51.06 |
| SMA(50) / SMA(200) | $186.63 / $179.49 |
| ATR(14) | $2.57 |
| Annualized volatility | 28.50% |
| Maximum drawdown | -44.38% |
| Risk grade | D |
| Drift-capped 30-day MC median | $193.69 |
| Coverage-aware 12-month base target | $191.09 |
| Composite score | 51.90 / 100 |
| Final action, no existing position | WATCHLIST |

The final result is internally consistent: a neutral composite maps to a hold
candidate, and because the fixture declares no existing position it becomes
WATCHLIST with zero entry order, zero stop/target order, and zero allocation.

### Before/after defect examples

- Synthetic unadjusted 10:1 split: annualized volatility fell from an invalid
  **92.7%** to **21.6%** after one artifact return was removed.
- A $200 analyst target with $110 DCF and $105 technical target produces
  **$138.67** with one analyst versus **$154.00** with 25 analysts; thin coverage
  no longer receives the same influence as broad consensus.
- Grade-F/beta-3 maximum weight is now about **0.67%**, versus the former global
  cap of up to 10%.

## Modified behavior and files

- `core/TradingAnalysis_ELITE.py`: financial/risk/sentiment/target/Monte Carlo/
  sizing/tracking/provenance/display corrections.
- `core/engine/data_status.py`: stale-data taxonomy.
- `core/engine/consistency_validator.py`: aligned grade-D policy.
- `webapp/app.py`: bounded inputs and loopback-only safety guard.
- `core/Alpha_Picks_Engine.py`, `modules/config.py`, and MCP server: removed
  embedded provider credentials.
- Tests: expanded decision/data-status/API contracts and added a deterministic
  full-stack AAPL trace; the existing bug matrix now passes.

## Readiness scores

| Area | Score / 100 |
|---|---:|
| Financial accuracy | 82 |
| Recommendation reliability | 72 |
| Data quality | 76 |
| Explainability | 84 |
| Risk management | 87 |
| Code quality | 70 |
| Security | 76 |
| Performance | 72 |
| UI/UX | 80 |
| Test coverage | 80 |
| **Production readiness** | **68** |

Final status: **not production-ready**. Critical source-code defects found in
this review are corrected, but public deployment and automated capital use are
blocked by H-08/H-09 and the durability/data-lineage limitations above.
