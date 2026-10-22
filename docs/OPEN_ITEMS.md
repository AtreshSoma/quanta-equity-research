# Open items

What is deliberately not done, why, and what would close it. Reviewed 20 October 2026. Anything not
listed here is closed.

## Needs an owner decision

These change what the product says or does, or are destructive, so they are not made in a
maintenance pass.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| 1 | **Public git history holds a fixture modelled on a real account.** The initial public commit (`a21f641`) contained `webapp/tests/fixtures/my_portfolio_view_18.json` and `my_portfolio_insights_18.json` with real tickers, weights, a total of 37,686.45 and cash of 371.64 (prices were synthetic). The current tree holds a fictional book instead (`make_book18_fixture.py`). | (a) Leave it. (b) Rewrite history with `git filter-repo` and force-push; every clone and fork must be re-cloned, and commit hashes in the docs change. | Decide by how sensitive you consider holdings and a total balance. If sensitive, do (b) soon, while there are few clones. Nothing needs rotating: no credentials were involved. |
| 2 | **Risk grade is absolute.** It grades a stock's own swings and drawdowns, so a volatile sector leader can grade F. Listed under Known limitations in the README. | Keep, or grade against sector peers | Keep until the validation work says a peer-relative grade predicts anything. Changing it alters engine output and the code fingerprint. |
| 3 | **Discover ranks Street candidates by consensus upside.** Targets can be stale, so the top of the list can be the most out of date. The 10-analyst floor and 50% upside cap limit this; the sort control already offers analyst count and distance from the 52-week low. | Keep the default, or default to analyst count | Keep, and review after a month of use. |
| 4 | **Colour judgments from the design pass** (which states earn a colour, and which stay neutral). | Per `DESIGN.md` | These were noted during the design pass without detail. Revisit on screen against `DESIGN.md` and record any change there. |
| 5 | **In-app legal pages** (terms, privacy). The README carries the licence, disclaimer and broker-connection statement. | Add pages, or keep the README as the statement | Add them before anyone other than you uses a hosted copy. A loopback-only tool does not need them. |
| 6 | **The 1.50:1 Research reward/risk gate is structurally strict.** Reward is a quarter of the 12-month valuation upside (63 of 252 sessions); risk is a 2.75×ATR stop. On 42 large caps, 2010-2026 (31,019 weekly rows, `elite_panel.parquet` plus daily prices), the gate needed a median 32% of 12-month upside (25th-75th: 26%-42%); realised 12-month returns reached that bar in 24% of rows (median realised 15.6%), and the stop was touched within the 63-session horizon in 52%. It blocks new money for almost every steady large cap, in Picks and for holdings alike. The reward side cannot be backtested: no point-in-time valuation targets are recorded. My Portfolio now shows the arithmetic beside each block. | (a) Keep. (b) Measure reward and risk on one horizon and one distribution (e.g. a volatility-scaled stop at the horizon). (c) Lower `RR.RESEARCH_MIN`. (b) and (c) loosen a funding threshold, which PRODUCT.md rules out without you. | Keep until a point-in-time target history exists to test (b); start recording each Decision's valuation target in the ledger so that test becomes possible. |
| 7 | **Fund look-through** (an index fund's largest constituents counted in a holding's exposure). | Issuer holdings files (dated, daily) as a data source; or keep funds as one line | Keep (`portfolio/exposure.py` explains why: provider profiles carry no as-of date). Revisit if an issuer file feed is added. |

## Engineering follow-ups

| Item | State | Why it is open |
|---|---|---|
| **2027 NSE holidays** | 2026 is complete. 2027 has only the fixed-date holidays and Good Friday. | NSE publishes the 2027 circular in December 2026. Add the dates to `NSE_HOLIDAYS` in `core/engine/market_calendar.py`. |
| **Front-end weight** | `app.js` 820 KB and `quanta.css` 624 KB, unminified; My Portfolio scripts (about 440 KB) load on every page. | Minifying or lazy-loading them changes the load order the 746-test JavaScript suite relies on, and needs a build step or a loader. Worth doing as its own change, measured against the figures in the README's Performance section. |
| **Unused CSS** | Not measured. | Static analysis cannot see classes set at runtime. A safe audit needs browser coverage across every view, state and theme. |
| **Six unwired modules** | `core/enhanced_analyzer.py`, `core/enhanced_technical_indicators.py`, `modules/TechnicalSignalGenerator.py`, `modules/polymarket_provider.py`, `modules/realtime_catalyst_integration.py`, `modules/robust_backtesting_engine.py` are imported by nothing, but each runs standalone. | The recalibration plan keeps them on purpose (Polymarket is "wired in Phase 5", the robust backtester is a Phase 2 / 8 candidate) and the September audit says to confirm outside users before removal. Retire or wire each when that phase is scheduled. |
| **`core/quant/`** | `factor_model.py`, `risk_model.py` and `data_quality.py` (about 700 lines) are imported only by the audit probe `docs/audits/QUANTA-ALPHA/QUANTA-Audit-Probes.py`. | Kept with the six above: retire or wire them when the portfolio-construction phase is scheduled, and keep the probe working until then. |
| **FOMC size multipliers** (70% and 85%) | Configured in `thresholds.py`, not validated. | A judgment, stated as one in the README. Calibrate only if the evidence work shows event risk is worth modelling. |
| **Em dashes in engine output** | 209 string literals remain, in CLI reports, log lines and the MCP server. None reaches the web app: seven analyses (US, ETF, ADR, India) were probed and returned none. | Editing `core/engine/` or the three engine files starts a new code-fingerprint period, and some strings are provider keys that must not change (`Software—Infrastructure`). Fix with the next engine change. |
| **Two load-sensitive e2e tests** | `guide.spec.ts` GUIDE-006 and REEL-001 failed once during a 500-test run on a busy machine and pass alone (12 of 12). | Timing under CPU load, not a defect. Tighten their waits if they fail in CI. |
| **Line endings** | Git warns "LF will be replaced by CRLF" on Windows. | A `.gitattributes` would settle it but renormalises files; do it as a single dedicated commit. |
