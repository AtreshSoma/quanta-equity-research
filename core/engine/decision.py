"""The one authoritative decision object per ticker and horizon.

Every engine builds a :class:`Decision` as its LAST step, after the final action
is resolved, and every surface (CLI report, web app, Streamlit, MCP, the signal
ledger) renders or records that object instead of re-deriving a verdict from
intermediate fields. That is what stops "BUY" in one panel and "WATCHLIST" in
the next.

The object deliberately separates four layers, so a reader can always tell
which is which:

    facts      -> ``sources`` (what data, from where, as of when)
    signals    -> ``factor_scores`` (predictive) and ``risk_metrics`` (sizing only)
    AI / text  -> ``interpretation`` (optional, grounded, cites source ids)
    decision   -> ``action`` / ``label`` / ``stance`` / levels / ``evidence``
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .evidence import Evidence, Presentation, present_action, stance_for
from .market_calendar import (india_trading_days_between, last_completed_india_session,
                              last_completed_session, listing_market, trading_days_between)
from .thresholds import LEVELS
from .types import FinalAction

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Horizon:
    key: str               # "swing" | "position" | "long"
    trading_days: int
    label: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SWING = Horizon("swing", 10, "Swing (~2 weeks)")
POSITION = Horizon("position", LEVELS.RESEARCH_HORIZON_DAYS, "Position (~3 months)")
LONG = Horizon("long", LEVELS.VALUATION_HORIZON_DAYS, "Long-term (~12 months)")


@dataclass
class SourceRef:
    """One piece of raw data behind a decision."""
    id: str                         # stable id the text layer cites, e.g. "px", "info", "8k-1"
    kind: str                       # price | fundamentals | analyst | filing | news | insider | macro
    provider: str                   # yfinance | stooq | sec-edgar | finnhub | ...
    as_of: Optional[str] = None     # market date / publication time of the data itself
    retrieved_at: Optional[str] = None
    title: str = ""
    url: str = ""


@dataclass
class Scenario:
    name: str                       # bear | base | bull
    price: float
    return_pct: float
    method: str                     # how the level was derived, in words
    source_ids: List[str] = field(default_factory=list)


@dataclass
class Freshness:
    status: str = "unknown"         # fresh | stale | unknown
    last_bar: Optional[str] = None
    lag_sessions: Optional[int] = None
    notes: List[str] = field(default_factory=list)


def assess_price_freshness(last_bar: Optional[_date], now: Optional[datetime] = None,
                           max_lag_sessions: int = 1, ticker: Optional[str] = None,
                           market: Optional[str] = None) -> Freshness:
    """Is the latest daily bar recent enough to act on?

    ``lag_sessions`` counts completed sessions after ``last_bar``. A bar dated
    today during market hours is a live (partial) bar and counts as fresh.

    Sessions are counted on the listing's own calendar: ``market`` ('US' / 'IN')
    when given, else from the ``ticker`` suffix (.NS / .BO is India: the IST
    close and the NSE holiday list); anything else uses the New York calendar.
    """
    if last_bar is None:
        return Freshness("unknown", None, None, ["No price date available"])
    if str(market or listing_market(ticker)).upper() == "IN":
        expected = last_completed_india_session(now)
        lag = max(0, india_trading_days_between(last_bar, expected))
    else:
        expected = last_completed_session(now)
        lag = max(0, trading_days_between(last_bar, expected))
    status = "fresh" if lag <= max_lag_sessions else "stale"
    notes = []
    if status == "stale":
        notes.append(
            f"Latest price bar is {last_bar.isoformat()}, {lag} completed sessions old "
            f"(expected {expected.isoformat()}). Levels may be out of date."
        )
    return Freshness(status, last_bar.isoformat(), lag, notes)


def _clean(value: Any) -> Any:
    """Make a value JSON-safe: NaN/inf -> None, dates -> ISO strings."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, _date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # numpy scalar
        try:
            return _clean(value.item())
        except Exception:  # noqa: BLE001
            return str(value)
    return value


@dataclass
class Decision:
    ticker: str
    engine: str                              # "shortterm" | "elite" | "elite_pro"
    horizon: Horizon
    action: FinalAction                      # the model's resolved action
    evidence: Evidence
    as_of: Optional[str] = None              # market date of the price the decision uses
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Signals
    score: Optional[float] = None            # composite, 0..100 (ranking only unless validated)
    # Which model and scale ``score`` comes from. Two engines' scores on the
    # same-looking 0-100 axis are NOT comparable; this says which one it is.
    score_basis: str = ""
    factor_scores: Dict[str, float] = field(default_factory=dict)   # predictive components
    risk_metrics: Dict[str, float] = field(default_factory=dict)    # sizing / risk grade only
    risk_grade: str = ""
    # [F3] What each factor added to the grade, and the daily returns it read.
    risk_grade_drivers: List[Dict[str, Any]] = field(default_factory=list)
    risk_grade_lookback_days: int = 0
    # [F7] The corporate-event check the ladder read (engine.corporate_events).
    event_check: Optional[Dict[str, Any]] = None

    # Levels
    reference_price: Optional[float] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    reward_risk: Optional[float] = None      # net of costs, to ``target``
    scenarios: List[Scenario] = field(default_factory=list)
    # What the target is and how far ahead it looks. The Decision's own horizon
    # (``horizon``) can be shorter than the valuation target's, and the two must
    # never be read as one.
    target_basis: str = ""
    target_horizon_days: Optional[int] = None
    # The longer-dated valuation reference ``target`` may be derived from
    # (context only: reward/risk is never measured to it).
    valuation_target: Optional[float] = None
    valuation_target_horizon_days: Optional[int] = None

    # Explanation
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)   # signals that disagree, stated openly
    blocking: List[str] = field(default_factory=list)    # why it is not a stronger call
    blocking_codes: List[str] = field(default_factory=list)  # stable identities for blocking
    transitions: List[Dict[str, Any]] = field(default_factory=list)
    interpretation: Optional[Dict[str, Any]] = None      # grounded LLM layer, if any

    # Sizing (a risk budget unless the strategy is validated)
    sizing: Optional[Dict[str, Any]] = None

    # Provenance
    sources: List[SourceRef] = field(default_factory=list)
    freshness: Freshness = field(default_factory=Freshness)

    # Directional view the Decision stands behind (after gates, before
    # ownership), recorded by the signal ledger.
    stance_override: Optional[str] = None

    # Per-reader meaning of ``action`` (see engine.blockers): the score's own
    # direction, whether new money may enter, what a holder should do, and the
    # first rule that blocked new money. Research never knows holdings, so the
    # holder action is conditional ("if you hold it").
    model_stance: str = "NONE"
    new_money: str = "NONE"
    holder_action: str = "N/A"
    holdings_known: bool = False
    primary_blocker: Optional[Dict[str, str]] = None
    # Every rule code in ``blocking_codes`` with its category, plain statement
    # and the condition that clears it, in the order the rules fired.
    blockers: List[Dict[str, str]] = field(default_factory=list)
    # For a funded call: the measurable conditions that would withdraw it.
    invalidation: List[str] = field(default_factory=list)

    @property
    def presentation(self) -> Presentation:
        return present_action(self.action, self.evidence, self.stance)

    @property
    def stance(self) -> str:
        return self.stance_override or stance_for(self.action)

    @property
    def label(self) -> str:
        return self.presentation.label

    def to_dict(self) -> Dict[str, Any]:
        pres = self.presentation
        d = {
            "schema_version": SCHEMA_VERSION,
            "ticker": self.ticker,
            "engine": self.engine,
            "horizon": self.horizon.to_dict(),
            "as_of": self.as_of,
            "generated_at": self.generated_at,
            "action": self.action.value,
            "label": pres.label,
            "stance": self.stance,
            "model_stance": self.model_stance,
            "new_money": self.new_money,
            "holder_action": self.holder_action,
            "holdings_known": self.holdings_known,
            "primary_blocker": dict(self.primary_blocker) if self.primary_blocker else None,
            "blockers": [dict(b) for b in self.blockers],
            "invalidation": list(self.invalidation),
            "sizing_basis": pres.sizing_basis,
            "caveat": pres.caveat,
            "qualifier": pres.qualifier,
            "evidence": self.evidence.to_dict(),
            "score": self.score,
            "score_basis": self.score_basis,
            "factor_scores": dict(self.factor_scores),
            "risk_metrics": dict(self.risk_metrics),
            "risk_grade": self.risk_grade,
            "risk_grade_drivers": [dict(d) for d in self.risk_grade_drivers],
            "risk_grade_lookback_days": self.risk_grade_lookback_days,
            "event_check": dict(self.event_check) if self.event_check else None,
            "levels": {
                "reference_price": self.reference_price,
                "entry": self.entry,
                "stop": self.stop,
                "target": self.target,
                "reward_risk": self.reward_risk,
                "target_basis": self.target_basis,
                "target_horizon_days": self.target_horizon_days,
                "valuation_target": self.valuation_target,
                "valuation_target_horizon_days": self.valuation_target_horizon_days,
            },
            "scenarios": [asdict(s) for s in self.scenarios],
            "reasons": list(self.reasons),
            "risks": list(self.risks),
            "conflicts": list(self.conflicts),
            "blocking": list(self.blocking),
            "blocking_codes": list(self.blocking_codes),
            "transitions": list(self.transitions),
            "interpretation": self.interpretation,
            "sizing": self.sizing,
            "sources": [asdict(s) for s in self.sources],
            "freshness": asdict(self.freshness),
        }
        return _clean(d)

    def headline(self) -> str:
        """One line: label, horizon, evidence. Used by every text surface."""
        pres = self.presentation
        label = f"{pres.label} ({pres.qualifier.lower()})" if pres.qualifier else pres.label
        parts = [f"{self.ticker}: {label}", self.horizon.label]
        if not self.evidence.status.permits_recommendation:
            parts.append(self.evidence.status_text)
        if self.freshness.status == "stale":
            parts.append("STALE DATA")
        return " | ".join(parts)
