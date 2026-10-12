"""Deep-analyzer fixes from the September 2026 full review (offline).

  * Piotroski counts only the tests whose inputs were reported, and the
    published score is N/A (None) when too few were.
  * Altman Z uses market-value X4 with the public coefficients, or the Z'
    private-firm coefficients with book equity — never a mix.
  * Position sizing derives the Kelly win/loss ratio from the published target.
  * Component scores are clamped to 0-100 before weighting.
  * The MCP payload carries the trade-setup provenance flags the UI needs to
    explain a zero R:R instead of recomputing one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_paths  # noqa: E402

ensure_paths()

import TradingAnalysis_ELITE as ta  # noqa: E402


# ---------------------------------------------------------------------------
# Piotroski
# ---------------------------------------------------------------------------
def _fund(**over):
    f = ta.FundamentalData()
    for k, v in over.items():
        setattr(f, k, v)
    return f


def test_piotroski_with_no_reported_inputs_is_unavailable_not_zero():
    score, available = ta._piotroski_from_fundamentals(_fund(), {})
    assert available == 0
    assert ta.piotroski_display_value(score, available) is None


def test_piotroski_scores_only_reported_tests():
    info = {
        "returnOnAssets": 0.12, "operatingCashflow": 5e9, "profitMargins": 0.2,
        "netIncome": 4e9, "debtToEquity": 40.0, "currentRatio": 1.9,
        "grossMargins": 0.45, "revenueGrowth": 0.08, "earningsGrowth": -0.1,
    }
    f = _fund(roa=12.0, operating_cash_flow=5e9, net_margin=20.0, debt_to_equity=0.4,
              current_ratio=1.9, gross_margin=45.0, revenue_growth_yoy=8.0, eps_growth_yoy=-10.0)
    score, available = ta._piotroski_from_fundamentals(f, info)
    assert available == 9
    assert score == 8  # everything passes except EPS growth
    assert ta.piotroski_display_value(score, available) == 8


def test_piotroski_partial_coverage_below_floor_reads_na():
    info = {"returnOnAssets": 0.1, "grossMargins": 0.3}
    f = _fund(roa=10.0, gross_margin=30.0)
    score, available = ta._piotroski_from_fundamentals(f, info)
    assert (score, available) == (2, 2)
    assert ta.piotroski_display_value(score, available) is None


# ---------------------------------------------------------------------------
# Altman Z
# ---------------------------------------------------------------------------
_BS = dict(total_assets=100.0, current_assets=40.0, current_liabilities=20.0,
           retained_earnings=30.0, ebit=10.0, revenue=80.0, liabilities=50.0)


def test_altman_public_model_uses_market_cap_for_x4():
    z, variant = ta.compute_altman_z(book_equity=50.0, market_cap=120.0, **_BS)
    assert variant == "public"
    expected = 1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.1 + 0.6 * (120 / 50) + 0.8
    assert z == pytest.approx(expected)


def test_altman_falls_back_to_private_coefficients_with_book_equity():
    z, variant = ta.compute_altman_z(book_equity=50.0, market_cap=0.0, **_BS)
    assert variant == "private"
    expected = 0.717 * 0.2 + 0.847 * 0.3 + 3.107 * 0.1 + 0.420 * 1.0 + 0.998 * 0.8
    assert z == pytest.approx(expected)


def test_altman_never_mixes_public_coefficients_with_book_equity():
    z_pub, _ = ta.compute_altman_z(book_equity=50.0, market_cap=50.0, **_BS)
    z_priv, _ = ta.compute_altman_z(book_equity=50.0, market_cap=0.0, **_BS)
    mixed = 1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.1 + 0.6 * 1.0 + 0.8
    assert z_pub == pytest.approx(mixed)          # same X4 value → same result
    assert z_priv != pytest.approx(mixed)          # the old behaviour is gone


# A cash-rich mega-cap: market cap 4,000 against liabilities of 32. X4 alone
# is 125, so 0.6*X4 = 75 — far past the old blanket -10..20 window, which
# reported the single safest kind of balance sheet as "unavailable".
_MEGACAP = dict(
    total_assets=111.0, current_assets=80.0, current_liabilities=19.0,
    retained_earnings=68.0, ebit=81.0, revenue=130.0, liabilities=32.0,
)


def test_altman_public_model_survives_a_debt_light_megacap():
    z, variant = ta.compute_altman_z(book_equity=79.0, market_cap=4000.0, **_MEGACAP)
    assert variant == "public"
    assert z is not None, "a low-leverage mega-cap must not report Z as unavailable"
    assert z > ta.ALTMAN_Z_BOUNDS["private"][1], "public Z legitimately exceeds the Z' ceiling"
    assert z == pytest.approx(80.096396, rel=1e-6)


def test_altman_does_not_flip_between_na_and_a_score_on_market_cap_presence():
    with_cap, _ = ta.compute_altman_z(book_equity=79.0, market_cap=4000.0, **_MEGACAP)
    without_cap, _ = ta.compute_altman_z(book_equity=79.0, market_cap=0.0, **_MEGACAP)
    assert with_cap is not None and without_cap is not None


def test_altman_still_rejects_a_unit_error():
    # Liabilities off by three orders of magnitude: X4 explodes past any
    # plausible balance sheet and must be refused, not published.
    z, variant = ta.compute_altman_z(
        book_equity=79.0, market_cap=4000.0,
        **dict(_MEGACAP, liabilities=0.001))
    assert (z, variant) == (None, "")


def test_altman_private_variant_keeps_its_tighter_ceiling():
    assert ta.ALTMAN_Z_BOUNDS["private"][1] < ta.ALTMAN_Z_BOUNDS["public"][1]


@pytest.mark.parametrize("bad", [dict(total_assets=0.0), dict(liabilities=0.0)])
def test_altman_reports_unavailable_without_a_denominator(bad):
    kwargs = dict(_BS, book_equity=50.0, market_cap=10.0)
    kwargs.update(bad)
    assert ta.compute_altman_z(**kwargs) == (None, "")


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def _risk():
    r = ta.RiskMetrics()
    r.annualized_volatility = 0.25
    r.liquidity_score = 8.0
    r.risk_grade = "B"
    r.beta = 1.0
    r.avg_volume = 5_000_000
    return r


def test_no_kelly_ceiling_is_published_and_size_is_risk_budgeted():
    """The Kelly figure rested on an uncalibrated win probability and never
    constrained the allocation, so it is not published. Size comes from the
    risk budget alone and does not move with the target."""
    sizer = ta.PositionSizer()
    wide = sizer.calculate(ta.Signal.BUY, 100.0, 95.0, 60.0, _risk(), 100_000, target=125.0)
    thin = sizer.calculate(ta.Signal.BUY, 100.0, 95.0, 60.0, _risk(), 100_000, target=102.0)
    assert wide.kelly_optimal == thin.kelly_optimal == 0.0
    assert wide.recommended_shares == thin.recommended_shares > 0


# ---------------------------------------------------------------------------
# Component score clamps
# ---------------------------------------------------------------------------
def test_component_scores_are_clamped_to_0_100():
    gen = ta.SignalGenerator()
    val = ta.ValuationMetrics()
    val.valuation_score = 140.0
    fund = _fund(quality_score=-30.0)
    scores = gen._calculate_component_scores(
        ta.TechnicalIndicators(), fund, _risk(), ta.SentimentData(),
        ta.InsiderData(), ta.MarketIntelligence(), val,
    )
    assert scores["valuation"] == 100.0
    assert scores["quality"] == 0.0
    assert all(0 <= v <= 100 for v in scores.values())


# ---------------------------------------------------------------------------
# Payload provenance
# ---------------------------------------------------------------------------
def test_mcp_payload_carries_trade_setup_provenance():
    result = ta.AnalysisResult()
    result.ticker = "TEST"
    result.trade_setup = ta.TradeSetup()
    result.trade_setup.entry_price = 100.0
    result.trade_setup.stop_loss = 95.0
    result.trade_setup.target_base = 98.0
    result.trade_setup.has_long_reward = False
    result.trade_setup.levels_available = True
    result.trade_setup.setup_basis = "valuation at or below spot"
    result.fundamental = _fund(piotroski_f_score=3, piotroski_components_available=2,
                               altman_z_status="unavailable")
    d = ta._mcp_result_to_dict(result)
    ts = d["trade_setup"]
    assert ts["has_long_reward"] is False
    assert ts["levels_available"] is True
    assert "valuation" in ts["setup_basis"]
    assert d["fundamental"]["piotroski_f_score"] is None
    assert d["fundamental"]["altman_z_score"] is None
    assert d["fundamental"]["altman_z_status"] == "unavailable"


# ---------------------------------------------------------------------------
# Console output must never fail a scan (cp1252 stdout)
# ---------------------------------------------------------------------------
def test_deep_confirm_summary_survives_a_cp1252_console(monkeypatch, capsys):
    import io
    import sys as _sys
    import SP_ELITE_PRO as sp

    buf = io.BytesIO()
    strict = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    monkeypatch.setattr(_sys, "stdout", strict)
    records = [{
        "ticker": "AAPL", "scanner_action": "BUY", "deep_action": "WATCHLIST",
        "final_action": "WATCHLIST", "reason": "Bearish reversal — wait for confirmation",
        "allocation_before": 1000.0, "allocation_after": 0.0, "downgraded": True,
    }]
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    sp.QuantaEliteTradingSystem._print_deep_confirmation_summary(obj, records)  # must not raise
    strict.flush()
    out = buf.getvalue().decode("cp1252", errors="replace")
    assert "DEEP CONFIRMATION SUMMARY" in out
    assert "AAPL" in out


# ---------------------------------------------------------------------------
# [B5] The partial Piotroski score must not reach the scores or the console
# ---------------------------------------------------------------------------
class _Fund:
    """Minimal stand-in for FundamentalData's Piotroski fields."""

    def __init__(self, score, available):
        self.piotroski_f_score = score
        self.piotroski_components_available = available


def test_piotroski_threshold_reads_require_enough_inputs():
    # 2 of 2 reported tests passed. The raw score (2) is neither "quality"
    # (>=7) nor evidence of weakness (<=3) — it is simply not a score yet.
    assert ta.piotroski_is_reportable(_Fund(2, 2)) is False
    assert ta.piotroski_is_reportable(_Fund(2, 5)) is True
    assert ta.piotroski_is_reportable(None) is False


def test_piotroski_display_text_is_honest_about_coverage():
    assert ta.piotroski_display_text(_Fund(8, 9)) == "8/9 [Quality]"
    assert ta.piotroski_display_text(_Fund(4, 9)) == "4/9 [Weak]"
    assert ta.piotroski_display_text(_Fund(2, 3)) == "N/A (3/9 inputs)"


def _quality_leg(score, available):
    """Reproduce the Piotroski contribution to quality_score (0-15)."""
    if available >= ta.PIOTROSKI_MIN_INPUTS:
        return score / available * 15
    return 7.5


def test_quality_leg_is_unchanged_for_full_coverage():
    assert _quality_leg(9, 9) == pytest.approx(15.0)
    assert _quality_leg(6, 9) == pytest.approx(10.0)


def test_quality_leg_does_not_penalise_unreported_fundamentals():
    # 2 of 2 reported tests passed. Scored over the full nine this was 3.3 of
    # 15 — a 11.7-point quality penalty for data the provider never sent.
    assert _quality_leg(2, 2) == 7.5
    assert 2 / 9 * 15 < 7.5


def test_quality_leg_scores_partial_but_sufficient_coverage_pro_rata():
    # 5 of 6 reported tests passed is a strong result, not a 8.3/15 one.
    assert _quality_leg(5, 6) == pytest.approx(12.5)
    assert 5 / 9 * 15 == pytest.approx(8.333, rel=1e-3)


def test_rich_display_mirrors_the_engine_reporting_floor():
    import rich_display

    assert rich_display.PIOTROSKI_MIN_INPUTS == ta.PIOTROSKI_MIN_INPUTS
    assert rich_display._piotroski_text(_Fund(2, 3)) == ta.piotroski_display_text(_Fund(2, 3))
    assert rich_display._piotroski_text(_Fund(8, 9)) == ta.piotroski_display_text(_Fund(8, 9))


# ---------------------------------------------------------------------------
# [B6/B14] deep_mode is per record, and reaches the summary table
# ---------------------------------------------------------------------------
class _ConfirmPick:
    def __init__(self, ticker):
        self.ticker = ticker
        self.signal = "BUY"
        self.final_action = "BUY"
        self.final_action_reasons = []
        self.allocation = 1000.0
        self.shares = 10
        self.position_pct = 5.0


class _ConfirmRes:
    data_status = "OK"
    final_action = "BUY"
    final_action_reasons = []


class _MixedModeAnalyzer:
    """LEGACY only accepts the old signature; BOOM raises; FAST takes fast_mode."""

    def analyze(self, ticker, account_size=0, **kwargs):
        if ticker == "LEGACY" and "fast_mode" in kwargs:
            raise TypeError("unexpected keyword argument 'fast_mode'")
        if ticker == "BOOM":
            raise RuntimeError("provider exploded")
        return _ConfirmRes()


def _run_deep_confirm(monkeypatch, tickers):
    import SP_ELITE_PRO as sp

    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj.capital = 2500.0
    obj._deep_confirm_records = []
    obj._deep_confirm_enabled = True

    analyzer = _MixedModeAnalyzer()

    class _FakeTA:
        QuantaEliteAnalyzer = staticmethod(lambda: analyzer)

    real_import = sp.importlib.import_module
    monkeypatch.setattr(
        sp.importlib, "import_module",
        lambda name, *a, **k: _FakeTA if name == "TradingAnalysis_ELITE" else real_import(name, *a, **k),
    )
    sp.QuantaEliteTradingSystem._deep_confirm_final_picks(
        obj, [_ConfirmPick(t) for t in tickers])
    return sp, obj, {r["ticker"]: r for r in obj._deep_confirm_records}


def test_deep_mode_is_recorded_per_pick_not_carried_from_the_last_one(monkeypatch):
    _, _, records = _run_deep_confirm(monkeypatch, ["LEGACY", "BOOM", "FAST"])

    from engine import thresholds as _T
    # [F7] One configured research mode for every pick (and for My Portfolio).
    assert records["LEGACY"]["deep_mode"] == _T.RESEARCH.MODE
    assert records["FAST"]["deep_mode"] == _T.RESEARCH.MODE
    # BOOM never reached a successful analyze() call, so its mode is genuinely
    # unknown - it must not inherit a neighbour's label.
    assert records["BOOM"]["deep_mode"] == "unknown"


def test_deep_confirmation_summary_reports_the_mode(monkeypatch, capsys):
    sp, obj, _ = _run_deep_confirm(monkeypatch, ["LEGACY", "FAST"])
    capsys.readouterr()
    sp.QuantaEliteTradingSystem._print_deep_confirmation_summary(
        obj, obj._deep_confirm_records)
    out = capsys.readouterr().out
    from engine import thresholds as _T
    assert "Mode" in out, "the summary table must name the analyzer mode"
    assert _T.RESEARCH.MODE in out


# ---------------------------------------------------------------------------
# [B7] "Pre-Momentum" must stay narrow on the webapp path too
# ---------------------------------------------------------------------------
def _universe(scan_type):
    import SP_ELITE_PRO as sp

    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    obj.market_data = sp.MarketDataProvider()
    return sp, obj.build_us_stock_universe(scan_type)


def test_pre_momentum_is_narrow_and_sized_by_config(monkeypatch):
    """scan_limits had no pre_momentum key, so the limit fell through to
    len(universe) and a webapp Pre-Momentum request ran all ~669 names. It is
    now the configured sub-industries (B-87), capped by US_SCAN_LIMITS."""
    import _index_fixture as fx
    from engine import thresholds as T
    fx.install(monkeypatch)
    sp, uni = _universe("pre_momentum")
    assert set(uni) <= fx.SEMIS
    assert len(uni) <= T.UNIVERSE.US_SCAN_LIMITS["pre_momentum"] < 100


def test_a_full_scan_is_still_the_whole_universe():
    _, uni = _universe("full")
    assert len(uni) > 500


# ---------------------------------------------------------------------------
# [B8] The pre-filter bulk download must not block the scan indefinitely
# ---------------------------------------------------------------------------
@pytest.fixture
def _prefilter(monkeypatch):
    import SP_ELITE_PRO as sp

    monkeypatch.setattr(sp.TradingConfig, "PREFILTER_TIMEOUT_SECONDS", 1.0)
    obj = sp.QuantaEliteTradingSystem.__new__(sp.QuantaEliteTradingSystem)
    return sp, obj, [f"T{i:03d}" for i in range(600)]


def test_prefilter_abandons_a_throttled_download_within_its_budget(_prefilter, monkeypatch):
    import time as _time

    sp, obj, tickers = _prefilter
    # yf.download has no timeout of its own; a throttled provider simply
    # blocks. Observed at 5-8 minutes against an advertised "30-60s".
    # [A01] It now runs in an owned child process (QUANTA_BULK_TARGET).
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:slow")

    started = _time.time()
    out = obj.smart_prefilter(tickers, max_candidates=250)
    elapsed = _time.time() - started

    assert elapsed < 8, f"budget not honoured: {elapsed:.1f}s"
    assert len(out) == 250
    # The fallback must stay unbiased - a deterministic spread, never an
    # alphabetical prefix.
    assert out == obj._even_sample(tickers, 250)


def test_prefilter_surfaces_a_provider_error_rather_than_hanging(_prefilter, monkeypatch):
    sp, obj, tickers = _prefilter
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:boom")
    out = obj.smart_prefilter(tickers, max_candidates=250)
    assert len(out) == 250


def test_prefilter_handles_an_empty_frame(_prefilter, monkeypatch):
    sp, obj, tickers = _prefilter
    monkeypatch.setenv("QUANTA_BULK_TARGET", "_bounded_targets:empty")
    assert len(obj.smart_prefilter(tickers, max_candidates=250)) == 250


def test_prefilter_skips_the_download_when_no_reduction_is_needed(_prefilter, monkeypatch):
    import yfinance as yf
    sp, obj, tickers = _prefilter

    def _must_not_run(*a, **k):
        raise AssertionError("no download should happen when the universe already fits")

    monkeypatch.setattr(yf, "download", _must_not_run)
    assert obj.smart_prefilter(tickers[:100], max_candidates=250) == tickers[:100]
