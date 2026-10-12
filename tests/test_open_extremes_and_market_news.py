"""Open = Low / Open = High boards, and per-market news.

Offline (no network). Two features, one theme: what the dashboard shows must
belong to the market the user selected.

  * A stock that opened exactly at its session low never traded below its
    first print; one that opened at its high never traded above it. Both are
    read off the session OHLC the movers feed already carries, so the boards
    cost no extra provider call.
  * Selecting India used to leave the headline tape on US wires, because the
    feed list was a module constant and the classifier's vocabulary knew
    "fed" and "cpi" but not "rbi", "sensex" or "nifty" -- so an Indian
    headline scored zero, fell out of the relevance gate, and vanished.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp  # noqa: E402


def _row(symbol, open_, high, low, change=1.0, **extra):
    row = {"symbol": symbol, "name": f"{symbol} Inc", "open": open_,
           "day_high": high, "day_low": low, "change_pct": change,
           "price": high, "prev_close": open_}
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Selection: exact match on the displayed price
# ---------------------------------------------------------------------------
def test_open_equal_to_low_is_selected():
    low, high = webapp._select_open_extremes([_row("BLTE", 172.77, 193.88, 172.77)])
    assert [r["symbol"] for r in low] == ["BLTE"]
    assert high == []


def test_open_equal_to_high_is_selected():
    low, high = webapp._select_open_extremes([_row("FADE", 60.00, 60.00, 51.20, change=-8.0)])
    assert [r["symbol"] for r in high] == ["FADE"]
    assert low == []


def test_a_one_cent_miss_is_not_a_match():
    # COHU in the reference screenshot: opened $0.15 above its low.
    low, high = webapp._select_open_extremes([_row("COHU", 47.15, 50.83, 47.00)])
    assert low == [] and high == []


def test_missing_ohlc_never_counts_as_equal():
    # Two absent fields must not compare equal to each other.
    rows = [
        _row("NOOPEN", None, 10.0, 9.0),
        _row("NOLOW", 10.0, 10.0, None),
        _row("NOHIGH", 9.0, None, 9.0),
    ]
    low, high = webapp._select_open_extremes(rows)
    assert low == [] and high == []


def test_a_flat_session_appears_in_neither_board():
    # open == high == low is a symbol that printed one price all session
    # (untraded or halted), not a directional signal.
    low, high = webapp._select_open_extremes([_row("HALT", 25.0, 25.0, 25.0, change=0.0)])
    assert low == [] and high == []


def test_rows_are_deduped_by_symbol_and_ranked_by_move():
    rows = [
        _row("SMALL", 10.0, 12.0, 10.0, change=2.0),
        _row("BIG", 20.0, 30.0, 20.0, change=25.0),
        _row("SMALL", 10.0, 12.0, 10.0, change=2.0),  # same name from a second source
    ]
    low, _ = webapp._select_open_extremes(rows)
    assert [r["symbol"] for r in low] == ["BIG", "SMALL"], "strongest move first, no duplicates"


def test_the_board_is_capped():
    rows = [_row(f"T{i}", 10.0, 12.0, 10.0, change=float(i)) for i in range(40)]
    low, _ = webapp._select_open_extremes(rows, limit=8)
    assert len(low) == 8


def test_selection_never_mutates_the_caller_rows():
    original = _row("KEEP", 10.0, 12.0, 10.0)
    snapshot = dict(original)
    webapp._select_open_extremes([original])
    assert original == snapshot


# ---------------------------------------------------------------------------
# India works on the same code path as the US
# ---------------------------------------------------------------------------
def test_indian_rows_select_identically():
    # India has no Yahoo screener, so its rows come from the fast_info path.
    # The selection must not care where the OHLC came from.
    rows = [_row("RELIANCE.NS", 1402.5, 1455.0, 1402.5, change=3.4)]
    low, _ = webapp._select_open_extremes(rows)
    assert [r["symbol"] for r in low] == ["RELIANCE.NS"]


# ---------------------------------------------------------------------------
# Per-market news feeds
# ---------------------------------------------------------------------------
def test_us_feeds_are_the_us_wires():
    names = [f["name"] for f in webapp._feeds_for_market("US")]
    assert "WSJ Markets" in names
    assert not any("Economic Times" in n for n in names), "no Indian feed on the US tape"


def test_india_feeds_are_indian_sources():
    names = [f["name"] for f in webapp._feeds_for_market("IN")]
    assert any("Economic Times" in n for n in names)
    assert any("Business Standard" in n or "Mint" in n for n in names)
    # Purely domestic US outlets do not belong on the Indian tape.
    assert "MarketWatch Top Stories" not in names
    assert "CNBC Politics" not in names


def test_india_keeps_the_global_macro_wires():
    # Indian equities trade off the Fed, crude and the dollar too, so the
    # global wires stay -- dropping them would make the tape parochial.
    names = [f["name"] for f in webapp._feeds_for_market("IN")]
    assert any("Reuters" in n or "Financial Times" in n for n in names)


def test_an_unknown_market_falls_back_to_us():
    assert webapp._feeds_for_market("ZZ") == webapp._feeds_for_market("US")


@pytest.mark.parametrize("code", ["IN", "in", "India", "NSE"])
def test_india_is_recognised_however_it_is_spelled(code):
    names = [f["name"] for f in webapp._feeds_for_market(code)]
    assert any("Economic Times" in n for n in names)


# ---------------------------------------------------------------------------
# The classifier must understand Indian market vocabulary
# ---------------------------------------------------------------------------
INDIAN_HEADLINES = [
    ("Sensex ends 400 pts lower as RBI holds repo rate", "Nifty slips below 24,000"),
    ("Nifty hits record high on FII inflows", "Broad-based rally led by banks"),
    ("RBI keeps repo rate unchanged at 6.5%", "Governor flags inflation risk"),
    ("Rupee weakens past 84 against the dollar", "Importers rush to hedge"),
    ("SEBI tightens derivatives rules", "Exchanges to raise margins"),
]


@pytest.mark.parametrize("title,description", INDIAN_HEADLINES)
def test_indian_headlines_survive_the_relevance_gate(title, description):
    category = webapp._article_category(title, description)
    article = {
        "title": title,
        "description": description,
        "category": category,
        "category_score": webapp._article_category_score(title, description, category),
        "impact": webapp._article_impact(title, description, "Economic Times Markets"),
    }
    assert webapp._is_actionable_catalyst(article), (
        f"{title!r} classified as {category!r} and was dropped from the tape")


def test_indian_headlines_score_a_real_impact():
    impact = webapp._article_impact(
        "RBI holds repo rate, Sensex falls 400 points", "", "Economic Times Markets")
    assert impact >= 5, "an RBI rate decision is a high-impact event, not noise"


def test_us_classification_is_unchanged_by_the_indian_vocabulary():
    # The Indian terms are additive: they must not reclassify a US headline.
    title = "Fed holds rates steady as CPI cools"
    assert webapp._article_category(title, "") == "macro"
    assert webapp._article_impact(title, "", "WSJ Markets") >= 6


# ---------------------------------------------------------------------------
# Home-market ranking
# ---------------------------------------------------------------------------
def _article(title, impact):
    return {"title": title, "description": "", "impact": impact}


def test_a_domestic_story_outranks_a_syndicated_foreign_one():
    # Indian outlets carry a lot of US coverage. On the India tape a Nifty
    # wrap must not sit below "US stocks end lower" just because the US
    # headline matched more of the US macro lexicon.
    nifty = _article("Market wrap: Sensex ends 400 pts lower, Nifty below 24,000", 20)
    us = _article("US stocks end lower as jobs data fuels hawkish Fed bets", 30)
    assert (us["impact"] + webapp._home_market_bonus(us, "IN")) < \
           (nifty["impact"] + webapp._home_market_bonus(nifty, "IN"))


def test_the_bonus_is_ordering_only_and_never_edits_impact():
    article = _article("Sensex rallies 500 points", 12)
    webapp._home_market_bonus(article, "IN")
    assert article["impact"] == 12, "the published impact a card shows is untouched"


def test_global_macro_still_ranks_on_the_india_tape():
    # A big Fed story must not be suppressed, only outranked by domestic news
    # of comparable weight.
    fed = _article("Fed cuts rates by 50bps in emergency move", 30)
    minor = _article("Sensex flat in listless trade", 4)
    assert (fed["impact"] + webapp._home_market_bonus(fed, "IN")) > \
           (minor["impact"] + webapp._home_market_bonus(minor, "IN"))


def test_us_tape_prefers_us_stories():
    us = _article("S&P 500 hits record as Fed holds", 20)
    ind = _article("Sensex ends 400 pts lower", 20)
    assert webapp._home_market_bonus(us, "US") > webapp._home_market_bonus(ind, "US")


def test_a_foreign_market_story_ranks_down_on_the_home_tape():
    us_on_india = _article("US stocks end lower as jobs data fuels hawkish Fed bets", 30)
    assert webapp._home_market_bonus(us_on_india, "IN") < 0


def test_a_general_global_story_is_neither_boosted_nor_penalised():
    # Crude and the dollar move every market; they belong to no single tape.
    oil = _article("Oil jumps 4% after OPEC+ output cut", 18)
    assert webapp._home_market_bonus(oil, "IN") == 0
    assert webapp._home_market_bonus(oil, "US") == 0


def test_a_domestic_story_about_a_foreign_event_still_counts_as_domestic():
    # "What the Fed means for Indian markets" is Indian coverage.
    article = _article("Fed pause lifts Nifty to a record; rupee steadies", 15)
    assert webapp._home_market_bonus(article, "IN") > 0


# ---------------------------------------------------------------------------
# Lexicon precision: three-letter acronyms are tokens, not substrings
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title", [
    "Defense stocks rally on new Pentagon budget",     # 'nse' inside "defense"
    "Analysts observed a shift in consensus estimates",  # 'bse'/'nse' inside both
    "Congress licenses new spectrum in a sensible deal",  # 'nse' inside "licenses"
])
def test_english_words_containing_indian_acronyms_are_not_indian_news(title):
    article = {"title": title, "description": "", "impact": 10}
    assert webapp._home_market_bonus(article, "IN") <= 0, (
        f"{title!r} was scored as Indian market news by substring match")


def test_a_real_acronym_still_matches_as_a_whole_token():
    article = {"title": "NSE and BSE extend trading hours", "description": "", "impact": 10}
    assert webapp._home_market_bonus(article, "IN") > 0


def test_whole_token_matching_removes_an_older_false_positive():
    # "sec" used to score inside "second" and "sector".
    assert webapp._has_term("the sec opened an inquiry", "sec")
    assert not webapp._has_term("second quarter sector rotation", "sec")


def test_the_regime_engine_drivers_lead_with_the_home_market():
    # The headline bar reads marketDrivers, so a foreign story leading it is
    # the most visible way for the tape to contradict the country toggle.
    news = {"groups": {"big_news": [
        {"title": "US stocks end lower as jobs data fuels hawkish Fed bets",
         "description": "", "impact": 30, "home_rank": -10, "direction": "bearish",
         "category": "macro", "source": "Economic Times Markets"},
        {"title": "Sensex ends 400 pts lower as RBI holds repo rate",
         "description": "", "impact": 20, "home_rank": 10, "direction": "bearish",
         "category": "macro", "source": "Economic Times Markets"},
    ]}}
    drivers = webapp._summarize_news_for_engine(news).get("drivers", [])
    assert drivers, "high-impact headlines produce drivers"
    assert "Sensex" in drivers[0]["title"], "the domestic story leads the headline bar"


def test_the_digest_stamps_a_home_rank_on_every_article():
    # Downstream consumers rank on this field rather than each being handed
    # the market code, so it must always be present.
    article = {"title": "Sensex rallies", "description": "", "impact": 10}
    assert webapp._home_market_bonus(article, "IN") == webapp.HOME_MARKET_BONUS
