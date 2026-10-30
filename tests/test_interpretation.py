"""Grounded interpretation (core/engine/interpretation.py).

Runs on the real Decision payloads frozen in the golden fixtures, so the
sentences are checked against every label, blocker and hurdle shape the
engines actually emit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from engine.interpretation import build_interpretation, check_narrative, interpretation_pair  # noqa: E402

GOLDEN = sorted((ROOT / "tests" / "golden" / "expected").glob("*.json"))


def _decision(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return (d.get("result") or {}).get("decision")


DECISIONS = [(p.stem, _decision(p)) for p in GOLDEN if _decision(p)]


def test_fixtures_present():
    assert len(DECISIONS) >= 10


@pytest.mark.parametrize("name,decision", DECISIONS, ids=[n for n, _ in DECISIONS])
def test_label_and_evidence_repeated_verbatim(name, decision):
    interp = build_interpretation(decision)
    first = interp["sentences"][0]["text"]
    assert decision["label"] in first
    assert interp["label_verbatim"] == decision["label"]
    assert decision["evidence"]["status_text"] in first


@pytest.mark.parametrize("name,decision", DECISIONS, ids=[n for n, _ in DECISIONS])
def test_own_text_is_grounded(name, decision):
    interp, check = interpretation_pair(decision)
    assert check["grounded"], check["findings"]
    for s in interp["sentences"]:
        assert s["cites"], s


@pytest.mark.parametrize("name,decision", DECISIONS, ids=[n for n, _ in DECISIONS])
def test_blocker_and_remedy_are_quoted(name, decision):
    pb = decision.get("primary_blocker")
    text = build_interpretation(decision)["text"]
    if pb:
        assert pb["text"] in text
        assert pb["remedy"] in text


def test_unvalidated_model_always_carries_the_edge_caveat():
    _, d = DECISIONS[0]
    assert not d["evidence"]["permits_recommendation"]
    assert "No model here has a demonstrated out-of-sample edge" in build_interpretation(d)["text"]


def test_check_flags_invented_numbers_and_contradictions():
    _, d = next((n, x) for n, x in DECISIONS if x["levels"].get("entry"))
    entry = d["levels"]["entry"]
    good = f"Entry is {entry:,.2f}."
    assert check_narrative(good, d)["grounded"]

    bad = f"Entry is {entry:,.2f} and the stock should reach 99999.99 soon. Strong buy now."
    res = check_narrative(bad, d)
    kinds = {f["kind"] for f in res["findings"]}
    assert not res["grounded"]
    assert "unsupported_number" in kinds
    if "BUY" not in d["label"]:
        assert "contradicts_label" in kinds


def test_check_flags_edge_claims_for_unvalidated_models():
    _, d = DECISIONS[0]
    res = check_narrative("This model has a proven edge.", d)
    assert {f["kind"] for f in res["findings"]} == {"overstates_evidence"}


def test_derived_percentages_are_allowed():
    _, d = next((n, x) for n, x in DECISIONS
                if x["levels"].get("entry") and x["levels"].get("stop"))
    e, s = d["levels"]["entry"], d["levels"]["stop"]
    pct = (e - s) / e * 100
    assert check_narrative(f"The stop sits {pct:.1f}% below entry.", d)["grounded"]


def test_handles_sparse_payload():
    interp = build_interpretation({"ticker": "AAA"})
    assert interp["sentences"][0]["text"].startswith("QUANTA's call on AAA is NO VERDICT")
