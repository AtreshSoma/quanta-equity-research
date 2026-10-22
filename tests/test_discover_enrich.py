"""Discover reads: the session tape, why a name moves, the odds of a Street
target, analyst revisions, past earnings reactions, and the Street list's
selection (one issuer, one buy-rating cut, universe-relative upside).

Run: python -m pytest tests/test_discover_enrich.py -q
"""
import importlib.util
import math
import os
import sys
from datetime import date, datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "webapp"))
sys.path.insert(0, os.path.join(ROOT, "core"))

import discover_enrich as D  # noqa: E402
from engine import thresholds as T  # noqa: E402


@pytest.fixture(scope="module")
def app_mod():
    os.environ.setdefault("QUANTA_NO_BROWSER", "1")
    spec = importlib.util.spec_from_file_location("quanta_app_discover", os.path.join(ROOT, "webapp", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def row(**kw):
    base = {"open": 100.0, "day_high": 101.0, "day_low": 99.0, "prev_close": 100.0, "price": 100.5}
    base.update(kw)
    return base


# ---- tape -----------------------------------------------------------------
def test_tape_gap_held_faded_extended_and_reversal():
    held = D.tape_read(row(open=110, day_high=112, day_low=109, price=109.5))   # keeps 95% of a +10% gap
    assert held["tag"] == "gap_held" and held["gap_pct"] == 10.0
    faded = D.tape_read(row(open=110, day_high=110.5, day_low=102, price=103))   # keeps 30%
    assert faded["tag"] == "gap_faded"
    extended = D.tape_read(row(open=104, day_high=110, day_low=103.5, price=109))  # gap 4%, now +9%
    assert extended["tag"] == "gap_extended"
    rev = D.tape_read(row(open=104, day_high=104.5, day_low=97, price=98))  # gapped up, now below the prior close
    assert rev["tag"] == "reversal" and rev["held_share"] < 0


def test_tape_trend_and_two_way_without_a_gap():
    up = D.tape_read(row(open=100.2, day_high=104, day_low=100, price=103.9))
    assert up["tag"] == "trend_up" and up["clv"] >= T.MARKET.TREND_DAY_CLV
    two = D.tape_read(row(open=100.2, day_high=104, day_low=98, price=101))
    assert two["tag"] == "two_way"


def test_tape_is_honest_about_missing_inputs_and_reads_relative_volume():
    t = D.tape_read({"price": 10, "volume": 3_000_000, "avg_volume": 1_000_000})
    assert t["tag"] is None and t["gap_pct"] is None
    assert t["rvol"] == 3.0


# ---- why it moves -----------------------------------------------------------
def test_deal_pattern_beats_everything_and_needs_a_tight_range():
    r = row(open=45.3, day_high=45.59, day_low=45.26, prev_close=30.04, price=45.49, change_pct=51.4, earnings_today=True)
    assert D.move_reason(r)["key"] == "deal"
    wide = row(open=40, day_high=46, day_low=39, prev_close=30, price=45, change_pct=50)
    assert D.move_reason(wide) is None, "a wide range is a momentum day, not a pinned price"


def test_reason_waits_for_the_read_then_names_no_clear_cause():
    r = row(change_pct=-9.0)
    assert D.move_reason(r) is None
    assert D.move_reason(r, {"analyst": None, "fund_change_pct": -1.0, "fund": "XLK"})["key"] == "none"
    assert D.move_reason(r, {"analyst": None, "fund_change_pct": -6.0, "fund": "XLK"})["key"] == "sector"
    act = {"label": "Downgrade", "detail": "X downgraded it."}
    assert D.move_reason(r, {"analyst": act, "fund_change_pct": -6.0, "fund": "XLK"})["label"] == "Downgrade"
    assert D.move_reason({**r, "earnings_today": True}, {"analyst": act})["key"] == "earnings"


def test_earnings_in_session_reads_before_open_and_after_prior_close():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    now = datetime(2026, 10, 20, 13, 0, tzinfo=ny)                       # Tuesday
    ts = lambda *a: datetime(*a, tzinfo=ny).timestamp()
    assert D.earnings_in_session(ts(2026, 10, 20, 7, 0), "US", now)        # before today's open
    assert D.earnings_in_session(ts(2026, 10, 19, 16, 5), "US", now)       # after Monday's close
    assert not D.earnings_in_session(ts(2026, 10, 20, 16, 5), "US", now)   # tonight: tomorrow's move
    assert not D.earnings_in_session(ts(2026, 10, 19, 7, 0), "US", now)    # Monday morning: Monday's move
    monday = datetime(2026, 10, 19, 11, 0, tzinfo=ny)
    assert D.earnings_in_session(ts(2026, 10, 16, 16, 30), "US", monday)   # Friday after close moves Monday


# ---- odds, revisions, earnings history -----------------------------------
def test_target_odds_is_n_d2():
    assert D.target_odds(100, 100, 0.3, 0.0, 1.0) == pytest.approx(0.5 * (1 + math.erf(-0.15 / math.sqrt(2))), abs=1e-4)
    far = D.target_odds(100, 146, 0.4, 0.04, 1.0)
    near = D.target_odds(100, 110, 0.4, 0.04, 1.0)
    assert 0 < far < near < 0.5
    assert D.target_odds(None, 110, 0.3) is None and D.target_odds(100, 110, 0) is None


def test_revisions_count_each_firms_latest_action_inside_the_window():
    now = datetime(2026, 10, 20)
    rows = [
        {"when": now - timedelta(days=5), "Firm": "A", "Action": "main", "currentPriceTarget": 90, "priorPriceTarget": 100},
        {"when": now - timedelta(days=40), "Firm": "A", "Action": "main", "currentPriceTarget": 100, "priorPriceTarget": 80},
        {"when": now - timedelta(days=10), "Firm": "B", "Action": "up", "currentPriceTarget": 120, "priorPriceTarget": 100},
        {"when": now - timedelta(days=200), "Firm": "C", "Action": "down", "currentPriceTarget": 50, "priorPriceTarget": 90},
    ]
    s = D.revisions_summary(rows, now, 90)
    assert (s["raises"], s["cuts"], s["upgrades"], s["downgrades"], s["firms"]) == (1, 1, 1, 0, 2)


def test_earnings_moves_use_the_session_the_report_landed_in():
    closes = [(date(2026, 7, 27), 100.0), (date(2026, 7, 28), 102.0), (date(2026, 7, 29), 90.0),
              (date(2026, 4, 21), 50.0), (date(2026, 4, 22), 55.0)]
    closes.sort()
    reports = [(datetime(2026, 7, 28, 16, 5), True), (datetime(2026, 4, 22, 7, 0), True), (datetime(2026, 10, 27, 16, 5), False)]
    moves = D.earnings_moves(reports, closes)
    assert moves[0] == {"date": "2026-07-28", "move_pct": round((90 / 102 - 1) * 100, 2), "timing": "amc"}
    assert moves[1]["move_pct"] == 10.0 and moves[1]["timing"] == "bmo"
    assert len(moves) == 2, "an unreported date is skipped"


def test_implied_move_and_strike_iv():
    calls = [{"strike": 95, "bid": 7, "ask": 7.4, "impliedVolatility": 0.4}, {"strike": 100, "bid": 4, "ask": 4.2, "impliedVolatility": 0.38},
             {"strike": 130, "bid": 0.5, "ask": 0.6, "impliedVolatility": 0.45}]
    puts = [{"strike": 100, "bid": 3.8, "ask": 4.0, "impliedVolatility": 0.39}]
    assert D.implied_move(calls, puts, 100) == 8.0
    assert D.strike_iv(calls, puts, 128, 100) == 0.45
    bad = [{"strike": 130, "bid": 0, "ask": 0, "impliedVolatility": 0.00001}, {"strike": 100, "bid": 4, "ask": 4.2, "impliedVolatility": 0.36}]
    assert D.strike_iv(bad, puts, 130, 100) == 0.36, "a junk quote falls back to the money"
    # CDNS, Oct 2026: the strike nearest the target had no market and a placeholder
    # IV inside the sane band (0.0625), which priced the odds at 0%.
    dead = [{"strike": 400, "bid": 27, "ask": 31.5, "impliedVolatility": 0.4989}, {"strike": 410, "bid": 0, "ask": 0, "impliedVolatility": 0.0625},
            {"strike": 420, "bid": 22.5, "ask": 27, "impliedVolatility": 0.4958}]
    assert D.strike_iv(dead, puts, 406.24, 312.37) == 0.4989, "a strike with no bid and ask is skipped, not read"
    assert D.strike_iv([{"strike": 130, "lastPrice": 2, "impliedVolatility": 0.4}], [], 130, 100) is None


def test_pick_expiry():
    ex = ["2026-10-23", "2026-10-30", "2027-01-15", "2027-09-17", "2027-12-17"]
    assert D.pick_expiry(ex, date(2026, 10, 28)) == "2026-10-30"
    assert D.pick_expiry(ex, date(2026, 10, 20), 365) == "2027-09-17"


# ---- the Street list ----------------------------------------------------------
def pick(sym, name, up, **kw):
    r = {"symbol": sym, "name": name, "price": 100.0, "upside_pct": up, "target_mean": 100 + up,
         "analyst_count": 20, "recommendation": "buy", "target_high": 150.0, "target_low": 90.0,
         "week52_low": 80.0, "week52_high": 180.0}
    r.update(kw)
    return r


def test_one_issuer_takes_one_slot_and_the_list_size_is_config(app_mod):
    rows = [pick("GOOGL", "Alphabet Inc.", 30), pick("GOOG", "Alphabet Inc. Class C", 29)] + \
           [pick(f"N{i}", f"Name {i} Corp", 20 - i) for i in range(12)]
    out = app_mod._street_candidates(rows)
    syms = [r["symbol"] for r in out]
    assert "GOOGL" in syms and "GOOG" not in syms
    assert len(out) == T.MARKET.ANALYST_PICK_COUNT
    assert out[0]["target_dispersion_pct"] == round(60 / 130 * 100, 1)
    assert out[0]["range_pos"] == 0.2


def test_buy_rating_cut_comes_from_config(app_mod):
    cut = T.MARKET.ANALYST_BUY_MEAN_MAX
    assert app_mod._buy_rated({"recommendation": cut})
    assert not app_mod._buy_rated({"recommendation": cut + 0.1})
    assert app_mod._buy_rated({"recommendation": "strong_buy"}) and not app_mod._buy_rated({"recommendation": "hold"})


def test_consensus_stats_median_over_covered_names(app_mod):
    rows = [pick("A", "A", 10, sector="Technology"), pick("B", "B", 20, sector="Technology"),
            pick("C", "C", 40, sector="Energy"), pick("D", "D", 90, analyst_count=3)]
    s = app_mod._consensus_stats(rows)
    assert s["median_upside_pct"] == 20 and s["n"] == 3
    assert s["sectors"] == {"Technology": 2, "Energy": 1}


def test_universe_lists_each_issuer_once(app_mod):
    assert "GOOGL" in app_mod._US_BROAD_UNIVERSE and "GOOG" not in app_mod._US_BROAD_UNIVERSE


def test_extremes_past_the_board_are_counted(app_mod):
    rows = [{"change_pct": 30 - i} for i in range(app_mod.MOVERS_DISPLAY_COUNT + 3)]
    hidden = app_mod._extremes_past(rows)
    assert hidden == sum(1 for r in rows[app_mod.MOVERS_DISPLAY_COUNT:] if abs(r["change_pct"]) >= app_mod.EXTREME_MOVE_PCT)


# ---- batched quotes ----------------------------------------------------------
def test_batch_quotes_cover_every_name_in_chunks_and_skip_a_failed_chunk(app_mod):
    calls = []

    def fetch(chunk):
        calls.append(len(chunk))
        if chunk[0] == "S100":
            raise RuntimeError("provider hiccup")
        return [{"symbol": s, "regularMarketPrice": 10.0, "regularMarketChangePercent": 1.0, "regularMarketOpen": 9.9,
                 "regularMarketDayHigh": 10.2, "regularMarketDayLow": 9.8, "regularMarketPreviousClose": 9.9,
                 "averageDailyVolume3Month": 1000, "regularMarketVolume": 2500} for s in chunk]

    syms = [f"S{i}" for i in range(130)]
    out = app_mod._batch_quotes(syms, fetch=fetch)
    assert calls == [app_mod.BATCH_QUOTE_CHUNK, 30]
    assert len(out) == app_mod.BATCH_QUOTE_CHUNK, "the failed chunk's names fall back to their own reads"
    assert out["S0"]["source"] == "yf_quote_batch" and out["S0"]["day_high"] == 10.2


def test_quote_figures_win_and_upside_is_repriced(app_mod):
    quote = {"symbol": "X", "price": 50.0, "market_cap": 9e9, "name": "X"}
    row = app_mod._with_fundamentals(quote, {"target_mean": 60.0, "market_cap": 1.0, "name": "X Holdings", "sector": "Energy"})
    assert row["market_cap"] == 9e9 and row["name"] == "X Holdings" and row["sector"] == "Energy"
    assert row["upside_pct"] == 20.0
