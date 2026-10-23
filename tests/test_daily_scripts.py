"""The daily job scripts: every file they run exists, and no stray control characters."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "quanta_daily.ps1"
SH = ROOT / "scripts" / "quanta_daily.sh"


def test_scripts_have_no_control_characters():
    for path in (PS1, SH):
        text = path.read_text(encoding="utf-8")
        bad = [hex(ord(c)) for c in text if ord(c) < 32 and c not in "\t\r\n"]
        assert not bad, f"{path.name} contains {bad}"


def test_every_file_the_windows_job_runs_exists():
    text = PS1.read_text(encoding="utf-8")
    joined = re.findall(r'Join-Path \$Root "([^"]+\.py)"', text)
    assert "webapp\\briefing.py" in joined
    for rel in joined:
        assert (ROOT / rel.replace("\\", "/")).is_file(), rel
    for rel in re.findall(r'Join-Path \$coreDir "([^"]+\.py)"', text):
        assert (ROOT / "core" / rel).is_file(), rel


def test_every_file_the_bash_job_runs_exists():
    text = SH.read_text(encoding="utf-8")
    scripts = re.findall(r'"\$PY" ((?:webapp|core|scripts)/[\w/]+\.py)', text)
    assert "webapp/briefing.py" in scripts
    for rel in scripts:
        assert (ROOT / rel).is_file(), rel
