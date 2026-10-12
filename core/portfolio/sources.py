"""Public, attributable event sources: SEC EDGAR filings and the official macro calendar.

Both are official publications. Nothing here is scraped from a news page or
guessed: EDGAR is the SEC's own JSON API, and the macro calendar is a small data
file transcribed from the Federal Reserve and BLS schedule pages, with its
retrieval date carried along so a stale file can be called stale.

Network access is injected (``get_json``), so tests never touch the network and a
failure of either source degrades to "not available", never to invented events.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# Fund share classes (ETF and mutual-fund series) are listed apart, under their
# trust's CIK: XLK is a series of the Select Sector SPDR Trust.
SEC_FUND_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"      # older filings, named in "files"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
USER_AGENT = "QUANTA-Portfolio/1.0 (local research tool; contact via QUANTA_SEC_CONTACT)"
SEC_MIN_INTERVAL = 0.12        # SEC asks for at most 10 requests per second
CIK_TTL = 24 * 3600
FILINGS_TTL = 30 * 60
TIMEOUT = 8

# Form 8-K item titles, as the SEC publishes them. Items not listed are shown by number.
ITEM_TEXT = {
    "1.01": "Entry into a material agreement",
    "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completion of an acquisition or disposition",
    "2.02": "Results of operations and financial condition",
    "2.03": "New direct financial obligation",
    "2.04": "Triggering events that accelerate an obligation",
    "2.05": "Costs of exit or disposal activities",
    "2.06": "Material impairments",
    "3.01": "Notice of delisting or failure to satisfy a listing rule",
    "3.02": "Unregistered sales of equity securities",
    "4.01": "Change in certifying accountant",
    "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Change in control of the company",
    "5.02": "Departure or appointment of directors or officers",
    "5.03": "Amendments to articles or bylaws",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}
# Items that change the reading of a holding; the rest are routine or exhibits only.
MATERIAL_ITEMS = {"1.01", "1.02", "1.03", "1.05", "2.01", "2.02", "2.03", "2.04", "2.05", "2.06", "3.01",
                  "4.01", "4.02", "5.01", "5.02"}
FORMS = {"8-K", "10-Q", "10-K", "6-K", "20-F", "40-F", "S-1", "S-3"}

GetJson = Callable[[str], Any]


SEC_CONTACT_VAR = "QUANTA_SEC_CONTACT"
SEC_AGENT_VAR = "QUANTA_SEC_USER_AGENT"
# "0" reads the process environment only (the test suite sets it, so no test
# ever reads the developer's own registry or .env file).
SEC_FALLBACK_SWITCH = "QUANTA_SEC_ENV_FALLBACK"
# Optional, git-ignored (.gitignore: .env): KEY=value lines next to start.ps1.
DOTENV_PATH: Optional[Path] = Path(__file__).resolve().parents[2] / ".env"

_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:[/?#]\S*)?")


class SecContactError(RuntimeError):
    """SEC EDGAR requires a User-Agent that names a contact; none usable is configured."""
    reason = "contact_missing"


class SecContactMissing(SecContactError):
    """No contact is configured in the process, the user scope or the .env file."""


class SecContactMalformed(SecContactError):
    """A contact is configured but it is not an email address or web address."""
    reason = "contact_malformed"


class SecContact:
    """Where the SEC contact came from and whether it is usable. The value never
    leaves this object: ``repr`` and ``public()`` carry the state only."""

    __slots__ = ("state", "source", "variable", "_value")

    def __init__(self, state: str, source: Optional[str], variable: str, value: str = ""):
        self.state, self.source, self.variable, self._value = state, source, variable, value

    def public(self) -> Dict[str, Any]:
        return {"state": self.state, "source": self.source, "variable": self.variable}

    def __repr__(self) -> str:
        return f"SecContact(state={self.state!r}, source={self.source!r}, variable={self.variable!r})"

    __str__ = __repr__


def _user_scope_value(name: str) -> Optional[str]:
    """The Windows user-scope environment variable as stored now, not as this
    process inherited it (a terminal opened before it was set never sees it)."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return None


def _dotenv_value(name: str) -> Optional[str]:
    path = DOTENV_PATH
    if path is None or not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().removeprefix("export ").strip() == name:
            return value.strip().strip("'\"")
    return None


def _lookup(name: str) -> tuple:
    """(value, source) for one variable: process, then user scope, then .env."""
    value = os.environ.get(name, "").strip()
    if value:
        return value, "process"
    if os.environ.get(SEC_FALLBACK_SWITCH, "1").strip() == "0":
        return "", None
    for source, read in (("user", _user_scope_value), ("dotenv", _dotenv_value)):
        value = (read(name) or "").strip()
        if value:
            return value, source
    return "", None


def resolve_sec_contact() -> SecContact:
    """The SEC contact the server will use, classified configured / missing / malformed.

    ``QUANTA_SEC_USER_AGENT`` (a full agent string) wins over
    ``QUANTA_SEC_CONTACT`` (an email or URL). Either must name a contact SEC
    can reach: an agent with no email or URL in it is malformed, not sent.
    """
    agent, source = _lookup(SEC_AGENT_VAR)
    if agent:
        ok = bool(_EMAIL.search(agent) or _URL.search(agent))
        return SecContact("configured" if ok else "malformed", source, SEC_AGENT_VAR, agent)
    contact, source = _lookup(SEC_CONTACT_VAR)
    if not contact:
        return SecContact("missing", None, SEC_CONTACT_VAR)
    ok = bool(_EMAIL.fullmatch(contact) or _URL.fullmatch(contact))
    return SecContact("configured" if ok else "malformed", source, SEC_CONTACT_VAR, contact)


def apply_sec_contact_to_env() -> Dict[str, Any]:
    """Server start: copy a fallback value into this process (so engine child
    processes inherit it) and return the state, never the value."""
    c = resolve_sec_contact()
    if c.state == "configured" and c.source != "process":
        os.environ[c.variable] = c._value
    return c.public()


def sec_user_agent() -> Optional[str]:
    """The User-Agent SEC requires, or None when no usable contact is configured.

    ``QUANTA_SEC_USER_AGENT`` is used verbatim; otherwise ``QUANTA_SEC_CONTACT``
    (an email or URL) is embedded in QUANTA's own agent string.
    """
    c = resolve_sec_contact()
    if c.state != "configured":
        return None
    return c._value if c.variable == SEC_AGENT_VAR else f"QUANTA-Research/1.0 ({c._value})"


_pace_lock = threading.Lock()
_pace_last = [0.0]


def _pace() -> None:
    with _pace_lock:
        wait = SEC_MIN_INTERVAL - (time.monotonic() - _pace_last[0])
        if wait > 0:
            time.sleep(wait)
        _pace_last[0] = time.monotonic()


def _agent(require_contact: bool) -> str:
    c = resolve_sec_contact()
    if c.state == "configured":
        return c._value if c.variable == SEC_AGENT_VAR else f"QUANTA-Research/1.0 ({c._value})"
    if require_contact:
        if c.state == "malformed":
            raise SecContactMalformed(f"{c.variable} is set but names no email address or web address")
        raise SecContactMissing(f"set {SEC_CONTACT_VAR} (an email address) so SEC EDGAR can be queried")
    return USER_AGENT


def failure_reason(exc: BaseException) -> tuple:
    """(reason, http_status) for a failed EDGAR request, so a missing contact,
    a rate limit, an HTTP error, a timeout and a network failure stay distinct."""
    if isinstance(exc, SecContactError):
        return exc.reason, None
    if isinstance(exc, urllib.error.HTTPError):
        return ("rate_limited" if exc.code == 429 else "http_error"), int(exc.code)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", None
    if isinstance(exc, urllib.error.URLError):
        inner = getattr(exc, "reason", None)
        if isinstance(inner, (socket.timeout, TimeoutError)) or "timed out" in str(inner).lower():
            return "timeout", None
        return "network_error", None
    if isinstance(exc, OSError):
        return "network_error", None
    return "error", None


def http_get_json(url: str, require_contact: bool = False) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": _agent(require_contact), "Accept": "application/json"})
    _pace()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:      # noqa: S310 - fixed https hosts
        return json.loads(resp.read(8_000_000).decode("utf-8"))


def http_get_text(url: str, max_bytes: int, require_contact: bool = True) -> str:
    """A filing document as text, read up to ``max_bytes`` (tags left in place)."""
    if not url.startswith("https://www.sec.gov/"):
        raise ValueError("only sec.gov documents are fetched")
    req = urllib.request.Request(url, headers={"User-Agent": _agent(require_contact)})
    _pace()
    with urllib.request.urlopen(req, timeout=TIMEOUT * 2) as resp:  # noqa: S310 - sec.gov only
        return resp.read(max_bytes).decode("utf-8", errors="replace")


# [P2] An older submission page is immutable once SEC writes it, so its parsed
# filings (all dates, per form set) are kept across scans, least recently used
# dropped first, at most EVENTS.FILING_PAGE_CACHE_PAGES pages.
_PAGE_CACHE: "OrderedDict[tuple, List[Dict[str, Any]]]" = OrderedDict()
_PAGE_CACHE_LOCK = threading.Lock()


class EdgarClient:
    def __init__(self, get_json: GetJson = http_get_json, clock: Callable[[], float] = time.time):
        self._get = get_json
        self._clock = clock
        self._lock = threading.Lock()
        self._ciks: Dict[str, int] = {}
        self._ciks_at = 0.0
        self._fund_ciks: Dict[str, int] = {}
        self._fund_ciks_at = 0.0
        self._cache: Dict[str, Any] = {}

    def _cik_map(self) -> Dict[str, int]:
        with self._lock:
            if self._ciks and self._clock() - self._ciks_at < CIK_TTL:
                return self._ciks
        raw = self._get(SEC_TICKERS_URL)
        m = {str(v.get("ticker", "")).upper(): int(v["cik_str"]) for v in (raw or {}).values()
             if isinstance(v, dict) and v.get("cik_str")}
        with self._lock:
            self._ciks, self._ciks_at = m, self._clock()
        return m

    def _fund_cik_map(self) -> Dict[str, int]:
        """Fund share-class symbol -> its trust's CIK. A failed read is an empty
        map, so the name stays "no registrant" and the event gate fails closed."""
        with self._lock:
            if self._fund_ciks and self._clock() - self._fund_ciks_at < CIK_TTL:
                return self._fund_ciks
        try:
            raw = self._get(SEC_FUND_TICKERS_URL) or {}
            fields = list(raw.get("fields") or [])
            ci, si = fields.index("cik"), fields.index("symbol")
            m = {str(row[si]).upper(): int(row[ci]) for row in raw.get("data") or []
                 if isinstance(row, list) and len(row) > max(ci, si) and row[si] and row[ci]}
        except Exception:  # noqa: BLE001 - optional source: absence keeps the gate closed
            return {}
        with self._lock:
            self._fund_ciks, self._fund_ciks_at = m, self._clock()
        return m

    def cik(self, symbol: str) -> Optional[int]:
        m = self._cik_map()
        sym = symbol.upper()
        hit = m.get(sym) or m.get(sym.replace(".", "-"))
        if hit is not None:
            return hit
        return self._fund_cik_map().get(sym)

    def recent_filings(self, symbol: str, since: date, forms=None) -> Dict[str, Any]:
        """Filings for one ticker on/after ``since``. ``status``: ok | no_cik | error.

        ``forms`` narrows or widens the form set (default: :data:`FORMS`, the set
        My Portfolio shows); the corporate-event gate passes its own.
        """
        forms = frozenset(forms) if forms else frozenset(FORMS)
        key = f"{symbol}:{since.isoformat()}:{','.join(sorted(forms))}"
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < FILINGS_TTL:
                return hit[1]
        try:
            cik = self.cik(symbol)
            if cik is None:
                out = {"status": "no_cik", "filings": []}      # foreign listings have no CIK here
            else:
                data = self._get(SEC_SUBMISSIONS_URL.format(cik=cik))
                filings = _parse_recent(data, cik, since, forms)
                covered_from, short = _coverage(data, since)
                pages_read = 0
                if short:
                    extra, covered_from, short, pages_read = self._older_pages(data, cik, since, forms)
                    filings += extra
                out = {"status": "ok", "cik": cik, "name": str((data or {}).get("name") or ""),
                       "filings": filings}
                if short:
                    # The list read does not reach back to ``since``: callers must not
                    # treat a missing filing as absent (the event gate fails closed).
                    out.update(coverage_short=True, covered_from=covered_from, pages_read=pages_read)
        except Exception as exc:  # noqa: BLE001 - one source failing must not break the page
            reason, http_status = failure_reason(exc)
            out = {"status": "error", "error": type(exc).__name__, "detail": str(exc)[:200], "filings": [],
                   "reason": reason, "http_status": http_status}
            return out
        with self._lock:
            self._cache[key] = (self._clock(), out)
        return out

    def _older_pages(self, data: Dict[str, Any], cik: int, since: date, forms) -> tuple:
        """Read EDGAR's older filing pages until they reach ``since`` (bounded).

        Returns (filings, covered_from, still_short, pages_read). A page that
        fails to load raises, so the caller reports an error rather than a short
        list. Pages come from the shared page cache when already read.
        """
        from engine.thresholds import EVENTS
        pages = ((data or {}).get("filings") or {}).get("files") or []
        out: List[Dict[str, Any]] = []
        covered_from, _ = _coverage(data, since)
        read = 0
        for page in pages[:EVENTS.FILING_PAGES_MAX]:
            name = str(page.get("name") or "")
            if str(page.get("filingTo") or "9999") < since.isoformat():
                return out, covered_from, False, read    # older pages end before the window: nothing missed
            if not name:
                break
            key = (cik, name, ",".join(sorted(forms)))
            with _PAGE_CACHE_LOCK:
                every = _PAGE_CACHE.get(key)
                if every is not None:
                    _PAGE_CACHE.move_to_end(key)
            if every is None:
                older = self._get(SEC_SUBMISSIONS_PAGE_URL.format(name=name))
                every = _parse_columns(older or {}, cik, date.min, forms)
                with _PAGE_CACHE_LOCK:
                    _PAGE_CACHE[key] = every
                    while len(_PAGE_CACHE) > EVENTS.FILING_PAGE_CACHE_PAGES:
                        _PAGE_CACHE.popitem(last=False)
            read += 1
            out += [f for f in every if f["filed"] >= since.isoformat()]
            covered_from = str(page.get("filingFrom") or covered_from or "")
            if covered_from and covered_from <= since.isoformat():
                return out, covered_from, False, read
        return out, covered_from, True, read


def _coverage(data: Dict[str, Any], since: date) -> tuple:
    """(oldest date in the "recent" block, whether older pages exist that ``since`` needs)."""
    filings = (data or {}).get("filings") or {}
    dates = [d for d in ((filings.get("recent") or {}).get("filingDate") or []) if d]
    oldest = min(dates) if dates else None
    short = bool(filings.get("files")) and (oldest is None or oldest > since.isoformat())
    return oldest, short


def _parse_recent(data: Dict[str, Any], cik: int, since: date, wanted=None) -> List[Dict[str, Any]]:
    return _parse_columns(((data or {}).get("filings") or {}).get("recent") or {}, cik, since, wanted)


def _parse_columns(rec: Dict[str, Any], cik: int, since: date, wanted=None) -> List[Dict[str, Any]]:
    """Filings from one columnar EDGAR block (the "recent" block or an older page)."""
    wanted = wanted or FORMS
    forms = rec.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form not in wanted:
            continue
        filed = (rec.get("filingDate") or [None] * len(forms))[i]
        if not filed or date.fromisoformat(filed) < since:
            continue
        acc = (rec.get("accessionNumber") or [""] * len(forms))[i]
        doc = (rec.get("primaryDocument") or [""] * len(forms))[i]
        items_raw = (rec.get("items") or [""] * len(forms))[i] or ""
        items = [x.strip() for x in str(items_raw).split(",") if x.strip()]
        out.append({
            "form": form, "filed": filed,
            "accepted": (rec.get("acceptanceDateTime") or [None] * len(forms))[i],
            "items": items, "accession": acc,
            "url": SEC_ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc) if acc and doc else None,
        })
    return out


# ---------------------------------------------------------------------------
# Official macro calendar (data file)
# ---------------------------------------------------------------------------

def calendar_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "official_calendar.json"


def load_official_calendar(path: Optional[Path] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """The macro schedule file, with an honest staleness verdict."""
    path = path or calendar_path()
    today = today or datetime.now(timezone.utc).date()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "missing", "events": [], "retrieved_on": None}
    events = [e for e in data.get("events", []) if isinstance(e, dict) and e.get("date") and e.get("title")]
    future = [e for e in events if e["date"] >= today.isoformat()]
    status = "ok" if future else "expired"
    return {"status": status, "events": events, "retrieved_on": data.get("retrieved_on"),
            "note": data.get("note", ""), "last_date": max((e["date"] for e in events), default=None)}
