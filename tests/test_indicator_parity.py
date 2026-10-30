"""Parity between the shared indicator module and every other implementation.

core/engine/indicators.py is the reference. It must match the Wilder
implementations in SP_ShortTerm.py and SP_ELITE_PRO.py to 1e-9, and the ``ta``
library (used by TradingAnalysis_ELITE.py) to a looser tolerance once the
warm-up is behind us: ``ta`` seeds RSI/ATR/ADX with SMAs/different start rules
so values converge rather than coincide.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_CORE = Path(__file__).resolve().parent.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from engine import indicators as ind  # noqa: E402


def _ohlc(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(500_000, 3_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


# ---- NaN semantics ---------------------------------------------------------

def test_insufficient_history_is_nan_not_neutral():
    df = _ohlc(10)
    assert np.isnan(ind.rsi(df["Close"]).iloc[-1])
    assert np.isnan(ind.atr(df["High"], df["Low"], df["Close"]).iloc[-1])
    a, p, m = ind.adx(df["High"], df["Low"], df["Close"])
    assert np.isnan(a.iloc[-1]) and np.isnan(p.iloc[-1]) and np.isnan(m.iloc[-1])
    up, mid, lo = ind.bollinger(df["Close"])
    assert np.isnan(up.iloc[-1]) and np.isnan(lo.iloc[-1])
    line, sig, hist = ind.macd(df["Close"])
    assert np.isnan(line.iloc[-1]) and np.isnan(hist.iloc[-1])


def test_flat_series_rsi_is_nan_and_pure_uptrend_is_100():
    flat = pd.Series([50.0] * 60)
    assert np.isnan(ind.rsi(flat).iloc[-1])
    up = pd.Series(np.arange(1.0, 61.0))
    assert ind.rsi(up).iloc[-1] == 100.0


# ---- ShortTerm / PRO parity (1e-9) -----------------------------------------

def test_matches_shortterm_wilder():
    st = pytest.importorskip("SP_ShortTerm")
    df = _ohlc()
    d = st.AdvancedTechnicalAnalyzer.calculate_comprehensive_indicators(df.copy())
    assert d, "ShortTerm returned no indicators"
    a, p, m = ind.adx(df["High"], df["Low"], df["Close"])
    assert d["RSI"] == pytest.approx(ind.rsi(df["Close"]).iloc[-1], abs=1e-9)
    assert d["ATR"] == pytest.approx(ind.atr(df["High"], df["Low"], df["Close"]).iloc[-1], abs=1e-9)
    assert d["ADX"] == pytest.approx(a.iloc[-1], abs=1e-9)
    assert d["Plus_DI"] == pytest.approx(p.iloc[-1], abs=1e-9)
    assert d["Minus_DI"] == pytest.approx(m.iloc[-1], abs=1e-9)
    line, sig, hist = ind.macd(df["Close"])
    assert d["MACD"] == pytest.approx(line.iloc[-1], abs=1e-9)
    assert d["MACD_Signal"] == pytest.approx(sig.iloc[-1], abs=1e-9)
    up, mid, lo = ind.bollinger(df["Close"])
    assert d["BB_Upper"] == pytest.approx(up.iloc[-1], abs=1e-9)
    assert d["BB_Lower"] == pytest.approx(lo.iloc[-1], abs=1e-9)


def _legacy_pro_adx(hist: pd.DataFrame, period: int = 14) -> float:
    """Frozen copy of PRO's pre-rewire inline ADX (the independent reference)."""
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    up_move, down_move = high.diff(), -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    pdi = 100 * (plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr)
    mdi = 100 * (minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr)
    dx = 100 * abs(pdi - mdi) / (pdi + mdi + 1e-10)
    return float(dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean().iloc[-1])


@pytest.mark.parametrize("seed", [1, 7, 21])
def test_pro_adx_identical_to_legacy_and_shared(seed):
    pro = pytest.importorskip("SP_ELITE_PRO")
    df = _ohlc(seed=seed)
    got = pro.TechnicalAnalysisEngine._calculate_adx(df)
    assert got == pytest.approx(_legacy_pro_adx(df), abs=1e-9)
    a, _, _ = ind.adx(df["High"], df["Low"], df["Close"])
    assert got == pytest.approx(a.iloc[-1], abs=1e-9)


@pytest.mark.parametrize("seed", [1, 7, 21])
def test_pro_rsi_differs_only_by_its_epsilon(seed):
    """PRO's RSI divides by (avg_loss + 1e-10); the shared one does not.

    The difference is ~5e-9 RSI points on ordinary data, above the 1e-9 bar
    for rewiring, so PRO keeps its own RSI (documented, not a bug).
    """
    pro = pytest.importorskip("SP_ELITE_PRO")
    df = _ohlc(seed=seed)
    delta = abs(pro.TechnicalAnalysisEngine._calculate_rsi(df["Close"]) - ind.rsi(df["Close"]).iloc[-1])
    assert delta < 1e-7


def test_pro_keeps_its_own_history_gates():
    pro = pytest.importorskip("SP_ELITE_PRO")
    eng = pro.TechnicalAnalysisEngine
    df = _ohlc(149)
    assert np.isnan(eng._calculate_adx(df))          # PRO needs 150 bars
    assert np.isnan(eng._calculate_rsi(df["Close"].head(14)))


# ---- ta library (looser, after warm-up) ------------------------------------

def test_close_to_ta_library_after_warmup():
    ta = pytest.importorskip("ta")
    from ta.momentum import RSIIndicator
    from ta.trend import ADXIndicator, MACD
    from ta.volatility import AverageTrueRange, BollingerBands
    df = _ohlc(600)
    c, h, l = df["Close"], df["High"], df["Low"]
    warm = 300
    deltas = {}

    deltas["rsi"] = (ind.rsi(c) - RSIIndicator(c).rsi()).iloc[warm:].abs().max()
    deltas["atr"] = (ind.atr(h, l, c) - AverageTrueRange(h, l, c).average_true_range()).iloc[warm:].abs().max()
    a, p, m = ind.adx(h, l, c)
    t = ADXIndicator(h, l, c)
    deltas["adx"] = (a - t.adx()).iloc[warm:].abs().max()
    deltas["+di"] = (p - t.adx_pos()).iloc[warm:].abs().max()
    deltas["-di"] = (m - t.adx_neg()).iloc[warm:].abs().max()
    line, sig, _ = ind.macd(c)
    tm = MACD(c)
    deltas["macd"] = (line - tm.macd()).iloc[warm:].abs().max()
    deltas["macd_signal"] = (sig - tm.macd_signal()).iloc[warm:].abs().max()
    up, mid, lo = ind.bollinger(c)
    tb = BollingerBands(c)
    # ta uses population std (ddof=0); ours is the sample std the engines use.
    deltas["bb_upper"] = (up - tb.bollinger_hband()).iloc[warm:].abs().max()
    print("\nmax |delta| vs ta after warm-up:", {k: float(v) for k, v in deltas.items()})
    for k in ("rsi", "atr", "adx", "+di", "-di"):
        assert deltas[k] < 1e-5, (k, deltas[k])
    assert deltas["macd"] < 1e-6 and deltas["macd_signal"] < 1e-6


def test_pro_adx_flat_close_bars_matches_legacy_zero():
    """Bars with range but zero directional movement: legacy PRO gave ADX 0.0."""
    pro = pytest.importorskip("SP_ELITE_PRO")
    n = 260
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame({"High": 51.0, "Low": 49.0, "Close": 50.0}, index=idx)
    assert pro.TechnicalAnalysisEngine._calculate_adx(df) == pytest.approx(_legacy_pro_adx(df), abs=1e-9)
