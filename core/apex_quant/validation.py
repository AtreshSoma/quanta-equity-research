"""Validation artifact I/O and the refuse-to-size confidence gate.

The system will NOT emit position sizes unless a current, passing validation
artifact exists. "Passing" means the walk-forward OOS result beat SPY and both
random baselines on a risk-adjusted basis with positive expectancy, and the
artifact is not stale.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .walkforward import WalkForwardResult

# Bump whenever the fill model changes in a way that invalidates prior results.
# "f01" is the corrected chronology: entries admitted before same-day exits,
# stop/target live from the entry bar, opening gaps fill at the open, terminal
# holdings closed and the first day's P&L in the curve. Any artifact produced
# under a different value describes trades that could not have happened the way
# the engine now simulates them, so it cannot authorise sizing.
EXECUTION_SEMANTICS_VERSION = "f01-causal-2026-09"


def implementation_hash() -> str:
    """Invalidate research approval when executable model code changes."""
    core = Path(__file__).resolve().parent.parent
    files = sorted((core / "apex_quant").glob("*.py")) + [core / "TradingAnalysis_ELITE.py"]
    digest = hashlib.sha256()
    for source in files:
        digest.update(source.name.encode())
        digest.update(source.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def strategy_identity(params: Any, universe: Any, costs: Any,
                      execution_semantics: str = EXECUTION_SEMANTICS_VERSION,
                      code_hash: Optional[str] = None) -> str:
    """Stable fingerprint of everything a validation result is conditional on.

    Approval is for a specific strategy, not for the project in general. The
    gate used to check only `passed` and age, so changing a stop multiple, the
    universe or the cost model left the old approval in force. Sorting keys
    makes the digest independent of dict ordering.
    """
    payload = json.dumps(
        {
            "params": params,
            "universe": sorted(universe) if isinstance(universe, (list, tuple, set)) else universe,
            "costs": costs,
            "execution_semantics": execution_semantics,
            "code_hash": code_hash or implementation_hash(),
        },
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_artifact(result: WalkForwardResult, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = result.validation_inputs
    if not identity or any(identity.get(k) is None for k in ("params", "universe", "costs")):
        raise ValueError("Validation results must record parameters, universe and costs")
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "oos_start": str(result.oos_start.date()),
        "oos_end": str(result.oos_end.date()),
        "data_hash": result.data_hash,
        "passed": result.beats_benchmarks() and result.coverage_complete,
        "coverage_complete": result.coverage_complete,
        "validation_inputs": identity,
        "code_hash": implementation_hash(),
        # What this result is conditional on. Without it a later run cannot
        # tell whether the approval still applies to the current strategy.
        "execution_semantics": EXECUTION_SEMANTICS_VERSION,
        "strategy_identity": strategy_identity(**identity),
        "n_folds": len(result.folds),
        "strategy": result.strategy.to_dict(),
        "spy": result.spy.to_dict(),
        "random_entry": result.random_entry.to_dict(),
        "random_exit": result.random_exit.to_dict(),
        "param_stability": result.param_stability,
        "caveats": [
            "Universe is static present-day liquid large-caps: survivorship "
            "bias inflates absolute returns. Trust relative comparisons.",
            "Price/volume only. No fundamentals component (no validated PIT "
            "fundamentals source configured).",
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_artifact(path: Path) -> Optional[dict]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def is_current(artifact: Optional[dict], max_age_days: int = 30,
               current_identity: Optional[Dict[str, Any]] = None) -> bool:
    """Whether ``artifact`` authorises sizing for the CURRENT configuration.

    ``current_identity`` is the kwargs of `strategy_identity` for the strategy
    about to be sized. When supplied, a passing artifact whose fingerprint does
    not match is refused — it validated something else. It is optional so
    existing callers can verify the recorded inputs against the current code.
    Artifacts without complete recorded inputs are refused.
    """
    if not artifact or not artifact.get("passed"):
        return False
    if artifact.get("execution_semantics") != EXECUTION_SEMANTICS_VERSION:
        return False
    if artifact.get("coverage_complete") is not True:
        return False
    if artifact.get("code_hash") != implementation_hash():
        return False
    try:
        gen = datetime.fromisoformat(artifact["generated_utc"])
        age_seconds = (datetime.now(timezone.utc) - gen).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    if not 0 <= age_seconds <= max_age_days * 86400:
        return False

    if current_identity is None:
        current_identity = artifact.get("validation_inputs")
        if not current_identity:
            return False

    if current_identity is not None:
        recorded = artifact.get("strategy_identity")
        if not recorded:
            # Predates identity recording: it cannot prove what it validated.
            return False
        try:
            matches = recorded == strategy_identity(**current_identity)
        except (TypeError, ValueError):
            return False
        if not matches:
            return False
    return True


def confidence_statement(result: WalkForwardResult) -> str:
    s, spy = result.strategy, result.spy
    passed = result.beats_benchmarks() and result.coverage_complete
    lines = [
        "=" * 70,
        "APEX QUANT - VALIDATION CONFIDENCE STATEMENT",
        "=" * 70,
        f"Out-of-sample window : {result.oos_start.date()} to {result.oos_end.date()}"
        f"  ({len(result.folds)} walk-forward folds)",
        f"Data hash            : {result.data_hash[:16]}...",
        "",
        "Out-of-sample, net-of-costs results:",
        f"  Strategy   CAGR {s.cagr_pct:6.2f}%  Sharpe {s.sharpe:5.2f}  "
        f"Sortino {s.sortino:5.2f}  MaxDD {s.max_drawdown_pct:5.2f}%  "
        f"Calmar {s.calmar:5.2f}",
        f"  SPY B&H    CAGR {spy.cagr_pct:6.2f}%  Sharpe {spy.sharpe:5.2f}",
        f"  RandEntry  Sharpe {result.random_entry.sharpe:5.2f}",
        f"  RandExit   Sharpe {result.random_exit.sharpe:5.2f}",
        f"  Expectancy/trade {s.expectancy_pct:+.3f}%  "
        f"HitRate {s.hit_rate_pct:.1f}%  Trades {s.n_trades}  "
        f"ProfitFactor {s.profit_factor:.2f}",
        f"  Alpha(ann) {('%.2f%%' % s.alpha_ann_pct) if s.alpha_ann_pct is not None else 'n/a'}"
        f"  Beta {('%.2f' % s.beta) if s.beta is not None else 'n/a'}",
        "",
    ]
    if passed:
        lines.append(
            "VERDICT: Strategy beat SPY and both random baselines with positive "
            "expectancy. Sizing is PERMITTED for the validated configuration."
        )
    else:
        lines.append(
            "VERDICT: Strategy did NOT beat all benchmarks / expectancy not "
            "positive. This is a SCREENER ONLY. Sizing is REFUSED."
        )
    lines.append(
        "CAVEAT: Static survivorship-biased universe; price/volume only; "
        "educational use only, not investment advice."
    )
    lines.append("=" * 70)
    return "\n".join(lines)


def sizing_gate_message(artifact: Optional[dict], max_age_days: int = 30,
                        current_identity: Optional[Dict[str, Any]] = None) -> str:
    if is_current(artifact, max_age_days, current_identity):
        return "Sizing PERMITTED: current passing validation on file."
    if artifact is None:
        return (
            "Sizing REFUSED: no validation artifact found. Run the walk-forward "
            "validation first."
        )
    if not artifact.get("passed"):
        return (
            "Sizing REFUSED: latest validation did not beat benchmarks. "
            "This configuration is a screener only."
        )
    try:
        gen = datetime.fromisoformat(artifact["generated_utc"])
        age = (datetime.now(timezone.utc) - gen).total_seconds()
        stale = not 0 <= age <= max_age_days * 86400
    except (KeyError, ValueError, TypeError):
        stale = True
    if stale:
        return "Sizing REFUSED: validation artifact is stale. Re-run validation."
    if current_identity is not None:
        recorded = artifact.get("strategy_identity")
        if not recorded:
            return (
                "Sizing REFUSED: the validation artifact does not record which "
                "configuration it validated. Re-run validation."
            )
        return (
            "Sizing REFUSED: the strategy configuration changed since this "
            "validation (parameters, universe, costs or execution semantics). "
            "Re-run validation for the current configuration."
        )
    return "Sizing REFUSED: validation identity or coverage is incomplete or changed. Re-run validation."
