"""Which daily bar a score may read: the last completed, settled session.

Why this exists
---------------
A daily bar is final only after its session closes, and even then the data
vendor keeps correcting it for a while (the closing auction, late prints).
Every score is validated on completed bars (the apex_quant backtests use them),
so an indicator that feeds a score must not read today's still-forming candle.
Otherwise the same ticker scores differently at 10:00, 14:00 and after the
close, and none of those readings is the one the model was tested on.

What stays live
---------------
Prices a user acts on (current price, entry, stop distance from the price,
reward-to-risk at the price, position sizing) are NOT routed through here.
They keep the live quote. Only the inputs to a score are restricted to completed
bars, and a live price is compared with those completed-bar levels as usual.

The rule
--------
The newest bar is LIVE (excluded from scoring) when it is:

* dated after today in the exchange's zone (a clock or timezone mismatch), or
* dated today and today is a session whose close plus the settle window
  (``thresholds.RESEARCH.BAR_SETTLE_MINUTES``) has not yet passed, or
* dated today on a day the market is closed (a stray bar; dropped, as before).

Any bar dated before today is complete. A weekend or holiday needs no special
case: the newest bar is then the previous session, which is already complete.

Pure computation: no network, no global clock patching. Pass ``now`` to pin it.
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from .market_calendar import (
    SESSION_CLOSE,
    early_close_time,
    is_india_trading_day,
    is_trading_day,
    listing_market,
    now_india,
    now_new_york,
)
from .thresholds import RESEARCH

_NY_ZONE = "America/New_York"
_IN_ZONE = "Asia/Kolkata"
_INDIA_TZ_NAMES = ("Asia/Kolkata", "Asia/Calcutta")


def market_of(index: Any = None, ticker: str = "") -> str:
    """'IN' for an NSE/BSE listing (by suffix, else by an Asia/Kolkata index), else 'US'."""
    if listing_market(ticker) == "IN":
        return "IN"
    tz = getattr(index, "tz", None)
    if tz is not None and str(tz) in _INDIA_TZ_NAMES:
        return "IN"
    return "US"


def _bar_date(stamp: pd.Timestamp, market: str) -> _date:
    """The calendar date a bar belongs to, read in its exchange's zone.

    A tz-naive stamp already is the exchange date. An aware one is converted, so
    a bar stamped at midnight New York is dated the New York day it belongs to.
    """
    if stamp.tzinfo is not None:
        return stamp.tz_convert(_IN_ZONE if market == "IN" else _NY_ZONE).date()
    return stamp.date()


def _local_now(market: str, now: Optional[datetime]) -> datetime:
    return now_india(now) if market == "IN" else now_new_york(now)


def _session_close(market: str, day: _date) -> time:
    """The regular close of *day*: NSE 15:30 IST, or NYSE 16:00 (13:00 on a half-day)."""
    if market == "IN":
        close_h, close_m = RESEARCH.INDIA_SESSION_CLOSE_IST
        return time(int(close_h), int(close_m))
    return early_close_time(day) or SESSION_CLOSE


def _is_session(market: str, day: _date) -> bool:
    return is_india_trading_day(day) if market == "IN" else is_trading_day(day)


def _newest_bar_status(stamp: pd.Timestamp, market: str, now: Optional[datetime],
                       settle_minutes: float) -> Tuple[bool, str]:
    """(is the newest bar live, plain-English reason) for the bar stamped *stamp*."""
    local = _local_now(market, now)
    today = local.date()
    zone = "IST" if market == "IN" else "NY"
    bar_day = _bar_date(stamp, market)

    if bar_day > today:
        return True, "newest bar is dated after today (clock or timezone mismatch): excluded"
    if bar_day < today:
        return False, "newest bar is a completed session"
    if not _is_session(market, today):
        return True, "newest bar is dated on a day the market is closed: excluded"

    close_t = _session_close(market, today)
    close_at = datetime.combine(today, close_t, tzinfo=local.tzinfo)
    settled_at = close_at + timedelta(minutes=settle_minutes)
    if local < close_at:
        return True, (f"today's session is still trading (closes {close_t:%H:%M} {zone}): "
                      "today's bar excluded")
    if local < settled_at:
        return True, (f"today's session closed at {close_t:%H:%M} {zone}; the bar settles at "
                      f"{settled_at:%H:%M} {zone}: today's bar excluded")
    return False, "newest bar is a completed, settled session"


def _settle(settle_minutes: Optional[float]) -> float:
    return float(RESEARCH.BAR_SETTLE_MINUTES if settle_minutes is None else settle_minutes)


def last_bar_is_live(data: Any, ticker: str = "", now: Any = None,
                     settle_minutes: Optional[float] = None,
                     market: Optional[str] = None) -> bool:
    """True when the newest daily bar is still forming or not yet settled.

    ``now`` defaults to the wall clock; pass a datetime to pin it (tests, replays).
    ``settle_minutes`` defaults to ``RESEARCH.BAR_SETTLE_MINUTES``; 0 means the bar
    is complete the moment the session closes. ``market`` ("US" or "IN") overrides
    the detection from ticker and index.
    """
    try:
        if data is None or len(data) == 0:
            return False
        stamp = pd.Timestamp(data.index[-1])
    except (TypeError, ValueError, IndexError, AttributeError):
        return False
    market = market or market_of(data.index, ticker)
    live, _ = _newest_bar_status(stamp, market, now, _settle(settle_minutes))
    return live


def scoring_bars(data: Any, ticker: str = "", now: Any = None,
                 settle_minutes: Optional[float] = None,
                 market: Optional[str] = None) -> Tuple[Any, Dict[str, Any]]:
    """``(frame, basis)``: the bars a score may read, and what they are.

    ``frame`` is ``data`` without a live or unsettled newest bar (``data`` itself
    when that bar is complete). ``basis`` is JSON-ready:

    * ``scored_through``: the date of the newest bar in ``frame`` (YYYY-MM-DD),
      or None when no completed bar exists;
    * ``live_bar_excluded``: whether a bar was dropped;
    * ``reason``: why, in plain English, for the UI ("Indicators as of ... close").
    """
    if data is None or len(data) == 0:
        return data, {"scored_through": None, "live_bar_excluded": False,
                      "reason": "no daily bars"}
    try:
        stamp = pd.Timestamp(data.index[-1])
    except (TypeError, ValueError, AttributeError):
        return data, {"scored_through": None, "live_bar_excluded": False,
                      "reason": "bar dates are unreadable"}
    market = market or market_of(data.index, ticker)
    live, reason = _newest_bar_status(stamp, market, now, _settle(settle_minutes))
    frame = data.iloc[:-1] if live else data
    if len(frame) == 0:
        return frame, {"scored_through": None, "live_bar_excluded": True,
                       "reason": reason + "; no completed bar exists yet"}
    last = pd.Timestamp(frame.index[-1])
    return frame, {"scored_through": _bar_date(last, market).isoformat(),
                   "live_bar_excluded": live, "reason": reason}


def live_preview(data: Any, basis: Dict[str, Any]) -> Dict[str, Any]:
    """Today's still-forming bar, shown beside the scores that exclude it.

    Empty unless ``basis`` says the newest bar was excluded (``scoring_bars``).
    The figures are informational: no score, action or level reads them. They
    show what the live bar does to the indicators the page quotes, so a reader
    is not left with RSI 39 and "Bullish" after a 13% drop. RSI is Wilder's,
    taken over all bars including the live one. The volume ratio is against the
    last completed sessions and is partial while the session is still trading.
    """
    from .indicators import rsi as _rsi
    from .thresholds import RESEARCH

    if not basis.get("live_bar_excluded") or data is None or len(data) < 3:
        return {}
    if "Close" not in data.columns:
        return {}
    closes = pd.to_numeric(data["Close"], errors="coerce")
    prior = closes.iloc[:-1].dropna()
    if len(prior) < 2 or pd.isna(closes.iloc[-1]) or prior.iloc[-1] <= 0:
        return {}

    day_change_pct = (float(closes.iloc[-1]) / float(prior.iloc[-1]) - 1.0) * 100.0
    rsi_now = _rsi(closes.ffill(), 14).iloc[-1]

    rets = prior.pct_change().dropna().iloc[-RESEARCH.LIVE_SIGMA_SESSIONS:]
    sigma_pct = float(rets.std(ddof=1) * 100.0) if len(rets) >= 20 else None

    volume_ratio = None
    if "Volume" in data.columns:
        vols = pd.to_numeric(data["Volume"], errors="coerce")
        avg = vols.iloc[-1 - RESEARCH.LIVE_VOLUME_SESSIONS:-1].mean()
        if pd.notna(vols.iloc[-1]) and pd.notna(avg) and avg > 0:
            volume_ratio = float(vols.iloc[-1] / avg)

    floor = RESEARCH.LIVE_MOVE_FLOOR_PCT
    threshold = max(floor, RESEARCH.LIVE_MOVE_SIGMA * sigma_pct) if sigma_pct is not None else floor
    stamp = pd.Timestamp(data.index[-1])
    return {
        "as_of": _bar_date(stamp, market_of(data.index)).isoformat(),
        "day_change_pct": round(day_change_pct, 2),
        "rsi": round(float(rsi_now), 1) if pd.notna(rsi_now) else None,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "large_move": bool(abs(day_change_pct) >= threshold),
        "daily_sigma_pct": round(sigma_pct, 2) if sigma_pct is not None else None,
        "note": ("Today's bar is still forming or not yet settled, so its volume may be partial. "
                 "The scores, action and levels are unchanged: they read the last completed session."),
    }
