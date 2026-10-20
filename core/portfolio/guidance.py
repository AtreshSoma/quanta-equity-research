"""Portfolio-context guidance: what one authoritative Decision means for THIS book.

This is not a second recommendation engine. It never scores a stock. For each
holding it takes the Decision exactly as the research engine published it and
answers three separate questions:

* model stance        -- the Decision's own ``stance`` (Bullish/Neutral/Bearish)
* new-money action    -- the Decision's ``new_money``, then held back by
                         portfolio constraints (size, sector, correlation, cash,
                         earnings, risk budget). Constraints only ever LOWER it.
* existing position   -- the Decision's ``holder_action`` (HOLD/TRIM/EXIT), plus
                         one portfolio-only rule: a Hold that has grown past the
                         trim-review weight is sent to Trim for concentration.

The reason shown for an action is built from the rule that produced it
(``engine.blockers.holder_rule`` re-run on the Decision's own score and codes),
never from a template that assumes a score band. A holder action that does not
follow from its own score and codes is not shown as a call.

A data failure is never turned into an action. No Decision, an INVALID one, a
failed analysis or stale prices all produce "Unavailable" with the reason.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from engine import thresholds as _T
from engine.blockers import BLOCKERS, NEUTRAL_FLOOR, TRIM_FLOOR, holder_rule
from engine.score_bands import band_for
from engine.types import FinalAction

from .analytics import PortfolioPolicy

# --- vocabularies ----------------------------------------------------------

STANCE_LABELS = {"BULLISH": "Bullish", "NEUTRAL": "Neutral", "BEARISH": "Bearish", "NONE": "Not rated"}

NM_ACCUMULATE = "Accumulate"
NM_HOLD = "Hold / No New Money"
NM_AVOID = "Avoid Adding"
UNAVAILABLE = "Unavailable"

EX_HOLD = "Hold"
EX_TRIM = "Trim"
EX_EXIT = "Exit Review"

PA_ACCUMULATE = "Accumulate"
PA_HOLD = "Hold"
PA_HOLD_NO_NEW = "Hold / No New Money"
PA_TRIM = "Trim"
PA_EXIT = "Exit Review"

# action -> (group key, group title, sort priority). Lower priority sorts first:
# the positions that need a decision come before the ones that do not.
GROUPS = {
    PA_EXIT: ("exit_review", "Exit Review", 0),
    PA_TRIM: ("trim_review", "Trim / Risk Review", 1),
    PA_ACCUMULATE: ("consider_adding", "Consider Adding", 2),
    PA_HOLD_NO_NEW: ("do_not_add", "Do Not Add", 3),
    PA_HOLD: ("hold", "Hold", 4),
    UNAVAILABLE: ("unavailable", "Analysis Unavailable", 5),
}
GROUP_ORDER = [v[0] for v in sorted(GROUPS.values(), key=lambda g: g[2])]
GROUP_TITLES = {v[0]: v[1] for v in GROUPS.values()}

_HOLDER = {"HOLD": EX_HOLD, "TRIM": EX_TRIM, "EXIT": EX_EXIT}
_NEW_MONEY = {"ELIGIBLE": NM_ACCUMULATE, "WATCH": NM_HOLD, "AVOID": NM_AVOID}

# Book-wide limitation raised when the account totals do not reconcile.
ACCOUNT_UNRECONCILED = "Account data has not fully reconciled"

# What KIND of reason drives an action, so a data gap is never read as a view
# on the stock: investment / risk / valuation / concentration / portfolio_limit
# are judgements; missing_evidence / account_data are analysis limitations.
_KIND_BY_CATEGORY = {
    "score": "investment", "technical": "investment", "reward_risk": "investment",
    "insider": "investment", "consistency": "investment", "risk": "risk",
    "valuation": "valuation", "missing_data": "missing_evidence", "sizing": "portfolio_limit",
}
_KIND_BY_CODE = {
    "POSITION_AT_CAP": "portfolio_limit", "SECTOR_AT_CAP": "portfolio_limit",
    "CORRELATED_CLUSTER": "portfolio_limit", "NO_BUYING_POWER": "portfolio_limit",
    "EARNINGS_SOON": "portfolio_limit", "RISK_BUDGET_FULL": "portfolio_limit",
    "BUYING_POWER_UNKNOWN": "account_data", "ACCOUNT_DATA_UNRECONCILED": "account_data",
    "PORTFOLIO_DATA_INCOMPLETE": "missing_evidence", "CONCENTRATION": "concentration",
}
LIMITATION_KINDS = ("missing_evidence", "account_data")


def reason_kind(code: str) -> str:
    if code in _KIND_BY_CODE:
        return _KIND_BY_CODE[code]
    category = BLOCKERS.get(code, ("other",))[0]
    return _KIND_BY_CATEGORY.get(category, "investment")


@dataclass
class PositionContext:
    """Facts about one holding in this portfolio (broker + market data)."""
    symbol: str
    quantity: float
    value: Optional[float]
    weight: Optional[float]                 # of total portfolio value
    price: Optional[float] = None
    average_cost: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    sector: str = ""
    sector_weight: Optional[float] = None
    correlated_with: List[str] = field(default_factory=list)
    cluster_weight: Optional[float] = None  # this name + its highly correlated holdings
    earnings_date: Optional[str] = None
    tax_lots: Optional[List[Dict[str, Any]]] = None


@dataclass
class PortfolioContext:
    total_value: Optional[float]
    buying_power: Optional[float]
    stop_risk_pct: Optional[float]
    policy: PortfolioPolicy = field(default_factory=PortfolioPolicy)
    today: date = field(default_factory=date.today)
    incomplete_reasons: List[str] = field(default_factory=list)


@dataclass
class Constraint:
    code: str
    text: str
    remedy: str
    source: str = "portfolio"   # "portfolio" | "quanta"

    def to_dict(self) -> Dict[str, str]:
        return dict(asdict(self), kind=reason_kind(self.code))


@dataclass
class PositionGuidance:
    symbol: str
    status: str                             # "ok" | "unavailable"
    stance: str                             # label
    stance_code: str                        # BULLISH / NEUTRAL / BEARISH / NONE
    score_leaning: str = ""                 # model_stance, when it differs from the stance
    new_money: str = UNAVAILABLE
    existing: str = UNAVAILABLE
    action: str = UNAVAILABLE
    group: str = "unavailable"
    priority: int = 5
    headline: str = ""
    key_reason: str = ""
    # The rule that produced ``action``: {code, kind, text}. ``key_reason`` is its text.
    basis: Dict[str, str] = field(default_factory=dict)
    next_step: str = ""
    # Conditional size, never a personalised allocation: {weight, text, basis}.
    target: Dict[str, Any] = field(default_factory=dict)
    why: List[str] = field(default_factory=list)
    supports: List[str] = field(default_factory=list)
    blocks: List[Dict[str, str]] = field(default_factory=list)
    would_change: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    horizon: str = ""
    conviction: Optional[float] = None      # the Decision's composite score
    evidence: Dict[str, Any] = field(default_factory=dict)
    tax_note: str = ""
    trace: List[str] = field(default_factory=list)   # the resolver's own steps, for audit
    impact: Dict[str, Any] = field(default_factory=dict)
    unavailable_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- helpers ---------------------------------------------------------------

def _pct(x: Optional[float], digits: int = 0) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def _money(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}"


def _dedupe(items: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for s in items:
        s = (s or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _days_until(iso: Optional[str], today: date) -> Optional[int]:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso[:10]) - today).days
    except ValueError:
        return None


def unavailable_reason(decision: Optional[Dict[str, Any]],
                       analysis_error: Optional[Dict[str, Any]]) -> str:
    """Why no guidance can be given, or "" when the Decision is usable."""
    if analysis_error:
        title = analysis_error.get("title") or analysis_error.get("error_message") or "analysis failed"
        return f"Analysis unavailable: {title}"
    if not decision:
        return "Analysis unavailable: no QUANTA decision for this holding yet."
    if decision.get("action") == "INVALID":
        blockers = decision.get("blockers") or []
        first = blockers[0]["text"] if blockers else "the data failed validation"
        return f"No valid data: {first}"
    if (decision.get("freshness") or {}).get("status") == "stale":
        return "Price data is out of date, so QUANTA is not giving a call. Refresh when the provider catches up."
    published = str(decision.get("holder_action") or "").upper()
    if published in _HOLDER:
        rule = published_holder_rule(decision)
        if rule is None or rule["action"] != published:
            return (f"Analysis unavailable: the published holder action ({_HOLDER[published]}) does not follow "
                    "from this analysis's own score and rules, so no call is shown. Re-run the analysis.")
    return ""


def published_holder_rule(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Re-run the engine's holder rule on the Decision's own score and codes."""
    try:
        action = FinalAction(str(decision.get("action") or ""))
    except ValueError:
        return None
    score = decision.get("score")
    codes = [b.get("code", "") for b in (decision.get("blockers") or [])]
    return holder_rule(action, score if isinstance(score, (int, float)) else None, codes,
                       event=decision.get("event_check") or None)


def portfolio_constraints(pos: PositionContext, ctx: PortfolioContext) -> List[Constraint]:
    """Portfolio reasons not to put new money into this holding right now."""
    p = ctx.policy
    out: List[Constraint] = []
    if pos.weight is not None and pos.weight >= p.max_single_position:
        out.append(Constraint(
            "POSITION_AT_CAP",
            f"Already {_pct(pos.weight, 1)} of the portfolio, at or over the {_pct(p.max_single_position)} single-name limit",
            f"The position falling below {_pct(p.max_single_position)} of the portfolio"))
    if pos.sector and pos.sector_weight is not None and pos.sector_weight >= p.max_sector:
        out.append(Constraint(
            "SECTOR_AT_CAP",
            f"{pos.sector} is already {_pct(pos.sector_weight)} of the portfolio (limit {_pct(p.max_sector)})",
            f"{pos.sector} exposure falling below {_pct(p.max_sector)}"))
    if pos.correlated_with and pos.cluster_weight is not None and pos.cluster_weight >= p.max_sector:
        names = ", ".join(pos.correlated_with[:3])
        out.append(Constraint(
            "CORRELATED_CLUSTER",
            f"Moves closely with {names}; together they are {_pct(pos.cluster_weight)} of the portfolio",
            f"That correlated group falling below {_pct(p.max_sector)}"))
    if ctx.buying_power is None:
        out.append(Constraint("BUYING_POWER_UNKNOWN", "Buying power could not be read from Robinhood",
                              "A successful refresh of account balances"))
    elif ctx.buying_power < p.min_add_value:
        out.append(Constraint("NO_BUYING_POWER",
                              f"Only {_money(ctx.buying_power)} of buying power available",
                              "Cash available to invest"))
    days = _days_until(pos.earnings_date, ctx.today)
    if days is not None and 0 <= days <= p.earnings_window_days:
        when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
        out.append(Constraint("EARNINGS_SOON", f"Reports earnings {when} ({pos.earnings_date[:10]})",
                              "The earnings report passing"))
    if ctx.stop_risk_pct is not None and ctx.stop_risk_pct >= p.stop_risk_budget:
        out.append(Constraint(
            "RISK_BUDGET_FULL",
            f"Loss to stops across the portfolio is already {_pct(ctx.stop_risk_pct, 1)} (budget {_pct(p.stop_risk_budget)})",
            "Portfolio loss-to-stops falling back inside the risk budget"))
    # Book-wide gaps block new money too, but they are not specific to this
    # holding: listed after the position's own reasons so the key reason names
    # what is true of THIS name (e.g. its single-name limit).
    for reason in ctx.incomplete_reasons:
        if reason == ACCOUNT_UNRECONCILED:
            out.append(Constraint("ACCOUNT_DATA_UNRECONCILED", reason,
                                  "Positions plus cash reconciling with the account total"))
        else:
            out.append(Constraint("PORTFOLIO_DATA_INCOMPLETE", reason,
                                  "Complete portfolio analysis and risk coverage"))
    return out


def tax_note(pos: PositionContext, today: date) -> str:
    """Plain facts about lots, for trims and exits. Informational, not tax advice."""
    lots = pos.tax_lots
    if lots is None:
        return "Tax lots were not available from Robinhood."
    if not lots:
        return ""
    short = long_ = 0.0
    soon: List[str] = []
    for lot in lots:
        qty = float(lot.get("quantity") or 0)
        term = lot.get("term")
        acquired = lot.get("acquired_on")
        if term not in ("short", "long") and acquired:
            try:
                term = "long" if (today - date.fromisoformat(acquired[:10])).days > 365 else "short"
            except ValueError:
                term = None
        if term == "long":
            long_ += qty
        elif term == "short":
            short += qty
            if acquired:
                try:
                    lt = date.fromisoformat(acquired[:10]) + timedelta(days=366)
                    if 0 <= (lt - today).days <= 45:
                        soon.append(f"{qty:g} shares turn long-term on {lt.isoformat()}")
                except ValueError:
                    pass
    parts = []
    if short or long_:
        parts.append(f"{long_:g} shares long-term, {short:g} short-term")
    parts.extend(soon)
    if not parts:
        return "Holding periods were not reported for these lots."
    return "; ".join(parts) + ". Informational only, not tax advice."


# --- main entry point ------------------------------------------------------

def _is_trace(line: str) -> bool:
    """Resolver steps ("Composite score 60.7 → candidate BUY") are audit, not argument."""
    return "\u2192" in line or "->" in line


def guide_position(pos: PositionContext, ctx: PortfolioContext,
                   decision: Optional[Dict[str, Any]],
                   analysis_error: Optional[Dict[str, Any]] = None,
                   impacts: Optional[Dict[str, Any]] = None,
                   case_for: Sequence[str] = (),
                   case_against: Sequence[str] = ()) -> PositionGuidance:
    """``case_for`` / ``case_against`` are the tear sheet's own bull and bear points
    for this Decision (thesis and risk warnings); they are shown, never re-scored."""
    reason = unavailable_reason(decision, analysis_error)
    if reason:
        stance_code = "NONE"
        g = PositionGuidance(
            symbol=pos.symbol, status="unavailable", stance=STANCE_LABELS[stance_code],
            stance_code=stance_code, headline="No call",
            key_reason=reason, unavailable_reason=reason,
            basis={"code": "ANALYSIS_UNAVAILABLE", "kind": "missing_evidence", "text": reason},
            next_step="Refresh to re-run the analysis. A missing analysis is not a view on the stock.",
            would_change=["A successful, current QUANTA analysis of this holding"],
        )
        g.group, g.priority = GROUPS[UNAVAILABLE][0], GROUPS[UNAVAILABLE][2]
        return g

    d = decision or {}
    p = ctx.policy
    stance_code = str(d.get("stance") or "NONE").upper()
    model_code = str(d.get("model_stance") or "NONE").upper()
    score = d.get("score")
    # Text comes from the engine's code table when the code is known, so an
    # analysis cached before a code gained its sentence still reads correctly.
    stock_blockers = [Constraint(b.get("code", ""),
                                 BLOCKERS[b["code"]][1] if b.get("code") in BLOCKERS else b.get("text", ""),
                                 BLOCKERS[b["code"]][2] if b.get("code") in BLOCKERS else b.get("remedy", ""),
                                 "quanta")
                      for b in (d.get("blockers") or [])]

    existing = _HOLDER.get(str(d.get("holder_action") or "").upper(), UNAVAILABLE)
    new_money = _NEW_MONEY.get(str(d.get("new_money") or "").upper(), UNAVAILABLE)
    held_back = portfolio_constraints(pos, ctx) if new_money == NM_ACCUMULATE else []
    if held_back:
        new_money = NM_HOLD

    concentration_trim = (existing == EX_HOLD and pos.weight is not None
                          and pos.weight >= p.trim_review_position)
    if concentration_trim:
        existing = EX_TRIM

    if existing == EX_EXIT:
        action = PA_EXIT
    elif existing == EX_TRIM:
        action = PA_TRIM
    elif existing == UNAVAILABLE:
        action = UNAVAILABLE
    elif new_money == NM_ACCUMULATE:
        action = PA_ACCUMULATE
    elif held_back or new_money == NM_AVOID or stance_code == "BULLISH" or model_code == "BULLISH":
        # A reason not to add exists beyond a plain neutral score.
        action = PA_HOLD_NO_NEW
    else:
        action = PA_HOLD

    g = PositionGuidance(
        symbol=pos.symbol, status="ok",
        stance=STANCE_LABELS.get(stance_code, stance_code.title()), stance_code=stance_code,
        score_leaning=(STANCE_LABELS.get(model_code, "") if model_code != stance_code else ""),
        new_money=new_money, existing=existing, action=action,
        conviction=score,
        horizon=(d.get("horizon") or {}).get("label", ""),
        impact=impacts or {},
    )
    g.group, _, g.priority = GROUPS[action]

    ev = d.get("evidence") or {}
    g.evidence = {
        "status": ev.get("status", "UNTESTED"),
        "text": ev.get("status_text", "Never validated"),
        "permits_recommendation": bool(ev.get("permits_recommendation")),
        "qualifier": d.get("qualifier", ""),
        "caveat": d.get("caveat", ""),
    }

    score_txt = f"score {score:.0f}" if isinstance(score, (int, float)) else "score n/a"
    weight_txt = _pct(pos.weight, 1)
    all_reasons = [r for r in (d.get("reasons") or []) if r]
    reasons = [r for r in all_reasons if not _is_trace(r)]
    g.trace = [r for r in all_reasons if _is_trace(r)]
    first_stock_block = stock_blockers[0] if stock_blockers else None
    rule = published_holder_rule(d) or {}
    band = band_for(score).replace("_", " ").lower() if isinstance(score, (int, float)) else ""

    def basis(code: str, text: str, kind: str = "") -> Dict[str, str]:
        return {"code": code, "kind": kind or reason_kind(code), "text": text}

    # Why, in the order a portfolio owner would ask it. Every key reason is the
    # text of the rule that produced the action (``rule`` or a constraint), so a
    # reason can never cite a score band the score is not in.
    why: List[str] = []
    if action == PA_EXIT and rule.get("basis") == "PENDING_ACQUISITION_SPREAD":
        ev = d.get("event_check") or {}
        deal = ev.get("deal_price")
        spread = ev.get("spread_pct")
        g.headline = "Review: the takeover spread has nearly closed"
        g.basis = basis("PENDING_ACQUISITION_SPREAD",
                        f"Pending cash acquisition at ${deal:,.2f}; {spread:.1%} of upside is left to the deal price, "
                        f"under the {_pct(_T.EVENTS.ARB_SPREAD_MIN)} minimum", "corporate_event")
        why.append(f"{pos.symbol} is being bought for cash. What remains is a merger-arbitrage position "
                   f"({spread:.1%} to the deal price), which QUANTA does not manage. {ev.get('text') or ''}".strip())
        g.next_step = "Review whether to hold to the deal close or sell now. This is a review prompt, not an order."
    elif action == PA_EXIT:
        flags = [BLOCKERS[c][1] if c in BLOCKERS else c.replace("_", " ").capitalize()
                 for c in rule.get("codes", [])]
        g.headline = "Review whether to keep this"
        g.basis = basis("SCORE_EXIT_CORROBORATED",
                        f"QUANTA {score_txt} is below {TRIM_FLOOR:.0f}, with a measured red flag: "
                        f"{flags[0][0].lower() + flags[0][1:]}", "investment")
        why.append(f"The research engine is {g.stance.lower()} on {pos.symbol} with a {score_txt}, below "
                   f"{TRIM_FLOOR:.0f}, corroborated by {len(flags)} measured red flag"
                   f"{'s' if len(flags) != 1 else ''}. This is a prompt to review the thesis, "
                   "not an instruction to sell.")
        g.next_step = "Review the thesis against the red flags. This is a review prompt, not an order."
    elif action == PA_TRIM and concentration_trim:
        g.headline = "Trim for position size"
        g.basis = basis("CONCENTRATION", f"Position is {weight_txt} of the portfolio, past the "
                                         f"{_pct(p.trim_review_position)} review level")
        why.append(f"QUANTA is still willing to hold {pos.symbol} ({score_txt}), but at {weight_txt} "
                   f"it is past the {_pct(p.trim_review_position)} review level. Trimming is about "
                   "single-name risk, not a change of view on the stock.")
        g.next_step = (f"Consider trimming toward the {_pct(p.max_single_position)} single-name limit; "
                       "the size scenarios show the effect. The limit and the "
                       f"{_pct(p.trim_review_position)} review level are QUANTA defaults, not a target set "
                       "for your goals, time horizon or tax position.")
    elif action == PA_TRIM and rule.get("basis") == "RISK_GRADE_F":
        g.headline = "Review size for risk"
        g.basis = basis("RISK_GRADE_F", "Risk grade F triggers a trim review at any score")
        why.append(f"QUANTA's {score_txt} for {pos.symbol} is in its {band} band, so this is not a "
                   "negative view of the stock. Its risk grade is F (very high volatility or drawdown), and "
                   "the engine sends every grade-F holding to a trim review because the swings are large.")
        g.next_step = "Check the position size against how large a swing you can accept."
    elif action == PA_TRIM:
        g.headline = "Consider reducing"
        g.basis = basis("SCORE_REDUCE_BAND",
                        f"QUANTA {score_txt} is in the reduce band ({TRIM_FLOOR:.0f} to {NEUTRAL_FLOOR:.0f})"
                        if band == "reduce" else
                        f"QUANTA {score_txt} is below {TRIM_FLOOR:.0f}; no measured red flag confirms an exit",
                        "investment")
        why.append(f"The evidence has turned against {pos.symbol} ({score_txt}). A holder would "
                   "normally scale down rather than wait for confirmation.")
        g.next_step = f"Review whether to scale down; re-check if the score recovers above {NEUTRAL_FLOOR:.0f}."
    elif action == PA_ACCUMULATE:
        g.headline = "Room to add"
        g.basis = basis("NEW_MONEY_ELIGIBLE", f"{g.stance} ({score_txt}) at {weight_txt} of the portfolio, "
                                              "with room under every limit", "investment")
        why.append(f"QUANTA is {g.stance.lower()} ({score_txt}) and nothing in the portfolio stands "
                   f"in the way: the position is {weight_txt} and within limits.")
        g.next_step = "Eligible to add up to QUANTA's risk-budget size; the impact preview shows the result."
    elif action == PA_HOLD_NO_NEW:
        g.headline = "Keep it, but do not add"
        first = held_back[0] if held_back else first_stock_block
        g.basis = (basis(first.code, first.text) if first is not None
                   else basis("NEW_MONEY_NOT_ELIGIBLE", f"QUANTA {score_txt}; new money is not eligible", "investment"))
        clause = f"{g.basis['text'][0].lower()}{g.basis['text'][1:]}"
        if held_back:
            lead = f"QUANTA likes the stock ({g.stance.lower()}, {score_txt}), but "
            tail = (" This is an analysis limitation, not a view on the stock."
                    if g.basis["kind"] in LIMITATION_KINDS else
                    " Adding would make the portfolio less balanced, not better.")
            why.append(f"{lead}{clause}.{tail}")
        elif first is not None:
            tail = (" This is an analysis limitation, not a negative view."
                    if g.basis["kind"] in LIMITATION_KINDS else "")
            why.append(f"Keep the position; the research engine does not support adding right now: {clause}.{tail}")
        else:
            why.append("Keep the position; the research engine does not support adding right now.")
        remedy = first.remedy if first is not None and first.remedy else "The analysis permitting new money"
        g.next_step = f"Keep the position. Do not add until: {remedy[0].lower()}{remedy[1:]}."
    elif action == PA_HOLD:
        g.headline = "No change warranted"
        g.basis = (basis(first_stock_block.code, first_stock_block.text) if first_stock_block
                   else basis("SCORE_HOLD", f"QUANTA {score_txt}, neutral", "investment"))
        why.append(f"Nothing here argues for adding or cutting: QUANTA is {g.stance.lower()} ({score_txt}) "
                   f"and the position is {weight_txt} of the portfolio.")
        g.next_step = "No change warranted."
    else:
        g.headline = "No call"
        g.basis = basis("NO_HOLDER_ACTION", "The Decision did not state a holder action", "missing_evidence")
        g.next_step = "Refresh to re-run the analysis."
    g.key_reason = g.basis["text"]
    if pos.unrealized_pnl_pct is not None:
        direction = "up" if pos.unrealized_pnl_pct >= 0 else "down"
        why.append(f"You are {direction} {abs(pos.unrealized_pnl_pct):.1f}% on a cost of "
                   f"${pos.average_cost:,.2f} a share. QUANTA's view does not depend on your entry price.")
    elif pos.average_cost is None:
        why.append("Robinhood did not report a cost basis for this position, so gain or loss is not shown.")
    event_check = d.get("event_check") or {}
    if event_check.get("status") == "not_applicable" and event_check.get("text"):
        why.append(event_check["text"])
    g.why = why

    g.supports = _dedupe(reasons + list(case_for))[:4]
    g.blocks = [c.to_dict() for c in held_back] + [c.to_dict() for c in stock_blockers]
    g.would_change = _dedupe([c.remedy for c in held_back]
                             + [c.remedy for c in stock_blockers]
                             + list(d.get("invalidation") or []))[:6]
    if action in (PA_TRIM, PA_EXIT) and not concentration_trim \
            and rule.get("basis") in ("SCORE_REDUCE_BAND", "SCORE_EXIT_CORROBORATED"):
        g.would_change = _dedupe(g.would_change + [f"Overall score recovering above {NEUTRAL_FLOOR:.0f}"])

    risks = list(d.get("risks") or []) + list(case_against)
    days = _days_until(pos.earnings_date, ctx.today)
    if days is not None and 0 <= days <= 14:
        risks.insert(0, f"Earnings on {pos.earnings_date[:10]}, {days} days away; the price can gap either way.")
    if pos.weight is not None and pos.weight >= p.max_single_position:
        risks.insert(0, f"At {weight_txt} of the portfolio, a 20% drop here costs "
                        f"{_pct(pos.weight * 0.2, 1)} of the whole account.")
    if pos.correlated_with:
        risks.append(f"Tends to move with {', '.join(pos.correlated_with[:3])}, so the diversification is less than it looks.")
    g.risks = _dedupe(risks)[:5]
    g.target = _conditional_target(action, pos, p, impacts or {})

    if action in (PA_TRIM, PA_EXIT):
        g.tax_note = tax_note(pos, ctx.today)
    return g


def _conditional_target(action: str, pos: PositionContext, p: PortfolioPolicy,
                        impacts: Dict[str, Any]) -> Dict[str, Any]:
    """A size only where a rule defines one; otherwise say that none is computed."""
    cap = p.max_single_position
    trim = impacts.get("trim_to_cap") or {}
    add = impacts.get("add") or {}
    if action in (PA_TRIM, PA_EXIT) and trim.get("weight_after") is not None:
        return {"weight": trim["weight_after"],
                "text": f"About {_pct(trim['weight_after'], 1)} if trimmed to the {_pct(cap)} single-name limit",
                "basis": f"QUANTA default limit ({_pct(cap)}), not a personal target"}
    if action == PA_ACCUMULATE and add.get("weight_after") is not None:
        return {"weight": add["weight_after"],
                "text": f"About {_pct(add['weight_after'], 1)} after adding QUANTA's risk-budget size",
                "basis": add.get("basis", "")}
    if action in (PA_TRIM, PA_EXIT):
        return {"weight": None, "text": "No target size: QUANTA does not size reductions",
                "basis": "The right size depends on your own risk tolerance; see the scenarios"}
    return {"weight": pos.weight, "text": f"Current weight, {_pct(pos.weight, 1)}", "basis": "No change indicated"}


def summarize_groups(guidance: Sequence[PositionGuidance]) -> List[Dict[str, Any]]:
    counts: Dict[str, List[str]] = {k: [] for k in GROUP_ORDER}
    for g in guidance:
        counts.setdefault(g.group, []).append(g.symbol)
    return [{"key": k, "title": GROUP_TITLES[k], "symbols": counts[k], "count": len(counts[k])}
            for k in GROUP_ORDER]
