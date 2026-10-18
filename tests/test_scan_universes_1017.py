"""Scan-universe sources found broken on 2026-10-17 (offline fixtures).

* ETF Sleeve: the Nasdaq Trader directory is a pipe-delimited .txt file. It
  went to the HTML reader ("No tables found"), so the scan had no names.
* Dow 30: the Wikipedia page dropped its components table; its navigation box
  still names the companies, resolved to tickers through the S&P 500 table.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import SP_ELITE_PRO as sp
from engine import index_lists as L

DIRECTORY = (
    "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|"
    "Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
    "Y|SPY|SPDR S&P 500 ETF Trust|P| |Y|100|N||SPY|SPY|N\n"
    "Y|TQQQ|ProShares UltraPro QQQ|Q|G|Y|100|N|N||TQQQ|N\n"
    "Y|AAPL|Apple Inc. - Common Stock|Q|Q|N|100|N|N||AAPL|N\n"
    "Y|ZZZT|Test ETF|P| |Y|100|Y||ZZZT|ZZZT|N\n"
    "File Creation Time: 1017202600:00|||||||||||\n"
)


def test_the_etf_directory_text_file_is_read_as_a_table(monkeypatch):
    monkeypatch.setattr(sp.NetworkSession, "rate_limited_get",
                        staticmethod(lambda url, *a, **k: SimpleNamespace(text=DIRECTORY, raise_for_status=lambda: None)))
    tables = sp.MarketDataProvider._read_index_tables("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt")
    tickers, attrs = L._rows(tables, ("Symbol",), "etf_directory", {"name": "Security Name"})
    # ETFs only, no test issue, no stock, no footer line.
    assert tickers == ["SPY", "TQQQ"]
    assert attrs["name"]["SPY"] == "SPDR S&P 500 ETF Trust"


NAVBOX = pd.DataFrame({
    "vteComponents of the Dow Jones Industrial Average": [
        "3M Alphabet Apple Coca-Cola Disney Johnson & Johnson McDonald's Merck Travelers"],
    "vteComponents of the Dow Jones Industrial Average.1": [
        "3M Alphabet Apple Coca-Cola Disney Johnson & Johnson McDonald's Merck Travelers"],
})
SP500 = pd.DataFrame({
    "Symbol": ["MMM", "GOOGL", "GOOG", "AAPL", "AMAT", "KO", "DIS", "JNJ", "JCI", "MCD", "MRK", "TRV"],
    "Security": ["3M", "Alphabet Inc. (Class A)", "Alphabet Inc. (Class C)", "Apple Inc.", "Applied Materials",
                 "Coca-Cola Company (The)", "Walt Disney Company (The)", "Johnson & Johnson",
                 "Johnson Controls", "McDonald's", "Merck & Co.", "Travelers Companies (The)"],
    "GICS Sub-Industry": ["x"] * 12,
})


def test_dow_members_resolve_from_the_navigation_box(monkeypatch):
    # Small fixture tables: lower both floors (a short live list is otherwise
    # treated as a broken page).
    monkeypatch.setitem(L.UNIVERSE.INDEX_MIN_CONSTITUENTS, "dow30", 9)
    monkeypatch.setitem(L.UNIVERSE.INDEX_MIN_CONSTITUENTS, "sp500", 10)
    L.clear_cache()
    pages = {L.INDEXES["dow30"]["url"]: [pd.DataFrame({"Year": [2025]}), NAVBOX],
             L.INDEXES["sp500"]["url"]: [SP500]}
    out = L.constituents("dow30", lambda url: pages[url])
    L.clear_cache()
    assert out["source"] == "live"
    # Longest leading match wins (J&J, not Johnson Controls; Apple, not
    # Applied Materials); both Alphabet share classes are kept.
    assert sorted(out["tickers"]) == sorted(["MMM", "GOOGL", "GOOG", "AAPL", "KO", "DIS", "JNJ", "MCD", "MRK", "TRV"])


def test_a_navbox_that_does_not_resolve_falls_back_to_the_snapshot(monkeypatch):
    L.clear_cache()
    pages = {L.INDEXES["dow30"]["url"]: [pd.DataFrame({"Year": [2025]})],
             L.INDEXES["sp500"]["url"]: [SP500]}
    out = L.constituents("dow30", lambda url: pages[url])
    L.clear_cache()
    assert out["source"] == "snapshot" and len(out["tickers"]) >= 28
    assert "navigation box did not resolve" in out["cause"]


@pytest.mark.parametrize("key", ["sp500", "nasdaq100", "dow30", "us_etfs"])
def test_every_us_scan_source_has_an_offline_snapshot(key):
    tickers, as_of = L._snapshot(key)
    assert len(tickers) >= L.UNIVERSE.INDEX_MIN_CONSTITUENTS[key] and as_of


def test_custom_tickers_are_analysed_once_each(monkeypatch):
    """"AAPL, aapl" is one name: the scanner used to analyse and list it twice."""
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {})
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    seen = []
    real = eng.analyze_stock

    def counting(ticker, *a, **k):
        seen.append(ticker)
        return real(ticker, *a, **k)

    monkeypatch.setattr(eng, "analyze_stock", counting)
    eng.scan_market(scan_type="custom", custom_tickers=["SYNA", "syna", "SYNB", "SYNA "])
    assert sorted(seen) == ["SYNA", "SYNB"]
    assert eng._universe_selection["selected"] == 2


def test_the_route_deduplicates_custom_tickers(monkeypatch):
    import app as webapp
    started = {}
    # Own job store: the stub worker never finishes its job, and a job left
    # "running" would make the next test's scan request a 409.
    monkeypatch.setattr(webapp, "_jobs", {})
    monkeypatch.setattr(webapp, "_run_stock_picks", lambda job_id, params: started.update(params))

    class _NoThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(webapp.threading, "Thread", _NoThread)
    client = webapp.app.test_client()
    res = client.post("/api/stock-picks", headers={"X-QUANTA-Request": "1"},
                      json={"scan_type": "custom", "custom_tickers": ["aapl", "AAPL", "msft"], "capital": 2500})
    assert res.status_code == 202, res.get_json()
    assert started["custom_tickers"] == ["AAPL", "MSFT"]
