"""
api_manager.py - Unified API Manager V2.0 COMPLETE
Handles all external API integrations with error handling and rate limiting
"""

import logging
import requests
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from urllib.parse import urlsplit
import pandas as pd
from config import API_KEYS, RATE_LIMITER

logger = logging.getLogger(__name__)


def _redact_url(url: Optional[str]) -> str:
    """Scheme, host and path only — never the query string.

    Provider URLs carry API keys as query parameters (`?apikey=...`), and
    requests embeds the full URL in its exception messages. Printing those
    exceptions therefore printed live credentials. Keeping the endpoint is
    enough to debug a provider failure; the query never is.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
        if not parts.scheme and not parts.netloc:
            return str(url).split("?", 1)[0]
        redacted = f"{parts.scheme}://{parts.netloc}{parts.path}"
        return redacted + "?<redacted>" if parts.query else redacted
    except Exception:
        return "<unparseable url>"


class EnhancedAPIManager:
    """Manage all API integrations for stock analysis."""

    def __init__(self):
        self.fmp_key = API_KEYS.fmp
        self.polygon_key = API_KEYS.polygon
        self.finnhub_key = API_KEYS.finnhub
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'HomeStockAnalyzer/1.0'})

        # Track API health
        self.api_health = {
            'FMP': True,
            'Finnhub': True,
            'Polygon': True
        }

    def _safe_request(self, url: str, api_name: str, timeout: int = 10) -> Optional[dict]:
        """Make a safe API request with error handling."""
        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()

            # Mark API as healthy
            self.api_health[api_name] = True
            return data

        # Failures are logged with the PROVIDER, the HTTP status and a redacted
        # endpoint. The exception object itself is never formatted into the
        # message: `str(e)` on a requests exception contains the full URL,
        # including the API key in its query string.
        except requests.exceptions.Timeout:
            logger.warning("Timeout for %s (%s)", api_name, _redact_url(url))
            self.api_health[api_name] = False
            return None
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 429:
                logger.warning("Rate limit (429) for %s (%s)", api_name, _redact_url(url))
            else:
                logger.warning("HTTP %s from %s (%s)", status, api_name, _redact_url(url))
            self.api_health[api_name] = False
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(
                "Request error for %s (%s): %s",
                api_name, _redact_url(url), type(e).__name__,
            )
            self.api_health[api_name] = False
            return None
        except ValueError as e:
            logger.warning(
                "Malformed JSON from %s (%s): %s",
                api_name, _redact_url(url), type(e).__name__,
            )
            self.api_health[api_name] = False
            return None
        except Exception as e:
            # Same rule as the handlers above: the exception's own message may
            # quote the request URL, and these URLs carry the API key in their
            # query string. Log the TYPE and a redacted endpoint only -- not
            # the message and not a traceback, either of which can quote it.
            logger.warning(
                "Unexpected error for %s (%s): %s",
                api_name, _redact_url(url), type(e).__name__,
            )
            self.api_health[api_name] = False
            return None

    # ==================== FINANCIAL MODELING PREP APIs ====================

    def get_insider_trading(self, ticker: str) -> Dict:
        """
        Get insider trading data from FMP.
        FREE TIER: 250 calls/day
        """
        if not RATE_LIMITER.can_call('FMP', 250, 'day'):
            print("⚠️  FMP daily limit reached (250 calls)")
            return {}

        print(f"📊 Fetching insider trading for {ticker}...")

        url = f"https://financialmodelingprep.com/api/v4/insider-trading?symbol={ticker}&page=0&apikey={self.fmp_key}"
        data = self._safe_request(url, 'FMP')

        if not data or not isinstance(data, list):
            return {}

        # Analyze insider activity (last 6 months)
        six_months_ago = datetime.now() - timedelta(days=180)

        recent_transactions = []
        total_buy_value = 0
        total_sell_value = 0
        buy_count = 0
        sell_count = 0

        for transaction in data[:50]:  # Analyze last 50 transactions
            try:
                trans_date_str = transaction.get('transactionDate', '')
                if not trans_date_str:
                    continue

                trans_date = datetime.strptime(trans_date_str, '%Y-%m-%d')
                if trans_date < six_months_ago:
                    continue

                trans_type = transaction.get('transactionType', '')
                shares = abs(transaction.get('securitiesTransacted', 0))
                price = transaction.get('price', 0)

                if shares == 0 or price == 0:
                    continue

                value = shares * price

                if 'P-Purchase' in trans_type or 'Buy' in trans_type:
                    total_buy_value += value
                    buy_count += 1
                elif 'S-Sale' in trans_type or 'Sell' in trans_type:
                    total_sell_value += value
                    sell_count += 1

                recent_transactions.append({
                    'date': trans_date_str,
                    'name': transaction.get('reportingName', 'Unknown'),
                    'type': trans_type,
                    'shares': shares,
                    'price': price,
                    'value': value
                })
            except Exception as e:
                continue

        # Calculate insider sentiment score
        net_value = total_buy_value - total_sell_value
        net_count = buy_count - sell_count

        if buy_count + sell_count > 0:
            buy_ratio = buy_count / (buy_count + sell_count)
        else:
            buy_ratio = 0.5

        # Sentiment scoring
        if buy_ratio > 0.7 and net_value > 0:
            sentiment = "STRONG BUY"
            score_impact = 15
        elif buy_ratio > 0.6 and net_value > 0:
            sentiment = "BULLISH"
            score_impact = 10
        elif buy_ratio < 0.3 and net_value < 0:
            sentiment = "BEARISH"
            score_impact = -12
        elif buy_ratio < 0.4 and net_value < 0:
            sentiment = "NEGATIVE"
            score_impact = -8
        else:
            sentiment = "NEUTRAL"
            score_impact = 0

        return {
            'insider_buys': buy_count,
            'insider_sells': sell_count,
            'total_buy_value': total_buy_value,
            'total_sell_value': total_sell_value,
            'net_value': net_value,
            'buy_ratio': buy_ratio,
            'sentiment': sentiment,
            'score_impact': score_impact,
            'recent_transactions': recent_transactions[:10],
            'analysis_period': '6 months'
        }

    def get_institutional_ownership(self, ticker: str) -> Dict:
        """
        Get institutional ownership changes from FMP.
        FREE TIER: 250 calls/day
        """
        if not RATE_LIMITER.can_call('FMP', 250, 'day'):
            print("⚠️  FMP daily limit reached")
            return {}

        print(f"🏛️  Fetching institutional ownership for {ticker}...")

        url = f"https://financialmodelingprep.com/api/v3/institutional-holder/{ticker}?apikey={self.fmp_key}"
        data = self._safe_request(url, 'FMP')

        if not data or not isinstance(data, list):
            return {}

        # Analyze top institutions and changes
        total_shares = sum(inst.get('totalShares', 0) for inst in data)
        total_value = sum(inst.get('totalValue', 0) for inst in data)

        increasing = []
        decreasing = []

        for inst in data[:20]:
            change = inst.get('change', 0)
            if change > 0:
                increasing.append(inst)
            elif change < 0:
                decreasing.append(inst)

        # Calculate institutional sentiment
        if len(increasing) > len(decreasing) * 1.5:
            sentiment = "STRONG INSTITUTIONAL BUYING"
            score_impact = 12
        elif len(increasing) > len(decreasing):
            sentiment = "INSTITUTIONAL ACCUMULATION"
            score_impact = 8
        elif len(decreasing) > len(increasing) * 1.5:
            sentiment = "INSTITUTIONAL SELLING"
            score_impact = -10
        else:
            sentiment = "NEUTRAL INSTITUTIONAL ACTIVITY"
            score_impact = 0

        return {
            'total_institutions': len(data),
            'total_shares_held': total_shares,
            'total_value_held': total_value,
            'institutions_increasing': len(increasing),
            'institutions_decreasing': len(decreasing),
            'sentiment': sentiment,
            'score_impact': score_impact,
            'top_holders': data[:5],
            'recent_increases': increasing[:3],
            'recent_decreases': decreasing[:3]
        }

    def get_finnhub_news(self, ticker: str, days: int = 7) -> List[Dict]:
        """
        Get company news from Finnhub.
        FREE TIER: 60 calls/minute
        """
        if not RATE_LIMITER.can_call('Finnhub', 60, 'minute'):
            print("⚠️  Finnhub rate limit reached (60/min)")
            return []

        print(f"📰 Fetching news from Finnhub for {ticker}...")

        from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        to_date = datetime.now().strftime('%Y-%m-%d')

        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={from_date}&to={to_date}&token={self.finnhub_key}"
        data = self._safe_request(url, 'Finnhub')

        if not data or not isinstance(data, list):
            return []

        processed_news = []
        for article in data[:20]:
            try:
                timestamp = article.get('datetime', 0)
                if timestamp:
                    published = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
                else:
                    published = "Recent"

                processed_news.append({
                    'title': article.get('headline', ''),
                    'source': article.get('source', 'Unknown'),
                    'url': article.get('url', ''),
                    'published': published,
                    'summary': article.get('summary', ''),
                    'sentiment': 0,
                    'api_source': 'Finnhub'
                })
            except Exception as e:
                continue

        return processed_news

    def get_basic_financials(self, ticker: str) -> Dict:
        """
        Get basic financial metrics from Finnhub.
        FREE TIER: 60 calls/minute
        """
        if not RATE_LIMITER.can_call('Finnhub', 60, 'minute'):
            return {}

        print(f"💰 Fetching basic financials for {ticker}...")

        url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={self.finnhub_key}"
        data = self._safe_request(url, 'Finnhub')

        if not data or 'metric' not in data:
            return {}

        metrics = data['metric']

        return {
            'pe_ratio': metrics.get('peNormalizedAnnual', 0),
            'ps_ratio': metrics.get('psAnnual', 0),
            'pb_ratio': metrics.get('pbAnnual', 0),
            'dividend_yield': metrics.get('dividendYieldIndicatedAnnual', 0),
            'roe': metrics.get('roeTTM', 0),
            'roa': metrics.get('roaTTM', 0),
            'beta': metrics.get('beta', 1),
            '52w_high': metrics.get('52WeekHigh', 0),
            '52w_low': metrics.get('52WeekLow', 0)
        }

    # ==================== COMBINED ANALYSIS ====================

    def get_comprehensive_data(self, ticker: str) -> Dict:
        """
        Get all available data from all APIs in one call.
        """
        print(f"\n{'='*90}")
        print(f"🔄 FETCHING COMPREHENSIVE DATA FOR {ticker}")
        print(f"{'='*90}\n")

        comprehensive_data = {
            'ticker': ticker,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_sources': [],
            'api_health': {}
        }

        # 1. Insider Trading (FMP)
        insider = self.get_insider_trading(ticker)
        if insider:
            comprehensive_data['insider_trading'] = insider
            comprehensive_data['data_sources'].append('FMP - Insider Trading')
        time.sleep(0.5)

        # 2. Institutional Ownership (FMP)
        institutional = self.get_institutional_ownership(ticker)
        if institutional:
            comprehensive_data['institutional_ownership'] = institutional
            comprehensive_data['data_sources'].append('FMP - Institutional')
        time.sleep(0.5)

        # 3. News (Finnhub)
        news = self.get_finnhub_news(ticker)
        if news:
            comprehensive_data['enhanced_news'] = news
            comprehensive_data['data_sources'].append('Finnhub - News')
        time.sleep(1)

        # 4. Basic Financials (Finnhub)
        financials = self.get_basic_financials(ticker)
        if financials:
            comprehensive_data['finnhub_financials'] = financials
            comprehensive_data['data_sources'].append('Finnhub - Financials')

        # Store API health
        comprehensive_data['api_health'] = self.api_health.copy()

        print(f"\n✅ Data fetched from {len(comprehensive_data['data_sources'])} sources")
        print(f"{'='*90}\n")

        return comprehensive_data