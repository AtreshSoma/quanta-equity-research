"""[HOLD-INFLATION-FIX] Audit driver: poll /api/analyze for 15 tickers and
emit a per-ticker JSON row + a reason-distribution table.

Captures the fields the user asked for in the HOLD inflation diagnosis:
    ticker, raw_signal, composite_score, risk_grade, data_quality_status,
    buy_now, timing, risk_reward, analyst_data_available,
    price_data_available, history_bars_count, deep_analyzer_status,
    final_action, final_action_reason (first), override_source.

Run with the dashboard up on :5500:
    python tests/manual/uat_hold_audit.py
or with a custom ticker list:
    python tests/manual/uat_hold_audit.py AAPL MSFT NVDA
"""
from __future__ import annotations

import collections
import json
import sys
import time
import urllib.request
from typing import Any, Dict, List

BASE = "http://127.0.0.1:5500"

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
    "C", "JPM", "BAC", "DELL", "MU",
    "XLK", "QQQ", "VTI", "TSM",
]


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


def _classify_first_reason(reasons: List[str]) -> str:
    """Bucket the first override reason into a stable category name."""
    if not reasons:
        return "none"
    r0 = " ".join(str(x).lower() for x in reasons)
    if "invalid" in r0:
        return "invalid_ticker"
    if "risk grade f" in r0:
        return "risk_grade_F"
    if "risk grade d" in r0:
        return "risk_grade_D"
    if "r/r" in r0 and "below" in r0:
        return "risk_reward_below_min"
    if "dcf overval" in r0:
        return "dcf_overvaluation"
    if "insider" in r0 and "sell" in r0:
        return "heavy_insider_selling"
    if "bearish" in r0 and "reversal" in r0:
        return "bearish_reversal"
    if "buy now" in r0 or "timing" in r0:
        return "buy_now_no"
    if "composite score" in r0 and "→" in r0:
        return "composite_band_only"
    return "other"


def audit_one(ticker: str, hard_timeout: float = 120.0) -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        started = _post("/api/analyze", {"ticker": ticker, "fast_mode": False})
    except Exception as exc:  # pragma: no cover
        return {"ticker": ticker, "error": f"start failed: {exc}"}
    job_id = started["job_id"]
    final: Dict[str, Any] = {}
    while time.monotonic() - t0 < hard_timeout:
        time.sleep(0.5)
        try:
            job = _get(f"/api/jobs/{job_id}")
        except Exception:
            continue
        if job.get("status") in ("complete", "error"):
            final = job
            break
    elapsed = time.monotonic() - t0
    result = (final.get("result") or {}) if final else {}
    mi = result.get("market_intel") or {}
    trade = result.get("trade_setup") or {}
    scores = result.get("scores") or {}
    risk = result.get("risk") or {}
    bg = result.get("beginner_guidance") or {}
    reasons = result.get("final_action_reasons") or []
    analyst_count = (
        int(mi.get("buy_ratings") or 0)
        + int(mi.get("hold_ratings") or 0)
        + int(mi.get("sell_ratings") or 0)
    )
    return {
        "ticker": ticker,
        "elapsed_s": round(elapsed, 2),
        "status": final.get("status", "timeout"),
        "raw_signal": result.get("signal"),
        "final_action": result.get("final_action"),
        "composite_score": scores.get("composite_score"),
        "risk_grade": risk.get("risk_grade"),
        "data_status": result.get("data_status"),
        "buy_now": bg.get("buy_now"),
        "timing": bg.get("entry_timing") or bg.get("long_tf"),
        "risk_reward": trade.get("risk_reward_ratio"),
        "analyst_count": analyst_count,
        "analyst_data_available": analyst_count > 0,
        "price_data_available": bool(result.get("current_price")),
        "history_bars_count": result.get("history_length"),
        "deep_analyzer_status": result.get("data_status") or "ok",
        "is_invalid": result.get("is_invalid", False),
        "final_action_reason": reasons[0] if reasons else "",
        "reason_count": len(reasons),
        "override_source": _classify_first_reason(reasons),
    }


def main() -> int:
    tickers = sys.argv[1:] or DEFAULT_TICKERS
    print(f"Auditing {len(tickers)} tickers against {BASE} (full mode)…")
    print()
    rows: List[Dict[str, Any]] = []
    for t in tickers:
        r = audit_one(t)
        rows.append(r)
        print(
            f"  {r['ticker']:<8} {str(r.get('final_action') or '-'):<20} "
            f"comp={str(r.get('composite_score') or '-'):>6} "
            f"risk={str(r.get('risk_grade') or '-'):<2} "
            f"R/R={str(r.get('risk_reward') or '-'):<6} "
            f"src={str(r.get('override_source') or '-'):<22} "
            f"t={r['elapsed_s']}s"
        )

    print("\n" + "=" * 78)
    print("REASON DISTRIBUTION")
    print("=" * 78)
    src_counts = collections.Counter(r.get("override_source", "?") for r in rows)
    action_counts = collections.Counter(str(r.get("final_action") or "-") for r in rows)
    for src, cnt in src_counts.most_common():
        examples = [r["ticker"] for r in rows if r.get("override_source") == src][:3]
        print(f"  {src:<30} {cnt:>2} examples: {', '.join(examples)}")

    print("\n" + "=" * 78)
    print("FINAL ACTION DISTRIBUTION")
    print("=" * 78)
    for act, cnt in action_counts.most_common():
        examples = [r["ticker"] for r in rows if str(r.get("final_action") or "-") == act][:3]
        print(f"  {act:<30} {cnt:>2} examples: {', '.join(examples)}")

    out_path = "/tmp/uat_hold_audit.json"
    with open(out_path, "w") as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"\nFull rows: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
