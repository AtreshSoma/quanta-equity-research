"""APEX Quant command-line runner.

Usage (from the repo root, with the venv active):

    python -m core.apex_quant.run validate \
        --start 2014-01-01 --end 2025-01-01 \
        --train-years 3 --test-years 1

    python -m core.apex_quant.run status   # prints the sizing gate verdict

    python -m core.apex_quant.run elite-oos \
        --end 2026-08-27 --eval-every 5

`elite-oos` evaluates the ELITE composite itself (plan blocker H-09) rather
than the rule-based APEX strategy: it recomputes the composite point-in-time
across history and measures its information coefficient against forward
returns. It reads the SEPARATE research price cache, never the committed
fixture parquets in _cache/.

Plain text only. No emojis, no decorative output. If the strategy is not
validated, the program says so and refuses to emit position sizes.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import validation
from .costs import CostModel
from .data_provider import CachedPriceProvider, _CACHE_DIR_RESEARCH
from .simulator import SimConfig
from .universe import get_universe
from .walkforward import run_walk_forward

_REPORT_DIR = Path(__file__).resolve().parents[2] / "apex_quant_reports"
_ARTIFACT = _REPORT_DIR / "validation.json"


def _setup_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")


def cmd_validate(args: argparse.Namespace) -> int:
    provider = CachedPriceProvider(start=args.data_start, end=args.end, cache_dir=_CACHE_DIR_RESEARCH)
    tickers = get_universe()
    if args.max_tickers:
        tickers = tickers[: args.max_tickers]

    print(f"Universe: {len(tickers)} tickers (static, survivorship-biased).")
    print(f"Validating {args.start} -> {args.end} "
          f"(train {args.train_years}y / test {args.test_years}y)\n")

    result = run_walk_forward(
        provider=provider,
        tickers=tickers,
        start=args.start,
        end=args.end,
        train_years=args.train_years,
        test_years=args.test_years,
        cfg=SimConfig(),
        cost_model=CostModel(),
        log=print,
    )

    print()
    print(validation.confidence_statement(result))
    path = validation.write_artifact(result, _ARTIFACT)
    print(f"\nValidation artifact written: {path}")
    return 0 if result.beats_benchmarks() else 2


def cmd_elite_oos(args: argparse.Namespace) -> int:
    """Evaluate the ELITE composite out of sample."""
    from .elite_oos import format_report, run_oos_report, save_report

    tickers = get_universe()
    if args.max_tickers:
        tickers = tickers[: args.max_tickers]

    report = run_oos_report(
        tickers=tickers,
        as_of=args.end,
        eval_every=args.eval_every,
    )
    print(format_report(report))
    path = save_report(report)
    print(f"\nwritten: {path}")
    print(f"         {path.with_suffix('.txt')}")
    return 0


def cmd_elite_walkforward(args: argparse.Namespace) -> int:
    """Walk-forward trade simulation of the ELITE composite entry rule.

    Reuses the same harness, cost model and -- critically -- the same
    random-entry / random-exit baselines as `validate`, so the two results are
    directly comparable. Requires a cached panel: run `elite-oos` first.
    """
    from . import elite_strategy
    from .elite_oos import load_panel

    try:
        panel = load_panel()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2

    dates = panel.index.get_level_values("date")
    print(f"panel: {len(panel):,} rows, {dates.min().date()}..{dates.max().date()}")

    provider = CachedPriceProvider(start=args.data_start, end=args.end, cache_dir=_CACHE_DIR_RESEARCH)
    tickers = get_universe()
    if args.max_tickers:
        tickers = tickers[: args.max_tickers]

    missing = set(tickers) - set(panel.index.get_level_values("ticker"))
    if missing:
        raise ValueError(f"Historical score panel is incomplete: {sorted(missing)}")

    result = run_walk_forward(
        provider=provider,
        tickers=tickers,
        start=args.start,
        end=args.end,
        train_years=args.train_years,
        test_years=args.test_years,
        cfg=SimConfig(),
        cost_model=CostModel(),
        param_grid=elite_strategy.EliteParams.grid(),
        signal_fn=elite_strategy.generate_elite_signals,
        feature_fn=lambda df, tk: elite_strategy.feature_frame_from_panel(df, panel, tk),
        stability_keys=elite_strategy.STABILITY_KEYS,
    )

    print()
    print("=" * 74)
    print("ELITE COMPOSITE - WALK-FORWARD TRADE SIMULATION")
    print("=" * 74)
    print(f"OOS window   {result.oos_start.date()} .. {result.oos_end.date()}")
    print(f"folds        {len(result.folds)}")
    print(f"data hash    {result.data_hash[:32]}...")
    print()
    header = (
        f"{'':<16}{'CAGR%':>8}{'Sharpe':>8}{'Sortino':>8}{'MaxDD%':>8}"
        f"{'trades':>7}{'hit%':>7}{'expect%':>8}{'PF':>6}{'alpha%':>8}"
    )
    print(header)
    print("-" * len(header))
    for label, rep in (
        ("strategy", result.strategy),
        ("buy&hold SPY", result.spy),
        ("random entry", result.random_entry),
        ("random exit", result.random_exit),
    ):
        alpha = rep.alpha_ann_pct
        print(
            f"{label:<16}"
            f"{rep.cagr_pct:>8.2f}"
            f"{rep.sharpe:>8.2f}"
            f"{rep.sortino:>8.2f}"
            f"{rep.max_drawdown_pct:>8.2f}"
            f"{rep.n_trades:>7}"
            f"{rep.hit_rate_pct:>7.1f}"
            f"{rep.expectancy_pct:>8.3f}"
            f"{rep.profit_factor:>6.2f}"
            f"{('n/a' if alpha is None else f'{alpha:.2f}'):>8}"
        )
    print()
    print("A strategy must beat ALL THREE baselines on a risk-adjusted basis to")
    print("claim an edge. Beating only buy-and-hold is not evidence of skill:")
    print("random-entry tests whether entry timing mattered, random-exit whether")
    print("the exit rule mattered.")
    print()
    print("param stability across folds:")
    for key, values in result.param_stability.items():
        print(f"  {key:<20} {values}")
    path = validation.write_artifact(
        result, Path(__file__).resolve().parent / "_reports" / "elite_validation.json")
    print(f"\nValidation artifact: {path}")
    print(f"Universe coverage: {len(result.validation_inputs['universe'])}/{len(tickers)}")
    print("Scope: partial price/risk composite; this does not validate the complete live model.")
    return 0 if result.coverage_complete else 2


def cmd_status(_: argparse.Namespace) -> int:
    artifact = validation.load_artifact(_ARTIFACT)
    print(validation.sizing_gate_message(artifact))
    if artifact:
        print(f"  generated : {artifact.get('generated_utc')}")
        print(f"  oos window: {artifact.get('oos_start')} -> {artifact.get('oos_end')}")
        print(f"  passed    : {artifact.get('passed')}")
    return 0 if validation.is_current(artifact) else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apex_quant", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="run walk-forward validation")
    v.add_argument("--start", default="2014-01-01", help="OOS analysis start")
    v.add_argument("--end", default="2025-01-01", help="OOS analysis end")
    v.add_argument("--data-start", default="2010-01-01", help="history download start")
    v.add_argument("--train-years", type=int, default=3)
    v.add_argument("--test-years", type=int, default=1)
    v.add_argument("--max-tickers", type=int, default=0, help="0 = full universe")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser(
        "elite-oos", help="evaluate the ELITE composite out of sample (H-09)"
    )
    e.add_argument("--end", default=None, help="as-of date; default today")
    e.add_argument(
        "--eval-every", type=int, default=5,
        help="bars between composite evaluations; 5 = weekly, 1 = daily",
    )
    e.add_argument("--max-tickers", type=int, default=0, help="0 = full universe")
    e.set_defaults(func=cmd_elite_oos)

    w = sub.add_parser(
        "elite-walkforward",
        help="walk-forward trade simulation of the ELITE composite (needs elite-oos first)",
    )
    w.add_argument("--start", default="2014-01-01", help="OOS analysis start")
    w.add_argument("--end", default="2026-08-27", help="OOS analysis end")
    w.add_argument("--data-start", default="2010-01-01", help="history download start")
    w.add_argument("--train-years", type=int, default=3)
    w.add_argument("--test-years", type=int, default=1)
    w.add_argument("--max-tickers", type=int, default=0, help="0 = full universe")
    w.set_defaults(func=cmd_elite_walkforward)

    s = sub.add_parser("status", help="print the sizing gate verdict")
    s.set_defaults(func=cmd_status)
    return p


def main() -> int:
    _setup_logging()
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
