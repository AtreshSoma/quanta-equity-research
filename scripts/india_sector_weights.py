"""Tile sizes for the India sector heatmap, computed rather than typed.

Each Overview tile is an NSE sector index (Nifty IT, Nifty Bank ...). Its weight
is the total market capitalisation of that index's constituents (NSE's own
constituent lists, market caps from Yahoo), as a share of the eleven indices
together. Indices overlap (a PSU bank is also in Nifty Bank), so the shares
describe each index's size, not a partition of the market. The weights only
size tiles; no calculation reads them.

    python scripts/india_sector_weights.py     # prints the table for engine/thresholds.py

Refresh it every quarter, as the US table is.
"""
from __future__ import annotations

import csv
import io
import sys
import time
import urllib.request
from datetime import date
from typing import Dict, List

INDICES = {
    "^CNXIT": "niftyit", "^NSEBANK": "niftybank", "^CNXAUTO": "niftyauto", "^CNXPHARMA": "niftypharma",
    "^CNXFMCG": "niftyfmcg", "^CNXMETAL": "niftymetal", "^CNXENERGY": "niftyenergy",
    "^CNXREALTY": "niftyrealty", "^CNXMEDIA": "niftymedia", "^CNXPSUBANK": "niftypsubank",
    "^CNXINFRA": "niftyinfra",
}
NSE_CSV = "https://archives.nseindia.com/content/indices/ind_{}list.csv"


def constituents(slug: str) -> List[str]:
    req = urllib.request.Request(NSE_CSV.format(slug), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed NSE host
        rows = csv.DictReader(io.StringIO(resp.read().decode("utf-8", "replace")))
        return [r["Symbol"].strip() + ".NS" for r in rows if r.get("Series", "").strip() == "EQ"]


def market_cap(symbol: str) -> float:
    import yfinance as yf
    try:
        return float(yf.Ticker(symbol).fast_info["marketCap"] or 0.0)
    except Exception:  # noqa: BLE001 - a missing cap counts as zero, and is reported
        return 0.0


def compute() -> Dict[str, object]:
    members = {idx: constituents(slug) for idx, slug in INDICES.items()}
    caps: Dict[str, float] = {}
    for sym in sorted({s for v in members.values() for s in v}):
        caps[sym] = market_cap(sym)
        time.sleep(0.3)
    totals = {idx: sum(caps[s] for s in syms) for idx, syms in members.items()}
    grand = sum(totals.values()) or 1.0
    missing = sorted(s for s, c in caps.items() if not c)
    return {
        "weights": {idx: round(100.0 * t / grand, 1) for idx, t in totals.items()},
        "names": len(caps), "missing": missing, "as_of": date.today().isoformat(),
    }


def main() -> int:
    out = compute()
    print(f"# {out['as_of']}: {out['names']} constituents, {len(out['missing'])} without a market cap "
          f"{out['missing'][:10]}")
    print('"IN": {' + ", ".join(f'"{k}": {v}' for k, v in sorted(out["weights"].items(), key=lambda kv: -kv[1])) + "},")
    return 0


if __name__ == "__main__":
    sys.exit(main())
