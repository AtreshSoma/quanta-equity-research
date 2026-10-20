# Picks output fix log (2026-10-04)

**Scan under review:** 2026-10-04 21:46. Settings: Focused Growth, $2,500, Balanced, max 5. Outcome: 0 funded, five names held at Watch with EVENT_CHECK_UNAVAILABLE, and the page showed "On watch 0".

**Root causes and evidence:** [docs/audit-picks/output-fix/ROOT_CAUSES.md](audit-picks/output-fix/ROOT_CAUSES.md).

**Baseline:** HEAD 31837df. Python 1,673 passed / 5 skipped / 14 xfailed; JS 479 passed. No new failures.

## I1: event check unavailable (4acb3e4)

**Root cause.** The 5500 server inherited the environment of VS Code, which started 2026-10-01 16:55. The user-scope QUANTA_SEC_CONTACT was written 2026-10-02 14:41, after that, so the server never saw it. Nothing in the app read the user scope.

On top of that, every failure mode looked the same: missing contact, malformed contact, 429, 403, timeout and network error all produced one code with free text. The fix text always said "set QUANTA_SEC_CONTACT".

**Change.**
- New resolver in `portfolio/sources.py`:
  - Lookup order: process, then Windows user scope, then the git-ignored `stock-analysis/.env`.
  - Each value is classified as configured, missing or malformed. The value itself is never exposed.
  - Tests turn the fallback off with `QUANTA_SEC_ENV_FALLBACK=0`.
- `start.ps1` reads the user scope and `.env` too, and prints the state only.
- The app warns at startup and reports `sec_contact` in `/api/health`.
- Each unavailable check now carries:
  - a structured `reason`: contact_missing, contact_malformed, rate_limited, http_error, timeout, network_error, no_registrant, coverage_short, document_unreadable, filing_ambiguous, not_covered, no_client or error;
  - `http_status`;
  - a `fix` that matches the reason. That fix is also the blocker remedy.
- New `result.event_check` field: counts, reasons with their fixes, and the contact state.
- The page gets a banner and a pre-scan notice.

**Files.** `sources.py`, `corporate_events.py`, `blockers.py`, `SP_ELITE_PRO.py`, `TradingAnalysis_ELITE.py`, `start.ps1`, `conftest.py`, `app.py`, `app.js`, `quanta.css`, `index.html`.

**Tests.** `test_sec_contact.py` (27) and 5 JS tests. The golden RELIANCE.NS snapshot gained three fields (reason, fix, http_status); the decision is unchanged.

**Evidence.**
- Scratch port 5800 with the variable absent: health reports `missing` and startup logs a warning.
- Scratch port 5801 with only the user scope set: startup logs "configured … from user".
- Checkpoint 2: the synthetic 18-holding book gives identical My Portfolio decisions before and after (18 × 6 fields).

**Unresolved.** SEC enforces fair access. A rejected address only shows up as HTTP 403, which is now named as such.

## I2: "On watch 0" (1d86c72)

**Root cause.** In `get_top_recommendations`, `_event_watch` was appended only on the normal exit. The early returns (nothing past the quality gates, nothing covering its costs) dropped the held names.

**Change.** `_publish_event_held()` runs on those exits and freezes each held row's decision. The event stage now reports `held` and `avoided` with tickers. A held row states its rule code and fix.

**Tests.**
- Python: 5 in `test_event_held_rows.py`.
- JS: 2.

**Evidence.** The worker now reports watch_count 5 and accounting.watch 5.

## I3: diagnostic mode (50113b8)

**Change.** `event_held_diagnostics()` rebuilds the pool the later gates would have seen: event-clear names plus held names. It applies the book's own formulas for risk grade, score, R:R, conviction and cost.

Each held name then reads either "blocked only by the event check" or "also fails <gate>: now vs needed". The result is published and shown as "diagnostic only, not funded". Selection limits and the Research check are stated as not assessed.

**Tests.**
- Python: 4, including an unavailable check with otherwise-passing names. That case gives zero funded, held rows, "would pass", and no field mutated.
- JS: 2.

**New config.** `UI.NAME_LIST_SHOWN` (5) sets how many names show before Show all.

## I4: progress after Complete (21cefc9)

**Root cause.** No terminal update ever set a stage, so the last running stage stayed current.

**Change.** The worker keeps a stage log. On complete, failed and cancel it publishes a terminal record: each stage is done (with its final counts), stopped, or "skipped: <reason>". The page renders that record with no current step.

**Tests.**
- Python: `test_scan_terminal_stage.py` (4).
- JS: 2.

## I5: "$100 minimum position" (f299e6d)

**Root cause.** The label, the guide demo and the FAQ all read `SIZING.MIN_POSITION_SIZE`. The effective minimum at $2,500 is $50 (A06).

**Change.** The result now carries `min_position_effective`, and every label reads it. The guide computes the same rule from the SIZING keys. The guide test that pinned "$100 minimum trade" now asserts the capital-scaled rule.

**No sizing defect.** MRNA, SMCI, COHR and WDC (inferred) would each have been sized at $44–48, which is under $50. Recomputed from cached bars.

**Unresolved (owner).** The per-name gate scales the minimum by `drawdown_mult`, but `apply_allocation` does not.

## I6: stale chase alert (f781d48, fe8b348)

**Root cause.** The gap rule never compared the latest close with the pre-gap close. The run rules already measure to the latest close, so that half of the finding was refuted.

**Change.**
- New config `CHASE.GAP_FILL_TOLERANCE`, default 0, the strictest setting.
- A filled gap goes to `chase_filled`: informational, with no penalty.
- A partly filled gap shows the share filled and the fade since the gap-day close.
- A run shows how far it sits below its window peak.
- The A07 gap fixtures (which were themselves filled gaps) now hold the gap.
- The chase scroll box is now focusable, because axe flagged it at 390 px.

**Tests.** 8 in `test_chase_gap_fill.py`: held, filled, partial, tolerance, missing bars, run peak, publication. Plus 3 JS tests.

## I7: same issuer (d0126d1, 24704ca)

**Change.** `EventCheck.cik` carries the CIK. `_try_add` and the backfill now key the book by issuer, so a second share class is skipped and named on the selection stage. With no CIK, the ticker is its own issuer, as before.

**Follow-up from the live scan.** The skip record named no one when the first share class had been withdrawn by the Research check. Fixed.

**Tests.**
- Python: 5 in `test_issuer_dedupe.py`, including two share classes both reaching the book.
- JS: 1.

**Checkpoint 2.** My Portfolio decisions are identical.

## I8: page text (0c1294d)

**Change.**
- The coverage line now reads "100 of 204 S&P 500 names in <sectors> (806 names in the full universe) … price download 5.9 s", followed by the event-check coverage.
- Validation failures are logged at WARNING with the ticker.
- `could_not_analyse.tickers` is published, and the outcome table lists those tickers with Show all.

**Unresolved.** The failed name in the 21:46 scan can't be identified. That run logged it at DEBUG only.

## I9 and I10

Analysis only; nothing was changed. See ROOT_CAUSES.md.

## Verification

- **Suites.** Python 1,730 passed / 5 skipped / 14 xfailed. JS 499 passed.
- **API matrix.** 76/76, 0 mismatches.
- **Playwright smoke** on scratch port 5800: 16/16.
- **Mutations.** All five failed tests, then the copy was deleted:
  - no SEC fallback;
  - held names not published;
  - no diagnostic;
  - no gap-fill rule;
  - no issuer dedupe.
- **Screenshots.** Synthetic reproduction from the real engine and real worker, both themes, at 1280, 1440 and 390. All viewed:
  - held names, cause, fix and the finished progress panel are all visible;
  - 0 overflow;
  - axe 0 violations after fe8b348.
- **Live Quick scan** on scratch port 5801, with the contact taken from the user scope:
  - Run time 98 s. Funnel 5 + 90 + 5 + 0 = 100.
  - The event check covered 100 names: 94 clear, 1 flagged, and 5 coverage_short (non-book heavy filers).
  - All five book names passed the event gate and every later gate through cost.
  - Selection kept 3: MSFT was too costly for one share at $2,500, and GOOG was skipped as the same issuer.
  - The Research check funded AMZN at $252 and withdrew CSCO and GOOGL to Watch.
  - Evidence: `output-fix/live-quick-summary.json`, recorded before 24704ca.

## Owner notes

- **The 5500 server was restarted at 22:24:29 outside this session.** It reports the contact as configured, but it runs code from before most of these commits.
- **An issuer with a published row is not refunded.** That includes a row the Research check withdrew; another share class can't take its place. This is stricter than before.

## What changed

- **User-visible.**
  - A banner and a pre-scan notice when the event check can't run, with the exact fix.
  - Held names appear as Watch rows and are counted.
  - A diagnostic block for held names.
  - A finished progress list.
  - The minimum position shown at the scan's own capital.
  - A "Gap filled" block, plus fill/fade and off-peak text on alerts.
  - Same-issuer skips named.
  - A clearer coverage line, and tickers for names that could not be analysed.
- **Behaviour or policy.**
  - The SEC contact is read from the user scope or `.env`.
  - A filled gap carries no chase penalty.
  - A second share class of a funded or withdrawn issuer is not funded.
  - Fail-closed rules and every threshold are unchanged.
- **Files.**
  - Engine: `SP_ELITE_PRO.py`, `TradingAnalysis_ELITE.py`, `engine/{corporate_events,blockers,thresholds}.py`, `portfolio/sources.py`, `start.ps1`.
  - Webapp: `app.py`, `static/{app,elite-pro}.js`, `quanta.css`, `index.html`, `package.json`.
  - Two golden snapshots.
- **Tests added.**
  - Python: 7 files and 57 tests.
  - JS: `picks_output_fix.test.cjs`, 20 tests.
  - Updated: `test_chase_alerts.py` fixtures, the `guide.test.cjs` minimum, and the `test_scan_accounting.py` fake engine.
- **Not changed.**
  - I9 signal changes and I10.
  - Holdings-aware Picks (F6).
  - The drawdown-scaled minimum.
  - Insider xfails.
  - SP_ELITE_PRO_Main.py.
  - Port 5500.
  - No push, merge or history rewrite.

# P1–P8 (2026-10-05)

Baseline: Python 1,730 / 5 skipped / 14 xfailed, JS 499. End: Python 1,763 / 5 / 14, JS 509, no new failures. Each fix was reverted once and its tests failed.

| Issue | Root cause | Commits | Tests |
|---|---|---|---|
| P1 progress | 5500 ran pre-I04 code; also the engine reported Research i of n (never n of n), the backfill reset totals, the book only "0 of N"; I04 CSS markers were control characters | b092ee3 | test_progress_final_counts.py, test_scan_terminal_stage.py, JS "P1: stage markers" |
| P2 unreadable filers | JPM 11, MS 9, C 6, GS 5, BAC 5 older EDGAR pages needed; cap was 4 | d767a87 | test_event_heavy_filers.py, JS "P2" |
| P3 passed, not funded | selection (_try_add) only logged its reasons | 2151e08 | test_passed_not_funded.py, JS "P3" |
| P4 risk tiles | denominator was deployed capital; colours were fixed cut-offs | f1c20a8 | test_min_position_label.py (book_limits), JS "P4" |
| P5 price basis, why | scanner and Research read separate daily fetches (251.52 vs 252.02), unlabelled; why was the decision-core trace | ebd07c9, 66504a0 | test_price_basis.py, JS "P5" |
| P6 chase thresholds | GROWTH_ thresholds chosen by scan type; alerts hidden in the audit details | 2a5ffe9 | test_chase_alerts.py (per-stock), JS "P6" ×2 |
| P7 pre-trade review | all lines literals; earnings line contradicted the engine | 59b4e52, 8876238 | test_earnings_size_rule.py, JS "P7" |
| P8 scan menu | "most-watched large caps" label predates the liquidity-ranked selector | b6de21f | test_universe_size_contract.py (basis) |

Evidence: synthetic fixture (real engine and worker: 1 funded, 2 watch, 1 avoid, 5 event-unavailable, MSFT and GOOGL not funded) in both themes at 1280, 1440 and 390: 0 page errors, 0 overflow, axe 0 (`output-fix/p-screens/`). Live Quick on 5801: funnel 5 + 90 + 5 + 0 = 100, event check 100 read and 0 unavailable, progress final (Research 3 of 3), not-funded list = log skips (`output-fix/live-quick-p-summary.json`).

Owner decisions: CHASE.WIDE_THRESHOLD_MIN_VOL (None; 0.35 separates the 2026-10-04 sector medians) and the CHECKLIST values.

## What changed
- **User-visible.**
  - The progress list ends with done, skipped or failed and final counts.
  - The banner names every unreadable ticker.
  - A "Passed the rules, not funded" list, with the capital needed.
  - Risk tiles show percent of capital, coloured by the book's limits.
  - A spot line, the sizing price and plain whys.
  - Chase alerts in their own collapsed section.
  - A generated pre-trade review.
  - True scan-menu labels.
- **Behaviour.**
  - Older EDGAR pages are read up to 16, with a cache.
  - Chase thresholds apply per stock, standard by default; Growth scans are stricter than before.
- **Files.**
  - Engine: `SP_ELITE_PRO.py`, `corporate_events.py`, `sources.py`, `thresholds.py`.
  - Webapp: `app.py`, `app.js`, `elite-pro.js`, `quanta.css`, `index.html`.
  - Tests: `conftest.py`.
- **Not changed.** Net R:R math, the WBD event result, the small-pool score floor, the decision core, funding thresholds, port 5500 and SP_ELITE_PRO_Main.py.
