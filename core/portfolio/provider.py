"""The read-only broker interface QUANTA depends on.

A broker integration implements :class:`PortfolioProvider` and nothing else in
QUANTA knows which broker it is talking to. The interface has no order methods
on purpose: execution, if it is ever built, lives behind a separate gateway
(``portfolio.execution``) with its own confirmation step.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .models import AccountSummary, ConnectionState, Portfolio


class ProviderError(RuntimeError):
    """The broker could not be read. ``code`` is stable; the message is safe to show."""

    def __init__(self, message: str, code: str = "PROVIDER_ERROR"):
        super().__init__(message)
        self.code = code


class NotConnected(ProviderError):
    def __init__(self, message: str = "Not connected to the broker."):
        super().__init__(message, "NOT_CONNECTED")


class AuthExpired(ProviderError):
    def __init__(self, message: str = "Broker authentication expired. Reconnect to continue."):
        super().__init__(message, "AUTH_EXPIRED")


class PortfolioProvider(ABC):
    """Read-only access to one user's brokerage data."""

    name: str = ""
    display_name: str = ""

    @abstractmethod
    def state(self) -> ConnectionState: ...

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """Connection status safe to send to the browser (no secrets, no ids)."""

    @abstractmethod
    def begin_connect(self, redirect_uri: str) -> str:
        """Start sign-in; return the URL the user must open."""

    @abstractmethod
    def complete_connect(self, params: Dict[str, str]) -> None:
        """Finish sign-in from the broker's redirect parameters."""

    @abstractmethod
    def disconnect(self) -> None:
        """Forget every credential this process holds."""

    @abstractmethod
    def list_accounts(self) -> List[AccountSummary]: ...

    @abstractmethod
    def load_portfolio(self, account_handle: str) -> Portfolio:
        """Read the account. Raise ProviderError subclasses; never return sample data."""

    def load_realized_series(self, account_handle: str, period: str) -> Dict[str, Any]:
        """Realized profit and loss for one window, bucketed. Optional."""
        raise ProviderError("This provider offers no realized profit-and-loss history.", "TOOL_UNAVAILABLE")

    def load_history(self, account_handle: str) -> Dict[str, Any]:
        """Order and closed-trade records. Optional: a provider without them reports so."""
        return {"orders": None, "trades": None,
                "issues": [{"code": "HISTORY_NOT_SUPPORTED", "message": "This provider offers no record history."}]}
