"""Synthetic index constituent sources for universe tests (audit B-87).

The US scans read their universes from live sources through
MarketDataProvider._read_index_tables (Wikipedia constituent tables, the
Nasdaq Trader symbol directory) with the dated snapshot as the only fallback.
Tests swap that reader for synthetic tables: made-up symbols, no network.
"""
from __future__ import annotations

import itertools
import string
from typing import Dict, Iterable, List, Optional

import pandas as pd

import SP_ELITE_PRO as sp
from engine import index_lists


def symbols(prefix: str, n: int) -> List[str]:
    """``n`` valid made-up tickers starting with ``prefix`` (letters only)."""
    width = 5 - len(prefix)
    combos = ("".join(c) for c in itertools.product(string.ascii_uppercase, repeat=width))
    return [prefix + c for c in itertools.islice(combos, n)]


SP500 = symbols("P", 503)
NASDAQ = SP500[:60] + symbols("N", 41)
DOW = SP500[:30]
SP400 = symbols("M", 400)
SP600 = symbols("S", 603)
SEMIS = {SP500[100], SP500[101], SP400[5], SP600[7], NASDAQ[-1]}


def _table(syms: Iterable[str], col: str = "Symbol", sub: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    syms = list(syms)
    frame = {col: syms, "Security": [f"{s} Inc." for s in syms]}
    if sub is not None:
        frame["GICS Sub-Industry"] = [sub.get(s, "Application Software") for s in syms]
    return pd.DataFrame(frame)


def etf_directory(etfs: Iterable[str], leveraged: Iterable[str] = (), stocks: Iterable[str] = (),
                  tests: Iterable[str] = ()) -> pd.DataFrame:
    rows = []
    for s in etfs:
        rows.append({"Symbol": s, "Security Name": f"{s} Sector Index ETF", "ETF": "Y", "Test Issue": "N"})
    for s in leveraged:
        rows.append({"Symbol": s, "Security Name": f"{s} Daily 3X Bull Shares ETF", "ETF": "Y", "Test Issue": "N"})
    for s in stocks:
        rows.append({"Symbol": s, "Security Name": f"{s} Inc. Common Stock", "ETF": "N", "Test Issue": "N"})
    for s in tests:
        rows.append({"Symbol": s, "Security Name": f"{s} Test ETF", "ETF": "Y", "Test Issue": "Y"})
    rows.append({"Symbol": "File Creation Time: 1008202614:00", "Security Name": "", "ETF": "", "Test Issue": ""})
    return pd.DataFrame(rows)


ETFS = symbols("E", 520)
LEVERAGED = symbols("L", 6)


def install(monkeypatch, *, fail: Iterable[str] = (), live_sub_industry: bool = True,
            snapshot: bool = True) -> Dict[str, int]:
    """Serve the synthetic sources; keys in ``fail`` raise like a dead page.
    Returns a per-URL call counter."""
    sub = {s: "Semiconductors" for s in SEMIS} if live_sub_industry else None
    by_key = {
        "sp500": [_table(SP500, sub=sub)],
        "nasdaq100": [pd.DataFrame({"Ticker": ["X"]}), _table(NASDAQ, col="Ticker", sub=sub)],
        "dow30": [_table(DOW)],
        "sp400": [_table(SP400, sub=sub)],
        "sp600": [_table(SP600, sub=sub)],
        "us_etfs": [etf_directory(ETFS, LEVERAGED, stocks=SP500[:5], tests=["ZZTST"])],
    }
    url_key = {str(spec["url"]): key for key, spec in index_lists.INDEXES.items()}
    calls: Dict[str, int] = {}
    failing = set(fail)

    def read(cls, url):
        key = url_key.get(url, url)
        calls[key] = calls.get(key, 0) + 1
        if key in failing or key not in by_key:
            raise ConnectionError(f"unreachable in tests: {key}")
        return [t.copy() for t in by_key[key]]

    monkeypatch.setattr(sp.MarketDataProvider, "_read_index_tables", classmethod(read))
    if not snapshot:
        monkeypatch.setattr(index_lists, "_snapshot", lambda key: ([], None))
    index_lists.clear_cache()
    return calls
