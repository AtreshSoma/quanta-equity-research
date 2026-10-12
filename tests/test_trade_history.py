"""Activity reads Robinhood's own realized trades, all of them.

Robinhood's get_pnl_trade_history is the record behind the app's realized P&L.
QUANTA called it without a span, which defaults to one week, so the ledger fell
back to a first-in, first-out estimate on the order history, and that estimate
is wrong across a stock split inside the history. These tests pin the full read.
All values here are synthetic.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths()

from brokers.robinhood import RobinhoodProvider  # noqa: E402
from portfolio import history as hs  # noqa: E402


def test_robinhood_trade_records_are_read_in_full():
    t = hs.normalize_trades([{"symbol": "syn", "side": "sell", "quantity": "2.5", "price": "100.40",
                              "realized_gain": "-12.50", "timestamp": "2026-01-14T15:00:00Z"}])[0]
    assert t == {"symbol": "SYN", "instrument_type": "unknown", "side": "sell", "quantity": 2.5, "price": 100.4, "realized_gain": -12.5,
                 "closed_at": "2026-01-14", "at": "2026-01-14T15:00:00Z", "opened_at": None}


def test_an_unreadable_gain_stays_unknown_never_zero():
    t = hs.normalize_trades([{"symbol": "AAA", "side": "sell", "quantity": "1", "price": "10", "timestamp": "2026-03-17T14:15:00Z"}])[0]
    assert t["realized_gain"] is None


def _provider(pages):
    rh = object.__new__(RobinhoodProvider)
    calls = []

    def _call(role, **kw):
        calls.append(kw)
        return pages[len(calls) - 1]
    rh._call = _call
    return rh, calls


def test_trade_history_follows_next_cursor_and_asks_for_all_time():
    pages = [{"data": {"trades": [{"symbol": "A"}], "next_cursor": "c1", "span": "all"}},
             {"data": {"trades": [{"symbol": "B"}], "next_cursor": "", "span": "all"}}]
    rh, calls = _provider(pages)
    rows = rh._list_pages("trade_history", "123", **RobinhoodProvider.HISTORY_ARGS["trade_history"])
    assert [r["symbol"] for r in rows] == ["A", "B"]
    assert calls[0] == {"account": "123", "extra": {"span": "all"}}
    assert calls[1] == {"account": "123", "extra": {"span": "all"}, "cursor": "c1"}


def test_a_cancelled_order_that_partly_filled_is_a_fill_at_its_filled_quantity():
    base = {"symbol": "AAA", "side": "sell", "price": "12.00", "last_transaction_at": "2026-05-01T15:00:00Z"}
    out = hs.normalize_orders([
        dict(base, state="cancelled", quantity="10", cumulative_quantity="4", average_price="11.50"),
        dict(base, state="cancelled", quantity="10", cumulative_quantity="0", average_price=None),
        dict(base, state="filled", quantity="3", cumulative_quantity="3", average_price="11.90"),
    ])
    assert [(f.quantity, f.price) for f in out["fills"]] == [(4.0, 11.5), (3.0, 11.9)]
