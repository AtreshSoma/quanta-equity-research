#!/usr/bin/env python3
"""
SP_ShortTerm point-in-time backtest harness.

Replays the TECHNICAL + MOMENTUM + PATTERN core of SP_ShortTerm.py over a
historical window WITHOUT look-ahead. Fundamentals/quality/analyst legs depend
on a live `info` dict and are held NEUTRAL (=50) because there is no
point-in-time source for them; the composite itself comes from the scanner's own
``composite_score`` so the weights can never drift from the live model.

What this harness does:
  1. Downloads split/dividend-adjusted daily bars from well before ``start`` so
     the indicator warm-up never eats the test window, plus SPY for the
     benchmark.
  2. Walks forward bar-by-bar from ``start``; at each bar it scores
     ``hist.iloc[:i+1]`` with the scanner's indicator, scoring and signal stack,
     i.e. only data available at that bar's close.
  3. On a BUY / STRONG_BUY / ACCUMULATE with no open position it enters at the
     NEXT bar's open with half the round-trip friction. The scanner's stop and
     target are planned at the signal close relative to its planned entry; the
     same percentage distances are applied to the actual fill.
  4. Exits on stop, target or max-hold. A bar that gaps through either level
     fills at its open; a bar touching both counts as the stop. A trade still
     open when the data ends is marked to the last close ("end"), not dropped.
  5. Scores every trade against SPY over the same fills (open to the exit
     day's close, or to its open for a time exit), and
     replays a RANDOM-ENTRY baseline per ticker: the same number of trades at
     random bars with the same stop/target/hold rules. If the signal's timing
     carries information, its trades must beat those.
  6. Aggregates trades into weekly entry cohorts (trades opened the same week
     are not independent) and reports Newey-West t and block-bootstrap intervals
     on that series, a signal-minus-random difference resampled by calendar
     week, and probabilistic / deflated Sharpe ratios.

Limitations (stated so they are not mistaken for results):
  - By default the fundamental, quality and analyst legs are neutral 50, so the
    replay tests the price-based 58% of the composite. With ``--pit-fundamentals``
    the fundamental and quality legs are scored by the scanner's own cards from
    SEC filings known on each bar (apex_quant/pit_fundamentals.py), covering 90%;
    the analyst leg (rating, short interest) has no point-in-time source and
    stays neutral.
  - No regime multiplier, peer re-scoring, funding overlay or portfolio caps:
    each ticker stands alone.
  - The default universe is apex_quant's static present-day list: survivorship
    bias applies. Compare signal vs random vs SPY, not absolute returns.
  - Each bar re-scores the full history up to it (exactly what the live scanner
    sees), which is quadratic in history length: fine for decades on dozens of
    names, slow for thousands.

Usage:
    python sp_shortterm_backtest.py --universe liquid --start 2016-01-01 --end 2026-09-30
    python sp_shortterm_backtest.py --tickers AAPL,MSFT --start 2024-01-01 --end 2025-01-01
    python sp_shortterm_backtest.py --universe liquid --pit-fundamentals --start 2016-01-01

Outputs (timestamped under --output-dir, default ./backtest_reports/):
    trades_<ts>.csv    every signal and random-baseline trade
    summary_<ts>.json  aggregate metrics, inference and verdict
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
from apex_quant import stats as S  # noqa: E402

logger = logging.getLogger("sp_shortterm_backtest")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

BENCHMARK = "SPY"
# Calendar days fetched before ``start`` so the 200-day indicators are warm on day one.
HISTORY_BUFFER_DAYS = 450
_LONG_SIGNALS = frozenset({TradingSignal.STRONG_BUY.signal_name, TradingSignal.BUY.signal_name,
                           TradingSignal.ACCUMULATE.signal_name})


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    ticker: str
    kind: str  # 'signal' | 'random'
    entry_date: str
    entry_price: float
    stop_loss: float
    primary_target: float
    exit_date: str
    exit_price: float
    exit_reason: str  # 'target' | 'stop' | 'time' | 'end' (data ran out; marked to close)
    bars_held: int
    gross_return_pct: float  # before friction
    net_return_pct: float  # after round-trip friction
    benchmark_return_pct: Optional[float]  # SPY, entry-day open to exit-day close
    excess_return_pct: Optional[float]  # net minus benchmark
    overall_score: float
    signal: str


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------


def download(ticker: str, start: str, end: str, with_split_close: bool = False) -> pd.DataFrame:
    """Adjusted daily OHLCV from ``HISTORY_BUFFER_DAYS`` before ``start`` to ``end``.

    ``with_split_close`` adds ``SplitClose``: the close adjusted for splits but
    not dividends, the units SEC per-share figures convert to. OHLC are then
    dividend-adjusted from the same download, exactly as ``auto_adjust`` does.
    """
    fetch_from = (pd.Timestamp(start) - pd.Timedelta(days=HISTORY_BUFFER_DAYS)).date().isoformat()
    hist = yf.download(ticker, start=fetch_from, end=end, progress=False,
                       auto_adjust=not with_split_close, threads=False)
    if hist is None or hist.empty:
        return pd.DataFrame()
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)
    if not with_split_close:
        return hist[["Open", "High", "Low", "Close", "Volume"]].dropna()
    raw = hist[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].dropna()
    ratio = raw["Adj Close"] / raw["Close"]
    out = pd.DataFrame({c: raw[c] * ratio for c in ("Open", "High", "Low")}, index=raw.index)
    out["Close"] = raw["Adj Close"]
    out["Volume"] = raw["Volume"]
    out["SplitClose"] = raw["Close"]
    return out


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
        # need the *methods*, not its scan loop.
        self.analyzer = StrategicStockAnalyzer(capital=Config.DEFAULT_CAPITAL)

    # ---- core single-bar evaluation --------------------------------------

    def _score_slice(self, hist_slice: pd.DataFrame, gated: bool = True,
                     info: Optional[dict] = None) -> Optional[dict]:
        """Score and plan a trade on a point-in-time slice.

        ``gated=False`` skips the score floor and signal check (the random
        baseline enters regardless) but still uses the scanner's own levels.
        ``info`` is a point-in-time fundamentals payload (yfinance units); when
        given, the fundamental and quality legs are scored by the scanner's own
        cards instead of defaulting to 50.
        """
        if len(hist_slice) < 60:
            return None
        indicators = self.tech.calculate_comprehensive_indicators(hist_slice)
        if not indicators:
            return None

        returns = indicators.get("hist_returns", np.array([]))
        try:
            patterns = self.tech.detect_patterns(hist_slice, indicators)
        except Exception:
            patterns = []

        # Hard-block bearish patterns (mirror scanner)
        if gated and any(p in {"Death Cross", "Death Cross (Recent)"} for p in patterns):
            return None

        current_price = float(hist_slice["Close"].iloc[-1])
        technical_score = self.analyzer._calculate_technical_score(indicators, current_price)
        momentum_score = self.analyzer._calculate_momentum_score(indicators, returns)
        pattern_score = self.analyzer._calculate_pattern_score(patterns, indicators)
        # The scanner's own weights; legs without point-in-time data default to 50.
        components = {"technical": technical_score, "momentum": momentum_score,
                      "pattern": pattern_score}
        if info:
            components["fundamental"] = self.analyzer._calculate_fundamental_score(info)
            components["quality"] = self.analyzer._calculate_quality_score(info)
        overall_score = self.analyzer.composite_score(components)

        if gated and overall_score < Config.MIN_SCORE:
            return None
        signal, confidence = self.analyzer._determine_signal(
            overall_score, indicators, technical_score, momentum_score
        )
        if gated and signal not in _LONG_SIGNALS:
            return None

        # Plan fixed at the signal bar's close, BEFORE looking at the next bar.
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
            "planned_entry": float(entry_strategy["optimal_entry"]),
        }

    # ---- per-ticker replay -----------------------------------------------

    def first_bar(self, hist: pd.DataFrame, start: Optional[str]) -> int:
        """First bar that may open a trade: inside the window and past the warm-up."""
        first = self.warmup_bars
        if start is not None:
            first = max(first, int(hist.index.searchsorted(pd.Timestamp(start))))
        return first

    def replay_ticker(self, ticker: str, hist: pd.DataFrame, start: Optional[str] = None,
                      forced_entries: Optional[Sequence[int]] = None,
                      benchmark: Optional[pd.DataFrame] = None,
                      fundamentals=None) -> List[Trade]:
        """Walk bar-by-bar and produce the closed trades for ``ticker``.

        With ``forced_entries`` (bar indices) the signal is ignored and a trade
        opens at each listed bar that is not already inside a trade.
        ``fundamentals`` (a ``PITFundamentals``) needs ``hist['SplitClose']``.
        """
        split_close = hist["SplitClose"] if "SplitClose" in hist.columns else None
        if split_close is not None:
            hist = hist.drop(columns=["SplitClose"])

        def info_at(i: int) -> Optional[dict]:
            if fundamentals is None or split_close is None:
                return None
            return fundamentals.info_at(hist.index[i], float(split_close.iloc[i])) or None

        trades: List[Trade] = []
        n = len(hist)
        i = self.first_bar(hist, start)
        if n <= i + 2:
            return trades
        forced = set(forced_entries) if forced_entries is not None else None
        kind = "signal" if forced is None else "random"
        open_trade: Optional[dict] = None

        while i < n - 1:
            bar_date = hist.index[i]

            if open_trade is None:
                plan = None
                # Pass ``info`` only when there is one, so the default replay
                # makes exactly the call it always made.
                extra = {}
                if fundamentals is not None and (forced is None or i in forced):
                    payload = info_at(i)
                    if payload:
                        extra["info"] = payload
                if forced is None:
                    plan = self._score_slice(hist.iloc[: i + 1], **extra)
                elif i in forced:
                    plan = self._score_slice(hist.iloc[: i + 1], gated=False, **extra)
                if plan is not None:
                    # Enter at NEXT bar's OPEN, paying half the round trip.
                    entry_fill = float(hist["Open"].iloc[i + 1]) * (1.0 + self.friction_pct / 2.0)
                    # Keep the planned percentage distances from the planned entry.
                    planned = plan.get("planned_entry") or plan["current_price"]
                    ratio = entry_fill / planned if planned > 0 else 1.0
                    open_trade = {
                        "entry_idx": i + 1,
                        "entry_date": hist.index[i + 1],
                        "entry_price": entry_fill,
                        "stop_loss": plan["stop_loss"] * ratio,
                        "primary_target": plan["primary_target"] * ratio,
                        "overall_score": plan["overall_score"],
                        "signal": plan["signal"],
                    }
                i += 1
                continue

            # Trade is open: check bar i for stop/target/time exit.
            bar_open = float(hist["Open"].iloc[i])
            bar_low = float(hist["Low"].iloc[i])
            bar_high = float(hist["High"].iloc[i])
            bars_held = i - open_trade["entry_idx"]
            stop = open_trade["stop_loss"]
            target = open_trade["primary_target"]

            exit_reason: Optional[str] = None
            exit_price: Optional[float] = None
            exit_day = bar_date
            if bar_open <= stop:
                # Gapped through the stop: a stop order fills at the open.
                exit_reason, exit_price = "stop", bar_open
            elif bar_low <= stop:
                # Conservative: if both touched same bar, assume stop hit first.
                exit_reason, exit_price = "stop", stop
            elif bar_open >= target:
                # Gapped through the target: a resting limit sell fills at the open.
                exit_reason, exit_price = "target", bar_open
            elif bar_high >= target:
                exit_reason, exit_price = "target", target
            elif bars_held >= self.max_hold_days:
                # Sold at the next open (i + 1 < n always holds inside the loop).
                exit_reason = "time"
                exit_price, exit_day = float(hist["Open"].iloc[i + 1]), hist.index[i + 1]

            if exit_reason is not None:
                trades.append(self._close(ticker, kind, open_trade, exit_reason, exit_price,
                                          exit_day, bars_held, benchmark))
                open_trade = None
            i += 1

        if open_trade is not None:
            # Still open when the data ends: marked to the last close, not dropped,
            # so the sample is not biased toward trades that happened to finish.
            trades.append(self._close(ticker, kind, open_trade, "end", float(hist["Close"].iloc[-1]),
                                      hist.index[-1], n - 1 - open_trade["entry_idx"], benchmark))
        return trades

    def _close(self, ticker: str, kind: str, trade: dict, reason: str, price: float,
               day: pd.Timestamp, bars_held: int, benchmark: Optional[pd.DataFrame]) -> Trade:
        """The closed-trade record, net of the exit half of the friction."""
        fill = price * (1.0 - self.friction_pct / 2.0)
        net_pct = (fill / trade["entry_price"] - 1.0) * 100.0
        bench = benchmark_return(benchmark, trade["entry_date"], day, at_open=reason == "time")
        return Trade(
            ticker=ticker,
            kind=kind,
            entry_date=str(trade["entry_date"].date()),
            entry_price=round(trade["entry_price"], 4),
            stop_loss=round(trade["stop_loss"], 4),
            primary_target=round(trade["primary_target"], 4),
            exit_date=str(day.date()),
            exit_price=round(fill, 4),
            exit_reason=reason,
            bars_held=bars_held,
            gross_return_pct=round((price / trade["entry_price"] - 1.0) * 100.0, 4),
            net_return_pct=round(net_pct, 4),
            benchmark_return_pct=None if bench is None else round(bench, 4),
            excess_return_pct=None if bench is None else round(net_pct - bench, 4),
            overall_score=round(trade["overall_score"], 2),
            signal=trade["signal"],
        )

    def random_entries(self, ticker: str, hist: pd.DataFrame, start: Optional[str], k: int,
                       seed: int = 0) -> List[int]:
        """``k`` distinct random entry bars in the test window, seeded per ticker."""
        first, last = self.first_bar(hist, start), len(hist) - 2
        if k <= 0 or last <= first:
            return []
        rng = np.random.default_rng([seed, zlib.crc32(ticker.encode())])
        pool = np.arange(first, last + 1)
        return sorted(int(x) for x in rng.choice(pool, size=min(k, len(pool)), replace=False))


def benchmark_return(bench: Optional[pd.DataFrame], entry: pd.Timestamp,
                     exit_: pd.Timestamp, at_open: bool = False) -> Optional[float]:
    """SPY return (%) from the entry day's open to the exit day's close (or open)."""
    if bench is None or bench.empty or entry not in bench.index or exit_ not in bench.index:
        return None
    o, c = float(bench.at[entry, "Open"]), float(bench.at[exit_, "Open" if at_open else "Close"])
    return (c / o - 1.0) * 100.0 if o > 0 else None


# ---- parallel driver ---------------------------------------------------------

_WORKER: Dict[str, object] = {}


def _init_worker(max_hold: int, warmup: int, bench: pd.DataFrame, pit: bool = False) -> None:
    # Quiet: the analyzer prints a banner on construction.
    with open(os.devnull, "w") as devnull:
        stdout, sys.stdout = sys.stdout, devnull
        try:
            _WORKER["bt"] = ShortTermBacktest(max_hold_days=max_hold, warmup_bars=warmup)
        finally:
            sys.stdout = stdout
    _WORKER["bench"] = bench
    _WORKER["pit"] = pit


def _replay_one(args: Tuple[str, str, str, int, bool]) -> Tuple[str, List[Trade], Optional[str]]:
    ticker, start, end, seed, baseline = args
    bt: ShortTermBacktest = _WORKER["bt"]  # type: ignore[assignment]
    try:
        pit = bool(_WORKER.get("pit"))
        hist = download(ticker, start, end, with_split_close=pit)
        if hist.empty:
            return ticker, [], "no data"
        bench = _WORKER["bench"]
        fundamentals = None
        if pit:
            from apex_quant.pit_fundamentals import load_pit_fundamentals
            fundamentals = load_pit_fundamentals(ticker)
        trades = bt.replay_ticker(ticker, hist, start, benchmark=bench, fundamentals=fundamentals)
        if baseline and trades:
            entries = bt.random_entries(ticker, hist, start, len(trades), seed)
            trades += bt.replay_ticker(ticker, hist, start, forced_entries=entries, benchmark=bench,
                                       fundamentals=fundamentals)
        return ticker, trades, None
    except Exception as exc:  # noqa: BLE001 - one bad ticker must not stop the run
        return ticker, [], f"{type(exc).__name__}: {exc}"


def run(tickers: Sequence[str], start: str, end: str, max_hold_days: int = 14,
        warmup_bars: int = 220, seed: int = 0, baseline: bool = True,
        workers: int = 1, pit_fundamentals: bool = False) -> List[Trade]:
    """Replay every ticker (in ``workers`` processes) and return all trades."""
    if pit_fundamentals:
        # Fetch every company's SEC facts once, here, so worker processes only
        # read the cache and never race each other against SEC's rate limit.
        from apex_quant.pit_fundamentals import load_pit_fundamentals
        for t in tickers:
            try:
                load_pit_fundamentals(t)
            except Exception as exc:  # noqa: BLE001 - that ticker is scored without them
                print(f"  {t:6} SEC fundamentals unavailable: {exc}", flush=True)
    # One benchmark download, shared: a throttled empty SPY in one worker would
    # silently drop every one of its tickers from the excess-return inference.
    bench = download(BENCHMARK, start, end)
    if bench.empty:
        raise RuntimeError(f"benchmark {BENCHMARK} unavailable; retry later (provider throttling?)")
    jobs = [(t, start, end, seed, baseline) for t in tickers]
    init = (max_hold_days, warmup_bars, bench, pit_fundamentals)
    out: List[Trade] = []
    failed: Dict[str, str] = {}

    def collect(results) -> None:
        for ticker, trades, err in results:
            n_sig = sum(t.kind == "signal" for t in trades)
            print(f"  {ticker:6} {n_sig:4d} signal trades" + (f"  ({err})" if err else ""), flush=True)
            if err:
                failed[ticker] = err
            out.extend(trades)

    if workers <= 1:
        _init_worker(*init)
        collect(map(_replay_one, jobs))
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=init) as pool:
            collect(pool.map(_replay_one, jobs))
    run.failed = failed  # type: ignore[attr-defined]
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _basic(df: pd.DataFrame) -> dict:
    wins = df[df["net_return_pct"] > 0]
    losses = df[df["net_return_pct"] <= 0]
    total = len(df)
    win_rate = 100.0 * len(wins) / total if total else 0.0
    avg_win = float(wins["net_return_pct"].mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses["net_return_pct"].mean())) if len(losses) else 0.0
    return {
        "total_trades": int(total),
        "win_rate_pct": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "expectancy_pct_per_trade": round(float(df["net_return_pct"].mean()), 3) if total else 0.0,
        "exit_reason_breakdown": df["exit_reason"].value_counts().to_dict(),
    }


def _drawdown(df: pd.DataFrame) -> float:
    """Max drawdown (%) of a 1%-risk-per-trade equity curve in exit order."""
    losses = df[df["net_return_pct"] <= 0]["net_return_pct"]
    r_unit = float(abs(losses.mean())) if len(losses) else 1.0
    eq = (1.0 + (df.sort_values("exit_date")["net_return_pct"] / r_unit) * 0.01).cumprod()
    return float(abs((eq / eq.cummax() - 1.0).min()) * 100.0) if len(eq) else 0.0


def _weekly(df: pd.DataFrame) -> pd.Series:
    """Mean excess return of the trades entered each calendar week.

    Trades opened in the same week share the market's path, so they are one
    observation, not many; the cohort series is what inference runs on.
    """
    d = df.dropna(subset=["excess_return_pct"])
    if d.empty:
        return pd.Series(dtype=float)
    week = pd.to_datetime(d["entry_date"]).dt.to_period("W")
    return d.groupby(week)["excess_return_pct"].mean().sort_index()


def _diff_ci(sig: pd.DataFrame, rnd: pd.DataFrame, n_boot: int = 4000,
             seed: int = 0) -> Optional[list]:
    """CI of mean signal excess minus mean random excess, resampling calendar weeks.

    Both samples are drawn from the same resampled weeks, so the market's own
    weekly moves cancel and same-week clustering is respected.
    """
    def by_week(df):
        d = df.dropna(subset=["excess_return_pct"])
        g = d.groupby(pd.to_datetime(d["entry_date"]).dt.to_period("W"))["excess_return_pct"]
        return g.sum(), g.count()

    s_sum, s_n = by_week(sig)
    r_sum, r_n = by_week(rnd)
    weeks = s_sum.index.union(r_sum.index)
    if len(weeks) < 10 or s_n.sum() < 5 or r_n.sum() < 5:
        return None
    s_sum, s_n = s_sum.reindex(weeks, fill_value=0.0).to_numpy(), s_n.reindex(weeks, fill_value=0).to_numpy()
    r_sum, r_n = r_sum.reindex(weeks, fill_value=0.0).to_numpy(), r_n.reindex(weeks, fill_value=0).to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(weeks), (n_boot, len(weeks)))
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = s_sum[idx].sum(1) / s_n[idx].sum(1) - r_sum[idx].sum(1) / r_n[idx].sum(1)
    diff = diff[np.isfinite(diff)]
    lo, hi = np.quantile(diff, [0.025, 0.975])
    return [float(lo), float(hi)]


def _finite(x):
    return None if x is None or not np.isfinite(x) else float(x)


def summarise(trades: List[Trade], n_trials: int = 1, max_hold_days: int = 14) -> dict:
    """Metrics, inference and a verdict for the signal trades against the baselines."""
    if not trades:
        return {"total_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    sig, rnd = df[df["kind"] == "signal"], df[df["kind"] == "random"]
    if sig.empty:
        return {"total_trades": 0}
    out = _basic(sig)
    # Trades compounded one after another in exit order: a stress figure for the
    # signal stream, not the drawdown of a portfolio holding concurrent trades.
    out["sequential_drawdown_pct"] = round(_drawdown(sig), 2)
    out["years_tested"] = int(pd.to_datetime(sig["exit_date"]).dt.year.nunique())
    per_year = sig.assign(year=pd.to_datetime(sig["exit_date"]).dt.year).groupby("year")
    out["per_year"] = {int(y): {"count": int(len(g)),
                                "mean_net_pct": _finite(g["net_return_pct"].mean()),
                                "mean_excess_pct": _finite(g["excess_return_pct"].mean())}
                       for y, g in per_year}
    out["trades_without_benchmark"] = int(sig["excess_return_pct"].isna().sum())

    # Weekly cohorts: a trade holds up to max_hold_days, so cohorts overlap by
    # about that many weeks; lags and blocks cover it.
    weekly = _weekly(sig)
    lags = max(1, int(np.ceil(max_hold_days / 5.0)))
    inf = S.infer_mean(weekly.to_numpy(), lags=lags, block=max(lags, S.default_block(len(weekly))))
    out["excess_vs_spy"] = ({**inf.to_dict(), "unit": "weekly entry cohort",
                             "mean_per_trade": _finite(sig["excess_return_pct"].mean())}
                            if inf else None)
    out["probabilistic_sharpe"] = S.probabilistic_sharpe(weekly.to_numpy())
    out["deflated_sharpe"] = S.deflated_sharpe(weekly.to_numpy(), n_trials) if n_trials > 1 else None
    out["n_trials_assumed"] = int(n_trials)
    if not rnd.empty:
        out["random_baseline"] = {**_basic(rnd),
                                  "mean_excess_pct": _finite(rnd["excess_return_pct"].mean())}
        out["signal_minus_random_excess"] = {
            "mean": _finite(sig["excess_return_pct"].mean() - rnd["excess_return_pct"].mean()),
            "ci95_bootstrap": _diff_ci(sig, rnd),
            "unit": "per trade, resampled by calendar week",
        }
    out["verdict"] = _verdict(out)
    return out


def _verdict(s: dict) -> str:
    ex = s.get("excess_vs_spy") or {}
    ci = ex.get("ci95_bootstrap")
    diff = (s.get("signal_minus_random_excess") or {}).get("ci95_bootstrap")
    parts = []
    if not ci:
        return "Too few trades to evaluate."
    if ci[0] > 0:
        parts.append("Signal trades beat SPY over the same dates (95% CI above zero).")
    elif ci[1] < 0:
        parts.append("Signal trades LAGGED SPY over the same dates (95% CI below zero).")
    else:
        parts.append("Signal trades are not distinguishable from SPY over the same dates.")
    if diff:
        if diff[0] > 0:
            parts.append("Entry timing adds value over random entries with the same exits.")
        elif diff[1] < 0:
            parts.append("Random entries with the same exits did BETTER: the timing is inverted.")
        else:
            parts.append("Entry timing is not distinguishable from random entries with the same exits.")
    dsr, psr = s.get("deflated_sharpe"), s.get("probabilistic_sharpe")
    if dsr is not None:
        parts.append(f"Deflated Sharpe probability {dsr:.2f} for {s['n_trials_assumed']} trials "
                     "(0.95+ is the usual bar).")
    elif psr is not None:
        parts.append(f"Probabilistic Sharpe {psr:.2f} that weekly excess beats zero (0.95+ is the "
                     "usual bar; pass --trials to deflate it for the variants tried).")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="AAPL,MSFT,KO,JPM,NVDA,GOOGL,META,AMZN",
                    help="Comma-separated ticker list (default: 8-name smoke universe)")
    ap.add_argument("--universe", choices=["liquid"],
                    help="use apex_quant's static liquid universe instead of --tickers")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--max-hold-days", type=int, default=14)
    ap.add_argument("--warmup-bars", type=int, default=220)
    ap.add_argument("--seed", type=int, default=0, help="seed for the random-entry baseline")
    ap.add_argument("--no-baseline", action="store_true", help="skip the random-entry baseline")
    ap.add_argument("--trials", type=int, default=1,
                    help="strategy variants evaluated so far, for the deflated Sharpe")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--pit-fundamentals", action="store_true",
                    help="score fundamental and quality legs from SEC filings known on each bar "
                         "(needs QUANTA_SEC_CONTACT)")
    ap.add_argument("--output-dir", default=str(_THIS_DIR.parent / "backtest_reports"))
    args = ap.parse_args()

    if args.universe == "liquid":
        from apex_quant.universe import get_universe
        tickers = list(get_universe())
    else:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Running backtest: {len(tickers)} tickers, {args.start} → {args.end}, "
          f"hold≤{args.max_hold_days}d, friction={Config.FRICTION_ROUND_TRIP_PCT * 100:.2f}% "
          f"round-trip, {args.workers} worker(s)", flush=True)
    trades = run(tickers, args.start, args.end, args.max_hold_days, args.warmup_bars,
                 seed=args.seed, baseline=not args.no_baseline, workers=args.workers,
                 pit_fundamentals=args.pit_fundamentals)

    summary = summarise(trades, n_trials=args.trials, max_hold_days=args.max_hold_days)
    summary["config"] = {k: getattr(args, k) for k in ("start", "end", "max_hold_days", "warmup_bars",
                                                       "seed", "trials", "pit_fundamentals")}
    summary["config"].update(tickers=tickers, failed_tickers=getattr(run, "failed", {}),
                             friction_round_trip=Config.FRICTION_ROUND_TRIP_PCT,
                             composite_weights=dict(StrategicStockAnalyzer.COMPOSITE_WEIGHTS))
    from tracking.fingerprint import code_fingerprint
    summary["code_fingerprint"] = code_fingerprint("shortterm", with_data=False)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))

    trades_path = out_dir / f"trades_{ts}.csv"
    summary_path = out_dir / f"summary_{ts}.json"
    pd.DataFrame([asdict(t) for t in trades]).to_csv(trades_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nLedger:  {trades_path}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
