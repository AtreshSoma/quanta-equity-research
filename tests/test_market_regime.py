"""Tests for the v2 4-factor market-regime model in webapp.app._market_snapshot.

Run from workspace root:

    python -m pytest tests/test_market_regime.py -q

These tests do NOT hit the network. They monkeypatch ``_quote_snapshot`` to
return controlled quote dicts so the deterministic 4-factor logic can be
asserted (trend / volatility / breadth / credit-rates).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

# Make the webapp package importable as a top-level module (mirrors
# test_market_intel.py's bootstrap).
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths(webapp=True)

import app as webapp_app  # noqa: E402


# US index_symbols order (see MARKET_UNIVERSES["US"]):
#   SPY, QQQ, DIA, IWM, ^VIX, ^TNX, GLD, USO, BTC-USD
# Breadth filter excludes ^VIX / ^INDIAVIX, leaving 8 "indices".
_BREADTH_SYMS = ["SPY", "QQQ", "DIA", "IWM", "^TNX", "GLD", "USO", "BTC-USD"]


def _make_snapshot_factory(*, trend_pct, vix_price, yield_pct, green_count):
    """Return a fake _quote_snapshot(symbol, label) that yields the desired
    4-factor configuration.

    ``green_count`` controls how many of the 8 non-VIX breadth symbols come
    back with change_pct > 0. SPY (the benchmark) is forced to ``trend_pct``
    and counts toward the green tally if positive; the remaining symbols are
    filled in deterministic order.
    """
    # Decide which of the 8 breadth symbols are green.
    green = set()
    if trend_pct > 0:
        green.add("SPY")
    # Fill remaining green slots from the ordered list (skip SPY since handled).
    remaining_needed = green_count - len(green)
    for sym in _BREADTH_SYMS:
        if remaining_needed <= 0:
            break
        if sym in green:
            continue
        green.add(sym)
        remaining_needed -= 1
    # If trend_pct <= 0 and green_count > 0, SPY isn't green; the loop above
    # already filled green_count symbols from the non-SPY pool.

    def _fake(symbol, label):
        if symbol == "SPY":
            chg = trend_pct
        elif symbol == "^VIX":
            chg = 0.0
        elif symbol == "^TNX":
            chg = yield_pct
        else:
            chg = 0.4 if symbol in green else -0.4
        price = vix_price if symbol == "^VIX" else 100.0
        return {
            "symbol": symbol,
            "label": label,
            "price": price,
            "change_pct": chg,
        }

    return _fake


class V2RegimeModelTests(unittest.TestCase):
    """Direct unit tests against ``_market_snapshot`` (v2 path)."""

    def _run_snapshot(self, fake_quote, *, regime_qs="", news=None, intel=None):
        with mock.patch.object(webapp_app, "_quote_snapshot", side_effect=fake_quote), \
             mock.patch.object(webapp_app, "_trend_metrics", return_value=None):
            ctx_path = f"/{regime_qs}" if regime_qs else "/"
            with webapp_app.app.test_request_context(ctx_path):
                return webapp_app._market_snapshot("US", news=news, intel=intel)

    def test_v2_regime_risk_on_when_three_factors_positive(self):
        fake = _make_snapshot_factory(
            trend_pct=0.8, vix_price=14.0, yield_pct=-0.6, green_count=7
        )
        snap = self._run_snapshot(fake)
        self.assertEqual(snap["tone_version"], "v2")
        self.assertEqual(snap["tone"], "Risk-on")
        self.assertEqual(len(snap["tone_factors"]), 4)
        net = sum(f["score"] for f in snap["tone_factors"])
        self.assertGreaterEqual(net, 2)

    def test_v2_regime_risk_off_when_three_factors_negative(self):
        fake = _make_snapshot_factory(
            trend_pct=-0.8, vix_price=27.0, yield_pct=0.7, green_count=1
        )
        snap = self._run_snapshot(fake)
        self.assertEqual(snap["tone_version"], "v2")
        self.assertEqual(snap["tone"], "Risk-off")
        self.assertEqual(len(snap["tone_factors"]), 4)
        net = sum(f["score"] for f in snap["tone_factors"])
        self.assertLessEqual(net, -2)

    def test_v2_regime_neutral_when_mixed(self):
        fake = _make_snapshot_factory(
            trend_pct=0.6, vix_price=14.0, yield_pct=0.7, green_count=4
        )
        snap = self._run_snapshot(fake)
        self.assertEqual(snap["tone_version"], "v2")
        self.assertEqual(snap["tone"], "Neutral")
        self.assertEqual(len(snap["tone_factors"]), 4)
        net = sum(f["score"] for f in snap["tone_factors"])
        self.assertLess(abs(net), 2)

    def test_market_regime_engine_is_present_and_structured(self):
        fake = _make_snapshot_factory(
            trend_pct=0.8, vix_price=14.5, yield_pct=-0.4, green_count=6
        )
        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        self.assertTrue(engine, "market_regime_engine should be populated")
        self.assertIn("marketRegime", engine)
        self.assertIn("marketHeadline", engine)
        self.assertIn("posture", engine)
        self.assertIn("modelSignal", engine)
        self.assertIn("confidenceScore", engine)
        self.assertIn("factors", engine)
        # 10-factor model: 8 price/macro + news impact + catalyst risk.
        self.assertEqual(len(engine.get("factors") or []), 10)
        keys = {f.get("key") for f in engine.get("factors") or []}
        self.assertIn("news_impact", keys)
        self.assertIn("catalyst_risk", keys)
        self.assertIn("primaryAction", engine)
        self.assertIn("portfolioGuidance", engine)
        # Canonical engine contract additions consumed by the dashboard.
        for field in ("newsImpactSummary", "marketDrivers", "catalystRisks",
                      "sectorLeadership", "sourceHealth", "overridesTriggered", "debug"):
            self.assertIn(field, engine, f"engine should expose {field}")
        # Headline must be non-empty and reference the computed regime.
        self.assertTrue(str(engine.get("marketHeadline") or "").strip())

    def test_high_vix_cannot_map_to_offensive_posture(self):
        fake = _make_snapshot_factory(
            trend_pct=0.9, vix_price=31.0, yield_pct=-0.2, green_count=7
        )
        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        posture = str(engine.get("posture") or "")
        self.assertNotIn(posture, {"Offensive", "Selective Offensive"})
        self.assertNotEqual(engine.get("colorTheme", {}).get("toneClass"), "bull")
        factor_map = {f.get("key"): f for f in (engine.get("factors") or [])}
        vol = factor_map.get("volatility") or {}
        self.assertLessEqual(int(vol.get("score") or 0), -1)
        self.assertTrue(any("VIX" in o for o in engine.get("overridesTriggered", [])))

    def test_qqq_weak_with_vix_rising_blocks_risk_on(self):
        def fake(symbol, label):
            table = {
                "SPY": (100.0, 0.7),
                "QQQ": (100.0, -1.6),   # weak leadership
                "DIA": (100.0, 0.2),
                "IWM": (100.0, 0.1),
                "^VIX": (19.0, 6.0),    # VIX rising
                "^TNX": (100.0, -0.6),
                "GLD": (100.0, 0.3),
                "USO": (100.0, 0.1),
                "BTC-USD": (100.0, 0.2),
            }
            price, chg = table.get(symbol, (100.0, 0.0))
            return {"symbol": symbol, "label": label, "price": price, "change_pct": chg}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        self.assertNotIn(str(engine.get("posture")), {"Offensive", "Selective Offensive"})
        self.assertNotEqual(engine.get("modelSignal"), "Risk-On")
        self.assertTrue(any("QQQ" in o for o in engine.get("overridesTriggered", [])))

    def test_stale_or_missing_data_forces_cautious_fallback(self):
        def fake(symbol, label):
            # Everything unavailable → many missing inputs → degraded/stale.
            return {"symbol": symbol, "label": label, "price": None, "change_pct": None}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        self.assertEqual(engine.get("marketRegime"), "Data Incomplete")
        self.assertEqual(engine.get("modelSignal"), "Insufficient Data")
        self.assertEqual(engine.get("colorTheme", {}).get("toneClass"), "warn")
        self.assertTrue(engine.get("dataFreshness", {}).get("stale"))
        self.assertNotIn(str(engine.get("posture")), {"Offensive", "Selective Offensive", "Lean Long"})

    def test_low_confidence_is_labelled_in_regime_and_has_reasons(self):
        # A barely-positive, conflicted tape yields low confidence.
        fake = _make_snapshot_factory(
            trend_pct=0.1, vix_price=21.0, yield_pct=0.4, green_count=4
        )
        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        if engine.get("confidenceScore", 100) < 50:
            self.assertTrue(engine.get("lowConfidence"))
            self.assertEqual(engine.get("confidenceLabel"), "Low")
            # Label must communicate the uncertainty (low-confidence OR stale).
            regime = engine.get("marketRegime") or ""
            self.assertTrue(
                ("low confidence" in regime.lower()) or ("Data Incomplete" in regime) or ("Cautious" in regime),
                f"regime label should flag uncertainty, got {regime!r}",
            )
            self.assertTrue(engine.get("confidenceReasons"))
            # Guidance must not be aggressive when confidence is low.
            band = (engine.get("portfolioGuidance") or {}).get("band", "")
            self.assertNotIn("95", band)
            self.assertEqual((engine.get("portfolioGuidance") or {}).get("headlineTone"), "warn")
        # actionBrief is always present and short.
        self.assertTrue(engine.get("actionBrief"))
        self.assertLess(len(engine.get("actionBrief") or ""), 60)

    def test_low_confidence_caps_guidance_and_degreens_bullish(self):
        # Strong bullish score but degraded data → low confidence path.
        def fake(symbol, label):
            # Only SPY/VIX available → many missing inputs → low confidence + stale.
            table = {"SPY": (100.0, 1.2), "^VIX": (12.0, -1.0)}
            if symbol in table:
                price, chg = table[symbol]
                return {"symbol": symbol, "label": label, "price": price, "change_pct": chg}
            return {"symbol": symbol, "label": label, "price": None, "change_pct": None}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        # Degraded data must never present a clean green offensive read.
        self.assertNotEqual(engine.get("colorTheme", {}).get("toneClass"), "bull")
        self.assertIn(engine.get("portfolioGuidance", {}).get("headlineTone"), {"warn", "bear"})

    def test_high_impact_bearish_news_lowers_confidence_but_never_caps_posture(self):
        # News is a keyword lexicon: it scores through News Impact and lowers
        # confidence, but no rule caps the posture on headlines alone
        # (tests/test_regime_accuracy_1019.py covers a fully measured tape).
        fake_bull = _make_snapshot_factory(
            trend_pct=0.9, vix_price=13.0, yield_pct=-0.6, green_count=7
        )
        # Baseline (no news) bullish read for comparison.
        base = self._run_snapshot(fake_bull).get("market_regime_engine") or {}
        bearish_news = {
            "groups": {
                "big_news": [
                    {"title": "Stocks plunge as recession fears and a rate hike loom",
                     "impact": 8, "direction": "bearish", "category": "macro",
                     "action": "Risk-off macro tape"},
                    {"title": "Selloff deepens; layoffs and default warning hit sentiment",
                     "impact": 7, "direction": "bearish", "category": "macro",
                     "action": "Credit stress"},
                ]
            },
            "source_status": [{"name": "X", "ok": True}],
        }
        snap = self._run_snapshot(fake_bull, news=bearish_news)
        engine = snap.get("market_regime_engine") or {}
        self.assertLessEqual(
            int(engine.get("confidenceScore") or 100),
            int(base.get("confidenceScore") or 0),
        )
        self.assertGreaterEqual(engine.get("newsImpact", {}).get("bearishHighImpact", 0), 2)
        news = next(f for f in engine["factors"] if f["key"] == "news_impact")
        self.assertEqual(engine["regimeScore"], base["regimeScore"] + news["score"])
        self.assertFalse(any("headlines override" in o.lower() for o in engine.get("overridesTriggered", [])))
        # The price/news disagreement is still disclosed.
        self.assertTrue(any("bearish" in o.lower() for o in engine.get("overridesTriggered", [])))


class RegimeSafetyRailTests(unittest.TestCase):
    """Conservative-by-default rails: missing/conflicting data can never
    produce a risk-on read."""

    def _run_snapshot(self, fake_quote, *, news=None):
        with mock.patch.object(webapp_app, "_quote_snapshot", side_effect=fake_quote), \
             mock.patch.object(webapp_app, "_trend_metrics", return_value=None):
            with webapp_app.app.test_request_context("/"):
                return webapp_app._market_snapshot("US", news=news)

    def test_missing_vix_blocks_offensive_posture(self):
        # Bullish tape everywhere, but the VIX quote fails. Rule 0 must veto
        # risk-on because volatility risk cannot be verified.
        def fake(symbol, label):
            if symbol == "^VIX":
                return {"symbol": symbol, "label": label, "price": None, "change_pct": None}
            if symbol == "^TNX":
                # Bullish tape means easing yields. At +0.9% this fixture was a
                # rate shock (-2), and only reached a risk-on score through a
                # +1 Catalyst Risk awarded for "no catalysts" that the model
                # could not actually verify (no macro calendar is ingested).
                return {"symbol": symbol, "label": label, "price": 4.0, "change_pct": -0.5}
            return {"symbol": symbol, "label": label, "price": 100.0, "change_pct": 0.9}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        self.assertNotIn(str(engine.get("posture")), {"Offensive", "Selective Offensive"})
        self.assertNotEqual(engine.get("colorTheme", {}).get("toneClass"), "bull")
        self.assertIn(
            "volatility",
            engine.get("dataFreshness", {}).get("missingInputs", []),
        )
        self.assertTrue(
            any("volatility" in o.lower() or "vix" in o.lower()
                for o in engine.get("overridesTriggered", [])),
            f"expected a VIX-unavailable override, got {engine.get('overridesTriggered')}",
        )

    def test_all_news_feeds_down_is_unknown_not_neutral(self):
        fake = _make_snapshot_factory(
            trend_pct=0.8, vix_price=14.0, yield_pct=-0.6, green_count=7
        )
        dead_news = {
            "groups": {"big_news": []},
            "source_status": [
                {"name": "WSJ", "ok": False},
                {"name": "Reuters", "ok": False},
            ],
        }
        snap = self._run_snapshot(fake, news=dead_news)
        engine = snap.get("market_regime_engine") or {}
        self.assertIn("news_feeds", engine.get("dataFreshness", {}).get("missingInputs", []))
        factor_map = {f.get("key"): f for f in (engine.get("factors") or [])}
        note = str((factor_map.get("news_impact") or {}).get("note") or "")
        self.assertIn("unreachable", note.lower())

    def test_missing_small_cap_data_is_not_labeled_narrow_leadership(self):
        # QQQ green but IWM/DIA quotes FAIL: unknown data must not be read as
        # "narrow tech-only leadership".
        def fake(symbol, label):
            if symbol in {"IWM", "DIA"}:
                return {"symbol": symbol, "label": label, "price": None, "change_pct": None}
            if symbol == "^VIX":
                return {"symbol": symbol, "label": label, "price": 14.0, "change_pct": 0.0}
            return {"symbol": symbol, "label": label, "price": 100.0, "change_pct": 0.6}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        factor_map = {f.get("key"): f for f in (engine.get("factors") or [])}
        lead = factor_map.get("sector_leadership") or {}
        self.assertNotIn("narrow", str(lead.get("note") or "").lower())

    def test_stale_headline_names_the_missing_inputs(self):
        def fake(symbol, label):
            return {"symbol": symbol, "label": label, "price": None, "change_pct": None}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        self.assertEqual(engine.get("marketRegime"), "Data Incomplete")
        self.assertIn("Missing/stale:", str(engine.get("marketHeadline") or ""))

    def test_conflicted_signals_cap_portfolio_guidance(self):
        # Force a conflicted read: half the factors strongly green, half red.
        def fake(symbol, label):
            table = {
                "SPY": (100.0, 0.9),
                "QQQ": (100.0, 1.2),
                "DIA": (100.0, -0.5),
                "IWM": (100.0, -0.8),
                "^VIX": (23.0, 4.0),
                "^TNX": (100.0, 0.9),
                "GLD": (100.0, 1.5),
                "USO": (100.0, 2.5),
                "BTC-USD": (100.0, -1.0),
            }
            price, chg = table.get(symbol, (100.0, 0.0))
            return {"symbol": symbol, "label": label, "price": price, "change_pct": chg}

        snap = self._run_snapshot(fake)
        engine = snap.get("market_regime_engine") or {}
        freshness = engine.get("dataFreshness", {})
        if freshness.get("state") == "conflicted":
            band = (engine.get("portfolioGuidance") or {}).get("band", "")
            self.assertNotIn("95", band)
            self.assertEqual((engine.get("portfolioGuidance") or {}).get("headlineTone"), "warn")


class NewsDecayTests(unittest.TestCase):
    """Old headlines must age out of the regime call."""

    def test_stale_headline_decays_below_high_impact(self):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        news = {
            "groups": {
                "big_news": [
                    {"title": "Old recession scare", "impact": 7,
                     "direction": "bearish", "category": "macro", "published": old},
                    {"title": "Fresh rate-cut signal lifts stocks", "impact": 7,
                     "direction": "bullish", "category": "macro", "published": fresh},
                ]
            }
        }
        summary = webapp_app._summarize_news_for_engine(news)
        # The 12-day-old impact-7 article decays to ~0.9 and drops out; the
        # fresh impact-7 article stays.
        self.assertEqual(summary["highImpactCount"], 1)
        self.assertEqual(summary["bullishHighImpact"], 1)
        self.assertEqual(summary["bearishHighImpact"], 0)


class V1RegimeFallbackTests(unittest.TestCase):
    """End-to-end through the Flask client to confirm ?regime=v1 still works."""

    def test_v1_regime_flag_falls_back_with_empty_factors(self):
        fake = _make_snapshot_factory(
            trend_pct=0.8, vix_price=14.0, yield_pct=-0.6, green_count=7
        )
        # Bust any dashboard cache so our patched snapshot is actually used.
        try:
            webapp_app._dashboard_cache.clear()
        except Exception:
            pass

        patches = [
            mock.patch.object(webapp_app, "_quote_snapshot", side_effect=fake),
            mock.patch.object(webapp_app, "_market_intel", return_value={}),
            mock.patch.object(webapp_app, "_build_news_digest", return_value={}),
            mock.patch.object(webapp_app, "_engine_payload", return_value={}),
        ]
        for p in patches:
            p.start()
        try:
            client = webapp_app.app.test_client()
            resp = client.get("/api/dashboard?regime=v1&refresh=1")
            self.assertEqual(resp.status_code, 200)
            payload = resp.get_json()
            self.assertIsNotNone(payload, "dashboard payload should be JSON")
            market = payload.get("market") or {}
            self.assertEqual(market.get("tone_version"), "v1")
            self.assertEqual(market.get("tone_factors"), [])
        finally:
            for p in reversed(patches):
                p.stop()


if __name__ == "__main__":
    unittest.main()


class RegimeFactorAccuracyTests(unittest.TestCase):
    """Factor notes must describe what the data actually did (Sep 2026 audit)."""

    def _snap(self, overrides=None, *, news=None, intel=None):
        overrides = overrides or {}

        def fake(symbol, label):
            price, chg = overrides.get(symbol, (100.0, 0.3))
            return {"symbol": symbol, "label": label, "price": price, "change_pct": chg}

        with mock.patch.object(webapp_app, "_quote_snapshot", side_effect=fake), \
             mock.patch.object(webapp_app, "_trend_metrics", return_value=None):
            with webapp_app.app.test_request_context("/"):
                snap = webapp_app._market_snapshot("US", news=news, intel=intel)
        return {f["key"]: f for f in snap["market_regime_engine"]["factors"]}

    def test_falling_oil_is_not_called_a_spike(self):
        down = self._snap({"^VIX": (15.0, -2.0), "USO": (148.33, -3.11)})["macro_risk"]["note"]
        self.assertIn("sharp oil drop -3.1%", down)
        self.assertNotIn("spike", down)
        up = self._snap({"^VIX": (15.0, -2.0), "USO": (150.0, 3.0)})["macro_risk"]["note"]
        self.assertIn("oil spike +3.0%", up)

    def test_catalyst_risk_counts_dated_earnings_not_headlines(self):
        # Four high-impact macro headlines: News Impact scores them. Catalyst
        # Risk must not count them again as "macro/policy events".
        headlines = [{"title": f"Macro story {i}", "category": "macro", "impact": 8,
                      "direction": "bearish"} for i in range(4)]
        news = {"groups": {"big_news": headlines}, "source_status": [{"ok": True}]}
        factors = self._snap({"^VIX": (15.0, -2.0)}, news=news,
                             intel={"earnings_week": [{"symbol": "MU"}, {"symbol": "COST"}]})
        cat = factors["catalyst_risk"]
        self.assertEqual(cat["score"], 0)
        self.assertEqual(cat["note"], "2 earnings reports in the next 7 days")
        self.assertNotIn("macro/policy", cat["note"])
        self.assertLess(factors["news_impact"]["score"], 0, "the headlines are still scored once, by News Impact")

        heavy = self._snap({"^VIX": (15.0, -2.0)}, intel={"earnings_week": [{"symbol": s} for s in "ABCD"]})
        self.assertEqual(heavy["catalyst_risk"]["score"], -1)
        none = self._snap({"^VIX": (15.0, -2.0)},
                          intel={"earnings_week": [], "universe_size": 120})["catalyst_risk"]
        self.assertEqual(none["score"], 0, "no macro calendar is ingested, so 'no earnings' is not an all-clear")
        self.assertIn("macro calendar not tracked", none["note"])

        # A screen that covered nothing (outage / warming up) is an UNKNOWN
        # calendar, never a measured "0 earnings".
        unknown = self._snap({"^VIX": (15.0, -2.0)},
                             intel={"earnings_week": [], "universe_size": 0})["catalyst_risk"]
        self.assertEqual(unknown["score"], 0)
        self.assertIn("unavailable", unknown["note"])

    def test_macro_factor_is_not_contained_without_inputs(self):
        """Audit C5: VIX, rates, oil and gold all missing used to score +1
        'Cross-asset macro stress is contained'."""
        missing = {s: (None, None) for s in ("^VIX", "^TNX", "USO", "GLD")}
        macro = self._snap(missing)["macro_risk"]
        self.assertEqual(macro["score"], 0)
        self.assertIn("unknown", macro["note"])

    def test_degraded_inputs_withhold_guidance(self):
        def fake(symbol, label):
            return {"symbol": symbol, "label": label, "price": None, "change_pct": None}
        with mock.patch.object(webapp_app, "_quote_snapshot", side_effect=fake), \
             mock.patch.object(webapp_app, "_trend_metrics", return_value=None):
            with webapp_app.app.test_request_context("/"):
                engine = webapp_app._market_snapshot(
                    "US", news={"source_status": [{"ok": False}]})["market_regime_engine"]
        self.assertTrue(engine["guidanceWithheld"])
        self.assertFalse(engine["newsFeedsLive"])
        self.assertFalse(engine["earningsCalendarKnown"])
        self.assertNotIn(1, [f["score"] for f in engine["factors"]], "no factor may confirm on missing data")


class HeadlineClassificationTests(unittest.TestCase):
    def test_index_wraps_are_not_single_name_catalysts(self):
        ctx = webapp_app._article_catalyst_context
        wraps = [
            "Dow, S&P 500 and Nasdaq open higher as oil prices ease, Treasury yields slip",
            "Dow up over 400 points in final hour as stocks rebound and oil prices pull back",
            "Stock futures slip ahead of jobs report",
        ]
        for title in wraps:
            self.assertEqual(ctx("stocks", 9, title)["catalyst_label"], "Market Wrap", title)
        for title in ["Apple drags the Dow lower after iPhone warning",
                      "Zscaler shares plunge 10% after guidance cut", "Nvidia leads chip rally"]:
            self.assertEqual(ctx("stocks", 9, title)["catalyst_label"], "Stock / Sector Catalyst", title)


class RatesNoteUnitsTests(unittest.TestCase):
    """A yield's day move is quoted in basis points, not as a percent of the yield."""

    def test_rates_factor_note_is_in_basis_points(self):
        base = _make_snapshot_factory(trend_pct=0.6, vix_price=14.8, yield_pct=0.25, green_count=4)

        def fake(symbol, label):
            q = base(symbol, label)
            if symbol == "^TNX":
                q["price"] = 5.24
            return q

        with mock.patch.object(webapp_app, "_quote_snapshot", side_effect=fake), \
             mock.patch.object(webapp_app, "_trend_metrics", return_value=None):
            with webapp_app.app.test_request_context("/"):
                snap = webapp_app._market_snapshot("US")
        factors = (snap.get("market_regime_engine") or {}).get("factors") or []
        rates = next(f for f in factors if f["key"] == "rates_credit")
        self.assertIn("+1.3 bp", rates["note"])
        self.assertNotIn("%", rates["note"])
