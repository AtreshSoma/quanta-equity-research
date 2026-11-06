"""Which code produced a call: content hashes of the engine's executable source.

A track record is only evidence for the model that produced it. The evidence
key (``elite.research.v2``) names a model version, but nothing stopped the code
behind a key from changing silently mid-record. Every ledger row carries two
fingerprints of the source that made it, so the record can be split at any code
change and a reviewer can check that a period was run on frozen code:

``code_fingerprint``
    SHA-256 over the raw bytes of every file a call depends on. Any edit, even a
    comment, changes it. Kept for audit: it says exactly which bytes ran.
``model_fingerprint``
    SHA-256 over the same files after normalisation: each Python file is parsed
    and hashed as its syntax tree with docstrings, logging and ``print`` calls,
    and the CLI-only display functions listed in :data:`PRESENTATION_ONLY`
    removed. Comments and formatting never reach a syntax tree. An edit that can
    change what a call says (a weight, a threshold, a gate, a level, a reason
    string inside the decision code) still changes it. Pre-registered tests read
    this one, so a reworded log line or CLI banner no longer restarts the
    evidence clock.

Both cover the same files: the shared decision core in ``core/engine``, the
engine's own top-level modules, every ``core/`` or ``modules/`` file those
import (followed transitively, including the optional imports made through
``_import_optional``) and the evidence registry, whose status gates every label.
Both also record which optional modules actually loaded in this process: a call
made without, say, ``earnings_revision_scoring`` is made by different code even
though no file changed.

File contents are read when this module is imported: a ``git pull`` under a
running server must not relabel calls made by the code still in memory.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import importlib
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CORE = Path(__file__).resolve().parent.parent
_ROOT = _CORE.parent
_MODULES = _ROOT / "modules"

# Engine key (as recorded in the ledger) -> the top-level files its calls depend on.
# The portfolio scanner runs the deep-analysis engine as its Research check.
ENGINE_SOURCES: Dict[str, Tuple[str, ...]] = {
    "shortterm": ("SP_ShortTerm.py",),
    "elite": ("TradingAnalysis_ELITE.py",),
    "elite_pro": ("SP_ELITE_PRO.py", "TradingAnalysis_ELITE.py"),
    # Shadow calls (ledger.record_shadow_calls) come from the same module. Any
    # other ``elite_<version>_shadow`` key resolves to the same sources through
    # :func:`engine_sources`, so a new shadow version cannot be left uncovered.
    "elite_v2_shadow": ("TradingAnalysis_ELITE.py",),
    "elite_v3_shadow": ("TradingAnalysis_ELITE.py",),
    "elite_v4_shadow": ("TradingAnalysis_ELITE.py",),
    # The free-factor baseline is scored from the Research engine's components.
    "factor_baseline_shadow": ("TradingAnalysis_ELITE.py",),
}
_SHADOW = re.compile(r"^elite_[a-z0-9_]+_shadow$")

# Data that changes what a call says without any code changing.
SHARED_DATA = ("apex_quant/_reports/evidence_registry.json",)

# Top-level functions that only print CLI output. They are left out of the
# model fingerprint; tests/test_fingerprint_coverage.py checks that no other
# code in the same file calls them, so leaving them out cannot hide a change
# to what a call says.
PRESENTATION_ONLY: Dict[str, FrozenSet[str]] = {
    "TradingAnalysis_ELITE.py": frozenset({
        "display_market_environment", "display_position_deep_dive",
        "display_decision_snapshot", "display_signal_explanation", "display_what_to_do",
        "display_execution_context", "display_peer_comparison", "display_signal_track_record",
        "display_company_news", "display_catalyst_alerts", "display_reconciled_recommendation",
        "display_analysis", "display_position_analysis", "main",
    }),
    "SP_ELITE_PRO.py": frozenset({"run_interactive_session", "_ensure_console_encoding", "main"}),
    "SP_ShortTerm.py": frozenset({"main"}),
}

_LOG_OWNERS = frozenset({"logger", "logging", "log", "_log", "LOGGER", "_logger"})


def engine_sources(engine: str) -> Tuple[str, ...]:
    """Top-level ``core/`` files behind an engine key; empty for an unknown key."""
    if engine in ENGINE_SOURCES:
        return ENGINE_SOURCES[engine]
    if _SHADOW.match(engine or ""):
        return ENGINE_SOURCES["elite"]
    return ()


# ----------------------------------------------------------------- discovery
def _is_main_guard(node: ast.AST) -> bool:
    """``if __name__ == "__main__":`` -- code that runs only as a script, never in a call."""
    test = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators))


def _walk_runtime(tree: ast.AST):
    """``ast.walk`` that skips script-only ``__main__`` blocks."""
    todo = [tree]
    while todo:
        node = todo.pop()
        yield node
        todo.extend(c for c in ast.iter_child_nodes(node) if not _is_main_guard(c))


def _imported_names(tree: ast.AST) -> Tuple[List[str], List[Tuple[str, ...]]]:
    """Module names a file imports, and each ``_import_optional`` candidate tuple."""
    names: List[str] = []
    optional: List[Tuple[str, ...]] = []
    for node in _walk_runtime(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "_import_optional"):
            cands = tuple(a.value for a in node.args
                          if isinstance(a, ast.Constant) and isinstance(a.value, str))
            if cands:
                optional.append(cands)
                names += list(cands)
    return names, optional


def _local_file(name: str) -> Optional[Path]:
    """The top-level ``core/X.py`` or ``modules/X.py`` an import name refers to."""
    base = name.split(".")
    if base[0] == "modules" and len(base) > 1:
        base = base[1:]
    if len(base) != 1:
        return None
    for folder in (_CORE, _MODULES):
        path = folder / f"{base[0]}.py"
        if path.is_file():
            return path
    return None


# Bytes of every hashed file as they were when first read (at import for the
# known engines), so both fingerprints describe the code loaded in this process.
_BYTES: Dict[Path, bytes] = {}


def _read(path: Path) -> bytes:
    if path not in _BYTES:
        _BYTES[path] = path.read_bytes().replace(b"\r\n", b"\n")
    return _BYTES[path]


@functools.lru_cache(maxsize=None)
def _imports_of(path: Path) -> Tuple[Tuple[str, ...], Tuple[Tuple[str, ...], ...]]:
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:
        return (), ()
    names, opts = _imported_names(tree)
    return tuple(names), tuple(opts)


@functools.lru_cache(maxsize=None)
def _closure(engine: str) -> Tuple[Tuple[Path, ...], Tuple[Tuple[str, ...], ...]]:
    """Every source file behind ``engine`` (sorted) and its optional-import candidates."""
    start = sorted((_CORE / "engine").glob("*.py"))
    start += [_CORE / name for name in engine_sources(engine)]
    seen: Dict[Path, None] = {}
    optional: Dict[Tuple[str, ...], None] = {}
    todo = [p for p in start if p.is_file()]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen[path] = None
        names, opts = _imports_of(path)
        optional.update(dict.fromkeys(opts))
        for name in names:
            found = _local_file(name)
            if found is not None and found not in seen:
                todo.append(found)
    files = sorted(seen, key=lambda p: p.relative_to(_ROOT).as_posix())
    return tuple(files), tuple(sorted(optional))


def _sources(engine: str, with_data: bool) -> List[Path]:
    files = list(_closure(engine)[0])
    if with_data:
        files += [_CORE / name for name in SHARED_DATA]
    return [f for f in files if f.exists()]


# -------------------------------------------------------------- normalising
class _ModelOnly(ast.NodeTransformer):
    """Strip what cannot change a call: docstrings, logging, print, CLI display."""

    def __init__(self, presentation: FrozenSet[str]):
        self.presentation = presentation

    @staticmethod
    def _is_noise(stmt: ast.stmt) -> bool:
        if not isinstance(stmt, ast.Expr):
            return False
        value = stmt.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return True  # a docstring or a bare string statement
        if isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Name) and func.id == "print":
                return True
            owner = func.value if isinstance(func, ast.Attribute) else None
            if isinstance(owner, ast.Attribute) and owner.attr in _LOG_OWNERS:
                return True  # self.logger.info(...)
            if isinstance(owner, ast.Name) and owner.id in _LOG_OWNERS:
                return True  # logger.info(...)
        return False

    def _clean(self, body: List[ast.stmt]) -> List[ast.stmt]:
        kept = [s for s in body if not self._is_noise(s)]
        return kept or [ast.Pass()]

    def generic_visit(self, node: ast.AST) -> ast.AST:
        super().generic_visit(node)
        for name in ("body", "orelse", "finalbody"):
            block = getattr(node, name, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                setattr(node, name, self._clean(block))
        return node

    def visit_Module(self, node: ast.Module) -> ast.AST:
        node.body = [s for s in node.body
                     if not (isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                             and s.name in self.presentation)]
        return self.generic_visit(node)


@functools.lru_cache(maxsize=None)
def model_source(path: Path) -> bytes:
    """The normalised form of ``path`` the model fingerprint hashes."""
    raw = _read(path)
    if path.suffix != ".py":
        return raw
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return raw
    tree = _ModelOnly(PRESENTATION_ONLY.get(path.name, frozenset())).visit(tree)
    return ast.dump(tree, include_attributes=False).encode("utf-8")


def _compute(engine: str, with_data: bool = True, model: bool = False) -> str:
    digest = hashlib.sha256()
    for source in _sources(engine, with_data):
        digest.update(source.relative_to(_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(model_source(source) if model else _read(source))
    return digest.hexdigest()


# ------------------------------------------------------ optional-module state
@functools.lru_cache(maxsize=None)
def optional_modules(engine: str) -> Tuple[Tuple[str, bool], ...]:
    """Each optional module the engine asks for, and whether it imports here.

    The engine imports these with ``_import_optional`` and silently runs without
    any that fail. The same import is tried here (it has already happened in a
    process that ran the engine), so the answer matches what the engine saw.
    """
    out = []
    for cands in _closure(engine)[1]:
        loaded = any(c in sys.modules for c in cands)
        if not loaded:
            for c in cands:
                try:
                    importlib.import_module(c)
                    loaded = True
                    break
                except Exception:  # noqa: BLE001 - any failure means the engine ran without it
                    continue
        out.append((cands[0], loaded))
    return tuple(out)


def _with_optional(base: str, engine: str) -> str:
    missing = [name for name, loaded in optional_modules(engine) if not loaded]
    if not missing:
        return base  # the usual case: nothing extra to say
    return hashlib.sha256((base + "\0missing:" + ",".join(sorted(missing))).encode()).hexdigest()


# Raw-byte fingerprints are computed now; every file's bytes are captured now, so
# the model fingerprint (hashed lazily: parsing is slower) reads the same bytes.
_AT_IMPORT: Dict[Tuple[str, bool], str] = {}
for _engine in ENGINE_SOURCES:
    _AT_IMPORT[(_engine, False)] = _compute(_engine)


def _cached(engine: str, model: bool) -> str:
    key = (engine, model)
    if key not in _AT_IMPORT:
        if not engine_sources(engine):
            logger.warning("fingerprint: unknown engine %r; hashing core/engine only", engine)
        _AT_IMPORT[key] = _compute(engine, model=model)
    return _AT_IMPORT[key]


def code_fingerprint(engine: str, with_data: bool = True) -> str:
    """SHA-256 over the engine's dependencies' bytes, as they were when this process loaded.

    ``with_data=False`` hashes the code alone. A validation run records that one:
    writing its own result into the evidence registry must not change the
    fingerprint of the code it tested.
    """
    if not with_data:
        return _compute(engine, with_data=False)
    return _with_optional(_cached(engine, model=False), engine)


def model_fingerprint(engine: str, with_data: bool = True) -> str:
    """SHA-256 over the engine's normalised model code (see the module docstring)."""
    if not with_data:
        return _compute(engine, with_data=False, model=True)
    return _with_optional(_cached(engine, model=True), engine)


def provenance(engine: str) -> Dict[str, object]:
    """What a ledger row stores about the code behind a call, beside the two hashes."""
    files, _ = _closure(engine)
    return {
        "modules": sorted(p.relative_to(_ROOT).as_posix() for p in files
                          if p.parent != _CORE / "engine"),
        "optional_missing": sorted(n for n, loaded in optional_modules(engine) if not loaded),
    }


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
