"""Normalise a serialized AnalysisResult into a stable, diffable snapshot.

Two kinds of noise have to come out before a snapshot is worth comparing:

* **Volatile fields** -- run timestamps, elapsed times, cache statistics. These
  change every run regardless of any code change, so they are replaced with a
  placeholder rather than dropped: keeping the key proves the field still
  exists, which a later refactor could otherwise remove unnoticed.
* **Float noise** -- the last bits of a float can differ across BLAS builds and
  platforms. Values are rounded, and the rounding is deliberately coarse
  (6 significant figures) because a real behavioural change moves a score by
  far more than that, while a NumPy version bump can move the last two digits.

Anything not on the volatile list is compared exactly. The default is
strictness: a new field appears in the diff and has to be acknowledged.
"""

from __future__ import annotations

import math
import re
from typing import Any

PLACEHOLDER = "<volatile>"

# Exact key names whose value is replaced wherever they appear, at any depth.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "analysis_timestamp",
        "generated_at",
        "as_of",
        "asof",
        "retrieved_at",
        "fetched_at",
        "last_updated",
        "elapsed",
        "elapsed_seconds",
        "duration",
        "runtime",
        "cache_stats",
        "cache_hits",
        "cache_misses",
        "hit_rate",
    }
)

# Key-name patterns treated as volatile (suffix/substring matches).
VOLATILE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"_timestamp$"),
    re.compile(r"_at$"),
    re.compile(r"^data_age"),
    re.compile(r"_age_days$"),
)

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)")

SIGFIGS = 6


def _is_volatile(key: str) -> bool:
    if key in VOLATILE_KEYS:
        return True
    return any(p.search(key) for p in VOLATILE_PATTERNS)


def _round_sig(value: float, sigfigs: int = SIGFIGS) -> float:
    """Round to significant figures, so tolerance scales with magnitude."""
    if value == 0 or not math.isfinite(value):
        return value
    return round(value, -int(math.floor(math.log10(abs(value)))) + (sigfigs - 1))


def normalize(obj: Any, _key: str = "") -> Any:
    """Recursively normalise a JSON-like structure."""
    if _key and _is_volatile(_key):
        return PLACEHOLDER

    if isinstance(obj, dict):
        return {k: normalize(v, k) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}

    if isinstance(obj, (list, tuple)):
        return [normalize(v, _key) for v in obj]

    if isinstance(obj, bool):
        return obj

    if isinstance(obj, float):
        if math.isnan(obj):
            return "<nan>"
        if math.isinf(obj):
            return "<inf>" if obj > 0 else "<-inf>"
        return _round_sig(obj)

    if isinstance(obj, int):
        return obj

    if isinstance(obj, str):
        # A datetime that reached the payload as a string is still volatile.
        if _ISO.match(obj):
            return PLACEHOLDER
        return obj

    if obj is None:
        return None

    # Anything else (Enum, Timestamp, numpy scalar) goes through str() so the
    # snapshot stays JSON-serialisable rather than failing at dump time.
    return str(obj)


def diff(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Return human-readable differences, deepest-first. Empty means identical."""
    out: list[str] = []

    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float)) and isinstance(actual, (int, float))
    ):
        out.append(
            f"{path or '<root>'}: type {type(expected).__name__} -> {type(actual).__name__}"
        )
        return out

    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in actual:
                out.append(f"{sub}: REMOVED (was {expected[key]!r})")
            elif key not in expected:
                out.append(f"{sub}: ADDED ({actual[key]!r})")
            else:
                out.extend(diff(expected[key], actual[key], sub))
        return out

    if isinstance(expected, list):
        if len(expected) != len(actual):
            out.append(f"{path}: length {len(expected)} -> {len(actual)}")
        for i, (e, a) in enumerate(zip(expected, actual)):
            out.extend(diff(e, a, f"{path}[{i}]"))
        return out

    if expected != actual:
        out.append(f"{path or '<root>'}: {expected!r} -> {actual!r}")
    return out
