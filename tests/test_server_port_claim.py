"""The QUANTA server must own its port, or a page can talk to the wrong process.

2026-09-28: "Connect Robinhood" reported "Cannot reach the QUANTA server". The
connect route itself worked; the page had outlived the server it was loaded
from. On Windows that is easy to get into and hard to see, because werkzeug
binds with SO_REUSEADDR, and on Windows that option lets a SECOND process bind
a port another server is already listening on. Both print "Running on ...",
and which one answers is undefined — an old checkout (with no /api/portfolio)
or a server about to be closed can silently shadow the real one.

These tests pin the fix: the server claims its port exclusively and refuses to
start, with a clear message, when the port is already taken.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _reuse_listener(port: int) -> socket.socket:
    """A listener bound the way an older QUANTA (plain werkzeug) binds."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


def test_port_in_use_is_reported_not_shared():
    port = _free_port()
    other = _reuse_listener(port)
    try:
        assert webapp._port_in_use("127.0.0.1", port) is True
    finally:
        other.close()
    assert webapp._port_in_use("127.0.0.1", port) is False


def test_main_refuses_to_start_on_a_taken_port(monkeypatch, capsys):
    port = _free_port()
    other = _reuse_listener(port)
    monkeypatch.setenv("QUANTA_PORT", str(port))
    monkeypatch.setenv("QUANTA_NO_BROWSER", "1")
    ran = []
    monkeypatch.setattr(webapp.app, "run", lambda *a, **k: ran.append(k))
    try:
        with pytest.raises(SystemExit) as exc:
            webapp.main()
    finally:
        other.close()
    assert exc.value.code == 1
    assert ran == [], "the server must not start next to another listener"
    err = capsys.readouterr().err
    assert f"127.0.0.1:{port}" in err and "already" in err.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="SO_EXCLUSIVEADDRUSE is Windows-only")
def test_running_server_cannot_be_shadowed_on_windows():
    webapp._claim_ports_exclusively()
    port = _free_port()
    srv = make_server("127.0.0.1", port, webapp.app, threaded=True)
    try:
        intruder = socket.socket()
        intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            intruder.bind(("127.0.0.1", port))
        intruder.close()
    finally:
        srv.server_close()
    # ...and the port is immediately reusable once the server stops.
    again = make_server("127.0.0.1", port, webapp.app, threaded=True)
    again.server_close()
