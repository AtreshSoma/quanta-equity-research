"""US equity trading calendar shared by every engine, the ledger and freshness checks.

A holding period stated as "9 days" is not actionable — a trader needs the DATE
to be out by. Converting one to the other means counting sessions, not calendar
days, which means knowing when the market is shut. The rules below are computed
rather than hardcoded so they do not silently go stale.

Moved here from ``SP_ShortTerm`` so the signal ledger (T+N measurement) and the
data-freshness gate use exactly the same notion of a "session" as the scanner.
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Optional

try:  # zoneinfo is stdlib on 3.9+, but tzdata can be missing on minimal images.
    from zoneinfo import ZoneInfo

    _NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 - fall back to a fixed EST offset
    _NY = timezone(timedelta(hours=-5))

# Regular session close, New York time. Half-days close earlier, which only
# matters for "is today's bar final yet", where erring late is the safe side.
SESSION_CLOSE = time(16, 0)


def easter_sunday(year: int) -> _date:
    """Easter Sunday for *year* (anonymous Gregorian algorithm).

    Needed only to locate Good Friday, the one NYSE holiday with no fixed date.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, day = divmod(h + lunar - 7 * m + 114, 31)
    return _date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _date:
    """The *n*-th *weekday* (0=Mon) of a month; n=-1 means the last one."""
    if n > 0:
        first = _date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    last_day = (_date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(day: _date) -> Optional[_date]:
    """Weekend-shift a fixed-date holiday to the day the NYSE actually closes."""
    if day.weekday() == 5:      # Saturday -> observed the preceding Friday
        return day - timedelta(days=1)
    if day.weekday() == 6:      # Sunday -> observed the following Monday
        return day + timedelta(days=1)
    return day


def us_market_holidays(year: int) -> set:
    """NYSE/Nasdaq full-day closures for *year*.

    Excludes half-days (the day after Thanksgiving, Christmas Eve), which are
    still sessions and still count toward a holding period.
    """
    holidays = {
        _nth_weekday(year, 1, 0, 3),    # MLK Jr. Day, 3rd Monday of January
        _nth_weekday(year, 2, 0, 3),    # Washington's Birthday, 3rd Monday Feb
        easter_sunday(year) - timedelta(days=2),   # Good Friday
        _nth_weekday(year, 5, 0, -1),   # Memorial Day, last Monday of May
        _nth_weekday(year, 9, 0, 1),    # Labor Day, 1st Monday of September
        _nth_weekday(year, 11, 3, 4),   # Thanksgiving, 4th Thursday
    }
    # New Year's Day is NOT observed on the preceding Friday when it falls on a
    # Saturday — the exchange simply stays open through year end.
    new_year = _date(year, 1, 1)
    if new_year.weekday() != 5:
        holidays.add(_observed(new_year))
    for fixed in (_date(year, 6, 19), _date(year, 7, 4), _date(year, 12, 25)):
        observed = _observed(fixed)
        if observed is not None:
            holidays.add(observed)
    return {h for h in holidays if h is not None}


_HOLIDAY_CACHE: Dict[int, set] = {}


def is_trading_day(day: _date) -> bool:
    """True when the US equity market holds a regular session on *day*."""
    if day.weekday() >= 5:
        return False
    if day.year not in _HOLIDAY_CACHE:
        _HOLIDAY_CACHE[day.year] = us_market_holidays(day.year)
    return day not in _HOLIDAY_CACHE[day.year]


def add_trading_days(start: _date, sessions: int) -> _date:
    """The date *sessions* trading days after *start*.

    ``sessions=0`` returns the next session on or after *start*, so a plan
    generated over a weekend still names a day the market is open.
    """
    day = start
    while not is_trading_day(day):
        day += timedelta(days=1)
    remaining = max(0, int(sessions))
    while remaining > 0:
        day += timedelta(days=1)
        if is_trading_day(day):
            remaining -= 1
    return day


def previous_trading_day(day: _date) -> _date:
    """The last session strictly before *day*."""
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def trading_days_between(start: _date, end: _date) -> int:
    """Sessions in the half-open interval (start, end]. Negative if end < start."""
    if end < start:
        return -trading_days_between(end, start)
    count, day = 0, start
    while day < end:
        day += timedelta(days=1)
        if is_trading_day(day):
            count += 1
    return count


def now_new_york(now: Optional[datetime] = None) -> datetime:
    """*now* (default: the wall clock) expressed in New York time."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_NY)


def last_completed_session(now: Optional[datetime] = None) -> _date:
    """The most recent session whose daily bar is final at *now*.

    Before 16:00 New York on a trading day, today's bar is still forming, so the
    last COMPLETED session is the previous one.
    """
    ny = now_new_york(now)
    today = ny.date()
    if is_trading_day(today) and ny.time() >= SESSION_CLOSE:
        return today
    return previous_trading_day(today)


def format_horizon(sessions: int, start: Optional[_date] = None) -> str:
    """Render a session count as "12 sessions (by Fri 18 Sep)"."""
    target = add_trading_days(start or datetime.now().date(), sessions)
    return f"{sessions} sessions (by {target:%a %d %b})"


# ── India (NSE / BSE) ───────────────────────────────────────────────────────
# A minimal session calendar for India listings: weekdays on the IST clock,
# less the NSE trading holidays below. The close is
# thresholds.RESEARCH.INDIA_SESSION_CLOSE_IST.

# India keeps no daylight saving, so a fixed offset is exact.
_IST = timezone(timedelta(hours=5, minutes=30))

INDIA_SUFFIXES = (".NS", ".BO")

# NSE equity trading holidays that fall on weekdays. REFRESH THIS LIST EVERY
# YEAR from the NSE holiday circular (published each December for the next
# year). 2026 is complete: it follows NSE's published 2026 calendar (circular
# of 12 December 2025, 12 weekday closures to Dussehra), cross-checked against
# two broker listings that agree on all 15 weekday dates. The Maharashtra
# municipal-election closure of 15 January 2026 appears in only some listings,
# is not in the circular and is past, so it is left out. Weekend holidays
# (Mahashivratri, Id-Ul-Fitr, Independence Day, Diwali Laxmi Pujan) close no
# extra weekday. 2027 still holds only the dates that are certain without the
# circular: the fixed-date national holidays and Good Friday (from Easter);
# its lunar-calendar closures (Holi, Ram Navami, Mahavir Jayanti, Id, Muharram,
# Ganesh Chaturthi, Dussehra, Diwali, Guru Nanak Jayanti ...) wait for the
# December 2026 circular. A missing holiday counts as a session, so a bar can
# only read one session older than it is (stale on the safe side), never fresher.
NSE_HOLIDAYS = frozenset({
    # 2026 (15 Feb, 21 Mar, 15 Aug and 8 Nov fall on weekends)
    _date(2026, 1, 26),     # Republic Day
    _date(2026, 3, 3),      # Holi
    _date(2026, 3, 26),     # Shri Ram Navami
    _date(2026, 3, 31),     # Shri Mahavir Jayanti
    _date(2026, 4, 3),      # Good Friday
    _date(2026, 4, 14),     # Dr. Baba Saheb Ambedkar Jayanti
    _date(2026, 5, 1),      # Maharashtra Day
    _date(2026, 5, 28),     # Bakri Id
    _date(2026, 6, 26),     # Muharram
    _date(2026, 9, 14),     # Ganesh Chaturthi
    _date(2026, 10, 2),     # Mahatma Gandhi Jayanti
    _date(2026, 10, 20),    # Dussehra
    _date(2026, 11, 10),    # Diwali-Balipratipada
    _date(2026, 11, 24),    # Prakash Gurpurb Sri Guru Nanak Dev
    _date(2026, 12, 25),    # Christmas
    # 2027 (1 May, 15 Aug, 2 Oct and 25 Dec fall on weekends)
    _date(2027, 1, 26),     # Republic Day
    _date(2027, 3, 26),     # Good Friday
    _date(2027, 4, 14),     # Dr. Baba Saheb Ambedkar Jayanti
})


def listing_market(ticker: Optional[str]) -> str:
    """'IN' for an NSE / BSE listing (by its .NS / .BO suffix), else 'US'."""
    return "IN" if str(ticker or "").strip().upper().endswith(INDIA_SUFFIXES) else "US"


def now_india(now: Optional[datetime] = None) -> datetime:
    """*now* (default: the wall clock) expressed in India time (IST)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_IST)


def is_india_trading_day(day: _date) -> bool:
    """True when NSE holds a regular session on *day* (see NSE_HOLIDAYS)."""
    return day.weekday() < 5 and day not in NSE_HOLIDAYS


def previous_india_trading_day(day: _date) -> _date:
    """The last India session strictly before *day*."""
    day -= timedelta(days=1)
    while not is_india_trading_day(day):
        day -= timedelta(days=1)
    return day


def india_trading_days_between(start: _date, end: _date) -> int:
    """India sessions in the half-open interval (start, end]. Negative if end < start."""
    if end < start:
        return -india_trading_days_between(end, start)
    count, day = 0, start
    while day < end:
        day += timedelta(days=1)
        if is_india_trading_day(day):
            count += 1
    return count


def last_completed_india_session(now: Optional[datetime] = None) -> _date:
    """The most recent India session whose daily bar is final at *now*.

    Before the IST close on a session day, today's bar is still forming, so the
    last COMPLETED session is the previous one.
    """
    from .thresholds import RESEARCH

    ist = now_india(now)
    today = ist.date()
    close_h, close_m = RESEARCH.INDIA_SESSION_CLOSE_IST
    if is_india_trading_day(today) and ist.time() >= time(int(close_h), int(close_m)):
        return today
    return previous_india_trading_day(today)
