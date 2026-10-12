"""
data_quality.py — Sanitize raw fundamentals from yfinance/Polygon/FMP.

WHY: Vendors return artifacts — BUSE reported "Revenue +143% YoY" in our
27-May-26 run because of an acquisition-period mismatch. Feeding that to a
scorer overweights the stock and corrupts the thesis ("Revenue +143% YoY").

Strategy:
1. Bound each metric to a sector-aware sensible range.
2. Flag (not silently drop) clipped values so the UI can surface them.
3. Provide a single `sanitize_fundamentals(metrics, sector)` entry point.

The sanitizer is intentionally CONSERVATIVE — when in doubt, replace with 0.0
(neutral) rather than fabricate a value. Returns a report listing every clip
so the caller can show a "Data Quality" badge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Plausible upper/lower bounds for each metric, by broad sector.
# Bounds chosen from S&P 500 long-run distributions (10th–90th pct + slack).
# Anything outside is almost certainly a vendor artifact.
_SECTOR_BOUNDS: Dict[str, Dict[str, Tuple[float, float]]] = {
    "default": {
        "revenue_growth":     (-0.50, 0.60),   # -50% .. +60% YoY
        "earnings_growth":    (-1.00, 1.50),
        "revenue_growth_3y":  (-0.30, 0.50),
        "earnings_growth_3y": (-0.50, 1.00),
        "pe_ratio":           (0.0, 200.0),
        "forward_pe":         (0.0, 200.0),
        "pb_ratio":           (0.0, 50.0),
        "ps_ratio":           (0.0, 100.0),
        "ev_ebitda":          (-50.0, 200.0),
        "peg_ratio":          (-10.0, 20.0),
        "roe":                (-2.0, 2.0),     # -200% .. +200%
        "roa":                (-1.0, 1.0),
        "roic":               (-2.0, 2.0),
        "gross_margin":       (-1.0, 1.0),
        "operating_margin":   (-2.0, 1.0),
        "profit_margin":      (-2.0, 1.0),
        "ebitda_margin":      (-2.0, 1.0),
        "fcf_margin":         (-2.0, 1.0),
        "debt_to_equity":     (0.0, 20.0),
        "current_ratio":      (0.0, 20.0),
        "quick_ratio":        (0.0, 20.0),
        "interest_coverage":  (-100.0, 500.0),
        "dividend_yield":     (0.0, 0.20),     # 20% is already extreme
        "payout_ratio":       (0.0, 3.0),
    },
    # Banks legitimately have higher D/E and lower margins
    "Financial Services": {
        "debt_to_equity":     (0.0, 30.0),
        "operating_margin":   (-1.0, 0.70),
        "profit_margin":      (-1.0, 0.50),
        "revenue_growth":     (-0.40, 0.45),  # tighter — no acquisitions inflate
    },
    "Financials": {  # alias
        "debt_to_equity":     (0.0, 30.0),
        "revenue_growth":     (-0.40, 0.45),
    },
    # Tech / SaaS can show high growth and low/negative margins
    "Technology": {
        "revenue_growth":     (-0.40, 1.50),
        "operating_margin":   (-2.0, 0.60),
        "ps_ratio":           (0.0, 150.0),
    },
    # Energy: cyclical, very wide range
    "Energy": {
        "revenue_growth":     (-0.70, 1.50),
        "earnings_growth":    (-2.0, 5.0),
    },
}


def _bounds_for(sector: str, metric: str) -> Optional[Tuple[float, float]]:
    sec = (sector or "").strip()
    table = _SECTOR_BOUNDS.get(sec) or {}
    if metric in table:
        return table[metric]
    return _SECTOR_BOUNDS["default"].get(metric)


@dataclass
class SanitizationReport:
    """Per-ticker record of which fields were clipped / nulled."""
    ticker: str = ""
    sector: str = ""
    clipped: List[Tuple[str, float, float]] = field(default_factory=list)
    # (field_name, original_value, replacement_value)
    nulled: List[Tuple[str, float]] = field(default_factory=list)

    @property
    def is_suspect(self) -> bool:
        return bool(self.clipped or self.nulled)

    @property
    def severity(self) -> str:
        n = len(self.clipped) + 2 * len(self.nulled)
        if n == 0:
            return "clean"
        if n <= 1:
            return "minor"
        if n <= 3:
            return "moderate"
        return "severe"

    def summary(self) -> str:
        if not self.is_suspect:
            return "Data quality: clean."
        parts = []
        for name, orig, repl in self.clipped:
            parts.append(f"{name} clipped {orig:.2g}→{repl:.2g}")
        for name, orig in self.nulled:
            parts.append(f"{name} nulled (was {orig:.2g})")
        return f"Data quality [{self.severity}]: " + "; ".join(parts)


class FundamentalSanitizer:
    """Apply sector-aware bounds to a FundamentalMetrics-like object."""

    # Fields that should be hard-nulled (set to 0) when out of bounds,
    # because clipping would still mislead downstream scorers (e.g. a fake
    # 60% growth is misleading even after being clipped from 143%).
    HARD_NULL = {
        "revenue_growth", "earnings_growth",
        "revenue_growth_3y", "earnings_growth_3y",
    }

    def __init__(self, sector: str = "", ticker: str = ""):
        self.sector = sector or ""
        self.ticker = ticker or ""
        self.report = SanitizationReport(ticker=self.ticker, sector=self.sector)

    def _check_one(self, name: str, value: Any) -> Any:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return value
        if v != v:  # NaN
            return 0.0
        bounds = _bounds_for(self.sector, name)
        if bounds is None:
            return v
        lo, hi = bounds
        if lo <= v <= hi:
            return v
        if name in self.HARD_NULL:
            # Suspect growth artifact — refuse to use it.
            self.report.nulled.append((name, v))
            return 0.0
        clipped = max(lo, min(hi, v))
        self.report.clipped.append((name, v, clipped))
        return clipped

    def apply(self, metrics: Any) -> Tuple[Any, SanitizationReport]:
        """
        Mutate metrics in place (and return it) with all numeric fields
        validated against sector-aware bounds. Non-numeric / unknown fields
        are left untouched.
        """
        # Iterate over known field names (works for dataclass or dict).
        if hasattr(metrics, "__dict__"):
            fields_iter = list(vars(metrics).keys())
            getter = lambda k: getattr(metrics, k)
            setter = lambda k, v: setattr(metrics, k, v)
        elif isinstance(metrics, dict):
            fields_iter = list(metrics.keys())
            getter = lambda k: metrics[k]
            setter = lambda k, v: metrics.__setitem__(k, v)
        else:
            return metrics, self.report

        for fname in fields_iter:
            new_v = self._check_one(fname, getter(fname))
            if new_v is not getter(fname):
                try:
                    setter(fname, new_v)
                except Exception:
                    pass
        return metrics, self.report


def sanitize_fundamentals(
    metrics: Any,
    sector: str = "",
    ticker: str = "",
) -> Tuple[Any, SanitizationReport]:
    """
    Convenience wrapper. Returns (sanitized_metrics, report).

    Safe to call on any object — if the input has no numeric attributes the
    report comes back empty and the object is returned unchanged.
    """
    return FundamentalSanitizer(sector=sector, ticker=ticker).apply(metrics)
