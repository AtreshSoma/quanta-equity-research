"""Audit F7: the ladder wired into both engines, My Portfolio and the session cache.

- The Research engine applies the corporate-event gate, so the Research page,
  deep confirmation and My Portfolio read the same event-aware verdict.
- A holder of a name under a pending cash deal with a thin spread gets a Sell
  review that says why; an unavailable or failing check never hides a holding
  or changes what a holder is told.
- The scanner's book bans only grade F (grade D at half size).
- One research call per (ticker, session, mode), shared by deep confirmation
  and My Portfolio.
"""
from __future__ import annotations

import types

import pytest

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from engine import research_cache as rc  # noqa: E402
from engine import thresholds as T  # noqa: E402
from engine.types import FinalAction  # noqa: E402
from portfolio import guidance as gd  # noqa: E402
from test_my_portfolio import ctx, decision, pctx  # noqa: E402

THIN = {"status": "flagged", "code": "PENDING_ACQUISITION", "deal_price": 102.5, "spread_pct": 0.009,
        "text": "Pending cash acquisition at $102.50 (merger agreement 2026-08-03; DEFM14A filed 2026-09-08 on SEC EDGAR)."}


# ── Research engine: the event gate ──────────────────────────────────────────

def _result(final_action="BUY", price=101.6):
    import TradingAnalysis_ELITE as ta
    r = types.SimpleNamespace(final_action=final_action, final_action_codes=[], final_action_reasons=[],
                              current_price=price, is_invalid=False, event_check=None, ticker="EXDX",
                              signal=ta.Signal.BUY)
    return ta, r


@pytest.mark.real_event_gate
def test_research_engine_publishes_avoid_for_a_pending_acquisition(monkeypatch):
    ta, r = _result()
    monkeypatch.setattr(ce, "check_many", lambda symbols, **_: {s: ce.EventCheck(**{
        k: v for k, v in THIN.items() if k in ce.EventCheck.__dataclass_fields__}) for s in symbols})
    ta._apply_corporate_events(r, "EXDX")
    assert r.final_action == FinalAction.AVOID.value
    assert r.final_action_codes[0] == "PENDING_ACQUISITION"
    assert r.final_action_reasons[-1].startswith("Pending cash acquisition at $102.50")
    assert r.event_check["status"] == "flagged"


@pytest.mark.real_event_gate
def test_research_engine_fails_closed_when_the_check_raises(monkeypatch):
    ta, r = _result()

    def boom(symbols, **_):
        raise TimeoutError("SEC did not answer")
    monkeypatch.setattr(ce, "check_many", boom)
    ta._apply_corporate_events(r, "EXDX")
    assert r.final_action == FinalAction.WATCHLIST.value
    assert r.event_check["status"] == "unavailable"


# ── My Portfolio ──────────────────────────────────────────────────────────────

def test_holder_sees_a_sell_review_when_the_deal_spread_is_thin():
    dec = decision("AVOID", 66.0, holder="EXIT", blockers=[{"code": "PENDING_ACQUISITION", "text": THIN["text"]}])
    dec["event_check"] = dict(THIN)
    g = gd.guide_position(pctx(weight=0.05), ctx(), dec)
    assert g.status == "ok" and g.action == gd.PA_EXIT
    assert g.basis["code"] == "PENDING_ACQUISITION_SPREAD"
    assert "$102.50" in g.basis["text"] and "0.9%" in g.basis["text"]


def test_an_unavailable_event_check_never_hides_a_holding_or_changes_its_action():
    """Item 4(a): fail closed for NEW money only; the holding is still guided."""
    unavailable = ce._unavailable("SEC EDGAR did not answer").to_dict()
    base = decision("WATCHLIST", 60.0)
    dec = dict(base, blockers=[{"code": "EVENT_CHECK_UNAVAILABLE", "text": unavailable["text"]}],
               event_check=unavailable, new_money="WATCH")
    g0 = gd.guide_position(pctx(weight=0.05), ctx(), base)
    g1 = gd.guide_position(pctx(weight=0.05), ctx(), dec)
    assert g1.status == "ok", "the holding is still shown with a call"
    assert g1.existing == g0.existing, "a holder's instruction does not depend on the event service"


# ── Scanner book: grade D allowed, grade F banned ────────────────────────────

def test_grade_d_half_size_book(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick
    assert T.BOOK.BANNED_GRADES == ("F",)
    assert T.SIZING.RISK_GRADE_SIZE_MULTIPLIERS["D"] == 0.5
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system()
    d_pick, f_pick = _pick("DDD", "Tech", 8.2, seed=1), _pick("FFF", "Health", 8.1, seed=2)
    d_pick.risk_grade, f_pick.risk_grade = "D", "F"
    sysm.scan_results = [d_pick, f_pick, _pick("BBB", "Energy", 7.9, seed=3)]
    out = {r.ticker for r in sysm.get_top_recommendations(max_positions=5)}
    assert "DDD" in out and "FFF" not in out


# ── one research call per (ticker, session, mode) ────────────────────────────

def test_session_cache_hit_and_miss():
    rc.clear()
    calls = []

    def compute(tag):
        def _run():
            calls.append(tag)
            return {"tag": tag}
        return _run

    a = rc.get_or_compute("MSFT", "full", compute("a"), session="2026-10-01")
    b = rc.get_or_compute("MSFT", "full", compute("b"), session="2026-10-01")
    assert a is b and calls == ["a"], "same ticker, session and mode: one call"
    rc.get_or_compute("MSFT", "full", compute("c"), session="2026-10-02")
    rc.get_or_compute("MSFT", "fast", compute("d"), session="2026-10-02")
    assert calls == ["a", "c", "d"], "a new session or another mode is a new call"
    rc.put("GOOGL", "full", {"tag": "portfolio"}, session="2026-10-02")
    assert rc.get("GOOGL", "full", session="2026-10-02") == {"tag": "portfolio"}


def test_one_research_mode_for_both_pages():
    assert T.RESEARCH.MODE in ("full", "fast")
