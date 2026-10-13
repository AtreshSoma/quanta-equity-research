"""Which code produced a call: a content hash of the engine's executable source.

A track record is only evidence for the model that produced it. The evidence
key (``elite.research.v2``) names a model version, but nothing stopped the code
behind a key from changing silently mid-record. Every ledger row now carries the
fingerprint of the source that made it, so the record can be split at any code
change and a reviewer can check that a period was run on frozen code.

The fingerprint covers every source file the engine's calls depend on (its
own module, modules it imports for confirmation, the shared decision core in
``core/engine``) and the evidence registry, whose status gates every label. It
is stable across line endings and checkouts, and is computed when this module is
imported: a ``git pull`` under a running server must not relabel calls made by
the code still in memory.
"""
from __future__ import annotations

import functools
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CORE = Path(__file__).resolve().parent.parent

# Engine key (as recorded in the ledger) -> the top-level files its calls depend on.
# The portfolio scanner runs the deep-analysis engine as its Research check.
ENGINE_SOURCES: Dict[str, Tuple[str, ...]] = {
    "shortterm": ("SP_ShortTerm.py",),
    "elite": ("TradingAnalysis_ELITE.py",),
    "elite_pro": ("SP_ELITE_PRO.py", "TradingAnalysis_ELITE.py"),
}
# Data that changes what a call says without any code changing.
SHARED_DATA = ("apex_quant/_reports/evidence_registry.json",)


def _sources(engine: str, with_data: bool) -> List[Path]:
    files = sorted((_CORE / "engine").glob("*.py"))
    files += [_CORE / name for name in ENGINE_SOURCES.get(engine, ())
              + (SHARED_DATA if with_data else ())]
    return [f for f in files if f.exists()]


def _compute(engine: str, with_data: bool = True) -> str:
    digest = hashlib.sha256()
    for source in _sources(engine, with_data):
        digest.update(source.relative_to(_CORE).as_posix().encode())
        digest.update(b"\0")
        digest.update(source.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


_AT_IMPORT: Dict[str, str] = {engine: _compute(engine) for engine in ENGINE_SOURCES}


def code_fingerprint(engine: str, with_data: bool = True) -> str:
    """SHA-256 over the engine's dependencies, as they were when this process loaded.

    ``with_data=False`` hashes the code alone. A validation run records that one:
    writing its own result into the evidence registry must not change the
    fingerprint of the code it tested.
    """
    if not with_data:
        return _compute(engine, with_data=False)
    if engine not in _AT_IMPORT:
        _AT_IMPORT[engine] = _compute(engine)
    return _AT_IMPORT[engine]


@functools.lru_cache(maxsize=1)
def git_commit() -> Optional[str]:
    """The checkout's commit, suffixed ``-dirty`` with uncommitted changes; None outside git."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_CORE, capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=_CORE,
                               capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{sha}-dirty" if dirty else sha or None
