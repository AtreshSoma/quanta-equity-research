"""Index constituent lists for the cap-focus, dividend and India scans.

The Mid-Cap, Small-Cap and Dividend Aristocrats scans used hand-typed lists
that had drifted: S&P 500 banks and foreign gold miners in "S&P 400", large
caps and delisted symbols in "Russell 2000". Each scan now reads its index's
current constituents (the same Wikipedia tables the S&P 500 list comes from),
and falls back to the dated snapshot in ``index_snapshot.json`` when the page
cannot be read or parses short. The source used is returned with the list so
the page can say which one a scan ran on.

The US broad scans read the same way (audit B-87): S&P 500, NASDAQ-100 and Dow
30 from their Wikipedia tables, and the ETF Sleeve from the Nasdaq Trader symbol
directory (every exchange-listed security, with an ETF flag). An index with no
live list and no snapshot entry returns no names and the cause: a scan never
falls back to a hand-typed list. Where a table carries GICS sub-industries they
are returned too (``attrs["sub_industry"]``); the snapshot keeps symbols only.

India's lists come from NSE's own constituent files (niftyindices.com archive)
and, for the BSE Sensex, Wikipedia's table. Their symbols keep NSE spelling
(``M&M``, ``BAJAJ-AUTO``) and carry the ``.NS`` suffix the scanner reads.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from engine.thresholds import UNIVERSE

logger = logging.getLogger(__name__)

_SUB_INDUSTRY = {"sub_industry": "GICS Sub-Industry"}

INDEXES: Dict[str, Dict[str, object]] = {
    "sp500": {"label": "S&P 500",
              "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
              "columns": ("Symbol",), "attrs": {**_SUB_INDUSTRY, "name": "Security"}},
    "nasdaq100": {"label": "NASDAQ-100",
                  "url": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
                  "columns": ("Ticker", "Symbol"), "attrs": _SUB_INDUSTRY},
    # The Dow page dropped its components table (2026-10); its navigation box
    # still names the 30 companies, which are resolved to tickers through the
    # S&P 500 table's company names (every Dow member is an S&P 500 company).
    "dow30": {"label": "Dow 30",
              "url": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
              "columns": ("Symbol",), "navbox": "Components of the Dow Jones"},
    "sp400": {"label": "S&P 400 mid-cap",
              "url": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
              "columns": ("Symbol",), "attrs": _SUB_INDUSTRY},
    "sp600": {"label": "S&P 600 small-cap",
              "url": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
              "columns": ("Symbol",), "attrs": _SUB_INDUSTRY},
    # Every security traded on US exchanges, one row each, with an ETF flag.
    "us_etfs": {"label": "US-listed ETFs (Nasdaq Trader symbol directory)",
                "url": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt",
                "columns": ("Symbol",), "style": "etf_directory",
                "attrs": {"name": "Security Name"}},
    "aristocrats": {"label": "S&P 500 Dividend Aristocrats",
                    "url": "https://en.wikipedia.org/wiki/S%26P_500_Dividend_Aristocrats",
                    "columns": ("Ticker symbol", "Symbol", "Ticker")},
}

_NSE_CSV = "https://archives.nseindia.com/content/indices/ind_{}list.csv"
for _key, _file, _label in (("nifty50", "nifty50", "Nifty 50"), ("niftynext50", "niftynext50", "Nifty Next 50"),
                            ("nifty500", "nifty500", "Nifty 500"), ("niftybank", "niftybank", "Nifty Bank"),
                            ("niftyit", "niftyit", "Nifty IT"), ("nifty100", "nifty100", "Nifty 100"),
                            ("niftymidcap150", "niftymidcap150", "Nifty Midcap 150"),
                            ("niftysmallcap250", "niftysmallcap250", "Nifty Smallcap 250")):
    INDEXES[_key] = {"label": _label, "url": _NSE_CSV.format(_file), "columns": ("Symbol",), "style": "nse"}
INDEXES["sensex"] = {"label": "BSE Sensex", "url": "https://en.wikipedia.org/wiki/BSE_SENSEX",
                     "columns": ("Symbol",), "style": "nse"}

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "index_snapshot.json")

_cache: Dict[str, Tuple[float, Dict[str, object]]] = {}
_lock = threading.Lock()


def _nse_symbol(raw: str) -> str:
    """An NSE listing as the scanner reads it: ``M&M`` -> ``M&M.NS``; a BSE
    ``ADANIPORTS.BO`` from the Sensex table -> ``ADANIPORTS.NS``."""
    sym = raw.strip().upper()
    for suffix in (".BO", ".NS"):
        if sym.endswith(suffix):
            sym = sym[: -len(suffix)]
    if sym.startswith("DUMMY"):
        return ""      # NSE's placeholder for a company mid-demerger, not a listing
    return f"{sym}.NS" if sym and sym.replace("-", "").replace("&", "").isalnum() else ""


def _us_symbol(raw: str) -> str:
    sym = raw.strip().upper().replace(".", "-")
    return sym if sym and sym.replace("-", "").isalnum() else ""


def _rows(tables: List[pd.DataFrame], columns: Tuple[str, ...], style: str = "us",
          attrs: Optional[Dict[str, str]] = None) -> Tuple[List[str], Dict[str, Dict[str, str]]]:
    """Symbols (and the requested attribute columns) from the table that yields
    the most symbols for one of ``columns``: a page's constituent table, not a
    stray fragment or a recent-changes table that shares a column name."""
    best: Tuple[List[str], Dict[str, Dict[str, str]]] = ([], {})
    for table in tables:
        col = next((c for c in columns if c in table.columns), None)
        if col is None:
            continue
        if style == "etf_directory":
            # Nasdaq Trader directory: keep ETFs that are not test issues.
            flag = table.get("ETF", pd.Series("", index=table.index)).astype(str).str.strip().str.upper()
            test = table.get("Test Issue", pd.Series("N", index=table.index)).astype(str).str.strip().str.upper()
            table = table[(flag == "Y") & (test != "Y")]
        out: List[str] = []
        seen = set()
        found: Dict[str, Dict[str, str]] = {k: {} for k in (attrs or {})}
        for _, row in table.iterrows():
            raw = row.get(col)
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                continue
            sym = _nse_symbol(str(raw)) if style == "nse" else _us_symbol(str(raw))
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
            for key, attr_col in (attrs or {}).items():
                val = row.get(attr_col) if attr_col in table.columns else None
                if val is not None and not (isinstance(val, float) and pd.isna(val)) and str(val).strip():
                    found[key][sym] = str(val).strip()
        if len(out) > len(best[0]):
            best = (out, found)
    return best


def _symbols(tables: List[pd.DataFrame], columns: Tuple[str, ...], style: str = "us") -> List[str]:
    """Symbols from the constituent table that carries one of ``columns``."""
    return _rows(tables, columns, style)[0]


def _snapshot(key: str) -> Tuple[List[str], Optional[str]]:
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        as_of = (data.get("as_of_by_index") or {}).get(key) or data.get("as_of")
        return list(data.get("indexes", {}).get(key) or []), as_of
    except (OSError, ValueError) as exc:
        logger.warning("Index snapshot unreadable: %s", exc)
        return [], None


_NAME_NOISE = {"inc", "corp", "corporation", "company", "co", "the", "plc", "ltd", "limited",
               "holdings", "group", "class", "a", "b", "c", "incorporated", "international"}


def _name_key(name: str) -> List[str]:
    """Company name as comparable words: lower case, punctuation and legal
    suffixes removed ("The Walt Disney Company" -> ["walt", "disney"])."""
    words = re.sub(r"[^a-z0-9& ]+", " ", str(name).lower()).split()
    return [w for w in words if w not in _NAME_NOISE]


def _navbox_names(tables: List[pd.DataFrame], title: str) -> List[str]:
    """The entries of a Wikipedia navigation box whose heading starts with ``title``."""
    for table in tables:
        head = " ".join(str(c) for c in table.columns)
        if title.lower() not in head.lower():
            continue
        cells: List[str] = []
        for v in table.values.ravel():
            text = str(v).strip()
            if text and text.lower() != "nan" and title.lower() not in text.lower():
                cells.append(text)
        # The box renders its list as one space-separated cell, repeated.
        return cells
    return []


def _resolve_names(cell_text: str, by_name: Dict[str, str]) -> List[str]:
    """Tickers for the company names packed in a navbox cell, matched against
    ``by_name`` (ticker -> company name).

    Walks the cell's words; at each position the company whose name opens
    with the longest run of those words wins ("Johnson & Johnson" beats
    "Johnson Controls"). One leading word on the company side may be skipped
    ("Disney" matches "Walt Disney"). A tie keeps every share class of one
    company (Alphabet: GOOGL and GOOG).
    """
    names = {t: _name_key(n) for t, n in by_name.items()}
    words = _name_key(cell_text)
    found: List[str] = []
    i = 0
    while i < len(words):
        best_len, best = 0, []
        for t, nk in names.items():
            for skip in (0, 1):
                rest = nk[skip:]
                n = 0
                while n < len(rest) and i + n < len(words) and words[i + n] == rest[n]:
                    n += 1
                if n > best_len:
                    best_len, best = n, [t]
                elif n and n == best_len and t not in best:
                    best.append(t)
        if best_len:
            found.extend(t for t in best if t not in found)
            i += best_len
        else:
            i += 1
    return found


def _from_navbox(key: str, spec: Dict[str, object], read_tables: Callable[[str], List[pd.DataFrame]],
                 floor: int, now: float) -> Optional[Dict[str, object]]:
    """An index whose page lists its members only in a navigation box: the
    company names there, resolved to tickers through the S&P 500 table."""
    names = _navbox_names(read_tables(str(spec["url"])), str(spec["navbox"]))
    sp = constituents("sp500", read_tables, now)
    by_name = dict((sp.get("attrs") or {}).get("name") or {})
    if not names or not by_name:
        return None
    tickers = _resolve_names(names[0], by_name)
    if len(tickers) < floor:
        return None
    out = {"key": key, "label": spec["label"], "tickers": tickers, "source": "live",
           "as_of": time.strftime("%Y-%m-%d", time.localtime(now)),
           "cause": "", "attrs": {}, "method": "navbox names resolved through the S&P 500 table"}
    with _lock:
        _cache[key] = (now, out)
    return dict(out)


def constituents(key: str, read_tables: Callable[[str], List[pd.DataFrame]],
                 now: Optional[float] = None) -> Dict[str, object]:
    """``{"key", "label", "tickers", "source": live|snapshot|none, "as_of", "cause", "attrs"}``.

    A live list shorter than ``UNIVERSE.INDEX_MIN_CONSTITUENTS[key]`` means the
    page layout changed; it is discarded rather than scanned as if complete.
    """
    spec = INDEXES[key]
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < UNIVERSE.INDEX_CACHE_SECONDS:
            return dict(hit[1])
    floor = int(UNIVERSE.INDEX_MIN_CONSTITUENTS.get(key, 1))
    cause = ""
    try:
        tickers, attrs = _rows(read_tables(str(spec["url"])), tuple(spec["columns"]),  # type: ignore[arg-type]
                               str(spec.get("style") or "us"), dict(spec.get("attrs") or {}))  # type: ignore[arg-type]
        if len(tickers) >= floor:
            out = {"key": key, "label": spec["label"], "tickers": tickers, "source": "live",
                   "as_of": time.strftime("%Y-%m-%d", time.localtime(now)), "cause": "", "attrs": attrs}
            with _lock:
                _cache[key] = (now, out)
            return dict(out)
        cause = f"the live list parsed only {len(tickers)} names (at least {floor} expected)"
        if spec.get("navbox"):
            resolved = _from_navbox(key, spec, read_tables, floor, now)
            if resolved:
                return resolved
            cause += "; the navigation box did not resolve either"
    except Exception as exc:  # network, parse or layout failure
        cause = f"the live list could not be read ({str(exc)[:120]})"
    tickers, as_of = _snapshot(key)
    logger.warning("%s: %s; using the snapshot of %s", spec["label"], cause, as_of)
    return {"key": key, "label": spec["label"], "tickers": tickers,
            "source": "snapshot" if tickers else "none", "as_of": as_of, "cause": cause, "attrs": {}}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def refresh_snapshot(read_tables: Callable[[str], List[pd.DataFrame]],
                     keys: Optional[List[str]] = None, path: str = SNAPSHOT_PATH) -> Dict[str, object]:
    """Write the live lists into the dated snapshot (the offline fallback).

    Only a live list that meets its floor replaces a snapshot entry; anything
    else keeps the previous entry, so a refresh can never shrink coverage.
    Returns ``{key: "updated" | "kept: <cause>"}``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {"indexes": {}, "as_of_by_index": {}}
    data.setdefault("indexes", {})
    data.setdefault("as_of_by_index", {})
    report: Dict[str, object] = {}
    clear_cache()
    for key in keys or list(INDEXES):
        out = constituents(key, read_tables)
        if out["source"] == "live":
            data["indexes"][key] = list(out["tickers"])
            data["as_of_by_index"][key] = out["as_of"]
            report[key] = "updated"
        else:
            report[key] = f"kept: {out['cause']}"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return report


if __name__ == "__main__":  # python -m engine.index_lists [key ...]  (from core/)
    import sys

    from SP_ELITE_PRO import MarketDataProvider

    for k, v in refresh_snapshot(MarketDataProvider._read_index_tables, sys.argv[1:] or None).items():
        print(f"{k}: {v}")
