"""Offline tests for the v4 sector-aware intrinsic value and blended target."""
from __future__ import annotations

import pytest

from engine.fair_value_v4 import DEFAULT_PARAMS, blended_target, intrinsic_value

P = dict(DEFAULT_PARAMS)
LOW_PB = {"sector": "Industrials", "trailingEps": 4.0, "bookValue": 30.0, "priceToBook": 1.5}
GRAHAM_LOW_PB = (22.5 * 4.0 * 30.0) ** 0.5


def test_asset_light_excludes_graham():
    # P/B ~6: Graham (~65) would drag the value down; only the DCF counts.
    info = {"sector": "Technology", "trailingEps": 2.5, "bookValue": 7.5, "priceToBook": 6.0}
    out = intrinsic_value(info, price=110.0, dcf_value=130.0, params=P)
    assert out["method"] == "dcf"
    assert out["value"] == pytest.approx(130.0)
    assert [c["source"] for c in out["components"]] == ["dcf"]


def test_low_pb_blends_dcf_and_graham():
    out = intrinsic_value(LOW_PB, price=45.0, dcf_value=60.0, params=P)
    assert out["method"] == "dcf+graham"
    assert out["value"] == pytest.approx((60.0 + GRAHAM_LOW_PB) / 2)


def test_graham_only_when_no_dcf():
    out = intrinsic_value(LOW_PB, price=45.0, dcf_value=None, params=P)
    assert out["method"] == "graham"


def test_bank_justified_pb():
    info = {"industry": "Banks - Regional", "sector": "Financial Services",
            "bookValue": 50.0, "returnOnEquity": 0.12}
    out = intrinsic_value(info, price=60.0, dcf_value=999.0, params=P)
    assert out["method"] == "justified_pb"
    assert out["value"] == pytest.approx(50 * (0.09 / 0.07))


def test_bank_roe_clipped():
    info = {"industry": "Insurance - Life", "bookValue": 50.0, "returnOnEquity": 0.60}
    out = intrinsic_value(info, price=100.0, dcf_value=None, params=P)
    assert out["value"] == pytest.approx(50 * (0.25 - 0.03) / 0.07)


def test_energy_fcf_yield_alone_and_with_dcf():
    info = {"sector": "Energy", "freeCashflow": 8e9, "sharesOutstanding": 1e9}
    alone = intrinsic_value(info, price=90.0, dcf_value=None, params=P)
    assert alone["method"] == "fcf_yield"
    assert alone["value"] == pytest.approx(100.0)  # 8 / 0.08
    both = intrinsic_value(info, price=90.0, dcf_value=80.0, params=P)
    assert both["value"] == pytest.approx(90.0)
    assert len(both["components"]) == 2


def test_energy_negative_fcf_uses_dcf_only():
    info = {"sector": "Energy", "freeCashflow": -1e9, "sharesOutstanding": 1e9}
    out = intrinsic_value(info, price=50.0, dcf_value=55.0, params=P)
    assert out["value"] == pytest.approx(55.0)
    assert out["method"] == "dcf"


def test_plausibility_band_drops_outlier():
    out = intrinsic_value(LOW_PB, price=100.0, dcf_value=500.0, params=P)  # 500 > 300
    assert out["method"] == "graham"
    assert "Dropped dcf" in out["note"]
    assert out["value"] == pytest.approx(GRAHAM_LOW_PB)


def test_all_outliers_unavailable():
    out = intrinsic_value({"sector": "Technology"}, price=100.0, dcf_value=1000.0, params=P)
    assert out["value"] is None and out["method"] == "unavailable"
    assert out["note"]


def test_missing_inputs_unavailable():
    assert intrinsic_value({}, price=100.0, dcf_value=None, params=P)["method"] == "unavailable"
    bank = intrinsic_value({"industry": "Banks"}, price=100.0, dcf_value=None, params=P)
    assert bank["value"] is None and "No usable inputs" in bank["note"]
    assert intrinsic_value({}, price=0.0, dcf_value=50.0, params=P)["value"] is None


def test_blended_target_renormalises():
    out = blended_target(100.0, 10, 80.0, P)
    assert out["value"] == pytest.approx((0.5 * 100 + 0.3 * 80) / 0.8)
    assert sum(c["weight"] for c in out["components"]) == pytest.approx(1.0)


def test_blended_target_thin_coverage():
    out = blended_target(100.0, 2, 80.0, P)
    assert out["value"] == pytest.approx((0.25 * 100 + 0.3 * 80) / 0.55)


def test_blended_target_single_and_none():
    assert blended_target(None, 0, 80.0, P)["value"] == pytest.approx(80.0)
    assert blended_target(100.0, 5, None, P)["value"] == pytest.approx(100.0)
    assert blended_target(None, 0, None, P) == {"value": None, "components": []}
