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
  display: { fontFamily: "'Playfair Display', Georgia, serif", fontSize: "30px", fontWeight: 700, lineHeight: 1.15 }
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
**Brand Font:** Playfair Display, for the wordmark, view page titles and Research chapter heads; never for tile titles, figures or body

**Character:** One engineering skeleton in two widths: Sans names things, Mono measures them.

### Hierarchy
- **Display** (Playfair 700, 30px, 1.15): page titles ("Research", "Picks") and the wordmark.
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

**The Hairline Rule.** Structure is drawn with 1px `--border-soft` rules. A heavier stroke is a state (3px selected rule, 2px condensed underline), never a frame.

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
