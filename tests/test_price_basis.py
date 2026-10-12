"""P5: every level set states the price it was built on, and the pick's "why"
is one plain sentence.

AMZN's scanner levels were built on the scan fetch's 2026-10-02 close (251.52)
and the binding Research levels on the Research fetch's (252.02); nothing on the
card said which. Its "why" was the decision core's trace "Composite score 63.8
-> candidate ACCUMULATE". Synthetic names only.
"""
from __future__ import annotations

import sys
from pathlib import Path

from _paths import ensure_paths

ensure_paths()

from test_binding_levels import _hst_like_pick, _install_research  # noqa: E402


def test_both_level_sets_carry_their_price_basis(monkeypatch):
    from test_elite_pro_review2_fixes import _bare_system
    _install_research(monkeypatch, "BUY", (22.58, 21.86, 24.47, 2.4, 63))
    res_mod = sys.modules["TradingAnalysis_ELITE"]
    cls = type(res_mod.QuantaEliteAnalyzer().analyze("HST"))
    cls.current_price, cls.market_timestamp = 22.58, "2026-10-02T00:00:00"
    pick = _hst_like_pick()
    pick.market_timestamp = "2026-10-02T00:00:00"
    out = _bare_system()._deep_confirm_final_picks([pick])[0]
    assert out.binding_levels["price_basis"] == {"price": 22.58, "session": "2026-10-02", "fetch": "Research"}
    assert out.scanner_levels["price_basis"] == {"price": 22.51, "session": "2026-10-02", "fetch": "scan"}


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    return webapp


def test_an_arrow_trace_becomes_one_plain_reason_from_the_score_bands():
    from engine import thresholds as T
    why = _app()._plain_why("Composite score 63.8 → candidate ACCUMULATE",
                            {"label": "Accumulate", "score": 63.8}, funded=True)
    assert "→" not in why
    assert why == (f"Score 64 of 100 is in the Accumulate band ({T.SCORE.ACCUMULATE * T.SCORE.TO_100:g} to {T.SCORE.GOOD * T.SCORE.TO_100:g}); "
                   "it passed every screening and risk rule")


def test_a_plain_reason_is_kept_as_written():
    text = "Corporate-event check unavailable: no SEC contact is configured. Held at Watchlist until it can be checked."
    assert _app()._plain_why(text, {"label": "Watchlist", "score": 70.0}, funded=False) == text


def test_the_risk_checks_table_reason_is_plain_too(monkeypatch):
    """Seen in the browser check: "Additional Risk Checks" printed the arrow trace."""
    from test_scan_accounting import _fake_engine_module, _watch
    w = _watch("W1", 1)
    mod = _fake_engine_module([w])
    base = mod.QuantaEliteTradingSystem

    class Eng(base):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._deep_confirm_records = [{
                "ticker": "W1", "scanner_action": "BUY", "deep_action": "WATCHLIST", "final_action": "WATCHLIST",
                "downgrade_flag": True, "reason": "Heavy insider selling → WATCHLIST (no new money)",
                "reason_codes": ["HEAVY_INSIDER_SELLING"], "allocation_before": 0.0, "allocation_after": 0.0}]
    mod.QuantaEliteTradingSystem = Eng
    webapp = _app()
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    job = "p5-table"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "started_at": 0, "worker_active": True}
    try:
        webapp._run_stock_picks(job, dict(capital=100_000.0, market="US", scan_type="custom", custom_tickers=None,
                                          risk_profile="balanced", max_positions=2, diversify=True, safe_mode=False))
        res = webapp._jobs[job]["result"]
    finally:
        webapp._jobs.pop(job, None)
    rows = res["deep_confirmation"]["records"]
    assert rows and all("→" not in r["reason"] for r in rows), [r["reason"] for r in rows]
