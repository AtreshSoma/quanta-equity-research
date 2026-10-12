"""Ad-hoc UAT harness: drive /api/analyze for several ticker archetypes and
print the asset-type + applicability mapping the tear sheet relies on.

Not part of the pytest suite — run manually:
    .venv/bin/python tests/uat_asset_types.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5500"
TICKERS = sys.argv[1:] or ["AAPL", "C", "TSM", "XLK", "FAKE123"]


def _post_analyze(ticker: str) -> str:
    body = json.dumps({"ticker": ticker}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/analyze", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["job_id"]


def _poll(job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(f"{BASE}/api/jobs/{job_id}", timeout=30) as resp:
            payload = json.loads(resp.read())
        if payload.get("status") in ("done", "error", "complete"):
            return payload
        time.sleep(1.0)
    return {"status": "timeout"}


def main() -> int:
    rows = []
    for t in TICKERS:
        try:
            job = _post_analyze(t)
            payload = _poll(job)
        except Exception as exc:  # noqa: BLE001 — UAT harness only
            rows.append((t, f"ERROR: {exc}", "", "", "", ""))
            continue
        result = payload.get("result") or payload.get("data") or {}
        rows.append((
            t,
            result.get("asset_type", "?"),
            result.get("is_adr"),
            result.get("is_financial"),
            result.get("is_high_volatility"),
            result.get("final_action") or payload.get("error_type") or payload.get("status"),
        ))
        time.sleep(2.0)

    print(f"{'Ticker':<10}{'asset_type':<16}{'adr':<7}{'fin':<7}{'hivol':<8}{'action/status'}")
    print("-" * 64)
    for r in rows:
        print(f"{r[0]:<10}{str(r[1]):<16}{str(r[2]):<7}{str(r[3]):<7}{str(r[4]):<8}{r[5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
