"""Grounded interpretation: plain sentences that cite the Decision they describe.

``Decision.interpretation`` was reserved for "a grounded LLM layer" and never
filled. This module fills it without a language model, and gives any model that
is used (Claude through the MCP server, for instance) two guard rails:

* :func:`build_interpretation` turns a ``Decision.to_dict()`` payload into a few
  sentences. Each sentence lists the payload fields it was written from
  (``cites``), so a reader or a test can trace every claim to a number the
  engine produced. It never re-scores, never picks a different label, and
  repeats the label and the evidence status word for word.
* :func:`check_narrative` reads text someone else wrote about the same Decision
  and lists every number in it that the payload does not contain (within
  rounding), plus any buy/sell word that contradicts the label. A narrative with
  findings is not grounded and should not be shown as QUANTA's view.

Both are pure functions over plain dicts, so they work on a live Decision, a
ledger snapshot or an MCP payload alike.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from .thresholds import floor_rr

INTERPRETATION_VERSION = "grounded.v1"


def _get(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _sentence(text: str, cites: Iterable[str]) -> Dict[str, Any]:
    return {"text": text, "cites": sorted(set(cites))}


def build_interpretation(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Sentences describing ``decision`` (a ``Decision.to_dict()`` payload).

    The first sentence is always the label and evidence status verbatim; the
    rest appear only when the field they read is present.
    """
    s: List[Dict[str, Any]] = []
    ticker = decision.get("ticker") or "This stock"
    label = decision.get("label") or decision.get("action") or "NO VERDICT"
    ev = decision.get("evidence") or {}
    status_text = ev.get("status_text") or ev.get("status") or "not validated out of sample"
    horizon = _get(decision, "horizon.label") or "its stated horizon"

    s.append(_sentence(
        f"QUANTA's call on {ticker} is {label} over {horizon}. Model evidence: {status_text}.",
        ["label", "horizon.label", "evidence.status_text"],
    ))

    blocker = decision.get("primary_blocker") or {}
    # engine.blockers rows: {"code", "category", "text", "remedy"}.
    if blocker.get("text"):
        what = blocker["text"]
        clears = blocker.get("remedy")
        text = f"The main thing holding it back: {what}."
        if clears:
            text += f" It would change with: {clears}."
        s.append(_sentence(text, ["primary_blocker"]))

    lv = decision.get("levels") or {}
    entry, stop, target, rr = (_num(lv.get(k)) for k in ("entry", "stop", "target", "reward_risk"))
    if entry and stop and target:
        risk_pct = (entry - stop) / entry * 100.0 if entry else None
        text = (f"The levels: entry {_money(entry)}, stop {_money(stop)}"
                + (f" ({risk_pct:.1f}% below entry)" if risk_pct is not None and risk_pct > 0 else "")
                + f", target {_money(target)}")
        thd = lv.get("target_horizon_days")
        if thd:
            text += f" over about {int(thd)} sessions"
        text += "."
        cites = ["levels.entry", "levels.stop", "levels.target", "levels.target_horizon_days"]
        if rr is not None:
            text += f" Reward to risk, net of costs: {floor_rr(rr):.2f} to 1."
            cites.append("levels.reward_risk")
        s.append(_sentence(text, cites))

    hurdles = lv.get("rr_hurdles") or {}
    failing = [(k, h) for k, h in hurdles.items() if isinstance(h, dict) and h.get("passes") is False]
    if failing:
        parts = []
        for k, h in failing:
            # Decision.rr_hurdles rows: {"min", "passes", "clear_entry"}.
            need = _num(h.get("min"))
            clear = _num(h.get("clear_entry"))
            p = f"the {k} hurdle" + (f" of {need:.2f}" if need else "")
            if clear:
                p += f" (cleared at a spot price of {_money(clear)} or lower)"
            parts.append(p)
        s.append(_sentence("Reward to risk falls short of " + "; ".join(parts) + ".",
                           ["levels.rr_hurdles"]))

    grade = decision.get("risk_grade")
    if grade:
        s.append(_sentence(f"Risk grade {grade}, which sets position size, not direction.",
                           ["risk_grade"]))

    reasons = [r for r in (decision.get("reasons") or []) if isinstance(r, str)][:3]
    if reasons:
        s.append(_sentence("Why: " + "; ".join(r.rstrip(".") for r in reasons) + ".", ["reasons"]))

    risks = [r for r in (decision.get("risks") or []) if isinstance(r, str)][:2]
    if risks:
        s.append(_sentence("Watch: " + "; ".join(r.rstrip(".") for r in risks) + ".", ["risks"]))

    if not ev.get("permits_recommendation"):
        s.append(_sentence(
            "No model here has a demonstrated out-of-sample edge, so treat this as a "
            "risk-budgeted view, not a validated signal.",
            ["evidence.permits_recommendation"],
        ))

    return {
        "version": INTERPRETATION_VERSION,
        "label_verbatim": label,
        "evidence_verbatim": status_text,
        "sentences": s,
        "text": " ".join(x["text"] for x in s),
    }


# --------------------------------------------------------------- narrative check

_NUMBER = re.compile(r"(?<![\w.])[-+]?\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?%?")
_DATE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_BULLISH = re.compile(r"\b(strong buy|buy now|buying opportunity|must[- ]buy|bullish call)\b", re.I)
_BEARISH = re.compile(r"\b(strong sell|sell now|short it|bearish call)\b", re.I)


def _collect_numbers(obj: Any, out: List[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        v = _num(obj)
        if v is not None:
            out.append(v)
    elif isinstance(obj, str):
        for m in _NUMBER.finditer(_DATE_LIKE.sub(" ", obj)):
            v = _num((m.group(1) or "").replace(",", "") + (m.group(2) or ""))
            if v is not None:
                out.append(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def _derived(decision: Dict[str, Any]) -> List[float]:
    """Numbers a faithful narrative may compute from the payload."""
    lv = decision.get("levels") or {}
    entry, stop, target = (_num(lv.get(k)) for k in ("entry", "stop", "target"))
    ref = _num(lv.get("reference_price")) or entry
    out: List[float] = []
    if entry and stop:
        out += [entry - stop, (entry - stop) / entry * 100.0]
    if entry and target:
        out += [target - entry, (target - entry) / entry * 100.0]
    if ref and target:
        out.append((target - ref) / ref * 100.0)
    if ref and stop:
        out.append((ref - stop) / ref * 100.0)
    return out


def _matches(value: float, known: List[float], decimals: int = 2) -> bool:
    # A number written with d decimals may be any value that rounds to it, so
    # allow half a unit in the last place (2.9 covers 2.85..2.95), or 0.5%
    # relative for large figures written without decimals.
    for k in known:
        tol = max(0.5 * 10 ** (-decimals) + 1e-9, abs(k) * 0.005)
        if abs(abs(value) - abs(k)) <= tol:
            return True
    return False


def _strings(obj: Any, out: List[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _strings(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strings(v, out)


def check_narrative(text: str, decision: Dict[str, Any],
                    allow: Iterable[float] = ()) -> Dict[str, Any]:
    """Findings for numbers and directional words in ``text`` not backed by ``decision``.

    Small integers (0-10) and the horizon's session counts are ignored: they
    are counts and ordinals, not claims. ``allow`` adds values the caller
    knows are legitimate (for example a position size it supplied).
    """
    known: List[float] = []
    _collect_numbers(decision, known)
    known += _derived(decision) + [float(a) for a in allow]

    findings: List[Dict[str, Any]] = []
    for m in _NUMBER.finditer(_DATE_LIKE.sub(" ", text or "")):
        raw = m.group(0)
        v = _num((m.group(1) or "").replace(",", "") + (m.group(2) or ""))
        if v is None or (float(v).is_integer() and 0 <= v <= 10):
            continue
        decimals = len(m.group(2)) - 1 if m.group(2) else 0
        if not _matches(v, known, decimals):
            findings.append({"kind": "unsupported_number", "value": raw.strip(),
                             "message": f"{raw.strip()} does not appear in the Decision."})

    # A phrase the Decision itself uses (a "Buy Now" timing rule named in its
    # reasons, say) is a quotation, not a contradiction.
    own: List[str] = []
    _strings(decision, own)
    own_text = " ".join(own).lower()

    label = str(decision.get("label") or "").upper()
    buyish = any(w in label for w in ("BUY", "ACCUMULATE"))
    for pattern, wrong, tone in ((_BULLISH, not buyish, "Bullish"), (_BEARISH, buyish, "Bearish")):
        if not wrong:
            continue
        for m in pattern.finditer(text or ""):
            if m.group(0).lower() not in own_text:
                findings.append({"kind": "contradicts_label", "value": m.group(0),
                                 "message": f"{tone} wording contradicts the label {label or 'NO VERDICT'}."})
                break
    ev = decision.get("evidence") or {}
    # Negated forms ("not a validated signal", "no proven edge") are the honest
    # caveat, not a claim.
    claim = re.search(r"(?<!not a )(?<!not )(?<!no )(?<!un)\b(proven|guaranteed|validated) "
                      r"(edge|signal|returns?)\b", text or "", re.I)
    if claim and not ev.get("permits_recommendation"):
        findings.append({"kind": "overstates_evidence", "value": "edge claim",
                         "message": "Claims a proven edge; the model's evidence status does not permit that."})

    return {"grounded": not findings, "findings": findings}


def interpretation_pair(decision: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(interpretation, self-check). The self-check must always be clean; a test pins it."""
    interp = build_interpretation(decision)
    return interp, check_narrative(interp["text"], decision)
