"""Provider calls owned by the scan and bounded by a wall-clock budget (audit A01).

yfinance's bulk download has no timeout and cannot be cancelled, so a thread
running it outlives a timeout and keeps issuing requests while the scan falls
back to another selection. Here the call runs in a child process the caller
owns: on timeout the child is killed and reaped before ``run`` returns, so no
abandoned provider work overlaps the fallback.

A target is named ``"module:function"`` and must be importable in the child.
Its keyword arguments go to the child over a pipe; its return value (a price
frame) comes back pickled in a file inside a fresh private temporary directory,
so no other account can swap the file between write and read.
``QUANTA_BULK_TARGET`` overrides the default bulk-download target only with the
default itself or a test target (module ``_bounded_targets``, which the suite
uses to refuse network access); any other value is ignored and logged, so an
environment variable cannot make the scan call arbitrary code.
"""
from __future__ import annotations

import logging
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

DEFAULT_BULK_TARGET = "engine.bounded:yf_download"
_TEST_TARGET_MODULE = "_bounded_targets"
logger = logging.getLogger(__name__)

_CHILD = (
    "import pickle, sys\n"
    "p = pickle.load(sys.stdin.buffer)\n"
    "sys.path[:0] = p['sys_path']\n"
    "sys.stdout = sys.stderr\n"
    "import importlib\n"
    "mod, fn = p['target'].split(':')\n"
    "try:\n"
    "    out = {'ok': True, 'value': getattr(importlib.import_module(mod), fn)(**p['kwargs'])}\n"
    "except BaseException as exc:\n"
    "    out = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}\n"
    "with open(p['out'], 'wb') as fh:\n"
    "    pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)\n"
)


@dataclass
class Bounded:
    status: str                  # "ok" | "timeout" | "error"
    value: Any = None
    error: str = ""
    seconds: float = 0.0
    reaped: bool = True          # the child is gone when run() returns


def bulk_target() -> str:
    override = (os.environ.get("QUANTA_BULK_TARGET") or "").strip()
    if not override or override == DEFAULT_BULK_TARGET:
        return DEFAULT_BULK_TARGET
    if override.split(":", 1)[0] == _TEST_TARGET_MODULE:
        return override
    logger.warning("Ignoring QUANTA_BULK_TARGET=%r: only the test targets may replace the bulk download",
                   override[:80])
    return DEFAULT_BULK_TARGET


def run(target: str, kwargs: Dict[str, Any], budget: float,
        kill_grace: float = 5.0) -> Bounded:
    """Run ``target(**kwargs)`` in a child process for at most ``budget`` seconds."""
    started = time.time()
    # mkdtemp makes the directory readable and writable by this account only.
    work_dir = tempfile.mkdtemp(prefix="quanta-bounded-")
    out_path = os.path.join(work_dir, "result.pkl")
    payload = pickle.dumps({"sys_path": list(sys.path), "target": target,
                            "kwargs": kwargs, "out": out_path})
    proc = subprocess.Popen([sys.executable, "-c", _CHILD], stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        try:
            proc.communicate(payload, timeout=max(0.01, float(budget)))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=kill_grace)
            except subprocess.TimeoutExpired:
                pass
            return Bounded("timeout", error=f"exceeded {budget:g}s", seconds=time.time() - started,
                           reaped=proc.poll() is not None)
        try:
            with open(out_path, "rb") as fh:
                out = pickle.load(fh)
        except (OSError, EOFError, pickle.UnpicklingError) as exc:
            return Bounded("error", error=f"no result from the provider process ({type(exc).__name__})",
                           seconds=time.time() - started)
        if not out.get("ok"):
            return Bounded("error", error=str(out.get("error") or "provider error"),
                           seconds=time.time() - started)
        return Bounded("ok", value=out.get("value"), seconds=time.time() - started)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        shutil.rmtree(work_dir, ignore_errors=True)


def yf_download(tickers, period: str, threads: int = 1) -> Optional[Any]:
    """One bulk daily download, as the scanner has always issued it."""
    import yfinance as yf
    return yf.download(" ".join(tickers), period=period, group_by="ticker", progress=False,
                       threads=max(1, int(threads)), auto_adjust=True, actions=False)
