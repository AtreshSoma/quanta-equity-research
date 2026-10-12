"""A04: scan accounting reconciles to the universe.

Every scanned name has exactly one terminal outcome (could not be analysed,
set aside by a per-name rule, or reached the book), warnings are counted on
their own, and the book stages reconcile the analysed names to the funded
count. The scan is classified on funded names, and funded and watch rows are
capped independently.

The engine runs for real on synthetic providers: no network, no account data.
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import corporate_events as ce  # noqa: E402
from engine import thresholds as T  # noqa: E402

_IDX = pd.bdate_range(end="2026-10-02", periods=520)


def _frame(i, scale=1.0, volume=2_000_000.0, rows=520):
    x = np.arange(520)
    closes = 60 * scale * np.exp(0.0012 * x + 0.025 * np.sin(x * 0.7 + i))
    opens = np.r_[closes[0], closes[:-1]] * (1 + 0.001 * np.sin(x))
    f = pd.DataFrame(dict(Open=opens, High=np.maximum(opens, closes) * 1.009,
                          Low=np.minimum(opens, closes) * 0.991, Close=closes,
                          Volume=np.full(520, volume)), index=_IDX)
    return f.tail(rows)


# Synthetic fundamentals strong enough for a name to reach the book.
_GOOD = dict(trailingPE=18, forwardPE=15, profitMargins=.25, returnOnEquity=.3, revenueGrowth=.2,
             earningsGrowth=.25, debtToEquity=30, currentRatio=2.0, pegRatio=1.1, priceToBook=4,
             freeCashflow=5e9, operatingMargins=.3, grossMargins=.6, returnOnAssets=.12, beta=1.0)

# name -> (frame kwargs, info overrides, calendar present, raises)
_NAMES = {
    "SYNA": ({}, dict(_GOOD), True, False),
    "SYNB": ({"scale": 1.3}, dict(_GOOD), True, False),
    "SYNC": ({}, dict(_GOOD), False, False),            # calendar missing: a warning
    "SYND": ({"rows": 100}, {}, True, False),           # short history: could not be analysed
    "SYNE": ({"volume": 1000.0}, {}, True, False),      # thin volume: per-name rule
    "SYNF": ({}, {"marketCap": 1e8}, True, False),      # small cap: per-name rule
    "SYNG": ({}, None, True, False),                    # no company info: data gap
    "SYNH": ({}, {}, True, True),                       # provider error: could not be analysed
}


def _install(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.key = symbol if symbol in _NAMES else "SYNA"

        def history(self, *a, **kw):
            fk, _, _, raises = _NAMES[self.key]
            if raises:
                raise ConnectionError("synthetic provider error")
            return _frame(list(_NAMES).index(self.key), **fk).copy()

        @property
        def info(self):
            _, over, _, _ = _NAMES[self.key]
            if over is None:
                return {}
            close = float(_frame(list(_NAMES).index(self.key))["Close"].iloc[-1])
            base = dict(symbol=self.key, longName=f"Synthetic {self.key}", shortName=self.key,
                        sector="Technology", industry="Synthetic", quoteType="EQUITY",
                        marketCap=50e9, currentPrice=close, regularMarketPrice=close,
                        averageVolume=2_000_000, targetMeanPrice=close * 1.4)
            base.update(over)
            return base

        @property
        def fast_info(self):
            return SimpleNamespace(market_cap=50e9)

        @property
        def calendar(self):
            return {"Earnings Date": [pd.Timestamp("2026-12-01")]} if _NAMES[self.key][2] else {}

        insider_transactions = property(lambda self: pd.DataFrame())
        insider_purchases = property(lambda self: pd.DataFrame())
        recommendations = property(lambda self: pd.DataFrame())
        news = property(lambda self: [])

        def get_earnings_dates(self, *a, **kw):
            return pd.DataFrame()

        def __getattr__(self, key):
            raise AttributeError(key)

    monkeypatch.setattr(sp.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(sp.yf, "download", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no network")))
    monkeypatch.setattr(sp.MarketRegimeDetector, "detect_regime", staticmethod(
        lambda *a, **kw: sp.MarketRegime(regime_type="BULL", vix_level=15.0,
                                         recommended_exposure=1.0, volatility_regime="LOW")))
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "_fetch_catalyst_boost", lambda *a, **kw: (0.0, []))
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "_fetch_insider_signal", lambda *a, **kw: {})
    monkeypatch.setattr(sp, "compute_earnings_revision_signal", lambda *a, **kw: {})
    monkeypatch.setattr(sp.TradingConfig, "YF_MAX_WORKERS", 2)
    monkeypatch.setattr(ce, "check_many", lambda symbols, **kw: {s: ce.EventCheck("clear") for s in symbols})


def _scan(monkeypatch, verdicts):
    from test_elite_pro_review2_fixes import _install_fake_deep
    _install(monkeypatch)
    _install_fake_deep(monkeypatch, verdicts)
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type="custom", custom_tickers=list(_NAMES))
    eng.get_top_recommendations(max_positions=5)
    return eng


@pytest.fixture
def scanned(monkeypatch):
    return _scan(monkeypatch, {})


def test_every_scanned_name_has_one_terminal_outcome(scanned):
    acc = scanned.scan_accounting()
    assert acc["universe"] == len(_NAMES)
    total = acc["could_not_analyse"]["count"] + acc["rule_rejected"]["count"] + acc["reached_book"] \
        + acc["not_analysed"]
    assert total == acc["universe"], acc
    assert sum(acc["could_not_analyse"]["by_reason"].values()) == acc["could_not_analyse"]["count"]
    assert sum(acc["rule_rejected"]["by_reason"].values()) == acc["rule_rejected"]["count"]
    # short history and a provider error are failures to analyse, not policy rejections
    assert acc["could_not_analyse"]["by_reason"].get("insufficient_data", 0) >= 1
    assert acc["could_not_analyse"]["count"] >= 3
    assert "insufficient_data" not in acc["rule_rejected"]["by_reason"]


def test_a_missing_calendar_is_a_warning_not_a_rejection(scanned):
    acc = scanned.scan_accounting()
    assert acc["warnings"].get("earnings_calendar_missing", 0) >= 1
    assert "earnings_calendar_missing" not in acc["rule_rejected"]["by_reason"]
    assert "earnings_calendar_missing" not in acc["could_not_analyse"]["by_reason"]


def _assert_chain(acc):
    stages = acc["book_stages"]
    assert [s["key"] for s in stages][:2] == ["actionable", "event"]
    entered = acc["reached_book"]
    dropped = 0
    for s in stages:
        if s["entered"] is None:
            assert not s["evaluated"] and s["passed"] is None
            continue
        assert s["entered"] == entered, s
        assert 0 <= s["passed"] <= s["entered"]
        dropped += s["entered"] - s["passed"]
        entered = s["passed"]
    assert dropped + acc["funded"] == acc["reached_book"]
    return {s["key"]: s for s in stages}


def test_research_withholding_is_its_own_stage(monkeypatch):
    eng = _scan(monkeypatch, {"SYNA": "WATCHLIST"})
    acc = eng.scan_accounting()
    stages = _assert_chain(acc)
    assert acc["reached_book"] >= 2
    final = stages["final_review"]
    assert final["entered"] - final["passed"] == 1, "SYNA withheld by the Research check"


def test_book_stages_reconcile_reached_book_to_funded(scanned):
    acc = scanned.scan_accounting()
    assert acc["reached_book"] >= 1 and acc["funded"] >= 1, "the synthetic book is not trivial"
    _assert_chain(acc)


def test_unevaluated_stages_carry_their_configured_threshold(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system
    sysm = _bare_system()
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear") for s in symbols}
    sysm.scan_results = []
    sysm.get_top_recommendations(max_positions=5)
    stages = {s["key"]: s for s in sysm.scan_accounting()["book_stages"]}
    score = stages["score"]
    assert score["evaluated"] is False
    assert score["threshold"]["configured"] == T.SCORE.ACCUMULATE
    assert stages["risk_reward"]["threshold"]["configured"] == T.RR.SCANNER_MIN


def test_the_sizing_count_is_the_first_pass_not_overwritten_by_backfill(monkeypatch):
    """A04: gate 7 counts the first selection pass; backfill is its own count."""
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {"AAA": "WATCHLIST"})
    sysm = _bare_system()
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear") for s in symbols}
    sysm.scan_results = [_pick("AAA", "Tech", 8.5, seed=1), _pick("BBB", "Health", 8.0, seed=2),
                         _pick("CCC", "Energy", 7.8, seed=3)]
    out = sysm.get_top_recommendations(max_positions=2)
    gs = sysm._gate_stats
    assert gs["gate7_sector_cap"] == 2, "first pass sized AAA and BBB"
    assert gs["backfilled"] == 1, "CCC backfilled for AAA's withdrawn capital"
    funded = [r for r in out if r.allocation > 0]
    assert {r.ticker for r in funded} == {"BBB", "CCC"}
    assert gs["funded"] == 2


# ── the worker: classification and independent row caps ────────────────────

def _fake_engine_module(recs, universe=8):
    from test_elite_pro_review2_fixes import _bare_system

    class _Eng:
        def __init__(self, capital, market):
            self.capital = capital
            self.market_regime = sp.MarketRegime(regime_type="BULL", vix_level=15.0,
                                                  recommended_exposure=1.0, volatility_regime="LOW")
            self._last_universe_size = universe
            self._gate_stats = {}
            self._filter_stats = {}
            self._deep_confirm_records = []
            self._deep_confirm_enabled = True
            self.chase_alerts = []
            self._universe_selection = {"method": "even_spread", "cause": "liquidity download exceeded its 90s budget",
                                        "seconds": 90.2, "budget_seconds": 90.0, "candidates": 503, "selected": universe}

        def scan_market(self, **kw):
            return list(recs)

        def get_top_recommendations(self, **kw):
            return list(recs)

        def get_error_summary(self):
            return {"records": [], "by_type": {}, "universe_size": universe,
                    "analyzed_success_count": universe, "analysis_failed_count": 0,
                    "exception_rate": 0.0}

        def explain_empty_scan(self):
            return "explained"

        def scan_accounting(self):
            return {"universe": universe, "could_not_analyse": {"count": 0, "by_reason": {}},
                    "rule_rejected": {"count": universe - len(recs), "by_reason": {"x": universe - len(recs)}},
                    "reached_book": len(recs), "not_analysed": 0, "warnings": {},
                    "book_stages": [], "funded": sum(1 for r in recs if r.allocation > 0)}

    mod = types.ModuleType("SP_ELITE_PRO")
    mod.QuantaEliteTradingSystem = _Eng
    mod.ENHANCEMENTS_AVAILABLE = False
    mod.apply_allocation = sp.apply_allocation
    mod.TradingConfig = sp.TradingConfig
    return mod


def _run_worker(monkeypatch, recs, max_positions=2):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", _fake_engine_module(recs))
    job_id = "a04-test"
    webapp._jobs[job_id] = {"type": "stock-picks", "status": "running", "progress": 0,
                            "started_at": time.time(), "worker_active": True}
    params = dict(capital=100_000.0, market="US", scan_type="custom", custom_tickers=None,
                  risk_profile="balanced", max_positions=max_positions, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job_id, params)
        job = webapp._jobs[job_id]
        assert job["status"] == "complete", job.get("error")
        return job["result"]
    finally:
        webapp._jobs.pop(job_id, None)


def _watch(ticker, seed):
    from test_elite_pro_review2_fixes import _pick
    p = _pick(ticker, "Tech", 7.0, seed=seed)
    sp.apply_allocation(p, 0.0, 100_000.0)
    p.final_action = "WATCHLIST"
    return p


def test_a_watch_only_scan_is_not_a_success(monkeypatch):
    res = _run_worker(monkeypatch, [_watch("W1", 1), _watch("W2", 2)])
    assert res["scan_status"] == "no_picks"
    assert res["funded_count"] == 0 and res["watch_count"] == 2
    assert res["empty_scan_explanation"] == "explained"
    assert len(res["recommendations"]) == 2, "watch rows are still published"


def test_funded_and_watch_rows_are_capped_independently(monkeypatch):
    from test_elite_pro_review2_fixes import _pick
    funded = [_pick(f"F{i}", f"S{i}", 8.0 - i * 0.1, seed=i) for i in range(3)]
    watch = [_watch(f"W{i}", 10 + i) for i in range(T.BOOK.MAX_WATCH_ROWS + 2)]
    res = _run_worker(monkeypatch, funded + watch, max_positions=2)
    rows = res["recommendations"]
    assert sum(1 for r in rows if r["is_funded"]) == 2
    assert sum(1 for r in rows if not r["is_funded"]) == T.BOOK.MAX_WATCH_ROWS
    assert res["scan_status"] == "success"
    acc = res["accounting"]
    assert acc["watch_total"] == T.BOOK.MAX_WATCH_ROWS + 2
    assert acc["watch_published"] == T.BOOK.MAX_WATCH_ROWS, "the cap is reported, not hidden"
    assert acc["funded"] == 2 and acc["funded_over_cap"] == 1


def test_the_cost_gate_fails_closed_when_no_name_covers_its_costs(monkeypatch):
    """Owner decision 2026-10-04: if no name's expected return covers
    BOOK.COST_VIABILITY_MULTIPLE x its round-trip cost, none is kept."""
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear") for s in symbols}
    picks = [_pick("AAA", "Tech", 8.5, seed=1), _pick("BBB", "Health", 8.0, seed=2)]
    for p in picks:     # 15% expected return vs 2 x 10% round trip
        p.transaction_cost_bps = 1000.0
    sysm.scan_results = picks
    assert sysm.get_top_recommendations(max_positions=5) == []
    cost = {s["key"]: s for s in sysm.scan_accounting()["book_stages"]}["cost"]
    assert cost["entered"] == 2 and cost["passed"] == 0
    assert cost["threshold"]["value"] == T.BOOK.COST_VIABILITY_MULTIPLE


def test_the_result_publishes_how_the_universe_was_chosen(monkeypatch):
    res = _run_worker(monkeypatch, [_watch("W1", 1)])
    sel = res["universe_selection"]
    assert sel["method"] == "even_spread" and "budget" in sel["cause"] and sel["seconds"] == 90.2
