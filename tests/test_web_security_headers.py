"""Production audit (October 2026) security quick wins for the web app.

* Every response carries a Content-Security-Policy that allows script only from
  this server (no inline script), plus the existing frame and sniffing guards.
* The page template has no inline script or inline event handler, so that
  policy holds.
* The thresholds JSON embedded in the page cannot close its <script> block.
* Feed links that are not absolute http(s) URLs never reach the page.
* API errors are JSON and never echo an exception's text.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from _paths import ensure_paths

ensure_paths(webapp=True)
import app as webapp  # noqa: E402

QUANTA_HEADERS = {"X-QUANTA-Request": "1"}


def _client():
    return webapp.app.test_client()


def test_pages_and_api_carry_the_content_security_policy():
    c = _client()
    for path in ("/", "/api/thresholds"):
        resp = c.get(path)
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src 'self'" in csp, path
        assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0], path
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_the_template_has_no_inline_script_or_handler():
    html = (Path(webapp.app.root_path) / webapp.app.template_folder / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, flags=re.S)
    for attrs, body in scripts:
        if "application/json" in attrs:
            continue
        assert "src=" in attrs and not body.strip(), f"inline script: {attrs!r}"
    assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html), "inline event handler in the template"


def test_static_scripts_set_no_inline_handlers():
    static = Path(webapp.app.static_folder)
    for js in static.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert not re.search(r"<[a-z][^<>]*\son(click|submit|load|error|input|change)=", text), js.name


def test_the_embedded_thresholds_cannot_close_their_script_block(monkeypatch):
    real = webapp._T.as_dict

    def poisoned():
        d = real()
        d["UI"] = dict(d["UI"], NOTE="</script><script>alert(1)</script>")
        return d

    monkeypatch.setattr(webapp._T, "as_dict", poisoned)
    html = _client().get("/").get_data(as_text=True)
    block = html.split('id="quanta-thresholds">', 1)[1].split("</script>", 1)[0]
    assert "<script" not in block and "</" not in block
    assert json.loads(block)["UI"]["NOTE"] == "</script><script>alert(1)</script>"


def test_only_http_links_survive_from_a_feed():
    assert webapp._safe_http_url("https://example.com/a?b=1") == "https://example.com/a?b=1"
    assert webapp._safe_http_url("http://example.com") == "http://example.com"
    for bad in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "//example.com", "/relative", "", None):
        assert webapp._safe_http_url(bad) == "", bad


def test_api_errors_are_json_without_internals():
    c = _client()
    resp = c.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.is_json and resp.get_json() == {"error": "Not found."}
    resp = c.get("/api/stock-picks")  # POST-only
    assert resp.status_code == 405 and resp.is_json


def test_a_failed_cache_clear_does_not_echo_the_exception(monkeypatch):
    def boom(persist=True):
        raise RuntimeError("C:/Users/secret/path/cache.db is locked")

    monkeypatch.setitem(webapp.__dict__, "_yf_cache_clear", boom)
    resp = _client().post("/api/cache/clear", headers=QUANTA_HEADERS)
    assert resp.status_code == 500
    assert "secret" not in resp.get_data(as_text=True)


def test_the_footer_build_label_comes_from_the_deploy_or_git(tmp_path, monkeypatch):
    """Audit A-05: no version is typed in the page; the build is QUANTA_VERSION,
    else the checked-out commit, else nothing."""
    monkeypatch.setenv("QUANTA_VERSION", "2026.10.1")
    assert webapp._build_label() == "2026.10.1"
    monkeypatch.delenv("QUANTA_VERSION")
    repo = tmp_path / "repo"
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / ".git" / "refs" / "heads" / "main").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    nested = repo / "ui" / "webapp"
    nested.mkdir(parents=True)
    monkeypatch.setattr(webapp, "WEBAPP_DIR", nested)
    assert webapp._build_label() == "build 0123456"
    monkeypatch.setattr(webapp, "WEBAPP_DIR", tmp_path / "nowhere")
    assert webapp._build_label() == ""
    html = _client().get("/").get_data(as_text=True)
    assert "{{" not in html and "{%" not in html
