"""My Portfolio: read-only Robinhood holdings -> QUANTA guidance.

Routes (all under /api/portfolio):

    GET  /status          connection + refresh + analysis state
    POST /connect         start Robinhood sign-in; returns the URL to open
    GET  /oauth/callback  Robinhood redirects here after sign-in
    POST /disconnect      forget every credential and all holdings in memory
    GET  /accounts        accounts (opaque handles and labels only)
    POST /refresh         read one account and start analysing its holdings
    GET  /                the full view (broker | market | QUANTA | guidance)
    GET  /diagnostics     live tool schemas and payload shapes (no values)
    GET  /performance     simulation / ledger performance, attribution, markers
    GET  /intraday        today's holdings over the latest session's 5-minute bars (simulation)
    POST /whatif          metrics after selling some holdings to cash (same rules as the view)
    GET  /outcomes        trade outcomes and recommendation outcomes (kept separate)
    POST /exposure        Sector / Asset class / Geography / Holdings views
    GET  /events          Today / This Week feed: earnings, filings, macro, news
    POST /plan            scenarios and candidate fit for the entered plan
    GET  /activity        realized P&L for one window (day .. all, ytd), bucketed
    GET  /records         open lots, order fills and closed trades (whichever exist)
    GET  /journal         the decision journal's summary and dated call changes

The rest of the app has no request-forgery protection, because until now it
held nothing private. These routes do, so they carry their own guards:

* the Host header must be loopback (blocks DNS-rebinding reads of holdings);
* POSTs need ``X-QUANTA-Request: 1`` (a custom header a cross-site page cannot
  send without a CORS preflight this server never approves) and, when the
  browser sends an Origin, it must be this server;
* every response is ``no-store`` with ``Referrer-Policy: no-referrer``, so
  holdings are not cached and the OAuth code never leaks through a Referer.

Robinhood credentials never reach the browser: the page only ever sees the
authorization URL (which carries no secret) and state labels.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, redirect, request

from brokers.redact import RedactingFilter, get_logger, redact
from portfolio.insights import Insights, MarketSources
from portfolio.provider import AuthExpired, NotConnected, PortfolioProvider, ProviderError
from portfolio.service import PortfolioService

log = get_logger("QUANTA-PORTFOLIO")

bp = Blueprint("my_portfolio", __name__, url_prefix="/api/portfolio")

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
REQUEST_HEADER = "X-QUANTA-Request"
_HANDLE = re.compile(r"[0-9a-f]{16}")

_service: Optional[PortfolioService] = None


def init_portfolio(app, analyze: Callable, market_returns: Optional[Callable] = None,
                   provider: Optional[PortfolioProvider] = None,
                   sources: Optional[MarketSources] = None) -> PortfolioService:
    """Create the service and register the routes. Idempotent per app."""
    global _service
    if provider is None:
        from brokers.robinhood import RobinhoodProvider
        provider = RobinhoodProvider()
    _service = PortfolioService(provider, analyze, market_returns)
    _service.insights = Insights(_service, sources or MarketSources())
    if "my_portfolio" not in app.blueprints:
        app.register_blueprint(bp)
    # The request log prints full URLs, including the OAuth callback's code and state.
    wz = logging.getLogger("werkzeug")
    if not any(isinstance(f, RedactingFilter) for f in wz.filters):
        wz.addFilter(RedactingFilter())
    return _service


def service() -> PortfolioService:
    if _service is None:
        raise RuntimeError("My Portfolio is not initialised")
    return _service


# --- guards -----------------------------------------------------------------

def current_holdings() -> Optional[dict]:
    """[B-89] Loaded broker positions for the Picks scan (read-only, from
    memory). None when nothing is loaded or the read fails: Picks then says
    holdings were not checked."""
    try:
        return _service.holdings_snapshot() if _service is not None else None
    except Exception as exc:  # noqa: BLE001 - a scan must not fail on this read
        log.info("Holdings snapshot unavailable for Picks: %s", type(exc).__name__)
        return None


def _hostname(hostport: str) -> str:
    return (urlsplit("//" + (hostport or "")).hostname or "").lower()


@bp.before_request
def _guard():
    if _hostname(request.host) not in LOOPBACK:
        return jsonify({"error": "My Portfolio is only served on this computer (loopback)."}), 403
    if request.method == "POST":
        if request.headers.get(REQUEST_HEADER) != "1":
            return jsonify({"error": "Missing request header."}), 403
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != request.host.lower():
            return jsonify({"error": "Cross-origin request refused."}), 403
    return None


@bp.after_request
def _headers(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def _error(exc: ProviderError):
    svc = _service
    state = svc.provider.state().value if svc else "provider_error"
    code = 401 if isinstance(exc, (AuthExpired, NotConnected)) else 502
    if exc.code in ("UNKNOWN_ACCOUNT", "INVALID_STATE"):
        code = 400
    return jsonify({"error": redact(str(exc)), "code": exc.code, "state": state}), code


# --- routes -----------------------------------------------------------------

@bp.get("/status")
def status():
    return jsonify(service().status())


def _redirect_uri() -> str:
    """The OAuth callback on this server.

    RFC 8252 (s. 7.3, 8.3) asks native apps to use the loopback IP literal
    rather than ``localhost``, which some authorization servers refuse. A page
    opened as localhost is served by the same socket, so it is rewritten to
    127.0.0.1; the sign-in state lives in server memory, not in cookies.
    """
    host = request.host
    if _hostname(host) == "localhost":
        port = urlsplit("//" + host).port
        host = "127.0.0.1" + (f":{port}" if port else "")
    return f"http://{host}/api/portfolio/oauth/callback"


@bp.post("/connect")
def connect():
    redirect_uri = _redirect_uri()
    try:
        url = service().provider.begin_connect(redirect_uri)
    except ProviderError as exc:
        log.warning("Robinhood connect failed: %s", exc)
        return _error(exc)
    return jsonify({"authorize_url": url, "state": service().provider.state().value})


@bp.get("/oauth/callback")
def oauth_callback():
    params = {k: request.args.get(k, "") for k in ("code", "state", "error", "error_description", "iss")}
    try:
        service().provider.complete_connect(params)
        log.info("Robinhood connected")
    except ProviderError as exc:
        log.warning("Robinhood sign-in did not complete: %s", exc)
    # The status endpoint reports success or the reason; the URL carries nothing.
    return redirect("/#holdings", code=303)


@bp.post("/disconnect")
def disconnect():
    service().disconnect()
    log.info("Robinhood disconnected; credentials and holdings cleared from memory")
    return jsonify(service().status())


@bp.get("/accounts")
def accounts():
    try:
        return jsonify({"accounts": service().accounts()})
    except ProviderError as exc:
        return _error(exc)


@bp.post("/refresh")
def refresh():
    body: Any = request.get_json(silent=True) or {}
    handle = str(body.get("account") or "")
    if not _HANDLE.fullmatch(handle):
        return jsonify({"error": "Choose an account first.", "code": "UNKNOWN_ACCOUNT"}), 400
    try:
        return jsonify(service().refresh(handle, run_analysis=body.get("analyze", True) is not False))
    except ProviderError as exc:
        log.warning("Portfolio refresh failed: %s", exc)
        return _error(exc)


@bp.get("")
@bp.get("/")
def view():
    return jsonify(service().view())


def _insights() -> Insights:
    ins = service().insights
    if ins is None:
        raise RuntimeError("My Portfolio insights are not initialised")
    return ins


def _guarded(fn, *args):
    """Insights failures are one section's problem: report it, keep the page alive."""
    try:
        return jsonify(fn(*args))
    except ProviderError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Portfolio insight failed: %s", type(exc).__name__)
        return jsonify({"available": False, "reason": f"This section could not be built ({type(exc).__name__})."})


@bp.get("/performance")
def performance():
    return _guarded(_insights().performance)


@bp.get("/intraday")
def intraday():
    return _guarded(_insights().intraday)


@bp.post("/whatif")
def whatif():
    body: Any = request.get_json(silent=True) or {}
    changes = body.get("changes") if isinstance(body.get("changes"), list) else []
    return _guarded(_insights().whatif, changes)


@bp.get("/outcomes")
def outcomes():
    return _guarded(_insights().outcomes)


@bp.post("/exposure")
def exposure():
    body: Any = request.get_json(silent=True) or {}
    prefs = body.get("prefs") if isinstance(body.get("prefs"), dict) else {}
    return _guarded(_insights().exposure, prefs)


@bp.get("/events")
def events():
    return _guarded(_insights().events)


@bp.post("/plan")
def plan():
    body: Any = request.get_json(silent=True) or {}
    cands = body.get("candidates") if isinstance(body.get("candidates"), list) else []
    profile = body.get("profile") if isinstance(body.get("profile"), dict) else {}
    return _guarded(_insights().plan, profile, cands, body.get("run") is True)


@bp.get("/activity")
def activity():
    return _guarded(_insights().activity, str(request.args.get("period") or "month"))


@bp.get("/records")
def records():
    return _guarded(_insights().records)


@bp.get("/journal")
def journal():
    return _guarded(_insights().journal_info)


@bp.get("/diagnostics")
def diagnostics():
    prov = service().provider
    diag = getattr(prov, "diagnostics", None)
    return jsonify(diag() if callable(diag) else {"status": prov.status()})
