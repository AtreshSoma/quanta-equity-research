# These reports predate the F01 execution-chronology fix — do not cite them

`elite_walkforward.txt` and the trade-level statistics in `elite_oos.txt` /
`elite_oos.json` were produced by the portfolio simulator BEFORE audit finding
F01 was corrected (see `docs/QUANTA_AUDIT_2026-09-07.md`).

That simulator:

  * processed intraday exits and marked closing equity BEFORE admitting trades
    at the same day's open, so entry sizing and cash used information that did
    not exist at the entry time;
  * never checked stop or target on a position's own entry bar, so entry-day
    stop-outs were recorded as whatever happened on later bars — the audit's
    synthetic example reports +20% where the trade actually lost 10%;
  * filled stops at the stop level even when the whole bar gapped through it,
    and did the same for targets;
  * left positions open at the end of the calendar out of the trade statistics
    entirely, and dropped the first simulated day's P&L from the equity curve.

Every Sharpe, expectancy, win-rate and trade-count figure in these files is
therefore computed from an infeasible execution path. The direction and size of
the error are not uniform, so the numbers cannot be adjusted — they have to be
regenerated.

Regenerating needs the full 42-name universe (the local `_cache/` holds 13) and
a Parquet engine, so it is a networked run:

    pip install -e ".[dev]" pyarrow
    python -m core.apex_quant.run elite-oos          # rebuilds the score panel
    python -m core.apex_quant.run elite-walkforward  # needs that panel

`elite-walkforward` exits 2 when the price provider could not cover the whole
universe, and writes `elite_validation.json`. A run that exits 2 has NOT
re-validated anything.

Until that run happens, treat this directory as historical evidence about the
old simulator, not as evidence about the strategy. The corrected execution
semantics are pinned by `core/apex_quant/tests/test_execution_semantics.py`.
