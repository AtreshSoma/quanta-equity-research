# Research tab redesign

## Sixth pass (2026-10-09): minimal search bar, Analyze, signals fit

- **Search bar.** No frame around the form: one 44 px field holding the search, the "/" shortcut and a
  Fast preview switch; the **Analyze** button beside it (was "Build tear sheet"); the per-index
  examples and the routing hint as quiet lines under it. Copy: placeholder "Search stocks and ETFs",
  loading "Analyzing NVDA...", hint "US tickers as listed. For India, add .NS (NSE) or .BO (BSE)."
  The Dow examples are JPM, WMT and KO (V was a one-letter symbol that read as a typo).
- **Analyze button (revised).** The owner replaced the liquid-glass look with their button-2 component,
  ported to CSS: an indigo gradient with a 2px rim and a radial glow that follows the pointer. The light
  gradient starts at #5b67e8, deeper than the component's #9ba3fd, so the white label keeps 4.5:1.
- **Examples carry company names** (`[ticker, company]` in `UI.RESEARCH_SUGGESTIONS`), shown in the search
  list as "AAPL Apple"; a market without configured examples shows no empty heading.
- **Superseded: liquid glass.** The pasted LiquidButton (React, shadcn, Tailwind) was ported to plain CSS: the
  same inset-shadow glass rim (light and dark sets), an azure tint, the `#container-glass` SVG
  refraction filter on a layer behind the label (Chromium; elsewhere the tint alone), hover scale,
  reduced motion respected. The app has no React toolchain, so the component was not installed as is.
  On phones the refraction layer is off (it smeared across a full-width button).
- **Technical signals fit.** The rows share one grid (subgrid), so the reading words form one column as
  wide as the longest; the value column may wrap inside its cell. A golden or death cross is a note
  under the Trend label, not part of the pill. Checked from 320 to 1920 px: no text leaves the card.

## Fifth pass (2026-10-09): half-size chart, Signals card, search

Owner request: the chart was too large; make it half size and use the room for other important
information; a better search for US and Indian stocks with 2 to 3 examples per index; a shorter,
colour-coded Technicals.

- **At-a-glance grid** (right of the Decision column): live chart beside **Technical signals**, then
  the session quote beside the multi-factor scores, then Performance vs the benchmark across the row.
  The chart's plot height follows the Signals card's content (160 to 320 px), so the pair ends level.
  On Decision detail the reading column is now longer than the Decision column, so no blank band.
- **Technical signals** replaces the Technical setup table: trend, MACD, RSI (with its 0 to 100 gauge),
  trend strength, volume, support, resistance, 52-week position and candles, each with one coloured
  word: green bullish, red bearish, amber near a level, gray neutral. A bar at the top tallies the
  directional rows. Bands come from config: `REGIME.RSI_OVERBOUGHT` and the new `UI.TECH_*` values.
- **Technicals tab** keeps only the candlestick detail; the session quote, signals and performance moved
  to the glance grid. Performance vs the benchmark gains a green/red bar in each Difference cell.
- **Search.** `GET /api/symbol-search?q=` (Yahoo search, US and Indian exchanges, stocks and ETFs).
  The field is a combobox: empty, it lists recent searches and examples per index for both markets
  (the selected market first); typing lists matching listings with exchange and market. Arrow keys,
  Enter, Escape, and "/" to focus. Under the field, the selected market's examples per index. The
  examples live in `UI.RESEARCH_SUGGESTIONS` (engine/thresholds.py).
- **Tests changed (intended).** `tests/dom_redesign.test.cjs`: Technical setup, the session quote and the
  scores are checked in the glance grid, not in Technicals or Decision detail. New:
  `tests/unit/research_signals.test.cjs`. `tests/fixtures/thresholds.json` regenerated.

## Fourth pass (2026-10-09): Decision column and live price chart

Owner request: the Decision as a left-aligned sticky column, an interactive live chart of the stock
with accurate axes on the right, an interactive chart on Overview, no blank space, and visuals where
they carry meaning.

- **Layout (1100 px and wider).** Left: the Decision column (360 px, sticky). Right: the live chart,
  then the section bar and panels. On the Decision detail tab the chart's height is set so both
  columns end level (`fitChartToRail`, 280 to 520 px); longer tabs scroll beside the sticky column.
  Under 1100 px the Decision becomes the full-width band and the chart follows it.
- **Live chart (`static/live-chart.js`, `GET /api/chart?symbol=&range=`).** Ranges 1D, 5D, 1M, 6M,
  YTD, 1Y, 5Y and Max. 1D uses 1-minute bars with pre- and post-market (shaded), a time axis spanning
  the exchange's whole trading day in its own time zone, and a dashed previous-close line. Price ticks
  are round steps that cover every plotted value. Crosshair with time, price, change and volume; arrow
  keys step through bars. 1D refreshes every minute while the session is open and the page is visible.
  The server caches 1D for 60 seconds and longer ranges for up to an hour, and falls back to the last
  bars when the provider rate-limits.
- **Research specifics.** The chart draws the Decision's stop (red) and target (gold); levels far
  outside the range are listed under the chart. The static price chart left Technicals, which now
  pairs the session quote with Technical setup.
- **Overview.** A price chart panel under the market ribbon, defaulting to the ribbon's first symbol;
  the ribbon's symbols are quick picks and any ticker can be typed. The picked symbol is remembered
  per market. "Open tear sheet" opens Research for a stock symbol.
- **Visuals in the Decision column.** A risk/reward ladder (stop to entry in red, entry to target in
  gold, today's price marked), a conviction meter (amber when low) and a risk-grade badge (amber for
  D and F).
- **Residuals closed from the third pass.** Technicals slack (chart removed from that panel); uneven
  "Supporting factors vs key risks" lists stack; all-capitals engine risk echoes are dropped when the
  risk-grade line states them; phone section tabs are a 3 x 2 grid; chart axis labels thin to the
  chart's real width.
- **Tests changed (intended).** `tests/dom_redesign.test.cjs`: the price chart is checked in `#ts-chart`
  instead of the Technicals chapter. New: `tests/unit/live_chart.test.cjs` (axis maths).

## Third pass (2026-10-08): Decision band, sticky section bar, full-width panels

Branch `research-rebuild-2`. Engine output is untouched: the Decision, its blockers,
levels, sizing, scores, evidence status and the "Model not yet validated" flag render
exactly as the backend returns them.

### Problems found (1440 x 900, dark, Fast preview, Decision detail, a name not held)

1. **Blank bands.** The reading column ended about 200 px above the Decision rail; a short
   tab (three panels) left an empty band under "Plain-language guidance".
2. **Three left edges.** The topbar padded the full viewport, `.app-main` was capped at
   `--page-max` and centred, and `.footer-inner` had its own cap, so header, page and footer
   started at 32, 112 and 80 px at 1920.
3. **The rail competed with the page.** It was the tallest element, and a facts strip above the
   tabs (conviction, risk grade, track record, data, checks) partly repeated it.
4. **The same fact several times.** The Fast preview caveat sat in a banner, in "Also holding it
   back" and as a "Why not Buy?" row; the main blocker sat in the rail and again in "Why not Buy?".
5. **A placeholder row.** "Why not Buy?" named each rule by its engine code (`HOLD_NOT_OWNED`
   became "Hold not owned") and replaced the main blocker's finding with the sentence "Main
   blocker: its finding and remedy are shown with the call." Source: `whyNotBuyHtml` in
   `static/app.js`, fed by `decision.blockers`, which is the same list the rail already showed.
6. **Holder plan for non-holders.** "Existing holder profit plan" was a full tile for a reader who
   holds nothing.
7. **Fast preview by default.** The template shipped `#an-fast-mode` checked, so the first sheet
   opened on a "preliminary" banner and "skipped" warnings.
8. **Mixed chrome.** Tracked capitals in the session quote and tables, four chip styles, and a
   wide Composite row over a 3 x 2 grid of bars of different widths.

### Structures explored

Captured at 1440 x 900 on the live DOM (ERAS, Fast preview):

- **A. Decision band.** Identity, call, why and levels as one full-width band; sticky section bar;
  full-width panels. Call, reasons, main blocker and every level are on the first screen, the tabs
  start near y 590 and the first panel row is visible.
- **B. Desk with a sticky rail** (the second pass, refined). The levels fell below the fold at
  1440 x 900, and a short panel left a blank band beside the rail whatever the rail's height.

**A was chosen.** B lost because its two columns have independent heights: no rail length fits
both a short tab and a long one, so a blank band beside one of them is structural, and the rail's
height pushes the levels off the first screen.

### What the page is now

```
Research  (title, request bar: ticker, Fast preview off by default, Build, dynamic Try chips)
Decision band #ts-snapshot
  identity row   ticker · company · sector · market cap · chips ............ price, day move
  row 1          the call (label, flag, meaning)  | horizon, stance, new money,
                                                    holder action, conviction, risk grade (3 x 2)
  row 2          why: reasons, main blocker + remedy,   | key levels (2 or 3 across,
                 the other rules + remedies, Fast         chosen to end with the why column)
                 preview rule with "Load full analysis"
Section bar (sticky)   [ticker  CALL  price, once the band has scrolled away]  six tabs
Panels (full width)    tiles pair only within 15 percent of height; otherwise full rows
```

### What moved where

| Item | Before | After |
| --- | --- | --- |
| Call, facts, reasons, levels | Sticky right rail | Full-width Decision band |
| Blockers and remedies | Rail and again in "Why not Buy?" | Band only, each once, in engine order |
| Fast preview caveat and "Load full analysis" | Banner + rail + rules row | The Fast preview rule in the band (a one-line note only when the engine sends no such rule) |
| "Why not Buy?" rules tile | Decision detail | Removed for engine payloads (the band holds the same rules); kept for older payloads without `decision.blockers` |
| Conviction, risk grade | Facts strip | Band facts |
| Track record, checks | Facts strip | Removed at the owner's request; the model flag beside the call links to the evidence page, and the data checks live in Evidence & data |
| Data freshness | Facts strip | Price tooltip when fresh; an amber fact when stale |
| Existing holder plan | Full tile | Full tile when the name is held, the call funds new money or sizing is withheld; otherwise a closed row ("you hold none") |
| Multi-factor scores | Composite row over a 3 x 2 grid | Seven equal cells on one ruled strip; label, bar, value rows on a phone |
| Session quote | Full-width tile above the chart | Beside the chart, stacked over Technical setup |
| Section bar | Names only | Names, plus ticker, call and price once the band has scrolled away |

### Shell

`.topbar`, `.app-main` and `.app-footer` share `--shell-pad: max(--page-pad, (100% - --page-max) / 2)`,
so every page has one left edge and one content width from header to footer.

### Page height (default tab) and first screen

| Payload | 1440 before | 1440 after | 390 before | 390 after |
| --- | --- | --- | --- | --- |
| ERAS, Fast preview | 1292 px | 1083 px | 2652 px | 2097 px |
| ERAS, Full analysis | 1168 px | 1003 px | 2354 px | 1975 px |
| NVDA, held (derived fixture) | 1187 px | 1191 px | 2374 px | 2319 px |

First screen at 1440 x 900: before, the rail showed the call and reasons with the levels below the fold.
After, the band shows the call, its six facts, reasons, every blocker with its remedy and all key
levels, then the section bar and the first panel row.

The held case is a fixture derived from a live NVDA payload with `has_existing_position` and
`decision.holdings_known` set to true; the live engine needs a brokerage snapshot to produce one.

### Tests changed for intended changes

- `tests/dom_render.test.cjs`: "Session quote" is sentence case.
- `tests/ui_render.test.cjs`: the data-quality label reads "NOT CONFIRMED: DATA QUALITY ISSUE" (no em dash).
- `tests/e2e/smoke.spec.ts` DD-001: asserts the call painted and over 1000 visible characters; the sheet no longer repeats facts, so the old 2000-character proxy no longer held.

### Known residuals

- Technicals at 1280 px: the chart card is about 74 px taller than its content, because the chart
  scales with width while the quote and setup stack does not. Flush at 1440.
- ERAS Fast preview band: the levels column ends about 30 px above the why column.


Scope: the Research tear sheet (`#view-analyze`, rendered by `renderAnalysis` in
`ui/stock-analysis/webapp/static/app.js`, styled in `static/quanta.css`).
Engine output is untouched: the Decision label, model stance, new-money and
holder wording, blockers, levels, sizing, the "Model not yet validated" flag and
withheld sizing render exactly as the backend returns them.

## Problems found

1. **The call was not the first thing read.** The Decision hero showed the label,
   but the reason (the main blocker) and the levels sat in a sticky side rail,
   and the full Decision chapter was the ninth of nine stacked chapters.
2. **Everything was one long scroll.** Nine chapters stacked vertically: MSFT was
   5542 px tall at 1440 and 10198 px at 390. Reaching Evidence meant scrolling
   past every other chapter.
3. **Detail competed with the headline.** The intraday session board sat between
   the hero and the chapters, and every chapter carried a coloured layer tag
   ("Facts", "Model output", "Authoritative") at the same weight as its title.
4. **The rail broke landmarks.** The `aside.ts-rail` was nested inside the main
   region, which axe reported as `landmark-complementary-is-top-level`.
5. **Inconsistent heads.** Chapter titles were serif display type, card kickers
   were sans, and the rail used a third style. Spacing differed between the rail,
   the hero and the chapters.
6. **Copy.** Several strings used em dashes, and the ETF caveat repeated its
   own banner prefix ("ETF / Fund: ETF / Fund ...").

## Chosen structure

```
Banners (asset caveat, symbol note, price reconciliation note)
Summary band  #ts-snapshot
  Identity strip   ticker, name, regime, price
  Call   | Why this Decision | Main blocker
  facts  | reasons           | holding back, conflicts, "What would withdraw it"
  Key levels (entry, stop, target, valuation target, reward/risk)
Sticky section bar  nav.ts-nav
  [TICKER summary]  Decision detail | Business | Valuation | Technicals | Risks & catalysts | Evidence & data
One panel visible at a time  div.ts-panels > section.ts-panel
```

Why this shape:

- **Call, reason and risk in one band.** The summary answers "what is the call,
  why, and what stops it" without scrolling at 1440, and within the first 900 px
  at 390. Risk grade is a fact in the call column and links to the Risks panel.
  "What would withdraw it" links to every condition in Decision detail.
- **Tabs, not a longer page.** Six panels behind an ARIA tablist make every
  section one action away and drop the default page height by about 70 percent.
  Panels use `hidden="until-found"`, so browser find-in-page still reaches text in
  a closed panel and opens it.
- **Decision detail first.** It is the authoritative chapter, so it is the
  default tab rather than the ninth chapter.
- **One heading system.** Every block head in the summary and every card kicker
  is the same 7 px x 16 px strip on `--board-head` with a hairline below.
  Chapter heads are sans, `--fs-md`, weight 600. Cards share one padding and one
  hairline border. Panels hold a two-column card grid that falls to one column
  under 760 px of panel width (container query, not viewport).
- **Colour discipline.** One accent (selected tab underline, links, focus).
  Green and red only for price direction and stop/target. Amber for caution
  (model flag, blockers, poor risk grade). Risk grade A or B now reads in ink.

## What moved where

| Item | Before | After |
| --- | --- | --- |
| Main blocker, holding back, conflicts, DQ verdict | Hero and rail | Summary, "Why this Decision" block |
| Key levels | Sticky side rail | Summary, full-width strip under the call |
| Risk grade | Risks chapter only | Also a fact in the summary call column, linked to Risks |
| "What would withdraw it" | Decision chapter only | Also a one-line summary link to Decision detail |
| Decision chapter (why not Buy, gates, conditions) | Ninth stacked chapter | First tab, "Decision detail" |
| Thesis + Fundamentals | Two chapters | "Business" tab |
| Valuation + Scenarios | Two chapters | "Valuation" tab |
| Intraday session board | Between hero and chapters | First card of the "Technicals" tab |
| Risks + Catalysts | Two chapters | "Risks & catalysts" tab |
| Evidence and data confidence | Last chapter | "Evidence & data" tab |
| Price reconciliation note | Inside the session board | Banner row above the summary |
| Layer tags ("Facts", "Model output") | Visible chip on each chapter head | `data-layer` attribute only (kept for tests and tooling) |
| Section links | Link list in the rail | Sticky tab bar with a ticker chip that returns to the summary |
| Side rail `aside.ts-rail` | Present | Removed (fixes the axe landmark violation) |
| Guide "How a tear sheet reads" | 10 chapters | 7 sections matching the tabs |

Phone layout (under 760 px of summary width): the call column dissolves
(`display: contents`) so the order reads verdict, why, main blocker, levels,
then the nine facts and the caveat. The DOM order is unchanged, so keyboard focus
reaches the fact links (Risk grade, Track record) before the "all conditions"
link; the visual and focus orders differ only inside the summary band. The tab
strip scrolls horizontally on phones and the cut-off last tab acts as the scroll
hint.

Copy changes (frontend strings only): "Not yet, watch" for the WATCH new-money
word, ETF / ADR / Financial caveats without em dashes or a doubled prefix, the
bull/bear kicker now points to the Decision "above", and four Decision-adjacent
strings lost their em dashes. Engine text (for example the Monte Carlo
probability note and the model evidence summary) still contains em dashes because
it is shown exactly as returned.

## Page height (default view, MSFT payload)

| Viewport | Before | After |
| --- | --- | --- |
| 1440 | 5542 px | 1558 px |
| 390 | 10198 px | 2990 px |

SPY: 5142 to 1588 px at 1440, 8908 to 2999 px at 390. No content was removed;
the remaining sections sit in the other tabs.

## Keyboard and accessibility

- Tabs use roving tabindex: Arrow Left/Right, Home and End move between tabs and
  activate them. Each panel is a `role="tabpanel"` labelled by its tab.
- Jump links (`data-ts-jump`) open the right panel, scroll it under the sticky
  bar and move focus to it.
- Tabs are 40 px tall (44 px on coarse pointers); all links in the summary are at
  least 24 px.
- axe: 0 violations in light and dark with every tab opened (was 1).
- No horizontal page overflow at 320, 390, 768, 1024, 1440 and 1920 px with every
  panel unhidden.

## Tests changed for intended changes

- `tests/dom_redesign.test.cjs`, `tests/unit/research_page.test.cjs`,
  `tests/unit/research_headings.test.cjs`: assert the summary, tab bar, six
  panels and their chapters instead of the rail, layer chip and nine-chapter nav.
- `tests/unit/guide.test.cjs`: the Guide lists seven sections.

Selectors the live contract specs use (`.decision-action`, `.signal`,
`[data-contract]`, `[data-primary-blocker]`, `.decision-levels`,
`.decision-ticker`, `#ts-snapshot`, `#ts-<chapter>` ids) are unchanged.

## Verification

- `npm test` (jsdom): all pass.
- Python template and static asset tests: pass.
- Playwright `research_contract`, `redesign`, `guide` and the smoke deep-dive specs
  against a scratch server.
- `tests/tools/research_shots.cjs`: before and after screenshots at 1440 and 390
  in both themes, axe per tab, and the overflow sweep.

## Section board (2026-10-08 rebuild)

The tab bar became a board of six cells. Each cell has a name and one reading taken from the
payload. No cell repeats something the summary band already shows.

| Cell | Reading | Context line |
|---|---|---|
| Decision detail | Position size | Number of withdraw conditions |
| Business | Return on equity | Net margin and Piotroski. ETFs show N/A (ETF). |
| Valuation | Trailing P/E | Forward P/E and PEG |
| Technicals | Trend | RSI and a 52-week range scale |
| Risks & catalysts | Max drawdown | Next earnings, in amber when inside `WHY.EARNINGS_NEAR_DAYS` |
| Evidence & data | Price history in sessions | Date range and provider |

A missing figure shows "Not reported".

- **Layout.** The board shows 6 columns on desktop, 3 on tablet and 2 on phone, using container queries.
- **Sticky bar.** Once the bar sticks, it condenses to section names only. `.ts-nav-pad` absorbs the height difference, measured at subpixel precision. Sticking and unsticking causes 0 px of layout shift at 1440 and 390.
- **Accessibility.**
  - Tabs take their name from `aria-labelledby` and the reading from `aria-describedby`.
  - Panels are labelled by the name span only.
  - The tablist no longer repeats the nav label.
- **Section names.** They live once, in `TS_SECTIONS`.
- **Test changes.**
  - `tests/dom_redesign.test.cjs` reads tab names through `aria-labelledby` and asserts every reading is non-empty.
  - `tests/unit/guide.test.cjs` reads the section names and panel order from `TS_SECTIONS`.
- **Verification.**
  - npm test: 654 of 654 pass.
  - pytest: 110 pass.
  - Playwright: 130 passed, 2 skipped.
  - axe: 0 violations on every panel in both themes for MSFT, SPY and TSLA.
  - Overflow sweep: nothing overflows.
