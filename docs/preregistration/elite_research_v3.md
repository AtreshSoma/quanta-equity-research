# Pre-registration: `elite.research.v3`

**Date:** 29 October 2026
**Model:** Research (deep analysis), `core/TradingAnalysis_ELITE.py`
**Status:** Pre-registered. Not validated. Runs in shadow beside the live model `elite.research.v2`.

## 1. Hypothesis

The out-of-sample test of `elite.research.v2` on 22 October 2026 measured an
information coefficient (IC) for each factor leg at 21 and 63 sessions. The
fundamental and quality legs carry the ranking signal. The valuation and
technical legs carry none. The live model gives those legs most of its weight
through regime tables, and fixed SIDEWAYS weights did not change the result.

Hypothesis: weighting the Research score toward the measured legs, and away from
the unmeasured ones, gives a 63-session ranking that predicts forward excess
return better than `elite.research.v2`.

This hypothesis is tested only prospectively (section 5). The historical result
that motivated it cannot test it.

## 2. The data that motivated the weights, and the in-sample caveat

Per-leg IC from the 2010-12-30 to 2026-10-21 point-in-time test of
`elite.research.v2` (42 names, weekly, fundamentals rebuilt from SEC filings
known on each date; `docs/LIVE_MODEL_VALIDATION_STATUS.md`, 22 October 2026):

| Leg | IC 21 sessions | IC 63 sessions |
| --- | ---: | ---: |
| Fundamental | +0.041 | +0.061 |
| Quality | +0.037 | +0.046 |
| Valuation | -0.007 | -0.002 |
| Technical | -0.003 | +0.001 |
| Sentiment | no point-in-time source | no point-in-time source |

**Caveat.** The weights in this document were chosen after reading those
results. The 2010-2026 sample and the 42-name present-day survivor universe
therefore **are in-sample for v3**. They say nothing about whether v3 works. Only
calls published after this date, and resolved by the ledger, can test it.

## 3. The rule, and the resulting weights

The rule, verbatim:

> "Legs whose measured IC was positive at both the 21-day and the 63-day horizon in
> the 2026-10-22 point-in-time test share 70% of the weight in proportion to their
> 63-day IC (fundamental 0.061, quality 0.046). Every other leg keeps a 10% floor,
> so no input is discarded."

Result, one fixed table for every regime:

| Leg | Weight |
| --- | ---: |
| Fundamental | 0.40 |
| Quality | 0.30 |
| Technical | 0.10 |
| Valuation | 0.10 |
| Sentiment | 0.10 |

Arithmetic: 0.70 x 0.061 / (0.061 + 0.046) = 0.399, shown as 0.40; 0.70 x 0.046 /
(0.061 + 0.046) = 0.301, shown as 0.30. The floors are technical, valuation and
sentiment, 0.10 each.

Which legs qualify, from the table in section 2: fundamental (+0.041, +0.061) and
quality (+0.037, +0.046) are positive at both horizons. Technical (-0.003, +0.001)
is negative at 21 days, valuation is negative at both, and sentiment was never
measured, so those three take the floor.

**Wording note (recorded before registration).** A first draft of this rule said
"positive measured 63-day IC". Read literally, that admits technical's +0.001 and
gives technical about 0.7% of the 70%. The intended reading, the one the
22 October document states ("technical carries none"), and the table the code
uses, all exclude it; the wording above says so explicitly. The clarification
was made on 29 October before this document was committed and with no new data
seen, so it is a drafting fix, not a tuning decision.

Code: `Config.RESEARCH_V3_WEIGHTS` in `core/TradingAnalysis_ELITE.py`.

## 4. What is unchanged

Everything except the factor weights is identical to `elite.research.v2`: the
factor scores themselves, the score bands (`engine/score_bands.py`), levels,
targets, reward/risk hurdles, overrides, the action ladder, sizing, the
evidence gate and the ledger. One change at a time.

## 5. The test

**Primary (prospective, decides the outcome).**

* Sample: calls recorded from 29 October 2026 onward whose evidence strategy is
  `elite.research.v3`. These are the live rows when v3 is selected, and the
  `elite_v3_shadow` rows while v2 is live. Only next-session-open entries count
  (the ledger's own rule). Calls from different code fingerprints are not pooled:
  any change to a file that affects v3 scoring is a new version (section 6).
* Horizon: 63 sessions.
* Statistic: for each publication date, the Spearman rank correlation between the
  v3 score and the forward excess return over the benchmark, across the calls
  published that day. Then the mean of these per-date ICs over **non-overlapping**
  publication dates (the ledger's own independence rule for a 63-session horizon),
  and its t-statistic across dates.
* Minimums (the ledger's gates): at least 30 resolved calls, spread over at least
  10 independent dates.
* **Pass:** IC t-statistic of at least 2.0.
* Procedure in code, fixed before the first evaluation: `core/tracking/prospective_ic.py`
  (tests: `tests/test_prospective_ic.py`). Run from `core/`:
  `python -m tracking.prospective_ic --engine elite_v3_shadow --compare elite`
  (use `--engine elite` once v3 is the live model). It reads the ledger only,
  reports each code fingerprint separately and gives its verdict on one.

**Secondary (reported, does not decide the outcome).**

* Paired comparison with `elite.research.v2` on the same dates and tickers: the
  difference in per-date IC between the two versions, with its interval.
* Hit rate of v3 calls (a Bullish call that beat the benchmark, a Bearish call that
  lagged it) with a Wilson 95% interval, reported beside v2's.

**Stopping rule.**

* Evaluate no earlier than the minimums are met. Any look at the result before
  then counts as an evaluation.
* Evaluate once. Report the result whether it passes or fails.
* Make no change to v3 during evaluation. Any change to weights, factors, bands,
  levels, gates or code that affects v3 scoring makes a new version (`v4`) with its
  own pre-registration.
* The evaluation procedure (the per-date IC code) must be committed before the
  first evaluation, and its output recorded in this document. It is not part of
  this change.

## 6. What happens on each outcome

* **Pass (t at least 2.0).** The result is a prospective point for v3, not a
  validation. The status may become `PARTIAL` only through the evidence
  registry writer (`core/apex_quant/evidence_registry.py`), with its numbers and
  caveats. It may never become `VALIDATED` on this result alone. Validation needs
  the full list in `docs/LIVE_MODEL_VALIDATION_STATUS.md` ("Required to validate").
* **Fail.** Retire v3. Record `NO_EDGE` through the registry writer. End the shadow
  record. Any later model is a new version with a new pre-registration.
* **Inconclusive** (minimums not met by the planned end date). Do not evaluate it
  early. Extend the window and keep the rule in section 5.

## 7. Trial count (multiple-testing control)

v3 is one variant for the strategy key `elite.research.v3`. It is registered only
through the explicit command below, which writes `core/apex_quant/_reports/trials.json`.
Tests never write that file.

```
cd core
python -m apex_quant.trials elite.research.v3 research_v3_weights_2026-10-29 --on-date 2026-10-29
```

## 8. Live switch and the default

* **Shadow (default).** `elite.research.v2` is live. v3 is scored from the same
  components on every Research call and recorded under the engine
  `elite_v3_shadow`, with its own evidence key, status and score.
* **Live v3.** Set `QUANTA_RESEARCH_MODEL=v3` before starting the app. The live
  call then uses v3 weights and carries the key `elite.research.v3`. It shows as
  `UNTESTED`, so every buy-tier call carries the "Unproven model" qualifier.
* The default stays `v2`. An untested model must not silently replace the current
  one. Switching live is an owner decision, and it changes which result the
  Decisions cite. It does not change the evidence, which stays `UNTESTED` until a
  registry entry says otherwise.
* Any other value of `QUANTA_RESEARCH_MODEL` keeps `v2`.

## 9. What the shadow call is not

The shadow score is not QUANTA's call. It applies no evidence gate, level,
reward/risk hurdle or override, and it is labelled as shadow wherever it is shown.
Its band is read from the same score bands as the live model.
