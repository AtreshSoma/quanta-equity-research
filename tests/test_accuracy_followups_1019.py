"""Accuracy follow-ups (2026-10-19).

1. The scanner's FOMC size multipliers and their day windows live in
   engine/thresholds.py (SIZING.FOMC_*), with no behaviour change.
2. Price freshness for an India listing (.NS / .BO) is measured on the India
   session clock (IST close RESEARCH.INDIA_SESSION_CLOSE_IST, weekdays, the
   NSE holiday list), never on the New York calendar. US is unchanged.
3. The Overview regime engine reads every band from MARKET (no behaviour change).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

from engine import market_calendar as mc  # noqa: E402
from engine import thresholds as T  # noqa: E402
from engine.decision import assess_price_freshness  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
WARN = chr(0x26A0) + chr(0xFE0F)   # the warning sign the scanner message already starts with


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST).astimezone(timezone.utc)


# ── 1. FOMC size multipliers ─────────────────────────────────────────────────

def _event_risk(monkeypatch, today: date):
    import SP_ELITE_PRO as sp
    ny = datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(mc, "now_new_york", lambda now=None: ny)
    return sp.QuantaEliteTradingSystem._get_event_risk_factor(None)


def test_fomc_values_are_the_former_literals():
    assert T.SIZING.FOMC_NEAR_DAYS == 3
    assert T.SIZING.FOMC_NEAR_SIZE_MULT == 0.70
    assert T.SIZING.FOMC_WEEK_DAYS == 7
    assert T.SIZING.FOMC_WEEK_SIZE_MULT == 0.85


def test_fomc_multiplier_and_message_unchanged(monkeypatch):
    # 2026-10-28 is an FOMC decision day.
    assert _event_risk(monkeypatch, date(2026, 10, 28)) == (0.70, WARN + " FOMC in 0 days - positions reduced 30%")
    assert _event_risk(monkeypatch, date(2026, 10, 25)) == (0.70, WARN + " FOMC in 3 days - positions reduced 30%")
    assert _event_risk(monkeypatch, date(2026, 10, 24)) == (0.85, WARN + " FOMC in 4 days - positions reduced 15%")
    assert _event_risk(monkeypatch, date(2026, 10, 21)) == (0.85, WARN + " FOMC in 7 days - positions reduced 15%")
    assert _event_risk(monkeypatch, date(2026, 10, 20)) == (1.0, "")


def test_fomc_multiplier_follows_thresholds(monkeypatch):
    monkeypatch.setattr(T.SIZING, "FOMC_NEAR_SIZE_MULT", 0.5)
    monkeypatch.setattr(T.SIZING, "FOMC_WEEK_DAYS", 8)
    assert _event_risk(monkeypatch, date(2026, 10, 26)) == (0.5, WARN + " FOMC in 2 days - positions reduced 50%")
    mult, _ = _event_risk(monkeypatch, date(2026, 10, 20))
    assert mult == T.SIZING.FOMC_WEEK_SIZE_MULT


def test_fomc_literals_are_gone_from_the_scanner():
    src = (REPO / "core" / "SP_ELITE_PRO.py").read_text(encoding="utf-8")
    assert "return 0.70, f" not in src and "return 0.85, f" not in src
    assert "0 <= days_until <= 3" not in src and "4 <= days_until <= 7" not in src


# ── 2. India price freshness ─────────────────────────────────────────────────

def test_listing_market_by_suffix():
    assert mc.listing_market("RELIANCE.NS") == "IN"
    assert mc.listing_market("infy.bo") == "IN"
    assert mc.listing_market("AAPL") == "US"
    assert mc.listing_market("") == "US"
    assert mc.listing_market(None) == "US"


def test_india_last_completed_session_uses_the_ist_close():
    # Wed 2026-07-08: before 15:30 IST the last completed session is Tuesday.
    assert mc.last_completed_india_session(_ist(2026, 7, 8, 15, 29)) == date(2026, 7, 7)
    assert mc.last_completed_india_session(_ist(2026, 7, 8, 15, 30)) == date(2026, 7, 8)
    # Weekend: Friday.
    assert mc.last_completed_india_session(_ist(2026, 7, 11, 10, 0)) == date(2026, 7, 10)
    assert mc.last_completed_india_session(_ist(2026, 7, 13, 9, 0)) == date(2026, 7, 10)


def test_india_close_is_read_from_thresholds(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "INDIA_SESSION_CLOSE_IST", (16, 0))
    assert mc.last_completed_india_session(_ist(2026, 7, 8, 15, 45)) == date(2026, 7, 7)


def test_nse_holidays_are_weekday_closures():
    assert mc.NSE_HOLIDAYS, "the NSE holiday list is empty"
    for day in mc.NSE_HOLIDAYS:
        assert day.weekday() < 5, day
        assert day.year in (2026, 2027), day
        assert not mc.is_india_trading_day(day)
    assert date(2026, 1, 26) in mc.NSE_HOLIDAYS      # Republic Day
    assert date(2026, 4, 3) in mc.NSE_HOLIDAYS       # Good Friday
    assert mc.is_india_trading_day(date(2026, 7, 8))
    assert not mc.is_india_trading_day(date(2026, 7, 11))


def test_india_bar_from_the_last_ist_session_is_fresh_after_close():
    now = _ist(2026, 7, 8, 16, 0)            # Wed after 15:30 IST (06:30 in New York)
    f = assess_price_freshness(date(2026, 7, 8), now=now, ticker="RELIANCE.NS")
    assert (f.status, f.lag_sessions) == ("fresh", 0)
    f = assess_price_freshness(date(2026, 7, 7), now=now, ticker="TCS.BO")
    assert (f.status, f.lag_sessions) == ("fresh", 1)


def test_india_bar_two_sessions_old_is_stale():
    now = _ist(2026, 7, 8, 16, 0)
    f = assess_price_freshness(date(2026, 7, 6), now=now, ticker="RELIANCE.NS")
    assert (f.status, f.lag_sessions) == ("stale", 2)
    assert "expected 2026-07-08" in f.notes[0]
    # The New York calendar would have called it fresh (its last session is Tue).
    assert assess_price_freshness(date(2026, 7, 6), now=now).status == "fresh"
    # A market argument works the same as the suffix.
    assert assess_price_freshness(date(2026, 7, 6), now=now, market="IN").status == "stale"


def test_india_holiday_is_not_a_session():
    # Mon 2026-01-26 (Republic Day) is closed: Friday's bar on Tuesday after
    # the close is one session old, not two.
    f = assess_price_freshness(date(2026, 1, 23), now=_ist(2026, 1, 27, 16, 0), ticker="SBIN.NS")
    assert (f.status, f.lag_sessions) == ("fresh", 1)


@pytest.mark.parametrize("bar, now", [
    (date(2026, 7, 6), datetime(2026, 7, 8, 10, 30, tzinfo=timezone.utc)),
    (date(2026, 7, 2), datetime(2026, 7, 8, 21, 0, tzinfo=timezone.utc)),
    (date(2026, 7, 2), datetime(2026, 7, 6, 21, 0, tzinfo=timezone.utc)),   # around 3 July (observed holiday)
    (date(2026, 10, 16), datetime(2026, 10, 19, 20, 5, tzinfo=timezone.utc)),
])
def test_us_ticker_is_unchanged(bar, now):
    base = assess_price_freshness(bar, now=now)
    assert assess_price_freshness(bar, now=now, ticker="AAPL") == base
    assert assess_price_freshness(bar, now=now, market="US") == base
    assert assess_price_freshness(None, now=now, ticker="AAPL") == assess_price_freshness(None, now=now)


def test_scanner_passes_the_ticker_to_freshness():
    src = (REPO / "core" / "SP_ELITE_PRO.py").read_text(encoding="utf-8")
    assert "freshness=assess_price_freshness(as_of, ticker=rec.ticker)" in src


# ── 3. Overview regime engine numbers ────────────────────────────────────────

_MARKET_VALUES = {
    "TREND_FALLBACK_UP_PCT": 0.75, "TREND_FALLBACK_DOWN_PCT": -1.0, "TREND_FALLBACK_WEAK_PCT": -0.25,
    "SESSION_MOVE_UP_PCT": 0.7, "SESSION_MOVE_DOWN_PCT": -1.2, "SESSION_MOVE_WEAK_PCT": -0.35,
    "MOMENTUM_UP_PCT": 3.0, "MOMENTUM_DOWN_PCT": -6.0, "MOMENTUM_WEAK_PCT": -2.0,
    "CATALYST_HEAVY_REPORTS": 4,
    "POSTURE_OFFENSIVE_SUM": 7, "POSTURE_SELECTIVE_SUM": 4, "POSTURE_LEAN_SUM": 2,
    "RULE2_QQQ_DROP_PCT": -1.0, "OFFENSIVE_MIN_CONFIRMED": 5,
    "CONFLICT_REASON_MIN": 2, "CONFLICT_HIGH": 3,
    "LEGACY_TONE_TREND_PCT": 0.5, "LEGACY_TONE_NET_SUM": 2,
    "NEWS_HALF_LIFE_DAYS": 4.0, "NEWS_DRIVER_MEDIUM": 4,
}


def test_regime_values_are_the_former_literals():
    for name, value in _MARKET_VALUES.items():
        assert getattr(T.MARKET, name) == value, name
        assert type(getattr(T.MARKET, name)) is type(value), name


def _engine(*, bench_chg=0.0, mom=None, catalysts=(), qqq=0.0, vix_chg=0.0, news=None):
    import app as webapp
    trend = {}
    if mom is not None:
        trend["SPY"] = {"above_sma200": True, "sma50_gt_sma200": True, "above_sma50": True,
                        "last": 100.0, "mom_21d_pct": mom}
    quotes = [{"symbol": s, "price": 100.0, "change_pct": c}
              for s, c in (("SPY", bench_chg), ("QQQ", qqq), ("DIA", 0.2), ("IWM", 0.2))]
    out = webapp._build_market_regime_engine(
        "US", "SPY", {"label": "S&P 500", "change_pct": bench_chg},
        {"price": 15.0, "change_pct": vix_chg}, {"price": 4.0, "change_pct": 0.0},
        quotes, trend, ["SPY", "QQQ", "DIA", "IWM"], None,
        news=news, catalysts=list(catalysts))
    return out, {f["key"]: f["score"] for f in out["factors"]}


@pytest.mark.parametrize("chg, trend, session", [
    (0.75, 1, 1), (0.7, 0, 1), (0.69, 0, 0), (-0.25, 0, 0), (-0.26, -1, 0),
    (-0.35, -1, -1), (-1.0, -2, -1), (-1.2, -2, -2),
])
def test_trend_fallback_and_session_move_bands_unchanged(chg, trend, session):
    _, f = _engine(bench_chg=chg)
    assert (f["trend"], f["index_performance"]) == (trend, session)


def test_trend_and_session_bands_follow_thresholds(monkeypatch):
    monkeypatch.setattr(T.MARKET, "TREND_FALLBACK_UP_PCT", 0.5)
    monkeypatch.setattr(T.MARKET, "SESSION_MOVE_UP_PCT", 0.5)
    _, f = _engine(bench_chg=0.6)
    assert (f["trend"], f["index_performance"]) == (1, 1)


@pytest.mark.parametrize("mom, score", [(3.0, 1), (2.9, 0), (-2.0, -1), (-5.9, -1), (-6.0, -2)])
def test_momentum_bands_unchanged(mom, score):
    assert _engine(mom=mom)[1]["momentum"] == score


def test_momentum_follows_thresholds(monkeypatch):
    monkeypatch.setattr(T.MARKET, "MOMENTUM_UP_PCT", 2.0)
    assert _engine(mom=2.5)[1]["momentum"] == 1


def test_heavy_earnings_week_follows_thresholds(monkeypatch):
    assert _engine(catalysts=[{}] * 4)[1]["catalyst_risk"] == -1
    assert _engine(catalysts=[{}] * 3)[1]["catalyst_risk"] == 0
    monkeypatch.setattr(T.MARKET, "CATALYST_HEAVY_REPORTS", 3)
    assert _engine(catalysts=[{}] * 3)[1]["catalyst_risk"] == -1


def test_rule2_follows_thresholds(monkeypatch):
    out, _ = _engine(qqq=-1.5, vix_chg=2.0)
    assert any(r.startswith("QQQ -1.50%") for r in out["overridesTriggered"])
    monkeypatch.setattr(T.MARKET, "RULE2_QQQ_DROP_PCT", -2.0)
    out, _ = _engine(qqq=-1.5, vix_chg=2.0)
    assert not any(r.startswith("QQQ") for r in out["overridesTriggered"])


def test_posture_bands_and_rule4_follow_thresholds(monkeypatch):
    def run():
        return _engine(bench_chg=0.8, mom=4.0)[0]
    out = run()
    assert (out["regimeScore"], out["posture"], out["overridesTriggered"]) == (6, "Selective Offensive", [])
    monkeypatch.setattr(T.MARKET, "POSTURE_SELECTIVE_SUM", 7)
    assert run()["posture"] == "Lean Long"
    monkeypatch.setattr(T.MARKET, "POSTURE_OFFENSIVE_SUM", 6)
    assert run()["posture"] == "Offensive"
    monkeypatch.setattr(T.MARKET, "OFFENSIVE_MIN_CONFIRMED", 7)
    out = run()
    assert out["posture"] == "Selective Offensive"
    assert out["overridesTriggered"] == ["Only 6 confirmed factors; full offensive requires >=7"]


def test_news_decay_and_medium_follow_thresholds(monkeypatch):
    import app as webapp
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    news = {"articles": [{"title": "Fed shock", "impact": 8, "direction": "bearish",
                          "published": old, "category": "macro"}]}
    s = webapp._summarize_news_for_engine(news)
    assert s["highImpactCount"] == 0                    # 8 halved after 4 days is under 6
    assert s["catalysts"][0]["impactLevel"] == "High"
    monkeypatch.setattr(T.MARKET, "NEWS_HALF_LIFE_DAYS", 400.0)
    assert webapp._summarize_news_for_engine(news)["highImpactCount"] == 1
    news["articles"][0]["impact"] = 5
    assert webapp._summarize_news_for_engine(news)["catalysts"][0]["impactLevel"] == "Medium"
    monkeypatch.setattr(T.MARKET, "NEWS_DRIVER_MEDIUM", 6)
    assert webapp._summarize_news_for_engine(news)["catalysts"][0]["impactLevel"] == "Low"


def test_legacy_tone_follows_thresholds(monkeypatch):
    import app as webapp

    def snap():
        def fake(symbol, label):
            chg = {"SPY": 0.55, "^VIX": 0.0}.get(symbol, 0.4)
            return {"symbol": symbol, "label": label, "price": 15.0 if symbol == "^VIX" else 100.0,
                    "change_pct": chg}
        with mock.patch.object(webapp, "_quote_snapshot", side_effect=fake), \
             mock.patch.object(webapp, "_trend_metrics", return_value=None):
            with webapp.app.test_request_context("/"):
                return webapp._market_snapshot("US")

    s = snap()
    trend = next(f for f in s["tone_factors"] if f["key"] == "trend")
    assert trend["score"] == 1
    net = sum(f["score"] for f in s["tone_factors"])
    assert s["tone"] == ("Risk-on" if net >= 2 else "Neutral")
    monkeypatch.setattr(T.MARKET, "LEGACY_TONE_TREND_PCT", 0.6)
    monkeypatch.setattr(T.MARKET, "LEGACY_TONE_NET_SUM", 99)
    s = snap()
    assert next(f for f in s["tone_factors"] if f["key"] == "trend")["score"] == 0
    assert s["tone"] == "Neutral"


_REGIME_LITERALS = [
    r"bench_chg >= 0\.75", r"bench_chg <= -1\.0\b", r"bench_chg < -0\.25",
    r"bench_chg >= 0\.7\b", r"bench_chg <= -1\.2", r"bench_chg <= -0\.35",
    r"mom >= 3\.0", r"mom <= -6\.0", r"mom <= -2\.0",
    r"catalyst_count >= 4", r"score_sum [<>]= -?[742]\b", r"qqq < -1\.0",
    r"positives < 5\b", r"conflicts >= [23]\b", r"bench_chg [<>]= -?0\.5\b",
    r"net [<>]= -?2\b", r"age / 4\.0", r"imp >= 4\b",
]


@pytest.mark.parametrize("pattern", _REGIME_LITERALS)
def test_no_regime_literal_remains_in_app(pattern):
    src = (REPO / "webapp" / "app.py").read_text(encoding="utf-8")
    hits = [f"app.py:{i}: {line.strip()}" for i, line in enumerate(src.splitlines(), 1)
            if re.search(pattern, line)]
    assert not hits, "\n".join(hits)


def test_research_and_shortterm_pass_the_ticker_to_the_freshness_check():
    """Every Decision builder hands the listing to assess_price_freshness, so an
    NSE/BSE name is judged on the India session calendar."""
    from pathlib import Path
    core = Path(__file__).resolve().parents[1] / "core"
    for name in ("TradingAnalysis_ELITE.py", "SP_ShortTerm.py", "SP_ELITE_PRO.py"):
        src = (core / name).read_text(encoding="utf-8")
        calls = [ln for ln in src.splitlines() if "assess_price_freshness(" in ln and "import" not in ln
                 and "def " not in ln]
        assert calls, name
        for ln in calls:
            assert "ticker=" in ln, f"{name}: {ln.strip()}"
