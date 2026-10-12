"""Scrub credentials and account identifiers from anything that may be logged or shown.

Applied to every broker error message, every log record from the broker
loggers, and every diagnostic payload before it leaves the server. It removes:

* bearer tokens, JWT-shaped strings and ``access_token`` / ``refresh_token`` /
  ``code`` / ``code_verifier`` / ``client_secret`` values in JSON, query strings
  or form bodies;
* any account number the provider has registered as sensitive;
* long digit runs (6+), which is how account numbers look when we did not
  know them in advance.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Iterable, Set

_SECRET_KEYS = ("access_token", "refresh_token", "id_token", "code_verifier",
                "client_secret", "authorization", "code", "state", "token",
                "account_number", "rhs_account_number", "account_id")

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_KV = re.compile(
    r"""(?ix)
    (["']?(?:%s)["']?\s*[:=]\s*)      # key, quoted or not, then : or =
    (["']?)([^"'&\s,}]+)(["']?)        # value up to a delimiter
    """ % "|".join(_SECRET_KEYS))
_LONG_DIGITS = re.compile(r"(?<![\d.])\d{6,}(?![\d.])")

_known_lock = threading.Lock()
_known: Set[str] = set()


def register_sensitive(values: Iterable[str]) -> None:
    """Remember identifiers (account numbers) that must never appear in output."""
    with _known_lock:
        for v in values:
            v = str(v or "").strip()
            if len(v) >= 4:
                _known.add(v)


def redact(value: Any) -> str:
    text = str(value if value is not None else "")
    with _known_lock:
        known = sorted(_known, key=len, reverse=True)
    for k in known:
        text = text.replace(k, "[account]")
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _JWT.sub("[token]", text)
    text = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]{m.group(4)}", text)
    text = _LONG_DIGITS.sub("[number]", text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites a record's message in place, so no handler ever sees the raw text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not leak args
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            # Tracebacks carry exception text; render it redacted and drop the original.
            import traceback
            record.msg += "\n" + redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
            record.exc_text = None
        return True


def get_logger(name: str) -> logging.Logger:
    """A logger whose records are redacted before any handler runs."""
    log = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in log.filters):
        log.addFilter(RedactingFilter())
    return log
