"""Minimal MCP client over Streamable HTTP, restricted to an allowlist of tools.

Why not the MCP SDK: this client is the single choke point through which QUANTA
can reach a brokerage, so it is kept small enough to audit in one sitting. It
speaks JSON-RPC 2.0 over POST (MCP 2025-06-18 Streamable HTTP): the server may
answer with ``application/json`` or an ``text/event-stream`` carrying the
response. It sends ``initialize``, ``notifications/initialized``,
``tools/list`` and ``tools/call``, and nothing else.

Read-only is enforced HERE, before any bytes leave the process:

* ``call_tool`` refuses any name that is not in the allowlist it was built with;
* independently of the allowlist, it refuses any name that looks like a write
  (place/review/preview/cancel/create/update/delete/...), so a future edit to
  the allowlist cannot enable trading by accident.

Server-initiated requests (sampling, elicitation, roots) are never answered.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional

import requests

from portfolio.provider import AuthExpired, ProviderError

from .redact import get_logger, redact
from .security import check_url, network_error, send, session_or_new

log = get_logger("QUANTA-BROKER")

PROTOCOL_VERSION = "2025-06-18"
# Versions whose Streamable HTTP transport and tools/* methods this client is
# tested against. Later requests carry whatever version the server chose in
# initialize, as the transport spec requires.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_RESPONSE_BYTES = 20 * 1024 * 1024

# Any tool whose name matches this is a write, whatever the allowlist says.
WRITE_TOOL = re.compile(
    r"(?i)(^|_)(place|review|preview|cancel|exercise|create|update|delete|add|remove|"
    r"follow|unfollow|mark|run|transfer|withdraw|deposit|submit|replace|modify|set|stake|lend)(_|$)")


class ToolNotPermitted(ProviderError):
    def __init__(self, name: str):
        super().__init__(f"Tool '{name}' is not permitted by QUANTA's read-only policy.",
                         "TOOL_NOT_PERMITTED")


def is_write_tool(name: str) -> bool:
    return bool(WRITE_TOOL.search(name or ""))


def _iter_sse(resp: requests.Response) -> Iterator[Dict[str, Any]]:
    """Yield each JSON-RPC message carried by an SSE response."""
    data_lines: List[str] = []
    total = 0
    for raw in resp.iter_lines(decode_unicode=True):
        line = raw if raw is not None else ""
        total += len(line) + 1
        if total > MAX_RESPONSE_BYTES:
            raise ProviderError("Robinhood MCP response exceeded the size limit.", "RESPONSE_TOO_LARGE")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    log.warning("Ignoring a malformed SSE event from the MCP server")
            continue
        if line.startswith(":"):
            continue                     # comment / keep-alive
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        try:
            yield json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            pass


class McpHttpClient:
    def __init__(self, url: str, token: Callable[[], str], *,
                 allowed_tools: FrozenSet[str],
                 on_unauthorized: Optional[Callable[[], bool]] = None,
                 session: Optional[requests.Session] = None,
                 timeout: float = 30.0,
                 client_name: str = "QUANTA",
                 client_version: str = "1.0"):
        bad = sorted(t for t in allowed_tools if is_write_tool(t))
        if bad:
            raise ValueError(f"Write tools cannot be allowlisted: {bad}")
        self.url = check_url(url)
        self._token = token
        self._on_unauthorized = on_unauthorized
        self._http = session_or_new(session)
        self._timeout = timeout
        self._allowed = frozenset(allowed_tools)
        self._session_id: Optional[str] = None
        self._protocol_version = PROTOCOL_VERSION
        self._initialized = False
        self._next_id = 1
        self._lock = threading.Lock()
        self._client_info = {"name": client_name, "version": client_version}
        self.server_info: Dict[str, Any] = {}

    # -- transport ----------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token()}",
        }
        if self._initialized:
            h["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post(self, message: Dict[str, Any], *, retry_auth: bool = True,
              retry_session: bool = True) -> Optional[Dict[str, Any]]:
        try:
            # Carries the bearer token: a redirect is followed only within this origin.
            resp = send(self._http, "post", self.url, check_url, same_origin=True,
                        data=json.dumps(message), headers=self._headers(),
                        timeout=self._timeout, stream=True)
        except requests.RequestException as exc:
            raise network_error(exc, "Robinhood MCP") from None
        try:
            if 300 <= resp.status_code < 400:
                raise ProviderError("Robinhood MCP redirected; refusing to forward credentials.",
                                    "UNTRUSTED_ENDPOINT")
            if resp.status_code == 401:
                if retry_auth and self._on_unauthorized and self._on_unauthorized():
                    return self._post(message, retry_auth=False, retry_session=retry_session)
                raise AuthExpired()
            if resp.status_code == 403:
                raise ProviderError("Robinhood refused this request (403). The connection may lack "
                                    "access to this account.", "FORBIDDEN")
            if resp.status_code == 404 and self._session_id and retry_session:
                # Session expired server-side: start a new one once.
                self._session_id, self._initialized = None, False
                self._initialize_locked()
                return self._post(message, retry_auth=retry_auth, retry_session=False)
            if resp.status_code == 429:
                raise ProviderError("Robinhood MCP is rate-limiting requests. Try again shortly.",
                                    "RATE_LIMITED")
            if resp.status_code >= 400:
                detail = _error_detail(resp)
                raise ProviderError(f"Robinhood MCP returned HTTP {resp.status_code}"
                                    f"{': ' + detail if detail else ''}.", "HTTP_ERROR")
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            if "id" not in message:            # notification: 202 Accepted, no body
                return None
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "text/event-stream":
                resp.encoding = "utf-8"
                for msg in _iter_sse(resp):
                    if msg.get("id") == message["id"] and ("result" in msg or "error" in msg):
                        return msg
                    if "method" in msg and "id" in msg:
                        # A server->client request (sampling/elicitation). QUANTA never
                        # grants these; log the method name only.
                        log.info("Ignoring server request %s from MCP server", msg.get("method"))
                raise ProviderError("Robinhood MCP closed the stream without a response.",
                                    "NO_RESPONSE")
            body = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ProviderError("Robinhood MCP response exceeded the size limit.",
                                    "RESPONSE_TOO_LARGE")
            try:
                parsed = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ProviderError("Robinhood MCP returned a response QUANTA could not parse.",
                                    "MALFORMED_RESPONSE") from None
            if isinstance(parsed, list):         # batched response
                parsed = next((m for m in parsed if m.get("id") == message["id"]), {})
            return parsed
        finally:
            resp.close()

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        reply = self._post(msg) or {}
        if "error" in reply:
            err = reply["error"] or {}
            raise ProviderError(redact(f"Robinhood MCP error {err.get('code')}: "
                                       f"{err.get('message', 'unknown error')}"), "RPC_ERROR")
        if "result" not in reply:
            raise ProviderError("Robinhood MCP sent a reply with no result.", "MALFORMED_RESPONSE")
        return reply["result"]

    def _initialize_locked(self) -> None:
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            # No sampling / elicitation / roots: the server gets nothing to call back into.
            "capabilities": {},
            "clientInfo": self._client_info,
        })
        negotiated = result.get("protocolVersion")
        if isinstance(negotiated, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", negotiated):
            if negotiated not in SUPPORTED_PROTOCOL_VERSIONS:
                # tools/list and tools/call are unchanged across published versions;
                # follow the server rather than fail the whole connection.
                log.warning("Robinhood MCP chose protocol %s; QUANTA was built for %s",
                            negotiated, PROTOCOL_VERSION)
            self._protocol_version = negotiated
        self.server_info = {
            "protocol_version": negotiated,
            "name": (result.get("serverInfo") or {}).get("name"),
            "version": (result.get("serverInfo") or {}).get("version"),
        }
        self._initialized = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # -- public -------------------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        with self._lock:
            if not self._initialized:
                self._initialize_locked()
            return dict(self.server_info)

    def list_tools(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self._initialized:
                self._initialize_locked()
            tools: List[Dict[str, Any]] = []
            cursor = None
            for _ in range(20):                          # pagination guard
                result = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
                tools.extend(result.get("tools") or [])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            else:
                raise ProviderError("MCP tool discovery exceeded its page limit.", "PAGINATION_ERROR")
            return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if is_write_tool(name) or name not in self._allowed:
            log.warning("Refused to call non-allowlisted tool %s", name)
            raise ToolNotPermitted(name)
        with self._lock:
            if not self._initialized:
                self._initialize_locked()
            result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in (result.get("content") or [])
                            if c.get("type") == "text")[:300]
            raise ProviderError(redact(f"{name} failed: {text or 'tool error'}"), "TOOL_ERROR")
        return result

    def close(self) -> None:
        """End the MCP session (best effort) and drop it."""
        with self._lock:
            if self._session_id:
                try:
                    self._http.delete(check_url(self.url), headers=self._headers(), timeout=5,
                                      allow_redirects=False, verify=True)
                except requests.RequestException:
                    pass
            self._session_id, self._initialized = None, False
            self._protocol_version = PROTOCOL_VERSION


def _error_detail(resp: requests.Response) -> str:
    """A short, redacted reason from an HTTP error body (JSON-RPC or OAuth style)."""
    try:
        raw = resp.raw.read(4096, decode_content=True)
        body = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - diagnostics only; the status code is reported anyway
        return ""
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    if isinstance(err, dict):
        text = err.get("message") or ""
    else:
        text = ": ".join(str(v) for v in (err, body.get("error_description")) if v)
    return redact(str(text))[:200]


def tool_payload(result: Dict[str, Any]) -> Any:
    """The JSON a tools/call returned: structuredContent, else parsed text content."""
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    texts = [c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text"]
    for t in texts:
        try:
            return json.loads(t)
        except (json.JSONDecodeError, TypeError):
            continue
    raise ProviderError("A Robinhood tool returned text QUANTA could not read as data.",
                        "MALFORMED_RESPONSE")
