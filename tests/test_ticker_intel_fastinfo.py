"""Backend regression: FastInfo keys are camelCase, so a snake_case read is
silently None -- which is why every India mover showed N/A for high and low.

US movers come from the Yahoo screener (which supplies its own OHLC), so the
bug was invisible there; India falls through to `_ticker_intel`.

Run: python -m pytest tests/test_ticker_intel_fastinfo.py -q
"""
import importlib.util
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "webapp", "app.py")


@pytest.fixture(scope="module")
def app_mod():
    os.environ.setdefault("QUANTA_NO_BROWSER", "1")
    spec = importlib.util.spec_from_file_location("quanta_app_under_test", APP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class CamelOnlyFastInfo:
    """Mimics yfinance.FastInfo: `keys()` exposes camelCase only, and `get()`
    validates against it, so `get("day_high")` returns the default silently."""

    _DATA = {
        "open": 100.0,
        "dayHigh": 110.0,
        "dayLow": 95.0,
        "lastPrice": 108.0,
        "previousClose": 99.0,
        "yearHigh": 150.0,
        "yearLow": 80.0,
    }

    def keys(self):
        return list(self._DATA)

    def __contains__(self, k):
        return k in self._DATA

    def __getitem__(self, k):
        if k not in self._DATA:
            raise KeyError(f"'{k}' not valid key. Examine 'FastInfo.keys()'")
        return self._DATA[k]

    def get(self, key, default=None):
        return self._DATA[key] if key in self._DATA else default


def test_snake_case_get_is_silently_none(app_mod):
    """The trap this fix exists for: no exception, just a missing number."""
    fi = CamelOnlyFastInfo()
    assert fi.get("day_high") is None
    assert fi.get("dayHigh") == 110.0


def test_open_extremes_returns_all_matches_without_eight_row_cap(app_mod):
    rows = [{"symbol": f"MATCH{i}", "open": 100, "day_low": 100,
             "day_high": 110, "change_pct": i} for i in range(15)]
    low, high = app_mod._select_open_extremes(rows)
    assert len(low) == 15
    assert high == []
    assert low[0]["symbol"] == "MATCH14"
    assert len(app_mod._select_open_extremes(rows, limit=3)[0]) == 3


def test_open_extremes_coverage_reconciles_and_excludes_unusable_rows(app_mod):
    rows = [
        {"symbol": "LOW", "open": 100, "day_low": 100, "day_high": 110},
        {"symbol": "low", "open": 100, "day_low": 100, "day_high": 110},
        {"symbol": "HIGH", "open": 110, "day_low": 100, "day_high": 110},
        {"symbol": "MIDDLE", "open": 105, "day_low": 100, "day_high": 110},
        {"symbol": "MISSING", "open": None, "day_low": 100, "day_high": 110},
        {"symbol": "FLAT", "open": 100, "day_low": 100, "day_high": 100},
        {"symbol": "INVALID", "open": 100, "day_low": 100, "day_high": 90},
    ]
    _, counts = app_mod._open_extreme_pool(rows)
    assert counts == {"received": 6, "comparable": 3, "missing_ohlc": 1, "invalid_ohlc": 1, "flat": 1}
    low, high = app_mod._select_open_extremes(rows)
    assert [r["symbol"] for r in low] == ["LOW"]
    assert [r["symbol"] for r in high] == ["HIGH"]


@pytest.mark.parametrize(
    "key, expected",
    [
        ("open", 100.0),
        ("day_high", 110.0),
        ("day_low", 95.0),
        ("last_price", 108.0),
        ("previous_close", 99.0),
        ("year_high", 150.0),
        ("year_low", 80.0),
    ],
)
def test_fast_info_get_reads_either_case(app_mod, key, expected):
    assert app_mod._fast_info_get(CamelOnlyFastInfo(), key) == expected


def test_fast_info_get_returns_none_for_unknown_and_bad_values(app_mod):
    class Odd(CamelOnlyFastInfo):
        _DATA = {"open": float("nan"), "dayHigh": None, "dayLow": "n/a", "lastPrice": 0}

    fi = Odd()
    assert app_mod._fast_info_get(fi, "open") is None, "NaN is not a price"
    assert app_mod._fast_info_get(fi, "day_high") is None
    assert app_mod._fast_info_get(fi, "day_low") is None, "a string is not a price"
    assert app_mod._fast_info_get(fi, "last_price") is None, "0 means absent here"
    assert app_mod._fast_info_get(fi, "not_a_field") is None


def test_snake_to_camel(app_mod):
    assert app_mod._snake_to_camel("day_high") == "dayHigh"
    assert app_mod._snake_to_camel("previous_close") == "previousClose"
    assert app_mod._snake_to_camel("open") == "open"
