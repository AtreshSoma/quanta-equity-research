"""What the book is exposed to: Fama-French five factors plus momentum.

The book's daily returns (today's weights over the shared history, the same
replay the volatility and tail-risk figures use) are regressed on the
Fama-French research factors:

    r_book - RF = alpha + b_mkt (Mkt-RF) + b_smb SMB + b_hml HML
                        + b_rmw RMW + b_cma CMA + b_mom Mom + e

A beta of 1.2 on Mkt-RF means the book moved about 1.2% for each 1% excess move
of the US market; a positive SMB loading leans small-cap, HML value, RMW
profitable firms, CMA conservative investers, Mom recent winners. Standard
errors are Newey-West (Bartlett kernel), because daily residuals are serially
correlated; intervals are normal 95%.

This describes the past behaviour of today's weights. It is not a forecast, and
the factors are US-market factors: Indian listings are left out and counted in
``coverage``.

Data: Kenneth R. French data library (daily files), free and public. It is
published with a lag of a few weeks, so the window ends at the factors' last
date (``as_of``). Downloaded files are cached on disk; when a refresh fails the
last good copy is served and flagged ``stale``.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from engine.thresholds import PORTFOLIO as _PF

logger = logging.getLogger(__name__)

BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {
    "ff5": "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
    "mom": "F-F_Momentum_Factor_daily_CSV.zip",
}
FACTORS = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom")
LABELS = {
    "Mkt-RF": "US market", "SMB": "Small minus big (size)", "HML": "High minus low book-to-market (value)",
    "RMW": "Robust minus weak profitability", "CMA": "Conservative minus aggressive investment",
    "Mom": "Momentum (winners minus losers)",
}
SOURCE = "Kenneth R. French data library (Fama-French 5 factors and momentum, daily)"
CACHE_NAME = "ff_factors_daily.csv"
CACHE_TTL_SECONDS = 7 * 86400        # the library updates monthly
_TRADING_DAYS = 252
_Z95 = 1.959963984540054

Fetch = Callable[[str], bytes]


# ------------------------------------------------------------------ parsing
def parse_french_csv(text: str) -> pd.DataFrame:
    """Daily rows of one French CSV, in decimal returns, indexed by date.

    The files carry a free-text preamble, a header line that starts with a
    comma, then ``YYYYMMDD, value, ...`` rows, then a copyright line. Values are
    percent; -99.99 and -999 mean missing.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(",")), None)
    if start is None:
        raise ValueError("no header row in factor file")
    header = [h.strip() for h in lines[start].split(",")]
    rows = []
    for ln in lines[start + 1:]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) != len(header) or not (len(parts[0]) == 8 and parts[0].isdigit()):
            if rows:
                break          # end of the daily block
            continue
        rows.append(parts)
    if not rows:
        raise ValueError("no daily rows in factor file")
    frame = pd.DataFrame(rows, columns=["date"] + header[1:])
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
    frame = frame.set_index("date").apply(pd.to_numeric, errors="coerce")
    frame = frame.mask(frame.isin([-99.99, -999.0]))
    return frame / 100.0


def _read_zip(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        return zf.read(name).decode("latin-1")


def _http_fetch(url: str) -> bytes:
    import requests
    resp = requests.get(url, timeout=20, headers={"User-Agent": "QUANTA research (personal use)"})
    resp.raise_for_status()
    return resp.content


def _cache_path() -> Path:
    root = os.environ.get("QUANTA_CACHE_DIR") or str(Path(__file__).resolve().parents[2] / "data" / "cache")
    return Path(root) / CACHE_NAME


def load_factors(fetch: Optional[Fetch] = None, cache_path: Optional[Path] = None,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """The daily factor frame and where it came from.

    Returns ``{"factors": DataFrame | None, "as_of", "from_cache", "stale",
    "source", "error"}``. Never raises.
    """
    fetch = fetch or _http_fetch
    path = Path(cache_path or _cache_path())
    now = time.time() if now is None else now
    cached = None
    if path.exists():
        try:
            cached = pd.read_csv(path, index_col=0, parse_dates=True)
        except Exception as exc:  # noqa: BLE001 - a bad cache is refetched
            logger.warning("factor cache unreadable, refetching: %s", exc)
    fresh = cached is not None and now - path.stat().st_mtime < CACHE_TTL_SECONDS

    def result(frame, from_cache, stale, error=None):
        as_of = frame.index.max().date().isoformat() if frame is not None and len(frame) else None
        return {"factors": frame, "as_of": as_of, "from_cache": from_cache, "stale": stale,
                "source": SOURCE, "error": error}

    if fresh:
        return result(cached, True, False)
    try:
        ff5 = parse_french_csv(_read_zip(fetch(BASE_URL + FILES["ff5"])))
        mom = parse_french_csv(_read_zip(fetch(BASE_URL + FILES["mom"])))
        mom.columns = [c.strip().title() if c.strip().lower() == "mom" else c for c in mom.columns]
        frame = ff5.join(mom[["Mom"]], how="inner").dropna()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        frame.to_csv(tmp)
        os.replace(tmp, path)
        return result(frame, False, False)
    except Exception as exc:  # noqa: BLE001 - attribution is optional
        logger.warning("factor download failed: %s", exc)
        if cached is not None:
            return result(cached, True, True, f"refresh failed ({type(exc).__name__}); last good copy")
        return result(None, False, False, f"factor data unavailable ({type(exc).__name__})")


# ------------------------------------------------------------------ regression
def _bartlett_lags(n: int) -> int:
    return max(1, int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))


def ols_hac(y: np.ndarray, X: np.ndarray, lags: Optional[int] = None) -> Dict[str, Any]:
    """OLS of y on X (X already holds an intercept column) with Newey-West covariance."""
    n, k = X.shape
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta
    L = _bartlett_lags(n) if lags is None else int(lags)
    u = X * resid[:, None]
    S = u.T @ u
    for lag in range(1, min(L, n - 1) + 1):
        w = 1.0 - lag / (L + 1.0)
        G = u[lag:].T @ u[:-lag]
        S += w * (G + G.T)
    cov = xtx_inv @ S @ xtx_inv * (n / (n - k))
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else None
    return {"beta": beta, "se": se, "r2": r2, "lags": L, "n": n}


def book_returns(weights: Mapping[str, float], returns: pd.DataFrame, min_days: int) -> Dict[str, Any]:
    """Daily returns of today's weights over the shared history (as the volatility figure)."""
    cols = [c for c in returns.columns
            if c in weights and weights[c] > 0 and returns[c].notna().sum() >= min_days]
    if not cols:
        return {"series": None, "columns": [], "coverage": 0.0}
    frame = returns[cols].dropna()
    total = sum(weights[c] for c in cols)
    w = np.array([weights[c] / total for c in cols])
    series = pd.Series(frame.to_numpy(dtype=float) @ w, index=pd.to_datetime(frame.index))
    if getattr(series.index, "tz", None) is not None:
        series.index = series.index.tz_localize(None)
    series.index = series.index.normalize()
    invested = sum(v for v in weights.values() if v > 0)
    return {"series": series, "columns": cols, "coverage": total / invested if invested else 0.0}


def is_us_listing(symbol: str) -> bool:
    return not str(symbol).upper().endswith((".NS", ".BO"))


def attribute(weights: Mapping[str, float], returns: Any, factors: Optional[pd.DataFrame],
              window_days: Optional[int] = None, min_days: Optional[int] = None) -> Dict[str, Any]:
    """Factor loadings of the book. ``weights`` are market values (any positive scale)."""
    window_days = int(window_days or _PF.FACTOR_WINDOW_DAYS)
    min_days = int(min_days or _PF.FACTOR_MIN_DAYS)
    out: Dict[str, Any] = {"available": False, "reason": None, "factors": [], "alpha_annual": None,
                           "r2": None, "days": 0, "start": None, "end": None, "coverage": 0.0,
                           "excluded_non_us": [], "window_days": window_days, "min_days": min_days,
                           "method": ("OLS of the book's daily excess return on the Fama-French five "
                                      "factors and momentum; Newey-West standard errors. Today's "
                                      "weights over past returns: a description, not a forecast.")}
    if factors is None or getattr(factors, "empty", True):
        out["reason"] = "Factor data unavailable."
        return out
    if returns is None or getattr(returns, "empty", True):
        out["reason"] = "No daily return history available."
        return out
    us = {s: float(v) for s, v in weights.items() if v and v > 0 and is_us_listing(s)}
    out["excluded_non_us"] = sorted(s for s, v in weights.items() if v and v > 0 and not is_us_listing(s))
    book = book_returns(us, returns, min_days)
    invested = sum(float(v) for v in weights.values() if v and v > 0)
    out["coverage"] = (book["coverage"] * sum(us.values()) / invested) if invested else 0.0
    if book["series"] is None:
        out["reason"] = f"No US holding has {min_days} days of return history."
        return out
    joined = pd.concat([book["series"].rename("book"), factors[list(FACTORS) + ["RF"]]],
                       axis=1, join="inner").dropna().sort_index().tail(window_days)
    if len(joined) < min_days:
        out["reason"] = (f"Only {len(joined)} days overlap the factor data (it ends "
                         f"{factors.index.max().date().isoformat()}); {min_days} are needed.")
        return out
    y = (joined["book"] - joined["RF"]).to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(joined)), joined[list(FACTORS)].to_numpy(dtype=float)])
    fit = ols_hac(y, X)
    rows = []
    for i, name in enumerate(FACTORS, start=1):
        b, se = float(fit["beta"][i]), float(fit["se"][i])
        rows.append({"factor": name, "label": LABELS[name], "beta": b, "se": se,
                     "t": b / se if se > 0 else None,
                     "ci95": [b - _Z95 * se, b + _Z95 * se]})
    a, a_se = float(fit["beta"][0]), float(fit["se"][0])
    out.update(available=True, factors=rows, r2=fit["r2"], days=int(fit["n"]), lags=fit["lags"],
               start=joined.index[0].date().isoformat(), end=joined.index[-1].date().isoformat(),
               alpha_annual=a * _TRADING_DAYS,
               alpha_annual_ci95=[(a - _Z95 * a_se) * _TRADING_DAYS, (a + _Z95 * a_se) * _TRADING_DAYS],
               columns=book["columns"])
    return out


def exposure_view(weights: Mapping[str, float], returns: Any, loaded: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``attribute`` plus where the factors came from, for the portfolio view."""
    loaded = loaded or {}
    out = attribute(weights, returns, loaded.get("factors"))
    out.update(source=loaded.get("source", SOURCE), factors_as_of=loaded.get("as_of"),
               factors_stale=bool(loaded.get("stale")), factors_error=loaded.get("error"))
    if not out["available"] and loaded.get("error") and out["reason"] == "Factor data unavailable.":
        out["reason"] = f"Factor data unavailable: {loaded['error']}."
    return json.loads(json.dumps(out, default=float))


def weights_from_positions(positions: Sequence[Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in positions:
        v = getattr(p, "market_value", None)
        if v is not None and math.isfinite(float(v)) and float(v) > 0:
            out[p.symbol] = out.get(p.symbol, 0.0) + float(v)
    return out
