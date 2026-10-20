"""Scanner accuracy fixes, 2026-10-19 review.

1. ADR deal checks no longer fail open: a foreign private issuer files 6-K
   current reports (free-form, not classified by the check) instead of 8-Ks and
   merger proxies, so a recent 6-K makes the event check "unavailable", never
   "clear". A Canadian target's tender response (SC 14D9F) is a deal form.
2. A consensus target set before a stock split (it sits nearer price x split
   ratio than price) is stale: it is treated as unavailable, not as upside.
3. Price freshness is counted in trading sessions (UNIVERSE.MAX_LIVE_DATA_AGE_SESSIONS),
   so a bar a week old is stale while a holiday weekend is not.
4. Indicators that cannot be computed are missing, not neutral readings: RSI is
   not 50, ADX is not 0 and beta is not 1.0.
5. The correlation caps and the crypto-correlated list live in thresholds.py.

The filing texts are short synthetic excerpts; nothing touches the network.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from _paths import ensure_paths

ensure_paths()

from engine import corporate_events as ce  # noqa: E402
from engine import thresholds as T  # noqa: E402
from portfolio.sources import EdgarClient  # noqa: E402

import SP_ELITE_PRO as sp  # noqa: E402

TODAY = date(2026, 10, 1)


# ── 1. foreign private issuers ───────────────────────────────────────────────

def _subs(*rows, name="Example Overseas Holdings plc"):
    rows = list(rows)
    return {"name": name, "filings": {"recent": {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "items": [r[2] for r in rows],
        "accessionNumber": [f"0002-26-{i:06d}" for i, _ in enumerate(rows)],
        "primaryDocument": [r[3] for r in rows],
        "acceptanceDateTime": [r[1] + "T16:00:00.000Z" for r in rows],
    }}}


class _FakeSec:
    def __init__(self, subs, docs=None):
        self.subs, self.docs = subs, docs or {}

    def get_json(self, url):
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "EXOV", "cik_str": 1700001}}
        return self.subs

    def get_text(self, url, max_bytes):
        for name, text in self.docs.items():
            if url.endswith(name):
                return text
        raise FileNotFoundError(url)


def _check(fake):
    return ce.check("EXOV", EdgarClient(get_json=fake.get_json), TODAY, fetch_text=fake.get_text)


@pytest.mark.parametrize("annual", ["20-F", "40-F"])
def test_a_foreign_issuer_with_an_unread_6k_is_unavailable_not_clear(annual):
    fake = _FakeSec(_subs((annual, "2026-04-15", "", "annual.htm"),
                          ("6-K", "2026-09-03", "", "sixk.htm")))
    ev = _check(fake)
    assert ev.status == "unavailable" and not ev.fundable
    assert ev.reason == "foreign_reports_unread"
    assert "6-K" in ev.text and "2026-09-03" in ev.text
    label, _text, fix = ce.UNAVAILABLE_REASONS["foreign_reports_unread"]
    assert ev.fix == fix and label


def test_a_foreign_issuer_with_no_current_report_in_the_window_is_clear():
    fake = _FakeSec(_subs(("20-F", "2026-04-15", "", "annual.htm")))
    assert _check(fake).status == "clear"


def test_a_canadian_targets_tender_response_is_a_pending_acquisition():
    tender = ("<p>The board of directors of the Company recommends that shareholders tender their "
              "shares to the offer by Purchaser at $41.00 per share in cash.</p>")
    fake = _FakeSec(_subs(("SC 14D9F", "2026-09-10", "", "sc14d9f.htm"),
                          ("6-K", "2026-09-03", "", "sixk.htm")),
                    docs={"sc14d9f.htm": tender})
    ev = _check(fake)
    assert ev.status == "flagged" and ev.code == "PENDING_ACQUISITION"


def test_a_domestic_filer_is_unaffected():
    credit = ("<p>Item 1.01. On July 9, 2026, the Company entered into a Credit Agreement "
              "with a syndicate of lenders.</p>")
    fake = _FakeSec(_subs(("8-K", "2026-07-10", "1.01", "credit8k.htm"), name="Example Domestic Inc."),
                    docs={"credit8k.htm": credit})
    assert _check(fake).status == "clear"


def test_foreign_forms_are_in_thresholds():
    assert "6-K" in T.EVENTS.FOREIGN_CURRENT_FORMS
    assert "SC 14D9F" in T.EVENTS.STRONG_FORMS


# ── 2. split-unadjusted analyst targets ──────────────────────────────────────

def _split_info(factor, days_ago, today=date(2026, 10, 19)):
    ts = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp() - days_ago * 86400
    return {"lastSplitFactor": factor, "lastSplitDate": int(ts)}


@pytest.mark.parametrize("factor,target,price", [
    ("2:1", 210.0, 100.0),      # pre-split target 2x price passes the 0.5x-3x band
    ("3:2", 165.0, 100.0),      # a 3:2 split
    ("1:2", 52.0, 100.0),       # a reverse split leaves the old target near half the price
])
def test_a_target_set_before_a_recent_split_is_stale(factor, target, price):
    lo, hi = T.LEVELS.ANALYST_TARGET_SANE_RANGE
    assert price * lo < target < price * hi          # the band alone accepts it
    info = _split_info(factor, days_ago=40)
    ratio = sp.analyst_target_split_ratio(target, price, info, today=date(2026, 10, 19))
    assert ratio is not None


@pytest.mark.parametrize("factor,days_ago,target", [
    ("2:1", 40, 115.0),         # a normal target after a split: nearer price than price x 2
    ("2:1", 800, 210.0),        # the split is older than the lookback window
    (None, None, 210.0),        # no split on record
])
def test_a_target_without_a_recent_explaining_split_is_kept(factor, days_ago, target):
    info = _split_info(factor, days_ago) if factor else {}
    assert sp.analyst_target_split_ratio(target, 100.0, info, today=date(2026, 10, 19)) is None


def test_the_split_lookback_is_a_threshold():
    assert T.LEVELS.ANALYST_TARGET_SPLIT_LOOKBACK_DAYS > 0
    info = _split_info("2:1", days_ago=T.LEVELS.ANALYST_TARGET_SPLIT_LOOKBACK_DAYS + 5)
    assert sp.analyst_target_split_ratio(210.0, 100.0, info, today=date(2026, 10, 19)) is None


def test_scanner_drops_a_stale_target_and_its_range():
    info = dict(_split_info("2:1", days_ago=30), targetMeanPrice=210.0, targetLowPrice=150.0,
                targetHighPrice=260.0)
    target, cleaned, note = sp.split_checked_analyst_target(info, 100.0, today=date(2026, 10, 19))
    assert target == 0.0
    assert cleaned.get("targetMeanPrice") is None and cleaned.get("targetLowPrice") is None
    assert cleaned.get("targetHighPrice") is None
    assert "split" in note
    fresh = dict(_split_info("2:1", days_ago=30), targetMeanPrice=112.0)
    target, cleaned, note = sp.split_checked_analyst_target(fresh, 100.0, today=date(2026, 10, 19))
    assert target == 112.0 and cleaned is fresh and note == ""


# ── 3. freshness in trading sessions ─────────────────────────────────────────

def _bars(last):
    idx = pd.bdate_range(end=last, periods=60)
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
                         "Volume": 1_000_000}, index=idx)


def _valid_at(bars, now, market="US"):
    from freezegun import freeze_time
    with freeze_time(now):
        return sp.DataValidator.validate_history(
            bars, "X", max_age_sessions=T.UNIVERSE.MAX_LIVE_DATA_AGE_SESSIONS, market=market).is_valid


def test_a_bar_a_week_old_is_stale():
    # Last bar Mon 2026-10-12; at noon New York on Mon 2026-10-19 it is four completed
    # sessions old (the old 10-calendar-day allowance accepted it).
    assert not _valid_at(_bars("2026-10-12"), "2026-10-19 16:00:00")


def test_a_holiday_weekend_is_not_stale():
    # Thanksgiving 2026: last bar Wed 11-25, checked Mon 11-30 before the close.
    assert _valid_at(_bars("2026-11-25"), "2026-11-30 15:00:00")
    # Labor Day 2026: last bar Fri 09-04, checked Tue 09-08 midday.
    assert _valid_at(_bars("2026-09-04"), "2026-09-08 16:00:00")


def test_india_freshness_counts_weekdays_on_the_india_clock():
    # Last bar Fri 2026-10-16, Mon 2026-10-19 11:00 IST (05:30 UTC): fresh.
    assert _valid_at(_bars("2026-10-16"), "2026-10-19 05:30:00", market="IN")
    assert not _valid_at(_bars("2026-10-09"), "2026-10-19 05:30:00", market="IN")


def test_the_allowance_is_in_sessions():
    assert T.UNIVERSE.MAX_LIVE_DATA_AGE_SESSIONS == 3
    assert sp.TradingConfig.MAX_LIVE_DATA_AGE_SESSIONS == 3


# ── 4. failure defaults are missing, not neutral ─────────────────────────────

def test_rsi_that_cannot_be_computed_is_missing():
    assert math.isnan(sp.TechnicalAnalysisEngine._calculate_rsi(pd.Series([1.0, 2.0, 3.0]), 14))


def test_adx_that_cannot_be_computed_is_missing():
    px = pd.Series(np.linspace(50, 60, 40))
    hist = pd.DataFrame({"High": px * 1.01, "Low": px * 0.99, "Close": px})
    assert math.isnan(sp.TechnicalAnalysisEngine._calculate_adx(hist))


def test_beta_that_cannot_be_measured_is_missing():
    beta, alpha = sp.RiskAnalyticsEngine.calculate_beta_alpha(pd.Series([0.01] * 5), pd.Series([0.01] * 5))
    assert beta is None and alpha is None


def test_indicators_with_a_missing_rsi_or_adx_are_not_published(monkeypatch):
    px = pd.Series(np.linspace(50, 100, 300))
    hist = pd.DataFrame({"Open": px, "High": px * 1.01, "Low": px * 0.99, "Close": px,
                         "Volume": np.full(300, 1e6)})
    assert sp.TechnicalAnalysisEngine.calculate_all_indicators(hist) is not None
    monkeypatch.setattr(sp.TechnicalAnalysisEngine, "_calculate_adx", staticmethod(lambda h, period=14: float("nan")))
    assert sp.TechnicalAnalysisEngine.calculate_all_indicators(hist) is None


def test_a_missing_rsi_earns_no_rsi_points():
    ind = sp.TechnicalIndicators()
    ind.current_price, ind.sma_20, ind.sma_50, ind.sma_200 = 100.0, 95.0, 90.0, 80.0
    ind.rsi = 50.0
    measured = sp.ScoringEngine.calculate_technical_score(ind)
    ind.rsi = float("nan")
    missing = sp.ScoringEngine.calculate_technical_score(ind)
    assert missing < measured


# ── 5. magic numbers moved to thresholds ─────────────────────────────────────

def test_correlation_caps_and_crypto_list_read_thresholds(monkeypatch):
    assert sp.RiskLimits.MAX_PAIRWISE_CORRELATION == T.BOOK.MAX_PAIRWISE_CORRELATION == 0.70
    assert sp.TradingConfig.MAX_AVERAGE_CORRELATION == T.BOOK.MAX_AVERAGE_CORRELATION == 0.60
    monkeypatch.setattr(T.BOOK, "MAX_PAIRWISE_CORRELATION", 0.5)
    assert sp.RiskLimits.MAX_PAIRWISE_CORRELATION == 0.5
    assert set(sp.CRYPTO_CORRELATED_STOCKS) == set(T.SCORE.CRYPTO_CORRELATED_TICKERS)
    assert T.SCORE.CRYPTO_SCORE_PREMIUM == 2.0
