"""Research model v4 (shadow): its legs, its expected-value gate, and that it
never touches the live call. Offline; synthetic inputs."""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths()

from engine import research_v4 as v4  # noqa: E402
from engine.thresholds import RESEARCH_V4 as V4  # noqa: E402

LEGS = {"technical": 60.0, "fundamental": 70.0, "quality": 65.0, "sentiment": 55.0}
BANK = {"bookValue": 50.0, "returnOnEquity": 0.12, "priceToBook": 1.1, "trailingEps": 6.0,
        "sector": "Financial Services", "industry": "Banks - Regional"}
UPGRADING = {"sentiment": "UPGRADING", "revision_score": 8.0}


def _assess(**kw):
    base = dict(legs=LEGS, revision=UPGRADING, info=BANK, price=55.0, dcf_value=None,
                consensus_mean=66.0, analyst_count=20, entry=55.0, stop=52.0, atr=1.1, cost_frac=0.0011)
    base.update(kw)
    return v4.assess(**base)


def test_weights_are_v3_scaled_plus_revisions_and_sum_to_one():
    assert sum(V4.WEIGHTS.values()) == pytest.approx(1.0)
    assert V4.WEIGHTS["revisions"] == pytest.approx(0.20)


def test_legs_map_to_0_100():
    assert v4.valuation_leg(0.0) == 50.0 and v4.valuation_leg(1.0) == 100.0 and v4.valuation_leg(-1.0) == 0.0
    assert v4.revisions_leg({"sentiment": "UNKNOWN", "revision_score": 9}) is None
    assert v4.revisions_leg(UPGRADING) == 80.0


def test_a_missing_leg_is_left_out_and_the_weights_renormalise():
    c = v4.composite(dict(LEGS, valuation=50.0, revisions=None))
    assert "revisions" not in c["weights"] and sum(c["weights"].values()) == pytest.approx(1.0, abs=1e-3)


def test_a_bank_is_valued_on_justified_book_not_graham():
    out = _assess()
    assert out["target"]["intrinsic"]["method"] == "justified_pb"
    assert out["target"]["value"] == pytest.approx((66.0 * 0.5 + 50 * 0.09 / 0.07 * 0.3) / 0.8, rel=1e-3)


def test_a_buy_band_without_a_positive_expected_value_is_watchlist():
    # Target barely above price and a wide stop: the gate cannot pass.
    out = _assess(consensus_mean=56.0, info={}, entry=55.0, stop=45.0)
    assert out["ev_gate"]["passes"] is False
    assert out["band_action"] not in v4.BUY_TIER


def test_the_gate_reads_todays_price_not_a_pullback_entry():
    """A pullback entry far below spot keeps its stop distance but is re-anchored
    to the price: the GOOGL case (entry 13% below spot) cannot pass on the entry."""
    out = _assess(entry=48.0, stop=45.0)
    assert out["ev_gate"]["inputs"]["b"] == pytest.approx(-__import__("math").log((55.0 - 3.0) / 55.0), rel=1e-6)


def test_no_target_means_no_buy():
    out = _assess(consensus_mean=None, info={})
    assert out["target"]["value"] is None and out["band_action"] not in v4.BUY_TIER


def test_a_fund_drops_the_company_legs():
    c = v4.composite(dict(LEGS, valuation=70.0, revisions=80.0), is_fund=True)
    assert set(c["weights"]) == {"technical", "sentiment"}


def test_the_live_selector_cannot_choose_v4():
    from engine.thresholds import RESEARCH
    from engine.evidence import RESEARCH_KEYS
    assert RESEARCH.MODEL_VERSION in ("v2", "v3") and "v4" not in RESEARCH_KEYS
