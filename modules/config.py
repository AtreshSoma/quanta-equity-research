"""
Config.py - API Configuration Management
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta


@dataclass
class APIKeys:
    """Store all API keys securely."""
    fmp: str = field(default_factory=lambda: os.getenv("FMP_API_KEY", ""))
    polygon: str = field(default_factory=lambda: os.getenv("POLYGON_API_KEY", ""))
    finnhub: str = field(default_factory=lambda: os.getenv("FINNHUB_API_KEY", ""))


class RateLimiter:
    """Simple rate limiter for API calls."""

    def __init__(self):
        self.call_history = {}
        self.last_reset = datetime.now()
        self.minute_reset = datetime.now()

    def can_call(self, api_name: str, limit: int, window: str = 'day') -> bool:
        """Check if we can make an API call."""
        now = datetime.now()

        if window == 'day' and (now - self.last_reset).days >= 1:
            self.call_history = {k: v for k, v in self.call_history.items() if 'minute' in k}
            self.last_reset = now
        elif window == 'minute' and (now - self.minute_reset).seconds >= 60:
            self.call_history = {k: v for k, v in self.call_history.items() if 'day' in k}
            self.minute_reset = now

        key = f"{api_name}_{window}"
        current = self.call_history.get(key, 0)

        if current >= limit:
            return False

        self.call_history[key] = current + 1
        return True

    def get_stats(self) -> dict:
        """Get current usage statistics."""
        return {
            'total_calls': sum(self.call_history.values()),
            'breakdown': self.call_history.copy()
        }


# Global instances
API_KEYS = APIKeys()
RATE_LIMITER = RateLimiter()
