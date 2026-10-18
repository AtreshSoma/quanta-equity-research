"""Regression tests for the 2026-10-17 Picks-engine audit (branch Quanta_1017).

Each test names the audit item it pins. None of them touch the network.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


class _NoData:
    def __init__(self, *a, **k):
        pass

    def history(self, *a, **k):
        return pd.DataFrame()


def _spy(n: int, start: float, end: float) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"Close": np.linspace(start, end, n)}, index=idx)


# --- #1: no invented VIX ----------------------------------------------------

def test_vix_cache_starts_empty_in_a_fresh_process():
    """The class must not ship pre-seeded 'last good' readings. Checked in a
    fresh interpreter because other tests legitimately populate the cache."""
    code = ("import sys; sys.path[:0]=['core','modules']; import SP_ELITE_PRO as p; "
            "print(repr(p.MarketRegimeDetector._last_good_vix_by_market))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "{}"


def test_failed_vix_fetch_without_cache_is_a_default_placeholder(monkeypatch):
    import SP_ELITE_PRO as pro
    monkeypatch.setattr(pro, "_import_optional", lambda *a, **k: None)
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    monkeypatch.setattr(pro.MarketRegimeDetector, "_last_good_vix_by_market", {})
    reg = pro.MarketRegimeDetector.detect_regime(vix=0.0, spy_hist=None, market="US")
    # "default" is the one source the webapp refuses to publish as a reading.
    assert reg.vix_source == "default" and reg.vix_is_proxy


def test_only_a_real_quote_is_reused_as_last_good(monkeypatch):
    import SP_ELITE_PRO as pro
    monkeypatch.setattr(pro, "_import_optional", lambda *a, **k: None)
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    monkeypatch.setattr(pro.MarketRegimeDetector, "_last_good_vix_by_market", {"US": 17.3})
    reg = pro.MarketRegimeDetector.detect_regime(vix=0.0, spy_hist=None, market="US")
    assert reg.vix_source == "last_good" and reg.vix_level == pytest.approx(17.3)


# --- #2 / #18: cash share from config, error case not overwritten ----------

def test_failed_detection_keeps_its_error_cash_share(monkeypatch):
    import SP_ELITE_PRO as pro
    from engine import thresholds as T

    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    # Force the outer except: the import helper itself raises.
    monkeypatch.setattr(pro, "_import_optional", boom)
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    reg = pro.MarketRegimeDetector.detect_regime(vix=0.0, spy_hist=None)
    assert reg.regime_type == "NEUTRAL"
    assert reg.recommended_cash_pct == T.REGIME.CASH_PCT["ERROR"]
    assert reg.recommended_cash_pct > T.REGIME.CASH_PCT["NEUTRAL"]


@pytest.mark.parametrize("closes,expected", [((100, 160), "BULL"), ((160, 100), "BEAR")])
def test_cash_share_follows_the_config_table(monkeypatch, closes, expected):
    import SP_ELITE_PRO as pro
    from engine import thresholds as T
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    reg = pro.MarketRegimeDetector.detect_regime(vix=14.0, spy_hist=_spy(300, *closes))
    assert reg.regime_type == expected
    assert reg.recommended_cash_pct == T.REGIME.CASH_PCT[expected]


# --- #3: a cash account is never levered -----------------------------------

def test_calm_bull_exposure_never_exceeds_capital(monkeypatch):
    import SP_ELITE_PRO as pro
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    reg = pro.MarketRegimeDetector.detect_regime(vix=9.0, spy_hist=_spy(300, 100, 160))
    assert reg.regime_type == "BULL" and reg.volatility_regime == "LOW"
    assert reg.recommended_exposure <= 1.0


# --- #16: one regime weight table ------------------------------------------

def test_regime_factor_weights_are_the_weights_scoring_uses(monkeypatch):
    import SP_ELITE_PRO as pro
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    reg = pro.MarketRegimeDetector.detect_regime(vix=14.0, spy_hist=_spy(300, 160, 100))
    assert reg.factor_weights == pro._REGIME_WEIGHTS["BEAR"]


# --- #17: trend hysteresis --------------------------------------------------

def test_a_close_just_above_the_50_day_is_not_bull(monkeypatch):
    import SP_ELITE_PRO as pro
    monkeypatch.setattr(pro.yf, "Ticker", _NoData)
    hist = _spy(300, 100, 160)
    sma50 = hist["Close"].rolling(50).mean().iloc[-1]
    hist.iloc[-1, 0] = sma50 * 1.002          # 0.2% above: inside the band
    reg = pro.MarketRegimeDetector.detect_regime(vix=14.0, spy_hist=hist)
    assert reg.regime_type == "RANGING"


# --- #5: the published (binding) levels must clear the hurdle they show ----

def test_funded_pick_cannot_publish_research_rr_below_the_research_hurdle(monkeypatch):
    """The deep engine says BUY but its own levels give R:R 1.20 with no
    LOW_REWARD_RISK code attached. The page would show a funded Buy beside a
    'needs 1.50' remedy. Fail closed: the pick is not funded on those levels."""
    from engine import thresholds as T
    from test_binding_levels import _install_research, _hst_like_pick
    from test_elite_pro_review2_fixes import _bare_system
    _install_research(monkeypatch, "BUY", (22.56, 21.86, 23.47, 1.20, 63))
    out = _bare_system()._deep_confirm_final_picks([_hst_like_pick()])[0]
    assert out.binding_levels["risk_reward"] < T.RR.RESEARCH_MIN
    assert out.allocation == 0
    assert (out.final_action or "").upper() not in ("BUY", "STRONG_BUY", "ACCUMULATE")
