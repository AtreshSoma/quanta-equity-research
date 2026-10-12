"""Validation of the OOS statistics themselves, on signals of known sign.

Phase 2 reports a *negative* information coefficient for the ELITE composite,
which is a strong claim. Before it can be trusted, the measurement has to be
shown correct on inputs whose answer is known in advance:

  * a synthetic factor built to predict -> must return a large positive IC
  * its negation                        -> must return the mirror-image negative
  * pure noise                          -> must return approximately zero

If these do not hold, a negative IC on real data says nothing about the model
and everything about the estimator. Reporting the finding without them would be
presenting a possible bug as a discovery.
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

from apex_quant.elite_oos import (  # noqa: E402
    _spearman,
    compute_buckets,
    compute_component_ic,
    compute_ic,
)

N_DATES = 160
N_TICKERS = 20
HORIZON = 21


def _panel(mode: str, seed: int = 3) -> pd.DataFrame:
    """Build a synthetic (date, ticker) panel with a known composite/return link.

    mode:
      "predictive"  composite is forward return plus modest noise
      "inverted"    composite is the negation of that
      "noise"       composite is independent of forward return
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-06", periods=N_DATES, freq="W-MON")
    tickers = [f"T{i:02d}" for i in range(N_TICKERS)]

    rows = []
    for date in dates:
        # A market-wide move shared by every name on this date. Its presence is
        # the reason IC must be measured per-date: a pooled correlation would
        # partly measure this instead of the factor.
        market = rng.normal(0.01, 0.03)
        for ticker in tickers:
            idiosyncratic = rng.normal(0.0, 0.05)
            fwd = market + idiosyncratic
            if mode == "predictive":
                composite = 50.0 + 100.0 * idiosyncratic + rng.normal(0.0, 1.0)
            elif mode == "inverted":
                composite = 50.0 - 100.0 * idiosyncratic + rng.normal(0.0, 1.0)
            elif mode == "noise":
                composite = 50.0 + rng.normal(0.0, 5.0)
            else:  # pragma: no cover
                raise ValueError(mode)
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "composite": composite,
                    "score_technical": composite,
                    "score_risk": 50.0,
                    f"fwd_{HORIZON}d": fwd,
                }
            )

    return pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()


# ---------------------------------------------------------------------------
# The estimator must recover a known sign and magnitude
# ---------------------------------------------------------------------------


def test_predictive_factor_yields_large_positive_ic() -> None:
    result = compute_ic(_panel("predictive"), HORIZON)
    assert result.mean_ic > 0.5, f"a factor built to predict scored only {result.mean_ic:.4f}"
    assert result.t_stat > 2.0, "a strong factor must clear the significance bar"
    assert result.hit_rate > 0.9
    assert result.decile_spread > 0


def test_inverted_factor_mirrors_the_predictive_one() -> None:
    """Sign must flip and magnitude must be preserved."""
    good = compute_ic(_panel("predictive"), HORIZON)
    bad = compute_ic(_panel("inverted"), HORIZON)
    assert bad.mean_ic < -0.5, bad.mean_ic
    assert bad.mean_ic == pytest.approx(-good.mean_ic, abs=0.05)
    assert bad.decile_spread < 0


def test_noise_yields_approximately_zero_ic() -> None:
    """An unrelated score must not appear to predict."""
    result = compute_ic(_panel("noise"), HORIZON)
    assert abs(result.mean_ic) < 0.06, f"noise scored IC {result.mean_ic:.4f}"
    assert abs(result.t_stat) < 2.5, "noise must not look significant"


# ---------------------------------------------------------------------------
# Overlap correction and per-date construction
# ---------------------------------------------------------------------------


def test_overlapping_t_stat_exceeds_the_independent_one() -> None:
    """The reported t-stat must be the more conservative of the two.

    Overlapping forward returns inflate significance. If the corrected figure
    were ever the larger, the correction is not doing anything.
    """
    result = compute_ic(_panel("predictive"), HORIZON)
    assert abs(result.t_stat_overlapping) >= abs(result.t_stat) * 0.99, (
        f"independent t={result.t_stat:.2f} exceeds overlapping "
        f"t={result.t_stat_overlapping:.2f}; the overlap correction is inverted"
    )
    assert result.n_independent_dates <= result.n_dates


def test_ic_is_not_fooled_by_a_shared_market_move() -> None:
    """A score that only tracks the market-wide move must score ~0 IC.

    This is the property that makes per-date IC the right statistic. Such a
    score is useless for *choosing between* names, and IC must say so even
    though a pooled correlation would look impressive.
    """
    rng = np.random.default_rng(5)
    dates = pd.date_range("2020-01-06", periods=N_DATES, freq="W-MON")
    rows = []
    for date in dates:
        market = rng.normal(0.01, 0.04)
        for i in range(N_TICKERS):
            fwd = market + rng.normal(0.0, 0.05)
            # Identical across names on a date: carries date info, not rank info.
            rows.append(
                {
                    "date": date,
                    "ticker": f"T{i:02d}",
                    "composite": 50.0 + 200.0 * market,
                    f"fwd_{HORIZON}d": fwd,
                }
            )
    panel = pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()

    # Constant within each date -> Spearman undefined -> no usable dates at all,
    # which is the correct answer rather than a spurious correlation.
    with pytest.raises(RuntimeError, match="no dates"):
        compute_ic(panel, HORIZON)


def test_buckets_are_monotonic_for_a_predictive_factor() -> None:
    rows = compute_buckets(_panel("predictive"), HORIZON, n_buckets=5)
    assert len(rows) == 5
    means = [r.mean_fwd_return for r in rows]
    assert all(b > a for a, b in zip(means, means[1:])), means
    assert rows[-1].win_rate > rows[0].win_rate


def test_buckets_invert_for_an_inverted_factor() -> None:
    rows = compute_buckets(_panel("inverted"), HORIZON, n_buckets=5)
    means = [r.mean_fwd_return for r in rows]
    assert all(b < a for a, b in zip(means, means[1:])), means


def test_component_ic_reports_each_input_separately() -> None:
    out = compute_component_ic(_panel("predictive"), HORIZON)
    assert out["composite"] > 0.5
    # score_risk is constant here, so its IC is undefined rather than 0.
    assert np.isnan(out["score_risk"]) or abs(out["score_risk"]) < 0.05


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


def test_spearman_handles_degenerate_input() -> None:
    idx = pd.RangeIndex(5)
    assert _spearman(pd.Series([1, 2, 3], index=range(3)), pd.Series([1, 2, 3], index=range(3))) == pytest.approx(1.0)
    assert _spearman(pd.Series([1, 2, 3, 4, 5], index=idx), pd.Series([5, 4, 3, 2, 1], index=idx)) == pytest.approx(-1.0)
    # Constant series: undefined, must be NaN not 0.0 (0.0 would read as "no signal").
    assert np.isnan(_spearman(pd.Series([1, 1, 1], index=range(3)), pd.Series([1, 2, 3], index=range(3))))
    assert np.isnan(_spearman(pd.Series([1, 2], index=range(2)), pd.Series([1, 2], index=range(2))))


def test_spearman_is_rank_based_not_linear() -> None:
    """A monotone but non-linear relationship must score 1.0."""
    idx = pd.RangeIndex(6)
    x = pd.Series([1, 2, 3, 4, 5, 6], index=idx)
    y = pd.Series([1, 4, 9, 16, 25, 36], index=idx)  # x squared
    assert _spearman(x, y) == pytest.approx(1.0)
