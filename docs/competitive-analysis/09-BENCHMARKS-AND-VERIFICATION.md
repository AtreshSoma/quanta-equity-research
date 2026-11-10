# 09 — Benchmarks and verification

Machine: the owner's Mac (Darwin 27.2), Python 3.12.4, Node 25.9.0. Date 2026-11-06.

All QUANTA runs used an **isolated virtualenv and temporary data directories** outside the
repository, with QUANTA's pinned versions (`webapp/constraints.txt`). The repository working tree
was checked clean afterwards (`git status`). No credentials, broker connections or production data
were used. Scripts are listed in §6 so each number can be reproduced.

## 1. What was executed

| Check | Result | Type |
|---|---|---|
| QUANTA Python suite, `pytest -m "not golden"` | **2,976 passed**, 18 skipped, 4 xfailed, 27 deselected, 104 s | Empirical |
| QUANTA JS suite, `npm test` | **882 / 882 passed**, 11.8 s | Empirical |
| `ruff check .` (CI gate) | Pass | Empirical |
| `ruff check . --select F401` | 123 findings (106 auto-fixable) | Empirical |
| `pip-audit` on pinned set | 2 packages with advisories: requests 2.32.5 (PYSEC-2026-2275, fix 2.33.0); curl-cffi 0.13.0 (PYSEC-2026-2431, fix 0.15.0) | Empirical |
| `npm audit` | 0 vulnerabilities (45 packages) | Empirical |
| Barrier-probability correctness (`core/engine/expected_value.py`) | Matches Monte Carlo (§3) | Empirical |
| Offline API latency (Flask test client, network blocked) | §4 | Empirical |
| Fingerprint coverage per engine key | v4 shadow omits `TradingAnalysis_ELITE.py` (§5) | Empirical |
| Track-record composition | §5 | Empirical |

**Note on the Python run.** A first run reported 6 failures in `test_signal_ledger.py` and
`test_research_model_v3.py`. Cause: I had exported `QUANTA_TRACKING_DISABLED=1`, which turns
recording off, and those tests assert that calls are recorded. Re-running both files without that
variable: 63 passed. The figure above counts them as passing. This was an audit-environment error,
not a QUANTA defect.

## 2. Not Verified, and why

| Item | Blocker |
|---|---|
| QUANTA golden-master suite | Needs the pinned recording environment (`tests/golden/requirements-lock.txt`); fixtures are pickled DataFrames that do not load across pandas majors. Not installed, to keep within time |
| QUANTA Playwright e2e and axe accessibility | Needs a browser install and, for most specs, live Yahoo data |
| Cold dashboard latency, scan duration, cache hit rates | Need live network; the README's figures (cold US dashboard ~8.9-11.3 s; 36-54 Yahoo requests) are the owner's, not re-measured |
| Validation re-runs (`elite-oos`, `baselines`, short-term replay) | Need network and SEC contact; results quoted from `core/apex_quant/_reports/` and `docs/LIVE_MODEL_VALIDATION_STATUS.md` |
| TradingAgents tests, runtime, token use, cost | Not executed. The brief forbids running untrusted repository code without reviewing it, and the 90 test files were not reviewed line by line. LLM runs also need paid API keys. Call counts in 04 are read from the graph definition |
| OpenBB tests and performance | Same rule; large install (dozens of packages) |
| Qlib tests and benchmark reproduction | Same rule; also needs Cython build and a multi-GB China dataset download. Benchmark figures in 02/04 are Qlib's own README table |
| Like-for-like cross-repo benchmarks | The four systems do not share a workload: QUANTA's offline endpoints, TradingAgents' LLM graph, OpenBB's fetchers and Qlib's training loop answer different questions. No equivalent dataset/workload exists, so no head-to-head speed numbers are reported |
| UI responsiveness | Not measured; README figures (FCP 90-145 ms repeat visit) are the owner's |

## 3. Financial calculation check: barrier probabilities

`barrier_outcomes(a, b, mu, sigma, H)` gives exact P(target first), P(stop first) for drifted
Brownian motion. Compared with 200,000 simulated paths at 20 steps per session:

| a (up) | b (down) | μ/session | σ/session | H | p_up exact | p_up MC | p_down exact | p_down MC | max abs diff | exact time |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.10 | 0.08 | 0.0005 | 0.02 | 63 | 0.4853 | 0.4869 | 0.4879 | 0.4801 | 0.0078 | 0.16 ms |
| 0.05 | 0.05 | 0 | 0.015 | 21 | 0.4382 | 0.4272 | 0.4382 | 0.4266 | 0.0116 | 0.09 ms |
| 0.30 | 0.12 | 0.001 | 0.025 | 63 | 0.1940 | 0.1888 | 0.4415 | 0.4307 | 0.0108 | 0.08 ms |
| 0.02 | 0.10 | −0.0005 | 0.01 | 10 | 0.4746 | 0.4492 | 0.0025 | 0.0022 | 0.0254 | 0.06 ms |
| 0.15 | 0.15 | 0 | 0.03 | 252 | 0.5000 | 0.5005 | 0.5000 | 0.4995 | 0.0005 | 0.06 ms |

Discrete monitoring misses crossings between steps, so simulated hit rates sit slightly below the
continuous answer, most for a barrier close to the start (row 4: 2σ away). Re-running row 4 with the
Broadie-Glasserman-Kou correction (barriers shifted by 0.5826·σ·√Δt) at 20, 100 and 400 steps per
session gives 0.4733, 0.4728, 0.4732 against the exact 0.4746. **The closed form is correct.**
The open question is the inputs (drift from the valuation target, ATR-to-σ ratio), not the maths
(04 §4.2).

Also read, not simulated: VaR/CVaR (CVaR is the mean at or beyond the VaR quantile, so CVaR ≥ VaR),
Euler risk shares (sum to 1 by construction), worst 21-session window (compounded in log space),
non-overlapping IC sampling (step = horizon / median spacing).

## 4. Offline API latency

Flask test client, 30 requests each, `socket.connect` patched to fail so no request could reach
a provider, all data directories in a temporary folder.

| Endpoint | Status | p50 | p95 | Body |
|---|---|---|---|---|
| `/api/thresholds` | 200 | 0.81 ms | 0.85 ms | 18.6 KB |
| `/api/engines` | 200 | 0.18 ms | 0.20 ms | 452 B |
| `/api/track-record` (empty temp ledger) | 200 | 1.05 ms | 1.90 ms | 10.1 KB |
| `/api/stock-picks/recent` | 200 | 0.14 ms | 0.16 ms | 34 B |
| `/api/my-list` | 200 | 0.41 ms | 0.48 ms | 132 B |
| `/` (page HTML) | 200 | 1.05 ms | 1.18 ms | 163 KB |

Importing `webapp/app.py`: 0.81 s. These measure server overhead only; real latency is bounded by
provider requests (README: cold dashboard ~9-11 s by deliberate 350 ms pacing).

## 5. Reproducibility and track record

**Fingerprint coverage** (`core/tracking/fingerprint._sources`, run from `core/`):

| Engine key | Fingerprint | Files hashed | Non-engine files |
|---|---|---|---|
| `elite` | `574a1c4d6c96` | 44 | `TradingAnalysis_ELITE.py`, `evidence_registry.json` |
| `elite_v3_shadow` | `574a1c4d6c96` | 44 | same |
| `elite_v4_shadow` | `a6947af7e39a` | 43 | `evidence_registry.json` only |

No key hashes any `modules/` file.

**Published record** (`track_record/ledger.csv` at `db71ed2`):

| Measure | Value |
|---|---|
| Rows | 435 |
| Publication dates | 12 (2026-10-16 to 2026-11-04) |
| Distinct code fingerprints | 23 |
| Engines | elite 344, elite_pro 36, elite_v3_shadow 24, elite_v4_shadow 24, shortterm 7 |
| Stances | neutral 374, bullish 53, bearish 8 |
| Resolved | 148 (mostly 5- and 10-session horizons) |
| Resolved excess return vs benchmark | mean −0.31%, median 0.00% |
| Resolved bullish calls | 8, mean excess −4.94% |

These numbers are **not evidence about any model**: they pool fingerprints and horizons, and 8
bullish calls is far below the pre-registered minimum. They are reported to show the size of the
record and the fingerprint fragmentation (02 F-3).

## 6. Reproduce

```bash
SP=<scratch dir>
python3.12 -m venv $SP/venv
$SP/venv/bin/pip install -c webapp/constraints.txt numpy pandas lxml requests rich ta yfinance \
    vaderSentiment python-dateutil scipy tabulate flask waitress pytest pytest-timeout ruff \
    pyarrow freezegun curl_cffi pip-audit
QUANTA_NO_WARMUP=1 $SP/venv/bin/python -m pytest -m "not golden" -q -p no:cacheprovider
$SP/venv/bin/ruff check . && $SP/venv/bin/ruff check . --select F401 --statistics
$SP/venv/bin/pip-audit -r webapp/constraints.txt
(cd <copy of repo>/webapp && npm ci --ignore-scripts && npm test && npm audit)
```

The Monte Carlo check and the latency script are short (≈40 lines each); they import
`engine.expected_value` and `webapp/app.py` from the repository and write nothing into it. They
were kept outside the repository on purpose and can be added under `docs/competitive-analysis/tools/`
if you want them versioned.
