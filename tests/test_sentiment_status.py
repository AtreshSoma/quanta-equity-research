"""An empty news result must say WHY it is empty.

Audit section 13: `SentimentAnalyzer.analyze` returned the same neutral,
all-default `SentimentData` whether the ticker genuinely had no news, every
provider was missing credentials, or every fetch raised. Downstream that reads
as "sentiment is neutral" — a measurement — when nothing was ever measured.

Optional data may fail without blocking the analysis, but its status has to
survive aggregation.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
MODULES = Path(__file__).resolve().parents[1] / "modules"
for _p in (CORE, MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ta = importlib.import_module("TradingAnalysis_ELITE")


def _analyzer(fetchers):
    sa = ta.SentimentAnalyzer.__new__(ta.SentimentAnalyzer)
    sa.api = None
    sa.vader = ta.SentimentIntensityAnalyzer()
    sa._fetchers = fetchers
    return sa


def _ok(_ticker, _seen):
    return [{"title": "Company beats estimates", "summary": "", "source": "X"}]


def _empty(_ticker, _seen):
    return []


def _boom(_ticker, _seen):
    raise RuntimeError("provider exploded")


def _no_credentials(_ticker, _seen):
    raise ta.MissingCredentialsError("no api key configured")


# ---------------------------------------------------------------------------
# The three empty cases are distinguishable
# ---------------------------------------------------------------------------

def test_genuinely_no_news_is_reported_as_no_news():
    out = _analyzer([_empty, _empty]).analyze("AAPL")

    assert out.status == "NO_NEWS"
    assert out.sources_attempted == 2
    assert out.sources_succeeded == 2


def test_every_provider_failing_is_reported_as_unavailable():
    out = _analyzer([_boom, _boom]).analyze("AAPL")

    assert out.status == "PROVIDER_UNAVAILABLE"
    assert out.sources_succeeded == 0


def test_missing_credentials_are_distinguished_from_a_dead_provider():
    out = _analyzer([_no_credentials, _no_credentials]).analyze("AAPL")

    assert out.status == "NOT_CONFIGURED"


def test_a_partial_failure_still_produces_a_result_but_records_the_gap():
    out = _analyzer([_ok, _boom]).analyze("AAPL")

    assert out.status == "PARTIAL"
    assert out.sources_attempted == 2
    assert out.sources_succeeded == 1


def test_a_clean_full_fetch_is_reported_as_ok():
    out = _analyzer([_ok, _ok]).analyze("AAPL")

    assert out.status == "OK"


# ---------------------------------------------------------------------------
# A non-measurement must not read as a neutral measurement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fetchers,expected", [
    ([_empty, _empty], "NO_NEWS"),
    ([_boom, _boom], "PROVIDER_UNAVAILABLE"),
    ([_no_credentials], "NOT_CONFIGURED"),
])
def test_empty_results_are_flagged_as_not_measured(fetchers, expected):
    out = _analyzer(fetchers).analyze("AAPL")

    assert out.status == expected
    assert out.measured is False


def test_a_real_reading_is_flagged_as_measured():
    assert _analyzer([_ok]).analyze("AAPL").measured is True


def test_status_survives_serialization_to_the_client():
    result = ta.AnalysisResult(ticker="TEST")
    result.sentiment = _analyzer([_boom]).analyze("AAPL")
    payload = ta._mcp_result_to_dict(result)

    assert payload["sentiment"]["status"] == "PROVIDER_UNAVAILABLE"
    assert payload["sentiment"]["measured"] is False


def test_batch_deadline_does_not_wait_for_running_provider():
    release = threading.Event()
    entered = threading.Event()
    def stalled(_ticker, _seen):
        entered.set()
        release.wait(2)
        return []
    sa = _analyzer([stalled])
    sa.BATCH_TIMEOUT_SECONDS = 0.05
    started = time.monotonic()
    try:
        out = sa.analyze("AAPL")
        assert entered.is_set()
        assert time.monotonic() - started < 1
        assert out.status == "PROVIDER_UNAVAILABLE"
        assert "timeout" in out.source_errors[0]
    finally:
        release.set()


@pytest.mark.parametrize("method,key", [("_fetch_newsapi", "news_api_key"),
                                      ("_fetch_finnhub", "finnhub_key"),
                                      ("_fetch_polygon", "polygon_key")])
def test_real_provider_adapters_distinguish_missing_keys_and_failed_responses(method, key):
    sa = _analyzer([_empty])
    sa.api = SimpleNamespace(**{key: ""})
    with pytest.raises(ta.MissingCredentialsError):
        getattr(sa, method)("AAPL", set())
    setattr(sa.api, key, "test-key")
    sa.api.fetch_json = lambda *args, **kwargs: None
    with pytest.raises(RuntimeError):
        getattr(sa, method)("AAPL", set())
