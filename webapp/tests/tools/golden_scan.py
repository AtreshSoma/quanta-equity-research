"""Run Picks scans in-process and save their results for before/after comparison.

This calls the same worker as ``POST /api/stock-picks`` (``app._run_stock_picks``)
without starting a server. It refuses to run unless tracking is disabled and the
ledger and cache point away from the production files, so a golden run can never
write a production signal row or cache entry.

Usage (from any directory; the engine logs to ``./logs`` so run it from scratch):

    python golden_scan.py --root <repo-or-baseline-root> --out <dir> [--variant NAME ...]

``--root`` is the repository root (the engine, with ``webapp/`` inside it). That lets the same script run against the branch
and against an exported baseline tree.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

VARIANTS = {
    "base": dict(capital=2500, risk_profile="balanced", max_positions=5, diversify=True),
    "cap372": dict(capital=372, risk_profile="balanced", max_positions=5, diversify=True),
    "cap25k": dict(capital=25000, risk_profile="balanced", max_positions=5, diversify=True),
    "max1": dict(capital=2500, risk_profile="balanced", max_positions=1, diversify=True),
}


def _guard_env() -> None:
    if os.environ.get("QUANTA_TRACKING_DISABLED") != "1":
        sys.exit("refusing to run: set QUANTA_TRACKING_DISABLED=1")
    for var in ("QUANTA_LEDGER_PATH", "QUANTA_CACHE_DIR"):
        val = os.environ.get(var, "")
        if not val or "Temp" not in val and "scratch" not in val.lower():
            sys.exit(f"refusing to run: {var} must point at a scratch location (got {val!r})")


def _summary(result: dict) -> dict:
    recs = result.get("recommendations") or []
    rows = []
    for r in recs:
        dc = r.get("deep_confirmation") or {}
        rows.append({
            "ticker": r.get("ticker"),
            "final_action": r.get("final_action") or (r.get("decision") or {}).get("action"),
            "is_funded": r.get("is_funded"),
            "allocation": r.get("allocation"),
            "shares": r.get("shares"),
            "entry": r.get("entry_price"),
            "stop": r.get("stop_loss"),
            "target": r.get("price_target"),
            "rr": r.get("risk_reward_ratio"),
            "binding_levels": r.get("binding_levels") or dc.get("binding_levels"),
            "risk_grade": r.get("risk_grade"),
            "deep_risk_grade": dc.get("deep_risk_grade"),
            "deep_reason": dc.get("reason"),
            "reason_codes": dc.get("reason_codes"),
        })
    funnel = result.get("funnel") or {}
    return {
        "scan_status": result.get("scan_status"),
        "rows": rows,
        "funnel": {k: funnel.get(k) for k in (
            "universe_size", "universe_selection", "analyzed_success", "analysis_failed",
            "final", "funded", "deep_confirm_withdrawn", "gate_stats", "filter_stats")},
        "near_misses": funnel.get("near_misses"),
        "empty_scan_explanation": result.get("empty_scan_explanation"),
        "evidence": result.get("evidence"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", action="append", choices=sorted(VARIANTS))
    ap.add_argument("--scan-type", default="standard")
    ap.add_argument("--tickers", default="", help="comma list: run a custom-universe scan")
    args = ap.parse_args()
    _guard_env()

    root = Path(args.root).resolve()
    engine = root
    webapp = root / "webapp"
    os.environ["QUANTA_ENGINE_ROOT"] = str(engine)
    for p in (str(webapp), str(engine / "core"), str(engine / "modules"), str(engine)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import app as webapp_app  # noqa: E402

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    custom = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    for name in args.variant or ["base"]:
        params = dict(VARIANTS[name], market="US", scan_type="custom" if custom else args.scan_type,
                      custom_tickers=custom or None, safe_mode=False)
        job_id = f"golden-{name}-{int(time.time())}"
        with webapp_app._jobs_lock:
            webapp_app._jobs[job_id] = {"type": "stock-picks", "status": "running", "progress": 0,
                                        "message": "", "result": None, "error": None,
                                        "started_at": time.time(), "worker_active": True}
        t0 = time.time()
        webapp_app._run_stock_picks(job_id, params)
        job = webapp_app._jobs[job_id]
        result = job.get("result") or {}
        payload = {"variant": name, "params": params, "status": job.get("status"),
                   "error": job.get("error"), "seconds": round(time.time() - t0, 1),
                   "summary": _summary(result), "result": result}
        (out / f"scan_{name}.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        s = payload["summary"]
        print(f"[{name}] {job.get('status')} in {payload['seconds']}s  scan_status={s['scan_status']}  "
              f"rows={[(r['ticker'], r['final_action'], r['is_funded'], r['rr']) for r in s['rows']]}")


if __name__ == "__main__":
    main()
