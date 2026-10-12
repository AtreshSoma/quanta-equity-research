"""Exposure views: Sector, Asset class, Geography, Holdings.

Each view is its own classification. Sector never contains fund style labels or
cash, and asset class never contains a sector. Every weight is a share of the
WHOLE account (cash included), so the four views agree with each other and with
the allocation shown elsewhere; a view that leaves some value unclassified says
how much (``unclassified``) instead of rescaling the rest to 100%.

Funds are not looked through. Look-through needs a dated constituent list, and
the profile data available here (Yahoo) carries no as-of date, so a fund is one
line in every view and the look-through block says why.

Limits: a bar carries a reference only when one exists, and says whose it is.
``QUANTA default`` marks the model's built-in policy; ``Your limit`` marks a value
the user entered. Asset class and geography have no default limits at all.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

FUND_TYPES = {"ETF", "MUTUALFUND", "ETN", "CEF"}

BOND_WORDS = ("bond", "treasury", "fixed income", "income", "aggregate", "credit", "muni", "tips", "inflation-protected")
COMMODITY_WORDS = ("commodit", "gold", "silver", "precious", "metals", "energy limited", "oil", "natural resources")
CRYPTO_WORDS = ("crypto", "bitcoin", "digital asset", "ether")


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def is_fund(row: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    a = row.get("analysis") or {}
    if a.get("is_etf") or (a.get("fundamental") or {}).get("is_etf") or str(a.get("asset_type") or "").upper() in FUND_TYPES:
        return True
    return str(profile.get("quote_type") or "").upper() in FUND_TYPES


def asset_class_of(row: Dict[str, Any], profile: Dict[str, Any]) -> str:
    if not is_fund(row, profile):
        return "Individual stocks"
    cat = " ".join(str(profile.get(k) or "") for k in ("category", "name")).lower() or str(
        (row.get("broker") or {}).get("name") or "").lower()
    if any(w in cat for w in CRYPTO_WORDS):
        return "Crypto-linked funds"
    if any(w in cat for w in COMMODITY_WORDS):
        return "Commodity funds"
    if any(w in cat for w in BOND_WORDS):
        return "Bond funds"
    if profile.get("category"):
        return "Stock funds"
    return "Funds, type not reported"


def _bars(items: Dict[str, Dict[str, Any]], total: float) -> List[Dict[str, Any]]:
    out = []
    for name, it in items.items():
        it["name"] = name
        it["weight"] = it["value"] / total if total else None
        it["symbols"] = sorted(it["symbols"], key=lambda s: -it["by_symbol"].get(s, 0))
        out.append(it)
    out.sort(key=lambda r: -(r["value"]))
    return out


def _view(kind: str, title: str, rows: Sequence[Dict[str, Any]], total: float, key, unknown_label: str,
          limit: Optional[float], limit_source: str, targets: Optional[Dict[str, float]],
          note: str = "") -> Dict[str, Any]:
    items: Dict[str, Dict[str, Any]] = {}
    unclassified = 0.0
    for r in rows:
        v = _num((r.get("broker") or {}).get("market_value"))
        if v is None:
            continue
        name = key(r)
        if name is None:
            name = unknown_label
        it = items.setdefault(name, {"value": 0.0, "symbols": [], "by_symbol": {}})
        it["value"] += v
        it["symbols"].append(r["symbol"])
        it["by_symbol"][r["symbol"]] = v
        if name == unknown_label:
            unclassified += v
    bars = _bars(items, total)
    for b in bars:
        b.pop("by_symbol", None)
        b["classified"] = b["name"] != unknown_label
        tgt = (targets or {}).get(b["name"])
        if tgt is not None:
            b["limit"], b["limit_source"], b["limit_kind"] = tgt, "Your target", "target"
        elif limit is not None and b["classified"]:
            b["limit"], b["limit_source"], b["limit_kind"] = limit, limit_source, "cap"
        else:
            b["limit"], b["limit_source"], b["limit_kind"] = None, "", ""
        b["over"] = bool(b["limit"] is not None and b["limit_kind"] == "cap" and b["weight"] is not None
                         and b["weight"] >= b["limit"])
    invested = sum(b["value"] for b in bars)
    return {"kind": kind, "title": title, "items": bars, "invested": invested,
            "unclassified": (unclassified / total) if total else None,
            "classified_share": ((invested - unclassified) / invested) if invested else None,
            "note": note}


def build_exposures(rows: Sequence[Dict[str, Any]], total: Optional[float], cash: Optional[float],
                    profiles: Dict[str, Dict[str, Any]], policy: Dict[str, Any],
                    metrics: Dict[str, Any], prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The four exposure views plus concentration, correlation and look-through status."""
    prefs = prefs or {}
    total = _num(total) or 0.0
    profiles = profiles or {}
    max_pos = _num(prefs.get("max_position"))
    max_sec = _num(prefs.get("max_sector"))
    pos_limit, pos_src = (max_pos, "Your limit") if max_pos else (_num(policy.get("max_single_position")), "QUANTA default")
    sec_limit, sec_src = (max_sec, "Your limit") if max_sec else (_num(policy.get("max_sector")), "QUANTA default")
    targets = prefs.get("targets") or {}

    def prof(r: Dict[str, Any]) -> Dict[str, Any]:
        return profiles.get(r["symbol"]) or {}

    def sector_key(r: Dict[str, Any]) -> Optional[str]:
        if is_fund(r, prof(r)):
            return "Funds (not split by sector)"
        s = (r.get("market") or {}).get("sector") or prof(r).get("sector")
        return str(s) if s else None

    def geo_key(r: Dict[str, Any]) -> Optional[str]:
        if is_fund(r, prof(r)):
            return "Funds (not looked through)"
        c = prof(r).get("country")
        return str(c) if c else None

    sector = _view("sector", "Sector", rows, total, sector_key, "Sector not reported", sec_limit, sec_src,
                   targets.get("sector"),
                   "Stocks by reported sector. Funds are one line because they are not looked through; cash is not a sector.")
    # A fund bucket is a disclosure, not a sector: it never carries the sector cap.
    for b in sector["items"]:
        if b["name"].startswith("Funds"):
            b.update(limit=None, limit_source="", limit_kind="", over=False, classified=False)
    asset = _view("asset_class", "Asset class", rows, total, lambda r: asset_class_of(r, prof(r)),
                  "Unclassified", None, "", targets.get("asset_class"),
                  "What each holding is, not what it invests in. No allocation limit is implied for any class.")
    geo = _view("geography", "Geography", rows, total, geo_key, "Country not reported", None, "",
                targets.get("geography"),
                "Company country of domicile for stocks. Funds are not looked through, so their true geography is unknown.")
    hold = _view("holdings", "Holdings", rows, total, lambda r: r["symbol"], "Unpriced", pos_limit, pos_src,
                 targets.get("holdings"), "Each position as a share of the whole account.")
    for b in hold["items"]:
        b["symbols"] = [b["name"]]

    if cash is not None and total:
        cash_w = cash / total
    else:
        cash_w = None

    weights = sorted(((r["symbol"], _num((r.get("broker") or {}).get("weight")) or 0.0) for r in rows),
                     key=lambda kv: -kv[1])
    top5 = weights[:5]
    conc = {
        "largest": {"symbol": weights[0][0], "weight": weights[0][1]} if weights else None,
        "top5": {"symbols": [s for s, _ in top5], "weight": sum(w for _, w in top5)} if top5 else None,
        "effective_names": (metrics.get("concentration") or {}).get("effective_names"),
        "position_limit": pos_limit, "position_limit_source": pos_src,
    }
    pairs = ((metrics.get("volatility") or {}).get("correlated_pairs") or [])
    wmap = dict(weights)
    correlated = [{"a": p["a"], "b": p["b"], "correlation": p["correlation"],
                   "combined_weight": wmap.get(p["a"], 0) + wmap.get(p["b"], 0)} for p in pairs]
    correlated.sort(key=lambda p: -p["combined_weight"])
    funds = [r["symbol"] for r in rows if is_fund(r, prof(r))]
    return {
        "cash_weight": cash_w,
        "views": {"sector": sector, "asset_class": asset, "geography": geo, "holdings": hold},
        "concentration": conc,
        "correlated": correlated[:5],
        "correlation_threshold": _num(policy.get("correlation_threshold")),
        "correlation_days": (metrics.get("volatility") or {}).get("days"),
        "correlation_coverage": (metrics.get("volatility") or {}).get("coverage"),
        "lookthrough": {
            "available": False,
            "funds": funds,
            "reason": ("Look-through is off: it needs a dated list of each fund's constituents, and the fund data "
                       "available here has no as-of date. Fund overlap is therefore not measured; funds are one "
                       "line in every view." if funds else "No funds held, so nothing to look through."),
        },
    }
