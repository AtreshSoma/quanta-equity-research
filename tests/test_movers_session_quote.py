"""Session-quote (OHLC) enrichment for the Top Gainers / deep-dive quote board.

Run from the workspace root:

    python -m pytest tests/test_movers_session_quote.py -q

No network. Covers the backend half of the exchange-style quote line:

  1. _opt_round tolerates the None / "" / "nan" / str soup providers emit.
  2. _normalize_screener_quote carries the session OHLC block through from the
     screener response (open / day high / day low / prev close / 52w band).
  3. _session_quote_from_history derives the same block off the daily-bar frame
     the deep-dive already fetches, with an honest bar count and no extra call.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp_app  # noqa: E402


class OptRound(unittest.TestCase):
    def test_passes_real_numbers_through_rounded(self):
        self.assertEqual(webapp_app._opt_round(3.14159), 3.14)
        self.assertEqual(webapp_app._opt_round("12.3456", 3), 12.346)
        self.assertEqual(webapp_app._opt_round(10), 10.0)

    def test_rejects_the_provider_soup(self):
        for bad in (None, "", "nan", "  ", "abc", float("nan"), float("inf"), [1]):
            self.assertIsNone(webapp_app._opt_round(bad), f"{bad!r} should be None")


class ScreenerQuoteOHLC(unittest.TestCase):
    def _quote(self, **over):
        q = {
            "symbol": "COALINDIA.NS",
            "shortName": "Coal India",
            "regularMarketPrice": 416.80,
            "regularMarketChangePercent": 3.78,
            "regularMarketChange": 15.20,
            "regularMarketOpen": 408.20,
            "regularMarketDayHigh": 417.75,
            "regularMarketDayLow": 406.50,
            "regularMarketPreviousClose": 401.60,
            "fiftyTwoWeekHigh": 470.0,
            "fiftyTwoWeekLow": 340.1,
            "marketCap": 2.56e12,
            "regularMarketVolume": 12_400_000,
        }
        q.update(over)
        return q

    def test_full_session_block_carried_through(self):
        row = webapp_app._normalize_screener_quote(self._quote())
        self.assertEqual(row["open"], 408.20)
        self.assertEqual(row["day_high"], 417.75)
        self.assertEqual(row["day_low"], 406.50)
        self.assertEqual(row["prev_close"], 401.60)
        self.assertEqual(row["change_abs"], 15.20)
        self.assertEqual(row["week52_high"], 470.0)
        self.assertEqual(row["week52_low"], 340.1)

    def test_missing_ohlc_becomes_none_not_zero(self):
        q = self._quote()
        for k in ("regularMarketOpen", "regularMarketDayHigh",
                  "regularMarketDayLow", "regularMarketPreviousClose"):
            q.pop(k)
        row = webapp_app._normalize_screener_quote(q)
        self.assertIsNone(row["open"])
        self.assertIsNone(row["day_high"])
        self.assertIsNone(row["prev_close"])
        # Core fields still present — a partial feed still yields a usable row.
        self.assertEqual(row["price"], 416.80)
        self.assertEqual(row["change_pct"], 3.78)

    def test_still_rejects_a_row_with_no_price(self):
        self.assertIsNone(webapp_app._normalize_screener_quote(
            {"symbol": "X", "regularMarketChangePercent": 1.0}))


class SessionFromHistory(unittest.TestCase):
    def _frame(self, rows):
        pd = __import__("pandas")
        idx = pd.to_datetime([r[0] for r in rows])
        return pd.DataFrame(
            {
                "Open": [r[1] for r in rows],
                "High": [r[2] for r in rows],
                "Low": [r[3] for r in rows],
                "Close": [r[4] for r in rows],
                "Volume": [r[5] for r in rows],
            },
            index=idx,
        )

    def test_derives_session_block_off_the_last_two_bars(self):
        frame = self._frame([
            ("2026-09-01", 100.0, 104.0, 99.0, 103.0, 1_000_000),
            ("2026-09-02", 103.5, 108.0, 102.0, 107.0, 1_500_000),
        ])
        s = webapp_app._session_quote_from_history(frame)
        self.assertEqual(s["open"], 103.5)
        self.assertEqual(s["day_high"], 108.0)
        self.assertEqual(s["day_low"], 102.0)
        self.assertEqual(s["prev_close"], 103.0)
        self.assertEqual(s["ltp"], 107.0)
        self.assertAlmostEqual(s["change_abs"], 4.0)
        self.assertAlmostEqual(s["change_pct"], round(4.0 / 103.0 * 100, 2))
        self.assertEqual(s["date"], "2026-09-02")

    def test_range_band_is_labelled_by_actual_bar_count(self):
        rows = [
            (f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 10 + i, 12 + i, 9 + i, 11 + i, 1000)
            for i in range(40)
        ]
        s = webapp_app._session_quote_from_history(self._frame(rows))
        self.assertEqual(s["range_bars"], 40)
        self.assertEqual(s["range_low"], 9.0)          # min Low across the frame
        self.assertEqual(s["range_high"], 12.0 + 39)   # max High across the frame

    def test_empty_or_degenerate_frame_returns_none(self):
        pd = __import__("pandas")
        self.assertIsNone(webapp_app._session_quote_from_history(None))
        self.assertIsNone(webapp_app._session_quote_from_history(pd.DataFrame()))

    def test_single_bar_frame_has_no_prev_close_but_still_returns_ohlc(self):
        s = webapp_app._session_quote_from_history(self._frame([
            ("2026-09-02", 103.5, 108.0, 102.0, 107.0, 1_500_000),
        ]))
        self.assertEqual(s["ltp"], 107.0)
        self.assertIsNone(s["prev_close"])
        self.assertNotIn("change_pct", s)


if __name__ == "__main__":
    unittest.main()
