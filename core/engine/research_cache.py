"""One research call per (ticker, session, mode), shared by both pages (audit F7).

Picks' deep confirmation and My Portfolio used to run the Research engine
separately (different modes, different moments), so the same ticker could get
two verdicts in one session. Both now read and write this process-wide store,
keyed by ticker, the last completed market session and the research mode
(``RESEARCH.MODE``). The action, levels and grade do not depend on the account
size; a consumer that needs a size re-sizes for its own account.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Tuple

_LOCK = threading.Lock()
_STORE: Dict[Tuple[str, str, str], Any] = {}


def session_key(session: Optional[str] = None) -> str:
    if session:
        return str(session)
    from .market_calendar import last_completed_session
    return last_completed_session().isoformat()


def _key(ticker: str, mode: str, session: Optional[str]) -> Tuple[str, str, str]:
    return (str(ticker or "").upper(), session_key(session), str(mode or ""))


def get(ticker: str, mode: str, session: Optional[str] = None) -> Any:
    with _LOCK:
        return _STORE.get(_key(ticker, mode, session))


def cacheable(result: Any) -> bool:
    """Only a usable verdict is shared: data status ok and an action present.
    A provider failure must be retried, not served from the cache."""
    if result is None:
        return False
    ds = getattr(result, "data_status", "ok")
    if str(getattr(ds, "value", ds) or "").lower() != "ok":
        return False
    return bool(str(getattr(result, "final_action", "x") or ""))


def put(ticker: str, mode: str, result: Any, session: Optional[str] = None) -> None:
    if not cacheable(result):
        return
    with _LOCK:
        _STORE[_key(ticker, mode, session)] = result


def get_or_compute(ticker: str, mode: str, compute: Callable[[], Any], session: Optional[str] = None) -> Any:
    """The cached result, or ``compute()`` stored under the key (failures are not cached;
    see :func:`cacheable`)."""
    hit = get(ticker, mode, session)
    if hit is not None:
        return hit
    result = compute()
    put(ticker, mode, result, session)
    return result


def clear() -> None:
    with _LOCK:
        _STORE.clear()
