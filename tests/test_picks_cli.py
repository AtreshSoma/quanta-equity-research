"""webapp/picks_cli.py runs a Picks scan exactly as the page does.

The daily job ran only the short-term scanner, so no Picks track record built
up unless someone pressed Run. The command goes through the same endpoint, so
the same validation, worker, saved scan and ledger recording apply.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import picks_cli  # noqa: E402


def _client(monkeypatch, tmp_path):
    import app as webapp
    from test_elite_pro_review2_fixes import _pick
    from test_scan_accounting import _fake_engine_module
    recs = [_pick("SYNA", "Tech", 8.0, seed=1), _pick("SYNB", "Health", 7.5, seed=2)]
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", _fake_engine_module(recs))
    monkeypatch.setattr(webapp, "_LAST_SCAN_DIR", tmp_path, raising=False)
    return webapp.app.test_client()


def test_the_command_runs_the_page_scan_and_prints_the_book(monkeypatch, tmp_path, capsys):
    client = _client(monkeypatch, tmp_path)
    args = picks_cli.parse_args(["--scan", "custom", "--capital", "100000", "--max-positions", "2"])
    body = picks_cli.request_body(args)
    assert body == {"market": "US", "scan_type": "custom", "capital": 100000.0, "max_positions": 2,
                    "risk_profile": "balanced", "diversify": True}
    rc = picks_cli.run(args, client=client, sleep=lambda s: time.sleep(0.05))
    out = capsys.readouterr()
    # A custom scan needs tickers: the server's own validation answers, unchanged.
    assert rc == 2 and "custom scan requires at least one ticker" in out.err


def test_a_completed_scan_is_summarised(monkeypatch, tmp_path, capsys):
    client = _client(monkeypatch, tmp_path)
    args = picks_cli.parse_args(["--scan", "quick", "--capital", "100000", "--max-positions", "2"])
    rc = picks_cli.run(args, client=client, sleep=lambda s: time.sleep(0.05))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Picks scan started" in out and "Scan quick at" in out


def test_settings_fall_back_to_the_daily_job_environment(monkeypatch):
    monkeypatch.setenv("QUANTA_DAILY_CAPITAL", "5000")
    monkeypatch.setenv("QUANTA_DAILY_POSITIONS", "8")
    monkeypatch.setenv("QUANTA_DAILY_PICKS_SCAN", "comprehensive")
    a = picks_cli.parse_args([])
    assert (a.capital, a.max_positions, a.scan) == (5000.0, 8, "comprehensive")


def test_a_scan_that_never_finishes_times_out(capsys):
    class _Resp:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def get_json(self, silent=False):
            return self._body

    class _Stuck:
        def post(self, *a, **k):
            return _Resp(202, {"job_id": "x"})

        def get(self, *a, **k):
            return _Resp(200, {"status": "running", "message": "Analysing names"})
    t = iter(range(0, 10_000, 10))
    rc = picks_cli.run(picks_cli.parse_args(["--timeout", "30"]), client=_Stuck(),
                       sleep=lambda s: None, clock=lambda: next(t))
    assert rc == 1 and "did not finish" in capsys.readouterr().err


@pytest.mark.parametrize("result,expect", [
    ({"scan_type": "standard", "stock_picks": [], "accounting": {"universe": 250}}, "No Buy, Accumulate or Watchlist call"),
    ({"scan_type": "standard", "stock_picks": [{"ticker": "META", "allocation": 0, "reason": "R:R 0.87 below 1.80"}],
      "near_misses": [{"ticker": "LLY", "score": 8.1}]}, "Ranked high, stopped by one rule: LLY 81"),
])
def test_the_summary_says_what_happened(result, expect):
    assert any(expect in line for line in picks_cli.summary(result))
