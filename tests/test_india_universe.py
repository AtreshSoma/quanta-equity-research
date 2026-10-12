"""India scans read the current index constituents, and a name the provider
under-reports is still measured.

* Each India scan type reads NSE's constituent files (Sensex from Wikipedia),
  live or from the dated snapshot, never a hand-typed list (on 2026-10-06 the
  hand-typed "Nifty 50" had 8 names that had left the index and lacked 8 that
  had joined).
* NSE symbols keep their spelling (M&M, BAJAJ-AUTO) and the .NS suffix.
* Yahoo's .info can omit marketCap for a listed company while fast_info has it
  (RELIANCE.NS and TCS.NS on 2026-10-06); the scan uses that measurement
  instead of dropping the name as "could not be analysed".

Synthetic tables and providers only: no network.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import index_lists  # noqa: E402
from engine import thresholds as T  # noqa: E402

import test_scan_accounting as acc  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_index_cache():
    index_lists.clear_cache()
    yield
    index_lists.clear_cache()


def test_nse_symbols_keep_their_spelling_and_take_the_nse_suffix():
    assert index_lists._nse_symbol("M&M") == "M&M.NS"
    assert index_lists._nse_symbol(" bajaj-auto ") == "BAJAJ-AUTO.NS"
    assert index_lists._nse_symbol("ADANIPORTS.BO") == "ADANIPORTS.NS"
    assert index_lists._nse_symbol("RELIANCE.NS") == "RELIANCE.NS"
    assert index_lists._nse_symbol("??") == ""
    assert index_lists._nse_symbol("DUMMYHEG") == "", "a demerger placeholder is not a listing"


def test_the_nifty_50_is_read_from_nse_and_a_short_file_falls_back_to_the_snapshot():
    live = pd.DataFrame({"Company Name": ["x"] * 50, "Symbol": [f"N{i:02d}" for i in range(49)] + ["M&M"]})
    out = index_lists.constituents("nifty50", lambda url: [live])
    assert out["source"] == "live" and len(out["tickers"]) == 50 and "M&M.NS" in out["tickers"]
    assert "nseindia.com" in index_lists.INDEXES["nifty50"]["url"]
    index_lists.clear_cache()
    short = index_lists.constituents("nifty50", lambda url: [live.head(10)])
    assert short["source"] == "snapshot" and "parsed only 10 names" in short["cause"]
    assert len(short["tickers"]) >= T.UNIVERSE.INDEX_MIN_CONSTITUENTS["nifty50"]
    assert short["as_of"] == "2026-10-06", "each index keeps the date its snapshot was taken"
    assert all(t.endswith(".NS") for t in short["tickers"])


def test_the_snapshot_holds_every_india_index_a_scan_reads():
    data = json.load(open(index_lists.SNAPSHOT_PATH, encoding="utf-8"))
    keys = {k for keys in sp.QuantaEliteTradingSystem.INDIA_SCAN_INDEXES.values() for k in keys}
    for key in keys:
        assert len(data["indexes"][key]) >= T.UNIVERSE.INDEX_MIN_CONSTITUENTS[key], key


def test_each_india_scan_type_reads_its_indexes_and_records_the_source(monkeypatch):
    lists = {"nifty50": ["A.NS", "B.NS"], "sensex": ["A.NS", "S.NS"], "niftybank": ["K.NS"],
             "niftyit": ["I.NS"], "nifty500": ["A.NS", "B.NS", "S.NS", "K.NS", "I.NS", "Z.NS"]}
    labels = {"nifty50": "Nifty 50", "sensex": "BSE Sensex", "niftybank": "Nifty Bank", "niftyit": "Nifty IT", "nifty500": "Nifty 500"}
    read = []

    def fake(self, key):
        read.append(key)
        return {"key": key, "label": labels[key], "tickers": list(lists[key]),
                "source": "snapshot" if key == "sensex" else "live", "as_of": "2026-10-06", "cause": ""}
    monkeypatch.setattr(sp.MarketDataProvider, "get_index_constituents", fake)
    eng = sp.QuantaEliteTradingSystem(capital=200_000, market="INDIA")
    assert eng.build_indian_stock_universe("india_quick") == ["A.NS", "B.NS"]
    assert eng._universe_selection["pool_label"] == "Nifty 50 names"
    assert eng._universe_selection["index_source"] == "live"
    assert eng.build_indian_stock_universe("india_standard") == ["A.NS", "B.NS", "S.NS", "K.NS"]
    assert eng._universe_selection["index_source"] == "snapshot", "any list from the snapshot says so"
    assert eng.build_indian_stock_universe("india_sectoral") == ["A.NS", "B.NS", "K.NS", "I.NS"]
    assert len(eng.build_indian_stock_universe("india_comprehensive")) == 6
    assert set(read) == set(lists), "no hand-typed list is read"


def test_a_market_cap_missing_from_info_is_taken_from_fast_info(monkeypatch):
    """The name is measured, not dropped as 'could not be analysed'."""
    monkeypatch.setitem(acc._NAMES, "SYNM", ({}, dict(acc._GOOD, marketCap=None), True, False))
    eng = acc._scan(monkeypatch, {})
    assert "SYNM" not in (eng.scan_accounting()["could_not_analyse"].get("tickers") or {}).get("no_market_cap", [])
    measured = {r.ticker for r in eng.scan_results} | {r["ticker"] for r in eng.candidate_rejections()}
    assert "SYNM" in measured


def test_with_no_market_cap_anywhere_the_name_is_still_a_data_gap(monkeypatch):
    monkeypatch.setitem(acc._NAMES, "SYNM", ({}, dict(acc._GOOD, marketCap=None), True, False))
    monkeypatch.setattr(sp._ScannerTicker, "fast_market_cap", lambda self: None)
    eng = acc._scan(monkeypatch, {})
    assert "SYNM" in eng.scan_accounting()["could_not_analyse"]["tickers"]["no_market_cap"]
