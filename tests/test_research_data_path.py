"""Research data path: symbol routing, honest failure categories, and the
facts a failed or new listing still shows.

The RVII case that prompted this: a new NYSE fund (31 daily bars) was retried
three times and reported as "the provider did not respond", while the engine
had correctly classified it as insufficient history.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
import TradingAnalysis_ELITE as ta  # noqa: E402
from engine.data_status import DataStatus  # noqa: E402
from engine.data_validator import validate_ticker_data  # noqa: E402
from engine.relative_performance import relative_performance  # noqa: E402
from engine.risk_grade import RiskGradeInputs, compute_risk_grade, risk_grade_drivers  # noqa: E402
from engine.symbols import plan_symbol, resolution_note, unsupported_reason  # noqa: E402

# Every state-changing request must carry the app's CSRF header.
QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


# --- symbol planning ---------------------------------------------------------

@pytest.mark.parametrize("raw,market,expected", [
    ("BRK.B", "US", ["BRK-B", "BRK-B.NS"]),
    (" aapl ", "US", ["AAPL", "AAPL.NS"]),
    ("RELIANCE", "IN", ["RELIANCE.NS", "RELIANCE.BO", "RELIANCE"]),
    ("HDFCBANK.BO", None, ["HDFCBANK.BO", "HDFCBANK.NS"]),
    ("M&M.NS", "IN", ["M&M.NS", "M&M.BO"]),
    ("^GSPC", "IN", ["^GSPC"]),
    ("BTC-USD", "IN", ["BTC-USD"]),
    ("EURUSD=X", None, ["EURUSD=X"]),
    ("SHEL.L", "US", ["SHEL.L"]),
])
def test_symbol_plan(raw, market, expected):
    assert plan_symbol(raw, market).candidates == expected


def test_resolution_notes_say_which_listing_was_analysed():
    assert "US listing" in resolution_note(plan_symbol("AAPL", "IN"), "AAPL")
    assert "NSE" in resolution_note(plan_symbol("RELIANCE", "US"), "RELIANCE.NS")
    assert "same company" in resolution_note(plan_symbol("HDFCBANK.BO", None), "HDFCBANK.NS")
    assert "BRK-B" in resolution_note(plan_symbol("BRK.B", "US"), "BRK-B")
    assert resolution_note(plan_symbol("AAPL", "US"), "AAPL") == ""


def test_unsupported_instruments_are_named():
    for qt in ("INDEX", "FUTURE", "CURRENCY", "CRYPTOCURRENCY", "OPTION"):
        assert unsupported_reason(qt)
    for qt in ("EQUITY", "ETF", "MUTUALFUND", ""):
        assert unsupported_reason(qt) == ""


# --- fetch resolution --------------------------------------------------------

class _Cache(dict):
    def get(self, k, default=None):
        return super().get(k, default)

    def set(self, k, v):
        self[k] = v


def _frame(rows, start="2024-01-01"):
    if rows == 0:
        return pd.DataFrame()
    idx = pd.date_range(start, periods=rows, freq="B")
    c = np.linspace(100, 110, rows)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000}, index=idx)


def _fake_yf(listings):
    """listings: symbol -> (rows, info)."""
    class _T:
        def __init__(self, sym):
            self.sym = sym
            self.rows, self._info = listings.get(sym, (0, {}))

        def history(self, period="2y", interval="1d", auto_adjust=True):
            return _frame(self.rows)

        @property
        def info(self):
            return self._info

        fast_info = {}
    return SimpleNamespace(Ticker=_T)


def _analyzer():
    obj = ta.QuantaEliteAnalyzer.__new__(ta.QuantaEliteAnalyzer)
    obj.cache = _Cache()
    return obj


NAMED = {"shortName": "Co", "quoteType": "EQUITY", "marketCap": 1e9}


def test_new_listing_is_not_replaced_by_a_foreign_listing(monkeypatch):
    monkeypatch.setattr(ta, "yf", _fake_yf({"RVII": (31, NAMED), "RVII.NS": (400, NAMED)}))
    data, status, meta = _analyzer()._fetch_price_data_classified("RVII")
    assert status == DataStatus.INSUFFICIENT_HISTORY
    assert meta["variant"] == "RVII" and meta["rows"] == 31
    assert meta["first_bar"] == "2024-01-01"


def test_india_mode_falls_back_to_the_us_listing_with_a_note(monkeypatch):
    monkeypatch.setattr(ta, "yf", _fake_yf({"AAPL": (300, NAMED)}))
    data, status, meta = _analyzer()._fetch_price_data_classified("AAPL", market="IN")
    assert status == DataStatus.OK and meta["variant"] == "AAPL"
    assert "US listing" in meta["resolution_note"]


def test_india_mode_prefers_nse_for_a_bare_symbol(monkeypatch):
    monkeypatch.setattr(ta, "yf", _fake_yf({"INFY": (300, NAMED), "INFY.NS": (300, NAMED)}))
    _, status, meta = _analyzer()._fetch_price_data_classified("INFY", market="IN")
    assert status == DataStatus.OK and meta["variant"] == "INFY.NS"


def test_bse_gap_uses_the_same_companys_nse_listing(monkeypatch):
    monkeypatch.setattr(ta, "yf", _fake_yf({"HDFCBANK.BO": (1, NAMED), "HDFCBANK.NS": (300, NAMED)}))
    _, status, meta = _analyzer()._fetch_price_data_classified("HDFCBANK.BO")
    assert status == DataStatus.OK and meta["variant"] == "HDFCBANK.NS"
    assert "same company" in meta["resolution_note"]


def test_share_class_dot_notation_resolves(monkeypatch):
    monkeypatch.setattr(ta, "yf", _fake_yf({"BRK-B": (300, NAMED)}))
    _, status, meta = _analyzer()._fetch_price_data_classified("BRK.B")
    assert status == DataStatus.OK and meta["variant"] == "BRK-B"


def test_delisted_sentinel_payload_is_invalid_not_a_provider_outage(monkeypatch):
    monkeypatch.setattr(ta, "yf", _fake_yf({"TWTR": (0, {"quoteType": "NONE"})}))
    _, status, _ = _analyzer()._fetch_price_data_classified("TWTR")
    assert status == DataStatus.INVALID_TICKER


def test_profile_without_market_cap_is_still_a_profile(monkeypatch):
    info = {"shortName": "Nippon India ETF Nifty 50 BeES", "quoteType": "EQUITY"}
    monkeypatch.setattr(ta, "yf", _fake_yf({"NIFTYBEES.NS": (300, info)}))
    data, status, _ = _analyzer()._fetch_price_data_classified("NIFTYBEES.NS")
    assert status == DataStatus.OK and not data.attrs.get("info_unavailable")


def test_missing_market_cap_does_not_invalidate_a_named_instrument():
    rep = validate_ticker_data("NIFTYBEES.NS", 264.0, 300, 0, "Nippon India ETF Nifty 50 BeES",
                               quote_type="EQUITY")
    assert rep.is_valid and not rep.has_market_cap


# --- analyze(): resolved symbol, short history, unsupported --------------------

def _stub_fetch(frame, status, variant, info=None):
    if info is not None:
        frame.attrs = {"info": info, "ticker": variant}

    def fetch(ticker, market=None):
        return frame, status, {"variant": variant, "rows": len(frame),
                               "first_bar": str(frame.index[0].date()) if len(frame) else None,
                               "last_bar": str(frame.index[-1].date()) if len(frame) else None,
                               "resolution_note": "note"}
    return fetch


def test_short_history_keeps_identity_and_price(monkeypatch):
    obj = _analyzer()
    obj._fetch_price_data_classified = _stub_fetch(_frame(31), DataStatus.INSUFFICIENT_HISTORY, "RVII")
    monkeypatch.setattr(ta, "yf", _fake_yf({"RVII": (31, {"shortName": "Robinhood Ventures Fund II",
                                                           "quoteType": "EQUITY", "marketCap": 1.7e8})}))
    res = ta.QuantaEliteAnalyzer.analyze(obj, "RVII")
    assert res.data_status == DataStatus.INSUFFICIENT_HISTORY
    assert res.company_name == "Robinhood Ventures Fund II" and res.current_price > 0
    assert res.history_bars == 31 and res.first_bar_date == "2024-01-01"
    assert res.final_action == "" and not res.is_invalid


def test_downstream_uses_the_resolved_listing(monkeypatch):
    obj = _analyzer()
    obj._fetch_price_data_classified = _stub_fetch(_frame(31), DataStatus.INSUFFICIENT_HISTORY, "RELIANCE.NS")
    seen = []
    fake = _fake_yf({"RELIANCE.NS": (31, NAMED)})
    monkeypatch.setattr(ta, "yf", SimpleNamespace(Ticker=lambda s: (seen.append(s) or fake.Ticker(s))))
    res = ta.QuantaEliteAnalyzer.analyze(obj, "RELIANCE", market="IN")
    assert res.ticker == "RELIANCE.NS" and res.requested_symbol == "RELIANCE"
    assert seen and set(seen) == {"RELIANCE.NS"}


def test_index_is_unsupported_not_scored():
    obj = _analyzer()
    obj._fetch_price_data_classified = _stub_fetch(
        _frame(300), DataStatus.OK, "^GSPC", info={"shortName": "S&P 500", "quoteType": "INDEX"})
    res = ta.QuantaEliteAnalyzer.analyze(obj, "^GSPC")
    assert res.data_status == DataStatus.UNSUPPORTED_INSTRUMENT
    assert "index" in res.data_status_reason and res.final_action == ""


# --- webapp: status-driven failures, no blind retries ---------------------------

def _run_job(monkeypatch, result, ticker="RVII", market=None):
    sleeps = []
    monkeypatch.setattr(webapp.time, "sleep", lambda s: sleeps.append(s))
    analyzer = SimpleNamespace(analyze=lambda *a, **k: result)
    monkeypatch.setattr(webapp, "_get_analysis_runtime", lambda: (ta, analyzer, None))
    monkeypatch.setattr(webapp, "_get_market_environment_cached", lambda *a, **k: {})
    monkeypatch.setattr(webapp, "_provider_in_cooldown", lambda: False)
    job_id = uuid.uuid4().hex
    webapp._jobs[job_id] = {"status": "running", "progress": 0, "message": "", "result": None,
                            "error": None, "created": time.time()}
    webapp._run_analysis(job_id, ticker, fast_mode=True, market=market)
    return webapp._jobs.pop(job_id), sleeps


def _result(**over):
    r = ta.AnalysisResult(ticker="RVII")
    for k, v in over.items():
        setattr(r, k, v)
    return r


def test_new_listing_is_reported_as_insufficient_data_without_retries(monkeypatch):
    res = _result(data_status=DataStatus.INSUFFICIENT_HISTORY, current_price=22.11,
                  company_name="Robinhood Ventures Fund II", history_bars=31,
                  first_bar_date="2026-08-13", last_bar_date="2026-09-25",
                  data_status_reason="Only 31 daily bar(s) of history since 2026-08-13 for RVII")
    job, sleeps = _run_job(monkeypatch, res)
    d = job["error_detail"]
    assert sleeps == []                                   # no pointless back-off
    assert d["category"] == "insufficient_data" and d["retryable"] is False
    assert "did not respond" not in job["error"]
    assert d["facts"]["history_bars"] == 31 and d["facts"]["last_price"] == 22.11
    assert "19 more trading session" in d["suggested_action"]


@pytest.mark.parametrize("status,category,retryable", [
    (DataStatus.INVALID_TICKER, "invalid_symbol", False),
    (DataStatus.UNSUPPORTED_INSTRUMENT, "unsupported_instrument", False),
    (DataStatus.STALE_DATA, "stale_data", True),
])
def test_each_status_has_its_own_category(monkeypatch, status, category, retryable):
    job, sleeps = _run_job(monkeypatch, _result(data_status=status, current_price=10.0,
                                                data_status_reason="reason"))
    assert job["error_detail"]["category"] == category
    assert job["error_detail"]["retryable"] is retryable and sleeps == []


def test_only_a_provider_gap_is_retried(monkeypatch):
    job, sleeps = _run_job(monkeypatch, _result(data_status=DataStatus.PROVIDER_DATA_UNAVAILABLE))
    assert len(sleeps) == 2 and job["error_detail"]["category"] == "provider_unavailable"
    assert job["error_detail"]["retryable"] is True


def test_unsupported_index_offers_a_tracking_etf(monkeypatch):
    job, _ = _run_job(monkeypatch, _result(ticker="^GSPC", data_status=DataStatus.UNSUPPORTED_INSTRUMENT,
                                           current_price=7743.0), ticker="^GSPC")
    assert "SPY" in job["error_detail"]["alternatives"]


def test_market_is_part_of_a_bare_symbols_cache_identity():
    assert webapp._cache_identity("INFY", "IN") != webapp._cache_identity("INFY", "US")
    assert webapp._cache_identity("INFY.NS", "US") == "INFY.NS"


def test_endpoint_accepts_ampersand_symbols_and_the_market(monkeypatch):
    started = {}
    monkeypatch.setattr(webapp.threading, "Thread",
                        lambda target, args, kwargs, daemon: SimpleNamespace(
                            start=lambda: started.update(args=args, kwargs=kwargs)))
    client = webapp.app.test_client()
    resp = client.post("/api/analyze", headers=QUANTA_HEADERS, json={"ticker": "M&M.NS", "market": "IN", "fast_mode": True})
    assert resp.status_code == 202
    assert started["args"][1] == "M&M.NS" and started["kwargs"]["market"] == "IN"
    webapp._jobs.clear()


# --- research analytics ---------------------------------------------------------

def test_relative_performance_uses_the_same_sessions_and_refuses_short_benchmarks():
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    stock = pd.Series(np.linspace(100, 130, 300), index=idx)
    bench_px = pd.Series(np.linspace(100, 110, 300), index=idx)
    out = relative_performance(stock, bench_px.pct_change().dropna(), "SPY")
    row = next(r for r in out["rows"] if r["window"] == "3M")
    assert row["stock_pct"] == pytest.approx((stock.iloc[-1] / stock.iloc[-64] - 1) * 100, abs=0.01)
    assert row["benchmark_pct"] == pytest.approx((bench_px.iloc[-1] / bench_px.iloc[-64] - 1) * 100, abs=0.01)
    short = relative_performance(stock, bench_px.iloc[-200:].pct_change().dropna(), "SPY")
    one_year = next(r for r in short["rows"] if r["window"] == "1Y")
    assert one_year["stock_pct"] is not None and one_year["benchmark_pct"] is None


def test_risk_grade_drivers_add_up_to_the_grade():
    inputs = RiskGradeInputs(annualized_volatility=0.44, beta=1.91, max_drawdown=-0.37,
                             tail_risk_score=70, liquidity_score=10)
    points, grade = compute_risk_grade(inputs)
    assert points == 3 + sum(d["points"] for d in risk_grade_drivers(inputs))
    assert grade == "F"


def test_target_components_carry_each_method_and_its_weight():
    val = ta.ValuationMetrics(dcf_status="measured", dcf_fair_value=120.0,
                              valuation_status="measured", fair_value_estimate=120.0,
                              fair_value_sources=["dcf"])
    out = ta.ValuationAnalyzer().calculate_composite_targets(
        100.0, val, ta.MarketIntelligence(target_mean=130.0, analyst_count=20),
        ta.MonteCarloResults(), ta.TechnicalIndicators(resistance_level=104.0))
    comps = out.target_components
    # Resistance is a tactical level, not a 12-month valuation input.
    assert [c["source"] for c in comps] == ["analyst_consensus", "dcf"]
    assert sum(c["weight"] for c in comps) == pytest.approx(1.0, abs=1e-3)
    assert out.target_base == pytest.approx(sum(c["value"] * c["weight"] for c in comps), rel=1e-3)


def test_52_week_range_uses_traded_highs_and_lows():
    tech = ta.TechnicalIndicators()
    idx = pd.date_range("2025-01-01", periods=260, freq="B")
    close = pd.Series(np.full(260, 100.0), index=idx)
    out = ta.TechnicalAnalyzer()._calculate_52week_range(
        close, 100.0, tech, high=close + 5, low=close - 5)
    assert out.week_52_high == 105.0 and out.week_52_low == 95.0


def test_payload_publishes_resolution_business_and_peer_roe_delta():
    r = ta.AnalysisResult(ticker="RELIANCE.NS", requested_symbol="RELIANCE",
                          symbol_note="Analysed RELIANCE.NS (NSE).",
                          business_summary="Reliance Industries operates in energy.")
    r.peer_comparison = ta.PeerComparison(roe_stock=10.0, roe_sector_avg=8.0, roe_vs_sector=25.0)
    d = ta._mcp_result_to_dict(r)
    assert d["requested_symbol"] == "RELIANCE" and d["symbol_note"].startswith("Analysed")
    assert d["business_summary"].startswith("Reliance")
    assert d["peer_comparison"]["roe_vs_sector"] == 25.0
