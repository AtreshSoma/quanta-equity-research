"""The MCP server's JSON tools: decisions, grounded narration, plans, tail risk.

No network: the analyzer, price bars and returns are replaced with synthetic
data. The server module is imported with a stand-in FastMCP, as in
test_mcp_my_list.py.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

REPO = Path(__file__).resolve().parents[1]
GOLDEN_KO = REPO / "tests" / "golden" / "expected" / "KO.json"


class _FakeFastMCP:
    def __init__(self, *a, **k):
        pass

    def _passthrough(self, *a, **k):
        return lambda fn: fn

    tool = resource = prompt = _passthrough

    def run(self, *a, **k):  # pragma: no cover
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


@pytest.fixture
def ko_decision():
    return json.loads(GOLDEN_KO.read_text(encoding="utf-8"))["result"]["decision"]


def _bars(n=800, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    open_ = close * np.exp(rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": 1_000_000}, index=idx)


def test_get_decision_returns_json_with_grounded_interpretation(server, monkeypatch, ko_decision):
    monkeypatch.setattr(server, "_analysis_and_decision", lambda t, a=100000: (None, ko_decision))
    out = json.loads(server.get_decision("ko"))
    assert out["ticker"] == "KO"
    assert out["decision"]["label"] == ko_decision["label"]
    first = out["interpretation"]["sentences"][0]["text"]
    assert ko_decision["label"] in first and ko_decision["evidence"]["status_text"] in first


def test_get_decision_rejects_bad_ticker(server):
    assert json.loads(server.get_decision("not a ticker!"))["error"] == "invalid ticker"


def test_explain_and_verify(server, ko_decision):
    dj = json.dumps(ko_decision)
    ex = json.loads(server.explain_decision(dj))
    assert ex["self_check"]["grounded"] is True
    ok = json.loads(server.verify_narrative(f"Entry {ko_decision['levels']['entry']:.2f}.", dj))
    assert ok["grounded"] is True
    bad = json.loads(server.verify_narrative("Strong buy now, target 999.99.", dj))
    assert bad["grounded"] is False
    assert {f["kind"] for f in bad["findings"]} >= {"unsupported_number", "contradicts_label"}
    assert "error" in json.loads(server.verify_narrative("x", "{not json"))


def test_plan_check_tool(server, monkeypatch):
    monkeypatch.setattr(server, "_plan_filings", lambda sym, today: None)  # no SEC call in tests
    out = json.loads(server.plan_check("AAA", shares=10, entry=100, stop=95, target=112,
                                       account_value=50_000))
    assert len(out["fingerprint"]) == 16
    ids = {c["id"]: c["status"] for c in out["checks"]}
    assert ids["geometry"] == "PASS"
    assert ids["concentration_name"] == "SKIPPED"   # holdings are web-app only
    assert out["context"] == {"research_used": False, "holdings_connected": False}
    bad = json.loads(server.plan_check("AAA", shares=10, entry=100, stop=105, target=112))
    assert bad["overall"] == "BLOCK"
    assert "error" in json.loads(server.plan_check("AAA", shares=-1, entry=100, stop=95, target=110))


def test_plan_simulate_tool_uses_bars_and_is_deterministic(server, monkeypatch):
    fake_yf = types.ModuleType("yfinance")

    class _T:
        def __init__(self, sym):
            pass

        def history(self, **k):
            return _bars()

    fake_yf.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    a = json.loads(server.plan_simulate("AAA", entry=100, stop=95, target=110, paths=1000))
    b = json.loads(server.plan_simulate("AAA", entry=100, stop=95, target=110, paths=1000))
    assert a == b
    sim = a["simulation"]
    assert abs(sim["p_target"] + sim["p_stop"] + sim["p_timeout"] - 1) < 1e-3
    assert a["history"]["drift"] == "zero"
    assert "error" in json.loads(server.plan_simulate("AAA", 100, 95, 110, drift="up"))


def test_portfolio_tail_risk_never_reports_zero_for_unknown(server, monkeypatch):
    idx = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(1)
    series = {"AAA": pd.Series(rng.normal(0, 0.01, 300), index=idx),
              "BBB": pd.Series(rng.normal(0, 0.02, 300), index=idx),
              "CCC": pd.Series(rng.normal(0, 0.01, 10), index=idx[-10:])}
    monkeypatch.setattr(server.PortfolioRiskManager, "_get_historical_returns",
                        lambda self, t, d=252: series[t])
    out = json.loads(server.portfolio_tail_risk(json.dumps(
        [{"ticker": "AAA", "value": 6000}, {"ticker": "BBB", "value": 3000},
         {"ticker": "CCC", "value": 1000}])))
    assert out["historical_var"] > 0 and out["cvar"] >= out["historical_var"]
    assert "CCC" in out["excluded"]
    assert 0.85 < out["coverage"] < 0.95
    assert "error" in json.loads(server.portfolio_tail_risk("[]"))


def test_risk_check_uses_portfolio_limits_and_reports_var(server, monkeypatch):
    fake_yf = types.ModuleType("yfinance")

    class _T:
        def __init__(self, sym):
            pass

        def history(self, **k):
            return pd.DataFrame({"Close": [100.0]})

    fake_yf.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    monkeypatch.setattr(server, "_var_section", lambda pos: ["## Value at Risk", "- stub", ""])
    text = server.portfolio_risk_check(json.dumps(
        [{"ticker": "AAA", "shares": 150, "avg_cost": 100, "sector": "Tech"},
         {"ticker": "BBB", "shares": 850, "avg_cost": 100, "sector": "Energy"}]), 100000)
    assert "## Value at Risk" in text
    # 15% single name and 85% sector breach the 10% / 30% limits My Portfolio uses.
    assert "QUANTA limit 10%" in text and "QUANTA limit 30%" in text


def test_material_filings_rejects_a_bad_ticker_and_bad_days(server):
    assert json.loads(server.material_filings("../etc"))["error"] == "invalid ticker"
    assert "error" in json.loads(server.material_filings("AAPL", days=0))
    assert "error" in json.loads(server.material_filings("AAPL", days=True))


def test_material_filings_india_is_not_covered_and_makes_no_sec_call(server):
    out = json.loads(server.material_filings("RELIANCE.NS", days=30))
    assert out["status"] == "not_covered"
    assert out["covered"] is False
    assert out["recent"] == []
    assert out["days"] == 30
