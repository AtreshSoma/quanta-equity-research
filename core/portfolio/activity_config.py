"""Activity tab settings: the single source for every limit, label and window it uses.

Served with ``/api/portfolio/records`` as ``config``; the page draws nothing that is
not here or in the loaded data. Change a value here, then regenerate the activity
fixtures (``tests/tools/make_activity_fixtures.py``) so the JS tests see it.
"""
from __future__ import annotations

from typing import Any, Dict

ACTIVITY: Dict[str, Any] = {
    # Range chips, in order. ``back`` is how far before today (New York date) the
    # window starts, inclusive; ``year_start`` starts on January 1; ``None`` is all
    # loaded history. ``span`` is the Robinhood realized-P&L window cross-checked.
    "ranges": [
        {"key": "day", "label": "Day", "long": "today", "back": {"unit": "day", "n": 0}, "span": "day"},
        {"key": "week", "label": "Week", "long": "the past week", "back": {"unit": "day", "n": 7}, "span": "week"},
        {"key": "month", "label": "Month", "long": "the past month", "back": {"unit": "month", "n": 1}, "span": "month"},
        {"key": "3month", "label": "3M", "long": "the past 3 months", "back": {"unit": "month", "n": 3}, "span": "3month"},
        {"key": "ytd", "label": "YTD", "long": "this year to date", "back": {"unit": "year_start", "n": 0}, "span": "ytd"},
        {"key": "year", "label": "1Y", "long": "the past year", "back": {"unit": "year", "n": 1}, "span": "year"},
        {"key": "all", "label": "All", "long": "all loaded history", "back": None, "span": "all"},
    ],
    "default_range": "month",
    "views": [
        # value/count: the ranking's column labels.
        {"key": "sales", "label": "Closing trades", "noun": ["closing trade", "closing trades"], "value": "Realized", "count": "Trades"},
        {"key": "buys", "label": "Purchases", "noun": ["purchase", "purchases"], "value": "Amount", "count": "Purchases"},
        {"key": "lots", "label": "Open lots", "noun": ["open lot", "open lots"], "value": "Unrealized", "count": "Lots"},
    ],
    "default_view": "sales",
    # Chart granularity: the finest whose bucket count fits ``max_buckets`` is chosen.
    "granularities": [
        {"key": "day", "label": "Day", "noun": ["day", "days"]},
        {"key": "week", "label": "Week", "noun": ["week", "weeks"]},
        {"key": "month", "label": "Month", "noun": ["month", "months"]},
    ],
    "max_buckets": 45,
    # Sparse rows: when the finest fitting granularity averages fewer rows per bucket
    # than this, the chart opens on ``use`` instead (if that fits max_buckets).
    "sparse": {"rows_per_bucket": 0.5, "use": "month"},
    "max_axis_labels": 8,
    "y_ticks": 4,
    # Rows per table page; "Show more" appears only past this.
    "page_size": 50,
    # Ranking: this many winners and this many losers, the rest folded into one row.
    "ranking_ends": 6,
    # Open lots by age (days since acquired), upper bounds exclusive; the last is open-ended.
    "lot_ages": [
        {"key": "m1", "label": "Under 1 month", "max_days": 30},
        {"key": "m3", "label": "1 to 3 months", "max_days": 91},
        {"key": "y1", "label": "3 to 12 months", "max_days": 365},
        {"key": "y1p", "label": "Over 1 year", "max_days": None},
    ],
    # Lots Robinhood reports without an acquisition date: their own bucket, never guessed.
    "lot_age_unknown": {"key": "nodate", "label": "Date not reported"},
    "decimals": {"shares": 4, "currency": 2, "percent": 2},
    "timezone": "America/New_York",
    # How much history the server reads and passes on. A source cut by a cap is
    # reported as capped, with its real date range, never as complete.
    "caps": {"trades": 2000, "fills": 400, "pages": 20},
    # A closing-trade record and the execution behind it are the same sale when the
    # symbol and side agree and their times are within this many seconds (the broker
    # stamps the two records separately). Used to classify trades by asset and to
    # avoid estimating a crypto sale the broker already reports.
    "evidence_window_s": 60,
}


def range_spans() -> tuple:
    """Robinhood realized-P&L windows the ranges cross-check against."""
    return tuple(r["span"] for r in ACTIVITY["ranges"])
