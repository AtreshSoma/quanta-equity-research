"""Truthful labelling for the backtest section.

The previous output advertised numbers like 'Annual Return 1291.9%' under
a 'Backtest' header. That is misleading because the simulation was
in-sample, not walk-forward, ignored slippage/commissions, used today's
weights on past data, and was not survivorship-bias-free.

This module exposes the standard banner / disclaimer text that both
scripts MUST use whenever they render those numbers.
"""
from __future__ import annotations

from typing import Iterable, List


BACKTEST_DIAGNOSTIC_BANNER = (
    "DIAGNOSTIC REPLAY — NOT A VALIDATED BACKTEST\n"
    "These numbers are a sanity-check using today's weights on past data. "
    "They are NOT evidence of strategy quality."
)


_DEFAULT_CAVEATS = (
    "in-sample (not out-of-sample)",
    "not walk-forward validated",
    "not survivorship-bias adjusted",
    "no slippage modelled",
    "no commissions modelled",
    "uses current weights on historical bars",
)


def backtest_disclaimer(extra_caveats: Iterable[str] = ()) -> str:
    """Return the full disclaimer block."""
    caveats: List[str] = list(_DEFAULT_CAVEATS) + list(extra_caveats)
    lines = [BACKTEST_DIAGNOSTIC_BANNER, "", "Caveats:"]
    lines.extend(f"  - {c}" for c in caveats)
    lines.append("")
    lines.append("Do not present these figures as predictive performance.")
    return "\n".join(lines)
