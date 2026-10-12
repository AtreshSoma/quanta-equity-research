"""Signal ledger: T+N measurement, benchmarking, bearish calls, isolation."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tracking.ledger import DEFAULT_PATH, SignalLedger, record_decisions, resolve_path
from engine.market_calendar import last_completed_session


def _frame(closes, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c})


@pytest.fixture
def ledger(tmp_path):
    class HistoricalLedger(SignalLedger):
        """Fixture calls are historical replays published on their as-of day."""

        def record(self, **kwargs):
            row_id = super().record(**kwargs)
            if row_id is not None:
                as_of = str(kwargs["as_of"])[:10]
                with self._connect() as con:
                    con.execute("UPDATE signals SET recorded_at=? WHERE id=?",
                                (f"{as_of}T22:00:00+00:00", row_id))
            return row_id

    return HistoricalLedger(tmp_path / "l.sqlite3")


def _loader(frames):
    def load(ticker, start, end):
        return frames[ticker]
    return load


def test_pytest_never_resolves_production_path(monkeypatch):
    monkeypatch.delenv("QUANTA_LEDGER_PATH", raising=False)
    assert resolve_path() != DEFAULT_PATH


def test_exit_is_nth_session_close_after_next_open(ledger):
    # Five sessions from the next open end at day 5's close.
    stock = _frame([100 + i for i in range(30)])
    spy = _frame([400.0] * 30)
    ledger.record(engine="t", ticker="abc", as_of=stock.index[0], horizon_days=5,
                  stance="BULLISH", action="BUY")
    n = ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": spy}), today=date(2026, 3, 1))
    assert n == 1
    row = ledger.rows()[0]
    assert row["entry_date"] == stock.index[1].date().isoformat()
    assert row["entry_price"] == pytest.approx(101.0)
    assert row["entry_basis"] == "next_session_open"
    assert row["exit_date"] == stock.index[5].date().isoformat()
    assert row["exit_close"] == pytest.approx(105.0)
    assert row["return_pct"] == pytest.approx((105 / 101 - 1) * 100)
    assert row["excess_return_pct"] == pytest.approx((105 / 101 - 1) * 100)
    assert row["outcome"] == "WIN"


def test_late_publication_cannot_claim_prior_returns(tmp_path):
    ledger = SignalLedger(tmp_path / "live.sqlite3")
    stock = _frame([100 + i for i in range(20)], start="2026-01-05")
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0],
                  horizon_days=5, stance="BULLISH", action="BUY")
    assert ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": stock}),
                                   today=date(2026, 9, 24)) == 0
    assert ledger.rows()[0]["outcome"] is None


def test_next_open_requires_matching_benchmark_entry(ledger):
    stock = _frame([100 + i for i in range(8)])
    spy = _frame([400.0] * 8).drop(index=stock.index[1])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0],
                  horizon_days=5, stance="BULLISH", action="BUY")
    assert ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": spy})) == 0


def test_unelapsed_horizon_stays_pending(ledger):
    stock = _frame([100.0] * 4)
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=10,
                  stance="BULLISH", action="BUY")
    assert ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": stock})) == 0
    assert ledger.rows()[0]["outcome"] is None


def test_excess_return_is_measured_over_identical_dates(ledger):
    stock = _frame([100, 102, 104, 106, 108, 110])
    spy = _frame([100, 101, 102, 103, 104, 110])     # SPY also +10% by day 5
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=5,
                  stance="BULLISH", action="BUY")
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": spy}))
    row = ledger.rows()[0]
    assert row["return_pct"] == pytest.approx((110 / 102 - 1) * 100)
    assert row["benchmark_return_pct"] == pytest.approx((110 / 101 - 1) * 100)
    assert row["excess_return_pct"] == pytest.approx(row["return_pct"] - row["benchmark_return_pct"])
    # Lagging the market is not a win for a bullish call.
    assert row["outcome"] == "LOSS"


def test_bearish_calls_are_recorded_and_scored(ledger):
    stock = _frame([100, 98, 96, 94, 92, 90])
    spy = _frame([100.0] * 6)
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=5,
                  stance="BEARISH", action="AVOID")
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": spy}))
    row = ledger.rows()[0]
    assert row["stance"] == "BEARISH"
    assert row["outcome"] == "WIN"
    stats = ledger.summarize()
    assert stats[0].mean_excess_pct == pytest.approx((1 - 90 / 98) * 100)


def test_same_session_rerun_preserves_first_published_call(ledger):
    for score in (61.0, 64.0):
        ledger.record(engine="t", ticker="ABC", as_of="2026-01-05", horizon_days=10,
                      stance="BULLISH", action="BUY", score=score)
    rows = ledger.rows()
    assert len(rows) == 1
    assert rows[0]["score"] == 61.0


def test_resolved_rows_are_immutable(ledger):
    stock = _frame([100 + i for i in range(10)])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=2,
                  stance="BULLISH", action="BUY", score=60)
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": stock}))
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=2,
                  stance="BEARISH", action="AVOID", score=10)
    row = ledger.rows()[0]
    assert row["stance"] == "BULLISH" and row["score"] == 60


def test_stop_touched_first_is_detected(ledger):
    closes = [100, 99, 94, 110, 111, 112]
    stock = _frame(closes)
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=5,
                  stance="BULLISH", action="BUY", stop_price=95.0, target_price=108.0)
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": _frame([100.0] * 6)}))
    assert ledger.rows()[0]["first_touch"] == "stop"


def test_neutral_and_invalid_views(ledger):
    assert ledger.record(engine="t", ticker="ABC", as_of="2026-01-05", horizon_days=5,
                         stance="NONE", action="INVALID") is None
    assert ledger.record(engine="t", ticker="ABC", as_of="2026-01-05", horizon_days=5,
                         stance="NEUTRAL", action="WATCHLIST") is not None


def test_small_samples_are_flagged_insufficient(ledger):
    stock = _frame([100 + i for i in range(20)])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=3,
                  stance="BULLISH", action="BUY")
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": _frame([100.0] * 20)}))
    s = ledger.summarize()[0]
    assert not s.sufficient
    assert "Insufficient sample" in s.verdict


def test_significance_uses_per_date_means(ledger):
    rng = np.random.default_rng(1)
    n_days = 80
    spy = _frame([100.0] * (n_days + 10))
    frames = {"SPY": spy}
    # 40 dates x 2 tickers, each call +2% excess with noise -> significant.
    for k, t in enumerate(["AAA", "BBB"]):
        base = 100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.002, n_days + 10))
        frames[t] = _frame(base)
    for i in range(0, 80, 2):
        for t in ("AAA", "BBB"):
            ledger.record(engine="t", ticker=t, as_of=spy.index[i], horizon_days=5,
                          stance="BULLISH", action="BUY")
    ledger.resolve_outcomes(_loader(frames))
    s = ledger.summarize(group_by=("engine",))[0]
    assert s.n_resolved == 80
    # 40 publication dates two sessions apart with a 5-session horizon overlap;
    # inference uses only the 14 whose windows do not (one per 7 calendar days).
    assert s.n_dates == 14
    assert s.sufficient
    assert s.t_stat > 2
    assert "exploratory" in s.verdict.lower()


def test_missing_benchmark_does_not_turn_absolute_gain_into_a_win(ledger):
    stock = _frame([100, 102, 104, 106])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=3,
                  stance="BULLISH", action="BUY")
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": None}))
    assert ledger.rows()[0]["outcome"] is None


def test_missing_entry_bar_or_benchmark_exit_keeps_call_pending(ledger):
    stock = _frame([100, 102, 104, 106])
    spy = _frame([100, 101, 102, 103])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=3,
                  stance="BULLISH", action="BUY")
    ledger.resolve_outcomes(_loader({"ABC": stock.iloc[1:], "SPY": spy}))
    assert ledger.rows()[0]["outcome"] is None
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": spy.iloc[:-1]}))
    assert ledger.rows()[0]["outcome"] is None


def test_later_ticker_does_not_truncate_earlier_benchmark_history(ledger):
    stock = _frame([100, 102, 104, 106, 108, 110])
    spy = _frame([100, 101, 102, 103, 104, 105])
    ledger.record(engine="t", ticker="AAA", as_of=stock.index[3], horizon_days=2,
                  stance="BULLISH", action="BUY")
    ledger.record(engine="t", ticker="ZZZ", as_of=stock.index[0], horizon_days=2,
                  stance="BULLISH", action="BUY")
    def loader(ticker, start, end):
        return {"AAA": stock, "ZZZ": stock, "SPY": spy}[ticker].loc[str(start):str(end)]
    assert ledger.resolve_outcomes(loader, today=stock.index[-1].date()) == 2
    assert all(r["benchmark_return_pct"] is not None for r in ledger.rows())


def test_future_exit_is_not_resolved(ledger):
    stock = _frame([100, 102, 104, 106])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=3,
                  stance="BULLISH", action="BUY")
    assert ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": stock}),
                                   today=stock.index[2].date()) == 0


def test_default_resolution_cutoff_is_last_completed_session(ledger, monkeypatch):
    stock = _frame([100, 102, 104, 106])
    ledger.record(engine="t", ticker="ABC", as_of=stock.index[0], horizon_days=3,
                  stance="BULLISH", action="BUY")
    monkeypatch.setattr("tracking.ledger.last_completed_session",
                        lambda: stock.index[2].date())
    assert ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": stock})) == 0


def test_group_by_context_condition(ledger):
    stock = _frame([100 + i for i in range(10)])
    for regime, day in (("BULL", 0), ("BEAR", 1)):
        ledger.record(engine="t", ticker="ABC", as_of=stock.index[day], horizon_days=2,
                      stance="BULLISH", action="BUY", context={"regime": regime})
    ledger.resolve_outcomes(_loader({"ABC": stock, "SPY": stock}))
    keys = {s.key["regime"] for s in ledger.summarize(group_by=("regime",))}
    assert keys == {"BULL", "BEAR"}


def test_deep_analysis_records_final_bearish_view_only_on_completed_fresh_bar(ledger):
    from TradingAnalysis_ELITE import AnalysisResult, record_authoritative_signal

    result = AnalysisResult(ticker="ABC", current_price=100.0)
    result.market_timestamp = last_completed_session().isoformat()
    result.final_action = "WATCHLIST"
    result.final_stance = "BEARISH"  # AVOID softened for a non-holder
    assert record_authoritative_signal(result, ledger=ledger) == 4
    rows = ledger.rows()
    assert {r["horizon_days"] for r in rows} == {5, 10, 30, 63}
    assert {r["stance"] for r in rows} == {"BEARISH"}
    assert {r["as_of_date"] for r in rows} == {result.market_timestamp}
    assert record_authoritative_signal(result, ledger=ledger) == 4
    assert len(ledger.rows()) == 4

    result.market_timestamp = (last_completed_session() - timedelta(days=14)).isoformat()
    assert record_authoritative_signal(result, ledger=ledger) == 0


def test_shortterm_published_decision_is_recorded_once_with_risk_separate(ledger):
    from SP_ShortTerm import decision_for_setup

    setup = SimpleNamespace(
        ticker="ABC", signal="BUY", is_funded=True, shares=4,
        expected_hold_days=10, price_as_of=last_completed_session().isoformat(),
        overall_score=74.0, technical_score=70.0, fundamental_score=65.0,
        momentum_score=80.0, quality_score=60.0, analyst_score=55.0,
        pattern_score=75.0, volatility_20d=30.0, beta=1.2,
        max_drawdown=-15.0, current_price=100.0, execution_entry=99.0,
        stop_loss=95.0, primary_target=110.0, risk_reward_ratio=2.5,
        allocation_usd=396.0, price_source="yfinance", info_missing=False,
    )
    decision = decision_for_setup(setup)
    assert decision.label == "BUY" and decision.presentation.qualifier
    assert "volatility_20d" not in decision.factor_scores
    assert decision.risk_metrics["volatility_20d"] == 30.0
    assert record_decisions([decision], ledger=ledger) == 1
    assert record_decisions([decision], ledger=ledger) == 1
    rows = ledger.rows()
    assert len(rows) == 1
    assert rows[0]["horizon_days"] == 10
    assert rows[0]["label"] == "BUY"

    setup.is_funded = False
    setup.shares = 0
    assert decision_for_setup(setup).action.value == "WATCHLIST"


def test_scanner_final_decision_records_matching_benchmark_and_freshness(ledger):
    from SP_ELITE_PRO import StockAnalysis, decision_for_scan_result

    rec = StockAnalysis(ticker="RELIANCE.NS", company="Reliance",
                        sector="Energy", industry="Energy")
    rec.final_action = "BUY"
    rec.allocation = 1000.0
    rec.shares = 5
    rec.market_timestamp = last_completed_session().isoformat()
    decision = decision_for_scan_result(rec)
    assert decision.action.value == "BUY"
    assert decision.label == "BUY" and decision.presentation.qualifier
    assert record_decisions([decision], ledger=ledger) == 1
    assert ledger.rows()[0]["benchmark"] == "^NSEI"

    rec.market_timestamp = (last_completed_session() - timedelta(days=14)).isoformat()
    assert record_decisions([decision_for_scan_result(rec)], ledger=ledger) == 0
    assert len(ledger.rows()) == 1
