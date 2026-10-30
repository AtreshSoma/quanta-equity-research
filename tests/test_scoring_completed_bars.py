"""A wild partial bar must not move the scores; the live price must still show it.

Clocks are pinned with ``now``. The frames are synthetic and contain no network I/O.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator

import TradingAnalysis_ELITE as ta
from engine import bars

_SPEC = importlib.util.spec_from_file_location(
    "sp_shortterm",
    Path(__file__).resolve().parent.parent / "core" / "SP_ShortTerm.py")
sp_shortterm = importlib.util.module_from_spec(_SPEC)
sys.modules["sp_shortterm"] = sp_shortterm
_SPEC.loader.exec_module(sp_shortterm)

NY_1000_OCT27 = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)   # 10:00 EDT, mid-session
NY_1630_OCT27 = datetime(2026, 10, 27, 20, 30, tzinfo=timezone.utc)  # 16:30 EDT, settled


def _history(rows: int = 320, seed: int = 5, end: str = "2026-10-26") -> pd.DataFrame:
    """Completed daily bars ending on *end* (New York dates, naive index)."""
    gen = np.random.default_rng(seed)
    close = 200 * np.exp(np.cumsum(gen.normal(0.001, 0.012, rows)))
    high = close * (1 + np.abs(gen.normal(0, 0.006, rows)))
    low = close * (1 - np.abs(gen.normal(0, 0.006, rows)))
    volume = gen.integers(2_000_000, 6_000_000, rows).astype(float)
    return pd.DataFrame({
        "Open": close * 0.999, "High": high, "Low": low,
        "Close": close, "Volume": volume,
    }, index=pd.bdate_range(end=end, periods=rows))


def _with_live_bar(base: pd.DataFrame, close_move: float = 0.0) -> pd.DataFrame:
    """Append a wild 27 Oct partial bar: a 16% intraday range and thin volume.

    ``close_move`` moves the LAST PRICE (the close of the partial bar) by that
    fraction. With 0.0 the last price equals yesterday's close, so only the candle's
    own range and volume differ from the completed frame.
    """
    prev = float(base["Close"].iloc[-1])
    live = pd.DataFrame({
        "Open": [prev],
        "High": [prev * 1.01],
        "Low": [prev * 0.85],                     # about -15% inside the session
        "Close": [prev * (1 + close_move)],
        "Volume": [float(base["Volume"].tail(20).mean()) * 0.4],  # partial day, thin so far
    }, index=pd.DatetimeIndex([pd.Timestamp("2026-10-27")]))
    return pd.concat([base, live])


# --- Research technical engine ---------------------------------------------------

_BAR_FIELDS = [
    "rsi", "macd_value", "macd_signal_line", "macd_histogram", "adx", "plus_di", "minus_di",
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "atr", "bollinger_upper",
    "bollinger_middle", "bollinger_lower", "stochastic_k", "stochastic_d", "williams_r",
    "cci", "tenkan_sen", "kijun_sen", "senkou_span_a", "senkou_span_b", "obv_trend", "mfi",
    "volatility_regime", "candlestick_bias", "candlestick_score", "candlestick_summary",
    "support_level", "resistance_level", "week_52_high",
    "week_52_low", "market_structure", "trend_direction", "trend_strength",
    "volume_ratio", "volume_confirmation", "momentum_score", "trend_score",
    "volatility_score", "macd_signal", "rsi_divergence",
]


def _analyze(df: pd.DataFrame, now: datetime):
    return ta.TechnicalAnalyzer().analyze(df, now=now, ticker="AAPL")


def test_wild_partial_bar_leaves_every_bar_derived_score_unchanged():
    base = _history()
    intraday = _analyze(_with_live_bar(base, close_move=0.0), NY_1000_OCT27)
    without_row = _analyze(base, NY_1000_OCT27)
    for name in _BAR_FIELDS:
        assert getattr(intraday, name) == getattr(without_row, name), name
    assert intraday.indicator_basis["live_bar_excluded"] is True
    assert intraday.indicator_basis["scored_through"] == "2026-10-26"
    assert without_row.indicator_basis["live_bar_excluded"] is False


def test_live_price_still_reflects_the_partial_bar():
    base = _history()
    live = _with_live_bar(base, close_move=-0.15)
    tech = _analyze(live, NY_1000_OCT27)
    live_close = float(live["Close"].iloc[-1])
    assert live_close < float(base["Close"].iloc[-1]) * 0.9
    # The level comparison is the live price against the COMPLETED-bar average.
    expected = (live_close - tech.sma_20) / tech.sma_20 * 100
    assert abs(tech.price_vs_sma20 - expected) < 1e-9
    assert tech.price_vs_sma20 < 0


def test_pivot_is_the_same_all_day_on_the_same_frame():
    """Intraday the previous session is iloc[-2]; after settle it is also iloc[-2]."""
    live = _with_live_bar(_history(), close_move=-0.15)
    intraday = _analyze(live, NY_1000_OCT27)
    settled = _analyze(live, NY_1630_OCT27)
    assert intraday.pivot_point == settled.pivot_point
    assert intraday.r1 == settled.r1 and intraday.s1 == settled.s1


def test_settled_bar_is_scored_exactly_as_the_full_frame_always_was():
    """After the close and settle, the completed frame IS the full frame."""
    full = _history(end="2026-10-27")
    tech = _analyze(full, NY_1630_OCT27)
    assert tech.indicator_basis["live_bar_excluded"] is False
    # The pre-change path ran each indicator on the full frame; the same numbers come out.
    assert tech.rsi == float(RSIIndicator(full["Close"], window=14).rsi().iloc[-1])
    assert tech.sma_20 == float(full["Close"].rolling(20).mean().iloc[-1])
    assert tech.volume_ratio_basis == "latest session"


def test_intraday_volume_ratio_is_the_last_completed_session():
    base = _history()
    tech = _analyze(_with_live_bar(base), NY_1000_OCT27)
    done = base["Volume"]
    expected = done.iloc[-1] / done.tail(20).mean()
    assert abs(tech.volume_ratio - expected) < 1e-9
    assert tech.volume_ratio_basis == "last completed session (today's is still trading)"


def test_research_payload_publishes_the_basis():
    base = _history()
    tech = _analyze(_with_live_bar(base), NY_1000_OCT27)
    assert tech.indicator_basis["scored_through"] == "2026-10-26"
    assert "still trading" in tech.indicator_basis["reason"]


# --- ShortTerm indicators and technical score --------------------------------------

def _shortterm_scores(hist: pd.DataFrame, now: datetime):
    """The price-derived ShortTerm inputs as analyze_stock builds them."""
    score_hist, basis = bars.scoring_bars(hist, "AAPL", now)
    indicators = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(
        score_hist)
    price = float(hist["Close"].iloc[-1])
    analyzer = sp_shortterm.StrategicStockAnalyzer.__new__(sp_shortterm.StrategicStockAnalyzer)
    technical = analyzer._calculate_technical_score(dict(indicators), price)
    return indicators, technical, basis


def test_shortterm_indicators_and_technical_score_ignore_the_wild_partial_bar():
    base = _history()
    live = _with_live_bar(base, close_move=0.0)
    ind_live, tech_live, basis = _shortterm_scores(live, NY_1000_OCT27)
    ind_base, tech_base, _ = _shortterm_scores(base, NY_1000_OCT27)
    assert basis["live_bar_excluded"] is True
    for key in ("RSI", "MACD", "MACD_Histogram", "SMA_20", "SMA_50", "ATR", "ADX",
                "BB_Position", "Momentum_1M", "Momentum_3M", "Volatility_20D", "Stoch_K"):
        assert ind_live[key] == ind_base[key], key
    assert tech_live == tech_base


def test_shortterm_raw_volume_ratio_stays_live_for_the_liquidity_gate():
    base = _history()
    live = _with_live_bar(base, close_move=0.0)
    raw = sp_shortterm.AdvancedTechnicalAnalyzer.raw_volume_ratio(live)
    full_calc = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(live)
    assert abs(raw - full_calc["Volume_Ratio"]) < 1e-9
    assert raw < 1.0  # the partial bar really is thin, so the gate sees it


def test_shortterm_after_settle_matches_the_full_frame():
    full = _history(end="2026-10-27")
    ind_settled, tech_settled, basis = _shortterm_scores(full, NY_1630_OCT27)
    ind_full = sp_shortterm.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(full)
    assert basis["live_bar_excluded"] is False
    assert ind_settled.keys() == ind_full.keys()
    for key, value in ind_full.items():
        if isinstance(value, np.ndarray):
            assert np.array_equal(ind_settled[key], value), key
        else:
            assert ind_settled[key] == value, key
