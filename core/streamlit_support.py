"""Small, testable adapter between the Streamlit UI and QUANTA's engine API."""

from __future__ import annotations

import csv
import html
import math
import re
import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from io import StringIO
from typing import Any


_RICH_TAG = re.compile(r"\[(?:/?[a-zA-Z][^\]]*|/)\]")


# ---------------------------------------------------------------------------
# Caching policy (pure; the Streamlit decorators live in streamlit_app.py)
# ---------------------------------------------------------------------------

# Identical inputs inside this window reuse the previous successful run.
SCAN_CACHE_TTL_SECONDS = 15 * 60
STOCK_CACHE_TTL_SECONDS = 15 * 60
# Engine objects (provider clients, analyzer caches) are rebuilt hourly.
RESOURCE_TTL_SECONDS = 60 * 60
CACHE_MAX_ENTRIES = 32

# Evidence-registry keys, one per engine that reaches this UI.
MARKET_SCAN_EVIDENCE_KEY = "elite_pro.composite"   # SP_ELITE_PRO
SINGLE_STOCK_EVIDENCE_KEY = "elite.research.v2"  # TradingAnalysis_ELITE (engine.evidence.ELITE)

BUY_TIER_ACTIONS = frozenset({"STRONG_BUY", "BUY", "ACCUMULATE"})
RESEARCH_DISCLAIMER = "The model is not validated: calls are research ideas, not recommendations."


class UncacheableResult(Exception):
    """Raised inside a cached function so a result is shown but never stored.

    Streamlit does not cache a call that raises, so invalid, degraded, or
    no-data outcomes cannot be replayed later as if they were successes.
    ``payload`` carries anything the caller may still display (e.g. a degraded
    scan report); ``message`` is the user-facing reason.
    """

    def __init__(self, message: str, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.payload = payload


@dataclass
class GuardedResource:
    """An engine object shared through ``st.cache_resource`` plus a lock.

    The engines keep per-run state (failed tickers, filter counters), so two
    sessions must never drive the same instance at once.
    """

    obj: Any
    lock: Any = field(default_factory=threading.Lock)


def scan_cache_key(
    market_code: str,
    capital: Any,
    scan_code: str,
    max_positions: Any,
    diversify: Any,
) -> tuple[str, float, str, int, bool]:
    """Normalized inputs that fully determine a market-scan result."""
    return (
        str(market_code or "US").strip().upper(),
        round(float(capital or 0.0), 2),
        normalize_scan_code(scan_code),
        int(max_positions or 0),
        bool(diversify),
    )


def stock_cache_key(ticker: Any, capital: Any) -> tuple[str, float]:
    """Normalized inputs that fully determine a single-stock brief."""
    return (str(ticker or "").strip().upper(), round(float(capital or 0.0), 2))


def cache_fingerprint(key: tuple) -> str:
    """Stable string form of a cache key, used to flag out-of-date results."""
    return "|".join(str(part) for part in key)


def plain_data(value: Any) -> Any:
    """Convert engine output into plain, picklable Python data.

    ``st.cache_data`` pickles return values; engine payloads can carry numpy
    scalars, datetimes, enums, or arbitrary objects. NaN/inf become ``None``.
    """
    if isinstance(value, Enum):
        return plain_data(value.value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain_data(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return plain_data(to_dict())
        except Exception:
            return str(value)
    item = getattr(value, "item", None)
    if callable(item):  # numpy scalar
        try:
            return plain_data(item())
        except Exception:
            return str(value)
    return str(value)


# ---------------------------------------------------------------------------
# Honesty layer: decision, evidence, labels, freshness, escaping
# ---------------------------------------------------------------------------

def esc(value: Any) -> str:
    """HTML-escape any engine or user string before ``unsafe_allow_html``."""
    return html.escape("" if value is None else str(value), quote=True)


def decision_dict(value: Any) -> dict[str, Any] | None:
    """Return a Decision as a dict, accepting a Decision object or a dict."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value) if value else None
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
        except Exception:
            return None
        return dict(result) if isinstance(result, Mapping) and result else None
    return None


def extract_decision(source: Any) -> dict[str, Any] | None:
    """Read the ``decision`` attached to an engine pick or result payload."""
    if source is None:
        return None
    if isinstance(source, Mapping):
        return decision_dict(source.get("decision"))
    return decision_dict(getattr(source, "decision", None))


def _fallback_evidence(strategy: str, reason: str) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "status": "UNTESTED",
        "status_text": "Never validated",
        "summary": reason,
        "permits_recommendation": False,
    }


def lookup_evidence(strategy: str) -> dict[str, Any]:
    """Evidence for an engine key from the registry. Never raises; fails closed."""
    try:
        from engine.evidence import get_evidence

        result = get_evidence(strategy).to_dict()
    except Exception:
        return _fallback_evidence(
            strategy,
            "The evidence registry could not be read, so this model is treated as not validated.",
        )
    return dict(result) if isinstance(result, Mapping) else _fallback_evidence(
        strategy, "The evidence registry returned no usable record."
    )


def resolve_evidence(decision: Any = None, engine_key: str = "") -> dict[str, Any]:
    """Evidence from the decision when present, else the registry.

    ``permits_recommendation`` is true only when the payload says so AND the
    status is VALIDATED; anything missing or unrecognized is not validated.
    """
    decision_data = decision_dict(decision)
    evidence = None
    if decision_data and isinstance(decision_data.get("evidence"), Mapping):
        evidence = dict(decision_data["evidence"])
        if not evidence.get("status"):
            evidence = None
    if evidence is None:
        evidence = lookup_evidence(engine_key)
    status = str(evidence.get("status") or "UNTESTED").upper()
    evidence["status"] = status
    evidence["permits_recommendation"] = bool(
        evidence.get("permits_recommendation") is True and status == "VALIDATED"
    )
    evidence.setdefault("status_text", status.replace("_", " ").title())
    evidence.setdefault("summary", "")
    return evidence


def evidence_banner(decision: Any = None, engine_key: str = "") -> dict[str, Any]:
    """Content of the single evidence banner shown once per results view."""
    evidence = resolve_evidence(decision, engine_key)
    status_text = str(evidence.get("status_text") or "Never validated")
    summary = " ".join(str(evidence.get("summary") or "").split())
    permits = bool(evidence["permits_recommendation"])
    parts = [f"Model evidence: {status_text}."]
    if summary:
        parts.append(summary.rstrip(".") + ".")
    if not permits:
        parts.append(RESEARCH_DISCLAIMER)
    return {
        "strategy": str(evidence.get("strategy") or engine_key),
        "status": evidence["status"],
        "status_text": status_text,
        "summary": summary,
        "permits_recommendation": permits,
        "disclaimer": "" if permits else RESEARCH_DISCLAIMER,
        "text": " ".join(parts),
    }


def normalize_action(value: Any) -> str:
    """``"Strong Buy"`` / ``"strong-buy"`` / ``FinalAction`` -> ``"STRONG_BUY"``."""
    raw = getattr(value, "value", value)
    return re.sub(r"[\s\-]+", "_", str(raw or "").strip().upper())


def _is_buy_tier_label(label: Any) -> bool:
    text = re.sub(r"[_\-]+", " ", str(label or "").strip().upper())
    return text.startswith(("STRONG BUY", "BUY", "ACCUMULATE"))


def display_label(
    action: Any,
    *,
    decision: Any = None,
    evidence: Mapping[str, Any] | None = None,
    raw_label: Any = None,
) -> str:
    """The verdict label a user sees.

    1. A decision's own ``label`` wins (the engine already applied the gate).
    2. Otherwise, a buy-tier action keeps its plain name; without validated
       evidence the caller shows it with :func:`is_unproven_buy`'s caveat.
    3. A buy-tier *label* on a non-buy action is contradictory; the action wins.
    """
    decision_data = decision_dict(decision)
    if decision_data and str(decision_data.get("label") or "").strip():
        return str(decision_data["label"]).strip()
    normalized = normalize_action(action)
    label = str(raw_label or "").strip() or normalized.replace("_", " ")
    if normalized in BUY_TIER_ACTIONS:
        return label if _is_buy_tier_label(label) else normalized.replace("_", " ")
    if _is_buy_tier_label(label):
        return normalized.replace("_", " ") or "WATCHLIST"
    return label


def is_unproven_buy(action: Any, evidence: Mapping[str, Any] | None) -> bool:
    """A buy-tier call from a model whose evidence does not permit a recommendation."""
    permits = bool(evidence and evidence.get("permits_recommendation") is True
                   and str(evidence.get("status") or "").upper() == "VALIDATED")
    return normalize_action(action) in BUY_TIER_ACTIONS and not permits


def freshness_summary(decision: Any = None, fallback_as_of: Any = None) -> dict[str, Any]:
    """Price-date freshness from ``decision.freshness``; the raw timestamp otherwise."""
    decision_data = decision_dict(decision) or {}
    freshness = decision_data.get("freshness")
    freshness = dict(freshness) if isinstance(freshness, Mapping) else {}
    as_of = decision_data.get("as_of") or freshness.get("last_bar") or fallback_as_of
    status = str(freshness.get("status") or "unknown").lower()
    last_bar = freshness.get("last_bar")
    lag = freshness.get("lag_sessions")
    notes = [str(note) for note in (freshness.get("notes") or []) if note]

    as_of_text = _display_date(as_of) if as_of else ""
    if not decision_data or not freshness:
        text = (
            f"Prices as of {as_of_text}; freshness not assessed."
            if as_of_text else "Price date unavailable; freshness not assessed."
        )
        status = "unknown"
    else:
        pieces = [f"Prices as of {as_of_text or 'unknown date'}", f"data {status}"]
        if last_bar:
            pieces.append(f"last bar {last_bar}")
        if isinstance(lag, (int, float)):
            pieces.append(f"{int(lag)} session{'s' if int(lag) != 1 else ''} behind")
        text = " · ".join(pieces) + "."
    return {
        "status": status,
        "as_of": str(as_of or ""),
        "last_bar": str(last_bar or ""),
        "lag_sessions": lag,
        "is_stale": status == "stale",
        "notes": notes,
        "text": text,
    }


_NEGATED_ENTRY = ("avoid", "do not", "don't", "no new", "no entry", "wait", "no recommendation", "not ")
_ENTRY_WORDS = ("starter", "buy", "entry", "add", "accumulate", "enter", "position")


def reconcile_new_buyer(text: Any, action: Any, label: Any, unproven: bool = False) -> str:
    """Keep the new-buyer panel consistent with the final action and label.

    The guidance text is computed before final-action gates run, so it can say
    "Starter position" after the final action became WATCHLIST. The final
    action wins; an unproven buy-tier call keeps the text but says what it is.
    """
    message = " ".join(str(text or "").split())
    lowered = message.lower()
    invites_entry = bool(message) and not any(word in lowered for word in _NEGATED_ENTRY) and any(
        word in lowered for word in _ENTRY_WORDS
    )
    normalized = normalize_action(action)
    if invites_entry and normalized not in BUY_TIER_ACTIONS:
        shown = str(label or normalized.replace("_", " ") or "WATCHLIST")
        return f"No new position: the final action is {shown}."
    if unproven and invites_entry:
        return (
            f"{message.rstrip('.')} — research idea only; the model is not validated, "
            "so any size shown is a risk budget, not advice to buy."
        )
    return message or "Wait for a clearer setup."


def normalize_scan_code(scan_code: str) -> str:
    """Return the lowercase scan codes expected by ``SP_ELITE_PRO``."""
    return str(scan_code or "standard").strip().lower()


def run_market_scan(
    system: Any,
    scan_code: str,
    max_positions: int,
    diversify_sectors: bool,
) -> tuple[list[str], list[Any], list[Any]]:
    """Run a market scan through the engine's current public API."""
    normalized_code = normalize_scan_code(scan_code)
    tickers = system.build_stock_universe(normalized_code)
    results = system.scan_market(
        scan_type=normalized_code,
        custom_tickers=tickers,
    )
    recommendations = system.get_top_recommendations(
        max_positions=max_positions,
        diversify_sectors=diversify_sectors,
    )
    return tickers, results, recommendations


def run_single_stock_analysis(system: Any, ticker: str) -> Any:
    """Run a research deep-dive while preserving event-risk guardrails."""
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return None
    return system.analyze_stock(normalized_ticker, research_mode=True)


def run_deep_stock_analysis(ticker: str, capital: float, analyzer: Any = None) -> dict[str, Any]:
    """Run the project's comprehensive Python analyzer for the Streamlit UI.

    ``analyzer`` lets the app reuse a cached ``QuantaEliteAnalyzer``.
    """
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return {}

    import TradingAnalysis_ELITE as deep_engine

    if analyzer is None:
        analyzer = deep_engine.QuantaEliteAnalyzer()
    result = analyzer.analyze(
        normalized_ticker,
        account_size=float(capital),
        fast_mode=False,
    )
    data = deep_engine._mcp_result_to_dict(result)
    # The engine may expose the Decision on the result object rather than in
    # the dict; carry it across so the UI renders the authoritative verdict.
    if isinstance(data, dict) and not data.get("decision"):
        attached = extract_decision(result)
        if attached:
            data["decision"] = attached
    return data


def deep_analysis_failure(ticker: str, data: Mapping[str, Any] | None) -> str | None:
    """Return a user-facing failure message, or ``None`` when the payload is usable."""
    normalized_ticker = str(ticker or "").strip().upper()
    data = data or {}
    if data.get("is_invalid"):
        reasons = list(data.get("invalid_reasons") or []) or ["The ticker did not pass validation."]
        return f"Could not validate {normalized_ticker}: {'; '.join(str(r) for r in reasons)}"
    if not _number(data.get("current_price")):
        status = data.get("data_status") or "data unavailable"
        reason = data.get("data_status_reason") or "The provider returned insufficient data."
        return f"Could not complete {normalized_ticker} ({status}): {reason}"
    return None


def compute_stock_report(resource: GuardedResource | None, ticker: str, capital: float) -> dict[str, Any]:
    """Body of the cached single-stock call. Raises on anything not a success."""
    analyzer = resource.obj if resource is not None else None
    lock = resource.lock if resource is not None else threading.Lock()
    with lock:
        data = run_deep_stock_analysis(ticker, capital, analyzer=analyzer)
    failure = deep_analysis_failure(ticker, data)
    if failure:
        raise UncacheableResult(failure)
    return plain_data(build_advisor_report(data))


def compute_market_report(
    resource: GuardedResource,
    scan_code: str,
    max_positions: int,
    diversify: bool,
    capital: float,
    currency_symbol: str,
    scan_label: str,
) -> dict[str, Any]:
    """Body of the cached market-scan call.

    A DEGRADED data path is returned to the caller through
    :class:`UncacheableResult` so it can be shown with its warning but is never
    cached as a clean result.
    """
    with resource.lock:
        tickers, results, recommendations = run_market_scan(
            system=resource.obj,
            scan_code=scan_code,
            max_positions=max_positions,
            diversify_sectors=diversify,
        )
        report = plain_data(
            build_market_scan_report(
                system=resource.obj,
                tickers=tickers,
                results=results,
                recommendations=recommendations,
                capital=capital,
                currency_symbol=currency_symbol,
                scan_label=scan_label,
            )
        )
    if report.get("health") != "HEALTHY":
        raise UncacheableResult(
            "The provider data path was degraded; this result is shown but not cached.",
            payload=report,
        )
    return report


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError):
        return default


def _evidence_label(value: Any) -> str:
    score = _number(value)
    if score >= 70:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    return "LOW"


def _score_label(value: Any) -> str:
    score = _number(value)
    if score >= 75:
        return "Strong"
    if score >= 60:
        return "Constructive"
    if score >= 45:
        return "Mixed"
    return "Weak"


def _unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = " ".join(str(item or "").split()).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _display_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%B %d, %Y")
    except ValueError:
        return text


def build_advisor_report(data: dict[str, Any]) -> dict[str, Any]:
    """Turn the deep analyzer payload into a clear, evidence-based advisor brief."""
    company = str(data.get("company_name") or data.get("ticker") or "This stock")
    ticker = str(data.get("ticker") or "")
    action = str(
        data.get("final_action")
        or (data.get("beginner_guidance") or {}).get("signal")
        or data.get("signal")
        or "WATCHLIST"
    ).upper()
    decision = extract_decision(data)
    if decision and decision.get("action"):
        action = normalize_action(decision["action"])
    if action == "HOLD_EXISTING":
        action = "WATCHLIST"

    guidance = data.get("beginner_guidance") or {}
    evidence = resolve_evidence(decision, SINGLE_STOCK_EVIDENCE_KEY)
    label = display_label(
        action,
        decision=decision,
        evidence=evidence,
        raw_label=guidance.get("recommendation_label") or data.get("recommendation_label"),
    )
    unproven = is_unproven_buy(action, evidence)
    outlook = data.get("portfolio_outlook") or {}
    scores = data.get("scores") or {}
    technical = data.get("technical") or {}
    fundamental = data.get("fundamental") or {}
    risk = data.get("risk") or {}
    valuation = data.get("valuation") or {}
    market_intel = data.get("market_intel") or {}
    thesis = data.get("thesis") or {}
    catalysts = data.get("catalysts") or {}

    score = _number(scores.get("composite_score"))
    evidence_score = _number(data.get("confidence_score"))
    risk_grade = str(risk.get("risk_grade") or "N/A")
    # The guidance "buy now" flag is computed before the final-action gates; it
    # must never say YES when the final action does not allocate new capital.
    buy_now = str(guidance.get("buy_now") or "CONDITIONAL")
    if normalize_action(action) not in BUY_TIER_ACTIONS:
        buy_now = "NO"
    elif unproven and buy_now.upper() == "YES":
        buy_now = "RESEARCH IDEA ONLY"
    earnings_days = catalysts.get("days_to_earnings")
    earnings_date = _display_date(
        catalysts.get("earnings_date") or market_intel.get("next_earnings_date")
    )

    if action in {"WATCHLIST", "HOLD", "AVOID"}:
        headline = f"Keep {ticker} on the watchlist; the business is stronger than the current setup."
    elif unproven:
        headline = (
            f"The model's call for {ticker} is {label}, but the model is unproven: "
            "treat it as a research idea, not a validated buy signal."
        )
    elif action == "ACCUMULATE":
        headline = f"{ticker} is a gradual-entry candidate, but only with disciplined sizing."
    elif action in BUY_TIER_ACTIONS:
        headline = f"{ticker} has an actionable setup, subject to the risk controls below."
    else:
        headline = f"The model's final action for {ticker} is {label}."

    main_reason = str(
        guidance.get("main_reason")
        or outlook.get("reason")
        or "The model sees mixed evidence and requires confirmation."
    )
    bottom_line = str(outlook.get("bottom_line") or main_reason)

    decision_reasons = [main_reason]
    if decision:
        decision_reasons.extend(
            f"Why not a stronger call: {item}" for item in (decision.get("blocking") or [])
        )
        decision_reasons.extend(
            f"Conflicting evidence: {item}" for item in (decision.get("conflicts") or [])
        )
    if isinstance(earnings_days, (int, float)) and 0 <= earnings_days <= 14:
        decision_reasons.append(
            f"Earnings are due in {int(earnings_days)} days"
            + (f" ({earnings_date})" if earnings_date else "")
            + "; the result and guidance can materially reset the valuation and price trend."
        )
    trend = str(technical.get("trend_direction") or "Unknown").replace("_", " ").title()
    macd = str(technical.get("macd_signal") or "Unknown").title()
    volume_ratio = _number(technical.get("volume_ratio"))
    decision_reasons.append(
        f"Technical confirmation is limited: trend {trend.lower()}, MACD {macd.lower()}, "
        f"and volume at {volume_ratio:.2f}x its recent average."
    )
    decision_reasons.append(
        f"The model assigns risk grade {risk_grade}, with "
        f"{_number(risk.get('annualized_volatility')):.1%} historical annualized volatility."
    )

    technical_points = []
    rsi = _number(technical.get("rsi"))
    if rsi:
        rsi_state = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
        technical_points.append(
            f"RSI is {rsi:.1f} ({rsi_state}); RSI alone is not an entry signal."
        )
    technical_points.append(
        f"Trend is {trend.lower()} with {str(technical.get('trend_strength') or 'unknown').lower()} strength; "
        f"MACD is {macd.lower()}."
    )
    technical_points.append(
        f"Volume is {volume_ratio:.2f}x the recent average, "
        + ("which confirms participation." if volume_ratio >= 1.2 else "so participation does not confirm a breakout.")
    )
    support = _number(technical.get("support_level"))
    resistance = _number(technical.get("resistance_level"))
    if support or resistance:
        technical_points.append(
            f"Observed support is near ${support:,.2f}; resistance is near ${resistance:,.2f}."
        )

    fundamental_points = []
    pe = _number(fundamental.get("pe_ratio"))
    forward_pe = _number(fundamental.get("forward_pe"))
    if pe:
        fundamental_points.append(
            f"Valuation is demanding: trailing P/E {pe:.1f}x"
            + (f" and forward P/E {forward_pe:.1f}x." if forward_pe else ".")
        )
    revenue_growth = _number(fundamental.get("revenue_growth_yoy"))
    eps_growth = _number(fundamental.get("eps_growth_yoy"))
    if revenue_growth or eps_growth:
        fundamental_points.append(
            f"Recent growth is strong: revenue {revenue_growth:.1f}% and EPS {eps_growth:.1f}% year over year."
        )
    roe = _number(fundamental.get("roe"))
    net_margin = _number(fundamental.get("net_margin"))
    if roe or net_margin:
        fundamental_points.append(
            f"Profitability is strong: ROE {roe:.1f}% and net margin {net_margin:.1f}%."
        )
    debt_to_equity = _number(fundamental.get("debt_to_equity"))
    current_ratio = _number(fundamental.get("current_ratio"))
    fundamental_points.append(
        f"Balance-sheet indicators: debt/equity {debt_to_equity:.2f}x and current ratio {current_ratio:.2f}x."
    )

    risk_points = [
        f"Historical annualized volatility: {_number(risk.get('annualized_volatility')):.1%}.",
        f"Historical maximum drawdown in the analysis window: {_number(risk.get('max_drawdown')):.1%}; "
        "this is an observed past decline, not a forecast or worst-case limit.",
        f"Beta estimate: {_number(risk.get('beta')):.2f}x versus the benchmark; risk grade: {risk_grade}.",
    ]
    if data.get("risk_warnings"):
        risk_points.extend(str(item) for item in data["risk_warnings"])

    valuation_points = []
    current_price = _number(data.get("current_price"))
    dcf = _number(valuation.get("dcf_fair_value"))
    if dcf:
        valuation_points.append(
            f"Model DCF estimate: ${dcf:,.2f} ({_number(valuation.get('dcf_upside')):+.1f}% versus the current price). "
            "This is assumption-sensitive, not a guaranteed fair value."
        )
    target_low = _number(valuation.get("target_conservative"))
    target_base = _number(valuation.get("target_base"))
    target_high = _number(valuation.get("target_aggressive"))
    if target_base:
        valuation_points.append(
            f"Internal scenario range: ${target_low:,.2f} / ${target_base:,.2f} / ${target_high:,.2f} "
            "(conservative / base / aggressive)."
        )
    analyst_mean = _number(market_intel.get("target_mean"))
    if analyst_mean:
        valuation_points.append(
            f"External analyst mean target: ${analyst_mean:,.2f}, with a reported range of "
            f"${_number(market_intel.get('target_low')):,.2f}–${_number(market_intel.get('target_high')):,.2f}. "
            "Analyst targets can change after earnings."
        )

    new_buyer = reconcile_new_buyer(
        guidance.get("new_buyer") or "Wait for a clearer setup before committing capital.",
        action,
        label,
        unproven,
    )
    new_buyer_plan = [new_buyer]
    if isinstance(earnings_days, (int, float)) and 0 <= earnings_days <= 14:
        new_buyer_plan.append(
            "Wait for earnings and management guidance, then rerun the analysis with the new price and estimates."
        )
    new_buyer_plan.append(
        "Require improving trend/MACD and entry-day volume of at least 1.5x average before treating a breakout as confirmed."
    )

    existing_holder_plan = [
        str(guidance.get("existing_holder") or "Review the position against your cost basis and risk tolerance."),
        "Treat model support and stop levels as risk references—not automatic orders—because your cost basis, taxes, and portfolio concentration are unknown.",
    ]
    if earnings_date:
        existing_holder_plan.append(f"Review position size and downside tolerance before {earnings_date} earnings.")

    scorecard = []
    for key, factor_label in (
        ("technical_score", "Technical"),
        ("fundamental_score", "Fundamental"),
        ("valuation_score", "Valuation"),
        ("quality_score", "Quality"),
        ("sentiment_score", "Sentiment"),
        ("risk_score", "Risk contribution"),
    ):
        if key in scores:
            value = _number(scores.get(key))
            scorecard.append({"Factor": factor_label, "Score": f"{value:.0f}/100", "Read": _score_label(value)})

    return {
        "ticker": ticker,
        "company": company,
        "sector": data.get("sector") or "Unknown",
        "industry": data.get("industry") or "Unknown",
        "currency_symbol": data.get("currency_symbol") or "$",
        "action": action,
        "recommendation_label": label,
        "decision": decision,
        "evidence": evidence_banner(decision, SINGLE_STOCK_EVIDENCE_KEY),
        "freshness": freshness_summary(decision, data.get("market_timestamp")),
        "sizing_basis": str((decision or {}).get("sizing_basis") or (
            "validated" if evidence["permits_recommendation"] else "risk-budget-only"
        )),
        "headline": headline,
        "bottom_line": bottom_line,
        "score": score,
        "evidence_score": evidence_score,
        "evidence_label": _evidence_label(evidence_score),
        "risk_grade": risk_grade,
        "buy_now": buy_now,
        "current_price": current_price,
        "day_change_pct": _number(data.get("day_change_pct")),
        "decision_reasons": _unique(decision_reasons),
        "new_buyer": new_buyer,
        "existing_holder": str(guidance.get("existing_holder") or "Review against your own cost basis."),
        "bull_case": _unique(list(thesis.get("bull_case") or [])),
        "bear_case": _unique(list(thesis.get("bear_case") or [])),
        "catalysts": _unique(list(thesis.get("catalysts") or [])),
        "technical_points": _unique(technical_points),
        "fundamental_points": _unique(fundamental_points),
        "risk_points": _unique(risk_points),
        "valuation_points": _unique(valuation_points),
        "new_buyer_plan": _unique(new_buyer_plan),
        "existing_holder_plan": _unique(existing_holder_plan),
        "scorecard": scorecard,
        "levels": {
            "Current price": current_price,
            "Support": support,
            "Resistance": resistance,
            "Model base case": target_base,
        },
        "earnings_date": earnings_date,
        "earnings_days": earnings_days,
        "data_quality": data.get("data_quality_status") or guidance.get("data_quality") or "unknown",
        "provider": data.get("data_provider") or "unknown",
        "market_timestamp": _display_date(data.get("market_timestamp")),
        "retrieval_timestamp": _display_date(data.get("retrieval_timestamp")),
        "disclosures": [
            "Model evidence strength is not a probability of profit.",
            "Internal and analyst price targets are scenarios, not guarantees.",
            "Guidance is conditional because holdings, cost basis, taxes, goals, and full portfolio risk were not provided.",
        ],
    }


def recommendation_action(recommendation: Any) -> str:
    """Prefer the engine's authoritative final action when one is available."""
    return str(
        getattr(recommendation, "final_action", "")
        or getattr(recommendation, "signal", "HOLD")
    )


def confidence_label(recommendation: Any) -> str:
    """Expose the honest confidence tier, not a fabricated win percentage."""
    return str(getattr(recommendation, "confidence_tier", "UNKNOWN") or "UNKNOWN")


def upside_percent(recommendation: Any) -> float | None:
    """Calculate target upside safely when an entry price is available."""
    entry = float(getattr(recommendation, "entry_price", 0.0) or 0.0)
    target = float(getattr(recommendation, "price_target", 0.0) or 0.0)
    if entry <= 0:
        return None
    return (target / entry - 1.0) * 100.0


def recommendation_row(recommendation: Any, currency: str) -> dict[str, str]:
    """Convert a ``StockAnalysis`` object into a Streamlit table row."""
    return {
        "Ticker": str(getattr(recommendation, "ticker", "")),
        "Company": str(getattr(recommendation, "company", "")),
        "Action": recommendation_action(recommendation),
        "Score": f"{float(getattr(recommendation, 'overall_score', 0.0) or 0.0):.1f}",
        "Entry": f"{currency}{float(getattr(recommendation, 'entry_price', 0.0) or 0.0):,.2f}",
        "Target": f"{currency}{float(getattr(recommendation, 'price_target', 0.0) or 0.0):,.2f}",
        "Stop": f"{currency}{float(getattr(recommendation, 'stop_loss', 0.0) or 0.0):,.2f}",
        "R:R": f"{float(getattr(recommendation, 'risk_reward_ratio', 0.0) or 0.0):.1f}:1",
        "Confidence": confidence_label(recommendation),
    }


def plain_rationale(system: Any, recommendation: Any) -> str:
    """Return the engine rationale without Rich-console markup."""
    rationale = system.generate_smart_rationale(recommendation)
    if isinstance(rationale, dict):
        text = str(rationale.get("full_analysis", ""))
    else:
        text = str(rationale or "")
    return _RICH_TAG.sub("", text).strip()


def earnings_risk_message(recommendation: Any) -> str | None:
    """Return a visible warning for near-term earnings event risk."""
    try:
        days = int(getattr(recommendation, "_cached_earnings_days", -1))
    except (TypeError, ValueError):
        return None
    if 0 <= days <= 14:
        return (
            f"Earnings are expected in {days} days. This is research-only: "
            "the engine has set the action to WATCHLIST and allocated no capital."
        )
    return None


def _attribute(source: Any, name: str, default: Any = None) -> Any:
    """Read an engine attribute without letting absent telemetry break the UI."""
    value = getattr(source, name, default)
    return default if value is None else value


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="seconds")
    return str(value or "")


def _decision_level(decision: Mapping[str, Any] | None, key: str, fallback: float) -> float:
    levels = (decision or {}).get("levels")
    value = _number(levels.get(key)) if isinstance(levels, Mapping) else 0.0
    return value if value > 0 else fallback


def market_recommendation_record(
    recommendation: Any,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return sortable numeric scan data while preserving the engine decision.

    ``Action`` is the label a user sees (the decision's label when attached,
    otherwise gated by ``evidence``); ``Model action`` is the raw engine action.
    """
    decision = extract_decision(recommendation)
    if evidence is None:
        evidence = resolve_evidence(decision, MARKET_SCAN_EVIDENCE_KEY)
    model_action = recommendation_action(recommendation)
    if decision and decision.get("action"):
        model_action = str(decision["action"])
    entry = _decision_level(decision, "entry", _number(_attribute(recommendation, "entry_price", 0.0)))
    target = _decision_level(decision, "target", _number(_attribute(recommendation, "price_target", 0.0)))
    stop = _decision_level(decision, "stop", _number(_attribute(recommendation, "stop_loss", 0.0)))
    shares = _number(_attribute(recommendation, "shares", 0.0))
    allocation = _number(_attribute(recommendation, "allocation", 0.0))
    return {
        "Ticker": str(_attribute(recommendation, "ticker", "")),
        "Company": str(_attribute(recommendation, "company", "")),
        "Sector": str(_attribute(recommendation, "sector", "Unknown")),
        "Action": display_label(model_action, decision=decision, evidence=evidence)
        + (" · unproven" if is_unproven_buy(model_action, evidence) else ""),
        "Model action": model_action,
        "Score": round(_number(_attribute(recommendation, "overall_score", 0.0)), 1),
        "Confidence": confidence_label(recommendation),
        "Risk grade": str(_attribute(recommendation, "risk_grade", "N/A")),
        "Conviction": int(_number(_attribute(recommendation, "conviction", 0))),
        "Setup": str(_attribute(recommendation, "setup_type", "Unclassified")).replace("_", " ").title(),
        "Entry": round(entry, 2),
        "Target": round(target, 2),
        "Stop": round(stop, 2),
        "Upside %": round(((target / entry) - 1.0) * 100.0, 1) if entry > 0 else None,
        "Risk / reward": round(_number(_attribute(recommendation, "risk_reward_ratio", 0.0)), 1),
        "Allocation": round(allocation, 2),
        "Portfolio %": round(_number(_attribute(recommendation, "position_pct", 0.0)), 2),
        "Shares": shares,
        "Risk at stop": round(max(0.0, entry - stop) * shares, 2),
    }


def build_market_recommendation_detail(
    recommendation: Any,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an editorial, audit-friendly brief for one market-scan pick."""
    record = market_recommendation_record(recommendation, evidence)
    decision = extract_decision(recommendation)
    technical = _attribute(recommendation, "technical_indicators", None)
    fundamental = _attribute(recommendation, "fundamental_metrics", None)
    risk = _attribute(recommendation, "risk_metrics", None)

    reasons = list(_attribute(recommendation, "final_action_reasons", []) or [])
    reasons.extend(_attribute(recommendation, "investment_committee_notes", []) or [])
    if decision:
        reasons.extend(f"Why not a stronger call: {item}" for item in (decision.get("blocking") or []))
        reasons.extend(f"Conflicting evidence: {item}" for item in (decision.get("conflicts") or []))
    if not reasons:
        reasons.append(
            f"The multi-factor model scored this setup {record['Score']:.1f} with "
            f"{record['Confidence'].lower()} heuristic confidence."
        )

    scorecard = []
    for field, label in (
        ("technical_score", "Technical"),
        ("fundamental_score", "Fundamental"),
        ("momentum_score", "Momentum"),
        ("quality_score", "Quality"),
        ("sentiment_score", "Sentiment"),
        ("value_score", "Value"),
    ):
        scorecard.append(
            {
                "Factor": label,
                "Score": round(_number(_attribute(recommendation, field, 0.0)), 1),
            }
        )

    technical_points = []
    if technical is not None:
        rsi = _number(_attribute(technical, "rsi", 0.0))
        if rsi:
            state = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
            technical_points.append(f"RSI is {rsi:.1f} ({state}); it is context, not a standalone trade signal.")
        sma_50 = _number(_attribute(technical, "sma_50", 0.0))
        sma_200 = _number(_attribute(technical, "sma_200", 0.0))
        if sma_50 or sma_200:
            technical_points.append(f"Trend references: 50-day average {sma_50:,.2f}; 200-day average {sma_200:,.2f}.")
        volume_ratio = _number(_attribute(technical, "volume_ratio", 0.0))
        if volume_ratio:
            technical_points.append(f"Volume is {volume_ratio:.2f}x its recent average.")
        macd = _number(_attribute(technical, "macd", 0.0))
        macd_signal = _number(_attribute(technical, "macd_signal", 0.0))
        adx = _number(_attribute(technical, "adx", 0.0))
        if macd or macd_signal or adx:
            technical_points.append(
                f"MACD is {macd:,.2f} versus a {macd_signal:,.2f} signal line; ADX is {adx:.1f}."
            )
        momentum_1m = _number(_attribute(technical, "momentum_1m", 0.0))
        momentum_3m = _number(_attribute(technical, "momentum_3m", 0.0))
        if momentum_1m or momentum_3m:
            technical_points.append(
                f"Price momentum: {momentum_1m:+.1%} over one month and {momentum_3m:+.1%} over three months."
            )
        atr_percent = _number(_attribute(technical, "atr_percent", 0.0))
        high_52w = _number(_attribute(technical, "high_52w", 0.0))
        low_52w = _number(_attribute(technical, "low_52w", 0.0))
        if atr_percent:
            technical_points.append(f"Average true range is {atr_percent:.1%} of price, a short-horizon volatility reference.")
        if high_52w or low_52w:
            technical_points.append(f"Observed 52-week range: {low_52w:,.2f} to {high_52w:,.2f}.")

    fundamental_points = []
    if fundamental is not None:
        pe = _number(_attribute(fundamental, "pe_ratio", 0.0))
        forward_pe = _number(_attribute(fundamental, "forward_pe", 0.0))
        peg = _number(_attribute(fundamental, "peg_ratio", 0.0))
        ev_ebitda = _number(_attribute(fundamental, "ev_ebitda", 0.0))
        revenue_growth = _number(_attribute(fundamental, "revenue_growth", 0.0))
        earnings_growth = _number(_attribute(fundamental, "earnings_growth", 0.0))
        roe = _number(_attribute(fundamental, "roe", 0.0))
        roic = _number(_attribute(fundamental, "roic", 0.0))
        margin = _number(_attribute(fundamental, "profit_margin", 0.0))
        debt_equity = _number(_attribute(fundamental, "debt_to_equity", 0.0))
        current_ratio = _number(_attribute(fundamental, "current_ratio", 0.0))
        interest_coverage = _number(_attribute(fundamental, "interest_coverage", 0.0))
        fcf_margin = _number(_attribute(fundamental, "fcf_margin", 0.0))
        dividend_yield = _number(_attribute(fundamental, "dividend_yield", 0.0))
        if pe:
            valuation_parts = [f"trailing P/E {pe:.1f}x"]
            if forward_pe:
                valuation_parts.append(f"forward P/E {forward_pe:.1f}x")
            if peg:
                valuation_parts.append(f"PEG {peg:.2f}x")
            if ev_ebitda:
                valuation_parts.append(f"EV/EBITDA {ev_ebitda:.1f}x")
            fundamental_points.append("Valuation references: " + "; ".join(valuation_parts) + ".")
        if revenue_growth or earnings_growth:
            fundamental_points.append(
                f"Growth: revenue {revenue_growth:.1%} and earnings {earnings_growth:.1%}."
            )
        if roe or roic or margin:
            fundamental_points.append(
                f"Profitability: ROE {roe:.1%}, ROIC {roic:.1%}, and net margin {margin:.1%}."
            )
        if debt_equity or current_ratio or interest_coverage:
            fundamental_points.append(
                f"Financial health: debt/equity {debt_equity:.2f}x, current ratio {current_ratio:.2f}x, "
                f"and interest coverage {interest_coverage:.1f}x."
            )
        if fcf_margin or dividend_yield:
            fundamental_points.append(
                f"Cash return profile: free-cash-flow margin {fcf_margin:.1%}; dividend yield {dividend_yield:.1%}."
            )

    risk_points = []
    if risk is not None:
        risk_points.extend(
            [
                f"Annualized volatility: {_number(_attribute(risk, 'volatility', 0.0)):.1%}.",
                f"Historical maximum drawdown: {_number(_attribute(risk, 'max_drawdown', 0.0)):.1%}; this is not a forecasted floor.",
                f"Beta: {_number(_attribute(risk, 'beta', 0.0)):.2f}x; Sharpe ratio: {_number(_attribute(risk, 'sharpe_ratio', 0.0)):.2f}; Sortino ratio: {_number(_attribute(risk, 'sortino_ratio', 0.0)):.2f}.",
                f"Historical one-day VaR / CVaR references: {_number(_attribute(risk, 'var_95', 0.0)):.2%} / {_number(_attribute(risk, 'cvar_95', 0.0)):.2%} at the model's 95% convention.",
                f"Correlation with the benchmark: {_number(_attribute(risk, 'correlation_spy', 0.0)):.2f}.",
            ]
        )
    risk_points.append(
        f"Executable risk at the reference stop is approximately {record['Risk at stop']:,.2f}, before gaps, slippage, and fees."
    )

    earnings_days = _attribute(recommendation, "_cached_earnings_days", None)
    try:
        earnings_days = int(earnings_days)
    except (TypeError, ValueError):
        earnings_days = None

    return {
        "record": record,
        "reasons": _unique([str(item) for item in reasons]),
        "scorecard": scorecard,
        "technical_points": _unique(technical_points) or ["No reliable technical detail was returned for this scan result."],
        "fundamental_points": _unique(fundamental_points) or ["No reliable fundamental detail was returned for this scan result."],
        "risk_points": _unique(risk_points),
        "earnings_days": earnings_days,
        "data_provider": str(_attribute(recommendation, "data_provider", "unknown")),
        "data_quality": str(_attribute(recommendation, "data_quality_status", "unknown")),
        "market_timestamp": _display_date(_attribute(recommendation, "market_timestamp", "")),
        "retrieval_timestamp": _display_date(_attribute(recommendation, "retrieval_timestamp", "")),
        "adjustment_status": str(_attribute(recommendation, "adjustment_status", "unknown")),
        "decision": decision,
        "caveat": str((decision or {}).get("caveat") or ""),
        "freshness": freshness_summary(decision, _attribute(recommendation, "market_timestamp", "")),
    }


def build_market_scan_report(
    system: Any,
    tickers: list[str],
    results: list[Any],
    recommendations: list[Any],
    capital: float,
    currency_symbol: str,
    scan_label: str,
) -> dict[str, Any]:
    """Aggregate scan, portfolio, regime, and provider telemetry for the UI."""
    first_decision = next(
        (found for found in (extract_decision(item) for item in recommendations) if found), None
    )
    evidence = resolve_evidence(first_decision, MARKET_SCAN_EVIDENCE_KEY)
    records = [market_recommendation_record(item, evidence) for item in recommendations]
    details = [build_market_recommendation_detail(item, evidence) for item in recommendations]
    failed_tickers = list(_attribute(system, "failed_tickers", []) or [])
    universe_size = len(tickers)
    qualified_count = len(results)
    failed_count = len(failed_tickers)
    filtered_count = max(0, universe_size - qualified_count - failed_count)
    deployment = sum(_number(item.get("Allocation")) for item in records)
    risk_at_stop = sum(_number(item.get("Risk at stop")) for item in records)

    sectors: dict[str, dict[str, float]] = defaultdict(lambda: {"Positions": 0, "Allocation": 0.0})
    for item in records:
        sector = str(item.get("Sector") or "Unknown")
        sectors[sector]["Positions"] += 1
        sectors[sector]["Allocation"] += _number(item.get("Allocation"))
    sector_rows = [
        {
            "Sector": sector,
            "Positions": int(values["Positions"]),
            "Allocation": round(values["Allocation"], 2),
            "Portfolio %": round(values["Allocation"] / capital * 100.0, 2) if capital else 0.0,
        }
        for sector, values in sorted(sectors.items(), key=lambda item: -item[1]["Allocation"])
    ]

    regime = _attribute(system, "market_regime", None)
    regime_data = {
        "type": str(_attribute(regime, "regime_type", "UNKNOWN")),
        "vix": _number(_attribute(regime, "vix_level", 0.0)),
        "volatility": str(_attribute(regime, "volatility_regime", "UNKNOWN")),
        "exposure": _number(_attribute(regime, "recommended_exposure", 1.0)),
        "cash": _number(_attribute(regime, "recommended_cash_pct", 0.0)),
        "confidence": _number(_attribute(regime, "confidence", 0.0)),
    }

    error_summary = {}
    get_error_summary = getattr(system, "get_error_summary", None)
    if callable(get_error_summary):
        try:
            error_summary = get_error_summary() or {}
        except Exception:
            error_summary = {}

    filter_rows = [
        {"Reason": str(reason).replace("_", " ").title(), "Count": int(count)}
        for reason, count in sorted(
            dict(_attribute(system, "_filter_stats", {}) or {}).items(),
            key=lambda item: -item[1],
        )
        if _number(count) > 0
    ]
    gate_rows = [
        {"Gate": str(reason).replace("_", " ").title(), "Count": int(count)}
        for reason, count in sorted(
            dict(_attribute(system, "_gate_stats", {}) or {}).items(),
            key=lambda item: -item[1],
        )
        if _number(count) > 0
    ]

    analysis_timestamp = _iso_timestamp(_attribute(system, "analysis_timestamp", datetime.now().astimezone()))
    health = "DEGRADED" if error_summary.get("provider_degraded") or error_summary.get("provider_circuit_open") else "HEALTHY"
    if universe_size and failed_count / universe_size >= 0.2:
        health = "DEGRADED"

    return {
        "scan_label": scan_label,
        "analysis_timestamp": analysis_timestamp,
        "currency_symbol": currency_symbol,
        "capital": float(capital),
        "universe_count": universe_size,
        "qualified_count": qualified_count,
        "filtered_count": filtered_count,
        "failed_count": failed_count,
        "selected_count": len(recommendations),
        "deployment": round(deployment, 2),
        "deployment_pct": round(deployment / capital * 100.0, 2) if capital else 0.0,
        "cash_remaining": round(max(0.0, capital - deployment), 2),
        "risk_at_stop": round(risk_at_stop, 2),
        "risk_at_stop_pct": round(risk_at_stop / capital * 100.0, 2) if capital else 0.0,
        "records": records,
        "details": details,
        "sector_rows": sector_rows,
        "regime": regime_data,
        "health": health,
        "failed_tickers": failed_tickers,
        "filter_rows": filter_rows,
        "gate_rows": gate_rows,
        "error_summary": error_summary,
        "chase_alerts": list(_attribute(system, "chase_alerts", []) or []),
        "evidence": evidence_banner(first_decision, MARKET_SCAN_EVIDENCE_KEY),
        "methodology_note": (
            "Candidates pass liquidity, data-quality, technical, fundamental, risk, and portfolio gates. "
            + (
                "Published allocations are executable model sizes after final-action and event-risk controls."
                if evidence["permits_recommendation"]
                else "Published allocations are the most the risk budget allows after final-action and "
                "event-risk controls; the model is not validated, so they are not advice to buy."
            )
        ),
    }


def market_scan_csv(report: dict[str, Any]) -> str:
    """Export the decision table without converting numeric values into strings."""
    records = list(report.get("records") or [])
    if not records:
        return ""
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=list(records[0].keys()))
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()
