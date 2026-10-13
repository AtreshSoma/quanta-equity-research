"""Write side of the evidence registry read by ``engine.evidence``.

The registry is a small JSON file, one entry per strategy key, recording the
latest out-of-sample verdict and the numbers behind it. Only the validation
harness writes it; engines and surfaces only read it (fail-closed).

    python -m apex_quant.evidence_registry     # rebuild ELITE entry from elite_oos.json
    python -m apex_quant.evidence_registry shortterm <summary.json>   # record a scanner replay

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


def shortterm_entry_from_backtest(summary: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Translate an ``sp_shortterm_backtest`` summary into a registry entry.

    The replay scores the price-based legs only (technical, momentum, pattern)
    with the other legs neutral, so the best possible verdict is PARTIAL: signal
    trades beat SPY on the same dates (95% CI above zero) AND beat random entries
    with the same exits. Anything else is NO_EDGE.
    """
    ex = summary.get("excess_vs_spy") or {}
    diff = summary.get("signal_minus_random_excess") or {}
    ci, dci = ex.get("ci95_bootstrap"), diff.get("ci95_bootstrap")
    beats_spy, beats_random = bool(ci and ci[0] > 0), bool(dci and dci[0] > 0)
    positive = beats_spy and beats_random
    cfg = summary.get("config") or {}
    weights = cfg.get("composite_weights") or {}
    covered = sum(weights.get(k, 0.0) for k in ("technical", "momentum", "pattern"))
    def pct(x):
        return "n/a" if x is None else f"{x:+.2f}%"
    stated = (f"{summary.get('total_trades', 0)} trades {cfg.get('start')}..{cfg.get('end')}: "
              f"excess vs SPY {pct(ex.get('mean'))} per {ex.get('unit') or 'trade'} "
              f"(95% CI {pct(ci[0]) if ci else 'n/a'} to {pct(ci[1]) if ci else 'n/a'}"
              + (f"; {pct(ex['mean_per_trade'])} per trade" if ex.get("mean_per_trade") is not None else "")
              + "); "
              f"vs random entries {pct(diff.get('mean'))} "
              f"(CI {pct(dci[0]) if dci else 'n/a'} to {pct(dci[1]) if dci else 'n/a'})")
    return {
        "status": "PARTIAL" if positive else "NO_EDGE",
        "summary": (stated if positive else
                    "Entry signals do not " + (
                        "beat SPY or random entries" if not (beats_spy or beats_random)
                        else "beat random entries" if beats_spy else "beat SPY")
                    + " out of sample: " + stated),
        "tested_through": cfg.get("end"),
        "scope": (f"Price-based legs only (technical, momentum, pattern: {covered:.0%} of composite "
                  "weight); fundamental, quality and analyst legs held neutral. No regime "
                  "multiplier, peer re-scoring or portfolio caps."),
        "metrics": {"excess_vs_spy": ex, "signal_minus_random_excess": diff,
                    "win_rate_pct": summary.get("win_rate_pct"),
                    "probabilistic_sharpe": summary.get("probabilistic_sharpe"),
                    "deflated_sharpe": summary.get("deflated_sharpe"),
                    "sequential_drawdown_pct": summary.get("sequential_drawdown_pct")},
        "caveats": [SURVIVORSHIP_CAVEAT] + ([
            f"A {summary.get('win_rate_pct')}% win rate is not evidence on its own: random entries "
            f"with the same exits won {(summary.get('random_baseline') or {}).get('win_rate_pct')}%."]
            if (summary.get("random_baseline") or {}).get("win_rate_pct") is not None else []),
        "code_fingerprint": summary.get("code_fingerprint"),
        "source": source,
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["shortterm"] and len(argv) == 2:
        src = Path(argv[1])
        try:
            source = src.resolve().relative_to(REPORT_DIR.parent.parent).as_posix()
        except ValueError:  # outside core/: record it as given
            source = str(src)
        entry = shortterm_entry_from_backtest(json.loads(src.read_text()), source=source)
        p = update("strategies", "shortterm.composite", entry)
        print(f"updated {p}: shortterm.composite -> {entry['status']}")
        return 0
    oos = REPORT_DIR / "elite_oos.json"
    if not oos.exists():
        print(f"missing {oos}", file=sys.stderr)
        return 1
    p = update("strategies", "elite.composite", elite_entry_from_oos(json.loads(oos.read_text())))
    print(f"updated {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
