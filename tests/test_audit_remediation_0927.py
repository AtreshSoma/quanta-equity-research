"""Regression tests for the 2026-09-27 audit remediation.

Each test pins one verified defect: missing or garbage data becoming a
favourable score, valuation filters that could only raise fair value, a
reweighting that cut the valuation leg whenever it read "overvalued", and
fundamental inputs scored twice.
"""
from __future__ import annotations

import pytest

import TradingAnalysis_ELITE as ta


def _fund(**kw):
    f = ta.FundamentalData()
    for k, v in kw.items():
        setattr(f, k, v)
    return f


# --- missing / garbage data ------------------------------------------------

@pytest.mark.parametrize("raw", [-150.0, 9000.0])
def test_negative_or_absurd_debt_to_equity_is_unreported_not_debt_free(raw):
    f = ta.FundamentalData(reported={"debt_to_equity": True, "current_ratio": True})
    out = ta.FundamentalAnalyzer()._calculate_financial_health(
        {"debtToEquity": raw, "currentRatio": 3.0}, f)
    assert out.debt_to_equity == 0.0
    assert out.has("debt_to_equity") is False


def test_unreported_leverage_does_not_vote_as_a_confirmation():
    tech = ta.TechnicalIndicators()
    f = ta.FundamentalData(reported={"debt_to_equity": False, "roe": False})
    bull, _bear = ta.SignalGenerator()._count_confirmations(tech, f, ta.SentimentData())
    f2 = ta.FundamentalData(reported={"debt_to_equity": True, "roe": False}, debt_to_equity=0.4)
    bull2, _ = ta.SignalGenerator()._count_confirmations(tech, f2, ta.SentimentData())
    assert bull2 == bull + 1


def test_interest_coverage_keeps_the_sign_of_an_operating_loss():
    f = ta.FundamentalData()
    out = ta.FundamentalAnalyzer()._calculate_financial_health(
        {"ebitda": -500.0, "interestExpense": 100.0}, f)
    assert out.interest_coverage == pytest.approx(-5.0)


# --- factor double counting ------------------------------------------------

def test_quality_no_longer_rescored_from_fundamental_leg_inputs():
    """ROE / margin / leverage / growth live in the fundamental leg only."""
    base = dict(piotroski_f_score=6, piotroski_components_available=9, altman_z_status="unavailable")
    strong = _fund(roe=40.0, net_margin=30.0, debt_to_equity=0.1, current_ratio=3.0,
                   revenue_growth_yoy=40.0, **base)
    weak = _fund(roe=1.0, net_margin=1.0, debt_to_equity=3.0, current_ratio=0.5,
                 revenue_growth_yoy=-10.0, **base)
    q = ta.FundamentalAnalyzer._independent_quality_score
    assert q({}, strong) == q({}, weak)
    assert q({}, strong) == pytest.approx(100 * 6 / 9)


def test_quality_is_neutral_when_nothing_independent_is_measured():
    f = _fund(piotroski_components_available=2, altman_z_status="unavailable")
    assert ta.FundamentalAnalyzer._independent_quality_score({}, f) == 50.0


# --- valuation symmetry ----------------------------------------------------

def _graham_only(price, eps, bvps):
    f = _fund(eps_ttm=eps, book_value_per_share=bvps, pe_ratio=price / eps, fcf_per_share=0.0)
    return ta.ValuationAnalyzer().analyze("X", price, f, ta.RiskMetrics())


def test_graham_is_used_whatever_the_pe_inside_the_band():
    # P/E 30: the old rule dropped Graham whenever P/E > 20, which is exactly
    # when it reads "overvalued".
    v = _graham_only(price=100.0, eps=100 / 30, bvps=40.0)   # graham ~= 54.8
    assert v.graham_status == "measured"
    assert "graham" in v.fair_value_sources
    assert v.valuation_label in {"OVERVALUED", "SIGNIFICANTLY_OVERVALUED"}


@pytest.mark.parametrize("ratio,admitted", [(0.6, True), (1.6, True), (0.4, False), (2.5, False)])
def test_plausibility_band_is_symmetric(ratio, admitted):
    assert ta.ValuationAnalyzer._plausible(100.0 * ratio, 100.0) is admitted


def test_twelve_month_target_excludes_upside_only_technical_levels():
    val = ta.ValuationMetrics()
    out = ta.ValuationAnalyzer().calculate_composite_targets(
        100.0, val, ta.MarketIntelligence(target_mean=120.0, analyst_count=10),
        ta.MonteCarloResults(), ta.TechnicalIndicators(resistance_level=140.0, r1=150.0))
    assert [c["source"] for c in out.target_components] == ["analyst_consensus"]


def test_target_blends_the_engines_own_overvalued_intrinsic_value():
    val = ta.ValuationMetrics(valuation_status="measured", fair_value_estimate=70.0,
                              fair_value_sources=["graham"])
    out = ta.ValuationAnalyzer().calculate_composite_targets(
        100.0, val, ta.MarketIntelligence(target_mean=110.0, analyst_count=20),
        ta.MonteCarloResults(), ta.TechnicalIndicators())
    assert "intrinsic_value" in out.target_sources
    assert out.target_base < 110.0


def test_high_growth_does_not_cut_the_valuation_weight():
    gen = ta.SignalGenerator()
    tech, risk = ta.TechnicalIndicators(), ta.RiskMetrics()
    val = ta.ValuationMetrics(valuation_score=20.0)
    common = dict(technical=tech, risk=risk, sentiment=ta.SentimentData(), insider=ta.InsiderData(),
                  market_intel=ta.MarketIntelligence(), valuation=val)
    _s, _c, slow, _e = gen.generate(fundamental=_fund(revenue_growth_yoy=5.0), **common)
    _s, _c, fast, _e = gen.generate(fundamental=_fund(revenue_growth_yoy=35.0), **common)
    assert fast.weights["valuation"] == pytest.approx(slow.weights["valuation"])


# --- one horizon for reward and risk ---------------------------------------

def test_reward_risk_is_measured_to_the_horizon_target_not_the_12_month_level():
    """Audit C3 (JPM replay): +4.6% 12-month upside against a 1.4% stop read
    R:R 2.9. Reward over the 63-session horizon is the pro-rated upside."""
    val = ta.ValuationMetrics(target_base=120.0, target_aggressive=130.0)
    tech = ta.TechnicalIndicators(atr=2.0, support_level=0.0, resistance_level=0.0, rsi=50.0)
    ts = ta.TradeSetupGenerator().generate(100.0, ta.Signal.BUY, tech, ta.RiskMetrics(), 60.0,
                                           valuation=val)
    frac = ts.horizon_days / ts.valuation_horizon_days
    assert ts.horizon_target == pytest.approx(ts.entry_price + (120.0 - ts.entry_price) * frac)
    assert ts.target_base == pytest.approx(120.0)
    cost = ts.entry_price * (ta.TradeSetupGenerator.SPREAD_BPS + 2 * ta.TradeSetupGenerator.SLIPPAGE_BPS) / 1e4
    expected = (ts.horizon_target - ts.entry_price - cost) / (ts.entry_price - ts.stop_loss + cost)
    assert ts.risk_reward_ratio == pytest.approx(expected)
    assert ts.risk_reward_ratio < 2.0, "a 12-month target no longer inflates a multi-week R:R"


# --- app-wide request guard (CSRF / DNS rebinding) --------------------------

def _webapp():
    import importlib
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "webapp"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("app")


def test_cross_site_text_plain_post_is_refused():
    web = _webapp()
    c = web.app.test_client()
    r = c.post("/api/analyze", data='{"ticker":"AAPL"}', content_type="text/plain",
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/cache/clear")
    assert r.status_code == 403, "no header, no state change"


def test_foreign_origin_is_refused_even_with_the_header():
    web = _webapp()
    r = web.app.test_client().post("/api/cache/clear", headers={
        "X-QUANTA-Request": "1", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_dns_rebinding_host_is_refused_for_reads():
    web = _webapp()
    r = web.app.test_client().get("/api/health?probe=0", headers={"Host": "evil.example:5500"})
    assert r.status_code == 403


def test_health_does_not_disclose_local_paths():
    web = _webapp()
    body = web.app.test_client().get("/api/health?probe=0").get_json()
    for key in ("cwd", "python_executable", "project_root", "virtual_env"):
        assert key not in body


def test_same_origin_post_with_header_is_accepted():
    web = _webapp()
    r = web.app.test_client().post("/api/cache/clear", headers={
        "X-QUANTA-Request": "1", "Origin": "http://localhost"})
    assert r.status_code != 403


def test_open_engine_route_is_gone():
    web = _webapp()
    r = web.app.test_client().post("/api/open-engine/sp-elite-pro", headers={"X-QUANTA-Request": "1"})
    assert r.status_code == 404


# --- fail-closed Decision resolution ----------------------------------------

def test_resolver_failure_publishes_a_no_new_money_decision():
    r = ta.AnalysisResult(ticker="X", company_name="X")
    r.signal = ta.Signal.STRONG_BUY
    ta._fail_closed_decision(r, "RuntimeError: boom")
    assert r.final_action == "WATCHLIST"
    assert r.final_action_codes == ["DECISION_CORE_ERROR"]
    assert r.signal != ta.Signal.STRONG_BUY
    from engine.blockers import blocker_info
    assert blocker_info("DECISION_CORE_ERROR")["category"] == "missing_data"


def test_one_band_table_feeds_resolver_generator_and_blockers():
    from engine import blockers, decision_engine
    from engine.score_bands import SCORE_BANDS, band_floor
    assert ta.Config.SIGNAL_CUTOFFS is SCORE_BANDS
    assert [f for f, _ in decision_engine._BUY_THRESHOLDS] == [75.0, 65.0, 55.0, 45.0]
    assert blockers.BULLISH_FLOOR == band_floor("ACCUMULATE") == 55.0
    assert blockers.TRIM_FLOOR == band_floor("REDUCE")


# --- evidence lineage --------------------------------------------------------

def test_untested_version_surfaces_its_failed_predecessor():
    from engine import evidence as ev
    reg = {"strategies": {"elite.composite": {
        "status": "NO_EDGE", "summary": "IC -0.023 (t -1.41)", "tested_through": "2026-08-25"}}}
    e = ev.get_evidence(ev.ELITE, registry=reg)
    assert e.status is ev.EvidenceStatus.UNTESTED
    assert e.status.permits_recommendation is False
    assert "no edge" in e.summary.lower() and "elite.composite" in e.summary
    names = [x["strategy"] for x in e.lineage]
    assert names == ["elite.alpha_only.v1", "elite.composite"]
    assert e.lineage[1]["status"] == "NO_EDGE"
    assert e.to_dict()["lineage"][1]["tested_through"] == "2026-08-25"


def test_a_predecessor_result_never_validates_the_current_version():
    from engine import evidence as ev
    reg = {"strategies": {"elite.composite": {"status": "VALIDATED",
                                              "generated_utc": "2026-09-20T00:00:00+00:00"}}}
    assert ev.get_evidence(ev.ELITE, registry=reg).status is ev.EvidenceStatus.UNTESTED


def test_ledger_stores_the_model_version_and_never_pools_versions(tmp_path):
    from types import SimpleNamespace
    from tracking.ledger import SignalLedger
    from engine.decision import Horizon
    led = SignalLedger(tmp_path / "l.sqlite3")
    for strategy, day in (("elite.alpha_only.v1", "2026-09-01"), ("elite.research.v2", "2026-09-02")):
        dec = SimpleNamespace(
            engine="elite", ticker="AAA", as_of=day, horizon=Horizon("position", 63, "p"),
            stance="BULLISH", action=SimpleNamespace(value="BUY"), label="BUY",
            evidence=SimpleNamespace(strategy=strategy, status=SimpleNamespace(value="UNTESTED")),
            score=60.0, factor_scores={}, reference_price=10.0, target=11.0, stop=9.0)
        assert led.record_decision(dec) is not None
    groups = led.summarize(group_by=("engine", "strategy", "horizon_days", "stance"))
    assert sorted(g.key["strategy"] for g in groups) == ["elite.alpha_only.v1", "elite.research.v2"]


def test_analyst_rating_is_not_counted_again_in_sentiment():
    gen = ta.SignalGenerator()
    base = dict(technical=ta.TechnicalIndicators(), fundamental=ta.FundamentalData(),
                risk=ta.RiskMetrics(), sentiment=ta.SentimentData(), insider=ta.InsiderData(),
                valuation=ta.ValuationMetrics())
    buy = gen._calculate_component_scores(market_intel=ta.MarketIntelligence(analyst_rating="STRONG_BUY"), **base)
    none = gen._calculate_component_scores(market_intel=ta.MarketIntelligence(analyst_rating=""), **base)
    assert buy["sentiment"] == none["sentiment"]


def test_trend_score_does_not_pay_a_mean_reversion_bonus():
    gen = ta.SignalGenerator()
    args = (ta.FundamentalData(), ta.RiskMetrics(), ta.SentimentData(), ta.InsiderData(),
            ta.MarketIntelligence(), ta.ValuationMetrics())
    oversold = gen._calculate_component_scores(ta.TechnicalIndicators(rsi=22.0), *args)["technical"]
    neutral = gen._calculate_component_scores(ta.TechnicalIndicators(rsi=50.0), *args)["technical"]
    assert oversold == neutral
    bull, _ = gen._count_confirmations(ta.TechnicalIndicators(rsi=22.0), ta.FundamentalData(
        reported={"roe": False, "debt_to_equity": False}), ta.SentimentData())
    bull_n, _ = gen._count_confirmations(ta.TechnicalIndicators(rsi=50.0), ta.FundamentalData(
        reported={"roe": False, "debt_to_equity": False}), ta.SentimentData())
    assert bull == bull_n


# --- ledger methodology --------------------------------------------------------

def _bars(closes, start="2026-01-05"):
    import pandas as pd
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.001, "Low": c * 0.999, "Close": c})


def test_split_after_publication_does_not_fake_a_stop_touch(tmp_path):
    """Levels are recorded in publication-day prices; the history comes back
    split-adjusted. A 10:1 split must not read as an instant stop-out."""
    from tracking.ledger import SignalLedger
    led = SignalLedger(tmp_path / "l.sqlite3")
    # Adjusted history: the stock traded ~$1000 pre-split, shown as ~$100.
    stock = _bars([100, 101, 102, 103, 104, 105, 106, 107])
    spy = _bars([400] * 8)
    led.record(engine="t", ticker="SPL", as_of=stock.index[0].date(), horizon_days=5,
               stance="BULLISH", action="BUY", reference_price=1000.0,
               stop_price=950.0, target_price=1040.0)
    import sqlite3
    with sqlite3.connect(led.path) as con:   # published before the entry bar
        con.execute("UPDATE signals SET recorded_at=?", (f"{stock.index[0].date()}T21:00:00+00:00",))
    led.resolve_outcomes(lambda t, a, b: stock if t == "SPL" else spy, today=stock.index[-1].date())
    row = led.rows()[0]
    assert row["outcome"] is not None
    assert row["first_touch"] == "target", "levels rescaled x0.1 onto the adjusted history"


def test_unexplainable_level_basis_claims_no_touch():
    from tracking import ledger as lg
    hist = _bars([100, 101])
    dates = [d.date() for d in hist.index]
    r = {"reference_price": 37.0}
    assert lg._level_basis(r, hist, dates, dates[0]) is None
    assert lg._level_basis({"reference_price": 99.0}, hist, dates, dates[0]) == pytest.approx(100 / 99)
    assert lg._level_basis({"reference_price": 1000.0}, hist, dates, dates[0]) == pytest.approx(0.1)


def test_overlapping_horizons_are_not_independent_samples():
    from tracking.ledger import _independent_dates
    days = [f"2026-03-{d:02d}" for d in range(2, 28)]
    assert len(_independent_dates(days, 63)) == 1
    assert _independent_dates(days, 1) == sorted(days)


# --- portfolio throughput: interactive requests are not starved -------------

def test_holdings_analysis_yields_to_a_waiting_tear_sheet_request(monkeypatch):
    import threading
    import time as _t
    web = _webapp()
    order = []
    monkeypatch.setattr(web, "INTERACTIVE_YIELD_SECONDS", 5.0)
    with web._interactive_cv:
        web._interactive_waiting += 1          # a Research request is queued

    def background():
        web._wait_for_interactive_requests(timeout=5.0)
        order.append("holding")

    th = threading.Thread(target=background)
    th.start()
    _t.sleep(0.2)
    order.append("tear sheet")
    with web._interactive_cv:
        web._interactive_waiting -= 1
        web._interactive_cv.notify_all()
    th.join(5)
    assert order == ["tear sheet", "holding"]


def test_decision_states_valuation_conflict_with_a_bullish_call():
    r = ta.AnalysisResult(ticker="JPM", company_name="JPM", current_price=354.0, currency_symbol="$")
    r.valuation = ta.ValuationMetrics(valuation_label="SIGNIFICANTLY_OVERVALUED", fair_value_estimate=264.3)
    r.market_intel = ta.MarketIntelligence(target_mean=374.0)
    out = ta._decision_conflicts(r, "BULLISH")
    assert out and "significantly overvalued" in out[0] and "264.30" in out[0]
    assert ta._decision_conflicts(r, "NEUTRAL") == []


def test_valuation_below_price_is_named_as_such_not_as_missing():
    val = ta.ValuationMetrics(target_base=90.0)
    ts = ta.TradeSetupGenerator().generate(
        100.0, ta.Signal.BUY, ta.TechnicalIndicators(atr=2.0, rsi=50.0), ta.RiskMetrics(), 60.0,
        valuation=val)
    assert ts.has_long_reward is False
    assert 0 < ts.horizon_target < ts.entry_price
    assert ts.risk_reward_ratio == 0.0
    from engine import DecisionInputs, resolve_final_action
    dec = resolve_final_action(DecisionInputs(
        ticker="X", composite_score=70.0, risk_grade="B", risk_reward_ratio=ts.risk_reward_ratio,
        current_price=100.0, stop_loss=ts.stop_loss, target_price=ts.horizon_target))
    assert "TARGET_BELOW_ENTRY" in dec.override_codes
    assert "INVALID_TARGET" not in dec.override_codes


# --- live validation 2026-09-27 (Robinhood-backed run) ------------------------

@pytest.mark.parametrize("info,expected", [
    # Real Yahoo payloads: dividendYield is a PERCENT; ETFs have no dividendRate.
    ({"dividendYield": 1.01, "trailingAnnualDividendYield": 0.0074, "regularMarketPrice": 771.1}, 0.0101),
    ({"dividendYield": 0.34, "currentPrice": 320.0}, 0.0034),          # was read as 34%
    ({"dividendYield": 0.34, "dividendRate": 1.08, "currentPrice": 320.0}, 1.08 / 320.0),
    ({"yield": 0.0125, "dividendYield": 1.3}, 0.0125),
    ({"trailingAnnualDividendYield": 0.023}, 0.023),
    ({}, 0.0),
])
def test_dividend_units_come_from_the_field_not_the_magnitude(info, expected):
    from engine.dividend import dividend_yield_from_info
    assert dividend_yield_from_info(info) == pytest.approx(expected)


def test_research_and_scanner_share_the_dividend_resolver():
    import inspect
    import SP_ELITE_PRO as pro
    assert "dividend_yield_from_info(info)" in inspect.getsource(pro)
    f = ta.FundamentalAnalyzer()._calculate_dividends({"dividendYield": 0.34, "currentPrice": 320.0},
                                                       None, ta.FundamentalData())
    assert f.dividend_yield == pytest.approx(0.0034)


def test_no_street_rating_is_invented_without_coverage():
    assert ta.MarketIntelligence().analyst_rating == ""


def test_etf_composite_excludes_unmeasured_fundamental_and_quality_legs():
    gen = ta.SignalGenerator()
    etf = ta.FundamentalData(is_etf=True, dividend_yield=0.04, quality_score=50.0)
    common = dict(technical=ta.TechnicalIndicators(), risk=ta.RiskMetrics(), sentiment=ta.SentimentData(),
                  insider=ta.InsiderData(), market_intel=ta.MarketIntelligence(),
                  valuation=ta.ValuationMetrics(valuation_score=50.0))
    _s, _c, br, _e = gen.generate(fundamental=etf, **common)
    assert br.weights["fundamental"] == 0.0 and br.weights["quality"] == 0.0
    assert br.fundamental_score == 50.0
    # Only measured legs remain; a 4% yield no longer adds points.
    legs = {"technical": br.technical_score, "valuation": br.valuation_score, "sentiment": br.sentiment_score}
    tot = sum(br.weights[k] for k in legs)
    assert br.composite_score == pytest.approx(sum(legs[k] * br.weights[k] for k in legs) / tot)


# --- VIX provenance (live scan showed a realized-vol proxy printed as "VIX") ---

class _NoData:
    def __init__(self, *a, **k):
        pass

    def history(self, *a, **k):
        import pandas as pd
        return pd.DataFrame()


def _detect(monkeypatch, spy_hist=None):
    import SP_ELITE_PRO as pro
    monkeypatch.setattr(pro, "_import_optional", lambda *a, **k: None)
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    monkeypatch.setattr(pro.MarketRegimeDetector, "_last_good_vix_by_market", {})
    return pro, pro.MarketRegimeDetector.detect_regime(vix=0.0, spy_hist=spy_hist)


def test_realized_vol_proxy_is_labelled_and_not_cached_as_last_good(monkeypatch):
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range("2026-01-01", periods=260)
    spy = pd.DataFrame({"Close": 100 * np.cumprod(1 + np.random.default_rng(0).normal(0, 0.01, 260))}, index=idx)
    pro, reg = _detect(monkeypatch, spy_hist=spy)
    assert reg.vix_source == "realized_vol_proxy" and reg.vix_is_proxy
    assert pro.MarketRegimeDetector._last_good_vix_by_market == {}


def test_no_vix_and_no_proxy_is_a_flagged_placeholder(monkeypatch):
    _pro, reg = _detect(monkeypatch)
    assert reg.vix_source == "default"
    assert reg.vix_is_proxy is True, "a placeholder must cap exposure like a proxy"


def test_exit_decision_publishes_null_levels_not_zero():
    from freezegun import freeze_time
    r = ta.AnalysisResult(ticker="SOUN", company_name="SOUN", current_price=6.05,
                          final_action="AVOID", market_timestamp="2026-09-25")
    r.trade_setup = ta.TradeSetup()           # exit stance: nothing computed
    with freeze_time("2026-09-27 12:00:00"):
        lv = ta.decision_for_analysis(r).to_dict()["levels"]
    assert lv["entry"] is None and lv["stop"] is None and lv["target"] is None


def test_measured_beta_is_not_clamped():
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range("2025-01-01", periods=200)
    bench = pd.Series(np.random.default_rng(3).normal(0, 0.01, 200), index=idx)
    stock = 3.6 * bench + pd.Series(np.random.default_rng(4).normal(0, 0.001, 200), index=idx)
    ra = ta.RiskAnalyzer()
    ra._get_benchmark_returns = lambda t: bench
    r = ra._calculate_beta(stock, ta.RiskMetrics(), "SOUN")
    assert r.beta_status == "measured"
    assert r.beta == pytest.approx(3.6, rel=0.02), "a beta above 3 is not published as 3.00"
