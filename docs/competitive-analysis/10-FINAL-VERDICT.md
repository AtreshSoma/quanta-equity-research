# 10 — Final verdict

## Current winner

**QUANTA, 6.28 / 10** on the requested weighted rubric (OpenBB 5.70, Qlib 5.36, TradingAgents 4.61).
With all twelve categories weighted equally QUANTA still leads, narrowly (6.25 vs OpenBB 6.17).

That result needs a plain reading. The rubric rewards an end-to-end product, and QUANTA is the
only one of the four that is a product: data in, one sized and explained decision out, recorded and
checked later. It does **not** mean QUANTA has the best data, the best models or the best AI. On
those, each competitor is ahead:

| Category | Winner | Margin over QUANTA |
|---|---|---|
| Data quality and integrations | OpenBB (9) | +4 |
| Research | **QUANTA (8)** | — |
| AI and agents | TradingAgents (8) | +4 |
| Quant models | Qlib (9) | +3 |
| Backtesting | Qlib (9) | +2 |
| Portfolio and risk | **QUANTA (8)** | — |
| Discovery | **QUANTA (7)** | — |
| UI/UX | **QUANTA (8)** | — |
| Architecture | OpenBB (8) | +4 |
| Performance | Qlib (8) | +2 |
| Security and testing | QUANTA / OpenBB (8) | tie |
| Extensibility | OpenBB (10) | +6 |

## Realistic competitive position

QUANTA is a well-governed personal research workstation whose decisions are honest about having
no proven edge. Its moat is not data or models, both of which it gets from free sources and simple
composites. Its moat is **discipline**: one decision object, coded blockers, evidence flags,
pre-registration, shadow models and a tamper-evident ledger. None of the competitors has that.

Its main risk is that the discipline cannot pay off as built. Three of its own mechanisms work
against each other today:

1. The scoring code changes faster than a test can finish: 12 publication dates produced 23
   fingerprints, and no model is near its pre-registered sample. (This audit first blamed the
   fingerprint for hashing wording; a replay of the commit history showed the logic itself
   changed that often. See [11](11-IMPLEMENTATION-STATUS.md).)
2. The fingerprint is also too narrow in places (v4 shadow; all `modules/`), so a period can
   silently mix code.
3. Call inputs other than prices and SEC facts are not recorded, so the live decision path can
   never be replayed or the reward/risk gate tested.

Fixing those (P0-1 to P0-4, about two weeks) matters more than any feature borrowed from the
competitors.

## Strongest path forward

1. **Make evidence accumulate** (P0): complete and split the fingerprint, record every input,
   patch two advisories.
2. **Make the ranking earn its complexity** (P1): record a 12-1 momentum + quality shadow next to
   the composite; let the pre-registered test decide.
3. **Borrow OpenBB's shape, not its code** (P1): a thin typed gateway with provenance and
   ordered failover.
4. **Add AI where QUANTA is already strong** (P1-P2): one grounded LLM call that explains the
   Decision and argues both sides, gated by `check_narrative`. Never let it decide.
5. **Borrow Qlib's rigour where QUANTA's is weakest** (P2): point-in-time universe, shrinkage
   covariance, turnover-bounded evaluation. ML only after that, and only if it beats the free factor.
6. **Pay down structure** (P2-P3): one indicator and regime module, split the 15K-line engines and
   `app.py`, then package.

## What not to do

- Do not adopt TradingAgents' LLM committee as the decision maker, or its LLM-written "lessons".
- Do not add OpenBB as a dependency.
- Do not train deep models, or any ML model, on the 42-name survivor universe.
- Do not loosen a funding threshold to make more names pass; PRODUCT.md reserves that for the
  owner, and v4's pre-registered EV gate is the tested route.

## Measurable targets for the next review

| Metric | Today | Target after P0-P1 |
|---|---|---|
| Model fingerprints per 10 publication dates | ~19 | ≤ 2 |
| Engine keys with complete fingerprint coverage | 2 of 3 Research keys; 0 cover `modules/` | all |
| Ledger rows with recorded valuation/analyst inputs | 0% | 100% of new rows |
| Shadow models recorded | v3, v4 | v3, v4, factor baseline |
| Direct `yf.` call sites outside adapters | 86 | ≤ 40 (phase 1) |
| Known dependency advisories | 2 | 0 |
| F401 findings | 123 | 0 and gated |
| Generated research text shown without passing the grounding check | n/a | 0 |
| Weighted score (this rubric), projected | 6.28 | ≈ 6.9 (data 5→7, AI 4→6, architecture 4→5, quant 6→7) |

The projected score is a judgment of what the listed changes would earn under this rubric, not a
measurement.
