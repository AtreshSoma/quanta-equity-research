"""1W range, the 1D intraday simulation and the what-if (sell to cash) metrics.

Synthetic data only. These prove the arithmetic and the fail-closed rules; none of it
is evidence about a live Robinhood account.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

from portfolio import history as hs  # noqa: E402
from portfolio.service import PortfolioService  # noqa: E402
from test_my_portfolio import connected_provider, decision, fake_analyze, research  # noqa: E402
from test_portfolio_review import closes  # noqa: E402


def test_one_week_range_is_five_sessions_from_the_prior_close():
    df = closes()
    sim = hs.simulate_current_holdings([{"symbol": "AAA", "quantity": 10, "value": 1000}], df, "SPY", "1W")
    assert sim["available"] and sim["range"] == "1W"
    assert len(sim["dates"]) == 6 and sim["stats"]["days"] == 5
    assert sim["portfolio"][0] == pytest.approx(100) and sim["benchmark_series"][0] == pytest.approx(100)
    assert "1W" in hs.RANGES


STAMPS = ["2026-09-29T08:00:00-04:00", "2026-09-29T09:30:00-04:00", "2026-09-29T12:00:00-04:00",
          "2026-09-29T15:55:00-04:00", "2026-09-29T16:30:00-04:00"]


def test_intraday_is_measured_from_the_previous_close_and_tags_each_session():
    prices = {"AAA": [None, 101.0, None, 104.0, 105.0], "BBB": [50.0, 49.0, 48.0, 48.0, None],
              "SPY": [400.0, 402.0, 404.0, 406.0, 404.0]}
    prev = {"AAA": 100.0, "BBB": 50.0, "SPY": 400.0}
    h = [{"symbol": "AAA", "quantity": 10, "value": 1050}, {"symbol": "BBB", "quantity": 20, "value": 960}]
    out = hs.simulate_intraday(h, STAMPS, prices, prev, "SPY")
    assert out["available"] and out["range"] == "1D"
    assert out["sessions"] == ["pre", "regular", "regular", "regular", "post"]
    # Before AAA trades it is worth its previous close; later prices carry forward, never interpolated.
    assert out["value"] == pytest.approx([1000 + 1000, 1010 + 980, 1010 + 960, 1040 + 960, 1050 + 960])
    assert out["stats"]["start_value"] == pytest.approx(2000.0)
    assert out["portfolio"][-1] == pytest.approx(2010 / 2000 * 100)
    assert out["stats"]["benchmark_return"] == pytest.approx(404 / 400 - 1)
    assert out["stats"]["excess"] == pytest.approx(2010 / 2000 - 404 / 400)


def test_intraday_names_what_it_leaves_out_and_refuses_thin_coverage():
    prices = {"AAA": [100.0] * 5, "BBB": [None] * 5, "SPY": [400.0] * 5}
    h = [{"symbol": "AAA", "quantity": 10, "value": 1000}, {"symbol": "BBB", "quantity": 1, "value": 100},
         {"symbol": "CCC", "quantity": 1, "value": 100}]
    out = hs.simulate_intraday(h, STAMPS, prices, {"AAA": 100.0, "BBB": 100.0, "SPY": 400.0}, "SPY")
    assert out["available"]
    assert {e["symbol"]: e["reason"] for e in out["excluded"]} == {"BBB": "No intraday prices today", "CCC": "No previous close"}
    assert out["coverage"] == pytest.approx(1000 / 1200)
    thin = hs.simulate_intraday(h, STAMPS, prices, {"BBB": 100.0, "CCC": 100.0, "SPY": 400.0}, "SPY")
    assert thin["available"] is False and "No holding" in thin["reason"]
    nob = hs.simulate_intraday(h, STAMPS, prices, {"AAA": 100.0}, "SPY")
    assert nob["available"] is False and "benchmark" in nob["reason"]


def _connected_service():
    prov, _ = connected_provider()
    svc = PortfolioService(prov, fake_analyze({"AAPL": research(decision())}))
    handle = next(a["handle"] for a in svc.accounts() if a["can_trade_via_agent"] is False)
    svc.refresh(handle, start_thread=False)
    return svc


def test_whatif_before_matches_the_view_and_after_reruns_the_same_rule():
    svc = _connected_service()
    pf = svc.view()["portfolio"]
    aapl = next(r for r in pf["positions"] if r["symbol"] == "AAPL")
    # The trim-to-limit size, computed as the view computes it (independent of whether a call is live).
    total = pf["metrics"]["total_value"]
    cut = aapl["broker"]["market_value"] - pf["policy"]["max_single_position"] * total
    sector = aapl["market"]["sector"]
    sector_now = next(s["weight"] for s in pf["metrics"]["sectors"] if s["sector"] == sector)
    trim = {"delta_value": -cut, "weight_after": pf["policy"]["max_single_position"], "sector": sector,
            "sector_weight_after": sector_now - cut / total}
    out = svc.whatif([{"symbol": "AAPL", "delta_value": trim["delta_value"]}])
    assert out["available"]
    assert out["before"]["risk"] == pf["metrics"]["risk"]
    assert out["before"]["weights"] == pytest.approx(pf["metrics"]["weights"])
    after = out["after"]
    # Selling to cash keeps the total; AAPL lands on the single-name limit; cash grows by the sale.
    assert after["total_value"] == pytest.approx(out["before"]["total_value"])
    assert after["weights"]["AAPL"] == pytest.approx(trim["weight_after"])
    assert after["cash"] == pytest.approx(out["before"]["cash"] - trim["delta_value"])
    assert out["freed"] == pytest.approx(-trim["delta_value"])
    tech = next(s for s in after["sectors"] if s["sector"] == trim["sector"])
    assert tech["weight"] == pytest.approx(trim["sector_weight_after"])
    assert after["risk"]["points"] <= out["before"]["risk"]["points"]


def test_whatif_accepts_reductions_only_and_ignores_unknown_holdings():
    svc = _connected_service()
    out = svc.whatif([{"symbol": "AAPL", "delta_value": 500}, {"symbol": "ZZZZ", "delta_value": -10},
                      {"symbol": "AAPL", "delta_value": "not a number"}, "junk"])
    assert out["changes"] == [] and out["freed"] == 0
    assert out["after"]["risk"] == out["before"]["risk"]
    big = svc.whatif([{"symbol": "AAPL", "delta_value": -1e12}])
    value = next(r for r in svc.view()["portfolio"]["positions"] if r["symbol"] == "AAPL")["broker"]["market_value"]
    assert big["changes"] == [{"symbol": "AAPL", "delta_value": -value}]
    assert "AAPL" in big["after"]["weights"] and big["after"]["weights"]["AAPL"] == pytest.approx(0)


def test_whatif_without_a_portfolio_says_so():
    prov, _ = connected_provider()
    assert PortfolioService(prov, fake_analyze({})).whatif([])["available"] is False
