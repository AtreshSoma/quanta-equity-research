# QUANTA Elite Picks remediation: implementation plan

On approval, this file is copied to `stock-analysis/docs/IMPLEMENTATION_PLAN.md`, with `stock-analysis/docs/IMPLEMENTATION_LOG.md` next to it.

## Context

The read-only audit (`stock-analysis/docs/PICKS_AUDIT.md`, written against `audit-remediation @ 4f878f8`) found 15 defects in the Picks page. The main ones:
- **LNTH** is shown under a signed cash takeover, with a pre-deal target.
- **HST**'s card shows R:R 2.25 while its binding reason quotes 1.23, which comes from a different engine's levels.
- There are two risk grades.
- The Standard universe is an alphabetical fallback.
- A watch-only result gets no explanation.
- Sizing ignores the account.
- Picks and My Portfolio can contradict each other.

The goal is for every published number to be the one that decided, with every threshold in one config, every empty result explained, both pages agreeing per ticker, and nothing funded that the data cannot support. The deep engine is not weakened and the factor weights are not retuned.

Owner decisions, all final: Q1 through Q6, plus grade D at half size everywhere. Also decided in this session:
- The golden My Portfolio snapshot comes from **your sign-in on a scratch-port server**.
- The SEC contact comes **from an env var only** (`QUANTA_SEC_CONTACT`). If it is unset, the check is unavailable and funding is blocked.
- **F15 is ported with adapted tests.** Assertions that only hold on the old branch are marked `xfail(strict)` and logged.

## Phase 0 verification (done read-only during planning)

All 15 findings still apply. The table records what has moved since the audit (checkpoint 2: recorded and adapted, no stop needed).

| F | Status | Current location and change from the audit |
|---|---|---|
| F1 | Confirmed, with additions | No M&A or delisting gate exists anywhere. `EdgarClient.recent_filings(symbol, since)` in `core/portfolio/sources.py:90` filters to `FORMS` (no DEFM14A, SC 14D9, 25-NSE or 15-12B) and already parses 8-K `items`. **New:** the User-Agent mentions `QUANTA_SEC_CONTACT`, but nothing reads it. TA's `sec_edgar_parser` scores 8-K 2.01/5.01 M&A as a **positive** catalyst, and fast mode (used by deep confirm) skips catalysts entirely. The 52-week high has no date (`SP:4400`). |
| F2 | Confirmed | OPTION-A is at `SP:10935-10989`. The ONE DECISION cap is at `SP:10991-11004` and never reads `res.trade_setup`. Its fields are at `TA:1011`. No `entry_zone_*` exists in the engine. `entryZoneLow` is at `app.js:6077`. Two R:R formats: `fmtNumber(rr,1)` at `app.js:6103` and `toFixed(2)` at `elite-pro.js:332`. |
| F3 | Confirmed | The scanner grades on 3 inputs over 1 year (`SP:8594`). TA grades on 5 inputs over 2 years and computes `grade_drivers` (`TA:3464-3488`). `decision_for_scan_result` (`SP:3369`) leaves `blockers`, `primary_blocker` and `holder_action` empty. |
| F4 | Confirmed | `smart_prefilter` is at `SP:10009`. It uses `tickers[:1000]` and a 90 s budget. `_even_sample` has 8 fallback call sites. Nothing records the selection method. |
| F5 | Confirmed, changed | Deep confirm and backfill run **inside the engine** (`SP:11829-11878`), so the engine can return more rows than `max_positions`. `app.py:5279` then truncates after the fact. `final_pick_count = len(picks)` is at `app.py:5454`. Gate 7 is at `SP:11887`. `explain_empty_scan` (`SP:6757`) ignores `_zero_alloc_reasons`. "Failed" counts exceptions only. |
| F6 | Confirmed | The 2500 literal appears in `index.html:392`, `app.js:5197/5946`, `app.py:4784`. The US reset is at `app.js:5169`. The scan takes no holdings. Cash is available at `pf.account.cash` and `pf.overview.cash`. |
| F7 | Confirmed | `FinalAction` has no SELL or TRIM. There is no `engine/ladder.py`. My Portfolio runs TA in full mode (`app.py:6413`), while deep confirm runs fast plus insider (`SP:10819`). No `(ticker, as_of)` cache exists. G2 bans grade D at `SP:11219`. |
| F8 | Confirmed | The registry holds only `elite.composite` (NO_EDGE), with the invalidated CAGR in its caveat. `elite.research.v2` and `elite_pro.composite` both resolve to UNTESTED. |
| F9 | Confirmed, changed | `blockers.py:60` hard-codes "1.5:1". `app.js:1075` is now the constant `GUIDE_MIN_RR`. The literal "1.5:1" also appears in `index.html` guide `:994/:1123/:1373`, `my-portfolio.js:856`, `TA:7477/11517` and `rich_display.py`. |
| F10, F11, F12, F13, F14 | Confirmed | Z-score stats come from post-gate survivors only (`SP:10513`). The 0.5 halving skips the minimum (`SP:11023`). `effective_min_position_size` and `_fund_at_least_one_share` are absent. The header stale threshold is 10 min (`app.js:2356`). The Aggressive mandate is ×1.00. |
| F15 | Confirmed | Branch `quanta-review-redesign` @ `064f747` is not an ancestor of HEAD. Its tests need 4 helpers that HEAD lacks. The insider tests pin market-cap-scaled thresholds that exist only on that branch. |

## Working setup (every run)

- **Branch:** `picks-remediation`, from `audit-remediation @ 4f878f8`. The first commit adds the audit, this plan and the log.
- **Scratch env:** `QUANTA_TRACKING_DISABLED=1`, plus `QUANTA_LEDGER_PATH`, `QUANTA_CACHE_DIR` (a copy of `data/cache`; the original is only read), `QUANTA_JOURNAL_PATH` and `QUANTA_EVIDENCE_PATH`. All of them point at a copy or file in the scratchpad.
- **Working directory:** set to scratch, because the engine logs to `./logs`.
- **Servers:** never on 5500. When a server is needed, start it with an explicit `QUANTA_PORT` from 5650 up, confirm it is my process (via `/api/cache/stats`), and stop it afterwards. No trades and no order previews.
- **Baseline code for before/after comparisons:** `git worktree add <scratch>/baseline 4f878f8`. The same day's data and the same scratch-cache copy are used for both sides.
- **Suites:**
  - Python, in `stock-analysis`: `python -m pytest -q`, run under both local time and `TZ=UTC` (Git Bash).
  - JavaScript, in `ui/stock-analysis/webapp`: `npm test`.
  - Playwright picks specs: run on the scratch port with `BASE_URL`.
- **Per finding:** write the failing test, write the fix, run the affected suite, update the log section, then commit `F<n>: <what>`. The log section goes in the same commit, along with the attribution trailer.
- **Context:** I can't trigger `/compact` myself. The log is the resume point, and I'll say when a phase boundary is a good moment for you to run it.

## Phase 0: baseline

1. **Baseline suites.** Known failures are streamlit, pyarrow and `test_end_to_end_with_analysis` (which depends on the wall clock). **Checkpoint 1:** if anything else fails, stop and report.
2. **Golden scan.** New tool `ui/stock-analysis/webapp/tests/tools/golden_scan.py`. It calls `app._run_stock_picks` in-process with US, $2,500, Balanced, max 5, diversify on, then the account's cash (amount redacted), $25,000 and max 1. The JSON output stays in scratch. A summary goes in the log: rows, actions, levels, R:R, the funnel and the explanation.
3. **Golden My Portfolio.**
   - I start a server on a scratch port with the scratch env, and you sign in once.
   - I read `/api/portfolio` and save one account snapshot to scratch only: symbol, weight, value, sector, cash and buying power.
   - New tool `tests/tools/portfolio_decision_table.py` replays that snapshot through the real `PortfolioService` / `build_view`, using a fake provider (the `connected_provider` pattern from `test_my_portfolio.py`) and real analysis. It writes a table per holding: action, basis code and text, score, grade, drivers, holder rule.
   - Running the same replay on the baseline worktree and on the branch isolates the code change.
   - The log gets the table **without amounts**, the same rule the journal follows. The server is stopped at the end of the phase.
4. **F15.**
   - Port `test_empty_book_root_causes.py` and `test_insider_signal_calibration.py` from `064f747` and adapt them to HEAD's names (for example `TradingConfig.min_position_size`, and `engine.decision_engine.minimum_score_for_action`).
   - Tests that need `effective_min_position_size` or `_fund_at_least_one_share` are marked `xfail(strict, reason="F11")`. The F11 commit removes those markers.
   - Assertions on insider behaviour that exists only on the old branch are marked `xfail(strict)` and recorded as unresolved.
   - Commit: `F15: port empty-book and insider calibration regression tests`.

## Phase 1: config foundation (F9, F14, audit 5.6)

- **New `stock-analysis/core/engine/thresholds.py`.** Frozen dataclass groups, loaded once, with today's env overrides kept:
  - `SCAN`: universe, liquidity, prefilter budget
  - `RR`: `SCANNER_MIN` 1.8, `RESEARCH_MIN` 1.5, `INSIDER_RELAX` 0.4 / floor 1.6
  - `COMMITTEE`
  - `REGIME`: VIX bands, signal thresholds, RSI
  - `BOOK`: conviction floors and pool sizes, cost viability 2×, sector caps
  - `RISK_GRADE`: points, bands, `LOOKBACK_DAYS`, `TAIL_WINSOR_PCT`, liquidity bands
  - `SIZING`: `MIN_POSITION_SIZE`, `MIN_POSITION_PCT`, `DEEP_ACCUMULATE_SCALE` 0.5, grade multipliers, mandate scale
  - `LEVELS`: `ENTRY_ZONE_ATR_MULT` 1.0, `TARGET_STRUCT_MAX_AGE_SESSIONS` 63, research horizon 63, deep costs
  - `EVENTS`: forms, lookback, `VOL_COLLAPSE_RATIO`, `ARB_SPREAD_MIN`
  - `LADDER`: avoid 45, accumulate 55, buy 65, reduce 45, exit 35, `trim_review_weight`
  - `UI`: header stale 5/10 min, `MAX_WATCH_ROWS`, `NEAR_MISS_N`, `TRACK_RECORD_MIN_SAMPLE`, capital default
  - `as_dict()` exports the whole set.
- **Values are moved, not changed.** `TradingConfig`, `RiskLimits`, TA `Config`, `engine/types.py`, `score_bands.py` and `risk_grade.py` keep their attribute names as aliases that read from `thresholds`, so call sites don't move. The inline literals in audit 5.6 are replaced by keys.
- **Reason and remedy text** in `blockers.BLOCKERS`, the TA reasons, `rich_display` and committee reasons is formatted from config and from the candidate's actual values. New `SCANNER_LOW_RR` code (the 1.8 gate) is kept separate from `LOW_REWARD_RISK` (the Research gate at 1.5), so the text names the hurdle that actually fired. New `ANALYST_DOWNSIDE` code is used when the "DCF" input is really analyst downside. The `get_top_recommendations` docstring and comments stop quoting stale numbers.
- **`GET /api/thresholds`** in `app.py` returns `thresholds.as_dict()`. JS fetches it once and uses it for `GUIDE_MIN_RR`, the header thresholds, the capital default, and the guide and My Portfolio "x:1" text through `data-threshold` spans. The "Scan method" drawer renders it.
- **F14:** remove the Aggressive option from `index.html` and the JS hints. The API keeps accepting `aggressive` for old clients, mapping it to Balanced with a notice.
- **Tests:**
  - `test_thresholds_single_source.py`: remedy text contains `RR.RESEARCH_MIN` formatted from config, and patching the config changes the text. The endpoint returns every group.
  - A guard test greps engine, app, JS and HTML sources for the moved literals ("1.5:1", "2500", `>= 10000`, 0.5 halving, VIX bands, and so on) outside `thresholds.py` and tests.
  - JS tests: the mandate select has no Aggressive option, and the guide text is filled from thresholds.
- **"Nothing hard-coded" is defined as:** every decision threshold in `TradingConfig`, `RiskLimits`, TA `Config`, `engine/*` and audit 5.6. Any numeric literal left in SP or TA that I judge not to be a threshold (window lengths inside indicator math, for example) is listed in the log with its reason, so you can disagree.
- **Commits:** `F9`, `F14`.

## Phase 2: critical correctness

### F1: corporate-event gate (new `core/engine/corporate_events.py`)

**EDGAR access.** `EdgarClient` gains a `forms=` argument, and My Portfolio's form set stays the same. The User-Agent is built from `QUANTA_SEC_CONTACT` / `QUANTA_SEC_USER_AGENT`. When neither is set, the status is `unconfigured`.

**Detection.**
- **Strong signals:** DEFM14A, PREM14A, SC 14D9 or SC TO-T within `EVENTS.LOOKBACK_DAYS`.
- **8-K item 1.01** counts only when its primary document matches merger-agreement language with this company as the target ("Agreement and Plan of Merger", Merger Sub / surviving-corporation patterns). Item 1.01 alone also covers credit agreements, so it is not enough.
- **Cleared** by a later 8-K item 1.02 (termination of the agreement).
- **DELISTING:** 25-NSE, 15-12B, or 8-K item 3.01 / 2.01 completion.

**Deal terms.**
- The per-share cash price and any CVR mention are parsed from the filing text, with a bounded fetch cached for 24 hours.
- If the price cannot be parsed, the text says so and does not invent one.

**Result.** `EventCheck{status: clear|flagged|unavailable, code, text, source, url, deal_price, filed, agreement_date}`.
- The text is built from the data, for example: "Pending cash acquisition at $102.50 (merger agreement 2026-08-03; DEFM14A filed …). The price is anchored to the deal terms; the CVR is non-transferable." The CVR clause appears only if the filing mentions one.
- A secondary flag, `POSSIBLE_PENDING_DEAL`, fires when the 20-day vs 120-day ATR ratio falls below `VOL_COLLAPSE_RATIO`. It caps the name at Watch with a caution.

**Where it runs.**
- In `get_top_recommendations`, on every book candidate before sizing and deep confirm, and on every published or near-miss row.
- `PENDING_ACQUISITION` / `DELISTING` → AVOID, shown in the rejected list.
- `unavailable` → not funded (Watch), with code `EVENT_CHECK_UNAVAILABLE` and the text "Corporate-event check unavailable: <reason>".

**Holders.** In `blockers`: Sell when `PENDING_ACQUISITION` and the spread to the deal price is below `EVENTS.ARB_SPREAD_MIN`. If the deal price is unknown, there is no Sell, and the text says "spread unavailable".

**TA's positive M&A catalyst score** is neutralised for names whose event status is flagged.

**52-week high age (audit 5.1).** Record the high's date (`idxmax`) in the indicators. `_calculate_price_targets` drops a high older than `TARGET_STRUCT_MAX_AGE_SESSIONS` from the target mean.

**Tests:**
- `test_pending_acquisition_avoid`: uses a recorded LNTH filing fixture with an injected `get_json` and document fetch. Gives AVOID, levels hidden, reason built from the data.
- 1.01 credit-agreement false positive → clear.
- 1.02 clears the flag.
- Unconfigured or erroring EDGAR → unavailable, so not funded.
- Holder Sell vs spread.
- Stale 52-week high is dropped from the target mean.

**Live check needs `QUANTA_SEC_CONTACT`.** If it isn't set by then: checkpoint 4, and I'll ask.

### F2: levels that bind the decision (new `core/engine/levels.py`)

- **`publish_levels(source, entry, stop, target, rr, atr, cost_per_share, horizon_days, basis)`** returns `{source, entry, entry_zone_low, entry_zone_high, stop, target, risk_reward (2 dp, net at the top of the zone), horizon_days, target_basis}`.
- **`rr_triggers(...)`** returns `target_needed = entry + m·(entry − stop + c) + c` and `entry_needed` (the spot price at which R:R reaches the minimum with the stop and target unchanged).
- **Q1: Research levels govern.**
  - When deep confirm ran and `trade_setup.levels_available` is true, `rec.binding_levels = publish_levels('research', …)` from `res.trade_setup`. These levels are used for the card, the table, the chip, the near-miss triggers and the remedy text.
  - Funded sizing is re-run against the binding stop, so the published stop is the one the position was sized on.
  - The scanner's levels move to `scanner_levels` and appear only in the disclosure, labelled "Scanner reference levels".
  - When deep confirm didn't run or returned no levels: `binding_levels.source = 'scanner'`, labelled "Research check not run". Funding follows today's rule.
- **The cap is not loosened.**
- **JS:** delete `entryZoneLow`. A single `fmtRR()` (2 dp) is used by the table, the chip and the card. The remedy text comes from the code that fired, plus its triggers, with the generic `VETO_REVALIDATION` copy for that code removed.
- **Tests:**
  - `test_binding_levels_are_published`: HST-shaped fake deep result. The published R:R equals the R:R in the binding reason.
  - Unit tests for `rr_triggers`.
  - JS `test_rr_rounding_consistent`, plus a test that the zone comes from the payload.

**Commits:** `F1`, `F2`. Then the golden scan is re-run and the delta logged.

## Phase 3: risk and decisions (highest risk)

### F3: one risk grade

- **New `engine.risk_grade.grade_from_history(close, high, low, volume, benchmark=None)`.**
  - Inputs: volatility, beta, max drawdown, tail and liquidity, over `RISK_GRADE.LOOKBACK_DAYS` (504, TA's current window, so My Portfolio's window doesn't change).
  - The tail score uses returns winsorized at `TAIL_WINSOR_PCT` (1/99).
  - TA's liquidity bands move here unchanged.
  - Returns `(grade, points, drivers)`.
- **Both callers use it:**
  - The scanner fetches `2y` bars once and hands `hist.tail(252)` to all existing indicator code, so scanner metrics other than the grade are byte-identical. Tested.
  - TA `_calculate_risk_score` calls the same function.
- **Decision:** gains `risk_grade_drivers` and `risk_grade_lookback_days`. `decision_for_scan_result` fills `blockers`, `primary_blocker` and `holder_action` through `action_contract`, copying the pattern in `decision_for_analysis` (`TA:7462`). The card replaces "The restrictive result controls" with the drivers.
- **Tests:** `test_single_risk_grade` (same bars give the same grade and drivers on both paths, and drivers are in the payload). Separately, one −40% gap day does not add tail points.

### F7: one ladder (new `core/engine/ladder.py`)

- **`decide(inputs, holding=None)`** composes the existing parts in this order:
  1. Event gate.
  2. `resolve_final_action`.
  3. Grade policy: D at half size, F blocks new money.
  4. Q4: STRONG_BUY folds into BUY unless `evidence.status == VALIDATED`.
  5. For holders: `holder_rule`, plus event Sell, plus the `trim_review_weight` Trim.
  - It returns `{new_money_action, holder_action, basis, codes, size_multiplier}`.
  - Picks shows a holder action only for names you own.
- **G2:** ban only grade F. Grade D passes at the 0.5 multiplier.
- **Shared research call.** New `engine/research_cache.py`, keyed `(ticker, session date, mode)`, with one configured `RESEARCH_MODE` used by deep confirm and by `_portfolio_analyze`.
  - I'll first check whether TA's *action* depends on `account_size`, and include it in the key only if it does.
  - The mode is My Portfolio's current one (full), with insider data added only if it is already on. Any change to My Portfolio's mode is measured, not assumed.
- **Guidance:** `guidance.py` consumes `ladder.decide`. `portfolio_constraints` (buying power) stays a portfolio-level input and is shown as one.
- **Tests:** `test_ladder_rules` (one per table row in audit section 6), `test_strong_buy_folds_until_validated`, `test_grade_d_half_size_book`, and session cache hit and miss.

### Checkpoint 3

The golden My Portfolio replay is run on the baseline and on the branch, and compared row by row. **Any changed action stops the work.** I'll report the before/after table with the cause per row: drivers, winsorization, mode, Q4. The expected candidates, which are an inference, are the grade-F Trims on NVDA, TSM, LRCX, MU and AVGO.

**Commits:** `F3`, `F7`.

## Phase 4: scan integrity

### F4

- The bulk `yf.download` in `smart_prefilter` becomes **chunked** (`SCAN.PREFILTER_CHUNK`). Each chunk is cached to `QUANTA_CACHE_DIR/bulk/` with pickle, keyed by chunk hash, period and session date.
- Chunks that finish within the budget are liquidity-ranked from the cache. The download keeps filling the cache after the budget, so the next scan is fully ranked. The rest of the universe is filled by even spread.
- The `tickers[:1000]` silent cut is removed (chunks cover all names).
- `funnel.universe_selection` takes one of `liquidity_ranked`, `partially_ranked` (with coverage) or `even_spread_fallback`, recorded at every `_even_sample` site.
- **Test:** `test_universe_selection_reported` (a fake download that sleeps past the budget gives the fallback; a cached frame gives ranked).

### F5

- `funded_pick_count` drives `_classify_scan_status`. The explanation is always built.
- `explain_empty_scan` returns a structured `{headline, lines, near_misses}` plus the legacy string, and it reads `_zero_alloc_reasons`.
- `_record_rejection(ticker, gate, value, threshold, levels)` is called at the score, signal, size, R:R, committee and adjusted-size gates. `_near_misses` keeps the top `NEAR_MISS_N` by normalized distance, with exact price triggers (R:R gates) or the capital needed (size gates, solved analytically).
- **Gate counts:**
  - `gate7_sector_cap` is set to the sized count captured before deep confirm.
  - New `gate8_research_confirmed`.
  - Funnel gains `funded`, `deep_confirm_withdrawn`, `near_misses` and `universe_selection`.
- **`app.py`:** keep `funded[:max_positions] + watch[:MAX_WATCH_ROWS]`, and remove the post-hoc truncation.
- **Header:** "Failed n · insufficient data n · validation n · not reached n".
- **Tests:** `test_watch_only_scan_explained`, `test_max_positions_limits_funded_only`, `test_small_capital_explanation`, `test_gate7_after_backfill`. JS: `no_picks` with watch rows still renders the rows.

### F10

- **Decision score (0–100)** is the score the ladder used: the Research composite when deep confirm ran, otherwise the scanner score ×10. Its source is labelled.
- Factor sub-scores are published on 0–100.
- Strength stars and Early Mom stop being scores; Early Mom stays as a tag.
- **Z-score fix:** `UniverseStats` is built from **every scored name**, collected before the score gate. It applies only to `ranking_score` and the percentile. The gates still use `overall_score`, so nothing is retuned.
- **Tests:** z-scores run when 2 names survive out of ≥ 30 scored; one score per card.

### F11

- `TradingConfig.effective_min_position_size(capital, share_price)` = `min(MIN_POSITION_SIZE, MIN_POSITION_PCT × capital)` in whole-share mode, valid only if the risk budget buys at least one whole share.
- `_fund_at_least_one_share`.
- The deep-ACCUMULATE halving goes through the same check.
- The F15 `xfail` markers are removed.
- **Tests:** `test_halving_respects_minimum`, `test_min_trade_capital_relative_whole_shares`.

### F12

- `setFreshness` uses `exchangeSession`. Outside regular hours, data from after the last close is not stale. Thresholds come from config.
- Labels: "Market data" on the header chip and "Daily bar" on the card.
- **Test:** JS `test_header_session_aware`.

**Commits:** `F4`, `F5`, `F10`, `F11`, `F12`. Then the golden scan is re-run.

## Phase 5: account awareness (F6)

- **Capital prefill.** The Picks view reads cached `GET /api/portfolio`, with no refresh triggered. When connected and the market is US, it prefills `#sp-capital` from `account.cash` with the hint "Using account cash $X · edit". A value you type is kept.
- **US reset removed.** The India branch records `dataset.autoSet` / `dataset.usValue` and restores only its own substitution.
- **Holdings in the scan.** `POST /api/stock-picks` accepts `holdings: [{ticker, market_value, sector}]` (validated, ≤ 500 rows, part of `_scan_params_key`). `get_top_recommendations(existing_positions=…)` seeds the sector room and the correlation gate.
- **One sector denominator:** total capital = scan capital + holdings value. It is used by both the code and the summary, and labelled.
- **Portfolio fit panel** (payload `portfolio_fit`):
  - funding source: cash, plus proposed Trim/Sell proceeds from My Portfolio guidance
  - sector weights before and after
  - correlation of each pick with the top-N holdings (over `RiskLimits.MIN_CORRELATION_PERIOD`)
  - limit breaches
- **Tests:** JS `test_capital_us_reset_removed` and the prefill hint. Python: holdings validation, sector caps that count existing exposure, the denominator.
- **Commit:** `F6`.

## Phase 6: UI (F13, audit 8.3 elements only)

- **Verdict line:** action summary · regime · universe selection · daily-bar date · evidence. The **funnel** is shown above the picks, not inside `<details>`.
- **Closest to qualifying,** with exact triggers.
- **Compact pick card:**
  - 90-day price sparkline with entry, stop and target bands. Payload `price_spark`. It reuses `mp-charts` if that file is loaded on Picks; otherwise a small SVG in `elite-pro.js`.
  - Factor bars.
  - The binding gate.
  - Consensus: `targetLow/Mean/High`, `numberOfAnalystOpinions`.
  - Earnings date.
  - Risk drivers.
- **Rejected list** with reasons (LNTH AVOID).
- **Track record,** only when resolved rows ≥ `TRACK_RECORD_MIN_SAMPLE` (reuses `/api/track-record` and `renderTrackRecord`).
- **Staged loading:** `scan_market` reports `analysed/universe` through a progress callback into `_update_job`, which the existing SSE carries.
- **States:** empty, error, partial and stale, as specified in audit 8.4.
- **Each value once per pick,** and **one footer disclaimer.** The other caveats are deleted or folded into `title` tooltips on the figure they qualify.
- **Tests:** a JS DOM test per element; a count test (each pick's stop value appears once, and one disclaimer node); a Playwright picks spec on a scratch port; `visual_audit.cjs` in both themes at desktop and mobile widths. `quanta.css` tokens only, and the `?v=` versions are bumped.
- **Commit:** `F13`.

## Phase 7: evidence display (F8)

- The payload carries `evidence: {scanner, research}`. Research resolves `elite.research.v2` and shows its lineage: "Research engine: NO_EDGE (last tested version elite.composite, through 2026-08-25)".
- The verdict line shows both statuses. No label, chip or text calls a pick validated (tested).
- The invalidated CAGR sentence is removed from the registry caveat.
- **Not done here:** rerunning the walk-forward and the negative risk-leg IC. These go in the log as a separate research task, with commands (`python -m core.apex_quant.run elite-oos` / `elite-walkforward`), the hypothesis and the acceptance criteria.
- **Test:** `test_evidence_statuses_on_picks`.
- **Commit:** `F8`.

## Phase 8: consistency sweep

- **`test_picks_portfolio_parity`:** a unit test with one fake research result shared through `research_cache`. Same ticker and same session give the same `ladder.decide` output on both paths, for unowned and owned names.
- **Live sweep tool:** `tests/tools/parity_sweep.py` runs a custom scan over the holdings ∪ published tickers and the portfolio replay, sharing one session cache, and tabulates the action per ticker on both pages.
- **Each divergence** is either fixed or explained by an input that the page shows (for example buying power, "you own it", weight). MSFT (R:R 1.52 vs "below 1.5:1") and GOOGL ("Hold, no new money" vs a funded ACCUMULATE at $25k) are explicitly resolved in the log.

## Acceptance (outputs pasted into the log)

1. Golden scan re-run:
   - LNTH shows AVOID with the acquisition reason.
   - HST's card R:R equals the R:R in its binding reason.
   - The funnel is in the visible DOM.
   - The watch-only explanation is non-empty.
2. At the account's cash level, the explanation names the $100 minimum (or the capital-relative one) and the nearest name's capital needed. $25,000 survives the market toggle (JS test).
3. With max positions = 1, a funded pick is never displaced by a watch row.
4. The parity test and the live sweep both pass.
5. Every audit section 9.2 test exists and passes. The baseline suites show no new failures, in local time and under `TZ=UTC`.
6. The literal-guard test passes, and a grep is pasted.

## Deliverable: `stock-analysis/docs/IMPLEMENTATION_LOG.md`

- For each finding: change, files, tests, before/after evidence, and anything unresolved.
- The Phase 0 verification table.
- The My Portfolio before/after table, with actions only and no amounts.
- Every threshold moved, with its key, value and old location.
- Literals left in place, with their reasons.
- The research task for the negative risk-leg IC.

## Risks

- **EDGAR.** If `QUANTA_SEC_CONTACT` is unset or EDGAR is down, fail-closed means nothing is funded. That is intended, and the page says so. The 8-K 1.01 false-positive patterns are covered by fixtures.
- **Research levels and sizing.** Using the Research levels and re-sizing on the binding stop changes published sizes. The golden diff will show it.
- **2-year scanner fetch.** This invalidates the warm cache once. Indicator parity is protected by `tail(252)` plus a test.
- **Research mode unification and winsorization** may change My Portfolio calls. That is checkpoint 3: I stop and report with the evidence.
- **Live data drift.** Before/after comparisons always run the same day on the same cache copy, using the baseline worktree.
- **Sign-in snapshot.** It holds account data, so it stays in the scratchpad. It is never committed or published.
