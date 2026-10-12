"""P3: names that passed every rule but were not funded are listed, each with
one plain reason (and, when one share is too expensive, the capital needed).

In the live Quick scan MSFT (one share 20.7% of $2,500 against the 18% cap) and
GOOG/GOOGL (same issuer, or correlated with a name in the book) passed every
rule and then appeared nowhere: selection only logged why. Synthetic names.
"""
from __future__ import annotations

import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import corporate_events as ce  # noqa: E402

from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick  # noqa: E402

CAP = 2_500.0


def _book(monkeypatch, picks, ciks, max_positions=5):
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system(CAP)
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear", cik=ciks.get(s)) for s in symbols}
    sysm.scan_results = picks
    out = sysm.get_top_recommendations(max_positions=max_positions)
    published = {r.ticker for r in out}
    return sysm, out, {d["ticker"]: d for d in sysm.passed_not_funded(published)}


def _expensive(ticker, price, seed):
    p = _pick(ticker, "Tech", 8.2, seed=seed, capital=CAP)
    p.entry_price, p.stop_loss, p.price_target = price, price * 0.95, price * 1.15
    p.risk_reward_ratio = 3.0
    return p


def test_a_name_whose_one_share_exceeds_the_cap_says_so_with_the_capital_needed(monkeypatch):
    cheap = _pick("CHEP", "Health", 8.0, seed=2, capital=CAP)
    sysm, out, skipped = _book(monkeypatch, [_expensive("PRCY", 517.53, 1), cheap], {"PRCY": 1, "CHEP": 2})
    assert "PRCY" not in {r.ticker for r in out}
    d = skipped["PRCY"]
    assert d["reason"] == "one_share_over_cap"
    assert "517.53" in d["text"] and "20.7%" in d["text"]
    need = d["capital_needed"]
    probe = _expensive("PRCY", 517.53, 1)
    assert sp.TradingConfig.one_share_allowed(probe, need)
    assert not sp.TradingConfig.one_share_allowed(probe, need - 100)


def test_a_second_share_class_and_a_correlated_name_are_listed(monkeypatch):
    first = _pick("CLSA", "Comm", 8.0, seed=7, capital=CAP)
    twin = _pick("CLSC", "Comm", 7.9, seed=8, capital=CAP)
    copy = _pick("COPY", "Tech", 7.8, seed=7, capital=CAP)          # same price path as CLSA
    _s, _out, skipped = _book(monkeypatch, [first, twin, copy], {"CLSA": 1, "CLSC": 1, "COPY": 3})
    assert skipped["CLSC"]["reason"] == "same_issuer" and "CLSA" in skipped["CLSC"]["text"]
    assert skipped["COPY"]["reason"] == "correlation" and "CLSA" in skipped["COPY"]["text"]


def test_names_never_tried_because_the_book_was_full_are_listed(monkeypatch):
    picks = [_pick(f"N{i}", f"S{i}", 8.0 - i * 0.1, seed=i + 1, capital=100_000.0) for i in range(3)]
    _install_fake_deep(monkeypatch, {})
    sysm = _bare_system(100_000.0)
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear", cik=i + 10) for i, s in enumerate(symbols)}
    sysm.scan_results = picks
    out = sysm.get_top_recommendations(max_positions=2)
    (d,) = sysm.passed_not_funded({r.ticker for r in out})
    assert d["ticker"] == "N2" and d["reason"] == "book_full" and "2" in d["text"]


def test_the_worker_publishes_the_list(monkeypatch):
    from test_scan_accounting import _run_worker, _watch
    res = _run_worker(monkeypatch, [_watch("W1", 1)])
    assert res["passed_not_funded"] == []
