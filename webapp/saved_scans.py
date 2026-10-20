"""Saved Picks scans: every completed scan is kept for a day, one per setting.

Switching the scan universe used to replace the single saved scan, so going from
Standard to Growth and back meant running Standard again. This keeps the
results of each distinct setting (market, universe, capital, mandate, position
limit, diversification, filters, custom list) for ``SAVED_SCANS.RETENTION_HOURS``,
newest ``SAVED_SCANS.MAX_SAVED`` per market, so a view can be reopened from disk.

A saved scan is a snapshot: nothing in it is refreshed or re-ranked when it is
reopened, and reopening it never touches the track-record ledger (calls are
recorded when a scan runs, not when one is viewed).

Layout, under the directory the app passes in (``<cache>/last_scan``)::

    recent/<US|IN>/<id>.json.gz     one file per distinct setting
    <US|IN>.json.gz                 the single-scan file this replaced; adopted
                                    into ``recent/`` on first read, then removed

Pure functions over a directory, so the tests need neither Flask nor a clock.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.thresholds import SAVED_SCANS

logger = logging.getLogger("quanta.saved_scans")

_ID = re.compile(r"^[0-9a-f]{12}$")
# Re-entrant: adopting the old single-scan file calls save() from inside a
# listing or load that already holds the lock.
_lock = threading.RLock()


def market_code(market: Any) -> str:
    return "IN" if str(market or "").strip().upper() in ("IN", "INDIA") else "US"


def scan_id(params: Dict[str, Any]) -> str:
    """Stable identity of a scan's setting: 12 hex characters.

    ``safe_mode`` is left out on purpose: a safe-mode retry of the same setting
    is the same view and replaces it. Capital and mandate are in: they change
    every size on the page.
    """
    tickers = sorted(str(t).upper() for t in (params.get("custom_tickers") or []))
    key = json.dumps({
        "market": market_code(params.get("market")),
        "scan_type": params.get("scan_type"),
        "capital": params.get("capital"),
        "risk_profile": params.get("risk_profile"),
        "max_positions": params.get("max_positions"),
        "diversify": bool(params.get("diversify", True)),
        "filters": params.get("filters") or {},
        "custom_tickers": tickers,
    }, sort_keys=True, default=str)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(stamp: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dir(base: Path, market: str) -> Path:
    return Path(base) / "recent" / market_code(market)


def _legacy(base: Path, market: str) -> Path:
    return Path(base) / f"{market_code(market)}.json.gz"


def _write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(gzip.compress(json.dumps(payload, default=str).encode("utf-8")))
    tmp.replace(path)


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - a corrupt file reads as "not saved"
        logger.warning("saved scan %s unreadable (%s); removing it", path.name, type(exc).__name__)
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data if isinstance(data, dict) and isinstance(data.get("result"), dict) else None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _prune(folder: Path, now: datetime) -> None:
    """Drop what is past retention, then everything beyond the newest MAX_SAVED."""
    cutoff = now - timedelta(hours=float(SAVED_SCANS.RETENTION_HOURS))
    kept = []
    for path in folder.glob("*.json.gz"):
        data = _read(path)
        saved = _parse((data or {}).get("saved_at"))
        if data is None or saved is None or saved < cutoff:
            _unlink(path)
        else:
            kept.append((saved, path))
    kept.sort(key=lambda t: t[0], reverse=True)
    for _saved, path in kept[int(SAVED_SCANS.MAX_SAVED):]:
        _unlink(path)


def save(base: Path, params: Dict[str, Any], result: Dict[str, Any],
         now: Optional[datetime] = None, saved_at: Optional[str] = None) -> str:
    """Keep this scan; returns its id. Replaces an earlier scan of the same setting."""
    now = _now(now)
    sid = scan_id(params)
    folder = _dir(base, params.get("market"))
    with _lock:
        if saved_at is None:
            # Strictly newer than every scan already kept: a coarse clock (Windows
            # ticks about every 15 ms) must not leave two saves tied, because
            # "newest" decides which scan opens first.
            newest = _newest_saved(folder)
            stamp = newest + timedelta(microseconds=1) if newest is not None and now <= newest else now
            saved_at = stamp.isoformat()
        payload = {"id": sid, "saved_at": saved_at, "params": params, "result": result}
        _write(folder / f"{sid}.json.gz", payload)
        _prune(folder, now)
    return sid


def _newest_saved(folder: Path) -> Optional[datetime]:
    """When the most recent scan in ``folder`` was saved, or None when it is empty."""
    stamps = [_parse((_read(p) or {}).get("saved_at")) for p in folder.glob("*.json.gz")]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _adopt_legacy(base: Path, market: str, now: datetime) -> None:
    """Carry the single-scan file this replaced into ``recent/``, once."""
    old = _legacy(base, market)
    if not old.exists():
        return
    data = _read(old)
    if data is not None and isinstance(data.get("params"), dict):
        saved = _parse(data.get("saved_at"))
        if saved and saved >= now - timedelta(hours=float(SAVED_SCANS.RETENTION_HOURS)):
            params = dict(data["params"])
            params.setdefault("market", market_code(market))
            save(base, params, data["result"], now=now, saved_at=data.get("saved_at"))
    _unlink(old)


def _closed_since(market: str, saved_at: datetime, now: datetime) -> Optional[Dict[str, Any]]:
    """Whether the US market has closed since this scan's prices were final.

    ``as_of_session`` is the last completed session when the scan was saved;
    ``closed_since`` is true once a later session has completed. Only the US
    calendar is modelled, so India reports ``None`` rather than a guess.
    """
    if market_code(market) != "US":
        return None
    try:
        from engine.market_calendar import last_completed_session, trading_days_between
    except ImportError:
        return None
    then, latest = last_completed_session(saved_at), last_completed_session(now)
    return {"as_of_session": then.isoformat(), "closed_since": latest > then,
            "sessions_since": trading_days_between(then, latest)}


def _summary(data: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    params, result = data.get("params") or {}, data.get("result") or {}
    calls = [str(r.get("call")) for r in (result.get("stock_picks") or [])]
    saved = _parse(data.get("saved_at"))
    scanned = result.get("total_scanned") or (result.get("accounting") or {}).get("universe")
    out = {
        "id": data.get("id") or scan_id(params), "saved_at": data.get("saved_at"),
        "market": market_code(params.get("market")), "scan_type": params.get("scan_type"),
        "capital": params.get("capital"), "risk_profile": params.get("risk_profile"),
        "max_positions": params.get("max_positions"), "diversify": bool(params.get("diversify", True)),
        "has_filters": bool(params.get("filters")), "custom_count": len(params.get("custom_tickers") or []),
        "scanned": scanned, "buy_accumulate": sum(c in ("buy", "accumulate") for c in calls),
        "watchlist": calls.count("watchlist"), "funded": result.get("funded_count"),
        "regime": (result.get("regime") or {}).get("type"),
    }
    if saved:
        out.update(_closed_since(params.get("market"), saved, now) or {})
    return out


def list_entries(base: Path, market: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Summaries of the saved scans for a market, newest first (no results)."""
    now = _now(now)
    with _lock:
        _adopt_legacy(base, market, now)
        folder = _dir(base, market)
        if not folder.exists():
            return []
        _prune(folder, now)
        entries = []
        for path in folder.glob("*.json.gz"):
            data = _read(path)
            if data is not None:
                entries.append(_summary(data, now))
    entries.sort(key=lambda e: e.get("saved_at") or "", reverse=True)
    return entries


def load(base: Path, market: str, sid: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """One saved scan in full (``id``, ``saved_at``, ``params``, ``result`` and
    the ``closed_since`` note), or ``None`` when it is unknown or past retention."""
    if not _ID.match(str(sid or "")):
        return None          # never builds a path from anything but 12 hex characters
    now = _now(now)
    with _lock:
        _adopt_legacy(base, market, now)
        folder = _dir(base, market)
        if folder.exists():
            _prune(folder, now)
        data = _read(folder / f"{sid}.json.gz")
    if data is None:
        return None
    return {**data, **{k: v for k, v in _summary(data, now).items() if k in ("as_of_session", "closed_since", "sessions_since")}}


def newest(base: Path, market: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The most recently saved scan in full: what opens when the page loads."""
    entries = list_entries(base, market, now)
    return load(base, market, entries[0]["id"], now) if entries else None
