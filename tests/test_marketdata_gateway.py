"""P1-2 phase 1: the market-data gateway (core/marketdata/gateway.py).

One request path that tries providers in order, checks each answer, and says
who served it. No network: providers are replaced with fakes.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from marketdata import gateway as gw


def _bars(n=300, start=100.0, jump_at=None):
    idx = pd.bdate_range("2025-01-02", periods=n)
    close = start * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, n)))
    if jump_at is not None:
        close[jump_at:] = close[jump_at:] / 2.0          # a 2:1 split left unadjusted
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1e6}, index=idx)


def _csv(frame):
    out = frame.copy()
    out.index.name = "Date"
    return out.reset_index().to_csv(index=False)


@pytest.fixture
def providers(monkeypatch):
    calls = []

    def install(**fakes):
        for name, fn in fakes.items():
            def wrapped(q, fn=fn, name=name):
                calls.append((name, q))
                return fn(q)
            monkeypatch.setitem(gw.BAR_PROVIDERS, name, wrapped)
        return calls
    return install


def test_the_first_provider_that_answers_serves_with_provenance(providers):
    calls = providers(yfinance=lambda q: _bars())
    env = gw.price_bars("aapl")
    assert env.provider == "yfinance" and env.warnings == []
    assert env.as_of == _bars().index.max().date().isoformat()
    assert env.attempts == [{"provider": "yfinance", "result": "ok"}]
    assert calls[0][1].symbol == "AAPL" and calls[0][1].adjusted is False
    assert set(env.provenance()) == {"provider", "as_of", "retrieved_at", "warnings", "attempts"}


def test_an_empty_answer_falls_back_and_says_so(providers):
    providers(yfinance=lambda q: pd.DataFrame(), stooq=lambda q: _bars())
    env = gw.price_bars("AAPL")
    assert env.provider == "stooq"
    assert env.attempts[0] == {"provider": "yfinance", "result": "no rows"}
    assert any("Served by stooq" in w for w in env.warnings)
    assert any("not dividend-adjusted" in w for w in env.warnings)


def test_unusable_frames_are_refused(providers):
    bad = _bars()
    bad.iloc[5, bad.columns.get_loc("Close")] = -1.0
    providers(yfinance=lambda q: bad, stooq=lambda q: pd.DataFrame({"Open": [1.0]}))
    with pytest.raises(gw.DataUnavailable) as err:
        gw.price_bars("AAPL")
    assert [a["result"] for a in err.value.attempts] == ["non-positive closes", "no Close column"]


def test_a_provider_error_is_recorded_and_the_next_is_tried(providers):
    def boom(q):
        raise TimeoutError("read timed out")
    providers(yfinance=boom, stooq=lambda q: _bars())
    env = gw.price_bars("AAPL")
    assert env.attempts[0]["result"].startswith("TimeoutError")


def test_a_caller_can_forbid_fallback(providers):
    calls = providers(yfinance=lambda q: pd.DataFrame(), stooq=lambda q: _bars())
    with pytest.raises(gw.DataUnavailable):
        gw.price_bars("AAPL", providers=("yfinance",))
    assert [c[0] for c in calls] == ["yfinance"]


# ------------------------------------------------------------------- Stooq
def test_stooq_parses_its_csv(monkeypatch):
    monkeypatch.setattr(gw, "STOOQ_GET", lambda url: _csv(_bars()))
    frame = gw._stooq_bars(gw.BarsQuery("BRK-B"))
    assert list(frame.columns) == list(gw.OHLCV) and len(frame) == 300


def test_stooq_url_uses_the_us_suffix_and_the_window(monkeypatch):
    seen = []
    monkeypatch.setattr(gw, "STOOQ_GET", lambda url: seen.append(url) or _csv(_bars()))
    gw._stooq_bars(gw.BarsQuery("BRK-B", start=date(2025, 1, 2), end=date(2026, 3, 4)))
    assert "s=brk.b.us" in seen[0] and "d1=20250102" in seen[0] and "d2=20260304" in seen[0]


def test_stooq_is_never_used_for_adjusted_or_non_us_requests(monkeypatch):
    monkeypatch.setattr(gw, "STOOQ_GET", lambda url: _csv(_bars()))
    with pytest.raises(LookupError):
        gw._stooq_bars(gw.BarsQuery("AAPL", adjusted=True))
    with pytest.raises(LookupError):
        gw._stooq_bars(gw.BarsQuery("RELIANCE.NS"))
    with pytest.raises(LookupError):
        gw._stooq_bars(gw.BarsQuery("^GSPC"))


def test_stooq_refuses_a_series_unadjusted_across_a_split(monkeypatch):
    monkeypatch.setattr(gw, "STOOQ_GET", lambda url: _csv(_bars(jump_at=150)))
    with pytest.raises(LookupError, match="split"):
        gw._stooq_bars(gw.BarsQuery("AAPL"))


def test_a_browser_check_page_is_a_refusal_not_missing_data(monkeypatch):
    page = '<!DOCTYPE html><html><body><noscript>This site requires JavaScript</noscript></body></html>'
    monkeypatch.setattr(gw, "STOOQ_GET", lambda url: page)
    for _ in range(gw.STOOQ_FAILURE_LIMIT):
        with pytest.raises(LookupError, match="browser-check"):
            gw._stooq_bars(gw.BarsQuery("AAPL"))
    with pytest.raises(LookupError, match="switched off"):
        gw._stooq_bars(gw.BarsQuery("AAPL"))


def test_stooq_switches_off_after_repeated_failures(monkeypatch):
    attempts = []

    def down(url):
        attempts.append(url)
        raise ConnectionError("unreachable")
    monkeypatch.setattr(gw, "STOOQ_GET", down)
    for _ in range(gw.STOOQ_FAILURE_LIMIT):
        with pytest.raises(ConnectionError):
            gw._stooq_bars(gw.BarsQuery("AAPL"))
    with pytest.raises(LookupError, match="switched off"):
        gw._stooq_bars(gw.BarsQuery("AAPL"))
    assert len(attempts) == gw.STOOQ_FAILURE_LIMIT


# --------------------------------------------------------- migrated callers
def test_plan_bars_come_through_the_gateway(providers):
    import plan_routes
    providers(yfinance=lambda q: pd.DataFrame(), stooq=lambda q: _bars())
    assert len(plan_routes.load_bars("AAPL")) == 300
    providers(yfinance=lambda q: pd.DataFrame(), stooq=lambda q: pd.DataFrame())
    with pytest.raises(plan_routes.PlanDataError, match="No daily price bars"):
        plan_routes.load_bars("AAPL")


def test_the_ledger_loader_is_yahoo_only_and_adjusted(providers):
    from tracking.ledger import yfinance_loader
    calls = providers(yfinance=lambda q: pd.DataFrame(), stooq=lambda q: _bars())
    out = yfinance_loader("AAPL", date(2026, 1, 2), date(2026, 3, 4))
    assert out.empty and [c[0] for c in calls] == ["yfinance"]
    assert calls[0][1].adjusted is True and calls[0][1].end == date(2026, 3, 4)
