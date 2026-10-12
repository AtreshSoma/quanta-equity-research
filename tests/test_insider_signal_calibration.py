"""Regressions for the insider-activity signal.

The engine hard-vetoes new money on a "Heavy insider selling" note, so this
function decides whether a stock can be bought at all. Two defects made that
veto fire on healthy mega-caps:

  1. `sells >= 4 and buys == 0` treated the ABSENCE of open-market purchases as
     evidence of selling. Purchases are 3 of 463 rows in a real mega-cap
     sample, so `buys == 0` is the base case, not a signal.
  2. A fixed $25M "heavy" threshold does not scale, so the larger the company
     the more certainly it tripped. NVDA's routine $839M of 10b5-1 flow is
     0.017% of a $5T cap and was vetoing the highest-scoring name in the scan.
"""

# Ported from ``quanta-review-redesign`` @ 064f747 (audit F15). That branch
# rewrote insider-flow classification (blank or compensation text is
# "unclassified", heavy-selling thresholds scale with market cap, notable selling
# is a haircut rather than a veto). That logic is not on this line of history and
# is outside the Picks audit, so the tests pinning it are strict xfails and are
# listed as unresolved in IMPLEMENTATION_LOG.md. The tests that hold on HEAD
# (explicit purchase/sale recognition, small-cap heavy flow, the committee hard
# veto) run normally and guard the current behaviour.

from datetime import datetime, timedelta

import pandas as pd
import pytest

import SP_ELITE_PRO as sp

BRANCH_ONLY_INSIDER = pytest.mark.xfail(
    strict=True, reason="insider calibration exists only on quanta-review-redesign; not in the Picks audit scope")


def _txns(rows):
    """Build a frame shaped like yfinance's insider_transactions."""
    recent = datetime.now() - timedelta(days=10)
    return pd.DataFrame([
        {'Shares': shares, 'Value': value, 'URL': '', 'Text': text,
         'Insider': 'X', 'Position': 'Officer', 'Transaction': '',
         'Start Date': recent, 'Ownership': 'D'}
        for shares, value, text in rows
    ])


class _Provider:
    def __init__(self, frame):
        self.insider_transactions = frame


def _signal(rows, market_cap=0.0, price=100.0):
    obj = object.__new__(sp.QuantaEliteTradingSystem)
    return sp.QuantaEliteTradingSystem._fetch_insider_signal(
        obj, 'TEST', _Provider(_txns(rows)), price, market_cap)


# ── Classification ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    'Stock Award(Grant) at price 0.00 per share.',
    'Conversion of Exercise of derivative security',
    'Stock Gift at price 0.00 per share.',
])
@BRANCH_ONLY_INSIDER
def test_compensation_is_not_insider_conviction(text):
    """A grant, an option exercise and a gift are pay and mechanics, not a view.

    Counting them as buys is what the previous engine did; it inflated
    `buys_90d` with compensation and called it insider confidence.
    """
    r = _signal([(30_000, 9_000_000, text)], market_cap=1e12)
    assert r['buys_90d'] == 0
    assert r['sells_90d'] == 0
    assert r['compensation_90d'] == 1
    assert r['sentiment'] == 'UNKNOWN'
    assert r['score_adjustment'] == 0.0


@BRANCH_ONLY_INSIDER
def test_blank_text_is_unclassified_not_a_buy():
    r = _signal([(30_104, 0, ''), (240, 0, '')], market_cap=1e12)
    assert r['buys_90d'] == 0 and r['sells_90d'] == 0
    assert r['unclassified_90d'] == 2
    assert r['sentiment'] == 'UNKNOWN'


def test_explicit_purchase_is_still_recognised():
    r = _signal([(20_000, 6_000_000, 'Purchase at price 300.00 per share.'),
                 (20_000, 6_000_000, 'Purchase at price 300.00 per share.')],
                market_cap=1e11)
    assert r['buys_90d'] == 2
    assert r['sentiment'] == 'BULLISH'
    assert r['score_adjustment'] > 0


def test_explicit_sale_is_still_recognised():
    r = _signal([(1_000, 1_000_000, 'Sale at price 1000.00 per share.')],
                market_cap=1e9)
    assert r['sells_90d'] == 1
    assert r['net_value_90d'] < 0


# ── The bug: zero buys is not bearish evidence ──────────────────────────────

@BRANCH_ONLY_INSIDER
def test_zero_buys_with_many_sells_is_not_automatically_heavy():
    """Five routine sales at a mega-cap must not trip the hard veto.

    This is the exact shape of real AAPL data: 0 buys, 5 sales, ~$1.8M net
    against a $3.5T cap. It previously produced "Heavy insider selling" and
    forced the stock to WATCHLIST regardless of score.
    """
    rows = [(1_439, 456_000, 'Sale at price 317.01 per share.') for _ in range(5)]
    r = _signal(rows, market_cap=3.5e12)
    assert r['buys_90d'] == 0 and r['sells_90d'] == 5
    assert r['sentiment'] == 'NEUTRAL', r['note']
    assert 'Heavy' not in r['note']
    assert r['score_adjustment'] == 0.0


@BRANCH_ONLY_INSIDER
def test_nvda_shaped_flow_is_routine_at_mega_cap_scale():
    """$839M of selling is 0.017% of a $5T company — noise, not an alarm."""
    rows = [(700_000, 209_750_000, 'Sale at price 299.64 per share.') for _ in range(4)]
    r = _signal(rows, market_cap=5.0e12)
    assert abs(r['net_value_90d']) == pytest.approx(839_000_000, rel=0.01)
    assert r['sentiment'] == 'NEUTRAL'
    assert 'Heavy' not in r['note']


# ── The threshold must still bite when selling is genuinely heavy ───────────

def test_same_dollar_flow_is_heavy_at_small_cap():
    """The identical $839M is 42% of a $2B company and must veto."""
    rows = [(700_000, 209_750_000, 'Sale at price 299.64 per share.') for _ in range(4)]
    r = _signal(rows, market_cap=2.0e9)
    assert r['sentiment'] == 'BEARISH'
    assert 'Heavy' in r['note']
    assert r['score_adjustment'] == pytest.approx(-0.8)


def test_heavy_note_still_triggers_the_committee_hard_veto():
    """The veto contract is unchanged — only what counts as 'heavy' moved."""
    obj = object.__new__(sp.QuantaEliteTradingSystem)
    signal, notes = sp.QuantaEliteTradingSystem._apply_investment_committee_policy(
        obj, signal=sp.SignalType.BUY.value, score=9.0, entry=100.0, target=130.0,
        stop=90.0, risk_reward=3.0, risk_grade='A',
        insider_sentiment='BEARISH',
        insider_note='Heavy insider selling $500.0M net (12.50% of cap), 9 sells')
    assert signal == sp.SignalType.HOLD.value
    assert any('Insider override' in n for n in notes)


@BRANCH_ONLY_INSIDER
def test_notable_selling_is_a_haircut_not_a_veto():
    # $30M on a $20B cap = 0.15%: above the 0.05% "notable" line, below the
    # 0.30% "heavy" line, so it costs size without blocking the trade.
    rows = [(33_334, 10_000_000, 'Sale at price 300.00 per share.') for _ in range(3)]
    r = _signal(rows, market_cap=2.0e10)
    assert r['sentiment'] == 'BEARISH', r['note']
    assert 'Heavy' not in r['note']
    assert r['score_adjustment'] == pytest.approx(-0.3)


@BRANCH_ONLY_INSIDER
def test_thresholds_scale_monotonically_with_market_cap():
    cfg = sp.TradingConfig
    def heavy(cap):
        return max(cfg.INSIDER_HEAVY_SELL_FLOOR, cfg.INSIDER_HEAVY_SELL_PCT_OF_CAP * cap)
    caps = [2e9, 2e10, 2e11, 5e12]
    limits = [heavy(c) for c in caps]
    assert limits == sorted(limits)
    assert limits[0] == cfg.INSIDER_HEAVY_SELL_FLOOR   # floor protects small caps
    assert limits[-1] > limits[0] * 100               # and genuinely scales
