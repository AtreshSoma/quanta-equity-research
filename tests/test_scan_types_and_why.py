"""Scan types do what their labels say, and every rated name says why.

* Mid-Cap, Small-Cap and Dividend Aristocrats read their index's current
  constituents (or the dated snapshot), not hand-typed lists.
* Growth, Value and Dividend rate only names that pass a fundamental screen.
* ETF Sleeve scores a fund on trend and momentum, with no earnings size cut.
* The evidence the scan reads (analyst consensus, catalysts, news, earnings
  date) is kept on the result and turned into "why this stock was picked".

Synthetic providers and tables only: no network.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import index_lists, style_screen, why_picked  # noqa: E402
from engine import thresholds as T  # noqa: E402

import test_scan_accounting as acc  # noqa: E402


# ── Index lists ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_index_cache():
    index_lists.clear_cache()
    yield
    index_lists.clear_cache()


def _table(col, n):
    return [pd.DataFrame({col: [f"S{i:03d}" for i in range(n)] + ["BRK.B"]})]


def test_a_full_live_list_is_used_and_cached():
    calls = []

    def read(url):
        calls.append(url)
        return _table("Symbol", 420)
    out = index_lists.constituents("sp400", read, now=1000.0)
    assert out["source"] == "live" and len(out["tickers"]) == 421 and "BRK-B" in out["tickers"]
    again = index_lists.constituents("sp400", read, now=1001.0)
    assert again["tickers"] == out["tickers"] and len(calls) == 1


def test_a_short_or_failed_live_list_falls_back_to_the_dated_snapshot():
    short = index_lists.constituents("sp600", lambda url: _table("Symbol", 40))
    assert short["source"] == "snapshot" and "parsed only 41 names" in short["cause"]
    assert len(short["tickers"]) >= T.UNIVERSE.INDEX_MIN_CONSTITUENTS["sp600"]
    index_lists.clear_cache()

    def boom(url):
        raise TimeoutError("synthetic")
    failed = index_lists.constituents("aristocrats", boom)
    assert failed["source"] == "snapshot" and "could not be read" in failed["cause"]
    assert failed["as_of"]


def test_the_snapshot_holds_every_index_a_scan_reads():
    """Every snapshot entry meets its floor, and the cap-focus scans have one.
    Indexes without an entry yet (filled by `python -m engine.index_lists` on
    a machine that can reach the sources) fail closed: see
    test_data_driven_universe.py."""
    data = json.load(open(index_lists.SNAPSHOT_PATH, encoding="utf-8"))
    for key, floor in T.UNIVERSE.INDEX_MIN_CONSTITUENTS.items():
        if key in data["indexes"]:
            assert len(data["indexes"][key]) >= floor, key
    assert set(sp.QuantaEliteTradingSystem._INDEX_OF_SCAN.values()) <= set(data["indexes"])


def test_a_snapshot_refresh_takes_live_lists_and_never_shrinks_coverage(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"indexes": {"sp400": ["KEEP"]}, "as_of_by_index": {}}), encoding="utf-8")
    floor = int(T.UNIVERSE.INDEX_MIN_CONSTITUENTS["dow30"])

    def read(url):
        if "Dow_Jones" in url:
            return _table("Symbol", floor + 1)
        raise ConnectionError("down")
    report = index_lists.refresh_snapshot(read, ["dow30", "sp400"], path=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert report["dow30"] == "updated" and len(data["indexes"]["dow30"]) == floor + 2
    assert report["sp400"].startswith("kept") and data["indexes"]["sp400"] == ["KEEP"]
    index_lists.clear_cache()


def test_the_aristocrats_table_uses_its_own_column_name():
    out = index_lists.constituents("aristocrats", lambda url: _table("Ticker symbol", 60))
    assert out["source"] == "live" and len(out["tickers"]) == 61


def test_large_cap_reads_the_sp500_and_dow_and_keeps_10b_and_up():
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng._universe_selection = {"method": "listed_order"}
    eng._bounded_bulk_download = lambda *a: SimpleNamespace(status="error", error="synthetic", seconds=1.0, value=None)
    picked = eng._select_index_universe("large_cap", ["AAA", "BBB"], ["BBB", "CCC"], 120)
    assert sorted(picked) == ["AAA", "BBB", "CCC"]
    assert eng._universe_selection["pool_label"] == "S&P 500 and Dow names"
    assert sp.min_market_cap_for("large_cap") == T.UNIVERSE.SCAN_MIN_MARKET_CAP["large_cap"] == 10e9
    assert sp.min_market_cap_for("mid_cap") == T.UNIVERSE.MIN_MARKET_CAP


def test_the_dividend_scan_remembers_its_list_for_the_screen():
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    eng._universe_selection = {"method": "listed_order"}
    eng.market_data = SimpleNamespace(get_index_constituents=lambda key: {
        "label": "S&P 500 Dividend Aristocrats", "tickers": ["KO", "PG"], "source": "live", "as_of": "2026-10-05", "cause": ""})
    assert eng._select_index_universe("focused_dividend", [], [], 100) == ["KO", "PG"]
    assert eng._style_index_members == {"KO", "PG"}


# ── Style screens (pure) ────────────────────────────────────────────────────

def _fm(**kw):
    fm = sp.FundamentalMetrics()
    for k, v in kw.items():
        setattr(fm, k, v)
        fm.data_available[k] = True
    return fm


def test_growth_needs_revenue_growth_and_one_earnings_signal():
    ok = style_screen.screen("growth", _fm(revenue_growth=0.18, earnings_growth=0.02, pe_ratio=30, forward_pe=24))
    assert ok["passed"], "forward EPS above trailing is the second signal"
    slow = style_screen.screen("growth", _fm(revenue_growth=0.04, earnings_growth=0.4))
    assert not slow["passed"] and slow["reason"] == "revenue growth under the floor"
    flat = style_screen.screen("growth", _fm(revenue_growth=0.15, earnings_growth=0.01, pe_ratio=20, forward_pe=22))
    assert not flat["passed"]


def test_value_needs_profit_and_two_cheap_measures():
    cheap = _fm(forward_pe=12, ev_ebitda=14, pb_ratio=4, free_cash_flow=6e9, market_cap=100e9, pe_ratio=13)
    res = style_screen.screen("value", cheap)
    assert res["passed"], res
    assert [c["key"] for c in res["checks"] if c["ok"]] == ["profitable", "forward_pe", "fcf_yield"]
    pricey = style_screen.screen("value", _fm(forward_pe=35, ev_ebitda=25, pb_ratio=9, free_cash_flow=1e9,
                                              market_cap=200e9, pe_ratio=40))
    assert not pricey["passed"] and "cheap on 0 of 4" in pricey["reason"]
    loss = style_screen.screen("value", _fm(forward_pe=8, ev_ebitda=5, pb_ratio=1, free_cash_flow=-1e9,
                                            market_cap=10e9, pe_ratio=9))
    assert not loss["passed"] and loss["reason"] == "not profitable on earnings and free cash flow"


def test_dividend_needs_the_list_a_dividend_and_a_covered_payout():
    payer = _fm(dividend_yield=0.03, payout_ratio=0.6)
    assert style_screen.screen("dividend", payer, "Consumer Defensive", on_list=True)["passed"]
    assert not style_screen.screen("dividend", payer, "Consumer Defensive", on_list=False)["passed"]
    stretched = _fm(dividend_yield=0.05, payout_ratio=1.6)
    assert not style_screen.screen("dividend", stretched, "Utilities", on_list=True)["passed"]
    assert style_screen.screen("dividend", stretched, "Real Estate", on_list=True)["passed"], "REIT payout is not earnings-based"


# ── The engine applies them (real analyze_stock, synthetic providers) ──────

def _scan(monkeypatch, scan_type, names, extra=None):
    acc._install(monkeypatch)
    for name, info in (extra or {}).items():
        monkeypatch.setitem(acc._NAMES, name, ({}, info, True, False))
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type=scan_type, custom_tickers=list(names))
    return eng


def test_the_value_scan_rates_only_names_that_pass_its_screen(monkeypatch):
    rich = dict(acc._GOOD, forwardPE=40, trailingPE=45, priceToBook=12, freeCashflow=2e8, enterpriseToEbitda=30)
    eng = _scan(monkeypatch, "focused_value", ["SYNA", "SYNR"], {"SYNR": rich})
    rated = {r.ticker: r for r in eng.scan_results}
    assert "SYNR" not in rated
    assert eng._filter_stats.get("style_screen_failed") == 1
    assert rated["SYNA"].style_screen["passed"] and rated["SYNA"].style_screen["style"] == "value"


def test_the_growth_scan_drops_a_name_without_revenue_growth(monkeypatch):
    slow = dict(acc._GOOD, revenueGrowth=0.01)
    eng = _scan(monkeypatch, "focused_growth", ["SYNA", "SYNS"], {"SYNS": slow})
    assert [r.ticker for r in eng.scan_results] == ["SYNA"]


def test_a_broad_scan_applies_no_style_screen(monkeypatch):
    slow = dict(acc._GOOD, revenueGrowth=0.01)
    eng = _scan(monkeypatch, "custom", ["SYNA", "SYNS"], {"SYNS": slow})
    assert {r.ticker for r in eng.scan_results} == {"SYNA", "SYNS"}
    assert all(r.style_screen is None for r in eng.scan_results)


def test_a_fund_is_scored_on_trend_and_momentum_without_an_earnings_cut(monkeypatch):
    fund = dict(acc._GOOD, quoteType="ETF", marketCap=None)
    acc._install(monkeypatch)
    monkeypatch.setitem(acc._NAMES, "SYNETF", ({}, fund, False, False))     # no earnings calendar
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type="focused_etf", custom_tickers=["SYNETF"])
    rec = next(r for r in eng.scan_results if r.ticker == "SYNETF")
    assert rec.is_fund and rec.scored_factors == list(T.ETF.SCORED_FACTORS)
    assert (rec.fundamental_score, rec.quality_score, rec.value_score) == (0.0, 0.0, 0.0)
    assert rec.days_to_earnings is None
    assert eng._filter_stats.get("earnings_calendar_missing", 0) <= 1


def test_the_fund_composite_rescales_the_regime_weights():
    w = {"technical": 0.15, "fundamental": 0.25, "momentum": 0.20, "quality": 0.20, "value": 0.20}
    assert sp._fund_score({"technical": 7.0, "momentum": 5.0}, w) == pytest.approx((7 * 0.15 + 5 * 0.2) / 0.35)


def test_the_scan_keeps_the_analyst_consensus_it_read(monkeypatch):
    covered = dict(acc._GOOD, recommendationKey="buy", numberOfAnalystOpinions=31, recommendationMean=1.9)
    eng = _scan(monkeypatch, "custom", ["SYNQ"], {"SYNQ": covered})
    rec = eng.scan_results[0]
    assert (rec.analyst_rating, rec.analyst_count, rec.analyst_rec_mean) == ("buy", 31, 1.9)
    assert rec.analyst_target > 0
    d = rec.to_dict()
    assert d["analyst_count"] == 31 and "news" in d and "catalysts" in d


# ── Why this stock was picked ───────────────────────────────────────────────

def _rec(**over):
    rec = {
        "ticker": "SYNA", "entry_price": 100.0, "value_score": 7.2, "quality_score": 7.0, "momentum_score": 7.4,
        "technical_indicators": {"current_price": 100.0, "sma_50": 95.0, "sma_200": 88.0, "rsi": 58.0,
                                 "momentum_3m": 0.08, "momentum_6m": 0.15},
        "fundamental_metrics": {"forward_pe": 14.0, "ev_ebitda": 9.5, "free_cash_flow": 6e9, "market_cap": 100e9,
                                "revenue_growth": 0.14, "earnings_growth": 0.2, "roe": 0.25, "operating_margin": 0.3,
                                "debt_to_equity": 0.4, "data_available": {}},
        "analyst_rating": "buy", "analyst_count": 25, "analyst_target": 120.0,
        "revision_sentiment": "UPGRADING", "net_revisions_30d": 4, "eps_trend_pct_30d": 2.5,
        "catalysts": [{"type": "CONTRACT", "summary": "Synthetic wins $2B defense contract", "action": "BUY", "confidence": 0.8}],
        "news": [{"title": "Synthetic wins $2B defense contract", "publisher": "Wire", "published": "2026-10-03T12:00:00+00:00", "url": "https://example.com/x"}],
        "days_to_earnings": 40, "insider_sentiment": "NEUTRAL", "risk_grade": "B",
    }
    rec.update(over)
    return rec


def test_why_lists_measured_reasons_with_their_numbers():
    w = why_picked.build(_rec())
    kinds = [i["kind"] for i in w["for"]]
    assert kinds[:3] == ["catalyst", "analysts", "earnings"]
    assert len(w["for"]) <= T.WHY.MAX_ITEMS
    text = " ".join(i["text"] for i in w["for"])
    assert "Synthetic wins $2B defense contract" in text
    assert "consensus is Buy from 25 analysts; mean target $120.00 (+20.0% from the price)" in text
    assert "4 more upward than downward revisions in 30 days" in text
    assert w["summary"] == "A new contract catalyst, a Buy analyst consensus and rising earnings estimates."
    assert w["against"] == []
    assert w["news"][0]["url"] == "https://example.com/x"


def test_why_states_what_counts_against():
    w = why_picked.build(_rec(analyst_target=90.0, revision_sentiment="DOWNGRADING", days_to_earnings=5,
                              risk_grade="D", technical_indicators={"current_price": 100.0, "sma_50": 104.0,
                                                                    "sma_200": 110.0, "rsi": 74.0}))
    against = " ".join(i["text"] for i in w["against"])
    assert "mean target $90.00 is 10.0% below the price" in against
    assert "cutting earnings estimates" in against
    assert "Earnings are due in 5 days" in against
    assert "under its 200-day average" in against
    assert "RSI 74" in against


def test_why_says_nothing_it_did_not_measure():
    bare = {"ticker": "X", "technical_indicators": {}, "fundamental_metrics": {"data_available": {}}}
    w = why_picked.build(bare)
    assert w == {"summary": "", "for": [], "against": [], "news": [], "notes": []}
    thin = why_picked.build(_rec(analyst_count=1, catalysts=[], revision_sentiment="UNKNOWN"))
    assert "analysts" not in [i["kind"] for i in thin["for"]], "a consensus needs ANALYST_MIN_COUNT analysts"


def test_why_for_a_fund_skips_company_data_and_says_so():
    w = why_picked.build(_rec(is_fund=True, catalysts=[], analyst_rating="", revision_sentiment="UNKNOWN"))
    assert {i["kind"] for i in w["for"]} <= {"technical", "momentum"}
    assert w["notes"] and "trend and momentum only" in w["notes"][0]


def test_why_reads_the_research_fair_value_and_news_tone_when_measured():
    research = SimpleNamespace(
        valuation=SimpleNamespace(dcf_status="measured", dcf_fair_value=130.0, dcf_upside=30.0),
        sentiment=SimpleNamespace(measured=True, sentiment_label="POSITIVE", direct_news_count=6))
    w = why_picked.build(_rec(catalysts=[], analyst_rating=""), research)
    texts = " ".join(i["text"] for i in w["for"])
    assert "fair value is $130.00, 30.0% above the price" in texts or len(w["for"]) == T.WHY.MAX_ITEMS
    unmeasured = SimpleNamespace(valuation=SimpleNamespace(dcf_status="unavailable"),
                                 sentiment=SimpleNamespace(measured=False))
    assert not any(i["kind"] == "research" for i in why_picked.build(_rec(), unmeasured)["for"])


def test_a_style_scan_leads_with_the_screen_it_passed():
    screen = style_screen.screen("value", _fm(forward_pe=12, ev_ebitda=8, pb_ratio=4, free_cash_flow=6e9,
                                              market_cap=100e9, pe_ratio=13))
    w = why_picked.build(_rec(style_screen=screen, catalysts=[]))
    assert w["for"][0]["kind"] == "style"
    assert "forward P/E 12.0 (at most 16)" in w["for"][0]["text"]
    assert "valuation" not in [i["kind"] for i in w["for"]], "the screen already states the valuation"


# ── A fund's SEC registrant (ETF Sleeve event check) ────────────────────────

def test_a_fund_resolves_to_its_trust_and_a_failed_fund_list_stays_closed():
    from datetime import date
    from engine import corporate_events as ce
    from portfolio.sources import EdgarClient, SEC_FUND_TICKERS_URL, SEC_TICKERS_URL
    subs = {"name": "SELECT SECTOR SPDR TRUST", "filings": {"recent": {
        "form": [], "filingDate": [], "accessionNumber": [], "primaryDocument": [], "items": []}}}

    def get_json(url):
        if url == SEC_TICKERS_URL:
            return {"0": {"ticker": "SPY", "cik_str": 884394}}
        if url == SEC_FUND_TICKERS_URL:
            return {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[1064641, "S1", "C1", "XLK"]]}
        return subs
    client = EdgarClient(get_json=get_json)
    assert client.cik("SPY") == 884394, "the company list wins"
    assert client.cik("XLK") == 1064641, "a fund series resolves to its trust"
    ev = ce.check("XLK", client, date(2026, 10, 5))
    assert ev.status == "clear" and ev.registrant == "SELECT SECTOR SPDR TRUST"

    def no_fund_list(url):
        if url == SEC_FUND_TICKERS_URL:
            raise TimeoutError("synthetic")
        return get_json(url)
    closed = ce.check("XLK", EdgarClient(get_json=no_fund_list), date(2026, 10, 5))
    assert closed.status == "unavailable", "without the fund list the gate stays closed"


# ── Company news when yfinance returns none (Yahoo RSS) ─────────────────────

_REAL_RSS = sp._rss_news_items      # the conftest guard replaces it per test

_FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Synthetic wins $2B defense contract</title><link>https://finance.yahoo.com/x.html</link>
<pubDate>Mon, 05 Oct 2026 18:58:42 +0000</pubDate><description>Investing.com -- Synthetic Corp said...</description></item>
<item><title>Second headline</title><link>javascript:alert(1)</link><pubDate>bad date</pubDate><description>No source</description></item>
</channel></rss>"""


def test_the_rss_feed_parses_title_publisher_time_and_link():
    seen = []

    def get(url):
        seen.append(url)
        return SimpleNamespace(content=_FEED, raise_for_status=lambda: None)
    items = _REAL_RSS("SYNA", get=get)
    assert "s=SYNA" in seen[0]
    assert items[0] == {"title": "Synthetic wins $2B defense contract", "publisher": "Investing.com",
                        "published": "2026-10-05T18:58:42+00:00", "url": "https://finance.yahoo.com/x.html"}
    assert items[1]["publisher"] == "Yahoo Finance" and items[1]["url"] == "" and items[1]["published"] == ""


def test_company_news_falls_back_to_rss_once_per_name(monkeypatch):
    calls = []
    monkeypatch.setattr(sp, "_rss_news_items", lambda t: calls.append(t) or [{"title": f"{t} news", "publisher": "Wire",
                                                                             "published": "", "url": ""}])
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    stock = SimpleNamespace(news=[])
    assert eng._company_news("SYNA", stock)[0]["title"] == "SYNA news"
    eng._company_news("SYNA", stock)
    assert calls == ["SYNA"], "read once per name per scan"
    yf_items = SimpleNamespace(news=[{"title": "From yfinance", "link": "https://e.com/y"}])
    assert eng._company_news("SYNB", yf_items)[0]["title"] == "From yfinance" and calls == ["SYNA"]


def test_a_growth_scan_states_growth_once():
    screen = style_screen.screen("growth", _fm(revenue_growth=0.24, earnings_growth=0.33))
    kinds = [i["kind"] for i in why_picked.build(_rec(style_screen=screen))["for"]]
    assert kinds.count("style") == 1 and "growth" not in kinds


# ── Fixes: revision wording, news subject, valuation ───────────────────────

def test_revision_wording_matches_a_falling_average_estimate():
    """[Fix 7] Net upgrades with a lower average EPS estimate is not "rising"."""
    w = why_picked.build(_rec(catalysts=[], analyst_rating="", net_revisions_30d=18, eps_trend_pct_30d=-0.3))
    item = next(i for i in w["for"] if i["kind"] == "earnings")
    assert item["label"] == "Revisions mostly up"
    assert item["text"] == "18 more upward than downward revisions in 30 days, though the average forward EPS estimate moved -0.3%."
    assert "rising" not in w["summary"].lower()


def test_news_keeps_only_the_companys_own_stories():
    """[Fix 6] A name in passing or late in a list is someone else's story."""
    info = {"shortName": "Cisco Systems, Inc."}
    titles = ["LeapXpert Delivers Governed and Compliant Client Messaging to the Enterprise with Webex by Cisco",
              "Analyst Sells Cisco (CSCO) and Trims Meta (META) Amid Rising Yields",
              "Market Report 2026: Global Size and Forecast | Microsoft, Dell, Amazon, Cisco, and",
              "Cisco's quarterly results beat estimates",
              "Why CSCO stock moved today"]
    kept = [i["title"] for i in sp._news_about("CSCO", info, [{"title": t} for t in titles])]
    assert kept == titles[1:2] + titles[3:]


def test_valuation_puts_every_estimate_on_one_scale_and_says_why_they_differ():
    """[Fix 4] Analyst low/mean/high, the level target, DCF and price, one sentence."""
    from engine import valuation_view
    rec = {"analyst_count": 25, "analyst_target": 136.16, "analyst_target_low": 100.0, "analyst_target_high": 160.0}
    row = {"price": 112.20, "target": 111.91, "status": "watch", "horizon_days": 63}
    research = SimpleNamespace(valuation=SimpleNamespace(dcf_status="measured", dcf_fair_value=69.20))
    v = valuation_view.build(rec, row, research)
    assert [m["key"] for m in v["marks"]] == ["analyst_low", "analyst_mean", "analyst_high", "level_target", "dcf"]
    assert (v["low"], v["high"]) == (69.20, 160.0)
    assert v["sentence"] == ("The analyst mean ($136.16) is 97% above the DCF fair value ($69.20) because analyst "
                             "targets are 12-month views from the covering analysts, while the DCF value discounts "
                             "projected free cash flows to today.")
    assert v["missing"] == []
    thin = valuation_view.build({"analyst_count": 1, "analyst_target": 50}, {"price": 40, "target": 44, "status": "not_funded"})
    assert [m["key"] for m in thin["marks"]] == ["level_target"]
    assert thin["missing"] == ["No analyst targets: fewer than 3 analysts cover it.",
                               "No DCF value: the Research check runs only on Buy, Accumulate and Watchlist calls."]
    assert thin["sentence"].startswith("The level target $44.00 is 10.0% above the price")
    assert valuation_view.build({}, {"price": 0, "target": 0})["marks"] == []
