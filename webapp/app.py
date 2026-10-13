#!/usr/bin/env python3
"""
QUANTA Web — Flask backend for the QUANTA Elite Trading Platform.

Exposes two core capabilities via REST + Server-Sent Events:
  1. /api/stock-picks   → SP_ELITE_PRO batch market scanner
  2. /api/analyze       → TradingAnalysis_ELITE deep single-stock analysis
"""

import copy
import gzip
import json
import logging
import math
import os
import re
import queue
import ssl
import sys
import threading
import time
import importlib
import traceback
import uuid
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import html as html_lib
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from flask import Flask, jsonify, render_template, request, Response, url_for

# ---------------------------------------------------------------------------
# Path setup — ensure core/ and modules/ are importable
# ---------------------------------------------------------------------------
# The analysis engines (core/TradingAnalysis_ELITE.py, core/SP_ELITE_PRO.py) are
# imported in-process by the /api routes. In this repository the webapp is a
# sibling of core/ (<root>/webapp); the other candidates resolve the former
# monorepo layout (ui/stock-analysis/webapp beside stock-analysis/). Rather than
# depend on the launcher exporting PYTHONPATH (start.ps1 did; the bash launchers
# and a direct `python app.py` did not — every one of those broke with
# "No module named 'TradingAnalysis_ELITE'"), locate the engine root here.
WEBAPP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEBAPP_DIR.parent  # the webapp's own project dir (data/, cache/)


def _find_engine_root() -> Path:
    """Return the directory that contains core/TradingAnalysis_ELITE.py."""
    sentinel = Path("core") / "TradingAnalysis_ELITE.py"
    candidates = [
        PROJECT_ROOT,                              # this repository: webapp sibling of core/
        WEBAPP_DIR.parents[2] / "stock-analysis",  # ui/stock-analysis/webapp -> <ws>/stock-analysis
        WEBAPP_DIR.parents[1] / "stock-analysis",  # ui/webapp -> ui/stock-analysis
        WEBAPP_DIR.parents[3] / "stock-analysis",  # deeper nesting
    ]
    env_root = os.environ.get("QUANTA_ENGINE_ROOT")
    if env_root:
        candidates.insert(0, Path(env_root))
    for cand in candidates:
        try:
            if (cand / sentinel).is_file():
                return cand.resolve()
        except (IndexError, OSError):
            continue
    # Fall back to the legacy assumption so the error message names a real path.
    return PROJECT_ROOT


ENGINE_ROOT = _find_engine_root()
CORE_DIR = ENGINE_ROOT / "core"
MODULES_DIR = ENGINE_ROOT / "modules"

for p in (str(ENGINE_ROOT), str(PROJECT_ROOT), str(CORE_DIR), str(MODULES_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# [F9] Every decision threshold, including the scan form's limits, lives in
# engine/thresholds.py and is published at GET /api/thresholds.
from engine import thresholds as _T  # noqa: E402
from engine import research_cache as _research_cache  # noqa: E402
from engine import why_picked as _why_picked  # noqa: E402
from engine import valuation_view as _valuation_view  # noqa: E402
from engine import scan_filters as _scan_filters  # noqa: E402


def _display_path(path: Path) -> str:
    """Path relative to a known root for display, or the bare name if outside."""
    for base in (PROJECT_ROOT, ENGINE_ROOT):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return path.name

# ---------------------------------------------------------------------------
# Logging — set up early so every module can use `logger`
# ---------------------------------------------------------------------------
# Suppress noisy loggers
for name in ("yfinance", "urllib3", "requests", "httpx",
             "polygon_provider", "compliance_manager", "peewee", "matplotlib"):
    logging.getLogger(name).setLevel(logging.CRITICAL)

# The engines print rich/emoji console output. When stdout is a cp1252 pipe
# (Windows, redirected log) a plain print() raised UnicodeEncodeError from
# inside a scan job and the job reported the encoding error instead of picks.
# Make the process streams tolerant before anything else writes to them.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("QUANTA-WEB")
# Warnings and errors also go to a file: a server started from a console it can
# no longer write to (start.ps1 in a closed or stalled terminal) otherwise loses
# every traceback, and a failed scan's reference could not be looked up.
try:
    _web_log_dir = Path(__file__).resolve().parent / "logs"
    _web_log_dir.mkdir(exist_ok=True)
    _web_file = logging.FileHandler(_web_log_dir / "quanta_web.log", encoding="utf-8", delay=True)
    _web_file.setLevel(logging.WARNING)
    _web_file.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
    logger.addHandler(_web_file)
except Exception:
    pass

# ---------------------------------------------------------------------------
# yfinance rate-limit protection — MUST run before any engine import
# Patches yfinance's download session to throttle requests (max 1/sec)
# ---------------------------------------------------------------------------
try:
    import yfinance as yf
    _yf_original_get = None
    _yf_lock = threading.Lock()
    _yf_last_call = 0.0
    _YF_MIN_INTERVAL = 0.35  # seconds between requests

    def _throttled_yf():
        """Ensure yfinance calls are spaced out to avoid rate-limiting."""
        global _yf_last_call
        with _yf_lock:
            now = time.monotonic()
            elapsed = now - _yf_last_call
            if elapsed < _YF_MIN_INTERVAL:
                time.sleep(_YF_MIN_INTERVAL - elapsed)
            _yf_last_call = time.monotonic()

    # Monkey-patch yfinance.Ticker to add throttling + remember the symbol so
    # the cache layer below can key on it.
    _orig_ticker_init = yf.Ticker.__init__
    def _patched_ticker_init(self, ticker, *args, **kwargs):
        _throttled_yf()
        _orig_ticker_init(self, ticker, *args, **kwargs)
        try:
            self._quanta_symbol = str(ticker).upper()
        except Exception:
            self._quanta_symbol = None
    yf.Ticker.__init__ = _patched_ticker_init
    logger.info("yfinance rate-limiter installed (%.0fms interval)", _YF_MIN_INTERVAL * 1000)

    # -----------------------------------------------------------------------
    # yfinance response cache — PERF (no behaviour change)
    # A single deep-dive re-creates yf.Ticker(<same symbol>) ~6 times and
    # reads `.info` on each (fundamental, sentiment, market-intel, insider,
    # peer, etc.). Each read is a fresh network round-trip. A portfolio scan
    # multiplies that across hundreds of tickers. This process-wide TTL cache
    # deduplicates `.info` and `.history()` so the same data is fetched once
    # per symbol within the TTL window — cutting both network time and the
    # global 350ms throttle queue, with identical results.
    # -----------------------------------------------------------------------
    _YF_CACHE_ENABLED = os.environ.get("QUANTA_YF_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")
    # [PERF] Generous defaults so repeat scans + restarts stay warm. Price
    # history during a single session barely moves intraday, and company info
    # changes on a multi-day cadence — so long TTLs are safe and dramatically
    # cut the number of throttled network round-trips.
    _YF_INFO_TTL = float(os.environ.get("QUANTA_YF_INFO_TTL", "3600"))   # company info changes slowly (1h)
    _YF_HIST_TTL = float(os.environ.get("QUANTA_YF_HIST_TTL", "900"))    # price history (15m; survives a full scan)
    _YF_CACHE_MAX = 4000  # hard cap on entries to bound memory during big scans
    _yf_info_cache: Dict[str, Any] = {}   # symbol -> (epoch_ts, info_dict)
    _yf_hist_cache: Dict[Any, Any] = {}   # (symbol, args, kwargs) -> (epoch_ts, dataframe)
    _yf_cache_lock = threading.Lock()
    _yf_disk_write_lock = threading.Lock()

    # [PERF] Persistent disk cache. The in-memory caches above are wiped on every
    # restart, so the first scan after `start.ps1` re-downloads the whole
    # universe. Persisting them (epoch-stamped) means a restart loads warm data
    # and skips the network for everything still inside its TTL window.
    import atexit as _atexit
    # The cache is written as JSON. It used to be a serialized Python object
    # graph, which reconstructs by calling constructors named in the file — so
    # a writable cache directory was a code-execution primitive. JSON cannot
    # execute: a corrupt or hostile file is at worst a parse error. History
    # frames use pandas' split orientation (see _yf_frame_to_json).
    _YF_DISK_CACHE_ENABLED = os.environ.get("QUANTA_YF_DISK_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")
    _YF_CACHE_DIR = Path(os.environ.get("QUANTA_CACHE_DIR", str(PROJECT_ROOT / "data" / "cache")))
    _YF_CACHE_FILE = _YF_CACHE_DIR / "yf_cache.json"
    _YF_LEGACY_PICKLE = _YF_CACHE_DIR / "yf_cache.pkl"
    _YF_CACHE_FORMAT = 1
    _yf_cache_dirty = False
    _yf_cache_last_save = 0.0
    _YF_CACHE_SAVE_INTERVAL = 30.0  # debounce disk writes to at most once / 30s

    def _yf_cache_evict(cache):
        """Drop the oldest ~10% of entries when a cache exceeds the cap."""
        if len(cache) <= _YF_CACHE_MAX:
            return
        try:
            ordered = sorted(cache.items(), key=lambda kv: kv[1][0])
            for key, _ in ordered[: max(1, len(cache) // 10)]:
                cache.pop(key, None)
        except Exception:
            cache.clear()

    def _yf_frame_to_json(frame):
        """Encode a history DataFrame as a JSON string (no code execution).

        Column dtypes travel with the data. JSON has one number type, so a
        float column of whole numbers (Open 1.0, 2.0) reads back as int64 and
        the restored frame is not the frame that was cached.
        """
        return json.dumps({
            "split": frame.to_json(orient="split", date_format="iso",
                                   double_precision=15),
            "dtypes": {str(c): str(t) for c, t in frame.dtypes.items()},
            "index_name": frame.index.name,
            "index_tz": str(frame.index.tz) if getattr(frame.index, 'tz', None) else None,
        })

    def _yf_frame_from_json(text):
        """Decode what _yf_frame_to_json wrote, restoring index and dtypes."""
        import pandas as _pd
        from io import StringIO as _StringIO
        blob = json.loads(text)
        frame = _pd.read_json(_StringIO(blob["split"]), orient="split")
        if len(frame.index):
            frame.index = _pd.to_datetime(frame.index)
            if blob.get("index_tz"):
                frame.index = _pd.to_datetime(frame.index, utc=True).tz_convert(blob["index_tz"])
        frame.index.name = blob.get("index_name")
        for col, dtype in (blob.get("dtypes") or {}).items():
            if col in frame.columns and str(frame[col].dtype) != dtype:
                try:
                    frame[col] = frame[col].astype(dtype)
                except (TypeError, ValueError):
                    pass
        return frame

    def _yf_cache_load_disk():
        """Load the persisted cache, dropping entries already past their TTL."""
        if not _YF_DISK_CACHE_ENABLED:
            return
        if _YF_LEGACY_PICKLE.exists():
            logger.info(
                "Ignoring the legacy binary cache at %s — the cache is JSON now "
                "and the old file is never read. Delete it at your convenience.",
                _YF_LEGACY_PICKLE,
            )
        if not _YF_CACHE_FILE.exists():
            return
        try:
            with open(_YF_CACHE_FILE, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            if not isinstance(blob, dict) or blob.get("version") != _YF_CACHE_FORMAT:
                logger.info("yfinance disk cache format changed — starting cold.")
                return
            now = time.time()
            info_in = blob.get("info", {}) or {}
            hist_in = blob.get("hist", {}) or {}
            kept_i = kept_h = 0
            with _yf_cache_lock:
                for sym, entry in info_in.items():
                    if not (isinstance(entry, list) and len(entry) == 2):
                        continue
                    ts, payload = entry
                    if isinstance(payload, dict) and payload and (now - float(ts)) < _YF_INFO_TTL:
                        _yf_info_cache[sym] = (float(ts), payload)
                        kept_i += 1
                for key, entry in hist_in.items():
                    if not (isinstance(entry, list) and len(entry) == 2):
                        continue
                    ts, payload = entry
                    if not isinstance(payload, str) or (now - float(ts)) >= _YF_HIST_TTL:
                        continue
                    try:
                        frame = _yf_frame_from_json(payload)
                    except Exception:
                        continue
                    if frame is not None and not getattr(frame, "empty", True):
                        _yf_hist_cache[key] = (float(ts), frame)
                        kept_h += 1
            logger.info("yfinance disk cache loaded (info=%d, history=%d still fresh)", kept_i, kept_h)
        except Exception as e:
            logger.warning("Could not load yfinance disk cache: %s", e)

    def _yf_cache_save_disk(force: bool = False):
        """Persist the cache to disk (debounced unless force=True)."""
        global _yf_cache_dirty, _yf_cache_last_save
        if not _YF_DISK_CACHE_ENABLED:
            return
        now = time.time()
        if not force and (not _yf_cache_dirty or (now - _yf_cache_last_save) < _YF_CACHE_SAVE_INTERVAL):
            return
        if not _yf_disk_write_lock.acquire(blocking=force):
            return
        try:
            with _yf_cache_lock:
                # Copy the entry maps under the short memory lock; encoding
                # frames and disk I/O must not block quote-cache readers.
                info_out = {
                    sym: [float(ts), payload]
                    for sym, (ts, payload) in _yf_info_cache.items()
                    if isinstance(payload, dict)
                }
                histories = list(_yf_hist_cache.items())
                _yf_cache_dirty = False
            hist_out = {}
            for key, (ts, frame) in histories:
                if frame is None or getattr(frame, "empty", True):
                    continue
                try:
                    hist_out[key] = [float(ts), _yf_frame_to_json(frame)]
                except Exception:
                    continue
            snapshot = {"version": _YF_CACHE_FORMAT, "info": info_out, "hist": hist_out}
            _YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _YF_CACHE_FILE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, default=str)
            os.replace(tmp, _YF_CACHE_FILE)  # atomic
            _yf_cache_last_save = now
        except Exception as e:
            _yf_cache_dirty = True
            logger.debug("Could not save yfinance disk cache: %s", e)
        finally:
            _yf_disk_write_lock.release()

    def _yf_cache_clear(persist: bool = True):
        """Wipe both in-memory caches (and the disk file). Used by /api/cache/clear."""
        with _yf_cache_lock:
            n = len(_yf_info_cache) + len(_yf_hist_cache)
            _yf_info_cache.clear()
            _yf_hist_cache.clear()
        if persist:
            try:
                if _YF_CACHE_FILE.exists():
                    _YF_CACHE_FILE.unlink()
            except Exception:
                pass
        return n

    if _YF_CACHE_ENABLED:
        _yf_cache_load_disk()
        if _YF_DISK_CACHE_ENABLED:
            _atexit.register(lambda: _yf_cache_save_disk(force=True))

        _orig_info_get = yf.Ticker.info.fget

        def _cached_info_getter(self):
            global _yf_cache_dirty
            sym = getattr(self, "_quanta_symbol", None)
            if not sym:
                return _orig_info_get(self)
            now = time.time()
            with _yf_cache_lock:
                hit = _yf_info_cache.get(sym)
                if hit and isinstance(hit[1], dict) and hit[1] and (now - hit[0]) < _YF_INFO_TTL:
                    return dict(hit[1]) if isinstance(hit[1], dict) else hit[1]
            info = _orig_info_get(self)  # network round-trip (outside lock)
            if not isinstance(info, dict) or not info:
                return info
            with _yf_cache_lock:
                _yf_info_cache[sym] = (time.time(), info)
                _yf_cache_evict(_yf_info_cache)
                _yf_cache_dirty = True
            _yf_cache_save_disk()
            return dict(info) if isinstance(info, dict) else info

        yf.Ticker.info = property(_cached_info_getter)

        _orig_history = yf.Ticker.history

        def _cached_history(self, *args, **kwargs):
            global _yf_cache_dirty
            sym = getattr(self, "_quanta_symbol", None)
            try:
                key = json.dumps([sym, args, sorted(kwargs.items())],
                                 sort_keys=True, default=str) if sym else None
            except Exception:
                key = None
            if key is None:
                return _orig_history(self, *args, **kwargs)
            now = time.time()
            with _yf_cache_lock:
                hit = _yf_hist_cache.get(key)
                if hit and hit[1] is not None and not getattr(hit[1], 'empty', True) and (now - hit[0]) < _YF_HIST_TTL:
                    cached_df = hit[1]
                    return cached_df.copy() if hasattr(cached_df, "copy") else cached_df
            df = _orig_history(self, *args, **kwargs)  # network round-trip (outside lock)
            # Yahoo can return an empty frame on a transient failure. Caching
            # that response made Retry replay the outage for fifteen minutes,
            # including after restarts via the disk cache.
            if df is None or getattr(df, 'empty', True):
                return df
            try:
                with _yf_cache_lock:
                    _yf_hist_cache[key] = (time.time(), df.copy() if hasattr(df, "copy") else df)
                    _yf_cache_evict(_yf_hist_cache)
                    _yf_cache_dirty = True
                _yf_cache_save_disk()
            except Exception:
                pass
            return df

        yf.Ticker.history = _cached_history
        logger.info(
            "yfinance response cache installed (info TTL %.0fs, history TTL %.0fs, disk=%s)",
            _YF_INFO_TTL, _YF_HIST_TTL, "on" if _YF_DISK_CACHE_ENABLED else "off",
        )
except Exception as e:
    logger.warning(f"Could not install yfinance throttle: {e}")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.urandom(24).hex()
# A long-running server must never keep serving a stale shell: re-read the
# template when it changes on disk, and version every asset URL by its mtime
# so browsers fetch the file that is actually on disk.
app.config["TEMPLATES_AUTO_RELOAD"] = True

# ---------------------------------------------------------------------------
# Request guard (whole app). QUANTA is an unauthenticated local app, so:
#  * the Host header must be loopback -- a DNS-rebinding page served from
#    another name cannot read /api/* responses through the browser;
#  * every state-changing request must carry X-QUANTA-Request: 1 -- a custom
#    header a cross-site page cannot send without a CORS preflight this server
#    never approves -- and, when the browser sends an Origin, it must be this
#    server. `get_json(force=True)` used to accept a cross-site text/plain POST
#    that started scans, cleared caches and opened files.
# The My Portfolio blueprint keeps its own identical, stricter guard.
# ---------------------------------------------------------------------------
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "media-src 'self'",
    # Tools that read the page's CSS (axe, readers) fetch the font stylesheet.
    "connect-src 'self' https://fonts.googleapis.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))
CSRF_HEADER = "X-QUANTA-Request"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _request_hostname(hostport: str) -> str:
    return (urlsplit("//" + (hostport or "")).hostname or "").lower()


@app.before_request
def _quanta_request_guard():
    if _request_hostname(request.host) not in LOOPBACK_HOSTS:
        return jsonify({"error": "QUANTA is only served on this computer (loopback)."}), 403
    if request.method in _UNSAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != "1":
            return jsonify({"error": "Missing request header."}), 403
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != (request.host or "").lower():
            return jsonify({"error": "Cross-origin request refused."}), 403
    return None


# ---------------------------------------------------------------------------
# Transfer: gzip for text bodies, long caching for versioned static files.
# The page's scripts and styles are ~1.5 MB raw and ~0.3 MB gzipped; Werkzeug
# sends neither compression nor cache headers on its own. A production server
# or proxy can take this over; it is harmless behind one that already does.
# ---------------------------------------------------------------------------
_GZIP_TYPES = frozenset({"text/html", "text/css", "text/plain", "application/javascript",
                         "text/javascript", "application/json", "image/svg+xml"})
_GZIP_MIN_BYTES = 1024
_STATIC_GZIP_CACHE: Dict[Tuple[str, str], bytes] = {}
_STATIC_GZIP_CACHE_MAX = 64


@app.after_request
def _quanta_transfer(resp):
    is_static = request.path.startswith("/static/")
    if is_static and resp.status_code in (200, 304) and request.args.get("v"):
        # asset_url() puts the file's mtime in ?v=, so a versioned URL never changes.
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    if resp.status_code != 200 or "gzip" not in request.headers.get("Accept-Encoding", "").lower():
        return resp
    if resp.headers.get("Content-Encoding") or resp.mimetype not in _GZIP_TYPES:
        return resp
    if resp.is_streamed and not resp.direct_passthrough:
        return resp                                    # a generator (SSE): never buffer it
    resp.headers.add("Vary", "Accept-Encoding")
    resp.direct_passthrough = False
    data = resp.get_data()
    if len(data) < _GZIP_MIN_BYTES:
        return resp
    key = (request.path, resp.headers.get("ETag", "")) if is_static and resp.headers.get("ETag") else None
    body = _STATIC_GZIP_CACHE.get(key) if key else None
    if body is None:
        body = gzip.compress(data, compresslevel=6)
        if key:
            if len(_STATIC_GZIP_CACHE) >= _STATIC_GZIP_CACHE_MAX:
                _STATIC_GZIP_CACHE.clear()
            _STATIC_GZIP_CACHE[key] = body
    resp.set_data(body)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(body))
    etag = resp.headers.get("ETag")
    if etag and not etag.endswith('-gz"'):
        resp.headers["ETag"] = etag[:-1] + '-gz"' if etag.endswith('"') else etag + "-gz"
    return resp


def _is_api_request() -> bool:
    return request.path.startswith("/api/")


@app.errorhandler(404)
def _not_found(err):
    if _is_api_request():
        return jsonify({"error": "Not found."}), 404
    return err


@app.errorhandler(405)
def _method_not_allowed(err):
    if _is_api_request():
        return jsonify({"error": "Method not allowed."}), 405
    return err


@app.errorhandler(500)
def _internal_error(err):
    # Never echo the exception: Flask logs it with its traceback.
    if _is_api_request():
        return jsonify({"error": "Internal server error."}), 500
    return err


@app.after_request
def _quanta_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    # Script only from this server (no inline script, no eval); styles allow
    # inline style attributes, which the charts and bars set per value.
    resp.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    return resp
app.jinja_env.auto_reload = True


def _build_label() -> str:
    """What the footer shows as the build: QUANTA_VERSION if the deploy sets it,
    else the checked-out git commit (read from .git, no subprocess), else ""."""
    env = os.environ.get("QUANTA_VERSION", "").strip()
    if env:
        return env[:40]
    try:
        for parent in (WEBAPP_DIR, *WEBAPP_DIR.parents):
            git = parent / ".git"
            if not git.is_dir():
                continue
            head = (git / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                ref = head[5:].strip()
                ref_file = git / ref
                if ref_file.is_file():
                    return f"build {ref_file.read_text(encoding='utf-8').strip()[:7]}"
                packed = git / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line.endswith(" " + ref):
                            return f"build {line.split()[0][:7]}"
                return ""
            return f"build {head[:7]}" if re.fullmatch(r"[0-9a-f]{40}", head) else ""
    except OSError:
        return ""
    return ""


APP_BUILD_LABEL = _build_label()


@app.context_processor
def _build_info():
    return {"app_build": APP_BUILD_LABEL}


@app.context_processor
def _asset_versioning():
    def asset_url(filename: str) -> str:
        try:
            version = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)
    return {"asset_url": asset_url}

# In-memory job store: job_id → {status, progress, result, error, ...}
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
# Finished jobs are kept only long enough for the client to collect the result
# (the SSE stream hands off to a GET on completion). Without a cap the store
# grew by one full scan payload per click for the life of the process.
JOB_RETENTION_SECONDS = int(os.getenv("QUANTA_JOB_RETENTION_SECONDS", "1800"))
JOB_RETENTION_MAX = int(os.getenv("QUANTA_JOB_RETENTION_MAX", "200"))
# Concurrent market scans. One is the right default: the scanner is throttled
# to 3 provider workers, so a second simultaneous scan does not run in
# parallel — it halves the throughput of both.
MAX_CONCURRENT_SCANS = max(1, int(os.getenv("QUANTA_MAX_CONCURRENT_SCANS", "1")))
# Wall-clock ceiling for a single scan. The worker runs on a daemon thread we
# cannot kill, but a wedged thread must not hold the single scan slot forever:
# past this budget the job is marked failed, the slot is released and the user
# can start a new scan. Generous by design — a full US scan is minutes, not
# seconds — so this only ever fires on a genuine hang.
SCAN_MAX_RUNTIME_SECONDS = max(
    60, int(os.getenv("QUANTA_SCAN_MAX_RUNTIME_SECONDS", "1800")))


def _scan_params_key(params: Dict[str, Any]) -> str:
    """Stable identity for a scan request, so duplicate clicks coalesce.

    Two requests are the same scan when every input that changes the result
    matches. ``safe_mode`` is included: a safe-mode retry is a different run.
    """
    tickers = params.get("custom_tickers") or []
    return json.dumps({
        "market": params.get("market"),
        "capital": params.get("capital"),
        "scan_type": params.get("scan_type"),
        "max_positions": params.get("max_positions"),
        "diversify": bool(params.get("diversify")),
        "filters": params.get("filters") or {},
        "custom_tickers": sorted(str(t) for t in tickers),
        "risk_profile": params.get("risk_profile"),
        "safe_mode": bool(params.get("safe_mode")),
    }, sort_keys=True)


def _expire_stale_jobs(now: Optional[float] = None) -> int:
    """Fail jobs that have been "running" past ``SCAN_MAX_RUNTIME_SECONDS``.

    A worker thread can wedge (a provider call with no timeout, a deadlock in a
    dependency). Nothing else ever moved such a job off ``running``, so it held
    the single scan slot — and the 409 gate — until the process restarted.
    Marking it failed is honest (we genuinely do not know if it will finish)
    and frees the slot. Caller must NOT hold ``_jobs_lock``.
    """
    now = time.time() if now is None else now
    expired = 0
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job.get("status") != "running":
                continue
            started = float(job.get("started_at") or 0.0)
            if not started or (now - started) <= SCAN_MAX_RUNTIME_SECONDS:
                continue
            job.update({
                "status": "error",
                "error": (
                    f"{job.get('type') or 'Job'} exceeded its "
                    f"{SCAN_MAX_RUNTIME_SECONDS // 60}-minute budget and was "
                    "abandoned. The data provider was most likely not "
                    "responding — try again, or use a smaller scan."
                ),
                "error_detail": {"reason": "timeout",
                                 "elapsed_seconds": round(now - started, 1)},
                "finished_at": now,
                "message": "Timed out",
            })
            expired += 1
            logger.warning("Job %s (%s) abandoned after %.0fs — exceeded the scan budget",
                           job_id, job.get("type"), now - started)
    return expired


def _prune_jobs(now: Optional[float] = None) -> int:
    """Drop finished jobs older than the retention window (oldest first past the cap).

    Caller must NOT hold ``_jobs_lock``. Jobs still running are never pruned,
    but ones wedged past the runtime budget are failed first by
    ``_expire_stale_jobs`` so they cannot pin the scan slot forever.
    Returns the number of jobs removed.
    """
    now = time.time() if now is None else now
    _expire_stale_jobs(now)
    removed = 0
    with _jobs_lock:
        finished = [
            (job.get("finished_at") or job.get("started_at") or 0.0, job_id)
            for job_id, job in _jobs.items()
            if job.get("status") in ("complete", "error") and not job.get("worker_active")
        ]
        finished.sort()
        for finished_at, job_id in finished:
            if now - float(finished_at) > JOB_RETENTION_SECONDS:
                _jobs.pop(job_id, None)
                removed += 1
        # Hard cap regardless of age, oldest finished first.
        overflow = len(_jobs) - JOB_RETENTION_MAX
        if overflow > 0:
            for _, job_id in finished:
                if overflow <= 0:
                    break
                if _jobs.pop(job_id, None) is not None:
                    removed += 1
                    overflow -= 1
    return removed

# [SCAN-HEALTH] Last-scan telemetry for the dashboard health panel. Updated at
# the end of every stock-picks scan so /api/health can report freshness and the
# most recent scan status without rerunning anything.
_last_scan_meta: Dict[str, Any] = {
    "timestamp": None,
    "scan_status": None,
    "universe_size": 0,
    "analysis_failed_count": 0,
    "exception_rate": 0.0,
    "final_pick_count": 0,
    "provider_rate_limited_count": 0,
    "provider_circuit_open": False,
    "deep_confirm_enabled": None,
}
_last_scan_meta_lock = threading.Lock()


def _classify_scan_status(
    universe_size: int,
    exception_rate: float,
    analysis_failed_count: int,
    final_pick_count: int,
    provider_rate_limited_count: int = 0,
    provider_circuit_open: bool = False,
) -> str:
    """Classify a scan outcome into a single status the UI keys its messaging off.

    Pure function (no I/O) so the classification policy is unit-testable.

    Returns one of:
        ``no_universe``          — nothing was scanned (engine/import problem).
        ``provider_rate_limited``— provider throttled the scan (no surviving picks).
        ``failed``               — >=50% of the universe errored out (non-throttle).
        ``partial``              — some tickers failed AND no picks survived.
        ``partial_with_picks``   — some tickers failed BUT picks still produced.
        ``no_picks``             — clean scan, filters rejected every candidate.
        ``success``              — clean scan with at least one final pick.

    Rate-limit precedence: when the provider circuit tripped, or rate-limit
    errors make up at least half of the failures, the outcome is reported as a
    provider throttle (not a generic failure) so the UI can show a cooldown +
    safe-mode retry. If picks still survived the throttle it is reported as
    ``partial_with_picks`` so the user still sees them.
    """
    if universe_size <= 0:
        return "no_universe"
    rl = int(provider_rate_limited_count or 0)
    rl_dominant = bool(provider_circuit_open) or (
        rl > 0 and rl >= 0.5 * max(1, analysis_failed_count)
    )
    if rl_dominant:
        if final_pick_count > 0:
            return "partial_with_picks"
        return "provider_rate_limited"
    if exception_rate >= 0.5:
        return "failed"
    if analysis_failed_count > 0 and final_pick_count == 0:
        return "partial"
    if analysis_failed_count > 0:
        return "partial_with_picks"
    if final_pick_count == 0:
        return "no_picks"
    return "success"

_dashboard_cache: Dict[str, Any] = {"timestamp": 0.0, "payload": None}
_dashboard_lock = threading.Lock()
DASHBOARD_CACHE_SECONDS = 180

_analysis_runtime: Dict[str, Any] = {
    "module": None,
    "analyzer": None,
    "api_client": None,
    "config_key": None,
}
_market_env_cache: Dict[str, Any] = {"timestamp": 0.0, "payload": None}
_market_env_lock = threading.Lock()
# Keyed by "<TICKER>::<mode>" — see `_analysis_cache_key`.
_analysis_result_cache: Dict[str, Dict[str, Any]] = {}
_analysis_result_lock = threading.Lock()
ANALYSIS_RESULT_CACHE_SECONDS = int(os.getenv("QUANTA_ANALYSIS_CACHE_SECONDS", "300"))
# [F04] How long an EXPIRED result stays available as a labelled last-good
# fallback for provider outages. Freshness and existence are separate
# questions: past ANALYSIS_RESULT_CACHE_SECONDS a result stops being served as
# current, and only past this window does it stop existing at all.
ANALYSIS_STALE_RETENTION_SECONDS = int(
    os.getenv("QUANTA_ANALYSIS_STALE_RETENTION_SECONDS", "86400")
)

# Account context for deep-dive position sizing. The analyzer defaults to
# 100,000 internally; naming the number here lets the API say whether the size
# on screen came from the user's mandate or from this placeholder.
DEFAULT_ACCOUNT_SIZE = 100_000.0
MAX_ACCOUNT_SIZE = 1_000_000_000.0
MIN_ACCOUNT_SIZE = 100.0


def _resolve_account_context(payload: Dict[str, Any]) -> tuple:
    """Return ``(account_size, assumed)`` for a request payload.

    ``assumed`` is True only when the payload did NOT supply a usable size and
    the documented placeholder was substituted. Deriving it here — rather than
    re-comparing the resolved number against the default at the call site — is
    what keeps the two answers consistent: a caller that legitimately declares
    an account of exactly DEFAULT_ACCOUNT_SIZE, or declares any size as a JSON
    string (`"100000"`, which is what an un-coerced form field serialises to),
    must still be reported as having declared it. Labelling a real mandate as a
    placeholder is the same class of error as the reverse.
    """
    raw = (payload or {}).get("account_size")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ACCOUNT_SIZE, True
    if not math.isfinite(value) or value < MIN_ACCOUNT_SIZE:
        return DEFAULT_ACCOUNT_SIZE, True
    return min(value, MAX_ACCOUNT_SIZE), False


def _resolve_account_size(payload: Dict[str, Any]) -> float:
    """Account value to size against, from the request or the documented default."""
    return _resolve_account_context(payload)[0]


# Catalyst calendar window. Calendar days, not trading sessions, and one day
# back so a report released after the last close still shows.
CATALYST_WINDOW_DAYS_AHEAD = 7
CATALYST_WINDOW_DAYS_BACK = 1
CATALYST_WINDOW_LABEL = "next 7 calendar days"
MARKET_ENV_CACHE_SECONDS = int(os.getenv("QUANTA_MARKET_ENV_CACHE_SECONDS", "600"))

ENGINE_FILES = {
    "trading-analysis": {
        "label": "TradingAnalysis_ELITE.py",
        "description": "Deep single-stock institutional analysis engine",
        "path": CORE_DIR / "TradingAnalysis_ELITE.py",
        "tab": "analyze",
    },
    "sp-elite-pro": {
        "label": "SP_ELITE_PRO.py",
        "description": "Batch stock-picking and portfolio scanner",
        "path": CORE_DIR / "SP_ELITE_PRO.py",
        "tab": "picks",
    },
}

FINANCE_SOURCE_LINKS = [
    {"name": "Barron's", "url": "https://www.barrons.com/markets", "focus": "Markets, stocks, investing ideas"},
    {"name": "WSJ Markets", "url": "https://www.wsj.com/news/markets", "focus": "Markets, macro, rates"},
    {"name": "Reuters Business", "url": "https://www.reuters.com/business/", "focus": "Breaking business and global markets"},
    {"name": "Bloomberg Markets", "url": "https://www.bloomberg.com/markets", "focus": "Global markets and macro"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/markets/", "focus": "Market movers and live updates"},
    {"name": "MarketWatch", "url": "https://www.marketwatch.com/", "focus": "Stocks, market pulse, retirement"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/", "focus": "Trending tickers and broad coverage"},
    {"name": "Financial Times", "url": "https://www.ft.com/markets", "focus": "Global markets and policy"},
]

# Global wires: macro that moves every equity market (Fed, crude, the dollar).
# These ride along with whichever market is selected.
# Probed 2026-09-25. Removed: both Reuters Agency feeds (HTTP 404; Reuters
# withdrew public RSS). The Fed is a primary source for the policy headlines
# every equity market trades on.
GLOBAL_RSS_FEEDS = [
    {"name": "Financial Times Markets", "url": "https://www.ft.com/markets?format=rss"},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
]

# Indian market wires. Selecting India used to leave the tape on US sources,
# which is worse than showing nothing: a Fed headline sitting above a Nifty
# position list reads as context for the names below it, and it is not.
# Every URL here was probed for a parseable <item> list before being added;
# NDTV Profit (403), Moneycontrol Business (502) and the Reuters India topic
# feed (404) were dropped for that reason rather than left in to fail quietly.
INDIA_RSS_FEEDS = [
    {"name": "Economic Times Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"name": "Economic Times Stocks", "url": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"},
    # Moneycontrol removed 2026-09-25: every Moneycontrol RSS endpoint
    # (marketreports, latestnews, business, buzzingstocks) last published in
    # April 2024. A feed that answers with two-year-old items is dead.
    {"name": "Business Standard Markets", "url": "https://www.business-standard.com/rss/markets-106.rss"},
    {"name": "Mint Markets", "url": "https://www.livemint.com/rss/markets"},
    {"name": "BusinessLine Markets", "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss"},
] + GLOBAL_RSS_FEEDS

# Probed 2026-09-25. WSJ moved to the Dow Jones content host; the old
# feeds.a.dj.com URL still answers but stopped publishing in January 2025.
# MarketWatch MarketPulse stopped in July 2025 (Bulletins replaces it).
# Barron's answers 403 to non-browser clients and was removed rather than
# spoofed around.
US_RSS_FEEDS = [
    {"name": "WSJ Markets", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
    {"name": "MarketWatch Top Stories", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "MarketWatch Bulletins", "url": "https://feeds.content.dowjones.io/public/rss/mw_bulletins"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "CNBC Economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"name": "CNBC Politics", "url": "https://www.cnbc.com/id/10000113/device/rss/rss.html"},
    # The broad news/rssindex endpoint returns 404; use S&P 500 headlines.
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/rss/headline?s=%5EGSPC"},
] + GLOBAL_RSS_FEEDS

MARKET_RSS_FEEDS = {"US": US_RSS_FEEDS, "IN": INDIA_RSS_FEEDS}

# Back-compat alias for the name that existed before the tape became
# market-aware. It is the US list, which is what it always held.
RSS_FEEDS = US_RSS_FEEDS


# Regime inputs that come from the headline tape rather than market data.
NEWS_ONLY_INPUTS = frozenset({"news_feeds", "news_impact"})


def _feeds_for_market(market: Any) -> List[Dict[str, str]]:
    """RSS sources for one market. Unknown codes fall back to the US wires.

    Resolves the market code through ``_normalize_market`` (defined below;
    the lookup happens at call time) so "IN", "in", "India" and "NSE" all
    reach the same list the market toggle means by them.
    """
    return MARKET_RSS_FEEDS.get(_normalize_market(market), US_RSS_FEEDS)

IMPACT_TERMS = {
    "fed": 7, "fomc": 7, "rate cut": 7, "rate hike": 7, "treasury": 5,
    "inflation": 6, "cpi": 6, "pce": 6, "jobs report": 6, "payroll": 5,
    "earnings": 5, "guidance": 5, "outlook": 4, "revenue": 3,
    "nvidia": 5, "apple": 4, "microsoft": 4, "tesla": 4, "amazon": 4,
    "oil": 4, "opec": 4, "dollar": 4, "yen": 4, "china": 4,
    "tariff": 5, "shutdown": 5, "default": 7, "recession": 7,
    "white house": 5, "congress": 4, "election": 4, "sanctions": 5,
    "iran": 4, "russia": 4, "ukraine": 4, "israel": 4, "china summit": 5,
    "selloff": 6, "rally": 5, "record high": 4, "correction": 6,
    "merger": 5, "acquisition": 5, "ipo": 4, "sec": 4,
}

# [MARKET-VOCABULARY] Indian market terms, merged into the tables above.
#
# The classifier's whole vocabulary was US macro ("fed", "fomc", "cpi",
# "nasdaq"). An Indian headline -- "Sensex ends 400 pts lower as RBI holds
# repo rate" -- matched nothing, scored zero, was categorised "other" and was
# dropped by the relevance gate. Selecting India therefore produced a tape of
# US headlines with the Indian ones filtered out of it.
#
# These are additions, not a replacement: a US headline cannot match "sensex"
# or "repo rate", so the US tape scores exactly as it did before. Where a term
# is genuinely shared (an RBI decision reported by Reuters) it should score on
# both tapes, which is the point.
INDIA_IMPACT_TERMS = {
    "rbi": 7, "repo rate": 7, "reverse repo": 6, "mpc": 6, "crr": 5, "slr": 4,
    "sensex": 6, "nifty": 6, "bank nifty": 5, "bse": 4, "nse": 4,
    "sebi": 5, "rupee": 5, "inr": 4, "fii": 5, "dii": 4,
    "union budget": 7, "gst": 5, "monsoon": 4, "fiscal deficit": 6,
    "wpi": 5, "iip": 4, "demat": 3, "circuit limit": 4, "block deal": 4,
    "bulk deal": 4, "qip": 4, "buyback": 4, "bonus issue": 3, "stock split": 3,
}
INDIA_CATEGORY_KEYWORDS = {
    "macro": ["rbi", "repo rate", "reverse repo", "mpc", "crr", "rupee", "inr",
              "union budget", "gst", "fiscal deficit", "wpi", "iip", "monsoon"],
    "politics": ["parliament", "lok sabha", "rajya sabha", "sebi", "finance ministry",
                 "niti aayog", "union budget"],
    "earnings": ["q1 results", "q2 results", "q3 results", "q4 results",
                 "quarterly results", "crore profit", "net profit"],
    "stocks": ["sensex", "nifty", "bank nifty", "bse", "nse", "midcap", "smallcap",
               "block deal", "bulk deal", "circuit limit", "listing gains"],
    "deals": ["qip", "buyback", "open offer", "stake sale", "bonus issue"],
}
INDIA_CATEGORY_RULES = {
    "macro": {"rbi": 8, "repo rate": 8, "mpc": 7, "rupee": 6, "inr": 5,
              "union budget": 8, "gst": 6, "fiscal deficit": 7, "wpi": 6, "iip": 5,
              "monsoon": 4, "crr": 6},
    "politics": {"sebi": 7, "parliament": 5, "lok sabha": 5, "finance ministry": 6,
                 "union budget": 7},
    "earnings": {"q1 results": 8, "q2 results": 8, "q3 results": 8, "q4 results": 8,
                 "quarterly results": 8, "net profit": 6, "crore profit": 6},
    "stocks": {"sensex": 8, "nifty": 8, "bank nifty": 7, "bse": 5, "nse": 5,
               "midcap": 5, "smallcap": 5, "block deal": 6, "bulk deal": 6,
               "listing gains": 5},
    "deals": {"qip": 7, "buyback": 6, "open offer": 7, "stake sale": 6,
              "bonus issue": 5},
}
IMPACT_TERMS.update(INDIA_IMPACT_TERMS)

CATEGORY_KEYWORDS = {
    "macro": ["fed", "fomc", "inflation", "cpi", "pce", "treasury", "yield", "jobs", "payroll", "gdp", "economy", "dollar", "recession", "tariff", "bond"],
    "politics": ["white house", "congress", "senate", "election", "tariff", "sanctions", "trump", "biden", "iran", "russia", "ukraine", "israel", "china", "putin", "xi", "geopolitical"],
    "earnings": ["earnings", "eps", "guidance", "quarterly results", "reports results", "profit forecast", "revenue forecast"],
    "stocks": ["stock", "stocks", "nasdaq", "s&p", "dow", "shares", "rally", "selloff", "futures", "sector"],
    "deals": ["merger", "acquisition", "takeover", "buyout", "ipo", "stake", "spin off", "spinoff", "deal talks"],
}

CATEGORY_RULES = {
    "deals": {
        "merger": 8, "acquisition": 8, "takeover": 8, "buyout": 7, "deal talks": 7,
        "ipo": 6, "stake": 4, "spin off": 5, "spinoff": 5,
    },
    "macro": {
        "fed": 8, "fomc": 8, "inflation": 8, "cpi": 8, "pce": 8,
        "rate hike": 8, "rate cut": 8, "interest rates": 7, "treasury": 7,
        "bond": 6, "yield": 6, "gdp": 8, "economy": 6, "economic": 6,
        "jobs report": 7, "payroll": 7, "unemployment": 6, "recession": 8,
        "tariff": 7, "china": 4, "dollar": 5, "yen": 5, "oil": 4,
    },
    "politics": {
        "white house": 8, "congress": 7, "senate": 6, "house republican": 6,
        "election": 7, "campaign": 5, "trump": 6, "biden": 6, "tariff": 6,
        "sanctions": 7, "geopolitical": 6, "war": 5, "ceasefire": 5,
        "iran": 6, "russia": 6, "ukraine": 6, "israel": 6, "china summit": 7,
        "putin": 6, "xi": 5, "beijing": 5, "regulation": 4,
    },
    "earnings": {
        "earnings": 9, "eps": 9, "quarterly results": 8, "reports results": 8,
        "results beat": 8, "results miss": 8, "profit forecast": 7, "revenue forecast": 7,
        "raises guidance": 8, "cuts guidance": 8, "guidance": 6, "same-store sales": 5,
    },
    "stocks": {
        "stock": 5, "stocks": 5, "shares": 5, "nasdaq": 5, "s&p": 5,
        "dow": 5, "futures": 4, "rally": 4, "selloff": 4, "analyst": 4,
        "upgrade": 5, "downgrade": 5, "price target": 5, "sector": 3,
        "battery": 5, "lithium": 5, "semiconductor": 5, "chip": 4,
        "artificial intelligence": 4, "electric vehicle": 4,
    },
}

# Merge the Indian vocabulary into the shared tables (see INDIA_IMPACT_TERMS
# above for why this is additive rather than a per-market swap).
for _category, _terms in INDIA_CATEGORY_KEYWORDS.items():
    CATEGORY_KEYWORDS.setdefault(_category, []).extend(
        t for t in _terms if t not in CATEGORY_KEYWORDS.get(_category, []))
for _category, _rules in INDIA_CATEGORY_RULES.items():
    CATEGORY_RULES.setdefault(_category, {}).update(_rules)
del _category, _terms, _rules

TREND_STOPWORDS = {
    # Common English words / fragments
    "A", "AN", "AND", "ARE", "AS", "AT", "BE", "BUT", "BY", "CAN", "DO",
    "FOR", "FROM", "GET", "GO", "HAS", "HIS", "HOW", "IF", "IN", "IS",
    "IT", "ITS", "NEW", "NO", "NOR", "NOT", "OF", "ON", "OR", "OUR",
    "OUT", "PER", "SO", "THE", "TO", "TOP", "UP", "VS", "WAS", "WE",
    "WHO", "WHY", "WILL", "WITH", "YOU", "YOUR",
    # Business / finance acronyms (not tickers)
    "AI", "API", "AUM", "BPS", "CEO", "CFO", "COO", "CTO", "CIO", "CMO",
    "COVID", "CPU", "CRO", "DJ", "DOJ", "EBIT", "EBITDA", "EOD", "EPS",
    "ESG", "ETF", "EU", "EV", "FAQ", "FCC", "FDA", "FED", "FOMC", "FTC",
    "FX", "GAAP", "GDP", "GPU", "HQ", "IPO", "IRS", "JPM", "LLC", "M&A",
    "MD", "ML", "NAV", "NDA", "NSE", "BSE", "NYSE", "OEM", "PE", "PEG",
    "PR", "Q1", "Q2", "Q3", "Q4", "R&D", "RBI", "ROE", "ROI", "ROIC",
    "SEC", "SOX", "SPV", "SaaS", "TAM", "TBA", "TBD", "TLD", "UK", "USA",
    "US", "USD", "VC", "VP", "WSJ", "YOY", "YTD",
    # Time / generic
    "AM", "PM", "ET", "PT", "GMT", "EST", "EDT", "PST", "PDT", "UTC",
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
    "NOV", "DEC",
    # Media / wires (often misread as tickers)
    "AP", "CNBC", "CNN", "BBC", "FT", "NYT", "NPR", "PBS", "NBC", "ABC",
    "FOX", "REUTERS", "BLOOMBERG", "BARRON", "MARKETWATCH",
    # Generic words that often render uppercase in headlines
    "ASAP", "BIG", "BIO", "BOSS", "BUY", "CASH", "CHIP", "CITY", "CLUB",
    "CORE", "DATA", "DEAL", "DROP", "DUE", "EARLY", "FIRST", "FREE",
    "FUND", "GAIN", "GAME", "GIANT", "GOLD", "HEAD", "HIGH", "HUGE",
    "IDEA", "INC", "INDIA", "JOBS", "KEY", "LAST", "LATE", "LIVE",
    "LONG", "LOOK", "LOSS", "LOW", "MORE", "MOST", "MOVE", "MUCH", "NEXT",
    "NEWS", "NICE", "OIL", "OK", "OPEN", "OWN", "PLAN", "PLUS", "POLL",
    "PROS", "PUT", "REAL", "RICH", "RISE", "RISK", "RX", "SAFE", "SALE",
    "SAY", "SEE", "SHOP", "SHORT", "SLOW", "SOON", "STAR", "STOP", "TAX",
    "TECH", "TIME", "TOLD", "TRADE", "TURN", "VIEW", "WAR", "WARN", "WEAK",
    "WEEK", "WIN", "YEAR", "YEN",
}

# ---------------------------------------------------------------------------
# Market universes
# ---------------------------------------------------------------------------

MARKET_UNIVERSES = {
    "US": {
        "label": "United States",
        "currency": "$",
        "index_symbols": [
            ("SPY", "S&P 500"),
            ("QQQ", "Nasdaq 100"),
            ("DIA", "Dow"),
            ("IWM", "Russell 2000"),
            ("^VIX", "VIX"),
            ("^TNX", "10Y Yield"),
            ("GLD", "Gold"),
            ("USO", "Oil"),
            ("BTC-USD", "Bitcoin"),
        ],
        "mega_caps": [
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO",
            "AMD", "BRK-B", "JPM", "V", "MA", "UNH", "XOM", "WMT", "LLY",
            "JNJ", "PG", "HD", "COST", "NFLX", "DIS", "BAC", "CRM", "ORCL",
            "ADBE", "PEP", "KO", "MRK",
        ],
    },
    "IN": {
        "label": "India",
        "currency": "\u20b9",
        "index_symbols": [
            ("^NSEI", "Nifty 50"),
            ("^BSESN", "Sensex"),
            ("^NSEBANK", "Bank Nifty"),
            ("^CNXIT", "Nifty IT"),
            ("^INDIAVIX", "India VIX"),
            ("INR=X", "USD/INR"),
            ("GLD", "Gold"),
            ("USO", "Oil"),
        ],
        "mega_caps": [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
            "HINDUNILVR.NS", "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS",
            "KOTAKBANK.NS", "AXISBANK.NS", "BAJFINANCE.NS", "ASIANPAINT.NS",
            "MARUTI.NS", "HCLTECH.NS", "WIPRO.NS", "SUNPHARMA.NS", "TITAN.NS",
            "NESTLEIND.NS", "ULTRACEMCO.NS", "M&M.NS", "NTPC.NS", "POWERGRID.NS",
            "TATAMOTORS.NS", "ADANIENT.NS", "COALINDIA.NS", "JSWSTEEL.NS",
            "TATASTEEL.NS", "ONGC.NS",
        ],
    },
}

# Canonical regime factor model keys used by the dashboard command center.
REGIME_FACTOR_ORDER = [
    "trend",
    "volatility",
    "participation",
    "rates_credit",
    "index_performance",
    "sector_leadership",
    "momentum",
    "macro_risk",
    "news_impact",
    "catalyst_risk",
]

# Per-market intel cache
_market_intel_cache: Dict[str, Dict[str, Any]] = {}
_market_intel_cache_lock = threading.Lock()
MARKET_INTEL_CACHE_SECONDS = 600  # 10 min — scanning 100+ symbols is heavier than 30


def _now_iso() -> str:
    """Timezone-aware local timestamp (ISO 8601 with UTC offset).

    Every "as of" / "generated" stamp that crosses to the browser must carry
    its offset: a naive ``datetime.now().isoformat()`` is parsed by
    ``new Date()`` as *browser-local* time, which only happens to be right when
    the server and the browser share a machine.
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize_market(market: Any) -> str:
    """Map any client market code onto a key of MARKET_UNIVERSES (default US)."""
    key = str(market or "US").strip().upper()
    if key in ("INDIA", "NSE", "BSE"):
        key = "IN"
    return key if key in MARKET_UNIVERSES else "US"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_serialize(obj: Any, _depth: int = 0) -> Any:
    """Recursively make objects JSON-serializable (with depth guard)."""
    if _depth > 20:
        return str(obj)  # prevent infinite recursion
    if obj is None:
        return None
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        if obj != obj or abs(obj) == float("inf"):
            return None
        return round(obj, 4)
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v, _depth + 1) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        try:
            return {k: _safe_serialize(v, _depth + 1) for k, v in asdict(obj).items()}
        except Exception:
            return str(obj)
    if hasattr(obj, "value"):  # Enum
        return obj.value
    # pandas types
    try:
        import pandas as pd
        if isinstance(obj, (pd.Series, pd.DataFrame)):
            return None  # skip large data structures
    except ImportError:
        pass
    return str(obj)


def _stock_analysis_to_dict(rec) -> Dict[str, Any]:
    """Convert an SP_ELITE_PRO StockAnalysis dataclass to a JSON-safe dict."""
    try:
        d = rec.to_dict()
        return _safe_serialize(d)
    except Exception as e:
        logger.warning(f"to_dict() failed for {getattr(rec, 'ticker', '?')}: {e}")
        # Manual extraction of key fields as fallback
        try:
            return _safe_serialize(asdict(rec))
        except Exception:
            return {
                "ticker": getattr(rec, "ticker", "?"),
                "company": getattr(rec, "company", ""),
                "sector": getattr(rec, "sector", ""),
                "signal": str(getattr(rec, "signal", "HOLD")),
                "overall_score": float(getattr(rec, "overall_score", 0)),
                "entry_price": float(getattr(rec, "entry_price", 0)),
                "price_target": float(getattr(rec, "price_target", 0)),
                "stop_loss": float(getattr(rec, "stop_loss", 0)),
                "allocation": float(getattr(rec, "allocation", 0)),
                "risk_reward_ratio": float(getattr(rec, "risk_reward_ratio", 0)),
                "position_pct": float(getattr(rec, "position_pct", 0)),
                "shares": int(getattr(rec, "shares", 0)),
            }


def _clean_text(value: Optional[str], limit: int = 280) -> str:
    if not value:
        return ""
    text = html_lib.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# A publish time further ahead than this is a bad clock or a mislabelled zone,
# not news from the future. It must never make a feed look fresh.
FEED_FUTURE_TOLERANCE = timedelta(minutes=15)


def _parse_feed_date(value: Optional[str], now: Optional[datetime] = None) -> str:
    """Normalise an RSS/Atom timestamp to ISO-8601 UTC, or "" when unknown.

    A timestamp without a zone is read as UTC on every path. The ISO fallback
    used ``naive.astimezone()``, which reads a naive value as the SERVER's local
    zone: Investing.com's "2026-09-25 11:33:00" (UTC) came out four hours in
    the future on a New York machine.
    """
    if not value:
        return ""
    value = str(value).strip()
    parsed: Optional[datetime] = None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed > (now or datetime.now(timezone.utc)) + FEED_FUTURE_TOLERANCE:
        return ""
    return parsed.isoformat()


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> "re.Pattern[str]":
    """Whole-token matcher for one lexicon term (compiled once per term)."""
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def _has_term(text: str, term: str) -> bool:
    """Does `text` contain `term` as a whole token?

    Plain ``term in text`` was fine while the vocabulary was long English
    words, but the Indian market lexicon is full of three-letter tickers and
    acronyms, and substring matching quietly mis-scored on them: "nse" is
    inside *defense*, *sense*, *license* and *consensus*; "bse" is inside
    *observed*. A US defence story would have scored as Indian market news.

    Tightening this also removes older false positives of the same shape
    ("sec" inside *second* and *sector*), so scores can only get more precise,
    never noisier.
    """
    return bool(_term_pattern(term).search(text))


def _article_category(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    scores = {category: 0 for category in CATEGORY_RULES}
    for category, rules in CATEGORY_RULES.items():
        for keyword, weight in rules.items():
            if _has_term(text, keyword):
                scores[category] += weight

    # "Quarter" is often macro calendar language, not an earnings event.
    # Only treat it as earnings when paired with explicit company-result words.
    if "quarter" in text and any(term in text for term in ("earnings", "eps", "revenue", "profit", "guidance")):
        scores["earnings"] += 3
    if "quarter" in text and any(term in text for term in ("gdp", "economy", "economic", "inflation")):
        scores["macro"] += 4

    # Deals should win when a transaction is the subject; policy headlines should
    # remain visible without being mislabeled as earnings or stock stories.
    priority = {"deals": 5, "macro": 4, "politics": 3, "earnings": 2, "stocks": 1}
    best_category = max(scores, key=lambda category: (scores[category], priority[category]))
    if scores[best_category] == 0:
        return "other"
    return best_category


def _article_category_score(title: str, description: str, category: str) -> int:
    text = f"{title} {description}".lower()
    score = 0
    for keyword, weight in CATEGORY_RULES.get(category, {}).items():
        if _has_term(text, keyword):
            score += weight
    return score


# [NEWS-DIRECTION] Keyword sentiment so the regime engine can read whether the
# *current* headline tape leans bullish, bearish, mixed, or neutral. This is a
# transparent lexicon (NOT a trained model) — it never raises and defaults to
# neutral when nothing matches.
NEWS_BEARISH_TERMS = (
    "selloff", "sell-off", "plunge", "plunges", "slump", "slumps", "tumble", "tumbles",
    "crash", "crashes", "sink", "sinks", "slide", "slides", "drop", "drops", "fall",
    "falls", "fell", "weak", "weaker", "weakness", "recession", "downturn", "contraction",
    "fear", "fears", "warning", "warn", "warns", "cuts guidance", "cut guidance",
    "guidance cut", "misses", "miss", "disappoint", "downgrade", "downgrades", "layoff",
    "layoffs", "job cuts", "bankruptcy", "default", "defaults", "hot inflation",
    "inflation surge", "sticky inflation", "rate hike", "rate hikes", "hawkish",
    "tariff", "tariffs", "sanction", "sanctions", "war", "escalate", "escalation",
    "shutdown", "selloff deepens", "bear market", "correction", "sell pressure",
    "growth scare", "stagflation", "yields surge", "yields spike", "dollar surges",
)
NEWS_BULLISH_TERMS = (
    "rally", "rallies", "surge", "surges", "soar", "soars", "jump", "jumps", "climb",
    "climbs", "gain", "gains", "rebound", "rebounds", "record high", "all-time high",
    "beat", "beats", "tops estimates", "raises guidance", "raise guidance",
    "guidance raise", "upgrade", "upgrades", "rate cut", "rate cuts", "dovish",
    "cools", "cooling", "eases", "easing", "soft landing", "optimism", "optimistic",
    "breakthrough", "deal reached", "agreement reached", "ceasefire", "strong demand",
    "strong results", "outperform", "bull market", "buy the dip", "stimulus",
    "relief rally", "yields fall", "yields ease", "dollar weakens",
)


def _news_direction(title: str, description: str = "") -> str:
    """Classify a headline as bullish / bearish / mixed / neutral via lexicon."""
    text = f"{title} {description}".lower()
    bear = sum(1 for term in NEWS_BEARISH_TERMS if term in text)
    bull = sum(1 for term in NEWS_BULLISH_TERMS if term in text)
    if bull and bear:
        if bull > bear * 1.5:
            return "bullish"
        if bear > bull * 1.5:
            return "bearish"
        return "mixed"
    if bull:
        return "bullish"
    if bear:
        return "bearish"
    return "neutral"


def _is_actionable_catalyst(article: Dict[str, Any]) -> bool:
    category = article.get("category")
    if category not in {"macro", "politics", "earnings", "stocks", "deals"}:
        return False

    title = str(article.get("title") or "").lower()
    description = str(article.get("description") or "").lower()
    text = f"{title} {description}"
    category_score = int(article.get("category_score") or 0)
    impact = int(article.get("impact") or 0)
    global_noise_terms = (
        "besties", "bitter rivals", "jealous", "dating", "divorce", "inherited",
        "lottery", "ebola", "feud", "rivalry", "celebrity", "girlfriend", "boyfriend",
        "wedding", "engaged", "viral video", "tiktok",
    )
    if any(term in title for term in global_noise_terms):
        return False
    title_noise_terms = ("shooting", "assault", "charged with", "indicted", "arrested")
    if any(term in title for term in title_noise_terms):
        return False

    if category == "politics":
        market_policy_terms = (
            "market", "markets", "stock", "stocks", "oil", "crude", "energy", "gas",
            "tariff", "trade", "sanctions", "china", "beijing", "xi", "iran", "russia",
            "ukraine", "israel", "war", "ceasefire", "shipping", "supply", "fed",
            "tax", "budget", "debt", "deficit", "regulation", "bank", "banks",
            "economy", "economic", "inflation", "interest rate", "interest rates", "rates",
            "dollar", "defense",
            # Indian policy that moves Indian equities. Without these a SEBI
            # or RBI headline scored as "politics" and was then dropped for
            # naming no market term the US list happened to contain.
            "sebi", "rbi", "repo rate", "sensex", "nifty", "rupee", "gst",
            "derivatives", "margins", "exchanges", "fii", "dii", "listing",
        )
        general_politics_terms = (
            "shooting", "assault", "charged", "trial", "lawsuit", "immigrant", "ice agent",
            "poll", "campaign rally", "endorsement",
        )
        if any(term in text for term in general_politics_terms) and not any(term in text for term in market_policy_terms):
            return False
        return category_score >= 6 and any(term in text for term in market_policy_terms)

    if category in {"macro", "politics", "deals", "earnings"}:
        return category_score >= 5 or impact >= 3

    stock_title_terms = (
        "stock", "stocks", "shares", "market", "s&p", "nasdaq", "dow", "futures",
        "sector", "analyst", "upgrade", "downgrade", "price target", "rally", "selloff",
        "battery", "lithium", "semiconductor", "chip", "cybersecurity",
    )
    noisy_terms = ("jealous", "dating", "divorce", "inherited", "ebola", "lottery")
    if any(term in title for term in noisy_terms) and not any(term in title for term in stock_title_terms):
        return False
    return impact >= 3 or category_score >= 5 or any(term in title for term in stock_title_terms)


_WRAP_INDEX_TERMS = ("dow", "s&p 500", "s&p", "nasdaq", "russell 2000")
_WRAP_MARKET_TERMS = ("stocks", "stock market", "points", "indexes", "indices", "futures")
_WRAP_STANDALONE = ("stock futures", "u.s. stocks", "us stocks", "stock market", "wall street")


def _is_market_wrap(text: str) -> bool:
    """A headline about the index tape rather than one company.

    Two index names ("Dow, S&P 500 and Nasdaq open higher"), or one index plus
    a whole-market word ("Dow up 400 points as stocks rebound"). "Apple drags
    the Dow lower" names one index and no market word, so it stays a
    single-name story.
    """
    t = (text or "").lower()
    if any(term in t for term in _WRAP_STANDALONE):
        return True
    hits = {term for term in _WRAP_INDEX_TERMS if term in t}
    if "s&p 500" in hits:
        hits.discard("s&p")
    return len(hits) >= 2 or (len(hits) == 1 and any(term in t for term in _WRAP_MARKET_TERMS))


def _article_catalyst_context(category: str, impact: int, title: str) -> Dict[str, str]:
    text = title.lower()
    if category == "macro":
        label = "Macro Catalyst"
        action = "Watch rates, dollar, index beta, and portfolio gross exposure."
        if any(term in text for term in ("fed", "fomc", "rate hike", "rate cut", "interest rate", "interest rates")):
            action = "Policy tape: track front-end yields, dollar strength, and growth multiple pressure."
        elif any(term in text for term in ("inflation", "cpi", "pce")):
            action = "Inflation risk: check pricing power, margin sensitivity, and duration exposure."
        elif any(term in text for term in ("bond", "yield", "treasury")):
            action = "Rate-sensitive tape: watch duration, high-multiple growth, banks, and USD."
        elif any(term in text for term in ("gdp", "economy", "economic", "jobs", "payroll")):
            action = "Growth read-through: compare cyclicals, small caps, credit, and defensive sectors."
        elif any(term in text for term in ("oil", "opec", "crude")):
            action = "Energy shock watch: monitor airlines, transports, consumer margins, and E&P beta."
    elif category == "politics":
        label = "Policy / Geopolitics"
        action = "Policy risk: monitor tariffs, sanctions, defense, energy, and supply-chain exposure."
        if any(term in text for term in ("iran", "israel", "ukraine", "russia", "war", "ceasefire")):
            action = "Geopolitical risk: watch oil, defense, shipping, safe havens, and headline volatility."
        elif any(term in text for term in ("tariff", "china", "beijing", "xi")):
            action = "Trade policy risk: check semis, industrials, retailers, and China revenue exposure."
        elif any(term in text for term in ("white house", "congress", "senate", "election", "trump", "biden")):
            action = "Washington watch: track fiscal, regulatory, and sector-specific policy changes."
    elif category == "earnings":
        label = "Earnings Read-Through"
        action = "Check guidance, margin tone, peer read-through, and next reporting date."
    elif category == "deals":
        label = "Deal Flow"
        action = "Watch spread, regulatory risk, financing terms, and affected peers."
    elif _is_market_wrap(text):
        # "Dow, S&P 500 and Nasdaq open higher..." is the index tape, not a
        # single-name catalyst; it was labelled one and told readers to "watch
        # for sympathy moves".
        label = "Market Wrap"
        action = "Index-level move: context for the tape, not a trigger for any one name."
    else:
        label = "Stock / Sector Catalyst"
        action = "Open a tear sheet before trading; confirm price, volume, valuation, and stop level."
        if any(term in text for term in ("analyst", "upgrade", "downgrade", "price target")):
            action = "Street revision risk: compare target upside with current price and consensus trend."
    urgency = "High impact" if impact >= 8 else ("Medium impact" if impact >= 4 else "Watch")
    return {"catalyst_label": label, "desk_action": action, "urgency": urgency}


def _market_relevant_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep dashboard headlines focused on tradeable market catalysts."""
    return [article for article in articles if _is_actionable_catalyst(article)]


def _recent_articles(articles: List[Dict[str, Any]], max_age_days: int = 14) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    recent = []
    for article in articles:
        published = article.get("published")
        if not published:
            recent.append(article)
            continue
        try:
            published_dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if published_dt.tzinfo is None:
                published_dt = published_dt.replace(tzinfo=timezone.utc)
            if published_dt >= cutoff:
                recent.append(article)
        except Exception:
            recent.append(article)
    return recent


def _article_impact(title: str, description: str, source: str) -> int:
    text = f"{title} {description}".lower()
    score = 0
    for term, weight in IMPACT_TERMS.items():
        if _has_term(text, term):
            score += weight
    if source in {"WSJ Markets", "Barron's", "Reuters Business", "Financial Times Markets"}:
        score += 2
    if "breaking" in text or "live updates" in text:
        score += 3
    return min(score, 100)


# The shared verifying context (system trust plus certifi); see engine/tls.py.
from engine.tls import SSL_CONTEXT as _RSS_SSL_CONTEXT  # noqa: E402


# A feed is LIVE when its newest dated article is younger than this. 72h spans
# a weekend without calling a working wire dead; it is a feed-health rule and
# does not change which articles the tape shows (see _recent_articles).
FEED_LIVE_MAX_AGE_HOURS = 72


def _safe_http_url(url: Any) -> str:
    """The URL when it is an absolute http(s) link, else "" (no javascript:, data:, etc.)."""
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    return text if parts.scheme.lower() in ("http", "https") and parts.netloc else ""


def _fetch_rss_feed(source: Dict[str, str], per_source_limit: int = 10) -> List[Dict[str, Any]]:
    return _fetch_rss_feed_with_status(source, per_source_limit)[0]


def _feed_status(source: Dict[str, str], articles: List[Dict[str, Any]],
                 error: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Classify one feed: live, stale (items but none recent), empty, or error."""
    now = now or datetime.now(timezone.utc)
    dated = []
    for a in articles:
        try:
            dated.append(datetime.fromisoformat(a["published"]))
        except (KeyError, TypeError, ValueError):
            continue
    newest = max(dated) if dated else None
    age_hours = round((now - newest).total_seconds() / 3600.0, 1) if newest else None
    if error:
        state = "error"
    elif not articles:
        state = "empty"
    elif age_hours is not None and age_hours <= FEED_LIVE_MAX_AGE_HOURS:
        state = "live"
    else:
        # Items with no usable date, or only old ones, cannot vouch for today.
        state = "stale"
    return {
        "name": source["name"],
        "url": source["url"],
        "items": len(articles),
        "newest": newest.isoformat() if newest else None,
        "age_hours": age_hours,
        "state": state,
        "error": error,
        "ok": state == "live",
    }


def _fetch_rss_feed_with_status(source: Dict[str, str], per_source_limit: int = 10):
    request = UrlRequest(
        source["url"],
        headers={
            "User-Agent": "QUANTA-Dashboard/1.0 (+local research dashboard)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urlopen(request, timeout=8, context=_RSS_SSL_CONTEXT) as response:
            raw = response.read(2_000_000)
    except (HTTPError, URLError, TimeoutError, OSError, ssl.SSLError) as exc:
        # A certificate failure used to trigger a retry with TLS verification
        # disabled. That is precisely the condition a network attacker
        # manufactures, and the retry accepted whatever they served as market
        # news. An unverifiable feed is an unavailable feed:
        # report it and move on, so a broken trust store gets fixed rather than
        # silently worked around.
        reason = str(getattr(exc, "reason", exc)).lower()
        if "certificate" in reason or "ssl" in reason:
            logger.warning(
                "RSS fetch for %s failed TLS verification — treating the feed as "
                "unavailable. Check the system certificate store: %s",
                source["name"], exc,
            )
        else:
            logger.debug("RSS fetch failed for %s: %s", source["name"], exc)
        code = getattr(exc, "code", None)
        label = (f"HTTP {code}" if code else
                 "TLS verification failed" if ("certificate" in reason or "ssl" in reason) else
                 "timeout" if "timed out" in reason or isinstance(exc, TimeoutError) else
                 "unreachable")
        return [], _feed_status(source, [], error=label)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.debug("RSS parse failed for %s: %s", source["name"], exc)
        return [], _feed_status(source, [], error="unparseable feed")

    items = root.findall(".//item")
    atom_ns = "{http://www.w3.org/2005/Atom}"
    if not items:
        items = root.findall(f".//{atom_ns}entry")

    articles: List[Dict[str, Any]] = []
    for item in items[:per_source_limit]:
        is_atom = item.tag.endswith("entry")
        if is_atom:
            title = _clean_text(item.findtext(f"{atom_ns}title"), 180)
            description = _clean_text(item.findtext(f"{atom_ns}summary") or item.findtext(f"{atom_ns}content"), 260)
            link = ""
            link_el = item.find(f"{atom_ns}link")
            if link_el is not None:
                link = link_el.attrib.get("href", "")
            published = _parse_feed_date(item.findtext(f"{atom_ns}updated") or item.findtext(f"{atom_ns}published"))
        else:
            title = _clean_text(item.findtext("title"), 180)
            description = _clean_text(item.findtext("description"), 260)
            link = _clean_text(item.findtext("link"), 500)
            published = _parse_feed_date(item.findtext("pubDate") or item.findtext("published"))

        if not title:
            continue
        # A feed is third-party input: only an http(s) link may reach the page.
        link = _safe_http_url(link)

        category = _article_category(title, description)
        category_score = _article_category_score(title, description, category)
        impact = _article_impact(title, description, source["name"])
        direction = _news_direction(title, description)
        context = _article_catalyst_context(category, impact, title)
        articles.append({
            "title": title,
            "description": description,
            "source": source["name"],
            "url": link,
            "published": published,
            "category": category,
            "category_score": category_score,
            "impact": impact,
            "direction": direction,
            **context,
        })

    return articles, _feed_status(source, articles)


def _dedupe_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for article in articles:
        key = re.sub(r"[^a-z0-9]+", "", article.get("title", "").lower())[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique


def _published_sort_key(article: Dict[str, Any]) -> str:
    return article.get("published") or "1970-01-01T00:00:00+00:00"


def _extract_trends(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    trends: Dict[str, Dict[str, Any]] = {}
    macro_terms = {
        "Fed": ["fed", "fomc", "rate cut", "rate hike"],
        "Inflation": ["inflation", "cpi", "pce"],
        "AI": [" ai ", "artificial intelligence", "nvidia"],
        "Oil": ["oil", "opec", "crude"],
        "Treasury Yields": ["treasury", "yield", "bond"],
        "Earnings": ["earnings", "guidance", "eps", "quarter"],
        "China": ["china", "beijing", "yuan"],
        "M&A": ["merger", "acquisition", "buyout"],
    }

    for article in articles:
        text = f" {article.get('title', '')} {article.get('description', '')} "
        impact = int(article.get("impact") or 1)

        # Only count tickers that explicitly use the $ cashtag prefix, OR
        # bare uppercase that's at least 3 chars AND not in our stopword list.
        for cash_prefix, ticker in re.findall(r"(?<![A-Z$])(\$?)([A-Z]{2,5})(?![A-Z])", text):
            ticker_upper = ticker.upper()
            if ticker_upper in TREND_STOPWORDS:
                continue
            # Bare (no $) words must be 4+ chars to reduce false positives
            if not cash_prefix and len(ticker) < 4:
                continue
            # Require either a cashtag, or appearance in a known mega-cap list
            known = ticker_upper in MARKET_UNIVERSES["US"]["mega_caps"] or any(
                ticker_upper == s.split(".")[0] for s in MARKET_UNIVERSES["IN"]["mega_caps"]
            )
            if not cash_prefix and not known:
                continue
            key = f"${ticker_upper}"
            trends.setdefault(key, {"label": key, "count": 0, "score": 0, "type": "ticker"})
            trends[key]["count"] += 1
            trends[key]["score"] += impact + 2

        lower_text = text.lower()
        for label, keywords in macro_terms.items():
            if any(keyword in lower_text for keyword in keywords):
                trends.setdefault(label, {"label": label, "count": 0, "score": 0, "type": "theme"})
                trends[label]["count"] += 1
                trends[label]["score"] += impact + 1

    ranked = sorted(trends.values(), key=lambda item: (item["score"], item["count"]), reverse=True)
    return ranked[:12]


# Terms that mark a headline as belonging to a given market's own tape.
HOME_MARKET_TERMS = {
    "IN": ("sensex", "nifty", "rbi", "repo rate", "sebi", "rupee", "bse", "nse",
           "mumbai", "india", "indian", "crore", "lakh", "dalal street",
           "union budget", "gst", "fii", "dii"),
    "US": ("wall street", "s&p", "nasdaq", "dow", "fed", "fomc", "treasury",
           "sec", "white house", "u.s.", "us stocks"),
}
HOME_MARKET_BONUS = 10


def _home_market_bonus(article: Dict[str, Any], market: Any = "US") -> int:
    """Rank weight for how much a headline belongs to the selected market.

    Returns ``+HOME_MARKET_BONUS`` for a domestic story, ``-HOME_MARKET_BONUS``
    for one explicitly about a different market, and 0 for anything general
    (crude, the dollar, a global risk event), which is genuinely relevant
    everywhere and should rank on its own impact alone.

    The penalty matters as much as the bonus. Indian outlets syndicate US
    coverage that scores heavily on the US macro lexicon, so a bonus by itself
    could not lift a Nifty wrap above "US stocks end lower as jobs data fuels
    hawkish Fed bets" -- the foreign story simply started 10 points ahead.

    A domestic mention wins over a foreign one when both appear, so an Indian
    story about the Fed's effect on Indian equities counts as domestic.

    Ranking only: it never edits the article's published impact and never
    drops a story. A major global event still outranks domestic trivia.
    """
    key = _normalize_market(market)
    text = f"{article.get('title') or ''} {article.get('description') or ''}".lower()
    if any(_has_term(text, term) for term in HOME_MARKET_TERMS.get(key, ())):
        return HOME_MARKET_BONUS
    for other, terms in HOME_MARKET_TERMS.items():
        if other != key and any(_has_term(text, term) for term in terms):
            return -HOME_MARKET_BONUS
    return 0


def _build_news_digest(market: Any = "US") -> Dict[str, Any]:
    """Build the headline tape for one market.

    The feed list follows the selected market, so India gets Indian wires
    (plus the global macro feeds) instead of a US tape with its Indian
    headlines filtered out.
    """
    feeds = _feeds_for_market(market)
    articles: List[Dict[str, Any]] = []
    source_status = []

    with ThreadPoolExecutor(max_workers=min(8, len(feeds))) as executor:
        futures = {executor.submit(_fetch_rss_feed_with_status, source): source for source in feeds}
        for future in as_completed(futures):
            source = futures[future]
            try:
                fetched, status = future.result()
            except Exception as exc:
                logger.debug("RSS worker failed for %s: %s", source["name"], exc)
                fetched, status = [], _feed_status(source, [], error=type(exc).__name__)
            articles.extend(fetched)
            source_status.append(status)
    # Stable, configuration order for the UI ledger (completion order is noise).
    order = {s["name"]: i for i, s in enumerate(feeds)}
    source_status.sort(key=lambda s: order.get(s["name"], len(order)))

    articles = _market_relevant_articles(_recent_articles(_dedupe_articles(articles)))
    # Stamp each article with how much it belongs to the selected market, so
    # every downstream consumer (the big-news list here, the regime engine's
    # market drivers, the headline bar) ranks on the same basis without each
    # one needing to be handed the market code.
    for _a in articles:
        _a["home_rank"] = _home_market_bonus(_a, market)
    latest = sorted(articles, key=_published_sort_key, reverse=True)[:36]
    # [HOME-MARKET RANK] Indian outlets syndicate a lot of US coverage, so
    # selecting India and ranking on raw impact alone put "US stocks end lower
    # as jobs data fuels hawkish Fed" above the Nifty wrap -- Indian sources
    # carrying foreign news, which is not what the country toggle promises.
    # A domestic story outranks a foreign one of equal weight; global macro
    # still appears, just below the home market. Ordering only: the published
    # `impact` each card shows is untouched.
    big_news = sorted(
        articles,
        key=lambda item: (
            (item.get("impact") or 0) + (item.get("home_rank") or 0),
            _published_sort_key(item),
        ),
        reverse=True,
    )[:12]

    grouped = {
        "latest": latest,
        "big_news": big_news,
        "stocks": [a for a in latest if a.get("category") == "stocks"][:14],
        "macro": [a for a in latest if a.get("category") == "macro"][:14],
        "politics": [a for a in latest if a.get("category") == "politics"][:14],
        "earnings": [a for a in latest if a.get("category") == "earnings"][:14],
        "deals": [a for a in latest if a.get("category") == "deals"][:14],
    }

    return {
        "articles": latest,
        "groups": grouped,
        "trends": _extract_trends(latest),
        "sources": FINANCE_SOURCE_LINKS,
        "source_status": source_status,
        "fetched_count": len(articles),
    }


_tape_quote_cache: Dict[str, Dict[str, Any]] = {}
_tape_quote_lock = threading.Lock()
TAPE_QUOTE_CACHE_SECONDS = 60
_snapshot_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='market-tape')
# Index histories run on their own pool, at the same time as the quotes, so a slow
# quote can never use up the time the trend read needs (and vice versa).
_trend_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='market-trend')
_trend_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_trend_inflight: Dict[str, Future] = {}
_trend_lock = threading.Lock()


def _completed_snapshot_jobs(futures, timeout):
    """Return completed jobs without waiting for stalled provider threads."""
    try:
        yield from as_completed(futures, timeout=timeout)
    except FuturesTimeout:
        logger.warning('Market snapshot exceeded its %ss provider budget', timeout)
    finally:
        for future in futures:
            future.cancel()


def _quote_snapshot(symbol: str, label: str) -> Dict[str, Any]:
    with _tape_quote_lock:
        cached = _tape_quote_cache.get(symbol)
    if cached and time.time() - cached['timestamp'] < TAPE_QUOTE_CACHE_SECONDS:
        return {**cached['payload'], 'label': label, 'cached': True}
    payload = _fetch_tape_quote(symbol, label)
    if payload.get('price') is not None:
        # ``as_of`` is when WE retrieved the quote; ``quote_date`` (set by the
        # fetch) is the session the price belongs to. They are different facts.
        payload['as_of'] = _now_iso()
        payload['retrieved_at'] = payload['as_of']
        with _tape_quote_lock:
            _tape_quote_cache[symbol] = {'timestamp': time.time(), 'payload': payload}
        return payload
    if cached:
        return {**cached['payload'], 'label': label, 'stale': True, 'cached': True}
    return payload


def _session_price_from_meta(meta: Any, last_bar_date: Optional[str]) -> Optional[Tuple[float, str]]:
    """(price, session date) from Yahoo's chart metadata when it is newer than
    the last complete daily bar; None otherwise. The date is the exchange's own."""
    if not isinstance(meta, dict):
        return None
    try:
        price = float(meta.get("regularMarketPrice"))
    except (TypeError, ValueError):
        return None
    if price != price:  # NaN
        return None
    when = meta.get("regularMarketTime")
    if price is None or price <= 0 or when is None:
        return None
    try:
        if hasattr(when, "date"):
            day = when.date().isoformat()
        else:
            tz_name = meta.get("exchangeTimezoneName") or "America/New_York"
            from zoneinfo import ZoneInfo
            day = datetime.fromtimestamp(int(when), ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return None
    if last_bar_date and day <= last_bar_date:
        return None
    return round(float(price), 4), day


def _session_price_from_intraday(ticker: Any, last_bar_date: Optional[str]) -> Optional[Tuple[float, str]]:
    """(last trade, session date) from today's 5-minute bars when that session
    is newer than the last complete daily bar."""
    try:
        intra = ticker.history(period="1d", interval="5m", timeout=8)
        closes = intra["Close"].dropna() if intra is not None and not intra.empty else []
        if not len(closes):
            return None
        ix = closes.index[-1]
        day = ix.date().isoformat() if hasattr(ix, "date") else str(ix)[:10]
        if last_bar_date and day <= last_bar_date:
            return None
        # The last 5-minute bar is not the closing auction: when the response
        # metadata is at hand (a fresh fetch), its regular-market price is exact.
        meta = getattr(getattr(ticker, "_price_history", None), "_history_metadata", None)
        exact = _session_price_from_meta(meta, last_bar_date)
        if exact is not None and exact[1] == day:
            return exact
        return round(float(closes.iloc[-1]), 4), day
    except Exception:
        return None


def _fetch_tape_quote(symbol: str, label: str) -> Dict[str, Any]:
    empty = {'symbol': symbol, 'label': label, 'price': None, 'change_pct': None}
    try:
        import yfinance as yf_local
        ticker = yf_local.Ticker(symbol)
        # An empty daily-history response must not suppress a usable quote.
        quote_date = None
        price_basis = 'daily_bar'
        try:
            hist = ticker.history(period="5d", interval="1d", timeout=8)
            close = hist['Close'].dropna() if hist is not None and not hist.empty else []
            last = _opt_round(close.iloc[-1], 4) if len(close) else None
            previous = _opt_round(close.iloc[-2], 4) if len(close) > 1 else None
            if len(close):
                last_ix = close.index[-1]
                quote_date = last_ix.date().isoformat() if hasattr(last_ix, 'date') else str(last_ix)[:10]
            # Yahoo often returns the current session's daily bar with a blank
            # close (ETFs especially). Dropping it made yesterday's close read
            # as today's price and yesterday's change as today's. The same
            # response's metadata carries the real last price and its time.
            newer = None
            placeholder = (hist is not None and not hist.empty and len(close)
                           and hist.index[-1] > close.index[-1])
            if placeholder:
                # A cached frame keeps the blank row but not the metadata: read
                # the session's last trade from today's intraday bars instead.
                newer = _session_price_from_intraday(ticker, quote_date)
            if newer is None:
                try:
                    newer = _session_price_from_meta(ticker.history_metadata, quote_date)
                except Exception:
                    newer = None
            if newer is not None:
                previous, last, quote_date = last, newer[0], newer[1]
                price_basis = 'regular_market_price'
        except Exception:
            last = previous = None
        if last is not None and previous is None:
            # A one-row daily series (NSE sector indices): the previous session
            # comes from the hourly bars, else from the quote's previous close.
            days = _daily_closes_from_hourly(ticker, period="1mo")
            prior = [c for d, c in days if quote_date and d < quote_date]
            if prior:
                previous = prior[-1]
            else:
                previous = _fast_info_get(ticker.fast_info, 'previous_close')
        if last is None or last <= 0:
            fi = ticker.fast_info
            last = _fast_info_get(fi, 'last_price')
            previous = _fast_info_get(fi, 'previous_close')
            # A last-trade price carries no session date from this feed.
            quote_date = None
            price_basis = 'last_price'
        if last is None or last <= 0:
            return empty
        change_pct = ((last - previous) / previous) * 100 if previous and previous > 0 else None
        return {
            "symbol": symbol,
            "label": label,
            "price": round(last, 2),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "quote_date": quote_date,
            "price_basis": price_basis,
        }
    except Exception as exc:
        logger.debug("Quote fetch failed for %s: %s", symbol, exc)
        return empty


def _session_quote_from_history(hist: Any) -> Optional[Dict[str, Any]]:
    """Derive the latest session's OHLC / previous-close quote from a history frame.

    The deep-dive already pulls a daily bar series for the price chart, so the
    exchange-style quote line (Open / High / Low / Prev Close / LTP) is a pure
    read off the last two rows — no extra provider call, no extra rate-limit
    exposure. Returns None when the frame can't supply a usable last bar.
    """
    try:
        if hist is None or getattr(hist, "empty", True) or len(hist) == 0:
            return None
        last_bar = hist.iloc[-1]
        ltp = _opt_round(last_bar.get("Close"), 4)
        if ltp is None:
            return None
        prev_close = _opt_round(hist.iloc[-2].get("Close"), 4) if len(hist) > 1 else None
        # Volume can be NaN on a partial/holiday bar. A bare int(NaN) raises
        # ValueError and used to take the whole session block down with it.
        volume = _opt_round(last_bar.get("Volume"), 0)
        session: Dict[str, Any] = {
            "open": _opt_round(last_bar.get("Open"), 4),
            "day_high": _opt_round(last_bar.get("High"), 4),
            "day_low": _opt_round(last_bar.get("Low"), 4),
            "prev_close": prev_close,
            "ltp": ltp,
            "volume": int(volume) if volume and volume > 0 else None,
        }
        if prev_close:
            session["change_abs"] = round(ltp - prev_close, 4)
            session["change_pct"] = round(((ltp - prev_close) / prev_close) * 100, 2)
        # 52-week band from whatever slice of history we hold (labelled by the
        # actual bar count so the UI never claims a full year it doesn't have).
        try:
            highs = hist["High"].dropna()
            lows = hist["Low"].dropna()
            if not highs.empty and not lows.empty:
                session["range_high"] = _opt_round(highs.max(), 4)
                session["range_low"] = _opt_round(lows.min(), 4)
                session["range_bars"] = int(len(hist))
        except Exception:
            pass
        try:
            idx = hist.index[-1]
            session["date"] = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        except Exception:
            pass
        return session
    except Exception:
        return None


def _price_chart_snapshot(symbol: str, period: str = "6mo") -> Dict[str, Any]:
    """Return a compact daily close series for the web tear sheet.

    [PROVIDER-RESILIENCE] Short-TTL cache + cooldown gate. When the shared
    PROVIDER_STATE is in cooldown we skip yfinance entirely and return cached
    points if available, or an empty placeholder labelled `degraded=True`.
    """
    # 1. Cooldown gate — never hammer Yahoo while a rate-limit cooldown is
    #    active. The scan circuit and the deep-dive share the same tracker.
    in_cooldown = _provider_in_cooldown()
    cached = _price_chart_cache_get(symbol, period)
    if cached is not None:
        if in_cooldown:
            return {**cached, "from_cache": True, "degraded": True}
        return {**cached, "from_cache": True}
    if in_cooldown:
        # No cache, provider in cooldown — return a structured empty payload
        # rather than triggering a rate-limit storm.
        return {"period": period, "points": [], "degraded": True, "from_cache": False,
                "cooldown_remaining_seconds": _provider_cooldown_remaining()}

    try:
        import yfinance as yf_local
        ticker = yf_local.Ticker(symbol)
        hist = ticker.history(period=period, interval="1d")
        if hist is None or hist.empty or "Close" not in hist:
            return {"period": period, "points": []}

        hist = hist.dropna(subset=["Close"]).tail(120)
        points = []
        for idx, row in hist.iterrows():
            close = row.get("Close")
            if close is None:
                continue
            points.append({
                "date": idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
                "close": round(float(close), 4),
                "volume": int(row.get("Volume") or 0),
            })

        closes = [point["close"] for point in points]
        payload = {
            "period": period,
            "points": points,
            "min": min(closes) if closes else None,
            "max": max(closes) if closes else None,
            "last": closes[-1] if closes else None,
            "session": _session_quote_from_history(hist),
        }
        _price_chart_cache_set(symbol, period, payload)
        _provider_note_success()
        return payload
    except Exception as exc:
        # [PROVIDER-RESILIENCE] Detect provider rate limits and seed cooldown
        # so concurrent components (tear sheet, picks, health probe) all back
        # off together instead of independently retrying.
        if _provider_classify_rate_limit(exc):
            _provider_note_rate_limit()
        logger.debug("Price chart snapshot failed for %s: %s", symbol, exc)
        if cached is not None:  # cache miss path can't reach here, but be safe
            return {**cached, "from_cache": True, "degraded": True}
        return {"period": period, "points": [], "degraded": True}


def _trend_metrics_job(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch one symbol's trend metrics and keep a usable result in the cache,
    even when the market read that asked for it has stopped waiting."""
    try:
        tm = _trend_metrics(symbol)
        if tm:
            with _trend_lock:
                _trend_cache[symbol] = (time.time(), tm)
        return tm
    finally:
        with _trend_lock:
            _trend_inflight.pop(symbol, None)


def _trend_future(symbol: str) -> Future:
    """A future for one symbol's trend metrics: done at once from a fresh cache
    entry, the fetch already running for this symbol, or a new fetch."""
    with _trend_lock:
        cached = _trend_cache.get(symbol)
        if cached and time.time() - cached[0] < float(_T.MARKET.TREND_CACHE_SECONDS):
            done: Future = Future()
            done.set_result(cached[1])
            return done
        running = _trend_inflight.get(symbol)
        if running is not None and not running.done():
            return running
        fut = _trend_executor.submit(_trend_metrics_job, symbol)
        _trend_inflight[symbol] = fut
        return fut


def _daily_closes_from_hourly(ticker: Any, period: str = "1y") -> List[Tuple[str, float]]:
    """(session date, close) pairs built from hourly bars: the last hourly close
    of each exchange day. Yahoo serves most NSE sector indices (^CNXAUTO,
    ^CNXFMCG, ...) with a one-row daily series but a full hourly one, so a
    daily read of them has no previous close and no 50-day average."""
    try:
        hist = ticker.history(period=period, interval="60m")
        if hist is None or hist.empty:
            return []
        close = hist["Close"].dropna()
        out: Dict[str, float] = {}
        for ix, v in close.items():
            out[ix.date().isoformat() if hasattr(ix, "date") else str(ix)[:10]] = float(v)
        return sorted(out.items())
    except Exception as exc:
        logger.debug("Hourly closes failed: %s", exc)
        return []


def _trend_metrics(symbol: str) -> Optional[Dict[str, Any]]:
    """Compute *structural* (multi-month) trend metrics from ~1y of daily history.

    Returns position vs the 50/200-day moving averages and 1-month momentum so
    the regime model reads a real trend, not a single day's move. Returns None
    on any failure so callers fall back to the daily-change heuristic.
    """
    try:
        import yfinance as yf_local
        ticker = yf_local.Ticker(symbol)
        hist = ticker.history(period="1y", interval="1d")
        closes = [float(c) for c in hist["Close"].dropna().tolist()] if hist is not None and not hist.empty else []
        if len(closes) < TREND_MIN_SESSIONS:
            hourly = [c for _d, c in _daily_closes_from_hourly(ticker)]
            if len(hourly) > len(closes):
                closes = hourly
        return _trend_metrics_from_closes(symbol, closes) if closes else None
    except Exception as exc:
        logger.debug("Trend metrics failed for %s: %s", symbol, exc)
        return None


# A 200-day moving average needs 200 sessions. Anything less is a different
# indicator wearing the same name.
SMA200_MIN_SESSIONS = 200
SMA50_MIN_SESSIONS = 50
TREND_MIN_SESSIONS = 60


def _trend_metrics_from_closes(symbol: str, closes: List[float]) -> Optional[Dict[str, Any]]:
    """Structural trend metrics from a close series, with honest coverage.

    The `sma200` field used to fall back to `closes.mean()` whenever there were
    at least 150 closes — and a one-year `history()` call on a recently listed
    name can return far fewer. That published, for example, a ~60-bar average
    labelled "200-day", and `above_sma200` / `sma50_gt_sma200` (a golden-cross
    test the regime model consumes) were evaluated against it. Insufficient
    history now returns None for those three fields, plus the coverage actually
    observed, so a caller can say "not available yet" instead of comparing
    against a different indicator.
    """
    n = len(closes)
    if n < TREND_MIN_SESSIONS:
        return None

    last = float(closes[-1])
    sma50 = float(sum(closes[-SMA50_MIN_SESSIONS:]) / min(n, SMA50_MIN_SESSIONS))

    if n >= SMA200_MIN_SESSIONS:
        window = closes[-SMA200_MIN_SESSIONS:]
        sma200 = float(sum(window) / len(window))
        above_sma200 = last > sma200
        sma50_gt_sma200 = sma50 > sma200
    else:
        sma200 = above_sma200 = sma50_gt_sma200 = None

    mom_21d = ((last / float(closes[-22]) - 1) * 100) if n > 22 else None
    return {
        "symbol": symbol,
        "last": round(last, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2) if sma200 is not None else None,
        "above_sma50": last > sma50,
        "above_sma200": above_sma200,
        "sma50_gt_sma200": sma50_gt_sma200,
        # Coverage the 200-day figure is (or is not) based on.
        "sma200_sessions": n,
        "sma200_sessions_required": SMA200_MIN_SESSIONS,
        "mom_21d_pct": round(mom_21d, 2) if mom_21d is not None else None,
    }


# Index names for the participation members, from the markets' own index lists.
_INDEX_LABELS = {sym: name for u in MARKET_UNIVERSES.values() for sym, name in u["index_symbols"]}


def _index_participation(basket_metrics: List[Dict[str, Any]],
                         requested_symbols: List[str]) -> Dict[str, Any]:
    """How many of a small INDEX PROXY basket sit above their 50-day average.

    This is not exchange breadth. The basket is four heavily overlapping index
    ETFs (or four Indian indices), so "75%" means three of four proxies, not
    75% of listed constituents — labelling it "Breadth" implied constituent
    coverage the implementation never had.

    A member whose history could not be fetched is reported in `missing` rather
    than dropped from the denominator, so a partial sample cannot masquerade as
    a complete one.
    """
    measured_symbols = [m.get("symbol") for m in basket_metrics]
    missing = [s for s in requested_symbols if s not in measured_symbols]
    measured = len(basket_metrics)
    above = sum(1 for m in basket_metrics if m.get("above_sma50"))

    pct = round(above / measured * 100) if measured else None
    label = (
        f"Index participation: {above}/{measured} above 50-day MA"
        if measured else "Index participation unavailable"
    )
    if missing:
        label += f" ({len(missing)} of {len(requested_symbols)} proxies unavailable)"

    return {
        "pct": pct,
        "above": above,
        "measured": measured,
        "requested": len(requested_symbols),
        "missing": missing,
        "label": label,
        "basis": "index_proxies",
        "members": [{"symbol": m.get("symbol"), "label": _INDEX_LABELS.get(m.get("symbol"), m.get("symbol")),
                     "above": bool(m.get("above_sma50"))} for m in basket_metrics],
    }


# [WHAT-CHANGED] Persist a small once-per-day regime snapshot so the dashboard
# can show honest day-over-day deltas (not fabricated). One row per market per
# day; we diff today against the most recent PRIOR calendar day.
_REGIME_HISTORY_PATH = Path(os.environ.get("QUANTA_REGIME_HISTORY_PATH", str(WEBAPP_DIR / ".regime_history.json")))
_regime_history_lock = threading.Lock()


def _load_regime_history() -> Dict[str, List[Dict[str, Any]]]:
    try:
        with open(_REGIME_HISTORY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _record_and_diff_regime(market_key: str, snap: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert today's regime snapshot and return human-readable changes vs the
    most recent prior day. Honest: if there's no prior day, flag baseline."""
    today = datetime.now().date().isoformat()
    changes: List[Dict[str, str]] = []
    baseline = True
    try:
        with _regime_history_lock:
            hist = _load_regime_history()
            rows = [r for r in hist.get(market_key, []) if isinstance(r, dict)]
            prior = None
            for r in sorted(rows, key=lambda x: str(x.get("date", "")), reverse=True):
                if str(r.get("date")) < today:
                    prior = r
                    break

            if prior:
                baseline = False
                # The engine's read first: posture, direction and factor flips.
                if prior.get("posture") and snap.get("posture") and prior["posture"] != snap["posture"]:
                    changes.append({"text": f"Posture moved {prior['posture']} \u2192 {snap['posture']}",
                                    "tone": "neutral", "kind": "posture", "label": "Posture",
                                    "from": prior["posture"], "to": snap["posture"]})
                pd_, nd_ = prior.get("direction"), snap.get("direction")
                if pd_ is not None and nd_ is not None and nd_ != pd_:
                    changes.append({"text": f"Direction {pd_} \u2192 {nd_} of 100",
                                    "tone": "bull" if nd_ > pd_ else "bear", "kind": "direction",
                                    "label": "Direction score", "from": pd_, "to": nd_})
                pef, nef = prior.get("engine_factors") or {}, snap.get("engine_factors") or {}
                for key, now_f in nef.items():
                    was = (pef.get(key) or {}).get("score")
                    if was is not None and was != now_f.get("score"):
                        changes.append({"text": f"{now_f.get('label') or key} {was:+d} \u2192 {now_f.get('score'):+d}",
                                        "tone": "bull" if now_f.get("score") > was else "bear", "kind": "factor",
                                        "label": now_f.get("label") or key, "from": was, "to": now_f.get("score")})
                if prior.get("tone") and snap.get("tone") and prior["tone"] != snap["tone"] and not snap.get("posture"):
                    changes.append({"text": f"Regime shifted {prior['tone']} \u2192 {snap['tone']}",
                                    "tone": "bull" if snap["tone"] == "Risk-on" else ("bear" if snap["tone"] == "Risk-off" else "neutral")})
                pv, nv = prior.get("vix"), snap.get("vix")
                if pv is not None and nv is not None and abs(nv - pv) >= 1.0:
                    up = nv > pv
                    changes.append({"text": f"VIX {'rose' if up else 'fell'} {pv:.1f} \u2192 {nv:.1f}",
                                    "tone": "bear" if up else "bull"})
                pp, npart = prior.get("participation"), snap.get("participation")
                if pp is not None and npart is not None and abs(npart - pp) >= 25:
                    up = npart > pp
                    changes.append({"text": f"Participation {'improved' if up else 'deteriorated'} {pp:.0f}% \u2192 {npart:.0f}%",
                                    "tone": "bull" if up else "bear"})
                py, ny = prior.get("ten_year"), snap.get("ten_year")
                if py is not None and ny is not None and abs(ny - py) >= 0.05:
                    up = ny > py
                    changes.append({"text": f"10Y yield {'rose' if up else 'fell'} {py:.2f} \u2192 {ny:.2f}",
                                    "tone": "bear" if up else "bull"})
                pf = prior.get("factors") or {}
                nf = snap.get("factors") or {}
                sign = lambda v: 1 if (v or 0) > 0 else (-1 if (v or 0) < 0 else 0)
                word = {1: "supportive", 0: "neutral", -1: "a headwind"}
                for key, label in (("trend", "Trend"), ("vol", "Volatility"),
                                   ("breadth", "Participation"), ("credit", "Rates")):
                    if key in pf and key in nf and sign(pf[key]) != sign(nf[key]):
                        changes.append({"text": f"{label} turned {word[sign(nf[key])]}",
                                        "tone": "bull" if sign(nf[key]) > sign(pf[key]) else "bear",
                                        "kind": "factor", "label": label,
                                        "from": word[sign(pf[key])], "to": word[sign(nf[key])]})

            rows = [r for r in rows if str(r.get("date")) != today]
            rows.append({"date": today, "tone": snap.get("tone"), "net": snap.get("net"),
                         "vix": snap.get("vix"), "participation": snap.get("participation"),
                         "ten_year": snap.get("ten_year"), "factors": snap.get("factors"),
                         **{k: snap[k] for k in ("posture", "regime", "direction", "confidence", "engine_factors") if k in snap}})
            hist[market_key] = sorted(rows, key=lambda x: str(x.get("date")))[-60:]
            # The strip: one cell per recorded session, newest last. Rows from
            # before the engine was recorded carry only the 4-factor tone.
            n_hist = int(_T.MARKET.REGIME_HISTORY_SESSIONS)
            history = [{"date": r.get("date"), "posture": r.get("posture"), "regime": r.get("regime"),
                        "direction": r.get("direction"), "confidence": r.get("confidence"), "tone": r.get("tone")}
                       for r in hist[market_key]
                       # A row with neither the engine's read nor any factor
                       # score is not a read (early test runs wrote such rows).
                       if r.get("posture") or r.get("factors")][-n_hist:]
            try:
                with open(_REGIME_HISTORY_PATH, "w", encoding="utf-8") as fh:
                    json.dump(hist, fh)
            except OSError as exc:
                logger.debug("Could not persist regime history: %s", exc)
    except Exception as exc:
        logger.debug("Regime diff failed: %s", exc)
        return {"changes": [], "baseline": True, "history": []}
    return {"changes": changes[:6], "baseline": baseline, "history": history}


def _regime_change_block(market_key: str, tone: str, tone_factors: List[Dict[str, Any]],
                         vix: Optional[float], participation: Optional[float],
                         ten_year: Optional[float], engine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the payload fragment with honest day-over-day regime deltas and
    the recorded sessions behind the history strip."""
    factors = {f.get("key"): f.get("score") for f in (tone_factors or []) if f.get("key")}
    snap = {
        "tone": tone,
        "net": sum(int(f.get("score") or 0) for f in (tone_factors or [])),
        "vix": vix, "participation": participation, "ten_year": ten_year,
        "factors": factors,
    }
    eng = engine if isinstance(engine, dict) else {}
    eng_factors = [f for f in (eng.get("factors") or []) if isinstance(f, dict) and f.get("key")]
    if eng.get("posture") and eng_factors and not (eng.get("dataFreshness") or {}).get("stale"):
        # The engine's own read: the posture, the 0-100 direction score (the
        # factor sum rescaled exactly as the page shows it), the confidence,
        # and each factor's score, so tomorrow can say which factor flipped.
        total = sum(int(f.get("score") or 0) for f in eng_factors)
        span = 2 * len(eng_factors)
        snap.update({
            "posture": eng.get("posture"),
            "regime": eng.get("marketRegimeBase") or eng.get("marketRegime"),
            "direction": round(50 + (total / span) * 50) if span else None,
            "confidence": eng.get("confidenceScore"),
            "engine_factors": {f["key"]: {"label": f.get("label") or f["key"], "score": int(f.get("score") or 0)}
                               for f in eng_factors},
        })
    result = _record_and_diff_regime(market_key, snap)
    return {"regime_changes": result.get("changes", []), "regime_baseline": result.get("baseline", True),
            "regime_history": result.get("history", [])}


def _summarize_news_for_engine(news: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Condense the live headline tape into a regime-ready news-impact summary.

    Returns counts + net direction of high-impact items, a one-line summary,
    the top market drivers, and the dated/headline catalyst risks. Pure and
    defensive: an empty/None digest yields a neutral, empty summary.
    """
    empty = {
        "highImpactCount": 0,
        "bullishHighImpact": 0,
        "bearishHighImpact": 0,
        "netDirection": "neutral",
        "summary": "No high-impact market headlines on the tape right now.",
        "drivers": [],
        "catalysts": [],
        "score": 0,
    }
    if not isinstance(news, dict):
        return empty
    groups = news.get("groups") or {}
    pool = groups.get("big_news") or groups.get("latest") or news.get("articles") or []
    if not isinstance(pool, list) or not pool:
        return empty

    HIGH = 6  # impact threshold for "high impact"
    impact_word = lambda imp: "High" if imp >= HIGH else ("Medium" if imp >= 4 else "Low")

    def _age_days(article: Dict[str, Any]) -> Optional[float]:
        published = article.get("published")
        if not published:
            return None
        try:
            dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        except Exception:
            return None

    def _decayed_impact(article: Dict[str, Any]) -> float:
        # Headlines age out of the regime call: full weight inside ~1 day,
        # half weight by ~4 days, near-zero past a week. Undated items keep
        # full weight (they came from a live fetch this cycle).
        raw = float(int(article.get("impact") or 0))
        age = _age_days(article)
        if age is None:
            return raw
        return raw * (0.5 ** (age / 4.0))

    high_items = [a for a in pool if isinstance(a, dict) and _decayed_impact(a) >= HIGH]
    bullish = sum(1 for a in high_items if a.get("direction") == "bullish")
    bearish = sum(1 for a in high_items if a.get("direction") == "bearish")

    if bearish > bullish:
        net = "bearish"
    elif bullish > bearish:
        net = "bullish"
    elif high_items:
        net = "mixed"
    else:
        net = "neutral"

    # News score nudges the regime: bearish high-impact macro risk weighs more.
    score = max(-2, min(2, bullish - bearish))

    drivers: List[Dict[str, Any]] = []
    # Home-market first: the headline bar is the most prominent news surface on
    # the dashboard, so a foreign story leading it is the most visible way for
    # the tape to contradict the selected country.
    for a in sorted(high_items,
                    key=lambda x: _decayed_impact(x) + (x.get("home_rank") or 0),
                    reverse=True)[:4]:
        drivers.append({
            "title": a.get("title"),
            "category": a.get("category"),
            "direction": a.get("direction") or "neutral",
            "impactLevel": impact_word(int(a.get("impact") or 0)),
            "marketMeaning": a.get("action") or a.get("label") or "",
            "source": a.get("source"),
            "url": a.get("url"),
        })

    catalysts: List[Dict[str, Any]] = []
    for a in pool:
        if not isinstance(a, dict):
            continue
        if a.get("category") in {"macro", "politics"} and int(a.get("impact") or 0) >= 5:
            catalysts.append({
                "title": a.get("title"),
                "category": a.get("category"),
                "direction": a.get("direction") or "neutral",
                "impactLevel": impact_word(int(a.get("impact") or 0)),
            })
        if len(catalysts) >= 4:
            break

    if net == "bearish":
        summary = f"{bearish} high-impact bearish headline(s) lead the tape — treat rallies with caution."
    elif net == "bullish":
        summary = f"{bullish} high-impact bullish headline(s) lead the tape — supportive backdrop, still confirm price."
    elif net == "mixed":
        summary = f"{len(high_items)} high-impact headlines are split bullish/bearish — headline risk is two-sided."
    else:
        summary = "No high-impact market headlines on the tape right now."

    return {
        "highImpactCount": len(high_items),
        "bullishHighImpact": bullish,
        "bearishHighImpact": bearish,
        "netDirection": net,
        "summary": summary,
        "drivers": drivers,
        "catalysts": catalysts,
        "score": score,
    }


def _regime_headline(market_regime: str, tone_class: str, posture: str,
                     top_driver: Optional[str], stale: bool) -> str:
    """Generate the dashboard's market headline from data (never hardcoded).

    Combines the computed regime label with a tone-specific one-clause read and,
    when present, the single most important live driver. The mapping below is a
    controlled template — the *selection* is data-driven, not fixed text.
    """
    if stale:
        return ("Data Incomplete — several market inputs are missing or stale. "
                "Treat signals cautiously until fresh data is restored.")
    tone_clause = {
        "bull": "Trend and breadth lean supportive — favor leaders and confirm with volume.",
        "bear": "Volatility and weak breadth favor capital preservation over new risk.",
        "warn": "Risk signals are building — raise the quality bar and keep size light.",
        "neutral": "Index action is uneven and risk signals are not fully supportive — stay selective.",
    }.get(tone_class, "Signals are mixed — require confirmation before adding risk.")
    base = f"{market_regime}. {tone_clause}"
    if top_driver:
        base = f"{base} Key driver: {top_driver}."
    return base


def _build_market_regime_engine(
    market_key: str,
    bench_symbol: str,
    bench: Dict[str, Any],
    vix: Dict[str, Any],
    yield_proxy: Dict[str, Any],
    quotes: List[Dict[str, Any]],
    trend_data: Dict[str, Any],
    breadth_basket: List[str],
    participation_pct: Optional[float],
    news: Optional[Dict[str, Any]] = None,
    catalysts: Optional[List[Dict[str, Any]]] = None,
    source_status: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the dashboard's single source of truth regime object.

    Uses 8 factors with score buckets (+1 / 0 / -1 / -2) and emits dynamic
    posture, signal, color, confidence, risk warnings, and action guidance.
    """
    by_symbol = {str(q.get("symbol") or "").upper(): q for q in (quotes or [])}

    def _safe_num(value: Any) -> Optional[float]:
        try:
            f = float(value)
            return f if f == f else None
        except Exception:
            return None

    def _tone_from_score(score: int) -> str:
        if score >= 1:
            return "bull"
        if score <= -2:
            return "bear"
        if score == -1:
            return "warn"
        return "neutral"

    def _status_from_score(score: int) -> str:
        if score >= 1:
            return "Confirmed"
        if score == 0:
            return "Neutral"
        if score == -1:
            return "Warning"
        return "Critical"

    factors: List[Dict[str, Any]] = []
    missing_inputs: List[str] = []

    def _push_factor(key: str, label: str, score: int, note: str, value: Optional[float] = None) -> None:
        if score not in {1, 0, -1, -2}:
            score = 0
        factors.append({
            "key": key,
            "label": label,
            "score": int(score),
            "status": _status_from_score(int(score)),
            "tone": _tone_from_score(int(score)),
            "note": note,
            "value": value,
        })

    bench_chg = _safe_num(bench.get("change_pct"))
    bench_label = str(bench.get("label") or bench_symbol)
    bench_trend = trend_data.get(bench_symbol)
    vix_level = _safe_num(vix.get("price"))
    yield_chg = _safe_num(yield_proxy.get("change_pct"))
    yield_level = _safe_num(yield_proxy.get("price"))

    # Factor 1: Structural trend
    # A benchmark with under 200 sessions reports `above_sma200 = None`. That
    # is UNKNOWN, not "below" — coercing it with bool() would have scored a
    # young index as a confirmed downtrend, so the short-history case scores on
    # the 50-DMA alone and says why.
    if bench_trend and bench_trend.get("above_sma200") is None:
        _sessions = bench_trend.get("sma200_sessions")
        _required = bench_trend.get("sma200_sessions_required")
        _above50 = bool(bench_trend.get("above_sma50"))
        _push_factor(
            "trend", "Trend", 1 if _above50 else -1,
            f"{bench_label} 200-DMA unavailable ({_sessions}/{_required} sessions); "
            f"50-DMA read only",
            _safe_num(bench_trend.get("last")),
        )
        missing_inputs.append("trend_sma200")
    elif bench_trend:
        above200 = bool(bench_trend.get("above_sma200"))
        golden = bool(bench_trend.get("sma50_gt_sma200"))
        if above200 and golden:
            score = 1
            note = f"{bench_label} above 200-DMA with 50>200 trend alignment"
        elif (not above200) and (not golden):
            score = -2
            note = f"{bench_label} below 200-DMA with 50<200 trend pressure"
        else:
            score = -1 if not above200 else 0
            note = f"{bench_label} trend is transitional vs 50/200-DMA"
        _push_factor("trend", "Trend", score, note, _safe_num(bench_trend.get("last")))
    elif bench_chg is not None:
        if bench_chg >= 0.75:
            score = 1
        elif bench_chg <= -1.0:
            score = -2
        elif bench_chg < -0.25:
            score = -1
        else:
            score = 0
        _push_factor("trend", "Trend", score, f"{bench_label} {bench_chg:+.2f}% daily proxy (history unavailable)", bench_chg)
        missing_inputs.append("trend_history")
    else:
        _push_factor("trend", "Trend", 0, "Trend unavailable; defaulting to neutral")
        missing_inputs.append("trend")

    # Factor 2: Volatility
    if vix_level is not None:
        calm_cut = 16.0 if market_key == "US" else 13.0
        elevated_cut = 22.0 if market_key == "US" else 20.0
        panic_cut = 30.0 if market_key == "US" else 28.0
        if vix_level < calm_cut:
            score = 1
            note = f"Volatility is calm ({vix_level:.1f})"
        elif vix_level >= panic_cut:
            score = -2
            note = f"Volatility is stressed ({vix_level:.1f})"
        elif vix_level >= elevated_cut:
            score = -1
            note = f"Volatility is elevated ({vix_level:.1f})"
        else:
            score = 0
            note = f"Volatility is in a normal range ({vix_level:.1f})"
        _push_factor("volatility", "Volatility", score, note, vix_level)
    else:
        _push_factor("volatility", "Volatility", 0, "Volatility index unavailable")
        missing_inputs.append("volatility")

    # Factor 3: Participation breadth
    if participation_pct is not None:
        p = float(participation_pct)
        if p >= 75:
            score = 1
            note = f"Participation strong ({p:.0f}% indices above 50-DMA)"
        elif p < 35:
            score = -2
            note = f"Participation weak ({p:.0f}% indices above 50-DMA)"
        elif p < 50:
            score = -1
            note = f"Participation below neutral ({p:.0f}% indices above 50-DMA)"
        else:
            score = 0
            note = f"Participation mixed ({p:.0f}% indices above 50-DMA)"
        _push_factor("participation", "Participation", score, note, p)
    else:
        _push_factor("participation", "Participation", 0, "Participation unavailable")
        missing_inputs.append("participation")

    # Factor 4: Rates / credit proxy
    if yield_chg is not None:
        y_label = str(yield_proxy.get("label") or ("10Y Yield" if market_key == "US" else "USD/INR"))
        # A yield's move is quoted in basis points (+0.25% of a 5.24% yield is
        # about +1.3 bp); an FX proxy keeps its percent change.
        move = f"{yield_chg:+.2f}%"
        try:
            lvl = float(yield_level) if yield_level is not None else None
        except (TypeError, ValueError):
            lvl = None
        if "yield" in y_label.lower() and lvl and yield_chg > -100:
            move = f"{(lvl - lvl / (1 + yield_chg / 100.0)) * 100:+.1f} bp"
        if yield_chg <= _T.MARKET.YIELD_EASING_PCT:
            score = 1
            note = f"{y_label} easing ({move})"
        elif yield_chg >= _T.MARKET.YIELD_SHARP_PCT:
            score = -2
            note = f"{y_label} tightening sharply ({move})"
        elif yield_chg >= _T.MARKET.YIELD_RISING_PCT:
            score = -1
            note = f"{y_label} tightening ({move})"
        else:
            score = 0
            note = f"{y_label} stable ({move})"
        _push_factor("rates_credit", "Rates / Credit", score, note, yield_level)
    else:
        _push_factor("rates_credit", "Rates / Credit", 0, "Rates proxy unavailable")
        missing_inputs.append("rates_credit")

    # Factor 5: Benchmark index performance
    if bench_chg is not None:
        if bench_chg >= 0.7:
            score = 1
        elif bench_chg <= -1.2:
            score = -2
        elif bench_chg <= -0.35:
            score = -1
        else:
            score = 0
        # Deliberately tactical (today's move) vs Factor 1's structural 50/200-DMA
        # read — the label must say so, or the two "trend" factors look contradictory.
        _push_factor("index_performance", "Today's Index Move", score,
                     f"{bench_label} {bench_chg:+.2f}% today (tactical; Trend is the structural read)", bench_chg)
    else:
        _push_factor("index_performance", "Today's Index Move", 0, "Benchmark move unavailable")
        missing_inputs.append("index_performance")

    # Factor 6: Leadership concentration / sector balance proxy
    leadership_score = 0
    leadership_note = "Leadership mix unavailable"
    qqq = _safe_num((by_symbol.get("QQQ") or {}).get("change_pct"))
    iwm = _safe_num((by_symbol.get("IWM") or {}).get("change_pct"))
    dia = _safe_num((by_symbol.get("DIA") or {}).get("change_pct"))
    if market_key == "US" and any(v is not None for v in (qqq, iwm, dia, bench_chg)):
        # "Narrow leadership" requires OBSERVED weakness in cyclicals/small caps.
        # A missing IWM/DIA quote is unknown data, not evidence of narrowness.
        tech_only = (
            (qqq is not None and qqq > 0)
            and (iwm is not None and iwm <= 0)
            and (dia is not None and dia <= 0)
        )
        broad_lead = sum(1 for v in (qqq, iwm, dia) if v is not None and v > 0)
        if iwm is None or dia is None:
            missing_inputs.append("sector_leadership_partial")
        if tech_only:
            leadership_score = -1
            leadership_note = "Leadership is narrow (mega-cap tech leading alone)"
        elif broad_lead >= 2 and (iwm is not None and iwm > 0):
            leadership_score = 1
            leadership_note = "Leadership is broad (growth + cyclicals participating)"
        elif broad_lead == 0:
            leadership_score = -2
            leadership_note = "Leadership absent across major groups"
        else:
            leadership_score = 0
            leadership_note = "Leadership mixed across growth and cyclicals"
    elif market_key == "IN":
        bank = _safe_num((by_symbol.get("^NSEBANK") or {}).get("change_pct"))
        it = _safe_num((by_symbol.get("^CNXIT") or {}).get("change_pct"))
        nifty = _safe_num((by_symbol.get("^NSEI") or {}).get("change_pct"))
        if bank is not None and it is not None and nifty is not None:
            if bank > 0 and it > 0 and nifty > 0:
                leadership_score = 1
                leadership_note = "Leadership broad across Bank Nifty and IT"
            elif bank < 0 and it < 0:
                leadership_score = -2
                leadership_note = "Leadership weak across major India sectors"
            else:
                leadership_score = 0
                leadership_note = "Leadership mixed across Indian sectors"
        else:
            missing_inputs.append("sector_leadership")
    else:
        missing_inputs.append("sector_leadership")
    _push_factor("sector_leadership", "Sector Leadership", leadership_score, leadership_note)

    # Factor 7: Momentum
    mom = _safe_num((bench_trend or {}).get("mom_21d_pct"))
    if mom is not None:
        if mom >= 3.0:
            score = 1
        elif mom <= -6.0:
            score = -2
        elif mom <= -2.0:
            score = -1
        else:
            score = 0
        # Closes are dividend-adjusted (yfinance default), so this is total
        # return; say so, or it disagrees with a price-only chart after an
        # ex-dividend date.
        _push_factor("momentum", "Momentum", score, f"21-day momentum {mom:+.2f}% (total return)", mom)
    else:
        _push_factor("momentum", "Momentum", 0, "Momentum unavailable")
        missing_inputs.append("momentum")

    # Factor 8: Macro / catalyst stress proxy from cross-asset shock context
    stress_points = 0
    stress_reasons: List[str] = []
    oil_chg = _safe_num((by_symbol.get("USO") or {}).get("change_pct"))
    gold_chg = _safe_num((by_symbol.get("GLD") or {}).get("change_pct"))
    if vix_level is not None and vix_level >= (25.0 if market_key == "US" else 24.0):
        stress_points += 1
        stress_reasons.append("elevated volatility")
    if yield_chg is not None and abs(yield_chg) >= 0.8:
        stress_points += 1
        stress_reasons.append("rate shock")
    if oil_chg is not None and abs(oil_chg) >= 2.2:
        # A large move either way is a cross-asset shock; name its direction.
        # This read "oil spike" on a 3% fall while the ribbon said "falling".
        stress_points += 1
        stress_reasons.append(f"{'oil spike' if oil_chg > 0 else 'sharp oil drop'} {oil_chg:+.1f}%")
    if gold_chg is not None and gold_chg >= 1.2:
        stress_points += 1
        stress_reasons.append("defensive flow to gold")
    _macro_inputs = sum(1 for v in (vix_level, yield_chg, oil_chg, gold_chg) if v is not None)
    if _macro_inputs == 0:
        # No VIX, rates, oil or gold reading: stress is UNKNOWN, not "contained".
        score = 0
        note = "Cross-asset macro inputs unavailable — stress is unknown, not contained"
        missing_inputs.append("macro_risk")
    elif stress_points == 0 and _macro_inputs < 2:
        score = 0
        note = f"No macro stress flag on {_macro_inputs} of 4 cross-asset inputs (too few to call it contained)"
    elif stress_points == 0:
        score = 1
        note = "Cross-asset macro stress is contained"
    elif stress_points == 1:
        score = 0
        note = f"One macro stress flag active ({stress_reasons[0]})"
    elif stress_points == 2:
        score = -1
        note = f"Macro stress building ({', '.join(stress_reasons[:2])})"
    else:
        score = -2
        note = f"Macro stress high ({', '.join(stress_reasons[:3])})"
    _push_factor("macro_risk", "Macro / Catalyst Risk", score, note, float(stress_points))

    # Factor 9: Live news impact — does the current headline tape lean bull/bear?
    news_summary = _summarize_news_for_engine(news)
    nd = news_summary["netDirection"]
    _src_list = source_status or []
    _feeds_ok = sum(1 for s in _src_list if s.get("ok"))
    if _src_list and _feeds_ok == 0:
        # Every news feed failed: that is UNKNOWN news risk, not a quiet tape.
        _push_factor("news_impact", "News Impact", 0,
                     "No news feed is live (unreachable or stale) — headline risk is unknown, not absent")
        missing_inputs.append("news_feeds")
    elif news_summary["highImpactCount"] == 0:
        _push_factor("news_impact", "News Impact", 0, "No high-impact market headlines on the tape")
        missing_inputs.append("news_impact")
    elif nd == "bullish":
        # Asymmetry vs the bearish branch (-2 possible) is intentional: good news
        # never adds more than +1; uncertainty must never amplify risk-taking.
        _push_factor("news_impact", "News Impact", 1,
                     f"{news_summary['bullishHighImpact']} high-impact bullish headline(s) lead the tape")
    elif nd == "bearish":
        bear_n = news_summary["bearishHighImpact"]
        _push_factor("news_impact", "News Impact", -2 if bear_n >= 2 else -1,
                     f"{bear_n} high-impact bearish headline(s) lead the tape")
    else:
        _push_factor("news_impact", "News Impact", 0,
                     f"{news_summary['highImpactCount']} high-impact headlines are two-sided")

    # Factor 10: Catalyst risk — DATED events only (earnings in the next
    # seven days). This used to add up to four macro/politics *headlines* and
    # call them "macro/policy events": they were not scheduled, they were the
    # same stories Factor 9 already scores, so one bearish tape was counted
    # twice, and a busy tape always read "Heavy catalyst window". No macro
    # calendar is ingested, so the note says so rather than implying "none".
    catalyst_count = len(catalysts or [])
    plural = "s" if catalyst_count != 1 else ""
    if catalysts is None:
        # The screener behind the earnings list returned nothing: the week's
        # calendar is unknown, which is not the same as an empty calendar.
        _push_factor("catalyst_risk", "Catalyst Risk", 0,
                     "Earnings calendar unavailable: upcoming reports are unknown, not absent")
        missing_inputs.append("earnings_calendar")
    elif catalyst_count >= 4:
        _push_factor("catalyst_risk", "Catalyst Risk", -1,
                     f"Heavy earnings week ({catalyst_count} report{plural} in the next 7 days)", float(catalyst_count))
    elif catalyst_count > 0:
        _push_factor("catalyst_risk", "Catalyst Risk", 0,
                     f"{catalyst_count} earnings report{plural} in the next 7 days", float(catalyst_count))
    else:
        _push_factor("catalyst_risk", "Catalyst Risk", 0,
                     "No tracked earnings in the next 7 days (macro calendar not tracked)", 0.0)

    score_sum = sum(int(f.get("score") or 0) for f in factors)
    positives = sum(1 for f in factors if int(f.get("score") or 0) > 0)
    negatives = sum(1 for f in factors if int(f.get("score") or 0) < 0)
    conflicts = min(positives, negatives)

    posture_meta = {
        "Offensive": ("Constructive Market", "Constructive Expansion", "bull"),
        "Selective Offensive": ("Constructive — Selective", "Constructive but Selective", "bull"),
        "Lean Long": ("Mildly Constructive", "Moderately Constructive", "neutral"),
        "Neutral": ("Mixed Market", "Balanced / Mixed", "neutral"),
        "Lean Defensive": ("Cautious Market", "Risk Caution", "warn"),
        "Selective Defensive": ("Defensive — Selective", "Defensive Bias", "bear"),
        "Defensive": ("Defensive Market", "Capital Preservation", "bear"),
    }
    ladder = ["Offensive", "Selective Offensive", "Lean Long", "Neutral",
              "Lean Defensive", "Selective Defensive", "Defensive"]

    if score_sum >= 7:
        posture = "Offensive"
    elif score_sum >= 4:
        posture = "Selective Offensive"
    elif score_sum >= 2:
        posture = "Lean Long"
    elif score_sum <= -7:
        posture = "Defensive"
    elif score_sum <= -4:
        posture = "Selective Defensive"
    elif score_sum <= -2:
        posture = "Lean Defensive"
    else:
        posture = "Neutral"
    market_regime, model_signal, tone_class = posture_meta[posture]

    # [HARD OVERRIDES] Risk data can VETO a bullish score. Never let the page
    # print risk-on / offensive when the tape contradicts it. Each fired rule is
    # recorded for the debug panel and the transparency disclosure.
    overrides_triggered: List[str] = []

    def _cap_posture(max_name: str, reason: str) -> None:
        nonlocal posture, market_regime, model_signal, tone_class
        if ladder.index(posture) < ladder.index(max_name):
            posture = max_name
            market_regime, model_signal, tone_class = posture_meta[max_name]
            overrides_triggered.append(reason)

    vix_chg = _safe_num(vix.get("change_pct"))
    equity_changes = [
        _safe_num((by_symbol.get(s) or {}).get("change_pct")) for s in breadth_basket
    ]
    equity_changes = [c for c in equity_changes if c is not None]
    reds = sum(1 for c in equity_changes if c < 0)
    mostly_negative = bool(equity_changes) and reds > len(equity_changes) / 2.0
    trend_score = next((int(f.get("score") or 0) for f in factors if f.get("key") == "trend"), 0)
    part_score = next((int(f.get("score") or 0) for f in factors if f.get("key") == "participation"), 0)

    # Rule 0: VIX unavailable → volatility risk is UNVERIFIED, so risk-on is
    # blocked. Without this, a yfinance outage during a volatility spike would
    # silently disarm Rule 1 and let an offensive read through.
    if vix_level is None:
        _cap_posture("Neutral", "Volatility index unavailable — risk-on blocked until volatility can be verified")

    # Rule 1: VIX above the stress band → never offensive / risk-on.
    vix_stress = _T.MARKET.VIX_STRESS
    if vix_level is not None and vix_level > vix_stress:
        cap = "Lean Defensive" if vix_level >= _T.MARKET.VIX_EXTREME else "Neutral"
        _cap_posture(cap, f"VIX {vix_level:.1f} > {vix_stress:g} — risk-on/offensive blocked (capped at {cap})")

    # Rule 2: QQQ down >1% AND VIX rising → never risk-on.
    if qqq is not None and qqq < -1.0 and vix_chg is not None and vix_chg > 0:
        _cap_posture("Neutral", f"QQQ {qqq:+.2f}% with VIX rising ({vix_chg:+.2f}%) — risk-on blocked")

    # Rule 3: Major indices mostly negative → no constructive unless trend & breadth strong.
    if mostly_negative and not (trend_score > 0 and part_score > 0):
        _cap_posture("Neutral", f"{reds}/{len(equity_changes)} major indices red without strong trend+breadth")

    # Rule 4: Full offensive needs broad confirmation (>=5 confirmed factors).
    if posture == "Offensive" and positives < 5:
        _cap_posture("Selective Offensive", f"Only {positives} confirmed factors — full offensive requires >=5")

    # Rule 5: Multiple high-impact bearish headlines override a bullish price tape.
    if news_summary.get("bearishHighImpact", 0) >= 2 and tone_class == "bull":
        _cap_posture("Neutral", "Multiple high-impact bearish headlines override bullish price signal")

    # Detect price-vs-news conflict for confidence handling + disclosure.
    price_bull = score_sum >= 4
    price_bear = score_sum <= -4
    news_net = news_summary.get("netDirection", "neutral")
    news_conflict = (price_bull and news_net == "bearish") or (price_bear and news_net == "bullish")
    if news_conflict:
        overrides_triggered.append(f"Price tape and news direction conflict (price {'bullish' if price_bull else 'bearish'} vs news {news_net})")

    # Coverage is the share of factors that rest on a MEASURED input. Every
    # factor is always pushed (a missing input becomes a neutral placeholder), so
    # counting factor objects reported 100% with momentum, participation and
    # trend history absent. A placeholder earns no credit; a degraded proxy earns
    # half; a quiet tape or empty calendar with a live source is an observation.
    _missing_set = set(missing_inputs)
    _partial_inputs = {
        "trend": {"trend_sma200", "trend_history"},
        "sector_leadership": {"sector_leadership_partial"},
    }
    _macro_observed = any(v is not None for v in (
        vix_level, yield_chg,
        _safe_num((by_symbol.get("USO") or {}).get("change_pct")),
        _safe_num((by_symbol.get("GLD") or {}).get("change_pct")),
    ))
    _present = {f.get("key") for f in factors}
    _credits: Dict[str, float] = {}
    for _key in REGIME_FACTOR_ORDER:
        if _key not in _present:
            _credits[_key] = 0.0
        elif _key == "news_impact":
            _credits[_key] = 0.0 if "news_feeds" in _missing_set else 1.0
        elif _key == "macro_risk":
            _credits[_key] = 1.0 if _macro_observed else 0.0
        elif _key == "catalyst_risk":
            _credits[_key] = 1.0 if catalysts is not None else 0.0
        elif _key in _missing_set:
            _credits[_key] = 0.0
        elif _partial_inputs.get(_key, set()) & _missing_set:
            _credits[_key] = 0.5
        else:
            _credits[_key] = 1.0
    coverage = sum(_credits.values()) / len(REGIME_FACTOR_ORDER)
    unmeasured_factors = [k for k, v in _credits.items() if v < 1.0]
    # abs(score_sum) is deliberate: confidence measures signal STRENGTH either
    # direction — a -10 defensive read is as high-conviction as a +10 bullish one.
    confidence = int(round((abs(score_sum) / max(8.0, float(len(factors)))) * 60 + coverage * 30 - conflicts * 5))
    confidence = max(15, min(95, confidence))
    if news_summary.get("bearishHighImpact", 0) >= 1:
        confidence = max(12, confidence - 8)
    if news_conflict:
        confidence = max(12, confidence - 12)
    # News inputs lower coverage through the News Impact factor above and are
    # reported as missing, but they are not market data: a headline outage must
    # not force the stale-data defensive posture or the price-data penalty.
    market_missing = [m for m in missing_inputs if m not in NEWS_ONLY_INPUTS]
    if len(market_missing) >= 3:
        confidence = max(12, confidence - 15)

    freshness_state = "healthy"
    stale_quotes = any(q.get('stale') for q in (quotes or []))
    if stale_quotes:
        missing_inputs.append('fresh_quotes')
        market_missing.append('fresh_quotes')
        confidence = min(confidence, 35)
    if len(market_missing) >= 3 or stale_quotes:
        freshness_state = "degraded"
    elif conflicts >= 3 or news_conflict:
        freshness_state = "conflicted"

    # Rule 6: Stale / missing data → force a cautious "Data Incomplete" read LAST,
    # so it cannot be undone by an earlier bullish posture assignment.
    stale = freshness_state == "degraded"
    if stale:
        _cap_posture("Lean Defensive", "Stale/missing data — forcing cautious posture")
        market_regime = "Data Incomplete"
        model_signal = "Insufficient Data"
        tone_class = "warn"
        if "Stale/missing data — forcing cautious posture" not in overrides_triggered:
            overrides_triggered.append("Stale/missing data — Data Incomplete fallback")

    if posture in {"Offensive", "Selective Offensive"}:
        primary_action = {
            "primary": "Add risk only in confirmed leaders with volume support.",
            "sizing": "Normal size with staged adds.",
            "avoid": "Avoid extended entries and low-liquidity breakouts.",
            "risk": "Keep explicit invalidation and trailing stop discipline.",
        }
        portfolio_guidance = {
            "band": "65-95% gross",
            "headline": "Build exposure through confirmed setups",
            "headlineTone": "bull",
            "actions": [
                {"tone": "bull", "text": "Prioritize relative-strength leaders"},
                {"tone": "neutral", "text": "Scale in, avoid all-in entries"},
                {"tone": "warn", "text": "Cut quickly when setups lose momentum"},
            ],
        }
    elif posture == "Lean Long":
        primary_action = {
            "primary": "Lean long only when setup quality is clearly above average.",
            "sizing": "Moderate starter size.",
            "avoid": "Avoid chasing first impulsive moves.",
            "risk": "Require clean stop placement before entry.",
        }
        portfolio_guidance = {
            "band": "50-70% gross",
            "headline": "Selective longs with dry powder",
            "headlineTone": "warn",
            "actions": [
                {"tone": "neutral", "text": "Focus on best-in-class names only"},
                {"tone": "warn", "text": "Hold back size until confirmation broadens"},
                {"tone": "bear", "text": "Skip weak breadth and late-stage breakouts"},
            ],
        }
    elif posture == "Neutral":
        primary_action = {
            "primary": "Keep risk balanced and wait for clearer confirmation.",
            "sizing": "Measured size with staggered entries.",
            "avoid": "Avoid forcing directional bets from mixed tape.",
            "risk": "Tighten stop discipline and reduce churn.",
        }
        portfolio_guidance = {
            "band": "45-65% gross",
            "headline": "Balanced stance while signals are mixed",
            "headlineTone": "neutral",
            "actions": [
                {"tone": "neutral", "text": "Keep core positions but limit new adds"},
                {"tone": "warn", "text": "Prefer entries with multiple confirmations"},
                {"tone": "bear", "text": "Avoid broad beta until trend alignment improves"},
            ],
        }
    else:
        primary_action = {
            "primary": "Protect capital first; reduce risk and wait for quality resets.",
            "sizing": "Light exposure with higher cash buffer.",
            "avoid": "Avoid averaging down and avoid weak rebounds.",
            "risk": "Cut laggards early and keep hard stop discipline.",
        }
        portfolio_guidance = {
            "band": "20-50% gross",
            "headline": "Capital preservation takes priority",
            "headlineTone": "bear",
            "actions": [
                {"tone": "bear", "text": "Trim weak and crowded exposure"},
                {"tone": "warn", "text": "Keep only highest conviction core"},
                {"tone": "neutral", "text": "Re-engage only after breadth and volatility improve"},
            ],
        }

    if vix_level is not None and vix_level >= _T.MARKET.VIX_STRESS:
        primary_action["sizing"] = "Reduced size until volatility cools"
        portfolio_guidance["headlineTone"] = "bear"
    if freshness_state != "healthy":
        primary_action["primary"] = "Data quality is degraded; trade smaller and demand extra confirmation."
        primary_action["sizing"] = "Half-size risk while data confidence is limited"

    # [CONFIDENCE] Label + dynamic reason codes for WHY confidence is what it is,
    # so the hero can surface low confidence prominently (not buried in Source
    # Health). Threshold-driven, never hardcoded text.
    LOW_CONF = _T.MARKET.CONFIDENCE_MODERATE
    if confidence >= _T.MARKET.CONFIDENCE_HIGH:
        confidence_label = "High"
    elif confidence >= LOW_CONF:
        confidence_label = "Moderate"
    else:
        confidence_label = "Low"
    low_confidence = confidence < LOW_CONF

    confidence_reasons: List[str] = []
    if missing_inputs:
        confidence_reasons.append(f"{len(set(missing_inputs))} data input(s) missing or stale")
    if conflicts >= 2:
        confidence_reasons.append(f"{conflicts} factors disagree (index action is uneven)")
    if news_conflict:
        confidence_reasons.append("Price action and the news tape disagree")
    if news_summary.get("netDirection") == "mixed":
        confidence_reasons.append("News impact is mixed")
    if news_summary.get("bearishHighImpact", 0) >= 1:
        confidence_reasons.append(f"{news_summary['bearishHighImpact']} high-impact bearish headline(s)")
    catalyst_factor = next((f for f in factors if f.get("key") == "catalyst_risk"), None)
    if catalyst_factor and int(catalyst_factor.get("score") or 0) < 0:
        confidence_reasons.append("Catalyst risk is elevated")
    if abs(score_sum) <= 1:
        confidence_reasons.append("Factor scores are nearly balanced (no clear edge)")
    if not confidence_reasons and not low_confidence:
        confidence_reasons.append("Most factors agree and data coverage is healthy")

    # [LOW-CONF LABEL] When confidence is low or data is stale, the regime label
    # itself must communicate it — never show a clean bullish/neutral label that
    # hides the uncertainty.
    display_regime = market_regime
    if stale:
        display_regime = "Data Incomplete"
    elif low_confidence:
        if tone_class == "bull":
            display_regime = f"{market_regime} — Low Confidence"
            tone_class = "warn"  # de-green a low-confidence bullish read
        elif market_regime == "Mixed Market":
            display_regime = "Mixed / Cautious — Low Confidence"
        else:
            display_regime = f"{market_regime} — Low Confidence"

    # [GUIDANCE CONSISTENCY] Low confidence, degraded data, OR heavily conflicted
    # signals can never carry an aggressive add recommendation. Cap the exposure
    # band + tone downward.
    conflicted = freshness_state == "conflicted"
    if low_confidence or stale or conflicted:
        portfolio_guidance = {
            "band": "20-45% gross" if stale else "30-55% gross",
            "headline": (
                "Stay cautious while signals conflict"
                if conflicted and not (low_confidence or stale)
                else "Stay cautious while data confidence is low"
            ),
            "headlineTone": "warn",
            "actions": [
                {"tone": "warn", "text": "Reduce size until confidence improves"},
                {"tone": "neutral", "text": "Add only on clean, confirmed setups"},
                {"tone": "bear", "text": "Avoid oversized single-name bets"},
                {"tone": "neutral", "text": "Tighten stops if volatility rises"},
            ],
        }

    # [SHORT ACTION] A punchy, scannable directive that reflects regime +
    # confidence + volatility + news (the long primary_action stays for detail).
    hot_vix = vix_level is not None and vix_level >= _T.MARKET.VIX_STRESS
    if stale:
        action_brief = "Reduce risk until data confidence improves."
    elif hot_vix:
        action_brief = "Trade smaller. Wait for confirmation."
    elif tone_class == "bear":
        action_brief = "Protect capital. Avoid forcing trades."
    elif low_confidence:
        action_brief = "Trade smaller. Confirm before adding."
    elif tone_class == "bull":
        action_brief = "Buy only after clean confirmation."
    else:
        action_brief = "Stay selective. Wait for a clean setup."

    factor_sorted = sorted(factors, key=lambda f: int(f.get("score") or 0), reverse=True)
    strongest = [f for f in factor_sorted if int(f.get("score") or 0) > 0][:2]
    weakest = [f for f in sorted(factors, key=lambda f: int(f.get("score") or 0)) if int(f.get("score") or 0) < 0][:2]

    strengthen_txt = (
        f"Stronger if {', '.join(f.get('label', '') for f in weakest)} recover"
        if weakest else "Stronger if participation and momentum improve further"
    )
    weaken_txt = (
        f"Weaker if {', '.join(f.get('label', '') for f in strongest)} lose confirmation"
        if strongest else "Weaker if volatility and rates pressure increase"
    )

    risk_warnings = [f.get("note") for f in factors if int(f.get("score") or 0) <= -1 and f.get("note")]
    if freshness_state == "degraded":
        risk_warnings.append("Several regime inputs are missing; confidence is reduced.")
    if conflicts >= 3:
        risk_warnings.append("Signals are highly conflicted across factors; avoid aggressive sizing.")
    if news_summary.get("bearishHighImpact", 0) >= 1:
        risk_warnings.append(news_summary.get("summary"))
    if news_conflict:
        risk_warnings.append("Price action and the news tape disagree; confidence is reduced until they align.")

    short_summary = (
        f"{market_regime}: {positives}/{len(factors)} supportive factors, "
        f"{negatives}/{len(factors)} negative factors, score {score_sum:+d}."
    )
    detailed_reasoning = " | ".join(
        f"{f.get('label')}: {f.get('status')} ({int(f.get('score') or 0):+d}) — {f.get('note')}"
        for f in factors
    )

    # Sector-leadership digest (top-level convenience over the factor).
    lead_factor = next((f for f in factors if f.get("key") == "sector_leadership"), None)
    sector_leadership = {
        "label": (lead_factor or {}).get("status", "Unavailable"),
        "tone": (lead_factor or {}).get("tone", "neutral"),
        "note": (lead_factor or {}).get("note", "Leadership mix unavailable"),
    }

    # Source health from the live RSS status + market-quote availability.
    src = source_status or []
    sources_ok = sum(1 for s in src if s.get("ok"))
    sources_total = len(src)
    quotes_ok = sum(1 for q in (quotes or []) if q.get("price") is not None)
    quotes_total = len(quotes or [])
    # Market-data health is quotes only. Averaging headline feeds into it let a
    # news outage read as "critical" data while every price was live.
    health_ratio = (quotes_ok / quotes_total) if quotes_total else 1.0
    news_ratio = (sources_ok / sources_total) if sources_total else None
    source_health = {
        "newsSourcesOk": sources_ok,
        "newsSourcesTotal": sources_total,
        "newsPct": round(news_ratio * 100, 1) if news_ratio is not None else None,
        "quotesOk": quotes_ok,
        "quotesTotal": quotes_total,
        "healthPct": round(health_ratio * 100, 1),
        "state": "healthy" if health_ratio >= 0.8 else ("degraded" if health_ratio >= 0.5 else "critical"),
    }

    top_driver = (news_summary.get("drivers") or [{}])[0].get("title") if news_summary.get("drivers") else None
    market_headline = _regime_headline(display_regime, tone_class, posture, top_driver, stale)
    if stale and missing_inputs:
        # Name the gaps so "Data Incomplete" is diagnosable, not just a shrug.
        _missing_pretty = ", ".join(m.replace("_", " ") for m in sorted(set(missing_inputs))[:4])
        market_headline = f"{market_headline} Missing/stale: {_missing_pretty}."
    # When confidence is low, lead the headline with the reason so the user sees
    # it at the top (not buried in Source Health).
    if low_confidence and not stale and confidence_reasons:
        market_headline = (
            f"{display_regime}. Confidence {confidence}% ({confidence_label}) — "
            f"{confidence_reasons[0].lower()}. Trade smaller and confirm before adding."
        )

    return {
        "marketRegime": display_regime,
        "marketRegimeBase": market_regime,
        "marketHeadline": market_headline,
        "posture": posture,
        "modelSignal": model_signal,
        "confidenceScore": confidence,
        "confidenceLabel": confidence_label,
        "confidenceReasons": confidence_reasons[:5],
        "lowConfidence": low_confidence,
        "actionBrief": action_brief,
        "dataConfidence": int(round(coverage * 100)),
        "regimeScore": score_sum,
        "factorCounts": {
            "positive": positives,
            "negative": negatives,
            "neutral": len(factors) - positives - negatives,
            "total": len(factors),
        },
        "factors": factors,
        "primaryAction": primary_action,
        "portfolioGuidance": portfolio_guidance,
        "riskWarnings": [w for w in risk_warnings if w][:6],
        "outlookTriggers": {
            "strengthens": strengthen_txt,
            "weakens": weaken_txt,
        },
        "newsImpactSummary": news_summary.get("summary"),
        "newsImpact": {
            "netDirection": news_summary.get("netDirection"),
            "highImpactCount": news_summary.get("highImpactCount"),
            "bullishHighImpact": news_summary.get("bullishHighImpact"),
            "bearishHighImpact": news_summary.get("bearishHighImpact"),
        },
        "marketDrivers": news_summary.get("drivers", []),
        "catalystRisks": news_summary.get("catalysts", []),
        "sectorLeadership": sector_leadership,
        "sourceHealth": source_health,
        "overridesTriggered": overrides_triggered,
        "colorTheme": {
            "toneClass": tone_class,
            "palette": (
                "green" if tone_class == "bull" else
                "red" if tone_class == "bear" else
                "amber" if tone_class == "warn" else "slate"
            ),
        },
        "shortSummary": short_summary,
        "detailedReasoning": detailed_reasoning,
        "dataFreshness": {
            "state": freshness_state,
            "coveragePct": round(coverage * 100, 1),
            "unmeasuredFactors": unmeasured_factors,
            "missingInputs": sorted(set(missing_inputs)),
            "conflictCount": conflicts,
            "stale": freshness_state == "degraded",
            "newsConflict": news_conflict,
        },
        # [DATA -> UI] When market inputs are degraded the regime read is not a
        # measurement, so no exposure band, sizing rule or risk level may be
        # presented as guidance. The UI renders "withheld" instead.
        "guidanceWithheld": freshness_state == "degraded",
        "earningsCalendarKnown": catalysts is not None,
        "newsFeedsLive": not (_src_list and _feeds_ok == 0),
        "timeframe": "Swing-to-position horizon (days to a few weeks)",
        # [DEV DEBUG] Raw inputs + decision trace so the UI debug panel can show
        # exactly why a regime was selected (only surfaced in debug mode).
        "debug": {
            "scoreSum": score_sum,
            "factorScores": {f.get("key"): int(f.get("score") or 0) for f in factors},
            "positives": positives,
            "negatives": negatives,
            "conflicts": conflicts,
            "missingInputs": sorted(set(missing_inputs)),
            "overridesTriggered": overrides_triggered,
            "stale": stale,
            "newsNetDirection": news_summary.get("netDirection"),
            "newsHighImpact": news_summary.get("highImpactCount"),
            "rawInputs": {
                "benchChangePct": bench_chg,
                "vixLevel": vix_level,
                "vixChangePct": vix_chg,
                "yieldChangePct": yield_chg,
                "participationPct": participation_pct,
                "qqqChangePct": qqq,
                "indicesRed": reds,
                "indicesCounted": len(equity_changes),
            },
        },
    }


def _market_snapshot(market: str = "US", news: Optional[Dict[str, Any]] = None,
                     intel: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    universe = MARKET_UNIVERSES.get(market.upper(), MARKET_UNIVERSES["US"])
    instruments = universe["index_symbols"]
    quotes_by_symbol: Dict[str, Dict[str, Any]] = {}
    # Quotes and trend histories start together, each wave with its own budget
    # from MARKET config: a slow quote cannot starve the 50/200-DMA read, and a
    # dead provider still costs one budget, not two back to back.
    started = time.time()
    baskets = _T.MARKET.TREND_BASKETS
    trend_basket = baskets.get(market.upper(), baskets["US"])
    trend_futures = {_trend_future(symbol): symbol
                     for symbol in dict.fromkeys([trend_basket["bench"], *trend_basket["breadth"]])}
    if instruments:
        futures = {_snapshot_executor.submit(_quote_snapshot, symbol, label): symbol for symbol, label in instruments}
        for future in _completed_snapshot_jobs(futures, float(_T.MARKET.QUOTE_BUDGET_SECONDS)):
            symbol = futures[future]
            try:
                quotes_by_symbol[symbol] = future.result()
            except Exception as exc:
                logger.debug("Quote worker failed for %s: %s", symbol, exc)
                label = next((name for ticker, name in instruments if ticker == symbol), symbol)
                quotes_by_symbol[symbol] = {"symbol": symbol, "label": label, "price": None, "change_pct": None}

    with _tape_quote_lock:
        for symbol, label in instruments:
            cached_quote = _tape_quote_cache.get(symbol)
            if symbol not in quotes_by_symbol and cached_quote:
                quotes_by_symbol[symbol] = {**cached_quote['payload'], 'label': label, 'stale': True}
    quotes = [quotes_by_symbol.get(symbol, {"symbol": symbol, "label": label, "price": None, "change_pct": None}) for symbol, label in instruments]

    by_symbol = {item["symbol"]: item for item in quotes}
    # Market-specific tone signals
    if market.upper() == "IN":
        vix = by_symbol.get("^INDIAVIX", {})
        yield_proxy = by_symbol.get("INR=X", {})
    else:
        vix = by_symbol.get("^VIX", {})
        yield_proxy = by_symbol.get("^TNX", {})
    bench_symbol = trend_basket["bench"]
    breadth_basket = list(trend_basket["breadth"])
    bench = by_symbol.get(bench_symbol, {})

    # [REGIME-V3] Structural trend + participation breadth from 50/200-DMA.
    # Fetched in parallel; degrades gracefully to the daily-change heuristic
    # when history is unavailable (e.g. provider cooldown).
    trend_data: Dict[str, Any] = {}
    # No live quote at all means the provider is not answering: take whatever
    # histories are already in hand (cache) instead of waiting out their budget.
    provider_answering = any(q.get("price") is not None and not q.get("stale") for q in quotes)
    trend_wait = (max(0.0, started + float(_T.MARKET.TREND_BUDGET_SECONDS) - time.time())
                  if provider_answering else 0.0)
    try:
        done_trends = list(as_completed(trend_futures, timeout=trend_wait))
    except FuturesTimeout:
        done_trends = [f for f in trend_futures if f.done()]
        logger.warning("Market trend read exceeded its %ss budget (%d of %d histories)",
                       _T.MARKET.TREND_BUDGET_SECONDS, len(done_trends), len(trend_futures))
    for future in done_trends:
        try:
            tm = future.result()
        except Exception:
            continue
        if tm:
            trend_data[tm["symbol"]] = tm
    bench_trend = trend_data.get(bench_symbol)
    basket_metrics = [trend_data[s] for s in breadth_basket if s in trend_data]
    participation = _index_participation(basket_metrics, breadth_basket)
    participation_pct = participation["pct"]

    tone = "Neutral"
    tone_detail = "Market tape is mixed; size positions with confirmation."
    tone_factors: List[Dict[str, Any]] = []
    tone_version = "v1"

    # [TIER-1] Honesty: replace the 2-rule heuristic with a transparent
    # 4-factor regime model. Each factor scores -1 / 0 / +1 and the sum
    # determines Risk-on / Neutral / Risk-off. The v1 heuristic is kept
    # behind ?regime=v1 as an opt-in fallback for one session.
    use_v1 = False
    try:
        use_v1 = (request.args.get("regime") == "v1")  # type: ignore[name-defined]
    except Exception:
        use_v1 = False

    if use_v1:
        tone_version = "v1"
        if bench.get("change_pct") is not None and vix.get("price") is not None:
            if bench["change_pct"] > _T.MARKET.INDEX_TONE_PCT and vix["price"] < _T.MARKET.VIX_CALM_MAX:
                tone = "Risk-on"
                tone_detail = "Equities are firm and volatility is contained."
            elif bench["change_pct"] < -_T.MARKET.INDEX_TONE_PCT or vix["price"] >= _T.MARKET.VIX_STRESS:
                tone = "Risk-off"
                tone_detail = "Volatility or equity weakness argues for tighter risk."
    else:
        tone_version = "v2"
        # Factor 1: Trend — STRUCTURAL (price vs 50/200-DMA), not a single day.
        bench_label = bench.get("label") or "Benchmark"
        if bench_trend and bench_trend.get("above_sma200") is None:
            # Under 200 sessions: unknown, not bearish. Score the 50-DMA only.
            trend_score = 1 if bench_trend.get("above_sma50") else -1
            trend_note = (
                f"{bench_label} 200-DMA unavailable "
                f"({bench_trend.get('sma200_sessions')}/"
                f"{bench_trend.get('sma200_sessions_required')} sessions); 50-DMA only"
            )
            tone_factors.append({"key": "trend", "label": "Trend",
                                 "score": trend_score, "note": trend_note})
        elif bench_trend:
            above200 = bench_trend["above_sma200"]
            golden = bench_trend["sma50_gt_sma200"]
            if above200 and golden:
                trend_score = 1
                trend_note = f"{bench_label} above 200-DMA, 50>200 (uptrend)"
            elif (not above200) and (not golden):
                trend_score = -1
                trend_note = f"{bench_label} below 200-DMA, 50<200 (downtrend)"
            else:
                trend_score = 0
                trend_note = f"{bench_label} mixed vs MAs (transition)"
            tone_factors.append({"key": "trend", "label": "Trend", "score": trend_score, "note": trend_note})
        else:
            # Fallback: daily change, explicitly labelled as a single-session read.
            bench_chg = bench.get("change_pct")
            if bench_chg is not None:
                if bench_chg >= 0.5:
                    trend_score, trend_note = 1, f"{bench_label} +{bench_chg:.2f}% today"
                elif bench_chg <= -0.5:
                    trend_score, trend_note = -1, f"{bench_label} {bench_chg:.2f}% today"
                else:
                    trend_score, trend_note = 0, f"{bench_label} {bench_chg:+.2f}% today (flat)"
                tone_factors.append({"key": "trend", "label": "Trend", "score": trend_score, "note": trend_note})

        # Factor 2: Volatility (VIX level)
        vix_level = vix.get("price")
        if vix_level is not None:
            if vix_level < _T.MARKET.VIX_CONTAINED_MAX:
                vol_score, vol_note = 1, f"VIX {vix_level:.1f} (calm)"
            elif vix_level >= _T.MARKET.VIX_STRESS:
                vol_score, vol_note = -1, f"VIX {vix_level:.1f} (elevated)"
            else:
                vol_score, vol_note = 0, f"VIX {vix_level:.1f} (normal)"
            tone_factors.append({"key": "vol", "label": "Volatility", "score": vol_score, "note": vol_note})

        # Factor 3: Participation — % of equity indices above their own 50-DMA
        # (a real breadth proxy, NOT "% of cross-asset quotes green today").
        if basket_metrics:
            above = sum(1 for m in basket_metrics if m["above_sma50"])
            total = len(basket_metrics)
            pct = above / total * 100
            if pct >= 75:
                breadth_score = 1
            elif pct <= 25:
                breadth_score = -1
            else:
                breadth_score = 0
            breadth_note = f"{above}/{total} indices above 50-DMA ({pct:.0f}%)"
            tone_factors.append({"key": "breadth", "label": "Participation", "score": breadth_score, "note": breadth_note})
        else:
            # Fallback: same-day green count, honestly labelled (not "breadth").
            usable = [q for q in quotes if q.get("change_pct") is not None and q.get("symbol") not in {"^VIX", "^INDIAVIX"}]
            if usable:
                up_count = sum(1 for q in usable if q["change_pct"] > 0)
                breadth_pct = (up_count / len(usable)) * 100
                if breadth_pct >= 65:
                    breadth_score = 1
                elif breadth_pct <= 35:
                    breadth_score = -1
                else:
                    breadth_score = 0
                breadth_note = f"{up_count}/{len(usable)} markets up today"
                tone_factors.append({"key": "breadth", "label": "Participation", "score": breadth_score, "note": breadth_note})

        # Factor 4: Credit / Yield direction (10Y or INR proxy)
        yield_chg = yield_proxy.get("change_pct")
        yield_label = yield_proxy.get("label") or ("10Y Yield" if market.upper() == "US" else "USD/INR")
        if yield_chg is not None:
            # Falling yields = risk-on for equities; rising yields = risk-off
            if yield_chg <= -0.5:
                yield_score, yield_note = 1, f"{yield_label} {yield_chg:.2f}% (easing)"
            elif yield_chg >= 0.5:
                yield_score, yield_note = -1, f"{yield_label} +{yield_chg:.2f}% (tightening)"
            else:
                yield_score, yield_note = 0, f"{yield_label} {yield_chg:+.2f}%"
            tone_factors.append({"key": "credit", "label": "Credit/Rates", "score": yield_score, "note": yield_note})

        # Aggregate
        if tone_factors:
            net = sum(f["score"] for f in tone_factors)
            if net >= 2:
                tone = "Risk-on"
                tone_detail = f"{net}/{len(tone_factors)} factors positive — equities favored, conviction sizing OK."
            elif net <= -2:
                tone = "Risk-off"
                tone_detail = f"{abs(net)}/{len(tone_factors)} factors negative — reduce sizing, tighten stops."
            else:
                tone = "Neutral"
                tone_detail = f"Mixed signals ({net:+d}/{len(tone_factors)}) — wait for confirmation before adding risk."

    regime_engine = _build_market_regime_engine(
        market_key=market.upper(),
        bench_symbol=bench_symbol,
        bench=bench,
        vix=vix,
        yield_proxy=yield_proxy,
        quotes=quotes,
        trend_data=trend_data,
        breadth_basket=breadth_basket,
        participation_pct=participation_pct,
        news=news,
        # None (unknown) unless the intel screen actually covered a universe:
        # a warming-up or failed screen carries an empty list that is not a
        # measured "no earnings this week".
        catalysts=(_earnings_ahead(intel.get("earnings_calendar") or intel.get("earnings_week"))
                   if isinstance(intel, dict) and not intel.get("loading")
                   and ((intel.get("earnings_week") or [])
                        or int(intel.get("universe_size") or 0) > 0) else None),
        source_status=(news or {}).get("source_status") if isinstance(news, dict) else None,
    )

    return {
        "market": market.upper(),
        "label": universe["label"],
        "currency": universe["currency"],
        "quotes": quotes,
        "tone": tone,
        "tone_detail": tone_detail,
        "tone_factors": tone_factors,
        "tone_version": tone_version,
        # [HONESTY] Disclose what the read is and is NOT, so the UI never
        # presents a daily cross-asset heuristic as a structural macro call.
        "tone_timeframe": "Swing-to-position horizon (days to a few weeks)",
        "tone_basis": (
            "Rules-based read of trend (50/200-DMA), volatility (VIX), "
            "participation (% of indices above 50-DMA) and rates (10Y). "
            "Not a backtested signal and not a long-cycle macro regime call."
        ),
        # Canonical dashboard regime model (single source of truth for UI state).
        "market_regime_engine": regime_engine,
        # Convenience mirrors so thin clients can bind without re-deriving labels.
        "market_regime": regime_engine.get("marketRegime"),
        "posture": regime_engine.get("posture"),
        "model_signal": regime_engine.get("modelSignal"),
        "confidence_score": regime_engine.get("confidenceScore"),
        "participation_pct": participation_pct,
        # Coverage-aware detail so the UI can name what was actually measured
        # (a handful of index proxies) instead of implying exchange breadth.
        "participation": participation,
        "trend_structural": bool(bench_trend),
        "data_source": "Public market data (Yahoo Finance) · news via public RSS",
        "vix_level": vix.get("price"),
        "ten_year_yield": yield_proxy.get("price"),
        # [WHAT-CHANGED] honest day-over-day deltas (empty + baseline=True on day 1)
        **_regime_change_block(
            market.upper(), tone, tone_factors, vix.get("price"),
            participation_pct, yield_proxy.get("price"), engine=regime_engine,
        ),
    }


def _ticker_intel(symbol: str, with_info: bool = True) -> Dict[str, Any]:
    """Fetch a single ticker's price/change/analyst/earnings data.

    Uses yfinance.fast_info for price (~10x faster than .history) and only falls back
    to .info when with_info=True (which is needed for analyst & earnings cards).
    """
    out: Dict[str, Any] = {"symbol": symbol}
    try:
        import yfinance as yf_local
        tk = yf_local.Ticker(symbol)

        # Fast path: fast_info gives last + previous close in a single call
        try:
            fi = tk.fast_info
            last = _fast_info_get(fi, "last_price")
            prev = _fast_info_get(fi, "previous_close")
            if last and prev:
                out["price"] = round(last, 2)
                out["change_pct"] = round(((last - prev) / prev) * 100, 2)
                out["change_abs"] = round(last - prev, 2)
            # The same fast_info payload already carries the session OHLC and the
            # 52-week band, so the movers quote line costs no extra request.
            for src_key, dst_key in (
                ("open", "open"),
                ("day_high", "day_high"),
                ("day_low", "day_low"),
                ("previous_close", "prev_close"),
                ("year_high", "week52_high"),
                ("year_low", "week52_low"),
            ):
                val = _opt_round(_fast_info_get(fi, src_key))
                if val is not None:
                    out[dst_key] = val
        except Exception:
            pass

        # Fallback to history when fast_info gave us no price OR no session
        # band. Markets without a screener feed (India) rely on this path for
        # the high/low the quote board prints, so a price-only fast_info read
        # must still be topped up rather than accepted as complete.
        if "price" not in out or out.get("day_high") is None or out.get("day_low") is None:
            hist = tk.history(period="5d", interval="1d")
            if hist is not None and not hist.empty:
                close = hist["Close"].dropna()
                if not close.empty:
                    last = float(close.iloc[-1])
                    prev = float(close.iloc[-2]) if len(close) > 1 else last
                    if "price" not in out:
                        out["price"] = round(last, 2)
                        out["change_pct"] = round(((last - prev) / prev) * 100, 2) if prev else 0.0
                        out["change_abs"] = round(last - prev, 2)
                    out.setdefault("prev_close", round(prev, 2))
                    last_bar = hist.iloc[-1]
                    for col, dst_key in (("Open", "open"), ("High", "day_high"), ("Low", "day_low")):
                        val = _opt_round(last_bar.get(col))
                        if val is not None:
                            out.setdefault(dst_key, val)

        if not with_info:
            out["name"] = symbol
            return out

        try:
            info = tk.info or {}
        except Exception:
            info = {}
        out["name"] = info.get("shortName") or info.get("longName") or symbol
        out["sector"] = info.get("sector") or ""
        # Listing venue, so the UI can build an exact TradingView symbol for
        # rows that came from the universe scan rather than the screener.
        out["exchange"] = info.get("exchange") or info.get("fullExchangeName") or None
        out["recommendation"] = info.get("recommendationKey") or info.get("recommendationMean")
        out["target_mean"] = info.get("targetMeanPrice")
        out["analyst_count"] = info.get("numberOfAnalystOpinions")
        out["market_cap"] = info.get("marketCap")
        if out.get("price") and out.get("target_mean"):
            try:
                out["upside_pct"] = round(((float(out["target_mean"]) - out["price"]) / out["price"]) * 100, 2)
            except Exception:
                out["upside_pct"] = None
        # Earnings date
        try:
            cal = tk.calendar
            edate = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if isinstance(ed, list) and ed:
                    edate = ed[0]
                elif ed:
                    edate = ed
            elif cal is not None and hasattr(cal, "columns"):
                try:
                    edate = cal.loc["Earnings Date"].iloc[0]
                except Exception:
                    edate = None
            if edate is not None:
                if hasattr(edate, "isoformat"):
                    out["earnings_date"] = edate.isoformat()
                else:
                    out["earnings_date"] = str(edate)
        except Exception:
            pass
    except Exception as exc:
        logger.debug("ticker intel failed for %s: %s", symbol, exc)
    return out


_market_intel_inflight: Dict[str, threading.Event] = {}
_market_intel_lock = threading.Lock()
_market_intel_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix='market-intel')
MARKET_INTEL_FETCH_SECONDS = 25
MARKET_INTEL_RETRY_SECONDS = 15


# ============================================================================
# Market Intel: screener-first + broad-universe (B + C revamp)
# ============================================================================
# Why this exists:
#   The previous _market_intel scanned only 30 hard-coded mega caps for
#   "Top Movers", which silently hid genuine market movers (e.g. a +25%
#   small/mid-cap rip on earnings). This module now:
#     (1) Pulls the actual market gainers/losers via yfinance's predefined
#         screener `day_gainers` / `day_losers` (ground truth — same data
#         Yahoo Finance shows on its public movers page).
#     (2) Falls back to a broader ~150-symbol universe when the screener is
#         unavailable (e.g. for non-US markets, or yfinance API hiccups).
#     (3) Always surfaces extreme moves (|change| >= EXTREME_MOVE_PCT) even
#         when not in the top-N by rank.
#     (4) Exposes scope metadata (`universe_source`, `as_of`, `extreme_count`)
#         so the UI can be honest about what it scanned.
# ============================================================================

EXTREME_MOVE_PCT = 15.0  # halo threshold — never hide a >=15% move
MOVERS_DISPLAY_COUNT = 8  # rows to display per up/down list
SCREENER_FETCH_COUNT = 25  # quotes to pull from yf.screen per side


# Broader large/mid-cap US universe used for analyst & earnings buckets.
# Curated, not algorithmic, to keep the file deterministic. Roughly tracks
# the top ~120 names of the S&P 500 by market cap with sector breadth.
_US_BROAD_EXTRA = [
    # Tech / Comm
    "AAPL","MSFT","GOOGL","GOOG","AMZN","META","NVDA","TSLA","AVGO","ORCL",
    "ADBE","CRM","CSCO","ACN","IBM","INTC","AMD","QCOM","TXN","NOW","INTU",
    "AMAT","MU","ADI","LRCX","KLAC","PANW","SNPS","CDNS","ANET","FTNT",
    "PLTR","SHOP","UBER","ABNB","NFLX","DIS","CMCSA","T","VZ","TMUS",
    # Financials
    "BRK-B","JPM","V","MA","BAC","WFC","GS","MS","BLK","SCHW",
    "C","AXP","SPGI","ICE","CME","PGR","CB","MMC","AON","TRV",
    # Healthcare
    "UNH","LLY","JNJ","ABBV","MRK","PFE","TMO","ABT","DHR","BMY",
    "AMGN","CVS","GILD","ELV","ISRG","SYK","BSX","REGN","VRTX","MDT",
    # Consumer
    "WMT","COST","HD","PG","KO","PEP","MCD","NKE","SBUX","TGT",
    "LOW","TJX","BKNG","CL","MDLZ","MO","PM","KMB","GIS","KHC",
    # Energy / Industrials / Materials / Utilities / Real Estate
    "XOM","CVX","COP","SLB","EOG","MPC","PSX","OXY","WMB","KMI",
    "CAT","BA","HON","UPS","RTX","GE","DE","LMT","ETN","MMM",
    "LIN","SHW","FCX","NEM","NUE","NEE","DUK","SO","AEP","D",
    "PLD","AMT","EQIX","PSA","SPG","O","CCI","WELL","DLR","SBAC",
]
# Light de-dup, preserving order
_US_BROAD_UNIVERSE: List[str] = list(dict.fromkeys(_US_BROAD_EXTRA))


# Broader India universe for analyst/earnings buckets (Nifty 50 + select 50).
_IN_BROAD_EXTRA = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS","HINDUNILVR.NS",
    "BHARTIARTL.NS","ITC.NS","SBIN.NS","LT.NS","KOTAKBANK.NS","AXISBANK.NS",
    "BAJFINANCE.NS","ASIANPAINT.NS","MARUTI.NS","HCLTECH.NS","WIPRO.NS",
    "SUNPHARMA.NS","TITAN.NS","NESTLEIND.NS","ULTRACEMCO.NS","M&M.NS","NTPC.NS",
    "POWERGRID.NS","TATAMOTORS.NS","ADANIENT.NS","COALINDIA.NS","JSWSTEEL.NS",
    "TATASTEEL.NS","ONGC.NS","BAJAJFINSV.NS","HDFCLIFE.NS","SBILIFE.NS",
    "DRREDDY.NS","CIPLA.NS","DIVISLAB.NS","APOLLOHOSP.NS","BRITANNIA.NS",
    "HEROMOTOCO.NS","EICHERMOT.NS","BPCL.NS","IOC.NS","GRASIM.NS",
    "INDUSINDBK.NS","TECHM.NS","HINDALCO.NS","ADANIPORTS.NS","UPL.NS",
    "TATACONSUM.NS","SHRIRAMFIN.NS","LTIM.NS",
]
_IN_BROAD_UNIVERSE: List[str] = list(dict.fromkeys(_IN_BROAD_EXTRA))


def _broad_universe(market: str) -> List[str]:
    return _US_BROAD_UNIVERSE if market.upper() == "US" else _IN_BROAD_UNIVERSE


def _opt_round(value: Any, digits: int = 2) -> Optional[float]:
    """Round a possibly-missing numeric to `digits`, or return None.

    Provider payloads mix None, "", NaN and strings into the same field, so a
    single tolerant coercion keeps every quote-shaped dict honest: a field is
    either a real number or absent, never the string "nan" or a stray 0.
    """
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return round(num, digits)


def _snake_to_camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _fast_info_get(fi: Any, key: str) -> Optional[float]:
    """Read one field from a yfinance `FastInfo`, whatever case it exposes.

    `FastInfo.get()` validates the key against `keys()`, and `keys()` returns
    camelCase only ("dayHigh", "previousClose"). A snake_case read therefore
    does not raise -- it silently returns the default. That silence is what
    left every India mover with N/A high/low: the US rows come from the Yahoo
    screener (which supplies its own OHLC), while India falls through to this
    call. Try both spellings and coerce to a finite float or None.
    """
    for candidate in (key, _snake_to_camel(key)):
        try:
            val = fi[candidate]
        except (KeyError, TypeError, AttributeError, IndexError):
            continue
        except Exception:
            continue
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if math.isfinite(num) and num != 0:
            return num
    return None


def _normalize_screener_quote(q: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a yfinance screener `quotes` entry into our row shape.

    The session OHLC block (open/day high/day low/previous close) rides along
    for free in the same screener response, so the movers card can render a
    full exchange-style quote line without a second round trip per symbol.
    """
    try:
        symbol = q.get("symbol")
        if not symbol:
            return None
        price = q.get("regularMarketPrice")
        change_pct = q.get("regularMarketChangePercent")
        if price is None or change_pct is None:
            return None
        return {
            "symbol": str(symbol),
            "name": q.get("shortName") or q.get("longName") or str(symbol),
            "price": round(float(price), 2),
            "change_pct": round(float(change_pct), 2),
            "market_cap": q.get("marketCap"),
            "volume": q.get("regularMarketVolume"),
            # Session OHLC — same fields the exchange quote board shows.
            "open": _opt_round(q.get("regularMarketOpen")),
            "day_high": _opt_round(q.get("regularMarketDayHigh")),
            "day_low": _opt_round(q.get("regularMarketDayLow")),
            "prev_close": _opt_round(q.get("regularMarketPreviousClose")),
            "change_abs": _opt_round(q.get("regularMarketChange")),
            "week52_high": _opt_round(q.get("fiftyTwoWeekHigh")),
            "week52_low": _opt_round(q.get("fiftyTwoWeekLow")),
            # Listing venue — lets the UI build an exact TradingView symbol
            # (NASDAQ:AAPL) instead of guessing. Absent is fine; the UI falls
            # back to the bare ticker, which TradingView resolves itself.
            "exchange": q.get("exchange") or q.get("fullExchangeName"),
            "source": "yf_screener",
        }
    except Exception:
        return None


def _screener_movers(side: str, count: int = SCREENER_FETCH_COUNT) -> List[Dict[str, Any]]:
    """Fetch real market movers via yfinance predefined screener.

    `side` is one of "day_gainers" / "day_losers". Returns a list of normalized
    rows. Returns [] on any failure (caller falls back to universe scan).
    """
    try:
        import yfinance as yf_local
        resp = yf_local.screen(side, count=count)
    except Exception as exc:
        logger.warning("yf.screen('%s') failed: %s", side, exc)
        return []
    if not isinstance(resp, dict):
        return []
    quotes = resp.get("quotes") or []
    rows: List[Dict[str, Any]] = []
    for q in quotes:
        n = _normalize_screener_quote(q)
        if n:
            rows.append(n)
    return rows


def _open_extreme_pool(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Count unique quotes and exclude unusable OHLC without inventing signals."""
    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        previous = unique.get(symbol)
        if previous is None or any(_opt_round(previous.get(k)) is None for k in ("open", "day_high", "day_low")):
            unique[symbol] = row
    counts = {"received": len(unique), "comparable": 0, "missing_ohlc": 0, "invalid_ohlc": 0, "flat": 0}
    valid = []
    for row in unique.values():
        open_, high, low = (_opt_round(row.get(k)) for k in ("open", "day_high", "day_low"))
        if any(v is None for v in (open_, high, low)):
            counts["missing_ohlc"] += 1
        elif low <= 0 or not low <= open_ <= high:
            counts["invalid_ohlc"] += 1
        elif open_ == high == low:
            counts["flat"] += 1
        else:
            valid.append(row)
            counts["comparable"] += 1
    return valid, counts


def _select_open_extremes(rows: List[Dict[str, Any]],
                          limit: Optional[int] = None
                          ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split quote rows into (opened at the low, opened at the high).

    A stock whose open equals its session low never traded below its first
    print. One whose open equals its high never traded above it. Neither
    condition implies that all buyers are profitable. Both use session OHLC the
    movers feed already carries, so the boards cost no extra provider call.

    Matching is exact on the same rounded figures the table displays, so every
    listed row visibly shows two identical numbers -- no tolerance the reader
    cannot see. Three rows are refused:

      * any row missing open, high or low (two absent fields must never
        compare equal to each other);
      * open == high == low, which carries no directional session range;
      * duplicates, since the screener and the universe scan overlap.

    Returns all matches, strongest absolute move first; only explicit callers
    requesting a limit truncate the result. Single-price rows are excluded
    without inferring a halt or a cause from OHLC alone.
    """
    at_low: List[Dict[str, Any]] = []
    at_high: List[Dict[str, Any]] = []
    valid, _ = _open_extreme_pool(rows)
    for row in valid:
        open_ = _opt_round(row.get("open"))
        high = _opt_round(row.get("day_high"))
        low = _opt_round(row.get("day_low"))
        if open_ == low:
            at_low.append(row)
        elif open_ == high:
            at_high.append(row)

    def _rank(bucket: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ranked = sorted(
            bucket,
            key=lambda r: abs(_opt_round(r.get("change_pct")) or 0.0),
            reverse=True,
        )
        return ranked if limit is None else ranked[:limit]

    return _rank(at_low), _rank(at_high)


def _market_intel(market: str = "US", *, blocking: bool = True) -> Dict[str, Any]:
    """Build movers, analyst picks, and earnings buckets for a market.

    When blocking=False and no fresh cache exists, schedules a background warmup
    and returns a minimal `{loading: True}` payload immediately.

    Data sources (in order of preference):
      - Movers (US):    yfinance predefined screener (`day_gainers`/`day_losers`)
      - Movers (other): broad universe scan, sorted by change_pct
      - Analyst/Earnings: broad ~150-symbol universe scan (needs .info fields)
    """
    key = _normalize_market(market)
    now = time.time()
    with _market_intel_cache_lock:
        cached = _market_intel_cache.get(key)
    ttl = MARKET_INTEL_RETRY_SECONDS if cached and cached['payload'].get('degraded') else MARKET_INTEL_CACHE_SECONDS
    if cached and (now - cached["timestamp"]) < ttl:
        return cached["payload"]

    with _market_intel_lock:
        # A worker may have finished between the initial cache read and this
        # lock. Recheck before starting another scan for the same generation.
        with _market_intel_cache_lock:
            latest = _market_intel_cache.get(key)
        if latest and latest is not cached:
            return latest['payload']
        event = _market_intel_inflight.get(key)
        if event is None:
            event = threading.Event()
            _market_intel_inflight[key] = event
            def _warm():
                try:
                    _build_market_intel(key)
                except Exception as exc:
                    logger.warning("intel warmup failed for %s: %s", key, exc)
                finally:
                    with _market_intel_lock:
                        _market_intel_inflight.pop(key, None)
                        event.set()
            threading.Thread(target=_warm, daemon=True).start()
    if blocking:
        event.wait(MARKET_INTEL_FETCH_SECONDS + 1)
        with _market_intel_cache_lock:
            latest = _market_intel_cache.get(key)
        if latest and latest is not cached:
            return latest['payload']
    if cached:
        return {**cached['payload'], 'stale': True, 'refreshing': not event.is_set()}
    return {
            "market": key,
            "currency": MARKET_UNIVERSES.get(key, MARKET_UNIVERSES["US"])["currency"],
            "loading": True,
            "movers_up": [],
            "movers_down": [],
            "open_at_low": [],
            "open_at_high": [],
            "open_extremes_scanned": 0,
            "analyst_picks": [],
            "earnings_week": [],
            "catalyst_window_label": CATALYST_WINDOW_LABEL,
            "universe_size": 0,
            "universe_source": "warming up",
            "as_of": None,
            "extreme_count": 0,
        }

# Yahoo's own earnings calendar (US). The intel screen only knows the report
# dates of the names it happened to cover (a partial screen found none in a
# heavy bank-earnings week), so the dated list is completed from the calendar.
_earnings_cal_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_earnings_cal_lock = threading.Lock()


def _earnings_calendar(market_key: str) -> List[Dict[str, Any]]:
    if market_key != "US" or os.environ.get("QUANTA_EARNINGS_CALENDAR", "1") == "0":
        return []
    now = time.time()
    with _earnings_cal_lock:
        hit = _earnings_cal_cache.get(market_key)
    if hit and now - hit[0] < float(_T.MARKET.EARNINGS_CALENDAR_CACHE_SECONDS):
        return [dict(r) for r in hit[1]]
    rows: List[Dict[str, Any]] = []
    try:
        import yfinance as yf_local
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        today = datetime.now(ny).date()
        start = today - timedelta(days=CATALYST_WINDOW_DAYS_BACK)
        end = today + timedelta(days=CATALYST_WINDOW_DAYS_AHEAD + 1)
        df = yf_local.Calendars(start=start, end=end).get_earnings_calendar(
            limit=int(_T.MARKET.EARNINGS_CALENDAR_LIMIT), filter_most_active=False,
            market_cap=float(_T.MARKET.EARNINGS_MIN_MARKET_CAP))
        for sym, r in (df.iterrows() if df is not None and not df.empty else []):
            sym = str(sym).upper()
            # Five-letter symbols ending in F or Y are over-the-counter foreign
            # shares and ADRs; the listed line of the same company is kept.
            if len(sym) == 5 and sym[-1] in "FY":
                continue
            when = r.get("Event Start Date")
            try:
                local = when.tz_convert(ny) if getattr(when, "tzinfo", None) else when
                day = local.date()
            except Exception:
                continue
            days = (day - today).days
            if not (-CATALYST_WINDOW_DAYS_BACK <= days <= CATALYST_WINDOW_DAYS_AHEAD):
                continue
            timing = str(r.get("Timing") or "").upper()
            eps = r.get("EPS Estimate")
            cap = r.get("Marketcap")
            rows.append({
                "symbol": sym,
                "name": str(r.get("Company") or sym),
                "market_cap": float(cap) if cap == cap and cap is not None else None,
                "earnings_date": day.isoformat(),
                "earnings_date_pretty": day.isoformat(),
                "earnings_in_days": days,
                "earnings_timing": {"BMO": "bmo", "AMC": "amc"}.get(timing),
                "eps_estimate": round(float(eps), 2) if eps == eps and eps is not None else None,
                "earnings_source": "Yahoo earnings calendar",
            })
    except Exception as exc:
        logger.debug("Earnings calendar unavailable: %s", exc)
        return []
    with _earnings_cal_lock:
        _earnings_cal_cache[market_key] = (time.time(), rows)
    return [dict(r) for r in rows]


def _earnings_ahead(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Reports from today through the week calendar's last day: the one window
    every earnings count on the Overview uses (the calendar shows
    CALENDAR_DAYS_AHEAD days, today included)."""
    days = int(_T.MARKET.CALENDAR_DAYS_AHEAD)
    return [r for r in (rows or []) if 0 <= int(r.get("earnings_in_days") or 0) < days]


def _merge_earnings_calendar(existing: List[Dict[str, Any]], market_key: str) -> List[Dict[str, Any]]:
    """The screen's own rows first, then calendar rows it did not have; largest first within a day."""
    cal = _earnings_calendar(market_key)
    by_sym = {r["symbol"]: r for r in cal}
    have = set()
    rows = []
    for r in existing:
        sym = str(r.get("symbol") or "").upper()
        have.add(sym)
        c = by_sym.get(sym)
        if c and str(c.get("earnings_date")) == str(r.get("earnings_date_pretty") or r.get("earnings_date") or "")[:10]:
            # Same report: the screen's row keeps its fields; the calendar fills
            # what the screen does not carry (timing, estimate, size).
            r = {**r, **{k: c[k] for k in ("earnings_timing", "eps_estimate", "market_cap") if r.get(k) is None and c.get(k) is not None}}
        rows.append(r)
    merged = rows + [r for r in cal if r["symbol"] not in have]
    merged.sort(key=lambda r: (r.get("earnings_in_days") or 0, -(r.get("market_cap") or 0)))
    return merged


def _build_market_intel(key: str) -> Dict[str, Any]:
    universe = MARKET_UNIVERSES.get(key, MARKET_UNIVERSES["US"])

    # ---------- (1) Movers via screener (US only) ------------------------
    screener_gainers: List[Dict[str, Any]] = []
    screener_losers: List[Dict[str, Any]] = []
    screener_used = False
    # One shared pool bounds provider concurrency across requests and markets.
    # Consume completed work within a deadline; a slow symbol must not hide
    # screener results or make executor shutdown wait for every provider call.
    broad_symbols = _broad_universe(key)
    universe_rows: List[Dict[str, Any]] = []
    futures = {}
    if key == 'US':
        for side in ('day_gainers', 'day_losers'):
            futures[_market_intel_executor.submit(_screener_movers, side, SCREENER_FETCH_COUNT)] = side
    for symbol in broad_symbols:
        futures[_market_intel_executor.submit(_ticker_intel, symbol)] = 'ticker'
    timed_out = False
    try:
        for future in as_completed(futures, timeout=MARKET_INTEL_FETCH_SECONDS):
            try:
                result = future.result()
            except Exception as exc:
                logger.debug('Intel worker failed: %s', exc)
                continue
            kind = futures[future]
            if kind == 'day_gainers':
                screener_gainers = result
            elif kind == 'day_losers':
                screener_losers = result
            elif result and result.get('price') is not None:
                universe_rows.append(result)
    except FuturesTimeout:
        timed_out = True
    finally:
        for future in futures:
            future.cancel()
    screener_used = bool(screener_gainers or screener_losers)

    # Index universe rows by symbol so we can enrich screener hits with
    # analyst & earnings data when available.
    by_symbol = {r["symbol"]: r for r in universe_rows if r.get("symbol")}

    # ---------- (3) Assemble movers (screener-first, universe-fallback) ----
    if screener_used:
        # Enrich screener rows with analyst/earnings data from broad universe
        for row in screener_gainers + screener_losers:
            extra = by_symbol.get(row["symbol"])
            if extra:
                for fld in ("sector", "recommendation", "target_mean",
                            "analyst_count", "upside_pct", "earnings_date"):
                    if extra.get(fld) is not None and row.get(fld) is None:
                        row[fld] = extra[fld]
        # Add any universe row that crossed the extreme threshold but wasn't
        # in the screener response (rare, but guarantees the halo invariant).
        screener_syms = {r["symbol"] for r in screener_gainers + screener_losers}
        for r in universe_rows:
            cp = r.get("change_pct")
            if cp is None or r["symbol"] in screener_syms:
                continue
            if abs(cp) >= EXTREME_MOVE_PCT:
                (screener_gainers if cp > 0 else screener_losers).append(r)
        # Sort + clip
        screener_gainers.sort(key=lambda r: r.get("change_pct") or 0, reverse=True)
        screener_losers.sort(key=lambda r: r.get("change_pct") or 0)
        movers_up = screener_gainers[:MOVERS_DISPLAY_COUNT]
        movers_down = screener_losers[:MOVERS_DISPLAY_COUNT]
        universe_source = f"Yahoo screener \u00b7 enriched with Top {len(universe_rows)} universe"
    else:
        # Pure universe sort
        movers_up = sorted(
            [r for r in universe_rows if (r.get("change_pct") or 0) > 0],
            key=lambda r: r.get("change_pct") or 0,
            reverse=True,
        )[:MOVERS_DISPLAY_COUNT]
        movers_down = sorted(
            [r for r in universe_rows if (r.get("change_pct") or 0) < 0],
            key=lambda r: r.get("change_pct") or 0,
        )[:MOVERS_DISPLAY_COUNT]
        universe_source = f"Top {len(universe_rows)} broad universe"

    # [OPEN-EXTREMES] Scan every quote fetched this cycle, not just the rows
    # the movers board displays. On the screener path that is 25 gainers + 25
    # losers plus the universe scan; on the India path it is the universe
    # rows, which carry session OHLC from fast_info.
    open_pool: List[Dict[str, Any]] = []
    if screener_used:
        open_pool.extend(screener_gainers)
        open_pool.extend(screener_losers)
    open_pool.extend(universe_rows)
    open_at_low, open_at_high = _select_open_extremes(open_pool)
    _, open_extremes_coverage = _open_extreme_pool(open_pool)
    open_extremes_coverage["tracked_universe"] = len(broad_symbols)
    open_extremes_scanned = open_extremes_coverage["received"]

    # Tag extreme moves so the UI can highlight
    extreme_count = 0
    for r in movers_up + movers_down:
        if r.get("change_pct") is not None and abs(r["change_pct"]) >= EXTREME_MOVE_PCT:
            r["extreme"] = True
            extreme_count += 1

    # ---------- (4) Analyst Picks + Earnings (uses broad universe) -------
    def _is_strong(r):
        rec = (r.get("recommendation") or "")
        if isinstance(rec, str):
            return rec.lower() in {"buy", "strong_buy", "strongbuy"}
        try:
            return float(rec) <= 2.3
        except Exception:
            return False

    analyst_picks = sorted(
        [r for r in universe_rows if _is_strong(r) and (r.get("upside_pct") or 0) > 2],
        key=lambda r: r.get("upside_pct") or 0,
        reverse=True,
    )[:8]

    earnings_week: List[Dict[str, Any]] = []
    # Days are counted from the market's own date, the date its week calendar
    # starts from: India is already on the next day during the US evening.
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Kolkata" if key == "IN" else "America/New_York")).date()
    except Exception:
        today = datetime.now().date()
    # The catalyst window is CALENDAR days, and it includes yesterday so a
    # report that landed after the last close is still visible. The UI used to
    # describe the same list as both "7 sessions" and a "7-day window"; neither
    # matched this filter. CATALYST_WINDOW_LABEL is the single wording.
    for r in universe_rows:
        ed = r.get("earnings_date")
        if not ed:
            continue
        try:
            ed_dt = datetime.fromisoformat(str(ed).replace("Z", "+00:00")).date()
        except Exception:
            try:
                ed_dt = datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
        days = (ed_dt - today).days
        if -CATALYST_WINDOW_DAYS_BACK <= days <= CATALYST_WINDOW_DAYS_AHEAD:
            row = dict(r)
            row["earnings_in_days"] = days
            row["earnings_date_pretty"] = ed_dt.isoformat()
            earnings_week.append(row)
    earnings_week.sort(key=lambda r: r["earnings_in_days"])
    earnings_week = _merge_earnings_calendar(earnings_week, key)
    # "ok" when Yahoo's calendar answered, "off" outside the US or when disabled,
    # "unavailable" when it was asked and returned nothing.
    if key != "US" or os.environ.get("QUANTA_EARNINGS_CALENDAR", "1") == "0":
        cal_status = "off"
    else:
        with _earnings_cal_lock:
            cal_status = "ok" if (_earnings_cal_cache.get(key) or (0.0, []))[1] else "unavailable"

    # ---------- (5) Fallback: derive movers from market.quotes ----------
    #   When BOTH the Yahoo screener and the broad universe scan return nothing
    #   (rate-limited, network down, etc.), we still have the small set of
    #   index/sector quotes from `_market_snapshot()` cached on disk. Use those
    #   as a last-resort feed so the UI shows real numbers instead of an empty
    #   skeleton. Tagged with source="market_quotes" so the front-end can
    #   render an honest "limited feed" badge.
    fallback_used = False
    fallback_as_of = None
    if not movers_up and not movers_down:
        try:
            # Read the existing dashboard; do not start another quote/trend
            # fetch while the provider is already failing or timing out.
            with _dashboard_lock:
                dashboard = _dashboard_cache.get(f'payload_{key}_v2') or {}
                snap = dashboard.get('market') or {}
                fallback_as_of = dashboard.get('generated_at')
            quote_rows: List[Dict[str, Any]] = []
            for q in (snap.get("quotes") or []):
                price = q.get("price")
                cp = q.get("change_pct")
                sym = q.get("symbol")
                if price is None or cp is None or not sym:
                    continue
                # Skip volatility / yield / fx series, but keep equity index
                # and ETF proxies so the UI can still show a limited feed.
                blocked_symbols = {"^VIX", "^TNX", "^IRX", "^FVX", "^TYX", "^INDIAVIX"}
                if str(sym).upper() in blocked_symbols or "=" in str(sym):
                    continue
                quote_rows.append({
                    "symbol": str(sym),
                    "name": q.get("label") or str(sym),
                    "price": float(price),
                    "change_pct": float(cp),
                    "source": "market_quotes",
                })
            if quote_rows:
                fallback_used = True
                movers_up = sorted(
                    [r for r in quote_rows if r["change_pct"] > 0],
                    key=lambda r: r["change_pct"], reverse=True,
                )[:MOVERS_DISPLAY_COUNT]
                movers_down = sorted(
                    [r for r in quote_rows if r["change_pct"] < 0],
                    key=lambda r: r["change_pct"],
                )[:MOVERS_DISPLAY_COUNT]
                universe_source = f"Limited feed \u00b7 {len(quote_rows)} index/ETF quotes (Yahoo rate-limited)"
            elif not universe_rows:
                universe_source = "Market movers unavailable \u00b7 data provider returned no usable quotes"
        except Exception as exc:
            logger.warning("market-quotes fallback failed for %s: %s", key, exc)
            if not universe_rows:
                universe_source = "Market movers unavailable \u00b7 quote fallback failed"

    payload = {
        "market": key,
        "currency": universe["currency"],
        "movers_up": movers_up,
        "movers_down": movers_down,
        # [OPEN-EXTREMES] Opened at the session low / high. Scanned over every
        # quote already in hand -- the full screener response (not just the 8
        # rows per side the movers board shows) plus the broad universe scan --
        # so the boards are drawn from a real pool without a single extra
        # provider call. India has no screener feed and is scanned from its
        # universe rows alone, which is why the count is published too: an
        # empty board should read as "nothing matched in N names", never as a
        # broken panel.
        "open_at_low": open_at_low,
        "open_at_high": open_at_high,
        "open_extremes_scanned": open_extremes_scanned,
        "open_extremes_coverage": open_extremes_coverage,
        "analyst_picks": analyst_picks,
        "earnings_week": earnings_week[:10],
        # The full dated list for the week calendar, and the count of reports
        # still ahead (today included), so counts are not capped by the table.
        "earnings_calendar": earnings_week,
        "earnings_week_count": len(_earnings_ahead(earnings_week)),
        "earnings_scope": (f"US companies over ${_T.MARKET.EARNINGS_MIN_MARKET_CAP / 1e9:,.0f}B and the tracked universe"
                           if cal_status == "ok" else "the tracked universe"),
        "earnings_calendar_status": cal_status,
        "universe_size": len(universe_rows),
        "universe_source": universe_source,
        "screener_used": screener_used,
        "fallback_used": fallback_used,
        "extreme_count": extreme_count,
        "as_of": fallback_as_of if fallback_used else _now_iso(),
        "loading": False,
        "partial": timed_out,
        "stale": fallback_used,
        "degraded": not bool(movers_up or movers_down or universe_rows),
    }
    # ---------- (6) Cache preservation -----------------------------------
    #   Never overwrite a healthy cache with an empty/degraded payload — that
    #   leaves the UI worse off than it was a minute ago. If the new payload
    #   has zero movers AND we previously had real movers, keep the old cache
    #   so the user keeps seeing the last good data.
    with _market_intel_cache_lock:
        prior = _market_intel_cache.get(key)
        new_empty = not payload["movers_up"] and not payload["movers_down"]
        if prior and new_empty:
            prior_payload = prior.get("payload") or {}
            prior_has_data = bool(prior_payload.get("movers_up") or prior_payload.get("movers_down"))
            if prior_has_data:
                logger.info("intel: preserving prior %s cache (new payload was empty)", key)
                stale = dict(prior_payload)
                stale["stale"] = True
                stale["last_refresh_attempt"] = payload["as_of"]
                # Bump timestamp slightly so the next request retries sooner.
                stale['degraded'] = True
                _market_intel_cache[key] = {"timestamp": time.time(), "payload": stale}
                return stale
        _market_intel_cache[key] = {"timestamp": time.time(), "payload": payload}
    return payload


def _engine_payload() -> List[Dict[str, Any]]:
    engines = []
    for key, info in ENGINE_FILES.items():
        path = info["path"]
        engines.append({
            "id": key,
            "label": info["label"],
            "description": info["description"],
            "tab": info["tab"],
            "path": _display_path(path),
            "exists": path.exists(),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None,
        })
    return engines


def _analysis_config() -> Dict[str, str]:
    return {
        "news_api_key": os.getenv("NEWS_API_KEY", ""),
        "finnhub_key": os.getenv("FINNHUB_API_KEY", ""),
        "fmp_key": os.getenv("FMP_API_KEY", ""),
        "polygon_key": os.getenv("POLYGON_API_KEY", ""),
        "alpha_vantage_key": os.getenv("ALPHA_VANTAGE_KEY", ""),
    }


def _analysis_config_key(config: Dict[str, str]) -> tuple:
    return tuple(sorted(config.items()))


def _get_analysis_runtime() -> tuple:
    config = _analysis_config()
    config_key = _analysis_config_key(config)

    if (
        _analysis_runtime.get("module") is not None
        and _analysis_runtime.get("analyzer") is not None
        and _analysis_runtime.get("api_client") is not None
        and _analysis_runtime.get("config_key") == config_key
    ):
        return _analysis_runtime["module"], _analysis_runtime["analyzer"], _analysis_runtime["api_client"]

    import TradingAnalysis_ELITE as ta

    analyzer = ta.QuantaEliteAnalyzer(config)
    api_client = ta.APIClient(config)
    _analysis_runtime.update({
        "module": ta,
        "analyzer": analyzer,
        "api_client": api_client,
        "config_key": config_key,
    })
    return ta, analyzer, api_client


# [F04] Analysis completeness is part of the cache key. A Fast Mode run skips
# the network-heavy modules and is explicitly labelled PRELIMINARY, so it must
# never be handed back to a caller that asked for a full analysis. The reverse
# IS allowed: a complete result strictly dominates the preview it substitutes
# for. Ranked least to most complete.
_ANALYSIS_MODE_RANK = {"fast": 0, "full": 1}


def _analysis_cache_key(ticker: str, mode: str,
                       account_size: Optional[float] = None) -> str:
    # Position sizing is account-relative, so two accounts must not share a
    # cached tear sheet: the shares, weight and dollar risk would be wrong.
    acct = DEFAULT_ACCOUNT_SIZE if account_size is None else float(account_size)
    return f"{ticker}::{mode}::{acct:.2f}"


def _get_cached_analysis(ticker: str, mode: str = "full",
                         allow_stale: bool = False,
                         account_size: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Return a cached tear sheet for ``ticker`` at least as complete as ``mode``.

    [F04] Two corrections over the original:

      * An expired entry is no longer deleted by an ordinary lookup. It used to
        be popped on the miss, which meant the `allow_stale=True` retry that a
        provider outage falls back to could never find it — the fallback path
        destroyed its own input. Entries now persist until
        ANALYSIS_STALE_RETENTION_SECONDS, so "not fresh" and "not present" are
        different answers.
      * Lookups are keyed by ticker AND completeness, so a fast preview cannot
        satisfy a full-analysis request.
    """
    wanted_rank = _ANALYSIS_MODE_RANK.get(mode, 1)
    now = time.time()

    with _analysis_result_lock:
        # Prefer an exact mode match, then any strictly more complete result.
        candidates = []
        for cached_mode, rank in _ANALYSIS_MODE_RANK.items():
            if rank < wanted_rank:
                continue
            entry = _analysis_result_cache.get(
                _analysis_cache_key(ticker, cached_mode, account_size))
            if entry:
                candidates.append((rank == wanted_rank, entry, cached_mode))
        if not candidates:
            return None
        # Exact match first, then the freshest of the more complete ones.
        candidates.sort(key=lambda c: (not c[0], -float(c[1].get("timestamp") or 0)))
        _, cached, hit_mode = candidates[0]

        age = now - float(cached.get("timestamp") or 0)
        if age > ANALYSIS_STALE_RETENTION_SECONDS:
            # Past the retention window it is no longer a usable fallback.
            _analysis_result_cache.pop(
                _analysis_cache_key(ticker, hit_mode, account_size), None)
            return None
        if age > ANALYSIS_RESULT_CACHE_SECONDS and not allow_stale:
            return None

    payload = cached.get("payload")
    if isinstance(payload, dict):
        payload = copy.deepcopy(payload)
        is_stale = age > ANALYSIS_RESULT_CACHE_SECONDS
        payload["cache"] = {
            "hit": True, "age_seconds": int(age),
            "ttl_seconds": ANALYSIS_RESULT_CACHE_SECONDS, "stale": is_stale,
            "mode": hit_mode, "requested_mode": mode,
            "retention_seconds": ANALYSIS_STALE_RETENTION_SECONDS,
        }
        payload["cache_timestamp"] = float(cached.get("timestamp") or 0)
        _refresh_cached_decision_freshness(payload)
        return payload
    return None


def _refresh_cached_decision_freshness(payload: Dict[str, Any]) -> None:
    """Reassess a cached Decision against today's completed market session."""
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        return
    raw_as_of = decision.get("as_of") or payload.get("market_timestamp")
    try:
        as_of = datetime.fromisoformat(str(raw_as_of).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        as_of = None
    try:
        from engine.decision import assess_price_freshness
        from engine.types import FinalAction

        freshness = assess_price_freshness(as_of)
        decision["freshness"] = asdict(freshness)
        action = FinalAction(str(decision.get("action") or ""))
    except (ImportError, TypeError, ValueError):
        return
    if freshness.status == "fresh" or not action.allocates_new_capital:
        return

    reason = freshness.notes[0] if freshness.notes else "Cached price freshness is unknown."
    decision["action"] = FinalAction.WATCHLIST.value
    decision["label"] = "WATCHLIST"
    decision["sizing_basis"] = "none"
    decision["sizing"] = {k: 0 for k in (decision.get("sizing") or {"shares": 0, "position_value": 0})}
    decision.setdefault("blocking", []).append(reason)
    decision.setdefault("blocking_codes", []).append("STALE_PRICE_DATA")
    decision.setdefault("transitions", []).append({
        "from": action.value, "to": FinalAction.WATCHLIST.value,
        "code": "STALE_PRICE_DATA", "reason": reason,
    })
    payload["final_action"] = FinalAction.WATCHLIST.value
    sizing = payload.get("position_sizing")
    if isinstance(sizing, dict):
        sizing.update({
            "recommended_shares": 0, "position_value": 0.0,
            "portfolio_weight": 0.0, "kelly_optimal": 0.0,
            "is_viable": False,
        })
    setup = payload.get("trade_setup")
    if isinstance(setup, dict):
        setup["is_actionable_entry"] = False
        setup["entry_type"] = "REFERENCE"
    payload["action_items"] = [
        "Do not open a new position until current market data is available."
    ]
    payload["recommendation_text"] = "WATCHLIST - Cached market data is stale; no new position."



def _set_cached_analysis(ticker: str, payload: Dict[str, Any],
                         mode: str = "full",
                         account_size: Optional[float] = None) -> None:
    with _analysis_result_lock:
        _analysis_result_cache[_analysis_cache_key(ticker, mode, account_size)] = {
            "timestamp": time.time(),
            "ticker": ticker,
            "mode": mode,
            "account_size": account_size,
            "payload": payload,
        }


def _get_market_environment_cached(ta_module: Any, api_client: Any, allow_refresh: bool = True) -> Dict[str, Any]:
    with _market_env_lock:
        age = time.time() - float(_market_env_cache.get("timestamp") or 0)
        cached = _market_env_cache.get("payload")
    if cached is not None and age < MARKET_ENV_CACHE_SECONDS:
        return cached

    if not allow_refresh:
        return cached or {}

    market_env = ta_module.fetch_market_environment(api_client)
    with _market_env_lock:
        _market_env_cache["timestamp"] = time.time()
        _market_env_cache["payload"] = market_env
    return market_env


# Session LTP and the analyzer's price can legitimately disagree: the analyzer
# prices off the (possibly cached) daily frame it scored, while the chart is a
# separate fetch. Rather than silently showing two numbers, reconcile them once
# here and hand the UI an explicit, labelled record of which is which.
PRICE_DISCREPANCY_TOLERANCE_PCT = float(os.getenv("QUANTA_PRICE_DISCREPANCY_PCT", "0.25"))


def _reconcile_prices(analysis: Dict[str, Any], chart: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: compare analysis.current_price with price_chart.session.ltp.

    Returns a JSON-safe block::

        {"analysis_price": float|None, "session_ltp": float|None,
         "diff_pct": float|None, "discrepancy": bool, "tolerance_pct": float,
         "analysis_bar": str|None, "session_date": str|None, "note": str}
    """
    session = (chart or {}).get("session") or {}
    a_price = _opt_round((analysis or {}).get("current_price"), 4)
    ltp = _opt_round(session.get("ltp"), 4)
    market_ts = str((analysis or {}).get("market_timestamp") or "")[:10] or None
    out: Dict[str, Any] = {
        "analysis_price": a_price,
        "session_ltp": ltp,
        "diff_pct": None,
        "discrepancy": False,
        "tolerance_pct": PRICE_DISCREPANCY_TOLERANCE_PCT,
        "analysis_bar": market_ts,
        "session_date": session.get("date"),
        "note": "",
    }
    if a_price is None or ltp is None or a_price <= 0:
        out["note"] = "Session quote unavailable; the analysis price is the only reference."
        return out
    diff_pct = round((ltp - a_price) / a_price * 100.0, 3)
    out["diff_pct"] = diff_pct
    if abs(diff_pct) > PRICE_DISCREPANCY_TOLERANCE_PCT:
        out["discrepancy"] = True
        out["note"] = (
            f"Analysis was priced at {a_price:,.2f}"
            f"{' (bar ' + market_ts + ')' if market_ts else ''}; the session quote "
            f"shows {ltp:,.2f} ({diff_pct:+.2f}%). Targets, stops and R:R are "
            f"relative to the analysis price."
        )
    else:
        out["note"] = "Analysis price and session quote agree."
    return out


def _dashboard_payload(force_refresh: bool = False, market: str = "US") -> Dict[str, Any]:
    market_key = _normalize_market(market)
    # Include the regime feature-flag in the cache key so ?regime=v1 doesn't
    # silently return a cached v2 payload (or vice versa).
    regime_variant = "v1" if (request.args.get("regime") == "v1") else "v2"
    cache_key = f"payload_{market_key}_{regime_variant}"
    now = time.time()
    with _dashboard_lock:
        cached = _dashboard_cache.get(cache_key)
        age = now - float(_dashboard_cache.get(f"timestamp_{market_key}_{regime_variant}") or 0)
        has_quotes = cached and any(q.get('price') is not None and not q.get('stale') for q in (cached.get('market') or {}).get('quotes', []))
        # A read built while an input was missing (history still loading, the
        # earnings screen still warming up) is retried soon, not held for the
        # full cache period: the inputs usually arrive seconds later.
        missing = bool(cached and (((cached.get('market') or {}).get('market_regime_engine') or {})
                                   .get('dataFreshness') or {}).get('missingInputs'))
        cache_ttl = DASHBOARD_CACHE_SECONDS if has_quotes and not missing else MARKET_INTEL_RETRY_SECONDS
        if cached and not force_refresh and age < cache_ttl:
            # Refresh just the intel slot from latest cache so users don't see
            # an empty card if the warmup completed after the dashboard cache.
            cached = dict(cached)
            cached["market_intel"] = _market_intel(market_key, blocking=False)
            return cached

    # Build news + intel FIRST so the regime engine can ingest the live headline
    # tape and the dated earnings calendar (single source of truth).
    market_intel = _market_intel(market_key, blocking=False)
    news_digest = _build_news_digest(market_key)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_market": market_key,
        "available_markets": [
            {"code": code, "label": MARKET_UNIVERSES[code]["label"], "currency": MARKET_UNIVERSES[code]["currency"]}
            for code in MARKET_UNIVERSES.keys()
        ],
        "market": _market_snapshot(market_key, news=news_digest, intel=market_intel),
        "market_intel": market_intel,
        "news": news_digest,
        "engines": _engine_payload(),
        "cache_seconds": DASHBOARD_CACHE_SECONDS,
        # How soon a read with missing inputs is rebuilt (the page re-reads once).
        "retry_seconds": MARKET_INTEL_RETRY_SECONDS,
    }

    with _dashboard_lock:
        _dashboard_cache[f"timestamp_{market_key}_{regime_variant}"] = now
        _dashboard_cache[cache_key] = payload

    return payload


# ---------------------------------------------------------------------------
# ROUTES — Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    # The JSON sits inside a <script> block: escape "<", ">" and "&" so no
    # config string can close the block or open a comment there.
    blob = (json.dumps(_T.as_dict())
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return render_template("index.html", thresholds_json=blob)


@app.route("/api/dashboard")
def dashboard_payload():
    """Return the daily dashboard brief: market tape, news, trends, and engines."""
    force = request.args.get("refresh") in {"1", "true", "yes"}
    market = (request.args.get("market") or "US").upper()
    return jsonify(_safe_serialize(_dashboard_payload(force_refresh=force, market=market)))


@app.route("/api/market-intel")
def market_intel_endpoint():
    """Return movers / analyst picks / earnings for a given market.

    Nonblocking by default so polling joins one background scan; cached for
    MARKET_INTEL_CACHE_SECONDS.
    """
    market = _normalize_market(request.args.get("market"))
    blocking = request.args.get("blocking", "0") in {"1", "true", "yes"}
    return jsonify(_safe_serialize(_market_intel(market, blocking=blocking)))


# ---------------------------------------------------------------------------
# Market context for the Overview: sector funds, key benchmark levels and the
# expected move, cross-asset gauges, index futures and the dated calendar.
# Loaded by the page after the dashboard, so the market read never waits on it.
# Every symbol comes from MARKET config; every figure is a measurement or is
# returned as None, never estimated.
# ---------------------------------------------------------------------------
_context_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_context_lock = threading.Lock()
_context_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='market-context')


def _prev_from_change(price: Optional[float], change_pct: Optional[float]) -> Optional[float]:
    if price is None or change_pct is None or change_pct <= -100:
        return None
    return price / (1 + change_pct / 100.0)


def _key_levels(bench: str, vol_symbol: Optional[str]) -> Dict[str, Any]:
    """The benchmark's session range, prior session, averages, ATR and the
    one-session move its volatility index implies."""
    out: Dict[str, Any] = {"symbol": bench}
    try:
        import yfinance as yf_local
        tk = yf_local.Ticker(bench)
        daily = tk.history(period="3mo", interval="1d", timeout=8)
        daily = daily.dropna(subset=["Close"]) if daily is not None and not daily.empty else None
        intra = tk.history(period="1d", interval="5m", timeout=8)
        intra = intra.dropna(subset=["Close"]) if intra is not None and not intra.empty else None
    except Exception as exc:
        logger.debug("Key levels failed for %s: %s", bench, exc)
        return out
    session_day = None
    if intra is not None and len(intra):
        session_day = intra.index[-1].date().isoformat()
        out["session"] = {"date": session_day, "high": round(float(intra["High"].max()), 2),
                          "low": round(float(intra["Low"].min()), 2), "last": round(float(intra["Close"].iloc[-1]), 2)}
    if daily is not None and len(daily):
        prior = daily[[d.date().isoformat() < session_day for d in daily.index]] if session_day else daily.iloc[:-1]
        if len(prior):
            row = prior.iloc[-1]
            out["prior"] = {"date": prior.index[-1].date().isoformat(), "high": round(float(row["High"]), 2),
                            "low": round(float(row["Low"]), 2), "close": round(float(row["Close"]), 2)}
        n = int(_T.MARKET.ATR_SESSIONS)
        if len(daily) > n:
            hi, lo, cl = daily["High"], daily["Low"], daily["Close"]
            tr = [max(float(hi.iloc[i]) - float(lo.iloc[i]), abs(float(hi.iloc[i]) - float(cl.iloc[i - 1])),
                      abs(float(lo.iloc[i]) - float(cl.iloc[i - 1]))) for i in range(len(daily) - n, len(daily))]
            out["atr"] = round(sum(tr) / n, 2)
            out["atr_sessions"] = n
    trend = _trend_metrics(bench) or {}
    for k in ("sma50", "sma200", "above_sma50", "above_sma200"):
        out[k] = trend.get(k)
    last = (out.get("session") or {}).get("last") or trend.get("last")
    out["last"] = last
    if vol_symbol and last:
        vol = _quote_snapshot(vol_symbol, vol_symbol)
        level = vol.get("price")
        if level:
            move = last * (float(level) / 100.0) / (float(_T.MARKET.TRADING_DAYS_PER_YEAR) ** 0.5)
            out["expected_move"] = {"vol_symbol": vol_symbol, "vol_level": level, "points": round(move, 2),
                                    "pct": round(move / last * 100, 2), "low": round(last - move, 2), "high": round(last + move, 2)}
    return out


def _cross_asset_reads(quotes: Dict[str, Dict[str, Any]], market_key: str) -> List[Dict[str, Any]]:
    """Derived gauges: the 10Y less 3-month spread, high-yield vs investment-grade
    bonds, copper vs gold. Each only when both legs were measured."""
    px = lambda s: (quotes.get(s) or {}).get("price")
    ch = lambda s: (quotes.get(s) or {}).get("change_pct")
    out: List[Dict[str, Any]] = []
    if market_key != "US":
        return out
    ten, bill = px("^TNX"), px("^IRX")
    if ten is not None and bill is not None:
        prev_ten, prev_bill = _prev_from_change(ten, ch("^TNX")), _prev_from_change(bill, ch("^IRX"))
        spread = (ten - bill) * 100
        prev = (prev_ten - prev_bill) * 100 if prev_ten is not None and prev_bill is not None else None
        out.append({"key": "curve", "label": "10Y less 3-month", "value": round(spread, 1), "unit": "bp",
                    "change": round(spread - prev, 1) if prev is not None else None, "change_unit": "bp",
                    "read": "inverted" if spread < 0 else "not inverted",
                    "note": "Long less short Treasury yields; below zero is inverted"})
    for key, label, a, b, note in (("credit", "High-yield vs investment-grade bonds", "HYG", "LQD",
                                    "Rising when riskier bonds outpace safer ones"),
                                   ("growth", "Copper vs gold", "HG=F", "GC=F",
                                    "Rising when industrial demand outpaces haven buying")):
        pa, pb = px(a), px(b)
        if pa and pb:
            qa, qb = _prev_from_change(pa, ch(a)), _prev_from_change(pb, ch(b))
            ratio = pa / pb
            chg = ((ratio / (qa / qb)) - 1) * 100 if qa and qb else None
            out.append({"key": key, "label": label, "value": round(ratio, 4), "unit": "ratio", "note": note,
                        "change": round(chg, 2) if chg is not None else None, "change_unit": "%",
                        "read": None if chg is None else ("risk appetite" if chg > 0 else "risk aversion" if chg < 0 else "flat")})
    return out


def _calendar_events(market_key: str) -> List[Dict[str, Any]]:
    """Dated macro events in the next CALENDAR_DAYS_AHEAD days, from the
    publishers' own schedules (US only: the file transcribes Fed and BLS dates)."""
    if market_key != "US":
        return []
    try:
        from portfolio.sources import load_official_calendar
        cal = load_official_calendar()
    except Exception as exc:
        logger.debug("Official calendar unavailable: %s", exc)
        return []
    today = datetime.now().date()
    ahead = int(_T.MARKET.CALENDAR_DAYS_AHEAD)
    out = []
    for e in (cal or {}).get("events") or []:
        try:
            d = datetime.strptime(str(e.get("date"))[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if 0 <= (d - today).days <= ahead:
            out.append({"kind": e.get("kind"), "title": e.get("title"), "date": d.isoformat(), "time": e.get("time"),
                        "tz": e.get("tz"), "source": e.get("source"), "url": e.get("url")})
    return sorted(out, key=lambda e: (e["date"], e.get("time") or ""))


def _market_context(market_key: str, force: bool = False) -> Dict[str, Any]:
    now = time.time()
    with _context_lock:
        hit = _context_cache.get(market_key)
    if hit and not force and now - hit[0] < float(_T.MARKET.CONTEXT_CACHE_SECONDS):
        return {**hit[1], "cached": True}
    basket = _T.MARKET.TREND_BASKETS.get(market_key) or _T.MARKET.TREND_BASKETS["US"]
    sectors = list(_T.MARKET.SECTOR_BASKETS.get(market_key, ()))
    cross = list(_T.MARKET.CROSS_ASSET.get(market_key, ()))
    futures = list(_T.MARKET.FUTURES.get(market_key, ()))
    vol_symbol = _T.MARKET.VOL_INDEX.get(market_key)
    jobs: Dict[str, Any] = {}
    # The levels read makes the most calls: it goes first, not behind the quotes.
    jobs["levels"] = _context_executor.submit(_key_levels, basket["bench"], vol_symbol)
    for sym, label in sectors + cross + futures:
        jobs[f"q:{sym}"] = _context_executor.submit(_quote_snapshot, sym, label)
    for sym, _label in sectors:
        jobs[f"t:{sym}"] = _context_executor.submit(_trend_metrics, sym)
    deadline = now + float(_T.MARKET.CONTEXT_BUDGET_SECONDS)
    results: Dict[str, Any] = {}
    missing: List[str] = []
    for key, fut in jobs.items():
        try:
            results[key] = fut.result(timeout=max(0.1, deadline - time.time()))
        except Exception:
            results[key] = None
            missing.append(key.split(":", 1)[-1])
    quote = lambda s: results.get(f"q:{s}") or {}

    def row(sym: str, label: str) -> Dict[str, Any]:
        q = quote(sym)
        return {"symbol": sym, "label": label, "price": q.get("price"), "change_pct": q.get("change_pct"),
                "quote_date": q.get("quote_date"), "stale": bool(q.get("stale"))}

    sector_rows = []
    for sym, label in sectors:
        r = row(sym, label)
        t = results.get(f"t:{sym}") or {}
        r["above_sma50"] = t.get("above_sma50")
        r["above_sma200"] = t.get("above_sma200")
        sector_rows.append(r)
    measured = [r for r in sector_rows if r["above_sma50"] is not None]
    payload = {
        "available": True,
        "market": market_key,
        "sectors": sector_rows,
        "sector_participation": {"above": sum(1 for r in measured if r["above_sma50"]), "measured": len(measured),
                                 "requested": len(sectors)},
        "levels": results.get("levels") or {"symbol": basket["bench"]},
        "cross_asset": {"quotes": [row(s, l) for s, l in cross],
                        "reads": _cross_asset_reads({s: quote(s) for s, _ in cross}, market_key)},
        "futures": [row(s, l) for s, l in futures],
        "calendar": _calendar_events(market_key),
        "missing": missing,
        "generated_at": _now_iso(),
    }
    # An incomplete read is kept only briefly, so the next request completes it
    # instead of serving the gap for the full cache window.
    stamp = time.time() if not missing else time.time() - float(_T.MARKET.CONTEXT_CACHE_SECONDS) + 30.0
    with _context_lock:
        _context_cache[market_key] = (stamp, payload)
    return payload


@app.route("/api/market-context")
def market_context_endpoint():
    """Sector funds, key levels and the expected move, cross-asset gauges,
    index futures and the dated macro calendar for one market."""
    if _cross_site_read():
        return jsonify({"available": False, "error": "Cross-site request refused."}), 403
    market = _normalize_market(request.args.get("market"))
    force = request.args.get("refresh") in {"1", "true", "yes"}
    return jsonify(_safe_serialize(_market_context(market, force=force)))


@app.route("/api/thresholds")
def thresholds_api():
    """Every decision threshold the scan, the Research engine and the page use.

    The page renders this in "Scan method" and fills every number it prints in
    a rule or remedy from it, so the text cannot drift from the rule (F9).
    """
    return jsonify({"thresholds": _T.as_dict(), "source": "engine/thresholds.py"})


@app.route("/api/engines")
def engines_payload():
    """Return dashboard metadata for the two local analysis engines."""
    return jsonify({"engines": _safe_serialize(_engine_payload())})


# [SCAN-HEALTH] Cache the live data-provider probe so the health panel does not
# hammer yfinance on every poll.
_health_cache: Dict[str, Any] = {"timestamp": 0.0, "payload": None}
_health_lock = threading.Lock()
HEALTH_CACHE_SECONDS = 60


def _provider_rl_snapshot() -> Dict[str, Any]:
    """Read the scanner's shared provider rate-limit tracker (cooldown, hits)."""
    try:
        mod = importlib.import_module("SP_ELITE_PRO")
        snap = mod.PROVIDER_STATE.snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception:
        return {}


# [PROVIDER-RESILIENCE] Thin helpers around the SP_ELITE_PRO singleton so all
# webapp code paths (tear sheet, chart, health probe) share one cooldown.
def _provider_in_cooldown() -> bool:
    try:
        mod = importlib.import_module("SP_ELITE_PRO")
        return bool(mod.PROVIDER_STATE.in_cooldown())
    except Exception:
        return False


def _provider_cooldown_remaining() -> float:
    try:
        mod = importlib.import_module("SP_ELITE_PRO")
        return float(mod.PROVIDER_STATE.cooldown_remaining())
    except Exception:
        return 0.0


def _provider_note_success() -> None:
    try:
        mod = importlib.import_module("SP_ELITE_PRO")
        mod.PROVIDER_STATE.note_success()
    except Exception:
        pass


def _provider_note_rate_limit(retry_after: Optional[float] = None) -> None:
    try:
        mod = importlib.import_module("SP_ELITE_PRO")
        mod.PROVIDER_STATE.note_rate_limit(retry_after)
    except Exception:
        pass


def _provider_classify_rate_limit(exc: BaseException) -> bool:
    """Return True when the exception looks like a yfinance rate-limit."""
    try:
        mod = importlib.import_module("SP_ELITE_PRO")
        return bool(mod._is_rate_limit_error(exc))
    except Exception:
        low = str(exc).lower()
        return ("ratelimit" in type(exc).__name__.lower() or "429" in low
                or "too many request" in low or "rate limit" in low
                or "throttle" in low)


# Tear-sheet chart cache (per symbol+period). Survives across requests so
# Stock Deep-Dive can serve the chart from cache during a provider cooldown.
_price_chart_cache: Dict[str, Dict[str, Any]] = {}
_price_chart_cache_lock = threading.Lock()
PRICE_CHART_CACHE_SECONDS = int(os.getenv("QUANTA_CHART_CACHE_SECONDS", "600"))


def _price_chart_cache_key(symbol: str, period: str) -> str:
    return f"{symbol.upper()}::{period}"


def _price_chart_cache_get(symbol: str, period: str) -> Optional[Dict[str, Any]]:
    key = _price_chart_cache_key(symbol, period)
    with _price_chart_cache_lock:
        entry = _price_chart_cache.get(key)
        if not entry:
            return None
        if (time.time() - entry["timestamp"]) > PRICE_CHART_CACHE_SECONDS:
            return None
        payload = dict(entry["payload"])
        payload["cache_timestamp"] = entry["timestamp"]
        return payload


def _price_chart_cache_set(symbol: str, period: str, payload: Dict[str, Any]) -> None:
    key = _price_chart_cache_key(symbol, period)
    with _price_chart_cache_lock:
        _price_chart_cache[key] = {"timestamp": time.time(), "payload": dict(payload)}


def _probe_price_provider() -> Dict[str, Any]:
    """Lightweight liveness probe: can we fetch a single quote right now?

    [PROVIDER-RESILIENCE] During an active cooldown we do NOT call yfinance —
    the probe would itself contribute to the rate-limit storm. Instead we
    report the cooldown state from the shared tracker.
    """
    if _provider_in_cooldown():
        return {
            "yfinance_available": True,
            "price_fetch_ok": False,
            "rate_limited": True,
            "detail": f"Skipped — provider in cooldown ({int(_provider_cooldown_remaining())}s remaining)",
            "skipped_due_to_cooldown": True,
        }
    try:
        import yfinance as yf  # noqa: WPS433 (local import keeps startup fast)
    except Exception as exc:  # pragma: no cover - import failure is the signal
        return {"yfinance_available": False, "price_fetch_ok": False, "rate_limited": False,
                "detail": f"yfinance import failed: {exc}"}
    try:
        hist = yf.Ticker("AAPL").history(period="1d")
        ok = hist is not None and not hist.empty
        if ok:
            _provider_note_success()
        return {
            "yfinance_available": True,
            "price_fetch_ok": bool(ok),
            "rate_limited": False,
            "detail": "Live quote fetched" if ok else "Empty price frame returned",
            "last_price": (round(float(hist["Close"].iloc[-1]), 2) if ok else None),
        }
    except Exception as exc:
        is_rl = _provider_classify_rate_limit(exc)
        if is_rl:
            _provider_note_rate_limit()
        return {"yfinance_available": True, "price_fetch_ok": False, "rate_limited": bool(is_rl),
                "detail": f"Price fetch {'rate-limited' if is_rl else 'failed'}: "
                          f"{type(exc).__name__}: {exc}"}


def _sec_contact_status() -> Dict[str, Any]:
    """configured / missing / malformed and where it came from; never the value.
    When it is not configured, the reason's label and fix ride along for the page."""
    try:
        from portfolio.sources import resolve_sec_contact
        from engine.corporate_events import UNAVAILABLE_REASONS
        status = resolve_sec_contact().public()
        if status["state"] in ("missing", "malformed"):
            label, _template, fix = UNAVAILABLE_REASONS[f"contact_{status['state']}"]
            status.update(label=label, fix=fix)
        return status
    except Exception as exc:  # noqa: BLE001 - health must answer even if the engine cannot load
        return {"state": "unknown", "source": None, "variable": "QUANTA_SEC_CONTACT",
                "error": type(exc).__name__}


def _sec_contact_startup_check() -> Dict[str, Any]:
    """[I1] Server start: adopt the user-scope or .env contact when the launching
    terminal's environment predates it, and warn when there is none. The corporate-
    event check fails closed without it, so no Picks name can be funded."""
    try:
        from portfolio.sources import apply_sec_contact_to_env
        status = apply_sec_contact_to_env()
    except Exception as exc:  # noqa: BLE001
        status = {"state": "unknown", "source": None, "variable": "QUANTA_SEC_CONTACT",
                  "error": type(exc).__name__}
    if status.get("state") == "configured":
        logger.info(f"SEC contact: configured ({status.get('variable')}, from {status.get('source')})")
    elif status.get("state") == "malformed":
        logger.warning(f"SEC contact: {status.get('variable')} is set (from {status.get('source')}) but is not "
                       "an email address or web address. The corporate-event check is unavailable and no "
                       "Picks name can get a Buy call until it is fixed.")
    else:
        logger.warning("SEC contact: QUANTA_SEC_CONTACT is not set in the process, the Windows user "
                       "environment or the repository .env. The corporate-event check is unavailable and "
                       "no Picks name can get a Buy call. Set it to your email address and restart the server.")
    return status


def _backend_health(probe: bool = True) -> Dict[str, Any]:
    """Assemble the backend/data-provider health snapshot for the dashboard."""
    # Backend import status — the single most common cause of a 100% failure.
    import_ok, import_error = True, None
    try:
        importlib.import_module("SP_ELITE_PRO")
    except Exception as exc:
        import_ok, import_error = False, f"{type(exc).__name__}: {exc}"

    providers = {
        "polygon": bool(os.getenv("POLYGON_API_KEY")),
        "fmp": bool(os.getenv("FMP_API_KEY")),
        "finnhub": bool(os.getenv("FINNHUB_API_KEY")),
        "newsapi": bool(os.getenv("NEWS_API_KEY")),
        "alpha_vantage": bool(os.getenv("ALPHA_VANTAGE_KEY")),
    }

    price_probe: Dict[str, Any] = {}
    if probe:
        now = time.time()
        with _health_lock:
            cached = _health_cache.get("payload")
            if cached and (now - _health_cache.get("timestamp", 0.0)) < HEALTH_CACHE_SECONDS:
                price_probe = cached
            else:
                price_probe = _probe_price_provider()
                _health_cache["payload"] = price_probe
                _health_cache["timestamp"] = now

    with _last_scan_meta_lock:
        scan_meta = dict(_last_scan_meta)

    # [PROVIDER-RESILIENCE] Provider throttle state shared with the scanner.
    provider_status = _provider_rl_snapshot()
    rate_limited = bool(price_probe.get("rate_limited")) or bool(provider_status.get("rate_limited"))

    # Roll up to an overall status the UI can colour-code.
    provider_live = bool(price_probe.get("price_fetch_ok")) if probe else None
    if not import_ok:
        overall = "down"
    elif rate_limited:
        # A throttle is distinct from a hard outage — surface it explicitly so
        # the UI shows the cooldown + safe-mode retry instead of "DEGRADED".
        overall = "rate_limited"
    elif probe and provider_live is False:
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "overall": overall,
        "rate_limited": rate_limited,
        "provider_status": provider_status,
        "python_version": sys.version.split()[0],
        "backend_import_ok": import_ok,
        "backend_import_error": import_error,
        "providers": providers,
        "price_probe": price_probe,
        "last_scan": scan_meta,
        "sec_contact": _sec_contact_status(),
        # Offset-aware like every other stamp that crosses to the browser;
        # a naive isoformat() is parsed by new Date() as browser-local time.
        "timestamp": _now_iso(),
    }


@app.route("/api/health")
def health_payload():
    """Backend + data-provider health for the dashboard status panel.

    ``?probe=0`` skips the live yfinance quote fetch (fast, no network).
    """
    probe = request.args.get("probe", "1") not in {"0", "false", "no"}
    return jsonify(_safe_serialize(_backend_health(probe=probe)))


# Engines whose calls reach the ledger, with the evidence key that governs
# how each is labelled. Mirrors engine/evidence.py; the gate stays there.
_TRACK_RECORD_ENGINES = (
    ("elite", "Deep analysis", "ELITE"),
    ("elite_pro", "Portfolio scanner", "ELITE_PRO"),
    ("shortterm", "Short-term setups", "SHORTTERM"),
)
_TRACK_RECORD_RECENT = 25


def _track_record_payload(engine: Optional[str] = None) -> Dict[str, Any]:
    """Benchmarked outcomes from the shared ledger, plus each engine's evidence.

    Read-only: resolving outcomes needs price history for every pending call,
    so it stays an explicit offline job (``python -m tracking resolve``).
    """
    from engine import evidence as ev  # noqa: WPS433 - engine lives on sys.path
    from tracking.alerts import recent_alerts
    from tracking.fingerprint import code_fingerprint
    from tracking.ledger import BENCHMARK, MIN_SAMPLE, SignalLedger, pending_schedule

    ledger = SignalLedger()
    rows = ledger.rows(engine=engine)
    # Grouped by model version too: a result measured on one version is never
    # pooled with another's (unversioned legacy rows group as strategy=None).
    groups = [g.to_dict() for g in ledger.summarize(
        group_by=("engine", "strategy", "horizon_days", "stance"), engine=engine)]

    def _recent_row(r: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: r.get(k) for k in (
            "engine", "ticker", "as_of_date", "horizon_days", "stance", "label",
            "evidence_status", "reference_price", "target_price", "stop_price",
            "entry_date", "entry_basis", "return_pct", "benchmark_return_pct",
            "excess_return_pct", "outcome", "first_touch",
        )}
        # Each call was scored against the benchmark recorded with it (Nifty for
        # NSE names), so the row must say which.
        out["benchmark"] = r.get("benchmark") or BENCHMARK
        if r.get("outcome") is None:
            out.update(pending_schedule(r))
        return out

    recent = [
        _recent_row(r)
        for r in sorted(rows, key=lambda r: (r.get("as_of_date") or "", r.get("id") or 0),
                        reverse=True)[:_TRACK_RECORD_RECENT]
    ]
    benchmarks = sorted({r.get("benchmark") or BENCHMARK for r in rows})
    # Pending calls, by where they actually stand on their own schedule.
    schedules = [pending_schedule(r) for r in rows if r.get("outcome") is None]
    maturing = [s["expected_exit"] for s in schedules
                if s["state"] == "awaiting_maturity" and s["expected_exit"]]
    resolved = sum(1 for r in rows if r.get("outcome")
                   and r.get("entry_basis") == "next_session_open")
    legacy = sum(1 for r in rows if r.get("outcome")
                 and r.get("entry_basis") != "next_session_open")
    engines = []
    for key, label, strategy_attr in _TRACK_RECORD_ENGINES:
        evidence = ev.get_evidence(getattr(ev, strategy_attr)).to_dict()
        # Model freeze: calls made by the code running now, versus earlier code.
        current = code_fingerprint(key)
        mine = [r for r in rows if r.get("engine") == key]
        engines.append({"engine": key, "label": label, "evidence": evidence,
                        "recorded": len(mine), "code_fingerprint": current,
                        "recorded_on_current_code": sum(1 for r in mine
                                                        if r.get("code_fingerprint") == current)})
    return {
        "as_of": _now_iso(),
        # The single benchmark when every call shares one; None when the ledger
        # mixes markets (see ``benchmarks``). Never a global label that hides Nifty.
        "benchmark": (benchmarks[0] if len(benchmarks) == 1 else (BENCHMARK if not benchmarks else None)),
        "benchmarks": benchmarks,
        "benchmark_mixed": len(benchmarks) > 1,
        "min_sample": MIN_SAMPLE,
        "totals": {
            "recorded": len(rows),
            "resolved": resolved,
            "pending": len(schedules),
            "awaiting_maturity": sum(1 for s in schedules if s["state"] == "awaiting_maturity"),
            "awaiting_resolution": sum(1 for s in schedules if s["state"] != "awaiting_maturity"),
            "earliest_expected_exit": min(maturing) if maturing else None,
            "legacy_excluded": legacy,
        },
        "groups": groups,
        "recent": recent,
        # Stops / targets touched by open calls (informational; never an outcome).
        "alerts": recent_alerts(ledger, engine=engine),
        "engines": engines,
        "method": (
            "Entry at the first session open after publication; exit at the close "
            "of the horizon's final session; scored as excess return over the benchmark "
            "recorded with each call (SPY for US listings, ^NSEI for NSE listings) "
            "on the same dates. Earlier as-of-close results are excluded."
        ),
    }


@app.route("/api/track-record")
def track_record():
    engine = (request.args.get("engine") or "").strip().lower() or None
    if engine and engine not in {k for k, _l, _s in _TRACK_RECORD_ENGINES}:
        return jsonify({"error": f"unknown engine '{engine}'"}), 400
    try:
        return jsonify(_safe_serialize(_track_record_payload(engine)))
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as an honest state
        logger.warning("track record unavailable: %s", exc)
        return jsonify({"error": "Track record is unavailable", "detail": type(exc).__name__}), 503


# The last completed Picks scan per market, so a restart (or a new tab) opens on
# the most recent book instead of an empty page. Beside the response cache, so
# the test suite's temporary cache directory isolates it too.
_LAST_SCAN_DIR = Path(os.environ.get("QUANTA_CACHE_DIR", str(PROJECT_ROOT / "data" / "cache"))) / "last_scan"


def _last_scan_path(market: str) -> Path:
    return _LAST_SCAN_DIR / f"{'IN' if str(market).upper() == 'IN' else 'US'}.json.gz"


def _save_last_scan(params: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Best effort: a failed save never fails the scan that produced the result."""
    path = _last_scan_path(params.get("market", "US"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"saved_at": _now_iso(), "params": _safe_serialize(params),
                   "result": _safe_serialize(result)}
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(gzip.compress(json.dumps(payload, default=str).encode("utf-8")))
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("last scan not saved: %s", exc)


@app.route("/api/stock-picks/last")
def stock_picks_last():
    """The most recent completed Picks scan for a market, or 404 when there is none."""
    market = (request.args.get("market") or "US").strip().upper()
    path = _last_scan_path(market)
    if not path.exists():
        return jsonify({"error": "No completed scan yet"}), 404
    try:
        return jsonify(json.loads(gzip.decompress(path.read_bytes())))
    except Exception as exc:  # noqa: BLE001 - a corrupt file reads as "no scan"
        logger.warning("last scan unreadable: %s", exc)
        return jsonify({"error": "The saved scan could not be read"}), 404


_TRACK_RESOLVE_LOCK = threading.Lock()


@app.route("/api/track-record/resolve", methods=["POST"])
def track_record_resolve():
    """Score every call whose horizon has elapsed, then check open calls' levels.

    The same work as ``python -m tracking resolve`` and ``alerts``, for owners who
    do not schedule scripts/quanta_daily.sh. One run at a time: it loads price
    history for every pending ticker.
    """
    if not _TRACK_RESOLVE_LOCK.acquire(blocking=False):
        return jsonify({"error": "A resolve run is already in progress"}), 409
    try:
        from tracking.alerts import check_levels, notify_desktop
        from tracking.ledger import SignalLedger, yfinance_loader

        # Resolving and the level check read the same tickers: download each once.
        cache: Dict[tuple, Any] = {}

        def loader(ticker, start, end):
            key = (ticker, start, end)
            if key not in cache:
                cache[key] = yfinance_loader(ticker, start, end)
            return cache[key]

        ledger = SignalLedger()
        resolved = ledger.resolve_outcomes(loader)
        alerts = check_levels(ledger, loader)
        # Announced here means the scheduled job will not repeat it, so say it now too.
        notify_desktop(alerts)
        logger.info("track record: resolved %d call(s), %d new level alert(s)", resolved, len(alerts))
        return jsonify({"resolved": resolved, "alerts": [a.to_dict() for a in alerts]})
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as an honest state
        logger.warning("track record resolve failed: %s", exc)
        return jsonify({"error": "Resolving outcomes failed; see the server log.",
                        "detail": type(exc).__name__}), 503
    finally:
        _TRACK_RESOLVE_LOCK.release()


@app.route("/api/cache/stats")
def cache_stats():
    """Report yfinance cache occupancy so the UI can show warm/cold state."""
    info_cache = globals().get("_yf_info_cache") or {}
    hist_cache = globals().get("_yf_hist_cache") or {}
    return jsonify({
        "enabled": bool(globals().get("_YF_CACHE_ENABLED")),
        "disk_enabled": bool(globals().get("_YF_DISK_CACHE_ENABLED")),
        "info_entries": len(info_cache),
        "history_entries": len(hist_cache),
        "info_ttl_s": globals().get("_YF_INFO_TTL"),
        "history_ttl_s": globals().get("_YF_HIST_TTL"),
    })


@app.route("/api/cache/clear", methods=["POST"])
def cache_clear():
    """Manually flush the yfinance response cache (memory + disk).

    Use when you want a guaranteed-fresh scan rather than waiting for the TTL to
    expire. Returns the number of entries dropped.
    """
    clear_fn = globals().get("_yf_cache_clear")
    if not callable(clear_fn):
        return jsonify({"ok": False, "error": "cache not installed"}), 503
    try:
        dropped = clear_fn(persist=True)
        logger.info("yfinance cache cleared via API (%d entries)", dropped)
        return jsonify({"ok": True, "dropped": dropped})
    except Exception as e:
        logger.warning("Cache clear failed: %s", e)
        return jsonify({"ok": False, "error": "Cache clear failed; see the server log."}), 500


# /api/open-engine (spawn VS Code / the OS file handler on an engine file) was
# removed: no page used it, and a local web server should not launch processes.


# ---------------------------------------------------------------------------
# ROUTES — Stock Picks (SP_ELITE_PRO)
# ---------------------------------------------------------------------------

@app.route("/api/stock-picks", methods=["POST"])
def start_stock_picks():
    """Launch a background stock scan. Returns a job_id for progress polling."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a valid JSON object"}), 400
    for field in ("diversify", "safe_mode"):
        if field in data and type(data[field]) is not bool:
            return jsonify({"error": f"{field} must be a boolean"}), 400
    job_id = str(uuid.uuid4())[:8]

    # SP_ELITE_PRO uses the literal string "INDIA" for the India branch. The
    # web UI passes the ISO-style "IN" code, so normalize here.
    raw_market = str(data.get("market") or "US").upper()
    market = "INDIA" if raw_market in ("IN", "INDIA") else "US"

    # Validate scan_type per market. The India universe builder only accepts
    # india_* scan types; the US builder rejects them. Map gracefully.
    INDIA_SCANS = {"india_quick", "india_standard", "india_sectoral",
                   "india_comprehensive", "india_full",
                   "india_growth", "india_value", "india_dividend", "india_momentum",
                   "india_large", "india_mid", "india_small"}
    US_SCANS = {"quick", "standard", "comprehensive", "full",
                "focused_growth", "focused_value", "focused_dividend", "focused_momentum",
                "focused_etf", "large_cap", "mid_cap", "small_cap",
                "pre_momentum"}
    # The same preset in the other market where one exists (ETF Sleeve and
    # Pre-Momentum have no India counterpart and map to Standard).
    US_TO_IN = {"quick": "india_quick", "standard": "india_standard",
                "comprehensive": "india_comprehensive", "full": "india_comprehensive",
                "focused_growth": "india_growth", "focused_value": "india_value",
                "focused_dividend": "india_dividend", "focused_momentum": "india_momentum",
                "focused_etf": "india_standard",
                "large_cap": "india_large", "mid_cap": "india_mid",
                "small_cap": "india_small", "pre_momentum": "india_standard"}
    IN_TO_US = {"india_quick": "quick", "india_standard": "standard",
                "india_sectoral": "standard",
                "india_comprehensive": "comprehensive", "india_full": "full",
                "india_growth": "focused_growth", "india_value": "focused_value",
                "india_dividend": "focused_dividend", "india_momentum": "focused_momentum",
                "india_large": "large_cap", "india_mid": "mid_cap", "india_small": "small_cap"}

    requested_scan = str(data.get("scan_type") or "quick").lower()
    if requested_scan == "custom":
        scan_type = "custom"
    elif market == "INDIA":
        if requested_scan in INDIA_SCANS:
            scan_type = requested_scan
        elif requested_scan in US_SCANS:
            scan_type = US_TO_IN.get(requested_scan, "india_standard")
        else:
            scan_type = None
    else:  # US
        if requested_scan in US_SCANS:
            scan_type = requested_scan
        elif requested_scan in INDIA_SCANS:
            scan_type = IN_TO_US.get(requested_scan, "standard")
        else:
            scan_type = None
    # [Picks revamp] An unknown scan used to run as Standard without a word: a
    # page newer than its server asked for Momentum and got Standard's 250.
    if scan_type is None:
        return jsonify({"error": f"Unknown scan type '{requested_scan[:40]}'. "
                                 "Restart the QUANTA server so it knows this scan, then run it again."}), 400

    C = _T.CAPITAL
    minimum = C.MIN_INDIA if market == "INDIA" else C.MIN_US
    default_capital = C.DEFAULT_INDIA if market == "INDIA" else C.DEFAULT_US
    if type(data.get("max_positions", C.MAX_POSITIONS_DEFAULT)) is not int:
        return jsonify({"error": "max_positions must be an integer"}), 400
    if type(data.get("capital", default_capital)) not in (int, float):
        return jsonify({"error": "capital must be numeric"}), 400
    try:
        capital = float(data.get("capital", default_capital))
        max_positions = int(data.get("max_positions", C.MAX_POSITIONS_DEFAULT))
    except (TypeError, ValueError):
        return jsonify({"error": "capital and max_positions must be numeric"}), 400
    if not (minimum <= capital <= C.MAX):
        return jsonify({"error": f"capital must be between {minimum:,.0f} and {C.MAX:,.0f}"}), 400
    if not (1 <= max_positions <= C.MAX_POSITIONS_LIMIT):
        return jsonify({"error": f"max_positions must be between 1 and {C.MAX_POSITIONS_LIMIT}"}), 400

    # [Scan filters] Optional narrowing, in the scan's currency; never loosens a rule.
    try:
        filters = _scan_filters.normalize(data.get("filters"))
    except ValueError as exc:
        return jsonify({"error": f"filters: {exc}"}), 400

    custom_tickers = data.get("custom_tickers")
    if scan_type == "custom" and not custom_tickers:
        return jsonify({"error": "custom scan requires at least one ticker"}), 400
    if custom_tickers is not None:
        if not isinstance(custom_tickers, list) or len(custom_tickers) > C.MAX_CUSTOM_TICKERS:
            return jsonify({"error": f"custom_tickers must be a list of at most {C.MAX_CUSTOM_TICKERS} symbols"}), 400
        normalized = []
        for symbol in custom_tickers:
            symbol = str(symbol or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9^.=-]{1,32}", symbol):
                return jsonify({"error": f"invalid ticker symbol: {symbol[:32]}"}), 400
            normalized.append(symbol)
        custom_tickers = normalized

    # The Client Mandate validates to one of the offered profiles
    # (SIZING.MANDATE_SCALE) and is applied in the worker. A retired profile
    # (audit F14: "aggressive" sized exactly like balanced) is accepted from old
    # clients, sized as its alias, and the result says so.
    risk_profile = str(data.get("risk_profile") or _T.SIZING.MANDATE_DEFAULT).strip().lower()
    risk_profile_notice = ""
    legacy = _T.SIZING.MANDATE_LEGACY_ALIASES.get(risk_profile)
    if legacy:
        risk_profile_notice = (f"The {risk_profile.title()} mandate is no longer offered: it sized "
                               f"exactly like {legacy.title()}. This scan used {legacy.title()}.")
        risk_profile = legacy
    if risk_profile not in RISK_PROFILE_ALLOCATION_SCALE:
        offered = " or ".join(sorted(RISK_PROFILE_ALLOCATION_SCALE))
        return jsonify({"error": f"risk_profile must be {offered}"}), 400

    params = {
        "market": market,
        "capital": capital,
        "scan_type": scan_type,
        "max_positions": max_positions,
        "diversify": bool(data.get("diversify", True)),
        "custom_tickers": custom_tickers,
        "risk_profile": risk_profile,
        "risk_profile_notice": risk_profile_notice,
        "filters": filters,
        # [PROVIDER-RESILIENCE] Safe-mode retry: minimal concurrency to recover
        # from a provider throttle without changing the universe selection logic.
        "safe_mode": bool(data.get("safe_mode", False)),
    }

    scan_key = _scan_params_key(params)

    _prune_jobs()
    with _jobs_lock:
        # A market scan is the heaviest thing this process does: hundreds of
        # provider calls throttled to 3 workers. Nothing here used to stop two
        # of them running at once, so a double-click (or a reload mid-scan)
        # started a second full scan that fought the first for the same
        # yfinance quota — both then ran at roughly a third of normal
        # throughput and neither finished in the advertised time.
        running = [
            (jid, job) for jid, job in _jobs.items()
            if job.get("type") == "stock-picks" and job.get("status") == "running"
        ]

        # Same scan, already in flight → hand back the running job so both
        # clicks share one result (matches /api/analyze coalescing).
        for existing_id, existing in running:
            if existing.get("scan_key") == scan_key:
                return jsonify({
                    "job_id": existing_id,
                    "status": "running",
                    "coalesced": True,
                }), 202

        # A *different* scan while one is in flight. Refusing is the honest
        # answer: admitting it would slow both runs rather than parallelise
        # them, because they contend for one rate-limited provider.
        # [F07] Count live WORKERS, not "running" statuses: a cancelled job's
        # thread is still scanning, so its slot is not free yet.
        _now = time.time()
        active_workers = [
            (jid, job) for jid, job in _jobs.items()
            if job.get("type") == "stock-picks" and _worker_slot_held(job, _now)
        ]
        if len(active_workers) >= MAX_CONCURRENT_SCANS:
            busy_id, busy = active_workers[0]
            elapsed = round(time.time() - float(busy.get("started_at") or time.time()), 1)
            return jsonify({
                "error": (
                    "A market scan is already running "
                    f"({busy.get('scan_type') or 'scan'}, started {busy.get('started')}). "
                    "Wait for it to finish, then start the new one — running two "
                    "scans at once makes both slower, not faster."
                ),
                "running_job_id": busy_id,
                "running_elapsed_seconds": elapsed,
                # The slot is releasable: the client can cancel the running
                # scan instead of waiting out the runtime budget.
                "cancel_url": f"/api/jobs/{busy_id}/cancel",
            }), 409

        _jobs[job_id] = {
            "type": "stock-picks",
            "status": "running",
            "progress": 0,
            "message": "Initializing scanner…",
            "result": None,
            "error": None,
            "scan_key": scan_key,
            "scan_type": scan_type,
            "started": _now_iso(),
            "started_at": time.time(),
            # [F07] Held until _run_stock_picks actually returns.
            "worker_active": True,
        }

    thread = threading.Thread(target=_run_stock_picks, args=(job_id, params), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "running"}), 202


DEEP_ACTIONABLE_SIGNALS = {"STRONG_BUY", "BUY", "ACCUMULATE"}

# Client-mandate scaling applied to every FUNDED pick after deep confirmation.
# The engine's own risk budget is the ceiling: no mandate adds capital beyond
# it, "conservative" takes a haircut so a cautious mandate visibly changes the
# position list. Values are multipliers on the reconciled
# allocation; shares / position_pct are re-derived atomically via
# SP_ELITE_PRO.apply_allocation so the three fields cannot drift.
RISK_PROFILE_ALLOCATION_SCALE = _T.SIZING.MANDATE_SCALE
# [F14] Only mandates that change sizing are offered; "aggressive" (identical to
# balanced) was retired. See SIZING.MANDATE_LEGACY_ALIASES.
RISK_PROFILE_NOTE = {
    "conservative": (f"Suggested sizes cut to {_T.SIZING.MANDATE_SCALE['conservative']:.0%} "
                     "of the engine's risk budget."),
    "balanced": "Positions sized at the engine's own risk budget.",
}
RISK_PROFILE_NARRATIVE_TOLERANCE = {
    "conservative": "conservative",
    "balanced": "moderate",
}


def _apply_risk_profile(recs: List[Any], profile: str, capital: float,
                        apply_allocation_fn: Any) -> Dict[str, Any]:
    """Scale funded allocations by the client mandate.

    Pure with respect to I/O; mutates the recommendation objects through
    ``apply_allocation_fn(rec, allocation, capital)`` (SP_ELITE_PRO.apply_allocation
    or a test double) and returns an audit block::

        {"profile": str, "multiplier": float, "adjusted": [{ticker, before, after}]}
    """
    profile = str(profile or "balanced").lower()
    mult = RISK_PROFILE_ALLOCATION_SCALE.get(profile, 1.0)
    adjusted: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for rec in recs or []:
        before = float(getattr(rec, "allocation", 0.0) or 0.0)
        if before <= 0 or mult >= 1.0:
            continue
        after = float(apply_allocation_fn(rec, round(before * mult, 2), capital) or 0.0)
        ticker = getattr(rec, "ticker", "")
        adjusted.append({"ticker": ticker, "before": before, "after": after,
                         "dropped": after <= 0})
        if after <= 0:
            # apply_allocation returns 0 when the scaled figure no longer buys a
            # whole share. That is not a trim — the position is gone. Say so on
            # the record (the UI keys its "why is this unfunded" line off it)
            # instead of reporting it in the same list as a haircut.
            dropped.append({"ticker": ticker, "before": before,
                            "entry_price": float(getattr(rec, "entry_price", 0.0) or 0.0)})
            rec.mandate_unfunded = True
            rec.mandate_unfunded_reason = (
                f"{profile.capitalize()} mandate scales this to "
                f"${before * mult:,.0f}, which does not buy a whole share at "
                f"${float(getattr(rec, 'entry_price', 0.0) or 0.0):,.2f}."
            )
    return {
        "profile": profile,
        "multiplier": mult,
        "adjusted": adjusted,
        # Picks the mandate removed from the funded list entirely.
        "dropped": dropped,
        "note": RISK_PROFILE_NOTE.get(profile, ""),
        # True when this mandate leaves sizing exactly as the engine set it, so
        # the UI can say "no change" instead of implying an effect it had not.
        "sizing_unchanged": mult >= 1.0,
    }
def _sync_decision_sizing(recs: List[Any], audit: Dict[str, Any]) -> None:
    """Keep each frozen Decision's sizing block equal to the mandate-scaled size.

    The Decision (action, label, evidence) is the model's call and is what the
    ledger recorded, so the mandate never re-derives it. Only the dollars it
    quotes are client sizing, and those must match the row that renders them.
    """
    touched = {row.get("ticker") for row in (audit or {}).get("adjusted", [])}
    for rec in recs or []:
        decision = getattr(rec, "decision", None)
        if getattr(rec, "ticker", None) not in touched or not isinstance(decision, dict):
            continue
        sizing = dict(decision.get("sizing") or {})
        sizing["allocation_usd"] = float(getattr(rec, "allocation", 0.0) or 0.0)
        sizing["shares"] = getattr(rec, "shares", 0) or 0
        sizing["mandate"] = (audit or {}).get("profile")
        if getattr(rec, "mandate_unfunded", False):
            sizing["mandate_unfunded"] = True
        decision["sizing"] = sizing


# Final actions that must NEVER carry a funded position in the UI/exports.
_NON_FUNDED_ACTIONS = {"WATCHLIST", "HOLD", "HOLD_EXISTING", "AVOID", "INVALID", "REDUCE", "SELL"}

# Canonical display labels for each decision-core FinalAction. The frontend owns
# colour/badge styling; this only normalises the human-readable label so the UI,
# API, and exports all speak the same vocabulary.
_DISPLAY_ACTION_MAP = {
    "STRONG_BUY": "STRONG BUY",
    "BUY": "BUY",
    "ACCUMULATE": "ACCUMULATE",
    "WATCHLIST": "WATCHLIST",
    "HOLD_EXISTING": "HOLD EXISTING",
    "HOLD": "HOLD EXISTING",
    "AVOID": "AVOID",
    "INVALID": "INVALID",
    "REDUCE": "REDUCE",
    "SELL": "SELL",
}


def _display_action(final_action: Optional[str], signal: Optional[str] = "") -> str:
    """Normalise a final_action (preferred) or legacy signal into a display label."""
    key = str(final_action or signal or "HOLD").upper().replace(" ", "_")
    return _DISPLAY_ACTION_MAP.get(key, key.replace("_", " ").title())


# [DATA-RELIABILITY] Validation-status taxonomy for the "Scanner Pick Not
# Confirmed" watchlist. Keys mirror engine.data_status.DataStatus values.
_VALIDATION_STATUS_LABELS = {
    "ok": "Confirmed",
    "invalid_ticker": "Invalid ticker",
    "insufficient_history": "Insufficient history",
    "provider_rate_limited": "Provider rate-limited",
    "provider_data_unavailable": "Data unavailable / provider issue",
    "deep_confirmation_failed": "Deep confirmation failed",
    "not_independently_confirmed": "Not independently confirmed",
}
_DATA_FAILURE_STATUSES = {
    "insufficient_history", "provider_rate_limited", "provider_data_unavailable",
    "deep_confirmation_failed", "not_independently_confirmed",
}


def _validation_status_label(status: Optional[str]) -> str:
    key = str(status or "").strip().lower()
    return _VALIDATION_STATUS_LABELS.get(key, key.replace("_", " ").title() or "Unknown")



def _first_numeric(*values: Any) -> Optional[float]:
    for value in values:
        try:
            if value is None:
                continue
            number = float(value)
            if number > 0:
                return number
        except (TypeError, ValueError):
            continue
    return None


# [A13] Scan stages: progress range and the label the page shows.
_SCAN_STAGES = {
    "selecting": (15, 20, "Choosing the universe"),
    "analysing": (20, 80, "Analysing names"),
    "book": (80, 84, "Applying the book rules"),
    "research_check": (84, 90, "Running the Research check"),
}


_TERMINAL_LABELS = {"complete": "Complete", "failed": "Failed", "cancelled": "Cancelled"}
# A finished stage is named, not narrated ("Book rules", not "Applying the book rules").
_STAGE_NOUNS = {"selecting": "Universe", "analysing": "Analysis", "book": "Book rules",
                "research_check": "Research check"}
_TERMINAL_WHY = {"failed": "the scan failed", "cancelled": "the scan was cancelled"}


def _plain_why(why: str, decision: Dict[str, Any], funded: bool) -> str:
    """[P5] One plain binding reason for the pick row. The decision core's trace
    ("Composite score 63.8 → candidate ACCUMULATE") becomes a sentence from the
    score bands in config; the full trace stays under Detail."""
    if "→" not in (why or ""):
        return why
    decision = decision or {}
    blocker = decision.get("primary_blocker") or {}
    if not funded and blocker.get("text"):
        return str(blocker["text"])
    S = _T.SCORE
    bands = {"strong buy": (S.EXCELLENT, None), "buy": (S.GOOD, S.EXCELLENT), "accumulate": (S.ACCUMULATE, S.GOOD)}
    label = str(decision.get("label") or "")
    score = decision.get("score")
    if isinstance(score, (int, float)) and label.lower() in bands:
        lo, hi = bands[label.lower()]
        k = S.TO_100                      # D3: one scale on the page, out of 100
        span = f"{lo * k:g} to {hi * k:g}" if hi is not None else f"{lo * k:g} or more"
        text = f"Score {score:.0f} of 100 is in the {label} band ({span})"
        return text + ("; it passed every screening and risk rule" if funded else "")
    return why.replace(" → ", ": ")


_SUB_SCORES = ("technical", "fundamental", "momentum", "quality", "value")


def _stock_picks_table(quanta: Any, picks: List[Dict[str, Any]], final_recs: List[Any],
                       max_positions: int) -> List[Dict[str, Any]]:
    """[Picks table] Every name that reached the book, ranked by score, each
    with its one Decision and either its position or the one reason it is not
    funded. Published rows supply what Research and the mandate decided; the
    engine supplies why every other name stopped (a pending deal, a selection
    limit, or the first book rule it missed)."""
    entrants = list(getattr(quanta, "_book_entrants", []) or [])
    if not entrants:
        return []
    published = {str(p.get("ticker")): p for p in picks}
    final_by = {str(getattr(r, "ticker", "")): r for r in final_recs}
    avoid = {str(e.get("ticker")): e for e in (getattr(quanta, "_event_rejections", []) or [])}

    def _call(name, *a, **kw):
        fn = getattr(quanta, name, None)
        return (fn(*a, **kw) or []) if callable(fn) else []
    pnf = {d["ticker"]: d for d in _call("passed_not_funded", set(published))}
    gates = {g["ticker"]: g for g in _call("closest_to_qualifying", set(published), uncapped=True)}
    banned = set(_T.BOOK.BANNED_GRADES)

    def num(v) -> float:
        try:
            return float(v or 0.0)
        except (TypeError, ValueError):
            return 0.0

    rows: List[Dict[str, Any]] = []
    ordered = sorted(entrants, key=lambda r: (-num(getattr(r, "overall_score", 0)), str(getattr(r, "ticker", ""))))
    for rank, rec in enumerate(ordered, start=1):
        t = str(rec.ticker)
        pub = published.get(t)
        src = pub if pub is not None else _stock_analysis_to_dict(rec)
        bind = src.get("binding_levels") or src.get("scanner_levels") or {}
        price = num((src.get("technical_indicators") or {}).get("current_price")) or num(src.get("entry_price"))
        target = num(bind.get("target")) or num(src.get("price_target"))
        decision = src.get("decision") or {}
        row: Dict[str, Any] = {
            "rank": rank, "ticker": t, "company": src.get("company") or t, "sector": src.get("sector") or "",
            "price": price, "price_session": str(src.get("market_timestamp") or "")[:10] or None,
            "signal": str(decision.get("label") or src.get("display_action") or src.get("final_action")
                          or src.get("signal") or ""),
            "entry": num(bind.get("entry")) or num(src.get("entry_price")),
            "entry_low": bind.get("entry_zone_low"), "entry_high": bind.get("entry_zone_high"),
            "target": target,
            "upside_pct": round((target / price - 1.0) * 100.0, 2) if price > 0 and target > 0 else None,
            "stop": num(bind.get("stop")) or num(src.get("stop_loss")),
            "risk_reward": num(bind.get("risk_reward")) or num(src.get("risk_reward_ratio")),
            "levels_label": bind.get("label"), "horizon_days": bind.get("horizon_days"),
            "price_basis": bind.get("price_basis"),
            "score": round(num(src.get("overall_score")), 2), "risk_grade": src.get("risk_grade"),
            "conviction": src.get("conviction"),
            "scores": {k: round(num(src.get(f"{k}_score")), 1) for k in _SUB_SCORES},
            "research_evidence": src.get("research_evidence"),
            "evidence": (decision.get("evidence") or {}).get("status"),
            "why": src.get("why") or "",
            "allocation": 0.0, "shares": 0, "status": "", "reason": "", "gate": None, "capital_needed": None,
        }
        # [Why picked] What the scan measured for this name, both sides, sourced.
        # Funded and watch rows also read the Research result the scan cached.
        research = None
        rec_dict = src if pub is None else _stock_analysis_to_dict(rec)
        try:
            research = _research_cache.get(t, _T.RESEARCH.MODE) if pub is not None else None
            row["why_picked"] = _why_picked.build(rec_dict, research)
        except Exception as exc:  # noqa: BLE001 - a missing explanation must not drop the row
            logger.warning(f"why-picked for {t} failed: {exc}")
            row["why_picked"] = None
        row["is_fund"] = bool(getattr(rec, "is_fund", False))
        # [Analysis cards] What a pick's card states: setup, risk gauge inputs,
        # the trend lines its thesis rests on, and the earnings clock.
        ti = getattr(rec, "technical_indicators", None)
        rm = getattr(rec, "risk_metrics", None)
        days = getattr(rec, "days_to_earnings", None)
        row.update({
            "setup": str(getattr(rec, "setup_type", "") or ""),
            "volatility": round(num(getattr(rm, "volatility", 0)), 4) or None,
            "beta": round(num(getattr(rm, "beta", 0)), 2) or None,
            "sma_50": round(num(getattr(ti, "sma_50", 0)), 2) or None,
            "sma_200": round(num(getattr(ti, "sma_200", 0)), 2) or None,
            "atr": round(num(getattr(ti, "atr", 0)), 2) or None,
            "trailing_stop": round(num(src.get("trailing_stop")), 2) or None,
            "days_to_earnings": int(days) if isinstance(days, (int, float)) and days >= 0 else None,
        })
        # [Analysis] Technicals for the gauges, the year of closes for the chart,
        # insider flow and the analyst split, each as measured (None = not).
        row["tech"] = {k: (round(num(getattr(ti, k, 0)), 4) or None) for k in _TECH_FIELDS}
        row["history"] = _close_history(getattr(rec, "_chart_hist", None) if getattr(rec, "_chart_hist", None) is not None
                                        else getattr(rec, "_hist_cache", None))
        row["insider"] = {"sentiment": str(getattr(rec, "insider_sentiment", "") or ""),
                          "buys": int(num(getattr(rec, "insider_buys_90d", 0))),
                          "sells": int(num(getattr(rec, "insider_sells_90d", 0))),
                          "net_value": round(num(getattr(rec, "insider_net_value_90d", 0)), 0),
                          "note": str(getattr(rec, "insider_note", "") or "")}
        mi = getattr(research, "market_intel", None) if research is not None else None
        split = [int(num(getattr(mi, k, 0))) for k in ("buy_ratings", "hold_ratings", "sell_ratings")] if mi else []
        row["analyst_split"] = ({"buy": split[0], "hold": split[1], "sell": split[2]} if sum(split) else None)
        # [Analysis] The scan's own consensus read: the same source the "why"
        # text quotes, so the analyst panel and the reasons never disagree.
        row["analyst"] = {"rating": str(getattr(rec, "analyst_rating", "") or ""),
                          "count": int(num(getattr(rec, "analyst_count", 0))),
                          "rec_mean": round(num(getattr(rec, "analyst_rec_mean", 0)), 2) or None,
                          "target": round(num(getattr(rec, "analyst_target", 0)), 2) or None}
        # [Calls] A name the corporate-event check could not read carries what clears it.
        # A Research downgrade on reward/risk carries the prices that would clear it.
        row["fix"] = (str((getattr(rec, "event_check", None) or {}).get("fix") or "")
                      or str(bind.get("remedy") or "")) or None
        if pub is not None and pub.get("is_funded"):
            row.update(status="funded", allocation=num(pub.get("allocation_after", pub.get("allocation"))),
                       shares=int(num(pub.get("shares"))))
            if not row["why"]:
                row["why"] = "Passed every screening and risk rule"
        elif pub is not None:
            row.update(status="watch", reason=str(pub.get("why") or ""))
        elif t in avoid:
            row.update(status="avoid", signal="AVOID", reason=str(avoid[t].get("reason") or ""))
        elif t in final_by:
            fr = final_by[t]
            if num(getattr(fr, "allocation", 0)) > 0:
                row.update(status="not_funded", reason=f"Book already holds its {int(max_positions)} positions")
            else:
                reasons = [str(x) for x in (getattr(fr, "final_action_reasons", None) or []) if x]
                row.update(status="watch", reason=_plain_why(reasons[-1] if reasons else "",
                                                             getattr(fr, "decision", None) or {}, False))
        elif t in pnf:
            d = pnf[t]
            row.update(status="not_funded", reason=str(d.get("text") or ""), capital_needed=d.get("capital_needed"))
        elif t in gates:
            row.update(status="below_rule", gate=gates[t])
        elif str(rec.risk_grade or "") in banned:
            row.update(status="below_rule", gate={"gate": "risk_grade", "now": rec.risk_grade,
                                                  "needed": sorted(banned), "unit": "excluded grades"})
        else:
            row.update(status="not_funded", reason="Not selected for this book")
        try:
            row["valuation"] = _valuation_view.build(rec_dict, row, research)
        except Exception as exc:  # noqa: BLE001 - optional evidence
            logger.warning(f"valuation view for {t} failed: {exc}")
            row["valuation"] = None
        rows.append(row)
    order = {k: i for i, k in enumerate(_STATUS_ORDER)}
    rows.sort(key=lambda r: (order.get(r["status"], len(order)), -num(r.get("score")), r["ticker"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


# [Picks] Internal outcome order (the page shows calls; see _picks_view).
_STATUS_ORDER = ("funded", "watch", "not_funded", "below_rule", "avoid")
# The engine's final action as QUANTA's call. STRONG_BUY folds into Buy until
# the model is validated (owner decision Q4); anything else is not a pick.
_CALL_OF_ACTION = {"STRONG_BUY": "buy", "BUY": "buy", "ACCUMULATE": "accumulate", "WATCHLIST": "watchlist"}
_CALL_ORDER = ("buy", "accumulate", "watchlist")


def _action_key(text: Any) -> str:
    return str(text or "").strip().upper().replace(" ", "_").replace("-", "_")


def _current_holdings() -> Optional[Dict[str, Any]]:
    """[B-89] The broker positions My Portfolio has loaded (read-only), or None."""
    try:
        from portfolio_routes import current_holdings
    except Exception:  # noqa: BLE001 - the portfolio blueprint is optional
        return None
    return current_holdings()


def _apply_current_holdings(result: Dict[str, Any], capital: float, market: str, *,
                            holdings: Any = ..., config: Any = None) -> None:
    """[B-89] Make the Picks rows aware of what the account already holds.

    A row in a held name carries ``held`` (quantity, value, share of the scan
    capital). A sized row counts the holding against the per-name cap the book
    uses (``config.effective_max_position_pct``): it keeps only the shares that
    fit the room left, and gets no new money once the holding fills the cap or
    its value is unknown. Selection and the engine's funding rules are not
    touched. Without loaded positions nothing changes and the result says the
    holdings were not checked; India listings are not matched against US
    broker positions.
    """
    rows = result.get("stock_picks") or []
    if str(market).upper() != "US":
        result["holdings_check"] = {"state": "not_applicable",
                                    "text": "Your broker holdings are US listings; not checked for this market."}
        return
    snap = _current_holdings() if holdings is ... else holdings
    if not snap:
        result["holdings_check"] = {"state": "not_checked",
                                    "text": "Holdings not checked: load your broker account in My Portfolio "
                                            "to size picks around what you already own."}
        return
    positions = {str(k).upper().replace(".", "-"): v for k, v in (snap.get("positions") or {}).items()}
    cap_pct = float(config.effective_max_position_pct(float(capital))) if config is not None else None
    cap_dollars = float(capital) * cap_pct if cap_pct is not None else None
    held_count = 0
    for r in rows:
        h = positions.get(str(r.get("ticker") or "").upper().replace(".", "-"))
        if not h:
            continue
        held_count += 1
        mv = h.get("market_value")
        mv = float(mv) if isinstance(mv, (int, float)) and math.isfinite(float(mv)) else None
        r["held"] = {"quantity": h.get("quantity"), "market_value": mv,
                     "pct_of_capital": round(mv / float(capital) * 100, 1) if mv is not None and capital else None}
        cap = r.get("capital") or {}
        if cap.get("kind") != "sized":
            continue
        price = float(r.get("price") or 0)
        if mv is None or price <= 0 or cap_dollars is None:
            r.update(allocation=0.0, shares=0)
            r["capital"] = {"kind": "held", "text": "Already held, value unknown: not sized on top of it"}
            continue
        room = cap_dollars - mv
        shares = int(min(float(r.get("shares") or 0), max(0.0, room) // price))
        if shares <= 0:
            r.update(allocation=0.0, shares=0)
            r["capital"] = {"kind": "held", "text": f"Already held: ${mv:,.0f} reaches the {cap_pct:.0%} "
                                                    "per-name cap, so no new money is suggested"}
        elif shares < int(r.get("shares") or 0):
            dollars = round(shares * price, 2)
            r.update(allocation=dollars, shares=shares)
            r["capital"] = {"kind": "sized", "dollars": dollars, "shares": shares, "reduced_for_holding": True}
    result["holdings_check"] = {"state": "checked", "retrieved_at": snap.get("retrieved_at"), "held": held_count}


def _picks_view(rows: List[Dict[str, Any]], quanta: Any) -> Dict[str, Any]:
    """[Calls] Split the table rows into QUANTA's calls and the names that are
    not picks.

    * A pick is Buy, Accumulate or Watchlist. Its call comes from the engine's
      final action and never from whether the capital fits; a Watchlist row
      (deep-confirm downgrade, corporate-event check unavailable) keeps its
      reason and, when the check failed, the fix.
    * Capital fit is its own field: suggested dollars and shares when the name
      fits every cap, the capital one share needs when it does not, or the
      selection limit that kept it unsized.
    * A name that missed a book rule is "screened out"; a pending acquisition
      or delisting is "excluded for safety" with its source and date.
    * Several share classes of one issuer (the event check's SEC CIK) are one
      row: the class chosen after the Research check; the others are named on it.
      When the Research check held the chosen class back, the others are held
      back too (fail closed) and say so on the row.
    """
    skips = dict(getattr(quanta, "_same_issuer_skips", {}) or {})
    notes = dict(getattr(quanta, "_selection_skips", {}) or {})
    events = {str(e.get("ticker")): e for e in (getattr(quanta, "_event_rejections", []) or [])}
    picks: List[Dict[str, Any]] = []
    screened: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    twins: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        status = r.get("status")
        if status == "avoid":
            ev = events.get(r["ticker"]) or {}
            check = ev.get("event_check") or {}
            excluded.append({"ticker": r["ticker"], "company": r.get("company"), "reason": r.get("reason") or ev.get("reason") or "",
                             "source": check.get("source") or "SEC EDGAR", "filed": check.get("filed"), "url": check.get("url")})
            continue
        if status == "below_rule":
            screened.append({"ticker": r["ticker"], "company": r.get("company"), "gate": r.get("gate")})
            continue
        if r["ticker"] in skips and status != "funded":
            note = notes.get(r["ticker"]) or {}
            twin = {"ticker": r["ticker"], "company": r.get("company")}
            if note.get("held_by_research"):
                twin.update(held_back=True, reason=note.get("text") or "")
            twins.setdefault(skips[r["ticker"]], []).append(twin)
            continue
        if status == "watch":
            call = "watchlist"
        else:
            call = _CALL_OF_ACTION.get(_action_key(r.get("signal")), "")
            if call not in ("buy", "accumulate"):
                # Funded or capital-limited names carry a buy-tier action; anything
                # else here is not a call QUANTA makes.
                screened.append({"ticker": r["ticker"], "company": r.get("company"), "gate": None,
                                 "reason": r.get("reason") or "No Buy or Accumulate call"})
                continue
        r["call"] = call
        if call != "watchlist":
            r.pop("fix", None)
        if status == "funded":
            r["capital"] = {"kind": "sized", "dollars": r.get("allocation"), "shares": r.get("shares")}
        elif call == "watchlist":
            r["capital"] = {"kind": "none", "text": "No new money on a Watchlist call"}
        elif r.get("capital_needed"):
            r["capital"] = {"kind": "needs", "needed": r.get("capital_needed"),
                            "text": f"Needs about ${float(r['capital_needed']):,.0f} for one share within the position cap"}
        else:
            r["capital"] = {"kind": "not_sized", "text": r.get("reason") or "Not sized for this book"}
        picks.append(r)
    for r in picks:
        if r["ticker"] in twins:
            r["other_classes"] = twins[r["ticker"]]
    order = {k: i for i, k in enumerate(_CALL_ORDER)}
    picks.sort(key=lambda r: (order.get(r["call"], len(order)), -float(r.get("score") or 0), r["ticker"]))
    for i, r in enumerate(picks, start=1):
        r["rank"] = i
    return {"picks": picks, "screened_out": screened, "excluded_for_safety": excluded}
_TECH_FIELDS = ("rsi", "sma_20", "sma_50", "sma_200", "volume_ratio", "atr", "atr_percent",
                "momentum_1m", "momentum_3m", "high_52w", "low_52w")


def _close_history(hist: Any) -> Optional[Dict[str, Any]]:
    """The year of daily closes the scan analysed, for the Analysis chart."""
    try:
        closes = hist["Close"].dropna()
    except Exception:  # noqa: BLE001 - no history cached for this name
        return None
    if len(closes) < 2:
        return None
    return {"dates": [str(d)[:10] for d in closes.index],
            "close": [round(float(c), 2) for c in closes.values]}


def _terminal_stage(outcome: str, stage_log: Dict[str, Dict[str, int]],
                    notes: Optional[Dict[str, str]] = None,
                    elapsed_s: Optional[float] = None) -> Dict[str, Any]:
    """[I4][P1] The stage list at a terminal state: every stage done (with its
    final counts), failed (where a failed or cancelled scan was, with why), or
    skipped with the reason. No stage is left running after the job ends."""
    reached = [k for k in _SCAN_STAGES if k in stage_log]
    last = reached[-1] if reached else None
    rows = []
    for key, (_lo, _hi, label) in _SCAN_STAGES.items():
        log = stage_log.get(key)
        note = (notes or {}).get(key, "")
        if log is None:
            state = "skipped"
            if not note.startswith("skipped: "):
                note = f"skipped: {_TERMINAL_WHY.get(outcome, 'not needed for this scan')}"
        elif outcome != "complete" and key == last:
            state = "failed"
            at = f" at {log['done']} of {log['total']}" if log["total"] else ""
            note = f"failed: {_TERMINAL_WHY.get(outcome, 'the scan stopped')}{at}"
        else:
            state = "done"
            if note.startswith("skipped: "):
                note = ""
        rows.append({"key": key, "label": _STAGE_NOUNS.get(key, label), "state": state,
                     "done": log["done"] if log else None, "total": log["total"] if log else None,
                     "note": note, **({"selection": log["selection"]} if log and log.get("selection") else {})})
    return {"key": outcome, "label": _TERMINAL_LABELS[outcome], "terminal": True,
            "stages": rows, "elapsed_s": elapsed_s}


def _data_dates(rows: List[Any], completed: str) -> Dict[str, Any]:
    """[A13] The three clocks of a scan, kept apart: the daily-bar session
    each analysed name was read on, the provider's quote time, and when the
    scan finished."""
    sessions: Dict[str, int] = {}
    quotes: List[str] = []
    for r in rows or []:
        ts = str(getattr(r, "market_timestamp", "") or "")
        if ts:
            day = ts[:10]
            sessions[day] = sessions.get(day, 0) + 1
        q = str(getattr(r, "quote_time", "") or "")
        if q:
            quotes.append(q)
    return {
        "bar_session": max(sessions) if sessions else None,
        "bar_sessions": dict(sorted(sessions.items(), reverse=True)),
        "quote_time": max(quotes) if quotes else None,
        "scan_completed": completed,
    }


def _run_stock_picks(job_id: str, params: dict):
    """Background worker for SP_ELITE_PRO scan."""
    stage_log: Dict[str, Dict[str, int]] = {}      # [I4] every stage reached, with its last counts
    _scan_started = time.time()
    try:
        _update_job(job_id, progress=5, message="Loading QUANTA Elite engine…")

        # Import here to avoid slow startup
        import SP_ELITE_PRO as sp

        _update_job(job_id, progress=10, message="Initializing trading system…")

        quanta = sp.QuantaEliteTradingSystem(
            capital=params["capital"],
            market=params["market"]
        )
        quanta.user_filters = dict(params.get("filters") or {})
        # [A13] Stage progress with counts and elapsed time.
        _scan_started = time.time()

        def _on_progress(stage: str, done: int, total: int) -> None:
            lo, hi, label = _SCAN_STAGES.get(stage, (None, None, stage))
            if lo is None:
                return
            pct = lo + (hi - lo) * (done / total) if total else lo
            elapsed = round(time.time() - _scan_started, 1)
            msg = (f"{label}: {done} of {total} · {elapsed:.0f} s elapsed" if total
                   else f"{label} · {elapsed:.0f} s elapsed")
            stage_log[stage] = {"done": int(done), "total": int(total)}
            stage_rec = {"key": stage, "label": label, "done": int(done),
                         "total": int(total), "elapsed_s": elapsed}
            # [Picks revamp] Before analysis starts the page names the source
            # size, the kept size, the method and any fallback.
            if stage == "selecting" and total:
                stage_rec["selection"] = _safe_serialize(dict(getattr(quanta, "_universe_selection", None) or {}))
                stage_log[stage]["selection"] = stage_rec["selection"]
            elif (stage_log.get("selecting") or {}).get("selection"):
                stage_rec["selection"] = stage_log["selecting"]["selection"]
            _update_job(job_id, progress=int(pct), message=msg, stage=stage_rec,
                        stage_log=dict(stage_log))

        quanta._progress_cb = _on_progress
        _vix_source = str(getattr(quanta.market_regime, "vix_source", "quote") or "quote")
        regime = {
            "type": quanta.market_regime.regime_type,
            # A placeholder is not a reading: no number is published for it.
            "vix": (None if _vix_source == "default"
                    else round(quanta.market_regime.vix_level, 1)),
            "vix_source": _vix_source,
            "vix_is_proxy": bool(getattr(quanta.market_regime, "vix_is_proxy", False)),
            "volatility": quanta.market_regime.volatility_regime,
            "exposure": round(quanta.market_regime.recommended_exposure * 100),
        }

        _update_job(job_id, progress=15, message=(
            f"Market regime: {regime['type']} | VIX: "
            + (f"{regime['vix']}" if regime['vix'] is not None else "unavailable")
            + (" (estimate)" if regime['vix_is_proxy'] and regime['vix'] is not None else "")))

        # Determine tickers for custom scan
        custom = params.get("custom_tickers")
        if custom and isinstance(custom, str):
            custom = [t.strip().upper() for t in custom.split(",") if t.strip()]

        if _job_is_cancelled(job_id):
            logger.info("Job %s cancelled before market scan — stopping.", job_id)
            return
        _update_job(job_id, progress=20, message="Scanning market…")
        scan_results = quanta.scan_market(
            scan_type=params["scan_type"],
            custom_tickers=custom if custom else None,
            safe_mode=bool(params.get("safe_mode")),
        )

        if _job_is_cancelled(job_id):
            logger.info("Job %s cancelled after market scan — stopping.", job_id)
            return
        _update_job(job_id, progress=80, message="Ranking and filtering…")

        recs = quanta.get_top_recommendations(
            max_positions=params["max_positions"],
            diversify_sectors=params["diversify"],
        )

        if _job_is_cancelled(job_id):
            logger.info("Job %s cancelled before deep confirmation — stopping.", job_id)
            return
        _update_job(job_id, progress=86, message="Reconciling picks with Deep Analysis…")
        # ------------------------------------------------------------------
        # [DEEP-CONFIRM] SP_ELITE_PRO.get_top_recommendations() already
        # reconciled every published pick against the authoritative
        # TradingAnalysis_ELITE deep analyzer (via _deep_confirm_final_picks).
        # The reconciled verdict is written back onto each pick
        # (final_action + reduced allocation) and the full audit trail lives on
        # quanta._deep_confirm_records. We CONSUME that single source of truth
        # here instead of running a second, divergent confirmation pass — the
        # old per-pick re-analysis doubled latency and could contradict the
        # engine's own reconciliation.
        # ------------------------------------------------------------------
        dc_enabled = bool(getattr(quanta, "_deep_confirm_enabled", True))
        dc_records = getattr(quanta, "_deep_confirm_records", []) or []
        dc_by_ticker = {
            str(r.get("ticker")): r for r in dc_records if isinstance(r, dict)
        }

        deep_confirm_summary: List[Dict[str, Any]] = []
        analyzer_disagreement_watchlist: List[Dict[str, Any]] = []
        for rec in recs:
            ticker = str(getattr(rec, "ticker", "") or "")
            record = dc_by_ticker.get(ticker, {})
            final_action = str(
                getattr(rec, "final_action", "") or getattr(rec, "signal", "") or "HOLD"
            ).upper().replace(" ", "_")
            scanner_action = str(record.get("scanner_action") or final_action).upper().replace(" ", "_")
            deep_action = str(record.get("deep_action") or "").upper().replace(" ", "_")
            downgraded = bool(record.get("downgraded", False))
            alloc_after = record.get("allocation_after")
            if alloc_after is None:
                alloc_after = float(getattr(rec, "allocation", 0.0) or 0.0)
            alloc_before = record.get("allocation_before")
            if alloc_before is None:
                alloc_before = alloc_after
            reason = record.get("reason") or ""
            # [DATA-RELIABILITY] Distinguish a true bearish disagreement from a
            # data/confirmation failure. A data failure is NOT a disagreement and
            # NOT an invalid ticker — it is "not independently confirmed".
            data_status = str(
                record.get("data_status")
                or getattr(rec, "data_status", "")
                or ""
            ).strip().lower()
            is_data_failure = (
                data_status in _DATA_FAILURE_STATUSES
                or bool(record.get("not_independently_confirmed"))
                or deep_action in ("NOT_CONFIRMED", "ERROR", "SKIPPED")
            )
            # Only a genuinely non-existent symbol is INVALID.
            is_true_invalid = data_status == "invalid_ticker" or deep_action == "INVALID"
            not_confirmed = is_data_failure
            # Stable identity of what blocked the pick. The UI keys the
            # "valid again when" advice off these, never off the reason prose.
            if is_true_invalid:
                reason_codes = ["INVALID_TICKER"]
            elif is_data_failure:
                reason_codes = [(data_status or "not_independently_confirmed").upper()]
            else:
                reason_codes = [str(c) for c in (record.get("reason_codes") or [])]

            rec.deep_confirmation = {
                "enabled": dc_enabled,
                "scanner_action": scanner_action,
                "deep_action": deep_action or None,
                "final_action": final_action,
                "downgrade_flag": downgraded,
                "allocation_before": alloc_before,
                "allocation_after": alloc_after,
                "reason": reason,
                "reason_codes": reason_codes,
                "not_independently_confirmed": not_confirmed,
                "data_status": data_status or "ok",
                "validation_status_label": _validation_status_label(data_status or "ok"),
                "deep_mode": record.get("deep_mode", "unknown"),
                "scanner_risk_grade": record.get("scanner_risk_grade", ""),
                "deep_risk_grade": record.get("deep_risk_grade", ""),
            }

            deep_confirm_summary.append({
                "ticker": ticker,
                "company": getattr(rec, "company", ""),
                "scanner_action": scanner_action,
                "deep_action": deep_action or ("SKIPPED" if not dc_enabled else "N/A"),
                "final_action": final_action,
                "downgrade_flag": downgraded,
                # [P5] The table shows one plain reason, as the card does.
                "reason": _plain_why(reason, getattr(rec, "decision", None) or {}, float(alloc_after or 0) > 0),
                "reason_codes": reason_codes,
                "allocation_before": alloc_before,
                "allocation_after": alloc_after,
                "not_independently_confirmed": not_confirmed,
                "data_status": data_status or "ok",
                "validation_status_label": _validation_status_label(data_status or "ok"),
                "deep_mode": record.get("deep_mode", "unknown"),
                "scanner_risk_grade": record.get("scanner_risk_grade", ""),
                "deep_risk_grade": record.get("deep_risk_grade", ""),
            })

            # A pick is a watchlist row when the deep analyzer downgraded it OR
            # the reconciled action is not fundable.
            if downgraded or final_action not in DEEP_ACTIONABLE_SIGNALS:
                scanner_score = _first_numeric(getattr(rec, "overall_score", None))
                # The deep analyzer only emits a comparable score when it could
                # actually validate the name. On a data failure there is none.
                analyzer_score = None
                if is_data_failure:
                    row_kind = "not_confirmed"
                    analyzer_display = "NOT_CONFIRMED"
                    validation_status = _validation_status_label(
                        data_status or "not_independently_confirmed"
                    )
                    score_reason = (
                        "Deep analyzer could not fetch enough historical bars"
                    )
                elif is_true_invalid:
                    row_kind = "invalid"
                    analyzer_display = "INVALID"
                    validation_status = _validation_status_label("invalid_ticker")
                    score_reason = "Ticker failed data-validation gate"
                else:
                    row_kind = "disagreement"
                    analyzer_display = deep_action or None
                    validation_status = "Analyzer disagrees"
                    score_reason = "Deep analyzer reconciled to a non-buy action"

                analyzer_disagreement_watchlist.append({
                    "ticker": ticker,
                    "company": getattr(rec, "company", ""),
                    "sector": getattr(rec, "sector", ""),
                    "reason": reason or "Deep analyzer reconciled this to a non-buy action",
                    "reason_codes": reason_codes,
                    "scanner_signal": scanner_action,
                    "analyzer_signal": analyzer_display,
                    "final_action": final_action,
                    "row_kind": row_kind,
                    "data_status": data_status or "ok",
                    "validation_status": validation_status,
                    "not_independently_confirmed": is_data_failure,
                    "scanner_score": scanner_score,
                    "analyzer_score": analyzer_score,
                    "score_reason": None if analyzer_score is not None else score_reason,
                    "scanner_risk_grade": record.get("scanner_risk_grade", ""),
                    "deep_risk_grade": record.get("deep_risk_grade", ""),
                    "allocation_before": alloc_before,
                    "allocation_after": alloc_after,
                    "entry_price": float(getattr(rec, "entry_price", 0.0) or 0.0),
                    "stop_loss": float(getattr(rec, "stop_loss", 0.0) or 0.0),
                    "price_target": float(getattr(rec, "price_target", 0.0) or 0.0),
                })
                if not dc_enabled:
                    logger.warning("%s shown WITHOUT deep confirmation (developer override)", ticker)
                else:
                    logger.info("%s reconciled to %s by Deep Analysis: %s", ticker, final_action, reason)


        # Keep the final picks (including downgraded WATCHLIST/AVOID with $0
        # allocation) so the UI can render each with its reconciled badge. The
        # frontend separates funded actionable picks from the watchlist using
        # final_action + allocation.
        # [A04] Funded and watch rows are capped independently: a single cap
        # at max_positions used to hide watch explanations behind funded rows.
        def _rec_funded(r) -> bool:
            fa = str(getattr(r, "final_action", "") or "").upper().replace(" ", "_")
            return fa in DEEP_ACTIONABLE_SIGNALS and float(getattr(r, "allocation", 0.0) or 0.0) > 0
        _funded_recs = [r for r in recs if _rec_funded(r)]
        _watch_recs = [r for r in recs if not _rec_funded(r)]
        _max_watch = int(_T.BOOK.MAX_WATCH_ROWS)
        row_caps = {
            "funded_over_cap": max(0, len(_funded_recs) - int(params["max_positions"])),
            "watch_total": len(_watch_recs),
            "watch_published": min(len(_watch_recs), _max_watch),
            "max_watch_rows": _max_watch,
        }
        _final_recs = _funded_recs + _watch_recs      # [Picks table] every final row, before the caps
        recs = _funded_recs[:params["max_positions"]] + _watch_recs[:_max_watch]

        # [CLIENT-MANDATE] Apply the requested risk profile to the reconciled
        # allocations. Runs AFTER deep confirmation so a downgrade to $0 is
        # never scaled back up, and BEFORE serialization so allocation /
        # shares / position_pct all reflect the mandate.
        risk_profile = str(params.get("risk_profile") or "balanced")
        risk_profile_audit = _apply_risk_profile(
            recs, risk_profile, float(params["capital"]), sp.apply_allocation
        )
        if params.get("risk_profile_notice"):
            risk_profile_audit["notice"] = params["risk_profile_notice"]
        _update_job(job_id, progress=90, message=f"Applying {risk_profile} mandate…")
        _sync_decision_sizing(recs, risk_profile_audit)

        # [MANDATE-CONSISTENCY] allocation_after was captured during deep
        # confirmation, i.e. BEFORE the mandate scaled the funded allocations.
        # The pick cards re-read the live figure at serialization time, so a
        # conservative mandate made the confirmation table and the cards print
        # two different dollar amounts for the same name. Re-sync every audit
        # row from the record now that sizing is final.
        _final_alloc = {
            str(getattr(rec, "ticker", "")): float(getattr(rec, "allocation", 0.0) or 0.0)
            for rec in recs
        }
        for _rec in recs:
            _conf = getattr(_rec, "deep_confirmation", None)
            if isinstance(_conf, dict):
                _conf["allocation_after"] = _final_alloc.get(str(getattr(_rec, "ticker", "")), 0.0)
                if getattr(_rec, "mandate_unfunded", False):
                    _conf["mandate_unfunded"] = True
                    _conf["mandate_unfunded_reason"] = getattr(_rec, "mandate_unfunded_reason", "")
        for _row in deep_confirm_summary:
            if _row["ticker"] in _final_alloc:
                _row["allocation_after"] = _final_alloc[_row["ticker"]]
        for _row in analyzer_disagreement_watchlist:
            if _row["ticker"] in _final_alloc:
                _row["allocation_after"] = _final_alloc[_row["ticker"]]

        # Try to generate narratives
        try:
            if sp.ENHANCEMENTS_AVAILABLE:
                profile = sp.InvestorProfile(
                    risk_tolerance=RISK_PROFILE_NARRATIVE_TOLERANCE.get(risk_profile, "moderate"),
                    investment_timeline="medium",
                    experience_level="intermediate",
                    capital=params["capital"]
                )
                for rec in recs:
                    rec.narrative = sp.RecommendationNarrator.create_narrative(rec, profile)
        except Exception:
            pass

        _update_job(job_id, progress=95, message="Serializing results…")

        picks = []
        for rec in recs:
            d = _stock_analysis_to_dict(rec)
            # Attach narrative if present
            confirmation = getattr(rec, "deep_confirmation", None)
            if confirmation:
                d["deep_confirmation"] = _safe_serialize(confirmation)

            # [SOURCE OF TRUTH] final_action is the reconciled, post-deep-confirm
            # action. Every badge/label/export must key off this, NOT the raw
            # scanner signal. We surface a flat, predictable contract so the
            # frontend never has to guess which field is authoritative.
            decision = d.get("decision") or {}
            fa = str(
                decision.get("action") or getattr(rec, "final_action", "")
                or d.get("final_action") or d.get("signal") or "HOLD"
            ).upper().replace(" ", "_")
            d["final_action"] = fa
            d["display_action"] = decision.get("label") or _display_action(fa, d.get("signal"))
            d["is_funded"] = fa in DEEP_ACTIONABLE_SIGNALS and float(d.get("allocation") or 0) > 0
            # [MANDATE] An actionable pick the client mandate sized out of the
            # portfolio. Without this the row shows BUY with $0 and no reason.
            if getattr(rec, "mandate_unfunded", False):
                d["mandate_unfunded"] = True
                d["mandate_unfunded_reason"] = getattr(rec, "mandate_unfunded_reason", "")
            # [WHY] One sentence the pick row can show at a glance. Prefer the
            # deep-confirm reason (it explains a downgrade), then the engine's
            # own reasons, then the committee note.
            _reasons = [str(r) for r in (d.get("final_action_reasons") or []) if r]
            _notes = [str(r) for r in (d.get("investment_committee_notes") or []) if r]
            d["why"] = _plain_why(
                (confirmation or {}).get("reason")
                if (confirmation or {}).get("downgrade_flag") and (confirmation or {}).get("reason")
                else (_reasons[-1] if _reasons else (_notes[0] if _notes else "")),
                decision, bool(d["is_funded"]))
            if confirmation:
                d["scanner_action"] = confirmation.get("scanner_action")
                d["deep_action"] = confirmation.get("deep_action")
                d["deep_mode"] = confirmation.get("deep_mode")
                d["downgrade_flag"] = bool(confirmation.get("downgrade_flag"))
                d["allocation_before"] = confirmation.get("allocation_before")
                # allocation_after is the LIVE reconciled figure: deep-confirm
                # first, then the client mandate. Re-read it from the record so
                # the frontend's "trust allocation_after" rule stays correct.
                d["allocation_after"] = float(d.get("allocation") or 0.0)
                d["deep_confirmation_reason"] = confirmation.get("reason")
                d["deep_confirm_enabled"] = confirmation.get("enabled")
                d["not_independently_confirmed"] = bool(confirmation.get("not_independently_confirmed"))
                d["data_status"] = confirmation.get("data_status") or "ok"
                d["validation_status_label"] = confirmation.get("validation_status_label")

            narrative = getattr(rec, "narrative", None)
            if narrative:
                d["narrative"] = {
                    "headline": getattr(narrative, "headline", ""),
                    "why_now": getattr(narrative, "why_now", ""),
                    "technical_story": getattr(narrative, "technical_story", ""),
                    "fundamental_thesis": getattr(narrative, "fundamental_thesis", ""),
                    "risk_factors": getattr(narrative, "risk_factors", ""),
                    "monitoring_tips": getattr(narrative, "monitoring_tips", ""),
                    "exit_strategy": getattr(narrative, "exit_strategy", ""),
                }
            picks.append(d)

        # [PRE-MOMENTUM] Surface chase-alert and early-momentum watchlists
        # from the QUANTA system instance so the UI can render them as
        # separate buckets (mirrors the terminal display layer).
        try:
            chase_alerts_payload = _safe_serialize(getattr(quanta, "chase_alerts", []) or [])
        except Exception:
            chase_alerts_payload = []
        # [I6] Gaps that have filled: informational, never in the active list.
        try:
            chase_filled_payload = _safe_serialize(getattr(quanta, "gap_filled_alerts", []) or [])
        except Exception:
            chase_filled_payload = []

        early_momentum_payload: List[Dict[str, Any]] = []
        try:
            chase_tickers = {c.get("ticker") for c in (chase_alerts_payload or []) if isinstance(c, dict)}
            picked_tickers = {p.get("ticker") for p in picks}
            for r in (scan_results or [])[:_T.CHASE.EARLY_MOMENTUM_SCAN_ROWS]:
                em_score = int(getattr(r, "early_momentum_score", 0) or 0)
                if em_score < _T.CHASE.EARLY_MOMENTUM_MIN_SIGNALS:
                    continue
                ticker = getattr(r, "ticker", "")
                if ticker in chase_tickers or ticker in picked_tickers:
                    continue
                tech = getattr(r, "technical_indicators", None)
                early_momentum_payload.append({
                    "ticker": ticker,
                    "company": getattr(r, "company", ""),
                    "sector": getattr(r, "sector", ""),
                    "score": float(getattr(r, "overall_score", 0) or 0),
                    "em_score": em_score,
                    "rsi": float(getattr(tech, "rsi", 0) or 0) if tech else 0,
                    "volume_ratio": float(getattr(tech, "volume_ratio", 0) or 0) if tech else 0,
                    "signals": list(getattr(r, "early_momentum_signals", []) or [])[:4],
                })
            early_momentum_payload.sort(key=lambda x: (x["em_score"], x["score"]), reverse=True)
            early_momentum_payload = early_momentum_payload[:_T.CHASE.EARLY_MOMENTUM_MAX_ROWS]
        except Exception as exc:
            logger.debug("early-momentum payload build failed: %s", exc)
            early_momentum_payload = []

        # ------------------------------------------------------------------
        # [SCAN-HEALTH] Classify the scan outcome so the UI never hides a
        # systemic failure (e.g. 100% data-provider errors) behind a benign
        # "no buy-qualified setups" message. error_summary carries the
        # structured per-ticker failures; scan_status is the single field the
        # frontend keys its empty/failure messaging off.
        # ------------------------------------------------------------------
        try:
            error_summary = quanta.get_error_summary()
        except Exception as exc:
            logger.debug("error summary build failed: %s", exc)
            error_summary = {
                "records": [], "by_type": {}, "universe_size": 0,
                "analyzed_success_count": 0, "analysis_failed_count": 0,
                "exception_rate": 0.0,
            }
        universe_size = int(getattr(quanta, "_last_universe_size", 0) or 0)
        analysis_failed_count = int(error_summary.get("analysis_failed_count", 0) or 0)
        analyzed_success_count = int(error_summary.get("analyzed_success_count", 0) or 0)
        exception_rate = float(error_summary.get("exception_rate", 0.0) or 0.0)
        provider_rate_limited_count = int(error_summary.get("provider_rate_limited_count", 0) or 0)
        provider_circuit_open = bool(error_summary.get("provider_circuit_open", False))
        final_pick_count = len(picks)
        # [A04] The outcome is classified on FUNDED names: a scan that only
        # published watch rows funded nothing and is not a success.
        funded_count = sum(1 for p in picks if p.get("is_funded"))
        watch_count = final_pick_count - funded_count

        scan_status = _classify_scan_status(
            universe_size=universe_size,
            exception_rate=exception_rate,
            analysis_failed_count=analysis_failed_count,
            final_pick_count=funded_count,
            provider_rate_limited_count=provider_rate_limited_count,
            provider_circuit_open=provider_circuit_open,
        )

        # [A04] Where every scanned name went, in counts that reconcile; the
        # client mandate is the last stage between the engine and the page.
        accounting: Dict[str, Any] = {}
        try:
            accounting = dict(quanta.scan_accounting() or {})
        except Exception as exc:
            logger.debug("scan accounting unavailable: %s", exc)
        if accounting:
            engine_funded = int(accounting.get("funded", 0) or 0)
            stages = list(accounting.get("book_stages") or [])
            stages.append({"key": "mandate", "entered": engine_funded,
                           "passed": funded_count + row_caps["funded_over_cap"],
                           "evaluated": engine_funded > 0})
            accounting["book_stages"] = stages
            accounting.update(row_caps)
            accounting["funded"] = funded_count
            accounting["watch"] = watch_count
            accounting["published"] = final_pick_count

        empty_scan_explanation = ""
        if funded_count == 0 and scan_status in {"no_picks", "partial"}:
            explain = getattr(quanta, "explain_empty_scan", None)
            if callable(explain):
                empty_scan_explanation = explain()

        result = {
            "regime": regime,
            "total_scanned": universe_size,
            # [SCAN-HEALTH] Authoritative scan outcome + structured errors.
            "scan_status": scan_status,
            "analyzed_success_count": analyzed_success_count,
            "analysis_failed_count": analysis_failed_count,
            "exception_rate": exception_rate,
            "final_pick_count": final_pick_count,
            "funded_count": funded_count,
            "watch_count": watch_count,
            "accounting": _safe_serialize(accounting),
            # [A12] Book names nearest to passing, with the value each needs.
            "closest_to_qualifying": _safe_serialize(
                quanta.closest_to_qualifying({p.get("ticker") for p in picks})
                if callable(getattr(quanta, "closest_to_qualifying", None)) else []),
            "candidate_rejections": _safe_serialize(
                quanta.candidate_rejections()
                if callable(getattr(quanta, "candidate_rejections", None)) else []),
            # [Picks table] One ranked row per name that reached the book.
            "stock_picks": _safe_serialize(_stock_picks_table(quanta, picks, _final_recs,
                                                              int(params["max_positions"]))),
            # [P3] Passed every rule, not funded: selection's reason per name.
            "passed_not_funded": _safe_serialize(
                quanta.passed_not_funded({p.get("ticker") for p in picks})
                if callable(getattr(quanta, "passed_not_funded", None)) else []),
            # [I3] Held-at-Watch names run through the later gates, diagnostic
            # only: never funded, never part of the book.
            "event_held_diagnostics": _safe_serialize(
                quanta.event_held_diagnostics()
                if callable(getattr(quanta, "event_held_diagnostics", None)) else []),
            # [A01] How the universe was chosen (method, duration, cause).
            "universe_selection": _safe_serialize(getattr(quanta, "_universe_selection", None) or {}),
            # [I1] Corporate-event check: counts, each unavailable reason with its
            # fix, and the SEC contact state (never its value).
            "event_check": _safe_serialize({**(getattr(quanta, "_event_summary", None) or {}),
                                            "contact": _sec_contact_status()}),
            "empty_scan_explanation": empty_scan_explanation,
            # [PROVIDER-RESILIENCE] Throttle telemetry for honest UI messaging.
            "provider_rate_limited_count": provider_rate_limited_count,
            "provider_circuit_open": provider_circuit_open,
            "provider_status": _safe_serialize(error_summary.get("provider_status", {}) or {}),
            "error_summary": _safe_serialize(error_summary),
            # [FUNNEL] Transparent scan funnel so the UI can explain WHY the
            # final pick count is what it is. universe_size is the count of
            # tickers that entered scan_market; gate_stats walks survivor
            # counts per quality gate; filter_stats aggregates the
            # per-rejection-reason histogram from analyze_stock.
            "funnel": {
                "universe_size": universe_size,
                "opportunities": len(scan_results),
                "gate_stats": _safe_serialize(getattr(quanta, "_gate_stats", {}) or {}),
                "filter_stats": _safe_serialize(getattr(quanta, "_filter_stats", {}) or {}),
                "failed_tickers": analysis_failed_count,
                "analyzed_success": analyzed_success_count,
                "analysis_failed": analysis_failed_count,
                "exception_rate": exception_rate,
                "final": final_pick_count,
            },
            "recommendations": picks,
            "deep_confirmation": {
                "enabled": dc_enabled,
                # Full reconciliation audit trail (one row per final pick).
                "records": deep_confirm_summary,
                "downgrades": [r for r in deep_confirm_summary if r["downgrade_flag"]],
                "not_confirmed": [r for r in deep_confirm_summary if r.get("not_independently_confirmed")],
                # Backward-compat key consumed by the Pro disagreement watchlist.
                "rejected": analyzer_disagreement_watchlist,
            },
            # [WATCHLIST] Surface analyzer-disagreement picks so the user sees
            # them as a "Scanner liked, Analyzer disagrees" bucket with full
            # context rather than as an opaque empty-state.
            "analyzer_disagreement_watchlist": analyzer_disagreement_watchlist,
            # [F1] Names the corporate-event gate rejected outright (pending
            # acquisition, delisting): action AVOID, the reason from the filing,
            # and no levels. Never funded, never a pick.
            "rejected": _safe_serialize(list(getattr(quanta, "_event_rejections", []) or [])),
            "chase_alerts": chase_alerts_payload,
            "chase_filled": chase_filled_payload,
            "early_momentum": early_momentum_payload,
            "scan_type": params.get("scan_type"),
            "risk_profile": risk_profile_audit,
            "capital": float(params["capital"]),
            # [I5] The minimum position the sizer applied at this capital (A06),
            # so no label falls back to the large-account figure.
            "min_position_effective": float(sp.TradingConfig.effective_min_position_size(float(params["capital"]))),
            # [P4] The limits the book applied at this capital, as fractions of
            # total capital, so the risk tiles colour from config, not fixed cut-offs.
            "book_limits": {
                "position_pct": float(sp.TradingConfig.effective_max_position_pct(float(params["capital"]))),
                "sector_pct": float(sp.TradingConfig.effective_max_sector_pct(float(params["capital"]))),
                "stop_risk_pct": float(sp.TradingConfig.MAX_PORTFOLIO_RISK),
            },
            "max_positions": int(params["max_positions"]),
            "timestamp": _now_iso(),
            "currency": "$" if params["market"] == "US" else "₹",
            "market": params["market"],
            "filters": dict(params.get("filters") or {}),
        }
        result["data_dates"] = _data_dates(list(scan_results or []) or list(recs), result["timestamp"])
        # [Calls] The page shows QUANTA's calls; names that are not picks go to Method.
        _view = _picks_view(result.get("stock_picks") or [], quanta)
        result.update(stock_picks=_view["picks"], screened_out=_view["screened_out"],
                      excluded_for_safety=_view["excluded_for_safety"])
        # [B-89] Rows in names the account already holds; sizing counts the holding.
        _apply_current_holdings(result, float(params["capital"]), params["market"], config=sp.TradingConfig)

        # [I4] Finish every stage with the real final counts or why it was skipped.
        _reached = int((accounting or {}).get("reached_book", len(scan_results or [])) or 0)
        # The table's calls; without a table, the published names' final actions.
        _calls = [r.get("call") for r in (result.get("stock_picks") or [])] or [
            _CALL_OF_ACTION.get(_action_key(r.get("final_action"))) for r in (result.get("recommendations") or [])]
        _ba = sum(1 for c in _calls if c in ("buy", "accumulate"))
        _notes = {"book": f"{_reached} passed the screens, {_ba} Buy or Accumulate, {_calls.count('watchlist')} Watchlist"}
        if "research_check" not in stage_log:
            _notes["research_check"] = ("skipped: turned off for this scan" if not dc_enabled
                                        else "skipped: no name was selected for it")
        _update_job(job_id, progress=100, message="Complete", status="complete", result=result,
                    finished_at=time.time(),
                    stage=_terminal_stage("complete", stage_log, _notes,
                                          round(time.time() - _scan_started, 1)))
        _save_last_scan(params, result)

        # [SCAN-HEALTH] Record telemetry for the dashboard health panel.
        with _last_scan_meta_lock:
            _last_scan_meta.update({
                "timestamp": result["timestamp"],
                "scan_status": scan_status,
                "universe_size": universe_size,
                "analysis_failed_count": analysis_failed_count,
                "exception_rate": exception_rate,
                "final_pick_count": final_pick_count,
                "provider_rate_limited_count": provider_rate_limited_count,
                "provider_circuit_open": provider_circuit_open,
                "deep_confirm_enabled": dc_enabled,
            })

    except Exception as e:
        logger.exception("Stock picks job failed (ref %s)", job_id)
        # The page gets a reference and the error's kind, not its text (paths,
        # internals); the full traceback is in logs/quanta_web.log under the
        # same reference, so it can be read even when the console cannot.
        _update_job(job_id, status="error", error=f"The scan stopped with an internal error ({type(e).__name__}, ref {job_id}).",
                    error_detail={"reason": "internal", "kind": type(e).__name__, "ref": job_id}, finished_at=time.time(),
                    stage=_terminal_stage("failed", stage_log, None, round(time.time() - _scan_started, 1)))
    finally:
        # [F07] Release the admission slot only now that the worker has really
        # stopped touching the provider. A cancel earlier flipped the job's
        # status to terminal, but until this line runs the scan was still in
        # flight and a second scan must not be admitted on top of it.
        _release_job_worker(job_id)


# ---------------------------------------------------------------------------
# ROUTES — Stock Analysis (TradingAnalysis_ELITE)
# ---------------------------------------------------------------------------

# Price chart for one symbol over one range (Overview and Research). Each range
# names its Yahoo period and bar interval; 1D carries pre- and post-market bars
# and the session boundaries so the time axis spans the whole trading day.
CHART_RANGES: Dict[str, Tuple[str, str, bool, float]] = {
    # key: (period, interval, include extended hours, cache seconds)
    "1d": ("1d", "1m", True, 60.0),
    "5d": ("5d", "5m", False, 120.0),
    "1mo": ("1mo", "30m", False, 600.0),
    "6mo": ("6mo", "1d", False, 900.0),
    "ytd": ("ytd", "1d", False, 900.0),
    "1y": ("1y", "1d", False, 900.0),
    "5y": ("5y", "1wk", False, 3600.0),
    "max": ("max", "1mo", False, 3600.0),
}
_chart_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_chart_cache_lock = threading.Lock()


def _chart_epoch_ms(value: Any) -> Optional[int]:
    try:
        import pandas as pd
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return int(ts.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


def _chart_payload(symbol: str, range_key: str) -> Dict[str, Any]:
    import yfinance as yf
    period, interval, prepost, _ttl = CHART_RANGES[range_key]
    ticker = yf.Ticker(symbol)
    # The process-wide history cache keeps bars for fifteen minutes; a live
    # chart keeps its own shorter cache, so it reads the provider directly.
    raw_history = globals().get("_orig_history")
    kwargs = {"period": period, "interval": interval, "prepost": prepost, "auto_adjust": False}
    frame = raw_history(ticker, **kwargs) if callable(raw_history) else ticker.history(**kwargs)
    if frame is None or getattr(frame, "empty", True):
        return {"available": False, "symbol": symbol, "range": range_key,
                "reason": "No price bars were returned for this symbol and range."}
    meta = {}
    try:
        meta = ticker.history_metadata or {}
    except Exception:  # noqa: BLE001
        meta = {}
    points = []
    for stamp, row in frame.iterrows():
        close = row.get("Close")
        if close is None or close != close:
            continue
        vol = row.get("Volume")
        points.append([_chart_epoch_ms(stamp), round(float(close), 4),
                       int(vol) if vol is not None and vol == vol else None])
    if not points:
        return {"available": False, "symbol": symbol, "range": range_key,
                "reason": "No price bars were returned for this symbol and range."}
    periods = meta.get("currentTradingPeriod") or {}
    session = {}
    for key in ("pre", "regular", "post"):
        part = periods.get(key) or {}
        start, end = _chart_epoch_ms(part.get("start")), _chart_epoch_ms(part.get("end"))
        if start and end:
            session[key] = [start, end]
    regular = session.get("regular")
    now_ms = int(time.time() * 1000)
    last_ms = points[-1][0]
    extended = None
    if range_key == "1d" and regular and not (regular[0] <= last_ms < regular[1]):
        extended = {"price": points[-1][1], "time": last_ms,
                    "session": "pre" if last_ms < regular[0] else "post"}
    reg_price = meta.get("regularMarketPrice")
    return {
        "available": True,
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "instrument_type": meta.get("instrumentType"),
        "timezone": meta.get("exchangeTimezoneName"),
        "range": range_key,
        "interval": interval,
        "points": points,
        "previous_close": meta.get("chartPreviousClose") if range_key == "1d" else None,
        "regular_price": round(float(reg_price), 4) if reg_price is not None else None,
        "regular_time": _chart_epoch_ms(meta.get("regularMarketTime")),
        "session": session if range_key == "1d" else {},
        "extended": extended,
        "market_open": bool(regular and regular[0] <= now_ms < regular[1]),
        "retrieved_at": now_ms,
    }


@app.route("/api/chart")
def chart_api():
    """Price bars for one symbol and range: GET /api/chart?symbol=AAPL&range=1d."""
    if _cross_site_read():
        return jsonify({"available": False, "error": "Cross-site request refused."}), 403
    symbol = (request.args.get("symbol") or "").strip().upper()
    range_key = (request.args.get("range") or "1d").strip().lower()
    if not symbol or not re.fullmatch(r"[A-Z0-9^.=&-]{1,32}", symbol):
        return jsonify({"available": False, "error": "symbol is required"}), 400
    if range_key not in CHART_RANGES:
        return jsonify({"available": False, "error": f"range must be one of {', '.join(CHART_RANGES)}"}), 400
    ttl = CHART_RANGES[range_key][3]
    key = (symbol, range_key)
    now = time.time()
    with _chart_cache_lock:
        hit = _chart_cache.get(key)
        if hit and now - hit[0] < ttl:
            return jsonify({**hit[1], "cached": True})
    if _provider_in_cooldown():
        if hit:
            return jsonify({**hit[1], "cached": True, "stale": True})
        return jsonify({"available": False, "symbol": symbol, "range": range_key,
                        "reason": "The price provider is rate-limiting; try again in a minute."}), 503
    try:
        payload = _chart_payload(symbol, range_key)
    except Exception as exc:  # noqa: BLE001
        if _provider_classify_rate_limit(exc):
            _provider_note_rate_limit()
        logger.info("Chart fetch failed for %s %s: %s", symbol, range_key, type(exc).__name__)
        if hit:
            return jsonify({**hit[1], "cached": True, "stale": True})
        return jsonify({"available": False, "symbol": symbol, "range": range_key,
                        "reason": "Price bars could not be fetched right now."}), 502
    if payload.get("available"):
        with _chart_cache_lock:
            _chart_cache[key] = (now, payload)
            if len(_chart_cache) > 400:
                for old in sorted(_chart_cache, key=lambda k: _chart_cache[k][0])[:100]:
                    _chart_cache.pop(old, None)
    return jsonify({**payload, "cached": False})


# Research search: listings matching a name or ticker, US and Indian exchanges
# only (the two markets QUANTA analyses), stocks and ETFs only.
SEARCH_EXCHANGES = {
    "US": {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "NAS", "NYS", "NIM"},
    "IN": {"NSI", "BSE"},
}
_search_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_search_cache_lock = threading.Lock()
SEARCH_TTL = 600.0


def _cross_site_read() -> bool:
    """A browser read started by another site (Sec-Fetch-Site: cross-site).

    The provider-backed GETs refuse it, so a page in another tab cannot spend
    QUANTA's provider budget or push it into a rate-limit cooldown.
    """
    return (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site"


@app.route("/api/symbol-search")
def symbol_search_api():
    """GET /api/symbol-search?q=reliance: up to eight listings, each tagged US or IN."""
    q = (request.args.get("q") or "").strip()
    if not q or len(q) > 40 or not re.fullmatch(r"[A-Za-z0-9 .&^=-]+", q):
        return jsonify({"results": []})
    if _cross_site_read():
        return jsonify({"results": [], "error": "Cross-site request refused."}), 403
    key = q.lower()
    now = time.time()
    with _search_cache_lock:
        hit = _search_cache.get(key)
    if hit and now - hit[0] < SEARCH_TTL:
        return jsonify({"results": hit[1], "cached": True})
    if _provider_in_cooldown():
        return jsonify({"results": [], "reason": "The search provider is rate-limiting; type the full ticker."})
    try:
        import yfinance as yf
        quotes = yf.Search(q, max_results=12, news_count=0).quotes or []
    except Exception as exc:  # noqa: BLE001
        if _provider_classify_rate_limit(exc):
            _provider_note_rate_limit()
        logger.info("Symbol search failed for %r: %s", q, type(exc).__name__)
        return jsonify({"results": [], "reason": "Search is unavailable right now; type the full ticker."})
    results: List[Dict[str, Any]] = []
    for item in quotes:
        exch = str(item.get("exchange") or "")
        market = next((m for m, codes in SEARCH_EXCHANGES.items() if exch in codes), None)
        if market is None or str(item.get("quoteType") or "") not in ("EQUITY", "ETF"):
            continue
        results.append({
            "symbol": str(item.get("symbol") or ""),
            "name": item.get("longname") or item.get("shortname") or "",
            "exchange": item.get("exchDisp") or exch,
            "type": "ETF" if item.get("quoteType") == "ETF" else "Stock",
            "market": market,
        })
        if len(results) >= 8:
            break
    with _search_cache_lock:
        _search_cache[key] = (now, results)
        if len(_search_cache) > 500:
            for old in sorted(_search_cache, key=lambda k: _search_cache[k][0])[:100]:
                _search_cache.pop(old, None)
    return jsonify({"results": results, "cached": False})


@app.route("/api/analyze", methods=["POST"])
def start_analysis():
    """Launch deep analysis for a single ticker. Returns job_id."""
    data = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    # "&" is part of real NSE symbols (M&M.NS); the UI accepts it, so must we.
    if not re.fullmatch(r"[A-Z0-9^.=&-]{1,32}", ticker):
        return jsonify({"error": "ticker contains unsupported characters"}), 400
    #   market        → the market toggle. A bare symbol follows it (India tries
    #                   NSE, then BSE, then the US listing); explicit suffixes win.
    market = _analysis_market(data.get("market"))

    # [PROVIDER-RESILIENCE] Optional client hints:
    #   safe_mode      → fetch only the lightweight chart/core data, skip the
    #                    heavy extended-fundamentals path during a throttle.
    #   prefer_cache   → return any cached tear sheet immediately without a
    #                    provider call (used by "Use cached data" UI action).
    safe_mode = bool(data.get("safe_mode"))
    prefer_cache = bool(data.get("prefer_cache"))
    # [PERF-FASTMODE] Fast Mode (or Safe Mode) skips the network-heavy optional
    # modules for a quick, preliminary tear sheet. Safe Mode implies Fast Mode.
    fast_mode = bool(data.get("fast_mode")) or safe_mode
    #   account_size  → the account the position sizing is measured against.
    #                   Absent, the analyzer's 100,000 placeholder is used and
    #                   the response says so, so a hypothetical size is never
    #                   mistaken for one that fits the user's real mandate.
    account_size, account_size_assumed = _resolve_account_context(data)

    job_id = str(uuid.uuid4())[:8]

    _prune_jobs()
    with _jobs_lock:
        # [LOCK-WATCHDOG] Coalesce rapid duplicate clicks for the same ticker.
        # If the user double-clicks Build Tear Sheet, do NOT spawn a second
        # worker — return the already-running job's id so both clicks share
        # the same result. This was the second half of the "stuck forever"
        # bug: every click added another job behind the lock.
        # Only a job of the SAME mode is a duplicate: a "Load full analysis"
        # click while a fast preview is still running must not be handed the
        # preliminary result and labelled as the validated verdict.
        for existing_id, existing in _jobs.items():
            if existing.get("status") != "running":
                continue
            if existing.get("type") != "analyze":
                continue
            existing_ticker = (existing.get("ticker") or "").upper()
            if (existing_ticker == ticker
                    and existing.get("market") == market
                    and bool(existing.get("fast_mode")) == fast_mode
                    and float(existing.get("account_size") or DEFAULT_ACCOUNT_SIZE)
                    == account_size):
                return jsonify({
                    "job_id": existing_id,
                    "status": "running",
                    "coalesced": True,
                }), 202

        if sum(1 for job in _jobs.values()
               if job.get("type") == "analyze" and job.get("worker_active")) >= 4:
            return jsonify({"error": "Analyzer busy; retry after pending analyses finish.",
                            "error_type": "analyzer_busy", "retryable": True}), 429

        _jobs[job_id] = {
            "type": "analyze",
            "worker_active": True,
            "ticker": ticker,
            "market": market,
            "fast_mode": fast_mode,
            "account_size": account_size,
            "account_size_assumed": account_size_assumed,
            "status": "running",
            "progress": 0,
            "message": f"Starting analysis for {ticker}…",
            "result": None,
            "error": None,
            "error_detail": None,
            "started": _now_iso(),
            "started_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_analysis_worker, args=(job_id, ticker),
        kwargs={"safe_mode": safe_mode, "prefer_cache": prefer_cache,
                "fast_mode": fast_mode, "account_size": account_size,
                "account_size_assumed": account_size_assumed, "market": market},
        daemon=True,
    )
    thread.start()

    return jsonify({
        "job_id": job_id,
        "status": "running",
        "account_size": account_size,
        "account_size_assumed": account_size_assumed,
    }), 202


_analyze_lock = threading.Lock()  # serialize analysis requests to avoid rate-limit storms
# Interactive (Research page) requests waiting for the analyzer. threading.Lock
# is not fair: a My Portfolio job analysing holdings back to back could re-take
# the lock ahead of a waiting tear-sheet request until it timed out ("Analyzer
# busy"). Background holdings yield while this count is non-zero.
_interactive_waiting = 0
_interactive_cv = threading.Condition()
INTERACTIVE_YIELD_SECONDS = 120.0


def _wait_for_interactive_requests(timeout: float = INTERACTIVE_YIELD_SECONDS) -> None:
    with _interactive_cv:
        _interactive_cv.wait_for(lambda: _interactive_waiting == 0, timeout=timeout)


def _run_analysis_worker(job_id: str, ticker: str, **kwargs):
    try:
        _run_analysis(job_id, ticker, **kwargs)
    finally:
        with _jobs_lock:
            awaiting = _jobs.get(job_id, {}).get("awaiting_provider", False)
        if not awaiting:
            _release_job_worker(job_id)


def _finish_timed_out_analysis(job_id: str) -> None:
    try:
        _analyze_lock.release()
    finally:
        _release_job_worker(job_id)


def _analyze_rate_limit_detail(ticker: str, attempts: int,
                               cached_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """[PROVIDER-RESILIENCE] Build the structured rate-limit error payload the
    tear-sheet UI renders (cooldown countdown, safe-mode retry, cached fallback).
    """
    snap = _provider_rl_snapshot()
    cooldown_remaining = _provider_cooldown_remaining()
    last_success = snap.get("last_success_time")
    last_success_iso = (
        datetime.fromtimestamp(last_success).isoformat() if isinstance(last_success, (int, float)) else None
    )
    return {
        "status": "provider_rate_limited",
        "ticker": ticker,
        "provider": "yfinance",
        "error_type": "provider_rate_limited",
        "error_message": (
            f"Yahoo Finance is rate-limiting requests for {ticker}. "
            f"Retry available in ~{int(cooldown_remaining)}s."
        ),
        "attempts": attempts,
        "retryable": True,
        "cooldown_remaining_seconds": round(cooldown_remaining, 1),
        "used_cached_data": bool(cached_payload),
        "cache_timestamp": (cached_payload or {}).get("cache_timestamp"),
        "partial_data_available": bool(cached_payload),
        "failed_sections": [] if cached_payload else ["price", "valuation", "fundamentals", "analyst", "insider"],
        "last_successful_fetch": last_success_iso,
        "suggested_action": (
            "Wait for the cooldown to elapse, retry in Safe Mode (chart + core "
            "metrics only), or use the most recent cached tear sheet."
        ),
    }


def _analyze_no_data_detail(ticker: str, attempts: int,
                            validated_invalid: bool,
                            cached_payload: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """[F05] Structured payload for "retries exhausted with no usable price".

    The engine's own validation gate is the ONLY thing that can conclude a
    ticker is invalid. Exhausting retries proves that the provider did not
    return data — which is a statement about the provider, not about the
    instrument. The original code emitted `error_type="invalid_ticker"` with
    `retryable=False` in both cases, so a temporary Yahoo outage told the user
    their ticker was permanently bad and removed the retry affordance that
    would have fixed it.
    """
    if validated_invalid:
        return {
            "status": "invalid_instrument",
            "ticker": ticker,
            "provider": "yfinance",
            "error_type": "invalid_ticker",
            # Say the word the verdict actually is. This is the one branch
            # where calling the symbol invalid is warranted (the validation
            # gate concluded it), and the reader scanning this line should not
            # have to infer that from "no such instrument was found".
            "error_message": (
                f"{ticker} appears to be an invalid ticker — it failed the "
                f"data-validation gate, which found no such tradable instrument."
            ),
            "attempts": attempts,
            "retryable": False,
            "used_cached_data": bool(cached_payload),
            "suggested_action": "Check the symbol, including any exchange suffix.",
        }
    return {
        "status": "provider_unavailable",
        "ticker": ticker,
        "provider": "yfinance",
        "error_type": "provider_unavailable",
        "error_message": (
            f"No price data was returned for {ticker} after {attempts} "
            f"attempt(s). The provider did not respond with usable data; this "
            f"has not been established as an invalid ticker."
        ),
        "attempts": attempts,
        "retryable": True,
        "used_cached_data": bool(cached_payload),
        "cache_timestamp": (cached_payload or {}).get("cache_timestamp"),
        "partial_data_available": bool(cached_payload),
        "suggested_action": (
            "Retry shortly. If the symbol is definitely correct and this "
            "persists, the provider may be down for that instrument."
        ),
    }


def _analysis_market(raw: Any) -> Optional[str]:
    """Market toggle for symbol routing; None when the client sent none."""
    m = str(raw or "").strip().upper()
    return "IN" if m in ("IN", "INDIA") else ("US" if m in ("US", "USA") else None)


def _cache_identity(ticker: str, market: Optional[str]) -> str:
    """A bare symbol means different securities in different markets (INFY is
    a US ADR and an NSE stock), so the market is part of its cache identity."""
    if market and not any(c in ticker for c in ".^="):
        return f"{ticker}@{market}"
    return ticker


def _analysis_failure_detail(result: Any, requested: str, attempts: int) -> Dict[str, Any]:
    """Structured, category-specific failure for a non-OK engine status.

    The engine already knows WHY it has no Decision (invalid symbol, new
    listing, unsupported instrument, stale bars, provider gap). That diagnosis
    is published as-is, with the facts that are known, instead of being
    flattened into "the provider did not respond".
    """
    from engine.data_status import FAILURE_CATEGORY, DataStatus
    from engine.symbols import FAILURE_CATEGORIES, alternatives_for, has_exchange_suffix

    status = str(getattr(result, "data_status", "") or "")
    if bool(getattr(result, "is_invalid", False)) and status in ("", DataStatus.OK):
        status = DataStatus.INVALID_TICKER
    category = FAILURE_CATEGORY.get(status, "provider_unavailable")
    resolved = str(getattr(result, "ticker", "") or requested)
    reason = (getattr(result, "data_status_reason", "") or "").strip()
    bars = int(getattr(result, "history_bars", 0) or 0)
    titles = {
        "invalid_symbol": f"{requested} was not found",
        "unsupported_instrument": f"{resolved} is not a stock or fund",
        "insufficient_data": f"{resolved} is too new to score",
        "stale_data": f"{resolved} has no current price data",
        "provider_unavailable": f"No usable data for {resolved} right now",
        "temporary_failure": "The data provider is throttling requests",
    }
    messages = {
        "invalid_symbol": (f"The data provider has no listed instrument for {requested}, or it has been "
                           f"delisted. Check the symbol and its exchange suffix."),
        "unsupported_instrument": reason,
        "insufficient_data": reason,
        "stale_data": reason or "The latest daily bar is out of date.",
        "provider_unavailable": (reason or "The provider answered without usable data.")
                                + " This is not a verdict that the symbol is invalid.",
        "temporary_failure": reason or "The provider rate-limited the request.",
    }
    actions = {
        "invalid_symbol": "Check the spelling. Indian listings need .NS (NSE) or .BO (BSE); "
                          "US share classes use a dash (BRK-B).",
        "unsupported_instrument": "Research an ETF that tracks it instead (for example SPY for the "
                                  "S&P 500, NIFTYBEES.NS for the Nifty 50).",
        "insufficient_data": (f"Research becomes available after {50 - bars} more trading session(s). "
                              f"The facts above are what the provider has so far.") if bars else
                             "Check back once the listing has more trading history.",
        "stale_data": "Retry later; if it persists the instrument may be suspended or delisted.",
        "provider_unavailable": "Retry shortly, or switch the market if the listing is on another exchange.",
        "temporary_failure": "Wait for the cooldown, then retry.",
    }
    price = float(getattr(result, "current_price", 0) or 0)
    facts = {
        "requested_symbol": requested,
        "resolved_symbol": resolved,
        "symbol_note": getattr(result, "symbol_note", "") or "",
        "company_name": getattr(result, "company_name", "") or "",
        "quote_type": getattr(result, "quote_type", "") or "",
        "exchange": getattr(result, "exchange", "") or "",
        "currency": getattr(result, "currency", "") or "",
        "currency_symbol": getattr(result, "currency_symbol", "") or "",
        "last_price": price if price > 0 else None,
        "market_cap": float(getattr(result, "market_cap", 0) or 0) or None,
        "history_bars": bars,
        "first_bar_date": getattr(result, "first_bar_date", "") or None,
        "last_bar_date": getattr(result, "last_bar_date", "") or None,
    }
    return {
        "status": category,
        "category": category,
        "data_status": status,
        "ticker": resolved,
        "provider": "yfinance",
        "error_type": category,
        "title": titles[category],
        "error_message": messages[category],
        "suggested_action": actions[category],
        "retryable": FAILURE_CATEGORIES[category],
        "attempts": attempts,
        "facts": facts,
        # Next steps the UI can offer as one-click actions.
        "alternatives": alternatives_for(resolved) if category == "unsupported_instrument" else [],
        "can_switch_market": (category in ("invalid_symbol", "provider_unavailable")
                              and not has_exchange_suffix(requested)
                              and not any(c in requested for c in "^=")),
    }


def _run_analysis(job_id: str, ticker: str, safe_mode: bool = False,
                  prefer_cache: bool = False, fast_mode: bool = False,
                  account_size: float = DEFAULT_ACCOUNT_SIZE,
                  account_size_assumed: bool = True,
                  market: Optional[str] = None):
    """Background worker for TradingAnalysis_ELITE with shared-cooldown awareness.

    [PROVIDER-RESILIENCE] Behaviour:
      • Serve a fresh cached tear sheet immediately (always honoured).
      • If the shared provider cooldown is active, do NOT call yfinance — return
        a stale cached tear sheet (labelled) when available, otherwise a
        structured `provider_rate_limited` error with cooldown + retry hints.
      • On a live rate-limit during analysis, seed the shared cooldown and emit
        the same structured error instead of a generic failure string.

    [LOCK-WATCHDOG] The analyzer is serialized by `_analyze_lock` to avoid
    yfinance rate-limit storms. We acquire it with a bounded timeout so a
    single stuck analysis can never silently queue every subsequent click
    forever (the user-visible "Building tear sheet…" forever bug).
    """
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]  # seconds
    cache_ticker = _cache_identity(ticker, market)

    # Cache hits need neither the analyzer lock nor another provider call.
    request_mode = "fast" if fast_mode else "full"
    cooldown = _provider_in_cooldown()
    cached = _get_cached_analysis(cache_ticker, mode=request_mode,
                                  allow_stale=prefer_cache or cooldown,
                                  account_size=account_size)
    if cached:
        cached["account_context"] = {
            "account_size": account_size, "assumed": bool(account_size_assumed),
            "note": (f"Sizing assumes a ${account_size:,.0f} account (default)."
                     if account_size_assumed else f"Sized against a ${account_size:,.0f} account."),
        }
        cached["used_cached_data"] = bool(prefer_cache or cached.get("cache", {}).get("stale"))
        if cooldown:
            cached["provider_rate_limited"] = True
        _update_job(job_id, progress=100, status="complete", result=cached,
                    message="Loaded cached analysis")
        return

    # [LOCK-WATCHDOG] Try to grab the serialization lock with a bounded wait.
    # 90s is long enough to let a slow-but-progressing analysis finish, short
    # enough that a hung one fails fast and clears the queue.
    _update_job(job_id, progress=2, message="Waiting for analyzer slot…")
    lock_wait_s = 90.0
    global _interactive_waiting
    with _jobs_lock:
        interactive = (_jobs.get(job_id) or {}).get("origin") != "portfolio"
    if interactive:
        with _interactive_cv:
            _interactive_waiting += 1
    try:
        acquired = _analyze_lock.acquire(timeout=lock_wait_s)
    finally:
        if interactive:
            with _interactive_cv:
                _interactive_waiting -= 1
                _interactive_cv.notify_all()
    if not acquired:
        _update_job(
            job_id, status="error", progress=100,
            message="Analyzer busy",
            error=(
                f"Another analysis has been running for over {int(lock_wait_s)}s. "
                "Wait a moment and click Build Tear Sheet again."
            ),
            error_detail={
                "status": "analyzer_busy",
                "category": "temporary_failure",
                "title": "Another analysis is still running",
                "error_type": "analyzer_busy",
                "error_message": "Server busy — please retry shortly.",
                "suggested_action": "Wait a moment, then build the tear sheet again.",
                "retryable": True,
                "ticker": ticker,
                "facts": {"requested_symbol": ticker},
                "lock_wait_seconds": lock_wait_s,
            },
        )
        return

    lock_release_deferred = False
    try:
        if _job_is_cancelled(job_id):
            return
        # [F04] Mode is part of cache identity: a full request must not be
        # satisfied by a fast preview.
        request_mode = "fast" if fast_mode else "full"
        cached = _get_cached_analysis(cache_ticker, mode=request_mode,
                                      account_size=account_size)
        if cached and not prefer_cache:
            cached = dict(cached)
            if not cached.get("price_chart"):
                cached["price_chart"] = _price_chart_snapshot(cached.get("ticker") or ticker)
            cached["price_reconciliation"] = _reconcile_prices(cached, cached.get("price_chart"))
            _update_job(job_id, progress=100, message="Loaded fresh cached analysis", status="complete", result=cached)
            return

        # [PROVIDER-RESILIENCE] Explicit "use cached data" request, or a fresh
        # cache hit we want to label — serve cache without touching the provider.
        if cached and prefer_cache:
            labeled = dict(cached)
            ts = cached.get("cache_timestamp") or cached.get("retrieved_at")
            labeled["used_cached_data"] = True
            labeled["cache_timestamp"] = ts
            _update_job(job_id, progress=100, message="Loaded cached analysis",
                        status="complete", result=labeled)
            return

        # [PROVIDER-RESILIENCE] Cooldown gate — never start a provider storm.
        if _provider_in_cooldown():
            detail = _analyze_rate_limit_detail(ticker, attempts=0, cached_payload=None)
            _update_job(job_id, status="error", progress=100,
                        message="Provider rate-limited",
                        error=detail["error_message"], error_detail=detail)
            return

        for attempt in range(MAX_RETRIES):
            try:
                _update_job(job_id, progress=6, message="Preparing QUANTA Elite analyzer…")
                ta, analyzer, api_client = _get_analysis_runtime()

                _update_job(job_id, progress=15, message="Loading market context…")
                market_env = {}
                try:
                    market_env = _get_market_environment_cached(ta, api_client, allow_refresh=False)
                except Exception as me:
                    logger.warning(f"Market env fetch failed (non-fatal): {me}")

                mode_label = " (fast mode)" if fast_mode else (" (safe mode)" if safe_mode else "")
                _update_job(job_id, progress=28, message=f"Analyzing {ticker}{mode_label}…")
                # [LOCK-WATCHDOG] Bound the analyzer with a wall-clock timeout
                # so a stuck yfinance call cannot hang the whole pipeline.
                # Fast mode is supposed to finish in ~15s; full mode ~45s.
                # We give 4x headroom to avoid false positives on cold-cache
                # large-cap pulls (e.g., HAL.NS), then escalate to an error
                # the user can see and retry — instead of "Building…" forever.
                wall_clock_s = 60.0 if fast_mode else 180.0
                # [LOCK-WATCHDOG] Manage the executor manually. A plain
                # `with ThreadPoolExecutor() as _ex:` calls shutdown(wait=True)
                # on exit, which BLOCKS until the worker finishes — so a hung
                # yfinance call would keep `_analyze_lock` held long past the
                # wall-clock timeout, jamming every queued request ("Building
                # tear sheet… forever"). Shut down WITHOUT waiting on a hung
                # worker so the timeout truly fails fast and frees the lock.
                _ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyze")
                _ex_clean_shutdown = False
                try:
                    # [ACCOUNT] Size against the requested account rather than
                    # letting the analyzer silently apply its own default.
                    fut = _ex.submit(analyzer.analyze, ticker,
                                     account_size=account_size,
                                     fast_mode=fast_mode, market=market)
                    try:
                        result = fut.result(timeout=wall_clock_s)
                        _ex_clean_shutdown = True
                        # [F7] Share this session's research verdict with Picks'
                        # deep confirmation (engine.research_cache).
                        try:
                            from engine import research_cache as _rc
                            _rc.put(ticker, "fast" if fast_mode else "full", result)
                        except Exception:  # noqa: BLE001 - the cache is an optimisation
                            pass
                    except FuturesTimeout:
                        fut.cancel()  # best-effort (Python can't preempt threads)
                        lock_release_deferred = True
                        with _jobs_lock:
                            if job_id in _jobs:
                                _jobs[job_id]["awaiting_provider"] = True
                        # [F07] The abandoned thread keeps running against the
                        # analyzer it already holds. Retire the SHARED runtime
                        # so the next request does not pick up that same
                        # mutable object and race with it.
                        _retire_analysis_runtime()
                        fut.add_done_callback(lambda _: _finish_timed_out_analysis(job_id))
                        logger.warning(
                            f"[LOCK-WATCHDOG] {ticker} exceeded {wall_clock_s}s wall clock — "
                            f"failing fast and retiring the shared analyzer runtime."
                        )
                        _update_job(
                            job_id, status="error", progress=100,
                            message=f"Analysis timed out after {int(wall_clock_s)}s",
                            error=(
                                f"{ticker} did not finish within {int(wall_clock_s)}s. "
                                "This is usually a slow Yahoo Finance response. Try Fast Preview "
                                "or retry in a moment."
                            ),
                            error_detail={
                                "status": "analysis_timeout",
                                "category": "temporary_failure",
                                "title": f"{ticker} took longer than {int(wall_clock_s)}s",
                                "ticker": ticker,
                                "error_type": "analysis_timeout",
                                "error_message": (f"The data provider did not finish within {int(wall_clock_s)}s. "
                                                  "This is usually a slow provider response, not a problem with the symbol."),
                                "suggested_action": "Retry, or use Fast preview for a quicker preliminary tear sheet.",
                                "facts": {"requested_symbol": ticker},
                                "retryable": True,
                                "wall_clock_seconds": wall_clock_s,
                                "fast_mode": fast_mode,
                            },
                        )
                        return
                finally:
                    # Never block on a hung worker: on the timeout path we shut
                    # down WITHOUT waiting so the lock is freed immediately; on
                    # the success path the future is already done so wait=True
                    # is instant.
                    _ex.shutdown(wait=_ex_clean_shutdown, cancel_futures=True)

                # [DATA-STATUS] The engine says WHY it has no Decision. Only a
                # provider gap is worth retrying; an invalid symbol, a listing
                # too new to score, stale bars or an unsupported instrument are
                # answered at once, with what is known, in their own category.
                from engine.data_status import DataStatus as _DS
                _status = str(getattr(result, "data_status", "") or _DS.OK)
                _no_price = result.current_price is None or result.current_price == 0
                if _status == _DS.PROVIDER_RATE_LIMITED or (_no_price and (
                        _provider_in_cooldown() or _provider_classify_rate_limit(
                            Exception(getattr(result, "error", "") or "")))):
                    _provider_note_rate_limit()
                    detail = _analyze_rate_limit_detail(ticker, attempts=attempt + 1, cached_payload=None)
                    _update_job(job_id, status="error", progress=100,
                                message="Provider rate-limited",
                                error=detail["error_message"], error_detail=detail)
                    return
                _retryable_gap = _status == _DS.PROVIDER_DATA_UNAVAILABLE or (
                    _no_price and _status == _DS.OK and not getattr(result, "is_invalid", False))
                if _status != _DS.OK or (_no_price and not getattr(result, "is_invalid", False)):
                    if _retryable_gap and attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        _update_job(job_id, progress=30,
                                    message=f"Provider returned no usable data; retrying in {delay}s "
                                            f"(attempt {attempt+2}/{MAX_RETRIES})…")
                        logger.warning(f"No usable data for {ticker} ({_status}), retrying in {delay}s")
                        time.sleep(delay)
                        continue
                    detail = _analysis_failure_detail(result, ticker, attempts=attempt + 1)
                    # A provider gap may still have a last-good tear sheet.
                    stale = (_get_cached_analysis(cache_ticker, mode=request_mode, allow_stale=True,
                                                  account_size=account_size)
                             if detail["category"] == "provider_unavailable" else None)
                    if stale:
                        labeled = dict(stale)
                        labeled["used_cached_data"] = True
                        labeled["provider_unavailable"] = True
                        labeled["error_detail"] = detail
                        _update_job(job_id, progress=100, status="complete",
                                    message="Provider unavailable — serving last good tear sheet",
                                    result=labeled)
                        return
                    _update_job(job_id, status="error", progress=100,
                                message=detail["title"], error=detail["error_message"],
                                error_detail=detail)
                    return

                _update_job(job_id, progress=85, message="Serializing results…")

                # Use the MCP serialization helper
                try:
                    analysis = ta._mcp_result_to_dict(result)
                except Exception as se:
                    logger.warning(f"MCP serialization failed: {se}, using fallback")
                    analysis = {"ticker": ticker, "error_detail": "Partial serialization"}
                analysis["market_environment"] = _safe_serialize(market_env)
                analysis["price_chart"] = _price_chart_snapshot(result.ticker or ticker)
                # [PRICE-RECONCILE] Make any analysis-price vs session-LTP gap
                # explicit instead of letting two different numbers sit on
                # the same tear sheet unexplained.
                analysis["price_reconciliation"] = _reconcile_prices(analysis, analysis["price_chart"])
                analysis["safe_mode"] = bool(safe_mode)
                analysis["fast_mode"] = bool(fast_mode)
                # Outcomes live in the benchmarked ledger (/api/track-record);
                # the legacy JSON tracker and its raw win rate are gone.
                analysis["cache"] = {"hit": False, "ttl_seconds": ANALYSIS_RESULT_CACHE_SECONDS}
                analysis["cache_timestamp"] = time.time()
                # [ACCOUNT] Name the account every share count and weight below
                # was computed against, and say when that was a placeholder.
                analysis["account_context"] = {
                    "account_size": account_size,
                    "assumed": bool(account_size_assumed),
                    "note": (
                        f"Sizing assumes a ${account_size:,.0f} account "
                        f"(default — not your declared mandate)."
                        if account_size_assumed else
                        f"Sized against a ${account_size:,.0f} account."
                    ),
                }
                _provider_note_success()
                # [F04] Fast-mode results are cached under their OWN mode key.
                # They can serve later fast requests but can never be returned
                # for a full request, so the preliminary result is reusable
                # without being able to masquerade as the validated analysis.
                _set_cached_analysis(cache_ticker, analysis, mode=request_mode,
                                     account_size=account_size)

                _update_job(job_id, progress=100, message="Complete", status="complete", result=analysis)
                return  # success

            except Exception as e:
                is_rate_limit = _provider_classify_rate_limit(e)

                if is_rate_limit:
                    # [PROVIDER-RESILIENCE] Seed the shared cooldown so the
                    # scanner, chart, and probe all back off together. Do not
                    # keep hammering on confirmed rate-limits.
                    _provider_note_rate_limit()
                    stale = _get_cached_analysis(cache_ticker, mode=request_mode,
                                                 allow_stale=True,
                                                 account_size=account_size)
                    detail = _analyze_rate_limit_detail(ticker, attempts=attempt + 1, cached_payload=stale)
                    if stale:
                        labeled = dict(stale)
                        labeled["used_cached_data"] = True
                        labeled["provider_rate_limited"] = True
                        labeled["rate_limit_detail"] = detail
                        _update_job(job_id, progress=100,
                                    message="Provider rate-limited — serving cached tear sheet",
                                    status="complete", result=labeled)
                    else:
                        _update_job(job_id, status="error", progress=100,
                                    message="Provider rate-limited",
                                    error=detail["error_message"], error_detail=detail)
                    return

                logger.exception(f"Analysis job failed for {ticker} (ref {job_id})")
                _update_job(job_id, status="error", error=f"Internal error (ref {job_id})", error_detail={
                    "status": "engine_error", "category": "engine_error", "ticker": ticker,
                    "title": f"The analysis of {ticker} stopped with an internal error",
                    "error_message": f"{type(e).__name__} (details in the server log, ref {job_id})",
                    "suggested_action": "Retry. If it repeats for this symbol, the logs name the failing stage.",
                    "retryable": True, "attempts": attempt + 1, "provider": "yfinance",
                    "facts": {"requested_symbol": ticker},
                })
                return
    finally:
        # [LOCK-WATCHDOG] Always release — even on uncaught exceptions — so a
        # single bad analysis can never permanently jam the queue.
        if not lock_release_deferred:
            _analyze_lock.release()



# ---------------------------------------------------------------------------
# ROUTES — Job Status (SSE + polling)
# ---------------------------------------------------------------------------

@app.route("/api/jobs/<job_id>")
def job_status(job_id: str):
    """Poll for job status."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job_id,
        "type": job["type"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "stage": job.get("stage"),
        "result": job["result"],
        "error": job["error"],
        "error_detail": job.get("error_detail"),
    })


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def job_cancel(job_id: str):
    """Give up on a running job and release its slot.

    The worker runs on a daemon thread that cannot be killed, so this is an
    abandonment, not a kill: the job is marked cancelled, ``_update_job``
    refuses to write to it afterwards (so a late-finishing worker cannot
    resurrect it), and the concurrency gate stops counting it. Say that plainly
    rather than implying the provider calls stop immediately.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.get("status") != "running":
            return jsonify({"job_id": job_id, "status": job.get("status"),
                            "cancelled": False,
                            "message": "Job already finished."}), 200
        job.update({
            "status": "error",
            "error": "Scan cancelled.",
            "error_detail": {"reason": "cancelled"},
            "message": "Cancelled",
            "cancelled": True,
            "finished_at": time.time(),
            # [I4] The stage list ends here too: none is left running.
            "stage": _terminal_stage("cancelled", dict(job.get("stage_log") or {})),
        })
    logger.info("Job %s cancelled by client request", job_id)
    return jsonify({
        "job_id": job_id,
        "status": "error",
        "cancelled": True,
        # [F07] The worker stops at its next stage boundary and keeps its
        # admission slot until then, so a replacement scan is not admitted on
        # top of one that is still calling the provider.
        "slot_released": not _active_scan_workers(),
        "message": ("Scan cancelled. Work already in flight stops at the next "
                    "stage boundary and its result is discarded; the scan slot "
                    "frees up when that happens."),
    }), 200


# A comment line every 15 s while a stage is silent: writing is how the server
# notices a client that has gone away (and keeps proxies from idling it out).
_SSE_HEARTBEAT_SECONDS = 15.0


@app.route("/api/jobs/<job_id>/stream")
def job_stream(job_id: str):
    """Server-Sent Events stream for real-time progress.
    
    IMPORTANT: Does NOT send the full result payload (too large for SSE).
    On completion, sends {status:'complete'} and the client fetches
    the result via GET /api/jobs/<id>.
    """
    def generate():
        last_seen = None
        last_sent = time.monotonic()
        while True:
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                break
            stage = job.get("stage") or {}
            seen = (job["progress"], stage.get("key"), stage.get("done"))
            if seen != last_seen or job["status"] in ("complete", "error"):
                last_seen = seen
                payload = {
                    "status": job["status"],
                    "progress": job["progress"],
                    "message": job["message"],
                }
                if stage:
                    payload["stage"] = stage
                # Do NOT include result in SSE — payload too large.
                # Client will fetch via GET /api/jobs/<id> on completion.
                if job["status"] == "error":
                    payload["error"] = job["error"]
                    # [PROVIDER-RESILIENCE] Include the structured rate-limit
                    # detail so the tear-sheet UI can render the cooldown banner
                    # without an extra round-trip.
                    if job.get("error_detail"):
                        payload["error_detail"] = job["error_detail"]
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                last_sent = time.monotonic()
                if job["status"] in ("complete", "error"):
                    break
            elif time.monotonic() - last_sent >= _SSE_HEARTBEAT_SECONDS:
                # A comment line: keeps a slow stage's connection from being
                # closed as idle by the server or a proxy; EventSource ignores it.
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# [F07] Worker ownership, cooperative cancellation and runtime retirement
# ---------------------------------------------------------------------------
# A job's STATUS is what the client sees; a job's WORKER is what is actually
# consuming the provider. Cancelling flips the status to terminal immediately,
# but the daemon thread cannot be killed and keeps scanning. Admission must
# therefore count live workers, not "running" statuses — otherwise a cancel
# instantly frees a slot and a second scan piles onto the first.

def _worker_slot_held(job: Dict[str, Any], now: float) -> bool:
    """True while this job's worker still owns a scan slot.

    Ownership ends when the worker returns (`_release_job_worker`), NOT when
    the job reaches a terminal status — cancellation and the runtime-budget
    abandonment both mark a job terminal while its thread keeps scanning.

    A response timeout cannot release resources still owned by a live thread.
    If a provider never returns, server restart is required for recovery.
    """
    return bool(job.get("worker_active"))


def _active_scan_workers() -> List[str]:
    """Job ids whose scan worker has not yet returned, whatever their status."""
    now = time.time()
    with _jobs_lock:
        return [
            jid for jid, job in _jobs.items()
            if job.get("type") == "stock-picks" and _worker_slot_held(job, now)
        ]


def _release_job_worker(job_id: str) -> None:
    """Mark a worker as finished. Must run in the worker's own `finally`."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["worker_active"] = False


def _job_is_cancelled(job_id: str) -> bool:
    """True when the worker should stop between stages.

    A job that has been cancelled, or pruned away entirely, has nobody left to
    consume its result — continuing only burns provider quota.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return True
        return bool(job.get("cancelled")) or job.get("status") != "running"


def _retire_analysis_runtime() -> None:
    """Drop the shared analyzer so the next request builds a fresh one.

    [F07] On a wall-clock timeout the abandoned thread keeps running against
    the analyzer it already holds. Leaving that same object in
    `_analysis_runtime` handed it to the NEXT request too, so two live analyses
    mutated one analyzer. Retiring the runtime lets the abandoned worker finish
    against its own copy while new work starts clean.
    """
    _analysis_runtime.update({
        "module": None, "analyzer": None,
        "api_client": None, "config_key": None,
    })


def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            job = _jobs[job_id]
            # Terminal is terminal. A job cancelled by the client, or abandoned
            # for exceeding the runtime budget, must not be resurrected by a
            # worker that finishes (or reports progress) afterwards.
            if job.get("status") in ("complete", "error"):
                return
            # [A13] Progress only moves forward: a stage callback can run ahead
            # of the worker's fixed milestones.
            if "progress" in kwargs and isinstance(job.get("progress"), (int, float)):
                kwargs["progress"] = max(job["progress"], kwargs["progress"])
            job.update(kwargs)
            # Stamp completion so _prune_jobs can age the entry out.
            if kwargs.get("status") in ("complete", "error") and not job.get("finished_at"):
                job["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# My Portfolio (read-only Robinhood holdings; see portfolio_routes.py)
# ---------------------------------------------------------------------------

def _portfolio_analyze(symbol: str, account_size: Optional[float]):
    """Research one holding through the SAME path as /api/analyze.

    Full mode, US market, sized against the real account value. Runs in the
    portfolio job's thread, so holdings are analysed one after another behind
    the shared analyzer lock, cache and provider cooldown; a fresh full-mode
    result already in the cache is reused rather than recomputed.
    """
    ticker = (symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9^.=&-]{1,32}", ticker):
        return None, {"status": "invalid_ticker", "title": f"{ticker[:32]} is not a supported symbol"}
    if account_size is not None and math.isfinite(account_size) and account_size >= MIN_ACCOUNT_SIZE:
        size, assumed = min(float(account_size), MAX_ACCOUNT_SIZE), False
    else:
        size, assumed = DEFAULT_ACCOUNT_SIZE, True
    # Background work yields to anyone waiting on a tear sheet.
    _wait_for_interactive_requests()
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "analyze", "origin": "portfolio", "worker_active": True,
            "ticker": ticker, "market": "US", "fast_mode": False,
            "account_size": size, "account_size_assumed": assumed,
            "status": "running", "progress": 0, "message": f"Analysing {ticker} for My Portfolio…",
            "result": None, "error": None, "error_detail": None,
            "started": _now_iso(), "started_at": time.time(),
        }
    # prefer_cache stays False: a FRESH full-mode cache hit is already reused
    # by _run_analysis; prefer_cache would additionally serve STALE results,
    # which must never drive guidance on real holdings.
    _run_analysis_worker(job_id, ticker, safe_mode=False, prefer_cache=False, fast_mode=False,
                         account_size=size, account_size_assumed=assumed, market="US")
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if job.get("status") == "complete" and job.get("result"):
        return job["result"], None
    return None, job.get("error_detail") or {"status": "engine_error",
                                              "title": job.get("error") or "Analysis did not complete"}


def _portfolio_returns(symbols: List[str]):
    """One year of daily returns for the holdings, or None. Market data (Yahoo)."""
    if not symbols or _provider_in_cooldown():
        return None
    import pandas as pd
    import yfinance as yf
    yahoo = {s: s.replace(".", "-") for s in symbols}
    try:
        frame = yf.download(sorted(set(yahoo.values())), period="1y", interval="1d",
                            auto_adjust=True, progress=False, threads=False, group_by="column")
    except Exception as exc:  # noqa: BLE001
        if _provider_classify_rate_limit(exc):
            _provider_note_rate_limit()
        logger.info("Portfolio returns download failed: %s", type(exc).__name__)
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    closes = frame["Close"] if "Close" in frame else frame
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=next(iter(yahoo.values())))
    back = {v: k for k, v in yahoo.items()}
    closes = closes.rename(columns=lambda c: back.get(str(c), str(c)))
    return closes.pct_change(fill_method=None).iloc[1:]


def _portfolio_prices(symbols: List[str], period: str):
    """Daily closes (split-adjusted, dividends excluded) for holdings plus the benchmark, or None.

    Unadjusted for dividends on purpose: holdings and the benchmark are compared on the same
    price-return basis. Market data (Yahoo); never broker data.
    """
    if not symbols or _provider_in_cooldown():
        return None
    import yfinance as yf
    yahoo = {s: s.replace(".", "-") for s in symbols}
    try:
        frame = yf.download(sorted(set(yahoo.values())), period=period, interval="1d",
                            auto_adjust=False, progress=False, threads=False, group_by="column")
    except Exception as exc:  # noqa: BLE001
        if _provider_classify_rate_limit(exc):
            _provider_note_rate_limit()
        logger.info("Portfolio price history download failed: %s", type(exc).__name__)
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    closes = frame["Close"] if "Close" in frame else frame
    if hasattr(closes, "to_frame") and not hasattr(closes, "columns"):
        closes = closes.to_frame(name=next(iter(yahoo.values())))
    back = {v: k for k, v in yahoo.items()}
    closes = closes.rename(columns=lambda c: back.get(str(c), str(c)))
    closes.index = [d.date() if hasattr(d, "date") else d for d in closes.index]
    return closes


def _portfolio_intraday(symbols: List[str]) -> Optional[Dict[str, Any]]:
    """The latest session's 5-minute last prices, pre- and post-market included, in New York time.

    For the 1D chart only. Market data (Yahoo); never broker data. None when unavailable.
    """
    if not symbols or _provider_in_cooldown():
        return None
    import yfinance as yf
    yahoo = {s: s.replace(".", "-") for s in symbols}
    try:
        frame = yf.download(sorted(set(yahoo.values())), period="5d", interval="5m", prepost=True,
                            auto_adjust=False, progress=False, threads=False, group_by="column")
    except Exception as exc:  # noqa: BLE001
        if _provider_classify_rate_limit(exc):
            _provider_note_rate_limit()
        logger.info("Portfolio intraday download failed: %s", type(exc).__name__)
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    closes = frame["Close"] if "Close" in frame else frame
    if not hasattr(closes, "columns"):
        closes = closes.to_frame(name=next(iter(yahoo.values())))
    try:
        idx = closes.index
        idx = idx.tz_convert("America/New_York") if idx.tz is not None else idx.tz_localize("UTC").tz_convert("America/New_York")
    except Exception:  # noqa: BLE001 - an index without times is not intraday data
        return None
    closes.index = idx
    last_day = max(d.date() for d in idx)
    day = closes[[d.date() == last_day for d in idx]]
    back = {v: k for k, v in yahoo.items()}
    prices = {back.get(str(c), str(c)): [None if v != v else round(float(v), 4) for v in day[c].tolist()]
              for c in day.columns}
    return {"date": last_day.isoformat(), "stamps": [d.isoformat() for d in day.index], "prices": prices}


def _portfolio_profiles(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Country, fund type and category from Yahoo company profiles (undated market data)."""
    if not symbols or _provider_in_cooldown():
        return {}
    import yfinance as yf

    def one(sym: str):
        try:
            info = yf.Ticker(sym.replace(".", "-")).get_info() or {}
        except Exception:  # noqa: BLE001
            return sym, {}
        return sym, {"country": info.get("country"), "quote_type": info.get("quoteType"),
                     "category": info.get("category"), "sector": info.get("sector"),
                     "name": info.get("longName") or info.get("shortName")}
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(one, symbols))


_portfolio_news_cache: Dict[str, Any] = {"at": 0.0, "articles": []}
PORTFOLIO_NEWS_TTL = 600


def _portfolio_articles() -> List[Dict[str, Any]]:
    """US headlines (the same RSS digest the dashboard builds), cached for ten minutes.

    Built directly rather than through _dashboard_payload, which reads request
    arguments and rebuilds the market tape; a feed failure yields no headlines.
    """
    now = time.time()
    if now - _portfolio_news_cache["at"] < PORTFOLIO_NEWS_TTL:
        return list(_portfolio_news_cache["articles"])
    try:
        articles = list((_build_news_digest("US") or {}).get("articles") or [])
    except Exception as exc:  # noqa: BLE001
        logger.info("Portfolio headlines unavailable: %s", type(exc).__name__)
        articles = []
    _portfolio_news_cache.update(at=now, articles=articles)
    return articles


try:
    from portfolio_routes import init_portfolio as _init_portfolio
    from portfolio.insights import MarketSources as _MarketSources
    from portfolio.journal import DecisionJournal as _DecisionJournal
    from portfolio.sources import EdgarClient as _EdgarClient, load_official_calendar as _load_calendar
    _init_portfolio(app, _portfolio_analyze, _portfolio_returns, sources=_MarketSources(
        prices=_portfolio_prices, intraday=_portfolio_intraday, profiles=_portfolio_profiles, articles=_portfolio_articles,
        edgar=_EdgarClient(), calendar=_load_calendar, journal=_DecisionJournal()))
except Exception as _portfolio_exc:  # noqa: BLE001 - the rest of QUANTA must still start
    logger.error("My Portfolio unavailable: %s", _portfolio_exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _port_in_use(host: str, port: int) -> bool:
    """True when something already accepts connections on host:port."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _claim_ports_exclusively() -> None:
    """Make the dev server own its port on Windows.

    werkzeug binds with SO_REUSEADDR. On Windows that option does not just
    allow a quick restart: it lets a second process bind a port another server
    is listening on. Both print "Running on ...", and which one answers a
    request is undefined — so a page could be served by this server and its
    next request by an old checkout, or by nothing once one of them stops
    ("Cannot reach the QUANTA server"). SO_EXCLUSIVEADDRUSE refuses any second
    bind while this server runs; an immediate restart still works.
    """
    import socket
    if sys.platform != "win32" or not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        return
    from werkzeug import serving
    base = serving.BaseWSGIServer
    if getattr(base, "_quanta_exclusive", False):
        return
    original_bind = base.server_bind

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        original_bind(self)

    base.allow_reuse_address = False
    base.server_bind = server_bind
    base._quanta_exclusive = True


def _listening_socket(host: str, port: int):
    """A bound socket that only this server can own (see _claim_ports_exclusively)."""
    import socket
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


def _serve(host: str, port: int) -> None:
    """Serve with waitress (a production WSGI server) when installed.

    Flask's development server remains the fallback, or the choice with
    QUANTA_SERVER=dev. waitress streams the job progress events unbuffered; it
    gets a pre-bound socket because its own bind sets SO_REUSEADDR, which on
    Windows would let a second server share the port.
    """
    if os.getenv("QUANTA_SERVER", "").lower() != "dev":
        try:
            from waitress import serve
        except ImportError:
            logger.info("waitress is not installed; using the Flask development server")
        else:
            try:
                sock = _listening_socket(host, port)
            except OSError as exc:  # taken between the port check and the bind
                print(f"QUANTA is not starting: cannot bind http://{host}:{port} ({exc}). "
                      "Stop the other server, or set QUANTA_PORT to a free port.", file=sys.stderr)
                sys.exit(1)
            serve(app, sockets=[sock], threads=32, channel_timeout=300, ident="QUANTA")
            return
    _claim_ports_exclusively()
    app.run(host=host, port=port, debug=False, threaded=True)


def main():
    port = int(os.getenv("QUANTA_PORT", 5500))
    host = os.getenv("QUANTA_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "Refusing to expose the unauthenticated QUANTA Flask app on a "
            "non-loopback host. Deploy behind authenticated access control."
        )

    if _port_in_use(host, port):
        print(
            f"QUANTA is not starting: http://{host}:{port} is already in use by another "
            "server (often an earlier QUANTA that is still running, possibly from a "
            "different checkout). Stop it, or set QUANTA_PORT to a free port.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info(f"QUANTA Web starting on http://{host}:{port}")
    _sec_contact_startup_check()

    # Auto-open browser
    if os.getenv("QUANTA_NO_BROWSER") != "1":
        opener = threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}"))
        opener.daemon = True  # a failed start must not still open a tab
        opener.start()

    _serve(host, port)


if __name__ == "__main__":
    main()
