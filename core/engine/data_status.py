"""Data-acquisition status taxonomy for the deep analyzer / reconciliation.

This module separates *why* the deep analyzer could not produce a verdict so the
UI never conflates a genuine "invalid ticker" with a transient data-provider
failure. A valid, listed symbol whose history could not be fetched (rate-limit,
empty payload, network hiccup) must NOT be labelled INVALID — it is simply
"not independently confirmed" and belongs on the watchlist, not in the bin.

These are plain string constants (not an Enum) so they serialise cleanly to JSON
and cross the SP_ELITE_PRO <-> TradingAnalysis_ELITE <-> webapp boundary without
import coupling.
"""
from __future__ import annotations


class DataStatus:
    """Single source of truth for data-acquisition / confirmation outcomes."""

    OK = "ok"
    # The symbol genuinely does not exist / is delisted with no metadata.
    INVALID_TICKER = "invalid_ticker"
    # The symbol is real but returned fewer than the required number of bars.
    INSUFFICIENT_HISTORY = "insufficient_history"
    # The provider throttled us (HTTP 429 / YFRateLimitError).
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    # The provider answered but with no usable data for a real symbol
    # (empty frame, network error, transient outage).
    PROVIDER_DATA_UNAVAILABLE = "provider_data_unavailable"
    # Provider returned bars, but the latest market bar is too old to support
    # a current recommendation.
    STALE_DATA = "stale_data"
    # The deep analyzer raised while confirming a scanner pick.
    DEEP_CONFIRMATION_FAILED = "deep_confirmation_failed"
    # Catch-all: scanner liked it but the deep analyzer could not validate it
    # for a non-bearish (data) reason.
    NOT_INDEPENDENTLY_CONFIRMED = "not_independently_confirmed"
    # A real, priced instrument that the equity research engine does not
    # model (index, future, currency pair, cryptocurrency, option).
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"


# Statuses that represent a *data/confirmation* problem rather than a bearish
# analyzer disagreement. Picks with these statuses move to the
# "Scanner Pick Not Confirmed" watchlist and receive NO allocation, but are NOT
# rendered as INVALID and NOT rendered as "analyzer disagrees".
DATA_FAILURE_STATUSES = frozenset({
    DataStatus.PROVIDER_RATE_LIMITED,
    DataStatus.PROVIDER_DATA_UNAVAILABLE,
    DataStatus.STALE_DATA,
    DataStatus.DEEP_CONFIRMATION_FAILED,
    DataStatus.NOT_INDEPENDENTLY_CONFIRMED,
    DataStatus.INSUFFICIENT_HISTORY,
})


# Human-readable labels for the UI "Validation Status" column.
DATA_STATUS_LABELS = {
    DataStatus.OK: "Confirmed",
    DataStatus.INVALID_TICKER: "Invalid ticker",
    DataStatus.INSUFFICIENT_HISTORY: "Insufficient history",
    DataStatus.PROVIDER_RATE_LIMITED: "Provider rate-limited",
    DataStatus.PROVIDER_DATA_UNAVAILABLE: "Data unavailable / provider issue",
    DataStatus.STALE_DATA: "Stale market data",
    DataStatus.DEEP_CONFIRMATION_FAILED: "Deep confirmation failed",
    DataStatus.NOT_INDEPENDENTLY_CONFIRMED: "Not independently confirmed",
    DataStatus.UNSUPPORTED_INSTRUMENT: "Unsupported instrument",
}


# The user-facing failure category of each non-OK status (engine.symbols).
FAILURE_CATEGORY = {
    DataStatus.INVALID_TICKER: "invalid_symbol",
    DataStatus.UNSUPPORTED_INSTRUMENT: "unsupported_instrument",
    DataStatus.INSUFFICIENT_HISTORY: "insufficient_data",
    DataStatus.STALE_DATA: "stale_data",
    DataStatus.PROVIDER_DATA_UNAVAILABLE: "provider_unavailable",
    DataStatus.PROVIDER_RATE_LIMITED: "temporary_failure",
    DataStatus.DEEP_CONFIRMATION_FAILED: "temporary_failure",
    DataStatus.NOT_INDEPENDENTLY_CONFIRMED: "provider_unavailable",
}


def is_data_failure(status: str) -> bool:
    """True when the status is a data/confirmation problem (not a real verdict)."""
    return str(status or "").strip().lower() in DATA_FAILURE_STATUSES


def is_true_invalid(status: str) -> bool:
    """True only when the ticker genuinely does not exist."""
    return str(status or "").strip().lower() == DataStatus.INVALID_TICKER


def status_label(status: str) -> str:
    """Human-readable label for the UI Validation Status column."""
    key = str(status or "").strip().lower()
    return DATA_STATUS_LABELS.get(key, key.replace("_", " ").title() or "Unknown")
