"""Portfolio-context guidance: what one authoritative Decision means for THIS book.

This is not a second recommendation engine. It never scores a stock. For each
holding it takes the Decision exactly as the research engine published it and
answers separate questions:

* model stance        -- the Decision's own ``stance`` (Bullish/Neutral/Bearish)
* existing position   -- the Decision's ``holder_action`` (HOLD/TRIM/EXIT), plus
                         one portfolio-only rule: a holding that has grown past
                         the trim-review weight is sent to Trim for concentration.
                         A grade-F trim review reads "Review Risk": the grade says
                         the swings are large, not what the size should be.
* adding              -- may new money go in? The Decision's ``new_money``, then
                         held back by portfolio limits (size, sector, correlation,
                         earnings, cash, risk budget). Limits only ever LOWER it.

One holder label per position: "Hold" whether or not adding is blocked. Whether
adding is allowed is a separate attribute (``adding``) with the ONE rule that
binds it, so the label no longer flips with a score that has no demonstrated edge
(audit 2026-10-21, F7). Portfolio limits are evaluated for every holding, not only
for names the research engine would buy, so an over-limit position says so (F4).

The reason shown for an action is the rule that produced it: the holder rule
re-run on the Decision's own score, codes and measured red flags
(``engine.blockers.holder_rule``), or the binding new-money rule
(``Decision.primary_blocker``), never the first entry of a list that also holds
non-blocking trims (F1).

A data failure is never turned into an action. No Decision, an INVALID one, a
failed analysis, stale prices, a truncated price history or missing valuation
inputs all produce "Unavailable" with the reason, so a data gap cannot change a
call or enter the journal as one (F3).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from engine import thresholds as _T
from engine.blockers import BLOCKERS, NEUTRAL_FLOOR, TRIM_FLOOR, BULLISH_FLOOR, holder_rule, is_blocking
from engine.score_bands import band_for
from engine.types import FinalAction

from .analytics import PortfolioPolicy

# --- vocabularies ----------------------------------------------------------

STANCE_LABELS = {"BULLISH": "Bullish", "NEUTRAL": "Neutral", "BEARISH": "Bearish", "NONE": "Not rated"}

NM_ACCUMULATE = "Accumulate"
NM_HOLD = "No New Money"
NM_AVOID = "Avoid Adding"
UNAVAILABLE = "Unavailable"

EX_HOLD = "Hold"
EX_TRIM = "Trim"
EX_EXIT = "Exit Review"

PA_ACCUMULATE = "Accumulate"
PA_HOLD = "Hold"
PA_TRIM = "Trim"
PA_REVIEW_RISK = "Review Risk"
PA_EXIT = "Exit Review"
# Journal entries written before 2026-10-21 carry this action; it is now a Hold
# whose ``adding`` is blocked. Read through :func:`canonical_action`.
LEGACY_HOLD_NO_NEW = "Hold / No New Money"
PA_HOLD_NO_NEW = LEGACY_HOLD_NO_NEW

# action -> (group key, group title, sort priority). Lower priority sorts first:
# the positions that need a decision come before the ones that do not.
GROUPS = {
    PA_EXIT: ("exit_review", "Exit Review", 0),
    PA_TRIM: ("trim_review", "Trim", 1),
    PA_REVIEW_RISK: ("risk_review", "Review Risk", 2),
    PA_ACCUMULATE: ("consider_adding", "Consider Adding", 3),
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
# / fund are judgements; missing_evidence / account_data are analysis limitations.
_KIND_BY_CATEGORY = {
    "score": "investment", "technical": "investment", "reward_risk": "investment",
    "insider": "investment", "consistency": "investment", "risk": "risk",
    "valuation": "valuation", "missing_data": "missing_evidence", "sizing": "portfolio_limit",
    "evidence": "investment", "corporate_event": "investment",
}
_KIND_BY_CODE = {
    "FUND_NOT_RATED": "fund", "RISK_REVIEW_HELD": "risk",
    "POSITION_AT_CAP": "portfolio_limit", "SECTOR_AT_CAP": "portfolio_limit",
    "CORRELATED_CLUSTER": "portfolio_limit", "NO_BUYING_POWER": "portfolio_limit",
    "EARNINGS_SOON": "portfolio_limit", "RISK_BUDGET_FULL": "portfolio_limit",
    "BUYING_POWER_UNKNOWN": "account_data", "ACCOUNT_DATA_UNRECONCILED": "account_data",
    "PORTFOLIO_DATA_INCOMPLETE": "missing_evidence", "CONCENTRATION": "concentration",
    "FUND_HOLD": "fund", "SCORE_BELOW_BUY_BAND": "investment", "HOLD_CLEAR": "investment",
}
LIMITATION_KINDS = ("missing_evidence", "account_data")

# Position-specific limits come before the stock's own rule; book-wide limits
# (cash, risk budget, account data) after it, as they say nothing about THIS name.
_POSITION_LIMIT_CODES = ("POSITION_AT_CAP", "SECTOR_AT_CAP", "CORRELATED_CLUSTER", "EARNINGS_SOON")

# A holder reads a red flag against the price, not against an order's entry.
_HOLDER_FLAG_TEXT = {
    "TARGET_BELOW_ENTRY": "QUANTA's 12-month valuation target is below the price",
    "BEARISH_REVERSAL": "A high-reliability bearish reversal candle (a short-term chart signal)",
}


def reason_kind(code: str) -> str:
    if code in _KIND_BY_CODE:
        return _KIND_BY_CODE[code]
    category = BLOCKERS.get(code, ("other",))[0]
    return _KIND_BY_CATEGORY.get(category, "investment")


def canonical_action(action: Optional[str], basis_code: Optional[str] = "") -> Optional[str]:
    """The current action name for a journal entry written under the older vocabulary.

    "Hold / No New Money" became a Hold whose adding is blocked, and a grade-F
    trim review became "Review Risk"; neither is a change of call.
    """
    if action == LEGACY_HOLD_NO_NEW:
        return PA_HOLD
    if action == PA_TRIM and basis_code == "RISK_GRADE_F":
        return PA_REVIEW_RISK
    return action


def flag_text(code: str) -> str:
    if code in _HOLDER_FLAG_TEXT:
        return _HOLDER_FLAG_TEXT[code]
    return BLOCKERS[code][1] if code in BLOCKERS else code.replace("_", " ").capitalize()


@dataclass
class PositionContext:
    """Facts about one holding in this portfolio (broker + market data)."""
    symbol: str
    quantity: float
    value: Optional[float]
    weight: Optional[float]                 # of total portfolio value (cash included)
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
    is_fund: bool = False                   # ETF / mutual fund: the stock score does not rate it
    risk_share: Optional[float] = None      # share of portfolio variance (Euler contribution)
    risk_grade: str = ""


@dataclass
class PortfolioContext:
    total_value: Optional[float]
    buying_power: Optional[float]
    stop_risk_pct: Optional[float]
    policy: PortfolioPolicy = field(default_factory=PortfolioPolicy)
    today: date = field(default_factory=date.today)
    incomplete_reasons: List[str] = field(default_factory=list)
    # symbol -> the last journalled call {date, action, basis_code}, shown beside
    # a No call so a data gap reads as "no fresh call", not as a new one.
    last_calls: Dict[str, Dict[str, Any]] = field(default_factory=dict)


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
    # May new money go in, and the ONE rule that decides it:
    # {status: eligible|blocked|not_supported|unavailable, code, kind, text, remedy}.
    adding: Dict[str, str] = field(default_factory=dict)
    next_step: str = ""
    # Conditional size, never a personalised allocation: {weight, text, basis}.
    target: Dict[str, Any] = field(default_factory=dict)
    why: List[str] = field(default_factory=list)
    supports: List[str] = field(default_factory=list)
    blocks: List[Dict[str, str]] = field(default_factory=list)
    # Rules that fired but do not block new money on their own (they cap a buy).
    notes: List[Dict[str, str]] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    would_change: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    horizon: str = ""
    conviction: Optional[float] = None      # the Decision's composite score
    evidence: Dict[str, Any] = field(default_factory=dict)
    tax_note: str = ""
    trace: List[str] = field(default_factory=list)   # the resolver's own steps, for audit
    impact: Dict[str, Any] = field(default_factory=dict)
    unavailable_reason: str = ""
    last_call: Dict[str, Any] = field(default_factory=dict)

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


def _lower_first(s: str) -> str:
    """Lower-case a leading capital for mid-sentence use; leave acronyms ("QUANTA", "RSI") alone."""
    if not s or (len(s) > 1 and s[1].isupper()):
        return s
    return s[0].lower() + s[1:]


def _action_label(action: str) -> str:
    return {PA_REVIEW_RISK: "Review risk", PA_EXIT: "Exit review", UNAVAILABLE: "No call"}.get(action, action)


def unavailable_reason(decision: Optional[Dict[str, Any]],
                       analysis_error: Optional[Dict[str, Any]],
                       is_fund: bool = False) -> str:
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
    codes = set(decision.get("blocking_codes") or []) | {b.get("code") for b in (decision.get("blockers") or [])}
    if "PRICE_HISTORY_TRUNCATED" in codes or str(decision.get("history_range") or "").startswith("6mo"):
        return ("Analysis unavailable: the price provider returned only six months of history this run, so the "
                "risk grade could not be measured on its full window. Refresh later; a data gap is not a view "
                "on the stock.")
    levels = decision.get("levels") or {}
    # A pending deal or delisting anchors the price and publishes no long levels:
    # that is the call, not a gap.
    event_anchored = (decision.get("event_check") or {}).get("status") == "flagged"
    if (not is_fund and not event_anchored and "levels" in decision
            and levels.get("valuation_target") is None):
        return ("Analysis unavailable: the valuation inputs (analyst consensus and DCF) were missing this run, "
                "and the score moves without them. Refresh later; a data gap is not a view on the stock.")
    published = str(decision.get("holder_action") or "").upper()
    if published in _HOLDER:
        rule = published_holder_rule(decision)
        if rule is None or rule["action"] != published:
            return (f"Analysis unavailable: the published holder action ({_HOLDER[published]}) does not follow "
                    "from this analysis's own score and rules, so no call is shown. Re-run the analysis.")
    return ""


def published_holder_rule(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Re-run the engine's holder rule on the Decision's own score, codes and red flags."""
    try:
        action = FinalAction(str(decision.get("action") or ""))
    except ValueError:
        return None
    score = decision.get("score")
    codes = [b.get("code", "") for b in (decision.get("blockers") or [])]
    return holder_rule(action, score if isinstance(score, (int, float)) else None, codes,
                       event=decision.get("event_check") or None,
                       red_flags=list(decision.get("red_flags") or []))


def position_limits(pos: PositionContext, ctx: PortfolioContext) -> List[Constraint]:
    """Limits specific to this holding: size, sector, correlated group, earnings."""
    p = ctx.policy
    out: List[Constraint] = []
    if pos.weight is not None and pos.weight >= p.max_single_position:
        out.append(Constraint(
            "POSITION_AT_CAP",
            f"{_pct(pos.weight, 1)} of the account, over the {_pct(p.max_single_position)} single-name limit",
            f"The position falling below {_pct(p.max_single_position)} of the account"))
    if (not pos.is_fund and pos.sector and pos.sector_weight is not None
            and pos.sector_weight >= p.max_sector):
        out.append(Constraint(
            "SECTOR_AT_CAP",
            f"{pos.sector} is {_pct(pos.sector_weight, 1)} of the account (limit {_pct(p.max_sector)})",
            f"{pos.sector} exposure falling below {_pct(p.max_sector)}"))
    if pos.correlated_with and pos.cluster_weight is not None and pos.cluster_weight >= p.max_sector:
        names = ", ".join(pos.correlated_with[:3])
        out.append(Constraint(
            "CORRELATED_CLUSTER",
            f"Moves closely with {names}; together they are {_pct(pos.cluster_weight)} of the account",
            f"That correlated group falling below {_pct(p.max_sector)}"))
    days = _days_until(pos.earnings_date, ctx.today)
    if days is not None and 0 <= days <= p.earnings_window_days:
        when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
        out.append(Constraint("EARNINGS_SOON", f"Reports earnings {when} ({pos.earnings_date[:10]})",
                              "The earnings report passing"))
    return out


def book_constraints(ctx: PortfolioContext) -> List[Constraint]:
    """Book-wide reasons not to add anywhere: cash, risk budget, account data gaps."""
    p = ctx.policy
    out: List[Constraint] = []
    if ctx.buying_power is None:
        out.append(Constraint("BUYING_POWER_UNKNOWN", "Buying power could not be read from Robinhood",
                              "A successful refresh of account balances"))
    elif ctx.buying_power < p.min_add_value:
        out.append(Constraint("NO_BUYING_POWER",
                              f"Only {_money(ctx.buying_power)} of buying power available",
                              "Cash available to invest"))
    if ctx.stop_risk_pct is not None and ctx.stop_risk_pct >= p.stop_risk_budget:
        out.append(Constraint(
            "RISK_BUDGET_FULL",
            f"Loss to stops across the portfolio is already {_pct(ctx.stop_risk_pct, 1)} (budget {_pct(p.stop_risk_budget)})",
            "Portfolio loss-to-stops falling back inside the risk budget"))
    for reason in ctx.incomplete_reasons:
        if reason == ACCOUNT_UNRECONCILED:
            out.append(Constraint("ACCOUNT_DATA_UNRECONCILED", reason,
                                  "Positions plus cash reconciling with the account total"))
        else:
            out.append(Constraint("PORTFOLIO_DATA_INCOMPLETE", reason,
                                  "Complete portfolio analysis and risk coverage"))
    return out


def portfolio_constraints(pos: PositionContext, ctx: PortfolioContext) -> List[Constraint]:
    """Portfolio reasons not to put new money into this holding right now.

    Position-specific limits first, then book-wide ones, so the key reason names
    what is true of THIS name (e.g. its single-name limit)."""
    return position_limits(pos, ctx) + book_constraints(ctx)


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


def _trim_sentence(impacts: Dict[str, Any], p: PortfolioPolicy) -> str:
    """'Trim range: to the 20% review level (sell about $X) or to the 10% limit (...)'."""
    parts = []
    for key, name in (("trim_to_review", f"the {_pct(p.trim_review_position)} review level"),
                      ("trim_to_cap", f"the {_pct(p.max_single_position)} single-name limit")):
        t = impacts.get(key) or {}
        sell = -(t.get("delta_value") or 0)
        if sell > 0:
            sh = t.get("shares")
            parts.append(f"to {name} (sell about {_money(sell)}"
                         + (f", {sh:,.2f} shares" if isinstance(sh, (int, float)) else "") + ")")
    if not parts:
        return ""
    out = "Trim range: " + " or ".join(parts) + "."
    cap = impacts.get("trim_to_cap") or {}
    gain = cap.get("gain")
    if isinstance(gain, (int, float)):
        split = ""
        if isinstance(cap.get("gain_long"), (int, float)) and isinstance(cap.get("gain_short"), (int, float)):
            split = (f" ({_money(cap['gain_long'])} long-term, {_money(cap['gain_short'])} short-term if the "
                     "oldest lots go first)")
        out += f" Trimming to the limit realises about {_money(gain)} of gain{split}; informational, not tax advice."
    return out


# --- main entry point ------------------------------------------------------

def _is_trace(line: str) -> bool:
    """Resolver steps ("Composite score 60.7 → candidate BUY") are audit, not argument."""
    return "→" in line or "->" in line


def _basis(code: str, text: str, kind: str = "") -> Dict[str, str]:
    return {"code": code, "kind": kind or reason_kind(code), "text": text}


def _stock_constraints(d: Dict[str, Any]) -> List[Constraint]:
    # Text comes from the engine's code table when the code is known, so an
    # analysis cached before a code gained its sentence still reads correctly.
    return [Constraint(b.get("code", ""),
                       BLOCKERS[b["code"]][1] if b.get("code") in BLOCKERS else b.get("text", ""),
                       BLOCKERS[b["code"]][2] if b.get("code") in BLOCKERS else b.get("remedy", ""),
                       "quanta")
            for b in (d.get("blockers") or [])]


def _binding_stock_rule(d: Dict[str, Any], stock: List[Constraint]) -> Optional[Constraint]:
    """The rule that actually kept new money out: the Decision's own primary blocker
    when it is a blocking rule, else the first blocking code. A trim that only caps
    a buy at Accumulate (candle timing, grade D, a reversal) never binds."""
    pb = d.get("primary_blocker") or {}
    code = pb.get("code")
    if code and is_blocking(code):
        hit = next((c for c in stock if c.code == code), None)
        if hit is not None:
            return hit
        text = BLOCKERS[code][1] if code in BLOCKERS else pb.get("text", "")
        remedy = pb.get("remedy") or (BLOCKERS[code][2] if code in BLOCKERS else "")
        return Constraint(code, text, remedy, "quanta")
    return next((c for c in stock if is_blocking(c.code)), None)


def reward_risk_text(d: Dict[str, Any]) -> str:
    """The arithmetic behind a reward/risk block, so the owner can see what it asks for.

    Reward is measured to a 63-session target pro-rated from the 12-month valuation
    target; risk to the ATR stop. Passing therefore needs a 12-month upside of about
    minimum x stop distance x (252 / 63). Costs are ignored here (a few basis points).
    """
    lv = d.get("levels") or {}
    rr, px, stop, vt = lv.get("reward_risk"), lv.get("reference_price"), lv.get("stop"), lv.get("valuation_target")
    if not all(isinstance(x, (int, float)) for x in (rr, px, stop)) or px <= 0 or stop >= px:
        return ""
    horizon = lv.get("target_horizon_days") or _T.LEVELS.RESEARCH_HORIZON_DAYS
    full = lv.get("valuation_target_horizon_days") or _T.LEVELS.VALUATION_HORIZON_DAYS
    need = _T.RR.RESEARCH_MIN * (px - stop) / px * (full / float(horizon))
    text = (f"Reward/risk {rr:.2f}:1 to the {horizon}-session target, under the {_T.fmt_rr(_T.RR.RESEARCH_MIN)} "
            f"minimum: passing needs about {need:.0%} of 12-month upside")
    if isinstance(vt, (int, float)) and vt > 0:
        text += f", and QUANTA's 12-month target is {vt / px - 1:+.0%}"
    return text


def _adding(d: Dict[str, Any], score: Any, limits: List[Constraint], book: List[Constraint],
            binding: Optional[Constraint], is_fund: bool = False) -> Dict[str, str]:
    raw = _NEW_MONEY.get(str(d.get("new_money") or "").upper(), UNAVAILABLE)

    def blocked(c: Constraint) -> Dict[str, str]:
        text = c.text
        if c.code == "LOW_REWARD_RISK":
            text = reward_risk_text(d) or text
        return {"status": "blocked", "code": c.code, "kind": reason_kind(c.code), "text": text,
                "remedy": c.remedy}

    if raw == UNAVAILABLE:
        return {"status": "unavailable", "code": "", "kind": "missing_evidence",
                "text": "The Decision did not say whether new money may go in", "remedy": ""}
    if limits:
        return blocked(limits[0])
    if raw in (NM_HOLD, NM_AVOID) and binding is not None:
        return blocked(binding)
    if raw == NM_ACCUMULATE and book:
        return blocked(book[0])
    if raw == NM_ACCUMULATE:
        return {"status": "eligible", "code": "NEW_MONEY_ELIGIBLE", "kind": "investment",
                "text": "Within every limit", "remedy": ""}
    if is_fund:
        return {"status": "not_supported", "code": "FUND_NOT_RATED", "kind": "fund",
                "text": "QUANTA's stock score does not rate funds; adding is an allocation choice",
                "remedy": ""}
    s = f"{score:.0f}" if isinstance(score, (int, float)) else "n/a"
    if isinstance(score, (int, float)) and score >= BULLISH_FLOOR:
        # A buy-band score without a buy: the engine's own first reason, if it named one.
        pb = d.get("primary_blocker") or {}
        text = pb.get("text") or "The research engine does not support new money now"
        return {"status": "blocked", "code": pb.get("code") or "NEW_MONEY_NOT_ELIGIBLE",
                "kind": reason_kind(pb.get("code") or ""), "text": text, "remedy": pb.get("remedy") or ""}
    if isinstance(score, (int, float)) and score < NEUTRAL_FLOOR:
        text = f"QUANTA score {s} is below {NEUTRAL_FLOOR:.0f} (bearish)"
    else:
        text = f"QUANTA score {s} is below the {BULLISH_FLOOR:.0f} buy band"
    return {"status": "not_supported", "code": "SCORE_BELOW_BUY_BAND", "kind": "investment", "text": text,
            "remedy": f"Overall score reaching {BULLISH_FLOOR:.0f} or higher"}


def guide_position(pos: PositionContext, ctx: PortfolioContext,
                   decision: Optional[Dict[str, Any]],
                   analysis_error: Optional[Dict[str, Any]] = None,
                   impacts: Optional[Dict[str, Any]] = None,
                   case_for: Sequence[str] = (),
                   case_against: Sequence[str] = ()) -> PositionGuidance:
    """``case_for`` / ``case_against`` are the tear sheet's own bull and bear points
    for this Decision (thesis and risk warnings); they are shown, never re-scored."""
    last = dict(ctx.last_calls.get(pos.symbol) or {})
    reason = unavailable_reason(decision, analysis_error, pos.is_fund)
    if reason:
        stance_code = "NONE"
        g = PositionGuidance(
            symbol=pos.symbol, status="unavailable", stance=STANCE_LABELS[stance_code],
            stance_code=stance_code, headline="No call",
            key_reason=reason, unavailable_reason=reason,
            basis=_basis("ANALYSIS_UNAVAILABLE", reason, "missing_evidence"),
            next_step="Refresh to re-run the analysis. A missing analysis is not a view on the stock.",
            would_change=["A successful, current QUANTA analysis of this holding"],
            last_call=last,
        )
        if last.get("action"):
            label = _action_label(canonical_action(last.get("action"), last.get("basis_code")) or "")
            g.why = [f"The last call QUANTA made ({last.get('date')}) was {label}. It is not repeated or "
                     "changed on incomplete data."]
        g.group, g.priority = GROUPS[UNAVAILABLE][0], GROUPS[UNAVAILABLE][2]
        return g

    d = decision or {}
    p = ctx.policy
    impacts = impacts or {}
    stance_code = str(d.get("stance") or "NONE").upper()
    model_code = str(d.get("model_stance") or "NONE").upper()
    score = d.get("score")
    stock = _stock_constraints(d)
    binding = _binding_stock_rule(d, stock)
    notes = [c for c in stock if not is_blocking(c.code)]

    limits = position_limits(pos, ctx)
    book = book_constraints(ctx)
    adding = _adding(d, score, limits, book, binding, pos.is_fund)
    raw_nm = _NEW_MONEY.get(str(d.get("new_money") or "").upper(), UNAVAILABLE)
    new_money = (NM_ACCUMULATE if adding["status"] == "eligible"
                 else NM_AVOID if raw_nm == NM_AVOID else
                 UNAVAILABLE if raw_nm == UNAVAILABLE else NM_HOLD)

    rule = published_holder_rule(d) or {}
    flags = list(rule.get("flags") or [])
    existing = _HOLDER.get(str(d.get("holder_action") or "").upper(), UNAVAILABLE)
    over_review = pos.weight is not None and pos.weight >= p.trim_review_position
    concentration_trim = over_review and (existing == EX_HOLD
                                          or (existing == EX_TRIM and rule.get("basis") == "RISK_GRADE_F"))
    if concentration_trim:
        existing = EX_TRIM

    # A grade-F review stays open while the grade sits just below F (see
    # PORTFOLIO.RISK_REVIEW_HOLD_GRADE): grades near the line slip back within weeks.
    grade = str(d.get("risk_grade") or pos.risk_grade or "").upper()
    review_held = (existing == EX_HOLD and not concentration_trim
                   and grade == _T.PORTFOLIO.RISK_REVIEW_HOLD_GRADE
                   and canonical_action(last.get("action"), last.get("basis_code")) == PA_REVIEW_RISK)

    if existing == EX_EXIT:
        action = PA_EXIT
    elif concentration_trim:
        action = PA_TRIM
    elif (existing == EX_TRIM and rule.get("basis") == "RISK_GRADE_F") or review_held:
        action = PA_REVIEW_RISK
    elif existing == EX_TRIM:
        action = PA_TRIM
    elif existing == UNAVAILABLE:
        action = UNAVAILABLE
    elif adding["status"] == "eligible":
        action = PA_ACCUMULATE
    else:
        action = PA_HOLD

    g = PositionGuidance(
        symbol=pos.symbol, status="ok",
        stance=STANCE_LABELS.get(stance_code, stance_code.title()), stance_code=stance_code,
        score_leaning=(STANCE_LABELS.get(model_code, "") if model_code != stance_code else ""),
        new_money=new_money, existing=existing, action=action, adding=adding,
        conviction=score, red_flags=flags, last_call=last,
        horizon=(d.get("horizon") or {}).get("label", ""),
        impact=impacts,
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
    share_txt = _pct(pos.risk_share, 1) if pos.risk_share is not None else ""
    all_reasons = [r for r in (d.get("reasons") or []) if r]
    reasons = [r for r in all_reasons if not _is_trace(r)]
    g.trace = [r for r in all_reasons if _is_trace(r)]
    band = band_for(score).replace("_", " ").lower() if isinstance(score, (int, float)) else ""
    flag_line = "; ".join(_lower_first(flag_text(c)) for c in flags)
    adding_line = ("Adding: allowed, within every limit." if adding["status"] == "eligible" else
                   f"Adding: {'blocked' if adding['status'] == 'blocked' else 'not supported'}: "
                   f"{_lower_first(adding['text'])}." if adding.get("text") else "")

    # Why, in the order a portfolio owner would ask it. Every key reason is the
    # text of the rule that produced the action, so a reason can never cite a
    # score band the score is not in, or a rule that did not bind.
    why: List[str] = []
    if action == PA_EXIT and rule.get("basis") == "PENDING_ACQUISITION_SPREAD":
        evc = d.get("event_check") or {}
        deal = evc.get("deal_price")
        spread = evc.get("spread_pct")
        g.headline = "Review: the takeover spread has nearly closed"
        g.basis = _basis("PENDING_ACQUISITION_SPREAD",
                         f"Pending cash acquisition at ${deal:,.2f}; {spread:.1%} of upside is left to the deal price, "
                         f"under the {_pct(_T.EVENTS.ARB_SPREAD_MIN)} minimum", "corporate_event")
        why.append(f"{pos.symbol} is being bought for cash. What remains is a merger-arbitrage position "
                   f"({spread:.1%} to the deal price), which QUANTA does not manage. {evc.get('text') or ''}".strip())
        g.next_step = "Review whether to hold to the deal close or sell now. This is a review prompt, not an order."
    elif action == PA_EXIT:
        codes = rule.get("codes", []) or flags
        g.headline = "Review whether to keep this"
        g.basis = _basis("SCORE_EXIT_CORROBORATED",
                         f"QUANTA {score_txt} is below {TRIM_FLOOR:.0f}, with a measured red flag: "
                         f"{_lower_first(flag_text(codes[0]))}" if codes else
                         f"QUANTA {score_txt} is below {TRIM_FLOOR:.0f}", "investment")
        why.append(f"The research engine's {score_txt} for {pos.symbol} is below {TRIM_FLOOR:.0f}, corroborated by "
                   f"{len(codes)} measured red flag{'s' if len(codes) != 1 else ''}"
                   f"{': ' + flag_line if flag_line else ''}. This is a prompt to review the thesis, "
                   "not an instruction to sell.")
        g.next_step = "Review the thesis against the red flags. This is a review prompt, not an order."
    elif action == PA_TRIM and concentration_trim:
        g.headline = "Trim for position size"
        g.basis = _basis("CONCENTRATION", f"{weight_txt} of the account, past the "
                                          f"{_pct(p.trim_review_position)} review level")
        why.append(f"At {weight_txt} of the account, {pos.symbol} is past the {_pct(p.trim_review_position)} "
                   "review level. Trimming is about single-name risk"
                   + (f" (it carries {share_txt} of the portfolio's risk)" if share_txt else "") + ".")
        if flags:
            why.append(f"QUANTA's own analysis also argues against the size: {flag_line}.")
        if rule.get("basis") == "RISK_GRADE_F":
            why.append("Its risk grade is F as well (very high volatility or drawdown).")
        trim = _trim_sentence(impacts, p)
        g.next_step = ((trim + " ") if trim else
                       f"Consider trimming toward the {_pct(p.max_single_position)} single-name limit. ") + \
            (f"The {_pct(p.trim_review_position)} review level and the {_pct(p.max_single_position)} limit are "
             "QUANTA defaults, not a target set for your goals, time horizon or tax position.")
    elif action == PA_REVIEW_RISK and review_held:
        g.headline = "Risk review still open"
        g.basis = _basis("RISK_REVIEW_HELD", f"Risk grade {grade} after F; the review stays open until grade C or "
                                             f"better; {weight_txt} of the account"
                                             + (f", {share_txt} of portfolio risk" if share_txt else ""), "risk")
        why.append(f"{pos.symbol} was graded F and is now {grade}, one step below. Grades this close to the line often "
                   "slip back within weeks, and names just out of F have kept swinging almost as much as F names, "
                   "so the size review stays open until the grade reaches C or better.")
        g.next_step = "Check the position size, together with the other grade-F holdings, against how large a swing you can accept."
    elif action == PA_REVIEW_RISK:
        g.headline = "Review size for risk"
        g.basis = _basis("RISK_GRADE_F", f"Risk grade F (very high volatility or drawdown); {weight_txt} of the account"
                                         + (f", {share_txt} of portfolio risk" if share_txt else ""), "risk")
        why.append(f"QUANTA's {score_txt} for {pos.symbol} is in its {band} band, so this is not a negative view of "
                   "the stock. Its risk grade is F, and every grade-F holding gets a size review because the swings "
                   "are large. The grade does not say what the size should be.")
        if share_txt:
            why.append(f"At {weight_txt} of the account it carries {share_txt} of the portfolio's day-to-day risk.")
        g.next_step = "Check the position size, together with the other grade-F holdings, against how large a swing you can accept."
    elif action == PA_TRIM:
        g.headline = "Consider reducing"
        g.basis = _basis("SCORE_REDUCE_BAND",
                         f"QUANTA {score_txt} is in the reduce band ({TRIM_FLOOR:.0f} to {NEUTRAL_FLOOR:.0f})"
                         if band == "reduce" else
                         f"QUANTA {score_txt} is below {TRIM_FLOOR:.0f}; no measured red flag confirms an exit",
                         "investment")
        why.append(f"The research engine's score for {pos.symbol} has turned down ({score_txt}). The score has no "
                   "demonstrated predictive edge, so treat this as a review, not a forecast.")
        g.next_step = f"Review whether to scale down; re-check if the score recovers above {NEUTRAL_FLOOR:.0f}."
    elif action == PA_ACCUMULATE:
        g.headline = "Room to add"
        g.basis = _basis("NEW_MONEY_ELIGIBLE", f"{g.stance} ({score_txt}) at {weight_txt} of the account, "
                                               "with room under every limit", "investment")
        why.append(f"QUANTA is {g.stance.lower()} ({score_txt}) and nothing in the portfolio stands "
                   f"in the way: the position is {weight_txt} and within limits.")
        g.next_step = "Eligible to add up to QUANTA's risk-budget size; the impact preview shows the result."
    elif action == PA_HOLD:
        limit = next((c for c in limits if c.code in ("POSITION_AT_CAP", "SECTOR_AT_CAP", "CORRELATED_CLUSTER")), None)
        if flags:
            g.headline = "Keep, with a red flag to watch"
            g.basis = _basis(flags[0], flag_text(flags[0])
                             + (f" (and {len(flags) - 1} more red flag{'s' if len(flags) > 2 else ''})"
                                if len(flags) > 1 else ""))
            why.append(f"QUANTA measured {len(flags)} red flag{'s' if len(flags) != 1 else ''} on {pos.symbol}: "
                       f"{flag_line}. With a score at or above {NEUTRAL_FLOOR:.0f} that is a reason to watch the "
                       f"thesis, not to sell; a score below {TRIM_FLOOR:.0f} as well would make it an exit review.")
            g.next_step = "Keep the position and re-check the thesis against the red flag."
        elif limit is not None:
            g.headline = "Keep, but do not add"
            g.basis = _basis(limit.code, limit.text)
            why.append(f"Keep {pos.symbol}; no measured red flag argues for cutting it, but {_lower_first(limit.text)}.")
            g.next_step = f"Keep the position. Do not add until: {_lower_first(limit.remedy)}."
        elif pos.is_fund:
            g.headline = "Core fund: no change indicated"
            g.basis = _basis("FUND_HOLD", "Fund within portfolio limits; QUANTA's stock score does not rate funds")
            why.append(f"{pos.symbol} is a fund. QUANTA's research score is built for single companies, so it is "
                       f"not used here; the fund is {weight_txt} of the account and within limits.")
            g.next_step = "No change indicated. Its role is allocation (core or satellite), not a stock call."
        elif "red_flags" in d:
            g.headline = "No change indicated"
            g.basis = _basis("HOLD_CLEAR", "No measured red flag; within portfolio limits")
            why.append(f"Nothing measured argues for cutting {pos.symbol}: no red flag, and the position is "
                       f"{weight_txt} of the account, within limits.")
            g.next_step = "No change indicated."
        else:
            # An analysis cached before red flags were measured at every score.
            g.headline = "No change indicated"
            g.basis = _basis("HOLD_CLEAR", "Within portfolio limits (red flags not checked by this analysis; refresh)")
            why.append(f"{pos.symbol} is {weight_txt} of the account, within limits. This analysis predates the "
                       "red-flag check for holders, so refresh before relying on it.")
            g.next_step = "Refresh to run the holder red-flag check."
        if adding_line and adding.get("code") != g.basis.get("code"):
            why.append(adding_line)
    else:
        g.headline = "No call"
        g.basis = _basis("NO_HOLDER_ACTION", "The Decision did not state a holder action", "missing_evidence")
        g.next_step = "Refresh to re-run the analysis."
    g.key_reason = g.basis["text"]
    if pos.unrealized_pnl_pct is not None and pos.average_cost is not None:
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
    blocks: List[Constraint] = list(limits)
    if binding is not None:
        blocks.append(binding)
    blocks += [c for c in stock if is_blocking(c.code) and c is not binding]
    blocks += book
    seen = set()
    g.blocks = []
    for c in blocks:
        if c.code not in seen:
            seen.add(c.code)
            g.blocks.append(c.to_dict())
    g.notes = [c.to_dict() for c in notes]
    g.would_change = _dedupe([adding.get("remedy", "")] + [c["remedy"] for c in g.blocks]
                             + list(d.get("invalidation") or []))[:6]
    if action in (PA_TRIM, PA_EXIT) and not concentration_trim \
            and rule.get("basis") in ("SCORE_REDUCE_BAND", "SCORE_EXIT_CORROBORATED"):
        g.would_change = _dedupe(g.would_change + [f"Overall score recovering above {NEUTRAL_FLOOR:.0f}"])

    risks = list(d.get("risks") or []) + list(case_against)
    days = _days_until(pos.earnings_date, ctx.today)
    if days is not None and 0 <= days <= _T.PORTFOLIO.EARNINGS_HORIZON_DAYS:
        when = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} away"
        risks.insert(0, f"Earnings on {pos.earnings_date[:10]}, {when}; the price can gap either way.")
    if pos.weight is not None and pos.weight >= p.max_single_position:
        risks.insert(0, f"At {weight_txt} of the account, a 20% drop here costs "
                        f"{_pct(pos.weight * 0.2, 1)} of the whole account.")
    if pos.correlated_with:
        risks.append(f"Tends to move with {', '.join(pos.correlated_with[:3])}, so the diversification is less than it looks.")
    g.risks = _dedupe(risks)[:5]
    g.target = _conditional_target(action, pos, p, impacts)

    if action in (PA_TRIM, PA_EXIT, PA_REVIEW_RISK):
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
    if action in (PA_TRIM, PA_EXIT, PA_REVIEW_RISK):
        return {"weight": None, "text": "No target size: QUANTA does not size reductions",
                "basis": "The right size depends on your own risk tolerance; see the scenarios"}
    return {"weight": pos.weight, "text": f"Current weight, {_pct(pos.weight, 1)}", "basis": "No change indicated"}


def summarize_groups(guidance: Sequence[PositionGuidance]) -> List[Dict[str, Any]]:
    counts: Dict[str, List[str]] = {k: [] for k in GROUP_ORDER}
    for g in guidance:
        counts.setdefault(g.group, []).append(g.symbol)
    return [{"key": k, "title": GROUP_TITLES[k], "symbols": counts[k], "count": len(counts[k])}
            for k in GROUP_ORDER]


def _names(items: Sequence[str], limit: int = 4) -> str:
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1] if items else ""
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def attention_items(positions: Sequence[Dict[str, Any]], metrics: Dict[str, Any],
                    policy: PortfolioPolicy, today: date) -> List[Dict[str, Any]]:
    """The few portfolio-level facts a holder should see before the rows.

    ``positions`` are the view's rows (symbol, broker.weight, market.earnings_date,
    guidance, risk_share). Each item names its holdings so the page can filter to
    them; at most ``PORTFOLIO.ATTENTION_MAX`` are returned, most structural first.
    """
    out: List[Dict[str, Any]] = []
    weights = {r["symbol"]: (r.get("broker") or {}).get("weight") for r in positions}
    sectors = [s for s in (metrics.get("sectors") or [])
               if s.get("sector") not in ("Cash", "Unclassified") and not s.get("fund")]
    over = [s for s in sectors if s.get("weight") is not None and s["weight"] >= policy.max_sector]
    if over:
        s = max(over, key=lambda x: x["weight"])
        out.append({"code": "SECTOR_OVER_LIMIT", "tone": "warn", "symbols": list(s.get("symbols") or []),
                    "text": f"{s['sector']} is {_pct(s['weight'], 1)} of the account (QUANTA limit {_pct(policy.max_sector)})"})

    risky = [r for r in positions if (r.get("guidance") or {}).get("action") == PA_REVIEW_RISK
             or ((r.get("guidance") or {}).get("basis") or {}).get("code") == "RISK_GRADE_F"]
    if len(risky) >= 2:
        w = sum(weights.get(r["symbol"]) or 0 for r in risky)
        shares = [r.get("risk_share") for r in risky]
        text = f"Holdings under a grade-F risk review ({_names([r['symbol'] for r in risky])}) are {_pct(w, 1)} of the account"
        if all(isinstance(x, (int, float)) for x in shares):
            text += f" and {_pct(sum(shares), 1)} of portfolio risk"
        out.append({"code": "RISK_GRADE_F_GROUP", "tone": "warn", "symbols": [r["symbol"] for r in risky], "text": text})

    soon = []
    for r in positions:
        days = _days_until((r.get("market") or {}).get("earnings_date"), today)
        if days is not None and 0 <= days <= policy.earnings_window_days:
            soon.append((days, r["symbol"]))
    if soon:
        soon.sort()
        today_syms = [s for d, s in soon if d == 0]
        later = [s for d, s in soon if d > 0]
        w = sum(weights.get(s) or 0 for _, s in soon)
        bits = []
        if today_syms:
            bits.append(f"{_names(today_syms)} today")
        if later:
            bits.append(f"{_names(later)} within {policy.earnings_window_days} days")
        out.append({"code": "EARNINGS_SOON", "tone": "info", "symbols": [s for _, s in soon],
                    "text": f"Earnings: {'; '.join(bits)} ({_pct(w, 1)} of the account)"})

    capped = [(r["symbol"], weights.get(r["symbol"])) for r in positions
              if weights.get(r["symbol"]) is not None
              and policy.max_single_position <= weights[r["symbol"]] < policy.trim_review_position]
    if capped:
        capped.sort(key=lambda x: -x[1])
        listed = _names([f"{s} ({_pct(w, 1)})" for s, w in capped])
        verb = "is" if len(capped) == 1 else "are"
        out.append({"code": "OVER_SINGLE_LIMIT", "tone": "info", "symbols": [s for s, _ in capped],
                    "text": f"{listed} {verb} over the {_pct(policy.max_single_position)} single-name limit"})
    return out[:_T.PORTFOLIO.ATTENTION_MAX]
