# QUANTA ELITE — Code-Level Bug-Fix Report

Scope: `core/TradingAnalysis_ELITE.py`. Every fix is implemented at the
general logic level — parameterized by ticker, date, and data availability.
The LRCX run in the brief was only one manifestation; nothing below is
ticker-specific. Verified by `tests/test_elite_bugfix_matrix.py` (43 tests)
plus the full existing suite (278 passed).

Line numbers reference the post-fix file.

---

## 1. Root causes and fixes, per bug class

### Bug 1 — Sign/arrow assignment
**Root cause.** Arrows were bound to *rank buckets or categorical strings*,
never to the sign of the number printed next to them:
- Sector row: `sector_laggards` = the 3 lowest-RANKED sectors; render
  hard-coded `[red]▼` per bucket. On an up-day a positive laggard (+0.5%)
  rendered ▼. Same defect in the markdown export.
- SPY row: arrow keyed on the SMA-based trend string while the number shown
  was the *daily* change — an up-trending SPY down on the day rendered
  `[green]▲ -0.80%`.

**Fix.** One shared helper pair — `arrow_for(value)` / `direction_style(value)`
(`core/TradingAnalysis_ELITE.py:1325`) — derives glyph and color from the
sign of the exact value being rendered. All drifting sites now call it:
sector rows (console + markdown export), SPY day-change, price-target
upside rows, track-record average return. Leader/laggard buckets still
control ordering only.

### Bug 2 — Date-diff inconsistency (22d vs 21d for the same date)
**Root cause.** Four independent countdown computations. FOMC / stored
`days_to_earnings` / mega-cap / ex-div used whole-day `date - date` math
(→ 22), but the earnings panel *re-derived* its countdown as
`(parser.parse(date_str) - datetime.now()).days`: a midnight-parsed date
minus a mid-session `datetime.now()` leaves `21 days, N hours`, and
`timedelta.days` truncates → 21 for most of the trading session.

**Fix.** One shared `days_until(target, today=None)` helper
(`:1356`) reduces both sides to calendar dates before subtracting. All five
countdown sites now route through it. Two same-date catalysts can no longer
disagree, for any ticker/date (test: `TestDateDiffConsistency`).

### Bug 3 — Sentiment attribution
**Root cause.** Two sentiment numbers existed: the authoritative
`sentiment_score` (mean of ALL fetched articles, no relevance filtering,
`SentimentAnalyzer._analyze_articles`) and a relevance-aware direct-weighted
score (`_classify_news_relevance`) computed **only at display time and
discarded**. Every scoring consumer — composite sentiment component,
confirmations, WHY-HOLD/BUY insights, thesis bull/bear bullets — read the
contaminated raw score, so peer/macro-only news scored as company evidence
while the news panel itself printed "No direct company headlines found."

**Fix.**
- `SentimentData` now carries provenance: `sentiment_source ∈
  {company, peer, macro, none}`, `has_direct_news`, direct/peer/macro
  counts, `weighted_sentiment_score`, and the full `relevance_detail` dict.
- The orchestrator runs `_reconcile_sentiment_provenance` (`:8069`)
  **before signal generation**, classifying headlines against the static
  sector/industry peer map (no network).
- Every downstream consumer is gated: component score and confirmations use
  `company_sentiment_score()` (dampened when no direct news), insights and
  thesis bullets require `has_direct_news`, and display/MCP reuse the SAME
  persisted classification the scorer saw instead of recomputing with a
  different peer list.

### Bug 4 — Unexplained-move detection (new standing feature)
`Config.UNEXPLAINED_MOVE_THRESHOLD_PCT = 4.0`: whenever
`abs(day_change_pct) >= threshold` AND zero direct company headlines, the
run sets `result.unexplained_move` + note, renders a leading warning, a
NEWS-panel banner, a data-quality check failure, and serializes the flag to
MCP. Fires for any ticker/day meeting the condition.

### Bug 5 — Split-adjustment / price-series integrity
**Root cause.** The main deep-dive fetch was already `auto_adjust=True`, but
the SPY benchmark fetch and the generic `get_historical` fallback relied on
yfinance's *implicit* default (which has changed across releases) — and no
code anywhere guarded against a split artifact reaching vol/beta/drawdown.

**Fix.** `auto_adjust=True` is now explicit on every risk-path fetch, and
`RiskAnalyzer._sanitize_returns` (`:2971`) drops any one-day |return| >
`Config.SPLIT_ARTIFACT_RETURN_THRESHOLD` (0.50) with a logged warning
naming the implied split ratio. Applied to stock returns, SPY returns, and
the drawdown series. Regression test simulates an unadjusted 10-for-1 split
(the LRCX Oct-2024 case, synthetically) and asserts vol/drawdown stay sane.

### Bug 6 — Altman Z = N/A (and Data Quality: PASS coexisting with it)
**Root cause.** All Altman inputs were pulled from yfinance `.info`, which
rarely carries balance-sheet fields; `totalAssets` defaulted to 1, the
`> 1` guard failed, Z silently became 0 → bare "N/A" for most tickers. No
financial-sector gating existed, and the data-quality flag never looked at
fundamentals gaps.

**Fix.**
- Inputs now come from `stock.balance_sheet` / `stock.financials` first
  (via `_statement_value`, `:2755`), `.info` as fallback; real Retained
  Earnings replaces the ROE proxy when available.
- Financial-sector tickers short-circuit to `altman_z_status =
  'not_applicable'` and render "N/A — not applicable for this sector"; real
  gaps set `'unavailable'` and render "N/A — data unavailable".
- The binary PASS/FAIL flag is replaced by an **itemized checklist**
  (`data_quality_checks`: price feed, analyst target, news freshness,
  sentiment provenance, move-explained, insider context, fundamentals
  coverage, Altman availability, stop validity, R/R computability, rating
  basis) that runs identically for every ticker; the verdict derives from
  the itemized results and renders as e.g. "WARN (7/9 checks, 2 flagged)".
  An Altman gap on a non-financial now *cannot* coexist with a clean PASS.

### Bug 7 — VWAP window labeling
The `ta`-library VWAP default (14-bar rolling) was silently used and
labeled bare "VWAP", implying an anchored/session VWAP. The window is now
explicit (`Config.VWAP_WINDOW = 14`) and every display renders
`VWAP (14d)`, including the pullback-support candidate label.

### Bug 8 — Insider transaction detail and sampling
**Root cause.** Notable transactions were the *first five* Finnhub rows in
feed order over $100k — no sort, no dedup by filer, and the formatted string
dropped the date and per-share price even though both were parsed.

**Fix.** Transactions are collected as structured dicts
(`key_transactions_detail`, `:717`) with name/action/shares/price/value/
date/code, sorted largest-first, and selected **distinct insiders first**
(one filer's tranches can no longer crowd out other sellers). Display
strings now read `NAME sold 8,233 shares @ $371.20 on 2026-06-12
($3,055,843)`. Thresholds are named constants
(`NOTABLE_TXN_MIN_VALUE/MAX_COUNT`).

---

## 2. Methodology fixes

1. **R:R convention** — one convention: reward/risk to the *base target*
   net of estimated spread+slippage (TradeSetupGenerator), labeled in the
   UI as **"R/R (to base target, net costs)"** in both the signal box and
   beginner guidance.
2. **Weights & grade cutoffs** — regime weight tables moved to
   `Config.REGIME_WEIGHTS`; `_calculate_composite` fallback defaults now
   reference the same `_DEFAULT_WEIGHTS` (no duplicated inline literals);
   letter grades from `Config.GRADE_CUTOFFS`; signal thresholds from
   `Config.SIGNAL_CUTOFFS` (kept adjacent so the scales can be compared).
3. **Confidence %** — `_determine_signal` now documents the full
   reproducible formula (composite ± enumerated adjustments with caps/
   floors) in its docstring; same inputs → same confidence for any ticker.
4. **Track-record "correct" rule** — one documented rule in
   `_evaluate_outcome` (`:6833`): BUY/SELL win at ±`ACTIONABLE_WIN_PCT`
   (2%), HOLD correct inside ±`HOLD_CORRECT_BAND_PCT` (5%); overall win
   rate = BUY+SELL wins / BUY+SELL signals, HOLD reported separately (and
   the display now says so).
5. **Blended analyst target** — the "BLENDED TARGET (model)" row no longer
   equal-weights Low/Consensus/High (one outlier analyst could move the
   model target). It now shows the consensus-weighted blend
   (analyst 50% / DCF 30% / technical 20%, `Config.BLEND_WEIGHT_*`); under
   thin coverage (< `THIN_COVERAGE_ANALYSTS = 3`) the analyst weight drops
   to 25% and renormalizes. New `MarketIntelligence.analyst_count` (from
   `numberOfAnalystOpinions`, rating-bucket fallback) powers this plus the
   "(N analysts)" display. Low/high rows are labeled "Single most
   conservative/optimistic analyst".
6. **Monte Carlo vs 12-month reconciliation** — `simulate()` accepts
   `annual_drift_cap`; after the blended target is computed, the
   orchestrator compares the MC median's annualized return with the
   target-implied CAGR and re-simulates with capped drift when it exceeds
   tolerance (`MC_TARGET_TOLERANCE = 0.05`). A 30-day median can no longer
   imply more upside than the 12-month base case.
7. **Data quality** — see Bug 6: itemized per-field checklist, identical
   for every ticker, serialized to MCP as `data_quality_checks`.

---

## 3. Structural / template fixes

1. **Track record next to Executive Summary** — `main()` renders
   `display_signal_track_record` (with the current ticker) immediately
   before the analysis instead of at the very bottom, and the panel now
   carries an auto-triggered banner when a signal type's trailing win rate
   is below `WIN_RATE_WARNING_PCT` (40%) on ≥ `MIN_SIGNALS_FOR_WARNING`
   (5) resolved signals.
2. **Position asked before the profit plan** — `main()` collects
   shares/cost basis right after analysis completes, *before* anything
   renders; the generic plan is additionally titled and captioned
   "hypothetical — assumes entry at current price" (console + MCP).
3. **Stop ladder** — long-term/EXIT-ALL, tactical, and pullback stops now
   render as one labeled "STOP LADDER (tightest → widest)" component with
   per-level basis and % distance, replacing rows scattered across the
   beginner table.
4. **De-duplicated messaging** — sentiment bullets across Executive
   Summary / insights / thesis / news panel / MCP now pull from the single
   persisted provenance fact set (`relevance_detail`) instead of
   re-deriving; data-quality wording derives from the one checklist.
5. **Benchmark granularity** — the peer panel now names its exact
   benchmark ETF (industry-level, e.g. SOXX) and explicitly notes that the
   Market Environment panel tracks broad SPDR sectors, so the two reads
   can't be conflated for any sector/sub-industry.

---

## 4. Missing features added (all generic, graceful fallbacks)

- **Risk-scaled position sizing** — `PositionSizer` caps position value at
  `MAX_WEIGHT_BY_RISK_GRADE[grade] / max(beta, 1)` (bounded by
  `MAX_POSITION_PCT`), stored in `PositionSizing.max_portfolio_weight`;
  the console action plan prints the same cap ("Max Weight: … (risk grade
  F, beta 3.0)").
- **Options market data** — nearest-expiry ATM IV + put/call volume ratio
  on `MarketIntelligence` (`options_available=False` → "no listed options"
  line).
- **Short interest / days-to-cover** — already fetched, now rendered in
  the Insider panel for every ticker.
- **13F flow trend** — best-effort ACCUMULATING/STABLE/DISTRIBUTING from
  top-holder reported position changes; UNKNOWN when data absent.
- **Ticker-specific signal accuracy** — `get_statistics(days, ticker=…)`
  filter; the track-record panel shows the entered ticker's own 90-day
  record alongside (never instead of) the aggregate.
- **Unexplained-move flag** — standing feature (Bug 4).

---

## 5. Prioritized fix list (by potential signal distortion for *some* ticker)

1. **Sentiment provenance gating** — silently inflated/deflated the
   composite (±20 pts of the sentiment component, more for high-growth
   names where sentiment weight is reallocated) and produced false
   WHY-BUY/HOLD evidence for every thinly-covered ticker.
2. **Split/price-series integrity** — one unadjusted split anywhere in the
   lookback poisons beta, annualized vol, max drawdown, and therefore risk
   grade, signal downgrades, position size, and Monte Carlo vol — the
   whole risk stack.
3. **Blended-target outlier weighting** — for 1-2-analyst small caps the
   equal-weight low/high blend could swing the model target by tens of
   percent.
4. **MC vs 12-month contradiction** — hot momentum names showed 30-day
   medians above the 12-month base case, directly contradictory guidance.
5. **Altman Z sourcing + data-quality PASS** — a "PASS" stamped over
   missing solvency data invites unwarranted trust on every ticker whose
   `.info` lacks balance-sheet fields (most of them).
6. **Date-diff truncation** — off-by-one countdowns for any afternoon run;
   small per-field, but it silently undermines catalyst timing everywhere.
7. **Arrow/sign drift** — misread market context on any mixed day
   (worst-case: all laggards positive but rendered red/down).
8. **Track-record placement/banner** — a 9%-win-rate BUY engine was
   footnoted below the recommendation it should have tempered.
9. **Insider sampling/detail** — repeated filer + missing dates/prices
   overstate apparent insider conviction for any name with tranche sellers.
10. **VWAP labeling / R:R labeling / profit-plan labeling** — clarity
    fixes; they don't move numbers but prevent misreading them.

---

## 6. Constants & thresholds (all in `class Config`, single definition)

| Constant | Value | Used for |
|---|---|---|
| `UNEXPLAINED_MOVE_THRESHOLD_PCT` | 4.0 | anomaly flag: abs daily % with no direct headlines |
| `SENTIMENT_DIRECT_WEIGHT` / `SENTIMENT_PEER_WEIGHT` | 0.8 / 0.2 | direct-vs-peer headline blend |
| `SENTIMENT_NO_DIRECT_DAMPENER` | 0.3 | dampener when zero direct headlines |
| `GRADE_CUTOFFS` | 80/70/60/50/40/30 → A…F | letter grade (`get_composite_grade`) |
| `SIGNAL_CUTOFFS` | 75/65/55/45/35/25 → STRONG_BUY…STRONG_SELL | `_determine_signal` |
| `REGIME_WEIGHTS` | BULL / BEAR / HIGH_VOL tables | regime-conditional composite weights |
| `WEIGHT_*` | .30/.25/.15/.10/.10/.10 | sideways/default weights (also the only fallback) |
| `BLEND_WEIGHT_ANALYST/DCF/TECHNICAL` | 0.50/0.30/0.20 | 12-month blended target |
| `THIN_COVERAGE_ANALYSTS` / `BLEND_WEIGHT_ANALYST_THIN` | 3 / 0.25 | thin-coverage down-weighting |
| `ACTIONABLE_WIN_PCT` / `HOLD_CORRECT_BAND_PCT` | 2.0 / 5.0 | track-record resolution rule |
| `WIN_RATE_WARNING_PCT` / `MIN_SIGNALS_FOR_WARNING` | 40.0 / 5 | track-record warning banner |
| `MAX_WEIGHT_BY_RISK_GRADE` | A .10 … F .02 (÷ max(beta,1)) | risk-scaled position cap |
| `NOTABLE_TXN_MIN_VALUE` / `NOTABLE_TXN_MAX_COUNT` | $100k / 5 | insider notable-transaction list |
| `MC_TARGET_TOLERANCE` | 0.05 | MC-vs-12-month reconciliation tolerance |
| `VWAP_WINDOW` | 14 | VWAP computation AND every display label |
| `SPLIT_ARTIFACT_RETURN_THRESHOLD` | 0.50 | one-day return treated as split/data artifact |

R:R convention: **reward/risk to the base target, net of estimated
round-trip costs** — labeled "R/R (to base target, net costs)" wherever
rendered.

---

## 7. Test matrix → tests

| Matrix case | Test |
|---|---|
| Ticker with a recent split (LRCX Oct-2024 class) | `TestSplitArtifactSanitization` (synthetic unadjusted 10:1) |
| Heavy same-day company news → `source == 'company'` | `TestSentimentProvenance.test_direct_news_sets_company_source` |
| Thinly-covered ticker, no direct news + big move | `TestSentimentProvenance.test_macro_only_news_*`, `TestUnexplainedMove.*` |
| Two catalysts on the same date | `TestDateDiffConsistency.*` |
| Bank/financial ticker → Altman "not applicable" | `TestAltmanZ.test_financial_sector_is_not_applicable` |
| 1-2 covering analysts → blend not outlier-dominated | `TestBlendedTarget.test_thin_coverage_downweights_outlier_consensus` |
| Arrow == sign across +/−/0, every arrow site | `TestArrowSignConsistency.*`, `TestTemplateLabels` |
| MC 30d vs 12-month consistency | `TestMonteCarloDriftCap` |
| Track-record rule + per-ticker stats | `TestTrackRecordRule.*` |
| Insider distinct sampling + date/per-share price | `TestInsiderTransactions.*` |

Run: `cd stock-analysis && python3 -m pytest tests/test_elite_bugfix_matrix.py -v`
(offline; no network). Full suite: `python3 -m pytest tests/ -q` → 278 passed.
