"""Symbol normalization, market routing and instrument support.

One place decides which provider symbols a user's input may mean, in what
order to try them, and whether the instrument is something the equity
research engine can analyse at all. Every failure a user sees must then be one
of a small set of honest categories (see ``FAILURE_CATEGORIES``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# Yahoo exchange suffixes that must never be rewritten. A single letter after a
# dot that is NOT one of these is a US share class (BRK.B -> BRK-B).
EXCHANGE_SUFFIXES = {
    "NS", "BO", "L", "TO", "V", "CN", "NE", "AX", "HK", "T", "DE", "F", "PA", "SW", "AS",
    "MI", "MC", "SI", "KS", "KQ", "TW", "TWO", "SS", "SZ", "SA", "MX", "JK", "BK", "ST",
    "OL", "CO", "HE", "IR", "VI", "BR", "LS", "NZ", "TA", "JO", "IS", "WA", "PR", "BD",
    "AT", "KL", "SR", "QA", "CA", "ME", "IL", "SG", "XC",
}
INDIA_SUFFIXES = ("NS", "BO")

# Instruments the equity research engine does not model (no fundamentals,
# valuation or company risk to score). They are reported as unsupported, not
# invalid and not a provider failure.
UNSUPPORTED_QUOTE_TYPES = {
    "INDEX": "an index (not directly investable)",
    "FUTURE": "a futures contract",
    "CURRENCY": "a currency pair",
    "CRYPTOCURRENCY": "a cryptocurrency",
    "OPTION": "an options contract",
}

# The categories a Research failure is reported in. Each carries whether a
# retry can help, so the UI can offer the right next action.
FAILURE_CATEGORIES = {
    "invalid_symbol": False,          # no such listed instrument (or delisted)
    "unsupported_instrument": False,  # real, but not something Research models
    "insufficient_data": False,       # real, but too little history to score yet
    "stale_data": True,               # latest bar is out of date
    "provider_unavailable": True,     # provider answered without usable data
    "temporary_failure": True,        # rate limit / timeout / analyzer busy
    "engine_error": True,             # the analysis itself raised
}

_CLASS_SHARE = re.compile(r"^([A-Z]{1,5})\.([A-Z])$")


@dataclass
class SymbolPlan:
    requested: str
    normalized: str
    market: str                                   # "US" | "IN" | ""
    candidates: List[str] = field(default_factory=list)
    note: str = ""                                # why normalization changed the input


def normalize_input(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw or "")).upper()


def has_exchange_suffix(symbol: str) -> bool:
    if "." not in symbol:
        return False
    return symbol.rsplit(".", 1)[1] in EXCHANGE_SUFFIXES


def plan_symbol(raw: str, market: Optional[str] = None) -> SymbolPlan:
    """Which provider symbols to try, in order, for this input and market.

    * An explicit exchange suffix, index (``^``), FX/future (``=``) or crypto
      pair is used as given.
    * ``BRK.B`` style US share classes become ``BRK-B`` (Yahoo's spelling).
    * A bare symbol follows the selected market: India tries NSE, then BSE,
      then the US listing; US tries the US listing, then NSE.
    * An NSE/BSE symbol may fall back to its sibling listing of the same
      company when one exchange's history is missing.
    """
    requested = normalize_input(raw)
    mkt = (market or "").strip().upper()
    mkt = "IN" if mkt in ("IN", "INDIA", "NSE", "BSE") else ("US" if mkt in ("US", "USA") else "")
    symbol, note = requested, ""

    m = _CLASS_SHARE.match(symbol)
    if m and m.group(2) not in EXCHANGE_SUFFIXES:
        symbol = f"{m.group(1)}-{m.group(2)}"
        note = f"{requested} is written {symbol} by the data provider (share class)."

    if symbol.startswith("^") or "=" in symbol or re.search(r"-(USD|USDT|EUR|INR|BTC)$", symbol):
        candidates = [symbol]
    elif has_exchange_suffix(symbol):
        base, suffix = symbol.rsplit(".", 1)
        candidates = [symbol]
        if suffix in INDIA_SUFFIXES:
            sibling = "BO" if suffix == "NS" else "NS"
            candidates.append(f"{base}.{sibling}")
    elif mkt == "IN":
        candidates = [f"{symbol}.NS", f"{symbol}.BO", symbol]
    else:
        candidates = [symbol, f"{symbol}.NS"]
    return SymbolPlan(requested, symbol, mkt, candidates, note)


def resolution_note(plan: SymbolPlan, resolved: str) -> str:
    """Plain statement of which listing was analysed, when it differs from
    what was typed or from the selected market."""
    parts = [plan.note] if plan.note else []
    if not resolved:
        return " ".join(parts)
    india = resolved.rsplit(".", 1)[-1] in INDIA_SUFFIXES if "." in resolved else False
    where = ("NSE" if resolved.endswith(".NS") else "BSE" if resolved.endswith(".BO")
             else "the US listing")
    if resolved != plan.normalized and has_exchange_suffix(plan.normalized):
        parts.append(f"{plan.normalized} had no usable history at the provider; "
                     f"analysed the same company's {where} listing, {resolved}.")
    elif plan.market == "IN" and not india and not resolved.startswith("^") and "=" not in resolved:
        parts.append(f"No NSE or BSE listing found for {plan.normalized}; analysed the US listing.")
    elif plan.market != "IN" and india and not has_exchange_suffix(plan.normalized):
        parts.append(f"No US listing found for {plan.normalized}; analysed {resolved} ({where}).")
    elif resolved != plan.normalized and not parts:
        parts.append(f"Analysed {resolved} ({where}).")
    return " ".join(parts)


# Investable instruments that track a non-investable index, offered as the
# next step when Research is asked for the index itself.
TRACKING_ALTERNATIVES = {
    "^GSPC": ["SPY", "VOO"], "^SPX": ["SPY", "VOO"], "^IXIC": ["QQQ"], "^NDX": ["QQQ"],
    "^DJI": ["DIA"], "^RUT": ["IWM"], "^NSEI": ["NIFTYBEES.NS"], "^BSESN": ["SENSEXBEES.NS"],
    "^NSEBANK": ["BANKBEES.NS"],
}


def alternatives_for(symbol: str) -> List[str]:
    return list(TRACKING_ALTERNATIVES.get(normalize_input(symbol), []))


def unsupported_reason(quote_type: Optional[str]) -> str:
    qt = str(quote_type or "").strip().upper()
    what = UNSUPPORTED_QUOTE_TYPES.get(qt)
    return (f"This is {what}. QUANTA Research scores companies and funds "
            f"(fundamentals, valuation, company risk), so it has no Decision for it.") if what else ""
