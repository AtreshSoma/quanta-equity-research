"""Audit B-89: Picks knows what the account already holds.

Before, the Picks scan never saw the user's holdings: it could suggest a full
new position in a name already held at or above the per-name cap. Now, when
My Portfolio has loaded the broker positions (read-only, in memory, no broker
call from the scan):

* every row in a held name says "already held", with the value and its share
  of the scan capital;
* a sized row counts the existing position against the same per-name cap the
  book uses (TradingConfig.effective_max_position_pct): it is sized only for
  the room left, and not at all once the holding fills the cap or its value is
  unknown;
* with no broker positions loaded the result says holdings were not checked
  (sizing is unchanged); India rows are not matched against US broker holdings.

The engine's selection and funding rules are unchanged. Synthetic data only.
"""
from __future__ import annotations

import math

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402
import SP_ELITE_PRO as sp  # noqa: E402
from portfolio.models import AccountSummary, Portfolio, Position  # noqa: E402
from portfolio.service import PortfolioService  # noqa: E402

CAPITAL = 10_000.0


def _rows():
    return [
        {"ticker": "AAA", "price": 50.0, "status": "funded", "allocation": 1000.0, "shares": 20,
         "capital": {"kind": "sized", "dollars": 1000.0, "shares": 20}},
        {"ticker": "BRK-B", "price": 100.0, "status": "funded", "allocation": 900.0, "shares": 9,
         "capital": {"kind": "sized", "dollars": 900.0, "shares": 9}},
        {"ticker": "CCC", "price": 10.0, "status": "funded", "allocation": 500.0, "shares": 50,
         "capital": {"kind": "sized", "dollars": 500.0, "shares": 50}},
        {"ticker": "DDD", "price": 20.0, "status": "watch", "allocation": 0.0, "shares": 0,
         "capital": {"kind": "none", "text": "No new money on a Watchlist call"}},
        {"ticker": "EEE", "price": 30.0, "status": "funded", "allocation": 600.0, "shares": 20,
         "capital": {"kind": "sized", "dollars": 600.0, "shares": 20}},
    ]


def _cap():
    return CAPITAL * sp.TradingConfig.effective_max_position_pct(CAPITAL)


def _holdings(cap):
    return {"retrieved_at": "2026-10-08T14:00:00+00:00", "account_value": 50_000.0, "positions": {
        "AAA": {"quantity": 10, "market_value": cap - 300.0},      # 300 of room left: 6 shares at 50
        "BRK-B": {"quantity": 50, "market_value": cap + 1.0},      # already past the cap
        "DDD": {"quantity": 5, "market_value": 100.0},             # held, on the Watchlist
        "EEE": {"quantity": 3, "market_value": None},              # held, price unknown
    }}


def _apply(rows, holdings, market="US"):
    result = {"stock_picks": rows}
    webapp._apply_current_holdings(result, CAPITAL, market, holdings=holdings, config=sp.TradingConfig)
    return result, {r["ticker"]: r for r in rows}


def test_a_held_name_is_sized_only_for_the_room_left_under_the_cap():
    cap = _cap()
    result, by = _apply(_rows(), _holdings(cap))
    a = by["AAA"]
    assert a["shares"] == math.floor(300.0 / 50.0) and a["allocation"] == pytest.approx(a["shares"] * 50.0)
    assert a["capital"]["kind"] == "sized" and a["capital"]["reduced_for_holding"] is True
    assert a["held"]["market_value"] == pytest.approx(cap - 300.0)
    assert a["held"]["pct_of_capital"] == pytest.approx(round((cap - 300.0) / CAPITAL * 100, 1))


def test_a_holding_at_the_cap_gets_no_new_money_and_says_why():
    result, by = _apply(_rows(), _holdings(_cap()))
    b = by["BRK-B"]
    assert b["allocation"] == 0 and b["shares"] == 0
    assert b["capital"]["kind"] == "held" and "already held" in b["capital"]["text"].lower()
    assert "cap" in b["capital"]["text"]


def test_a_holding_with_unknown_value_is_not_sized_on_top_of():
    result, by = _apply(_rows(), _holdings(_cap()))
    e = by["EEE"]
    assert e["allocation"] == 0 and e["capital"]["kind"] == "held" and "unknown" in e["capital"]["text"]


def test_unheld_names_and_watchlist_rows_keep_their_sizing():
    rows = _rows()
    before = {r["ticker"]: (r["allocation"], r["shares"], dict(r["capital"])) for r in rows}
    result, by = _apply(rows, _holdings(_cap()))
    assert (by["CCC"]["allocation"], by["CCC"]["shares"], by["CCC"]["capital"]) == before["CCC"]
    assert "held" not in by["CCC"]
    assert by["DDD"]["capital"] == before["DDD"][2] and by["DDD"]["held"]["quantity"] == 5
    assert result["holdings_check"]["state"] == "checked" and result["holdings_check"]["held"] == 4


def test_without_broker_positions_holdings_are_not_checked_and_sizing_is_unchanged():
    rows = _rows()
    before = [(r["allocation"], r["shares"]) for r in rows]
    result, _ = _apply(rows, None)
    assert [(r["allocation"], r["shares"]) for r in rows] == before
    assert result["holdings_check"]["state"] == "not_checked"
    assert "My Portfolio" in result["holdings_check"]["text"]


def test_india_rows_are_not_matched_against_us_broker_holdings():
    rows = [dict(r, ticker=r["ticker"] + ".NS") for r in _rows()]
    result, by = _apply(rows, {"retrieved_at": "x", "account_value": 1.0,
                               "positions": {"AAA.NS": {"quantity": 1, "market_value": 1e9}}}, market="IN")
    assert result["holdings_check"]["state"] == "not_applicable"
    assert all("held" not in r for r in rows)


def test_the_cap_comes_from_the_engine_config(monkeypatch):
    monkeypatch.setattr(sp.TradingConfig, "effective_max_position_pct", staticmethod(lambda capital: 0.5))
    rows = _rows()
    _apply(rows, {"retrieved_at": "x", "account_value": 1.0,
                  "positions": {"CCC": {"quantity": 1, "market_value": CAPITAL * 0.5 - 100.0}}})
    ccc = next(r for r in rows if r["ticker"] == "CCC")
    assert ccc["shares"] == 10, "room = 50% cap - holding = 100, 10 shares at 10"


def test_the_portfolio_service_offers_a_read_only_holdings_snapshot():
    pf = Portfolio("robinhood", AccountSummary("opaque", "Individual", portfolio_value=20_000.0, cash=0.0),
                   [Position("BRK.B", 2, last_price=500.0), Position("XYZ", 1, last_price=None)],
                   "2026-10-08T14:00:00+00:00")
    svc = PortfolioService.__new__(PortfolioService)
    import threading
    svc._lock = threading.Lock()
    svc._portfolio = None
    assert svc.holdings_snapshot() is None
    svc._portfolio = pf
    snap = svc.holdings_snapshot()
    assert snap["retrieved_at"] == pf.retrieved_at and snap["account_value"] == 20_000.0
    assert snap["positions"]["BRK-B"] == {"quantity": 2, "market_value": 1000.0}
    assert snap["positions"]["XYZ"]["market_value"] is None


def test_the_scan_reads_holdings_through_the_portfolio_routes(monkeypatch):
    import portfolio_routes as pr
    monkeypatch.setattr(pr, "_service", None)
    assert pr.current_holdings() is None

    class Svc:
        def holdings_snapshot(self):
            raise RuntimeError("broker went away")
    monkeypatch.setattr(pr, "_service", Svc())
    assert pr.current_holdings() is None, "a failing read is 'not checked', never an error in the scan"


def test_a_full_scan_marks_and_resizes_a_held_pick(monkeypatch):
    from test_stock_picks_table import CAP, _run
    cap = CAP * sp.TradingConfig.effective_max_position_pct(CAP)
    monkeypatch.setattr(webapp, "_current_holdings", lambda: {
        "retrieved_at": "2026-10-08T14:00:00+00:00", "account_value": 9_000.0,
        "positions": {"AMZN": {"quantity": 3, "market_value": cap}}})
    result = _run(monkeypatch)
    amzn = next(r for r in result["stock_picks"] if r["ticker"] == "AMZN")
    assert amzn["held"]["quantity"] == 3
    assert amzn["capital"]["kind"] == "held" and amzn["allocation"] == 0
    assert result["holdings_check"]["state"] == "checked"
