"""Hard pre-scoring data-validation gate.

If this gate fails, NO scores, signals, stops, targets or allocations
should be produced. Both scripts MUST consult this gate before rendering.

ETF awareness: ETFs and mutual funds report `totalAssets` rather than
`marketCap`, do not have a `longName`/`shortName` distinct from the
ticker for some niche funds, and need fewer history bars to be tradable.
Pass `quote_type` (from yfinance `info['quoteType']`) or `is_etf=True`
to relax those checks appropriately.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .types import ValidationReport


_ETF_LIKE_QUOTE_TYPES = {"ETF", "MUTUALFUND", "INDEX", "CLOSED-END-FUND", "CEF"}


def _is_finite_positive(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


def is_etf_quote_type(quote_type: Optional[str]) -> bool:
    return bool(quote_type) and quote_type.strip().upper() in _ETF_LIKE_QUOTE_TYPES


def validate_ticker_data(
    ticker: str,
    current_price: Any,
    history_length: int,
    market_cap: Any,
    company_name: Optional[str],
    min_history: int = 50,
    quote_type: Optional[str] = None,
    is_etf: bool = False,
    net_assets: Any = None,
) -> ValidationReport:
    """Run the hard gate.

    Returns ValidationReport with .is_valid set to True only if ALL of:
      * ticker is a non-empty string
      * current_price is finite and > 0
      * history_length >= min_history (relaxed to max(20, min_history//2) for ETFs/funds)
      * company_name is not empty (ETFs may legitimately have a short name)

    ETFs/funds are flagged via ``quote_type`` (preferred) or ``is_etf``.
    """
    rep = ValidationReport(ticker=(ticker or "").upper())

    if not ticker or not isinstance(ticker, str) or not ticker.strip():
        return rep.fail("Empty ticker symbol")

    etf_like = bool(is_etf) or is_etf_quote_type(quote_type)
    rep.is_etf = etf_like
    rep.quote_type = (quote_type or "").strip().upper()

    rep.has_price = _is_finite_positive(current_price)
    if not rep.has_price:
        rep.fail(f"No valid price (got {current_price!r})")

    effective_min_history = max(20, min_history // 2) if etf_like else min_history
    rep.has_history = isinstance(history_length, int) and history_length >= effective_min_history
    if not rep.has_history:
        rep.fail(f"Insufficient history ({history_length} bars, need ≥{effective_min_history})")

    # ETFs report totalAssets / net_assets; fall back to that. A missing size
    # field is recorded, not treated as proof the instrument is fake: Yahoo
    # omits it for real instruments (NIFTYBEES.NS, some ETFs and funds) whose
    # name, price and history are all present. Those three decide validity.
    rep.has_market_cap = _is_finite_positive(market_cap) or (etf_like and _is_finite_positive(net_assets))

    # For ETFs a short name equal to the ticker is acceptable (some niche
    # funds report `shortName == ticker`).
    if etf_like:
        name_ok = bool(company_name) and bool(company_name.strip())
    else:
        name_ok = bool(company_name) and company_name.strip().upper() != rep.ticker
    rep.has_company_name = name_ok
    if not name_ok:
        rep.fail("Unknown company name")

    rep.is_valid = rep.has_price and rep.has_history and rep.has_company_name
    return rep
