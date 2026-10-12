"""Canonical import-path resolution for the test suite.

Every test module needs some combination of ``core/``, ``modules/`` and the
Flask web app on ``sys.path``. Before this module existed, each of the 24 test
files rebuilt those paths itself. When the web app moved out of
``stock-analysis/`` into ``ui/stock-analysis/`` (monorepo commits 1af5106 / b461881) the
launchers were repointed but five test files were not -- they kept inserting a
directory that no longer existed, `import app` started failing, and 1,226 lines
of coverage stopped running without anyone noticing.

Resolution therefore lives in exactly one place, and the web app is *searched
for* rather than hardcoded, so the next move cannot silently break it again.

Under pytest these paths are also supplied by ``pythonpath`` in pyproject.toml.
This module is what additionally makes direct execution work::

    python tests/test_market_regime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/ -> the repository root (the engine lives here: core/, modules/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The parent of the repository root; only used to resolve the web app in an
# older monorepo checkout (PyProjects/stock-analysis + PyProjects/ui/...).
REPO_ROOT = PROJECT_ROOT.parent

CORE = PROJECT_ROOT / "core"
MODULES = PROJECT_ROOT / "modules"

# Candidate locations for the Flask web app, current location first. Add to
# this list rather than editing call sites if it moves again.
_WEBAPP_CANDIDATES = (
    PROJECT_ROOT / "webapp",                         # this repository
    REPO_ROOT / "ui" / "stock-analysis" / "webapp",  # the former monorepo layout
)


def _resolve_webapp() -> Path | None:
    """Return the web app directory, or None if no candidate exists.

    Returns None rather than raising so that a test which only needs `core`
    is not brought down by a missing web app.
    """
    for candidate in _WEBAPP_CANDIDATES:
        if (candidate / "app.py").is_file():
            return candidate
    return None


WEBAPP = _resolve_webapp()


def ensure_paths(*, webapp: bool = False) -> None:
    """Put ``core`` and ``modules`` on ``sys.path``; optionally the web app.

    Idempotent. Pass ``webapp=True`` in the modules that ``import app``.

    Raises:
        RuntimeError: if ``webapp=True`` but no candidate directory contains
            ``app.py`` -- a loud failure is correct here, because the silent
            version of this is the bug the module exists to prevent.
    """
    wanted: list[Path] = [CORE, MODULES]

    if webapp:
        if WEBAPP is None:
            searched = "\n  ".join(str(c) for c in _WEBAPP_CANDIDATES)
            raise RuntimeError(
                "Could not locate the Flask web app (app.py). Searched:\n  "
                f"{searched}\n"
                "If it moved again, add the new location to "
                "_WEBAPP_CANDIDATES in tests/_paths.py."
            )
        wanted.append(WEBAPP)

    for path in wanted:
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
