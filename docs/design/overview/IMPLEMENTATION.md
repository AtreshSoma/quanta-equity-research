# QUANTA Overview redesign: proposal

Status: proposal only, awaiting approval. No app code was changed for this document.

**Mockup files**
- **Revision 2 (current):** `overview-mockup.html`. Screenshots: `overview-mockup.jpg` (1440 viewport) and `overview-mockup-mobile.jpg` (390).
- **Revision 1 (kept for comparison):** `overview-mockup-v1.html` and `overview-mockup-v1.jpg`.

Both mockups are static, with the live data from Sun 2026-10-18 hardcoded. The 1440 screenshot is 1425px wide because the scrollbar takes 15px.

---

## Revision 2 (supersedes sections 2 to 4 and 6 where they differ)

**Feedback addressed**
- The page still read as boxes of jargon.
- The news and the bulletin were missing.
- Nobody understood the regime section.
- Market Pulse belonged at the top.
- It needed more charts and fewer boxes.

### New page order

| # | Section | What it is |
|---|---|---|
| 1 | News bulletin | A thin, scrolling headline bar under the nav (time, headline, source). It pauses on hover or focus, and is static under reduced motion. It is the only animated element. |
| 2 | Status line | "Markets closed for the weekend", which close the prices are from, next session, data health. |
| 3 | Market pulse | 9 instruments. Each shows Friday change, a 1-month sparkline and the week change. No boxes: hairline columns on the page background. Every name has a plain tooltip ("Fear index: VIX, expected volatility"). |
| 4 | This week + Market mood | See below. |
| 5 | Market news | Filters (All, Markets, Economy, Earnings, Politics) and a news-mood bar. A lead story with "Why it matters" and a chart of the stocks it names. Two secondary stories, then a timed list. Right rail: trending topics bar chart, stocks in the news with Friday change, most-covered story. |
| 6 | Where the money moved | Two breadth donuts (sectors and indexes in an uptrend). Sorted, diverging sector bars. Gainers and losers bars. |
| 7 | Fear, rates and oil | Three 1-month mini charts. The VIX chart carries plain zones: calm, normal, nervous, fearful. |
| 8 | The week ahead | Earnings as bubbles sized by market value, placed by day and by morning or evening. "What to expect Monday": the likely range band and the history odds bar. |
| 9 | Ideas for you | The empty state explains the scan in 3 steps; sample cards show a stop, buy and target line. |
| 10 | For pros | Collapsed: factors, key levels, cross-asset. |

**Section 4 in detail**
- **Left:** a headline sentence ("The S&P 500 fell 2.0% this week"), a 5-session S&P 500 area chart from 30-minute bars with each day's change, a Friday callout, last week's close and the 50-day line, and three plain facts.
- **Right:** the Market mood card, which replaces the regime section.

### The regime, translated

The **Plain English / Pro** toggle in the top bar switches the wording; Plain English is the default.

| Pro term (today) | Plain English |
|---|---|
| Defensive regime | Market mood: Cautious. "Our model says: play it safe for now." |
| Direction 33/100 | Mood dial, 0 to 100: Cautious, Mixed, Confident |
| Gross exposure 20-50% | "Put no more than half of your money into stocks right now", shown on a 0 to 100% money bar |
| Factors against | "Why we are cautious": 3 plain reasons, each with a mini visual (index dots, sector change pill, news count) |
| What flips the call | "What would make us more positive": 2 progress bars toward a target |
| Confidence 59 | "How sure we are: Moderate", with a meter |

### Visual rules added

- **Few surfaces.** Only 2 surfaces are raised: the mood card and the lead news story. Idea cards appear only in the preview. Everything else sits on the page background, separated by space and hairlines.
- **Every section opens with a picture:** sparkline, area chart, dial, bars, donut, zone chart, bubbles, range band.
- **Glossary tooltips** on every term a beginner may not know (dotted underline). They work on hover and keyboard focus.

### New components

| Component | Key props |
|---|---|
| `NewsBulletin` | `items` (time, title, source, url) |
| `WeekChart` | `bars` (30-minute closes), `dayChanges`, `refLines` (last week close, 50-day), `callout` |
| `MoodCard` | `mood` (Cautious, Mixed, Confident), `score`, `investBand`, `reasons[3]` (text, visual), `goals[]` (label, now, progress), `confidence`, `proLine` |
| `NewsLead` | `title`, `source`, `age`, `sentiment`, `whyItMatters`, `tickers[]` (symbol, change) |
| `NewsList` | `items` (time, title, source, sentiment, category) |
| `NewsMood` | `negative`, `neutral`, `positive`, `total` |
| `TopicBars` | `topics` (label, count) |
| `StockChips` | `stocks` (symbol, name, change) |
| `BreadthDonut` | `above`, `total`, `label` |
| `SectorBars` | `rows` (name, change, above50) |
| `MiniChart` | `series`, `zones` (optional), `value`, `caption` |
| `EarningsBubbles` | `events` (symbol, day, timing, marketCap), `highlight[]` |
| `MondayRange` | `low`, `high`, `last`, `baseRate` (up percent, n, since) |

### Data added by revision 2

These come on top of section 7's list:
- **Plain-English "Why it matters" line per lead story.** Today the engine's `desk_action` is trader shorthand. Proposed: a short template per catalyst type, written in plain words.
- **Prices for tickers named in headlines** (`news.mentioned_tickers`, already proposed).
- **Better sentiment.** The keyword classifier mislabels some headlines; for example "Top Wall Street analysts are bullish..." is tagged negative. The mockup leaves that item out. Needed: a better sentiment classifier, or a small rule fix with tests, before news tags ship.
- **Daily change per index for the week** (from the same batched download as the sparklines).

### Phase plan changes

- **Phase 1 now:** shell, bulletin, status line, pulse, Market mood card, Plain/Pro toggle (80-110k).
- **Phase 2:** week chart.
- **Phase 4 becomes the full Market news section:** 70-90k, including the sentiment fix.
- **Phases 5 to 8** map to the money-moved, fear/rates/oil, week-ahead and ideas sections.
- **Total:** about 500-650k tokens.

### Questions added

- Should Plain English be the default for everyone, with Pro remembered per user?
- Should the bulletin carry only high-impact items, or all of the latest headlines?

---

---

## 1. Status report

**Done (uncommitted in the working tree)**
- Backend fixes, each with a regression test in `tests/test_overview_fixes_1018.py`:
  - chart timestamps (the "Dec 31" close time and the empty 1D chart);
  - regime history keyed to trading sessions, so weekend rows are gone;
  - personal-finance columns removed from high-impact news;
  - one high-impact count, published as engine keys;
  - the engine headline names only a driver that matches the call;
  - the band cap only ever lowers the band;
  - weekend wording.
- A first Overview layout pass (the current screen).
- Updated JS and e2e tests.

**Test results**
- Python: 2,094 pass.
- JS unit: 716 pass.
- E2E smoke and axe: 14 of 15 pass.

**In progress**
- Fixing the timing of the e2e theme-toggle test. You stopped this; it was not applied.

**Pending**
- This redesign.
- Commit.
- Updating DESIGN.md.

**Blockers:** none.
- `UX-006` (theme toggle) fails because the test reads the theme before the View Transition applies it. This predates my changes.
- Unrelated uncommitted Picks changes were already in `app.js`, `quanta.css`, `index.html` and `recent_scans.test.cjs` before this work.

**Files modified so far**
- `webapp/app.py`
- `core/engine/thresholds.py`
- `webapp/static/app.js`
- `webapp/static/quanta.css`
- `webapp/templates/index.html`
- `webapp/tests/fixtures/thresholds.json`
- `tests/test_market_context.py`
- `tests/test_overview_fixes_1018.py` (new)
- 13 JS test files
- 2 e2e specs

---

## 2. Design principles and the 5-second test

**Five questions, answered in this order, above the fold:**

| Question | Answered by |
|---|---|
| How is the market doing? | Verdict word, plus the week in 3 sentences |
| What is trending? | Pulse tiles with sparklines, trending chips |
| Hot news? | 3 headline cards with impact and meaning |
| Forecast? | Next-session range band and history base rate |
| One idea worth acting on? | Ideas card 1, or the scan call to action |

**Principles**
1. **One lead.** The verdict word is the only 56px element. Everything else steps down a clear scale.
2. **Pictures before prose.** Every reading has a visual: gauge, meters, sparklines, band, treemap. Sentences explain; they never carry the number.
3. **Color has one meaning.**
   - Green and red: price direction only.
   - Regime hues (amber, slate, violet): stance only.
   - Amber: caution.
   - Azure: action.
4. **Closed is a designed state.** On weekends and holidays the page becomes "Week in review and the week ahead". Changes read Friday and week, and nothing says "today".
5. **Honest numbers.**
   - A probability appears only as labelled history ("53.8% of 273 sessions").
   - Proposed fields are listed in section 7 before they reach the page.
   - Missing data shows as unknown, never as zero.
6. **Detail is one click away.** The 10 factors, the full chart and cross-asset sit in collapsed rows at the bottom.

**Three unique features (chosen)**
1. **Market in 3 sentences.** On weekends it becomes the week in review. It is the fastest route to "how is the market doing" and is composed only from fields the page already shows, so it cannot drift from them.
2. **Verdict history strip (last 10 sessions).** A call earns trust when you can see how long it has held and when it last changed. Unrecorded sessions show as empty slots, never filled in.
3. **Price vs headlines flag.**
   - When price action and the high-impact tape disagree (engine `dataFreshness.newsConflict`), the reader should size down.
   - When they agree, the flag confirms it in one line.
   - The field already exists.

**Rejected**
- **Regime-coloured page accent:** kept only as the hero's top rule, so colour does not compete with price hues.
- **Scenario cards with probabilities:** no calibrated model exists; they would invent numbers.

---

## 3. Wireframes

### Desktop (1440, 12 columns, max width 1360)

```
+--------------------------------------------------------------------------------------------+
| QUANTA Elite   Overview  Discover  Research  Picks  My Portfolio      [Search] [Guide] [Ev] |
+--------------------------------------------------------------------------------------------+
| (o) Closed | Weekend: week in review | Prices: Fri Oct 16 close | Next: Mon 9:30 ET | Data | [US|India]
+--------------------------------------------------------------------------------------------+
| MARKET VERDICT, FRI OCT 16 CLOSE                       | WHAT FLIPS THE CALL               |
| DEFENSIVE  [Stance: capital preservation]              | VIX below 16           20.0 now   |
| one-sentence summary                                   | [=========......|]                |
| +----------------+----------------+------------------+ | S&P above 50-day 767.40  763.25   |
| | gauge 33/100   | Confidence     | Gross exposure   | | [==========.....|]                |
| | bearish zone   | Moderate 59    | 20-50%  [band]   | | 3 of 4 index funds      0 of 4    |
| +----------------+----------------+------------------+ | [...............|]                |
| STANCE [##][  ][  ][  ][  ][  ][  ] Def .. Neu .. Off  | Weaker read: 2 of 4 hold ...      |
| HISTORY [ ][ ][ ][ ][ ][ ][ ][ ][ ][#]  PRICE vs NEWS ok|                                   |
+--------------------------------------------------------------------------------------------+
| WEEK IN REVIEW: The market in 3 sentences | sentence 1 | sentence 2 | sentence 3            |
+--------------------------------------------------------------------------------------------+
| Market pulse: [SPX][NDX][DOW][RUT][VIX][10Y][GOLD][OIL][BTC]  each: price, Fri, week, spark |
+--------------------------------------------------+-----------------------------------------+
| Next session: S&P 500 (7 col)                    | Hot now (5 col)                         |
| 715 ----[ 68% band ]--- markers --- 785          | [theme chips] [ticker chips +/- %]      |
| Implied range | After 1% down day | Levels | Risk| [card] [card] [card]  all headlines >    |
+--------------------------------------------------+-----------------------------------------+
| Sectors (8 col): breadth counts + treemap        | Largest movers (4 col): gainers, losers |
+--------------------------------------------------+-----------------------------------------+
| Ideas for this call (12): ranked cards or designed empty state with Run a Picks scan        |
+--------------------------------------------------------------------------------------------+
| Week ahead (12): Mon..Fri columns, count bar, largest 5 per day, top 3 highlighted, macro  |
+--------------------------------------------------------------------------------------------+
| > All 10 factors      > Price chart      > Cross-asset           (collapsed)              |
+--------------------------------------------------------------------------------------------+
| Disclaimer                                                         Model evidence           |
+--------------------------------------------------------------------------------------------+
```

### Mobile (390, single column, 16px gutters)

```
+------------------------------+
| QUANTA        [menu] [search]|
| (o) Closed  Fri close  [US|IN]|
+------------------------------+
| DEFENSIVE                    |
| summary (2 lines)            |
| [gauge 33] [conf Mod] [20-50%]|  three cells in one row, 11px labels
| stance bar                   |
| What flips the call          |  3 meters, stacked
+------------------------------+
| Week in review (3 sentences, |
| stacked)                     |
+------------------------------+
| Pulse: horizontal scroll,    |
| 2.3 tiles visible, snap      |
+------------------------------+
| Next session band + 2x2 stats|
+------------------------------+
| Hot now: chips (scroll),     |
| 3 cards                      |
+------------------------------+
| Ideas (cards stack / CTA)    |  moved above sectors on mobile
+------------------------------+
| Sectors: treemap 240px,      |
| breadth counts above         |
+------------------------------+
| Movers (gainers, losers)     |
+------------------------------+
| Week ahead: one row per day, |
| top 3 names, "+N more"       |
+------------------------------+
| collapsed detail rows        |
+------------------------------+
```

---

## 4. Section by section spec

| # | Section | Purpose | Data (see section 7) | Component | Loading | Empty | Error | Market closed |
|---|---|---|---|---|---|---|---|---|
| 1 | Status strip | Is the market open, how fresh is the data | session, sourceHealth, quote_date, generated_at | `StatusStrip` | Pill "Reading" | n/a | Pill "Offline", Retry | "Closed", "Weekend: week in review", next session |
| 2 | Verdict hero | The call in one word plus its measures | engine regime, score, confidence, band, history, newsConflict | `VerdictHero`, `DirectionGauge`, `ConfidenceMeter`, `ExposureBand`, `StanceBar`, `HistoryStrip`, `AgreementFlag` | Skeleton of word and 3 cells | "No read: inputs missing", band hidden | Last good read plus amber "Refresh failed" banner | Label "Fri Oct 16 close" |
| 2b | What flips the call | Distance to a change of stance | vix, participation, sma50, sma200, thresholds | `FlipMeter` x3, `WeakerLine` | 3 grey meters | A condition with no input is omitted | Same as hero | Unchanged |
| 2c | Market in 3 sentences | 5-second summary | quotes, week change, sectors, earnings count, VIX | `Briefing` | 3 skeleton lines | Omitted if under 2 inputs | Omitted | Title "Week in review", week figures |
| 3 | Market pulse | Trend at a glance | quotes, spark, week_change | `PulseTile` x9 | Tile skeletons | "Not reported" in tile | Tile shows the last value with a stale tag | Friday and week rows; crypto says "24-hour" |
| 4 | Forecast | Where tomorrow likely trades | levels, expected_move, base_rate, risk event | `RangeBand`, `StatCell` x4 | Band skeleton | Band hidden if no VIX; text says why | Last read | "Next session Mon Oct 19" |
| 5 | Hot now | What the tape is talking about | trends, mentioned_tickers, high-impact articles | `TrendChip`, `HeadlineCard` x3 | Card skeletons | "No high-impact headlines" (feeds live) | "Feeds unreachable: headline risk unknown" | Same; ages in hours or days |
| 6 | Sectors | Where money moved, breadth | sectors, weights, sector_participation, participation | `BreadthCounts`, `Treemap` | Grey treemap | "Sector quotes unavailable" | Restart or failed-read note | "Friday's move" |
| 6b | Largest movers | Biggest single-name moves | movers_up, movers_down, universe_size | `MoverList` x2 | Rows skeleton | "Screen returned no movers" | Same | Dated "Fri Oct 16" |
| 7 | Ideas for this call | The one idea worth acting on | picks recommendations, mandate, sector trends | `IdeaCard`, `IdeasEmpty` | Card skeletons | Designed empty state with "Run a Picks scan" | "Last scan failed", link to Picks | Empty-state copy unchanged; scans can run on weekends |
| 8 | Week ahead | What is coming | earnings_calendar, macro calendar | `WeekTimeline`, `DayColumn` | Skeleton columns, "Loading this week" | "No dated events" only after the calendar answered | "Calendar did not answer" note | Starts Monday; weekend columns hidden unless something is dated on them |
| 9 | Detail (collapsed) | Evidence on demand | factors, chart, cross_asset | native `details` | n/a | n/a | n/a | n/a |

---

## 5. Visual system tokens

### Color

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | #0A0D14 | #F4F6FA | Page |
| `--surface` | #11151F | #FFFFFF | Cards |
| `--elev` | #171C28 | #F1F4F9 | Inner cards, chips, tracks |
| `--border` | #232A3A | #E2E7EF | Card and row rules |
| `--border-strong` | #33405A | #C9D2DE | Inputs, axis, dividers |
| `--text` | #E9EEF6 | #0F172A | Primary text, numbers |
| `--text-2` | #B4BFCF | #334155 | Secondary text |
| `--muted` | #8A96A8 | #5B6778 | Labels, captions (4.5:1 or better on surface) |
| `--up` | #34D399 | #15803D | Price up only |
| `--down` | #F87171 | #B91C1C | Price down only |
| `--accent` | #5EA2F7 | #1A5DC8 | Links, buttons, focus, selected, highlighted events |
| `--warn` | #FBBF24 | #A16207 | Caution, high-impact tag, weaker-read line |
| `--reg-defensive` | #F2A93B | #B45309 | Defensive stance only |
| `--reg-neutral` | #9AA6B8 | #64748B | Neutral stance only |
| `--reg-offensive` | #A78BFA | #6D28D9 | Offensive stance only |
| `--up-tint`, `--down-tint` | rgba of up/down at 7% | 6% | Pulse tile wash |

**VIX colour:** green and red follow its price direction, like every other quote. The tile adds the text "Higher means more expected volatility" so the meaning never depends on colour.

### Typography

Fonts: IBM Plex Sans (UI) and IBM Plex Mono with `tabular-nums` (every number).

| Role | Family | Size / line | Weight | Notes |
|---|---|---|---|---|
| Hero verdict | Sans | 56 / 60 | 700 | Tracking -0.03em; one per page |
| Section title | Sans | 18 / 24 | 600 | |
| Card title | Sans | 15 / 21 | 600 | Headline cards, idea ticker line |
| Body | Sans | 14 / 20 | 400 | Minimum 13 / 18 for captions |
| Label | Sans | 11 / 14 | 600 | Uppercase, tracking 0.08em, `--muted` |
| Number large | Mono | 32 / 36 | 600 | Direction, exposure, confidence word in Sans |
| Number medium | Mono | 20 / 24 | 600 | Tile price, forecast stats |
| Number small | Mono | 13 / 17 | 500 | Changes, table cells |

### Space, shape, grid, motion

- **Spacing (4px base):** 4, 8, 12, 16, 20, 24, 32, 40, 48, 64.
- **Radius:** card 12px, tile 10px, control 8px, chip and pill 999px, bar 3px.
- **Border and depth:**
  - 1px borders.
  - No card shadows in dark; light uses `0 1px 2px rgba(15,23,42,.05)`.
  - Only popovers get a soft shadow.
- **Grid:** 12 columns, max width 1360px, 24px gutter, 32px page padding.
- **Breakpoints:**

  | Width | Padding | Layout change |
  |---|---|---|
  | 1440 | 32px | Full layout |
  | 1280 | 24px | Hero flips under the verdict |
  | 1024 | 24px | Forecast and Hot now stack |
  | 768 | 20px | Treemap full width |
  | 480 | 16px | Single column, pulse scrolls |

- **Motion:**
  - Number tick on refresh: 400ms, ease-out.
  - Sparkline draw once: 600ms.
  - Hover background: 120ms.
  - Disclosure chevron: 140ms.
  - All of it is off under `prefers-reduced-motion`.
  - No looping animation.
- **Accessibility:**
  - All text meets 4.5:1 (labels 11px use `--muted`, measured 5.8:1 on `--surface`).
  - Every coloured reading has a sign, word or number beside it.
  - Charts carry `role="img"` and an `aria-label` with the values.
  - Focus ring: 2px `--accent`.
  - Touch targets are 44px on coarse pointers.

---

## 6. Component list

| Component | Props |
|---|---|
| `StatusStrip` | `phase` (open, pre, after, closed), `weekend`, `pricesFrom` (date), `nextSession` (date, time), `quotesOk`, `quotesTotal`, `feedsOk`, `feedsTotal`, `readAt`, `market` (US, IN), `onMarket` |
| `VerdictHero` | `regime` (word), `stanceLabel`, `summary`, `asOf`, `children` |
| `DirectionGauge` | `score` (0-100), `bearMax` (35), `bullMin` (65) |
| `ConfidenceMeter` | `label`, `score`, `moderate` (50), `high` (70), `withheld` |
| `ExposureBand` | `low`, `high`, `cappedFrom`, `capReason` |
| `StanceBar` | `posture` (one of 7) |
| `HistoryStrip` | `sessions` (array of date and posture; max 10, missing shown empty) |
| `AgreementFlag` | `conflict` (bool), `bearish`, `bullish`, `total` |
| `FlipMeter` | `label`, `now`, `target`, `from`, `progress` (0-1), `note`, `met` |
| `WeakerLine` | `held`, `total`, `next` (array of label and distance) |
| `Briefing` | `title` (Week in review or Market in 3 sentences), `sentences` (3) |
| `PulseTile` | `label`, `symbol`, `price`, `dayChange`, `weekChange`, `unit` (pct, bp), `spark` (number array), `note`, `stale` |
| `RangeBand` | `min`, `max`, `band` (low, high, label), `session` (low, high), `markers` (array of value, label, position top or bottom, emphasis) |
| `StatCell` | `label`, `value`, `caption`, `split` (optional up percent) |
| `TrendChip` | `label`, `count` or `change`, `kind` (theme, ticker) |
| `HeadlineCard` | `title`, `source`, `age`, `impact`, `direction`, `kind`, `means`, `url` |
| `BreadthCounts` | `items` (label, above, total, marks) |
| `Treemap` | `cells` (label, symbol, weight, change, above50) |
| `MoverList` | `title`, `rows` (symbol, name, change), `scale` |
| `IdeaCard` | `rank`, `ticker`, `name`, `decision`, `thesis`, `entry`, `target`, `stop`, `size`, `rr`, `mandateFit`, `sectorTrend` |
| `IdeasEmpty` | `scanned` (bool), `watchlistCount`, `settings` (market, capital, mandate), `fits` (sectors, sizing, avoid) |
| `WeekTimeline` | `days` (date, count, events: symbol, timing, cap, highlighted), `macro` (array or empty), `scope` |
| `DetailRow` | `title`, `summary`, `open`, `children` |

---

## 7. Data mapping

**Existing fields**
- `/api/dashboard`:
  - `market.quotes[]`
  - `market.vix_level`
  - `market.participation`
  - `market.regime_history`
  - `market.regime_changes`
  - `market.market_regime_engine.{marketRegimeBase, posture, regimeScore, confidenceScore, confidenceLabel, portfolioGuidance.{band, cappedFrom, capReason}, marketHeadline, factors, newsImpact.{highImpactCount, bearishHighImpact, bullishHighImpact, keys}, dataFreshness.newsConflict, sourceHealth}`
  - `market_intel.{movers_up, movers_down, universe_size, earnings_calendar, earnings_week_count, earnings_scope, analyst_picks}`
  - `news.{articles, trends}`
  - `generated_at`
- `/api/market-context`:
  - `levels.{last, sma50, sma200, atr, expected_move, prior, session}`
  - `sectors[].{change_pct, above_sma50}`
  - `sector_participation`
  - `futures`
  - `cross_asset.reads`
  - `calendar`
- `/api/chart` (the detail chart).
- Picks scan result: `recommendations[]`.
- Thresholds (`/api/thresholds`): `MARKET.*`.

| Component | Fields |
|---|---|
| StatusStrip | `sourceHealth`, `quotes[0].quote_date`, `generated_at`; client `exchangeSession()` for phase |
| VerdictHero | `marketRegimeBase`, `posture`, `marketHeadline` (sentence), `regimeScore` (direction), `confidenceScore`/`Label`, `portfolioGuidance`, `regime_history`, `dataFreshness.newsConflict`, `newsImpact` |
| FlipMeter | `vix_level`, `participation.{above, measured}`, `levels.{last, sma50, sma200}`, `MARKET.{VIX_CONTAINED_MAX, VIX_STRESS, PARTICIPATION_*}` |
| Briefing | `quotes`, sectors, `earnings_week_count`, largest earnings by `market_cap`, VIX quote |
| PulseTile | `quotes[].{price, change_pct, quote_date}` |
| RangeBand / StatCell | `levels.expected_move`, `sma50`, `sma200`, `prior.close`, `session`; earnings by cap for the risk event |
| HeadlineCard | `articles` filtered by `newsImpact.keys`: `title`, `source`, `published`, `catalyst_label`, `direction`, `desk_action` (as "Means") |
| TrendChip (themes) | `news.trends[].{label, count}` |
| Treemap / BreadthCounts | `sectors`, `sector_participation`, `participation.members` |
| MoverList | `movers_up`, `movers_down`, `universe_size` |
| IdeaCard | `recommendations[].{ticker, company, decision, entry_price, price_target, stop, allocation, rr, sector}` (verify `thesis` and `stop` names at Phase 6) |
| WeekTimeline | `earnings_calendar[].{symbol, name, earnings_date, earnings_timing, market_cap}`, `market-context.calendar` |

**New fields needed (backend)**

| Field | Where | Source | Why |
|---|---|---|---|
| `quotes[].spark` (22 daily closes) | dashboard | One batched yfinance download, cached with the quotes | Sparklines without 9 chart calls |
| `quotes[].week_change_pct` | dashboard | Same download | Weekend "week" figures |
| `session.next_open` | dashboard | Exchange calendar with holidays | "Next session Mon 9:30 ET" |
| `levels.base_rate` (`up_pct`, `n`, `since`, `condition`; plus the all-days `up_pct`) | market-context | 10 years of SPY daily, conditioned on the last session's move bucket | The honest "probability" cell |
| `news.mentioned_tickers[]` (`symbol`, `count`, `change_pct`) | dashboard | Ticker extraction already in `_extract_trends`, plus quotes | Trending ticker chips |
| `sectors[].weight` | market-context | Static config of S&P 500 sector weights (quarterly) or SPY holdings | Treemap tile size |
| `briefing.sentences[3]` (optional) | dashboard | Server-composed from the fields above | Keeps copy testable in Python; client composition also works |

---

## 8. Implementation plan

Effort is rough, in model tokens per phase (read, edit, test, verify). Each phase ends green on Python, JS unit and e2e, with a 1440 and 390 screenshot check. I stop after each phase for your approval.

| Phase | Scope | Files | Effort |
|---|---|---|---|
| 0 | Decide what to keep from the working tree (question 1). Commit the backend fixes on their own. | git only | 5k |
| 1 | Tokens, shell and status strip. Verdict hero with gauge, confidence meter, exposure band, stance bar, history strip, agreement flag. FlipMeters. | `quanta.css`, `app.js`, `index.html`, tests | 80-110k |
| 2 | Pulse tiles with sparklines. Backend `spark` and `week_change_pct`. Briefing (week in review). | `app.py`, `app.js`, css, tests | 60-80k |
| 3 | Forecast panel. Backend `base_rate`. Risk event from the earnings calendar. | `app.py`, `app.js`, css, tests | 50-70k |
| 4 | Hot now: theme and ticker chips, headline cards. Backend `mentioned_tickers`. | `app.py`, `app.js`, css, tests | 50-60k |
| 5 | Sector treemap with weights config. Breadth counts. Movers. | `thresholds.py` or config, `app.js`, css, tests | 40-50k |
| 6 | Ideas cards and the designed empty state, wired to the Picks result. | `app.js`, css, tests | 50-70k |
| 7 | Week timeline. Collapsed detail rows. | `app.js`, css, tests | 30-40k |
| 8 | Mobile layout. Light theme pass. Axe. E2E updates. DESIGN.md update. Retire unused CSS. | all of the above | 60-80k |

Total: about 430-570k tokens.

---

## 9. Open questions

1. **Working tree.**
   - Keep the uncommitted backend fixes and build on them (recommended), or revert everything first?
   - The current Overview layout pass would be replaced either way.
2. **Type and labels.** Your brief asks for 11px uppercase tracked labels; DESIGN.md sets a 12px floor, sentence case and no tracked caps, and a test enforces it.
   - Approve updating DESIGN.md and that test?
   - Keep IBM Plex (current brand), or switch to Inter?
3. **Probability.** No calibrated forecast exists. Is a labelled historical base rate ("after a 1% down day, 53.8% of 273 sessions closed up") acceptable in place of a probability?
4. **Sector weights.** Use a static quarterly config (simple, slightly stale) or compute them from SPY holdings (live, one more provider call)?
5. **Ideas ranking.**
   - Order cards by QUANTA score alone, or score with a bonus for sectors above their 50-day average when the call is Defensive?
   - Should the mobile order put Ideas above Sectors, as drawn?

---

## What changed

- **Created:**
  - `docs/design/overview/IMPLEMENTATION.md`
  - `overview-mockup.html` (now revision 2)
  - `overview-mockup.jpg` (now revision 2)
  - `overview-mockup-mobile.jpg`
  - `overview-mockup-v1.html`
  - `overview-mockup-v1.jpg`
- **Modified for this proposal:** none. The source files listed in section 1 were modified earlier, before the stop; nothing was touched after it.
