"""The request guard shared by the whole app and each of its blueprints.

QUANTA is an unauthenticated local app, so:
 * the Host header must be loopback -- a DNS-rebinding page served from
   another name cannot read /api/* responses through the browser;
 * every state-changing request must carry X-QUANTA-Request: 1 -- a custom
   header a cross-site page cannot send without a CORS preflight this server
   never approves -- and, when the browser sends an Origin, it must be this
   server. `get_json(force=True)` used to accept a cross-site text/plain POST
   that started scans, cleared caches and opened files.

app.py installs it app-wide. Each blueprint installs it too, so a blueprint
registered on its own (as the tests do) is guarded by the same rule.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import jsonify, request

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CSRF_HEADER = "X-QUANTA-Request"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def hostname(hostport: str) -> str:
    return (urlsplit("//" + (hostport or "")).hostname or "").lower()


def guard():
    """A 403 response for a request that must not be served; None otherwise."""
    if hostname(request.host) not in LOOPBACK_HOSTS:
        return jsonify({"error": "QUANTA is only served on this computer (loopback)."}), 403
    if request.method in UNSAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != "1":
            return jsonify({"error": "Missing request header."}), 403
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != (request.host or "").lower():
            return jsonify({"error": "Cross-origin request refused."}), 403
    return None
