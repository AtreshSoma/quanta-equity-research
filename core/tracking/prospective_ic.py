"""The pre-registered prospective test: per-date rank IC on the forward ledger.

``docs/preregistration/elite_research_v3.md`` (section 5) fixes how a Research
model version is judged on calls recorded after it was registered:

* for each publication date, the Spearman rank correlation between the model's
  score and the forward excess return over the benchmark, across that day's calls;
* the mean of those per-date ICs over non-overlapping publication dates (the
  ledger's own independence rule, :func:`tracking.ledger._independent_dates`), and
  its t-statistic across dates;
* the ledger's minimum sample (``EVIDENCE.MIN_SAMPLE`` resolved calls on
  ``EVIDENCE.MIN_DATES`` dates) before any verdict;
* pass when t >= 2.0.

This module computes exactly that and nothing more, so the procedure is fixed in
code before the first evaluation. It reads the ledger and never writes to it.
Calls from different code fingerprints are never pooled: the result is reported
per fingerprint, and the verdict reads one fingerprint only.

    python -m tracking.prospective_ic --engine elite_v3_shadow
    python -m tracking.prospective_ic --engine elite_v3_shadow --compare elite
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from .ledger import MIN_SAMPLE, SignalLedger, _EVIDENCE, _independent_dates

PASS_T = 2.0
REGISTERED_ON = "2026-10-29"
MIN_CALLS_PER_DATE = 3   # a rank correlation needs at least three names


def _context(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("context_json")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _eligible(rows: Iterable[Dict[str, Any]], horizon_days: int, since: str,
              strategy: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        if r.get("horizon_days") != horizon_days or str(r.get("as_of_date") or "") < since:
            continue
        if r.get("score") is None or r.get("excess_return_pct") is None:
            continue
        if strategy and _context(r).get("strategy", strategy) != strategy:
            continue
        out.append(r)
    return out


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation with average ranks for ties; None if undefined."""
    if len(x) < MIN_CALLS_PER_DATE or len(x) != len(y):
        return None
    rx = pd.Series(list(x), dtype=float).rank(method="average")
    ry = pd.Series(list(y), dtype=float).rank(method="average")
    if rx.std(ddof=0) == 0 or ry.std(ddof=0) == 0:
        return None
    return float(rx.corr(ry))


def _per_date_ic(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_date[str(r["as_of_date"])].append(r)
    out = {}
    for day, members in sorted(by_date.items()):
        ic = spearman([m["score"] for m in members], [m["excess_return_pct"] for m in members])
        if ic is not None:
            out[day] = {"ic": ic, "n": len(members),
                        "tickers": sorted({m["ticker"] for m in members})}
    return out


def _mean_t(values: Sequence[float]) -> Dict[str, Optional[float]]:
    k = len(values)
    if k == 0:
        return {"mean": None, "sd": None, "t": None, "ci95": None}
    mean = sum(values) / k
    if k < 2:
        return {"mean": mean, "sd": None, "t": None, "ci95": None}
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (k - 1))
    se = sd / math.sqrt(k)
    t = mean / se if se > 0 else None
    return {"mean": mean, "sd": sd, "t": t, "ci95": [mean - 1.96 * se, mean + 1.96 * se]}


def _fingerprint_report(rows: List[Dict[str, Any]], horizon_days: int) -> Dict[str, Any]:
    per_date = _per_date_ic(rows)
    independent = _independent_dates(list(per_date), horizon_days)
    stats = _mean_t([per_date[d]["ic"] for d in independent])
    n_calls = len(rows)
    n_dates = len({r["as_of_date"] for r in rows})
    evaluable = n_calls >= MIN_SAMPLE and len(independent) >= _EVIDENCE.MIN_DATES
    if not evaluable:
        verdict = "insufficient sample"
    elif stats["t"] is not None and stats["t"] >= PASS_T:
        verdict = "pass"
    else:
        verdict = "fail"
    return {
        "n_resolved_calls": n_calls, "n_dates": n_dates,
        "n_dates_with_ic": len(per_date), "n_independent_dates": len(independent),
        "independent_dates": independent,
        "mean_ic": stats["mean"], "sd_ic": stats["sd"], "t": stats["t"], "ci95": stats["ci95"],
        "evaluable": evaluable, "verdict": verdict,
        "per_date": {d: {"ic": v["ic"], "n": v["n"]} for d, v in per_date.items()},
    }


def fingerprint_of(row: Dict[str, Any]) -> str:
    """The fingerprint a row is grouped by: its model fingerprint when it has one.

    Rows recorded before the model fingerprint existed carry only the raw-byte
    code fingerprint; they are grouped under it (prefixed ``code:``), never merged
    with model-fingerprint groups.
    """
    model = row.get("model_fingerprint")
    if model:
        return str(model)
    code = row.get("code_fingerprint")
    return f"code:{code}" if code else "unknown"


def prospective_ic(ledger: SignalLedger, engine: str, horizon_days: int = 63,
                   since: str = REGISTERED_ON, strategy: Optional[str] = None) -> Dict[str, Any]:
    """The primary test for ``engine`` (e.g. "elite_v3_shadow"), per model fingerprint.

    ``verdict`` reads the fingerprint with the most resolved calls; every
    fingerprint is reported so a reader can see nothing was pooled.
    """
    rows = _eligible(ledger.rows(engine=engine, resolved=True), horizon_days, since, strategy)
    by_fp: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_fp[fingerprint_of(r)].append(r)
    reports = {fp: _fingerprint_report(rs, horizon_days) for fp, rs in by_fp.items()}
    primary_fp = max(reports, key=lambda fp: reports[fp]["n_resolved_calls"]) if reports else None
    primary = reports.get(primary_fp) if primary_fp else None
    return {
        "engine": engine, "strategy": strategy, "horizon_days": horizon_days, "since": since,
        "pass_t": PASS_T, "min_calls": MIN_SAMPLE, "min_independent_dates": _EVIDENCE.MIN_DATES,
        "primary_fingerprint": primary_fp,
        "verdict": primary["verdict"] if primary else "insufficient sample",
        "primary": primary, "by_fingerprint": reports,
        "note": ("Prospective test pre-registered in docs/preregistration/elite_research_v3.md. "
                 "A pass can raise the evidence status to PARTIAL only through the evidence "
                 "registry writer; it never validates a model on its own."),
    }


def paired_ic_difference(ledger: SignalLedger, engine: str, baseline: str, horizon_days: int = 63,
                         since: str = REGISTERED_ON) -> Dict[str, Any]:
    """Secondary test: per-date IC of ``engine`` minus ``baseline`` on the same dates and tickers."""
    a = _eligible(ledger.rows(engine=engine, resolved=True), horizon_days, since, None)
    b = _eligible(ledger.rows(engine=baseline, resolved=True), horizon_days, since, None)
    key = lambda r: (str(r["as_of_date"]), r["ticker"])  # noqa: E731
    common = {key(r) for r in a} & {key(r) for r in b}
    ia = _per_date_ic([r for r in a if key(r) in common])
    ib = _per_date_ic([r for r in b if key(r) in common])
    days = sorted(set(ia) & set(ib))
    independent = _independent_dates(days, horizon_days)
    diffs = [ia[d]["ic"] - ib[d]["ic"] for d in independent]
    stats = _mean_t(diffs)
    return {"engine": engine, "baseline": baseline, "horizon_days": horizon_days, "since": since,
            "n_paired_calls": len(common), "n_independent_dates": len(independent),
            "mean_ic_difference": stats["mean"], "t": stats["t"], "ci95": stats["ci95"]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--engine", default="elite_v3_shadow")
    p.add_argument("--strategy", default=None)
    p.add_argument("--horizon", type=int, default=63)
    p.add_argument("--since", default=REGISTERED_ON)
    p.add_argument("--compare", default=None, help="baseline engine for the paired secondary test")
    a = p.parse_args(argv)
    ledger = SignalLedger()
    out = {"primary": prospective_ic(ledger, a.engine, a.horizon, a.since, a.strategy)}
    if a.compare:
        out["secondary"] = paired_ic_difference(ledger, a.engine, a.compare, a.horizon, a.since)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


def evidence_clock(rows: Sequence[Dict[str, Any]], model_fp: str,
                   horizon_days: Optional[int] = None) -> Dict[str, Any]:
    """How far the current model version is from the pre-registered minimum sample.

    Counts the rows recorded under ``model_fp`` and, of those resolved at
    ``horizon_days`` (the engine's most-recorded horizon when not given), the
    resolved calls and the publication dates whose horizons do not overlap.
    Descriptive only: it says when a verdict becomes possible, not what it is.
    """
    mine = [r for r in rows if fingerprint_of(r) == model_fp]
    if horizon_days is None:
        counts: Dict[int, int] = defaultdict(int)
        for r in mine:
            counts[int(r.get("horizon_days") or 0)] += 1
        horizon_days = max(counts, key=lambda h: (counts[h], h)) if counts else None
    resolved = [r for r in mine if r.get("outcome")
                and (horizon_days is None or int(r.get("horizon_days") or 0) == horizon_days)]
    dates = _independent_dates(sorted({str(r.get("as_of_date")) for r in resolved}), horizon_days)
    return {
        "model_fingerprint": model_fp,
        "horizon_days": horizon_days,
        "recorded": len(mine),
        "resolved": len(resolved),
        "independent_dates": len(dates),
        "min_calls": MIN_SAMPLE,
        "min_independent_dates": _EVIDENCE.MIN_DATES,
        "ready": len(resolved) >= MIN_SAMPLE and len(dates) >= _EVIDENCE.MIN_DATES,
    }
