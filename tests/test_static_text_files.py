"""Web assets stay plain text.

On 2026-10-29 a CSS escape written as a raw NUL byte (``content: "<NUL>b7<NUL>a0"``)
reached webapp/static/quanta.css: the page showed "�b7�a0" before
each "since" date and git treated the stylesheet as a binary file. A NUL byte
is never valid in these files, so any one of them fails this test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "webapp"
FILES = sorted(p for pattern in ("static/*.js", "static/*.css", "templates/*.html")
               for p in STATIC.glob(pattern))


def test_assets_found():
    assert len(FILES) > 5


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_no_nul_bytes(path):
    data = path.read_bytes()
    offset = data.find(b"\x00")
    assert offset == -1, f"{path.name}: NUL byte at offset {offset}"
