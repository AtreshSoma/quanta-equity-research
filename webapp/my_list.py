"""My list (saved symbols) and price alerts that run in the background.

* **My list** is the reader's own set of symbols, kept on this computer in
  SQLite (``data/my_list.sqlite3``), not in one browser's storage. It is a
  different thing from the "Watchlist" Decision call and Discover's Watchlist
  tab, which are the model's.
* **Alerts** are price levels on a symbol: above or below, with an optional
  label (a Decision's stop or target, or the reader's own). A checker thread
  reads each alerted symbol's price every ``QUANTA_ALERT_INTERVAL`` seconds
  (300 by default) while its market is open, and posts a desktop notification
  (Windows or macOS) when a level is crossed. Each alert fires once: it is
  claimed with a conditional update, so two checks can never announce it twice.
  Fired alerts wait in the inbox (the bell in the top bar) until dismissed, so
  a notification nobody saw is not lost. Optional email: set
  ``QUANTA_SMTP_HOST`` (and ``QUANTA_SMTP_PORT``, ``QUANTA_SMTP_USER``,
  ``QUANTA_SMTP_PASSWORD``, ``QUANTA_ALERT_EMAIL``) in ``.env``.

Nothing here places orders or reads an account.
"""
from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

from my_list_store import (  # noqa: F401 - re-exported for callers and tests
    DEFAULT_EXPIRY_DAYS, DEFAULT_PATH, LABEL_MAX, MAX_ACTIVE_ALERTS, MAX_SYMBOLS, PATH_ENV,
    SYMBOL_RE, MyListStore, _iso, _now, clean_symbol, resolve_path,
)

logger = logging.getLogger(__name__)

bp = Blueprint("my_list", __name__)

def crossed(alert: Dict[str, Any], price: float) -> bool:
    level = float(alert["level"])
    return price >= level if alert["direction"] == "above" else price <= level


def alert_message(alert: Dict[str, Any], price: float) -> str:
    what = alert.get("label") or ("Price" if alert.get("source") == "manual" else "Level")
    verb = "rose to" if alert["direction"] == "above" else "fell to"
    return f"{alert['symbol']} {verb} {price:,.2f}: {what} {float(alert['level']):,.2f}"


QuoteFn = Callable[[str], Optional[Dict[str, Any]]]
NotifyFn = Callable[[str, str], bool]


def check_alerts(store: MyListStore, quote: QuoteFn, notify: NotifyFn,
                 only_open: bool = True) -> List[Dict[str, Any]]:
    """Read each alerted symbol once and fire every crossed level. Returns the fired alerts.

    ``quote(symbol)`` returns ``{"price": float, "market_open": bool}`` or None.
    With ``only_open`` a closed market is skipped: a level is judged on the
    session's trading, not on a stale close.
    """
    pending = store.alerts(active_only=True)
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for a in pending:
        by_symbol.setdefault(a["symbol"], []).append(a)
    fired: List[Dict[str, Any]] = []
    for symbol, rows in by_symbol.items():
        try:
            q = quote(symbol)
        except Exception as exc:  # noqa: BLE001 - one symbol must not stop the rest
            logger.info("alerts: quote failed for %s: %s", symbol, type(exc).__name__)
            continue
        if not q or q.get("price") is None:
            continue
        if only_open and not q.get("market_open"):
            continue
        price = float(q["price"])
        for a in rows:
            if crossed(a, price) and store.claim(a["id"], price):
                fired.append({**a, "triggered_price": price, "message": alert_message(a, price)})
    if fired:
        title = f"QUANTA: {len(fired)} price alert{'s' if len(fired) != 1 else ''}"
        body = "; ".join(f["message"] for f in fired[:3]) + (f"; +{len(fired) - 3} more" if len(fired) > 3 else "")
        try:
            if notify(title, body):
                for f in fired:
                    store.mark_notified(f["id"])
        except Exception as exc:  # noqa: BLE001 - the inbox still holds them
            logger.warning("alerts: notification failed: %s", exc)
        send_email(title, "\n".join(f["message"] for f in fired))
    return fired


def send_email(subject: str, body: str) -> bool:
    """Email the alert when SMTP is configured in the environment; otherwise do nothing."""
    host, to = os.environ.get("QUANTA_SMTP_HOST"), os.environ.get("QUANTA_ALERT_EMAIL")
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("QUANTA_SMTP_FROM") or os.environ.get("QUANTA_SMTP_USER") or to
    msg["To"] = to
    msg.set_content(body + "\n\nSent by QUANTA on this computer. Information only, not investment advice.")
    try:
        port = int(os.environ.get("QUANTA_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            user = os.environ.get("QUANTA_SMTP_USER")
            if user:
                smtp.login(user, os.environ.get("QUANTA_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - email is best effort
        logger.warning("alerts: email failed: %s", type(exc).__name__)
        return False


class AlertWorker:
    """Checks alerts every ``interval`` seconds on a daemon thread."""

    def __init__(self, store: MyListStore, quote: QuoteFn, notify: NotifyFn, interval: float = 300.0):
        self.store, self.quote, self.notify = store, quote, notify
        self.interval = max(30.0, float(interval))
        self.last_run: Optional[str] = None
        self.last_fired = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._run_lock = threading.Lock()

    def run_once(self, only_open: bool = True) -> List[Dict[str, Any]]:
        if not self._run_lock.acquire(blocking=False):
            return []
        try:
            fired = check_alerts(self.store, self.quote, self.notify, only_open=only_open)
            self.last_run = _iso(_now())
            self.last_fired = len(fired)
            return fired
        finally:
            self._run_lock.release()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.run_once()
                except Exception as exc:  # noqa: BLE001 - keep checking
                    logger.warning("alerts: check failed: %s", exc)

        self._thread = threading.Thread(target=loop, name="price-alerts", daemon=True)
        self._thread.start()
        logger.info("Price alerts checked every %.0f seconds while markets are open", self.interval)

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


# ---------------------------------------------------------------------------
# Routes (the app-wide guard already requires loopback and X-QUANTA-Request on writes)
# ---------------------------------------------------------------------------

_STATE: Dict[str, Any] = {}


def _store() -> MyListStore:
    return _STATE["store"]


def _payload() -> Dict[str, Any]:
    store = _store()
    worker: Optional[AlertWorker] = _STATE.get("worker")
    inbox = store.inbox()
    return {
        "symbols": store.symbols(),
        "alerts": store.alerts(active_only=True),
        "inbox": inbox,
        "unread": len(inbox),
        "checker": {
            "running": bool(worker and worker.running),
            "interval_seconds": worker.interval if worker else None,
            "last_run": worker.last_run if worker else None,
        },
        "email": bool(os.environ.get("QUANTA_SMTP_HOST") and os.environ.get("QUANTA_ALERT_EMAIL")),
    }


@bp.route("/api/my-list", methods=["GET"])
def my_list_get():
    return jsonify(_payload())


@bp.route("/api/my-list", methods=["POST"])
def my_list_add():
    data = request.get_json(silent=True) or {}
    sym = clean_symbol(data.get("symbol"))
    if not sym:
        return jsonify({"error": "That is not a supported symbol."}), 400
    try:
        _store().add_symbol(sym)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(_payload())


@bp.route("/api/my-list/<symbol>", methods=["DELETE"])
def my_list_remove(symbol: str):
    sym = clean_symbol(symbol)
    if not sym:
        return jsonify({"error": "That is not a supported symbol."}), 400
    _store().remove_symbol(sym)
    return jsonify(_payload())


@bp.route("/api/my-list/quotes", methods=["GET"])
def my_list_quotes():
    """Price and day change for each saved name, with QUANTA's last recorded call on it."""
    quote: Optional[QuoteFn] = _STATE.get("quote")
    last_call: Optional[Callable[[str], Optional[Dict[str, Any]]]] = _STATE.get("last_call")
    out: Dict[str, Any] = {}
    calls: Dict[str, Any] = {}
    for row in _store().symbols()[:MAX_SYMBOLS]:
        sym = row["symbol"]
        try:
            out[sym] = quote(sym) if quote else None
        except Exception:  # noqa: BLE001 - shown as unavailable
            out[sym] = None
        try:
            calls[sym] = last_call(sym) if last_call else None
        except Exception:  # noqa: BLE001 - shown without a call
            calls[sym] = None
    return jsonify({"quotes": out, "calls": calls})


@bp.route("/api/alerts", methods=["POST"])
def alerts_add():
    data = request.get_json(silent=True) or {}
    sym = clean_symbol(data.get("symbol"))
    if not sym:
        return jsonify({"error": "That is not a supported symbol."}), 400
    try:
        level = float(data.get("level"))
    except (TypeError, ValueError):
        return jsonify({"error": "Give the alert a price."}), 400
    try:
        _store().add_alert(sym, str(data.get("direction") or ""), level, label=str(data.get("label") or ""),
                           source=str(data.get("source") or "manual"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_payload())


@bp.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def alerts_delete(alert_id: int):
    _store().delete_alert(alert_id)
    return jsonify(_payload())


@bp.route("/api/alerts/<int:alert_id>/dismiss", methods=["POST"])
def alerts_dismiss(alert_id: int):
    _store().dismiss(alert_id)
    return jsonify(_payload())


@bp.route("/api/alerts/dismiss-all", methods=["POST"])
def alerts_dismiss_all():
    _store().dismiss(None)
    return jsonify(_payload())


@bp.route("/api/alerts/check", methods=["POST"])
def alerts_check_now():
    worker: Optional[AlertWorker] = _STATE.get("worker")
    if worker is None:
        return jsonify({"error": "The alert checker is not running."}), 503
    fired = worker.run_once()
    return jsonify({**_payload(), "fired": [f["message"] for f in fired]})


def init_my_list(app, quote: QuoteFn, notify: NotifyFn, store: Optional[MyListStore] = None,
                 interval: Optional[float] = None,
                 last_call: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None) -> AlertWorker:
    """Register the routes; the caller starts the worker (``main`` does, tests do not).

    ``last_call(symbol)`` returns QUANTA's latest recorded call on a symbol
    (from the track-record ledger), shown beside each saved name.
    """
    _STATE["store"] = store or MyListStore()
    _STATE["quote"] = quote
    _STATE["last_call"] = last_call
    every = interval if interval is not None else float(os.environ.get("QUANTA_ALERT_INTERVAL", "300") or 300)
    worker = AlertWorker(_STATE["store"], quote, notify, interval=every)
    _STATE["worker"] = worker
    if "my_list" not in app.blueprints:
        app.register_blueprint(bp)
    return worker
