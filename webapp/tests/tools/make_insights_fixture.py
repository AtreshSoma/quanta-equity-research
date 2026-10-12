"""Build tests/fixtures/my_portfolio_insights.json from the REAL Insights code.

Holdings come from my_portfolio_view.json (itself built by the real build_view). Prices,
profiles, filings and headlines are synthetic and deterministic; the simulation,
attribution, exposure, events, outcome and scenario logic are the production code.
The preview server and the JS tests serve/render this file.

    python webapp/tests/tools/make_insights_fixture.py
    BOOK=18 python webapp/tests/tools/make_insights_fixture.py   # the 18-holding book

Also records the 1D intraday simulation (synthetic 5-minute bars) and the what-if answers
of the REAL PortfolioService.whatif for every combination of the decisions' own scenarios,
keyed as the preview server looks them up (see whatif_key).
"""
import itertools
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(_ENGINE / "core"), str(_ENGINE / "modules"), str(_ENGINE / "tests")]
sys.path.insert(0, str(Path(__file__).resolve().parent))
HERE = Path(__file__).resolve().parents[1] / "fixtures"
SUFFIX = f"_{os.environ['BOOK']}" if os.environ.get("BOOK") else ""
OUT = HERE / f"my_portfolio_insights{SUFFIX}.json"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from portfolio.insights import Insights, MarketSources  # noqa: E402
from portfolio.service import PortfolioService  # noqa: E402
from portfolio.journal import DecisionJournal  # noqa: E402
from portfolio.sources import EdgarClient, load_official_calendar  # noqa: E402

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
VIEW = json.loads((HERE / f"my_portfolio_view{SUFFIX}.json").read_text())
SYMS = [r["symbol"] for r in VIEW["portfolio"]["positions"]]
LAST = {r["symbol"]: r["broker"]["last_price"] for r in VIEW["portfolio"]["positions"]}


def closes(symbols, period):
    idx = [d.date() for d in pd.bdate_range(end="2026-09-28", periods=760)]
    rng = np.random.default_rng(7)
    mkt = rng.normal(0.0004, 0.010, len(idx))
    beta = {"NVDA": 1.8, "AAPL": 1.15, "MSFT": 1.05, "RIVN": 1.6, "XOM": 0.7, "KO": 0.45, "JNJ": 0.5, "BRK.B": 0.85, "SPY": 1.0}
    out = {}
    for s in symbols:
        b = beta.get(s, 1.0)
        noise = rng.normal(0, 0.004 if s == "SPY" else 0.013, len(idx))
        r = mkt * b + (0 if s == "SPY" else noise) + (0.0009 if s == "NVDA" else -0.0012 if s == "RIVN" else 0)
        path = np.cumprod(1 + r)
        end = LAST.get(s, 571.0)
        out[s] = path / path[-1] * end
    df = pd.DataFrame(out, index=idx)
    if "RIVN" in df:
        df.loc[df.index[:430], "RIVN"] = np.nan      # listed later than the window start: excluded, never held flat
    return df


class FakeService:
    _account = "0f3c9a1b2d4e5f60"
    _returns_fn = None

    class provider:  # noqa: N801 - mimics the attribute
        @staticmethod
        def load_realized_series(handle, period):
            # Synthetic buckets in Robinhood's shape: one per hour / day / week / month by window.
            rng = np.random.default_rng({"day": 1, "week": 2, "month": 3, "3month": 4, "year": 5, "ytd": 6, "all": 7}[period])
            n, step = {"day": (7, "h"), "week": (5, "D"), "month": (21, "B"), "3month": (13, "W"), "year": (12, "MS"),
                       "ytd": (9, "MS"), "all": (24, "MS")}[period]
            end = pd.Timestamp("2026-09-29 16:00")
            starts = pd.date_range(end=end, periods=n, freq=step)
            width = {"h": pd.Timedelta(hours=1), "D": pd.Timedelta(days=1), "B": pd.Timedelta(days=1), "W": pd.Timedelta(days=7),
                     "MS": pd.DateOffset(months=1)}[step]
            buckets = []
            for i, a in enumerate(starts):
                trades = int(rng.integers(0, 4))
                gain = None if (period == "3month" and i == 4) else (round(float(rng.normal(40, 160)), 2) if trades else 0.0)
                buckets.append({"start": a.isoformat(), "end": (a + width).isoformat(), "gain": gain,
                                "rate": None if gain is None else round(gain / 50.0, 2), "trades": trades})
            total = round(sum(b["gain"] or 0 for b in buckets), 2)
            return {"period": period, "window": period, "total": total, "rate": round(total / 120, 2), "currency": "USD",
                    "buckets": buckets, "retrieved_at": NOW.isoformat()}

        @staticmethod
        def load_history(handle):
            return {"orders": None, "trades": None, "issues": [
                {"code": "ORDERS_UNUSABLE", "message": "get_equity_orders was not readable in this synthetic build."},
                {"code": "TRADE_HISTORY_UNUSABLE", "message": "get_pnl_trade_history was not readable in this synthetic build."}]}

    def view(self):
        return json.loads(json.dumps(VIEW))

    def _analyze(self, sym, size):
        return None, {"title": "synthetic build"}


def profiles(symbols):
    country = {"KO": "United States", "XOM": "United States", "AAPL": "United States", "NVDA": "United States",
               "MSFT": "United States", "JNJ": "United States", "RIVN": "United States"}
    etf = {"VOO", "QQQM"}
    return {s: {"country": country.get(s, "United States"), "quote_type": "ETF" if s in etf else "EQUITY",
                "category": "Large Blend" if s in etf else None} for s in symbols}


def articles():
    return [
        {"title": "NVIDIA Corp expands data-center partnerships ahead of earnings", "source": "Reuters Business",
         "url": "https://example.com/nvda-1", "published": "2026-09-29T11:40:00+00:00", "category": "stocks"},
        {"title": "NVIDIA Corp expands data center partnerships ahead of earnings season", "source": "MarketWatch",
         "url": "https://example.com/nvda-2", "published": "2026-09-29T12:05:00+00:00", "category": "stocks"},
        {"title": "Treasury yields edge higher before Friday's payrolls report", "source": "CNBC Markets",
         "url": "https://example.com/macro-1", "published": "2026-09-29T09:15:00+00:00", "category": "macro"},
        {"title": "Exxon Mobil to present at energy conference", "source": "Wire", "url": "https://example.com/xom",
         "published": "2026-09-28T21:30:00+00:00", "category": "stocks"},
    ]


def edgar():
    tickers = {"0": {"cik_str": 1045810, "ticker": "NVDA"}, "1": {"cik_str": 34088, "ticker": "XOM"}}
    subs = {1045810: {"filings": {"recent": {"form": ["8-K", "4"], "filingDate": ["2026-09-25", "2026-09-24"],
                                             "accessionNumber": ["0001045810-26-000101", "x"], "primaryDocument": ["nvda8k.htm", "f4.xml"],
                                             "items": ["5.02,9.01", ""], "acceptanceDateTime": ["2026-09-25T20:31:00.000Z", None]}}},
            34088: {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-09-12"], "accessionNumber": ["0000034088-26-000044"],
                                           "primaryDocument": ["xom8k.htm"], "items": ["7.01"], "acceptanceDateTime": ["2026-09-12T12:00:00.000Z"]}}}}

    def get(url):
        if url.endswith("company_tickers.json"):
            return tickers
        for cik, body in subs.items():
            if f"CIK{cik:010d}" in url:
                return body
        return {"filings": {"recent": {}}}
    return EdgarClient(get)


def intraday(symbols):
    """One synthetic session, 5-minute bars 04:00 to 19:55 New York; pre/post bars are sparse."""
    day = pd.Timestamp("2026-09-29")
    stamps = [day + pd.Timedelta(minutes=5 * i) + pd.Timedelta(hours=4) for i in range(192)]
    prev = closes(symbols, "5y").iloc[-1]
    rng = np.random.default_rng(29)
    mkt = np.cumsum(rng.normal(0.00004, 0.0007, len(stamps)))
    prices = {}
    for s in symbols:
        b = {"NVDA": 1.8, "SPY": 1.0}.get(s, 1.1)
        path = float(prev[s]) * (1 + mkt * b + np.cumsum(rng.normal(0, 0.0006, len(stamps))))
        col = []
        for t, v in zip(stamps, path):
            regular = t.hour * 60 + t.minute >= 570 and t.hour < 16
            col.append(round(float(v), 4) if regular or rng.random() < 0.35 else None)
        prices[s] = col
    return {"date": "2026-09-29", "stamps": [t.strftime("%Y-%m-%dT%H:%M:%S-04:00") for t in stamps], "prices": prices}


def whatif_key(changes):
    """The preview server's lookup key: sorted SYMBOL:cents pairs."""
    return ",".join(f"{c['symbol']}:{round(c['delta_value'] * 100)}" for c in sorted(changes, key=lambda c: c["symbol"]))


def whatifs():
    """PortfolioService.whatif for every combination of the reductions' own scenarios."""
    book = __import__("make_book18_fixture" if SUFFIX == "_18" else "make_my_portfolio_fixture")

    class _Provider:
        def status(self):
            return dict(book.status, state="connected")
    svc = PortfolioService(_Provider(), lambda *a: (None, None), clock=lambda: book.NOW)
    svc._portfolio, svc._analysis, svc._returns = book.pf, book.analysis, book.rets
    options = []
    for r in VIEW["portfolio"]["positions"]:
        g = r["guidance"]
        if g["action"] not in ("Trim", "Exit Review"):
            continue
        im = g.get("impact") or {}
        opts = [None] + [im[k]["delta_value"] for k in ("trim_to_cap", "remove") if im.get(k)]
        options.append([(r["symbol"], d) for d in opts])
    out = {}
    for combo in itertools.product(*options):
        changes = [{"symbol": s, "delta_value": d} for s, d in combo if d is not None]
        res = svc.whatif(changes)
        out[whatif_key(res["changes"])] = res
    return out


def build():
    with tempfile.TemporaryDirectory() as tmp:
        j = DecisionJournal(Path(tmp) / "j.json")
        # A journal that started recently: a handful of entries, no completed horizons.
        rows = VIEW["portfolio"]["positions"]
        j.record([dict(r, guidance=dict(r["guidance"], action="Hold")) if r["symbol"] == "XOM" else r for r in rows], "2026-09-15", NOW)
        j.record(rows, "2026-09-22", NOW)          # XOM moves Hold -> Trim: a dated call change
        ins = Insights(FakeService(), MarketSources(prices=closes, intraday=intraday, profiles=profiles, articles=articles, edgar=edgar(),
                                                    calendar=lambda: load_official_calendar(today=date(2026, 9, 29)), journal=j),
                       clock=lambda: NOW)
        journal = ins.journal_info()
        out = {
            "performance": ins.performance(),
            "intraday": ins.intraday(),
            "whatif": whatifs(),
            "exposure": ins.exposure({}),
            "events": ins.events(),
            "outcomes": ins.outcomes(),
            "records": ins.records(),
            "activity": {p: ins.activity(p) for p in ins.ACTIVITY_PERIODS},
            "plan": ins.plan({"contribution": 2000, "liquidity_reserve": 3000}, [], False),
            "journal": dict(journal, path_hint="portfolio_journal.json"),
        }
    return json.loads(json.dumps(out, default=str))


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    p = data["performance"]
    print("perf", p["available"], {k: v["available"] for k, v in p["simulation"].items()})
    print("events", data["events"]["counts"], "plan", [s["status"] for s in data["plan"]["scenarios"]])
