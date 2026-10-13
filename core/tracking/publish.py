"""Publish the ledger as a tamper-evident public track record.

``signals.sqlite3`` is local, git-ignored state: a reader has to take its word.
``publish`` exports it to ``track_record/`` at the repository root, a directory
meant to be committed every day:

* ``ledger.csv``   one row per call, with ``call_hash`` over the fields fixed at
  publication (ticker, dates, stance, levels, code fingerprint) and
  ``outcome_hash`` over the result once it resolves.
* ``calls.jsonl``  APPEND-ONLY: one line when a call is first published, one more
  when its outcome first appears. Nothing is ever rewritten in it.
* ``chain.jsonl``  one line per publication that changed anything, hashing the
  previous line and recording how many ``calls.jsonl`` lines existed and the
  hash of exactly those lines.

``verify`` therefore detects a forecast edited after publication, a call deleted
from the record, an outcome changed after it was first published, and any
rewritten link. What it cannot detect on its own is someone regenerating every
file from scratch: that is what committing ``track_record/`` daily (and pushing
it) is for, since git history then timestamps each link independently.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .ledger import SignalLedger

DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "track_record"
GENESIS = "0" * 64

# Fixed when the call is published. Changing any of these changes call_hash.
CALL_FIELDS = (
    "signal_key", "engine", "strategy", "ticker", "as_of_date", "recorded_at", "horizon_days",
    "stance", "action", "label", "evidence_status", "score", "reference_price",
    "target_price", "stop_price", "benchmark", "code_fingerprint", "git_commit",
)
# Filled in when the horizon resolves; fixed from then on.
OUTCOME_FIELDS = (
    "entry_date", "entry_price", "exit_date", "exit_close", "return_pct",
    "benchmark_return_pct", "excess_return_pct", "first_touch", "outcome", "resolved_at",
)
COLUMNS = ("id",) + CALL_FIELDS + ("call_hash",) + OUTCOME_FIELDS + ("outcome_hash",)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(round(value, 6))
    return str(value)


def _digest(row: Dict[str, Any], fields) -> str:
    payload = "\x1f".join(_cell(row.get(k)) for k in fields)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def call_hash(row: Dict[str, Any]) -> str:
    return _digest(row, CALL_FIELDS)


def outcome_hash(row: Dict[str, Any]) -> str:
    """Hash of the resolved result; empty while the call is pending."""
    return _digest(row, OUTCOME_FIELDS) if _cell(row.get("outcome")) else ""


def _public_rows(ledger: SignalLedger) -> List[Dict[str, str]]:
    out = []
    for r in ledger.rows():
        r = dict(r)
        r["strategy"] = json.loads(r.get("context_json") or "{}").get("strategy")
        r["call_hash"] = call_hash(r)
        r["outcome_hash"] = outcome_hash(r)
        out.append({k: _cell(r.get(k)) for k in COLUMNS})
    return out


def _csv_text(rows: List[Dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _link_hash(link: Dict[str, Any]) -> str:
    body = {k: link.get(k) for k in ("published_at", "rows", "resolved", "csv_sha256",
                                     "calls_lines", "calls_sha256", "prev")}
    return _sha(json.dumps(body, sort_keys=True))


def _lines(path: Path) -> List[str]:
    """Lines exactly as stored (LF-terminated), so prefix hashes are reproducible."""
    if not path.exists():
        return []
    return path.read_bytes().decode("utf-8").splitlines(keepends=True)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(out_dir: Path) -> Iterator[None]:
    """One publisher at a time (the daily job and a manual run can overlap)."""
    path = out_dir / ".publish.lock"
    fh = open(path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # Windows
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        fh.close()  # releases the lock


def publish(ledger: SignalLedger, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Rewrite ``ledger.csv``, append new calls/outcomes and, if anything changed, a link."""
    out_dir = Path(out_dir or DEFAULT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    with _locked(out_dir):
        rows = _public_rows(ledger)
        text = _csv_text(rows)
        _write_atomic(out_dir / "ledger.csv", text)  # always: a hand edit never survives

        calls_path = out_dir / "calls.jsonl"
        logged = _lines(calls_path)
        seen_calls, seen_outcomes = set(), set()
        for line in logged:
            entry = json.loads(line)
            (seen_outcomes if "outcome_hash" in entry else seen_calls).add(entry["signal_key"])
        new = []
        for r in rows:
            if r["signal_key"] not in seen_calls:
                new.append({"signal_key": r["signal_key"], "call_hash": r["call_hash"]})
            if r["outcome_hash"] and r["signal_key"] not in seen_outcomes:
                new.append({"signal_key": r["signal_key"], "outcome_hash": r["outcome_hash"]})
        if new:
            with open(calls_path, "a", encoding="utf-8", newline="") as fh:
                fh.writelines(json.dumps(e, sort_keys=True) + "\n" for e in new)
            logged = _lines(calls_path)

        chain_path = out_dir / "chain.jsonl"
        chain = [json.loads(x) for x in _lines(chain_path)]
        digest = _sha(text)
        head = chain[-1] if chain else None
        if head and head.get("csv_sha256") == digest and head.get("calls_lines") == len(logged):
            return {"changed": False, "rows": len(rows), "head": head["hash"], "dir": str(out_dir)}
        link = {
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": len(rows),
            "resolved": sum(1 for r in rows if r["outcome"]),
            "csv_sha256": digest,
            "calls_lines": len(logged),
            "calls_sha256": _sha("".join(logged)),
            "prev": head["hash"] if head else GENESIS,
        }
        link["hash"] = _link_hash(link)
        with open(chain_path, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(link, sort_keys=True) + "\n")
        return {"changed": True, "rows": len(rows), "head": link["hash"], "dir": str(out_dir)}


@dataclass
class Verification:
    ok: bool = True
    problems: List[str] = field(default_factory=list)
    links: int = 0
    rows: int = 0

    def fail(self, message: str) -> None:
        self.ok = False
        self.problems.append(message)


def _parse_jsonl(lines: List[str], name: str, res: Verification) -> List[Dict[str, Any]]:
    out = []
    for i, line in enumerate(lines, 1):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            res.fail(f"{name} line {i} is not valid JSON")
    return out


def verify(out_dir: Optional[Path] = None, ledger: Optional[SignalLedger] = None) -> Verification:
    """Check the chain, the append-only call log, the CSV and (optionally) the local ledger."""
    out_dir = Path(out_dir or DEFAULT_DIR)
    res = Verification()
    chain = _parse_jsonl(_lines(out_dir / "chain.jsonl"), "chain.jsonl", res)
    calls_lines = _lines(out_dir / "calls.jsonl")
    res.links = len(chain)

    prev, prev_lines = GENESIS, 0
    for i, link in enumerate(chain, 1):
        if link.get("prev") != prev:
            res.fail(f"chain link {i} does not follow link {i - 1}")
        if _link_hash(link) != link.get("hash"):
            res.fail(f"chain link {i} was edited (hash mismatch)")
        n = link.get("calls_lines")
        if not isinstance(n, int) or n < prev_lines or n > len(calls_lines):
            res.fail(f"chain link {i}: calls.jsonl is shorter than when it was published")
        elif _sha("".join(calls_lines[:n])) != link.get("calls_sha256"):
            res.fail(f"chain link {i}: published calls were removed or rewritten")
        prev, prev_lines = link.get("hash"), n if isinstance(n, int) else prev_lines

    csv_path = out_dir / "ledger.csv"
    if not csv_path.exists():
        if chain:
            res.fail("ledger.csv is missing")
        return res
    text = csv_path.read_bytes().decode("utf-8")
    if chain and _sha(text) != chain[-1].get("csv_sha256"):
        res.fail("ledger.csv differs from the last published link")
    published = list(csv.DictReader(io.StringIO(text)))
    res.rows = len(published)
    if chain and chain[-1].get("rows") != len(published):
        res.fail("ledger.csv row count differs from the last published link")
    by_key: Dict[str, Dict[str, str]] = {}
    for row in published:
        key = row.get("signal_key", "")
        by_key[key] = row
        if call_hash(row) != row.get("call_hash"):
            res.fail(f"call {key} was edited after publication")
        if outcome_hash(row) != row.get("outcome_hash", ""):
            res.fail(f"call {key}: outcome fields do not match their hash")

    for entry in _parse_jsonl(calls_lines, "calls.jsonl", res):
        key = entry.get("signal_key")
        row = by_key.get(key)
        if row is None:
            res.fail(f"call {key} was published and later removed")
        elif "call_hash" in entry and entry["call_hash"] != row.get("call_hash"):
            res.fail(f"call {key}: forecast differs from when it was first published")
        elif "outcome_hash" in entry and entry["outcome_hash"] != row.get("outcome_hash"):
            res.fail(f"call {key}: outcome changed after it was first published")

    if ledger is not None:
        local = {r["signal_key"]: r for r in _public_rows(ledger)}
        for key, row in by_key.items():
            mine = local.get(key)
            if mine is None:
                res.fail(f"call {key} is published but missing from the local ledger")
            elif mine["call_hash"] != row.get("call_hash"):
                res.fail(f"call {key} differs between the ledger and its publication")
    return res
