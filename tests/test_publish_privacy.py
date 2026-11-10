"""The public track record never says what the account holds.

A published row "HOLD (owned)" for a ticker, or any call made while the
account's holdings were known, discloses a position. Those calls stay in the
local ledger (and its statistics) and are left out of track_record/.
"""
from __future__ import annotations

import csv
import json

import pytest

from engine.decision import Decision, Horizon
from engine.evidence import ELITE, get_evidence
from engine.types import FinalAction
from tracking.ledger import SignalLedger, record_shadow_calls
from tracking.publish import is_private, publish, verify


@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(tmp_path / "l.sqlite3")


def _decision(ticker, action=FinalAction.WATCHLIST, held=False):
    d = Decision(ticker=ticker, engine="elite", horizon=Horizon("position", 63, "63 sessions"),
                 action=action, evidence=get_evidence(ELITE), as_of="2026-09-18", score=55.0,
                 reference_price=100.0)
    d.holdings_known = held
    return d


def test_holder_calls_are_recorded_locally_but_not_published(ledger, tmp_path):
    ledger.record_decision(_decision("PUB"))
    ledger.record_decision(_decision("HELD", held=True))
    ledger.record_decision(_decision("OWNED", action=FinalAction.HOLD_EXISTING))
    assert {r["ticker"] for r in ledger.rows()} == {"PUB", "HELD", "OWNED"}
    out = tmp_path / "track"
    publish(ledger, out)
    published = {r["ticker"] for r in csv.DictReader((out / "ledger.csv").open())}
    assert published == {"PUB"}
    calls = (out / "calls.jsonl").read_text()
    assert "HELD" not in calls and "OWNED" not in calls
    assert verify(out, ledger).ok


def test_shadow_calls_of_a_held_name_are_private_too(ledger):
    d = _decision("HELD", held=True)
    record_shadow_calls(d, {"key": "elite.research.v3", "version": "v3", "score": 60.0,
                            "band_action": "ACCUMULATE", "status": "UNTESTED"}, horizons=[63], ledger=ledger)
    row = ledger.rows(engine="elite_v3_shadow")[0]
    assert json.loads(row["context_json"])["holdings_known"] is True and is_private(row)


def test_calls_without_holdings_keep_their_context_unchanged(ledger):
    ledger.record_decision(_decision("PUB"))
    assert "holdings_known" not in json.loads(ledger.rows()[0]["context_json"])


def test_the_committed_record_holds_no_owned_call():
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "track_record" / "ledger.csv"
    rows = list(csv.DictReader(path.open())) if path.exists() else []
    assert rows, "the published record is present"
    assert not [r["signal_key"] for r in rows if r.get("action") == "HOLD_EXISTING"]
