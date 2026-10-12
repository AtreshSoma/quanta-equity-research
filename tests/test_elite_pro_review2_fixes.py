"""Regression tests for the second SP_ELITE_PRO review (2026-09-23).

Each test is named for the defect it locks in and says what the old behaviour
was, so a change that reintroduces it fails with an explanation. Everything
here is offline: synthetic prices, fake provider objects, no network.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


CORE = Path(__file__).resolve().parents[1] / "core"
MODULE_PATH = CORE / "SP_ELITE_PRO.py"

if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

if "SP_ELITE_PRO" in sys.modules:
    elite = sys.modules["SP_ELITE_PRO"]
else:
    spec = importlib.util.spec_from_file_location("SP_ELITE_PRO", MODULE_PATH)
    assert spec and spec.loader
    elite = importlib.util.module_from_spec(spec)
    sys.modules["SP_ELITE_PRO"] = elite
    spec.loader.exec_module(elite)


# ── helpers ─────────────────────────────────────────────────────────────────

def _bars(close, volume=None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(end="2026-09-01", periods=len(close))
    vol = np.full(len(close), 1e6) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": vol,
    }, index=idx)


def _bare_system(capital: float = 100_000.0, regime: str = "NEUTRAL"):
    sysm = object.__new__(elite.QuantaEliteTradingSystem)
    sysm.capital = capital
    sysm.currency_symbol = "$"
    sysm.market = "US"
    sysm.drawdown_multiplier = 1.0
    sysm.current_drawdown = 0.0
    sysm.market_regime = elite.MarketRegime(regime_type=regime, recommended_exposure=1.0)
    sysm.scan_results = []
    sysm._universe_stats = None
    sysm._filter_stats = {}
    sysm._filter_stats_lock = threading.Lock()
    sysm._zero_alloc_reasons = {}
    sysm._zero_alloc_tickers = []
    return sysm


def _pick(ticker, sector="Tech", score=8.0, stop=95.0, signal="BUY", seed=1,
          capital=100_000.0):
    p = elite.StockAnalysis(ticker=ticker, company=ticker, sector=sector, industry="x")
    p.signal = getattr(elite.SignalType, signal).value
    p.final_action = signal
    p.overall_score = score
    p.ranking_score = score
    p.entry_price, p.stop_loss, p.price_target = 100.0, stop, 115.0
    p.risk_reward_ratio = (115.0 - 100.0) / (100.0 - stop)
    p.risk_metrics.volatility = 0.25
    p.risk_grade, p.conviction = "B", 4
    p.transaction_cost_bps = 15.0
    alloc = _bare_system(capital)._calculate_position_size(p.signal, score, 100.0, stop, 0.25)
    elite.apply_allocation(p, alloc, capital)
    rng = np.random.default_rng(seed)
    p._hist_cache = _bars(100 * np.cumprod(1 + rng.normal(0, 0.015, 260)))
    return p


def _install_fake_deep(monkeypatch, verdicts):
    class _Res:
        def __init__(self, fa):
            self.final_action = fa
            self.final_action_reasons = [f"deep says {fa}"]
            self.data_status = "ok"
            self.data_status_reason = ""

    class _Deep:
        def analyze(self, ticker, account_size=0, fast_mode=False):
            return _Res(verdicts.get(ticker, "BUY"))

    mod = types.ModuleType("TradingAnalysis_ELITE")
    mod.QuantaEliteAnalyzer = _Deep
    from engine.types import DecisionInputs
    mod.build_reconciliation_inputs = lambda res: DecisionInputs(
        ticker='TEST', composite_score=0, risk_grade='B', risk_reward_ratio=0,
        current_price=100, stop_loss=0, target_price=0,
        heavy_insider_selling=res.final_action == 'WATCHLIST',
    )
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", mod)


# ── Data and universe ──────────────────────────────────────────────────────

class TestTwelveTwoMomentum:
    def test_one_year_of_yfinance_bars_is_enough(self):
        """period="1y" returns 250-252 bars; a hard len >= 252 made 12-2
        momentum 0.0 for every US name (251 bars on 2026-09-23)."""
        closes = pd.Series(np.linspace(50, 100, 251))
        assert elite.compute_momentum_12_2(closes) > 0.5
        multi = elite.EliteQuantEngine.calculate_multi_horizon_momentum(_bars(closes))
        assert multi["momentum_12_2"] == elite.compute_momentum_12_2(closes)
        assert multi["momentum_12m"] > 0.9

    def test_short_history_still_returns_zero(self):
        assert elite.compute_momentum_12_2(pd.Series(np.linspace(50, 100, 200))) == 0.0

    def test_horizons_match_pct_change(self):
        """EliteQuantEngine used iloc[-20] (a 19-day return) where the
        technical engine used pct_change(20)."""
        closes = pd.Series(np.linspace(50, 100, 300))
        multi = elite.EliteQuantEngine.calculate_multi_horizon_momentum(_bars(closes))
        assert multi["momentum_1m"] == pytest.approx(closes.pct_change(20).iloc[-1])


class TestMomentumScore:
    def _ind(self, **kw):
        base = dict(current_price=100.0, sma_50=95.0, momentum_1m=0.0, momentum_3m=0.0,
                    momentum_6m=0.0, momentum_12_2=0.0, adx=15.0, roc=0.0)
        base.update(kw)
        return elite.TechnicalIndicators(**base)

    def test_intermediate_momentum_is_scored(self):
        """12-2 carried zero points; a strong 12-month trend in a routine
        1-3 month pullback scored 0.0 momentum."""
        pullback = self._ind(momentum_12_2=0.60, momentum_6m=0.25, momentum_3m=-0.04,
                             momentum_1m=-0.03)
        assert elite.ScoringEngine.calculate_momentum_score(pullback) >= 4.0

    def test_adx_needs_an_uptrend(self):
        """ADX is direction-blind; a strong DOWNtrend earned +2.0."""
        down = self._ind(current_price=80.0, sma_50=95.0, adx=45.0)
        up = self._ind(current_price=100.0, sma_50=95.0, adx=45.0)
        assert elite.ScoringEngine.calculate_momentum_score(up) - \
            elite.ScoringEngine.calculate_momentum_score(down) == pytest.approx(2.0)


class TestUniverse:
    def test_constituent_scrapers_wrap_html_in_stringio(self):
        """pandas 3 read_html(str) treats the string as a PATH; every live
        constituent list silently fell back to the hardcoded one."""
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "pd.read_html(response.text)" not in src
        assert "StringIO(response.text)" in src

    def test_india_lists_are_nse_only(self):
        md = elite.MarketDataProvider()
        for getter in (md.get_nifty50_tickers, md.get_sensex_tickers,
                       md.get_nifty_bank_tickers, md.get_nifty_it_tickers):
            assert all(t.endswith(".NS") for t in getter())
        assert not any(t.endswith(".BO") for t in md.get_complete_indian_universe())

    def test_ampersand_symbols_survive_and_renames_apply_with_suffix(self):
        sysm = _bare_system()
        assert sysm._sanitize_ticker("M&M.NS") == "M&M.NS"
        assert sysm._sanitize_ticker("ZOMATO.NS") == "ETERNAL.NS"
        assert sysm._sanitize_ticker("BF.B") == "BF-B"
        assert sysm._sanitize_ticker("GOLD") == "B"

    def test_dow_list_comes_from_its_constituent_table(self, monkeypatch):
        """B-87: the Dow is read from data (live table, else snapshot), not a
        hand-typed list that goes stale at the next index change."""
        import _index_fixture as fx
        fx.install(monkeypatch)
        assert elite.MarketDataProvider().get_dow30_tickers() == fx.DOW
        fx.install(monkeypatch, fail={"dow30"}, snapshot=False)
        assert elite.MarketDataProvider().get_dow30_tickers() == []


# ── Risk and indicator maths ───────────────────────────────────────────────

class TestRiskMetrics:
    def test_real_crash_day_keeps_its_volatility(self):
        """Any >50% day returned an all-zero RiskMetrics: a -55% failed-trial
        name showed vol 0 / drawdown 0 and graded risk A."""
        close = np.linspace(100, 120, 250)
        close[150:] *= 0.45
        m = elite.RiskAnalyticsEngine.calculate_risk_metrics(_bars(close))
        assert m.volatility > 0.3
        assert m.max_drawdown < -0.5

    def test_exact_two_for_one_split_is_caught(self):
        """The screen was strictly > 50%, so an exact 2:1 (-50.0%) passed."""
        close = np.linspace(100, 120, 250)
        vol = np.full(250, 1e6)
        close[150:] /= 2.0
        vol[150:] *= 2.0
        assert not elite.DataValidator.validate_history(_bars(close, vol), "X").is_valid

    def test_drawdown_counts_the_first_close(self):
        close = np.r_[100.0, np.full(99, 50.0)]
        m = elite.RiskAnalyticsEngine.calculate_risk_metrics(_bars(close))
        assert m.max_drawdown == pytest.approx(-0.5)

    def test_alpha_uses_the_fitted_beta(self):
        rng = np.random.default_rng(3)
        mkt = pd.Series(rng.normal(0.0008, 0.01, 300))
        stock = 2.0 * mkt + rng.normal(0, 0.0001, 300)
        _beta, alpha = elite.RiskAnalyticsEngine.calculate_beta_alpha(stock, mkt, 0.04)
        assert abs(alpha - (-0.04 + 2.0 * 0.04)) < 0.02  # (Rs-Rf) - b(Rm-Rf) with b=2

    def test_stochastic_stays_in_range_on_flat_data(self):
        ind = elite.TechnicalAnalysisEngine.calculate_all_indicators(_bars(np.full(260, 50.0)))
        assert 0.0 <= ind.stoch_k <= 100.0


class TestFundamentalsIngestion:
    def test_dividend_yield_percent_units(self):
        """yfinance dividendYield is in PERCENT; QQQ's 0.42 read as 42%."""
        etf = elite.FundamentalAnalysisEngine.analyze_fundamentals(
            {"dividendYield": 0.42, "yield": 0.0042, "quoteType": "ETF"})
        stock = elite.FundamentalAnalysisEngine.analyze_fundamentals(
            {"dividendYield": 0.32, "currentPrice": 337.0})
        assert etf.dividend_yield == pytest.approx(0.0042)
        assert stock.dividend_yield == pytest.approx(0.0032)

    def test_mixed_currency_ratios_are_unavailable(self):
        info = {"currency": "USD", "financialCurrency": "JPY", "bookValue": 3000.0,
                "sharesOutstanding": 1e9, "operatingMargins": 0.1, "totalRevenue": 1e13,
                "priceToBook": 0.01, "freeCashflow": 1e12, "marketCap": 1e11}
        fm = elite.FundamentalAnalysisEngine.analyze_fundamentals(info)
        assert fm.currency_mismatch
        assert not fm.data_available["roic"] and not fm.data_available["free_cash_flow"]

    def test_bank_roic_is_unavailable(self):
        info = {"sector": "Financial Services", "bookValue": 100.0, "sharesOutstanding": 1e9,
                "operatingMargins": 0.4, "totalRevenue": 1e11, "totalDebt": 1e10}
        fm = elite.FundamentalAnalysisEngine.analyze_fundamentals(info)
        assert fm.roic == 0.0 and not fm.data_available["roic"]

    def test_thin_data_cannot_outscore_complete_data(self):
        """score / coverage let ROIC alone score 10.0 vs 9.5 for a perfect
        seven-field company."""
        only_roic = elite.FundamentalMetrics(roic=0.25, data_available={"roic": True})
        assert elite.ScoringEngine.calculate_fundamental_score(only_roic) < 6.0


class TestUniverseStats:
    def test_outlier_is_winsorised(self):
        pop = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.12, 0.18, 0.22, 25.0]
        assert elite.UniverseStats._zscore(0.30, pop) > 0.5
        assert abs(elite.UniverseStats._zscore(1e9, pop)) <= 3.0


# ── Score / signal pipeline ────────────────────────────────────────────────

class TestScorePipeline:
    def test_normalisation_never_moves_the_gated_score(self):
        """normalize_scores rebuilt overall_score from raw sub-scores after the
        signal was set, discarding the chase/insider/catalyst adjustments."""
        stats = elite.UniverseStats()
        a = elite.StockAnalysis(ticker="A", company="A", sector="S", industry="I")
        a.technical_score = a.fundamental_score = a.momentum_score = 6.0
        a.quality_score = a.value_score = 6.0
        a.overall_score, a.score_adjustment = 5.0, -1.0
        a.signal = elite.SignalType.HOLD.value
        weights = elite._REGIME_WEIGHTS["NEUTRAL"]
        elite.ScoringEngine.normalize_scores(a, stats, weights)
        assert a.overall_score == 5.0
        assert a.signal == elite.SignalType.HOLD.value
        assert a.ranking_score == pytest.approx(5.0)

    def test_scanner_thresholds_match_the_decision_core(self):
        """5.0/6.0 vs the core's 55/65 killed or capped names silently."""
        assert elite.TradingConfig.validate_config()
        assert elite.TradingConfig.ACCUMULATE_SCORE == 5.5
        assert elite.TradingConfig.GOOD_SCORE == 6.5

    def test_oversold_bounce_respects_the_regime(self):
        """A 6.1 score with Sharpe -0.8 and 70% vol became STRONG_BUY in BEAR."""
        sysm = _bare_system(regime="BEAR")
        sysm.current_scan_type = "standard"
        ind = elite.TechnicalIndicators(rsi=33, momentum_1w=0.02, macd_histogram=0.1,
                                        current_price=100, sma_20=98, volume_ratio=1.4,
                                        momentum_1m=-0.15)
        rm = elite.RiskMetrics(sharpe_ratio=-0.8, volatility=0.70)
        assert sysm._determine_signal_regime_aware(6.1, ind, rm) != elite.SignalType.STRONG_BUY.value


class TestInsiderSignal:
    def _stock(self, rows):
        return types.SimpleNamespace(insider_transactions=pd.DataFrame(rows))

    def test_awards_and_gifts_are_not_purchases(self):
        """9 awards + 1 gift + 4 sales read 'Insiders net buyers $78.7M'."""
        today = pd.Timestamp.now().normalize()
        rows = (
            [{"Start Date": today, "Shares": 10_000, "Value": 0.0,
              "Text": "Stock Award(Grant) at price 0.00 per share."}] * 9
            + [{"Start Date": today, "Shares": 500, "Value": np.nan, "Text": "Stock Gift"}]
            + [{"Start Date": today, "Shares": 2_000, "Value": 1_000_000.0,
                "Text": "Sale at price 500.00 per share."}] * 4
        )
        sysm = _bare_system()
        r = sysm._fetch_insider_signal("MSFT", self._stock(rows), 500.0, market_cap=3.7e12)
        assert r["buys_90d"] == 0 and r["sells_90d"] == 4
        assert r["sentiment"] == "BEARISH" and r["score_adjustment"] < 0
        assert "Heavy" not in r["note"]  # $4M at a $3.7T company is routine
        assert math.isfinite(r["net_value_90d"])


class TestSizing:
    def test_etf_is_not_billed_small_cap_costs(self):
        """ETFs have no marketCap -> 0 -> the <$2B tier (100 bps)."""
        c, s = elite.TradingConfig.get_transaction_costs(8e11)
        assert (c + s) * 2 * 10_000 < 20

    def test_india_costs_include_statutory_levies_and_fx(self):
        # ₹20,000 cr (~$2.3B) is a mid cap, not a $200B mega cap.
        c_in, s_in = elite.TradingConfig.get_transaction_costs(2e11, market="INDIA")
        assert (c_in + s_in) * 2 * 10_000 > 60

    def test_whole_shares_do_not_lose_one_to_float_error(self, monkeypatch):
        # monkeypatch restores the real default; a hard-coded restore to True
        # used to switch fractional sizing on for every later test.
        monkeypatch.setattr(elite.TradingConfig, "FRACTIONAL_SHARES", False)
        assert elite.TradingConfig.size_shares(70.49, 10.07) == 7.0

    def test_drawdown_throttle_binds_through_the_position_cap(self):
        full, half = _bare_system(25_000), _bare_system(25_000)
        half.drawdown_multiplier = 0.5
        a = full._calculate_position_size("STRONG_BUY", 8.0, 100.0, 96.0, 0.2)
        b = half._calculate_position_size("STRONG_BUY", 8.0, 100.0, 96.0, 0.2)
        assert b == pytest.approx(a * 0.5, rel=0.01)

    def test_validator_rejects_nan_and_excess_stop_risk(self):
        r = elite.StockAnalysis(ticker="X", company="X", sector="S", industry="I")
        r.entry_price, r.stop_loss, r.price_target = float("nan"), 90.0, 120.0
        assert not elite.validate_recommendation(r, 10_000)["is_valid"]
        r.entry_price, r.shares, r.allocation, r.signal = 450.0, 1, 450.0, "BUY"
        r.stop_loss, r.price_target, r.risk_reward_ratio = 396.0, 560.0, 2.0
        out = elite.validate_recommendation(r, 2_500)
        assert any("per-trade ceiling" in e for e in out["errors"])


class TestCalendars:
    def test_fomc_2026_dates_are_the_published_ones(self):
        assert "2026-10-28" in elite.FOMC_DATES_2026
        assert "2026-11-04" not in elite.FOMC_DATES_2026
        assert elite.FOMC_DATES_2027

    def test_eps_estimates_are_not_parsed_as_dates(self):
        cal = {"Earnings Date": [pd.Timestamp("2020-01-01")], "Earnings Average": 1.7}
        assert elite._extract_next_earnings_date(cal) is None


class TestExitMonitoring:
    def test_stop_from_entry_governs_the_holding(self):
        """A fresh analysis always puts its stop >= 3% under today's price,
        so a breached ENTRY stop never fired."""
        a = elite.StockAnalysis(ticker="X", company="X", sector="S", industry="I")
        a.technical_indicators.current_price = 85.0
        a.stop_loss = 80.0
        ev = elite.evaluate_sell_triggers(a, entry_price=100.0, entry_stop=92.0)
        assert ev.action == "SELL"


# ── Allocation ─────────────────────────────────────────────────────────────

class TestAllocation:
    def test_sector_diversification_off_still_publishes(self, monkeypatch):
        """The only selection loop lived inside `if diversify_sectors:`."""
        _install_fake_deep(monkeypatch, {})
        sysm = _bare_system()
        sysm.scan_results = [_pick("AAA", seed=1), _pick("BBB", score=7.5, seed=2)]
        assert sysm.get_top_recommendations(max_positions=3, diversify_sectors=False)

    def test_risk_based_sizes_are_published_unchanged(self, monkeypatch):
        """An inverse-vol pass re-split the total after every gate had run."""
        _install_fake_deep(monkeypatch, {})
        sysm = _bare_system()
        picks = [_pick("AAA", "Tech", 8.0, 95.0, "BUY", 1),
                 _pick("CCC", "Energy", 7.2, 94.0, "BUY", 5)]
        before = {p.ticker: p.allocation for p in picks}
        sysm.scan_results = picks
        for r in sysm.get_top_recommendations(max_positions=3):
            assert r.allocation == pytest.approx(before[r.ticker])

    def test_withdrawn_pick_is_backfilled(self, monkeypatch):
        _install_fake_deep(monkeypatch, {"XX": "WATCHLIST"})
        sysm = _bare_system()
        sysm.scan_results = [_pick("XX", "Tech", 9.0, seed=1), _pick("YY", "Energy", 8.5, seed=2),
                             _pick("ZZ", "Health", 8.0, seed=3), _pick("WW", "Util", 7.8, seed=4)]
        out = sysm.get_top_recommendations(max_positions=3)
        funded = {r.ticker for r in out if r.allocation > 0}
        assert "XX" not in funded and "WW" in funded and len(funded) == 3

    def test_empty_deep_verdict_is_not_a_confirmation(self, monkeypatch):
        _install_fake_deep(monkeypatch, {"AAA": ""})
        sysm = _bare_system()
        out = sysm._deep_confirm_final_picks([_pick("AAA")])
        assert out[0].allocation == 0.0 and out[0].not_independently_confirmed


# ── Output ─────────────────────────────────────────────────────────────────

class TestOutput:
    def test_export_dict_drops_private_caches_and_is_strict_json(self):
        p = _pick("AAA")
        p.risk_metrics.sharpe_ratio = float("nan")
        d = p.to_dict()
        assert "_hist_cache" not in d
        json.dumps(elite._json_safe(d), allow_nan=False, default=str)

    @pytest.mark.parametrize("text,value", [
        ("$10,000", 10_000.0), ("10k", 10_000.0), ("₹2,00,000", 200_000.0),
        ("2 lakh", 200_000.0), ("-5000", None), ("inf", None), ("abc", None),
    ])
    def test_capital_input_parsing(self, text, value):
        assert elite.parse_capital_input(text) == value


# ── Deep analyzer: the confirmation layer every published pick passes ─────

def test_deep_dcf_declared_unreliable_does_not_veto():
    """For a high-growth name whose DCF came in under 70% of price the deep
    analyzer reset fair value to the price ("unreliable") but kept the raw
    upside (GOOGL: -77.8%), and the decision core vetoed on it as "DCF
    overvaluation 78%" — which blocked every growth pick in a live scan."""
    ta = pytest.importorskip("TradingAnalysis_ELITE")
    fund = ta.FundamentalData()
    fund.revenue_growth_yoy, fund.pe_ratio = 24.2, 16.9
    fund.fcf_per_share, fund.eps_ttm = 3.86, 19.95
    risk = ta.RiskMetrics()
    risk.beta = 1.0
    v = ta.ValuationAnalyzer().analyze("GOOGL", 337.83, fund, risk)
    # A rejected DCF is UNAVAILABLE — not "fair value = price" — and carries no
    # upside, so nothing downstream can veto on (or blend in) the discarded figure.
    assert v.dcf_status == "unavailable"
    assert v.dcf_fair_value == 0.0
    assert v.dcf_upside == 0.0


# ── Live full-scan failures (2026-09-23 22:29 run, $2,500, 836 names) ──────

def test_throttled_scan_resumes_instead_of_halting(monkeypatch):
    """The provider circuit HALTED a full scan at 627/836 names — everything
    after "R" was never analysed and the throttled names were dropped."""
    monkeypatch.setattr(elite.TradingConfig, "RATE_LIMIT_COOLDOWN_SECONDS", 0.0)
    sysm = _bare_system(25_000.0, regime="BULL")
    sysm.failed_tickers = []
    sysm._chase_alerts_lock = threading.Lock()
    sysm._estimate_current_drawdown = lambda *a, **k: 0.0
    calls = {"n": 0}
    lock = threading.Lock()

    def fake_analyze(ticker, research_mode=False):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if 20 <= n < 40:
            sysm._record_scan_error(ticker, Exception("YFRateLimitError: Too Many Requests"))
            return None
        a = elite.StockAnalysis(ticker=ticker, company=ticker, sector="S", industry="I")
        a.overall_score, a.signal = 6.0, "BUY"
        return a

    sysm.analyze_stock = fake_analyze
    tickers = [f"T{i:03d}" for i in range(120)]
    out = sysm.scan_market(scan_type="custom", custom_tickers=tickers)
    assert {r.ticker for r in out} == set(tickers)
    assert sysm._scan_not_reached == []


def test_deep_info_failure_is_not_an_invalid_ticker(monkeypatch):
    """A throttled .info request left the bars cached without company info,
    and the gate declared NVDA / GOOGL / AMZN INVALID ("missing market cap,
    unknown company name"), withdrawing every pick."""
    ta = pytest.importorskip("TradingAnalysis_ELITE")

    class _Ticker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, *a, **k):
            return _bars(np.linspace(100, 150, 300))

        @property
        def info(self):
            raise Exception("YFRateLimitError: Too Many Requests. Rate limited.")

    monkeypatch.setattr(ta.yf, "Ticker", _Ticker)
    res = ta.QuantaEliteAnalyzer().analyze("NVDA", account_size=25_000, fast_mode=True)
    assert not res.is_invalid
    assert res.final_action != "INVALID"
    assert str(res.data_status) in ("provider_rate_limited", "provider_data_unavailable")


def test_partial_deep_info_is_a_provider_failure(monkeypatch):
    ta = pytest.importorskip("TradingAnalysis_ELITE")

    class _Ticker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, *a, **k):
            return _bars(np.linspace(100, 150, 300))

        @property
        def info(self):
            return {"quoteType": "EQUITY"}

    monkeypatch.setattr(ta.yf, "Ticker", _Ticker)
    res = ta.QuantaEliteAnalyzer().analyze("REAL", account_size=25_000, fast_mode=True)
    assert not res.is_invalid
    assert res.final_action != "INVALID"
    assert str(res.data_status) == "provider_data_unavailable"


def test_deep_confirm_retries_a_provider_failure_once(monkeypatch):
    class _Res:
        def __init__(self, fa, ds="ok"):
            self.final_action, self.final_action_reasons = fa, [fa]
            self.data_status, self.data_status_reason = ds, ""

    calls = {"n": 0}

    class _Deep:
        def analyze(self, ticker, account_size=0, fast_mode=False):
            calls["n"] += 1
            return _Res("", "provider_rate_limited") if calls["n"] == 1 else _Res("BUY")

    mod = types.ModuleType("TradingAnalysis_ELITE")
    mod.QuantaEliteAnalyzer = _Deep
    from engine.types import DecisionInputs
    mod.build_reconciliation_inputs = lambda res: DecisionInputs(
        ticker='AAA', composite_score=0, risk_grade='B', risk_reward_ratio=0,
        current_price=100, stop_loss=0, target_price=0,
    )
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", mod)
    out = _bare_system()._deep_confirm_final_picks([_pick("AAA")])
    assert calls["n"] == 2
    assert out[0].final_action == "BUY" and out[0].allocation > 0


def test_failed_qualitative_reconciliation_withdraws_provisional_allocation(monkeypatch):
    _install_fake_deep(monkeypatch, {"AAA": "BUY"})
    def broken_inputs(_result):
        raise RuntimeError("synthetic reconciliation error")
    monkeypatch.setattr(sys.modules["TradingAnalysis_ELITE"],
                        "build_reconciliation_inputs", broken_inputs)
    out = _bare_system()._deep_confirm_final_picks([_pick("AAA")])
    assert out[0].final_action == "WATCHLIST"
    assert out[0].allocation == 0


def test_deep_confirmation_runs_the_one_configured_research_mode(monkeypatch):
    """[F7] One research mode for deep confirmation and My Portfolio
    (RESEARCH.MODE). The full run includes the insider module that fast mode
    used to have to request separately."""
    from engine import thresholds as _T
    modes = []

    class _Deep:
        def analyze(self, ticker, account_size=0, fast_mode=False, include_insider=False):
            modes.append((fast_mode, include_insider))
            return types.SimpleNamespace(
                final_action="BUY", final_action_reasons=[], data_status="ok"
            )

    mod = types.ModuleType("TradingAnalysis_ELITE")
    mod.QuantaEliteAnalyzer = _Deep
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", mod)
    _bare_system()._deep_confirm_final_picks([_pick("AAA")])
    assert modes == [(_T.RESEARCH.MODE != "full", False)]


def test_fast_deep_analysis_can_supply_insider_red_flag():
    ta = pytest.importorskip("TradingAnalysis_ELITE")
    deep = object.__new__(ta.QuantaEliteAnalyzer)
    deep.technical_analyzer = types.SimpleNamespace(analyze=lambda data: ta.TechnicalIndicators())
    deep.risk_analyzer = types.SimpleNamespace(analyze=lambda data, ticker: ta.RiskMetrics())
    deep.fundamental_analyzer = types.SimpleNamespace(analyze=lambda ticker: ta.FundamentalData())
    insider = ta.InsiderData()
    insider.insider_sentiment, insider.net_flow = "BEARISH", -30_000_000.0
    deep.insider_analyzer = types.SimpleNamespace(analyze=lambda ticker: insider)
    deep.valuation_analyzer = types.SimpleNamespace(
        analyze=lambda *a, **k: ta.ValuationMetrics()
    )
    result = ta.AnalysisResult()
    result.ticker, result.current_price = "AAA", 100.0
    result = deep._run_parallel_analysis(
        "AAA", _bars(np.linspace(80, 100, 260)), result,
        fast_mode=True, include_insider=True,
    )
    assert result.insider is insider
    assert ta.build_reconciliation_inputs(result).heavy_insider_selling


def test_deep_analyzer_unavailable_withdraws_funding(monkeypatch):
    original_import = importlib.import_module

    def unavailable(name):
        if name == "TradingAnalysis_ELITE":
            raise ImportError("simulated missing deep analyzer")
        return original_import(name)

    monkeypatch.setattr(elite.importlib, "import_module", unavailable)
    sysm = _bare_system()
    out = sysm._deep_confirm_final_picks([_pick("AAA")])
    assert out[0].final_action == "WATCHLIST"
    assert out[0].allocation == 0.0 and out[0].shares == 0
    assert out[0].not_independently_confirmed
    assert sysm._deep_confirm_records[0]["allocation_after"] == 0.0


def test_min_trade_size_scales_for_small_fractional_accounts(monkeypatch):
    """A flat $100 floor sized 24 risk-sized names to 0 on a $2,500 account."""
    monkeypatch.setattr(elite.TradingConfig, "FRACTIONAL_SHARES", True)
    assert elite.TradingConfig.min_position_size(2_500) == pytest.approx(50.0)
    assert elite.TradingConfig.min_position_size(100_000) == pytest.approx(100.0)
    monkeypatch.setattr(elite.TradingConfig, "FRACTIONAL_SHARES", False)
    # A06 (former F11 xfails): whole-share accounts share the one minimum.
    assert elite.TradingConfig.min_position_size(2_500) == pytest.approx(50.0)


# ── Option A: deep-confirm vetoes only on real red flags ───────────────────

class TestDeepConfirmRedFlagsOnly:
    """The deep analyzer's OWN stop/target/R:R/composite score are a second,
    independently-computed set of numbers. Adopting its final_action wholesale
    let two engines merely disagreeing on price levels withdraw a pick the
    scanner's own gates had already cleared (live: GOOGL, TXN, LNTH, CSCO were
    all withdrawn on "R/R below min" / "Target <= entry" / "Score below min"
    computed from the DEEP engine's own levels, never the scanner's)."""

    def test_engine_level_reconciliation_skips_price_and_score_gates(self):
        inputs = elite.DecisionInputs(
            ticker="X", composite_score=10.0,   # would map to AVOID alone
            risk_grade="C", risk_reward_ratio=0.1,   # would fail the R:R gate
            current_price=100.0, stop_loss=200.0, target_price=50.0,  # inverted setup
        )
        dec = elite.resolve_final_action(
            inputs, starting_candidate=elite.FinalAction.BUY, enforce_price_gates=False
        )
        assert dec.final_action == elite.FinalAction.BUY

    def test_engine_level_reconciliation_still_applies_risk_grade(self):
        inputs = elite.DecisionInputs(
            ticker="X", composite_score=10.0, risk_grade="F",
            risk_reward_ratio=0.1, current_price=100.0, stop_loss=200.0, target_price=50.0,
        )
        dec = elite.resolve_final_action(
            inputs, starting_candidate=elite.FinalAction.BUY, enforce_price_gates=False
        )
        assert dec.final_action == elite.FinalAction.WATCHLIST

    @staticmethod
    def _fake_deep_result(final_action, reasons, *, risk_grade="C", dcf_upside=5.0,
                          patterns=(), prior_trend="SIDEWAYS", insider_sentiment="NEUTRAL",
                          net_flow=0.0, is_invalid=False):
        return types.SimpleNamespace(
            final_action=final_action, final_action_reasons=list(reasons),
            data_status="ok", data_status_reason="", is_invalid=is_invalid,
            ticker="X", current_price=100.0, has_existing_position=False,
            risk=types.SimpleNamespace(risk_grade=risk_grade),
            technical=types.SimpleNamespace(candlestick_patterns=list(patterns), prior_trend=prior_trend),
            valuation=types.SimpleNamespace(dcf_upside=dcf_upside),
            insider=types.SimpleNamespace(insider_sentiment=insider_sentiment, net_flow=net_flow),
        )

    def _install(self, monkeypatch, result):
        ta_real = pytest.importorskip("TradingAnalysis_ELITE")

        class _Deep:
            def analyze(self, ticker, account_size=0, fast_mode=False):
                return result

        mod = types.ModuleType("TradingAnalysis_ELITE")
        mod.QuantaEliteAnalyzer = _Deep
        mod.build_reconciliation_inputs = ta_real.build_reconciliation_inputs
        monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", mod)

    def test_research_decision_caps_the_published_scan_call(self, monkeypatch):
        """[ONE DECISION, 2026-09-27] Option A let the scanner publish BUY while
        the Research tear sheet for the same name said WATCHLIST. The Research
        Decision is now the ceiling: a scan may rank and size, never out-bid it."""
        res = self._fake_deep_result(
            "WATCHLIST", ["Composite score 65.8 → candidate BUY",
                         "R/R 0.26 below min 1.50 → WATCHLIST"],
        )
        self._install(monkeypatch, res)
        out = _bare_system()._deep_confirm_final_picks([_pick("GOOGL")])
        assert out[0].final_action == "WATCHLIST" and out[0].allocation == 0.0

    def test_audit_retains_raw_deep_verdict_and_names_the_cap(self, monkeypatch):
        res = self._fake_deep_result(
            "WATCHLIST", ["R/R 0.26 below min 1.50 → WATCHLIST"]
        )
        self._install(monkeypatch, res)
        sysm = _bare_system()
        sysm._deep_confirm_final_picks([_pick("GOOGL")])
        record = sysm._deep_confirm_records[0]
        assert record["deep_action"] == "WATCHLIST"
        assert record["final_action"] == "WATCHLIST"
        assert "RESEARCH_DECISION_CAP" in record["reason_codes"]
        assert "Research Decision" in record["reason"]

    def test_a_more_bullish_research_verdict_never_upgrades_the_scan(self, monkeypatch):
        res = self._fake_deep_result("STRONG_BUY", ["Composite 80"])
        self._install(monkeypatch, res)
        out = _bare_system()._deep_confirm_final_picks([_pick("MSFT")])
        assert out[0].final_action == "BUY"

    def test_risk_grade_f_still_vetoes(self, monkeypatch):
        res = self._fake_deep_result(
            "WATCHLIST", ["Risk Grade F blocks new-money BUY → WATCHLIST"], risk_grade="F",
        )
        self._install(monkeypatch, res)
        out = _bare_system()._deep_confirm_final_picks([_pick("NVDA")])
        assert out[0].final_action == "WATCHLIST" and out[0].allocation == 0.0

    def test_bearish_reversal_still_trims_to_accumulate(self, monkeypatch):
        res = self._fake_deep_result(
            "ACCUMULATE", ["Bearish reversal (Hanging Man) → ACCUMULATE"],
            patterns=[{"bias": "BEARISH", "type": "REVERSAL", "reliability": "HIGH",
                       "name": "Hanging Man"}],
            prior_trend="UP",
        )
        self._install(monkeypatch, res)
        out = _bare_system()._deep_confirm_final_picks([_pick("TXN")])
        assert out[0].final_action == "ACCUMULATE" and out[0].allocation > 0

    def test_true_invalid_verdict_is_never_reconciled_away(self, monkeypatch):
        res = self._fake_deep_result("INVALID", ["No valid historical data"], is_invalid=True)
        self._install(monkeypatch, res)
        out = _bare_system()._deep_confirm_final_picks([_pick("ZZZZ")])
        assert out[0].final_action == "INVALID" and out[0].allocation == 0.0
