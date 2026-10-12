"""Final-action resolver. Used by BOTH scripts so summary == detail.

Decision flow:
  1. If inputs.is_valid is False -> INVALID
  2. Map composite_score to a candidate FinalAction
  3. Apply risk_overrides
  4. If candidate becomes HOLD_EXISTING but user does not own -> WATCHLIST

A bearish (AVOID) call stays AVOID for a non-holder: for new money it means
"do not buy", which is exactly the instruction, and folding it into WATCHLIST
made a bearish score indistinguishable from a neutral one. What a HOLDER should
do is published separately (``contract["holder_action"]``).
"""
from __future__ import annotations

from typing import Optional

from .blockers import action_contract
from .evidence import stance_for
from .risk_overrides import apply_risk_override_details
from .score_bands import SCORE_BANDS
from .types import ConfidenceTier, DecisionInputs, DecisionResult, FinalAction


# Score buckets for the *candidate* action (before overrides).
# Iterated in declared order — highest threshold wins. The final entry is
# the implicit fallthrough handled by the function below.
# Candidate actions from the single band table (engine.score_bands).
_BAND_TO_ACTION = {
    "STRONG_BUY": FinalAction.STRONG_BUY,
    "BUY": FinalAction.BUY,
    "ACCUMULATE": FinalAction.ACCUMULATE,
    "HOLD": FinalAction.HOLD_EXISTING,   # neutral zone — only meaningful if owned
}
_BUY_THRESHOLDS = tuple(
    (floor, _BAND_TO_ACTION[name]) for floor, name in SCORE_BANDS if name in _BAND_TO_ACTION
)


def minimum_score_for_action(action: FinalAction) -> float:
    """Expose the candidate floor so scanner diagnostics use the actual policy."""
    return next(threshold for threshold, candidate in _BUY_THRESHOLDS if candidate == action)


def _candidate_from_score(score: float) -> FinalAction:
    for threshold, action in _BUY_THRESHOLDS:
        if score >= threshold:
            return action
    return FinalAction.AVOID


def _confidence_tier(score: float, overrides_count: int) -> ConfidenceTier:
    if overrides_count >= 2:
        return ConfidenceTier.LOW
    if score >= 70 and overrides_count == 0:
        return ConfidenceTier.HIGH
    if score >= 55 and overrides_count <= 1:
        return ConfidenceTier.MODERATE
    return ConfidenceTier.LOW


def resolve_final_action(
    inputs: DecisionInputs,
    starting_candidate: Optional[FinalAction] = None,
    enforce_price_gates: bool = True,
) -> DecisionResult:
    """Resolve a FinalAction from ``inputs``.

    By default the candidate is derived from ``inputs.composite_score`` and
    every override rule (including this engine's own price-level and R:R
    gates) applies — the normal case, where one engine is producing its own
    independent verdict from its own numbers.

    Pass ``starting_candidate`` to instead RECONCILE an action that came from
    elsewhere (e.g. another engine's scanner pick) against this engine's
    qualitative red flags only. Combine it with ``enforce_price_gates=False``
    so this engine's own (different) price targets, R:R and composite score
    cannot re-litigate a decision the candidate's own engine already made —
    only real red flags (risk grade, insider activity, bearish reversal, DCF
    overvaluation, data validity) can move it down the conviction ladder.
    """
    if not inputs.is_valid:
        return DecisionResult(
            final_action=FinalAction.INVALID,
            reasons=["Ticker failed data-validation gate"],
            confidence_tier=ConfidenceTier.LOW,
            allocation_eligible=False,
            blocking_reasons=["INVALID"],
            override_codes=["INVALID_INPUTS"],
            blocking_codes=["INVALID_INPUTS"],
            stance="NONE",
            contract=action_contract(FinalAction.INVALID, None, ["INVALID_INPUTS"],
                                     inputs.has_existing_position),
        )

    if starting_candidate is not None:
        candidate = starting_candidate
    else:
        candidate = _candidate_from_score(inputs.composite_score)
    resolved, details = apply_risk_override_details(inputs, candidate, enforce_price_gates=enforce_price_gates)
    # Captured before the ownership collapse below turns AVOID into WATCHLIST.
    stance = stance_for(resolved)

    # If the neutral candidate is HOLD_EXISTING but user doesn't own -> WATCHLIST
    if resolved == FinalAction.HOLD_EXISTING and not inputs.has_existing_position:
        resolved = FinalAction.WATCHLIST
        details.append(("HOLD_NOT_OWNED", "HOLD_EXISTING but no existing position → WATCHLIST"))

    overrides = [text for _code, text in details]
    override_codes = [code for code, _text in details]
    if starting_candidate is not None:
        reasons = [f"Reconciling against {starting_candidate.value} (this engine's own price levels/score not applied)"]
    else:
        reasons = [f"Composite score {inputs.composite_score:.1f} → candidate {candidate.value}"]
    reasons.extend(overrides)

    allocation_eligible = resolved.allocates_new_capital

    return DecisionResult(
        final_action=resolved,
        reasons=reasons,
        overrides_applied=overrides,
        confidence_tier=_confidence_tier(inputs.composite_score, len(overrides)),
        allocation_eligible=allocation_eligible,
        blocking_reasons=[] if allocation_eligible else overrides[:],
        override_codes=override_codes,
        blocking_codes=[] if allocation_eligible else override_codes[:],
        stance=stance,
        contract=action_contract(
            resolved,
            inputs.composite_score if starting_candidate is None else None,
            override_codes, inputs.has_existing_position,
        ),
    )


# ---------------------------------------------------------------------------
# Mapping helper for the existing Signal enum in TradingAnalysis_ELITE.py
# ---------------------------------------------------------------------------
_SIGNAL_NAME_TO_ACTION = {
    "STRONG_BUY":   FinalAction.STRONG_BUY,
    "BUY":          FinalAction.BUY,
    "ACCUMULATE":   FinalAction.ACCUMULATE,
    "HOLD":         FinalAction.HOLD_EXISTING,
    "REDUCE":       FinalAction.AVOID,
    "SELL":         FinalAction.AVOID,
    "STRONG_SELL":  FinalAction.AVOID,
    "NO_POSITION":  FinalAction.WATCHLIST,
}


def signal_to_final_action(signal_name: Optional[str], has_existing_position: bool) -> FinalAction:
    """Convert a legacy Signal enum name to a FinalAction.

    HOLD without an existing position becomes WATCHLIST. REDUCE / SELL /
    STRONG_SELL become AVOID for everyone: for a non-holder that reads "do not
    buy", which is the correct new-money instruction for a bearish call.
    """
    if not signal_name:
        return FinalAction.WATCHLIST
    action = _SIGNAL_NAME_TO_ACTION.get(signal_name.upper(), FinalAction.WATCHLIST)
    if not has_existing_position and action == FinalAction.HOLD_EXISTING:
        return FinalAction.WATCHLIST
    return action
