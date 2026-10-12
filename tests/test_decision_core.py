"""Unit tests for the shared decision-core engine.

Run from the workspace root with:

    python -m pytest tests/test_decision_core.py -q

Or directly:

    python tests/test_decision_core.py
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

# Make ``core`` importable when running this file directly.
_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from engine import (  # noqa: E402
    DecisionInputs,
    FinalAction,
    backtest_disclaimer,
    check_allocation_eligible,
    normalize_dividend_yield,
    resolve_final_action,
    signal_to_final_action,
    validate_consistency,
    validate_dividend_yield,
    validate_ticker_data,
)
from engine.consistency_validator import TickerView  # noqa: E402


# ---------------------------------------------------------------------------
# Data-validation gate
# ---------------------------------------------------------------------------


class TestInvalidTicker(unittest.TestCase):
    """The DRAM scenario: bad ticker must return INVALID, no recommendation."""

    def test_empty_ticker_is_invalid(self):
        rep = validate_ticker_data("", 0, 0, 0, "")
        self.assertFalse(rep.is_valid)
        self.assertTrue(any("Empty ticker" in r for r in rep.reasons))

    def test_zero_price_is_invalid(self):
        rep = validate_ticker_data("DRAM", 0.0, 250, 5_000_000_000, "Some Co Inc")
        self.assertFalse(rep.is_valid)
        self.assertFalse(rep.has_price)

    def test_short_history_is_invalid(self):
        rep = validate_ticker_data("FOO", 100.0, 5, 1_000_000_000, "Foo Corp")
        self.assertFalse(rep.is_valid)
        self.assertFalse(rep.has_history)

    def test_no_company_name_is_invalid(self):
        rep = validate_ticker_data("FOO", 100.0, 250, 1_000_000_000, "FOO")
        self.assertFalse(rep.is_valid)
        self.assertFalse(rep.has_company_name)

    def test_valid_ticker_passes(self):
        rep = validate_ticker_data("AAPL", 180.0, 500, 2_900_000_000_000, "Apple Inc.")
        self.assertTrue(rep.is_valid, msg=rep.reasons)


class TestETFValidation(unittest.TestCase):
    """ETFs / funds report totalAssets and may share name with ticker."""

    def test_etf_without_market_cap_passes_via_net_assets(self):
        # SPY-like: marketCap missing/0 but totalAssets populated
        rep = validate_ticker_data(
            "SPY", 540.0, 500, market_cap=0, company_name="SPDR S&P 500 ETF Trust",
            quote_type="ETF", net_assets=480_000_000_000,
        )
        self.assertTrue(rep.is_valid, msg=rep.reasons)
        self.assertTrue(rep.is_etf)
        self.assertTrue(rep.has_market_cap)

    def test_etf_short_name_equal_to_ticker_is_acceptable(self):
        rep = validate_ticker_data(
            "QQQM", 195.0, 250, market_cap=0, company_name="QQQM",
            is_etf=True, net_assets=30_000_000_000,
        )
        self.assertTrue(rep.is_valid, msg=rep.reasons)

    def test_etf_history_threshold_relaxed(self):
        # Equities need 50 bars, ETFs only need 20+
        rep = validate_ticker_data(
            "VOO", 500.0, 25, market_cap=0, company_name="Vanguard S&P 500 ETF",
            quote_type="ETF", net_assets=400_000_000_000, min_history=50,
        )
        self.assertTrue(rep.is_valid, msg=rep.reasons)

    def test_fund_without_reported_assets_is_valid_with_the_gap_recorded(self):
        """Yahoo omits AUM for real funds (NIFTYBEES.NS). Name, price and a full
        history prove the instrument; the missing size is a data gap, not a
        verdict that the ticker is invalid."""
        rep = validate_ticker_data(
            "NIFTYBEES.NS", 264.0, 250, market_cap=0, company_name="Nippon India ETF Nifty 50 BeES",
            quote_type="ETF", net_assets=0,
        )
        self.assertTrue(rep.is_valid, msg=rep.reasons)
        self.assertFalse(rep.has_market_cap)

    def test_nameless_priceless_symbol_still_fails(self):
        rep = validate_ticker_data("BADETF", 0.0, 250, market_cap=0, company_name="",
                                   quote_type="ETF", net_assets=0)
        self.assertFalse(rep.is_valid)

    def test_mutualfund_quote_type_treated_as_fund(self):
        rep = validate_ticker_data(
            "VFIAX", 450.0, 500, market_cap=0, company_name="Vanguard 500 Index Fund",
            quote_type="MUTUALFUND", net_assets=900_000_000_000,
        )
        self.assertTrue(rep.is_valid, msg=rep.reasons)
        self.assertTrue(rep.is_etf)


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------


def _base_inputs(**over):
    defaults = dict(
        ticker="TST",
        composite_score=70.0,
        risk_grade="B",
        risk_reward_ratio=2.5,
        current_price=100.0,
        stop_loss=95.0,
        target_price=115.0,
        has_existing_position=False,
    )
    defaults.update(over)
    return DecisionInputs(**defaults)


class TestDecisionEngine(unittest.TestCase):

    def test_strong_buy_path(self):
        out = resolve_final_action(_base_inputs(composite_score=80))
        self.assertEqual(out.final_action, FinalAction.STRONG_BUY)
        self.assertTrue(out.allocation_eligible)

    def test_buy_path(self):
        out = resolve_final_action(_base_inputs(composite_score=70))
        self.assertEqual(out.final_action, FinalAction.BUY)

    def test_invalid_inputs_force_invalid(self):
        out = resolve_final_action(_base_inputs(is_valid=False))
        self.assertEqual(out.final_action, FinalAction.INVALID)
        self.assertFalse(out.allocation_eligible)

    def test_risk_grade_d_trims_one_notch(self):
        """Risk Grade D is elevated but not disqualifying: it trims conviction
        one notch (BUY candidate -> ACCUMULATE) yet stays allocation-eligible
        at reduced size, rather than being blanket-forced to HOLD/WATCHLIST."""
        out = resolve_final_action(_base_inputs(composite_score=70, risk_grade="D"))
        self.assertEqual(out.final_action, FinalAction.ACCUMULATE)
        self.assertTrue(out.allocation_eligible)

    def test_risk_grade_d_strong_buy_caps_at_accumulate(self):
        """A grade-D name allows NO fresh BUY: even a STRONG_BUY candidate is
        capped at ACCUMULATE (reduced size), never published as a BUY."""
        out = resolve_final_action(_base_inputs(composite_score=80, risk_grade="D"))
        self.assertEqual(out.final_action, FinalAction.ACCUMULATE)
        self.assertTrue(out.allocation_eligible)

    def test_risk_grade_f_blocks_buy(self):
        out = resolve_final_action(_base_inputs(composite_score=80, risk_grade="F"))
        self.assertNotIn(out.final_action,
                         (FinalAction.STRONG_BUY, FinalAction.BUY, FinalAction.ACCUMULATE))

    def test_hold_without_position_becomes_watchlist(self):
        out = resolve_final_action(_base_inputs(composite_score=48, has_existing_position=False))
        self.assertEqual(out.final_action, FinalAction.WATCHLIST)

    def test_hold_with_position_stays_hold(self):
        out = resolve_final_action(_base_inputs(composite_score=48, has_existing_position=True))
        self.assertEqual(out.final_action, FinalAction.HOLD_EXISTING)

    def test_bad_stop_blocks_buy(self):
        out = resolve_final_action(_base_inputs(stop_loss=120))  # above entry
        self.assertEqual(out.final_action, FinalAction.WATCHLIST)

    def test_bad_target_blocks_buy(self):
        out = resolve_final_action(_base_inputs(target_price=90))  # below entry
        self.assertEqual(out.final_action, FinalAction.WATCHLIST)

    def test_low_rr_blocks_buy(self):
        out = resolve_final_action(_base_inputs(risk_reward_ratio=0.5))
        self.assertEqual(out.final_action, FinalAction.WATCHLIST)

    def test_signal_to_final_action_hold_becomes_watchlist(self):
        self.assertEqual(signal_to_final_action("HOLD", has_existing_position=False),
                         FinalAction.WATCHLIST)
        self.assertEqual(signal_to_final_action("HOLD", has_existing_position=True),
                         FinalAction.HOLD_EXISTING)

    def test_avoid_without_position_stays_avoid(self):
        """A bearish score is AVOID for a non-holder too: for new money it means
        "do not buy". Folding it into WATCHLIST made it indistinguishable from a
        neutral score. The holder instruction is published separately."""
        out = resolve_final_action(_base_inputs(composite_score=20,
                                                has_existing_position=False))
        self.assertEqual(out.final_action, FinalAction.AVOID)
        self.assertEqual(out.contract["new_money"], "AVOID")
        # A bearish score alone is a TRIM review for a holder; EXIT needs a
        # measured red flag as well (audit 2026-09-27).
        self.assertEqual(out.contract["holder_action"], "TRIM")

    def test_avoid_with_position_stays_avoid(self):
        out = resolve_final_action(_base_inputs(composite_score=20,
                                                has_existing_position=True))
        self.assertEqual(out.final_action, FinalAction.AVOID)

    def test_signal_to_final_action_sell_without_position(self):
        self.assertEqual(signal_to_final_action("SELL", has_existing_position=False),
                         FinalAction.AVOID)
        self.assertEqual(signal_to_final_action("SELL", has_existing_position=True),
                         FinalAction.AVOID)

    def test_risk_grade_is_normalised(self):
        """Lower-case and whitespace must gate identically to upper-case:
        ' d ' trims like 'D' (BUY -> ACCUMULATE) and 'f' blocks like 'F'."""
        out = resolve_final_action(_base_inputs(composite_score=70, risk_grade=" d "))
        self.assertEqual(out.final_action, FinalAction.ACCUMULATE)
        out2 = resolve_final_action(_base_inputs(composite_score=70, risk_grade="f"))
        self.assertEqual(out2.final_action, FinalAction.WATCHLIST)

    def test_unknown_risk_grade_defaults_to_c(self):
        """Garbage risk grades must collapse to neutral 'C', not silently
        bypass the D/F gate by being neither D nor F."""
        di = _base_inputs(risk_grade="X")
        self.assertEqual(di.risk_grade, "C")
        di2 = _base_inputs(risk_grade="")
        self.assertEqual(di2.risk_grade, "C")

    def test_grade_d_accumulate_is_consistency_valid(self):
        """The validator must match the resolver's documented grade-D cap."""
        view = TickerView(
            ticker="TST", summary_action="ACCUMULATE", detail_action="ACCUMULATE",
            risk_grade="D", current_price=100.0, stop_loss=95.0,
            target_price=115.0, risk_reward_ratio=3.0, allocation_amount=500.0,
        )
        report = validate_consistency([view])
        self.assertTrue(report.ok, msg=report.issues)


# ---------------------------------------------------------------------------
# Dividend
# ---------------------------------------------------------------------------


class TestDividendYield(unittest.TestCase):
    def test_fraction_input_preserved(self):
        self.assertAlmostEqual(normalize_dividend_yield(0.0088), 0.0088, places=5)

    def test_percent_input_disambiguated_by_rate_and_price(self):
        # yfinance sometimes returns 0.23 meaning '0.23%'. When dividend_rate
        # and price are available we MUST prefer them to disambiguate, which
        # is the realistic GOOGL fix path.
        y = normalize_dividend_yield(raw_yield=0.23, dividend_rate=0.88, current_price=170.0)
        self.assertAlmostEqual(y, 0.88 / 170.0, places=4)

    def test_raw_fraction_preserved_when_no_rate(self):
        # No rate/price provided -> raw value <=1 is taken as a fraction.
        self.assertAlmostEqual(normalize_dividend_yield(0.0088), 0.0088, places=5)

    def test_already_percent_above_1(self):
        # 2.5 -> 2.5% -> 0.025 fraction
        self.assertAlmostEqual(normalize_dividend_yield(2.5), 0.025, places=5)

    def test_absurd_value_suppressed(self):
        # > 100 is always treated as garbage and dropped to 0.0
        self.assertEqual(normalize_dividend_yield(111.0), 0.0)
        self.assertEqual(normalize_dividend_yield(9999.0), 0.0)

    def test_compute_from_rate_and_price(self):
        y = normalize_dividend_yield(raw_yield=0, dividend_rate=4.4, current_price=220.0)
        self.assertAlmostEqual(y, 0.02, places=4)

    def test_quarterly_rate_annualised(self):
        y = normalize_dividend_yield(raw_yield=0, dividend_rate=1.1, current_price=220.0,
                                     is_quarterly_rate=True)
        self.assertAlmostEqual(y, 0.02, places=4)

    def test_validate_suspicious_largecap(self):
        ok, note = validate_dividend_yield(0.18, market_cap=50_000_000_000)
        self.assertTrue(ok)
        self.assertIn("suspicious", note)

    def test_validate_suppressed(self):
        ok, _ = validate_dividend_yield(0.75)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Consistency validator
# ---------------------------------------------------------------------------


class TestConsistencyValidator(unittest.TestCase):
    def test_summary_must_equal_detail(self):
        v = TickerView(ticker="X", summary_action="BUY", detail_action="HOLD_EXISTING",
                       risk_grade="B", current_price=10, stop_loss=9, target_price=12,
                       risk_reward_ratio=2.0)
        rep = validate_consistency([v])
        self.assertFalse(rep.ok)
        self.assertTrue(any("summary=BUY" in i for i in rep.issues))

    def test_buy_without_setup_flags_issue(self):
        v = TickerView(ticker="X", summary_action="BUY", detail_action="BUY",
                       risk_grade="B", current_price=10, stop_loss=0, target_price=0,
                       risk_reward_ratio=2.0)
        rep = validate_consistency([v])
        self.assertFalse(rep.ok)

    def test_risk_grade_d_buy_flagged(self):
        v = TickerView(ticker="X", summary_action="BUY", detail_action="BUY",
                       risk_grade="D", current_price=10, stop_loss=9, target_price=12,
                       risk_reward_ratio=2.0)
        rep = validate_consistency([v])
        self.assertFalse(rep.ok)

    def test_hold_without_position_flagged(self):
        v = TickerView(ticker="X", summary_action="HOLD_EXISTING", detail_action="HOLD_EXISTING",
                       risk_grade="B", has_existing_position=False)
        rep = validate_consistency([v])
        self.assertFalse(rep.ok)

    def test_invalid_must_have_no_allocation(self):
        v = TickerView(ticker="X", summary_action="INVALID", detail_action="INVALID",
                       risk_grade="", allocation_amount=500.0)
        rep = validate_consistency([v])
        self.assertFalse(rep.ok)

    def test_non_buy_must_have_no_allocation(self):
        v = TickerView(ticker="X", summary_action="WATCHLIST", detail_action="WATCHLIST",
                       risk_grade="B", allocation_amount=100.0)
        rep = validate_consistency([v])
        self.assertFalse(rep.ok)

    def test_clean_passes(self):
        v = TickerView(ticker="X", summary_action="BUY", detail_action="BUY",
                       risk_grade="B", current_price=10, stop_loss=9, target_price=12,
                       risk_reward_ratio=2.5, allocation_amount=500.0)
        rep = validate_consistency([v], deployed=500.0, max_exposure=1000.0)
        self.assertTrue(rep.ok, msg=rep.issues)


# ---------------------------------------------------------------------------
# Allocation gate
# ---------------------------------------------------------------------------


class TestAllocationGate(unittest.TestCase):
    def test_watchlist_never_allocates(self):
        dec = resolve_final_action(_base_inputs(composite_score=48, has_existing_position=False))
        ok, reasons = check_allocation_eligible(dec)
        self.assertFalse(ok)
        self.assertTrue(any("does not allocate" in r for r in reasons))

    def test_sector_cap_blocks(self):
        dec = resolve_final_action(_base_inputs(composite_score=80))
        ok, reasons = check_allocation_eligible(dec, sector_exposure_pct=0.30, sector_cap_pct=0.25)
        self.assertFalse(ok)
        self.assertTrue(any("sector exposure" in r for r in reasons))


# ---------------------------------------------------------------------------
# Backtest labelling
# ---------------------------------------------------------------------------


class TestBacktestLabels(unittest.TestCase):
    def test_disclaimer_never_claims_validated(self):
        text = backtest_disclaimer().lower()
        self.assertIn("diagnostic", text)
        self.assertIn("not", text)
        self.assertNotIn("validated predictive", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
