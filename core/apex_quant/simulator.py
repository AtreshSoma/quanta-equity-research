"""Portfolio simulator: turns signals into a daily, cost-aware equity curve.

Realism features (Phase 3 / Phase 6):
  * Enter at the NEXT bar's open after the signal date (no same-bar fill).
  * Risk-first sizing: dollars risked per trade = ``risk_per_trade`` * equity,
    shares = floor(risk_dollars / (entry * stop_pct)).
  * Integer shares only; drop the setup if it can't buy >= 1 share within the
    risk and position caps (a real small account cannot fractionally fill).
  * Position-notional cap and a liquidity participation cap vs. ADV.
  * Max concurrent positions cap.
  * Conservative same-bar exits: stop is checked before target.
  * Commission + half-spread + volatility/liquidity slippage on both sides.
  * Portfolio kill-switch: if drawdown breaches ``kill_switch_dd`` the book is
    flattened at that day's close and no new trades open for the rest of the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .costs import CostModel
from .labeling import long_bar_exit
from .strategy import Signal


@dataclass
class SimConfig:
    initial_capital: float = 100_000.0
    risk_per_trade: float = 0.01          # 1% of equity risked per trade
    max_position_pct: float = 0.20        # <=20% of equity in one name
    max_participation: float = 0.02       # order <=2% of ADV
    max_open_positions: int = 8
    kill_switch_dd: float = 0.25          # flatten if 25% drawdown breached
    max_hold_days: int = 20


@dataclass
class _Position:
    ticker: str
    shares: int
    entry_price: float
    stop_price: float
    target_price: float
    atr_pct: float
    adv_dollars: float
    entry_iloc: int                       # positional index in the ticker frame
    cost_pct_entry: float


@dataclass
class SimResult:
    daily_returns: pd.Series
    trade_returns: List[float]
    turnover: float
    n_trades: int
    killed: bool = False


def _ticker_lookup(ohlcv: pd.DataFrame) -> Dict[pd.Timestamp, int]:
    return {ts: i for i, ts in enumerate(ohlcv.index)}


def simulate(
    data: Dict[str, pd.DataFrame],
    signals: List[Signal],
    calendar: pd.DatetimeIndex,
    cost_model: CostModel,
    cfg: SimConfig,
) -> SimResult:
    """Run a day-by-day portfolio simulation over ``calendar``.

    ``data``     : ticker -> full OHLCV frame (DatetimeIndex).
    ``signals``  : entries to consider (decision-bar signals).
    ``calendar`` : ordered trading days to simulate (the OOS window).
    """
    iloc_maps = {tk: _ticker_lookup(df) for tk, df in data.items()}

    # Map each signal to its entry date (next bar after the signal date).
    entries_by_date: Dict[pd.Timestamp, List[Signal]] = {}
    for sig in signals:
        df = data.get(sig.ticker)
        if df is None:
            continue
        imap = iloc_maps[sig.ticker]
        i = imap.get(pd.Timestamp(sig.signal_date).normalize())
        if i is None or i + 1 >= len(df):
            continue
        entry_date = df.index[i + 1]
        entries_by_date.setdefault(entry_date, []).append(sig)

    cash = cfg.initial_capital
    positions: List[_Position] = []
    equity_curve: List[float] = []
    dates: List[pd.Timestamp] = []
    trade_returns: List[float] = []
    gross_traded = 0.0
    killed = False
    peak_equity = cfg.initial_capital

    def mark_to_market(day: pd.Timestamp) -> float:
        val = cash
        for p in positions:
            df = data[p.ticker]
            imap = iloc_maps[p.ticker]
            j = imap.get(day)
            if j is None:
                j = df.index.searchsorted(day, side="right") - 1
            px = float(df["Close"].iloc[j]) if j >= p.entry_iloc else p.entry_price
            val += p.shares * px
        return val

    equity = cfg.initial_capital
    for day in calendar:
        # Entries use prior-close equity and cash available BEFORE intraday exits.
        # Conservatively, same-day exit proceeds cannot fund opening entries.
        if not killed and day in entries_by_date:
            for sig in entries_by_date[day]:
                if len(positions) >= cfg.max_open_positions:
                    break
                if any(p.ticker == sig.ticker for p in positions):
                    continue
                df = data[sig.ticker]
                j = iloc_maps[sig.ticker].get(day)
                if j is None:
                    continue
                entry_price = float(df["Open"].iloc[j])
                if entry_price <= 0 or sig.stop_pct <= 0:
                    continue

                risk_dollars = equity * cfg.risk_per_trade
                shares = int(risk_dollars / (entry_price * sig.stop_pct))
                # Position notional cap.
                max_shares_notional = int((equity * cfg.max_position_pct) / entry_price)
                shares = min(shares, max_shares_notional)
                # Liquidity participation cap.
                max_shares_liq = int((sig.adv_dollars * cfg.max_participation) / entry_price)
                shares = min(shares, max_shares_liq)
                # Cash cap.
                shares = min(shares, int(cash / entry_price))
                if shares < 1:
                    continue

                cost_entry = cost_model.side_cost_pct(
                    entry_price, shares, sig.atr_pct, sig.adv_dollars
                )
                outlay = shares * entry_price * (1.0 + cost_entry)
                if outlay > cash:
                    continue
                cash -= outlay
                gross_traded += shares * entry_price
                positions.append(
                    _Position(
                        ticker=sig.ticker,
                        shares=shares,
                        entry_price=entry_price,
                        stop_price=entry_price * (1.0 - sig.stop_pct),
                        target_price=entry_price * (1.0 + sig.target_pct),
                        atr_pct=sig.atr_pct,
                        adv_dollars=sig.adv_dollars,
                        entry_iloc=j,
                        cost_pct_entry=cost_entry,
                    )
                )

        # 1) Process exits on open positions using today's bar.
        still_open: List[_Position] = []
        for p in positions:
            df = data[p.ticker]
            imap = iloc_maps[p.ticker]
            j = imap.get(day)
            if j is None:
                still_open.append(p)
                continue
            low = float(df["Low"].iloc[j])
            high = float(df["High"].iloc[j])
            close = float(df["Close"].iloc[j])
            held = j - p.entry_iloc

            exit_price: Optional[float] = None
            fill = long_bar_exit(float(df["Open"].iloc[j]), low, high,
                                 p.stop_price, p.target_price)
            if fill:
                exit_price = fill[1]
            elif held >= cfg.max_hold_days or day == calendar[-1]:
                exit_price = close

            if exit_price is None:
                still_open.append(p)
                continue

            cost_exit = cost_model.side_cost_pct(
                exit_price, p.shares, p.atr_pct, p.adv_dollars
            )
            proceeds = p.shares * exit_price * (1.0 - cost_exit)
            cash += proceeds
            gross_traded += p.shares * exit_price
            net_ret = exit_price * (1 - cost_exit) / (p.entry_price * (1 + p.cost_pct_entry)) - 1
            trade_returns.append(net_ret)
        positions = still_open

        # 2) Mark to market and update drawdown / kill-switch.
        equity = mark_to_market(day)
        peak_equity = max(peak_equity, equity)
        if not killed and peak_equity > 0 and equity / peak_equity - 1.0 <= -cfg.kill_switch_dd:
            # Flatten everything at today's close.
            for p in positions:
                df = data[p.ticker]
                j = iloc_maps[p.ticker].get(day)
                px = float(df["Close"].iloc[j]) if j is not None else p.entry_price
                cost_exit = cost_model.side_cost_pct(px, p.shares, p.atr_pct, p.adv_dollars)
                cash += p.shares * px * (1.0 - cost_exit)
                gross_traded += p.shares * px
                trade_returns.append(
                    px * (1 - cost_exit) / (p.entry_price * (1 + p.cost_pct_entry)) - 1
                )
            positions = []
            killed = True
            equity = cash

        equity_curve.append(equity)
        dates.append(day)

    eq = pd.Series(equity_curve, index=pd.DatetimeIndex(dates))
    daily_returns = eq.pct_change().fillna(0.0)
    if len(eq):
        daily_returns.iloc[0] = eq.iloc[0] / cfg.initial_capital - 1.0
    turnover = gross_traded / cfg.initial_capital if cfg.initial_capital > 0 else 0.0
    return SimResult(
        daily_returns=daily_returns,
        trade_returns=trade_returns,
        turnover=turnover,
        n_trades=len(trade_returns),
        killed=killed,
    )
