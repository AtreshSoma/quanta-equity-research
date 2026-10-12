"""P1: the Research check reports its final count, across the first pass and
the backfill, so a finished scan never shows "Research check: 2 of 3".

The engine reported ``_i`` of ``n`` before each pick and never ``n`` of ``n``;
the backfill's second pass then overwrote the totals. Synthetic names only.
"""
from __future__ import annotations

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402

from test_elite_pro_review2_fixes import _bare_system, _install_fake_deep, _pick  # noqa: E402


def _run(monkeypatch, verdicts, names):
    _install_fake_deep(monkeypatch, verdicts)
    sysm = _bare_system()
    seen = []
    sysm._progress_cb = lambda stage, done, total: seen.append((stage, done, total))
    sysm._event_checker = lambda symbols, **_: {s: ce.EventCheck("clear", cik=i + 1) for i, s in enumerate(symbols)}
    sysm.scan_results = [_pick(t, f"S{i}", 8.0 - i * 0.1, seed=i + 1) for i, t in enumerate(names)]
    sysm.get_top_recommendations(max_positions=3)
    _run.seen = seen
    return [(d, t) for s, d, t in seen if s == "research_check"]


def test_the_research_check_ends_at_n_of_n(monkeypatch):
    rc = _run(monkeypatch, {}, ["AAA", "BBB", "CCC"])
    assert rc[-1] == (3, 3), rc
    assert rc == sorted(rc), "counts never move backwards"


def test_the_backfill_adds_to_the_totals_instead_of_resetting_them(monkeypatch):
    rc = _run(monkeypatch, {"AAA": "WATCHLIST"}, ["AAA", "BBB", "CCC", "DDD"])
    done, total = rc[-1]
    assert done == total == 4, rc          # 3 in the first pass, 1 backfilled
    assert all(d <= t for d, t in rc)


def test_the_book_reports_its_final_count_before_the_research_check(monkeypatch):
    _run(monkeypatch, {}, ["AAA", "BBB", "CCC"])
    stages = [(s, d, t) for s, d, t in _run.seen if s in ("book", "research_check")]
    first_rc = next(i for i, x in enumerate(stages) if x[0] == "research_check")
    assert stages[first_rc - 1] == ("book", 3, 3), stages
    assert all(x[0] == "research_check" for x in stages[first_rc:]), "no stage flips back"


def test_a_book_held_entirely_by_the_event_gate_still_ends_at_n_of_n(monkeypatch):
    from test_event_held_rows import _book, _five
    seen = []
    import test_elite_pro_review2_fixes as h
    real = h._bare_system

    def spy(*a, **kw):
        s = real(*a, **kw)
        s._progress_cb = lambda stage, d, t: seen.append((stage, d, t))
        return s
    monkeypatch.setattr("test_event_held_rows._bare_system", spy)
    _book(monkeypatch, _five())
    book = [(d, t) for s, d, t in seen if s == "book"]
    assert book[-1] == (5, 5), book
