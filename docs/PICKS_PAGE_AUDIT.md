# QUANTA Picks: independent end-to-end audit

Audit completed 2026-10-04, America/New_York. Branch `portfolio-ui-quickfix`; baseline HEAD `aeab947`. This report covers the **current dirty worktree**, not HEAD alone. Its pre-existing changes are recorded in [baseline-status.txt](audit-picks/baseline-status.txt). Production code was read-only under the supplied audit brief. No production fixes, commits, trades, account reads or port-5500 restarts were performed.

## 1. Verdict and evidence boundary

**Fix first before presenting Picks as a trustworthy stock-selection service.** The empty result is a reproducible outcome of the present pipeline, but its explanation mixes different populations, warnings and rejection counts. Provider timeout ownership and API validation also need repair. The arithmetic checks support the implemented formulas; they do not establish investment merit or predictive power.

One live Quick scan completed in **280.1 seconds**, analyzed **100/100** names without analysis exceptions, funded **zero**, and reproduced all six chase alerts in the supplied screenshot. A 90-second bulk prefilter timeout changed universe selection to an even spread. The page does not disclose that change. “Bull” and “suggested exposure 100%” do not require the individual selection rules to produce a buy.

The strongest existing improvements are the corporate-event safety gate, shared history-based risk grade, binding Research levels, final-action funding checks, centralized major policy thresholds, explicit hypothetical-book copy and removal of the duplicate Aggressive mandate. They are real improvements, but several earlier findings are only partially resolved.

| Area | Verdict | Reason |
|---|---|---|
| Data | Fix first | Hidden universe fallback; incomplete event coverage; source dates not consistently visible. |
| Algorithm | Hold performance claims | Hand-set factors and thresholds; current scanner and Research versions lack validated out-of-sample evidence. |
| Risk | Ship existing protective behavior, fix observability | Shared grade and fail-closed gates work in covered cases. Risk grade is a risk policy, not demonstrated alpha. |
| Sizing | Fix first | Sound stop-risk arithmetic; whole-share minimum behavior remains unresolved; nontrivial whole-book property coverage incomplete. |
| Decisions | Fix first | Shared core improved; Picks has no holdings context or timed holding-state history. |
| Backend | Fix first | Timeout leaves work alive; invalid request types/coercions; empty candidate list bypasses event publication. |
| Frontend | Fix first | Misleading counts/statuses, missing chase fields, stale level-source prose and retry semantics. |
| Performance | Fix first | Quick took almost five minutes; bulk timeout consumed 90 seconds; progress lacks useful stage counts. |
| Tests | Ship with explicit gaps | Broad passing suites, but 19 expected failures, nine skips, empty golden collections and an undetected chase mutation. |
| UX | Fix first | Strong initial decision heading, but lengthy empty diagnostics and duplicated funded details; error contrast fails. |

**Evidence labels:** **V** = directly verified by source inspection or an executed reproduction; **P** = partially verified under stated fixture/data limits; **U** = unverified. Source verification alone is distinguished from runtime verification. No result below should be read as a profitable-trading claim.

### Executed work

| Check | Result | Evidence / limit |
|---|---|---|
| Source inventory | 332 code/test/support files, hashes and last commits | [file-inventory.csv](audit-picks/file-inventory.csv); broad dependency inventory, not dynamic code coverage. |
| Flask input/job matrix | 76 cases; 63 meet intended contract, 13 mismatches | [api-matrix.csv](audit-picks/api-matrix.csv); real routes, controlled worker. |
| Actual engine on synthetic providers | Eight worker runs across capital, mandate, market and repeat cases | [synthetic-matrix.json](audit-picks/synthetic-matrix.json); no funded cases. |
| Universe routing | All 17 offered market/universe combinations complete | [universe-worker-matrix.json](audit-picks/universe-worker-matrix.json); real selectors, synthetic constituents/history, per-name rejection stub. |
| Public live scan | One Quick scan, 100 analyzed, zero funded, six chase alerts | [live result](audit-picks/live-quick/scan_base.json), [log](audit-picks/live-quick.log). One of three allowed live full scans used. |
| Independent calculations | 110 indicator/level checks + 231 score/grade/sizing/trigger comparisons pass | [arithmetic](audit-picks/independent-arithmetic.csv), [score/grade/sizing](audit-picks/independent-score-grade-sizing.csv). Independence limits in section 5. |
| Cached public data replay | 100 names, 93 scored, seven rejected before full scoring | [distribution](audit-picks/cache-replay-distribution.csv); optional enrichment neutralized, so not exact live scores. |
| Python, local environment | 1,542 passed, 9 skipped, 19 xfailed | [suite-local.log](audit-picks/suite-local.log), [XML](audit-picks/python-local.xml). |
| Python, `TZ=UTC` environment | Same counts | [suite-utc.log](audit-picks/suite-utc.log); Windows did **not** change actual process timezone. |
| JavaScript | 439 passed | [suite-js.log](audit-picks/suite-js.log). |
| Existing Playwright smoke subset | Eight passed | [playwright-suite.log](audit-picks/playwright-suite.log); navigation/theme/market/overflow, desktop and mobile. |
| Picks browser harness | Seven journeys, 19 screenshots, hostile-string probe | [browser-report.json](audit-picks/browser-report.json); actual UI/job protocol with controlled responses. |
| Mutation checks | Seven of eight eventually killed | [initial](audit-picks/mutations.csv), [full-suite follow-up](audit-picks/mutations-full.json); disabling chase detection survives. |
| Dependency consistency | `pip check` passes | [versions](audit-picks/dependency-versions.json), [pip-check.txt](audit-picks/pip-check.txt); not a vulnerability audit. |

The fixture server intercepted unrelated/account APIs with empty synthetic responses. J5's changed capital and J6's India choice exercised the controls and request cycle, but their response fixture retained US/default-capital content. Those screenshots **do not verify currency/market consistency or recalculated sizing**. J4 deliberately changed failure counts without every derived percentage; its “100/100 ... (0%)” is a fixture inconsistency, not a reproduced production arithmetic defect. Contrast remains a valid observation of the production styles.

## 2. Architecture, inventory and operating model

Paths abbreviated below: `SP` = `stock-analysis/core/SP_ELITE_PRO.py`; `TA` = `stock-analysis/core/TradingAnalysis_ELITE.py`; `API` = `ui/stock-analysis/webapp/app.py`; `JS` = `ui/stock-analysis/webapp/static/app.js`; `EP` = `.../static/elite-pro.js`. Line numbers refer to the hashed worktree at audit time.

```text
templates/index.html: Picks form and results container
  -> app.js: applyPicksMarketUI / runScan
  -> POST /api/stock-picks, guarded local request
  -> parse parameters; coalesce duplicate / reject conflicting active job
  -> in-memory job + daemon worker: _run_stock_picks
      -> SP: regime + universe selection + bulk prefilter/prefetch
      -> per-name bars/info/enrichment -> scores, levels, risk, decision, sizing
      -> book gates + corporate events + sector/cost/conviction rules
      -> Research confirmation -> binding levels -> final funding reconciliation
      -> client mandate -> public result/diagnostics -> optional tracking
  -> /api/jobs/<id>/stream (SSE), with /api/jobs/<id> polling fallback
  -> app.js renderPicks + elite-pro.js funnel / cards / watchlist export
  -> quanta.css theme, layout, empty and error states

Public inputs: Yahoo/yfinance, constituent lists, SEC EDGAR,
               calendar/catalyst/revision providers where configured
State: response cache + constituent/event caches + analysis reuse + job store
       + separate ledger/journal (isolated or disabled for this audit)
```

### Runtime map and test relationships

The CSV supplies path, role, bytes, lines, SHA-256, last commit and text-based test references for all 332 inventory entries. References are discovery aids, **not measured execution coverage**. It intentionally excludes production account/data directories and vendored dependencies, and includes some support files beyond the runtime path. The following traced map identifies the principal Picks dependencies. The untracked `SP_ELITE_PRO_Main.py` is an alternate implementation, not the production import; it was preserved and not substituted.

| Component | Source anchor | Tests / evidence |
|---|---|---|
| Form, market/capital/mandate, results | `templates/index.html:380`; `JS:5190`, `5264` | `tests/unit/picks_form.test.cjs`; browser journeys; API matrix |
| Result state, cards, watch buckets | `JS:5936`, `6344`; `EP:378` | `ui_render.test.cjs`, event/binding/risk-driver JS tests; hostile-string probe |
| Funnel and export | `EP:791`, `717` | `audit_remediation.test.cjs`; browser report; live payload |
| Shared styles | `static/quanta.css:656`, `683` | axe + light/dark screenshots; inline `<strong>` fix verified |
| Request parsing and worker | `API:4751`, `5074` | API matrix, `test_webapp_jobs.py` / scanner review tests as inventoried |
| Job lifecycle / status / stream | `API:484`, `607`, `6266`, `6307`, `6353` | duplicate, conflict, cancel/retry probes; worker tests |
| Serialization and mandate | `API:1080`, `4945`, `5292` | decision serialization, mandate, binding-level tests |
| Universe and providers | `SP:3836`–`4308`, `7463`, `7708`, `10044` | universe matrix; guide contract; provider tests |
| Indicators and fundamental inputs | `SP:4313`, `4578`, `4748` | independent arithmetic, existing scanner tests |
| Factor scoring / weights / ranking | `SP:5694`, `5803`, `5951`, `6071`, `6175`, `6229`, `6560` | independent factor comparisons; threshold/scanner tests |
| Analysis and sizing | `SP:7889`, `9507`, `9710`, `10236`, `11388` | sizing/sector/empty-book tests; profile |
| Deep confirmation and binding levels | `SP:10783`, `10850`; `TA` | `test_binding_levels.py`, ladder wiring; mutation M2 |
| Authoritative decision / policy | `core/engine/decision_engine.py`, `risk_overrides.py`, `decision.py`, `types.py`, `score_bands.py`, `thresholds.py`, `levels.py`, `blockers.py` | threshold, decision, fail-closed and ladder tests |
| Shared risk grade | `core/engine/risk_grade.py:57`, `152` | `test_risk_grade.py`, risk drivers; independent grade comparison |
| Corporate events | `core/engine/corporate_events.py:415`, `602`; `SP:11280` | `test_corporate_events.py`, event-rejection fixtures; M1; empty-list probe |
| SEC public filings | `core/portfolio/sources.py:70`, `104`, `129` | corporate-event tests; fixed HTTPS hosts and timeout inspection; no account service invoked |
| Data/session validation and enrichments | `modules/` and scanner helpers listed in inventory/import graph | scanner/data quality/earnings/insider suites; limited live coverage |
| Response caches / analysis reuse | `API:167`–`413`, `669` | cache isolation tests; saved public response cache; no full concurrent cache stress |
| Evidence registry | `core/engine/evidence.py:34`, `core/apex_quant/_reports/evidence_registry.json` | evidence tests; registry read, no current strategy validation run |
| Ledger, journal, forward tracking | tracking modules in [inventory](audit-picks/file-inventory.csv), `core/apex_quant/` | isolated ledger tests only; production records deliberately not opened |
| Test isolation, fixtures, tools | `stock-analysis/tests/conftest.py`, both test trees, `tests/tools/golden_scan.py` | full suites; preservation checks; fixture gaps in section 10 |

Exact UI-string searches are retained in [ui-string-map.txt](audit-picks/ui-string-map.txt); Python imports in [imports.csv](audit-picks/imports.csv). No claim is made that static tracing discovers every possible optional import.

### Providers, caches, environment and concurrency

The Flask import installs a process-wide yfinance construction throttle of 350 ms and response caching. Info TTL is 3,600 s; history TTL 900 s; each cache has a 4,000-entry cap. Disk JSON persists compatible values with a 30-second save debounce and atomic replacement; expired values are discarded on load. Legacy pickle is not loaded. **Bulk `yf.download` does not receive the same Ticker response cache**, so a warm info/history cache does not remove the prefilter timeout. (`API:125`–`416`; live log.)

Analysis reuse defaults to 300 s with separate stale retention of 86,400 s; market environment defaults to 600 s. SEC CIK mappings cache for 24 h and submissions for 30 min (`core/portfolio/sources.py:28`). The event layer separately bounds document reads/caches. These clocks serve different purposes and are not presented as one coherent scan provenance record.

Important environment controls: `QUANTA_ENGINE_ROOT`, `QUANTA_CACHE_DIR`, `QUANTA_YF_CACHE`, `QUANTA_YF_DISK_CACHE`, `QUANTA_YF_INFO_TTL`, `QUANTA_YF_HIST_TTL`, `QUANTA_REGIME_HISTORY_PATH`, `QUANTA_ANALYSIS_CACHE_SECONDS`, `QUANTA_ANALYSIS_STALE_RETENTION_SECONDS`, `QUANTA_MAX_CONCURRENT_SCANS`, `QUANTA_SCAN_MAX_RUNTIME_SECONDS`, `QUANTA_JOB_RETENTION_SECONDS`, `QUANTA_JOB_RETENTION_MAX`, `QUANTA_PREFILTER_TIMEOUT`, `QUANTA_SEC_CONTACT` / `QUANTA_SEC_USER_AGENT`, `QUANTA_EVIDENCE_PATH`, tracking/ledger/journal paths, `QUANTA_NO_DEEP_CONFIRM`, and optional provider API-key variables. [environment-references.txt](audit-picks/environment-references.txt) records names and code references, not secret values.

Jobs live in a locked, in-process store. Default concurrent scans: one; max runtime: 1,800 s; terminal retention: 1,800 s and 200 records. Equal active parameters reuse the job; differing parameters return 409. Cancellation is cooperative and retains worker ownership until release. SSE and polling expose the job state; neither can terminate an already-running provider call. Process restart loses the in-memory jobs; multiple server processes would need shared admission control. `TradingConfig.ACCOUNT_TO_USD` is mutable global state written during market initialization (`SP:6600`); interference with concurrent analysis is a source-level risk, not a reproduced race.

## 3. Earlier findings F1–F15, rechecked

Original files were found under `stock-analysis/docs/PICKS_AUDIT.md` and `stock-analysis/docs/IMPLEMENTATION_LOG.md`, rather than root `docs/`. Commit history and the actual worktree were checked independently. Relevant remediation commits include `781eadd` (tests), `9c5b3a8` (thresholds), `9c46931` (mandate), `9aca69b`/`bfc7723`/`166820d`/`90a92de` (events), `ff19863` (levels), `ca8f10f` (risk), and `523c0b8` (ladder). A fix on another branch is not a fix in this worktree.

| Prior finding | Current status | Verified resolution / remaining issue |
|---|---|---|
| F1 Corporate events | **Partial** | Source checks recognize target versus acquirer, deal consideration and relevant delisted class; unavailable checks deny new funding. However `SP:11444` returns before `_apply_event_gate` if every name failed per-name analysis: events disappear from the published reject list. Empty-list probe reproduces this. India and some funds have no applicable source; original policy remains fail closed. |
| F2 Conflicting levels and R:R | **Partial** | `_bind_research_levels` publishes the levels that actually bind; stop changes resize positions. Independent arithmetic and M2 support this. `deepConfirmSummaryHtml` still says scanner levels remain the basis; other page copy correctly says Research levels govern. Remove contradictory prose. |
| F3 Two risk grades | **Fixed within covered inputs** | Both paths use shared `grade_from_history`, lookback and drivers. Identical-input math verified; no claim that distinct live data retrievals always match. |
| F4 Timeout / universe fallback | **Still open** | Live prefilter exceeded 90 s and used even sampling. Probe shows one provider thread alive after timeout. Prior bounded-process work is not in this branch. Fallback is not in the visible result contract. |
| F5 Funnel and watch-only success | **Partial** | “Analyzed” is clearer and empty scans show the funnel. Actual synthetic watch-only runs still report `success`; `API:5292` truncates all published rows to max positions; book counter is overwritten after backfill (`SP:12146`). |
| F6 Portfolio awareness / capital | **Partial** | Current page explicitly says hypothetical allocation and not existing holdings; defaults use threshold config. No holdings/cash input is supplied to Picks, and US market-switch code resets budgets ≥ India minimum (`JS:5242`). Account auto-connection is outside this audit's authorized scope. |
| F7 Decision ladder | **Partial** | Shared decision core now supports holder SELL/TRIM and grade D is not universally banned. Picks has no existing-position context, so holder behavior is not a Picks portfolio lifecycle. No persistent time-in-Hold rule. |
| F8 Evidence / methodology | **Still open for validation; presentation improved** | Current Research `elite.research.v2` and scanner `elite_pro.composite` are untested. Predecessor NO_EDGE is versioned lineage, not the current model's result. No validated forward portfolio edge established. |
| F9 Single config / reason text | **Partial** | Major policy thresholds and `/api/thresholds` exist; specific analyst-downside reasoning improved. India screens, chase rules and Research sizing still contain literals. Not every applied rule is displayed. |
| F10 Score scales / calibration | **Still open** | Scanner /10, strength /5, Research /100 and early-momentum /6 coexist. Cross-sectional ranking is separate from authoritative action score, but normalization occurs after filters and is skipped in small pools. |
| F11 Minimum sizing | **Still open** | Whole-share minimum remains a flat $100-equivalent floor; capital-relative floor applies only to fractional mode. Five strict expected failures explicitly retain this gap. The historical sub-minimum deep-halving example was not newly reproduced as a funded result. |
| F12 Freshness | **Still open** | Header/dashboard age, scan completion, quote date and completed daily bar are different clocks. Chase “today/yesterday” is undated trading-row text, including on the weekend. |
| F13 Dense/repeated UI | **Partial** | The screenshot's broken inline emphasis is fixed at `quanta.css:656`. Current empty state still repeats counts and internal gate notation; funded cards repeat levels; useful diagnostics remain collapsed on funded/watch output. |
| F14 Duplicate mandate | **Fixed** | UI offers Balanced and Conservative. Legacy `aggressive` requests are accepted as Balanced with a notice. Conservative uses 0.7 risk allocation scale; Balanced 1.0. |
| F15 Missing regression tests | **Partial** | Named test files restored, but 19 strict xfails remain. Chase removal survives full Python+JS; six golden tests skip because collections are empty. |

## 4. Findings and concrete fixes

Effort is a planning estimate: S = roughly 0.5–2 engineering days, M = 3–7 days, L = multiple weeks with data/validation work. It excludes waiting for a forward performance record. No Critical defect that currently funds a prohibited live position was reproduced; this does not certify all funding paths.

| ID | Severity / area | Evidence and reproduction | Impact | Proposed fix / effort |
|---|---|---|---|---|
| A01 | High, provider ownership | `SP:10089`–`10118`; [behavior-probes.json](audit-picks/behavior-probes.json). Delay the provider beyond a 0.03 s test budget: caller returns, one provider thread survives. Live timeout also observed. **V** | Overlapping provider work, unreliable cancellation and false throttling attribution; hidden selection change. | Bound bulk work in an owned subprocess; terminate and reap before fallback. Report selection method, duration and cause. S–M |
| A02 | High, request contract | `API:4751`–`4848`; 13 mismatches in [API matrix](audit-picks/api-matrix.csv). Nonempty list/scalar JSON returns 500; malformed JSON becomes default scan; INR100 accepted; 1.8 positions truncated; string `false` truthy. **V** | Expensive unintended scans and inconsistent API/UI constraints. | Typed schema: object-only, strict boolean/integer, market minimum, nonempty custom list. Reject before creating a job. S |
| A03 | High, event publication | `SP:11444` before `11467`; empty-list probe: scanned SYNTH, checker never called. **V** | All-filtered scans omit flagged/unavailable event explanations. Funding stays zero. | Run event coverage/publication for scanned universe independently of fundable candidates; bound warmup join. S |
| A04 | High, scan accounting | `API:607`, `5292`; `SP:12146`; synthetic runs show watch-only `success`; live chart includes a warning. **V/P** | Users cannot reconcile the outcome; row cap can hide watch explanations. | Separate analyzed/rejected/warning/funded/published counts; classify on funded count; cap funded and watch buckets independently. M |
| A05 | High, investment validity | `engine/evidence.py:44`–`65`, registry; no current validated strategy. **V** | Risk of interpreting heuristic sizing as evidence-backed selection. | Keep evidence status next to decision; establish frozen, costed, portfolio-level validation and forward record. L |
| A06 | Medium, minimum sizing | `SP:2002`, `9507`; F11 strict xfails. **V source/tests; funded live impact P** | Small accounts can lose otherwise qualified setups after rounding/haircuts; very large capital is not automatically better. | One final sizing/minimum function applied after every transform; explicit minimum/one-share policy and cap checks. M |
| A07 | Medium, chase semantics/data | `SP:8298`, `9610`; `JS:6351`; captured FICO alert. **V** | Empty company/distance columns; oversold rebound described as broadly extended; weekend “today.” | Include company, distance-to-20DMA, trigger type, source bar dates and threshold. Label specific gap/run-up trigger. S |
| A08 | Medium, config integrity | [literal-comparisons.csv](audit-picks/literal-comparisons.csv); `SP:9610`, India gates; Research PositionSizer. **V** | “Every threshold in config/UI” claim is false; policy drifts across surfaces. | Inventory and centralize policy literals; separate model parameters, numerical guards and display thresholds. Generated effective-rule view. M |
| A09 | Medium, score interpretation | `SP:6229`, `6560`, `10549`; [distribution](audit-picks/cache-replay-summary.json). **V/P** | Multiple scales and post-selection normalization obscure what decided. | Single decision score plus named component/ordering scores; calibrate on full eligible universe before selection using PIT data. L |
| A10 | Medium, retry/budget UX | `JS:5242`, `5273`. Market switch can reset a valid large US budget; safe retry changes scan type to Quick. **V source** | User silently gets different capital/universe. | Preserve per-market budgets; retain parameters or explicitly offer a smaller scan with a visible change. S |
| A11 | Medium, accessibility | `JS:5962`; [J4 screenshot](audit-picks/J4-outage-light-1440.png); axe 2.12:1 heading contrast vs 4.5:1 needed. **V** | Failure message difficult to read in light mode. | Semantic theme tokens; contrast regression for empty/error/retry states. S |
| A12 | Medium, explanation hierarchy | `JS:5936`; [empty](audit-picks/J2-empty-light-1440.png) and [funded](audit-picks/J3-funded-dark-1440.png) screenshots. **V observation** | Long search for cause/action; repeated levels and disclaimers. | Compact verdict, typed stage summary and one decision row; disclose detailed diagnostics on demand. S–M |
| A13 | Medium, progress/freshness | `API:5074` worker updates; `JS:2356`, screenshot, live log. **V source/P runtime timing** | Minutes with little useful progress; age labels describe different data. | Stage counts, elapsed time and bounded-provider status; separate quote/bar/scan timestamps. M |
| A14 | Medium, test integrity | [test-skips.json](audit-picks/test-skips.json), [mutations-full.json](audit-picks/mutations-full.json). **V** | Green suites miss chase removal and omit frozen golden replay. | Fill golden recordings from synthetic/public frozen data; implement chase boundary/date tests; retire explicit xfails only with fixes. M |
| A15 | Medium, shared mutable state | `SP:6600` writes class account conversion; app monkey-patches global yfinance. **V source/U race** | Future parallel markets can affect sizing/conversion or diagnostics. | Immutable per-scan context and market-qualified caches; concurrency property tests. M |
| A16 | Low, stale level-source text | `JS:5376` deep-confirm summary vs binding-level table; M2 killed. **V source** | Correct math is explained incorrectly. | Render source/horizon from authoritative `trade_setup`; remove scanner-basis sentence. S |

The live scan's earnings-calendar warning is not proof of an earnings-calendar rejection bug: unavailable calendar applies a sizing haircut, and its diagnostic counter is mixed into rejection statistics. Distinguish calendar availability from corporate-event coverage; they are different inputs with different consequences.

## 5. Algorithm, arithmetic and screenshot anomalies

### Stage order and effective rules

This is the production scanner path; Research confirmation is an additional model, not a second name for the same calculation. [thresholds-current.json](audit-picks/thresholds-current.json) preserves the complete exported policy groups. [literal-comparisons.csv](audit-picks/literal-comparisons.csv) inventories 3,145 numeric comparisons across production Python; many are numerical guards or presentation rules, so that count is **not** a count of policy defects.

| Stage | Inputs / rule | Source / definition |
|---|---|---|
| Request | US capital 100–1e9, default 2,500; India UI minimum 10,000/default 200,000; max positions 1–100/default 5 | `engine/thresholds.py:CAPITAL`; API currently enforces US minimum for India too. |
| Regime context | VIX plus benchmark trend; low/normal/high/extreme bands at 12/20/30; volatile trigger 25; trend can constrain exposure | `SP:5126`, `6600`; config `REGIME`. Independent source verification remains U. |
| Universe | Quick100, Standard250, Comprehensive400; full/current constituents and market-specific/focused lists | `SP:7463`–`7780`; actual membership depends on available source and fallback. “Most-watched” is not proven by popularity data. |
| Prefilter | Bulk liquidity ranking with 90 s budget; up to 1,000 names; timeout uses even sampling | `SP:10044`; budget configurable; 1,000 also appears literally. Focused selector separately calls bulk download without this ownership boundary. |
| Market data | Adjusted historical bars, public company info, benchmark; completed daily-session validation | `SP:7889`, history/data-quality helpers. Historical current snapshots are not point-in-time fundamentals. |
| Basic eligibility | US price $10–5,000; market cap ≥$2bn; average volume ≥750k; daily dollar volume ≥$20m; ≥200 history rows; stale/invalid bars denied | Config `UNIVERSE`; India alternatives still inline. These are sequential early returns. |
| Indicators | Returns, SMA20/50/200, RSI14, ATR14, trend/volume and other technical values | `SP:4313`; formulas checked on synthetic bars. |
| Five factors | Technical, fundamental, momentum, quality, value; each approximately 0–10 | `SP:5694`, `5803`, `5951`, `6071`, `6175`; factor cutoffs mostly literals/model parameters. |
| Composite weights | Bull 15/20/35/15/15%; Bear 10/20/15/20/35%; Volatile 10/15/15/40/20%; Neutral/Ranging 15/25/20/20/20%; Unknown 10/25/15/30/20% | `_REGIME_WEIGHTS`, `SP:6560`; order is technical/fundamental/momentum/quality/value. |
| Enrichment and adjustments | Insider, revisions, analyst consensus, catalysts, chase, earnings; bounded adjustment cap 1 point | `SP:8219` onward; `SCORE.ADJUSTMENT_CAP`; optional data changes result, so neutral replay is not identical. |
| Chase classification | OR across recent positive gaps and 5/10-day advances; ordinary/growth thresholds differ | `SP:9610`; gap 3/5%, 5-day 10/15%, 10-day 20/30% literals. Low RSI does not exempt a gap alert. |
| Preliminary action | Regime-specific signal bands, minimum score 3.5, then committee/policy restrictions | `SP:7889` onward; `SCORE`, `REGIME`, `COMMITTEE`. Core new-money bands are 55/65/75 on /100. |
| Levels | Current-price/zone entry, ATR/structure stop and target; ATR stop multiple 2.75, stop-distance bounds 3–20%; target structural/analyst/fallback logic | `SP:9710`; `LEVELS` plus implementation literals. Target is a heuristic, not a distribution or guarantee. |
| Execution costs / R:R | Net reward divided by stop-distance risk after configured round-trip cost; scanner hurdle 1.8, Research hurdle 1.5 | `SP` levels/sizing, shared `engine/levels.py`; config `RR`, execution-cost groups. Different stages can legitimately apply different hurdles if labeled. |
| Committee | Upside hurdle 8% (6% with bullish insider); R:R floor 1.6 with configured insider relaxation; downside/extended-RSI checks | Config `COMMITTEE`, `RR`; per-name logic. |
| Sizing and risk haircut | Stop-risk budget, signal/score multipliers, drawdown reduction, grade multiplier, exposure/caps, calendar haircut and whole shares | `SP:8476`, `9507`; `SIZING`. Defaults target risk 0.6%, Strong Buy 0.75%; maximum 2%/2.5%. |
| Shared grade | History-based volatility, beta, drawdown, winsorized tail and liquidity; publishes contributing drivers | `engine/risk_grade.py:57`, `152`; `RISK_GRADE` config. Same-history grade shared with Research. |
| Per-name survivors | Results appended only after earlier action, score, risk/reward, liquidity and allocation checks | `SP:7889`–`9007`. Failures/warnings counted separately in code but combined in the exposed map. |
| Cross-sectional ranking | At least 30 surviving scored names; builds ranking score; authoritative score/action not replaced | `SP:6229`, `10549`; `UNIVERSE.ZSCORE_MIN_UNIVERSE`. This is survivor normalization, not sector-relative fundamental grading. |
| Book action gate | BUY/STRONG BUY/ACCUMULATE and valid final action only | `SP:11451`. This is the displayed “Actionable” stage, not the earlier per-name signal count. |
| Corporate events | Acquired/delisted equity denied; unavailable source denies new money; background coverage over scanned universe | `SP:11280`, `11297`, `11467`; `engine/corporate_events.py`. Empty-result early return bypasses reporting. |
| Book quality gates | Grade F excluded (D allowed with lower sizing); score floor 5.5 or median for larger pools; R:R ≥1.8; conviction ≥2, or ≥3 with pool ≥20 | `SP:11473`–`11545`; `BOOK`, `RR`, `SCORE`. |
| Cost viability | Expected return at least 2× round-trip cost | `BOOK.COST_VIABILITY_MULTIPLE`; default book fallback 15 bps. |
| Portfolio construction | Capital, per-position, risk and sector constraints; normal position cap10%, small-account cap25%; sector profile controls allowed concentration | `SP:11388` onward; `BOOK`, `SIZING`; sector weights displayed against deployed capital while caps may use capital. |
| Deep confirmation | Research may restrict action; missing confirmation denies funding; Research levels bind and trigger re-sizing | `SP:10783`, `10850`; binding-level and fail-closed tests. |
| Final reconciliation | No new money for non-fundable final action; share rounding, remaining book checks, mandate 1.0/0.7; serialize Decision | `SP` finalization; `API:4945`, `5292`; post-confirm row cap still problematic. |
| Presentation / record | Public score/levels/action/reasons/evidence, watch and event buckets, scan diagnostics; optional ledger writes | `API:5074`–`5570`; JS/EP; audit disabled tracking for scans. |

The stage table describes order at the level of major pipeline phases. Some checks recur after a score or allocation changes; there is no single immutable event log describing every pass/fail/adjustment per ticker. Creating that log is preferable to inferring stages from aggregate counters.

### Independent calculations: what was proved

[synthetic_engine.py](audit-picks/synthetic_engine.py) builds 12 controlled name histories/info profiles spanning normal candidates, gap/chase, low liquidity, missing/short data, adverse trend and reward/risk conditions. Eleven have sufficient bars for **110** comparisons of moving averages, RSI, ATR, returns and level calculations. [quant_checks.py](audit-picks/quant_checks.py) adds **231** factor, grade, sizing and trigger comparisons. All comparisons passed within the scripted tolerances.

The reference calculations use arithmetic/table rules rather than calling the corresponding scoring/sizing function for their expected answer. Factor tests use the measured indicator/fundamental inputs supplied to the scorer; they do not independently recreate every upstream extraction field. Grade tests independently calculate from synthetic history without a live benchmark-beta reconciliation. Sizing cases span capital 100, 2,500, 25,000 and 1e9 and BUY/ACCUMULATE/HOLD. Trigger-price equations are checked separately from market plausibility.

Useful identities, with entry `E`, stop `S`, target `T`, cost fraction `c` and desired ratio `r`:

```text
net R:R = (T - E - E*c) / (E - S + E*c)
trigger entry for fixed S,T,c,r = (T + r*S) / ((1+r)*(1+c))
stop-distance allocation = risk budget / ((E-S)/E), then caps/haircuts/rounding
risk budget = capital * target risk * signal multiplier * score multiplier * drawdown multiplier
whole shares = floor(allowed allocation / entry)
```

Invalid/zero stop distance must deny allocation; stop-loss risk does not cover gaps, nonexecution or liquidity failure. The page properly qualifies planned stop loss in its funded summary. The browser's funded example is hand-constructed to verify rendering; it is **not evidence that this audit's actual engine produced a qualifying funded book**. Independent arithmetic of at least eight synthetic names is V; complete independent reconstruction of eight live decisions through every external enrichment and final page value is **P**, not accomplished.

### The seven supplied anomalies

| Observation | Confirmed? | Cause and correct interpretation |
|---|---|---|
| (a) Zero actionable/survivors despite at least 13 later-gate failures | **Yes; labeling problem, not arithmetic contradiction** | Seven `risk_reward_low` plus six `zero_allocation` had reached later per-name checks. The visible actionable count is taken from the already-surviving list, which is empty. Two sequential filtering systems are drawn as one funnel. |
| (b) Text omits earnings warning; chart sums to 100% | **Yes** | Text takes top five counts: 50+26+7+6+6=95. Chart takes top six, adding calendar-missing5. Actual other rejection reasons sum to five: score-post-penalty2, insufficient-data1, committee-hold1, signal-avoid1. Calendar warning overlaps rather than replacing those actual rejections; the 100% total is coincidental. |
| (c) Thresholds show `--` | **Yes** | Gate stats initialize thresholds to null; early return prevents evaluation/assignment. The label should say “not evaluated; no per-name survivors,” optionally showing configured baseline separately. It does not mean the threshold was disabled. |
| (d) Down about41% / RSI26 flagged chase | **Yes; implemented rule consistent, label imprecise** | FICO's prior-session positive gap was7.2%; the OR-rule fires independently of one-month return/RSI. A rebound gap can occur inside a large decline. It is not proof of sustained upward extension. |
| (e) COMPANY / FROM20DMA blank | **Yes, payload defect** | Chase producer supplies ticker/reason/price/RSI/1m only. Renderer reads company and distance fields never populated. |
| (f) “Today” on weekend | **Yes, date ambiguity** | It names the last bar, not calendar today. Publish “gap on YYYY-MM-DD” using the market session date, with timezone/source. |
| (g) Zero of100 in Bull regime | **Yes; not proof of a broken engine or no opportunities market-wide** | Score50, nonactionable26 and other gates exhaust the chosen universe. Regime changes context/weights/exposure ceiling; it cannot guarantee buys. Hidden selection fallback and uncalibrated interacting thresholds limit economic interpretation. |

Captured chase names: AMAT, FICO, HPE, RCL, SNPS and TXN. The result matches the screenshot's characteristic reasons and approximate values. No investment action is inferred from them.

### Distribution, sensitivity and calibration

The original production response does not save all rejected names' complete factor/enrichment snapshots. The audit retained public price/info cache input in [market-cache-snapshot.json.gz](audit-picks/market-cache-snapshot.json.gz) and replayed all100 with HTTP blocked. Calendar data was unavailable and optional insider/revision/catalyst signals were neutralized. The distribution is therefore **a controlled partial replay**, not an exact historical decision reproduction.

| Replay measure | Result |
|---|---|
| Full scoring available | 93/100 |
| Score /10 | min0.96; p25=2.20; median2.99; p75=4.59; p90=5.62; max7.60 |
| Grade | A3, B34, C30, D10, F16 |
| R:R | min−0.26; p25=1.49; median2.22; p75=2.99; p90=3.648; max5.08 |
| Final signal | HOLD26, AVOID67 |
| Final action | WATCHLIST26, AVOID67 |

Single-variable sensitivity, holding the replayed values fixed:

| Threshold | Lower / base / higher | Pass counts of93 |
|---|---|---|
| Preliminary score | 3.0 /3.5 /4.0 | 46 /39 /31 |
| Action/book score | 5.0 /5.5 /6.0 | 20 /12 /8 |
| Scanner R:R | 1.6 /1.8 /2.0 | 64 /58 /54 |

The step sizes 0.5 score and0.2 R:R are **declared audit perturbations**, not configured production sensitivity steps; the config has none. These are individual gate pass counts, not newly fundable books. Grade, conviction, event, sector, cost and sizing perturbations were not exhaustively rerun. [sensitivity.csv](audit-picks/sensitivity.csv) records the exact limited experiment.

There is evidence of a selective scale: only12/93 partial-replay names clear5.5, before all remaining requirements. There is **no evidence here establishing an optimal threshold**, that lower thresholds improve returns, or that current strictness is economically justified. Requiring every gate to pass compounds selectivity, but observing an empty book cannot distinguish useful risk control from poorly calibrated screening. Do not change thresholds simply to manufacture a populated page.

### Methodology and evidence quality

Technical, quality, value and momentum inputs are plausible research features, but implementation plausibility is not validation. Fundamental and quality legs overlap profitability/leverage concepts; quality/value also overlap some return-on-equity measures. Absolute cutoffs are not sector-peer factor grades. EPS revisions are optional enrichment rather than a fully separate, calibrated peer-relative leg. Analyst targets and projected horizons are uncertain estimates.

Risks requiring a validation design: current-constituent survivorship; current fundamentals applied to historical decisions; latest consensus/revisions without publication timestamps; adjustments selected after inspecting predecessor results; hand-set regime weights; parameter search across many filters; historical corporate-action gaps; dividend/split handling and delisted securities; realistic latency, spreads, slippage, partial fills, fees and cash drag. Default bps estimates and ATR stops do not capture nonlinear execution risk.

The registry's `elite.composite` predecessor has NO_EDGE evidence from a limited technical/risk scope and survivor sample. Its reported mean ICs were approximately−0.023 at21d and−0.034 at63d and not statistically persuasive. This **does not establish a current-version result**. `engine/evidence.py` explicitly versions `elite.research.v2` and preserves predecessor lineage; `elite_pro.composite` lacks validated evidence. The invalidated historical CAGR is not repeated as a performance result here.

Needed before a claim: versioned full input snapshots, point-in-time universe/fundamentals/revisions/events, predeclared evaluation rules, untouched holdout, costed portfolio simulator and investable benchmark, confidence intervals, turnover/drawdown/exposure reporting, and a forward-only decision record with corrections appended rather than rewriting history. Production ledger holdings/returns were not opened, so current record completeness and account/portfolio performance remain U.

VIX and index trend were not independently numerically verified. The current scan reports Bull/VIX15.3. Cboe's primary historical landing page was reachable, but the underlying current CSV could not be retrieved in the browsing tool; an independent index-trend series was not established. A second Yahoo display would not be independent confirmation.

## 6. Reference products and smallest credible target

Public descriptions were checked during this audit; they are product claims, not proof of an investment edge. Sources: [Seeking Alpha: What is Alpha Picks?](https://help.seekingalpha.com/what-is-alpha-picks), [main benefits](https://help.seekingalpha.com/what-are-the-main-benefits-of-seeking-alphas-alpha-picks-service), [public methodology discussion](https://seekingalpha.com/article/4920939-2026-top-stocks-mid-year-performance-review), [public sell-rule discussion](https://seekingalpha.com/article/4902506-6-stocks-for-mid-terms-and-market-uncertainty), [Barron's Stock Picks](https://www.barrons.com/market-data/stocks/stock-picks). Seeking Alpha discussion pages include transcript material; treat exact current subscriber operations as P. No proprietary method was inferred or copied.

[Yahoo Research Hub](https://finance.yahoo.com/research/) and [screener](https://finance.yahoo.com/research-hub/screener/) / [help](https://help.yahoo.com/kb/SLN29213.html) direct opens failed. Search excerpts indicate research/screening and third-party reports, but do not establish one unified Yahoo selection portfolio. Its cadence, factor methodology and sell rules are U. [Cboe historical data](https://www.cboe.com/tradable_products/vix/vix_historical_data) was used only for the independent-input attempt above.

| Feature | QUANTA current | Public reference concepts | Decision: include/adapt/skip |
|---|---|---|---|
| Selection method | On-demand multi-stage technical/fundamental screen | Alpha Picks describes quantitative selection; Barron's page attributes picks to editorial reporting/data; Yahoo U | **Adapt:** explicit model version and selection reason so a user understands why this name. |
| Five sector-peer factors | Technical/fundamental/momentum/quality/value; optional revisions; no full peer grades | Alpha Picks describes value, growth, profitability, EPS revisions and momentum relative to peers | **Adapt:** independently designed peer comparisons with sparse-sector fallbacks; validate incremental signal before adding weight. |
| Cadence | User-triggered scans, potentially changing intra-session | Alpha Picks describes two monthly selections; Barron's is dated editorial publication; Yahoo U | **Include:** explicit scheduled review/publication cutoffs for a model book; keep exploratory scans separately labeled. |
| Holding orientation | Entry/stop/target and near-term gates | Alpha Picks describes longer-term buy-and-hold | **Adapt:** declare horizon and review rules; do not imply ATR-trading targets are a long-term thesis. |
| Buy/Accumulate/Hold/Sell/Avoid/Watch | Core supports distinct new-money and holder outcomes; Picks lacks positions | Alpha Picks public rating/sell policy; other services' exact state machine U | **Include:** clear new-money decision plus model-book maintenance action with reason/date. |
| Sell/time-in-Hold | No persistent Picks holding-state clock | Public Alpha Picks descriptions include sell ratings and Hold beyond180days | **Adapt:** predeclare deterioration and maximum unresolved-Hold review rules; choose/validate QUANTA's own duration. |
| Portfolio benchmark | Infrastructure exists; no current validated Picks portfolio shown here | Alpha Picks discusses S&P500 comparison; Barron's page shows pick-level return and selected benchmark comparison | **Include:** total-return model equity/cash benchmark, same dates/costs/currency; aggregate portfolio view, not averaged trade returns. |
| Forward-only record | Ledger/test infrastructure; live completeness U | Public reference pages alone do not prove all immutability/backfill practices | **Include:** append-only timestamped selection/version/input/exit log; expose coverage and corrections. |
| Sector clustering | Diversification option and capital-based caps | Alpha Picks allows sector opportunities; exact current constraints U | **Adapt:** explicit cap and concentration rationale; allow only predeclared policy exceptions, never silent relaxation to fill slots. |
| Thesis/risk | Generated narrative, warnings, additional checks | Research-oriented narratives exist in reference products | **Include:** concise evidence-linked thesis, failure condition and next review date. |
| Catalysts/earnings | Optional calendars; missing coverage haircut; corporate event fail closed | Reference depth varies; systematic coverage U | **Include:** event date/source/coverage status; missing data must not appear as “no event.” |
| Method transparency | Exported major thresholds, many hidden literals and multiple score scales | Public high-level methods, not full proprietary implementation | **Adapt:** disclose effective rules and measured evidence; skip proprietary imitation and performance comparisons without equivalent data. |

**Smallest credible target:** retain today's exploratory scanner as a research tool; add a separately versioned, forward-recorded model selection process only after data provenance and decision contracts are stable. Its inputs are a dated universe and frozen research snapshot; its output is one authoritative new-money decision, one canonical set of levels/horizon, named blockers and explicit uncertainty. A model-book review records hold/trim/exit actions independently of the user's brokerage account. Missing event coverage blocks new funding as currently required. Account linking is optional future product work, not a prerequisite for an honest hypothetical book.

Exists: indicators, factors, shared decision/risk core, event gate, levels binding, job runner, evidence labels and ledger components. Build: authoritative scan event ledger, complete provenance, calibrated peer features if justified, persistent model-book state/cadence/exit rules and portfolio evaluation. Validate: economics with point-in-time holdout and forward records, not UI screenshots or passing unit tests.

## 7. Backend, security and maintainability

AST branch counts are a rough hotspot measure, not formal cyclomatic complexity. `analyze_stock` spans1,119 lines/188 branch nodes; `get_top_recommendations`791/127; `_run_stock_picks`497/113. Other inventory hotspots are outside the narrow Picks path. [complexity.csv](audit-picks/complexity.csv) preserves all measurements. These functions mix data retrieval, scoring, restrictions, mutation, narrative and counters, making it difficult to prove that a later haircut updates every dependent field.

Prefer a typed per-scan context and immutable intermediate result per phase; keep scanner versus Research model separation explicit, while sharing policy/levels/grade contracts. Avoid replacing the active scanner with the untracked alternate implementation. Extract boundaries only as covered fixes need them; a broad rewrite would obscure behavior changes.

Broad exception handling is used to keep optional enrichment from aborting analysis. Some paths correctly mark unavailable, while others silently discard details (for example chase append `SP:8305`). Missing provider data, policy rejection, invalid input and internal failure need separate structured codes. Diagnostic logs currently carry more stage detail than the payload; user explanations should derive from the same structured record.

Security checks exercised: custom-header requirement and foreign Origin rejection both return403; invalid ticker markup fails input validation; hostile server strings in ticker/company/sector/industry/thesis/reasons/chase content create zero `onerror` nodes and execute zero payloads in tested render paths. Normal strings are escaped in the inspected templates. This is **not an exhaustive proof of every `innerHTML` context** or a penetration test. CSV/spreadsheet formula injection was not tested for every export format.

The app is intentionally an unauthenticated loopback application (`API:429`–`467`), not an Internet-facing authenticated service. That is a deployment assumption, not a reason to add an account/auth flow to this audit. Public SEC fetch helpers use fixed HTTPS hosts and bounded reads/timeouts. No secret values were printed or exported; configured API keys stay server-side in inspected analysis configuration. No dynamic secret scan or vulnerability-database certification was performed.

Provider, cache and ownership concerns: prefilter daemon survives return; focused-universe bulk call lacks the same timeout; event warmup uses unbounded `join()` at `SP:11331`; yfinance monkey-patches/global caches couple concurrent requests; disk-cache TTL does not preserve point-in-time provenance. Partial failures must never overwrite a known-good result without visible age/source status. Tests protect production cache paths, but audit setup still required strict scratch environment before imports.

Hard-coded search results are saved in [hardcoded-claims-and-names.txt](audit-picks/hardcoded-claims-and-names.txt), [literal-comparisons.csv](audit-picks/literal-comparisons.csv) and [security-search.txt](audit-picks/security-search.txt). Legitimate hits include universe seeds, benchmarks, examples and source identifiers. No newly verified company-specific buy/sell override was found in the scoped event/decision path. This is a scoped source review, not proof that every fact-stating sentence in all332 files is current. “Every decision threshold is in config and shown in the UI” is **refuted** by identified chase/India/Research literals and null gate labels. Factor weights are explicitly excluded by the config module's own design.

`pip check` verifies installed dependency compatibility only. Reliance on unofficial Yahoo interfaces remains an availability/schema/licensing dependency risk. Installed versions are recorded; no claim of latest or vulnerability-free versions is made.

## 8. Frontend state, usability and design changes

The form builds typed numeric values and a real checkbox boolean, disables submit during work, and the backend coalesces duplicate requests. Job state can be streamed or polled. UI retry changes the universe; cancellation cannot interrupt in-flight provider work. Existing navigation smoke tests pass on desktop/mobile. Tab switching during a *long live scan*, prolonged disconnect/reconnect and thousands of rerenders were not stress-tested, so race/leak freedom is U. Source inspection found no newly confirmed listener leak in the scoped Picks path.

No page-level horizontal overflow occurred at1280,1440,820 or390 in the captured states. Tables use their own constrained regions. Inputs inspected by the harness were labeled. Final result focus was frequently BODY after the disabled submit button cycle; there is no strong verified result-focus/announcement contract. A real screen-reader session and full keyboard-only traversal were not performed. Add a concise `role=status` completion announcement and preserve intentional focus; do not move it unexpectedly on background updates.

Axe found one serious issue: error heading light-theme contrast2.12:1. Nineteen console errors in the custom harness were blocked external resource/font loads, **not JavaScript runtime exceptions**. Fonts were therefore rendered with local fallbacks, which limits exact typographic comparison to the user's screenshot. Network blocking protected fixture isolation. Public market-provider calls were made only by the one bounded live scan, not by browser journeys.

### Expert journey scores

Scores1–5:1 obstructive,3 usable with friction,5 clear. H1 status visibility; H2 real-world language; H3 user control; H4 consistency; H5 error prevention; H6 recognition; H7 efficiency; H8 minimalism; H9 recovery; H10 help. These are **one expert's observations**, not user-study measurements. J5/J6 economics and fixture-derived numeric inconsistencies are excluded from production judgments.

| Journey | H1 | H2 | H3 | H4 | H5 | H6 | H7 | H8 | H9 | H10 | Main observation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| J1 First use/default | 4 | 4 | 4 | 4 | 3 | 4 | 3 | 3 | 3 | 4 | Controls and purpose clear; default universe cost not explained. |
| J2 Empty | 3 | 2 | 3 | 2 | 3 | 2 | 2 | 2 | 2 | 3 | Verdict clear; causal accounting hard to reconcile. |
| J3 Funded fixture | 4 | 3 | 3 | 3 | 3 | 3 | 2 | 2 | 3 | 3 | Levels visible but repeated; risk denominator differs from allocation denominator. |
| J4 Outage | 3 | 3 | 3 | 2 | 3 | 3 | 3 | 3 | 2 | 2 | Distinguishes provider failure from no picks; weak contrast/actionable recovery. |
| J5 Rerun | 3 | 4 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | Parameter controls work; need persistent run/parameter identity. |
| J6 India | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 3 | 2 | 2 | Market controls switch; no clear pre-scan warning that current event coverage prevents funding. |
| J7 Mobile | 3 | 3 | 3 | 3 | 3 | 2 | 2 | 2 | 3 | 3 | No horizontal page overflow, but setup and long diagnostics put result far down the page. |

All19 images were viewed in [contact sheet1](audit-picks/contact-sheet-1.jpg), [2](audit-picks/contact-sheet-2.jpg), [3](audit-picks/contact-sheet-3.jpg), [4](audit-picks/contact-sheet-4.jpg); empty, funded and error images were also opened at full size. The “no card more than15% empty” criterion is **not met by visual assessment** of the centered empty-state panel and funded risk tiles. No calibrated pixel occupancy measurement was made, so a percentage is not invented. Dense text is not a good remedy for every empty pixel. On mobile, moving optional setup/details behind disclosure would improve hierarchy more than filling space.

Empty-state readability: lead sentence is understandable within roughly5seconds of expert inspection; reconciling why the scan is empty takes much longer and requires code/payload knowledge. These are estimates, not measured user times. J2's internal `action=0; risk grade=0` syntax and undeclared thresholds undermine the plain-language copy. Trust cues improve with the hypothetical-book/evidence labels, but repeated disclaimers and multiple clocks dilute them.

| Section | User question | Does it answer? |
|---|---|---|
| Scan Setup | What am I scanning and with what constraints? | Mostly; no duration estimate/effective coverage or persistent per-market budget. |
| Proposed Allocation | What should I review now; how much hypothetical capital is used? | Yes for broad outcome; not a personal portfolio recommendation. |
| Empty verdict | Why no funded names, and what next? | Only partly: duplicated prose, missing first-failure accounting. |
| Scan Funnel | Where did the universe go? | No coherent single-stage interpretation today. |
| Rejection chart | What blocked candidates? | Partly; warning/rejection populations mixed and tail omitted. |
| Chase watchlist | Which recent moves merit caution, and when? | Partly; missing company/distance and undated trigger wording. |
| Funded row/card | Which decision, levels and reason bind? | Values repeat; level-source explanation still inconsistent in one detail block. |
| Proposed Book Risk | How concentrated is deployed capital and planned stop loss? | Labeled, but users must distinguish deployed-capital and total-capital denominators. |
| Pre-trade review/export | What conditions and symbols need later checking? | Useful secondary tasks; the checklist is not a completed live validation. |

### Required layout changes: at most five elements

1. **Compact scan verdict row:** funded/watch/rejected/failed counts, capital used, run date and parameters. Answers “what happened?” Replace the tall centered paragraph.
2. **Coverage and selection line:** actual universe selection method, provider status, quote/bar dates and event coverage. Answers “can I trust this run's inputs?”
3. **Stage audit table:** typed counts plus “not evaluated,” first-failure reason and separate warning counts. Answers “why did names drop out?” Replaces the misleading funnel/chart duplication.
4. **Single decision table with expandable detail:** authoritative action, binding reason, one level set/source/horizon, allocation and evidence status. Answers “which rule and price set decides?” Remove redundant level chips/sentences.
5. **Dated watch/event list with a contextual next step:** gap/run-up trigger, company, source session and unavailable fields explicitly labeled. Answers “what can change, and what should I review?”

Keep existing navigation, routes and financial policy. Use shared theme tokens for failure states, normal sentence casing and concise labels. Move one shared evidence/limitations note next to the result rather than repeating it per card. A stale or failed run should preserve its provenance and clearly offer retry with the same parameters.

## 9. Performance baseline and priorities

| Workload | Measurement | Interpretation |
|---|---|---|
| Live Quick100 | 280.1s total;90s prefilter budget hit | Almost32% of elapsed time spent on failed selection prefilter. Cache log loaded zero fresh info/history; not a paired cold/warm experiment. |
| Eight synthetic real-engine scans | 0.25–0.90s | Provider functions stubbed; first run does extra initialization. Counts in [timing.csv](audit-picks/timing.csv) are provider-method calls, not actual HTTP requests. |
| Seventeen selector/worker cases | 0.005–1.017s; counts50–1,200 according to synthetic universe | [universe-timing.csv](audit-picks/universe-timing.csv); reject stub means these are routing overheads, not real full-universe latency forecasts. |
| Offline cached100 research replay | 8.72s | No network; optional enrichments neutral. |
| 250 real per-name analyses on synthetic providers | 41.61s wall /39.95s CPU under cProfile+tracemalloc;1.56MB incremental Python heap peak | Instrumentation adds substantial overhead; heap excludes native allocations and process RSS. Two threads observed after run, not peak utilization. |
| Render5 rows | 22,692B;6.8ms synchronous /28.6ms visible;685 nodes | Desktop local sample. |
| Render50 rows | 208,128B;25.9ms /132ms;5,005 nodes | Same harness. |
| Render250 rows | 1,032,629B;77.2ms /427.6ms;24,205 nodes | Scaling risk for slower devices; one measurement, not p95. |

[frontend-timing.csv](audit-picks/frontend-timing.csv), [profile-summary.json](audit-picks/profile-summary.json), [synthetic-profile.txt](audit-picks/synthetic-profile.txt), [synthetic-profile.pstats](audit-picks/synthetic-profile.pstats) preserve measurements. Fixture click-to-result durations are recorded in `browser-report.json`; they measure local job/render overhead, not provider latency. The live log has one slow-response timeout warning and no verified429 rate-limit hit; timeout alone does not prove throttling. Exact live HTTP request counts, per-stage CPU/RSS and peak provider threads were not instrumented.

Profile hotspot: indicator calculation24.79s cumulative; rolling apply18.23s, with a Python lambda at `SP:4440` consuming14.04s cumulatively. Calls overlap; do not sum cumulative times. The 250 attempts include early rejects and recursive/alternate calls, so only124 reached full indicators. Results establish an optimization target, not a production speedup estimate.

| Priority | Improvement | Gain / risk and validation |
|---|---|---|
| 1 | Owned, bounded provider workers; avoid second bulk stage after timeout; cache selection inputs | High likely latency/reliability gain; medium integration risk. Verify no surviving worker, deterministic selection fallback and cancellation ownership. |
| 2 | Reuse one per-name completed-bar snapshot and enrichment record across scanner/Research | High likely network reduction; medium freshness risk. Cache by market, symbol, session, parameters and model version. |
| 3 | Publish stage counts/timers and source selection method | High perceived clarity, low model risk. Validate counters reconcile independently. |
| 4 | Replace rolling Python lambda with equivalent vectorized calculation | Measured CPU hotspot; low-to-medium numeric risk. Compare boundary/NaN/warmup behavior against frozen arrays. |
| 5 | Render compact rows first; mount detailed cards on expansion / paginate large lists | Reduces DOM at250 rows; low policy risk. Preserve accessibility, export and canonical values. |

Live timings for every universe and paired cold/warm runs were deliberately not manufactured: only one full live scan was used, and synthetic timings cannot fill that evidence gap. Larger-universe production performance remains P/U.

## 10. Test integrity, mutation results and contracts to add

Commands and environment are embodied in [run_suite.py](audit-picks/run_suite.py), [api_matrix.py](audit-picks/api_matrix.py), [synthetic_engine.py](audit-picks/synthetic_engine.py), [quant_checks.py](audit-picks/quant_checks.py), [browser_audit.cjs](audit-picks/browser_audit.cjs), and [playwright.audit.config.cjs](audit-picks/playwright.audit.config.cjs). Tests ran from the temporary code snapshot with scratch cache/regime/ledger/journal. Synthetic ledger tests were allowed to write only their isolated databases; tracking was disabled for scans. Initial attempts failed because the audit snapshot omitted public calendar/guide fixtures and because ledger writes were disabled too broadly. Those harness errors were corrected; their attempt logs remain available.

The local and `TZ=UTC` suite commands both passed, but [timezone-verification.json](audit-picks/timezone-verification.json) shows Windows retained Eastern timezone labels and lacks `tzset`. This is **not** proof of two OS timezone runs. Use a UTC container/CI runner and explicit clock/zone fixtures for that requirement.

Baseline comparison used `4f878f8` through the present worktree. Raw diff is saved in [test-integrity.diff](audit-picks/test-integrity.diff), with [numstat](audit-picks/test-change-numstat.txt) and changed assertions search. New strict xfails document missing whole-share sizing5, worker cooldown/circuit5, blue-sky target1 and insider calibration8. They fail visibly if an implementation unexpectedly starts passing, but **still exclude19 unresolved behaviors from green acceptance**. Nine skips comprise six empty golden-fixture/replay collections, two obsolete scanner APIs and missing optional Streamlit. [test-skips.json](audit-picks/test-skips.json) lists exact reasons.

Some fixture/assertion updates appropriately follow shared grade, binding levels, new mandates and new UI structure. Raw baseline comparison alone cannot establish semantic correctness of every changed expectation; no blanket “no weakened tests” claim is made. The chase mutation provides direct evidence of a real coverage hole. Golden fixture filenames existing elsewhere do not mean the skipped canonical replay collection is populated.

| Mutation | Broken behavior | Result |
|---|---|---|
| M1 | Bypass corporate-event gate | Killed by selected relevant Python suite. |
| M2 | Skip binding Research levels | Killed. |
| M3 | Allocate entire capital from position sizer | Killed. |
| M4 | Change scanner R:R threshold1.8→0.01 | Killed. |
| M5 | Add17 to actionable funnel count | Killed. |
| M6 | Bypass not-confirmed fail-closed marker | Killed. |
| M7 | Always return no chase alert | **Survived selected and complete Python+JS suites.** |
| M8 | Quick universe size100→7 | Survived Python, killed by full JS guide/source-threshold contract. |

All eight mutants were syntactically valid, applied in the separate temporary copy, and restored between runs. Mutation baseline passed. A source-string guide test catching M8 is useful but weaker than an executed selector contract. These spot checks are not a mutation-coverage percentage for the repository.

### Behavior matrix interpretation

API matrix includes US/India minimum/default/small/large/1e9/over-limit values, zero/negative/non-numeric/special/NaN/infinite inputs, every offered/legacy/invalid mandate, positions1/5/100 and invalid values, diversify booleans/coercions, every universe, bad/custom tickers, duplicate submit, conflicting active job, cancellation and retry before/after ownership release. It tests request policy, not every Cartesian combination's economics.

The17-universe worker matrix verifies selectors and routing, not per-name model soundness. Eight actual synthetic engine runs cover real analysis with controlled providers, including short/missing/invalid data and repeats. Timeout ownership is a synchronized provider probe, not a live provider outage injection. All-pass/one-funded actual-engine acceptance and broad randomized funded-book properties remain missing. The rendered one-funded fixture cannot substitute for them.

### Invariants and contract suite to implement

| Invariant / fixture | Required assertion |
|---|---|
| Funded book | Sum of final allocations≤capital; nonnegative cash; count of positive allocations≤max positions. |
| Position / sector risk | Final whole-share cost and stop risk respect per-name and sector caps after every haircut, Research change and backfill; document denominator. |
| Decision-funding agreement | Any denied/unknown/unconfirmed final new-money action implies zero allocation/shares, regardless of earlier signal. |
| Canonical levels | Finite stop<entry<target for fundable rows; R:R recomputes from published levels/costs within displayed tolerance; horizon/source identified. |
| Accounting | Every universe member has one terminal analysis outcome; first rejection counts reconcile exactly; warnings tracked separately; monotonic stages only within the same cohort. |
| Determinism | Same frozen inputs, clock, config/model version and market yield identical actions/levels/allocations/reasons; timestamps compared separately. |
| Provider ownership | Timed-out/cancelled provider processes terminate and are reaped before retry admission; no fallback overlaps an abandoned worker. |
| Chase | Independent OR-branch/boundary tests for each gap/5d/10d threshold, growth overrides, split adjustment, missing bars and weekend source date. |
| Request shape | Non-object/malformed JSON, wrong booleans/fractional counts, market minima and empty custom all return400 and create no job. |
| Response contract | Typed versioned `scan`, `coverage`, `stages`, `warnings`, `rejections`, `funded`, `watch`, `events`; thresholds have value/unit/evaluated/source. |
| State fixtures | Empty universe, one funded, all fail, all pass, watch-only, partial data, provider outage, stale data, event unavailable and market mismatch. |
| UI consistency | Summary/table/card use identical final values; no invented defaults for missing fields; result tied to submitted market/capital/run ID. |

Audit fail-closed invariants passed for eight actual synthetic scans, but all had zero funded rows, so capital/sector assertions there are trivial. Independent sizing tests are nontrivial at the single-position level. Repeat synthetic results match business fields after removing declared generation/retrieval/job timestamps; this is same-process determinism under controlled providers, not a guarantee across refreshes or live market data.

## 11. Prioritized roadmap and release criteria

**Quick wins (first):** strict request validation; separate warnings/rejections and classify by funded count; fill/date chase fields; render “not evaluated”; fix error contrast and stale level-source prose; preserve market budgets and retry parameters. Add focused acceptance tests with each change. These changes improve correctness without relaxing risk rules.

**Reliability work (next):** bounded provider process ownership, event publication independent of candidate survival, effective-selection/provenance fields and one canonical scan event record. Consolidate final sizing/minimum/cap enforcement, resolve the explicit F11 expected failures, and add nontrivial all-pass/funded/randomized book tests. Reconcile score naming and known literal policy thresholds. Keep current India/event fail-closed behavior until actual coverage exists.

**Research/product work (strategic):** a separately versioned model-book lifecycle, scheduled selection/review cadence, interpretable peer features if supported by incremental evidence, predeclared exits and hold-duration policy, portfolio benchmark and forward-only record. No copied proprietary methodology; no claimed alpha until QUANTA's own complete validation supports it.

Release criteria for the correctness repair: reproduce A01/A02/A03 before fixes; tests fail under the corresponding mutants; new API rejects before job creation; selection fallback and data gaps are visible; zero-funded/watch states are consistent across API/UI; all counts reconcile; all published funded levels/sizing obey invariants; screenshots and axe pass on four widths/two themes; owned workers are absent after cancel/timeout; source-preservation check distinguishes authorized changes from unrelated work.

## 12. Limits, retained evidence and handoff

- **No production remediation was authorized by the detailed read-only brief.** This deliverable resolves the audit/diagnosis and supplies reviewable fixes; it does not claim the underlying defects have been patched.
- One live US Quick scan only. No live India/full-universe acceptance or full cold/warm performance matrix; no provider load test.
- Public replay lacks calendar/revision/insider/event snapshots and is not an exact reproduction of live final scores. Public cache retained; no brokerage data retained.
- Full independent reconstruction of every input and eight live funded decisions was not possible from the captured output. Synthetic arithmetic and boundaries are the verified scope.
- No independent numeric VIX/index confirmation; no current strategy out-of-sample/forward performance validation; no production ledger completeness review.
- Yahoo current product pages could not be opened directly; those features remain U. Reference products' claims are not independently validated returns.
- Windows `TZ=UTC` did not change actual runtime timezone. No real screen-reader session, exhaustive XSS/export fuzzing, memory-leak stress, race certification or dependency CVE audit.
- Whitespace and Nielsen scores are expert observations; no user study, measured comprehension time or exact15% occupancy analysis.
- Existing browser suite was scoped to eight relevant smoke checks, supplemented by seven Picks journeys; it was not the entire unrelated app E2E suite.
- The supplied screenshot predates the already-present inline-emphasis CSS fix. That fix is credited to the worktree, not this audit.

Reproduction entry points and logs are all under [audit-picks](audit-picks/PROGRESS.md). The temporary code/mutation snapshots and owned server are removed during finalization; [cleanup.json](audit-picks/cleanup.json) records that result. [preservation-summary.json](audit-picks/preservation-summary.json) records comparison of the332 original source/test hashes and the final Git state against the initial state. Port5500 was not restarted or controlled. No commit, branch, merge or push was made.

