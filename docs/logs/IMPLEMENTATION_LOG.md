# QUANTA Elite Picks remediation: implementation log

Plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Audit: [PICKS_AUDIT.md](../audits/PICKS_AUDIT.md).
Branch `picks-remediation`, created from `audit-remediation @ 4f878f8`.

This file is the resume point. Every finding commit updates its section here.

## Status

| Phase | Finding | Commit | State |
|---|---|---|---|
| 0 | baseline clock fix (test-only) | `f133457` | done |
| 0 | F15 test port, golden tools, plan, log | `781eadd` | done |
| 1 | F9 thresholds | `9c5b3a8` | done |
| 1 | F14 mandate | `9c46931` | done |
| 2 | F1 corporate events | `9aca69b` | done |
| 2 | F1 follow-up: live false positives fixed, India reason | `bfc7723` | done; 806-name live sweep |
| 2 | Guard: no name / deal literals | `9dddbfc` | done |
| 2 | F1: check every scanned name | `166820d` | partial (rows only) |
| 2 | F1: check the scanned universe | `90a92de` | done |
| 2 | F2 binding levels | `ff19863` | done; golden re-run in progress |
| 3 | F3 one risk grade | `ca8f10f` | done |
| 3 | F7 one ladder | `523c0b8` | done; **checkpoint 3: stopped for review** |
| 3 | test: isolate production cache and regime history | *(this commit)* | done (incident, see below) |
| 4 | F4, F5, F10, F11, F12 | — | |
| 5 | F6 | — | |
| 6 | F13 | — | |
| 7 | F8 | — | |
| 8 | parity sweep | — | |

## Run setup used for every run

- **Env:** `QUANTA_TRACKING_DISABLED=1`, plus `QUANTA_LEDGER_PATH`, `QUANTA_CACHE_DIR` and `QUANTA_JOURNAL_PATH`, all pointing at the session scratchpad.
- **Cache:** `QUANTA_CACHE_DIR` is a copy of the warm production cache (`ui/stock-analysis/data/cache`) made at 08:31 on 2026-10-02.
- **Working directory:** scratch, because the engine logs to `./logs`.
- **Baseline code:** the tree at `4f878f8`, exported with `git archive` to `<scratch>/baseline`. A `git worktree` could not be used, because Windows path length broke `git worktree add` under the scratch path.
- **Tools:**
  - `ui/stock-analysis/webapp/tests/tools/golden_scan.py` runs `app._run_stock_picks` in-process.
  - `ui/stock-analysis/webapp/tests/tools/portfolio_decision_table.py` replays an account snapshot through the real `PortfolioService`.
  - Both refuse to run unless tracking is disabled and every path points at scratch.

## Phase 0: verification and baseline

### Findings re-verified against current code (2026-10-02)

All 15 still apply. Where the code has moved since the audit, it is recorded under checkpoint 2 below: adapted, no stop needed. The full table is in the plan, under "Phase 0 verification".

Checkpoint 2 notes (audit claims that differ from the current code):

| # | Audit said | Current code | Effect on the plan |
|---|---|---|---|
| 1 | `app.js:1075` duplicates "1.5:1" as text | `app.js:1075` is the constant `GUIDE_MIN_RR = 1.5`. The text "1.5:1" is in `index.html:994/1123/1373`, `my-portfolio.js:856`, `TA:7477/11517` and `rich_display.py:167/197` | F9 covers all of them |
| 2 | `app.py:5279` truncation after deep confirmation | Deep confirm and up to two backfill rounds run **inside** `get_top_recommendations` (`SP:11829-11878`). The engine can return more rows than `max_positions`, and `app.py:5279` cuts them afterwards | F5 limits funded rows in `app.py` and keeps every watch row |
| 3 | EDGAR via `EdgarClient` is ready to use | Its User-Agent mentions `QUANTA_SEC_CONTACT`, but nothing reads it. TA's `sec_edgar_parser` scores 8-K 2.01/5.01 M&A as a **positive** catalyst, and fast mode (used by deep confirm) skips catalysts entirely | F1 reads the env var and fails closed. M&A catalyst points are neutralised for flagged names |
| 4 | Funnel at `elite-pro.js:777-806` | `funnelCard` is now at `elite-pro.js:777-864` | location only |

### Checkpoint 1: baseline suites (before any change)

| Suite | Result |
|---|---|
| JS, `npm test` (webapp) | **383 passed, 0 failed** |
| Python, local time (US/Eastern), with `QUANTA_TRACKING_DISABLED=1` | 1399 passed, **5 failed**, 5 skipped |
| Python, those 5 re-run without `QUANTA_TRACKING_DISABLED` | 3 pass (see below); **2 still fail** |
| Python, `TZ=UTC`, without `QUANTA_TRACKING_DISABLED` | 1402 passed, **2 failed**, 5 skipped (same 2) |

**The 3 that are not real failures:** three tests in `test_signal_ledger.py` assert that a decision *is recorded*. `QUANTA_TRACKING_DISABLED=1` in the run environment turns recording off, so they fail by construction. `tests/conftest.py` already points the ledger at a temp file for the whole session, so the suite runs **without** that variable from here on. Golden scans and replays keep it.

**Known baseline failures (owner decision: record them, and pin the clock in a separate test-only commit):**

| Test | Why it failed | Fix |
|---|---|---|
| `test_my_portfolio.py::TestService::test_end_to_end_with_analysis` | Reads the wall clock against fixture quotes dated Friday 2026-09-25. During market hours, or once the quote is older than one session, the quote is stale and AAPL gets no call. This was a known trap in earlier sessions | `f133457`: runs under `freeze_time("2026-09-26 16:00:00")` |
| `test_watchlist_root_causes.py::test_funded_action_with_no_executable_size_does_not_read_as_an_order` | The fixture bar `market_timestamp="2026-09-25"` has gone stale, so the Decision adds `STALE_PRICE_DATA`, which takes the place of the `SIZE_NOT_EXECUTABLE` the test checks | `f133457`: same freeze |

Both tests pass after `f133457`, in local time and under `TZ=UTC`. That commit contains no production code.

### Production ledger, cache and journal are untouched

These were checked after the suite ran without `QUANTA_TRACKING_DISABLED`, and compared with the listing taken at 08:31, before any run.

| File | Size before | Modified before | Size after | Modified after | sha256 (prefix) |
|---|---|---|---|---|---|
| `stock-analysis/data/ledger/signals.sqlite3` | 274,432 | 2026-10-01 21:43:28 | 274,432 | 2026-10-01 21:43:28 | `5ba117b55fda7f19` |
| `ui/stock-analysis/data/cache/yf_cache.json` | 34,755,507 | 2026-10-01 21:43:23 | 34,755,507 | 2026-10-01 21:43:23 | `444f997a9c4cf152` |
| `ui/stock-analysis/data/cache/yf_cache.pkl` | 35,489,173 | 2026-09-08 10:04:59 | 35,489,173 | 2026-09-08 10:04:59 | `451b68de2d5495e4` |
| `ui/stock-analysis/webapp/data/portfolio_journal.json` | 8,800 | 2026-10-01 13:26:30 | 8,800 | 2026-10-01 13:26:30 | `955a9906fc6dcbfd` |

All unchanged. The 21:43 times are from your own scan on 2026-10-01, before this session.

### Golden scan (before any change, 2026-10-02 morning, pre-market)

Settings: US, Standard, Balanced, max 5, diversify on. The the small-capital case, $25,000 and max-positions-1 variants are below.

**base ($2,500)**
- `scan_status = partial_with_picks`. 7 names were rate-limited this run.
- Universe 250: 243 analysed, 7 failed.
- The pre-filter exceeded its 90 s budget, so the universe was an **even-spread fallback** (F4).

| Ticker | Published | Funded | Card R:R | Scanner / Research grade | Binding reason |
|---|---|---|---|---|---|
| LNTH | WATCHLIST | no | 2.77 | B / F | "Risk Grade F blocks new-money BUY → WATCHLIST" (F1, F3, F9) |
| HST | WATCHLIST | no | 2.25 | A / C | "R/R 1.23 below min 1.50 → WATCHLIST; Capped at the Research Decision" (F2) |

- `gate_stats.gate7_sector_cap = 0` while 2 names were sized (F5). `empty_scan_explanation = ''` (F5).
- `filter_stats`: score_below_min 82, signal_not_actionable 49, risk_reward_low 23, low_volume 23, market_cap_too_low 17, insufficient_data 17, zero_allocation 10, provider_rate_limited 7, earnings_calendar_missing 7, score_below_min_post_penalty 6, price_outside_range 4, data_validation_failed 4, signal_avoid 4, committee_hold 1, risk_grade_size_below_minimum 1.

This matches the audit's run from the evening before: the same two names, the same R:R values and the same withdrawals.

**the small-capital case** (account-derived input redacted): `scan_status = no_picks`. 0 rows. 250 of 250 analysed.
- `filter_stats`: zero_allocation **37**, signal_not_actionable 50, score_below_min 85.
- The explanation reads: "No new-money candidates passed this scan's rules … Filter counts: score_below_min=85; signal_not_actionable=50; zero_allocation=37; …". **It never names the $100 minimum or the capital any name would need** (F5, F6, F11).

**$25,000**: `scan_status = success`.

| Ticker | Published | Funded | Size | Card R:R | Grades |
|---|---|---|---|---|---|
| LNTH | WATCHLIST | no | — | 2.77 | B / F |
| GOOGL | ACCUMULATE | **yes** | 2 sh = $676.48 | 2.41 | B / C (deep confirm agreed) |
| HST | WATCHLIST | no | — | 2.25 | A / C |

`risk_reward_low` = 33 (up from 23), because the names sized out at $2,500 now reach the R:R gate.

**Max positions 1**: `scan_status = success` with **0 funded**.
- The engine log says "Published 2 recommendation(s), 0 carrying capital", but the API returned **1 row (HST)**. LNTH was cut by `app.py`'s post-hoc `recs[:max_positions]` (F5).

All four variants reproduce the audit's runs (sections 2.3 and 2.6). Run times: 555 s, 418 s, 425 s and 425 s.

### Golden My Portfolio (before any change)

**Source.** You signed in to Robinhood once, on a scratch server (port 5657, scratch ledger, cache and journal). One read-only snapshot was saved to the scratchpad: the `Portfolio` model with positions, values, cash and buying power. **Not in the repo.** It is kept until the checkpoint 3 comparison, then deleted. After the snapshot, the instance was disconnected (`POST /api/portfolio/disconnect`, after which status read `disconnected` and the account was cleared) and the server was stopped. No order was placed or previewed.

**Cross-check.** The live server's own view of 18 holdings was compared with `portfolio_decision_table.py`'s replay of the snapshot through the current code. **0 differences** across 18 holdings × 7 fields (action, new money, basis, score, grade, holder action, engine action).

Actions only. No quantities, values or weights. Holdings are labelled H01 to H18; the symbols are redacted.

| Symbol | Action | Basis | Score | Grade | Holder rule | Engine action |
|---|---|---|---|---|---|---|
| H01 | Trim | CONCENTRATION | 54.7 | C | HOLD | WATCHLIST |
| H02 | Trim | RISK_GRADE_F | 65.6 | F | TRIM | WATCHLIST |
| H03 | Trim | RISK_GRADE_F | 66.4 | F | TRIM | WATCHLIST |
| H04 | Trim | RISK_GRADE_F | 69.6 | F | TRIM | WATCHLIST |
| H05 | Trim | RISK_GRADE_F | 69.7 | F | TRIM | WATCHLIST |
| H06 | Trim | RISK_GRADE_F | 66.7 | F | TRIM | WATCHLIST |
| H07 | Hold / No New Money | LOW_REWARD_RISK | 65.7 | C | HOLD | WATCHLIST |
| H08 | Hold / No New Money | ACCOUNT_DATA_UNRECONCILED | 57.8 | C | HOLD | ACCUMULATE |
| H09 | Hold / No New Money | ACCOUNT_DATA_UNRECONCILED | 57.0 | C | HOLD | ACCUMULATE |
| H10 | Hold / No New Money | RISK_GRADE_D_CAP | 68.5 | D | HOLD | WATCHLIST |
| H11 | Hold / No New Money | RISK_GRADE_D_HOLD | 65.0 | D | HOLD | WATCHLIST |
| H12 | Hold / No New Money | LOW_REWARD_RISK | 58.4 | B | HOLD | WATCHLIST |
| H13 | Hold / No New Money | RISK_GRADE_D_HOLD | 59.8 | D | HOLD | WATCHLIST |
| H14 | Hold | HOLD_NOT_OWNED | 51.6 | B | HOLD | WATCHLIST |
| H15 | Hold | HOLD_NOT_OWNED | 50.2 | B | HOLD | WATCHLIST |
| H16 | Hold | HOLD_NOT_OWNED | 47.9 | F | HOLD | WATCHLIST |
| H17 | Hold | HOLD_NOT_OWNED | 47.3 | B | HOLD | WATCHLIST |
| H18 | Unavailable | ANALYSIS_UNAVAILABLE | — | — | — | — (too new: 15 more sessions needed) |

Notes for later phases:
- **H08** reads "Hold / No New Money" because the account data does not reconcile (`ACCOUNT_DATA_UNRECONCILED`), not because of anything about the stock. The engine's own action is ACCUMULATE. That is the input difference behind the audit's H08 conflict (Phase 8).
- **H07** is held back by `LOW_REWARD_RISK`, the Research engine's 1.5 hurdle. On Picks, its rejection came from the scanner's 1.8 gate on different levels (Phase 8 / F2).
- **H16** is grade F but its holder rule is HOLD, not TRIM. To be checked in F3/F7: the decision's codes may not carry `RISK_GRADE_F` when the candidate is already below the band.

### F15: regression tests ported from `quanta-review-redesign` @ `064f747`

- **Change:** both files copied and adapted. That branch is not an ancestor of HEAD.
  - `minimum_score_for_action` is imported from `engine.decision_engine`. The branch re-exported it from SP; HEAD does not.
  - `_StubScan` reads `_run_scan_workers` with `getattr`, so the module imports on HEAD.
- **Files:** `stock-analysis/tests/test_empty_book_root_causes.py`, `stock-analysis/tests/test_insider_signal_calibration.py`.
- **Result on HEAD:** 7 passed, 19 strict xfail. Identical under `TZ=UTC`.

| Test group | Count | Marker | Why |
|---|---|---|---|
| Defect 2: capital-relative minimum, one-share funding (`test_minimum_position_scales_with_capital`, `test_large_accounts_keep_the_absolute_floor`, `test_small_account_can_fund_a_position_after_legitimate_haircuts`, `test_whole_share_rounding_does_not_demote_the_action`, `test_one_share_is_refused_when_it_breaches_the_position_cap`) | 5 | `F11_PENDING` (strict, `raises=AttributeError`) | Need `effective_min_position_size` and `_fund_at_least_one_share`. **F11 adds them and removes this marker** |
| Defect 3: scan-worker cooldown and circuit (`_run_scan_workers`) | 5 | `BRANCH_ONLY_SCAN_WORKERS` (strict, `raises=TypeError`) | The worker loop was rewritten on that branch only. Outside the Picks audit. **Unresolved** |
| Defect 1: `test_blue_sky_name_never_gets_a_target_below_its_entry` | 1 | strict xfail | That branch's blue-sky target fix (ATR projection when nothing sits overhead) is not on HEAD. On HEAD such a name gets R:R < 0 and fails the 1.8 gate, so it is a missed candidate, not a wrong publication. Outside the Picks audit. **Unresolved** |
| Insider calibration (compensation and blank text, mega-cap scaling, haircut vs veto, monotone thresholds) | 8 | `BRANCH_ONLY_INSIDER` (strict) | That logic exists only on that branch and is outside the Picks audit. Porting it would change committee decisions. **Unresolved** |
| Passing on HEAD | 7 | none | Two defect-1 shapes, the new-money floor agreement, explicit purchase and sale recognition, small-cap heavy flow, and the committee hard veto |

The markers are strict, so if any of that behaviour is ported later, the test passes, the xfail becomes a failure, and the marker has to be removed.

## F9: one source for every threshold (Phase 1)

**Change.** New `stock-analysis/core/engine/thresholds.py` holds every decision threshold in 17 groups:
CAPITAL, UNIVERSE, SCORE, RR, COMMITTEE, REGIME, BOOK, SIZING, COSTS, LEVELS, RISK_GRADE, LADDER, RESEARCH, PORTFOLIO, EVENTS, EVIDENCE, UI.

- **Moved, not changed.** Every value was moved unchanged, and the edit scripts asserted that each old literal equalled its new key before replacing it.
- **Old names still work.** They are **read-through aliases** (`thresholds.alias`): `TradingConfig.*` (51 names), TA `Config.MIN_RISK_REWARD`, `TradeSetupGenerator.SPREAD_BPS/SLIPPAGE_BPS`, `DecisionInputs.min_*` and `PortfolioPolicy` defaults. Patching either the group or the alias changes behaviour, and the two can never differ.
- **Text built at read time.** Reason and remedy text is formatted from the config whenever it is read:
  - `engine.blockers.BLOCKERS` is now a mapping over templates.
  - The committee reasons, TA beginner/outlook/invalidation text and `rich_display` print R:R through `thresholds.fmt_rr`, at 2 dp.
- **New codes.**
  - `SCANNER_LOW_RR`: the scanner's 1.8 gate on its own levels, kept apart from `LOW_REWARD_RISK`, the Research 1.5 gate. The text now names the hurdle that fired.
  - `ANALYST_DOWNSIDE`: the scanner used to feed analyst-target downside into `dcf_overvaluation_pct`, so its reason said "DCF". The new code applies the same one-notch trim at the same 25% (`LADDER.ANALYST_DOWNSIDE_TRIM_PCT`), named as what it is. It is added to the holder exit-corroboration set, as DCF already was, so holder behaviour is unchanged.
- **API.** `GET /api/thresholds` returns `thresholds.as_dict()`.
- **Page.** The page embeds the same JSON (`<script id="quanta-thresholds">`, rendered by `index()`).
  - `app.js` reads it through `thr()`, `fmtThreshold()` and `applyThresholds()`. Every rule number in the copy is a `data-threshold` span filled at boot: 59 spans across the Picks form, the guide and the FAQ.
  - The guide calculator (`GUIDE_SIZE`, `GUIDE_BANDS`, `GUIDE_MIN_RR`, `GUIDE_EXIT_FLOOR`), the capital defaults and minimums, the header stale/aging minutes, the funnel's cost-viability note and the My Portfolio "Reward/risk below …" tag all read `thr()`.
- **Docstrings and comments.** The `get_top_recommendations` docstring ("≥6.5/10", "≥2.0:1", "25% per sector") and the gate-4 comment ("2.0:1") now name the config keys. Comments that restated a moved value were removed or reworded.

**Files.**
- New: `engine/thresholds.py`.
- Engine: `SP_ELITE_PRO.py`, `TradingAnalysis_ELITE.py`, `rich_display.py`, `engine/{types,score_bands,risk_grade,risk_overrides,blockers,decision}.py`, `portfolio/analytics.py`, `tracking/ledger.py`.
- Web app: `app.py`, `templates/index.html`, `static/{app,elite-pro,my-portfolio}.js`, `package.json` (test script `--require`).
- Test helpers: `tests/helpers/thresholds_setup.cjs`, `tests/fixtures/thresholds.json`.

**Tests.**
- **New `tests/test_thresholds_single_source.py`** (37 tests):
  - remedy text quotes `RR.RESEARCH_MIN` and follows a config patch;
  - scanner and Research hurdles are named apart;
  - the aliases read through (SP, TA, `PortfolioPolicy`);
  - `ANALYST_DOWNSIDE` is not `DCF_OVERVALUATION`;
  - `/api/thresholds` returns every group;
  - the page embeds exactly `as_dict()`;
  - the JS fixture equals the config;
  - a 25-pattern literal guard over the engine, the web app, the JS and the template.
- **Guide JS tests rewritten.** The four that regex-parsed Python source now check the guide copy and calculator against the thresholds fixture, plus that the engine reads the same keys.
- **Updated expectations:**
  - `test_dell_display.TestFormatRR` now expects `"2.00:1"` (2 dp).
  - 7 golden-master snapshots re-recorded (`BRK-A`, `BRK-B`, `KO`, `MSTR`, `NVDA`, `RELIANCE.NS`, `RIVN`). The 31 changed fields are all wording, R:R at 2 dp and "Research reward/risk … 1.50:1". No action, score, level or size changed.
- **Order (honest note).** The aliases were introduced before this test file was written. Each step was checked against the full suite, which stayed green, and against the old literal. The new test file fails on the baseline tree, because `engine.thresholds` does not exist there.

**Evidence.**
- Python: 1448 passed, 0 failed, 19 xfail, in both local time and `TZ=UTC`. The baseline was 1402 plus 2 date-dependent failures; the rise is the new tests.
- JS: 383 / 0.
- No golden-master action changed.

**Thresholds now in config** (key = value; old location):

| Group | Keys and values | Was |
|---|---|---|
| CAPITAL | DEFAULT_US 2500, MIN_US 100, DEFAULT_INDIA 200000, MIN_INDIA 10000, MAX 1e9, MAX_POSITIONS_DEFAULT 5, MAX_POSITIONS_LIMIT 100, MAX_CUSTOM_TICKERS 500, MAX_HOLDINGS_ROWS 500 (new, F6) | `index.html:392`, `app.js:5161/5169/5197/5946`, `app.py:4785-4797`, `TradingConfig.DEFAULT_CAPITAL` |
| UNIVERSE | MIN_MARKET_CAP 2e9, MIN_PRICE 10, MAX_PRICE 5000, MIN_AVG_VOLUME 750k, MIN_DAILY_DOLLAR_VOLUME 20M, MIN_HISTORY_DAYS 200, MAX_LIVE_DATA_AGE_DAYS 10, PREFILTER_TIMEOUT_SECONDS 90 (env), ZSCORE_MIN_UNIVERSE 30 (env), PREFILTER_MAX_TICKERS 1000 (F4 removes the cut) | `TradingConfig` |
| SCORE | MIN_SCORE 3.5, ACCUMULATE 5.5, GOOD 6.5, EXCELLENT 7.5, ADJUSTMENT_CAP 1.0 | `TradingConfig` |
| RR | SCANNER_MIN 1.8, RESEARCH_MIN 1.5, OPTIMAL 3.0, COMMITTEE_FLOOR 1.6, COMMITTEE_INSIDER_RELAX 0.4 | `TradingConfig.RISK_REWARD_MIN`, `engine/types.py:65`, TA `Config.MIN_RISK_REWARD`, SP committee `max(1.6, … 0.4)`, "1.5:1" text in `blockers.py:60`, TA:7477/11517/12111/12165, `rich_display.py`, `app.js:722`, `my-portfolio.js:856`, guide |
| COMMITTEE | UPSIDE_HURDLE 8 / 6 (bullish insider), SCORE_FLOOR_BULLISH_RELAX 0.3, BUY_TIER_BULLISH_RELAX 0.2, ANALYST_DOWNSIDE_LIMIT −5 / −10, RSI_EXTENDED 75 | SP `_apply_investment_committee_policy` |
| REGIME | VIX 12/20/30, VIX_VOLATILE 25, exposures 1.2/1.0/0.7/0.5, error fallback 0.8, caps RANGING .85 VOLATILE .70 BEAR .75 UNKNOWN .60, SIGNAL_THRESHOLDS BEAR 9.0/8.0 VOLATILE 8.7/7.7 RANGING 8.5/–, NEUTRAL VIX 15 → 8.0/7.0 and 30 → 9.0/8.0, RSI_OVERBOUGHT 70/80 (growth), OVERBOUGHT_HOLD_SCORE 6.5, VIX_SKIP 25 | SP `detect_regime`, exposure caps, signal generator |
| BOOK | BANNED_GRADES (D, F) (F7 changes it), SMALL_POOL_FOR_SCORE_FLOOR 8, LARGE_POOL_FOR_CONVICTION 20, CONVICTION_FLOOR 3/2, COST_VIABILITY_MULTIPLE 2.0, DEFAULT_ROUND_TRIP_BPS 15, MAX_SECTOR_PCT 0.25, SECTOR_CAP_PROFILE balanced, profile % 0.25/0.35/0.40, MAX_WATCH_ROWS 10 (new, F5) | SP `get_top_recommendations`, `TradingConfig` |
| SIZING | target risk 0.6% / 0.75%, max risk 2% / 2.5%, MAX_PORTFOLIO_RISK 10%, MIN_POSITION_SIZE 100, MIN_POSITION_PCT 2%, MAX_POSITION_PCT 10%, SMALL_ACCOUNT 5000 / 25% / scale floor 500, POSITION_CAP_BUFFER 1%, grade multipliers A 1, B 1, C .8, D .5, F 0 (unknown .5), signal multipliers 1.0/.8/.6 (other .5), score mult .7 + .5 × score/10, DEEP_ACCUMULATE_SCALE .5, earnings multipliers .25/.5/.8/.7, MANDATE_SCALE conservative .70, balanced 1.00, aggressive 1.00 | `TradingConfig`, SP `_calculate_position_size`, `:8683`, `:11024`, `:2052`, `app.py:4908`, `app.js` `GUIDE_SIZE` |
| COSTS | tier floors 200B/10B/2B, commission/slippage per tier, India statutory 0.12% | `TradingConfig._tier_costs` |
| LEVELS | STOP_ATR 2.75, TARGET_ATR 5.5, stop distance 3%–20%, TRAILING_ATR 1.75, RESEARCH_HORIZON_DAYS 63, VALUATION_HORIZON_DAYS 252, Research costs 5 + 3 bps; ENTRY_ZONE_ATR_MULT 1.0 and TARGET_STRUCT_MAX_AGE_SESSIONS 63 are new (F2, F1) | `TradingConfig`, TA `TradeSetup:1059`, `TradeSetupGenerator:6336`, `TA:7422`, `engine/decision.py` horizons |
| RISK_GRADE | baseline 3, vol 25/35/50%, beta 1.2/1.5, drawdown −25/−40%, tail 50/70, liquidity floor 5, cut-offs A ≤ 3, B ≤ 5, C ≤ 7, D ≤ 8, liquidity bands 50M/10M/1M → 10/8/6 (floor 4); LOOKBACK_DAYS 504 and TAIL_WINSOR_PCT 1% are new (F3) | `engine/risk_grade.py:46-93`, `TA:3444-3457` |
| LADDER | SCORE_BANDS 75/65/55/45/35/25, MIN_SCORE_FOR_BUY 55, DCF_OVERVALUATION_CAP_PCT 25, ANALYST_DOWNSIDE_TRIM_PCT 25 | `engine/score_bands.py`, `engine/types.py:64`, `engine/risk_overrides.py:156` |
| RESEARCH | MIN_RISK_REWARD (alias of RR.RESEARCH_MIN), TARGET_UPSIDE_THIN 5%, ANALYST_DOWNSIDE_LIMIT −5%, RSI_EXHAUSTION 85, RSI_CAPITULATION 15, STRONG_CONFIRMATIONS 3 | TA overrides `:5511-5536`, `:5838-5858` |
| PORTFOLIO | MAX_SINGLE_POSITION 10%, TRIM_REVIEW_POSITION 20%, MAX_SECTOR 30%, CORRELATION 0.70, EARNINGS_WINDOW_DAYS 7, STOP_RISK_BUDGET 6%, MIN_ADD_VALUE 50 | `portfolio/analytics.PortfolioPolicy` |
| EVENTS | all new for F1 | — |
| EVIDENCE | MIN_SAMPLE 30, MIN_DATES 10 | `tracking/ledger.py:54/649` |
| UI | HEADER_AGING 5 / STALE 10 min, RR_DECIMALS 2; NEAR_MISS_N 10 and NEAR_MISS_SHOWN 5 are new (F5) | `app.js:2356` |

**Literals left in place, with the reason (disagree and I will move them):**
- **Factor and regime weights** (TA `Config.WEIGHT_*`, `REGIME_WEIGHTS`, SP blend weights, `regime.factor_weights`). These are model parameters, not thresholds. The brief says do not retune them, and they stay where the model defines them.
- **Indicator window lengths and maths constants:** RSI 14, ATR 14, the 20/50/200-day MAs, `tail(252)`, the 0.97 "pullback −3%" display hint in `rich_display`, and TA confidence heuristics such as `max(35, confidence - 15)`. These are measurement definitions or display heuristics, not cut-offs a decision turns on.
- **Market-playbook posture bands** on the Overview and the guide ("Offensive 65–95%" and similar, `app.py` playbook). That is the market-regime playbook engine, not the Picks or decision pipeline. It is outside this audit, and the guide test already pins it to `app.py`.
- **Guide worked examples** (Entry $100.00, Stop $95.80, the $2,500 sample account, 25 shares) and the reel's illustration geometry. These are example inputs and outputs, not rules. The calculator recomputes them from `GUIDE_SIZE`.
- **`regime.recommended_cash_pct`.** Set and never read (audit 5.7). Left as is and flagged for removal; out of scope.
- **Provider pacing, retries and timeouts** (`PACER_*`, `BACKOFF_*`, `PREFETCH_*`). These are operational, not decision thresholds. They stay in `TradingConfig` with their env overrides.

**Unresolved.**
- `BOOK.BANNED_GRADES` and the guide's "risk grade of A to C" text change in F7.

## F14: Aggressive mandate removed (Phase 1, owner decision Q6)

**Change.**
- `SIZING.MANDATE_SCALE` now offers only `conservative` (×0.70) and `balanced` (×1.00).
- The Aggressive option is removed from the scan form (`#sp-risk`) and the guide calculator (`#hw-c-mandate`), along with its hint, its `RISK_PROFILE_NOTE` and narrative entries, and the dead `data-run-scan="aggressive"` shortcut branch. No code emitted that shortcut; it would silently switch the mandate.
- `MANDATE_HINTS` is built from the configured scales, so the Conservative hint quotes 70% from config.
- **Old clients.** A saved form or old client that still sends `aggressive` is accepted. It is sized as its alias in `SIZING.MANDATE_LEGACY_ALIASES` (`balanced`), and the scan result's `risk_profile.notice` says so ("The Aggressive mandate is no longer offered: it sized exactly like Balanced. This scan used Balanced.").
- **Unknown values** are still rejected (400). The error text lists the offered mandates from config.

**Not changed.**
- `BOOK.SECTOR_CAP_PROFILE_PCT` still contains an `aggressive` key. That is the small-account **sector-cap** profile, a different setting from the mandate. It stays hard-wired to `balanced`, as the audit noted.

**Files.** `engine/thresholds.py`, `webapp/app.py`, `templates/index.html`, `static/app.js`, `tests/fixtures/thresholds.json`.

**Tests.**
- New `tests/test_mandate_offer.py` (4 tests). All four failed before the change:
  - the config offers only mandates that change sizing;
  - a legacy `aggressive` request is sized as Balanced and the response says so;
  - an unknown mandate is rejected, and the error no longer lists aggressive;
  - no Aggressive note or narrative entry remains.
- JS `review_round2.test.cjs`: the two Aggressive tests are replaced by "the mandate hint quotes the configured scale" and "Aggressive is not offered anywhere; a scan shortcut never changes the mandate". The second one failed before the change.
- `picks_form.test.cjs` no longer expects an Aggressive option.

**Evidence.**
- Python: 1452 passed, 0 failed, both time zones.
- JS: 383 / 0.
- A search finds "aggressive" only in the new comment, in unrelated `target_aggressive` / `aggressive_buyer` fields, and in the sector-cap profile key.

## F1: corporate-event gate (Phase 2)

**Change.**

New module `engine/corporate_events.py`. It reads the company's own SEC EDGAR filings through the existing `portfolio.sources.EdgarClient`:

- **`PENDING_ACQUISITION`.** Raised by a target-side merger proxy (DEFM14A or PREM14A whose text is not the buyer's), a target's tender response (SC 14D9), or an 8-K item 1.01 whose text is a merger agreement **with this company as the target**.
  - A later 8-K item 1.02 that terminates the merger agreement clears it.
  - Deal price, agreement date and CVR terms are parsed from the filing text, never invented. If the price cannot be read, the text says "deal price not stated in the filing text read".
- **`DELISTING`.** Raised by Form 25-NSE or 15-12B.
- **`POSSIBLE_PENDING_DEAL`.** The 20-day true range is below `EVENTS.VOL_COLLAPSE_RATIO` × the 120-day range, and no filing explains it. This is a caution only: the name is capped at Watch.
- **Fail closed.** These cases give `EVENT_CHECK_UNAVAILABLE`, and the name is never funded but still shown:
  - no SEC contact configured;
  - an EDGAR error;
  - no registrant found for the ticker;
  - a deciding document that could not be read.
- **Reason text** is built from the data. Example: "Pending cash acquisition at $102.50 (merger agreement 2026-08-03; DEFM14A filed 2026-09-10 on SEC EDGAR). The price is anchored to the deal terms, so its range and past highs no longer describe its upside. The contingent value right is non-transferable: it stays with holders at closing and cannot be bought or sold separately. Remaining spread to the cash price: 2.9%."
  - The non-transferable sentence appears only when the filing says so.
  - Nothing says the target "cannot be reached".

**Departures from the audit's sketch (D1), both made to avoid false positives:**

| Audit sketch | Built instead | Reason |
|---|---|---|
| Any 8-K item 1.01 counts | Item 1.01 needs merger-agreement text naming this company as the target | Item 1.01 also covers credit agreements, and the buyer files one too |
| SC TO-T is a strong form | Dropped | It also appears in the bidder's own filings. The target always answers with SC 14D9 |

8-K items 2.01 and 3.01 are also not treated as a delisting. The buyer files 2.01, and 3.01 is often a deficiency notice.

**Wiring:**
- **Scanner.** New gate **G1b** in `get_top_recommendations`, `_apply_event_gate`, runs on every actionable candidate.
  - AVOID names go to `rejected`: `{ticker, final_action: AVOID, code, reason, event_check}`, with no levels.
  - Unavailable or caution names are published as $0 Watch rows with the reason, after the funded and withdrawn rows.
  - New gate stats: `gate1b_event_clear`, `event_avoid`, `event_watch`.
  - `StockAnalysis.event_check` carries the result on every row.
- **Data needed for the checks.** Indicators gain `atr_pct_20`, `atr_pct_120` and `high_52w_age_sessions`.
- **Holders.** `blockers.holder_rule(..., event=)` returns EXIT (`PENDING_ACQUISITION_SPREAD`) when the remaining spread is below `EVENTS.ARB_SPREAD_MIN` (2%), per owner decision Q2. An unknown spread never produces an exit.
- **Blocker codes.** `PENDING_ACQUISITION`, `DELISTING`, `POSSIBLE_PENDING_DEAL` and `EVENT_CHECK_UNAVAILABLE` are added to `BLOCKERS` and `_BLOCKING`.
- **EDGAR client.**
  - `recent_filings(..., forms=)`, so My Portfolio keeps its own form set.
  - The User-Agent is built from `QUANTA_SEC_USER_AGENT` / `QUANTA_SEC_CONTACT`, and event queries **require** it.
  - SEC pacing at ≤10 requests/s; a bounded `http_get_text` (sec.gov only); a process-wide client and a document cache, so results outlive a scan.
- **Stale structural targets (audit 5.1).** A 52-week high older than `LEVELS.TARGET_STRUCT_MAX_AGE_SESSIONS` (63) is dropped from the target mean. The structural "2% above spot" rule, the consensus sanity range and the regime safety caps also moved to `LEVELS`.
- **API.** `result.rejected` is the list of event rejections.

**Deferred.**
- The rejected-list **UI** is Phase 6, audit 8.3.
- The Research engine scores an M&A filing as a *positive* catalyst. That is neutralised for flagged names when the shared ladder (F7) applies the event gate on the Research and My Portfolio paths.

**Files.**
- New: `engine/corporate_events.py`.
- Changed: `engine/{blockers,thresholds}.py`, `portfolio/sources.py`, `SP_ELITE_PRO.py`, `webapp/app.py`, `pyproject.toml` (marker), `tests/conftest.py`, `tests/fixtures/thresholds.json`.

**Tests.** New `tests/test_corporate_events.py` (18). The module tests were written and seen failing before the module existed; the two book-level tests were written after the gate was wired.
- **Detection:** the merger proxy and the merger 8-K flag; a credit-agreement 8-K does not; the buyer's 8-K and the buyer's proxy do not; a termination clears; a delisting flags; an unparseable price is said, not invented; a range collapse is a caution only.
- **Fail closed:** no contact, a provider error, or an unknown registrant gives unavailable; event queries require the contact.
- **Holders:** EXIT below the spread minimum; no EXIT when the spread is wide or unknown.
- **Stale 52-week high:** dropped from the target mean (LNTH shape).
- **Book:** `test_pending_acquisition_avoid` (audit 9.2: AVOID with `PENDING_ACQUISITION`, levels hidden); an unavailable check is published but never funded.
- **conftest.** An autouse `_no_sec_network` fixture stubs the shared checker as all-clear, so unit tests never call SEC. `@pytest.mark.real_event_gate` opts out.

**Evidence.** Python: 1470 passed, 0 failed, both time zones. JS: 383 / 0.

**Live check not run: checkpoint 4.** `QUANTA_SEC_CONTACT` is not set (process, user and machine scope all checked). Until it is set, every candidate's check is `unavailable` and **no name is funded**. That is the fail-closed rule working as designed. The LNTH acceptance run needs the variable.

**Open question (checkpoint 4).** EDGAR covers only SEC registrants. NSE names (India scans) have no event source, so under fail-closed **no India name can be funded**. The decision is listed under Unresolved.

## F2: publish the levels that bind the decision (Phase 2, owner decision Q1)

**Change.**
- **One level set per pick.** New `engine/levels.py`:
  - `publish_levels` builds the set a page shows: source, label, entry, an **engine-computed entry zone** (`LEVELS.ENTRY_ZONE_ATR_MULT` × ATR below the entry), stop, target, R:R at 2 dp (net of round-trip cost at the top of the zone), horizon and target basis.
  - `rr_triggers` solves the price triggers exactly from the engines' own formula `rr = (T − E − c)/(E − S + c)`, `c = k·E`:
    - `target_needed = E + c + m(E − S + c)`
    - `entry_needed = (T + m·S) / ((1 + k)(1 + m))`
  - When the R:R is below the hurdle, `rr_remedy` writes the remedy text from those levels and **the gate that fired** ("Research R:R 1.23 needs 1.50: reached at a spot at or below $22.48 with the stop unchanged, or at a 63-session target at or above $23.67.").
- **Q1, Research levels govern.** In `_deep_confirm_final_picks`, `_bind_research_levels` takes the Research engine's `trade_setup` whenever it has usable levels.
  - That set becomes the pick's `entry_price` / `stop_loss` / `price_target` / `risk_reward_ratio` and `binding_levels`.
  - The scanner's set moves to `scanner_levels`, labelled "Scanner reference levels".
- **Sizing on the binding stop.** A funded pick is **re-sized on the Research stop**: allocation × stop distance = risk budget, so it scales with the ratio of the stop distances. The position cap and whole shares then apply.
- **No Research levels.** Before the Research check runs, or when it is disabled or not confirmed, `binding_levels` is the scanner set, labelled "Scanner levels (Research check not run / not confirmed)".
- **The cap is not loosened.** The ONE DECISION cap still applies the Research verdict. The page now shows the numbers that verdict was decided on.
- **Page.**
  - One `fmtRR()` at the configured decimals for the table (was 1 dp), the card and the elite-pro chip.
  - The table, card and chip read `binding_levels`. The zone comes only from `binding_levels.entry_zone_low`: `entryZoneLow` and its 1-ATR / 2%-of-price fallback are deleted.
  - The card states which set decided (`data-levels-source`), and shows the scanner set only inside the disclosure as "Scanner reference levels (not binding)".
  - For an R:R gate, the watch guidance shows the engine's `binding_levels.remedy`. The generic "a tighter stop or a higher target lifts reward-to-risk" line is gone.
  - New `SCANNER_LOW_RR` remedy entry.

**Checkpoint 2 note.** The audit quoted HST's triggers as "spot ≤ $22.40, target ≥ $23.66". Solved exactly from the engine's net-of-cost formula they are **$22.48 / $23.67**. Both reproduce R:R 1.50 exactly, which is tested by substitution. The audit's figures were approximations.

**Files.**
- New: `engine/levels.py`.
- Changed: `SP_ELITE_PRO.py` (`binding_levels` and `scanner_levels` fields; `_bind_scanner_levels`, `_bind_research_levels`), `static/app.js`, `static/elite-pro.js`, `package.json`.

**Tests.**
- **New `tests/test_binding_levels.py`** (9):
  - the level set and zone;
  - triggers reproduce R:R exactly (3 cases);
  - HST triggers;
  - the remedy names the gate;
  - **`test_binding_levels_are_published`** (audit 9.2): with `RESEARCH_DECISION_CAP` binding on `LOW_REWARD_RISK`, the published R:R equals the R:R in the binding reason (1.23 = 1.23);
  - an agreed pick is sized on the binding stop;
  - no Research levels gives a labelled scanner set.
- **New JS `tests/unit/binding_levels.test.cjs`** (4): **`test_rr_rounding_consistent`**; the zone comes from the engine; the remedy is keyed to the gate; scanner levels only as a reference. All failed before the change.
- **Updated tests:**
  - `dom_redesign` fixture: the pick carries `binding_levels`. The "2% of price fallback" test is replaced by "entry alone when the engine published no zone".
  - `audit_fixes`: the remedy wording.

**Evidence.**
- Python: 1479 passed, both time zones.
- JS: 387 / 0.
- The golden-scan delta is recorded after the live re-run (it needs `QUANTA_SEC_CONTACT`).

**Deferred.**
- The audit's longer-term single `TradeSetup` builder shared by both engines (5.1) is not done; the binding set is the bridge.
- The compact card layout is Phase 6.

## F1 (follow-up): live verification of the event gate (2026-10-02 afternoon)

`QUANTA_SEC_CONTACT` was set (user environment), so the event gate could be run against EDGAR for the first time. It was run over the **whole US pool the Standard scan draws from (806 names)**, not only the 250 a scan samples.

**First live run: 16 flags, of which 8 were wrong.** Each was traced to its filing text.

| Name | First run | Filing | Truth | Cause |
|---|---|---|---|---|
| AAPL | DELISTING | 25-NSE 2025-11-14 | a matured bond ("0.000% Notes due 2025") | Form 25 also removes notes, warrants, units and preferred series |
| OKE | DELISTING | 25-NSE 2026-09-18, after an **8-K12B** on 2026-09-10 | holding-company reorganization; OKE still trades | the predecessor's shares were replaced by the successor issuer's |
| GILD, VRTX | PENDING_ACQUISITION | their own 8-K 1.01 | **buyers** (of ACLX and CRNX) | an acquirer's 8-K defines "the Company" as the TARGET, so "merge with and into the Company" matched |
| FOX, FOXA | PENDING_ACQUISITION | 8-K 1.01 | **buyer** (the filing defines the target as "the Company") | same |
| FFBC | PENDING_ACQUISITION | 8-K 1.01 | **buyer** (a bank merger: the seller merges into the filer) | "merge with and into the Company" matched with the seller as subject |
| DVN | PENDING_ACQUISITION | DEFM14A | **buyer** (its joint proxy; Merger Sub is Devon's subsidiary) | proxies counted unless "subsidiary of the Company" appeared |
| CCL | PENDING_ACQUISITION | DEFM14A | **parent** in its own DLC unification | same |
| D, KVUE | "Pending cash acquisition at $0.41 / $3.50" | DEFM14A | real targets, paid mostly in the acquirer's **stock** | the cash leg was printed as the deal price |

**Change.** The gate now reads the filer's side and the consideration from the filing, never from a name.
- **The filer's side** (`filer_side`): the registrant's EDGAR name (now returned by `EdgarClient.recent_filings`) and any short name the filing *defines* for it ("Acme Corp., a Delaware corporation ("Parent")") are compared with the filing's roles.
  - **Target** when the filing says the filer itself ends up owned by someone else ("<filer> surviving as a wholly owned subsidiary of <other>", "the acquisition of <filer> by <other>"). A parent named after the filer ("New <filer>", its own reorganization) is not someone else. This outranks buyer evidence, because a target's proxy can also describe its own earlier holding-company merger (seen live in a target's proxy).
  - **Buyer** when the filer is defined as Parent/Purchaser/Buyer/Acquiror, owns the merger vehicle ("Merger Sub …, a wholly owned subsidiary of <filer>", including through a chain of subsidiaries), or the other party "will become a … wholly owned subsidiary of <filer>". Boilerplate such as "any wholly owned subsidiary of <filer>" does not count.
  - **Unknown**: an 8-K that names someone else as "the Company" with no buyer evidence is *unavailable* (fail closed), not clear.
- **Takeover wording**: only a merger vehicle (merger sub, purchaser, bidco) merging into the company counts; a seller merging into the filer is the filer buying.
- **Stock consideration**: when the per-share consideration includes a fraction of the acquirer's shares ("0.8138 shares of …", "exchange ratio of …"), there is no single cash price. The reason says so, and there is no spread and no holder Sell on a spread.
- **Delisting**: Form 25 / 15-12B counts only when the removed class is the stock (common or ordinary shares, ADSs, shares of beneficial interest). Notes, warrants, units and preferred are clear; an unread or unstated class is *unavailable*. A successor-issuer 8-K12B / 8-K12G3 on or before the Form 25 makes it a reorganization.
- **Mini-tender**: a 14D-9 answering a sub-5% mini-tender offer is not an acquisition.
- **Bidder swap**: an 8-K that terminates one merger agreement (1.02) *and* signs another (1.01) never clears a flag on its own.
- **India**: NSE/BSE symbols return "no corporate-event source covers NSE listings (SEC EDGAR covers US registrants only)" without spending an SEC request. Suffixes live in `EVENTS.UNCOVERED_LISTINGS`.
- New thresholds: `EVENTS.SUCCESSION_FORMS`, `EVENTS.UNCOVERED_LISTINGS`.

**Final live run (v4), whole US pool:**

| | Count |
|---|---|
| Names checked | 806 |
| Clear | 756 |
| Flagged PENDING_ACQUISITION | **8**: AES ($15.00 cash), ITGR ($127.00 cash), LNTH ($102.50 cash + non-transferable CVR), TECH ($73.00 cash), WBD ($31.00 cash), D, KVUE, QRVO (paid partly or wholly in the acquirer's stock, no fixed price) |
| Flagged DELISTING | 0 |
| Unavailable (fail closed) | **42**, all "no SEC registrant found": tickers in the universe lists that no longer map to a registrant (acquired, renamed or taken private, e.g. ACLX and CRNX, the targets of the GILD and VRTX deals). Never funded. The stale universe lists are an F4 matter. |

Every flag was checked against its filing text. Source for each: SEC EDGAR form and date in the row's reason, with the document URL in `event_check.url`.

**Throttle and cache (measured).** 1,722 SEC requests in 307 s. The minimum gap between requests was 0.109 s (target 0.12 s; the 11 ms shortfall was not explained by retained measurements) and the most in any one second was **8** (SEC limit 10). A second pass over 50 names made **0** requests: the ticker map is cached 24 h, filings lists 30 min and documents 24 h, **in process memory** (a fresh process re-fetches; nothing is written to disk).

**Tests** (`tests/test_corporate_events.py`, 18 to 44): one synthetic excerpt per live false-positive shape (buyer 8-K, alias buyer, bank merger, buyer joint proxy, parent reorganization, buyer through a subsidiary chain), the target side of the same wording, target proxies that name the buyer throughout, a target proxy that also describes its own reorganization, a stock deal, a successor reorganization, bond / warrant / unit / preferred / common classes, unreadable class, mini-tender, bidder swap, India. Each failed before its fix. The live names appear in this log only, never in source or fixtures.

**Every scanned name is checked (found in the post-F2 golden runs).** In today's branch scans the audit's acquisition target never became a candidate. With its pre-deal 52-week high no longer feeding the target (audit 5.1), its scanner R:R fell and an early stage dropped it. The gate ran only on book candidates, so the name was simply absent: not funded, but not explained either.
- A first fix (`166820d`) checked the analysed rows. A re-run showed that was not enough: names dropped at the score or committee stage never become rows (three of the eight flagged targets were scanned in that run, and none was published).
- The gate now checks **the scanned universe itself**. `scan_market` starts the EDGAR pass over the universe on a background thread when the scan begins, and the gate joins it and reads the warm cache.
- A flagged name is published in the rejected list as AVOID with its filing's reason, wherever it stopped. A name with no analysis row is labelled with its SEC registrant name (new `EventCheck.registrant`).
- Those names were never fundable, so an unavailable check on them changes nothing.
- Tests: `test_a_scanned_name_that_never_becomes_a_candidate_still_shows_avoid`, `test_a_universe_name_dropped_before_analysis_finished_still_shows_avoid`, `test_the_event_check_names_the_registrant`.

## Guard: no decision keyed to a name or a deal figure

New `tests/test_no_name_literals.py`:
1. The audit's names, companies and deal figures (and the F1 fixtures') never appear in non-test source, not even in comments, except as members of an index-constituent list.
2. Decision code (`SP_ELITE_PRO`, `TradingAnalysis_ELITE`, `engine/`, `portfolio/`, `app.py`, `static/*.js`) never compares a ticker with a literal symbol. Benchmarks used as market data (SPY, ^VIX, ^NSEI, …) are the only exception.
3. `engine/corporate_events.py` and `engine/levels.py` contain no dollar literal.

It found two comments naming the audit's ticker and deal price (rewritten generically) and one real special case: the Research engine exempted six named tickers from the HIGH LEVERAGE warning (`TA:9216`). It now uses the sector and interest coverage only. This affects only that warning's text, not any action. Of the six names, only the one classed outside Technology loses the name exemption, and its interest coverage is far above 10, so it still gets no warning.

Result:
```
tests/test_no_name_literals.py .....          5 passed
```

## F3: one risk grade (Phase 3)

**Before.** Both engines already called the shared rule `compute_risk_grade`, but they measured its inputs differently:
- **The scanner** used three inputs (volatility, beta, drawdown) over one year, with tail and liquidity left at neutral defaults.
- **The Research engine** used all five inputs over two years.

So the same bars could get two grades, and the card printed "Risk grade: scanner B · additional checks F. The restrictive result controls." The scanner also kept a legacy 0-100 inline score with literal cut-offs as its fallback.

**Change.**
- **New `engine.risk_grade.grade_from_history(close, volume, benchmark_returns)`.** It measures all five inputs from daily bars over `RISK_GRADE.LOOKBACK_DAYS` (504), the Research engine's window, so My Portfolio's window does not change.
  - **Volatility**: the standard deviation of daily returns, with unadjusted-split days (|r| ≥ `SPLIT_ARTIFACT_RETURN`) dropped as before.
  - **Beta**: against the market benchmark (SPY, or ^NSEI for India) on the last `BETA_LOOKBACK_DAYS` (252) common sessions. Below `BETA_MIN_OBS` it is "not measured", neutral, and says so.
  - **Drawdown**: computed on the returns path.
  - **Tail score**: skew and kurtosis read on returns **winsorized at `TAIL_WINSOR_PCT` (1%/99%)**, with steps `TAIL_SKEW_STEPS` and `TAIL_KURT_STEPS`. These were literals in TA; they now live in config. A single gap day therefore counts once, as drawdown, not again as a fat tail.
  - **Liquidity**: 20-day average dollar volume against the existing bands.
  - Returns grade, points, drivers, the window actually read and the measured inputs.
- **Research engine**: `RiskAnalyzer.analyze` grades through it. The reported skewness and kurtosis stay the raw statistics (Monte Carlo reads them); only the grade's tail score uses winsorized returns. The Research Decision now carries `risk_grade_drivers` and `risk_grade_lookback_days`.
- **Scanner**:
  - It fetches `RISK_GRADE.FETCH_PERIOD` (2y), in both the per-name request and the bulk prefetch, and grades the full fetch with the same function.
  - Every other metric keeps reading the one-year window, through new `indicator_window()`, which keeps the bars on or after the same calendar date a year earlier, as a `1y` request does.
  - Live check (MSFT, HST, RELIANCE.NS): the cut has exactly the same index as a `1y` request. Closes differ by at most $0.00006, because the provider recomputes the adjustment factor per request. Indicators are therefore identical up to floating-point noise, not byte-identical.
  - The legacy inline 0-100 fallback score is deleted. With too little history, the shared rule runs on the scanner's own measures, with tail and liquidity neutral, and the result is marked `partial`.
- **Scanner Decision** (`decision_for_scan_result`): now carries `risk_grade_drivers` and `risk_grade_lookback_days`, plus `blocking_codes`, `blockers`, `primary_blocker`, `holder_action` and `model_stance` through `action_contract`, the same contract as the Research Decision. The codes are the Research check's reason codes (new `deep_reason_codes` field on the pick), the event-gate code, and `RISK_GRADE_F` for a grade-F name.
- **Card**: "The restrictive result controls" is gone. The card lists the factors that added points ("Risk grade F: Volatility 45% annualised (+2) · Beta 1.92 vs its benchmark (+2) · measured on 504 sessions"), or "no factor adds risk points".
- **New thresholds**: `RISK_GRADE.FETCH_PERIOD`, `BETA_LOOKBACK_DAYS`, `BETA_MIN_OBS`, `MIN_RETURNS`, `SPLIT_ARTIFACT_RETURN` (was TA `Config.SPLIT_ARTIFACT_RETURN_THRESHOLD`, now an alias), `TAIL_BASE`, `TAIL_SKEW_STEPS`, `TAIL_KURT_STEPS`, `LIQUIDITY_AVG_DAYS`.

**Research golden master (12 recorded analyses).** Re-recorded with `--expected-only`; the fixtures are unchanged. Every change comes from winsorizing the tail:

| Ticker | Change |
|---|---|
| BRK-A | grade **B → A** (risk points 5 → 3; tail 70 → 50). The action stays WATCHLIST. |
| BRK-B | risk points 5 → 4 (tail 70 → 60); grade unchanged |
| NVDA | risk points 10 → 9 (tail 80 → 60); grade F unchanged |
| others | tail score 70/80 → 50/60 on some; no grade change |

No action, score, level or size changed. Monte Carlo outputs are identical, after the fix above kept the raw skewness and kurtosis.

**Tests.**
- **New `tests/test_single_risk_grade.py`** (6):
  - the configured window, with bars older than it ignored;
  - **`test_single_risk_grade`** (audit 9.2): three synthetic markets give the same grade, points and drivers on the scanner and Research paths;
  - **`test_one_gap_day_does_not_add_tail_points`**: unwinsorized, that day pushes kurtosis past the top band; it still counts as drawdown;
  - unmeasured beta is neutral and says so;
  - the scanner's indicator window is one calendar year;
  - drivers and blockers are on the scanner Decision.
- **New JS `tests/unit/risk_drivers.test.cjs`** (2).
- **Updated:** `dom_redesign`, which pinned the two-grade caveat, now expects the drivers line.
- **Results:** Python 1517 passed, JS 389/0.

**Found for F7.** H16 is grade F, yet its holder rule says HOLD while five other grade-F holdings say TRIM. `RISK_GRADE_F` is only added when the candidate would have received new money, and H16's score (47.9) is in the hold band, so the holder rule never sees the grade. The ladder (F7) feeds the grade to the holder rule directly.

## Golden scans after F1 and F2 (2026-10-02 afternoon)

**Method.** Two passes.

1. **The Standard scan, baseline then branch, the same afternoon.** These runs could not isolate the code change:
   - the pre-filter's 90 s budget makes the 250-name sample differ between runs (F4);
   - the bulk history download bypasses the response cache;
   - prices moved between runs about 90 minutes apart.

   Rows that appeared or vanished there (for example MSFT at $25k, UNP at max 1) were data drift, not code.
2. **Deterministic comparison** (the one used below):
   - **Universe**: a fixed custom universe of 24 names: every name seen in any run today, plus the 8 live acquisition targets.
   - **Data**: the bulk prefetch is off (`QUANTA_PREFETCH_MIN_UNIVERSE`) and the response-cache TTLs are pinned to 48 h (`QUANTA_YF_HIST_TTL`, `QUANTA_YF_INFO_TTL`), so the second tree reads exactly the first tree's data.
   - **Trees**: the baseline (`4f878f8`) against **`166820d`** (F1 and F2 only) and against **`90a92de`** (F1, F2 and F3, including the universe-level event check).

### Baseline → F1/F2 (`166820d`), identical data

| Variant | Ticker | Action | Shares | Stop | Target | R:R | Levels |
|---|---|---|---|---|---|---|---|
| $2,500 | AMZN | ACCUMULATE (funded) | 1 → 1 | 236.03 → **241.86** | 292.97 → **271.67** | 2.58 → **1.85** | scanner → **research** |
| $2,500 | CSCO | WATCHLIST | 0 | 103.46 → **108.27** | 133.02 → **111.91** | 2.31 → **0.00** | research |
| $2,500 | NVDA | WATCHLIST | 0 | 217.72 → **218.88** | 297.05 → **257.39** | 3.78 → **1.51** | research |
| $2,500 | UNP | WATCHLIST | 0 | 264.45 → **265.58** | 312.01 → **275.73** | 2.25 → **0.00** | research |
| $25,000 | AMZN | ACCUMULATE (funded) | **4 → 6** | 236.03 → **241.86** | | 2.58 → **1.85** | research |
| $25,000 | GOOGL | ACCUMULATE (funded) | **2 → 3** | 318.79 → **330.65** | 400.55 → **365.46** | 2.23 → **1.51** | research |
| $25,000 | HST | WATCHLIST | 0 | 21.12 → **21.86** | 25.39 → **23.52** | 1.81 → **1.09** | research |
| the small-capital case | none | no picks (both) | | | | | |

**F2 sizing (item 6).** Funded names are re-sized on the Research stop, which is tighter, so the share count rises and the dollar risk stays within the budget:
- AMZN: 4 × $15.49 = **$62** → 6 × $10.16 = **$61**.
- GOOGL: 2 × $24.76 = **$50** → 3 × $13.51 = **$41** (whole shares round down).

**F2 acceptance.** Every published row now carries the Research levels, and its card R:R equals the R:R in its binding reason. For example, HST shows "R/R 1.09 below min 1.50" with card R:R 1.09, where the baseline showed 1.81 beside a reason quoting the other engine's number.

**Row presence (not F1/F2).** With max 5 the baseline published 5 of 7 final rows and the branch 5 of 6. Which watch rows survive differs (HST, GOOGL) because `app.py` truncates to `max_positions` after the engine (audit F5, Phase 4).

### Baseline → F1, F2, F3 (`90a92de`), identical data

- **Rejected list (F1 acceptance):** all 8 scanned names with a pending acquisition are published as **AVOID**, each with a reason built from its own filing, in every variant. For example: "LNTH AVOID PENDING_ACQUISITION: Pending cash acquisition at $102.50 (merger agreement 2026-08-03; DEFM14A filed 2026-09-08 on SEC EDGAR)… The contingent value right is non-transferable…". The other seven are AES ($15.00), ITGR ($127.00), TECH ($73.00), WBD ($31.00), and KVUE, D and QRVO ("paid partly or wholly in the acquirer's stock… no fixed deal price").
- **AMZN at $2,500**: funded ACCUMULATE → absent. **F3**: AMZN's measured grade is C on two years of bars with tail and liquidity measured (B on the scanner's old one-year, three-input measure). Grade C's 0.8 size factor with the earnings factor puts the ACCUMULATE size at $95, under the $100 minimum. Data, constants and sizing code were checked identical across trees.
- **NVDA** leaves the book: **F3**. On F1/F2 it was still a published watch row carrying the Research engine's `RISK_GRADE_F`, while the scanner had graded it differently. With one grade, the scanner also measures F, and the book's G2 gate excludes grade F.
- **LNTH** stops being a candidate: **F1** (audit 5.1). Without its pre-deal 52-week high its scanner target and R:R fall. It is published as AVOID instead (above).

## F7: one ladder for both pages (Phase 3)

**Change.**
- **New `engine/ladder.py`, `decide(inputs, holding=None)`** (audit section 6). It composes, in order:
  1. **The corporate-event gate.** PENDING_ACQUISITION or DELISTING → AVOID. An unavailable check, a possible pending deal, or a listing no source covers → no new money.
  2. **The resolver's action**, unchanged.
  3. **The grade policy.**
     - F takes no new money, and is **AVOID when a measured red flag corroborates it** (audit: "grade F and a measured red flag").
     - D is allowed at **half size** (`SIZING.RISK_GRADE_SIZE_MULTIPLIERS`).
  4. **Owner decision Q4.** STRONG_BUY folds into BUY, with the new code `STRONG_BUY_FOLDED`, unless the model's evidence status is VALIDATED (`LADDER.STRONG_BUY_EVIDENCE`).
  5. **For a holder:** `holder_rule`, with the event and the grade, then the concentration trim at `PORTFOLIO.TRIM_REVIEW_POSITION`.
  - It returns the action, the new-money field, the holder action and its basis, the codes and the size multiplier.
- **Scanner.**
  - G2 bans only grade F (`BOOK.BANNED_GRADES = ("F",)`); grade D was banned before.
  - The STRONG_BUY fold happens at analysis time, before the existing re-size, so the name is sized at the BUY tier it is published as.
  - The scanner Decision runs through the ladder.
- **Research engine.**
  - It applies the event gate itself (`_apply_corporate_events`), so the Research page, deep confirmation and My Portfolio read one event-aware verdict. A failing check never raises; it becomes "unavailable".
  - It folds STRONG_BUY after resolution and re-sizes at the BUY tier (its sizer's own 3% / 4% ratio).
  - Its Decision runs through the ladder.
  - Its position sizer now uses the shared grade table: A 1.2 → **1.0**, F 0.3 → **0.0**; B, C and D are unchanged. The sizer's other literal tables (base risk 4/3/2%, drawdown steps, the $500 minimum) are listed for the Phase 8 literal sweep.
- **H16 fix.** A grade-F holding gets the TRIM review at any score. Before, `RISK_GRADE_F` was attached only when the candidate would have taken new money, so a grade-F name with a hold-band score read HOLD.
- **My Portfolio.**
  - The Decision carries `event_check`. The guidance re-checks the holder rule with the event, and has its own wording for the takeover-spread Sell: "Review: the takeover spread has nearly closed". An unknown spread never produces a Sell.
  - An unavailable or failing check never hides a holding and never changes a holder's instruction (item 4a). It only removes new money.
- **One research call per session.**
  - New `engine/research_cache.py`, keyed by (ticker, last completed session, mode), with one configured mode, `RESEARCH.MODE = "full"`. That is My Portfolio's current mode, so **My Portfolio's research is unchanged**. Deep confirmation moves from "fast + insider" to full.
  - Deep confirmation reads and writes the cache. The webapp writes every completed Research analysis to it.
  - Only usable results are cached (data status ok, action present), so a provider failure is retried.
  - The action, levels and grade do not depend on the account size (checked: `account_size` reaches only the position sizer), so a verdict computed for one account serves the other.
  - My Portfolio does not read verdicts computed by Picks, because those were sized for the scan's capital. Phase 8's parity sweep measures what this leaves.
- **India.** The new code `EVENT_CHECK_NOT_COVERED` has the text "No corporate-event source covers this exchange's listings". Its remedy is "None: names on exchanges no event source covers are not funded (owner decision)", not an SEC remedy.
- **Test harness.**
  - The golden-master recorder and replay give the event check one offline answer: clear, except uncovered listings, which keep their real offline answer. Recording and testing therefore agree.
  - `conftest` clears the research cache around every test.

**Research golden master (12).** Re-recorded:
- `decision.event_check` was added: "clear" offline, and `EVENT_CHECK_NOT_COVERED` for RELIANCE.NS. RELIANCE.NS's first blocker is now that reason.
- MSTR and RIVN: holder HOLD → **TRIM** (grade F, hold-band score; the DELL fix). Their primary blocker is now `RISK_GRADE_F`.
- No other field changed.

**Tests.**
- **`tests/test_ladder.py`** (17): one per section-6 row.
  - New money: event AVOID ×2, unavailable → Watch, low score → AVOID, F with red flag → AVOID, F without one → Watch, D half size, `test_strong_buy_folds_until_validated`, clean Buy.
  - Holders: Hold, reduce-band Trim, corroborated Exit, F Trim at any score, concentration Trim, thin-spread Sell, unavailable check unchanged, conditional holder action.
- **`tests/test_ladder_wiring.py`** (7): Research AVOID on a pending deal; Research fails closed when the check raises; holder spread Sell with its wording; an unavailable check never hides a holding; **`test_grade_d_half_size_book`**; session cache hit and miss; one research mode.
- **Updated:** the deep-mode tests now expect the one configured mode, and the India event test expects the not-covered code and no SEC remedy.
- **Results:** Python 1543 passed (local and `TZ=UTC`), JS 389/0.

## Checkpoint 3: My Portfolio before and after (2026-10-02 afternoon)

**Method.** The saved snapshot of your account (18 holdings) was replayed through the real My Portfolio code, three times, on one scratch cache with the response-cache TTLs pinned, so all three runs read the same data:
1. the baseline (`4f878f8`);
2. after F3 (`ca8f10f`);
3. the F7 working tree, committed as F7 right after.

The table holds actions only: no quantities, values or weights.

| Symbol | Action | Basis | Grade | Holder rule | Engine action | Score |
|---|---|---|---|---|---|---|
| H01 | Trim | CONCENTRATION | C | HOLD | WATCHLIST | 54.7 |
| H02 | Trim | RISK_GRADE_F | F | TRIM | WATCHLIST | 65.6 |
| H03 | Trim | RISK_GRADE_F | F | TRIM | WATCHLIST | 66.4 |
| H04 | Trim | RISK_GRADE_F | F | TRIM | WATCHLIST | 67.4 |
| H05 | Trim | RISK_GRADE_F | F | TRIM | WATCHLIST | 70.6 |
| H06 | Trim | RISK_GRADE_F | F | TRIM | WATCHLIST | 66.7 |
| H07 | Hold / No New Money | LOW_REWARD_RISK | **C → B** (F3) | HOLD | WATCHLIST | 65.7 |
| H14 | Hold / No New Money | INVALID_TARGET → **EVENT_CHECK_UNAVAILABLE** (F7) | B | HOLD | WATCHLIST | 62.5 |
| H08 | Hold / No New Money | ACCOUNT_DATA_UNRECONCILED | C | HOLD | ACCUMULATE | 60.5 |
| H09 | Hold / No New Money | ACCOUNT_DATA_UNRECONCILED | C | HOLD | ACCUMULATE | 57.0 |
| H10 | Hold / No New Money | RISK_GRADE_D_HOLD → **LOW_REWARD_RISK** (F3) | **D → C** (F3) | HOLD | WATCHLIST | 60.8 |
| H11 | Hold / No New Money | RISK_GRADE_D_HOLD → **LOW_REWARD_RISK** (F3) | **D → C** (F3) | HOLD | WATCHLIST | 65.0 |
| H12 | Hold / No New Money | LOW_REWARD_RISK | B | HOLD | WATCHLIST | 58.4 |
| H13 | Hold / No New Money | RISK_GRADE_D_CAP → **LOW_REWARD_RISK** (F3) | **D → C** (F3) | HOLD | WATCHLIST | 65.3 |
| H15 | Hold | HOLD_NOT_OWNED | **B → A** (F3) | HOLD | WATCHLIST | 50.2 |
| **H16** | **Hold → Trim** (F7) | HOLD_NOT_OWNED → **RISK_GRADE_F** (F7) | F | **HOLD → TRIM** (F7) | WATCHLIST | 47.9 |
| H17 | Hold | HOLD_NOT_OWNED → **EVENT_CHECK_UNAVAILABLE** (F7) | B | HOLD | WATCHLIST | 47.3 |
| H18 | Unavailable | ANALYSIS_UNAVAILABLE | n/a | n/a | n/a | n/a |

**What changed and why.**
- **One action changed: H16, Hold → Trim (F7).** H16 is grade F (volatility +3, beta +2, drawdown +2). Its score, 47.9, is in the hold band, and the old code attached `RISK_GRADE_F` only to names that would have taken new money, so the holder rule never saw the grade. Five other grade-F holdings already read Trim. This is the inconsistency logged in Phase 0.
- **Five grades changed, all from F3's winsorized tail.** One gap day no longer counts twice:
  - H07 C → B (drivers now volatility +1, drawdown +1);
  - H15 B → A (no driver adds points);
  - H10, H11 and H13 D → C (the tail point is gone).

  **No action changed.** All three D → C names stay "Hold / No New Money". Their basis moves from the grade-D code to `LOW_REWARD_RISK`, the rule that now blocks new money first.
- **Two bases changed, from the event gate (F7):** H14 and H17. The company ticker lookup did not identify these funds, so the check is unavailable, and fail-closed lists it first. Actions are unchanged: Hold / No New Money and Hold. See the open question on ETFs.
- **Unchanged:** every engine action, every new-money field, and the five grade-F Trims (H02, H03, H04, H05, H06).

**Same-day data check.** This afternoon's baseline replay differs from this morning's live table on H14 (score 51.6 → 62.5, so Hold → Hold / No New Money with INVALID_TARGET) and on the D-code order of H10 and H13. That is market data that moved since the morning, on the baseline code. This is why the comparison above uses three replays of the same data, not the morning table.

## Incident: the test suite wrote two production files (found 2026-10-02, 16:4x)

**What happened.** The end-of-phase check found two files changed.
- `ui/stock-analysis/data/cache/yf_cache.json` went from 34,755,507 bytes (last written 2026-10-01 21:43) to 171,605 bytes.
- `ui/stock-analysis/webapp/.regime_history.json` got a fake row for today (US 2026-10-02: VIX, participation and ten-year all null).

The ledger, `yf_cache.pkl` and `portfolio_journal.json` are unchanged (same size, time and sha256 as at 08:31). No other non-source file under the data directories changed.

**Cause.** Many existing Python tests import `app`. At import, `app` loads `<QUANTA_CACHE_DIR or data/cache>/yf_cache.json`. At exit it saves its in-memory response cache there, keeping only entries still within their TTL. It also upserts today's regime snapshot into a fixed path next to the webapp. `conftest` isolated the ledger but not these two files. Every full-suite run that added a cache entry therefore pruned the production cache file to the run's fresh entries. Phase 0's check passed because that run added none.

The regime file already held fake rows from before this session (2026-09-11 and 2026-09-22: VIX 14.0, ten-year 100.0), so that leak predates this work. My runs today also wrote to it.

**Impact.**
- **Cache:** a response cache whose entries live 15 minutes (history) to 1 hour (info). Entries written on 2026-10-01 were already expired, and `app` discards expired entries at load, so nothing usable was lost. The next scan simply starts cold.
- **Regime history:** today's US row is fake until the running server next loads the Overview, which upserts today's row with live values. The older fake rows from before this session are still in the file.

**Fix (this commit, test infrastructure only).**
- `conftest` forces `QUANTA_CACHE_DIR` to a temporary directory and `QUANTA_YF_DISK_CACHE=0`, before anything imports `app`.
- `app.py` reads the regime history path from `QUANTA_REGIME_HISTORY_PATH` (default unchanged), and `conftest` points it at the temporary directory.
- New guard, `tests/test_no_production_cache_writes.py` (3): the suite's cache directory is temporary and off, and the regime history path is not the live file.
- Verified: a full run leaves both production files byte-for-byte unchanged (size and modification time identical before and after).

**Not done without your say-so:** restoring the old cache contents, and removing the older fake regime rows. See the report.

## Unresolved (running list)

- **Branch-only behaviour not ported (F15):** the scan-worker cooldown and circuit (5 tests), the blue-sky target (1), and insider calibration (8). Recommendation: a separate decision on whether to port `quanta-review-redesign`'s scan-worker and insider work. It is not part of this audit.
- **H16** grade F with holder HOLD: cause found in F3 (see F3), fixed in F7.
- **Checkpoint 4 (F1):** resolved 2026-10-02: `QUANTA_SEC_CONTACT` is set in the user environment. Without it the event gate funds nothing. India (NSE) has no corporate-event source. **Owner decision 2026-10-02: fail closed as written.** India names are never funded, and each row says why.
- **Holder Sell wiring:** done in F7. **The Research engine's positive M&A catalyst score** for flagged names is moot when the name is AVOID (F7 applies the event gate after scoring), but the score itself still reads M&A as positive. Left for review.
- **Stale universe lists (F4):** 42 of 806 universe tickers no longer map to an SEC registrant (acquired, renamed or taken private). They fail closed today; the lists themselves belong to F4.
- **India screen literals (F9 sweep):** `SP_ELITE_PRO.py` India price / volume / market-cap screens (`MIN_PRICE_INR` etc.) are still inline. To move in the Phase 8 sweep.
- **Checkpoint 3 (open):** H16 Hold → Trim; H07, H15, H10, H11 and H13 grade changes; H14 and H17 basis. See Checkpoint 3.
- **ETFs and the event gate (decision needed):** funds are not in the SEC company list, so every ETF (H14, H17, QQQ, TLT…) is "check unavailable" and is never funded on Picks. Options: keep fail-closed, or cover funds from SEC's fund list (closures are filed as 497 supplements).
- **Research engine position sizer literals (Phase 8 sweep):** base risk by signal, the drawdown steps, the $500 minimum and the 10% volume cap are still inline in `PositionSizer`.
- **My Portfolio reads its own research, not Picks':** Picks re-uses a My Portfolio verdict from the same session, but not the reverse (sizing is account-specific). Phase 8 measures parity.
- **Production files written by the test suite (incident, see above):** decide whether to restore the response cache (no usable entries were lost) and whether to remove the fake regime-history rows (some predate this session).

## Continuation verification (2026-10-02)
- Retained task output confirms the completed 18-holding baseline/HEAD/HEAD replay: repeated HEAD results match; H16 is the sole changed action, Hold to Trim. Accepted by the owner.
- Risk points include a fixed baseline of 3: baseline H16 penalties sum to 8 (raw 11, capped 10); HEAD penalties sum to 7 (total 10); both F.
- My Portfolio uses a separate pre-event verdict for explicit ETF, MUTUALFUND, CLOSED-END-FUND and CEF types; unknown types and old cached results retain fail-closed behavior.
- Shared Research/Picks decisions and funding rules are unchanged. Portfolio guidance exposes the non-blocking not-applicable text.
- Validation: 337 Python and 433 JS tests passed, including offline golden replay, acquirer and India coverage. Only a portfolio-specific field was added to the two fund snapshots.
- Post-contact live F1/F2 acceptance already ran: see the live sweep and pinned-data golden sections above. No new live sweep was performed in this continuation.
- Removed account-derived log examples; notes contain no account amounts or lots. Old local Git history remains unchanged and must remain unpushed.
- Scratch account files and baseline worktree are absent; no scratch listener remains. Port 5500 was left untouched.
- Order-cap increase is proposed only. Phase 4, merge, push and history rewrite were not performed.
