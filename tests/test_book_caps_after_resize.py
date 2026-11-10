"""The book's capital and sector limits hold after the Research re-size.

Selection sizes each pick on the scanner's stop and checks the capital the
market read allows and the sector cap. The Research check then re-sizes a
funded pick on the Research stop (a tighter stop buys more, up to the position
cap). Nothing checked the book again, so with a Research stop at 3% against a
scanner stop at 8% a ten-name book published $60,000 against $50,000 allowed,
and one sector reached 43% against a 25% cap.
"""
from __future__ import annotations

import sys
import types

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from test_elite_pro_review2_fixes import _bare_system, _pick  # noqa: E402

RESEARCH_STOP_PCT = 0.03      # the scanner picks below carry an 8% stop


def _install_research(monkeypatch):
    class _TS:
        levels_available = True
        entry_price = 100.0
        stop_loss = 100.0 * (1 - RESEARCH_STOP_PCT)
        horizon_target = 108.0
        risk_reward_ratio = 2.2
        horizon_days = 63
        target_base = 140.0
        valuation_horizon_days = 252

    class _Res:
        final_action = "BUY"
        final_action_reasons: list = []
        final_action_codes: list = []
        data_status = "ok"
        data_status_reason = ""
        trade_setup = _TS()
        technical = types.SimpleNamespace(atr=1.0)
        current_price = 100.0

    class _Deep:
        def analyze(self, ticker, account_size=0, fast_mode=False, include_insider=False):
            return _Res()

    mod = types.ModuleType("TradingAnalysis_ELITE")
    mod.QuantaEliteAnalyzer = _Deep
    from engine.types import DecisionInputs
    mod.build_reconciliation_inputs = lambda res: DecisionInputs(
        ticker="X", composite_score=0, risk_grade="B", risk_reward_ratio=0,
        current_price=100, stop_loss=0, target_price=0)
    monkeypatch.setitem(sys.modules, "TradingAnalysis_ELITE", mod)
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "_apply_event_gate",
                        lambda self, rows, **_k: rows, raising=False)
    from engine import research_cache
    from tracking import ledger
    monkeypatch.setattr(ledger, "record_decisions", lambda *a, **k: None)
    monkeypatch.setattr(research_cache, "get_or_compute", lambda _t, _m, run: run())


def _book(monkeypatch, capital, exposure, sectors, n, max_positions):
    _install_research(monkeypatch)
    sysm = _bare_system(capital)
    sysm.market_regime = sp.MarketRegime(regime_type="NEUTRAL", recommended_exposure=exposure)
    picks = [_pick(f"T{i}", sectors[i % len(sectors)], 8.0 - i * 0.01, stop=92.0, seed=i + 1, capital=capital)
             for i in range(n)]
    for p in picks:
        p.price_target, p.risk_reward_ratio = 130.0, 3.0
    sysm.scan_results = picks
    before = {p.ticker: float(p.allocation) for p in picks}
    return sysm, before, sysm.get_top_recommendations(max_positions=max_positions)


def test_research_resize_is_real_in_this_fixture(monkeypatch):
    """Guard for the tests below: the Research stop does size the first pick up."""
    _sysm, before, out = _book(monkeypatch, 100_000.0, 1.0, ["A", "B", "C", "D", "E"], 5, 5)
    assert out[0].allocation > before[out[0].ticker]


def test_deployed_capital_stays_inside_the_market_read(monkeypatch):
    capital, exposure = 100_000.0, 0.5
    _sysm, _before, out = _book(monkeypatch, capital, exposure, ["A", "B", "C", "D", "E"], 10, 10)
    deployed = sum(float(p.allocation or 0.0) for p in out)
    assert deployed <= capital * exposure + 1e-6
    assert deployed > 0


def test_sector_cap_holds_after_the_resize(monkeypatch):
    capital = 100_000.0
    _sysm, _before, out = _book(monkeypatch, capital, 1.0, ["A", "A", "A", "B", "C"], 12, 12)
    cap = sp.TradingConfig.effective_max_sector_pct(capital)
    by_sector: dict = {}
    for p in out:
        by_sector[p.sector] = by_sector.get(p.sector, 0.0) + float(p.allocation or 0.0)
    assert by_sector["A"] > 0
    assert all(v / capital <= cap + 1e-9 for v in by_sector.values()), by_sector


def test_a_pick_cut_to_nothing_is_not_published_as_a_buy(monkeypatch):
    capital = 100_000.0
    sysm, _before, out = _book(monkeypatch, capital, 0.5, ["A", "B", "C", "D", "E"], 10, 10)
    unfunded = [p for p in out if float(p.allocation or 0.0) <= 0]
    assert unfunded, "the fixture is meant to leave at least one pick without room"
    for p in unfunded:
        assert p.final_action == "WATCHLIST"
        assert p.decision["action"] == "WATCHLIST"
        assert p.shares == 0
    # The reason names the limit that had no room left.
    assert any("no minimum position fits" in (sysm._selection_skips.get(p.ticker) or {}).get("text", "")
               for p in unfunded)


def test_a_trimmed_pick_says_why_and_keeps_whole_shares(monkeypatch):
    capital = 100_000.0
    _sysm, _before, out = _book(monkeypatch, capital, 1.0, ["A", "A", "A", "B", "C"], 12, 12)
    trimmed = [p for p in out if any("Size cut to stay inside" in r for r in p.final_action_reasons)]
    assert trimmed
    for p in trimmed:
        assert p.allocation == pytest.approx(p.shares * p.entry_price)
        assert p.position_pct == pytest.approx(p.allocation / capital * 100, abs=0.01)
