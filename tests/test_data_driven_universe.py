"""Audit B-87: the US scan universes come from data, never from ticker lists
typed into the engine, and fail closed when no data source can be read.

* S&P 500, NASDAQ-100, Dow 30, S&P 400 and S&P 600 come from their live
  constituent tables, else the dated snapshot (engine/index_snapshot.json),
  else nothing: a stated cause, never a hand-typed fallback list.
* Quick ranks that index pool; if ranking returns nothing it spreads evenly
  across the same pool, never a curated seed.
* Pre-Momentum (semis / AI infrastructure) is the index names whose GICS
  sub-industry is in UNIVERSE.PRE_MOMENTUM_SUB_INDUSTRIES, momentum-screened,
  sized by UNIVERSE.US_SCAN_LIMITS; without sub-industry data it is empty.
* The ETF Sleeve reads the Nasdaq Trader symbol directory (ETF = Y, not a test
  issue), drops leveraged / inverse products, and is empty if it cannot be read.

Synthetic constituents only (tests/_index_fixture.py); no network.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import SP_ELITE_PRO as sp
from engine import thresholds as T

import _index_fixture as fx

CORE = Path(sp.__file__).resolve().parent


def _system():
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj.market_data = sp.MarketDataProvider()
    obj._universe_selection = {"method": None}
    return obj


@pytest.fixture
def no_ranking(monkeypatch):
    """Liquidity ranking keeps the given order (no bulk download in tests)."""
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "smart_prefilter",
                        lambda self, tickers, max_candidates=200, **_: list(tickers)[:max_candidates])


def test_no_hand_typed_ticker_list_feeds_a_us_scan():
    tree = ast.parse((CORE / "SP_ELITE_PRO.py").read_text(encoding="utf-8"))
    for name in ("PRE_MOMENTUM_UNIVERSE",):
        assert not any(isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets)
                       for n in tree.body), name
    gone = {"get_trending_stocks", "get_russell2000_expanded", "get_midcap_growth_stocks",
            "get_international_stocks", "get_sector_leaders", "_get_fallback_sp500",
            "_get_fallback_nasdaq100", "_get_comprehensive_sp500_list"}
    md = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MarketDataProvider")
    present = {f.name for f in md.body if isinstance(f, ast.FunctionDef)} & gone
    assert not present, present
    # No US source method returns a literal list of tickers.
    for f in md.body:
        if isinstance(f, ast.FunctionDef) and f.name in {"get_complete_sp500", "get_sp500_tickers",
                                                         "get_nasdaq100_tickers", "get_dow30_tickers",
                                                         "get_sp400_midcap_tickers", "get_sp600_smallcap_tickers"}:
            lists = [n for n in ast.walk(f) if isinstance(n, ast.List) and len(n.elts) >= 5]
            assert not lists, f.name


def test_live_sources_build_the_universe_and_say_where_it_came_from(monkeypatch, no_ranking):
    fx.install(monkeypatch)
    obj = _system()
    uni = obj.build_us_stock_universe("full")
    assert set(uni) == set(fx.SP500) | set(fx.NASDAQ) | set(fx.DOW) | set(fx.SP400) | set(fx.SP600)
    sources = obj._universe_selection["index_sources"]
    assert {k: v["source"] for k, v in sources.items()} == {
        "sp500": "live", "nasdaq100": "live", "dow30": "live", "sp400": "live", "sp600": "live"}


def test_a_dead_source_without_a_snapshot_adds_nothing_and_states_why(monkeypatch, no_ranking):
    fx.install(monkeypatch, fail={"sp500", "nasdaq100", "dow30"}, snapshot=False)
    obj = _system()
    uni = obj.build_us_stock_universe("full")
    assert set(uni) == set(fx.SP400) | set(fx.SP600), "no hand-typed S&P 500 / NASDAQ-100 / Dow fallback"
    src = obj._universe_selection["index_sources"]
    assert src["sp500"]["source"] == "none" and "could not be read" in src["sp500"]["cause"]
    assert obj.market_data.last_sp500_source["index_source"] == "none"


def test_with_every_source_dead_and_no_snapshot_the_scan_is_empty(monkeypatch, no_ranking):
    fx.install(monkeypatch, fail={"sp500", "nasdaq100", "dow30", "sp400", "sp600"}, snapshot=False)
    for scan in ("quick", "standard", "full", "large_cap", "mid_cap", "small_cap", "pre_momentum"):
        obj = _system()
        assert obj.build_us_stock_universe(scan) == [], scan
        assert obj._universe_selection.get("cause"), scan


def test_quick_falls_back_to_an_even_spread_of_the_index_pool(monkeypatch):
    fx.install(monkeypatch)
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "smart_prefilter", lambda self, t, max_candidates=200, **_: [])
    obj = _system()
    uni = obj.build_us_stock_universe("quick")
    pool = set(fx.SP500) | set(fx.NASDAQ) | set(fx.DOW)
    assert len(uni) == T.UNIVERSE.US_SCAN_LIMITS["quick"] and set(uni) <= pool
    assert obj._universe_selection["method"] == "even_spread"


def test_pre_momentum_is_the_configured_sub_industries_momentum_screened(monkeypatch):
    fx.install(monkeypatch)
    seen = {}

    def screen(self, tickers, max_candidates=200, screen_style=None, **_):
        seen.update(pool=list(tickers), style=screen_style, cap=max_candidates)
        return list(tickers)
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "smart_prefilter", screen)
    monkeypatch.setattr(T.UNIVERSE, "US_SCAN_LIMITS", {**T.UNIVERSE.US_SCAN_LIMITS, "pre_momentum": 2})
    obj = _system()
    uni = obj.build_us_stock_universe("pre_momentum")
    assert set(seen["pool"]) == fx.SEMIS and seen["style"] == "momentum" and seen["cap"] == 2
    assert set(uni) <= fx.SEMIS
    assert "Semiconductors" in T.UNIVERSE.PRE_MOMENTUM_SUB_INDUSTRIES


def test_pre_momentum_without_sub_industry_data_is_empty_not_a_curated_list(monkeypatch, no_ranking):
    fx.install(monkeypatch, live_sub_industry=False)
    obj = _system()
    assert obj.build_us_stock_universe("pre_momentum") == []
    assert "sub-industry" in obj._universe_selection["cause"]


def test_the_etf_sleeve_reads_the_symbol_directory_and_drops_leveraged_funds(monkeypatch, no_ranking):
    fx.install(monkeypatch)
    obj = _system()
    uni = obj.build_us_stock_universe("focused_etf")
    assert len(uni) == T.UNIVERSE.US_SCAN_LIMITS["focused_etf"]
    assert set(uni) <= set(fx.ETFS)
    assert not set(uni) & set(fx.LEVERAGED), "leveraged / inverse products stay out"
    assert not set(uni) & set(fx.SP500[:5]) and "ZZTST" not in uni, "stocks and test issues stay out"
    assert all(sp.is_etf(t) for t in uni), "a directory ETF is classified as a fund"
    assert obj._universe_selection["method"] in {"liquidity_ranked", "etf_directory"}


def test_the_etf_sleeve_is_empty_when_the_directory_cannot_be_read(monkeypatch, no_ranking):
    fx.install(monkeypatch, fail={"us_etfs"}, snapshot=False)
    obj = _system()
    assert obj.build_us_stock_universe("focused_etf") == []
    assert "could not be read" in obj._universe_selection["cause"]


def test_the_cli_pre_momentum_choice_uses_the_same_builder():
    src = (CORE / "SP_ELITE_PRO.py").read_text(encoding="utf-8")
    assert "custom_tickers = list(PRE_MOMENTUM_UNIVERSE)" not in src
    assert "custom_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']" not in src
