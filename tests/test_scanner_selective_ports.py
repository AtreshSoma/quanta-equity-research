"""Scanner fixes ported selectively from the redesign branch.

Each test pins one behaviour that was wrong on this branch before the port.
The redesign's research ranking and post-hoc rescoring were NOT ported (the
ranking emitted its own BUY/HOLD verdict; this branch already keeps
cross-sectional scoring in ``ranking_score``), so nothing here exercises them.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pandas as pd
import pytest

import SP_ELITE_PRO as elite


def _bare_system(capital: float = 100_000.0):
    sysm = object.__new__(elite.QuantaEliteTradingSystem)
    sysm.capital = capital
    sysm.currency_symbol = "$"
    sysm.market = "US"
    sysm.drawdown_multiplier = 1.0
    sysm.current_drawdown = 0.0
    sysm.market_regime = elite.MarketRegime(regime_type="NEUTRAL", recommended_exposure=1.0)
    sysm.scan_results = []
    sysm._universe_stats = None
    sysm._filter_stats = {}
    sysm._filter_stats_lock = threading.Lock()
    sysm._zero_alloc_reasons = {}
    sysm._zero_alloc_tickers = []
    return sysm


# --- Drawdown throttle ------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (0.0, 0.0),          # halted: `0.0 or 1.0` used to read this as full size
    (0.5, 0.5),
    (1.0, 1.0),
    (float("nan"), 0.0),  # corrupt account state fails closed
    ("bad", 0.0),
    (3.0, 1.0),
])
def test_drawdown_multiplier_fails_closed(value, expected):
    sysm = _bare_system()
    sysm.drawdown_multiplier = value
    assert sysm._drawdown_size_multiplier() == expected


def test_capital_must_be_finite_and_positive():
    for bad in (0, -5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            elite.QuantaEliteTradingSystem(capital=bad)


# --- Cross-sectional statistics ---------------------------------------------

def test_small_population_publishes_no_percentile():
    assert elite.UniverseStats._percentile_rank(7.0, [5.0, 6.0, 8.0]) is None
    assert elite.StockAnalysis(ticker="X", company="X", sector="S", industry="I").universe_percentile is None


def test_regime_weighted_ranking_without_zscores_on_a_thin_pool():
    """Below ZSCORE_MIN_UNIVERSE the ranking still applies regime weights,
    never touches overall_score (the gate input) and publishes no percentile."""
    a = elite.StockAnalysis(ticker="A", company="A", sector="S", industry="I")
    a.technical_score = a.fundamental_score = a.momentum_score = 6.0
    a.quality_score = a.value_score = 6.0
    a.overall_score = 7.3
    elite.ScoringEngine.normalize_scores(a, elite.UniverseStats(), {"technical": 0.2, "fundamental": 0.2,
                                                                     "momentum": 0.2, "quality": 0.2,
                                                                     "value": 0.2})
    assert a.overall_score == 7.3
    assert a.ranking_score == pytest.approx(6.0)
    assert a.universe_percentile is None


# --- Portfolio construction gates -------------------------------------------

def test_gate_one_respects_a_vetoed_final_action():
    """A scanner BUY that the decision core vetoed must not reach Gate #2."""
    sysm = _bare_system()
    good = elite.StockAnalysis(ticker="OK", company="OK", sector="S", industry="I")
    vetoed = elite.StockAnalysis(ticker="VETO", company="V", sector="S", industry="I")
    for rec in (good, vetoed):
        rec.signal = elite.SignalType.BUY.value
        rec.risk_grade = "Z"   # stops the run right after Gate #2; we only count Gate #1
    good.final_action, vetoed.final_action = "BUY", "WATCHLIST"
    sysm.scan_results = [good, vetoed]
    sysm.get_top_recommendations()
    assert sysm._gate_stats["gate1_actionable"] == 1


def test_thin_pool_keeps_the_sector_cap_unless_opted_in():
    cap, relaxed = elite.QuantaEliteTradingSystem._resolve_sector_cap(0.25, 3, True, False)
    assert (cap, relaxed) == (0.25, False)
    cap, relaxed = elite.QuantaEliteTradingSystem._resolve_sector_cap(0.25, 3, True, True)
    assert (cap, relaxed) == (1.0, True)
    cap, relaxed = elite.QuantaEliteTradingSystem._resolve_sector_cap(0.25, 10, False, False)
    assert (cap, relaxed) == (1.0, False)


# --- Insider rows -----------------------------------------------------------

class _InsiderStock:
    def __init__(self, frame):
        self.insider_transactions = frame


def test_undated_insider_rows_are_not_recent_evidence():
    frame = pd.DataFrame({
        "Text": ["Purchase at price 10.00 per share."] * 3,
        "Shares": [10_000, 10_000, 10_000],
        "Value": [100_000, 100_000, 100_000],
    })
    out = _bare_system()._fetch_insider_signal("X", _InsiderStock(frame), 10.0, market_cap=1e10)
    assert out.get("buys_90d", 0) == 0


def test_future_dated_insider_rows_are_ignored():
    future = datetime.now() + timedelta(days=30)
    frame = pd.DataFrame({
        "Start Date": [future],
        "Text": ["Purchase at price 10.00 per share."],
        "Shares": [10_000], "Value": [100_000],
    })
    out = _bare_system()._fetch_insider_signal("X", _InsiderStock(frame), 10.0, market_cap=1e10)
    assert out.get("buys_90d", 0) == 0


# --- Provider pacing --------------------------------------------------------

def test_pacer_backs_off_multiplicatively_and_recovers_additively():
    pacer = elite.AdaptivePacer(start=1.0, minimum=0.5, maximum=4.0,
                                backoff=2.0, recover=0.25, recover_after=2)
    pacer.note_rate_limit()
    assert pacer.interval == 2.0
    pacer.note_rate_limit(); pacer.note_rate_limit()
    assert pacer.interval == 4.0          # capped
    pacer.note_success()
    assert pacer.interval == 4.0          # needs a streak
    pacer.note_success()
    assert pacer.interval == 3.75


def test_slow_response_is_telemetry_not_a_rate_limit():
    tracker = elite.ProviderRateLimitTracker()
    tracker.note_slow()
    assert tracker.slow_responses == 1
    assert not tracker.in_cooldown()


def test_prefetched_history_is_served_without_a_live_request():
    sysm = _bare_system()
    frame = pd.DataFrame({"Close": [1.0] * 5})
    sysm._bulk_history = {"AAA": frame}
    assert sysm._bulk_history_for("AAA") is not None
    assert sysm._bulk_history_for("BBB") is None


def test_filter_summary_keeps_infrastructure_errors_out_of_the_business_total(caplog):
    sysm = _bare_system()
    sysm._filter_stats = {"score_below_min": 4, "provider_rate_limited": 3,
                          "earnings_calendar_missing": 9}
    with caplog.at_level("INFO", logger="QUANTA"):
        sysm._log_filter_summary()
    assert "(4 filter hits" in caplog.text
    assert "Infrastructure failures" in caplog.text
    assert "9 names sized down (not a rejection)" in caplog.text


def test_network_session_accepts_a_caller_timeout(monkeypatch):
    """Passing timeout= used to raise 'multiple values for keyword argument'."""
    seen = {}

    class _Session:
        def get(self, url, **kwargs):
            seen.update(kwargs)

            class _R:
                status_code = 200
            return _R()

    monkeypatch.setattr(elite.NetworkSession, "get_session", classmethod(lambda cls: _Session()))
    elite.NetworkSession.rate_limited_get("https://example.invalid", timeout=3.0)
    assert seen["timeout"] == 3.0
