"""Overview market-regime accuracy fixes (2026-10-19).

* The Rates / Credit factor read the percent change of the yield LEVEL, so a
  3 bp move on a 4.10% yield (+0.7%) scored as tightening and flagged a "rate
  shock". A yield now moves in basis points; USD/INR (India's proxy) keeps a
  percent move with its own, currency-sized bands.
* The volatility, participation and macro cut-offs come from engine/thresholds.py.
* News is a keyword lexicon: it contributes its factor score but can never cap
  the posture on its own.
No test here reaches the network.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402
from engine import thresholds as T  # noqa: E402


def _pct_for_bp(level: float, bp: float) -> float:
    """The quote's change_pct for a yield that moved `bp` basis points to `level`."""
    prev = level - bp / 100.0
    return (level - prev) / prev * 100.0


def _engine(market="US", *, vix=15.0, vix_chg=0.0, yield_price=4.13, yield_chg=0.0,
            part=50.0, bench_chg=0.3, trend=None, quotes=None, news=None,
            catalysts=(), source_status=None):
    bench_sym = "SPY" if market == "US" else "^NSEI"
    breadth = ["SPY", "QQQ", "DIA", "IWM"] if market == "US" else ["^NSEI", "^BSESN", "^NSEBANK", "^CNXIT"]
    if quotes is None:
        quotes = [{"symbol": s, "price": 100.0, "change_pct": bench_chg} for s in breadth]
    trend = trend if trend is not None else {
        "above_sma200": True, "sma50_gt_sma200": False, "above_sma50": True,
        "last": 100.0, "mom_21d_pct": 0.5}
    return webapp._build_market_regime_engine(
        market_key=market, bench_symbol=bench_sym,
        bench={"label": bench_sym, "change_pct": bench_chg},
        vix={"price": vix, "change_pct": vix_chg},
        yield_proxy={"label": "10Y Yield" if market == "US" else "USD/INR",
                     "price": yield_price, "change_pct": yield_chg},
        quotes=quotes, trend_data={bench_sym: trend}, breadth_basket=breadth,
        participation_pct=part, news=news,
        catalysts=list(catalysts) if catalysts is not None else None,
        source_status=source_status if source_status is not None else [{"name": "X", "ok": True}])


def _factor(engine, key):
    return next(f for f in engine["factors"] if f["key"] == key)


# ---- 1. Rates factor in basis points ---------------------------------------

def test_a_three_bp_yield_move_is_stable_not_a_rate_shock():
    # 4.10 -> 4.13 is +0.73% of the level, which the old percent bands read as
    # "tightening" and as a macro "rate shock".
    eng = _engine(yield_price=4.13, yield_chg=(4.13 - 4.10) / 4.10 * 100)
    rates = _factor(eng, "rates_credit")
    assert rates["score"] == 0
    assert "stable" in rates["note"] and "+3.0 bp" in rates["note"]
    assert "rate shock" not in _factor(eng, "macro_risk")["note"]


def test_yield_bands_are_in_basis_points_from_thresholds():
    M = T.MARKET
    lvl = 4.20
    sharp = _factor(_engine(yield_price=lvl, yield_chg=_pct_for_bp(lvl, M.YIELD_SHARP_BP + 0.5)), "rates_credit")
    rising = _factor(_engine(yield_price=lvl, yield_chg=_pct_for_bp(lvl, M.YIELD_RISING_BP + 0.5)), "rates_credit")
    easing = _factor(_engine(yield_price=lvl, yield_chg=_pct_for_bp(lvl, M.YIELD_EASING_BP - 0.5)), "rates_credit")
    assert (sharp["score"], rising["score"], easing["score"]) == (-2, -1, 1)
    shock = _engine(yield_price=lvl, yield_chg=_pct_for_bp(lvl, -(M.YIELD_SHARP_BP + 0.5)))
    assert "rate shock" in _factor(shock, "macro_risk")["note"], "a sharp move either way is a shock"


def test_yield_bands_follow_a_config_change(monkeypatch):
    monkeypatch.setattr(T.MARKET, "YIELD_RISING_BP", 2.0)
    eng = _engine(yield_price=4.13, yield_chg=(4.13 - 4.10) / 4.10 * 100)
    assert _factor(eng, "rates_credit")["score"] == -1


def test_usd_inr_keeps_a_percent_move_with_its_own_bands():
    M = T.MARKET
    sharp = _factor(_engine("IN", vix=14.0, yield_price=84.0, yield_chg=M.FX_SHARP_PCT + 0.05), "rates_credit")
    rising = _factor(_engine("IN", vix=14.0, yield_price=84.0, yield_chg=M.FX_MOVE_PCT + 0.05), "rates_credit")
    easing = _factor(_engine("IN", vix=14.0, yield_price=84.0, yield_chg=-(M.FX_MOVE_PCT + 0.05)), "rates_credit")
    stable = _factor(_engine("IN", vix=14.0, yield_price=84.0, yield_chg=0.1), "rates_credit")
    assert (sharp["score"], rising["score"], easing["score"], stable["score"]) == (-2, -1, 1, 0)
    assert "%" in rising["note"] and "bp" not in rising["note"]
    # A 0.7% rupee move was "tightening" (-1) under the yield's percent bands; it is a sharp move for a currency.
    assert M.FX_SHARP_PCT < 0.8


def test_legacy_tone_rates_factor_reads_basis_points():
    from unittest import mock

    def fake(symbol, label):
        if symbol == "^TNX":
            return {"symbol": symbol, "label": label, "price": 4.13, "change_pct": 0.73}
        if symbol == "^VIX":
            return {"symbol": symbol, "label": label, "price": 15.0, "change_pct": 0.0}
        return {"symbol": symbol, "label": label, "price": 100.0, "change_pct": 0.2}

    with mock.patch.object(webapp, "_quote_snapshot", side_effect=fake), \
         mock.patch.object(webapp, "_trend_metrics", return_value=None):
        with webapp.app.test_request_context("/"):
            snap = webapp._market_snapshot("US")
    credit = next(f for f in snap["tone_factors"] if f["key"] == "credit")
    assert credit["score"] == 0, credit


# ---- 2. Volatility / participation / macro cut-offs from thresholds --------

def test_volatility_bands_come_from_thresholds(monkeypatch):
    M = T.MARKET
    assert _factor(_engine(vix=M.VIX_CONTAINED_MAX - 0.5), "volatility")["score"] == 1
    assert _factor(_engine(vix=M.VIX_CALM_MAX + 0.5), "volatility")["score"] == -1
    assert _factor(_engine(vix=M.VIX_EXTREME + 0.5), "volatility")["score"] == -2
    monkeypatch.setattr(T.MARKET, "VIX_CALM_MAX", 18.0)
    assert _factor(_engine(vix=18.5), "volatility")["score"] == -1


def test_india_volatility_bands_come_from_thresholds(monkeypatch):
    M = T.MARKET
    assert _factor(_engine("IN", vix=M.INDIA_VIX_CALM_MAX - 0.5), "volatility")["score"] == 1
    assert _factor(_engine("IN", vix=M.INDIA_VIX_ELEVATED + 0.5), "volatility")["score"] == -1
    assert _factor(_engine("IN", vix=M.INDIA_VIX_EXTREME + 0.5), "volatility")["score"] == -2
    monkeypatch.setattr(T.MARKET, "INDIA_VIX_STRESS", 15.0)
    assert "elevated volatility" in _factor(_engine("IN", vix=16.0), "macro_risk")["note"]


def test_participation_bands_come_from_thresholds(monkeypatch):
    assert _factor(_engine(part=75.0), "participation")["score"] == 1
    assert _factor(_engine(part=50.0), "participation")["score"] == 0
    assert _factor(_engine(part=25.0), "participation")["score"] == -2
    monkeypatch.setattr(T.MARKET, "PARTICIPATION_BROAD_PCT", 80.0)
    assert _factor(_engine(part=75.0), "participation")["score"] == 0
    monkeypatch.setattr(T.MARKET, "PARTICIPATION_NEUTRAL_PCT", 60.0)
    assert _factor(_engine(part=50.0), "participation")["score"] == -1


def test_participation_wording_does_not_claim_breadth():
    note = _factor(_engine(part=75.0), "participation")["note"]
    assert "breadth" not in note.lower() and "indices" in note
    for tone in ("bull", "bear", "warn", "neutral"):
        assert "breadth" not in webapp._regime_headline("X", tone, "Neutral", None, False).lower()


def test_macro_oil_and_gold_flags_use_the_ribbon_thresholds(monkeypatch):
    M = T.MARKET
    quotes = [{"symbol": s, "price": 100.0, "change_pct": 0.3} for s in ("SPY", "QQQ", "DIA", "IWM")]
    oil = quotes + [{"symbol": "USO", "price": 70.0, "change_pct": M.OIL_MOVE_PCT + 0.05}]
    assert "oil spike" in _factor(_engine(quotes=oil), "macro_risk")["note"]
    gold = quotes + [{"symbol": "GLD", "price": 200.0, "change_pct": M.GOLD_MOVE_PCT + 0.05}]
    assert "gold" in _factor(_engine(quotes=gold), "macro_risk")["note"]
    monkeypatch.setattr(T.MARKET, "VIX_STRESS", 15.0)
    assert "elevated volatility" in _factor(_engine(vix=16.0), "macro_risk")["note"]


# ---- 3. News alone can never cap the posture --------------------------------

_BULL_TREND = {"above_sma200": True, "sma50_gt_sma200": True, "above_sma50": True,
               "last": 100.0, "mom_21d_pct": 4.0}
_BULL_QUOTES = [{"symbol": s, "price": 100.0, "change_pct": 0.9} for s in ("SPY", "QQQ", "DIA", "IWM")]


def _bearish_news(n):
    return {"groups": {"big_news": [
        {"title": f"Stocks plunge as recession fears loom {i}", "impact": 9,
         "direction": "bearish", "category": "macro"} for i in range(n)]}}


def test_bearish_headlines_alone_do_not_cap_a_bullish_posture():
    kw = dict(vix=13.0, yield_price=4.10, yield_chg=_pct_for_bp(4.10, -6.0), part=100.0,
              bench_chg=0.9, trend=_BULL_TREND, quotes=_BULL_QUOTES)
    base = _engine(**kw)
    hit = _engine(news=_bearish_news(3), **kw)
    assert base["posture"] in {"Offensive", "Selective Offensive"}
    news = _factor(hit, "news_impact")
    assert news["score"] == -int(T.MARKET.NEWS_FACTOR_MAX_ABS)
    # The posture moves only by the news factor's own score through the sum.
    assert hit["regimeScore"] == base["regimeScore"] + news["score"]
    assert not any("headline" in o.lower() and "override" in o.lower() for o in hit["overridesTriggered"])
    assert hit["posture"] == base["posture"] == "Offensive"
    assert hit["dataFreshness"]["state"] != "conflicted", "a news/price disagreement is not a factor conflict"
    assert hit["dataFreshness"]["newsConflict"], "the disagreement is still disclosed"
    # News lowers confidence, but on its own never into Low, which would cap the
    # exposure band and de-green the read.
    assert hit["confidenceScore"] < base["confidenceScore"]
    assert not hit["lowConfidence"] and hit["confidenceScore"] >= T.MARKET.CONFIDENCE_MODERATE
    assert hit["portfolioGuidance"]["band"] == base["portfolioGuidance"]["band"]


def test_news_factor_weight_comes_from_thresholds(monkeypatch):
    monkeypatch.setattr(T.MARKET, "NEWS_FACTOR_MAX_ABS", 2)
    assert _factor(_engine(news=_bearish_news(3)), "news_impact")["score"] == -2
    monkeypatch.setattr(T.MARKET, "NEWS_FACTOR_MAX_ABS", 0)
    assert _factor(_engine(news=_bearish_news(3)), "news_impact")["score"] == 0


def test_lexicon_reads_concessions_and_fizzling_rallies():
    d = webapp._news_direction
    assert d("Stocks climb despite tariff fears") == "bullish"
    assert d("Bitcoin rally fizzles") == "bearish"
    assert d("Stocks fall despite strong earnings") == "bearish"
    assert d("Despite tariff fears, stocks climb to a record high") == "bullish"
    assert d("Tech rally stalls as yields surge") == "bearish"


# ---- 4. Confidence constants come from thresholds ---------------------------

def test_confidence_scale_comes_from_thresholds(monkeypatch):
    monkeypatch.setattr(T.MARKET, "CONFIDENCE_MAX", 40)
    kw = dict(vix=13.0, yield_price=4.10, yield_chg=_pct_for_bp(4.10, -6.0), part=100.0,
              bench_chg=0.9, trend=_BULL_TREND, quotes=_BULL_QUOTES)
    assert _engine(**kw)["confidenceScore"] <= 40
    monkeypatch.undo()
    base = _engine(news=_bearish_news(1), **kw)["confidenceScore"]
    monkeypatch.setattr(T.MARKET, "CONFIDENCE_NEWS_BEARISH_PENALTY", 0)
    assert _engine(news=_bearish_news(1), **kw)["confidenceScore"] > base
