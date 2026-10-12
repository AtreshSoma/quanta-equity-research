"""A14: the scan sizes the menu advertises are the sizes the selector builds.

Executed, not a source-string check: the real US selector runs over
synthetic constituent lists (tests/_index_fixture.py) (the bulk liquidity download is refused in tests, so
selection takes its deterministic even-spread path) and must return exactly
the number of names the Picks menu promises for Quick, Standard and
Comprehensive. Audit mutation M8 (Quick 100 -> 7) survived the Python suite.
"""
from __future__ import annotations

import re

import pytest

from _paths import WEBAPP
from test_review_engine_fixes import _universe
from engine import thresholds as T  # noqa: E402

_MENU = (WEBAPP / "templates" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _index_sources(monkeypatch):
    """B-87: the universes come from constituent sources; serve synthetic ones."""
    import _index_fixture as fx
    fx.install(monkeypatch)


def _advertised(scan_type: str) -> int:
    """The size the menu shows: its option fills "{n} most liquid" from
    UNIVERSE.US_SCAN_LIMITS, the same config the selector reads."""
    m = re.search(rf'<option value="{scan_type}"[^>]*data-text-tpl="[^"]*\{{n\}} most liquid', _MENU)
    assert m, f"the menu no longer states a size for {scan_type}"
    return int(T.UNIVERSE.US_SCAN_LIMITS[scan_type])


@pytest.mark.parametrize("scan_type", ["quick", "standard", "comprehensive"])
def test_the_selector_builds_the_advertised_universe(scan_type):
    _, uni = _universe(scan_type)
    assert len(uni) == _advertised(scan_type), f"{scan_type}: built {len(uni)}, menu says {_advertised(scan_type)}"
    assert len(set(uni)) == len(uni), "no duplicates"


def test_the_quick_universe_is_a_spread_not_an_alphabetical_prefix():
    import _index_fixture as fx
    _, uni = _universe("quick")
    pool = sorted(set(fx.SP500) | set(fx.NASDAQ) | set(fx.DOW))
    assert uni != pool[:len(uni)], "not the first names of the alphabet"
    assert uni[-1] in pool[-len(pool) // 10:], "reaches the end of the pool"


def _label(scan_type: str) -> str:
    m = re.search(rf'<option value="{scan_type}"[^>]*>([^<]*)</option>', _MENU)
    assert m, scan_type
    return m.group(1)


@pytest.mark.parametrize("scan_type,pool", [("quick", "S&amp;P 500, NASDAQ-100 and Dow"),
                                            ("standard", "full universe"), ("comprehensive", "full universe")])
def test_the_menu_states_the_basis_the_selector_uses(monkeypatch, scan_type, pool):
    """P8: Quick said "most-watched large caps" while the selector ranked the
    S&P 500 + NASDAQ-100 + Dow pool by average daily dollar volume (the result
    read "100 most liquid of 518 names"). The label must name that basis."""
    import SP_ELITE_PRO as sp
    pools = []

    def spy(self, tickers, max_candidates=200, **_):
        pools.append(list(tickers))
        return list(tickers)[:max_candidates]
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "smart_prefilter", spy)
    _, uni = _universe(scan_type)
    assert pools, f"{scan_type} is liquidity-ranked"
    label = _label(scan_type)
    assert "most liquid" in label and pool in label, label
    assert "most-watched" not in label
    if scan_type == "quick":
        md = sp.MarketDataProvider()
        pool_names = set(md.get_complete_sp500()) | set(md.get_nasdaq100_tickers()) | set(md.get_dow30_tickers())
        assert set(pools[0]) <= pool_names, "Quick ranks only the index pool the label names"
