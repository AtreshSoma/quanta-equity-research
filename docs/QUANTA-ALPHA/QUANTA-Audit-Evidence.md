# QUANTA audit verification evidence

Reviewed commit: `7bb6386d176ca98764641031bab327ad0ab1d647`.

The remote CI run completed successfully: [GitHub Actions run](https://github.com/AtreshSoma/quanta-stock-analysis/actions/runs/34356189592).

## Locally rerun tests

The following targeted command ran against the retrieved source snapshot:

```sh
python3 -m pytest -q core/apex_quant/tests tests/test_decision_core.py tests/test_decision_overrides.py tests/test_earnings_surprise_units.py tests/test_elite_scorer.py tests/test_target_confidence.py tests/test_portfolio_exposure.py tests/test_sector_cap_binding.py
```

Result: **160 passed in 4.06 seconds.** This is an engineering test result, not evidence of investment returns.

## Isolated synthetic probes

The SignalTracker class was extracted from the reviewed AST without changing its methods. File writes and price acquisition were replaced by controlled test inputs. The optimizer was imported from the reviewed module and exercised with its SciPy-unavailable fallback; it is disconnected from the main application paths. The action resolver was called without any validation artifact. These were offline probes, not a full scan or live-data run.

```json
{
  "signal_tracker_horizon_reuse": {
    "recorded_5d_price": 105.0,
    "available_current_price": 120.0,
    "recorded_10d_price": 105.0,
    "fetch_count": 0
  },
  "unmatured_denominator": {
    "resolved_buy_signals": 1,
    "resolved_wins": 1,
    "unresolved_buy_signals": 1,
    "reported_win_rate_pct": 50.0
  },
  "disconnected_optimizer_fallback": {
    "requested_cap": 0.4,
    "reported_cap": 0.4,
    "weights": [
      0.7590225645181781,
      0.08515443281397367,
      0.07847584153210334,
      0.07734716113574511
    ],
    "method": "risk_parity"
  },
  "decision_core_has_no_validation_artifact_gate": {
    "action": "STRONG_BUY",
    "allocation_eligible": true
  },
  "scope": "Synthetic offline class/function probes, not a live-data backtest or production run."
}
```

The paired `QUANTA-Audit-Probes.py` contains the reproduction logic. To rerun it, set `ROOT` to a checkout of the reviewed commit with the repository's Python dependencies installed. Its `Config` stub supplies only outcome classification thresholds; the price-reuse and pending-denominator findings do not depend on the particular thresholds, given the supplied WIN record.

## Limits

- No new market-wide backtest or provider validation was run.
- Binary golden fixtures and the full browser test suite were not rerun locally.
- Current UI behavior was assessed from source, not a rendered browser session.
- Invalidated historical trade reports were not treated as valid current performance evidence.
- Active execution-path defects were distinguished from disconnected experimental code.
- No changes were pushed, no GitHub comment was posted, and no trades were placed.
