"""A ticker's headlines from Yahoo Finance's per-symbol RSS feed.

yfinance's news call (``Ticker.news``) fails for whole days at a time: on
2026-11-09 it raised "YAHOO! FINANCE IS CURRENTLY DOWN" for 134 of 144 names
in one scan while this feed kept serving. The Picks scanner and the Research
sentiment leg both read this one parser.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any, Callable, Dict, List, Optional

YAHOO_TICKER_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
TIMEOUT_SECONDS = 10.0


def _default_get(url: str) -> Any:
    import requests
    return requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0 (QUANTA research)"})


def rss_news_items(ticker: str, get: Optional[Callable[[str], Any]] = None,
                   limit: int = 20) -> List[Dict[str, Any]]:
    """Dated, linked headlines: ``title``, ``publisher``, ``published`` (UTC ISO), ``url``, ``summary``."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from urllib.parse import quote

    resp = (get or _default_get)(YAHOO_TICKER_RSS.format(symbol=quote(ticker)))
    resp.raise_for_status()
    out: List[Dict[str, Any]] = []
    for item in ET.fromstring(resp.content).iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        when = ""
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            when = ""
        desc = (item.findtext("description") or "").strip()
        publisher = desc.split(" -- ", 1)[0].strip() if " -- " in desc[:60] else "Yahoo Finance"
        link = (item.findtext("link") or "").strip()
        out.append({"title": title[:160], "publisher": publisher[:60], "published": when,
                    "url": link if link.startswith("http") else "", "summary": desc[:400]})
        if len(out) >= limit:
            break
    return out
