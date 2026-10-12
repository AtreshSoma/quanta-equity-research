"""Portfolio exposure must be real, complete and binding.

Audit finding F02: deep-dive portfolio guidance was not grounded in enforceable
holdings.

  * Holdings were reconstructed from BUY research records at an assumed 100
    shares, so research observations became a fake account state.
  * The caller supplied positions carrying a dollar ``value`` while the risk
    manager summed a ``weight`` key, so every sector exposure came out zero.
  * The proposed trade was built into a dict that was never passed to the
    check, so a position could not breach a limit on its own.
  * A sector breach only produced an ``adjustments`` entry, while the caller
    acted only when ``approved`` turned False - so the cap never bound.
  * Research analysis called ``record_trade``, fabricating executed-trade audit
    records for trades nobody placed.

These tests pin the corrected contract: an exposure check consumes a real
account snapshot plus the proposed order, and its verdict is enforced.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
MODULES = Path(__file__).resolve().parents[1] / "modules"
for _p in (CORE, MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ta = importlib.import_module("TradingAnalysis_ELITE")
compliance_manager = importlib.import_module("compliance_manager")


@pytest.fixture()
def manager():
    """Exposure manager with correlation lookups stubbed out (no network)."""
    mgr = ta.PortfolioExposureManager()
    mgr._count_correlated_positions = lambda ticker, existing: 0
    return mgr


def _tech(ticker: str, value: float) -> dict:
    return {"ticker": ticker, "value": value, "sector": "Technology"}


# ---------------------------------------------------------------------------
# Holdings must be declared, never inferred from research
# ---------------------------------------------------------------------------

def test_buy_research_records_do_not_become_holdings():
    analyzer = ta.QuantaEliteAnalyzer.__new__(ta.QuantaEliteAnalyzer)
    analyzer._tracked_positions = []
    analyzer.signal_tracker = type("T", (), {})()
    analyzer.signal_tracker.history = [
        {"ticker": "AAPL", "signal": "BUY", "price": 100.0},
        {"ticker": "MSFT", "signal": "STRONG_BUY", "price": 400.0},
    ]

    assert analyzer._get_tracked_positions() == []


def test_explicitly_tracked_positions_are_returned():
    analyzer = ta.QuantaEliteAnalyzer.__new__(ta.QuantaEliteAnalyzer)
    analyzer._tracked_positions = []
    analyzer.signal_tracker = type("T", (), {})()
    analyzer.signal_tracker.history = []
    # Don't let the test write a holdings file into the working tree.
    analyzer._save_tracked_positions = lambda: None
    analyzer.update_tracked_position("AAPL", 25_000.0, "Technology")

    held = analyzer._get_tracked_positions()
    assert [p["ticker"] for p in held] == ["AAPL"]
    assert held[0]["value"] == 25_000.0


# ---------------------------------------------------------------------------
# Exposure must be computed from the caller's schema, including the new trade
# ---------------------------------------------------------------------------

def test_dollar_valued_holdings_produce_real_sector_exposure(manager):
    """The old code summed a 'weight' key that this schema never carries."""
    res = manager.check_position_limits(
        new_position=_tech("AAPL", 10_000.0),
        existing_positions=[_tech("MSFT", 90_000.0)],
        portfolio_value=200_000.0,
    )
    # 90k held + 10k proposed on a 200k account.
    assert res["sector_exposure"]["Technology"] == pytest.approx(0.50)


def test_proposed_trade_counts_toward_its_own_sector_exposure(manager):
    """A single order large enough to breach the cap must be caught alone."""
    res = manager.check_position_limits(
        new_position=_tech("AAPL", 60_000.0),
        existing_positions=[],
        portfolio_value=200_000.0,
    )
    assert res["sector_exposure"]["Technology"] == pytest.approx(0.30)
    assert res["approved"] is False


def test_sector_breach_returns_a_binding_dollar_cap(manager):
    """Enforcement needs a number to size to, not a vague 'reduce' hint."""
    res = manager.check_position_limits(
        new_position=_tech("AAPL", 20_000.0),
        existing_positions=[_tech("MSFT", 40_000.0)],
        portfolio_value=200_000.0,
    )
    # Cap is 25% of 200k = 50k; 40k is already held, so 10k may still be added.
    assert res["approved"] is False
    assert res["adjustments"]["max_position_value"] == pytest.approx(10_000.0)


def test_position_within_the_sector_cap_is_approved_untouched(manager):
    res = manager.check_position_limits(
        new_position=_tech("AAPL", 10_000.0),
        existing_positions=[_tech("MSFT", 20_000.0)],
        portfolio_value=200_000.0,
    )
    assert res["approved"] is True
    assert res["adjustments"] == {}


def test_unknown_holdings_are_reported_as_hypothetical(manager):
    """No account snapshot must not read as a passed portfolio check."""
    res = manager.check_position_limits(
        new_position=_tech("AAPL", 10_000.0),
        existing_positions=[],
        portfolio_value=200_000.0,
    )
    assert res["holdings_known"] is False


def test_declared_holdings_are_reported_as_known(manager):
    res = manager.check_position_limits(
        new_position=_tech("AAPL", 10_000.0),
        existing_positions=[_tech("MSFT", 20_000.0)],
        portfolio_value=200_000.0,
    )
    assert res["holdings_known"] is True


# ---------------------------------------------------------------------------
# The verdict must actually change the recommended size, consistently
# ---------------------------------------------------------------------------

def test_risk_adjustment_rescales_every_sizing_field():
    """Halving shares while leaving weight and risk untouched is incoherent."""
    sizing = ta.PositionSizing(
        recommended_shares=100,
        position_value=20_000.0,
        portfolio_weight=0.10,
        dollar_risk=1_000.0,
        risk_percent=0.005,
        is_viable=True,
    )
    ta.QuantaEliteAnalyzer._rescale_position_sizing(
        sizing, max_position_value=10_000.0, price=200.0, account_size=200_000.0
    )

    assert sizing.recommended_shares == 50
    assert sizing.position_value == pytest.approx(10_000.0)
    assert sizing.portfolio_weight == pytest.approx(0.05)
    assert sizing.dollar_risk == pytest.approx(500.0)
    assert sizing.risk_percent == pytest.approx(0.0025)
    assert "50 shares" in sizing.recommendation


@pytest.mark.parametrize("holding", [{"ticker": "X"}, _tech("X", float("nan")),
                                     _tech("X", -100)])
def test_invalid_holdings_cannot_pass_exposure_check(manager, holding):
    check = manager.check_position_limits(_tech("AAPL", 100), [holding], 100_000)
    assert not check["approved"]
    assert not check["adjustments"]


def test_allocations_cannot_exceed_available_capital(manager):
    held = [{"ticker": str(i), "sector": str(i), "value": 24_000} for i in range(4)]
    check = manager.check_position_limits(_tech("AAPL", 10_000), held, 100_000)
    assert not check["approved"]
    assert check["adjustments"]["max_position_value"] == pytest.approx(4_000)


def test_large_portfolio_never_discards_earlier_sector_exposure(manager):
    held = [_tech("MSFT", 30_000)] + [
        {"ticker": str(i), "sector": "Other", "value": 100} for i in range(16)]
    check = manager.check_position_limits(_tech("AAPL", 100), held, 100_000)
    assert check["sector_exposure"]["Technology"] == pytest.approx(0.301)
    assert not check["approved"]
    assert not check["correlation_checked"]


@pytest.mark.parametrize("ticker,country,expected", [
    ("RELIANCE.NS", "India", False), ("VOD.L", "United Kingdom", False),
    ("TSM", "Taiwan", True),
])
def test_native_foreign_listings_are_not_depositary_receipts(ticker, country, expected):
    classification = ta.classify_asset_type(ticker=ticker, country=country, quote_type="EQUITY")
    assert classification["is_adr"] is expected


def test_rescaling_below_one_share_makes_the_position_non_viable():
    sizing = ta.PositionSizing(
        recommended_shares=100,
        position_value=20_000.0,
        portfolio_weight=0.10,
        dollar_risk=1_000.0,
        risk_percent=0.005,
        is_viable=True,
    )
    ta.QuantaEliteAnalyzer._rescale_position_sizing(
        sizing, max_position_value=50.0, price=200.0, account_size=200_000.0
    )

    assert sizing.recommended_shares == 0
    assert sizing.position_value == 0.0
    assert sizing.is_viable is False


# ---------------------------------------------------------------------------
# Research must never fabricate executed-trade records
# ---------------------------------------------------------------------------

def test_exposure_verdict_is_serialized_for_the_ui():
    """`holdings_known` must reach the client, or a hypothetical looks checked."""
    result = ta.AnalysisResult(ticker="TEST")
    result.portfolio_risk_check = {
        "approved": False, "holdings_known": True, "warnings": ["sector"],
    }
    payload = ta._mcp_result_to_dict(result)

    assert payload["portfolio_risk_check"]["holdings_known"] is True
    assert payload["portfolio_risk_check"]["approved"] is False


def test_audit_trail_refuses_a_trade_with_no_execution_evidence(tmp_path):
    """An audit record asserts a fill happened; research has no fill to cite."""
    cm = compliance_manager.ComplianceManager(
        audit_file=str(tmp_path / "audit.json")
    )
    with pytest.raises(ValueError):
        cm.record_trade(
            ticker="AAPL", signal="BUY", action="BUY", quantity=10, price=100.0
        )
    assert cm.trade_history == []


def test_audit_trail_accepts_a_trade_with_an_execution_reference(tmp_path):
    path = tmp_path / "audit.json"
    cm = compliance_manager.ComplianceManager(audit_file=str(path))
    cm.record_trade(
        ticker="AAPL", signal="BUY", action="BUY", quantity=10, price=100.0,
        order_id="BROKER-123",
    )

    assert len(cm.trade_history) == 1
    assert cm.trade_history[0].order_id == "BROKER-123"
    assert json.loads(path.read_text())["trades"][0]["order_id"] == "BROKER-123"
