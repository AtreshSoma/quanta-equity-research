"""Research model v4, scored in shadow beside the live call.

Pre-registered in docs/preregistration/elite_research_v4.md. It reads the same
finished analysis as the live model and never changes the live action, score,
levels or gates. Against v3 it changes three things, each from the 2026-10-29
Picks review:

* Estimate revisions are a leg of their own (breadth, trend and surprise, the
  Zacks-style read in modules/earnings_revision_scoring), not a +/- nudge.
* The valuation leg reads a sector-aware 12-month target (engine/fair_value_v4):
  justified P/B for financials, FCF yield for energy, and the Graham Number only
  where book value is a real anchor (it priced CSCO at about $66 against $110).
* New money is gated on expected value, not on reward/risk to a target pro-rated
  to 25% of the 12-month upside against a 2.75xATR stop (a hurdle almost no large
  cap could clear): the chance of reaching the 12-month target before the stop
  within the horizon, from today's price (engine/expected_value).

Everything here is a pure function of plain inputs, so the adapter in
TradingAnalysis_ELITE stays thin and the model is testable without a network.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from engine import expected_value as ev
from engine import fair_value_v4 as fv
from engine.score_bands import band_for
from engine.thresholds import RESEARCH_V4 as V4

VERSION = "v4"
BUY_TIER = ("STRONG_BUY", "BUY", "ACCUMULATE")


def fair_value_params() -> Dict[str, Any]:
    """fair_value_v4 parameters from the v4 thresholds (one source of numbers)."""
    return dict(fv.DEFAULT_PARAMS, cost_of_equity=V4.COST_OF_EQUITY, growth=V4.LONG_RUN_GROWTH,
                roe_clip=tuple(V4.BANK_ROE_CLIP), energy_fcf_yield=V4.ENERGY_FCF_YIELD,
                graham_max_pb=V4.GRAHAM_MAX_PB, plausible_ratio=V4.PLAUSIBLE_RATIO)


def valuation_leg(upside: Optional[float]) -> Optional[float]:
    """0..100 from the 12-month upside (a fraction); None when there is no target."""
    if upside is None:
        return None
    sat = float(V4.VALUATION_SATURATION)
    return 50.0 + 50.0 * max(-1.0, min(1.0, upside / sat))


def revisions_leg(revision: Optional[Dict[str, Any]]) -> Optional[float]:
    """0..100 from the revision read (its 0..10 score); None when it was not read."""
    if not revision or str(revision.get("sentiment") or "UNKNOWN").upper() == "UNKNOWN":
        return None
    try:
        return max(0.0, min(100.0, float(revision.get("revision_score")) * 10.0))
    except (TypeError, ValueError):
        return None


def composite(legs: Dict[str, Optional[float]], is_fund: bool = False) -> Dict[str, Any]:
    """Weighted score over the legs that were measured, weights renormalised.

    A fund has no fundamentals, quality, valuation or revisions, as in v3."""
    weights = dict(V4.WEIGHTS)
    if is_fund:
        weights = {k: (0.0 if k in ("fundamental", "quality", "valuation", "revisions") else w)
                   for k, w in weights.items()}
    used = {k: w for k, w in weights.items() if w > 0 and legs.get(k) is not None}
    total = sum(used.values())
    if total <= 0:
        return {"score": None, "weights": {}}
    used = {k: w / total for k, w in used.items()}
    score = sum(float(legs[k]) * w for k, w in used.items())
    return {"score": round(score, 2), "weights": {k: round(w, 4) for k, w in used.items()}}


def assess(*, legs: Dict[str, Optional[float]], revision: Optional[Dict[str, Any]],
           info: Dict[str, Any], price: float, dcf_value: Optional[float],
           consensus_mean: Optional[float], analyst_count: int,
           entry: float, stop: float, atr: float, cost_frac: float,
           is_fund: bool = False) -> Dict[str, Any]:
    """The v4 call: score, band, the expected-value gate and the action.

    ``legs`` carries the live components (technical, fundamental, quality,
    sentiment); v4 supplies its own valuation and revisions legs. The gate is
    read from today's price: the live stop distance (entry - stop) is kept and
    re-anchored to ``price``, so a pullback entry far below spot cannot carry it.
    """
    intrinsic = fv.intrinsic_value(info or {}, price=price, dcf_value=dcf_value, params=fair_value_params())
    target = fv.blended_target(consensus_mean, int(analyst_count or 0), intrinsic.get("value"),
                               fair_value_params())
    tgt = target.get("value")
    upside = (tgt / price - 1.0) if (tgt and price > 0) else None
    all_legs = dict(legs, valuation=valuation_leg(upside), revisions=revisions_leg(revision))
    comp = composite(all_legs, is_fund=is_fund)
    score = comp["score"]
    band = band_for(score) if score is not None else None

    gate: Dict[str, Any] = {"passes": False, "reason": "no 12-month target"}
    dist = float(entry or 0) - float(stop or 0)
    if tgt and price > 0 and dist > 0:
        gate = ev.assess(price, price - dist, tgt, tgt, atr,
                         horizon=int(V4.HORIZON_DAYS), valuation_horizon=int(V4.VALUATION_HORIZON_DAYS),
                         shrink=float(V4.DRIFT_SHRINK), atr_to_sigma=float(V4.ATR_TO_SIGMA),
                         cost_frac=float(cost_frac), min_ev_r=float(V4.MIN_EV_R))
    elif tgt and price > 0:
        gate = {"passes": False, "reason": "no stop to measure risk against"}

    action = band
    if band in BUY_TIER and not gate.get("passes"):
        action = "WATCHLIST"
    return {
        "version": VERSION,
        "score": score,
        "band_action": action,          # what the ledger records as this model's call
        "score_band": band,
        "weights": comp["weights"],
        "legs": {k: (round(v, 2) if v is not None else None) for k, v in all_legs.items()},
        "target": {"value": round(tgt, 2) if tgt else None, "upside_pct": round(upside * 100, 2) if upside is not None else None,
                   "components": target.get("components") or [], "intrinsic": intrinsic},
        "ev_gate": gate,
    }
