"""Audit B-82: insider buying relaxes the new-money hurdles only in proportion
to how much buying there was, and by how many insiders.

Before, any net purchase with buys > sells read "BULLISH" and switched on every
relaxation at full strength (lower upside, R/R, score and Buy-tier hurdles, a
looser analyst-downside limit, and no RSI-extended veto). Now the insider read
carries a relaxation strength from 0 to 1, from INSIDER config:

* below INSIDER.RELAX_MIN_NET_USD of net open-market buying: 0 (no relaxation);
* size: net buying / INSIDER.RELAX_FULL_NET_USD, capped at 1;
* breadth: distinct buying insiders / INSIDER.RELAX_FULL_BUYERS, capped at 1;
* strength = size x breadth. Each relaxation is scaled by it; the RSI-extended
  veto is skipped only at INSIDER.RSI_VETO_SKIP_MIN_STRENGTH or more.

Full strength is exactly the old full relaxation, so no hurdle is ever looser
than before; the base hurdles and the heavy-selling veto are unchanged.
Synthetic transactions only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import SP_ELITE_PRO as sp
from engine import thresholds as T

PURCHASE = "Purchase at price 100.00 per share."
SALE = "Sale at price 100.00 per share."


def _signal(rows, market_cap=5e10, price=100.0):
    recent = datetime.now() - timedelta(days=5)
    frame = pd.DataFrame([{"Shares": value / price, "Value": value, "URL": "", "Text": text,
                           "Insider": who, "Position": "Director", "Transaction": "",
                           "Start Date": recent, "Ownership": "D"} for who, value, text in rows])

    class _P:
        insider_transactions = frame
    obj = object.__new__(sp.QuantaEliteTradingSystem)
    return sp.QuantaEliteTradingSystem._fetch_insider_signal(obj, "TEST", _P(), price, market_cap)


def _committee(strength, sentiment="BULLISH", upside=7.0, rsi=50.0, signal=None, score=7.0, rr=2.5):
    obj = object.__new__(sp.QuantaEliteTradingSystem)
    entry = 100.0
    return sp.QuantaEliteTradingSystem._apply_investment_committee_policy(
        obj, signal=signal or sp.SignalType.ACCUMULATE.value, score=score, entry=entry,
        target=entry * (1 + upside / 100), stop=95.0, risk_reward=rr, risk_grade="B",
        rsi=rsi, insider_sentiment=sentiment, insider_note="Net insider buying",
        insider_relax_strength=strength)


def test_one_small_purchase_relaxes_nothing():
    small = T.INSIDER.RELAX_MIN_NET_USD / 2
    r = _signal([("A", small, PURCHASE)])
    assert r["sentiment"] == "BULLISH"            # still reported as buying
    assert r["relax_strength"] == 0.0
    assert r["score_adjustment"] == 0.0           # and no score lift either
    # Upside between the bullish and the base hurdle: held, as for no insider read.
    mid = (T.COMMITTEE.UPSIDE_HURDLE_PCT + T.COMMITTEE.UPSIDE_HURDLE_BULLISH_INSIDER_PCT) / 2
    assert _committee(r["relax_strength"], upside=mid)[0] == sp.SignalType.HOLD.value
    # And the RSI-extended veto still applies to a Buy.
    hot = T.COMMITTEE.RSI_EXTENDED + 5
    assert _committee(r["relax_strength"], upside=20, rsi=hot, signal=sp.SignalType.BUY.value)[0] == sp.SignalType.HOLD.value


def test_large_broad_buying_keeps_todays_full_relaxation():
    each = T.INSIDER.RELAX_FULL_NET_USD / T.INSIDER.RELAX_FULL_BUYERS
    r = _signal([(f"I{i}", each, PURCHASE) for i in range(int(T.INSIDER.RELAX_FULL_BUYERS))])
    assert r["relax_strength"] == pytest.approx(1.0)
    mid = (T.COMMITTEE.UPSIDE_HURDLE_PCT + T.COMMITTEE.UPSIDE_HURDLE_BULLISH_INSIDER_PCT) / 2
    assert _committee(1.0, upside=mid)[0] == sp.SignalType.ACCUMULATE.value
    hot = T.COMMITTEE.RSI_EXTENDED + 5
    assert _committee(1.0, upside=20, rsi=hot, signal=sp.SignalType.BUY.value)[0] == sp.SignalType.BUY.value


def test_strength_grows_with_size_and_with_distinct_buyers_and_is_capped():
    full = T.INSIDER.RELAX_FULL_NET_USD
    one_buyer = _signal([("A", full, PURCHASE)])["relax_strength"]
    two_buyers = _signal([("A", full / 2, PURCHASE), ("B", full / 2, PURCHASE)])["relax_strength"]
    smaller = _signal([("A", full / 4, PURCHASE), ("B", full / 4, PURCHASE)])["relax_strength"]
    huge = _signal([("A", full * 10, PURCHASE), ("B", full * 10, PURCHASE), ("C", full, PURCHASE)])["relax_strength"]
    assert 0 < smaller < two_buyers
    assert 0 < one_buyer < two_buyers, "the same money from more insiders is stronger evidence"
    assert huge == pytest.approx(1.0)
    # Repeat purchases by one insider are one buyer, not several.
    repeat = _signal([("A", full / 2, PURCHASE), ("A", full / 2, PURCHASE)])["relax_strength"]
    assert repeat == pytest.approx(one_buyer)


def test_selling_alongside_buying_gives_no_relaxation():
    full = T.INSIDER.RELAX_FULL_NET_USD
    r = _signal([("A", full, PURCHASE), ("B", full * 2, SALE), ("C", full, SALE)])
    assert r["relax_strength"] == 0.0


def test_each_hurdle_moves_in_proportion_and_never_past_the_old_full_relaxation():
    base = T.COMMITTEE.UPSIDE_HURDLE_PCT
    relaxed = T.COMMITTEE.UPSIDE_HURDLE_BULLISH_INSIDER_PCT
    for s in (0.0, 0.25, 0.5, 0.75, 1.0):
        hurdle = base - (base - relaxed) * s
        assert _committee(s, upside=hurdle + 0.01)[0] == sp.SignalType.ACCUMULATE.value, s
        if hurdle - 0.01 >= relaxed:
            assert _committee(s, upside=hurdle - 0.01)[0] == sp.SignalType.HOLD.value, s
    # Below the fully relaxed hurdle nothing passes, whatever the strength.
    assert _committee(1.0, upside=relaxed - 0.5)[0] == sp.SignalType.HOLD.value


def test_the_rsi_veto_is_skipped_only_at_the_configured_strength():
    hot = T.COMMITTEE.RSI_EXTENDED + 5
    need = T.INSIDER.RSI_VETO_SKIP_MIN_STRENGTH
    below = max(0.0, need - 0.01)
    assert _committee(below, upside=20, rsi=hot, signal=sp.SignalType.BUY.value)[0] == sp.SignalType.HOLD.value
    assert _committee(need, upside=20, rsi=hot, signal=sp.SignalType.BUY.value)[0] == sp.SignalType.BUY.value


def test_the_scale_comes_from_config(monkeypatch):
    full = T.INSIDER.RELAX_FULL_NET_USD
    rows = [("A", full / 2, PURCHASE), ("B", full / 2, PURCHASE)]
    before = _signal(rows)["relax_strength"]
    monkeypatch.setattr(T.INSIDER, "RELAX_FULL_NET_USD", full * 4)
    assert _signal(rows)["relax_strength"] == pytest.approx(before / 4)


def test_the_heavy_selling_veto_is_unchanged():
    signal, notes = _committee(0.0, sentiment="BEARISH", upside=30)
    assert signal == sp.SignalType.ACCUMULATE.value      # plain bearish is not a veto
    obj = object.__new__(sp.QuantaEliteTradingSystem)
    signal, notes = sp.QuantaEliteTradingSystem._apply_investment_committee_policy(
        obj, signal=sp.SignalType.BUY.value, score=9.0, entry=100.0, target=130.0, stop=90.0,
        risk_reward=3.0, risk_grade="A", insider_sentiment="BEARISH",
        insider_note="Heavy insider selling $500.0M net, 9 sells")
    assert signal == sp.SignalType.HOLD.value
