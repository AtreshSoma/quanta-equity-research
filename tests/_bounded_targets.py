"""Child-process targets for engine.bounded tests (A01). Synthetic data only, no network."""
from __future__ import annotations

import time
from pathlib import Path


def refuse(*args, **kwargs):
    raise RuntimeError("network forbidden in tests: set QUANTA_BULK_TARGET to a synthetic target")


def slow(tickers=(), period="", threads=1, seconds=30.0, marker=""):
    if marker:
        Path(marker + ".started").write_text("1")
    time.sleep(float(seconds))
    if marker:
        Path(marker + ".finished").write_text("1")
    return None


def boom(*args, **kwargs):
    raise RuntimeError("429 Too Many Requests")


def empty(*args, **kwargs):
    import pandas as pd
    return pd.DataFrame()


def frame(tickers=(), period="", threads=1):
    """A MultiIndex bulk frame: liquidity rises with the ticker's index."""
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range(end="2026-10-02", periods=40)
    parts = {}
    for i, t in enumerate(tickers):
        parts[t] = pd.DataFrame({"Open": 50.0, "High": 51.0, "Low": 49.0, "Close": np.full(40, 50.0),
                                 "Volume": np.full(40, 1_000_000.0 + 10_000.0 * i)}, index=idx)
    return pd.concat(parts, axis=1)


def trend_frame(tickers=(), period="", threads=1):
    """260 sessions: even-indexed names in a steady uptrend (they pass the
    momentum screen), odd-indexed names flat; liquidity rises with the index."""
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range(end="2026-10-02", periods=260)
    parts = {}
    for i, t in enumerate(tickers):
        close = np.linspace(40.0, 80.0, 260) if i % 2 == 0 else np.full(260, 50.0)
        parts[t] = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close,
                                 "Volume": np.full(260, 1_000_000.0 + 10_000.0 * i)}, index=idx)
    return pd.concat(parts, axis=1)
