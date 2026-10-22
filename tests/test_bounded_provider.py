"""A01: provider work is owned and bounded.

A timed-out bulk download is killed and reaped before the scan falls back, no
second bulk stage runs after a timeout, and the scan records how its universe
was chosen (method, duration, cause). Synthetic child targets only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import SP_ELITE_PRO as sp  # noqa: E402
from engine import bounded  # noqa: E402


def test_a_timed_out_child_is_killed_before_run_returns(tmp_path):
    marker = str(tmp_path / "slow")
    t0 = time.time()
    res = bounded.run("_bounded_targets:slow", {"seconds": 3.0, "marker": marker}, budget=0.5)
    assert res.status == "timeout" and res.reaped
    assert time.time() - t0 < 3.0
    time.sleep(3.5)
    assert not Path(marker + ".finished").exists(), "the provider work kept running after the timeout"


def test_a_result_and_an_error_come_back_from_the_child():
    ok = bounded.run("_bounded_targets:frame", {"tickers": ["SYNA", "SYNB"]}, budget=60)
    assert ok.status == "ok" and set(ok.value.columns.get_level_values(0)) == {"SYNA", "SYNB"}
    bad = bounded.run("_bounded_targets:boom", {}, budget=60)
    assert bad.status == "error" and "429" in bad.error


def test_the_test_suite_default_target_refuses_network():
    res = bounded.run(bounded.bulk_target(), {"tickers": ["SYNA"], "period": "6mo"}, budget=60)
    assert res.status == "error" and "network forbidden" in res.error


@pytest.fixture
def system(monkeypatch):
    monkeypatch.setattr(sp.TradingConfig, "PREFILTER_TIMEOUT_SECONDS", 0.5)
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj._bulk_timed_out = False
    obj._universe_selection = {"method": None}
    return obj, [f"T{i:03d}" for i in range(300)]


def test_a_prefilter_timeout_is_recorded_and_blocks_a_second_bulk_stage(system, monkeypatch):
    obj, tickers = system
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:slow")
    out = obj.smart_prefilter(tickers, max_candidates=100)
    assert out == obj._even_sample(tickers, 100)
    sel = obj._universe_selection
    assert sel["method"] == "even_spread" and "budget" in sel["cause"]
    assert sel["seconds"] is not None and sel["budget_seconds"] == 0.5
    calls = []
    monkeypatch.setattr(bounded, "run", lambda *a, **k: calls.append(a) or bounded.Bounded("ok"))
    monkeypatch.setattr(sp.TradingConfig, "PREFETCH_MIN_UNIVERSE", 1)
    assert obj.prefetch_history(out) == 0
    assert calls == [], "no second bulk call after a timeout"


def test_a_successful_prefilter_is_liquidity_ranked(system, monkeypatch):
    obj, tickers = system
    monkeypatch.setattr(sp.TradingConfig, "PREFILTER_TIMEOUT_SECONDS", 60.0)
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:frame")
    out = obj.smart_prefilter(tickers, max_candidates=10)
    assert out == list(reversed(tickers))[:10], "most liquid first"
    sel = obj._universe_selection
    assert sel["method"] == "liquidity_ranked" and sel["cause"] is None
    assert sel["candidates"] == 300


def test_only_test_targets_may_replace_the_bulk_download(monkeypatch):
    monkeypatch.setenv("QUANTA_BULK_TARGET", "os:system")
    assert bounded.bulk_target() == bounded.DEFAULT_BULK_TARGET
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:frame")
    assert bounded.bulk_target() == "_bounded_targets:frame"
    monkeypatch.delenv("QUANTA_BULK_TARGET")
    assert bounded.bulk_target() == bounded.DEFAULT_BULK_TARGET
