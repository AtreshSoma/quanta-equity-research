# Documentation

Design notes, audits and fix logs for QUANTA, written while the project was built. Apart from the
two "start here" files they are dated working records, not a stable specification.

Audit and fix-log documents refer to evidence artifacts (screenshots, raw test output, scratch
data) that were produced during development. Those artifacts are not included in this
repository, so those references will not resolve. The conclusions in each document stand on
their own.

The code is released under the MIT License; see [../LICENSE](../LICENSE). The project overview,
install steps and architecture are in the [root README](../README.md).

## Start here

| Document | Read it for |
|---|---|
| [LIVE_MODEL_VALIDATION_STATUS.md](LIVE_MODEL_VALIDATION_STATUS.md) | What the live model has and has not demonstrated out of sample |
| [../DESIGN.md](../DESIGN.md) | The web app's design system: tokens, type, colour and motion rules |
| [../PRODUCT.md](../PRODUCT.md) | Who the product is for and what it will not do |

## Layout

```
docs/
├── LIVE_MODEL_VALIDATION_STATUS.md   Current validation status (kept at the top level on purpose)
├── guides/       How to run, share and deploy
├── audits/       Findings: what was checked and what was wrong
├── design/       Redesign briefs, proposals and their logs
├── logs/         Plans and implementation or fix logs: what was changed, in order
├── reference/    Comparisons and teardowns of other code
└── img/          Screenshots used by the root README
```

Each folder answers a different question, so a document belongs where its *purpose* sits, not
where its topic does: a Picks audit lives in `audits/`, the fixes it caused in `logs/`.

## guides/

| Document | Covers |
|---|---|
| [README_FOR_USERS.md](guides/README_FOR_USERS.md) | A quick-start for a non-developer, from the scanner's early releases |
| [SIMPLE_SHARING_GUIDE.md](guides/SIMPLE_SHARING_GUIDE.md) | Sharing the scanner as a zip of Python files |
| [STREAMLIT_DEPLOY.md](guides/STREAMLIT_DEPLOY.md) | Deploying the Streamlit research desk |
| [ELITE_README.md](guides/ELITE_README.md) | The ELITE v2.0 engine's features and output |

These four predate the Flask web app. Where they disagree with the [root README](../README.md), the
root README is current.

## audits/

| Document | Covers |
|---|---|
| [QUANTA-ALPHA/](audits/QUANTA-ALPHA/README.md) | The QUANTA Alpha audit and build blueprint, with its evidence and probes |
| [QUANTA_AUDIT_2026-09-07.md](audits/QUANTA_AUDIT_2026-09-07.md) | Architecture, trading-intelligence and product audit, 7 September 2026 |
| [PRODUCTION_READINESS_AUDIT.md](audits/PRODUCTION_READINESS_AUDIT.md) | Production-readiness audit of the engines, providers, API, UI and MCP surface, 10 July 2026 |
| [PICKS_AUDIT.md](audits/PICKS_AUDIT.md) | Read-only audit of the Picks page: code, logic, results and UX, 1 October 2026 |
| [AUDIT_REPORT.md](audits/AUDIT_REPORT.md), [AUDIT_PROGRESS.md](audits/AUDIT_PROGRESS.md) | An independent audit of the Picks remediation, and its progress record |
| [PICKS_PAGE_AUDIT.md](audits/PICKS_PAGE_AUDIT.md) | Independent end-to-end audit of Picks, 4 October 2026 |
| [UI_PRODUCTION_AUDIT.md](audits/UI_PRODUCTION_AUDIT.md) | Production audit of the web app (Stage C synthesis), 8 October 2026 |
| [AUDIT_2026-10-17.md](audits/AUDIT_2026-10-17.md) | Picks engine audit of 17 October 2026: defects fixed and decisions taken |

## design/

| Document | Covers |
|---|---|
| [OVERVIEW_REDESIGN.md](design/OVERVIEW_REDESIGN.md) | The Overview page, from the owner's trader and analyst review (9 October 2026) |
| [overview/](design/overview/IMPLEMENTATION.md) | The Overview proposal, its two mockups and screenshots of the build |
| [RESEARCH_REDESIGN.md](design/RESEARCH_REDESIGN.md) | The Research tear sheet |
| [PICKS_REVAMP.md](design/PICKS_REVAMP.md), [PICKS_DESIGN_BRIEF.md](design/PICKS_DESIGN_BRIEF.md) | The Picks revamp log (stage 1 closed 6 October 2026) and the design brief with its measured gates |
| [GUIDE_REDESIGN.md](design/GUIDE_REDESIGN.md) | The Guide as the hero page (13 October 2026) |
| [my_portfolio.md](design/my_portfolio.md) | How My Portfolio reads a Robinhood account through its MCP server |
| [ACTIVITY_REDESIGN_LOG.md](design/ACTIVITY_REDESIGN_LOG.md) | The Activity tab redesign log |
| [UI_Instructions.md](design/UI_Instructions.md) | An early rebuild brief written for a React and Tailwind stack. The app did not adopt that stack; [../DESIGN.md](../DESIGN.md) is the current system |

## logs/

| Document | Covers |
|---|---|
| [RECALIBRATION_PLAN.md](logs/RECALIBRATION_PLAN.md) | The plan to recalibrate the scoring model, phase by phase |
| [IMPLEMENTATION_PLAN.md](logs/IMPLEMENTATION_PLAN.md), [IMPLEMENTATION_LOG.md](logs/IMPLEMENTATION_LOG.md) | The Picks remediation plan and its resume-point log |
| [Implementation_Document.md](logs/Implementation_Document.md) | A gap analysis of QUANTA Elite v6.0 (full-market scan): improvements confirmed and gaps left |
| [PICKS_FIX_LOG.md](logs/PICKS_FIX_LOG.md), [PICKS_OUTPUT_FIX_LOG.md](logs/PICKS_OUTPUT_FIX_LOG.md) | Picks fixes in batches, and the fix to a scan that funded nothing (4 October 2026) |
| [ELITE_BUGFIX_REPORT.md](logs/ELITE_BUGFIX_REPORT.md) | Code-level bug fixes in `TradingAnalysis_ELITE.py` |
| [MAIN_SELECTIVE_INTEGRATION_2026-09-24.md](logs/MAIN_SELECTIVE_INTEGRATION_2026-09-24.md) | What was taken selectively from `main` and the redesign line |

## reference/

| Document | Covers |
|---|---|
| [SP_ELITE_COMPARISON.md](reference/SP_ELITE_COMPARISON.md) | `SP_ELITE_PRO.py` compared with an alternative scanner, 5 October 2026, with the selective port that followed |
| [SP_ELITE_PRO_TEARDOWN.html](reference/SP_ELITE_PRO_TEARDOWN.html) | A teardown of the Elite engine (open in a browser) |

## Adding a document

1. Pick the folder by purpose (table above). Put the date in the file name when it records one day.
2. Link it from this page.
3. Link to other documents with relative paths, so the links survive a move.
4. Do not edit `core/engine/` or the three engine files only to fix a comment's doc path: their
   source is hashed into each recorded call's code fingerprint (see the root README, "Track record").
