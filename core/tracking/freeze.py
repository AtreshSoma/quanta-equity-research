"""Model freeze: hold a model's scoring code still long enough for its test to finish.

The pre-registered prospective test needs 30 resolved calls on 10 independent
dates from ONE model fingerprint. Between 2026-10-16 and 2026-11-04 the
Research engine's logic changed in about 20 periods (docs/competitive-analysis/
11-IMPLEMENTATION-STATUS.md), so no version ever got there. A freeze records the
model fingerprint of an engine and a date until which it must not change.

Opt-in. With no ``track_record/model_freeze.json`` nothing is enforced. Once a
freeze exists, ``tests/test_model_freeze.py`` fails while the frozen engine's
model fingerprint differs from the recorded one and the freeze has not ended, and
says what to do: put the change into a new shadow version, or release the freeze
on purpose (which is itself recorded, with a reason).

    python -m tracking.freeze status
    python -m tracking.freeze set elite --days 91 --reason "test elite.research.v2"
    python -m tracking.freeze release elite --reason "bug fix in the stop rule"

Run from ``core/``. The file sits beside the published track record, so the
freeze and every release are committed and timestamped with it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fingerprint import ENGINE_SOURCES, engine_sources, model_fingerprint

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "track_record" / "model_freeze.json"


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path or DEFAULT_PATH)
    if not p.exists():
        return {"frozen": {}, "released": []}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("frozen", {})
    data.setdefault("released", [])
    return data


def _save(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = Path(path or DEFAULT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def _known(engine: str) -> None:
    if not engine_sources(engine):
        raise ValueError(f"unknown engine {engine!r}; known: {', '.join(sorted(ENGINE_SOURCES))}")


def freeze(engine: str, days: int, reason: str, today: Optional[date] = None,
           path: Optional[Path] = None) -> Dict[str, Any]:
    """Freeze ``engine`` at its current model fingerprint for ``days`` calendar days."""
    _known(engine)
    if days < 1:
        raise ValueError("days must be at least 1")
    if not reason.strip():
        raise ValueError("a reason is required")
    today = today or date.today()
    data = load(path)
    entry = {"model_fingerprint": model_fingerprint(engine), "since": today.isoformat(),
             "until": (today + timedelta(days=int(days))).isoformat(), "reason": reason.strip()}
    data["frozen"][engine] = entry
    _save(data, path)
    return entry


def release(engine: str, reason: str, today: Optional[date] = None,
            path: Optional[Path] = None) -> Dict[str, Any]:
    """End a freeze early, keeping a record of when and why."""
    if not reason.strip():
        raise ValueError("a reason is required")
    data = load(path)
    entry = data["frozen"].pop(engine, None)
    if entry is None:
        raise ValueError(f"{engine} is not frozen")
    record = dict(entry, engine=engine, released_on=(today or date.today()).isoformat(),
                  release_reason=reason.strip())
    data["released"].append(record)
    _save(data, path)
    return record


def check(today: Optional[date] = None, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """One row per frozen engine: whether its model code still matches the freeze."""
    today = today or date.today()
    out = []
    for engine, entry in sorted(load(path)["frozen"].items()):
        active = today.isoformat() <= str(entry.get("until"))
        current = model_fingerprint(engine)
        out.append({"engine": engine, "active": active, "until": entry.get("until"),
                    "since": entry.get("since"), "reason": entry.get("reason"),
                    "frozen_fingerprint": entry.get("model_fingerprint"),
                    "current_fingerprint": current,
                    "intact": current == entry.get("model_fingerprint")})
    return out


def breaches(today: Optional[date] = None, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Active freezes whose model code has changed."""
    return [r for r in check(today, path) if r["active"] and not r["intact"]]


def status_for(engine: str, today: Optional[date] = None,
               path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The freeze row for one engine, for the Evidence page; None when not frozen."""
    return next((r for r in check(today, path) if r["engine"] == engine), None)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracking.freeze")
    ap.add_argument("command", choices=["status", "set", "release"])
    ap.add_argument("engine", nargs="?")
    ap.add_argument("--days", type=int, default=91, help="set: freeze length in calendar days")
    ap.add_argument("--reason", default="", help="set / release: why (required)")
    ap.add_argument("--path", help="freeze file (default: track_record/model_freeze.json)")
    a = ap.parse_args(argv)
    path = Path(a.path) if a.path else None
    try:
        if a.command == "set":
            if not a.engine:
                ap.error("set needs an engine")
            e = freeze(a.engine, a.days, a.reason, path=path)
            print(f"{a.engine} frozen at {e['model_fingerprint'][:12]} until {e['until']}.")
            return 0
        if a.command == "release":
            if not a.engine:
                ap.error("release needs an engine")
            r = release(a.engine, a.reason, path=path)
            print(f"{a.engine} released on {r['released_on']}: {r['release_reason']}")
            return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rows = check(path=path)
    if not rows:
        print("No engine is frozen.")
        return 0
    for r in rows:
        state = ("intact" if r["intact"] else "CHANGED") if r["active"] else "ended"
        print(f"{r['engine']:<24} frozen {r['since']} to {r['until']}  {state}  ({r['reason']})")
    return 1 if any(r["active"] and not r["intact"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
