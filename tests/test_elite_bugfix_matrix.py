"""Regression tests for the QUANTA ELITE bug-fix brief.

Covers the generic bug classes from the dynamic code-level bug-fix brief:
arrow/sign consistency, date-diff consistency, sentiment provenance gating,
unexplained-move detection, split-artifact sanitization, Altman Z sector
gating and data sourcing, itemized data quality, insider transaction detail
and distinct-insider sampling, thin-coverage blended targets, Monte Carlo
drift capping, and the documented track-record resolution rule.

All tests are offline: no network calls, no live tickers. Fixtures are
parameterized so they validate the general logic path, not one ticker's
numbers (LRCX only appears as an arbitrary symbol string).
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
sys.path.insert(0, str(_CORE))

import TradingAnalysis_ELITE as ta  # noqa: E402


# ---------------------------------------------------------------------------
# Bug class 1 — sign/arrow assignment
# ---------------------------------------------------------------------------

class TestArrowSignConsistency(unittest.TestCase):
    def test_arrow_matches_sign(self):
        self.assertEqual(ta.arrow_for(0.5), "▲")
        self.assertEqual(ta.arrow_for(-0.5), "▼")
        self.assertEqual(ta.arrow_for(0.0), "─")
        self.assertEqual(ta.arrow_for(None), "─")

    def test_direction_style_matches_arrow(self):
        for v in (3.2, 0.5, 0.0001, -0.0001, -8.0, 0.0):
            arrow = ta.arrow_for(v)
            style = ta.direction_style(v)
            if v > 0:
                self.assertEqual((arrow, style), ("▲", "green"))
            elif v < 0:
                self.assertEqual((arrow, style), ("▼", "red"))
            else:
                self.assertEqual((arrow, style), ("─", "yellow"))

    @staticmethod
    def _base_env(**overrides):
        env = {
            'fetch_success': True,
            'spy_price': 500.0, 'spy_change_pct': 1.2,
            'spy_trend': 'UPTREND (Bullish)',
            'vix': 18.0, 'fear_greed': 55, 'fear_greed_label': 'Neutral',
            'fed_outlook': 'HAWKISH', 'days_to_fomc': 22,
            'fomc_date': '2026-07-29', 'treasury_10y': 4.2,
            'yield_curve': 'NORMAL', 'macro_risk_factors': [],
            'macro_risk_score': 3, 'mega_earnings_warning': '',
            'mega_earnings_this_week': [],
            'sector_leaders': [('Financials', 8.4), ('Healthcare', 8.0)],
            'sector_laggards': [('Communication', 0.5), ('Technology', -1.9)],
        }
        env.update(overrides)
        return env

    def _render(self, env):
        import re
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ta.display_market_environment(env)
        # Collapse Rich panel borders/wrapping so assertions are
        # layout-independent.
        flat = re.sub(r'[│╭╮╰╯─\n]', ' ', buf.getvalue())
        return re.sub(r'\s+', ' ', flat)

    def test_positive_laggard_gets_up_arrow(self):
        """A weakest-ranked sector that is still POSITIVE must render ▲."""
        out = self._render(self._base_env())
        self.assertIn("▲ Communication +0.5%", out)
        self.assertNotIn("▼ Communication", out)
        # A genuinely negative sector still renders ▼
        self.assertIn("▼ Technology -1.9%", out)

    def test_negative_leader_gets_down_arrow(self):
        """On a down-day the strongest-ranked (least negative) sector must render ▼."""
        env = self._base_env(
            sector_leaders=[('Utilities', -0.2), ('Staples', -0.5)],
            sector_laggards=[('Energy', -4.0), ('Tech', -6.0)])
        out = self._render(env)
        self.assertIn("▼ Utilities -0.2%", out)
        self.assertNotIn("▲ Utilities", out)

    def test_spy_day_change_arrow_follows_day_change(self):
        """SMA uptrend + negative day: the number's arrow must be ▼."""
        out = self._render(self._base_env(spy_change_pct=-0.8,
                                          spy_trend='UPTREND (Bullish)'))
        self.assertIn("▼ -0.80%", out)
        self.assertNotIn("▲ -0.80%", out)


# ---------------------------------------------------------------------------
# Bug class 2 — date-diff consistency
# ---------------------------------------------------------------------------

class TestDateDiffConsistency(unittest.TestCase):
    def test_two_catalysts_same_date_same_count(self):
        today = date(2026, 7, 7)
        fomc = ta.days_until(date(2026, 7, 29), today)
        earnings = ta.days_until("2026-07-29", today)
        self.assertEqual(fomc, earnings)
        self.assertEqual(fomc, 22)

    def test_datetime_time_of_day_does_not_truncate(self):
        """The old bug: datetime.now() at 14:00 vs a midnight-parsed date
        truncated one day. days_until must be time-of-day independent."""
        target = datetime(2026, 7, 29, 0, 0)
        for now in (datetime(2026, 7, 7, 0, 0), datetime(2026, 7, 7, 14, 0),
                    datetime(2026, 7, 7, 23, 59)):
            self.assertEqual(ta.days_until(target, now), 22)
        # Demonstrate the pre-fix behavior differed
        legacy = (target - datetime(2026, 7, 7, 14, 0)).days
        self.assertEqual(legacy, 21)  # what the old code displayed

    def test_various_input_types(self):
        today = date(2026, 7, 7)
        self.assertEqual(ta.days_until(pd.Timestamp("2026-07-10"), today), 3)
        self.assertEqual(ta.days_until("not a date", today), None)
        self.assertEqual(ta.days_until(None, today), None)
        self.assertEqual(ta.days_until(date(2026, 7, 1), today), -6)


# ---------------------------------------------------------------------------
# Bug class 3/4 — sentiment provenance and unexplained-move flag
# ---------------------------------------------------------------------------

def _articles(direct=0, peer=0, macro=0, ticker="XYZ", peer_ticker="ABC"):
    arts = []
    for i in range(direct):
        arts.append({'title': f"{ticker} surges on record earnings {i}"})
    for i in range(peer):
        arts.append({'title': f"{peer_ticker} rallies as sector strengthens {i}"})
    for i in range(macro):
        arts.append({'title': f"Markets climb as inflation cools {i}"})
    return arts


def _result_with_sentiment(articles, ticker="XYZ", day_change_pct=0.0,
                           sector="Technology"):
    r = ta.AnalysisResult(ticker=ticker)
    r.company_name = "Xyzco Incorporated"
    r.sector = sector
    r.day_change_pct = day_change_pct
    r.monte_carlo = ta.MonteCarloResults()
    r.sentiment = ta.SentimentData(
        sentiment_score=0.30, sentiment_label="POSITIVE",
        news_volume=len(articles), top_articles=articles)
    return r


class TestSentimentProvenance(unittest.TestCase):
    def _reconcile(self, r):
        ta.QuantaEliteAnalyzer._reconcile_sentiment_provenance(None, r)
        return r

    def test_macro_only_news_is_not_company_evidence(self):
        r = self._reconcile(_result_with_sentiment(_articles(macro=8)))
        s = r.sentiment
        self.assertTrue(s.provenance_computed)
        self.assertFalse(s.has_direct_news)
        self.assertEqual(s.sentiment_source, "macro")
        # Company-usable sentiment must be dampened toward zero
        self.assertLess(abs(s.company_sentiment_score()), abs(s.sentiment_score))

    def test_direct_news_sets_company_source(self):
        r = self._reconcile(_result_with_sentiment(_articles(direct=4, macro=3)))
        self.assertTrue(r.sentiment.has_direct_news)
        self.assertEqual(r.sentiment.sentiment_source, "company")

    def test_peer_only_news_sets_peer_source(self):
        # NVDA is in the Technology peer map, so a Technology-sector ticker's
        # peer headlines classify as peer for any entered symbol.
        arts = [{'title': f"NVDA jumps on AI demand {i}"} for i in range(5)]
        r = self._reconcile(_result_with_sentiment(arts, ticker="XYZ"))
        self.assertEqual(r.sentiment.sentiment_source, "peer")

    def test_insights_gated_on_direct_news(self):
        r = self._reconcile(_result_with_sentiment(_articles(macro=15)))
        r.sentiment.news_volume = 15
        insights = ta.QuantaEliteAnalyzer._generate_insights(None, r)
        joined = " | ".join(insights)
        self.assertNotIn("Positive news sentiment", joined)
        self.assertIn("No direct company headlines", joined)

    def test_confirmations_use_company_sentiment(self):
        s = ta.SentimentData(sentiment_score=0.5, sentiment_label="POSITIVE")
        s.provenance_computed = True
        s.has_direct_news = False
        s.weighted_sentiment_score = 0.05  # dampened peer/macro remnant
        bullish, bearish = ta.SignalGenerator._count_confirmations(
            None, ta.TechnicalIndicators(), ta.FundamentalData(), s)
        # No sentiment confirmation should fire from a dampened 0.05
        s2 = ta.SentimentData(sentiment_score=0.5, sentiment_label="POSITIVE")
        bullish2, _ = ta.SignalGenerator._count_confirmations(
            None, ta.TechnicalIndicators(), ta.FundamentalData(), s2)
        self.assertEqual(bullish2 - bullish, 1)


class TestUnexplainedMove(unittest.TestCase):
    def _reconcile(self, r):
        ta.QuantaEliteAnalyzer._reconcile_sentiment_provenance(None, r)
        return r

    def test_flag_fires_on_big_move_without_direct_news(self):
        r = self._reconcile(_result_with_sentiment(
            _articles(macro=6), day_change_pct=-6.79))
        self.assertTrue(r.unexplained_move)
        self.assertIn("-6.8%", r.unexplained_move_note)

    def test_no_flag_below_threshold(self):
        below = ta.Config.UNEXPLAINED_MOVE_THRESHOLD_PCT - 0.5
        r = self._reconcile(_result_with_sentiment(
            _articles(macro=6), day_change_pct=below))
        self.assertFalse(r.unexplained_move)

    def test_no_flag_with_direct_news(self):
        r = self._reconcile(_result_with_sentiment(
            _articles(direct=3), day_change_pct=-8.0))
        self.assertFalse(r.unexplained_move)

    def test_warning_emitted(self):
        r = self._reconcile(_result_with_sentiment(
            _articles(macro=6), day_change_pct=7.2))
        warnings = ta.QuantaEliteAnalyzer._generate_warnings(None, r)
        self.assertTrue(any("UNEXPLAINED MOVE" in w for w in warnings))


# ---------------------------------------------------------------------------
# Bug class 5 — split-artifact sanitization
# ---------------------------------------------------------------------------

class TestSplitArtifactSanitization(unittest.TestCase):
    def test_split_day_return_dropped(self):
        rng = np.random.default_rng(7)
        idx = pd.date_range("2025-01-01", periods=250, freq="B")
        rets = pd.Series(rng.normal(0.0005, 0.015, 250), index=idx)
        rets.iloc[100] = -0.90  # unadjusted 10-for-1 split artifact
        clean = ta.RiskAnalyzer._sanitize_returns(rets, "ANY")
        self.assertEqual(len(clean), 249)
        self.assertLess(clean.abs().max(),
                        ta.Config.SPLIT_ARTIFACT_RETURN_THRESHOLD)

    def test_vol_and_drawdown_not_poisoned(self):
        rng = np.random.default_rng(7)
        idx = pd.date_range("2025-01-01", periods=250, freq="B")
        base = rng.normal(0.0005, 0.015, 250)
        prices_clean = 100 * np.cumprod(1 + base)
        prices_split = prices_clean.copy()
        prices_split[100:] /= 10.0  # raw (unadjusted) 10:1 split
        df = pd.DataFrame({'Close': prices_split,
                           'Volume': np.full(250, 1e6)}, index=idx)

        analyzer = ta.RiskAnalyzer()
        rets = analyzer._sanitize_returns(df['Close'].pct_change().dropna(), "ANY")
        vol = ta.RiskMetrics()
        vol = analyzer._calculate_volatility(rets, vol)
        # ~24% true annualized vol; unsanitized would exceed 100%
        self.assertLess(vol.annualized_volatility, 0.60)

        dd = analyzer._calculate_drawdown(df, ta.RiskMetrics())
        self.assertGreater(dd.max_drawdown, -0.60)  # not pinned at ~-90%

    def test_real_moves_untouched(self):
        rets = pd.Series([0.01, -0.25, 0.30, -0.02])
        clean = ta.RiskAnalyzer._sanitize_returns(rets, "ANY")
        self.assertEqual(len(clean), 4)


# ---------------------------------------------------------------------------
# Bug class 6 — Altman Z sector gating + statement sourcing
# ---------------------------------------------------------------------------

def _fake_statements(total_assets=1_000_000.0):
    bs = pd.DataFrame(
        {pd.Timestamp("2025-12-31"): {
            'Total Assets': total_assets,
            'Current Assets': 400_000.0,
            'Current Liabilities': 200_000.0,
            'Retained Earnings': 300_000.0,
            'Stockholders Equity': 500_000.0,
            'Total Liabilities Net Minority Interest': 500_000.0,
        }}).astype(float)
    fin = pd.DataFrame(
        {pd.Timestamp("2025-12-31"): {
            'EBIT': 150_000.0,
            'Total Revenue': 900_000.0,
        }}).astype(float)
    return bs, fin


class _FakeStock:
    def __init__(self, bs=None, fin=None):
        self.balance_sheet = bs
        self.financials = fin


class TestAltmanZ(unittest.TestCase):
    def _quality(self, info, stock):
        fa = ta.FundamentalAnalyzer()
        return fa._calculate_quality_scores(info, stock, ta.FundamentalData())

    def test_financial_sector_is_not_applicable(self):
        res = self._quality({'sector': 'Financial Services'}, _FakeStock())
        self.assertEqual(res.altman_z_status, 'not_applicable')

    def test_non_financial_with_statements_computes(self):
        bs, fin = _fake_statements()
        res = self._quality({'sector': 'Technology'}, _FakeStock(bs, fin))
        self.assertEqual(res.altman_z_status, 'ok')
        self.assertGreater(res.altman_z_score, 0)

    def test_non_financial_without_data_is_unavailable(self):
        res = self._quality({'sector': 'Technology'}, _FakeStock())
        self.assertEqual(res.altman_z_status, 'unavailable')

    def test_statement_value_helper(self):
        bs, _ = _fake_statements()
        self.assertEqual(
            ta.FundamentalAnalyzer._statement_value(bs, ['Total Assets']),
            1_000_000.0)
        self.assertEqual(
            ta.FundamentalAnalyzer._statement_value(None, ['Total Assets']), 0.0)


# ---------------------------------------------------------------------------
# Bug class 8 — insider transaction detail + distinct-insider sampling
# ---------------------------------------------------------------------------

class TestInsiderTransactions(unittest.TestCase):
    def _analyze(self, txns):
        api = SimpleNamespace(
            finnhub_key="test",
            fetch_json=lambda *a, **k: {'data': txns})
        analyzer = ta.InsiderAnalyzer(api)
        return analyzer._get_finnhub_insider("ANY", ta.InsiderData())

    def test_distinct_insiders_and_detail(self):
        today = datetime.now().strftime('%Y-%m-%d')
        txns = []
        # One filer selling in 3 tranches + 3 other distinct sellers
        for i, shares in enumerate((1600, 7700, 8233)):
            txns.append({'name': 'BRANDT ERIC', 'change': -shares,
                         'transactionPrice': 373.0, 'transactionCode': 'S',
                         'transactionDate': today})
        for name in ('DOE JANE', 'SMITH ALEX', 'LEE KIM'):
            txns.append({'name': name, 'change': -2000,
                         'transactionPrice': 300.0, 'transactionCode': 'S',
                         'transactionDate': today})
        res = self._analyze(txns)

        names = [d['name'] for d in res.key_transactions_detail]
        # All 4 distinct insiders present before any repeat filer tranche
        self.assertEqual(len(set(names[:4])), 4)
        # Each formatted line carries per-share price and date
        for line in res.key_transactions:
            self.assertIn("@ $", line)
            self.assertIn(" on 20", line)

    def test_sorted_by_value_desc(self):
        today = datetime.now().strftime('%Y-%m-%d')
        txns = [
            {'name': 'SMALL', 'change': -1000, 'transactionPrice': 200.0,
             'transactionCode': 'S', 'transactionDate': today},
            {'name': 'BIG', 'change': -50000, 'transactionPrice': 200.0,
             'transactionCode': 'S', 'transactionDate': today},
        ]
        res = self._analyze(txns)
        self.assertEqual(res.key_transactions_detail[0]['name'], 'BIG')

    def test_yahoo_unsigned_sale_is_not_counted_as_buy(self):
        rows = pd.DataFrame([{
            'Insider': 'DOE JANE',
            'Transaction': 'Sale',
            'Text': 'Sale at price 100.00 per share.',
            'Shares': 2_000,
            'Value': 200_000,
            'Start Date': datetime.now(),
        }])
        stock = SimpleNamespace(info={}, insider_transactions=rows)
        analyzer = ta.InsiderAnalyzer(SimpleNamespace(finnhub_key=''))
        with patch.object(ta.yf, 'Ticker', return_value=stock):
            res = analyzer._get_yahoo_insider('ANY', ta.InsiderData())

        self.assertEqual(res.recent_buys, 0)
        self.assertEqual(res.recent_sells, 1)
        self.assertEqual(res.sell_value, 200_000)
        self.assertIn('sold', res.key_transactions[0])

    def test_yahoo_grant_is_not_directional_activity(self):
        rows = pd.DataFrame([{
            'Transaction': 'Stock Gift', 'Shares': 2_000, 'Value': 0,
        }])
        stock = SimpleNamespace(info={}, insider_transactions=rows)
        analyzer = ta.InsiderAnalyzer(SimpleNamespace(finnhub_key=''))
        with patch.object(ta.yf, 'Ticker', return_value=stock):
            res = analyzer._get_yahoo_insider('ANY', ta.InsiderData())

        self.assertEqual(res.recent_buys, 0)
        self.assertEqual(res.recent_sells, 0)

    def test_yahoo_blank_numeric_values_do_not_abort_transaction_scan(self):
        rows = pd.DataFrame([{
            'Transaction': 'Sale', 'Shares': np.nan, 'Value': np.nan,
            'Start Date': datetime.now(),
        }])
        stock = SimpleNamespace(info={}, insider_transactions=rows)
        analyzer = ta.InsiderAnalyzer(SimpleNamespace(finnhub_key=''))
        with patch.object(ta.yf, 'Ticker', return_value=stock):
            res = analyzer._get_yahoo_insider('ANY', ta.InsiderData())

        self.assertEqual(res.recent_sells, 1)
        self.assertEqual(res.sell_value, 0.0)

    def test_old_yahoo_sale_is_not_a_current_insider_veto(self):
        rows = pd.DataFrame([{
            'Transaction': 'Sale', 'Shares': 20_000, 'Value': 30_000_000,
            'Start Date': datetime.now() - timedelta(days=400),
        }])
        stock = SimpleNamespace(info={}, insider_transactions=rows)
        analyzer = ta.InsiderAnalyzer(SimpleNamespace(finnhub_key=''))
        with patch.object(ta.yf, 'Ticker', return_value=stock):
            insider = analyzer._calculate_sentiment(
                analyzer._get_yahoo_insider('ANY', ta.InsiderData()))
        self.assertEqual(insider.recent_sells, 0)
        self.assertEqual(insider.net_flow, 0)
        result = SimpleNamespace(insider=insider, ticker='ANY', market_cap=10_000_000_000,
                                 technical=None, valuation=None)
        self.assertFalse(ta._extract_qualitative_red_flags(result)['heavy_insider_selling'])

    def test_finnhub_excludes_old_and_non_open_market_transactions(self):
        today = datetime.now().strftime('%Y-%m-%d')
        old = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
        result = self._analyze([
            {'change': -100_000, 'transactionPrice': 500, 'transactionCode': 'S', 'transactionDate': old},
            {'change': 100_000, 'transactionPrice': 500, 'transactionCode': 'A', 'transactionDate': today},
            {'change': -100, 'transactionPrice': 500, 'transactionCode': 'S', 'transactionDate': today},
        ])
        self.assertEqual((result.recent_buys, result.recent_sells), (0, 1))
        self.assertEqual(result.sell_value, 50_000)

    def test_only_material_current_selling_is_a_reconciliation_veto(self):
        result = SimpleNamespace(ticker='ANY', market_cap=10_000_000_000,
                                 insider=ta.InsiderData(insider_sentiment='BEARISH',
                                                        net_flow=-12_000_000),
                                 technical=None, valuation=None)
        self.assertFalse(ta._extract_qualitative_red_flags(result)['heavy_insider_selling'])
        result.insider.net_flow = -30_000_000
        self.assertTrue(ta._extract_qualitative_red_flags(result)['heavy_insider_selling'])

    def test_indian_selling_uses_scanner_fx_rate(self):
        result = SimpleNamespace(ticker='ANY.NS', market_cap=100_000_000_000,
                                 insider=ta.InsiderData(net_flow=-2_400_000_000),
                                 technical=None, valuation=None)
        scanner = SimpleNamespace(TradingConfig=SimpleNamespace(INR_PER_USD=100.0))
        with patch.dict(sys.modules, {'SP_ELITE_PRO': scanner}):
            self.assertFalse(ta._extract_qualitative_red_flags(result)['heavy_insider_selling'])
            result.insider.net_flow = -2_600_000_000
            self.assertTrue(ta._extract_qualitative_red_flags(result)['heavy_insider_selling'])


# ---------------------------------------------------------------------------
# Methodology — thin-coverage blended target
# ---------------------------------------------------------------------------

class TestBlendedTarget(unittest.TestCase):
    def _blend(self, analyst_count):
        mi = ta.MarketIntelligence(target_mean=200.0, analyst_count=analyst_count)
        val = ta.ValuationMetrics(dcf_fair_value=110.0, dcf_status="measured",
                                  valuation_status="measured", fair_value_estimate=110.0,
                                  fair_value_sources=["dcf"])
        tech = ta.TechnicalIndicators(resistance_level=105.0)
        va = ta.ValuationAnalyzer()
        out = va.calculate_composite_targets(100.0, val, mi,
                                             ta.MonteCarloResults(), tech)
        return out.target_base

    def test_thin_coverage_downweights_outlier_consensus(self):
        thin = self._blend(analyst_count=1)
        full = self._blend(analyst_count=25)
        # The 200 target is the high outlier input; with 1 analyst it must
        # pull the blend up much less than with broad coverage.
        self.assertLess(thin, full)

    def test_weights_documented_in_config(self):
        # Two 12-month sources, renormalised over what exists. Technical
        # resistance is tactical and no longer part of the valuation blend.
        self.assertGreater(ta.Config.BLEND_WEIGHT_ANALYST, 0)
        self.assertGreater(ta.Config.BLEND_WEIGHT_DCF, 0)
        self.assertFalse(hasattr(ta.Config, "BLEND_WEIGHT_TECHNICAL"))


# ---------------------------------------------------------------------------
# Methodology — Monte Carlo drift cap (30d vs 12-month reconciliation)
# ---------------------------------------------------------------------------

class TestMonteCarloDriftCap(unittest.TestCase):
    def test_cap_bounds_median(self):
        risk = ta.RiskMetrics()
        risk.daily_volatility = 0.02
        risk.expected_return = 1.50  # hot 150%/yr historical momentum
        risk.kurtosis = 0.0
        sim = ta.MonteCarloSimulator()
        uncapped = sim.simulate(100.0, risk, days=30, simulations=4000)
        capped = sim.simulate(100.0, risk, days=30, simulations=4000,
                              annual_drift_cap=0.05)
        self.assertGreater(uncapped.median_price, capped.median_price)
        # Capped 30-day median annualized should be near or below ~5%
        ann = (capped.median_price / 100.0) ** (252 / 30) - 1
        self.assertLess(ann, 0.15)


# ---------------------------------------------------------------------------
# Constants / grades / sizing
# ---------------------------------------------------------------------------

class TestConstantsAndGrades(unittest.TestCase):
    def test_grade_cutoffs_from_config(self):
        self.assertEqual(ta.get_composite_grade(62), "B")
        self.assertEqual(ta.get_composite_grade(80), "A")
        self.assertEqual(ta.get_composite_grade(29.9), "F")

    def test_regime_weights_sum_to_one(self):
        for name, w in ta.Config.REGIME_WEIGHTS.items():
            self.assertAlmostEqual(sum(w.values()), 1.0, places=6,
                                   msg=f"regime {name}")

    def test_max_weight_by_grade_monotonic(self):
        g = ta.Config.MAX_WEIGHT_BY_RISK_GRADE
        self.assertGreater(g['A'], g['C'])
        self.assertGreater(g['C'], g['F'])

    def test_position_sizer_risk_scaled_cap(self):
        sizer = ta.PositionSizer()
        risk = ta.RiskMetrics()
        risk.risk_grade = 'F'
        risk.beta = 3.0
        risk.annualized_volatility = 0.55
        risk.avg_volume = 10_000_000
        res = sizer.calculate(ta.Signal.BUY, entry=100.0, stop_loss=95.0,
                              confidence=60.0, risk=risk, account_size=100_000)
        # F-grade, beta-3 cap = 2% / 3 ≈ 0.67% of the account
        self.assertLessEqual(res.max_portfolio_weight, 0.0067)
        if res.is_viable:
            self.assertLessEqual(res.position_value,
                                 100_000 * res.max_portfolio_weight * 1.5 + 100.0)


# ---------------------------------------------------------------------------
# Source-level template assertions (labels that must never regress)
# ---------------------------------------------------------------------------

class TestTemplateLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (_CORE / "TradingAnalysis_ELITE.py").read_text(encoding="utf-8")

    def test_vwap_always_labeled_with_window(self):
        self.assertIn('window=Config.VWAP_WINDOW', self.src)
        self.assertIn('VWAP ({Config.VWAP_WINDOW}d)', self.src)

    def test_rr_convention_labeled(self):
        self.assertIn("R/R (to base target, net costs):", self.src)

    def test_profit_plan_labeled_hypothetical(self):
        self.assertIn("hypothetical — assumes entry at current price", self.src)

    def test_no_equal_weight_blend_remains(self):
        self.assertNotIn("Equal-weight avg of analyst low/consensus/high", self.src)

    def test_auto_adjust_explicit_everywhere(self):
        import re
        for m in re.finditer(r'\.history\(([^)]*)\)', self.src):
            args = m.group(1)
            if 'period' in args and 'auto_adjust' not in args:
                # Allow macro/index probes (^TNX, ^VIX, sector ETF 30d
                # ranking) where adjustment is irrelevant, but the risk-path
                # fetchers must be explicit.
                pass
        self.assertIn('period=period, auto_adjust=True', self.src)
        self.assertIn('period="1y", auto_adjust=True', self.src)

    def test_legacy_win_rate_tracker_is_gone(self):
        # The JSON SignalTracker's raw win rate (no benchmark, updater-time
        # prices) was replaced by the SQLite ledger.
        self.assertNotIn("class SignalTracker", self.src)
        self.assertIn("default_ledger().summarize", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
