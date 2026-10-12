"""Plan & Ideas: contribution and rebalance scenarios, and candidate fit.

Nothing here scores a stock. Candidates arrive with the SAME authoritative Decision
the Research page shows; this module asks the portfolio question about it: does it
fit, or does it duplicate what is already held? The verdict vocabulary keeps a good
business separate from a suitable addition now, and "keep cash" is a first-class
outcome, not a failure.

Scenarios are arithmetic on the current book under explicit assumptions that travel
with the result: the reserve, the deposit assumption, whole or fractional shares,
fees, and the fact that tax lots may be unknown. Amounts are proposals to compare,
not orders; QUANTA has no trading capability.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from . import guidance as gd
from .analytics import PortfolioPolicy

# What the profile must contain before ideas are described as fitting THIS investor.
PROFILE_REQUIRED = ("horizon_years", "risk_tolerance", "liquidity_reserve", "contribution")
RISK_LEVELS = ("conservative", "moderate", "aggressive")
# Risk grades a candidate may carry for each stated tolerance. Stated, editable defaults.
RISK_GRADE_LIMIT = {"conservative": "B", "moderate": "C", "aggressive": "D"}
GRADE_ORDER = "ABCDF"
GOOD_COMPANY_QUALITY = 60          # fundamental.quality_score at or above this reads as "good company"
MAX_PER_NAME_SHARE = 0.5           # no single purchase takes more than half a scenario's budget
MAX_BUYS = 3


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def clean_profile(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate what the browser sent. Unknown or malformed fields become absent, never defaults."""
    raw = raw or {}
    out: Dict[str, Any] = {}
    for k in ("horizon_years", "liquidity_reserve", "contribution", "max_position", "max_sector", "fee_per_trade"):
        v = _num(raw.get(k))
        if v is not None and v >= 0:
            out[k] = v
    for k in ("max_position", "max_sector"):
        if k in out and not 0 < out[k] <= 1:
            out.pop(k)
    if raw.get("risk_tolerance") in RISK_LEVELS:
        out["risk_tolerance"] = raw["risk_tolerance"]
    out["fractional"] = raw.get("fractional") is not False
    out["use_excess_cash"] = bool(raw.get("use_excess_cash"))
    if raw.get("account_type") in ("taxable", "retirement"):
        out["account_type"] = raw["account_type"]
    out["goal"] = str(raw.get("goal") or "")[:200]
    ex = raw.get("exclusions") or []
    out["exclusions"] = sorted({str(x).strip().upper()[:32] for x in ex if str(x).strip()})[:40] if isinstance(ex, list) else []
    return out


def profile_status(p: Dict[str, Any]) -> Dict[str, Any]:
    missing = [k for k in PROFILE_REQUIRED if p.get(k) is None]
    labels = {"horizon_years": "investment horizon", "risk_tolerance": "risk tolerance",
              "liquidity_reserve": "cash reserve to keep", "contribution": "contribution budget"}
    return {"complete": not missing, "missing": [labels[m] for m in missing]}


def _limits(policy: PortfolioPolicy, p: Dict[str, Any]) -> Dict[str, Any]:
    return {"position": p.get("max_position") or policy.max_single_position,
            "position_source": "Your limit" if p.get("max_position") else "QUANTA default",
            "sector": p.get("max_sector") or policy.max_sector,
            "sector_source": "Your limit" if p.get("max_sector") else "QUANTA default"}


def _book(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    book = {}
    for r in rows:
        b = r.get("broker") or {}
        v = _num(b.get("market_value"))
        if v is None:
            continue
        book[r["symbol"]] = {"value": v, "price": _num(b.get("last_price")),
                             "sector": (r.get("market") or {}).get("sector") or "Unclassified",
                             "beta": _num((r.get("market") or {}).get("beta"),)}
    return book


def _summarise(book: Dict[str, Dict[str, Any]], cash: Optional[float]) -> Dict[str, Any]:
    invested = sum(x["value"] for x in book.values())
    total = invested + (cash or 0.0)
    if total <= 0:
        return {"total": total}
    w = sorted(((s, x["value"] / total) for s, x in book.items()), key=lambda kv: -kv[1])
    sectors: Dict[str, float] = {}
    for s, x in book.items():
        if x["sector"] not in ("Unclassified",):
            sectors[x["sector"]] = sectors.get(x["sector"], 0.0) + x["value"] / total
    top_sector = max(sectors.items(), key=lambda kv: kv[1]) if sectors else None
    covered = [(x["value"], x["beta"]) for x in book.values() if x["beta"] is not None]
    beta = (sum(v * b for v, b in covered) / total) if covered and len(covered) == len(book) else None
    return {"total": total, "cash": cash, "cash_weight": (cash / total) if cash is not None else None,
            "largest": {"symbol": w[0][0], "weight": w[0][1]} if w else None,
            "top5": sum(x for _, x in w[:5]) if w else None,
            "top_sector": {"name": top_sector[0], "weight": top_sector[1]} if top_sector else None,
            "beta": beta, "positions": len(book)}


def _room(sym: str, sector: str, book: Dict[str, Dict[str, Any]], total_after: float, lim: Dict[str, Any]) -> float:
    cur = book.get(sym, {}).get("value", 0.0)
    pos_room = lim["position"] * total_after - cur
    if sector and sector != "Unclassified":
        sec_now = sum(x["value"] for x in book.values() if x["sector"] == sector)
        sec_room = lim["sector"] * total_after - sec_now
    else:
        sec_room = math.inf
    return max(0.0, min(pos_room, sec_room))


def _excluded(sym: str, sector: str, profile: Dict[str, Any]) -> bool:
    ex = set(profile.get("exclusions") or [])
    return sym.upper() in ex or (sector or "").upper() in ex


def eligible_buys(rows: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]],
                  profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Targets a purchase may go to, ranked by the model's own score (ties: smaller weight first).

    Holdings qualify only when guidance says Accumulate; candidates only when their verdict is
    "Suitable addition now". Exclusions remove anything, whatever its score.
    """
    out = []
    for r in rows:
        g = r.get("guidance") or {}
        sec = (r.get("market") or {}).get("sector") or "Unclassified"
        if g.get("action") != gd.PA_ACCUMULATE or _excluded(r["symbol"], sec, profile):
            continue
        b = r.get("broker") or {}
        price = _num(b.get("last_price"))
        if not price:
            continue
        out.append({"symbol": r["symbol"], "held": True, "price": price, "sector": sec,
                    "score": _num(g.get("conviction")) or 0.0, "weight": _num(b.get("weight")) or 0.0,
                    "cap": _num(((g.get("impact") or {}).get("add") or {}).get("delta_value")),
                    "reason": g.get("key_reason") or ""})
    for c in candidates:
        if c.get("verdict", {}).get("code") != "suitable" or _excluded(c["symbol"], c.get("sector") or "", profile):
            continue
        if not c.get("price"):
            continue
        out.append({"symbol": c["symbol"], "held": False, "price": c["price"], "sector": c.get("sector") or "Unclassified",
                    "score": _num(c.get("score")) or 0.0, "weight": 0.0, "cap": _num(c.get("max_position_value")),
                    "reason": c.get("verdict", {}).get("text") or ""})
    out.sort(key=lambda t: (-t["score"], t["weight"]))
    return out


def _allocate(budget: float, targets: Sequence[Dict[str, Any]], book: Dict[str, Dict[str, Any]],
              total_after: float, lim: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    fee = profile.get("fee_per_trade") or 0.0
    left = budget
    buys: List[Dict[str, Any]] = []
    working = {k: dict(v) for k, v in book.items()}
    for t in targets:
        if len(buys) >= MAX_BUYS or left <= fee + 1:
            break
        room = _room(t["symbol"], t["sector"], working, total_after, lim)
        cap = t["cap"] if t["cap"] else math.inf
        amount = min(left - fee, room, cap, budget * MAX_PER_NAME_SHARE)
        if amount < 1:
            continue
        shares = amount / t["price"]
        if not profile.get("fractional", True):
            shares = math.floor(shares)
            amount = shares * t["price"]
            if shares < 1:
                continue
        elif shares < 1e-6:
            continue
        spent = amount + fee
        left -= spent
        w = working.setdefault(t["symbol"], {"value": 0.0, "price": t["price"], "sector": t["sector"], "beta": None})
        w["value"] += amount
        buys.append({"symbol": t["symbol"], "held": t["held"], "action": "Buy", "amount": amount, "shares": shares,
                     "price": t["price"], "fee": fee, "reason": t["reason"], "sector": t["sector"]})
    return {"buys": buys, "left": left, "book": working}


def build_scenarios(rows: Sequence[Dict[str, Any]], overview: Dict[str, Any], policy: PortfolioPolicy,
                    profile: Dict[str, Any], candidates: Sequence[Dict[str, Any]], data_ok: bool) -> Dict[str, Any]:
    """No change, contribution-only and (when a limit is breached) a supported rebalance."""
    lim = _limits(policy, profile)
    book = _book(rows)
    cash = _num(overview.get("cash"))
    bp = _num(overview.get("buying_power"))
    reserve = profile.get("liquidity_reserve")
    contribution = profile.get("contribution")
    assumptions = [
        "Amounts are proposals to compare, not orders. QUANTA cannot trade.",
        "Prices are the latest broker quotes; execution prices will differ.",
        f"Fees: {'$%.2f per trade' % profile['fee_per_trade'] if profile.get('fee_per_trade') else 'none entered (enter your broker fee if it charges one)'}.",
        "Fractional shares: " + ("assumed available for every name (not every stock is eligible)." if profile.get("fractional", True)
                                 else "not used; amounts are rounded down to whole shares."),
        "Tax lots: " + ("reported for holdings; sales are described, not tax-optimised." if any(
            (r.get("broker") or {}).get("tax_lots") for r in rows) else "not reported, so the tax cost of any sale is unknown."),
    ]
    base = _summarise(book, cash)
    excess = None
    if cash is not None:
        avail = min(cash, bp) if bp is not None else cash
        excess = max(0.0, avail - (reserve or 0.0)) if reserve is not None else None
    scenarios: List[Dict[str, Any]] = [{
        "key": "none", "title": "No change", "summary": "Keep the account exactly as it is.",
        "moves": [], "after": base, "leftover": cash, "status": "ok",
        "tradeoffs": ["No transaction cost or tax event.", "Any concentration or gap in the current book stays."],
    }]

    def targets_for(extra_candidates=candidates):
        return eligible_buys(rows, extra_candidates, profile)

    # -- contribution only --------------------------------------------------
    contrib: Dict[str, Any] = {"key": "contribution", "title": "Contribution only",
                               "summary": "Put new money to work without selling anything."}
    if contribution is None:
        contrib.update(status="needs_input", moves=[], after=base,
                       note="Enter a contribution budget in Your plan to compare this scenario.")
    else:
        budget = contribution + (excess if profile.get("use_excess_cash") and excess else 0.0)
        total_after = base.get("total", 0.0) + contribution
        al = _allocate(budget, targets_for(), book, total_after, lim, profile)
        after_cash = (cash or 0.0) + contribution - sum(b["amount"] + b["fee"] for b in al["buys"])
        contrib.update(
            status="ok", budget=budget,
            funding=[{"label": "New contribution", "amount": contribution, "note": "Assumes the money is deposited first."}]
                    + ([{"label": "Cash above your reserve", "amount": excess,
                         "note": f"Cash and buying power less the {reserve:,.0f} reserve."}]
                       if profile.get("use_excess_cash") and excess else []),
            moves=al["buys"], leftover=after_cash, unallocated=al["left"],
            after=_summarise(al["book"], after_cash),
            note=("" if al["buys"] else "Nothing qualifies right now: keeping the contribution in cash is the recommendation "
                  "of this scenario. Ideas that are held back appear under Ideas."),
            tradeoffs=_tradeoffs(base, _summarise(al["book"], after_cash), al["buys"]),
        )
        if reserve is not None and after_cash < reserve - 0.5:
            contrib["warning"] = f"Cash after this scenario ({after_cash:,.0f}) is below your {reserve:,.0f} reserve."
    scenarios.append(contrib)

    # -- supported rebalance ------------------------------------------------
    total = base.get("total") or 0.0
    over = []
    for r in rows:
        b = r.get("broker") or {}
        v = _num(b.get("market_value"))
        w = _num(b.get("weight"))
        if v is None or w is None or w < lim["position"]:
            continue
        cut = v - lim["position"] * total
        if cut >= 1:
            over.append({"symbol": r["symbol"], "amount": cut, "price": _num(b.get("last_price")),
                         "weight_before": w, "lots": b.get("tax_lots"),
                         "tax": (r.get("guidance") or {}).get("tax_note") or "Tax lots were not reported."})
    reb: Dict[str, Any] = {"key": "rebalance", "title": "Rebalance to your limits",
                           "summary": f"Trim positions above the {lim['position']:.0%} single-name limit ({lim['position_source']}) and reinvest."}
    if not over:
        reb.update(status="not_needed", moves=[], after=base,
                   note=f"No holding is at or above the {lim['position']:.0%} single-name limit, so there is nothing a rebalance would fix.")
    elif not data_ok:
        reb.update(status="blocked", moves=[], after=base,
                   note="Account data has not reconciled, so QUANTA will not size a sale until it does.")
    else:
        sell_book = {k: dict(v) for k, v in book.items()}
        moves = []
        proceeds = 0.0
        for o in over:
            fee = profile.get("fee_per_trade") or 0.0
            amt = o["amount"]
            shares = amt / o["price"] if o["price"] else None
            if not profile.get("fractional", True) and shares is not None:
                shares = math.floor(shares)
                amt = shares * o["price"]
            if amt < 1:
                continue
            sell_book[o["symbol"]]["value"] -= amt
            proceeds += amt - fee
            moves.append({"symbol": o["symbol"], "held": True, "action": "Sell", "amount": amt, "shares": shares,
                          "price": o["price"], "fee": fee, "weight_before": o["weight_before"],
                          "reason": f"Above the {lim['position']:.0%} limit", "tax": o["tax"], "sector": book[o["symbol"]]["sector"]})
        budget = proceeds + (contribution or 0.0)
        total_after = total + (contribution or 0.0)
        # Sold names must not be bought back.
        skip = {m["symbol"] for m in moves}
        targets = [t for t in eligible_buys(rows, candidates, profile) if t["symbol"] not in skip]
        al = _allocate(budget, targets, sell_book, total_after, lim, profile)
        after_cash = (cash or 0.0) + (contribution or 0.0) + proceeds - sum(b["amount"] + b["fee"] for b in al["buys"])
        reb.update(
            status="ok", moves=moves + al["buys"], leftover=after_cash, unallocated=al["left"],
            funding=[{"label": "Sale proceeds", "amount": proceeds, "note": "Cash accounts may take a day to settle."}]
                    + ([{"label": "New contribution", "amount": contribution, "note": "Assumes the money is deposited first."}]
                       if contribution else []),
            after=_summarise(al["book"], after_cash),
            note=("" if al["buys"] else "Nothing qualifies to buy, so the proceeds would stay in cash."),
            tradeoffs=_tradeoffs(base, _summarise(al["book"], after_cash), moves + al["buys"])
            + ["A sale may realise a taxable gain; " + ("tax lots are shown per holding." if any(
                m.get("tax") for m in moves) else "the tax cost is unknown.")],
        )
    scenarios.append(reb)
    return {"scenarios": scenarios, "assumptions": assumptions, "limits": lim, "excess_cash": excess}


def _tradeoffs(before: Dict[str, Any], after: Dict[str, Any], moves: Sequence[Dict[str, Any]]) -> List[str]:
    out = []
    b, a = before.get("largest"), after.get("largest")
    if b and a and (b["symbol"], round(b["weight"], 4)) != (a["symbol"], round(a["weight"], 4)):
        out.append(f"Largest position: {b['symbol']} {b['weight']:.1%} → {a['symbol']} {a['weight']:.1%}.")
    if before.get("top5") is not None and after.get("top5") is not None and abs(before["top5"] - after["top5"]) >= 0.0005:
        out.append(f"Top five holdings: {before['top5']:.1%} → {after['top5']:.1%} of the account.")
    ts_b, ts_a = before.get("top_sector"), after.get("top_sector")
    if ts_b and ts_a and abs(ts_b["weight"] - ts_a["weight"]) >= 0.0005:
        out.append(f"Largest sector ({ts_a['name']}): {ts_b['weight']:.1%} → {ts_a['weight']:.1%}.")
    if before.get("cash_weight") is not None and after.get("cash_weight") is not None:
        out.append(f"Cash: {before['cash_weight']:.1%} → {after['cash_weight']:.1%} of the account.")
    if before.get("beta") is not None and after.get("beta") is not None and abs(before["beta"] - after["beta"]) >= 0.005:
        out.append(f"Modeled market beta: {before['beta']:.2f} → {after['beta']:.2f}.")
    if not moves:
        out.append("No purchase clears QUANTA's rules, so the money stays in cash.")
    return out


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

VERDICTS = {
    "suitable": "Suitable addition now",
    "good_not_now": "Good company, not now",
    "duplicates": "Duplicates what you own",
    "not_suitable": "Not suitable",
    "insufficient": "Insufficient evidence",
    "excluded": "On your exclusion list",
}


def evaluate_candidate(symbol: str, analysis: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]],
                       rows: Sequence[Dict[str, Any]], metrics: Dict[str, Any], overview: Dict[str, Any],
                       policy: PortfolioPolicy, profile: Dict[str, Any], today, correlations: Optional[Dict[str, float]] = None
                       ) -> Dict[str, Any]:
    """One candidate against this book. ``analysis`` is the compacted research result."""
    lim = _limits(policy, profile)
    out: Dict[str, Any] = {"symbol": symbol, "held": any(r["symbol"] == symbol for r in rows)}
    if error or not analysis or not analysis.get("decision"):
        why = (error or {}).get("title") or "No analysis is available for this ticker."
        return dict(out, verdict={"code": "insufficient", "label": VERDICTS["insufficient"], "text": why},
                    unavailable=True)
    dec = analysis["decision"]
    if dec.get("action") == "INVALID":
        return dict(out, verdict={"code": "insufficient", "label": VERDICTS["insufficient"],
                                  "text": "The research engine could not produce a valid Decision."}, unavailable=True)
    sector = analysis.get("sector") or ""
    fund = analysis.get("fundamental") or {}
    val = analysis.get("valuation") or {}
    intel = analysis.get("market_intel") or {}
    risk = analysis.get("risk") or {}
    cat = analysis.get("catalysts") or {}
    thesis = analysis.get("thesis") or {}
    price = _num(analysis.get("current_price"))
    total = _num(overview.get("total_value")) or 0.0
    sec_w = 0.0
    for s in metrics.get("sectors", []):
        if s.get("sector") == sector:
            sec_w = _num(s.get("weight")) or 0.0
    stance = str(dec.get("stance") or "NONE").upper()
    new_money = str(dec.get("new_money") or "").upper()
    score = _num(dec.get("score"))
    quality = _num(fund.get("quality_score"))
    good = (quality is not None and quality >= GOOD_COMPANY_QUALITY) or stance == "BULLISH"

    pctx = gd.PositionContext(symbol=symbol, quantity=0.0, value=0.0, weight=0.0, price=price, sector=sector,
                              sector_weight=sec_w if sector else None,
                              earnings_date=str(cat.get("earnings_date") or intel.get("next_earnings_date") or "")[:10] or None)
    ctx = gd.PortfolioContext(total_value=total, buying_power=_num(overview.get("buying_power")),
                              stop_risk_pct=_num((metrics.get("stops") or {}).get("loss_pct")), policy=policy, today=today)
    constraints = [c for c in gd.portfolio_constraints(pctx, ctx) if c.code != "POSITION_AT_CAP"]
    if sector and sec_w >= lim["sector"] and not any(c.code == "SECTOR_AT_CAP" for c in constraints):
        constraints.append(gd.Constraint("SECTOR_AT_CAP", f"{sector} is already {sec_w:.0%} of the account "
                                         f"(limit {lim['sector']:.0%}, {lim['sector_source']})", "", "quanta"))
    dup = []
    for held, corr in sorted((correlations or {}).items(), key=lambda kv: -kv[1]):
        row = next((r for r in rows if r["symbol"] == held), None)
        hw = _num(((row or {}).get("broker") or {}).get("weight")) or 0.0
        if corr >= policy.correlation_threshold and hw >= 0.05:
            dup.append({"symbol": held, "correlation": corr, "weight": hw})
    grade = str(dec.get("risk_grade") or risk.get("risk_grade") or "").upper()
    tol = profile.get("risk_tolerance")
    risk_ok = True
    if tol and grade in GRADE_ORDER:
        risk_ok = GRADE_ORDER.index(grade) <= GRADE_ORDER.index(RISK_GRADE_LIMIT[tol])
    stock_blocks = [b.get("text", "") for b in (dec.get("blockers") or [])]

    excluded = _excluded(symbol, sector, profile)
    if excluded:
        code, text = "excluded", "You excluded this ticker or its sector."
    elif out["held"]:
        code, text = "duplicates", "Already held. Adding to it is a question for Holdings, not Ideas."
    elif dup:
        d = dup[0]
        code, text = "duplicates", f"Moves closely with {d['symbol']} (correlation {d['correlation']:.2f}), which is already {d['weight']:.0%} of the account."
    elif new_money == "ELIGIBLE" and not constraints and risk_ok and stance != "BEARISH":
        code, text = "suitable", "QUANTA's Decision allows new money and nothing in the portfolio stands in the way."
    elif good and stance != "BEARISH":
        reasons = [c.text for c in constraints] + stock_blocks
        if not risk_ok:
            reasons.append(f"Risk grade {grade} is above what a {tol} tolerance allows")
        code, text = "good_not_now", ("; ".join(reasons[:2]) or "The Decision does not support new money yet") + "."
    else:
        code, text = "not_suitable", "The Decision is not supportive: " + ("; ".join(stock_blocks[:2]) or f"stance {stance.lower()}") + "."

    level = dec.get("levels") or {}
    sizing = _num((dec.get("sizing") or {}).get("position_value"))
    room = _room(symbol, sector, _book(rows), total, lim) if total else None
    max_val = min(x for x in (sizing, room) if x is not None) if code == "suitable" and (sizing or room) else None
    invalid = list(dec.get("invalidation") or [])
    if _num(level.get("stop")):
        invalid.insert(0, f"A daily close below QUANTA's stop, ${_num(level['stop']):,.2f}")
    bear = [str(x) for x in (thesis.get("bear_case") or []) if x]
    warnings = [str(x) for x in (analysis.get("risk_warnings") or []) if x]
    strength = []
    if _num(fund.get("debt_to_equity")) is not None:
        strength.append(f"Debt/equity {fund['debt_to_equity']:.2f}")
    if _num(fund.get("net_margin")) is not None:
        strength.append(f"Net margin {fund['net_margin']:.1f}%")
    if _num(fund.get("roe")) is not None:
        strength.append(f"ROE {fund['roe']:.1f}%")
    if quality is not None:
        strength.append(f"Quality score {quality:.0f}/100")
    valuation = []
    if val.get("valuation_label"):
        valuation.append(f"Valuation read: {str(val['valuation_label']).replace('_', ' ')}")
    if _num(fund.get("pe_ratio")) is not None:
        valuation.append(f"P/E {fund['pe_ratio']:.1f}" + (f", forward {fund['forward_pe']:.1f}" if _num(fund.get("forward_pe")) else ""))
    if val.get("dcf_status") == "measured" and _num(val.get("dcf_upside")) is not None:
        valuation.append(f"DCF upside {val['dcf_upside']:+.1f}% (model estimate)")
    if _num(intel.get("target_mean")) is not None:
        valuation.append(f"Analyst mean target ${intel['target_mean']:,.0f} ({intel.get('analyst_count') or '?'} analysts)")
    conf = "low"
    ev = (dec.get("evidence") or {})
    if ev.get("permits_recommendation"):
        conf = "moderate"
    if ev.get("status") in ("UNTESTED", None):
        conf = "low"
    freshness = (dec.get("freshness") or {}).get("status")
    return dict(
        out, name=analysis.get("company_name") or symbol, sector=sector, price=price, score=score,
        stance=stance.title(), risk_grade=grade or None,
        verdict={"code": code, "label": VERDICTS[code], "text": text},
        good_company={"value": bool(good), "basis": (f"Quality score {quality:.0f} ≥ {GOOD_COMPANY_QUALITY}" if quality is not None and quality >= GOOD_COMPANY_QUALITY
                                                     else "Bullish Decision" if stance == "BULLISH" else "Quality below threshold or not measured")},
        thesis=str(thesis.get("summary") or ""), bull=[str(x) for x in (thesis.get("bull_case") or [])][:3],
        fundamentals=strength, valuation=valuation,
        catalysts=([f"Next earnings {str(cat.get('earnings_date'))[:10]}"] if cat.get("earnings_date") else [])
        + ([str(cat["near_term_catalyst"])] if cat.get("near_term_catalyst") else []),
        fit={"sector_weight": sec_w if sector else None, "sector": sector or None, "duplicates": dup[:3],
             "constraints": [c.text for c in constraints], "fund_overlap": "Not measured (no dated constituent data)"},
        principal_risk=(warnings[0] if warnings else (dec.get("risks") or [None])[0]) or "No principal risk was stated by the engine.",
        counterargument=bear[0] if bear else "The engine stated no bear case for this ticker.",
        invalidation=invalid[:3] or ["The engine stated no invalidation trigger."],
        entry_note=("Reference price $%.2f; QUANTA states no entry zone." % _num(level.get("reference_price"))
                    if _num(level.get("reference_price")) else "QUANTA states no entry zone."),
        max_position_value=max_val,
        position_basis=("Up to QUANTA's risk-budget size, capped by your single-name and sector limits." if max_val else None),
        evidence={"confidence": conf, "status": ev.get("status"), "text": ev.get("status_text"),
                  "freshness": freshness, "as_of": analysis.get("analysis_timestamp"),
                  "source": analysis.get("data_provider")},
        conditional=not profile_status(profile)["complete"],
    )
