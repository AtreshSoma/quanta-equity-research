"""Research tear sheet fixes, 2026-10-23.

The 1Y "Performance vs SPY" row printed n/a for the benchmark on every US name:
the benchmark series was the one beta reads, a period="1y" fetch, which begins
a session or two after the 1Y window's start session.
"""
import numpy as np
import pandas as pd

import TradingAnalysis_ELITE as ta
from engine.relative_performance import relative_performance


def _closes(n: int, seed: int) -> pd.Series:
    idx = pd.bdate_range(end="2026-10-23", periods=n)
    rets = np.random.default_rng(seed).normal(0.0004, 0.01, n)
    return pd.Series(100 * np.cumprod(1 + rets), index=idx)


class _Ticker:
    def __init__(self, closes, calls):
        self._closes, self._calls = closes, calls

    def history(self, period=None, **_):
        self._calls.append(period)
        if period == "2y":
            return pd.DataFrame({"Close": self._closes})
        return pd.DataFrame({"Close": self._closes.iloc[-252:]})


def test_relative_performance_measures_the_1y_benchmark(monkeypatch):
    bench_closes, calls = _closes(504, 1), []
    monkeypatch.setattr(ta.yf, "Ticker", lambda symbol: _Ticker(bench_closes, calls))
    ra = ta.RiskAnalyzer()
    out = relative_performance(_closes(504, 2), ra._get_benchmark_returns_long("AAPL"), "SPY")
    row = {r["window"]: r for r in out["rows"]}["1Y"]
    want = (bench_closes.iloc[-1] / bench_closes.iloc[-253] - 1) * 100
    assert row["benchmark_pct"] is not None and abs(row["benchmark_pct"] - want) < 0.01
    # Beta keeps its one-year fetch; the long series is fetched once and cached.
    assert len(ra._get_spy_returns()) == 251
    ra._get_benchmark_returns_long("AAPL")
    assert calls == ["2y", "1y"]


def test_a_failed_long_fetch_falls_back_to_the_one_year_series(monkeypatch):
    one_year = _closes(252, 3).pct_change().dropna()
    ra = ta.RiskAnalyzer()

    def boom(symbol):
        raise RuntimeError("no network")
    monkeypatch.setattr(ta.yf, "Ticker", boom)
    ra._get_benchmark_returns = lambda ticker: one_year
    assert ra._get_benchmark_returns_long("AAPL") is one_year


# --- Every gate a blocked buy fails is named: a grade-F name whose reward/risk
# is also under the Research hurdle read "risk grade D or better would change
# this", which was not enough on its own.

def _grade_f(**kw):
    from engine.types import DecisionInputs
    base = dict(ticker="AAA", composite_score=59.4, risk_grade="F", risk_reward_ratio=1.2,
                current_price=211.84, stop_loss=195.18, target_price=241.12)
    base.update(kw)
    return DecisionInputs(**base)


def test_a_grade_f_name_also_names_its_failing_reward_risk():
    from engine.decision_engine import resolve_final_action
    from engine.types import FinalAction
    dec = resolve_final_action(_grade_f())
    assert dec.final_action is FinalAction.WATCHLIST
    assert dec.blocking_codes[0] == "RISK_GRADE_F", "the main blocker is unchanged"
    assert "LOW_REWARD_RISK" in dec.blocking_codes
    assert dec.contract["primary_blocker"]["code"] == "RISK_GRADE_F"
    assert dec.contract["holder_action"] == "TRIM"
    # The extra blocker is not an override: tier and reasons are as before.
    alone = resolve_final_action(_grade_f(risk_reward_ratio=2.5))
    assert dec.confidence_tier == alone.confidence_tier
    assert dec.reasons == alone.reasons


def test_no_extra_blocker_when_the_reward_risk_passes_or_the_levels_are_invalid():
    from engine.decision_engine import resolve_final_action
    assert "LOW_REWARD_RISK" not in resolve_final_action(_grade_f(risk_reward_ratio=2.5)).blocking_codes
    below = resolve_final_action(_grade_f(target_price=200.0, risk_grade="C"))
    assert "TARGET_BELOW_ENTRY" in below.blocking_codes and "LOW_REWARD_RISK" not in below.blocking_codes


# --- Buy zone: the spot at which the Research levels clear a hurdle, with the
# stop (an ATR distance) and the pro-rated target re-derived from that spot.

def test_the_clear_entry_reproduces_the_engines_net_rr():
    from engine.levels import net_rr, research_rr_clear_entry
    v, f, d, k = 328.95, 63 / 252, 16.66, 0.0011
    p = research_rr_clear_entry(v, f, d, k, 1.8)
    assert 200 < p < 211
    assert abs(net_rr(p, p - d, p + f * (v - p), k) - 1.8) < 1e-9
    # Above that spot the hurdle fails; below it, it passes.
    assert net_rr(p + 1, p + 1 - d, p + 1 + f * (v - p - 1), k) < 1.8 < net_rr(p - 1, p - 1 - d, p - 1 + f * (v - p + 1), k)
    assert research_rr_clear_entry(100.0, f, 50.0, k, 1.8) is None, "no positive price clears it"
