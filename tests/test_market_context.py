"""Overview market context and the engine-aware regime history (October 2026)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()
import app as webapp_app  # noqa: E402


def test_cross_asset_reads_use_both_legs_and_name_the_unit():
    q = {"^TNX": {"price": 5.24, "change_pct": 0.25}, "^IRX": {"price": 4.06, "change_pct": 0.0},
         "HYG": {"price": 80.0, "change_pct": 0.5}, "LQD": {"price": 106.0, "change_pct": 0.1},
         "HG=F": {"price": 4.6, "change_pct": None}, "GC=F": {"price": 2900.0, "change_pct": 0.3}}
    reads = {r["key"]: r for r in webapp_app._cross_asset_reads(q, "US")}
    assert reads["curve"]["unit"] == "bp" and reads["curve"]["value"] == 118.0
    assert reads["credit"]["read"] == "risk appetite"
    # Copper's change is unknown: the ratio is shown, its change and read are not invented.
    assert reads["growth"]["change"] is None and reads["growth"]["read"] is None
    assert webapp_app._cross_asset_reads(q, "IN") == []


def test_calendar_keeps_only_the_coming_week(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    today = datetime.now().date()
    cal = {"events": [
        {"kind": "cpi", "title": "CPI", "date": (today + timedelta(days=3)).isoformat(), "time": "08:30"},
        {"kind": "fomc", "title": "FOMC", "date": (today + timedelta(days=30)).isoformat(), "time": "14:00"},
        {"kind": "jobs", "title": "Jobs", "date": (today - timedelta(days=2)).isoformat(), "time": "08:30"},
    ]}
    import portfolio.sources as src
    monkeypatch.setattr(src, "load_official_calendar", lambda *a, **k: cal)
    out = webapp_app._calendar_events("US")
    assert [e["title"] for e in out] == ["CPI"]
    assert webapp_app._calendar_events("IN") == []


def test_regime_history_records_the_engine_read_and_reports_its_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp_app, "_REGIME_HISTORY_PATH", tmp_path / "rh.json")
    import json
    # Sessions, not calendar days: Thursday's row, then Friday's read.
    (tmp_path / "rh.json").write_text(json.dumps({"US": [{
        "date": "2026-10-15", "tone": "Neutral", "net": 0, "vix": 15.0, "participation": 50, "ten_year": 5.2, "factors": {},
        "posture": "Lean Long", "regime": "Mildly Constructive", "direction": 58, "confidence": 30,
        "engine_factors": {"news": {"label": "News Impact", "score": 0}}}]}), encoding="utf-8")
    engine = {"posture": "Neutral", "marketRegimeBase": "Mixed Market", "confidenceScore": 18,
              "factors": [{"key": "trend", "label": "Trend", "score": 1}, {"key": "news", "label": "News Impact", "score": -2}]}
    out = webapp_app._regime_change_block("US", "Neutral", [], 15.0, 50, 5.2, engine=engine,
                                          session_date="2026-10-16")
    texts = [c["text"] for c in out["regime_changes"]]
    assert "Posture moved Lean Long \u2192 Neutral" in texts
    assert "Direction 58 \u2192 38 of 100" in texts  # two factors summing to -1 of a possible 4
    assert "News Impact +0 \u2192 -2" in texts
    assert [h["posture"] for h in out["regime_history"]] == ["Lean Long", "Neutral"]


def test_curve_reads_not_inverted_and_each_gauge_says_what_it_means():
    q = {"^TNX": {"price": 5.24, "change_pct": 0.0}, "^IRX": {"price": 4.06, "change_pct": 0.0}}
    curve = webapp_app._cross_asset_reads(q, "US")[0]
    assert curve["read"] == "not inverted" and curve["note"]


def test_index_participation_names_each_fund():
    out = webapp_app._index_participation(
        [{"symbol": "SPY", "above_sma50": True}, {"symbol": "IWM", "above_sma50": False}], ["SPY", "QQQ", "IWM"])
    assert out["members"] == [{"symbol": "SPY", "label": "S&P 500", "above": True},
                              {"symbol": "IWM", "label": "Russell 2000", "above": False}]
    assert out["missing"] == ["QQQ"]


def test_earnings_calendar_completes_the_screen_list(monkeypatch):
    """Yahoo's calendar adds the reports the screen did not cover; OTC lines are
    dropped, dates are New York dates, and the screen's own row wins."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    import pandas as pd
    import yfinance as yf

    ny = ZoneInfo("America/New_York")
    today = datetime.now(ny).date()
    at = lambda d, hh: pd.Timestamp(datetime.combine(today + timedelta(days=d), datetime.min.time()), tz="UTC") + pd.Timedelta(hours=hh)
    df = pd.DataFrame(
        {"Company": ["JPMorgan Chase", "Taiwan Semi OTC", "Bank", "Far"],
         "Marketcap": [800e9, 900e9, 20e9, 50e9],
         "Event Start Date": [at(2, 11), at(2, 11), at(1, 2), at(30, 11)],
         "Timing": ["BMO", "BMO", "AMC", "AMC"],
         "EPS Estimate": [4.92, 3.1, float("nan"), 1.0]},
        index=pd.Index(["JPM", "TSMWF", "MTB", "FAR"], name="Symbol"))

    class FakeCalendars:
        def __init__(self, start=None, end=None):
            pass

        def get_earnings_calendar(self, **kw):
            return df

    monkeypatch.setattr(yf, "Calendars", FakeCalendars)
    monkeypatch.setenv("QUANTA_EARNINGS_CALENDAR", "1")
    webapp_app._earnings_cal_cache.clear()
    rows = webapp_app._earnings_calendar("US")
    by = {r["symbol"]: r for r in rows}
    assert set(by) == {"JPM", "MTB"}, "OTC line and out-of-window rows dropped"
    assert by["JPM"]["earnings_timing"] == "bmo" and by["JPM"]["eps_estimate"] == 4.92
    # 02:00 UTC is the evening before in New York.
    assert by["MTB"]["earnings_in_days"] == 0 and by["MTB"]["eps_estimate"] is None
    merged = webapp_app._merge_earnings_calendar([{"symbol": "JPM", "earnings_in_days": 2, "from": "screen"}], "US")
    assert [r["symbol"] for r in merged] == ["MTB", "JPM"]
    assert merged[1].get("from") == "screen", "the screen's row is kept"
    assert webapp_app._earnings_calendar("IN") == []
    monkeypatch.setenv("QUANTA_EARNINGS_CALENDAR", "0")
    webapp_app._earnings_cal_cache.clear()
    assert webapp_app._earnings_calendar("US") == []


def test_one_row_daily_series_takes_its_history_from_hourly_bars():
    """Yahoo serves most NSE sector indices with one daily row: the quote's
    previous close and the 50-day read come from hourly bars instead."""
    import pandas as pd

    days = pd.bdate_range("2026-06-01", "2026-10-09", tz="Asia/Kolkata")
    hourly_ix = [d + pd.Timedelta(hours=h) for d in days for h in (10, 15)]
    hourly = pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(len(hourly_ix))]}, index=pd.DatetimeIndex(hourly_ix))
    daily = pd.DataFrame({"Close": [hourly["Close"].iloc[-1]]}, index=pd.DatetimeIndex([days[-1]]))

    class FakeTicker:
        history_metadata = {}
        fast_info = {}

        def history(self, period=None, interval=None, **kw):
            return hourly if interval == "60m" else daily

    closes = webapp_app._daily_closes_from_hourly(FakeTicker())
    assert len(closes) == len(days) and closes[-1][1] == hourly["Close"].iloc[-1]
    import yfinance as yf
    orig = yf.Ticker
    yf.Ticker = lambda sym: FakeTicker()
    try:
        q = webapp_app._fetch_tape_quote("^CNXAUTO", "Auto")
        t = webapp_app._trend_metrics("^CNXAUTO")
    finally:
        yf.Ticker = orig
    assert q["price"] is not None and q["change_pct"] is not None and q["change_pct"] > 0
    assert t is not None and t["above_sma50"] is True


def test_every_earnings_count_uses_the_week_calendars_window():
    """The risk card, the calendar, the Catalysts tab and the Catalyst Risk
    factor all count today through the calendar's last day: yesterday's
    report and the day after the calendar are out."""
    days = int(webapp_app._T.MARKET.CALENDAR_DAYS_AHEAD)
    rows = [{"symbol": s, "earnings_in_days": d} for s, d in (("Y", -1), ("T", 0), ("L", days - 1), ("X", days))]
    assert [r["symbol"] for r in webapp_app._earnings_ahead(rows)] == ["T", "L"]
    assert webapp_app._earnings_ahead(None) == []


def test_screen_rows_take_timing_and_estimate_from_the_calendar(monkeypatch):
    """The scan's own row wins, but it carries no timing or estimate: the same
    report on Yahoo's calendar fills those, and only those."""
    cal = [{"symbol": "JPM", "earnings_date": "2026-10-13", "earnings_in_days": 3, "earnings_timing": "bmo",
            "eps_estimate": 5.94, "market_cap": 8e11, "name": "JPMorgan Chase & Co."}]
    monkeypatch.setattr(webapp_app, "_earnings_calendar", lambda key: [dict(r) for r in cal])
    screen = [{"symbol": "JPM", "earnings_date": "2026-10-13T12:30:00", "earnings_date_pretty": "2026-10-13",
               "earnings_in_days": 3, "name": "JPMorgan", "earnings_timing": None}]
    out = webapp_app._merge_earnings_calendar(screen, "US")
    assert len(out) == 1
    assert out[0]["earnings_timing"] == "bmo" and out[0]["eps_estimate"] == 5.94
    assert out[0]["name"] == "JPMorgan", "the screen's own fields are kept"
    moved = [{**screen[0], "earnings_date_pretty": "2026-10-20", "earnings_date": "2026-10-20"}]
    assert webapp_app._merge_earnings_calendar(moved, "US")[0].get("eps_estimate") is None, "a different date is a different report"
