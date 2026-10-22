"""Causality and correctness guards for the point-in-time composite scorer.

An out-of-sample report is worth exactly as much as its causality guarantees. A
backtest with look-ahead does not produce a weak result that needs caveats -- it
produces a *confidently wrong* one, which is worse than no result. So these
tests come before any number is reported, and the central one
(`test_score_is_unaffected_by_future_bars`) is the whole basis for trusting the
Phase 2 output.
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

from apex_quant import elite_scorer  # noqa: E402
from apex_quant.elite_scorer import ScorerConfig, forward_returns, score_history  # noqa: E402


def _frame(n: int = 420, seed: int = 7, drift: float = 0.0004) -> pd.DataFrame:
    """Deterministic OHLCV with a mild trend and valid OHLC relationships."""
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
    spread = close * 0.007
    frame = pd.DataFrame(
        {
            "Open": close - spread * rng.uniform(-1, 1, n),
            "High": close + spread * rng.uniform(0.4, 1.4, n),
            "Low": close - spread * rng.uniform(0.4, 1.4, n),
            "Close": close,
            "Volume": rng.integers(4_000_000, 18_000_000, n).astype(float),
        },
        index=idx,
    )
    frame["High"] = frame[["Open", "High", "Close"]].max(axis=1)
    frame["Low"] = frame[["Open", "Low", "Close"]].min(axis=1)
    return frame


def _benchmark(index: pd.DatetimeIndex, seed: int = 11) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0003, 0.009, len(index)), index=index)


@pytest.fixture(scope="module")
def scored():
    frame = _frame()
    bench = _benchmark(frame.index)
    out = score_history(frame, "TEST", bench, ScorerConfig(eval_every=10, warmup_bars=250))
    assert not out.empty, "scorer produced no rows on a 420-bar frame"
    return frame, bench, out


# ---------------------------------------------------------------------------
# Causality -- the load-bearing guarantees
# ---------------------------------------------------------------------------


def test_score_is_unaffected_by_future_bars(scored) -> None:
    """The score at bar t must be identical whether or not bars > t exist.

    This is the definition of no look-ahead, tested the only way that actually
    proves it: score a truncated frame and compare against the same date scored
    from the full frame. If any indicator reached forward -- a centred window, a
    full-series percentile, a global min/max -- these two disagree.
    """
    frame, bench, full = scored

    cut = 320
    truncated = score_history(
        frame.iloc[:cut], "TEST", bench, ScorerConfig(eval_every=10, warmup_bars=250)
    )
    assert not truncated.empty

    shared = full.index.intersection(truncated.index)
    assert len(shared) >= 5, f"only {len(shared)} overlapping evaluations to compare"

    numeric = ["composite", "score_technical", "score_risk", "rsi", "adx", "atr_pct"]
    for date in shared:
        for col in numeric:
            a, b = full.loc[date, col], truncated.loc[date, col]
            if pd.isna(a) and pd.isna(b):
                continue
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9), (
                f"LOOK-AHEAD at {date.date()} in '{col}': "
                f"{a} with future bars present, {b} without. "
                f"Something in the scoring path reads beyond bar t."
            )


def test_benchmark_window_is_truncated_at_asof() -> None:
    """Beta must not see benchmark data published after the bar being scored."""
    frame = _frame()
    bench = _benchmark(frame.index)

    analyzer = elite_scorer._PointInTimeRiskAnalyzer(bench)
    asof = frame.index[300]
    analyzer._asof = asof

    served = analyzer._spy_returns()
    assert served.index.max() <= asof, (
        f"benchmark series served data to {served.index.max()} while scoring {asof}"
    )
    assert len(served) <= elite_scorer.BETA_WINDOW_BARS, (
        "served window is longer than the trailing window the live engine uses, "
        "so backtest beta would be computed on more history than production beta"
    )


def test_forward_returns_are_labels_not_features() -> None:
    """Forward returns must look forward, and the tail must be NaN."""
    close = pd.Series(
        [100.0, 101.0, 102.0, 103.0, 104.0],
        index=pd.date_range("2024-01-01", periods=5, freq="B"),
    )
    fwd = forward_returns(close, horizons=(2,))

    assert fwd["fwd_2d"].iloc[0] == pytest.approx(102.0 / 100.0 - 1.0)
    assert pd.isna(fwd["fwd_2d"].iloc[-1]), "last row must be NaN: t+2 does not exist"
    assert pd.isna(fwd["fwd_2d"].iloc[-2])


# ---------------------------------------------------------------------------
# Correctness of the reconstruction
# ---------------------------------------------------------------------------


def test_composite_matches_the_engines_own_arithmetic(scored) -> None:
    """The reconstructed composite equals what SignalGenerator would produce.

    Guards against the reconstruction drifting into a parallel implementation --
    the exact failure that would make this whole exercise validate the wrong
    model.
    """
    import TradingAnalysis_ELITE as elite

    _, _, out = scored
    weights = dict(elite.Config.REGIME_WEIGHTS["SIDEWAYS"])
    generator = elite.SignalGenerator()

    for date, row in out.head(8).iterrows():
        scores = {k: row[f"score_{k}"] for k in
                  ("technical", "fundamental", "valuation", "quality", "sentiment", "risk")}
        expected = generator._calculate_composite(scores, weights)
        assert row["composite"] == pytest.approx(expected, abs=1e-9), (
            f"composite at {date.date()} does not match the engine's own formula"
        )


def test_unavailable_components_are_constant_across_history(scored) -> None:
    """The four components with no point-in-time source must not vary.

    Constancy -- not any particular value -- is the property that matters. A
    constant carries no information and therefore cannot leak any. If one of
    these ever starts moving, the backtest has begun using present-day
    fundamentals to score the past and every result becomes invalid.
    """
    _, _, out = scored
    for component in elite_scorer.PINNED_COMPONENTS:
        column = out[f"score_{component}"]
        assert column.nunique() == 1, (
            f"'{component}' varies across history ({sorted(column.unique())[:5]}) but has "
            f"no point-in-time source -- that is look-ahead bias, not signal."
        )


def test_pinned_components_hold_the_engines_own_defaults() -> None:
    """Record what the engine assigns to empty inputs, including D8's magnitude.

    An empty `FundamentalData` scores 40, not 50: `_calculate_component_scores`
    starts at 50 and applies `roe < 5: -10` to a default ROE of 0.0. So the
    engine treats *absent* fundamentals as mildly bad rather than unknown, which
    is defect D8 -- and this test pins the magnitude at -10 fundamental points
    (-2.5 composite points) so a future fix to D8 shows up here deliberately
    rather than silently shifting every backtest baseline.
    """
    import TradingAnalysis_ELITE as elite

    generator = elite.SignalGenerator()
    scores = generator._calculate_component_scores(
        elite.TechnicalIndicators(),
        elite.FundamentalData(),
        elite.RiskMetrics(),
        elite.SentimentData(),
        elite.InsiderData(),
        elite.MarketIntelligence(),
        elite.ValuationMetrics(),
    )

    assert scores["fundamental"] == 40, (
        "empty FundamentalData no longer scores 40. If D8 was fixed so missing "
        "data is neutral, update elite_scorer's documented arithmetic (27.5) and "
        "the reachable band, then re-run the OOS report -- its baseline moved."
    )
    for neutral_component in ("valuation", "quality", "sentiment"):
        assert scores[neutral_component] == elite_scorer.NEUTRAL_SCORE


def test_composite_stays_within_the_reachable_band(scored) -> None:
    """With 60% of weight pinned, the composite is bounded to [29.5, 67.5].

    Documents the scope limit as an executable assertion: STRONG_BUY (>=75)
    cannot occur, so no result from this harness may be described as validating
    the full model.
    """
    _, _, out = scored
    assert out["composite"].min() >= 29.4, out["composite"].min()
    assert out["composite"].max() <= 67.6, out["composite"].max()
    assert (out["composite"] < 75).all(), "STRONG_BUY should be unreachable here"


def test_scorer_is_deterministic() -> None:
    """Two runs on identical inputs agree exactly."""
    frame = _frame()
    bench = _benchmark(frame.index)
    cfg = ScorerConfig(eval_every=20, warmup_bars=250)
    first = score_history(frame, "TEST", bench, cfg)
    second = score_history(frame, "TEST", bench, cfg)
    pd.testing.assert_frame_equal(first, second)


def test_non_evaluated_bars_are_absent_not_stale(scored) -> None:
    """Skipped bars must not appear with a forward-filled score."""
    frame, _, out = scored
    assert len(out) < len(frame), "eval_every=10 should evaluate a subset of bars"
    gaps = out.index.to_series().diff().dropna()
    assert (gaps >= pd.Timedelta(days=1)).all()
    assert not out["composite"].duplicated().all(), "scores look forward-filled"


class _SpyFundamentals:
    """A PITFundamentals stand-in that records every day it is asked about."""

    sector, industry = "Technology", "Software"

    def __init__(self, known_from: str):
        self.known_from = pd.Timestamp(known_from)
        self.asked = []

    def info_at(self, day, price):
        self.asked.append(pd.Timestamp(day))
        if pd.Timestamp(day) < self.known_from:
            return {}
        return {"quoteType": "EQUITY", "symbol": "AAA", "totalRevenue": 1e9,
                "netIncomeToCommon": 2.5e8, "profitMargins": 0.25, "returnOnEquity": 0.30,
                "revenueGrowth": 0.22, "debtToEquity": 30.0, "currentRatio": 2.5,
                "sharesOutstanding": 1e8, "trailingEps": 2.5, "bookValue": 8.0,
                "trailingPE": price / 2.5, "marketCap": price * 1e8}

    def statements_at(self, day):
        return pd.DataFrame(), pd.DataFrame()

    def filing_state(self, day):
        return "2023-02-01" if pd.Timestamp(day) >= self.known_from else None


def test_point_in_time_fundamentals_are_only_read_as_of_each_bar() -> None:
    frame = _frame()
    spy = _SpyFundamentals("2023-03-01")
    out = score_history(frame, "AAA", _benchmark(frame.index), ScorerConfig(eval_every=20),
                        fundamentals=spy)
    # Every lookup is for a bar that was being scored: never a later date.
    assert spy.asked and set(spy.asked) <= set(out.index)
    before, after = out[out.index < spy.known_from], out[out.index >= spy.known_from]
    assert before["fund_filed"].isna().all() and after["fund_filed"].notna().all()
    # Strong reported fundamentals lift the leg above the engine's empty-input value.
    assert (after["score_fundamental"] > before["score_fundamental"].iloc[0]).all()


def test_auto_regime_uses_the_live_regime_weights() -> None:
    frame = _frame()
    out = score_history(frame, "AAA", _benchmark(frame.index),
                        ScorerConfig(eval_every=20, regime="auto"))
    assert out["regime"].notna().all()
    assert set(out["regime"]) <= {"SIDEWAYS", "BULL_TRENDING", "BULL_VOLATILE", "BEAR_TRENDING",
                                  "BEAR_VOLATILE", "CRISIS", "HIGH_VOLATILITY"}
