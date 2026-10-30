"""Persisted trial counter for multiple-testing control (deflated Sharpe).

Every time a walk-forward run evaluates a parameter grid, each grid point is a
"trial" the researcher looked at. Deflating a Sharpe ratio needs the honest
count of how many variants were ever tried for a strategy, not just the grid
of the current run. This keeps an append-only JSON log of
``{strategy_key, variant_id, date}`` and counts DISTINCT variants per strategy,
so re-running the same grid does not inflate the count but widening the grid
(or trying new variants) does.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

DEFAULT_TRIALS_PATH = Path(__file__).resolve().parent / "_reports" / "trials.json"


def _load(path: Path) -> List[dict]:
    try:
        rows = json.loads(Path(path).read_text())
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def trial_count(strategy_key: str, path: Union[str, Path, None] = None) -> int:
    """Distinct variants ever registered for ``strategy_key`` (0 if none)."""
    rows = _load(Path(path or DEFAULT_TRIALS_PATH))
    return len({r.get("variant_id") for r in rows if r.get("strategy_key") == strategy_key})


def register_trials(strategy_key: str, variant_ids: Iterable[str],
                    path: Union[str, Path, None] = None,
                    on_date: Optional[str] = None) -> int:
    """Append unseen variants for ``strategy_key``; return the distinct count.

    The file and its folder are created only when this is called. Already
    registered variants are not duplicated.
    """
    p = Path(path or DEFAULT_TRIALS_PATH)
    rows = _load(p)
    seen = {(r.get("strategy_key"), r.get("variant_id")) for r in rows}
    today = on_date or date.today().isoformat()
    added = False
    for vid in variant_ids:
        vid = str(vid)
        if (strategy_key, vid) not in seen:
            rows.append({"strategy_key": strategy_key, "variant_id": vid, "date": today})
            seen.add((strategy_key, vid))
            added = True
    if added:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, indent=2))
    return len({r.get("variant_id") for r in rows if r.get("strategy_key") == strategy_key})


def main(argv: Optional[List[str]] = None) -> int:
    """Register one variant explicitly (the only writer besides a real study run).

    python -m apex_quant.trials <strategy_key> <variant_id> [--on-date YYYY-MM-DD]

    The pre-registration of each Research version names its variant id and the
    command that registers it (docs/preregistration/). Tests never call this.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Register a variant in the persistent trial counter.")
    parser.add_argument("strategy_key")
    parser.add_argument("variant_id")
    parser.add_argument("--on-date", default=None, help="registration date (default: today)")
    args = parser.parse_args(argv)
    count = register_trials(args.strategy_key, [args.variant_id], on_date=args.on_date)
    print(f"{args.strategy_key}: {count} distinct variant(s) registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
