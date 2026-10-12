"""Activity records state what they cover, and never present a capped history as complete.

The "Realized by stock shows No closing trades while closing trades exist" report:
a server started before the span fix asked Robinhood for its default one-week trade
history, got an empty list, and the page took that list as the whole record. The
records now carry their coverage (dates, counts, the window Robinhood actually used,
and whether a read limit cut them), so the page can say what is missing.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths()

from brokers.robinhood import RobinhoodProvider  # noqa: E402
from portfolio.activity_config import ACTIVITY  # noqa: E402
from portfolio.insights import Insights, MarketSources  # noqa: E402
from portfolio import history as hs  # noqa: E402


def test_crypto_fills_use_broker_currency_and_execution_time_not_order_update():
    orders = [{"currency_code": "COIN", "side": "buy", "state": "canceled",
               "updated_at": "2026-10-07T15:00:00Z", "price": "999", "quantity": "10",
               "executions": [{"quantity": "0.25", "effective_price": "20", "price": "19",
                               "timestamp": "2026-09-01T15:00:00Z"}]}]
    norm = hs.normalize_crypto_orders(orders)
    assert norm["rejected"] == 0
    f, = norm["fills"]
    assert (f.symbol, f.instrument_type, f.quantity, f.price, f.at) == (
        "COIN", "crypto", 0.25, 20, "2026-09-01T15:00:00Z")


def test_crypto_history_uses_linked_rhs_and_paginates_separately_from_equity():
    rh, calls = _provider([{"data": {"results": [{"currency_code": "COIN"}], "next": "page2"}},
                           {"data": {"results": [], "next": ""}}])
    rh._account_number = lambda h: "broker"
    rh._rhs_accounts = {"broker": "linked"}
    out = rh.load_crypto_history("opaque")
    assert out["orders"] == [{"currency_code": "COIN"}]
    assert all(c["account"] == "linked" for c in calls)
    assert calls[1]["cursor"] == "page2"
    assert out["coverage"]["truncated"] is False


def test_crypto_activity_records_are_separate_cached_and_fail_visibly():
    svc = _Svc({"orders": [_order(0, "03-02")], "trades": [_trade(0, "03-03")]})
    calls = []

    def load(handle):
        calls.append(handle)
        return {"orders": [{"currency_code": "COIN", "side": "buy", "executions": [
            {"quantity": "1", "effective_price": "20", "timestamp": "2026-03-01T15:00:00Z"}]}],
                "coverage": {"pages": 1, "truncated": False, "span": None}}

    svc.provider.load_crypto_history = load
    ins = Insights(svc, MarketSources())
    r = ins.records()
    assert r["crypto"]["available"] and r["crypto"]["coverage"]["complete"]
    assert r["crypto"]["fills"][0]["symbol"] == "COIN"
    assert all(f["symbol"] != "COIN" for f in r["fills"])
    ins.records()
    assert len(calls) == 1
    svc._account = "another-account"
    ins.records()
    assert len(calls) == 2

    def failed(handle):
        raise RuntimeError("unavailable")

    svc.provider.load_crypto_history = failed
    r = Insights(svc, MarketSources()).records()
    assert not r["crypto"]["available"]
    assert "could not be read" in r["crypto"]["reason"]


def _provider(pages):
    rh = object.__new__(RobinhoodProvider)
    calls = []

    def _call(role, **kw):
        calls.append(kw)
        return pages[min(len(calls) - 1, len(pages) - 1)]
    rh._call = _call
    return rh, calls


def test_crypto_list_orders_are_hydrated_before_normalization():
    order = {"id": "order-one", "currency_code": "XYZ", "side": "buy",
             "state": "filled", "cumulative_quantity": "2"}
    execution = {"id": "fill-one", "quantity": "2", "price": "10",
                 "effective_price": "10.01", "notional": "20.02",
                 "timestamp": "2026-10-01T00:30:00Z"}
    rh, calls = _provider([{"data": {"results": [order]}},
                           {"data": {"results": [dict(order, executions=[execution])]}}])
    rh._account_number = lambda h: "broker"
    rh._rhs_accounts = {"broker": "linked"}
    out = rh.load_crypto_history("opaque")
    assert calls[1]["extra"] == {"order_id": "order-one"}
    assert out["orders"][0]["executions"] == [execution]
    assert out["details"] == {"requested": 1, "succeeded": 1, "failed": 0}
    norm = hs.normalize_crypto_orders(out["orders"])
    assert len(norm["fills"]) == 1 and norm["rejected"] == 0
    assert norm["fills"][0].execution_id == "fill-one"


def test_crypto_bad_dates_and_missing_detail_report_structural_reasons():
    norm = hs.normalize_crypto_orders([
        {"currency_code": "XYZ", "side": "buy", "cumulative_quantity": "2"},
        {"currency_code": "XYZ", "side": "buy", "executions": [
            {"quantity": "2", "effective_price": "10", "timestamp": "not-a-date"}]}])
    assert not norm["fills"] and norm["rejected"] == 2
    assert norm["rejection_reasons"] == {"missing_executions": 1, "invalid_execution_timestamp": 1}


def test_mixed_closing_history_does_not_default_unknown_assets_to_equity():
    trades = [dict(_trade(0, "03-03"), symbol="ETH"),
              dict(_trade(1, "03-03"), symbol="UNRESOLVED")]
    r = Insights(_Svc({"orders": [], "trades": trades}), MarketSources()).records()
    assert {t["instrument_type"] for t in r["closed_trades"]} == {"unknown"}
    assert r["classification"]["unresolved"] == 2


def test_same_symbol_crypto_and_etf_require_execution_evidence():
    t = dict(_trade(0, "03-03"), symbol="ETH")
    equity = dict(_order(0, "03-02"), symbol="ETH")
    svc = _Svc({"orders": [equity], "trades": [t]})
    svc.provider.load_crypto_history = lambda h: {"orders": [
        {"currency_code": "ETH", "side": "sell", "executions": [
            {"quantity": "1", "price": "10", "effective_price": "9.99", "timestamp": t["timestamp"]}]}],
        "coverage": {"pages": 1, "truncated": False}}
    r = Insights(svc, MarketSources()).records()
    assert r["closed_trades"][0]["instrument_type"] == "crypto"
    assert r["closed_trades"][0]["assetType"] == "crypto"
    assert r["fills"][0]["assetType"] == "equity"


def test_list_pages_reports_the_window_used_and_a_read_limit_that_cut_it():
    endless = [{"data": {"trades": [{"symbol": "A"}], "next_cursor": f"c{i}", "span": "all"}} for i in range(5)]
    rh, calls = _provider(endless)
    rep = {}
    rows = rh._list_pages("trade_history", "123", cap_pages=3, report=rep, span="all")
    assert len(rows) == 3 and len(calls) == 3
    assert rep == {"pages": 3, "truncated": True, "span": "all"}

    rh, _ = _provider([{"data": {"trades": [], "next_cursor": "", "span": "week"}}])
    rep = {}
    assert rh._list_pages("trade_history", "123", report=rep) == []
    assert rep == {"pages": 1, "truncated": False, "span": "week"}


class _Svc:
    _account = "0f3c9a1b2d4e5f60"

    def __init__(self, history, positions=None):
        hist = history

        class provider:  # noqa: N801 - mimics the attribute
            @staticmethod
            def load_history(handle):
                return hist
        self.provider = provider
        self._positions = positions or []

    def view(self):
        return {"portfolio": {"positions": self._positions, "overview": {}}}


def _trade(i, day, gain="1.00"):
    return {"symbol": "AAA", "side": "sell", "quantity": "1", "price": "10", "realized_gain": gain,
            "timestamp": f"2026-{day}T15:{i % 60:02d}:00Z"}


def _order(i, day):
    return {"symbol": "BBB", "side": "buy", "state": "filled", "cumulative_quantity": "1", "average_price": "5",
            "last_transaction_at": f"2026-{day}T14:{i % 60:02d}:00Z"}


def test_records_carry_the_config_and_complete_coverage():
    hist = {"orders": [_order(1, "03-02"), _order(2, "05-04")], "trades": [_trade(1, "04-01"), _trade(2, "09-01")],
            "issues": [], "coverage": {"orders": {"pages": 1, "truncated": False, "span": None},
                                       "trades": {"pages": 1, "truncated": False, "span": "all"}}}
    r = Insights(_Svc(hist), MarketSources()).records()
    assert r["config"] == ACTIVITY
    t = r["coverage"]["trades"]
    assert (t["read"], t["passed"], t["complete"], t["capped_by"]) == (2, 2, True, None)
    assert (t["first"], t["last"]) == ("2026-04-01T15:01:00Z", "2026-09-01T15:02:00Z")
    f = r["coverage"]["fills"]
    assert (f["read"], f["passed"], f["complete"]) == (2, 2, True)
    assert (f["first"], f["last"]) == ("2026-03-02T14:01:00Z", "2026-05-04T14:02:00Z")


def test_records_preserve_instrument_type_for_each_activity_source():
    orders = [dict(_order(i, "03-02"), instrument_type=kind)
              for i, kind in enumerate(("equity", "etf", "crypto"))]
    trades = [dict(_trade(i, "03-03"), instrument_type=kind)
              for i, kind in enumerate(("equity", "etf", "crypto"))]
    positions = [{"symbol": "SAME", "broker": {"instrument_type": kind, "last_price": 12,
                  "tax_lots": [{"quantity": 1, "cost_per_share": 10, "acquired_on": "2026-03-01"}]}}
                 for kind in ("equity", "etf", "crypto")]
    r = Insights(_Svc({"orders": orders, "trades": trades}, positions), MarketSources()).records()
    for source in ("fills", "closed_trades", "lots"):
        assert {x["instrument_type"] for x in r[source]} == {"equity", "etf", "crypto"}
    legacy = Insights(_Svc({"orders": [_order(0, "03-02")], "trades": [_trade(0, "03-03")]}),
                      MarketSources()).records()
    assert legacy["fills"][0]["instrument_type"] == "equity"
    assert legacy["closed_trades"][0]["instrument_type"] == "unknown"


def test_a_row_cap_is_reported_with_its_real_size_and_the_dates_it_kept():
    cap = ACTIVITY["caps"]["fills"]
    orders = [_order(i, f"{1 + i // 200:02d}-{1 + (i // 10) % 20:02d}") for i in range(cap + 25)]
    hist = {"orders": orders, "trades": None, "issues": [],
            "coverage": {"orders": {"pages": 2, "truncated": False, "span": None}}}
    r = Insights(_Svc(hist), MarketSources()).records()
    f = r["coverage"]["fills"]
    assert (f["read"], f["passed"], f["cap"], f["capped_by"], f["complete"]) == (cap + 25, cap, cap, "rows", False)
    kept = sorted(x["at"] for x in r["fills"])
    assert (f["first"], f["last"]) == (kept[0], kept[-1]), "the dates of what was passed on, not of what was read"


def test_a_page_limit_or_a_short_window_is_not_complete():
    hist = {"orders": None, "trades": [_trade(1, "09-28")], "issues": [],
            "coverage": {"trades": {"pages": 1, "truncated": False, "span": "week"}}}
    t = Insights(_Svc(hist), MarketSources()).records()["coverage"]["trades"]
    assert (t["complete"], t["span"], t["capped_by"]) == (False, "week", "span")
    hist["coverage"]["trades"] = {"pages": ACTIVITY["caps"]["pages"], "truncated": True, "span": "all"}
    ins = Insights(_Svc(hist), MarketSources())
    t = ins.records()["coverage"]["trades"]
    assert (t["complete"], t["capped_by"], t["pages"]) == (False, "pages", ACTIVITY["caps"]["pages"])


def test_history_without_coverage_from_an_older_provider_is_not_claimed_complete():
    hist = {"orders": None, "trades": [_trade(1, "09-28")], "issues": []}
    t = Insights(_Svc(hist), MarketSources()).records()["coverage"]["trades"]
    assert t["complete"] is False and t["capped_by"] == "unknown"


def test_open_lots_carry_price_cost_and_value():
    pos = [{"symbol": "CCC", "broker": {"last_price": 12.0, "tax_lots": [
        {"quantity": 2.0, "cost_per_share": 10.0, "acquired_on": "2026-01-05", "term": "short"},
        {"quantity": 1.0, "cost_per_share": 9.0, "acquired_on": None, "term": None}]}}]
    r = Insights(_Svc({"orders": None, "trades": None, "issues": []}, pos), MarketSources()).records()
    a = next(x for x in r["lots"] if x["acquired_on"])
    assert (a["price"], a["cost"], a["value"], a["gain"]) == (12.0, 20.0, 24.0, 4.0)
    b = next(x for x in r["lots"] if not x["acquired_on"])
    assert (b["cost"], b["value"], b["gain"]) == (9.0, 12.0, 3.0)


def test_load_history_reports_coverage_per_source():
    rh = object.__new__(RobinhoodProvider)
    rh._account_number = lambda h: "n1"
    rh._rhs_accounts = {"n1": "r1"}

    class Spec:
        usable = True
        account_arg = "rhs_account_number"
        unfillable = ()
    rh._tools = {"get_equity_orders": Spec(), "get_pnl_trade_history": Spec()}
    pages = {"orders": {"data": {"orders": [{"x": 1}], "next": None}},
             "trade_history": {"data": {"trades": [{"y": 1}], "next_cursor": "", "span": "all"}}}
    rh._call = lambda role, **kw: pages[role]
    out = rh.load_history("h")
    assert out["coverage"]["trades"] == {"pages": 1, "truncated": False, "span": "all"}
    assert out["coverage"]["orders"] == {"pages": 1, "truncated": False, "span": None}


def test_the_activity_fixtures_carry_the_current_config():
    """The JS tests render the fixtures; regenerate them after a config change
    (webapp/tests/tools/make_activity_fixtures.py)."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "webapp" / "tests" / "fixtures"
    files = sorted((root / "activity").glob("*.json"))
    assert len(files) >= 9
    for f in files + [root / "my_portfolio_insights.json", root / "my_portfolio_insights_18.json"]:
        body = json.loads(f.read_text(encoding="utf-8"))
        rec = body["records"]
        assert rec["config"] == json.loads(json.dumps(ACTIVITY)), f"{f.name} is stale: regenerate the fixtures"
