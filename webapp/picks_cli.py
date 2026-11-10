"""Run one Picks scan from the command line, exactly as the web page runs it.

The daily job (scripts/quanta_daily.sh) ran only the short-term scanner, so
Picks calls reached the track record only when someone pressed Run in the
browser, and no Picks record built up. This runs the same endpoint in-process
(``POST /api/stock-picks`` through Flask's test client, the same validation,
worker, saved scan and ledger recording) and waits for it.

    python webapp/picks_cli.py                       # US Standard, $2,500, 5 positions
    python webapp/picks_cli.py --capital 5000 --scan quick --max-positions 8

Settings fall back to QUANTA_DAILY_CAPITAL / QUANTA_DAILY_PICKS_SCAN /
QUANTA_DAILY_POSITIONS. Exit 0 when the scan completed, 1 when it failed, 2 on
a request the server rejected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "core", ROOT / "modules", ROOT / "webapp"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

HEADERS = {"X-QUANTA-Request": "1", "Content-Type": "application/json"}
POLL_SECONDS = 5.0
TIMEOUT_SECONDS = 3 * 3600.0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    env = os.environ
    p = argparse.ArgumentParser(description="Run one Picks scan as the web page does.")
    p.add_argument("--market", default="US", choices=("US", "IN"))
    p.add_argument("--scan", default=env.get("QUANTA_DAILY_PICKS_SCAN", "standard"),
                   help="scan type, as on the page (standard, quick, comprehensive, focused_growth, ...)")
    p.add_argument("--capital", type=float, default=float(env.get("QUANTA_DAILY_CAPITAL", "2500")))
    p.add_argument("--max-positions", type=int, default=int(env.get("QUANTA_DAILY_POSITIONS", "5")))
    p.add_argument("--risk-profile", default="balanced")
    p.add_argument("--no-diversify", action="store_true")
    p.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="seconds to wait for the scan")
    return p.parse_args(argv)


def request_body(args: argparse.Namespace) -> Dict[str, Any]:
    return {"market": args.market, "scan_type": args.scan, "capital": args.capital,
            "max_positions": args.max_positions, "risk_profile": args.risk_profile,
            "diversify": not args.no_diversify}


def summary(result: Dict[str, Any]) -> List[str]:
    """Plain lines for the daily log: what was funded, what is on watch, what came close."""
    sym = result.get("currency") or "$"
    acc = result.get("accounting") or {}
    sel = result.get("universe_selection") or {}
    lines = [
        f"Scan {result.get('scan_type')} at {result.get('timestamp')}: "
        f"{acc.get('universe', '?')} names, universe {sel.get('method', '?')}, "
        f"{(acc.get('could_not_analyse') or {}).get('count', 0)} not analysed, "
        f"daily bars to {(result.get('data_dates') or {}).get('bar_session')}",
    ]
    picks = result.get("stock_picks") or []
    funded = [p for p in picks if float(p.get("allocation") or 0) > 0]
    watch = [p for p in picks if float(p.get("allocation") or 0) <= 0]
    for p in funded:
        lines.append(f"  {str(p.get('call', '')).upper():<10} {p['ticker']:<7} {sym}{float(p.get('allocation') or 0):,.0f}"
                     f"  entry {p.get('entry')}  stop {p.get('stop')}  target {p.get('target')}  R:R {p.get('risk_reward')}")
    for p in watch:
        lines.append(f"  WATCHLIST  {p['ticker']:<7} {str(p.get('reason') or '')[:110]}")
    if not picks:
        lines.append("  No Buy, Accumulate or Watchlist call.")
    near = result.get("near_misses") or []
    if near:
        lines.append("  Ranked high, stopped by one rule: " + ", ".join(
            f"{n['ticker']} {round(float(n.get('score') or 0) * 10)}" for n in near[:5]))
    return lines


def run(args: argparse.Namespace, client: Any = None, sleep=time.sleep, clock=time.monotonic) -> int:
    if client is None:
        import app as webapp
        client = webapp.app.test_client()
    resp = client.post("/api/stock-picks", data=json.dumps(request_body(args)), headers=HEADERS,
                       base_url="http://127.0.0.1")
    body = resp.get_json(silent=True) or {}
    if resp.status_code >= 400 or not body.get("job_id"):
        print(f"Scan request rejected ({resp.status_code}): {body.get('error') or body}", file=sys.stderr)
        return 2
    job_id = body["job_id"]
    print(f"Picks scan started: job {job_id}", flush=True)
    deadline = clock() + float(args.timeout)
    last = ""
    while clock() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers=HEADERS, base_url="http://127.0.0.1").get_json(silent=True) or {}
        status = str(job.get("status") or "")
        message = str(job.get("message") or "")
        if message and message != last:
            print(f"  {message}", flush=True)
            last = message
        if status == "complete":
            for line in summary(job.get("result") or {}):
                print(line, flush=True)
            return 0
        if status in ("error", "failed", "cancelled"):
            print(f"Picks scan {status}: {job.get('error') or message}", file=sys.stderr)
            return 1
        sleep(POLL_SECONDS)
    print(f"Picks scan did not finish within {args.timeout:.0f} s", file=sys.stderr)
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
