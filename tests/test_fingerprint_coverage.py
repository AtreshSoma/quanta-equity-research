"""P0-1..P0-4: a call's fingerprints cover all the code behind it, the model
fingerprint ignores edits that cannot change a call, and every recorded call
carries the inputs it read.

Audit 2026-11-06 (docs/competitive-analysis/02-CODEBASE-AUDIT.md, F-1..F-3):
the v4 shadow's fingerprint hashed 43 files and missed TradingAnalysis_ELITE.py;
no fingerprint covered modules/; and 12 publication dates produced 23 code
fingerprints because any comment edit restarted the evidence clock.
"""
from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

import pytest

from engine.decision import Decision, Horizon
from engine.evidence import ELITE, get_evidence
from engine.types import FinalAction
from tracking import fingerprint as fpm
from tracking.inputs import capture, inputs_hash
from tracking.ledger import SignalLedger, record_shadow_calls, shadow_engine
from tracking.publish import CALL_FIELDS, _digest, call_hash, publish, verify

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"


def _names(engine):
    return {p.relative_to(ROOT).as_posix() for p in fpm._sources(engine, True)}


# --------------------------------------------------------------- coverage
@pytest.mark.parametrize("version", ["v2", "v3", "v4", "v9"])
def test_every_research_shadow_version_hashes_the_research_engine(version):
    names = _names(shadow_engine(version))
    assert "core/TradingAnalysis_ELITE.py" in names
    assert names == _names("elite")


def test_v4_shadow_covers_its_revisions_leg():
    assert "modules/earnings_revision_scoring.py" in _names("elite_v4_shadow")


def test_optional_and_plain_imports_of_modules_are_hashed():
    names = _names("elite")
    for mod in ("catalyst_scanner", "compliance_manager", "portfolio_risk_manager",
                "sec_edgar_parser", "polygon_provider"):
        assert f"modules/{mod}.py" in names


def test_script_only_imports_are_not_dependencies():
    # engine/index_lists.py imports SP_ELITE_PRO only under ``if __name__ == "__main__"``.
    assert "core/SP_ELITE_PRO.py" not in _names("elite")
    assert "core/SP_ELITE_PRO.py" in _names("elite_pro")


def test_a_modules_edit_changes_both_fingerprints(tmp_path, monkeypatch):
    target = ROOT / "modules" / "earnings_revision_scoring.py"
    before = (fpm._compute("elite_v4_shadow"), fpm._compute("elite_v4_shadow", model=True))
    monkeypatch.setitem(fpm._BYTES, target, fpm._read(target) + b"\nREVISION_SHIFT = 1\n")
    fpm.model_source.cache_clear()
    try:
        after = (fpm._compute("elite_v4_shadow"), fpm._compute("elite_v4_shadow", model=True))
    finally:
        fpm.model_source.cache_clear()
    assert after[0] != before[0] and after[1] != before[1]


def test_a_missing_optional_module_changes_the_fingerprint(monkeypatch):
    base_code, base_model = fpm.code_fingerprint("elite"), fpm.model_fingerprint("elite")
    present = fpm.optional_modules("elite")
    assert present, "the Research engine has optional imports"
    gone = tuple((n, False if n == "earnings_revision_scoring" else ok) for n, ok in present)
    monkeypatch.setattr(fpm, "optional_modules", lambda engine: gone)
    assert fpm.code_fingerprint("elite") != base_code
    assert fpm.model_fingerprint("elite") != base_model
    assert "earnings_revision_scoring" in fpm.provenance("elite")["optional_missing"]


# ------------------------------------------------------- model fingerprint
def _model(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    fpm.model_source.cache_clear()
    fpm._BYTES.pop(path, None)
    return fpm.model_source(path)


BASE = '''
import logging
logger = logging.getLogger(__name__)
WEIGHT = 0.40


def score(x):
    """Weighted score."""
    logger.info("scoring %s", x)
    return WEIGHT * x
'''


def test_comments_docstrings_logging_and_formatting_do_not_change_the_model(tmp_path):
    base = _model(tmp_path, "a.py", BASE)
    edited = BASE.replace('"""Weighted score."""', '"""A different docstring."""')
    edited = edited.replace('logger.info("scoring %s", x)', 'logger.warning("now %s", x)  # note')
    edited = edited.replace("return WEIGHT * x", "return (WEIGHT\n            * x)  # same")
    assert _model(tmp_path, "b.py", edited) == base


def test_a_weight_or_logic_change_changes_the_model(tmp_path):
    base = _model(tmp_path, "a.py", BASE)
    assert _model(tmp_path, "b.py", BASE.replace("0.40", "0.41")) != base
    assert _model(tmp_path, "c.py", BASE.replace("WEIGHT * x", "WEIGHT * x + 1")) != base


def test_presentation_only_functions_exist_and_nothing_else_calls_them():
    """Leaving a function out of the model hash is safe only if no decision code calls it."""
    for filename, names in fpm.PRESENTATION_ONLY.items():
        tree = ast.parse((CORE / filename).read_bytes())
        defined = {n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        assert names <= defined, f"{filename}: {sorted(names - defined)} no longer exist"
        for node in tree.body:
            if getattr(node, "name", None) in names or fpm._is_main_guard(node):
                continue  # excluded code, or script-only code under ``__main__``
            for sub in fpm._walk_runtime(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    assert sub.id not in names, (
                        f"{filename}: {getattr(node, 'name', type(node).__name__)} calls "
                        f"presentation-only {sub.id}; remove it from PRESENTATION_ONLY")


def test_model_fingerprint_is_stable_and_distinct_from_code_fingerprint():
    m = fpm.model_fingerprint("elite")
    assert len(m) == 64 and m == fpm.model_fingerprint("elite")
    assert m != fpm.code_fingerprint("elite")
    assert fpm.model_fingerprint("elite") != fpm.model_fingerprint("shortterm")


# ------------------------------------------------------ ledger and publish
def _decision(ticker="AAPL", as_of="2026-09-18"):
    return Decision(ticker=ticker, engine="elite", horizon=Horizon("position", 63, "63 sessions"),
                    action=FinalAction.BUY, evidence=get_evidence(ELITE), as_of=as_of, score=71.0,
                    reference_price=200.0, entry=200.0, stop=190.0, target=230.0,
                    valuation_target=260.0, factor_scores={"trend": 64.0})


class _Intel:
    target_mean, analyst_count, analyst_rating = 245.0, 31, "BUY"
    revision = {"revision_score": 7.0, "sentiment": "POSITIVE"}


class _Result:
    market_intel = _Intel()
    valuation = sentiment = insider = None


@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(tmp_path / "l.sqlite3")


def test_each_call_records_model_fingerprint_provenance_and_inputs(ledger):
    inputs = capture(_decision(), _Result())
    ledger.record_decision(_decision(), inputs=inputs)
    row = ledger.rows()[0]
    assert row["model_fingerprint"] == fpm.model_fingerprint("elite")
    assert "core/TradingAnalysis_ELITE.py" in json.loads(row["provenance_json"])["modules"]
    stored = json.loads(row["inputs_json"])
    assert stored["levels"]["valuation_target"] == 260.0
    assert stored["analyst"]["target_mean"] == 245.0 and stored["analyst"]["analyst_count"] == 31
    assert stored["revision"]["revision_score"] == 7.0
    assert row["inputs_hash"] == inputs_hash(inputs)


def test_decision_inputs_are_captured_without_an_analysis(ledger):
    ledger.record_decision(_decision())
    stored = json.loads(ledger.rows()[0]["inputs_json"])
    assert stored["levels"]["target"] == 230.0 and "analyst" not in stored


def test_shadow_calls_carry_the_live_calls_inputs(ledger):
    inputs = capture(_decision(), _Result())
    record_shadow_calls(_decision(), {"key": "elite.research.v4", "version": "v4", "score": 66.0,
                                      "band_action": "BUY", "status": "UNTESTED"},
                        horizons=[63], ledger=ledger, inputs=inputs)
    row = ledger.rows(engine="elite_v4_shadow")[0]
    assert row["inputs_hash"] == inputs_hash(inputs)
    assert row["model_fingerprint"] == fpm.model_fingerprint("elite_v4_shadow")


def test_published_rows_fix_model_fingerprint_and_inputs_and_verify(ledger, tmp_path):
    ledger.record_decision(_decision(), inputs=capture(_decision(), _Result()))
    out = tmp_path / "track"
    publish(ledger, out)
    row = next(csv.DictReader((out / "ledger.csv").open()))
    assert row["model_fingerprint"] and row["inputs_hash"]
    assert verify(out, ledger).ok
    # Editing the recorded inputs after publication is detected.
    with ledger._connect() as con:
        con.execute("UPDATE signals SET inputs_hash='0'")
    assert not verify(out, ledger).ok


def test_legacy_rows_keep_their_original_hash():
    legacy = {k: "x" for k in CALL_FIELDS}
    assert call_hash(legacy) == _digest(legacy, CALL_FIELDS)
    assert call_hash(dict(legacy, model_fingerprint="", inputs_hash="")) == _digest(legacy, CALL_FIELDS)


def test_the_committed_track_record_still_verifies():
    assert verify(ROOT / "track_record").ok
