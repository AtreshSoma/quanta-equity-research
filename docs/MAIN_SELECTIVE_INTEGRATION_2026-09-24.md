# Selective integration from main and the redesign line

Updated 2026-09-25. Supersedes the 2026-09-24 version of this note.

## Sources compared

| Ref | What it contains for QUANTA |
| --- | --- |
| `origin/main` (`18718f5`) | Quote board, session quote, dependency and import fixes. The other commits since the merge base touch AlphaLens only. |
| `origin/quanta-review-redesign` (`7543a85`) | Built on `origin/main`. Single design-system UI, web-layer hardening, engine review fixes, redacted golden fixtures. Its UI is byte-identical to the standalone `quanta-review/main` compared on 2026-09-24. |
| `audit-remediation` | Authoritative Decision, evidence gate (NO_EDGE / UNTESTED), shared ledger with next-session-open, benchmark-aligned outcomes, corrected factor and R:R logic, validation safeguards. |

Everything from main arrives through the redesign line, so the table below covers both.
Integration used a three-way merge per file where the current branch had not diverged.
Where it had diverged (`SP_ELITE_PRO.py`), each feature was ported by hand.

## Scanner features

| Feature | Current equivalent | Decision | Reason |
| --- | --- | --- | --- |
| Cross-sectional rescoring that rewrites `overall_score`, then re-derives action and size (`_reconcile_rescored_analysis`) | `ranking_score` is kept separate and never gates or sizes | Reject | The current design avoids the stale-action problem this code repairs, without a second pass over actions and dollars. |
| Research ranking (`get_stock_picks`) with its own STRONG BUY / BUY / HOLD rating | Decision label on every published pick | Reject | It is a second verdict beside the Decision. No UI consumed it. |
| Required, optional and deferred provider gaps (`_ScannerTicker`) | Provider circuit, resume-after-cooldown, data_status | Defer | Coupled to the rejected ranking mode. Needs its own contract with the Decision's data-status rules. |
| Adaptive AIMD pacer and bulk history prefetch | One history request per ticker, unpaced | Integrated | Cuts the most frequent provider call to a few chunked requests and backs off on 429. Tests disable the pacer and prefetch via environment variables. |
| Wall-clock budget on the pre-filter download; slow ≠ rate limit | Unbounded `yf.download` | Integrated | A throttled provider blocked scans for 5–8 minutes. Slowness no longer opens a global cooldown. |
| Sector cap on thin pools | **Current auto-relaxed the cap to 100% when ≤ 4 names survived** | Integrated (opt-in) | The diversification mandate now binds by default. Concentration requires `QUANTA_ALLOW_SECTOR_CONCENTRATION=1` or an explicit argument. The 2026-09-24 note had this backwards. |
| Minimum score applied after bounded adjustments, with an unreachable-floor short circuit | Early cut-off on the base score | Integrated | Before, negative evidence could reject a name but positive evidence could not rescue it. The short circuit is outcome-identical and skips three provider calls per hopeless name. |
| Price from the completed session close | `fast_info.last_price` (intraday) mixed with completed-bar levels | Integrated | Price, stop and target now come from the same completed bar. |
| Fail-closed drawdown multiplier | `float(x or 1.0)` turned a halted `0.0` into full size | Integrated | — |
| Conservative rank for unknown actions | Unknown ranked 99, above STRONG_BUY, at one call site | Integrated | — |
| Gate #1 respects the resolved action and invalid rows | Signal only | Integrated | — |
| Conviction fallback, flagged | Silent fallback | Integrated | Survivors are published with a stated reason, and a gate statistic records the fallback. |
| No percentile below a real sample (`ZSCORE_MIN_UNIVERSE`, default 30) | z-scores over ≥ 5 names; a fabricated 50th percentile otherwise | Integrated, adapted | Thin pools still get regime-weighted ordering through `ranking_score`, without z-scores. |
| Constituent-table sanity floors; fallback substitutes rather than supplements | A partial parse was cached as the index | Integrated | — |
| Pre-momentum web scan used the full universe | CLI-only list | Integrated | — |
| Undated or future-dated insider rows | Counted as recent | Integrated | Current already ignored awards and gifts, and already signed catalysts correctly. Both are better than in the redesign. |
| In-scan walk-forward replay | Ran on every scan | Opt-in (`QUANTA_RUN_DIAGNOSTIC_REPLAY=1`) | Replaying post-selection survivors is not evidence, and the replay costs provider quota. |
| Deep confirmation during a provider cooldown | Waits out the cooldown and retries once | Kept current | The current approach attempts confirmation instead of skipping it. The per-pick mode (`fast+insider` / `full`) was added. |
| UTF-8 exports, console-safe printing, exit codes, key-redacted API logging | — | Integrated | — |

## Deep analyzer and modules

These came in through a clean three-way merge. One conflict was resolved: the candle guidance applies both the redesign's ceiling and the current branch's non-allocating-action rule.

- Piotroski counts only reported inputs and shows N/A below 5 inputs. Altman uses the correct public or private variant.
- Target confidence measures dispersion, not source count. The relabelled 30-day Monte Carlo "target probability" was removed.
- Kelly sizing uses the published target. Portfolio exposure limits rescale every derived field and never fabricate holdings.
- Research runs no longer write compliance "executed trade" records.
- Earnings-surprise units come from the provider schema, not from value magnitude.
- The SEC parser moved to the submissions JSON. A 404 no longer trips the circuit breaker.

## Web UI

The redesign's design system replaces five competing stylesheets (about 19k lines, 1,800 `!important`). The current branch's Decision wiring (`decision.label`, `caveat`, `action`) was carried through the merge.

The seven "regressions" reported on 2026-09-24 were reviewed one by one:

| Check | Outcome |
| --- | --- |
| Decision Brief title and action plan | Restructured into Regime, Playbook and Risk panels with the same facts. Not a regression. |
| Backend risk caps | Still rendered, as an inline flag. |
| Low-confidence banner | Now a gauge with a stated reason. |
| Signal Check rows | One bar per factor. |
| Market movers timeout | **Regression, fixed.** A timed-out feed now says so instead of "no usable quotes". |
| Catalyst empty state | More honest: "no dates returned" is not an all-clear. |
| Monte Carlo warning | The old test encoded a scale bug. `expected_return` is in percent. |

Changes beyond both branches:

- Tear-sheet hero: shows the Decision's horizon and evidence status, and its caveat.
- Fast-preview banner: no longer promises a "validated verdict".
- Scan results:
  - watch-only rows quote a "Watch level", not "Buy near";
  - execution caveats appear only when something is funded;
  - the funnel no longer calls deep-downgraded picks "confirmed";
  - the Scanned tile shows the scanned universe;
  - scan-size labels match the engine's limits.
- Mandate rescaling keeps the frozen Decision's sizing block in sync. The action is never re-derived.
- Market ribbon: names the ETF behind index labels. The Street consensus column is labelled "Street rating".
- Track Record: a new view and `/api/track-record`, both read-only over the shared ledger and the evidence gate.
- Top bar: fixed a height ratchet (CSS read the height that JS measured from it) and an unplanned wrap between 1281 and 1420 px.

## Tests adopted

The redesign's test files were adopted where their behaviour was integrated. The files for rejected features were not adopted: rescoring, research ranking and `_ScannerTicker`. The golden snapshots were re-derived from the redesign's redacted fixtures. Every changed field traces to an integrated fix or to a fixture that records more data. For example, RELIANCE.NS was INVALID only because its old fixture was incomplete.

Passing tests establish compatibility within the tested paths. They do not establish predictive edge, and every engine remains `UNTESTED` (see `LIVE_MODEL_VALIDATION_STATUS.md`).
