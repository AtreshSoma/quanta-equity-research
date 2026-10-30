"""Plan Simulator: how often a long plan's target is reached before its stop.

Given daily OHLC bars and a plan {entry, stop, target, horizon}, this module
answers two questions and says how far each answer can be trusted:

1. **Simulation.** A stationary block bootstrap (Politis and Romano, geometric
   block lengths) redraws the last N sessions, expressed as log-ratios to the
   prior close. Blocks keep short-range autocorrelation and volatility
   clustering that i.i.d. draws would destroy. Each drawn bar is rescaled so the
   sample's volatility becomes today's EWMA volatility (lambda 0.94). Paths are
   resolved bar by bar with the plan's stop and target.

2. **Historical replay.** The same plan geometry, expressed as ATR multiples
   (Wilder ATR 14), is applied to every past session as a hypothetical entry at
   that session's close, and resolved forward with real prices. Entries overlap,
   so the Wilson interval uses the effective sample n / horizon.

Path rules (long only):
* an open at or below the stop fills at the open (a loss worse than -1R);
* an open at or above the target fills at the open;
* a bar that touches both stop and target counts as a stop (conservative);
* a path with neither by the horizon exits at the last close (timeout).

R is net of a round-trip cost ``c = entry x cost_frac`` charged to both legs,
the same convention as :func:`engine.levels.net_rr`:
``R = (exit - entry - c) / (entry - stop + c)``.

Pure functions over a DataFrame. Loading bars is the caller's job (the webapp
blueprint reuses the app's cached daily history).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import bars as _bars
from .costs import round_trip_cost_frac  # the one cost model (re-exported for callers)
from .levels import net_rr
from .thresholds import RESEARCH

# Defaults and bounds (the route validates the same bounds before calling in).
DEFAULT_PATHS = 5000
DEFAULT_HORIZON = 63
MIN_HORIZON, MAX_HORIZON = 5, 126
DEFAULT_LOOKBACK = 756
MIN_LOOKBACK, MAX_LOOKBACK = 250, 2000
MIN_PATHS, MAX_PATHS = 500, 20000
DEFAULT_BLOCK_MEAN = 7.0
MIN_BLOCK_MEAN, MAX_BLOCK_MEAN = 2.0, 20.0
DEFAULT_SEED = 20261028
EWMA_LAMBDA = 0.94
ATR_PERIOD = 14
TRADING_DAYS = 252
# Target share within this many points of break-even reads "Close to break-even".
CLOSE_BAND = 0.03
_Z95 = 1.959963984540054

BAND_BELOW = "Below break-even"
BAND_CLOSE = "Close to break-even"
BAND_ABOVE = "Above break-even"
BAND_NOT_DISTINGUISHABLE = "Not distinguishable from break-even"

_TARGET, _STOP, _TIMEOUT = 0, 1, 2
_OUTCOME_NAME = {_TARGET: "target", _STOP: "stop", _TIMEOUT: "timeout"}


class PlanSimulationError(ValueError):
    """The plan or the history cannot be simulated; ``str(exc)`` says why."""


# ---------------------------------------------------------------------------
# Costs and geometry
# ---------------------------------------------------------------------------

def _check_geometry(entry: float, stop: float, target: float) -> None:
    for name, v in (("entry", entry), ("stop", stop), ("target", target)):
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) <= 0:
            raise PlanSimulationError(f"{name} must be a finite positive number")
    if not (stop < entry < target):
        raise PlanSimulationError("a long plan needs stop < entry < target")


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = ATR_PERIOD) -> np.ndarray:
    """Wilder ATR aligned to the bars; NaN before the first full window.

    Delegates to the shared ``engine.indicators.atr`` so the simulator's stop
    geometry uses the same ATR as the engines (one definition per concept).
    """
    from engine import indicators as _ind

    h, l, c = (pd.Series(np.asarray(a, dtype=float)) for a in (high, low, close))
    return _ind.atr(h, l, c, period).to_numpy(dtype=float)


def wilson_interval(k: float, n: int, z: float = _Z95) -> Optional[Tuple[float, float]]:
    """Wilson score interval for k successes in n trials (None when n < 1)."""
    if n < 1:
        return None
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

def _ohlc(bars: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.Index]:
    if bars is None or len(bars) == 0:
        raise PlanSimulationError("no price bars")
    df = bars.rename(columns={c: str(c).capitalize() for c in bars.columns})
    missing = [c for c in ("Open", "High", "Low", "Close") if c not in df.columns]
    if missing:
        raise PlanSimulationError(f"bars lack {', '.join(missing)}")
    df = df[["Open", "High", "Low", "Close"]].astype(float).dropna()
    df = df[(df > 0).all(axis=1) & (df["High"] >= df["Low"])]
    if len(df) == 0:
        raise PlanSimulationError("no usable price bars")
    return (df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy(),
            df["Close"].to_numpy(), df.index)


def _session_label(stamp: Any) -> str:
    return str(stamp)[:10]


# ---------------------------------------------------------------------------
# Path resolution (shared by the simulation and the historical replay)
# ---------------------------------------------------------------------------

def resolve_paths(Opn: np.ndarray, H: np.ndarray, L: np.ndarray, C: np.ndarray,
                  entry: Any, stop: Any, target: Any) -> Tuple[np.ndarray, np.ndarray]:
    """Resolve each row of (n, horizon) bar arrays against its own stop and target.

    ``entry``, ``stop`` and ``target`` broadcast against the rows (scalars or
    (n,) arrays). Returns ``(exit_price, outcome)`` per row, where outcome is
    0 target, 1 stop, 2 timeout (exit at the last close).
    """
    S = np.asarray(stop, dtype=float).reshape(-1, 1)
    T = np.asarray(target, dtype=float).reshape(-1, 1)
    gap_stop = Opn <= S
    gap_tgt = (~gap_stop) & (Opn >= T)
    open_ok = ~(gap_stop | gap_tgt)
    intra_stop = open_ok & (L <= S)
    intra_tgt = open_ok & (~intra_stop) & (H >= T)
    hit = gap_stop | gap_tgt | intra_stop | intra_tgt
    price = np.where(gap_stop | gap_tgt, Opn, np.where(intra_stop, S, T))
    kind = np.where(gap_stop | intra_stop, _STOP, _TARGET)
    rows = np.arange(Opn.shape[0])
    any_hit = hit.any(axis=1)
    first = hit.argmax(axis=1)
    exit_price = np.where(any_hit, price[rows, first], C[:, -1])
    outcome = np.where(any_hit, kind[rows, first], _TIMEOUT)
    return exit_price, outcome


def net_r(exit_price: np.ndarray, entry: Any, stop: Any, cost_frac: float) -> np.ndarray:
    """Net R of an exit: ``(exit - entry - c) / (entry - stop + c)``, c = entry x cost."""
    E = np.asarray(entry, dtype=float)
    c = E * cost_frac
    return (np.asarray(exit_price, dtype=float) - E - c) / (E - np.asarray(stop, dtype=float) + c)


def _tally(outcome: np.ndarray) -> Dict[str, int]:
    return {
        "target": int(np.sum(outcome == _TARGET)),
        "stop": int(np.sum(outcome == _STOP)),
        "timeout": int(np.sum(outcome == _TIMEOUT)),
    }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _block_indices(rng: np.random.Generator, n_bars: int, paths: int, horizon: int,
                   block_mean: float) -> np.ndarray:
    """Stationary bootstrap indices: each step starts a new random block with
    probability 1 / block_mean, otherwise continues the previous block (wrapping)."""
    new_block = rng.random((paths, horizon)) < (1.0 / block_mean)
    starts = rng.integers(0, n_bars, size=(paths, horizon))
    idx = np.empty((paths, horizon), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for k in range(1, horizon):
        idx[:, k] = np.where(new_block[:, k], starts[:, k], (idx[:, k - 1] + 1) % n_bars)
    return idx


def _ewma_var(log_close_returns: np.ndarray, lam: float, live_return: Optional[float] = None) -> float:
    """RiskMetrics EWMA variance of the completed returns, seeded with their variance.

    A live (still-forming) day's close-to-close return, when there is one, is the
    newest observation and is folded in last: today's move is real information,
    so it counts. It is not a sample for the bootstrap.
    """
    v = float(np.var(log_close_returns, ddof=1))
    for r in log_close_returns:
        v = lam * v + (1.0 - lam) * float(r) * float(r)
    if live_return is not None:
        v = lam * v + (1.0 - lam) * float(live_return) ** 2
    return v


def _horizon_var(sample_var: float, ewma_var: float, horizon: int) -> float:
    """Average variance over the holding period, mean-reverting to the sample.

    A one-day EWMA reading is a forecast for tomorrow only. Applied to all
    ``horizon`` sessions it overstates risk after a shock, because the shock
    decays. The average of the decaying forecast over the horizon is
    ``LR + (EWMA - LR) (1 - phi^H) / (H (1 - phi))``, with LR the sample variance
    and ``phi = 0.5 ** (1 / half_life)``. A shock day therefore moves the
    forecast by well under its raw EWMA effect for a 63-session horizon.
    """
    phi = 0.5 ** (1.0 / float(RESEARCH.VOL_HALF_LIFE_SESSIONS))
    h = max(1, int(horizon))
    weight = (1.0 - phi ** h) / (h * (1.0 - phi))
    return sample_var + (ewma_var - sample_var) * weight


def simulate_plan(bars: pd.DataFrame, *, entry: float, stop: float, target: float,
                  horizon_sessions: int = DEFAULT_HORIZON, lookback_sessions: int = DEFAULT_LOOKBACK,
                  block_mean: float = DEFAULT_BLOCK_MEAN, paths: int = DEFAULT_PATHS,
                  seed: int = DEFAULT_SEED, drift: str = "zero",
                  cost_frac: Optional[float] = None, ticker: str = "",
                  side: str = "long", now: Any = None) -> Dict[str, Any]:
    """Simulate a long plan and replay its geometry over history.

    ``drift``: "zero" (the default) removes the sample's average daily log
    return; "historical" keeps the stock's own past trend. Zero is the default
    because a stock that rose 60% a year over the lookback would otherwise be
    simulated as if it will keep doing so, which flatters every long plan on
    exactly the names that already ran (the hindsight a reality check must
    not have). Raises PlanSimulationError when the plan or history cannot
    support a simulation.
    """
    if side != "long":
        raise PlanSimulationError("only long plans are supported")
    _check_geometry(entry, stop, target)
    if not (MIN_HORIZON <= int(horizon_sessions) <= MAX_HORIZON):
        raise PlanSimulationError(f"horizon must be {MIN_HORIZON} to {MAX_HORIZON} sessions")
    if not (MIN_LOOKBACK <= int(lookback_sessions) <= MAX_LOOKBACK):
        raise PlanSimulationError(f"lookback must be {MIN_LOOKBACK} to {MAX_LOOKBACK} sessions")
    if not (MIN_BLOCK_MEAN <= float(block_mean) <= MAX_BLOCK_MEAN):
        raise PlanSimulationError(f"block mean must be {MIN_BLOCK_MEAN:g} to {MAX_BLOCK_MEAN:g} sessions")
    if not (MIN_PATHS <= int(paths) <= MAX_PATHS):
        raise PlanSimulationError(f"paths must be {MIN_PATHS} to {MAX_PATHS}")
    if drift not in ("historical", "zero"):
        raise PlanSimulationError("drift must be 'historical' or 'zero'")

    H_days = int(horizon_sessions)
    E, S, T = float(entry), float(stop), float(target)
    k = round_trip_cost_frac(ticker) if cost_frac is None else float(cost_frac)
    Opn, Hi, Lo, C, index = _ohlc(bars)

    # Only completed, settled bars are samples (engine.bars.scoring_bars). A live,
    # still-forming bar is left out of the bootstrap pool and the history tests;
    # its close-to-close move reaches the volatility forecast as the newest input.
    live_return = None
    frame = pd.DataFrame({"Open": Opn, "High": Hi, "Low": Lo, "Close": C}, index=index)
    done, _basis = _bars.scoring_bars(frame, ticker, now)
    if _basis.get("live_bar_excluded") and len(C) >= 2:
        live_return = math.log(float(C[-1]) / float(C[-2]))
    Opn, Hi, Lo, C = (done[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close"))
    index = done.index
    if len(C) == 0:
        raise PlanSimulationError("no completed daily bar yet; the history is still forming")

    # Ratio bars need the prior close, so the window is lookback + 1 closes.
    available = len(C) - 1
    if available < MIN_LOOKBACK:
        raise PlanSimulationError(
            f"only {available} usable sessions of history; at least {MIN_LOOKBACK} are needed "
            f"to simulate this plan")
    n = min(int(lookback_sessions), available)
    o_, h_, l_, c_ = Opn[-(n + 1):], Hi[-(n + 1):], Lo[-(n + 1):], C[-(n + 1):]
    lo = np.log(o_[1:] / c_[:-1])
    lh = np.log(h_[1:] / c_[:-1])
    ll = np.log(l_[1:] / c_[:-1])
    lc = np.log(c_[1:] / c_[:-1])

    sample_sd = float(np.std(lc, ddof=1))
    if not (sample_sd > 0 and math.isfinite(sample_sd)):
        raise PlanSimulationError("the history has no price movement to simulate")
    mu = float(np.mean(lc))
    sample_var = sample_sd * sample_sd
    ewma_var = _ewma_var(lc, EWMA_LAMBDA, live_return)
    avg_var = _horizon_var(sample_var, ewma_var, H_days)
    vol_scale = math.sqrt(avg_var / sample_var)
    keep_mu = mu if drift == "historical" else 0.0

    # Centre, rescale to today's volatility, restore the chosen drift.
    def _rescale(x: np.ndarray) -> np.ndarray:
        return keep_mu + (x - mu) * vol_scale

    lo_s, lh_s, ll_s, lc_s = _rescale(lo), _rescale(lh), _rescale(ll), _rescale(lc)

    rng = np.random.default_rng(int(seed))
    idx = _block_indices(rng, n, int(paths), H_days, float(block_mean))
    LC = lc_s[idx]
    cum = np.cumsum(LC, axis=1)
    prev_close = E * np.exp(np.concatenate([np.zeros((idx.shape[0], 1)), cum[:, :-1]], axis=1))
    P_O = prev_close * np.exp(lo_s[idx])
    P_H = prev_close * np.exp(lh_s[idx])
    P_L = prev_close * np.exp(ll_s[idx])
    P_C = E * np.exp(cum)
    exit_p, outcome = resolve_paths(P_O, P_H, P_L, P_C, E, S, T)
    r_sim = net_r(exit_p, E, S, k)
    tally = _tally(outcome)
    n_paths = int(idx.shape[0])
    p_t, p_s, p_to = (tally["target"] / n_paths, tally["stop"] / n_paths, tally["timeout"] / n_paths)

    rr_net = net_rr(E, S, T, k)
    break_even = 1.0 / (1.0 + rr_net) if rr_net > 0 else 1.0
    decided_sim = tally["target"] + tally["stop"]
    share_sim = tally["target"] / decided_sim if decided_sim else float("nan")

    # Historical replay: same geometry in ATR multiples, entry at each close.
    atr = wilder_atr(Hi, Lo, C, ATR_PERIOD)
    atr_now = float(atr[-1]) if len(atr) and math.isfinite(float(atr[-1])) else float("nan")
    if not (atr_now > 0):
        raise PlanSimulationError("ATR could not be measured from the history")
    stop_mult = (E - S) / atr_now
    target_mult = (T - E) / atr_now
    hist = _historical_replay(Opn, Hi, Lo, C, n, H_days, stop_mult, target_mult, k, atr)

    decided_h = hist["target"] + hist["stop"]
    n_eff = max(1, int(round(decided_h / H_days))) if decided_h else 0
    eff_share = hist["target"] / decided_h if decided_h else None
    if decided_h and n_eff:
        k_eff = int(round(eff_share * n_eff))
        ci_eff = wilson_interval(k_eff, n_eff)
        ci_raw = wilson_interval(hist["target"], decided_h)
    else:
        ci_eff = ci_raw = None

    verdict = _verdict(share_sim, break_even, ci_eff)

    annual_drift = mu * TRADING_DAYS
    out: Dict[str, Any] = {
        "available": True,
        "seed": int(seed),
        "paths": n_paths,
        "horizon_sessions": H_days,
        "plan": {
            "entry": round(E, 4), "stop": round(S, 4), "target": round(T, 4),
            "risk_per_share": round(E - S, 4), "reward_per_share": round(T - E, 4),
            "stop_pct": round((E - S) / E, 6), "target_pct": round((T - E) / E, 6),
            "rr_gross": round((T - E) / (E - S), 4), "rr_net": round(rr_net, 4),
            "cost_frac": k, "atr": round(atr_now, 4),
            "stop_atr": round(stop_mult, 3), "target_atr": round(target_mult, 3),
        },
        "history": {
            "sessions_used": n,
            "sessions_requested": int(lookback_sessions),
            "first_session": _session_label(index[-(n + 1)]),
            "last_session": _session_label(index[-1]),
            "block_mean": float(block_mean),
            "vol_sample_ann": round(sample_sd * math.sqrt(TRADING_DAYS), 4),
            "vol_ewma_ann": round(math.sqrt(ewma_var) * math.sqrt(TRADING_DAYS), 4),
            "vol_used_ann": round(math.sqrt(avg_var) * math.sqrt(TRADING_DAYS), 4),
            "vol_method": _vol_method(H_days, n, live_return is not None),
            "live_bar_in_volatility": live_return is not None,
            "vol_scale": round(vol_scale, 4),
            "drift_ann": round(annual_drift, 4),
            "drift": drift,
        },
        "simulation": {
            "p_target": round(p_t, 4),
            "p_stop": round(p_s, 4),
            "p_timeout": round(p_to, 4),
            "target_share_decided": round(share_sim, 4) if math.isfinite(share_sim) else None,
            "break_even_share": round(break_even, 4),
            "expected_r": round(float(np.mean(r_sim)), 4),
            "r": {
                "mean": round(float(np.mean(r_sim)), 4),
                "median": round(float(np.median(r_sim)), 4),
                "p5": round(float(np.percentile(r_sim, 5)), 4),
                "p95": round(float(np.percentile(r_sim, 95)), 4),
            },
            "simulated_band": verdict["simulated_band"],
        },
        "historical": {
            "sessions_tested": hist["sessions_tested"],
            "unresolved_sessions": hist["unresolved_sessions"],
            "target": hist["target"],
            "stop": hist["stop"],
            "timeout": hist["timeout"],
            "decided": decided_h,
            "target_share_decided": round(eff_share, 4) if eff_share is not None else None,
            "wilson95_raw": _ci(ci_raw),
            "effective_n": n_eff,
            "wilson95_effective": _ci(ci_eff),
            "mean_r": hist["mean_r"],
            "median_r": hist["median_r"],
        },
        "verdict": verdict["verdict"],
    }
    out["plain"] = _plain(out, verdict)
    out["summary"] = _summary(out, verdict)
    out["notes"] = _notes(out, H_days, k)
    return out


def _vol_method(horizon: int, sessions: int, live: bool) -> str:
    """How the volatility the simulation uses was reached, in plain words."""
    live_txt = " Today's live move is the newest input." if live else ""
    return (f"The {horizon}-session average of an EWMA (lambda {EWMA_LAMBDA}) that decays toward the "
            f"{sessions}-session sample on a {RESEARCH.VOL_HALF_LIFE_SESSIONS}-session half-life, "
            f"so one large day counts for its share of the horizon, not all of it.{live_txt}")


def _plain(out: Dict[str, Any], verdict: Dict[str, Any]) -> Dict[str, Any]:
    """The figures a reader needs, per 100 paths, with the verdict in one sentence."""
    sim, hist = out["simulation"], out["historical"]
    band = verdict["simulated_band"]
    rel = {BAND_BELOW: "below", BAND_CLOSE: "near", BAND_ABOVE: "above"}.get(band, "near")
    read = verdict["verdict"]["history_read"]
    if read == "insufficient":
        hist_txt = "The stock's own history is too short to judge this plan."
    elif read == "not distinguishable":
        hist_txt = "The stock's own history cannot tell this plan from break-even."
    else:
        hist_txt = f"The stock's own history reads {read} break-even."
    ci = hist["wilson95_effective"]
    return {
        "per_100": {
            "target": int(round(sim["p_target"] * 100)),
            "stop": int(round(sim["p_stop"] * 100)),
            "timeout": int(round(sim["p_timeout"] * 100)),
        },
        "breakeven_per_100": int(round(sim["break_even_share"] * 100)),
        "history_per_100": (int(round(hist["target_share_decided"] * 100))
                            if hist["target_share_decided"] is not None else None),
        "history_range_per_100": ([int(round(ci[0] * 100)), int(round(ci[1] * 100))] if ci else None),
        "independent_samples": hist["effective_n"] or None,
        "verdict": f"The simulation puts the target share {rel} break-even. {hist_txt}",
    }


def _ci(ci: Optional[Tuple[float, float]]) -> Optional[List[float]]:
    return None if ci is None else [round(ci[0], 4), round(ci[1], 4)]


def _historical_replay(Opn, Hi, Lo, C, n_window: int, H_days: int, stop_mult: float,
                       target_mult: float, k: float, atr: np.ndarray) -> Dict[str, Any]:
    from numpy.lib.stride_tricks import sliding_window_view

    total = len(C)
    first = max(total - n_window, ATR_PERIOD)   # ATR must exist at the entry
    last_entry = total - 1 - H_days             # H full sessions must follow
    empty = {"sessions_tested": 0, "unresolved_sessions": 0, "target": 0, "stop": 0,
             "timeout": 0, "mean_r": None, "median_r": None}
    if last_entry < first:
        empty["unresolved_sessions"] = max(0, total - first)
        return empty
    entries = np.arange(first, last_entry + 1)
    E = C[entries]
    A = atr[entries]
    S = E - stop_mult * A
    T = E + target_mult * A
    # Row i of the window view is Opn[i+1 : i+1+H]; entry i needs the bars after it.
    wO = sliding_window_view(Opn[1:], H_days)[entries]
    wH = sliding_window_view(Hi[1:], H_days)[entries]
    wL = sliding_window_view(Lo[1:], H_days)[entries]
    wC = sliding_window_view(C[1:], H_days)[entries]
    exit_p, outcome = resolve_paths(wO, wH, wL, wC, E, S, T)
    r = net_r(exit_p, E, S, k)
    tally = _tally(outcome)
    return {
        "sessions_tested": int(len(entries)),
        "unresolved_sessions": int(total - 1 - last_entry),
        **tally,
        "mean_r": round(float(np.mean(r)), 4),
        "median_r": round(float(np.median(r)), 4),
    }


def _verdict(share_sim: float, break_even: float, ci_eff: Optional[Tuple[float, float]]) -> Dict[str, Any]:
    if not math.isfinite(share_sim):
        band = BAND_CLOSE
    elif share_sim < break_even - CLOSE_BAND:
        band = BAND_BELOW
    elif share_sim > break_even + CLOSE_BAND:
        band = BAND_ABOVE
    else:
        band = BAND_CLOSE
    if ci_eff is None:
        history_read = "insufficient"
        label = band
    elif ci_eff[0] <= break_even <= ci_eff[1]:
        history_read = "not distinguishable"
        label = BAND_NOT_DISTINGUISHABLE
    elif ci_eff[0] > break_even:
        history_read = "above"
        label = band
    else:
        history_read = "below"
        label = band
    return {"simulated_band": band,
            "verdict": {"label": label, "simulated_band": band, "history_read": history_read,
                        "historical_distinguishable": history_read != "not distinguishable"
                        and history_read != "insufficient"}}


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{float(x) * 100:.0f}%"


def _summary(out: Dict[str, Any], verdict: Dict[str, Any]) -> str:
    sim, hist, plan = out["simulation"], out["historical"], out["plan"]
    parts = [
        f"In {out['paths']:,} simulated paths built from {out['history']['sessions_used']} sessions, "
        f"{_pct(sim['p_target'])} reached the target, {_pct(sim['p_stop'])} hit the stop and "
        f"{_pct(sim['p_timeout'])} timed out after {out['horizon_sessions']} sessions.",
        f"Expected result {sim['expected_r']:+.2f}R net of costs. Zero expectancy needs "
        f"{_pct(sim['break_even_share'])} of decided paths to reach the target"
        f" (planned net R/R {plan['rr_net']:.2f}).",
    ]
    if verdict["verdict"]["history_read"] == "insufficient":
        parts.append("This stock's own history is too short to judge the plan's geometry.")
    elif verdict["verdict"]["history_read"] == "not distinguishable":
        ci = hist["wilson95_effective"]
        parts.append(
            f"The stock's own history gives {_pct(hist['target_share_decided'])} "
            f"(95% CI {_pct(ci[0])} to {_pct(ci[1])}, effective n {hist['effective_n']}), "
            f"which is not distinguishable from break-even.")
    else:
        parts.append(f"The stock's own history reads {verdict['verdict']['history_read']} break-even.")
    return " ".join(parts)


def _notes(out: Dict[str, Any], H_days: int, k: float) -> List[str]:
    hist, h = out["history"], out["historical"]
    notes = [
        f"Simulation, not a forecast: built from this stock's last {hist['sessions_used']} sessions; "
        "earnings, halts and news can do things those sessions never did.",
        f"Entry is assumed filled at the plan entry on session 0. Costs: {k * 10000:.0f} bps round trip, "
        "charged to both legs, so R is net of costs.",
        "Gaps: an open through the stop fills at the open (a loss worse than -1R); an open through the "
        "target fills at the open. A bar that touches both stop and target counts as a stop.",
        f"Volatility: drawn bars rescaled from the {hist['vol_sample_ann'] * 100:.1f}% sample to "
        f"{hist['vol_used_ann'] * 100:.1f}% over the {H_days}-session horizon (scale {hist['vol_scale']:.2f}). "
        f"The one-day EWMA alone is {hist['vol_ewma_ann'] * 100:.1f}%; it is pulled back toward the sample "
        f"on a {RESEARCH.VOL_HALF_LIFE_SESSIONS}-session half-life.",
        ("Drift: the sample's average trend "
         f"({hist['drift_ann'] * 100:+.1f}% a year) is kept, which assumes the past trend continues."
         if hist["drift"] == "historical" else "Drift: removed; the simulation assumes no trend."),
        f"Break-even ({out['simulation']['break_even_share'] * 100:.0f}%) ignores gap fills, so read expected R "
        "as the figure to trust over the share alone.",
        f"Historical replay: {h['sessions_tested']} past sessions were tested with the same ATR geometry. Entries "
        f"overlap across a {H_days}-session horizon, so the {h['decided']} decided trades are about "
        f"{h['effective_n']} independent ones; the interval uses that effective count (n / horizon).",
    ]
    if hist["sessions_used"] < hist["sessions_requested"]:
        notes.append(f"Only {hist['sessions_used']} sessions were available, fewer than the "
                     f"{hist['sessions_requested']} requested.")
    if h["effective_n"] and h["effective_n"] < 10:
        notes.append("Fewer than 10 independent historical trades: the interval is wide and says little.")
    return notes
