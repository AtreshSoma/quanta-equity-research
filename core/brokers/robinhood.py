"""Robinhood, read-only, through the official Robinhood Trading MCP server.

Robinhood does not publish its MCP tool schemas; they are only available from
an authenticated ``tools/list``. So nothing here assumes a schema:

* At connect, ``tools/list`` is read and each tool QUANTA wants is checked
  against the live ``inputSchema``. Arguments are filled only for properties
  the schema declares (account, symbol/symbols), and a tool whose required
  inputs QUANTA cannot supply is marked unusable instead of guessed at.
* Payloads are read with short candidate-key lists. Every value records which
  key it came from (``field_map``), missing values stay ``None`` with an issue,
  and ``Portfolio.reconciliation`` checks the result against Robinhood's own
  total on every refresh. The diagnostics view shows the live key names (never
  values) so the mapping can be confirmed against a real account.

Only the tools in ``READ_TOOLS`` can be called; the MCP client enforces that and
separately refuses anything that looks like a write, including
``review_equity_order`` and ``place_equity_order``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from portfolio.models import (AccountSummary, ConnectionState, DataIssue, Portfolio,
                              Position, TaxLot, finite)
from portfolio.provider import AuthExpired, NotConnected, PortfolioProvider, ProviderError
from portfolio.activity_config import ACTIVITY

from .mcp_http import McpHttpClient, is_write_tool, tool_payload
from .oauth import OAuthClient
from .redact import get_logger, redact, register_sensitive
from .security import ROBINHOOD_DOMAIN, ROBINHOOD_HOSTS

log = get_logger("QUANTA-BROKER")

MCP_URL = "https://agent.robinhood.com/mcp/trading"
ALLOWED_HOSTS = ROBINHOOD_HOSTS

# Candidate tool names, pending authenticated validation against this account.
# Each is checked against the server's own list at every connect.
READ_TOOLS: Dict[str, str] = {
    "accounts": "get_accounts",
    "portfolio": "get_portfolio",
    "positions": "get_equity_positions",
    "quotes": "get_equity_quotes",
    "tax_lots": "get_equity_tax_lots",
    "fundamentals": "get_equity_fundamentals",
    "earnings": "get_earnings_calendar",
    "realized_pnl": "get_realized_pnl",
    # Record-keeping reads for the Portfolio Review. Neither payload is among the
    # schemas recorded on 2026-09-26, so both are read tolerantly and fail closed:
    # history_ledger() reports what it could not read, and the page shows
    # "Insufficient history" rather than a number built on a guess.
    "orders": "get_equity_orders",
    "crypto_orders": "get_crypto_orders",
    "trade_history": "get_pnl_trade_history",
}
REQUIRED_ROLES = ("accounts", "portfolio", "positions", "quotes")
assert not any(is_write_tool(t) for t in READ_TOOLS.values())

MAX_POSITIONS = 500

_ACCOUNT_ARG = ("account_number", "rhs_account_number", "account_id", "account")
_SYMBOLS_ARG = ("symbols", "tickers")
_SYMBOL_ARG = ("symbol", "ticker")


# --- payload reading --------------------------------------------------------

def _get_path(rec: Any, path: str) -> Any:
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _money(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        return finite(value.get("amount", value.get("value")))
    return finite(value)


def pick(rec: Dict[str, Any], paths: Sequence[str], field_map: Optional[Dict[str, str]] = None,
         name: str = "", money: bool = False) -> Any:
    for p in paths:
        v = _get_path(rec, p)
        if v is None or v == "":
            continue
        if money:
            v = _money(v)
            if v is None:
                continue
        if field_map is not None and name and name not in field_map:
            field_map[name] = p
        return v
    return None


def records(payload: Any, keys: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """The list of records inside a tool payload, wherever it is nested."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for k in tuple(keys) + ("results", "data", "items"):
        v = payload.get(k)
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
        if isinstance(v, dict):
            inner = records(v, keys)
            if inner:
                return inner
    return [payload]


def shape(payload: Any, depth: int = 0) -> Any:
    """Key names and value types only, for diagnostics. Never values."""
    if depth > 4:
        return "…"
    if isinstance(payload, dict):
        return {k: shape(v, depth + 1) for k, v in list(payload.items())[:60]}
    if isinstance(payload, list):
        return [shape(payload[0], depth + 1), f"({len(payload)} items)"] if payload else []
    return type(payload).__name__


def _schema_props(tool: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    schema = tool.get("inputSchema") or {}
    return dict(schema.get("properties") or {}), list(schema.get("required") or [])


class ToolSpec:
    """A live tool definition and how QUANTA can call it."""

    def __init__(self, tool: Dict[str, Any]):
        self.name = tool.get("name", "")
        self.description = tool.get("description") or ""
        self.input_schema = tool.get("inputSchema") or {}
        self.output_schema = tool.get("outputSchema")
        self.props, self.required = _schema_props(tool)
        self.account_arg = next((a for a in _ACCOUNT_ARG if a in self.props), None)
        self.symbols_arg = next((a for a in _SYMBOLS_ARG if a in self.props), None)
        self.symbol_arg = next((a for a in _SYMBOL_ARG if a in self.props), None)
        fillable = {self.account_arg, self.symbols_arg, self.symbol_arg} - {None}
        self.unfillable = [r for r in self.required if r not in fillable]
        self.usable = not self.unfillable

    def symbols_is_array(self) -> bool:
        spec = self.props.get(self.symbols_arg or "", {})
        return spec.get("type") == "array" or "items" in spec

    def args(self, account: Optional[str] = None, symbols: Optional[List[str]] = None,
             symbol: Optional[str] = None, cursor: Optional[str] = None,
             extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        # Optional inputs (a date window, say) are passed only when the live schema
        # declares them; an undeclared one is dropped rather than sent blind.
        for k, v in (extra or {}).items():
            if k in self.props and v is not None:
                out[k] = v
        if self.account_arg and account:
            out[self.account_arg] = account
        if self.symbols_arg and symbols:
            out[self.symbols_arg] = symbols if self.symbols_is_array() else ",".join(symbols)
        elif self.symbol_arg and (symbol or (symbols and len(symbols) == 1)):
            out[self.symbol_arg] = symbol or symbols[0]
        if cursor is not None:
            if "cursor" not in self.props:
                raise ProviderError("Pagination is not supported by this tool schema.", "SCHEMA_MISMATCH")
            out["cursor"] = cursor
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "usable": self.usable, "inputs": sorted(self.props),
                "required": self.required, "unfillable": self.unfillable,
                "description": self.description, "input_schema": self.input_schema,
                "output_schema": self.output_schema}


# --- provider ---------------------------------------------------------------

class RobinhoodProvider(PortfolioProvider):
    name = "robinhood"
    display_name = "Robinhood"

    def __init__(self, mcp_url: str = MCP_URL, session=None):
        self.mcp_url = mcp_url
        self._oauth = OAuthClient(mcp_url, ALLOWED_HOSTS, endpoint_domain=ROBINHOOD_DOMAIN,
                                  session=session)
        self._session = session
        self._client: Optional[McpHttpClient] = None
        self._lock = threading.RLock()
        self._handle_key = secrets.token_bytes(32)        # per process: handles die with it
        self._accounts: Dict[str, str] = {}               # handle -> account number (server only)
        self._rhs_accounts: Dict[str, str] = {}           # account number -> realized-P&L identifier
        self._tools: Dict[str, ToolSpec] = {}
        self._tool_catalog: Dict[str, ToolSpec] = {}
        self._blocked_tools: List[str] = []
        self._state = ConnectionState.DISCONNECTED
        self._last_error: Optional[Dict[str, str]] = None
        self._last_shapes: Dict[str, Any] = {}
        self._field_maps: Dict[str, Dict[str, str]] = {}
        self._connected_at: Optional[str] = None
        self._server_info: Dict[str, Any] = {}

    # -- state --------------------------------------------------------------

    def state(self) -> ConnectionState:
        with self._lock:
            if self._state in (ConnectionState.DISCONNECTED,) and self._oauth.has_pending():
                return ConnectionState.AWAITING_AUTH
            return self._state

    def _set_error(self, exc: ProviderError) -> None:
        with self._lock:
            if isinstance(exc, (AuthExpired,)) or exc.code == "AUTH_EXPIRED":
                self._state = ConnectionState.AUTH_EXPIRED
                self._oauth.forget()
                self._client = None
            elif self._oauth.connected:
                self._state = ConnectionState.PROVIDER_ERROR
            self._last_error = {"code": exc.code, "message": redact(str(exc))}

    def status(self) -> Dict[str, Any]:
        st = self.state()
        with self._lock:
            tools = {role: (self._tools[name].usable if name in self._tools else False)
                     for role, name in READ_TOOLS.items()} if self._tools else {}
            return {
                "provider": self.name,
                "provider_name": self.display_name,
                "state": st.value,
                "state_label": st.label,
                "connected_at": self._connected_at,
                "server": self._server_info,
                "read_only": True,
                "execution_enabled": False,
                "tools": tools,
                "last_error": self._last_error,
                "token": {k: v for k, v in self._oauth.token_info().items() if k != "scope"},
            }

    # -- connection ---------------------------------------------------------

    def begin_connect(self, redirect_uri: str) -> str:
        try:
            url = self._oauth.authorization_url(redirect_uri)
        except ProviderError as exc:
            self._set_error(exc)
            raise
        with self._lock:
            self._last_error = None
        return url

    def complete_connect(self, params: Dict[str, str]) -> None:
        already = self._client is not None
        if params.get("error"):
            reason = str(params.get("error"))[:64]
            if reason == "access_denied":
                err = ProviderError("Robinhood sign-in was cancelled or denied.", "AUTH_DENIED")
            else:
                detail = redact(str(params.get("error_description") or ""))[:200]
                err = ProviderError(f"Robinhood could not complete the sign-in ({reason}"
                                    f"{': ' + detail if detail else ''}). Connect again.", "AUTH_DENIED")
                # A stale or rejected client registration must not poison the retry.
                self._oauth.forget_registration()
            with self._lock:
                self._last_error = {"code": err.code, "message": str(err)}
                if not already:
                    self._state = ConnectionState.DISCONNECTED
            raise err
        try:
            self._oauth.exchange(params.get("code", ""), params.get("state", ""), params.get("iss") or None)
        except ProviderError as exc:
            # A stale or forged callback (unknown state) must not disturb a live
            # connection; it is refused and reported, nothing else.
            if exc.code == "INVALID_STATE" and already:
                with self._lock:
                    self._last_error = {"code": exc.code, "message": str(exc)}
                raise
            if exc.code != "INVALID_STATE":
                self._oauth.forget_registration()
            self._set_error(exc)
            raise
        try:
            self._open_client()
        except ProviderError as exc:
            self._set_error(exc)
            raise

    def _open_client(self) -> None:
        client = McpHttpClient(
            self.mcp_url, self._oauth.access_token,
            allowed_tools=frozenset(READ_TOOLS.values()),
            on_unauthorized=self._oauth.refresh,
            session=self._session,
        )
        info = client.initialize()
        live = client.list_tools()
        specs = {t.get("name"): ToolSpec(t) for t in live if t.get("name")}
        wanted = {name: specs[name] for name in READ_TOOLS.values() if name in specs}
        missing = [r for r in REQUIRED_ROLES
                   if READ_TOOLS[r] not in wanted or not wanted[READ_TOOLS[r]].usable]
        with self._lock:
            self._client = client
            self._tools = wanted
            self._tool_catalog = specs
            self._blocked_tools = sorted(n for n in specs if n not in READ_TOOLS.values())
            self._server_info = info
            self._connected_at = datetime.now(timezone.utc).isoformat()
            self._state = ConnectionState.PARTIAL if missing else ConnectionState.CONNECTED
            self._last_error = ({"code": "TOOLS_MISSING",
                                 "message": "Robinhood did not offer usable tools for: " + ", ".join(missing)}
                                if missing else None)
        log.info("Robinhood MCP connected: %d tools listed, %d read tools usable",
                 len(specs), sum(1 for s in wanted.values() if s.usable))

    def disconnect(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            self._accounts.clear()
            self._rhs_accounts.clear()
            self._tools.clear()
            self._tool_catalog.clear()
            self._last_shapes.clear()
            self._field_maps.clear()
            self._state = ConnectionState.DISCONNECTED
            self._last_error = None
            self._connected_at = None
        try:
            if client:
                client.close()
        except ProviderError:
            log.info("Remote MCP session termination failed; local credentials are cleared")
        finally:
            self._oauth.forget()

    # -- tool calls ---------------------------------------------------------

    def _require_client(self) -> McpHttpClient:
        with self._lock:
            client, state = self._client, self._state
        if client is not None:
            return client
        if state == ConnectionState.AUTH_EXPIRED:
            raise AuthExpired()
        if self._oauth.connected:
            # Signed in, but opening the MCP session failed (network, server
            # hiccup). Retry it rather than force a fresh sign-in.
            try:
                self._open_client()
            except ProviderError as exc:
                self._set_error(exc)
                raise
            with self._lock:
                if self._client is not None:
                    return self._client
        raise NotConnected()

    def _call(self, role: str, **kw) -> Any:
        name = READ_TOOLS[role]
        client = self._require_client()
        spec = self._tools.get(name)
        if spec is None or not spec.usable:
            raise ProviderError(f"Robinhood does not offer a usable '{name}' tool.", "TOOL_UNAVAILABLE")
        try:
            payload = tool_payload(client.call_tool(name, spec.args(**kw)))
        except ProviderError as exc:
            if isinstance(exc, AuthExpired):
                self._set_error(exc)
            raise
        with self._lock:
            self._last_shapes[role] = shape(payload)
        return payload

    def _paged_records(self, role: str, key: str, **kw) -> List[Dict[str, Any]]:
        """Official equity positions/lots contract: data.next -> cursor, never a URL fetch."""
        rows: List[Dict[str, Any]] = []
        seen = set()
        cursor = None
        for _ in range(100):
            payload = self._call(role, **kw, **({"cursor": cursor} if cursor else {}))
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            page = data.get(key, data.get("results"))
            if page is None and key in data:
                page = []                 # official schemas allow a null array
            if not isinstance(page, list) or any(not isinstance(r, dict) for r in page if r is not None):
                raise ProviderError("Robinhood returned an unreadable page.", "SCHEMA_MISMATCH")
            rows.extend(r for r in page if r is not None)
            cursor = data.get("next")
            if not cursor:
                return rows
            if not isinstance(cursor, str) or cursor in seen:
                raise ProviderError("Robinhood pagination did not advance.", "PAGINATION_ERROR")
            seen.add(cursor)
        raise ProviderError("Robinhood pagination exceeded the page limit.", "PAGINATION_ERROR")

    def _account_number(self, handle: str) -> str:
        with self._lock:
            number = self._accounts.get(handle)
        if not number:
            raise ProviderError("Unknown account. Reload the account list.", "UNKNOWN_ACCOUNT")
        return number

    def _handle(self, number: str) -> str:
        return hmac.new(self._handle_key, number.encode(), hashlib.sha256).hexdigest()[:16]

    # -- reads --------------------------------------------------------------

    def list_accounts(self) -> List[AccountSummary]:
        payload = self._call("accounts")
        fm: Dict[str, str] = {}
        out: List[AccountSummary] = []
        seen_labels: Dict[str, int] = {}
        numbers = []
        account_records = records(payload, ("accounts",))
        register_sensitive(str(r.get(k) or "") for r in account_records
                           for k in ("account_number", "rhs_account_number", "rhc_account_number"))
        account_records.sort(key=lambda r: not bool(r.get("is_default")))
        for rec in account_records:
            if rec.get("deactivated") or rec.get("permanently_deactivated"):
                continue
            number = pick(rec, ("account_number",), fm, "number")
            if not number:
                continue
            number = str(number)
            numbers.append(number)
            nickname = pick(rec, ("nickname",), fm, "nickname")
            atype = pick(rec, ("brokerage_account_type",), fm, "type") or ""
            label = str(nickname or str(atype).replace("_", " ").title() or "Account")
            # Labels must not carry digits from the account number.
            label = redact(label)
            n = seen_labels.get(label, 0) + 1
            seen_labels[label] = n
            if n > 1:
                label = f"{label} ({n})"
            agentic = pick(rec, ("agentic_allowed",), fm, "agentic")
            handle = self._handle(number)
            with self._lock:
                self._accounts[handle] = number
                if rec.get("rhs_account_number"):
                    self._rhs_accounts[number] = str(rec["rhs_account_number"])
            out.append(AccountSummary(handle=handle, label=label, account_type=str(atype),
                                      can_trade_via_agent=bool(agentic) if agentic is not None else None))
        register_sensitive(numbers)
        with self._lock:
            self._field_maps["accounts"] = fm
        if not out:
            raise ProviderError("Robinhood returned no accounts QUANTA could read.", "NO_ACCOUNTS")
        return out

    def load_portfolio(self, account_handle: str) -> Portfolio:
        number = self._account_number(account_handle)
        issues: List[DataIssue] = []
        retrieved = datetime.now(timezone.utc).isoformat()
        account = next((a for a in self.list_accounts() if a.handle == account_handle), None)
        if account is None:
            raise ProviderError("That account is no longer available.", "UNKNOWN_ACCOUNT")

        # 1. Balances
        fm_p: Dict[str, str] = {}
        prec = records(self._call("portfolio", account=number), ("portfolio", "portfolios"))
        prec = prec[0] if prec else {}
        account.portfolio_value = pick(prec, ("total_value",), fm_p, "portfolio_value", money=True)
        account.cash = pick(prec, ("cash",),
                            fm_p, "cash", money=True)
        account.buying_power = pick(prec, ("buying_power.buying_power",),
                                    fm_p, "buying_power", money=True)
        for key, label in (("portfolio_value", "portfolio value"), ("cash", "cash"), ("buying_power", "buying power")):
            if getattr(account, key) is None:
                issues.append(DataIssue(f"{key.upper()}_MISSING",
                                        f"Robinhood did not report {label} in a form QUANTA recognises.",
                                        "account"))

        # 2. Positions
        fm_pos: Dict[str, str] = {}
        positions: List[Position] = []
        seen_symbols = set()
        for rec in self._paged_records("positions", "positions", account=number):
            symbol = pick(rec, ("symbol",), fm_pos, "symbol")
            qty = finite(pick(rec, ("quantity",), fm_pos, "quantity"))
            if not symbol:
                issues.append(DataIssue("POSITION_UNREADABLE", "A position had no symbol and was skipped.", "account"))
                continue
            symbol = str(symbol).upper().strip()
            if qty is None:
                issues.append(DataIssue("QUANTITY_MISSING", f"{symbol}: quantity unreadable; position skipped.",
                                        symbol, "error"))
                continue
            if qty == 0:
                continue
            if qty < 0 or rec.get("type") == "boxed":
                raise ProviderError("Short or boxed positions are not supported by portfolio guidance.",
                                    "UNSUPPORTED_POSITION")
            if symbol in seen_symbols:
                raise ProviderError("Robinhood returned duplicate position symbols; refusing to double-count.",
                                    "DUPLICATE_POSITION")
            seen_symbols.add(symbol)
            avg = pick(rec, ("average_buy_price",), fm_pos, "average_cost", money=True)
            if avg is not None and avg <= 0:
                avg = None
            pos = Position(
                symbol=symbol, quantity=qty,
                instrument_type="equity",
                average_cost=avg,
            )
            if avg is None:
                pos.missing.append("cost_basis")
                issues.append(DataIssue("COST_BASIS_MISSING",
                                        f"{symbol}: Robinhood did not report a cost basis; gain/loss is not shown.",
                                        symbol))
            positions.append(pos)
        if len(positions) > MAX_POSITIONS:
            raise ProviderError(f"This account exceeds the {MAX_POSITIONS}-position limit; no partial book is used.",
                                "TOO_MANY_POSITIONS")
        symbols = [p.symbol for p in positions]

        # 3. Quotes (broker prices value the book)
        fm_q: Dict[str, str] = {}
        if symbols:
            try:
                quotes = self._batched("quotes", symbols, number)
                for p in positions:
                    q = (quotes.get(p.symbol) or [None])[0]
                    if not q:
                        p.missing.append("quote")
                        issues.append(DataIssue("QUOTE_MISSING", f"{p.symbol}: no Robinhood quote.", p.symbol, "error"))
                        continue
                    if _get_path(q, "quote.has_traded") is False:
                        p.missing.append("quote")
                        issues.append(DataIssue("QUOTE_UNAVAILABLE", f"{p.symbol}: the instrument has never traded.",
                                                p.symbol, "error"))
                        continue
                    listing_state = _get_path(q, "quote.state")
                    if listing_state and listing_state != "active":
                        issues.append(DataIssue("INSTRUMENT_INACTIVE", f"{p.symbol}: instrument is not active.",
                                                p.symbol, "error"))
                    p.last_price = pick(q, ("quote.last_trade_price",),
                                        fm_q, "last_price", money=True)
                    p.previous_close = pick(q, ("quote.adjusted_previous_close",),
                                            fm_q, "previous_close", money=True)
                    ts = pick(q, ("quote.venue_last_trade_time",), fm_q, "as_of")
                    # Robinhood's portfolio includes the latest extended-hours
                    # trade. An older extended trade must not replace today's
                    # regular-session trade.
                    extended_price = _money(_get_path(q, "quote.last_non_reg_trade_price"))
                    extended_time = _get_path(q, "quote.venue_last_non_reg_trade_time")
                    try:
                        regular_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        extended_dt = datetime.fromisoformat(str(extended_time).replace("Z", "+00:00"))
                        newer_extended = extended_dt > regular_dt
                    except (ValueError, TypeError):
                        newer_extended = False
                    if extended_price is not None and extended_price > 0 and newer_extended:
                        p.last_price, ts = extended_price, extended_time
                        fm_q["last_price"] = "quote.last_non_reg_trade_price"
                        fm_q["as_of"] = "quote.venue_last_non_reg_trade_time"
                    p.quote_as_of = str(ts) if ts else None
                    p.quote_source = "broker"
                    if p.last_price is None:
                        p.missing.append("quote")
                        issues.append(DataIssue("QUOTE_MISSING", f"{p.symbol}: quote had no price.", p.symbol, "error"))
            except ProviderError as exc:
                if isinstance(exc, AuthExpired):
                    raise
                issues.append(DataIssue("QUOTES_FAILED", redact(f"Robinhood quotes unavailable: {exc}"),
                                        "portfolio", "error"))
                for p in positions:
                    p.missing.append("quote")

        # 4. Optional reads: fundamentals, earnings, tax lots, realized P&L.
        optional: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for role in ("fundamentals", "earnings"):
            if not symbols:
                break
            try:
                optional[role] = self._batched(role, symbols, number, extra=self._earnings_window()
                                               if role == "earnings" else None)
            except ProviderError as exc:
                if isinstance(exc, AuthExpired):
                    raise
                issues.append(DataIssue(f"{role.upper()}_UNAVAILABLE",
                                        redact(f"Robinhood {role} unavailable: {exc}"), "portfolio", "info"))
        fm_f: Dict[str, str] = {}
        today = datetime.now(timezone.utc).date().isoformat()
        for p in positions:
            f = ((optional.get("fundamentals") or {}).get(p.symbol) or [{}])[0]
            if f:
                p.name = p.name or str(pick(f, ("simple_name",), fm_f, "name") or "")
                p.sector = str(pick(f, ("sector",), fm_f, "sector") or "")
                p.industry = str(pick(f, ("industry",), fm_f, "industry") or "")
                ex = pick(f, ("ex_dividend_date",), fm_f, "ex_dividend_date")
                p.ex_dividend_date = str(ex)[:10] if ex else None
                pay = pick(f, ("payable_date",), fm_f, "payable_date")
                p.payable_date = str(pay)[:10] if pay else None
                p.dividend_per_share = finite(pick(f, ("dividend_per_share",), fm_f, "dividend_per_share"))
                freq = pick(f, ("distribution_frequency",), fm_f, "distribution_frequency")
                p.distribution_frequency = str(freq) if freq else None
            # The next report on or after today; a calendar also lists past ones.
            # Only unreported quarters (eps.actual null) count as upcoming, and the
            # company's own confirmation (report.verified) travels with the date.
            reports = []
            for e in (optional.get("earnings") or {}).get(p.symbol, []):
                d = pick(e, ("report.date",), fm_f, "earnings_date")
                if not d or str(d)[:10] < today:
                    continue
                if pick(e, ("eps.actual",)) not in (None, ""):
                    continue
                reports.append((str(d)[:10], e))
            reports.sort(key=lambda t: t[0])
            if reports:
                d, e = reports[0]
                p.earnings_date = d
                v = _get_path(e, "report.verified")
                p.earnings_verified = v if isinstance(v, bool) else None
                t = _get_path(e, "report.timing")
                p.earnings_timing = str(t).lower() if t in ("am", "pm") else None
                p.eps_estimate = finite(_get_path(e, "eps.estimate"))
                fm_f.setdefault("earnings_verified", "report.verified")
            else:
                p.earnings_date = None

        self._load_tax_lots(positions, number, issues)
        self._load_realized(account, number, issues)

        with self._lock:
            self._field_maps.update({"portfolio": fm_p, "positions": fm_pos, "quotes": fm_q,
                                     "fundamentals_earnings": fm_f})
            if self._state in (ConnectionState.CONNECTED, ConnectionState.PARTIAL, ConnectionState.PROVIDER_ERROR):
                errors = [i for i in issues if i.severity == "error"]
                self._state = ConnectionState.PARTIAL if errors else ConnectionState.CONNECTED
                self._last_error = None
        return Portfolio(provider=self.name, account=account, positions=positions,
                         retrieved_at=retrieved, issues=issues)

    @staticmethod
    def _earnings_window() -> Dict[str, Any]:
        """The calendar tool has no symbol input: it lists a market-wide window from a start
        date (max 31 days). Asking for the whole window is what makes a report three weeks
        out visible; the default window was silently shorter."""
        return {"start_date": datetime.now(timezone.utc).date().isoformat(), "days": 31}

    def _batched(self, role: str, symbols: List[str], number: str,
                 extra: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
        """{symbol: [records]} for a per-symbol tool, batching when the schema allows."""
        spec = self._tools.get(READ_TOOLS[role])
        if spec is None or not spec.usable:
            raise ProviderError(f"No usable {READ_TOOLS[role]} tool.", "TOOL_UNAVAILABLE")
        out: Dict[str, List[Dict[str, Any]]] = {}
        if spec.symbols_arg:
            # Live fundamentals returned "too many symbols (max 10, got 18)".
            declared = spec.props[spec.symbols_arg].get("maxItems")
            limit = min(10 if role == "fundamentals" else 20,
                        declared if isinstance(declared, int) and declared > 0 else 50)
            symbols = list(dict.fromkeys(symbols))
            chunks = [symbols[i:i + limit] for i in range(0, len(symbols), limit)]
            calls: Iterable[Dict[str, Any]] = ({"symbols": c} for c in chunks)
        elif spec.symbol_arg:
            calls = ({"symbol": s} for s in symbols)
        else:
            calls = iter([{}])
        for kw in calls:
            payload = self._call(role, account=number, extra=extra, **kw)
            recs = records(payload, (role, "quotes", "fundamentals", "earnings"))
            for rec in recs:
                sym = pick(rec, ("quote.symbol",) if role == "quotes" else ("symbol",))
                if not sym and "symbol" in kw:
                    sym = kw["symbol"]
                if sym:
                    out.setdefault(str(sym).upper(), []).append(rec)
        return out

    def _load_tax_lots(self, positions: List[Position], number: str, issues: List[DataIssue]) -> None:
        spec = self._tools.get(READ_TOOLS["tax_lots"])
        if not positions:
            return
        if spec is None or not spec.usable:
            issues.append(DataIssue("TAX_LOTS_UNAVAILABLE", "Robinhood tax lots are not available.", "portfolio", "info"))
            return
        fm: Dict[str, str] = {}
        try:
            if spec.symbol_arg and not spec.symbols_arg:
                by_symbol = {p.symbol: self._paged_records("tax_lots", "tax_lots", account=number, symbol=p.symbol)
                             for p in positions}
            else:
                recs = records(self._call("tax_lots", account=number,
                                          symbols=[p.symbol for p in positions] if spec.symbols_arg else None),
                               ("tax_lots", "lots"))
                by_symbol: Dict[str, List[Dict[str, Any]]] = {}
                for r in recs:
                    sym = pick(r, ("symbol", "instrument.symbol", "ticker"))
                    if sym:
                        by_symbol.setdefault(str(sym).upper(), []).append(r)
        except ProviderError as exc:
            if isinstance(exc, AuthExpired):
                raise
            issues.append(DataIssue("TAX_LOTS_UNAVAILABLE", redact(f"Tax lots unavailable: {exc}"), "portfolio", "info"))
            return
        for p in positions:
            lots = []
            for r in by_symbol.get(p.symbol, []):
                q = finite(pick(r, ("quantity",), fm, "quantity"))
                if q is None or q <= 0:
                    continue
                term = str(pick(r, ("term",), fm, "term") or "").lower()
                lots.append(TaxLot(
                    quantity=q,
                    cost_per_share=pick(r, ("cost_per_share",),
                                        fm, "cost_per_share", money=True),
                    acquired_on=(str(pick(r, ("open_date",), fm, "acquired_on") or "")[:10] or None),
                    term="long" if term in ("lt", "long") else ("short" if term in ("st", "short") else None),
                ))
            p.tax_lots = lots
        with self._lock:
            self._field_maps["tax_lots"] = fm

    def _load_realized(self, account: AccountSummary, number: str, issues: List[DataIssue]) -> None:
        spec = self._tools.get(READ_TOOLS["realized_pnl"])
        if spec is None or not spec.usable:
            return
        fm: Dict[str, str] = {}
        rhs_number = self._rhs_accounts.get(number)
        if not rhs_number:
            issues.append(DataIssue("REALIZED_PNL_UNAVAILABLE", "Robinhood did not supply its realized-P&L account identifier.",
                                    "account", "info"))
            return
        try:
            recs = records(self._call("realized_pnl", account=rhs_number), ("realized_pnl",))
        except ProviderError as exc:
            if isinstance(exc, AuthExpired):
                raise
            issues.append(DataIssue("REALIZED_PNL_UNAVAILABLE", redact(f"Realized P&L unavailable: {exc}"),
                                    "account", "info"))
            return
        rec = recs[0] if recs else {}
        account.realized_pnl = pick(rec, ("total_returns",),
                                    fm, "realized_pnl", money=True)
        account.realized_pnl_period = str(pick(rec, ("window",), fm, "period") or "")
        with self._lock:
            self._field_maps["realized_pnl"] = fm

    # -- realized P&L by period (Activity) -----------------------------------

    REALIZED_SPANS = {"day": "day", "week": "week", "month": "month", "3month": "3month", "year": "year", "all": "all"}

    def load_realized_series(self, account_handle: str, period: str) -> Dict[str, Any]:
        """Robinhood's realized profit and loss for one window, with its buckets.

        ``period`` is a preset span (day, week, month, 3month, year, all) or ``ytd``,
        which is sent as a custom window from January 1 to today. Only inputs the live
        schema declares are sent. A bucket with no readable gain keeps ``None``
        (Robinhood uses that for transfer-only buckets), never 0.
        """
        number = self._account_number(account_handle)
        spec = self._tools.get(READ_TOOLS["realized_pnl"])
        if spec is None or not spec.usable:
            raise ProviderError("Robinhood does not offer realized profit and loss here.", "TOOL_UNAVAILABLE")
        rhs = self._rhs_accounts.get(number)
        if not rhs:
            raise ProviderError("Robinhood did not supply its realized-P&L account identifier.", "REALIZED_ACCOUNT_MISSING")
        today = datetime.now(timezone.utc).date()
        if period == "ytd":
            extra = {"start_date": f"{today.year}-01-01", "end_date": today.isoformat(), "timezone": "America/New_York"}
        elif period in self.REALIZED_SPANS:
            extra = {"span": self.REALIZED_SPANS[period], "timezone": "America/New_York"}
        else:
            raise ProviderError("Unknown realized-P&L period.", "INVALID_PERIOD")
        payload = self._call("realized_pnl", account=rhs, extra=extra)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        buckets = []
        for b in (data.get("data_points") or []):
            if not isinstance(b, dict):
                continue
            n = b.get("number_of_trades")
            buckets.append({"start": str(b.get("start_time") or "") or None, "end": str(b.get("end_time") or "") or None,
                            "gain": _money(b.get("realized_gain")), "rate": finite(b.get("rate_of_realized_gain")),
                            "trades": int(n) if isinstance(n, (int, float)) else None})
        return {"period": period, "window": str(data.get("window") or ""), "total": _money(data.get("total_returns")),
                "rate": finite(data.get("total_rate_of_return")), "currency": str(data.get("display_currency") or "USD"),
                "buckets": buckets, "retrieved_at": datetime.now(timezone.utc).isoformat()}

    # -- record history (orders and closed trades) --------------------------

    # Extra arguments per history tool. The trade history defaults to one week
    # ("span": week); the ledger needs every closing trade.
    HISTORY_ARGS: Dict[str, Dict[str, Any]] = {"trade_history": {"span": "all"}}

    def _list_pages(self, role: str, account: str, cap_pages: Optional[int] = None,
                    report: Optional[Dict[str, Any]] = None, **extra: Any) -> List[Dict[str, Any]]:
        """Every record of a list-returning tool, following ``next`` / ``next_cursor`` cursors.

        ``report`` (when given) receives what the read covered: pages read, whether the
        page limit stopped it with more to come, and the window the tool says it used.
        """
        cap = cap_pages if cap_pages is not None else ACTIVITY["caps"]["pages"]
        rows: List[Dict[str, Any]] = []
        cursor, seen, pages, span = None, set(), 0, None
        for _ in range(cap):
            payload = self._call(role, account=account, **({"extra": extra} if extra else {}),
                                 **({"cursor": cursor} if cursor else {}))
            pages += 1
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            page = data if isinstance(data, list) else next(
                (v for v in (data or {}).values() if isinstance(v, list)), []) if isinstance(data, dict) else []
            rows.extend(r for r in page if isinstance(r, dict))
            if pages == 1 and isinstance(data, dict) and data.get("span"):
                span = str(data["span"])
            cursor = (data.get("next") or data.get("next_cursor")) if isinstance(data, dict) else None
            if not cursor or not isinstance(cursor, str) or cursor in seen:
                cursor = None
                break
            seen.add(cursor)
        if report is not None:
            report.update(pages=pages, truncated=bool(cursor), span=span)
        return rows

    def load_history(self, account_handle: str) -> Dict[str, Any]:
        """Order and closed-trade records, or the reason each is unavailable. Read-only."""
        number = self._account_number(account_handle)
        out: Dict[str, Any] = {"orders": None, "trades": None, "issues": [], "coverage": {}}
        # Messages reach the page: name the record in plain words, not the tool.
        what = {"orders": "order history", "trade_history": "closed-trade history"}
        for role, key in (("orders", "orders"), ("trade_history", "trades")):
            spec = self._tools.get(READ_TOOLS[role])
            if spec is None:
                out["issues"].append({"code": f"{role.upper()}_NOT_OFFERED",
                                      "message": f"Robinhood does not offer its {what[role]} to connected apps."})
                continue
            if not spec.usable:
                log.info("history tool %s needs inputs QUANTA cannot supply: %s",
                         READ_TOOLS[role], ", ".join(spec.unfillable))
                out["issues"].append({"code": f"{role.upper()}_UNUSABLE",
                                      "message": f"Robinhood's {what[role]} asks for details QUANTA cannot supply, so it was not read."})
                continue
            try:
                acct = self._rhs_accounts.get(number) if spec.account_arg == "rhs_account_number" else number
                rep: Dict[str, Any] = {}
                out[key] = self._list_pages(role, acct or number, report=rep, **self.HISTORY_ARGS.get(role, {}))
                out["coverage"][key] = rep
            except ProviderError as exc:
                if isinstance(exc, AuthExpired):
                    raise
                out["issues"].append({"code": f"{role.upper()}_FAILED", "message": redact(str(exc))})
        return out

    def load_crypto_history(self, account_handle: str) -> Dict[str, Any]:
        """Activity-only crypto executions; never mixed into the equity ledger."""
        number = self._account_number(account_handle)
        rhs = self._rhs_accounts.get(number)
        if not rhs:
            raise ProviderError("The linked crypto history account identifier is unavailable.", "CRYPTO_ACCOUNT_MISSING")
        report: Dict[str, Any] = {}
        orders = self._list_pages("crypto_orders", rhs, report=report)
        # The live MCP contract omits executions from list responses. Only the
        # order_id query returns actual fill timestamps and per-fill economics.
        details = {"requested": 0, "succeeded": 0, "failed": 0}
        enriched = []
        rate_limited = False
        for order in orders:
            order = dict(order)
            if not order.get("executions") and (finite(order.get("cumulative_quantity")) or 0) > 0:
                if not order.get("id"):
                    order["detail_error"] = "missing_order_id"
                elif rate_limited:
                    order["detail_error"] = "order_detail_rate_limited"
                elif details["requested"] >= ACTIVITY["caps"]["fills"]:
                    order["detail_error"] = "order_detail_limit"
                else:
                    details["requested"] += 1
                    try:
                        payload = self._call("crypto_orders", account=rhs, extra={"order_id": order["id"]})
                        matches = [r for r in records(payload, ("results",)) if r.get("id") == order["id"]]
                        if len(matches) != 1:
                            order["detail_error"] = "order_detail_not_found"
                        else:
                            order.update(matches[0])
                            if not order.get("executions"):
                                order["detail_error"] = "missing_executions"
                    except AuthExpired:
                        raise
                    except ProviderError as exc:
                        order["detail_error"] = "order_detail_request_failed"
                        if exc.code == "RATE_LIMITED":
                            # Stop further requests during this read, retain the
                            # filled orders and report the missing detail honestly.
                            rate_limited = True
                    details["failed" if order.get("detail_error") else "succeeded"] += 1
            enriched.append(order)
        return {"orders": enriched, "coverage": report, "details": details}

    # -- diagnostics --------------------------------------------------------

    def diagnostics(self) -> Dict[str, Any]:
        """What the live server offers and how QUANTA read it. No values, no ids."""
        with self._lock:
            return {
                "status": self.status(),
                "mcp_url": self.mcp_url,
                "read_tools": {role: (self._tools[n].to_dict() if n in self._tools else {"name": n, "offered": False})
                               for role, n in READ_TOOLS.items()},
                "tool_catalog": {name: spec.to_dict() for name, spec in self._tool_catalog.items()},
                "not_used_tools": list(self._blocked_tools),
                "write_tools_offered": [n for n in self._blocked_tools if is_write_tool(n)],
                "field_maps": dict(self._field_maps),
                "response_shapes": dict(self._last_shapes),
            }
