"""Evidence gate: what has actually been shown to work, and how to label output.

Every engine in this project produces a model action (STRONG_BUY ... AVOID).
Whether that action deserves to be *presented* as a recommendation depends on
one thing only: has the scoring behind it been validated out of sample?

This module is the single place that answers that question. It reads the
evidence registry written by the validation harness
(``core/apex_quant/elite_oos.py``, ``core/sp_shortterm_backtest.py`` and
``core/apex_quant/evidence_registry.py``) and converts a model
action into a presentation label:

* VALIDATED strategy  -> the action is shown as-is (BUY, ACCUMULATE, ...).
* anything else       -> buy-tier actions keep their plain name, so a reader
                         always sees the model's actual call, but every one
                         carries a ``qualifier`` ("Unproven model", ...) and a
                         caveat, and position sizes are shown as a *risk
                         budget*, not a recommendation.

The gate fails closed: a missing, unreadable or stale registry means
"not validated".
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import FinalAction

REGISTRY_ENV = "QUANTA_EVIDENCE_PATH"
DEFAULT_REGISTRY = (
    Path(__file__).resolve().parent.parent / "apex_quant" / "_reports" / "evidence_registry.json"
)
# Evidence older than this is treated as stale: markets change, and a result
# nobody has re-run in a quarter is not current evidence.
MAX_EVIDENCE_AGE_DAYS = 120

# Strategy keys. One per scoring path that reaches a user.
SHORTTERM = "shortterm.composite"
SHORTTERM_PRICE_CORE = "shortterm.price_core"
# The Research model changes version whenever its scoring or levels change,
# so no earlier test can validate a model it did not measure:
#   elite.composite      -- tested out of sample: NO_EDGE (2026-08-27)
#   elite.alpha_only.v1  -- risk leg removed from the ranking (never tested)
#   elite.research.v2    -- 2026-09-27 audit: two-sided valuation, independent
#                           quality leg, one-horizon reward/risk; tested out of
#                           sample: NO_EDGE (through 2026-09-30)
ELITE = "elite.research.v2"
ELITE_PRO = "elite_pro.composite"

# A scanner call is funded only after the model named here confirms it (Picks
# deep-confirm runs the Research engine and publishes its levels). While the
# scanner's own composite has no test of its own, its calls carry the governing
# model's result, so a Picks Buy shows "no edge found" when Research has none.
GOVERNED_BY: Dict[str, str] = {ELITE_PRO: ELITE}

# Earlier versions of each live model, newest first. Their results are shown
# as LINEAGE -- what was learned about the predecessor -- never as evidence
# for the current version. The removal of the risk leg was chosen by looking
# at the predecessor's out-of-sample result, so that sample period is no
# longer out-of-sample for any successor.
LINEAGE: Dict[str, List[Dict[str, str]]] = {
    ELITE: [
        {"strategy": "elite.alpha_only.v1", "change": "risk leg removed from the ranking composite"},
        {"strategy": "elite.composite", "change": "original composite"},
    ],
}


class EvidenceStatus(str, Enum):
    VALIDATED = "VALIDATED"          # passed OOS tests on its full scope, current
    PARTIAL = "PARTIAL"              # a sub-component passed; the whole was not testable
    NO_EDGE = "NO_EDGE"              # tested out of sample, no demonstrated predictive power
    UNTESTED = "UNTESTED"            # never measured
    STALE = "STALE"                  # was validated, but the evidence is too old

    @property
    def permits_recommendation(self) -> bool:
        return self is EvidenceStatus.VALIDATED


_STATUS_TEXT = {
    EvidenceStatus.VALIDATED: "Validated out of sample",
    EvidenceStatus.PARTIAL: "Partially tested, not validated as a whole",
    EvidenceStatus.NO_EDGE: "Tested out of sample: no demonstrated edge",
    EvidenceStatus.UNTESTED: "Never validated",
    EvidenceStatus.STALE: "Validation is out of date",
}


@dataclass
class Evidence:
    strategy: str
    status: EvidenceStatus = EvidenceStatus.UNTESTED
    summary: str = ""
    tested_through: Optional[str] = None      # last date in the OOS sample
    generated_utc: Optional[str] = None
    scope: str = ""                            # what was (and was not) testable
    metrics: Dict[str, Any] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)
    # Results for earlier versions of this model (context, never evidence).
    lineage: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def status_text(self) -> str:
        return _STATUS_TEXT[self.status]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["status_text"] = self.status_text
        d["permits_recommendation"] = self.status.permits_recommendation
        return d


def registry_path() -> Path:
    override = os.environ.get(REGISTRY_ENV)
    return Path(override) if override else DEFAULT_REGISTRY


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path) if path else registry_path()
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _age_days(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds() / 86400.0


def _lineage(strategy: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in LINEAGE.get(strategy, []):
        entry = (reg.get("strategies") or {}).get(item["strategy"]) or {}
        try:
            status = EvidenceStatus(entry.get("status", "UNTESTED"))
        except ValueError:
            status = EvidenceStatus.UNTESTED
        out.append({
            "strategy": item["strategy"],
            "change": item.get("change", ""),
            "status": status.value,
            "status_text": _STATUS_TEXT[status],
            "summary": str(entry.get("summary", "")) if entry else "Never tested.",
            "tested_through": entry.get("tested_through") if entry else None,
        })
    return out


def get_evidence(strategy: str, registry: Optional[Dict[str, Any]] = None,
                 now: Optional[datetime] = None) -> Evidence:
    """Evidence for *strategy*. Never raises; unknown means UNTESTED."""
    reg = registry if registry is not None else load_registry()
    entry = (reg.get("strategies") or {}).get(strategy)
    lineage = _lineage(strategy, reg)
    governor = GOVERNED_BY.get(strategy)
    if not entry and governor:
        gov = get_evidence(governor, reg, now)
        if gov.status is not EvidenceStatus.UNTESTED:
            gov.strategy = strategy
            gov.summary = (
                f"This scanner's own score has not been tested on its own. Every Buy or Accumulate call "
                f"must pass the Research model ({governor}), whose result applies: {gov.summary}"
            )
            return gov
    if not entry:
        ev = Evidence(
            strategy=strategy,
            status=EvidenceStatus.UNTESTED,
            summary="No out-of-sample validation has been run for this scoring model.",
            lineage=lineage,
        )
        failed = [x for x in lineage if x["status"] == EvidenceStatus.NO_EDGE.value]
        if failed:
            ev.summary = (
                "This version has not been tested. Its predecessor "
                f"({failed[0]['strategy']}) was tested out of sample and showed no edge: "
                f"{failed[0]['summary']}"
            )
            ev.caveats.append(
                "Changes since the tested predecessor were informed by that test, so its "
                "sample period is no longer out-of-sample for this version."
            )
        return ev
    try:
        status = EvidenceStatus(entry.get("status", "UNTESTED"))
    except ValueError:
        status = EvidenceStatus.UNTESTED
    ev = Evidence(
        strategy=strategy,
        status=status,
        summary=str(entry.get("summary", "")),
        tested_through=entry.get("tested_through"),
        generated_utc=entry.get("generated_utc"),
        scope=str(entry.get("scope", "")),
        metrics=dict(entry.get("metrics") or {}),
        caveats=list(entry.get("caveats") or []),
        lineage=lineage,
    )
    if ev.status is EvidenceStatus.VALIDATED:
        age = _age_days(ev.generated_utc, now)
        if age is None or age > MAX_EVIDENCE_AGE_DAYS:
            ev.status = EvidenceStatus.STALE
            ev.caveats.append(
                f"Validation older than {MAX_EVIDENCE_AGE_DAYS} days (or undated); re-run it."
            )
    return ev


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

@dataclass
class Presentation:
    label: str                 # what the user sees as the call
    stance: str                # BULLISH / NEUTRAL / BEARISH / NONE
    sizing_basis: str          # "validated" | "risk-budget-only" | "none"
    caveat: str = ""
    qualifier: str = ""        # shown beside an unvalidated buy-tier label

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


_VALIDATED_LABELS = {
    FinalAction.STRONG_BUY: "STRONG BUY",
    FinalAction.BUY: "BUY",
    FinalAction.ACCUMULATE: "ACCUMULATE",
}
_NEUTRAL_LABELS = {
    FinalAction.HOLD_EXISTING: "HOLD (owned)",
    FinalAction.WATCHLIST: "WATCHLIST",
    FinalAction.AVOID: "AVOID",
    FinalAction.INVALID: "NO DATA",
}
# Short tag and plain-language reason for a buy-tier call whose model has not
# earned a validated label. The tag always travels with the label.
_QUALIFIERS = {
    EvidenceStatus.UNTESTED: ("Unproven model", "the scoring model has not been tested on past data yet"),
    EvidenceStatus.PARTIAL: ("Partly tested model", "only parts of the scoring model have been tested on past data"),
    EvidenceStatus.NO_EDGE: ("No proven edge", "past-data tests found no reliable edge for this model"),
    EvidenceStatus.STALE: ("Validation out of date", "the model's last successful test is out of date"),
}


def stance_for(action: FinalAction) -> str:
    if action.allocates_new_capital:
        return "BULLISH"
    if action is FinalAction.AVOID:
        return "BEARISH"
    if action is FinalAction.INVALID:
        return "NONE"
    return "NEUTRAL"


def present_action(action: FinalAction, evidence: Evidence,
                   stance: Optional[str] = None) -> Presentation:
    """Turn a model action into what the user is shown.

    ``stance`` lets a caller keep a bearish view visible when the action was
    softened for display (AVOID -> WATCHLIST for someone who owns nothing).
    """
    stance = stance or stance_for(action)
    if action.allocates_new_capital:
        label = _VALIDATED_LABELS[action]
        if evidence.status.permits_recommendation:
            return Presentation(label, stance, "validated")
        qualifier, why = _QUALIFIERS[evidence.status]
        return Presentation(
            label, stance, "risk-budget-only",
            caveat=(
                f"{qualifier}: {why}, so treat this as a research idea, not a recommendation. "
                "Sizes shown are the most your risk budget allows, not advice to buy."
            ),
            qualifier=qualifier,
        )
    label = _NEUTRAL_LABELS.get(action, action.value)
    return Presentation(label, stance, "none")
