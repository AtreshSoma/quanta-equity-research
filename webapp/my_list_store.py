"""Storage for My list and price alerts: one SQLite file, no web framework.

Shared by the web app (``my_list.py`` serves it and runs the checker) and the
MCP server (its My list tools), so both read and write the same
``data/my_list.sqlite3``.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "my_list.sqlite3"
PATH_ENV = "QUANTA_MYLIST_PATH"
SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.=&^-]{0,24}$")
MAX_SYMBOLS = 100
MAX_ACTIVE_ALERTS = 100
DEFAULT_EXPIRY_DAYS = 90
LABEL_MAX = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS my_list (
    symbol     TEXT PRIMARY KEY,
    added_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    level           REAL NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'manual',
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    triggered_at    TEXT,
    triggered_price REAL,
    notified        INTEGER NOT NULL DEFAULT 0,
    dismissed_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_open ON price_alerts(triggered_at, expires_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def clean_symbol(raw: Any) -> Optional[str]:
    sym = str(raw or "").strip().upper()
    return sym if SYMBOL_RE.fullmatch(sym) else None


def resolve_path(path: Optional[os.PathLike] = None) -> Path:
    """Where the store lives. Tests never reach the real file."""
    if path:
        return Path(path)
    override = os.environ.get(PATH_ENV)
    if override:
        return Path(override)
    if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
        return Path(tempfile.gettempdir()) / f"quanta-test-mylist-{os.getpid()}.sqlite3"
    return DEFAULT_PATH


class MyListStore:
    """Thread-safe SQLite store; one connection per call."""

    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=10)
        con.row_factory = sqlite3.Row
        return con

    # ---- my list
    def symbols(self) -> List[Dict[str, Any]]:
        with self._connect() as con:
            return [dict(r) for r in con.execute("SELECT symbol, added_at FROM my_list ORDER BY added_at")]

    def add_symbol(self, symbol: str) -> bool:
        with self._lock, self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM my_list").fetchone()[0]
            if n >= MAX_SYMBOLS:
                raise ValueError(f"My list holds at most {MAX_SYMBOLS} symbols.")
            cur = con.execute("INSERT OR IGNORE INTO my_list(symbol, added_at) VALUES (?, ?)",
                              (symbol, _iso(_now())))
            return cur.rowcount == 1

    def remove_symbol(self, symbol: str) -> bool:
        with self._lock, self._connect() as con:
            return con.execute("DELETE FROM my_list WHERE symbol=?", (symbol,)).rowcount == 1

    # ---- alerts
    def alerts(self, active_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM price_alerts"
        args: tuple = ()
        if active_only:
            sql += " WHERE triggered_at IS NULL AND expires_at > ?"
            args = (_iso(_now()),)
        with self._connect() as con:
            return [dict(r) for r in con.execute(sql + " ORDER BY id DESC", args)]

    def add_alert(self, symbol: str, direction: str, level: float, label: str = "",
                  source: str = "manual", expiry_days: int = DEFAULT_EXPIRY_DAYS) -> int:
        if direction not in ("above", "below"):
            raise ValueError("direction must be 'above' or 'below'")
        if not (level > 0) or level != level or level > 1e9:
            raise ValueError("level must be a positive price")
        now = _now()
        with self._lock, self._connect() as con:
            active = con.execute("SELECT COUNT(*) FROM price_alerts WHERE triggered_at IS NULL AND expires_at > ?",
                                 (_iso(now),)).fetchone()[0]
            if active >= MAX_ACTIVE_ALERTS:
                raise ValueError(f"At most {MAX_ACTIVE_ALERTS} alerts can be active.")
            # The same level on the same symbol is one alert, not two.
            dup = con.execute("SELECT id FROM price_alerts WHERE symbol=? AND direction=? AND ABS(level-?) < 1e-9 "
                              "AND triggered_at IS NULL AND expires_at > ?",
                              (symbol, direction, level, _iso(now))).fetchone()
            if dup:
                return int(dup[0])
            cur = con.execute(
                "INSERT INTO price_alerts(symbol, direction, level, label, source, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol, direction, float(level), str(label or "")[:LABEL_MAX],
                 "decision" if source == "decision" else "manual",
                 _iso(now), _iso(now + timedelta(days=max(1, min(366, int(expiry_days)))))))
            return int(cur.lastrowid)

    def delete_alert(self, alert_id: int) -> bool:
        with self._lock, self._connect() as con:
            return con.execute("DELETE FROM price_alerts WHERE id=?", (int(alert_id),)).rowcount == 1

    def dismiss(self, alert_id: Optional[int] = None) -> int:
        """Dismiss one fired alert, or every fired one when ``alert_id`` is None."""
        with self._lock, self._connect() as con:
            if alert_id is None:
                return con.execute("UPDATE price_alerts SET dismissed_at=? WHERE triggered_at IS NOT NULL "
                                   "AND dismissed_at IS NULL", (_iso(_now()),)).rowcount
            return con.execute("UPDATE price_alerts SET dismissed_at=? WHERE id=? AND triggered_at IS NOT NULL",
                               (_iso(_now()), int(alert_id))).rowcount

    def inbox(self) -> List[Dict[str, Any]]:
        with self._connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM price_alerts WHERE triggered_at IS NOT NULL AND dismissed_at IS NULL "
                "ORDER BY triggered_at DESC")]

    def claim(self, alert_id: int, price: float) -> bool:
        """Mark an alert fired. True only for the one caller that fired it."""
        with self._lock, self._connect() as con:
            cur = con.execute("UPDATE price_alerts SET triggered_at=?, triggered_price=? "
                              "WHERE id=? AND triggered_at IS NULL", (_iso(_now()), float(price), int(alert_id)))
            return cur.rowcount == 1

    def mark_notified(self, alert_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute("UPDATE price_alerts SET notified=1 WHERE id=?", (int(alert_id),))
