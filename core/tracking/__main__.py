"""Track-record CLI.

    python -m tracking resolve          # fill in outcomes whose horizon has elapsed
    python -m tracking report           # benchmarked stats per engine/horizon/stance
    python -m tracking report --by regime

Run from ``core/`` (or with ``core`` on PYTHONPATH).
"""
from __future__ import annotations

import argparse
import json
import sys

from .ledger import SignalLedger, yfinance_loader


def _fmt(x, spec="+.2f", suffix="%"):
    return "n/a" if x is None else f"{x:{spec}}{suffix}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracking")
    ap.add_argument("command", choices=["resolve", "report"])
    ap.add_argument("--path", help="ledger file (default: $QUANTA_LEDGER_PATH or data/ledger)")
    ap.add_argument("--engine")
    ap.add_argument("--by", nargs="*", default=["engine", "horizon_days", "stance"],
                    help="group keys; may include recorded context keys such as 'regime'")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ledger = SignalLedger(args.path)
    if args.command == "resolve":
        n = ledger.resolve_outcomes(yfinance_loader)
        print(f"Resolved {n} call(s). Ledger: {ledger.path}")
        return 0

    stats = ledger.summarize(group_by=args.by, engine=args.engine)
    if args.json:
        print(json.dumps([s.to_dict() for s in stats], indent=2, default=str))
        return 0
    print(f"Ledger: {ledger.path}")
    print("Excess return is vs SPY over the same sessions, signed so + means the call was right.")
    print("t-stat uses per-date means (same-day calls are not independent).\n")
    if not stats:
        print("No calls recorded yet.")
        return 0
    for s in stats:
        key = ", ".join(f"{k}={v}" for k, v in s.key.items())
        hit = "n/a" if s.hit_rate is None else f"{s.hit_rate:.0%}"
        ci = "n/a" if s.ci95_excess_pct is None else (
            f"[{s.ci95_excess_pct[0]:+.2f}%, {s.ci95_excess_pct[1]:+.2f}%]")
        t = "n/a" if s.t_stat is None else f"{s.t_stat:+.2f}"
        print(f"{key}\n  resolved {s.n_resolved} (pending {s.n_pending}, {s.n_dates} dates)  "
              f"hit {hit}  mean excess {_fmt(s.mean_excess_pct)}  95% CI {ci}  t {t}\n"
              f"  -> {s.verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
