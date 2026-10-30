"""Repository hygiene: the documentation index must only point at files git tracks.

A broad ``.gitignore`` pattern once hid a whole docs folder from git: the files
stayed on disk, so every link still worked locally, and they were missing from
the remote. Checking links against ``git ls-files`` (not the filesystem) catches
that on the machine that made the mistake.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r'\]\(([^)\s]+)\)|<img[^>]+src="([^"]+)"')


def _tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return set(out.splitlines()) or None


def _targets(doc: Path):
    for match in LINK.finditer(doc.read_text(encoding="utf-8")):
        target = match.group(1) or match.group(2)
        if re.match(r"^(https?:|mailto:|#)", target):
            continue
        path = target.split("#")[0]
        if path:
            yield (doc.parent / path).resolve().relative_to(ROOT).as_posix()


@pytest.mark.parametrize("doc", ["README.md", "docs/README.md"])
def test_documentation_links_point_at_tracked_files(doc):
    tracked = _tracked()
    if tracked is None:
        pytest.skip("not a git checkout")
    missing = sorted({t for t in _targets(ROOT / doc)
                      if t not in tracked and not any(f.startswith(t.rstrip("/") + "/") for f in tracked)})
    assert not missing, f"{doc} links to files git does not track: {missing}"


def test_runtime_log_ignores_do_not_hide_documentation():
    tracked = _tracked()
    if tracked is None:
        pytest.skip("not a git checkout")
    hidden = subprocess.run(["git", "check-ignore", "docs/logs/IMPLEMENTATION_LOG.md"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    assert not hidden, "docs/logs is ignored by .gitignore"


# A home-directory path names the machine's user. The repository is public, so
# published text (docs, the README, the page itself) must cite files by repo
# path or in words. Placeholders a test or an example uses on purpose are allowed.
HOME_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+([A-Za-z0-9._-]+)|/(?:home|Users)/([A-Za-z0-9._-]+)/")
PLACEHOLDER_USERS = {"you", "your-name", "username", "user", "name", "secret", "example"}


def test_published_text_names_no_home_directory():
    tracked = _tracked()
    if tracked is None:
        pytest.skip("not a git checkout")
    published = [f for f in tracked if f.endswith((".md", ".html", ".js", ".css"))
                 and not f.startswith(("tests/", "webapp/tests/"))]
    hits = []
    for f in published:
        text = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
        for m in HOME_PATH.finditer(text):
            if (m.group(1) or m.group(2)).lower() not in PLACEHOLDER_USERS:
                hits.append(f"{f}: {m.group(0)}")
    assert not hits, f"published files name a home directory: {hits}"
