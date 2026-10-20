"""My Portfolio: a held name is analysed as held (2026-10-19 review, item 17).

The Research engine never knew the account owned the ticker, so a neutral score read
"HOLD_EXISTING but no existing position -> WATCHLIST" for VOO, VXUS and JNJ, and the
portfolio wrapper later relabelled it Hold. The owner flag now travels with the
analysis, and an owned result never shares a cache entry with a not-owned one.
"""
from __future__ import annotations

import inspect

from _paths import ensure_paths

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
import TradingAnalysis_ELITE as ta  # noqa: E402
from engine.decision_engine import DecisionInputs  # noqa: E402


def test_analyze_takes_ownership_and_records_it():
    sig = inspect.signature(ta.QuantaEliteAnalyzer.analyze)
    assert "owned" in sig.parameters and sig.parameters["owned"].default is False
    src = inspect.getsource(ta.QuantaEliteAnalyzer.analyze)
    assert "result.has_existing_position = bool(owned)" in src


def test_neutral_score_on_a_held_name_is_hold_without_the_not_owned_trace():
    from engine.decision_engine import resolve_final_action
    held = resolve_final_action(_inputs(owned=True))
    not_held = resolve_final_action(_inputs(owned=False))
    assert "HOLD_NOT_OWNED" not in held.override_codes
    assert not_held.final_action.value == "WATCHLIST" and "HOLD_NOT_OWNED" in not_held.override_codes
    assert held.final_action.value == "HOLD_EXISTING"


def _inputs(owned):
    # A neutral score (HOLD zone) with levels that pass every gate, so only ownership differs.
    return DecisionInputs(ticker="VOO", composite_score=50.0, risk_grade="B", risk_reward_ratio=2.5,
                          current_price=100.0, stop_loss=92.0, target_price=120.0,
                          has_existing_position=owned)


def test_owned_analysis_has_its_own_cache_identity_and_is_not_shared_with_picks():
    src = inspect.getsource(webapp._run_analysis)
    assert 'cache_ticker = f"{cache_ticker}#held"' in src
    assert "if not owned:" in src and "_rc.put(" in src
    assert "owned=True" in inspect.getsource(webapp._portfolio_analyze)
    # A held-name entry and a plain entry for the same ticker are different keys.
    plain = webapp._analysis_cache_key(webapp._cache_identity("VOO", "US"), "full", 5000.0)
    held = webapp._analysis_cache_key(webapp._cache_identity("VOO", "US") + "#held", "full", 5000.0)
    assert plain != held
