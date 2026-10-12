"""An after-hours scan read the regime as UNKNOWN (exposure capped at 60%).

Yahoo returns today's SPY bar with volume but no close once the session ends;
the NaN made both moving averages NaN and the trend check failed. A bar
without a close is now dropped before the trend is measured. Synthetic data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402


def _rising(n=300):
    idx = pd.bdate_range(end="2026-10-05", periods=n)
    close = 400 * np.exp(0.001 * np.arange(n))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                         "Volume": np.full(n, 5e7)}, index=idx)


def test_a_bar_without_a_close_does_not_hide_the_trend():
    hist = _rising()
    hist.iloc[-1, hist.columns.get_loc("Close")] = np.nan       # today's open bar after hours
    regime = sp.MarketRegimeDetector.detect_regime(vix=15.5, spy_hist=hist, market="US")
    assert regime.regime_type == "BULL"
    assert regime.recommended_exposure > sp._T.REGIME.EXPOSURE_CAP_UNKNOWN

