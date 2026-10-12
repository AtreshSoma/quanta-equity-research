"""
Shared decision-core engine for QUANTA stock-picking and single-stock analysis.

This package centralises validation, dividend normalisation, risk overrides,
final-action resolution and consistency checking so that SP_ELITE_PRO.py and
TradingAnalysis_ELITE.py cannot disagree on the same ticker.

Public surface:
    FinalAction               -- unified action enum (BUY/HOLD_EXISTING/WATCHLIST/...)
    DecisionInputs            -- minimal struct the decision engine needs
    DecisionResult            -- result of decision engine (action + reasons + flags)
    validate_ticker_data      -- hard pre-scoring gate
    normalize_dividend_yield  -- single source of truth for dividend %
    resolve_final_action      -- single source of truth for final action
    check_allocation_eligible -- allocation gate
    validate_consistency      -- pre-render cross-check
"""
from .types import FinalAction, ConfidenceTier, DecisionInputs, DecisionResult, ValidationReport
from .data_validator import validate_ticker_data, is_etf_quote_type
from .data_status import (
    DataStatus,
    DATA_FAILURE_STATUSES,
    is_data_failure,
    is_true_invalid,
    status_label,
)
from .dividend import dividend_yield_from_info, normalize_dividend_yield, validate_dividend_yield
from .risk_overrides import apply_risk_overrides
from .decision_engine import resolve_final_action, signal_to_final_action
from .allocation import check_allocation_eligible
from .consistency_validator import validate_consistency
from .backtest_labels import BACKTEST_DIAGNOSTIC_BANNER, backtest_disclaimer
from .risk_grade import RiskGradeInputs, compute_risk_grade, risk_grade_drivers

__all__ = [
    "FinalAction",
    "ConfidenceTier",
    "DecisionInputs",
    "DecisionResult",
    "ValidationReport",
    "validate_ticker_data",
    "is_etf_quote_type",
    "DataStatus",
    "DATA_FAILURE_STATUSES",
    "is_data_failure",
    "is_true_invalid",
    "status_label",
    "normalize_dividend_yield",
    "dividend_yield_from_info",
    "validate_dividend_yield",
    "apply_risk_overrides",
    "resolve_final_action",
    "signal_to_final_action",
    "check_allocation_eligible",
    "validate_consistency",
    "BACKTEST_DIAGNOSTIC_BANNER",
    "backtest_disclaimer",
    "RiskGradeInputs",
    "compute_risk_grade",
    "risk_grade_drivers",
]
