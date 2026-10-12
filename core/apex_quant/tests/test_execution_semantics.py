"""Execution-chronology guards shared by the labeler and the portfolio simulator.

Audit finding F01: the portfolio simulator processed intraday exits and closing
equity BEFORE admitting trades at that same day's open, newly entered positions
skipped their own entry bar's stop/target check, and stops filled at the stop
level even when the whole bar gapped through it.

These fixtures pin the causal execution model:
  * a bar's opening gap is observed before its intraday range;
  * a position is exposed to stop/target from its entry bar onward;
  * entries may only consume information and cash that existed at the open;
  * the equity curve accounts for the first day and for terminal holdings.

All examples are deterministic synthetic bars with costs switched off, so the
labeler and the simulator must agree exactly.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.apex_quant.costs import CostModel
from core.apex_quant.labeling import simulate_forward
from core.apex_quant.simulator import SimConfig, simulate
from core.apex_quant.strategy import Signal

FREE = CostModel(
    commission_per_share=0.0,
    min_commission=0.0,
    half_spread_bps=0.0,
    base_slippage_bps=0.0,
    impact_k=0.0,
)


def _frame(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build an OHLCV frame from (open, high, low, close) tuples."""
    idx = pd.bdate_range("2024-01-01", periods=len(bars))
    return pd.DataFrame(
        {
            "Open": [b[0] for b in bars],
            "High": [b[1] for b in bars],
            "Low": [b[2] for b in bars],
            "Close": [b[3] for b in bars],
            "Volume": [10_000_000] * len(bars),
        },
        index=idx,
    )


def _signal(df: pd.DataFrame, i: int, stop_pct: float, target_pct: float,
            ticker: str = "TEST") -> Signal:
    return Signal(
        ticker=ticker,
        signal_date=df.index[i],
        entry_index=i,
        stop_pct=stop_pct,
        target_pct=target_pct,
        atr_pct=0.02,
        adv_dollars=1e12,
    )


def _run(df: pd.DataFrame, signals: list[Signal], cfg: SimConfig,
         data: dict | None = None):
    data = data or {"TEST": df}
    calendar = df.index[1:]
    return simulate(data, signals, calendar, FREE, cfg)


# --------------------------------------------------------------------------
# Same-bar fill semantics (labeler)
# --------------------------------------------------------------------------

def test_stop_gap_fills_at_the_open_not_at_the_stop_level():
    """A bar that opens below the stop cannot fill at the stop price."""
    df = _frame([
        (100, 101, 99, 100),    # signal bar
        (100, 105, 99, 100),    # entry bar, no touch
        (70, 75, 65, 72),       # gap straight through the 90 stop
    ])
    out = simulate_forward(df, entry_index=0, stop_pct=0.10,
                           target_pct=0.20, max_hold_days=10)
    assert out.outcome == "stop"
    assert out.exit_price == pytest.approx(70.0)
    assert out.gross_return_pct == pytest.approx(-0.30)


def test_target_gap_fills_at_the_open_not_at_the_target_level():
    df = _frame([
        (100, 101, 99, 100),
        (100, 105, 99, 100),
        (130, 135, 129, 133),   # gap straight through the 120 target
    ])
    out = simulate_forward(df, entry_index=0, stop_pct=0.10,
                           target_pct=0.20, max_hold_days=10)
    assert out.outcome == "target"
    assert out.exit_price == pytest.approx(130.0)
    assert out.gross_return_pct == pytest.approx(0.30)


def test_ambiguous_bar_still_resolves_stop_first():
    """Range touches both levels without gapping: keep the conservative rule."""
    df = _frame([
        (100, 101, 99, 100),
        (100, 125, 85, 110),    # touches both 90 and 120 intraday
    ])
    out = simulate_forward(df, entry_index=0, stop_pct=0.10,
                           target_pct=0.20, max_hold_days=10)
    assert out.outcome == "stop"
    assert out.exit_price == pytest.approx(90.0)


# --------------------------------------------------------------------------
# Entry-bar exposure and labeler/simulator agreement
# --------------------------------------------------------------------------

def test_entry_bar_stop_is_not_skipped_by_the_portfolio_simulator():
    """The audit's headline reproduction: +20% reported where -10% occurred."""
    df = _frame([
        (100, 101, 99, 100),    # signal bar
        (100, 105, 85, 95),     # entry bar: low 85 takes out the 90 stop
        (100, 121, 99, 120),    # next bar would have hit the 120 target
    ])
    cfg = SimConfig(max_hold_days=10)
    res = _run(df, [_signal(df, 0, 0.10, 0.20)], cfg)

    assert res.n_trades == 1
    assert res.trade_returns[0] == pytest.approx(-0.10)


def test_labeler_and_simulator_agree_on_the_same_bars():
    df = _frame([
        (100, 101, 99, 100),
        (100, 105, 85, 95),
        (100, 121, 99, 120),
    ])
    label = simulate_forward(df, entry_index=0, stop_pct=0.10,
                             target_pct=0.20, max_hold_days=10)
    res = _run(df, [_signal(df, 0, 0.10, 0.20)], SimConfig(max_hold_days=10))

    assert res.trade_returns[0] == pytest.approx(label.gross_return_pct)


# --------------------------------------------------------------------------
# Causality of entry sizing and cash
# --------------------------------------------------------------------------

def test_entries_cannot_be_funded_by_the_same_days_exit_proceeds():
    """Cash released by today's intraday exit is not available at today's open."""
    a = _frame([
        (100, 101, 99, 100),    # A signal bar
        (100, 105, 99, 100),    # A entry bar
        (130, 135, 129, 133),   # A gaps through its 120 target
    ])
    b = _frame([
        (50, 51, 49, 50),
        (50, 51, 49, 50),       # B signal bar
        (50, 51, 49, 50),       # B entry bar - same day A exits
    ])
    cfg = SimConfig(
        initial_capital=10_000.0,
        risk_per_trade=1.0,
        max_position_pct=1.0,
        max_hold_days=10,
    )
    signals = [_signal(a, 0, 0.10, 0.20, "A"), _signal(b, 1, 0.10, 0.20, "B")]
    res = simulate({"A": a, "B": b}, signals, a.index[1:], FREE, cfg)

    # A consumes the whole account on day 1, so B is unaffordable on day 2.
    # Turnover proves B was never bought: 10,000 in + 13,000 out on a 10,000
    # account and nothing else.
    assert res.n_trades == 1
    assert res.turnover == pytest.approx(2.3)


def test_position_count_cap_is_evaluated_before_todays_exits():
    """A slot freed by today's exit cannot be reused at today's open."""
    a = _frame([
        (100, 101, 99, 100),
        (100, 105, 99, 100),
        (130, 135, 129, 133),   # A exits on day 2
    ])
    b = _frame([
        (50, 51, 49, 50),
        (50, 51, 49, 50),
        (50, 51, 49, 50),       # B wants day 2
    ])
    cfg = SimConfig(max_open_positions=1, max_hold_days=10)
    signals = [_signal(a, 0, 0.10, 0.20, "A"), _signal(b, 1, 0.10, 0.20, "B")]
    res = simulate({"A": a, "B": b}, signals, a.index[1:], FREE, cfg)

    # Only A's 10,000 entry and 13,000 exit may appear in gross traded value.
    assert res.n_trades == 1
    assert res.turnover == pytest.approx(0.23)


# --------------------------------------------------------------------------
# Equity-curve completeness
# --------------------------------------------------------------------------

def test_first_day_pnl_is_present_in_daily_returns():
    """Compounding the daily returns must reproduce the realised equity."""
    df = _frame([
        (100, 101, 99, 100),
        (100, 105, 85, 95),     # entry and stop on the first simulated day
        (100, 101, 99, 100),
    ])
    cfg = SimConfig(max_hold_days=10)
    res = _run(df, [_signal(df, 0, 0.10, 0.20)], cfg)

    compounded = float((1.0 + res.daily_returns).prod()) * cfg.initial_capital
    # 100 shares risking 1% of 100k, stopped for -10 a share => -1,000.
    assert compounded == pytest.approx(99_000.0)


def test_open_position_at_the_end_of_the_calendar_is_closed_and_recorded():
    df = _frame([
        (100, 101, 99, 100),
        (100, 105, 99, 100),    # entry, still open
        (102, 103, 101, 102),   # calendar ends with the trade live
    ])
    cfg = SimConfig(max_hold_days=50)
    res = _run(df, [_signal(df, 0, 0.10, 0.20)], cfg)

    assert res.n_trades == 1
    assert res.trade_returns[0] == pytest.approx(0.02)
