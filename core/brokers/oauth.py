"""OAuth 2.1 (authorization code + PKCE) for an MCP resource server.

Flow, per the MCP authorization spec and what agent.robinhood.com publishes:

1. ``discover``: GET the protected-resource metadata (the RFC 9728 well-known
   paths, then the ``resource_metadata`` hint in the MCP server's 401), then
   the authorization-server metadata (RFC 8414 and OpenID Connect locations).
   Every endpoint must be https on an approved Robinhood host, so a tampered
   metadata document or redirect cannot send the code or tokens elsewhere.
2. ``register``: dynamic client registration (RFC 7591) as a PUBLIC client
   (``token_endpoint_auth_method: none``); there is no client secret to leak.
3. ``authorization_url``: S256 PKCE challenge plus a random ``state`` that is
   the CSRF defence for the callback. Pending attempts expire in 10 minutes.
4. ``exchange`` / ``refresh``: token endpoint calls with the verifier.

Tokens live only in this object's memory. They are never written to disk, the
browser, or a log; ``TokenSet.__repr__`` hides them. Restarting QUANTA means
signing in again — deliberately, since Robinhood's only published scope
(``internal``) is not read-only.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse

import requests

from portfolio.provider import AuthExpired, ProviderError

from .redact import get_logger, redact
from .security import check_url, network_error, send, session_or_new

log = get_logger("QUANTA-BROKER")

PENDING_TTL_SECONDS = 900
_RESOURCE_METADATA = re.compile(r'resource_metadata="([^"]+)"')


def _server_error(r) -> str:
    """The OAuth ``error`` / ``error_description`` a server sent, short and redacted."""
    try:
        body = r.json() or {}
    except (ValueError, AttributeError):
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [str(body.get(k)) for k in ("error", "error_description") if body.get(k)]
    return redact(": ".join(parts))[:200]


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[float]
    scope: str = ""

    def __repr__(self) -> str:  # never print a token
        return f"TokenSet(scope={self.scope!r}, expires_at={self.expires_at!r}, refresh={'yes' if self.refresh_token else 'no'})"

    __str__ = __repr__

    def expiring(self, skew: float = 60.0) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at - skew


@dataclass
class ServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: Optional[str]
    scopes: Tuple[str, ...] = ()
    issuer: str = ""


@dataclass
class _Pending:
    verifier: str
    redirect_uri: str
    created: float = field(default_factory=time.time)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair() -> Tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))           # 64 chars, within 43..128
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


class OAuthClient:
    def __init__(self, resource: str, allowed_hosts: Tuple[str, ...], *,
                 endpoint_domain: Optional[str] = None,
                 client_name: str = "QUANTA (read-only portfolio research)",
                 session: Optional[requests.Session] = None, timeout: float = 20.0):
        self._allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        # The resource (where the bearer token goes) is pinned to an exact host;
        # endpoints named by the provider's own metadata may use its domain.
        self._endpoint_domain = endpoint_domain.lower() if endpoint_domain else None
        self.resource = check_url(resource, self._allowed_hosts)
        self._client_name = client_name
        self._http = session_or_new(session)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._meta: Optional[ServerMetadata] = None
        self._client_ids: Dict[str, str] = {}             # redirect_uri -> client_id (public)
        self._pending: Dict[str, _Pending] = {}
        self._tokens: Optional[TokenSet] = None
        self._client_id: Optional[str] = None

    # -- helpers ------------------------------------------------------------

    def _check_url(self, url: str, what: str = "") -> str:
        return check_url(url, self._allowed_hosts, self._endpoint_domain)

    def _send(self, method: str, url: str, *, same_origin: bool = False, **kwargs):
        try:
            return send(self._http, method, url, self._check_url, same_origin=same_origin,
                        timeout=self._timeout, **kwargs)
        except requests.RequestException as exc:
            raise network_error(exc) from None

    def _get_json(self, url: str) -> Dict:
        r = self._send("get", url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise ProviderError(f"Robinhood metadata request failed (HTTP {r.status_code}).", "DISCOVERY_FAILED")
        try:
            doc = r.json()
        except ValueError:
            raise ProviderError("Robinhood returned unreadable OAuth metadata.", "DISCOVERY_FAILED") from None
        if not isinstance(doc, dict):
            raise ProviderError("Robinhood returned unreadable OAuth metadata.", "DISCOVERY_FAILED")
        return doc

    def _first_json(self, urls) -> Optional[Dict]:
        """The first metadata document found. A refused endpoint is never skipped silently."""
        last: Optional[ProviderError] = None
        for url in dict.fromkeys(urls):
            try:
                return self._get_json(url)
            except ProviderError as exc:
                if exc.code == "UNTRUSTED_ENDPOINT":
                    raise
                last = exc
        if last is not None and last.code == "NETWORK_ERROR":
            raise last
        return None

    def _resource_metadata_hint(self) -> Optional[str]:
        """``resource_metadata`` from the MCP server's unauthenticated 401 (MCP authorization spec)."""
        try:
            r = self._send("post", self.resource, same_origin=True,
                           json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                           headers={"Accept": "application/json, text/event-stream"})
        except ProviderError:
            return None
        try:
            if r.status_code != 401:
                return None
            m = _RESOURCE_METADATA.search(r.headers.get("WWW-Authenticate") or "")
            return m.group(1) if m else None
        finally:
            close = getattr(r, "close", None)
            if callable(close):
                close()

    # -- discovery & registration ------------------------------------------

    def discover(self) -> ServerMetadata:
        with self._lock:
            if self._meta:
                return self._meta
        p = urlparse(self.resource)
        origin = f"{p.scheme}://{p.netloc}"
        path = p.path.rstrip("/")
        prm = self._first_json([f"{origin}/.well-known/oauth-protected-resource{path}",
                                f"{origin}/.well-known/oauth-protected-resource"])
        if prm is None:
            hint = self._resource_metadata_hint()
            prm = self._get_json(hint) if hint else None
        if prm is None:
            raise ProviderError("Robinhood's sign-in metadata was not found.", "DISCOVERY_FAILED")
        servers = prm.get("authorization_servers") or []
        if not servers or not isinstance(servers[0], str):
            raise ProviderError("Robinhood did not publish an authorization server.", "DISCOVERY_FAILED")
        issuer = self._check_url(servers[0], "authorization server")
        ip = urlparse(issuer)
        base, ipath = f"{ip.scheme}://{ip.netloc}", ip.path.rstrip("/")
        doc = self._first_json([
            f"{base}/.well-known/oauth-authorization-server{ipath}",
            f"{base}/.well-known/openid-configuration{ipath}",
            f"{base}{ipath}/.well-known/openid-configuration",
            f"{base}/.well-known/oauth-authorization-server",
        ])
        if not doc:
            raise ProviderError("Robinhood authorization-server metadata was not found.", "DISCOVERY_FAILED")
        if doc.get("issuer") and doc["issuer"].rstrip("/") != issuer.rstrip("/"):
            raise ProviderError("Robinhood metadata issuer does not match; refusing to continue.",
                                "UNTRUSTED_ENDPOINT")
        if "S256" not in (doc.get("code_challenge_methods_supported") or []):
            raise ProviderError("Robinhood does not advertise PKCE S256.", "DISCOVERY_FAILED")
        meta = ServerMetadata(
            authorization_endpoint=self._check_url(doc.get("authorization_endpoint", ""), "authorization endpoint"),
            token_endpoint=self._check_url(doc.get("token_endpoint", ""), "token endpoint"),
            registration_endpoint=(self._check_url(doc["registration_endpoint"], "registration endpoint")
                                   if doc.get("registration_endpoint") else None),
            scopes=tuple(prm.get("scopes_supported") or doc.get("scopes_supported") or ()),
            issuer=issuer,
        )
        with self._lock:
            self._meta = meta
        return meta

    def register(self, redirect_uri: str) -> str:
        with self._lock:
            if redirect_uri in self._client_ids:
                return self._client_ids[redirect_uri]
        meta = self.discover()
        if not meta.registration_endpoint:
            raise ProviderError("Robinhood does not support dynamic client registration.", "REGISTRATION_FAILED")
        body = {
            "client_name": self._client_name,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        r = self._send("post", meta.registration_endpoint, json=body,
                       headers={"Accept": "application/json"})
        if r.status_code not in (200, 201):
            detail = _server_error(r)
            raise ProviderError(f"Robinhood client registration failed (HTTP {r.status_code}"
                                f"{': ' + detail if detail else ''}).", "REGISTRATION_FAILED")
        try:
            reg = r.json()
        except ValueError:
            reg = None
        client_id = reg.get("client_id") if isinstance(reg, dict) else None
        if not client_id or not isinstance(client_id, str):
            raise ProviderError("Robinhood registration returned no client id.", "REGISTRATION_FAILED")
        with self._lock:
            self._client_ids[redirect_uri] = client_id
        return client_id

    def forget_registration(self) -> None:
        """Drop cached metadata and client ids so the next sign-in rediscovers and
        registers afresh (after invalid_client, a moved endpoint, a failed exchange)."""
        with self._lock:
            self._client_ids.clear()
            self._meta = None

    # -- authorization code flow ------------------------------------------

    def authorization_url(self, redirect_uri: str) -> str:
        meta = self.discover()
        client_id = self.register(redirect_uri)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if now - v.created < PENDING_TTL_SECONDS}
            self._pending[state] = _Pending(verifier, redirect_uri)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self.resource,
        }
        if meta.scopes:
            params["scope"] = " ".join(meta.scopes)
        return f"{meta.authorization_endpoint}?{urlencode(params)}"

    def has_pending(self) -> bool:
        now = time.time()
        with self._lock:
            return any(now - v.created < PENDING_TTL_SECONDS for v in self._pending.values())

    def exchange(self, code: str, state: str, issuer: Optional[str] = None) -> TokenSet:
        with self._lock:
            pending = self._pending.pop(state or "", None)
        if pending is None or time.time() - pending.created > PENDING_TTL_SECONDS:
            raise ProviderError("This sign-in link is invalid or expired. Start the connection again.",
                                "INVALID_STATE")
        if not code:
            raise ProviderError("Robinhood did not return an authorization code.", "AUTH_DENIED")
        meta = self.discover()
        # RFC 9207: when the server names itself on the callback it must be the
        # issuer this attempt started with (mix-up defence).
        if issuer and meta.issuer and issuer.rstrip("/") != meta.issuer.rstrip("/"):
            raise ProviderError("The sign-in response came from an unexpected issuer; refusing it.",
                                "UNTRUSTED_ENDPOINT")
        with self._lock:
            client_id = self._client_ids.get(pending.redirect_uri)
        if not client_id:
            raise ProviderError("This sign-in link is invalid or expired. Start the connection again.",
                                "INVALID_STATE")
        tokens = self._token_request(meta.token_endpoint, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": client_id,
            "code_verifier": pending.verifier,
            "resource": self.resource,
        })
        with self._lock:
            self._tokens, self._client_id = tokens, client_id
        return tokens

    def _token_request(self, url: str, form: Dict[str, Optional[str]]) -> TokenSet:
        # The form carries the code verifier or refresh token: same-origin redirects only.
        r = self._send("post", url, same_origin=True,
                       data={k: v for k, v in form.items() if v is not None},
                       headers={"Accept": "application/json"})
        if r.status_code in (400, 401):
            detail = _server_error(r)
            raise AuthExpired("Robinhood rejected the sign-in" + (f" ({detail})" if detail else "")
                              + ". Connect again.")
        if r.status_code != 200:
            raise ProviderError(f"Robinhood token request failed (HTTP {r.status_code}).", "TOKEN_FAILED")
        try:
            body = r.json()
        except ValueError:
            raise ProviderError("Robinhood returned an unreadable token response.", "TOKEN_FAILED") from None
        access = body.get("access_token")
        if not access or str(body.get("token_type", "bearer")).lower() != "bearer":
            raise ProviderError("Robinhood returned no usable access token.", "TOKEN_FAILED")
        expires_in = body.get("expires_in")
        return TokenSet(
            access_token=access,
            refresh_token=body.get("refresh_token") or form.get("refresh_token"),
            expires_at=(time.time() + float(expires_in)) if expires_in else None,
            scope=str(body.get("scope", "")),
        )

    # -- token access -------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._tokens is not None

    def token_info(self) -> Dict[str, object]:
        with self._lock:
            t = self._tokens
        if t is None:
            return {"present": False}
        return {"present": True, "scope": t.scope, "expires_at": t.expires_at,
                "refreshable": bool(t.refresh_token)}

    def access_token(self) -> str:
        with self._lock:
            t = self._tokens
        if t is None:
            raise AuthExpired("Not signed in to Robinhood.")
        if t.expiring() and t.refresh_token:
            if not self.refresh():
                raise AuthExpired()
            with self._lock:
                t = self._tokens
            if t is None:
                raise AuthExpired()
        return t.access_token

    def refresh(self) -> bool:
        """Try the refresh token once. On failure the tokens are dropped."""
        with self._lock:
            t, client_id = self._tokens, self._client_id
        if t is None or not t.refresh_token or not client_id:
            self.forget()
            return False
        try:
            new = self._token_request(self.discover().token_endpoint, {
                "grant_type": "refresh_token",
                "refresh_token": t.refresh_token,
                "client_id": client_id,
                "resource": self.resource,
            })
        except ProviderError:
            log.info("Robinhood token refresh failed; the user must sign in again")
            self.forget()
            return False
        with self._lock:
            self._tokens = new
        return True

    def forget(self) -> None:
        with self._lock:
            self._tokens = None
            self._client_id = None
            self._pending.clear()
