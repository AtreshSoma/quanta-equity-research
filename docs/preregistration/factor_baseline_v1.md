# Pre-registration: `factor.baseline.v1` (control model)

**Date:** 6 November 2026
**Model:** free-factor control, `core/engine/factor_baseline.py`, scored from the Research
engine's own bars and quality leg (`core/TradingAnalysis_ELITE.py`, `factor_baseline_payload`)
**Status:** Pre-registered. Not validated. Runs in shadow beside the live model
`elite.research.v2` and the shadows `elite.research.v3` and `elite.research.v4`. It changes
none of them, and no gate, level or label reads it.

## 1. Purpose

This is a **control**, not a candidate for the live call. QUANTA's own backtests found the Research
composite "not distinguishable from 12-1 momentum" on identical cross-sections
(`core/apex_quant/_reports/factor_baselines_research_v2_pit_oos.txt`), and the fundamental and
quality legs carried its measured ranking power (`docs/LIVE_MODEL_VALIDATION_STATUS.md`,
22 October 2026). The prospective record should therefore compare each Research version with
the cheapest model that captures the same evidence: 12-1 momentum plus the quality leg.

Question: over the same calls, does `elite.research.v2` (or v3, v4) rank 63-session excess returns
better than this control?

## 2. In-sample caveat

The choice of legs follows the 2026-10-22 leg ICs on the 2010-2026 survivor universe, so that
sample is in-sample for this model too. Only the prospective record decides.

## 3. The model

Constants: `core/engine/thresholds.py`, group `FACTOR_BASELINE`.

* **Momentum leg.** 12-1 return: close about 30 calendar days ago over close about 365 calendar
  days ago, minus one, read with `Series.asof` exactly as `apex_quant/factor_baselines.py` does
  (a test pins the two equal). Mapped to 50 + 50 x clip(return / 0.50, -1, 1).
* **Quality leg.** The Research engine's `quality_score` (0-100), unchanged.
* **Score.** 0.5 x momentum + 0.5 x quality. Funds have no company quality and are scored on
  momentum alone. No momentum (under a year of history, or a bad price) means no call.
* **Band.** `engine.score_bands.band_for(score)`, the same table the other shadows use.

## 4. Recording

Each Research call that the ledger records also records this model under the ledger engine
`factor_baseline_shadow`, strategy `factor.baseline.v1`, at the same 5/10/30/63-session horizons,
with the same session and freshness gates. Its rows carry their own model fingerprint, which
covers `engine/factor_baseline.py`, `TradingAnalysis_ELITE.py` and every module those import.

## 5. Test

The same prospective test as v3 (`core/tracking/prospective_ic.py`):

* Per publication date, Spearman IC between the score and the 63-session forward excess return
  over the benchmark, across that day's calls.
* Mean of those ICs over non-overlapping dates, and its t-statistic.
* Minimum before any verdict: 30 resolved calls on 10 independent dates, within one model fingerprint.
* Comparison: run `python -m tracking.prospective_ic --engine elite --compare factor_baseline_shadow`
  from `core/`. The composite earns its complexity only if its IC is higher than the control's on
  the same dates.

Nothing is decided before the minimums hold. A result for this control never changes a live label.
