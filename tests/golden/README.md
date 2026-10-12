# Golden master

Regression harness for `QuantaEliteAnalyzer.analyze()`. It replays recorded I/O
with the clock frozen and all network blocked, then diffs the normalised result
against a committed snapshot.

This is the safety net every phase of `docs/RECALIBRATION_PLAN.md` leans on.
Without it, changing a 13,091-line engine is guesswork: a weight adjustment in
one place moves a win probability three modules away, and nothing reports it.

## Running

```bash
pytest -m golden -q            # replay every fixture      (~2.5 min)
pytest -m "not golden" -q      # everything else           (~13 s)
pytest tests/test_golden_master.py -v
```

## When a golden test fails

A failure means output changed — not necessarily that it broke. Most later
phases change behaviour on purpose.

1. **Read the diff.** The test prints every changed field, plus which branch
   that fixture guards.
2. **Confirm every line was intended.** An unexpected field in the list is the
   finding. This is the whole point: a 0.01 change to one regime weight shows up
   as seven changed fields, including `trade_setup.win_probability`.
3. **Accept it:**
   ```bash
   python tests/golden/record.py --expected-only AAPL
   ```
   No network. Re-derives the snapshot from the stored fixture.
4. **Commit the snapshot with the code change**, so review sees the behavioural
   delta next to its cause.

## Re-recording from the network

Different and rarer. This changes the *inputs*, which invalidates comparison
against previous snapshots entirely.

```bash
python tests/golden/record.py                 # the PRIORITY set (12 tickers)
python tests/golden/record.py AAPL NVDA       # named tickers
python tests/golden/record.py etf utility     # named cohorts
python tests/golden/record.py all             # the full 40-name cohort
```

Each ticker is recorded in a **fresh subprocess**. This is required, not tidy:
several caches in the tree are class- or module-level, so recording a batch in
one process warms them and later tickers skip fetches a cold replay still
performs. That produced incomplete fixtures for 7 of 12 tickers — all
cross-ticker peer and correlation lookups — until isolation was added. Pass
`--no-isolate` to reproduce the broken behaviour.

## What gets recorded

Three external dependencies, and that is the complete list:

| Seam | Surface |
|---|---|
| `yf.Ticker` | 11 properties (`info`, `news`, `calendar`, `dividends`, `options`, `financials`, `quarterly_financials`, `balance_sheet`, `earnings_history`, `insider_transactions`, `fast_info`) and 2 methods (`history`, `option_chain`) |
| `APIClient.fetch_json` | The single choke point for NewsAPI, Finnhub, Polygon, FMP, Alpha Vantage and SEC EDGAR |
| Wall clock | Frozen to the recording date via `freezegun`, because the engine reads `date.today()` in earnings-proximity and staleness logic |

During replay, `requests.Session` is replaced with a stand-in that raises on
`get`/`post`. Any I/O that bypassed `fetch_json` fails loudly rather than
silently reaching the network.

`test_replay_needs_no_unrecorded_io` asserts zero fixture gaps. Without it, a
change that reads a *new* property would be served a neutral default, and the
snapshot could still pass while the new code path went untested.

## Files

```
cohorts.py                  40 tickers, each with the branch it holds open
record.py                   the only thing that touches the network
_recorder.py                record/replay, persistence
_normalize.py               volatile-field stripping, float rounding, diffing
requirements-lock.txt       exact environment the fixtures were recorded in
fixtures/<TICKER>.pkl.gz    captured I/O          (~68 KB each)
expected/<TICKER>.json      normalised snapshot   (~11 KB each)
```

## Pinned dependencies

Fixtures are gzipped pickles containing pandas DataFrames, and pickle is not a
stable cross-version format. The CI golden job installs
`requirements-lock.txt` exactly. **If you upgrade pandas or numpy, re-record and
read the diff** — a dependency upgrade that moves a score is precisely the drift
this harness exists to catch.

## Normalisation

Timestamps, elapsed times and cache statistics are replaced with
`<volatile>` — the key is kept, so a refactor that *removes* the field still
shows up. Floats are rounded to 6 significant figures, which absorbs BLAS-level
noise while leaving any real behavioural change far outside the tolerance.
Everything else is compared exactly, so a new field appears in the diff and has
to be acknowledged.
