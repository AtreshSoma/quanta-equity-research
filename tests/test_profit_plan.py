"""Profit plan (portfolio/profit_plan.py): stops, booking rules, tax-loss candidates, sector paths.

Synthetic tickers and prices only; no account figures.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=False)

from engine.thresholds import PORTFOLIO as PF  # noqa: E402
from portfolio import profit_plan as pp  # noqa: E402
from portfolio.analytics import PortfolioPolicy  # noqa: E402

TODAY = date(2026, 10, 30)
N = 260


def frame(**paths):
    idx = [TODAY - timedelta(days=N - i) for i in range(N)]
    return pd.DataFrame({k: v for k, v in paths.items()}, index=idx)


def ramp(start, end, n=N):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def row(sym, qty, price, cost, weight, sector="Technology", lots=None, target=None, fund=False):
    return {"symbol": sym, "is_fund": fund,
            "broker": {"last_price": price, "quantity": qty, "average_cost": cost, "market_value": qty * price,
                       "weight": weight, "tax_lots": lots},
            "market": {"sector": sector}, "guidance": {"action": "Hold"},
            "analysis": {"decision": {"levels": {"valuation_target": target}}}}


def test_the_profit_stop_is_the_quarter_high_less_eight_average_moves():
    vals = [100.0, 102.0] * 40
    ts = pp.trailing_stop(vals, PF.PROFIT_TRAIL_SESSIONS, PF.PROFIT_TRAIL_MOVES)
    assert ts["high"] == 102.0 and ts["move"] == pytest.approx(2.0)
    assert ts["stop"] == pytest.approx(102.0 - 8 * 2.0)
    assert pp.trailing_stop(vals[:PF.PROFIT_TRAIL_SESSIONS], PF.PROFIT_TRAIL_SESSIONS, 8.0) is None


def test_a_doubled_position_sells_its_cost_back_out_and_the_rest_is_locked_or_exposed():
    closes = frame(AAA=ramp(100, 300))
    lots = [{"acquired_on": "2023-01-05", "cost_per_share": 100.0, "quantity": 10, "term": "long"}]
    h = pp.plan_holding(row("AAA", 10, 300.0, 100.0, 0.12, lots=lots), closes, 25_000, PortfolioPolicy(), TODAY)
    assert h["signal"]["code"] == "BOOK"
    sale = h["sale"]
    assert sale["code"] == "RECOVER_COST" and sale["value"] == pytest.approx(1_000.0)
    assert sale["gain"] == pytest.approx(sale["shares"] * 200.0) and sale["gain_short"] == 0
    assert h["locked_gain"] + h["exposed_gain"] == pytest.approx(h["gain"])
    assert 0 < h["locked_gain"] < h["gain"]


def test_below_the_stop_nothing_is_locked_and_the_sale_waits_on_the_close():
    vals = ramp(100, 200, N - 5) + [150.0] * 5
    h = pp.plan_holding(row("BBB", 10, 150.0, 100.0, 0.05), frame(BBB=vals), 30_000, PortfolioPolicy(), TODAY)
    assert h["signal"]["code"] == "STOP_HIT" and "if it closes there" in h["signal"]["text"]
    assert h["locked_gain"] == 0 and h["exposed_gain"] == pytest.approx(500.0)
    assert h["stop_sale"]["shares"] == pytest.approx(5.0)
    assert "sale" not in h, "a conditional stop sale is not part of booking now"


def test_funds_get_the_concentration_rule_only():
    vals = ramp(100, 200, N - 5) + [150.0] * 5
    h = pp.plan_holding(row("FFF", 10, 150.0, 60.0, 0.05, sector="Large Blend", fund=True),
                        frame(FFF=vals), 30_000, PortfolioPolicy(), TODAY)
    assert h["signal"]["code"] == "HOLD" and "allocation" in h["signal"]["text"]
    assert not h["triggers"] and "stop_sale" not in h


def test_past_the_valuation_target_books_a_third_and_concentration_sells_to_the_trim_target():
    h = pp.plan_holding(row("CCC", 30, 110.0, 90.0, 0.033, target=100.0), frame(CCC=ramp(80, 110)),
                        100_000, PortfolioPolicy(), TODAY)
    assert h["sale"]["code"] == "VALUATION" and h["sale"]["shares"] == pytest.approx(10.0)
    big = pp.plan_holding(row("DDD", 250, 100.0, 80.0, 0.25), frame(DDD=ramp(80, 100)), 100_000, PortfolioPolicy(), TODAY)
    assert big["sale"]["code"] == "CONCENTRATION"
    assert big["sale"]["weight_after"] == pytest.approx(PortfolioPolicy().trim_target_position)


def test_a_loss_is_a_tax_loss_candidate_with_the_wash_sale_window_flagged():
    lots = [{"acquired_on": "2025-06-01", "cost_per_share": 100.0, "quantity": 8, "term": "long"},
            {"acquired_on": (TODAY - timedelta(days=10)).isoformat(), "cost_per_share": 85.0, "quantity": 2}]
    h = pp.plan_holding(row("EEE", 10, 70.0, 97.0, 0.02, lots=lots), frame(EEE=ramp(100, 70)), 35_000,
                        PortfolioPolicy(), TODAY)
    hv = h["harvest"]
    assert h["signal"]["code"] == "HARVEST" and hv["loss"] == pytest.approx(-270.0)
    assert hv["loss_long"] == pytest.approx(-240.0) and hv["loss_short"] == pytest.approx(-30.0)
    assert hv["wash_risk"] and "wash sale" in h["signal"]["text"]
    assert hv["no_rebuy_before"] == (TODAY + timedelta(days=31)).isoformat()


def test_a_sector_over_its_limit_gets_a_tax_cheapest_and_a_pro_rata_path():
    rows = [row("WIN", 40, 100.0, 50.0, 0.40), row("LOS", 20, 100.0, 130.0, 0.20),
            row("OTH", 40, 100.0, 100.0, 0.40, sector="Healthcare")]
    closes = frame(WIN=ramp(60, 100), LOS=ramp(130, 100), OTH=[100.0] * N)
    plan = pp.build_profit_plan(rows, closes, PortfolioPolicy(), 10_000, 0.0, TODAY)
    path = next(p for p in plan["sector_paths"] if p["sector"] == "Technology")
    # The loss is sold first: it lowers the tax bill instead of adding to it.
    assert path["cheapest"]["legs"][0]["symbol"] == "LOS"
    assert sum(x["value"] for x in path["cheapest"]["legs"]) == pytest.approx(path["excess_value"])
    assert path["cheapest"]["gain"] < path["pro_rata"]["gain"]
    assert sum(x["value"] for x in path["pro_rata"]["legs"]) == pytest.approx(path["excess_value"])
    assert plan["holdings"][0]["signal"]["code"] in pp.SIGNAL_ORDER
    assert plan["rules"]["trail_moves"] == PF.PROFIT_TRAIL_MOVES


def test_no_history_still_plans_size_and_tax_but_draws_no_stop():
    rows = [row("AAA", 250, 100.0, 40.0, 0.25)]
    plan = pp.build_profit_plan(rows, None, PortfolioPolicy(), 100_000, 0.0, TODAY)
    h = plan["holdings"][0]
    assert "stop" not in h and h["signal"]["code"] == "BOOK"
    assert {t["code"] for t in h["triggers"]} == {"CONCENTRATION", "RECOVER_COST"}
    assert h["sale"]["value"] == pytest.approx(10_000.0)
    assert plan["totals"]["locked"] == 0


def test_the_ui_fixture_is_what_the_module_builds():
    """webapp/tests/fixtures/profit_plan.json renders the tab in the JS tests; it must stay
    the real module's output. Regenerate: python webapp/tests/tools/make_profit_plan_fixture.py"""
    import importlib.util
    import json
    tool = Path(__file__).resolve().parents[1] / "webapp" / "tests" / "tools" / "make_profit_plan_fixture.py"
    spec = importlib.util.spec_from_file_location("make_profit_plan_fixture", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fix = mod.FIX / "profit_plan.json"
    before = fix.read_text(encoding="utf-8")
    try:
        mod.main()
        assert json.loads(fix.read_text(encoding="utf-8")) == json.loads(before), \
            "profit_plan.json is stale: regenerate it with make_profit_plan_fixture.py"
    finally:
        fix.write_text(before, encoding="utf-8")
