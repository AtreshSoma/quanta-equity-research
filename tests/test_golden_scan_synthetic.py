"""A14: a frozen scan-level golden snapshot on synthetic providers.

The per-ticker golden master replays recorded public data for single
analyses; nothing froze a whole Picks scan. This runs the real worker and
engine on the synthetic providers of ``test_scan_accounting`` (including a
gap name for the chase list) and compares the decision-bearing fields with a
frozen snapshot. Timestamps and timing are excluded.

Regenerate deliberately, after reviewing the change: QUANTA_REGEN_SCAN_GOLDEN=1.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

SNAPSHOT = Path(__file__).resolve().parent / "golden" / "expected_scan" / "synthetic_scan.json"


def _r(v):
    return round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _business(result):
    rows = [{k: _r(p.get(k)) for k in ("ticker", "final_action", "allocation", "shares", "entry_price",
                                        "stop_loss", "price_target", "risk_reward_ratio", "is_funded")}
            for p in result["recommendations"]]
    acc = dict(result["accounting"])
    chase = [{k: _r(c.get(k)) for k in ("ticker", "trigger", "move_pct", "threshold_pct", "bar_date",
                                         "session_date", "distance_from_20dma")} for c in result["chase_alerts"]]
    return {
        "scan_status": result["scan_status"], "funded_count": result["funded_count"],
        "watch_count": result["watch_count"], "recommendations": rows,
        "accounting": json.loads(json.dumps(acc, default=str)),
        "chase_alerts": chase, "rejected": [r.get("ticker") for r in result.get("rejected") or []],
        "universe_selection": {k: result["universe_selection"].get(k) for k in ("method", "candidates", "selected")},
        "bar_session": result["data_dates"]["bar_session"],
    }


def _scan(monkeypatch):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    monkeypatch.setitem(acc._NAMES, "SYNJ", ({}, dict(acc._GOOD), True, False))
    orig = acc._frame
    j = list(acc._NAMES).index("SYNJ")

    def gapped(i, **kw):
        f = orig(i, **kw)
        if i == j:
            f = f.copy()
            o = float(f["Close"].iloc[-2]) * 1.072
            f.iloc[-1, f.columns.get_loc("Open")] = o
            f.iloc[-1, f.columns.get_loc("High")] = max(o, float(f["High"].iloc[-1]))
        return f

    monkeypatch.setattr(acc, "_frame", gapped)
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {"SYNA": "WATCHLIST"})
    import app as webapp
    job = "golden-synthetic-scan"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "message": "",
                         "started_at": time.time(), "worker_active": True}
    params = dict(capital=25_000.0, market="US", scan_type="custom", custom_tickers=list(acc._NAMES),
                  risk_profile="balanced", max_positions=5, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job, params)
        out = webapp._jobs[job]
        assert out["status"] == "complete", out.get("error")
        return out["result"]
    finally:
        webapp._jobs.pop(job, None)


def test_the_synthetic_scan_matches_its_frozen_snapshot(monkeypatch):
    got = _business(_scan(monkeypatch))
    if os.environ.get("QUANTA_REGEN_SCAN_GOLDEN") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("snapshot regenerated")
    assert SNAPSHOT.exists(), "no frozen snapshot: run once with QUANTA_REGEN_SCAN_GOLDEN=1 and review it"
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert got == want


def test_the_snapshot_is_not_trivial():
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert want["funded_count"] >= 1 and want["watch_count"] >= 1, "covers a funded and a watch row"
    assert want["chase_alerts"] and want["accounting"]["could_not_analyse"]["count"] >= 1
