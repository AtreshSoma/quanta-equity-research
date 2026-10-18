"""ETF Sleeve universe and the leveraged / inverse filter (2026-10-17).

* The directory lists ~5,700 ETFs, mostly tiny; the pre-filter downloads at
  most 1,000, so an even sample of the directory left out SPY, QQQ and the
  sector funds. The scan now starts from the most-traded ETFs.
* Plain inverse funds ("ProShares Short QQQ") passed the filter, while
  ultra-short bond funds ("JPMorgan Ultra-Short Income") were blocked.
"""
from __future__ import annotations

import pytest

import SP_ELITE_PRO as sp
from engine import thresholds as T


@pytest.mark.parametrize("ticker,name", [
    ("PSQ", "ProShares Short QQQ"),
    ("SH", "ProShares Short S&P500"),
    ("SJB", "ProShares Short High Yield"),
    ("TBF", "ProShares Short 20+ Year Treasury"),
    ("HDGE", "Ranger Equity Bear Bear ETF"),
    ("QID", "ProShares UltraShort QQQ"),
    ("XYZQ", "ProShares UltraPro Short QQQ"),
])
def test_inverse_and_leveraged_funds_are_excluded(ticker, name):
    assert sp.is_leveraged_or_inverse(ticker, {"longName": name})


@pytest.mark.parametrize("ticker,name", [
    ("JPST", "JPMorgan Ultra-Short Income ETF"),
    ("ICSH", "iShares Ultra Short Duration Bond Active ETF"),
    ("VCSH", "Vanguard Short-Term Corporate Bond ETF"),
    ("NEAR", "iShares Short Duration Bond Active ETF"),
    ("MINT", "PIMCO Enhanced Short Maturity Active Exchange-Traded Fund"),
    ("SMB", "VanEck Short Muni ETF"),
    ("SHYD", "VanEck Short High Yield Muni ETF"),
    ("FTLS", "First Trust Long/Short Equity"),
    ("CLSE", "Convergence Long/Short Equity ETF"),
    ("SPY", "State Street SPDR S&P 500 ETF Trust"),
])
def test_short_duration_bonds_and_long_short_funds_are_kept(ticker, name):
    assert not sp.is_leveraged_or_inverse(ticker, {"longName": name})


def _etf_system(monkeypatch, active, directory):
    sysm = object.__new__(sp.QuantaEliteTradingSystem)
    sysm._universe_selection = {}
    seen = {}

    class _MD:
        def get_us_etf_directory(self):
            return {"tickers": directory, "names": {}, "label": "dir", "source": "live", "as_of": "x", "cause": ""}

        def get_most_active_etfs(self):
            return active

    sysm.market_data = _MD()

    def broad(pool, limit, screen_style=None):
        seen["pool"] = list(pool)
        return list(pool)[:limit]

    monkeypatch.setattr(sysm, "_select_broad_universe", broad)
    return sysm, seen


def test_the_etf_sleeve_starts_from_the_most_traded_funds(monkeypatch):
    directory = [f"Z{i:04d}" for i in range(3000)] + ["SPY", "QQQ", "XLK", "PSQ"]
    active = {"tickers": ["SPY", "PSQ", "QQQ", "XLK", "NOTLISTED"],
              "names": {"PSQ": "ProShares Short QQQ"}, "source": "yahoo_screener", "cause": ""}
    sysm, seen = _etf_system(monkeypatch, active, directory)
    out = sysm._select_focused_universe([], "focused_etf", 3)
    # Inverse and unlisted names dropped; order is most traded first.
    assert out == ["SPY", "QQQ", "XLK"]
    assert seen["pool"] == ["SPY", "QQQ", "XLK"]
    assert sysm._universe_selection["method"] == "most_active_etfs"


def test_without_the_screener_the_directory_fallback_keeps_what_came_back(monkeypatch):
    directory = [f"Z{i:04d}" for i in range(3000)] + ["SPY"]
    active = {"tickers": ["SPY"], "names": {}, "source": "yahoo_screener", "cause": ""}
    sysm, seen = _etf_system(monkeypatch, active, directory)
    sysm._select_focused_universe([], "focused_etf", 80)
    pool = seen["pool"]
    assert pool[0] == "SPY" and len(pool) <= T.UNIVERSE.PREFILTER_MAX_TICKERS
    assert "ranked across the directory instead" in sysm._universe_selection["cause"]
