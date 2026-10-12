"""Portfolio Review / Exposure / Events / Plan / Journal contracts.

Synthetic data only. These prove the arithmetic, the definitions and the fail-closed
behaviour; none of it is evidence about a live Robinhood account.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

from portfolio import events as ev  # noqa: E402
from portfolio import exposure as ex  # noqa: E402
from portfolio import history as hs  # noqa: E402
from portfolio import plan as pl  # noqa: E402
from portfolio.analytics import PortfolioPolicy  # noqa: E402
from portfolio.journal import DecisionJournal  # noqa: E402
from portfolio.sources import EdgarClient, load_official_calendar  # noqa: E402

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


def closes(n=300, symbols=("AAA", "BBB", "SPY"), start="2025-06-02", drift=(0.0008, 0.0002, 0.0004), seed=1):
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    cols = {}
    for s, d in zip(symbols, drift):
        cols[s] = 100 * np.cumprod(1 + d + rng.normal(0, 0.01, n))
    df = pd.DataFrame(cols, index=[x.date() for x in idx])
    return df


def rows_for(specs):
    """[(symbol, value, weight, sector, extra_broker)] -> view rows."""
    out = []
    for sym, value, weight, sector, extra in specs:
        out.append({"symbol": sym,
                    "broker": dict({"market_value": value, "weight": weight, "quantity": 10, "last_price": value / 10,
                                    "name": sym + " Corp", "tax_lots": None}, **extra),
                    "market": {"sector": sector, "beta": 1.0}, "analysis": {},
                    "guidance": {"status": "ok", "action": "Hold", "conviction": 60, "key_reason": "x"}})
    return out


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class TestSimulation:
    def test_replays_current_shares_and_reconciles_contributions(self):
        df = closes()
        h = [{"symbol": "AAA", "quantity": 10, "value": 1000}, {"symbol": "BBB", "quantity": 5, "value": 500}]
        sim = hs.simulate_current_holdings(h, df, "SPY", "6M")
        assert sim["available"] and sim["label"] == "Current holdings simulation" and sim["kind"] == "simulation"
        # contributions in points add back to the portfolio return
        assert sim["contribution_total_points"] == pytest.approx(sim["stats"]["portfolio_return"] * 100, rel=1e-9)
        assert sim["portfolio"][0] == 100 and sim["benchmark_series"][0] == 100
        assert len(sim["dates"]) == len(sim["portfolio"]) == len(sim["benchmark_series"]) == len(sim["drawdown"])
        assert sim["stats"]["max_drawdown"]["depth"] <= 0

    def test_missing_history_is_excluded_and_named_never_held_flat(self):
        df = closes()
        df.loc[df.index[:200], "BBB"] = np.nan          # BBB has no price for most of the window
        h = [{"symbol": "AAA", "quantity": 10, "value": 1000}, {"symbol": "BBB", "quantity": 1, "value": 100},
             {"symbol": "NEW", "quantity": 1, "value": 50}]
        sim = hs.simulate_current_holdings(h, df, "SPY", "1Y")
        assert sim["available"]
        ex_syms = {e["symbol"] for e in sim["excluded"]}
        assert {"BBB", "NEW"} <= ex_syms
        assert sim["coverage"] == pytest.approx(1000 / 1150)

    def test_low_coverage_refuses_to_draw(self):
        df = closes()
        h = [{"symbol": "AAA", "quantity": 1, "value": 100}, {"symbol": "ZZZ", "quantity": 10, "value": 900}]
        sim = hs.simulate_current_holdings(h, df, "SPY", "3M")
        assert sim["available"] is False and "60%" in sim["reason"]

    def test_gap_inside_window_excludes_the_holding(self):
        df = closes()
        df.loc[df.index[-60:-40], "AAA"] = np.nan        # a 20-day hole
        sim = hs.simulate_current_holdings([{"symbol": "AAA", "quantity": 1, "value": 100},
                                            {"symbol": "BBB", "quantity": 1, "value": 100}], df, "SPY", "6M")
        assert any(e["symbol"] == "AAA" and "gaps" in e["reason"] for e in sim["excluded"])

    def test_no_benchmark_no_chart(self):
        sim = hs.simulate_current_holdings([{"symbol": "AAA", "quantity": 1, "value": 100}], closes()[["AAA"]], "SPY")
        assert sim["available"] is False


class TestOpenLotAttribution:
    def test_lots_bought_in_window_use_actual_cost_and_sold_lots_are_disclosed_absent(self):
        df = closes(n=100, symbols=("AAA", "SPY"), drift=(0.001, 0.0))
        dates = list(df.index)
        start = dates[10].isoformat()
        buy_day = dates[50].isoformat()
        lots = [{"quantity": 4, "cost_per_share": 90.0, "acquired_on": "2020-01-01"},
                {"quantity": 6, "cost_per_share": 120.0, "acquired_on": buy_day}]
        out = hs.open_lot_attribution([{"symbol": "AAA", "tax_lots": lots}], df, start)
        assert out["available"]
        row = out["rows"][0]
        end_px, start_px = df["AAA"].iloc[-1], df["AAA"].iloc[10]
        assert row["dollars"] == pytest.approx(4 * (end_px - start_px) + 6 * (end_px - 120.0))
        assert "Positions sold" in out["note"]

    def test_missing_lots_are_unknown_not_zero(self):
        df = closes(symbols=("AAA", "SPY"), drift=(0, 0))
        out = hs.open_lot_attribution([{"symbol": "AAA", "tax_lots": None}], df, df.index[5].isoformat())
        assert out["available"] is False and out["unknown"][0]["symbol"] == "AAA"


class TestLedger:
    def fills(self):
        return [hs.Fill("AAA", "buy", 10, 100.0, "2025-07-01T14:00:00Z"),
                hs.Fill("AAA", "buy", 5, 110.0, "2025-09-01T14:00:00Z"),
                hs.Fill("BBB", "buy", 4, 100.0, "2025-08-01T14:00:00Z"),
                hs.Fill("BBB", "sell", 1, 105.0, "2025-10-01T14:00:00Z")]

    def test_reconciles_only_when_shares_match(self):
        pos = [{"symbol": "AAA", "quantity": 15}, {"symbol": "BBB", "quantity": 3}]
        assert hs.reconcile_ledger(self.fills(), pos)["ok"]
        bad = hs.reconcile_ledger(self.fills(), [{"symbol": "AAA", "quantity": 14}, {"symbol": "BBB", "quantity": 3}])
        assert not bad["ok"] and bad["mismatched"][0]["symbol"] == "AAA"
        assert not hs.reconcile_ledger([], pos)["ok"]

    def test_time_weighted_return_ignores_contributions_of_money(self):
        # One stock, flat price, then bought twice: value rises with the buys but the return must stay 0.
        idx = [d.date() for d in pd.bdate_range("2025-01-02", periods=40)]
        df = pd.DataFrame({"AAA": 100.0, "SPY": np.linspace(100, 110, 40)}, index=idx)
        f = [hs.Fill("AAA", "buy", 10, 100.0, f"{idx[3]}T14:00:00Z"), hs.Fill("AAA", "buy", 30, 100.0, f"{idx[20]}T14:00:00Z")]
        out = hs.ledger_performance(f, df, "SPY", "MAX")
        assert out["available"]
        assert out["stats"]["portfolio_return"] == pytest.approx(0.0, abs=1e-12)
        assert out["stats"]["benchmark_return"] > 0

    def test_return_is_measured_on_the_shares_held_each_day(self):
        idx = [d.date() for d in pd.bdate_range("2025-01-02", periods=30)]
        px = np.array([100.0] * 15 + [110.0] * 15)
        df = pd.DataFrame({"AAA": px, "SPY": 100.0}, index=idx)
        f = [hs.Fill("AAA", "buy", 10, 100.0, f"{idx[2]}T14:00:00Z")]
        out = hs.ledger_performance(f, df, "SPY", "MAX")
        assert out["stats"]["portfolio_return"] == pytest.approx(0.10)

    def test_order_normalizer_rejects_unreadable_and_non_fills(self):
        recs = [{"symbol": "AAA", "side": "buy", "state": "filled", "cumulative_quantity": "2", "average_price": "10",
                 "last_transaction_at": "2025-01-05T10:00:00Z"},
                {"symbol": "AAA", "side": "buy", "state": "cancelled", "quantity": "2", "price": "10", "created_at": "2025-01-05"},
                {"symbol": "AAA", "side": "buy", "state": "filled", "quantity": "x", "price": "10", "created_at": "2025-01-05"}]
        n = hs.normalize_orders(recs)
        assert len(n["fills"]) == 1 and n["rejected"] == 1


class TestOutcomes:
    def test_wilson_interval_bounds(self):
        lo, hi = hs.wilson_interval(15, 20)
        assert 0.5 < lo < 0.75 < hi < 0.92
        assert hs.wilson_interval(0, 0) is None

    def test_trade_outcomes_insufficient_below_minimum(self):
        out = hs.trade_outcomes([{"realized_gain": 5}] * 5)
        assert out["status"] == "insufficient" and out["rate"] is None and out["n"] == 5
        assert "20" in out["missing"][0]

    def test_trade_outcomes_denominator_payoff_and_unreadable(self):
        trades = [{"realized_gain": 100}] * 12 + [{"realized_gain": -50}] * 8 + [{"realized_gain": None}] * 3
        out = hs.trade_outcomes(trades)
        assert out["n"] == 20 and out["unreadable"] == 3 and out["successes"] == 12
        assert out["rate"] == pytest.approx(0.6)
        assert out["expectancy"] == pytest.approx((1200 - 400) / 20)
        assert out["payoff_ratio"] == pytest.approx(2.0)
        assert out["profit_factor"] == pytest.approx(3.0)
        assert "future" in out["definition"]["not"].lower()

    def test_recommendations_never_evaluate_unfinished_horizons(self):
        df = closes(n=200, symbols=("AAA", "SPY"), drift=(0.002, 0.0))
        e = [{"symbol": "AAA", "action": "Accumulate", "date": df.index[10].isoformat()},
             {"symbol": "AAA", "action": "Accumulate", "date": df.index[190].isoformat()},
             {"symbol": "AAA", "action": "Hold", "date": df.index[10].isoformat()}]
        out = hs.recommendation_outcomes(e, df, "SPY", minimum=1)
        assert out["n"] == 1 and out["pending"] == 1 and out["directional"] == 2
        assert out["sample"][0]["success"] is True   # AAA drifts up vs flat SPY

    def test_empty_journal_says_it_never_backfills(self):
        out = hs.recommendation_outcomes([], None, "SPY")
        assert out["status"] == "insufficient" and "never backfills" in out["missing"][0]

    def test_trim_is_a_success_when_the_stock_lags(self):
        df = closes(n=200, symbols=("AAA", "SPY"), drift=(-0.002, 0.0))
        out = hs.recommendation_outcomes([{"symbol": "AAA", "action": "Trim", "date": df.index[5].isoformat()}], df, "SPY", minimum=1)
        assert out["sample"][0]["success"] is True


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------

class TestExposure:
    def build(self, profiles=None):
        rows = rows_for([("AAA", 4000, 0.40, "Technology", {}), ("BBB", 2000, 0.20, "Energy", {}),
                         ("FND", 1000, 0.10, "", {}), ("UNK", 1000, 0.10, "", {})])
        rows[2]["analysis"] = {"is_etf": True}
        metrics = {"concentration": {"effective_names": 3.0}, "volatility": {"correlated_pairs": [
            {"a": "AAA", "b": "BBB", "correlation": 0.8}], "days": 250, "coverage": 1.0}}
        return ex.build_exposures(rows, 10000, 2000, profiles or {"AAA": {"country": "United States"},
                                                              "BBB": {"country": "Canada"},
                                                              "FND": {"quote_type": "ETF", "category": "Large Blend"}},
                                  {"max_sector": 0.3, "max_single_position": 0.1, "correlation_threshold": 0.7}, metrics)

    def test_views_are_separate_classifications_and_cash_is_never_a_sector(self):
        out = self.build()
        names = {b["name"] for b in out["views"]["sector"]["items"]}
        assert "Cash" not in names and "Large Blend" not in names
        assert "Funds (not split by sector)" in names and "Sector not reported" in names
        assets = {b["name"] for b in out["views"]["asset_class"]["items"]}
        assert assets == {"Individual stocks", "Stock funds"}
        geo = {b["name"] for b in out["views"]["geography"]["items"]}
        assert {"United States", "Canada", "Funds (not looked through)", "Country not reported"} == geo

    def test_weights_are_shares_of_the_whole_account_and_add_up(self):
        out = self.build()
        for v in out["views"].values():
            assert sum(b["weight"] for b in v["items"]) == pytest.approx(0.8)
        assert out["cash_weight"] == pytest.approx(0.2)
        assert out["views"]["sector"]["unclassified"] == pytest.approx(0.1)

    def test_limits_are_labelled_and_never_universal(self):
        out = self.build()
        tech = next(b for b in out["views"]["sector"]["items"] if b["name"] == "Technology")
        assert tech["limit"] == 0.3 and tech["limit_source"] == "QUANTA default" and tech["over"]
        assert all(b["limit"] is None for b in out["views"]["asset_class"]["items"])
        assert all(b["limit"] is None for b in out["views"]["geography"]["items"])
        fund = next(b for b in out["views"]["sector"]["items"] if b["name"].startswith("Funds"))
        assert fund["limit"] is None                       # a disclosure bucket is not a sector

    def test_user_limit_overrides_default_and_says_so(self):
        rows = rows_for([("AAA", 4000, 0.40, "Technology", {})])
        out = ex.build_exposures(rows, 10000, 0, {}, {"max_sector": 0.3, "max_single_position": 0.1}, {},
                                 {"max_sector": 0.5, "targets": {"sector": {"Technology": 0.25}}})
        tech = out["views"]["sector"]["items"][0]
        assert tech["limit"] == 0.25 and tech["limit_source"] == "Your target"

    def test_lookthrough_is_off_and_says_why(self):
        out = self.build()
        assert out["lookthrough"]["available"] is False and "as-of date" in out["lookthrough"]["reason"]
        assert out["concentration"]["top5"]["weight"] == pytest.approx(0.8)
        assert out["correlated"][0]["combined_weight"] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

class TestJournal:
    def row(self, sym="AAA", action="Accumulate", price=10.0, status="ok"):
        return {"symbol": sym, "broker": {"last_price": price},
                "guidance": {"status": status, "action": action, "stance": "Bullish",
                             "basis": {"code": "NEW_MONEY_ELIGIBLE", "text": "because"}, "key_reason": "because"},
                "analysis": {"decision": {"score": 70}}}

    def test_records_calls_not_data_failures_and_stores_no_amounts(self, tmp_path):
        j = DecisionJournal(tmp_path / "j.json")
        assert j.record([self.row(), self.row("BBB", "Unavailable", status="unavailable")], "2026-09-29") == 1
        raw = (tmp_path / "j.json").read_text()
        assert "quantity" not in raw and "market_value" not in raw and "weight" not in raw
        e = j.entries()[0]
        assert e["symbol"] == "AAA" and e["kind"] == "first" and e["price"] == 10.0

    def test_repeat_sightings_are_not_new_entries_but_changes_and_reaffirmations_are(self, tmp_path):
        j = DecisionJournal(tmp_path / "j.json")
        j.record([self.row()], "2026-09-01")
        assert j.record([self.row()], "2026-09-01") == 0
        assert j.record([self.row()], "2026-09-10") == 0
        assert j.record([self.row(action="Trim")], "2026-09-11") == 1
        assert j.changes()[0]["from_action"] == "Accumulate"
        assert j.record([self.row(action="Trim")], "2026-10-15") == 1          # 21+ days later: reaffirmed
        assert [e["kind"] for e in j.entries()] == ["first", "change", "reaffirmed"]

    def test_journal_never_accepts_an_earlier_date(self, tmp_path):
        j = DecisionJournal(tmp_path / "j.json")
        j.record([self.row()], "2026-09-20")
        assert j.record([self.row(action="Trim")], "2026-09-01") == 0

    def test_corrupt_file_starts_empty_instead_of_crashing(self, tmp_path):
        p = tmp_path / "j.json"
        p.write_text("{not json")
        j = DecisionJournal(p)
        assert j.entries() == [] and j.record([self.row()], "2026-09-29") == 1


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def ev_rows():
    return rows_for([
        ("AAA", 4000, 0.40, "Technology", {"earnings_date": "2026-09-29", "earnings_verified": True, "earnings_timing": "pm",
                                            "eps_estimate": 1.25}),
        ("BBB", 2000, 0.20, "Energy", {"earnings_date": "2026-10-20", "earnings_verified": False}),
        ("KO", 1000, 0.10, "Staples", {"ex_dividend_date": "2026-10-01", "dividend_per_share": 0.5,
                                       "payable_date": "2026-10-15"}),
    ])


class TestEvents:
    def build(self, **kw):
        cal = {"status": "ok", "retrieved_on": "2026-09-29", "events": [
            {"kind": "jobs", "title": "Employment Situation", "date": "2026-10-02", "time": "08:30", "tz": "America/New_York",
             "source": "BLS", "url": "https://www.bls.gov/schedule/news_release/empsit.htm"}]}
        args = dict(now=NOW, policy={"earnings_window_days": 7}, filings={}, articles=[], calendar=cal, portfolio_beta=1.1)
        args.update(kw)
        return ev.build_events(ev_rows(), **args)

    def test_confirmed_versus_tentative_earnings_and_buckets(self):
        out = self.build()
        by = {i["id"]: i for i in out["upcoming"]}
        a = by["earn:AAA:2026-09-29"]
        assert a["status"] == "confirmed" and a["bucket"] == "today" and "after the market closes" in a["what"]
        assert "$1.25" in a["watch"] and "new money" in a["why"]
        b = by["earn:BBB:2026-10-20"]
        assert b["status"] == "estimate" and b["bucket"] == "later" and "Tentative" in b["basis"]
        assert by["div:KO:2026-10-01"]["bucket"] == "week"
        assert by["macro:jobs:2026-10-02"]["source"]["url"].startswith("https://www.bls.gov")
        assert out["counts"]["today"] == 1

    def test_missing_dates_are_not_invented(self):
        rows = rows_for([("ZZZ", 1000, 0.1, "Tech", {})])
        out = ev.build_events(rows, now=NOW, policy={}, filings={}, articles=[],
                              calendar={"status": "expired", "events": []})
        assert out["upcoming"] == []

    def test_news_dedupes_stories_matches_holdings_and_drops_undated(self):
        arts = [
            {"title": "AAA Corp beats estimates as cloud grows", "source": "Wire A", "url": "https://a/1", "published": "2026-09-29T12:00:00+00:00"},
            {"title": "AAA Corp beats estimates as cloud grows fast", "source": "Wire B", "url": "https://b/1", "published": "2026-09-29T12:30:00+00:00"},
            {"title": "Unrelated company jumps", "source": "Wire A", "url": "https://a/2", "published": "2026-09-29T12:00:00+00:00"},
            {"title": "AAA Corp announces something", "source": "Wire A", "url": "https://a/3", "published": ""},
            {"title": "Old AAA Corp story", "source": "Wire A", "url": "https://a/4", "published": "2026-09-01T12:00:00+00:00"},
            {"title": "AAA Corp late-evening update", "source": "Wire D", "url": "https://d/1",
             "published": "2026-09-29T02:00:00+00:00"},
            {"title": "Fed officials signal patience on rates", "source": "Wire C", "url": "https://c/1", "category": "macro",
             "published": "2026-09-29T09:00:00+00:00"},
        ]
        out = self.build(articles=arts)
        news = [i for i in out["upcoming"] + out["recent"] if i["kind"] == "news"]
        titles = [n["title"] for n in news]
        assert sum("beats estimates" in t for t in titles) == 1
        assert not any("Unrelated" in t or "Old AAA" in t or "announces something" in t for t in titles)
        assert any("Fed officials" in t for t in titles)
        n = next(x for x in news if "beats" in x["title"])
        assert n["status"] == "reported" and n["symbols"] == ["AAA"] and n["tz"] == "UTC" and "not verified" in n["basis"]
        assert n["exposure"]["weight"] == pytest.approx(0.4) and n["bucket"] == "today"
        # 02:00 UTC on the 29th is the evening of the 28th in New York: not "today".
        late = next(x for x in news if "late-evening" in x["title"])
        assert late["bucket"] == "recent" and late["date"] == "2026-09-28"

    def test_short_tickers_need_a_cashtag_or_name(self):
        rows = rows_for([("KO", 1000, 0.1, "Staples", {})])
        hits = ev.match_articles([{"title": "Rally in KO shares"}, {"title": "$KO rises"}, {"title": "Koala rescue"}], rows)
        assert list(hits) == [1]

    def test_sec_filings_use_official_item_text(self):
        f = {"AAA": {"status": "ok", "filings": [{"form": "8-K", "filed": "2026-09-29", "accepted": "2026-09-29T20:05:00.000Z",
                                                  "items": ["2.02", "9.01"], "accession": "0001-26-000001",
                                                  "url": "https://www.sec.gov/Archives/x"}]}}
        out = self.build(filings=f)
        item = next(i for i in out["upcoming"] + out["recent"] if i["kind"] == "filing")
        assert "Results of operations" in item["what"] and item["material"] and item["source"]["name"] == "SEC EDGAR"


class TestSources:
    def test_edgar_parses_recent_and_handles_funds_and_failures(self):
        tickers = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
        subs = {"filings": {"recent": {"form": ["8-K", "4", "10-Q"], "filingDate": ["2026-09-25", "2026-09-25", "2026-08-01"],
                                       "accessionNumber": ["0000320193-26-000001", "x", "y"],
                                       "primaryDocument": ["a.htm", "b.xml", "c.htm"], "items": ["5.02,9.01", "", ""],
                                       "acceptanceDateTime": ["2026-09-25T21:00:00.000Z", None, None]}}}

        def get(url):
            if url.endswith("company_tickers.json"):
                return tickers
            if "CIK0000320193" in url:
                return subs
            raise OSError("down")
        c = EdgarClient(get)
        r = c.recent_filings("AAPL", date(2026, 9, 1))
        assert r["status"] == "ok" and [f["form"] for f in r["filings"]] == ["8-K"]
        assert r["filings"][0]["items"] == ["5.02", "9.01"] and r["filings"][0]["url"].endswith("/000032019326000001/a.htm")
        assert c.recent_filings("VTI", date(2026, 9, 1))["status"] == "no_cik"
        bad = EdgarClient(lambda u: (_ for _ in ()).throw(OSError("down")))
        assert bad.recent_filings("AAPL", date(2026, 9, 1))["status"] == "error"

    def test_official_calendar_reports_staleness(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"retrieved_on": "2026-01-01", "events": [{"kind": "x", "title": "t", "date": "2026-02-01"}]}))
        assert load_official_calendar(p, date(2026, 9, 29))["status"] == "expired"
        assert load_official_calendar(tmp_path / "none.json")["status"] == "missing"
        shipped = load_official_calendar(today=date(2026, 9, 29))
        assert shipped["status"] == "ok" and shipped["events"]


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def plan_rows():
    rows = rows_for([("BIG", 5000, 0.50, "Technology", {"tax_lots": [{"quantity": 5, "cost_per_share": 100, "acquired_on": "2024-01-01"}]}),
                     ("OK1", 1000, 0.10, "Health", {}), ("OK2", 1000, 0.10, "Energy", {})])
    rows[1]["guidance"].update(action="Accumulate", conviction=75)
    rows[2]["guidance"].update(action="Hold", conviction=55)
    return rows


OVERVIEW = {"cash": 3000.0, "buying_power": 3000.0, "total_value": 10000.0}
POLICY = PortfolioPolicy()


class TestPlan:
    def test_profile_is_validated_not_defaulted(self):
        p = pl.clean_profile({"contribution": "500", "horizon_years": -3, "risk_tolerance": "yolo",
                              "max_position": 5, "exclusions": ["tsla", " "], "fractional": False})
        assert p["contribution"] == 500 and "horizon_years" not in p and "risk_tolerance" not in p
        assert "max_position" not in p and p["exclusions"] == ["TSLA"] and p["fractional"] is False
        st = pl.profile_status(p)
        assert not st["complete"] and "risk tolerance" in st["missing"]

    def test_contribution_goes_only_to_eligible_names_within_limits_and_leaves_cash_when_none(self):
        prof = pl.clean_profile({"contribution": 1000, "liquidity_reserve": 500, "horizon_years": 10, "risk_tolerance": "moderate"})
        out = pl.build_scenarios(plan_rows(), OVERVIEW, POLICY, prof, [], True)
        c = next(s for s in out["scenarios"] if s["key"] == "contribution")
        assert [m["symbol"] for m in c["moves"]] == ["OK1"]               # only the Accumulate name
        m = c["moves"][0]
        # OK1 is 10% of 10,000 = at the 10% cap before money; after +1000 total (11,000) room = 1,100-1,000 = 100
        assert m["amount"] == pytest.approx(100.0)
        assert c["leftover"] == pytest.approx(3000 + 1000 - 100)
        assert c["after"]["cash_weight"] == pytest.approx(3900 / 11000)

    def test_exclusion_removes_a_target_and_keep_cash_is_stated(self):
        prof = pl.clean_profile({"contribution": 1000, "exclusions": ["OK1"]})
        out = pl.build_scenarios(plan_rows(), OVERVIEW, POLICY, prof, [], True)
        c = next(s for s in out["scenarios"] if s["key"] == "contribution")
        assert c["moves"] == [] and "cash" in c["note"].lower()

    def test_whole_share_mode_rounds_down(self):
        rows = plan_rows()
        rows[1]["broker"]["last_price"] = 37.0
        prof = pl.clean_profile({"contribution": 1000, "fractional": False, "max_position": 0.5})
        out = pl.build_scenarios(rows, OVERVIEW, POLICY, prof, [], True)
        m = next(s for s in out["scenarios"] if s["key"] == "contribution")["moves"][0]
        assert m["shares"] == int(m["shares"]) and m["amount"] == pytest.approx(m["shares"] * 37.0)

    def test_rebalance_sells_to_the_limit_and_is_blocked_on_unreconciled_data(self):
        prof = pl.clean_profile({})
        out = pl.build_scenarios(plan_rows(), OVERVIEW, POLICY, prof, [], True)
        r = next(s for s in out["scenarios"] if s["key"] == "rebalance")
        sell = next(m for m in r["moves"] if m["action"] == "Sell")
        assert sell["symbol"] == "BIG" and sell["amount"] == pytest.approx(4000.0)   # 5000 -> 10% of 10,000
        assert r["funding"][0]["label"] == "Sale proceeds"
        blocked = pl.build_scenarios(plan_rows(), OVERVIEW, POLICY, prof, [], False)
        assert next(s for s in blocked["scenarios"] if s["key"] == "rebalance")["status"] == "blocked"

    def test_no_contribution_means_needs_input_not_a_guess(self):
        out = pl.build_scenarios(plan_rows(), OVERVIEW, POLICY, pl.clean_profile({}), [], True)
        assert next(s for s in out["scenarios"] if s["key"] == "contribution")["status"] == "needs_input"


def cand_analysis(**over):
    a = {"decision": {"action": "BUY", "stance": "BULLISH", "new_money": "ELIGIBLE", "score": 72, "risk_grade": "B",
                      "blockers": [], "invalidation": ["Thesis breaks if margins fall"], "levels": {"stop": 90.0, "reference_price": 100.0},
                      "sizing": {"position_value": 800.0}, "evidence": {"status": "UNTESTED", "permits_recommendation": False},
                      "freshness": {"status": "fresh"}},
         "sector": "Utilities", "current_price": 100.0, "company_name": "Cand Inc",
         "fundamental": {"quality_score": 70, "debt_to_equity": 0.4, "pe_ratio": 15.0},
         "valuation": {"valuation_label": "fairly_valued"}, "thesis": {"summary": "s", "bull_case": ["b1"], "bear_case": ["bear1"]},
         "risk": {"risk_grade": "B"}, "catalysts": {"earnings_date": "2026-11-01"}, "risk_warnings": ["Regulatory risk"],
         "analysis_timestamp": "2026-09-29T10:00:00+00:00", "data_provider": "yfinance"}
    a.update(over)
    return a


class TestCandidates:
    def ev(self, analysis=None, error=None, profile=None, corr=None, sym="CAND"):
        prof = pl.clean_profile(profile if profile is not None else {
            "contribution": 500, "liquidity_reserve": 100, "horizon_years": 10, "risk_tolerance": "moderate"})
        metrics = {"sectors": [{"sector": "Technology", "weight": 0.5}, {"sector": "Utilities", "weight": 0.02}], "stops": {}}
        return pl.evaluate_candidate(sym, analysis, error, plan_rows(), metrics, OVERVIEW, POLICY, prof, date(2026, 9, 29), corr)

    def test_suitable_addition_carries_thesis_risk_counterargument_and_invalidation(self):
        c = self.ev(cand_analysis())
        assert c["verdict"]["code"] == "suitable" and c["good_company"]["value"]
        assert c["counterargument"] == "bear1" and c["principal_risk"] == "Regulatory risk"
        assert "stop" in c["invalidation"][0].lower() and c["max_position_value"] <= 800.0
        assert c["evidence"]["confidence"] == "low" and c["evidence"]["as_of"]
        assert c["conditional"] is False

    def test_good_company_is_not_the_same_as_suitable_now(self):
        a = cand_analysis()
        a["decision"] = dict(a["decision"], new_money="WATCH")
        c = self.ev(a)
        assert c["verdict"]["code"] == "good_not_now" and c["good_company"]["value"]
        assert c["max_position_value"] is None                       # no size unless suitable

    def test_duplicate_exposure_and_exclusions_and_failures(self):
        assert self.ev(cand_analysis(), corr={"BIG": 0.9})["verdict"]["code"] == "duplicates"
        assert self.ev(cand_analysis(), profile={"exclusions": ["CAND"]})["verdict"]["code"] == "excluded"
        f = self.ev(None, {"title": "no data"})
        assert f["verdict"]["code"] == "insufficient" and f["unavailable"]

    def test_risk_grade_above_tolerance_holds_back(self):
        a = cand_analysis()
        a["decision"] = dict(a["decision"], risk_grade="D")
        c = self.ev(a, profile={"risk_tolerance": "conservative", "contribution": 1, "liquidity_reserve": 0, "horizon_years": 5})
        assert c["verdict"]["code"] == "good_not_now" and "Risk grade D" in c["verdict"]["text"]

    def test_missing_profile_makes_ideas_conditional(self):
        assert self.ev(cand_analysis(), profile={})["conditional"] is True

    def test_suitable_candidate_can_receive_a_purchase(self):
        c = self.ev(cand_analysis())
        prof = pl.clean_profile({"contribution": 1000, "liquidity_reserve": 0})
        out = pl.build_scenarios(plan_rows(), OVERVIEW, POLICY, prof, [c], True)
        buys = next(s for s in out["scenarios"] if s["key"] == "contribution")["moves"]
        assert "CAND" in [m["symbol"] for m in buys]


# ---------------------------------------------------------------------------
# Robinhood contract: earnings window, confirmation, dividends, no-call causes
# ---------------------------------------------------------------------------

class TestRobinhoodEarningsContract:
    def test_window_is_requested_only_when_declared_and_verification_is_read(self):
        import test_my_portfolio as t
        tools = [x for x in t.DEFAULT_TOOLS if x["name"] not in ("get_earnings_calendar", "get_equity_fundamentals")] + [
            t.tool("get_earnings_calendar", ["start_date", "days", "filter"]),
            t.tool("get_equity_fundamentals", ["symbols"], ["symbols"])]
        fake = t.FakeRobinhood(tools=tools)
        orig = fake.payload

        def payload(name, args):
            if name == "get_earnings_calendar":
                return {"data": {"results": [
                    {"symbol": "AAPL", "report": {"date": "2099-01-01", "verified": False, "timing": "pm"}, "eps": {"actual": None, "estimate": "1.50"}},
                    {"symbol": "AAPL", "report": {"date": "2098-06-01", "verified": True}, "eps": {"actual": "1.2", "estimate": "1.1"}}]}}
            if name == "get_equity_fundamentals":
                return {"results": [{"symbol": "AAPL", "sector": "Technology", "ex_dividend_date": "2099-02-01",
                                     "dividend_per_share": "0.26", "payable_date": "2099-02-15"}]}
            return orig(name, args)
        fake.payload = payload
        prov, fake = t.connected_provider(fake)
        pf = prov.load_portfolio(next(a for a in prov.list_accounts() if a.can_trade_via_agent is False).handle)
        cal = [a for n, a in fake.calls if n == "get_earnings_calendar"]
        assert cal and cal[0]["days"] == 31 and "start_date" in cal[0] and "filter" not in cal[0]
        a = next(p for p in pf.positions if p.symbol == "AAPL")
        # A reported quarter (eps.actual set) is not "upcoming", even though its date is later than today.
        assert (a.earnings_date, a.earnings_verified, a.earnings_timing, a.eps_estimate) == ("2099-01-01", False, "pm", 1.5)
        assert (a.ex_dividend_date, a.dividend_per_share, a.payable_date) == ("2099-02-01", 0.26, "2099-02-15")

    def test_undeclared_window_inputs_are_not_sent(self):
        import test_my_portfolio as t
        prov, fake = t.connected_provider()
        prov.load_portfolio(next(a for a in prov.list_accounts() if a.can_trade_via_agent is False).handle)
        cal = [a for n, a in fake.calls if n == "get_earnings_calendar"]
        assert cal and "days" not in cal[0] and "start_date" not in cal[0]


class TestNoCallCause:
    def test_each_cause_names_its_remedy_and_never_reconnect(self):
        from portfolio.models import Position
        from portfolio.service import _no_call_cause
        p = Position("AAA", 1, last_price=10)
        assert _no_call_cause(Position("A", 1), {}, {}, None, None)["code"] == "NO_QUOTE"
        assert _no_call_cause(p, {"status": "delayed", "text": "25 min old"}, {}, None, None)["remedy"] == "wait"
        assert _no_call_cause(p, {"status": "live"}, {"state": "running"}, None, None)["code"] == "ANALYSIS_PENDING"
        err = _no_call_cause(p, {"status": "live"}, {"state": "error", "error": {"title": "rate limited", "retryable": False}}, None, None)
        assert err["code"] == "ANALYSIS_FAILED" and err["remedy"] == "none"
        assert _no_call_cause(p, {"status": "live"}, {"state": "done"}, {"x": 1}, {"action": "INVALID"})["code"] == "DECISION_INVALID"
        # No cause offers reconnecting the broker as its remedy.
        assert err["remedy"] != "reconnect"


class TestActivity:
    def provider(self):
        import test_my_portfolio as t
        tools = [x for x in t.DEFAULT_TOOLS if x["name"] != "get_realized_pnl"] + [
            t.tool("get_realized_pnl", ["account_number", "span", "start_date", "end_date", "timezone"], ["account_number"])]
        fake = t.FakeRobinhood(tools=tools)
        orig = fake.payload

        def payload(name, args):
            if name == "get_realized_pnl" and ("span" in args or "start_date" in args):
                return {"data": {"window": args.get("span", "custom"), "total_returns": "120.50", "total_rate_of_return": "4.1",
                                 "display_currency": "USD", "data_points": [
                                     {"start_time": "2026-09-01T00:00:00-04:00", "end_time": "2026-09-02T00:00:00-04:00",
                                      "realized_gain": "150.50", "rate_of_realized_gain": "5", "number_of_trades": 2},
                                     {"start_time": "2026-09-02T00:00:00-04:00", "end_time": "2026-09-03T00:00:00-04:00",
                                      "realized_gain": None, "rate_of_realized_gain": None, "number_of_trades": 0},
                                     {"start_time": "2026-09-03T00:00:00-04:00", "end_time": "2026-09-04T00:00:00-04:00",
                                      "realized_gain": "-30", "rate_of_realized_gain": "-1", "number_of_trades": 1}]}}
            return orig(name, args)
        fake.payload = payload
        prov, fake = t.connected_provider(fake)
        handle = next(a for a in prov.list_accounts() if a.can_trade_via_agent is False).handle
        return prov, fake, handle

    def test_span_and_ytd_windows_are_sent_as_declared_and_unknown_buckets_stay_unknown(self):
        prov, fake, handle = self.provider()
        r = prov.load_realized_series(handle, "month")
        call = [a for n, a in fake.calls if n == "get_realized_pnl"][-1]
        assert call["span"] == "month" and call["account_number"] == "12345678" and "start_date" not in call
        assert r["total"] == 120.5 and [b["gain"] for b in r["buckets"]] == [150.5, None, -30.0]
        prov.load_realized_series(handle, "ytd")
        call = [a for n, a in fake.calls if n == "get_realized_pnl"][-1]
        assert call["start_date"].endswith("-01-01") and "end_date" in call and "span" not in call
        with pytest.raises(Exception):
            prov.load_realized_series(handle, "fortnight")

    def test_bucket_times_keep_their_zone(self):
        """Robinhood sends RFC 3339 bucket bounds (live: UTC, "...Z"). Cutting them to 19
        characters dropped the zone, so New York midnight (05:00Z) read as 05:00 in New York."""
        prov, _, handle = self.provider()
        r = prov.load_realized_series(handle, "month")
        assert [b["start"] for b in r["buckets"]] == ["2026-09-01T00:00:00-04:00", "2026-09-02T00:00:00-04:00",
                                                      "2026-09-03T00:00:00-04:00"]
        assert r["buckets"][-1]["end"] == "2026-09-04T00:00:00-04:00"

    def test_insights_activity_statistics_never_count_unknown_as_zero(self):
        from portfolio.insights import Insights, MarketSources
        prov, _, handle = self.provider()

        class Svc:
            provider = prov
            _account = handle
        out = Insights(Svc(), MarketSources()).activity("month")
        st = out["stats"]
        assert out["available"] and st["buckets"] == 3 and st["unknown_buckets"] == 1
        assert (st["positive"], st["negative"], st["trades"]) == (1, 1, 3)
        assert st["best"]["gain"] == 150.5 and st["worst"]["gain"] == -30.0
        assert st["per_trade"] == pytest.approx(120.5 / 3)
        assert "Realized results only" in out["note"]
        assert Insights(Svc(), MarketSources()).activity("bogus")["available"] is False


def test_recent_paths_skip_gaps_and_report_the_window():
    df = closes(n=40)
    df.loc[df.index[-3], "BBB"] = np.nan
    out = hs.recent_paths(df, ["AAA", "BBB", "ZZZ"], 22)
    assert set(out) == {"AAA"}
    a = out["AAA"]
    assert len(a["values"]) == 22 and a["end"] == df.index[-1].isoformat()
    assert a["change"] == pytest.approx(df["AAA"].iloc[-1] / df["AAA"].iloc[-22] - 1)
