"""Accuracy invariants for the webapp Market Intel / Top Movers payload.

Run from the workspace root:

    python -m pytest tests/test_market_intel.py -q

These tests do NOT hit the network. They monkeypatch yfinance + the broad
universe scan to deterministic fixtures, then assert:

  1. Top gainer rendered is the highest change_pct row.
  2. Top decliner rendered is the lowest change_pct row.
  3. Any row with |change_pct| >= 15 is ALWAYS included (extreme-move halo).
  4. universe_source + as_of metadata are populated.
  5. Universe-fallback path also respects ordering + halo when screener empty.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

# Make the webapp package importable as a top-level module
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp_app  # noqa: E402


def _mk_screener_quote(sym, name, price, change_pct):
    return {
        "symbol": sym,
        "shortName": name,
        "regularMarketPrice": price,
        "regularMarketChangePercent": change_pct,
        "marketCap": 1e9,
        "regularMarketVolume": 1e6,
    }


def _mk_universe_row(sym, change_pct, *, name=None, price=100.0):
    """Synthesize what _ticker_intel would return for a universe symbol."""
    return {
        "symbol": sym,
        "name": name or sym,
        "price": price,
        "change_pct": change_pct,
        "sector": "Technology",
        "recommendation": "buy",
        "target_mean": price * 1.10,
        "analyst_count": 12,
        "upside_pct": 10.0,
        "earnings_date": None,
        "market_cap": 1e10,
    }


class MarketIntelInvariants(unittest.TestCase):
    def setUp(self):
        # Drop cache between tests
        webapp_app._market_intel_cache.clear()

    def _patch(self, *, screener_gainers, screener_losers, universe_rows):
        """Patch yfinance screener + _broad_universe + _ticker_intel."""
        screener_responses = {
            "day_gainers": {"quotes": screener_gainers},
            "day_losers": {"quotes": screener_losers},
        }

        def fake_screen(name, count=25):
            return screener_responses.get(name, {"quotes": []})

        fake_yf = mock.MagicMock()
        fake_yf.screen.side_effect = fake_screen

        # Restrict broad-universe scan to test symbols only
        sym_to_row = {r["symbol"]: r for r in universe_rows}
        broad_syms = list(sym_to_row.keys())

        def fake_ticker_intel(sym, with_info=True):
            return sym_to_row.get(sym, {"symbol": sym})

        def fake_broad_universe(market):
            return broad_syms

        return (
            mock.patch.dict(sys.modules, {"yfinance": fake_yf}),
            mock.patch.object(webapp_app, "_ticker_intel", side_effect=fake_ticker_intel),
            mock.patch.object(webapp_app, "_broad_universe", side_effect=fake_broad_universe),
        )

    # ---- 1. screener-first ordering ----------------------------------------
    def test_top_gainer_matches_highest_change_pct(self):
        gainers = [
            _mk_screener_quote("BRAI", "Braiin", 5.10, 74.20),
            _mk_screener_quote("TEN",  "Ten Inc", 12.30, 29.33),
            _mk_screener_quote("RDW",  "Redwire", 25.00, 26.01),
        ]
        losers = [
            _mk_screener_quote("OCT", "Octave", 2.00, -18.16),
            _mk_screener_quote("EMAT", "Evol Mat", 4.00, -10.89),
        ]
        universe = [_mk_universe_row("AAPL", 0.5), _mk_universe_row("MSFT", -0.2)]

        p_yf, p_tk, p_bu = self._patch(
            screener_gainers=gainers, screener_losers=losers, universe_rows=universe
        )
        with p_yf, p_tk, p_bu:
            payload = webapp_app._market_intel("US", blocking=True)

        self.assertEqual(payload["movers_up"][0]["symbol"], "BRAI")
        self.assertGreaterEqual(payload["movers_up"][0]["change_pct"], 74.0)
        self.assertEqual(payload["movers_down"][0]["symbol"], "OCT")
        self.assertLessEqual(payload["movers_down"][0]["change_pct"], -18.0)

    # ---- 2. extreme-move halo invariant -----------------------------------
    def test_extreme_move_in_universe_always_surfaces(self):
        # Screener returns only mid-tier moves
        gainers = [_mk_screener_quote("A", "A", 10.0, 4.5)]
        losers = [_mk_screener_quote("B", "B", 10.0, -3.0)]
        # But our universe has a +22% move not in the screener response
        universe = [
            _mk_universe_row("HIDDEN", 22.5),
            _mk_universe_row("AAPL", 0.1),
        ]

        p_yf, p_tk, p_bu = self._patch(
            screener_gainers=gainers, screener_losers=losers, universe_rows=universe
        )
        with p_yf, p_tk, p_bu:
            payload = webapp_app._market_intel("US", blocking=True)

        symbols_up = [r["symbol"] for r in payload["movers_up"]]
        self.assertIn("HIDDEN", symbols_up, "extreme move must surface even if screener missed it")
        self.assertGreaterEqual(payload["extreme_count"], 1)
        hidden = next(r for r in payload["movers_up"] if r["symbol"] == "HIDDEN")
        self.assertTrue(hidden.get("extreme"))

    # ---- 3. metadata -------------------------------------------------------
    def test_payload_includes_scope_metadata(self):
        p_yf, p_tk, p_bu = self._patch(
            screener_gainers=[_mk_screener_quote("A", "A", 1, 5)],
            screener_losers=[_mk_screener_quote("B", "B", 1, -5)],
            universe_rows=[_mk_universe_row("AAPL", 0.1)],
        )
        with p_yf, p_tk, p_bu:
            payload = webapp_app._market_intel("US", blocking=True)

        self.assertIn("universe_source", payload)
        self.assertTrue(payload["universe_source"])
        self.assertIn("as_of", payload)
        self.assertIsNotNone(payload["as_of"])
        self.assertTrue(payload["screener_used"])

    # ---- 4. fallback path (screener empty) --------------------------------
    def test_universe_fallback_sorts_correctly(self):
        universe = [
            _mk_universe_row("WINNER", 18.0),  # extreme
            _mk_universe_row("MID", 3.5),
            _mk_universe_row("FLAT", 0.0),
            _mk_universe_row("LOSER", -22.0),  # extreme
        ]
        p_yf, p_tk, p_bu = self._patch(
            screener_gainers=[], screener_losers=[], universe_rows=universe
        )
        with p_yf, p_tk, p_bu:
            payload = webapp_app._market_intel("US", blocking=True)

        self.assertFalse(payload["screener_used"])
        self.assertEqual(payload["movers_up"][0]["symbol"], "WINNER")
        self.assertEqual(payload["movers_down"][0]["symbol"], "LOSER")
        self.assertGreaterEqual(payload["extreme_count"], 2)


if __name__ == "__main__":
    unittest.main()
