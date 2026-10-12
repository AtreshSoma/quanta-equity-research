"""Scan filters and strategy presets, both markets.

* Filters (sector, market cap, price, traded value) only narrow a scan: a name
  outside one is screened out with that filter as its reason, and the outcome
  table still reconciles to the universe. Bad input is refused with a plain
  message before any scan starts.
* Presets: Momentum (US and India) reads the trend and the 3-month return;
  India Dividend asks for a real yield (India has no dividend-growth list);
  India Large/Mid/Small read the Nifty 100 / Midcap 150 / Smallcap 250, the
  most liquid names first; India Small-Cap has its own market-cap floor.

Synthetic data only: no network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import scan_filters, style_screen  # noqa: E402
from engine import thresholds as T  # noqa: E402

import test_scan_accounting as acc  # noqa: E402


# ── Filters: validation ─────────────────────────────────────────────────────

def test_filters_normalize_to_what_the_engine_reads():
    assert scan_filters.normalize(None) == {}
    assert scan_filters.normalize({"sectors": ["Technology"], "min_market_cap": 1e10, "max_price": 500, "min_price": ""}) == \
        {"sectors": ["Technology"], "min_market_cap": 1e10, "max_price": 500.0}
    assert scan_filters.normalize({"sectors": list(T.UNIVERSE.SECTORS)}) == {}, "every sector ticked is no filter"
    assert scan_filters.normalize({"min_daily_value": 0}) == {}, "zero is unset"


@pytest.mark.parametrize("raw, message", [
    ({"sectors": ["Crypto"]}, "unknown sector"),
    ({"min_price": 100, "max_price": 50}, "min_price is above max_price"),
    ({"min_market_cap": -1}, "zero or more"),
    ({"min_price": "ten"}, "must be a number"),
    ({"pe": 10}, "unknown filter"),
    ([1, 2], "must be an object"),
])
def test_bad_filters_are_refused_with_a_plain_message(raw, message):
    with pytest.raises(ValueError, match=message):
        scan_filters.normalize(raw)


def test_a_name_outside_a_filter_gets_that_filter_as_its_reason():
    f = {"sectors": ["Healthcare"], "min_market_cap": 1e10, "max_price": 300.0, "min_daily_value": 5e7}
    base = dict(sector="Healthcare", market_cap=2e10, price=100.0, daily_value=1e8, is_fund=False)
    assert scan_filters.miss(f, **base) is None
    assert scan_filters.miss(f, **{**base, "sector": "Technology"}) == "user_sector"
    assert scan_filters.miss(f, **{**base, "market_cap": 5e9}) == "user_market_cap"
    assert scan_filters.miss(f, **{**base, "price": 400.0}) == "user_price"
    assert scan_filters.miss(f, **{**base, "daily_value": 1e7}) == "user_liquidity"
    fund = {**base, "sector": "", "market_cap": 0.0, "is_fund": True}
    assert scan_filters.miss(f, **fund) is None, "a fund has no sector or market cap to filter on"


def test_filters_screen_names_out_and_the_outcomes_still_reconcile(monkeypatch):
    acc._install(monkeypatch)
    from test_elite_pro_review2_fixes import _install_fake_deep
    _install_fake_deep(monkeypatch, {})
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.user_filters = {"sectors": ["Healthcare"]}          # every synthetic name is Technology
    eng.scan_market(scan_type="custom", custom_tickers=list(acc._NAMES))
    a = eng.scan_accounting()
    assert a["rule_rejected"]["by_reason"].get("user_sector", 0) >= 1
    assert a["reached_book"] == 0
    total = a["could_not_analyse"]["count"] + a["rule_rejected"]["count"] + a["reached_book"] + a["not_analysed"]
    assert total == a["universe"]


def test_the_endpoint_refuses_bad_filters_before_any_scan(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
    import app as webapp
    before = set(webapp._jobs)
    client = webapp.app.test_client()
    r = client.post("/api/stock-picks", headers={"X-QUANTA-Request": "1"}, json={"market": "US", "capital": 25000, "scan_type": "quick",
                                               "filters": {"min_price": 100, "max_price": 50}})
    assert r.status_code == 400 and "filters" in r.get_json()["error"]
    assert set(webapp._jobs) == before


# ── Presets: style screens ──────────────────────────────────────────────────

def _rising(n=260, start=100.0, step=0.4):
    return [start + i * step for i in range(n)]


def test_momentum_passes_an_uptrend_and_refuses_a_fall():
    up = style_screen.screen("momentum", SimpleNamespace(), closes=_rising())
    assert up["passed"] and {c["key"] for c in up["checks"]} == {"trend", "return_3m"}
    down = style_screen.screen("momentum", SimpleNamespace(), closes=list(reversed(_rising())))
    assert not down["passed"] and down["reason"] == "not in an established uptrend"
    flat_end = _rising()[:-63] + [_rising()[-64] * 1.01] * 63          # trend intact, 3-month gain small
    slow = style_screen.screen("momentum", SimpleNamespace(), closes=flat_end)
    assert not slow["passed"]
    short = style_screen.screen("momentum", SimpleNamespace(), closes=_rising(120))
    assert not short["passed"], "under 200 sessions there is no 200-day average to read"


def test_india_dividend_asks_for_a_real_yield_instead_of_a_list():
    fm = SimpleNamespace(dividend_yield=0.02, payout_ratio=0.4, data_available={})
    assert style_screen.screen("dividend", fm, on_list=None)["passed"]
    thin = SimpleNamespace(dividend_yield=T.STYLE.DIVIDEND_MIN_YIELD_NO_LIST / 2, payout_ratio=0.4, data_available={})
    out = style_screen.screen("dividend", thin, on_list=None)
    assert not out["passed"] and "aristocrat" not in {c["key"] for c in out["checks"]}
    us = style_screen.screen("dividend", fm, on_list=False)
    assert not us["passed"], "the US Dividend scan still needs the Aristocrats list"


def test_every_preset_maps_to_a_style_or_an_index():
    assert {"india_growth", "india_value", "india_dividend", "india_momentum", "focused_momentum"} <= set(style_screen.STYLE_OF_SCAN)
    idx = sp.QuantaEliteTradingSystem.INDIA_SCAN_INDEXES
    assert idx["india_large"] == ("nifty100",) and idx["india_mid"] == ("niftymidcap150",)
    assert idx["india_small"] == ("niftysmallcap250",)
    assert sp.india_min_market_cap_for("india_small") < sp.india_min_market_cap_for("india_quick")


def test_an_india_preset_scans_the_most_liquid_names_of_its_index(monkeypatch):
    names = [f"S{i:03d}.NS" for i in range(251)]
    monkeypatch.setattr(sp.MarketDataProvider, "get_index_constituents", lambda self, key: {
        "key": key, "label": "Nifty Smallcap 250", "tickers": list(names), "source": "live", "as_of": "2026-10-06", "cause": ""})
    ranked = []
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "smart_prefilter",
                        lambda self, tickers, max_candidates, **_: ranked.append(len(tickers)) or list(reversed(tickers))[:max_candidates])
    eng = sp.QuantaEliteTradingSystem(capital=200_000, market="INDIA")
    out = eng.build_indian_stock_universe("india_small")
    assert len(out) == T.UNIVERSE.INDIA_SCAN_LIMITS["india_small"] and ranked == [251]
    assert out[0] == "S250.NS", "ranked by the liquidity selector, not alphabetically"
    assert eng._universe_selection["selected"] == len(out)


def test_the_liquidity_ranker_holds_each_listing_to_its_own_market_floors(monkeypatch):
    """An NSE name above Rs 5,000 with India-sized volume is eligible; the US
    dollar floors used to drop it (and most mid-caps) from India presets."""
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2026-04-01", periods=60, freq="B")
    spec = {"MRF.NS": (8000.0, 300_000), "MIDCO.NS": (900.0, 400_000), "THIN.NS": (100.0, 50_000),
            "BIG": (6000.0, 2_000_000), "LIQ": (100.0, 5_000_000)}
    frames = {t: pd.DataFrame({"Close": np.full(60, p), "Volume": np.full(60, v)}, index=idx) for t, (p, v) in spec.items()}
    data = pd.concat(frames, axis=1)
    eng = sp.QuantaEliteTradingSystem(capital=200_000, market="INDIA")
    monkeypatch.setattr(eng, "_bounded_bulk_download",
                        lambda tickers, period, budget: SimpleNamespace(status="ok", value=data, seconds=0.1, error=None))
    out = eng.smart_prefilter(list(spec), max_candidates=4)
    assert "MRF.NS" in out and "MIDCO.NS" in out, "India names are held to the India floors"
    assert "THIN.NS" not in out, "below India's own volume floor"
    assert "BIG" not in out and "LIQ" in out, "US names keep the US floors"


def test_value_judges_profit_on_earnings_when_free_cash_flow_is_not_reported():
    """Yahoo reports no free cash flow for Indian companies or banks; the screen
    used to fail all of them (ONGC at a forward P/E of 5)."""
    ongc = SimpleNamespace(pe_ratio=6.0, forward_pe=5.4, ev_ebitda=4.4, pb_ratio=0.76, free_cash_flow=0.0,
                           market_cap=3e12, data_available={"free_cash_flow": False})
    out = style_screen.screen("value", ongc)
    assert out["passed"], out["reason"]
    assert out["checks"][0]["label"] == "Positive earnings (free cash flow not reported)"
    burning = SimpleNamespace(pe_ratio=6.0, forward_pe=5.4, ev_ebitda=4.4, pb_ratio=0.76, free_cash_flow=-5e9,
                              market_cap=3e12, data_available={})
    assert not style_screen.screen("value", burning)["passed"], "a reported cash burn still fails"
    loss = SimpleNamespace(pe_ratio=0.0, forward_pe=0.0, ev_ebitda=4.4, pb_ratio=0.76, free_cash_flow=0.0,
                           market_cap=3e12, data_available={"free_cash_flow": False, "pe_ratio": False, "forward_pe": False})
    assert not style_screen.screen("value", loss)["passed"], "no earnings is not value"
