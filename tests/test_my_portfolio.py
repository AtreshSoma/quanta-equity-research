"""My Portfolio: read-only broker holdings -> authoritative Decision -> portfolio context.

These tests pin the contract with fakes. They prove the logic, the security
guards and the failure handling; they are NOT evidence that the live Robinhood
MCP integration works (that needs a real, signed-in account).
"""
from __future__ import annotations

import io
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

from brokers import mcp_http, oauth, security  # noqa: E402
from brokers.mcp_http import McpHttpClient, ToolNotPermitted, is_write_tool  # noqa: E402
from brokers.redact import RedactingFilter, redact, register_sensitive  # noqa: E402
from brokers.robinhood import READ_TOOLS, RobinhoodProvider  # noqa: E402
from portfolio import analytics as an  # noqa: E402
from portfolio import guidance as gd  # noqa: E402
from portfolio.execution import EXECUTION_ENABLED, ExecutionDisabled, ExecutionGateway, OrderIntent  # noqa: E402
from portfolio.models import AccountSummary, ConnectionState, Portfolio, Position  # noqa: E402
from portfolio.provider import AuthExpired, ProviderError  # noqa: E402
from portfolio.service import PortfolioService, build_view, quote_freshness  # noqa: E402

ACCOUNT_NUMBER = "5QR98260417"
ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJxdWFudGEtdGVzdCJ9.c2lnbmF0dXJlLXZhbHVlLXg"
REFRESH_TOKEN = "rt_7f3a9c1d2e4b5a6978"


# ---------------------------------------------------------------------------
# Decision fixtures (shape of engine.decision.Decision.to_dict())
# ---------------------------------------------------------------------------

def decision(action="BUY", score=68.0, stance=None, new_money=None, holder=None,
             blockers=(), freshness="fresh", stop=90.0, evidence="UNTESTED"):
    """``holder`` defaults to what the engine's own holder rule publishes."""
    if holder is None:
        from engine.blockers import holder_action
        from engine.types import FinalAction
        holder = holder_action(FinalAction(action), score, [b["code"] for b in blockers])
    stance = stance or {"BUY": "BULLISH", "STRONG_BUY": "BULLISH", "ACCUMULATE": "BULLISH",
                        "AVOID": "BEARISH", "INVALID": "NONE"}.get(action, "NEUTRAL")
    new_money = new_money or {"BUY": "ELIGIBLE", "STRONG_BUY": "ELIGIBLE", "ACCUMULATE": "ELIGIBLE",
                              "AVOID": "AVOID", "INVALID": "NONE"}.get(action, "WATCH")
    return {
        "action": action, "stance": stance, "model_stance": stance, "new_money": new_money,
        "holder_action": holder, "score": score, "risk_grade": "B",
        "horizon": {"key": "position", "trading_days": 63, "label": "Position (~3 months)"},
        "blockers": [dict(b) for b in blockers],
        "reasons": ["Earnings momentum and margin expansion"], "risks": ["Valuation is rich"],
        "invalidation": ["A daily close below the stop"],
        "levels": {"stop": stop, "reference_price": 100.0, "valuation_target": 120.0},
        "red_flags": [],
        "evidence": {"status": evidence, "status_text": "Never validated", "permits_recommendation": False},
        "qualifier": "Unproven model", "caveat": "Treat as research.",
        "freshness": {"status": freshness}, "sizing": {"position_value": 5000.0},
    }


TARGET_BELOW = {"code": "TARGET_BELOW_ENTRY", "text": "12-month valuation target is at or below the entry",
                "remedy": "A valuation target above the entry price"}
RISK_F = {"code": "RISK_GRADE_F", "text": "Risk grade F (very high volatility/drawdown) allows no new money",
          "remedy": "Risk grade improving to D or better"}


def pctx(symbol="AAA", weight=0.05, sector="Technology", sector_weight=0.2, **kw):
    base = dict(symbol=symbol, quantity=10, value=weight * 100_000, weight=weight, price=100.0,
                average_cost=80.0, unrealized_pnl=200.0, unrealized_pnl_pct=25.0,
                sector=sector, sector_weight=sector_weight)
    base.update(kw)
    return gd.PositionContext(**base)


def ctx(**kw):
    base = dict(total_value=100_000, buying_power=10_000, stop_risk_pct=0.02, today=date(2026, 9, 28))
    base.update(kw)
    return gd.PortfolioContext(**base)


# ---------------------------------------------------------------------------
# Portfolio-context guidance
# ---------------------------------------------------------------------------

class TestGuidance:
    def test_bullish_within_limits_is_accumulate(self):
        g = gd.guide_position(pctx(), ctx(), decision())
        assert (g.stance, g.new_money, g.existing, g.action) == ("Bullish", "Accumulate", "Hold", "Accumulate")
        assert g.group == "consider_adding"

    def test_bullish_but_overweight_holds_and_blocks_adding(self):
        """The critical distinction: a stock-level BUY never becomes Accumulate by itself."""
        g = gd.guide_position(pctx(weight=0.14), ctx(), decision())
        assert g.stance == "Bullish"
        assert g.new_money == "No New Money"
        assert g.action == "Hold" and g.group == "hold"
        assert g.adding["status"] == "blocked" and g.adding["code"] == "POSITION_AT_CAP"
        assert g.basis["code"] == "POSITION_AT_CAP" and "over the 10% single-name limit" in g.key_reason
        assert g.blocks[0]["code"] == "POSITION_AT_CAP" and g.blocks[0]["source"] == "portfolio"
        assert any("below 10%" in c for c in g.would_change)

    @pytest.mark.parametrize("kw,code", [
        ({"sector_weight": 0.35}, "SECTOR_AT_CAP"),
        ({"earnings_date": "2026-10-01"}, "EARNINGS_SOON"),
        ({"correlated_with": ["BBB", "CCC"], "cluster_weight": 0.33}, "CORRELATED_CLUSTER"),
    ])
    def test_each_portfolio_constraint_holds_back_new_money(self, kw, code):
        g = gd.guide_position(pctx(**kw), ctx(), decision())
        assert g.action == "Hold" and g.adding["status"] == "blocked"
        assert code in [b["code"] for b in g.blocks]

    @pytest.mark.parametrize("kw,code", [
        ({"weight": 0.117}, "POSITION_AT_CAP"),
        ({"sector_weight": 0.467}, "SECTOR_AT_CAP"),
    ])
    def test_limits_are_shown_on_holdings_the_engine_would_not_buy(self, kw, code):
        """Audit F4: a holding over the 10% cap was explained by its reward/risk instead."""
        dec = decision("WATCHLIST", 58, new_money="WATCH", blockers=[{"code": "LOW_REWARD_RISK", "text": "", "remedy": ""}])
        dec["primary_blocker"] = {"code": "LOW_REWARD_RISK"}
        g = gd.guide_position(pctx(**kw), ctx(), dec)
        assert g.action == "Hold" and g.basis["code"] == code
        assert g.adding["code"] == code
        codes = [b["code"] for b in g.blocks]
        assert codes.index(code) < codes.index("LOW_REWARD_RISK")

    def test_funds_never_count_toward_a_sector_limit(self):
        g = gd.guide_position(pctx(sector="Large Blend", sector_weight=0.5, is_fund=True), ctx(),
                              decision("WATCHLIST", 52, new_money="WATCH"))
        assert "SECTOR_AT_CAP" not in [b["code"] for b in g.blocks]
        assert g.basis["code"] == "FUND_HOLD" and "does not rate funds" in g.key_reason
        assert g.adding["code"] == "FUND_NOT_RATED"

    def test_unknown_buying_power_fails_closed(self):
        g = gd.guide_position(pctx(), ctx(buying_power=None), decision())
        assert g.new_money == "No New Money" and g.action == "Hold"
        assert "BUYING_POWER_UNKNOWN" in [b["code"] for b in g.blocks]
        assert g.adding["code"] == "BUYING_POWER_UNKNOWN"

    def test_no_buying_power_and_full_risk_budget(self):
        g = gd.guide_position(pctx(), ctx(buying_power=10, stop_risk_pct=0.07), decision())
        codes = [b["code"] for b in g.blocks]
        assert "NO_BUYING_POWER" in codes and "RISK_BUDGET_FULL" in codes

    def test_holder_trim_and_exit_come_from_the_decision(self):
        assert gd.guide_position(pctx(), ctx(), decision("AVOID", 40, holder="TRIM")).action == "Trim"
        g = gd.guide_position(pctx(), ctx(), decision("AVOID", 30, holder="EXIT", blockers=[TARGET_BELOW]))
        assert g.action == "Exit Review" and g.group == "exit_review"
        assert "not an instruction to sell" in g.why[0]
        assert g.key_reason.startswith("QUANTA score 30 is below 35, with a measured red flag: QUANTA's 12-month valuation")

    def test_exit_review_from_red_flags_measured_at_any_score(self):
        """Audit F2: red flags were only recorded in a buy band, so an exit could never fire."""
        from engine.blockers import holder_action
        from engine.types import FinalAction
        flags = ["HEAVY_INSIDER_SELLING", "DCF_OVERVALUATION"]
        dec = decision("AVOID", 30, holder=holder_action(FinalAction.AVOID, 30, [], flags))
        dec["red_flags"] = flags
        assert dec["holder_action"] == "EXIT"
        g = gd.guide_position(pctx(), ctx(), dec)
        assert g.action == "Exit Review" and g.status == "ok"
        assert g.red_flags == sorted(flags)

    def test_a_hold_discloses_red_flags_instead_of_a_clean_bill(self):
        dec = decision("WATCHLIST", 54.0, new_money="WATCH")
        dec["red_flags"] = ["TARGET_BELOW_ENTRY"]
        g = gd.guide_position(pctx(), ctx(), dec)
        assert g.action == "Hold" and g.basis["code"] == "TARGET_BELOW_ENTRY"
        assert g.key_reason == "QUANTA's 12-month valuation target is below the price"
        assert not any("Nothing measured argues" in w for w in g.why)

    def test_an_analysis_without_the_red_flag_check_does_not_claim_none(self):
        dec = decision("WATCHLIST", 50, new_money="WATCH")
        del dec["red_flags"]
        g = gd.guide_position(pctx(), ctx(), dec)
        assert "not checked" in g.key_reason and "No measured red flag" not in g.key_reason

    # --- the reason is the rule that fired, never an assumed score band ---

    def test_risk_grade_f_review_cites_risk_size_and_risk_share(self):
        """The screenshot bug: scores 68-72 were explained as 'in the reduce band (35 to 45)'."""
        for score in (68.0, 70.0, 72.0):
            dec = decision("WATCHLIST", score, stance="NEUTRAL", new_money="WATCH", blockers=[RISK_F])
            assert dec["holder_action"] == "TRIM"
            g = gd.guide_position(pctx(weight=0.023, risk_share=0.059), ctx(), dec)
            assert g.action == "Review Risk" and g.group == "risk_review"
            assert g.basis["code"] == "RISK_GRADE_F" and g.basis["kind"] == "risk"
            assert "Risk grade F" in g.key_reason and "2.3% of the account" in g.key_reason
            assert "5.9% of portfolio risk" in g.key_reason
            assert "reduce band" not in g.key_reason and "35 to 45" not in " ".join(g.why)
            assert "not a negative view" in g.why[0]
            assert not any("score recovering" in w for w in g.would_change)

    def test_grade_f_past_the_review_level_is_a_size_trim(self):
        dec = decision("WATCHLIST", 60, stance="NEUTRAL", new_money="WATCH", blockers=[RISK_F])
        g = gd.guide_position(pctx(weight=0.22), ctx(), dec)
        assert g.action == "Trim" and g.basis["code"] == "CONCENTRATION"
        assert any("risk grade is F" in w for w in g.why)

    def test_key_reason_is_the_binding_rule_not_the_first_listed(self):
        """Audit F1: the reason named a candle-timing trim while low reward/risk was the rule that blocked."""
        from engine.blockers import blocker_info
        dec = decision("WATCHLIST", 67.0, new_money="WATCH",
                       blockers=[blocker_info(c) for c in ("BUY_NOW_NO", "BEARISH_REVERSAL", "LOW_REWARD_RISK")])
        dec["primary_blocker"] = blocker_info("LOW_REWARD_RISK")
        dec["levels"].update(reward_risk=0.15, stop=91.0, valuation_target=105.1)
        g = gd.guide_position(pctx(), ctx(), dec)
        assert g.adding["code"] == "LOW_REWARD_RISK"
        assert "passing needs about 54% of 12-month upside" in g.adding["text"]
        assert "QUANTA's 12-month target is +5%" in g.adding["text"]
        assert "BUY_NOW_NO" in [n["code"] for n in g.notes]
        assert "BUY_NOW_NO" not in [b["code"] for b in g.blocks]
        dec2 = decision("WATCHLIST", 64.0, new_money="WATCH",
                        blockers=[blocker_info(c) for c in ("RISK_GRADE_D_CAP", "LOW_REWARD_RISK")])
        g2 = gd.guide_position(pctx(), ctx(), dec2)          # no primary_blocker: first BLOCKING code
        assert g2.adding["code"] == "LOW_REWARD_RISK"

    @pytest.mark.parametrize("dec_update,phrase", [
        ({"blocking_codes": ["PRICE_HISTORY_TRUNCATED"]}, "six months of history"),
        ({"history_range": "6mo/1d"}, "six months of history"),
        ({"levels": {"stop": 90.0, "reference_price": 100.0, "valuation_target": None}}, "valuation inputs"),
    ])
    def test_degraded_data_is_a_no_call_not_a_changed_call(self, dec_update, phrase):
        """Audit F3: a 6-month fallback history and a missing valuation target changed holders' calls."""
        dec = dict(decision("WATCHLIST", 60, new_money="WATCH"), **dec_update)
        c = ctx(last_calls={"AAA": {"date": "2026-10-19", "action": "Trim", "basis_code": "RISK_GRADE_F"}})
        g = gd.guide_position(pctx(), c, dec)
        assert g.status == "unavailable" and phrase in g.key_reason
        assert any("Review risk" in w for w in g.why)

    def test_a_fund_or_an_event_anchored_name_without_valuation_still_gets_a_call(self):
        dec = dict(decision("WATCHLIST", 50, new_money="WATCH"),
                   levels={"stop": 90.0, "reference_price": 100.0, "valuation_target": None})
        assert gd.guide_position(pctx(is_fund=True), ctx(), dec).status == "ok"

    @pytest.mark.parametrize("score,codes,action,basis,text", [
        (45.0, [], "Hold", "HOLD_CLEAR", "No measured red flag"),
        (44.9, [], "Trim", "SCORE_REDUCE_BAND", "is in the reduce band (35 to 45)"),
        (35.0, [], "Trim", "SCORE_REDUCE_BAND", "is in the reduce band (35 to 45)"),
        (34.9, [], "Trim", "SCORE_REDUCE_BAND", "below 35; no measured red flag confirms an exit"),
        (34.9, ["TARGET_BELOW_ENTRY"], "Exit Review", "SCORE_EXIT_CORROBORATED", "below 35, with a measured red flag"),
        (35.0, ["TARGET_BELOW_ENTRY"], "Trim", "SCORE_REDUCE_BAND", "is in the reduce band (35 to 45)"),
    ])
    def test_score_band_boundaries_match_action_and_reason(self, score, codes, action, basis, text):
        from engine.blockers import blocker_info
        dec = decision("AVOID" if score < 45 else "WATCHLIST", score, blockers=[blocker_info(c) for c in codes])
        g = gd.guide_position(pctx(), ctx(), dec)
        assert (g.action, g.basis["code"]) == (action, basis)
        assert text in g.key_reason and g.key_reason == g.basis["text"]

    def test_holder_action_that_contradicts_its_own_score_is_not_a_call(self):
        dec = decision("WATCHLIST", 70.0, stance="NEUTRAL", new_money="WATCH", holder="TRIM")
        g = gd.guide_position(pctx(), ctx(), dec)
        assert g.status == "unavailable" and g.action == "Unavailable"
        assert "does not follow" in g.key_reason and g.basis["kind"] == "missing_evidence"

    def test_data_limitations_are_not_read_as_a_view_on_the_stock(self):
        c = ctx()
        c.incomplete_reasons.append(gd.ACCOUNT_UNRECONCILED)
        g = gd.guide_position(pctx(), c, decision())
        assert g.action == "Hold" and g.basis["kind"] not in gd.LIMITATION_KINDS
        assert g.adding == {"status": "blocked", "code": "ACCOUNT_DATA_UNRECONCILED", "kind": "account_data",
                            "text": "Account data has not fully reconciled",
                            "remedy": "Positions plus cash reconciling with the account total"}
        missing = gd.guide_position(pctx(), ctx(), decision("WATCHLIST", 60, new_money="WATCH", blockers=[{
            "code": "INVALID_TARGET", "text": "", "remedy": ""}]))
        assert missing.adding["kind"] == "missing_evidence"
        assert missing.adding["text"].startswith("No valuation target")

    def test_every_engine_rule_code_has_a_sentence(self):
        """An unmapped code used to reach users as 'Risk grade d hold'."""
        from engine.blockers import BLOCKERS
        assert BLOCKERS["RISK_GRADE_D_HOLD"][1] == "Risk grade D limits new money to a reduced Accumulate size"
        g = gd.guide_position(pctx(), ctx(), decision("WATCHLIST", 61, new_money="WATCH", blockers=[{
            "code": "RISK_GRADE_D_HOLD", "text": "Risk grade d hold", "remedy": ""}]))
        assert g.notes[0]["text"] == "Risk grade D limits new money to a reduced Accumulate size"
        for code in ("EVENT_CHECK_FOREIGN_FILER", "PRICE_HISTORY_TRUNCATED"):
            assert BLOCKERS[code][0] == "missing_data" and gd.reason_kind(code) == "missing_evidence"

    def test_old_journal_actions_read_under_the_current_vocabulary(self):
        assert gd.canonical_action("Hold / No New Money") == "Hold"
        assert gd.canonical_action("Trim", "RISK_GRADE_F") == "Review Risk"
        assert gd.canonical_action("Trim", "CONCENTRATION") == "Trim"

    def test_attention_names_sector_grade_f_group_earnings_and_over_cap(self):
        rows = [
            {"symbol": "AAA", "broker": {"weight": 0.25}, "market": {"earnings_date": "2026-11-05"},
             "guidance": {"action": "Trim", "basis": {"code": "CONCENTRATION"}}, "risk_share": 0.21},
            {"symbol": "BBB", "broker": {"weight": 0.12}, "market": {"earnings_date": "2026-10-27"},
             "guidance": {"action": "Hold", "basis": {}}, "risk_share": 0.15},
            {"symbol": "CCC", "broker": {"weight": 0.03}, "market": {"earnings_date": "2026-10-21"},
             "guidance": {"action": "Review Risk", "basis": {"code": "RISK_GRADE_F"}}, "risk_share": 0.06},
            {"symbol": "DDD", "broker": {"weight": 0.04}, "market": {},
             "guidance": {"action": "Review Risk", "basis": {"code": "RISK_GRADE_F"}}, "risk_share": 0.05},
        ]
        metrics = {"sectors": [{"sector": "Technology", "weight": 0.48, "symbols": ["AAA", "CCC", "DDD"]},
                               {"sector": "Large Blend", "weight": 0.5, "symbols": ["FFF"], "fund": True}]}
        items = gd.attention_items(rows, metrics, an.PortfolioPolicy(), date(2026, 10, 21))
        assert [i["code"] for i in items] == ["SECTOR_OVER_LIMIT", "RISK_GRADE_F_GROUP", "EARNINGS_SOON"]
        assert items[0]["text"] == "Technology is 48.0% of the account (QUANTA limit 30%)"
        assert items[1]["text"] == ("Holdings under a grade-F risk review (CCC and DDD) are 7.0% of the account "
                                    "and 11.0% of portfolio risk")
        assert items[2]["text"].startswith("Earnings: CCC today; BBB within 7 days")

    @pytest.mark.parametrize("grade,last,action", [
        ("D", {"action": "Review Risk", "basis_code": "RISK_GRADE_F"}, "Review Risk"),
        ("D", {"action": "Trim", "basis_code": "RISK_GRADE_F"}, "Review Risk"),     # pre-rename journal entry
        ("C", {"action": "Review Risk", "basis_code": "RISK_GRADE_F"}, "Hold"),     # C or better closes it
        ("D", {"action": "Hold", "basis_code": "HOLD_CLEAR"}, "Hold"),              # D alone never opens one
        ("D", {}, "Hold"),
    ])
    def test_a_grade_f_review_stays_open_at_d_and_closes_at_c(self, grade, last, action):
        dec = dict(decision("WATCHLIST", 60, new_money="WATCH"), risk_grade=grade)
        g = gd.guide_position(pctx(weight=0.03), ctx(last_calls={"AAA": last} if last else {}), dec)
        assert g.action == action
        if action == "Review Risk":
            assert g.basis["code"] == "RISK_REVIEW_HELD" and "until grade C or better" in g.key_reason

    def test_conditional_target_only_where_a_rule_defines_one(self):
        g = gd.guide_position(pctx(weight=0.24), ctx(), decision("WATCHLIST", 50),
                              impacts={"trim_to_cap": {"weight_after": 0.10}})
        assert g.target["weight"] == 0.10 and "not a personal target" in g.target["basis"]
        g = gd.guide_position(pctx(), ctx(), decision("AVOID", 40))
        assert g.target["weight"] is None and "does not size reductions" in g.target["text"]
        assert gd.guide_position(pctx(), ctx(), decision("WATCHLIST", 50)).next_step == "No change indicated."

    def test_concentration_sends_a_hold_to_trim_review(self):
        g = gd.guide_position(pctx(weight=0.24), ctx(), decision("WATCHLIST", 50))
        assert g.existing == "Trim" and g.action == "Trim"
        assert "single-name risk" in g.why[0]

    def test_neutral_is_plain_hold(self):
        g = gd.guide_position(pctx(), ctx(), decision("WATCHLIST", 50, blockers=[
            {"code": "HOLD_NOT_OWNED", "text": "Overall score is in the neutral band (45–55)", "remedy": "Overall score reaching 55"}]))
        assert g.action == "Hold" and g.new_money == "No New Money"
        assert g.adding["status"] == "not_supported" and "below the 55 buy band" in g.adding["text"]

    def test_one_hold_label_whatever_the_score(self):
        """Audit F7: a higher score used to read the more restrictive 'Hold, no new money'."""
        g = gd.guide_position(pctx(), ctx(), decision("WATCHLIST", 66, stance="NEUTRAL", new_money="WATCH", blockers=[
            {"code": "RISK_GRADE_F", "text": "Risk grade F allows no new money", "remedy": "Risk grade improving to D"}]))
        g2 = dict(decision("WATCHLIST", 66, stance="NEUTRAL", new_money="WATCH"), model_stance="BULLISH")
        g2 = gd.guide_position(pctx(), ctx(), g2)
        g3 = gd.guide_position(pctx(), ctx(), decision("WATCHLIST", 51, new_money="WATCH"))
        # The engine's holder rule sends a grade-F holding to a risk review.
        assert g.action == "Review Risk" and g.basis["kind"] == "risk"
        assert g2.action == g3.action == "Hold" and g2.score_leaning == "Bullish"
        assert g2.adding["status"] == "blocked" and "buy band" not in g2.adding["text"]

    @pytest.mark.parametrize("dec,err", [
        (None, None),
        (decision("INVALID", None, holder="N/A"), None),
        (decision(freshness="stale"), None),
        (decision(), {"status": "provider_rate_limited", "title": "Provider rate-limited"}),
    ])
    def test_data_failure_is_never_an_action(self, dec, err):
        g = gd.guide_position(pctx(weight=0.30), ctx(), dec, err)
        assert g.status == "unavailable"
        assert g.action == "Unavailable" and g.new_money == "Unavailable" and g.existing == "Unavailable"
        assert g.group == "unavailable" and g.unavailable_reason

    def test_missing_cost_basis_is_stated_not_zero(self):
        g = gd.guide_position(pctx(average_cost=None, unrealized_pnl=None, unrealized_pnl_pct=None), ctx(), decision())
        assert any("did not report a cost basis" in w for w in g.why)

    def test_tax_note_only_for_reductions(self):
        lots = [{"quantity": 5, "acquired_on": "2025-10-20", "term": None},
                {"quantity": 5, "acquired_on": "2024-01-02", "term": None}]
        g = gd.guide_position(pctx(tax_lots=lots), ctx(), decision("AVOID", 40, holder="TRIM"))
        assert "5 shares long-term, 5 short-term" in g.tax_note
        assert "turn long-term on 2026-10-21" in g.tax_note and "not tax advice" in g.tax_note
        assert gd.guide_position(pctx(tax_lots=lots), ctx(), decision()).tax_note == ""


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def holdings():
    return [
        an.Holding("AAA", 100, 30_000, 300.0, "Technology", beta=1.4, stop=270.0, earnings_date="2026-10-02"),
        an.Holding("BBB", 100, 20_000, 200.0, "Technology", beta=1.2, stop=None),
        an.Holding("CCC", 100, 10_000, 100.0, "Utilities", beta=0.5, stop=90.0),
        an.Holding("DDD", 10, 5_000, 500.0, "", beta=None, stop=550.0),
    ]


class TestAnalytics:
    def test_weights_sectors_and_concentration(self):
        m = an.compute_metrics(holdings(), cash=35_000, reported_total=100_000, today=date(2026, 9, 28))
        assert m.weights["AAA"] == pytest.approx(0.30)
        sectors = {s["sector"]: s["weight"] for s in m.sectors}
        assert sectors == {"Technology": pytest.approx(0.5), "Utilities": pytest.approx(0.1),
                           "Unclassified": pytest.approx(0.05), "Cash": pytest.approx(0.35)}
        assert m.concentration["top"]["symbol"] == "AAA"
        assert [o["symbol"] for o in m.concentration["over_cap"]] == ["AAA", "BBB", "CCC"]
        # HHI on the invested book (65k): (30²+20²+10²+5²)/65²
        assert m.concentration["hhi"] == pytest.approx((900 + 400 + 100 + 25) / 65 ** 2)

    def test_beta_reports_coverage_and_cash_dilution(self):
        m = an.compute_metrics(holdings(), 35_000, 100_000)
        eb = (30 * 1.4 + 20 * 1.2 + 10 * 0.5) / 60
        assert m.beta["equity_beta"] == pytest.approx(eb)
        assert m.beta["coverage"] == pytest.approx(60 / 65)
        assert m.beta["portfolio_beta"] == pytest.approx(eb * 0.65)

    def test_stop_exposure_skips_missing_and_flags_below_stop(self):
        s = an.compute_metrics(holdings(), 35_000, 100_000).stops
        assert s["loss"] == pytest.approx(100 * 30 + 100 * 10)
        assert s["below_stop"] == ["DDD"]
        assert s["coverage"] == pytest.approx(45 / 65)

    def test_earnings_window(self):
        e = an.compute_metrics(holdings(), 35_000, 100_000, today=date(2026, 9, 28)).earnings
        assert [u["symbol"] for u in e["upcoming"]] == ["AAA"] and e["upcoming"][0]["days"] == 4

    def test_volatility_and_correlation_from_returns(self):
        rng = np.random.default_rng(7)
        base = rng.normal(0, 0.01, 250)
        rets = pd.DataFrame({"AAA": base + rng.normal(0, 0.002, 250),
                             "BBB": base + rng.normal(0, 0.002, 250),
                             "CCC": rng.normal(0, 0.01, 250)})
        v = an.compute_metrics(holdings(), 35_000, 100_000, returns=rets).volatility
        assert v["volatility"] is not None and v["days"] == 250
        assert v["coverage"] == pytest.approx(60 / 65)
        assert [(p["a"], p["b"]) for p in v["correlated_pairs"]] == [("AAA", "BBB")]

    def test_short_history_is_not_reported(self):
        rets = pd.DataFrame({"AAA": [0.01] * 20, "BBB": [0.0] * 20})
        assert an.compute_metrics(holdings(), 35_000, 100_000, returns=rets).volatility["volatility"] is None

    def test_risk_level_needs_data(self):
        hs = [an.Holding("AAA", 1, 100.0, 100.0, "Tech")]
        r = an.compute_metrics(hs, 0.0, 100.0).risk
        assert r["level"] == "Not assessed"
        r2 = an.compute_metrics(holdings(), 35_000, 100_000).risk
        assert r2["level"] in ("Moderate", "Elevated", "High") and r2["drivers"]

    def test_change_impact(self):
        hs = holdings()
        imp = an.change_impact(hs, 100_000, 35_000, "CCC", 5_000)
        assert imp["weight_after"] == pytest.approx(0.15)
        assert imp["sector_weight_after"] == pytest.approx(0.15)
        assert imp["cash_after"] == pytest.approx(30_000)
        rm = an.change_impact(hs, 100_000, 35_000, "AAA", -30_000)
        assert rm["weight_after"] == 0 and rm["sector_weight_after"] == pytest.approx(0.2)

    def test_empty_portfolio(self):
        m = an.compute_metrics([], cash=500.0, reported_total=500.0)
        assert m.concentration["label"] == "No holdings" and m.unrealized_pnl["total"] is None


class TestModels:
    def test_missing_cost_basis_gives_no_pnl(self):
        p = Position("X", 10, average_cost=None, last_price=5.0)
        assert p.market_value == 50 and p.unrealized_pnl is None and p.unrealized_pnl_pct is None

    def test_reconciliation(self):
        acct = AccountSummary("h", "Individual", portfolio_value=1_050.0, cash=50.0)
        pf = Portfolio("robinhood", acct, [Position("X", 10, last_price=100.0)], "now")
        assert pf.reconciliation()["status"] == "ok"
        acct.portfolio_value = 1_500.0
        assert pf.reconciliation()["status"] == "mismatch"
        pf.positions[0].last_price = None
        assert pf.reconciliation()["status"] == "unverifiable"


# ---------------------------------------------------------------------------
# Redaction and logging
# ---------------------------------------------------------------------------

class TestRedaction:
    def test_secrets_and_ids_are_scrubbed(self):
        register_sensitive([ACCOUNT_NUMBER])
        text = (f"Authorization: Bearer {ACCESS_TOKEN} account {ACCOUNT_NUMBER} "
                f'{{"refresh_token": "{REFRESH_TOKEN}"}} /cb?code=abc123xyz&state=s7aT3 acct 123456789')
        out = redact(text)
        for secret in (ACCESS_TOKEN, ACCOUNT_NUMBER, REFRESH_TOKEN, "abc123xyz", "s7aT3", "123456789"):
            assert secret not in out

    def test_filter_rewrites_records_and_tracebacks(self):
        stream = io.StringIO()
        log = logging.getLogger("test-redact")
        handler = logging.StreamHandler(stream)
        log.addHandler(handler)
        log.addFilter(RedactingFilter())
        try:
            log.warning("token %s", ACCESS_TOKEN)
            try:
                raise ValueError(f"bad account {ACCOUNT_NUMBER}")
            except ValueError:
                log.exception("failed")
        finally:
            log.removeHandler(handler)
        out = stream.getvalue()
        assert ACCESS_TOKEN not in out and ACCOUNT_NUMBER not in out and "failed" in out


# ---------------------------------------------------------------------------
# Fake Robinhood (OAuth + MCP over HTTP). Test-only.
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status=200, body=None, headers=None, sse=False):
        self.status_code = status
        self._body = body
        self.headers = dict(headers or {})
        self._sse = sse
        if body is not None and "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "text/event-stream" if sse else "application/json"
        raw = (b"" if body is None else
               (f"event: message\ndata: {json.dumps(body)}\n\n".encode() if sse else json.dumps(body).encode()))
        self.raw = io.BytesIO(raw)
        self.raw.read = (lambda n=-1, decode_content=True, _r=self.raw: io.BytesIO.read(_r, n))  # type: ignore

    def iter_lines(self, decode_unicode=True):
        for line in self.raw.getvalue().decode().split("\n"):
            yield line

    def json(self):
        return self._body

    def close(self):
        pass


def tool(name, props=(), required=()):
    return {"name": name, "description": name,
            "inputSchema": {"type": "object", "properties": {p: {"type": "string"} for p in props},
                            "required": list(required)}}


DEFAULT_TOOLS = [
    tool("get_accounts"),
    tool("get_portfolio", ["account_number"], ["account_number"]),
    tool("get_equity_positions", ["account_number"], ["account_number"]),
    dict(tool("get_equity_quotes", ["symbols"], ["symbols"]),
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}},
                      "required": ["symbols"]}),
    tool("get_equity_tax_lots", ["account_number", "symbol"], ["account_number", "symbol"]),
    tool("get_equity_fundamentals", ["symbols"], ["symbols"]),
    tool("get_earnings_calendar", ["symbols"]),
    tool("get_realized_pnl", ["account_number"]),
    tool("review_equity_order", ["account_number", "symbol", "quantity"]),
    tool("place_equity_order", ["account_number", "symbol", "quantity"]),
    tool("create_watchlist", ["name"]),
]


class FakeRobinhood:
    """Just enough of agent.robinhood.com to exercise the client end to end."""

    def __init__(self, tools=None, sse=False):
        self.tools = tools if tools is not None else DEFAULT_TOOLS
        self.sse = sse
        self.calls = []                 # tool names actually sent
        self.requests = []              # (method, url, body)
        self.fail_tool = {}             # name -> FakeResp or "error"
        self.expire_after = None        # force 401 after N tool calls
        self.token_ok = True
        self.positions = [
            {"symbol": "AAPL", "quantity": "10.0000", "average_buy_price": "150.00", "name": "Apple"},
            {"symbol": "BRK.B", "quantity": "4", "average_buy_price": None},
            {"symbol": "ZERO", "quantity": "0", "average_buy_price": "1"},
            {"symbol": "BAD", "quantity": "n/a"},
        ]
        self.quotes = {"AAPL": {"symbol": "AAPL", "last_trade_price": "200.00", "adjusted_previous_close": "198.00",
                                "updated_at": "2026-09-25T20:00:00Z"},
                       "BRK.B": {"symbol": "BRK.B", "last_trade_price": "500.00", "previous_close": "495.00",
                                 "updated_at": "2026-09-25T20:00:00Z"}}

    # requests.Session surface
    def get(self, url, timeout=None, headers=None, *, allow_redirects=None, verify=None):
        assert allow_redirects is False and verify is True
        self.requests.append(("GET", url, None))
        if url.endswith("/.well-known/oauth-protected-resource/mcp/trading"):
            return FakeResp(body={"authorization_servers": ["https://agent.robinhood.com/mcp/trading"],
                                  "resource": "https://agent.robinhood.com/mcp/trading", "scopes_supported": ["internal"]})
        if "oauth-authorization-server" in url:
            return FakeResp(body={"issuer": "https://agent.robinhood.com/mcp/trading",
                                  "authorization_endpoint": "https://robinhood.com/oauth",
                                  "token_endpoint": "https://api.robinhood.com/oauth2/token/",
                                  "registration_endpoint": "https://agent.robinhood.com/oauth/trading/register",
                                  "code_challenge_methods_supported": ["S256"]})
        return FakeResp(404, {})

    def delete(self, url, headers=None, timeout=None, *, allow_redirects=None, verify=None):
        assert allow_redirects is False and verify is True
        return FakeResp(200, {})

    def post(self, url, data=None, json=None, headers=None, timeout=None, stream=False,
             *, allow_redirects=None, verify=None):  # noqa: A002
        assert allow_redirects is False and verify is True
        self.requests.append(("POST", url, data if data is not None else json))
        if url.endswith("/register"):
            return FakeResp(201, {"client_id": "public-client-1"})
        if url.endswith("/oauth2/token/"):
            if not self.token_ok:
                return FakeResp(400, {"error": "invalid_grant"})
            return FakeResp(body={"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN,
                                  "token_type": "Bearer", "expires_in": 3600, "scope": "internal"})
        msg = globals()["json"].loads(data)
        if (headers or {}).get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return FakeResp(401, {"error": "unauthorized"})
        method = msg.get("method")
        if "id" not in msg:
            return FakeResp(202, None, {"Content-Type": "application/json"})
        if method == "initialize":
            return FakeResp(body={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-06-18", "serverInfo": {"name": "robinhood-trading", "version": "1"}}},
                headers={"Mcp-Session-Id": "sess-1"}, sse=self.sse)
        if method == "tools/list":
            return FakeResp(body={"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": self.tools}}, sse=self.sse)
        if method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"]["arguments"]
            self.calls.append((name, args))
            if self.expire_after is not None and len(self.calls) > self.expire_after:
                return FakeResp(401, {"error": "expired"})
            if name in self.fail_tool:
                return FakeResp(body={"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "isError": True, "content": [{"type": "text", "text": f"boom for {ACCOUNT_NUMBER}"}]}})
            payload = self.payload(name, args)
            return FakeResp(body={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": globals()["json"].dumps(payload)}]}}, sse=self.sse)
        return FakeResp(body={"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}})

    def payload(self, name, args):
        if name == "get_accounts":
            return {"accounts": [{"account_number": ACCOUNT_NUMBER, "rhs_account_number": "12345678", "nickname": "", "brokerage_account_type": "individual",
                                  "agentic_allowed": False},
                                 {"account_number": "9A99990001", "nickname": "Agentic", "agentic_allowed": True}]}
        if name == "get_portfolio":
            return {"total_value": "5000.00", "cash": "1000.00", "buying_power": {"buying_power": "900.00"}}
        if name == "get_equity_positions":
            assert args == {"account_number": ACCOUNT_NUMBER}
            return {"results": self.positions}
        if name == "get_equity_quotes":
            return {"results": [{"quote": dict(self.quotes[s], venue_last_trade_time=self.quotes[s].get("updated_at"))}
                                for s in args["symbols"] if s in self.quotes]}
        if name == "get_equity_tax_lots":
            if args["symbol"] == "AAPL":
                return {"tax_lots": [{"quantity": "6", "cost_per_share": "140", "open_date": "2024-02-01", "term": "lt"},
                                     {"quantity": "4", "cost_per_share": "165", "open_date": "2026-03-01", "term": "st"}]}
            return {"tax_lots": []}
        if name == "get_equity_fundamentals":
            return {"results": [{"symbol": "AAPL", "sector": "Technology", "industry": "Hardware"}]}
        if name == "get_earnings_calendar":
            return {"results": [{"symbol": "AAPL", "report": {"date": "2025-01-30"}},
                                {"symbol": "AAPL", "report": {"date": "2099-10-30"}}]}
        if name == "get_realized_pnl":
            assert args["account_number"] == "12345678"
            return {"total_returns": "123.45", "window": "3month"}
        return {}


def connected_provider(fake=None):
    fake = fake or FakeRobinhood()
    prov = RobinhoodProvider(session=fake)
    url = prov.begin_connect("http://127.0.0.1:5500/api/portfolio/oauth/callback")
    state = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["state"]
    prov.complete_connect({"code": "auth-code-1", "state": state})
    return prov, fake


# ---------------------------------------------------------------------------
# MCP transport and the read-only boundary
# ---------------------------------------------------------------------------

class TestReadOnlyBoundary:
    @pytest.mark.parametrize("name", ["place_equity_order", "review_equity_order", "cancel_equity_order",
                                      "preview_crypto_order", "create_watchlist", "add_to_watchlist",
                                      "run_scan", "delete_alert", "exercise_option"])
    def test_write_tools_are_recognised(self, name):
        assert is_write_tool(name)

    @pytest.mark.parametrize("name", list(READ_TOOLS.values()))
    def test_read_tools_are_not_writes(self, name):
        assert not is_write_tool(name)

    def test_write_tool_cannot_be_allowlisted(self):
        with pytest.raises(ValueError):
            McpHttpClient("https://x", lambda: "t", allowed_tools=frozenset({"get_accounts", "place_equity_order"}))

    def test_refused_tools_never_reach_the_network(self):
        prov, fake = connected_provider()
        sent_before = len(fake.requests)
        for name in ("place_equity_order", "review_equity_order", "get_option_positions"):
            with pytest.raises(ToolNotPermitted):
                prov._client.call_tool(name, {"symbol": "AAPL"})
        assert len(fake.requests) == sent_before
        assert not any(n in ("place_equity_order", "review_equity_order") for n, _ in fake.calls)

    def test_diagnostics_list_but_do_not_use_write_tools(self):
        prov, _ = connected_provider()
        diag = prov.diagnostics()
        assert set(diag["write_tools_offered"]) == {"review_equity_order", "place_equity_order", "create_watchlist"}
        assert diag["status"]["execution_enabled"] is False and diag["status"]["read_only"] is True

    def test_execution_layer_is_disabled(self):
        assert EXECUTION_ENABLED is False
        with pytest.raises(ExecutionDisabled):
            ExecutionGateway().place(OrderIntent("AAPL", "buy", 1), "token")
        with pytest.raises(ExecutionDisabled):
            ExecutionGateway().review(OrderIntent("AAPL", "buy", 1))

    def test_sse_responses_are_parsed(self):
        prov, fake = connected_provider(FakeRobinhood(sse=True))
        assert prov.state() is ConnectionState.CONNECTED
        assert len(prov.list_accounts()) == 2


class TestOAuth:
    @pytest.mark.parametrize("url", [
        "http://agent.robinhood.com/mcp/trading",
        "https://agent.robinhood.com.evil.example/mcp/trading",
        "https://unapproved.robinhood.com/mcp/trading",
        "https://user:password@agent.robinhood.com/mcp/trading",
        "https://agent.robinhood.com:444/mcp/trading",
        "https://agent.robinhood.com/mcp/trading#fragment",
    ])
    def test_unapproved_resource_rejected_before_network(self, url):
        fake = FakeRobinhood()
        with pytest.raises(ProviderError, match="unapproved"):
            RobinhoodProvider(mcp_url=url, session=fake)
        assert fake.requests == []

    def test_http_redirect_cannot_forward_token(self):
        import requests
        from requests.adapters import BaseAdapter

        seen = []

        class RedirectAdapter(BaseAdapter):
            def send(self, request, **kwargs):
                seen.append((request.url, kwargs["verify"], kwargs["proxies"]))
                response = requests.Response()
                response.status_code = 307
                response.headers["Location"] = "https://evil.example/steal"
                response.raw = io.BytesIO(b"")
                response.request = request
                return response

        session = requests.Session()
        session.verify = False  # client must override even an insecure injected session
        session.mount("https://", RedirectAdapter())
        client = McpHttpClient("https://agent.robinhood.com/mcp/trading", lambda: ACCESS_TOKEN,
                               allowed_tools=frozenset(READ_TOOLS.values()), session=session)
        with pytest.raises(ProviderError) as exc:
            client.initialize()
        assert exc.value.code == "UNTRUSTED_ENDPOINT"
        assert [url for url, _, _ in seen] == ["https://agent.robinhood.com/mcp/trading"]
        # Verification is never off: True, or the CA bundle the system configured.
        assert all(verify is True or (isinstance(verify, str) and verify) for _, verify, _ in seen)
        # ~/.netrc is never consulted: Session.auth is occupied by a no-op.
        assert isinstance(session.auth, security._NoNetrc)

    def test_tls_error_fails_closed_without_retry(self):
        import requests
        fake = FakeRobinhood()
        calls = []

        def fail_tls(url, **kwargs):
            calls.append(kwargs["verify"])
            raise requests.exceptions.SSLError("certificate hostname mismatch")

        fake.post = fail_tls
        client = McpHttpClient("https://agent.robinhood.com/mcp/trading", lambda: ACCESS_TOKEN,
                               allowed_tools=frozenset(READ_TOOLS.values()), session=fake)
        with pytest.raises(ProviderError) as exc:
            client.initialize()
        assert exc.value.code == "NETWORK_ERROR"
        assert calls == [True]

    @pytest.mark.parametrize("operation", ["metadata", "register", "token"])
    def test_oauth_redirect_fails_closed(self, operation):
        fake = FakeRobinhood()
        client = oauth.OAuthClient("https://agent.robinhood.com/mcp/trading",
                                  ("robinhood.com", "agent.robinhood.com", "api.robinhood.com"),
                                  session=fake)
        if operation != "metadata":
            client.discover()
        calls = []

        def redirect(url, **kwargs):
            calls.append(url)
            assert kwargs["allow_redirects"] is False and kwargs["verify"] is True
            return FakeResp(307, {}, {"Location": "https://evil.example/steal"})

        fake.get = fake.post = redirect
        with pytest.raises(ProviderError):
            if operation == "metadata":
                client.discover()
            elif operation == "register":
                client.register("http://127.0.0.1:5501/cb")
            else:
                client._token_request("https://api.robinhood.com/oauth2/token/",
                                      {"refresh_token": REFRESH_TOKEN})
        assert len(calls) == 1
        assert not client.connected

    def test_pkce_and_state(self):
        verifier, challenge = oauth.pkce_pair()
        import base64
        import hashlib
        assert 43 <= len(verifier) <= 128
        assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        prov = RobinhoodProvider(session=FakeRobinhood())
        url = prov.begin_connect("http://127.0.0.1:5500/cb")
        assert url.startswith("https://robinhood.com/oauth?") and "code_challenge_method=S256" in url
        assert prov.state() is ConnectionState.AWAITING_AUTH

    def test_wrong_state_is_rejected(self):
        prov = RobinhoodProvider(session=FakeRobinhood())
        prov.begin_connect("http://127.0.0.1:5500/cb")
        with pytest.raises(ProviderError) as e:
            prov.complete_connect({"code": "x", "state": "forged"})
        assert e.value.code == "INVALID_STATE"
        assert prov._client is None

    def test_denied_sign_in(self):
        prov = RobinhoodProvider(session=FakeRobinhood())
        prov.begin_connect("http://127.0.0.1:5500/cb")
        with pytest.raises(ProviderError):
            prov.complete_connect({"error": "access_denied"})
        assert prov.status()["last_error"]["code"] == "AUTH_DENIED"

    def test_untrusted_metadata_endpoint_is_refused(self):
        fake = FakeRobinhood()
        real_get = fake.get

        def evil_get(url, **kw):
            r = real_get(url, **kw)
            if "oauth-authorization-server" in url:
                r._body["token_endpoint"] = "https://evil.example/token"
                r.raw = io.BytesIO(json.dumps(r._body).encode())
            return r
        fake.get = evil_get
        prov = RobinhoodProvider(session=fake)
        with pytest.raises(ProviderError) as e:
            prov.begin_connect("http://127.0.0.1:5500/cb")
        assert e.value.code == "UNTRUSTED_ENDPOINT"

    def test_tokens_never_repr(self):
        t = oauth.TokenSet(ACCESS_TOKEN, REFRESH_TOKEN, time.time() + 60)
        assert ACCESS_TOKEN not in repr(t) and REFRESH_TOKEN not in str(t)

    def test_expired_token_refreshes_once_then_fails_closed(self):
        prov, fake = connected_provider()
        fake.expire_after = 0
        fake.token_ok = False                     # refresh is rejected too
        with pytest.raises(AuthExpired):
            prov.list_accounts()
        assert prov.state() is ConnectionState.AUTH_EXPIRED
        assert prov._oauth.connected is False
        with pytest.raises(AuthExpired):          # no silent reuse afterwards
            prov.list_accounts()

    def test_disconnect_forgets_everything(self):
        prov, _ = connected_provider()
        prov.list_accounts()
        prov.disconnect()
        assert prov.state() is ConnectionState.DISCONNECTED
        assert prov._oauth.token_info() == {"present": False} and not prov._accounts


# ---------------------------------------------------------------------------
# Normalizing a Robinhood account
# ---------------------------------------------------------------------------

class MovedRobinhood(FakeRobinhood):
    """Robinhood after a routine deployment change: sign-in and token hosts moved to
    other robinhood.com subdomains, metadata only discoverable through the MCP 401
    hint and OpenID Connect paths, registration behind a 307, a trailing-slash 301
    on metadata, and a newer MCP protocol version that must be echoed back."""

    AS = "https://auth.robinhood.com"
    TOKEN = "https://auth.robinhood.com/oauth2/token/"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.protocol_headers = []

    def get(self, url, timeout=None, headers=None, *, allow_redirects=None, verify=None):
        assert allow_redirects is False and verify is True
        self.requests.append(("GET", url, None))
        if url == "https://auth.robinhood.com/meta/prm":
            return FakeResp(301, {}, {"Location": "/meta/prm/"})
        if url == "https://auth.robinhood.com/meta/prm/":
            return FakeResp(body={"authorization_servers": [self.AS], "scopes_supported": ["internal"]})
        if url == f"{self.AS}/.well-known/openid-configuration":
            return FakeResp(body={"issuer": self.AS,
                                  "authorization_endpoint": "https://login.robinhood.com/oauth2/authorize",
                                  "token_endpoint": self.TOKEN,
                                  "registration_endpoint": f"{self.AS}/register",
                                  "code_challenge_methods_supported": ["S256"]})
        return FakeResp(404, {})

    def post(self, url, data=None, json=None, headers=None, timeout=None, stream=False,
             *, allow_redirects=None, verify=None):  # noqa: A002
        assert allow_redirects is False and verify is True
        if url == f"{self.AS}/register":
            self.requests.append(("POST", url, json))
            return FakeResp(307, {}, {"Location": f"{self.AS}/register/v2"})
        if url == f"{self.AS}/register/v2":
            self.requests.append(("POST", url, json))
            return FakeResp(201, {"client_id": "public-client-2"})
        if url == self.TOKEN:
            return super().post("https://api.robinhood.com/oauth2/token/", data=data, headers=headers,
                                allow_redirects=allow_redirects, verify=verify)
        auth = (headers or {}).get("Authorization")
        if auth is None or auth == "Bearer ":
            self.requests.append(("POST", url, "unauthenticated probe"))
            return FakeResp(401, {"error": "unauthorized"}, {
                "WWW-Authenticate": 'Bearer resource_metadata="https://auth.robinhood.com/meta/prm"'})
        msg = globals()["json"].loads(data)
        if msg.get("method") != "initialize":
            self.protocol_headers.append((headers or {}).get("MCP-Protocol-Version"))
            if (headers or {}).get("MCP-Protocol-Version") != "2025-11-25":
                return FakeResp(400, {"jsonrpc": "2.0", "error": {"code": -32600,
                                                                  "message": "Unsupported protocol version"}})
        reply = super().post(url, data=data, headers=headers, stream=stream,
                             allow_redirects=allow_redirects, verify=verify)
        if msg.get("method") == "initialize":
            reply._body["result"]["protocolVersion"] = "2025-11-25"
            reply.raw = io.BytesIO(globals()["json"].dumps(reply._body).encode())
            reply.raw.read = (lambda n=-1, decode_content=True, _r=reply.raw: io.BytesIO.read(_r, n))
        return reply


def _state_of(url):
    return dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["state"]


class TestConnectResilience:
    """Sign-in must keep working for any user when Robinhood changes routine
    deployment details, while every security property still holds."""

    def test_connects_after_robinhood_moves_its_sign_in_hosts(self):
        fake = MovedRobinhood()
        prov = RobinhoodProvider(session=fake)
        url = prov.begin_connect("http://127.0.0.1:5500/api/portfolio/oauth/callback")
        assert url.startswith("https://login.robinhood.com/oauth2/authorize?")
        assert "client_id=public-client-2" in url
        prov.complete_connect({"code": "c", "state": _state_of(url), "iss": MovedRobinhood.AS})
        assert prov.state() is ConnectionState.CONNECTED, prov.status()["last_error"]
        assert len(prov.list_accounts()) == 2
        # Every request after initialize carried the version the server chose.
        assert fake.protocol_headers and set(fake.protocol_headers) == {"2025-11-25"}
        assert prov.status()["server"]["protocol_version"] == "2025-11-25"

    def test_mcp_resource_stays_pinned_to_exact_host(self):
        for url in ("https://auth.robinhood.com/mcp/trading", "https://robinhood.com.evil.example/mcp"):
            with pytest.raises(ProviderError) as e:
                RobinhoodProvider(mcp_url=url, session=FakeRobinhood())
            assert e.value.code == "UNTRUSTED_ENDPOINT"

    @pytest.mark.parametrize("endpoint", ["https://robinhood.com.evil.example/token",
                                          "https://evilrobinhood.com/token",
                                          "http://auth.robinhood.com/token",
                                          "https://auth.robinhood.com:8443/token",
                                          "https://user@auth.robinhood.com/token"])
    def test_lookalike_oauth_endpoints_still_refused(self, endpoint):
        fake = MovedRobinhood()
        real_get = fake.get

        def get(url, **kw):
            r = real_get(url, **kw)
            if url.endswith("openid-configuration"):
                r._body["token_endpoint"] = endpoint
            return r
        fake.get = get
        with pytest.raises(ProviderError) as e:
            RobinhoodProvider(session=fake).begin_connect("http://127.0.0.1:5500/cb")
        assert e.value.code == "UNTRUSTED_ENDPOINT"

    def test_token_redirect_to_another_host_is_refused(self):
        fake = FakeRobinhood()
        client = oauth.OAuthClient("https://agent.robinhood.com/mcp/trading", ("agent.robinhood.com",),
                                   endpoint_domain="robinhood.com", session=fake)
        sent = []

        def post(url, **kw):
            sent.append(url)
            return FakeResp(307, {}, {"Location": "https://other.robinhood.com/token"})
        fake.post = post
        with pytest.raises(ProviderError) as e:
            client._token_request("https://api.robinhood.com/oauth2/token/", {"refresh_token": REFRESH_TOKEN})
        assert e.value.code == "UNTRUSTED_ENDPOINT" and sent == ["https://api.robinhood.com/oauth2/token/"]

    def test_post_is_never_replayed_across_a_method_changing_redirect(self):
        fake = FakeRobinhood()
        client = oauth.OAuthClient("https://agent.robinhood.com/mcp/trading", ("agent.robinhood.com",),
                                   endpoint_domain="robinhood.com", session=fake)
        client.discover()
        fake.post = lambda url, **kw: FakeResp(302, {}, {"Location": url + "/moved"})
        with pytest.raises(ProviderError) as e:
            client.register("http://127.0.0.1:5500/cb")
        assert e.value.code == "UNTRUSTED_ENDPOINT"

    def test_redirect_loop_is_bounded(self):
        fake = FakeRobinhood()
        calls = []

        def loop(url, **kw):
            calls.append(url)
            return FakeResp(301, {}, {"Location": url})
        fake.get = loop
        with pytest.raises(ProviderError):
            oauth.OAuthClient("https://agent.robinhood.com/mcp/trading", ("agent.robinhood.com",),
                              session=fake).discover()
        assert 0 < len(calls) <= 2 * (security.MAX_REDIRECTS + 1) + 1

    def test_registration_rejection_names_the_reason(self):
        fake = FakeRobinhood()
        real_post = fake.post

        def post(url, **kw):
            if url.endswith("/register"):
                return FakeResp(400, {"error": "invalid_redirect_uri",
                                      "error_description": "redirect_uri must use 127.0.0.1"})
            return real_post(url, **kw)
        fake.post = post
        with pytest.raises(ProviderError) as e:
            RobinhoodProvider(session=fake).begin_connect("http://localhost:5500/cb")
        assert e.value.code == "REGISTRATION_FAILED"
        assert "invalid_redirect_uri" in str(e.value) and "127.0.0.1" in str(e.value)

    def test_unreadable_registration_is_an_error_not_a_crash(self):
        fake = FakeRobinhood()
        real_post = fake.post

        def post(url, **kw):
            if url.endswith("/register"):
                r = FakeResp(201, {})
                r.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
                return r
            return real_post(url, **kw)
        fake.post = post
        with pytest.raises(ProviderError) as e:
            RobinhoodProvider(session=fake).begin_connect("http://127.0.0.1:5500/cb")
        assert e.value.code == "REGISTRATION_FAILED"

    def test_sign_in_error_is_explained_and_registration_is_redone(self):
        fake = FakeRobinhood()
        prov = RobinhoodProvider(session=fake)
        prov.begin_connect("http://127.0.0.1:5500/cb")
        with pytest.raises(ProviderError):
            prov.complete_connect({"error": "invalid_client", "error_description": "unknown client"})
        assert "invalid_client" in prov.status()["last_error"]["message"]
        registrations = sum(1 for m, u, _ in fake.requests if m == "POST" and u.endswith("/register"))
        prov.begin_connect("http://127.0.0.1:5500/cb")
        assert sum(1 for m, u, _ in fake.requests if m == "POST" and u.endswith("/register")) == registrations + 1

    def test_rejected_code_exchange_rediscovers_on_retry(self):
        fake = FakeRobinhood()
        fake.token_ok = False
        prov = RobinhoodProvider(session=fake)
        url = prov.begin_connect("http://127.0.0.1:5500/cb")
        with pytest.raises(ProviderError):
            prov.complete_connect({"code": "c", "state": _state_of(url)})
        assert "invalid_grant" in prov.status()["last_error"]["message"]
        fake.token_ok = True
        gets = sum(1 for m, _, _ in fake.requests if m == "GET")
        url = prov.begin_connect("http://127.0.0.1:5500/cb")
        assert sum(1 for m, _, _ in fake.requests if m == "GET") > gets     # metadata read again
        prov.complete_connect({"code": "c", "state": _state_of(url)})
        assert prov.state() is ConnectionState.CONNECTED

    def test_mismatched_issuer_on_callback_is_refused(self):
        prov = RobinhoodProvider(session=FakeRobinhood())
        url = prov.begin_connect("http://127.0.0.1:5500/cb")
        with pytest.raises(ProviderError) as e:
            prov.complete_connect({"code": "c", "state": _state_of(url), "iss": "https://evil.example"})
        assert e.value.code == "UNTRUSTED_ENDPOINT" and prov._client is None and not prov._oauth.connected

    def test_mcp_start_failure_recovers_without_a_new_sign_in(self):
        fake = FakeRobinhood()
        real_post = fake.post
        outage = {"on": True}

        def post(url, **kw):
            if outage["on"] and url.endswith("/mcp/trading"):
                return FakeResp(503, {"error": "maintenance"})
            return real_post(url, **kw)
        fake.post = post
        prov = RobinhoodProvider(session=fake)
        url = prov.begin_connect("http://127.0.0.1:5500/cb")
        with pytest.raises(ProviderError) as e:
            prov.complete_connect({"code": "c", "state": _state_of(url)})
        assert "503" in str(e.value) and "maintenance" in str(e.value)
        assert prov.state() is ConnectionState.PROVIDER_ERROR
        outage["on"] = False
        assert len(prov.list_accounts()) == 2
        assert prov.state() is ConnectionState.CONNECTED

    def test_network_errors_are_actionable(self):
        import requests
        msg = str(security.network_error(requests.exceptions.SSLError("x")))
        assert "REQUESTS_CA_BUNDLE" in msg
        assert "proxy" in str(security.network_error(requests.exceptions.ProxyError("x")))
        assert security.network_error(requests.exceptions.ConnectionError("x")).code == "NETWORK_ERROR"

    def test_system_proxy_is_honoured_but_netrc_is_not(self, tmp_path, monkeypatch):
        import requests
        netrc = tmp_path / "netrc"
        netrc.write_text("machine agent.robinhood.com login leaked password leaked\n")
        netrc.chmod(0o600)
        monkeypatch.setenv("NETRC", str(netrc))
        # Clear first, then set: on Windows environment names are
        # case-insensitive, so deleting https_proxy after setting HTTPS_PROXY
        # deleted the proxy under test.
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:3128")
        session = security.session_or_new(requests.Session())
        prepared = session.prepare_request(requests.Request("GET", "https://agent.robinhood.com/x"))
        assert "Authorization" not in prepared.headers
        settings = session.merge_environment_settings(prepared.url, {}, None, True, None)
        assert settings["proxies"].get("https") == "http://proxy.corp.example:3128"
        assert settings["verify"]


class TestRobinhoodNormalization:
    def test_observed_official_nested_payloads(self):
        """Observed live structure, synthetic values only; not live proof."""
        fake = FakeRobinhood()
        original = fake.payload

        def payload(name, args):
            body = original(name, args)
            if name == "get_portfolio":
                body = {"total_value": "5010.00", "cash": "1000.00",
                        "buying_power": {"buying_power": "900.00"}}
            elif name == "get_equity_quotes":
                body = {"results": [{"quote": dict(q,
                    venue_last_trade_time="2026-09-25T20:00:00Z",
                    last_non_reg_trade_price="201" if q["symbol"] == "AAPL" else None,
                    venue_last_non_reg_trade_time="2026-09-26T00:00:00Z"),
                    "close": {"price": q["last_trade_price"], "date": "2026-09-25"}}
                    for q in (entry["quote"] for entry in body["results"])]}
            elif name == "get_equity_tax_lots":
                body = {"symbol": args["symbol"], "tax_lots": [
                    {"quantity": "10", "cost_per_share": "150", "tax_cost_basis": "1500",
                     "open_date": "2024-01-01", "term": "long"}]}
            elif name == "get_realized_pnl":
                body = {"total_returns": "12.34", "window": "year", "data_points": []}
            return {"data": body, "guide": "Synthetic test payload matching the observed envelope."}

        fake.payload = payload
        prov, _ = connected_provider(fake)
        pf = prov.load_portfolio(prov.list_accounts()[0].handle)
        p = next(p for p in pf.positions if p.symbol == "AAPL")
        assert (p.last_price, p.previous_close, p.tax_lots[0].cost_per_share) == (201, 198, 150)
        assert p.quote_as_of == "2026-09-26T00:00:00Z"
        assert (pf.account.buying_power, pf.account.realized_pnl, pf.account.realized_pnl_period) == (900, 12.34, "year")
        assert pf.reconciliation()["status"] == "ok"

    def test_fundamentals_respects_observed_ten_symbol_limit(self):
        prov, fake = connected_provider()
        prov._batched("fundamentals", [f"TEST{i}" for i in range(23)], ACCOUNT_NUMBER)
        sizes = [len(a["symbols"].split(",")) for n, a in fake.calls if n == "get_equity_fundamentals"]
        assert sizes == [10, 10, 3]

    def test_reconciliation_rejects_small_percentage_but_material_cash_difference(self):
        pf = Portfolio("robinhood", AccountSummary("opaque", "Individual", portfolio_value=100_000, cash=0),
                       [Position("TEST", 100, last_price=999)], "2026-09-26")
        assert pf.reconciliation()["status"] == "mismatch"

    def load(self, fake=None):
        prov, fake = connected_provider(fake)
        accts = prov.list_accounts()
        main = next(a for a in accts if a.can_trade_via_agent is False)
        return prov, fake, prov.load_portfolio(main.handle)

    def test_accounts_are_opaque(self):
        prov, _ = connected_provider()
        accts = prov.list_accounts()
        blob = json.dumps([a.to_dict() for a in accts])
        assert ACCOUNT_NUMBER not in blob and "9A99990001" not in blob
        assert {a.label for a in accts} == {"Individual", "Agentic"}

    def test_positions_quotes_basis_and_lots(self):
        _, fake, pf = self.load()
        by = {p.symbol: p for p in pf.positions}
        assert set(by) == {"AAPL", "BRK.B"}                   # zero qty dropped, unreadable qty skipped
        a = by["AAPL"]
        assert (a.quantity, a.average_cost, a.last_price, a.previous_close) == (10.0, 150.0, 200.0, 198.0)
        assert a.unrealized_pnl == pytest.approx(500.0) and a.sector == "Technology"
        assert a.earnings_date == "2099-10-30"                 # next report, not the past one
        assert [lot.quantity for lot in a.tax_lots] == [6.0, 4.0]
        b = by["BRK.B"]
        assert b.average_cost is None and b.unrealized_pnl is None and "cost_basis" in b.missing
        codes = {i.code for i in pf.issues}
        assert {"COST_BASIS_MISSING", "QUANTITY_MISSING"} <= codes
        assert pf.account.portfolio_value == 5000.0 and pf.account.cash == 1000.0
        assert pf.account.buying_power == 900.0 and pf.account.realized_pnl == pytest.approx(123.45)
        # 10*200 + 4*500 + 1000 cash = 5000 -> reconciles with Robinhood's total.
        assert pf.reconciliation()["status"] == "ok"

    def test_arguments_follow_the_live_schema(self):
        _, fake, _ = self.load()
        quotes = [a for n, a in fake.calls if n == "get_equity_quotes"]
        assert quotes and isinstance(quotes[0]["symbols"], list)          # array schema -> list
        fund = [a for n, a in fake.calls if n == "get_equity_fundamentals"]
        assert fund[0]["symbols"] == "AAPL,BRK.B"                          # string schema -> csv
        lots = [a["symbol"] for n, a in fake.calls if n == "get_equity_tax_lots"]
        assert lots == ["AAPL", "BRK.B"]                                   # single-symbol tool -> per symbol

    def test_missing_quotes_tool_is_partial_not_fabricated(self):
        fake = FakeRobinhood(tools=[t for t in DEFAULT_TOOLS if t["name"] != "get_equity_quotes"])
        prov, fake = connected_provider(fake)
        assert prov.state() is ConnectionState.PARTIAL
        pf = prov.load_portfolio(prov.list_accounts()[0].handle)
        assert all(p.last_price is None for p in pf.positions)
        assert pf.reconciliation()["status"] == "unverifiable"
        assert "QUOTES_FAILED" in {i.code for i in pf.issues}

    def test_unfillable_required_input_marks_tool_unusable(self):
        tools = [t for t in DEFAULT_TOOLS if t["name"] != "get_equity_tax_lots"] + [
            tool("get_equity_tax_lots", ["account_number", "lot_method"], ["account_number", "lot_method"])]
        prov, fake = connected_provider(FakeRobinhood(tools=tools))
        pf = prov.load_portfolio(prov.list_accounts()[0].handle)
        assert not any(n == "get_equity_tax_lots" for n, _ in fake.calls)
        assert all(p.tax_lots is None for p in pf.positions)
        assert prov.diagnostics()["read_tools"]["tax_lots"]["unfillable"] == ["lot_method"]

    def test_tool_error_is_redacted_issue(self):
        fake = FakeRobinhood()
        fake.fail_tool["get_equity_fundamentals"] = True
        _, _, pf = self.load(fake)
        msgs = " ".join(i.message for i in pf.issues)
        assert "FUNDAMENTALS_UNAVAILABLE" in {i.code for i in pf.issues}
        assert ACCOUNT_NUMBER not in msgs

    def test_no_holdings(self):
        fake = FakeRobinhood()
        fake.positions = []
        _, _, pf = self.load(fake)
        assert pf.positions == [] and pf.account.cash == 1000.0

    def test_diagnostics_have_shapes_not_values(self):
        prov, _, _ = self.load()
        blob = json.dumps(prov.diagnostics())
        assert "average_buy_price" in blob and "150.00" not in blob and ACCOUNT_NUMBER not in blob


# ---------------------------------------------------------------------------
# Service: portfolio view assembly
# ---------------------------------------------------------------------------

def fake_analyze(results):
    def run(symbol, size):
        r = results.get(symbol)
        if isinstance(r, dict) and "status" in r and "decision" not in r:
            return None, r
        return r, None
    return run


def research(dec, sector="Technology", beta=1.1):
    return {"decision": dec, "sector": sector, "industry": "x", "risk": {"beta": beta, "beta_status": "measured"},
            "analysis_timestamp": "2026-09-26T10:00:00Z", "thesis": {"summary": "s"}}


class TestService:
    def test_disconnected_view_has_no_portfolio(self):
        svc = PortfolioService(RobinhoodProvider(session=FakeRobinhood()), fake_analyze({}))
        v = svc.view()
        assert v["portfolio"] is None and v["status"]["state"] == "disconnected"

    def test_end_to_end_with_analysis(self):
        # The fixture quotes are Friday 2026-09-25 closes. Pin the clock to the
        # following Saturday so the test does not depend on today's date or on
        # whether the US market happens to be open while it runs.
        from freezegun import freeze_time
        with freeze_time("2026-09-26 16:00:00"):
            self._end_to_end_with_analysis()

    def _end_to_end_with_analysis(self):
        prov, _ = connected_provider()
        svc = PortfolioService(prov, fake_analyze({"AAPL": research(decision()),
                                                   "BRK.B": {"status": "provider_rate_limited", "title": "Rate-limited"}}))
        handle = next(a["handle"] for a in svc.accounts() if a["can_trade_via_agent"] is False)
        v = svc.refresh(handle, start_thread=False)
        pf = v["portfolio"]
        rows = {r["symbol"]: r for r in pf["positions"]}
        # AAPL is 40% of this account: bullish stock, but over the limit -> no new money,
        # and past the 20% review level -> Trim for concentration.
        g = rows["AAPL"]["guidance"]
        assert g["stance"] == "Bullish" and g["new_money"] == "No New Money" and g["action"] == "Trim"
        assert rows["BRK.B"]["guidance"]["action"] == "Unavailable"
        assert set(rows["AAPL"]) >= {"broker", "market", "analysis", "interpretation", "guidance"}
        assert rows["AAPL"]["market"]["sector_source"] == "quanta"
        assert "ANALYSIS_FAILED" in {i["code"] for i in pf["issues"]}
        assert pf["reconciliation"]["status"] == "ok"
        assert json.dumps(v).find(ACCOUNT_NUMBER) == -1

    def test_auth_expiry_during_refresh_clears_holdings(self):
        prov, fake = connected_provider()
        svc = PortfolioService(prov, fake_analyze({}))
        handle = svc.accounts()[0]["handle"]
        svc.refresh(handle, run_analysis=False)
        fake.expire_after = len(fake.calls)
        fake.token_ok = False
        with pytest.raises(AuthExpired):
            svc.refresh(handle, run_analysis=False)
        assert svc.view()["portfolio"] is None
        assert svc.status()["state"] == "auth_expired"

    def test_disconnect_clears_holdings(self):
        prov, _ = connected_provider()
        svc = PortfolioService(prov, fake_analyze({}))
        svc.refresh(svc.accounts()[0]["handle"], run_analysis=False)
        svc.disconnect()
        assert svc.view()["portfolio"] is None

    def test_stale_quote_is_flagged(self):
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)      # Monday, market open (11:00 ET)
        assert quote_freshness("2026-09-25T20:00:00Z", now)["status"] == "stale"
        assert quote_freshness("2026-09-28T14:55:00Z", now)["status"] == "live"
        assert quote_freshness(None, now)["status"] == "unknown"
        weekend = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
        assert quote_freshness("2026-09-25T20:00:00Z", weekend)["status"] == "last_close"

    def test_large_portfolio(self):
        n = 400
        acct = AccountSummary("h" * 16, "Individual", portfolio_value=None, cash=10_000.0, buying_power=10_000.0)
        positions = [Position(f"S{i:03d}", 10, average_cost=10.0, last_price=11.0,
                              quote_as_of="2026-09-25T20:00:00Z") for i in range(n)]
        pf = Portfolio("robinhood", acct, positions, "2026-09-26T00:00:00Z")
        analysis = {p.symbol: {"state": "done", "result": {"decision": decision(), "sector": f"Sec{i % 11}"}}
                    for i, p in enumerate(positions)}
        t0 = time.time()
        v = build_view(pf, analysis, None, "", an.PortfolioPolicy(), {"analysis": {}},
                       datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc))
        assert time.time() - t0 < 5
        assert len(v["portfolio"]["positions"]) == n
        assert sum(g["count"] for g in v["portfolio"]["groups"]) == n


# ---------------------------------------------------------------------------
# Routes and request-level security
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    import app as webapp
    import portfolio_routes
    prov = RobinhoodProvider(session=FakeRobinhood())
    portfolio_routes.init_portfolio(webapp.app, fake_analyze({"AAPL": research(decision())}),
                                    None, provider=prov)
    yield webapp.app.test_client(), prov
    portfolio_routes.init_portfolio(webapp.app, webapp._portfolio_analyze, webapp._portfolio_returns)


H = {"X-QUANTA-Request": "1"}


class TestRoutes:
    def test_headers_and_guards(self, client):
        c, _ = client
        r = c.get("/api/portfolio/status")
        assert r.status_code == 200 and r.headers["Cache-Control"].startswith("no-store")
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert c.get("/api/portfolio/status", headers={"Host": "attacker.example"}).status_code == 403
        assert c.post("/api/portfolio/connect").status_code == 403
        assert c.post("/api/portfolio/connect", headers={**H, "Origin": "https://attacker.example"}).status_code == 403

    def test_full_flow_and_logs_are_clean(self, client, caplog):
        c, prov = client
        caplog.set_level(logging.DEBUG)
        r = c.post("/api/portfolio/connect", headers={**H, "Origin": "http://localhost"})
        url = r.get_json()["authorize_url"]
        assert url.startswith("https://robinhood.com/oauth?")
        state = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["state"]
        cb = c.get(f"/api/portfolio/oauth/callback?code=secret-code-9&state={state}")
        assert cb.status_code == 303 and cb.headers["Location"].endswith("/#holdings")
        assert "secret-code" not in cb.headers["Location"]
        assert c.get("/api/portfolio/status").get_json()["state"] == "connected"
        accts = c.get("/api/portfolio/accounts").get_json()["accounts"]
        r = c.post("/api/portfolio/refresh", json={"account": accts[0]["handle"], "analyze": False}, headers=H)
        assert r.status_code == 200 and r.get_json()["portfolio"]["positions"]
        diag = c.get("/api/portfolio/diagnostics").get_json()
        everything = json.dumps([r.get_json(), diag, c.get("/api/portfolio/status").get_json()]) + caplog.text
        for secret in (ACCESS_TOKEN, REFRESH_TOKEN, ACCOUNT_NUMBER, "9A99990001", "secret-code-9"):
            assert secret not in everything
        assert c.post("/api/portfolio/disconnect", headers=H).get_json()["state"] == "disconnected"
        assert c.get("/api/portfolio").get_json()["portfolio"] is None

    def test_localhost_page_uses_loopback_ip_redirect(self, client):
        c, prov = client
        r = c.post("/api/portfolio/connect", headers={**H, "Host": "localhost:5500",
                                                       "Origin": "http://localhost:5500"})
        assert r.status_code == 200
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(r.get_json()["authorize_url"]).query)
        assert q["redirect_uri"] == ["http://127.0.0.1:5500/api/portfolio/oauth/callback"]
        r = c.post("/api/portfolio/connect", headers={**H, "Host": "127.0.0.1:5501"})
        q = parse_qs(urlsplit(r.get_json()["authorize_url"]).query)
        assert q["redirect_uri"] == ["http://127.0.0.1:5501/api/portfolio/oauth/callback"]

    def test_callback_error_reason_reaches_status(self, client):
        c, _ = client
        c.post("/api/portfolio/connect", headers=H)
        cb = c.get("/api/portfolio/oauth/callback?error=server_error&error_description=try+later")
        assert cb.status_code == 303
        err = c.get("/api/portfolio/status").get_json()["last_error"]
        assert err["code"] == "AUTH_DENIED" and "server_error" in err["message"]

    def test_refresh_requires_valid_handle(self, client):
        c, _ = client
        assert c.post("/api/portfolio/refresh", json={"account": ACCOUNT_NUMBER}, headers=H).status_code == 400

    def test_not_connected_is_401_not_sample_data(self, client):
        c, _ = client
        r = c.get("/api/portfolio/accounts")
        assert r.status_code == 401 and "accounts" not in r.get_json()


def test_ui_fixture_matches_build_view():
    """The JS tests render this fixture; it must be what build_view really emits."""
    import importlib.util
    tools = Path(__file__).resolve().parents[1] / "webapp" / "tests" / "tools"
    spec = importlib.util.spec_from_file_location("mpf", tools / "make_my_portfolio_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    def rounded(v):
        # Float sums differ in the last bits across platforms/BLAS builds; the
        # fixture contract is the view's content, not its ULPs.
        if isinstance(v, float):
            return float(f"{v:.9g}")
        if isinstance(v, dict):
            return {k: rounded(x) for k, x in v.items()}
        if isinstance(v, list):
            return [rounded(x) for x in v]
        return v

    assert rounded(json.loads(mod.FIXTURE.read_text())) == rounded(mod.build()), \
        "Regenerate: python webapp/tests/tools/make_my_portfolio_fixture.py"


def test_resolver_trace_is_audit_not_argument():
    dec = decision()
    dec["reasons"] = ["Composite score 60.7 → candidate ACCUMULATE", "Margins expanding"]
    g = gd.guide_position(pctx(), ctx(), dec, case_for=["Services mix lifts margins"],
                          case_against=["Hardware cycle is maturing"])
    assert g.supports == ["Margins expanding", "Services mix lifts margins"]
    assert g.trace == ["Composite score 60.7 → candidate ACCUMULATE"]
    assert "→" not in g.key_reason
    assert "Hardware cycle is maturing" in g.risks


def test_forged_callback_does_not_disturb_a_live_connection():
    prov, _ = connected_provider()
    for params in ({"code": "x", "state": "forged"}, {"error": "access_denied"}):
        with pytest.raises(ProviderError):
            prov.complete_connect(params)
        assert prov.state() is ConnectionState.CONNECTED
        assert prov.list_accounts()
