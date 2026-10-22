"""The one decision ladder both pages read (audit F7, section 6).

Picks used to apply its own book gates and My Portfolio its own holder logic, so
one ticker could read "Buy" on one page and "Hold, no new money" on the other
for reasons neither page showed. ``decide`` composes, in order:

1. the corporate-event gate (engine.corporate_events): a pending acquisition or
   delisting is AVOID; an unavailable check or a possible pending deal is never
   funded (fail closed);
2. the action the resolver already produced (score bands, price gates, red
   flags; engine.decision_engine.resolve_final_action);
3. the grade policy: F takes no new money (AVOID when a measured red flag
   corroborates it), D is allowed at the configured half size;
4. owner decision Q4: STRONG_BUY folds into BUY until the model's evidence
   status is VALIDATED;
5. for a holder: ``holder_rule`` (with the grade and the event), then the
   concentration trim at ``PORTFOLIO.TRIM_REVIEW_POSITION``.

Picks calls it with ``holding=None`` (the holder action is then conditional,
"if you hold it"); My Portfolio passes the position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .blockers import _EXIT_CORROBORATION, dedupe, holder_rule, new_money
from .thresholds import LADDER, PORTFOLIO, SIZING
from .types import FinalAction

_AVOID_EVENT_CODES = frozenset({"PENDING_ACQUISITION", "DELISTING"})


@dataclass
class LadderInputs:
    action: FinalAction                      # what the resolver / reconciliation produced
    composite_score: Optional[float]         # 0-100
    risk_grade: str = ""
    codes: List[str] = field(default_factory=list)
    evidence_status: str = "UNTESTED"        # engine.evidence status of the model behind the call
    event: Optional[Dict[str, Any]] = None   # EventCheck.to_dict()
    # Holder red flags measured at any score (risk_overrides.measured_red_flags):
    # they corroborate an exit review, never change the new-money action.
    red_flags: List[str] = field(default_factory=list)


@dataclass
class Holding:
    weight: Optional[float] = None           # fraction of the portfolio


@dataclass
class LadderDecision:
    action: FinalAction                      # new-money action
    new_money: str                           # ELIGIBLE | WATCH | AVOID | NONE
    holder_action: str                       # HOLD | TRIM | EXIT | N/A
    holder_basis: str
    codes: List[str]
    size_multiplier: float
    reasons: List[str] = field(default_factory=list)
    holder_codes: List[str] = field(default_factory=list)
    holder_flags: List[str] = field(default_factory=list)   # every measured red flag found


def size_multiplier(grade: str) -> float:
    return float(SIZING.RISK_GRADE_SIZE_MULTIPLIERS.get(str(grade or "").upper(),
                                                       SIZING.UNKNOWN_GRADE_SIZE_MULTIPLIER))


def decide(inputs: LadderInputs, holding: Optional[Holding] = None) -> LadderDecision:
    action = inputs.action
    codes = list(inputs.codes or [])
    reasons: List[str] = []
    grade = str(inputs.risk_grade or "").upper()
    ev = dict(inputs.event or {})

    # 1. corporate events
    ev_code = ev.get("code") or ""
    if ev.get("status") == "flagged" and ev_code in _AVOID_EVENT_CODES:
        action = FinalAction.AVOID
        codes.insert(0, ev_code)
        reasons.append(str(ev.get("text") or ev_code))
    elif ev.get("status") in ("flagged", "unavailable") and ev_code:
        codes.insert(0, ev_code)
        if action.allocates_new_capital:
            action = FinalAction.WATCHLIST
            reasons.append(str(ev.get("text") or ev_code))

    # 3. grade policy
    if grade == "F":
        codes.append("RISK_GRADE_F")
        if action.allocates_new_capital:
            action = FinalAction.WATCHLIST
            reasons.append("Risk grade F takes no new money")
        if action is FinalAction.WATCHLIST and set(codes) & _EXIT_CORROBORATION:
            action = FinalAction.AVOID
            reasons.append("Risk grade F with a measured red flag")

    # 4. owner decision Q4
    if action is FinalAction.STRONG_BUY and str(inputs.evidence_status).upper() != LADDER.STRONG_BUY_EVIDENCE:
        action = FinalAction.BUY
        codes.append("STRONG_BUY_FOLDED")
        reasons.append("Shown as Buy: a Strong Buy needs a validated model")

    codes = dedupe(codes)
    mult = size_multiplier(grade) if action.allocates_new_capital else 0.0
    if grade == "F":
        mult = 0.0

    # 5. holder
    rule = holder_rule(action, inputs.composite_score, codes, event=ev or None, red_flags=inputs.red_flags)
    holder, basis, holder_codes = str(rule["action"]), str(rule["basis"]), list(rule.get("codes") or [])
    if (holding is not None and holder == "HOLD" and holding.weight is not None
            and holding.weight >= PORTFOLIO.TRIM_REVIEW_POSITION):
        holder, basis, holder_codes = "TRIM", "CONCENTRATION", []

    return LadderDecision(action=action, new_money=new_money(action), holder_action=holder,
                          holder_basis=basis, codes=codes, size_multiplier=mult,
                          reasons=reasons, holder_codes=holder_codes,
                          holder_flags=list(rule.get("flags") or []))
