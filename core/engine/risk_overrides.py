"""Shared risk-override rules that BOTH scripts must apply identically."""
from __future__ import annotations

from typing import List, Tuple

from .data_validator import _is_finite_positive as _is_valid_price
from .thresholds import LADDER
from .types import DecisionInputs, FinalAction


def measured_red_flags(inputs: DecisionInputs) -> List[str]:
    """Every measured, company- or price-specific red flag, at ANY score.

    The override rules below only look for these while the candidate is a buy,
    because their job there is to stop new money. A holder needs them whatever
    the score: they are what corroborates an exit review, and a Hold shown
    without them claims a clean bill it never checked (audit 2026-10-21, F2).
    All flags that fire are listed, in a fixed order; none short-circuits.
    """
    if not inputs.is_valid or not _is_valid_price(inputs.current_price):
        return []
    flags: List[str] = []
    if _is_valid_price(inputs.target_price) and inputs.target_price <= inputs.current_price:
        flags.append("TARGET_BELOW_ENTRY")
    if inputs.dcf_overvaluation_pct is not None and inputs.dcf_overvaluation_pct > LADDER.DCF_OVERVALUATION_CAP_PCT:
        flags.append("DCF_OVERVALUATION")
    if inputs.analyst_downside_pct is not None and inputs.analyst_downside_pct > LADDER.ANALYST_DOWNSIDE_TRIM_PCT:
        flags.append("ANALYST_DOWNSIDE")
    if inputs.heavy_insider_selling:
        flags.append("HEAVY_INSIDER_SELLING")
    if inputs.bearish_reversal:
        flags.append("BEARISH_REVERSAL")
    return flags


def apply_risk_overrides(
    inputs: DecisionInputs,
    candidate: FinalAction,
    enforce_price_gates: bool = True,
) -> Tuple[FinalAction, List[str]]:
    """Apply non-bypassable risk overrides; returns (action, override texts).

    Thin wrapper over :func:`apply_risk_override_details`, which also returns
    the stable rule code of every override.
    """
    resolved, details = apply_risk_override_details(inputs, candidate, enforce_price_gates)
    return resolved, [text for _code, text in details]


def apply_risk_override_details(
    inputs: DecisionInputs,
    candidate: FinalAction,
    enforce_price_gates: bool = True,
) -> Tuple[FinalAction, List[Tuple[str, str]]]:
    """Apply non-bypassable risk overrides to a candidate action.

    Returns (resolved_action, [(rule_code, override_text), ...]). The code is
    the stable identity of the rule that fired; the text is for display only
    and must never be pattern-matched to decide what a rule means.

    Rules (in order):
      1. INVALID inputs                      -> INVALID
      2. Bad price                           -> INVALID
      3. Bad stop/target or wrong-side       -> WATCHLIST (or HOLD_EXISTING if owned)  [price gate]
      4. Risk grade F -> WATCHLIST/HOLD_EXISTING (never BUY); grade D trims one notch
      4b. Heavy insider selling -> block new money; Buy-Now=NO / Timing=WAIT /
          bearish reversal / DCF overvaluation or analyst downside beyond
          LADDER caps (engine.thresholds) -> cap conviction at ACCUMULATE
      5. R/R below threshold                 -> WATCHLIST/HOLD_EXISTING              [price gate]
      6. Score below min_score_for_buy       -> WATCHLIST/HOLD_EXISTING              [price gate]

    ``enforce_price_gates=False`` skips the three rules marked ``[price gate]``
    (3, 5, 6): they all re-derive a verdict from THIS engine's own price
    levels / R:R / composite score. That is the right thing when this
    resolver is producing this engine's OWN independent verdict, but wrong
    when it is reconciling a candidate that came from a DIFFERENT engine
    (e.g. the scanner) against a second, differently-computed set of price
    targets — two models disagreeing on where to put a stop is not a red
    flag about the company. Rules 1, 2, 4 and 4b (data validity, risk grade,
    insider activity, bearish reversal, DCF overvaluation) are qualitative
    and always apply.
    """
    overrides: List[Tuple[str, str]] = []

    def _add(code: str, text: str) -> None:
        overrides.append((code, text))

    if not inputs.is_valid:
        _add("INVALID_INPUTS", "Inputs marked invalid → INVALID")
        return FinalAction.INVALID, overrides

    if not _is_valid_price(inputs.current_price):
        _add("INVALID_PRICE", f"Invalid current_price={inputs.current_price} → INVALID")
        return FinalAction.INVALID, overrides

    owned = inputs.has_existing_position
    downgrade_target = FinalAction.HOLD_EXISTING if owned else FinalAction.WATCHLIST

    # 2. trade-setup sanity (only if candidate is a buy) — [price gate]
    if enforce_price_gates and candidate.allocates_new_capital:
        if not _is_valid_price(inputs.stop_loss):
            _add("INVALID_STOP", f"Invalid stop_loss={inputs.stop_loss} → {downgrade_target.value}")
            candidate = downgrade_target
        elif not _is_valid_price(inputs.target_price):
            _add("INVALID_TARGET", f"Invalid target_price={inputs.target_price} → {downgrade_target.value}")
            candidate = downgrade_target
        elif inputs.stop_loss >= inputs.current_price:
            _add(
                "STOP_ABOVE_ENTRY", f"Stop {inputs.stop_loss:.2f} >= entry {inputs.current_price:.2f} → {downgrade_target.value}"
            )
            candidate = downgrade_target
        elif inputs.target_price <= inputs.current_price:
            _add(
                "TARGET_BELOW_ENTRY", f"Target {inputs.target_price:.2f} <= entry {inputs.current_price:.2f} → {downgrade_target.value}"
            )
            candidate = downgrade_target

    # 3. risk-grade gate (graduated — single source of truth shared by both scripts)
    #    F = genuinely dangerous       -> block new money entirely.
    #    D = elevated, not disqualifying-> trim ONE conviction notch but stay
    #        allocation-eligible (reduced position size). This mirrors the
    #        analyzer `_determine_signal` and the scanner committee policy so
    #        every layer agrees instead of contradicting one another.
    if candidate.allocates_new_capital:
        if inputs.risk_grade == "F":
            _add("RISK_GRADE_F", f"Risk Grade F blocks new-money BUY → {downgrade_target.value}")
            candidate = downgrade_target
        elif inputs.risk_grade == "D":
            # Requirement: a Grade D name allows NO fresh BUY — the strongest
            # action it may carry is ACCUMULATE (reduced size). Both STRONG_BUY
            # and BUY are capped at ACCUMULATE so no layer can publish a D-grade
            # BUY.
            if candidate in (FinalAction.STRONG_BUY, FinalAction.BUY):
                _add(
                    "RISK_GRADE_D_CAP", f"Risk Grade D blocks fresh BUY: {candidate.value} → ACCUMULATE (reduced size)"
                )
                candidate = FinalAction.ACCUMULATE
            else:
                _add("RISK_GRADE_D_HOLD", "Risk Grade D: hold at ACCUMULATE (reduced size)")

    # 3b. Qualitative red-flag overrides shared by BOTH engines.
    #     These let the scanner and the deep analyzer feed the SAME signals
    #     (Buy-Now timing, bearish reversal, DCF overvaluation, heavy insider
    #     selling) into one resolver so they cannot contradict each other.
    #     A helper trims a buy-tier candidate by one conviction notch and
    #     caps it at ACCUMULATE (never below, so the name stays on the radar).
    def _trim_to_accumulate(cand: FinalAction) -> FinalAction:
        # Any buy-tier conviction (STRONG_BUY or BUY) is capped at ACCUMULATE
        # so a red-flagged name can never be published as a fresh BUY, while
        # still staying on the radar as a scale-in/watch candidate.
        if cand in (FinalAction.STRONG_BUY, FinalAction.BUY):
            return FinalAction.ACCUMULATE
        return cand

    if candidate.allocates_new_capital:
        # Heavy insider selling is the strongest tell — block new money outright,
        # mirroring the scanner's investment-committee hard override.
        if inputs.heavy_insider_selling:
            _add("HEAVY_INSIDER_SELLING", f"Heavy insider selling → {downgrade_target.value} (no new money)")
            candidate = downgrade_target

    if candidate.allocates_new_capital:
        # Deep-analyzer "Buy Now = NO" or "Timing = WAIT": do not publish a
        # fresh BUY. Cap at ACCUMULATE (scale-in only) so the scanner cannot
        # show an immediate-entry BUY while the deep analyzer says wait.
        if inputs.buy_now is False or inputs.timing_wait:
            why = "Buy Now = NO" if inputs.buy_now is False else "Timing = WAIT"
            trimmed = _trim_to_accumulate(candidate)
            if trimmed != candidate or candidate != FinalAction.ACCUMULATE:
                _add("BUY_NOW_NO" if inputs.buy_now is False else "TIMING_WAIT", f"{why} → cap at {trimmed.value} (wait for entry)")
            candidate = trimmed

    if candidate.allocates_new_capital and inputs.bearish_reversal:
        # High-reliability bearish reversal after an uptrend (e.g. Shooting
        # Star): downgrade one notch and surface the pattern as a reason.
        name = inputs.bearish_reversal_name or "bearish reversal"
        trimmed = _trim_to_accumulate(candidate)
        _add("BEARISH_REVERSAL", f"Bearish reversal ({name}) → {trimmed.value} (wait for confirmation)")
        candidate = trimmed

    if candidate.allocates_new_capital and inputs.dcf_overvaluation_pct is not None:
        # DCF overvaluation beyond the cap trims conviction one notch as a penalty.
        cap = LADDER.DCF_OVERVALUATION_CAP_PCT
        if inputs.dcf_overvaluation_pct > cap:
            trimmed = _trim_to_accumulate(candidate)
            _add(
                "DCF_OVERVALUATION",
                f"DCF overvaluation {inputs.dcf_overvaluation_pct:.0f}% (>{cap:.0f}%) → {trimmed.value}"
            )
            candidate = trimmed

    if candidate.allocates_new_capital and inputs.analyst_downside_pct is not None:
        # The same one-notch trim, applied to the analyst consensus target and
        # named as such (audit F9: the scanner used to report it as a DCF fact).
        cap = LADDER.ANALYST_DOWNSIDE_TRIM_PCT
        if inputs.analyst_downside_pct > cap:
            trimmed = _trim_to_accumulate(candidate)
            _add(
                "ANALYST_DOWNSIDE",
                f"Analyst consensus target {inputs.analyst_downside_pct:.0f}% below the entry "
                f"(>{cap:.0f}%) → {trimmed.value}"
            )
            candidate = trimmed

    # 4. R/R gate — [price gate]
    if (enforce_price_gates and candidate.allocates_new_capital
            and inputs.risk_reward_ratio < inputs.min_rr_for_buy):
        _add(
            "LOW_REWARD_RISK", f"R/R {inputs.risk_reward_ratio:.2f} below min {inputs.min_rr_for_buy:.2f} → {downgrade_target.value}"
        )
        candidate = downgrade_target

    # 5. score gate — [price gate]
    if (enforce_price_gates and candidate.allocates_new_capital
            and inputs.composite_score < inputs.min_score_for_buy):
        _add(
            "LOW_SCORE", f"Score {inputs.composite_score:.0f} below min {inputs.min_score_for_buy:.0f} → {downgrade_target.value}"
        )
        candidate = downgrade_target

    return candidate, overrides
