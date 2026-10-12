"""A08: chase and India rules come from engine/thresholds.py and change behaviour.

Executed checks on synthetic providers: an India listing (.NS suffix) is held
to the UNIVERSE.INDIA_* screens, and the early-momentum watchlist uses
CHASE.EARLY_MOMENTUM_MIN_SIGNALS. The literal guard lives in
test_thresholds_single_source.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import thresholds as T  # noqa: E402


def _india_outcome(monkeypatch):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {})
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type="custom", custom_tickers=["SYNA.NS"])
    return eng.scan_accounting()


def test_india_screens_follow_config(monkeypatch):
    base = _india_outcome(monkeypatch)
    assert "price_outside_range" not in base["rule_rejected"]["by_reason"]
    monkeypatch.setattr(T.UNIVERSE, "INDIA_MIN_PRICE", 10_000.0)
    raised = _india_outcome(monkeypatch)
    assert raised["rule_rejected"]["by_reason"].get("price_outside_range") == 1


def test_india_volume_and_cap_screens_follow_config(monkeypatch):
    monkeypatch.setattr(T.UNIVERSE, "INDIA_MIN_AVG_VOLUME", 10**9)
    assert _india_outcome(monkeypatch)["rule_rejected"]["by_reason"].get("low_volume") == 1


def test_early_momentum_watchlist_follows_config(monkeypatch):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _pick
    a, b = _pick("EM4", "Tech", 6.0, signal="HOLD", seed=1), _pick("EM3", "Tech", 6.0, signal="HOLD", seed=2)
    a.final_action = b.final_action = "WATCHLIST"
    a.early_momentum_score, b.early_momentum_score = T.CHASE.EARLY_MOMENTUM_MIN_SIGNALS, T.CHASE.EARLY_MOMENTUM_MIN_SIGNALS - 1
    for r in (a, b):
        sp.apply_allocation(r, 0.0, 100_000.0)
    real = acc._fake_engine_module

    def scanned_but_unpublished(recs, universe=8):
        mod = real(recs, universe)
        mod.QuantaEliteTradingSystem.get_top_recommendations = lambda self, **kw: []
        return mod

    monkeypatch.setattr(acc, "_fake_engine_module", scanned_but_unpublished)
    res = acc._run_worker(monkeypatch, [a, b])
    assert [e["ticker"] for e in res["early_momentum"]] == ["EM4"]
    monkeypatch.setattr(T.CHASE, "EARLY_MOMENTUM_MIN_SIGNALS", T.CHASE.EARLY_MOMENTUM_MIN_SIGNALS - 1)
    res = acc._run_worker(monkeypatch, [a, b])
    assert sorted(e["ticker"] for e in res["early_momentum"]) == ["EM3", "EM4"], "the threshold is read from config"
