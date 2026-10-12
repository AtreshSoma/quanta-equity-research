# Overview redesign (2026-10-09)

Source: the owner's trader/analyst review of the Overview page. Work is done in
priority order; each item names what was wrong and what the page does now.

## P1 Contradictions

| # | Was | Now |
|---|-----|-----|
| 1 | Regime card "Mildly Constructive", factor panel "Moderately Constructive" (two labels per posture in `app.py`) | One name everywhere: the regime title. The factor panel reads "55 out of 100: direction score for Mildly Constructive". |
| 2 | Confidence 29%, score 55/100 and "3/10 back the call" side by side with no legend | Regime card shows two labelled measures: **Direction** (factor sum, 0-100) and **Confidence** (how much the inputs agree and how fresh they are), with one sentence saying how they combine. |
| 3 | "Lean Long" with a 30-55% band and no reason | Playbook says the band is capped by low confidence and states the uncapped band. |
| 4 | Constructive regime and calm VIX next to "Elevated" risk | Risk card lists every reason for its level, and names what is calm. |
| 5 | Impact chain "Mixed tape: indices split" on an all-green tape | A tape where the indices move together is a broad advance or decline; "Mixed tape" only when they split. |
| 6 | "1 warning, 0 opposed" while Catalyst Risk scored -1 | Tally reads support / neutral / against; strength (-1 or -2) stays on the row. |
| 7 | Earnings 5 / Catalysts 5 / Earnings tab 1 | Counts say their universe: earnings this week in the tracked universe; the news tab is "Earnings news". |
| 8 | Every headline "High", a 1-day-old item above 1-hour-old ones | The tape shows category, not a constant "High"; headlines sort by relevance then recency. |

## P2 Units and labels

- VIX and yields are index levels: no currency symbol in the chart.
- 10Y yield change in basis points (ribbon and the Rates factor note).
- Crypto trades around the clock: no "regular session" label.
- VIX colour follows risk: a falling VIX reads as supportive.
- The chart's last-price tag never overlaps an axis label.

## P3 One number, one place

| Number | Lives in |
|---|---|
| Index levels and changes, VIX, 10Y | Ribbon |
| Index participation (2 of 4 benchmark ETFs above 50-DMA) | Participation & movers panel (and the factor row) |
| Direction score, confidence | Regime card |
| Earnings this week, high-impact headlines | Risk card |

## P4 Breadth, honestly named

The backend has no constituent breadth (no NYSE advance/decline, no % of S&P 500
members above the 50/200-DMA). The panel is renamed **Participation & movers**:
index participation as a fraction of benchmark ETFs, and the split of the
largest movers, each labelled for what it is. A single-sector "sector mix" is
not drawn.

## P5 Layout

```
tape
regime (direction + confidence) | playbook | risk
ribbon (prices only)
chart (S&P 500 by default)        | opportunities
                                  | participation & movers
market signal / drivers / catalysts | market intelligence
```

Every row's columns end on one line; nothing sits in a column of its own.

## P6 Opportunities

Each Street candidate shows price, mean target and analyst count, so the upside
can be judged; a gap wider than the configured check reads "check: wide gap".

## P7 Accessibility

One `h1` (the page). The regime name is an `h2`. Ribbon session text is short
enough not to truncate; the full line is in the shared note.

## Shipped (verification)

- `tests/unit/overview_redesign.test.cjs` (13 tests) covers each row above; the
  chart's index and inverse rules are in `live_chart.test.cjs`; the Rates note
  in basis points is in `test_market_regime.py`.
- Tests that pinned the old behaviour were changed on purpose: the regime's
  4-cell price strip, the ribbon's participation tile, the risk card's VIX and
  participation cells, and "Mixed tape" or "Risk-off rotation" on an all-green tape.
- 1440 and 1920 px: each row's columns end on one line; 390 px: no horizontal
  overflow; axe 0 violations in both themes; one `h1`.
- Opportunities rank plausible targets first; a gap at or above
  `MARKET.TARGET_GAP_CHECK_PCT` is listed after them with "check: wide gap".

## Phase 2: market context and the book (same day)

**Stale ribbon fixed.** Yahoo returns the current session's daily bar with a
blank close (ETFs). `_fetch_tape_quote` dropped it and printed yesterday's
close and change as today's (SPY 773.93 -0.42% "Previous close Oct 8" beside a
chart at 778.57). It now takes the session price from the response metadata or,
for a cached frame that kept the blank row, today's intraday bars
(`tests/test_tape_quote_session.py`). The risk card's earnings count is redrawn
when market intel arrives, so it no longer disagrees with Catalysts.

**New rows.**

| Row | Content | Source |
|---|---|---|
| Futures line | S&P, Nasdaq, Dow, Russell futures, only outside the regular session | `/api/market-context` |
| Your book against the call | Invested vs the band (gauge), today's change, holdings reporting this week (with timing), holdings among the largest movers | My Portfolio's cached view, GET only |
| What changed | Today's story (the impact chain, moved out of Market intelligence), posture, direction and factor changes since the last session, the recorded-session strip | `regime_changes`, `regime_history` (the engine's read is now stored daily) |
| What would change the call | Stronger and weaker read conditions against MARKET thresholds, met or not, with the live value | the model and the benchmark levels |
| Sectors | 11 sector funds (India: sector indices) by change, heat-coloured, a "50" mark when above the 50-day average; select one to chart it | `/api/market-context` |
| Levels and expected move | Expected move (VIX-implied, one session), prior and current session ranges on one axis, ATR, 50/200-day averages | `/api/market-context` |
| Cross-asset | Dollar index, 10Y less 3-month (bp), high-yield vs investment-grade, copper vs gold | `/api/market-context` |
| The week ahead | Seven days: Fed and BLS releases with times, tracked-universe earnings, the holdings' own reports with timing | official calendar, intel, the book |

All symbols live in `MARKET.SECTOR_BASKETS`, `CROSS_ASSET`, `FUTURES`, `VOL_INDEX`.
The context endpoint is loaded after the dashboard, cached for
`CONTEXT_CACHE_SECONDS`. An incomplete read is cached for only 30 seconds, and
the page re-reads it once.

Verification: `overview_context.test.cjs` (13), `test_market_context.py` (3),
`test_tape_quote_session.py` (3); axe 0 in both themes; keyboard: sector tiles
focus with a ring and chart on Enter; no overflow at 390, 1440, 1920.

## Phase 3: one order of questions (same day, owner review)

The book panel is removed from the Overview; its data still marks the holdings'
own reports on the calendar. The page now answers, in order:

```
1 the call            tape · regime | playbook | risk
2 the market          ribbon (+ futures) · chart | levels + cross-asset
3 where it moves      sectors | opportunities
4 what is coming      the week ahead | what would change the call
5 why                 what changed | participation & movers
6 the evidence        signal / drivers / catalysts | market intelligence
```

- **The week ahead:** a "Next up" banner with a countdown, date tiles (weekday,
  large date, month), colour-coded event chips (macro releases with their ET
  time, tracked earnings, the reader's holdings marked "You hold"), hatched
  weekends, and a legend. Day cards fill the row's height.
- **Empty context panels:** a server started before `/api/market-context`
  answers 404. Each panel now says to restart QUANTA, rather than showing three
  blank boxes. A cold read that misses the time budget is re-read after 30 s.
- **Dead space:** each row's panels end within the panel padding at 1440 and
  1920 px (measured: 21-70 px from the last text to the panel edge).

## Not built (needs data the backend does not have)

Futures and pre-market, DXY, 2s10s, credit spreads, NYSE internals, sector ETF
heatmap, timed economic calendar. Each needs a new provider feed.

## Phase 4: clarity pass (owner review, 2026-10-09)

| Section | Was | Now |
|---|---|---|
| The week ahead | Earnings came only from the intel screen, capped at 10 rows; a partial screen showed none in a heavy bank week | Yahoo's earnings calendar (US companies over `MARKET.EARNINGS_MIN_MARKET_CAP`) completes the list (`_earnings_calendar`, merged in `_build_market_intel`, cached `EARNINGS_CALENDAR_CACHE_SECONDS`). Each day lists the `EARNINGS_PER_DAY_SHOWN` largest reports with timing and EPS estimate, a "+N more reports" disclosure, and a report count under the date. After the close, nothing from today is "Next up". |
| What changed | A one-word story, dots, "Rates turned neutral" in red, an undated strip | Three labelled parts: **How prices moved today** (one bar per index with its %, checks as pass chips, "N of N checks agree with today's prices"); **What the model changed since {date}** (Measure / Was / Now / Stronger or Weaker table); **The model's call, last N sessions** (dated axis, latest outlined, a plain count caption). |
| Participation & movers | Fractions without names, a sector sentence about one tagged mover | Renamed **Uptrends & movers**. Index funds by name, each in an uptrend or below its 50-day average. Sector funds as an 11-segment meter with the names in uptrends. The five largest gainers and losers as bars. Header verdict: Broad / Mixed / Narrow from the share in uptrends (`PARTICIPATION_BROAD_PCT` / `WEAK_PCT`). |
| What would change the call | Count printed twice; "Benchmark"; "Index participation at 70%" | The index named ("S&P 500 above its 50-day average"); participation as fund counts ("3 of 4 index funds in uptrends or more"); a meter per side; a Direction score gauge with the bearish and bullish zones from `SIGNAL_SCORE_BEAR` / `BULL`. |
| Cross-asset | Raw pair ratios (copper/gold 0.0016); curve "positive" | Pair rows show the read as a chip ("Risk appetite") and today's change, with the ratio in the tooltip. Each gauge carries a one-line meaning. The curve reads "not inverted". |
| Risk card / Catalysts | Earnings count capped by the 10-row table | `earnings_week_count` counts every report still ahead; the scope is named ("large caps and tracked names"); Catalysts reads "8 of 33 scheduled". |

Backend payload additions: `market_intel.earnings_calendar`, `earnings_week_count`, `earnings_scope`; `market.participation.members[{symbol,label,above}]`; `regime_changes[].label/from/to`; `cross_asset.reads[].note`. Tests set `QUANTA_EARNINGS_CALENDAR=0` (conftest), so the suite never calls Yahoo.

Verification: `npm test` 692 pass; pytest 1911 pass; ruff clean; axe 0 in both themes; no overflow at 390 or 1440.

## Phase 5: earnings on the running server, and India (2026-10-10)

- **"Only 1 company" in the week.** The owner's server started at 21:25 and the earnings calendar landed at 21:42: the static page was new, the Python backend was not. A restart loads it (checked: the server's own Python 3.12 / yfinance 1.2 returns 44 large caps). The week now says so itself: an intel payload without `earnings_scope` shows "Restart QUANTA (start.ps1) to load it", and `earnings_calendar_status: "unavailable"` says Yahoo did not answer.
- **India sectors.** Yahoo serves 8 of the 11 NSE sector indices (`^CNXAUTO`, `^CNXFMCG`, ...) with a one-row daily series, so they had no change and no 50-day read. `_daily_closes_from_hourly` rebuilds daily closes from hourly bars for both the quote's previous close and the trend read: 11 of 11 now.
- **India "What changed".** The story read only SPY/QQQ/IWM, so an all-green Nifty day read "Mixed tape, 0 of 3". `indexChain` uses the market's own indices and volatility index: "Broad advance, led by Nifty IT".
- **Overlaps.** The ribbon's tiles had `min-width: 0` and collided on a phone; they keep 112-124px and the row scrolls. NSE symbols lose `.NS` in movers and the week, and the mover bars share one grid so long tickers no longer run into them. Index chips drop raw `^NSEI` codes. The ribbon footnote is padded inside the panel.
- **Wording.** India's week no longer claims Eastern time or Fed/BLS releases it does not show. Its triggers count "indices", not "index funds". All-caps company names read normally ("HCL Technologies Ltd"). A factor change that keeps its word shows the score ("Headwind (-2) → Headwind (-1)"). The risk card's earnings count trusts the scan's list once it has rows.
- **Contrast.** Full-heat sector tiles (India IT +3%) dropped white text below 4.5:1. The tint now stops at 36%.

Verification: `npm test` 697; pytest 1912; ruff clean; axe 0 for US and India in both themes; no overflow at 390 or 1440.

## Phase 6: a desk, not a wall of cards (2026-10-10, design critique)

The owner's critique asked whether the page read as AI-generated. It scored 5/10 on the slop rubric: identical rounded cards, a narrating subtitle on every panel, monospace and pills everywhere, "·" strings, and the same numbers repeated (and disagreeing). The reference the owner shared (an agency landing page) was used for its typographic confidence, flat ruled sections and single accent, not its marketing theatrics.

| Was | Now |
|---|---|
| Earnings 35 / 33 / 10 / 8 in four places | One window (`_earnings_ahead`: today through the calendar's last day) for the risk card, the week, the Catalysts tab and the Catalyst Risk factor |
| Direction score printed 4 times; "Elevated" and "30-55% gross" twice each | Each figure once: the risk level and the band are the panel heads' figures; the factor table no longer repeats the score |
| "Why this call" / "What changes it" / "Risk triggers" disclosures | Removed; they restated the factor table and the triggers panel. One link jumps to the factors |
| 14 identical cards | The call, the chart and the week keep a surface; every other section is a ruled block (`.q-flat`) |
| A subtitle under every heading | Removed (the opportunities line stays: it says these are not QUANTA setups) |
| Playfair on the regime title only | Plex Sans 650, tight tracking; a direction zone bar beside the confidence gauge |
| Mono for prose, pills for status | Mono only for figures in columns; status as coloured words and square checks |
| "Avoid" in red, upside in violet, counts in yellow | Green/red only for price direction; neutral text otherwise |
| "50" badges on sector tiles | A green top edge means above the 50-day average; the last tile spans the empty cells |
| Ribbon: 5 lines per tile, the same session date 8 times | The shared session is stated once; a tile names its own only when it differs (crypto); futures form a band under the ribbon |
| Flat after-hours line and a "+0.00" after-hours quote | The axis stops 30 minutes after the last move; a zero after-hours change is not printed |
| Levels ruler 778.53 vs ribbon 778.57 | The ruler uses the ribbon's price |
| Empty weekends at full width | Narrow "Closed" columns |
| Sub-12px text in the Overview blocks | Lifted to the 12px floor token |
| Italic "·Elite" | Upright |

Verification: `npm test` 698; pytest 1912; ruff clean; axe 0 for US and India in both themes; keyboard: the factor link has a 2px focus ring, is 27px tall and jumps to the table.

## Phase 7: the call as instruments, a compact week (2026-10-10)

**Market regime row (design critique).** The call was correct but inert: text and two thin bars, confidence stated three times, no sense of time. Now:

- **Direction dial.** A half-circle 0-100 with the bearish and bullish zones from `MARKET.SIGNAL_SCORE_BEAR/BULL`. The needle sweeps from the bearish end on first view and turns from the last reading to the new one when the score changes.
- **Factor balance.** The ten factors as one strip, strongest support first (green, grey, red), drawn in once.
- **Posture age.** From `regime_history`: "Neutral since Oct 7, 3 sessions", or "Lean Defensive since today, from Defensive" in the accent colour when the stance just changed.
- **Posture scale (playbook).** The seven postures, Defensive to Offensive, with today's step marked and each step's base gross band in its tooltip. The marker slides when the stance moves.
- **Risk meter.** Contained, Moderate, Elevated, High, lit up to today's level.

**The week ahead, about half the height.**
- A bar chart of reports per day grows in once when it scrolls into view.
- One line per company: a sun (before the open) or moon (after the close), the ticker and the EPS estimate. Each line opens the company's tear sheet.
- The "Next up" banner has a live countdown ("in 1 d 22 h", updated each minute), computed in the market's time zone, with a pulsing marker.

**Consistency fixes.**
- The scan's earnings rows take timing, estimate and size from Yahoo's calendar when it lists the same report.
- Earnings days count from the market's own date, so India no longer reads 5 in the Risk card against 8 in its week.
- The factor table's count sits beside its tally.
- The chart's instrument name stays on one line.

**Motion rules.** WAAPI only, using the transform, opacity and scale properties, with strong ease-out and ease-in-out curves. A reading animates only when it changed since the last render, so refreshes don't replay. Under reduced motion nothing moves and the pulse is off. Measured: the needle went 66° → 95° in about 0.5 s with motion and stayed at 95° under reduced motion; the bars grew in, staggered, on first scroll.

Verification: `npm test` 701; pytest 1914; axe 0 for US and India in both themes; no overflow at 390 or 1440.

## Phase 8: density (2026-10-10, owner review)

The owner's screenshots at about 1870 px showed wide, half-empty blocks. The causes and fixes:

- **The call row had forced heights.** `min-height: 336px` on the row and 290 px per panel. Removed; every row sizes to its content.
- **"What would change the call" stood beside the week, mostly empty.** It now sits in the call row as a fourth column: regime | playbook | risk | what would change it. Between 1101 and 1499 px the row is a 2×2 grid; on a phone it is one column.
- **The week ahead takes the full row and lists every report.** Each company is a chip (sun or moon for timing, the ticker; estimate and company in the tooltip and read aloud), so nothing is folded behind "+N more". The busiest-day bars stay. Bars and chips arrive in a short cascade, once, when the week scrolls into view.
- **Opportunities read on one line each** at 1500 px and wider (rank, name, rating, price to target, analysts, upside), so the row matches the sector map.
- **Redundant blocks removed.**
  - What changed's index bars repeated the ribbon.
  - The Uptrends sector meter repeated the Sectors map and its count.
  - The regime's "Strategy" line repeated the playbook.
  - The low-confidence reason now sits under the confidence gauge.

Verification: `npm test` 701; axe 0 for US and India in both themes; no overflow at 390, 1440 or 1872.

## Phase 9: where it moves, and render cost (2026-10-10)

- **Sectors** are a ranked diverging bar chart: strongest first, each move from one zero line, the 50-day trend as a filled or hollow dot. Select a row to chart it. The bars grow in once when the row scrolls into view.
- **Where the opportunities are** lists five names, each with an upside bar scaled to the largest, so the two columns end level.
- **The week ahead** is a flat agenda: no day cards, hairline dividers, today marked by a top rule, "Next up" as a plain line.
- **Rendering.**
  - Below-the-fold rows use `content-visibility: auto`.
  - `renderMarketContext` skips a repaint when its data is unchanged.
  - Measured at 1600 px, warm, three runs:

    | Measure | Before | After |
    |---|---|---|
    | Layout | 250-340 ms | 170-200 ms |
    | Style recalculation | 69-90 ms | 39-47 ms |
    | Longest task | 119-133 ms | 71-86 ms |
    | Context re-render | 12-21 ms | 5-10 ms |


## Publication review (2026-10-10)

The context repaint signature now includes market quote values, calendar contents and book data, so changed values repaint even when source timestamps and earnings counts do not change. A regression test proves both the changed-quote update and the unchanged-input skip.

Offline Python: 1,944 passed, 5 skipped, 14 expected failures; final focused scan-console and market-context tests: 14 passed; Python lint clean. Overview Chromium on synthetic data: zero accessibility violations in both themes and no overflow at 390, 1440 or 1872. Activity: 68 browser tests passed. See [Activity reload evidence](activity-redesign/FILTER_RELOAD_FIX.md).
