"""Backend contracts behind the UI audit.

* rejections carry a stable rule code, index-aligned with their prose
* skipped / failed insider analysis is not reported as measured inactivity
* regime data coverage counts measured inputs, not placeholder factors
* the track record names each call's real benchmark and pending state
* tape quotes carry the session they belong to, apart from retrieval time
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
import TradingAnalysis_ELITE as ta  # noqa: E402
from engine import DecisionInputs, FinalAction, resolve_final_action  # noqa: E402
from engine.decision import Decision, Horizon  # noqa: E402
from engine.evidence import ELITE, get_evidence  # noqa: E402
from engine.risk_overrides import apply_risk_override_details, apply_risk_overrides  # noqa: E402
from tracking.ledger import SignalLedger, pending_schedule  # noqa: E402


def _inputs(**over):
    kw = dict(ticker="T", composite_score=80.0, risk_grade="B", risk_reward_ratio=3.0,
              current_price=100.0, stop_loss=92.0, target_price=130.0)
    kw.update(over)
    return DecisionInputs(**kw)


# --- 4. rejection rule codes -------------------------------------------------

def test_override_codes_identify_the_rule_that_fired():
    _, details = apply_risk_override_details(_inputs(risk_grade="F"), FinalAction.BUY)
    assert [code for code, _t in details] == ["RISK_GRADE_F"]
    _, details = apply_risk_override_details(_inputs(risk_reward_ratio=0.5), FinalAction.BUY)
    assert [code for code, _t in details] == ["LOW_REWARD_RISK"]


def test_the_text_api_is_unchanged_and_aligned_with_the_codes():
    action, texts = apply_risk_overrides(_inputs(risk_grade="F"), FinalAction.BUY)
    action2, details = apply_risk_override_details(_inputs(risk_grade="F"), FinalAction.BUY)
    assert action == action2 and texts == [t for _c, t in details]


def test_resolved_decision_carries_index_aligned_codes():
    res = resolve_final_action(_inputs(risk_grade="F"), starting_candidate=FinalAction.BUY,
                               enforce_price_gates=False)
    assert res.final_action == FinalAction.WATCHLIST
    assert res.override_codes == ["RISK_GRADE_F"]
    assert len(res.override_codes) == len(res.overrides_applied)
    assert res.blocking_codes == ["RISK_GRADE_F"]
    # A risk-grade F is not a reward-to-risk problem.
    assert "LOW_REWARD_RISK" not in res.override_codes


def test_ownership_collapse_has_its_own_code():
    res = resolve_final_action(_inputs(composite_score=50.0))
    assert res.override_codes == ["HOLD_NOT_OWNED"]


# --- 3. insider provenance ---------------------------------------------------

def test_fast_mode_marks_insider_as_not_fetched():
    analyzer = object.__new__(ta.QuantaEliteAnalyzer)
    analyzer.technical_analyzer = MagicMock(analyze=MagicMock(return_value=ta.TechnicalIndicators()))
    analyzer.risk_analyzer = MagicMock(analyze=MagicMock(return_value=ta.RiskMetrics()))
    analyzer.fundamental_analyzer = MagicMock(analyze=MagicMock(return_value=ta.FundamentalData()))
    analyzer.insider_analyzer = MagicMock(analyze=MagicMock())
    analyzer.valuation_analyzer = MagicMock(analyze=MagicMock(return_value=ta.ValuationMetrics()))
    result = ta.AnalysisResult()
    result.current_price = 100.0
    idx = pd.date_range("2024-01-01", periods=260, freq="B")
    closes = [100 + i * 0.1 for i in range(260)]
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1}, index=idx)
    out = analyzer._run_parallel_analysis("AAPL", df, result, fast_mode=True)
    assert out.insider.data_status == "not_fetched"
    assert ta._mcp_result_to_dict(out)["insider"]["data_status"] == "not_fetched"


def test_a_failed_optional_insider_check_in_fast_mode_is_unavailable():
    analyzer = object.__new__(ta.QuantaEliteAnalyzer)
    analyzer.technical_analyzer = MagicMock(analyze=MagicMock(return_value=ta.TechnicalIndicators()))
    analyzer.risk_analyzer = MagicMock(analyze=MagicMock(return_value=ta.RiskMetrics()))
    analyzer.fundamental_analyzer = MagicMock(analyze=MagicMock(return_value=ta.FundamentalData()))
    analyzer.insider_analyzer = MagicMock(analyze=MagicMock(side_effect=RuntimeError("boom")))
    analyzer.valuation_analyzer = MagicMock(analyze=MagicMock(return_value=ta.ValuationMetrics()))
    result = ta.AnalysisResult()
    result.current_price = 100.0
    idx = pd.date_range("2024-01-01", periods=260, freq="B")
    closes = [100 + i * 0.1 for i in range(260)]
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1}, index=idx)
    out = analyzer._run_parallel_analysis("AAPL", df, result, fast_mode=True, include_insider=True)
    assert out.insider.data_status == "unavailable"


def test_insider_zero_is_measured_only_when_a_source_answered():
    analyzer = ta.InsiderAnalyzer(SimpleNamespace(finnhub_key=""))
    empty = SimpleNamespace(info={}, insider_transactions=pd.DataFrame())
    with patch.object(ta.yf, "Ticker", return_value=empty):
        assert analyzer.analyze("ANY").data_status == "measured"       # answered, nothing in 90d
    with patch.object(ta.yf, "Ticker", side_effect=RuntimeError("down")):
        down = analyzer.analyze("ANY")
    assert down.data_status == "unavailable"                            # nobody answered
    assert down.recent_buys == 0 and down.recent_sells == 0


# --- 7. regime coverage ------------------------------------------------------

_QUOTES = [{"symbol": s, "price": 100.0, "change_pct": 0.3}
           for s in ("SPY", "QQQ", "DIA", "IWM", "^VIX", "^TNX")]


def _regime(feeds_ok=True, catalysts=()):
    status = [{"name": f"F{i}", "ok": feeds_ok} for i in range(8)]
    return webapp._build_market_regime_engine(
        "US", "SPY", {"price": 100, "change_pct": 0.3}, {"price": 15.0, "change_pct": 1.0},
        {"price": 4.2, "change_pct": 0.1}, _QUOTES, {}, ["SPY", "QQQ"], 55.0,
        news={"articles": [], "groups": {}}, catalysts=list(catalysts), source_status=status,
    )["dataFreshness"]


def test_coverage_is_not_100_when_inputs_are_missing():
    fresh = _regime()
    # No trend history (proxy only, half credit) and no momentum (none) in this fixture.
    assert "momentum" in fresh["unmeasuredFactors"] and "trend" in fresh["unmeasuredFactors"]
    assert fresh["coveragePct"] == 85.0
    assert "momentum" in fresh["missingInputs"] and "trend_history" in fresh["missingInputs"]


def test_a_news_outage_lowers_coverage_but_a_quiet_live_tape_does_not():
    assert _regime(feeds_ok=False)["coveragePct"] == 75.0
    assert "news_impact" not in _regime(feeds_ok=True)["unmeasuredFactors"]


# --- 6 + 15. track record ----------------------------------------------------

def _decision(ticker, as_of, horizon=21):
    return Decision(
        ticker=ticker, engine="elite", horizon=Horizon("swing", horizon, f"{horizon} sessions"),
        action=FinalAction.BUY, evidence=get_evidence(ELITE), as_of=as_of, score=71.0,
        reference_price=100.0, entry=100.0, stop=95.0, target=115.0,
    )


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "signals.sqlite3"
    monkeypatch.setenv("QUANTA_LEDGER_PATH", str(path))
    return SignalLedger(path)


def test_track_record_reports_each_calls_real_benchmark(ledger):
    ledger.record_decision(_decision("AAPL", date.today().isoformat()))
    ledger.record_decision(_decision("RELIANCE.NS", date.today().isoformat()))
    body = webapp.app.test_client().get("/api/track-record").get_json()
    assert body["benchmarks"] == ["SPY", "^NSEI"]
    assert body["benchmark_mixed"] is True and body["benchmark"] is None
    by_ticker = {r["ticker"]: r["benchmark"] for r in body["recent"]}
    assert by_ticker == {"AAPL": "SPY", "RELIANCE.NS": "^NSEI"}
    assert body["groups"][0]["benchmarks"] == ["SPY", "^NSEI"]
    assert "^NSEI" in body["method"]


def test_a_single_market_ledger_keeps_its_one_benchmark(ledger):
    ledger.record_decision(_decision("RELIANCE.NS", date.today().isoformat()))
    body = webapp.app.test_client().get("/api/track-record").get_json()
    assert body["benchmark"] == "^NSEI" and body["benchmark_mixed"] is False


def test_pending_calls_are_split_by_where_they_stand_on_their_own_schedule(ledger):
    ledger.record_decision(_decision("AAPL", date.today().isoformat(), horizon=63))
    old = (date.today() - timedelta(days=400)).isoformat()
    ledger.record_decision(_decision("MSFT", old, horizon=5))
    # ``recorded_at`` is the publication time; back-date it with the as-of date.
    with ledger._connect() as con:
        con.execute("UPDATE signals SET recorded_at=? WHERE ticker='MSFT'",
                    (datetime.fromisoformat(old).replace(tzinfo=timezone.utc).isoformat(),))
    body = webapp.app.test_client().get("/api/track-record").get_json()
    totals = body["totals"]
    assert totals["pending"] == 2
    assert totals["awaiting_maturity"] == 1 and totals["awaiting_resolution"] == 1
    states = {r["ticker"]: r["state"] for r in body["recent"]}
    assert states == {"AAPL": "awaiting_maturity", "MSFT": "awaiting_resolution"}
    assert totals["earliest_expected_exit"] is not None


def test_maturity_starts_from_publication_not_the_analysis_date():
    # Analysed Friday 2026-09-04 but only published Tuesday 2026-09-08 (after Labor Day).
    row = {"as_of_date": "2026-09-04", "horizon_days": 5,
           "recorded_at": datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc).isoformat()}
    sched = pending_schedule(row, today=date(2026, 9, 9))
    assert sched["expected_entry"] == "2026-09-09"          # first session after publication
    assert sched["expected_exit"] == "2026-09-15"           # 5th session from entry
    assert sched["state"] == "awaiting_maturity"
    from_analysis = pending_schedule({**row, "recorded_at": "2026-09-04T20:00:00+00:00"}, today=date(2026, 9, 9))
    assert from_analysis["expected_exit"] < sched["expected_exit"]


# --- 13. quote session vs retrieval -----------------------------------------

def test_tape_quote_reports_its_session_date_and_basis(monkeypatch):
    idx = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"])
    hist = pd.DataFrame({"Close": [99.0, 100.0, 101.0]}, index=idx)
    fake = SimpleNamespace(history=lambda **kw: hist, fast_info={})
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: fake)
    q = webapp._fetch_tape_quote("SPY", "S&P 500")
    assert q["quote_date"] == "2026-09-23" and q["price_basis"] == "daily_bar"

    empty = SimpleNamespace(history=lambda **kw: pd.DataFrame(),
                            fast_info={"last_price": 55.0, "previous_close": 54.0})
    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: empty)
    q = webapp._fetch_tape_quote("SPY", "S&P 500")
    assert q["quote_date"] is None and q["price_basis"] == "last_price"


def test_snapshot_separates_retrieval_time_and_flags_cached_quotes(monkeypatch):
    webapp._tape_quote_cache.clear()
    monkeypatch.setattr(webapp, "_fetch_tape_quote", lambda s, l: {
        "symbol": s, "label": l, "price": 10.0, "change_pct": 1.0, "quote_date": "2026-09-23", "price_basis": "daily_bar"})
    first = webapp._quote_snapshot("SPY", "S&P 500")
    assert first["retrieved_at"] == first["as_of"] and first["quote_date"] == "2026-09-23"
    again = webapp._quote_snapshot("SPY", "S&P 500")
    assert again["cached"] is True
    webapp._tape_quote_cache.clear()
