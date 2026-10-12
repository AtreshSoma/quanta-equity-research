"""Fixed liquid trading universe.

SURVIVORSHIP-BIAS CAVEAT (read this)
------------------------------------
This universe is a *static, present-day* list of large, liquid US equities. It
is NOT point-in-time index membership. Consequences you must keep in mind when
reading any backtest built on it:

  * Survivorship bias: names that were delisted, acquired, or went to zero
    before today are absent. The set is skewed toward survivors, which inflates
    historical returns versus a true as-of-date membership feed.
  * Look-ahead selection: these tickers were chosen *because* they are large and
    liquid today; some were small/illiquid years ago.
  * No reconstitution: real indices add/drop members over time. This list does
    not.

The honest fix is a point-in-time membership vendor (e.g. CRSP, Sharadar
SF1/Tickers with delisting flags). Until that is wired in via a provider, every
result produced on this universe must be reported WITH this caveat. Treat
absolute return numbers as optimistic; trust the *relative* comparisons
(strategy vs SPY vs random baselines) far more than the absolute equity curve.
"""

from __future__ import annotations

from typing import List

# Static, present-day liquid large-caps across sectors. Chosen for liquidity
# and long price history, NOT for expected performance.
LIQUID_UNIVERSE: List[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    # Semis / hardware
    "AVGO", "TXN", "QCOM", "INTC", "CSCO",
    # Consumer / retail
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "PG", "KO", "PEP",
    # Financials
    "JPM", "BAC", "GS", "V", "MA", "AXP",
    # Healthcare
    "JNJ", "UNH", "PFE", "MRK", "ABBV", "TMO",
    # Industrials / energy
    "CAT", "BA", "HON", "UNP", "XOM", "CVX",
    # Comms / media
    "DIS", "NFLX", "T", "VZ",
]

BENCHMARK = "SPY"


def get_universe() -> List[str]:
    """Return a copy of the fixed liquid universe."""
    return list(LIQUID_UNIVERSE)
