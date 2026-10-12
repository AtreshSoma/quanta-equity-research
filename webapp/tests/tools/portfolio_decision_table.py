"""Replay a saved account snapshot through the real My Portfolio guidance code.

The snapshot (a pickled ``portfolio.models.Portfolio`` plus the time it was taken)
is replayed through ``PortfolioService`` with a read-only stub provider, the
webapp's real research function (``app._portfolio_analyze``) and its real returns
function. The view is built with the clock fixed at the snapshot time, so quote
freshness reads the same as it did when the snapshot was taken.

Running the same snapshot against two code trees (``--root``) isolates the code
change: same account, same session, different engine.

The output table holds actions, rule codes, scores and grades only. No quantity,
value, weight or account identifier is written, matching the decision journal's
rule. The snapshot itself holds account data: keep it outside the repository.

    python portfolio_decision_table.py --root <tree> --snapshot <file.pkl> --out <table.json>
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path


def _guard_env() -> None:
    if os.environ.get("QUANTA_TRACKING_DISABLED") != "1":
        sys.exit("refusing to run: set QUANTA_TRACKING_DISABLED=1")
    for var in ("QUANTA_LEDGER_PATH", "QUANTA_CACHE_DIR", "QUANTA_JOURNAL_PATH"):
        val = os.environ.get(var, "")
        if not val or "Temp" not in val and "scratch" not in val.lower():
            sys.exit(f"refusing to run: {var} must point at a scratch location (got {val!r})")


def table_from_view(view: dict) -> list:
    rows = []
    for r in (view.get("portfolio") or {}).get("positions") or []:
        g = r.get("guidance") or {}
        d = (r.get("analysis") or {}).get("decision") or {}
        score = d.get("score")
        rows.append({
            "symbol": r.get("symbol"),
            "action": g.get("action"),
            "new_money": g.get("new_money"),
            "basis": (g.get("basis") or {}).get("code"),
            "score": round(score, 1) if isinstance(score, (int, float)) else None,
            "grade": d.get("risk_grade"),
            "grade_drivers": [f"{x.get('factor')}+{x.get('points')}" for x in (d.get("risk_grade_drivers") or [])
                              if x.get("points")],
            "holder_action": d.get("holder_action"),
            "engine_action": d.get("action"),
            "codes": d.get("blocking_codes"),
            "no_call": (r.get("no_call") or {}).get("code"),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
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
    from portfolio.models import ConnectionState  # noqa: E402
    from portfolio.provider import PortfolioProvider  # noqa: E402
    from portfolio.service import PortfolioService  # noqa: E402

    snap = pickle.loads(Path(args.snapshot).read_bytes())
    pf = snap["portfolio"]
    taken = datetime.fromtimestamp(snap["taken"], tz=timezone.utc)

    class SnapshotProvider(PortfolioProvider):
        name = display_name = "snapshot"

        def state(self):
            return ConnectionState.CONNECTED

        def status(self):
            return {"state": "connected", "provider": "snapshot"}

        def begin_connect(self, redirect_uri):
            raise RuntimeError("snapshot provider cannot connect")

        def complete_connect(self, params):
            raise RuntimeError("snapshot provider cannot connect")

        def disconnect(self):
            pass

        def list_accounts(self):
            return [pf.account]

        def load_portfolio(self, account_handle):
            return pf

    svc = PortfolioService(SnapshotProvider(), webapp_app._portfolio_analyze, webapp_app._portfolio_returns,
                           clock=lambda: taken)
    view = svc.refresh(snap.get("account") or "snapshot", start_thread=False)
    rows = table_from_view(view)
    Path(args.out).write_text(json.dumps({"snapshot_taken": taken.isoformat(), "root": str(root),
                                          "rows": rows}, indent=1), encoding="utf-8")
    for row in rows:
        print(row["symbol"], row["action"], row["basis"], row["score"], row["grade"], row["holder_action"])


if __name__ == "__main__":
    main()
