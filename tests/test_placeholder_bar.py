"""A provider placeholder bar must never present a live ticker as delisted.

Observed 2026-09-03: `yf.Ticker('NVDA').history(period='2y')` returned 502 rows
where the final row — the current session — carried NaN for every OHLC field.
`float(data['Close'].iloc[-1])` was therefore NaN, the hard validation gate
correctly rejected a non-finite price, and the tear sheet reported:

    NVDA: data unavailable
    No usable price or fundamentals were returned. The ticker may be
    delisted, suspended, or rate-limited.

...while `fast_info` reported a perfectly good 228.45 and 501 usable bars sat
behind the stub. The bug hit every symbol for as long as the feed emitted the
placeholder, so these tests pin the behaviour at the fetch boundary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import TradingAnalysis_ELITE as ta  # noqa: E402


def _frame(closes):
    """OHLC frame mirroring a yfinance daily history."""
    idx = pd.date_range("2026-08-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000_000 if c == c else np.nan for c in closes],
        },
        index=idx,
    )


def test_trailing_placeholder_bar_is_dropped():
    """The stub row goes; every real bar stays."""
    df = _frame([100.0, 101.0, 102.0, np.nan])
    cleaned = ta._drop_unusable_bars(df)
    assert len(cleaned) == 3
    assert float(cleaned["Close"].iloc[-1]) == pytest.approx(102.0)


def test_interior_gap_bar_is_dropped_too():
    """A NaN close mid-frame is just as unusable as one at the end."""
    df = _frame([100.0, np.nan, 102.0])
    cleaned = ta._drop_unusable_bars(df)
    assert len(cleaned) == 2
    assert list(cleaned["Close"]) == pytest.approx([100.0, 102.0])


def test_clean_frame_is_returned_untouched():
    """No copy, no reindex, no cost when there is nothing to clean."""
    df = _frame([100.0, 101.0])
    assert ta._drop_unusable_bars(df) is df


def test_all_nan_frame_becomes_empty_so_the_invalid_path_fires():
    """A frame with no usable close must NOT be smuggled through as data —
    the caller's no-data branch has to see it as empty."""
    df = _frame([np.nan, np.nan])
    assert len(ta._drop_unusable_bars(df)) == 0


@pytest.mark.parametrize("value", [None, pd.DataFrame()])
def test_degenerate_inputs_are_passed_through(value):
    """Never raise from the fetch boundary."""
    out = ta._drop_unusable_bars(value)
    assert out is None or len(out) == 0


def test_frame_without_close_column_is_untouched():
    df = pd.DataFrame({"Adj Close": [1.0, 2.0]})
    assert ta._drop_unusable_bars(df) is df


def test_validation_gate_would_have_rejected_the_stub_price():
    """Documents the failure this fix prevents: the gate is right to reject a
    NaN price — the defect was feeding it one, not the gate itself."""
    from engine.data_validator import validate_ticker_data

    stub = _frame([100.0, 101.0, np.nan])
    bad_price = float(stub["Close"].iloc[-1])          # what the old code read
    good_price = float(stub["Close"].dropna().iloc[-1])  # what it reads now

    rejected = validate_ticker_data(
        ticker="NVDA", current_price=bad_price, history_length=500,
        market_cap=1e12, company_name="NVIDIA Corporation",
    )
    assert not rejected.is_valid
    assert any("price" in r.lower() for r in rejected.reasons)

    accepted = validate_ticker_data(
        ticker="NVDA", current_price=good_price, history_length=500,
        market_cap=1e12, company_name="NVIDIA Corporation",
    )
    assert accepted.is_valid
