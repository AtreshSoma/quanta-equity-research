# SP_ELITE scanner comparison

The comparison below is the original **pre-port snapshot**. The later, explicitly requested implementation and its validation are recorded under **Selective integration** at the end; original line references and comparison results remain historical.

Compared on 2026-10-05 (America/New_York), at repository HEAD `99ecf40aa327611895df5189e065d6d9f24a6b93`. PRO means `stock-analysis/core/SP_ELITE_PRO.py`; Main means `stock-analysis/core/SP_ELITE_PRO_Main.py`. All source-line references below refer to the original workspace snapshot, including Main references after it was substituted under PRO's filename in the temporary copy.

**Overall winner: PRO, because it matches the app's tested contracts, enforces corporate-event and issuer safeguards, and keeps published Research levels and final sizing consistent.** This is an integration and safety judgment, not a claim about investment returns.

| Area | SP_ELITE_PRO.py | SP_ELITE_PRO_Main.py | Better |
| --- | --- | --- | --- |
| git state | Tracked, clean; 57 commits with rename-following history | Untracked; no history | PRO |
| size | 14,985 lines; 739,839 bytes | 13,766 lines; 681,665 bytes | Main |
| last modified | 2026-10-05 20:09:51 UTC (16:09:51 EDT) | 2026-10-04 01:42:52 UTC (Oct 3, 21:42:52 EDT) | Unknown |
| used by the app | Yes: Flask `app.py:5343`, engine mapping `app.py:732`; Streamlit `streamlit_app.py:221` | No imports/startup references found | PRO |
| structure | 302 explicit function definitions; 42 classes | 256 explicit function definitions; 44 classes | Tie |
| corporate-event gate (fail closed) | Yes: `PRO:11888`, missing/failed check held at zero; called before book selection at `PRO:12109` | Absent; actionable candidates proceed directly to risk gate at `Main:11114` | PRO |
| Research levels bind published R:R | Yes: `PRO:11356`; publishes Research entry/stop/target/R:R and resizes using its stop | Absent; describes two conflicting R:R bases at `Main:10773`, without binding Research levels | PRO |
| shared risk grade | Yes: `PRO:11409`, full-history shared grade and disclosed partial fallback | Partial: shared point calculation at `Main:8340`, neutral tail/liquidity inputs; separate legacy fallback at `Main:8350` | PRO |
| thresholds from engine/thresholds.py | Yes: import `PRO:1775`; read-through aliases, e.g. `PRO:1847` | Partial indirect shared decision policy (`Main:119`); scanner-local hard-coded values remain, e.g. `Main:1699`, `Main:9296` | PRO |
| chase rules from config | Yes: `PRO:10151` reads `_T.CHASE` | No: inline gap/5-day/10-day thresholds at `Main:9473` | PRO |
| bounded provider timeouts | Yes for bulk workers: `PRO:8216`; child terminated/reaped before fallback | Partial caller waits: daemon downloads at `Main:9807`, `Main:9923` survive timeout; focused selector directly downloads at `Main:7384` | PRO |
| universe selection record | Yes: `PRO:8112`, `PRO:8208`; method/cause/budget/coverage | No equivalent; aggregate pipeline counts at `Main:10411` | PRO |
| per-name outcome counting | Yes: `PRO:7069`, `PRO:7292`; one terminal outcome per name | Partial rejection records (`Main:6475`) and aggregate counters (`Main:10411`); overlapping filter counts | PRO |
| final sizing | Yes: `PRO:2473`, `PRO:2515`, final allocator call `PRO:12721` | Partial: atomic allocation at `Main:2200`, but lacks the final cap/minimum enforcement inside it | PRO |
| minimum position | Yes: `PRO:2119`, checked before and after share rounding at `PRO:2518`, `PRO:2527` | Early checks at `Main:9440`; rounding/deep downgrade can leave smaller funded amounts | PRO |
| deep-confirm downgrade | Yes: `PRO:11432`; missing verdict also removes funding at `PRO:11630` | Yes: `Main:10586`, downgrade at `Main:10788`; missing verdict preserves funding at `Main:10757` | PRO |
| same-issuer cap | Yes: SEC CIK identity `PRO:12247`, rejection `PRO:12580` | Partial exchange-suffix dedupe only, `Main:11529`; distinct share classes remain independent | PRO |
| SEC contact handling | Yes through gate: `PRO:11909` → `engine/corporate_events.py:620` → `portfolio/sources.py:211`, valid contact required | Shared app startup contact resolution still applies (`app.py:4552`); optional catalyst integration `Main:157` has no required-contact funding gate | PRO |
| tests | 1,791 pass; 2 fail; 0 errors (omitted fixtures) | 1,569 pass; 195 fail; 5 errors | PRO |
| speed | Synthetic scan 1.768 s + book 0.080 s = 1.847 s | Synthetic scan 2.534 s + book 0.008 s = 2.542 s | Unknown |
| behavior differences | 26 analyzed rows; 3 funded; 0 funded with event checks unavailable | 39 analyzed rows; 2 funded; still 2 funded with event checks unavailable; 25/26 shared rows differ in action | Unknown |
| safety | Blocks unavailable events, second same-CIK class and below-minimum rounded allocation in probes | Funds unavailable-event names, both same-CIK classes and below-minimum rounded allocation | PRO |
| code quality | Ruff error gate clean; 15 unused imports + 8 unused locals; clearer shared contracts | Same lint counts; more local policy duplication and API incompatibilities | PRO |

A smaller file wins the size row only; it does not establish better design. Newer modification time, counts of functions/classes, and different funded counts are descriptive facts, not quality or investment-performance scores. The speed row is Unknown as a general claim because this was one synthetic sample with different analyzed populations, run while each variant's suite was active; PRO was faster in that sample.

## Facts and integration

Initial `git status --short` contained only the pre-existing `?? .impeccable/` and `?? stock-analysis/core/SP_ELITE_PRO_Main.py`. Neither was changed. PRO had no worktree or staged changes. Main has zero path-history entries and is not tracked. PRO's oldest rename-following entry was `8c587fb` (2026-02-27); its latest five path commits were:

| Commit | Date (EDT) | Subject |
| --- | --- | --- |
| 903fd0f | 2026-10-05 16:11:57 | Picks: company news and headline catalysts come from Yahoo's RSS feed |
| 39a383d | 2026-10-05 15:19:54 | Scan types do what their labels say: index lists, style screens, ETF scoring |
| 5b64106 | 2026-10-05 13:09:32 | Picks: finish-review fixes (truthful reasons, whole reasons, quieter header) |
| f2b7800 | 2026-10-05 12:43:44 | Picks: publish one ranked stock-picks table; small-cap scans get their own cap floor |
| b6de21f | 2026-10-05 11:43:10 | P8: the scan menu states the basis the selector really uses |

App startup leads to `ui/stock-analysis/webapp/app.py`: `stock-analysis/start.ps1:72`, `stock-analysis/launch_quanta.bat`, and `ui/stock-analysis/run_stock_analysis.sh` all select that app. The app imports PRO lazily in the Picks worker at line 5343 and maps its engine source at line 732. Streamlit imports PRO at `stock-analysis/core/streamlit_app.py:221`. A source search found 39 Python test files importing PRO or explicitly reading its filename. No Main import or startup reference was found in those application/test paths.

**VS Code's one problem:** not verified. No available tool exposes the live native Problems panel. A request for its file, line and message was sent while the review continued. Both copied scanners passed the repository's Ruff error selection (`E9,F63,F7,F82`); this does not identify, reproduce or dismiss a Pylance/VS Code diagnostic.

Source SHA-256 values, rechecked after the work:

- PRO: `527A0D8F4FEDAA0A62050F603547B244F21757AA562BAB4C6B65C9A5592421A7`
- Main: `A4918993FE9114D0B2DB157784C996C7CC59E751492D0319CDDF4ED8DA2F1F39`

## Structure and similarity

AST counts include methods, nested functions and conditional fallback definitions; generated dataclass methods are excluded. No duplicate qualified function/class definitions were found within either file.

| Measurement | Count |
| --- | ---: |
| Common function names | 239 |
| Identical function source | 159 |
| Similar changed functions (source-text SequenceMatcher ratio ≥ 0.75) | 51 |
| Different changed functions (ratio < 0.75) | 29 |
| Functions only in PRO | 63 |
| Functions only in Main | 17 |
| Classes shared | 42 |
| Classes only in PRO / Main | 0 / 2 |
| Identical lines in order (SequenceMatcher, autojunk disabled) | 11,258 |
| Replaced-region lines: PRO / Main | 2,060 / 1,886 |
| Unmatched deletion / insertion lines: PRO / Main | 1,667 / 622 |

Identical lines cover 75.13% of PRO and 81.78% of Main. These are text and structural measures, not semantic equivalence. A changed large function may contain substantial shared code despite falling below the similarity cutoff.

### Definitions only in PRO

| Function (qualified name) | Line |
| --- | ---: |
| `DataValidator.split_like_days` | 983 |
| `MarketDataProvider._read_html_tables` | 3914 |
| `MarketDataProvider.get_index_constituents` | 3987 |
| `QuantaEliteTradingSystem._apply_event_gate` | 11888 |
| `QuantaEliteTradingSystem._bind_research_levels` | 11356 |
| `QuantaEliteTradingSystem._bind_scanner_levels` | 11331 |
| `QuantaEliteTradingSystem._book_stage` | 7114 |
| `QuantaEliteTradingSystem._bounded_bulk_download` | 8216 |
| `QuantaEliteTradingSystem._capital_for_one_share` | 7185 |
| `QuantaEliteTradingSystem._chase_trigger` | 10133 |
| `QuantaEliteTradingSystem._chase_trigger._day` | 10168 |
| `QuantaEliteTradingSystem._chase_trigger._hit` | 10174 |
| `QuantaEliteTradingSystem._chase_trigger._pct` | 10188 |
| `QuantaEliteTradingSystem._company_news` | 9804 |
| `QuantaEliteTradingSystem._deep_confirm_final_picks._deep_analyze` | 11521 |
| `QuantaEliteTradingSystem._deep_confirm_final_picks._deep_analyze._run` | 11530 |
| `QuantaEliteTradingSystem._fetch_catalyst_boost._conf` | 9869 |
| `QuantaEliteTradingSystem._fetch_catalyst_boost._relevant` | 9835 |
| `QuantaEliteTradingSystem._fetch_insider_signal._num` | 9710 |
| `QuantaEliteTradingSystem._fund_at_least_one_share` | 11350 |
| `QuantaEliteTradingSystem._note_outcome` | 7069 |
| `QuantaEliteTradingSystem._note_selection` | 8208 |
| `QuantaEliteTradingSystem._note_selection_skip` | 7179 |
| `QuantaEliteTradingSystem._note_survivor` | 7103 |
| `QuantaEliteTradingSystem._publish_event_held` | 11986 |
| `QuantaEliteTradingSystem._report_progress` | 7092 |
| `QuantaEliteTradingSystem._risk_grade_for` | 11409 |
| `QuantaEliteTradingSystem._select_index_universe` | 8184 |
| `QuantaEliteTradingSystem._start_event_warmup` | 11871 |
| `QuantaEliteTradingSystem._start_event_warmup._warm` | 11879 |
| `QuantaEliteTradingSystem.analyze_stock._size_for` | 8961 |
| `QuantaEliteTradingSystem.closest_to_qualifying` | 7123 |
| `QuantaEliteTradingSystem.event_held_diagnostics` | 7223 |
| `QuantaEliteTradingSystem.explain_empty_scan` | 7034 |
| `QuantaEliteTradingSystem.get_top_recommendations._issuer_key` | 12247 |
| `QuantaEliteTradingSystem.get_top_recommendations._try_add` | 12576 |
| `QuantaEliteTradingSystem.passed_not_funded` | 7204 |
| `QuantaEliteTradingSystem.scan_accounting` | 7292 |
| `ScoringEngine.calculate_fundamental_score._present_raw` | 6017 |
| `ScoringEngine.normalize_scores.blend` | 6458 |
| `TradingConfig._tier_costs` | 2172 |
| `TradingConfig.effective_min_position_size` | 2119 |
| `TradingConfig.max_position_dollars` | 2140 |
| `TradingConfig.min_position_size` | 2135 |
| `TradingConfig.one_share_allowed` | 2145 |
| `TradingConfig.to_usd` | 2114 |
| `_extract_news_items` | 641 |
| `_extract_next_earnings_date._is_date_key` | 749 |
| `_fund_score` | 2535 |
| `_headlines_about` | 709 |
| `_json_safe` | 390 |
| `_new_money_for` | 3685 |
| `_quote_time_iso` | 1778 |
| `_rss_news_items` | 675 |
| `_twelve_month_anchor` | 468 |
| `apply_allocation._none` | 2509 |
| `decision_for_scan_result` | 3595 |
| `earnings_size_multiplier` | 1807 |
| `india_menu_counts` | 14462 |
| `indicator_window` | 3576 |
| `min_market_cap_for` | 1801 |
| `parse_capital_input` | 14435 |
| `validate_recommendation._finite` | 2376 |

### Definitions only in Main

| Function (qualified name) | Line |
| --- | ---: |
| `FundamentalMetrics.has_metric` | 2815 |
| `QuantaEliteTradingSystem._apply_universe_scoring` | 8717 |
| `QuantaEliteTradingSystem._empty_scan_explanation` | 6488 |
| `QuantaEliteTradingSystem._entry_rsi_limit` | 8873 |
| `QuantaEliteTradingSystem._reconcile_rescored_analysis` | 8797 |
| `QuantaEliteTradingSystem._record_candidate_rejection` | 6475 |
| `QuantaEliteTradingSystem._signal_rejection_detail` | 8877 |
| `QuantaEliteTradingSystem.display_stock_picks` | 10994 |
| `QuantaEliteTradingSystem.export_results._csv_row` | 12836 |
| `QuantaEliteTradingSystem.get_stock_picks` | 10921 |
| `QuantaEliteTradingSystem.prefetch_history._download_chunk` | 9807 |
| `QuantaEliteTradingSystem.smart_prefilter._bulk_download` | 9923 |
| `ScoringEngine.apply_regime_weights` | 5787 |
| `_ScannerTicker.__getattr__` | 1672 |
| `_ScannerTicker.__init__` | 1627 |
| `_ScannerTicker._read` | 1635 |
| `_ScannerTicker.history` | 1675 |

Classes only in Main: `_ProviderCooldown` (1596), `_ScannerTicker` (1600). No classes are unique to PRO.

## Tests and execution controls

Tests ran once per variant in a disposable copy outside the project folder. The copy contained 470 tracked files from `stock-analysis/` and `ui/stock-analysis/`, plus untracked Main, excluding 46 portfolio-related fixture/image/audit files to avoid account data. It omitted the live worktree's untracked data, caches, credentials, virtual environment and Git metadata. Tests used the existing Python 3.12.10 / pytest 9.1.1 environment read-only; bytecode writing was disabled.

The suite scope was the configured QUANTA suite in `stock-analysis/pyproject.toml`: `tests/` and `core/apex_quant/tests/`. This was not the unrelated monorepo Python suites or JavaScript/browser tests. Arguments were `-q -m "not network" --continue-on-collection-errors`, with per-variant JUnit and temporary pytest directories. Main was copied over `SP_ELITE_PRO.py` only inside the disposable copy, so imports and source-contract tests exercised Main as the scanner module.

All temporary cache, ledger and pytest paths were isolated. Python socket operations rejected external endpoints and ports below 5800; port zero meant an OS-selected ephemeral test port. No app launcher was executed and no operation connected to or bound port 5500. Tests' literal example URLs containing 5500 were strings used with fakes/test clients. No account files or credentials were copied, no real account was used and no trades were placed.

| Variant | Passed | Failed | Errors | Skipped / expected failures | Duration |
| --- | ---: | ---: | ---: | --- | --- |
| PRO | 1791 | 2 | 0 | 5 skipped; 14 xfailed | 175.06 s |
| Main | 1569 | 195 | 5 | 5 skipped; 14 xfailed | 445.73 s |

PRO's two failures were `test_activity_records.py::test_the_activity_fixtures_carry_the_current_config` and `test_my_portfolio.py::test_ui_fixture_matches_build_view`: the intentionally omitted `my_portfolio_insights.json` and `my_portfolio_view.json` were unavailable in the copy. They are copy-scope limitations, not demonstrated PRO regressions; the suite is nevertheless reported as two failures, not a full pass. Main encountered the same omitted-fixture limitations.

First Main-specific failures, after the common omitted-fixture failure:

| Test | Observed cause |
| --- | --- |
| `test_audit_remediation_0927.py::test_research_and_scanner_share_the_dividend_resolver` | Source-contract assertion: Main does not call the shared dividend resolver |
| `test_audit_remediation_0927.py::test_realized_vol_proxy_is_labelled_and_not_cached_as_last_good` | `MarketRegime.vix_source` missing |
| `test_audit_remediation_0927.py::test_no_vix_and_no_proxy_is_a_flagged_placeholder` | Same missing provenance field |
| `test_binding_levels.py::test_binding_levels_are_published` | `StockAnalysis.binding_levels` missing |
| `test_binding_levels.py::test_agreed_pick_is_sized_on_the_binding_stop` | Same missing level-binding field |
| `test_bounded_provider.py::test_a_prefilter_timeout_is_recorded_and_blocks_a_second_bulk_stage` | Selection method stayed `None`, expected `even_spread` plus a recorded timeout cause |
| `test_bounded_provider.py::test_a_successful_prefilter_is_liquidity_ranked` | Fallback sampled names instead of expected liquidity ordering under the test's bounded-provider target |
| `test_chase_alerts.py::test_a_gap_above_the_threshold_is_a_dated_gap_trigger` | `_chase_trigger` missing |

Main's five errors comprise one collection error in `test_scan_types_and_why.py:315` (`_rss_news_items` missing) and four `test_stock_picks_table.py` setup errors: the app worker requires `TradingConfig.effective_min_position_size`, which Main lacks. These demonstrate app/test interface incompatibility, not just different score expectations.

Main printed its complete summary and wrote JUnit output, but its runner stayed alive afterward. The two processes whose command lines identified this temporary Main runner were stopped before cleanup. No unrelated process was stopped. The specific surviving thread's cause was not traced.

No suite was rerun. Standalone benchmark and safety probes were separate synthetic executions, not a second suite run.

## Matched synthetic behavior

Both used the same 100 symbols (`SYNAA` through `SYNDV`), fixed 520-session OHLCV frames ending 2026-10-02, and a repeating eight-case fixture: two sound companies, missing earnings calendar, short history, thin volume, small market cap, missing company information, and a raised provider exception. Price paths vary by symbol index identically for both. The scan used two workers, an artificial capital input, the same BULL regime and fake BUY Research verdict. Bulk prefetch was disabled; all ticker histories/info and event verdicts were synthetic. No account amounts are reported here.

The timed interval covers `scan_market` and `get_top_recommendations`, excluding imports/initialization and the additional safety allocator runs.

| Measurement | PRO | Main |
| --- | --- | --- |
| Time: scan / allocation / total | 1.768 / 0.080 / 1.847 s | 2.534 / 0.008 / 2.542 s |
| Requested / analyzed result rows | 100 / 26 | 100 / 39 |
| Funded result rows | 3 | 2 |
| Funded symbols | SYNDC, SYNAQ, SYNBI | SYNBO, SYNAK |
| Shared analyzed symbols / differing actions | 26 / 25 | 26 / 25 |
| Actions across analyzed rows after allocation | BUY 4; ACCUMULATE 22 | WATCHLIST 30; ACCUMULATE 3; AVOID 6 |
| Raised provider exceptions recorded | 12 | 12 |
| Terminal could-not-analyze outcomes | 37 (13 insufficient history; 12 missing info; 12 exceptions) | No equivalent reconciled per-name API |
| Terminal rule rejections | 37 (13 low R:R; 12 low volume; 12 small cap) | Overlapping reason counters, not mutually exclusive terminal outcomes |
| Reached book / not analyzed | 26 / 0 | No equivalent reconciled per-name API |
| Missing-calendar warnings | 13 | 13 |
| Event checks unavailable: funded | 0 | 2 (SYNBO, SYNAK) |

PRO reconciles exactly: 37 could not be analyzed + 37 rule rejected + 26 reached the book + 0 not analyzed = 100. Its book stages go 26 actionable → 26 event-clear → 26 grade-qualified → 20 score-qualified → 20 R:R-qualified → 20 conviction/cost-qualified → 3 selected → 3 confirmed.

Main's counters were: signal-not-actionable 15, low R:R 14, committee-hold 24, size-below-minimum 24, insufficient-data 13, low-volume 12, missing-calendar 13, small-cap 12, missing-info 12, analysis-exception 12. These can describe the same ticker at several stages and include warnings; summing them would misstate terminal outcomes. Its 13 additional analyzed symbols correspond to the second fixture pattern, which PRO rejected for low R:R.

Examples of actual same-symbol action differences after allocation (funded means membership in the final returned funded book, not an intermediate allocation on a scan row):

| Synthetic ticker | PRO action / final funded membership | Main action / final funded membership |
| --- | --- | --- |
| SYNAK | BUY / not funded | ACCUMULATE / funded |
| SYNAQ | ACCUMULATE / funded | WATCHLIST / not funded |
| SYNBI | ACCUMULATE / funded | WATCHLIST / not funded |
| SYNDC | BUY / funded | WATCHLIST / not funded |

The disjoint funded lists and differing actions demonstrate non-interchangeability; they do not prove either model's predictions are better.

## Safety and quality findings

Three executable probes establish funding behavior that PRO blocks and Main permits:

| Probe | PRO | Main |
| --- | --- | --- |
| Event checker returns unavailable for all scanned candidates | Zero funded | Two names remain funded |
| Two synthetic share classes with one SEC CIK, otherwise eligible and independent histories | Only SYNCLASSA funded | SYNCLASSA and SYNCLASSB funded |
| Whole-share rounding drops a proposed position below the configured minimum | Allocation zero | A smaller positive allocation survives |

The event and issuer probes injected verdicts rather than making SEC requests. The rounding probe isolates `apply_allocation`; it explains how a final resize can bypass Main's earlier minimum checks.

Other material differences:

- **Confirmation without a verdict:** PRO calls `_mark_not_confirmed` at `PRO:11630` and zeroes the trade. Main logs an N/A verdict and preserves allocation at `Main:10757`. Both implement explicit conservative downgrades, but their incomplete-confirmation behavior is different.
- **Published levels:** PRO binds Research levels at `PRO:11356` and uses the Research stop to resize risk. Main reports discrepancies at `Main:10773`, leaving scanner levels in place. A superficially matching action can therefore carry different R:R and risk sizing.
- **Provider ownership:** PRO's subprocess bulk timeout kills/reaps before fallback (`PRO:8216`). Main abandons daemon workers (`Main:9807`, `Main:9923`), allowing in-flight requests to overlap subsequent stages. Its focused-selector download at `Main:7384` has no equivalent owned-worker deadline.
- **Score-policy coupling:** PRO's normalization changes `ranking_score` while preserving absolute gate inputs (`PRO:6423`). Main mutates `overall_score` and reconciles actions/sizes afterward (`Main:8717`, `Main:8797`), so the scored population changes the policy-bearing score.
- **Different grade eligibility:** PRO reads `BOOK.BANNED_GRADES` (currently F) and can allow grade D with the shared reduced-size policy; Main hard-codes A/B/C admission at `Main:11114`. Main is stricter in this respect. The safety winner refers to the demonstrated event/issuer/final-size safeguards, not uniformly stricter thresholds.
- **Duplicated policy:** Main retains local risk-grade thresholds after its shared calculation (`Main:8350`), local committee hurdles (`Main:9296`) and local chase cutoffs (`Main:9473`). PRO centralizes the compared admission/sizing/chase policies, though it still contains heuristics and curated universe data.
- **Hard-coded names:** both contain named ETF/sector/universe/crypto baskets (PRO `2300`, `2747`, `2764`; Main `2056`, `2443`, `2460`). Some determine eligibility or special classification and require maintenance. The checked literal-ticker decision guard did not establish arbitrary ticker-specific promotion rules; named lists are distinct from a single-ticker exception.
- **Lint and dead assignments:** both pass the configured Ruff error gate and each has 15 F401 unused-import findings and 8 F841 unused-local findings. Examples: PRO `5082`, `5083`, `10864`; Main `4531`, `4532`, `13641`. F401 can include intentional re-exports/side effects; these findings do not prove entire functions are dead. No duplicate qualified definitions were found. Shared/repeated fallback policy is the more material duplication.
- **Error handling:** PRO contains 108 broad exception handlers and 8 pass-only handlers; Main has 106 and 7. Both `main()` functions return a failure exit code on fatal exceptions (PRO `14929`; Main `13709`). Counts alone do not establish safety. Main's missing scanner interfaces and mismatched fields produce the concrete test failures documented above; Ruff cannot find those runtime contract failures.

## Pieces in Main worth keeping

- `_ScannerTicker` (`Main:1600`), especially `_read` (`Main:1635`): distinguishes required failures, optional enrichment gaps and requests deferred by provider cooldown. Its “fast_info is required” policy and cached `history` behavior need independent review before any port.
- `_record_candidate_rejection` (`Main:6475`) and `_signal_rejection_detail` (`Main:8877`): retain detailed rejection explanations. PRO already has richer terminal accounting and closest-to-qualifying diagnostics; any port should add explanation detail to those contracts.
- `get_stock_picks` (`Main:10921`) and `display_stock_picks` (`Main:10994`): useful explicit presentation of research eligibility and trade permission as separate facts. Current PRO already has a ranked Picks surface, so preserve useful evidence fields, not a duplicate page/engine.
- `FundamentalMetrics.has_metric` (`Main:2815`): a small availability/finite-value accessor. Its empty-map fallback assumes values are available, so that default should not be carried over without checking PRO's stricter missing-data policy.

**Recommended action only:** keep PRO as production and port only independently validated provider/rejection diagnostics if they add information missing from PRO. Do not replace PRO with Main. No port, archive or other action was performed.

## Verification limits

- The exact VS Code problem (file, line, message) remains unavailable without the live Problems panel or its text from the user. Ruff is a separate diagnostic system.
- Two intentionally omitted portfolio fixtures prevented a fully passing baseline. No original fixture/account data was read to resolve those failures.
- The configured QUANTA suite ran; unrelated monorepo suites, browser tests and a live app startup were not run.
- The timing is one synthetic run per variant, with suites running concurrently, fake Research results and different surviving populations. It does not verify live-provider throughput, SEC availability, cached-market replay, or predictive/investment performance.
- The synthetic scan disabled network and bulk prefetch. Static inspection and existing timeout tests cover provider ownership; the replay does not measure live timeout recovery.
- Main's collection error means one test module never ran; failure/error counts are not interchangeable with complete coverage.
- The syntax/AST review, lint and tests do not prove that every error path, dead-code path or branch is safe.

## What changed during the read-only comparison

Nothing in code was changed. The only workspace file written was `docs/reference/SP_ELITE_COMPARISON.md`. No files were staged or committed; no branches, merges, pushes, app deployments or trades were performed. PRO and Main hashes were unchanged and the existing untracked artifacts were preserved.

Created and then deleted the entire temporary tree `<temp>\quanta-scanner-compare-d8518d82a01943d7931c2527816acc05\`, including:

- `repo/`: 470 copied tracked files plus Main; only its scanner filename was substituted for Main testing. Generated test/log/cache artifacts within this copy were also deleted.
- `PRO-original.py`, `manifest.json`, `analysis.py`, `run_tests.py`, `benchmark.py`, `safety_probes.py`.
- `structure.json`, `similarity.json`, `lint-debt.json`, `behavior-comparison.json`, `safety-probes.json`, `test-summary.json`.
- `PRO-tests.log`, `PRO-tests.xml`, `Main-tests.log`, `Main-tests.xml`.
- `PRO-benchmark.log`, `PRO-benchmark.json`, `Main-benchmark.log`, `Main-benchmark.json`, `safety-probes.log`.
- `runtime-PRO/`, `runtime-Main/`, `benchmark-PRO/`, `benchmark-Main/`, `safety-probes/`, including all descendant pytest temporary files, synthetic ledgers, caches and logs.

Cleanup verified: 589 temporary files and their parent tree were deleted; the root no longer existed and no task-owned Python process remained. Final Git status added only this report to the initial two untracked entries.

## Selective integration — 2026-10-05

The user subsequently authorized carefully integrating Main's useful pieces into PRO and making one local commit after validation. PRO remains the production engine. Main was read as a reference and was not executed or changed during this implementation.

| Port | Production implementation | Preserved contract |
| --- | --- | --- |
| Provider diagnostics | `_ScannerTicker`, `_record_provider_read`, `provider_diagnostics`; per-field required/optional, available/empty/failed/deferred state, exception class and retry-failure count | Existing provider retries, timeout arguments, pacing, circuit behavior and optional `fast_info` fallback. No response bodies or exception messages are copied into the new diagnostic records. |
| Rejection explanations | `_record_candidate_rejection`, `candidate_rejections`, `_signal_rejection_detail`; first score/signal/sizing/R:R rejection per name, using current configuration | Existing terminal outcome counts remain authoritative. Explanations do not increment counts; research-mode scan survivors are excluded. Per-scan stores reset on reuse. |
| Strict metric availability | `FundamentalMetrics.has_metric`, used in fundamental ranking populations and normalization | Missing flags stay unknown; real zero is valid; booleans, strings, complex values, NaN and infinity are excluded. Main's permissive empty-map fallback was not copied. |
| Research evidence | `StockAnalysis.research_evidence`, serialized in existing Picks rows and JSON exports; includes factor inputs, available fundamentals, provider observations and timestamps | One existing trade decision and one set of binding levels. No independent rating, duplicate research board, post-hoc action rewrite or new funding permission. |

The new diagnostics are accessible through the existing scan API (`error_summary.provider_reads`, `candidate_rejections`, `stock_picks[].research_evidence`) and detailed JSON exports. Empty-scan text includes up to five candidate explanations. The existing visible Picks layout is unchanged.

The only intentional ranking-input change is rejecting nonfinite or nonnumeric fundamental values even if an availability flag was incorrectly true. Valid-data scores and decisions remain unchanged in the synthetic comparison. Corporate-event fail-closed behavior, Research-bound levels, risk grades, chase rules, issuer limits, deep confirmation, final sizing and minimum positions remain PRO's existing implementation.

### Implementation validation

All Python runs used the existing Python 3.12.10 environment against a disposable copy outside the project, with temporary caches/ledgers and synthetic or recorded test providers. No live account was accessed, no trades were submitted, and no server or port was used.

| Check | Result |
| --- | --- |
| Initial focused regressions | 87 passed |
| Final diagnostics, export, deep-confirm and fixture checks | 43 passed |
| Final complete configured Python suite, `python -m pytest -q` from `stock-analysis` | **1,813 passed; 0 failed; 0 errors; 5 skipped; 14 expected failures**, 100.59 seconds. Includes 20 new diagnostic-port cases. |
| Existing web UI unit suite, `npm.cmd test` | **532 passed; 0 failed**, 12.37 seconds |
| Configured Ruff error gate on changed Python files | Passed |
| Whitespace validation, `git diff --check` | Passed |
| Tested-source SHA-256 comparison | Production scanner, app adapter and new tests match the files tested in the disposable copy |

An earlier full run had one missing-fixture failure because `my_portfolio_insights_18.json` was initially excluded. Reading the fixture generators established that both the standard and 18-holding views and insights are synthetic test data. The original fixtures were then copied into the disposable tree, without regeneration or changes, and the complete final suite passed. This resolves the fixture-related validation limitation for this implementation run; the original comparison results above are preserved as recorded.

The five skips cover two inapplicable recorded SEC paths, two obsolete scanner APIs and the unavailable optional Streamlit dependency. The 14 pre-existing expected failures concern blue-sky targets, provider dispatch behavior and insider calibration; they were not changed or reclassified. Live-provider throughput, a live browser/server session and investment performance were not evaluated.

### Matched 100-name replay

Three sequential runs per variant compared the committed HEAD scanner (`99ecf40`) against the selectively enhanced scanner, using the same 100 synthetic symbols and provider fixtures, seed, capital, worker count and fake Research confirmation. No Main execution was involved.

| Measurement | Committed PRO | Enhanced PRO |
| --- | --- | --- |
| Scan plus book-selection seconds | 2.0071 / 2.1744 / 2.3094 | 2.0790 / 2.0073 / 1.9666 |
| Median seconds | 2.1744 | 2.0073 |
| Names / could not analyse / rule rejected / reached book | 100 / 37 / 37 / 26 | 100 / 37 / 37 / 26 |
| Funded positions | 3 | 3 |
| Different ticker decisions, allocations, shares, absolute/ranking scores or published levels | 0 | 0 |
| Terminal outcomes and book-stage accounting | Identical across all six runs | Identical across all six runs |

Timing is a small synthetic sample, not evidence of a live speed improvement. The original frozen synthetic scan also passes, and an injected optional `fast_info` failure preserves its business results through PRO's existing `info` fallback.

## What changed

The follow-up changed `stock-analysis/core/SP_ELITE_PRO.py` and `ui/stock-analysis/webapp/app.py`, added `stock-analysis/tests/test_main_diagnostic_ports.py`, and included this comparison/validation report in the local commit. Main and the pre-existing `.impeccable/` directory remain untouched and untracked. No branch change or push was performed.

The disposable tree `<temp>\quanta-main-ports-42237daabdc24167877c18a233799b9f\` contained the test copy, a `baseline/` copy, `replay.py`, focused/full test logs and XML results, the UI test log, baseline/ported replay logs and JSON, `replay-summary.json`, and `runtime/` caches and synthetic ledgers. It was created for validation and deleted after recording the results.

Cleanup verified: all 1,027 temporary files and their parent tree were deleted; no task-owned Python process remained.
