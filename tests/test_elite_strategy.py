"""Guards for the ELITE-composite strategy adapter and the pluggable harness.

Two things must hold for the walk-forward result to mean anything:

  1. The adapter honours the `Signal` contract the simulator and the
     random-entry / random-exit baselines depend on. If `EliteParams` stopped
     exposing `atr_stop_mult`, the baselines would silently fail and the
     strategy would be compared against nothing.
  2. Making `walkforward` pluggable did not change its default behaviour. The
     original APEX strategy must still be selected when no hooks are passed,
     or the refactor invalidated the existing validation artifact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _paths import ensure_paths  # noqa: E402

ensure_paths()

from apex_quant import baselines, indicators, walkforward  # noqa: E402
from apex_quant.elite_strategy import (  # noqa: E402
    STABILITY_KEYS,
    EliteParams,
    feature_frame_from_panel,
    generate_elite_signals,
)
from apex_quant.strategy import StrategyParams  # noqa: E402


def _ohlcv(n: int = 320, seed: int = 4) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.011, n)))
    spread = close * 0.008
    frame = pd.DataFrame(
        {
            "Open": close - spread * rng.uniform(-1, 1, n),
            "High": close + spread * rng.uniform(0.4, 1.3, n),
            "Low": close - spread * rng.uniform(0.4, 1.3, n),
            "Close": close,
            "Volume": rng.integers(8_000_000, 30_000_000, n).astype(float),
        },
        index=idx,
    )
    frame["High"] = frame[["Open", "High", "Close"]].max(axis=1)
    frame["Low"] = frame[["Open", "Low", "Close"]].min(axis=1)
    return frame


def _panel(ohlcv: pd.DataFrame, ticker: str, composites) -> pd.DataFrame:
    """A minimal panel: composite on every 5th bar, as the real one is."""
    dates = ohlcv.index[::5]
    values = list(composites)[: len(dates)]
    dates = dates[: len(values)]
    return (
        pd.DataFrame({"date": dates, "ticker": ticker, "composite": values})
        .set_index(["date", "ticker"])
        .sort_index()
    )


# ---------------------------------------------------------------------------
# Contract with the simulator and baselines
# ---------------------------------------------------------------------------


def test_elite_params_satisfies_the_baseline_attribute_contract() -> None:
    """`baselines` reads stop/target multiples by attribute name.

    If these are renamed, random_entry_signals raises and the strategy ends up
    compared against an empty baseline -- which would look like a win.
    """
    params = EliteParams()
    assert hasattr(params, "atr_stop_mult")
    assert hasattr(params, "atr_target_mult")
    assert all(hasattr(params, key) for key in STABILITY_KEYS)


def test_baselines_accept_elite_signals() -> None:
    """Both baselines must run against ELITE signals unmodified."""
    ohlcv = _ohlcv()
    params = EliteParams(min_composite=45.0)
    panel = _panel(ohlcv, "T", np.linspace(40, 60, 100))
    feats = feature_frame_from_panel(ohlcv, panel, "T")
    signals = generate_elite_signals(ohlcv, feats, params, "T")
    assert signals, "fixture produced no signals to build baselines from"

    random_entry = baselines.random_entry_signals(signals, {"T": feats}, params, seed=1)
    random_exit = baselines.random_exit_signals(signals, seed=1)

    assert random_entry, "random-entry baseline produced nothing"
    assert len(random_exit) == len(signals)
    # Same trade count per ticker is the point of the random-entry baseline.
    assert len(random_entry) == len(signals)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


def test_signals_fire_only_on_a_crossing_by_default() -> None:
    """A composite that stays high must not fire every evaluated bar.

    Otherwise the simulator measures position stacking in one name rather than
    signal quality.
    """
    ohlcv = _ohlcv()
    # Rises through 50 once, then stays above.
    composites = [45.0] * 10 + [55.0] * 50
    panel = _panel(ohlcv, "T", composites)
    feats = feature_frame_from_panel(ohlcv, panel, "T")

    crossing = generate_elite_signals(
        ohlcv, feats, EliteParams(min_composite=50.0, require_crossing=True), "T"
    )
    every_bar = generate_elite_signals(
        ohlcv, feats, EliteParams(min_composite=50.0, require_crossing=False), "T"
    )

    assert len(crossing) == 1, f"expected one crossing, got {len(crossing)}"
    assert len(every_bar) > len(crossing)


def test_no_signal_below_threshold() -> None:
    ohlcv = _ohlcv()
    panel = _panel(ohlcv, "T", [40.0] * 60)
    feats = feature_frame_from_panel(ohlcv, panel, "T")
    assert generate_elite_signals(ohlcv, feats, EliteParams(min_composite=50.0), "T") == []


def test_composite_is_not_forward_filled() -> None:
    """Unevaluated bars must stay NaN, so the strategy cannot act on a stale score."""
    ohlcv = _ohlcv()
    panel = _panel(ohlcv, "T", np.linspace(45, 55, 60))
    feats = feature_frame_from_panel(ohlcv, panel, "T")

    evaluated = feats["composite"].notna().sum()
    assert evaluated < len(feats), "composite appears filled on every bar"
    # Signals may only occur on evaluated bars.
    signals = generate_elite_signals(ohlcv, feats, EliteParams(min_composite=48.0), "T")
    for signal in signals:
        assert not pd.isna(feats["composite"].loc[signal.signal_date])


def test_signal_entry_index_leaves_room_for_a_next_bar() -> None:
    """Entry is at t+1, so a signal on the last bar is unfillable."""
    ohlcv = _ohlcv()
    panel = _panel(ohlcv, "T", np.linspace(45, 60, 80))
    feats = feature_frame_from_panel(ohlcv, panel, "T")
    for signal in generate_elite_signals(ohlcv, feats, EliteParams(min_composite=48.0), "T"):
        assert signal.entry_index < len(feats) - 1


def test_missing_ticker_in_panel_yields_no_signals() -> None:
    """A ticker absent from the panel must produce nothing, not crash."""
    ohlcv = _ohlcv()
    panel = _panel(ohlcv, "OTHER", [60.0] * 60)
    feats = feature_frame_from_panel(ohlcv, panel, "T")
    assert feats["composite"].isna().all()
    assert generate_elite_signals(ohlcv, feats, EliteParams(), "T") == []


def test_feature_frame_keeps_the_columns_baselines_read() -> None:
    ohlcv = _ohlcv()
    panel = _panel(ohlcv, "T", [50.0] * 60)
    feats = feature_frame_from_panel(ohlcv, panel, "T")
    for column in ("close", "rsi14", "atr_pct", "mom63", "dollar_vol20", "composite"):
        assert column in feats.columns, column


# ---------------------------------------------------------------------------
# The pluggable refactor must be behaviour-preserving
# ---------------------------------------------------------------------------


def test_walkforward_defaults_are_unchanged() -> None:
    """With no hooks passed, the harness must still use the APEX strategy.

    Checked by signature and default resolution rather than by running a full
    walk-forward, which needs network data. `core/apex_quant/tests/` covers the
    behavioural side.
    """
    import inspect

    signature = inspect.signature(walkforward.run_walk_forward)
    for name in ("param_grid", "signal_fn", "feature_fn", "stability_keys"):
        assert name in signature.parameters, f"missing hook: {name}"
        assert signature.parameters[name].default is None, (
            f"{name} must default to None so existing callers are unaffected"
        )

    # The defaults the function falls back to.
    assert walkforward.generate_signals is not None
    assert callable(indicators.feature_frame)
    assert len(StrategyParams.grid()) > 0


def test_elite_grid_probes_the_reachable_composite_band() -> None:
    """Thresholds must sit inside the band the scorer can actually produce.

    With four components pinned the composite spans ~[29.5, 67.5] and in
    practice [35, 59]. A grid built around ELITE's own BUY threshold of 65 would
    select almost nothing and the walk-forward would report "no signals" rather
    than a result.
    """
    thresholds = {p.min_composite for p in EliteParams.grid()}
    assert thresholds, "empty grid"
    assert max(thresholds) <= 60.0, (
        f"threshold {max(thresholds)} is at or beyond the observed composite "
        "maximum; the grid would select nothing"
    )
    assert min(thresholds) >= 40.0, "threshold so low it selects everything"
