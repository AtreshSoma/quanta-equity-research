# Pre-registration: `elite.research.v4`

**Date:** 29 October 2026
**Model:** Research (deep analysis), `core/TradingAnalysis_ELITE.py` with `core/engine/research_v4.py`
**Status:** Pre-registered. Not validated. Runs in shadow beside the live model `elite.research.v2`
and the shadow `elite.research.v3`. Neither is changed by it.

## 1. Hypothesis

The Picks review of 29 October 2026 (kept outside the repository)
found three defects in how the Research model turns its analysis into a call:

1. **The reward/risk hurdle is structurally out of reach.** The horizon target is
   63/252 of the 12-month valuation upside, but the stop is a full 2.75 x ATR. To
   clear the 1.8 funding hurdle a stock needs about 19.8 x its ATR% in 12-month
   upside (about +50% at an ATR of 2.5%). All 7 calls in both scans that day failed
   it, while the Street saw +17% to +29% on each.
2. **The intrinsic value misprices asset-light companies.** The Graham Number
   (sqrt(22.5 x EPS x BVPS)) is averaged equally with the DCF. Buybacks shrink book
   value, so CSCO came out near $66 against a $110 price and a $136 consensus.
3. **Estimate revisions are not a leg.** The scanner reads them only as a capped
   +/-1.5 point nudge, and the Research model not at all. Revisions are the core of
   the Zacks Rank and one of Seeking Alpha's five factor grades.

Hypothesis: a model that (a) adds a revisions leg, (b) values each company with a
method suited to its sector, and (c) gates new money on expected value from today's
price makes calls whose 63-session excess returns rank better than `elite.research.v2`'s.

**This is a bundle of three changes, not one.** A pass cannot say which change
earned it. That is accepted because the hurdle defect alone leaves the model with
almost no buy-tier calls to score. Any later attribution needs one version per change.

## 2. In-sample caveat

These changes were chosen after reading one day of scans and published research
practice (Zacks, Seeking Alpha Quant, Morningstar). No historical test motivated
the numbers, so no historical test can confirm them. Only the prospective test below decides.

## 3. The model

Constants: `core/engine/thresholds.py`, group `RESEARCH_V4`. Code: `engine/research_v4.py`,
`engine/fair_value_v4.py`, `engine/expected_value.py`.

* **Weights.** v3's weights x 0.8, plus revisions at 0.20: technical 0.08,
  fundamental 0.32, valuation 0.08, quality 0.24, sentiment 0.08, revisions 0.20.
  A leg that was not measured is left out and the rest renormalise. A fund keeps
  only technical and sentiment, as in v3.
* **Revisions leg.** `modules/earnings_revision_scoring.revision_score` (0-10) x 10.
  The inputs are the 30-day net analyst revisions, the 30-day change in the forward
  estimate and the average surprise. An `UNKNOWN` read leaves the leg out.
* **Valuation leg.** 50 + 50 x clip(upside / 0.40, -1, 1), on the 12-month target below.
* **12-month target.** Consensus x 0.5 (0.25 under 3 analysts) plus intrinsic value x 0.3,
  renormalised over the sources present. Intrinsic value by sector:
  * Banks, insurers, capital markets: justified P/B = (ROE - 3%) / (10% - 3%),
    with ROE clipped to 0-25%.
  * Energy: FCF per share / 8%, averaged with the DCF when one exists.
  * Others: the DCF, plus Graham only where 0 < P/B <= 3.
  * Any value outside price x [1/3, 3] is dropped.
* **Expected-value gate.** Log price follows Brownian motion over 63 sessions,
  starting at today's price.
  * The stop keeps the live setup's distance (entry - stop), re-anchored to the price.
  * The target barrier is the 12-month target.
  * Volatility = (ATR / price) / 1.25.
  * Expected price growth = 0.5 x ln(target / price) / 252 per session. The log drift
    is that growth less sigma^2/2.
  * The chances of target, stop and neither, and the expected return net of a
    round-trip cost, are computed analytically with an eigenfunction series.
  * The gate passes when the expected return is positive and at least 0.10 of the
    risk (stop distance plus cost).
  * A buy-tier score band that fails the gate is recorded as `WATCHLIST`.

**Known property, recorded before any result.** Under this model a stop and a target
add no value by themselves. A name whose target equals its price nets minus the
cost however volatile it is (`tests/test_expected_value.py::test_no_upside_is_a_fair_game_net_of_cost`).
The expected return is therefore roughly the haircut upside accrued over the
expected holding time. The target and stop probabilities are reported for the
reader; the gate's information is the valuation upside. The drift haircut (0.5) is
a judgement, because consensus targets run optimistic.

## 4. What is unchanged

The live model, its levels, hurdles, overrides, sizing, the evidence gate, and v3
as a shadow are all unchanged. The golden-master snapshots differ from before v4
only by the added `decision.shadow_v4` field (verified field by field on 29 October 2026).

## 5. The test

Exactly as v3's section 5 (sample, 63-session horizon, per-date Spearman IC over
non-overlapping dates, at least 30 resolved calls over 10 or more independent dates,
pass at an IC t-statistic of at least 2.0), with one change: the engine is
`elite_v4_shadow` and the paired comparison is against both `elite.research.v2` and
`elite_v3_shadow`. The procedure is `core/tracking/prospective_ic.py`:
`python -m tracking.prospective_ic --engine elite_v4_shadow --compare elite`.

**Secondary (reported, does not decide):**

* The hit rate of v4 buy-tier calls, with a Wilson 95% interval.
* The share of v4 buy-tier calls that the live hurdle rejected on the same day,
  which measures defect 1 directly.

**Stopping rule:** v3's. Evaluate once, after the minimums are met. Any change to v4's
weights, legs, targets, gate or the code that scores them is `v5`, with its own
pre-registration.

## 6. Outcomes

v3's section 6 applies, with `v4` in place of `v3`.

## 7. Trial count

Registered on 30 October 2026, before any evaluation, as variant
`research_v4_bundle_2026-10-29` (the bundle is dated by when it was written, the
registration by when it was entered). `elite.research.v4` now counts 1 variant in
`core/apex_quant/_reports/trials.json`. The command used:

```
cd core
python -m apex_quant.trials elite.research.v4 research_v4_bundle_2026-10-29
```

## 8. Live switch

v4 is shadow only. `QUANTA_RESEARCH_MODEL` accepts `v2` and `v3`, and `v4` is not
yet a choice. Making it one needs the live call to read v4's target and gate,
not only its score. That is a separate change, decided by the owner after section 5.

## 9. What the shadow call is not

It is not QUANTA's call. It is recorded under its own engine and evidence key
(`UNTESTED`), and it never changes the live action, levels, sizing or Picks funding.
