"""Sector-aware intrinsic value and 12-month target for the shadow model v4.

The live intrinsic value averages the Graham Number 50/50 with a DCF. Graham,
sqrt(22.5 x EPS x BVPS), only makes sense where book value is a real anchor; it
structurally undervalues asset-light, buyback-heavy firms (CSCO came out near
$65 against a $110 price and a $136 Street target). v4 picks the method by
business type and refuses any value that is implausibly far from the price:

* banks / insurers / capital markets: justified P/B, (ROE - g) / (r - g);
* energy: free-cash-flow yield, blended with the DCF when there is one;
* everyone else: the DCF, plus Graham only when price-to-book is low enough.

Pure functions, no I/O, so it can be unit-tested offline.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# One parameter per line; the caller may override any of them.
DEFAULT_PARAMS: Dict[str, Any] = {
    "cost_of_equity": 0.10,      # r in the justified P/B formula
    "growth": 0.03,              # g, long-run growth in the justified P/B formula
    "roe_clip": (0.0, 0.25),     # ROE is clipped to this (low, high) before use
    "energy_fcf_yield": 0.08,    # required FCF yield; value = FCF per share / yield
    "graham_max_pb": 3.0,        # Graham is used only when 0 < P/B <= this
    "plausible_ratio": 3.0,      # candidates must lie in price x [1/k, k]
    "w_analyst": 0.5,            # target weight on analyst consensus
    "w_analyst_thin": 0.25,      # same, when fewer than thin_coverage analysts
    "thin_coverage": 3,          # analyst count below which coverage is "thin"
    "w_intrinsic": 0.3,          # target weight on intrinsic value
}

_FINANCIAL_KEYS = ("bank", "insurance", "capital markets", "mortgage")


def _num(v: Any) -> Optional[float]:
    """Finite float or None (yfinance returns None, strings and NaN)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _is_financial(info: Dict[str, Any]) -> bool:
    text = f"{info.get('industry') or ''} {info.get('sector') or ''}".lower()
    return any(k in text for k in _FINANCIAL_KEYS)


def _justified_pb(info: Dict[str, Any], p: Dict[str, Any]) -> Optional[float]:
    bvps, roe = _num(info.get("bookValue")), _num(info.get("returnOnEquity"))
    r, g = p["cost_of_equity"], p["growth"]
    if bvps is None or bvps <= 0 or roe is None or r <= g:
        return None
    lo, hi = p["roe_clip"]
    roe = min(max(roe, lo), hi)
    return bvps * (roe - g) / (r - g)


def _fcf_yield_value(info: Dict[str, Any], p: Dict[str, Any]) -> Optional[float]:
    fcf, shares = _num(info.get("freeCashflow")), _num(info.get("sharesOutstanding"))
    if fcf is None or fcf <= 0 or shares is None or shares <= 0 or p["energy_fcf_yield"] <= 0:
        return None
    return fcf / shares / p["energy_fcf_yield"]


def _graham(info: Dict[str, Any], p: Dict[str, Any]) -> Optional[float]:
    eps, bvps = _num(info.get("trailingEps")), _num(info.get("bookValue"))
    pb = _num(info.get("priceToBook"))
    if eps is None or bvps is None or pb is None:
        return None
    if eps <= 0 or bvps <= 0 or not 0 < pb <= p["graham_max_pb"]:
        return None
    return (22.5 * eps * bvps) ** 0.5


def _out(value: Optional[float], method: str, comps: List[Dict[str, Any]], note: str) -> Dict[str, Any]:
    return {"value": value, "method": method, "components": comps, "note": note}


def intrinsic_value(info: Dict[str, Any], *, price: float, dcf_value: Optional[float],
                    params: Dict[str, Any]) -> Dict[str, Any]:
    """Sector-aware intrinsic value: ``{"value", "method", "components", "note"}``."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    px = _num(price)
    if px is None or px <= 0:
        return _out(None, "unavailable", [], "No valid price, so no value can be checked for plausibility.")
    dcf = _num(dcf_value)
    if dcf is not None and dcf <= 0:
        dcf = None

    cands: List[Tuple[str, float]] = []
    if _is_financial(info):
        # The DCF is skipped for banks and insurers, as in the live model.
        v = _justified_pb(info, p)
        if v is not None:
            cands.append(("justified_pb", v))
    elif str(info.get("sector") or "").lower() == "energy":
        v = _fcf_yield_value(info, p)
        if v is not None:
            cands.append(("fcf_yield", v))
        if dcf is not None:
            cands.append(("dcf", dcf))
    else:
        if dcf is not None:
            cands.append(("dcf", dcf))
        g = _graham(info, p)
        if g is not None:
            cands.append(("graham", g))

    k = float(p["plausible_ratio"])
    lo, hi = px / k, px * k
    kept = [(s, v) for s, v in cands if lo <= v <= hi]
    note = " ".join(f"Dropped {s} value {v:.2f}: outside the plausible band {lo:.2f} to {hi:.2f}."
                    for s, v in cands if not lo <= v <= hi)

    if not kept:
        why = "" if cands else "No usable inputs for this business type."
        return _out(None, "unavailable", [], f"{why} {note}".strip())

    w = 1.0 / len(kept)
    comps = [{"source": s, "value": v, "weight": w} for s, v in kept]
    method = "+".join(s for s, _ in kept) if len(kept) > 1 else kept[0][0]
    if method == "fcf_yield+dcf":
        method = "fcf_yield"  # still an energy valuation, DCF is a cross-check
    return _out(sum(c["value"] * w for c in comps), method, comps, note)


def blended_target(consensus_mean: Optional[float], analyst_count: int,
                   intrinsic: Optional[float], params: Dict[str, Any]) -> Dict[str, Any]:
    """12-month target: analyst consensus and intrinsic, weights renormalised over what is present."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    thin = int(analyst_count or 0) < p["thin_coverage"]
    raw: List[Tuple[str, float, float]] = []
    c, i = _num(consensus_mean), _num(intrinsic)
    if c is not None and c > 0:
        raw.append(("analyst", c, p["w_analyst_thin"] if thin else p["w_analyst"]))
    if i is not None and i > 0:
        raw.append(("intrinsic", i, p["w_intrinsic"]))
    total = sum(w for _, _, w in raw)
    if not raw or total <= 0:
        return {"value": None, "components": []}
    comps = [{"source": s, "value": v, "weight": w / total} for s, v, w in raw]
    return {"value": sum(c["value"] * c["weight"] for c in comps), "components": comps}
