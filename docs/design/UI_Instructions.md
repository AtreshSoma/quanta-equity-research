You are a senior frontend engineer and visual systems architect.
Perform a full production rebuild of the QUANTA-Elite trading dashboard.
Stack: React + Tailwind CSS + CSS custom properties.
Apply every section below completely. Nothing is optional.

---

## PART 1: DESIGN SYSTEM — DUAL THEME WITH LIQUID GLASS

### 1.1 Theme Architecture

Implement two themes: DARK (default) and LIGHT.
Toggle via a .theme-dark / .theme-light class on `<html>`.
All colors, glass surfaces, and gradients must be driven
entirely by CSS custom properties — zero hardcoded hex values in components.

Define these token layers:

:root {
  /* Backgrounds */
  --bg-base:           #0a0f1e;   /* dark deep navy */
  --bg-surface:        #0d1424;
  --bg-elevated:       #111827;

  /* Glass */
  --glass-bg:          rgba(255, 255, 255, 0.04);
  --glass-bg-hover:    rgba(255, 255, 255, 0.07);
  --glass-border:      rgba(255, 255, 255, 0.10);
  --glass-border-glow: rgba(255, 255, 255, 0.18);
  --glass-shadow:      0 8px 32px rgba(0, 0, 0, 0.45),
                       0 1px 0px rgba(255,255,255,0.06) inset;
  --glass-blur:        blur(24px) saturate(180%);

  /* Gradient Mesh (dark) */
  --grad-primary:   linear-gradient(135deg, #1a2a4a 0%, #0a0f1e 60%, #0d1e3a 100%);
  --grad-accent-1:  radial-gradient(ellipse at 20% 20%, rgba(56,139,255,0.18) 0%, transparent 60%);
  --grad-accent-2:  radial-gradient(ellipse at 80% 80%, rgba(99,60,180,0.14) 0%, transparent 60%);
  --grad-accent-3:  radial-gradient(ellipse at 60% 10%, rgba(0,210,180,0.08) 0%, transparent 50%);

  /* Liquid Glass Highlight */
  --liquid-top:        linear-gradient(180deg,
                         rgba(255,255,255,0.13) 0%,
                         rgba(255,255,255,0.03) 40%,
                         transparent 100%);
  --liquid-specular:   radial-gradient(ellipse at 50% 0%,
                         rgba(255,255,255,0.18) 0%,
                         transparent 65%);

  /* Text */
  --text-primary:   #f0f4ff;
  --text-secondary: #94a3b8;
  --text-muted:     #4b5a72;
  --text-accent:    #60a5fa;

  /* Signal colors */
  --signal-bull:    #22d3a0;
  --signal-bear:    #f87171;
  --signal-neutral: #94a3b8;
  --signal-warn:    #fbbf24;

  /* Borders */
  --border-subtle:  rgba(255,255,255,0.07);
  --border-mid:     rgba(255,255,255,0.12);
  --border-bright:  rgba(255,255,255,0.22);
}

.theme-light {
  --bg-base:           #e8eef8;
  --bg-surface:        #dde5f3;
  --bg-elevated:       #cdd8ef;

  --glass-bg:          rgba(255, 255, 255, 0.45);
  --glass-bg-hover:    rgba(255, 255, 255, 0.58);
  --glass-border:      rgba(255, 255, 255, 0.72);
  --glass-border-glow: rgba(255, 255, 255, 0.90);
  --glass-shadow:      0 8px 32px rgba(100, 130, 200, 0.18),
                       0 1px 0px rgba(255,255,255,0.85) inset;

  --grad-primary:   linear-gradient(135deg, #c8d8f0 0%, #e8eef8 60%, #d0ddf2 100%);
  --grad-accent-1:  radial-gradient(ellipse at 20% 20%, rgba(96,165,250,0.22) 0%, transparent 60%);
  --grad-accent-2:  radial-gradient(ellipse at 80% 80%, rgba(139,92,246,0.14) 0%, transparent 60%);
  --grad-accent-3:  radial-gradient(ellipse at 60% 10%, rgba(34,211,160,0.10) 0%, transparent 50%);

  --liquid-top:     linear-gradient(180deg,
                      rgba(255,255,255,0.75) 0%,
                      rgba(255,255,255,0.25) 40%,
                      transparent 100%);
  --liquid-specular:radial-gradient(ellipse at 50% 0%,
                      rgba(255,255,255,0.90) 0%,
                      transparent 65%);

  --text-primary:   #0f172a;
  --text-secondary: #334155;
  --text-muted:     #94a3b8;
  --text-accent:    #2563eb;

  --border-subtle:  rgba(148,163,184,0.25);
  --border-mid:     rgba(148,163,184,0.45);
  --border-bright:  rgba(148,163,184,0.70);
}

### 1.2 Page Background — Layered Gradient Mesh

The page background is NOT a flat color.
Build it as a fixed full-viewport layered composition:

<div class="bg-mesh">
  <!-- Base gradient -->
  <div class="bg-mesh__base" />
  <!-- Floating color orbs — CSS animated, slow drift -->
  <div class="bg-mesh__orb bg-mesh__orb--blue" />
  <div class="bg-mesh__orb bg-mesh__orb--violet" />
  <div class="bg-mesh__orb bg-mesh__orb--teal" />
</div>

CSS:
.bg-mesh {
  position: fixed; inset: 0; z-index: 0; overflow: hidden;
  background: var(--grad-primary);
}
.bg-mesh__base {
  position: absolute; inset: 0;
  background:
    var(--grad-accent-1),
    var(--grad-accent-2),
    var(--grad-accent-3);
}
.bg-mesh__orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.5;
  animation: orbDrift 18s ease-in-out infinite alternate;
}
.bg-mesh__orb--blue {
  width: 600px; height: 600px; top: -100px; left: -100px;
  background: radial-gradient(circle, rgba(56,139,255,0.25), transparent 70%);
  animation-delay: 0s;
}
.bg-mesh__orb--violet {
  width: 500px; height: 500px; bottom: -80px; right: -80px;
  background: radial-gradient(circle, rgba(139,92,246,0.20), transparent 70%);
  animation-delay: -6s;
}
.bg-mesh__orb--teal {
  width: 400px; height: 400px; top: 40%; left: 50%;
  background: radial-gradient(circle, rgba(34,211,160,0.12), transparent 70%);
  animation-delay: -12s;
}
@keyframes orbDrift {
  0%   { transform: translate(0, 0) scale(1); }
  33%  { transform: translate(40px, -30px) scale(1.05); }
  66%  { transform: translate(-20px, 50px) scale(0.97); }
  100% { transform: translate(30px, 20px) scale(1.03); }
}

### 1.3 Liquid Glass Card System

Every card/panel uses this glass treatment.
Create a reusable GlassCard component:

.glass-card {
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: 16px;
  box-shadow: var(--glass-shadow);
  overflow: hidden;
  transition: background 0.2s ease, border-color 0.2s ease,
              box-shadow 0.2s ease;
}

/* Apple Liquid Glass top highlight — the specular edge */
.glass-card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--liquid-top);
  pointer-events: none;
  z-index: 1;
}

/* Specular hotspot — catches ambient light at top center */
.glass-card::after {
  content: '';
  position: absolute;
  top: 0; left: 50%;
  transform: translateX(-50%);
  width: 70%; height: 40px;
  background: var(--liquid-specular);
  pointer-events: none;
  z-index: 2;
  border-radius: 0 0 50% 50%;
}

/* All card content above pseudo layers */
.glass-card > * { position: relative; z-index: 3; }

/* Hover state — glass brightens */
.glass-card:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border-glow);
  box-shadow:
    var(--glass-shadow),
    0 0 0 1px rgba(255,255,255,0.06);
}

/* Variant: elevated (modals, tooltips) */
.glass-card--elevated {
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  background: rgba(255,255,255,0.08);
  border-color: var(--glass-border-glow);
}

/* Variant: signal-tinted (regime card) */
.glass-card--bull {
  border-color: rgba(34,211,160,0.30);
  background: rgba(34,211,160,0.05);
}
.glass-card--bear {
  border-color: rgba(248,113,113,0.30);
  background: rgba(248,113,113,0.05);
}

### 1.4 Theme Toggle Button

Position: fixed top-right in the navbar.
Use a pill toggle: sun icon ↔ moon icon.
On click: toggle .theme-light on `<html>`, persist to localStorage.
Animate the toggle with a 200ms ease transition.
The entire page repaints via CSS variables — no component re-render needed.

---

## PART 2: LAYOUT RESTRUCTURE

### 2.1 Canonical Grid

<div class="bg-mesh" />
<div class="app-shell">
  <Navbar />
  <LiveTicker />
  <main class="dashboard-grid">
    <div class="col-left">
      <MarketPulseHeader />
      <TradingActionCard />
      <MarketBriefCard />
      <TopMoversCard />
      <TooLateVetoesCard />
      <AllocatorPlaybookCard />
    </div>
    <div class="col-right">
      <RegimeModelCard />
      <ActionCenterCard />
      <CatalystWatchCard />
      <EarningsMacroCard />
      <SourceLedgerCard />
    </div>
  </main>
  <Footer />
</div>

CSS:
.app-shell {
  position: relative; z-index: 1;
  min-height: 100vh;
  display: flex; flex-direction: column;
}
.dashboard-grid {
  display: grid;
  grid-template-columns: 1fr 420px;
  gap: 16px;
  padding: 16px 24px 80px;
  flex: 1;
}
.col-left, .col-right {
  display: flex; flex-direction: column; gap: 16px;
}
@media (max-width: 1024px) {
  .dashboard-grid { grid-template-columns: 1fr; }
}

### 2.2 Navbar

Glass pill navbar: full width, backdrop-filter blur.
Left: QUANTA-Elite logo + TRADING COMMAND CENTER label.
Center: region flags (US / India toggles).
Nav tabs: Dashboard, Portfolio Builder, Stock Deep-Dive —
  active tab = glass pill with border glow.
Right: Live clock (ET), "Updated Xm ago" pulse dot,
  theme toggle, Refresh button.
Height: 52px. Border-bottom: 1px solid var(--glass-border).

### 2.3 Live Ticker

Full-width strip below navbar.
Height: 36px. Glass background, border-bottom.
CSS marquee animation (transform: translateX), NOT JS scroll.
Pause on tab hidden (visibilitychange listener).
Show: asset name, price, delta with color coding.
On load fail: collapse to 0px height with smooth transition —
  do NOT show "Loading live ticker..." text.

### 2.4 Footer

<footer class="app-footer glass-card" 
  style="position:sticky; bottom:0; z-index:50; border-radius:0;
         border-top:1px solid var(--glass-border); padding:10px 24px;">
  QUANTA Elite Trading System v2.0 · Educational and informational use only ·
  Not investment advice.
</footer>
NEVER render this inside the scrollable content column.

---

## PART 3: SECTION-BY-SECTION CONTENT FIXES

### 3.1 Market Pulse Header Card

- Headline: "Constructive Market" — 32px, font-weight 700, var(--text-primary)
- Sub-label: "Most signals are positive, but use stops and avoid over-concentration."
- 3 metadata pills inline: Trading Posture / Model Signal / Meaning
  Each pill: glass-card variant, 10px label, 12px value.
- Right block: 4-Factor Regime Model tiles (see 3.6).
- Signal status dot (green pulsing) left of "MARKET PULSE" section label.

### 3.2 Trading Action Card

- Badge "SELECTIVE LONGS ONLY" — pill, var(--signal-bull) background,
  white text, 11px, font-semibold, letter-spacing 0.08em.
- Section label: TODAY'S TRADING ACTION (muted, 10px, uppercase).
- Primary action headline: "Look for confirmed breakouts only."
  24px, font-weight 700. Never truncate.
- 4-column sub-table: AVOID / RISK RULE / POSITIONING / RECOMMENDED POSTURE.
  Table uses CSS grid: grid-template-columns: repeat(4, 1fr); gap: 12px.
  Each cell: glass-card inner variant, min-height 72px, padding 10px 12px.
  Label row: 9px uppercase muted. Value row: 13px var(--text-primary),
  line-height 1.5, allow full 3-line wrap, NEVER clip mid-word.

### 3.3 Market Brief Card (MERGED — no duplication)

- Remove the separate "Desk Brief" card. ONE card titled "Market Brief".
- 3-column inner grid: DESK PLAYBOOK / RISK NOTES / EVENT WATCH.
  Each column: inner glass panel, full text visible, no truncation.
  align-items: stretch so all 3 columns match the tallest.
- Metric row below: VIX / BREADTH / 10Y / EARNINGS 7D / HIGH-IMPACT.
  Use overflow-x: auto with hidden scrollbar for narrow viewports.
  All 5 metrics always fully visible at 1280px+.
- "27 high-impact catalysts · 2 earnings on tap" — bottom label,
  11px, var(--text-muted).

### 3.4 Top Movers Card

- Header: section label "BREADTH" (muted, 10px, uppercase) + "Top Movers" title.
- Loading state: 5 skeleton rows, shimmer animation, each row
  mimics: ticker chip | price | delta | volume bar.
- Success: render movers table.
- Error: glass empty state panel —
  icon (activity) + "Market data temporarily unavailable" +
  "Retry" button (glass pill, var(--text-accent) border).
- REMOVE the "FETCHING" or "LOADINGOU2026" raw text entirely.
  Replace with a 14px spinner (animate-spin, 2px border-t,
  var(--text-accent) color) inside the header right slot.

### 3.5 Portfolio Scan — Modal/Drawer Only

- The scan result block must NEVER render inline in the page flow.
- Render as a slide-over drawer from the right:
  width: 480px; height: 100vh; position: fixed; right: 0; top: 0;
  glass-card--elevated treatment; backdrop behind it:
  rgba(0,0,0,0.4) with blur(4px).
- Drawer content: scan status, result list, action CTAs.
- Dismiss: ESC key or click backdrop or close button (×, top-right).
- "0 SCANNING" badge fix:
  - Idle state: no badge.
  - Scanning: animated "Scanning..." pill with pulse dot.
  - Done: "X setups found" badge (green) or "No setups" (muted).

### 3.6 4-Factor Regime Model Card

- Title: "4-FACTOR REGIME MODEL" (10px, uppercase, muted label).
- 4 tiles in 2×2 grid: Trend / Volatility / Breadth / Credit+Rates.
  Each tile: glass inner panel, icon (16px lucide), label, value.
- Icon and value color by signal:
  Positive = var(--signal-bull), Neutral = var(--signal-neutral),
  Negative = var(--signal-bear).
- No mixing of icon styles — use lucide-react exclusively:
  TrendingUp, Activity, BarChart2, Landmark.

### 3.7 Action Center Card (NEW — replaces floating CTAs)

- Glass card in right column, below Regime Model.
- Title: "ACTION CENTER" (section label style).
- 3 full-width CTA rows, each:
  glass inner row, icon left (14px), label center,
  action button right (glass pill, 11px).
  Rows: Run Portfolio Scan / Build Tear Sheet / Review Event Risk.
- Each row has a one-line descriptor in muted text below the label.

### 3.8 Catalyst Watch / Market News Card

- Header: "CATALYST WATCH" label + "Market News" title.
- Badge: "27 ARTICLES" — glass pill, 10px, var(--text-accent).
- Tab bar: Macro / Politics / Earnings / Stocks / Deals with counts.
  Active tab: glass pill, border var(--glass-border-glow).
- MOST-MENTIONED TICKERS section:
  Each ticker chip: bg var(--glass-bg), border var(--glass-border),
  text var(--text-primary), 11px, px-2 py-0.5, rounded-md.
  NEVER render as black blobs — the text must always contrast.
- MACRO THEMES tag cloud: same chip treatment with tinted variants per theme.
- News item cards:
  Source badge (colored by source), impact badge,
  headline (13px, semibold, 2-line clamp),
  source + time + category (10px, muted).
- Tag color map (enforce strictly):
  MACRO CATALYST:      bg rgba(59,130,246,0.15) border rgba(59,130,246,0.35) text #93c5fd
  POLICY/GEOPOLITICS:  bg rgba(139,92,246,0.15) border rgba(139,92,246,0.35) text #c4b5fd
  DEAL FLOW:           bg rgba(34,197,94,0.15)  border rgba(34,197,94,0.35)  text #86efac
  EARNINGS READ-THROUGH: bg rgba(251,191,36,0.15) border rgba(251,191,36,0.35) text #fde68a
- Impact badge:
  HIGH:   bg rgba(239,68,68,0.15)  border rgba(239,68,68,0.30)  text #fca5a5
  MEDIUM: bg rgba(251,191,36,0.15) border rgba(251,191,36,0.30) text #fde68a
  LOW:    bg rgba(100,116,139,0.15) border rgba(100,116,139,0.30) text #94a3b8

### 3.9 Earnings & Macro Card

- Label: "EVENT RISK" (muted, 10px, uppercase).
- Title: "Earnings & Macro".
- "7-DAY WINDOW" badge: glass pill, var(--signal-warn) border.
- Empty state when no events:
  glass inner panel bg var(--glass-bg),
  text var(--text-muted) 12px, centered, min-height 64px.
  Text: "No near-term earnings risk. No tracked events in the next 7 days."
  NOT a near-black box on dark background — always readable.

### 3.10 Source Ledger Card

- Header: "RESEARCH STACK" label + "Source Ledger" title.
- Status summary: "X/8 sources active" badge.
  - 7–8 active: green dot.
  - 4–6 active: amber dot.
  - < 4 active: red dot.
- "27 articles · updated 2m ago" — secondary header detail,
  11px muted. NEVER truncate "Updated Xm ago".
- Per source row:
  Status dot (green=active, amber=slow, gray=no response) +
  Source name (13px) + "last checked Xm ago" (10px muted) +
  Count badge (glass pill, right-aligned).
  - Count 0 with active feed: muted badge, 50% opacity.
  - Feed unresponsive (Barron's etc): amber dot + "Unavailable" +
    retry icon button (↺ 12px, var(--text-accent)).
  - Count 10+: var(--signal-bull) tinted badge.
- Distinguish clearly: gray dot = inactive/unavailable,
  green dot + 0 = active but no matching articles today.

### 3.11 Too Late / Vetoes Card

- Section label: "ENTRY DISCIPLINE".
- Title: "Too Late / Vetoes".
- Empty state (no scan run yet):
  Glass inner panel, centered layout:
  Icon (ShieldX, 20px, var(--text-muted)) +
  "No vetoes or late entries detected" (13px, var(--text-secondary)) +
  "Run a portfolio scan to populate this feed." (11px, var(--text-muted)) +
  CTA button "Run Portfolio Scan" (glass pill, var(--text-accent) border, 12px).

### 3.12 Allocator Playbook Card

- Section label: "CAPITAL DESK".
- "RISK-FIRST" badge: right-aligned header, glass pill,
  var(--signal-warn) border.
- 4-column grid: CURRENT MANDATE / EXPOSURE / WHAT TO FAVOR / WHAT TO AVOID.
  All 4 headers fully visible — no truncation.
  Use grid-template-columns: repeat(4, 1fr); at min 240px per col,
  overflow-x: auto on the grid wrapper for narrow screens.
  Each cell: inner glass panel, label 9px uppercase muted,
  value 12px var(--text-primary), 2-line wrap allowed.
- Footer stat row: Breadth 56% / VIX 17.0 / 24 high-impact headlines /
  0 earnings events — pill stats, 10px, var(--text-muted).

---

## PART 4: TIMESTAMP CONSISTENCY

Every "Updated Xm ago" string must:

- Use white-space: nowrap — never clip.
- Update in real-time via a shared timestamp utility:
  formatRelativeTime(ts: Date): string — returns "Xm ago", "Xh ago", "Just now".
- Pulse green dot left of text when updated < 2m ago.
- Amber dot when > 5m ago (stale).
- Red dot when > 15m ago (feed warning).
  Apply this pattern consistently to: Source Ledger header,
  Market Brief header, Catalyst Watch header, Navbar "Updated" indicator.

---

## PART 5: PERFORMANCE ARCHITECTURE

### 5.1 Concurrent Data Fetching

On mount, fire ALL data fetches in parallel:
const [ticker, regime, movers, news, ledger, earnings, scan] =
  await Promise.allSettled([
    fetchTicker(), fetchRegime(), fetchMovers(),
    fetchNews(), fetchLedger(), fetchEarnings(), fetchScanStatus()
  ]);
Each result handled independently.
One failure never blocks or delays other cards.

### 5.2 Independent Card State

Each card: local useState({ status: 'loading' | 'success' | 'error', data }).
Cards never share loading state.
Cards never re-render due to sibling state changes.
All card components wrapped in React.memo().
All handlers wrapped in useCallback().
All derived data wrapped in useMemo().

### 5.3 Polling Manager (Context)

Single PollingContext provides:

- registerFeed(key, fetchFn, intervalMs): registers a feed
- unregisterFeed(key): clears interval
  Intervals: Ticker 30s, Regime 60s, Movers 90s, News 120s, Ledger 120s.
  All intervals cleared on unmount via useEffect cleanup.
  No component manages its own setInterval.

### 5.4 Ticker Performance

CSS-only marquee: @keyframes ticker { from { transform: translateX(0) }
  to { transform: translateX(-50%) } }
animation: ticker 40s linear infinite.
Pause via animation-play-state: paused on visibilitychange.
Duplicate content array once for seamless loop.
No JS position calculation.

### 5.5 Lazy Loading

Lazy load right sidebar and scan drawer:
const RightSidebar = React.lazy(() => import('./RightSidebar'));
const ScanDrawer = React.lazy(() => import('./ScanDrawer'));
Both wrapped in <Suspense fallback={`<GlassSkeletonPanel />`}>.

### 5.6 Skeleton System

Single `<Skeleton>` component:
  props: width, height, rounded ('sm'|'md'|'lg'|'full'), className.
  CSS: background linear-gradient shimmer, 1.5s ease-in-out infinite.
  Shimmer direction: 90deg, from var(--glass-bg) through
  rgba(255,255,255,0.08) back to var(--glass-bg).
Use in every card loading state. No per-card custom shimmer.

### 5.7 Error Boundaries

Wrap `<LeftColumn>` and `<RightSidebar>` each in an ErrorBoundary.
On error: render a glass card "Section unavailable — refresh to retry."
Never crash the full page.

---

## PART 6: PRODUCTION REQUIREMENTS

### 6.1 No Raw State in UI

ZERO instances of: "FETCHING", "LOADINGOU2026", "undefined", "null",
"Loading...", "Error", raw variable names, or JSON blobs in visible UI.
All strings must pass through a display layer.

### 6.2 Console Hygiene

No console.log in production. Use ESLint no-console rule.
All errors go to an error reporting utility (console.error in dev only).

### 6.3 Accessibility

- All icon-only buttons: aria-label.
- Color is never the sole state indicator — always pair with text or icon.
- Focus ring: focus-visible:outline-none focus-visible:ring-2
  focus-visible:ring-[var(--text-accent)] focus-visible:ring-offset-2
  focus-visible:ring-offset-transparent.
- Contrast: all body text minimum WCAG AA (4.5:1) in both themes.

### 6.4 Environment Config

All API base URLs, polling intervals, feature flags in .env / config.ts.
Zero hardcoded values in component files.

---

## PART 7: DELIVERY CHECKLIST

Before marking complete, verify every item:

THEME:
[ ] Dark theme: deep navy mesh background, glass cards,
    white/teal/blue signal text
[ ] Light theme: soft blue-gray mesh, frosted glass cards,
    dark text — all text remains legible
[ ] Theme toggle persists via localStorage
[ ] Zero hardcoded hex values in components

GLASS:
[ ] All cards use glass-card class with backdrop-filter
[ ] ::before liquid highlight visible in both themes
[ ] ::after specular hotspot visible and natural-looking
[ ] Hover state brightens glass correctly
[ ] Background orbs animate slowly and naturally

LAYOUT:
[ ] Two-column grid: left 1fr / right 420px
[ ] Navbar 52px glass strip, all elements visible
[ ] Ticker full-width below navbar, CSS animation only
[ ] Footer pinned to bottom, NEVER mid-page

CONTENT:
[ ] No "FETCHING", "LOADINGOU2026", or raw state text anywhere
[ ] Market Brief and Desk Brief merged into one card
[ ] Scan results render in drawer, page layout never reflows
[ ] "0 SCANNING" badge replaced with proper state UI
[ ] Top Movers: skeleton → data → error states all working
[ ] Ticker chips in Catalyst Watch: text always visible
[ ] Source Ledger: active/unavailable feeds visually distinct
[ ] All column headers in Allocator Playbook fully visible
[ ] No text clipped mid-word anywhere
[ ] All "Updated Xm ago" strings fully visible, never truncated
[ ] Footer legal text appears exactly once

PERFORMANCE:
[ ] All fetches concurrent via Promise.allSettled
[ ] No card blocks another card on load
[ ] Single polling manager, all intervals cleaned up on unmount
[ ] React.memo on all cards
[ ] Right sidebar and scan drawer lazy loaded
[ ] Single shared Skeleton component used throughout

PRODUCTION:
[ ] Zero console.log in build
[ ] All env values in config, not components
[ ] ErrorBoundary on left and right columns
[ ] All icon buttons have aria-label
[ ] Passes WCAG AA contrast in both light and dark themes
