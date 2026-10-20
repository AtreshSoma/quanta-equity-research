"""Corporate events that end a directional price thesis (audit F1).

A company under a signed cash acquisition trades at a spread to the deal price.
Its historical volatility, its 52-week high and the analyst targets written
before the deal no longer describe where the price can go: the deal terms do.
A delisting ends the thesis outright. The scanner must not present either as a
directional idea, and a holder needs to know the remaining spread.

Evidence comes from SEC EDGAR (the company's own filings), read through
:class:`portfolio.sources.EdgarClient`:

* a merger proxy (DEFM14A / PREM14A) or a target's tender-offer response
  (SC 14D9) names a pending acquisition;
* an 8-K item 1.01 counts only when its text is a merger agreement with THIS
  company as the target (item 1.01 also covers credit agreements, and the
  acquirer files one too);
* the filer's side is read from the filing, never assumed: an acquirer's 8-K
  defines "the Company" as the TARGET, so the text's "Company" must be the
  registrant itself (its EDGAR name, or a short name the filing defines for
  it). A filer that is the Parent/Purchaser, or owns the merger sub, is the
  buyer; a filing that names someone else as the Company with no buyer evidence
  is "unavailable" (fail closed), not clear;
* a deal paid partly or wholly in the acquirer's stock has no single cash price;
* a later 8-K item 1.02 that terminates the merger agreement clears it;
* Form 25-NSE / 15-12B is a delisting only when the class it removes is the
  equity (common or ordinary shares, ADSs). The same forms also remove matured
  notes, warrants, units and preferred series, which say nothing about the
  stock; a class that cannot be read is "unavailable", not clear. After a
  successor-issuer 8-K12B (holding-company reorganization) the 25-NSE removes
  the predecessor's shares while the ticker trades on, so it is not a delisting;
* a 14D-9 answering a mini-tender offer (a sub-5% bid the company tells holders
  to reject) is not an acquisition;
* a foreign private issuer (an ADR) files no 8-K or merger proxy: it announces a
  deal in a free-form Form 6-K, which this check does not classify, so a 6-K in
  the window makes the result "unavailable", never "clear". A Canadian target's
  tender response (SC 14D9F) counts like an SC 14D9.

A range collapse with no filing (20-day ATR far below the 120-day ATR) is only a
caution: it caps a name at Watch and says to check for a deal.

Fail closed: when EDGAR cannot be asked (no SEC contact configured, the request
failed, no registrant for the ticker, or a deciding document could not be read)
the result is ``unavailable`` and the name is never funded.

Every number and date in the text is read from the filing; nothing is invented.
"""
from __future__ import annotations

import html as _html
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from .thresholds import EVENTS

PENDING_ACQUISITION = "PENDING_ACQUISITION"
DELISTING = "DELISTING"
POSSIBLE_PENDING_DEAL = "POSSIBLE_PENDING_DEAL"
EVENT_CHECK_UNAVAILABLE = "EVENT_CHECK_UNAVAILABLE"
EVENT_CHECK_NOT_COVERED = "EVENT_CHECK_NOT_COVERED"

AVOID_CODES = frozenset({PENDING_ACQUISITION, DELISTING})

FetchText = Callable[[str, int], str]


@dataclass
class EventCheck:
    status: str                      # clear | flagged | unavailable
    code: str = ""
    text: str = ""
    source: str = ""
    url: Optional[str] = None
    filed: Optional[str] = None
    agreement_date: Optional[str] = None
    deal_price: Optional[float] = None
    cvr: bool = False
    cvr_non_transferable: bool = False
    spread_pct: Optional[float] = None
    stock: bool = False              # consideration includes the acquirer's shares
    note: str = ""
    registrant: str = ""             # the company's name on its SEC registrant record
    checked_at: str = ""
    reason: str = ""                 # why "unavailable": a key of UNAVAILABLE_REASONS
    fix: str = ""                    # what clears that reason, in the reader's words
    http_status: Optional[int] = None
    cik: Optional[int] = None        # SEC registrant id: one issuer across share classes (I7)

    @property
    def blocks_new_money_outright(self) -> bool:
        return self.status == "flagged" and self.code in AVOID_CODES

    @property
    def fundable(self) -> bool:
        return self.status == "clear"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["avoid"] = self.blocks_new_money_outright
        if self.status != "unavailable":
            # Why-unavailable fields describe a failed check only (I1).
            for key in ("reason", "fix", "http_status"):
                d.pop(key, None)
        if self.cik is None:
            d.pop("cik", None)
        return d


# ── filing text ──────────────────────────────────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}

_TARGET_PATTERNS = [
    # Only a merger vehicle merging into the Company is a takeover; a seller
    # merging INTO the filer (a bank merger, say) is the filer buying.
    re.compile(r"(?:merger\s+sub\w*(?:\s+(?:\d|i{1,3}))?|purchaser|bidco|acquisition\s+(?:sub|corp)\w*)"
               r"[^.]{0,60}?\bmerge\w*\s+with\s+and\s+into\s+the\s+company\b", re.I),
    re.compile(r"\bthe\s+company\s+(?:will\s+)?surviv\w*\s+(?:the\s+merger\s+)?as\s+a\s+wholly[\s-]owned\s+subsidiary", re.I),
    re.compile(r"each\s+(?:outstanding\s+)?share\s+of\s+(?:the\s+)?(?:company|our)\s+common\s+stock", re.I),
    re.compile(r"\byou\s+will\s+(?:be\s+entitled\s+to\s+)?receive\s+\$", re.I),
]
_PRICE_PATTERNS = [
    re.compile(r"right\s+to\s+receive\s+(?:\(i\)\s*)?\$\s?([\d,]+(?:\.\d{1,2})?)\s+(?:per\s+share\s+)?in\s+cash", re.I),
    re.compile(r"entitled\s+to\s+receive\s+\$\s?([\d,]+(?:\.\d{1,2})?)\s+(?:per\s+share\s+)?in\s+cash", re.I),
    re.compile(r"\$\s?([\d,]+\.\d{2})\s+per\s+share\s+in\s+cash", re.I),
]
_AGREEMENT_DATE = re.compile(
    r"\bon\s+(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s+(\d{4}),?.{0,400}?entered\s+into\s+an\s+agreement\s+and\s+plan\s+of\s+merger",
    re.I | re.S)


def _plain(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def is_merger_target(text: str) -> bool:
    t = _plain(text)
    return bool(re.search(r"agreement\s+and\s+plan\s+of\s+merger|merger\s+agreement", t, re.I)) and any(
        p.search(t) for p in _TARGET_PATTERNS)


def is_merger_acquirer(text: str) -> bool:
    """The filer is the BUYER: the other party becomes a subsidiary of the Company."""
    t = _plain(text)
    return bool(re.search(r"wholly[\s-]owned\s+subsidiary\s+of\s+the\s+company", t, re.I)) and not any(
        p.search(t) for p in _TARGET_PATTERNS)


def deal_terms(text: str) -> Dict[str, Any]:
    t = _plain(text)
    price = None
    for p in _PRICE_PATTERNS:
        m = p.search(t)
        if m:
            price = float(m.group(1).replace(",", ""))
            break
    agreement = None
    m = _AGREEMENT_DATE.search(t)
    if m:
        try:
            agreement = date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            agreement = None
    cvr = bool(re.search(r"contingent\s+value\s+right", t, re.I))
    non_transferable = bool(re.search(r"non[\s-]?transferable\s+contingent\s+value\s+right", t, re.I))
    stock = paid_in_stock(t)
    if stock:
        price = None          # no single cash price: the value moves with the acquirer's shares
    return {"deal_price": price, "agreement_date": agreement, "cvr": cvr, "cvr_non_transferable": non_transferable,
            "stock": stock}


_CONSIDERATION = re.compile(r"(?:right\s+to\s+receive|converted\s+into|consisting\s+of)\s*(.{0,250})", re.I | re.S)
_STOCK_LEG = re.compile(r"\b\d*\.\d+\s+(?:of\s+a\s+)?(?:validly\s+issued[^,]{0,40}?\s+)?shares?\s+of\b"
                        r"|exchange\s+ratio\s+(?:of\s+)?\d*\.\d+", re.I)


def paid_in_stock(text: str) -> bool:
    """The per-share consideration includes a fraction of the acquirer's shares."""
    t = _plain(text)
    return any(_STOCK_LEG.search(m.group(1)) for m in _CONSIDERATION.finditer(t)) or bool(
        re.search(r"exchange\s+ratio\s+of\s+\d*\.\d+", t, re.I))


# ── the filer's side of a merger agreement ───────────────────────────────────

_LEGAL_WORDS = frozenset("""inc incorporated corp corporation co company ltd limited plc holdings holding group
the llc lp sa nv ag se de new""".split())
_BUYER_TERMS = frozenset({"parent", "purchaser", "buyer", "acquiror", "acquirer"})
_QUOTED = re.compile(r"[\"\u201c]\s*([^\"\u201d]{1,40}?)\s*[\"\u201d]")
_COMPANY_DEF = re.compile(r"\(([^()]{0,60}?[\"\u201c]\s*Company\s*[\"\u201d][^()]{0,60}?)\)")


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "


def registrant_aliases(name: str, symbol: str = "") -> set:
    """Names the filer goes by: its EDGAR name without legal suffixes, its first word, its ticker."""
    words = [w for w in _norm(re.sub(r"/[^/]*/?", " ", name or "")).split() if w not in _LEGAL_WORDS]
    out = set()
    if words:
        out.add(" ".join(words))
        if len(words[0]) >= 3:
            out.add(words[0])
    sym = re.split(r"[.\-]", symbol or "")[0].lower()
    if len(sym) >= 3:
        out.add(sym)
    return out


def _has(segment: str, aliases) -> bool:
    seg = _norm(segment)
    return any(f" {a} " in seg for a in aliases)


# Between a name and its definition only legal form may stand:
# 'Acme Holdings, Inc., a Delaware corporation ("Parent")'.
_ENTITY_GAP = re.compile(
    r"^[\s,.]*(?:(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|l\.?p|n\.?v|s\.?a|holdings)"
    r"\.?[\s,]*)*(?:(?:a|an)\s+[\w\s.\-]{0,40}?(?:corporation|company|partnership|plc|trust|limited)[\s,]*)?$",
    re.I)


def _defined_terms(t: str, aliases) -> set:
    """Short names the filing DEFINES for the filer: 'Acme Corp., a Delaware corporation ("Parent")'.

    Only a definition counts (the name, its legal form, then the quoted term),
    so a proxy that mentions the buyer next to the filer's name hundreds of
    times does not hand the buyer's name to the filer.
    """
    terms = set()
    low = t.lower()
    for alias in aliases:
        for m in re.finditer(r"\b" + re.escape(alias).replace(r"\ ", r"[^a-z0-9]+") + r"\b", low):
            gap_end = t.find("(", m.end(), m.end() + 90)
            if gap_end < 0 or not _ENTITY_GAP.match(t[m.end():gap_end]):
                continue
            close = t.find(")", gap_end, gap_end + 80)
            if close > 0:
                terms |= {q.strip().lower() for q in _QUOTED.findall(t[gap_end:close + 1])}
    return terms


# "a direct, wholly-owned subsidiary of" (extracted text may read "wholly -owned").
_WHOLLY_OWNED = r"(?:(?:direct|indirect)[,\s]+)?wholly\s*-?\s*owned\s+subsidiary\s+of\s+(?:the\s+)?"


def _alias_pattern(n: str) -> str:
    return re.escape(n).replace(r"\ ", r"[\s\W]+")


def _is_acquired(t: str, names) -> bool:
    """The filing says the filer itself ends up owned by SOMEONE ELSE.

    "<filer> surviving as a wholly owned subsidiary of <other>" or "the
    acquisition of <filer> by <other>". A parent whose name contains the
    filer's own ("New <filer>", a holding-company reorganization) is not
    someone else.
    """
    for n in names:
        pat = _alias_pattern(n)
        for m in re.finditer(r"\b" + pat + r"\b[^.;]{0,40}?surviv\w*\s+(?:the\s+merger\s+)?as\s+(?:a|an)\s+"
                             + _WHOLLY_OWNED + r"([^.;,()]{1,40})", t, re.I):
            if not _has(m.group(1), names):
                return True
        for m in re.finditer(r"\bacquisition\s+of\s+" + pat + r"\b\s+by\s+([^.;,()]{1,40})", t, re.I):
            if not _has(m.group(1), names):
                return True
    return False


def filer_side(text: str, aliases) -> str:
    """buyer | target | unknown, from who the filing defines as Parent and as the Company."""
    t = _plain(text)
    if not aliases:
        return "unknown"
    names = set(aliases) | _defined_terms(t, aliases)
    if _is_acquired(t, names - {"company"}):
        return "target"          # outranks buyer evidence from the filer's own reorganizations
    if names & _BUYER_TERMS:
        return "buyer"
    for n in names - {"company"}:
        pat = re.escape(n).replace(r"\ ", r"[\s\W]+")
        # The filer owns the merger vehicle ("Merger Sub, a wholly owned
        # subsidiary of <filer>"), or the other party "will become a wholly
        # owned subsidiary of <filer>". Only the appositive form counts: "any
        # wholly owned subsidiary of <filer>" is boilerplate in a target's proxy.
        own = _WHOLLY_OWNED + pat + r"\b"
        if re.search(r"(?:merger\s+sub\w*(?:\s+(?:\d|i{1,3}))?|purchaser|bidco)[^;]{0,160}?\b(?:a|an|and)\s+" + own,
                     t, re.I) or re.search(r"will\s+become\s+(?:a|an)\s+[^.;]{0,120}?" + own, t, re.I):
            return "buyer"
    m = _COMPANY_DEF.search(t)
    if m is None:
        return "unknown"
    before = re.split(r"\)|\bamong\b|\bwith\b|\bbetween\b|,\s*and\b", t[max(0, m.start() - 200):m.start()], flags=re.I)[-1]
    if _has(before + " " + m.group(1), names):
        return "target"
    return "unknown"


_CLASS_XML = re.compile(r"<descriptionClassSecurity>(.*?)</descriptionClassSecurity>", re.I | re.S)
_CLASS_FORM15 = re.compile(r"([^()]{3,300}?)\s*\(\s*title\s+of\s+each\s+class", re.I)
_EQUITY_CLASS = re.compile(
    r"common\s+(?:stock|shares)|ordinary\s+shares|american\s+depositary\s+(?:shares|receipts)"
    r"|shares\s+of\s+beneficial\s+interest|capital\s+stock", re.I)
_NOT_THE_STOCK = re.compile(
    r"\bnotes?\b|debentures?|\bbonds?\b|\bdue\s+(?:19|20)\d\d\b|\bwarrants?\b|\bunits?\b|\brights?\b"
    r"|preferred|preference", re.I)


def delisted_class(text: str) -> str:
    """The class of security a Form 25 / Form 15 removes, as the filing states it."""
    m = _CLASS_XML.search(text or "")
    if m:
        return _plain(m.group(1))
    t = _plain(text)
    m = _CLASS_FORM15.search(t)
    if m:
        # The class sits just before "(Title of each class ...)"; drop the form heading.
        return re.split(r"\b(?:1934|Inc\.|Corp\.|Corporation|Company|Ltd\.)\s+", m.group(1))[-1].strip()
    return ""


def is_equity_class(security_class: str) -> Optional[bool]:
    """True: the listed stock. False: notes, warrants, units, preferred. None: not stated."""
    c = (security_class or "").strip()
    if not c:
        return None
    if _EQUITY_CLASS.search(c) and not _NOT_THE_STOCK.search(c):
        return True
    if _NOT_THE_STOCK.search(c):
        return False
    return None


def is_mini_tender_response(text: str) -> bool:
    return bool(re.search(r"mini[\s-]?tender", _plain(text), re.I))


_MERGER_AGREEMENT = r"(?:the\s+)?(?:merger\s+agreement|agreement\s+and\s+plan\s+of\s+merger|plan\s+of\s+merger)"
_DEAL_TERMINATED = [
    re.compile(r"\bterminat\w*\s+" + _MERGER_AGREEMENT, re.I),           # "agreed to terminate the Merger Agreement"
    re.compile(r"\btermination\s+of\s+" + _MERGER_AGREEMENT, re.I),      # "notice of termination of the Merger Agreement"
    # "the Merger Agreement (as amended), dated June 2, 2026, was terminated": only a
    # parenthetical or an appositive without another agreement may sit in between.
    re.compile(r"\b(?:merger\s+agreement|plan\s+of\s+merger)\b(?:\s*\([^()]{0,80}\))?"
               r"(?:\s*,(?:(?!agreement)[^.;]){0,60}?,)?\s*(?:was|were|has\s+been|had\s+been|is)\s+terminated", re.I),
]
# Conditional or contractual wording ("Parent may terminate the Merger Agreement",
# "in the event of a termination of the Merger Agreement") is not a termination.
_CONDITIONAL = re.compile(r"(?:\bmay|\bright\s+to|\bif|\bunless|\bwould|\bcould|\bin\s+the\s+event\s+of(?:\s+a)?|\bupon(?:\s+a)?"
                          r"|\bfollowing\s+(?:any|a))\s*$", re.I)


def terminates_merger(text: str) -> bool:
    """The filing says the merger agreement ITSELF was terminated.

    A target terminating something else "in connection with the Merger
    Agreement" (a credit facility repaid before closing, a rights plan) keeps
    the deal pending, so a mention of both words is not enough (audit E1).
    """
    t = _plain(text)
    for pattern in _DEAL_TERMINATED:
        for m in pattern.finditer(t):
            if not _CONDITIONAL.search(t[max(0, m.start() - 40):m.start()]):
                return True
    return False


# ── the check ────────────────────────────────────────────────────────────────

class _Unreadable(Exception):
    pass


def _reader(fetch_text: Optional[FetchText]):
    def read(filing: Dict[str, Any]) -> str:
        url = filing.get("url")
        if not url or fetch_text is None:
            raise _Unreadable(f"{filing.get('form')} filed {filing.get('filed')} has no readable document")
        try:
            return fetch_text(url, EVENTS.DOC_FETCH_MAX_BYTES)
        except Exception as exc:  # noqa: BLE001 - surfaced as "unavailable"
            raise _Unreadable(f"{filing.get('form')} filed {filing.get('filed')} could not be read "
                              f"({type(exc).__name__})") from exc
    return read


# Each way the check can fail: a short label, what happened, and what the reader
# can do about it. The text says what happened; the fix names the exact setting or action (I1: a missing
# contact, a rate limit and a timeout used to read alike, and all three told the
# reader to set the contact).
_SET_CONTACT = ("Set QUANTA_SEC_CONTACT to your email address in the repository .env "
                "(copy .env.example), or as an environment variable, then restart the server")
UNAVAILABLE_REASONS: Dict[str, tuple] = {
    "contact_missing": ("no SEC contact configured", "no SEC contact is configured for the server "
                        "(QUANTA_SEC_CONTACT is not set in its environment)", _SET_CONTACT),
    "contact_malformed": ("SEC contact is not a valid address", "QUANTA_SEC_CONTACT is set but is not an email address or web address",
                          "Set QUANTA_SEC_CONTACT to a valid email address, then restart the server"),
    "rate_limited": ("rate-limited by SEC EDGAR", "SEC EDGAR limited the request rate (HTTP 429)",
                     "Wait a few minutes, then run the scan again"),
    "http_error": ("SEC EDGAR returned an HTTP error", "SEC EDGAR answered HTTP {http_status}",
                   "Run the scan again later; HTTP 403 means SEC refused the request, "
                   "so check that QUANTA_SEC_CONTACT is a real address"),
    "timeout": ("SEC EDGAR timed out", "SEC EDGAR did not answer in time", "Run the scan again"),
    "network_error": ("SEC EDGAR unreachable", "SEC EDGAR could not be reached",
                      "Check the network connection, then run the scan again"),
    "no_registrant": ("no SEC registrant for the ticker", "no SEC registrant found for {symbol}",
                      "None while SEC lists no registrant for this ticker"),
    "coverage_short": ("filing history longer than the pages read",
                       "the SEC filing list read for {symbol} reaches back only to {covered_from} "
                       "after {pages} older pages",
                       "Held at Watchlist this scan. To check these names, raise EVENTS.FILING_PAGES_MAX "
                       "so more of their filing history is read"),
    "document_unreadable": ("a filing document could not be read", "{detail}", "Run the scan again"),
    "filing_ambiguous": ("a filing could not be read unambiguously", "{detail}", "None until the filing can be read unambiguously"),
    "foreign_reports_unread": ("foreign issuer reports not classified",
                               "{symbol} files as a foreign private issuer: a deal would be announced in a "
                               "Form 6-K (latest filed {filed}), which is free-form and not read by this check",
                               "None: check the company's recent 6-K reports on SEC EDGAR yourself "
                               "before acting"),
    "not_covered": ("exchange not covered by an event source", "no corporate-event source covers {exchange} listings "
                    "(SEC EDGAR covers US registrants only)",
                    "None: names on exchanges no event source covers get no Buy call (owner decision)"),
    "no_client": ("no SEC EDGAR client", "no SEC EDGAR client", "Restart the server"),
    "error": ("the check failed", "{detail}", "Run the scan again"),
}


def _unavailable(detail: str, reason: str = "error", http_status: Optional[int] = None,
                 **fields: Any) -> EventCheck:
    _label, template, fix = UNAVAILABLE_REASONS.get(reason, UNAVAILABLE_REASONS["error"])
    text = template.format(detail=detail, http_status=http_status, **fields)
    return EventCheck("unavailable", EVENT_CHECK_UNAVAILABLE,
                      f"Corporate-event check unavailable: {text}. Held at Watchlist until it can be checked.",
                      checked_at=datetime.now().isoformat(timespec="seconds"),
                      reason=reason, fix=fix, http_status=http_status)


def _acquisition_text(ev: EventCheck, proxy: Optional[Dict[str, Any]], merger_8k: Optional[Dict[str, Any]]) -> str:
    refs = []
    if ev.agreement_date:
        refs.append(f"merger agreement {ev.agreement_date}")
    elif merger_8k:
        refs.append(f"merger agreement 8-K filed {merger_8k['filed']}")
    if proxy:
        refs.append(f"{proxy['form']} filed {proxy['filed']}")
    ref = "; ".join(refs)
    if ev.deal_price is not None:
        head = f"Pending cash acquisition at ${ev.deal_price:,.2f} ({ref} on SEC EDGAR)."
    elif ev.stock:
        head = (f"Pending acquisition paid partly or wholly in the acquirer's stock ({ref} on SEC EDGAR); "
                "its value moves with the acquirer's share price, so no fixed deal price or spread is used.")
    else:
        head = f"Pending acquisition ({ref} on SEC EDGAR); deal price not stated in the filing text read."
    tail = " The price is anchored to the deal terms, so its range and past highs no longer describe its upside."
    if ev.cvr:
        tail += (" The contingent value right is non-transferable: it stays with holders at closing and"
                 " cannot be bought or sold separately." if ev.cvr_non_transferable
                 else " The deal also carries a contingent value right.")
    if ev.spread_pct is not None:
        tail += f" Remaining spread to the cash price: {ev.spread_pct:.1%}."
    return head + tail


def check(symbol: str, edgar: Any, today: Optional[date] = None, price: Optional[float] = None,
          atr_pct_20: Optional[float] = None, atr_pct_120: Optional[float] = None,
          fetch_text: Optional[FetchText] = None) -> EventCheck:
    """Look for a pending acquisition or delisting of ``symbol`` in its SEC filings."""
    today = today or date.today()
    for suffix, exchange in EVENTS.UNCOVERED_LISTINGS.items():
        if symbol.upper().endswith(suffix):
            ev = _unavailable("", "not_covered", exchange=exchange)
            ev.code = EVENT_CHECK_NOT_COVERED
            return ev
    if edgar is None:
        return _unavailable("", "no_client")
    forms = (set(EVENTS.STRONG_FORMS) | set(EVENTS.DELISTING_FORMS) | set(EVENTS.SUCCESSION_FORMS)
             | set(EVENTS.FOREIGN_CURRENT_FORMS) | {"8-K"})
    res = edgar.recent_filings(symbol, today - timedelta(days=EVENTS.LOOKBACK_DAYS), forms=forms)
    status = (res or {}).get("status")
    if status == "no_cik":
        return _unavailable("", "no_registrant", symbol=symbol)
    if status != "ok":
        res = res or {}
        detail = res.get("detail") or res.get("error") or "SEC EDGAR did not answer"
        return _unavailable(detail, res.get("reason") or "error", res.get("http_status"))

    ev_out = _check_filings(symbol, res, today, price, atr_pct_20, atr_pct_120, fetch_text)
    if ev_out.status == "clear" and res.get("coverage_short"):
        # A heavy filer's filing list did not reach back across the whole window,
        # so an older deal filing could be missing: never call that clear (audit E2).
        ev_out = _unavailable("", "coverage_short", symbol=symbol,
                              covered_from=res.get('covered_from') or 'an unknown date',
                              pages=int(res.get('pages_read') or 0))
    ev_out.registrant = str((res or {}).get("name") or "")
    ev_out.cik = (res or {}).get("cik")
    return ev_out


def _check_filings(symbol: str, res: Dict[str, Any], today: date, price: Optional[float],
                   atr_pct_20: Optional[float], atr_pct_120: Optional[float],
                   fetch_text: Optional[FetchText]) -> EventCheck:
    filings = sorted(res.get("filings") or [], key=lambda f: f.get("filed") or "", reverse=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    read = _reader(fetch_text)

    aliases = registrant_aliases((res or {}).get("name", ""), symbol)
    successions = [f.get("filed") or "" for f in filings if f.get("form") in EVENTS.SUCCESSION_FORMS]
    unresolved: List[str] = []          # delisting forms whose class could not be read
    for f in filings:
        if f.get("form") not in EVENTS.DELISTING_FORMS:
            continue
        if any(s and s <= (f.get("filed") or "") for s in successions):
            continue                    # predecessor shares replaced by a successor issuer's
        # EDGAR lists the styled view (".../xslF25X02/primary_doc.xml"); the raw
        # document carries the class field.
        raw = dict(f, url=re.sub(r"/xsl[^/]+/", "/", f["url"])) if f.get("url") else f
        try:
            security_class = delisted_class(read(raw))
        except _Unreadable as exc:
            unresolved.append(str(exc))
            continue
        equity = is_equity_class(security_class)
        if equity:
            return EventCheck("flagged", DELISTING,
                              f"Delisting of {security_class} filed with the SEC ({f['form']} filed {f['filed']}). "
                              "The listing is ending, so there is no directional thesis to fund.",
                              f"SEC EDGAR {f['form']} {f['filed']}", f.get("url"), f.get("filed"), checked_at=stamp)
        if equity is None:
            unresolved.append(f"{f['form']} filed {f['filed']} does not state the class of security removed")

    try:
        proxy = None
        merger_8k = None
        ambiguous: List[str] = []
        terms: Dict[str, Any] = {}
        for f in filings:
            form, items = f.get("form"), f.get("items") or []
            if form in EVENTS.STRONG_FORMS and proxy is None:
                try:
                    text = read(f)
                except _Unreadable:
                    text = ""          # a target-only form stands on its own
                if text and form in ("DEFM14A", "PREM14A") and (
                        is_merger_acquirer(text) or filer_side(text, aliases) == "buyer"):
                    continue           # the buyer's proxy (e.g. a share issuance vote)
                if text and form in ("SC 14D9", "SC 14D9F") and is_mini_tender_response(text):
                    continue           # a sub-5% mini-tender the company tells holders to reject
                if text and form in ("DEFM14A", "PREM14A") and not re.search(
                        r"\bmerger\b|plan\s+of\s+merger|tender\s+offer|scheme\s+of\s+arrangement", _plain(text), re.I):
                    # A merger-form proxy with no takeover wording (an asset sale, say)
                    # is not read as the filer being acquired; it is not cleared either.
                    ambiguous.append(f"{form} filed {f['filed']} does not say the company is being acquired")
                    continue
                proxy = f
                terms = {k: v for k, v in deal_terms(text).items() if v} if text else {}
            elif form == "8-K" and EVENTS.MERGER_8K_ITEM in items and merger_8k is None:
                text = read(f)
                if is_merger_target(text):
                    side = filer_side(text, aliases)
                    if side == "buyer":
                        continue       # the acquirer's own 8-K ("the Company" is its target)
                    if side == "unknown" and _COMPANY_DEF.search(_plain(text)):
                        # names someone else as "the Company" with no buyer evidence
                        ambiguous.append(f"8-K filed {f['filed']} names another company as the merger party")
                        continue
                    merger_8k = f
                    for k, v in deal_terms(text).items():
                        terms.setdefault(k, v) if v else None
        deal = merger_8k or proxy
        if deal is not None:
            deal_filed = min(x["filed"] for x in (merger_8k, proxy) if x)
            for f in filings:
                items_f = f.get("items") or []
                # A filing that also signs an agreement (item 1.01) may be swapping
                # one bidder for another: it never clears on its own.
                if (f.get("form") == "8-K" and EVENTS.TERMINATION_8K_ITEM in items_f
                        and EVENTS.MERGER_8K_ITEM not in items_f
                        and (f.get("filed") or "") >= deal_filed and terminates_merger(read(f))):
                    return EventCheck("clear", note=f"merger agreement terminated (8-K item 1.02 filed {f['filed']})",
                                      source=f"SEC EDGAR 8-K {f['filed']}", url=f.get("url"), checked_at=stamp)
            ev = EventCheck("flagged", PENDING_ACQUISITION, "", f"SEC EDGAR {deal['form']} {deal['filed']}",
                            deal.get("url"), deal.get("filed"), terms.get("agreement_date"),
                            terms.get("deal_price"), bool(terms.get("cvr")), bool(terms.get("cvr_non_transferable")),
                            stock=bool(terms.get("stock")), checked_at=stamp)
            if ev.deal_price and price and price > 0:
                ev.spread_pct = (ev.deal_price - float(price)) / float(price)
            ev.text = _acquisition_text(ev, proxy, merger_8k)
            return ev
    except _Unreadable as exc:
        return _unavailable(str(exc), "document_unreadable")

    if ambiguous:
        unresolved.append(ambiguous[0])
    if unresolved:
        return _unavailable(unresolved[0], "filing_ambiguous")
    if atr_pct_20 and atr_pct_120 and atr_pct_120 > 0 and atr_pct_20 / atr_pct_120 < EVENTS.VOL_COLLAPSE_RATIO:
        return EventCheck("flagged", POSSIBLE_PENDING_DEAL,
                          f"Daily range collapsed to {atr_pct_20:.1%} from {atr_pct_120:.1%} with no deal filing "
                          "found; check for a pending deal before treating this as a directional idea.",
                          "price behaviour", checked_at=stamp)
    current = [f for f in filings if f.get("form") in EVENTS.FOREIGN_CURRENT_FORMS]
    if current:
        # A foreign private issuer files no 8-K or merger proxy; its 6-Ks are
        # free-form and not classified here, so no deal found is not "clear".
        return _unavailable("", "foreign_reports_unread", symbol=symbol, filed=current[0].get("filed") or "")
    return EventCheck("clear", checked_at=stamp)


# ── shared client (one per process, so its caches outlive a scan) ────────────

_DEFAULT: Dict[str, Any] = {}
_DEFAULT_LOCK = threading.Lock()
# Filing documents by URL, oldest first; bounded by EVENTS.DOC_CACHE_MAX_BYTES (audit E3).
_DOC_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_DOC_CACHE_LOCK = threading.Lock()
_DOC_CACHE_BYTES = [0]


def default_edgar():
    """The process-wide EDGAR client for event checks (contact required)."""
    with _DEFAULT_LOCK:
        if "edgar" not in _DEFAULT:
            from functools import partial
            from portfolio.sources import EdgarClient, http_get_json
            _DEFAULT["edgar"] = EdgarClient(get_json=partial(http_get_json, require_contact=True))
        return _DEFAULT["edgar"]


def default_fetch_text(url: str, max_bytes: int) -> str:
    """Read a filing document through the SEC archive, cached per process."""
    with _DOC_CACHE_LOCK:
        hit = _DOC_CACHE.get(url)
        if hit and time.time() - hit[0] < EVENTS.DOC_CACHE_SECONDS:
            _DOC_CACHE.move_to_end(url)
            return hit[1]
    from portfolio.sources import http_get_text
    text = http_get_text(url, max_bytes, require_contact=True)
    _cache_doc(url, text)
    return text


def _cache_doc(url: str, text: str) -> None:
    """Keep a document, dropping expired entries and then the least recently used
    until the cache fits ``EVENTS.DOC_CACHE_MAX_BYTES``."""
    size = len(text)
    now = time.time()
    with _DOC_CACHE_LOCK:
        old = _DOC_CACHE.pop(url, None)
        if old is not None:
            _DOC_CACHE_BYTES[0] -= len(old[1])
        for key in [k for k, (at, _) in _DOC_CACHE.items() if now - at >= EVENTS.DOC_CACHE_SECONDS]:
            _DOC_CACHE_BYTES[0] -= len(_DOC_CACHE.pop(key)[1])
        while _DOC_CACHE and _DOC_CACHE_BYTES[0] + size > EVENTS.DOC_CACHE_MAX_BYTES:
            _DOC_CACHE_BYTES[0] -= len(_DOC_CACHE.popitem(last=False)[1][1])
        if size <= EVENTS.DOC_CACHE_MAX_BYTES:
            _DOC_CACHE[url] = (now, text)
            _DOC_CACHE_BYTES[0] += size


def summarize(checks: Dict[str, "EventCheck"], book: Optional[List[str]] = None) -> Dict[str, Any]:
    """Counts by status and, for unavailable checks, by reason with its label and
    fix, so the page can say why the check failed and what clears it (I1).
    ``book`` names the candidates that reached the book (listed per reason)."""
    book_set = set(book or [])
    out: Dict[str, Any] = {"checked": 0, "clear": 0, "flagged": 0, "unavailable": 0, "reasons": []}
    by_reason: Dict[str, Dict[str, Any]] = {}
    for sym, ev in checks.items():
        out["checked"] += 1
        key = ev.status if ev.status in ("clear", "flagged") else "unavailable"
        out[key] += 1
        if key != "unavailable":
            continue
        reason = ev.reason or "error"
        label, _template, fix = UNAVAILABLE_REASONS.get(reason, UNAVAILABLE_REASONS["error"])
        row = by_reason.setdefault(reason, {"reason": reason, "code": ev.code, "label": label, "fix": ev.fix or fix,
                                            "count": 0, "tickers": [], "book_tickers": []})
        row["count"] += 1
        row["tickers"].append(sym)          # [P2] every unreadable name, not only book names
        if sym in book_set:
            row["book_tickers"].append(sym)
    out["reasons"] = sorted(by_reason.values(), key=lambda r: -r["count"])
    return out


def check_many(symbols: List[str], prices: Optional[Dict[str, float]] = None,
               atr: Optional[Dict[str, tuple]] = None, today: Optional[date] = None,
               edgar: Any = None, fetch_text: Optional[FetchText] = None) -> Dict[str, EventCheck]:
    """Check several names with the shared client (sequential: SEC rate limit)."""
    edgar = edgar if edgar is not None else default_edgar()
    fetch_text = fetch_text or default_fetch_text
    out = {}
    for sym in symbols:
        a20, a120 = (atr or {}).get(sym, (None, None))
        try:
            out[sym] = check(sym, edgar, today, (prices or {}).get(sym), a20, a120, fetch_text)
        except Exception as exc:  # noqa: BLE001 - one name's failure is that name's "unavailable"
            out[sym] = _unavailable(f"{type(exc).__name__} while checking {sym}", "error")
    return out
