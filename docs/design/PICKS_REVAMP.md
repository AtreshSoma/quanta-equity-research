# Picks revamp log

Branch `portfolio-ui-quickfix`, local only. Stage 1 (calls, scan selection, audit and logic) closed 2026-10-06.
Commits: 8d16411 (wording, escaping), 3514a43 (scan selection), 45a993c (outcomes, fix), plus the stage-close commit.

## Stage 1: issues

| # | Issue | Root cause | Fix | Status | Test |
|---|---|---|---|---|---|
| S1-01 | "funded", "on watch" and "not funded" still appeared in the legacy Picks path, the Overview brief and the Guide | The guard read only the pk* render path. Older saved results (no `stock_picks`) render through `renderPicks`/`pickCard`, which the guard never covered | Reworded every shown string. The guard now renders each fixture through the legacy path and reads the whole template | Done | `picks_wording_guard.test.cjs` (legacy + template test) |
| S1-02 | Engine log lines said "none funded" | Messages were written before the calls model | Reworded to Buy or Accumulate | Done | AST guard |
| S1-03 | The server's currency symbol reached HTML raw | `fmtPrice`/`fmtMoney`/`fmtCurrency`/`fmtCur` interpolate `sym` without escaping, and their output is placed unescaped in the legacy path and Pro cards | `safeSym` strips markup characters inside every formatter | Done | `picks_escape.test.cjs` |
| S1-04 | Server values used as class or data tokens | `data-pk-outcome`, valuation `m.key` and `signalKey()` let `"` and `>` through | `cssToken`; `signalKey` keeps `[a-z0-9_-]` only | Done | `picks_escape.test.cjs` |
| S1-05 | Raw server numbers (analyst split, insiders, exposure, scanned, em score, error counts) | Interpolated without `Number()` or escaping | Coerce to numbers or escape | Done | `picks_escape.test.cjs` |
| S1-06 | News and filing links accepted any URL scheme | Escaped, but the scheme was never checked | `safeUrl`: http(s) only | Done | `picks_escape.test.cjs` (javascript: probe) |
| S1-07 | A ticker containing `"` broke card selection; the job id went unencoded into the stream URL | Raw value in a CSS selector and in the URL | Match by `dataset`; `encodeURIComponent` | Done | escaping test renders poisoned tickers |
| S1-08 | Momentum Leaders showed "Analysing names: 79 of 250" | 79 was the live counter. 250 is the Standard cap (Momentum is 150): an unknown `scan_type` silently became Standard, so a server started before 3da1778 ran Standard | Unknown scan types return 400 with "Restart the QUANTA server" | Done | `test_picks_scan_selection.py` |
| S1-09 | The progress panel showed no source size, kept size, method or fallback until the scan ended | `selecting` was reported once as (0, 0); the selection record reached the page only in the final coverage line | The engine reports (kept, source) after selection. Every stage carries `selection`, and one `selectionText` feeds the progress panel, the coverage line and Method | Done | `coverage_line.test.cjs`, `test_picks_scan_selection.py` |
| S1-10 | The pre-filter download cut the universe at an alphabetical 1000 | `tickers[:1000]` predates `PREFILTER_MAX_TICKERS`, which nothing read | Even sample above `UNIVERSE.PREFILTER_MAX_TICKERS`, recorded as `prefilter_sampled` (latent at 806 names) | Done | `test_picks_scan_selection.py` |
| S1-11 | The page could not say how many names passed the floors but were not analysed | The count `len(promising)` existed only in a log line | `passed_floors` and `rank_by` in `universe_selection`; the selection line names them | Done | same |
| S1-12 | A fallback to the built-in S&P 500 list was invisible on style and Large-Cap scans | Only index-file scans recorded `index_source` | `market_data.last_sp500_source` is recorded for those pools; the line says "fallback: the built-in index list" | Done | `coverage_line.test.cjs` |
| S1-13 | The progress bar read "N of N" even when throttled names were never reached | The final call reported `(total, total)` | Reports total minus the unreached names | Done | `test_picks_scan_selection.py::test_a_throttled_scan_never_reads_n_of_n...` (fails on the old line) |
| S1-14 | Menu text understated the caps ("S&P 500 names" for a 150-name scan) and used em dashes; Guide chips listed "Full India" and omitted Momentum and the India presets | Hand-written text drifted from `scan_limits` | Options state the engine caps, using ": " in place of em dashes; Guide chips match the menu | Done | `guide.test.cjs` (caps read from source), `test_universe_size_contract.py` |
| S1-15 | Full's coverage line said "in index-list order" | Default wording did not match the `full` order (index names first) | Reworded | Done | `coverage_line.test.cjs` |
| S1-16 | Method had no line for how the universe was chosen | — | The Universe coverage line under the list is built from `selectionText` (a step in the list repeated it, so stage 3 removed the step) | Done | `picks_table.test.cjs` (coverage line in Method) |
| S1-17 | Three floor-rejected names had a count but no named outcome | `scan_accounting` kept tickers only for could-not-analyse | `rule_rejected.tickers` per reason | Done | `test_picks_outcomes.py`; golden snapshot updated (names only) |
| S1-18 | A Watchlist call from a Research R:R downgrade had no fix | `row.fix` was read only from the event check | Falls back to the binding remedy ("Research R:R 1.41 needs 1.50: reached at a spot at or below $110.69 ...") | Done | `test_picks_outcomes.py` |
| S1-19 | The remedy and the target basis were quoted in "$" for NSE/BSE names | `rr_remedy` and the basis string had "$" hard-coded | `_listing_currency(ticker)` passed to `publish_levels` | Done | `test_picks_outcomes.py` |

## Scan selection, per scan (stage 1 trace)

Pre-filter floors: US $10 to $5,000, 750k shares, $20M a day. India ₹50 to ₹1,00,000, 1 lakh shares, ₹5 crore a day (NSE/BSE names use the India floors). Ranking: 20-day average daily traded value, ties by symbol. Style screens, market-cap floors and user filters run after the cap, inside `analyze_stock`.

| Scan | Source (size) | Cap | Analysed | Passed the filter, not analysed | Timeout fallback |
|---|---|---|---|---|---|
| Quick | S&P 500 ∪ NDX ∪ Dow (~520) | 100 | 100 | `passed_floors − 100`, now published | Even spread (`method=even_spread`, cause shown during progress) |
| Standard / Comprehensive | full assembled universe (~806) | 250 / 400 | 250 / 400 | `passed_floors − cap` | same |
| Full | ~806 | none | all | 0 | none (no pre-filter) |
| Growth / Value | S&P 500 (live or built-in) 503 | 150 | 150, then style screen | ~353 never style-screened | same |
| Momentum (US) | S&P 500 503 | 150 | uptrend screen on all past the floors, then the 150 most liquid | `passed_screen − 150`, published | cap-then-screen, named in the cause |
| Dividend Aristocrats | Aristocrats 69 | 100 | 69 | 0 | none |
| ETF Sleeve | registry 54 | 80 | 54 | 0 | none |
| Large / Mid / Small-Cap | S&P 500+Dow / S&P 400 / S&P 600 | 120 / 100 / 80 | ≤ cap | rest of the index | same (Small-Cap uses its own floors) |
| Pre-Momentum | curated 48 | 48 | 48 | 0 | none |
| India Quick / Standard / Sectoral / Comprehensive | Nifty lists (50 / ~59 / ~65 / 500) | none | all | 0 | none |
| India Growth / Value / Dividend / Momentum | Nifty 500 | 150 | 150, then style screen | ~350 | same |
| India Large / Mid / Small | Nifty 100 / Midcap 150 / Smallcap 250 | 100 each | 100 | 0 / ~50 / ~150 | same |
| Custom | your list | 500 max | all | 0 | none |

The history-prefetch timeout (180 s) does not change the selection: names fall back to one-by-one fetches.

## Escaping audit (sections A to E) after stage 1
- A currency symbol, B class and data tokens, D link schemes: closed.
- C raw numbers: closed, including the legacy `max_positions` line (fixed after stage 1).
- E: the CSS selector and the job URL are closed. Still open: `pkEntryText` puts a `\u0000` separator into the entry-zone text. Both callers strip or split it, but a new caller would print it. This is a robustness issue, not an injection.

## Owner decisions (confirmed 2026-10-06, applied)
- (a) Momentum Leaders (US) screens every S&P 500 name past the floors on price history (1-year download), then keeps the 150 most liquid of those that pass. If the download times out, it caps first and says the screen runs after the cap. If no name passes, the scan is empty, not a fallback. Growth and Value stay cap-then-screen, and their menu text says so. India Momentum is unchanged (cap-then-screen). Tests: `test_picks_scan_selection.py`, `coverage_line.test.cjs`.
- (b) Small-Cap has its own floors in `UNIVERSE.SCAN_FLOORS['small_cap']` ($5, 250k shares, $5M a day; the $300M market-cap floor stays), applied in the pre-filter and in `analyze_stock`; Method states them. Test: `test_small_cap_has_its_own_floors...`.
- (c) ETF cap left as is (54 funds, cap 80).

## Harness re-run (stage 1)
- `api_matrix.py`: 78/78. Added Momentum and an unknown scan (400).
- `quant_checks.py`: 231/231 (scores, risk grade, sizing at 4 capitals × 3 calls, trigger equation).
- Independent recompute of 5 sized Buy or Accumulate rows: PARTLY CLOSED in stage 3 (see "Live Quick scan"): the live scan made 1 sized call, so 3 real rows were recomputed and the 5-sized-row target stays OPEN.
- Independent recompute on real rows so far: QCAL R:R 1.41 net of 0.11% cost at the top of the zone; spot trigger $110.69, target trigger $118.41, all exact. Sizing is covered by quant_checks across 12 names. The synthetic scans in the current build make no Buy or Accumulate call, so no sized row exists to recompute.
- `browser_audit.cjs` (scratch copy, which now opens the folded setup): 19 screens, axe 0, overflow 0, XSS 0 executed. Its only page errors are external fonts the harness blocks.
- Performance (once per stage), synthetic worker vs baseline: us-default 0.90 → 0.44 s; the rest within ±0.05 s. Frontend render for 250 rows: 49 ms vs 428 ms. Nothing over 2x.
- The harnesses in docs/audit-picks read a deleted temp copy (`state.json`). Run scratch copies with their own `state.json`, and never edit the evidence.

## Suites at stage-1 close
Python 1,812 passed / 5 skipped / 14 xfailed (local and TZ=UTC); JS 578/578. Start of stage: Python 1,801, JS 567.

## Stage 1 follow-ups (owner, 2026-10-06)
- f30b394: S1-13 now has a direct test (a fake provider leaves names unreached); legacy `max_positions` escaped.
- 5932ed7: decisions (a) and (b) applied, (c) unchanged. e0a97a2: untracked `SP_ELITE_PRO_Main.py`, which `git add -A` had swept into 5932ed7 (no history rewrite); it remains on disk.

## Stage 2: shell, Overview, Analysis (81ec69c + gates spec)

| # | Issue | Root cause | Fix | Status | Test |
|---|---|---|---|---|---|
| S2-01 | The report was a rounded card holding bordered panels (card in card), inset 16 px with a ~30 px gap under the setup bar | `.results-body` padding and the workspace gap applied to the report too | Report sits on the page ground with a framed header and tab rail; the bar-to-report gap is 12 px | Done | `picks_gates.spec.ts` (gap ≤ 16) |
| S2-02 | Calls were coloured (blue Buy, amber Watchlist) | The tone came from a colour map | Buy is solid ink, Accumulate ink outline, Watchlist dashed muted | Done | gates + visual |
| S2-03 | Green and red used for non-gain meanings (Bull regime, Low risk, analyst buy/sell, insider net, pros/cons bullets, done ticks) | Tone classes reused bull/bear | Neutral ink or blue; amber only where it warns (moderate or high risk, caution, pending) | Done | visual (both themes) |
| S2-04 | Summary and key-strip cells were 58% filled beside taller neighbours | A figure-only cell sat centred in a cell sized by a three-line neighbour | Every cell is label / figure / sub-line (sub-lines from the payload: last close, net of costs) | Done | gates (≥ 85%) |
| S2-05 | Levels took three lines per row | Entry sat on its own line | Stop · entry · target on one line (CSS grid areas) | Done | visual |
| S2-06 | Coloured 3 px edge bars on sized rows and the empty state | A style left over from the old design | Removed; the empty state has a full 1 px warning line | Done | detector |
| S2-07 | "Holding / Pending" looked like buttons | Pill border and fill | Dot plus word | Done | visual |
| S2-08 | Insiders reading overflowed into the value at 1440 and was clipped at 1280; Technicals "from the high" wrapped | A long reading column squeezed the bar column, and three columns at 1280 are too narrow | Insiders shares the analyst row shape (bar, net, counts); readings never wrap; three columns from 1366 px (wider middle), two below | Done | gates (`wrapped`, `clipped`) |
| S2-09 | Phone: tab rail and stock rail scrolled sideways | `overflow-x: auto` rails | Both wrap | Done | gates (no scroll boxes) |
| S2-10 | Progress showed "79 of 150" twice and a green done tick | The status line and the current stage both print the count | Clock on the current stage; status line stays for screen readers only; neutral tick; selection on its own line | Done | `scan_progress.test.cjs`, `coverage_line.test.cjs` |
| S2-11 | Measured design gates were never written | — | `tests/e2e/picks_gates.spec.ts`: 1280/1440/390 × both themes × Overview/Analysis; fill, spread, gap, overflow, scroll boxes, wrap, clip, axe | Done | 48/48 (desktop + mobile projects) |

Detector (Impeccable) on quanta.css: one Picks finding, a false positive (the sort-arrow triangle). The other 38 are on other pages, outside scope.
Accepted: Analysis keeps a sticky list column beside the longer card. It stays visible while you scroll, so it is not a blank column.

## Suites at stage-2 close
- Python 1,818 passed / 5 skipped / 14 xfailed (stage start: 1,818). JS 579/579 (start: 579).
- Playwright on a scratch server: smoke plus picks_gates, 80/80, run without PB-001.
- PB-001 runs a live custom scan in every project. The first run let it start one live scan on the scratch server (not planned, stopped by killing the server), and the parallel projects then collided ("Another scan is running"). The spec is unchanged. Run it only on purpose, with `--workers=1 --project=desktop-light`.

## Stage 3: Momentum alerts, Risk and execution, Method, final verification (5c825e0 and the follow-up commit)

| # | Issue | Root cause | Fix | Status | Test |
|---|---|---|---|---|---|
| S3-01 | Chase alerts, additional risk checks, Pro cards, the gap-filled list and the method list were unframed or looked like cards in cards, with 24 px gaps | They kept the pre-redesign markup and styles, which the stage 2 CSS never reached | One CSS panel look scoped to the report: 1 px line, header strip, body on the panel padding; tables use the Overview rhythm. Markup is unchanged, so the Pro cards and their tests are untouched | Done | `picks_gates.spec.ts` (all five tabs) |
| S3-02 | Final decisions showed as green and amber pills | `signal-pill` used the old tone colours | `pkActionBadge`: the Overview call badges (Buy solid, Accumulate outline, Watchlist dashed) | Done | `picks_wording_guard`, gates |
| S3-03 | Book-risk figures wrapped inside 161 px cells ("10.1% of / capital"); nested cards; the mix row was centred | A fixed `auto-fit` grid of bordered boxes in a column-direction flex cell | A three-cell strip on hairlines (one column under 900 px), figures never wrap, neutral chips, rows on hairlines | Done | gates (`wrapped`, `clipped`) |
| S3-04 | Notes and banners had a 3 to 4 px coloured left edge | Legacy `border-left` | 1 px frame all round | Done | Impeccable detector (one false positive left, the sort arrow) |
| S3-05 | Method printed the Universe line twice (list step and the coverage line) | The stage 1 step repeated the coverage line | The step is removed; the coverage line under the list is the one place | Done | `picks_table.test.cjs` |
| S3-06 | On a phone the Overview Levels row overflowed once a price had four figures (found on the live payload, not the synthetic one) | Three `nowrap` labels (Stop, Entry, Target + %) needed more than the 364 px row | Phone layout: stop and target share a line, entry takes its own. The committed fixture now holds a four-figure price (4,502.51), and it fails on the old CSS | Done | `picks_gates.spec.ts` at 390 px |
| S3-07 | Chase table on a phone scrolled sideways | Seven columns | Phones drop Company and Bar date; no scroll box | Done | gates |

Left as allowed: wide data tables (additional risk checks, stage tables) scroll inside a focusable labelled region on phones only. The gates spec states this.

### Live Quick scan (once, 2026-10-07, US, capital $25,000, Balanced, 5 positions)
- Result: success, BULL regime (VIX 15.8); 100 scanned. Outcomes reconcile: 100 = 5 could not be analysed + 91 rejected by a rule + 4 reached the book + 0 not analysed, and all 96 non-book names are listed by ticker.
- Event check: 100 of 100 names checked, 0 unavailable, 1 flagged (the contact was read from the user environment variable, as the earlier SEC finding required).
- Selection line: top 100 of 518 names, ranked by 20-day average daily traded value; 475 passed the floors, 375 of them not analysed; price download 16 s.
- Calls: 1 Accumulate (AMZN, sized: 4 shares, $1,027.20) and 2 Watchlist (GOOG and CSCO, Research reward/risk below 1.50, each with the prices that would clear it).
- Timing (once): 123.6 s end to end against the earlier 98 s baseline, so 1.26x, under the 2x flag. Stage starts after POST: universe 24.1 s, analysis 42.2 s, book rules 102.5 s, Research check 111.5 s, complete 123.6 s. The 24 s before "selecting" is the 518-name list read and server work after the POST, not a stage; it was not measured in the baseline, so no stage is compared in isolation.
- Rendered through the gates rig at 1440 and 390, both themes: Overview and Analysis pass every gate, axe 0.

### Independent recompute on the live rows (`docs/audit-picks/live_recompute.py`, 15 checks, 0 failed)
- It uses only the published fields and the config fixture; it does not import QUANTA. A corrupted value makes it fail (4 failures on a mutated copy).
- AMZN (the one sized row): net R:R 2.1386 vs 2.14; scanner size 8 shares, re-sized on the tighter Research stop and capped at 10% of capital to 9 shares, $2,311.20; then half-sized because the Research check made a scanner Buy an Accumulate (`DEEP_ACCUMULATE_SCALE` 0.5) to 4 shares, $1,027.20 (4.11% of capital; 0.133% at risk at the stop). All match.
- GOOG and CSCO: R:R 1.3933 and 0.0; target and entry triggers match to the cent. CSCO's target trigger is 129.99 recomputed against 130.00 published (a 0.006 gap from the unrounded inputs).
- **Not met:** 5 sized rows. The market produced one sized call, so the recompute covers all 3 real rows (1 sized, 2 Watchlist). The 5-sized-row target stays OPEN for a scan that produces them; it is not claimed.

## Suites at the end of stage 3 (final)
- Python 1,818 passed / 5 skipped / 14 xfailed, both local and with TZ=UTC. JS 579/579.
- Audit harnesses: `api_matrix.py` 78/78, `quant_checks.py` 231/231 (scratch copies).
- Playwright on a scratch server: `picks_gates` 120/120 (5 tabs x 3 widths x 2 themes x 2 projects), smoke 32/32 without PB-001 (it starts a live scan per project).
- Axe 0 on every gated view. Impeccable detector: one finding in the Picks block, a false positive on the sort arrow.

## Still open (owner or later)
- Recompute of 5 sized rows: needs a scan with 5 sized calls (the 2026-10-07 live scan made 1).
- Style scans still screen only the 150 most liquid (Growth, Value); ETF cap is alphabetical (latent).
- `pkEntryText` still carries a ` ` separator (robustness only).
- Chase table top-10 and sorting (an earlier owner request) is not built.
- India Momentum is still cap-then-screen.
