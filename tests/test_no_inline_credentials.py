"""Audit B-13: no email address or password literal in engine source.

core/Alpha_Picks_Engine.py carried an SMTP example block (an email address and a
password placeholder) for an alert method nothing calls. Credentials belong in
the environment; unused code that asks for them is removed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_KEYS = {"password", "passwd", "smtp_password", "app_password"}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_alpha_picks_engine_has_no_email_or_password_literal():
    path = CORE / "Alpha_Picks_Engine.py"
    tree = _tree(path)
    emails = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
              and isinstance(n.value, str) and EMAIL.search(n.value)]
    assert not emails, f"{len(emails)} email literal(s) in {path.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and str(key.value).lower() in SECRET_KEYS:
                    assert not (isinstance(value, ast.Constant) and value.value), \
                        f"literal secret for {key.value!r} at line {value.lineno}"


def test_the_unused_smtp_alert_path_is_gone():
    src = (CORE / "Alpha_Picks_Engine.py").read_text(encoding="utf-8")
    assert "smtp_config" not in src and "send_email_alert" not in src and "smtplib" not in src
