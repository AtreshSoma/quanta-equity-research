"""Every name a Picks scan scored, kept per session, and the test it makes possible.

The ledger records the calls a scan publishes: one to five names a day. A rank
test needs the whole cross-section scored on the same day, so with the ledger
alone "does the Picks score rank stocks better than plain momentum?" would take
years to answer. A Picks scan already scores about 250 names. This keeps that
list, once per session, written when the scan finishes and never rewritten:

    data/score_snapshots/<market>/<session>__<scan_type>.csv
    columns: ticker, score (scanner 0-10), outcome (reached_book or the rule that stopped it)

``evaluate`` then reads the realised prices and asks, per snapshot date, the
same question the backtests ask (apex_quant/factor_baselines.py): the Spearman
correlation of the score with the forward return, and of 12-1 momentum computed
from prices before that date, with their difference tested across dates. The
first scan of a session is the one kept, so nothing written after the fact can
enter the sample. Under ``EVIDENCE.MIN_DATES`` non-overlapping dates no verdict
is given.

    python -m tracking.score_snapshots              # report (needs price data)
    python -m tracking.score_snapshots --json

Set QUANTA_SNAPSHOT_DIR to keep the files elsewhere; QUANTA_TRACKING_DISABLED=1
records nothing (tests, scratch servers).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DIR_ENV = "QUANTA_SNAPSHOT_DIR"
HORIZONS = (21, 63)
MIN_NAMES = 10                 # fewer names priced on a date: that date is not used
MOMENTUM_LOOKBACK_DAYS = 365   # 12-1, as apex_quant/factor_baselines.py and engine/factor_baseline.py
MOMENTUM_SKIP_DAYS = 30
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")
FIELDS = ("ticker", "score", "outcome")


def snapshot_dir() -> Path:
    return Path(os.environ.get(DIR_ENV) or (ROOT / "data" / "score_snapshots"))


def _file(market: str, session: str, scan_type: str, base: Optional[Path] = None) -> Path:
    for part in (market, session, scan_type):
        if not _SAFE.match(str(part or "")):
            raise ValueError(f"unsafe snapshot name part: {part!r}")
    return (base or snapshot_dir()) / str(market).upper() / f"{session}__{scan_type}.csv"


def record_snapshot(market: str, session: Optional[str], scan_type: str,
                    rows: Iterable[Dict[str, Any]], base: Optional[Path] = None) -> Optional[Path]:
    """Write one session's scored names, unless that session already has a file.

    Returns the path written, or None when nothing was written (tracking off, no
    session, no rows, or the session already recorded).
    """
    if os.environ.get("QUANTA_TRACKING_DISABLED") == "1" or not session:
        return None
    clean = []
    seen = set()
    for r in rows:
        t = str(r.get("ticker") or "").strip().upper()
        try:
            s = float(r.get("score"))
        except (TypeError, ValueError):
            continue
        if not t or t in seen or not math.isfinite(s):
            continue
        seen.add(t)
        clean.append({"ticker": t, "score": round(s, 4), "outcome": str(r.get("outcome") or "")[:40]})
    if not clean:
        return None
    path = _file(market, str(session)[:10], scan_type, base)
    if path.exists():
        return None                       # the first scan of the session is the one kept
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(clean, key=lambda x: x["ticker"]))
    tmp.replace(path)
    return path


def load_snapshots(market: str = "US", scan_type: Optional[str] = None,
                   base: Optional[Path] = None) -> Dict[str, pd.DataFrame]:
    """{session: frame(ticker, score, outcome)} for one market, oldest first."""
    folder = (base or snapshot_dir()) / str(market).upper()
    out: Dict[str, pd.DataFrame] = {}
    for p in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
        session, _, kind = p.stem.partition("__")
        if scan_type and kind != scan_type:
            continue
        frame = pd.read_csv(p, dtype={"ticker": str, "outcome": str})
        if session in out:              # two scan types on one session: keep the first file
            continue
        out[session] = frame
    return out


# ── Evaluation ───────────────────────────────────────────────────────────────

def _spearman(a: Sequence[float], b: Sequence[float]) -> float:
    s = pd.Series(a, dtype=float).rank()
    t = pd.Series(b, dtype=float).rank()
    if s.std() == 0 or t.std() == 0:
        return float("nan")
    return float(np.corrcoef(s, t)[0, 1])


def _momentum(close: pd.Series, on: pd.Timestamp) -> float:
    """12-1 return at ``on`` from closes at or before it only."""
    past = close[close.index <= on]
    if past.empty:
        return float("nan")
    first = past.index[0]
    m1_at, m12_at = on - pd.Timedelta(days=MOMENTUM_SKIP_DAYS), on - pd.Timedelta(days=MOMENTUM_LOOKBACK_DAYS)
    if m12_at < first:
        return float("nan")
    m1, m12 = past.asof(m1_at), past.asof(m12_at)
    return float(m1 / m12 - 1.0) if m12 and m12 > 0 else float("nan")


def _forward(close: pd.Series, on: pd.Timestamp, sessions: int) -> float:
    """Close-to-close return from the session ``on`` over the next ``sessions`` bars."""
    idx = close.index
    pos = idx.searchsorted(on, side="right") - 1
    if pos < 0 or idx[pos].normalize() != on.normalize() or pos + sessions >= len(idx):
        return float("nan")
    a, b = float(close.iloc[pos]), float(close.iloc[pos + sessions])
    return b / a - 1.0 if a > 0 else float("nan")


@dataclass
class HorizonResult:
    horizon: int
    dates: int                       # snapshot dates with enough priced names
    independent_dates: int           # of those, non-overlapping at this horizon
    mean_ic_score: Optional[float]
    mean_ic_momentum: Optional[float]
    difference: Optional[Dict[str, Any]]   # score IC minus momentum IC, paired by date
    verdict: str


@dataclass
class SnapshotReport:
    market: str
    snapshots: int
    first: Optional[str]
    last: Optional[str]
    names_per_snapshot: Optional[float]
    min_dates: int
    horizons: List[HorizonResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _independent(dates: Sequence[str], horizon: int) -> int:
    gap = timedelta(days=int(math.ceil(horizon * 7 / 5)))
    kept, last = 0, None
    for d in sorted(dates):
        day = _date.fromisoformat(d)
        if last is None or day - last >= gap:
            kept, last = kept + 1, day
    return kept


def evaluate(snapshots: Dict[str, pd.DataFrame], loader: Callable[[str, _date, _date], pd.DataFrame],
             market: str = "US", horizons: Sequence[int] = HORIZONS, min_dates: Optional[int] = None,
             today: Optional[_date] = None) -> SnapshotReport:
    """Per-date rank IC of the scan score and of 12-1 momentum against forward returns."""
    if min_dates is None:
        try:
            from engine.thresholds import EVIDENCE
            min_dates = int(EVIDENCE.MIN_DATES)
        except ImportError:
            min_dates = 10
    sessions = sorted(snapshots)
    report = SnapshotReport(market, len(sessions), sessions[0] if sessions else None,
                            sessions[-1] if sessions else None,
                            float(np.mean([len(f) for f in snapshots.values()])) if snapshots else None,
                            int(min_dates))
    if not sessions:
        report.horizons = [HorizonResult(h, 0, 0, None, None, None, "no snapshots yet") for h in horizons]
        return report
    start = _date.fromisoformat(sessions[0]) - timedelta(days=MOMENTUM_LOOKBACK_DAYS + 10)
    end = today or _date.today()
    closes: Dict[str, pd.Series] = {}
    for t in sorted({t for f in snapshots.values() for t in f["ticker"]}):
        try:
            frame = loader(t, start, end)
        except Exception:  # noqa: BLE001 - an unpriced name simply drops out
            continue
        if frame is None or getattr(frame, "empty", True) or "Close" not in frame:
            continue
        c = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        c.index = pd.DatetimeIndex(c.index).tz_localize(None) if getattr(c.index, "tz", None) else pd.DatetimeIndex(c.index)
        closes[t] = c.sort_index()

    from apex_quant import stats as S
    for h in horizons:
        ic_s, ic_m, used = [], [], []
        for d in sessions:
            on = pd.Timestamp(d)
            rows = []
            for t, sc in zip(snapshots[d]["ticker"], snapshots[d]["score"]):
                c = closes.get(t)
                if c is None:
                    continue
                fwd = _forward(c, on, h)
                mom = _momentum(c, on)
                if math.isfinite(fwd) and math.isfinite(mom) and math.isfinite(float(sc)):
                    rows.append((float(sc), mom, fwd))
            if len(rows) < MIN_NAMES:
                continue
            a, m, f = zip(*rows)
            s_ic, m_ic = _spearman(a, f), _spearman(m, f)
            if math.isfinite(s_ic) and math.isfinite(m_ic):
                ic_s.append(s_ic)
                ic_m.append(m_ic)
                used.append(d)
        indep = _independent(used, h) if used else 0
        diff = S.infer_mean(np.array(ic_s) - np.array(ic_m), lags=h) if len(used) >= 5 else None
        if not used:
            verdict = "no snapshot has reached this horizon yet"
        elif indep < min_dates:
            verdict = f"not enough data: {indep} of {min_dates} independent dates"
        elif diff is not None and diff.t_newey_west is not None and diff.t_newey_west >= 2.0:
            verdict = "the scan score ranks better than 12-1 momentum"
        elif diff is not None and diff.t_newey_west is not None and diff.t_newey_west <= -2.0:
            verdict = "12-1 momentum ranks better than the scan score"
        else:
            verdict = "no significant difference from 12-1 momentum"
        report.horizons.append(HorizonResult(
            h, len(used), indep,
            float(np.mean(ic_s)) if ic_s else None, float(np.mean(ic_m)) if ic_m else None,
            diff.to_dict() if diff else None, verdict))
    return report


def format_report(r: SnapshotReport) -> str:
    lines = [f"Picks score snapshots, {r.market}: {r.snapshots} session(s)"
             + (f", {r.first} to {r.last}, about {r.names_per_snapshot:.0f} names each" if r.snapshots else "")]
    def num(x: Optional[float]) -> str:
        return "n/a" if x is None else f"{x:+.3f}"

    for h in r.horizons:
        d = h.difference or {}
        diff = "n/a" if d.get("mean") is None else f"{d['mean']:+.3f} (t {float(d.get('t_newey_west') or 0):+.2f})"
        lines.append(
            f"  {h.horizon:>2} sessions: {h.dates} dates ({h.independent_dates} independent); "
            f"IC score {num(h.mean_ic_score)}, momentum {num(h.mean_ic_momentum)}, "
            f"difference {diff}; {h.verdict}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracking.score_snapshots")
    ap.add_argument("--market", default="US")
    ap.add_argument("--scan-type", default="standard",
                    help="snapshots of this scan type (default standard, the daily job's scan; 'all' mixes types)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from .ledger import yfinance_loader
    kind = None if args.scan_type == "all" else args.scan_type
    report = evaluate(load_snapshots(args.market, kind), yfinance_loader, args.market)
    print(json.dumps(report.to_dict(), indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
