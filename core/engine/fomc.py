"""FOMC meeting calendar: one list for every engine.

Decision (second) day of each scheduled meeting, as published by the Federal
Reserve (federalreserve.gov/monetarypolicy/fomccalendars.htm, checked
2026-10-22; 2027 dates are tentative until each preceding meeting confirms
them). The scanner sizes new positions down near a meeting and the Research
engine reports the days to the next one, so both read this module.

Two copies used to disagree: the Research engine's "official" list had five
wrong dates (e.g. 2026-11-04 instead of 2026-10-28), so a week before a rate
decision it reported the meeting as two weeks away.

Years after the last published one fall back to the Fed's usual cadence and are
marked estimated, so no caller mistakes them for the calendar.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

OFFICIAL: Dict[int, Tuple[str, ...]] = {
    2025: ("2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
           "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"),
    2026: ("2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
           "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"),
    2027: ("2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
           "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08"),
}
TENTATIVE_YEARS = frozenset({2027})
LAST_OFFICIAL_YEAR = max(OFFICIAL)

# Fallback cadence: (month, nth Wednesday) of the decision day.
_CADENCE = ((1, 4), (3, 3), (4, 5), (6, 2), (7, 4), (9, 3), (10, 4), (12, 2))


def _nth_wednesday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    first_wed = first + timedelta(days=(2 - first.weekday()) % 7)
    day = first_wed + timedelta(weeks=n - 1)
    if day.month != month:  # no fifth Wednesday: the last one
        day -= timedelta(weeks=1)
    return day


def dates_for(year: int) -> Tuple[List[date], bool]:
    """(decision days, estimated?) for ``year``."""
    if year in OFFICIAL:
        return [datetime.strptime(d, "%Y-%m-%d").date() for d in OFFICIAL[year]], False
    return [_nth_wednesday(year, m, n) for m, n in _CADENCE], True


def all_dates(start_year: int, end_year: int) -> List[date]:
    out: List[date] = []
    for year in range(start_year, end_year + 1):
        out.extend(dates_for(year)[0])
    return sorted(out)


def next_meeting(today: Optional[date] = None) -> Tuple[Optional[date], bool]:
    """(next decision day on or after ``today``, estimated?)."""
    today = today or date.today()
    for year in (today.year, today.year + 1):
        days, estimated = dates_for(year)
        upcoming = [d for d in days if d >= today]
        if upcoming:
            return upcoming[0], estimated
    return None, True


def official_iso() -> List[str]:
    """Every published decision day, ISO strings, oldest first."""
    return [d for year in sorted(OFFICIAL) for d in OFFICIAL[year]]
