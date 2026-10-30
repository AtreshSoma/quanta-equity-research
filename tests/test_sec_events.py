"""Material filings: item tagging, severity tiers, reaction sessions, coverage and malformed SEC rows.

No network: the EDGAR client is a fake with the same ``recent_filings`` contract."""
from __future__ import annotations

from datetime import date

import pytest

from engine import sec_events as se

CIK = 320193
ACC = "0000320193-26-000042"


class FakeEdgar:
    def __init__(self, res=None, raises=None):
        self.res = res
        self.raises = raises
        self.calls = []

    def recent_filings(self, symbol, since, forms=None):
        self.calls.append((symbol, since, forms))
        if self.raises:
            raise self.raises
        return self.res


def _row(**over):
    row = {"form": "8-K", "filed": "2026-10-20", "accepted": "2026-10-20T20:05:00.000Z",
           "items": ["2.02", "9.01"], "accession": ACC, "url": None}
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# Item dictionary and tiers
# ---------------------------------------------------------------------------

HIGH_CODES = ["1.03", "2.04", "2.06", "3.01", "4.01", "4.02", "5.01"]
MEDIUM_CODES = ["1.01", "1.02", "2.01", "2.02", "2.03", "2.05", "3.02", "3.03", "5.02", "5.03"]
LOW_CODES = ["5.07", "7.01", "8.01", "9.01"]


@pytest.mark.parametrize("code", HIGH_CODES)
def test_high_tier_items(code):
    assert se.tag_item(code)["severity"] == "HIGH"


@pytest.mark.parametrize("code", MEDIUM_CODES)
def test_medium_tier_items(code):
    assert se.tag_item(code)["severity"] == "MEDIUM"


@pytest.mark.parametrize("code", LOW_CODES)
def test_low_tier_items(code):
    assert se.tag_item(code)["severity"] == "LOW"


def test_every_item_has_title_why_and_a_known_tier():
    for code, entry in se.ITEM_DICTIONARY.items():
        assert entry["title"] and entry["why"], code
        assert entry["severity"] in ("HIGH", "MEDIUM", "LOW"), code


def test_known_titles_match_the_sec_form():
    assert se.tag_item("2.02")["title"] == "Results of Operations and Financial Condition"
    assert se.tag_item("4.02")["title"].startswith("Non-Reliance on Previously Issued Financial Statements")
    assert se.tag_item("5.02")["title"].startswith("Departure of Directors or Certain Officers")


def test_unknown_well_formed_code_is_not_rated_not_fine():
    item = se.tag_item("6.05")
    assert item["severity"] == "UNRATED"
    assert "not in QUANTA's item list" in item["title"]


def test_malformed_codes_are_dropped_and_counted():
    items, dropped = se.tag_items(["2.02", "2.2", "", None, "../x", "5.02"])
    assert [i["code"] for i in items] == ["2.02", "5.02"]
    assert dropped == 4


def test_duplicate_codes_collapse_to_one_item():
    items, dropped = se.tag_items(["2.02", "2.02"])
    assert len(items) == 1 and dropped == 0


# ---------------------------------------------------------------------------
# Multi-item attribution and max severity
# ---------------------------------------------------------------------------

def test_multi_item_filing_is_attributed_to_the_highest_information_item_not_9_01():
    items, _ = se.tag_items(["7.01", "2.02", "9.01"])
    prim = se.primary_item(items)
    assert prim["code"] == "2.02"
    assert se.max_severity(items) == "MEDIUM"


def test_ties_go_to_the_lower_item_number():
    items, _ = se.tag_items(["5.02", "2.02"])
    assert se.primary_item(items)["code"] == "2.02"


def test_a_high_item_outranks_a_medium_one_regardless_of_order():
    items, _ = se.tag_items(["2.02", "4.02"])
    assert se.primary_item(items)["code"] == "4.02"
    assert se.max_severity(items) == "HIGH"


def test_exhibit_only_filing_is_attributed_to_9_01():
    items, _ = se.tag_items(["9.01"])
    assert se.primary_item(items)["code"] == "9.01"


def test_no_items_has_no_primary_and_unrated_severity():
    assert se.primary_item([]) is None
    assert se.max_severity([]) == "UNRATED"


def test_normalize_row_records_primary_and_co_filed():
    ev = se.normalize_row(_row(items=["2.02", "7.01", "9.01"]), CIK)
    assert ev["primary_item"] == "2.02"
    assert ev["co_filed"] == ["7.01", "9.01"]
    assert ev["max_severity"] == "MEDIUM"


def test_8k_a_is_accepted_as_a_form():
    ev = se.normalize_row(_row(form="8-K/A", items="2.02,9.01"), CIK)
    assert ev["form"] == "8-K/A"
    assert [i["code"] for i in ev["items"]] == ["2.02", "9.01"]


def test_items_may_arrive_as_a_comma_string():
    ev = se.normalize_row(_row(items="5.02, 9.01"), CIK)
    assert ev["primary_item"] == "5.02"


# ---------------------------------------------------------------------------
# Reaction session
# ---------------------------------------------------------------------------

def test_after_close_filing_reacts_the_next_session():
    # 2026-10-28 20:05 UTC is 16:05 EDT (after the close).
    day, note, ny = se.reaction_session(date(2026, 10, 28), "2026-10-28T20:05:00.000Z")
    assert day == date(2026, 10, 29)
    assert "after" in note.lower()
    assert ny.startswith("2026-10-28T16:05")


def test_exactly_the_close_counts_as_after_the_close():
    day, _, _ = se.reaction_session(date(2026, 10, 28), "2026-10-28T20:00:00.000Z")
    assert day == date(2026, 10, 29)


def test_pre_open_filing_reacts_the_same_day():
    # 12:30 UTC is 08:30 EDT.
    day, note, _ = se.reaction_session(date(2026, 10, 28), "2026-10-28T12:30:00.000Z")
    assert day == date(2026, 10, 28)
    assert "trading hours" in note


def test_winter_after_close_uses_est():
    # 21:30 UTC in January is 16:30 EST.
    day, _, _ = se.reaction_session(date(2026, 1, 15), "2026-01-15T21:30:00.000Z")
    assert day == date(2026, 1, 16)


def test_weekend_filing_reacts_the_monday():
    # Saturday 2026-10-24 at 10:00 UTC.
    day, note, _ = se.reaction_session(date(2026, 10, 24), "2026-10-24T14:00:00.000Z")
    assert day == date(2026, 10, 26)
    assert "non-trading day" in note


def test_holiday_filing_reacts_the_next_trading_session():
    # Independence Day 2026 falls on a Saturday; NYSE closes Friday 2026-07-03.
    day, _, _ = se.reaction_session(date(2026, 7, 3), "2026-07-03T14:00:00.000Z")
    assert day == date(2026, 7, 6)


def test_missing_acceptance_uses_the_filing_day_and_says_so():
    day, note, ny = se.reaction_session(date(2026, 10, 28), None)
    assert day == date(2026, 10, 28)
    assert ny is None
    assert "No acceptance time" in note


def test_unreadable_acceptance_is_treated_as_missing():
    assert se.parse_accepted("not a time") is None
    day, _, _ = se.reaction_session(date(2026, 10, 28), "not a time")
    assert day == date(2026, 10, 28)


# ---------------------------------------------------------------------------
# Coverage, URLs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["AAPL", "SPY", "BRK-B", "msft"])
def test_us_symbols_are_covered(symbol):
    assert se.covered(symbol)


@pytest.mark.parametrize("symbol", ["RELIANCE.NS", "TCS.BO", "BTC-USD", "^GSPC", "VOD.L", "", None])
def test_indian_and_other_non_sec_symbols_are_not_covered(symbol):
    assert not se.covered(symbol)


def test_accession_index_url_is_built_from_cik_and_accession():
    assert se.accession_index_url(CIK, ACC) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000042/0000320193-26-000042-index.htm")


def test_accession_index_url_refuses_malformed_input():
    assert se.accession_index_url(CIK, "../../etc") is None
    assert se.accession_index_url("x", ACC) is None


# ---------------------------------------------------------------------------
# Loading: coverage, errors, malformed payloads
# ---------------------------------------------------------------------------

def test_india_is_not_covered_and_never_calls_sec():
    edgar = FakeEdgar({"status": "ok", "filings": []})
    out = se.load_events("RELIANCE.NS", date(2021, 1, 1), date(2026, 10, 28), edgar)
    assert out["status"] == "not_covered"
    assert "SEC filings exist only for US-registered issuers" in out["message"]
    assert edgar.calls == []


def test_ok_with_no_filings_is_ok_not_unavailable():
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28), FakeEdgar({"status": "ok", "filings": []}))
    assert out["status"] == "ok" and out["events"] == []


def test_events_are_newest_first_and_form_filter_is_passed():
    edgar = FakeEdgar({"status": "ok", "cik": CIK, "filings": [
        _row(filed="2026-09-01", accession="0000320193-26-000001"),
        _row(filed="2026-10-20"),
    ]})
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28), edgar)
    assert [e["date"] for e in out["events"]] == ["2026-10-20", "2026-09-01"]
    assert edgar.calls[0][2] == se.FORMS


def test_malformed_rows_are_counted_and_do_not_break_the_list():
    edgar = FakeEdgar({"status": "ok", "cik": CIK, "filings": [
        "not a dict",
        _row(form="10-K"),
        _row(filed="20-10-2026"),
        _row(items=12345),
        _row(),
    ]})
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28), edgar)
    assert out["status"] == "ok"
    assert len(out["events"]) == 2  # the 12345 items row survives with no items
    assert out["malformed_rows"] == 3
    assert any(e["items"] == [] and e["max_severity"] == "UNRATED" for e in out["events"])


def test_non_list_filing_payload_is_unavailable_not_empty():
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28),
                         FakeEdgar({"status": "ok", "cik": CIK, "filings": {"oops": 1}}))
    assert out["status"] == "unavailable"
    assert out["reason"] == "malformed"


def test_sec_error_is_unavailable_with_the_reason_label():
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28),
                         FakeEdgar({"status": "error", "reason": "rate_limited", "filings": []}))
    assert out["status"] == "unavailable"
    assert out["reason"] == "rate_limited"
    assert "rate-limited" in out["message"]


def test_missing_contact_names_the_setting():
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28),
                         FakeEdgar({"status": "error", "reason": "contact_missing", "filings": []}))
    assert out["status"] == "unavailable"
    assert "QUANTA_SEC_CONTACT" in out["fix"]


def test_no_registrant_is_unavailable_not_an_empty_list():
    out = se.load_events("ZZZZ", date(2026, 1, 1), date(2026, 10, 28), FakeEdgar({"status": "no_cik", "filings": []}))
    assert out["status"] == "unavailable"
    assert out["reason"] == "no_registrant"


def test_client_exception_is_unavailable_not_a_crash():
    out = se.load_events("AAPL", date(2026, 1, 1), date(2026, 10, 28), FakeEdgar(raises=OSError("boom")))
    assert out["status"] == "unavailable"


def test_within_days_filters_both_ends():
    events = [{"date": "2026-10-28"}, {"date": "2026-09-01"}, {"date": "2026-07-01"}, {"date": "bad"}]
    kept = se.within_days(events, 30, date(2026, 10, 28))
    assert [e["date"] for e in kept] == ["2026-10-28"]


def test_plan_window_reads_the_last_30_days_only():
    edgar = FakeEdgar({"status": "ok", "cik": CIK, "filings": [
        _row(filed="2026-10-20", items=["4.02"]),
        _row(filed="2026-08-01", accession="0000320193-26-000002", items=["4.02"]),
    ]})
    out = se.plan_window("AAPL", date(2026, 10, 28), edgar)
    assert out["status"] == "ok"
    assert [e["date"] for e in out["events"]] == ["2026-10-20"]
    assert out["events"][0]["max_severity"] == "HIGH"


def test_plan_window_india_is_not_covered():
    out = se.plan_window("TCS.BO", date(2026, 10, 28), FakeEdgar({"status": "ok", "filings": []}))
    assert out["status"] == "not_covered"
    assert out["events"] == []
