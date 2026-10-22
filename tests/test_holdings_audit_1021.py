"""Holdings audit, 2026-10-21: one guard per fixed finding outside guidance.py.

guidance.py's own fixes (binding reason, one Hold label, limits on every holding,
red-flag disclosure, data-gap No calls, attention items) are covered in
test_my_portfolio.py::TestGuidance. These cover the engine, analytics, service
and journal halves.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from engine import thresholds as T  # noqa: E402
from engine.blockers import RED_FLAG_CODES, holder_rule, is_blocking  # noqa: E402
from engine.decision_engine import resolve_final_action  # noqa: E402
from engine.ladder import Holding as LadderHolding, LadderInputs, decide  # noqa: E402
from engine.risk_overrides import measured_red_flags  # noqa: E402
from engine.types import DecisionInputs, FinalAction  # noqa: E402
from portfolio import analytics as an  # noqa: E402
from portfolio.journal import DecisionJournal  # noqa: E402
from portfolio.models import AccountSummary, Portfolio, Position, TaxLot  # noqa: E402
from portfolio.service import _future_date, _sale_detail, is_fund_result, quote_freshness  # noqa: E402


def _inputs(score, **kw):
    base = dict(ticker="X", composite_score=score, risk_grade="C", risk_reward_ratio=2.0, current_price=100.0,
                stop_loss=90.0, target_price=110.0, has_existing_position=True)
    base.update(kw)
    return DecisionInputs(**base)


# ── F2: holder red flags at any score; exit review reachable ────────────────

def test_every_red_flag_is_measured_at_a_bearish_score_and_none_short_circuits():
    di = _inputs(30.0, target_price=70.0, bearish_reversal=True, dcf_overvaluation_pct=60.0,
                 analyst_downside_pct=40.0, heavy_insider_selling=True)
    assert measured_red_flags(di) == ["TARGET_BELOW_ENTRY", "DCF_OVERVALUATION", "ANALYST_DOWNSIDE",
                                      "HEAVY_INSIDER_SELLING", "BEARISH_REVERSAL"]
    r = resolve_final_action(di)
    assert r.final_action is FinalAction.AVOID and r.red_flags == measured_red_flags(di)
    lad = decide(LadderInputs(r.final_action, 30.0, "C", r.override_codes, "NO_EDGE", red_flags=r.red_flags),
                 LadderHolding(0.05))
    assert (lad.holder_action, lad.holder_basis) == ("EXIT", "SCORE_EXIT_CORROBORATED")
    assert r.contract["holder_action"] == "EXIT"


def test_red_flags_never_change_the_new_money_action():
    di = _inputs(60.0, dcf_overvaluation_pct=60.0)
    with_flags = resolve_final_action(di)
    assert "DCF_OVERVALUATION" in with_flags.red_flags
    lad = decide(LadderInputs(with_flags.final_action, 60.0, "C", with_flags.override_codes, "NO_EDGE",
                              red_flags=with_flags.red_flags))
    lad0 = decide(LadderInputs(with_flags.final_action, 60.0, "C", with_flags.override_codes, "NO_EDGE"))
    assert (lad.action, lad.new_money) == (lad0.action, lad0.new_money)
    assert lad.holder_action == "HOLD" and lad.holder_flags == ["DCF_OVERVALUATION"]


def test_no_red_flags_on_invalid_inputs_and_flags_are_exit_corroboration():
    assert measured_red_flags(_inputs(30.0, is_valid=False, heavy_insider_selling=True)) == []
    assert holder_rule(FinalAction.AVOID, 30.0, [], red_flags=["HEAVY_INSIDER_SELLING"])["action"] == "EXIT"
    assert {"TARGET_BELOW_ENTRY", "HEAVY_INSIDER_SELLING"} <= RED_FLAG_CODES


# ── F9: a foreign filer is a standing coverage gap, with its own code ───────

def test_foreign_filer_event_gap_has_its_own_blocking_code():
    from test_scanner_accuracy_1019 import _FakeSec, _check, _subs
    ev = _check(_FakeSec(_subs(("20-F", "2026-04-15", "", "annual.htm"), ("6-K", "2026-09-03", "", "sixk.htm"))))
    assert ev.status == "unavailable" and ev.code == ce.EVENT_CHECK_FOREIGN_FILER
    assert is_blocking(ce.EVENT_CHECK_FOREIGN_FILER) and is_blocking("PRICE_HISTORY_TRUNCATED")
    assert not is_blocking("BUY_NOW_NO") and not is_blocking("RISK_GRADE_D_CAP")


# ── F3: a 6-month fallback history never funds and is flagged ───────────────

def test_truncated_price_history_is_flagged_and_never_funded(monkeypatch):
    import TradingAnalysis_ELITE as ta
    from engine import decision as dc
    fresh = dc.Freshness("fresh", "2026-10-21", 0)
    monkeypatch.setattr(dc, "assess_price_freshness", lambda *_a, **_k: fresh)
    r = ta.AnalysisResult(ticker="XYZ", current_price=100, final_action="BUY",
                          scores=ta.ScoreBreakdown(composite_score=68), risk=ta.RiskMetrics(risk_grade="C"))
    r.history_range = "6mo/1d"
    d = ta.decision_for_analysis(r)
    assert d.action is FinalAction.WATCHLIST and "PRICE_HISTORY_TRUNCATED" in d.blocking_codes
    assert d.history_range == "6mo/1d" and d.to_dict()["history_range"] == "6mo/1d"
    r2 = ta.AnalysisResult(ticker="XYZ", current_price=100, final_action="BUY",
                           scores=ta.ScoreBreakdown(composite_score=68), risk=ta.RiskMetrics(risk_grade="C"))
    r2.history_range = "2y/1d"
    r2.holder_red_flags = ["DCF_OVERVALUATION"]
    d2 = ta.decision_for_analysis(r2)
    assert "PRICE_HISTORY_TRUNCATED" not in d2.blocking_codes and d2.red_flags == ["DCF_OVERVALUATION"]


# ── F3: quote age, reconciliation, stale earnings ────────────────────────────

def test_a_same_session_quote_read_a_while_ago_is_delayed_not_void():
    now = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)          # 11:30 ET, a Wednesday
    q = quote_freshness("2026-10-21T15:04:58Z", now)
    assert q["status"] == "delayed" and q["text"].startswith("Price from 11:04 AM ET")
    assert quote_freshness("2026-10-21T15:20:00Z", now)["status"] == "live"
    assert quote_freshness("2026-10-20T19:59:00Z", now)["status"] == "stale"


def test_reconciliation_allows_intraday_price_drift_but_not_after_the_close():
    acct = AccountSummary("h", "Individual", portfolio_value=50_000.00, cash=2_000.00)
    pf = Portfolio("robinhood", acct, [Position("X", 100, last_price=480.0037)], "now")
    assert abs(pf.reconciliation()["difference"] - 0.37) < 0.01
    assert pf.reconciliation()["status"] == "mismatch"
    assert pf.reconciliation(session_open=True)["status"] == "ok"
    acct.portfolio_value = 51_000.00                                   # $1,000 off: still a mismatch
    assert pf.reconciliation(session_open=True)["status"] == "mismatch"


def test_extended_hours_count_as_prices_moving():
    from portfolio.service import prices_moving
    assert prices_moving(datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc))      # 4:30 PM ET, after the close
    assert not prices_moving(datetime(2026, 10, 22, 1, 30, tzinfo=timezone.utc))   # 9:30 PM ET
    assert not prices_moving(datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc))   # Saturday


def test_a_past_next_earnings_date_is_stale_not_upcoming():
    assert _future_date("2026-10-13", date(2026, 10, 21)) is None
    assert _future_date("2026-10-28", date(2026, 10, 21)) == "2026-10-28"


def test_fund_detection():
    assert is_fund_result({"quote_type": "ETF"}) and is_fund_result({"quote_type": "MUTUALFUND"})
    assert not is_fund_result({"quote_type": "EQUITY", "is_etf": False}) and not is_fund_result(None)


# ── F6: risk share per holding; funds out of the sector driver ───────────────

def test_risk_share_sums_to_one_and_names_the_volatile_holding():
    rng = np.random.default_rng(3)
    mkt = rng.normal(0, 0.01, 250)
    rets = pd.DataFrame({"AAA": mkt * 3 + rng.normal(0, 0.02, 250), "BBB": mkt + rng.normal(0, 0.005, 250),
                         "CCC": rng.normal(0, 0.005, 250)})
    hs = [an.Holding("AAA", 1, 10_000, 100.0, "Tech"), an.Holding("BBB", 1, 30_000, 100.0, "Staples"),
          an.Holding("CCC", 1, 30_000, 100.0, "Utilities")]
    rs = an.compute_metrics(hs, 0.0, 70_000, returns=rets).volatility["risk_share"]
    assert sum(rs.values()) == pytest.approx(1.0)
    assert rs["AAA"] > 10_000 / 70_000                                   # a small, volatile name carries more risk


def test_fund_style_buckets_do_not_drive_the_sector_risk_level():
    hs = [an.Holding("VOO", 1, 60_000, 100.0, "Large Blend", beta=1.0, is_fund=True),
          an.Holding("KO", 1, 40_000, 100.0, "Consumer Defensive", beta=0.6)]
    m = an.compute_metrics(hs, 0.0, 100_000)
    assert {s["sector"]: s["fund"] for s in m.sectors} == {"Large Blend": True, "Consumer Defensive": False}
    assert not any("Large Blend" in d for d in m.risk["drivers"])


# ── F12: trim arithmetic ─────────────────────────────────────────────────────

def test_sale_detail_uses_average_cost_and_oldest_lots_first():
    p = Position("AAA", 25, average_cost=150.0, last_price=300.0,
                 tax_lots=[TaxLot(2, 260.0, "2025-03-03", "long"), TaxLot(23, 140.0, "2023-01-02", "long"),
                           TaxLot(0.1, 290.0, "2026-10-01", "short")])
    d = _sale_detail(p, 3000.0)
    assert d["shares"] == pytest.approx(10.0)
    # With lots known, the total is what the lots realise, so the parts add up to it.
    assert d["gain_long"] == pytest.approx(10 * (300 - 140.0)) and d["gain_short"] == 0
    assert d["gain"] == pytest.approx(d["gain_long"] + d["gain_short"])
    # Without lots, the broker's average cost.
    bare = Position("AAA", 25, average_cost=150.0, last_price=300.0)
    assert _sale_detail(bare, 3000.0)["gain"] == pytest.approx(10 * (300 - 150.0))


# ── F10: the journal records calls, not data gaps or renamed labels ─────────

def _row(symbol="AAA", action="Hold", code="HOLD_CLEAR", kind="investment", status="ok"):
    return {"symbol": symbol, "broker": {"last_price": 10.0},
            "guidance": {"status": status, "action": action, "stance": "Neutral",
                         "basis": {"code": code, "kind": kind, "text": "t"}},
            "analysis": {"decision": {"score": 50}}}


def test_journal_skips_limitation_calls_and_reads_old_labels_as_the_same_call(tmp_path):
    j = DecisionJournal(tmp_path / "j.json")
    assert j.record([_row(code="ACCOUNT_DATA_UNRECONCILED", kind="account_data")], "2026-10-19") == 0
    j.record([_row(action="Hold / No New Money", code="LOW_REWARD_RISK", kind="investment"),
              _row("BBB", action="Trim", code="RISK_GRADE_F", kind="risk")], "2026-10-19")
    # The rename to Hold / Review Risk is not a change of call.
    assert j.record([_row(), _row("BBB", action="Review Risk", code="RISK_GRADE_F", kind="risk")], "2026-10-21") == 0
    assert j.record([_row(action="Trim", code="CONCENTRATION", kind="concentration")], "2026-10-22") == 1
    assert j.changes()[0]["from_action"] == "Hold"
    assert j.last_by_symbol()["AAA"]["action"] == "Trim"


def test_reading_the_view_does_not_write_the_journal():
    from test_my_portfolio import connected_provider, decision, fake_analyze, research
    from portfolio.service import PortfolioService

    class Insights:
        recorded = 0

        def record_journal(self, pf):
            Insights.recorded += 1
            return 0

        def last_calls(self):
            return {}

        def journal_info(self):
            return {"enabled": True}

    prov, _ = connected_provider()
    svc = PortfolioService(prov, fake_analyze({"AAPL": research(decision())}))
    svc.insights = Insights()
    handle = next(a["handle"] for a in svc.accounts() if a["can_trade_via_agent"] is False)
    svc.refresh(handle, start_thread=False)
    after_job = Insights.recorded
    assert after_job == 1, "the finished job journals once"
    for _ in range(3):
        svc.view()
    assert Insights.recorded == after_job, "a GET of the view never writes"


def test_new_thresholds_live_in_config():
    P = T.PORTFOLIO
    assert (P.QUOTE_LIVE_MINUTES, P.EARNINGS_HORIZON_DAYS, P.ATTENTION_MAX) == (20, 14, 3)
    assert 0 < P.RECON_SESSION_TOLERANCE_PCT < 0.001
