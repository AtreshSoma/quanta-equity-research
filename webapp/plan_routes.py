"""Plan Simulator and Plan Check: JSON API (Flask blueprint).

POST /api/plan/simulate  -- how often a long plan's target is reached before its stop
POST /api/plan/check     -- a pre-trade checklist for one intended BUY

Both are POST, so they carry the app's CSRF guard (X-QUANTA-Request: 1 and a
matching Origin), the same guard My Portfolio uses. Input is validated strictly
and every error is ``{"error": "..."}`` with a 400 (422 when the history cannot
support a simulation). QUANTA never places orders; these answers are checks,
not recommendations.

The loaders are module-level names so tests can replace them:
``load_bars(symbol)``, ``research_read(symbol)``, ``holdings_read()``.
"""
from __future__ import annotations

import logging
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import pandas as pd
from flask import Blueprint, jsonify, request

# core/ holds the engine package; the app already puts it on sys.path, but a
# standalone import (tests) needs the same.
_CORE = Path(__file__).resolve().parent.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from engine import plan_check as _plan_check  # noqa: E402
from engine import plan_simulator as _plan_sim  # noqa: E402
from engine import research_cache as _research_cache  # noqa: E402
from engine import thresholds as _T  # noqa: E402

log = logging.getLogger("quanta.plan")
bp = Blueprint("plan", __name__)

# The same symbol grammar the app uses for every ticker field.
TICKER_RE = re.compile(r"[A-Z0-9^.=&-]{1,32}")
REQUEST_HEADER = "X-QUANTA-Request"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
PRICE_MAX = 1e7
MONEY_MAX = 1e13
SHARES_MAX = 1e9


class PlanInputError(ValueError):
    """A request field failed validation; the message is shown to the user."""


class PlanDataError(RuntimeError):
    """Price bars could not be fetched right now."""


# ---------------------------------------------------------------------------
# Guard (mirrors My Portfolio's: loopback host, custom header, same-origin)
# ---------------------------------------------------------------------------

def _hostname(hostport: str) -> str:
    return (urlsplit("//" + (hostport or "")).hostname or "").lower()


@bp.before_request
def _guard():
    if _hostname(request.host) not in LOOPBACK:
        return jsonify({"error": "The plan tools are only served on this computer (loopback)."}), 403
    if request.method == "POST":
        if request.headers.get(REQUEST_HEADER) != "1":
            return jsonify({"error": "Missing request header."}), 403
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != (request.host or "").lower():
            return jsonify({"error": "Cross-origin request refused."}), 403
    return None


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@bp.errorhandler(PlanInputError)
def _bad_input(exc: PlanInputError):
    return jsonify({"error": str(exc)}), 400


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise PlanInputError("Send a JSON object.")
    return data


def _ticker(body: Dict[str, Any]) -> str:
    raw = body.get("ticker")
    if not isinstance(raw, str) or not raw.strip():
        raise PlanInputError("ticker is required")
    sym = raw.strip().upper()
    if not TICKER_RE.fullmatch(sym):
        raise PlanInputError("ticker contains unsupported characters")
    return sym


def _number(body: Dict[str, Any], key: str, *, lo: float, hi: float,
            required: bool = True, default: Optional[float] = None) -> Optional[float]:
    v = body.get(key)
    if v is None:
        if required:
            raise PlanInputError(f"{key} is required")
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PlanInputError(f"{key} must be a number")
    f = float(v)
    if not math.isfinite(f):
        raise PlanInputError(f"{key} must be a finite number")
    if not (lo <= f <= hi):
        raise PlanInputError(f"{key} must be between {lo:g} and {hi:g}")
    return f


def _integer(body: Dict[str, Any], key: str, *, lo: int, hi: int, default: int) -> int:
    f = _number(body, key, lo=lo, hi=hi, required=False, default=float(default))
    if f != int(f):
        raise PlanInputError(f"{key} must be a whole number")
    return int(f)


def _plan_prices(body: Dict[str, Any]) -> Tuple[float, float, float]:
    entry = _number(body, "entry", lo=1e-6, hi=PRICE_MAX)
    stop = _number(body, "stop", lo=1e-6, hi=PRICE_MAX)
    target = _number(body, "target", lo=1e-6, hi=PRICE_MAX)
    return entry, stop, target


# ---------------------------------------------------------------------------
# Loaders (replaceable in tests)
# ---------------------------------------------------------------------------

def load_bars(symbol: str) -> pd.DataFrame:
    """Daily bars through the app's cached history path.

    The app wraps ``yf.Ticker.history`` with a process-wide cache (app.py,
    ``_cached_history``), so this call shares the cache the Research page uses.
    Empty responses are not cached upstream, so a retry reaches the provider.
    """
    import yfinance as yf

    try:
        frame = yf.Ticker(symbol).history(period="5y", interval="1d", auto_adjust=False)
    except Exception as exc:  # noqa: BLE001 - the provider error is not shown
        log.info("Daily bars for %s unavailable: %s", symbol, type(exc).__name__)
        raise PlanDataError("Price bars could not be fetched right now.") from exc
    if frame is None or getattr(frame, "empty", True):
        raise PlanDataError("No daily price bars were returned for this symbol.")
    return frame


# The web app's tear-sheet cache reader, registered by init_plan(app, ...).
# The Research page caches its payload there (app._analysis_result_cache);
# engine.research_cache only holds Picks/Portfolio deep confirmations, so
# reading that alone missed a Research run the user had just made.
_TEAR_SHEET_READER = None


def research_read(symbol: str) -> Optional[Any]:
    """The cached Research result for this ticker, or None. Never starts an analysis.

    Prefers the Research page's tear-sheet payload (a dict), then the engine's
    deep-confirmation cache (an analysis result object).
    """
    if _TEAR_SHEET_READER is not None:
        try:
            payload = _TEAR_SHEET_READER(symbol)
        except Exception as exc:  # noqa: BLE001 - a cache miss must never fail the check
            log.info("Tear-sheet cache read failed for %s: %s", symbol, type(exc).__name__)
            payload = None
        if payload:
            return payload
    return _research_cache.get(symbol, _T.RESEARCH.MODE)


def _as_date(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _research_context(symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[date], Optional[str]]:
    """(decision dict, next earnings date, sector) from the cached Research result."""
    res = research_read(symbol)
    if res is None:
        return None, None, None
    if isinstance(res, dict):
        # The Research page's tear-sheet payload.
        decision = res.get("decision") if isinstance(res.get("decision"), dict) else None
        cat = res.get("catalysts") if isinstance(res.get("catalysts"), dict) else {}
        intel = res.get("market_intel") if isinstance(res.get("market_intel"), dict) else {}
        earnings = (_as_date(res.get("next_earnings_date")) or _as_date(intel.get("next_earnings_date"))
                    or _as_date(cat.get("earnings_date")))
        sector = res.get("sector")
        return decision, earnings, (str(sector) if sector and sector != "Unknown" else None)
    decision = getattr(res, "decision", None)
    if not isinstance(decision, dict):
        decision = None
    earnings = None
    for attr in ("next_earnings_date", "earnings_date"):
        earnings = _as_date(getattr(res, attr, None))
        if earnings:
            break
    if earnings is None:
        cat = getattr(res, "catalysts", None)
        earnings = _as_date(getattr(cat, "earnings_date", None))
    sector = getattr(res, "sector", None)
    return decision, earnings, (str(sector) if sector and sector != "Unknown" else None)


def _research_facts(symbol: str) -> Tuple[Optional[date], Optional[Dict[str, Any]]]:
    """(last earnings release date, today's live move) from the cached Research read.

    The live move is ``technical.live_preview``: present only while today's bar is
    excluded from the scores. Either part is None when it is not in the read.
    """
    res = research_read(symbol)
    if res is None:
        return None, None
    if isinstance(res, dict):
        cat = res.get("catalysts") if isinstance(res.get("catalysts"), dict) else {}
        tech = res.get("technical") if isinstance(res.get("technical"), dict) else {}
        live = tech.get("live_preview")
        return _as_date(cat.get("last_earnings_date")), (dict(live) if isinstance(live, dict) and live else None)
    cat = getattr(res, "catalysts", None)
    tech = getattr(res, "technical", None)
    live = getattr(tech, "live_preview", None)
    return (_as_date(getattr(cat, "last_earnings_date", None)),
            dict(live) if isinstance(live, dict) and live else None)


def _provider_next_earnings(symbol: str) -> Optional[date]:
    """The next report date from the provider's earnings table, through the Research
    engine's six-hour cache. Used only when the cached read has no future date.
    Never raises: an unavailable provider leaves the earnings check to report it as unknown."""
    try:
        import yfinance as yf
        from TradingAnalysis_ELITE import _earnings_schedule
        return _earnings_schedule(yf.Ticker(symbol), date.today())[0]
    except Exception as exc:  # noqa: BLE001 - the checklist must still answer
        log.info("Next earnings for %s unavailable: %s", symbol, type(exc).__name__)
        return None


def holdings_read() -> Optional[Dict[str, Any]]:
    """Connected My Portfolio holdings, or None when nothing is loaded.

    ``{"account_value", "positions": {SYM: {"market_value", "sector"}}}``. The
    sector comes from the portfolio view; a failed read leaves it blank, which
    the sector check reports as unknown rather than as clear.
    """
    try:
        from portfolio_routes import current_holdings, service
    except Exception:  # noqa: BLE001 - My Portfolio is optional
        return None
    snap = current_holdings()
    if not snap:
        return None
    positions: Dict[str, Dict[str, Any]] = {}
    for sym, p in (snap.get("positions") or {}).items():
        positions[_norm_sym(sym)] = {"market_value": p.get("market_value"), "sector": ""}
    try:
        rows = service().view().get("portfolio", {}).get("positions") or []
    except Exception:  # noqa: BLE001 - sectors are best effort
        rows = []
    for r in rows:
        sym = _norm_sym(r.get("symbol", ""))
        slot = positions.setdefault(sym, {"market_value": None, "sector": ""})
        slot["sector"] = str((r.get("market") or {}).get("sector") or "")
        if slot["market_value"] is None:
            slot["market_value"] = (r.get("broker") or {}).get("market_value")
    return {"account_value": snap.get("account_value"), "positions": positions}


def filings_read(symbol: str) -> Optional[Dict[str, Any]]:
    """The last 30 days of 8-K filings for Plan Check (events_routes.plan_filings).

    Never raises: a failure is returned as status "unavailable", which the check
    reports as SKIPPED, so an unreachable SEC cannot break the checklist.
    """
    try:
        import events_routes
        return events_routes.plan_filings(symbol)
    except Exception as exc:  # noqa: BLE001 - the 8-K check is optional for the checklist
        log.info("Material filings for %s unavailable: %s", symbol, type(exc).__name__)
        return {"status": "unavailable", "message": "The 8-K check could not run.", "events": []}


def _norm_sym(sym: Any) -> str:
    return str(sym or "").strip().upper().replace(".", "-")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/api/plan/simulate", methods=["POST"])
def simulate_route():
    body = _body()
    sym = _ticker(body)
    side = body.get("side", "long")
    if side != "long":
        raise PlanInputError("only long plans are supported")
    entry, stop, target = _plan_prices(body)
    if not (stop < entry < target):
        raise PlanInputError("stop must be below entry and target above it")
    horizon = _integer(body, "horizon_sessions", lo=_plan_sim.MIN_HORIZON, hi=_plan_sim.MAX_HORIZON,
                       default=_plan_sim.DEFAULT_HORIZON)
    lookback = _integer(body, "lookback_sessions", lo=_plan_sim.MIN_LOOKBACK, hi=_plan_sim.MAX_LOOKBACK,
                        default=_plan_sim.DEFAULT_LOOKBACK)
    paths = _integer(body, "paths", lo=_plan_sim.MIN_PATHS, hi=_plan_sim.MAX_PATHS,
                     default=_plan_sim.DEFAULT_PATHS)
    seed = _integer(body, "seed", lo=0, hi=2**31 - 1, default=_plan_sim.DEFAULT_SEED)
    block = _number(body, "block_mean", lo=_plan_sim.MIN_BLOCK_MEAN, hi=_plan_sim.MAX_BLOCK_MEAN,
                    required=False, default=_plan_sim.DEFAULT_BLOCK_MEAN)
    drift = body.get("drift", "zero")
    if drift not in ("historical", "zero"):
        raise PlanInputError("drift must be 'historical' or 'zero'")

    try:
        bars = load_bars(sym)
    except PlanDataError as exc:
        return jsonify({"error": str(exc), "available": False, "ticker": sym}), 502
    try:
        result = _plan_sim.simulate_plan(
            bars, entry=entry, stop=stop, target=target, horizon_sessions=horizon,
            lookback_sessions=lookback, block_mean=block, paths=paths, seed=seed,
            drift=drift, ticker=sym, side="long")
    except _plan_sim.PlanSimulationError as exc:
        return jsonify({"error": str(exc), "available": False, "ticker": sym}), 422
    return jsonify({"ticker": sym, **result})


@bp.route("/api/plan/check", methods=["POST"])
def check_route():
    body = _body()
    sym = _ticker(body)
    side = body.get("side", "buy")
    if side not in _plan_check.SUPPORTED_SIDES:
        raise PlanInputError("only buy plans are checked; QUANTA never places orders")
    shares = _number(body, "shares", lo=1e-9, hi=SHARES_MAX)
    entry, stop, target = _plan_prices(body)
    account = _number(body, "account_value", lo=1e-6, hi=MONEY_MAX, required=False)
    cash = _number(body, "cash", lo=0.0, hi=MONEY_MAX, required=False)
    plan_day = body.get("date")
    if plan_day is None:
        plan_date = date.today()
    else:
        if not isinstance(plan_day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan_day):
            raise PlanInputError("date must be YYYY-MM-DD")
        try:
            plan_date = datetime.strptime(plan_day, "%Y-%m-%d").date()
        except ValueError as exc:
            raise PlanInputError("date must be a real calendar date") from exc

    decision, next_earnings, sector = _research_context(sym)
    last_earnings, todays_move = _research_facts(sym)
    today = date.today()
    # A cached read with no future report date (an old payload, or one whose date has
    # passed) asks the provider's table once; the Research engine caches that lookup.
    if research_read(sym) is not None and (next_earnings is None or next_earnings < today):
        next_earnings = _provider_next_earnings(sym) or next_earnings
    holdings = holdings_read()
    if sector is None and holdings:
        sector = (holdings["positions"].get(_norm_sym(sym)) or {}).get("sector") or None
    result = _plan_check.check_plan(
        ticker=sym, shares=shares, entry=entry, stop=stop, target=target, plan_date=plan_date,
        today=today, account_value=account, cash=cash,
        horizon_sessions=_T.LEVELS.RESEARCH_HORIZON_DAYS, research=decision,
        next_earnings=next_earnings, holdings=holdings, sector=sector, side="buy",
        material_filings=filings_read(sym), last_earnings=last_earnings, todays_move=todays_move)
    result["context"] = {
        "research_cached": decision is not None,
        "earnings_known": next_earnings is not None,
        "holdings_connected": holdings is not None,
        "account_value_source": ("entered" if account else
                                 "holdings" if holdings and holdings.get("account_value") else "none"),
    }
    return jsonify(result)


def init_plan(app, tear_sheet_reader=None) -> None:
    """Register the plan blueprint on the app.

    ``tear_sheet_reader(symbol) -> dict | None`` returns the Research page's
    cached payload for a ticker (app.py passes its own cache lookup).
    """
    global _TEAR_SHEET_READER
    _TEAR_SHEET_READER = tear_sheet_reader
    app.register_blueprint(bp)
