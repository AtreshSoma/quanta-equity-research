"""A3: free baseline factors are point-in-time, and the comparison finds a planted edge."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.factor_baselines import add_baselines, run_baselines


def _panel(n_tickers=20, n_dates=160, seed=0, plant=0.0):
    """Weekly synthetic panel; ``plant`` mixes the 21d forward return into composite."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-02", periods=n_dates * 5)[::5]
    frames = []
    for t in range(n_tickers):
        close = 50 * np.exp(np.cumsum(rng.normal(0.001, 0.03, len(dates))))
        s = pd.Series(close, index=dates)
        fwd21 = s.shift(-4) / s - 1
        fwd63 = s.shift(-13) / s - 1
        noise = rng.normal(0, 1, len(dates))
        comp = plant * (fwd21.fillna(0).to_numpy() * 40) + noise
        frames.append(pd.DataFrame({
            "date": dates, "ticker": f"T{t:02d}", "close": close, "composite": comp,
            "score_technical": rng.normal(0, 1, len(dates)),
            "annualized_volatility": rng.uniform(0.1, 0.6, len(dates)),
            "fwd_21d": fwd21.to_numpy(), "fwd_63d": fwd63.to_numpy(),
        }))
    return pd.concat(frames).set_index(["date", "ticker"]).sort_index()


def test_momentum_uses_only_past_closes():
    panel = _panel(n_tickers=2)
    base = add_baselines(panel)
    t = "T00"
    close = panel.xs(t, level="ticker")["close"]
    day = close.index[100]
    want = close.asof(day - pd.Timedelta(days=30)) / close.asof(day - pd.Timedelta(days=365)) - 1
    assert base.loc[(day, t), "momentum_12_1"] == pytest.approx(want)
    # Changing every later close must not change today's value.
    future = panel.copy()
    future.loc[future.index.get_level_values("date") > day, "close"] *= 3
    assert add_baselines(future).loc[(day, t), "momentum_12_1"] == pytest.approx(want)


def test_the_first_year_has_no_momentum_value():
    base = add_baselines(_panel(n_tickers=2))
    first = base.index.get_level_values("date").min()
    assert base.xs(first, level="date")["momentum_12_1"].isna().all()


def test_a_planted_signal_beats_every_baseline():
    report = run_baselines(_panel(plant=1.0), horizons=(21,))
    rows = {r.factor: r for r in report.horizons["21d"]}
    assert rows["composite"].mean_ic > 0.2 and rows["composite"].significant_fdr
    for name in ("momentum_12_1", "reversal_1m", "low_volatility", "random"):
        assert rows[name].composite_minus["ci95_bootstrap"][0] > 0
    assert "Reliably better than" in report.verdicts["21d"]


def test_noise_is_not_called_an_edge():
    report = run_baselines(_panel(plant=0.0, seed=3), horizons=(21,))
    rows = {r.factor: r for r in report.horizons["21d"]}
    assert abs(rows["composite"].mean_ic) < 0.05
    assert rows["composite"].significant_fdr is False
