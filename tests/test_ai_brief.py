"""P1-5 / P2-1: the grounded AI research brief (core/ai/brief.py).

No network and no API key: a fake client stands in for the Anthropic SDK. What
is pinned is the contract around the model: what is sent, and the checks a
response must pass before anything reaches the page.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ai import brief
from engine.decision import Decision, Horizon, SourceRef
from engine.evidence import ELITE, get_evidence
from engine.types import FinalAction


def _decision(action=FinalAction.WATCHLIST):
    d = Decision(ticker="AAPL", engine="elite", horizon=Horizon("position", 63, "Position (~3 months)"),
                 action=action, evidence=get_evidence(ELITE), as_of="2026-11-05", score=58.2,
                 reference_price=200.0, entry=200.0, stop=190.0, target=230.0, reward_risk=1.4,
                 factor_scores={"quality": 71.0, "technical": 44.0},
                 sources=[SourceRef(id="px", kind="price", provider="yfinance", as_of="2026-11-05")])
    return d.to_dict()


def _sentence(text, *cites):
    return {"text": text, "cites": list(cites) or ["label"]}


def _good(dec):
    return {
        "brief": [_sentence(f"QUANTA's call on AAPL is {dec['label']}. Model evidence: "
                            f"{dec['evidence']['status_text']}.", "label", "evidence.status_text"),
                  _sentence("The stop sits at 190.00 against an entry of 200.00.", "levels.stop", "levels.entry")],
        "bull": [_sentence("Quality scores 71.0, the strongest leg.", "factor_scores.quality"),
                 _sentence("The target is 230.00 and the upside looks strong.", "levels.target")],
        "bear": [_sentence("The technical leg is weak at 44.0.", "factor_scores.technical"),
                 _sentence("Prices come from yfinance.", "source:px")],
    }


class _Client:
    def __init__(self, output=None, stop_reason="end_turn", raw=None):
        self.calls = []
        text = raw if raw is not None else json.dumps(output)
        self.response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason,
            model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=1200, output_tokens=300,
                                                         cache_read_input_tokens=0,
                                                         cache_creation_input_tokens=0))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTA_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("QUANTA_AI_MODEL", raising=False)
    monkeypatch.delenv("QUANTA_AI_EFFORT", raising=False)


def test_a_grounded_response_is_shown_with_its_sections():
    dec = _decision()
    client = _Client(_good(dec))
    out = brief.write_brief(dec, client_factory=lambda: client)
    assert out["status"] == "ok", out.get("findings")
    assert out["brief"][0]["text"].startswith("QUANTA's call on AAPL is WATCHLIST")
    assert len(out["bull"]) == 2 and len(out["bear"]) == 2
    assert out["usage"]["input_tokens"] == 1200


def test_the_request_uses_fallbacks_a_schema_and_a_cached_system_prompt():
    dec = _decision()
    client = _Client(_good(dec))
    brief.write_brief(dec, client_factory=lambda: client)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["schema"] == brief.OUTPUT_SCHEMA
    assert call["output_config"]["effort"] == "medium"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in call and "temperature" not in call
    # The schema has no slot for a rating: the model cannot issue a second verdict.
    assert set(brief.OUTPUT_SCHEMA["properties"]) == {"brief", "bull", "bear"}
    sent = json.loads(call["messages"][0]["content"].split("\n\n", 1)[1])
    assert sent["decision"]["ticker"] == "AAPL" and sent["grounded_sentences"]


def test_an_invented_number_withholds_the_brief():
    dec = _decision()
    bad = _good(dec)
    bad["brief"][1]["text"] = "The stop sits at 187.40 against an entry of 200.00."
    out = brief.write_brief(dec, client_factory=lambda: _Client(bad))
    assert out["status"] == "rejected"
    assert any(f["kind"] == "unsupported_number" and f["section"] == "brief" for f in out["findings"])
    assert "brief" not in out


def test_an_invented_number_in_the_bull_case_is_also_caught():
    dec = _decision()
    bad = _good(dec)
    bad["bull"][0]["text"] = "Quality scores 93.5, the strongest leg."
    out = brief.write_brief(dec, client_factory=lambda: _Client(bad))
    assert out["status"] == "rejected" and out["findings"][0]["section"] == "bull"


def test_bullish_wording_is_allowed_in_the_bull_case_but_not_in_the_brief():
    dec = _decision()
    assert brief.verify(_good(dec), dec)["grounded"]          # "strong" in bull is fine
    bad = _good(dec)
    bad["brief"].append(_sentence("This is a strong buy.", "label"))
    kinds = {f["kind"] for f in brief.verify(bad, dec)["findings"]}
    assert "contradicts_label" in kinds


def test_citations_must_resolve():
    dec = _decision()
    bad = _good(dec)
    bad["bear"][0]["cites"] = ["factor_scores.momentum"]
    bad["bear"][1]["cites"] = ["source:news-9"]
    bad["bull"][0]["cites"] = []
    kinds = [f["kind"] for f in brief.verify(bad, dec)["findings"]]
    assert kinds.count("unknown_cite") == 2 and "uncited" in kinds


def test_the_first_sentence_must_carry_the_label_and_an_edge_claim_is_rejected():
    dec = _decision()
    bad = _good(dec)
    bad["brief"][0]["text"] = "Here is the summary."
    bad["bear"].append(_sentence("The model has a proven edge.", "evidence.status"))
    kinds = {f["kind"] for f in brief.verify(bad, dec)["findings"]}
    assert {"label_missing", "overstates_evidence"} <= kinds


def test_refusal_and_bad_json_are_reported_not_shown():
    dec = _decision()
    refused = brief.write_brief(dec, client_factory=lambda: _Client(_good(dec), stop_reason="refusal"))
    assert refused["status"] == "refused"
    garbled = brief.write_brief(dec, client_factory=lambda: _Client(raw="not json"), use_cache=False)
    assert garbled["status"] == "rejected" and garbled["findings"][0]["kind"] == "not_json"


def test_a_request_failure_is_unavailable_and_never_raises():
    def boom():
        raise RuntimeError("no credentials")
    out = brief.write_brief(_decision(), client_factory=boom)
    assert out == {"status": "unavailable", "reason": "The request failed (RuntimeError)."}


def test_a_brief_is_paid_for_once_per_decision():
    dec = _decision()
    client = _Client(_good(dec))
    first = brief.write_brief(dec, client_factory=lambda: client)
    again = brief.write_brief(dict(dec, generated_at="2026-11-06T00:00:00Z"), client_factory=lambda: client)
    assert len(client.calls) == 1 and again["from_cache"] and again["brief"] == first["brief"]
    changed = dict(dec, score=61.0)
    brief.write_brief(changed, client_factory=lambda: client)
    assert len(client.calls) == 2


def test_rejected_briefs_are_not_cached():
    dec = _decision()
    bad = _good(dec)
    bad["brief"][1]["text"] = "Stop 187.40."
    client = _Client(bad)
    brief.write_brief(dec, client_factory=lambda: client)
    brief.write_brief(dec, client_factory=lambda: client)
    assert len(client.calls) == 2


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("QUANTA_AI_BRIEF", raising=False)
    assert brief.status()["available"] is False
    assert brief.write_brief(_decision())["status"] == "unavailable"


def test_the_api_routes(monkeypatch):
    import app as webapp
    monkeypatch.delenv("QUANTA_AI_BRIEF", raising=False)
    client = webapp.app.test_client()
    assert client.get("/api/research/brief/status").get_json()["available"] is False
    assert client.post("/api/research/brief", json={}).status_code == 403   # the CSRF guard holds
    same_origin = {"X-QUANTA-Request": "1"}
    assert client.post("/api/research/brief", json={}, headers=same_origin).status_code == 400
    body = client.post("/api/research/brief", json={"decision": _decision()}, headers=same_origin).get_json()
    assert body["status"] == "unavailable" and "QUANTA_AI_BRIEF" in body["reason"]
