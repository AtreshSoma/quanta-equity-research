"""Overview review fixes (2026-10-18).

* The chart's close time and session window come from Yahoo epoch seconds; a
  bare number given to pd.Timestamp is read as nanoseconds, which printed
  "At close: Dec 31, 7:00 PM EST" and squeezed the 1D line into the right edge.
No test here reaches the network.
"""
from __future__ import annotations

import sys
import types

import pandas as pd

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402

# Fri 2026-10-16, US/Eastern: pre 04:00, regular 09:30-16:00, post to 20:00.
PRE, OPEN, CLOSE, POST_END = 1792137600, 1792157400, 1792180800, 1792195200


def test_epoch_seconds_become_milliseconds():
    assert webapp._chart_epoch_ms(CLOSE) == CLOSE * 1000
    assert webapp._chart_epoch_ms(float(CLOSE)) == CLOSE * 1000
    assert webapp._chart_epoch_ms(pd.Timestamp(CLOSE, unit="s", tz="UTC")) == CLOSE * 1000
    for missing in (None, 0, -5, float("nan"), True):
        assert webapp._chart_epoch_ms(missing) is None


def _fake_yfinance(monkeypatch, frame, meta):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.history_metadata = meta

        def history(self, **_kw):
            return frame

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    monkeypatch.setattr(webapp, "_orig_history", None, raising=False)


def test_one_day_payload_keeps_the_real_session_window(monkeypatch):
    idx = pd.date_range(pd.Timestamp(PRE, unit="s", tz="UTC"), periods=4, freq="5h")
    frame = pd.DataFrame({"Close": [773.4, 770.0, 763.25, 761.26], "Volume": [1, 2, 3, 4]}, index=idx)
    meta = {
        "regularMarketTime": CLOSE, "regularMarketPrice": 763.25, "chartPreviousClose": 771.79,
        "currentTradingPeriod": {
            "pre": {"start": PRE, "end": OPEN},
            "regular": {"start": OPEN, "end": CLOSE},
            "post": {"start": CLOSE, "end": POST_END},
        },
    }
    _fake_yfinance(monkeypatch, frame, meta)
    out = webapp._chart_payload("SPY", "1d")
    assert out["regular_time"] == CLOSE * 1000
    assert out["session"]["regular"] == [OPEN * 1000, CLOSE * 1000]
    assert out["session"]["pre"][0] == PRE * 1000
    # The last bar (23:00 UTC, 7 PM ET) sits after the close, so it is after-hours.
    assert out["extended"]["session"] == "post"
    assert out["market_open"] is False


# ---- Regime history is keyed by trading session, not the wall clock --------

def _snap(direction):
    return {"tone": "Neutral", "net": -2, "vix": 20.0, "participation": 0, "ten_year": 5.1,
            "factors": {"trend": 1}, "posture": "Defensive", "regime": "Defensive Market",
            "direction": direction, "confidence": 59, "engine_factors": {}}


def test_weekend_loads_update_fridays_row(monkeypatch, tmp_path):
    path = tmp_path / "hist.json"
    path.write_text('{"US": [{"date": "2026-10-15", "posture": "Defensive", "direction": 40, "factors": {"trend": 1}},'
                    ' {"date": "2026-10-17", "posture": "Defensive", "direction": 32, "factors": {"trend": 1}}]}',
                    encoding="utf-8")
    monkeypatch.setattr(webapp, "_REGIME_HISTORY_PATH", path)
    # Saturday and Sunday loads both read Friday's bars.
    webapp._record_and_diff_regime("US", _snap(33), session_date="2026-10-16")
    out = webapp._record_and_diff_regime("US", _snap(33), session_date="2026-10-16")
    dates = [r["date"] for r in out["history"]]
    assert dates == ["2026-10-15", "2026-10-16"], dates  # the stale Saturday row is gone
    # The change is measured against Thursday, the prior session, not Friday itself.
    assert out["baseline"] is False
    assert any(c.get("kind") == "direction" for c in out["changes"])


# ---- A reader's money column is not a macro catalyst ------------------------

def test_personal_finance_columns_are_not_market_news():
    columns = [
        "‘Some people did everything right’: I lost my home in the Great Recession. I rent in my 50s. Why is that judged as failure?",
        "My wife says we can’t afford to retire. I vetoed her $8,000 trip to Italy. We’re in our 60s and",
        "My parents left me a house and $400,000. Should I pay off my mortgage?",
    ]
    for t in columns:
        assert webapp._is_personal_finance_column(t), t
    news = [
        "Economists: Hot PPI shows Fed's work is not done",
        "Trump waives new Russia sanctions, Zelenskyy condemns it as 'wrong'",
        "‘I’m worried about the consumer,’ says Dimon as JPMorgan beats",
        "The 10-Year Treasury Is Above 5% for the First Time in Almost 20 Years",
        "Black Monday Fears Grip Wall Street as Traders Lose $52 Million on AI Stock Bets",
    ]
    for t in news:
        assert not webapp._is_personal_finance_column(t), t


# ---- The headline agrees with the call it explains --------------------------

FACTORS = [
    {"key": "trend", "label": "Trend", "score": 1},
    {"key": "volatility", "label": "Volatility", "score": 0},
    {"key": "participation", "label": "Participation", "score": -2},
    {"key": "sector_leadership", "label": "Sector Leadership", "score": -2},
    {"key": "news", "label": "News Impact", "score": -1},
]


def test_defensive_headline_names_its_real_factors_and_a_bearish_driver():
    drivers = [
        {"title": "A Record Number of S&P 500 Companies Issued Upbeat Guidance", "direction": "bullish"},
        {"title": "Economists: Hot PPI shows Fed's work is not done", "direction": "bearish"},
    ]
    h = webapp._regime_headline("Defensive Market", "bear", "Defensive", drivers[0]["title"], False,
                                factors=FACTORS, drivers=drivers)
    assert "Upbeat Guidance" not in h
    assert "Key driver: Economists: Hot PPI shows Fed's work is not done." in h
    assert "participation and sector leadership" in h
    assert "Volatility" not in h  # the volatility factor is neutral here


def test_no_matching_driver_means_no_key_driver():
    h = webapp._regime_headline("Defensive Market", "bear", "Defensive", None, False,
                                factors=FACTORS, drivers=[{"title": "Stocks rally", "direction": "bullish"}])
    assert "Key driver" not in h


def test_high_impact_keys_identify_the_counted_headlines():
    news = {"groups": {"big_news": [
        {"title": "Fed hikes", "url": "https://x.test/a", "impact": 9, "direction": "bearish"},
        {"title": "Quiet story", "url": "https://x.test/b", "impact": 2, "direction": "neutral"},
    ]}}
    out = webapp._summarize_news_for_engine(news)
    assert out["highImpactCount"] == 1
    assert out["highImpactKeys"] == ["https://x.test/a"]


def test_session_phrase_says_today_only_for_todays_bar():
    assert webapp._session_phrase("2026-10-16", today="2026-10-16") == "today"
    assert webapp._session_phrase("2026-10-16", today="2026-10-18") == "on Fri Oct 16"
    assert webapp._session_phrase(None) == "in the last session"


# ---- A low-confidence cap only lowers the exposure band ---------------------

def test_cap_never_lifts_a_defensive_band():
    assert webapp._cap_exposure_band("20-50% gross", "30-55% gross") == "20-50% gross"
    assert webapp._cap_exposure_band("50-70% gross", "30-55% gross") == "30-55% gross"
    assert webapp._cap_exposure_band("65-95% gross", "20-45% gross") == "20-45% gross"
    assert webapp._cap_exposure_band("45-65% gross", "30-55% gross") == "30-55% gross"
    assert webapp._cap_exposure_band(None, "30-55% gross") == "30-55% gross"


# ---- Headline sentiment: the headline decides; words match whole ------------

def test_the_headline_decides_its_direction():
    blurb = "Amid fear of rate hikes and oil prices, investors can track Street analysts' picks."
    assert webapp._news_direction("Top Wall Street analysts are bullish on these 3 stocks", blurb) == "bullish"
    assert webapp._news_direction("Stocks head for a showdown", blurb) == "bearish"  # neutral head, blurb decides
    assert webapp._news_direction("Software stocks steady", "") == "neutral"  # "war" is not inside "software"


# ---- Base rate and pulse series ------------------------------------------------

def _fake_closes(monkeypatch, closes):
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    frame = pd.DataFrame({"Close": closes}, index=idx)

    class T:
        def __init__(self, s):
            pass

        def history(self, **_kw):
            return frame

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=T))


def test_base_rate_counts_next_sessions_after_a_similar_day(monkeypatch):
    # Alternating -2% then +1% days, ending on a -2% day: every -2% day was followed by a rise.
    closes, p = [100.0], 100.0
    for i in range(401):
        p *= 0.98 if i % 2 == 0 else 1.01
        closes.append(p)
    _fake_closes(monkeypatch, closes)
    webapp._base_rate_cache.clear()
    out = webapp._base_rate("SPY")
    assert out["condition"] == "down"
    assert out["up_pct"] == 100.0 and out["n"] == 200
    assert 40 < out["all_up_pct"] < 60


def test_pulse_series_gives_a_month_of_closes_and_the_week_change(monkeypatch):
    _fake_closes(monkeypatch, [100.0 + i for i in range(40)])
    out = webapp._pulse_series(["SPY", "BTC-USD"])
    assert len(out["SPY"]["closes"]) == 22
    assert out["SPY"]["week_change_pct"] == round((139 / 134 - 1) * 100, 2)  # five sessions back
    assert out["BTC-USD"]["week_change_pct"] == round((139 / 132 - 1) * 100, 2)  # seven days back


def test_reversed_fear_and_past_selloff_do_not_read_bearish():
    d = webapp._news_direction
    assert d("Black Monday Fears Fade as Nasdaq Futures Rally After Friday Selloff") == "bullish"
    assert d("U.S. stock futures climb as investors return to AI trade after selloff") == "bullish"
    assert d("Dow Jones Futures Rebound After AI Sell-Off; Meta, SpaceX, Tesla Earnings Key") == "bullish"
    assert d("Black Monday Fears Grip Wall Street as Traders Lose $52 Million on AI Stock Bets") == "bearish"
    assert d("Stocks tumble as yields surge") == "bearish"


def test_reader_advice_headlines_are_not_market_news():
    f = webapp._is_personal_finance_column
    assert f("Should You Invest in the S&P 500 Right Now or Wait? There's 1 Clear Answer")
    assert f("3 Stocks to Buy and Hold Forever")
    assert f('If I Could Own Only 1 "Magnificent Seven" Stock for the Next 10 Years, It Would Be This One.')
    assert not f("Only one Fed official dissented")
    assert not f("Fed signals patience on rates")
    assert not f("These surprising stocks could be the biggest drivers of S&P 500 earnings growth")


def test_market_context_serves_last_good_read_while_refreshing(monkeypatch):
    calls = {"n": 0}

    def levels(*_a, **_k):
        calls["n"] += 1
        return {"symbol": "SPY", "sma50": 767.4} if calls["n"] == 1 else None

    monkeypatch.setattr(webapp, "_key_levels", levels)
    monkeypatch.setattr(webapp, "_quote_snapshot", lambda s, l: {"price": 1.0, "change_pct": 0.1})
    monkeypatch.setattr(webapp, "_trend_metrics", lambda s: {"above_sma50": True})
    monkeypatch.setattr(webapp, "_pulse_series", lambda syms: {"SPY": {"closes": [1, 2]}})
    monkeypatch.setattr(webapp, "_base_rate", lambda b: {"up_pct": 54.0, "n": 10})
    monkeypatch.setattr(webapp, "_calendar_events", lambda m: [])
    webapp._context_cache.clear()
    webapp._context_good.clear()
    webapp._aux_jobs.clear()
    first = webapp._market_context("US", force=True)
    assert first["levels"]["sma50"] == 767.4 and "levels" not in first["missing"]
    second = webapp._market_context("US", force=True)
    assert second["levels"]["sma50"] == 767.4, "the last good levels are served"
    assert "levels" in second["stale"] and "levels" not in second["missing"]


def test_stock_opinion_questions_and_listicles_are_not_news():
    f = webapp._is_personal_finance_column
    assert f("3 Insurance Stocks That Fall Short")
    assert f("Is Broadcom Stock Cheap Enough Yet?")
    assert f("Tesla Stock vs. the S&P 500: Which Investment Wins Through 2030")
    assert not f("Is the Fed done raising rates?")
    assert not f("US defence stocks tumble as investors bet on Democratic election sweep")


def test_rebound_wording_reads_bullish():
    d = webapp._news_direction
    assert d("Micron and other chip stocks spring back to life after bruising selloff") == "bullish"
    assert d("Update: Tech Stock Rebound Nudges US Equity Futures Mostly Higher Pre-Bell") == "bullish"



def test_exposure_bands_come_from_config():
    from core.engine import thresholds as T
    bands = T.MARKET.EXPOSURE_BANDS
    assert bands["defensive"] == (20, 50) and bands["offensive"] == (65, 95)
    assert webapp._band_text("lean_long") == "50-70% gross"
    assert webapp._band_text("cap_low_confidence") == "30-55% gross"


def test_picks_scan_deploys_no_more_than_the_overview_band(monkeypatch):
    """One exposure model: a scan started while the Overview reads 30-55% gross
    deploys at most 55% of capital, and says where the cap came from."""
    import types as _t
    key = "payload_US_v2"
    monkeypatch.setitem(webapp._dashboard_cache, key, {"market": {"market_regime_engine": {
        "posture": "Neutral", "portfolioGuidance": {"band": "30-55% gross"}}}})
    quanta = _t.SimpleNamespace(market_regime=_t.SimpleNamespace(recommended_exposure=0.85))
    info = webapp._apply_overview_exposure_cap(quanta, "US")
    assert quanta.market_regime.recommended_exposure == 0.55
    assert info == {"source": "overview", "band": "30-55% gross", "posture": "Neutral"}
    # A lower scanner figure is kept: the band is a ceiling, never a floor.
    quanta.market_regime.recommended_exposure = 0.40
    webapp._apply_overview_exposure_cap(quanta, "US")
    assert quanta.market_regime.recommended_exposure == 0.40
    # No Overview read yet: the scanner's own figure stands and says so.
    monkeypatch.setitem(webapp._dashboard_cache, key, {})
    quanta.market_regime.recommended_exposure = 0.85
    assert webapp._apply_overview_exposure_cap(quanta, "US") == {"source": "scanner"}
    assert quanta.market_regime.recommended_exposure == 0.85
