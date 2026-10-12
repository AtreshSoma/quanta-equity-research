"""
Catalyst Scanner Module for SP_ELITE_PRO

Institutional-grade news-driven catalyst detection system that identifies:
- Product launches and technology adoption
- Major contracts, partnerships, and enterprise deals
- Acquisitions, mergers, and strategic investments
- Regulatory approvals and policy tailwinds

Author: Quanta Elite System
Date: 2025-12-29
"""

import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class CatalystType(Enum):
    """Types of actionable catalysts"""
    PRODUCT_LAUNCH = "product_launch"
    CONTRACT = "contract"
    MNA = "mna"  # Mergers & Acquisitions
    REGULATORY = "regulatory"
    EARNINGS = "earnings"
    PARTNERSHIP = "partnership"
    MANAGEMENT = "management"
    UNKNOWN = "unknown"


@dataclass
class Catalyst:
    """Represents a detected catalyst event"""
    type: CatalystType
    headline: str
    confidence: float          # 0.0 to 1.0
    sentiment: float           # -1.0 to 1.0
    weight: float              # Signal weight contribution
    keywords_matched: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def is_actionable(self) -> bool:
        """Check if catalyst is actionable (high confidence + positive sentiment)"""
        return self.confidence >= 0.5 and self.sentiment > 0.2


@dataclass
class CatalystSignal:
    """Trading signal derived from catalyst detection"""
    ticker: str
    catalysts: List[Catalyst]
    aggregate_weight: float
    volume_confirmed: bool
    recommended_action: str    # BUY, HOLD, AVOID
    entry_timing: str          # IMMEDIATE, PULLBACK, WAIT
    hold_period_days: int
    stop_loss_pct: float
    target_pct: float


class CatalystScanner:
    """
    Scan news headlines for actionable trading catalysts.
    
    Uses keyword pattern matching and NLP-style scoring to identify
    the same catalysts professional hedge funds trade on.
    """
    
    # Catalyst keyword patterns (case-insensitive)
    CATALYST_PATTERNS = {
        CatalystType.PRODUCT_LAUNCH: {
            'keywords': [
                'launch', 'launches', 'released', 'unveil', 'unveiled', 'introduce',
                'rollout', 'new product', 'next-gen', 'AI chip', 'breakthrough',
                'innovation', 'revolutionary', 'game-changing', 'first-of-its-kind'
            ],
            'weight': 0.15,
            'typical_impact': '+5-15%',
            'hold_days': 10
        },
        CatalystType.CONTRACT: {
            'keywords': [
                'contract', 'deal', 'agreement', 'awarded', 'wins', 'secures',
                'partnership', 'enterprise', 'billion', 'million', 'government',
                'defense', 'DOD', 'Pentagon', 'multi-year', 'exclusive'
            ],
            'weight': 0.12,
            'typical_impact': '+3-10%',
            'hold_days': 20
        },
        CatalystType.MNA: {
            'keywords': [
                'acquire', 'acquisition', 'merger', 'takeover', 'buyout', 'purchase',
                'bid', 'offer', 'strategic investment', 'stake', 'all-cash deal',
                'premium', 'per share'
            ],
            'weight': 0.20,
            'typical_impact': '+20-50% (target)',
            'hold_days': 30
        },
        CatalystType.REGULATORY: {
            'keywords': [
                'FDA', 'approved', 'approval', 'clearance', 'patent', 'granted',
                'regulatory', 'license', 'certification', 'PDUFA', 'NDA accepted',
                'breakthrough therapy', 'fast track', 'priority review'
            ],
            'weight': 0.18,
            'typical_impact': '+10-30%',
            'hold_days': 7
        },
        CatalystType.EARNINGS: {
            'keywords': [
                'beats', 'exceeds', 'surpasses', 'guidance', 'raised', 'raises',
                'outlook', 'record revenue', 'record profit', 'blowout',
                'surprise', 'better than expected', 'EPS beat'
            ],
            'weight': 0.10,
            'typical_impact': '+5-15%',
            'hold_days': 5
        },
        CatalystType.PARTNERSHIP: {
            'keywords': [
                'partner', 'collaboration', 'joint venture', 'alliance', 'team up',
                'strategic', 'licensing', 'co-develop', 'ecosystem'
            ],
            'weight': 0.08,
            'typical_impact': '+3-8%',
            'hold_days': 15
        },
        CatalystType.MANAGEMENT: {
            'keywords': [
                'CEO', 'CFO', 'executive', 'appointment', 'hire', 'activist',
                'board', 'restructuring', 'cost cutting', 'turnaround'
            ],
            'weight': 0.05,
            'typical_impact': '+/-5%',
            'hold_days': 20
        }
    }
    
    # Negative sentiment keywords (reduce signal weight or flip to AVOID)
    NEGATIVE_KEYWORDS = [
        'lawsuit', 'sued', 'investigation', 'fraud', 'recall', 'warning',
        'downgrade', 'miss', 'missed', 'disappoints', 'cuts', 'reduces',
        'layoff', 'layoffs', 'terminated', 'bankruptcy', 'default', 'fails',
        'loss', 'losses', 'writeoff', 'write-off', 'impairment', 'exit',
        'closure', 'shut down', 'shutdown', 'penalty', 'fine', 'settles',
        'decline', 'plunge', 'crash', 'tumble', 'slump', 'weak', 'concern'
    ]
    
    # Dollar amount pattern for contract size detection
    DOLLAR_PATTERN = re.compile(r'\$\s*(\d+(?:\.\d+)?)\s*(billion|million|B|M)', re.IGNORECASE)
    
    def __init__(self):
        self.recent_catalysts: Dict[str, List[Catalyst]] = {}
    
    def scan_headlines(self, ticker: str, headlines: List[str], 
                        timestamps: List[datetime] = None) -> List[Catalyst]:
        """
        Scan news headlines for actionable catalysts.
        
        Args:
            ticker: Stock ticker symbol
            headlines: List of news headlines
            timestamps: Optional list of headline timestamps
            
        Returns:
            List of detected Catalyst objects
        """
        catalysts = []
        
        for i, headline in enumerate(headlines):
            headline_lower = headline.lower()
            timestamp = timestamps[i] if timestamps and i < len(timestamps) else datetime.now()
            
            # Check for negative sentiment first
            is_negative = any(neg in headline_lower for neg in self.NEGATIVE_KEYWORDS)
            base_sentiment = -0.5 if is_negative else 0.5
            
            # Scan for each catalyst type
            for cat_type, config in self.CATALYST_PATTERNS.items():
                matched_keywords = []
                
                for keyword in config['keywords']:
                    if keyword.lower() in headline_lower:
                        matched_keywords.append(keyword)
                
                if matched_keywords:
                    # Calculate confidence based on keyword matches
                    confidence = min(1.0, len(matched_keywords) * 0.3)
                    
                    # Adjust for negative context
                    sentiment = base_sentiment
                    if is_negative:
                        confidence *= 0.5  # Reduce confidence for negative news
                    
                    # Extract dollar amounts for contract size
                    dollar_match = self.DOLLAR_PATTERN.search(headline)
                    if dollar_match:
                        amount = float(dollar_match.group(1))
                        unit = dollar_match.group(2).lower()
                        if 'b' in unit:
                            confidence = min(1.0, confidence + 0.3)  # Billion-dollar deals
                        elif 'm' in unit and amount >= 100:
                            confidence = min(1.0, confidence + 0.1)  # $100M+ deals
                    
                    catalyst = Catalyst(
                        type=cat_type,
                        headline=headline,
                        confidence=confidence,
                        sentiment=sentiment,
                        weight=config['weight'],
                        keywords_matched=matched_keywords,
                        timestamp=timestamp
                    )
                    
                    catalysts.append(catalyst)
                    break  # One catalyst type per headline
        
        # Cache for later reference
        self.recent_catalysts[ticker.upper()] = catalysts
        
        return catalysts
    
    def validate_with_volume(self, catalysts: List[Catalyst], 
                              volume_ratio: float) -> List[Catalyst]:
        """
        Validate catalysts with volume confirmation.
        
        Professional hedge funds require volume confirmation before acting.
        Volume ratio = current volume / average volume
        
        Args:
            catalysts: List of detected catalysts
            volume_ratio: Current volume divided by average volume
            
        Returns:
            Filtered list of volume-confirmed catalysts
        """
        if volume_ratio < 1.5:
            # Insufficient volume - only keep highest confidence catalysts
            return [c for c in catalysts if c.confidence >= 0.8]
        elif volume_ratio >= 2.0:
            # Strong volume confirmation - boost all catalysts
            for c in catalysts:
                c.confidence = min(1.0, c.confidence + 0.2)
            return catalysts
        else:
            # Moderate volume - keep actionable catalysts
            return [c for c in catalysts if c.is_actionable]
    
    def generate_signal(self, ticker: str, catalysts: List[Catalyst],
                         volume_ratio: float = 1.0) -> Optional[CatalystSignal]:
        """
        Generate trading signal from detected catalysts.
        
        Args:
            ticker: Stock ticker
            catalysts: List of detected catalysts
            volume_ratio: Current/average volume ratio
            
        Returns:
            CatalystSignal if actionable, None otherwise
        """
        if not catalysts:
            return None
        
        # Validate with volume
        validated = self.validate_with_volume(catalysts, volume_ratio)
        if not validated:
            return None
        
        # Calculate aggregate weight
        aggregate_weight = sum(c.weight * c.confidence for c in validated)
        
        # Determine recommended action
        avg_sentiment = sum(c.sentiment for c in validated) / len(validated)
        
        if avg_sentiment > 0.3 and aggregate_weight > 0.10:
            action = "BUY"
        elif avg_sentiment < -0.3:
            action = "AVOID"
        else:
            action = "HOLD"
        
        # Determine entry timing based on catalyst type
        primary_type = validated[0].type
        if primary_type == CatalystType.MNA:
            entry_timing = "IMMEDIATE"  # M&A targets move fast
        elif primary_type == CatalystType.REGULATORY:
            entry_timing = "IMMEDIATE"  # FDA approvals spike immediately
        elif volume_ratio > 2.0:
            entry_timing = "PULLBACK"   # Wait for pullback on high volume
        else:
            entry_timing = "WAIT"       # Wait for confirmation
        
        # Get hold period from primary catalyst type
        config = self.CATALYST_PATTERNS.get(primary_type, {})
        hold_days = config.get('hold_days', 10)
        
        return CatalystSignal(
            ticker=ticker,
            catalysts=validated,
            aggregate_weight=aggregate_weight,
            volume_confirmed=volume_ratio >= 1.5,
            recommended_action=action,
            entry_timing=entry_timing,
            hold_period_days=hold_days,
            stop_loss_pct=-0.08,  # 8% stop loss for catalyst trades
            target_pct=0.12       # 12% target for catalyst trades
        )
    
    def get_catalyst_summary(self, signal: CatalystSignal) -> str:
        """Generate human-readable catalyst summary."""
        if not signal:
            return "No actionable catalysts detected"
        
        lines = [
            f"📰 CATALYST ALERT: {signal.ticker}",
            f"Action: {signal.recommended_action}",
            f"Entry: {signal.entry_timing}",
            f"Hold: {signal.hold_period_days} days",
            f"Volume Confirmed: {'✅' if signal.volume_confirmed else '❌'}",
            "",
            "Detected Catalysts:"
        ]
        
        for c in signal.catalysts[:5]:  # Top 5 catalysts
            icon = "🟢" if c.sentiment > 0 else "🔴"
            lines.append(f"  {icon} [{c.type.value.upper()}] {c.headline[:60]}...")
        
        return "\n".join(lines)


# Risk controls for catalyst trades
CATALYST_RISK_RULES = {
    'max_position_pct': 0.05,       # Max 5% of portfolio per catalyst play
    'max_catalyst_exposure': 0.15,  # Max 15% total in catalyst trades
    'stop_loss_pct': -0.08,         # 8% stop loss
    'min_liquidity': 1_000_000,     # $1M daily volume minimum
    'max_concurrent_catalyst': 3,   # Max 3 catalyst trades at once
}


# =============================================================================
# SEC EDGAR INTEGRATION
# =============================================================================

def fetch_sec_catalysts(ticker: str) -> List[Catalyst]:
    """
    Fetch catalyst events from SEC EDGAR 8-K filings.
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        List of Catalyst objects from SEC filings
    """
    catalysts = []
    
    try:
        from sec_edgar_parser import SECEdgarParser
        parser = SECEdgarParser()
        filings = parser.get_material_filings(ticker, days=14)
        
        for filing in filings:
            cat_type = {
                'MNA': CatalystType.MNA,
                'CONTRACT': CatalystType.CONTRACT,
                'EARNINGS': CatalystType.EARNINGS,
                'MANAGEMENT': CatalystType.MANAGEMENT,
            }.get(filing.catalyst_type, CatalystType.UNKNOWN)
            
            catalysts.append(Catalyst(
                type=cat_type,
                headline=f"SEC 8-K: {filing.description[:80]}",
                confidence=min(1.0, filing.catalyst_weight + 0.3),  # SEC filings high confidence
                sentiment=0.6 if filing.is_material else 0.3,
                weight=filing.catalyst_weight,
                keywords_matched=[str(item.value) for item in filing.items],
                timestamp=filing.filing_date
            ))
            
    except ImportError:
        logger.debug("sec_edgar_parser not available")
    except Exception as e:
        logger.debug(f"SEC catalyst fetch failed: {e}")
    
    return catalysts


# =============================================================================
# POLYGON.IO NEWS API INTEGRATION
# =============================================================================

def fetch_polygon_news(ticker: str, api_key: str = None) -> List[str]:
    """
    Fetch news headlines from Polygon.io API.
    
    Polygon.io provides faster news than yfinance with better categorization.
    
    Args:
        ticker: Stock ticker symbol
        api_key: Polygon.io API key (falls back to env var)
        
    Returns:
        List of news headlines
    """
    import os
    
    api_key = api_key or os.getenv('POLYGON_API_KEY')
    if not api_key:
        return []
    
    try:
        import requests
        
        url = f"https://api.polygon.io/v2/reference/news"
        params = {
            'ticker': ticker.upper(),
            'limit': 20,
            'order': 'desc',
            'apiKey': api_key
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            headlines = [
                article.get('title', '')
                for article in data.get('results', [])
                if article.get('title')
            ]
            return headlines
            
    except Exception as e:
        logger.debug(f"Polygon news fetch failed: {e}")
    
    return []


# =============================================================================
# CATALYST-AWARE SIGNAL GENERATOR
# =============================================================================

class CatalystAwareSignalGenerator:
    """
    Dedicated signal generator that incorporates catalyst detection.
    
    This is the professional hedge fund approach:
    1. Detect catalysts from multiple sources
    2. Validate with volume and price action
    3. Generate weighted signal with catalyst boost
    4. Apply catalyst-specific risk controls
    """
    
    def __init__(self, polygon_api_key: str = None):
        self.scanner = CatalystScanner()
        self.polygon_key = polygon_api_key
    
    def generate_catalyst_signal(
        self,
        ticker: str,
        headlines: List[str] = None,
        volume_ratio: float = 1.0,
        current_price: float = 0.0,
        include_sec: bool = True,
        include_polygon: bool = True
    ) -> Tuple[Optional[CatalystSignal], float]:
        """
        Generate a comprehensive catalyst-aware signal.
        
        Args:
            ticker: Stock ticker
            headlines: News headlines (optional, will fetch if not provided)
            volume_ratio: Current/average volume ratio
            current_price: Current stock price
            include_sec: Include SEC EDGAR filings
            include_polygon: Include Polygon.io news
            
        Returns:
            Tuple of (CatalystSignal, score_boost)
            score_boost is the points to add to overall score (0-15)
        """
        all_catalysts = []
        
        # Source 1: Provided headlines
        if headlines:
            all_catalysts.extend(self.scanner.scan_headlines(ticker, headlines))
        
        # Source 2: Polygon.io news
        if include_polygon and self.polygon_key:
            polygon_headlines = fetch_polygon_news(ticker, self.polygon_key)
            if polygon_headlines:
                all_catalysts.extend(self.scanner.scan_headlines(ticker, polygon_headlines))
        
        # Source 3: SEC EDGAR 8-K filings
        if include_sec and not ticker.endswith(('.NS', '.BO')):  # US stocks only
            sec_catalysts = fetch_sec_catalysts(ticker)
            all_catalysts.extend(sec_catalysts)
        
        if not all_catalysts:
            return None, 0.0
        
        # Generate signal
        signal = self.scanner.generate_signal(ticker, all_catalysts, volume_ratio)
        
        if not signal:
            return None, 0.0
        
        # Calculate score boost (max 15 points)
        base_boost = signal.aggregate_weight * 50  # 0-10 points from weight
        
        # Bonus for volume confirmation
        if signal.volume_confirmed:
            base_boost += 3.0
        
        # Bonus for SEC filings (highest reliability)
        sec_count = sum(1 for c in all_catalysts if 'SEC 8-K' in c.headline)
        if sec_count > 0:
            base_boost += min(2.0, sec_count)
        
        score_boost = min(15.0, base_boost)
        
        return signal, score_boost
    
    def get_catalyst_position_size(
        self,
        signal: CatalystSignal,
        account_size: float,
        max_risk_pct: float = 0.02
    ) -> Dict:
        """
        Calculate position size for catalyst trades with specific risk controls.
        
        Args:
            signal: CatalystSignal from generate_catalyst_signal
            account_size: Total account value
            max_risk_pct: Maximum risk per trade (default 2%)
            
        Returns:
            Dict with position sizing details
        """
        if not signal:
            return {'shares': 0, 'allocation': 0, 'reason': 'No catalyst signal'}
        
        # Apply catalyst risk rules
        max_position = account_size * CATALYST_RISK_RULES['max_position_pct']
        
        # Adjust for volume confirmation
        if not signal.volume_confirmed:
            max_position *= 0.5  # Half size without volume confirmation
        
        # Adjust for catalyst type
        type_multipliers = {
            'IMMEDIATE': 1.0,   # Full size for time-sensitive
            'PULLBACK': 0.75,   # Less for waiting trades
            'WAIT': 0.5         # Minimum for unconfirmed
        }
        max_position *= type_multipliers.get(signal.entry_timing, 0.5)
        
        # Risk-based sizing
        stop_loss_pct = abs(signal.stop_loss_pct)
        risk_amount = account_size * max_risk_pct
        risk_based_position = risk_amount / stop_loss_pct
        
        # Take the smaller of max position or risk-based
        final_position = min(max_position, risk_based_position)
        
        return {
            'allocation': round(final_position, 2),
            'max_allowed': round(max_position, 2),
            'stop_loss_pct': signal.stop_loss_pct,
            'target_pct': signal.target_pct,
            'hold_days': signal.hold_period_days,
            'entry_timing': signal.entry_timing,
            'volume_confirmed': signal.volume_confirmed,
            'catalyst_count': len(signal.catalysts)
        }


def get_catalyst_scanner() -> CatalystScanner:
    """Get or create global catalyst scanner instance."""
    return CatalystScanner()


def get_catalyst_signal_generator(polygon_key: str = None) -> CatalystAwareSignalGenerator:
    """Get catalyst-aware signal generator instance."""
    return CatalystAwareSignalGenerator(polygon_key)


if __name__ == "__main__":
    # Demo
    scanner = CatalystScanner()
    
    # Sample headlines
    headlines = [
        "NVIDIA Unveils Revolutionary AI Chip with 2x Performance Boost",
        "Apple Secures $5 Billion Enterprise Deal with Major Cloud Provider",
        "Pfizer Receives FDA Approval for Breakthrough Cancer Treatment",
        "Microsoft to Acquire Gaming Studio for $2.5 Billion",
        "Tesla Faces Recall Investigation Over Autopilot Safety Concerns"
    ]
    
    catalysts = scanner.scan_headlines("NVDA", headlines)
    
    print("\n📊 CATALYST SCAN RESULTS")
    print("=" * 60)
    for c in catalysts:
        icon = "🟢" if c.sentiment > 0 else "🔴"
        print(f"{icon} {c.type.value.upper()} (conf: {c.confidence:.0%})")
        print(f"   {c.headline[:50]}...")
        print(f"   Keywords: {', '.join(c.keywords_matched)}")
        print()
    
    # Generate signal
    signal = scanner.generate_signal("NVDA", catalysts, volume_ratio=2.5)
    if signal:
        print(scanner.get_catalyst_summary(signal))
    
    # Test CatalystAwareSignalGenerator
    print("\n" + "=" * 60)
    print("Testing CatalystAwareSignalGenerator...")
    generator = CatalystAwareSignalGenerator()
    signal, boost = generator.generate_catalyst_signal("NVDA", headlines, volume_ratio=2.0)
    if signal:
        print(f"Score Boost: +{boost:.1f} points")
        sizing = generator.get_catalyst_position_size(signal, account_size=100000)
        print(f"Position Size: ${sizing['allocation']:,.0f}")
        print(f"Entry: {sizing['entry_timing']} | Hold: {sizing['hold_days']} days")

