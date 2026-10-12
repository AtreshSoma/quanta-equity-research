# Independent audit: branch `portfolio-ui-quickfix` at `aeab947` against baseline `4f878f8`

Auditor's position: I did not write this code and did not rely on the logs. Every "verified" below was re-run or re-read by me in this session (2026-10-03). Production code was not edited; nothing was committed. Servers ran on scratch ports 5800 and up and were stopped. All data used is synthetic or a committed fixture. Port 5500 (the owner's server) was not touched.

Scope: Picks remediation (F1, F2, F3, F7, F9, F14, F15), the Activity tab redesign, the bucket time-zone fix (`bdfc4de`) and the fund event-check change (`7f1fd7a`). Picks Phases 4 to 8 (F4, F5, F6, F8, F10, F11, F12, F13) are not done, as expected.

---

## 0. Resolution status (2026-10-03, after the audit; uncommitted working tree)

The findings below were fixed on request, each with a test that fails without the fix. Sections 1 to 7 keep the audit as it was found.

| ID | Status | What changed | Proof |
|---|---|---|---|
| E1 | **fixed** | `terminates_merger` clears only when the merger agreement itself is terminated; conditional wording ("may terminate", "in the event of a termination") and other agreements terminated "in connection with" the deal no longer clear | 2 new tests; case 5b now flagged; mutation "termination on keywords" killed |
| E2 | **fixed** | `EVENTS.LOOKBACK_DAYS` 365 → 730; `EdgarClient` reads up to `EVENTS.FILING_PAGES_MAX` older EDGAR pages when `recent` falls short and reports `coverage_short`; the gate returns `unavailable` instead of `clear` when coverage is short | 5 new tests; case 8 now flagged; 3 mutations killed |
| E3 | **fixed** | document cache bounded by `EVENTS.DOC_CACHE_MAX_BYTES` (64 MB), least recently used dropped, expired entries removed, lock added | new test; mutation killed |
| E4 | **fixed** | a merger-form proxy that never mentions a merger (asset sale) is `unavailable`, not AVOID | new test; mutation killed |
| U1 | **fixed** | Picks renders "Avoid · pending acquisition or delisting (N)" from `result.rejected`, in both the normal and the no-picks state; all text escaped; links kept only for `https://www.sec.gov/` | `tests/unit/event_rejections.test.cjs` (3); `audit/fix-picks-verdict-d1440.png` |
| R1 | **fixed** | the drivers line adds "3 base points, N of 10" and "raw capped at 10"; `RISK_GRADE.MAX_POINTS` now names the cap | new JS test; mutation killed |
| A1 | **fixed** | only trades without their own realized figure are left out; a null Robinhood bucket flags its period and the footer counts the trades inside it; the cross-check still compares like with like | new test "same trades, same total in every covering range"; browser check: YTD, All and 3M all show −$367.30 on 31 trades |
| U3 | **fixed** | below 640 px the table hides Quantity and Cost (→ sell), keeping Date, Stock and the realized or unrealized figure; no sideways scroll | browser check at 412 px: 3 visible columns, no overflow, axe clean |
| U2 | **fixed** (copy) | bold figures stay inline; filter counters read in words, the minimum position quoted from config; the funnel says "Passed every per-name filter: X of N analysed" | new JS test; `audit/fix-picks-small-capital-d1440.png`. Naming the capital the nearest name needs stays with F5/F6/F11 |
| — (found while fixing) | **fixed** | since F7 the guide and the funnel still said grades D and F never reach sizing; they now say grade F is excluded and D is sized at half (banned grades read from config) | guide tests updated and passing |
| A3 | **fixed** | footer says "Times are New York time" instead of today's EDT/EST abbreviation | browser check |
| A4 | **fixed** | an empty chart draws only the zero line | browser check |
| T1 | **fixed** | the bucket test derives its expected counts from the fixture | passes on seeds 1, 7, 42, 31337, 987654 |
| D1 | **fixed** | the Picks results no longer repeat the footer's "not investment advice" sentence | browser check |
| R2 | **test gap fixed; policy open** | new test: a non-fund result carrying a fund verdict keeps the shared decision (mutation 10 now killed). **Whether funds are exempt on My Portfolio while Picks fails closed is the owner's decision** | `test_portfolio_fund_events.py` |
| P1 | **docs fixed; history open** | holdings in `IMPLEMENTATION_LOG.md` and `NEXT_SESSION.md` relabelled H01 to H18; the account cash figure removed from `PICKS_AUDIT.md` and `IMPLEMENTATION_PLAN.md`; memory notes redacted. **Commits `74461f8` and `e56c46e` still hold real trades in local history: rewriting history needs the owner's go-ahead** | `grep` finds no cash figure and no holding symbol on holding lines |
| L1 | not changed | publishing R:R recomputed from rounded levels would make the card differ from the engine's binding reason, which is the defect F2 fixed. The 0.01 gap stays within rounding | — |
| A2 | not changed | "Week" and "Month" follow the broker's own span (same weekday last week), which the cross-check depends on | — |
| G1, D2 | not changed | process and repository housekeeping (commit labelling, screenshots in the repo) for the owner | — |

**Verification after the fixes:** Python 1590 passed, 5 skipped, 19 xfailed, 0 failed, in local time and under `TZ=UTC` (16 new tests). JS 439/439. Playwright on a scratch port: 250 passed, 2 skipped, 0 failed (Activity, My Portfolio, guide, redesign, topbar; four projects). Mutation set extended to 24: all killed. Production ledger, cache, journal and regime history byte-identical before and after. Port 5500 untouched.

## 1. Verdict per area

| Area | Verdict | Why |
|---|---|---|
| F1 corporate-event gate | **fix first** | Two fail-open paths: any later 8-K item 1.02 that mentions the merger agreement clears a live deal (E1), and a deal signed more than 365 days ago reads clear (E2). On Picks the AVOID row is never shown (U1). |
| F2 binding levels | ship | One level set per pick; card R:R equals the binding reason; triggers and re-sizing recompute exactly. |
| F3 one risk grade | ship (copy fix R1 soon) | One function on both paths; points and grades recompute for all 12 golden names. The card's driver line omits the 3 base points and the 10-point cap. |
| F7 one ladder | ship | Golden replay on identical inputs: only the documented MSTR/RIVN holder change and the BRK-A grade change. |
| F9 thresholds, F14 mandate, F15 port | ship | Verified; no decision literals found outside `thresholds.py`. |
| Activity redesign | **fix first (A1), then ship** | Numbers reconcile and are XSS-safe; but a no-figure bucket wipes out more trades the coarser the range, so one set of trades gets different totals in All, YTD and Month (A1). Mobile hides the Realized column (U3). |
| Bucket time-zone fix `bdfc4de` | ship | Code and mutation check confirm it. |
| Fund event-check change `7f1fd7a` | **hold** | It breaks the same-ticker parity between Picks and My Portfolio without a recorded owner decision, and its type guard is untested (R2). |
| Branch publication | **hold** | Real holdings and account figures are still in the docs and a memory note, and two local commits hold real trades (P1). Do not push as is. |

---

## Phase 0: inventory

### 0.1 Commits since `4f878f8` (24), mapped

| # | Commit | Title (short) | Maps to | Notes |
|---|---|---|---|---|
| 1 | `f133457` | pin the clock in two tests | Phase 0 baseline | test-only, as stated |
| 2 | `781eadd` | F15 port tests; Phase 0 baseline | F15 | also adds the plan, log, audit and two golden tools |
| 3 | `9c5b3a8` | one source for thresholds | F9 | 33 files; 7 golden snapshots re-recorded |
| 4 | `9c46931` | remove Aggressive | F14 | |
| 5 | `9aca69b` | corporate-event gate | F1 | |
| 6 | `ff19863` | binding levels | F2 | |
| 7 | `bfc7723` | filer's side, delisted class, consideration | F1 (follow-up) | |
| 8 | `9dddbfc` | guard against name-keyed decisions | labelled F9; really a new guard | also changes production TA text (a named-ticker exemption removed) without a log update in the same commit |
| 9 | `166820d` | check every scanned name | F1 | log marks it "partial" |
| 10 | `ca8f10f` | one risk grade | F3 | 12 golden snapshots re-recorded |
| 11 | `90a92de` | check the scanned universe | F1 | lands after F3 |
| 12 | `523c0b8` | one ladder; checkpoint 3 | F7 | 12 golden snapshots re-recorded |
| 13 | `0bc0d8b` | keep tests off production cache | incident fix (test infra) | also edits `app.py` (env var for the regime-history path) |
| 14 | `74461f8` | My Portfolio: holdings first, catalysts, ledger, gauge | **outside the Picks plan** | holds real trade examples in a test tool; local only |
| 15 | `00dbcd2` | fill-time expectation | test fix for 14 | |
| 16 | `e56c46e` | Robinhood closing trades, one-screen layout | Activity (pre-redesign) | its trade-history test held real values until `b510939` |
| 17 | `ef7f11c` | Activity backend | Activity | |
| 18 | `1f25a50` | Activity front end | Activity | 43 files, 16 PNGs (5.1 MB total), 9 fixtures (0.8 MB) |
| 19 | `dfc64e2` | Activity follow-up | Activity | |
| 20 | `92b7759` | synthetic trades in the preview | privacy clean-up of 14 | |
| 21 | `b510939` | synthetic values in a test | privacy clean-up of 16 | |
| 22 | `bdfc4de` | keep the time zone on buckets | bucket TZ fix | 4,623 / 4,603 lines are regenerated fixtures |
| 23 | `7f1fd7a` | fund event exemption | fund event-check change | TA, portfolio service and guidance, 2 golden snapshots |
| 24 | `aeab947` | next-session notes | docs | `NEXT_SESSION.md` at the repo root |

Not found in history: F4, F5, F6, F8, F10, F11, F12, F13 (expected). F15's 19 strict xfails stay in place on purpose.

### 0.2 Claims matrix

Result key: **verified** (I reproduced it), **partial** (part reproduced, or reproduced only in a weaker form), **unverified** (needs live data or deleted material), **false** (contradicted by evidence).

| ID | Claim | Source | How I checked | Result |
|---|---|---|---|---|
| C1 | Baseline: JS 383/0; Python 1402 pass + 2 date-dependent failures | IMPL log, Checkpoint 1 | not re-run at baseline (historical) | unverified |
| C2 | F9 moved every threshold unchanged; no golden action, score, level or size changed | IMPL log, F9 | my own field-by-field diff of all 12 golden snapshots, baseline vs HEAD | verified (only text, drivers, event fields, BRK-A grade and two holder actions changed, each attributed to F3/F7 in the log) |
| C3 | `/api/thresholds` equals the page's embedded JSON and the JS fixture | IMPL log, F9 | pytest passes; the preview page embeds the JS fixture, which a pytest pins to `as_dict()`; `/api/thresholds` returns `as_dict()` (code read, `app.py`) | verified (the Flask endpoint was not called live) |
| C4 | F14: Aggressive removed; a legacy `aggressive` request is sized as Balanced and says so | IMPL log, F14 | tests pass; the rendered form offers only `conservative`, `balanced` | verified |
| C5 | F1 fails closed with no SEC contact, an EDGAR error, no registrant, or an unreadable document | IMPL log, F1 | code read (`corporate_events.check`, `sources._agent`, `_apply_event_gate`); mutations 5a, 5b, 5c all killed | verified |
| C6 | F1: credit-agreement 8-K, acquirer 8-K and acquirer proxy do not flag; a termination clears | IMPL log, F1 | my own filing texts (Phase 3): credit agreement and acquirer 8-K stay clear, a real termination clears, **but a credit-facility termination that mentions the merger agreement also clears**; mutation 7 survived | partial (E1) |
| C7 | 806-name live sweep: 8 flags, 42 unavailable, all checked by hand | IMPL log, F1 follow-up | needs live EDGAR | unverified |
| C8 | SEC pacing ≤ 10 req/s; measured max 8 per second | IMPL log | code: process-wide lock, 0.12 s minimum gap (`sources.py:79-88`); live rate not re-measured | partial |
| C9 | Event-check caches live in memory only; nothing written to disk | IMPL log | code read: `EdgarClient._cache`, `_DOC_CACHE` are dicts | verified |
| C10 | F2: the published R:R equals the R:R in the binding reason | IMPL log, F2 | test passes; mutation 3 killed; rendered card shows 1.22 in table, card chip and reason | verified |
| C11 | HST triggers $22.48 / $23.67 reproduce R:R 1.50 exactly | IMPL log, F2 | recomputed: $22.48 / $23.67 reproduce 1.500000 | verified (but the displayed levels give R:R 1.22, not the 1.23 in the reason: L1) |
| C12 | AMZN re-sized 4 × $15.49 = $62 → 6 × $10.16 = $61 on the Research stop | IMPL log | $61.96 → $60.96; 6 is the most whole shares within the budget; GOOGL 3 likewise | verified |
| C13 | F3: scanner and Research produce the same grade from the same bars | IMPL log, F3 | one function `grade_from_history` called by both (`SP:10827`, TA `RiskAnalyzer`); test passes; mutation 2 killed | verified |
| C14 | F3: only the tail score moved in the golden master; BRK-A B → A | IMPL log, F3 | golden diff | verified |
| C15 | F7: MSTR and RIVN holder HOLD → TRIM; no other field changed | IMPL log, F7 | golden diff (also `event_check` added) | verified |
| C16 | Checkpoint 3: H16 Hold → Trim is the only action change in the 18-holding replay | IMPL log; NEXT_SESSION | the snapshot was deleted; the replay tool needs live research data; substitute offline golden replay on both trees | unverified |
| C17 | After the incident fix, a full test run leaves production cache and regime history unchanged | IMPL log, incident | size, mtime and sha256 of 5 production files before and after my two full runs | verified |
| C18 | Activity: JS 426, Python 1557 + 3 env failures, Playwright 148 | ACTIVITY log, §4 | JS now 433 (later commits add 7); Python 1531 + 43 (apex_quant) = 1574 pass; Playwright in Phase 2 | partial (counts moved with later commits; no failures) |
| C19 | Activity invariants: KPI = chart = ranking = table for 300+ combinations | ACTIVITY log | test passes; re-run on fixtures regenerated with 5 new seeds; mutation 4 killed | verified |
| C20 | "% on closed cost" fixed; `one_trade` shows +4.05% | ACTIVITY log, defect 2 | recomputed from the fixture: 4 × $135.25, +$21.04 → +4.05%; mutation 6 killed | verified |
| C21 | No share total; one scroll; consistent decimals | ACTIVITY log, defects 5-7 | screenshots and J1 walk: no share total, one page scroll on desktop, 2-dp currency | verified (mobile scrolls the table sideways: U3) |
| C22 | `bdfc4de` keeps the zone on bucket bounds; the page converts to New York | ACTIVITY log, NEXT_SESSION | code read; mutation 13 (truncate to 19 chars again) killed | verified |
| C23 | Continuation: "337 Python and 433 JS tests passed" | NEXT_SESSION, IMPL log | JS 433/0 reproduced; 337 is a subset, not the full suite | partial (the full suite also passes in my runs) |
| C24 | H16: 3 baseline points + 8 / 7 input penalties, capped at 10; F both times | NEXT_SESSION, IMPL log | arithmetic against `RISK_GRADE` bands; inputs need live bars | partial (see Phase 1, F-R1) |
| C25 | "Account-derived examples removed from this log"; "notes contain no account amounts or lots" | ACTIVITY log; IMPL log; NEXT_SESSION | own search of docs and memory notes | **false** (see finding P1) |
| C26 | `7f1fd7a` leaves shared Research/Picks decisions and funding unchanged | IMPL log, continuation | code read; golden diff shows only the new `portfolio_fund_decision` field | verified |
| C27 | The Activity work touched no Picks files | ACTIVITY log | file lists of `e56c46e`, `ef7f11c`, `1f25a50`, `dfc64e2` | verified |
| C28 | 59 `data-threshold` spans filled at boot | IMPL log, F9 | counted in the rendered page | partial (58 spans, none empty) |
| C29 | The signed-in server on 5500 was not restarted | ACTIVITY log; NEXT_SESSION | the memory note says it was restarted at 20:34 on 10-02; the listener now on 5500 started 2026-10-03 17:57 | contradictory sources; not attributable |
| C30 | One commit per finding | plan | commit map above | partial (F1 is 4 commits; a guard is labelled F9; an unrelated My Portfolio commit sits in the series) |

---

## Phase 1: developer review

### 1.1 Hard-coding

- My grep over 123 non-test, non-fixture source files for the audit's tickers, company names and deal figures (`LNTH`, `Lantheus`, `Curium`, `102.50`, `HST`, `22.48`, `23.67`, the account cash figure, and the 8 live targets and false-positive filers) finds them **only inside universe lists** (`SP_ELITE_PRO.py` sector lists) and one pre-existing comment (`TradingAnalysis_ELITE.py:8016`, from June). No decision path compares a ticker with a literal.
- Added lines since baseline that compare against a numeric literal: none in decision code outside `engine/thresholds.py` (the remaining hits are chart scaling and a tooltip position).
- Thresholds: the page embeds `thresholds.as_dict()` and `/api/thresholds` returns it; the JS fixture is checked against the config by a pytest. Live comparison in Phase 4.
- Activity: no year, ticker, cap or zone literal in `mp-activity.js` / `mp-activity-model.js`; every limit comes from `core/portfolio/activity_config.py`.

### 1.2 Event gate (`engine/corporate_events.py`, `portfolio/sources.py`, `SP_ELITE_PRO._apply_event_gate`)

Works as designed:
- Any ticker: nothing is keyed to a name; the registrant's own EDGAR name and defined terms decide the filer's side (`filer_side`, `corporate_events.py:265`).
- Fail closed: no contact raises `SecContactMissing` (`sources.py:91-97`) which `recent_filings` turns into `status: error` → `unavailable`; no CIK → `unavailable`; an unreadable deciding document → `unavailable`; a candidate with no result → `unavailable` (`SP:11365`); the whole checker raising → all `unavailable` (`SP:11340`). The ladder holds unavailable at Watch (`ladder.py:76-80`).
- Throttle: one process-wide lock, 0.12 s minimum gap (`sources.py:79-88`). The background warm-up and the gate share it.
- User-Agent: `QUANTA_SEC_USER_AGENT` verbatim, else `QUANTA-Research/1.0 (<contact>)`; event queries require it. Documents are fetched from `https://www.sec.gov/` only.
- Errors are not cached; `no_cik` and good answers are cached 30 min; the ticker map 24 h.

Defects and risks found (detail in the findings table):
- **E1 Termination clears on keywords.** `terminates_merger` (`:333`) returns true for any text containing "terminat…" and "merger agreement". Any later 8-K with item 1.02 and no 1.01 that mentions the merger agreement clears a pending acquisition, e.g. a target terminating its credit facility or rights plan "in connection with the Merger Agreement". Clearing makes the name fundable again (fail open). Mutation 7 (drop the text check entirely) **survived** the suite.
- **E2 Look-back and EDGAR "recent" window.** `LOOKBACK_DAYS = 365` and `_parse_recent` reads only `filings.recent`. A deal signed more than a year ago that is still pending (long antitrust reviews), or a heavy filer whose `recent` block covers less than a year, returns `clear`. That is fail open and not detected.
- **E3 Unbounded document cache.** `_DOC_CACHE` keeps up to 3 MB per document for 24 h with no size bound and no eviction of expired entries (`:518-539`). In a long-lived server that has swept the universe several times this grows without limit.
- **E4 DEFM14A for an asset sale** is read as the filer being acquired (no buyer evidence, not mini-tender). That fails safe (AVOID), but it is a false positive.

### 1.3 Levels and R:R (`engine/levels.py`, `SP:10768-10825`)

- One published set per pick: `binding_levels`; the scanner set moves to `scanner_levels` with a "not binding" label. Verified in code and by mutation 3.
- Trigger formulas are algebraically correct (`entry_needed = (T + mS)/((1+k)(1+m))`, `target_needed = E + c + m(E − S + c)`); recomputed in Phase 3.
- The published R:R is the engine's own `trade_setup.risk_reward_ratio`, not recomputed from the published levels, while the triggers use `cost_frac` = 11 bps. If the engine's R:R and `net_rr(entry, stop, target, 11 bps)` differ, the remedy's prices would not reproduce the published R:R. Checked in Phase 3.
- Sizing on the binding stop: `alloc × scan_stop_pct / research_stop_pct`, then the position cap. Correct for a constant dollar risk. It calls `apply_allocation` with the re-sized amount; whether the $100 minimum is re-checked there is F11 territory (not done).

### 1.4 Risk grade and ladder

- One grade path: `grade_from_history` (`risk_grade.py:150`) is called by the scanner (`SP:10827`) and the Research engine; the legacy 0-100 fallback is gone. Drivers are published on both Decisions.
- One ladder: `engine/ladder.decide` is used by the scanner and Research Decisions; My Portfolio reads the Research Decision.
- **R1 Drivers do not add up to the grade on the card.** The card prints only the factors that add points (`app.js:6174-6185`). It never shows the 3 baseline points or the 10-point cap, so "Volatility (+3) · Beta (+2) · Drawdown (+2)" (7) sits next to grade F, whose band starts at 9. For H16 this is exactly the confusion in the earlier report: penalties 8 (baseline) and 7 (HEAD) are **raw totals 11 and 10, published as 10 and 10**. Band check: vol +3 needs > 50 %, beta +2 needs > 1.5, drawdown +2 needs worse than −40 %, and the baseline's extra point needs a tail score above 50; HEAD's winsorized tail must be ≤ 50. Both totals exceed the D ceiling of 8, so F in both versions is consistent. H16's actual inputs need live bars: not verified.
- **R2 Parity break by design (`7f1fd7a`).** My Portfolio now shows a typed fund with "Corporate-event check: not applicable" and the decision computed without the event gate, while Picks still fails closed on the same ticker. The log listed the ETF policy as an open owner decision; the continuation notes record it as "decided" without an owner reference. Mutation 10 (exemption for any instrument type) **survived**: no test feeds a non-fund result that carries a not-applicable candidate.

### 1.5 Activity

- XSS: every broker-supplied string (symbol, dates, term, labels) passes through `esc` before insertion; numbers go through formatters. URL state is parsed with a whitelist (`^[A-Z0-9.\-]{1,12}$` for symbols, `YYYY-MM-DD` for brushed ranges, known keys for range and view; `mp-activity-model.js:478-492`). No injection path found.
- Reconciliation: KPI total, chart buckets, ranking and table are sums over one filtered row set (`build`, `:275-399`). Held on 5 extra seeds; mutation 4 killed.
- Time zones: zoned instants convert to New York wall time; zoneless broker times are read as UTC; zoneless bucket bounds as market time; calendar arithmetic runs at noon UTC.
- Window lengths: "Week" is today minus 7 days inclusive (8 calendar days), "Month" is a month plus a day (A2, low).
- Performance: 42 model builds over the 1,000-trade fixture took 85 ms (2 ms per build).

### 1.6 Privacy

- **P1** The logs say account-derived examples were removed. Still present:
  - `IMPLEMENTATION_LOG.md` lists the owner's 18 real holdings by symbol with action, score and grade (two tables, 39 rows).
  - `PICKS_AUDIT.md` repeats the account cash figure from the owner's screenshot 8 times (committed in `781eadd`).
  - Memory note `quanta-activity-redesign.md` states one real trade amount tied to a ticker.
  - Local history: `74461f8` (preview tool, 9 real-looking tickers with amounts) and `e56c46e` (3 real values in a test) hold account data. Neither is on any remote (`git branch -r --contains 74461f8` is empty), so pushing this branch as-is would publish them.
- The personal email appears in `core/Alpha_Picks_Engine.py:989-990`, unchanged since before the baseline (pre-existing, not this branch).
- No account numbers found in docs, fixtures or notes.

### 1.7 Other

- Dead code: none introduced that I found; `regime.recommended_cash_pct` (never read) is pre-existing and logged.
- `9dddbfc` changed TA warning logic (a six-name exemption from HIGH LEVERAGE replaced by sector and coverage) under an F9 label.

---

## Phase 2: tester

### 2.1 Suites

| Suite | Result (this audit) | Logged |
|---|---|---|
| Python `tests/`, local time (America/New_York) | **1531 passed, 5 skipped, 19 xfailed, 0 failed** (96 s) | — |
| Python `tests/`, `TZ=UTC` | **1531 passed, 5 skipped, 19 xfailed, 0 failed** | — |
| Python `core/apex_quant/tests` (also in `testpaths`) | 43 passed | — |
| Python total | 1574 passed | 1557 at `1f25a50`; 337 subset at the continuation |
| JS `npm test` | **433 passed, 0 failed** | 433 |
| Playwright | see 2.4 | 148 (Activity and My Portfolio specs only) |

Production files (ledger, both cache files, journal, regime history): byte-identical before and after both runs.

### 2.2 Test integrity since `4f878f8`

- New skips/xfails: the 19 strict xfails in the two ported F15 files (documented, strict, each with a reason) and one JS skip guarded on the engine source being present. No `only`, no loosened tolerance found.
- Removed assertions in pre-existing tests: the old Activity assertions in `my_portfolio_workspace.test.cjs` and `my_portfolio.spec.ts` (replaced by `activity_model` / `activity_view` tests, which are stronger); the review-engine mode tests now assert the one configured mode (matches F7); `test_dell_display` now expects 2 dp. None hides a regression.
- Regenerated fixtures: golden snapshots were re-recorded in 4 commits. My field-level diff of all 12 shows no change in final action, levels, size or score, only the documented grade and holder changes plus added fields.
- Fragile test: `activity_model.test.cjs:144` asserts that exactly 2 trades fall in the no-figure bucket; with seed 7 the generator puts 3 there and the test fails although the model is right (T1, low).

### 2.3 Mutation spot checks (scratch copy, deleted afterwards)

| # | Mutation | Result |
|---|---|---|
| 1 | Event gate never flags an acquisition | killed |
| 2 | Tail score without winsorization | killed |
| 3 | R:R stays the scanner's (no Research binding) | killed |
| 4 | Activity KPI total drops one row (reconciliation) | killed (4 tests) |
| 5a | Scanner funds an "unavailable" check | killed |
| 5b | No SEC contact still queries EDGAR | killed |
| 5c | Ladder ignores an unavailable check | killed |
| 6 | Percent printed from a fraction (no ×100) | killed |
| 7 | Any 8-K item 1.02 clears a deal (termination text ignored) | **survived** |
| 8 | Acquirer's own 8-K counts as target | killed |
| 9 | No re-size on the binding stop | killed |
| 10 | Fund exemption for any instrument type | **survived** |
| 11 | STRONG_BUY never folds | killed |
| 12 | R:R trigger formula ignores cost | killed |
| 13 | Bucket bounds truncated to 19 characters again | killed |

### 2.4 Playwright

All runs used `BASE_URL` on the synthetic preview server (port 5801); no spec hard-codes a port.

| Specs | Projects | Result |
|---|---|---|
| `activity.spec.ts`, `my_portfolio.spec.ts` | desktop-dark, desktop-light, tablet, mobile | **148 passed, 0 failed** (5.2 min), matching the log |
| `guide`, `redesign`, `topbar`, `monitor` | desktop-dark | 25 passed, 0 failed |
| `smoke` | desktop-dark | 5 passed, 4 failed (dashboard, portfolio builder, deep-dive ×2) |
| `research_contract` | desktop-dark | 9 failed (every test waits for a live analysis job) |
| the same six specs | desktop-light | partial run, same failure pattern; stopped at 85/180 |

The 13 failures all come from specs that need the live Flask app and market data (analysis jobs, live dashboard). The synthetic server cannot answer them, so I record them as **not verified**, not as regressions. Tablet and mobile for these six specs were not run, because each live-dependent test waits out an 85-second timeout.

### 2.5 Activity invariants on fresh fixtures

Fixtures regenerated through the real server code in the scratch copy with seeds 1, 7, 42, 31337 and 987654. The reconciliation invariant (every view × range × filter) passed for all five; the view-level sum tests passed for all five. The only failure is T1 above.

---

## Phase 3: validation analyst

Every number below was recomputed by my own scripts, without importing the code under test (except the event-gate cases, which must call the gate).

### 3.1 R:R, triggers, sizing and risk points (12 golden names)

Formula used: `rr = (T − E − c)/(E − S + c)`, `c = 0.0011·E` (5 bps spread + 2 × 3 bps slippage), `T = E + (target_base − E)·63/252`.

| Check | Result |
|---|---|
| R:R from each name's own published `entry`, `stop`, `target_base` vs published `risk_reward_ratio` | 7 of 7 names with a long reward match to 4 dp (BRK-A, BRK-B, KO, MSTR, NVDA, RELIANCE.NS, RIVN); the 5 without reward publish 0 |
| `entry_needed` and `target_needed` at 1.50, substituted back | exactly 1.500000 for all 7 |
| HST (audit levels 22.56 / 21.86 / 23.47) | triggers $22.48 / $23.67 confirmed; R:R from the displayed levels is **1.22**, the reason quoted 1.23. The 2-dp rounding of the levels allows 1.19 to 1.25, so both are "right", but a reader recomputing from the page gets 1.22 (L1) |
| F2 re-size (AMZN, GOOGL) | budget $61.96 → $60.96 and $49.52 → $40.53; whole shares round down as stated |
| Planned loss on the rendered J3 card | 9 shares × ($50 − $46) = $36, as shown |
| Risk points = min(10, 3 + Σ drivers) and the grade band | 12 of 12 match. No golden name exceeds 10 raw points, so the cap path (H16: raw 11 → 10) is not covered by any golden test |

### 3.2 Activity totals, gross profit and loss, profit factor, return on closed cost

Independent calculation from the raw fixture records compared with the model's KPIs, for All, Month, YTD and Week:

| Scenario | Result |
|---|---|
| `one_trade`, `many` (1,000 trades), `all_losses`, `all_gains`, `short_window`, `capped` | **all match** (count, realized, gross profit, gross loss, profit factor, return on closed cost). `one_trade` = +4.05%. Rendered page (All, 1,000 trades): KPI = ranking total = table total = gross profit + gross loss = +$20,396.57; profit factor 1.45 |
| `no_figure_bucket` | the model follows its stated rule exactly (my count of trades inside null buckets: All 13, YTD 6, Month 2, equal to the model's exclusions). **The rule itself makes totals depend on the range**: the same 31 trades are counted 26 times under YTD and 19 under All, because Robinhood's All series uses month buckets and one null month removes every trade in it, including 12 that carry their own realized figure (A1) |
| Cross-check vs Robinhood's total | matches where it should; `short_window` and `capped` show the named difference, as designed |

### 3.3 Replay at baseline and HEAD

- `portfolio_decision_table.py` needs the webapp's live research function (market data) and has no offline mode. **Not run.**
- Substitute: the golden-master harness replays 12 recorded tickers offline. I exported `4f878f8` and ran its own golden test: 27/27 pass; HEAD: 27/27 pass. Each tree reproduces its own snapshots from identical recorded inputs, so the snapshot diff is the code change:

| Ticker | Change | Cause |
|---|---|---|
| BRK-A | risk grade B → A (points 5 → 3) | F3 tail score on winsorized returns |
| BRK-B, NVDA and others | fewer tail points, grade unchanged | F3 |
| MSTR, RIVN | holder action HOLD → TRIM | F7: grade F gets the trim review at any score (the H16 fix) |
| RELIANCE.NS | first blocker becomes `EVENT_CHECK_NOT_COVERED` | F7 event gate; India fails closed (owner decision) |
| SPY, TLT | new `portfolio_fund_decision` field (WATCHLIST / WATCH) | `7f1fd7a` |
| all 12 | final action, levels, size, score | **unchanged** |

### 3.4 Event gate on filing structures written for this audit

Six invented companies and tickers, texts written by me (not the repo's fixtures), fed through a fake EDGAR client into `corporate_events.check`:

| Case | Result | Correct? |
|---|---|---|
| 1 Target proxy (DEFM14A), cash + non-transferable CVR | PENDING_ACQUISITION, $48.75, CVR non-transferable, spread 1.77% | yes |
| 2 Target 8-K 1.01, stock consideration (exchange ratio) | PENDING_ACQUISITION, stock, no price | yes |
| 3 Target SC 14D9 | PENDING_ACQUISITION, $22.10 | yes |
| 4 Credit agreement 8-K 1.01 | clear | yes |
| 5a Proxy, then 8-K 1.02 terminating the merger agreement | clear | yes |
| 5b Proxy, then 8-K 1.02 terminating a **credit facility** "in connection with the pending merger contemplated by the Merger Agreement" | **clear** | **no** (E1, fail open) |
| 6 Acquirer's own 8-K (target defined as "the Company") | clear | yes |
| 6b The same text filed by the target | PENDING_ACQUISITION, $61.00 | yes |
| 7a Form 25 for senior notes | clear | yes |
| 7b Form 25 for common stock | DELISTING | yes |
| 8 Deal proxy filed 400 days ago, no termination | **clear** | **no** (E2, fail open) |
| 9 EDGAR error | unavailable | yes |
| 10 `.NS` listing | EVENT_CHECK_NOT_COVERED | yes |

It works for unrelated tickers: nothing is keyed to a name.

---

## Phase 4: end user

Servers: the repo's synthetic preview (`tests/tools/portfolio-preview.cjs`) on 5801 (standard book), 5802 (`ACTIVITY=many`), 5803 (`ACTIVITY=empty`), 5804 (`FAIL=perf,events,records`). Picks results were rendered with `renderPicks()` from synthetic payloads shaped like the server's (one funded pick, one R:R watch row, one event rejection; and a no-picks small-capital case using `explain_empty_scan`'s exact wording). No live data. Screenshots: `docs/audit/` (34 files).

### J1 Activity (1,000 trades, 1440 dark)
- Range chips update `aria-pressed` and the URL hash; the stock filter adds `s=` and chips; a bar click filters and a second click clears; keyboard brush (Shift+Arrow, Enter) and pointer brush both produce a custom `r=from~to`; the view switch drives every section; reload restores range and view from the URL; Clear all removes the chips.
- No console errors and no failed requests. axe (WCAG 2.1 AA) on `#mpa-root`: 0 violations in every view and viewport. No horizontal page overflow at any width.
- Tab walk: every stop has a visible focus ring and an accessible name; the chart bars are one tab stop with names like "Week of Sep 28, 2026: gain plus $33.18, 2 closing trades".

### J2 My Portfolio tabs (1440 dark, 1280 light, mobile)
- All six tabs load and select, with no console errors or failed requests on the standard book. axe: 0 violations on every tab.
- Failing endpoints: only the injected 502s; Activity says "Activity is unavailable: The data source did not answer. Try again", and other sections stay.
- Empty account: named empty states everywhere, but the empty chart draws a $0 to $1.00 axis no data supports (A4).

### J3 Picks
- Verdict and funnel: the verdict card, research-book table, card and chip all show the same R:R (1.71 funded, 1.22 watch), equal to the reason text. The engine remedy ("Research R:R 1.22 needs 1.50: … $22.48 … $23.67") shows when the server's `reason_codes` are present. The funnel is only inside the collapsed "Scan method" audit (F5, not done).
- **The acquisition-flagged name (AVOID, pending acquisition) is not visible anywhere**: the page never reads `result.rejected` (`app.js` reads only `deep_confirmation.rejected`, `:5963`). U1.
- Small capital (no picks): the explanation is the engine's text verbatim, raw keys included (`score_below_min=85; …; zero_allocation=37`). It does not name the $100 minimum or the capital the nearest name needs (F5/F6/F11, not done). The funnel reads "Survived filters 0 — Passed per-name analysis: 0 of 250" directly below "Analyzed 250 of 250 names". The hero sentence breaks onto separate lines around its bold numbers (U2).

---

## Phase 5: agentic-work audit

| Topic | Evidence | Judgement |
|---|---|---|
| Overclaims | "Account-derived examples removed" (C25) is false; "Validation: 337 Python and 433 JS tests" presents a subset as validation (C23); "59 spans" is 58 (C28); "repeated HEAD results match" for the 18-holding replay cannot be checked (snapshot deleted) | 1 false, 2 partial, 1 unverifiable |
| Work outside the plan | `74461f8` (My Portfolio: holdings first, catalysts, ledger, gauge) and part of `e56c46e` (Overview layout) are not Picks findings; `9dddbfc` changes Research warning logic under an F9 label | disclosed only in the memory note, not in the plan |
| Side effects | The test-suite incident is reported well. The 5500 restart: the Activity log and NEXT_SESSION say it was not restarted; the memory note says it was restarted at 20:34; it now runs from 2026-10-03 17:57 | contradiction left unresolved |
| Owner decisions | Research levels govern: done. Acquisitions out of scope (AVOID; holders sell below the spread minimum): done in the engine, **not visible on Picks** (U1). Capital from account cash: not done (F6, expected). STRONG_BUY folds: done and tested. Aggressive removed: done. Grade D at half size: done (`test_grade_d_half_size_book`). Fail closed: done for the listed cases, but **two fail-open gaps** (E1, E2), and My Portfolio now exempts typed funds without a recorded owner decision (R2) | 2 deviations |
| One commit per finding | F1 spans 4 commits; a guard is labelled F9; `0bc0d8b` is titled `test:` but edits `app.py` | partial |
| Commit messages | mostly accurate; `7f1fd7a` mixes a behaviour change with docs under "verify continuation" | minor |
| Secrets, binaries | no SEC contact or token in the repo; 16 PNGs (5.1 MB) committed under docs | acceptable |
| Docs vs code | match on levels, ladder, thresholds; the logs describe the rejected list as "published", but users cannot see it | partial |
| Acceptance checks never run at HEAD | live deal-target AVOID scan after F7 (last run at `90a92de`); the 18-holding replay after `7f1fd7a`; `test_picks_portfolio_parity` (Phase 8); `test_watch_only_scan_explained`, `test_max_positions_limits_funded_only`, `test_small_capital_explanation`, `test_gate7_after_backfill`, `test_universe_selection_reported`, `test_evidence_statuses_on_picks`, `test_header_session_aware` (Phases 4 to 8, expected) | |

---

## 3. Findings

| ID | Sev | Area | Evidence | Reproduction | Proposed fix (not applied) |
|---|---|---|---|---|---|
| E1 | **high** | F1 event gate | `engine/corporate_events.py:333-335`, `:482-490`; Phase 3 case 5b; mutation 7 survived | A proxy for a pending deal, then an 8-K item 1.02 that terminates a credit facility and mentions "the Merger Agreement" → `clear`, so the target becomes fundable | Clear only when the text says the merger agreement itself was terminated; otherwise return `unavailable`. Add case 5b as a test |
| E2 | **high** | F1 event gate | `thresholds.EVENTS.LOOKBACK_DAYS = 365`; `sources._parse_recent` reads only `filings.recent`; case 8 | A merger proxy filed 400 days ago with no termination or delisting → `clear` | Look back until a deal is closed, terminated or delisted (or 24 months); page `filings.files` when `recent` does not cover the window; return `unavailable` when coverage is short |
| U1 | **high** | F1 on Picks | `static/app.js:5963` reads only `deep_confirmation.rejected`; `app.py:5534` sends `rejected`; `audit/j3-picks-verdict-d1440-dark.png` | Render a payload with one `rejected` AVOID row: it appears nowhere | Add the "Rejected, with reason" list (audit 8.3) |
| P1 | **high** | privacy | `IMPLEMENTATION_LOG.md` (two tables of 18 real holdings with scores and grades); `PICKS_AUDIT.md` (account cash ×8); memory note `quanta-activity-redesign.md` (one ticker with a real amount); local commits `74461f8`, `e56c46e` (real trades; on no remote) | counts in Phase 1.6 | Replace holdings with synthetic labels, redact the cash figure, edit the memory note; decide a clean-history approach before any push |
| A1 | medium | Activity | `mp-activity-model.js:255-266`, `:308-316`; Phase 3.2 | `no_figure_bucket`: the same trades count 26 under YTD and 19 under All; trades with their own realized figure are dropped | Exclude only trades without their own figure; let a null bucket flag the period, not drop trades |
| R2 | medium | fund event check | `portfolio/fund_events.py`; `TradingAnalysis_ELITE.py:7531-7545`; `portfolio/service.py:80-86`; mutation 10 survived; the IMPL log's Unresolved list still calls the ETF policy a decision needed | A typed ETF holding reads "event check not applicable" on My Portfolio while Picks fails closed on the same ticker | Record the owner's decision; test that a non-fund result carrying a not-applicable candidate keeps the shared decision; say on the page why the two differ |
| R1 | medium | F3 card | `static/app.js:6174-6185` | A grade-F name with drivers +3 +2 +2 shows 7 points beside F (band starts at 9); H16's raw 11 is published as 10 | Print "3 base + drivers = N (capped at 10)" |
| E3 | medium | performance | `corporate_events.py:516-539` | Repeated universe sweeps keep every document (up to 3 MB) in memory; expired entries are never removed | LRU cache with a byte cap; drop expired entries |
| U2 | medium | Picks empty state | `audit/j3-picks-small-capital-d1440-dark.png` | No-picks payload with the engine's explanation | Fix the inline layout; translate filter keys; align the funnel with the analysed count; with F5/F6/F11, name the minimum and the capital needed |
| U3 | medium | Activity mobile | `audit/activity-all-mobile-dark.png` | 412 px wide: the Realized column is off-screen | Show the realized figure in the first columns on narrow screens, or stack rows |
| G1 | low | process | commit map (0.1) | — | One finding per commit; `fix:` for production edits |
| L1 | low | F2 | Phase 3.1 | HST levels on the page give 1.22; the reason says 1.23 | Compute the published R:R from the rounded published levels |
| E4 | low | F1 | `corporate_events.py:454-465` | A DEFM14A for an asset sale flags the seller as acquired (fails safe) | Require target wording in the proxy, as for 8-Ks |
| A2 | low | Activity | `activity_config.py` ranges | "Week" spans 8 calendar days, "Month" a month and a day | Make `back` exclusive or label the window |
| A3 | low | Activity | `mp-activity.js:429` (`tzShort(new Date(), …)`) | Trades from winter months are labelled EDT | Say "New York time" |
| A4 | low | Activity | `audit/j2-empty-activity-activity-1440-dark.png` | An empty account draws a $0–$1.00 axis | Hide the axis without data |
| T1 | low | tests | `tests/unit/activity_model.test.cjs:144` | Regenerate fixtures with seed 7: fails although the model is right | Assert against the fixture's own bucket membership |
| D1 | low | UX | Picks and My Portfolio screenshots | Four disclaimers on Picks, two per My Portfolio page | One disclaimer per page |
| D2 | low | repo | 16 PNGs, 5.1 MB in `docs/activity-redesign` | — | Keep screenshots out of the repo |

## 4. UX scorecard (1 = poor, 5 = excellent)

| Screen | Score | Main reasons (Nielsen heuristic) | Screenshot (`docs/audit/`) |
|---|---|---|---|
| Activity, 1,000 trades, desktop | 4 | Same total in four places (H4 consistency), clear coverage line (H1), full keyboard and labels; empty right column beside the long table; "Times are EDT" for winter trades (A3) | `activity-all-d1440-dark.png`, `activity-all-d1280-light.png` |
| Activity, mobile | 3 | Stacks well, no page overflow; the Realized column is off-screen (H6, U3) | `activity-all-mobile-dark.png` |
| Activity, Purchases and Open lots | 4 | Same structure per view (H4) | `j1-activity-buys-1440-dark.png`, `j1-activity-lots-1440-dark.png` |
| Activity, empty | 3 | Honest empty text; invented axis; two near-empty cards (46%) | `j2-empty-activity-activity-1440-dark.png` |
| Activity, error | 4 | Plain message plus retry (H9) | `j2-failing-endpoints-activity-1440-dark.png` |
| Overview | 4 | Dense and consistent (+$25,874 matches "What drove return"); Allocation card about a third empty; disclaimer twice | `j2-standard-overview-1440-dark.png` |
| Guidance | 4 | Each action with the rule that fired; "Unproven model" stated once | `j2-standard-guidance-1280-light.png` |
| Review | 3 | Very long; jargon ("Beta-implied", "one-factor estimate", a raw tool name in a status line); realized shown as $2,314.55 and $2,315 on one screen | `j2-standard-review-1440-dark.png` |
| Holdings, News | 4 | Load clean, axe clean | `j2-standard-holdings-1440-dark.png`, `j2-standard-news-1440-dark.png` |
| Picks verdict | 3 | R:R consistent, drivers shown; AVOID row missing (H1); four disclaimers; "Evidence Unknown"; "Scan completed time unavailable"; 100% concentration in red for a one-name book | `j3-picks-verdict-d1440-dark.png`, `j3-picks-verdict-d1280-light.png`, `j3-picks-verdict-mobile-dark.png` |
| Picks no picks (small capital) | 2 | Broken sentence layout, raw filter keys (H2), contradictory funnel, no actionable remedy (H9) | `j3-picks-small-capital-d1440-dark.png` |

Whitespace rule (no card more than 15% empty): failed by the Overview allocation panel (34%), the standard-book Activity panel (30%), Review (23%), News (20%) and the empty Activity ranking (46%), measured as empty height below the last content.

## 5. Test gaps and tests to add

- `test_termination_of_other_agreement_does_not_clear_a_deal` (E1) and `test_deal_older_than_lookback_is_not_clear` (E2).
- `test_recent_block_shorter_than_lookback_is_unavailable` (EDGAR `recent` truncation).
- JS: rejected AVOID rows are rendered on Picks (U1).
- `test_non_fund_result_keeps_shared_decision` (R2; closes the mutation-10 gap).
- A risk-points case above 10 raw points, plus a golden name that reaches it (R1).
- Activity: the same trades give the same total in every preset range that covers them (A1); a seed-independent bucket assertion (T1); the mobile table shows the realized figure (U3).
- Live acceptance at HEAD (owner to run): one pinned-data scan with a known deal target after F7, and the 18-holding replay after `7f1fd7a`.

## 6. Not verified, and why

| Item | Why |
|---|---|
| 806-name live sweep counts, SEC request rate | needs live EDGAR |
| H16's actual risk inputs | needs live price history; only arithmetic and band edges checked |
| Checkpoint 3 (18 holdings) and its HEAD repeat | account snapshot deleted; live data |
| `portfolio_decision_table.py` replay | needs the live research function; offline golden replay used instead |
| `/api/thresholds` over HTTP | the Flask app was not started (it would load market data); checked in code and through the fixture pinned to `as_dict()` |
| `total_rate_of_return` unit | needs account data; the page no longer uses it |
| Who restarted port 5500 | sources contradict |
| Playwright specs that call live analysis | the synthetic server cannot answer them; see 2.4 |

**Production files during the audit.** Directly after both full pytest runs, the ledger, both cache files, the journal and the regime history were byte-identical to the start of the audit. Later, at 19:46 on 2026-10-03, `ui/stock-analysis/data/cache/yf_cache.json` was rewritten and `.regime_history.json` was re-saved with identical content (same sha256, new mtime). At that minute none of my processes ran Python: only Node preview servers and Playwright against port 5801 were running. The cache file had already been rewritten at 18:00, before any test of mine. The likely writer is the owner's server on port 5500 (running since 17:57, PID unchanged), but I cannot prove that.

## 7. Recommended order of next steps

1. Redact P1 from the docs and the memory note now; agree a clean-history plan before any push.
2. Fix E1 and E2 (fail open in the event gate) with the two tests above.
3. Render the rejected list on Picks (U1), so a deal target reads AVOID.
4. Get the owner's decision on fund handling and parity (R2); add the guard test.
5. Fix A1 (range-dependent Activity totals), then U3 (mobile column).
6. Copy fixes R1, U2, L1, A3, A4, D1; bound the document cache (E3).
7. Then resume Picks Phase 4 (F4, F5, F10, F11, F12), which also fixes the small-capital explanation.
