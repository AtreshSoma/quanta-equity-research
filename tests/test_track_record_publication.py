"""A5/B3: every call carries its code fingerprint and Decision snapshot, and the
published record is hash-chained so a rewritten forecast is detectable."""
from __future__ import annotations

import csv
import json
from datetime import date

import pytest

from engine.decision import Decision, Horizon
from engine.evidence import ELITE, get_evidence
from engine.types import FinalAction
from tracking.fingerprint import code_fingerprint
from tracking.ledger import SignalLedger
from tracking.publish import publish, verify


@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(tmp_path / "l.sqlite3")


def _decision(ticker="AAPL", as_of="2026-09-18"):
    return Decision(ticker=ticker, engine="elite", horizon=Horizon("position", 63, "63 sessions"),
                    action=FinalAction.BUY, evidence=get_evidence(ELITE), as_of=as_of, score=71.0,
                    reference_price=200.0, entry=200.0, stop=190.0, target=230.0,
                    factor_scores={"trend": 64.0})


def test_fingerprints_are_per_engine_and_stable():
    assert code_fingerprint("elite") == code_fingerprint("elite")
    assert code_fingerprint("elite") != code_fingerprint("shortterm")
    assert len(code_fingerprint("unknown")) == 64  # shared decision core only


def test_each_call_stores_fingerprint_and_its_decision_once(ledger):
    first = ledger.record_decision(_decision())
    row = ledger.rows()[0]
    assert row["code_fingerprint"] == code_fingerprint("elite")
    snap = ledger.snapshot(first)
    assert snap["ticker"] == "AAPL" and snap["levels"]["stop"] == 190.0
    # A repeat for the same session returns the original row and keeps its snapshot.
    later = _decision()
    later.stop = 150.0
    assert ledger.record_decision(later) == first
    assert ledger.snapshot(first)["levels"]["stop"] == 190.0


def test_calls_without_a_decision_have_no_snapshot(ledger):
    rid = ledger.record(engine="t", ticker="X", as_of=date(2026, 1, 5), horizon_days=5,
                        stance="BULLISH", action="BUY")
    assert ledger.snapshot(rid) is None


def test_publish_chains_only_real_changes_and_verifies(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    one = publish(ledger, out)
    assert one["changed"] and publish(ledger, out)["changed"] is False
    ledger.record_decision(_decision("MSFT"))
    two = publish(ledger, out)
    assert two["changed"] and two["rows"] == 2
    links = [json.loads(x) for x in (out / "chain.jsonl").read_text().splitlines()]
    assert len(links) == 2 and links[1]["prev"] == links[0]["hash"]
    res = verify(out, ledger)
    assert res.ok, res.problems


def test_editing_a_published_forecast_is_detected(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision())
    publish(ledger, out)
    path = out / "ledger.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["target_price"] = "999.0"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    problems = " ".join(verify(out).problems)
    assert "differs from the last published link" in problems and "edited after publication" in problems


def test_an_edited_chain_link_is_detected(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    publish(ledger, out)
    ledger.record_decision(_decision("MSFT"))
    publish(ledger, out)
    lines = (out / "chain.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["rows"] = 50
    lines[0] = json.dumps(first, sort_keys=True)
    (out / "chain.jsonl").write_text("\n".join(lines) + "\n")
    assert any("link 1 was edited" in p for p in verify(out).problems)


def _resolve_first(ledger, outcome="LOSS"):
    with ledger._connect() as con:
        con.execute("UPDATE signals SET outcome=?, return_pct=-5.0, excess_return_pct=-6.0, "
                    "resolved_at='2026-12-20T00:00:00+00:00' WHERE id=(SELECT MIN(id) FROM signals)",
                    (outcome,))


def test_a_deleted_call_is_detected_even_after_republishing(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    ledger.record_decision(_decision("MSFT"))
    publish(ledger, out)
    with ledger._connect() as con:
        con.execute("DELETE FROM signals WHERE ticker='AAPL'")
    publish(ledger, out)
    assert any("published and later removed" in p for p in verify(out, ledger).problems)


def test_a_rewritten_outcome_is_detected_even_after_republishing(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    _resolve_first(ledger, "LOSS")
    publish(ledger, out)
    _resolve_first(ledger, "WIN")
    publish(ledger, out)
    assert any("outcome changed after it was first published" in p
               for p in verify(out, ledger).problems)


def test_a_truncated_call_log_is_detected(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    publish(ledger, out)
    (out / "calls.jsonl").write_text("")
    assert any("shorter than when it was published" in p for p in verify(out).problems)


def test_a_garbled_record_fails_instead_of_crashing(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    publish(ledger, out)
    with (out / "chain.jsonl").open("a") as fh:
        fh.write("{not json\n")
    res = verify(out)
    assert not res.ok and any("not valid JSON" in p for p in res.problems)


def test_publish_always_rewrites_a_hand_edited_csv(ledger, tmp_path):
    out = tmp_path / "record"
    ledger.record_decision(_decision("AAPL"))
    publish(ledger, out)
    good = (out / "ledger.csv").read_text()
    (out / "ledger.csv").write_text(good.replace("230.0", "999.0"))
    publish(ledger, out)
    assert (out / "ledger.csv").read_text() == good and verify(out, ledger).ok
