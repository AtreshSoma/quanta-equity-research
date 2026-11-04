"""Material filings: recent SEC 8-K filings and this stock's past reactions (Flask blueprint).

GET /api/events?symbol=AAPL&days=90

Answers the last ``days`` days of 8-K and 8-K/A filings, newest first, each tagged
with its items, and a past-reaction study per item (see engine/event_study.py).
The study uses about five years of daily bars for the stock and SPY.

The route is read-only, so it needs only the loopback host check. Results are
cached in-process for CACHE_TTL_SECONDS, keyed by symbol. Unavailable answers are
not cached, so a retry reaches SEC again.

The loaders are module-level names so tests can replace them:
``_edgar()``, ``_bars(symbol)``.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

_CORE = Path(__file__).resolve().parent.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from engine import event_study as _es  # noqa: E402
from engine import sec_events as _se  # noqa: E402

import plan_routes  # noqa: E402  - the shared ticker grammar and the daily-bar loader
import request_guard  # noqa: E402

log = logging.getLogger("quanta.events")
bp = Blueprint("events", __name__)

TICKER_RE = plan_routes.TICKER_RE
CACHE_TTL_SECONDS = 30 * 60

_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


class EventsInputError(ValueError):
    """A request field failed validation; the message is shown to the user."""


# Guard: loopback host (the route is GET-only, so the CSRF check never applies)
bp.before_request(request_guard.guard)


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@bp.errorhandler(EventsInputError)
def _bad_input(exc: EventsInputError):
    return jsonify({"error": str(exc)}), 400


# ---------------------------------------------------------------------------
# Loaders (replaceable in tests)
# ---------------------------------------------------------------------------

def _edgar():
    """The process-wide EDGAR client shared with the corporate-event gate (contact, pacing, cache)."""
    from engine import corporate_events
    return corporate_events.default_edgar()


def _bars(symbol: str):
    """Daily bars through the app's cached history path (plan_routes.load_bars)."""
    return plan_routes.load_bars(symbol)


# ---------------------------------------------------------------------------
# Report with cache
# ---------------------------------------------------------------------------

def _cache_get(symbol: str) -> Optional[Dict[str, Any]]:
    with _CACHE_LOCK:
        hit = _CACHE.get(symbol)
        if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    return None


def _cache_put(symbol: str, report: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[symbol] = (time.time(), report)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def report_for(symbol: str, today: Optional[date] = None) -> Tuple[Dict[str, Any], bool]:
    """(report, cached). The report covers the full year window; routes filter it to ``days``."""
    today = today or date.today()
    hit = _cache_get(symbol)
    if hit is not None and hit.get("as_of") == today.isoformat():
        return hit, True
    report = _es.build_report(symbol, days=_se.MAX_DAYS, today=today, edgar=_edgar(), bars_loader=_bars)
    if report.get("status") == "ok" and report.get("study_status") == "ok":
        _cache_put(symbol, report)
    return report, False


def plan_filings(symbol: str, today: Optional[date] = None) -> Dict[str, Any]:
    """The filings Plan Check reads (sec_events.plan_window): the last 30 days, no price study."""
    return _se.plan_window(symbol, today or date.today(), _edgar())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _symbol(raw: Optional[str]) -> str:
    sym = (raw or "").strip().upper()
    if not sym:
        raise EventsInputError("symbol is required")
    if not TICKER_RE.fullmatch(sym):
        raise EventsInputError("symbol contains unsupported characters")
    return sym


def _days(raw: Optional[str]) -> int:
    if raw is None or raw == "":
        return _se.DEFAULT_DAYS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise EventsInputError("days must be a whole number") from exc
    if not (_se.MIN_DAYS <= value <= _se.MAX_DAYS):
        raise EventsInputError(f"days must be between {_se.MIN_DAYS} and {_se.MAX_DAYS}")
    return value


@bp.route("/api/events", methods=["GET"])
def events_route():
    sym = _symbol(request.args.get("symbol"))
    days = _days(request.args.get("days"))
    today = date.today()
    try:
        report, cached = report_for(sym, today)
    except Exception as exc:  # noqa: BLE001 - never a traceback to the page
        log.info("Material filings for %s unavailable: %s", sym, type(exc).__name__)
        return jsonify({"error": "Material filings could not be read right now.", "ticker": sym,
                        "available": False}), 502
    if report.get("status") == "unavailable":
        body = {"error": report.get("message") or "SEC EDGAR is unavailable right now.",
                "ticker": sym, "available": False, "reason": report.get("reason"),
                "fix": report.get("fix", "")}
        return jsonify(body), 502
    out = dict(report)
    out["recent"] = _se.within_days(report.get("recent") or [], days, today)
    out["days"] = days
    out["cached"] = cached
    out["available"] = True
    return jsonify(out)
