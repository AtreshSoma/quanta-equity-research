# 11 — Implementation status

Branch `audit/roadmap-p0-p1`, from `main` at `ce1e9b4`. 2026-11-06. Not pushed, not merged.

## Verification at the branch head

| Suite | Before (main) | After (branch) |
|---|---|---|
| Python, `pytest -m "not golden"` | 2,976 passed | **3,048 passed**, 18 skipped, 4 xfailed |
| Golden master (pinned env, `tests/golden/requirements-lock.txt`) | 27 passed | **27 passed**: no change to engine output |
| JavaScript, `npm test` | 882 (1 started failing on 2026-11-06, a date bomb) | **892 passed** |
| `ruff check .` | pass, F401 not gated (123 findings) | **pass with F401 gated** (0 findings) |
| `pip-audit` on `webapp/constraints.txt` | 2 advisories | **no known vulnerabilities** |
| `python -m tracking verify` on the committed `track_record/` | ok | ok (tested) |

Verified in the second round (below): live yfinance 1.4.1 analyses and the full browser suite.
Still not verified: a full live Picks scan (needs `QUANTA_SEC_CONTACT`, not set on this machine) and
a live Claude API call (no credentials here; no money was spent).

## Done

| Task | Commit | What changed | Measured result |
|---|---|---|---|
| — | `5e0ecb9` | `my_portfolio_workspace.test.cjs` read today's date against a 45-day window and a fixture dated 2026-09-22; it began failing on 2026-11-06 on every branch. The clock is now pinned | JS suite green again |
| P0-1 Fingerprint coverage | `dea9af5` | Every `elite_<version>_shadow` key (v2, v3, v4 and any later one) hashes `TradingAnalysis_ELITE.py` | v4 shadow: 43 files before, 51 now |
| P0-2 Optional-module provenance | `dea9af5` | Fingerprints follow imports into `core/` and `modules/`, including `_import_optional` ones, skipping `__main__`-only imports; a missing optional module changes the fingerprint and is listed in the row's `provenance_json` | `elite` now covers 8 `modules/` files, none before |
| P0-3 Model fingerprint | `dea9af5` | New `model_fingerprint` column: the same files as syntax trees without docstrings, logging, `print` or CLI display functions (a test proves nothing else calls the excluded ones). Prospective IC and the new evidence clock group by it; legacy rows group as `code:<fingerprint>` | See "Changed from the plan" |
| P0-4 Input recording | `dea9af5` | Each ledger row stores `inputs_json` (analyst targets and counts, valuation method and targets, revisions, sentiment, insider flow, levels, source ids) and `inputs_hash`; publish fixes `model_fingerprint` and `inputs_hash` in `call_hash` for new rows only | The committed record still verifies |
| P0-5 Advisories | `11123d2` | requests 2.34.2, curl_cffi 0.17.0, yfinance 1.4.1 (1.2.0 capped curl_cffi below the fix); floors raised in `pyproject.toml` and `requirements.txt` | 0 advisories; all suites pass |
| P0-6 Doc drift | `d22f5d1` | README and OPEN_ITEMS no longer quote stale counts | — |
| P1-8 Unused imports | `d22f5d1` | 110 removed by ruff's safe fix; 17 availability checks inside `try:` kept with `# noqa: F401`; F401 gated in CI | Golden unchanged |
| P1-1 Free-factor control | `9f3f988` | `factor.baseline.v1` (12-1 momentum + quality leg) scored beside every Research call under ledger engine `factor_baseline_shadow`; pre-registered in `docs/preregistration/factor_baseline_v1.md`; 12-1 matches `apex_quant` exactly (test) | Starts recording on the next Research call |
| P1-6 Evidence clock | `dea9af5` | `/api/track-record` and the Evidence page show, per engine, resolved calls and independent dates on the current model version against the pre-registered 30 and 10 | — |
| P1-3 Shrinkage covariance | `1788890` | Risk shares use Ledoit-Wolf (constant correlation); method and intensity reported. Volatility, VaR/CVaR and correlated pairs unchanged | UI fixture: 8% shrinkage, shares move under 0.2 points |
| P1-4 Factor attribution (+E3) | `b6fca72` | Fama-French 5 + momentum loadings of the book with Newey-West intervals, from the Ken French library (downloaded in the background job, cached a week, last good copy on failure); card in Risk exposure | Real data: SPY market beta 0.99 (R² 0.99), IWM size +0.87, QQQ market 1.14 |
| P1-5 AI brief (+P2-1, E2) | `9a25d07` | Optional one-call Claude brief (`claude-opus-5-5`, server-side fallbacks, structured output) with summary, case for and case against; shown only if every citation resolves and `check_narrative` passes; off unless `QUANTA_AI_BRIEF=1`; written only on a button press; cached per Decision | Stub-tested (13 Python, 3 JS tests); not live-tested |
| P1-2 Gateway, phase 1 | `031853c` | `core/marketdata/gateway.py`: `price_bars()` with ordered fallback (Yahoo, then Stooq for unadjusted US requests, split-cliff refused, circuit breaker) and a provenance envelope; Plan Lab, two MCP tools and the ledger loader migrated | Direct yfinance call sites 86 → 82 |

## Second round (2026-11-06, after review)

| Step | Commit | Result |
|---|---|---|
| Live check on yfinance 1.4.1 | `9f9d1c5` | AAPL, JPM, XOM, SPY and RELIANCE.NS analysed live in 2-4 s each with labels, levels, v4 shadow and the control model. RELIANCE.NS (a completed session) was recorded into a scratch ledger with its inputs, model fingerprint and provenance; the four US names were correctly not recorded (today's bar was still trading). Factor download live: 15,918 days through 2026-09-30 |
| Stooq | `9f9d1c5` | **Stooq now answers scripted requests with a JavaScript browser-check page**, so the fallback cannot serve data. The gateway now names that page as a refusal and switches Stooq off after three; README updated. QUANTA does not try to get past the check. The short-term scanner's older Stooq path reads the page as "no data" and keeps trying per ticker (small cost; left as is because it is engine code) |
| Model freeze | `f98eea3` | `python -m tracking.freeze set/release/status`, enforced by `tests/test_model_freeze.py` once a freeze exists, shown on the Evidence page. Opt-in: nothing is frozen yet |
| Browser suite, CI set | — | 66 / 66 passed (desktop dark and phone; includes WCAG 2.1 AA checks on Research and Evidence) |
| Browser suite, full (528 tests, 4 device profiles, live Yahoo) | this commit | 516 passed, 4 skipped, 8 failed. Four desktop failures (Discover and Guide accessibility, Discover links, Guide progress bar) were timeouts under load and pass when re-run alone. The other four were one test failing on every profile, already on `main`: it expected three stylesheets after Plan Lab and News & Events added two more. Updated; its spec now passes 24 / 24 |

## Changed from the plan

1. **The fingerprint was not the cause of the evidence-clock churn.** The audit (02 F-3) said the
   fingerprint resets on wording edits. Replaying all 67 commits since 2026-10-15 against the new
   code: the raw-byte and model fingerprints both change in 19 periods, and even ignoring every
   string literal the syntax tree changes in 20 of 21. The Research engine's logic changed that
   often. The model fingerprint is still in place (it is correct, and future comment, log and CLI
   edits will no longer reset the clock), but the measurable target in 08 (≤ 2 fingerprints per
   10 dates) **cannot be met by code**. It needs a freeze policy; see "Owner decisions" below.
2. **P1-7 (one indicator module) was not done.** The audit counted the plan simulator's ATR as a
   copy; it already delegates to `engine/indicators.py`. The remaining copies differ on purpose:
   `apex_quant/indicators.py` must stay byte-stable for the published validation results to be
   reproducible, and Picks' RSI differs from the shared one only on a perfectly flat series
   (`tests/test_indicator_parity.py` pins that). Changing it would change Picks output for no
   measured gain.
3. **The audit's call-site count was 83; the correct count was 86** (a summing error). Corrected in 02-10.
4. **P2-1 (bull/bear) and E2/E3 (UI panels) were folded into P1-5 and P1-4**, since they cost little once those existed.
5. **The gateway package is `core/marketdata/`, not `core/data/`**: the repository root already has
   a runtime `data/` folder.

## Pending

| Task | Why not done | What it needs |
|---|---|---|
| P1-2 phase 2 | Remaining 82 call sites sit mostly in the three 6-15K-line engines; moving them changes code the golden master covers, so each engine should be its own change | Migrate engine by engine (quotes and profiles next), golden master after each |
| P1-7 | See above | An owner call on whether Picks' flat-series RSI should become undefined (NaN) |
| P2-2 EV calibration | Needs resolved v4 calls; the ledger now records the inputs (P0-4) | About 30 resolved 63-session v4 calls |
| P2-3 Point-in-time universe | Large data-sourcing task (historical S&P 500 membership, delisted prices) | A free source with add/remove dates; 2-3 weeks |
| P2-4 Top-k dropout evaluation | Most useful after P2-3 | — |
| P2-5 Live-path replay | Needs P2-3 and some history of recorded inputs | — |
| P2-6 Constrained what-if weights | Not started | Uses the shrinkage covariance now in place |
| P2-7 Split `app.py` and the engines | Large mechanical refactor | Its own branch, behind the golden master |
| P2-8 FRED macro panel | Not started | A free FRED key |
| P2-9 One regime module | Not started; touches all three engines | — |
| P3-1 … P3-9 | Not started | See 08 |

## Owner decisions this work raises

1. **Freeze the Research scoring code during a test period.** Without a freeze, no model version
   reaches 30 resolved calls on 10 independent dates. Options: freeze `TradingAnalysis_ELITE.py`'s
   scoring and the `core/engine` decision code for a quarter at a time, with changes going into a
   new shadow version instead; or accept that the prospective record cannot decide anything yet.
2. **Merge this branch.** Merging starts a new fingerprint period for every engine, as any engine
   edit does. Doing it once, now, costs one period.
3. **Turn on the AI brief** (`QUANTA_AI_BRIEF=1`, `pip install -e ".[ai]"`, an Anthropic key). Each
   brief is one paid API call; first use should be watched to measure cost and how often the checker
   withholds a brief.
4. **yfinance 1.4.1.** The unit and golden suites pass on it, and the golden fixtures were recorded
   with it, but a live scan on the new version has not been run. Run one before relying on it.
