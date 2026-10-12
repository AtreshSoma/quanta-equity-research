"""Each fixture must record the outcome that produced the path it took.

`SECEdgarParser.get_cik` has two paths. When `company_tickers.json` loads it
resolves the CIK from that map and fetches
`data.sec.gov/submissions/CIK<n>.json`; when the map is unavailable it falls
back to the legacy `cgi-bin/browse-edgar` endpoint.

The fixtures were recorded across both states: AAPL captured the map and the
modern submissions document, while ten others captured only the legacy CGI
call — because at their recording time the map request failed. That failure was
never written down, so replay saw "no recorded response", stubbed a 503, and
counted a miss even though the fallback it then took WAS recorded faithfully.

A fixture has to record the failure that shaped it, not just the successes.
That keeps replay hermetic without inventing data and without re-recording
market history that would drag today's filings into a run frozen to August.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden"
if str(GOLDEN) not in sys.path:
    sys.path.insert(0, str(GOLDEN))

import _recorder  # noqa: E402

CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
LEGACY_MARKER = "cgi-bin/browse-edgar"


def _sec_urls(rec) -> list[str]:
    return [k[2] for k in rec.calls if len(k) > 2 and "sec.gov" in str(k[2])]


@pytest.mark.parametrize("ticker", _recorder.available_fixtures())
def test_a_fixture_that_used_the_legacy_path_records_why(ticker):
    """Taking the fallback implies the map request happened and failed."""
    rec = _recorder.load_recording(ticker)
    urls = _sec_urls(rec)
    if not any(LEGACY_MARKER in u for u in urls):
        pytest.skip(f"{ticker} did not use the legacy CIK path")

    assert any(CIK_MAP_URL in u for u in urls), (
        f"{ticker} recorded the legacy CIK fallback but not the "
        f"company_tickers.json outcome that caused it"
    )


@pytest.mark.parametrize("ticker", _recorder.available_fixtures())
def test_every_recorded_sec_call_replays_without_touching_the_network(ticker):
    rec = _recorder.load_recording(ticker)
    for key in rec.calls:
        if len(key) > 2 and "sec.gov" in str(key[2]):
            assert rec.calls[key] is not None
