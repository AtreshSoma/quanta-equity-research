"""Picks redesign: one ranked table of every name that reached the book.

Each row carries the name's one Decision, its price, entry, target, upside,
net reward-to-risk and score, and either its position (funded) or the one
reason it is not funded: a watch reason, a pending deal, a selection limit, or
the first book rule it missed. Real engine book and real worker on synthetic
rows; no market data.
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import corporate_events as ce  # noqa: E402
from engine import thresholds as T  # noqa: E402

from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick  # noqa: E402

CAP = 2_500.0
CIK = {"AMZN": 1, "CSCO": 2, "GOOG": 3, "GOOGL": 3, "MSFT": 4, "LOWR": 5}


def _row(t, score, price, seed, stop_pct=0.955):
    p = _pick(t, "S" + t, score, seed=seed, capital=CAP)
    p.company = f"{t} Inc."
    p.entry_price, p.stop_loss, p.price_target = price, round(price * stop_pct, 2), round(price * 1.16, 2)
    p.risk_reward_ratio = round((p.price_target - price) / (price - p.stop_loss), 2)
    p.technical_indicators.current_price = price
    p.market_timestamp = "2026-10-02T00:00:00"
    return p


def _checker(symbols, **_):
    out = {}
    for s in symbols:
        if s == "AVDX":
            out[s] = ce.EventCheck("flagged", ce.PENDING_ACQUISITION, "Pending cash acquisition at $102.50.")
        else:
            out[s] = ce.EventCheck("clear", cik=CIK.get(s, 99))
    return out


def _run(monkeypatch, verdicts=None):
    _install_fake_deep(monkeypatch, verdicts if verdicts is not None else {"CSCO": "WATCHLIST"})
    eng = _bare_system(capital=CAP, regime="BULL")
    eng.scan_results = [_row("AMZN", 8.1, 251.52, 1), _row("CSCO", 8.0, 70.2, 2), _row("GOOG", 7.9, 245.1, 3),
                        _row("GOOGL", 7.8, 246.0, 4), _row("MSFT", 7.7, 517.53, 5), _row("AVDX", 7.6, 99.4, 6),
                        _row("LOWR", 7.5, 50.0, 7, stop_pct=0.80)]          # R:R 0.8: below the book's minimum
    eng._event_checker = _checker
    eng._last_universe_size = 7
    eng.scan_market = lambda **kw: list(eng.scan_results)
    eng.get_error_summary = lambda: {"records": [], "by_type": {}, "universe_size": 7, "analyzed_success_count": 7,
                                     "analysis_failed_count": 0, "exception_rate": 0.0}
    eng.explain_empty_scan = lambda: ""
    mod = types.ModuleType("SP_ELITE_PRO")
    mod.QuantaEliteTradingSystem = lambda capital, market: eng
    mod.ENHANCEMENTS_AVAILABLE = False
    mod.apply_allocation = sp.apply_allocation
    mod.TradingConfig = sp.TradingConfig
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    job = "picks-table"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "started_at": time.time(),
                         "worker_active": True}
    try:
        webapp._run_stock_picks(job, dict(capital=CAP, market="US", scan_type="quick", custom_tickers=None,
                                          risk_profile="balanced", max_positions=5, diversify=True, safe_mode=False))
        assert webapp._jobs[job]["status"] == "complete", webapp._jobs[job].get("error")
        return webapp._jobs[job]["result"]
    finally:
        webapp._jobs.pop(job, None)


@pytest.fixture
def result(monkeypatch):
    return _run(monkeypatch)


def test_every_name_that_reached_the_book_is_a_call_or_listed_in_method(result):
    """[Calls] Rows are QUANTA's calls (Buy, Accumulate, Watchlist), ordered by
    call then score; a book-rule miss is screened out, a pending deal is
    excluded for safety, and a second share class rides on its twin's row."""
    rows = result["stock_picks"]
    tickers = [r["ticker"] for r in rows]
    assert sorted(tickers) == sorted(["AMZN", "CSCO", "GOOG", "MSFT"])
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    order = ["buy", "accumulate", "watchlist"]
    keys = [(order.index(r["call"]), -r["score"]) for r in rows]
    assert keys == sorted(keys), "by call, then score"
    assert [s["ticker"] for s in result["screened_out"]] == ["LOWR"]
    ex = result["excluded_for_safety"]
    assert [e["ticker"] for e in ex] == ["AVDX"] and "acquisition" in ex[0]["reason"].lower()
    assert ex[0]["source"]
    goog = next(r for r in rows if r["ticker"] == "GOOG")
    assert [o["ticker"] for o in goog.get("other_classes", [])] == ["GOOGL"]
    actionable = {st["key"]: st for st in result["accounting"]["book_stages"]}["actionable"]
    assert len(rows) + len(result["screened_out"]) + len(ex) + 1 == actionable["passed"], "every entrant accounted for once"


def test_a_class_held_back_by_research_holds_back_its_twin_on_the_same_row(monkeypatch):
    """D2: share classes fail closed. Research puts GOOG on Watchlist, so GOOGL
    (same CIK, from the event check's data) gets no money either, and GOOG's
    row names it with the reason."""
    result = _run(monkeypatch, {"CSCO": "WATCHLIST", "GOOG": "WATCHLIST"})
    rows = result["stock_picks"]
    assert "GOOGL" not in [r["ticker"] for r in rows]
    goog = next(r for r in rows if r["ticker"] == "GOOG")
    assert goog["call"] == "watchlist"
    (twin,) = goog["other_classes"]
    assert twin["ticker"] == "GOOGL" and twin["held_back"] is True
    assert "Research check held GOOG back" in twin["reason"]
    assert not any(float(r.get("allocation") or 0) > 0 for r in rows if r["ticker"] in ("GOOG", "GOOGL"))


def test_each_call_is_separate_from_capital_fit(result):
    by = {r["ticker"]: r for r in result["stock_picks"]}
    assert by["AMZN"]["call"] in ("buy", "accumulate")
    assert by["AMZN"]["capital"] == {"kind": "sized", "dollars": by["AMZN"]["allocation"], "shares": by["AMZN"]["shares"]}
    assert by["CSCO"]["call"] == "watchlist" and by["CSCO"]["reason"]
    assert by["CSCO"]["capital"]["kind"] == "none"
    msft = by["MSFT"]
    assert msft["call"] in ("buy", "accumulate"), "capital fit never changes the call"
    assert msft["capital"]["kind"] == "needs" and msft["capital"]["needed"] > CAP
    assert msft["capital"]["text"] == f"Needs about ${msft['capital_needed']:,.0f} for one share within the position cap"
    low = result["screened_out"][0]
    assert low["gate"]["gate"] == "risk_reward" and low["gate"]["needed"] == pytest.approx(T.RR.SCANNER_MIN)


def test_rows_carry_the_table_columns(result):
    for r in result["stock_picks"]:
        for key in ("ticker", "company", "sector", "price", "signal", "entry", "target", "upside_pct", "stop",
                    "risk_reward", "score", "risk_grade", "conviction", "scores", "allocation", "shares", "status"):
            assert key in r, (r["ticker"], key)
        if r["price"] and r["target"]:
            assert r["upside_pct"] == pytest.approx((r["target"] / r["price"] - 1) * 100, abs=0.01)
    amzn = next(r for r in result["stock_picks"] if r["ticker"] == "AMZN")
    assert set(amzn["scores"]) == {"technical", "fundamental", "momentum", "quality", "value"}


def test_the_small_cap_scan_uses_its_own_market_cap_floor():
    assert sp.min_market_cap_for("small_cap") == T.UNIVERSE.SCAN_MIN_MARKET_CAP["small_cap"]
    assert sp.min_market_cap_for("small_cap") < sp.TradingConfig.MIN_MARKET_CAP
    assert sp.min_market_cap_for("quick") == sp.TradingConfig.MIN_MARKET_CAP


def test_every_row_says_why_it_was_picked(result):
    """[Why picked] Each row carries the measured reasons, both sides, from the
    scanner result; a missing explanation never drops a row."""
    for r in result["stock_picks"]:
        w = r["why_picked"]
        assert set(w) == {"summary", "for", "against", "news", "notes"}, r["ticker"]
        for item in w["for"] + w["against"]:
            assert item["text"] and item["label"] and item["kind"], (r["ticker"], item)
        assert r["is_fund"] is False


def test_every_row_carries_the_analysis_inputs(result):
    """[Analysis] Technicals, insider flow and the valuation block on every row;
    a field that was not measured is None, never 0."""
    for r in result["stock_picks"]:
        assert set(r["tech"]) >= {"rsi", "sma_50", "sma_200", "volume_ratio", "atr", "momentum_1m", "high_52w", "low_52w"}
        assert all(v is None or v != 0 for v in r["tech"].values()), r["ticker"]
        assert set(r["insider"]) == {"sentiment", "buys", "sells", "net_value", "note"}
        assert set(r["analyst"]) == {"rating", "count", "rec_mean", "target"}
        assert r["valuation"] is None or set(r["valuation"]) == {"price", "marks", "low", "high", "sentence", "missing"}
