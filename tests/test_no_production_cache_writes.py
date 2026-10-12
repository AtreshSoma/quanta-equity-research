"""The test suite never reads or writes the production response cache.

app.py loads ``<QUANTA_CACHE_DIR or data/cache>/yf_cache.json`` at import and
saves its in-memory cache there at exit, keeping only entries still within
their TTL. A test run that imported app without isolating the directory pruned
the production file (34.7 MB to 171 KB on 2026-10-02). conftest now points the
suite at a temporary directory with the disk cache off, before anything imports
app.
"""
from __future__ import annotations

import os
from pathlib import Path

from _paths import ensure_paths

ensure_paths()


def test_the_suite_uses_a_temporary_cache_directory():
    import app
    production = (Path(app.PROJECT_ROOT) / "data" / "cache").resolve()
    assert Path(app._YF_CACHE_DIR).resolve() != production
    assert "quanta-test-cache" in os.environ.get("QUANTA_CACHE_DIR", "")


def test_the_disk_cache_is_off_in_tests():
    import app
    assert app._YF_DISK_CACHE_ENABLED is False


def test_the_suite_never_writes_the_live_regime_history():
    """The Overview's day-over-day regime notes read .regime_history.json; test
    runs used to upsert fake rows (vix null, ten-year 100) into the live file."""
    import app
    assert Path(app._REGIME_HISTORY_PATH).resolve() != (Path(app.WEBAPP_DIR) / ".regime_history.json").resolve()
