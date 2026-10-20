"""[PERF-FASTMODE] Quick UAT driver for the tear-sheet Fast Mode path.

Drives /api/analyze for a list of (ticker, fast_mode) pairs and prints a
timing table including ``first_response_seconds`` (when the job moves off
0% progress) and ``complete_seconds`` (terminal status). Also reports the
slowest stage_timings entry the analyzer recorded.

Run from any directory while the dashboard is up on :5500.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from typing import Any, Dict, List, Tuple

BASE = "http://127.0.0.1:5500"


def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _get(path: str) -> Dict[str, Any]:
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def run_one(ticker: str, fast_mode: bool, hard_timeout: float = 90.0) -> Dict[str, Any]:
    t0 = time.monotonic()
    started = _post("/api/analyze", {"ticker": ticker, "fast_mode": fast_mode})
    job_id = started["job_id"]
    first_response = None
    last_progress = 0
    final = None
    while time.monotonic() - t0 < hard_timeout:
        time.sleep(0.4)
        try:
            job = _get(f"/api/jobs/{job_id}")
        except Exception as e:  # pragma: no cover — network blip
            print(f"  poll error: {e}")
            continue
        if first_response is None and job.get("progress", 0) > 0:
            first_response = time.monotonic() - t0
        last_progress = job.get("progress", last_progress)
        if job.get("status") in ("complete", "error"):
            final = job
            break
    complete = time.monotonic() - t0
    stages = []
    if final and final.get("result"):
        stages = final["result"].get("stage_timings", []) or []
    slowest = max(stages, key=lambda s: s.get("duration_ms", 0), default={})
    return {
        "ticker": ticker,
        "fast_mode": fast_mode,
        "status": (final or {}).get("status", "timeout"),
        "first_response_s": round(first_response or complete, 2),
        "complete_s": round(complete, 2),
        "last_progress": last_progress,
        "slowest_stage": slowest.get("stage", "-"),
        "slowest_ms": slowest.get("duration_ms", 0),
        "error_type": ((final or {}).get("error_detail") or {}).get("error_type", "-"),
    }


def main() -> int:
    cases: List[Tuple[str, bool]] = [
        ("FAKE123", False),   # invalid — must be fast (<5s)
        ("AAPL", True),       # fast mode
        ("AAPL", False),      # full mode (warm cache now)
        ("AAPL", True),       # cached repeat — must be <2s
    ]
    # Optional second-round set passed on CLI: `python uat_fast_mode.py HAL.NS INFY.NS`
    for sym in sys.argv[1:]:
        cases.append((sym, True))
        cases.append((sym, False))

    print(f"{'Ticker':<10} {'Mode':<6} {'Status':<10} {'1st':>6} {'Done':>7} "
          f"{'Slowest':<22} {'Err':<14}")
    print("-" * 92)
    for ticker, fast in cases:
        r = run_one(ticker, fast)
        mode = "FAST" if fast else "FULL"
        slowest = f"{r['slowest_stage']}({r['slowest_ms']}ms)"
        print(f"{r['ticker']:<10} {mode:<6} {r['status']:<10} "
              f"{r['first_response_s']:>5}s {r['complete_s']:>6}s "
              f"{slowest:<22} {r['error_type']:<14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
