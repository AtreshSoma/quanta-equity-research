"""Fixes from the core/portfolio audit (2026-11-04): caches that outlive an account, a
candidate job that strands names, an SEC outage that stalls every holding, and a
per-trade average over a partial count. Synthetic data only."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import pytest

from _paths import ensure_paths

ensure_paths()

from portfolio import profit_plan as pp  # noqa: E402
from portfolio.insights import Insights, MarketSources  # noqa: E402
from portfolio.models import Position, TaxLot  # noqa: E402
from portfolio.service import _sale_detail  # noqa: E402
from portfolio.sources import OUTAGE_TTL, EdgarClient  # noqa: E402


class _Svc:
    """Enough of PortfolioService for Insights: an account, a provider and a view."""

    def __init__(self, histories, analyze=None):
        self._account = "aaaaaaaaaaaaaaaa"
        self.histories = histories
        self.calls = []
        svc = self

        class provider:  # noqa: N801
            @staticmethod
            def load_history(handle):
                svc.calls.append(handle)
                return svc.histories[handle]
        self.provider = provider
        self._analyze = analyze or (lambda sym, size: ({"quote_type": "EQUITY"}, None))
        self._returns_fn = None

    def view(self):
        return {"portfolio": {"positions": [], "overview": {"total_value": 10000.0}, "metrics": {}, "policy": {},
                              "reconciliation": {"status": "ok"}}}


def _hist(symbol):
    return {"orders": [], "trades": [{"symbol": symbol, "side": "sell", "quantity": "1", "price": "10",
                                      "realized_gain": "1.00", "timestamp": "2026-03-03T15:00:00Z"}], "issues": []}


# ---- 3: broker history belongs to one account and ends with the connection -------------------

def test_history_cache_is_per_account_and_empty_after_disconnect():
    svc = _Svc({"aaaaaaaaaaaaaaaa": _hist("AAA"), "bbbbbbbbbbbbbbbb": _hist("BBB")})
    ins = Insights(svc, MarketSources())
    assert ins._ledger()["trades"][0]["symbol"] == "AAA"
    ins._ledger()
    assert svc.calls == ["aaaaaaaaaaaaaaaa"]                 # cached while the account is unchanged
    svc._account = "bbbbbbbbbbbbbbbb"
    assert ins._ledger()["trades"][0]["symbol"] == "BBB"     # not the other account's trades
    svc._account = None                                      # disconnect
    out = ins._ledger()
    assert out["orders"] is None and out["trades"] is None
    assert ins.outcomes()["trades"]["source"] is None


# ---- 2: a new candidate run does not strand the names still queued ---------------------------

def test_a_second_candidate_run_takes_over_the_queued_names():
    started = []

    class Quiet(Insights):
        def _run_candidates(self, job, symbols, size, holdings):      # no thread work: inspect the hand-over
            started.append((job, list(symbols)))

    ins = Quiet(_Svc({}), MarketSources())
    import threading
    real = threading.Thread
    threading.Thread = lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})()
    try:
        ins.plan({}, ["AAA", "BBB", "CCC"], run=True)
        first = started[0][0]
        # A is mid-analysis, B and C wait; the user now asks for D as well.
        ins._cand["AAA"]["state"] = "running"
        ins.plan({}, ["AAA", "BBB", "CCC", "DDD"], run=True)
    finally:
        threading.Thread = real
    assert first["cancelled"] is True
    assert sorted(started[1][1]) == ["BBB", "CCC", "DDD"]    # B and C are not left "pending" forever
    # Asking again while that job is live starts nothing new (a polling client must not restart it).
    count = len(started)
    threading.Thread = lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})()
    try:
        ins._cand_job["finished_at"] = None
        ins.plan({}, ["AAA", "BBB", "CCC", "DDD"], run=True)
    finally:
        threading.Thread = real
    assert len(started) == count


# ---- 4: one SEC outage is answered at once for the other holdings ----------------------------

def test_sec_outage_is_remembered_so_other_holdings_do_not_each_wait():
    now = [1000.0]
    seen = []

    def down(url):
        seen.append(url)
        raise TimeoutError("timed out")
    c = EdgarClient(down, clock=lambda: now[0])
    first = c.recent_filings("AAA", date(2026, 9, 1))
    assert first["status"] == "error" and first["reason"] == "timeout"
    c.recent_filings("BBB", date(2026, 9, 1))
    c.recent_filings("CCC", date(2026, 9, 1))
    assert len(seen) == 1                                    # not one 8-second wait per holding
    now[0] += OUTAGE_TTL + 1
    c.recent_filings("DDD", date(2026, 9, 1))
    assert len(seen) == 2                                    # and it tries again once the pause is over


def test_one_bad_filing_page_is_not_treated_as_an_outage():
    import urllib.error
    tickers = {"0": {"cik_str": 1, "ticker": "AAA"}, "1": {"cik_str": 2, "ticker": "BBB"}}

    def get(url):
        if url.endswith("company_tickers.json"):
            return tickers
        if "CIK0000000001" in url:
            raise urllib.error.HTTPError(url, 404, "nf", None, None)
        return {"filings": {"recent": {"form": [], "filingDate": []}}}
    c = EdgarClient(get)
    assert c.recent_filings("AAA", date(2026, 9, 1))["status"] == "error"
    assert c.recent_filings("BBB", date(2026, 9, 1))["status"] == "ok"


# ---- 5: per-trade average only over a complete count -----------------------------------------

def test_per_trade_is_unknown_when_a_bucket_has_no_trade_count():
    def realized(buckets):
        svc = _Svc({})
        svc.provider.load_realized_series = staticmethod(
            lambda handle, period: {"buckets": buckets, "total": sum(b["gain"] or 0 for b in buckets)})
        return Insights(svc, MarketSources()).activity("month")["stats"]["per_trade"]
    assert realized([{"gain": 100.0, "trades": 2}, {"gain": 300.0, "trades": None}]) is None
    assert realized([{"gain": 100.0, "trades": 2}, {"gain": 300.0, "trades": 2}]) == pytest.approx(100.0)


# ---- 6: the trim preview and the profit plan share one gain rule -----------------------------

@pytest.mark.parametrize("lots", [
    [TaxLot(10, 100.0, "2023-01-02", "long"), TaxLot(15, 250.0, "2026-06-01", "short")],
    [TaxLot(10, 100.0, "2023-01-02")],                       # no stated term: derived from the date
    None,
])
def test_sale_detail_matches_profit_plan_sale_gain(lots):
    p = Position("AAA", 25, average_cost=180.0, last_price=300.0, tax_lots=lots)
    today = date(2026, 11, 4)
    d = _sale_detail(p, 3600.0, today)
    g = pp.sale_gain(12.0, 300.0, 180.0, [x.to_dict() for x in lots] if lots else None, today)
    assert d["shares"] == pytest.approx(12.0) and d["gain"] == pytest.approx(g["gain"])
    assert d.get("gain_long") == g["gain_long"] if g["basis"].startswith("lots") else "gain_long" not in d


# ---- 8/9: the shared price cache drops what has expired --------------------------------------

def test_expired_price_reads_are_dropped_when_a_new_one_is_stored(monkeypatch):
    ins = Insights(_Svc({}), MarketSources(prices=lambda syms, period: object()))
    ins._prices(["AAA"], "5y")
    assert len(ins._closes) == 1
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    ins._prices(["BBB"], "5y")
    assert list(ins._closes) == [(("BBB",), "5y")]
    assert datetime.now(timezone.utc)                         # (keeps the import used)
