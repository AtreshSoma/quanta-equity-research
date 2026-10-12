"""News feed ingestion: TLS trust, timestamp zones, feed freshness, and the
rule that a headline outage never degrades price-based market state.

Root cause of "Data quality 0% / 0 of 8 live": urllib on python.org's macOS
build has an empty CA store, so every feed failed TLS verification while
prices (requests + certifi) kept working. All tests here are offline.
"""
from __future__ import annotations

import ssl
from datetime import datetime, timedelta, timezone

import pytest

import app as webapp

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


# --- TLS -----------------------------------------------------------------

def test_rss_context_verifies_and_has_a_populated_trust_store():
    ctx = webapp._RSS_SSL_CONTEXT
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.cert_store_stats()["x509_ca"] > 0, "no CA certificates loaded"


def test_feed_fetch_passes_the_verifying_context(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None, context=None):
        seen["context"] = context
        raise webapp.URLError("offline")

    monkeypatch.setattr(webapp, "urlopen", fake_urlopen)
    articles, status = webapp._fetch_rss_feed_with_status({"name": "X", "url": "https://x.invalid/rss"})
    assert seen["context"] is webapp._RSS_SSL_CONTEXT
    assert articles == [] and status["state"] == "error" and status["ok"] is False


def test_tls_failure_is_reported_not_bypassed(monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        raise webapp.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr(webapp, "urlopen", fake_urlopen)
    _, status = webapp._fetch_rss_feed_with_status({"name": "X", "url": "https://x.invalid/rss"})
    assert status["error"] == "TLS verification failed"


# --- Timestamps ----------------------------------------------------------

def test_zone_less_iso_timestamp_is_utc_not_server_local():
    # Investing.com style. Read as local time on a New York machine it landed
    # four hours in the future.
    assert webapp._parse_feed_date("2026-09-25 11:33:00", now=NOW) == "2026-09-25T11:33:00+00:00"


def test_rfc822_offset_is_converted_to_utc():
    assert webapp._parse_feed_date("Fri, 25 Sep 2026 07:15:00 -0400", now=NOW) == "2026-09-25T11:15:00+00:00"


def test_future_timestamp_is_unknown_not_fresh():
    assert webapp._parse_feed_date("Fri, 25 Sep 2026 18:00:00 GMT", now=NOW) == ""
    # Small clock skew is tolerated.
    assert webapp._parse_feed_date("Fri, 25 Sep 2026 12:10:00 GMT", now=NOW) != ""


def test_garbage_timestamp_is_empty():
    assert webapp._parse_feed_date("not a date", now=NOW) == ""


# --- Feed freshness classification --------------------------------------

def _article(hours_ago):
    return {"published": (NOW - timedelta(hours=hours_ago)).isoformat()}


SRC = {"name": "Feed", "url": "https://feed.invalid/rss"}


def test_recent_article_makes_a_feed_live():
    s = webapp._feed_status(SRC, [_article(2), _article(30)], now=NOW)
    assert (s["state"], s["ok"], s["age_hours"]) == ("live", True, 2.0)


def test_items_that_are_all_old_make_a_feed_stale_not_live():
    # WSJ's retired feed answered 200 with items from January 2025; it used to
    # count as "ok" because it returned something.
    s = webapp._feed_status(SRC, [_article(24 * 400)], now=NOW)
    assert (s["state"], s["ok"]) == ("stale", False)


def test_undated_items_cannot_vouch_for_freshness():
    s = webapp._feed_status(SRC, [{"published": ""}], now=NOW)
    assert (s["state"], s["ok"], s["newest"]) == ("stale", False, None)


def test_threshold_is_a_weekend_not_a_minute():
    assert webapp.FEED_LIVE_MAX_AGE_HOURS == 72
    assert webapp._feed_status(SRC, [_article(71)], now=NOW)["state"] == "live"
    assert webapp._feed_status(SRC, [_article(73)], now=NOW)["state"] == "stale"


def test_parsed_feed_reports_status_from_its_own_items(monkeypatch):
    rss = f"""<?xml version="1.0"?><rss><channel>
      <item><title>Fed holds rates steady</title><pubDate>{(datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>
      <item><title>Stocks rally into the close</title><pubDate>{(datetime.now(timezone.utc) - timedelta(hours=3)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>
    </channel></rss>""".encode()

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return rss

    monkeypatch.setattr(webapp, "urlopen", lambda request, timeout=None, context=None: _Resp())
    articles, status = webapp._fetch_rss_feed_with_status(SRC)
    assert len(articles) == 2
    assert status["state"] == "live" and status["items"] == 2
    assert 0.9 <= status["age_hours"] <= 1.1


def test_digest_reports_every_configured_feed_in_configuration_order(monkeypatch):
    feeds = [{"name": f"F{i}", "url": f"https://f{i}.invalid"} for i in range(4)]
    monkeypatch.setattr(webapp, "_feeds_for_market", lambda market: feeds)
    monkeypatch.setattr(webapp, "_fetch_rss_feed_with_status",
                        lambda src, per_source_limit=10: ([], webapp._feed_status(src, [], error="HTTP 404")))
    digest = webapp._build_news_digest("US")
    assert [s["name"] for s in digest["source_status"]] == ["F0", "F1", "F2", "F3"]
    assert all(s["state"] == "error" and s["error"] == "HTTP 404" for s in digest["source_status"])


def test_configured_feeds_exclude_the_retired_endpoints():
    urls = {f["url"] for f in webapp.US_RSS_FEEDS + webapp.INDIA_RSS_FEEDS}
    assert not any("reutersagency.com" in u for u in urls)
    assert "https://feeds.a.dj.com/rss/RSSMarketsMain.xml" not in urls
    assert not any("moneycontrol.com/rss" in u for u in urls)
    assert "https://finance.yahoo.com/news/rssindex" not in urls


# --- News never degrades price-based state -------------------------------

QUOTES = [{"symbol": s, "price": 100.0, "change_pct": 0.3}
          for s in ("SPY", "QQQ", "DIA", "IWM", "^VIX", "^TNX")]


def _regime(feeds_ok: bool):
    status = [{"name": f"F{i}", "ok": feeds_ok} for i in range(8)]
    return webapp._build_market_regime_engine(
        "US", "SPY", {"price": 100, "change_pct": 0.3}, {"price": 15.0, "change_pct": 1.0},
        {"price": 4.2, "change_pct": 0.1}, QUOTES, {}, ["SPY", "QQQ"], 55.0,
        news={"articles": [], "groups": {}}, catalysts=[], source_status=status,
    )


def test_a_news_outage_does_not_force_the_stale_data_posture():
    # Two market inputs are missing in this fixture (trend history, momentum).
    # The news outage used to be the third, flipping the dashboard to
    # "Data Incomplete" and capping posture at Lean Defensive.
    down, up = _regime(False), _regime(True)
    assert "news_feeds" in down["dataFreshness"]["missingInputs"], "the outage is still disclosed"
    assert down["dataFreshness"]["state"] != "degraded"
    assert down["posture"] == up["posture"]


def test_market_data_health_is_quotes_only():
    health = _regime(False)["sourceHealth"]
    assert health["state"] == "healthy" and health["healthPct"] == 100.0
    assert health["newsPct"] == 0.0 and health["newsSourcesOk"] == 0
