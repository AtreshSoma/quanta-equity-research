"""One FOMC calendar for every engine (engine/fomc.py)."""
from __future__ import annotations

from datetime import date

from _paths import ensure_paths

ensure_paths()

from engine import fomc  # noqa: E402


def test_next_meeting_after_the_october_2026_correction():
    assert fomc.next_meeting(date(2026, 10, 22)) == (date(2026, 10, 28), False)
    assert fomc.next_meeting(date(2026, 10, 29)) == (date(2026, 12, 9), False)


def test_published_days_are_wednesdays_and_eight_a_year():
    for year, days in fomc.OFFICIAL.items():
        parsed, estimated = fomc.dates_for(year)
        assert len(parsed) == 8 and not estimated
        assert all(d.weekday() == 2 for d in parsed), year


def test_years_past_the_calendar_are_marked_estimated():
    days, estimated = fomc.dates_for(fomc.LAST_OFFICIAL_YEAR + 1)
    assert estimated and len(days) == 8 and all(d.weekday() == 2 for d in days)


def test_both_engines_read_the_same_calendar():
    import SP_ELITE_PRO as pro
    import TradingAnalysis_ELITE as elite

    assert pro.FOMC_DATES_2026 == list(fomc.OFFICIAL[2026])
    research = elite.MacroEnvironmentAnalyzer._generate_fomc_calendar()
    assert "2026-10-28" in research and "2026-11-04" not in research
