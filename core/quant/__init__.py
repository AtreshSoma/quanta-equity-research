"""
quant/ — Quantitative analytics subpackage for SP_ELITE_PRO.

Each module is independent, fail-safe, and degrades gracefully when its
dependencies (yfinance data, scipy, etc.) are missing. Public API is kept
small and stable so integration sites in SP_ELITE_PRO.py stay one-liners.

Modules
-------
- data_quality   : sanitize raw fundamentals (cap outliers, flag artifacts)
- risk_model     : Ledoit-Wolf covariance + robust mean-variance (small-N safe)
- factor_model   : FF5+MOM exposure via liquid ETF-proxy regression
- (planned)      : walk_forward, regime_signals, sentiment_nlp, dcf, reconciliation
"""

from .data_quality import (
    FundamentalSanitizer,
    SanitizationReport,
    sanitize_fundamentals,
)
from .risk_model import (
    RobustOptimizer,
    OptimizationResult,
    ledoit_wolf_shrinkage,
)
from .factor_model import (
    FactorExposureModel,
    FactorLoadings,
    ETF_PROXIES,
)

__all__ = [
    "FundamentalSanitizer",
    "SanitizationReport",
    "sanitize_fundamentals",
    "RobustOptimizer",
    "OptimizationResult",
    "ledoit_wolf_shrinkage",
    "FactorExposureModel",
    "FactorLoadings",
    "ETF_PROXIES",
]
