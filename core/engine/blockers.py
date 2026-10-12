"""What a Decision means, stated separately for each reader.

One action label cannot say three different things at once. WATCHLIST used to
stand for "neutral score", "bearish score, you do not own it" and "bullish
score, blocked by risk" alike. The Decision therefore publishes, next to its
one authoritative ``action``:

* ``model_stance``  -- direction of the alpha score before any gate
                       (BULLISH / NEUTRAL / BEARISH / NONE)
* ``new_money``     -- may a non-holder open a position now?
                       (ELIGIBLE / WATCH / AVOID / NONE)
* ``holder_action`` -- what the same evidence implies for someone who already
                       holds it (HOLD / TRIM / EXIT / N/A). Research never knows
                       holdings, so this is conditional: "if you hold it".
* ``primary_blocker`` -- the first rule that kept a positive score from new
                       money, with a stable code, a category and the condition
                       that would clear it.

Every value is derived here from rule codes and the score band, so no surface
(web UI, CLI, Streamlit) re-derives a verdict from prose.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Dict, Iterable, Iterator, List, Optional

from . import thresholds as _T

from .types import FinalAction

# Score bands, read from the resolver's single band table.
from .score_bands import band_floor as _band_floor  # noqa: E402

BULLISH_FLOOR = _band_floor("ACCUMULATE")   # ACCUMULATE and above
NEUTRAL_FLOOR = _band_floor("HOLD")         # HOLD band
TRIM_FLOOR = _band_floor("REDUCE")          # REDUCE band (below this: SELL / STRONG SELL)

# code -> (category, plain statement, what would clear it)
# Templates, formatted from engine.thresholds every time they are read, so a
# reason or remedy can never quote a number the rule does not use (audit F9).
_BLOCKER_TEMPLATES: Dict[str, tuple] = {
    "HOLD_NOT_OWNED": ("score", "Overall score is in the neutral band ({hold}–{acc})",
                       "Overall score reaching {acc} or higher"),
    "SCORE_BEARISH": ("score", "Overall score is below {hold} (bearish)",
                      "Overall score recovering above {hold}"),
    "LOW_SCORE": ("score", "Overall score is below the buy minimum",
                  "Overall score reaching the buy minimum"),
    "TARGET_BELOW_ENTRY": ("valuation", "12-month valuation target is at or below the entry",
                           "A valuation target above the entry price"),
    "ANALYST_CONSENSUS_DOWNSIDE": ("valuation", "Analyst consensus target implies more than {analyst_down}% downside",
                                   "Consensus target back within {analyst_down}% of the price or above it"),
    "ANALYST_DOWNSIDE": ("valuation", "Analyst consensus target is more than {analyst_trim}% below the entry",
                         "Consensus target within {analyst_trim}% of the entry or above it"),
    "DCF_OVERVALUATION": ("valuation", "Price is more than {dcf}% above the DCF value",
                          "Price within {dcf}% of the DCF value"),
    "INVALID_TARGET": ("missing_data",
                       "No valuation target: no analyst consensus or valid DCF for this instrument",
                       "An analyst consensus target or a valid DCF"),
    "INVALID_STOP": ("missing_data", "No usable stop level", "A valid stop below the entry"),
    "STOP_ABOVE_ENTRY": ("technical", "Stop is at or above the entry", "A valid stop below the entry"),
    "RISK_GRADE_F": ("risk", "Risk grade F (very high volatility/drawdown) allows no new money",
                     "Risk grade improving to D or better"),
    "RISK_GRADE_D_CAP": ("risk", "Risk grade D caps a fresh buy at Accumulate",
                         "Risk grade improving to C or better"),
    "RISK_GRADE_D_HOLD": ("risk", "Risk grade D limits new money to a reduced Accumulate size",
                          "Risk grade improving to C or better"),
    "LOW_REWARD_RISK": ("reward_risk", "Research reward/risk to the horizon target is below the {research_rr} minimum",
                        "Research reward/risk of at least {research_rr} (a better entry or a higher target)"),
    "SCANNER_LOW_RR": ("reward_risk", "Scanner reward/risk on its own levels is below the {scanner_rr} minimum",
                       "Scanner reward/risk of at least {scanner_rr} (a better entry or a higher target)"),
    "PENDING_ACQUISITION": ("corporate_event", "Pending acquisition: the price is anchored to the deal terms",
                            "The deal being terminated (the name is then re-scored as a stock)"),
    "DELISTING": ("corporate_event", "The listing is ending (delisting filed with the SEC)", "None while delisting"),
    "POSSIBLE_PENDING_DEAL": ("corporate_event", "Daily range collapsed with no deal filing found; possible pending deal",
                              "The range normalising, or a filing that explains it"),
    "EVENT_CHECK_UNAVAILABLE": ("missing_data", "The corporate-event check could not be completed",
                                "SEC EDGAR answering (set QUANTA_SEC_CONTACT)"),
    "EVENT_CHECK_NOT_COVERED": ("missing_data", "No corporate-event source covers this exchange's listings",
                                "None: names on exchanges no event source covers get no Buy call (owner decision)"),
    "RESEARCH_DECISION_CAP": ("consistency",
                              "The Research Decision for this name is more cautious than the scan",
                              "The Research Decision permitting new money"),
    "DECISION_CORE_ERROR": ("missing_data", "The decision rules could not be evaluated",
                            "A complete re-run of the analysis"),
    "HEAVY_INSIDER_SELLING": ("insider", "Material net insider selling in the last 90 days",
                              "Insider selling subsiding below the materiality threshold"),
    "BUY_NOW_NO": ("technical", "Candle timing says do not buy today", "A confirming entry signal"),
    "TIMING_WAIT": ("technical", "Entry timing says wait", "A confirming entry signal"),
    "BEARISH_REVERSAL": ("technical", "High-reliability bearish reversal pattern", "The pattern failing or a new higher low"),
    "RSI_EXHAUSTION": ("technical", "RSI above {rsi_exh} with a bearish MACD (exhaustion)",
                       "RSI cooling below {rsi_exh} or MACD turning up"),
    "TARGET_UPSIDE_THIN": ("valuation", "Valuation target upside is under {thin}%",
                           "Valuation target more than {thin}% above the price"),
    "STRONG_BUY_UNCONFIRMED": ("technical", "Fewer than {confirm} confirmations for a Strong Buy",
                               "{confirm} or more confirming signals"),
    "STRONG_BUY_FOLDED": ("evidence", "Shown as Buy: a Strong Buy needs a validated model",
                          "Forward evidence that validates the model"),
    "STALE_PRICE_DATA": ("missing_data", "Latest price bar is out of date", "A current price bar"),
    "PRELIMINARY_FAST_MODE": ("missing_data", "Fast preview: news, insider and catalyst checks skipped",
                              "Running the full analysis"),
    "SIZE_NOT_EXECUTABLE": ("sizing", "No position fits the risk budget at this account size",
                            "A larger account or a tighter stop"),
    "SIGNAL_CONFIRMATION_NOT_MET": ("technical", "The signal generator's confirmations were not met",
                                    "Technical confirmations aligning with the score"),
    # Provider data statuses (engine.data_status), upper-cased as codes.
    "INVALID_TICKER": ("missing_data", "Symbol not recognised by the data provider", "A valid symbol"),
    "INSUFFICIENT_HISTORY": ("missing_data", "Not enough price history to analyse", "More trading history"),
    "PROVIDER_RATE_LIMITED": ("missing_data", "Data provider rate-limited the request", "Retrying shortly"),
    "PROVIDER_DATA_UNAVAILABLE": ("missing_data", "Data provider returned no usable data", "Provider data returning"),
    "STALE_DATA": ("missing_data", "Latest price bar is out of date", "A current price bar"),
    "INVALID_INPUTS": ("missing_data", "Ticker failed the data-validation gate", "Valid market data"),
    "INVALID_PRICE": ("missing_data", "No valid current price", "A valid current price"),
}


def _template_values() -> Dict[str, str]:
    return {
        "hold": f"{_band_floor('HOLD'):g}",
        "acc": f"{_band_floor('ACCUMULATE'):g}",
        "analyst_down": f"{abs(_T.RESEARCH.ANALYST_DOWNSIDE_LIMIT_PCT):g}",
        "analyst_trim": f"{_T.LADDER.ANALYST_DOWNSIDE_TRIM_PCT:g}",
        "dcf": f"{_T.LADDER.DCF_OVERVALUATION_CAP_PCT:g}",
        "research_rr": _T.fmt_rr(_T.RR.RESEARCH_MIN),
        "scanner_rr": _T.fmt_rr(_T.RR.SCANNER_MIN),
        "rsi_exh": f"{_T.RESEARCH.RSI_EXHAUSTION:g}",
        "thin": f"{_T.RESEARCH.TARGET_UPSIDE_THIN_PCT:g}",
        "confirm": f"{_T.RESEARCH.STRONG_CONFIRMATIONS:g}",
    }


class _BlockerTable(Mapping):
    """``code -> (category, text, remedy)`` with the numbers filled in at read time."""

    def __getitem__(self, code: str) -> tuple:
        category, text, remedy = _BLOCKER_TEMPLATES[code]
        vals = _template_values()
        return category, text.format(**vals), remedy.format(**vals)

    def __iter__(self) -> Iterator[str]:
        return iter(_BLOCKER_TEMPLATES)

    def __len__(self) -> int:
        return len(_BLOCKER_TEMPLATES)


BLOCKERS: Mapping = _BlockerTable()

# Rules that on their own move a funded candidate out of the funded tiers.
# Trims (D cap, timing, reversal, DCF, thin upside) only cap at Accumulate.
_BLOCKING = {
    "INVALID_INPUTS", "INVALID_PRICE", "INVALID_STOP", "INVALID_TARGET",
    "STOP_ABOVE_ENTRY", "TARGET_BELOW_ENTRY", "ANALYST_CONSENSUS_DOWNSIDE", "RSI_EXHAUSTION",
    "RISK_GRADE_F", "HEAVY_INSIDER_SELLING", "LOW_REWARD_RISK", "LOW_SCORE",
    "STALE_PRICE_DATA", "SIZE_NOT_EXECUTABLE", "SIGNAL_CONFIRMATION_NOT_MET",
    "INVALID_TICKER", "INSUFFICIENT_HISTORY", "PROVIDER_RATE_LIMITED",
    "PROVIDER_DATA_UNAVAILABLE", "STALE_DATA", "RESEARCH_DECISION_CAP", "DECISION_CORE_ERROR",
    "PENDING_ACQUISITION", "DELISTING", "POSSIBLE_PENDING_DEAL", "EVENT_CHECK_UNAVAILABLE",
    "EVENT_CHECK_NOT_COVERED",
}

_FUNDED = {FinalAction.STRONG_BUY, FinalAction.BUY, FinalAction.ACCUMULATE}


def model_stance(composite_score: Optional[float]) -> str:
    if composite_score is None:
        return "NONE"
    if composite_score >= BULLISH_FLOOR:
        return "BULLISH"
    if composite_score >= NEUTRAL_FLOOR:
        return "NEUTRAL"
    return "BEARISH"


def new_money(action: FinalAction) -> str:
    if action in _FUNDED:
        return "ELIGIBLE"
    if action is FinalAction.AVOID:
        return "AVOID"
    if action is FinalAction.INVALID:
        return "NONE"
    return "WATCH"


# Measured, company- or price-specific reasons that can corroborate an exit.
# A low composite on its own is an unvalidated heuristic, not grounds to tell
# someone to sell a real holding (taxes, costs, a thesis the score cannot see).
_EXIT_CORROBORATION = {
    "TARGET_BELOW_ENTRY", "ANALYST_CONSENSUS_DOWNSIDE", "ANALYST_DOWNSIDE", "DCF_OVERVALUATION",
    "HEAVY_INSIDER_SELLING", "BEARISH_REVERSAL",
}
# Measured risk that warrants trimming a holding whatever the score says.
_TRIM_RISK = {"RISK_GRADE_F"}


def holder_rule(action: FinalAction, composite_score: Optional[float],
                codes: Iterable[str] = (), event: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """The holder action AND the rule that produced it, so a reason can never
    cite a band the score is not in.

    ``basis`` is one of PENDING_ACQUISITION_SPREAD, SCORE_EXIT_CORROBORATED,
    SCORE_REDUCE_BAND, RISK_GRADE_F, SCORE_HOLD or NO_DECISION; ``codes`` are
    the rule codes that fired with it (the corroborating red flags for an exit).

    ``event`` is the corporate-event check (engine.corporate_events). A holder
    of a name under a pending cash acquisition whose remaining spread to the deal
    price is below ``EVENTS.ARB_SPREAD_MIN`` gets an exit review: what is left is
    a merger-arbitrage position, which is out of scope (owner decision Q2). An
    unknown spread never produces an exit.
    """
    codes = set(codes or ())
    if event and "PENDING_ACQUISITION" in codes:
        spread = event.get("spread_pct")
        if isinstance(spread, (int, float)) and spread < _T.EVENTS.ARB_SPREAD_MIN:
            return {"action": "EXIT", "basis": "PENDING_ACQUISITION_SPREAD", "codes": ["PENDING_ACQUISITION"]}
    if action is FinalAction.INVALID or composite_score is None:
        return {"action": "N/A", "basis": "NO_DECISION", "codes": []}
    red_flags = sorted(codes & _EXIT_CORROBORATION)
    if composite_score < TRIM_FLOOR and red_flags:
        return {"action": "EXIT", "basis": "SCORE_EXIT_CORROBORATED", "codes": red_flags}
    if composite_score < NEUTRAL_FLOOR:
        return {"action": "TRIM", "basis": "SCORE_REDUCE_BAND", "codes": []}
    if codes & _TRIM_RISK:
        return {"action": "TRIM", "basis": "RISK_GRADE_F", "codes": sorted(codes & _TRIM_RISK)}
    return {"action": "HOLD", "basis": "SCORE_HOLD", "codes": []}


def holder_action(action: FinalAction, composite_score: Optional[float],
                  codes: Iterable[str] = ()) -> str:
    """What the evidence implies for an existing holder (conditional).

    EXIT needs a bearish score AND at least one measured red flag; a bearish
    score alone is a TRIM review. A risk grade of F is a TRIM review even on a
    neutral or bullish score.
    """
    return str(holder_rule(action, composite_score, codes)["action"])


def blocker_info(code: str, event: Optional[Mapping] = None) -> Dict[str, str]:
    """One rule's category, text and remedy. When the rule is the corporate-event
    check, its own reason-specific fix (``EventCheck.fix``) is the remedy: a rate
    limit and a missing SEC contact need different actions (I1)."""
    category, text, remedy = BLOCKERS.get(code, ("other", code.replace("_", " ").capitalize(), ""))
    if event and event.get("code") == code and event.get("fix"):
        remedy = str(event["fix"])
    return {"code": code, "category": category, "text": text, "remedy": remedy}


def primary_blocker(action: FinalAction, composite_score: Optional[float],
                    codes: Iterable[str], event: Optional[Mapping] = None) -> Optional[Dict[str, str]]:
    """The first rule that kept this Decision from new money, if any."""
    if action in _FUNDED:
        return None
    codes = list(codes or [])
    for code in codes:
        if code in _BLOCKING:
            return blocker_info(code, event)
    stance = model_stance(composite_score)
    if stance == "BEARISH":
        return blocker_info("SCORE_BEARISH")
    if stance == "NEUTRAL":
        return blocker_info("HOLD_NOT_OWNED")
    return blocker_info(codes[0], event) if codes else None


def action_contract(action: FinalAction, composite_score: Optional[float],
                    codes: Iterable[str] = (), holdings_known: bool = False,
                    event: Optional[Mapping] = None) -> Dict[str, object]:
    """The published per-reader meaning of one resolved action."""
    codes = list(codes or ())
    return {
        "model_stance": model_stance(composite_score),
        "new_money": new_money(action),
        "holder_action": holder_action(action, composite_score, codes),
        "holdings_known": bool(holdings_known),
        "primary_blocker": primary_blocker(action, composite_score, codes, event),
    }


def dedupe(codes: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for code in codes:
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out
