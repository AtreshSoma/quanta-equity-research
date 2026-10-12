import unittest
import threading
import time
import sys
import os
from collections import OrderedDict

# Resolve paths relative to this test file so the suite runs from any cwd
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_STOCK_ANALYSIS_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_STOCK_ANALYSIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.append(_PROJECT_ROOT)

import types

# Load SP_ELITE_PRO by source path (kept as exec for back-compat with the
# original extensionless module name used throughout this test).
#
# IMPORTANT: reuse an already-imported SP_ELITE_PRO instead of overwriting
# sys.modules with a fresh exec'd copy. Other suites (test_provider_resilience)
# and webapp.app share module-level singletons (PROVIDER_STATE); replacing the
# module at collection time used to split that singleton in two and break the
# cooldown tests when the full suite ran together.
source_path = os.path.join(_STOCK_ANALYSIS_DIR, "core", "SP_ELITE_PRO.py")
if "SP_ELITE_PRO" in sys.modules:
    sp_elite = sys.modules["SP_ELITE_PRO"]
else:
    with open(source_path, 'r', encoding='utf-8') as f:
        source_code = f.read()
    sp_elite = types.ModuleType("SP_ELITE_PRO")
    sys.modules["SP_ELITE_PRO"] = sp_elite
    sp_elite.__dict__["__file__"] = source_path
    exec(source_code, sp_elite.__dict__)

class TestCircuitBreaker(unittest.TestCase):
    def test_circuit_breaker_locking(self):
        """Verify thread safety of CircuitBreaker under concurrent failures.

        ``record_failure`` is internally synchronized, so callers must NOT
        grab ``cb._lock`` themselves (that would self-deadlock on the
        non-reentrant lock). We just hammer it from many threads and assert
        the breaker ends in a consistent, valid state without crashing.
        """
        cb = sp_elite.CircuitBreaker(failure_threshold=5, reset_timeout=1)

        def impact_circuit():
            for _ in range(100):
                cb.record_failure()

        threads = [threading.Thread(target=impact_circuit) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        # 10 threads * 100 failures = 1000 failures. Exact count depends on
        # the breaker's reset logic; we only require that concurrent access
        # left the state machine in a valid, non-corrupted state.
        self.assertIn(cb.state, [cb.OPEN, cb.CLOSED, cb.HALF_OPEN])

    def test_circuit_breaker_trip(self):
        cb = sp_elite.CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        self.assertEqual(cb.state, cb.CLOSED)
        cb.record_failure()
        self.assertEqual(cb.state, cb.OPEN)

@unittest.skip(
    "Obsolete: SP_ELITE_PRO.MarketDataProvider was rewritten into a ticker/"
    "universe provider and no longer exposes the LRU cache API (_cache, "
    "_set_cache, MAX_CACHE_SIZE). Rewrite against the current API if cache "
    "behavior is reintroduced."
)
class TestMarketDataCache(unittest.TestCase):
    def test_lru_eviction(self):
        """Verify LRU cache eviction policy"""
        provider = sp_elite.MarketDataProvider()
        provider.MAX_CACHE_SIZE = 3
        provider._cache = OrderedDict() # Reset
        
        provider._set_cache("A", 1)
        provider._set_cache("B", 2)
        provider._set_cache("C", 3)
        
        self.assertIn("A", provider._cache)
        self.assertIn("C", provider._cache)
        
        # Add D, should evict A (oldest)
        provider._set_cache("D", 4)
        self.assertNotIn("A", provider._cache)
        self.assertIn("D", provider._cache)
        
        # Access B (makes it recent)
        provider._set_cache("B", 2)
        
        # Add E, should evict C (now oldest, since B was refreshed)
        provider._set_cache("E", 5)
        self.assertNotIn("C", provider._cache)
        self.assertIn("B", provider._cache)

@unittest.skip(
    "Obsolete: SP_ELITE_PRO.TradingConstants was removed in the module "
    "rewrite. Re-point these assertions if the constants are reintroduced."
)
class TestTradingConstants(unittest.TestCase):
    def test_constants_values(self):
        """Verify logic fix constants"""
        self.assertEqual(sp_elite.TradingConstants.WIN_PROB_MAX, 0.78)
        self.assertEqual(sp_elite.TradingConstants.CRASH_PENALTY, 0.85)

if __name__ == '__main__':
    unittest.main()
