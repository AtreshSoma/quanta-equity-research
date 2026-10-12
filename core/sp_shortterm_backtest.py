#!/usr/bin/env python3
"""
SP_ShortTerm point-in-time backtest harness.

Replays the TECHNICAL + MOMENTUM + PATTERN core of SP_ShortTerm.py over a
historical window WITHOUT look-ahead. Fundamentals/sentiment/quality scores
that depend on a live `info` dict are held NEUTRAL (=50) at replay time
because we have no point-in-time fundamentals — this is the honest compromise
called out in the backtest-expert audit.

What this harness does:
  1. For each ticker, fetches `period` of daily OHLCV up front (one-shot, NOT
     re-fetched per bar — the slice fed to the scorer is what enforces PIT).
  2. Walks forward bar-by-bar from `warmup` to end.
  3. At each bar, slices `hist.iloc[:i+1]` and runs the same indicator +
     scoring + signal stack the live scanner uses, but always on data
     *available up to and including the close of bar i*.
  4. If a BUY/STRONG_BUY/ACCUMULATE fires AND there is no open position for
     that ticker, opens a trade at the NEXT bar's OPEN (no same-bar fill),
     adds round-trip friction (commission + slippage), and stores the
     trade plan (stop, target).
  5. Walks subsequent bars; exits on stop / target / max-hold (default 14d).
     If a bar's range covers both stop and target, exit at the stop
     (conservative — assume the worst).
  6. Aggregates a trade ledger, emits CSV + summary JSON, and feeds the
     summary into .github/skills/backtest-expert/scripts/evaluate_backtest.py
     for a structured Deploy/Refine/Abandon verdict.

Limitations (documented honestly per the skill's "no look-ahead, no
cherry-picking" mandate):
  - Fundamentals/sentiment held at neutral 50 — affects absolute composite
    score level but not the *technical* edge being tested.
  - No portfolio-level position-count cap is enforced (each ticker stands
    alone). Aggregate-level concurrency caps can be layered on top of the
    per-ticker ledger if needed.
  - Universe is whatever the caller passes; survivorship bias is the
    caller's responsibility. The default CLI universe is a small dummy
    set for smoke-testing — replace with a point-in-time S&P 500
    constituent list for serious work.

Usage:
    python sp_shortterm_backtest.py --tickers AAPL,MSFT,KO,JPM,NVDA \
        --start 2024-01-01 --end 2026-05-01 --max-hold-days 14

Outputs (timestamped under ./backtest_reports/):
    trades_<ts>.csv         Trade ledger (one row per closed trade)
    summary_<ts>.json       Aggregate metrics + per-year breakdown
    eval_<ts>.{json,md}     evaluate_backtest.py verdict (if available)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

# Import the scanner's own analyzers so we backtest the SAME math the live
# scanner produces — avoids any "the backtest tested a different strategy"
# class of false negatives.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from SP_ShortTerm import (  # noqa: E402
    AdvancedTechnicalAnalyzer,
    Config,
    StrategicStockAnalyzer,
    TradingSignal,
)

logger = logging.getLogger("sp_shortterm_backtest")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    ticker: str
    entry_date: str
    entry_price: float
    stop_loss: float
    primary_target: float
    exit_date: str
    exit_price: float
    exit_reason: str  # 'target' | 'stop' | 'time'
    bars_held: int
    gross_return_pct: float  # before friction
    net_return_pct: float  # after round-trip friction
    overall_score: float
    signal: str


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------


class ShortTermBacktest:
    """Point-in-time replay of SP_ShortTerm's technical/momentum/pattern core."""

    def __init__(self, max_hold_days: int = 14, warmup_bars: int = 220,
                 friction_pct: Optional[float] = None) -> None:
        self.max_hold_days = max_hold_days
        self.warmup_bars = warmup_bars
        # Use the scanner's own friction model so live and backtest agree.
        self.friction_pct = (
            friction_pct if friction_pct is not None
            else Config.FRICTION_ROUND_TRIP_PCT
        )
        self.tech = AdvancedTechnicalAnalyzer()
        # StrategicStockAnalyzer carries the scoring + signal methods. We only
        # need the *methods*, not its scan loop or market-regime detection, so
        # we instantiate with default capital and call the private helpers.
        self.analyzer = StrategicStockAnalyzer(capital=Config.DEFAULT_CAPITAL)

    # ---- core single-bar evaluation --------------------------------------

    def _score_slice(self, hist_slice: pd.DataFrame) -> Optional[dict]:
        """Compute composite score + signal on a point-in-time slice.

        Returns None if data is insufficient or the signal floor is missed.
        Fundamentals/quality/sentiment scores are NEUTRAL=50 (no PIT data).
        """
        if len(hist_slice) < 60:
            return None
        indicators = self.tech.calculate_comprehensive_indicators(hist_slice)
        if not indicators:
            return None

        returns = indicators.get("hist_returns", np.array([]))
        # Patterns
        try:
            patterns = self.tech.detect_patterns(hist_slice, indicators)
        except Exception:
            patterns = []

        # Hard-block bearish patterns (mirror scanner)
        if any(p in {"Death Cross", "Death Cross (Recent)"} for p in patterns):
            return None

        current_price = float(hist_slice["Close"].iloc[-1])

        technical_score = self.analyzer._calculate_technical_score(indicators, current_price)
        momentum_score = self.analyzer._calculate_momentum_score(indicators, returns)
        pattern_score = self.analyzer._calculate_pattern_score(patterns, indicators)

        # Fundamentals/quality/sentiment are unavailable point-in-time; hold
        # neutral so the technical edge can be tested in isolation. This is
        # the honest compromise documented in the harness docstring.
        NEUTRAL = 50.0
        overall_score = (
            technical_score * 0.28
            + NEUTRAL * 0.20      # fundamental
            + momentum_score * 0.22
            + NEUTRAL * 0.12      # quality
            + NEUTRAL * 0.10      # sentiment
            + pattern_score * 0.08
        )

        if overall_score < Config.MIN_SCORE:
            return None

        signal, confidence = self.analyzer._determine_signal(
            overall_score, indicators, technical_score, momentum_score
        )

        # Compute entry/exit strategy at the close of the signal bar so the
        # plan is fixed BEFORE we look at the next bar.
        entry_strategy = self.analyzer._calculate_entry_strategy(current_price, indicators)
        exit_strategy = self.analyzer._calculate_exit_strategy(
            entry_strategy["optimal_entry"], indicators, signal, overall_score
        )
        return {
            "signal": signal,
            "confidence": confidence,
            "overall_score": overall_score,
            "stop_loss": exit_strategy["stop_loss"],
            "primary_target": exit_strategy["primary_target"],
            "current_price": current_price,
        }

    # ---- per-ticker replay -----------------------------------------------

    def replay_ticker(self, ticker: str, hist: pd.DataFrame) -> List[Trade]:
        """Walk bar-by-bar and produce a list of closed trades for `ticker`."""
        trades: List[Trade] = []
        if len(hist) <= self.warmup_bars + 2:
            return trades

        i = self.warmup_bars
        n = len(hist)
        open_trade: Optional[dict] = None

        while i < n - 1:
            bar_date = hist.index[i]

            if open_trade is None:
                # Evaluate signal on data up to and including bar i (close).
                plan = self._score_slice(hist.iloc[: i + 1])
                if plan is not None and plan["signal"] in (
                    TradingSignal.STRONG_BUY.signal_name,
                    TradingSignal.BUY.signal_name,
                    TradingSignal.ACCUMULATE.signal_name,
                ):
                    # Enter at NEXT bar's OPEN (no same-bar look-ahead).
                    next_open = float(hist["Open"].iloc[i + 1])
                    # Slippage already baked into friction at exit; for a
                    # conservative entry we pay half the round-trip on the way
                    # in. The full friction is debited again at exit so net
                    # return reflects the full round-trip cost.
                    entry_fill = next_open * (1.0 + self.friction_pct / 2.0)
                    # Re-anchor stop/target proportionally to the actual fill
                    # so the trade plan stays internally consistent.
                    plan_entry = plan["current_price"]
                    if plan_entry > 0:
                        ratio = entry_fill / plan_entry
                        stop = plan["stop_loss"] * ratio
                        target = plan["primary_target"] * ratio
                    else:
                        stop, target = plan["stop_loss"], plan["primary_target"]
                    open_trade = {
                        "entry_idx": i + 1,
                        "entry_date": hist.index[i + 1],
                        "entry_price": entry_fill,
                        "stop_loss": stop,
                        "primary_target": target,
                        "overall_score": plan["overall_score"],
                        "signal": plan["signal"],
                    }
                i += 1
                continue

            # Trade is open: check bar i for stop/target/time exit.
            bar_low = float(hist["Low"].iloc[i])
            bar_high = float(hist["High"].iloc[i])
            bars_held = i - open_trade["entry_idx"]
            stop = open_trade["stop_loss"]
            target = open_trade["primary_target"]

            exit_reason: Optional[str] = None
            exit_price: Optional[float] = None

            # Conservative: if both touched same bar, assume stop hit first.
            if bar_low <= stop:
                exit_reason = "stop"
                exit_price = stop
            elif bar_high >= target:
                exit_reason = "target"
                exit_price = target
            elif bars_held >= self.max_hold_days:
                # Force-close at next open
                if i + 1 < n:
                    exit_reason = "time"
                    exit_price = float(hist["Open"].iloc[i + 1])
                else:
                    exit_reason = "time"
                    exit_price = float(hist["Close"].iloc[i])

            if exit_reason is not None:
                # Apply slippage on exit (sell hit half round-trip).
                exit_fill = exit_price * (1.0 - self.friction_pct / 2.0)
                gross_pct = (exit_price / open_trade["entry_price"] - 1.0) * 100.0
                net_pct = (exit_fill / open_trade["entry_price"] - 1.0) * 100.0
                trades.append(Trade(
                    ticker=ticker,
                    entry_date=str(open_trade["entry_date"].date()),
                    entry_price=round(open_trade["entry_price"], 4),
                    stop_loss=round(stop, 4),
                    primary_target=round(target, 4),
                    exit_date=str(bar_date.date()),
                    exit_price=round(exit_fill, 4),
                    exit_reason=exit_reason,
                    bars_held=bars_held,
                    gross_return_pct=round(gross_pct, 4),
                    net_return_pct=round(net_pct, 4),
                    overall_score=round(open_trade["overall_score"], 2),
                    signal=open_trade["signal"],
                ))
                open_trade = None
            i += 1

        return trades

    # ---- driver ----------------------------------------------------------

    def run(self, tickers: List[str], start: str, end: str) -> List[Trade]:
        all_trades: List[Trade] = []
        for tk in tickers:
            try:
                hist = yf.download(
                    tk, start=start, end=end, progress=False, auto_adjust=False
                )
                if hist is None or hist.empty:
                    logger.warning("%s: no data", tk)
                    continue
                # Normalise to single-level columns (yfinance occasionally
                # returns a MultiIndex when downloading a single ticker).
                if isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
                hist = hist[["Open", "High", "Low", "Close", "Volume"]].dropna()
                trades = self.replay_ticker(tk, hist)
                all_trades.extend(trades)
                print(f"  {tk:6}  {len(trades):3d} trades")
            except Exception as e:
                logger.warning("%s: replay failed: %s", tk, e)
        return all_trades


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarise(trades: List[Trade]) -> dict:
    if not trades:
        return {"total_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = df[df["net_return_pct"] > 0]
    losses = df[df["net_return_pct"] <= 0]
    total = len(df)
    win_rate = 100.0 * len(wins) / total if total else 0.0
    avg_win = float(wins["net_return_pct"].mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses["net_return_pct"].mean())) if len(losses) else 0.0
    expectancy = (win_rate / 100.0) * avg_win - (1 - win_rate / 100.0) * avg_loss

    # Per-year breakdown
    df["year"] = pd.to_datetime(df["exit_date"]).dt.year
    per_year = df.groupby("year")["net_return_pct"].agg(["count", "mean", "sum"]).to_dict("index")
    per_year = {int(k): {kk: float(vv) for kk, vv in v.items()} for k, v in per_year.items()}

    # Equity curve with fixed-fractional 1% risk-per-trade sizing — this is
    # the realistic account-level DD. Compounding raw net_return_pct as if
    # each trade was 100% of capital overstates DD by 5-10x for a strategy
    # that intends to risk 1% per name.
    RISK_PER_TRADE = 0.01  # 1% account risk per trade (industry standard)
    df_sorted = df.sort_values("exit_date").copy()
    # Express each trade in R-multiples: R = net_return_pct / avg_loss_pct
    # (avg_loss_pct approximates the per-trade stop distance). For trades
    # that hit the stop this is -1R; for winners it's typically 1-3R.
    r_unit = avg_loss if avg_loss > 0 else 1.0
    df_sorted["r_multiple"] = df_sorted["net_return_pct"] / r_unit
    df_sorted["equity_pct"] = (1.0 + df_sorted["r_multiple"] * RISK_PER_TRADE).cumprod()
    rolling_max = df_sorted["equity_pct"].cummax()
    drawdown = (df_sorted["equity_pct"] / rolling_max - 1.0) * 100.0
    max_dd_pct = float(abs(drawdown.min())) if len(drawdown) else 0.0

    years_tested = int(df["year"].nunique())

    return {
        "total_trades": int(total),
        "win_rate_pct": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "expectancy_pct_per_trade": round(expectancy, 3),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "years_tested": years_tested,
        "exit_reason_breakdown": df["exit_reason"].value_counts().to_dict(),
        "per_year": per_year,
    }


def run_evaluator(summary: dict, output_dir: Path) -> Optional[Path]:
    """Invoke .github/skills/backtest-expert/scripts/evaluate_backtest.py."""
    repo_root = _THIS_DIR.parents[1]  # stock-analysis/core/ -> repo root
    script = (
        repo_root / ".github" / "skills" / "backtest-expert"
        / "scripts" / "evaluate_backtest.py"
    )
    if not script.exists():
        logger.warning("evaluate_backtest.py not found at %s", script)
        return None
    import subprocess
    cmd = [
        sys.executable, str(script),
        "--total-trades", str(summary["total_trades"]),
        "--win-rate", str(summary["win_rate_pct"]),
        "--avg-win-pct", str(summary["avg_win_pct"]),
        "--avg-loss-pct", str(summary["avg_loss_pct"]),
        "--max-drawdown-pct", str(summary["max_drawdown_pct"]),
        "--years-tested", str(max(1, summary["years_tested"])),
        # Backtest-tunable degrees of freedom (not the frozen strategy weights):
        # friction_pct, max_hold_days, warmup_bars, MIN_SCORE (inherited).
        "--num-parameters", "4",
        "--slippage-tested",
        "--output-dir", str(output_dir),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            logger.warning("evaluator failed: %s", res.stderr)
            return None
        print(res.stdout)
        return output_dir
    except Exception as e:
        logger.warning("evaluator invocation failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="AAPL,MSFT,KO,JPM,NVDA,GOOGL,META,AMZN",
                    help="Comma-separated ticker list (default: 8-name smoke universe)")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--max-hold-days", type=int, default=14)
    ap.add_argument("--warmup-bars", type=int, default=220)
    ap.add_argument("--output-dir", default=str(_THIS_DIR.parent / "backtest_reports"))
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    bt = ShortTermBacktest(max_hold_days=args.max_hold_days, warmup_bars=args.warmup_bars)
    print(f"Running backtest: {len(tickers)} tickers, {args.start} → {args.end}, "
          f"hold≤{args.max_hold_days}d, friction={bt.friction_pct*100:.2f}% round-trip")
    trades = bt.run(tickers, args.start, args.end)

    summary = summarise(trades)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))

    # Persist
    trades_path = out_dir / f"trades_{ts}.csv"
    summary_path = out_dir / f"summary_{ts}.json"
    pd.DataFrame([asdict(t) for t in trades]).to_csv(trades_path, index=False)
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nLedger:  {trades_path}")
    print(f"Summary: {summary_path}")

    # Hand off to evaluator
    if summary.get("total_trades", 0) > 0:
        print("\n--- evaluate_backtest.py ---")
        run_evaluator(summary, out_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
