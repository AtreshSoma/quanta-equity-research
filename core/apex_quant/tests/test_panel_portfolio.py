"""The portfolio study of the score panel: arithmetic, timing and calibration.

Synthetic panels with known answers. The properties that matter most are the
ones a backtest gets wrong quietly: a score traded at the close it was built
from, later data changing an earlier result, and costs that are not charged on
what was actually traded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant import panel_portfolio as pp

TICKERS = [f"T{i:02d}" for i in range(20)]


def _panel(n_dates: int = 60, seed: int = 0, drift=None, score=None) -> pd.DataFrame:
    """Weekly panel: 20 names, each with its own constant weekly return unless given."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-03", periods=n_dates, freq="7D")
    weekly = np.linspace(-0.01, 0.01, len(TICKERS)) if drift is None else np.asarray(drift)
    rows = []
    for j, t in enumerate(TICKERS):
        close = 100.0 * np.cumprod(np.full(n_dates, 1.0 + weekly[j]))
        for i, d in enumerate(dates):
            sc = float(j) if score is None else float(score(i, j, rng))
            rows.append((d, t, close[i], sc))
    frame = pd.DataFrame(rows, columns=["date", "ticker", "close", "composite"])
    frame = frame.set_index(["date", "ticker"]).sort_index()
    close = frame["close"].unstack("ticker")
    frame["fwd_63d"] = (close.shift(-13) / close - 1.0).stack()
    return frame


def test_a_score_that_orders_the_drifts_puts_the_top_ahead_and_the_bottom_behind():
    panel = _panel()
    top = pp.period_returns(panel, side="top")
    bottom = pp.period_returns(panel, side="bottom")
    assert len(top.gross) > 5
    assert np.all(top.gross > top.benchmark)
    assert np.all(bottom.gross < bottom.benchmark)
    assert top.holdings[0] == TICKERS[-4:]          # a fifth of 20 names, highest scores
    assert bottom.holdings[0] == TICKERS[:4]


def test_period_return_is_execution_close_to_execution_close():
    panel = _panel()
    pr = pp.period_returns(panel, side="top")
    weekly = np.linspace(-0.01, 0.01, len(TICKERS))[-4:]
    expected = float(np.mean((1.0 + weekly) ** pp.STEP - 1.0))
    assert pr.gross[0] == pytest.approx(expected)
    dates = panel.index.get_level_values("date").unique().sort_values()
    assert pr.entry_dates[0] == dates[pp.ENTRY_LAG]
    assert pr.exit_dates[0] == dates[pp.ENTRY_LAG + pp.STEP]


def test_costs_are_charged_on_the_weight_traded():
    panel = _panel()
    pr = pp.period_returns(panel, side="top")
    # The first rebalance buys the whole portfolio; the same names are held after.
    assert pr.traded[0] == pytest.approx(1.0)
    assert np.allclose(pr.traded[1:], 0.0)
    free = pp.portfolio_stats("x", "", pr, 0.0)
    costly = pp.portfolio_stats("x", "", pr, 0.01)
    assert costly.cost_drag_per_year == pytest.approx(0.01 * 1.0 / costly.years)
    assert costly.cagr < free.cagr
    assert costly.turnover_per_year == pytest.approx(0.5 / costly.years)


def test_full_rotation_trades_twice_the_portfolio():
    # Scores flip every rebalance: every name held is sold and replaced.
    panel = _panel(score=lambda i, j, _r: j if (i // pp.STEP) % 2 == 0 else -j)
    pr = pp.period_returns(panel, side="top")
    assert np.allclose(pr.traded[1:], 2.0)


def test_a_score_is_never_traded_at_the_close_it_was_built_from():
    """A score that knows the NEXT week's jump earns nothing from it: the
    position is opened one panel date later, after the jump."""
    n = 60
    rng = np.random.default_rng(3)
    dates = pd.date_range("2020-01-03", periods=n, freq="7D")
    jump = rng.normal(0, 0.05, size=(n, len(TICKERS)))
    rows = []
    for j, t in enumerate(TICKERS):
        close = 100.0 * np.cumprod(1.0 + jump[:, j])
        for i, d in enumerate(dates):
            nxt = jump[i + 1, j] if i + 1 < n else 0.0     # the coming week's return
            rows.append((d, t, close[i], nxt))
    panel = pd.DataFrame(rows, columns=["date", "ticker", "close", "composite"]).set_index(["date", "ticker"])
    lagged = pp.period_returns(panel, side="top", entry_lag=1)
    same_close = pp.period_returns(panel, side="top", entry_lag=0)
    assert float(np.mean(same_close.gross - same_close.benchmark)) > 0.02      # the look-ahead it avoids
    assert abs(float(np.mean(lagged.gross - lagged.benchmark))) < 0.02


def test_later_data_does_not_change_earlier_periods():
    panel = _panel(n_dates=80, score=lambda i, j, r: r.normal())
    base = pp.period_returns(panel, side="top")
    cutoff = base.exit_dates[5]
    changed = panel.copy()
    later = changed.index.get_level_values("date") > cutoff
    changed.loc[later, "close"] = changed.loc[later, "close"] * 3.0
    changed.loc[later, "composite"] = -changed.loc[later, "composite"]
    again = pp.period_returns(changed, side="top")
    assert np.allclose(again.gross[:6], base.gross[:6])
    assert again.holdings[:6] == base.holdings[:6]


def test_a_name_scored_a_day_off_the_grid_is_still_held_and_adds_no_dates():
    panel = _panel().reset_index()
    off = panel["ticker"] == TICKERS[-1]
    panel.loc[off, "date"] = panel.loc[off, "date"] - pd.Timedelta(days=1)
    panel = panel.set_index(["date", "ticker"]).sort_index()
    grid = pp.grid_dates(panel)
    assert len(grid) == 60                                   # the stray dates are not rebalance dates
    pr = pp.period_returns(panel, side="top")
    assert TICKERS[-1] in pr.holdings[0]


def test_stats_on_a_known_series():
    dates = pd.date_range("2020-01-01", periods=11, freq="365D")
    pr = pp.PeriodReturns(list(dates[:-1]), list(dates[1:]), np.array([0.10, -0.10] * 5),
                          np.zeros(10), np.zeros(10), [[] for _ in range(10)])
    st = pp.portfolio_stats("x", "", pr, 0.0)
    assert st.periods == 10
    assert st.cagr == pytest.approx((1.1 * 0.9) ** 0.5 - 1.0, abs=2e-4)
    assert st.max_drawdown == pytest.approx(0.9 ** 5 * 1.1 ** 4 - 1.0, abs=1e-9)   # the fall from the start
    assert st.hit_rate == 0.5
    assert st.sharpe == pytest.approx(0.0, abs=1e-9)


def test_too_few_periods_report_nothing_rather_than_a_number():
    dates = pd.date_range("2020-01-01", periods=4, freq="30D")
    pr = pp.PeriodReturns(list(dates[:-1]), list(dates[1:]), np.array([0.1, 0.1, 0.1]),
                          np.zeros(3), np.zeros(3), [[], [], []])
    st = pp.portfolio_stats("x", "", pr, 0.0)
    assert st.cagr is None and st.sharpe is None and st.hit_rate is None


def test_band_calibration_rises_when_the_score_orders_returns():
    # Scores spread over the live bands; the drift rises with the score.
    scores = np.linspace(20.0, 80.0, len(TICKERS))
    panel = _panel(n_dates=200, drift=np.linspace(-0.004, 0.004, len(TICKERS)),
                   score=lambda i, j, _r: scores[j])
    rows = pp.band_calibration(panel)
    have = [r for r in rows if r.mean_excess is not None and r.dates >= 5]
    assert len(have) >= 4
    assert [r.mean_excess for r in have] == sorted((r.mean_excess for r in have), reverse=True)
    assert pp.band_monotonicity(rows) == pytest.approx(1.0)
    # Overlapping quarters are not counted as separate observations.
    assert max(r.dates for r in rows) <= 200 // 13 + 1


def test_fixed_weight_composite_reproduces_a_weighted_mean():
    idx = pd.MultiIndex.from_product([pd.to_datetime(["2020-01-03"]), ["A", "B"]], names=["date", "ticker"])
    panel = pd.DataFrame({"score_technical": [100.0, 0.0], "score_fundamental": [0.0, 100.0],
                          "score_valuation": [50.0, 50.0], "score_quality": [50.0, 50.0],
                          "score_sentiment": [50.0, 50.0]}, index=idx)
    w = {"technical": 0.5, "fundamental": 0.1, "valuation": 0.2, "quality": 0.1, "sentiment": 0.1}
    out = pp.fixed_weight_composite(panel, w)
    assert out.loc[(pd.Timestamp("2020-01-03"), "A")] == pytest.approx(70.0)
    assert out.loc[(pd.Timestamp("2020-01-03"), "B")] == pytest.approx(30.0)


def test_report_runs_end_to_end_and_states_its_caveats():
    panel = _panel(n_dates=120, score=lambda i, j, r: 50 + r.normal(0, 10))
    for leg in pp.LEGS:
        panel[f"score_{leg}"] = panel["composite"]
    panel["fwd_21d"] = panel["fwd_63d"]
    panel["annualized_volatility"] = 0.2
    report = pp.run_portfolio_report(panel, panel_name="synthetic")
    text = pp.format_report(report)
    assert "survivorship" in text and "not a validated strategy" in text
    assert {p.name for p in report.portfolios} >= {"composite_top", "composite_top_stress", "composite_bottom"}
    stress = next(p for p in report.portfolios if p.name == "composite_top_stress")
    base = next(p for p in report.portfolios if p.name == "composite_top")
    assert stress.cagr < base.cagr
