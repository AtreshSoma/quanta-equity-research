"""Picks revamp, stage 1: every scanned name has one named outcome, and a
Watchlist call from a Research downgrade says what would clear it.

- ``accounting.rule_rejected`` names each rule-rejected ticker, so the names
  that failed a floor are listed, not only counted.
- A Research reward/risk downgrade publishes its remedy as the row's fix.
- The remedy and the target basis are quoted in the listing's currency.
Synthetic providers only.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine.levels import publish_levels  # noqa: E402


def test_every_scanned_name_has_exactly_one_named_outcome(monkeypatch):
    import test_scan_accounting as acc
    from test_elite_pro_review2_fixes import _install_fake_deep
    acc._install(monkeypatch)
    _install_fake_deep(monkeypatch, {})
    eng = sp.QuantaEliteTradingSystem(capital=25_000, market="US")
    eng.scan_market(scan_type="custom", custom_tickers=list(acc._NAMES))
    a = eng.scan_accounting()
    named = [t for ts in a["could_not_analyse"]["tickers"].values() for t in ts]
    named += [t for ts in a["rule_rejected"]["tickers"].values() for t in ts]
    reached = [t for t, (cat, _) in eng._outcomes.items() if cat == "analyzed"]
    assert len(named) == len(set(named)), "no name has two outcomes"
    assert set(named).isdisjoint(reached)
    assert len(named) + len(reached) + a["not_analysed"] == a["universe"] == len(acc._NAMES)
    assert sum(len(v) for v in a["rule_rejected"]["tickers"].values()) == a["rule_rejected"]["count"]


def test_a_research_downgrade_names_the_prices_that_clear_it():
    lv = publish_levels("research", 110.86, 106.03, 117.98, 1.41, atr=3.22, cost_frac=0.0011,
                        horizon_days=63, target_basis="t", min_rr=1.5)
    assert lv["triggers"]["entry_needed"] == 110.69 and lv["triggers"]["target_needed"] == 118.41
    assert "$110.69" in lv["remedy"] and "$118.41" in lv["remedy"]


def test_an_nse_remedy_is_quoted_in_rupees():
    lv = publish_levels("research", 2500.0, 2400.0, 2560.0, 0.5, atr=40.0, cost_frac=0.0011,
                        horizon_days=63, target_basis="t", min_rr=1.5, currency="₹")
    assert "₹" in lv["remedy"] and "$" not in lv["remedy"]
    assert sp._listing_currency("RELIANCE.NS") == "₹" and sp._listing_currency("AAPL") == "$"


def test_the_table_row_carries_the_research_remedy_as_its_fix(monkeypatch):
    import test_stock_picks_table as tbl

    def _bind(self, pick, label):
        # A Research downgrade on reward/risk: binding levels below the 1.50 hurdle.
        pick.binding_levels = publish_levels("research", pick.entry_price, pick.entry_price * 0.96,
                                             pick.entry_price * 1.05, 1.0, atr=1.0, cost_frac=0.0011,
                                             horizon_days=63, target_basis="t", min_rr=1.5)
    monkeypatch.setattr(sp.QuantaEliteTradingSystem, "_bind_scanner_levels", _bind, raising=False)
    result = tbl._run(monkeypatch)
    watch = [r for r in result["stock_picks"] if r["call"] == "watchlist"]
    assert watch, "the fixture has a Watchlist call"
    for r in watch:
        assert r["fix"] and "needs 1.50" in r["fix"], r
