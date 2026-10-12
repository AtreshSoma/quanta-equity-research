"""Shared pytest configuration for the QUANTA test suite.

Import paths are supplied by ``pythonpath`` in pyproject.toml. This file adds a
defensive fallback for the case where pytest is invoked with a different
rootdir (from the repository root, or by an IDE that does not read the ini
file), plus the small number of fixtures that more than one test module needs.

Deliberately thin: fixtures nobody uses are worse than no fixtures, because
they still have to be read and maintained.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "golden"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _paths import CORE, MODULES, WEBAPP, ensure_paths  # noqa: E402

# Deep-confirm pauses before retrying a provider failure; tests use fakes, so
# never sleep. Must be set before SP_ELITE_PRO is imported (read at import).
import os  # noqa: E402
os.environ.setdefault("QUANTA_DEEP_CONFIRM_RETRY_WAIT", "0")

# Never let a test run read or write the production response cache. app.py
# loads <QUANTA_CACHE_DIR>/yf_cache.json at import and saves it at exit, and
# many tests import app. Set before anything imports it; forced, not
# setdefault, so a developer shell pointing at the real cache cannot leak in.
import tempfile  # noqa: E402
os.environ["QUANTA_CACHE_DIR"] = tempfile.mkdtemp(prefix="quanta-test-cache-")
os.environ["QUANTA_YF_DISK_CACHE"] = "0"
# The same for the Overview's day-over-day regime history (app.py upserts it).
os.environ["QUANTA_REGIME_HISTORY_PATH"] = os.path.join(os.environ["QUANTA_CACHE_DIR"], "regime_history.json")
# The suite never calls Yahoo's earnings calendar; tests that need it stub it.
os.environ.setdefault("QUANTA_EARNINGS_CALENDAR", "0")
# The provider pacer spaces live requests; fakes need no spacing. The bulk
# history prefetch issues a real yf.download for any universe of 20+ names,
# so unit tests with stubbed analyzers must never reach it. Tests of either
# feature set these values explicitly.
os.environ.setdefault("QUANTA_PACER_START", "0")
os.environ.setdefault("QUANTA_PACER_MIN", "0")
os.environ.setdefault("QUANTA_PACER_MAX", "0")
os.environ.setdefault("QUANTA_PREFETCH_MIN_UNIVERSE", "1000000")
# [A01] Bulk downloads run in a child process that monkeypatching cannot reach;
# the default target refuses, so no test can issue a real provider call.
os.environ.setdefault("QUANTA_BULK_TARGET", "_bounded_targets:refuse")
# [I1] The SEC contact resolver falls back to the Windows user scope and a .env
# file; tests read the process environment only, never the developer's own.
os.environ["QUANTA_SEC_ENV_FALLBACK"] = "0"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

# Fallback for invocations that bypass the ini `pythonpath`.
ensure_paths()


@pytest.fixture(autouse=True, scope="session")
def _isolated_ledger(tmp_path_factory):
    """Point the signal ledger at a throwaway file for the whole session.

    The old JSON tracker had no isolation: 498 of the 500 rows in the
    production history were written by golden-master runs. The ledger also
    refuses the production path under pytest on its own; this makes the
    location explicit and per-session.
    """
    path = tmp_path_factory.mktemp("ledger") / "signals.sqlite3"
    old = os.environ.get("QUANTA_LEDGER_PATH")
    os.environ["QUANTA_LEDGER_PATH"] = str(path)
    yield path
    if old is None:
        os.environ.pop("QUANTA_LEDGER_PATH", None)
    else:
        os.environ["QUANTA_LEDGER_PATH"] = old


def pytest_report_header(config) -> list[str]:  # noqa: ANN001
    """Show what the suite resolved, so a path problem is visible immediately.

    The five web-app test modules were silently dead for weeks because nothing
    surfaced that their target directory had moved. Printing the resolution
    makes the same class of breakage obvious on the first line of output.
    """
    webapp = str(WEBAPP) if WEBAPP else "NOT FOUND"
    try:
        from _recorder import available_fixtures

        golden = len(available_fixtures())
    except Exception:  # noqa: BLE001 - header must never break collection
        golden = 0
    return [
        f"core:    {CORE}",
        f"modules: {MODULES}",
        f"webapp:  {webapp}",
        f"golden fixtures: {golden}",
    ]


@pytest.fixture(autouse=True)
def _deterministic_random():
    """Seed the global RNGs before every test.

    The engine seeds its own Monte Carlo generator explicitly, but several
    analyzers and helpers reach for module-level `random`/`numpy.random`. An
    unseeded call there makes a failure reproduce only sometimes, which is far
    more expensive to chase than the same failure every run.
    """
    random.seed(0)
    try:
        import numpy as np

        np.random.seed(0)
    except Exception:  # noqa: BLE001
        pass
    yield


@pytest.fixture(autouse=True)
def _no_sec_network(request, monkeypatch):
    """Keep unit tests off SEC EDGAR (audit F1).

    The scanner's corporate-event gate calls ``engine.corporate_events.check_many``
    on every new-money candidate. Unit tests that build a book are about other
    gates, so the shared checker answers "clear" for every name. Tests of the gate
    itself inject their own checker or call ``corporate_events.check`` directly;
    ``@pytest.mark.real_event_gate`` leaves the default in place.
    """
    if request.node.get_closest_marker("real_event_gate"):
        yield
        return
    try:
        from engine import corporate_events as ce
    except Exception:  # noqa: BLE001 - engine not importable in this test
        yield
        return
    def _stub(symbols, **_):
        # Listings no event source covers keep their real (offline) answer, so
        # an India name reads "unavailable" in tests as it does on the page.
        return {s: (ce.check(s, None) if s.upper().endswith(tuple(ce.EVENTS.UNCOVERED_LISTINGS))
                    else ce.EventCheck("clear", note="stubbed in tests")) for s in symbols}

    monkeypatch.setattr(ce, "check_many", _stub)
    yield


@pytest.fixture(autouse=True)
def _no_news_network(monkeypatch):
    """Keep unit tests off Yahoo's RSS news feed: a fake ticker with no news
    would otherwise fall back to a live request (SP_ELITE_PRO._rss_news_items).
    Tests of the feed pass their own fetcher."""
    import sys
    sp = sys.modules.get("SP_ELITE_PRO")
    if sp is not None and hasattr(sp, "_rss_news_items"):
        monkeypatch.setattr(sp, "_rss_news_items", lambda *a, **kw: [])
    yield


@pytest.fixture(autouse=True)
def _fresh_market_trend_cache(monkeypatch):
    """Give every test an empty market-read trend cache (webapp ``app``).

    The market read caches each index's 50/200-DMA metrics for
    MARKET.TREND_CACHE_SECONDS. Without this, metrics one test fetched (on a
    runner that can reach the provider) served a later test that stubs
    ``_trend_metrics`` to return nothing, and its "no data" read came back with
    a measured trend.
    """
    import sys
    app = sys.modules.get("app")
    if app is not None and hasattr(app, "_trend_cache"):
        monkeypatch.setattr(app, "_trend_cache", {})
        monkeypatch.setattr(app, "_trend_inflight", {})
    yield


@pytest.fixture(autouse=True)
def _capture_quanta_logger(caplog):
    """Let ``caplog`` see records from the QUANTA logger.

    ``SP_ELITE_PRO.setup_logging`` sets ``propagate = False`` (providers add
    root handlers and every line printed twice), so caplog's root handler
    captured nothing. Attach it to the QUANTA logger for each test instead.
    """
    import logging

    quanta = logging.getLogger("QUANTA")
    quanta.addHandler(caplog.handler)
    try:
        yield
    finally:
        quanta.removeHandler(caplog.handler)


@pytest.fixture(scope="session")
def elite():
    """The engine module, imported once per session.

    Importing `TradingAnalysis_ELITE` costs a few seconds (it pulls in `ta`,
    yfinance and the optional module probes), so session scope matters.
    """
    import TradingAnalysis_ELITE

    return TradingAnalysis_ELITE


@pytest.fixture
def price_frame():
    """A deterministic 2-year OHLCV frame with a mild uptrend.

    For unit-testing indicator and risk code without any network. Returns a
    fresh copy per test so a mutating test cannot leak into the next one.
    """
    import numpy as np
    import pandas as pd

    periods = 504
    index = pd.date_range("2024-01-01", periods=periods, freq="B")
    rng = np.random.default_rng(42)
    # Geometric walk with positive drift, then OHLC derived from it so the
    # High >= max(Open, Close) >= Low invariant holds.
    steps = rng.normal(0.0004, 0.012, periods)
    close = 100.0 * np.exp(np.cumsum(steps))
    spread = close * 0.006
    frame = pd.DataFrame(
        {
            "Open": close - spread * rng.uniform(-1, 1, periods),
            "High": close + spread * rng.uniform(0.5, 1.5, periods),
            "Low": close - spread * rng.uniform(0.5, 1.5, periods),
            "Close": close,
            "Volume": rng.integers(5_000_000, 20_000_000, periods).astype(float),
        },
        index=index,
    )
    frame["High"] = frame[["Open", "High", "Close"]].max(axis=1)
    frame["Low"] = frame[["Open", "Low", "Close"]].min(axis=1)
    return frame


@pytest.fixture(autouse=True)
def _fresh_research_cache():
    """[F7] The session research cache is process-wide; tests start empty so
    one test's (fake) research verdict never answers another's."""
    from engine import research_cache
    research_cache.clear()
    yield
    research_cache.clear()


@pytest.fixture(autouse=True)
def _fresh_sec_page_cache():
    """[P2] Older EDGAR pages are cached process-wide (they never change in
    production); tests reuse page names with different contents, so each test
    starts empty."""
    from portfolio import sources
    sources._PAGE_CACHE.clear()
    yield
    sources._PAGE_CACHE.clear()
