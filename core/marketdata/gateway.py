"""One way to ask for market data, with provenance and an ordered fallback.

QUANTA reaches Yahoo Finance from about eighty call sites. Pacing, caching and
stale-grace work because they are installed at the HTTP layer, but each call
site decides on its own what to do when Yahoo returns nothing, and none of them
can say afterwards which provider answered. This gateway is the start of one
path: a caller names what it wants, the gateway tries the providers that can
answer it in order, checks the answer, and returns it in an :class:`Envelope`
that says who served it, when, and anything the caller should know.

The shape follows OpenBB's three-step fetcher (query -> extract -> transform)
without the dependency. Unlike OpenBB's executor, which calls exactly one named
provider, the gateway falls back: Yahoo first, then Stooq where Stooq can answer
the same question.

Stooq serves unadjusted US daily bars. It is used only for requests that ask
for unadjusted prices, only for US listings, and a frame with a split-sized
jump is refused (yfinance adjusts splits even with ``auto_adjust=False``, so an
unadjusted split would not be the same series). After repeated failures Stooq
is switched off for the rest of the process, as the short-term scanner does.
"""
from __future__ import annotations

import io
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

OHLCV = ("Open", "High", "Low", "Close", "Volume")
STOOQ_TIMEOUT = 8.0
STOOQ_FAILURE_LIMIT = 3
_PERIOD_DAYS = {"1y": 366, "2y": 731, "5y": 1827, "10y": 3653}


class DataUnavailable(RuntimeError):
    """No provider could answer; ``attempts`` says what each one returned."""

    def __init__(self, message: str, attempts: List[Dict[str, str]]):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class Envelope:
    data: Any
    provider: str
    as_of: Optional[str]                      # market date of the latest row
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: List[str] = field(default_factory=list)
    attempts: List[Dict[str, str]] = field(default_factory=list)

    def provenance(self) -> Dict[str, Any]:
        """The envelope without its data: what a response or a ledger row records."""
        return {"provider": self.provider, "as_of": self.as_of, "retrieved_at": self.retrieved_at,
                "warnings": list(self.warnings), "attempts": list(self.attempts)}


@dataclass(frozen=True)
class BarsQuery:
    symbol: str
    period: str = "5y"
    start: Optional[date] = None
    end: Optional[date] = None                # inclusive
    adjusted: bool = False                    # dividend-adjusted closes (yfinance auto_adjust)

    @property
    def us_listing(self) -> bool:
        return not self.symbol.upper().endswith((".NS", ".BO")) and not self.symbol.startswith("^")

    def window(self) -> tuple:
        end = self.end or date.today()
        start = self.start or end - timedelta(days=_PERIOD_DAYS.get(self.period, 1827))
        return start, end


# ------------------------------------------------------------------ providers
def _yfinance_bars(q: BarsQuery) -> pd.DataFrame:
    import yfinance as yf  # imported per call: tests swap the module

    if q.start is None and q.end is None:
        return yf.Ticker(q.symbol).history(period=q.period, interval="1d", auto_adjust=q.adjusted)
    start, end = q.window()
    frame = yf.download(q.symbol, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                        auto_adjust=q.adjusted, progress=False, threads=False)
    if frame is not None and isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    return frame


_stooq_lock = threading.Lock()
_stooq_failures = 0


def _stooq_get(url: str) -> str:
    import requests
    resp = requests.get(url, timeout=STOOQ_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def _stooq_bars(q: BarsQuery) -> pd.DataFrame:
    global _stooq_failures
    if q.adjusted:
        raise LookupError("Stooq serves unadjusted prices only")
    if not q.us_listing:
        raise LookupError("Stooq fallback covers US listings only")
    with _stooq_lock:
        if _stooq_failures >= STOOQ_FAILURE_LIMIT:
            raise LookupError("Stooq switched off after repeated failures")
    start, end = q.window()
    url = (f"https://stooq.com/q/d/l/?s={q.symbol.lower().replace('-', '.')}.us&i=d"
           f"&d1={start:%Y%m%d}&d2={end:%Y%m%d}")
    try:
        text = STOOQ_GET(url)
    except Exception:
        with _stooq_lock:
            _stooq_failures += 1
        raise
    with _stooq_lock:
        _stooq_failures = 0
    frame = pd.read_csv(io.StringIO(text))
    if frame.empty or "Date" not in frame.columns:
        return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date"]).set_index("Date").sort_index()
    if any(c not in frame.columns for c in OHLCV):
        return pd.DataFrame()
    closes = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    step = (closes / closes.shift(1)).dropna()
    if len(step) and (step.max() > 1.8 or step.min() < 0.55):
        raise LookupError("Stooq series has a split-sized jump (unadjusted across a split)")
    return frame[list(OHLCV)]


# The fetcher Stooq requests go through; tests replace it.
STOOQ_GET: Callable[[str], str] = _stooq_get

BAR_PROVIDERS: Dict[str, Callable[[BarsQuery], pd.DataFrame]] = {
    "yfinance": _yfinance_bars,
    "stooq": _stooq_bars,
}
DEFAULT_BAR_ORDER = ("yfinance", "stooq")


# ------------------------------------------------------------------- checking
def check_bars(frame: Any) -> Optional[str]:
    """Why a frame is not usable daily bars; None when it is."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return "no rows"
    if "Close" not in frame.columns:
        return "no Close column"
    closes = pd.to_numeric(frame["Close"], errors="coerce")
    if closes.notna().sum() == 0:
        return "no usable closes"
    if (closes.dropna() <= 0).any():
        return "non-positive closes"
    return None


def _last_date(frame: pd.DataFrame) -> Optional[str]:
    try:
        return pd.Timestamp(frame.index.max()).date().isoformat()
    except (TypeError, ValueError):
        return None


def price_bars(symbol: str, period: str = "5y", *, start: Optional[date] = None,
               end: Optional[date] = None, adjusted: bool = False,
               providers: Sequence[str] = DEFAULT_BAR_ORDER) -> Envelope:
    """Daily OHLCV bars for ``symbol`` from the first provider that answers usably.

    ``providers`` is the order to try; pass ``("yfinance",)`` where mixing
    providers would change what a number means (for example a ledger outcome and
    its benchmark must come from one source). Raises :class:`DataUnavailable`.
    """
    q = BarsQuery(symbol=str(symbol).strip().upper(), period=period, start=start, end=end,
                  adjusted=adjusted)
    attempts: List[Dict[str, str]] = []
    for name in providers:
        fetch = BAR_PROVIDERS.get(name)
        if fetch is None:
            attempts.append({"provider": name, "result": "unknown provider"})
            continue
        try:
            frame = fetch(q)
        except Exception as exc:  # noqa: BLE001 - each provider failure is recorded, then the next is tried
            attempts.append({"provider": name, "result": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        problem = check_bars(frame)
        if problem:
            attempts.append({"provider": name, "result": problem})
            continue
        attempts.append({"provider": name, "result": "ok"})
        warnings = []
        if name != providers[0]:
            warnings.append(f"Served by {name}: {providers[0]} did not answer "
                            f"({attempts[0]['result']}).")
        if name == "stooq":
            warnings.append("Stooq bars are not dividend-adjusted.")
        return Envelope(data=frame, provider=name, as_of=_last_date(frame), warnings=warnings,
                        attempts=attempts)
    logger.info("price bars unavailable for %s: %s", q.symbol, attempts)
    raise DataUnavailable(f"No provider returned daily bars for {q.symbol}.", attempts)
