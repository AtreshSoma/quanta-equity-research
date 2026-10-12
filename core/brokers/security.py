"""Network boundary for the official Robinhood OAuth and MCP endpoints.

Two host policies:

* ``ROBINHOOD_HOSTS`` (exact names) is where the MCP resource, and therefore the
  bearer token on every tool call, may live.
* OAuth endpoints are read from Robinhood's own TLS-verified metadata. They may
  sit on ``robinhood.com`` or any of its subdomains (``check_url(..., domain=)``),
  so a routine move of Robinhood's sign-in or token host does not break sign-in
  for every user. Look-alikes such as ``robinhood.com.evil.example`` still fail.

Every URL must be https on port 443 with no userinfo or fragment. Redirects are
followed by hand (``send``), never by requests: each hop is re-checked against
the same policy, a request that carries a secret may only be redirected within
its own origin, and a body is never replayed across a method-changing redirect.
"""
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import requests

from portfolio.provider import ProviderError

ROBINHOOD_DOMAIN = "robinhood.com"
ROBINHOOD_HOSTS = ("robinhood.com", "agent.robinhood.com", "api.robinhood.com")

MAX_REDIRECTS = 3
_REDIRECTS = (301, 302, 303, 307, 308)
_BODY_PRESERVING = (307, 308)


def _host_allowed(host: Optional[str], allowed_hosts, domain: Optional[str]) -> bool:
    if not host:
        return False
    if host in allowed_hosts:
        return True
    return bool(domain) and (host == domain or host.endswith("." + domain))


def check_url(url: str, allowed_hosts=ROBINHOOD_HOSTS, domain: Optional[str] = None) -> str:
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and _host_allowed(parsed.hostname, allowed_hosts, domain)
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ProviderError("Refusing an unapproved Robinhood HTTPS endpoint.",
                            "UNTRUSTED_ENDPOINT")
    return url


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{(p.hostname or '').lower()}:{p.port or 443}"


def send(http, method: str, url: str, check: Callable[[str], str], *,
         same_origin: bool = False, **kwargs):
    """One HTTP request with redirects followed only onto approved endpoints.

    ``check`` validates every URL, the first included. ``same_origin`` is for
    requests carrying a credential (bearer token, authorization code, refresh
    token): they may be redirected only within their own origin. A redirect
    that would turn a POST into a GET (301/302/303) is refused rather than
    guessed at. TLS verification is always on.
    """
    current = check(url)
    for _ in range(MAX_REDIRECTS + 1):
        resp = getattr(http, method)(current, allow_redirects=False, verify=True, **kwargs)
        if resp.status_code not in _REDIRECTS:
            return resp
        location = (resp.headers.get("Location") or "").strip()
        status = resp.status_code
        close = getattr(resp, "close", None)
        if callable(close):
            close()
        target = urljoin(current, location) if location else ""
        try:
            check(target)
        except ProviderError:
            raise ProviderError("Robinhood redirected to an unapproved address; refusing to follow.",
                                "UNTRUSTED_ENDPOINT") from None
        if same_origin and _origin(target) != _origin(current):
            raise ProviderError("Robinhood redirected a signed-in request to another host; refusing "
                                "to forward credentials.", "UNTRUSTED_ENDPOINT")
        if method != "get" and status not in _BODY_PRESERVING:
            raise ProviderError(f"Robinhood answered HTTP {status} with a redirect QUANTA does not follow.",
                                "UNTRUSTED_ENDPOINT")
        current = target
    raise ProviderError("Robinhood redirected too many times.", "UNTRUSTED_ENDPOINT")


def network_error(exc: BaseException, what: str = "Robinhood") -> ProviderError:
    """A user-actionable message for a transport failure. Never includes the URL or a secret."""
    if isinstance(exc, requests.exceptions.SSLError):
        msg = (f"Could not verify {what}'s security certificate. If antivirus software or a work "
               "network inspects HTTPS on this computer, set REQUESTS_CA_BUNDLE to its certificate "
               "file and restart QUANTA.")
    elif isinstance(exc, requests.exceptions.ProxyError):
        msg = f"Could not reach {what} through the configured proxy. Check the proxy settings."
    elif isinstance(exc, requests.exceptions.Timeout):
        msg = f"{what} did not respond in time. Try again."
    else:
        msg = f"Could not reach {what} ({type(exc).__name__}). Check the internet connection."
    return ProviderError(msg, "NETWORK_ERROR")


class _NoNetrc(requests.auth.AuthBase):
    """Occupies ``Session.auth`` so requests never falls back to ~/.netrc. Adds nothing."""

    def __call__(self, r):
        return r


def session_or_new(session=None):
    client = session if session is not None else requests.Session()
    # The system proxy and CA-bundle settings are honoured (HTTPS_PROXY, the
    # Windows Internet Options proxy, REQUESTS_CA_BUNDLE): without them
    # Robinhood is unreachable on many home-office, corporate and antivirus-
    # filtered networks. TLS verification itself is never relaxed — every call
    # passes verify=True, which also overrides an insecure injected session.
    # ~/.netrc credentials are never wanted: a set Session.auth stops requests
    # from reading them, and the no-op leaves our own headers untouched.
    if getattr(client, "auth", None) is None:
        client.auth = _NoNetrc()
    return client
