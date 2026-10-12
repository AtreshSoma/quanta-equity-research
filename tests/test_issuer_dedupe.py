"""I7: two share classes of one issuer are one exposure.

The book deduplicated names only by stripping .NS/.BO, so two share classes of
one company (GOOGL and GOOG in the 2026-10-04 scan) were separate names: each got
its own position cap, and only a near-identical price history happened to stop
the second through the correlation check. The issuer identity is data-driven:
the SEC CIK the corporate-event check already resolves. Without a CIK the
ticker stays its own issuer, as before. Synthetic names and CIKs only.
"""
from __future__ import annotations

from datetime import date

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from portfolio.sources import EdgarClient  # noqa: E402

from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick  # noqa: E402

CIKS = {"CLSA": 1111, "CLSC": 1111, "OTHR": 2222}


def _clear_with(ciks):
    return lambda symbols, **_: {s: ce.EventCheck("clear", cik=ciks.get(s)) for s in symbols}


def _book(monkeypatch, ciks):
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    sysm._event_checker = _clear_with(ciks)
    # Distinct seeds: the two share classes' histories are not correlated, so
    # only the issuer identity can tell them apart.
    sysm.scan_results = [_pick("CLSA", "Comm", 8.0, seed=7), _pick("CLSC", "Comm", 7.9, seed=8),
                         _pick("OTHR", "Tech", 7.5, seed=3)]
    out = sysm.get_top_recommendations(max_positions=5)
    return sysm, {r.ticker for r in out if r.allocation > 0}


def test_two_share_classes_of_one_issuer_are_funded_once(monkeypatch):
    sysm, funded = _book(monkeypatch, CIKS)
    assert funded == {"CLSA", "OTHR"}, funded
    sel = {s["key"]: s for s in sysm.scan_accounting()["book_stages"]}["selection"]
    assert sel["same_issuer"] == {"count": 1, "tickers": ["CLSC"], "as": {"CLSC": "CLSA"}}


def test_different_issuers_are_both_funded(monkeypatch):
    _sysm, funded = _book(monkeypatch, {"CLSA": 1111, "CLSC": 3333, "OTHR": 2222})
    assert funded == {"CLSA", "CLSC", "OTHR"}


def test_without_a_cik_each_ticker_stays_its_own_issuer(monkeypatch):
    _sysm, funded = _book(monkeypatch, {})
    assert funded == {"CLSA", "CLSC", "OTHR"}


def test_the_event_check_carries_the_cik_it_resolved():
    subs = {"name": "Example Co", "filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": [],
                                                          "primaryDocument": [], "items": []}}}

    def get_json(url):
        return {"0": {"ticker": "EXDX", "cik_str": 1521036}} if url.endswith("company_tickers.json") else subs
    ev = ce.check("EXDX", EdgarClient(get_json=get_json), date(2026, 10, 1))
    assert ev.cik == 1521036
    assert ev.to_dict()["cik"] == 1521036
    assert "cik" not in ce.EventCheck("clear").to_dict(), "no CIK, no key"


def _book_with(monkeypatch, verdicts):
    _install_fake_deep(monkeypatch, verdicts)
    sysm = _bare_system()
    sysm._event_checker = _clear_with(CIKS)
    sysm.scan_results = [_pick("CLSA", "Comm", 8.0, seed=7), _pick("CLSC", "Comm", 7.9, seed=8),
                         _pick("OTHR", "Tech", 7.5, seed=3)]
    out = sysm.get_top_recommendations(max_positions=5)
    return sysm, {r.ticker for r in out if r.allocation > 0}


def test_a_twin_is_held_back_when_research_holds_back_the_first_class(monkeypatch):
    """D2 (October 2026): share classes fail closed. CLSA fails the Research
    check, so CLSC (same CIK) is held back too rather than funded in its
    place, and its note says why. The issuer link is the CIK from the data."""
    sysm, funded = _book_with(monkeypatch, {"CLSA": "WATCHLIST"})
    assert funded == {"OTHR"}, funded
    assert sysm._same_issuer_skips.get("CLSC") == "CLSA"
    note = sysm._selection_skips["CLSC"]
    assert note["reason"] == "same_issuer" and note["held_by_research"] is True
    assert "Research check held CLSA back" in note["text"]


def test_a_twin_ranked_behind_a_funded_class_is_not_marked_held(monkeypatch):
    sysm, _funded = _book_with(monkeypatch, {})
    note = sysm._selection_skips["CLSC"]
    assert note["held_by_research"] is False
    assert "ranked ahead" in note["text"]


def test_two_classes_are_never_funded_together(monkeypatch):
    for verdicts in ({}, {"OTHR": "WATCHLIST"}):
        _sysm, funded = _book_with(monkeypatch, verdicts)
        assert len(funded & {"CLSA", "CLSC"}) == 1, (verdicts, funded)


def test_when_both_classes_fail_research_neither_is_funded(monkeypatch):
    _sysm, funded = _book_with(monkeypatch, {"CLSA": "WATCHLIST", "CLSC": "WATCHLIST"})
    assert funded == {"OTHR"}, funded
