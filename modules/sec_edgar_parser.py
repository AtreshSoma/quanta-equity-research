"""
SEC EDGAR 8-K Filing Parser for Catalyst Detection

This module provides real-time detection of material corporate events from SEC filings:
- M&A announcements (Form 8-K Item 2.01)
- Material agreements (Item 1.01)
- Financial results (Item 2.02)
- Director/Officer changes (Item 5.02)
- Securities offerings (Item 3.02)

Author: Quanta Elite System
Date: 2025-12-29
"""

import os
import threading

import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
import logging
import re
import time

logger = logging.getLogger(__name__)


class FilingType(Enum):
    """SEC Filing Types"""
    FORM_8K = "8-K"
    FORM_8K_A = "8-K/A"  # Amended
    FORM_13D = "SC 13D"  # Activist investor
    FORM_13G = "SC 13G"  # Passive investor
    FORM_4 = "4"         # Insider trading
    FORM_S1 = "S-1"      # IPO
    FORM_424B = "424B"   # Prospectus


class Form8KItem(Enum):
    """Form 8-K Item Types - Material Events"""
    ITEM_1_01 = "1.01"  # Entry into Material Agreement
    ITEM_1_02 = "1.02"  # Termination of Material Agreement
    ITEM_2_01 = "2.01"  # Completion of Acquisition/Disposition
    ITEM_2_02 = "2.02"  # Results of Operations (Earnings)
    ITEM_2_03 = "2.03"  # Creation of Direct Financial Obligation
    ITEM_2_05 = "2.05"  # Costs for Exit/Restructuring
    ITEM_2_06 = "2.06"  # Material Impairment
    ITEM_3_02 = "3.02"  # Unregistered Sales of Equity
    ITEM_4_01 = "4.01"  # Changes in Registrant's Certifying Accountant
    ITEM_5_01 = "5.01"  # Changes in Control
    ITEM_5_02 = "5.02"  # Director/Principal Officer Departure
    ITEM_7_01 = "7.01"  # Regulation FD Disclosure
    ITEM_8_01 = "8.01"  # Other Events


@dataclass
class SECFiling:
    """Represents an SEC filing"""
    ticker: str
    company_name: str
    filing_type: FilingType
    filing_date: datetime
    description: str
    items: List[Form8KItem] = field(default_factory=list)
    url: str = ""
    
    # Catalyst scoring
    catalyst_type: str = ""
    catalyst_weight: float = 0.0
    is_material: bool = False


class SECEdgarParser:
    """
    Parse SEC EDGAR filings for material corporate events.

    Uses the modern ``data.sec.gov`` submissions API. The legacy
    ``cgi-bin/browse-edgar`` Atom endpoint this class used to call is a
    deprecated CGI script that SEC throttles hard: measured at ~10 s per
    ticker and frequently timing out past 20 s, it accounted for ~88% of the
    total wall time of ``analyze_stock``. The submissions JSON returns the
    same filings in ~0.4 s and, unlike the Atom summary text, carries the
    REAL 8-K item numbers instead of leaving us to guess them from prose.
    """

    # SEC endpoints. data.sec.gov is the supported JSON API; the browse-edgar
    # CGI endpoints are kept only as documentation of what was replaced.
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    LEGACY_RSS_FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"  # deprecated, do not use

    # Back-compat aliases (external callers referenced these).
    RSS_FEED_URL = LEGACY_RSS_FEED_URL
    FULL_TEXT_URL = "https://efts.sec.gov/LATEST/search-index"
    COMPANY_SEARCH_URL = LEGACY_RSS_FEED_URL

    # ticker -> CIK, populated once from COMPANY_TICKERS_URL for every US
    # listing. Shared across instances: get_sec_parser() and several callers
    # construct a new parser per ticker, so anything per-instance is useless
    # as a cache (and made the old rate limiter a no-op).
    _cik_cache: Dict[str, str] = {}

    # Catalyst weights by 8-K item type
    ITEM_WEIGHTS = {
        Form8KItem.ITEM_2_01: 0.25,  # M&A completion - HIGH
        Form8KItem.ITEM_1_01: 0.15,  # Material agreement - MEDIUM-HIGH
        Form8KItem.ITEM_5_01: 0.20,  # Change of control - HIGH
        Form8KItem.ITEM_2_02: 0.10,  # Earnings - MEDIUM
        Form8KItem.ITEM_5_02: 0.08,  # Executive departure - MEDIUM
        Form8KItem.ITEM_2_05: 0.05,  # Restructuring - LOW
        Form8KItem.ITEM_7_01: 0.08,  # Reg FD disclosure - MEDIUM
        Form8KItem.ITEM_8_01: 0.05,  # Other events - LOW
    }

    # SEC's Internet Security Policy requires a User-Agent that identifies the
    # requester with a real contact address. Set QUANTA_SEC_USER_AGENT to your
    # own "Name contact@example.com" — the placeholder below is what SEC
    # deprioritises.
    HEADERS = {
        'User-Agent': os.getenv(
            'QUANTA_SEC_USER_AGENT',
            'QuantaElite/2.0 (quantaelite@example.com)'),
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip, deflate',
        'Host': 'data.sec.gov',
    }

    # --- Tunables -------------------------------------------------------
    # SEC answers in ~0.4s; a 15s read timeout only bought us long stalls.
    TIMEOUT = (
        float(os.getenv('QUANTA_SEC_CONNECT_TIMEOUT', '3')),
        float(os.getenv('QUANTA_SEC_TIMEOUT', '6')),
    )
    CACHE_TTL = float(os.getenv('QUANTA_SEC_CACHE_TTL', '21600'))  # 6h
    ENABLED = os.getenv('QUANTA_SEC_ENABLED', '1').strip().lower() not in ('0', 'false', 'no')
    # Filings move slowly; one lookup per ticker per scan is plenty.
    RATE_LIMIT_DELAY = float(os.getenv('QUANTA_SEC_RATE_DELAY', '0.11'))  # SEC allows 10/s
    # After this many consecutive failures, stop calling SEC for the rest of
    # the cooldown. Without this an SEC outage costs (universe x timeout) —
    # 669 tickers x 6s = 67 minutes of waiting for nothing.
    CIRCUIT_TRIP_FAILURES = int(os.getenv('QUANTA_SEC_CIRCUIT_TRIP', '5'))
    CIRCUIT_COOLDOWN = float(os.getenv('QUANTA_SEC_CIRCUIT_COOLDOWN', '900'))

    # --- Shared, thread-safe state --------------------------------------
    _session = None
    _session_lock = threading.Lock()
    _rate_lock = threading.Lock()
    _last_request_time = 0.0
    _cik_map_loaded = False
    _cik_map_lock = threading.Lock()
    _filings_cache: Dict[str, Tuple[float, List["SECFiling"]]] = {}
    _filings_cache_lock = threading.Lock()
    _consecutive_failures = 0
    _circuit_open_until = 0.0
    _circuit_lock = threading.Lock()

    def __init__(self):
        # Deliberately stateless: every cache and limiter above is class-level
        # so that constructing a parser per ticker (which several callers do)
        # still shares one session, one CIK map and one rate limiter.
        pass

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    @classmethod
    def _get_session(cls):
        """One pooled session for the process (TLS handshake reuse)."""
        if cls._session is None:
            with cls._session_lock:
                if cls._session is None:
                    session = requests.Session()
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=4, pool_maxsize=8, max_retries=0)
                    session.mount("https://", adapter)
                    cls._session = session
        return cls._session

    @classmethod
    def _rate_limit(cls):
        """Global 10 req/s ceiling, shared across scan threads.

        `_last_request_time` is a process-wide wall-clock stamp, so the gap can
        come out NEGATIVE whenever the clock moves backwards between two calls
        — an NTP step correction, an operator changing the system clock, or a
        test that freezes time to an earlier date than the previous one. The
        sleep was `RATE_LIMIT_DELAY - elapsed`, which for a backwards jump of
        D seconds becomes a sleep of D. An eleven-day jump therefore parked the
        caller for eleven days while it held `_cik_map_lock`, blocking every
        other thread that needed a CIK behind it.

        The pause can never usefully exceed the delay itself, so clamp it.
        """
        with cls._rate_lock:
            now = time.time()
            elapsed = now - cls._last_request_time
            if elapsed < 0:
                # Clock went backwards; treat it as "no useful history".
                elapsed = cls.RATE_LIMIT_DELAY
            if elapsed < cls.RATE_LIMIT_DELAY:
                time.sleep(min(cls.RATE_LIMIT_DELAY,
                               cls.RATE_LIMIT_DELAY - elapsed))
            cls._last_request_time = time.time()

    @classmethod
    def reset_transient_state(cls) -> None:
        """Drop every piece of process-wide clock/cache state.

        The rate-limiter stamp, the circuit breaker and the CIK map are all
        class attributes that outlive any single analysis. That is right for a
        long-running scan and wrong for replayed fixtures, where each recording
        is frozen to its own date: state carried from one date into another is
        what turned the backwards-clock bug above into a suite-wide hang.
        """
        with cls._rate_lock:
            cls._last_request_time = 0.0
        with cls._circuit_lock:
            cls._circuit_open_until = 0.0
            cls._consecutive_failures = 0
        with cls._cik_map_lock:
            cls._cik_cache = {}
            cls._cik_map_loaded = False

    @classmethod
    def _circuit_is_open(cls) -> bool:
        with cls._circuit_lock:
            if time.time() < cls._circuit_open_until:
                return True
            if cls._circuit_open_until and time.time() >= cls._circuit_open_until:
                cls._circuit_open_until = 0.0
                cls._consecutive_failures = 0
            return False

    @classmethod
    def _note_failure(cls):
        with cls._circuit_lock:
            cls._consecutive_failures += 1
            if cls._consecutive_failures >= cls.CIRCUIT_TRIP_FAILURES and not cls._circuit_open_until:
                cls._circuit_open_until = time.time() + cls.CIRCUIT_COOLDOWN
                logger.warning(
                    "SEC EDGAR unreachable after %d consecutive failures; "
                    "skipping filing catalysts for %.0fs. Scores fall back to "
                    "the non-catalyst path.",
                    cls._consecutive_failures, cls.CIRCUIT_COOLDOWN)

    @classmethod
    def _note_success(cls):
        with cls._circuit_lock:
            cls._consecutive_failures = 0

    @classmethod
    def _get_json(cls, url: str, host: str) -> Optional[dict]:
        """GET a SEC JSON document, or None. Never raises."""
        if not cls.ENABLED or cls._circuit_is_open():
            return None
        headers = dict(cls.HEADERS)
        headers['Host'] = host
        try:
            cls._rate_limit()
            response = cls._get_session().get(url, headers=headers, timeout=cls.TIMEOUT)
            if response.status_code != 200:
                logger.debug("SEC returned %s for %s", response.status_code, url)
                # A 404/410 is EDGAR answering correctly: the document does not
                # exist (a company with no filings of this type, a CIK with no
                # facts). Counting it as a connectivity failure meant five such
                # companies in a row blacked out filing catalysts for every
                # ticker for the whole cooldown. Only genuine unavailability —
                # throttling, auth blocks, server errors — trips the circuit.
                if response.status_code in (400, 404, 410):
                    cls._note_success()
                else:
                    cls._note_failure()
                return None
            cls._note_success()
            return response.json()
        except Exception as e:
            logger.debug("SEC request failed for %s: %s", url, e)
            cls._note_failure()
            return None

    # ------------------------------------------------------------------
    # CIK resolution
    # ------------------------------------------------------------------
    @classmethod
    def _load_cik_map(cls) -> bool:
        """Fetch ticker->CIK for every US listing, once per process.

        One 0.4s request replaces a per-ticker CIK lookup against the legacy
        CGI endpoint.
        """
        if cls._cik_map_loaded:
            return True
        with cls._cik_map_lock:
            if cls._cik_map_loaded:
                return True
            data = cls._get_json(cls.COMPANY_TICKERS_URL, host='www.sec.gov')
            if not isinstance(data, dict):
                # Leave the map retryable. The shared circuit breaker prevents
                # an outage from turning this into one timeout per ticker, and
                # permits recovery after its cooldown without a process restart.
                return False
            for row in data.values():
                try:
                    symbol = str(row['ticker']).upper()
                    cls._cik_cache[symbol] = str(row['cik_str']).zfill(10)
                except (KeyError, TypeError, ValueError):
                    continue
            logger.debug("SEC CIK map loaded: %d tickers", len(cls._cik_cache))
            cls._cik_map_loaded = True
            return True

    def get_cik(self, ticker: str) -> Optional[str]:
        """Get the zero-padded CIK for a ticker, or None."""
        symbol = (ticker or '').upper().replace('.', '-')
        if symbol in self._cik_cache:
            return self._cik_cache[symbol]
        self._load_cik_map()
        return self._cik_cache.get(symbol)

    # ------------------------------------------------------------------
    # Filings
    # ------------------------------------------------------------------
    def get_recent_8k_filings(self, ticker: str, days: int = 30) -> List[SECFiling]:
        """Recent 8-K filings for a ticker, newest first.

        Returns [] rather than raising on any failure: filing catalysts are an
        enrichment, and must never be able to fail or stall an analysis.
        """
        ticker = (ticker or '').upper()
        if not ticker:
            return []

        # Non-US listings do not file 8-Ks.
        if '.' in ticker and not ticker.endswith('.US'):
            logger.debug("Skipping non-US ticker %s for SEC filings", ticker)
            return []

        cache_key = f"{ticker}:{days}"
        now = time.time()
        with self._filings_cache_lock:
            hit = self._filings_cache.get(cache_key)
            if hit and (now - hit[0]) < self.CACHE_TTL:
                return list(hit[1])

        filings = self._fetch_8k_filings(ticker, days)
        if filings is None:
            # Transport failure is not evidence that there are no filings.
            # Do not turn a transient outage into a six-hour negative cache.
            return []

        with self._filings_cache_lock:
            self._filings_cache[cache_key] = (now, list(filings))
        return filings

    def _fetch_8k_filings(self, ticker: str, days: int) -> Optional[List[SECFiling]]:
        cik = self.get_cik(ticker)
        if not cik:
            # A successfully loaded map with no symbol is a valid negative.
            # A map transport failure is retryable and must not be cached.
            return [] if self._cik_map_loaded else None

        data = self._get_json(self.SUBMISSIONS_URL.format(cik=cik), host='data.sec.gov')
        if not isinstance(data, dict):
            return None

        recent = ((data.get('filings') or {}).get('recent') or {})
        forms = recent.get('form') or []
        dates = recent.get('filingDate') or []
        items_col = recent.get('items') or []
        descriptions = recent.get('primaryDocDescription') or []
        accessions = recent.get('accessionNumber') or []
        documents = recent.get('primaryDocument') or []
        company_name = data.get('name') or ticker

        cutoff = datetime.now() - timedelta(days=days)
        filings: List[SECFiling] = []

        for i, form in enumerate(forms):
            form = str(form or '')
            if not form.startswith('8-K'):
                continue
            try:
                filing_date = datetime.strptime(str(dates[i]), '%Y-%m-%d')
            except (IndexError, ValueError, TypeError):
                continue
            if filing_date < cutoff:
                # `recent` is newest-first, so everything after this is older.
                break

            raw_items = str(items_col[i]) if i < len(items_col) else ''
            description = str(descriptions[i]) if i < len(descriptions) else ''

            url = ''
            try:
                accession = str(accessions[i]).replace('-', '')
                url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                       f"{accession}/{documents[i]}")
            except (IndexError, ValueError, TypeError):
                pass

            filing = SECFiling(
                ticker=ticker,
                company_name=str(company_name).strip(),
                filing_type=FilingType.FORM_8K_A if form.startswith('8-K/A') else FilingType.FORM_8K,
                filing_date=filing_date,
                description=description,
                url=url,
            )
            self._analyze_8k_items(filing, raw_items)
            filings.append(filing)

        return filings

    def _analyze_8k_items(self, filing: SECFiling, raw_items: str = ""):
        """Score a filing from its declared 8-K item numbers.

        ``raw_items`` is the submissions API's ``items`` cell, e.g.
        ``"2.02,9.01"`` — the items the registrant actually filed under. The
        previous implementation had no such field and inferred item types by
        grepping the Atom summary for words like "acquisition", which tagged
        any filing mentioning a purchase as M&A. Text matching is kept only as
        a fallback for filings with no declared items.
        """
        declared = [part.strip() for part in (raw_items or "").split(',') if part.strip()]
        by_value = {item.value: item for item in Form8KItem}

        for code in declared:
            item = by_value.get(code)
            if item is not None and item not in filing.items:
                filing.items.append(item)

        if not filing.items:
            description = (filing.description or "").lower()
            item_patterns = {
                Form8KItem.ITEM_2_01: ['acquisition', 'disposition', 'merger', 'acqui', 'purchase'],
                Form8KItem.ITEM_1_01: ['material agreement', 'definitive agreement', 'contract'],
                Form8KItem.ITEM_5_01: ['change of control', 'controlling', 'change in control'],
                Form8KItem.ITEM_2_02: ['results of operations', 'financial results', 'earnings'],
                Form8KItem.ITEM_5_02: ['departure', 'resignation', 'appointed', 'ceo', 'cfo'],
                Form8KItem.ITEM_2_05: ['restructuring', 'exit', 'cost reduction'],
                Form8KItem.ITEM_7_01: ['regulation fd', 'press release', 'investor presentation'],
            }
            for item, patterns in item_patterns.items():
                if any(p in description for p in patterns):
                    filing.items.append(item)

        total_weight = sum(self.ITEM_WEIGHTS.get(item, 0.05) for item in filing.items)

        if not filing.items:
            filing.items.append(Form8KItem.ITEM_8_01)
            total_weight = 0.05

        filing.catalyst_weight = min(1.0, total_weight)
        filing.is_material = total_weight >= 0.15

        if Form8KItem.ITEM_2_01 in filing.items or Form8KItem.ITEM_5_01 in filing.items:
            filing.catalyst_type = "MNA"
        elif Form8KItem.ITEM_1_01 in filing.items:
            filing.catalyst_type = "CONTRACT"
        elif Form8KItem.ITEM_2_02 in filing.items:
            filing.catalyst_type = "EARNINGS"
        elif Form8KItem.ITEM_5_02 in filing.items:
            filing.catalyst_type = "MANAGEMENT"
        else:
            filing.catalyst_type = "OTHER"

    def get_material_filings(self, ticker: str, days: int = 14) -> List[SECFiling]:
        """Get only material 8-K filings (weight >= 0.15)."""
        filings = self.get_recent_8k_filings(ticker, days)
        return [f for f in filings if f.is_material]
    
    def get_filing_summary(self, ticker: str) -> Dict:
        """Get summary of recent SEC filings for a ticker."""
        filings = self.get_recent_8k_filings(ticker, days=30)
        
        return {
            'ticker': ticker,
            'total_filings': len(filings),
            'material_filings': sum(1 for f in filings if f.is_material),
            'recent_catalysts': [
                {
                    'type': f.catalyst_type,
                    'date': f.filing_date.strftime('%Y-%m-%d'),
                    'weight': f.catalyst_weight,
                    'description': f.description[:100] if f.description else ""
                }
                for f in filings if f.is_material
            ][:5]
        }


_PARSER_SINGLETON: Optional[SECEdgarParser] = None
_PARSER_LOCK = threading.Lock()


def get_sec_parser() -> SECEdgarParser:
    """Get the shared SEC EDGAR parser instance."""
    global _PARSER_SINGLETON
    if _PARSER_SINGLETON is None:
        with _PARSER_LOCK:
            if _PARSER_SINGLETON is None:
                _PARSER_SINGLETON = SECEdgarParser()
    return _PARSER_SINGLETON


if __name__ == "__main__":
    # Demo
    parser = SECEdgarParser()
    
    print("📋 SEC EDGAR 8-K PARSER")
    print("=" * 60)
    
    # Test with a few tickers
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        print(f"\n{ticker}:")
        summary = parser.get_filing_summary(ticker)
        print(f"  Total 8-K filings (30d): {summary['total_filings']}")
        print(f"  Material filings: {summary['material_filings']}")
        
        for cat in summary['recent_catalysts'][:3]:
            print(f"    - {cat['date']}: {cat['type']} (weight: {cat['weight']:.0%})")
