"""A2: a scanner replay becomes a fail-closed registry entry with its numbers."""
from __future__ import annotations

from apex_quant.evidence_registry import shortterm_entry_from_backtest

_W = {"technical": 0.28, "fundamental": 0.20, "momentum": 0.22, "quality": 0.12,
      "analyst": 0.10, "pattern": 0.08}


def _summary(ci, dci):
    return {"total_trades": 500, "win_rate_pct": 60.0, "deflated_sharpe": 0.3,
            "excess_vs_spy": {"mean": 0.1, "ci95_bootstrap": ci},
            "signal_minus_random_excess": {"mean": 0.2, "ci95_bootstrap": dci},
            "config": {"start": "2016-01-01", "end": "2026-09-30", "composite_weights": _W},
            "code_fingerprint": "abc"}


def test_no_edge_unless_it_beats_spy_and_random():
    assert shortterm_entry_from_backtest(_summary([-0.1, 0.3], [0.1, 0.4]), "s")["status"] == "NO_EDGE"
    assert shortterm_entry_from_backtest(_summary([0.1, 0.3], [-0.1, 0.4]), "s")["status"] == "NO_EDGE"
    assert shortterm_entry_from_backtest(_summary([0.1, 0.3], [0.05, 0.4]), "s")["status"] == "PARTIAL"


def test_entry_states_scope_numbers_and_fingerprint():
    e = shortterm_entry_from_backtest(_summary([-0.4, 0.07], [-0.26, 0.45]), "src.json")
    assert "58% of composite" in e["scope"]
    assert "500 trades" in e["summary"] and "-0.40%" in e["summary"]
    assert e["code_fingerprint"] == "abc" and e["tested_through"] == "2026-09-30"
    assert e["caveats"][0].startswith("Universe")


def test_missing_intervals_fail_closed():
    assert shortterm_entry_from_backtest({"config": {}}, "s")["status"] == "NO_EDGE"


def test_summary_names_the_test_that_failed_and_the_measured_win_rates():
    s = _summary([0.1, 0.3], [-0.1, 0.4])
    s["random_baseline"] = {"win_rate_pct": 56.4}
    e = shortterm_entry_from_backtest(s, "s")
    assert e["summary"].startswith("Entry signals do not beat random entries")
    assert any("60.0%" in c and "56.4%" in c for c in e["caveats"])


def test_point_in_time_replay_states_its_wider_scope():
    s = _summary([-0.1, 0.3], [0.1, 0.4])
    s["config"]["pit_fundamentals"] = True
    e = shortterm_entry_from_backtest(s, "s")
    assert "90% of composite weight" in e["scope"] and "SEC filings" in e["scope"]
    assert e["status"] == "NO_EDGE"


def test_elite_entry_records_point_in_time_scope_and_honest_wording():
    from apex_quant.evidence_registry import elite_entry_from_oos

    report = {"ic": {"21d": {"mean_ic": 0.012, "t_stat": 1.1, "n_independent_dates": 150,
                             "decile_spread": 0.002},
                     "63d": {"mean_ic": 0.02, "t_stat": 0.9, "n_independent_dates": 50,
                             "decile_spread": 0.004}},
              "end": "2026-10-21", "scope": "Technical, fundamental, valuation and quality legs",
              "pit_fundamentals": True, "fundamentals_row_share": 0.93}
    e = elite_entry_from_oos(report, source="x.json")
    assert e["status"] == "NO_EDGE"
    assert "positive but below the t >= 2 bar" in e["summary"]
    assert e["scope"].startswith("Technical, fundamental") and e["source"] == "x.json"
    assert any("93% of panel rows" in c for c in e["caveats"])
    report["ic"]["21d"]["t_stat"] = report["ic"]["63d"]["t_stat"] = 2.4
    assert elite_entry_from_oos(report)["status"] == "PARTIAL"
