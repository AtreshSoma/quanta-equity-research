#!/usr/bin/env python
"""Capture golden-master fixtures. This is the only step that touches the network.

Usage
-----
    python tests/golden/record.py                    # the PRIORITY set
    python tests/golden/record.py AAPL NVDA          # named tickers
    python tests/golden/record.py etf utility        # named cohorts
    python tests/golden/record.py all                # the whole 40-name cohort
    python tests/golden/record.py --expected-only    # re-freeze snapshots from
                                                     # existing fixtures, no network

Two artifacts are written per ticker:

    fixtures/<TICKER>.pkl.gz   the captured I/O, replayed by the test
    expected/<TICKER>.json     the normalised snapshot the test compares against

Re-running overwrites both. `--expected-only` re-derives just the snapshot from
the stored fixture, which is what you want after an *intentional* behaviour
change: inspect the diff the test prints, then accept it with this flag.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
# Fixture capture/replay must never create live forecast records.
os.environ["QUANTA_TRACKING_DISABLED"] = "1"

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from _paths import ensure_paths  # noqa: E402

ensure_paths()

import cohorts  # noqa: E402
from _normalize import normalize  # noqa: E402
from _recorder import (  # noqa: E402
    EXPECTED_DIR,
    fixture_path,
    load_recording,
    record_io,
    replay_io,
    save_recording,
)

ACCOUNT_SIZE = 100_000.0  # fixed so position sizing is deterministic


def _snapshot(ticker: str, recording) -> dict:
    """Run the engine under replay and return the normalised result."""
    import TradingAnalysis_ELITE as elite
    from freezegun import freeze_time

    with freeze_time(recording.recorded_at):
        with replay_io(recording, strict=False):
            analyzer = elite.QuantaEliteAnalyzer({})
            result = analyzer.analyze(ticker, account_size=ACCOUNT_SIZE)
            payload = elite._mcp_result_to_dict(result)

    if recording.misses:
        raise ValueError(f"Incomplete replay: {len(recording.misses)} unrecorded inputs")

    return {
        "ticker": ticker,
        "recorded_at": recording.recorded_at,
        "account_size": ACCOUNT_SIZE,
        "result": normalize(payload),
    }


def _write_expected(ticker: str, snapshot: dict) -> Path:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPECTED_DIR / f"{ticker.replace('.', '_').replace('^', '_')}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def record_one(ticker: str, *, expected_only: bool = False) -> tuple[bool, str]:
    """Record (or re-freeze) one ticker. Returns (ok, message)."""
    try:
        if expected_only:
            recording = load_recording(ticker)
        else:
            import TradingAnalysis_ELITE as elite

            recording, ctx = record_io(ticker)
            with ctx:
                analyzer = elite.QuantaEliteAnalyzer({})
                analyzer.analyze(ticker, account_size=ACCOUNT_SIZE)

        snapshot = _snapshot(ticker, recording)
        if not expected_only:
            save_recording(recording)
        _write_expected(ticker, snapshot)

        res = snapshot["result"]
        signal = res.get("signal") or res.get("recommendation") or "?"
        size_kb = fixture_path(ticker).stat().st_size / 1024
        return True, f"{len(recording.calls):3d} calls, {size_kb:6.1f} KB, signal={signal}"
    except Exception as exc:  # noqa: BLE001 - reported per ticker, loop continues
        return False, f"{type(exc).__name__}: {exc}"


def _record_in_subprocess(symbol: str, expected_only: bool) -> tuple[bool, str]:
    """Record one ticker in a fresh interpreter.

    Process isolation is required, not merely tidy. Several caches in the tree
    are class-level or module-level (`sec_edgar_parser._cik_cache` among them),
    so recording a batch in one process warms them: later tickers skip fetches
    that a cold replay still performs, and the fixture comes out incomplete.
    That showed up as 7 of 12 fixtures reporting unrecorded reads, all of them
    cross-ticker peer and correlation lookups. One interpreter per ticker makes
    every recording a genuine cold start, matching how the test replays it.
    """
    cmd = [sys.executable, str(Path(__file__).resolve()), "--single", symbol]
    if expected_only:
        cmd.append("--expected-only")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    if proc.returncode == 0:
        return True, last[-1] if last else "ok"
    err = (proc.stderr or "").strip().splitlines()
    return False, (last[-1] if last else (err[-1] if err else f"exit {proc.returncode}"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="*", help="tickers, cohort names, 'priority' or 'all'")
    ap.add_argument(
        "--expected-only",
        action="store_true",
        help="re-freeze snapshots from existing fixtures (no network)",
    )
    ap.add_argument(
        "--delay", type=float, default=1.5, help="seconds between tickers (Yahoo throttle)"
    )
    ap.add_argument(
        "--single",
        metavar="TICKER",
        help="internal: record exactly one ticker in this process",
    )
    ap.add_argument(
        "--no-isolate",
        action="store_true",
        help="record all tickers in this process (produces warm-cache fixtures; not recommended)",
    )
    args = ap.parse_args()

    # Worker mode: one ticker, this interpreter, minimal output.
    if args.single:
        success, message = record_one(args.single, expected_only=args.expected_only)
        print(message)
        return 0 if success else 1

    symbols = cohorts.resolve(args.targets)
    mode = "re-freezing snapshots" if args.expected_only else "recording"
    isolation = "in-process" if args.no_isolate else "isolated per ticker"
    print(f"{mode} {len(symbols)} ticker(s), {isolation}\n")

    ok, failed = 0, []
    for i, symbol in enumerate(symbols, 1):
        print(f"[{i:2d}/{len(symbols)}] {symbol:14s} ", end="", flush=True)
        if args.no_isolate:
            success, message = record_one(symbol, expected_only=args.expected_only)
        else:
            success, message = _record_in_subprocess(symbol, args.expected_only)
        print(("OK   " if success else "FAIL ") + message)
        if success:
            ok += 1
        else:
            failed.append(symbol)
        if not args.expected_only and i < len(symbols):
            time.sleep(args.delay)

    print(f"\n{ok}/{len(symbols)} succeeded")
    if failed:
        print("failed: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
