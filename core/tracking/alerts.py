"""Stop / target alerts for open calls in the signal ledger.

The ledger scores a call once, at the close of its horizon. Between publication
and that exit nothing told the owner that a stop or target had traded, which is
the moment a paper (or real) position needs attention. This module scans the
daily bars of every unresolved call from its expected entry session and reports
the first session each level was touched.

It is informational only: it never writes to the ``signals`` table, so an alert
cannot change a measured outcome. Each (call, level) pair is announced once;
the record of what was announced lives in its own ``level_alerts`` table in the
same file.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date as _date
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.market_calendar import last_completed_session

from .ledger import PriceLoader, SignalLedger, _as_date, _frame_dates, pending_schedule

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS level_alerts (
    signal_key   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    session      TEXT NOT NULL,
    level        REAL NOT NULL,
    announced_at TEXT NOT NULL,
    PRIMARY KEY (signal_key, kind)
);
"""


@dataclass(frozen=True)
class LevelAlert:
    signal_key: str
    engine: str
    ticker: str
    stance: str
    kind: str          # "TARGET" or "STOP"
    level: float
    session: str       # first session whose range touched the level
    as_of_date: str

    def message(self) -> str:
        verb = "reached its target" if self.kind == "TARGET" else "hit its stop"
        return (f"{self.ticker} {verb} {self.level:,.2f} on {self.session} "
                f"({self.engine} call of {self.as_of_date}, {self.stance.lower()})")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _first_touch(bars: Any, start: _date, end: Optional[_date], level: float, *,
                 above: bool) -> Optional[str]:
    """First session in [start, end] whose high (above) or low (below) reaches ``level``."""
    column = "High" if above else "Low"
    for day, value in zip(_frame_dates(bars), bars[column]):
        if day is None or day < start or value != value:  # NaN guard
            continue
        if end is not None and day > end:
            break  # after the horizon the position is closed; a later touch means nothing
        if (value >= level) if above else (value <= level):
            return day.isoformat()
    return None


def check_levels(ledger: SignalLedger, price_loader: PriceLoader,
                 today: Optional[_date] = None, announce: bool = True) -> List[LevelAlert]:
    """New stop/target touches for every unresolved directional call.

    ``announce=False`` reports without remembering, so a dry run can be repeated.
    """
    today = today or last_completed_session()
    with ledger._connect() as con:
        con.executescript(_SCHEMA)
        seen = {(r["signal_key"], r["kind"]) for r in con.execute(
            "SELECT signal_key, kind FROM level_alerts")}

    by_ticker: Dict[str, List[Any]] = {}
    for r in ledger.pending():
        if r["stance"] in {"BULLISH", "BEARISH"} and (r["stop_price"] or r["target_price"]):
            by_ticker.setdefault(r["ticker"], []).append(r)

    found: List[LevelAlert] = []
    for ticker, rows in sorted(by_ticker.items()):
        schedules = {r["id"]: pending_schedule(dict(r), today) for r in rows}
        entries = {k: _as_date(v["expected_entry"]) for k, v in schedules.items()}
        start = min(d for d in entries.values() if d is not None) if any(entries.values()) else None
        if start is None or start > today:
            continue  # nothing has traded since publication yet
        try:
            bars = price_loader(ticker, start, today)
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not stop the rest
            logger.warning("alerts: price load failed for %s: %s", ticker, exc)
            continue
        if bars is None or getattr(bars, "empty", True):
            continue
        for r in rows:
            entry = entries[r["id"]]
            if entry is None:
                continue
            bullish = r["stance"] == "BULLISH"
            for kind, level, above in (("TARGET", r["target_price"], bullish),
                                       ("STOP", r["stop_price"], not bullish)):
                if not level or (r["signal_key"], kind) in seen:
                    continue
                session = _first_touch(bars, entry, _as_date(schedules[r["id"]]["expected_exit"]),
                                       float(level), above=above)
                if session:
                    found.append(LevelAlert(r["signal_key"], r["engine"], ticker, r["stance"],
                                            kind, float(level), session, r["as_of_date"]))

    if announce and found:
        now = datetime.now(timezone.utc).isoformat()
        with ledger._lock, ledger._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO level_alerts VALUES (?, ?, ?, ?, ?)",
                [(a.signal_key, a.kind, a.session, a.level, now) for a in found])
    return found


def recent_alerts(ledger: SignalLedger, limit: int = 25,
                  engine: Optional[str] = None) -> List[Dict[str, Any]]:
    """Announced alerts, newest touch first, joined to the call they belong to."""
    with ledger._connect() as con:
        con.executescript(_SCHEMA)
        rows = con.execute(
            "SELECT a.kind, a.session, a.level, a.announced_at, s.engine, s.ticker, s.stance, "
            "s.as_of_date, s.label FROM level_alerts a JOIN signals s ON s.signal_key = a.signal_key "
            "WHERE (? IS NULL OR s.engine = ?) "
            "ORDER BY a.session DESC, a.announced_at DESC LIMIT ?", (engine, engine, int(limit)))
        return [dict(r) for r in rows]


def notify_desktop(alerts: List[LevelAlert]) -> bool:
    """Best-effort macOS notification summarising ``alerts``. False elsewhere."""
    if not alerts or sys.platform != "darwin":
        return False
    title = f"QUANTA: {len(alerts)} level alert{'s' if len(alerts) != 1 else ''}"
    body = "; ".join(a.message() for a in alerts[:3])
    if len(alerts) > 3:
        body += f"; +{len(alerts) - 3} more"
    script = f"display notification {_osa(body)} with title {_osa(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=10,
                       capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("alerts: desktop notification failed: %s", exc)
        return False


def _osa(text: str) -> str:
    """AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
