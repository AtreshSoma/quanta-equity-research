# Live model validation status — 9 November 2026

## Update, 9 November 2026: what the ranking returned as a portfolio

The tests below measure rank correlation. `core/apex_quant/panel_portfolio.py`
asks the next question of the same point-in-time panel (`research_v2_pit_oos`,
42 names, 2010-12-30..2026-10-21): what did holding the top-ranked names
return after costs, against simply holding all of them? One configuration,
fixed before any result was read: top and bottom fifth by score (about 8
names), equal weight, rebalanced every 4 weekly panel dates, traded at the
next panel date's close after the signal, 5.5 bps a side (half the Research
round-trip cost), 198 periods.

| Portfolio | CAGR | Equal-weight benchmark | Active per year | t | Sharpe | Max drawdown | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Top fifth by composite | +16.6% | +16.1% | +0.9% | +0.42 | 1.01 | -28.6% | 6.8x a year |
| Same, 25 bps a side | +13.5% | +16.1% | -1.7% | -0.80 | 0.85 | -30.1% | 6.8x a year |
| Bottom fifth by composite | +12.6% | +16.1% | -2.7% | -1.21 | 0.83 | -33.1% | 6.2x a year |
| Top fifth by 12-1 momentum (free control) | +24.7% | +16.4% | +7.7% | +2.92 | 1.35 | -19.4% | 2.9x a year |

Reading:

- **The composite's top fifth did not beat holding every name.** +0.9% a year
  is inside its noise (t +0.42) and turns negative at 25 bps a side. The
  ordering is in the right direction (top ahead of bottom) and not
  significant. This agrees with the NO_EDGE status; it does not change it.
- **Free 12-1 momentum beat it on this sample**, with less than half the
  turnover. The universe is 42 present-day large caps, so momentum's figure is
  flattered by survivorship and is not a tradable estimate; the comparison is
  the point. `engine/factor_baseline.py` already records this control in
  shadow beside every Research call.
- **The decision bands are not calibrated.** Mean 63-session return against
  the same date's average name: Buy +1.47% (t +1.06), Accumulate -0.18%,
  Hold +0.33%, Reduce -0.83%, Sell +4.01% (14 dates). No band is
  distinguishable from zero and the order is not monotone (Spearman -0.10).
  With sentiment neutral the panel's scores sit closer to 50 than live ones,
  so this tests the cut-offs on a compressed scale; 19 rows reach 75.
- **Regime-adaptive weights add nothing measurable.** Per-date IC of the live
  regime tables minus one fixed table, paired: -0.002 at 21 sessions (95% CI
  -0.005 to +0.001), +0.001 at 63 (CI -0.003 to +0.005). The evidence does not
  support adaptive weighting; v3's single fixed table is the simpler model.
  The live default is unchanged: replacing it is a model change that needs its
  own pre-registration.

Absolute returns here carry survivorship bias; drawdown is measured at
rebalance dates; the live gates, stops and sizes are not simulated. Timing,
cost and no-look-ahead properties are pinned by
`core/apex_quant/tests/test_panel_portfolio.py`.

    cd core
    python -m apex_quant.run portfolio --panel research_v2_pit_oos

## Update, 22 October 2026: the fundamental legs are now tested

Every earlier test held the fundamental, valuation and quality legs neutral,
because yfinance only serves today's figures. `core/apex_quant/pit_fundamentals.py`
now rebuilds them from SEC XBRL `companyfacts`, reading only facts **filed
before** each date (restatements count from the day they were filed). The
live analyzers score the rebuilt payload with their own code
(`FundamentalAnalyzer.analyze_info`, `ValuationAnalyzer`, the scanner's
fundamental and quality cards). Sentiment, insider flow, analyst targets,
forward EPS and short interest still have no point-in-time source and stay neutral.

| Model | Test | Result |
| --- | --- | --- |
| `elite.research.v2` | technical, fundamental, valuation, quality (89% of weight), live regime weights, 42 names, weekly, 2010-12-30..2026-10-21; 95% of rows had filings | IC +0.020 (21d, t +0.87), +0.041 (63d, t +1.42); Q5-Q1 +0.28% / +1.36%, monotone at 63d. **NO_EDGE** (positive, not significant). Beats its own technical leg (CIs above zero); not distinguishable from 12-1 momentum |
| `shortterm.composite` | technical, momentum, pattern, fundamental, quality (90% of weight), 2,020 trades, 2016-2026 | -0.20% per weekly entry cohort vs SPY (95% CI -0.44% to +0.02%); -0.26% vs random entries with the same exits (CI -0.59% to +0.04%). **NO_EDGE**, leaning negative |

Reading: the fundamental (IC +0.041 / +0.061) and quality (+0.037 / +0.046)
legs carry the research model's ranking power; valuation (-0.007 / -0.002) and
technical (-0.003 / +0.001) carry none. With fixed SIDEWAYS weights instead of
the live regime tables the result barely moves (IC +0.022 / +0.040, t +0.94 /
+1.23), so the change from the earlier test comes from the fundamental legs,
not from the weighting. For the short-term scanner, adding the
same kind of evidence made entries worse, not better. Neither result
validates a model, and the static present-day universe (survivorship bias)
still applies to both.

Reproduce (needs `QUANTA_SEC_CONTACT`; SEC facts cache under `core/apex_quant/_cache_research/sec/`):

    cd core
    python -m apex_quant.run elite-oos --name research_v2_pit_oos --pit-fundamentals --regime auto
    python -m apex_quant.run baselines --panel research_v2_pit_oos
    python sp_shortterm_backtest.py --universe liquid --pit-fundamentals --start 2016-01-01 --end 2026-09-30 --output-dir apex_quant/_reports/shortterm/pit

## Update, 13 October 2026

The current models have now been measured, with the scope each test allows:

| Model | Test | Result |
| --- | --- | --- |
| `elite.research.v2` | technical leg (33% of weight), 42 names, weekly, 2010-12-30..2026-09-30 | IC -0.003 (21d, t -0.26), +0.001 (63d); Q5-Q1 spread -0.24% / -0.12%. **NO_EDGE.** Not distinguishable from 12-1 momentum or random on identical cross-sections |
| `shortterm.composite` | price core (58% of weight) replayed bar by bar, 1,284 trades, 2016-2026 | +0.03% per weekly entry cohort vs SPY (95% CI -0.29% to +0.36%); +0.11% vs random entries with the same exits (CI -0.27% to +0.49%). **NO_EDGE** |
| `elite.composite` (predecessor) | as below, re-read on identical cross-sections | 21d IC -0.029, significantly negative after FDR control; reliably worse than 12-1 momentum |

The `elite.research.v2` row carries the caveat recorded below: its risk-leg
removal was chosen after reading the predecessor's result on the same sample,
so the period is not fully out of sample for it. Both entries fail closed in
`apex_quant/_reports/evidence_registry.json`. The prospective ledger, now
fingerprinted per call and published as a hash-chained record, remains the
only path to evidence that is out of sample by construction.

## Verdict (27 September 2026)

**No predictive edge is demonstrated for the current live model.** The deep
(Research) engine's evidence key is now `elite.research.v2`, which fails closed
as `UNTESTED`. The 2026-09-27 audit changed its scoring and levels: a
two-sided valuation band, a quality leg independent of the fundamental leg,
no analyst-rating term in sentiment, no oversold bonus inside the trend score,
and reward/risk measured to a 63-session target pro-rated from the 12-month
valuation target (stops 1.5-3 ATR). No earlier study measured this model.

Lineage is published beside the status (API `evidence.lineage`, the Evidence
page and the tear-sheet hero):

| Version | Change | Out-of-sample result |
| --- | --- | --- |
| `elite.composite` | original composite | **NO_EDGE** (through 2026-08-25) |
| `elite.alpha_only.v1` | risk leg removed from the ranking | never tested |
| `elite.research.v2` | 2026-09-27 audit remediation | never tested |

The risk-leg removal was chosen after reading the predecessor's out-of-sample
result, so 2010-2026 is no longer out-of-sample for any successor. The ledger
records each call's model version and never pools versions in one statistic;
its t-statistics use only publication dates whose horizons do not overlap.

The production ledger has no prospective resolved sample in this checkout.
Published deep calls are measured at 5/10/30/63 sessions; the 63-session
decision is the displayed horizon. Scanner calls use their displayed position
horizon and short-term setups use their expected holding horizon. Outcomes now
enter at the first session open **after publication** and compare against the
benchmark over matching dates. Prior as-of-close results are excluded from
the new summary statistics.

## What the available study does measure

`core/apex_quant/_reports/elite_oos.json` and `elite_panel.parquet` contain a
32,825-row, 42-ticker, static present-day universe. The last tested date is
2026-08-25. It reconstructs technical and risk components of the **previous**
model: 40% of its SIDEWAYS composite. Historical fundamental, valuation,
quality, and sentiment inputs are constant neutral values because there is no
point-in-time source. The same panel has 1,456 dates but only 42 surviving
tickers, so neither its absolute returns nor a new model's performance can be
treated as survivorship-aware validation.

The trade-level returns in these reports also predate the corrected execution
chronology and are invalid for performance claims; see
[`INVALIDATED.md`](../core/apex_quant/_reports/INVALIDATED.md). The factor ICs
below are descriptive diagnostics for the old score panel, not simulator results.

| Previous-model diagnostic | 21 sessions | 63 sessions |
| --- | ---: | ---: |
| Composite cross-sectional mean IC | -0.0225 | -0.0339 |
| Non-overlapping-date IC t-statistic | -1.41 | -0.96 |
| Positive-IC date share | 44.6% | 47.1% |
| Technical component mean IC | -0.0069 | -0.0004 |
| Risk component mean IC | -0.0447 | -0.0966 |

These statistics show no established edge. The risk component's negative IC
does not prove that high volatility is a tradable alpha signal: the survivor
universe can make beta and volatility look favorable after failures have been
excluded. In this panel, Spearman correlation is -0.733 between risk score and
annualized volatility, and -0.462 between risk score and beta. Technical score
correlates 0.708 with RSI. These are diagnostics for the old, partial model,
not independent evidence for new factor weights. No weights were fit to these
results.

## Required to validate the current decision path

1. Store versioned, as-reported fundamentals, estimates, filings and news with
   their **available-at** timestamps. Today's Yahoo `info` fields cannot be
   replayed into past decisions.
2. Use historical constituents including delisted names, corporate actions,
   and a point-in-time sector classification. Compare each realized call with
   both SPY and the appropriate sector benchmark on identical entry/exit bars.
3. Replay the actual live decision code, including missing-data, risk, scanner
   reconciliation and publication gates. Enter no earlier than the next
   tradable open after the decision timestamp; account for costs and overlap.
4. Report 5/10/30/63-session sample sizes, factor IC and pairwise correlation,
   benchmark hit rate, excess return, drawdown, and confidence intervals with
   clustering by decision date. Keep a held-out period and do not tune weights
   against it.

Until these inputs and enough prospective calls exist, the UI and APIs must
label buy-tier output **CANDIDATE**, not a validated recommendation.

## 2026-10-29: v3 pre-registered

A third version of the Research model, `elite.research.v3`, is pre-registered in
[`docs/preregistration/elite_research_v3.md`](preregistration/elite_research_v3.md). Its factor
weights are one fixed table, re-set by a rule written down before any result for it exists:
fundamental 0.40, quality 0.30, technical 0.10, valuation 0.10, sentiment 0.10. The rule was
derived from the 2026-10-22 leg ICs, so the 2010-2026 sample and the survivor universe are
in-sample for v3.

- Status: **UNTESTED** (no registry entry). It fails closed like any untested key.
- The default live model stays `elite.research.v2` (`QUANTA_RESEARCH_MODEL` selects v3).
- v3 is scored in shadow on every Research call and recorded under the ledger engine
  `elite_v3_shadow`, from 29 October 2026. Its record is kept apart from v2 statistics.
- The primary test is prospective: 63-session per-date Spearman IC of the v3 score against
  forward excess return, with a pass at t of 2.0 or more, on at least 30 resolved calls over at
  least 10 non-overlapping dates. Nothing is decided before those minimums hold.
