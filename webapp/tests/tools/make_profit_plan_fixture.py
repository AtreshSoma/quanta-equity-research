"""Build tests/fixtures/profit_plan.json: the REAL profit_plan.build_profit_plan over the
synthetic My Portfolio fixture (my_portfolio_view.json) and synthetic, deterministic closes.

    python webapp/tests/tools/make_profit_plan_fixture.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "core")]

import pandas as pd  # noqa: E402

from portfolio import profit_plan as pp  # noqa: E402
from portfolio.analytics import PortfolioPolicy  # noqa: E402

FIX = ROOT / "webapp" / "tests" / "fixtures"
TODAY = date(2026, 9, 28)
SESSIONS = 260
# How each synthetic path ends, relative to its own peak: most drift up; one breaks down
# below its profit stop, one sits just above it.
SHAPES = {"NVDA": "up", "AAPL": "up", "MSFT": "breakdown", "KO": "near", "JNJ": "up", "XOM": "down", "RIVN": "down"}


def path(price: float, shape: str):
    out = []
    for i in range(SESSIONS):
        t = i / (SESSIONS - 1)
        wiggle = 1 + 0.015 * math.sin(i / 3.0)
        if shape == "down":
            base = 1.35 - 0.35 * t
        elif shape == "breakdown":
            base = 0.75 + 0.45 * min(t / 0.93, 1.0) - (0.30 if t > 0.97 else 0.0)
        elif shape == "near":
            base = 0.8 + 0.3 * min(t / 0.9, 1.0) - (0.06 if t > 0.95 else 0.0)
        else:
            base = 0.65 + 0.35 * t
        out.append(price * base * wiggle / (1.0 if shape != "breakdown" else 0.9))
    out[-1] = price
    return out


def main() -> None:
    view = json.loads((FIX / "my_portfolio_view.json").read_text(encoding="utf-8"))
    pf = view["portfolio"]
    idx = [TODAY - timedelta(days=SESSIONS - i) for i in range(SESSIONS)]
    cols = {}
    for r in pf["positions"]:
        px = (r.get("broker") or {}).get("last_price")
        if px and r["symbol"] in SHAPES:
            cols[r["symbol"]] = path(float(px), SHAPES[r["symbol"]])
    closes = pd.DataFrame(cols, index=idx)
    known = set(PortfolioPolicy.__dataclass_fields__)
    policy = PortfolioPolicy(**{k: v for k, v in pf["policy"].items() if k in known})
    plan = pp.build_profit_plan(pf["positions"], closes, policy, pf["overview"]["total_value"],
                                pf["overview"]["cash"], TODAY)
    (FIX / "profit_plan.json").write_text(json.dumps(plan, indent=1, sort_keys=True, default=str) + "\n",
                                          encoding="utf-8")
    print({h["symbol"]: h["signal"]["code"] for h in plan["holdings"]})


if __name__ == "__main__":
    main()
