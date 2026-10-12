"""I5: the minimum position the page states is the one the sizer applies.

The page said "$100 minimum position" for a $2,500 scan, while the sizer's
effective minimum there is min(MIN_POSITION_SIZE, max(MIN_POSITION_FLOOR,
MIN_POSITION_PCT x capital)) = $50 (A06). The scan now publishes the effective
value for its own capital.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import thresholds as T  # noqa: E402


def _run(monkeypatch, capital):
    from test_scan_accounting import _fake_engine_module, _watch
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", _fake_engine_module([_watch("W1", 1)]))
    job = "i5-test"
    webapp._jobs[job] = {"type": "stock-picks", "status": "running", "progress": 0, "started_at": time.time(),
                         "worker_active": True}
    params = dict(capital=float(capital), market="US", scan_type="custom", custom_tickers=None,
                  risk_profile="balanced", max_positions=5, diversify=True, safe_mode=False)
    try:
        webapp._run_stock_picks(job, params)
        return webapp._jobs[job]["result"]
    finally:
        webapp._jobs.pop(job, None)


@pytest.mark.parametrize("capital", [2_500, 100_000])
def test_the_scan_publishes_the_effective_minimum_for_its_capital(monkeypatch, capital):
    res = _run(monkeypatch, capital)
    expected = min(T.SIZING.MIN_POSITION_SIZE, max(T.SIZING.MIN_POSITION_FLOOR, T.SIZING.MIN_POSITION_PCT * capital))
    assert res["min_position_effective"] == pytest.approx(expected)
    assert res["min_position_effective"] == pytest.approx(sp.TradingConfig.effective_min_position_size(capital))


@pytest.mark.parametrize("capital", [2_500, 100_000])
def test_the_scan_publishes_its_book_limits_at_its_capital(monkeypatch, capital):
    """P4: risk tiles colour from the limits the book applied, not fixed cut-offs."""
    lim = _run(monkeypatch, capital)["book_limits"]
    assert lim["position_pct"] == pytest.approx(sp.TradingConfig.effective_max_position_pct(capital))
    assert lim["sector_pct"] == pytest.approx(sp.TradingConfig.effective_max_sector_pct(capital))
    assert lim["stop_risk_pct"] == pytest.approx(sp.TradingConfig.MAX_PORTFOLIO_RISK)
