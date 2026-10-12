"""Allocation eligibility gate."""
from __future__ import annotations

from typing import List, Tuple

from .types import DecisionResult, FinalAction


def check_allocation_eligible(
    decision: DecisionResult,
    *,
    sector_exposure_pct: float = 0.0,
    sector_cap_pct: float = 0.25,
    portfolio_risk_pct: float = 0.0,
    portfolio_risk_cap_pct: float = 0.06,
) -> Tuple[bool, List[str]]:
    """Return (eligible, reasons_if_not).

    A ticker is eligible for capital ONLY when:
      * final_action.allocates_new_capital is True
      * sector exposure would remain below sector cap
      * portfolio risk would remain below risk cap
    """
    reasons: List[str] = []
    if not decision.final_action.allocates_new_capital:
        reasons.append(f"action {decision.final_action.value} does not allocate new capital")
        return False, reasons

    if sector_exposure_pct >= sector_cap_pct:
        reasons.append(
            f"sector exposure {sector_exposure_pct * 100:.1f}% >= cap {sector_cap_pct * 100:.1f}%"
        )

    if portfolio_risk_pct >= portfolio_risk_cap_pct:
        reasons.append(
            f"portfolio risk {portfolio_risk_pct * 100:.2f}% >= cap {portfolio_risk_cap_pct * 100:.2f}%"
        )

    return (len(reasons) == 0), reasons
