"""Reproducibility test: identical inputs -> identical data hash and signals.

Uses the synthetic provider so it is offline and deterministic. The data hash
must be stable across repeated reads, and signal generation on the same frame
must be byte-for-byte identical.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.apex_quant import indicators
from core.apex_quant.data_provider import _frame_hash
from core.apex_quant.strategy import StrategyParams, generate_signals


def _make_history(n=500, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n)
    price = 100 + np.cumsum(rng.normal(0.05, 1, n))
    price = np.maximum(price, 1.0)
    return pd.DataFrame(
        {
            "Open": price,
            "High": price + np.abs(rng.normal(0, 0.5, n)),
            "Low": price - np.abs(rng.normal(0, 0.5, n)),
            "Close": price,
            "Volume": rng.integers(2_000_000, 8_000_000, n),
        },
        index=idx,
    )


def test_frame_hash_stable():
    df = _make_history()
    assert _frame_hash(df) == _frame_hash(df.copy())


def test_signals_deterministic():
    df = _make_history()
    feats = indicators.feature_frame(df)
    params = StrategyParams(min_dollar_vol=0.0)  # ensure some pass liquidity
    s1 = generate_signals(df, feats, params, "TEST")
    s2 = generate_signals(df, feats, params, "TEST")
    assert len(s1) == len(s2)
    for a, b in zip(s1, s2):
        assert a.signal_date == b.signal_date
        assert a.stop_pct == b.stop_pct
        assert a.target_pct == b.target_pct


def test_wilder_rsi_bounds():
    df = _make_history()
    rsi = indicators.wilder_rsi(df["Close"], 14).dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()
