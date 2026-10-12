"""Every price a pick is measured against, on one scale, with why they differ.

A Picks row could show three different "targets" (the binding level target,
the analysts' mean, Research's DCF value) without saying which is which. This
gathers them with the analysts' low and high and the price, and states in one
sentence why the two furthest-apart estimates differ, from what each method
measures (``METHODS``). Nothing is shown that was not measured.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.thresholds import WHY

# What each estimate measures. Used to explain a gap, never as a number.
METHODS = {
    "analyst_mean": "analyst targets are 12-month views from the covering analysts",
    "level_target": "the level target is where the {horizon} plan takes profit, set from volatility and trend",
    "dcf": "the DCF value discounts projected free cash flows to today",
}
LABELS = {
    "analyst_low": "Analyst low", "analyst_mean": "Analyst mean", "analyst_high": "Analyst high",
    "level_target": "Level target", "dcf": "DCF fair value", "price": "Price",
}


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 and x == x else None


def build(rec: Dict[str, Any], row: Dict[str, Any], research: Any = None) -> Dict[str, Any]:
    """``{"price", "marks": [{key, label, value}], "low", "high", "sentence", "missing": [...]}``."""
    price = _f(row.get("price"))
    marks: List[Dict[str, Any]] = []
    missing: List[str] = []
    count = int(_f(rec.get("analyst_count")) or 0)
    if count >= WHY.ANALYST_MIN_COUNT and _f(rec.get("analyst_target")):
        for key, field in (("analyst_low", "analyst_target_low"), ("analyst_mean", "analyst_target"),
                           ("analyst_high", "analyst_target_high")):
            v = _f(rec.get(field))
            if v:
                marks.append({"key": key, "label": LABELS[key], "value": round(v, 2)})
    else:
        missing.append("No analyst targets: fewer than "
                       f"{WHY.ANALYST_MIN_COUNT} analysts cover it." if count else "No analyst targets reported.")
    lt = _f(row.get("target"))
    if lt and row.get("status") != "avoid":
        marks.append({"key": "level_target", "label": LABELS["level_target"], "value": round(lt, 2)})
    val = getattr(research, "valuation", None) if research is not None else None
    if val is not None and str(getattr(val, "dcf_status", "")) == "measured" and _f(getattr(val, "dcf_fair_value", 0)):
        marks.append({"key": "dcf", "label": LABELS["dcf"], "value": round(float(val.dcf_fair_value), 2)})
    elif research is None:
        missing.append("No DCF value: the Research check runs only on Buy, Accumulate and Watchlist calls.")
    else:
        missing.append("No DCF value: Research could not measure one for this name.")
    if not price or not marks:
        return {"price": price, "marks": marks, "low": None, "high": None, "sentence": "", "missing": missing}
    values = [m["value"] for m in marks] + [price]
    sentence = _sentence(marks, price, row)
    return {"price": round(price, 2), "marks": marks, "low": min(values), "high": max(values),
            "sentence": sentence, "missing": missing}


def _lc(label: str) -> str:
    """Lower-case a label for mid-sentence use, keeping acronyms (DCF)."""
    return label if label[1:2].isupper() else label[:1].lower() + label[1:]


def _sentence(marks: List[Dict[str, Any]], price: float, row: Dict[str, Any]) -> str:
    """Why the two furthest-apart estimates differ (or how one compares to the price)."""
    est = [m for m in marks if m["key"] in METHODS]
    if not est:
        return ""
    horizon = f"{int(row['horizon_days'])}-session" if row.get("horizon_days") else "trading"
    note = lambda k: METHODS[k].format(horizon=horizon)  # noqa: E731
    if len(est) == 1:
        m = est[0]
        gap = (m["value"] / price - 1) * 100
        return f"The {_lc(m['label'])} ${m['value']:,.2f} is {abs(gap):.1f}% {'above' if gap >= 0 else 'below'} the price: {note(m['key'])}."
    hi = max(est, key=lambda m: m["value"])
    lo = min(est, key=lambda m: m["value"])
    spread = (hi["value"] / lo["value"] - 1) * 100
    return (f"The {_lc(hi['label'])} (${hi['value']:,.2f}) is {spread:.0f}% above the {_lc(lo['label'])} "
            f"(${lo['value']:,.2f}) because {note(hi['key'])}, while {note(lo['key'])}.")
