"""Pre-render cross-checks. Run this before printing any report.

Each rule returns either None (pass) or a short string describing the
inconsistency. The caller can decide to log / raise / abort rendering.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .dividend import SUPPRESS_THRESHOLD
from .types import FinalAction


@dataclass
class TickerView:
    """Lightweight view used purely by the consistency validator."""
    ticker: str
    summary_action: str            # FinalAction.value as shown in summary
    detail_action: str             # FinalAction.value as shown in detail
    risk_grade: str                # 'A'..'F' or ''
    current_price: float = 0.0
    stop_loss: float = 0.0
    target_price: float = 0.0
    risk_reward_ratio: float = 0.0
    allocation_amount: float = 0.0
    has_existing_position: bool = False
    dividend_yield_fraction: float = 0.0


@dataclass
class ConsistencyReport:
    ok: bool = True
    issues: List[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.ok = False
        self.issues.append(msg)


_BUY_ACTIONS = {FinalAction.STRONG_BUY.value, FinalAction.BUY.value, FinalAction.ACCUMULATE.value}


def _finite_pos(x) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


def validate_consistency(views: Iterable[TickerView], *, deployed: float = 0.0,
                         max_exposure: float = float("inf")) -> ConsistencyReport:
    rep = ConsistencyReport()
    for v in views:
        # rule: summary == detail
        if v.summary_action != v.detail_action:
            rep.add(f"{v.ticker}: summary={v.summary_action} but detail={v.detail_action}")

        # rule: invalid ⇒ no scores / setup
        if v.summary_action == FinalAction.INVALID.value:
            if _finite_pos(v.stop_loss) or _finite_pos(v.target_price) or v.allocation_amount > 0:
                rep.add(f"{v.ticker}: INVALID but has setup or allocation")
            continue

        # rule: BUY-like must have valid setup
        if v.summary_action in _BUY_ACTIONS:
            if not (_finite_pos(v.current_price) and _finite_pos(v.stop_loss) and _finite_pos(v.target_price)):
                rep.add(f"{v.ticker}: {v.summary_action} but missing price/stop/target")
            if v.stop_loss >= v.current_price:
                rep.add(f"{v.ticker}: {v.summary_action} but stop {v.stop_loss} >= entry {v.current_price}")
            if v.target_price <= v.current_price:
                rep.add(f"{v.ticker}: {v.summary_action} but target {v.target_price} <= entry {v.current_price}")
            if v.risk_grade == "F":
                rep.add(f"{v.ticker}: {v.summary_action} but risk grade F")
            if v.risk_grade == "D" and v.summary_action in (
                FinalAction.STRONG_BUY.value, FinalAction.BUY.value
            ):
                rep.add(
                    f"{v.ticker}: {v.summary_action} but risk grade D "
                    "(maximum allowed action is ACCUMULATE)"
                )
            if v.risk_reward_ratio < 1.5:
                rep.add(f"{v.ticker}: {v.summary_action} but R/R {v.risk_reward_ratio:.2f} < 1.5")

        # rule: HOLD_EXISTING requires existing position
        if v.summary_action == FinalAction.HOLD_EXISTING.value and not v.has_existing_position:
            rep.add(f"{v.ticker}: HOLD_EXISTING but user has no position (should be WATCHLIST)")

        # rule: non-allocating actions get no money
        if v.summary_action not in _BUY_ACTIONS and v.allocation_amount > 0:
            rep.add(
                f"{v.ticker}: {v.summary_action} but allocation_amount={v.allocation_amount}"
            )

        # rule: dividend sanity
        if v.dividend_yield_fraction > SUPPRESS_THRESHOLD:
            rep.add(
                f"{v.ticker}: dividend yield {v.dividend_yield_fraction * 100:.1f}% exceeds {SUPPRESS_THRESHOLD * 100:.0f}%"
            )

    if deployed > max_exposure:
        rep.add(f"deployed {deployed:.2f} exceeds max exposure {max_exposure:.2f}")

    return rep
