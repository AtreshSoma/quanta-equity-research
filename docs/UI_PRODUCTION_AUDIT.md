# QUANTA Elite web app: production audit (Stage C synthesis)

- **Date:** 2026-10-08 (UTC).
- **Scope:** the web app in `ui/stock-analysis/webapp` and the engine it calls in `stock-analysis/core`.
- **Branch:** `portfolio-ui-quickfix`.
- **Inputs:**
  - [Stage A](audit-ui/STAGE_A.md): inventory and page-by-page review, findings A-01…A-53.
  - [Stage B](audit-ui/STAGE_B.md): B1–B15 cross-cutting review, findings B-01…B-89.
  - [Fix status](audit-ui/FIX_STATUS.md): groups G1–G6 and owner decisions D1–D5.
- **Evidence:** saved under `docs/audit-ui/raw/` and `docs/audit-ui/shots/`.
- **Context:** QUANTA is used today by its owner, on their own computer. The verdict below is given for both that use and a public release.

---

## 1. Executive summary and verdict

**Verdict: ready for the owner's own use; not ready for a public release.**

**For one person on their own computer, QUANTA is in good shape:**
- The wrong-output issues found in Stage A are fixed.
- Security quick wins are in: CSP, no inline script, safe links, JSON errors.
- Hard-coded values in the UI have moved to config.
- One score scale and one label set are used everywhere.
- Every call carries a "Model not yet validated" flag that links to the evidence page.
- Share classes fail closed.
- axe finds 0 WCAG 2.2 AA violations on 63 captures.
- Test status:
  - 609 JS unit tests pass;
  - 1,877 Python tests pass;
  - the Playwright fixture suite passes.

**A public release is a platform project, not a fix list.** Three things stand in the way:
1. **The model has no demonstrated edge.** Its calls use recommendation verbs (Buy/Accumulate) and include personalised Trim/Exit guidance.
2. **There are no accounts and no per-user isolation.** The app also runs on a development server.
3. **There is no licensing, legal or privacy groundwork.** That covers market data, terms, privacy and registration questions.

**Top 10 blockers for a public release** (all verified unless noted):

| # | ID | Blocker | Why it blocks |
|---|---|---|---|
| 1 | B-80 / B-20 | No validated edge (registry: predecessor NO_EDGE, live engines UNTESTED), yet calls say Buy/Accumulate | Misleading to the public; regulatory risk |
| 2 | B-21 | Personalised Trim/Exit guidance on users' own holdings | Likely regulated advice (US Advisers Act; SEBI IA/RA); needs legal advice per country |
| 3 | B-01 | No authentication or user model | Anyone reaching the server acts as the owner |
| 4 | B-02 | One global broker session | One user would see another user's holdings |
| 5 | B-60 | Werkzeug development server, loopback only; no TLS, WSGI server or container | Not an internet service |
| 6 | B-23 / B-65 | Unofficial Yahoo Finance data is the default source | Not licensed for redistribution |
| 7 | B-15 / B-22 | No privacy policy, terms, jurisdiction notice or age gate; Google Fonts sends visitors' IPs to Google | Legal minimum for a public site |
| 8 | B-81 | Uncalibrated 1–5 conviction stars beside untested calls | Implies precision the model hasn't earned |
| 9 | B-64 | My Portfolio's EDGAR client can send no contact (SEC fair-access rules) | A public deployment would breach SEC fair-access rules |
| 10 | B-11 / B-13 | History has data the owner flagged (74461f8, `SP_ELITE_PRO_Main.py`); a personal email is hard-coded in engine source | Must be cleaned before the repo or the app goes public |

**Also High before public:**
- B-03: job IDs are not scoped to the user.
- B-45: no screen-reader pass.
- B-53: Firefox and WebKit not tested.
- B-61: the web app's tests are not in CI.
- B-82: insider trigger too easy.
- B-87: fixed ticker lists in the scans.
- B-89: Picks ignores holdings.

---

## 2. Readiness scorecard (14 areas)

**Score:** 1 = poor, 5 = production-ready. "Own use" means one user on their own computer; "Public" means an internet release.

| # | Area | Own use | Public | Key evidence |
|---|---|---|---|---|
| 1 | Navigation and IA (B1) | 4 | 3 | Five clear destinations; Guide/Evidence labelled (D5); page title never changes (B-30) |
| 2 | Design system (B2) | 4 | 3 | Mostly tokens; 29 breakpoints, 77 stray colours (B-34, B-35) |
| 3 | Content and wording (B3) | 4 | 3 | G4/D1/D3 unified the labels; hard-coded tickers and baseline remain (B-39, B-40) |
| 4 | Cross-page consistency (B4) | 4 | 4 | One scale, one flag, one Hold label; two different "/100" scores for one ticker (B-43) |
| 5 | Accessibility (B5) | 4 | 3 | axe 0 violations; no manual screen-reader pass (B-45); table scopes (B-46) |
| 6 | Performance (B6) | 3 | 2 | gzip 0.44 MB; no bundling (B-50); 12.4 s with providers down (B-51) |
| 7 | Browsers, print, offline (B7) | 3 | 2 | Chromium only (B-53) |
| 8 | Frontend code quality (B8) | 4 | 3 | Escaping everywhere, no leaks; 101 HTML sinks, 10.6k-line app.js (B-56, B-57) |
| 9 | Backend and data (B9) | 3 | 2 | Validated scan routes; portfolio routes loose; Yahoo; EDGAR contact gap (B-62, B-64, B-65) |
| 10 | Security and privacy (B10) | 4 | 1 | Loopback guard, CSP, PKCE; no accounts (B-01, B-02) |
| 11 | Deployment and operations (B11) | 3 | 1 | Dev server; web app tests not in CI (B-60, B-61) |
| 12 | Compliance (B12) | 3 | 1 | Disclaimers present; Buy wording, personalised guidance, data licence (B-20 to B-23) |
| 13 | Recommendation quality (B13) | 2 | 1 | No validated edge; good fail-closed rails; insider, universe and holdings gaps (B-80 to B-89) |
| 14 | Testing and validation | 4 | 3 | 609 JS + 1,877 Python + Playwright fixture suite; live data not testable here; no cross-browser |

---

## 3. Page-by-page review

The full notes are in Stage A §A2.

| Page | What works | Fixed since Stage A | Still open |
|---|---|---|---|
| **Global chrome** | Skip link; visible focus; status popover; theme persists | Build label from deploy/git (A-05); "not yet" freshness (A-02); status words for screen readers (A-03); one Connect button (A-41); labelled Guide/Evidence (A-07, D5) | Page title per view (B-30); nav coded as tabs (B-31); no legal links (B-22) |
| **Overview** | Regime read with factor tally; height budget kept | Confirmed-chain overreach (A-08); fixed advice per headline (A-09); bands from `MARKET` config (A-10); "No read" with no data (A-11, A-12); ribbon text (A-13, A-14); one time budget (A-17); score /100 (D3) | Benchmark/macro tickers in code (B-40) |
| **Discover** | Movers and open-extreme boards; rejection reasons | Rows open Research, chart is secondary (A-21, D4); "Last" not "LTP", plain captions (A-19) | $ vs % change basis (A-20) |
| **Research** | Rail with the Decision, levels, horizon, evidence link; DQ gate | Contradictory call under failed DQ (A-22); contrast and wrapping (A-24 part); recent and mover chips, not fixed tickers (A-26); model flag beside the call (D1) | "--" rows (A-24 rest); page length (A-25); Street View rating source and licence (A-27); two "/100" scores (B-43); ticker not in URL (B-33) |
| **Picks** | Ranked calls, near-miss gaps, Method accounting, escape-safe | Scan sizes from config (A-32); one scan name (A-18); targets (A-35); scroll trap (A-34); share classes fail closed (A-29, D2); scores /100 (A-40, D3); flag beside every call (A-31, D1) | Trigger copy (A-33); chart axis (A-36); stale fixture (A-37); holdings-unaware (B-89); fixed universes (B-87); stars (B-81) |
| **My Portfolio** | Broker read-only via PKCE; attribution; review export; hide amounts | One Hold label (A-39); tool names removed (A-42); macro headlines (A-44); flag beside every action (D1) | Single broker session (A-38, B-02); personalised actions (A-46, B-21); Activity empty state (A-43); Review length (A-45) |
| **Guide** | Explains the five steps; keyboard demos; reduced motion | Prints without animation (A-47); "Model not yet validated" wording (D1) | Length (A-48); scrollspy (A-49); static ticker copy (A-50) |
| **Evidence** | Honest status (never validated, predecessor no edge); sample counts | Visible header link (A-53, D5); every call links here (D1) | Developer instructions shown (A-51, owner: public-release item); jargon IC/t (A-52) |

---

## 4. Findings table

### 4a. Open findings

Each open finding has evidence, status and an effort estimate.

**Effort:** S < 1 day, M = 1–5 days, L > 1 week.

**Severity:**
- **Blk**: blocks a public release.
- **H**: High.
- **M**: Medium.
- **L**: Low.

| ID | Sev | Area | Page | Evidence | Status | Impact | Recommendation | Effort |
|---|---|---|---|---|---|---|---|---|
| B-80 | Blk | Reco quality | All calls | `engine/evidence.py:35-204`; registry | verified | Calls without demonstrated edge | Out-of-sample validation; publish results | L |
| B-20 | Blk | Compliance | All calls | `engine/evidence.py:236-241`; D1 | verified | Recommendation verbs on an unvalidated model | Rename until VALIDATED, or obtain advice | S |
| B-21 | Blk | Compliance | My Portfolio | `my-portfolio.js:82-96, 777-799` | verified (code) | Likely regulated personalised advice | Legal advice; reframe as user rules | M–L |
| B-01 | Blk | Security | All | `app.py:434-470, 7387-7391` | verified | No users or isolation | Accounts, per-user scoping | L |
| B-02 | Blk | Security | My Portfolio | `portfolio_routes.py` `_service`; A-38 | verified | Cross-user broker exposure | Per-user sessions | M |
| B-60 | Blk | Ops | All | `app.py:7386-7411` | verified | Not deployable | WSGI + TLS + container | M |
| B-23 / B-65 | Blk | Data / compliance | All prices | `index.html:1396`; `pyproject.toml` | verified | Unlicensed redistribution | Licensed provider | M |
| B-15 | Blk | Privacy | All | template fonts; no legal pages | verified | No privacy basis | Privacy policy, self-hosted fonts, retention | M |
| B-81 | Blk | Reco quality | Picks, Research | `SP:6709-6772`; `app.js:3798, 7430` | verified | False precision | Hide or relabel the stars | S |
| B-64 | H→Blk (public) | Backend | My Portfolio | `app.py:7336`; `sources.py:211-242` | verified | SEC fair-access breach | Require a contact, fail closed | S |
| B-11 | H | Repo | — | `raw/B10_git_history_check.md` | verified | Possible real trade data in history | Replace; rewrite before public | S/M |
| B-12 | M | Repo | — | same | verified | Large untracked engine in history | `.gitignore`; rewrite before public | S |
| B-13 | H | Repo / hard-coding | — | `Alpha_Picks_Engine.py:985-991` | verified | Personal email in source | Move to env; scrub | S |
| B-14 | M | Repo | — | `raw/B10_repo_scan.txt` | verified | Personal documents in the same repo | Split the repo | S |
| B-22 | H | Compliance | All | `index.html:893, 1471` | verified | No terms, jurisdiction or age gate | Terms, 18+, risk page | S–M |
| B-03 | H | Security | Jobs | `app.py:4960, 6317, 7177` | verified | Job read by ID | 128-bit, owner-scoped IDs | S |
| B-04 | H | Security | All writes | `app.py:437-459` | verified | Needed once cookies exist | CSRF tokens, SameSite | S |
| B-82 | H | Reco quality | Picks | `SP:10034-10057, 10227-10282` | verified | One small insider buy loosens every rule | Thresholds in config; keep the extended veto | S–M |
| B-87 | H | Reco quality / hard-coding | Picks | `SP:2381-4499, 8258-8351` | verified | Fixed ticker lists | Data-driven universes | M |
| B-89 | H | Reco quality | Picks | `app.py:5015-5080`; `SP:2695` | verified | Adds to overweight holdings | Holdings-aware sizing | M |
| B-45 | H | A11y | All | — | unverified | Unknown screen-reader issues | NVDA and VoiceOver pass | M |
| B-53 | H | Browsers | All | `/opt/pw-browsers` | unverified | Unknown Firefox/Safari issues | Add Playwright projects | S |
| B-61 | H | Ops | — | `.github/workflows/stock-analysis-ci.yml` | verified | Web app regressions unguarded | Web app CI job | S |
| A-27 | M | Compliance | Research | shot | verified | Third-party rating shown prominently | Label source and date; licence | S |
| B-05 | M | Security | API | no `MAX_CONTENT_LENGTH` | verified | Large bodies parsed | 64 KB cap | S |
| B-06 | M | Security | All | live `Server` header | verified | Version disclosure | Proxy strips it; HSTS | S |
| B-10 | M | Deps | — | `raw/B10_npm_audit_*.json` | verified | 1 dev high; no lock | `npm audit fix`; lock file | S |
| B-24 | M | Compliance | Evidence | `app.js:2130-2209` | verified | — | Minimum sample before showing rates | S |
| B-25 | M | Compliance / a11y | All | capture report | partial | Accessibility law exposure | Accessibility statement | M |
| B-30 | M | IA | All | `index.html:7` | verified | Same title everywhere | Title per view and ticker | S |
| B-34 | M | Design | All | `raw/B2_design_system.txt` | verified | Regressions at odd widths | 5–6 breakpoint tokens | M |
| B-39 | M | Hard-coding | Research | `app.js:9477` | verified | Fixed "baseline 3" | From config | S |
| B-40 | M | Hard-coding | Overview, My Portfolio, Evidence | `app.js:2031-2166, 5145`; `my-portfolio.js:482` | verified | Fixed benchmark and macro tickers | Config per market | S |
| B-43 | M | Consistency | Picks vs Research | D3 note | verified | Two "/100" numbers for one ticker | Name them apart | S |
| B-46 | M | A11y | Tables | `raw/B5_a11y.txt` | verified | Weak table semantics | scope + caption | S |
| B-50 | M | Perf | All | `raw/B6_weight.txt` | verified | 0.44 MB up front | Build step, split | M |
| B-51 | M | Perf | Overview | FIX_STATUS | partial | Slow when providers lag | Stream tiles | M |
| B-56 | M | Frontend | All | `raw/B8_frontend.txt` | verified | XSS relies on discipline | Escaping template helper | M |
| B-57 | M | Frontend | All | 10,601 lines | verified | Maintainability | ES modules | L |
| B-62 | M | Backend | My Portfolio API | `portfolio_routes.py:232-266` | verified | Unbounded inputs | Schemas | S |
| B-66 | M | Backend | Jobs | `app.py:620-692` | verified | Jobs lost on restart | Queue (public) | M |
| B-67 | M | Backend | API | — | verified | No rate limits | Per-user limits | S |
| B-68 | M | Ops | Logs | `app.py:107-125` | verified | No rotation; redaction gaps | One logging config | S |
| B-70 | M | Ops | — | `pyproject.toml` | verified | Unpinned deps | Lock file | S |
| B-71 | M | Ops | — | `app.py:4799` | verified | No readiness/version | `/healthz`, `/readyz`, `/api/version` | S |
| B-72 | M | Ops | — | `sources.py:114-175` | verified | Registry config | Env-only in production | S |
| B-73 | M | Ops | — | state files | verified | No backups | DB + backups | S |
| B-83 | M | Reco / hard-coding | Research | `SP:9732` | verified | Fixed 14 days | Config | S |
| B-85 | M | Reco | Picks | `corporate_events.py:307-309` | partial | Possible false ETF AVOID | Series-level match | S |
| A-01 | M | Testing | — | STAGE_A A0 | verified | Smoke test passes offline | Assert real data | S |
| A-04 | M (public) | Security | Header | `app.py:4769` | verified | Global cache clear | Admin only (with B-01) | S |
| A-20 | L | Data | Discover | shot | partial | $ vs % mismatch | One basis | S |
| A-24 | L | Content | Research | capture report | partial | "--" rows remain | Hide unmeasured rows | S |
| A-25 | L | UX | Research | shot | verified | Long page | Collapse sections | M |
| A-33 | L | Content | Picks | shot | partial | Trigger copy contradiction | Assert ≥ threshold; one name | S |
| A-36 | L | Charts | Picks | shot | verified | No price axis | Add axis | S |
| A-37 | L | Testing | — | fixture | verified | Stale fixture | Refresh | S |
| A-43, A-45 | L | UX | My Portfolio | shots | verified | Empty state, length | Fix copy; collapse | S |
| A-48, A-49, A-50 | L | UX / hard-coding | Guide | shots; `guide.test` | verified | Length, scrollspy, static tickers | Shorten; fix spy; config copy | S–M |
| A-51 / B-74 | L (owner: public item) | Content | Evidence, Picks | A-30, A-51 | verified | Operator text for users | Logs + admin page | S |
| A-52 / B-41 | L | Content | Evidence, Research | — | verified | Jargon | Glossary | S |
| A-06 / B-75 | L (owner: public item) | Privacy | All | template | verified | Google Fonts | Self-host | S |
| B-31, B-33, B-35–B-38, B-42, B-44, B-47–B-49, B-52, B-54, B-58, B-63, B-69, B-86 | L | various | various | STAGE_B | verified | Polish | See STAGE_B | S–M |

### 4b. Fixed since Stage A

Verified by tests in the listed commits.

| Findings | Fix | Commit |
|---|---|---|
| A-08, A-09, A-11, A-12, A-22 | Wrong or misleading output | 51da241 |
| CSP, safe links, JSON errors, thresholds escaping | Security quick wins | ee15ccd |
| A-02, A-05, A-10, A-16, A-26, A-32 | Hard-coded values → config/data | 51da241, 86016e6 |
| A-15, A-18, A-19, A-39, A-42, A-44 | Wording | add0a89 |
| A-03, A-13, A-14, A-24 (part), A-28, A-34, A-35, A-41, A-47 | Layout and accessibility | e152fb6, 30fd695 |
| A-17 | Speed (one time budget; gzip; caching) | 929e7ea |
| A-31 (flag), A-53 (D1); A-29 (D2); A-40 (D3) | Owner decisions | d21c334 |
| A-21 (D4); A-07 (D5) | Owner decisions | 02a8c17 |
| A-23 | Kept by design: the rail labels R:R "to the 12-month target" | — |

---

## 5. Content audit and consistency matrix

| Concept | Overview | Discover | Research | Picks | My Portfolio | Guide / Evidence | Consistent? |
|---|---|---|---|---|---|---|---|
| Call labels (Buy / Accumulate / Watchlist / Hold, no new money) | ✓ | — | ✓ | ✓ | ✓ (`ACTION_LABEL`) | ✓ | Yes (G4) |
| Model-evidence flag | "Model not yet validated" on brief | — | Flag on rail | Flag on overview + cards | Flag on every action | Same wording; flag links here | Yes (D1) |
| Score scale | /100 | — | /100 (one decimal) | /100 | /100 | /100 bands | Yes (D3); decimals differ (B-44); two models (B-43) |
| Scan name | "Picks scan" | "Run a Picks scan" | — | "Picks scan" | — | ✓ | Yes (G4) |
| Price label | "Last" | "Last / Change" | Last price | Price | Last | — | Yes (G4) |
| Share classes | — | — | — | One row per issuer, held-back twins named | — | — | Yes (D2) |
| Data source and delay | Ribbon note | Board note | Rail footnote | Method | Holdings timestamps | Evidence "Data" | Yes |
| Disclaimer | Footer | Footer | Footer | Footer | Footer + guidance footnote | Guide fine print | Partly: none beside each call (B-22) |
| Benchmark | SPY literal fallback | — | — | — | SPY/QQQ/DIA/IWM literal | SPY / ^NSEI literal | **No: hard-coded (B-40)** |

**Hard-coded values still open** (no-hard-coding rule):
- **UI:**
  - B-39: "(baseline 3)".
  - B-40: benchmark, macro and index tickers.
- **Engine:**
  - B-87: fixed ticker universes.
  - B-82, B-83: insider and earnings literals.
  - B-81: conviction cut-offs.
  - `evidence.py:40` (120 days).
  - `robinhood.py:65, 387, 659`.
- **Repo:** B-13 (email and holding in `Alpha_Picks_Engine.py`).

The full list is in STAGE_B B13 and `raw/B13_recommendation_quality.md`.

---

## 6. Add / modify / remove

| Action | Item | Why | Ref |
|---|---|---|---|
| **Add** | Out-of-sample validation report on the evidence page | Earn or withhold the recommendation verbs | B-80, idea 1 |
| Add | Accounts, per-user broker sessions, owner-scoped jobs | Public prerequisite | B-01–B-03 |
| Add | Terms, privacy policy, risk disclosure, jurisdiction and 18+ | Legal minimum | B-15, B-22 |
| Add | Web app CI (JS unit, Playwright ×3 browsers, lint) | Guard every change | B-61, B-53 |
| Add | Holdings awareness in Picks | Avoid adding to overweight names | B-89 |
| Add | Per-view page titles and ticker routes | Shareable, announced pages | B-30, B-33 |
| **Modify** | Buy/Accumulate → neutral criteria labels until VALIDATED (public) | Compliance | B-20 |
| Modify | Personalised Trim/Exit → user-configured rule alerts (public) | Compliance | B-21 |
| Modify | Insider "bullish" trigger: minimum value and number of insiders from config; never skip the extended veto | Rule integrity | B-82 |
| Modify | Universes from live data, not fixed lists | No hard-coding | B-87 |
| Modify | EDGAR: contact required everywhere, fail closed | SEC fair access | B-64 |
| Modify | Benchmarks, macro proxies, indexes, "baseline" → config | No hard-coding | B-39, B-40 |
| Modify | Name the two scores apart ("Scan score" / "Research score") | Consistency | B-43 |
| **Remove** | Conviction stars (or relabel as a checklist count) until calibrated | False precision | B-81 |
| Remove | Clear-cache button for non-admins | Global action | A-04 |
| Remove | Operator setup text from user pages (public) | Users can't act on it | B-74 |
| Remove | Google Fonts (self-host) | Privacy, CSP | B-75 |
| Remove | Personal email, holding and personal documents from the QUANTA repo | Hygiene | B-13, B-14 |

### 6a. Ideas (B15, ranked) and advice

The full table is in STAGE_B §B15: user problem, what it shows, where it lives, data needed, whether it can be fully dynamic, effort, impact, label.

| Group | Ideas (rank order) | Labels |
|---|---|---|
| Trust | 1. Published validation report · 2. Call history per ticker · 3. "What would change this call" | Strategic · Strategic · Quick win |
| Decision support | 4. Holdings-aware Picks · 7. Daily re-check watchlist · 5. Earnings week board · 6. Saved scan presets · 8. Sector map by call | Strategic · Strategic · Quick win · Quick win · Nice to have |
| Understanding | 9. Inline glossary · 10. Score breakdown bar · 11. Gap-through-stop scenario | Quick win · Quick win · Nice to have |
| Platform | 12. Shareable tear-sheet URL · 13. Installable PWA | Quick win · Nice to have |

All ideas are fully dynamic (live data or config) and keep every safety rule.

**Top 5 moves for the next 90 days:**
1. Validate the live engine out of sample, or relabel the calls before any public release.
2. Close the risky rails: insider trigger, holdings-aware Picks, data-driven universes.
3. Put the web app in CI, with three browsers.
4. Platform phase 2: accounts, per-user sessions and jobs, CSRF tokens, rate limits.
5. Legal and data: advice on personalised guidance, terms and privacy, a licensed data contract, self-hosted fonts.

**What to avoid:**
- New engines or labels before one is validated.
- Social features, options and crypto.
- Probabilities or win-rate headlines before calibration.
- Loosening fail-closed checks to fill lists.
- Rebuilding TradingView-grade charting.

---

## 7. Competitor gap matrix (summary of STAGE_B §B14)

**Basis:** general product knowledge; marked partial.

| Gap vs Yahoo / TradingView / Seeking Alpha / Morningstar / Robinhood / Finviz | QUANTA decision |
|---|---|
| Published rating methodology and track record (Seeking Alpha Quant, Morningstar stars) | **Include**: the trust gap to close (B-80) |
| Licensed real-time or delayed data (all) | **Include**: before public (B-23) |
| Saved screeners (Finviz, TradingView, Seeking Alpha, Morningstar) | **Include**: saved scan presets |
| Watchlists that re-check and alert (all) | **Include**: daily re-check watchlist |
| Earnings calendar (all) | **Include**: earnings week board |
| Interactive charts with indicators (TradingView) | **Adapt**: in-app chart with QUANTA's own levels; TradingView stays secondary (D4) |
| Fundamentals and statements (Yahoo, Seeking Alpha, Morningstar) | **Adapt**: summary with source and date |
| Analyst consensus (Yahoo, Seeking Alpha, Robinhood) | **Adapt**: beside the QUANTA call, with disagreement explained |
| Fair value (Morningstar) | **Adapt**: a range with inputs |
| Mobile app (all but Finviz) | **Adapt**: PWA later |
| Market heat map (Finviz, TradingView) | **Include** (nice to have): coloured by call |
| Personalised holding guidance (none of the six do this as advice) | **Adapt**: user-set rule alerts for public (B-21) |
| Social ideas (TradingView, Seeking Alpha), options/crypto (Robinhood, TradingView) | **Skip** |

**QUANTA's differentiators to keep:**
- a plain-language reason for every call;
- fail-closed safety rails;
- an honest evidence page;
- broker-linked portfolio review.

---

## 8. Launch checklist

**Own use (now).** Done unless marked:
- [x] Wrong-output fixes (G1); security quick wins (G2); config-driven UI (G3); wording (G4); layout/a11y (G5); speed (G6).
- [x] Model flag beside every call (D1); share classes fail closed (D2); one /100 scale (D3); Discover → Research (D4); Guide/Evidence visible (D5).
- [ ] **Owner:** restart port 5500 and run one live Quick scan and one live Research tear sheet. Not verifiable here: live data is blocked.
- [ ] Add `stock-analysis/core/SP_ELITE_PRO_Main.py` to `.gitignore` (B-12).
- [ ] Decide on the 74461f8 trade data (B-11).

**Public release** (every box needed):
- [ ] Validation published, or calls relabelled (B-80, B-20).
- [ ] Legal advice on personalised guidance per launch country; guidance reframed or restricted (B-21).
- [ ] Terms, privacy policy, risk disclosure, jurisdiction list, 18+ (B-15, B-22).
- [ ] Licensed market data; Yahoo removed from production (B-23, B-65).
- [ ] Accounts; per-user broker sessions, jobs, journals and ledger; CSRF tokens; rate limits; admin-only maintenance (B-01–B-04, B-67, A-04).
- [ ] WSGI server, TLS proxy (HSTS, no `Server` banner), container, env-only config, health/readiness/version endpoints, structured logs, backups (B-60, B-06, B-68, B-71–B-73).
- [ ] EDGAR contact required everywhere; shared rate limiter (B-64).
- [ ] Stars hidden or relabelled (B-81); insider trigger fixed (B-82); data-driven universes (B-87); holdings-aware Picks (B-89).
- [ ] Self-hosted fonts; CSP without Google (B-75, B-08).
- [ ] Operator messages moved to logs/admin (B-74).
- [ ] Web app CI with Chromium, Firefox and WebKit (B-61, B-53).
- [ ] Manual screen-reader pass and accessibility statement (B-45, B-25).
- [ ] Repo split; history cleaned of flagged data and personal email (B-11–B-14).
- [ ] Load test and cost controls per user (public plan, phase 4).

---

## 9. Roadmap

Effort is for one experienced engineer. Legal and data-licensing lead times run in parallel.

| Phase | Weeks | Content | Exit criterion |
|---|---|---|---|
| 0. Own-use polish | 1 | B-39, B-40, B-43, B-44, B-30, B-33, B-46, B-62, B-64, B-68, B-83, A-33, A-36, `.gitignore`; web app CI (B-61) | CI green on three browsers |
| 1. Model truth | 2–4 (parallel) | Walk-forward validation of the live engine (B-80); score-band outcomes (B-81); insider trigger (B-82); data-driven universes (B-87); holdings-aware Picks (B-89) | Evidence status decided from data; labels follow it |
| 2. Decisions | parallel | Legal advice (B-21, B-20); data licence (B-23); terms and privacy drafts (B-15, B-22) | Signed-off wording and data contract |
| 3. Accounts and isolation | 3–4 | B-01–B-04, B-67, A-04; per-user stores | Two test users fully isolated |
| 4. Production platform | 2–3 | B-60, B-06, B-66, B-70–B-73, B-75, B-74; build step (B-50) | Staging deploy with TLS, monitoring, backups |
| 5. Launch polish | 2 | B-45, B-25, B-56, B-57 (start), ideas 3, 9, 10, 12; load test | Checklist §8 complete |

**Total:** about 8–12 weeks of development for phases 0 and 3–5, plus phases 1–2 in parallel. This is consistent with the public plan in FIX_STATUS.md.

---

## 10. What was not verified

- **Live data:**
  - Yahoo, SEC EDGAR, RSS and Google Fonts are blocked by this environment's proxy.
  - Every check ran on fixtures or on the offline state.
  - The owner will run one live Quick scan and one live Research tear sheet on port 5500.
- **Broker paths:** Robinhood connection, holdings, orders and history were never used live. They are covered by unit tests with synthetic data only. No trades and no order previews.
- **Firefox and WebKit:** not installed here (B-53).
- **Manual screen reader:** no NVDA or VoiceOver pass (B-45).
- **Production performance:** no load test, no live latency (B-51).
- **Legal conclusions:** B12 lists questions for a qualified adviser, not legal advice.
- **Competitor capabilities:** from general product knowledge, not a side-by-side session (B14, partial).
- **Possible ETF false-AVOID from a trust-level Form 25:** read from code, not reproduced (B-85).
- **Git history:** the 10 token-like strings in `SP_ELITE_PRO_Main.py` were not inspected. Whether the 74461f8 figures are real fills cannot be told from the repo.
- **Dependency audit:** run on the scratch environment's resolved versions; the owner's installed versions may differ (no lock file, B-10).

---

## 11. Appendix

**Documents:**
- [STAGE_A.md](audit-ui/STAGE_A.md)
- [STAGE_B.md](audit-ui/STAGE_B.md)
- [FIX_STATUS.md](audit-ui/FIX_STATUS.md)
- [PROGRESS.md](audit-ui/PROGRESS.md)

**Raw evidence (`docs/audit-ui/raw/`):**

| File | Covers |
|---|---|
| `B10_grep.txt` | Guards, env vars, headers, job IDs |
| `B10_repo_scan.txt` | Tracked-file scan; counts and file names only |
| `B10_git_history_check.md` | 74461f8 and `SP_ELITE_PRO_Main.py` |
| `B10_npm_audit_prod.json`, `B10_npm_audit_all.json`, `B10_pip_audit_scratch_venv.json` | Dependency audits |
| `B12_grep.txt` | Disclaimers, legal pages, regulators, claims, attribution |
| `B13_recommendation_quality.md` | B13 code review with file:line |
| `B9_B11_backend_review.md` | Backend, data, deployment, file:line |
| `B2_design_system.txt`, `B5_a11y.txt`, `B6_weight.txt`, `B8_frontend.txt` | Metrics |

**Screenshots:** `docs/audit-ui/shots/` (65 Stage A captures at 1440 light, 1440 dark and 390 dark), plus `capture_report.json`.

**Fix commits:**
- 51da241, ee15ccd, 86016e6, add0a89, e152fb6, 929e7ea, 30fd695: groups G1–G6;
- c6bb3e2: FIX_STATUS.md;
- d21c334: D1–D3;
- commit 02a8c17.

**Test commands:** listed in FIX_STATUS.md. Last recorded results:
- JS 609 pass;
- Python 1,877 pass, 6 skip, 14 xfail;
- Playwright fixture suite (desktop-dark, mobile; live-data tests excluded).

---

## What changed

**Since Stage A**, these are fixed and verified by tests:
- the wrong-output issues;
- the security quick wins;
- the hard-coded UI values;
- wording;
- layout and accessibility;
- speed;
- all five owner decisions.

**Owner decisions in this round:**
- **D1:** "Model not yet validated" beside every call (Picks, Research rail, My Portfolio, Overview brief, Pro watchlist), with one wording.
- **D2:** share classes fail closed, with the reason on the row.
- **D3:** the Picks 0–10 score is checked to be linear with the ladder (×10), so every page shows /100 and the engine keeps its scale.
- **D4:** Discover rows open Research, with TradingView as "Open chart".
- **D5:** labelled Guide and Evidence links in the header, still visible on phones.

**Stage B added 70+ findings.** The public-release blockers:
- no validated edge;
- Buy wording (owner: logged as a blocker);
- personalised guidance;
- no accounts;
- single broker session;
- dev server;
- unlicensed data;
- no legal pages;
- uncalibrated stars;
- EDGAR contact;
- repo history and personal data.

**Logged as public-release items per the owner:** self-hosted fonts, and moving the operator setup messages out of user pages.
