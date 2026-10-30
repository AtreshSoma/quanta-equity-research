"""The round-trip cost every reward/risk figure in QUANTA is measured after.

One definition, used by the Research engine (TradingAnalysis_ELITE), Plan Check
and the Plan Simulator. When each module kept its own copy, the same plan
could show a net R/R of 1.52 on one page and 1.51 on another. The cost is
charged to both legs as a fraction of the entry price:

* the spread, plus slippage on each side (thresholds.LEVELS), and
* for an NSE/BSE listing (.NS or .BO), the statutory levies on each side
  (thresholds.COSTS.INDIA_STATUTORY_COST_PER_SIDE).

Net reward/risk built on this cost is :func:`engine.levels.net_rr`.
"""
from __future__ import annotations

from .thresholds import LEVELS

INDIA_SUFFIXES = (".NS", ".BO")


def round_trip_cost_frac(ticker: str = "") -> float:
    """Round-trip cost as a fraction of price for ``ticker`` (0.0011 for a US name)."""
    k = (LEVELS.RESEARCH_SPREAD_BPS + 2 * LEVELS.RESEARCH_SLIPPAGE_BPS) / 10000.0
    if str(ticker or "").strip().upper().endswith(INDIA_SUFFIXES):
        k += 2.0 * float(LEVELS.RESEARCH_INDIA_STATUTORY_PER_SIDE)
    return k
