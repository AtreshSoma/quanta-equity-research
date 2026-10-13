"""Point-in-time data layer.

Guarantees
----------
1. No look-ahead: ``get_history(ticker, as_of)`` returns ONLY bars whose
   timestamp is <= ``as_of``. The same ticker queried for an earlier date is a
   strict prefix of the later query (proven by tests/test_leakage.py).
2. Reproducibility: every ticker's full history is downloaded once and cached
   to local parquet. Subsequent runs read the cache, so a run is byte-for-byte
   stable. ``run_data_hash`` returns a SHA-256 over all bars used in a run.
3. Data-quality gate: ``get_history`` raises ``DataQualityError`` (caller skips
   the ticker for that date) when required fields are missing or the warmup
   window is too short. Tickers are never silently scored with a neutral value.
4. Throttling: downloads are serialized with a fixed delay to avoid
   rate-limited, run-dependent results. Primary source is yfinance; Stooq is
   the fallback.

This module is intentionally price/volume only. A ``FundamentalsProvider``
abstract hook is defined so a true point-in-time vendor (SimFin, Sharadar, FMP,
pre-downloaded snapshots) can be added later WITHOUT touching strategy code.
Until such a provider is wired in, fundamentals are unavailable by contract.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger("apex_quant.data")

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
_CACHE_DIR_DEFAULT = Path(__file__).resolve().parent / "_cache"

# A SEPARATE cache for research runs. Thirteen parquet files in `_cache` are
# committed to git and serve as frozen test fixtures --
# `tests/test_recommendation_trace.py` asserts AAPL ends 2023-12-29 at close
# 192.52999877929688. Refreshing `_cache` to pick up recent data therefore
# breaks that test, which is exactly what happened once. Research runs that want
# current data must use this directory instead, so the fixtures stay frozen.
_CACHE_DIR_RESEARCH = Path(__file__).resolve().parent / "_cache_research"


class DataQualityError(Exception):
    """Raised when a ticker fails the data-quality gate for an as-of date."""


class FundamentalsProvider(ABC):
    """Abstract point-in-time fundamentals hook.

    Deliberately unimplemented: no validated PIT fundamentals source is
    configured. A concrete adapter (SimFin/Sharadar/FMP/local snapshots) must
    return fundamentals *as they were known on or before* ``as_of`` — never
    restated/current values. Strategy code must treat absence as "no data",
    not as a neutral score.
    """

    @abstractmethod
    def get_fundamentals(self, ticker: str, as_of: pd.Timestamp) -> Dict[str, float]:
        raise NotImplementedError


class DataProvider(ABC):
    """Point-in-time OHLCV provider interface."""

    @abstractmethod
    def get_history(
        self, ticker: str, as_of: pd.Timestamp, min_bars: int = 250
    ) -> pd.DataFrame:
        """Return OHLCV bars with index <= ``as_of`` (ascending).

        Raises ``DataQualityError`` if fewer than ``min_bars`` clean bars exist
        on/before ``as_of`` or required columns are missing.
        """

    @abstractmethod
    def get_full(self, ticker: str) -> pd.DataFrame:
        """Return the full cached OHLCV history (all dates)."""


@dataclass
class CachedPriceProvider(DataProvider):
    """yfinance-primary, Stooq-fallback provider with local parquet cache.

    All network access happens lazily and exactly once per ticker per process
    (then served from cache), so repeated ``get_history`` calls are cheap and
    deterministic.
    """

    start: str = "2010-01-01"
    end: Optional[str] = None
    cache_dir: Path = field(default_factory=lambda: _CACHE_DIR_DEFAULT)
    throttle_seconds: float = 0.5
    _mem: Dict[str, pd.DataFrame] = field(default_factory=dict, init=False)
    _used_hashes: Dict[str, str] = field(default_factory=dict, init=False)
    _last_download: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- public ------------------------------------------------------------

    def get_full(self, ticker: str) -> pd.DataFrame:
        if ticker in self._mem:
            return self._mem[ticker]
        df = self._load_cached(ticker)
        if df is None:
            df = self._download(ticker)
            self._save_cache(ticker, df)
        self._mem[ticker] = df
        # Record the hash of the canonical bytes actually loaded this run.
        self._used_hashes[ticker] = _frame_hash(df)
        return df

    def get_history(
        self, ticker: str, as_of: pd.Timestamp, min_bars: int = 250
    ) -> pd.DataFrame:
        as_of = pd.Timestamp(as_of).normalize()
        full = self.get_full(ticker)
        # POINT-IN-TIME SLICE: only bars at/before the as-of close are visible.
        hist = full.loc[full.index <= as_of]
        self._quality_gate(ticker, as_of, hist, min_bars)
        return hist

    def run_data_hash(self) -> str:
        """SHA-256 over every ticker's cached bytes touched this run.

        Hash is independent of query order (tickers are sorted), so two runs
        that touch the same data on the same date produce the same hash.
        """
        h = hashlib.sha256()
        for tk in sorted(self._used_hashes):
            h.update(tk.encode("utf-8"))
            h.update(self._used_hashes[tk].encode("utf-8"))
        return h.hexdigest()

    # -- internals ---------------------------------------------------------

    def _quality_gate(
        self, ticker: str, as_of: pd.Timestamp, hist: pd.DataFrame, min_bars: int
    ) -> None:
        if hist.empty:
            raise DataQualityError(f"{ticker}: no bars on/before {as_of.date()}")
        missing = [c for c in REQUIRED_COLUMNS if c not in hist.columns]
        if missing:
            raise DataQualityError(f"{ticker}: missing columns {missing}")
        window = hist.iloc[-min_bars:] if len(hist) >= min_bars else hist
        if len(hist) < min_bars:
            raise DataQualityError(
                f"{ticker}: only {len(hist)} bars on/before {as_of.date()} "
                f"(need {min_bars})"
            )
        if window[list(REQUIRED_COLUMNS)].isna().any().any():
            raise DataQualityError(
                f"{ticker}: NaNs in required window ending {as_of.date()}"
            )
        if (window["Close"] <= 0).any() or (window["Volume"] < 0).any():
            raise DataQualityError(f"{ticker}: non-positive price/negative volume")

    def _cache_path(self, ticker: str) -> Path:
        safe = ticker.replace("/", "_").replace(".", "_")
        return self.cache_dir / f"{safe}.parquet"

    def _load_cached(self, ticker: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(ticker)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            return _normalize(df)
        except Exception as e:  # pragma: no cover - corrupt cache
            logger.warning("%s: cache read failed (%s); re-downloading", ticker, e)
            return None

    def _save_cache(self, ticker: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._cache_path(ticker))
        except Exception as e:  # pragma: no cover
            logger.warning("%s: cache write failed (%s)", ticker, e)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_download
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_download = time.monotonic()

    def _download(self, ticker: str) -> pd.DataFrame:
        self._throttle()
        df = self._download_yfinance(ticker)
        # Yahoo answers a throttled request with an empty frame ("possibly
        # delisted"); one backed-off retry recovers most of those without making
        # a genuinely delisted ticker cost more than a few seconds.
        for wait in (8.0,):
            if df is not None and not df.empty:
                break
            logger.warning("%s: yfinance empty; retrying in %.0fs", ticker, wait)
            time.sleep(wait)
            df = self._download_yfinance(ticker)
        if df is None or df.empty:
            logger.warning("%s: yfinance empty, trying Stooq", ticker)
            df = self._download_stooq(ticker)
        if df is None or df.empty:
            raise DataQualityError(f"{ticker}: no data from any source")
        return _normalize(df)

    def _download_yfinance(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf

            df = yf.download(
                ticker,
                start=self.start,
                end=self.end,
                progress=False,
                auto_adjust=False,
            )
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception as e:  # pragma: no cover - network
            logger.warning("%s: yfinance error %s", ticker, e)
            return None

    def _download_stooq(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            import requests

            sym = ticker.lower()
            if "." not in sym:
                sym = f"{sym}.us"
            url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
            resp = requests.get(url, timeout=20)
            if resp.status_code != 200 or not resp.text.startswith("Date"):
                return None
            df = pd.read_csv(io.StringIO(resp.text), parse_dates=["Date"])
            df = df.set_index("Date").sort_index()
            return df
        except Exception as e:  # pragma: no cover - network
            logger.warning("%s: stooq error %s", ticker, e)
            return None


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize to ascending DatetimeIndex with REQUIRED_COLUMNS."""
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    cols = {c.title(): c for c in out.columns}
    rename = {}
    for want in REQUIRED_COLUMNS:
        if want not in out.columns and want in cols:
            rename[cols[want]] = want
    if rename:
        out = out.rename(columns=rename)
    keep = [c for c in REQUIRED_COLUMNS if c in out.columns]
    return out[keep]


def _frame_hash(df: pd.DataFrame) -> str:
    """Stable SHA-256 of a frame's canonical CSV bytes."""
    buf = df.sort_index().to_csv(float_format="%.6f").encode("utf-8")
    return hashlib.sha256(buf).hexdigest()
