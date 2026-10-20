"""Integration tests for the scanner's deep-confirmation reconciliation.

These prove that ``QuantaEliteTradingSystem._deep_confirm_final_picks`` cannot
publish a recommendation MORE bullish than the authoritative deep analyzer
(TradingAnalysis_ELITE). A fast scanner BUY is reconciled against a mocked deep
analyzer; when the deep engine is more conservative its verdict wins, the
downgrade reason is surfaced, and the position size is reduced/zeroed according
to the final action.

Run:
    python -m pytest tests/test_deep_confirm.py -q
"""
from __future__ import annotations

import sys
import types
import unittest
import json
import tempfile
import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import SP_ELITE_PRO  # noqa: E402
from SP_ELITE_PRO import QuantaEliteTradingSystem, StockAnalysis  # noqa: E402
from engine.types import DecisionInputs  # noqa: E402


class _FakeDeepResult:
    """Minimal stand-in for TradingAnalysis_ELITE.AnalysisResult."""

    def __init__(self, final_action, reasons, data_status="ok", data_status_reason=""):
        self.final_action = final_action
        self.final_action_reasons = list(reasons)
        self.data_status = data_status
        self.data_status_reason = data_status_reason


class _FakeDeepAnalyzer:
    """Returns a configurable verdict per ticker, mimicking the deep analyzer."""

    #: ticker -> (final_action, reasons)
    verdicts: dict = {}
    #: ticker -> (data_status, data_status_reason) for data-failure simulation.
    data_statuses: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def analyze(self, ticker, account_size=100000):
        ds, ds_reason = self.data_statuses.get(ticker, ("ok", ""))
        if ds != "ok":
            # A data failure yields NO verdict (final_action empty).
            return _FakeDeepResult("", [ds_reason], data_status=ds,
                                   data_status_reason=ds_reason)
        fa, reasons = self.verdicts.get(ticker, ("BUY", ["agree"]))
        return _FakeDeepResult(fa, reasons)


def _risk_inputs(result):
    reasons = ' '.join(result.final_action_reasons).lower()
    return DecisionInputs(
        ticker='TEST', composite_score=0, risk_grade='D' if 'risk grade d' in reasons else 'B',
        risk_reward_ratio=0, current_price=100, stop_loss=0, target_price=0,
        heavy_insider_selling='heavy insider' in reasons or result.final_action == 'WATCHLIST',
    )



def _make_system(capital=2500.0):
    """Build a system instance WITHOUT the network-heavy __init__."""
    sys_obj = QuantaEliteTradingSystem.__new__(QuantaEliteTradingSystem)
    sys_obj.capital = capital
    return sys_obj


def _make_buy_pick(ticker="C", allocation=1000.0, entry_price=50.0, capital=2500.0):
    pick = StockAnalysis(
        ticker=ticker,
        company=f"{ticker} Inc.",
        sector="Financials",
        industry="Banks",
    )
    pick.signal = SP_ELITE_PRO.SignalType.BUY.value
    pick.final_action = "BUY"
    pick.final_action_reasons = ["Composite score 73 → candidate BUY"]
    pick.entry_price = entry_price
    pick.allocation = allocation
    pick.shares = int(allocation // entry_price)
    pick.position_pct = round(allocation / capital * 100, 2)
    return pick


class DeepConfirmReconciliationTest(unittest.TestCase):
    def setUp(self):
        # Inject a fake TradingAnalysis_ELITE module so the lazy import inside
        # _deep_confirm_final_picks resolves to our mock.
        self._saved_mod = sys.modules.get("TradingAnalysis_ELITE")
        fake_mod = types.ModuleType("TradingAnalysis_ELITE")
        fake_mod.QuantaEliteAnalyzer = _FakeDeepAnalyzer
        fake_mod.build_reconciliation_inputs = _risk_inputs
        sys.modules["TradingAnalysis_ELITE"] = fake_mod
        _FakeDeepAnalyzer.verdicts = {}
        _FakeDeepAnalyzer.data_statuses = {}

    def tearDown(self):
        if self._saved_mod is not None:
            sys.modules["TradingAnalysis_ELITE"] = self._saved_mod
        else:
            sys.modules.pop("TradingAnalysis_ELITE", None)

    def test_provider_data_unavailable_moves_to_not_confirmed_not_invalid(self):
        """Case B/D: scanner BUY + deep DATA failure (real ticker, history could
        not be fetched) => NOT_CONFIRMED watchlist, zero allocation, and it is
        NOT marked INVALID and NOT a bearish disagreement."""
        _FakeDeepAnalyzer.data_statuses = {
            "C": ("provider_data_unavailable",
                  "Deep analyzer could not fetch enough historical bars for C "
                  "(data provider issue).")
        }
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("C", allocation=1000.0, entry_price=50.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        # Not funded, moved to watchlist — but NOT invalid.
        self.assertEqual(p.final_action, "WATCHLIST")
        self.assertEqual(p.allocation, 0.0)
        self.assertEqual(p.shares, 0)
        self.assertTrue(getattr(p, "not_independently_confirmed", False))
        self.assertEqual(getattr(p, "data_status", ""), "provider_data_unavailable")

        rec = sys_obj._deep_confirm_records[0]
        self.assertEqual(rec["deep_action"], "NOT_CONFIRMED")
        self.assertNotEqual(rec["deep_action"], "INVALID")
        self.assertTrue(rec["not_independently_confirmed"])
        self.assertEqual(rec["data_status"], "provider_data_unavailable")
        self.assertEqual(rec["allocation_after"], 0.0)
        # Reason is about data, NOT a bearish thesis.
        self.assertIn("data", rec["reason"].lower())

    def test_rate_limited_data_status_not_confirmed(self):
        """A provider rate-limit during confirmation => NOT_CONFIRMED, zero alloc."""
        _FakeDeepAnalyzer.data_statuses = {
            "FFBC": ("provider_rate_limited",
                     "Deep analyzer could not fetch historical bars for FFBC "
                     "(Yahoo Finance rate-limited the request).")
        }
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("FFBC", allocation=900.0, entry_price=45.0)
        pick.final_action = "STRONG_BUY"
        pick.signal = SP_ELITE_PRO.SignalType.STRONG_BUY.value

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        self.assertEqual(p.final_action, "WATCHLIST")
        self.assertEqual(p.allocation, 0.0)
        rec = sys_obj._deep_confirm_records[0]
        self.assertEqual(rec["deep_action"], "NOT_CONFIRMED")
        self.assertEqual(rec["data_status"], "provider_rate_limited")

    def test_buy_downgraded_to_accumulate_grade_d(self):
        """Scanner BUY (alloc>0) + deep ACCUMULATE/Grade D => ACCUMULATE, reduced size."""
        _FakeDeepAnalyzer.verdicts = {
            "C": ("ACCUMULATE", [
                "Composite score 58 → candidate ACCUMULATE",
                "Risk Grade D: hold at ACCUMULATE (reduced size)",
                "DCF overvaluation 40% (>25%) → ACCUMULATE",
            ])
        }
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("C", allocation=1000.0, entry_price=50.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        # Final action becomes ACCUMULATE (deep analyzer wins).
        self.assertEqual(p.final_action, "ACCUMULATE")
        self.assertEqual(p.signal, SP_ELITE_PRO.SignalType.ACCUMULATE.value)

        # The downgrade reason is displayed.
        joined = " | ".join(p.final_action_reasons)
        self.assertIn("Deep-analyzer confirmation → ACCUMULATE", joined)
        self.assertIn("Risk Grade D", joined)

        # Allocation is reduced (ACCUMULATE = reduced size, still funded).
        self.assertGreater(p.allocation, 0.0)
        self.assertLess(p.allocation, 1000.0)

        # An audit record is exposed for the summary table.
        rec = sys_obj._deep_confirm_records[0]
        self.assertTrue(rec["downgraded"])
        self.assertEqual(rec["scanner_action"], "BUY")
        self.assertEqual(rec["deep_action"], "ACCUMULATE")
        self.assertEqual(rec["allocation_before"], 1000.0)
        self.assertEqual(rec["allocation_after"], p.allocation)

    def test_buy_downgraded_to_watchlist_zeroes_allocation(self):
        """Deep WATCHLIST => no new capital, no entry order / position sizing."""
        _FakeDeepAnalyzer.verdicts = {
            "XYZ": ("WATCHLIST", ["Bearish reversal: wait for confirmation"])
        }
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("XYZ", allocation=800.0, entry_price=40.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        self.assertEqual(p.final_action, "WATCHLIST")
        self.assertEqual(p.allocation, 0.0)
        self.assertEqual(p.shares, 0)
        self.assertEqual(p.position_pct, 0.0)
        rec = sys_obj._deep_confirm_records[0]
        self.assertEqual(rec["allocation_after"], 0.0)

    def test_agreement_keeps_buy(self):
        """Deep also says BUY => no downgrade, allocation untouched."""
        _FakeDeepAnalyzer.verdicts = {"AAA": ("BUY", ["Composite score 80 → BUY"])}
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("AAA", allocation=1200.0, entry_price=60.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        self.assertEqual(p.final_action, "BUY")
        self.assertEqual(p.allocation, 1200.0)
        rec = sys_obj._deep_confirm_records[0]
        self.assertFalse(rec["downgraded"])

    def test_no_upgrade_when_deep_more_bullish(self):
        """Deep STRONG_BUY must NOT upgrade a scanner ACCUMULATE (conservatism only)."""
        _FakeDeepAnalyzer.verdicts = {"BBB": ("STRONG_BUY", ["very strong"])}
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("BBB", allocation=500.0, entry_price=25.0)
        pick.signal = SP_ELITE_PRO.SignalType.ACCUMULATE.value
        pick.final_action = "ACCUMULATE"

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        self.assertEqual(p.final_action, "ACCUMULATE")
        self.assertEqual(p.allocation, 500.0)

    def test_disabled_via_env(self):
        """QUANTA_NO_DEEP_CONFIRM=1 bypasses reconciliation entirely."""
        import os
        _FakeDeepAnalyzer.verdicts = {"C": ("AVOID", ["should be ignored"])}
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("C", allocation=1000.0, entry_price=50.0)
        os.environ["QUANTA_NO_DEEP_CONFIRM"] = "1"
        try:
            out = sys_obj._deep_confirm_final_picks([pick])
        finally:
            os.environ.pop("QUANTA_NO_DEEP_CONFIRM", None)
        self.assertEqual(out[0].final_action, "BUY")

    def test_heavy_insider_selling_moves_buy_to_the_research_decision(self):
        """A deep insider veto blocks new capital; the published call is the
        Research Decision itself (AVOID for new money), never a softer one."""
        _FakeDeepAnalyzer.verdicts = {
            "DDD": ("AVOID", ["Heavy insider selling — avoid new money"])
        }
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("DDD", allocation=900.0, entry_price=45.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        self.assertEqual(p.final_action, "AVOID")
        self.assertEqual(p.allocation, 0.0)
        self.assertEqual(p.shares, 0)
        self.assertEqual(p.position_pct, 0.0)
        joined = " | ".join(p.final_action_reasons)
        self.assertIn("Heavy insider selling", joined)
        # The record names the rules that fired, so the UI states their remedies.
        codes = sys_obj._deep_confirm_records[0]["reason_codes"]
        self.assertEqual(codes[0], "HEAVY_INSIDER_SELLING")
        self.assertIn("RESEARCH_DECISION_CAP", codes)

    def test_deep_failure_does_not_crash_and_moves_to_not_confirmed(self):
        """Case E: deep analyzer raising must not crash. A confirmation FAILURE
        is a DATA problem, not a verdict — the pick is moved to the
        not-confirmed watchlist with ZERO allocation (never left funded)."""

        class _RaisingDeep(_FakeDeepAnalyzer):
            def analyze(self, ticker, account_size=100000):
                raise RuntimeError("simulated deep analyzer timeout")

        sys.modules["TradingAnalysis_ELITE"].QuantaEliteAnalyzer = _RaisingDeep
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("EEE", allocation=700.0, entry_price=35.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        # No crash; a pick that could not be confirmed must NOT stay funded.
        self.assertEqual(p.final_action, "WATCHLIST")
        self.assertEqual(p.allocation, 0.0)
        self.assertEqual(p.shares, 0)
        rec = sys_obj._deep_confirm_records[0]
        self.assertTrue(rec["downgraded"])
        self.assertEqual(rec["deep_action"], "NOT_CONFIRMED")
        self.assertTrue(rec["not_independently_confirmed"])
        self.assertEqual(rec["data_status"], "deep_confirmation_failed")
        self.assertEqual(rec["allocation_after"], 0.0)

    def test_deep_failure_surfaces_not_confirmed_reason(self):
        """Case E (QA fix 5): failure adds a visible 'data issue' reason and
        deep_action == 'NOT_CONFIRMED' (NOT a bearish disagreement)."""

        class _RaisingDeep(_FakeDeepAnalyzer):
            def analyze(self, ticker, account_size=100000):
                raise TimeoutError("simulated timeout")

        sys.modules["TradingAnalysis_ELITE"].QuantaEliteAnalyzer = _RaisingDeep
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("FFF", allocation=600.0, entry_price=30.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        joined = " | ".join(p.final_action_reasons).lower()
        self.assertIn("data issue", joined)
        rec = sys_obj._deep_confirm_records[0]
        self.assertEqual(rec["deep_action"], "NOT_CONFIRMED")

    def test_invalid_deep_verdict_downgrades_and_zeroes(self):
        """QA fix 2: a deep INVALID verdict is the most conservative — it must
        downgrade a scanner BUY and zero allocation/shares/position_pct."""
        _FakeDeepAnalyzer.verdicts = {
            "GGG": ("INVALID", ["No valid data — produce no scores"])
        }
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("GGG", allocation=1000.0, entry_price=50.0)

        out = sys_obj._deep_confirm_final_picks([pick])
        p = out[0]

        self.assertEqual(p.final_action, "INVALID")
        self.assertEqual(p.allocation, 0.0)
        self.assertEqual(p.shares, 0)
        self.assertEqual(p.position_pct, 0.0)
        rec = sys_obj._deep_confirm_records[0]
        self.assertTrue(rec["downgraded"])
        self.assertEqual(rec["allocation_after"], 0.0)

    def test_bypass_sets_disabled_flag_and_skipped_records(self):
        """QA fix 1: QUANTA_NO_DEEP_CONFIRM=1 sets deep_confirm_enabled=False and
        records deep_action='SKIPPED' (warning is emitted via logger/print)."""
        import os
        _FakeDeepAnalyzer.verdicts = {"C": ("AVOID", ["ignored"])}
        sys_obj = _make_system(capital=2500.0)
        pick = _make_buy_pick("C", allocation=1000.0, entry_price=50.0)
        os.environ["QUANTA_NO_DEEP_CONFIRM"] = "1"
        try:
            out = sys_obj._deep_confirm_final_picks([pick])
        finally:
            os.environ.pop("QUANTA_NO_DEEP_CONFIRM", None)

        self.assertFalse(sys_obj._deep_confirm_enabled)
        self.assertEqual(out[0].final_action, "BUY")  # not downgraded
        rec = sys_obj._deep_confirm_records[0]
        self.assertEqual(rec["deep_action"], "SKIPPED")


class DeepConfirmExportTest(unittest.TestCase):
    """QA fix 3 & 4: exports must carry deep-confirmation fields and the TXT
    summary must use final_action, not the raw signal."""

    def setUp(self):
        self._saved_mod = sys.modules.get("TradingAnalysis_ELITE")
        fake_mod = types.ModuleType("TradingAnalysis_ELITE")
        fake_mod.QuantaEliteAnalyzer = _FakeDeepAnalyzer
        fake_mod.build_reconciliation_inputs = _risk_inputs
        sys.modules["TradingAnalysis_ELITE"] = fake_mod
        _FakeDeepAnalyzer.verdicts = {}
        _FakeDeepAnalyzer.data_statuses = {}
        self._tmp = tempfile.mkdtemp(prefix="quanta_qa_")

    def tearDown(self):
        if self._saved_mod is not None:
            sys.modules["TradingAnalysis_ELITE"] = self._saved_mod
        else:
            sys.modules.pop("TradingAnalysis_ELITE", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _build_system_with_downgrade(self):
        import datetime as _dt
        _FakeDeepAnalyzer.verdicts = {
            "C": ("WATCHLIST", ["Bearish reversal: wait for confirmation"])
        }
        sys_obj = _make_system(capital=2500.0)
        sys_obj.market = "US"
        sys_obj.currency_symbol = "$"
        sys_obj.analysis_timestamp = _dt.datetime(2026, 6, 2, 11, 0, 0)
        sys_obj.scan_results = []
        sys_obj.failed_tickers = []
        pick = _make_buy_pick("C", allocation=1000.0, entry_price=50.0)
        confirmed = sys_obj._deep_confirm_final_picks([pick])
        return sys_obj, confirmed

    def test_csv_export_contains_deep_confirm_fields(self):
        import csv
        import glob
        sys_obj, recs = self._build_system_with_downgrade()
        self.assertTrue(sys_obj.export_results(recs, output_dir=self._tmp))
        csv_path = sorted(glob.glob(f"{self._tmp}/quanta_signals_*.csv"))[-1]
        with open(csv_path, newline="") as f:
            row = next(csv.DictReader(f))
        for col in ("Scanner_Action", "Deep_Action", "Final_Action",
                    "Allocation_Before", "Allocation_After", "Downgrade_Flag",
                    "Deep_Confirmation_Reason", "Deep_Confirm_Enabled"):
            self.assertIn(col, row, f"CSV missing column {col}")
        self.assertEqual(row["Scanner_Action"], "BUY")
        self.assertEqual(row["Deep_Action"], "WATCHLIST")
        self.assertEqual(row["Final_Action"], "WATCHLIST")
        self.assertEqual(float(row["Allocation_Before"]), 1000.0)
        self.assertEqual(float(row["Allocation_After"]), 0.0)
        self.assertEqual(row["Downgrade_Flag"], "True")

    def test_json_export_contains_deep_confirm_block_and_flag(self):
        import glob
        sys_obj, recs = self._build_system_with_downgrade()
        self.assertTrue(sys_obj.export_results(recs, output_dir=self._tmp))
        json_path = sorted(glob.glob(f"{self._tmp}/quanta_detailed_*.json"))[-1]
        data = json.load(open(json_path))
        self.assertIn("deep_confirm_enabled", data["metadata"])
        self.assertTrue(data["metadata"]["deep_confirm_enabled"])
        dc = data["recommendations"][0]["deep_confirmation"]
        for k in ("scanner_action", "deep_action", "final_action",
                  "allocation_before", "allocation_after", "downgrade_flag",
                  "deep_confirmation_reason", "deep_confirm_enabled"):
            self.assertIn(k, dc, f"JSON deep_confirmation missing {k}")
        self.assertEqual(dc["deep_action"], "WATCHLIST")
        self.assertEqual(dc["allocation_after"], 0.0)

    def test_txt_summary_uses_final_action_not_signal(self):
        import glob
        sys_obj, recs = self._build_system_with_downgrade()
        self.assertTrue(sys_obj.export_results(recs, output_dir=self._tmp))
        txt_path = sorted(glob.glob(f"{self._tmp}/quanta_summary_*.txt"))[-1]
        text = open(txt_path).read()
        # final_action is WATCHLIST; the raw signal got mapped to HOLD.
        self.assertIn("Action: WATCHLIST", text)
        self.assertNotIn("Action: HOLD", text)


if __name__ == "__main__":
    unittest.main()
