"""Plan Check: each check's PASS / WARN / BLOCK / SKIPPED boundary, overall
status, the evidence note, and the plan fingerprint."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from engine import plan_check as pc
from engine.levels import rr_triggers

TODAY = date(2026, 10, 28)  # a Wednesday
FAR_FOMC = [date(2026, 12, 9)]  # well outside the two-session window
FAR_EARNINGS = TODAY + timedelta(days=200)


def _run(**over):
    kw = dict(ticker="AAPL", shares=10.0, entry=100.0, stop=95.0, target=112.0,
              plan_date=TODAY, today=TODAY, account_value=50_000.0, cash=10_000.0,
              next_earnings=FAR_EARNINGS, fomc_dates=FAR_FOMC, research=None, holdings=None)
    kw.update(over)
    return pc.check_plan(**kw)


def _check(res, cid):
    return next(c for c in res["checks"] if c["id"] == cid)


def _status(res, cid):
    return _check(res, cid)["status"]


def _holdings(positions=None, account=50_000.0):
    return {"account_value": account, "positions": positions or {}}


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def test_geometry_blocks_out_of_order_prices():
    assert _status(_run(stop=101.0), "geometry") == "BLOCK"
    assert _status(_run(target=99.0), "geometry") == "BLOCK"


def test_geometry_blocks_a_stop_inside_the_noise_floor_and_passes_just_above():
    assert _status(_run(stop=99.96), "geometry") == "BLOCK"   # 0.04% of price
    assert _status(_run(stop=99.94), "geometry") == "PASS"    # 0.06% of price


def test_invalid_geometry_skips_the_dependent_checks():
    res = _run(stop=101.0)
    assert _status(res, "rr") == "SKIPPED"
    assert _status(res, "dollar_risk") == "SKIPPED"
    assert res["overall"] == "BLOCK"


# ---------------------------------------------------------------------------
# net R/R against the hurdles (Research 1.5, funding 1.8)
# ---------------------------------------------------------------------------

def test_rr_passes_at_or_above_the_funding_hurdle():
    res = _run(target=112.0)
    assert _status(res, "rr") == "PASS"
    cost = res["plan"]["cost_frac"]
    at = rr_triggers(100.0, 95.0, 112.0, cost, 1.8)["target_needed"]
    assert _status(_run(target=at + 1e-6), "rr") == "PASS"


def test_rr_warns_between_research_gate_and_funding_hurdle():
    res = _run(target=108.0)  # net R/R about 1.54
    assert _status(res, "rr") == "WARN"
    assert "1.8" in _check(res, "rr")["next_step"]


def test_rr_blocks_below_the_research_gate():
    res = _run(target=106.0)  # net R/R about 1.15
    assert _status(res, "rr") == "BLOCK"
    assert "1.5" in _check(res, "rr")["message"]


# ---------------------------------------------------------------------------
# dollar risk per trade (target 0.6%, hard cap 2%)
# ---------------------------------------------------------------------------

def test_dollar_risk_boundaries():
    # risk per share 5; account 50,000 -> 0.6% is 300 = 60 shares
    assert _status(_run(shares=60), "dollar_risk") == "PASS"
    assert _status(_run(shares=80), "dollar_risk") == "WARN"    # 0.8%
    assert _status(_run(shares=200), "dollar_risk") == "WARN"   # exactly 2% = 1,000
    assert _status(_run(shares=201), "dollar_risk") == "BLOCK"  # 2.01%


def test_dollar_risk_is_skipped_without_an_account_value():
    res = _run(account_value=None, holdings=None)
    assert _status(res, "dollar_risk") == "SKIPPED"


# ---------------------------------------------------------------------------
# position size vs the 10% cap, and vs cash
# ---------------------------------------------------------------------------

def test_position_cap_is_inclusive_at_ten_percent():
    assert _status(_run(shares=50, cash=None), "position_size") == "PASS"   # 5,000 = 10%
    assert _status(_run(shares=51, cash=None), "position_size") == "BLOCK"  # 5,100


def test_position_larger_than_cash_is_blocked():
    res = _run(shares=50, cash=4_000.0)
    assert _status(res, "position_size") == "BLOCK"
    assert "cash" in _check(res, "position_size")["message"]


def test_position_is_skipped_without_account_or_cash():
    assert _status(_run(account_value=None, cash=None), "position_size") == "SKIPPED"


# ---------------------------------------------------------------------------
# concentration against My Portfolio limits (single name 10%, sector 30%)
# ---------------------------------------------------------------------------

def test_concentration_skipped_when_not_connected():
    res = _run(holdings=None)
    assert _status(res, "concentration_name") == "SKIPPED"
    assert _status(res, "concentration_sector") == "SKIPPED"
    assert "Not connected" in _check(res, "concentration_name")["message"]


def test_single_name_limit_after_the_trade():
    h = _holdings({"AAPL": {"market_value": 3_000.0, "sector": "Technology"}})
    assert _status(_run(shares=20, holdings=h), "concentration_name") == "PASS"   # 5,000 = 10%
    assert _status(_run(shares=21, holdings=h), "concentration_name") == "BLOCK"  # 5,100


def test_sector_limit_after_the_trade():
    h = _holdings({"MSFT": {"market_value": 14_000.0, "sector": "Technology"}})
    assert _status(_run(shares=10, holdings=h, sector="Technology"), "concentration_sector") == "PASS"   # 30%
    assert _status(_run(shares=20, holdings=h, sector="Technology"), "concentration_sector") == "BLOCK"  # 32%


def test_unknown_sector_is_skipped_not_passed():
    h = _holdings({"MSFT": {"market_value": 14_000.0, "sector": "Technology"}})
    assert _status(_run(holdings=h, sector=None), "concentration_sector") == "SKIPPED"


def test_unknown_market_value_is_skipped_not_passed():
    h = _holdings({"AAPL": {"market_value": None, "sector": "Technology"}})
    assert _status(_run(holdings=h), "concentration_name") == "SKIPPED"


# ---------------------------------------------------------------------------
# earnings and FOMC timing
# ---------------------------------------------------------------------------

def test_earnings_inside_the_horizon_warns_outside_passes():
    assert _status(_run(next_earnings=TODAY + timedelta(days=30)), "earnings") == "WARN"
    assert _status(_run(next_earnings=TODAY + timedelta(days=200)), "earnings") == "PASS"


def test_earnings_unknown_or_stale_is_skipped():
    assert _status(_run(next_earnings=None), "earnings") == "SKIPPED"
    assert _status(_run(next_earnings=TODAY - timedelta(days=3)), "earnings") == "SKIPPED"


def test_fomc_within_two_sessions_warns():
    # Oct 28 (Wed) -> Oct 30 (Fri) is two sessions after today: WARN.
    assert _status(_run(fomc_dates=[date(2026, 10, 30)]), "fomc") == "WARN"
    assert _status(_run(fomc_dates=[TODAY]), "fomc") == "WARN"


def test_fomc_three_sessions_out_passes_and_empty_calendar_skips():
    # Nov 2 (Mon) is three sessions after Oct 28: PASS.
    assert _status(_run(fomc_dates=[date(2026, 11, 2)]), "fomc") == "PASS"
    assert _status(_run(fomc_dates=[]), "fomc") == "SKIPPED"


# ---------------------------------------------------------------------------
# QUANTA's own Decision, and the evidence note
# ---------------------------------------------------------------------------

def test_decision_skipped_without_a_cached_research_read():
    assert _status(_run(research=None), "decision") == "SKIPPED"


def test_decision_warns_on_avoid_and_names_the_blocker():
    res = _run(research={"action": "AVOID", "new_money": "AVOID",
                         "blockers": [{"text": "Risk grade F."}], "evidence": {"status": "NO_EDGE"}})
    assert _status(res, "decision") == "WARN"
    assert "Risk grade F." in _check(res, "decision")["message"]


def test_decision_warns_on_an_active_blocker_or_watch():
    assert _status(_run(research={"action": "BUY", "new_money": "ELIGIBLE",
                                  "blockers": [{"text": "Earnings within 3 days."}]}), "decision") == "WARN"
    assert _status(_run(research={"action": "BUY", "new_money": "WATCH", "blockers": []}), "decision") == "WARN"


def test_decision_passes_with_eligible_money_and_no_blocker():
    assert _status(_run(research={"action": "BUY", "new_money": "ELIGIBLE", "blockers": []}), "decision") == "PASS"


def test_evidence_note_appears_unless_validated():
    not_validated = _run(research={"action": "BUY", "new_money": "ELIGIBLE", "evidence": {"status": "NO_EDGE"}})
    assert pc.EVIDENCE_NOTE in not_validated["notes"]
    validated = _run(research={"action": "BUY", "new_money": "ELIGIBLE", "evidence": {"status": "VALIDATED"}})
    assert pc.EVIDENCE_NOTE not in validated["notes"]
    assert pc.EVIDENCE_NOTE in _run(research=None)["notes"]


def test_evidence_note_does_not_change_the_status():
    base = {"action": "BUY", "new_money": "ELIGIBLE", "blockers": []}
    no_edge = _run(research={**base, "evidence": {"status": "NO_EDGE"}})
    validated = _run(research={**base, "evidence": {"status": "VALIDATED"}})
    assert no_edge["overall"] == validated["overall"]
    assert _status(no_edge, "decision") == "PASS"


# ---------------------------------------------------------------------------
# overall status and the buy-only principle
# ---------------------------------------------------------------------------

def test_overall_is_the_worst_status():
    assert _run(stop=101.0)["overall"] == "BLOCK"
    assert _run(target=108.0)["overall"] == "WARN"


def test_overall_is_incomplete_when_a_check_was_skipped():
    res = _run(account_value=None, cash=None, next_earnings=None)
    assert res["overall"] == "SKIPPED"
    assert res["incomplete"] is True


def test_only_buy_plans_are_checked():
    with pytest.raises(ValueError):
        _run(side="sell")


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_sixteen_hex_characters_and_stable():
    fp = pc.plan_fingerprint("AAPL", 10, 100.0, 95.0, 112.0, "2026-10-28")
    assert re.fullmatch(r"[0-9a-f]{16}", fp)
    assert fp == pc.plan_fingerprint("AAPL", 10, 100.0, 95.0, 112.0, "2026-10-28")


def test_fingerprint_ignores_case_padding_and_number_types():
    base = pc.plan_fingerprint("AAPL", 10, 100.0, 95.0, 112.0, "2026-10-28")
    assert pc.plan_fingerprint(" aapl ", 10.0, 100, 95, 112.0, "2026-10-28") == base


def test_fingerprint_changes_when_any_field_changes():
    base = pc.plan_fingerprint("AAPL", 10, 100.0, 95.0, 112.0, "2026-10-28")
    assert pc.plan_fingerprint("AAPL", 11, 100.0, 95.0, 112.0, "2026-10-28") != base
    assert pc.plan_fingerprint("AAPL", 10, 100.0, 95.0, 112.0, "2026-10-29") != base
    assert pc.plan_fingerprint("MSFT", 10, 100.0, 95.0, 112.0, "2026-10-28") != base


def test_result_carries_the_plan_fingerprint():
    res = _run()
    assert res["fingerprint"] == pc.plan_fingerprint("AAPL", 10.0, 100.0, 95.0, 112.0, TODAY)
    assert res["checks"] and all({"id", "status", "message", "next_step"} <= set(c) for c in res["checks"])


# ---------------------------------------------------------------------------
# material_filings: a recent HIGH 8-K warns; none passes; unknown is SKIPPED
# ---------------------------------------------------------------------------

def _high_event(filed, code="4.02", title="Non-Reliance on Previously Issued Financial Statements", sev="HIGH"):
    return {"date": filed.isoformat(), "max_severity": sev,
            "items": [{"code": code, "title": title, "severity": sev}]}


def test_material_filings_not_requested_is_skipped_not_passed():
    res = _run()
    assert _status(res, "material_filings") == "SKIPPED"
    assert "not checked" in _check(res, "material_filings")["message"]


def test_recent_high_8k_warns_and_names_the_item_and_date():
    filed = TODAY - timedelta(days=5)
    res = _run(material_filings={"status": "ok", "events": [_high_event(filed)]})
    check = _check(res, "material_filings")
    assert check["status"] == "WARN"
    assert filed.isoformat() in check["message"]
    assert "Item 4.02" in check["message"]
    assert check["next_step"] == "Read the filing before you buy."
    assert res["overall"] == "WARN"


def test_high_8k_older_than_thirty_days_passes():
    filed = TODAY - timedelta(days=31)
    res = _run(material_filings={"status": "ok", "events": [_high_event(filed)]})
    assert _status(res, "material_filings") == "PASS"


def test_a_medium_8k_does_not_warn():
    filed = TODAY - timedelta(days=2)
    ev = _high_event(filed, code="2.02", title="Results of Operations and Financial Condition", sev="MEDIUM")
    assert _status(_run(material_filings={"status": "ok", "events": [ev]}), "material_filings") == "PASS"


def test_no_filings_at_all_passes_with_the_window_stated():
    res = _run(material_filings={"status": "ok", "events": []})
    assert _status(res, "material_filings") == "PASS"
    assert "30 days" in _check(res, "material_filings")["message"]


def test_several_recent_highs_name_the_latest_and_count_the_rest():
    newer = TODAY - timedelta(days=1)
    older = TODAY - timedelta(days=9)
    res = _run(material_filings={"status": "ok", "events": [_high_event(older), _high_event(newer)]})
    msg = _check(res, "material_filings")["message"]
    assert newer.isoformat() in msg and "1 more" in msg


def test_india_or_not_covered_is_skipped_with_the_plain_reason():
    res = _run(material_filings={"status": "not_covered",
                                 "message": "Not covered: SEC filings exist only for US-registered issuers."})
    check = _check(res, "material_filings")
    assert check["status"] == "SKIPPED"
    assert "Not covered" in check["message"]
    assert res["incomplete"] is True


def test_sec_unavailable_is_skipped_never_a_pass():
    res = _run(material_filings={"status": "unavailable", "message": "SEC EDGAR could not be read: rate-limited."})
    check = _check(res, "material_filings")
    assert check["status"] == "SKIPPED"
    assert "unavailable" in check["message"]
    assert check["status"] != "PASS"
