"""News & Events: what is scheduled, what was filed, what was reported, for THESE holdings.

Every item is one of four kinds of statement and says which:

* ``confirmed``   the company or an agency published it (a verified report date, an
                  SEC filing, an official release schedule);
* ``estimate``    a date or figure from a data vendor that the company has not confirmed
                  (a tentative report date, a projected date from market data);
* ``reported``    a news outlet said it; QUANTA has not verified it;
* ``interpretation`` never produced here. QUANTA does not say a headline moved a price.

Times keep their zone. A scheduled event carries its own (America/New_York for US
markets); a news item carries its publisher's UTC timestamp. Nothing is given a
date it was not given.

Each important item fills four fixed fields from facts it holds, and leaves a field
empty when it holds nothing: ``what`` happened or is scheduled, ``why`` it matters to
this portfolio (weights from the account, policy from QUANTA), ``exposure`` (the
holdings and the share of the account affected) and ``watch`` (the next
checkable thing).
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .sources import ITEM_TEXT, MATERIAL_ITEMS

NY = ZoneInfo("America/New_York")
WEEK_DAYS = 7
HORIZON_DAYS = 31
NEWS_MAX_AGE_HOURS = 72

TIMING_TEXT = {"am": "before the market opens", "pm": "after the market closes"}
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by", "with", "is", "are", "from", "its",
         "stock", "shares", "inc", "corp", "co", "ltd"}


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _weight(rows_by_sym: Dict[str, Dict[str, Any]], symbols: Iterable[str]) -> Optional[float]:
    ws = [_num((rows_by_sym.get(s) or {}).get("broker", {}).get("weight")) for s in symbols]
    ws = [w for w in ws if w is not None]
    return sum(ws) if ws else None


def _pct(w: Optional[float]) -> str:
    return f"{w * 100:.1f}%" if w is not None else "an unknown share"


def _bucket(days: Optional[int]) -> str:
    if days is None:
        return "later"
    if days < 0:
        return "recent"
    if days == 0:
        return "today"
    return "week" if days <= WEEK_DAYS else "later"


def _tokens(title: str) -> frozenset:
    return frozenset(t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in _STOP and len(t) > 1)


def _similar(a: frozenset, b: frozenset) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.75


def _entity_terms(row: Dict[str, Any]) -> List[str]:
    """Names a headline could use for this holding: the company name, without corporate suffixes."""
    name = str((row.get("broker") or {}).get("name") or (row.get("analysis") or {}).get("company_name") or "")
    name = re.sub(r"(?i)\b(inc|corp|corporation|co|company|ltd|plc|holdings|group|class [a-c])\b\.?,?", "", name).strip(" ,.")
    return [name] if len(name) >= 4 else []


def match_articles(articles: Sequence[Dict[str, Any]], rows: Sequence[Dict[str, Any]]) -> Dict[int, List[str]]:
    """Which holdings each article is about: a whole-word ticker (cashtag or 2+ letters caps) or the company name."""
    out: Dict[int, List[str]] = {}
    for i, a in enumerate(articles):
        text = f"{a.get('title', '')} {a.get('description', '')}"
        hits = []
        for r in rows:
            sym = r["symbol"]
            cashtag = re.search(rf"\${re.escape(sym)}(?![A-Za-z0-9])", text)
            bare = re.search(rf"(?<![A-Za-z0-9$]){re.escape(sym)}(?![A-Za-z0-9])", text)
            named = any(re.search(rf"(?i)\b{re.escape(n)}\b", text) for n in _entity_terms(r))
            # A bare ticker counts only when it is 3+ capitals and not an ordinary word (ALL, NEW, KEY);
            # shorter tickers need a cashtag or the company name.
            if named or cashtag or (bare and len(sym) >= 3 and sym not in _COMMON_WORDS):
                hits.append(sym)
        if hits:
            out[i] = hits
    return out


_COMMON_WORDS = {"ALL", "ARE", "FOR", "NOW", "ONE", "OUT", "NEW", "CAN", "HAS", "KEY", "LOW", "OPEN", "REAL", "SEE",
                 "TOP", "WELL", "BIG", "CAR", "EAT", "FAST", "GOOD", "HUGE", "LIVE", "LOVE", "PLAY", "RUN", "SAFE"}


# ---------------------------------------------------------------------------
# Item builders
# ---------------------------------------------------------------------------

def _earnings_items(rows: Sequence[Dict[str, Any]], by_sym: Dict[str, Dict[str, Any]], today: date,
                    earnings_window_days: int) -> List[Dict[str, Any]]:
    items = []
    for r in rows:
        b = r.get("broker") or {}
        m = r.get("market") or {}
        d = b.get("earnings_date") or m.get("earnings_date")
        if not d:
            continue
        try:
            when = date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        days = (when - today).days
        if days < 0 or days > HORIZON_DAYS:
            continue
        verified = b.get("earnings_verified")
        source = m.get("earnings_source") or ("broker" if verified is not None else "")
        if verified is True:
            status, basis = "confirmed", "Company-confirmed report date (Robinhood earnings calendar)"
        elif verified is False:
            status, basis = "estimate", "Tentative date: the company has not confirmed it (Robinhood earnings calendar)"
        else:
            status = "estimate"
            basis = ("Date from market data; the company's confirmation status was not reported" if source == "market"
                     else "Date from the broker; the company's confirmation status was not reported")
        timing = TIMING_TEXT.get(b.get("earnings_timing") or "")
        w = _num(b.get("weight"))
        eps = _num(b.get("eps_estimate"))
        why = [f"{r['symbol']} is {_pct(w)} of the account."]
        if days <= earnings_window_days:
            why.append(f"QUANTA holds new money in a name within {earnings_window_days} days of a report.")
        items.append({
            "id": f"earn:{r['symbol']}:{when.isoformat()}", "kind": "earnings", "status": status,
            "title": f"{r['symbol']} reports earnings", "symbols": [r["symbol"]],
            "date": when.isoformat(), "time": None, "tz": "America/New_York", "days": days, "bucket": _bucket(days),
            "when_text": timing or "time of day not yet known",
            "what": f"{r['symbol']} is scheduled to report {when.strftime('%b %d')}, {timing or 'time of day not yet known'}.",
            "why": " ".join(why),
            "exposure": {"symbols": [r["symbol"]], "weight": w},
            "watch": ("Results against the consensus EPS estimate of $%.2f, and any change to guidance." % eps)
            if eps is not None else "Results against consensus, and any change to guidance.",
            "source": {"name": "Robinhood earnings calendar" if source != "market" else "Market data (Yahoo)",
                       "url": None}, "basis": basis,
        })
    return items


def _dividend_items(rows: Sequence[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    items = []
    for r in rows:
        b = r.get("broker") or {}
        d = b.get("ex_dividend_date")
        dps = _num(b.get("dividend_per_share"))
        if not d or dps is None:
            continue
        try:
            when = date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        days = (when - today).days
        if days < 0 or days > 14:
            continue
        qty = _num(b.get("quantity"))
        pay = b.get("payable_date")
        items.append({
            "id": f"div:{r['symbol']}:{when.isoformat()}", "kind": "dividend", "status": "confirmed",
            "title": f"{r['symbol']} goes ex-dividend", "symbols": [r["symbol"]],
            "date": when.isoformat(), "time": None, "tz": "America/New_York", "days": days, "bucket": _bucket(days),
            "what": f"Ex-dividend {when.strftime('%b %d')}: ${dps:.4g} per share"
                    f"{', payable ' + str(pay)[:10] if pay else ''}.",
            "why": "Shares bought on or after this date do not receive this payment; the share price usually falls by about the dividend that day.",
            "exposure": {"symbols": [r["symbol"]], "weight": _num(b.get("weight")),
                         "estimate": dps * qty if qty else None},
            "watch": "The payable date, and whether the next declaration keeps the amount.",
            "source": {"name": "Robinhood fundamentals (declared dividend)", "url": None},
            "basis": "Declared dividend schedule as reported by the broker",
        })
    return items


def _filing_items(filings: Dict[str, Dict[str, Any]], by_sym: Dict[str, Dict[str, Any]], today: date
                  ) -> List[Dict[str, Any]]:
    items = []
    for sym, res in filings.items():
        for f in res.get("filings", []):
            try:
                filed = date.fromisoformat(f["filed"])
            except (KeyError, ValueError):
                continue
            items_txt = [f"Item {i}: {ITEM_TEXT.get(i, 'see filing')}" for i in f.get("items", []) if i != "9.01"]
            material = f["form"] in ("10-Q", "10-K", "20-F", "40-F") or any(i in MATERIAL_ITEMS for i in f.get("items", []))
            w = _weight(by_sym, [sym])
            what = f"{sym} filed a {f['form']}" + (f" ({'; '.join(items_txt)})" if items_txt else "") + f" on {filed.strftime('%b %d')}."
            accepted = f.get("accepted")
            items.append({
                "id": f"sec:{f.get('accession') or sym + f['filed']}", "kind": "filing", "status": "confirmed",
                "title": f"{sym} {f['form']}", "symbols": [sym], "date": filed.isoformat(),
                "time": accepted, "tz": "UTC" if accepted else "America/New_York", "days": (filed - today).days,
                "bucket": "today" if filed == today else "recent", "material": material,
                "what": what,
                "why": (f"{sym} is {_pct(w)} of the account. "
                        + ("A results, governance or financing filing can change how the holding is read."
                           if material else "This is a routine filing.")),
                "exposure": {"symbols": [sym], "weight": w},
                "watch": "Read the filing itself; QUANTA has not summarised it.",
                "source": {"name": "SEC EDGAR", "url": f.get("url")}, "basis": "Filed with the SEC",
            })
    return items


def _macro_items(calendar: Dict[str, Any], today: date, beta: Optional[float]) -> List[Dict[str, Any]]:
    items = []
    for e in calendar.get("events", []):
        try:
            when = date.fromisoformat(e["date"])
        except ValueError:
            continue
        days = (when - today).days
        if days < 0 or days > HORIZON_DAYS:
            continue
        items.append({
            "id": f"macro:{e['kind']}:{e['date']}", "kind": "macro", "status": "confirmed",
            "title": e["title"], "symbols": [], "date": e["date"], "time": e.get("time"),
            "tz": e.get("tz") or "America/New_York", "days": days, "bucket": _bucket(days),
            "what": f"{e['title']} is scheduled for {when.strftime('%b %d')}"
                    f"{', ' + e['time'] + ' ET' if e.get('time') else ''}." + (f" {e['detail']}" if e.get("detail") else ""),
            "why": ("Market-wide: it can move every holding at once."
                    + (f" The account's modeled beta is {beta:.2f}, so it has historically moved about {beta:.2f}x the market."
                       if beta is not None else "")),
            "exposure": {"symbols": [], "weight": None, "scope": "whole account"},
            "watch": "The published figure or decision against consensus; QUANTA does not forecast it.",
            "source": {"name": e.get("source", ""), "url": e.get("url")},
            "basis": "Date published by the source, as transcribed on " + str(calendar.get("retrieved_on") or "an unknown date"),
        })
    return items


def _news_items(articles: Sequence[Dict[str, Any]], rows: Sequence[Dict[str, Any]], by_sym: Dict[str, Dict[str, Any]],
                now: datetime, macro_limit: int = 3) -> List[Dict[str, Any]]:
    hits = match_articles(articles, rows)
    items: List[Dict[str, Any]] = []
    seen: List[frozenset] = []
    ordered = sorted(range(len(articles)), key=lambda i: articles[i].get("published") or "", reverse=True)
    macro_used = 0
    for i in ordered:
        a = articles[i]
        pub = a.get("published")
        try:
            pub_dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue                                   # no publication time -> cannot state freshness -> not shown
        age_h = (now - pub_dt).total_seconds() / 3600.0
        if age_h > NEWS_MAX_AGE_HOURS or age_h < -1:
            continue
        syms = hits.get(i, [])
        macro = not syms and a.get("category") == "macro"
        if not syms and not macro:
            continue
        if macro:
            if macro_used >= macro_limit:
                continue
        title = str(a.get("title") or "").strip()
        tok = _tokens(title)
        if any(_similar(tok, s) for s in seen):
            continue                                   # the same story from another outlet
        seen.append(tok)
        if macro:
            macro_used += 1
        w = _weight(by_sym, syms) if syms else None
        pub_day = pub_dt.astimezone(NY).date()
        today = now.astimezone(NY).date()
        items.append({
            "id": "news:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:12], "kind": "news", "status": "reported",
            "title": title, "symbols": syms, "date": pub_day.isoformat(),
            "time": pub_dt.astimezone(timezone.utc).isoformat(), "tz": "UTC", "days": (pub_day - today).days,
            # "Today" is the New York calendar day, not a rolling 24 hours.
            "bucket": "today" if pub_day == today else "recent", "age_hours": round(age_h, 1),
            "what": title,
            "why": (f"Names {', '.join(syms)} ({_pct(w)} of the account)." if syms
                    else "Market-wide headline; it may bear on every holding."),
            "exposure": {"symbols": syms, "weight": w, **({} if syms else {"scope": "whole account"})},
            "watch": ("Not confirmed by the company in this feed. Look for a filing or release before acting on it." if syms
                      else "Not confirmed by an official source in this feed. Check the issuing agency's own release before acting on it."),
            "source": {"name": a.get("source") or "News feed", "url": a.get("link") or a.get("url")},
            "basis": "Reported by a news outlet; QUANTA has not verified it and does not say it moved a price",
        })
    return items


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_events(rows: Sequence[Dict[str, Any]], *, now: datetime, policy: Dict[str, Any],
                 filings: Dict[str, Dict[str, Any]], articles: Sequence[Dict[str, Any]],
                 calendar: Dict[str, Any], portfolio_beta: Optional[float] = None) -> Dict[str, Any]:
    """The Today / This Week / Later feed, with each source's own health."""
    today = now.astimezone(NY).date()
    by_sym = {r["symbol"]: r for r in rows}
    items: List[Dict[str, Any]] = []
    items += _earnings_items(rows, by_sym, today, int(policy.get("earnings_window_days") or 7))
    items += _dividend_items(rows, today)
    items += _filing_items(filings, by_sym, today)
    items += _macro_items(calendar, today, portfolio_beta)
    items += _news_items(articles, rows, by_sym, now)

    # One item per id, keeping the first (builders run most-authoritative first).
    seen_ids, unique = set(), []
    for it in items:
        if it["id"] in seen_ids:
            continue
        seen_ids.add(it["id"])
        unique.append(it)

    rank = {"today": 0, "week": 1, "later": 2, "recent": 3}
    kind_rank = {"earnings": 0, "filing": 1, "dividend": 2, "macro": 3, "news": 4}

    # Relevance: an item that names a holding is "holding"; a market-wide one is "market".
    # Holding items lead each bucket so general stories cannot crowd them out.
    for it in unique:
        it["relevance"] = "holding" if (it.get("exposure") or {}).get("symbols") else "market"

    def key(it: Dict[str, Any]):
        recent = it["bucket"] == "recent"
        return (rank[it["bucket"]], 0 if it["relevance"] == "holding" else 1,
                it["date"] if not recent else "", -(it.get("exposure", {}).get("weight") or 0),
                kind_rank.get(it["kind"], 9), "" if not recent else it.get("time") or "")

    unique.sort(key=key)
    # Recent items read newest first.
    recent = sorted([i for i in unique if i["bucket"] == "recent"], key=lambda i: i.get("time") or i["date"], reverse=True)
    upcoming = [i for i in unique if i["bucket"] != "recent"]
    counts = {b: sum(1 for i in upcoming if i["bucket"] == b) for b in ("today", "week", "later")}
    counts["recent"] = len(recent)

    filing_status = [r.get("status") for r in filings.values()]
    sources = {
        "earnings": {"name": "Robinhood earnings calendar", "status": "ok" if any(
            (r.get("broker") or {}).get("earnings_date") for r in rows) else "no_dates"},
        "filings": {"name": "SEC EDGAR", "status": ("error" if filing_status and all(s == "error" for s in filing_status)
                                                    else "ok" if "ok" in filing_status else "none"),
                    "checked": len(filings), "no_cik": sum(1 for s in filing_status if s == "no_cik")},
        "macro": {"name": "Federal Reserve and BLS schedules", "status": calendar.get("status"),
                  "retrieved_on": calendar.get("retrieved_on"), "last_date": calendar.get("last_date")},
        "news": {"name": "News feeds", "status": "ok" if articles else "none", "articles": len(articles)},
    }
    return {"generated_at": now.astimezone(timezone.utc).isoformat(), "today": today.isoformat(),
            "timezone": "America/New_York", "counts": counts, "upcoming": upcoming, "recent": recent[:12],
            "sources": sources}
