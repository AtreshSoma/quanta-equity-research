"""P2: heavy filers' older EDGAR pages are read across the whole look-back.

On 2026-10-05 the 730-day look-back needed 11 older submission pages for JPM,
9 for MS, 6 for C and 5 for GS and BAC (measured on SEC's public index); the
gate read at most EVENTS.FILING_PAGES_MAX = 4, so those five names were
"unavailable" with "None until the whole filing window can be read". Pages
are now read up to the look-back (bounded, throttled by the client, cached:
an older page never changes). A name still short names itself, with a plain
reason and action. Synthetic filings only.
"""
from __future__ import annotations

from datetime import date, timedelta

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from engine import thresholds as T  # noqa: E402
from portfolio import sources  # noqa: E402
from portfolio.sources import EdgarClient  # noqa: E402

from test_corporate_events import TODAY, PagedSec, _paged  # noqa: E402


def _monthly(n_pages):
    """A JPM-shaped filer: the recent block covers one year, then one page a month."""
    end = TODAY - timedelta(days=366)
    pages = []
    for i in range(n_pages):
        start = end - timedelta(days=31)
        pages.append((f"CIK0001521036-submissions-{i + 1:03d}.json", start.isoformat(), end.isoformat(),
                      [("424B2", (start + timedelta(days=3)).isoformat(), "", f"p{i}.htm")]))
        end = start - timedelta(days=1)
    return _paged([("424B2", (TODAY - timedelta(days=2)).isoformat(), "", "n.htm"),
                   ("424B2", (TODAY - timedelta(days=365)).isoformat(), "", "m.htm")], pages)


def _check(fake):
    return ce.check("EXDX", EdgarClient(get_json=fake.get_json), TODAY, fetch_text=fake.get_text)


def test_a_filer_needing_eleven_older_pages_is_checked_in_full():
    sources._PAGE_CACHE.clear()
    subs, bodies = _monthly(14)
    fake = PagedSec(subs, bodies)
    ev = _check(fake)
    assert ev.status == "clear", ev.text
    read = [c for c in fake.calls if "submissions-0" in c]
    assert 11 <= len(read) <= 13, len(read)            # only pages inside the look-back


def test_older_pages_are_cached_across_checks():
    sources._PAGE_CACHE.clear()
    subs, bodies = _monthly(14)
    first, second = PagedSec(subs, bodies), PagedSec(subs, bodies)
    _check(first)
    _check(second)                                     # a new client: no filings cache
    assert not [c for c in second.calls if "submissions-0" in c], "older pages come from the cache"


def test_a_filer_still_short_after_the_cap_names_why_and_what_to_do(monkeypatch):
    sources._PAGE_CACHE.clear()
    monkeypatch.setattr(T.EVENTS, "FILING_PAGES_MAX", 3)
    subs, bodies = _monthly(14)
    ev = _check(PagedSec(subs, bodies))
    assert ev.status == "unavailable" and ev.reason == "coverage_short"
    assert "after 3 older pages" in ev.text
    assert "None until" not in ev.fix and "EVENTS.FILING_PAGES_MAX" in ev.fix and "held at watchlist" in ev.fix.lower()


def test_the_summary_names_every_unreadable_ticker():
    checks = {"JPM": ce._unavailable("", "coverage_short", symbol="JPM", covered_from="2025-01-01", pages=16),
              "MS": ce._unavailable("", "coverage_short", symbol="MS", covered_from="2025-02-01", pages=16),
              "AAPL": ce.EventCheck("clear")}
    (row,) = ce.summarize(checks, book=["AAPL"])["reasons"]
    assert row["tickers"] == ["JPM", "MS"] and row["count"] == 2
