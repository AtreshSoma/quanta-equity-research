"""P1-1: the free-factor control model (12-1 momentum + quality) scored in shadow.

Pre-registered in docs/preregistration/factor_baseline_v1.md. It must use the
same 12-1 definition as the backtest baselines, never touch the live call, and
be recorded under its own ledger engine so it is never pooled with the composite.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import TradingAnalysis_ELITE as elite
from apex_quant.factor_baselines import add_baselines
from engine import factor_baseline as fb
from engine.thresholds import FACTOR_BASELINE as FB
from tracking import fingerprint as fpm
from tracking.ledger import SignalLedger, record_shadow_calls


def _closes(n_days=800, seed=1):
    idx = pd.bdate_range("2023-06-01", periods=n_days)
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n_days))), index=idx)


def test_momentum_matches_the_backtest_baseline_definition():
    close = _closes()
    panel = pd.DataFrame({"close": close.values},
                         index=pd.MultiIndex.from_arrays([close.index, ["X"] * len(close)],
                                                         names=["date", "ticker"]))
    expected = add_baselines(panel)["momentum_12_1"].iloc[-1]
    assert fb.momentum_12_1(close) == pytest.approx(expected, rel=1e-12)


def test_momentum_is_none_without_a_full_year_or_with_bad_prices():
    close = _closes(n_days=200)
    assert fb.momentum_12_1(close) is None
    assert fb.momentum_12_1(pd.Series(dtype=float)) is None
    bad = _closes()
    bad.iloc[-275:-245] = 0.0   # the bars around 12 months ago
    assert fb.momentum_12_1(bad) is None


def test_momentum_reads_only_bars_up_to_as_of():
    close = _closes()
    as_of = close.index[-100]
    assert fb.momentum_12_1(close, as_of) == fb.momentum_12_1(close.loc[:as_of])


def test_momentum_leg_saturates_and_is_centred():
    sat = FB.MOMENTUM_SATURATION
    assert fb.momentum_leg(0.0) == 50.0
    assert fb.momentum_leg(sat) == 100.0 and fb.momentum_leg(5 * sat) == 100.0
    assert fb.momentum_leg(-sat) == 0.0
    assert fb.momentum_leg(None) is None


def test_score_blends_the_legs_and_funds_use_momentum_only():
    out = fb.assess(0.25, 80.0)
    assert out["score"] == pytest.approx(0.5 * 75.0 + 0.5 * 80.0)
    assert out["engine"] == "factor_baseline_shadow" and out["key"] == "factor.baseline.v1"
    fund = fb.assess(0.25, 80.0, is_fund=True)
    assert fund["score"] == pytest.approx(75.0) and fund["legs"]["quality"] is None
    assert fb.assess(None, 80.0) is None


class _Scores:
    quality_score = 70.0
    weights_used = {"technical": 1.0}


class _Fund:
    is_etf = False


class _Result:
    ticker = "AAPL"
    scores = _Scores()
    fundamental = _Fund()
    factor_baseline_momentum = 0.10


def test_payload_reads_the_finished_analysis_and_is_untested():
    out = elite.factor_baseline_payload(_Result())
    assert out["score"] == pytest.approx(0.5 * 60.0 + 0.5 * 70.0)
    assert out["status"] == "UNTESTED"


def test_no_payload_for_an_unscored_analysis():
    class Unscored(_Result):
        scores = None
    assert elite.factor_baseline_payload(Unscored()) is None


class _Decision:
    ticker, as_of, reference_price = "AAPL", "2026-09-18", 200.0
    factor_scores = {"quality": 70.0}


def test_recorded_under_its_own_engine(tmp_path):
    ledger = SignalLedger(tmp_path / "l.sqlite3")
    payload = elite.factor_baseline_payload(_Result())
    assert record_shadow_calls(_Decision(), payload, horizons=[5, 63], ledger=ledger) == 2
    rows = ledger.rows(engine="factor_baseline_shadow")
    assert {r["horizon_days"] for r in rows} == {5, 63}
    assert all(r["model_fingerprint"] == fpm.model_fingerprint("factor_baseline_shadow")
               for r in rows)
    assert not ledger.rows(engine="elite")


def test_fingerprint_covers_the_baseline_and_the_engine_that_feeds_it():
    names = {p.name for p in fpm._sources("factor_baseline_shadow", True)}
    assert {"factor_baseline.py", "TradingAnalysis_ELITE.py"} <= names
