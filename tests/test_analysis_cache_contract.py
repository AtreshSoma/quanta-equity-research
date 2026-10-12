"""Cache identity, stale retention and honest failure classification.

Audit findings F04 and F05.

F04 — two defects in the analysis result cache:
  * a normal (non-stale) lookup DELETED an expired entry, so the later
    `allow_stale=True` lookup that a provider outage depends on found nothing.
    The fallback path destroyed the very thing it existed to serve.
  * entries were keyed by ticker alone, so a cheap Fast Mode preview could be
    returned to satisfy a request for a full analysis.

F05 — an exhausted retry loop with no usable price reported
  `error_type="invalid_ticker"` and `retryable=False`, turning a temporary
  provider outage into a permanent verdict about the instrument.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    with webapp._analysis_result_lock:
        webapp._analysis_result_cache.clear()
    yield
    with webapp._analysis_result_lock:
        webapp._analysis_result_cache.clear()


def _age_entry(ticker: str, seconds: float, mode: str = "full") -> None:
    """Backdate a cached entry so it reads as expired."""
    with webapp._analysis_result_lock:
        for key, entry in webapp._analysis_result_cache.items():
            if entry.get("ticker", key.split("::")[0]) == ticker:
                entry["timestamp"] = time.time() - seconds


# ---------------------------------------------------------------------------
# F04a — an expired entry must survive for the outage fallback
# ---------------------------------------------------------------------------

def test_expired_entry_survives_a_normal_lookup_for_later_stale_use():
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL"}, mode="full")
    _age_entry("AAPL", webapp.ANALYSIS_RESULT_CACHE_SECONDS + 60)

    # The fresh lookup correctly reports a miss...
    assert webapp._get_cached_analysis("AAPL", mode="full") is None
    # ...but must not have destroyed the last-good payload.
    stale = webapp._get_cached_analysis("AAPL", mode="full", allow_stale=True)
    assert stale is not None
    assert stale["cache"]["stale"] is True


def test_last_good_results_are_dropped_only_past_the_retention_window():
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL"}, mode="full")
    _age_entry("AAPL", webapp.ANALYSIS_STALE_RETENTION_SECONDS + 60)

    webapp._get_cached_analysis("AAPL", mode="full")
    assert webapp._get_cached_analysis("AAPL", mode="full", allow_stale=True) is None


def test_retention_window_is_longer_than_the_freshness_window():
    assert webapp.ANALYSIS_STALE_RETENTION_SECONDS > webapp.ANALYSIS_RESULT_CACHE_SECONDS


# ---------------------------------------------------------------------------
# F04b — a preview must never satisfy a request for a full analysis
# ---------------------------------------------------------------------------

def test_fast_preview_does_not_satisfy_a_full_analysis_request():
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL"}, mode="fast")

    assert webapp._get_cached_analysis("AAPL", mode="full") is None


def test_full_analysis_does_satisfy_a_fast_preview_request():
    """A complete result is strictly better than the preview that was asked for."""
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL"}, mode="full")

    assert webapp._get_cached_analysis("AAPL", mode="fast") is not None


def test_preview_hits_are_labelled_with_their_mode():
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL"}, mode="fast")
    hit = webapp._get_cached_analysis("AAPL", mode="fast")

    assert hit["cache"]["mode"] == "fast"


def test_modes_are_cached_side_by_side_without_evicting_each_other():
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL", "n": 1}, mode="fast")
    webapp._set_cached_analysis("AAPL", {"ticker": "AAPL", "n": 2}, mode="full")

    assert webapp._get_cached_analysis("AAPL", mode="fast")["n"] == 1
    assert webapp._get_cached_analysis("AAPL", mode="full")["n"] == 2


# ---------------------------------------------------------------------------
# F05 — an outage is not a verdict about the ticker
# ---------------------------------------------------------------------------

def test_exhausted_retries_without_a_validation_verdict_are_retryable():
    detail = webapp._analyze_no_data_detail(
        "AAPL", attempts=3, validated_invalid=False
    )

    assert detail["error_type"] != "invalid_ticker"
    assert detail["retryable"] is True


def test_a_validated_invalid_ticker_stays_terminal():
    detail = webapp._analyze_no_data_detail(
        "ZZZZ", attempts=1, validated_invalid=True
    )

    assert detail["error_type"] == "invalid_ticker"
    assert detail["retryable"] is False


def test_unavailable_provider_and_invalid_instrument_are_distinct_statuses():
    unavailable = webapp._analyze_no_data_detail("AAPL", attempts=3,
                                                 validated_invalid=False)
    invalid = webapp._analyze_no_data_detail("ZZZZ", attempts=1,
                                             validated_invalid=True)

    assert unavailable["status"] != invalid["status"]


def test_no_data_detail_reports_the_attempt_count_it_is_based_on():
    detail = webapp._analyze_no_data_detail("AAPL", attempts=3,
                                            validated_invalid=False)
    assert detail["attempts"] == 3
