"""Material filings: SEC 8-K items, plain-English tags, and each filing's first reaction session.

QUANTA reads an issuer's 8-K current reports from SEC EDGAR through the same
client the corporate-event gate uses (``corporate_events.default_edgar``), so
it shares that client's contact header, pacing, CIK map and cache. Nothing
here opens its own HTTP connection.

Filing text and item codes are untrusted data. Item codes are checked against
a strict pattern before they are used; an unknown but well-formed code is shown
as "not rated" rather than as harmless.

Pure functions here take the EDGAR client as a parameter, so tests can use a
fake client and never touch the network.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import market_calendar as _mc

# ---------------------------------------------------------------------------
# Item dictionary
#
# Titles follow the Form 8-K General Instructions as the SEC publishes them
# (sec.gov, Exchange Act Form 8-K compliance and disclosure interpretations).
# Severity is QUANTA's tier for how much a filing can change the investment
# case, not a forecast. Item 2.03 uses the form's full wording.
# ---------------------------------------------------------------------------

HIGH, MEDIUM, LOW, UNRATED = "HIGH", "MEDIUM", "LOW", "UNRATED"
SEVERITY_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1, UNRATED: 0}

ITEM_DICTIONARY: Dict[str, Dict[str, str]] = {
    "1.01": {"title": "Entry into a Material Definitive Agreement", "severity": MEDIUM,
             "why": "A significant contract, loan or deal was signed. Its terms decide whether it matters."},
    "1.02": {"title": "Termination of a Material Definitive Agreement", "severity": MEDIUM,
             "why": "A significant contract ended early, which can remove revenue or a source of funding."},
    "1.03": {"title": "Bankruptcy or Receivership", "severity": HIGH,
             "why": "The company or a major part of it entered bankruptcy or receivership. Equity holders are at serious risk."},
    "1.04": {"title": "Mine Safety – Reporting of Shutdowns and Patterns of Violations", "severity": LOW,
             "why": "A mine-safety shutdown or pattern of violations. Mainly relevant to mining companies."},
    "1.05": {"title": "Material Cybersecurity Incidents", "severity": HIGH,
             "why": "A material cyber attack or breach. It can bring costs, outages and legal exposure."},
    "2.01": {"title": "Completion of Acquisition or Disposition of Assets", "severity": MEDIUM,
             "why": "A business or major asset was bought or sold. Its size and price decide the effect."},
    "2.02": {"title": "Results of Operations and Financial Condition", "severity": MEDIUM,
             "why": "Reported results or guidance for a period. The usual cause of earnings-day moves."},
    "2.03": {"title": "Creation of a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement of a Registrant",
             "severity": MEDIUM, "why": "New debt or a binding financing obligation was taken on."},
    "2.04": {"title": "Triggering Events That Accelerate or Increase a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement",
             "severity": HIGH, "why": "A default or similar event lets lenders demand repayment early."},
    "2.05": {"title": "Costs Associated with Exit or Disposal Activities", "severity": MEDIUM,
             "why": "Restructuring, closures or layoffs with a cost attached."},
    "2.06": {"title": "Material Impairments", "severity": HIGH,
             "why": "A write-down of asset value, a sign that past investments are worth less than booked."},
    "3.01": {"title": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing",
             "severity": HIGH, "why": "The exchange warned of possible removal from listing, which can force selling and thin trading."},
    "3.02": {"title": "Unregistered Sales of Equity Securities", "severity": MEDIUM,
             "why": "New shares were sold without a public registration. Existing holders are diluted."},
    "3.03": {"title": "Material Modification to Rights of Security Holders", "severity": MEDIUM,
             "why": "The terms or rights attached to a class of shares or securities changed."},
    "4.01": {"title": "Changes in Registrant's Certifying Accountant", "severity": HIGH,
             "why": "The auditor changed. The stated reason decides whether it signals a problem."},
    "4.02": {"title": "Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or Completed Interim Review",
             "severity": HIGH, "why": "Earlier financial statements should no longer be relied on. A restatement is likely."},
    "5.01": {"title": "Changes in Control of Registrant", "severity": HIGH,
             "why": "Control of the company changed hands, often through a takeover or a major new investor."},
    "5.02": {"title": "Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers",
             "severity": MEDIUM, "why": "A director or senior officer left, joined, or had pay terms changed."},
    "5.03": {"title": "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year", "severity": MEDIUM,
             "why": "The governing documents or fiscal year changed, which can shift shareholder rights."},
    "5.04": {"title": "Temporary Suspension of Trading Under Registrant's Employee Benefit Plans", "severity": LOW,
             "why": "A trading blackout in employee plans. Usually routine."},
    "5.05": {"title": "Amendments to the Registrant's Code of Ethics, or Waiver of a Provision of the Code of Ethics", "severity": LOW,
             "why": "The ethics code changed or was waived for an officer. Usually governance housekeeping."},
    "5.06": {"title": "Change in Shell Company Status", "severity": LOW,
             "why": "The company became, or stopped being, a shell company."},
    "5.07": {"title": "Submission of Matters to a Vote of Security Holders", "severity": LOW,
             "why": "Results of a shareholder vote. Routine unless the outcome surprised."},
    "5.08": {"title": "Shareholder Director Nominations", "severity": LOW,
             "why": "Timing of shareholder-nominated directors for the annual meeting."},
    "7.01": {"title": "Regulation FD Disclosure", "severity": LOW,
             "why": "Material furnished to investors, such as a presentation. Often repeats other news."},
    "8.01": {"title": "Other Events", "severity": LOW,
             "why": "Anything the company chose to report. The content varies widely, so read it."},
    "9.01": {"title": "Financial Statements and Exhibits", "severity": LOW,
             "why": "Attached documents only, such as the press release or agreement text. Not an event by itself."},
}

EXHIBIT_CODE = "9.01"
FORMS = ("8-K", "8-K/A")
DEFAULT_DAYS = 90
MIN_DAYS = 1
MAX_DAYS = 365

# A hyphen share class (BRK-B) is a US listing. Dotted or exchange-suffixed
# symbols (RELIANCE.NS, VOD.L, BTC-USD) are not covered by SEC filings.
_US_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9]{0,9}(?:-[A-Z])?")
_ITEM_RE = re.compile(r"[1-9]\.\d{2}")
_ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}")
_NOT_COVERED = ("Not covered: SEC filings exist only for US-registered issuers. "
                "QUANTA could not check {symbol} for 8-K filings.")


def covered(symbol: Optional[str]) -> bool:
    """True for a US-listed symbol that SEC EDGAR can cover (Indian .NS/.BO and other exchange suffixes are not)."""
    return bool(_US_SYMBOL_RE.fullmatch(str(symbol or "").strip().upper()))


def not_covered_message(symbol: str) -> str:
    return _NOT_COVERED.format(symbol=str(symbol or "").upper())


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

def tag_item(code: Any) -> Optional[Dict[str, str]]:
    """One item tagged with its title, severity and why. None when the code is malformed."""
    c = str(code or "").strip()
    if not _ITEM_RE.fullmatch(c):
        return None
    entry = ITEM_DICTIONARY.get(c)
    if entry is None:
        return {"code": c, "title": f"Item {c} (not in QUANTA's item list)", "severity": UNRATED,
                "why": "QUANTA has no plain-English note for this item. Read the filing."}
    return {"code": c, "title": entry["title"], "severity": entry["severity"], "why": entry["why"]}


def tag_items(codes: Iterable[Any]) -> Tuple[List[Dict[str, str]], int]:
    """Tagged items in filing order, de-duplicated, and the count of malformed codes dropped."""
    out: List[Dict[str, str]] = []
    seen = set()
    dropped = 0
    for raw in codes:
        item = tag_item(raw)
        if item is None:
            dropped += 1
            continue
        if item["code"] in seen:
            continue
        seen.add(item["code"])
        out.append(item)
    return out, dropped


def max_severity(items: List[Dict[str, str]]) -> str:
    if not items:
        return UNRATED
    return max((i["severity"] for i in items), key=lambda s: SEVERITY_RANK[s])


def primary_item(items: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """The item a filing is attributed to: highest severity, then lower item number; 9.01 only when nothing else is filed.

    Exhibit-only items (9.01) never carry the attribution when another item exists.
    """
    pool = [i for i in items if i["code"] != EXHIBIT_CODE] or list(items)
    if not pool:
        return None
    return min(pool, key=lambda i: (-SEVERITY_RANK[i["severity"]], i["code"]))


# ---------------------------------------------------------------------------
# Reaction session
# ---------------------------------------------------------------------------

def parse_accepted(value: Any) -> Optional[datetime]:
    """SEC's acceptanceDateTime as an aware UTC datetime, or None when missing or unreadable."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # SEC stamps are UTC
    return dt


def reaction_session(filed: date, accepted: Optional[str]) -> Tuple[date, str, Optional[str]]:
    """(first reaction session, plain note, New York acceptance time in ISO form or None).

    The filing is judged in New York time. Filed after the 16:00 close, or on a
    non-trading day, the first session that can react is the next trading
    session. Otherwise it is the filing day itself.
    """
    dt = parse_accepted(accepted)
    if dt is None:
        day = filed
        ny_iso = None
        note = "No acceptance time in the SEC record, so the filing day is used as the reaction day."
        after_close = False
    else:
        ny = _mc.now_new_york(dt)
        day = ny.date()
        ny_iso = ny.isoformat(timespec="minutes")
        after_close = ny.time() >= _mc.SESSION_CLOSE
        note = ""
    if not _mc.is_trading_day(day):
        react = _mc.add_trading_days(day, 0)
        note = note or "Filed on a non-trading day, so the next trading session is the first that can react."
    elif after_close:
        react = _mc.add_trading_days(day, 1)
        note = "Filed after the 4:00 pm New York close, so the next trading session is the first that can react."
    else:
        react = day
        note = note or "Filed during trading hours, so the filing day is the first reaction session."
    return react, note, ny_iso


def accession_index_url(cik: Any, accession: str) -> Optional[str]:
    """The sec.gov filing index page, built from the CIK and accession number. None when either is malformed."""
    try:
        cik_int = int(cik)
    except (TypeError, ValueError):
        return None
    if not _ACCESSION_RE.fullmatch(str(accession or "")):
        return None
    flat = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{flat}/{accession}-index.htm"


# ---------------------------------------------------------------------------
# Rows to events
# ---------------------------------------------------------------------------

def normalize_row(row: Any, cik: Any) -> Optional[Dict[str, Any]]:
    """One EDGAR filing row as a validated event, or None when the row is malformed."""
    if not isinstance(row, dict):
        return None
    form = row.get("form")
    if form not in FORMS:
        return None
    try:
        filed = date.fromisoformat(str(row.get("filed") or "")[:10])
    except ValueError:
        return None
    raw_items = row.get("items")
    if isinstance(raw_items, str):
        raw_codes: List[Any] = raw_items.split(",")
    elif isinstance(raw_items, list):
        raw_codes = raw_items
    else:
        raw_codes = []
    items, dropped = tag_items(raw_codes)
    prim = primary_item(items)
    prim_code = prim["code"] if prim else None
    accepted_raw = row.get("accepted")
    accepted = accepted_raw if isinstance(accepted_raw, str) and parse_accepted(accepted_raw) else None
    react, note, ny_iso = reaction_session(filed, accepted)
    accession = str(row.get("accession") or "")
    url = accession_index_url(cik, accession)
    return {
        "date": filed.isoformat(),
        "accepted_at": accepted,
        "accepted_at_ny": ny_iso,
        "form": form,
        "items": items,
        "item_codes_dropped": dropped,
        "max_severity": max_severity(items),
        "primary_item": prim_code,
        "co_filed": [i["code"] for i in items if i["code"] != prim_code],
        "accession": accession if _ACCESSION_RE.fullmatch(accession) else None,
        "url": url,
        "reaction_session": react.isoformat(),
        "reaction_note": note,
    }


def load_events(symbol: str, since: date, today: date, edgar: Any = None) -> Dict[str, Any]:
    """8-K and 8-K/A filings for ``symbol`` on or after ``since``, newest first.

    ``status`` is "ok", "not_covered" or "unavailable". "ok" with no events
    means EDGAR answered and there were none; it is never used for a failure.
    """
    sym = str(symbol or "").strip().upper()
    base = {"ticker": sym, "events": [], "malformed_rows": 0, "coverage_short": False,
            "covered_from": None, "reason": None, "fix": ""}
    if not covered(sym):
        return {**base, "status": "not_covered", "message": not_covered_message(sym)}
    from . import corporate_events as _ce
    client = edgar if edgar is not None else _ce.default_edgar()
    try:
        res = client.recent_filings(sym, since, forms=FORMS)
    except Exception as exc:  # noqa: BLE001 - the client should not raise; a failure is "unavailable", not a crash
        return {**base, "status": "unavailable", "reason": "error",
                "message": f"SEC EDGAR could not be read ({type(exc).__name__}).",
                "fix": "Try again later."}
    if not isinstance(res, dict):
        return {**base, "status": "unavailable", "reason": "error",
                "message": "SEC EDGAR returned an unreadable answer.", "fix": "Try again later."}
    status = res.get("status")
    if status == "no_cik":
        return {**base, "status": "unavailable", "reason": "no_registrant",
                "message": f"SEC lists no company for {sym}, so its 8-K history cannot be read.",
                "fix": ""}
    if status != "ok":
        reason = str(res.get("reason") or "error")
        label, _tpl, fix = _ce.UNAVAILABLE_REASONS.get(reason, _ce.UNAVAILABLE_REASONS["error"])
        return {**base, "status": "unavailable", "reason": reason,
                "message": f"SEC EDGAR could not be read: {label}.", "fix": fix}
    rows = res.get("filings")
    if not isinstance(rows, list):
        return {**base, "status": "unavailable", "reason": "malformed",
                "message": "SEC EDGAR returned a filing list QUANTA could not read.", "fix": "Try again later."}
    events: List[Dict[str, Any]] = []
    malformed = 0
    for row in rows:
        ev = normalize_row(row, res.get("cik"))
        if ev is None:
            malformed += 1
            continue
        events.append(ev)
    events.sort(key=lambda e: (e["date"], e["accepted_at"] or ""), reverse=True)
    covered_from = res.get("covered_from") if isinstance(res.get("covered_from"), str) else None
    return {**base, "status": "ok", "events": events, "malformed_rows": malformed,
            "coverage_short": bool(res.get("coverage_short")), "covered_from": covered_from,
            "message": ""}


def within_days(events: List[Dict[str, Any]], days: int, today: date) -> List[Dict[str, Any]]:
    """Events filed in the last ``days`` calendar days up to ``today``, newest first."""
    start = today - timedelta(days=int(days))
    out = []
    for e in events:
        try:
            d = date.fromisoformat(e["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= d <= today:
            out.append(e)
    return out


PLAN_WINDOW_DAYS = 30  # the window Plan Check reads (plan_check.FILINGS_WARN_DAYS uses the same span)


def plan_window(symbol: str, today: date, edgar: Any = None) -> Dict[str, Any]:
    """The filings Plan Check reads: the last PLAN_WINDOW_DAYS days, no price study (fast).

    ``status`` is "ok", "not_covered" or "unavailable"; ``events`` is empty unless ok.
    """
    sym = str(symbol or "").strip().upper()
    if not covered(sym):
        return {"status": "not_covered", "message": not_covered_message(sym), "events": []}
    loaded = load_events(sym, today - timedelta(days=PLAN_WINDOW_DAYS), today, edgar)
    if loaded["status"] != "ok":
        return {"status": loaded["status"], "message": loaded.get("message", ""), "events": []}
    return {"status": "ok", "message": "", "events": within_days(loaded["events"], PLAN_WINDOW_DAYS, today)}
