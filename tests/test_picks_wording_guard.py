"""[Calls] Wording guard: the Picks page shows QUANTA's call (Buy, Accumulate,
Watchlist) and never the removed status words ("funded", "not funded", "set
aside", "on watch", "rated").

Two checks: every string the worker publishes for the page (rows, Method
lists, stage notes, warnings), and every user-facing string constant in the
server and engine code that writes them. Internal field names and values
(snake_case keys such as status="funded") may stay; log lines and docstrings
are not shown to the user.
"""
from __future__ import annotations

import ast
import re
import sys
import time
import types
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths()

import test_stock_picks_table as spt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# "pro-rated" (a horizon target scaled from a 12-month one) is not a status word.
BANNED = re.compile(r"(?<![\w-])(?:un)?(?:funded|set aside|on watch|rated)\b", re.I)
INTERNAL = re.compile(r"^[a-z0-9_]+$")          # a key or enum value, never shown as text
NOT_SHOWN = {"status", "signal", "gate", "url", "evidence", "research_evidence"}

# The functions and modules whose strings reach the Picks page.
APP_FUNCS = ("_picks_view", "_stock_picks_table", "_plain_why", "_run_stock_picks", "_terminal_stage",
             "_sec_contact_startup_check")
ENGINE_FILES = ("core/engine/corporate_events.py", "core/engine/blockers.py",
                "core/engine/valuation_view.py", "core/engine/why_picked.py")


def _strings(obj, key=None):
    if isinstance(obj, str):
        if key not in NOT_SHOWN and not INTERNAL.match(obj):
            yield key, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, k)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, key)


def _shown_constants(tree):
    """String constants that can reach the reader: not docstrings, not log calls,
    not dict keys or snake_case identifiers."""
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                skip.add(id(body[0].value))
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            base = getattr(getattr(fn, "value", None), "id", "")
            if base in ("logger", "logging", "log") or name in ("debug", "info", "warning", "error", "exception"):
                for sub in ast.walk(node):
                    skip.add(id(sub))
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    skip.add(id(k))
        if isinstance(node, ast.Subscript):
            skip.add(id(node.slice))
        if isinstance(node, ast.Compare):
            for sub in ast.walk(node):
                skip.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if not INTERNAL.match(node.value):
                yield node.lineno, node.value


def test_the_banned_words_are_caught_and_pro_rated_is_not():
    for bad in ("funded", "Not funded", "set aside", "On watch", "Rated stocks", "unfunded"):
        assert BANNED.search(bad), bad
    for ok in ("pro-rated to 63 sessions", "generated", "fund", "Watchlist", "Screened out"):
        assert not BANNED.search(ok), ok


def test_the_server_and_engine_strings_the_page_shows_use_the_calls():
    hits = []
    app_tree = ast.parse((ROOT / "webapp/app.py").read_text(encoding="utf-8"))
    for node in app_tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in APP_FUNCS:
            hits += [f"app.py:{ln} {s!r}" for ln, s in _shown_constants(node) if BANNED.search(s)]
    for rel in ENGINE_FILES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        hits += [f"{rel}:{ln} {s!r}" for ln, s in _shown_constants(tree) if BANNED.search(s)]
    assert not hits, "\n".join(hits)


@pytest.fixture
def job(monkeypatch):
    """The real worker on the synthetic book; keeps the whole job (result and stage)."""
    spt._install_fake_deep(monkeypatch, {"CSCO": "WATCHLIST"})
    eng = spt._bare_system(capital=spt.CAP, regime="BULL")
    eng.scan_results = [spt._row("AMZN", 8.1, 251.52, 1), spt._row("CSCO", 8.0, 70.2, 2), spt._row("GOOG", 7.9, 245.1, 3),
                        spt._row("GOOGL", 7.8, 246.0, 4), spt._row("MSFT", 7.7, 517.53, 5), spt._row("AVDX", 7.6, 99.4, 6),
                        spt._row("LOWR", 7.5, 50.0, 7, stop_pct=0.80)]
    eng._event_checker = spt._checker
    eng._last_universe_size = 7
    eng.scan_market = lambda **kw: list(eng.scan_results)
    eng.get_error_summary = lambda: {"records": [], "by_type": {}, "universe_size": 7, "analyzed_success_count": 7,
                                     "analysis_failed_count": 0, "exception_rate": 0.0}
    eng.explain_empty_scan = lambda: ""
    mod = types.ModuleType("SP_ELITE_PRO")
    mod.QuantaEliteTradingSystem = lambda capital, market: eng
    mod.ENHANCEMENTS_AVAILABLE = False
    mod.apply_allocation = spt.sp.apply_allocation
    mod.TradingConfig = spt.sp.TradingConfig
    sys.path.insert(0, str(ROOT / "webapp"))
    import app as webapp
    monkeypatch.setitem(sys.modules, "SP_ELITE_PRO", mod)
    key = "picks-wording"
    webapp._jobs[key] = {"type": "stock-picks", "status": "running", "progress": 0, "started_at": time.time(),
                         "worker_active": True}
    try:
        webapp._run_stock_picks(key, dict(capital=spt.CAP, market="US", scan_type="quick", custom_tickers=None,
                                          risk_profile="balanced", max_positions=5, diversify=True, safe_mode=False))
        assert webapp._jobs[key]["status"] == "complete", webapp._jobs[key].get("error")
        return dict(webapp._jobs[key])
    finally:
        webapp._jobs.pop(key, None)


def test_what_the_worker_publishes_for_the_page_uses_the_calls(job):
    res = job["result"]
    shown = {k: res.get(k) for k in ("stock_picks", "screened_out", "excluded_for_safety", "warnings",
                                     "empty_scan_explanation", "scan_note")}
    shown["stage"] = job.get("stage")
    shown["message"] = job.get("message")
    hits = [f"{k}: {s!r}" for k, s in _strings(shown) if BANNED.search(s)]
    assert not hits, "\n".join(hits)
    notes = [s.get("note") for s in (job.get("stage") or {}).get("stages", []) if s.get("note")]
    book = next(n for n in notes if "passed the screens" in n)
    assert re.fullmatch(r"\d+ passed the screens, \d+ Buy or Accumulate, \d+ Watchlist", book), book
