"""Build tests/fixtures/activity/<scenario>.json for the Activity tab.

Synthetic data only: tickers, amounts and dates are generated. The payloads are in
Robinhood's raw shapes (get_pnl_trade_history, get_equity_orders, get_realized_pnl)
and go through the REAL RobinhoodProvider parsing (paging, realized buckets) and the
REAL Insights.records() / Insights.activity(), so the fixtures carry the server's
coverage and config exactly as a live server would send them.

    python webapp/tests/tools/make_activity_fixtures.py

Scenarios: empty, one_trade, many (1,000 trades in 75 stocks), all_losses, all_gains,
capped (order rows over the cap, trade pages over the read limit), missing_lot_dates,
no_figure_bucket (a Robinhood bucket with closing trades but no figure), short_window
(the defect-1 server: Robinhood's one-week default against a month with closings).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(_ENGINE / "core"), str(_ENGINE / "modules"), str(_ENGINE / "tests")]
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "activity"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from brokers.robinhood import READ_TOOLS, RobinhoodProvider  # noqa: E402
from portfolio.activity_config import ACTIVITY  # noqa: E402
from portfolio.insights import Insights, MarketSources  # noqa: E402

TZ = ACTIVITY["timezone"]
NOW = pd.Timestamp("2026-09-29 16:00", tz=TZ)
HANDLE = "0f3c9a1b2d4e5f60"


def sym(i):
    return "S" + chr(65 + i // 26) + chr(65 + i % 26)            # SAA, SAB, ... synthetic tickers


def money(x):
    return f"{x:.2f}"


def trade(symbol, when, qty, price, gain, side="sell"):
    return {"timestamp": when.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": symbol, "side": side,
            "quantity": f"{qty:g}", "price": money(price), "realized_gain": gain if isinstance(gain, str) or gain is None else money(gain)}


def order(symbol, when, qty, price, side="buy"):
    return {"symbol": symbol, "side": side, "state": "filled", "quantity": f"{qty:g}", "cumulative_quantity": f"{qty:g}",
            "average_price": money(price), "price": money(price), "fees": "0.00",
            "last_transaction_at": when.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")}


def session_time(day, rng):
    return pd.Timestamp(day, tz=TZ) + pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(minutes=int(rng.integers(0, 389)))


def random_trades(rng, n, symbols, start, end, sign=0):
    days = pd.bdate_range(start, end)
    out = []
    for _ in range(n):
        s = symbols[int(rng.integers(0, len(symbols)))]
        qty = round(float(rng.choice([1, 2, 3, 5, 10, rng.uniform(0.05, 4)])), 4)
        price = round(float(rng.uniform(8, 600)), 2)
        r = float(rng.normal(0.03, 0.12)) if sign == 0 else abs(float(rng.normal(0.06, 0.08))) * sign or 0.01 * sign
        gain = round(qty * price * r / (1 + r), 2)
        if gain == 0:
            gain = 0.01 if sign >= 0 else -0.01
        out.append(trade(s, session_time(days[int(rng.integers(0, len(days)))], rng), qty, price, gain))
    return out


def buckets_for(span, trades, no_figure_days=()):
    """Robinhood-shaped realized buckets over the span's window, summed from the trades."""
    end = NOW.normalize() + pd.Timedelta(days=1)
    if span == "day":
        starts = pd.date_range(NOW.normalize() + pd.Timedelta(hours=9), periods=8, freq="h")
        width = pd.Timedelta(hours=1)
    elif span in ("week", "month", "3month"):
        back = {"week": pd.DateOffset(days=7), "month": pd.DateOffset(months=1), "3month": pd.DateOffset(months=3)}[span]
        starts = pd.date_range(NOW.normalize() - back, end - pd.Timedelta(days=1), freq="D")
        width = pd.Timedelta(days=1)
    elif span in ("ytd", "year"):
        first = pd.Timestamp(f"{NOW.year}-01-01", tz=TZ) if span == "ytd" else NOW.normalize() - pd.DateOffset(years=1)
        starts = pd.date_range(first, end - pd.Timedelta(days=1), freq="7D")
        width = pd.Timedelta(days=7)
    else:
        times = [pd.Timestamp(t["timestamp"]).tz_convert(TZ) for t in trades]
        first = (min(times) if times else NOW).normalize().replace(day=1)
        starts = pd.date_range(first, end, freq="MS")
        width = None
    pts, total = [], 0.0
    for i, a in enumerate(starts):
        z = (a + width) if width is not None else (starts[i + 1] if i + 1 < len(starts) else end)
        z = min(z, end)
        inside = [t for t in trades if a <= pd.Timestamp(t["timestamp"]).tz_convert(TZ) < z]
        blank = any(a <= pd.Timestamp(d, tz=TZ) < z for d in no_figure_days)
        gains = [float(t["realized_gain"]) for t in inside if t["realized_gain"] not in (None, "")]
        gain = None if blank or not inside else round(sum(gains), 2)
        if gain is not None:
            total += gain
        pts.append({"start_time": a.isoformat(), "end_time": z.isoformat(), "number_of_trades": len(inside),
                    "realized_gain": None if gain is None else money(gain),
                    "rate_of_realized_gain": None if gain is None else "0"})
    return {"data": {"account_number": "synthetic", "window": span, "display_currency": "USD", "data_points": pts,
                     "total_returns": money(total), "total_rate_of_return": "0"}, "guide": ""}


class Spec:
    usable = True
    account_arg = "rhs_account_number"
    unfillable = ()


def provider(sc):
    """The real provider, with its transport replaced by the scenario's raw pages."""
    rh = object.__new__(RobinhoodProvider)
    rh._account_number = lambda h: "n1"
    rh._rhs_accounts = {"n1": "r1"}
    rh._tools = {READ_TOOLS[k]: Spec() for k in ("orders", "trade_history", "realized_pnl")}
    page = sc.get("page_size", 50)
    order_page = sc.get("order_page_size", 124)
    echoed = sc.get("trade_span", "all")
    trades = sorted(sc["trades"], key=lambda t: t["timestamp"], reverse=True)       # most recent first
    if echoed != "all":
        cut = (NOW - pd.Timedelta(days=7)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        trades = [t for t in trades if t["timestamp"] >= cut]
    orders = sorted(sc["orders"], key=lambda o: o["last_transaction_at"], reverse=True)

    def _call(role, **kw):
        cur = int(kw.get("cursor") or 0)
        if role == "realized_pnl":
            span = (kw.get("extra") or {}).get("span") or "ytd"
            return buckets_for(span, sc["trades"], sc.get("no_figure_days", ()))
        rows, key, nxt = (trades, "trades", "next_cursor") if role == "trade_history" else (orders, "orders", "next")
        size = page if role == "trade_history" else order_page
        chunk = rows[cur:cur + size]
        more = cur + size < len(rows)
        data = {key: chunk, nxt: str(cur + size) if more else ("" if nxt == "next_cursor" else None)}
        if role == "trade_history":
            data["span"] = echoed
        return {"data": data, "guide": ""}
    rh._call = _call
    return rh


class Svc:
    _account = HANDLE

    def __init__(self, sc):
        self.provider = provider(sc)
        self._positions = sc.get("positions", [])

    def view(self):
        return {"portfolio": {"positions": json.loads(json.dumps(self._positions)), "overview": {}}}


def position(symbol, price, lots):
    return {"symbol": symbol, "broker": {"last_price": price, "tax_lots": lots}}


def lot(qty, cost, acquired, term=None):
    return {"quantity": qty, "cost_per_share": cost, "acquired_on": acquired, "term": term}


def scenarios():
    rng = np.random.default_rng(20261002)
    few = ["SAA", "SAB", "SAC", "SAD"]
    yr = (NOW - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    today = NOW.strftime("%Y-%m-%d")
    lots_mixed = [position("SAA", 120.0, [lot(3.0, 100.0, "2026-08-03", "short"), lot(1.5, 130.0, "2026-09-21", "short")]),
                  position("SAB", 40.0, [lot(10.0, 52.5, "2025-06-10", "long"), lot(2.25, 38.0, "2026-04-02", "short")]),
                  position("SAC", 15.0, [lot(20.0, 12.0, "2026-01-15", "short")])]
    many_syms = [sym(i) for i in range(75)]
    many = random_trades(rng, 1000, many_syms, "2024-10-01", today)
    many_orders = [order(s, session_time(d, rng), round(float(rng.uniform(0.5, 6)), 4), round(float(rng.uniform(8, 600)), 2))
                   for s, d in zip(rng.choice(many_syms, 380), rng.choice(pd.bdate_range("2025-10-01", today), 380))]
    bucket_day = (NOW - pd.Timedelta(days=20)).normalize()
    nf_trades = random_trades(rng, 30, few, (NOW - pd.DateOffset(months=3)).strftime("%Y-%m-%d"), today)
    nf_trades += [trade("SAB", bucket_day + pd.Timedelta(hours=11), 5, 20.0, ""),
                  trade("SAC", bucket_day + pd.Timedelta(hours=13), 2, 30.0, 4.5)]
    month_trades = random_trades(rng, 6, few, (NOW - pd.DateOffset(days=28)).strftime("%Y-%m-%d"),
                                 (NOW - pd.DateOffset(days=9)).strftime("%Y-%m-%d"))
    return {
        "empty": {"trades": [], "orders": [], "positions": []},
        # The reported case: one closing trade of about +$21 on about $520 of cost.
        "one_trade": {"trades": [trade("SAA", pd.Timestamp("2026-09-15 14:05", tz=TZ), 4, 135.25, 21.04)],
                      "orders": [order("SAA", pd.Timestamp("2026-08-03 10:12", tz=TZ), 4, 129.99)], "positions": lots_mixed},
        "many": {"trades": many, "orders": many_orders, "positions": lots_mixed},
        "all_losses": {"trades": random_trades(rng, 12, few, yr, today, sign=-1), "orders": [], "positions": lots_mixed},
        "all_gains": {"trades": random_trades(rng, 12, few, yr, today, sign=1), "orders": [], "positions": lots_mixed},
        "capped": {"trades": random_trades(rng, 260, few, "2025-01-02", today), "page_size": 10,
                   "orders": [order(few[i % 4], session_time(d, rng), 1.5, 25.0 + i % 7)
                              for i, d in enumerate(pd.bdate_range(end=today, periods=460))], "positions": lots_mixed},
        "missing_lot_dates": {"trades": random_trades(rng, 8, few, yr, today), "orders": [],
                              "positions": [position("SAA", 120.0, [lot(3.0, 100.0, None), lot(1.0, 110.0, "2026-07-01", "short")]),
                                            position("SAB", 40.0, [lot(4.0, 45.0, None)])]},
        "no_figure_bucket": {"trades": nf_trades, "orders": [], "positions": lots_mixed,
                             "no_figure_days": [bucket_day.strftime("%Y-%m-%d")]},
        "short_window": {"trades": month_trades, "orders": [], "positions": lots_mixed, "trade_span": "week"},
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sc in scenarios().items():
        ins = Insights(Svc(sc), MarketSources(), clock=lambda: NOW.to_pydatetime())
        body = {"scenario": name, "now": NOW.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "records": ins.records(),
                "activity": {r["span"]: ins.activity(r["span"]) for r in ACTIVITY["ranges"]}}
        (OUT / f"{name}.json").write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
        rec = body["records"]
        print(f"{name}: {len(rec['closed_trades'] or [])} trades, {len(rec['fills'] or [])} fills, {len(rec['lots'])} lots, "
              f"coverage {json.dumps({k: (v['capped_by'], v['passed']) for k, v in rec['coverage'].items()})}")


if __name__ == "__main__":
    main()
