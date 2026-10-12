# Picks fixes

## Batch 1 baseline — 2026-10-04
- Branch `portfolio-ui-quickfix`, clean tracked HEAD `e641f22`; excluded untracked alternate scanner left untouched.
- Full offline Python suites: 1,590 passed, 5 skipped, 19 xfailed; JavaScript: 439 passed.
- Audit snapshot allowed selected extensions but omitted `.pkl.gz` golden recordings. Current HEAD includes 12 recordings.
- Golden completeness collection: 2 to 24; golden master: 5 to 27. Total +44 cases and four formerly skipped placeholders executed: +48 passes, -4 skips.
- No thresholds, golden expectations or skip markers changed to obtain this baseline.

## A02 — Strict scan request validation
- Reject malformed/nonobject JSON, nonboolean flags, noninteger counts, nonnumeric capital and empty custom scans before job creation.
- Apply configured market-specific capital minimum and default; preserve existing universe mapping and legacy mandate handling.
- Files: webapp `app.py`; `tests/test_webapp_input_validation.py`.
- Regression before fix: 16 failures, 5 passes. Affected input/job/mandate/review suites after fix: 76 passed.
- Baseline and A02 logs retained in the isolated `quanta-batch1-baseline-20261004` temporary directory during this batch.
- Remaining Batch 1 findings: A03, A04, A07, A10, A11, A16 and stage/UI contracts.

## A03 — Event publication independent of candidate survival
- `get_top_recommendations` runs `_apply_event_gate([], universe=_scanned_universe)` before its empty-scan return, so flagged universe names still publish as AVOID.
- Warm-up join bounded by new `EVENTS.WARMUP_JOIN_SECONDS` (60 s); after it, the gate checks names itself and unanswered names fail closed.
- Files: `core/SP_ELITE_PRO.py`, `core/engine/thresholds.py`, `tests/test_corporate_events.py`, JS `tests/fixtures/thresholds.json` (regenerated).
- Tests: 2 new (empty-scan publication, bounded join) failed before the fix; event/fund/threshold/review2 suites 176 passed after.
- My Portfolio unaffected: change is inside the Picks book path only.

## A04 — Scan accounting, funded-count classification, independent row caps
- Engine keeps one terminal outcome per scanned name (first failure wins): could not analyse (data gaps + errors), per-name rule, reached book; `earnings_calendar_missing` is a warning only. `scan_accounting()` publishes these plus book stages (entered/passed, applied and configured threshold, `evaluated`).
- Gate 7 is the first selection pass; backfill is `backfilled`; `funded` recorded after the final decision freeze.
- Worker classifies on funded count (watch-only → `no_picks`), caps funded rows at max positions and watch rows at `BOOK.MAX_WATCH_ROWS` separately, reports `funded_over_cap`/`watch_total`/`watch_published`, adds a mandate stage, publishes `accounting`, `funded_count`, `watch_count`.
- Files: `core/SP_ELITE_PRO.py`, webapp `app.py`, new `tests/test_scan_accounting.py` (real engine on synthetic providers, 2 funded names; fake-engine worker cases).
- Tests: 8 new, failed before; affected engine/webapp/golden/provider suites 533 passed, 4 skipped.
- Unresolved (not changed, policy): the cost-viability gate keeps every name when none is cost-viable (`if cost_viable:`); the stage reports `applied: false` in that case.
- My Portfolio unaffected: counters only, no decision path touched.

## Funnel and labels (Batch 1, with A04)
- Stage table (`stageTableCard`) replaces bars + rejection chart when `accounting` is present: per-name outcomes with reasons in words, book stages as "passed of entered" with the rule from config or the applied value, "Not evaluated (not reached / no names reached this stage)" with the configured baseline, warnings in their own line, watch-row cap stated.
- Empty state: compact verdict row (funded, on watch, could not be analysed, scanned, capital, parameters, run time) and one plain sentence; backend prose kept only for legacy payloads. Legacy funnel no longer prints `--`.
- `explain_empty_scan` states the reconciled counts in words; `key=value` lines removed. Partial-scan titles say "N of U names hit errors" instead of an "analyzed" figure that contradicted the table. Reason wording extended in `FILTER_WORDS`.
- Files: `static/elite-pro.js`, `static/app.js`, `static/quanta.css`, `core/SP_ELITE_PRO.py`, `tests/test_scan_health.py`, new `tests/unit/scan_accounting.test.cjs`, `package.json`.
- Tests: 7 JS failed on the old UI, pass now; JS suite 446 passed. `test_empty_scan_explanation_uses_observed_failures_and_gates` rewritten: it asserted the internal `action=2` syntax the audit flagged.

## A07 — Dated, typed chase alerts from config
- New `CHASE` config group (gap / 5-day / 10-day thresholds, growth overrides, gap lookback, minimum bars, score penalties); `_chase_trigger` returns trigger, move, threshold, trigger-bar date, run start date, latest-bar date; reason text built from those values with ISO dates, no "today"/"yesterday", a gap is called a gap.
- Alert payload adds company, `distance_from_20dma` (fraction, like `momentum_1m`), trigger fields and dates. Page: "Chase alerts · recent gap or run-up" table with Trigger, Bar date, From 20-day avg; missing fields read "Not recorded".
- Files: `core/SP_ELITE_PRO.py`, `core/engine/thresholds.py`, `static/app.js`, JS `tests/fixtures/thresholds.json`, `package.json`, new `tests/test_chase_alerts.py`, new `tests/unit/chase_alerts.test.cjs`.
- Tests: 9 Python (boundaries on each OR branch, growth overrides, config, oversold rebound, real-analysis payload) and 3 JS failed before; engine/golden/webapp suites 475 passed, 4 skipped; JS 449 passed. Golden master unchanged, so the refactor kept every decision.
- My Portfolio unaffected: only the Picks worker imports the scanner.

## A10 — Per-market budgets and a parameter-preserving safe retry
- `applyPicksMarketUI` keeps each market's typed budget and restores it on return; the "≥ India minimum looks like INR" reset of valid US budgets is gone. A value below the market minimum still falls back to the configured default.
- `runScan` records the submitted parameters; `retrySafeMode` re-sends them with only `safe_mode` set (the server lowers concurrency; universe unchanged). Throttle copy no longer claims a smaller universe.
- Files: `static/app.js`, new `tests/unit/picks_budget.test.cjs`, `package.json`.
- Tests: 4 JS failed before; JS suite 453 passed.

## A11 — Failure-state colours from theme tokens
- Failed / throttled scan panels use `.scan-fail` / `.scan-fail.is-throttle` (bear/warn `-dim` panel, `-line` border, `-bright` heading); inline hex/rgba removed, including the vetoes block (now `.rejected-block` from CSS, no 0.8 opacity title).
- Files: `static/app.js`, `static/quanta.css`, new `tests/unit/failure_contrast.test.cjs`, `package.json`.
- Tests: contrast computed from the CSS tokens over the tinted panel, both themes, both tones (all ≥ 4.5:1); render test failed before (inline colours). JS suite 458 passed.

## A16 — Level source from the binding setup
- Removed "Scanner price levels, score and net R:R remain the basis"; the risk-check audit now ends with "Levels used:" grouped from each row's `binding_levels` label and `horizon_days` (or "no binding level set published").
- Files: `static/app.js`, `tests/unit/binding_levels.test.cjs`.
- Tests: 1 JS test failed before; JS suite 459 passed.

## Batch 1 verification — 2026-10-04
- API matrix (audit script, run on a scratch copy aimed at this repo): 76/76 cases meet the contract, 0 mismatches (was 13). Evidence: `docs/audit-picks/after/api-matrix.csv`.
- Full suites: Python 1,626 passed, 5 skipped, 19 xfailed, 0 failed (baseline 1,607 / 5 / 19); JS 460 passed (baseline 439). No skip, xfail or threshold loosened.
- Browser: scratch server on 5800 (disk cache off, scratch ledger/cache, tracking disabled, provider APIs blocked). Scan results are real worker output on synthetic providers. 22 screenshots (empty, watch-only, funded, outage; light and dark; 1280, 1440, 390) in `docs/audit-picks/after/`, all viewed: 0 page errors, 0 horizontal overflow, no `--`. The "8/6" outage title comes from a hand-edited fixture, not production. axe was not run (`@axe-core/playwright` not installed); contrast is covered by the token test.
- My Portfolio: no shared decision path changed (counters, chase payload, UI only; golden master unchanged), so no replay was needed.
- Unresolved: mobile setup collapse after a run (UI rule; pair with A13 staged progress); cost-viability gate fall-through when no name is cost-viable (policy, owner); the mandate stage reads "no names reached this stage" when the engine funded none (accurate, different wording from the other stages).

## Cost-viability gate fails closed (owner decision 2026-10-04)
- The gate used to keep every name when none covered `BOOK.COST_VIABILITY_MULTIPLE` x its round-trip cost. It now keeps none, and the book returns empty; the stage table shows "0 of N".
- Files: `core/SP_ELITE_PRO.py`, `static/elite-pro.js` (dropped the "removed none" note), `tests/test_scan_accounting.py`.
- Tests: 1 new test failed before; affected suites 503 passed, 4 skipped, 11 xfailed.

## A11 follow-up — axe on the Picks states
- `@axe-core/playwright` was already declared (^4.11.3) but not installed; installed from the lock with no package.json or lock change.
- axe (WCAG 2.0/2.1 A and AA) on empty, watch, funded and outage states, light and dark: 4 serious color-contrast findings, all from the error-details table header (inline `opacity:0.7`, 2.96:1 in light). Removed it and the fixed rgba card colours; rerun: 0 violations. Regression test added to `tests/unit/failure_contrast.test.cjs`. JS 462 passed.

## A01 — Owned, bounded provider work and a published universe selection
- New `core/engine/bounded.py`: a provider call runs in a child process (`"module:function"` target, pickled arguments and result in a temp file). On timeout the child is killed and reaped before `run` returns, so no abandoned download overlaps the fallback.
- The liquidity prefilter, each history-prefetch chunk and the focused selector use it (replacing the abandoned daemon thread and the unbounded focused download). After any bulk timeout the scan sets `_bulk_timed_out`; history prefetch is then skipped.
- `_universe_selection` records method (liquidity_ranked, even_spread, profile_ranked, custom_list, curated_list, curated_seed, etf_registry, listed_order), cause, seconds, budget, candidates, selected and total time. Published as `result.universe_selection`; the page shows a coverage line ("Universe: an even spread of 100 of 503 names, not liquidity-ranked: …").
- Tests: new `tests/test_bounded_provider.py` (the killed child never finishes its work, result and error round trip, suite default refuses network, timeout recorded, no second bulk call, liquidity ranking), worker publication test, `tests/unit/coverage_line.test.cjs`. The four prefilter tests in `test_review_engine_fixes.py` now inject their stub through `QUANTA_BULK_TARGET` (a child process cannot see a monkeypatch); their assertions are unchanged. conftest defaults that target to one that refuses network access.
- Results: affected Python suites 525 passed, 4 skipped; JS 465 passed.

## A06 — One final sizing and minimum function
- `apply_allocation` is the one final sizing step after every transform (book sizing, Research resize and halving, STRONG_BUY trim, event-gate and action zeroing, client mandate): cap at `max_position_dollars` (effective cap + buffer), round to the share policy, drop anything under `effective_min_position_size`.
- Minimum policy: min(`SIZING.MIN_POSITION_SIZE`, max(new `SIZING.MIN_POSITION_FLOOR` $25, `MIN_POSITION_PCT` x capital)), in account currency, whole or fractional; `min_position_size` delegates to it.
- One-share policy (`one_share_allowed`): one whole share only if it fits the position cap and the per-trade risk ceiling; the book also requires remaining capital and sector room. Only the book's sizing pass (`one_share=True`, `_fund_at_least_one_share`) may round up, so a later scale-down below one share drops the position (the mandate test's existing contract).
- Files: `core/SP_ELITE_PRO.py`, `core/engine/thresholds.py`, JS thresholds fixture, `tests/test_empty_book_root_causes.py` (5 F11 strict xfails retired), new `tests/test_final_sizing.py` (2,000-case invariant, scale-down, one-share, risk ceiling, minimum).
- Changed assertion: `test_min_trade_size_scales_for_small_fractional_accounts` whole-share line 100.0 → 50.0; it pinned the F11 defect the retired xfails describe.
- Tests: full Python 1,642 passed, 5 skipped, 14 xfailed before that one-line update, then 98 passed in that file; JS passed. My Portfolio unaffected: only the Picks worker and mandate use these functions.

## A13 — Staged progress, separate data clocks, phone setup collapse
- Engine `_report_progress` (callback, never raises): selecting; analysing with done/total (monotonic); book; Research check per pick. Worker maps stages to progress ranges (`_SCAN_STAGES`), message "Analysing names: 37 of 100 · 72 s elapsed", job `stage` {key, label, done, total, elapsed_s}. `_update_job` progress never moves backwards. The stream sends stage changes even when the percent is unchanged; the job GET returns `stage`.
- New `StockAnalysis.quote_time` from company-info `regularMarketTime`. `result.data_dates` = latest daily-bar session with per-session counts, latest provider quote time, scan completion. Page: "Daily bars through 2026-10-02 (98 names; 2 on an earlier session) · provider quote … · scan completed …" replaces the single scan-time caveat; missing quote time says so.
- Progress panel stage list (done / current with counts / pending). On phones (≤ 640 px, matching quanta.css), the setup panel collapses after a user-run scan; "Edit scan setup" reopens it.
- Files: `core/SP_ELITE_PRO.py`, webapp `app.py`, `static/app.js`, `static/quanta.css`, `templates/index.html`, new `tests/test_scan_progress.py`, new `tests/unit/scan_progress.test.cjs`, `package.json`.
- Tests: 5 Python + 4 JS failed before; affected Python suites 531 passed, 4 skipped; JS 469 passed. Caught and fixed a missing `timezone` import on the quote-time path (test added).

## A14 — Test integrity
- Golden collections: the audit's six empty-collection skips no longer occur (HEAD has 12 golden recordings; see Batch 1 baseline). Added a frozen scan-level golden snapshot on synthetic providers (`tests/test_golden_scan_synthetic.py`, `tests/golden/expected_scan/synthetic_scan.json`): real worker and engine, 1 funded, 1 Research-withheld watch row, 1 chase alert, accounting, universe selection, bar session. Regenerate only deliberately with `QUANTA_REGEN_SCAN_GOLDEN=1`.
- Executed universe-size contract (`tests/test_universe_size_contract.py`): the real US selector must build exactly the size the Picks menu advertises for Quick, Standard and Comprehensive. Mutation M8 (`"quick": 100` → 7) applied temporarily: 2 failures, file restored from git.
- Chase boundary/date tests landed with A07; xfails retired only with fixes (5 F11 with A06). Remaining 14 xfails are out-of-scope behaviours (insider calibration 8, scan-worker loop 5, blue-sky target 1).
- Remaining 5 skips: 2 conditional legacy-CIK checks (fixture did not take that path), 2 obsolete APIs, 1 optional Streamlit extra.

## A08 — Chase and India literals in config, shown in an effective-rule view
- `UNIVERSE.INDIA_MIN_PRICE / INDIA_MAX_PRICE / INDIA_MIN_MARKET_CAP / INDIA_MIN_AVG_VOLUME / INDIA_MIN_DAILY_VALUE` and `RISK_FREE_RATE_US / _INDIA` replace the inline India screens and the 4% / 6.5% literal. `CHASE.EARLY_MOMENTUM_MIN_SIGNALS / _MAX_ROWS / _SCAN_ROWS` and `PREMOMENTUM_MIN_SIGNALS / PREMOMENTUM_MAX_1M` replace the watchlist and pre-momentum literals (scanner and webapp).
- Fixed an A07 omission: `CHASE` was not in `GROUPS`, so it never reached `/api/thresholds` or the page. New test: every config group is published.
- Guide: "Effective rules" block (chase gap / run-up / penalty, pre-momentum, India screens) filled from config spans; the Discover watchlist sentence now reads its count from config. New formats `inr`, `crore`, `num`.
- Tests: new `tests/test_effective_rules.py` (India screens and the early-momentum threshold change behaviour with config; 3 failed on the old code), 10 new literal guards (all failed before), group-publication test, JS effective-rule test. Affected Python suites and JS 470 passed. My Portfolio unaffected (scanner-only code; config keys added only).

## A12.1 — One decision row per pick
- Each funded and watch card leads with a decision row: Decision, Why (binding reason), one level set (entry zone, stop, target, net R:R) headed by its source and horizon ("Reference levels (watch only)" on watch rows), Allocation (with the pre-mandate figure struck through when trimmed) and Evidence. Price, score, strength, upside/stop distance, impact, risk drivers, price session, level basis, scanner reference levels and narrative sit in a collapsed "Detail".
- Removed the quick-reference table, the R:R ladder and the chip strip: they repeated the same levels and allocation. Concentration moved to the summary; the ticker is a tear-sheet button on the card. An unpublished R:R reads "not published".
- Files: `static/app.js`, `static/elite-pro.js`, `static/quanta.css`; tests `dom_redesign.test.cjs` (table tests ported to the row, plus "each value appears once"), `binding_levels.test.cjs`, `scan_accounting.test.cjs`.
- JS 471 passed.

## A12.2 — One shared evidence and disclaimer note
- `sharedResultsNoteHtml` renders one note directly after the summary: model evidence in plain words from the picks' own status, the engine's confidence caveat (or a default), the stop-loss and holdings caveats and "paper-trade before risking capital". Removed the summary's evidence line, its two caveats and the bottom disclaimer.
- Files: `static/app.js`, `static/quanta.css`, `tests/dom_redesign.test.cjs` (one note, right after the summary, no repeats on cards). JS 472 passed.

## A12.3 — India pre-scan notice from config
- Choosing India shows, before any scan, "No new-money funding for NSE and BSE listings until corporate-event coverage exists: candidates are shown, never funded." The exchanges and the notice's presence come from `EVENTS.UNCOVERED_LISTINGS`; an empty map shows nothing.
- Files: `templates/index.html`, `static/app.js`, `static/quanta.css`, `tests/unit/picks_budget.test.cjs` (shown for India, hidden for US, hidden when config lists no uncovered listing). JS 473 passed.

## A12.4 — Closest to qualifying
- `closest_to_qualifying(published)`: names that reached the book but were not published, at their first failing measurable gate in book order (score below the Accumulate floor for a non-buy action, applied score floor, net R:R with `engine.levels.rr_triggers` entry and target prices on the row's own levels and cost, conviction floor, cost viability with the target needed). Event and banned-grade blocks are rules with no distance and are left out. Sorted by relative shortfall, capped at new `BOOK.CLOSEST_ROWS` (5). Published as `result.closest_to_qualifying`, shown in empty and funded results ("Not picks").
- Files: `core/SP_ELITE_PRO.py`, `core/engine/thresholds.py`, JS thresholds fixture, webapp `app.py`, `static/app.js`, `static/quanta.css`, new `tests/test_closest_to_qualifying.py`, `tests/unit/scan_accounting.test.cjs`.
- Tests: 3 Python + 1 JS failed before; JS 474 passed. Per-name rejects (no analysis row) are not candidates, stated on the page.

## A12.5 — Compact rows first for long result lists
- When a result has more picks than new `UI.COMPACT_LIST_AFTER` (20), each renders as one compact row (ticker, decision, allocation, binding reason); its full card mounts on first open. Shorter lists render full cards. The Pro layer no longer re-injects its duplicate watchlist card when only compact rows are present.
- Files: `core/engine/thresholds.py`, JS thresholds fixture, `static/app.js`, `static/elite-pro.js`, `static/quanta.css`, `tests/dom_redesign.test.cjs` (compact-then-mount; duplicate guard shown red without the fix). JS 476 passed.

## A12.6 — Focus after a user-run scan only
- `onUserScanComplete` (the `runScan` completion path) renders, folds the setup on phones and focuses the result heading (`data-result-heading tabindex="-1"` on the funded, empty and failure headings). Other `renderPicks` calls (restored state, market switch) never move focus.
- Files: `static/app.js`, `tests/unit/scan_progress.test.cjs`. JS 477 passed.

## A13 follow-up — the setup collapse applies on phones only
- Found in the browser run: after a phone-width scan the collapsed setup stayed hidden when the window widened. The hide rule and the "Edit scan setup" button now apply only at ≤ 640 px; desktop always shows the setup.
- Files: `static/quanta.css`. JS 477 passed.

## A12 follow-ups found in the browser run
- The India notice overlapped the market hint inside the paired-field grid; it is now its own line under the market and capital row.
- The Pro cards (book risk, pre-trade review, export) are anchored after the shared note, so the summary and its note stay together (test added).
- axe `scrollable-region-focusable` on the watchlist export box with 25 tickers: the box is now a focusable, labelled region (test added).
- Files: `templates/index.html`, `static/quanta.css`, `static/elite-pro.js`, `tests/dom_redesign.test.cjs`, `tests/unit/failure_contrast.test.cjs`. JS 479 passed.

## Batch 2 and A12 verification — 2026-10-04
- Full suites: Python 1,673 passed, 5 skipped, 14 xfailed, 0 failed (5 F11 xfails retired with A06); JS 479 passed.
- API matrix 76/76; `synthetic_engine.py` 110 checks, 0 mismatches, 8 worker cases complete; `quant_checks.py` 231 checks, 0 failed (scratch copies aimed at this repo).
- Mutations: M7 (chase never fires, now in `_chase_trigger`) → 8 failures in the full Python suite; M8 (`"quick": 100` → 7) → 2 failures in the universe-size contract. Both restored from git.
- One live Quick scan (public data, scratch cache and ledger, tracking off, not on 5500): 120.7 s total (audit: 280.1 s). Prefilter liquidity-ranked 100 of 518 in 14.0 s and prefetch 4.0 s, both in owned child processes, reaped. No provider thread left (only tqdm's monitor); no timeout occurred live, so the kill path is evidenced by `test_a_timed_out_child_is_killed_before_run_returns`. Funnel 5 + 90 + 5 + 0 = 100, stages chain to 0 funded, 4 watch rows, 4 earnings-calendar warnings. 20 chase alerts, all with company, trigger, move, threshold, bar date, latest bar and 20-day distance. Evidence: `docs/audit-picks/after/live-quick-summary.json` (committed with A12.1).
- Browser (scratch server 5800, synthetic worker payloads plus two labelled render fixtures for closest-to-qualifying and a 25-row list): 34 screenshots, light and dark, 1280 / 1440 / 390, India pre-scan, phone after a run; 0 page errors, 0 overflow, no `--`, axe 0 violations after the fixes above, focus on the result heading after every user-run scan.
