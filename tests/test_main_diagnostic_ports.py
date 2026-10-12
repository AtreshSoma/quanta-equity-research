"""Selective Main diagnostics must not change PRO's trade permissions.

All providers and account values here are synthetic. Existing golden scans
and funding regression tests cover the decision-bearing fields separately.
"""
import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)
import SP_ELITE_PRO as sp
from test_elite_pro_review2_fixes import _bare_system, _pick


def _diagnostic_engine():
    eng = _bare_system()
    eng._diagnostics_lock = threading.Lock()
    eng._provider_reads = {}
    eng._candidate_rejections = {}
    eng.scan_results = []
    return eng


def test_provider_observer_preserves_arguments_retries_and_exception_identity():
    eng = _diagnostic_engine()
    calls = []
    error = TimeoutError("sensitive provider response must not be stored")

    class Provider:
        def history(self, *args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise error
            return kwargs["period"]

    ticker = sp._ScannerTicker(Provider(), eng, "SYN")
    with pytest.raises(TimeoutError) as caught:
        ticker.history(period="2y", timeout=7)
    assert caught.value is error
    assert ticker.history(period="2y", timeout=7) == "2y"
    assert ticker.history(period="1mo", timeout=3) == "1mo"
    assert calls == [((), {"period": "2y", "timeout": 7}),
                     ((), {"period": "2y", "timeout": 7}),
                     ((), {"period": "1mo", "timeout": 3})]
    row = eng.provider_diagnostics()[0]["reads"][0]
    assert row == dict(field="history", required=True, status="available", error_type="", failures=1)
    assert "sensitive" not in json.dumps(eng.provider_diagnostics())


def test_lazy_fast_info_is_optional_and_does_not_hide_required_failure():
    eng = _diagnostic_engine()

    class FastInfo:
        @property
        def market_cap(self):
            raise ConnectionError("private response")

    class Provider:
        fast_info = FastInfo()
        calendar = {}

        @property
        def info(self):
            raise TimeoutError("private response")

        def get_earnings_dates(self, limit=1):
            return [limit]

    ticker = sp._ScannerTicker(Provider(), eng, "SYN")
    with pytest.raises(ConnectionError):
        ticker.fast_market_cap()
    with pytest.raises(TimeoutError):
        _ = ticker.info
    assert ticker.calendar == {}
    assert ticker.get_earnings_dates(limit=4) == [4]
    reads = {r["field"]: r for r in eng.provider_diagnostics()[0]["reads"]}
    assert reads["fast_info"]["required"] is False
    assert reads["info"]["required"] is True
    assert reads["info"]["status"] == "failed"
    assert reads["calendar"]["status"] == "empty"
    assert reads["get_earnings_dates"]["status"] == "available"
    assert "private" not in json.dumps(reads)


@pytest.mark.parametrize("value,flag,expected", [
    (0.0, True, True), (-0.1, True, True), (np.float64(2), True, True),
    (10.0, False, False), (float("nan"), True, False), (float("inf"), True, False),
    (True, True, False), (np.bool_(True), True, False), ("2", True, False),
    (2 + 1j, True, False), (None, True, False),
])
def test_metric_availability_requires_reported_finite_real_value(value, flag, expected):
    fm = sp.FundamentalMetrics(debt_to_equity=value, data_available={"debt_to_equity": flag})
    assert fm.has_metric("debt_to_equity") is expected
    assert not fm.has_metric("unknown_field")
    fm.data_available = {}
    assert not fm.has_metric("debt_to_equity")


def test_nonfinite_reported_fundamentals_do_not_pollute_populations_or_ranking():
    rows = [_pick(f"SYN{i}", score=8, seed=i) for i in range(6)]
    for i, row in enumerate(rows):
        row.fundamental_metrics.debt_to_equity = float(i)
        row.fundamental_metrics.data_available = {"debt_to_equity": True}
    rows[-1].fundamental_metrics.debt_to_equity = float("nan")
    stats = sp.UniverseStats.from_analyses(rows)
    assert stats.debt_to_equity == [0., 1., 2., 3., 4.]
    observed = []
    original = stats._zscore

    def checked(value, population, **kwargs):
        assert np.isfinite(value)
        observed.append(value)
        return original(value, population, **kwargs)

    stats._zscore = checked
    before = rows[-1].overall_score
    sp.ScoringEngine.normalize_scores(rows[-1], stats, sp._REGIME_WEIGHTS["NEUTRAL"])
    assert rows[-1].overall_score == before
    assert np.isfinite(rows[-1].ranking_score)
    assert observed


def test_rejection_detail_is_unique_does_not_count_and_uses_actual_rsi_limit():
    eng = _diagnostic_engine()
    before = dict(eng._filter_stats)
    eng._record_candidate_rejection("SYN", "score_below_min", 3, "first rule")
    eng._record_candidate_rejection("SYN", "zero_allocation", 3, "later rule")
    assert eng.candidate_rejections() == [dict(ticker="SYN", reason="score_below_min", score=3., detail="first rule")]
    assert eng._filter_stats == before
    eng.scan_results = [SimpleNamespace(ticker="SYN")]
    assert eng.candidate_rejections() == []
    for scan_type in ("standard", "growth"):
        eng.current_scan_type = scan_type
        limit = eng._entry_rsi_limit()
        ti = sp.TechnicalIndicators(rsi=limit + 1)
        assert f"{limit:g}" in eng._signal_rejection_detail("HOLD", 8., ti)
        assert "RSI" in eng._signal_rejection_detail("HOLD", 8., ti)


def test_research_evidence_has_unknowns_and_does_not_change_trade_state():
    rec = _pick("SYN")
    rec.fundamental_metrics.data_available = {"debt_to_equity": True, "pe_ratio": True}
    rec.fundamental_metrics.pe_ratio = float("nan")
    rec.fundamental_metrics.debt_to_equity = 0.
    rec.provider_reads = [dict(field="news", status="failed", required=False)]
    before = (rec.overall_score, rec.final_action, rec.allocation, rec.shares)
    evidence = rec.to_dict()["research_evidence"]
    assert evidence["fundamentals"]["debt_to_equity"] == 0.
    assert evidence["fundamentals"]["pe_ratio"] is None
    assert evidence["fundamentals"]["revenue_growth"] is None
    assert evidence["factor_scores"]["eps_revisions"] is None
    evidence["provider_reads"][0]["status"] = "changed"
    assert rec.provider_reads[0]["status"] == "failed"
    assert (rec.overall_score, rec.final_action, rec.allocation, rec.shares) == before


def test_optional_provider_failure_preserves_synthetic_scan_business_result(monkeypatch):
    import test_golden_scan_synthetic as golden
    baseline = golden._business(golden._scan(monkeypatch))
    original = sp._ScannerTicker.fast_market_cap

    def unavailable(self):
        def fail():
            raise TimeoutError("synthetic lazy fast_info outage")
        return self._read("fast_info", fail)

    monkeypatch.setattr(sp._ScannerTicker, "fast_market_cap", unavailable)
    result = golden._scan(monkeypatch)
    assert golden._business(result) == baseline
    assert result["funded_count"] >= 1
    reads = result["error_summary"]["provider_reads"]
    assert any(r["status"] == "failed" and not r["required"]
               for row in reads for r in row["reads"] if r["field"] == "fast_info")
    assert all(row["research_evidence"] for row in result["stock_picks"])
    assert isinstance(result["candidate_rejections"], list)
    monkeypatch.setattr(sp._ScannerTicker, "fast_market_cap", original)


def test_deferred_required_reads_make_no_provider_request(monkeypatch):
    eng = _diagnostic_engine()
    eng._provider_circuit_open = True
    monkeypatch.setattr(sp.yf, "Ticker", lambda *_: pytest.fail("circuit must skip provider"))
    assert eng.analyze_stock("SYN") is None
    rows = eng.provider_diagnostics()[0]["reads"]
    assert {r["field"] for r in rows} == {"history", "info"}
    assert all(r["required"] and r["status"] == "deferred" and r["failures"] == 0 for r in rows)


def test_rescan_discards_old_diagnostics(monkeypatch):
    import test_scan_accounting as acc
    acc._install(monkeypatch)
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type="custom", custom_tickers=["SYNA"])
    assert any(r["ticker"] == "SYNA" for r in eng.provider_diagnostics())
    eng._record_candidate_rejection("STALE", "score_below_min", 1, "old scan")
    eng.scan_market(scan_type="custom", custom_tickers=["SYND"])
    assert {r["ticker"] for r in eng.provider_diagnostics()} == {"SYND"}
    assert eng.candidate_rejections() == []


def test_export_carries_diagnostics_without_provider_response_text(tmp_path):
    from datetime import datetime
    eng = _diagnostic_engine()
    eng.analysis_timestamp = datetime(2026, 10, 5)
    eng._record_provider_read("SYN", "news", "failed", "TimeoutError")
    eng._record_candidate_rejection("LOW", "score_below_min", 3, "Score below entry floor")
    assert eng.export_results([_pick("SYN")], output_dir=str(tmp_path))
    payload = json.loads(next(tmp_path.glob("quanta_detailed_*.json")).read_text(encoding="utf-8"))
    assert payload["provider_reads"] == eng.provider_diagnostics()
    assert payload["candidate_rejections"] == eng.candidate_rejections()
    assert payload["recommendations"][0]["research_evidence"]["source"] == "QUANTA heuristic model"
