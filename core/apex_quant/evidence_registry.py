"""Write side of the evidence registry read by ``engine.evidence``.

The registry is a small JSON file, one entry per strategy key, recording the
latest out-of-sample verdict and the numbers behind it. Only the validation
harness writes it; engines and surfaces only read it (fail-closed).

    python -m apex_quant.evidence_registry     # rebuild ELITE entry from elite_oos.json

(run from ``core/``).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REPORT_DIR = Path(__file__).resolve().parent / "_reports"
REGISTRY_PATH = REPORT_DIR / "evidence_registry.json"
REGISTRY_ENV = "QUANTA_EVIDENCE_PATH"   # same variable engine.evidence reads

SURVIVORSHIP_CAVEAT = (
    "Universe is a static list of 42 present-day US large caps (survivorship "
    "and selection bias). Relative comparisons are more trustworthy than levels."
)
T_THRESHOLD = 2.0


def _path(path: Optional[Path]) -> Path:
    if path:
        return Path(path)
    override = os.environ.get(REGISTRY_ENV)
    return Path(override) if override else REGISTRY_PATH


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    p = _path(path)
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"strategies": {}, "factors": {}}


def update(section: str, key: str, entry: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Merge one entry into the registry atomically."""
    p = _path(path)
    reg = load(p)
    reg.setdefault("strategies", {})
    reg.setdefault("factors", {})
    entry = dict(entry)
    entry.setdefault("generated_utc", datetime.now(timezone.utc).isoformat())
    reg[section][key] = entry
    reg["updated_utc"] = datetime.now(timezone.utc).isoformat()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True, default=str))
    tmp.replace(p)
    return p


def elite_entry_from_oos(report: Dict[str, Any]) -> Dict[str, Any]:
    """Translate an ``elite_oos`` report into a registry entry.

    VALIDATED would require every horizon's non-overlapping IC t-stat >= 2 AND a
    full-scope test. The ELITE test covers only 40% of the composite weight, so
    the best this can ever be is PARTIAL; a non-positive IC makes it NO_EDGE.
    """
    ic = report.get("ic", {})
    stats = {h: {"mean_ic": v.get("mean_ic"), "t_stat": v.get("t_stat"),
                 "n_independent_dates": v.get("n_independent_dates"),
                 "decile_spread": v.get("decile_spread")} for h, v in ic.items()}
    positive = all((v.get("mean_ic") or 0) > 0 and (v.get("t_stat") or 0) >= T_THRESHOLD
                   for v in ic.values()) and bool(ic)
    status = "PARTIAL" if positive else "NO_EDGE"
    parts = [f"{h}: IC {v['mean_ic']:+.3f} (t {v['t_stat']:+.2f})" for h, v in stats.items()
             if v.get("mean_ic") is not None and v.get("t_stat") is not None]
    return {
        "status": status,
        "summary": ("Composite score does not predict forward returns out of sample — "
                    + "; ".join(parts)) if status == "NO_EDGE" else "; ".join(parts),
        "tested_through": report.get("end"),
        "scope": (f"Technical + risk components only ({report.get('composite_weight_covered', 0):.0%} "
                  "of composite weight); fundamentals have no point-in-time source."),
        "metrics": stats,
        "caveats": [SURVIVORSHIP_CAVEAT,
                    "Walk-forward trade simulation (2017-2026): CAGR 0.76% vs SPY 11.41%."],
        "source": "apex_quant/_reports/elite_oos.json",
        "generated_utc": report.get("generated_at"),
    }


def main(argv=None) -> int:
    oos = REPORT_DIR / "elite_oos.json"
    if not oos.exists():
        print(f"missing {oos}", file=sys.stderr)
        return 1
    p = update("strategies", "elite.composite", elite_entry_from_oos(json.loads(oos.read_text())))
    print(f"updated {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
