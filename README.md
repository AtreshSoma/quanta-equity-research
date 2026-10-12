# QUANTA Equity Research

A research workstation for US and Indian equities: a Flask web app (Overview, Discover, Research,
Picks, My Portfolio) built on multi-factor screening, risk-first position sizing and
walk-forward validation engines.

[![CI](https://github.com/AtreshSoma/quanta-equity-research/actions/workflows/ci.yml/badge.svg)](https://github.com/AtreshSoma/quanta-equity-research/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Lint](https://img.shields.io/badge/ruff-clean-brightgreen)](#code-style)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Educational software, released under the [MIT License](LICENSE).** This is not financial advice, and no strategy here has a demonstrated
> out-of-sample edge. See [Licence and disclaimer](#licence-and-disclaimer) before you act on
> anything it prints.

The [QUANTA Alpha audit and build blueprint](docs/QUANTA-ALPHA/README.md) is a
dated design reference. The [current validation status](docs/LIVE_MODEL_VALIDATION_STATUS.md)
describes the live model and the limits of its evidence.

---

## Contents

| Section | What you'll find |
|---|---|
| [Quick start](#quick-start) | Install, start the web app, or produce a CLI report |
| [The web app](#the-web-app) | What each page does |
| [What's in the box](#whats-in-the-box) | The four engines and when to reach for each |
| [The short-term scanner](#the-short-term-scanner) | Primary CLI, flags, and how to read its output |
| [Validation harness](#validation-harness) | Walk-forward testing, leakage and cost guards |
| [Project layout](#project-layout) | Directory map |
| [Configuration](#configuration) | Tunable knobs and their meaning |
| [Testing](#testing) | How to run the suite; why 11 tests need a pinned env |
| [Development](#development) | Code style, CI, contributing |
| [Troubleshooting](#troubleshooting) | Common failures and their causes |
| [Known limitations](#known-limitations) | What this does *not* do |

---

## Quick start

### Requirements

- **Python 3.11+** (CI runs 3.12)
- **Node.js 20+** only to run the web app's JavaScript tests
- No API keys needed for the default path — market data comes from Yahoo Finance via `yfinance`

### Install

```bash
git clone https://github.com/AtreshSoma/quanta-equity-research.git
cd quanta-equity-research

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev,webapp]"
```

### Start the web app

```powershell
.\start.ps1                      # Windows: installs what is missing, serves http://127.0.0.1:5500
```

```bash
./scripts/run_stock_analysis.sh  # macOS / Linux
```

Set `QUANTA_PORT` to serve on another port. Optional settings (SEC contact, provider keys) go in
a `.env` file at the repository root; it is git-ignored.

### Run your first CLI scan

```bash
python core/SP_ShortTerm.py --capital 25000 --scan standard --max-positions 5 -y
```

That scans 250 of the ~569-name universe and prints a 12-section research plan with
entry and risk references. Roughly 4–6 minutes; the request throttle is deliberate (see
[Troubleshooting](#troubleshooting)).

<details>
<summary><strong>Sample output</strong> (click to expand)</summary>

```
──────────────────────────────────────────────────────────────────────────────
 1.  AT A GLANCE
──────────────────────────────────────────────────────────────────────────────
   • 5 positions to open — MU, FTNT, CVX, CF, VEEV
   • Deploy $6,916.62 of $25,000.00 (27.7%); keep $18,083.38 in cash
   • Total risk if every stop is hit: $549.94 (2.20%) against a 6% budget
   • Lead idea: MU — buy $950.00, stop $833.57, first target $1,254.81 (2.6:1)
   • Horizon: 12 sessions (~2026-09-17); close everything by 2026-10-05

──────────────────────────────────────────────────────────────────────────────
 4.  TRADE LEVELS
──────────────────────────────────────────────────────────────────────────────
Ticker   Buy Limit  Entry Zone       Stop              Target 1 (take 50%)
-------  ---------  ---------------  ----------------  -----------------------
MU       $950.00    $929.62–$987.84  $833.57 (-12.3%)  $1,254.81 (+32.1% 2.6R)
FTNT     $169.88    $167.43–$174.43  $155.90 (-8.2%)   $198.37 (+16.8% 2.0R)
```

</details>

### Other entry points

```bash
python core/SP_ELITE_PRO.py                  # conversational multi-factor engine
python core/TradingAnalysis_ELITE.py         # institutional analysis platform
streamlit run core/streamlit_app.py          # browser research desk
python -m core.apex_quant.run status         # walk-forward sizing verdict
```

Windows users can double-click `launch_quanta.bat` (web app) or run `.\start.ps1` (dev server on
port 5500).

---

## The web app

`webapp/` is a Flask server (`app.py`) with a vanilla-JavaScript front end (`static/`) and a single
Jinja template. It imports the engines in-process.

| Page | What it does |
|---|---|
| **Overview** | Market regime, session quote board and an interactive price chart, for the US or India |
| **Discover** | Ranked ideas and the names moving today, each labelled by the model's Decision |
| **Research** | One-ticker tear sheet: decision and levels, live chart, colour-coded technical signals, valuation, peers and risks; searchable US and Indian listings |
| **Picks** | Runs a scan through the engine's gates and lists Buy / Accumulate / Watchlist calls sized to your capital, with the rules each name passed or failed |
| **My Portfolio** | Read-only Robinhood holdings with guidance, intraday, what-if and an Activity ledger |
| **Track record** | Whether each model is validated out of sample, and how published Decisions did |
| **Guide** | Live demonstrations of the rules the engine applies |

Decision thresholds have one source, `core/engine/thresholds.py`; the front end reads an exported
copy (`webapp/tests/fixtures/thresholds.json`), and a test fails if the two drift.

---

## What's in the box

Four engines share the `core/` tree. They are **independent** — pick the one that matches your
question.

| Engine | File | Use it when you want |
|---|---|---|
| **Short-term scanner** | `core/SP_ShortTerm.py` | Swing-trade setups with concrete entry/stop/target levels and dated horizons. **Start here.** |
| **QUANTA Elite Pro** | `core/SP_ELITE_PRO.py` | A conversational, profile-aware screen with narrative recommendations |
| **Trading Analysis Elite** | `core/TradingAnalysis_ELITE.py` | Deep single-name analysis: news sentiment, catalysts, compliance, portfolio risk |
| **Alpha Picks Engine** | `core/Alpha_Picks_Engine.py` | High-conviction screening weighted toward analyst ratings and fundamentals |

Supporting subsystems:

- **`core/apex_quant/`** — walk-forward validation harness with explicit leakage, cost-model and
  reproducibility guards
- **`core/sp_shortterm_backtest.py`** — point-in-time replay of the scanner's technical core
- **`modules/`** — 20 shared services: catalyst intelligence, SEC EDGAR parsing, portfolio risk,
  data-quality validation, provider adapters
- **`mcp_server/`** — Model Context Protocol server exposing the engine to LLM clients

---

## The short-term scanner

`core/SP_ShortTerm.py` is the most actively maintained engine and the one with the richest output.

### How it works

```
Universe (569 names)          S&P 500 + NASDAQ-100 + curated momentum list
        ↓
Liquidity & price gates       min $10M/day dollar volume, $5–$3000, 100+ bars
        ↓
Technical scoring             Wilder RSI / ATR / ADX, MACD, Bollinger, OBV, patterns
        ↓
Price-only pre-screen         skips the expensive fundamentals fetch when the
                              best case still can't clear the threshold
        ↓
Composite score (0–100)       technical 28% · momentum 22% · fundamental 20%
                              quality 12% · sentiment 10% · pattern 8%
        ↓
Regime adjustment             SPY trend + VIX → score multiplier
        ↓
Risk-first sizing             1.5% per trade (2.5% high conviction), 6% portfolio
        ↓
Portfolio construction        max 2 per sector, correlation gate at 0.78
        ↓
12-section report             levels, dated timing, factor grades, tickets
```

### CLI reference

```
python core/SP_ShortTerm.py [options]

  --capital FLOAT              Trading capital in USD (default 2500, floor 500)
  --scan {quick,standard,comprehensive,full}
                               75 / 250 / 400 / entire universe (default standard)
  --max-positions N            Most positions sized at once, 1–10 (default 5)
  --fractional                 Allow decimal share quantities
  --whole-shares               Force whole-share accounting (default)
  --export / --no-export       Write JSON + CSV + Markdown without prompting
  --output-dir DIR             Where exports land (default: current directory)
  -y, --yes                    Non-interactive; use defaults for anything not given
```

With **no arguments** it runs interactively and prompts for each setting, so existing habits still
work.

### Reading the report

| Section | Answers |
|---|---|
| 1. At a glance | The whole report in five lines |
| 2. Market context | What regime these picks were found in |
| 3. The picks | Overview table with signal, quant rating, score, RS |
| 4. Trade levels | **Entry, stop, and three targets** with % and R-multiples |
| 5. Timing | **When each target is due**, as real dates |
| 6. Factor grades | A+..F percentile rank against sector peers |
| 7. Execution tickets | Per-position orders, thesis, risks, step-by-step instructions |
| 8. Supporting data | The raw readings behind the scores |
| 9. Portfolio | Aggregate exposure, watchlist, PM review |
| 10. Intraday refinement | Optional 5-minute entry timing |
| 11. Action plan | What to do, in order |
| 12. How to read this report | Legend for every symbol used |

**Key concepts:**

- **R** — one unit of the trade's own risk (entry − stop). A 2R target pays twice what you risk.
  `+14%` means something different on a name stopped 4% away than one stopped 12% away.
- **R/R** — reward-to-risk to Target 1, **net** of a modelled 0.30% round-trip cost.
- **Break-even hit rate** — `1 / (1 + R/R)`. The Target-1 hit rate the geometry needs just to stop
  losing money. Arithmetic, not a forecast.
- **Factor grades** — percentile rank against *sector peers in this scan*, A+ (top 5%) to F
  (bottom 8%). A P/E of 30 is expensive for a utility and unremarkable for a chipmaker; absolute
  bands punish whole sectors for their structure, so grading is relative.
- **⚠ LOW** — conviction downgrade. Position size is already halved; treat as confirmation-only.
- **Sessions** — trading days with weekends and the ten NYSE holidays excluded, not calendar days.

### Exports

`--export` writes three files:

| Format | Purpose |
|---|---|
| `.md` | Human-readable briefing — the one to keep or paste into a journal |
| `.json` | Full dataclass snapshot including every field |
| `.csv` | Flat table for spreadsheets, with run metadata on each row |

---

## Validation harness

`core/apex_quant/` exists to answer *"does this actually work?"* honestly.

```bash
# Walk-forward validation over a historical window
python -m core.apex_quant.run validate \
    --start 2014-01-01 --end 2025-01-01 --train-years 3 --test-years 1

# Is the ELITE composite predictive out of sample?
python -m core.apex_quant.run elite-oos --end 2026-08-27 --eval-every 5

# Walk-forward the ELITE composite as a tradeable strategy
python -m core.apex_quant.run elite-walkforward

# Sizing gate verdict
python -m core.apex_quant.run status
```

If the strategy is not validated, the runner **says so and refuses to emit position sizes**. Plain
text only — no decorative output.

The harness carries its own guards in `core/apex_quant/tests/`:

- `test_leakage.py` — no future information reaches a training fold
- `test_costs.py` — the friction model is applied everywhere
- `test_reproducibility.py` — identical inputs produce identical outputs

**Result so far:** the ELITE composite has **no demonstrated out-of-sample edge** (recorded in
`docs/RECALIBRATION_PLAN.md`). That finding is kept deliberately visible rather than buried.

---

## Project layout

```
quanta-equity-research/
├── webapp/                     Flask web app
│   ├── app.py                    Server and /api routes
│   ├── portfolio_routes.py       My Portfolio and Activity routes
│   ├── static/                   Front end (app.js, live-chart.js, quanta.css, ...)
│   ├── templates/index.html      Single-page template
│   └── tests/                    node:test + jsdom units, Playwright e2e, fixtures, tools
├── core/                       Engines and analysis code
│   ├── engine/                   Decision gates, thresholds, events, valuation
│   ├── portfolio/                Broker holdings, history and insights
│   ├── SP_ShortTerm.py           Short-term scanner (primary entry point)
│   ├── SP_ELITE_PRO.py           Conversational multi-factor engine
│   ├── TradingAnalysis_ELITE.py  Institutional analysis platform
│   ├── Alpha_Picks_Engine.py     Analyst/fundamental screening
│   ├── sp_shortterm_backtest.py  Point-in-time replay harness
│   ├── streamlit_app.py          Browser research desk
│   └── apex_quant/               Walk-forward validation package
├── modules/                    20 shared services (catalysts, risk, providers, SEC)
├── mcp_server/                 MCP server + client for LLM integration
├── tests/                      Python suite (engine and web app)
│   └── golden/                   Recorded fixtures + pinned lockfile
├── docs/                       Design notes, audits, redesign logs
├── config/                     Reference data
├── data/                       Static calendars and compliance data
├── scripts/                    macOS / Linux launcher
├── .github/workflows/          CI and the Windows executable build
├── PRODUCT.md, DESIGN.md       Product context and the web app's design system
├── pyproject.toml              Dependencies, pytest and ruff configuration
├── start.ps1                   Windows web-app launcher (port 5500)
└── launch_quanta.bat           Windows one-click web-app launcher
```

> **Packaging note.** This tree is deliberately *not* an installable package yet. `core/` holds
> top-level modules that import each other by bare name, resolved via the `pythonpath` entries in
> `pyproject.toml`. `pip install -e .` installs the dependency set and nothing else. Restructuring
> is planned future work.

---

## Configuration

Most behaviour lives in the `Config` class at the top of `core/SP_ShortTerm.py`. The knobs you're
most likely to touch:

### Risk and sizing

| Setting | Default | Meaning |
|---|---|---|
| `MAX_RISK_PER_TRADE` | `0.015` | Dollar risk cap per position, as a fraction of capital |
| `MAX_RISK_AGGRESSIVE` | `0.025` | Cap for high-conviction (STRONG_BUY) setups |
| `MAX_PORTFOLIO_RISK` | `0.06` | Total heat if every stop fills |
| `MAX_POSITION_PCT` | `0.35` | Hard ceiling on any single position |
| `MIN_POSITION` | `150` | Dust-trade floor in dollars |
| `ALLOW_FRACTIONAL_SHARES` | `False` | Whole-share accounting by default |

### Screening

| Setting | Default | Meaning |
|---|---|---|
| `MIN_SCORE` | `60` | Composite score gate (0–100) |
| `MIN_RISK_REWARD` | `2.0` | Minimum reward:risk, **net** of costs |
| `MIN_DOLLAR_VOLUME` | `10_000_000` | Liquidity floor |
| `EARNINGS_BLOCK_DAYS` | `4` | No new positions this close to a report |
| `MAX_SECTOR_POSITIONS` | `2` | Sector concentration limit |
| `CORRELATION_THRESHOLD` | `0.78` | Reject a pick this correlated to a holding |

### Execution costs

| Setting | Default | Meaning |
|---|---|---|
| `COMMISSION_BPS` | `5.0` | Per side |
| `SLIPPAGE_BPS` | `10.0` | Per side |

Round-trip friction is debited from the reward leg *before* the R/R gate, so a setup built to a
gross 2.0R does not sneak through at 1.97R net.

### Ticker hygiene

Two optional JSON files, read from the working directory or alongside the module:

- **`ticker_aliases.json`** — `{"SQ": "XYZ"}` remaps renamed symbols
- **`stale_tickers.json`** — `["TWTR", "ATVI"]` excludes delisted names

Sensible defaults are built in, so both are optional.

---

## Testing

```bash
pytest -m "not golden" -q     # fast unit suite — this is CI's gate
pytest -q                     # everything, including golden master
pytest tests/test_sp_shortterm.py -q
pytest -m "not network"       # skip anything hitting a live API
```

Web app (from `webapp/`):

```bash
npm ci
npm test                      # node:test + jsdom unit suite — also a CI gate
npx playwright install chromium
QUANTA_PORT=5821 python app.py &   # a scratch server
BASE_URL=http://127.0.0.1:5821 npm run test:e2e   # desktop, tablet and mobile projects
```

### Test markers

| Marker | Meaning |
|---|---|
| `network` | Hits a live external API |
| `slow` | Takes more than a few seconds |
| `golden` | Golden-master regression against recorded fixtures |

### Why the golden suite needs a pinned environment

The fixtures in `tests/golden/fixtures/` are **gzipped pickles of pandas DataFrames**, and pickle
is not a stable cross-version format. A pandas or numpy major-version bump can make a fixture fail
to load — or worse, load with subtly different dtypes and shift a score.

So CI runs the golden job against `tests/golden/requirements-lock.txt`, the exact environment the
fixtures were recorded in. **If you run `pytest -q` in a modern venv you will see ~11 golden
failures. That is the version mismatch, not a regression.** Use `-m "not golden"` for day-to-day
work, or install the lockfile to replay fixtures faithfully.

A genuine golden failure means engine output changed. That is not automatically a bug — but it
must be acknowledged by re-freezing the snapshot in the same commit.

---

## Development

### Code style

```bash
ruff check .          # the CI gate
```

Line length 100, target `py311`. The lint gate is **errors only** (`E9`, `F63`, `F7`, `F82`) —
syntax errors, broken comparisons, misplaced control flow, undefined names. Things that are always
wrong.

Known debt, deliberately not gated yet: ~107 `F401` unused-import findings across `core/`,
`modules/` and `mcp_server/`. See them with `ruff check . --select F401`.

### CI

`.github/workflows/ci.yml` runs three jobs, separated by cost:

| Job | Runtime | Trigger | What it does |
|---|---|---|---|
| `python` | ~1m | every push | Ruff + `pytest -m "not golden"` against pyproject ranges |
| `javascript` | ~30s | every push | `npm test` for the web app |
| `golden` | ~3m | every push | Replays fixtures in the pinned recording environment |

No job touches the network: the unit suites are mocked, the golden suite replays with HTTP
blocked. The Python job installing from dependency *ranges* is deliberate — it doubles as an early
warning when an upstream release breaks the build.

`.github/workflows/build_exe.yml` builds the Windows scanner executable when the engine changes.

### Optional extras

```bash
pip install -e ".[dev]"        # pytest + ruff
pip install -e ".[webapp]"     # Flask web app
pip install -e ".[ui]"         # Streamlit research desk
pip install -e ".[mcp]"        # MCP server
pip install -e ".[providers]"  # Polygon, pandas-datareader, TextBlob
```

### Contributing

1. Branch from `main`
2. Keep `ruff check .` clean, `pytest -m "not golden"` and `npm test` green
3. Add a regression test for every behaviour change — the suite is the safety net that makes this
   tree editable
4. If engine output changes, re-freeze the golden snapshot **in the same commit**

---

## Troubleshooting

<details>
<summary><strong>The scan is slow</strong></summary>

This is intentional. `yfinance` issues one un-throttled HTTP request per call, and a thread pool
fan-out makes Yahoo answer `429` — which silently returns empty frames and collapses the scan. A
process-wide minimum interval (`YF_MIN_REQUEST_INTERVAL`, 0.35s) spaces requests out and widens
adaptively when a 429 appears. Budget ~4–6 minutes for a standard scan.

</details>

<details>
<summary><strong>"UNIVERSE DEGRADED" in the output</strong></summary>

An index constituent list could not be fetched and a small built-in fallback was used instead, so
coverage is materially reduced. The scan still completes. Check `apex_trading.log` — usually a
Wikipedia layout change or a network issue.

</details>

<details>
<summary><strong>Zero setups found</strong></summary>

Not an error. The rejection funnel in the scan summary shows exactly which gate stopped each name.
A defensive regime applies a score haircut by design, so a weak tape legitimately produces nothing.
If it's `below_min_position_size`, raise `--capital` or enable `--fractional`.

</details>

<details>
<summary><strong>Provider failures / "Invalid Crumb"</strong></summary>

Yahoo intermittently returns 401/429 under concurrent load. The scanner retries with backoff,
falls back to Stooq, then reports the failure honestly rather than counting it as "no data". A
handful per scan is normal; dozens means you are being rate-limited — wait and re-run.

</details>

<details>
<summary><strong>~11 test failures on a fresh install</strong></summary>

Expected. See [Why the golden suite needs a pinned environment](#why-the-golden-suite-needs-a-pinned-environment).
Run `pytest -m "not golden"` instead.

</details>

---

## Known limitations

Stated plainly, because the alternative is you discovering them with real money.

- **No demonstrated edge.** Walk-forward testing of the ELITE composite found no out-of-sample
  predictive power. Better-looking output is not better performance.
- **Long-only.** No short setups, options, or non-US equities.
- **Free data.** Yahoo Finance fundamentals are frequently incomplete or stale. Missing fields are
  imputed at neutral rather than scored as zero, but imputation is not measurement.
- **No point-in-time fundamentals.** The backtest harness holds fundamental scores neutral during
  replay because historical `info` snapshots don't exist. Backtested results therefore exercise the
  technical core only.
- **Intraday data is delayed.** The 5-minute playbook uses ~15-minute-delayed bars. It is advisory;
  use a real-time feed for execution.
- **Timing estimates are pace, not prediction.** ATR says how far a name typically travels in a
  session, not which direction. "T1 due 17 Sep" means *if* it works, roughly then.
- **Levels move.** Entries, stops and targets are ATR-scaled and change daily. Re-run before acting
  on a stale report.

---

## Licence and disclaimer

### Licence

Copyright (c) 2026 Atresh Somayajula. Released under the [MIT License](LICENSE): you may use, copy,
modify, merge, publish, distribute, sublicense and sell the code, provided the copyright notice and
licence text are kept. The software is provided "as is", without warranty of any kind.

The licence covers this repository's source code and documentation only. It does not cover market
data you download with it. Yahoo Finance, SEC EDGAR, Robinhood and any other provider keep their
own terms; you are responsible for complying with them. This project is not affiliated with or
endorsed by any of them. Third-party dependencies keep their own licences (see `pyproject.toml`).

### Disclaimer

**Educational software. This is not financial advice.**

This software produces model output from public market data. It does not know your circumstances,
tax position, or risk tolerance, and it has no demonstrated ability to predict prices. Nothing it
prints is a recommendation to buy or sell any security.

Verify every price against your broker before placing an order. Past performance — and backtested
performance especially — does not indicate future results. You are solely responsible for your own
trading decisions and any losses that follow.
