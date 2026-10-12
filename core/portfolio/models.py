"""Normalized, broker-neutral portfolio models.

Every numeric field a broker might not supply is ``Optional``. A missing value
stays ``None`` and is listed in ``missing``; nothing downstream substitutes a
default (a missing cost basis must never become a 0% gain). Account numbers
never enter these models: an account is addressed by an opaque ``handle`` the
provider maps back to the real identifier server-side.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"          # never connected, or user disconnected
    AWAITING_AUTH = "awaiting_auth"        # OAuth started, browser sign-in pending
    CONNECTED = "connected"                # authenticated; last read succeeded
    AUTH_EXPIRED = "auth_expired"          # token rejected / refresh failed
    PARTIAL = "partial"                    # authenticated, but some reads failed
    PROVIDER_ERROR = "provider_error"      # broker/MCP unreachable or malformed

    @property
    def label(self) -> str:
        return _STATE_LABELS[self]


_STATE_LABELS = {
    ConnectionState.DISCONNECTED: "Disconnected",
    ConnectionState.AWAITING_AUTH: "Waiting for Robinhood sign-in",
    ConnectionState.CONNECTED: "Connected (read-only)",
    ConnectionState.AUTH_EXPIRED: "Authentication expired",
    ConnectionState.PARTIAL: "Partial data",
    ConnectionState.PROVIDER_ERROR: "Provider error",
}


def finite(value: Any) -> Optional[float]:
    """``float(value)`` when it is a real finite number, else None.

    Broker payloads carry numbers as strings ("12.3400"); empty strings, None,
    "NaN" and garbage all become None rather than 0.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


@dataclass
class DataIssue:
    code: str                  # stable, e.g. COST_BASIS_MISSING, QUOTE_STALE
    message: str               # plain statement for the UI
    scope: str = "portfolio"   # "portfolio" | "account" | a ticker
    severity: str = "warning"  # "info" | "warning" | "error"

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class TaxLot:
    quantity: float
    cost_per_share: Optional[float] = None
    acquired_on: Optional[str] = None      # ISO date
    term: Optional[str] = None             # "short" | "long" | None (unknown)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    quantity: float
    name: str = ""
    instrument_type: str = "equity"
    average_cost: Optional[float] = None       # per share, as the broker reports it
    last_price: Optional[float] = None
    previous_close: Optional[float] = None
    quote_as_of: Optional[str] = None          # ISO timestamp of the quote itself
    quote_source: str = ""                     # "broker" | "market-data"
    tax_lots: Optional[List[TaxLot]] = None    # None = not retrieved; [] = none reported
    # Reference data the broker also serves (fundamentals / earnings calendar).
    sector: str = ""
    industry: str = ""
    earnings_date: Optional[str] = None
    # Calendar facts the broker states about that report: has the company confirmed
    # the date (False = tentative), before/after the market, and the consensus EPS.
    earnings_verified: Optional[bool] = None
    earnings_timing: Optional[str] = None          # "am" | "pm" | None (not yet known)
    eps_estimate: Optional[float] = None
    # Declared dividend schedule (fundamentals). Absent for non-payers and for unknowns.
    ex_dividend_date: Optional[str] = None
    payable_date: Optional[str] = None
    dividend_per_share: Optional[float] = None
    distribution_frequency: Optional[str] = None
    missing: List[str] = field(default_factory=list)

    @property
    def market_value(self) -> Optional[float]:
        if self.last_price is None:
            return None
        return self.quantity * self.last_price

    @property
    def cost_basis(self) -> Optional[float]:
        if self.average_cost is None:
            return None
        return self.quantity * self.average_cost

    @property
    def unrealized_pnl(self) -> Optional[float]:
        mv, cb = self.market_value, self.cost_basis
        if mv is None or cb is None:
            return None
        return mv - cb

    @property
    def unrealized_pnl_pct(self) -> Optional[float]:
        pnl, cb = self.unrealized_pnl, self.cost_basis
        if pnl is None or not cb:
            return None
        return pnl / cb * 100.0

    @property
    def day_change(self) -> Optional[float]:
        if self.last_price is None or self.previous_close is None:
            return None
        return self.quantity * (self.last_price - self.previous_close)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "instrument_type": self.instrument_type,
            "quantity": self.quantity,
            "average_cost": self.average_cost,
            "cost_basis": self.cost_basis,
            "last_price": self.last_price,
            "previous_close": self.previous_close,
            "market_value": self.market_value,
            "day_change": self.day_change,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "quote_as_of": self.quote_as_of,
            "quote_source": self.quote_source,
            "tax_lots": None if self.tax_lots is None else [lot.to_dict() for lot in self.tax_lots],
            "sector": self.sector,
            "industry": self.industry,
            "earnings_date": self.earnings_date,
            "earnings_verified": self.earnings_verified,
            "earnings_timing": self.earnings_timing,
            "eps_estimate": self.eps_estimate,
            "ex_dividend_date": self.ex_dividend_date,
            "payable_date": self.payable_date,
            "dividend_per_share": self.dividend_per_share,
            "distribution_frequency": self.distribution_frequency,
            "missing": list(self.missing),
        }


@dataclass
class AccountSummary:
    handle: str                                  # opaque; never the account number
    label: str                                   # nickname / type, no digits
    account_type: str = ""
    portfolio_value: Optional[float] = None      # broker-reported total (equity + cash)
    cash: Optional[float] = None
    buying_power: Optional[float] = None
    realized_pnl: Optional[float] = None
    realized_pnl_period: str = ""                # e.g. "YTD" — as the broker labels it
    can_trade_via_agent: Optional[bool] = None   # informational; execution is disabled

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Portfolio:
    provider: str                                # "robinhood"
    account: AccountSummary
    positions: List[Position]
    retrieved_at: str                            # when WE read it (UTC ISO)
    issues: List[DataIssue] = field(default_factory=list)

    @property
    def positions_value(self) -> Optional[float]:
        """Sum of priced positions; None when any position lacks a price."""
        values = [p.market_value for p in self.positions]
        if any(v is None for v in values):
            return None
        return float(sum(values))

    def reconciliation(self) -> Dict[str, Any]:
        """Do our position values plus cash add up to the broker's total?

        This is the live check that the normalizer read the payload the way the
        broker meant it. A mismatch is reported, never silently corrected.
        """
        reported = self.account.portfolio_value
        pos = self.positions_value
        cash = self.account.cash
        if reported is None or pos is None or cash is None:
            return {"status": "unverifiable", "reported": reported,
                    "computed": None, "difference": None,
                    "note": "Broker total, cash or a position price is missing."}
        computed = pos + cash
        diff = computed - reported
        pct = abs(diff) / reported * 100.0 if reported else (0.0 if abs(diff) < 0.01 else 100.0)
        # Half a cent per independently rounded holding, cash and total;
        # a percentage tolerance could hide hundreds of dollars of missing data.
        tolerance = max(0.01, (len(self.positions) + 2) * 0.005)
        matches = abs(diff) <= tolerance + 1e-9
        return {"status": "ok" if matches else "mismatch",
                "reported": reported, "computed": computed, "difference": diff,
                "difference_pct": pct,
                "note": "" if matches else
                "Positions plus cash do not add up to Robinhood's reported total."}
