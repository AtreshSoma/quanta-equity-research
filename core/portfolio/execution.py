"""Future execution layer. DISABLED in this phase.

The intended flow, when a later phase builds it, is:

    guidance -> OrderIntent (portfolio-impact preview)
             -> broker order REVIEW (e.g. Robinhood ``review_equity_order``)
             -> explicit, per-order user confirmation in the UI
             -> broker order PLACEMENT (``place_equity_order``)

It is kept apart from the read-only research path on purpose:

* the research path's MCP client cannot call order tools at all
  (``brokers.mcp_http`` refuses write-shaped tool names regardless of its
  allowlist), so enabling execution requires a separate client here;
* ``EXECUTION_ENABLED`` is a module constant, not configuration: turning it on
  is a code change that goes through review, never an environment variable or
  a request parameter;
* Robinhood states an agent instructed to act autonomously can trade without
  confirmation, so any implementation must require a confirmation token that
  only a human action in the UI can produce, bound to the exact reviewed order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

EXECUTION_ENABLED = False


class ExecutionDisabled(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Order execution is disabled in QUANTA. My Portfolio is analysis-only.")


@dataclass(frozen=True)
class OrderIntent:
    """What a user might do, for impact preview only. Never sent anywhere."""
    symbol: str
    side: str                     # "buy" | "sell"
    quantity: Optional[float] = None
    notional: Optional[float] = None
    reason: str = ""


class ExecutionGateway:
    """Placeholder for review -> confirm -> place. Every method refuses."""

    def review(self, intent: OrderIntent):
        raise ExecutionDisabled()

    def place(self, intent: OrderIntent, confirmation_token: str):
        raise ExecutionDisabled()
