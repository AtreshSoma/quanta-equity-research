"""elite.research.v3, pre-registered 2026-10-29 (docs/preregistration/elite_research_v3.md).

What these pin:

* the weight table is exactly the pre-registered one, and one table in every regime;
* the live model stays v2 by default, and QUANTA_RESEARCH_MODEL=v3 switches the
  composite and the evidence key together (checked in fresh processes, because the
  selector and the evidence constant are read at import);
* v3 has no registry entry, so it is UNTESTED and every buy-tier call says so;
* the other version is scored in shadow from the same components, published
  beside the call, recorded under its own ledger engine, and never changes the
  live action, score or levels;
* the shadow's ledger rows are never pooled with the live model's statistics;
* trials are registered only by the explicit CLI, never by a test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from _paths import ensure_paths

ensure_paths(webapp=True)

from engine import thresholds as T  # noqa: E402
from engine.evidence import (  # noqa: E402
    ELITE_PRO, ELITE_V2, ELITE_V3, GOVERNED_BY, LINEAGE, SUCCESSORS,
    EvidenceStatus, band_stance, get_evidence, live_research_key, load_registry, present_action,
)
from engine.market_calendar import last_completed_session  # noqa: E402
from engine.types import FinalAction  # noqa: E402
import TradingAnalysis_ELITE as ta  # noqa: E402
from tracking.fingerprint import ENGINE_SOURCES, code_fingerprint  # noqa: E402
from tracking.ledger import SignalLedger, record_shadow_calls, shadow_engine  # noqa: E402
from apex_quant import trials  # noqa: E402

CORE = Path(__file__).resolve().parents[1] / "core"
MODULES = Path(__file__).resolve().parents[1] / "modules"

V3_TABLE = {"technical": 0.10, "fundamental": 0.40, "valuation": 0.10,
            "quality": 0.30, "sentiment": 0.10}
V2_SIDEWAYS_RAW = {"technical": .3333333333, "fundamental": .2777777778, "valuation": .1666666667,
                   "quality": .1111111111, "sentiment": .1111111111}
SCORES = {"technical": 60.0, "fundamental": 70.0, "valuation": 40.0, "quality": 80.0,
          "sentiment": 50.0, "risk": 30.0}


def _registry(**strategies):
    return {"strategies": strategies}


def _run_fresh(code: str, env_extra: dict) -> str:
    """Run ``code`` in a new interpreter (selector and evidence key are read at import)."""
    env = {k: v for k, v in os.environ.items() if k != "QUANTA_RESEARCH_MODEL"}
    env.update({"PYTHONIOENCODING": "utf-8", "QUANTA_TRACKING_DISABLED": "1",
                "PYTHONPATH": os.pathsep.join([str(CORE), str(MODULES)])})
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(CORE), env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


_PROBE = (
    "import json\n"
    "from engine import thresholds as T\n"
    "from engine.evidence import ELITE, ELITE_PRO, get_evidence, live_research_key\n"
    "from TradingAnalysis_ELITE import SignalGenerator\n"
    "reg = {'strategies': {'elite.research.v3': {'status': 'NO_EDGE', 'summary': 'probe'}}}\n"
    "print(json.dumps({'model': T.RESEARCH.MODEL_VERSION, 'elite': ELITE,\n"
    "    'live': live_research_key(),\n"
    "    'weights': SignalGenerator()._get_regime_weights(None),\n"
    "    'picks': get_evidence(ELITE_PRO, registry=reg).status.value}))\n"
)


# --- the weights ----------------------------------------------------------------

def test_v3_table_is_exactly_the_pre_registered_one():
    assert ta.Config.RESEARCH_V3_WEIGHTS == V3_TABLE
    assert sum(ta.Config.RESEARCH_V3_WEIGHTS.values()) == pytest.approx(1.0)


def test_v3_table_follows_from_the_pre_registered_rule():
    # 70% split by 63-day IC between fundamental (0.061) and quality (0.046); the
    # other legs keep the 10% floor.
    fund = 0.70 * 0.061 / (0.061 + 0.046)
    qual = 0.70 * 0.046 / (0.061 + 0.046)
    assert round(fund, 2) == 0.40 and round(qual, 2) == 0.30


@pytest.mark.parametrize("regime_name", ["SIDEWAYS", "BULL_TRENDING", "BEAR_TRENDING", "HIGH_VOLATILITY", "CRISIS"])
def test_v3_is_one_fixed_table_in_every_regime(monkeypatch, regime_name):
    from TradingAnalysis_ELITE import MarketRegime
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v3")
    weights = ta.SignalGenerator()._get_regime_weights(getattr(MarketRegime, regime_name))
    assert weights == pytest.approx(V3_TABLE)


def test_v2_keeps_the_regime_tables_when_v3_is_not_selected(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    weights = ta.SignalGenerator()._get_regime_weights(None)
    total = sum(V2_SIDEWAYS_RAW.values())
    assert weights == pytest.approx({k: v / total for k, v in V2_SIDEWAYS_RAW.items()})


# --- the composite, by hand -----------------------------------------------------

def test_v3_composite_matches_the_hand_calculation(monkeypatch):
    # 60*0.10 + 70*0.40 + 40*0.10 + 80*0.30 + 50*0.10 = 67.0
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v3")
    gen = ta.SignalGenerator()
    assert gen._calculate_composite(SCORES, gen._get_regime_weights(None)) == pytest.approx(67.0)


def test_v2_composite_on_the_same_components_is_unchanged(monkeypatch):
    # 60/3 + 70*5/18 + 40/6 + 80/9 + 50/9 = 60.556 (SIDEWAYS, normalised)
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    gen = ta.SignalGenerator()
    assert gen._calculate_composite(SCORES, gen._get_regime_weights(None)) == pytest.approx(60.5556, abs=1e-3)


# --- the selector ---------------------------------------------------------------

def test_selector_defaults_to_v2_and_picks_follow_it():
    out = json.loads(_run_fresh(_PROBE, {}))
    assert out["model"] == "v2"
    assert out["elite"] == ELITE_V2 and out["live"] == ELITE_V2
    # Picks is governed by v2, which has no entry in this registry: UNTESTED.
    assert out["picks"] == "UNTESTED"


def test_env_override_v3_switches_composite_and_evidence_key_together():
    out = json.loads(_run_fresh(_PROBE, {"QUANTA_RESEARCH_MODEL": "v3"}))
    assert out["model"] == "v3"
    assert out["elite"] == ELITE_V3 and out["live"] == ELITE_V3
    assert out["weights"] == pytest.approx(V3_TABLE)
    # Picks is governed by Research, so it carries v3's status: the probe registry
    # gives v3 NO_EDGE, and Picks must show it.
    assert out["picks"] == "NO_EDGE"


def test_an_unknown_selector_value_keeps_v2():
    out = json.loads(_run_fresh(_PROBE, {"QUANTA_RESEARCH_MODEL": "v9"}))
    assert out["model"] == "v2" and out["elite"] == ELITE_V2


def test_live_research_key_reads_the_selector_at_call_time(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v3")
    assert live_research_key() == ELITE_V3
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    assert live_research_key() == ELITE_V2


# --- evidence -------------------------------------------------------------------

def test_v3_resolves_untested_when_the_registry_has_no_entry():
    reg = _registry(**{ELITE_V2: {"status": "NO_EDGE", "summary": "IC +0.041 (63d)",
                                  "tested_through": "2026-10-21"}})
    ev = get_evidence(ELITE_V3, registry=reg)
    assert ev.status is EvidenceStatus.UNTESTED
    assert ev.status.permits_recommendation is False
    assert "elite.research.v2" in ev.summary and "no edge" in ev.summary.lower()
    earlier = [x for x in ev.lineage if x["relation"] == "earlier"]
    assert earlier[0]["strategy"] == ELITE_V2
    assert earlier[0]["status"] == "NO_EDGE"


def test_the_real_registry_has_no_v3_entry_so_v3_is_untested():
    assert ELITE_V3 not in (load_registry().get("strategies") or {}), (
        "a v3 result belongs in the registry only through its pre-registered test; "
        "update this test with that result")
    assert get_evidence(ELITE_V3).status is EvidenceStatus.UNTESTED


def test_v3_lineage_names_the_change_from_v2_exactly():
    assert LINEAGE[ELITE_V3][0] == {
        "strategy": ELITE_V2,
        "change": "weights re-set by pre-registered rule from the 2026-10-22 leg ICs "
                  "(fundamental 40, quality 30, others 10)",
    }
    assert [x["strategy"] for x in LINEAGE[ELITE_V3][1:]] == ["elite.alpha_only.v1", "elite.composite"]


def test_v2_shows_v3_as_a_pre_registered_successor_not_as_evidence():
    reg = _registry(**{ELITE_V2: {"status": "NO_EDGE", "summary": "x"}})
    ev = get_evidence(ELITE_V2, registry=reg)
    succ = [x for x in ev.lineage if x["relation"] == "successor"]
    assert [x["strategy"] for x in succ] == [ELITE_V3]
    assert succ[0]["status"] == "UNTESTED"
    assert succ[0]["doc"] == "docs/preregistration/elite_research_v3.md"
    assert ev.status is EvidenceStatus.NO_EDGE  # the successor never changes v2's status


def test_a_successor_never_explains_or_fails_the_version_it_succeeds(monkeypatch):
    # The fail-closed sentence names an earlier version only.
    reg = _registry(**{ELITE_V2: {"status": "NO_EDGE", "summary": "x"}, ELITE_V3: {"status": "NO_EDGE", "summary": "y"}})
    ev = get_evidence(ELITE_V3, registry=reg)
    assert ev.status is EvidenceStatus.NO_EDGE
    assert ELITE_V3 not in [x["strategy"] for x in get_evidence(ELITE_V2, registry=reg).lineage
                            if x["relation"] == "earlier"]


def test_buy_tier_calls_on_v3_carry_the_unproven_qualifier():
    ev = get_evidence(ELITE_V3, registry=_registry())
    for action in (FinalAction.STRONG_BUY, FinalAction.BUY, FinalAction.ACCUMULATE):
        pres = present_action(action, ev)
        assert pres.label == action.value.replace("_", " ")
        assert pres.qualifier == "Unproven model"
        assert pres.sizing_basis == "risk-budget-only"


def test_picks_is_governed_by_the_live_research_key_at_import():
    # The v3 inheritance itself is checked in a fresh process (see the selector tests).
    assert GOVERNED_BY[ELITE_PRO] == ELITE_V2
    assert SUCCESSORS[ELITE_V2][0]["strategy"] == ELITE_V3


# --- shadow model in the composite ---------------------------------------------

def _generate(gen=None):
    gen = gen or ta.SignalGenerator()
    return gen.generate(
        technical=ta.TechnicalIndicators(), fundamental=ta.FundamentalData(), risk=ta.RiskMetrics(),
        sentiment=ta.SentimentData(), insider=ta.InsiderData(), market_intel=ta.MarketIntelligence(),
        valuation=ta.ValuationMetrics())


def _components(bd):
    return {"technical": bd.technical_score, "fundamental": bd.fundamental_score,
            "valuation": bd.valuation_score, "quality": bd.quality_score,
            "sentiment": bd.sentiment_score}


def test_shadow_under_live_v2_is_v3_scored_from_the_same_components(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    _s, _c, bd, _e = _generate()
    gen = ta.SignalGenerator()
    expected = gen._calculate_composite(_components(bd), V3_TABLE)
    assert bd.shadow_model["version"] == "v3"
    assert bd.shadow_model["score"] == pytest.approx(round(expected, 2))
    from engine.score_bands import band_for
    assert bd.shadow_model["band_action"] == band_for(expected)
    assert bd.shadow_model["weights"] == {k: round(v, 4) for k, v in V3_TABLE.items()}


def test_shadow_under_live_v3_is_v2_and_the_live_score_is_v3(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v3")
    _s, _c, bd, _e = _generate()
    gen = ta.SignalGenerator()
    assert bd.shadow_model["version"] == "v2"
    assert bd.composite_score == pytest.approx(gen._calculate_composite(_components(bd), V3_TABLE))
    v2_expected = gen._calculate_composite(_components(bd), gen._model_weights(None, "v2"))
    assert bd.shadow_model["score"] == pytest.approx(round(v2_expected, 2))


def test_shadow_never_changes_the_live_call(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    sig, conf, bd, expl = _generate()
    monkeypatch.setattr(ta.SignalGenerator, "_shadow_model", lambda self, *a, **k: {})
    sig2, conf2, bd2, expl2 = _generate()
    assert (sig, conf, bd.composite_score, expl.primary_reason) == (
        sig2, conf2, bd2.composite_score, expl2.primary_reason)
    assert bd2.shadow_model == {}


def test_a_failing_shadow_costs_the_live_call_nothing(monkeypatch):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    live = ta.SignalGenerator()._model_weights
    def boom(self, regime, version):
        if version == "v3":
            raise ValueError("shadow only")
        return live(regime, version)
    monkeypatch.setattr(ta.SignalGenerator, "_model_weights", boom)
    sig, conf, bd, _e = _generate()
    assert bd.shadow_model == {}
    # The live composite is the v2 value on these components, shadow or not.
    assert bd.composite_score == pytest.approx(
        ta.SignalGenerator()._calculate_composite(_components(bd), ta.SignalGenerator()._model_weights(None, "v2")))


# --- the Decision payload -------------------------------------------------------

@pytest.fixture
def empty_registry(tmp_path, monkeypatch):
    path = tmp_path / "evidence_registry.json"
    path.write_text(json.dumps({"strategies": {}, "factors": {}}))
    monkeypatch.setenv("QUANTA_EVIDENCE_PATH", str(path))
    return path


def _research_result(final_action="WATCHLIST", as_of=None):
    result = ta.AnalysisResult(ticker="TLT")
    result.current_price = 83.0
    result.market_timestamp = as_of or last_completed_session().isoformat()
    result.final_action = final_action
    result.final_stance = "NEUTRAL"
    result.scores = ta.ScoreBreakdown(composite_score=60.6, technical_score=60, fundamental_score=70,
                                      valuation_score=40, quality_score=80, sentiment_score=50)
    return result


def test_decision_carries_the_live_key_and_the_shadow(monkeypatch, empty_registry):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    result = _research_result()
    result.scores.shadow_model = {"version": "v3", "score": 67.0, "band_action": "BUY", "weights": dict(V3_TABLE)}
    decision = ta.decision_for_analysis(result)
    assert decision.evidence.strategy == ELITE_V2
    sm = decision.to_dict()["shadow_model"]
    assert sm["key"] == ELITE_V3 and sm["score"] == 67.0 and sm["band_action"] == "BUY"
    assert sm["status"] == "UNTESTED"
    assert "not used for this call" in sm["note"].lower()


def test_v3_live_decision_is_keyed_v3_and_shadows_v2(monkeypatch, empty_registry):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v3")
    result = _research_result()
    result.scores.shadow_model = {"version": "v2", "score": 58.1, "band_action": "ACCUMULATE", "weights": {}}
    decision = ta.decision_for_analysis(result)
    assert decision.evidence.strategy == ELITE_V3
    assert decision.evidence.status is EvidenceStatus.UNTESTED
    assert decision.to_dict()["shadow_model"]["key"] == ELITE_V2


def test_the_shadow_changes_no_live_field_of_the_decision(monkeypatch, empty_registry):
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    plain = ta.decision_for_analysis(_research_result())
    shadowed_result = _research_result()
    shadowed_result.scores.shadow_model = {"version": "v3", "score": 99.0, "band_action": "STRONG_BUY", "weights": {}}
    shadowed = ta.decision_for_analysis(shadowed_result)
    a, b = plain.to_dict(), shadowed.to_dict()
    assert "shadow_model" not in a
    b.pop("shadow_model")
    b.pop("generated_at"), a.pop("generated_at")
    assert a == b, "the shadow is informational: action, label, levels, score and evidence are unchanged"


def test_decision_without_a_shadow_has_no_shadow_key():
    from engine.decision import Decision, POSITION
    d = Decision(ticker="X", engine="elite", horizon=POSITION, action=FinalAction.WATCHLIST,
                 evidence=get_evidence(ELITE_V2, registry=_registry()))
    assert "shadow_model" not in d.to_dict()


def test_band_stance_follows_the_directional_bands():
    assert band_stance("STRONG_BUY") == band_stance("BUY") == band_stance("ACCUMULATE") == "BULLISH"
    assert band_stance("HOLD") == "NEUTRAL"
    assert band_stance("REDUCE") == band_stance("SELL") == band_stance("STRONG_SELL") == "BEARISH"


# --- the ledger -----------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(tmp_path / "shadow.sqlite3")


AS_OF = "2026-10-23"


def _decision_like():
    return SimpleNamespace(ticker="TLT", as_of=AS_OF, factor_scores={"technical": 60.0},
                           reference_price=83.0)


def test_shadow_calls_are_recorded_under_their_own_engine_at_every_horizon(ledger):
    shadow = {"key": ELITE_V3, "version": "v3", "score": 67.0, "band_action": "BUY",
              "status": "UNTESTED", "weights": {}}
    n = record_shadow_calls(_decision_like(), shadow, horizons=[5, 10, 30, 63],
                            context={"regime": "SIDEWAYS"}, ledger=ledger)
    assert n == 4
    rows = ledger.rows(engine="elite_v3_shadow")
    assert {r["horizon_days"] for r in rows} == {5, 10, 30, 63}
    assert {r["stance"] for r in rows} == {"BULLISH"}
    assert {r["score"] for r in rows} == {67.0}
    assert {r["evidence_status"] for r in rows} == {"UNTESTED"}
    assert json.loads(rows[0]["context_json"]) == {"regime": "SIDEWAYS", "strategy": ELITE_V3, "role": "shadow"}


def test_shadow_engine_name_and_fingerprint_sources():
    assert shadow_engine("v3") == "elite_v3_shadow" == "elite_v3_shadow"
    assert "elite_v3_shadow" in ENGINE_SOURCES and "elite_v2_shadow" in ENGINE_SOURCES
    fp = code_fingerprint("elite_v3_shadow")
    assert len(fp) == 64 and fp == code_fingerprint("elite_v3_shadow")


def test_summaries_never_pool_the_shadow_with_the_live_model(ledger):
    ledger.record(engine="elite", ticker="TLT", as_of=AS_OF, horizon_days=63, stance="BULLISH",
                  action="BUY", score=71.0, context={"strategy": ELITE_V2})
    record_shadow_calls(_decision_like(), {"key": ELITE_V3, "version": "v3", "score": 67.0,
                                           "band_action": "BUY", "status": "UNTESTED"},
                        horizons=[63], ledger=ledger)
    groups = ledger.summarize(group_by=("engine", "strategy", "horizon_days", "stance"))
    keyed = {(g.key["engine"], g.key["strategy"]) for g in groups}
    assert keyed == {("elite", ELITE_V2), ("elite_v3_shadow", ELITE_V3)}
    assert all(g.n_resolved == 0 for g in groups)  # counts are per group, never summed
    assert {r["engine"] for r in ledger.rows(engine="elite")} == {"elite"}


def test_shadow_recording_respects_the_tracking_switch(ledger, monkeypatch):
    monkeypatch.setenv("QUANTA_TRACKING_DISABLED", "1")
    n = record_shadow_calls(_decision_like(), {"key": ELITE_V3, "version": "v3", "score": 67.0,
                                               "band_action": "BUY", "status": "UNTESTED"},
                            horizons=[63], ledger=ledger)
    assert n == 0 and ledger.rows() == []


def test_a_deep_call_records_the_shadow_beside_the_live_one(ledger, monkeypatch, empty_registry):
    monkeypatch.delenv("QUANTA_TRACKING_DISABLED", raising=False)
    monkeypatch.setattr(T.RESEARCH, "MODEL_VERSION", "v2")
    result = _research_result()
    result.scores.shadow_model = {"version": "v3", "score": 67.0, "band_action": "BUY", "weights": dict(V3_TABLE)}
    assert ta.record_authoritative_signal(result, ledger=ledger) == 4, "the return value stays the live count"
    assert {r["engine"] for r in ledger.rows(engine="elite")} == {"elite"}
    assert len(ledger.rows(engine="elite")) == 4
    shadow_rows = ledger.rows(engine="elite_v3_shadow")
    assert len(shadow_rows) == 4
    assert {r["horizon_days"] for r in shadow_rows} == {5, 10, 30, 63}


# --- trials ---------------------------------------------------------------------

def test_trials_are_written_only_by_the_explicit_command(tmp_path, monkeypatch):
    real = trials.DEFAULT_TRIALS_PATH
    before = real.read_bytes() if real.exists() else None
    scratch = tmp_path / "trials.json"
    monkeypatch.setattr(trials, "DEFAULT_TRIALS_PATH", scratch)
    # Registering through the module's own helper and CLI writes only the path given.
    assert trials.register_trials(ELITE_V3, ["research_v3_weights_2026-10-29"], path=scratch) == 1
    assert trials.main([ELITE_V3, "research_v3_weights_2026-10-29", "--on-date", "2026-10-29"]) == 0
    assert trials.trial_count(ELITE_V3, path=scratch) == 1
    assert (real.read_bytes() if real.exists() else None) == before


# --- thesis spacing -------------------------------------------------------------

def _thesis_result(macd="BULLISH", volume_ratio=1.5, cs="NEUTRAL", target=0.0, price=100.0):
    return SimpleNamespace(
        technical=SimpleNamespace(macd_signal=macd, volume_ratio=volume_ratio, candlestick_bias=cs),
        market_intel=SimpleNamespace(target_mean=target), current_price=price)


def test_a_double_space_collapses_even_when_no_phrase_is_removed():
    out = ta._sanitize_thesis_text("iShares 20+ Year Treasury Bond  (TLT) offers duration.", _thesis_result())
    assert out == "iShares 20+ Year Treasury Bond (TLT) offers duration."


def test_a_removed_phrase_leaves_no_double_space():
    out = ta._sanitize_thesis_text("strong momentum  and a steady bid.",
                                   _thesis_result(macd="BEARISH", volume_ratio=0.5))
    assert "  " not in out and "strong momentum" not in out.lower()


def test_thesis_newlines_are_kept():
    out = ta._sanitize_thesis_text("First line.\n\nSecond   line.", _thesis_result())
    assert out == "First line.\n\nSecond line."


def test_publish_and_verify_carry_the_shadow_engine(ledger, tmp_path):
    from tracking.publish import publish, verify

    ledger.record(engine="elite", ticker="TLT", as_of=AS_OF, horizon_days=63, stance="BULLISH",
                  action="BUY", score=71.0, context={"strategy": ELITE_V2})
    record_shadow_calls(_decision_like(), {"key": ELITE_V3, "version": "v3", "score": 67.0,
                                           "band_action": "BUY", "status": "UNTESTED"},
                        horizons=[63], ledger=ledger)
    out = tmp_path / "track_record"
    assert publish(ledger, out_dir=out)["rows"] == 2
    res = verify(out, ledger=ledger)
    assert res.ok, res.problems
    csv_text = (out / "ledger.csv").read_text(encoding="utf-8")
    assert "elite_v3_shadow" in csv_text and ELITE_V3 in csv_text
