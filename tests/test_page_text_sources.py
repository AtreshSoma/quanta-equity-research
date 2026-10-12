"""I8: every count on the Picks coverage line and outcome table comes from a
published field that says what it counts.

"100 of 204 names ... ranked in 5.9 s" never said that 204 was the S&P 500
growth-sector pool of an 806-name universe, and 5.9 s was the price download,
not a ranking. "Could not be analysed 1" named no ticker: the validation failure
was logged at DEBUG and the accounting published counts only. Synthetic data.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402


def test_the_index_selector_publishes_its_pool_and_source():
    """A cap-focus scan says which index list it read (live or the dated
    snapshot) and what its "of N" counts; the seconds are the price download."""
    eng = object.__new__(sp.QuantaEliteTradingSystem)
    pool = [f"T{i:03d}" for i in range(120)]
    eng.market_data = SimpleNamespace(get_index_constituents=lambda key: {
        "key": key, "label": "S&P 400 mid-cap", "tickers": pool, "source": "snapshot",
        "as_of": "2026-10-05", "cause": "the live list could not be read (synthetic)"})
    eng._universe_selection = {"scan_type": "mid_cap", "method": "listed_order"}
    eng._bounded_bulk_download = lambda tickers, period, budget: SimpleNamespace(
        status="error", error="synthetic", seconds=5.9, value=None)
    picked = eng._select_index_universe("mid_cap", [], [], 30)
    sel = eng._universe_selection
    assert len(picked) == 30 and set(picked) <= set(pool)
    assert sel["universe_total"] == 120 and sel["candidates"] == 120
    assert sel["pool_label"] == "S&P 400 mid-cap names"
    assert sel["index_source"] == "snapshot" and sel["index_as_of"] == "2026-10-05"
    assert sel["seconds_measure"] == "price_download"


def test_names_that_could_not_be_analysed_are_listed_by_reason(monkeypatch, caplog):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {})
    real = sp.DataValidator.validate_history

    def flaky(hist, ticker, **kw):
        res = real(hist, ticker, **kw)
        if ticker == "SYNB":
            res.is_valid, res.errors = False, ["synthetic: stale bars"]
        return res
    monkeypatch.setattr(sp.DataValidator, "validate_history", staticmethod(flaky))
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    with caplog.at_level(logging.WARNING):
        eng.scan_market(scan_type="custom", custom_tickers=list(acc._NAMES))
    failed = eng.scan_accounting()["could_not_analyse"]
    assert failed["tickers"]["data_validation_failed"] == ["SYNB"]
    assert sum(len(v) for v in failed["tickers"].values()) == failed["count"]
    assert any("SYNB" in r.getMessage() and "validation" in r.getMessage().lower()
               for r in caplog.records if r.levelno >= logging.WARNING)
