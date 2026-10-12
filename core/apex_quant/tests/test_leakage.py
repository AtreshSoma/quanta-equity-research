"""Leakage test: prove no future data reaches a past as-of decision.

A synthetic, offline provider is used so the test is deterministic and needs no
network. The contract under test is the point-in-time slicing in
``DataProvider.get_history``: the bars visible on date D must be exactly the
prefix of the bars visible on any later date D2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.apex_quant.data_provider import DataProvider, DataQualityError


class _SyntheticProvider(DataProvider):
    """Holds a fixed full history in memory and serves PIT slices."""

    def __init__(self, full: pd.DataFrame):
        self._full = full

    def get_full(self, ticker: str) -> pd.DataFrame:
        return self._full

    def get_history(self, ticker, as_of, min_bars=10):
        as_of = pd.Timestamp(as_of).normalize()
        hist = self._full.loc[self._full.index <= as_of]
        if len(hist) < min_bars:
            raise DataQualityError("insufficient bars")
        return hist


def _make_history(n=400, seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    price = 100 + np.cumsum(rng.normal(0, 1, n))
    price = np.maximum(price, 1.0)
    return pd.DataFrame(
        {
            "Open": price,
            "High": price + np.abs(rng.normal(0, 0.5, n)),
            "Low": price - np.abs(rng.normal(0, 0.5, n)),
            "Close": price,
            "Volume": rng.integers(1_000_000, 5_000_000, n),
        },
        index=idx,
    )


def test_no_future_bars_visible():
    full = _make_history()
    provider = _SyntheticProvider(full)
    as_of = full.index[200]
    hist = provider.get_history("TEST", as_of, min_bars=50)
    assert hist.index.max() <= as_of
    # No bar strictly after as_of should appear.
    assert not (hist.index > as_of).any()


def test_past_is_prefix_of_future():
    full = _make_history()
    provider = _SyntheticProvider(full)
    early = full.index[150]
    late = full.index[300]
    h_early = provider.get_history("TEST", early, min_bars=50)
    h_late = provider.get_history("TEST", late, min_bars=50)
    # The earlier view must be an exact prefix of the later view.
    assert len(h_early) < len(h_late)
    pd.testing.assert_frame_equal(h_late.iloc[: len(h_early)], h_early)


def test_quality_gate_excludes_thin_history():
    full = _make_history(n=20)
    provider = _SyntheticProvider(full)
    with pytest.raises(DataQualityError):
        provider.get_history("TEST", full.index[-1], min_bars=250)
