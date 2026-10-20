# Guide redesign (2026-10-13)

Source: four rounds of owner review of the Guide ("How QUANTA works"). The final direction: the
Guide is QUANTA's hero page. It must be attractive, tell its story through scroll, carry a
full-size promo reel, state plainly why QUANTA is worth using, and stay accurate and humble. No
competitor is named or compared, and no performance claim is made.

Scope: `#view-guide` in `webapp/templates/index.html`, the "HOW QUANTA WORKS (#guide)" block in
`webapp/static/quanta.css`, `bindGuide`, `bindGuideSteps`, `bindGuideMore`, `bindGuideReveals` and
the reel in `webapp/static/app.js`, the tests that pin them, and DESIGN.md (Guide display, Guide
section, Guide Walkthrough).

## The page now

| # | Section | Answers | What it is |
|---|---|---|---|
| 1 | Hero | What is QUANTA? | "Research that shows its work.", an 18-word lede, two actions, and the example call: chart, checks, Decision and the "Model not yet validated" tag |
| 2 | Why QUANTA | Why use it? | The statement holds in place (sticky) while six promises scroll by as an editorial list. Each promise jumps to the step that proves it |
| 3 | See it in 15 seconds | What does it look like working? | The reel at full content width; its picture opens to the full frame as it scrolls in. The poster is the Research frame |
| 4 | How QUANTA works | How does it work? | The five pages told by scroll: a sticky step bar marks the step being read and fills as the story advances, and each demo performs its rule as it arrives |
| 5 | Honest about what it knows | How do I read it responsibly? | "Using a call well", how a call is measured, live status, tags, data and checkability |
| 6 | Your first five minutes | Where do I begin? | Three steps, each linked to the page that does it |
| 7 | Common questions | Anything else? | Eleven answers, closed by default |

Page height at 1440 x 900: 11,044 px (11,608 before the redesign). The length is deliberate: the owner chose scroll storytelling over the shorter tabbed version of round 3. It is kept in check by folding each step's reference lists behind "More detail".

## How it evolved

| Round | Direction | Kept | Replaced |
|---|---|---|---|
| 1 | Humble, no competitor framing; easier for anyone | "What it does for you", folds, first five minutes, the Floor Rule test | Internal-count strip, decorative glows, the eyebrow |
| 2 | Shorter, less manual-like | The track record as its own section, "Using a call well" | Five stacked chapters became tabs (5,458 px) |
| 3 | The tabs read as a manual, not a story; earlier scroll animations were better; the reel should be full size | Every fix and fold; the Research poster | The tabs became a scroll story; the reel returned to full width; Why QUANTA moved up as an editorial list |

## Motion

The focal sequence is the scroll story through "How QUANTA works", built from the product's own rules:

- **Step bar**: sticky under the topbar. One azure rule travels to the step crossing the middle of the screen (`aria-current="step"`). A 1px line fills as the whole section is read: a CSS view timeline on `.hw-steps`, `contain 0%` to `contain 100%`. On first arrival its line draws in and the five icons light in order.
- **Each step**: as it arrives, its icon tile pops, its heading rises, and its demo plays once:
  - Overview: the dial sweeps to the posture band.
  - Discover: beside the sticky funnel, each gate fires as its card crosses the middle of the screen, and the twelve example names fall away at their own gate.
  - Research: the call steps down the ladder and lands.
  - Picks: the receipt fills line by line and the risk box grows to its budget.
  - My Portfolio: the holdings bars grow against the limit lines.
- **Reel**: the picture opens from a framed window to the full stage as it scrolls in (`clip-path` on a scroll view timeline). It autoplays once in view and pauses off screen.
- **Why QUANTA**: the statement stays in view while the promises arrive in reading order.
- **Reduced motion**: nothing moves by itself. The fill and the reel opening are absent, every demo shows its finished state, and the reel waits on its poster.
- Nothing loops, and there is no parallax. All motion is compositor work (transform, opacity, `clip-path`) or a one-shot Web Animation; the only scroll-driven pieces are CSS view timelines and IntersectionObservers.

## Defects found and fixed (all rounds)

| # | Defect | Root cause and fix |
|---|---|---|
| 1 | Limits strip and ledger labels broke across lines around numbers | Block-span rules hit the threshold spans inside labels; scoped to direct children |
| 2 | Example-book percentages struck through by the limit lines | Labels raised above the lines on a matching backing |
| 3 | Ten labels below the 12px floor | Raised to `--fs-2xs`; a unit test enforces the floor |
| 4 | Decorative glows: hero haze in light theme, button glow, end card, reel stage, the ledger dot's halo, the read-only gate | Removed (DESIGN.md: azure marks action only; Impeccable craft floor: no zero-offset halos) |
| 5 | Thick 3px accent edges on "Keep in mind" notes, promises, "Using a call well", tab cards and the holdings inspector | "Keep in mind" now uses the DESIGN.md caveat box (1px warn line, 4px radius); the lists use hairlines |
| 6 | Reel controls clipped on phones ("Download WebM" cut off) | The scrubber takes its own row; GUIDE-002 checks for clipped controls |
| 7 | Play button covered the wordmark, and later the stamp, on the poster | At the foot of the frame on desktop, in the corner on phones |
| 8 | "$2,500 x 4.2%" in the reel read as the account size | Labelled "position x stop distance"; WebM re-rendered |
| 9 | Two FAQ questions missing "that" | Fixed |
| 10 | Flat hierarchy: the page's section titles were the same 20px as a step's heading | `--hs-section` (24 to 30px) for the Guide's own sections |
| 11 | GUIDE-009 raced the app's view fade | The test counts animations inside the guide's content |

## The reel

Kept and refined rather than replaced. It is drawn live in SVG on one Web Animations timeline, every
figure in it is tested against the page, and it follows the product. A rendered film would drift.

- Full width.
- Research-frame poster (`REEL_POSTER_MS = 9600`); play starts at 0:00.
- Scroll-linked opening.
- Labelled sizing scene.
- WebM re-rendered with `tests/tools/render_reel.cjs`: VP8, 1280 x 720, 15.0 s.

## Skills and tools used

- **impeccable**:
  - Context loaded with `impeccable context`.
  - Followed `animate` (one focal sequence from the product; scroll-driven motion only where scroll carries meaning) and the craft floor. That led to: no eyebrows, no hero-metric strip (a planned figures band was dropped), no thick accent edges, no zero-offset halos, and no same-size icon cards as page structure (Why QUANTA became an editorial list).
  - Verified in one batched round plus one confirmation round.
- **design-taste-frontend**: hero discipline, one label per CTA intent, copy self-audit, varied layout families.
- **hyperframes-animation** and **hyperframes-keyframes**: pose and timing doctrine for the reel's WAAPI timeline. The final frame is part of the animation, which is why the Research frame is the poster; exits run faster than entrances.
- **hyperframes-registry**: searched (`catalog --query "scroll reveal section headline and staggered list"`). The closest items (staggered-fade-up, ui-3d-reveal, scroll-camera-story) are HyperFrames video blocks and cannot be installed into this Flask page. They served as references only.
- **ui-ux-pro-max** (earlier rounds): confirmed Plex and the trust-first pattern; its palette was rejected under DESIGN.md.
- **Tools**: Playwright (Chromium) for screenshots, motion captures, heights and e2e; ffmpeg for the WebM.

## Tests

- `npm test`: 708 of 708.
- `playwright test tests/e2e/guide.spec.ts` on desktop dark, desktop light, tablet and mobile: 96 of 96.
- **Round 4**:
  - GUIDE-003: the step bar jumps to each step and moves focus.
  - GUIDE-006: gates advance with the scroll on wide screens and run once in view on narrow ones.
  - GUIDE-006b: the bar marks the step being read and its fill sits between 0.3 and 1 mid-story.
  - GUIDE-014: the lab arrives from the bottom edge and plays its first look.
  - GUIDE-016: promises jump and move focus.
  - REEL-004: the reel is at least 95% of the content width and its picture uses the scroll-linked opening.
  - Unit: step bar order and targets; no tab roles; the no-JS state.
- `tests/e2e/topbar.spec.ts` fails 7 of 8 tests both before and after these changes (checked against a clean worktree of `HEAD`). It does not involve the Guide.

## Verified in a browser

- **Screenshots**: 1440 x 900 in dark and light, and 390 x 844.
- **Motion captures**: mid-scroll at 35% and 70% of the story. The fill measured 0.42 and 0.82; the current step was Discover, then Picks. The reel was captured while entering.
- **Covered by e2e**: overflow and clipped controls from 360 to 1600 px, and WCAG 2.1 AA in both themes (axe, zero violations).

## Not verified

- Safari and Firefox were not run. Where CSS view timelines are unsupported (Firefox, older Safari), the step-bar fill and the reel opening simply do not appear. Everything else, including the scroll-spy bar and the arrival animations, uses IntersectionObserver and Web Animations.
