"""Guards for the market-cap fallback chain.

The bug this fixes, observed live: Yahoo returned a complete `.info` payload for
MU -- 183 keys, correct shortName, price and quoteType EQUITY -- but with
`marketCap` absent AND `sharesOutstanding` absent. The hard validation gate
treats a zero market cap as INVALID, so the engine refused to analyse a
trillion-dollar semiconductor company and told the user its ticker was invalid.
`fast_info` on the very same quote object carried the value.

Rejecting a real instrument because one optional provider field is missing --
while the number sits on another endpoint of the same object -- is a
data-plumbing failure being reported as a verdict about the user's ticker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _paths import ensure_paths  # noqa: E402

ensure_paths()

import TradingAnalysis_ELITE as elite  # noqa: E402

normalize_info = elite.QuantaEliteAnalyzer._normalize_info


class _FakeTicker:
    """Stands in for yf.Ticker, exposing only fast_info."""

    def __init__(self, fast_info=None):
        self.fast_info = fast_info


def _frame(last_close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": [last_close - 1, last_close]},
        index=pd.date_range("2026-01-01", periods=2, freq="B"),
    )


# ---------------------------------------------------------------------------
# The chain, in order
# ---------------------------------------------------------------------------


def test_present_market_cap_is_used_unchanged() -> None:
    """The authoritative field wins and nothing else is consulted."""
    info = {"marketCap": 5_000_000_000, "sharesOutstanding": 1}
    out = normalize_info(info, _FakeTicker(fast_info={"marketCap": 999}), _frame(), "X")
    assert out["marketCap"] == 5_000_000_000
    assert out["_market_cap_source"] == "info.marketCap"


def test_fund_assets_are_left_to_the_etf_branch() -> None:
    """ETFs report totalAssets; no fallback should be attempted for them."""
    for key in ("totalAssets", "netAssets"):
        out = normalize_info(
            {key: 795_306_885_120}, _FakeTicker(fast_info={"marketCap": 1}), _frame(), "SPY"
        )
        assert out["_market_cap_source"] == "info.totalAssets"
        assert "marketCap" not in out or not out.get("marketCap")


def test_fast_info_recovers_the_mu_case() -> None:
    """The exact live failure: marketCap and sharesOutstanding both absent."""
    info = {
        "shortName": "Micron Technology, Inc.",
        "quoteType": "EQUITY",
        "marketCap": None,
        "sharesOutstanding": None,
        "currentPrice": 935.39,
    }
    ticker = _FakeTicker(fast_info={"marketCap": 1_056_423_076_057.735})
    out = normalize_info(info, ticker, _frame(935.39), "MU")
    assert out["marketCap"] == pytest.approx(1_056_423_076_057.735)
    assert out["_market_cap_source"] == "fast_info"


def test_fast_info_attribute_spelling_is_accepted() -> None:
    """Some yfinance versions expose market_cap as an attribute, not a key."""

    class _AttrOnly:
        market_cap = 7_500_000_000

    out = normalize_info({"marketCap": None}, _FakeTicker(fast_info=_AttrOnly()), _frame(), "X")
    assert out["marketCap"] == 7_500_000_000
    assert out["_market_cap_source"] == "fast_info"


def test_derived_from_shares_and_price_when_fast_info_is_empty() -> None:
    info = {"marketCap": None, "sharesOutstanding": 1_000_000, "currentPrice": 250.0}
    out = normalize_info(info, _FakeTicker(fast_info={}), _frame(), "X")
    assert out["marketCap"] == pytest.approx(250_000_000.0)
    assert out["_market_cap_source"] == "derived:shares*price"


def test_derived_falls_back_to_last_close_and_implied_shares() -> None:
    """No price field in info: the frame's last close is used."""
    info = {"marketCap": None, "impliedSharesOutstanding": 2_000_000}
    out = normalize_info(info, _FakeTicker(fast_info=None), _frame(last_close=42.0), "X")
    assert out["marketCap"] == pytest.approx(84_000_000.0)
    assert out["_market_cap_source"] == "derived:shares*price"


def test_genuinely_unavailable_is_reported_not_invented() -> None:
    """When no source has it, the field stays absent and is labelled.

    The gate must still reject these -- silently substituting a fabricated
    market cap would be far worse than an INVALID verdict.
    """
    out = normalize_info({"marketCap": None}, _FakeTicker(fast_info=None), _frame(), "X")
    assert not out.get("marketCap")
    assert out["_market_cap_source"] == "unavailable"


# ---------------------------------------------------------------------------
# Robustness: enrichment must never break the fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [0, -1, float("nan"), float("inf"), "", "n/a", None, [], {}],
)
def test_non_positive_values_are_treated_as_absent(bad_value) -> None:
    """A zero, NaN or junk value must not be preferred over the next source."""
    info = {"marketCap": bad_value}
    out = normalize_info(info, _FakeTicker(fast_info={"marketCap": 123_456_789}), _frame(), "X")
    assert out["marketCap"] == 123_456_789
    assert out["_market_cap_source"] == "fast_info"


def test_raising_fast_info_does_not_break_enrichment() -> None:
    """fast_info can raise on delisted or thin symbols; fall through quietly."""

    class _Exploding:
        @property
        def fast_info(self):
            raise RuntimeError("no quote")

    info = {"marketCap": None, "sharesOutstanding": 10, "currentPrice": 5.0}
    out = normalize_info(info, _Exploding(), _frame(), "X")
    assert out["marketCap"] == pytest.approx(50.0)


def test_non_dict_info_is_handled() -> None:
    for junk in (None, [], "", 0):
        assert normalize_info(junk, _FakeTicker(), _frame(), "X") == {}


def test_empty_info_is_returned_untouched() -> None:
    """An empty payload means the symbol is bogus; do not add a source label."""
    assert normalize_info({}, _FakeTicker(fast_info={"marketCap": 1}), _frame(), "X") == {}


def test_input_dict_is_not_mutated() -> None:
    """The caller's dict must be left alone.

    `data.attrs['info']` used to be the same object as `stock.info`. Returning a
    copy keeps enrichment from leaking into a shared or cached payload.
    """
    info = {"marketCap": None, "sharesOutstanding": 100, "currentPrice": 10.0}
    before = dict(info)
    out = normalize_info(info, _FakeTicker(), _frame(), "X")
    assert info == before, "input info dict was mutated"
    assert out is not info


def test_missing_close_column_does_not_raise() -> None:
    """A malformed frame must degrade, not crash the fetch path."""
    out = normalize_info(
        {"marketCap": None, "sharesOutstanding": 100},
        _FakeTicker(fast_info=None),
        pd.DataFrame({"Open": [1.0]}),
        "X",
    )
    assert out["_market_cap_source"] == "unavailable"


# ---------------------------------------------------------------------------
# End to end through the validation gate
# ---------------------------------------------------------------------------


def test_recovered_market_cap_passes_the_validation_gate() -> None:
    """The point of the fix: MU must validate rather than read INVALID."""
    from engine import validate_ticker_data

    info = {"marketCap": None, "sharesOutstanding": None, "shortName": "Micron Technology, Inc."}
    out = normalize_info(
        info, _FakeTicker(fast_info={"marketCap": 1_056_423_076_057.735}), _frame(935.39), "MU"
    )

    report = validate_ticker_data(
        ticker="MU",
        current_price=935.39,
        history_length=502,
        market_cap=out.get("marketCap") or 0.0,
        company_name=out.get("shortName"),
        quote_type="EQUITY",
    )
    assert report.is_valid, report.reasons
    assert report.has_market_cap


def test_is_positive_number_helper() -> None:
    assert elite._is_positive_number(1)
    assert elite._is_positive_number(0.001)
    assert elite._is_positive_number("42")
    for bad in (0, -1, float("nan"), float("inf"), None, "", "abc", [], {}, np.nan):
        assert not elite._is_positive_number(bad), bad
