# QUANTA Elite — End-to-End Test Suite & Production-Readiness Review

> **Status: the original test plan, kept because the e2e specs cite its case IDs.** It was written
> before the suites existed, so its file paths (`ui/stock-analysis/...`) and its "no jsdom/Playwright
> suite" gap are out of date. The suites that exist now are `tests/unit/` (jsdom), `tests/e2e/`
> (Playwright) and the Python suite in the repository's `tests/`; see the root README, "Testing".

> Scope: `ui/stock-analysis/webapp` (Flask + vanilla JS SPA). Three views — **Dashboard**, **Portfolio Builder** (`#view-picks`), **Stock Deep-Dive** (`#view-analyze`). Reviewed as a financial trading-intelligence product, not just a website.
>
> Test app at `http://127.0.0.1:5500`. Backend routes: `/`, `/api/dashboard`, `/api/market-intel`, `/api/engines`, `/api/health`, `/api/cache/stats`, `/api/cache/clear`, `/api/stock-picks` (POST), `/api/analyze` (POST + job), `/api/jobs/<id>`, `/api/jobs/<id>/stream` (SSE).
>
> Data provider: Yahoo Finance (rate-limited, ~350 ms throttle) + public RSS. **All numbers are model-derived heuristics, NOT backtested signals** — this is the single most important thing the test suite must keep the product honest about.

---

## 0. How to read this suite

| Field | Meaning |
|---|---|
| **ID** | `AREA-NNN`. Areas: `DASH` (dashboard), `PB` (portfolio builder), `DD` (deep-dive), `DATA` (data/calc integrity), `UX` (ui/ux/a11y), `FUNC` (navigation/functional), `PERF` (performance/reliability), `SEC` (security/edge). |
| **Priority** | Critical / High / Medium / Low. *Critical = blocks release or risks misleading a user into a real money decision.* |
| **Type** | Functional / UI / Data / Regression / Accessibility / Performance / Security. |
| **Automation** | Playwright (E2E/browser), Cypress (alt), unit (`node --test`), API (pytest/`requests`), manual. |

**Existing harness to build on (do not reinvent):**
-- JS pure-function units: `node --test ui/stock-analysis/webapp/tests/ui_render.test.cjs` and `pro_watchlist.test.cjs` (functions exported from `app.js` `module.exports`).
-- Backend: `pytest ui/stock-analysis/tests/` (test_fast_mode, test_provider_resilience, test_scan_health, test_decision_core, …).
-- Syntax gate: `node --check ui/stock-analysis/webapp/static/app.js`.
- **Gap:** there is currently **no jsdom/Playwright suite** exercising the render functions or full DOM. Section 11 fixes this.

---

## 1. Landing Page / Dashboard

Sections (top→bottom, by DOM id): `#hero-strip` (regime hero) → `#strategist-brief` → `#market-pulse` → `#decision-strip` → `.cc-tradedesk` (`#portfolio-scan-body` Qualified, `#setup-status-body` Veto, `#allocator-body` Portfolio Action) → `#news-body` (Market Impact Radar) → `#movers-body` → `#event-risk-body` (Catalyst Calendar) → `#source-ledger-body` (Source Health). Topbar: market toggle (`.mkt-btn`), nav tabs (`.nav-tab`), clock (`#market-clock`), freshness (`#freshness-chip`), theme (`#theme-toggle`), refresh (`#refresh-dashboard`).

| ID | Scenario | Preconditions | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|---|
| DASH-001 | All 9 dashboard sections render on load | Server up, `/api/dashboard` returns 200 | Load `/`; wait for skeletons to clear | Every section id has non-skeleton content; **no `[role="alert"]` "Offline — retry once the backend is reachable" banner**; `console` has 0 uncaught errors | Critical | Functional | Playwright | All 9 ids populated, 0 console errors, 0 offline banner |
| DASH-002 | Hero shows regime label, posture, action, signal-check | Dashboard loaded | Inspect `#hero-strip` | Contains: posture badge (e.g. "Selective Offensive"), serif title (e.g. "Constructive — Selective"), "Today's action" directive, segmented signal ring with center `N/4`, 4 signal rows with state badges (Confirmed/Neutral/Warning), and Outlook-Triggers (strengthens/weakens) | Critical | UI/Data | Playwright | Label, ring `N/4`, exactly 4 signal rows, 2 triggers present |
| DASH-003 | Signal-ring count equals confirmed factor count | `tone_factors` present in payload | Read ring center `N`; count `tone_factors` with `score>0` | `N` == number of positive-score factors; ring lit-arc count == `N`; total == 4 | Critical | Data | Playwright+unit | `computeRegimeModel().alignment.aligned === N`; lit arcs === N |
| DASH-004 | Regime label is graduated, not binary | Payload net = +2 with one opposing/neutral factor | Read hero title | Shows "Constructive — **Selective**" (not full "Constructive Market"); posture "Selective Offensive" — i.e. `regimeLadder` graduates by factor vote | High | Data | unit | `regimeLadder(2,0,2)` → Selective; never labels Selective as full Offensive |
| DASH-005 | Hero ↔ Portfolio Action are not contradictory | Dashboard loaded | Compare `#hero-strip` posture vs `#allocator-body` exposure band/headline | A "Constructive/Selective Offensive" hero must NOT pair with a "Reduce exposure" allocator. Exposure band must come from the SAME `computeRegimeModel` | **Critical** | Data | Playwright | Posture tone and exposure headline directionally agree (regression on the historical contradiction bug) |
| DASH-006 | Confidence/strength is honestly framed (no fake %) | Dashboard loaded | Inspect hero meter + signal summary | Strength shown as factor **vote** (`x/4`, `2▲ 0▼ 2—`) and a plain-English summary; **no fabricated "87% confidence"** number | High | Data | Playwright | No `%`-confidence string in hero; vote present |
| DASH-007 | Market Pulse tiles: value, change, label, color, interpretation | `quotes` present | Inspect `#market-pulse` `.cc-pl-tile` | Each tile shows category tag, instrument name, value, signed change, and a one-word/short read (e.g. "VIX 21.5 — not risk-on confirmation"); color state (`.bull/.bear/.warn/.neutral`) matches sign/level | High | UI/Data | Playwright | Every tile has value+change+read; color class matches change sign; VIX read maps to level |
| DASH-008 | Market Pulse change color matches sign | Quotes with mixed +/- | For each tile assert color | `change_pct > 0` → bull/green accent; `< 0` → bear/red; `0`/N.A → neutral; volatility (VIX) inverted semantics labeled correctly | High | Data | unit | `pulseInterp` returns correct tone per instrument |
| DASH-009 | Market Pulse grid has no trailing blank/ragged columns | US market (10 tiles) | Resize desktop ~1372px | Tiles flow `auto-fill minmax(228px,1fr)` → clean rows, no orphaned blank cell | Medium | UI | Playwright | 0 empty grid cells; no horizontal scroll |
| DASH-010 | Strategist's Read adds non-duplicated synthesis | Dashboard loaded | Read `#strategist-brief` | Shows market TYPE (investor's/stock-picker's/two-way/defensive), where RISK HIDES, favored setups, today's focus — and does **not** restate the hero verbatim | Medium | Data | unit | `strategistBrief(model)` fields populated, distinct lead text from hero sub |
| DASH-011 | Today's Decision: primary action, reason, positioning, avoid, risk-rule, triggers, 3 CTAs | Dashboard loaded | Inspect `#decision-strip` | All present; reason text coherent with posture (positive posture + high macro risk reads "Selective…demand confirmation", not a contradiction); 3 CTA buttons clickable | Critical | Functional/Data | Playwright | 5 playbook fields + 2 triggers + 3 CTAs, reason non-contradictory |
| DASH-012 | Signal Check logic: confirmed / neutral / warning / opposed states | Factors with mixed scores | Inspect 4 `.cc-sig-row` state badges | `score>0`→Confirmed (emerald), `score<0`→Warning (amber, opposing styled amber not red), `score==0`→Neutral (slate); strength pips/`x/4` match | High | Data | unit | `factorState(score)` maps sign→{cls,label}; strengths [3/4,2/4,…] match |
| DASH-013 | "What Changed Today" is honest (real prior reference) | `.regime_history.json` has ≥2 days | Inspect decision "what changed" | Shows real deltas (regime flip, VIX ±, participation ±, 10Y ±) vs prior day; on day 1 shows "Baseline captured — changes appear tomorrow" (NOT fabricated) | Critical | Data | API+Playwright | Day-1 baseline message OR real diffs; never invented numbers |
| DASH-014 | Trade Desk — Qualified Setups | Scan data available | Inspect `#portfolio-scan-body` | Lists candidates OR educational empty-state (5 fail reasons + "no trade protects capital"); each row has the "screen candidates, NOT validated buy signals" caveat | High | Data/UI | Playwright | Caveat present; empty-state educational |
| DASH-015 | Trade Desk — Veto List with re-validation | Veto items present | Inspect `#setup-status-body` | Each veto row shows reason + "↺ Can become valid: …" (`vetoRevalidation`) without clipping ("TOO EXTENDE…" must not be cut) | High | UI/Data | Playwright | Reason 2-line clamp, re-validation line wraps, no clipped text |
| DASH-016 | Trade Desk — Portfolio Action / allocator | Dashboard loaded | Inspect `#allocator-body` | Gross-exposure band + headline + 4 action bullets, sourced from `computeRegimeModel().exposure`; matches regime band table (Offensive 80-100 … Def 25-45) | Critical | Data | unit | Exposure band matches posture per `regimeExposure` |
| DASH-017 | Market Impact Radar (news) tabs + per-item meaning | News present | Click each `.seg-btn` (High Impact/Macro/Stock-Specific/Earnings) | Filters news group; each item shows relevance High/Med/Low (tooltip: "keyword relevance, not a measured market-impact score") + MEANS/DO lines; impact-chain shows "Confirmed N/N" vs "Hypothesis" | High | Functional/Data | Playwright | All 4 tabs switch; relevance honestly labeled; no fake numeric impact score |
| DASH-018 | Moves That Matter (movers) | `movers` present | Inspect `#movers-body` | Top gainers/losers with %; click row → opens tear sheet for ticker | Medium | Functional | Playwright | Rows render; click navigates to `#view-analyze` w/ ticker |
| DASH-019 | Catalyst Calendar splits scheduled vs live | `earnings_week` + `macro` present | Inspect `#event-risk-body` | Divider "Scheduled · dated earnings" vs "Live · just-broken macro headlines"; dates valid, day-of-week matches date | Medium | Data | Playwright+unit | Sections labeled; no date/day-of-week mismatch |
| DASH-020 | Source Health quiet-when-healthy | Sources ratio ≥ 0.8 | Inspect `#source-ledger-body` | Single quiet line "Sources healthy · N/M live"; only degraded shows full confidence block + % | Medium | UI/Data | Playwright | Healthy→one line; degraded→detailed block |
| DASH-021 | Data Confidence reflects real source freshness | Stale/missing source | Force one feed stale | Stale-data warning surfaces; confidence drops; timestamps shown | High | Data | API+Playwright | Stale source → visible warning, not silent |
| DASH-022 | Section copy / icons / badges accurate | Dashboard loaded | Audit each section header + badges | Titles match function (no leftover placeholder "Waiting"/"Fetching" stuck after load); icons semantically correct | Medium | UI | Playwright | No badge stuck on loading text post-load |
| DASH-023 | No stale / duplicated / inconsistent data across sections | Dashboard loaded | Cross-check breadth/participation across hero, pulse, decision | "Index breadth N/4" (pulse, equity-only) vs 4-factor participation must not contradict; the same VIX value everywhere | Critical | Data | Playwright | Cross-section values reconcile; no duplicate-but-different numbers |
| DASH-024 | Freshness chip + clock update | Dashboard loaded | Watch `#freshness-time`, `#clock-text` | Clock ticks (ET/IST per market); freshness shows "just now" then relative time; updates on refresh | Low | Functional | Playwright | Clock increments; freshness updates after refresh |

---

## 2. Portfolio Builder (`#view-picks`)

Inputs: `#sp-market`, `#sp-capital` (min 100, step any), `#sp-scan` (universe/scan-type optgroups US/IN + Custom), `#sp-custom-tickers`, `#sp-risk` (conservative/balanced/aggressive), `#sp-positions` (1–20), `#sp-diversify`. Submit `#btn-scan` → POST `/api/stock-picks`. Results in `#picks-results`.

| ID | Scenario | Preconditions | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|---|
| PB-001 | Happy-path scan returns ranked position list | US, capital 2500, quick scan | Fill form, submit, wait for job | `#picks-results` shows ranked picks with ticker, capital, %port, shares, risk$, risk%cap, score; progress bar completes | Critical | Functional | Playwright+API | ≥1 funded pick OR honest empty-state; totals reconcile |
| PB-002 | Capital below minimum rejected | — | Enter `50` in `#sp-capital`, submit | Browser min-validation (min=100) blocks OR backend rejects with clear message; no NaN positions | High | Functional | Playwright | Submit blocked or graceful error; no silent garbage |
| PB-003 | Custom tickers field toggles + validates | Scan = "Custom Tickers…" | Select custom; `#custom-tickers-field` un-hides; enter `AAPL, MSFT` | Field appears; valid tickers scanned; empty custom → validation message | High | Functional | Playwright | Field visibility toggles; empty custom guarded |
| PB-004 | Invalid ticker in custom list | Custom = `FAKE123, AAPL` | Submit | FAKE123 skipped/flagged as invalid (not mislabeled rate-limited); AAPL still processed | High | Data | API | Invalid surfaced as `invalid_ticker`, valid still ranked |
| PB-005 | Max positions bound (1–20) honored | positions = 5 | Submit | Result count ≤ 5; entering 25 clamps to 20 or rejects | Medium | Functional | Playwright | Picks ≤ max; out-of-range clamped |
| PB-006 | Position sizing math is correct | Funded pick returned | For each pick verify | `shares = floor(capital_share / entry)` (whole-share mode); `risk$ = shares × (entry − stop)`; `%port = capital/total`; sum(capital) ≤ budget; sum(risk) honest | **Critical** | Data | API+unit | Per-pick arithmetic verified; allocated+cash == capital |
| PB-007 | Sub-1-share pick surfaced honestly | Capital too low for some entry | Submit low capital | Sub-1-share name shows intended $ but **0 shares / $0 risk / $0 executable** (engine honest), not a phantom funded position | High | Data | API | 0-share pick: shares=0, risk=0, executable=0 |
| PB-008 | Sector diversification cap | Diversify checked, aggressive single-sector universe | Submit | Single-sector exposure capped per mandate; sector exposure shown | High | Data | API | No sector exceeds cap when diversify on |
| PB-009 | Recommendations match current market regime | Dashboard regime = defensive | Run scan | Exposure guidance / risk warnings align with regime (defensive → lower gross, tighter); not "go aggressive" in a risk-off tape | **Critical** | Data | API+Playwright | Scan posture consistent with dashboard regime band |
| PB-010 | Risk profile changes output | Same universe | Run conservative vs aggressive | Conservative → fewer/tighter positions, lower risk%; aggressive → larger sizing within caps | Medium | Data | API | Output differs sensibly by mandate |
| PB-011 | Decimal / negative / zero capital edge cases | — | Enter `2500.55`, `-100`, `0` | Decimal accepted; negative/zero rejected; no negative shares | High | Security/Data | Playwright | Negatives blocked; decimals handled |
| PB-012 | Duplicate custom tickers de-duped | Custom = `AAPL, AAPL, aapl` | Submit | Single AAPL entry; case-insensitive de-dupe | Medium | Data | API | One AAPL in results |
| PB-013 | Market switch US↔IN changes universe options | — | Toggle `#sp-market` | `#sp-scan` optgroups swap (US ↔ NSE); capital hint updates | Medium | Functional | Playwright | India scans show Nifty/Sensex options only |
| PB-014 | Scan failure / 100% provider errors not hidden as "no setups" | Force provider rate-limit | Run during throttle | Red failure banner + error details + safe-mode retry; does NOT show "No buy-qualified setups" or leak stale picks | **Critical** | Data | API | `scan_status=failed/provider_rate_limited` → failure banner, no stale picks |
| PB-015 | Safe-mode retry path | After a rate-limited scan | Click safe-mode retry | Re-runs `quick` + `safe_mode=true` (2 workers); succeeds or clean cooldown message | High | Functional | Playwright | Safe-mode scan completes |
| PB-016 | Empty portfolio / no qualifying setups | Genuine 0-pick market | Run scan | Educational empty-state ("no trade protects capital"), invariants: alloc=$0/cash=capital/positions=0 | High | Data/UX | Playwright | Empty-state shown, invariants hold |
| PB-017 | Large universe (Full 200 / Nifty 500) performance & stability | — | Run Full scan | Completes or partial-with-picks; progress bar advances; no UI freeze | Medium | Performance | API | No timeout-crash; partial results acceptable |
| PB-018 | Rebalance / exposure guidance present | Picks returned | Inspect results extras | Capital-allocation layer + watchlist + exposure guidance render; rebalance suggestions sensible | Medium | Data | Playwright | Guidance section present and coherent |
| PB-019 | Input persistence within session | Filled form | Switch view away and back | Form values retained (capital, scan, risk) | Low | Functional | Playwright | Inputs persist on view switch |
| PB-020 | Progress + cancel/repeat behavior | Long scan running | Submit again mid-scan | No duplicate concurrent jobs corrupting results; serialized (`_analyze_lock`) | Medium | Reliability | API | No double-submit corruption |

---

## 3. Stock Deep-Dive (`#view-analyze`)

Inputs: `#an-ticker`, quick chips (`.chip-btn`), `#an-fast-mode` (default checked). Submit `#btn-analyze` → POST `/api/analyze` (async job, SSE `/api/jobs/<id>/stream`). Result in `#an-results` via `renderAnalysis`.

| ID | Scenario | Preconditions | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|---|
| DD-001 | Fast-preview tear sheet renders fully | Fast mode on | Enter AAPL, submit | Full tear sheet paints (hero price, reco, score, chart, ~28 cards); orange "Fast preview" info banner (uses `.error-box` class but is INFO); "Load full analysis" button present | Critical | Functional | Playwright | Tear sheet body >9k chars, reco+price shown, no offline |
| DD-002 | renderAnalysis never silently fails | Any valid ticker | Complete a job, call `window.renderAnalysis(result)` in page context wrapped in try/catch | No `ReferenceError` (regression on `displayRecoLabel` bug); tear sheet paints, bodyLen grows past skeleton | **Critical** | Regression | Playwright | renderAnalysis throws 0 exceptions; body painted |
| DD-003 | "Load full analysis" upgrades preview | Fast preview shown | Click `[data-load-full-analysis]` | Re-runs `fast_mode=false`; news, insider, analyst, peers populate; reco may refine | High | Functional | Playwright | Full sections fill; no duplicate render |
| DD-004 | Quick chips populate + run | — | Click NVDA chip | `#an-ticker` = NVDA, analysis runs | Low | Functional | Playwright | Chip sets ticker and triggers |
| DD-005 | Buy/Hold/Avoid output is consistent w/ signal | Result with `final_action` | Inspect reco label vs signal | Displayed reco == `final_action`; never shows BUY while body says "DO NOT BUY"; signal==final_action | **Critical** | Data | API+unit | `classifyFinalAction` consistent; no contradiction |
| DD-006 | Data-Quality FAIL gate | NVDA (reliable DQ=FAIL fixture) | Run full | Banner "PROVISIONAL — DATA QUALITY FAIL"; reco forced "PROVISIONAL HOLD — DO NOT BUY NOW" when low confidence; targets relabeled "(Not Validated)"/"preliminary"; hero tone neutral | **Critical** | Data | API+unit | `dataQualityGate` drives provisional-hold; targets muted |
| DD-007 | Confidence scoring + risk grade alignment | Result returned | Inspect confidence %, risk grade | Grade F blocks new money → HOLD/WATCHLIST; D trims one notch; negative upside → HOLD; values internally consistent | Critical | Data | API+unit | Risk-grade rules applied per `apply_risk_overrides` |
| DD-008 | Setup quality / chase risk / event risk / veto logic | Extended or pre-earnings name | Run | Chase-risk + event-risk flags shown; veto logic explains why not to chase; earnings within window flagged | High | Data | API | Flags present and explainable |
| DD-009 | Price chart scale is price-driven | Any name w/ far target | Inspect `renderPriceChartCard` | Vertical scale from closes + current price; out-of-range target/stop CLAMPED (not autoscaled flat); badge labeled "Latest · 6M" not target | High | UI/Data | Playwright+unit | Price line fills chart; badge correctly labeled |
| DD-010 | ETF correctness (XLK/QQQ/VTI) | ETF ticker | Run XLK | asset_type=etf; Piotroski/insider/earnings show "N/A (ETF)" (not misleading `--`); blue ETF caveat banner | High | Data | API+unit | `assetTypeMeta` etf; N/A applied; caveat shown |
| DD-011 | ADR correctness (TSM) | ADR ticker | Run TSM | asset_type=adr + Taiwan; purple ADR caveat; valuation caveat | Medium | Data | API+unit | `assetTypeMeta` adr; caveat present |
| DD-012 | Financial correctness (C/JPM) | Financial ticker | Run C | asset_type=financial; amber valuation caveat (book-value framing) | Medium | Data | API+unit | financial classified; caveat present |
| DD-013 | High-volatility overlay (NVDA) | High-vol name | Run | HighVol chip overlays on top of common_stock; not mutually exclusive | Low | Data | unit | overlay applied independently |
| DD-014 | Invalid ticker fast-path | — | Enter `FAKE123` | Fast (~1s) honest error `invalid_ticker`, retryable=false; NOT misclassified as rate-limited; no 50s hang | **Critical** | Data | API | status=error type=invalid_ticker attempts=1 |
| DD-015 | Provider rate-limit during deep-dive | Force cooldown | Run TSM under cooldown | Orange rate-limit banner w/ live countdown; "Retry in safe mode" disabled until cooldown; "Use cached data" option; stale-cache fallback labeled | High | Data | API+Playwright | `provider_rate_limited` detail; countdown ticks; cache fallback labeled |
| DD-016 | Partial data / missing sections | Name with missing fundamentals | Run | Missing sections show "N/A"/"insufficient data" not blanks or `undefined`/`NaN`; required-section audit banner lists gaps | High | Data | API+unit | `computeRequiredSectionAudit` lists missing; no NaN |
| DD-017 | Stale data warning | Served from stale cache | Force stale | "Using cached data from HH:MM:SS" banner | Medium | Data | API+unit | `tearSheetCacheBannerHtml` shown when used_cached_data |
| DD-018 | Calculations explainable & non-contradictory | Result returned | Read bull/bear case, targets, upside | Bull case references THIS ticker's metrics (no template bleed/wrong ROE); upside = (target−price)/price; targets ordered bear<base<bull | Critical | Data | API+unit | `upsidePct` matches; targets monotonic; no cross-ticker bleed |
| DD-019 | RR / trade plan integrity | Trade setup present | Inspect entry/stop/target | RR = (target−entry)/(entry−stop) net of friction; RR verdict label matches; stop < entry < target | High | Data | unit | `computeRR`/`rrVerdict` correct |
| DD-020 | Repeated search performance | Warm cache | Run AAPL twice | 2nd run fast (cache); identical result; no duplicate job | Medium | Performance | API | 2nd run ≪ 1st; result stable |

---

## 4. Data Validation (cross-cutting, the strict layer)

| ID | Scenario | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|
| DATA-001 | Every displayed value traces to source/formula | Snapshot `/api/dashboard` + `/api/analyze` JSON; diff vs rendered DOM | No DOM number exists that isn't in the payload or a documented derivation | Critical | Data | Playwright+API | 0 untraceable numbers |
| DATA-002 | Regime factor math | Feed known `tone_factors` to `computeRegimeModel` | aligned/bull/bear/neutral counts + posture + exposure exact | Critical | Data | unit | matches expected for fixtures |
| DATA-003 | Percent/price formatting | Drive `fmtPct`, `upsidePct` | `+0.6%`, `+70.0%`, fractional vs percent handled; no `NaN%`/`undefined%` | High | Data | unit | matches existing assertions + NaN guard |
| DATA-004 | Threshold/status mappings | Drive `factorState`, `pulseInterp`, `actionMeta`, `classifyFinalAction` | Sign/level → correct label/tone for full boundary set (incl 0, exactly-at-threshold) | High | Data | unit | boundary cases correct |
| DATA-005 | Regime ↔ portfolio ↔ setup ↔ stock reco alignment | For one session, capture dashboard regime, PB exposure, a DD reco | All directionally consistent (no risk-on dashboard + risk-off everything else) | **Critical** | Data | API+Playwright | cross-product coherent |
| DATA-006 | Timestamps & freshness | Inspect `data_source`, cache_timestamp, last_success_time | ISO/locale valid; "Latest · 6M" labels correct; future-dated values rejected | High | Data | API | no future timestamps; labels correct |
| DATA-007 | Fallback / degraded data labeled | Degrade SPY benchmark | "N/A (degraded)" shown, not silent 0.0 | High | Data | API | degraded explicit |
| DATA-008 | Price-scale sanity (ETF vs futures, instrument notation) | Audit pulse + chart values | No ETF price shown at futures scale or vice-versa; instrument labels correct | Medium | Data | manual+Playwright | scale-consistent |
| DATA-009 | Allocation totals | Sum PB picks | allocated + cash == capital (both share modes) | Critical | Data | API | exact reconciliation |
| DATA-010 | Content not misleading | Read all generated narrative | No claim of "validated"/"backtested" signal; "Backtest status: Not validated" honesty preserved; "NOT investment advice" footer present | **Critical** | Data | Playwright | honesty strings present, overselling absent |

---

## 5. UI / UX & Design

| ID | Scenario | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|
| UX-001 | Desktop layout integrity (1440px) | Load each view | 0 overlap, 0 horizontal scroll, 0 clipped text; visual hierarchy hero>brief>pulse | Critical | UI | Playwright | `scrollWidth<=clientWidth`; no overlap |
| UX-002 | Tablet (820px) | Resize | Grids collapse (tradedesk 3→2, pulse, hero ledger) gracefully; no overlap | High | UI | Playwright | no hScroll/overlap at 820 |
| UX-003 | Mobile (390px) | Resize | Single-column flows; tap targets ≥40px; ticker strip placeholder pill shown (never hidden on empty) | High | UI | Playwright | no hScroll; tape visible |
| UX-004 | Dark theme | Default load | Cards lift off canvas; text contrast adequate; signal/risk colors correct (green opp / red risk / amber caution) | High | UI | Playwright | computed contrast OK (note: composite rgba over canvas manually) |
| UX-005 | Light theme | Toggle `#theme-toggle` | Canvas is cool-slate (not washed-out near-white); cards have layered shadow separation; title color vivid emerald/red/amber `!important` wins | High | UI | Playwright | light title computed `rgb(5,150,105)` for bull; no washed-out flat cards |
| UX-006 | Theme persists | Toggle, reload | `localStorage.quanta_theme` honored on reload; `data-theme` set pre-paint (no flash) | Medium | UI | Playwright | theme stable across reload |
| UX-007 | Loading / skeleton states | Throttle API | Skeletons show then resolve; no permanent skeleton; no layout shift jump | High | UI | Playwright | skeletons clear; CLS minimal |
| UX-008 | Empty states | No data per section | Each empty-state is informative (not blank); compact variants render | Medium | UI | Playwright | every section has empty-state copy |
| UX-009 | Error states | Kill backend | "Offline — retry once the backend is reachable" banner + Retry button; recovers on retry | High | UI | Playwright | offline banner + working retry |
| UX-010 | Hover / focus / disabled states | Tab + hover interactive els | Visible hover + focus ring; disabled buttons non-interactive + styled | High | Accessibility | Playwright | focus-visible ring on all controls |
| UX-011 | Keyboard navigation | Tab through page | Logical order; skip-link (`.skip-link`) works; all actions reachable w/o mouse; modals trap focus | Critical | Accessibility | Playwright | full keyboard operability |
| UX-012 | ARIA / semantics | Audit | `role=tablist/tab`, `aria-label`s, `aria-live` on freshness/source-status, `aria-pressed` on theme; landmarks present | High | Accessibility | axe + Playwright | 0 critical axe violations |
| UX-013 | Color contrast (WCAG AA) | Run axe | Text ≥4.5:1, large ≥3:1, in both themes | High | Accessibility | axe | 0 contrast failures (verify tinted badges manually) |
| UX-014 | Screen-reader readout | NVDA/VoiceOver pass | Section names, values, state badges announced meaningfully; icons `aria-hidden` | Medium | Accessibility | manual | coherent SR narration |
| UX-015 | No overlap / clipping / hidden content | All viewports | Badges (e.g. "Risk-first", veto reasons) fully visible; no `overflow:hidden` cut text | High | UI | Playwright | 0 clipped elements |
| UX-016 | Typography & spacing consistency | Audit | Serif for display headings only; Inter body; JetBrains Mono numbers; consistent scale (hero ~34px) | Low | UI | manual | type system consistent |
| UX-017 | Reduced motion | `prefers-reduced-motion` | Marquee/animations respect setting (or non-essential) | Low | Accessibility | manual | no motion when reduced |

---

## 6. Functional / Navigation

| ID | Scenario | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|
| FUNC-001 | View switching | Click each `.nav-tab` | Only one `.view.active` visible; **never** two views stacked (regression on `#view-dashboard{display:flex}` ID-specificity bug — must scope to `.view.active`) | **Critical** | Regression | Playwright | exactly 1 visible view per tab |
| FUNC-002 | Deep-link by hash | Load `/?#analyze` | Opens Deep-Dive view directly | Medium | Functional | Playwright | hash routes to analyze |
| FUNC-003 | Refresh button | Click `#refresh-dashboard` | Re-fetches with `refresh=1`; freshness updates; spinner then done | High | Functional | Playwright | data refetched, timestamp bumped |
| FUNC-004 | Market US↔IN switch (topbar) | Click `.mkt-btn[data-market=IN]` | Dashboard reloads India data; clock→IST; pulse instruments swap; active state moves | High | Functional | Playwright | India payload loads, US deselected |
| FUNC-005 | Theme toggle | Click `#theme-toggle` | Theme flips; `aria-pressed` updates | Medium | Functional | Playwright | toggles + persists |
| FUNC-006 | News group tabs | Click each `.seg-btn` | Active state moves; news list filters | Medium | Functional | Playwright | filter works per group |
| FUNC-007 | Tear-sheet creation from dashboard | Click a mover/pulse ticker | Navigates to Deep-Dive, runs that ticker | High | Functional | Playwright | ticker opens in analyze |
| FUNC-008 | Clear cache FAB | Click `#clear-cache` → confirm | POST `/api/cache/clear`; next scan fresh; feedback shown | Medium | Functional | Playwright+API | cache cleared, confirmed |
| FUNC-009 | All buttons/links/dropdowns functional | Click every interactive el | No dead controls; each has handler/effect | High | Functional | Playwright | 0 dead controls |
| FUNC-010 | Catalyst review / scan buttons | Trigger each | Perform intended action without console error | Medium | Functional | Playwright | actions succeed |
| FUNC-011 | Input persistence (deep-dive ticker) | Enter ticker, switch view, return | Ticker retained | Low | Functional | Playwright | value persists |

---

## 7. Performance & Reliability

| ID | Scenario | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|
| PERF-001 | Dashboard first paint | Cold load | `/api/dashboard` cached → page interactive quickly; Flask serves instantly (slowness is yfinance-bound, cached) | High | Performance | Playwright | TTI within target on warm cache |
| PERF-002 | Deep-dive latency | Run AAPL fast warm | <2s warm (`~0.4s` observed); cold fast ~12s; full cold ~16s acceptable (opt-in) | High | Performance | API | warm fast <2s |
| PERF-003 | yfinance cache effectiveness | Run NVDA twice | 2nd run ~10× faster (info TTL 600s/hist 180s); identical result | High | Performance | API | cache hit speedup verified |
| PERF-004 | No duplicate API calls | Load dashboard | Network panel: no redundant identical requests; `.info` not re-fetched per analyze | Medium | Performance | Playwright | no duplicate inflight calls |
| PERF-005 | Slow-API behavior | Throttle network | Graceful skeleton/cooldown UI, not a frozen page | High | Reliability | Playwright | UI responsive under slow API |
| PERF-006 | Failed API / timeout / retry | Kill/slow backend | Timeout → graceful "did not finish within 60s, try Fast Preview" banner; retry works | High | Reliability | API+Playwright | graceful timeout + retry |
| PERF-007 | Heavy portfolio (Full 200) | Run | Completes/partial; no memory blowup; lock serializes | Medium | Performance | API | stable under load |
| PERF-008 | No console errors / leaks | Navigate all views repeatedly | 0 console errors; no detached-node growth; no runaway timers (`_tearSheetCooldownTimer` cleared) | High | Reliability | Playwright | 0 errors; timers cleaned |
| PERF-009 | Concurrent scan + deep-dive | Start scan, launch deep-dive | Shared throttle → graceful, no crash; watchdog frees lock | Medium | Reliability | API | no deadlock; lock released |

---

## 8. Security & Edge Cases

| ID | Scenario | Steps | Expected Result | Priority | Type | Automation | Acceptance |
|---|---|---|---|---|---|---|---|
| SEC-001 | XSS via ticker input | Enter `<img src=x onerror=alert(1)>` in `#an-ticker` | No script executes; input sanitized/escaped on render; treated as invalid ticker | **Critical** | Security | Playwright | no alert; escaped output |
| SEC-002 | XSS via custom tickers | `"><script>` in `#sp-custom-tickers` | Escaped/rejected; no injection into `#picks-results` | Critical | Security | Playwright | no DOM injection |
| SEC-003 | XSS via rendered news/headlines (untrusted RSS) | Feed headline with HTML | News titles HTML-escaped (`stripHtml`/text nodes), not innerHTML-injected | **Critical** | Security | unit+Playwright | headline rendered as text |
| SEC-004 | Broken query handling | Malformed JSON to `/api/analyze` | 400 + clean error, no stack trace leak | High | Security | API | graceful 4xx |
| SEC-005 | Extreme values | Capital `1e308`, positions `99999` | Clamped/rejected; no overflow/Infinity in sizing | High | Security/Data | API | bounded, no Infinity |
| SEC-006 | Delisted / market-closed ticker | Run delisted symbol | Honest "no data / delisted" classification, not rate-limit | Medium | Data | API | correct classification |
| SEC-007 | Unsupported ticker formats | `AAPL US`, ` aapl `, unicode | Trimmed/normalized or rejected cleanly | Medium | Data | API | normalized/rejected |
| SEC-008 | SSE stream abuse / reconnect | Drop SSE mid-job | Falls back to polling; no zombie connections | Medium | Reliability | API | poll fallback works |
| SEC-009 | Access to job of another id | GET `/api/jobs/<random>` | 404/empty, no data leak | Low | Security | API | no cross-job leak |
| SEC-010 | Large input flood | 5000-char custom tickers | Truncated/bounded, no DoS | Low | Security | API | bounded handling |

---

## 9. Regression Test Checklist

Run on every `app.js` / `index.html` / `app.py` change (these are the bugs that have actually shipped here):

- [ ] **View isolation:** exactly one `.view.active` visible per tab (no `#view-dashboard{display:flex}` ID-specificity leak).
- [ ] **renderAnalysis:** no `ReferenceError` (e.g. `displayRecoLabel`) — deep-dive paints fully.
- [ ] **Hero ↔ allocator coherence:** no "Constructive hero + Reduce exposure" contradiction.
- [ ] **CSS class collisions:** new `.cc-*` classes grepped before adding (e.g. `.cc-action-meta` vs `.cc-act-meta`).
- [ ] **Tone-class `!important`:** themed overrides on `.bull/.bear/.neutral` elements still win.
- [ ] **`renderTape` `strip`/`stripHtml`:** no TDZ crash; ticker strip never hidden on empty (placeholder pill).
- [ ] **Cache-bust:** all 7 `?v=…pro-tNN` refs bumped + Flask restarted.
- [ ] **Signal ring:** 4 segments, lit count == aligned factors, center `N/4`.
- [ ] **Invalid-ticker fast-path:** FAKE123 ~1s, `invalid_ticker`, not rate-limited.
- [ ] **DQ gate:** NVDA DQ=FAIL → provisional-hold, targets muted.
- [ ] **Scan-health:** 100% failures → failure banner, never "no buy-qualified setups".
- [ ] **Position sizing:** 0-share names honest ($0 risk/executable); allocated+cash==capital.
- [ ] Existing units green: `node --test ui_render.test.cjs pro_watchlist.test.cjs` + `pytest stock-analysis/tests/`.
- [ ] `node --check static/app.js` clean.
- [ ] Playwright dark + light, 1440/820/390: 0 overlap / 0 hScroll / 0 clip / 0 offline.

---

## 10. Smoke / Data-Accuracy / Production-Readiness Checklists

### 10.1 Smoke (≤2 min, run before any demo/deploy)
- [ ] `/` loads, all 9 dashboard sections populate, 0 console errors, 0 offline banner.
- [ ] Switch to Portfolio Builder → run **Quick US $2500** → ≥1 pick or honest empty-state.
- [ ] Switch to Deep-Dive → **AAPL fast** → tear sheet paints with reco + price + chart.
- [ ] **FAKE123** → fast honest invalid-ticker error.
- [ ] Theme toggle dark↔light works + persists on reload.
- [ ] Market toggle US↔IN reloads dashboard.
- [ ] Refresh button updates freshness timestamp.
- [ ] `/api/health` → overall ok / import ok / price probe ok.

### 10.2 Data-Accuracy
- [ ] Ring `N/4` == positive `tone_factors` count.
- [ ] VIX/breadth/10Y identical across hero, pulse, decision.
- [ ] PB: per-pick `shares=floor(share$/entry)`, `risk=shares×(entry−stop)`, Σalloc+cash==capital.
- [ ] DD: upside == (target−price)/price; targets bear<base<bull; bull case uses correct ticker metrics.
- [ ] Exposure band matches posture (Offensive 80–100 … Defensive 25–45).
- [ ] "What changed" = real prior-day diff or honest baseline.
- [ ] No fabricated confidence %; no "validated/backtested" claims.
- [ ] Timestamps valid, no future dates; degraded sources labeled.

### 10.3 UI/UX Production-Readiness
- [ ] Dark + light both pass at 1440 / 820 / 390 (0 overlap/clip/hScroll).
- [ ] Keyboard-only operable; skip-link works; focus rings visible.
- [ ] axe: 0 critical/serious violations both themes.
- [ ] Every interactive control has effect; no dead buttons.
- [ ] Loading/empty/error/disabled states all designed (no blanks).
- [ ] "Not investment advice" footer present; honesty disclaimers intact.
- [ ] No placeholder text stuck ("Waiting"/"Fetching") after load.

---

## 11. Critical Bugs / Risks to Hunt (financial-product specific)

1. **Contradictory guidance** (highest risk): dashboard regime vs portfolio exposure vs single-stock reco pointing different directions — a user could take opposite real-money actions. (DASH-005, PB-009, DATA-005.)
2. **Silent render failure** masking as "offline": a caught exception in `renderAnalysis`/render path shows the offline banner, hiding the real JS error. Always probe via `page.evaluate` try/catch, not just `node --check`. (DD-002.)
3. **Overselling rigor:** any "confidence %", "validated", or "backtested" wording on heuristic output is a trust/compliance risk. Keep factor-vote framing + "Not validated". (DD-006, DATA-010.)
4. **Hiding 100% scan failure** as "no setups found" → user thinks market is empty when the data pipe is down. (PB-014.)
5. **Invalid-ticker misclassified as rate-limit** → 50s hang + wrong message. (DD-014.)
6. **Data-quality FAIL still promoting targets** as if reliable. (DD-006.)
7. **Phantom funded positions** (0-share names counted with capital but $0 risk shown inconsistently). (PB-007, DATA-009.)
8. **View-stacking** CSS regression (two views visible). (FUNC-001.)
9. **XSS via untrusted RSS headlines / ticker inputs** rendered as HTML. (SEC-001/002/003.)
10. **Stale data shown as fresh** (no timestamp / no degraded label). (DD-017, DATA-006/007.)
11. **Numeric formatting leaks** (`NaN%`, `undefined`, `$Infinity`) at boundaries. (DATA-003, SEC-005.)
12. **Color-semantic errors** (green on a risk/veto, red on an opportunity) — misleads at a glance. (DASH-008, UX-004.)

---

## 12. Recommended Automation Structure

```
stock-analysis/webapp/tests/
├── unit/                          # node --test, pure functions (fast, no browser)
│   ├── ui_render.test.cjs         # EXISTING — keep; add boundary cases
│   ├── pro_watchlist.test.cjs     # EXISTING
│   ├── regime_model.test.cjs      # NEW — computeRegimeModel/regimeLadder/factorState fixtures
│   ├── pulse_interp.test.cjs      # NEW — pulseInterp/color mapping boundaries
│   └── formatting.test.cjs        # NEW — fmtPct/upsidePct/computeRR NaN+boundary guards
├── api/                           # pytest — backend contracts (EXISTING dir: stock-analysis/tests)
│   ├── test_dashboard_contract.py # NEW — /api/dashboard schema + cross-field coherence
│   ├── test_picks_contract.py     # NEW — sizing math, totals, scan_status states
│   ├── test_analyze_contract.py   # NEW — asset types, DQ gate, invalid/rate-limit detail
│   └── (existing test_fast_mode / test_provider_resilience / test_scan_health …)
├── e2e/                           # Playwright (@playwright/test) — NEW
│   ├── playwright.config.ts       # baseURL 127.0.0.1:5500, projects: desktop/tablet/mobile × dark/light
│   ├── fixtures/                  # mocked /api responses for deterministic runs (route.fulfill)
│   ├── dashboard.spec.ts          # DASH-001..024
│   ├── portfolio.spec.ts          # PB-001..020
│   ├── deepdive.spec.ts           # DD-001..020
│   ├── navigation.spec.ts         # FUNC-001..011
│   ├── responsive.spec.ts         # UX-001..003,015 across viewports
│   ├── theme.spec.ts              # UX-004..006
│   ├── accessibility.spec.ts      # UX-010..013 via @axe-core/playwright
│   ├── security.spec.ts           # SEC-001..003
│   └── smoke.spec.ts              # §10.1 (tagged @smoke)
└── QUANTA_E2E_TEST_SUITE.md       # this document
```

**Tooling:** Playwright (browser, network mocking via `page.route`, multi-viewport, screenshots/visual diff), `@axe-core/playwright` (a11y), pytest + `requests` (API contracts), `node --test` (units). Run order in CI: `node --check` → units → pytest → Playwright `@smoke` → full Playwright. Use **mocked API fixtures** for deterministic E2E (live yfinance is rate-limited/flaky) and a separate nightly **live** run for real-data sanity.

**Determinism note:** because data is live + rate-limited, E2E assertions on *exact* numbers must run against mocked payloads; live runs assert only structure/invariants (ring count math, totals reconcile, no contradictions), not specific values.

---

## 13. Missing Functionality / Design Gaps to Fix Before Production

| Gap | Why it matters | Recommendation |
|---|---|---|
| **No DOM/E2E test layer** | Units cover pure fns only; render bugs (DD-002 class) ship undetected | Add Playwright suite (§12) as a release gate |
| **No a11y automation** | Accessibility regressions invisible | Add `@axe-core/playwright`, gate on 0 critical |
| **No explicit input validation messages** on PB/DD forms | Negative/zero/garbage may produce confusing output | Add inline field validation + helper text |
| **Honesty strings are convention, not enforced** | A future edit could reintroduce a fake "confidence %" | Add a unit/E2E assertion that forbids `/\d+%\s*confidence/` in hero + forbids "validated/backtested" near signals |
| **Cross-section coherence is implicit** | Single source of truth exists but isn't asserted | Add API contract test asserting hero posture ↔ allocator band ↔ DD reco directionally agree |
| **No visual-regression baseline** | Subtle layout/theme breaks (the many `pro-tNN` iterations) slip | Add Playwright screenshot baselines for dark+light × 3 viewports |
| **Rate-limit cooldown is global/process-wide** | One user's throttle blocks others | Document/observe; consider per-session in multi-user deployment |
| **SSE + polling fallback untested automatically** | Streaming edge cases (drop/reconnect) are reliability risks | Add API test for SSE drop → poll fallback |
| **No "market closed / after-hours" explicit handling surfaced** | Values may look stale without context | Add a "market closed — last close HH:MM" badge |
| **Disclaimer is footer-only** | Easy to miss on a long page | Surface "educational, not advice" near actionable CTAs too |
| **No loading timeout copy on dashboard sections** | A perpetually-loading section looks broken | Add per-section timeout → empty/error state |

---

*End of suite. Treat Critical/`**bold**` items as release blockers. The product's core obligation is that its numbers, recommendations, and trading logic are internally consistent and honestly framed — the suite above is weighted toward that, not toward "the page renders".*
