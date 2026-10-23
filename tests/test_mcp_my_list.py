"""The MCP server's My list tools read and write the web app's store."""
from __future__ import annotations

import importlib
import importlib.machinery
import sys
import types
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()

REPO = Path(__file__).resolve().parents[1]


class _FakeFastMCP:
    """Stands in for mcp.server.fastmcp.FastMCP: decorators return the function."""

    def __init__(self, *a, **k):
        pass

    def _passthrough(self, *a, **k):
        return lambda fn: fn

    tool = resource = prompt = _passthrough

    def run(self, *a, **k):  # pragma: no cover - never served in tests
        pass


@pytest.fixture
def server(monkeypatch, tmp_path):
    fake = types.ModuleType("mcp.server.fastmcp")
    fake.FastMCP = _FakeFastMCP
    for name, mod in (("mcp", types.ModuleType("mcp")), ("mcp.server", types.ModuleType("mcp.server")),
                      ("mcp.server.fastmcp", fake)):
        mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv("QUANTA_MYLIST_PATH", str(tmp_path / "list.sqlite3"))
    monkeypatch.syspath_prepend(str(REPO / "mcp_server"))
    sys.modules.pop("quanta_mcp_server", None)
    return importlib.import_module("quanta_mcp_server")


def test_tools_add_alert_list_and_remove(server):
    assert "added to" in server.my_list_add("msft")
    assert "already in" in server.my_list_add("MSFT")
    assert "not a supported symbol" in server.my_list_add("no way")
    out = server.set_price_alert("MSFT", "Below", 480, "Stop")
    assert out.startswith("Alert #") and "at or below 480.00" in out
    assert "Alert not set" in server.set_price_alert("MSFT", "sideways", 1)
    listing = server.my_list()
    assert "My list (1): MSFT" in listing and "(Stop)" in listing
    alert_id = int(out.split("#")[1].split(":")[0])
    assert "deleted" in server.delete_price_alert(alert_id)
    assert "removed from" in server.my_list_remove("MSFT")
    assert "My list (0): empty" in server.my_list()
