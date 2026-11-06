"""Research tab data-accuracy fixes (GOOGL audit, 2026-10-29).

Each block pins one verified defect: the reward/risk hurdle price shown as a
spot, flooring of displayed reward/risk, PEG from a one-off growth figure, the
breakout entry offered far above price, the stale earnings date, the neutral
fills counted in the composite, the live-bar read-out, relative-performance
windows, Plan Check and the Plan Simulator.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

import TradingAnalysis_ELITE as ta  # noqa: E402
from engine import bars as _bars  # noqa: E402
from engine import plan_check as pc  # noqa: E402
from engine import plan_simulator as ps  # noqa: E402
from engine import relative_performance as rp  # noqa: E402
from engine.costs import round_trip_cost_frac  # noqa: E402
from engine.levels import breakout_entry, entry_to_spot, net_rr, publish_levels, research_rr_clear_entry  # noqa: E402
from engine.thresholds import LEVELS, fmt_rr, floor_rr  # noqa: E402


# ---------------------------------------------------------------------------
# 2. Reward/risk is floored, never rounded up to a hurdle it does not meet
# ---------------------------------------------------------------------------

def test_floor_rr_never_rounds_a_failing_ratio_up_to_the_minimum():
    assert floor_rr(1.4988) == pytest.approx(1.49)
    assert fmt_rr(1.4988) == "1.49:1"
    assert fmt_rr(1.5) == "1.50:1"
    # Float noise (1.15 * 100 = 114.99999...) must not push an exact value down a cent.
    assert fmt_rr(1.15) == "1.15:1"
    assert fmt_rr(3.0) == "3.00:1"


def test_published_levels_store_the_floored_ratio_and_still_show_triggers():
    lv = publish_levels("research", entry=100.0, stop=95.0, target=107.49, rr=1.4988, atr=2.0,
                        cost_frac=0.0011, horizon_days=63, target_basis="test", min_rr=1.5)
    assert lv["risk_reward"] == pytest.approx(1.49)
    assert "triggers" in lv and "remedy" in lv
    assert "1.49" in lv["remedy"]


# ---------------------------------------------------------------------------
# 1. The R/R hurdle price is a SPOT, converted from the solver's entry
# ---------------------------------------------------------------------------

def test_clear_entry_converted_to_spot_reproduces_the_hurdle_at_the_entry_the_engine_uses():
    spot = 283.16
    entry = spot * 1.002                    # the engine's limit at a premium to spot
    stop = entry - 12.0
    d = entry - stop
    valuation = 330.0
    frac = 63 / 252
    cost = round_trip_cost_frac("GOOGL")

    p = research_rr_clear_entry(valuation, frac, d / entry, cost, 1.5)
    assert p is not None
    spot_p = entry_to_spot(p, entry, spot)
    # The spot quoted to a reader maps back to the solver's entry exactly.
    assert spot_p * entry / spot == pytest.approx(p, rel=1e-12)
    # A reader at that spot, using the engine's premium, meets the hurdle (to the cent).
    e_at = spot_p * 1.002
    rr = net_rr(e_at, e_at * (1 - d / entry), e_at + frac * (valuation - e_at), cost)
    assert rr == pytest.approx(1.5, abs=0.02)


def _hurdle_setup(entry, stop, rr, horizon=63, val_horizon=252):
    return SimpleNamespace(risk_reward_ratio=rr, entry_price=entry, stop_loss=stop,
                           horizon_days=horizon, valuation_horizon_days=val_horizon)


def test_rr_hurdles_publish_clear_entry_as_a_spot_below_the_entry_and_name_the_basis():
    spot = 283.16
    entry = round(spot * 1.002, 2)
    stop = entry - 14.0
    result = SimpleNamespace(ticker="GOOGL", current_price=spot)
    hurdles = ta._rr_hurdles(result, _hurdle_setup(entry, stop, rr=1.2), 330.0)
    row = hurdles["research"]
    assert row["passes"] is False
    assert row["clear_entry_basis"] == "spot"
    # Spot is entry / 1.002 (a little under the limit), so the printed price sits below the entry.
    assert row["clear_entry"] < entry
    p = research_rr_clear_entry(330.0, 63 / 252, (entry - stop) / entry, round_trip_cost_frac("GOOGL"), 1.5)
    assert row["clear_entry"] == math.floor(entry_to_spot(p, entry, spot) * 100) / 100
    # Flooring to the cent keeps a spot at the printed price on the passing side.
    assert math.isclose(row["clear_entry"], math.floor(row["clear_entry"] * 100) / 100)


def test_rr_hurdles_pass_carries_no_clear_entry():
    result = SimpleNamespace(ticker="GOOGL", current_price=100.0)
    hurdles = ta._rr_hurdles(result, _hurdle_setup(100.2, 90.0, rr=2.5), 150.0)
    assert hurdles["research"]["passes"] is True
    assert "clear_entry" not in hurdles["research"]


# ---------------------------------------------------------------------------
# 3. PEG: provider first, computed only from a sane forward growth, else None
# ---------------------------------------------------------------------------

def test_peg_prefers_the_provider_figure():
    assert ta._peg_ratio({"trailingPegRatio": 1.14, "pegRatio": 1.2}, 25.0) == (1.14, "provider")
    assert ta._peg_ratio({"pegRatio": 1.2}, 25.0) == (1.2, "provider")


def test_peg_is_not_computed_from_a_one_off_quarterly_growth_jump():
    # GOOGL-like: earningsGrowth 294% made P/E 14.7 into a 0.05 PEG. No forward EPS: no PEG.
    info = {"earningsGrowth": 2.94, "trailingEps": 6.0}
    assert ta._peg_ratio(info, 14.7) == (0.0, None)


def test_peg_computed_from_forward_growth_in_the_sane_band():
    value, source = ta._peg_ratio({"trailingEps": 5.0, "forwardEps": 5.5}, 30.0)
    assert source == "computed"
    assert value == pytest.approx(30.0 / 10.0)


def test_peg_refused_when_forward_growth_is_outside_the_band():
    assert ta._peg_ratio({"trailingEps": 0.5, "forwardEps": 5.0}, 30.0) == (0.0, None)
    assert ta._peg_ratio({"trailingEps": 5.0, "forwardEps": 4.0}, 30.0) == (0.0, None)


# ---------------------------------------------------------------------------
# 4. Breakout entry: only the nearest overhead level, within 3 ATRs
# ---------------------------------------------------------------------------

def test_breakout_entry_is_not_offered_far_above_price():
    # 52-week high 45% overhead, ATR 5: 9 ATRs away.
    assert breakout_entry(100.0, 144.8, 0.0, 5.0) == 0.0


def test_breakout_entry_offered_within_the_atr_band():
    assert LEVELS.BREAKOUT_MAX_ATR == 3.0
    assert breakout_entry(100.0, 108.0, 0.0, 4.0) == 108.0      # 2 ATRs
    assert breakout_entry(100.0, 130.0, 104.0, 2.0) == 104.0    # nearest overhead level wins


def test_breakout_entry_needs_a_known_atr_and_an_overhead_level():
    assert breakout_entry(100.0, 108.0, 0.0, 0.0) == 0.0
    assert breakout_entry(100.0, 95.0, 0.0, 2.0) == 0.0


# ---------------------------------------------------------------------------
# 5. Earnings: the next report date, the last one, sessions since
# ---------------------------------------------------------------------------

class _EarningsStock:
    """A yfinance-like ticker: a calendar dict and an earnings-dates table."""

    def __init__(self, ticker, calendar, table):
        self.ticker = ticker
        self.calendar = calendar
        self.info = {}
        self._table = table

    def get_earnings_dates(self, limit=12):
        if self._table is None:
            raise RuntimeError("no table")
        return self._table


def _table(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York") for d, _ in rows])
    return pd.DataFrame({"Reported EPS": [v for _, v in rows]}, index=idx)


@pytest.fixture(autouse=False)
def _clear_earnings_cache():
    ta._EARNINGS_TABLE_CACHE.clear()
    yield
    ta._EARNINGS_TABLE_CACHE.clear()


def test_earnings_schedule_gives_next_and_last_from_the_provider_table(_clear_earnings_cache):
    stock = _EarningsStock("TSTE1", {}, _table([
        (datetime(2026, 10, 28, 16), 2.8), (datetime(2027, 2, 3, 16), float("nan"))]))
    nxt, last = ta._earnings_schedule(stock, date(2026, 10, 29))
    assert nxt == date(2027, 2, 3)
    assert last == date(2026, 10, 28)


def test_earnings_schedule_is_cached_per_symbol(_clear_earnings_cache):
    calls = []

    class _Counting(_EarningsStock):
        def get_earnings_dates(self, limit=12):
            calls.append(1)
            return _table([(datetime(2027, 2, 3, 16), float("nan"))])

    stock = _Counting("TSTE2", {}, None)
    ta._earnings_schedule(stock, date(2026, 10, 29), "TSTE2")
    ta._earnings_schedule(stock, date(2026, 10, 30), "TSTE2")
    assert len(calls) == 1


def test_a_past_calendar_date_is_replaced_by_the_next_listed_date(_clear_earnings_cache):
    stock = _EarningsStock("TSTE3", {"Earnings Date": [datetime(2026, 10, 28)]}, _table([
        (datetime(2026, 10, 28, 16), 2.8), (datetime(2027, 2, 3, 16), float("nan"))]))
    res = ta.CatalystAnalyzer(None)._analyze_calendar(stock, ta.CatalystData(), date(2026, 10, 29), "TSTE3")
    assert res.earnings_date == "2027-02-03"
    assert res.days_to_earnings == (date(2027, 2, 3) - date(2026, 10, 29)).days
    assert res.last_earnings_date == "2026-10-28"
    assert res.sessions_since_last_earnings == 1
    assert res.is_past_earnings is True


def test_a_future_calendar_date_is_kept(_clear_earnings_cache):
    stock = _EarningsStock("TSTE4", {"Earnings Date": [datetime(2026, 11, 20)]}, None)
    res = ta.CatalystAnalyzer(None)._analyze_calendar(stock, ta.CatalystData(), date(2026, 10, 29))
    assert res.earnings_date == "2026-11-20"
    assert res.days_to_earnings == 22


def test_no_provider_table_and_a_past_date_leaves_the_date_unknown(_clear_earnings_cache):
    stock = _EarningsStock("TSTE5", {"Earnings Date": [datetime(2026, 10, 28)]}, None)
    res = ta.CatalystAnalyzer(None)._analyze_calendar(stock, ta.CatalystData(), date(2026, 10, 29))
    assert res.earnings_date == "Unknown"
    assert res.days_to_earnings == -1


# ---------------------------------------------------------------------------
# 6. Neutral fills are named; the composite is unchanged
# ---------------------------------------------------------------------------

def test_neutral_filled_names_valuation_and_sentiment_when_unmeasured():
    gen = ta.SignalGenerator()
    tech = ta.TechnicalIndicators()
    out = gen.generate(tech, ta.FundamentalData(), ta.RiskMetrics(), ta.SentimentData(), ta.InsiderData(),
                       ta.MarketIntelligence(), ta.ValuationMetrics(valuation_status="unavailable"),
                       current_price=100.0)
    breakdown = out[2]
    assert "valuation" in breakdown.neutral_filled
    assert "sentiment" in breakdown.neutral_filled
    assert breakdown.weights_used
    assert sum(breakdown.weights_used.values()) == pytest.approx(1.0, abs=1e-3)
    assert set(breakdown.weights_used) == set(breakdown.weights)


# ---------------------------------------------------------------------------
# 7. Live-bar read-out: informational, excluded from scoring, absent when settled
# ---------------------------------------------------------------------------

def _daily_bars(closes, end="2026-10-29"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": close.shift(1).fillna(close.iloc[0]), "High": close * 1.01,
                         "Low": close * 0.99, "Close": close,
                         "Volume": np.full(len(closes), 1_000_000.0)}, index=idx)


def _noisy(n, seed=7, sd=0.01):
    rng = np.random.default_rng(seed)
    return list(100.0 * np.exp(np.cumsum(rng.normal(0.0, sd, n))))


LIVE_NOON_NY = datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc)   # 12:00 New York, still trading
SETTLED_EVENING_NY = datetime(2026, 10, 29, 22, 0, tzinfo=timezone.utc)  # 18:00 New York, settled


def test_live_preview_reads_the_forming_bar_and_flags_a_large_move():
    closes = _noisy(120)
    closes[-1] = closes[-2] * 0.872           # a -12.8% live day
    data = _daily_bars(closes)
    frame, basis = _bars.scoring_bars(data, "GOOGL", LIVE_NOON_NY)
    assert basis["live_bar_excluded"] is True
    lp = _bars.live_preview(data, basis)
    assert lp["as_of"] == "2026-10-29"
    assert lp["day_change_pct"] == pytest.approx(-12.8, abs=0.05)
    assert lp["large_move"] is True
    assert lp["rsi"] is not None and lp["rsi"] < 30
    assert lp["daily_sigma_pct"] is not None and lp["daily_sigma_pct"] < 3
    assert "partial" in lp["note"]


def test_live_preview_is_empty_once_the_bar_has_settled():
    data = _daily_bars(_noisy(120))
    _, basis = _bars.scoring_bars(data, "GOOGL", SETTLED_EVENING_NY)
    assert basis["live_bar_excluded"] is False
    assert _bars.live_preview(data, basis) == {}


def test_technical_analysis_reports_the_live_preview_and_scores_are_unchanged():
    closes = _noisy(300)
    closes[-1] = closes[-2] * 0.872
    data = _daily_bars(closes)
    live = ta.TechnicalAnalyzer().analyze(data, now=LIVE_NOON_NY, ticker="GOOGL")
    settled_data = data.iloc[:-1]
    settled = ta.TechnicalAnalyzer().analyze(settled_data, now=SETTLED_EVENING_NY, ticker="GOOGL")
    assert live.live_preview and live.live_preview["large_move"] is True
    assert settled.live_preview == {}
    # The live bar never reaches a score: the readings equal those of the bars before it.
    assert live.rsi == pytest.approx(settled.rsi)
    assert live.macd_signal == settled.macd_signal


# ---------------------------------------------------------------------------
# 8. Relative performance windows carry their start and end dates
# ---------------------------------------------------------------------------

def test_relative_performance_rows_name_their_base_and_end_dates():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2025-06-02", periods=300)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))), index=idx)
    bench = pd.Series(rng.normal(0.0004, 0.01, 300), index=idx)
    out = rp.relative_performance(closes, bench, "SPY")
    six = next(r for r in out["rows"] if r["window"] == "6M")
    assert six["start_date"] == idx[-1 - 126].date().isoformat()
    assert six["end_date"] == idx[-1].date().isoformat()
    assert six["start_date"] < six["end_date"]


# ---------------------------------------------------------------------------
# 9. Plan Check: titles, summary, earnings, today's move, one cost model
# ---------------------------------------------------------------------------

_GOOGL_PLAN = dict(ticker="GOOGL", shares=10, entry=283.7, stop=270.0, target=310.0,
                   plan_date=date(2026, 10, 29), today=date(2026, 10, 29), account_value=100_000.0,
                   horizon_sessions=63)


def _check_of(res, cid):
    return next(c for c in res["checks"] if c["id"] == cid)


def test_every_check_has_a_human_title_and_no_raw_id_shows():
    res = pc.check_plan(**_GOOGL_PLAN, research=None, material_filings=None)
    for c in res["checks"]:
        assert c["title"] and c["title"] != c["id"]
    assert _check_of(res, "material_filings")["title"] == "Recent SEC filings"
    assert _check_of(res, "concentration_sector")["title"] == "Sector limit"


def test_summary_counts_and_headline_match_the_checks():
    res = pc.check_plan(**_GOOGL_PLAN, research={"action": "WATCHLIST", "new_money": "ELIGIBLE",
                                                 "blockers": [], "evidence": {"status": "VALIDATED"}},
                        next_earnings=date(2027, 2, 3), last_earnings=date(2026, 10, 28),
                        todays_move={"day_change_pct": -12.8, "large_move": True},
                        material_filings={"status": "ok", "events": []})
    counts = res["summary"]["counts"]
    assert sum(counts.values()) == len(res["checks"])
    headline = res["summary"]["headline"]
    assert "warning" in headline and "Earnings" in headline and "Today's move" in headline
    assert "Some checks could not run, so this is not a full pass" not in headline


def test_recent_release_warns_while_the_price_is_still_reacting():
    res = pc.check_plan(**_GOOGL_PLAN, research={"action": "WATCHLIST", "new_money": "ELIGIBLE",
                                                 "blockers": [], "evidence": {"status": "VALIDATED"}},
                        next_earnings=date(2027, 2, 3), last_earnings=date(2026, 10, 28),
                        todays_move={"day_change_pct": -12.8, "large_move": True},
                        material_filings={"status": "ok", "events": []})
    earn = _check_of(res, "earnings")
    assert earn["status"] == "WARN"
    assert "released on 2026-10-28" in earn["message"] and "-12.8%" in earn["message"]


def test_next_earnings_beyond_the_horizon_passes_and_says_how_far():
    res = pc.check_plan(**_GOOGL_PLAN, next_earnings=date(2027, 2, 3), last_earnings=date(2026, 8, 1))
    earn = _check_of(res, "earnings")
    assert earn["status"] == "PASS"
    assert "sessions away" in earn["message"]


def test_unknown_earnings_skips_only_when_nothing_is_known():
    res = pc.check_plan(**_GOOGL_PLAN)
    assert _check_of(res, "earnings")["status"] == "SKIPPED"


def test_todays_move_warns_on_a_large_live_move_and_skips_without_research():
    res = pc.check_plan(**_GOOGL_PLAN, research=None, todays_move={"day_change_pct": -12.8, "large_move": True})
    assert _check_of(res, "todays_move")["status"] == "SKIPPED"
    res = pc.check_plan(**_GOOGL_PLAN, research={"action": "WATCHLIST", "blockers": [],
                                                 "evidence": {"status": "VALIDATED"}},
                        todays_move={"day_change_pct": -12.8, "large_move": True})
    move = _check_of(res, "todays_move")
    assert move["status"] == "WARN"
    assert move["message"].startswith("Today's -12.8% move is not yet in QUANTA's signals")
    res = pc.check_plan(**_GOOGL_PLAN, research={"action": "WATCHLIST", "blockers": [],
                                                 "evidence": {"status": "VALIDATED"}},
                        todays_move={"day_change_pct": 0.4, "large_move": False})
    assert _check_of(res, "todays_move")["status"] == "PASS"


def test_rr_next_step_states_its_assumption():
    res = pc.check_plan(**dict(_GOOGL_PLAN, target=300.0))   # net R/R about 1.14: blocked
    rr = _check_of(res, "rr")
    assert rr["status"] in ("WARN", "BLOCK")
    assert "(keeping your stop and target)" in rr["next_step"]


def test_plan_check_rr_uses_the_floored_value():
    cost = round_trip_cost_frac("GOOGL")
    rr = net_rr(283.7, 270.0, 310.0, cost)
    res = pc.check_plan(**_GOOGL_PLAN)
    assert f"{floor_rr(rr):.2f}" in _check_of(res, "rr")["message"]


def test_plan_check_plan_block_carries_the_shared_cost():
    res = pc.check_plan(**_GOOGL_PLAN)
    assert res["plan"]["cost_frac"] == pytest.approx(round_trip_cost_frac("GOOGL"))


# ---------------------------------------------------------------------------
# 9d. One cost model, one net R/R: the engine, Plan Check and the simulator agree
# ---------------------------------------------------------------------------

def _walk(n, seed=11, sd=0.012):
    rng = np.random.default_rng(seed)
    c = 280.0 * np.exp(np.cumsum(rng.normal(0.0, sd, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.002, n))
    hi = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.004, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.004, n)))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c}, index=idx)


def test_engine_plan_check_and_simulator_agree_on_cost_and_net_rr():
    entry, stop, target = 283.7, 270.0, 310.0
    cost = round_trip_cost_frac("GOOGL")
    assert ta.TradeSetupGenerator.round_trip_cost_pct("GOOGL") / 100 == pytest.approx(cost, rel=1e-12)
    assert ps.round_trip_cost_frac("GOOGL") == pytest.approx(cost, rel=1e-12)
    sim = ps.simulate_plan(_walk(400), entry=entry, stop=stop, target=target, paths=500,
                           ticker="GOOGL", now=datetime(2026, 10, 29, 22, tzinfo=timezone.utc))
    expected = round(net_rr(entry, stop, target, cost), 4)
    assert sim["plan"]["rr_net"] == pytest.approx(expected, abs=1e-4)
    assert sim["plan"]["cost_frac"] == pytest.approx(cost, rel=1e-12)
    res = pc.check_plan(**_GOOGL_PLAN)
    assert res["plan"]["cost_frac"] == pytest.approx(cost, rel=1e-12)
    assert f"{floor_rr(expected):.2f}" in _check_of(res, "rr")["message"]


def test_india_listing_pays_the_statutory_levies_in_every_module():
    us = round_trip_cost_frac("RELIANCE.NS") - round_trip_cost_frac("AAPL")
    assert us == pytest.approx(2 * LEVELS.RESEARCH_INDIA_STATUTORY_PER_SIDE)
    assert ta.TradeSetupGenerator.round_trip_cost_pct("RELIANCE.NS") / 100 == pytest.approx(
        round_trip_cost_frac("RELIANCE.NS"), rel=1e-12)
    assert ps.round_trip_cost_frac("RELIANCE.NS") == pytest.approx(round_trip_cost_frac("RELIANCE.NS"))


# ---------------------------------------------------------------------------
# 9g. Simulator volatility: completed bars only, horizon-averaged, shocks decay
# ---------------------------------------------------------------------------

def test_horizon_variance_mean_reverts_and_a_shock_counts_for_about_its_share():
    sample = 0.01 ** 2
    shock_ewma = sample + 0.06 * (0.2 ** 2)          # one -20% day lifts the one-day EWMA by this
    avg = ps._horizon_var(sample, shock_ewma, 63)
    raw_excess = shock_ewma - sample
    used_excess = avg - sample
    assert 0 < used_excess <= 0.5 * raw_excess       # a shock day counts for well under half its raw EWMA effect
    assert ps._horizon_var(sample, shock_ewma, 1) == pytest.approx(shock_ewma)


def test_simulated_volatility_is_bounded_by_the_sample_and_the_ewma():
    out = ps.simulate_plan(_walk(500), entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL",
                           now=datetime(2026, 10, 29, 22, tzinfo=timezone.utc))
    h = out["history"]
    lo, hi = sorted((h["vol_sample_ann"], h["vol_ewma_ann"]))
    assert lo - 1e-4 <= h["vol_used_ann"] <= hi + 1e-4
    assert 0.5 < h["vol_scale"] < 2.0
    assert isinstance(h["vol_method"], str) and "half-life" in h["vol_method"]


def test_a_recent_shock_moves_the_used_volatility_less_than_the_raw_ewma():
    base = _walk(500, sd=0.008)
    shocked = base.copy()
    shocked.iloc[-1, shocked.columns.get_loc("Close")] *= 0.80
    shocked.iloc[-1, shocked.columns.get_loc("Low")] *= 0.78
    shocked.iloc[-1, shocked.columns.get_loc("High")] = shocked.iloc[-2]["Close"] * 1.001
    shocked.iloc[-1, shocked.columns.get_loc("Open")] = shocked.iloc[-2]["Close"] * 0.999
    now = datetime(2026, 10, 29, 22, tzinfo=timezone.utc)
    a = ps.simulate_plan(base, entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL", now=now)
    b = ps.simulate_plan(shocked, entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL", now=now)
    raw_gap = b["history"]["vol_ewma_ann"] ** 2 - a["history"]["vol_ewma_ann"] ** 2
    used_gap = b["history"]["vol_used_ann"] ** 2 - a["history"]["vol_used_ann"] ** 2
    assert raw_gap > 0 and used_gap < raw_gap


def test_live_bar_is_not_a_bootstrap_sample_but_its_move_reaches_the_volatility():
    base = _walk(400, sd=0.01)
    live = base.copy()
    last = live.index[-1] + pd.offsets.BDay(1)
    prior_close = float(base["Close"].iloc[-1])
    row = pd.DataFrame({"Open": [prior_close], "High": [prior_close * 1.01],
                        "Low": [prior_close * 0.80], "Close": [prior_close * 0.82]}, index=[last])
    live = pd.concat([base, row])
    # "Now" is mid-session on the live bar's own day, so that bar is still forming.
    now = datetime(last.year, last.month, last.day, 16, 0, tzinfo=timezone.utc)
    out = ps.simulate_plan(live, entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL", now=now)
    settled = ps.simulate_plan(base, entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL",
                               now=datetime(2026, 10, 29, 22, tzinfo=timezone.utc))
    assert out["history"]["live_bar_in_volatility"] is True
    assert out["history"]["sessions_used"] == settled["history"]["sessions_used"]
    assert out["history"]["vol_ewma_ann"] > settled["history"]["vol_ewma_ann"]


def test_plain_block_carries_the_figures_the_page_shows():
    out = ps.simulate_plan(_walk(400), entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL",
                           now=datetime(2026, 10, 29, 22, tzinfo=timezone.utc))
    plain = out["plain"]
    assert set(plain["per_100"]) == {"target", "stop", "timeout"}
    assert all(isinstance(v, int) for v in plain["per_100"].values())
    assert isinstance(plain["breakeven_per_100"], int)
    assert plain["history_range_per_100"] is None or len(plain["history_range_per_100"]) == 2
    assert plain["verdict"].endswith(".")
    assert plain["independent_samples"] is None or plain["independent_samples"] >= 0


def test_the_simulator_refuses_a_series_with_no_completed_bar():
    idx = pd.bdate_range(end="2026-10-29", periods=300)
    closes = 280.0 * np.exp(np.cumsum(np.full(300, 0.001)))
    df = pd.DataFrame({"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes}, index=idx)
    # Pinned to the trading day itself, mid-session: today's bar is the only forming one.
    with pytest.raises(ps.PlanSimulationError):
        ps.simulate_plan(df.iloc[-1:], entry=283.7, stop=270.0, target=310.0, paths=500, ticker="GOOGL",
                         now=datetime(2026, 10, 29, 16, tzinfo=timezone.utc))



def test_last_release_is_known_even_when_the_calendar_has_rolled_forward(_clear_earnings_cache):
    """A calendar already showing the next report must not hide a report made yesterday."""
    stock = _EarningsStock("TSTE6", {"Earnings Date": [datetime(2027, 2, 3)]}, _table([
        (datetime(2026, 10, 28, 16), 2.8), (datetime(2027, 2, 3, 16), float("nan"))]))
    res = ta.CatalystAnalyzer(None)._analyze_calendar(stock, ta.CatalystData(), date(2026, 10, 29), "TSTE6")
    assert res.earnings_date == "2027-02-03"
    assert res.last_earnings_date == "2026-10-28"
    assert res.sessions_since_last_earnings == 1


def test_route_reads_last_release_and_live_move_from_the_cached_payload(monkeypatch):
    import sys
    from pathlib import Path
    webapp = Path(__file__).resolve().parent.parent / "webapp"
    if str(webapp) not in sys.path:
        sys.path.insert(0, str(webapp))
    import plan_routes
    payload = {"catalysts": {"earnings_date": "2027-02-03", "last_earnings_date": "2026-10-28",
                             "sessions_since_last_earnings": 1},
               "technical": {"live_preview": {"day_change_pct": -12.8, "large_move": True,
                                              "as_of": "2026-10-29"}}}
    monkeypatch.setattr(plan_routes, "research_read", lambda symbol: payload)
    last, move = plan_routes._research_facts("GOOGL")
    assert last == date(2026, 10, 28)
    assert move["large_move"] is True and move["day_change_pct"] == -12.8
    settled = dict(payload, technical={})
    monkeypatch.setattr(plan_routes, "research_read", lambda symbol: settled)
    assert plan_routes._research_facts("GOOGL")[1] is None
