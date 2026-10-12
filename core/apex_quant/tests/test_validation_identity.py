"""A validation artifact only authorises the thing it actually validated.

Audit section 12 (Validation drift): `is_current` checked `passed` and age and
nothing else. A passing artifact therefore kept authorising position sizing
after the strategy parameters changed, after the universe changed, after the
cost model changed, and — most sharply — after the execution simulator that
produced its numbers was corrected (F01). Approval must be bound to the
identity of what was validated.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.apex_quant import validation


def _now_iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


IDENTITY = {
    "params": {"atr_stop_mult": 2.0, "atr_target_mult": 4.0},
    "universe": ["AAPL", "MSFT"],
    "costs": {"half_spread_bps": 3.0},
    "execution_semantics": validation.EXECUTION_SEMANTICS_VERSION,
}


def _artifact(**over):
    art = {
        "generated_utc": _now_iso(1),
        "passed": True,
        "strategy_identity": validation.strategy_identity(**IDENTITY),
        "execution_semantics": validation.EXECUTION_SEMANTICS_VERSION,
        "coverage_complete": True,
        "code_hash": validation.implementation_hash(),
    }
    art.update(over)
    return art


# ---------------------------------------------------------------------------
# The identity fingerprint
# ---------------------------------------------------------------------------

def test_identity_is_stable_for_the_same_configuration():
    assert validation.strategy_identity(**IDENTITY) == validation.strategy_identity(**IDENTITY)


def test_identity_is_independent_of_dict_ordering():
    a = validation.strategy_identity(
        params={"b": 2, "a": 1}, universe=["X", "Y"], costs={}, execution_semantics="v1")
    b = validation.strategy_identity(
        params={"a": 1, "b": 2}, universe=["X", "Y"], costs={}, execution_semantics="v1")
    assert a == b


@pytest.mark.parametrize("field,value", [
    ("params", {"atr_stop_mult": 1.5, "atr_target_mult": 4.0}),
    ("universe", ["AAPL", "MSFT", "NVDA"]),
    ("costs", {"half_spread_bps": 10.0}),
    ("execution_semantics", "some-other-version"),
])
def test_identity_changes_when_any_component_changes(field, value):
    changed = dict(IDENTITY)
    changed[field] = value
    assert validation.strategy_identity(**changed) != validation.strategy_identity(**IDENTITY)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_a_matching_current_passing_artifact_permits_sizing():
    assert validation.is_current(_artifact(), current_identity=IDENTITY) is True


def test_a_failing_artifact_never_permits_sizing():
    assert validation.is_current(_artifact(passed=False), current_identity=IDENTITY) is False


def test_explicit_identity_cannot_bypass_coverage_or_code():
    assert not validation.is_current(_artifact(coverage_complete=False), current_identity=IDENTITY)
    assert not validation.is_current(_artifact(code_hash="old code"), current_identity=IDENTITY)


def test_a_stale_artifact_never_permits_sizing():
    art = _artifact(generated_utc=_now_iso(400))
    assert validation.is_current(art, current_identity=IDENTITY) is False


def test_a_changed_strategy_invalidates_a_passing_artifact():
    changed = dict(IDENTITY, params={"atr_stop_mult": 1.0})
    assert validation.is_current(_artifact(), current_identity=changed) is False


def test_an_artifact_from_the_old_execution_model_is_rejected():
    """F01 changed how every trade was filled; prior results cannot authorise."""
    stale_semantics = _artifact(
        strategy_identity=validation.strategy_identity(
            **dict(IDENTITY, execution_semantics="pre-f01")),
        execution_semantics="pre-f01",
    )
    assert validation.is_current(stale_semantics, current_identity=IDENTITY) is False


def test_an_artifact_with_no_identity_is_rejected():
    """Artifacts predating the identity field cannot prove what they validated."""
    art = _artifact()
    art.pop("strategy_identity")
    assert validation.is_current(art, current_identity=IDENTITY) is False


def test_omitting_current_identity_requires_complete_recorded_inputs():
    assert validation.is_current(_artifact()) is False
    assert validation.is_current(_artifact(passed=False)) is False


def test_recorded_inputs_are_bound_to_code_and_coverage():
    art = _artifact(validation_inputs=IDENTITY,
                    code_hash=validation.implementation_hash(), coverage_complete=True)
    assert validation.is_current(art)
    assert not validation.is_current(dict(art, code_hash="previous implementation"))
    assert not validation.is_current(dict(art, coverage_complete=False))


@pytest.mark.parametrize("timestamp", ["2026-01-01", "invalid", None, _now_iso(-1)])
def test_invalid_or_future_dates_refuse_without_crashing(timestamp):
    art = _artifact(generated_utc=timestamp)
    assert not validation.is_current(art, current_identity=IDENTITY)
    assert "REFUSED" in validation.sizing_gate_message(art, current_identity=IDENTITY)


# ---------------------------------------------------------------------------
# The refusal message says which check failed
# ---------------------------------------------------------------------------

def test_gate_message_names_a_configuration_mismatch():
    changed = dict(IDENTITY, params={"atr_stop_mult": 1.0})
    msg = validation.sizing_gate_message(_artifact(), current_identity=changed)

    assert "REFUSED" in msg
    assert "configuration" in msg.lower() or "changed" in msg.lower()


def test_gate_message_distinguishes_stale_from_mismatched():
    stale = validation.sizing_gate_message(
        _artifact(generated_utc=_now_iso(400)), current_identity=IDENTITY)
    assert "stale" in stale.lower()


def test_artifact_round_trips_with_its_identity(tmp_path):
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_artifact()), encoding="utf-8")

    loaded = validation.load_artifact(path)
    assert validation.is_current(loaded, current_identity=IDENTITY) is True
