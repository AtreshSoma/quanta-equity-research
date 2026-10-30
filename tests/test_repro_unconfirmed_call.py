"""Picks review 2026-10-29 (B1, B5): every Buy or Accumulate on the page has
passed the Research check, and a selection reason never names a pick the
Research check withdrew.

Seen live on 2026-10-29 15:37: CFG was skipped for correlation with C, C was
then withdrawn by the Research check, and CFG was published as the scan's only
Accumulate on the scanner's unchecked levels (R:R 2.71; Research put it at 0.73).
"""
from __future__ import annotations

import sys

from _paths import ensure_paths

ensure_paths()

import test_stock_picks_table as spt  # noqa: E402

ALL_WATCH = {"AMZN": "WATCHLIST", "CSCO": "WATCHLIST", "GOOG": "WATCHLIST",
             "GOOGL": "WATCHLIST", "MSFT": "WATCHLIST"}


def _run(monkeypatch, verdicts, twins=("AMZN", "CSCO")):
    """The table worker on synthetic rows; ``twins`` share one return path
    (correlation 1.0). Returns the result and the tickers Research checked."""
    seen = []
    orig = spt._install_fake_deep

    def spy(mp, v):
        orig(mp, v)
        deep = sys.modules["TradingAnalysis_ELITE"].QuantaEliteAnalyzer
        real = deep.analyze

        def analyze(self, ticker, **kw):
            seen.append(ticker)
            return real(self, ticker, **kw)
        deep.analyze = analyze

    monkeypatch.setattr(spt, "_install_fake_deep", spy)
    base = spt._row

    def row(t, score, price, seed, stop_pct=0.955):
        r = base(t, score, price, seed, stop_pct)
        if t in twins:
            r._hist_cache = base(twins[0], score, price, 1)._hist_cache * (price / 100.0)
        r.signal, r.final_action = "ACCUMULATE", "ACCUMULATE"
        return r

    monkeypatch.setattr(spt, "_row", row)
    return spt._run(monkeypatch, verdicts), seen


def test_every_buy_tier_call_on_the_page_was_research_checked(monkeypatch):
    result, checked = _run(monkeypatch, ALL_WATCH)
    unchecked = {r["ticker"]: r["capital"] for r in result["stock_picks"]
                 if r["call"] in ("buy", "accumulate") and r["ticker"] not in checked}
    assert not unchecked, f"buy-tier calls never Research-checked: {unchecked}"


def test_a_name_left_out_and_downgraded_by_research_is_a_watchlist_call(monkeypatch):
    """MSFT does not fit the capital; Research says Watchlist, so the page does too."""
    result, checked = _run(monkeypatch, ALL_WATCH)
    msft = next(r for r in result["stock_picks"] if r["ticker"] == "MSFT")
    assert "MSFT" in checked and msft["call"] == "watchlist"
    assert msft["capital"]["kind"] == "none"


def test_a_name_left_out_that_research_agrees_with_keeps_its_call(monkeypatch):
    """Research agrees with MSFT (Accumulate): it stays a call with its capital need."""
    verdicts = dict(ALL_WATCH, MSFT="ACCUMULATE")
    result, checked = _run(monkeypatch, verdicts)
    msft = next(r for r in result["stock_picks"] if r["ticker"] == "MSFT")
    assert "MSFT" in checked and msft["call"] == "accumulate"
    assert msft["capital"]["kind"] in ("needs", "not_sized")


def test_a_correlation_reason_never_names_a_withdrawn_pick(monkeypatch):
    """CSCO moves exactly with AMZN; AMZN is withdrawn, so CSCO is retried
    against the rebuilt book instead of keeping "Moves with AMZN"."""
    result, checked = _run(monkeypatch, {"AMZN": "WATCHLIST"})
    rows = {r["ticker"]: r for r in result["stock_picks"]}
    assert "CSCO" in checked, "CSCO is retried once AMZN is withdrawn"
    for r in rows.values():
        text = str((r.get("capital") or {}).get("text") or "")
        assert "AMZN" not in text, f"{r['ticker']} still cites withdrawn AMZN: {text}"
