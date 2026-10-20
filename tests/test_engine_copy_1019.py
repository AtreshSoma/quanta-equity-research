"""Engine copy carries no em dashes (2026-10-19).

User-facing engine text (reasons, notes, warnings, setup_basis, guidance)
uses ": ", "; " or ", " instead of an em dash. The golden snapshots are the
serialised analyzer output, so scanning them covers every string a fixture
reaches. News headlines are third-party text and are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()

EM_DASH = chr(0x2014)
EXPECTED = Path(__file__).resolve().parent / "golden" / "expected"
_THIRD_PARTY_KEYS = ("headline", "news")


def _is_third_party(path: tuple) -> bool:
    return any(
        isinstance(p, str) and any(k in p.lower() for k in _THIRD_PARTY_KEYS)
        for p in path
    )


def _dashed_strings(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if EM_DASH in str(key) and not _is_third_party(path):
                yield path + (key,), str(key)
            yield from _dashed_strings(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _dashed_strings(value, path + (i,))
    elif isinstance(node, str) and EM_DASH in node and not _is_third_party(path):
        yield path, node


_FIXTURES = sorted(EXPECTED.glob("*.json"))


def test_golden_snapshots_exist():
    assert _FIXTURES, f"no golden snapshots under {EXPECTED}"


@pytest.mark.parametrize("snapshot", _FIXTURES, ids=lambda p: p.stem)
def test_golden_output_has_no_em_dash_outside_news(snapshot):
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    hits = [f"{'.'.join(map(str, p))}: {s[:120]!r}" for p, s in _dashed_strings(data)]
    assert not hits, "em dash in engine output:\n" + "\n".join(hits)


def test_dividend_notes_have_no_em_dash():
    from engine.dividend import validate_dividend_yield

    _, suspicious = validate_dividend_yield(0.18, market_cap=50_000_000_000)
    _, suppressed = validate_dividend_yield(0.75)
    assert "suspicious" in suspicious and EM_DASH not in suspicious
    assert "data validation failed" in suppressed and EM_DASH not in suppressed
