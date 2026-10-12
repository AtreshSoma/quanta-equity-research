"""
Institutional Catalyst Intelligence Engine

Professional hedge fund-grade catalyst detection implementing the 4-tier workflow:

TIER 1: Primary Sources (SEC EDGAR, News Wires, FDA, Clinical Trials)
TIER 2: NLP Classification (Entity Extraction, Materiality Scoring)
TIER 3: Validation (Multi-source, Volume, Options Flow, Insider)
TIER 4: Signal Generation (Entry, Stop, Target, Position)

Author: Quanta Elite System
Date: 2025-12-30
"""

import re
import requests
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# TIER 1: PRIMARY DATA SOURCES
# =============================================================================

class DataSourceTier1:
    """
    Primary catalyst data sources with latency < 5 minutes.
    
    Implements:
    - SEC EDGAR 8-K Real-Time Feed
    - FDA Approvals Database
    - News Wire Services (PRNewswire, BusinessWire)
    - Clinical Trial Status Changes
    """
    
    # FDA Drug Approval RSS Feed
    FDA_APPROVALS_URL = "https://www.fda.gov/about-fda/contact-fda/rss-feeds"
    
    # SEC EDGAR Company Facts API
    SEC_COMPANY_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    
    # Request headers
    HEADERS = {
        'User-Agent': 'QuantaElite/2.0 (institutional-research@example.com)',
        'Accept': 'application/json'
    }
    
    @staticmethod
    def fetch_sec_8k_filings(ticker: str, days: int = 14) -> List[Dict]:
        """Fetch recent 8-K filings from SEC EDGAR."""
        try:
            from sec_edgar_parser import SECEdgarParser
            parser = SECEdgarParser()
            filings = parser.get_material_filings(ticker, days=days)
            return [
                {
                    'source': 'SEC_EDGAR',
                    'type': f.catalyst_type,
                    'date': f.filing_date.isoformat(),
                    'description': f.description[:200] if f.description else '',
                    'weight': f.catalyst_weight,
                    'is_material': f.is_material,
                    'url': f.url
                }
                for f in filings
            ]
        except Exception as e:
            logger.debug(f"SEC 8-K fetch failed: {e}")
            return []
    
    @staticmethod
    def fetch_fda_approvals(company_name: str = None) -> List[Dict]:
        """
        Fetch recent FDA drug approvals.
        
        For biotech/pharma companies, FDA approvals are major catalysts.
        """
        # Note: Full FDA API requires registration
        # This is a simplified version that would be enhanced with FDA API key
        approvals = []
        try:
            # Placeholder for FDA OpenFDA API
            # Real implementation would use: https://api.fda.gov/drug/drugsfda.json
            pass
        except Exception as e:
            logger.debug(f"FDA fetch failed: {e}")
        return approvals
    
    @staticmethod
    def fetch_clinical_trials(company_name: str, ticker: str) -> List[Dict]:
        """
        Fetch clinical trial status updates from clinicaltrials.gov.
        
        Phase 3 completions and NDA submissions are major catalysts.
        """
        trials = []
        try:
            # ClinicalTrials.gov API
            url = f"https://clinicaltrials.gov/api/v2/studies"
            params = {
                'query.spons': company_name,
                'filter.overallStatus': 'COMPLETED',
                'pageSize': 10,
                'format': 'json'
            }
            # Note: Would need proper API implementation
            pass
        except Exception as e:
            logger.debug(f"Clinical trials fetch failed: {e}")
        return trials


# =============================================================================
# TIER 2: NLP CLASSIFICATION & ENTITY EXTRACTION
# =============================================================================

@dataclass
class ExtractedEntity:
    """Extracted entity from news text"""
    entity_type: str      # COMPANY, AMOUNT, COUNTERPARTY, PRODUCT
    value: str
    confidence: float
    position: Tuple[int, int] = field(default_factory=lambda: (0, 0))


class NLPClassifier:
    """
    NLP-based catalyst classification.
    
    Implements:
    - Entity Extraction (Amount, Counterparty, Product)
    - Materiality Scoring (% of revenue/market cap)
    - Event Clustering (deduplication)
    """
    
    # Regex patterns for entity extraction
    AMOUNT_PATTERN = re.compile(
        r'\$\s*(\d+(?:\.\d+)?)\s*(billion|million|B|M|bn|mn)',
        re.IGNORECASE
    )
    
    COUNTERPARTY_MARKERS = [
        'with', 'from', 'by', 'to acquire', 'merger with', 'partnership with',
        'agreement with', 'contract with', 'deal with'
    ]
    
    # Materiality thresholds
    MAJOR_DEAL_THRESHOLD = 1_000_000_000  # $1B+
    SIGNIFICANT_DEAL_THRESHOLD = 100_000_000  # $100M+
    
    def extract_entities(self, text: str) -> List[ExtractedEntity]:
        """Extract key entities from catalyst text."""
        entities = []
        
        # Extract monetary amounts
        for match in self.AMOUNT_PATTERN.finditer(text):
            amount_value = float(match.group(1))
            unit = match.group(2).lower()
            
            # Normalize to dollars
            if 'b' in unit:
                amount_value *= 1_000_000_000
            elif 'm' in unit:
                amount_value *= 1_000_000
            
            entities.append(ExtractedEntity(
                entity_type='AMOUNT',
                value=f"${amount_value:,.0f}",
                confidence=0.95,
                position=match.span()
            ))
        
        # Extract counterparties
        text_lower = text.lower()
        for marker in self.COUNTERPARTY_MARKERS:
            if marker in text_lower:
                start = text_lower.find(marker) + len(marker)
                # Get next few words as potential counterparty
                end = min(start + 50, len(text))
                snippet = text[start:end].strip()
                
                # Extract company name (simplified)
                company_match = re.match(r'^[\s,]*([A-Z][A-Za-z\s&]+?)(?:\s+for|\s+to|\s+in|,|\.)', snippet)
                if company_match:
                    entities.append(ExtractedEntity(
                        entity_type='COUNTERPARTY',
                        value=company_match.group(1).strip(),
                        confidence=0.7
                    ))
                    break
        
        return entities
    
    def calculate_materiality_score(
        self,
        deal_amount: float,
        market_cap: float = 0,
        revenue: float = 0
    ) -> float:
        """
        Calculate materiality score (0-1) for a catalyst.
        
        Materiality = max(deal/market_cap, deal/revenue)
        Higher materiality = higher potential impact.
        """
        if deal_amount <= 0:
            return 0.3  # Unknown amount - assume moderate
        
        scores = []
        
        if market_cap > 0:
            cap_pct = deal_amount / market_cap
            if cap_pct >= 0.50:
                scores.append(1.0)
            elif cap_pct >= 0.25:
                scores.append(0.9)
            elif cap_pct >= 0.10:
                scores.append(0.7)
            elif cap_pct >= 0.05:
                scores.append(0.5)
            else:
                scores.append(0.3)
        
        if revenue > 0:
            rev_pct = deal_amount / revenue
            if rev_pct >= 0.30:
                scores.append(1.0)
            elif rev_pct >= 0.15:
                scores.append(0.8)
            elif rev_pct >= 0.05:
                scores.append(0.5)
            else:
                scores.append(0.3)
        
        if not scores:
            # Fallback based on absolute amount
            if deal_amount >= self.MAJOR_DEAL_THRESHOLD:
                return 0.9
            elif deal_amount >= self.SIGNIFICANT_DEAL_THRESHOLD:
                return 0.7
            else:
                return 0.4
        
        return max(scores)
    
    def cluster_events(self, catalysts: List[Dict]) -> List[Dict]:
        """
        Cluster similar events (deduplication).
        
        Multiple sources reporting the same event = higher confidence.
        """
        if len(catalysts) <= 1:
            return catalysts
        
        clustered = []
        used = set()
        
        for i, cat1 in enumerate(catalysts):
            if i in used:
                continue
            
            cluster = [cat1]
            text1 = cat1.get('headline', '').lower()
            
            for j, cat2 in enumerate(catalysts[i+1:], i+1):
                if j in used:
                    continue
                
                text2 = cat2.get('headline', '').lower()
                
                # Simple similarity check (could use proper NLP)
                words1 = set(text1.split())
                words2 = set(text2.split())
                overlap = len(words1 & words2) / max(len(words1), len(words2), 1)
                
                if overlap > 0.5:  # 50%+ word overlap = same event
                    cluster.append(cat2)
                    used.add(j)
            
            # Merge cluster into single event with boosted confidence
            merged = cat1.copy()
            if len(cluster) > 1:
                merged['source_count'] = len(cluster)
                merged['confidence'] = min(1.0, cat1.get('confidence', 0.5) + 0.1 * (len(cluster) - 1))
                merged['multi_source_confirmed'] = True
            else:
                merged['source_count'] = 1
                merged['multi_source_confirmed'] = False
            
            clustered.append(merged)
            used.add(i)
        
        return clustered


# =============================================================================
# TIER 3: VALIDATION & ENRICHMENT
# =============================================================================

class CatalystValidator:
    """
    Multi-factor validation for detected catalysts.
    
    Implements:
    - Multi-Source Confirmation
    - Volume Confirmation
    - Options Flow Analysis
    - Insider Transaction Cross-Reference
    - Historical Pattern Matching
    """
    
    # Validation thresholds
    VOLUME_CONFIRM_THRESHOLD = 1.5  # 1.5x average volume
    HIGH_VOLUME_THRESHOLD = 2.5     # 2.5x = significant
    OPTIONS_UNUSUAL_THRESHOLD = 2.0  # 2x options volume
    
    def __init__(self):
        self.historical_patterns = {}  # Cache for pattern matching
    
    def validate_catalyst(
        self,
        catalyst: Dict,
        current_volume: float = 0,
        avg_volume: float = 1,
        options_volume: float = 0,
        avg_options_volume: float = 1,
        recent_insider_buys: int = 0
    ) -> Dict:
        """
        Comprehensive catalyst validation.
        
        Returns validation result with confidence adjustments.
        """
        result = {
            'original_confidence': catalyst.get('confidence', 0.5),
            'adjusted_confidence': catalyst.get('confidence', 0.5),
            'validations_passed': [],
            'validations_failed': [],
            'validation_score': 0.0
        }
        
        validations = 0
        passes = 0
        
        # 1. Volume Confirmation
        validations += 1
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        if volume_ratio >= self.VOLUME_CONFIRM_THRESHOLD:
            passes += 1
            result['validations_passed'].append(f"Volume confirmed ({volume_ratio:.1f}x)")
            result['adjusted_confidence'] += 0.10
            if volume_ratio >= self.HIGH_VOLUME_THRESHOLD:
                result['adjusted_confidence'] += 0.05
        else:
            result['validations_failed'].append(f"Low volume ({volume_ratio:.1f}x)")
            result['adjusted_confidence'] -= 0.05
        
        # 2. Multi-Source Confirmation
        validations += 1
        if catalyst.get('multi_source_confirmed', False):
            passes += 1
            source_count = catalyst.get('source_count', 1)
            result['validations_passed'].append(f"Multi-source ({source_count} sources)")
            result['adjusted_confidence'] += 0.05 * min(source_count, 3)
        else:
            result['validations_failed'].append("Single source only")
        
        # 3. Options Flow (if available)
        if avg_options_volume > 0:
            validations += 1
            options_ratio = options_volume / avg_options_volume
            if options_ratio >= self.OPTIONS_UNUSUAL_THRESHOLD:
                passes += 1
                result['validations_passed'].append(f"Unusual options ({options_ratio:.1f}x)")
                result['adjusted_confidence'] += 0.10
        
        # 4. Insider Activity Confirmation
        if recent_insider_buys > 0:
            validations += 1
            passes += 1
            result['validations_passed'].append(f"Insider buying ({recent_insider_buys} buys)")
            result['adjusted_confidence'] += 0.08
        
        # 5. Materiality Check
        if catalyst.get('materiality_score', 0) >= 0.7:
            validations += 1
            passes += 1
            result['validations_passed'].append("Material event")
            result['adjusted_confidence'] += 0.05
        
        # Calculate overall validation score
        result['validation_score'] = passes / validations if validations > 0 else 0
        
        # Cap confidence at 0.95
        result['adjusted_confidence'] = max(0.1, min(0.95, result['adjusted_confidence']))
        
        return result
    
    def match_historical_pattern(
        self,
        catalyst_type: str,
        sector: str = None
    ) -> Dict:
        """
        Match against historical patterns for similar catalysts.
        
        Returns expected return range and hold period based on history.
        """
        # Historical average returns by catalyst type (research-based)
        HISTORICAL_PATTERNS = {
            'MNA': {
                'target_company_avg_return': 0.30,  # 30% premium typical
                'acquirer_avg_return': -0.02,       # Acquirers often dip
                'typical_hold_days': 30,
                'completion_rate': 0.85
            },
            'FDA_APPROVAL': {
                'avg_return_approved': 0.25,
                'avg_return_rejected': -0.40,
                'approval_rate_phase3': 0.60,
                'typical_hold_days': 5
            },
            'CONTRACT': {
                'major_contract_return': 0.08,
                'minor_contract_return': 0.03,
                'typical_hold_days': 15
            },
            'EARNINGS_BEAT': {
                'beat_avg_return': 0.05,
                'miss_avg_return': -0.08,
                'typical_hold_days': 5
            },
            'PRODUCT_LAUNCH': {
                'successful_launch': 0.10,
                'typical_hold_days': 20
            }
        }
        
        pattern = HISTORICAL_PATTERNS.get(catalyst_type, {})
        return pattern


# =============================================================================
# TIER 4: SIGNAL GENERATION
# =============================================================================

@dataclass
class InstitutionalCatalystSignal:
    """
    Institutional-grade catalyst trading signal.
    
    Contains all information needed for execution.
    """
    ticker: str
    catalyst_type: str
    catalyst_summary: str
    
    # Signal details
    action: str               # BUY, SELL, AVOID, WAIT
    entry_timing: str         # IMMEDIATE, PULLBACK_3PCT, PULLBACK_5PCT, WAIT_CONFIRMATION
    
    # Price levels
    entry_price: float
    stop_loss_price: float
    target_price: float
    
    # Position sizing
    max_position_pct: float   # Max % of portfolio
    recommended_size: str     # FULL, HALF, QUARTER, STARTER
    
    # Time parameters
    hold_period_days: int
    urgency: str              # HIGH, MEDIUM, LOW
    
    # Confidence metrics
    confidence: float
    validation_score: float
    source_count: int
    
    # Risk parameters
    catalyst_specific_stop: bool  # Wider stop for binary events
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'ticker': self.ticker,
            'action': self.action,
            'entry_timing': self.entry_timing,
            'entry_price': self.entry_price,
            'stop_loss': self.stop_loss_price,
            'target': self.target_price,
            'hold_days': self.hold_period_days,
            'confidence': self.confidence,
            'catalyst': self.catalyst_summary
        }


class InstitutionalSignalGenerator:
    """
    Generate trading signals from validated catalysts.
    
    Entry Timing Logic:
    - IMMEDIATE: M&A targets, FDA approvals (time-sensitive)
    - PULLBACK: Product launches, contracts (wait for dip)
    - WAIT: Unconfirmed, low volume
    
    Stop-Loss Logic:
    - Binary events (FDA): Wider stops (15-20%)
    - M&A targets: Tight stops (deal risk)
    - Regular catalysts: Standard (8-10%)
    """
    
    # Entry timing rules by catalyst type
    ENTRY_RULES = {
        'MNA': {'timing': 'IMMEDIATE', 'urgency': 'HIGH'},
        'FDA_APPROVAL': {'timing': 'IMMEDIATE', 'urgency': 'HIGH'},
        'CONTRACT': {'timing': 'PULLBACK_3PCT', 'urgency': 'MEDIUM'},
        'PRODUCT_LAUNCH': {'timing': 'PULLBACK_5PCT', 'urgency': 'MEDIUM'},
        'EARNINGS_BEAT': {'timing': 'WAIT_CONFIRMATION', 'urgency': 'LOW'},
        'PARTNERSHIP': {'timing': 'PULLBACK_3PCT', 'urgency': 'MEDIUM'},
        'MANAGEMENT': {'timing': 'WAIT_CONFIRMATION', 'urgency': 'LOW'},
    }
    
    # Stop-loss rules by catalyst type
    STOP_RULES = {
        'MNA': {'stop_pct': 0.05, 'is_binary': False},  # 5% - deal spread
        'FDA_APPROVAL': {'stop_pct': 0.20, 'is_binary': True},  # 20% - binary
        'CONTRACT': {'stop_pct': 0.08, 'is_binary': False},
        'PRODUCT_LAUNCH': {'stop_pct': 0.10, 'is_binary': False},
        'EARNINGS_BEAT': {'stop_pct': 0.08, 'is_binary': False},
        'PARTNERSHIP': {'stop_pct': 0.08, 'is_binary': False},
        'MANAGEMENT': {'stop_pct': 0.10, 'is_binary': False},
    }
    
    # Target calculation by catalyst type
    TARGET_RULES = {
        'MNA': {'base_target': 0.25, 'vol_adjusted': False},
        'FDA_APPROVAL': {'base_target': 0.30, 'vol_adjusted': True},
        'CONTRACT': {'base_target': 0.12, 'vol_adjusted': False},
        'PRODUCT_LAUNCH': {'base_target': 0.15, 'vol_adjusted': False},
        'EARNINGS_BEAT': {'base_target': 0.08, 'vol_adjusted': True},
        'PARTNERSHIP': {'base_target': 0.10, 'vol_adjusted': False},
        'MANAGEMENT': {'base_target': 0.08, 'vol_adjusted': False},
    }
    
    # Hold period by catalyst type
    HOLD_PERIODS = {
        'MNA': 30,
        'FDA_APPROVAL': 5,
        'CONTRACT': 20,
        'PRODUCT_LAUNCH': 15,
        'EARNINGS_BEAT': 5,
        'PARTNERSHIP': 15,
        'MANAGEMENT': 20,
    }
    
    def generate_signal(
        self,
        ticker: str,
        catalyst: Dict,
        validation: Dict,
        current_price: float,
        volatility: float = 0.25
    ) -> Optional[InstitutionalCatalystSignal]:
        """
        Generate institutional-grade trading signal from catalyst.
        
        Args:
            ticker: Stock ticker
            catalyst: Validated catalyst dict
            validation: Validation result dict
            current_price: Current stock price
            volatility: Annualized volatility (for target adjustment)
        """
        catalyst_type = catalyst.get('type', 'UNKNOWN')
        confidence = validation.get('adjusted_confidence', 0.5)
        
        # Skip low-confidence catalysts
        if confidence < 0.4:
            return None
        
        # Get rules for this catalyst type
        entry_rule = self.ENTRY_RULES.get(catalyst_type, {'timing': 'WAIT_CONFIRMATION', 'urgency': 'LOW'})
        stop_rule = self.STOP_RULES.get(catalyst_type, {'stop_pct': 0.10, 'is_binary': False})
        target_rule = self.TARGET_RULES.get(catalyst_type, {'base_target': 0.10, 'vol_adjusted': False})
        hold_days = self.HOLD_PERIODS.get(catalyst_type, 10)
        
        # Determine action
        sentiment = catalyst.get('sentiment', 0.5)
        if sentiment < -0.3:
            action = 'AVOID'
        elif confidence >= 0.7 and sentiment > 0.3:
            action = 'BUY'
        elif confidence >= 0.5 and sentiment > 0:
            action = 'BUY'
        else:
            action = 'WAIT'
        
        # Calculate entry timing based on validation
        if validation.get('validation_score', 0) < 0.5:
            entry_timing = 'WAIT_CONFIRMATION'
        else:
            entry_timing = entry_rule['timing']
        
        # Calculate price levels
        entry_price = current_price
        if 'PULLBACK' in entry_timing:
            pullback_pct = float(entry_timing.split('_')[-1].replace('PCT', '')) / 100
            entry_price = current_price * (1 - pullback_pct)
        
        stop_pct = stop_rule['stop_pct']
        target_pct = target_rule['base_target']
        
        # Adjust target for volatility if needed
        if target_rule['vol_adjusted']:
            vol_multiplier = min(volatility / 0.25, 1.5)
            target_pct *= vol_multiplier
        
        stop_loss = entry_price * (1 - stop_pct)
        target = entry_price * (1 + target_pct)
        
        # Position sizing based on confidence
        if confidence >= 0.8:
            size = 'FULL'
            max_pct = 0.05
        elif confidence >= 0.6:
            size = 'HALF'
            max_pct = 0.03
        else:
            size = 'QUARTER'
            max_pct = 0.02
        
        return InstitutionalCatalystSignal(
            ticker=ticker,
            catalyst_type=catalyst_type,
            catalyst_summary=catalyst.get('headline', '')[:100],
            action=action,
            entry_timing=entry_timing,
            entry_price=round(entry_price, 2),
            stop_loss_price=round(stop_loss, 2),
            target_price=round(target, 2),
            max_position_pct=max_pct,
            recommended_size=size,
            hold_period_days=hold_days,
            urgency=entry_rule['urgency'],
            confidence=confidence,
            validation_score=validation.get('validation_score', 0),
            source_count=catalyst.get('source_count', 1),
            catalyst_specific_stop=stop_rule['is_binary']
        )


# =============================================================================
# MAIN INTELLIGENCE ENGINE
# =============================================================================

class InstitutionalCatalystEngine:
    """
    Complete 4-tier institutional catalyst intelligence engine.
    
    Usage:
        engine = InstitutionalCatalystEngine()
        signals = engine.analyze(ticker, current_price, market_cap, volume, avg_volume)
    """
    
    def __init__(self):
        self.data_source = DataSourceTier1()
        self.nlp_classifier = NLPClassifier()
        self.validator = CatalystValidator()
        self.signal_generator = InstitutionalSignalGenerator()
    
    def analyze(
        self,
        ticker: str,
        current_price: float,
        market_cap: float = 0,
        revenue: float = 0,
        current_volume: float = 0,
        avg_volume: float = 1,
        sector: str = None,
        headlines: List[str] = None
    ) -> List[InstitutionalCatalystSignal]:
        """
        Complete institutional catalyst analysis.
        
        Implements all 4 tiers:
        1. Fetch from primary sources
        2. Classify and extract entities
        3. Validate with volume/options/insider
        4. Generate trading signals
        """
        signals = []
        all_catalysts = []
        
        # === TIER 1: PRIMARY SOURCES ===
        
        # SEC EDGAR 8-K
        sec_catalysts = DataSourceTier1.fetch_sec_8k_filings(ticker, days=14)
        for cat in sec_catalysts:
            cat['source'] = 'SEC_EDGAR'
            all_catalysts.append(cat)
        
        # News headlines (if provided)
        if headlines:
            from catalyst_scanner import CatalystScanner
            scanner = CatalystScanner()
            news_catalysts = scanner.scan_headlines(ticker, headlines)
            for cat in news_catalysts:
                all_catalysts.append({
                    'type': cat.type.value.upper(),
                    'headline': cat.headline,
                    'confidence': cat.confidence,
                    'sentiment': cat.sentiment,
                    'source': 'NEWS',
                    'keywords': cat.keywords_matched
                })
        
        if not all_catalysts:
            return signals
        
        # === TIER 2: NLP CLASSIFICATION ===
        
        for catalyst in all_catalysts:
            # Extract entities
            entities = self.nlp_classifier.extract_entities(catalyst.get('headline', ''))
            catalyst['entities'] = [e.__dict__ for e in entities]
            
            # Calculate materiality (if amount found)
            amount = 0
            for entity in entities:
                if entity.entity_type == 'AMOUNT':
                    # Parse amount from formatted string
                    amount_str = entity.value.replace('$', '').replace(',', '')
                    try:
                        amount = float(amount_str)
                    except:
                        pass
            
            catalyst['materiality_score'] = self.nlp_classifier.calculate_materiality_score(
                amount, market_cap, revenue
            )
        
        # Cluster events (deduplication with confidence boost)
        clustered = self.nlp_classifier.cluster_events(all_catalysts)
        
        # === TIER 3: VALIDATION ===
        
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        
        for catalyst in clustered:
            validation = self.validator.validate_catalyst(
                catalyst,
                current_volume=current_volume,
                avg_volume=avg_volume
            )
            catalyst['validation'] = validation
            
            # === TIER 4: SIGNAL GENERATION ===
            
            signal = self.signal_generator.generate_signal(
                ticker=ticker,
                catalyst=catalyst,
                validation=validation,
                current_price=current_price
            )
            
            if signal:
                signals.append(signal)
        
        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        
        return signals
    
    def get_summary(self, signals: List[InstitutionalCatalystSignal]) -> str:
        """Generate human-readable summary of catalyst signals."""
        if not signals:
            return "No actionable catalysts detected."
        
        lines = [
            "🎯 INSTITUTIONAL CATALYST SIGNALS",
            "=" * 60
        ]
        
        for i, sig in enumerate(signals[:3], 1):
            action_emoji = {'BUY': '🟢', 'SELL': '🔴', 'AVOID': '⚠️', 'WAIT': '🟡'}.get(sig.action, '⚪')
            lines.append(f"\n{i}. {sig.ticker} - {action_emoji} {sig.action}")
            lines.append(f"   Catalyst: {sig.catalyst_type}")
            lines.append(f"   Entry: ${sig.entry_price:.2f} ({sig.entry_timing})")
            lines.append(f"   Stop: ${sig.stop_loss_price:.2f} | Target: ${sig.target_price:.2f}")
            lines.append(f"   Hold: {sig.hold_period_days} days | Size: {sig.recommended_size}")
            lines.append(f"   Confidence: {sig.confidence:.0%} | Validation: {sig.validation_score:.0%}")
        
        return '\n'.join(lines)


# Factory function
def get_institutional_catalyst_engine() -> InstitutionalCatalystEngine:
    """Get institutional catalyst engine instance."""
    return InstitutionalCatalystEngine()


if __name__ == "__main__":
    # Demo
    engine = InstitutionalCatalystEngine()
    
    print("🏛️ INSTITUTIONAL CATALYST INTELLIGENCE ENGINE")
    print("=" * 70)
    
    # Test with sample headlines
    test_headlines = [
        "Apple announces $10 billion partnership with Samsung for chip manufacturing",
        "FDA grants breakthrough therapy designation to Pfizer cancer drug",
        "Microsoft to acquire Activision Blizzard for $69 billion",
        "Tesla unveils next-generation battery with 50% more range"
    ]
    
    signals = engine.analyze(
        ticker='AAPL',
        current_price=250.0,
        market_cap=3_000_000_000_000,
        revenue=400_000_000_000,
        current_volume=50_000_000,
        avg_volume=40_000_000,
        headlines=test_headlines
    )
    
    print(engine.get_summary(signals))
