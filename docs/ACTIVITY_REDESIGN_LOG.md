# Activity tab redesign log

Built on a temporary branch in its own worktree (cut from `portfolio-ui-quickfix @ e56c46e`, so it never shared a working tree with the Picks session), then committed to `portfolio-ui-quickfix` as two commits: backend, then front end. No Picks files were touched.

Safety: all data in tests, fixtures and screenshots is synthetic (tickers `SAA`…`SCW`, generated amounts and dates). No trade was placed or previewed. The signed-in server on port 5500 was not restarted or changed; the only thing read from it was `/api/portfolio/diagnostics`, which returns payload shapes without values, to confirm the trade-history field names and ordering. Preview servers ran on scratch ports 5731–5734 and were stopped afterwards.

## 1. Defects: confirmed or not

| # | Defect | Verdict | Cause in code | Fix |
|---|---|---|---|---|
| 1 | "Realized by stock" says "No closing trades" while closing trades exist | **Confirmed** | The tab drew from two sources that were never reconciled. The chart and KPIs read Robinhood's bucketed aggregate (`/activity`); the by-stock list and the table read per-trade records (`/records`). A trade list of `[]` was taken as the complete record. **Trigger, also confirmed:** the server on 5500 started at 14:44, before `e56c46e` (18:49), so its backend still asked `get_pnl_trade_history` for its default one-week span, which returned `[]`. The JS is served fresh on every request, so the page ran the new front end against the old backend. | One source: trade records drive the KPIs, chart, ranking and table. Robinhood's aggregate is used only to cross-check, and any gap shows as named lines ("Robinhood's total for …" and "Not in the trade records"). `/records` now reports coverage per source: counts, first and last time, the span Robinhood says it used, and whether a row cap or the page limit cut the read. A trade history shorter than all time is labelled incomplete. |
| 2 | "% on closed cost" shows a fraction as a percent (0.04% instead of about 4%) | **Confirmed in code; the live value is not verifiable here** | The tile printed Robinhood's `total_rate_of_return` with `pct(v, 2, fraction=false)`, i.e. as already being in percentage points. The recorded schema says the field is a percentage ("10" = 10%), but the reported return was a fraction displayed as a percentage; account amounts have been removed. The field's real unit could only be confirmed by reading account data, which this job did not do. | The figure no longer depends on that field. Return on closed cost = Σ realized ÷ Σ closed cost, where closed cost = proceeds − realized gain for a sale (+ for a buy to close), computed from the same rows as the total. The `one_trade` fixture (4 × $135.25, +$21.04) shows **+4.05%**. The tooltip and footer give the definition. |
| 3 | Ambiguous "Jun 26" axis labels and best/worst dates | **Confirmed** | `bucketLabel` used `{ month: 'short', year: '2-digit' }` for buckets over 8 days. | Month axes read "Jun"; a four-digit year sits under the first label and wherever the year changes. Day and week axes read "Sep 3". Tooltips and screen-reader labels give the full period ("June 2026", "Week of Sep 1 to Sep 7, 2026", "Thu, Sep 3, 2026"). Best and worst trades show ticker and full date. |
| 4 | "All time" does not state its coverage; a capped history is shown as complete | **Confirmed** | No dates were shown. The footer hard-coded "newest 400" whether or not the cap was reached. `_list_pages` stopped silently at 20 pages, and `[:400]` / `[:2000]` truncated silently. | The range is always shown with its dates, e.g. "All (Oct 2, 2024 to Sep 29, 2026)". The footer states the loaded date range and, only when capped, the real cap from the data ("only the newest 400 of 460 filled orders", "the read stopped after 20 pages", or "Robinhood returned its "week" window only"). Caps live in config. |
| 5 | A share total summed across different stocks | **Confirmed** | `summaryLine` added `H.qty` across every row ("6 shares"). | Removed. A test asserts that no share total appears in any view's summary. |
| 6 | Nested scrolling table plus a paginator | **Confirmed** | `.mpa-tw { max-height: 420px; overflow: auto }` plus "Show 30 more". The by-stock list had its own `max-height` scroll as well. | One mechanism: the page scrolls, the header row is sticky, and "Show N more" appears only past `page_size`. A CSS test asserts the table wrapper has no overflow or max-height on desktop. Below 1000px the wrapper may scroll sideways so wide tables don't overflow the page. |
| 7 | Inconsistent decimals | **Confirmed** | `H.qty` allowed 6 decimals; best/worst and the by-stock values used 0 decimals while other figures used 2. | Shares show up to `decimals.shares` (4), trailing zeros trimmed. Currency uses `decimals.currency` (2) everywhere, and percent uses `decimals.percent`. |

## 2. What changed

**Backend**
- `core/portfolio/activity_config.py` (new) is the single source for ranges (labels and windows), views, granularities, `max_buckets`, axis label count, y ticks, `page_size`, ranking ends, lot age buckets, decimals, time zone and history caps. It is served as `records.config`.
- `brokers/robinhood.py`: `_list_pages(report=…)` reports pages read, whether the page limit stopped it with more to come, and the span the tool echoed. `load_history` returns `coverage` per source. The page cap comes from config.
- `portfolio/insights.py`: `records()` returns `coverage` (read, passed, cap, `capped_by` ∈ rows / pages / span / unknown, `complete`, first, last) and `config`. Lots gain `price`, `cost` and `value`.

**Front end**
- `static/mp-activity-model.js` (new, no DOM) holds windows in market time, granularity selection, periods, rows per view, exclusions, KPIs, the ranking, the cross-check, the chart scale, URL state and formatting.
- `static/mp-activity.js` (rewritten) lays out, top to bottom: a sticky control row (range chips, searchable multi-select of stocks, view switch, active-filter chips with Clear all) → five KPI tiles per view → period chart (2/3) beside the by-stock ranking (1/3) → table (2/3) beside its summary (1/3) → one footer. Interactions:
  - hover or focus on a bar: crosshair, tooltip, and the matching table rows highlighted;
  - click a bar to filter to that period (again to clear); brush with pointer, touch, or Shift+arrows then Enter;
  - click a stock in the ranking or the table to filter to it (again to clear);
  - hover a table row to highlight its bar and ranking entry;
  - range, view and stocks persist in the URL (`#holdings/activity/r=…&v=…&s=…`).
  - `ins.act` and `X.loadActivity` are kept for the Overview's realized tile.
- `static/my-portfolio.js`: `setTab` appends a tab's own hash state (`ext.tabHash`), so a refresh keeps the view.
- `static/quanta.css`: the Activity block is replaced and the dead `mpa2-*` rules removed. Tokens only; spacing on the 4/8/12/16/24 scale; 32px rows; tabular figures; green and red only on gain/loss selectors; responsive (two columns from 1000px up, one below).
- `templates/index.html` loads the model script. Asset `?v=` values are file mtimes (`asset_url`), so every edited asset already carries a new version and nothing needed a manual bump.

**Rules applied where the brief left a choice**
- A Robinhood bucket with closing trades but a null figure excludes every trade inside it from every sum. The trades are listed once in the footer, and the period is hatched with one legend entry. A null bucket with **zero** trades is an empty bucket, not a missing figure (the live payload sends null gains for empty buckets too).
- The cross-check against Robinhood's total runs only for a preset range with no stock or period filter. It sums every usable record inside Robinhood's own bucket bounds, not the page's window.
- Range chips filter each view by its event date: closing date, purchase date, or acquisition date for open lots. Lots without a date appear only under All, in a "Date not reported" age bucket.
- When a bar is selected, "chart bucket sum" means the active (selected) buckets. The others stay drawn but dimmed.

## 3. Files touched
Modified: `stock-analysis/core/brokers/robinhood.py`, `stock-analysis/core/portfolio/insights.py`, `ui/stock-analysis/webapp/{package.json, static/mp-activity.js, static/my-portfolio.js, static/quanta.css, templates/index.html, tests/e2e/my_portfolio.spec.ts, tests/fixtures/my_portfolio_insights.json, tests/fixtures/my_portfolio_insights_18.json, tests/tools/portfolio-preview.cjs, tests/tools/portfolio-shots.cjs, tests/unit/my_portfolio_workspace.test.cjs}`.
New: `stock-analysis/core/portfolio/activity_config.py`, `stock-analysis/tests/test_activity_records.py`, `stock-analysis/docs/ACTIVITY_REDESIGN_LOG.md`, `stock-analysis/docs/activity-redesign/*.png`, `ui/stock-analysis/webapp/{static/mp-activity-model.js, tests/e2e/activity.spec.ts, tests/fixtures/activity/*.json, tests/tools/make_activity_fixtures.py, tests/unit/activity_model.test.cjs, tests/unit/activity_view.test.cjs}`.

The two shared insights fixtures were regenerated with the real code; only `records` changed (it gained `config`, `coverage`, and lot price, cost and value).

## 4. Tests

| Suite | Before | After |
|---|---|---|
| `npm test` (JS) | 390 pass | **426 pass** (+16 model, +23 view; 3 old Activity tests replaced by 1) |
| pytest, portfolio files | 211 pass | **219 pass** (+8 in `test_activity_records.py`), also green under `TZ=UTC` |
| pytest, full suite | n/a | 1557 pass, 5 skipped, 19 xfailed, plus 3 `test_signal_ledger` failures caused only by `QUANTA_TRACKING_DISABLED=1` in the shell (they pass without it and fail identically on a clean `HEAD`) |
| Playwright `activity.spec.ts` + `my_portfolio.spec.ts`, 4 projects, scratch port | n/a | **148 pass** |

- **Fixtures** (`make_activity_fixtures.py`): raw Robinhood-shaped payloads go through the real provider parsing and the real `Insights`. Scenarios: `empty`, `one_trade`, `many` (1,000 trades, 75 stocks), `all_losses`, `all_gains`, `capped` (460 orders over the 400 cap; trade pages over the read limit), `missing_lot_dates`, `no_figure_bucket`, and `short_window` (a defect-1 regression).
- **Invariants:** for every scenario × view × range × {none, one stock, two stocks, a selected bar}, the KPI total equals the chart buckets, the ranking and the table (300+ combinations), and the same holds as rendered.
- **JS tests** also cover:
  - percent formatting, unambiguous date labels, granularity versus config, and URL round-trips;
  - filter chips, the view switch driving every section, one scroll mechanism, column limits per view;
  - empty states, hidden amounts, and a server that predates the redesign.
- **Guards:**
  - no ticker, amount, ISO date, written date or year literal in `mp-activity.js` or `mp-activity-model.js`, comments included (`GET`, `POST` and `UTC` are allowlisted as protocol words);
  - the Activity CSS uses no literal colours, stays on the spacing scale, and uses red and green only on gain/loss selectors;
  - a pytest checks that every fixture carries the current config.
- **Playwright** covers hover tooltip and row highlight, click to filter and clear, pointer brush, keyboard brush, the searchable stock filter, persistence across a reload, empty states, axe WCAG 2.1 AA in each view, and visual checks in light and dark at 1280 and 1440 (no overflow, chart and ranking equal height, no clipped tiles) plus a single column at 960.

## 5. Screenshots (synthetic data only), `docs/activity-redesign/`
Every screenshot renders the synthetic activity fixtures (tickers `SAA`…, generated amounts and dates), with the page clock pinned to the fixture's date. The preview tool's older built-in trade list was not used for any of them.
- Before (the code at `e56c46e`, `many` fixture, Month): `before-activity-{1280,1440}-{dark,light}.png`. It shows a share total summed across stocks, the nested scrolling table plus "Show 9 more", dates without a year, a by-stock list with its own scroll, and 15 empty days counted as "no figure". Its "0.00% on closed cost" comes from the fixture's placeholder return field and only stands in for the live 0.04% bug.
- After, the same data and range: `after-month-activity-{1280,1440}-{dark,light}.png`.
- After, 1,000 trades, All: `after-activity-{1280,1440}-{dark,light}.png`.
- After, scenarios: `after-short_window-…`, `after-no_figure_bucket-…`, `after-capped-…`, `after-empty-activity-1440-dark.png`.

## 6. Unresolved or unverifiable
- **Not verified on the real account.** The running server on 5500 has the old backend and needs a QUANTA restart to serve `coverage` and `config`, which drops its in-memory Robinhood sign-in. Until then the tab shows one line asking for a restart, with no figures invented.
- **The unit of `total_rate_of_return`** (percent per the schema; apparently a fraction live) is not confirmed. The page no longer uses it.
- **Days held:** Robinhood's trade records carry no open date, so the column is omitted for them. It appears only on the FIFO fallback, which does know the dates.
- **Open lots table:** the brief asks for at most 6 columns and a tax-term column when any lot is long-term. Both can't hold, so the term sits in the Acquired column ("Acquired · term").
- **The ranking card** stretches to the chart's height (equal height was asked for). With very few stocks (3 in the lots screenshot) it is more than 15% empty. The two requirements conflict there; nothing was padded in.
- **Bucket time zones (resolved 2026-10-02):** the live YTD window starts at `05:00Z`, so Robinhood sends bucket bounds in UTC. `load_realized_series` used to cut them to 19 characters, which dropped the zone, so bounds read as New York time 4 to 5 hours late. The full RFC 3339 string is now kept, and the page converts it to New York time.
- **Granularity override:** limited to granularities that fit `max_buckets`; the others show disabled, with a tooltip.
- **Standalone repo:** `quanta-stock-analysis` is not synced; the change exists only on this branch of the monorepo.

## 7. Follow-up (2026-10-02 evening)
- The "Showing n of m / Table total / Stocks" summary now sits in the table's own header line. The freed lower-right space holds one card built from the same rows:
  - Closing trades: gross profit against gross loss, net and profit factor (gross profit ÷ gross loss, omitted when there is no loss).
  - Open lots: unrealized gains against losses.
  - Purchases: purchase sizes (smallest, median, average, largest).

  Gross gain + gross loss = the total, checked in the reconciliation test for every combination.
- The ranking labels its columns. The labels come from config (`views[].value` and `views[].count`, e.g. "Trades").
- Sparse rows: when the finest fitting granularity averages fewer rows per bucket than `sparse.rows_per_bucket` (0.5), the chart opens on `sparse.use` (month), if that fits. An override still wins.
- Week buckets read "Week of <date>" on the axis (the prefix is hidden below 640px) and in tooltips.
- The table's "Buy → sell" column is now "Cost → sell", with a header tooltip. The figure is the closed lots' average cost implied by Robinhood's realized gain (sale less gain), not one order's price.
- When the cross-check runs and matches, the ranking says "Matches Robinhood's total for …". A difference shows the two named lines instead, and an unreadable Robinhood total gets a footer note.

## Continuation verification (2026-10-02)
- Zoned bucket bounds from `bdfc4de` are preserved; Overview consumes gains only and Review does not consume these bounds.
- Added a rendered Overview/Review regression with UTC and explicit-offset winter/summer bounds; 433 JS tests passed.
- Portfolio review and activity-record tests: 60 passed, included in the broader 337-test Python verification.
- Proposed only: raise the configured fill cap to 2,000. The row cap is downstream of paging; keep the page limit and coverage warnings.
- Account-derived examples removed from this log. Remaining numeric examples are synthetic tests or configuration.
- Scratch account files and baseline worktree are absent; no scratch server remains. The production server was not restarted.
