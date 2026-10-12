"""One shared, measurable record of every call the system makes.

Replaces the two per-engine ``SignalTracker`` JSON files, which had four defects
that made their "track record" meaningless:

* the N-day outcome was scored against whatever the price was *when the updater
  happened to run*, not the price N sessions after the signal (and the 10-day
  outcome reused the 5-day price);
* bearish calls were collapsed to HOLD before being recorded, so sell accuracy
  was never measured;
* the file path was relative to the working directory and the test suite wrote
  into the production file (498 of its 500 rows were golden-fixture runs);
* nothing was benchmarked, so a 60% "win rate" in a rising market meant nothing.

Design
------
* SQLite, one immutable call per (engine, ticker, horizon, as-of session).
  Re-running the same scan cannot rewrite the original forecast.
* Outcomes are resolved from daily history: entry is the first session open
  after publication, exit is the close of the N-th session starting at that
  entry, and the benchmark (SPY by default) uses the same dates and prices.
  The path between them decides whether the stop or target was touched first.
* Everything is scored on **excess return vs the benchmark**. A bullish call
  "works" if it beat SPY over its horizon; a bearish call works if it lagged.
* The database path never resolves to the production file under pytest.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from engine.market_calendar import add_trading_days, last_completed_session
from engine.thresholds import EVIDENCE as _EVIDENCE

logger = logging.getLogger(__name__)

LEDGER_ENV = "QUANTA_LEDGER_PATH"
DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ledger" / "signals.sqlite3"
BENCHMARK = "SPY"
# A neutral call is "right" if the name stayed within this band of the benchmark.
NEUTRAL_BAND_PCT = 2.0
# Below this many resolved calls a group's statistics are shown but flagged.
MIN_SAMPLE = _EVIDENCE.MIN_SAMPLE

# price_loader(ticker, start_date, end_date) -> DataFrame indexed by date with
# Open, High, Low, Close. Split/dividend-adjusted OHLC expected.
PriceLoader = Callable[[str, _date, _date], Any]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key             TEXT NOT NULL UNIQUE,
    engine                 TEXT NOT NULL,
    ticker                 TEXT NOT NULL,
    as_of_date             TEXT NOT NULL,
    recorded_at            TEXT NOT NULL,
    horizon_days           INTEGER NOT NULL,
    horizon_key            TEXT,
    stance                 TEXT NOT NULL,
    action                 TEXT NOT NULL,
    label                  TEXT,
    evidence_status        TEXT,
    score                  REAL,
    factors_json           TEXT,
    context_json           TEXT,
    reference_price        REAL,
    target_price           REAL,
    stop_price             REAL,
    benchmark              TEXT NOT NULL,
    entry_date             TEXT,
    entry_price            REAL,
    benchmark_entry_price  REAL,
    entry_basis            TEXT,
    exit_date              TEXT,
    entry_close            REAL,
    exit_close             REAL,
    benchmark_entry_close  REAL,
    benchmark_exit_close   REAL,
    return_pct             REAL,
    benchmark_return_pct   REAL,
    excess_return_pct      REAL,
    first_touch            TEXT,
    outcome                TEXT,
    resolved_at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_pending ON signals(outcome, as_of_date);
CREATE INDEX IF NOT EXISTS ix_signals_group ON signals(engine, horizon_days, stance);
"""


def _under_pytest() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def resolve_path(path: Optional[os.PathLike] = None) -> Path:
    """Where the ledger lives. Tests can never reach the production file."""
    if path:
        chosen = Path(path)
        if _under_pytest() and chosen.resolve() == DEFAULT_PATH.resolve():
            raise ValueError("Tests cannot write the production signal ledger")
        return chosen
    override = os.environ.get(LEDGER_ENV)
    if override:
        return resolve_path(override)
    if _under_pytest():
        tmp = Path(tempfile.gettempdir()) / f"quanta-test-ledger-{os.getpid()}.sqlite3"
        logger.debug("pytest detected; using throwaway ledger %s", tmp)
        return tmp
    return DEFAULT_PATH


def _as_date(value: Any) -> Optional[_date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _finite(x: Any) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass
class GroupStats:
    """Performance of one group of resolved calls, benchmarked."""
    key: Dict[str, Any]
    n_resolved: int
    n_pending: int
    hit_rate: Optional[float]            # share of calls that beat (bull) / lagged (bear) the benchmark
    mean_return_pct: Optional[float]
    mean_excess_pct: Optional[float]     # signed so that positive = the call was right
    median_excess_pct: Optional[float]
    ci95_excess_pct: Optional[tuple]
    t_stat: Optional[float]              # on per-date mean excess (same-day calls are not independent)
    n_dates: int
    target_first_rate: Optional[float]
    stop_first_rate: Optional[float]
    sufficient: bool
    verdict: str
    # Benchmarks the group's calls were actually scored against ("SPY" for US
    # listings, "^NSEI" for NSE names). More than one means a mixed-market group.
    benchmarks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        if d["ci95_excess_pct"] is not None:
            d["ci95_excess_pct"] = list(d["ci95_excess_pct"])
        return d


class SignalLedger:
    """Thread-safe SQLite ledger. Cheap to construct; one connection per call."""

    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)
            # Existing ledgers from the earlier as-of-close protocol retain
            # their rows, but new measurements use next-session opens.
            columns = {r["name"] for r in con.execute("PRAGMA table_info(signals)")}
            for name, kind in (("entry_date", "TEXT"), ("entry_price", "REAL"),
                               ("benchmark_entry_price", "REAL"), ("entry_basis", "TEXT")):
                if name not in columns:
                    con.execute(f"ALTER TABLE signals ADD COLUMN {name} {kind}")
            con.execute("UPDATE signals SET entry_basis='as_of_close_legacy' "
                        "WHERE outcome IS NOT NULL AND entry_basis IS NULL")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=10)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------ record
    def record(
        self,
        *,
        engine: str,
        ticker: str,
        as_of: Any,
        horizon_days: int,
        stance: str,
        action: str,
        label: str = "",
        horizon_key: str = "",
        evidence_status: str = "",
        score: Optional[float] = None,
        factors: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        reference_price: Optional[float] = None,
        target_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        benchmark: str = BENCHMARK,
    ) -> Optional[int]:
        """Record one call. Returns its row id, None if rejected.

        ``as_of`` is the market session whose data produced the call. The
        immutable ``recorded_at`` timestamp determines the earliest possible
        next-session entry. A second
        call for the same engine/ticker/horizon/session returns the first row;
        repeated scans cannot revise a forecast after seeing later data.
        """
        as_of_d = _as_date(as_of)
        ticker = (ticker or "").strip().upper()
        stance = (stance or "").upper()
        if not ticker or as_of_d is None or horizon_days <= 0:
            logger.warning("ledger: rejected record ticker=%r as_of=%r horizon=%r",
                           ticker, as_of, horizon_days)
            return None
        if stance not in {"BULLISH", "NEUTRAL", "BEARISH"}:
            # INVALID / NONE decisions carry no view and are not scored.
            return None
        key = f"{engine}|{ticker}|{int(horizon_days)}|{as_of_d.isoformat()}"
        row = {
            "signal_key": key,
            "engine": engine,
            "ticker": ticker,
            "as_of_date": as_of_d.isoformat(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "horizon_days": int(horizon_days),
            "horizon_key": horizon_key,
            "stance": stance,
            "action": action,
            "label": label,
            "evidence_status": evidence_status,
            "score": _finite(score),
            "factors_json": json.dumps(factors or {}, default=str),
            "context_json": json.dumps(context or {}, default=str),
            "reference_price": _finite(reference_price),
            "target_price": _finite(target_price),
            "stop_price": _finite(stop_price),
            "benchmark": benchmark,
        }
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        sql = f"INSERT OR IGNORE INTO signals ({cols}) VALUES ({marks})"
        with self._lock, self._connect() as con:
            con.execute(sql, row)
            got = con.execute("SELECT id FROM signals WHERE signal_key=?", (key,)).fetchone()
        return int(got["id"]) if got else None

    def record_decision(self, decision: Any, context: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Record an ``engine.decision.Decision``.

        The model version (evidence strategy key) is stored with every call so
        outcomes of different model versions are never pooled into one verdict.
        """
        strategy = getattr(getattr(decision, "evidence", None), "strategy", None)
        context = dict(context or {})
        if strategy:
            context.setdefault("strategy", strategy)
        return self.record(
            engine=decision.engine,
            ticker=decision.ticker,
            as_of=decision.as_of,
            horizon_days=decision.horizon.trading_days,
            horizon_key=decision.horizon.key,
            stance=decision.stance,
            action=decision.action.value,
            label=decision.label,
            evidence_status=decision.evidence.status.value,
            score=decision.score,
            factors=decision.factor_scores,
            context=context,
            reference_price=decision.reference_price,
            target_price=decision.target,
            stop_price=decision.stop,
            benchmark="^NSEI" if decision.ticker.upper().endswith(".NS") else BENCHMARK,
        )

    # ----------------------------------------------------------------- resolve
    def pending(self) -> List[sqlite3.Row]:
        with self._connect() as con:
            return list(con.execute(
                "SELECT * FROM signals WHERE outcome IS NULL ORDER BY as_of_date"))

    def resolve_outcomes(self, price_loader: PriceLoader, today: Optional[_date] = None,
                         max_tickers: Optional[int] = None) -> int:
        """Resolve every pending call whose horizon has elapsed. Returns the count.

        Loads each ticker's history once. A call resolves only when the next
        eligible entry open, N-th exit close, and matching benchmark bars exist.
        """
        rows = self.pending()
        if not rows:
            return 0
        # UTC "today" may still be an unfinished US session. Never use its
        # partial daily bar as the T+N exit by default.
        today = today or last_completed_session()
        by_ticker: Dict[str, List[sqlite3.Row]] = {}
        for r in rows:
            by_ticker.setdefault(r["ticker"], []).append(r)
        tickers = sorted(by_ticker)
        if max_tickers is not None:
            tickers = tickers[:max_tickers]
        bench_cache: Dict[str, Any] = {}
        bench_starts: Dict[str, _date] = {}
        for r in rows:
            bench = r["benchmark"] or BENCHMARK
            day = _as_date(r["as_of_date"])
            bench_starts[bench] = min(bench_starts.get(bench, day), day)
        resolved = 0
        for ticker in tickers:
            recs = by_ticker[ticker]
            start = min(_as_date(r["as_of_date"]) for r in recs)
            try:
                hist = price_loader(ticker, start, today)
            except Exception as exc:  # noqa: BLE001 - one bad ticker must not stop the rest
                logger.warning("ledger: price load failed for %s: %s", ticker, exc)
                continue
            for r in recs:
                bench = r["benchmark"] or BENCHMARK
                if bench not in bench_cache:
                    try:
                        bench_cache[bench] = price_loader(bench, bench_starts[bench], today)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("ledger: benchmark load failed for %s: %s", bench, exc)
                        bench_cache[bench] = None
                outcome = _measure(r, hist, bench_cache[bench], today=today)
                if outcome is None:
                    continue
                resolved += self._write_outcome(r["id"], outcome)
        return resolved

    def _write_outcome(self, row_id: int, o: Dict[str, Any]) -> int:
        o = dict(o, resolved_at=datetime.now(timezone.utc).isoformat(), id=row_id)
        sets = ", ".join(f"{k}=:{k}" for k in o if k != "id")
        with self._lock, self._connect() as con:
            cursor = con.execute(f"UPDATE signals SET {sets} WHERE id=:id AND outcome IS NULL", o)
            return cursor.rowcount

    # ------------------------------------------------------------------- query
    def rows(self, engine: Optional[str] = None, ticker: Optional[str] = None,
             resolved: Optional[bool] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM signals WHERE 1=1", []
        if engine:
            sql += " AND engine=?"
            args.append(engine)
        if ticker:
            sql += " AND ticker=?"
            args.append(ticker.upper())
        if resolved is True:
            sql += " AND outcome IS NOT NULL"
        elif resolved is False:
            sql += " AND outcome IS NULL"
        with self._connect() as con:
            return [dict(r) for r in con.execute(sql + " ORDER BY as_of_date, id", args)]

    def summarize(self, group_by: Sequence[str] = ("engine", "horizon_days", "stance"),
                  engine: Optional[str] = None, ticker: Optional[str] = None,
                  condition: Optional[str] = None) -> List[GroupStats]:
        """Benchmarked performance per group.

        ``group_by`` may also name a key inside the recorded context (for
        example ``"regime"``), which is how the ledger answers "under what
        conditions does this work?".
        """
        all_rows = self.rows(engine=engine, ticker=ticker)
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for r in all_rows:
            ctx = json.loads(r.get("context_json") or "{}")
            if condition and not ctx.get(condition):
                continue
            key = tuple(r.get(g, ctx.get(g)) for g in group_by)
            groups.setdefault(key, []).append(r)
        out: List[GroupStats] = []
        for key, members in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0]))):
            stats = _group_stats(dict(zip(group_by, key)), members)
            stats.benchmarks = sorted({m.get("benchmark") or BENCHMARK for m in members})
            out.append(stats)
        return out


# ----------------------------------------------------------------------------
# Measurement
# ----------------------------------------------------------------------------

def _frame_dates(frame: Any) -> List[_date]:
    return [_as_date(ix) for ix in frame.index]


def _measure(r: Any, hist: Any, bench: Any,
             today: Optional[_date] = None) -> Optional[Dict[str, Any]]:
    """Outcome of one call, or None if its horizon is not yet in the data."""
    if hist is None or len(hist) == 0 or not {"Open", "Close"}.issubset(hist):
        return None
    as_of = _as_date(r["as_of_date"])
    h = int(r["horizon_days"])
    dates = _frame_dates(hist)
    # A signal cannot enter at the close that produced its inputs. Require an
    # actual as-of bar and enter at the first open AFTER publication in New
    # York time. A morning call waits until tomorrow's open, conservatively.
    if as_of not in dates:
        return None
    try:
        published = datetime.fromisoformat(r["recorded_at"])
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        eligible_after = max(as_of, published.astimezone(ZoneInfo("America/New_York")).date())
        i_entry = next(i for i, day in enumerate(dates) if day and day > eligible_after)
    except (TypeError, ValueError, StopIteration):
        return None
    i_exit = i_entry + h - 1
    if i_exit >= len(dates):
        return None                      # horizon has not elapsed in the data yet
    entry_date = dates[i_entry]
    entry = _finite(hist["Open"].iloc[i_entry])
    exit_ = _finite(hist["Close"].iloc[i_exit])
    if not entry or exit_ is None:
        return None
    exit_date = dates[i_exit]
    if exit_date is None or (today is not None and exit_date > today):
        return None
    ret = (exit_ / entry - 1.0) * 100.0

    if bench is None or len(bench) == 0 or not {"Open", "Close"}.issubset(bench):
        return None
    bench_dates = _frame_dates(bench)
    try:
        b_entry = _finite(bench["Open"].iloc[bench_dates.index(entry_date)])
        b_exit = _finite(bench["Close"].iloc[bench_dates.index(exit_date)])
    except ValueError:
        return None
    if not b_entry or b_exit is None:
        return None
    b_ret = (b_exit / b_entry - 1.0) * 100.0
    excess = ret - b_ret

    # Path: which level did price touch first? Same-bar touches count as the
    # stop (conservative: intrabar order is unknown).
    #
    # The stop and target were recorded in the price basis of publication day;
    # the history is split/dividend-ADJUSTED as of today. A split or dividend
    # after publication rescales every earlier bar, so raw levels would be
    # compared with bars on a different basis (a 10:1 split "hits" every stop
    # on day one). Rescale the levels by how the as-of bar itself moved.
    first = "neither"
    stop, target = _finite(r["stop_price"]), _finite(r["target_price"])
    basis = _level_basis(r, hist, dates, as_of)
    if basis is None and (stop or target):
        stop = target = None          # levels cannot be placed on this history
    elif basis is not None:
        stop = stop * basis if stop else stop
        target = target * basis if target else target
    stance = r["stance"]
    if stop or target:
        highs = hist["High"] if "High" in hist else hist["Close"]
        lows = hist["Low"] if "Low" in hist else hist["Close"]
        for i in range(i_entry, i_exit + 1):
            hi, lo = _finite(highs.iloc[i]), _finite(lows.iloc[i])
            if stance == "BEARISH":
                hit_stop = stop is not None and hi is not None and hi >= stop
                hit_tgt = target is not None and lo is not None and lo <= target
            else:
                hit_stop = stop is not None and lo is not None and lo <= stop
                hit_tgt = target is not None and hi is not None and hi >= target
            if hit_stop:
                first = "stop"
                break
            if hit_tgt:
                first = "target"
                break

    basis = excess
    if stance == "BULLISH":
        outcome = "WIN" if basis > 0 else "LOSS"
    elif stance == "BEARISH":
        outcome = "WIN" if basis < 0 else "LOSS"
    else:
        outcome = "CORRECT" if abs(basis) <= NEUTRAL_BAND_PCT else "INCORRECT"

    return {
        "entry_date": entry_date.isoformat(),
        "entry_price": entry,
        "benchmark_entry_price": b_entry,
        "entry_basis": "next_session_open",
        "exit_date": exit_date.isoformat(),
        "exit_close": exit_,
        "benchmark_exit_close": b_exit,
        "return_pct": ret,
        "benchmark_return_pct": b_ret,
        "excess_return_pct": excess,
        "first_touch": first,
        "outcome": outcome,
    }


def _level_basis(r: Any, hist: Any, dates: List[Optional[_date]], as_of: _date) -> Optional[float]:
    """Factor that maps recorded levels onto this (adjusted) history's basis.

    ``reference_price`` was the as-of close when the call was published; the
    same bar in today's adjusted history reveals any later split or dividend
    adjustment. Returns 1.0 when nothing was recorded to compare against,
    and None when the two disagree by more than any adjustment could explain
    (a different instrument or a bad record), so no touch is claimed.
    """
    ref = _finite(r["reference_price"]) if "reference_price" in r.keys() else None
    if not ref:
        return 1.0
    try:
        close = _finite(hist["Close"].iloc[dates.index(as_of)])
    except (ValueError, KeyError):
        return None
    if not close:
        return None
    factor = close / ref
    # Splits are exact ratios; dividends shave a few percent. A reference that
    # was an intraday/after-hours quote can differ from the close by a few
    # percent too, which is noise, not an adjustment.
    if 0.93 <= factor <= 1.07:
        return 1.0 if 0.995 <= factor <= 1.005 else factor
    for split in (2, 3, 4, 5, 8, 10, 15, 20, 25, 50):
        for f in (1.0 / split, float(split)):
            if abs(factor / f - 1.0) <= 0.07:
                return factor
    return None


def _independent_dates(dates: Sequence[str], horizon_days: Optional[int]) -> List[str]:
    """Publication dates whose horizons do not overlap (greedy, earliest first).

    Calls published on consecutive days with a 63-session horizon share almost
    all of their return window; treating each date as an independent sample
    overstated the t-statistic. Sessions are approximated as 7/5 calendar days.
    """
    ordered = sorted(d for d in dates if d)
    if not horizon_days or horizon_days <= 1:
        return ordered
    gap = timedelta(days=int(math.ceil(horizon_days * 7 / 5)))
    kept: List[str] = []
    last: Optional[_date] = None
    for d in ordered:
        day = _as_date(d)
        if day is None:
            continue
        if last is None or day - last >= gap:
            kept.append(d)
            last = day
    return kept


def pending_schedule(r: Dict[str, Any], today: Optional[_date] = None) -> Dict[str, Any]:
    """Where an unresolved call stands, from its OWN publication schedule.

    Entry is the first session after the later of the as-of date and the
    publication date (New York time) — the rule ``_measure`` applies — and the
    exit is the ``horizon_days``-th session from that entry. Holidays other than
    US market holidays are not modelled, so the dates are estimates.

    ``state`` is ``awaiting_maturity`` while the exit session has not completed,
    else ``awaiting_resolution``: the horizon has elapsed but no outcome is
    recorded (the resolve job has not run, or a price/benchmark bar is missing).
    """
    today = today or last_completed_session()
    as_of = _as_date(r["as_of_date"])
    if as_of is None:
        return {"expected_entry": None, "expected_exit": None, "state": "unknown"}
    try:
        published = datetime.fromisoformat(r["recorded_at"])
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        eligible_after = max(as_of, published.astimezone(ZoneInfo("America/New_York")).date())
    except (TypeError, ValueError, KeyError):
        eligible_after = as_of
    entry = add_trading_days(eligible_after + timedelta(days=1), 0)
    exit_ = add_trading_days(entry, max(0, int(r["horizon_days"]) - 1))
    return {
        "expected_entry": entry.isoformat(),
        "expected_exit": exit_.isoformat(),
        "state": "awaiting_maturity" if exit_ > today else "awaiting_resolution",
    }


def _signed_excess(r: Dict[str, Any]) -> Optional[float]:
    """Excess return signed so that positive means the call was right."""
    ex = r.get("excess_return_pct")
    if ex is None:
        return None
    return -ex if r.get("stance") == "BEARISH" else ex


def _group_stats(key: Dict[str, Any], members: List[Dict[str, Any]]) -> GroupStats:
    # Earlier as-of-close results used an impossible entry. Never mix their
    # returns with next-open measurements in summary statistics.
    done = [m for m in members if m.get("outcome")
            and m.get("entry_basis") == "next_session_open"]
    pending = sum(m.get("outcome") is None for m in members)
    n = len(done)
    if n == 0:
        return GroupStats(key, 0, pending, None, None, None, None, None, None, 0,
                          None, None, False, "No comparable next-open outcomes yet")

    directional = key.get("stance") != "NEUTRAL" if "stance" in key else True
    wins = sum(1 for m in done if m["outcome"] in ("WIN", "CORRECT"))
    rets = [m["return_pct"] for m in done if m.get("return_pct") is not None]
    signed = [s for s in (_signed_excess(m) for m in done) if s is not None]

    by_date: Dict[str, List[float]] = {}
    for m in done:
        s = _signed_excess(m)
        if s is not None:
            by_date.setdefault(m["as_of_date"], []).append(s)
    # Overlapping horizons are not independent samples: inference uses only
    # publication dates whose windows do not overlap (per group horizon).
    horizons = {int(m["horizon_days"]) for m in done if m.get("horizon_days")}
    horizon = max(horizons) if horizons else None
    independent = _independent_dates(list(by_date), horizon)
    date_means = [sum(by_date[d]) / len(by_date[d]) for d in independent]

    mean_ex = sum(signed) / len(signed) if signed else None
    ordered = sorted(signed)
    median_ex = (ordered[len(ordered) // 2] if len(ordered) % 2
                 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2) if signed else None
    t_stat = ci = None
    if len(date_means) >= 3:
        mu = sum(date_means) / len(date_means)
        var = sum((x - mu) ** 2 for x in date_means) / (len(date_means) - 1)
        se = math.sqrt(var / len(date_means)) if var > 0 else 0.0
        if se > 0:
            t_stat = mu / se
            ci = (mu - 1.96 * se, mu + 1.96 * se)

    touches = [m.get("first_touch") for m in done if m.get("first_touch")]
    tgt_rate = (sum(1 for t in touches if t == "target") / len(touches)) if touches else None
    stop_rate = (sum(1 for t in touches if t == "stop") / len(touches)) if touches else None

    sufficient = n >= MIN_SAMPLE and len(date_means) >= _EVIDENCE.MIN_DATES
    if not sufficient:
        verdict = (f"Insufficient sample ({n} resolved calls over {len(date_means)} non-overlapping "
                   f"dates; need {MIN_SAMPLE}+ calls and {_EVIDENCE.MIN_DATES}+ dates)")
    elif not directional:
        verdict = "Neutral calls: measured for completeness, not an edge claim"
    elif t_stat is not None and t_stat >= 2.0:
        verdict = "Positive historical excess (t >= 2); exploratory, not a validated edge"
    elif t_stat is not None and t_stat <= -2.0:
        verdict = "Significantly WORSE than the benchmark — the signal is inverted"
    else:
        verdict = "No significant difference from the benchmark"

    return GroupStats(
        key=key,
        n_resolved=n,
        n_pending=pending,
        hit_rate=wins / n,
        mean_return_pct=(sum(rets) / len(rets)) if rets else None,
        mean_excess_pct=mean_ex,
        median_excess_pct=median_ex,
        ci95_excess_pct=ci,
        t_stat=t_stat,
        n_dates=len(date_means),
        target_first_rate=tgt_rate,
        stop_first_rate=stop_rate,
        sufficient=sufficient,
        verdict=verdict,
    )


def yfinance_loader(ticker: str, start: _date, end: _date):
    """Default price loader: adjusted daily bars from yfinance."""
    import pandas as pd
    import yfinance as yf

    frame = yf.download(ticker, start=start.isoformat(),
                        end=(pd.Timestamp(end) + pd.Timedelta(days=1)).date().isoformat(),
                        auto_adjust=True, progress=False, threads=False)
    if frame is None or frame.empty:
        return frame
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    return frame


_DEFAULT: Optional[SignalLedger] = None
_DEFAULT_LOCK = threading.Lock()


def default_ledger() -> SignalLedger:
    """Process-wide ledger at the resolved path (re-resolved if the env changes)."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        want = resolve_path()
        if _DEFAULT is None or _DEFAULT.path != want:
            _DEFAULT = SignalLedger(want)
        return _DEFAULT


def record_decisions(decisions: Iterable[Any], context: Optional[Dict[str, Any]] = None,
                     ledger: Optional[SignalLedger] = None) -> int:
    """Record published decisions only from completed, fresh market bars.

    A recommendation may be displayed with an intraday or stale quote, but it
    cannot enter a daily T+N study unless its as-of date is a completed session.
    """
    if os.environ.get("QUANTA_TRACKING_DISABLED") == "1":
        return 0
    n = 0
    try:
        ledger = ledger or default_ledger()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ledger unavailable: %s", exc)
        return 0
    for d in decisions:
        try:
            day = _as_date(getattr(d, "as_of", None))
            if (day is None or day > last_completed_session()
                    or getattr(getattr(d, "freshness", None), "status", None) != "fresh"):
                continue
            if ledger.record_decision(d, context=context) is not None:
                n += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("ledger: failed to record %s: %s", getattr(d, "ticker", "?"), exc)
    return n
