"""What the running app needs is declared where an install will find it.

A "250 most liquid" scan on 2026-11-09 read an even spread of 1,517 names:
engine/bounded.py returns the bulk price download as Parquet, and pyarrow was
declared only in the `dev` extra. start.sh installs `.[webapp]`, so the server
had no Parquet engine, the liquidity ranking failed on every scan, and 32 names
under the market-cap floor were analysed in place of large caps. CI installs
`dev`, so every test passed.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()

ROOT = Path(__file__).resolve().parents[1]


def _names(lines) -> set:
    out = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return out


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_the_parquet_engine_is_a_hard_dependency_not_a_test_one():
    assert "pyarrow" in _names(_project()["dependencies"])


def test_webapp_requirements_carry_every_engine_dependency():
    hard = _names(_project()["dependencies"])
    listed = _names((ROOT / "webapp" / "requirements.txt").read_text(encoding="utf-8").splitlines())
    assert hard - listed == set()


def test_the_pinned_set_covers_the_parquet_engine():
    pinned = _names((ROOT / "webapp" / "constraints.txt").read_text(encoding="utf-8").splitlines())
    assert "pyarrow" in pinned


@pytest.mark.parametrize("launcher", ["start.sh", "start.ps1"])
def test_launchers_probe_for_the_parquet_engine(launcher):
    assert "pyarrow" in (ROOT / launcher).read_text(encoding="utf-8")


def test_frame_transport_reports_ok_here():
    from engine import bounded
    status = bounded.frame_transport_status()
    assert status["ok"] is True and status["engine"] in ("pyarrow", "fastparquet")


def test_frame_transport_names_the_fix_when_no_engine_is_installed(monkeypatch):
    import importlib.util
    from engine import bounded
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    status = bounded.frame_transport_status()
    assert status["ok"] is False
    assert "pyarrow" in status["label"] and "--reinstall" in status["fix"]


def test_health_publishes_the_universe_ranking_state():
    import app as webapp
    status = webapp._universe_ranking_status()
    assert status.get("ok") is True
    client = webapp.app.test_client()
    body = client.get("/api/health?probe=0").get_json()
    assert body["universe_ranking"]["ok"] is True


def test_the_server_warns_at_start_when_scans_cannot_rank(monkeypatch, caplog):
    import app as webapp
    monkeypatch.setattr(webapp, "_universe_ranking_status",
                        lambda: {"ok": False, "label": "the pyarrow package is not installed", "fix": "Run ./start.sh --reinstall"})
    with caplog.at_level("WARNING"):
        webapp._universe_ranking_startup_check()
    assert any("cannot rank names by liquidity" in r.getMessage() for r in caplog.records)
