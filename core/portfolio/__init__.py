"""Broker-held portfolios, read-only, and the portfolio-context layer.

Layers, each kept separate so a reader can always tell what came from where:

    broker data      -> ``models`` (Portfolio / Position / TaxLot), filled by a
                        ``provider.PortfolioProvider`` (Robinhood today)
    market data      -> ``analytics`` inputs (returns, beta, earnings dates)
    QUANTA analysis  -> the authoritative per-ticker Decision, unchanged
    portfolio layer  -> ``guidance``: what the Decision means for THIS portfolio

The portfolio layer never re-scores a stock. It reads the Decision's own
``stance`` / ``new_money`` / ``holder_action`` and applies portfolio constraints
(size, sector, correlation, cash, event risk) that can only hold an action back,
never promote it.
"""
from .models import (
    AccountSummary,
    ConnectionState,
    DataIssue,
    Portfolio,
    Position,
    TaxLot,
)
from .provider import PortfolioProvider, ProviderError, AuthExpired, NotConnected

__all__ = [
    "AccountSummary",
    "ConnectionState",
    "DataIssue",
    "Portfolio",
    "Position",
    "TaxLot",
    "PortfolioProvider",
    "ProviderError",
    "AuthExpired",
    "NotConnected",
]
