"""
factor_model.py — Fama-French 5 + Momentum factor exposure via ETF proxies.

WHY: SP_ELITE_PRO scores stocks on technicals/fundamentals/momentum but never
decomposes returns into systematic factor exposures vs idiosyncratic alpha.
Two stocks with identical scores can be near-clones of the market (zero edge)
or true alpha generators. Without the decomposition you can't tell.

We use liquid US ETFs as factor proxies — no Ken French data needed, no API
key, and the regression is interpretable on a 1-year window:

    SPY   — market (RM-RF)
    IWM   — SMB proxy (small)        ; SPY is the implicit "big" leg
    IWD   — HML proxy (value)        ; vs IWF (growth)
    QUAL  — QMJ / RMW proxy (quality)
    USMV  — low-vol / CMA proxy
    MTUM  — UMD / momentum
    SHY   — risk-free proxy (short Treasuries)

Output for each ticker: alpha, betas, R², residual vol, and a quality flag.
The intended downstream use is: penalize composite score when |alpha| is
noise (R² very low, residual vol very high) and boost it when alpha is
statistically meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

try:
    import pandas as pd  # type: ignore
except ImportError:  # pragma: no cover
    pd = None  # type: ignore


ETF_PROXIES: Dict[str, str] = {
    "MKT":  "SPY",
    "SMB":  "IWM",
    "HML":  "IWD",
    "QMJ":  "QUAL",
    "LOWV": "USMV",
    "MOM":  "MTUM",
    "RF":   "SHY",
}


@dataclass
class FactorLoadings:
    ticker: str
    alpha_annual: float = 0.0       # annualized alpha (intercept × 252)
    betas: Dict[str, float] = field(default_factory=dict)
    r_squared: float = 0.0
    residual_vol_annual: float = 0.0
    n_obs: int = 0
    t_alpha: float = 0.0            # t-statistic on alpha
    method: str = "ols_etf_proxy"

    @property
    def alpha_is_significant(self) -> bool:
        # Two-sided ~95% with df>30
        return abs(self.t_alpha) > 1.96

    @property
    def is_market_clone(self) -> bool:
        """High market beta + high R² + insignificant alpha."""
        return (
            self.r_squared > 0.85
            and abs(self.betas.get("MKT", 0.0) - 1.0) < 0.25
            and not self.alpha_is_significant
        )

    def explain(self) -> str:
        bits = [f"α={self.alpha_annual*100:+.1f}%/yr"]
        bits.append(f"βMKT={self.betas.get('MKT', 0):.2f}")
        bits.append(f"R²={self.r_squared:.2f}")
        if self.alpha_is_significant:
            bits.append("α-sig✓")
        if self.is_market_clone:
            bits.append("market-clone⚠")
        return " | ".join(bits)


class FactorExposureModel:
    """
    Regress excess stock returns on excess factor returns:
        r_i - r_f = α + Σ β_k (r_k - r_f) + ε

    Caller supplies a fetch_history callable so we don't hard-bind to any
    one data provider (works with yfinance, Polygon, etc.).
    """

    def __init__(
        self,
        fetch_history,                  # callable(ticker, period_days) -> DataFrame['Close']
        period_days: int = 252,
        min_obs: int = 60,
        cache_factors: bool = True,
    ):
        self.fetch_history = fetch_history
        self.period_days = int(period_days)
        self.min_obs = int(min_obs)
        self.cache_factors = bool(cache_factors)
        self._factor_returns: Optional["pd.DataFrame"] = None  # type: ignore

    # ---------- factor side ----------
    def _load_factor_returns(self) -> Optional["pd.DataFrame"]:  # type: ignore
        if pd is None:
            return None
        if self.cache_factors and self._factor_returns is not None:
            return self._factor_returns

        parts: Dict[str, "pd.Series"] = {}  # type: ignore
        for name, etf in ETF_PROXIES.items():
            try:
                df = self.fetch_history(etf, self.period_days)
            except Exception:
                df = None
            if df is None or len(df) < self.min_obs or "Close" not in df.columns:
                continue
            rets = df["Close"].pct_change().dropna()
            parts[name] = rets

        if "MKT" not in parts or "RF" not in parts:
            return None

        factor_df = pd.DataFrame(parts).dropna()
        if len(factor_df) < self.min_obs:
            return None

        # Build excess factor returns. Long-short legs from raw ETF returns:
        rf = factor_df["RF"]
        out = pd.DataFrame(index=factor_df.index)
        out["MKT"] = factor_df["MKT"] - rf
        if "SMB" in factor_df:
            out["SMB"] = factor_df["SMB"] - factor_df["MKT"]  # small minus big
        if "HML" in factor_df:
            out["HML"] = factor_df["HML"] - factor_df["MKT"]  # value minus market
        if "QMJ" in factor_df:
            out["QMJ"] = factor_df["QMJ"] - factor_df["MKT"]
        if "LOWV" in factor_df:
            out["LOWV"] = factor_df["LOWV"] - factor_df["MKT"]
        if "MOM" in factor_df:
            out["MOM"] = factor_df["MOM"] - factor_df["MKT"]
        out["RF"] = rf

        if self.cache_factors:
            self._factor_returns = out
        return out

    # ---------- per-stock regression ----------
    def fit(self, ticker: str) -> Optional[FactorLoadings]:
        if pd is None:
            return None
        try:
            stock_df = self.fetch_history(ticker, self.period_days)
        except Exception:
            return None
        if stock_df is None or "Close" not in stock_df.columns:
            return None
        stock_rets = stock_df["Close"].pct_change().dropna()
        if len(stock_rets) < self.min_obs:
            return None

        factors = self._load_factor_returns()
        if factors is None:
            return None

        aligned = pd.concat([stock_rets.rename("Y"), factors], axis=1).dropna()
        if len(aligned) < self.min_obs:
            return None

        y = (aligned["Y"] - aligned["RF"]).values
        X_cols = [c for c in aligned.columns if c not in ("Y", "RF")]
        X = aligned[X_cols].values
        X = np.column_stack([np.ones(len(X)), X])  # intercept

        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        except np.linalg.LinAlgError:
            return None

        y_hat = X @ beta
        residuals = y - y_hat
        ss_res = float((residuals ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

        n, k = X.shape
        dof = max(n - k, 1)
        sigma2 = ss_res / dof
        # Standard error of intercept = sqrt( sigma2 * (X'X)^{-1}[0,0] )
        try:
            xtx_inv = np.linalg.inv(X.T @ X)
            se_alpha = float(np.sqrt(max(sigma2 * xtx_inv[0, 0], 0.0)))
        except np.linalg.LinAlgError:
            se_alpha = 0.0
        t_alpha = beta[0] / se_alpha if se_alpha > 1e-12 else 0.0

        loadings = FactorLoadings(
            ticker=ticker,
            alpha_annual=float(beta[0]) * 252.0,
            betas={name: float(b) for name, b in zip(X_cols, beta[1:])},
            r_squared=float(r2),
            residual_vol_annual=float(np.sqrt(sigma2 * 252.0)),
            n_obs=int(n),
            t_alpha=float(t_alpha),
        )
        return loadings

    def fit_many(self, tickers: List[str]) -> Dict[str, FactorLoadings]:
        out: Dict[str, FactorLoadings] = {}
        for t in tickers:
            lo = self.fit(t)
            if lo is not None:
                out[t] = lo
        return out
