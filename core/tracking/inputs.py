"""The inputs a call was made from, captured when the call is recorded.

Prices and SEC filings have history, so a backtest can rebuild them for any past
date. Analyst targets and ratings, the valuation target and the method behind it,
estimate revisions, news sentiment and insider flow do not: yfinance and the
news feeds serve only today's figures. A decision that read them can be replayed
later only if they were written down the day it was made. This module writes
them down, as plain JSON, for the ledger's ``inputs_json`` column.

Everything here reads attributes defensively (``getattr`` with defaults) and
never raises: recording inputs must not cost the call itself.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, Optional

INPUTS_VERSION = 1


def _num(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _pick(obj: Any, names: Iterable[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if obj is None:
        return out
    for name in names:
        value = getattr(obj, name, None) if not isinstance(obj, dict) else obj.get(name)
        if isinstance(value, bool) or value is None:
            out[name] = value
        elif isinstance(value, (int, float)):
            out[name] = _num(value)
        elif isinstance(value, str):
            out[name] = value
        elif isinstance(value, (list, tuple)):
            out[name] = [v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                         for v in value][:20]
        elif isinstance(value, dict):
            out[name] = json.loads(json.dumps(value, default=str))
        else:
            out[name] = str(getattr(value, "value", value))
    return out


def from_decision(decision: Any) -> Dict[str, Any]:
    """The inputs every engine's Decision already carries."""
    sources = []
    for s in getattr(decision, "sources", None) or []:
        sources.append(_pick(s, ("id", "kind", "provider", "as_of", "retrieved_at")))
    return {
        "version": INPUTS_VERSION,
        "levels": _pick(decision, ("reference_price", "entry", "stop", "target", "reward_risk",
                                   "target_basis", "target_horizon_days", "valuation_target",
                                   "valuation_target_horizon_days")),
        "rr_hurdles": json.loads(json.dumps(getattr(decision, "rr_hurdles", None) or {},
                                            default=str)),
        "risk_metrics": _pick(getattr(decision, "risk_metrics", None) or {},
                              list((getattr(decision, "risk_metrics", None) or {}).keys())),
        "sources": sources,
    }


def from_analysis(result: Any) -> Dict[str, Any]:
    """Research-engine inputs with no point-in-time history anywhere else."""
    mi = getattr(result, "market_intel", None)
    val = getattr(result, "valuation", None)
    sen = getattr(result, "sentiment", None)
    ins = getattr(result, "insider", None)
    return {
        "analyst": _pick(mi, ("analyst_rating", "rating_score", "buy_ratings", "hold_ratings",
                              "sell_ratings", "target_low", "target_mean", "target_median",
                              "target_high", "implied_upside", "analyst_count",
                              "earnings_estimate", "revenue_estimate", "last_earnings_surprise",
                              "earnings_beat_rate", "eps_revision_trend",
                              "revenue_revision_trend", "next_earnings_date")),
        "revision": json.loads(json.dumps(getattr(mi, "revision", None) or {}, default=str)),
        "valuation": _pick(val, ("dcf_fair_value", "dcf_status", "dcf_method", "graham_number",
                                 "graham_status", "fair_value_estimate", "valuation_score",
                                 "valuation_label", "valuation_status", "fair_value_sources",
                                 "target_conservative", "target_base", "target_aggressive",
                                 "target_agreement_pct", "target_source_count", "target_sources",
                                 "target_timeframe")),
        "sentiment": _pick(sen, ("sentiment_score", "sentiment_label", "sentiment_confidence",
                                 "sentiment_source", "direct_news_count", "last_news_timestamp")),
        "insider": _pick(ins, ("insider_sentiment", "recent_buys", "recent_sells", "buy_value",
                               "sell_value", "net_flow", "short_interest", "short_ratio",
                               "data_status")),
    }


def capture(decision: Any, result: Any = None) -> Dict[str, Any]:
    """Inputs for one call: the Decision's own, plus the analysis behind it when given."""
    try:
        out = from_decision(decision)
    except Exception as exc:  # noqa: BLE001 - never cost the call
        out = {"version": INPUTS_VERSION, "error": f"decision inputs: {exc}"}
    if result is not None:
        try:
            out.update(from_analysis(result))
        except Exception as exc:  # noqa: BLE001
            out["analysis_error"] = str(exc)
    return out


def inputs_hash(inputs: Optional[Dict[str, Any]]) -> str:
    """SHA-256 of the canonical JSON of ``inputs``; empty when there are none."""
    if not inputs:
        return ""
    text = json.dumps(inputs, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
