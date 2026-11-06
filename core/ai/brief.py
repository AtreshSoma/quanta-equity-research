"""A grounded research brief: one Claude call that explains a Decision, checked before it is shown.

QUANTA's engine decides; this module only explains. It sends one finished
``Decision.to_dict()`` payload (plus the grounded interpretation sentences from
``engine.interpretation``) to Claude and asks for three short lists of sentences:

* ``brief``  what the call is and why, opening with the label and evidence status;
* ``bull``   the strongest case for the stock that the Decision's own fields support;
* ``bear``   the strongest case against it.

Every sentence must list the Decision fields (dot paths such as ``levels.stop``)
or source ids (``source:<id>``) it was written from. Before anything is shown:

1. every citation must resolve to a field that exists, or a source the Decision lists;
2. the brief must pass ``engine.interpretation.check_narrative`` in full: no number
   the Decision does not contain, no wording that contradicts the label, no claim of
   a proven edge;
3. the bull and bear lists must pass the same check except for directional wording,
   which a two-sided case needs; they still may not invent numbers or claim an edge;
4. the brief's first sentence must carry the label word for word.

A response that fails any check is withheld, with its findings, and the page keeps
showing the deterministic interpretation. The output schema has no field for a
rating, so the model has nowhere to put a second verdict.

Off by default. ``QUANTA_AI_BRIEF=1`` turns it on; credentials come from the
Anthropic SDK's own resolution (``ANTHROPIC_API_KEY`` and the rest). Results are
cached on disk by (model, Decision content), so a brief is paid for once.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from engine.interpretation import _get, build_interpretation, check_narrative

logger = logging.getLogger(__name__)

BRIEF_VERSION = "brief.v1"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 8000
TIMEOUT_SECONDS = 90.0
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_SOURCE = "source:"
# Fields that change on every build of the same call; left out of the cache key.
_VOLATILE = ("generated_at",)

SYSTEM_PROMPT = """You explain one stock decision made by QUANTA, a rules-based research engine. \
The decision is given as JSON. You do not make decisions and you do not change this one.

Write three short lists of plain-English sentences for an individual investor:
- "brief": 3 to 6 sentences on what the call is and why. The first sentence states the call's \
label and its evidence status, copied exactly as given in "label" and "evidence.status_text".
- "bull": 2 to 4 sentences making the strongest case for the stock that these fields support.
- "bear": 2 to 4 sentences making the strongest case against it.

Rules that a checker enforces after you answer; a response that breaks any of them is discarded:
- Every sentence lists in "cites" the JSON fields it was written from, as dot paths into the \
decision (for example "levels.stop", "primary_blocker.text", "factor_scores.quality"), or \
sources as "source:<id>" using ids from "sources". Cite only fields that exist.
- Use only numbers that appear in the decision, written as they appear or rounded to two decimals. \
Do not compute new figures such as percentages that are not in the JSON.
- Never suggest buying, selling or holding beyond what "label" says, and never call the model's \
signal proven, validated or reliable unless "evidence.permits_recommendation" is true.
- If the decision has no information for a list, write one sentence saying so and cite "label".
- No headings, lists or markdown inside sentences."""

_SENTENCES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "cites": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "cites"],
        "additionalProperties": False,
    },
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"brief": _SENTENCES, "bull": _SENTENCES, "bear": _SENTENCES},
    "required": ["brief", "bull", "bear"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ settings
def enabled() -> bool:
    return os.environ.get("QUANTA_AI_BRIEF") == "1"


def model_id() -> str:
    return os.environ.get("QUANTA_AI_MODEL") or DEFAULT_MODEL


def effort() -> str:
    value = os.environ.get("QUANTA_AI_EFFORT") or DEFAULT_EFFORT
    return value if value in ("low", "medium", "high", "xhigh", "max") else DEFAULT_EFFORT


def status() -> Dict[str, Any]:
    """Whether the brief can run here, without making any request."""
    if not enabled():
        return {"available": False, "reason": "Off. Set QUANTA_AI_BRIEF=1 to turn it on."}
    try:
        import anthropic  # noqa: F401 - availability check
    except ImportError:
        return {"available": False, "reason": "The anthropic package is not installed (pip install -e \".[ai]\")."}
    return {"available": True, "model": model_id(), "effort": effort()}


# ------------------------------------------------------------------ checking
def _cite_ok(cite: str, decision: Dict[str, Any], source_ids: set) -> bool:
    cite = str(cite or "").strip()
    if cite.startswith(_SOURCE):
        return cite[len(_SOURCE):] in source_ids
    return bool(cite) and _get(decision, cite) is not None


def verify(output: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """Every check the brief must pass before it is shown. Pure; never raises on bad input."""
    findings: List[Dict[str, Any]] = []
    source_ids = {str(s.get("id")) for s in decision.get("sources") or [] if isinstance(s, dict)}
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for key in ("brief", "bull", "bear"):
        items = output.get(key) if isinstance(output, dict) else None
        if not isinstance(items, list) or not items:
            findings.append({"kind": "missing_section", "section": key,
                             "message": f"The {key} section is missing or empty."})
            items = []
        clean = []
        for i, item in enumerate(items):
            text = str((item or {}).get("text") or "").strip()
            cites = [str(c) for c in (item or {}).get("cites") or []]
            if not text:
                continue
            if not cites:
                findings.append({"kind": "uncited", "section": key, "sentence": i,
                                 "message": f"{key} sentence {i + 1} cites nothing."})
            for c in cites:
                if not _cite_ok(c, decision, source_ids):
                    findings.append({"kind": "unknown_cite", "section": key, "sentence": i, "value": c,
                                     "message": f"{key} sentence {i + 1} cites {c}, which the Decision does not have."})
            clean.append({"text": text, "cites": cites})
        sections[key] = clean

    brief_text = " ".join(s["text"] for s in sections["brief"])
    for f in check_narrative(brief_text, decision)["findings"]:
        findings.append(dict(f, section="brief"))
    for key in ("bull", "bear"):
        text = " ".join(s["text"] for s in sections[key])
        for f in check_narrative(text, decision)["findings"]:
            if f["kind"] != "contradicts_label":    # a two-sided case argues both ways
                findings.append(dict(f, section=key))
    label = str(decision.get("label") or "").strip()
    first = sections["brief"][0]["text"] if sections["brief"] else ""
    if label and label.lower() not in first.lower():
        findings.append({"kind": "label_missing", "section": "brief",
                         "message": f"The first sentence does not state the label {label}."})
    return {"grounded": not findings, "findings": findings, "sections": sections}


# ------------------------------------------------------------------- caching
def _cache_dir() -> Path:
    root = os.environ.get("QUANTA_CACHE_DIR") or str(Path(__file__).resolve().parents[2] / "data" / "cache")
    return Path(root) / "ai_briefs"


def cache_key(decision: Dict[str, Any], model: str) -> str:
    stable = {k: v for k, v in decision.items() if k not in _VOLATILE}
    text = json.dumps(stable, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{BRIEF_VERSION}\0{model}\0{text}".encode("utf-8")).hexdigest()


def _cached(key: str) -> Optional[Dict[str, Any]]:
    path = _cache_dir() / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def _store(key: str, payload: Dict[str, Any]) -> None:
    folder = _cache_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{key}.tmp"
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, folder / f"{key}.json")
    except OSError as exc:
        logger.warning("brief cache not written: %s", exc)


# -------------------------------------------------------------------- calling
def _default_client():
    import anthropic
    return anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=2)


def _request(client: Any, decision: Dict[str, Any], interpretation: Dict[str, Any], model: str) -> Any:
    payload = json.dumps({"decision": decision, "grounded_sentences": interpretation.get("sentences")},
                         sort_keys=True, default=str)
    return client.beta.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        betas=[FALLBACK_BETA],
        fallbacks="default",                 # a declined request is retried server-side
        # Static instructions first and marked for caching; the per-call JSON comes after.
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": effort(),
                       "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": f"Explain this decision.\n\n{payload}"}],
    )


def _usage(response: Any) -> Dict[str, Any]:
    u = getattr(response, "usage", None)
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {k: getattr(u, k, None) for k in keys} if u is not None else {}


def write_brief(decision: Dict[str, Any], client_factory: Optional[Callable[[], Any]] = None,
                use_cache: bool = True) -> Dict[str, Any]:
    """The checked brief for ``decision`` (a ``Decision.to_dict()`` payload). Never raises.

    ``status`` is one of: ``ok`` (shown), ``rejected`` (written but failed a check;
    ``findings`` says why), ``refused`` (the model declined), ``unavailable``
    (off, not installed, no credentials, or the request failed).
    """
    st = status() if client_factory is None else {"available": True}
    if not st.get("available"):
        return {"status": "unavailable", "reason": st.get("reason")}
    if not isinstance(decision, dict) or not decision.get("ticker") or not decision.get("label"):
        return {"status": "unavailable", "reason": "No finished Decision to explain."}
    model = model_id()
    key = cache_key(decision, model)
    if use_cache:
        hit = _cached(key)
        if hit is not None:
            return dict(hit, from_cache=True)
    interpretation = build_interpretation(decision)
    started = time.time()
    try:
        client = (client_factory or _default_client)()
        response = _request(client, decision, interpretation, model)
    except Exception as exc:  # noqa: BLE001 - the page keeps the deterministic interpretation
        name = type(exc).__name__
        logger.warning("research brief request failed: %s", name)
        return {"status": "unavailable", "reason": f"The request failed ({name})."}
    base = {"version": BRIEF_VERSION, "ticker": decision.get("ticker"), "label": decision.get("label"),
            "model": getattr(response, "model", model), "requested_model": model,
            "seconds": round(time.time() - started, 2), "usage": _usage(response), "from_cache": False}
    if getattr(response, "stop_reason", None) == "refusal":
        return dict(base, status="refused", reason="The model declined to write this brief.")
    text = next((b.text for b in getattr(response, "content", []) or [] if getattr(b, "type", "") == "text"), "")
    try:
        output = json.loads(text)
    except ValueError:
        return dict(base, status="rejected", findings=[{"kind": "not_json", "message": "The response was not valid JSON."}])
    checked = verify(output, decision)
    if not checked["grounded"]:
        logger.info("research brief withheld for %s: %d findings", decision.get("ticker"), len(checked["findings"]))
        return dict(base, status="rejected", findings=checked["findings"])
    result = dict(base, status="ok", **checked["sections"])
    if use_cache:
        _store(key, result)
    return result
