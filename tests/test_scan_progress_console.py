"""A scan never depends on the console progress bar (2026-10-10).

A server whose stderr could not be written failed every Picks scan "at 0 of
100": the bar's first draw raised before the loop handled one result.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()
import SP_ELITE_PRO as sp  # noqa: E402


class _DeadConsole:
    """A terminal the process can no longer write to."""

    def isatty(self):
        return True

    def write(self, *_a, **_k):
        raise OSError(22, "Invalid argument")

    def flush(self):
        raise OSError(22, "Invalid argument")


def _futures(n=5):
    ex = ThreadPoolExecutor(max_workers=2)
    futs = {ex.submit(lambda i=i: i): i for i in range(n)}
    return ex, futs


def test_a_dead_console_does_not_stop_the_scan_loop(monkeypatch):
    monkeypatch.setattr(sys, "stderr", _DeadConsole())
    ex, futs = _futures()
    try:
        got = sorted(f.result() for f in sp.QuantaEliteTradingSystem._progress_iter(futs, len(futs)))
    finally:
        ex.shutdown()
    assert got == [0, 1, 2, 3, 4]


def test_no_terminal_means_no_bar(monkeypatch):
    class _File:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stderr", _File())
    ex, futs = _futures(3)
    try:
        it = sp.QuantaEliteTradingSystem._progress_iter(futs, 3)
        assert sorted(f.result() for f in it) == [0, 1, 2]
    finally:
        ex.shutdown()


def test_no_stderr_at_all(monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    ex, futs = _futures(2)
    try:
        assert len(list(sp.QuantaEliteTradingSystem._progress_iter(futs, 2))) == 2
    finally:
        ex.shutdown()


def test_a_bar_update_failure_does_not_drop_completed_futures(monkeypatch):
    class _Terminal:
        def isatty(self):
            return True

    class _BrokenBar:
        def __init__(self, **_kwargs):
            pass

        def update(self, _count):
            raise OSError("console disconnected during scan")

        def close(self):
            raise OSError("console disconnected during close")

    monkeypatch.setattr(sys, "stderr", _Terminal())
    monkeypatch.setattr(sp, "HAS_TQDM", True)
    monkeypatch.setattr(sp, "tqdm", _BrokenBar)
    ex, futs = _futures()
    try:
        assert sorted(f.result() for f in sp.QuantaEliteTradingSystem._progress_iter(futs, 5)) == list(range(5))
    finally:
        ex.shutdown()


def test_a_bar_close_failure_does_not_fail_the_scan(monkeypatch):
    class _Terminal:
        def isatty(self):
            return True

    class _BrokenClose:
        def __init__(self, **_kwargs):
            pass

        def update(self, _count):
            pass

        def close(self):
            raise OSError("console disconnected during close")

    monkeypatch.setattr(sys, "stderr", _Terminal())
    monkeypatch.setattr(sp, "HAS_TQDM", True)
    monkeypatch.setattr(sp, "tqdm", _BrokenClose)
    ex, futs = _futures(2)
    try:
        assert len(list(sp.QuantaEliteTradingSystem._progress_iter(futs, 2))) == 2
    finally:
        ex.shutdown()
