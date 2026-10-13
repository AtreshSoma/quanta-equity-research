"""Track-record CLI.

    python -m tracking resolve          # fill in outcomes whose horizon has elapsed
    python -m tracking report           # benchmarked stats per engine/horizon/stance
    python -m tracking report --by regime
    python -m tracking alerts           # stops / targets touched by open calls
    python -m tracking alerts --notify  # ... and post a macOS notification
    python -m tracking publish          # export the tamper-evident record to track_record/
    python -m tracking verify           # check that record's hash chain and calls

Run from ``core/`` (or with ``core`` on PYTHONPATH).
"""
from __future__ import annotations

import argparse
import json
import sys

from .alerts import check_levels, notify_desktop
from .ledger import SignalLedger, yfinance_loader
from .publish import publish, verify


def _fmt(x, spec="+.2f", suffix="%"):
    return "n/a" if x is None else f"{x:{spec}}{suffix}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracking")
    ap.add_argument("command", choices=["resolve", "report", "alerts", "publish", "verify"])
    ap.add_argument("--path", help="ledger file (default: $QUANTA_LEDGER_PATH or data/ledger)")
    ap.add_argument("--engine")
    ap.add_argument("--by", nargs="*", default=["engine", "horizon_days", "stance"],
                    help="group keys; may include recorded context keys such as 'regime'")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out-dir", help="publish/verify: record directory (default: track_record/)")
    ap.add_argument("--notify", action="store_true", help="alerts: post a desktop notification")
    args = ap.parse_args(argv)

    ledger = SignalLedger(args.path)
    if args.command == "resolve":
        n = ledger.resolve_outcomes(yfinance_loader)
        print(f"Resolved {n} call(s). Ledger: {ledger.path}")
        return 0

    if args.command == "alerts":
        alerts = check_levels(ledger, yfinance_loader)
        if args.json:
            print(json.dumps([a.to_dict() for a in alerts], indent=2))
        else:
            print(f"{len(alerts)} new level alert(s). Ledger: {ledger.path}")
            for a in alerts:
                print(f"  {a.kind:<6} {a.message()}")
        if args.notify:
            notify_desktop(alerts)
        return 0

    if args.command == "publish":
        res = publish(ledger, args.out_dir)
        state = "updated" if res["changed"] else "unchanged"
        print(f"Track record {state}: {res['rows']} call(s) in {res['dir']} (chain head {res['head'][:12]})")
        return 0

    if args.command == "verify":
        res = verify(args.out_dir, ledger)
        print(f"{'OK' if res.ok else 'FAILED'}: {res.links} chain link(s), {res.rows} published call(s)")
        for problem in res.problems:
            print(f"  - {problem}")
        return 0 if res.ok else 1

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
