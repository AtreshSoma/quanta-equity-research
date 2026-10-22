---
name: QUANTA Elite
description: A local research desk that reads like an instrument board. Plex type, hairline rules, one azure accent.
colors:
  primary: "#1a5dc8"
  primary-hover: "#154ca6"
  primary-dark: "#5ea2f7"
  bull: "#15803d"
  bull-dark: "#34d399"
  bear: "#b91c1c"
  bear-dark: "#f87171"
  warn: "#a16207"
  warn-bright: "#854d0e"
  warn-dark: "#fbbf24"
  warn-bright-dark: "#fcd34d"
  gold: "#b45309"
  gold-dark: "#f2b64a"
  quant: "#164a9c"
  info: "#0c63b0"
  ground: "#f3f5f9"
  surface: "#ffffff"
  surface-raised: "#f8fafc"
  board-head: "#eef2f7"
  hairline: "#e2e7ef"
  rule-strong: "#c9d2de"
  ink: "#0f172a"
  ink-secondary: "#334155"
  ink-muted: "#5b6778"
  ground-dark: "#090c12"
  surface-dark: "#10141c"
  surface-raised-dark: "#161b25"
  board-head-dark: "#1d2330"
  hairline-dark: "#222938"
  rule-strong-dark: "#343e50"
  ink-dark: "#eef2f7"
  ink-secondary-dark: "#c6cdd9"
  ink-muted-dark: "#929cae"
typography:
  display: { fontFamily: "'Playfair Display', Georgia, serif", fontSize: "24px", fontWeight: 700, lineHeight: 1.2, letterSpacing: "-0.012em" }
  headline: { fontFamily: "'IBM Plex Mono', ui-monospace, Consolas, monospace", fontSize: "24px", fontWeight: 700, lineHeight: 1, fontFeature: "tnum" }
  title: { fontFamily: "'IBM Plex Sans', system-ui, sans-serif", fontSize: "15px", fontWeight: 600, lineHeight: 1.35 }
  body: { fontFamily: "'IBM Plex Sans', system-ui, sans-serif", fontSize: "13.5px", fontWeight: 400, lineHeight: 1.55 }
  label: { fontFamily: "'IBM Plex Sans', system-ui, sans-serif", fontSize: "12px", fontWeight: 600, lineHeight: 1.4, letterSpacing: "0" }
  figure: { fontFamily: "'IBM Plex Mono', ui-monospace, Consolas, monospace", fontSize: "15px", fontWeight: 600, lineHeight: 1.35, fontFeature: "tnum" }
  figure-sm: { fontFamily: "'IBM Plex Mono', ui-monospace, Consolas, monospace", fontSize: "13.5px", fontWeight: 600, fontFeature: "tnum" }
rounded: { xs: "4px", sm: "7px", md: "10px", lg: "14px", pill: "999px" }
spacing: { sp-1: "4px", sp-2: "8px", sp-3: "12px", sp-4: "16px", sp-5: "20px", sp-6: "24px", sp-8: "32px", sp-10: "48px" }
components:
  facts-cell: { backgroundColor: "{colors.surface}", textColor: "{colors.ink}", typography: "{typography.label}", padding: "8px 14px 9px" }
  section-tab: { backgroundColor: "{colors.ground}", textColor: "{colors.ink-secondary}", typography: "{typography.body}", height: "40px", padding: "2px 2px 0" }
  section-tab-selected: { textColor: "{colors.ink}" }
  button-primary: { backgroundColor: "{colors.primary}", textColor: "{colors.surface}", rounded: "{rounded.md}", padding: "10px 16px", height: "40px" }
  button-primary-hover: { backgroundColor: "{colors.primary-hover}" }
  input-ticker: { backgroundColor: "{colors.surface}", textColor: "{colors.ink}", typography: "{typography.figure-sm}", rounded: "{rounded.sm}", padding: "8px 12px 8px 36px", height: "40px" }
  research-tile: { backgroundColor: "{colors.surface}", rounded: "{rounded.md}", padding: "12px 16px" }
  level-cell: { textColor: "{colors.ink}", typography: "{typography.figure}", padding: "8px 16px 10px" }
---

# Design System: QUANTA Elite

## Overview

**Creative North Star: "The Instrument Board"**

QUANTA reads like a measured panel, not a magazine. Every reading sits in a fixed cell with the same anatomy, separated by 1px hairlines rather than shadowed cards, so the eye sweeps across readings before opening any detail. Words are IBM Plex Sans; every measured value is IBM Plex Mono with tabular numerals, so a label and the figure under it share one skeleton and columns align.

Colour is meaning, never decoration. Azure is the single accent and marks what is selected, focused or actionable. Green and red are reserved for price direction, amber for caution, gold for price targets. Density comes from tight rows and flat surfaces, never from type below 12px. Both themes ship: dark at 1440 (.impeccable/review/desktop.png), light at 390 (.impeccable/review/mobile.png).

**Key Characteristics:**
- Fixed-anatomy cells on a hairline grid; a state restyles a cell, never breaks the grid.
- Plex Sans for words, Plex Mono tabular for figures.
- One azure accent; market hues carry meaning only.
- Flat working surfaces in Research; depth by tone and hairline.
- Missing figures are stamped "Not reported", never dropped.

## Colors

A cool slate field, one azure voice, four semantic hues that each mean one thing. Tokens are declared once on `:root` (light) and once on `[data-theme="dark"]` in webapp/static/quanta.css; values below read light / dark.

### Primary
- **Instrument Azure** (#1a5dc8 / #5ea2f7): selected cell rule and name, condensed-tab underline, 2px focus outlines, primary button fill, input focus border with a 3px `--primary-soft` halo, chart line. Hue ~215°, clear of violet.

### Semantic
- **Tape Green** (#15803d / #34d399) and **Tape Red** (#b91c1c / #f87171): price direction and a call's bull/bear tone. The stop level takes red as the downside figure.
- **Caution Amber** (`--warn` #a16207 / #fbbf24; `--warn-bright` #854d0e / #fcd34d): blockers, timing caveats, caution readings in the band's facts. Fills `--warn-dim`, borders `--warn-line`.
- **Target Gold** (#b45309 / #f2b64a): price targets only.
- **Model Ink** (`--quant` #164a9c / #93c3fb): QUANTA-derived figures; azure family, not a second accent.
- **Info Blue** (#0c63b0 / #5cc0f8): informational notes in the tear-sheet banner stack.

### Neutral
- **Ground** (#f3f5f9 / #090c12): page, and the condensed section bar once it sticks.
- **Surface** (#ffffff / #10141c): Decision band, tiles. **Raised** (#f8fafc / #161b25): cell hover. **Board Head** (#eef2f7 / #1d2330): header strips, press feedback.
- **Hairline** (#e2e7ef / #222938): every grid and divider rule. **Strong Rule** (#c9d2de / #343e50): input strokes, the 52-week track.
- **Ink**: primary #0f172a / #eef2f7, secondary #334155 / #c6cdd9, muted #5b6778 / #929cae; all at least 4.5:1 on Surface.

### Named Rules
**The One Voice Rule.** Azure is the only accent. Selection, focus and action use it; nothing else does.

**The Meaning Rule.** Green and red mean the market moved; amber means caution; gold means target. No hue appears for decoration or emphasis.

## Typography

**Body Font:** IBM Plex Sans (with system-ui, Segoe UI)
**Figure Font:** IBM Plex Mono (with ui-monospace, Consolas), always `font-variant-numeric: tabular-nums`
**Brand Font:** Playfair Display, for the wordmark, view page titles, Research chapter heads and the Guide's hero headline; never for tile titles, figures or body

**Character:** One engineering skeleton in two widths: Sans names things, Mono measures them.

### Hierarchy
- **Display** (Playfair 700, 24px `--hs-page`, 21px at 640px and below, 1.2, -0.012em): page titles ("Research", "Picks") and the wordmark. Tracking tightens as size grows; body and labels stay at 0.
- **Guide display** (Playfair 700, `--hs-display`: 32px rising to 46px with the viewport, 1.1, -0.018em): the Guide's hero headline only. The Guide is the one page that introduces QUANTA to someone new, so its headline carries the page; every app view keeps the 24px page title. A unit test keeps `--hs-display` to `.hw-title`.
- **Guide section** (Sans 600, `--hs-section`: 24px rising to 30px, 1.15, -0.014em): the Guide's own section titles (Why QUANTA, See it in 15 seconds, How QUANTA works, Honest about what it knows, Your first five minutes, Common questions), one clear step above a step's 20px heading.
- **Chapter** (Playfair 700, 20px `--hs-chapter`, 1.2): chapter heads inside a Research panel that holds more than one chapter, over a hairline.
- **Headline** (Mono 700, 24px): ticker and price in the Decision band; the call word is Sans 700 24px.
- **Title** (Sans 600, 15px): panel titles and key figures (`--fs-lg`).
- **Body** (Sans 400, 13.5px, 1.55): primary UI, table cells. Prose capped at 75ch.
- **Label** (Sans 600, 12px, 1.4, no tracking, sentence case): cell names, header strips, reading keys.
- **Figure** (Mono 600, 15px, tabular): board values and key levels; a word value (`.is-word`) falls back to Sans.

### Named Rules
**The Floor Rule.** Nothing renders below 12px (`--fs-2xs`). Density comes from rhythm, not small type.

**The Measured Value Rule.** A number someone measured is Mono with tabular numerals.

## Layout

A 4px spacing base (`--sp-1` 4 to `--sp-10` 48); the Research stack breathes at 12px. Page max 1760px, 24px gutter, 56px topbar.

Research from 1100px is a desk: the Decision column (360px, sticky) on the left; the live price chart, the section bar and the open panel on the right, the chart sized so both columns end level on Decision detail. Narrower, it reads top to bottom: request bar, Decision band, chart, sticky section bar, open panel, all at one content width. The shell shares one measure: `.topbar`, `.app-main` and `.app-footer` pad by `--shell-pad: max(--page-pad, (100% - --page-max) / 2)`, so header, page and footer start at one left edge. The band is a container (`tsrail`): from 720px an identity row, then the call beside its six facts (3 x 2), then why across two thirds beside the levels; narrower it stacks. `balanceDecisionBand` sets the levels two or three across, whichever ends nearer the why column. Panel chapters pair tiles two to a row (`tsp`), one column at 760px or narrower; a tile alone in its row spans it, and two tiles share a row only when their natural heights match within 15 percent (`fitChapterPairs`, `TS_PAIR_TOLERANCE`). Technicals sets the chart (viewBox 720 x 340) beside a stack of the session quote and Technical setup.

On coarse pointers or at 640px and below, every tab, button and link is at least 44px tall. At 520px viewport height or less the topbar scrolls away and only the section bar sticks.

## Elevation & Depth

Research is flat. Tiles and the Decision band carry a 1px hairline and no shadow (`#an-results` sets `box-shadow: none`). Depth is tonal: Surface over Ground, Board Head strips over Surface. The shared `--panel-shadow` and frosted glass belong to app chrome (topbar, popovers), never behind a price.

**The Overview Glass Rule.** The Overview adds frost (the glassmorphism skill, filtered through this system: IBM Plex and the azure accent stay) to its raised reading surfaces only: the market mood card, the lead news story and the "What to expect today" card, plus the hero chart's range tabs and the floating tooltips (glossary and chart crosshair). Frost is `--glass-blur` and `--glass-saturate` over a 62 to 66 percent surface, a 1px edge at 12 percent ink and a 1px top highlight; soft ambient light (the mood's tone and azure, about 15 to 20 percent) sits behind the hero and the news so the blur has something to read. Frost sits over static ambient light only, never over moving data or a scrolling list, so the contrast it was tested at holds; the lead story's stock bars use an opaque inset. Without `backdrop-filter`, or under `prefers-reduced-transparency`, every glass surface falls back to the solid Surface.

**The Discover Glass Rule.** Discover keeps glass for the Street ideas only (owner direction, 2026-10-21: fewer boxes, sections blended into the page). Static azure lights (11 to 22 percent) sit behind the page and never move. The idea cards, the ideas list, the comparison and the side panel are the glass: one token (`--dc-frost`, blur 22px at 170 percent saturation) over a 160-degree white gradient (7.5 to 2 percent dark, 92 to 78 percent light), a 1px edge, an inset top highlight and a soft drop, radius 16. Hover lifts a card 2px and brightens its edge; press scales to 0.99. Everything else on the page has no frame: a section is its heading on a 1px hairline (18px Sans 600 title), and its rows sit on hairlines over the page itself. Under `prefers-reduced-transparency` or `prefers-contrast: more` every glass surface is solid Surface.

The page reads top to bottom, tape before ideas. The quote status line sits under the lede at every width.
- **The brief** (`#discover-pulse`), on its own row under the Playfair title (the quote status sits at the right of the title row): three or four readings of the ideas on hairline separators, no frame, each a 12px label over a 20px Mono figure over one line of context, built only from figures on the page. With every idea's odds in, it leads with **Best target odds** (the ticker and the odds in Model Ink, "to $586.95, from option prices", or "from price history" when no usable chain priced it); before that with **Most ideas** (the leading sector's share). Then **Median upside** in gold and where it sits among every tracked name, **Report within N days** (how many, and who reports first, in amber when today or tomorrow), and **Target may lag** (up to three tickers, its context in amber); without a lagging target the sector reading takes the last slot. Labels that need defining carry a tooltip. Its tickers are buttons that find their card. Two across under 900px, where the context lines wrap.
- **Today's tape** (`#security-monitor`): a run-in read under the title (how many moves follow earnings, which look like deals, which are giving back their gap); underline tabs for Movers, Opened at high/low (with its match count), Watchlist, Extended and Avoid in the same header row at the right; the run-in reads the movers, so it steps back (keeping its space) on every other tab (a 2px azure rule grows under the open tab; a tab that fills only after a scan is muted; on phones the tabs drop under the title and scroll sideways). The Movers panel (`#tape-main`) sets gainers beside losers on one centre hairline; under 1080px they stack. Over the movers, two segmented controls: cause (All, Likely deal, Earnings, Analyst action, Sector move, No clear cause) at the left and size (any, at or over `MARKET.EARNINGS_MIN_MARKET_CAP`, under it) at the right; every count follows the size. On phones the tray wraps rather than scrolls. Each side is a labelled list of hairline rows under a muted column key (Symbol, Cause, Session, Last, Change), every move shown: the ticker (its TradingView chart in a new tab) with Extended and Held marks and the company under it, the cause (Checking cause while the read is out, Cause not loaded when it fails, never "No clear cause" unread), the session (gap held, extended, faded in amber, reversed in amber, with relative volume inked at `MARKET.RVOL_HIGH`), the price, and the change in its direction colour over a 15 percent fill drawn linearly on one scale for both sides (dollar move and sector baseline in its title). The row opens Research; a drawn Research icon appears in its own column on hover or focus. Where a side is narrower than 600px (a container query) each row reads on two lines, ticker, price and change, then cause and session, and the volume figure steps out.
- **Opened at high/low** (`#open-extremes`) is its own tab of the tape: one run-in line (what the boards show, a price condition, not a recommendation) with the match meta at the right, then the open-at-low board beside the open-at-high board on the centre hairline, then one footnote line across both ("Session snapshot" and the coverage `<details>`, "2 exact matches from 189 fetched quotes. Coverage"). Each board keeps the OHLC figures that qualify a row: Symbol (with the company beside it), Open, High, Low, Prev., Last with the change beside it, sortable by symbol and change, 40px rows on hairlines. Under 1080px the boards stack; on phones the company and Prev. step out and the change drops under the price. The boards show every match the backend found; the movers' cause and size filters do not apply to them.
- **Street ideas** (`#opps-filters`, `#portfolio-scan-body`): sector segments with counts, the earnings `role="switch"`, then sort segments (Likeliest, Upside, Revisions, Most analysts) and a Cards / List view switch, remembered per browser (`quanta.discover.view`). **Cards** (default): four across, two under 1180px, a swipeable snap row with the next card peeking under 640px. Each card: ticker and company, the Street's rating and analyst count, the price with its 3-month change, a 52px area chart of about three months in the direction colour, then a stats row of Upside (to the gold target), Target odds as a 42px ring in Model Ink, and Revisions (raises against cuts, cuts amber when they lead), and a one-line note footer: a report within three days in amber with the option-priced move, else "Target may lag" in amber, else days to the report, else a calm "No report due, no target flag". The whole card is one button (its label names the Street rating, upside and odds) that opens **the side panel**; the ticker link and the odds ring sit above it. **List**: the compact sortable table (Ticker, Price with spark, Upside bar, Target odds, Revisions, Street rating), row detail and the Compare ticks.
- **The side panel** (`#dsc-drawer`, `role="dialog"`, `aria-modal`): slides in from the right at 260ms on the iOS drawer curve and leaves at 180ms; a scrim dims the page; reduced motion fades. It holds the ticker, price, 3-month change and upside, a 120px chart, Add to compare and Open tear sheet, then the Street's view (the targets and 52-week bar), the odds sentence with volatility and momentum, and the next step. Escape, the scrim and the close button dismiss it and return focus to the card. When odds arrive after it opened, it redraws in place.

Discover's figures:
- **Tooltips**: one shared `role="tooltip"` element (`#ovx-tipbox`) serves Overview and Discover; it opens on hover, focus or tap, closes on Escape, and is linked with `aria-describedby`. Table header terms and the range bars carry one.
- **Upside**: the figure and the average target in gold Mono. **Target odds**: the chance of finishing at or above the average target in 12 months at the option market's implied volatility (the stock's 1-year volatility when no sane chain exists, then captioned "price history"), in Model Ink; risk-neutral, never called a forecast. **Revisions**: target raises against cuts over `MARKET.REVISION_WINDOW_DAYS` and the 30-day EPS drift. Odds, revisions, charts and causes arrive from `/api/discover/enrich` after first paint; until then a quiet placeholder (an empty ring, "Reading"), never N/A; when the read fails, odds and revisions step out of the card and the list rather than show as missing. **Compare** (up to three, from the list's ticks or the side panel) opens a table under the ideas, one row per measure, the stronger figure in bold, beside a "Where each leads" summary. The list steps columns out as it narrows so it never scrolls sideways.
- **Targets and 52 weeks** (Street consensus): the 52-week range as a grey track, the analysts' lowest to highest target as a faint gold band, the average target as a gold diamond and the price as a dot, on one scale that stretches to hold every mark. The two end figures are the scale's real ends, gold when the end is a target.

All are `role="img"` with a sentence label or `aria-hidden` beside the figure they draw. An amber "Earnings in N days" chip marks a report inside `WHY.EARNINGS_NEAR_DAYS`, counted on the exchange's own calendar. A status line beside the page title gives the quote time and age, how many tracked names were read and a retry when the feed is delayed; on Discover the header's freshness chip reads the same quotes. `j` and `k` step through the rows of the table in view. Security monitor tabs carry their count, or "needs a scan" (muted) when their list only fills after a Picks scan. Under `prefers-reduced-transparency` or `prefers-contrast: more` every Discover glass surface is solid Surface with a hairline.

**The Hairline Rule.** Structure is drawn with 1px `--border-soft` rules. A heavier stroke is a state (3px selected rule, 2px condensed underline), never a frame.

## Motion

Motion is feedback and wayfinding, never decoration (the apple-design skill, filtered through this system).

- **One spring.** `--ease-spring` is critically damped (no overshoot, about 0.33 s response) over `--dur-spring` 480ms. Nothing in QUANTA follows a flick, so nothing earns bounce.
- **Origins.** Popovers (data status, Export) grow from the button that opened them (`transform-origin: top right`) and leave along the same path.
- **Direction.** The primary nav has one azure underline that travels to the new tab; views without a tab hide it.
- **Brightness.** The theme switch cross-fades the page (View Transitions) instead of flashing.
- **Press, not release.** Pressed states apply on `:active`; hover styles apply only where a pointer can hover (`@media (hover: hover)`), so a tap on a phone leaves nothing highlighted. A hover that reveals content stays unguarded so touch can reach it.
- **Discover reveals once.** The tape prints on its first paint with data: rows settle in order (420ms, 32ms apart, capped at twelve) and each change fill sweeps out from the price edge (640ms), so magnitude reads before the digits. Street idea cards rise as they scroll into view on a CSS view timeline, their odds arcs filling to the read and their charts drawing left to right; because the scroll position drives it, a card at rest in view is always fully shown (full-page captures and print included), and without view-timeline support nothing moves. The tape print runs once per page load (`armReveal`, IntersectionObserver, with a 2.5s safety release); refreshes and filter changes redraw in place. A mover whose price changed since the last read flashes its price once in the direction colour (1.2s, timed from the change so a redraw resumes it). Content is visible by default; without IntersectionObserver or under reduced motion nothing is held back.
- **Reduced motion** stops movement but keeps the comprehension fades: views, popovers and the theme switch fade; nothing slides, scales or springs.

## Shapes

Three radii carry the system: controls 7px (`--r-sm`), blocks and tiles 10px (`--r-md`), shell panels 14px (`--r-lg`); 4px (`--r-xs`) for caveat boxes and focus rings, pills for chips. Band fact cells and score cells are square on hairlines. A call's tone is an 8px square key with 2px corners beside its label, never a coloured edge.

## Components

### Decision Band (signature)
The Decision in one full-width frame, each block on a hairline: the answer to what, why, how risky and at what levels, on the first screen at 1440 x 900.
- **Identity row:** ticker (Mono 22px), company, sector and market cap (Mono figure), sentence-case chips (asset flags, `Market: <regime>`); price and day move at the right edge. Fresh data is the price's tooltip; stale data is an amber fact.
- **Call:** `QUANTA Decision` with the 8px tone key, the call word (Sans 700 24px) with the model flag beside it, then its meaning.
- **Facts (3 x 2):** Horizon, Model stance, New money, If you hold it, Conviction, Risk grade; label over value on hairlines. They stay inside `.decision-action` (`data-contract` hooks the e2e contract reads).
- **Why:** the engine's reasons, then every rule in `decision.blockers`, once each and in order: the main blocker in a 4px box with a 1px `--warn-line` border on `--warn-dim` and its remedy, the rest under "Also holding it back" with theirs. The Fast preview rule carries "Load full analysis". Carries `data-why-not-buy`; each rule's code is `.gate-name[data-gate]`.
- **Key levels:** Entry, Stop, the Decision's target, Reward / risk and the 12-month valuation target as context; stop red, target gold, context muted.

### Live Price Chart
`static/live-chart.js` over `GET /api/chart`; on Overview (under the ribbon) and Research (beside the Decision). Range tabs 1D to Max (azure when selected). Line green at or above the reference (previous close on 1D, first bar otherwise), red below, with an 18 percent fill fading to nothing; previous close dashed gray; stop red and target gold dashed; volume bars gray; extended hours shaded on Raised. Axis text is HTML (Mono 12px for prices) so it never scales; the last price and the previous close sit in hairline badges on the price axis. 1D spans the exchange's full trading day in its own time zone.

### Guide Walkthrough
The Guide's "How QUANTA works" is told by scroll: the five pages stack as steps, each opened by a 44px icon tile beside its heading. A step bar (`nav`, one button per page: page name in Sans 700 over what it does in muted Sans) sticks under the topbar on Ground with a bottom hairline. It marks the step crossing the middle of the screen (`aria-current="step"`), moves one 2px azure rule under it on the system spring, and fills a 1px azure line as the whole story is read (a CSS view timeline; absent where unsupported and under reduced motion). On phones the bar scrolls sideways and the current step carries its own rule. Each step's demo performs its rule once as it arrives (dial sweep, gates firing beside the sticky funnel, the call stepping down the ladder, the risk box growing). Reference lists fold behind one "More detail" button per step. Without JavaScript the bar is hidden and the steps read the same.

### Section Tabs
Six names (`TS_SECTIONS`): Decision detail, Business, Valuation, Technicals, Risks & catalysts, Evidence & data. The bar sticks under the topbar on Ground with a bottom hairline; the open tab is ink over a 2px azure underline. Once the band has scrolled away (`is-stuck`) the bar leads with the ticker, the call word and the price. `role=tab` buttons in a `role=tablist`, roving tabindex, ArrowLeft/ArrowRight wrap, Home/End jump. Closed panels are `hidden="until-found"` so find-in-page opens them.

### Research Tiles
1px hairline frame, 10px radius, 12px 16px padding, no shadow, no card inside a card. Chapter heads are Playfair 20px 700 over a hairline; tile titles are Sans 13.5px 600 on a Board Head strip; a sub-block inside a tile (Position sizing, Signal conflicts) is Sans 13.5px 600 under a hairline. Fact rows: label left, Mono value right, hairline above each row.

### Request Bar and Inputs
Ticker field: 40px tall, 7px radius, Strong Rule stroke, Mono uppercase value, Sans placeholder, inset 16px icon; focus swaps to an azure border with a 3px soft halo. Primary button: azure fill, 10px radius, Sans 700, darker azure on hover, 1px press. Inputs are 16px on phones so iOS does not zoom.

## Do's and Don'ts

### Do:
- **Do** set every measured value in Plex Mono with `tabular-nums`.
- **Do** draw structure with 1px `--border-soft` hairlines and keep Research tiles shadowless.
- **Do** give every band fact and score cell the same anatomy and stamp "Not reported" where a figure is missing.
- **Do** read thresholds such as `WHY.EARNINGS_NEAR_DAYS` from config; copy never carries the number.
- **Do** keep targets at 44px on coarse pointers and honour `prefers-reduced-motion` for scrolls.

### Don't:
- **Don't** use azure for anything but selection, focus and action, or green/red for anything but price direction.
- **Don't** set text below 12px.
- **Don't** use em dashes or emojis in UI copy.
- **Don't** put eyebrows or kickers above headings; labels are sentence case with no tracking.
- **Don't** use Playfair inside a tile; in the tear sheet it is for chapter heads only.
- **Don't** state a fact twice: the band owns the call, its facts, its rules and its levels; panels own the detail.
- **Don't** let a tile carry a dead floor: a pair that differs by more than 15 percent in height becomes two full rows.
