"""Shared types for the decision-core engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .thresholds import LADDER, RR


class FinalAction(str, Enum):
    """Single source of truth for what the user should DO with a ticker.

    These are mutually exclusive. A ticker has exactly one FinalAction.
    """
    STRONG_BUY    = "STRONG_BUY"      # high-conviction new entry
    BUY           = "BUY"             # standard new entry
    ACCUMULATE   = "ACCUMULATE"      # add on weakness
    HOLD_EXISTING = "HOLD_EXISTING"   # only meaningful if user owns it
    WATCHLIST     = "WATCHLIST"       # do not buy yet, wait for setup
    AVOID         = "AVOID"           # actively bearish / reduce / sell
    INVALID       = "INVALID"         # no valid data — produce no scores

    @property
    def allocates_new_capital(self) -> bool:
        return self in (FinalAction.STRONG_BUY, FinalAction.BUY, FinalAction.ACCUMULATE)


class ConfidenceTier(str, Enum):
    HIGH     = "HIGH"
    MODERATE = "MODERATE"
    LOW      = "LOW"


@dataclass
class ValidationReport:
    """Output of the hard pre-scoring data-validation gate."""
    is_valid: bool = False
    ticker: str = ""
    reasons: List[str] = field(default_factory=list)
    has_price: bool = False
    has_history: bool = False
    has_market_cap: bool = False
    has_company_name: bool = False
    is_etf: bool = False
    quote_type: str = ""          # EQUITY / ETF / MUTUALFUND / INDEX / CRYPTO ...

    def fail(self, reason: str) -> "ValidationReport":
        self.is_valid = False
        self.reasons.append(reason)
        return self


@dataclass
class DecisionInputs:
    """Minimal inputs the decision engine needs. Both scripts can build this."""
    ticker: str
    composite_score: float            # 0..100
    risk_grade: str                   # 'A'..'F'
    risk_reward_ratio: float          # absolute ratio, e.g. 2.5
    current_price: float
    stop_loss: float
    target_price: float
    has_existing_position: bool = False
    is_valid: bool = True             # set False to force INVALID
    min_score_for_buy: float = field(default_factory=lambda: LADDER.MIN_SCORE_FOR_BUY)
    min_rr_for_buy: float = field(default_factory=lambda: RR.RESEARCH_MIN)

    # ── Cross-engine override signals (optional; default = no effect) ────────
    # These let the scanner and the deep analyzer feed the SAME qualitative
    # red flags into ONE resolver so they cannot disagree on the same ticker.
    buy_now: Optional[bool] = None        # deep analyzer "Buy Now"; False blocks fresh BUY
    timing_wait: bool = False             # deep analyzer Timing == WAIT
    bearish_reversal: bool = False        # high-reliability bearish reversal after uptrend
    bearish_reversal_name: str = ""       # e.g. "Shooting Star"
    dcf_overvaluation_pct: Optional[float] = None  # +ve = overvalued by X% vs fair value
    # +ve = the analyst consensus target sits X% BELOW the entry. Kept apart from
    # dcf_overvaluation_pct so a reason never calls an analyst fact a DCF one.
    analyst_downside_pct: Optional[float] = None
    heavy_insider_selling: bool = False   # cluster of insider sells / heavy net selling

    def __post_init__(self) -> None:
        # Normalise risk_grade so 'd ', 'D', 'd' all gate identically.
        # Unknown grades collapse to 'C' (neutral) rather than silently
        # bypassing the D/F new-money block.
        g = (self.risk_grade or '').strip().upper()
        self.risk_grade = g if g in {'A', 'B', 'C', 'D', 'F'} else 'C'


@dataclass
class DecisionResult:
    final_action: FinalAction
    reasons: List[str] = field(default_factory=list)
    overrides_applied: List[str] = field(default_factory=list)
    confidence_tier: ConfidenceTier = ConfidenceTier.LOW
    allocation_eligible: bool = False
    blocking_reasons: List[str] = field(default_factory=list)
    # Stable rule codes, index-aligned with ``overrides_applied`` /
    # ``blocking_reasons`` (display text). Consumers key behaviour off these.
    override_codes: List[str] = field(default_factory=list)
    blocking_codes: List[str] = field(default_factory=list)
    # Directional view BEFORE ownership softening. A bearish call shown to a
    # non-holder as WATCHLIST is still a bearish call, and the signal ledger
    # must score it as one. BULLISH / NEUTRAL / BEARISH / NONE.
    stance: str = "NEUTRAL"
    # Per-reader meaning (model_stance / new_money / holder_action /
    # primary_blocker); see engine.blockers.action_contract.
    contract: Dict[str, Any] = field(default_factory=dict)
