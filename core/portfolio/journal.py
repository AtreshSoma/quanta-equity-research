"""The decision journal: a dated record of what this page told the user.

It exists so recommendation outcomes can be measured honestly later. It records
calls from the day it is switched on and NEVER backfills: a past call cannot be
reconstructed with the information available on that day, so none is invented.

What is stored (on this computer only, in one JSON file): the session date, the
ticker, the guidance action and QUANTA score, the rule that fired, and the market
price then. What is NOT stored: quantities, dollar amounts, cost basis, weights or
any account identifier.

Recording rule (so repeat sightings do not inflate the sample): a call is written
when a ticker first appears, when its action changes, and again when the last
entry for that ticker is at least :data:`REAFFIRM_DAYS` old.

A call whose rule is an analysis limitation (missing evidence, account data) is
not a view and is never written, so the outcome record cannot score a data gap
as a call (audit 2026-10-21, F10). Actions are compared under the current
vocabulary (:func:`portfolio.guidance.canonical_action`): an entry written as
"Hold / No New Money" and a Hold today are the same call.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REAFFIRM_DAYS = 21
# Rule kinds that describe a gap in the analysis, not a view on the stock.
_LIMITATION_KINDS = ("missing_evidence", "account_data")


def _canonical(action, basis_code=""):
    from .guidance import canonical_action
    return canonical_action(action, basis_code)
MAX_ENTRIES = 5000
SCHEMA = 1


def default_path() -> Path:
    env = os.getenv("QUANTA_JOURNAL_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "webapp" / "data" / "portfolio_journal.json"


class DecisionJournal:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.RLock()

    # -- storage ------------------------------------------------------------

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"schema": SCHEMA, "started": None, "entries": []}

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # -- API ----------------------------------------------------------------

    def entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._read()["entries"])

    def started(self) -> Optional[str]:
        with self._lock:
            return self._read().get("started")

    def record(self, rows: Iterable[Dict[str, Any]], session_date: str,
               now: Optional[datetime] = None) -> int:
        """Append this session's calls. Returns the number of entries written.

        Only holdings with a real call (guidance.status == "ok") and a market price are
        recorded; a "No call" is a data limitation, not a view, and is never journalled.
        """
        now = now or datetime.now(timezone.utc)
        with self._lock:
            data = self._read()
            entries: List[Dict[str, Any]] = data["entries"]
            last: Dict[str, Dict[str, Any]] = {}
            for e in entries:
                last[e["symbol"]] = e
            added = 0
            for r in rows:
                g = r.get("guidance") or {}
                if g.get("status") != "ok" or g.get("action") in (None, "", "Unavailable"):
                    continue
                if (g.get("basis") or {}).get("kind") in _LIMITATION_KINDS:
                    continue
                price = (r.get("broker") or {}).get("last_price")
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                sym = r["symbol"]
                prev = last.get(sym)
                if prev is not None and prev["date"] > session_date:
                    continue                      # the journal only moves forward in time
                if prev is not None:
                    prev_action = _canonical(prev["action"], prev.get("basis_code"))
                    same_action = prev_action == g["action"]
                    age = (date.fromisoformat(session_date) - date.fromisoformat(prev["date"])).days
                    if prev["date"] == session_date and same_action:
                        continue
                    if same_action and age < REAFFIRM_DAYS:
                        continue
                basis = g.get("basis") or {}
                dec = (r.get("analysis") or {}).get("decision") or {}
                entry = {
                    "date": session_date, "recorded_at": now.isoformat(), "symbol": sym,
                    "action": g["action"], "stance": g.get("stance"), "score": dec.get("score"),
                    "basis_code": basis.get("code"), "basis_text": basis.get("text") or g.get("key_reason"),
                    "price": price,
                    "kind": "change" if prev is not None and not same_action else
                            ("reaffirmed" if prev is not None else "first"),
                    "from_action": prev_action if prev is not None and not same_action else None,
                }
                entries.append(entry)
                last[sym] = entry
                added += 1
            if added:
                if not data.get("started"):
                    data["started"] = session_date
                data["entries"] = entries[-MAX_ENTRIES:]
                self._write(data)
            return added

    def last_by_symbol(self) -> Dict[str, Dict[str, Any]]:
        """The latest entry per ticker: {symbol: {date, action, basis_code}}."""
        out: Dict[str, Dict[str, Any]] = {}
        for e in self.entries():
            out[e["symbol"]] = {"date": e.get("date"), "action": e.get("action"),
                                "basis_code": e.get("basis_code")}
        return out

    def changes(self, limit: int = 12) -> List[Dict[str, Any]]:
        """Thesis-change tracking: dated moves from one action to another."""
        ch = [e for e in self.entries() if e.get("kind") == "change"]
        return list(reversed(ch))[:limit]

    def summary(self) -> Dict[str, Any]:
        es = self.entries()
        return {"started": self.started(), "entries": len(es),
                "symbols": len({e["symbol"] for e in es}),
                "stored": "On this computer only. No amounts, quantities or account details are stored.",
                "path_hint": self.path.name}
