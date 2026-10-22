"""Audit F9: one source for every decision threshold.

The scanner, the Research engine, the decision core and the web app read their
cut-offs from engine/thresholds.py, the page fills the numbers it prints from
it, and reason/remedy text is formatted from it at the moment it is written.
These tests pin that contract:

* reason and remedy text follows the config (patch the config, the text moves);
* the old names are read-through aliases, not copies;
* the scanner reports an analyst-target fact as ANALYST_DOWNSIDE, not a DCF one;
* /api/thresholds publishes every group and the page embeds the same values;
* the JS test fixture is exactly the config;
* none of the moved literals has crept back into the sources.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

from engine import thresholds as T  # noqa: E402
from engine.blockers import BLOCKERS, blocker_info  # noqa: E402
from engine.decision_engine import resolve_final_action  # noqa: E402
from engine.types import DecisionInputs, FinalAction  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "core"
WEBAPP = REPO / "webapp"


# ── reason / remedy text is formatted from the config ────────────────────────

def test_remedy_text_quotes_the_configured_research_minimum():
    info = blocker_info("LOW_REWARD_RISK")
    assert T.fmt_rr(T.RR.RESEARCH_MIN) in info["text"]
    assert T.fmt_rr(T.RR.RESEARCH_MIN) in info["remedy"]


def test_remedy_text_follows_a_config_change(monkeypatch):
    monkeypatch.setattr(T.RR, "RESEARCH_MIN", 2.25)
    monkeypatch.setattr(T.RR, "SCANNER_MIN", 2.75)
    assert "2.25:1" in BLOCKERS["LOW_REWARD_RISK"][1]
    assert "2.75:1" in BLOCKERS["SCANNER_LOW_RR"][2]
    # The resolver's default hurdle is the same value, read at construction.
    di = DecisionInputs(ticker="X", composite_score=70, risk_grade="B", risk_reward_ratio=2.0,
                        current_price=100, stop_loss=95, target_price=110)
    assert di.min_rr_for_buy == 2.25


def test_scanner_and_research_hurdles_are_named_apart():
    scan, research = blocker_info("SCANNER_LOW_RR"), blocker_info("LOW_REWARD_RISK")
    assert T.fmt_rr(T.RR.SCANNER_MIN) in scan["text"] and "Scanner" in scan["text"]
    assert T.fmt_rr(T.RR.RESEARCH_MIN) in research["text"] and "Research" in research["text"]


def test_score_band_text_follows_the_band_table(monkeypatch):
    bands = tuple((55.0 if n == "HOLD" else f, n) for f, n in T.LADDER.SCORE_BANDS)
    monkeypatch.setattr(T.LADDER, "SCORE_BANDS", bands)
    # band_floor reads the module-level table bound at import; the text reads it live.
    import engine.score_bands as sb
    monkeypatch.setattr(sb, "SCORE_BANDS", bands)
    assert "below 55 (bearish)" in BLOCKERS["SCORE_BEARISH"][1]


# ── old names are aliases of the one value ───────────────────────────────────

def test_trading_config_reads_through_to_thresholds(monkeypatch):
    import SP_ELITE_PRO as sp
    assert sp.TradingConfig.RISK_REWARD_MIN == T.RR.SCANNER_MIN
    monkeypatch.setattr(T.RR, "SCANNER_MIN", 2.4)
    assert sp.TradingConfig.RISK_REWARD_MIN == 2.4
    monkeypatch.setattr(T.SIZING, "MIN_POSITION_SIZE", 75.0)
    assert sp.TradingConfig.MIN_POSITION_SIZE == 75.0


def test_research_engine_reads_through_to_thresholds(monkeypatch):
    import TradingAnalysis_ELITE as ta
    assert ta.Config.MIN_RISK_REWARD == T.RR.RESEARCH_MIN
    monkeypatch.setattr(T.RR, "RESEARCH_MIN", 1.7)
    assert ta.Config.MIN_RISK_REWARD == 1.7
    assert ta._format_rr(1.6, 120.0, 90.0, 100.0).startswith("Unfavorable")


def test_portfolio_policy_defaults_come_from_thresholds(monkeypatch):
    from portfolio.analytics import PortfolioPolicy
    monkeypatch.setattr(T.PORTFOLIO, "MAX_SINGLE_POSITION", 0.12)
    assert PortfolioPolicy().max_single_position == 0.12


# ── an analyst fact is reported as one ──────────────────────────────────────

def test_analyst_downside_is_its_own_code_not_a_dcf_one():
    di = DecisionInputs(ticker="X", composite_score=70, risk_grade="B", risk_reward_ratio=2.5,
                        current_price=100, stop_loss=95, target_price=115,
                        analyst_downside_pct=T.LADDER.ANALYST_DOWNSIDE_TRIM_PCT + 5)
    res = resolve_final_action(di)
    assert "ANALYST_DOWNSIDE" in res.override_codes
    assert "DCF_OVERVALUATION" not in res.override_codes
    assert res.final_action == FinalAction.ACCUMULATE        # the same one-notch trim as before
    assert any("Analyst consensus target" in r for r in res.overrides_applied)


def test_scanner_passes_analyst_downside_under_its_own_name():
    src = (CORE / "SP_ELITE_PRO.py").read_text(encoding="utf-8")
    assert "analyst_downside_pct=_analyst_down" in src
    assert "dcf_overvaluation_pct=_dcf_over" not in src


# ── the API and the page publish the same values ────────────────────────────

def test_api_thresholds_publishes_every_group():
    import app as webapp
    resp = webapp.app.test_client().get("/api/thresholds", headers={"X-QUANTA-Request": "1"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["thresholds"]) == {g.__name__ for g in T.GROUPS}
    assert body["thresholds"]["RR"]["SCANNER_MIN"] == T.RR.SCANNER_MIN


def test_page_embeds_the_same_thresholds():
    import app as webapp
    html = webapp.app.test_client().get("/").get_data(as_text=True)
    m = re.search(r'<script type="application/json" id="quanta-thresholds">(.*?)</script>', html, re.S)
    assert m, "the page must carry the thresholds block"
    assert json.loads(m.group(1)) == T.as_dict()


def test_js_fixture_is_exactly_the_config():
    fixture = json.loads((WEBAPP / "tests" / "fixtures" / "thresholds.json").read_text(encoding="utf-8"))
    assert fixture == json.loads(json.dumps(T.as_dict())), (
        "tests/fixtures/thresholds.json is stale: regenerate it from engine/thresholds.py")


# ── no moved literal has crept back ──────────────────────────────────────────

_SOURCES = [CORE / "SP_ELITE_PRO.py", CORE / "TradingAnalysis_ELITE.py", CORE / "rich_display.py",
            *sorted((CORE / "engine").glob("*.py")), CORE / "portfolio" / "analytics.py",
            CORE / "tracking" / "ledger.py", WEBAPP / "app.py", WEBAPP / "templates" / "index.html",
            *sorted((WEBAPP / "static").glob("*.js"))]

# Each pattern is a threshold that used to be typed in place.
_FORBIDDEN = [
    r"\b1\.[58] ?: ?1\b",                         # R:R hurdles in text
    r"rr_ratio < 1\.5\b", r"MIN_RISK_REWARD = 1\.5",
    r"min_rr_for_buy: float = 1\.5", r"min_score_for_buy: float = 55\.0",
    r"RISK_REWARD_MIN: float = 1\.8",
    r"capital\", 2500\)", r"\|\| 2500\b", r'value="2500"', r">= 10000\)",
    r"alloc_before \* 0\.5\b",
    r"vix_value < (12|20|30)\b", r"vix_value [<>] 25\b",
    r"BANNED_GRADES = \{'D', 'F'\}",
    r"rsi > 75\b", r"overbought_threshold = 80",
    r"num_candidates >= 20\b", r"len\(safe_results\) <= 8\b",
    r"dcf_overvaluation_pct > 25\.0",
    r"ageMin >= 10\b", r"ageMin >= 5\b",
    r"GUIDE_MIN_RR = 1\.5", r"GUIDE_EXIT_FLOOR = 35",
    r"MIN_SAMPLE = 30\b",
    r"0\.02 \* float\(capital\)",
    # A08: chase and India literals
    r"GAP_THRESHOLD = 0\.0", r"MOVE_(5|10)D_THRESHOLD = 0\.",
    r"5000_00_00_000", r"MIN_PRICE_INR = ", r"MAX_PRICE_INR = ", r"MIN_AVG_VOLUME_INR = ",
    r"MIN_DAILY_INR_VOLUME = ", r"0\.065 if is_indian_stock else 0\.04",
    r"em_score < 4\b", r"early_momentum_score < 3\b", r"mom_1m >= 0\.20\b",
    r"at least four of six checks",
]


@pytest.mark.parametrize("pattern", _FORBIDDEN)
def test_no_moved_threshold_literal_remains(pattern):
    hits = []
    for path in _SOURCES:
        if path.name == "thresholds.py":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{path.relative_to(REPO)}:{i}: {line.strip()[:120]}")
    assert not hits, "threshold literal outside engine/thresholds.py:\n" + "\n".join(hits)


def test_every_config_group_is_published():
    """A group missing from GROUPS never reaches /api/thresholds or the page (CHASE was, A07)."""
    groups = {name for name, obj in vars(T).items()
              if isinstance(obj, type) and issubclass(obj, T._Group) and obj is not T._Group}
    assert groups == {g.__name__ for g in T.GROUPS}


# ── the short-term scanner's profile lives here too ──────────────────────────

def test_shortterm_scanner_reads_its_profile_from_thresholds(monkeypatch):
    import SP_ShortTerm as st

    assert st.Config.MIN_RISK_REWARD == T.SHORTTERM.MIN_RISK_REWARD
    assert st.Config.CORRELATION_THRESHOLD == T.SHORTTERM.CORRELATION_THRESHOLD
    assert st.Config.MAX_PORTFOLIO_RISK == T.SHORTTERM.MAX_PORTFOLIO_RISK
    assert st.TradingSignal.STRONG_BUY.min_score == T.SHORTTERM.STRONG_BUY
    # Aliases, not copies: a change to the profile moves the scanner.
    monkeypatch.setattr(T.SHORTTERM, "MIN_RISK_REWARD", 2.4)
    assert st.Config.MIN_RISK_REWARD == 2.4


def test_allocation_gate_defaults_to_the_books_caps(monkeypatch):
    from engine.allocation import check_allocation_eligible
    from engine.types import DecisionResult, FinalAction

    dec = DecisionResult(final_action=FinalAction.BUY)
    ok, _ = check_allocation_eligible(dec, sector_exposure_pct=T.BOOK.MAX_SECTOR_PCT - 0.01,
                                      portfolio_risk_pct=T.SIZING.MAX_PORTFOLIO_RISK - 0.001)
    assert ok
    monkeypatch.setattr(T.SIZING, "MAX_PORTFOLIO_RISK", 0.02)
    ok, reasons = check_allocation_eligible(dec, portfolio_risk_pct=0.03)
    assert not ok and "2.00%" in reasons[0]
