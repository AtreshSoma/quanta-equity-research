"""Record and replay the engine's external I/O so analysis becomes deterministic.

Why this exists
---------------
`QuantaEliteAnalyzer.analyze()` is the thing every later phase of the
recalibration plan modifies. To change it safely we need to be able to run it
on fixed inputs and diff the output. That requires cutting every external
dependency, of which there are four:

1. ``yf.Ticker`` -- 68 construction sites across the tree. Eleven properties
   and two methods are actually read (see ``TICKER_PROPERTIES`` /
   ``TICKER_METHODS``).
2. ``APIClient.fetch_json`` -- the choke point for the REST providers the
   engine itself calls (NewsAPI, Finnhub, Polygon, FMP, Alpha Vantage).
3. ``requests.sessions.Session.request`` -- the *real* transport funnel. Every
   outbound HTTP call in the process passes through it, including
   ``requests.get()``, which is just a one-shot Session.
4. Wall-clock time -- the engine reads ``date.today()`` in earnings-proximity
   and staleness logic, so a recording replayed tomorrow would drift.

Seam 3 was missing from the first version of this harness, and its absence was
not theoretical. ``modules/catalyst_scanner.py``,
``modules/catalyst_intelligence_engine.py`` and ``modules/sec_edgar_parser.py``
call ``requests.get`` directly against SEC EDGAR, FDA and ClinicalTrials.gov,
bypassing ``APIClient`` entirely. Those calls ran LIVE during replay, so
``catalyst_score`` varied with whatever the SEC returned that minute -- and
because ``has_catalyst`` switches on jump diffusion in the Monte Carlo
simulator, a single unrecorded HTTP call moved fifteen fields of the snapshot.
The suite passed or failed depending on network weather. Patching the transport
funnel closes it for every consumer, present and future.

Recording captures 1-3 keyed by call signature and stamps the date for 4.
Replay serves from the recording. An unrecorded yfinance read is loud (it is
counted as a miss and asserted on), while an unrecorded HTTP call returns a
deterministic empty 503 -- see ``_StubResponse`` for why a stub is right there
and a raise is not.
"""

from __future__ import annotations

import gzip
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from unittest import mock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
EXPECTED_DIR = Path(__file__).resolve().parent / "expected"

_SECRET_NAMES = {"apikey", "api_key", "token", "access_token", "authorization", "password", "secret"}


def _safe_url(url: str) -> str:
    parts = urlsplit(str(url))
    if not any(k.lower() in _SECRET_NAMES for k, _ in parse_qsl(parts.query)):
        return str(url)
    query = urlencode([(k, "<redacted>" if k.lower() in _SECRET_NAMES else v)
                       for k, v in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _safe_error(exc) -> str:
    return re.sub(r"(?i)((?:api_?key|access_token|token|password|secret)=)[^&\s]+",
                  r"\1<redacted>", str(exc))

# yfinance surface actually read by core/TradingAnalysis_ELITE.py.
TICKER_PROPERTIES = (
    "info",
    "news",
    "calendar",
    "dividends",
    "options",
    "financials",
    "quarterly_financials",
    "balance_sheet",
    "earnings_history",
    "insider_transactions",
    "fast_info",
)
TICKER_METHODS = ("history", "option_chain")

# Sentinel stored when a real access raised, so replay can reproduce the
# failure rather than papering over it with an empty default.
@dataclass(frozen=True)
class RaisedError:
    type_name: str
    message: str


@dataclass(frozen=True)
class _CapturedResponse:
    """The parts of a requests.Response that matter, made picklable.

    A live Response holds a socket-backed urllib3 connection and cannot be
    pickled, so the fields consumers actually read are copied out.
    """

    status_code: int
    text: str
    content: bytes
    headers: dict
    url: str

    @classmethod
    def of(cls, response) -> "_CapturedResponse":  # noqa: ANN001
        try:
            body = response.content or b""
        except Exception:  # noqa: BLE001
            body = b""
        return cls(
            status_code=int(getattr(response, "status_code", 0) or 0),
            text=getattr(response, "text", "") or "",
            content=body,
            headers={k: v for k, v in (getattr(response, "headers", {}) or {}).items()
                     if k.lower() in {"content-type", "content-encoding", "retry-after"}},
            url=_safe_url(getattr(response, "url", "") or ""),
        )

    def to_response(self):  # noqa: ANN201
        import requests

        out = requests.Response()
        out.status_code = self.status_code
        out._content = self.content
        out.url = self.url
        out.headers.update(self.headers)
        return out


@dataclass
class Recording:
    """Captured I/O for one analysis run."""

    ticker: str
    # ("ticker", symbol, attr) -> value           (property access)
    # ("ticker", symbol, attr, argkey) -> value   (method call)
    # ("http", url, paramkey) -> value            (APIClient.fetch_json)
    calls: dict[tuple, Any] = field(default_factory=dict)
    recorded_at: str = ""  # ISO date; replay freezes the clock here
    misses: list[tuple] = field(default_factory=list)


def _picklable(name: str, value: Any) -> Any:
    """Convert a captured value into something that survives pickling.

    yfinance's ``fast_info`` is a lazy proxy holding a requests session and a
    thread lock, so pickling it raises ``TypeError: cannot pickle
    \'_thread.RLock\' object``. Flattening it to a plain dict keeps the data and
    drops the machinery -- and a dict still satisfies the ``.get()`` access the
    engine uses.
    """
    if name != "fast_info" or value is None or isinstance(value, dict):
        return value

    flattened: dict[str, Any] = {}
    try:
        keys = list(value.keys()) if hasattr(value, "keys") else []
    except Exception:  # noqa: BLE001
        keys = []
    for key in keys:
        try:
            item = value[key]
        except Exception:  # noqa: BLE001
            continue
        # Keep only plainly serialisable scalars; anything else is machinery.
        if item is None or isinstance(item, (bool, int, float, str)):
            flattened[key] = item
    # Attribute spellings some yfinance versions expose instead of mapping keys.
    for attr in ("market_cap", "last_price", "previous_close", "shares"):
        if attr not in flattened:
            try:
                item = getattr(value, attr, None)
            except Exception:  # noqa: BLE001
                continue
            if item is None or isinstance(item, (bool, int, float, str)):
                flattened[attr] = item
    return flattened


def _argkey(args: tuple, kwargs: dict) -> str:
    """Stable key for a call signature.

    Sorted kwargs so that ``history(period='1y')`` and the same call written
    with a different dict ordering share one fixture entry.
    """
    safe = {k: "<redacted>" if str(k).lower() in _SECRET_NAMES and v else v
            for k, v in kwargs.items()}
    return repr((args, tuple(sorted(safe.items()))))


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class _RecordingTicker:
    """Wraps a real ``yf.Ticker`` and records everything the engine reads."""

    def __init__(self, symbol: str, recording: Recording, real_factory: Callable):
        self._symbol = str(symbol)
        self._rec = recording
        self._real = real_factory(symbol)

    def __getattr__(self, name: str) -> Any:
        if name in TICKER_METHODS:
            def _method(*args, **kwargs):
                key = ("ticker", self._symbol, name, _argkey(args, kwargs))
                if key not in self._rec.calls:
                    try:
                        self._rec.calls[key] = getattr(self._real, name)(*args, **kwargs)
                    except Exception as exc:  # noqa: BLE001 - recorded verbatim
                        self._rec.calls[key] = RaisedError(type(exc).__name__, _safe_error(exc))
                value = self._rec.calls[key]
                if isinstance(value, RaisedError):
                    raise RuntimeError(f"{value.type_name}: {value.message}")
                return value

            return _method

        key = ("ticker", self._symbol, name)
        if key not in self._rec.calls:
            try:
                self._rec.calls[key] = _picklable(name, getattr(self._real, name))
            except Exception as exc:  # noqa: BLE001
                self._rec.calls[key] = RaisedError(type(exc).__name__, _safe_error(exc))
        value = self._rec.calls[key]
        if isinstance(value, RaisedError):
            raise RuntimeError(f"{value.type_name}: {value.message}")
        return value


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


_EMPTY_FOR_PROPERTY: dict[str, Any] = {
    "info": {},
    "news": [],
    "options": (),
}


class _ReplayTicker:
    """Serves recorded values. Records misses instead of hitting the network."""

    def __init__(self, symbol: str, recording: Recording, strict: bool):
        self._symbol = str(symbol)
        self._rec = recording
        self._strict = strict

    def _miss(self, key: tuple, default: Any) -> Any:
        self._rec.misses.append(key)
        if self._strict:
            raise KeyError(
                f"No recorded value for {key}. The fixture is stale or the code "
                f"reads something new -- re-record with "
                f"`python tests/golden/record.py {self._symbol}`."
            )
        return default

    def __getattr__(self, name: str) -> Any:
        if name in TICKER_METHODS:
            def _method(*args, **kwargs):
                key = ("ticker", self._symbol, name, _argkey(args, kwargs))
                if key in self._rec.calls:
                    value = self._rec.calls[key]
                    if isinstance(value, RaisedError):
                        raise RuntimeError(f"{value.type_name}: {value.message}")
                    return value
                import pandas as pd

                return self._miss(key, pd.DataFrame())

            return _method

        key = ("ticker", self._symbol, name)
        if key in self._rec.calls:
            value = self._rec.calls[key]
            if isinstance(value, RaisedError):
                raise RuntimeError(f"{value.type_name}: {value.message}")
            return value
        return self._miss(key, _EMPTY_FOR_PROPERTY.get(name))


# ---------------------------------------------------------------------------
# Patch context managers
# ---------------------------------------------------------------------------


def _target_modules() -> list[Any]:
    """Modules whose ``yf`` global must be patched.

    Patching ``yfinance.Ticker`` itself is not enough to be safe: any module
    that did ``from yfinance import Ticker`` would keep its own reference. In
    practice every call site uses the ``yf.Ticker`` form, so patching the
    attribute on the shared module object covers them all -- but we patch the
    engine module's own ``yf`` name too, defensively.
    """
    import yfinance

    mods = [yfinance]
    try:
        import TradingAnalysis_ELITE as elite

        if getattr(elite, "yf", None) is not None and elite.yf is not yfinance:
            mods.append(elite.yf)
    except Exception:  # noqa: BLE001 - engine not importable in some contexts
        pass
    return mods


class _StubResponse:
    """A minimal requests.Response stand-in for an unrecorded call.

    Replay must be hermetic, but a hard raise is wrong here: the modules that
    reach the network directly (`catalyst_scanner`, `catalyst_intelligence_engine`,
    `sec_edgar_parser`) all wrap their calls in try/except and degrade to "no
    catalyst found". Returning an empty 503 reproduces the offline behaviour
    those modules are written to handle, deterministically, instead of throwing
    an exception type they never expect.
    """

    status_code = 503
    text = ""
    content = b""
    headers: dict[str, str] = {}
    ok = False
    reason = "golden-replay: no recorded response"
    url = ""

    def json(self):  # noqa: ANN201
        return {}

    def raise_for_status(self) -> None:
        import requests

        raise requests.HTTPError(self.reason, response=self)

    def iter_lines(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _BlockedSession:
    """A requests.Session stand-in that fails only on actual transport.

    `APIClient.__init__` builds a session and configures `.headers` and
    adapters, so the object has to behave like a session everywhere *except*
    the methods that would put bytes on the wire. Raising on construction or
    on `.headers` would block the engine from starting; raising on `.get`
    catches the thing we actually care about -- I/O that bypassed
    `fetch_json` and therefore was never recorded.
    """

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.headers: dict[str, str] = {}
        self.params: dict[str, str] = {}
        self.adapters: dict[str, Any] = {}
        self.verify = True
        self.trust_env = True

    def mount(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def close(self) -> None:
        return None

    def _blocked(self, method: str, url: Any) -> None:
        raise AssertionError(
            f"Un-recorded HTTP {method} during replay: {url}. Every outbound "
            "request must pass through APIClient.fetch_json so the recorder "
            "can capture it. If a new provider was added, route it through "
            "fetch_json and re-record."
        )

    def get(self, url, *args, **kwargs):  # noqa: ANN001
        self._blocked("GET", url)

    def post(self, url, *args, **kwargs):  # noqa: ANN001
        self._blocked("POST", url)

    def request(self, method, url, *args, **kwargs):  # noqa: ANN001
        self._blocked(str(method).upper(), url)


def _isolated_account_patches(elite) -> list:
    """Fixtures describe public research, independent of local account files.

    Disable persistence and asynchronous outcome lookups so neither recording
    nor replay can modify user history or continue I/O after the context exits.
    """
    from sec_edgar_parser import SECEdgarParser

    return [
        mock.patch.object(elite.QuantaEliteAnalyzer, "_load_tracked_positions", return_value=[]),
        mock.patch.object(SECEdgarParser, "_cik_cache", {}),
        mock.patch.object(SECEdgarParser, "_cik_map_loaded", False),
        mock.patch.object(SECEdgarParser, "_filings_cache", {}),
        mock.patch.object(SECEdgarParser, "_consecutive_failures", 0),
        mock.patch.object(SECEdgarParser, "_circuit_open_until", 0.0),
        # The rate limiter's last-request stamp is wall-clock and process-wide.
        # Fixtures are frozen to their own recording dates (AAPL 2026-09-07,
        # JPM 2026-08-27), so a stamp carried from one test into an earlier one
        # made the gap negative and the limiter slept for the difference —
        # eleven days, while holding the CIK-map lock. Every later ticker then
        # blocked behind it and the suite hung.
        mock.patch.object(SECEdgarParser, "_last_request_time", 0.0),
    ]


def record_io(ticker: str) -> tuple[Recording, Any]:
    """Return (recording, context_manager) that captures live I/O for `ticker`."""
    from datetime import date

    import yfinance

    rec = Recording(ticker=ticker, recorded_at=date.today().isoformat())
    real_factory = yfinance.Ticker

    def _factory(symbol, *args, **kwargs):  # noqa: ANN001
        return _RecordingTicker(symbol, rec, real_factory)

    patches = [mock.patch.object(m, "Ticker", _factory) for m in _target_modules()]

    import TradingAnalysis_ELITE as elite

    patches.extend(_isolated_account_patches(elite))

    real_fetch = elite.APIClient.fetch_json

    def _fetch(self, url, params=None, **kwargs):  # noqa: ANN001
        key = ("http", _safe_url(url), _argkey((), params or {}))
        if key not in rec.calls:
            try:
                rec.calls[key] = real_fetch(self, url, params=params, **kwargs)
            except Exception as exc:  # noqa: BLE001
                rec.calls[key] = RaisedError(type(exc).__name__, _safe_error(exc))
        value = rec.calls[key]
        if isinstance(value, RaisedError):
            raise RuntimeError(f"{value.type_name}: {value.message}")
        return value

    patches.append(mock.patch.object(elite.APIClient, "fetch_json", _fetch))

    # Every outbound HTTP call in the process funnels through
    # requests.sessions.Session.request -- including requests.get(), which is
    # just a one-shot Session. Recording there catches the modules that bypass
    # APIClient entirely (catalyst_scanner, catalyst_intelligence_engine and
    # sec_edgar_parser all call requests.get directly against SEC EDGAR, FDA and
    # ClinicalTrials.gov). Without this the fixture is not hermetic and those
    # calls run live on replay, which made catalyst_score -- and through
    # has_catalyst, the whole Monte Carlo distribution -- non-deterministic.
    import requests.sessions

    real_request = requests.sessions.Session.request

    def _request(self, method, url, **kwargs):  # noqa: ANN001
        key = ("request", str(method).upper(), _safe_url(url),
               _argkey((), kwargs.get("params") or {}))
        if key not in rec.calls:
            try:
                response = real_request(self, method, url, **kwargs)
                rec.calls[key] = _CapturedResponse.of(response)
            except Exception as exc:  # noqa: BLE001
                rec.calls[key] = RaisedError(type(exc).__name__, _safe_error(exc))
        value = rec.calls[key]
        if isinstance(value, RaisedError):
            raise RuntimeError(f"{value.type_name}: {value.message}")
        return value.to_response()

    patches.append(mock.patch.object(requests.sessions.Session, "request", _request))
    patches.extend(_offline_event_patches())

    class _Ctx:
        def __enter__(self):
            for p in patches:
                p.start()
            return rec

        def __exit__(self, *exc):
            for p in reversed(patches):
                p.stop()
            return False

    return rec, _Ctx()


def _offline_event_patches() -> list:
    """[F7] SEC EDGAR is not part of a recording. The corporate-event check gets
    the same offline answer when recording and when testing: "clear", except
    listings no event source covers, which keep their real (offline) answer."""
    try:
        from engine import corporate_events as _ce
    except ImportError:
        return []

    def _offline_events(symbols, **_kw):
        return {s: (_ce.check(s, None) if s.upper().endswith(tuple(_ce.EVENTS.UNCOVERED_LISTINGS))
                    else _ce.EventCheck("clear", note="not recorded (offline replay)")) for s in symbols}

    return [mock.patch.object(_ce, "check_many", _offline_events)]


def replay_io(rec: Recording, *, strict: bool = True) -> Any:
    """Context manager serving `rec` and blocking all real network access."""
    import TradingAnalysis_ELITE as elite

    def _factory(symbol, *args, **kwargs):  # noqa: ANN001
        return _ReplayTicker(symbol, rec, strict)

    patches = [mock.patch.object(m, "Ticker", _factory) for m in _target_modules()]
    patches.extend(_isolated_account_patches(elite))

    def _fetch(self, url, params=None, **kwargs):  # noqa: ANN001
        key = ("http", _safe_url(url), _argkey((), params or {}))
        if key in rec.calls:
            value = rec.calls[key]
            if isinstance(value, RaisedError):
                raise RuntimeError(f"{value.type_name}: {value.message}")
            return value
        rec.misses.append(key)
        if strict:
            raise KeyError(f"No recorded HTTP response for {_safe_url(url)}")
        return None

    patches.append(mock.patch.object(elite.APIClient, "fetch_json", _fetch))

    # Seal the real transport funnel. Anything not in the fixture gets a
    # deterministic empty 503 rather than a live request -- see _StubResponse
    # for why a stub beats an exception here.
    import requests.sessions

    def _request(self, method, url, **kwargs):  # noqa: ANN001
        key = ("request", str(method).upper(), _safe_url(url),
               _argkey((), kwargs.get("params") or {}))
        if key in rec.calls:
            value = rec.calls[key]
            if isinstance(value, RaisedError):
                raise RuntimeError(f"{value.type_name}: {value.message}")
            return value.to_response()
        rec.misses.append(key)
        return _StubResponse()

    patches.append(mock.patch.object(requests.sessions.Session, "request", _request))
    patches.extend(_offline_event_patches())

    class _Ctx:
        def __enter__(self):
            rec.misses.clear()
            for p in patches:
                p.start()
            return rec

        def __exit__(self, *exc):
            for p in reversed(patches):
                p.stop()
            return False

    return _Ctx()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def fixture_path(ticker: str) -> Path:
    return FIXTURE_DIR / f"{ticker.replace('.', '_').replace('^', '_')}.pkl.gz"


def save_recording(rec: Recording) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = fixture_path(rec.ticker)

    # Name the offending key rather than surfacing a bare pickle TypeError:
    # yfinance returns lazy proxies holding sessions and locks, and "cannot
    # pickle _thread.RLock" alone does not say which read produced it.
    for key, value in rec.calls.items():
        try:
            pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:  # noqa: BLE001
            raise TypeError(
                f"Captured value for {key} cannot be pickled ({exc}). Add a "
                f"conversion for it in _picklable()."
            ) from exc

    with gzip.open(path, "wb") as fh:
        pickle.dump(rec, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def load_recording(ticker: str) -> Recording:
    path = fixture_path(ticker)
    if not path.is_file():
        raise FileNotFoundError(
            f"No fixture for {ticker} at {path}. Record it with "
            f"`python tests/golden/record.py {ticker}`."
        )
    with gzip.open(path, "rb") as fh:
        return pickle.load(fh)


def available_fixtures() -> list[str]:
    if not FIXTURE_DIR.is_dir():
        return []
    out = []
    for path in sorted(FIXTURE_DIR.glob("*.pkl.gz")):
        try:
            with gzip.open(path, "rb") as fh:
                out.append(pickle.load(fh).ticker)
        except Exception:  # noqa: BLE001 - a corrupt fixture is reported by the test
            continue
    return out
