# QUANTA Elite — Picks page audit

**Date:** 2026-10-01 · **Branch:** `audit-remediation` @ `4f878f8` · **Scope:** Picks page (`#picks`) code, logic, results, UX
**Reviewers' lens:** quant analyst, senior equity research analyst, financial advisor, hedge-fund PM
**Mode:** read-only. No code or project data was changed. Fixes are proposed as diffs and were not applied. No trade was placed or previewed.

**Evidence labels.** **[V-code]** read in the code. **[V-run]** reproduced by running the engine. **[V-ext]** confirmed from an external source. **[Unverified]** not confirmed.

**How the runs were done.** Scans ran in-process through `app._run_stock_picks`, the same entry point as `POST /api/stock-picks`. Settings:
- `QUANTA_TRACKING_DISABLED=1`, a scratch `QUANTA_LEDGER_PATH`, and a scratch `QUANTA_CACHE_DIR` (a copy of the warm cache). The production signal ledger and cache were not touched.
- No server was started, and the user's server on :5500 was left alone.

One side effect: the first run appended INFO lines to the untracked runtime log `ui/stock-analysis/webapp/logs/quanta_trading.log`, because the engine logs relative to the working directory. Later runs logged to scratch.

---

## 1. Architecture map

### 1.1 Request path

| # | Component | File : line | Function | Inputs | Outputs |
|---|---|---|---|---|---|
| 1 | Scan form | `ui/stock-analysis/webapp/templates/index.html:380-473` | `#picks-form` | market, capital (**literal 2500**), scan type, mandate, max positions, diversify | DOM values |
| 2 | Market switch | `static/app.js:5109-5172` | `applyPicksMarketUI` | market | Switching to US resets any capital **≥ 10,000 to 2500** (`:5169`) |
| 3 | Submit | `static/app.js:5191-5265` | `runScan` | form | `POST /api/stock-picks` JSON |
| 4 | Route | `webapp/app.py:4737-4897` | `start_stock_picks` | JSON | Validates input, coalesces duplicate scans by `_scan_params_key` (`:497`), returns 409 if a different scan is running, then 202 `{job_id}` |
| 5 | Job runner | `app.py:5061-5551` | `_run_stock_picks` (daemon thread) | params | Progress sits at 20% for the whole scan, then jumps to 80% |
| 6 | Engine init and regime | `stock-analysis/core/SP_ELITE_PRO.py:6525`, regime `:5191-5290` | `QuantaEliteTradingSystem(capital, market)` | VIX, SPY bars | `BULL / NORMAL`, exposure 1.0 |
| 7 | Universe | `SP_ELITE_PRO.py:7444`, `_select_broad_universe :7601`, `smart_prefilter :10009`, `_even_sample :7624` | `build_us_stock_universe` | 806 index names | 250 tickers, liquidity-ranked **or an alphabetical even spread** on timeout |
| 8 | Per-name gates | `SP_ELITE_PRO.py:7814-8890` | `analyze_stock` | 1y daily bars and `.info` (yfinance) | `StockAnalysis` or `None`, with counts in `_filter_stats` |
| 9 | Levels | `SP_ELITE_PRO.py:9704-9799` | `_calculate_price_targets` | price, Wilder ATR(14) (`:4326`), support/resistance, analyst target | stop, target |
| 10 | Net R:R | `SP_ELITE_PRO.py:8438-8468` | in `analyze_stock` | entry = reference, tiered costs (`:1971-1981`) | `risk_reward_ratio` |
| 11 | Committee policy | `SP_ELITE_PRO.py:9386-9466` | `_apply_committee_policy` | score, grade, insider, upside, R:R, RSI | may change signal to HOLD |
| 12 | Sizing | `SP_ELITE_PRO.py:9504-9545`, `TradingConfig.min_position_size :2003` | risk budget ÷ stop % | capital, signal, score, stop % | allocation, shares |
| 13 | Decision core | `core/engine/decision_engine.py`, `risk_overrides.py`, `blockers.py`, `score_bands.py`, `types.py` | `resolve_final_action` | `DecisionInputs` | `final_action`, reasons, codes |
| 14 | Book gates | `SP_ELITE_PRO.py:11147-11890` | `get_top_recommendations` | analyses | G1 to G6 gates, caps, correlation, then sized book (`_gate_stats`) |
| 15 | Deep confirmation | `SP_ELITE_PRO.py:10728-11070` | `_deep_confirm_final_picks` | final picks | runs `TradingAnalysis_ELITE.analyze(fast_mode=True, include_insider=True)`, then OPTION-A reconciliation, then the **ONE DECISION cap** |
| 16 | Deep engine | `core/TradingAnalysis_ELITE.py` (`TradeSetupGenerator :6360-6533`, risk `:3395-3490`, composite `:5799`) | `QuantaEliteAnalyzer.analyze` | 2y bars | its own entry, stop, horizon target, R:R, composite and risk grade |
| 17 | Decision freeze | `SP_ELITE_PRO.py:3369-3430` | `decision_for_scan_result` | analysis | `decision` (action, horizon, freshness, evidence). `blockers` / `primary_blocker` / `holder_action` are never filled |
| 18 | Serialize | `app.py:5332-5524`, `_stock_analysis_to_dict :1080`, deep-confirm block `:5170-5196` | | | `result` = recommendations, funnel, deep_confirmation, scan_status, … |
| 19 | Status | `app.py:603-648`, called at `:5454` | `_classify_scan_status` | counts | `success` / `no_picks` / `partial` / … |
| 20 | Render | `static/app.js:5831` `renderPicks`, `:6093` `quickRefTableHtml`, `:6230` `pickCard`, `:5303` `deepConfirmSummaryHtml`; `static/elite-pro.js:378` `injectPicksExtras` (funnel `:777-806`, export `:702`) | | `result` | page |

### 1.2 Where each on-page string comes from

| UI string | Source |
|---|---|
| "Run Portfolio Scan" | `index.html:465` |
| "Proposed Allocation" | `index.html:479` |
| "Research Book · N Candidates", "Binding reason" | `app.js:6145`, `:6155` |
| "Watchlist Export" | `elite-pro.js:702` |
| "Scan method and risk-check audit" | `app.js:6045` |
| "allocation multiplier" | `app.js:5962`; multipliers from `app.py` `RISK_PROFILE_ALLOCATION_SCALE` (~`:4908`) |
| "Risk Grade F blocks new-money BUY" | `engine/risk_overrides.py:99`; humanized at `app.js:262` |
| "Capped at the Research Decision" | `SP_ELITE_PRO.py:11001` |
| "Early Mom x/6" | `app.js:6254`, built by `classify_early_momentum` `SP_ELITE_PRO.py:6425` |
| "Quality Compounder" | `SP_ELITE_PRO.py:3203`, `:6400` |
| "The restrictive result controls" | **UI text only**, `app.js:6386`. No code computes a combined grade |
| "Valid again if a tighter stop…" | `app.js:5052`, keyed on `LOW_REWARD_RISK` |
| "source yfinance · fresh" | `app.js:6385`, from `decision.sources` / `decision.freshness` (`engine/decision.py:76-94`) |

### 1.3 External data sources

| Source | Used for | Notes |
|---|---|---|
| **yfinance** (sole price source for the scan) | daily bars (`auto_adjust=True`), `.info`, insider and analyst data, earnings calendar | Monkey-patched TTL cache in `app.py:128-407`: history 900s, info 3600s, 0.35s spacing, JSON disk cache. **Bulk `yf.download` (pre-filter) is not cached.** No `timeout=` argument. 429 cooldown and adaptive pacer at `SP_ELITE_PRO.py:1899-1961` |
| Polygon | VIX / SPY / benchmark only, if `POLYGON_API_KEY` is set | no per-ticker fallback |
| SEC EDGAR | My Portfolio only (`core/portfolio/sources.py:66` `EdgarClient`) | **not used by Picks** |
| Index constituent lists | universe build | |

### 1.4 Threshold sources

- **There is no config file.** `stock-analysis/config/` holds only `README.md` and a CSV.
- Thresholds live in the `TradingConfig` class (`SP_ELITE_PRO.py:1705-1965`, a few overridable by env var), `RiskLimits` (`:1138-1150`), `engine/types.py:64-65` (`min_score_for_buy=55`, `min_rr_for_buy=1.5`), `engine/score_bands.py`, `engine/risk_grade.py:46-93`, and many inline literals (see section 5.6).

---

## 2. Verdict, scores and outcome evidence

### 2.1 Verdict and scores

**The page's goal, from its code and copy:** *"Scan a universe under your capital and mandate, then size a hypothetical book from the names whose Decision allows new money"* (`index.html` subtitle). The product goal adds research-grade Buy / Accumulate / Hold / Sell / Avoid / Watch calls with honest evidence and actionable sizing.

**Verdict: not achieved.** The safety ratchet works: nothing unsafe gets funded. But the page cannot tell a user *why* it holds cash or *what would change that*. Its numbers contradict each other on the same card. And it showed two names it should not have:
- **LNTH** is under a signed $102.50 cash takeover. The scanner shows it with a $109.80 target that cannot be reached.
- **HST**'s card shows R:R 2.25 while the binding reason quotes 1.23 from a different level set.

The "0 funded of 250" result is explainable and partly correct (section 2.5). It is not explained on the page.

| Area | Score /5 | Evidence |
|---|---|---|
| Data quality | **2** | Single provider. The pre-filter timed out on all 3 runs, so the universe silently became an alphabetical spread (F4). No corporate-event awareness (F1). Freshness rules conflict (F12) |
| Factor model | **2** | Plausible blend, but regime weights and thresholds are hand-set and never validated. Five score scales (F10). Value factor is price-based only |
| Risk logic | **2** | Two risk grades over different windows and inputs (1y/3 factors vs 2y/5 factors). Drivers are computed and then dropped (F3) |
| Decision logic | **2** | Two engines with three level sets. The ONE DECISION cap undoes OPTION-A (F2). No SELL/TRIM on Picks (F7) |
| Sizing | **3** | The formula is sound and reproduced to the cent. Weaknesses: flat $100 minimum in whole-share mode, deep-ACCUMULATE halving skips the minimum (F11), no link to account cash (F6) |
| Explainability | **2** | Binding reason quotes numbers the card doesn't show. No funnel when watch rows exist. The F grade is unexplained (F2, F3, F5) |
| Validation | **1** | Scanner: `UNTESTED`, no registry entry. Deep engine (the binding veto): **`NO_EDGE`**, with negative out-of-sample IC (F8). 8 elite_pro ledger rows, 0 resolved. A harness exists (`core/apex_quant`) but has never covered the scanner |
| Portfolio awareness | **1** | No holdings, no cash, no correlation with the existing book (F6) |
| UX | **2** | Each value is repeated 3–4×, ~19 caveat sentences, the funnel is collapsed, progress is stuck at 20%, no chart (F13) |

**Seed observations: verdicts**

| # | Seed | Verdict | Evidence / finding |
|---|---|---|---|
| 1 | HST: R/R 1.23 in the reason, 2.3 in the table, 2.25 in the chip; contradictory "valid again" text | **Confirmed. Two engines.** 1.23 = deep engine (entry 22.56 / stop 21.86 / 63-session target 23.47, 11 bps). 2.25 = scanner, net at the reference price, 30 bps. "2.3" is the same 2.25 at 1 dp. The remedy text is keyed to the deep gate | F2 |
| 2 | Two risk grades, no reason for the F | **Confirmed.** 1y/3-input vs 2y/5-input. LNTH's F = vol +2, drawdown +2, tail +2. The drivers are computed, then dropped | F3 |
| 3 | Four score scales on one card | **Confirmed, and worse:** five scales plus three confidence heuristics. The 0–10 scale is uncentred (median 3.47) | F10 |
| 4 | 250 / 2 / 0 in a bull regime, no funnel | **Confirmed.** The funnel exists but is collapsed. A watch-only result is classed `success`, so no explanation is built. The 250 were an alphabetical spread, not liquidity-ranked | F4, F5, section 2.3 |
| 5 | LNTH zone ≈ 0.6% vs 3% stop | **Real, but not a unit bug.** Zone = 1 Wilder ATR (0.59). Stop = the 3% floor (≈ 5 ATR). Cause: a pending $102.50 cash takeover pins the price | F1, section 2.7 |
| 6 | Capital $2,500 vs the account cash (amount redacted); holdings ignored | **Confirmed in code** (the cash figure itself is unverified). At that cash level, all 37 signal-qualified names size to $0 | F6, section 2.6 |
| 7 | Only WATCHLIST is ever shown | **Mostly.** Ledger: 7 WATCHLIST, 1 BUY (GOOGL 2026-09-25). Picks cannot emit Hold/Trim/Sell. Avoid is never shown as a row. At $25k the same scan funds GOOGL | F7, section 2.6 |
| 8 | Values and disclaimers repeated | **Confirmed.** Entry / stop / target ×4, R:R ×3, about 19 caveat sentences | F13 |
| 9 | yfinance; red stale header | **Confirmed.** yfinance is the sole provider. Red = dashboard age ≥ 10 min regardless of session. The card's "fresh" is a different clock (daily-bar lag) | F12, F4 |
| 10 | No valuation, estimates, catalysts, consensus, chart | **Confirmed for display.** The engine already *uses* the analyst mean target, EPS revisions and the earnings date, but the page doesn't show them | Section 7 |

### 2.2 Outcome audit (evidence)

Sections 2.3–2.7 cover Step 4: the funnel, distributions, whether the empty book is correct, sensitivity, and an independent recomputation of the levels.

### 2.3 Funnel: Standard US scan, $2,500, Balanced, max 5, diversify on

The user's scan (2026-10-01 21:36–21:43) was reproduced twice. All three runs gave the same 2 names, sizes, R:R and withdrawals, so determinism holds for a fixed provider state **[V-run]**. Counts from `funnel.filter_stats` and `gate_stats`, in gate order (`SP:7890-8692`, `:11204-11887`):

| Stage (gate, file:line) | Survivors | Removed | Notes |
|---|---|---|---|
| Index universe (`build_us_stock_universe`) | 806 | — | |
| Scan universe (`smart_prefilter` → **even-spread fallback**) | **250** | 556 | 90s bulk budget exceeded on every run (F4) |
| Bars ≥ 200 and data ≤ 10 days old (`:7890`, `:7897`) | 229 | 21 | 17 insufficient history + 4 validation. The scan header still reports "Failed 0" |
| Price $10–$5,000 (`:8017`) | 224 | 5 | |
| 20-day volume ≥ 750k (`:8034`) | 200 | 24 | |
| Market cap ≥ $2B (`:8059`) | **182** | 18 | **68 of 250 (27%) were untradeable by the scan's own rules** |
| Score ≥ 3.5 before and after adjustments (`:8179`, `:8316`) | 91 | 91 | 85 + 6 |
| Signal ≠ AVOID (`:8339`) | 87 | 4 | |
| Signal BUY/ACCUMULATE at regime thresholds (`:8385`) | 37 | 50 | |
| Risk-sized position ≥ $100 (`:8385`) | 27 | 10 | all 10 removed by the flat $100 floor |
| Net R:R ≥ 1.8 (`:8466`) | 4 | **23** | the largest single cut among signal-qualified names |
| Committee policy (`:8668`) | 3 | 1 | MU: score 8.71, R:R 2.19, **grade F** |
| Grade/earnings-adjusted size ≥ $100 (`:8692`) | 2 | 1 | **GOOGL**: score 6.48, R:R 2.41, grade B; $99.5 after the ×0.8 earnings haircut |
| Book gates G1–G6 (`:11204-11609`) | 2 | 0 | score floor relaxed to 5.5 (pool ≤ 8) |
| Sized, after sector / position / correlation caps | **2** | 0 | LNTH 3 sh $298.95 (12%), HST 7 sh $157.57 (6%). `gate7_sector_cap` *reports 0* (F5) |
| Deep confirmation / Research cap (`:10728-11003`) | **0** | 2 | LNTH: deep grade F. HST: Research R:R 1.23 |

The per-name attribution comes from a research-mode pass (`analyze_stock(research_mode=True)` over the same 250 names, 3 workers). Its first-failing-gate counts **match the scan's `filter_stats` exactly**. Two caveats on that pass:
- It records every gate a name trips, so its "all hits" counts are larger. Only the first failing gate is used above.
- Per-name values are **[V-run]** for this session's provider data.

### 2.4 Distributions

Over the **182 names that passed the data and liquidity gates** (research-mode pass, scanner's own grade and action) **[V-run]**:

| Measure | Distribution |
|---|---|
| Scanner risk grade | A 32 · B 69 · C 46 · D 13 · F 22 |
| Scanner decision | AVOID 118 · WATCHLIST 62 · ACCUMULATE 2 · **BUY 0** · STRONG_BUY 0 |
| Published decision | WATCHLIST 2 · funded 0 |
| Scanner score (0–10) | **median 3.47** (below the 3.5 floor) · 90th percentile 6.54 |
| Net R:R | median 2.23 · 62% ≥ 1.8 (in line with the 2026-09-09 base rate: median 2.05, 55%) |
| Ledger, all elite_pro scans (3 dates) | WATCHLIST 7 · BUY 1 (GOOGL 2026-09-25) · resolved outcomes 0 |

**Reading:** R:R is not the main constraint (62% clear it). **The score scale is.** The median liquid name scores 3.5/10, and only the top decile reaches the BULL BUY threshold (6.5). The scale is never re-centred: cross-sectional z-scores need ≥ 30 names (`QUANTA_ZSCORE_MIN_UNIVERSE`) but run **after** the per-name gates, which left 2. The log line reads "Cross-sectional z-scores skipped: 2 scored names (< 30)". So normalization can never apply in the scans where it matters. That finding goes with F10.

**Closest to qualifying** (the input for the new near-miss panel):

| Ticker | Failed gate | Value → threshold | What it would take |
|---|---|---|---|
| GOOGL | grade/earnings-adjusted size | $99.5 → $100; 1 share = $338.24 carries $25.07 at the stop = 1.0% of capital vs a 0.37% budget | capital ≥ ~$6,800 (ACCUMULATE) or ~$5,100 (BUY), then deep confirmation (not run) |
| MMM | net R:R | 1.71 → 1.80 | a 5% better R:R (lower entry or higher target) |
| COP | net R:R | 1.69 → 1.80 (score 6.33) | +7% R:R |
| TEL | net R:R | 1.56 → 1.80 | +15% R:R |
| MSFT | net R:R | 1.52 → 1.80 (score 7.54) | +18% R:R |
| MU | committee: grade F | F → ≤ C (score 8.71, R:R 2.19) | grade improvement; drivers not published (F3) |
| PWR | $100 floor, then R:R | 1.64 → 1.80 | +10% R:R |

The panel should compute the exact price trigger per name from the published entry, stop, target and cost: `target_needed = entry + min_rr × (entry − stop + c) + c`. This pass did not capture entry prices, so no price triggers are quoted here.

Of the 10 names removed by the $100 floor, **none** had R:R ≥ 1.8 (max 1.64). At $2,500 the floor hid no fundable name in this scan. It is still the wrong rule for small accounts (F11).

### 2.5 Is "0 funded of 250" correct?

**Partly.** Three of the outcomes are defensible:
- **LNTH** was correctly kept out of funding, but for the wrong stated reason. Its real disqualifier is the pending cash takeover (F1). Its deep R:R is also 1.43, under 1.5 **[V-run]**.
- **HST**'s withdrawal follows a coherent rule: the 63-session, pro-rated valuation target gives only 1.23. But the page shows the scanner's 2.25, so the rule looks like a bug (F2).
- The 23 R:R failures are ordinary: they are names whose structural target sits close to price.
- The 85 score failures and 50 not-actionable signals follow mechanically from an uncentred 0–10 scale (median 3.47) set against BULL thresholds of 6.5 for BUY and 7.5 for STRONG_BUY (`SP:9037-9055`). That is a calibration question (F10), not a bug.

Three things make the result worse than the market justifies:
- **The universe was not the liquid universe** (F4). 27% of the names were untradeable by the scan's own rules.
- **The best-qualified name was lost to the flat $100 floor.** GOOGL (score 6.48, R:R 2.41, grade B) missed by $0.50 after the earnings haircut (F11). Whether it would have passed the book gates and deep confirmation was not tested.
- **The Research cap reapplies a price gate** that OPTION-A was written to remove (F2).

**What it takes for a name to be funded today:**

| Requirement | Threshold |
|---|---|
| History | ≥ 200 bars, data ≤ 10 days old |
| Liquidity | price $10–$5,000; 20-day volume ≥ 750k; market cap ≥ $2B; dollar volume ≥ $20M |
| Score | ≥ 3.5 before and after adjustments |
| Signal | BUY/ACCUMULATE at the regime thresholds |
| Size | risk size ≥ $100 |
| Net R:R | ≥ 1.8 |
| Committee | grade not F, upside ≥ 8%, RSI ≤ 75 on BUY, no heavy insider selling |
| Book gates | grade A–C; score ≥ max(5.5, pool median); conviction ≥ 2/3 |
| Deep engine | Research Decision **at least ACCUMULATE**, which needs its own 2-year grade ≤ D, its own R:R ≥ 1.5 against a **pro-rated 63-session** valuation target, and composite ≥ 55 |

That last condition is the binding one. A valuation upside *U* over 12 months becomes *U*/4 over the horizon. With a stop of 1.5–3 ATR and a 1.5 hurdle, it needs roughly *U* ≥ 6×stop%. For a 3% stop that is ≥ ~18% 12-month upside, before costs.

### 2.6 Sensitivity

**Setup.** A custom-universe scan over the **91 names that cleared the score floor** in the full scan. Score, R:R and grade don't depend on capital, mandate or caps, so the earlier gates can't change. Each variant was run once in-process. **The base variant reproduces the full scan exactly, and a repeat base run is identical (determinism)** **[V-run]**.

| Variant | R:R rejects | Sized out (min trade) | Published | Funded | Changed as it should? |
|---|---|---|---|---|---|
| Base: $2,500 · Balanced · max 5 · diversify | 23 | 10 | LNTH, HST (Watch) | 0 | — |
| **Capital = account cash** (amount redacted) | — | **37 (all)** | none | 0 | Yes. Every risk-sized position is under the flat $100. `scan_status = no_picks`, and the explanation says only "zero_allocation=37". It never says that the capital is too small for any position to reach the $100 minimum **[V-run]** |
| **Capital $25,000** | 33 | 0 | LNTH (W), **GOOGL ACCUMULATE 2 sh = $676.48**, HST (W) | **1** | Yes. Sizing allows a whole GOOGL share; the 10 formerly sized-out names now reach the R:R gate and fail it (23 → 33); deep confirmation agrees on GOOGL |
| Mandate Conservative (×0.70) | 23 | 10 | LNTH, HST (W) | 0 | Yes. The mandate scales only funded rows, and there are none |
| Mandate Aggressive (×1.00) | 23 | 10 | LNTH, HST (W) | 0 | Identical to Balanced by design (F14) |
| **Max positions 1** | 23 | 10 | **HST only** | 0 | **No.** The engine logged "Published 2 recommendation(s)" (LNTH withdrawn, HST backfilled, then withdrawn). The API returned 1, because `app.py:5279` `recs = recs[:max_positions]` truncates *after* deep confirmation, which contradicts its own comment "Keep ALL final picks" **[V-run, V-code]**. Watch rows count against the funded-position limit. Whether a funded backfill could be cut the same way is **[Unverified]**; it depends on list order |
| Diversify off | 23 | 10 | LNTH, HST (W) | 0 | Yes. The two names are in different sectors |
| Deep confirmation off (`QUANTA_NO_DEEP_CONFIRM=1`, developer override) | 23 | 10 | **LNTH ACCUMULATE 3 sh $298.95, HST ACCUMULATE 7 sh $157.57** | 2 ($456.52, 18%) | Yes. Shows the pre-confirmation book |
| Deep off + $25,000 | 33 | 0 | LNTH 25 sh $2,491.25 · GOOGL 2 sh $676.48 · HST 76 sh $1,710.76 | 3 | Yes |

**Reading:**
- Results move only with the inputs that should move them. Capital matters through the whole-share and $100 floors; deep confirmation is the binding gate. Mandate and diversify are inert when nothing is funded.
- **One defect:** truncation by `max_positions` after deep confirmation (folded into F5).
- **Small-account conclusion (GOOGL arithmetic):** below roughly $5,100 (BUY) to $6,800 (ACCUMULATE), one share of a $338 name with a 7.4% stop ($25 at risk) exceeds the 0.37–0.49% risk budget. Small accounts are structurally limited to low-priced or tight-stop names. The page should say so, not show "0 eligible".

### 2.7 Independent recomputation of displayed levels

| Item | LNTH | HST | Method / verdict |
|---|---|---|---|
| Close 2026-10-01 | 99.65 | 22.51 | yfinance raw = adjusted (no split or dividend in window for LNTH; HST adjusted ATR 0.429 vs raw 0.433) **[V-run]** |
| Wilder ATR(14) | 0.5905 | 0.4291 (adj) | Matches the engine exactly. SMA-ATR would give 0.423 / 0.442 **[V-run]** |
| Entry zone | 99.06–99.65 | 22.08–22.51 | = [close − 1 ATR, close]. **No percentage/price mix-up.** LNTH's zone is 0.6% wide because its realized volatility collapsed after the deal (F1) |
| Stop | 96.66 | 21.33 | LNTH: 2.75 × ATR = 1.62 < 3% floor, so clamped to the `MIN_STOP_DISTANCE_PCT` floor (≈ 5 ATR). HST: 22.51 − 2.75 × 0.4291 ✓ |
| Target | 109.80 | 25.39 | mean(52w high, analyst mean): (111.86 + 107.74)/2 and (25.48 + 25.29)/2 ✓ |
| Net R:R | 2.77 | 2.25 | HST: (2.88 − 0.0675)/(1.18 + 0.0675) = 2.25 ✓. LNTH (50 bps): (10.15 − 0.498)/(2.99 + 0.498) = 2.77 ✓. Gross: 3.39 / 2.44 |
| Deep levels | 99.65 / 97.88 / 102.45, R:R 1.43 | 22.56 / 21.86 / 23.47, R:R 1.23 | `TA.analyze(fast_mode=True, include_insider=True)` **[V-run]** |
| Sizing before deep | 3 sh = $298.95 | 7 sh = $157.57 | Hand-derived budget: 2500 × 0.006 × 0.6 × (0.7 + score/10 × 0.5) ÷ stop%, floored to shares. Matches the log ✓ |

**Second data source:** Stooq's CSV endpoint returned HTTP 404, and no other keyless daily-bar source was available. **Cross-source verification is [Unverified].** The LNTH price pattern was cross-checked against SEC filings (the deal terms explain the ~$100 pin).

---

## 3. Findings

Ranked by severity. File paths are relative to the repo root. `SP` = `stock-analysis/core/SP_ELITE_PRO.py`, `TA` = `stock-analysis/core/TradingAnalysis_ELITE.py`.

| ID | Sev | Area | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| **F1** | **Critical** | Data / research | **LNTH is under a definitive merger agreement.** Curium will pay **$102.50 cash** plus non-transferable CVRs of up to $12 (paid on 2028–2030 milestones), signed 2026-08-03, expected close 1H 2027 **[V-ext]** ([DEFM14A](https://www.sec.gov/Archives/edgar/data/0001521036/000119312526384284/d180727ddefm14a.htm), [InsideArbitrage](https://www.insidearbitrage.com/2026/08/curium-us-to-acquire-lantheus-in-a-8-billion-deal/)). Since the week of 2026-08-09 its average daily range fell from ~$3.50 to ~$0.40 **[V-run]**. The scanner target **$109.80** is the mean of the *pre-deal* 52-week high (111.86) and the analyst mean (107.74) (`SP:9746-9772`) **[V-run]**. The deep engine's 12-month base target is **$110.85** **[V-run]**. The horizon (23 sessions) is derived from 1-year volatility of 33% while realized ATR is 0.59% (`SP:5325-5378`) **[V-run]**. LNTH appears in **3 of 3** elite_pro scans in the ledger. Nothing in the pipeline detects pending M&A. | The only plausible upside is the remaining **$2.85 (2.9%)** spread to the cash price. The real risk is the deal breaking, which the stop does not model. R:R 2.77 is fictitious. Any desk would mark this name *untradeable for a directional thesis*. | Add a **corporate-event gate**. EDGAR filings (DEFM14A / PREM14A / SC TO-T / SC 14D9 / 8-K item 1.01), using the existing `EdgarClient`. Plus a volatility-collapse heuristic as a secondary flag. Result: **AVOID**, code `PENDING_ACQUISITION`, text "Pending cash acquisition at $X; upside capped". Diff D1 |
| **F2** | **Critical** | Decision logic / numeric consistency | HST's binding reason "R/R 1.23 below min 1.50" comes from **TA's own levels**: entry **22.56**, stop **21.86**, horizon target **23.47**. That target is the 12-month base of 26.21 pro-rated by 63/252 sessions (`TA:6360-6533`). Cost is a flat 11 bps **[V-run]**. The card shows the **scanner's** levels: zone 22.08–22.51, stop **21.33**, target **25.39**, net R:R **2.25** at 30 bps (`SP:8438-8468`) **[V-run]**. The table rounds the same field to "2.3" (`app.js:6103`) and the chip to "2.25" (`elite-pro.js:332`). OPTION-A (`SP:10935-10970`) re-resolves with `enforce_price_gates=False` precisely so that "R/R below min" from the deep engine's own levels cannot withdraw a pick. **The ONE DECISION cap (`SP:10991-11003`) then puts back `raw_deep_fa`, the price-gated verdict**, plus its reasons **[V-code, V-run]**. The `LOW_REWARD_RISK` remedy ("…lifts reward-to-risk above the minimum", `app.js:5052`; `engine/blockers.py:60` hard-codes "1.5:1") sits next to a displayed 2.25 that already clears both 1.5 and 1.8. | A user sees a 2.25:1 setup rejected for "R/R 1.23". The page contradicts itself. Three level sets are in play (scanner, zone, deep) and no one can act on them. | **One level set per Decision.** When the Research cap binds on a price gate, publish the deep engine's `trade_setup` (entry, stop, horizon target, R:R, horizon) as the card's levels, and grey out the scanner's. Longer term: one `TradeSetup` builder in `engine/`. This does **not** loosen the cap. Diff D2 |
| **F3** | **High** | Risk logic / explainability | Scanner grade: 1-year window, 3 inputs (vol, beta, drawdown), `SP:8602`. Deep grade: 2-year window, 5 inputs (+tail, +liquidity), `TA:3472-3483`. Recomputed with `engine.risk_grade` on yfinance bars **[V-run]**:
- LNTH, **1y = B** (4 points on the scanner's 3 inputs, 5 on all five).
- LNTH, **2y = F (9 points)**: vol 49% (+2), max drawdown −57% (+2), tail 90/100 (+2; skew −2.69, excess kurtosis 26.8, driven by one crash day).
- HST: scanner A and deep C. Both match the card. "The restrictive result controls" is UI prose only. `decision.risk_grade` publishes **B** for LNTH, with `blockers: []` and `primary_blocker: null` **[V-run]**. TA computes `grade_drivers` (`TA:3478`), but they never reach the Picks payload (`app.py:5170-5196`) **[V-code]**. | The binding gate's grade differs from the grade the Decision publishes. The user cannot see why it is F. The F is a lookback artefact from one event 12–24 months ago, so the grade swings with the window. | Compute **one** risk grade (single lookback in config, all 5 inputs) in `engine/risk_grade.py`, used by both engines. Publish `risk_grade_drivers` and the binding grade on the Decision. Diff D3 |
| **F4** | **High** | Data / universe | `smart_prefilter`'s bulk `yf.download` of 806 names exceeded its **90s budget on all three runs** (user's 21:36 scan, and mine at 22:05 and 22:13) **[V-run]**. It then fell back to `_even_sample`, roughly every third ticker alphabetically (`SP:7624-7629`). Of the 250, **68** were then discarded for data, price, volume or market cap (section 2.3). The bulk download is **not cached** (only `Ticker.history` / `.info` are), so every scan repeats the timeout **[V-code]**. The UI still labels it "Standard — ~250 names (recommended)" and says nothing about the fallback. | The "recommended" universe is not the liquid universe it claims to be. A quarter of the scan budget goes to untradeable names. Results depend on alphabetical position. | Rank liquidity from the cached per-ticker history the scan prefetches anyway (or cache the bulk frame). Publish `funnel.universe_selection` (`liquidity_ranked` or `even_spread_fallback`) and show it in the verdict line. Diff D4 |
| **F5** | **High** | Explainability / states | `final_pick_count = len(picks)` includes unfunded watch rows (`app.py:5332-5394`, `:5454`). So **0 funded + 2 watch is classed `success`** and `empty_scan_explanation` stays `''` **[V-run]**. The funnel renders only inside the collapsed "Scan method and risk-check audit" `<details>` (`app.js:6045`). After backfill, `gate7_sector_cap` reports **0** although 2 names were sized (`SP:11887`) **[V-run]**. Per-name rejection reasons are logged at DEBUG only (`SP:8465`), and `_zero_alloc_reasons` is never returned. The scan header says "Failed 0 / 100% success" while 17 names had insufficient data and 4 failed validation **[V-run]**. `app.py:5279` truncates published rows to `max_positions` *after* deep confirmation. With max positions = 1 the engine published 2 and the API returned 1, dropping LNTH **[V-run]**. | "0 of 250" reads as a broken scanner. The user cannot see that 23 names failed on R:R and 2 were withdrawn by deep confirmation, or which name was closest. | Classify on **funded** count, always build the explanation, show the funnel first, fix the gate7 counter, return per-name first-failing gate for the top N near-misses. Diff D5 |
| **F6** | **High** | Portfolio awareness / sizing | Capital default **2500** is a literal in `index.html:392`, `app.js:5197`/`:5946`/`:5169` and `app.py:4785` **[V-code]**. Nothing reads broker cash (`core/portfolio/service.py` has `account.cash` / `buying_power`). `/api/stock-picks` accepts no holdings (`app.py:4814-4825`). Sector and correlation caps see only the proposed book. Switching to US silently resets capital ≥ $10,000 to $2,500 (`app.js:5169`) **[V-code]**. The seed reports account cash (amount redacted) (from the user's screenshot; **[Unverified]** by me). At that cash level, every risk-sized position lands under the flat $100 minimum (section 2.6). | The book is sized on money the user may not have, ignores what they already own, and can silently discard a real US budget. | Prefill capital from account cash when connected ("Using account cash · edit"). Pass holdings (`ticker, value, sector`) into the scan for sector-before/after and correlation. Remove the US reset. Diff D6 |
| **F7** | **High** | Decision ladder | `FinalAction` = STRONG_BUY / BUY / ACCUMULATE / HOLD_EXISTING / WATCHLIST / AVOID / INVALID (`engine/types.py:14-20`). **No SELL or TRIM.** Picks can only emit BUY-family, WATCHLIST, AVOID or INVALID; HOLD_EXISTING is unreachable because holdings are unknown **[V-code]**. Ledger: 7 of 8 elite_pro calls are WATCHLIST **[V-run]**. My Portfolio runs TA in **full** mode (`app.py:6413`); Picks' deep confirm runs **fast+insider** mode. The same ticker can get different verdicts **[V-code]**. Contradictions: G2 bans grade D outright (`SP:11219`), while the core and committee keep D as ACCUMULATE at half size. The `RISK_GRADE_SIZE_MULTIPLIERS` 0.5 for D never reaches Picks **[V-code]**. | The two pages can contradict each other. "Avoid" is shown only as an absence. Users never see a sell on a name they hold. | One decision ladder (section 6), one engine call per ticker per session, shared by Picks and My Portfolio. Remove G2's D ban or make D WATCHLIST everywhere. |
| **F8** | **High** | Validation / methodology | The evidence registry (`core/apex_quant/_reports/evidence_registry.json`, read by `engine/evidence.py`) holds **one** strategy, `elite.composite` (the deep engine that caps every Pick), with status **`NO_EDGE`**: out-of-sample IC **−0.023 at 21d (t −1.41)** and **−0.034 at 63d (t −0.96)**, Q5−Q1 −0.58% / −1.15% (t ≈ −1.9), tested through 2026-08-25 (`elite_oos.txt`) **[V-run, file read]**. Component ICs: the **risk leg is negative** (score_risk −0.045 / −0.097), while annualized volatility (+0.073 / +0.139) and beta (+0.063 / +0.123) are positive. In that sample, the names the risk grade penalizes did better. Scope limits: 42 present-day large caps (survivorship), and only the technical + risk legs (40% of weight) are point-in-time. The registry's caveat still cites "CAGR 0.76% vs SPY 11.41%", a trade-level figure that `_reports/INVALIDATED.md` says predates the F01 execution fix and must not be cited. The scanner's own `elite_pro.composite` has **no entry**, hence `UNTESTED`. `permits_recommendation=False` only relabels output and never gates sizing **[V-code]**. Picks shows "Model evidence: UNTESTED" and never says that the binding Research check is `NO_EDGE`. | The page's final veto comes from a model whose only measured evidence is no edge, with the wrong sign. The grade-F gate that removed LNTH and MU rests on a risk score that is inversely related to forward returns in the one test that exists. Neither engine is fit to promise a BUY. | Show both evidence statuses on Picks. Remove the invalidated CAGR from the registry caveat. Regenerate the walk-forward with the corrected simulator (`python -m core.apex_quant.run elite-oos` / `elite-walkforward`). Add `elite_pro.composite` to the harness. Until then, treat both the risk-grade veto and the Research cap as *unvalidated policy*: keep them, since they reduce exposure, but label them as such (section 9). |
| **F9** | Medium | Config, thresholds and reason text | Reason text: "Risk Grade F blocks new-money **BUY**" is printed when the candidate was ACCUMULATE (LNTH). The scanner feeds analyst-consensus downside into `dcf_overvaluation_pct` (`SP:8836`), so a "DCF >25% over" reason can really be an analyst-target fact **[V-code]**. Minimum R:R: **1.8** in the scanner (`SP:1803`), **1.5** in the core (`engine/types.py:65`), "1.5:1" hard-coded in remedy text (`engine/blockers.py:60`, duplicated `app.js:1075`), "2.0:1" in a comment (`SP:11264`), "R:R ≥2.0, score ≥6.5, 25% sector cap" in the `get_top_recommendations` docstring (`SP:11152`). Actual: 1.8 / 5.5-or-median / 35% under $5k **[V-code]**. No config file exists. | Users and maintainers cannot know the real rule. Remedy text can name the wrong hurdle or the wrong action. | A single `engine/thresholds.py` (or a YAML loaded once) imported by both engines. Format remedy and reason text from it and from the actual candidate. Add an `ANALYST_DOWNSIDE` code. Expose `GET /api/thresholds` and render it in "Scan method". |
| **F10** | Medium | Score scales | A single card shows: scanner strength x/5 (`SP:6293-6355`), scanner score x/10 (`SP:8152-8296`), the ledger's ×10 of that (59.3), a deep composite /100 (65.8, `TA:5799`), Early Mom x/6 (`SP:6425`), and three different confidence tiers (`SP:8474`, `TA:6550`, `decision_engine.py:52`). HST's reasons list holds **"Composite score 59.3"** *and* **"Composite score 65.8"** **[V-run]**. The 0–10 scale is not centred: the median liquid name scores **3.47** and only the top decile reaches the BULL BUY bar of 6.5 **[V-run]**. Cross-sectional z-scores, the intended normalization, run only on names that already passed the per-name gates (2 here, minimum 30), so they never apply ("z-scores skipped: 2 scored names") **[V-run]**. | Nobody can tell which number decides. The thresholds act on an uncalibrated scale. | Publish one **Decision score (0–100)**, the one the ladder uses. Show factor sub-scores as bars on the same 0–100 scale. Drop strength stars and Early Mom as scores; keep Early Mom as a tag. |
| **F11** | Medium | Sizing | A deep-ACCUMULATE halving calls `apply_allocation(pick, alloc_before*0.5)` (`SP:11027`), which skips the minimum-trade check. Hand-derived: LNTH would fall to 1 share = $99.65 and HST to 3 shares = $67.53, both under the $100 minimum **[V-code; arithmetic]**. Not reproduced at runtime (it needs a deep ACCUMULATE verdict). Separately, `min_position_size` relaxes to 2% of capital only with fractional shares (`SP:2003-2014`), which the webapp never enables. So at $2,500, **10 names** were sized out before their R:R was checked **[V-run]**. In this scan none of the 10 would have cleared R:R 1.8 (max 1.64). But **GOOGL** (score 6.48, R:R 2.41, grade B) fell to **$99.5** after the earnings ×0.8 haircut and was dropped by $0.50 **[V-run]**. | Sub-minimum orders can be published. Small accounts lose qualified candidates to a flat $100 floor inside a percentage-risk sizer. | Route the halving through the same minimum / 1-share check. Make the floor capital-relative in whole-share mode too (whole-share cost permitting). |
| **F12** | Medium | Data freshness | The header "Updated 13m ago" turns red at **≥ 10 minutes of dashboard age** (`app.js:2356`), ignores market session (the scan ran at 21:43 ET, market closed), and nothing auto-refreshes the dashboard **[V-code]**. The card's "fresh" means "daily bar lag ≤ 1 completed session" (`engine/decision.py:76-94`). The two clocks measure different things and aren't labelled. | A red stale chip next to a "fresh" card undermines trust in both. | Session-aware header: after the close, the age of the last completed session governs. Label the chip "Market data" and the card "Daily bar". |
| **F13** | Medium | UX / redundancy | Per funded pick: entry ×4, stop ×4, target ×4, R:R ×3 (1 dp in the table, 2 dp elsewhere), why ×2–3. About **19 caveat sentences** per page plus 2–3 per card **[V-code]**. The entry-zone low is computed in JS with a 2%-of-price fallback (`app.js:6076-6082`), not in the engine. Progress sits at 20% for the whole scan with no counts **[V-run]**. | Noise hides the decision. The two R:R roundings look like two values. | Section 8: one value per pick, one disclaimer, verdict first, staged progress. |
| **F14** | Low | Mandate | Balanced and Aggressive are both ×1.00 (`app.py:4908-4912`). This is deliberate: comment `[B13]` says the engine's risk budget is the ceiling, and the UI discloses it. The sector cap profile is hard-wired to "balanced" regardless of mandate (`SP:1738`) **[V-code]** (see sensitivity, section 2.6). | The Aggressive option is a choice that changes nothing. | Either differentiate (risk budget, sector cap, conviction floor) or remove it from the UI. |
| **F15** | Low | Tests / regression | The regression suites named in the 2026-09-09 empty-book work, `test_empty_book_root_causes.py` and `test_insider_signal_calibration.py`, exist only on branch `quanta-review-redesign` (`064f747`, **not an ancestor of HEAD**) **[V-run, git]**. The corresponding fix names (`effective_min_position_size`, `_fund_at_least_one_share`) are absent from HEAD; partial equivalents exist. | Fixed defects can return unnoticed (F11 is one). | Port the two test files, then add the tests in section 9. |

---

## 4. Proposed diffs for the critical and high findings (not applied)

These diffs are minimal sketches against `4f878f8`. Line numbers are approximate. Every new threshold is a config key, not a literal.

### D1 (F1): corporate-event gate, AVOID on a pending acquisition

New module `stock-analysis/core/engine/corporate_events.py`:

```diff
+"""Corporate events that invalidate a directional price thesis.
+
+A name under a signed cash acquisition trades at a spread to the deal
+price; its historical volatility, structural highs and analyst targets no
+longer describe its upside. The scanner must not publish levels for it.
+"""
+from dataclasses import dataclass
+from datetime import date, timedelta
+from typing import Any, Dict, Optional
+
+from engine.thresholds import EVENTS  # LOOKBACK_DAYS, VOL_COLLAPSE_RATIO, FORMS
+
+DEAL_FORMS = EVENTS.FORMS   # {"DEFM14A", "PREM14A", "SC TO-T", "SC 14D9", "8-K:1.01"}
+
+@dataclass(frozen=True)
+class EventFlag:
+    code: str            # PENDING_ACQUISITION | POSSIBLE_PENDING_DEAL
+    text: str
+    source: str          # "SEC EDGAR <form> <filed>" or "price behaviour"
+    url: Optional[str] = None
+
+def detect(symbol: str, edgar, atr_pct_20: float, atr_pct_120: float,
+           today: Optional[date] = None) -> Optional[EventFlag]:
+    today = today or date.today()
+    res = edgar.recent_filings(symbol, today - timedelta(days=EVENTS.LOOKBACK_DAYS),
+                               forms=DEAL_FORMS)
+    for f in res.get("filings", []):
+        is_merger_8k = f["form"] == "8-K" and "1.01" in f.get("items", [])
+        if f["form"] in {"DEFM14A", "PREM14A", "SC TO-T", "SC 14D9"} or is_merger_8k:
+            return EventFlag("PENDING_ACQUISITION",
+                             f"Pending acquisition ({f['form']} filed {f['filed']}); "
+                             "upside is capped at the deal terms",
+                             f"SEC EDGAR {f['form']} {f['filed']}", f.get("url"))
+    # Secondary, lower-confidence signal: realized range collapsed.
+    if atr_pct_120 > 0 and atr_pct_20 / atr_pct_120 < EVENTS.VOL_COLLAPSE_RATIO:
+        return EventFlag("POSSIBLE_PENDING_DEAL",
+                         "Daily range collapsed to "
+                         f"{atr_pct_20:.1%} from {atr_pct_120:.1%}; check for a pending deal",
+                         "price behaviour")
+    return None
```

`core/portfolio/sources.py`: let callers pass their own form set, so My Portfolio keeps its current set.

```diff
-    def recent_filings(self, symbol: str, since: date) -> Dict[str, Any]:
+    def recent_filings(self, symbol: str, since: date, forms=None) -> Dict[str, Any]:
...
-                out = {"status": "ok", "cik": cik, "filings": _parse_recent(data, cik, since)}
+                out = {"status": "ok", "cik": cik,
+                       "filings": _parse_recent(data, cik, since, forms or FORMS)}
```

`SP_ELITE_PRO.py`, in `analyze_stock` after the levels are computed (~`:8470`):

```diff
+            _evt = corporate_events.detect(ticker, self._edgar, atr_pct_20, atr_pct_120)
+            if _evt and _evt.code == "PENDING_ACQUISITION":
+                self._record_filter('pending_acquisition')
+                self._record_rejection(ticker, gate='corporate_event', value=_evt.text)
+                if not research_mode:
+                    return None
+            elif _evt:
+                event_flags.append(_evt)     # rendered as a caution; caps at WATCHLIST
```

`engine/risk_overrides.py`: map `PENDING_ACQUISITION` to AVOID for new money, and to SELL in `holder_rule` when the spread is below `EVENTS.ARB_SPREAD_MIN`.

### D2 (F2): publish the levels that bind the decision

`SP_ELITE_PRO.py:10991-11003` (ONE DECISION cap): keep the cap, and record the Research engine's levels.

```diff
             if (raw_deep_fa and deep_fa != FinalAction.INVALID.value
                     and self._rank_action(raw_deep_fa) < self._rank_action(deep_fa)):
                 deep_fa = raw_deep_fa
                 deep_reasons = deep_reasons + list(
                     getattr(res, 'final_action_reasons', []) or []) + [
                     f"Capped at the Research Decision ({raw_deep_fa}) for this name"]
                 recon_codes = recon_codes + [
                     c for c in (getattr(res, 'final_action_codes', []) or []) if c not in recon_codes
                 ] + ["RESEARCH_DECISION_CAP"]
+                _ts = getattr(res, 'trade_setup', None)
+                if _ts is not None:
+                    # The binding verdict was computed on THESE levels; the card
+                    # must show them, or its R:R contradicts its own reason.
+                    binding_levels[pick.ticker] = {
+                        'source': 'research',
+                        'entry': round(float(_ts.entry_price), 2),
+                        'stop': round(float(_ts.stop_loss), 2),
+                        'target': round(float(_ts.horizon_target), 2),
+                        'risk_reward': round(float(_ts.risk_reward_ratio), 2),
+                        'horizon_days': TradingConfig.RESEARCH_HORIZON_DAYS,
+                        'target_basis': '12-month valuation target pro-rated to horizon',
+                    }
```

And in the record loop (`:11063`):

```diff
             rec.setdefault('deep_risk_grade', deep_grades.get(rec['ticker'], ''))
+            if rec['ticker'] in binding_levels:
+                rec['binding_levels'] = binding_levels[rec['ticker']]
```

`webapp/app.py:5196` (the deep_confirmation dict):

```diff
                 "deep_risk_grade": record.get("deep_risk_grade", ""),
+                "binding_levels": record.get("binding_levels"),
```

`static/app.js`, in `quickRefTableHtml` and `pickCard`: when `deep_confirmation.binding_levels` exists, render those levels and R:R, labelled "Research levels (binding)". Show the scanner's levels only inside the disclosure, as "Scanner reference levels". Format R:R with 2 dp everywhere.

```diff
-    const rr = Number(p.risk_reward_ratio || 0);
+    const bind = p.deep_confirmation?.binding_levels;
+    const rr = Number(bind?.risk_reward ?? p.risk_reward_ratio ?? 0);
...
-                            : (rr > 0 ? fmtNumber(rr, 1) : '--');
+                            : (rr > 0 ? fmtNumber(rr, 2) : '--');
```

### D3 (F3): one risk grade with published drivers

`SP_ELITE_PRO.py:10932`:

```diff
-            deep_grades[pick.ticker] = str(getattr(getattr(res, 'risk', None), 'risk_grade', '') or '')
+            _risk = getattr(res, 'risk', None)
+            deep_grades[pick.ticker] = str(getattr(_risk, 'risk_grade', '') or '')
+            deep_grade_drivers[pick.ticker] = list(getattr(_risk, 'grade_drivers', []) or [])
```

```diff
             rec.setdefault('deep_risk_grade', deep_grades.get(rec['ticker'], ''))
+            rec.setdefault('deep_risk_grade_drivers', deep_grade_drivers.get(rec['ticker'], []))
```

`app.py:5196` passes `deep_risk_grade_drivers` through. `decision_for_scan_result` (`SP:3416`) publishes the **binding** grade, the more restrictive of the two, plus `blockers` from `recon_codes`:

```diff
-        risk_grade=str(rec.risk_grade or ''),
+        risk_grade=_more_restrictive(rec.risk_grade, (rec.deep_confirmation or {}).get('deep_risk_grade')),
+        blockers=list((rec.deep_confirmation or {}).get('reason_codes') or []),
```

`app.js:6386`: replace the prose with the drivers.

```diff
-  ...`<p class="qr-caveat">Risk grade: scanner ${...} · additional checks ${...}. The restrictive result controls.</p>`...
+  ...`<p class="qr-caveat">Risk grade ${g} (binding): ${drivers.filter(d => d.points).map(d => `${escapeHtml(d.label)} +${d.points} (${escapeHtml(d.detail)})`).join(' · ')}</p>`...
```

Follow-up (section 5.3): unify the lookback in `engine/risk_grade.py`, and winsorize returns for the tail score.

### D4 (F4): report universe selection, and stop repeating the timeout

`SP_ELITE_PRO.py:10070-10083`:

```diff
             if _worker.is_alive():
                 logger.warning(...)
                 PROVIDER_STATE.note_slow()
+                self._universe_selection = 'even_spread_fallback'
                 return self._even_sample(tickers, max_candidates)
```

(Do the same at the two other `_even_sample` fallbacks, and set `'liquidity_ranked'` on success.)

`app.py:5491`:

```diff
             "funnel": {
                 "universe_size": universe_size,
+                "universe_selection": getattr(quanta, "_universe_selection", "unknown"),
```

`app.js` verdict line: "Scanned 250 · *even spread (liquidity ranking timed out)*" in the caution tone. Root fix (not a diff): rank liquidity from the cached `Ticker.history` frames, which the scan prefetches anyway (`SP:9943-9967`, budget 180s), or cache the bulk `yf.download` frame through the same JSON disk cache.

### D5 (F5): explain a watch-only result, show the funnel, fix the gate-7 count

`webapp/app.py:5454-5466`:

```diff
-        final_pick_count = len(picks)
+        final_pick_count = len(picks)
+        funded_pick_count = sum(1 for d in picks if d.get("is_funded"))

         scan_status = _classify_scan_status(
             universe_size=universe_size,
             exception_rate=exception_rate,
             analysis_failed_count=analysis_failed_count,
-            final_pick_count=final_pick_count,
+            final_pick_count=funded_pick_count,
             provider_rate_limited_count=provider_rate_limited_count,
             provider_circuit_open=provider_circuit_open,
         )

         empty_scan_explanation = ""
-        if final_pick_count == 0 and scan_status in {"no_picks", "partial"}:
+        if funded_pick_count == 0 and scan_status in {"no_picks", "partial"}:
```

and in the funnel dict:

```diff
                 "final": final_pick_count,
+                "funded": funded_pick_count,
+                "deep_confirm_withdrawn": sum(1 for r in dc_records if r.get("downgraded")),
+                "near_misses": _safe_serialize(getattr(quanta, "_near_misses", [])[:10]),
```

(Check that `renderPicks` still renders watch rows when `scan_status == "no_picks"`. Today that status implies zero rows; add a JS test.)

`SP_ELITE_PRO.py:11887` counts the list after backfill has replaced its contents:

```diff
-            self._gate_stats['gate7_sector_cap'] = len(recommendations)
+            self._gate_stats['gate7_sector_cap'] = sized_before_deep_confirm   # captured before :11850
+            self._gate_stats['gate8_research_confirmed'] = len(
+                [r for r in final if (r.decision or {}).get('action') in DEEP_ACTIONABLE])
```

`SP_ELITE_PRO.py` `_record_filter` call sites: add `_record_rejection(ticker, gate, value, threshold)` next to the `_record_filter` calls at `:8179`, `:8385`, `:8466`, `:8668` and `:8692`. Keep the 10 nearest by normalized distance (`(threshold − value)/threshold`) in `self._near_misses`.

`webapp/app.py:5279`: limit funded rows, not published rows.

```diff
-        recs = recs[:params["max_positions"]]
+        # max_positions bounds FUNDED positions. Withdrawn/watch rows are
+        # information, not positions, and must never displace a funded pick.
+        _funded = [r for r in recs if _is_funded_rec(r)][:params["max_positions"]]
+        _watch = [r for r in recs if not _is_funded_rec(r)][:params.get("max_watch_rows", 10)]
+        recs = _funded + _watch
```

`app.js:6045`: render the funnel card **above** the picks stream, not inside `<details>`.

### D6 (F6): capital from account cash, holdings into the scan, no silent reset

`static/app.js:5167-5170`:

```diff
     if (capInput) {
       capInput.min = '100';
-      // Reset to default only if value looks INR-scale.
-      if (Number(capInput.value || 0) >= 10000) capInput.value = '2500';
+      // Convert only a value the India branch itself set; never discard a
+      // user-entered US budget.
+      if (capInput.dataset.autoSet === 'IN') { capInput.value = capInput.dataset.usValue || ''; }
     }
```

The India branch (`:5161`) then has to record `dataset.autoSet = 'IN'` and `dataset.usValue = <previous value>` whenever it substitutes 200000.

`static/app.js` `runScan`: when `/api/portfolio` is connected, prefill `#sp-capital` from `account.cash` with a visible "Using account cash $X · edit" hint. Send `holdings: [{ticker, market_value, sector}]`.

`webapp/app.py:4814` accepts `holdings` (validated list, ≤ 500 rows) into `params`. `_run_stock_picks` passes it to `get_top_recommendations`, which needs a **new** `existing_positions` parameter (today's signature is `max_positions, diversify_sectors, allow_sector_cap_relaxation`, `SP:11147`). Seed the sector-room and correlation-gate state from it, so existing exposure counts against the caps.

`_more_restrictive`, `binding_levels`, `deep_grade_drivers`, `_record_rejection`, `_near_misses`, `_universe_selection`, `sized_before_deep_confirm`, `existing_positions`, `_is_funded_rec`, `max_watch_rows`, `engine/thresholds.py`, `engine/corporate_events.py`, `TradingConfig.RESEARCH_HORIZON_DAYS` and the `forms=` argument are **new names** these diffs introduce. Nothing else in the codebase uses them yet.

### F7 (high): no diff

F7 is a design change, not a local fix. The rule table is in section 6. The smallest first step is to make My Portfolio and the Picks deep confirmation share **one** `TA.analyze` call per ticker per session, in the same mode, through a session cache keyed `(ticker, as_of)`. That way the two pages cannot disagree while the ladder is being built.

---

## 5. Algorithm and logic changes

### 5.1 Levels
- **One `TradeSetup` per Decision**, built in `engine/` from config: entry basis, ATR period and smoothing, stop multiple and floor, target method, horizon, costs. Both engines call it.
- Until that exists, the card shows the levels of whichever engine's verdict binds (D2).
- The entry zone moves to the engine (published `entry_zone_low` / `entry_zone_high`). Delete `entryZoneLow` in JS.
- Report R:R at the **zone top**, the worst fill (which is what is computed today), and say so: "Net R:R at top of zone".
- **LNTH case:** a 3% stop floor on a name whose ATR is 0.6% puts the stop at 5 ATR. That is fine for noise, but meaningless if the price is pinned by a deal. F1's event gate pre-empts it.
- **Stale structural targets:** drop a 52-week high older than `TARGET_STRUCT_MAX_AGE_SESSIONS` (proposed 63, matching the deep horizon) from the target mean. LNTH's $111.86 high printed on 2026-06-30, about 66 sessions before the scan, so it would have been excluded.

### 5.2 Gates
- **Keep the deep engine as the ceiling.** Its strictness is intentional and should not be weakened to manufacture picks; it is not the defect. The defect is that it is applied to numbers the user is not shown.
- Pick one of two policies, and record it (Q1):
  - **(a) Research levels govern.** Show them (D2).
  - **(b) The cap uses only qualitative codes**, as OPTION-A intended. Price gates then apply to the scanner's published levels only.
- G2's grade-D ban versus the core's D → ACCUMULATE (½ size): pick one. Recommendation: allow D at half size everywhere, so the contract matches the code comments.
- Report every gate's survivors, including "deep confirmation" as its own funnel row (`deep_confirmation.downgrades` already exists).

### 5.3 Risk grade
- One function, one lookback (`RISK_GRADE_LOOKBACK_DAYS`, proposed 504), all 5 inputs.
- **Event-robust tail measure:** compute skew and kurtosis on **winsorized** returns (1%/99%), or score tail on CVaR-95 relative to volatility. One gap day should not set the grade for two years.
- Publish the drivers.

### 5.4 Scores
- One Decision score from 0 to 100: the composite that `score_bands` uses.
- The scanner's 0–10 becomes a factor-blend input. Conviction stars and Early Mom stop being scores.

### 5.5 Sizing
1. Show the formula on the card: `risk $ = capital × 0.6% × signal × score adj → shares = risk $ ÷ (entry − stop)`.
2. Make the minimum trade capital-relative in whole-share mode too: `floor = min(MIN_POSITION_SIZE, MIN_POSITION_PCT × capital)`, with `MIN_POSITION_PCT` proposed at 2%. A position is valid when its risk budget buys at least one whole share.
3. Halving goes through the same minimum.
4. **Denominators:** sector caps use **total capital** in code (`SP:11370`, `self.capital`). The summary's sector weights use **deployed** capital (JS). Pick total capital for both, and label it.
5. Funding source: cash first, then proceeds of Trim/Sell decisions from My Portfolio (section 8.3, element 4).

### 5.6 Hard-coded literals to move into config (and show where they decide)

| Literal | Value | Location |
|---|---|---|
| Committee hurdles | upside 8%, BUY score 6.5, RSI 75, analyst −5%, R:R 1.6/1.8 | `SP:9419-9459` |
| VIX bands / exposures | 12 / 20 / 25 / 30 | `SP:5191-5203`, `:6575-6590` |
| Regime signal thresholds | 7.5/6.5, BEAR 9.0/8.0, VOLATILE 8.7/7.7 | `SP:9037-9055` |
| RSI overbought | 70 / 80 | `SP:9031` |
| Conviction floor / pool size | 3 if pool ≥ 20 else 2; pools ≤ 8, ≤ 4 | `SP:11240-11310` |
| Cost viability | 2× round trip | `SP:11597` |
| Deep ACCUMULATE halving | 0.5 | `SP:11027` |
| Mandate scale | 0.70 / 1.00 / 1.00 | `app.py:4908-4912` |
| Risk-grade points and bands | 25/35/50% vol, 1.2/1.5 beta, −25/−40% drawdown, 50/70 tail; A ≤ 3 … F ≥ 9 | `engine/risk_grade.py:46-93` |
| DCF cap | 25% | `engine/risk_overrides.py:156` |
| Liquidity score bands | $1M / $10M / $50M | `TA:3444-3457` |
| Deep horizon | 63 sessions | `TA:1059` |
| Deep costs | 5 bps spread + 3 bps slippage | `TA:6335-6336` |
| Capital default / US reset | 2500 / 10,000 | `index.html:392`, `app.js:5169/5197/5946`, `app.py:4785` |
| Header stale threshold | 10 / 5 min | `app.js:2356` |

### 5.7 Regime
- **How it's computed:** BULL = SPY > 50-day MA > 200-day MA and VIX < 25 (`SP:5226`). NORMAL = VIX 12–20 → exposure 1.0 (`SP:5195`) **[V-code]**.
- **What it changes:** signal thresholds, composite weights, available capital, and the 2.5× target cap.
- **What it doesn't change:** score floors, R:R, grades, sector caps.
- `recommended_cash_pct` and `factor_weights` are set and never used (`SP:5274`, `:5290`). Either use them or delete them.
- Show the regime's effect in "Scan method": "BULL raises the bar for BUY to 6.5/10".

---

## 6. Decision ladder (one engine, both pages)

Thresholds in braces are config keys (current values in parentheses). The rule is evaluated by **one** function, `engine.ladder.decide(inputs, holding=None)`. Picks calls it with `holding=None`; My Portfolio calls it with the position. Both use the same deep-engine mode and the same session cache, so a ticker gets one verdict per session.

| Decision | Applies to | Inputs | Rule (first match wins, top to bottom) | Required evidence | Size treatment |
|---|---|---|---|---|---|
| **Avoid** | New names | event flags, data validity, grade, measured red flags | `PENDING_ACQUISITION` / `DELISTING` / `INVALID`, **or** score < {avoid_score} (45), **or** grade F **and** a measured red flag | The failed gate, named, with its value and threshold, e.g. "Pending cash acquisition at $102.50 (merger agreement 2026-08-03; DEFM14A on EDGAR)" | None. Listed in "Rejected, with reason" (top N by score) |
| **Watch** | New names | as for Buy | Score ≥ {accumulate_score} but a **price / timing gate** fails (R:R < {min_rr}, RSI > {rsi_ob}, earnings window, grade F without a red flag) | The failed gate, its value, and the distance to the trigger ("R:R 1.23 → needs 1.50; reached at spot ≤ $22.40 with the stop unchanged, or at a 63-session target ≥ $23.66") | None. Levels are monitoring references |
| **Accumulate** | New names, and holders below target weight | score, grade, R:R, timing | Score in [{accumulate_score}, {buy_score}) (55–65), **or** a Buy capped by grade D / timing WAIT / bearish reversal | R:R ≥ {min_rr} on the **published** levels; grade ≤ D | {accumulate_size_ratio} × the Buy risk budget (today 0.6/0.8 = 0.75), subject to the minimum trade. A grade-D cap halves it again (½) |
| **Buy** | New names, and holders below target weight | as above | Score ≥ {buy_score} (65), grade ≤ C, R:R ≥ {min_rr}, no event flag, Research Decision ≥ Buy | As above, plus deep confirmation agrees | Full risk budget (0.6% × score adj), all caps |
| **Hold** | **Holders only** | score, grade, weight | Score ≥ {reduce_score} (45), weight ≤ {trim_review_weight}, no exit trigger | Current score and grade vs entry | Keep the position; no new money unless Accumulate also fires |
| **Trim** | **Holders only** | score, grade, weight | Score < {reduce_score} (45), **or** grade F, **or** weight > {trim_review_weight} | The rule that fired (`holder_rule` basis) | To target weight or ½ (from config); proceeds become the funding source on Picks |
| **Sell** | **Holders only** | score, measured red flags, events | Score < {exit_score} (35) **and** a measured red flag (target < entry, DCF over, heavy insider selling, bearish reversal), **or** a pending cash acquisition with spread < {arb_spread_min} | The red flag with source and date | Full exit; proceeds become the funding source |

Rules:
- Picks never shows Hold, Trim or Sell for names the user doesn't own. My Portfolio never shows Buy for a name already above target weight.
- **Do not create a decision the data cannot support.** No Strong Buy until `evidence.status == VALIDATED`. Until then STRONG_BUY folds into Buy, and the label "Strong" is hidden. That decision is the PM's (Q4).

---

## 7. Missing items: include or skip

Benchmark sources: SA Quant / Factor Grades FAQ and Alpha Picks help pages (fetched); Yahoo AAPL quote, analyst-insights and Argus report pages (fetched); Barron's (blocked, so secondary sources only, marked unverified). Full matrix in the appendix.

| Item | SA | Barron's | Yahoo | Decision | Why (which decision it improves) |
|---|---|---|---|---|---|
| **Corporate events (M&A, tender, spin, delisting)** | Exits Alpha Picks on M&A (third-party review) | — | partial (news) | **Include** | Turns LNTH-type names into Avoid / Sell (F1) |
| **Earnings date and window** | Yes | prose | Yes | **Include** | Already used for sizing (×0.5/×0.8). Show it: "Earnings in 14d, size halved" |
| **Analyst consensus and target range (low/avg/high, count)** | Yes | partial | Yes | **Include** | The target already uses the analyst mean. Show the range and count so the user can judge it. Data in `.info` (`targetLow/Mean/HighPrice`, `numberOfAnalystOpinions`) |
| **Factor grades (valuation, growth, profitability, momentum, revisions) vs sector** | Yes (A+–F) | No | partial | **Include, as bars on 0–100 with sector percentile** | Replaces the five score scales (F10). Inputs already computed (technical, fundamental, momentum, quality, value, revisions) |
| **EPS revisions (30d net, fwd EPS change)** | Yes | — | partial | **Include** | Computed and logged (`analyze_stock` "EPS revisions…") but not shown; a Buy/Watch tiebreaker |
| **Fair value / valuation target** | grade only | multiples | Yes (premium) | **Include the deep engine's 12-month base target, labelled** | It is what actually decides HST (F2) |
| **Price chart with zone / stop / target bands** | — | — | Yes | **Include** | Lets a user judge level placement (e.g. LNTH's pinned price is obvious at a glance) |
| **Thesis and key risks (generated)** | partial | Yes | paywalled | **Include as one line each, from the data** (setup type + strongest factor; binding risk = the gate) | Explains Buy vs Watch without prose padding |
| **Track record per pick (since-pick return vs SPY)** | Yes | yearly report card | — | **Include once ledger rows resolve** | Required for validation (section 9). The ledger already stores reference / target / stop / benchmark |
| **Sell rules** | Yes | — | — | **Include** (section 6 ladder) | Turns Picks into a full lifecycle |
| Technical-events feed (Trading Central-style) | momentum grade | — | premium | **Skip** | Would duplicate momentum and Early Mom. No decision change |
| Hiring score, ESG, insider-sentiment widget | — | — | Yes | **Skip** | Insider is already a gate. The others don't change buy/hold/sell |
| Authors' and Wall St rating blend | Yes | — | — | **Skip** | Consensus range covers it. Author ratings aren't available as data |
| Quant "Strong Buy" label | Yes | — | — | **Skip until validated** | Section 6 rule: no decision the evidence can't support |

---

## 8. UI and layout

### 8.1 Redundancy rules
- **Once per pick:** entry zone, stop, target, net R:R (2 dp everywhere), why (the binding gate), size.
- The Research Book table becomes the *only* place for levels. Cards show thesis, factors and chart, not the same numbers again.
- **One disclaimer**, persistent in the footer: "Educational research, not investment advice. Model untested; sizes are research risk budgets." Remove the other ~18 caveat sentences or merge them into tooltips on the figure they qualify.
- One evidence badge in the verdict line ("Model: untested").

### 8.2 Page order and wireframe

```
┌ Picks ─────────────────────────────────────────────────────────────────────┐
│ [Scan setup ▸ collapsed after a run: US · account cash ▾ · Balanced ·  │
│   max 5 · diversify]   [Run scan]                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ VERDICT  Hold cash · 0 funded · 1 on watch · 1 avoided (pending deal)       │
│ BULL · normal vol · scanned 250 (liquidity-ranked ✓) · data: daily bar      │
│ 2026-10-01 · Model untested                                                 │
├──────────────────────────────┬──────────────────────────────────────────────┤
│ SCAN FUNNEL (bars)           │ CLOSEST TO QUALIFYING                        │
│ 250 scanned                  │ HST  Watch  R:R 1.23 → 1.50  (spot ≤ $22.40, │
│ 182 tradeable                │       or 63-session target ≥ $23.66)         │
│  91 score ≥ 3.5              │ GOOGL Sized out  $99.5 < $100 min            │
│  37 signal BUY/ACC           │ MMM  Watch  R:R 1.71 → 1.80                  │
│  27 sizeable                 │ … (top 5 by distance)                        │
│   4 R:R ≥ 1.8                │                                              │
│   2 committee / grade        │                                              │
│   2 sized ($457)             │                                              │
│   0 after Research check     │                                              │
├──────────────────────────────┴──────────────────────────────────────────────┤
│ PICKS (funded first, then watch) — compact cards                            │
│ ┌ HST · Host Hotels · Real Estate · WATCH ────────────────── $22.51 ┐       │
│ │ [90-day price sparkline with entry zone / stop / target bands]     │       │
│ │ Valuation ███▌  Quality ██▎  Momentum ███  Revisions ██▉  (0–100) │       │
│ │ Binding gate: Research R:R 1.23 < 1.50 (entry 22.56 · stop 21.86 · │       │
│ │   63-session target 23.47) · Next: spot ≤ 22.40 or target ≥ 23.66  │       │
│ │ Consensus mean $25.29 (count, low–high) · Earnings date · Grade C  │       │
│ │   (vol +1, drawdown +1, tail +1)  ▸ Thesis, sources, history       │       │
│ └───────────────────────────────────────────────────────────────────┘       │
│ ┌ LNTH · AVOID · Pending cash acquisition at $102.50 (agreement 08-03) ┐    │
├─────────────────────────────────────────────────────────────────────────────┤
│ PORTFOLIO FIT (when connected)                                              │
│ Funding: account cash · proposed trims $0 │ sector before→after │ corr. with    │
│ top-3 holdings │ limit breaches: none                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ TRACK RECORD (when ≥ MIN_SAMPLE resolved rows) · hit rate · avg excess vs   │
│ SPY · max drawdown                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ Watchlist export · Scan method & thresholds (from /api/thresholds) ▸        │
│ Footer: one disclaimer                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 New elements (5, each tied to a decision and fed by real data)

| # | Element | User decision it informs | Data source (exists?) |
|---|---|---|---|
| 1 | **Scan funnel chart** | "Is cash the right answer, or is the scan broken?" | `funnel.filter_stats` / `gate_stats` (exist). Add the universe-selection and deep-confirm rows (D4, D5) |
| 2 | **Compact pick card with sparkline and bands, plus factor bars** | Buy/Watch at these levels? | Bars already fetched (1y). Factor sub-scores exist on `StockAnalysis`. Levels from the single `TradeSetup` |
| 3 | **Closest to qualifying** | "What would change this?" | Needs per-name first-failing gate and value (D5). Research-mode data already computes them |
| 4 | **Portfolio fit panel** | Fund from cash or trims? Would a limit break? | `core/portfolio` (cash, holdings, sectors), plus a holdings parameter on the scan (D6) |
| 5 | **Pick track record** | Trust the model? | `tracking/ledger.py` + `/api/track-record` (exists). Show only when resolved rows ≥ `MIN_SAMPLE` |

### 8.4 States

| State | Spec |
|---|---|
| **Loading** | Staged: "Universe 250 (liquidity-ranked) → Scored n/250 → Gates → Research check k/m". Stream counts in the SSE `message` (`_run_stock_picks` already has the numbers per stage). Replace the 20%→80% jump with `analysed/universe` |
| **Empty (0 funded)** | Verdict "Hold cash", the funnel, the top-5 closest-to-qualifying, and one sentence on what would change it, computed from the published levels (e.g. "HST clears the Research R:R of 1.5 at a close ≤ $22.40"; "At this cash level no position reaches the $100 minimum; the nearest name needs ≈ $6,800") |
| **Error** | Failure box with the provider status and "Retry in safe mode" (exists). Never show a partial book as complete |
| **Partial** | Banner "Analysed n/250 (provider throttled)". Funnel denominators use the analysed count |
| **Stale result** | Scan result age: "Scan 2h ago · prices from 2026-10-01 close". After 1 session, "Re-run to refresh" |

### 8.5 Component list
`PicksSetupBar` · `VerdictLine` · `FunnelChart` · `NearMissList` · `PickCard` (`Sparkline`, `FactorBars`, `GateLine`, `ConsensusLine`, `RiskDrivers`) · `RejectedList` · `PortfolioFitPanel` · `TrackRecordPanel` · `ExportCard` · `MethodDrawer` (thresholds from `/api/thresholds`) · `FooterDisclaimer`.

---

## 9. Validation plan and tests

### 9.1 Smallest credible validation

**Build on what exists.** `core/apex_quant` already has the pieces:
- a validation harness: `elite_oos.py`, `walkforward.py`, `simulator.py`, `costs.py`, `validation.py`, `evidence_registry.py`;
- tests for leakage, execution semantics, reproducibility and costs (`apex_quant/tests/`).

It has validated only `elite.composite` (result: NO_EDGE, F8), and its trade-level results are invalidated pending a rerun with the F01 execution fix (`_reports/INVALIDATED.md`). The smallest credible plan:

0. **Rerun** `python -m core.apex_quant.run elite-oos` and `elite-walkforward` with the corrected simulator. Remove the invalidated CAGR from the registry caveat.
1. **Point-in-time walk-forward backtest** of the *Decision ladder* for `elite_pro.composite`, not of the raw score:
   - Weekly rebalance, 2018–2026.
   - 3-year train / 1-year test windows rolled annually.
   - Thresholds frozen per window.
   - Costs = the tiered model already in `TradingConfig`, plus a 10 bps slippage stress.
   - Benchmark SPY plus an equal-weight universe.
   - Needs point-in-time constituents (survivorship) and fundamentals as of the filing date (look-ahead). yfinance `.info` is **current-only**, so fundamental, analyst and insider factors **cannot be backtested honestly** with it. Either source PIT data, or validate only the price-derived factors and keep the others labelled "unvalidated" (Q5).
2. **Forward paper log** (exists: `tracking/ledger.py`):
   - Record every published Decision, including Watch and Avoid, as the counterfactual.
   - Resolve at the horizon: hit target / stop first, return, excess vs SPY.
   - The `UNTESTED` label stays until ≥ `MIN_SAMPLE` resolved Buy/Accumulate rows show a positive excess return with a confidence interval excluding 0.
   - Today: 8 elite_pro rows, **0 resolved**.
3. **Calibration:** bucket the confidence tiers. Their realized hit rates must be monotonic before any tier is shown as "confidence".
4. **Test the vetoes too, not only the score.**
   - Risk-grade bucket: forward 63-day return and drawdown by grade A–F.
   - Research cap: the forward returns of withdrawn picks vs funded ones. The ledger already records both: `context_json.publication`.
   - The one existing test shows the risk leg with negative IC (F8). Until the vetoes have their own evidence, they are policy, not findings.

### 9.1a Methodology review (quant)

| Topic | Finding | Status |
|---|---|---|
| Momentum and technical | Price-derived and point-in-time reconstructible. Technical leg IC ≈ 0 (−0.007 / −0.000) in the one test | [V-run, file read] |
| Quality, value, fundamentals, revisions, analyst, insider | From yfinance `.info` / current snapshots. **No point-in-time source**, so any backtest using them has look-ahead bias. The harness correctly holds them constant | [V-code] |
| Survivorship | The harness universe is 42 present-day large caps. The live scanner's universe is today's index constituents. Both overstate the past | [V-code] |
| Leakage | `apex_quant/tests/test_leakage.py` exists. The reported \|IC\| < 0.05 shows no sign of leakage | [V-code] |
| Costs | Scanner: tiered 30–50 bps round trip for large/mid caps, charged to both reward and risk. Deep engine: flat 11 bps. **Two cost models for one decision** (F2) | [V-code] |
| Overfitting | 40+ hand-set literals (section 5.6). Code comments justify rules by individual tickers ("observed live: GOOGL, TXN, LNTH, CSCO", `SP:10941`). Rules are tuned on anecdotes; none are fitted or validated | [V-code] |
| Regime | Rule-based (SPY 50/200-day MA, VIX bands). It changes thresholds and weights. Its effect on outcomes is untested | [V-code] |
| Confidence tiers | Three separate heuristics (`SP:8474`, `TA:6550`, `decision_engine.py:52`). The UI correctly says "NOT a validated win probability". No calibration exists | [V-code] |
| "UNTESTED" label | Driven by `engine/evidence.py`, which fails closed (a missing registry means not validated). It cannot flip to VALIDATED without a registry entry, so the label will persist correctly | [V-code] |

### 9.2 Tests to add

| Test | Asserts |
|---|---|
| `test_pending_acquisition_avoid` | an 8-K 1.01 / DEFM14A in the last 12 months gives AVOID with `PENDING_ACQUISITION`, levels hidden |
| `test_binding_levels_are_published` | when `RESEARCH_DECISION_CAP` binds on `LOW_REWARD_RISK`, the card's published R:R equals the R:R in the binding reason |
| `test_option_a_not_undone` (if policy (b)) | the cap ignores price-gate codes |
| `test_single_risk_grade` | scanner and deep engine produce the same grade for the same bars; drivers are present in the payload |
| `test_watch_only_scan_explained` | 0 funded + N watch gives `scan_status="no_picks"`, a non-empty explanation, and the watch rows still rendered |
| `test_max_positions_limits_funded_only` | with `max_positions=1`, a withdrawn pick plus a backfill still publish both rows, and a funded pick is never displaced by a watch row |
| `test_small_capital_explanation` | at the account's cash level the explanation names the $100 minimum and the capital needed for the nearest name |
| `test_evidence_statuses_on_picks` | the payload carries both the scanner's (`UNTESTED`) and the Research engine's (`NO_EDGE`) evidence status |
| `test_gate7_after_backfill` | `gate7_sector_cap` equals the sized count |
| `test_universe_selection_reported` | pre-filter timeout gives `funnel.universe_selection == "even_spread_fallback"` |
| `test_halving_respects_minimum` | deep ACCUMULATE halving never publishes < `min_position_size` |
| `test_min_trade_capital_relative_whole_shares` | at $2,500 a $92 risk-sized position with share price < $92 is funded |
| `test_thresholds_single_source` | remedy text contains `RISK_REWARD_MIN` formatted from config |
| `test_picks_portfolio_parity` | same ticker, same session gives the same action on Picks and My Portfolio |
| `test_regime_boundaries` | VIX 11.99/12/20/25/30, and SPY/MA crossovers |
| `test_capital_us_reset_removed` (JS) | a US capital of 25,000 survives a market toggle |
| `test_rr_rounding_consistent` (JS) | table and chip render the same 2 dp string |
| `test_header_session_aware` (JS) | after the close, a 13-minute-old dashboard is not "stale" |
| Port `test_empty_book_root_causes.py`, `test_insider_signal_calibration.py` | from `quanta-review-redesign` |

---

## 10. Open questions and unverified items

**Open questions (owner decisions):**
- **Q1.** When the scanner and the Research engine disagree on price levels, which governs? (a) Research levels govern and are shown, or (b) the cap applies only qualitative red flags. This audit recommends (a): it keeps the deep check strict and makes it visible.
- **Q2.** Is LNTH-style merger-arb ever in scope? (Recommend: no. Avoid, plus Sell for holders when the spread < {arb_spread_min}.)
- **Q3.** Should Picks prefill capital from account cash by default, or require an explicit "use account cash"?
- **Q4.** Hide STRONG_BUY until evidence is VALIDATED?
- **Q5.** Is a point-in-time fundamentals source in budget? Without one, only price factors can be validated.
- **Q6.** Should the Aggressive mandate be removed, or given a real effect?

**Unverified:**
- A second-source price check: Stooq returned 404 and no keyless alternative was available.
- The user's account cash (amount redacted) (from the seed screenshot; not read from the broker).
- F11's sub-minimum halving at runtime (code path and arithmetic only).
- Whether Polygon bars, used for VIX/SPY, are adjusted.
- The Barron's feature set (site blocked; secondary sources only).
- Seeking Alpha's sell rules (third-party review; the SA help article returned 403).
- Published performance claims by competitors (snippets only).

---

## Appendix A: competitor matrix (per pick)

| Feature | Seeking Alpha | Barron's | Yahoo Finance |
|---|---|---|---|
| Factor grades vs sector | Yes, A+–F, 5 factors, daily (help FAQ) | No (unverified) | Partial (dividend, hiring, insider scores) |
| Rating scale | Strong Buy … Strong Sell, 1–5 | No in-house quant rating (unverified) | Consensus 1–5; Argus BUY/HOLD/SELL (premium) |
| Consensus and target range | Yes (low/avg/high, 90-day activity) | Partial (unverified) | Yes (low/avg/high, latest actions, analyst scorecards) |
| Thesis / risks | Partial | Yes (narrative) | Paywalled reports |
| Catalysts / earnings date | Partial | Prose | Yes |
| Fair value | Grade only | Multiples in text | Yes ("Fair Value $X", premium on some names) |
| Technical outlook | Momentum grade | No | Trading Central events (premium) |
| Track record | Alpha Picks open/closed, vs SPY, dividends reinvested | Yearly report cards | No per-pick record |
| Holding period | Buy and hold | ~1 year | — |
| Sizing | Trims > 15% to 10% (third-party) | No | No |
| Sell rules | Rating → Sell, 180 days at Hold, or M&A (third-party) | No | No |

Sources: help.seekingalpha.com (quant-ratings FAQ, Alpha Picks performance methodology), finance.yahoo.com (AAPL quote, MSTR analyst insights, Argus report preview), wallstreetsurvivor.com Alpha Picks review, investinglive.com on the Barron's 2025 picks. The barrons.com domain was blocked.
