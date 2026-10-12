"""Fill times reach the Activity ledger in UTC, so the page can show market time."""
from _paths import ensure_paths

ensure_paths()

from portfolio.insights import _utc_iso  # noqa: E402


def test_fill_times_are_utc_iso():
    assert _utc_iso("2026-09-23T14:31:02.123456Z") == "2026-09-23T14:31:02Z"
    assert _utc_iso("2026-09-23T10:31:02-04:00") == "2026-09-23T14:31:02Z"


def test_a_time_without_a_zone_is_passed_through_to_the_second():
    assert _utc_iso("2026-09-23T14:31:02.5") == "2026-09-23T14:31:02"
    assert _utc_iso("not a time, but a long string") == "not a time, but a l"
