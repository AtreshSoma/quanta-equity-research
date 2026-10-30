"""Research/PRO engine audit fixes (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_CORE = Path(__file__).resolve().parent.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import TradingAnalysis_ELITE as ta  # noqa: E402


def _ohlc(n=300, seed=3, volume=True):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close},
                      index=pd.bdate_range("2023-01-02", periods=n))
    if volume:
        df["Volume"] = rng.integers(1_000_000, 2_000_000, n).astype(float)
    return df


# ---- 1. market-aware discount / Sharpe -------------------------------------

def _fund():
    f = ta.FundamentalData()
    f.fcf_per_share = 10.0
    f.eps_ttm = 12.0
    f.revenue_growth_yoy = 10.0
    f.pe_ratio = 15.0
    return f


def _dcf(ticker):
    risk = ta.RiskMetrics()
    risk.beta, risk.beta_status = 1.0, "measured"
    return ta.ValuationAnalyzer()._calculate_dcf(
        _fund(), risk, 150.0, ta.ValuationMetrics(), 0.0, ticker)


def test_india_dcf_uses_higher_discount_rate_us_unchanged():
    us, us_default = _dcf("AAPL"), _dcf("")
    india_ns, india_bo = _dcf("RELIANCE.NS"), _dcf("X.BO")
    assert us.dcf_fair_value > 0
    assert us.dcf_fair_value == pytest.approx(us_default.dcf_fair_value)   # US path unchanged
    assert india_ns.dcf_fair_value < us.dcf_fair_value
    assert india_ns.dcf_fair_value == pytest.approx(india_bo.dcf_fair_value)


def test_india_dcf_reads_india_constants_not_config(monkeypatch):
    from engine import thresholds as T
    assert T.UNIVERSE.RISK_FREE_RATE_INDIA == 0.065
    assert T.EQUITY_RISK_PREMIUM_INDIA == 0.07
    assert ta.Config.RISK_FREE_RATE == 0.045 and ta.Config.MARKET_RISK_PREMIUM == 0.055
    base = _dcf("A.NS").dcf_fair_value
    us_base = _dcf("A").dcf_fair_value
    monkeypatch.setattr(ta.Config, "RISK_FREE_RATE", 0.06)
    monkeypatch.setattr(ta.Config, "MARKET_RISK_PREMIUM", 0.08)
    assert _dcf("A.NS").dcf_fair_value == pytest.approx(base)   # India ignores Config rates
    assert _dcf("A").dcf_fair_value < us_base                   # US follows Config
    monkeypatch.setattr(T, "EQUITY_RISK_PREMIUM_INDIA", 0.09)
    assert _dcf("A.NS").dcf_fair_value < base


def _returns_metrics(ticker):
    rng = np.random.default_rng(11)
    r = pd.Series(rng.normal(0.0008, 0.01, 500))
    res = ta.RiskMetrics()
    res.annualized_volatility = float(r.std() * np.sqrt(252))
    res = ta.RiskAnalyzer()._calculate_return_metrics(r, res, ticker)
    return res, float(r.mean() * 252)


def test_sharpe_uses_market_risk_free_rate():
    us, mu = _returns_metrics("AAPL")
    ind, _ = _returns_metrics("RELIANCE.NS")
    assert us.sharpe_ratio == pytest.approx((mu - 0.045) / us.annualized_volatility)
    assert ind.sharpe_ratio == pytest.approx((mu - 0.065) / ind.annualized_volatility)


# ---- 2. fabricated inputs ---------------------------------------------------

def test_missing_volume_is_unmeasured_not_invented():
    t = ta.TechnicalAnalyzer().analyze(_ohlc(volume=False))
    for name in ("volume", "obv", "mfi", "vwap"):
        assert not t.has(name)
    assert t.volume_confirmation is False
    assert t.obv_trend == "NEUTRAL"
    assert t.vwap == 0.0 and t.volume_sma == 0.0


def test_volume_present_is_measured():
    t = ta.TechnicalAnalyzer().analyze(_ohlc())
    for name in ("volume", "obv", "mfi", "vwap"):
        assert t.has(name)
    assert t.vwap > 0


def test_ohlc_aligned_on_common_index():
    df = _ohlc()
    df.loc[df.index[100], "High"] = np.nan
    df.loc[df.index[150], "Low"] = np.nan
    t = ta.TechnicalAnalyzer().analyze(df)
    assert t.has("rsi") and t.has("adx")
    assert np.isfinite(t.atr) and t.atr > 0


def test_stochastic_williams_cci_flagged_when_unmeasured():
    an = ta.TechnicalAnalyzer()
    df = _ohlc(n=60)
    short = an._calculate_momentum(df["Close"].head(5), df["High"].head(5),
                                   df["Low"].head(5), ta.TechnicalIndicators())
    for name in ("stochastic", "williams_r", "cci"):
        assert not short.has(name)
    full = an._calculate_momentum(df["Close"], df["High"], df["Low"], ta.TechnicalIndicators())
    for name in ("stochastic", "williams_r", "cci"):
        assert full.has(name)


def test_bollinger_placeholder_marked_unmeasured_and_cannot_signal():
    an = ta.TechnicalAnalyzer()
    df = _ohlc(n=19)    # ATR(14) computes, Bollinger(20) cannot
    px = float(df["Close"].iloc[-1])
    res = an._calculate_volatility(df["Close"], df["High"], df["Low"], px, ta.TechnicalIndicators())
    assert not res.has("bollinger")
    assert res.bollinger_signal == "NEUTRAL" and not res.squeeze_detected


# ---- 3. peer median ---------------------------------------------------------

def test_peer_sector_pe_and_roe_use_median(monkeypatch):
    analyzer = ta.PeerAnalyzer()
    sector = next(iter(analyzer.SECTOR_MAPPINGS))
    peers = analyzer.SECTOR_MAPPINGS[sector]["peers"][:5]
    data = {peers[0]: (10, 0.10), peers[1]: (12, 0.12), peers[2]: (14, 0.14),
            peers[3]: (16, 0.16), peers[4]: (190, 4.00)}   # outlier P/E and ROE

    class FakeTicker:
        def __init__(self, sym):
            pe, roe = data.get(sym, (0, 0))
            self.info = {"trailingPE": pe, "returnOnEquity": roe, "priceToBook": 2.0}

    monkeypatch.setattr(ta.yf, "Ticker", FakeTicker)
    monkeypatch.setitem(analyzer.SECTOR_MAPPINGS, sector,
                        {**analyzer.SECTOR_MAPPINGS[sector], "peers": peers})
    f = ta.FundamentalData()
    f.pe_ratio, f.roe = 20.0, 15.0
    out = analyzer.analyze("ZZZZ", sector, f)
    assert out.pe_sector_avg == pytest.approx(14.0)      # mean would be 48.4
    assert out.roe_sector_avg == pytest.approx(14.0)     # mean would be 90.4


# ---- 4. portfolio VaR -------------------------------------------------------

def _pm():
    cls = next(c for n, c in vars(ta).items()
               if isinstance(c, type) and c.__module__ == ta.__name__
               and hasattr(c, "calculate_portfolio_var"))
    obj = cls.__new__(cls)
    obj._returns_cache = {}
    obj.api = None
    return obj


def test_elite_portfolio_var_not_measured_without_volatility():
    out = _pm().calculate_portfolio_var([{"ticker": "A", "weight": 0.5},
                                         {"ticker": "B", "weight": 0.5, "daily_volatility": 0.02}])
    assert out["portfolio_var"] is None and out["var_status"] == "not_measured"


def test_elite_portfolio_var_measured_when_inputs_present(monkeypatch):
    pm = _pm()
    monkeypatch.setattr(pm, "_get_correlation_matrix", lambda t: np.ones((len(t), len(t))))
    out = pm.calculate_portfolio_var([{"ticker": "A", "weight": 0.5, "daily_volatility": 0.02},
                                      {"ticker": "B", "weight": 0.5, "daily_volatility": 0.02}])
    assert out["var_status"] == "measured" and out["portfolio_var"] > 0


def test_pro_fallback_stub_returns_none_not_zero():
    import SP_ELITE_PRO as pro
    if pro.HAS_PORTFOLIO_RISK:
        pytest.skip("real PortfolioRiskManager loaded; stub not in use")
    assert pro.PortfolioRiskManager().calculate_portfolio_var([]) is None


# ---- 6. volatility scenario -------------------------------------------------

def _setup(target_pct, daily_vol=0.02, analyst_w=0.5):
    price = 100.0
    t = ta.TechnicalIndicators()
    t.atr = 2.0
    t.atr_percent = 2.0
    risk = ta.RiskMetrics()
    risk.daily_volatility = daily_vol
    val = ta.ValuationMetrics()
    val.target_base = price * (1 + target_pct)
    val.target_aggressive = val.target_base
    val.target_components = [{"source": "analyst_consensus", "value": val.target_base,
                              "weight": analyst_w}]
    return ta.TradeSetupGenerator().generate(price, ta.Signal.BUY, t, risk, 60.0,
                                             valuation=val, ticker="X")


def test_vol_scenario_fields_and_caveat_when_target_is_stretch():
    s = _setup(target_pct=1.5)
    vs = s.vol_scenario
    assert vs["horizon_sessions"] == 63
    assert vs["plus_1sigma_price"] == pytest.approx(s.entry_price * np.exp(0.02 * np.sqrt(63)), rel=1e-3)
    assert vs["analyst_weight"] == pytest.approx(0.5)
    expected_sigma = np.log(s.horizon_target / s.entry_price) / (0.02 * np.sqrt(63))
    assert vs["target_sigma"] == pytest.approx(expected_sigma, abs=0.01)
    assert vs["target_sigma"] > 1.5 and "stretch" in s.vol_scenario_caveat


def test_vol_scenario_no_caveat_for_modest_target_and_levels_unchanged():
    modest = _setup(target_pct=0.10)
    assert modest.vol_scenario and modest.vol_scenario_caveat == ""
    a, b = _setup(1.5, daily_vol=0.02), _setup(1.5, daily_vol=0.0)
    assert b.vol_scenario == {}
    for f in ("entry_price", "horizon_target", "target_base", "risk_reward_ratio", "reward_amount"):
        assert getattr(a, f) == getattr(b, f)


# ---- 7. decision core fails loudly -----------------------------------------

@pytest.mark.parametrize("modname", ["TradingAnalysis_ELITE", "SP_ELITE_PRO"])
def test_missing_decision_core_raises(modname):
    src = (_CORE / f"{modname}.py").read_text(encoding="utf-8")
    i = src.index("except ImportError as _core_exc:")
    assert "raise ImportError(" in src[i:i + 600]
    assert "HAS_DECISION_CORE = False" not in src
