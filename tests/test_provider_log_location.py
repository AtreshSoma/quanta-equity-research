"""The provider log goes to its fixed location, never into the process's working directory."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=False)

import SP_ShortTerm as st  # noqa: E402

PROVIDER_LOGGERS = ("yfinance", "urllib3", "urllib3.connectionpool", "peewee")


def test_configuring_provider_logging_leaves_the_working_directory_clean(tmp_path, monkeypatch):
    """Review 2026-10-30: a relative FileHandler("apex_trading.log") wrote the yfinance warnings
    into whichever directory the server was started from, leaving a stray log in the repo root."""
    saved = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).level, logging.getLogger(n).propagate)
             for n in PROVIDER_LOGGERS}
    ready = st._PROVIDER_LOGGING_READY
    monkeypatch.chdir(tmp_path)
    try:
        st._PROVIDER_LOGGING_READY = False
        st.configure_provider_logging()
        assert list(tmp_path.iterdir()) == [], "no log file may appear in the working directory"
    finally:
        st._PROVIDER_LOGGING_READY = ready
        for name, (handlers, level, propagate) in saved.items():
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                if h not in handlers:
                    lg.removeHandler(h)
                    h.close()
            lg.setLevel(level)
            lg.propagate = propagate
