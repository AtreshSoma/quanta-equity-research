# Picks design brief (revamp, 2026-10-06)

1. Scene: one owner at a desktop, day and evening, reading a trading workstation. Panels tile edge to edge on 1 px hairlines, with no card inside a card and no blank cells. Light and dark both use the app tokens.
2. Shell: Scan Setup stays a compact sticky sidebar beside the report on wide screens (owner, 2026-10-07); only where the layout stacks (1080 px and below) does it fold after a run to a 40 px command bar (summary + Edit). The scan menu shows short labels; the full description sits in the hint under it. Progress is a stage rail, and its first stage shows the selection sentence (source, kept, rule, fallback) before analysis starts.
3. Report frame: flat. One header strip (count line, run meta, actions), then a tab rail, then panels that share borders (a 1 px grid gap over the line colour) instead of floating cards.
4. Overview: one quote strip of five equal cells (Buy and Accumulate, Watchlist, Scanned, Suggested deployment, Regime). Each cell has a label, a figure and one sub-line, so it fills. Below it sit one filter row, then the ranked table.
5. Calls are told apart by weight, never colour: Buy is solid ink, Accumulate is ink outline, Watchlist is a muted dashed outline. "Model not yet validated" sits beside the calls.
6. Colour roles: green for gain and red for loss, nothing else. Amber means warning or pending; blue is a neutral measure (bars, averages, entry zone). Regime, risk level and analyst counts are plain text.
7. Type: sans for labels and prose (11 / 13 px), mono tabular figures for every number (13 / 15 / 20 px), sentence case, and a tooltip on each piece of jargon (R:R, ATR, RSI, DCF).
8. Spacing: 4 / 8 / 12 / 16 px only. Section gap is at most 12 px, panel padding 10 to 12 px, table rows at most two text lines.
9. Analysis: key strip; chart (2 fr) beside plan and checks (1 fr); reasons; then three equal panels. Check status (Holding, Pending) is a dot plus a word, never button-shaped.
10. Motion: one moment, the report settling in 160 ms after a scan. Tabs switch instantly; press feedback starts on pointer-down; reduced motion is honoured.
11. Missing data gets one muted line and no empty box. Each value appears once per view.
12. Gates (Playwright, 1280 / 1440 / 390 in both themes): each panel ≥ 85 % filled, side-by-side panels within 15 % height, no stray scrollbars or blank columns, axe 0.
