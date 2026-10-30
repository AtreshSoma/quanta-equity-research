"""Event study for material filings: how this stock has reacted to each 8-K item, against SPY.

For each past 8-K, the abnormal return is the stock's simple return minus the
benchmark's simple return over the same sessions. The window starts at the close
BEFORE the first reaction session and ends at the close of the 1st, 2nd, 5th or
20th reaction-session day. Prices are the split-adjusted daily closes; dividends
are not added back, for both the stock and SPY.

Each 8-K is counted once, under its primary item (see sec_events.primary_item).
Co-filed items are listed with the filing but not counted again.

Honesty rules: a group with fewer than MIN_N filings shows no mean, median or
interval. The numbers describe past reactions of this stock to this kind of
filing. They are not a forecast.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import sec_events as _se

WINDOWS: Tuple[int, ...] = (1, 2, 5, 20)  # sessions counted from the close before the reaction session
HEADLINE_WINDOW = 5
MIN_N = 5
BOOT_MIN_N = 10
BOOT_RESAMPLES = 2000
BOOT_SEED = 20261028
LOOKBACK_YEARS = 5
BENCHMARK = "SPY"
NOTE_NOT_FORECAST = "Past reactions of this stock to this kind of filing; small samples; not a forecast."
NOTE_ATTRIBUTION = ("Each 8-K is counted once, under its primary item: the highest-severity item other than 9.01, "
                    "ties going to the lower item number. Co-filed items are listed with the filing but not counted again.")
NOTE_PRICES = ("Returns are price returns from split-adjusted daily closes. Dividends are not added back, "
               "for the stock or for SPY.")
NOTE_INTRADAY = ("A filing made during trading hours is measured from the close before its own day, so its day-0 "
                 "window includes trading before the filing.")
NOTE_AMENDMENTS = "8-K/A amendments are listed but not studied, so one event is not counted twice."

# Two-sided 95% Student-t critical values for df = 1..30; the normal value above that.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
        10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
        18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def t_critical(df: int) -> float:
    if df < 1:
        return float("nan")
    return _T95.get(df, 1.96)


def close_map(frame: Any) -> Dict[date, float]:
    """Trading date to close from a daily-bar frame. Finite closes only."""
    out: Dict[date, float] = {}
    if frame is None or "Close" not in getattr(frame, "columns", []):
        return out
    for ts, v in frame["Close"].items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(f):
            continue
        d = ts.date() if hasattr(ts, "date") else ts
        out[d] = f
    return out


def aligned(stock: Dict[date, float], bench: Dict[date, float]) -> Tuple[List[date], np.ndarray, np.ndarray]:
    """Dates on which both series have a close, sorted, with the two close arrays."""
    dates = sorted(set(stock) & set(bench))
    return dates, np.array([stock[d] for d in dates], dtype=float), np.array([bench[d] for d in dates], dtype=float)


def abnormal_returns(dates: Sequence[date], s: np.ndarray, b: np.ndarray,
                     reaction: date) -> Optional[Dict[int, Optional[float]]]:
    """Abnormal return (fraction) for each window, or None when the reaction session has no bar.

    A window is None when the price history ends before it completes.
    """
    r0 = bisect_left(list(dates), reaction)
    if r0 >= len(dates) or dates[r0] != reaction:
        return None
    base = r0 - 1
    if base < 0:
        return None
    out: Dict[int, Optional[float]] = {}
    for sessions in WINDOWS:
        end = r0 + sessions - 1
        if end >= len(dates):
            out[sessions] = None
            continue
        stock_ret = s[end] / s[base] - 1.0
        bench_ret = b[end] / b[base] - 1.0
        out[sessions] = float(stock_ret - bench_ret)
    return out


def summarize(values: Sequence[float]) -> Dict[str, Any]:
    """Statistics for one window, in percent. Below MIN_N nothing is estimated; the numbers are None."""
    n = len(values)
    out: Dict[str, Any] = {"n": n, "min_n": MIN_N, "too_few": n < MIN_N,
                           "mean_pct": None, "median_pct": None, "sd_pct": None, "ci95_pct": None,
                           "bootstrap_ci95_pct": None, "share_negative": None, "mean_abs_pct": None}
    if n < MIN_N:
        return out
    x = np.asarray(values, dtype=float) * 100.0
    mean = float(x.mean())
    sd = float(x.std(ddof=1))
    half = t_critical(n - 1) * sd / math.sqrt(n)
    out.update(mean_pct=_r(mean), median_pct=_r(float(np.median(x))), sd_pct=_r(sd),
               ci95_pct=[_r(mean - half), _r(mean + half)],
               share_negative=_r(float(np.mean(x < 0)), 4), mean_abs_pct=_r(float(np.mean(np.abs(x)))))
    if n >= BOOT_MIN_N:
        rng = np.random.default_rng(BOOT_SEED)
        idx = rng.integers(0, n, size=(BOOT_RESAMPLES, n))
        means = x[idx].mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        out["bootstrap_ci95_pct"] = [_r(float(lo)), _r(float(hi))]
    return out


def _r(v: float, dp: int = 4) -> float:
    return round(float(v), dp)


def study_events(events: List[Dict[str, Any]], stock: Dict[date, float],
                 bench: Dict[date, float]) -> Dict[str, Any]:
    """Per-item statistics from past 8-K events.

    Each event needs ``primary_item`` and ``reaction_session`` (ISO date). Events
    with no primary item are skipped and counted.
    """
    dates, s, b = aligned(stock, bench)
    groups: Dict[str, Dict[str, Any]] = {}
    no_price = 0
    unattributed = 0
    for ev in events:
        code = ev.get("primary_item")
        if not code:
            unattributed += 1
            continue
        g = groups.setdefault(code, {"filings": 0, "no_price": 0, "by_window": {w: [] for w in WINDOWS}})
        g["filings"] += 1
        try:
            reaction = date.fromisoformat(str(ev.get("reaction_session")))
        except ValueError:
            g["no_price"] += 1
            no_price += 1
            continue
        ar = abnormal_returns(dates, s, b, reaction)
        if ar is None:
            g["no_price"] += 1
            no_price += 1
            continue
        for w in WINDOWS:
            if ar[w] is not None:
                g["by_window"][w].append(ar[w])
    items: Dict[str, Any] = {}
    for code, g in sorted(groups.items()):
        tag = _se.tag_item(code) or {"code": code, "title": code, "severity": _se.UNRATED}
        items[code] = {
            "code": code,
            "title": tag["title"],
            "severity": tag["severity"],
            "filings": g["filings"],
            "no_price_data": g["no_price"],
            "headline_window": HEADLINE_WINDOW,
            "windows": {str(w): summarize(g["by_window"][w]) for w in WINDOWS},
        }
    return {"items": items, "filings_studied": sum(g["filings"] for g in groups.values()),
            "no_price_data": no_price, "unattributed": unattributed,
            "price_rows": len(dates), "benchmark": BENCHMARK}


def build_report(symbol: str, *, days: int = _se.DEFAULT_DAYS, today: Optional[date] = None,
                 edgar: Any = None, bars_loader: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """The full material-filings answer for one symbol: recent filings plus the past-reaction study.

    ``status`` is "ok", "not_covered" or "unavailable". Study failures are
    reported as ``study_status`` and never hide the filings.
    """
    today = today or date.today()
    sym = str(symbol or "").strip().upper()
    notes: List[str] = [NOTE_NOT_FORECAST, NOTE_ATTRIBUTION, NOTE_PRICES, NOTE_INTRADAY, NOTE_AMENDMENTS]
    base: Dict[str, Any] = {"ticker": sym, "as_of": today.isoformat(), "days": int(days),
                            "lookback_years": LOOKBACK_YEARS, "covered": _se.covered(sym)}
    if not base["covered"]:
        return {**base, "status": "not_covered", "message": _se.not_covered_message(sym), "recent": [],
                "study": {}, "study_status": "not_run", "study_message": "", "notes": notes}
    since = today - timedelta(days=int(365.25 * LOOKBACK_YEARS) + 2)
    loaded = _se.load_events(sym, since, today, edgar)
    if loaded["status"] == "not_covered":
        return {**base, "covered": False, "status": "not_covered", "message": loaded["message"], "recent": [],
                "study": {}, "study_status": "not_run", "study_message": "", "notes": notes}
    if loaded["status"] != "ok":
        return {**base, "status": "unavailable", "reason": loaded.get("reason"), "message": loaded["message"],
                "fix": loaded.get("fix", ""), "recent": [], "study": {}, "study_status": "not_run",
                "study_message": "", "notes": notes}
    events = loaded["events"]
    if loaded["coverage_short"] and loaded.get("covered_from"):
        notes.append(f"The SEC filing list reaches back only to {loaded['covered_from']}, so older 8-Ks are not studied.")
    if loaded["malformed_rows"]:
        notes.append(f"{loaded['malformed_rows']} SEC filing row(s) could not be read and were left out.")
    recent = _se.within_days(events, int(days), today)
    study: Dict[str, Any] = {}
    study_status = "not_run"
    study_message = ""
    if bars_loader is not None:
        try:
            stock_map = close_map(bars_loader(sym))
            bench_map = close_map(bars_loader(BENCHMARK))
            if not stock_map or not bench_map:
                raise ValueError("no closes")
            study = study_events([e for e in events if e["form"] == "8-K"], stock_map, bench_map)
            study_status = "ok"
        except Exception as exc:  # noqa: BLE001 - a price failure must not hide the filings
            study_status = "unavailable"
            study_message = (f"Price history could not be read ({type(exc).__name__}), so past reactions are not shown. "
                             "The filings are still listed.")
    return {**base, "status": "ok", "message": "", "recent": recent, "study": study,
            "study_status": study_status, "study_message": study_message, "notes": notes,
            "events_in_lookback": len(events)}
