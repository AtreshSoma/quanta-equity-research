"""Scan data faults found on 2026-11-09, each pinned.

* Real -20% earnings days were read as unadjusted 5:4 splits (FTNT, APP, MSTR,
  BE, SNDK): the name was dropped as "data validation failed".
* A newest bar whose close sat outside its own range voided the whole name;
  nine names were clean an hour later.
* Yahoo's news endpoint raised for 134 of 144 names; the scanner never reached
  its RSS fallback, and the Research sentiment leg had none.
* The highest-scored names stopped only by reward/risk were not reported.
"""
from __future__ import annotations

import threading
import types

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import thresholds as T  # noqa: E402

DV = sp.DataValidator


def _bars(n=260, end="2026-11-06"):
    idx = pd.bdate_range(end=end, periods=n)
    close = np.linspace(150, 175, n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1_000_000.0}, index=idx)


def _ftnt_like(days_ago=6):
    """A -20.1% day landing on 5:4 with volume up 1.56x (FTNT, 2026-10-29)."""
    h = _bars()
    i = len(h) - 1 - days_ago
    h.iloc[i:, h.columns.get_loc("Close")] = h["Close"].iloc[i:] * (139.10 / 174.08)
    for c in ("Open", "High", "Low"):
        h.iloc[i:, h.columns.get_loc(c)] = h["Close"].iloc[i:] * {"Open": 1.0, "High": 1.01, "Low": 0.99}[c]
    h.iloc[i, h.columns.get_loc("Volume")] = 1_560_000.0
    return h, i


# ── split-sized real moves ───────────────────────────────────────────────────

def test_the_signature_alone_still_flags_a_split_sized_move():
    h, i = _ftnt_like()
    assert DV.split_like_days(h["Close"], h["Volume"]) == [i]
    assert not DV.validate_history(h, "FTNT").is_valid, "with no split list, the old rule holds"


def test_a_split_sized_move_with_no_listed_split_is_a_real_move():
    h, _i = _ftnt_like()
    res = DV.validate_history(h, "FTNT", listed_split_dates=[])
    assert res.is_valid
    assert any("real price moves" in w for w in res.warnings)


def test_a_listed_split_near_the_day_keeps_it_an_error():
    h, i = _ftnt_like()
    res = DV.validate_history(h, "X", listed_split_dates=[h.index[i] - pd.Timedelta(days=1)])
    assert not res.is_valid


def test_a_fresh_split_sized_move_stays_an_error_until_a_listing_could_exist():
    h, _i = _ftnt_like(days_ago=1)
    assert not DV.validate_history(h, "X", listed_split_dates=[]).is_valid


def test_an_exact_two_for_one_is_never_cleared_by_an_empty_list():
    h = _bars()
    h.iloc[150:, h.columns.get_loc("Close")] /= 2.0
    for c in ("Open", "High", "Low"):
        h.iloc[150:, h.columns.get_loc(c)] /= 2.0
    h.iloc[150:, h.columns.get_loc("Volume")] *= 2.0
    assert not DV.validate_history(h, "X", listed_split_dates=[]).is_valid


# ── the newest bar ───────────────────────────────────────────────────────────

def test_an_inconsistent_newest_bar_is_dropped_and_named():
    h = _bars()
    h.iloc[-1, h.columns.get_loc("Close")] = float(h["High"].iloc[-1]) * 1.002
    out, what = DV.drop_inconsistent_newest_bar(h)
    assert len(out) == len(h) - 1 and what.startswith(h.index[-1].date().isoformat())
    assert DV.validate_history(out, "X").is_valid


def test_a_clean_newest_bar_and_an_older_bad_bar_are_left_alone():
    h = _bars()
    assert DV.drop_inconsistent_newest_bar(h) == (h, None)
    h.iloc[-5, h.columns.get_loc("Close")] = float(h["High"].iloc[-5]) * 1.01
    out, what = DV.drop_inconsistent_newest_bar(h)
    assert what is None and len(out) == len(h)
    assert not DV.validate_history(out, "X").is_valid, "an older bad bar still fails"


# ── news ─────────────────────────────────────────────────────────────────────

class _RaisingNews:
    @property
    def news(self):
        raise RuntimeError("*** YAHOO! FINANCE IS CURRENTLY DOWN! ***")


def _system():
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj._filter_stats, obj._filter_stats_lock = {}, threading.Lock()
    return obj


def test_a_raising_news_endpoint_falls_back_to_the_feed(monkeypatch):
    feed = [{"title": "Acme beats", "publisher": "Yahoo Finance", "published": "", "url": "", "summary": ""}]
    monkeypatch.setattr(sp, "_rss_news_items", lambda t: feed)
    obj = _system()
    assert obj._company_news("ACME", _RaisingNews()) == feed


def test_after_repeated_failures_the_endpoint_is_skipped(monkeypatch):
    calls = {"n": 0}

    class _Counting:
        @property
        def news(self):
            calls["n"] += 1
            raise RuntimeError("down")
    monkeypatch.setattr(sp, "_rss_news_items", lambda t: [])
    obj = _system()
    for k in range(sp.QuantaEliteTradingSystem.NEWS_PRIMARY_MAX_FAILURES + 4):
        obj._company_news(f"T{k}", _Counting())
    assert calls["n"] == sp.QuantaEliteTradingSystem.NEWS_PRIMARY_MAX_FAILURES


def test_both_feeds_down_is_a_warning_not_a_rejection(monkeypatch):
    def boom(_t):
        raise OSError("feed down")
    monkeypatch.setattr(sp, "_rss_news_items", boom)
    obj = _system()
    assert obj._company_news("ACME", _RaisingNews()) == []
    assert obj._filter_stats.get("news_feeds_unavailable") == 1
    assert "news_feeds_unavailable" in sp.SCAN_WARNING_REASONS


def test_research_sentiment_reads_the_feed_when_yfinance_news_fails(monkeypatch):
    import TradingAnalysis_ELITE as elite
    from engine import news_feed
    monkeypatch.setattr(elite.yf, "Ticker", lambda t: _RaisingNews())
    monkeypatch.setattr(news_feed, "rss_news_items", lambda t, **k: [
        {"title": "Acme raises guidance", "publisher": "Reuters", "published": "2026-11-09T12:00:00+00:00",
         "url": "https://x", "summary": ""}])
    analyzer = elite.SentimentAnalyzer.__new__(elite.SentimentAnalyzer)
    out = analyzer._fetch_yahoo("ACME", set())
    assert [a["title"] for a in out] == ["Acme raises guidance"] and out[0]["source"] == "Reuters"


def test_research_sentiment_still_reports_failure_when_both_are_down(monkeypatch):
    import TradingAnalysis_ELITE as elite
    from engine import news_feed
    monkeypatch.setattr(elite.yf, "Ticker", lambda t: _RaisingNews())

    def boom(t, **k):
        raise OSError("down")
    monkeypatch.setattr(news_feed, "rss_news_items", boom)
    analyzer = elite.SentimentAnalyzer.__new__(elite.SentimentAnalyzer)
    with pytest.raises(RuntimeError, match="Yahoo news unavailable"):
        analyzer._fetch_yahoo("ACME", set())


def test_the_feed_parser_reads_dated_linked_items():
    from engine.news_feed import rss_news_items
    xml = b"""<rss><channel>
      <item><title>Acme beats</title><link>https://finance.yahoo.com/a</link>
        <pubDate>Mon, 09 Nov 2026 18:57:11 +0000</pubDate><description>Reuters -- Acme beat</description></item>
      <item><title></title></item></channel></rss>"""
    resp = types.SimpleNamespace(content=xml, raise_for_status=lambda: None)
    out = rss_news_items("ACME", get=lambda url: resp)
    assert len(out) == 1
    assert out[0]["publisher"] == "Reuters" and out[0]["published"].startswith("2026-11-09T18:57:11")


# ── near misses ──────────────────────────────────────────────────────────────

def test_near_misses_rank_by_score_and_say_what_clears_reward_risk():
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj._diagnostics_lock, obj._candidate_rejections, obj.scan_results = threading.Lock(), {}, []
    obj._record_candidate_rejection("EOG", "risk_reward_low", 8.11, "After-cost R:R 1.46:1 below 1.80:1",
                                    entry=120.0, stop=112.0, target=132.0, rr=1.46, cost_frac=0.0015)
    obj._record_candidate_rejection("SN", "signal_not_actionable", 7.11, "HOLD: RSI 74.6 exceeds the entry limit 70")
    obj._record_candidate_rejection("LOW", "score_below_min", 2.78, "Adjusted score 2.78 below the entry floor 3.50")
    obj._record_candidate_rejection("SBUX", "signal_not_actionable", 5.43, "HOLD: score 5.43 below 5.50")
    out = obj.near_misses()
    assert [n["ticker"] for n in out] == ["EOG", "SN"], "below the Accumulate floor is not a near miss"
    eog = out[0]
    assert eog["needed"] == T.RR.SCANNER_MIN
    from engine.levels import net_rr
    assert net_rr(eog["entry_needed"], 112.0, 132.0, 0.0015) == pytest.approx(T.RR.SCANNER_MIN, abs=0.01)
    assert net_rr(120.0, 112.0, eog["target_needed"], 0.0015) == pytest.approx(T.RR.SCANNER_MIN, abs=0.01)
    assert len(obj.near_misses(limit=1)) == 1


def test_the_scan_result_publishes_near_misses_and_the_bar_lag(monkeypatch):
    import app as webapp
    assert webapp._bar_lag_sessions(None) is None
    from engine import market_calendar as mc
    last = mc.last_completed_session()
    assert webapp._bar_lag_sessions(last.isoformat()) == 0
    prev = last - pd.offsets.BDay(3)
    assert webapp._bar_lag_sessions(pd.Timestamp(prev).date().isoformat()) >= 1
