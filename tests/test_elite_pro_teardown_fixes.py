"""Regression tests for the SP_ELITE_PRO teardown fixes.

Each test is named for the finding it locks in. The comments say what the old
behaviour was, so a future change that reintroduces it fails here with an
explanation rather than a bare assertion error.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


CORE = Path(__file__).resolve().parents[1] / "core"
MODULE_PATH = CORE / "SP_ELITE_PRO.py"

if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

if "SP_ELITE_PRO" in sys.modules:
    elite = sys.modules["SP_ELITE_PRO"]
else:
    spec = importlib.util.spec_from_file_location("SP_ELITE_PRO", MODULE_PATH)
    assert spec and spec.loader
    elite = importlib.util.module_from_spec(spec)
    sys.modules["SP_ELITE_PRO"] = elite
    spec.loader.exec_module(elite)


def _code_only(text: str) -> str:
    """Blank out comments and docstrings, preserving line/column structure.

    Source assertions must not be satisfiable — or defeatable — by the very
    comments that document a fix. Spans are replaced with spaces rather than
    deleted so multi-token phrases still match on their original lines.
    """
    import io
    import tokenize

    lines = text.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError:
        return text

    blanks = []
    prev_meaningful = tokenize.NEWLINE
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            blanks.append((tok.start, tok.end))
            continue
        if tok.type == tokenize.STRING and prev_meaningful in (
            tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL,
        ):
            blanks.append((tok.start, tok.end))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev_meaningful = tok.type

    grid = [list(line) for line in lines]
    for (srow, scol), (erow, ecol) in blanks:
        for row in range(srow, erow + 1):
            if row - 1 >= len(grid):
                break
            line = grid[row - 1]
            begin = scol if row == srow else 0
            finish = ecol if row == erow else len(line)
            for col in range(begin, min(finish, len(line))):
                if line[col] != "\n":
                    line[col] = " "
    return "".join("".join(line) for line in grid)


def _source() -> str:
    return _code_only(MODULE_PATH.read_text(encoding="utf-8"))


def _func_code(name: str) -> str:
    """Return one function's body as code-only text.

    Uses the AST rather than string slicing, because slicing on a name finds
    the first CALL SITE, which in this module often precedes the definition by
    a thousand lines and swallows unrelated code.
    """
    import ast

    text = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = text.splitlines()[node.lineno - 1: node.end_lineno]
            return _code_only("\n".join(lines))
    raise AssertionError(f"function {name!r} not found in {MODULE_PATH.name}")


def _bare_system(capital: float = 100_000.0, regime: str = "RANGING"):
    """A system instance without the network-touching __init__."""
    sysm = object.__new__(elite.QuantaEliteTradingSystem)
    sysm.capital = capital
    sysm.currency_symbol = "$"
    sysm.market = "US"
    sysm.drawdown_multiplier = 1.0
    sysm.current_drawdown = 0.0
    sysm.market_regime = elite.MarketRegime(regime_type=regime)
    sysm.scan_results = []
    sysm._universe_stats = None
    return sysm


def _spy(bars: int, start: float = 100.0, end: float = 140.0) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-09-01", periods=bars)
    close = pd.Series(np.linspace(start, end, bars), index=idx)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": np.full(bars, 1e6),
    })


# ── B1: universe selection ──────────────────────────────────────────────────

class TestUniverseSelection:
    def test_broad_universe_is_never_a_lexicographic_prefix(self):
        """[B1] `universe_list[:limit]` meant a standard scan of 669 names only
        ever examined A-BSY, making NVDA/MSFT/META/JPM/XOM unreachable."""
        sysm = _bare_system()
        universe = sorted(f"{a}{b}" for a in "ABCXYZ" for b in "ABCDEFGHIJ")
        # Force the ranking path to be unavailable so we exercise the fallback,
        # which must ALSO be unbiased.
        sysm.smart_prefilter = lambda t, max_candidates=200, **_: []
        selected = sysm._select_broad_universe(universe, 20)

        assert len(selected) == 20
        assert selected != universe[:20], "still taking an alphabetical prefix"
        # The fallback spread must reach the far end of the alphabet.
        assert any(t.startswith("Z") for t in selected)

    def test_full_scan_covers_the_whole_universe(self):
        """[B1] `full` must not silently drop names; its limit exceeds the universe."""
        sysm = _bare_system()
        universe = [f"T{i:04d}" for i in range(669)]
        assert sysm._select_broad_universe(universe, 10_000) == universe

    def test_prefilter_ranks_by_liquidity_not_momentum(self):
        """[B1/M2] The prefilter used momentum HARD CUTS (above 20DMA, within
        25% of the period high), imposing a trend tilt on the universe itself
        and deleting every mean-reversion candidate."""
        body = _func_code("smart_prefilter")
        assert "sma20 * 0.92" not in body, "momentum hard cut is back"
        assert "high_period * 0.75" not in body, "relative-strength hard cut is back"
        assert "dollar_volume" in body, "liquidity ranking key missing"
        # A failure must never fall back to a prefix.
        assert "tickers[:max_candidates]" not in body


# ── B2 / O13: trailing stop ─────────────────────────────────────────────────

class TestTrailingStop:
    def _pick(self):
        rec = elite.StockAnalysis(ticker="XYZ", company="X", sector="Tech", industry="SW")
        rec.entry_price = 100.0
        rec.stop_loss = 92.0
        rec.trailing_stop = 94.5
        rec.technical_indicators.atr = 2.0
        rec.technical_indicators.high_52w = 150.0
        return rec

    def test_no_highs_supplied_leaves_the_initial_trail_untouched(self):
        """[B2] Using high_52w as "high since entry" then clamping to
        entry*0.995 published a trailing stop 0.5% below entry — a guaranteed
        same-day stop-out that also raised the stop above the analysed one."""
        sysm = _bare_system()
        rec = self._pick()
        sysm.update_trailing_stops([rec])
        assert rec.trailing_stop == 94.5
        assert rec.trailing_stop < rec.entry_price

    def test_ratchets_only_upward_from_a_real_running_high(self):
        sysm = _bare_system()
        rec = self._pick()
        sysm.update_trailing_stops([rec], {"XYZ": 120.0})
        assert rec.trailing_stop == pytest.approx(116.5)

        # A lower running high must not pull the trail back down.
        rec2 = self._pick()
        rec2.trailing_stop = 110.0
        sysm.update_trailing_stops([rec2], {"XYZ": 108.0})
        assert rec2.trailing_stop == 110.0

    def test_price_targets_no_longer_return_a_trailing_stop_above_entry(self):
        """[O13] The 4th return value was entry + (target-entry)*0.40 — a
        profit-taking trigger ABOVE entry, labelled a stop."""
        sysm = _bare_system()
        ti = elite.TechnicalIndicators(
            current_price=100, atr=2.0, support=88.0, resistance=104.0, high_52w=118.0
        )
        result = sysm._calculate_price_targets(100.0, ti, elite.RiskMetrics(volatility=0.30))
        assert len(result) == 3
        entry, target, stop = result
        assert stop < entry < target


# ── B3 / B4 / W11: sizing ───────────────────────────────────────────────────

class TestRiskBasedSizing:
    def test_dollar_risk_equals_the_target_budget(self):
        """[B4] MAX_RISK_PER_TRADE was referenced only by validate_config;
        nothing tied share count to (entry - stop)."""
        sysm = _bare_system(capital=100_000.0)
        entry, stop = 100.0, 88.0   # 12% stop, wide enough that the cap misses
        alloc = sysm._calculate_position_size(
            signal=elite.SignalType.BUY.value, score=6.0, entry=entry, stop=stop
        )
        cap = sysm.capital * elite.TradingConfig.effective_max_position_pct(sysm.capital)
        assert alloc < cap, "cap bound; pick a wider stop for this test"
        dollar_risk = alloc * ((entry - stop) / entry)
        expected = (
            sysm.capital * elite.TradingConfig.TARGET_RISK_PER_TRADE * 0.80 * (0.7 + 0.6 * 0.5)
        )
        assert dollar_risk == pytest.approx(expected, rel=1e-6)

    def test_hard_risk_ceiling_is_enforced(self):
        sysm = _bare_system(capital=100_000.0)
        for stop_pct in (0.03, 0.06, 0.12, 0.20):
            entry = 100.0
            stop = entry * (1 - stop_pct)
            alloc = sysm._calculate_position_size(
                signal=elite.SignalType.STRONG_BUY.value, score=10.0, entry=entry, stop=stop
            )
            risk_pct = (alloc * stop_pct) / sysm.capital
            assert risk_pct <= elite.TradingConfig.MAX_RISK_STRONG_BUY + 1e-9

    def test_sizes_differentiate_by_conviction_and_volatility(self):
        """[B3] Every published position used to be equal weight, so a 5-star
        18%-vol grade-A name got identical dollars to a 2-star 45%-vol one."""
        sysm = _bare_system(capital=100_000.0)
        by_signal = [
            sysm._calculate_position_size(signal=sig, score=6.0, entry=100.0, stop=93.0)
            for sig in (
                elite.SignalType.STRONG_BUY.value,
                elite.SignalType.BUY.value,
                elite.SignalType.ACCUMULATE.value,
            )
        ]
        assert by_signal[0] > by_signal[1] > by_signal[2] > 0

        by_stop = [
            sysm._calculate_position_size(
                signal=elite.SignalType.BUY.value, score=6.0, entry=100.0, stop=100.0 - d
            )
            for d in (7.0, 12.0, 18.0)
        ]
        assert by_stop[0] > by_stop[1] > by_stop[2] > 0

    def test_unsizeable_stop_returns_zero(self):
        sysm = _bare_system()
        for stop in (100.0, 120.0, 0.0, -5.0):
            assert sysm._calculate_position_size(
                signal=elite.SignalType.BUY.value, score=6.0, entry=100.0, stop=stop
            ) == 0.0

    def test_apply_allocation_keeps_the_three_fields_consistent(self):
        """[W11] The greedy pass set allocation and shares but never refreshed
        position_pct, so the card printed a dollar figure and a % that did not
        correspond whenever the optimizer was skipped."""
        rec = elite.StockAnalysis(ticker="T", company="T", sector="S", industry="I")
        rec.entry_price = 50.0
        rec.position_pct = 99.0   # stale value from an earlier sizing
        applied = elite.apply_allocation(rec, 5_000.0, 100_000.0)
        assert applied == rec.allocation
        assert rec.shares * rec.entry_price == pytest.approx(rec.allocation)
        assert rec.position_pct == pytest.approx(rec.allocation / 100_000.0 * 100, abs=0.02)

        elite.apply_allocation(rec, 0.0, 100_000.0)
        assert (rec.allocation, rec.shares, rec.position_pct) == (0.0, 0, 0.0)


# ── B5: no return forecast in the optimizer ─────────────────────────────────

def test_optimizer_does_not_forecast_returns_from_trailing_means():
    """[B5] `mu = returns_df.mean() * 252` is error maximization, and it was
    disconnected from the engine's own alpha score."""
    body = _func_code("get_top_recommendations")
    assert "returns_df.mean().values * 252" not in body
    assert "neg_utility" not in body, "mean-variance utility is back"
    # [S5-2] The inverse-vol pass that followed re-split the total by 1/vol
    # after the sector / stop-risk / VaR gates had run, undoing the per-name
    # risk sizing. Published sizes are the risk-based sizes.
    assert "budget * weight" not in body, "a post-gate re-weighting pass is back"


# ── B6 / M9: levels ─────────────────────────────────────────────────────────

class TestTradeLevels:
    def test_risk_reward_is_not_manufactured(self):
        """[B6] `if target < required_target: target = entry + risk * 1.8`
        meant the downstream R:R gate could never legitimately reject."""
        body = _func_code("_calculate_price_targets")
        assert "required_target" not in body
        assert "RISK_REWARD_MIN" not in body

    def test_bearish_analyst_target_can_sink_the_trade(self):
        """[B6] The old guard `analyst_target > price * 1.02` admitted only
        bullish targets, baking sell-side optimism into every reward figure."""
        sysm = _bare_system()
        ti = elite.TechnicalIndicators(
            current_price=100, atr=2.0, support=88.0, resistance=104.0, high_52w=118.0
        )
        rm = elite.RiskMetrics(volatility=0.30)

        bull_e, bull_t, bull_s = sysm._calculate_price_targets(100.0, ti, rm, analyst_target=140.0)
        bear_e, bear_t, bear_s = sysm._calculate_price_targets(100.0, ti, rm, analyst_target=80.0)

        assert bear_t < bull_t, "a bearish target must pull the target down"
        bull_rr = (bull_t - bull_e) / (bull_e - bull_s)
        bear_rr = (bear_t - bear_e) / (bear_e - bear_s)
        assert bull_rr > elite.TradingConfig.RISK_REWARD_MIN
        assert bear_rr < elite.TradingConfig.RISK_REWARD_MIN

    def test_stop_is_not_the_tightest_of_competing_methods(self):
        """[B6/M9] `max(atr_stop, support_stop, volatility_stop)` selected the
        TIGHTEST candidate, so a 1.8x ATR day-trade stop nearly always won."""
        sysm = _bare_system()
        ti = elite.TechnicalIndicators(
            current_price=100, atr=2.0, support=70.0, resistance=104.0, high_52w=118.0
        )
        _e, _t, stop = sysm._calculate_price_targets(100.0, ti, elite.RiskMetrics(volatility=0.30))
        old_style_tightest = 100.0 - 1.8 * 2.0
        assert stop < old_style_tightest, "stop is still a day-trade distance"

    def test_stop_distance_stays_inside_a_tradeable_band(self):
        sysm = _bare_system()
        for atr in (0.05, 2.0, 25.0):
            ti = elite.TechnicalIndicators(
                current_price=100, atr=atr, support=1.0, resistance=104.0, high_52w=118.0
            )
            _e, _t, stop = sysm._calculate_price_targets(
                100.0, ti, elite.RiskMetrics(volatility=0.30)
            )
            distance = (100.0 - stop) / 100.0
            assert elite.TradingConfig.MIN_STOP_DISTANCE_PCT - 1e-9 <= distance
            assert distance <= elite.TradingConfig.MAX_STOP_DISTANCE_PCT + 1e-9


# ── B7: scenario expected value ─────────────────────────────────────────────

class TestPriceScenarios:
    def test_expected_value_is_not_structurally_positive(self):
        """[B7] Bull ran at +1.5 sigma against a bear at only -0.75, plus
        ordering floors at 1.05x and 1.155x, so EV was positive for every stock
        and ROSE with volatility (+5.8% at 15% vol, +12.4% at 50%)."""
        sysm = _bare_system()
        ti = elite.TechnicalIndicators(current_price=100, atr=2.0)
        evs = []
        for vol in (0.15, 0.30, 0.50):
            sc = sysm._calculate_price_scenarios(
                100.0, ti, elite.RiskMetrics(volatility=vol), {}
            )
            evs.append(sc.expected_value)
            assert sc.bear_target < sc.base_target < sc.bull_target
        # No information means no edge, at any volatility.
        assert all(ev == pytest.approx(100.0, abs=0.05) for ev in evs)

    def test_bearish_deck_produces_negative_expected_value(self):
        sysm = _bare_system()
        sc = sysm._calculate_price_scenarios(
            100.0,
            elite.TechnicalIndicators(current_price=100, atr=2.0),
            elite.RiskMetrics(volatility=0.30),
            {"targetMeanPrice": 92, "targetHighPrice": 105, "targetLowPrice": 70},
        )
        assert sc.expected_value < 100.0


# ── W1 / W2: regime ─────────────────────────────────────────────────────────

class TestRegimeDetection:
    def test_bull_and_bear_are_reachable(self, monkeypatch):
        """[W1] period="6mo" gave ~126 bars while the trend test needs 200, and
        `else sma_50` aliased the missing average — so `price > sma_50 >
        sma_200` evaluated `sma_50 > sma_50` and both branches were dead."""
        class Dead:
            def __init__(self, *a, **k): pass
            def history(self, *a, **k): raise RuntimeError("no network in test")
        monkeypatch.setattr(elite.yf, "Ticker", Dead)

        up = elite.MarketRegimeDetector.detect_regime(vix=14.0, spy_hist=_spy(300, 100, 160))
        down = elite.MarketRegimeDetector.detect_regime(vix=14.0, spy_hist=_spy(300, 160, 100))
        assert up.regime_type == "BULL"
        assert down.regime_type == "BEAR"

    def test_short_history_reports_unknown_not_a_guess(self, monkeypatch):
        class Dead:
            def __init__(self, *a, **k): pass
            def history(self, *a, **k): raise RuntimeError("no network in test")
        monkeypatch.setattr(elite.yf, "Ticker", Dead)

        regime = elite.MarketRegimeDetector.detect_regime(vix=14.0, spy_hist=_spy(126))
        assert regime.regime_type == "UNKNOWN"
        assert regime.confidence < 0.5
        # "We don't know" must hold more cash than "conditions are balanced".
        neutral_cash = 0.25
        assert regime.recommended_cash_pct > neutral_cash

    def test_all_regime_weight_sets_sum_to_one(self):
        for name, weights in elite._REGIME_WEIGHTS.items():
            assert sum(weights.values()) == pytest.approx(1.0), name

    def test_unknown_regime_has_a_weight_set(self):
        assert "UNKNOWN" in elite._REGIME_WEIGHTS


# ── W3 / W4 / W5 / M5 / M6 / M7: arithmetic ─────────────────────────────────

class TestArithmetic:
    def test_adx_directional_movement_is_mutually_exclusive(self):
        """[W3] Only clipping negatives left both +DM and -DM positive on an
        outside bar, inflating both DI legs and corrupting DX/ADX."""
        rng = np.random.default_rng(7)
        n = 260
        high = pd.Series(100.0 + rng.normal(0, 2, n))
        low = pd.Series(90.0 - abs(rng.normal(0, 2, n)))
        close = (high + low) / 2
        hist = pd.DataFrame({"High": high, "Low": low, "Close": close})

        up = high.diff()
        down = -low.diff()
        assert int(((up > 0) & (down > 0)).sum()) > 0, "fixture has no outside bars"

        plus_dm = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)
        assert int(((plus_dm > 0) & (minus_dm > 0)).sum()) == 0

        adx = elite.TechnicalAnalysisEngine._calculate_adx(hist)
        assert 0.0 <= adx <= 100.0

    def test_one_definition_of_twelve_two_momentum(self):
        """[W4] TechnicalAnalysisEngine used iloc[-44] while EliteQuantEngine
        used iloc[-42] for the same named factor."""
        px = pd.Series(np.linspace(50, 100, 300))
        hist = pd.DataFrame({
            "Open": px, "High": px * 1.01, "Low": px * 0.99, "Close": px,
            "Volume": np.full(300, 1e6),
        })
        indicators = elite.TechnicalAnalysisEngine.calculate_all_indicators(hist)
        multi = elite.EliteQuantEngine.calculate_multi_horizon_momentum(hist)
        assert indicators.momentum_12_2 == multi["momentum_12_2"]
        assert indicators.momentum_12_2 == elite.compute_momentum_12_2(px)

    def test_momentum_12m_is_a_real_twelve_month_return(self):
        """[W4] momentum_12m was aliased to momentum_12_2, so a field labelled
        "12-month return" was not one."""
        px = pd.Series(np.linspace(50, 100, 300))
        hist = pd.DataFrame({"Close": px})
        multi = elite.EliteQuantEngine.calculate_multi_horizon_momentum(hist)
        assert multi["momentum_12m"] != multi["momentum_12_2"]
        assert multi["momentum_12m"] == pytest.approx(
            float(px.iloc[-1] / px.iloc[-252] - 1)
        )

    def test_qmj_reads_the_normalized_debt_to_equity(self):
        """[W5] QMJ used `raw/100 if raw > 10 else raw`, so a company reporting
        debtToEquity 5.0 (0.05x, almost debt-free) was read as 5.0x and earned
        junk_score 3.0."""
        info = {
            "debtToEquity": 5.0, "returnOnEquity": 0.22, "returnOnAssets": 0.12,
            "currentRatio": 2.0, "revenueGrowth": 0.15, "earningsGrowth": 0.20,
            "dividendRate": 0.9, "currentPrice": 180.0, "payoutRatio": 0.3,
        }
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals(info)
        assert metrics.debt_to_equity == pytest.approx(0.05)
        _q, junk, spread = elite.EliteQuantEngine.calculate_qmj_score(
            info, pd.DataFrame(), metrics=metrics
        )
        assert junk == 0.0
        assert spread > 0

    def test_unknown_debt_is_not_treated_as_leverage(self):
        """[W5] The old default of 100 was read as 100x, convicting every name
        with no debt data reported."""
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals(
            {"returnOnEquity": 0.25, "returnOnAssets": 0.15}
        )
        assert metrics.data_available.get("debt_to_equity") is False
        _q, junk, _s = elite.EliteQuantEngine.calculate_qmj_score(
            {}, pd.DataFrame(), metrics=metrics
        )
        assert junk == 0.0

    def test_roic_is_computed_from_keys_yfinance_returns(self):
        """[M5] ROIC read totalStockholderEquity / ebit / effectiveTaxRate,
        none of which appear in the yfinance info payload, so it was silently
        0.0 for essentially every ticker."""
        info = {
            "bookValue": 7.36, "sharesOutstanding": 14_594_180_000,
            "operatingMargins": 0.3262, "totalRevenue": 466_800_000_000,
            "totalDebt": 98_000_000_000, "totalCash": 55_000_000_000,
        }
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals(info)
        assert metrics.data_available["roic"] is True
        assert metrics.roic > 0

    def test_unavailable_fundamentals_are_marked_absent(self):
        """[M5] interest_coverage and debt_to_assets are not derivable from the
        info payload, and 0.0 was being reported as if measured."""
        metrics = elite.FundamentalAnalysisEngine.analyze_fundamentals({"ebitda": 1e9})
        assert metrics.data_available.get("interest_coverage") is False
        assert metrics.data_available.get("debt_to_assets") is False

    def test_time_to_target_scales_with_the_square_of_sigma(self):
        """[M6] `abs(return)/monthly_vol` is linear; for a diffusion the
        expected time scales with the SQUARE, so targets were dated far too
        soon and increasingly so for larger moves."""
        near = elite.TimeframeProjector.project_timeframe(100, 110, 0.30, 0.0, "Health Care")
        far = elite.TimeframeProjector.project_timeframe(100, 140, 0.30, 0.0, "Health Care")
        # Quadrupling the sigma-multiple should more than double the horizon.
        assert far.horizon_months > near.horizon_months * 3

    def test_jensens_alpha_nets_the_risk_free_rate(self):
        """[M7] alpha = Rs - beta*Rm omitted Rf, biasing every reading by
        (beta - 1) * Rf."""
        rng = np.random.default_rng(11)
        market = pd.Series(rng.normal(0.0004, 0.01, 500))
        stock = 1.0 * market  # zero idiosyncratic return
        beta, alpha = elite.RiskAnalyticsEngine.calculate_beta_alpha(
            stock, market, risk_free_rate=0.04
        )
        assert beta == pytest.approx(1.0, abs=0.05)
        assert alpha == pytest.approx(0.0, abs=0.005)


# ── W6: decision core is a ratchet ──────────────────────────────────────────

def test_decision_core_never_upgrades_past_scanner_confirmations():
    """[W6] `analysis.signal` was overwritten unconditionally by the score-only
    core verdict, so a 7.6 score with no MACD/volume/momentum/Sharpe
    confirmation became STRONG_BUY."""
    body = _func_code("analyze_stock")
    # The lookup moved behind _rank_action so both call sites resolve an
    # unknown action the same (conservative) way; the ranking itself is
    # unchanged.
    assert "_rank_action" in body, "conservatism ranking is not applied"
    assert elite.QuantaEliteTradingSystem._rank_action("STRONG_BUY") > \
        elite.QuantaEliteTradingSystem._rank_action("ACCUMULATE")
    assert "_core_rank <= _scanner_rank" in body, "core can still upgrade"


def test_unknown_action_ranks_as_the_most_conservative_known_action():
    """An unrecognised action used to rank 99 (above STRONG_BUY) at one call
    site, so an unknown deep verdict could never force a downgrade."""
    rank = elite.QuantaEliteTradingSystem._rank_action
    assert rank("SOMETHING_NEW") == rank("AVOID")
    assert rank(None) < rank("WATCHLIST") < rank("BUY")


def test_action_rank_orders_conviction_correctly():
    rank = elite.QuantaEliteTradingSystem._ACTION_RANK
    assert rank["STRONG_BUY"] > rank["BUY"] > rank["ACCUMULATE"]
    assert rank["ACCUMULATE"] > rank["WATCHLIST"] > rank["AVOID"] > rank["INVALID"]


# ── W7 / W8 / W9: position ledger ───────────────────────────────────────────

class TestPositionLedger:
    def test_drawdown_is_measured_from_the_equity_peak(self):
        """[W7] Cost-basis P&L reported 0% drawdown for a book that ran to
        125k and fell back to 105k, so the 5/10/15% throttle never fired."""
        sysm = _bare_system()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "equity.csv"
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["date", "equity"])
                writer.writeheader()
                for date, equity in [
                    ("2026-08-01", 100_000), ("2026-08-20", 125_000),
                    ("2026-09-01", 105_000),
                ]:
                    writer.writerow({"date": date, "equity": equity})
            assert sysm._estimate_current_drawdown(str(path)) == pytest.approx(0.16)

    def test_trading_halt_is_reachable(self):
        sysm = _bare_system()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "equity.csv"
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["date", "equity"])
                writer.writeheader()
                writer.writerow({"date": "2026-08-01", "equity": 100_000})
                writer.writerow({"date": "2026-09-01", "equity": 84_000})
            dd = sysm._estimate_current_drawdown(str(path))
            assert elite.RiskLimits.get_drawdown_multiplier(dd) == 0.0

    def test_missing_curve_does_not_infer_a_drawdown(self):
        sysm = _bare_system()
        assert sysm._estimate_current_drawdown("/nonexistent/equity.csv") == 0.0

    def test_signal_log_and_position_ledger_are_separate_files(self):
        """[W8] save_portfolio wrote scan output to the same file the drawdown
        estimate and downgrade check read as the record of real holdings."""
        cls = elite.QuantaEliteTradingSystem
        assert cls.POSITIONS_PATH != cls.SIGNALS_PATH
        assert cls.EQUITY_CURVE_PATH not in (cls.POSITIONS_PATH, cls.SIGNALS_PATH)
        body = _func_code("check_downgrades")
        assert "load_positions" in body, "downgrade check still reads the scan output"

    def test_reduce_is_detected_as_a_downgrade(self):
        """[W9] The priority map keyed 'SELL' while SignalType emits 'REDUCE',
        which fell back to HOLD's priority — so HOLD -> REDUCE raised no
        alert. Exits are where the money is lost."""
        assert elite._signal_priority("REDUCE") < elite._signal_priority("HOLD")
        assert elite._signal_priority("AVOID") < elite._signal_priority("REDUCE")
        assert elite._signal_priority("SELL") == elite._signal_priority("AVOID")

    def test_signal_priority_covers_every_enum_member(self):
        for member in elite.SignalType:
            assert elite._signal_priority(member.value) == member.priority


# ── W10: score adjustments precede the signal ───────────────────────────────

class TestScoreAdjustmentOrder:
    def test_signal_is_derived_after_all_adjustments(self):
        body = _func_code("analyze_stock")
        adj_pos = body.index("net_adjustment")
        sig_pos = body.index("_determine_signal_regime_aware")
        assert adj_pos < sig_pos, "score still moves after the signal is derived"

    def test_adjustment_budget_is_bounded(self):
        """[W10] The catalyst boost alone could move the composite by 2.0 on a
        0-10 scale — 20% of the range from headline NLP."""
        assert elite.TradingConfig.SCORE_ADJUSTMENT_CAP <= 1.0


# ── M1 / M2 / M4: score design ──────────────────────────────────────────────

class TestScoreDesign:
    def test_adx_is_scored_in_exactly_one_subscore(self):
        """[M1] ADX was counted in both technical_score and momentum_score, a
        combined 0.35 weight on one indicator."""
        tech = _func_code("calculate_technical_score")
        mom = _func_code("calculate_momentum_score")
        assert "indicators.adx >" not in tech, "ADX is scored in technical again"
        assert "adx >" in mom, "ADX left momentum entirely"

    def test_valuation_is_scored_in_exactly_one_subscore(self):
        """[M1] P/E, PEG and ROE were counted in both fundamental_score and
        value_score — a combined 0.45 weight double-counting cheapness."""
        fund = _func_code("calculate_fundamental_score")
        assert "metrics.pe_ratio" not in fund
        assert "metrics.peg_ratio" not in fund

    def test_technical_score_does_not_reward_a_condition_and_its_opposite(self):
        """[M2] Deeply oversold earned 1.7 (mean reversion) while strong ADX
        earned 1.0 (trend following) inside the same number."""
        oversold = elite.TechnicalIndicators(
            current_price=90, sma_20=100, sma_50=105, sma_200=110,
            rsi=25, macd=-1.0, macd_signal=-0.5, macd_histogram=-0.5,
            stoch_k=10, volume_ratio=1.0, bb_position=0.05, adx=30,
        )
        trending = elite.TechnicalIndicators(
            current_price=110, sma_20=105, sma_50=100, sma_200=95,
            rsi=58, macd=1.0, macd_signal=0.5, macd_histogram=0.5,
            stoch_k=60, volume_ratio=1.6, bb_position=0.65, adx=30,
        )
        assert (
            elite.ScoringEngine.calculate_technical_score(trending)
            > elite.ScoringEngine.calculate_technical_score(oversold)
        )

    def test_quality_score_measures_business_quality(self):
        """[M4] The old 10 points were Sharpe 3, drawdown 2, market cap 2,
        beta 1, dividend yield 2 — a low-vol + large-cap + payout factor
        wearing a quality label, with no ROIC or margins anywhere."""
        body = _func_code("calculate_quality_score")
        assert "market_cap" not in body, "size is back in the quality score"
        assert "dividend_yield" not in body, "payout is back in the quality score"
        assert "roic" in body
        assert "gross_margin" in body

    def test_quality_prefers_a_high_roic_compounder_over_a_mega_cap_payer(self):
        compounder = elite.FundamentalMetrics(
            market_cap=8e9, roic=0.28, gross_margin=0.62, operating_margin=0.30,
            debt_to_equity=0.15, dividend_yield=0.0,
            data_available={"roic": True, "debt_to_equity": True},
        )
        mega_payer = elite.FundamentalMetrics(
            market_cap=900e9, roic=0.05, gross_margin=0.18, operating_margin=0.04,
            debt_to_equity=1.8, dividend_yield=0.04,
            data_available={"roic": True, "debt_to_equity": True},
        )
        risk = elite.RiskMetrics(sharpe_ratio=1.1, max_drawdown=-0.20, beta=1.0)
        assert (
            elite.ScoringEngine.calculate_quality_score(compounder, risk)
            > elite.ScoringEngine.calculate_quality_score(mega_payer, risk)
        )

    def test_beta_is_not_scored_in_both_quality_and_bab(self):
        """[M2] quality_score paid for beta near 1.0 while
        calculate_betting_against_beta paid for LOW beta, in the same run."""
        assert "risk_metrics.beta" not in _func_code("calculate_quality_score")


# ── M3 / W13: relative vs absolute, and the dead factor layer ───────────────

def test_absolute_score_weight_dominates_the_relative_component():
    """[M3] A percentile has no absolute meaning, yet the blend was 70%
    z-score judged against fixed 5.0/6.0/7.5 thresholds."""
    assert elite.TradingConfig.ABSOLUTE_SCORE_WEIGHT > 0.5


def test_universe_percentile_is_tracked_separately_from_the_score():
    rec = elite.StockAnalysis(ticker="T", company="T", sector="S", industry="I")
    assert hasattr(rec, "universe_percentile")


def test_elite_factor_composite_is_actually_used():
    """[W13] elite_composite_score was computed and never read, along with
    check_factor_vif and calculate_true_factor_loadings — so QMJ, BAB, the
    style factors and 12-2 momentum influenced neither ranking nor sizing."""
    assert elite.TradingConfig.ELITE_FACTOR_WEIGHT > 0
    body = _func_code("normalize_scores")
    assert "elite_composite_score" in body, "elite factors are decorative again"


def test_collinearity_check_is_wired_in():
    assert hasattr(elite.QuantaEliteTradingSystem, "_log_factor_collinearity")
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "self._log_factor_collinearity(opportunities)" in source


# ── M8 / M10 / M11 / M12 / M13 / M14: data plumbing ─────────────────────────

class TestDataPlumbing:
    def test_earnings_proximity_scales_rather_than_excludes(self):
        """[M10] Any name with earnings in 0-14 days was dropped outright, so
        ~15% of the universe was unavailable and which 15% depended on the
        calendar date the scan happened to run."""
        cfg = elite.TradingConfig
        assert 0 < cfg.EARNINGS_IMMINENT_SIZE_MULT < cfg.EARNINGS_NEAR_SIZE_MULT
        assert cfg.EARNINGS_NEAR_SIZE_MULT < cfg.EARNINGS_ELEVATED_SIZE_MULT < 1.0
        # A missing calendar is a data gap, not an all-clear.
        assert 0 < cfg.EARNINGS_UNKNOWN_SIZE_MULT < 1.0

    def test_fomc_calendar_warns_when_exhausted(self, caplog, monkeypatch):
        """[M11] Once today passed the last hardcoded date the function
        returned 1.0 forever with no warning — a risk control that stops
        firing while still being believed."""
        sysm = _bare_system()
        monkeypatch.setattr(elite, "FOMC_DATES_2025", ["2020-01-29"])
        monkeypatch.setattr(elite, "FOMC_DATES_2026", ["2020-03-18"])
        monkeypatch.setattr(elite, "FOMC_DATES_2027", ["2020-04-29"])
        # QUANTA owns its console/file handlers and deliberately does not
        # propagate to root, where pytest normally attaches caplog.handler.
        # Capture the named logger directly without changing production routing.
        assert elite.logger.propagate is False
        elite.logger.addHandler(caplog.handler)
        try:
            with caplog.at_level("WARNING", logger="QUANTA"):
                multiplier, _note = sysm._get_event_risk_factor()
        finally:
            elite.logger.removeHandler(caplog.handler)
        assert multiplier == 1.0
        assert any("EXHAUSTED" in r.message for r in caplog.records)

    def test_correlation_inputs_share_one_window(self):
        """[M12] Scanned names contributed 1y while the fallback fetch
        contributed 6mo, so a single matrix mixed two sample windows."""
        assert elite.TradingConfig.CORRELATION_WINDOW_DAYS > 0
        body = _func_code("_get_hist_for_correlation")
        assert "CORRELATION_WINDOW_DAYS" in body
        assert 'period="6mo"' not in body

    def test_correlation_gate_is_order_independent(self):
        """[M12] A greedy pairwise veto in score order meant the
        highest-scoring name in a sector arbitrarily blocked its peers."""
        assert 0 < elite.TradingConfig.MAX_AVERAGE_CORRELATION < 1.0
        assert "MAX_AVERAGE_CORRELATION" in _func_code("_passes_correlation")

    def test_sector_cap_uses_total_capital(self):
        """[M13] Dividing by available_capital made a documented 25% sector cap
        really 21.25% of the account at 0.85 exposure, and it silently moved
        with the regime."""
        body = _func_code("get_top_recommendations")
        assert "sector_cap_base = self.capital" in body
        assert "cap_dollars = MAX_SECTOR_PCT * sector_cap_base" in body

    def test_known_delisted_tickers_are_dropped(self):
        """[M14] Dead symbols consumed scan slots and inflated the failure
        count; BLOCK -> SQ mapped one bad symbol onto another."""
        sysm = _bare_system()
        assert sysm._sanitize_ticker("SPLK") is None
        assert sysm._sanitize_ticker("PXD") is None
        assert sysm._sanitize_ticker("BLOCK") == "XYZ"
        assert sysm._sanitize_ticker("SQ") == "XYZ"
        assert sysm._sanitize_ticker("AAPL") == "AAPL"

    def test_dividend_classification_requires_a_covered_payout(self):
        """[M14] `payout_ratio is None or ...` let an unknown payout ratio pass
        as sustainable — exactly how a yield trap becomes "income"."""
        trap = elite.StockAnalysis(ticker="T", company="T", sector="S", industry="I")
        trap.fundamental_metrics = elite.FundamentalMetrics(
            dividend_yield=0.12, payout_ratio=0.0, free_cash_flow=-1e8,
        )
        assert elite.ScoringEngine.classify_setup_type(trap) != "Dividend Income"

        real = elite.StockAnalysis(ticker="R", company="R", sector="S", industry="I")
        real.fundamental_metrics = elite.FundamentalMetrics(
            dividend_yield=0.035, payout_ratio=0.55, free_cash_flow=5e9,
        )
        assert elite.ScoringEngine.classify_setup_type(real) == "Dividend Income"


# ── Output layer ────────────────────────────────────────────────────────────

class TestOutputLayer:
    def test_reported_weights_come_from_the_active_regime(self):
        """[O1] Five hardcoded literals read 30/25/20/15/10, none of which
        matched _REGIME_WEIGHTS — the first thing the report asserted about
        the model was false."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert 'logger.info("  • Technical Analysis (30%)")' not in source
        assert "_active_weights.items()" in source

    def test_price_formatting_keeps_order_level_precision(self):
        """[O5] Every price used :.0f, so an $11.40 stop rendered "$11"."""
        assert elite.fmt_price(11.40) == "$11.40"
        assert elite.fmt_price(1234.5) == "$1,234.50"
        assert elite.fmt_price(0.0) == "$0.00"
        assert elite.fmt_price(None) == "$—"
        assert elite.fmt_price(float("nan")) == "$—"

    def test_conviction_uses_the_full_scale(self):
        """[O6] `min(conviction, 4)` made a 5-star pick indistinguishable from
        a 4-star one, hiding the top of the scale."""
        assert elite.CONVICTION_MAX == 5
        assert "min(conviction,4)" not in _source().replace(" ", "")

    def test_percentile_claims_require_a_real_population(self):
        """[O7] `"top 10%" if margin > 0.25 else "top 25%"` was a fabricated
        statistic, while _percentile_rank sat unused."""
        sysm = _bare_system()
        sysm._universe_stats = None
        assert sysm._margin_percentile_label(0.34) == ""

        stats = elite.UniverseStats()
        stats.profit_margins = [0.05 * i for i in range(1, 30)]
        sysm._universe_stats = stats
        label = sysm._margin_percentile_label(1.40)
        assert "top" in label and "scanned" in label

    def test_risk_gauge_is_driven_by_risk_not_conviction(self):
        """[O10] `"LOW" if conviction_num >= 4` rendered a 5-star, 45%-vol,
        grade-C name as RISK [●●●●●] LOW."""
        sysm = _bare_system()
        risky = elite.StockAnalysis(ticker="A", company="A", sector="S", industry="I")
        risky.conviction = 5
        risky.risk_grade = "C"
        risky.risk_metrics = elite.RiskMetrics(volatility=0.48, beta=1.8)

        calm = elite.StockAnalysis(ticker="B", company="B", sector="S", industry="I")
        calm.conviction = 2
        calm.risk_grade = "A"
        calm.risk_metrics = elite.RiskMetrics(volatility=0.16, beta=0.9)

        assert sysm._risk_gauge(risky)[0] == "HIGH"
        assert sysm._risk_gauge(calm)[0] == "LOW"

    def test_warnings_reflect_live_state(self):
        """[O12] A fixed "SKIP if ... VIX >25" line could render directly under
        a header showing VIX 28."""
        calm = _bare_system()
        calm.market_regime = elite.MarketRegime(regime_type="BULL", vix_level=14.0)
        assert not any("skip threshold" in w for w in calm._live_warnings())

        stressed = _bare_system()
        stressed.market_regime = elite.MarketRegime(regime_type="VOLATILE", vix_level=28.0)
        warnings = stressed._live_warnings()
        assert any("28.0" in w and "skip threshold" in w for w in warnings)

    def test_one_holding_horizon_everywhere(self):
        """[O4] The card, the projection and the rationale each rendered their
        own horizon, and a 1-2 month projection fell through the rationale's
        substring chain to "12+ months"."""
        assert elite.describe_horizon(1.5) == "1-2 months"
        assert elite.describe_horizon(0.5) == "2-4 weeks"
        assert elite.describe_horizon(20) == "12-18 months"
        assert elite.describe_horizon(float("nan")) == "unknown"

        projection = elite.TimeframeProjector.project_timeframe(
            100, 108, 0.30, 0.0, "Health Care"
        )
        assert projection.horizon_months > 0
        assert projection.review_date
        assert projection.target_timeframe == elite.describe_horizon(
            projection.horizon_months
        )

    def test_roe_renders_as_a_percentage(self):
        """[O3] `f"ROE: {roe:.1f}%"` on a decimal printed "ROE: 0.2%" for a 22%
        ROE while asserting "strong capital deployment" in the same sentence."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert 'f"ROE: {roe:.1f}%' not in source
        assert 'f"ROE: {roe * 100:.1f}%' in source

    def test_drawdown_is_not_framed_as_a_forward_worst_case(self):
        """[O8] Realized drawdown is a LOWER bound on future drawdown."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "expect this worst-case drop" not in source
        assert "Worst 12-month decline observed" in source

    def test_rsi_narrative_agrees_with_the_decision(self):
        """[O9] "signals institutional buying" for RSI > 70 while the engine
        was rejecting RSI > 70 as overbought."""
        source = _source()
        assert "signals institutional buying" not in source
        assert "historically precedes mean-reversion bounces" not in source

    def test_setup_column_is_not_labelled_catalyst(self):
        """[O11] The CATALYST column showed persistent STATES (MACD above
        signal, above 200MA), not dated events."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert 'table.add_column("CATALYST"' not in source
        assert 'catalyst = "MACD cross"' not in source

    def test_single_trailing_rule_in_the_execution_block(self):
        """[O13] "Trail 5%" was unconnected to trailing_stop or to the 1.75 ATR
        multiple — a third inconsistent trailing concept."""
        source = _source()
        assert "Trail 5%" not in source
        assert "TRAILING_ATR_MULTIPLIER" in source

    def test_share_counts_render_cleanly(self):
        assert elite.fmt_shares(7.0) == "7"
        assert elite.fmt_shares(1234.0) == "1,234"
        assert elite.fmt_shares(7.5) == "7.5"
        assert elite.fmt_shares(None) == "0"


def test_export_metadata_counts_the_analyzed_universe():
    """[O14] total_analyzed was len(scan_results) + len(failed_tickers) —
    survivors plus failures, not the denominator — which made hit rates look
    far better than they were."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "'total_analyzed': len(self.scan_results) + len(self.failed_tickers)" not in source
    assert "'universe_counts': _counts" in source
    assert "'gate_stats':" in source


def test_csv_export_includes_every_subscore():
    """[O14] Value_Score was the one sub-score omitted, making the composite
    unreproducible from the export."""
    body = _func_code("export_results")
    for column in ("Technical_Score", "Fundamental_Score", "Momentum_Score",
                   "Quality_Score", "Value_Score", "Conviction", "Currency"):
        assert f"'{column}'" in body, f"{column} missing from the CSV export"


def test_tradingview_export_maps_the_exchange_from_the_suffix():
    """[O14] Every Indian symbol was prefixed NSE:, so .BO tickers would not
    resolve."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert 'exchange = "BSE" if symbol.endswith(\'.BO\') else "NSE"' in source
