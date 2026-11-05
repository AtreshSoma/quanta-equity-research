"""Plan Check: a pre-trade checklist for one intended BUY.

QUANTA never places orders. This module reads a user's intended buy (ticker,
shares, entry, stop, target) with optional account context and returns one
status per check: PASS, WARN, BLOCK or SKIPPED, each with a plain message and
the next step. Overall = the worst status, ranked BLOCK > WARN > SKIPPED > PASS.
SKIPPED means a check could not run; it is never shown as a pass.

Principle: only BUY is supported. A check may BLOCK new money, never an exit or
a risk-reducing action. Adding a sell or trim path must not reuse these checks
as a gate on the reducing side.

Pure function: the caller supplies the research read, holdings, earnings date
and today's date. Thresholds come from engine.thresholds, never hard-coded.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from . import fomc
from . import market_calendar as _mkt
from .costs import round_trip_cost_frac
from .levels import net_rr, rr_triggers
from .thresholds import PORTFOLIO, RR, SIZING, floor_rr

SUPPORTED_SIDES = ("buy",)

PASS, WARN, BLOCK, SKIPPED = "PASS", "WARN", "BLOCK", "SKIPPED"
_RANK = {BLOCK: 3, WARN: 2, SKIPPED: 1, PASS: 0}

# A stop closer than this share of price is inside ordinary noise: no real risk.
MIN_RISK_PCT_OF_PRICE = 0.0005
# FOMC inside this many sessions is WARN.
FOMC_WARN_SESSIONS = 2
# Earnings are judged against the horizon in calendar days (5 sessions = 7 days).
CALENDAR_DAYS_PER_SESSION = 7.0 / 5.0
# A report this many sessions old or fewer is still being priced: WARN.
RECENT_EARNINGS_SESSIONS = 2

EVIDENCE_NOTE = "No model here has a demonstrated edge."
NOTE_NOT_ADVICE = "A checklist, not advice: nothing here places or recommends an order."


def plan_fingerprint(ticker: str, shares: float, entry: float, stop: float, target: float,
                     plan_date: Any) -> str:
    """SHA-256 (first 16 hex) of the normalised plan. Key order and input types
    do not change it: numbers are rounded and the JSON keys are sorted."""
    payload = {
        "ticker": str(ticker or "").strip().upper(),
        "shares": round(float(shares), 6),
        "entry": round(float(entry), 6),
        "stop": round(float(stop), 6),
        "target": round(float(target), 6),
        "date": str(plan_date)[:10],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# The plain name a reader sees for each check (the id is for code, never shown).
TITLES = {
    "geometry": "Stop and target order",
    "rr": "Reward to risk",
    "dollar_risk": "Dollar risk per trade",
    "position_size": "Position size",
    "concentration_name": "Single-name limit",
    "concentration_sector": "Sector limit",
    "earnings": "Earnings",
    "fomc": "Fed decision",
    "decision": "QUANTA's call",
    "material_filings": "Recent SEC filings",
    "todays_move": "Today's move",
}

# What to do for a check that could not run, in a few words, for the summary line.
_SKIP_HINTS = {
    "rr": "add a valid stop and target",
    "dollar_risk": "add account value",
    "position_size": "add account value",
    "concentration_name": "connect My Portfolio",
    "concentration_sector": "connect My Portfolio",
    "earnings": "add the earnings date",
    "fomc": "add the Fed calendar",
    "decision": "run Research on this ticker",
    "material_filings": "check SEC filings",
    "todays_move": "run Research on this ticker",
    "geometry": "fix the prices",
}


def _check(cid: str, status: str, message: str, next_step: str = "") -> Dict[str, str]:
    return {"id": cid, "title": TITLES.get(cid, cid.replace("_", " ").capitalize()),
            "status": status, "message": message,
            "next_step": next_step or ("Nothing to do." if status == PASS else "")}


def _summary(checks: List[Dict[str, str]]) -> Dict[str, Any]:
    """Counts and one accurate headline. Warnings are named before skipped checks."""
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (PASS, WARN, BLOCK, SKIPPED)}
    parts: List[str] = []
    blocks = [c["title"] for c in checks if c["status"] == BLOCK]
    warns = [c["title"] for c in checks if c["status"] == WARN]
    skipped = [c for c in checks if c["status"] == SKIPPED]
    if blocks:
        parts.append(f"{len(blocks)} blocking: {', '.join(blocks)}.")
    if warns:
        parts.append(f"{len(warns)} warning{'s' if len(warns) != 1 else ''}: {', '.join(warns)}.")
    if skipped:
        hints: List[str] = []
        for c in skipped:
            hint = _SKIP_HINTS.get(c["id"], "")
            if hint and hint not in hints:
                hints.append(hint)
        n = len(skipped)
        tail = f" ({', '.join(hints)})" if hints else ""
        parts.append(f"{n} check{'s' if n != 1 else ''} could not run{tail}.")
    if not parts:
        parts.append("Every check passed.")
    return {"counts": counts, "headline": " ".join(parts)}


def _money(v: float, cur: str = "$") -> str:
    return f"{cur}{v:,.2f}"


def _pct(v: float, dp: int = 2) -> str:
    return f"{v * 100:.{dp}f}%"


def _geometry(entry: float, stop: float, target: float) -> Dict[str, str]:
    if not (stop < entry < target):
        return _check("geometry", BLOCK,
                      f"The prices are out of order: the stop must sit below entry and the target above it "
                      f"(stop {stop:g}, entry {entry:g}, target {target:g}).",
                      "Fix the order of entry, stop and target.")
    risk_pct = (entry - stop) / entry
    if risk_pct <= MIN_RISK_PCT_OF_PRICE:
        return _check("geometry", BLOCK,
                      f"The stop is only {_pct(risk_pct)} below entry: that is inside ordinary price noise, "
                      f"so the plan has no real risk per share.",
                      "Move the stop further below entry.")
    return _check("geometry", PASS,
                  f"Stop {_pct(risk_pct)} below entry, target {_pct((target - entry) / entry)} above.")


def _rr(entry: float, stop: float, target: float, cost_frac: float) -> Dict[str, str]:
    research_min = float(RR.RESEARCH_MIN)
    funding_min = max(float(RR.SCANNER_MIN), research_min)
    rr = net_rr(entry, stop, target, cost_frac)
    # Floored, as the page prints every R/R: a true 1.4988 never reads as 1.50.
    rr_txt = f"{floor_rr(rr):.2f}"
    if rr >= funding_min:
        return _check("rr", PASS, f"Net R/R {rr_txt} (after {cost_frac * 10000:.0f} bps round-trip cost) "
                                  f"clears the {funding_min:.1f} funding hurdle.")
    if rr >= research_min:
        trig = rr_triggers(entry, stop, target, cost_frac, funding_min)
        return _check("rr", WARN,
                      f"Net R/R {rr_txt} clears the Research gate ({research_min:.1f}) but not the "
                      f"{funding_min:.1f} funding hurdle.",
                      f"Enter at or below {_money(trig['entry_needed'])} (keeping your stop and target), "
                      f"or raise the target to {_money(trig['target_needed'])}, to reach {funding_min:.1f}.")
    trig = rr_triggers(entry, stop, target, cost_frac, research_min)
    return _check("rr", BLOCK,
                  f"Net R/R {rr_txt} is below the Research gate of {research_min:.1f}.",
                  f"Enter at or below {_money(trig['entry_needed'])} (keeping your stop and target), "
                  f"or raise the target to {_money(trig['target_needed'])}, to reach {research_min:.1f}.")


def _dollar_risk(shares: float, entry: float, stop: float, account: Optional[float]) -> Dict[str, str]:
    if not account or account <= 0:
        return _check("dollar_risk", SKIPPED, "Enter the account value to size the dollar risk.",
                      "Add the account value to check the risk per trade.")
    risk = shares * (entry - stop)
    pct = risk / account
    target_cap = float(SIZING.TARGET_RISK_PER_TRADE)
    hard_cap = float(SIZING.MAX_RISK_PER_TRADE)
    per_share = entry - stop
    max_shares = math.floor(account * target_cap / per_share) if per_share > 0 else 0
    msg = f"Losing the stop would cost {_money(risk)}, {_pct(pct)} of the account."
    if pct <= target_cap:
        return _check("dollar_risk", PASS, msg + f" Within the {_pct(target_cap, 1)} target risk per trade.")
    if pct <= hard_cap:
        return _check("dollar_risk", WARN,
                      msg + f" Above the {_pct(target_cap, 1)} target, below the {_pct(hard_cap, 1)} hard cap.",
                      f"Reduce to {max_shares} shares to sit at the {_pct(target_cap, 1)} target.")
    return _check("dollar_risk", BLOCK, msg + f" Over the {_pct(hard_cap, 1)} hard cap per trade.",
                  f"Reduce to {max_shares} shares to sit at the {_pct(target_cap, 1)} target.")


def _position_size(shares: float, entry: float, account: Optional[float], cash: Optional[float]) -> Dict[str, str]:
    value = shares * entry
    cap = float(SIZING.MAX_POSITION_PCT)
    if not account or account <= 0:
        if cash is None:
            return _check("position_size", SKIPPED, "Enter the account value or cash to size the position.",
                          "Add the account value to check the position against the cap.")
        if value > cash:
            return _check("position_size", BLOCK,
                          f"The position costs {_money(value)}, more than the {_money(cash)} cash.",
                          f"Reduce to {math.floor(cash / entry)} shares or fewer.")
        return _check("position_size", SKIPPED,
                      f"The position costs {_money(value)}, within the {_money(cash)} cash. The "
                      f"{_pct(cap, 0)} position cap needs the account value.",
                      "Add the account value to check the position cap.")
    pct = value / account
    max_shares = math.floor(account * cap / entry)
    cash_note = ""
    if cash is None:
        cash_note = " Cash not given, so the cash check was skipped."
    if pct > cap:
        return _check("position_size", BLOCK,
                      f"The position is {_money(value)}, {_pct(pct, 1)} of the account, over the "
                      f"{_pct(cap, 0)} position cap.{cash_note}",
                      f"Reduce to {max_shares} shares or fewer.")
    if cash is not None and value > cash:
        return _check("position_size", BLOCK,
                      f"The position costs {_money(value)}, more than the {_money(cash)} cash.",
                      f"Reduce to {math.floor(cash / entry)} shares or fewer.")
    return _check("position_size", PASS,
                  f"The position is {_money(value)}, {_pct(pct, 1)} of the account, within the "
                  f"{_pct(cap, 0)} cap.{cash_note}")


def _max_shares(entry: float, stop: float, account: Optional[float],
                cash: Optional[float]) -> Optional[Dict[str, Any]]:
    """The largest whole-share size inside every sizing limit, and which limit set it.

    One number in place of three separate "reduce to" hints. None when neither
    an account value nor cash is known.
    """
    per_share = entry - stop
    limits: Dict[str, int] = {}
    if account and account > 0:
        if per_share > 0:
            limits["risk target"] = math.floor(account * float(SIZING.TARGET_RISK_PER_TRADE) / per_share)
        limits["position cap"] = math.floor(account * float(SIZING.MAX_POSITION_PCT) / entry)
    if cash is not None and cash >= 0:
        limits["cash"] = math.floor(cash / entry)
    if not limits:
        return None
    binding = min(limits, key=limits.get)
    return {"max_shares": max(0, limits[binding]), "binding": binding, "limits": limits}


def _concentration_name(sym: str, value: float, account: Optional[float],
                        holdings: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if holdings is None:
        return _check("concentration_name", SKIPPED,
                      "Not connected to My Portfolio, so current holdings were not checked.",
                      "Connect My Portfolio to check the single-name limit.")
    if not account or account <= 0:
        return _check("concentration_name", SKIPPED, "No account value, so the weight cannot be measured.")
    pos = (holdings.get("positions") or {}).get(sym) or {}
    existing = pos.get("market_value")
    if existing is None and sym in (holdings.get("positions") or {}):
        return _check("concentration_name", SKIPPED,
                      f"The market value of your {sym} position is not known, so the limit was not checked.")
    after = float(existing or 0.0) + value
    weight = after / account
    limit = float(PORTFOLIO.MAX_SINGLE_POSITION)
    if weight > limit:
        return _check("concentration_name", BLOCK,
                      f"After this buy {sym} would be {_pct(weight, 1)} of the account, over the "
                      f"{_pct(limit, 0)} single-name limit.",
                      "Buy fewer shares, or none while the position is above the limit.")
    return _check("concentration_name", PASS,
                  f"{sym} would be {_pct(weight, 1)} of the account, within the {_pct(limit, 0)} single-name limit.")


def _concentration_sector(sym: str, sector: Optional[str], value: float, account: Optional[float],
                          holdings: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if holdings is None:
        return _check("concentration_sector", SKIPPED,
                      "Not connected to My Portfolio, so the sector exposure was not checked.")
    if not account or account <= 0:
        return _check("concentration_sector", SKIPPED, "No account value, so the weight cannot be measured.")
    if not sector:
        return _check("concentration_sector", SKIPPED,
                      f"The sector for {sym} is not known, so the sector limit was not checked.")
    existing = 0.0
    for p in (holdings.get("positions") or {}).values():
        if str(p.get("sector") or "") == sector:
            if p.get("market_value") is None:
                return _check("concentration_sector", SKIPPED,
                              f"A {sector} position has no known market value, so the limit was not checked.")
            existing += float(p["market_value"])
    weight = (existing + value) / account
    limit = float(PORTFOLIO.MAX_SECTOR)
    if weight > limit:
        return _check("concentration_sector", BLOCK,
                      f"After this buy {sector} would be {_pct(weight, 1)} of the account, over the "
                      f"{_pct(limit, 0)} sector limit.",
                      "Buy fewer shares, or pick a name outside this sector.")
    return _check("concentration_sector", PASS,
                  f"{sector} would be {_pct(weight, 1)} of the account, within the {_pct(limit, 0)} sector limit.")


def _sessions_between(start: date, end: date, india: bool) -> int:
    """Trading sessions in (start, end], on the listing's own calendar."""
    if india:
        return _mkt.india_trading_days_between(start, end)
    return _mkt.trading_days_between(start, end)


def _move_text(todays_move: Optional[Dict[str, Any]]) -> str:
    try:
        pct = float((todays_move or {}).get("day_change_pct"))
    except (TypeError, ValueError):
        return ""
    return f" (today {pct:+.1f}%)"


def _earnings(next_earnings: Optional[date], today: date, horizon: int, *,
              last_earnings: Optional[date] = None, india: bool = False,
              todays_move: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """The next report against the horizon, and a warning while the last report's
    price reaction is still under way (released within RECENT_EARNINGS_SESSIONS)."""
    recent_msg = None
    if last_earnings is not None and last_earnings <= today:
        since = _sessions_between(last_earnings, today, india)
        if 0 <= since <= RECENT_EARNINGS_SESSIONS:
            recent_msg = (f"Earnings were released on {last_earnings.isoformat()}; the price is still "
                          f"reacting{_move_text(todays_move)}.")
    if next_earnings is None or next_earnings < today:
        if recent_msg:
            return _check("earnings", WARN, recent_msg,
                          "Wait for the reaction to settle, or size down, before you buy.")
        return _check("earnings", SKIPPED,
                      "The next earnings date is not known for this ticker.",
                      "Check the company's earnings calendar before you buy.")
    days = (next_earnings - today).days
    sessions = _sessions_between(today, next_earnings, india)
    window = math.ceil(horizon * CALENDAR_DAYS_PER_SESSION)
    if days <= window:
        msg = (f"Earnings on {next_earnings.isoformat()} ({sessions} sessions away), inside the "
               f"{horizon}-session horizon. A results gap can pass the stop or target in one bar.")
        if recent_msg:
            msg = recent_msg + " " + msg
        return _check("earnings", WARN, msg,
                      "Consider waiting until after the report, or sizing down.")
    if recent_msg:
        return _check("earnings", WARN, recent_msg,
                      "Wait for the reaction to settle, or size down, before you buy.")
    return _check("earnings", PASS,
                  f"Next earnings {next_earnings.isoformat()}, {sessions} sessions away, beyond the "
                  f"{horizon}-session horizon.")


def _todays_move(research: Optional[Dict[str, Any]], todays_move: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """WARN when today's live move is large and not yet in the signals (informational
    from engine.bars.live_preview; it never changes the score or the action)."""
    if not research:
        return _check("todays_move", SKIPPED,
                      "No Research read is cached for this ticker, so today's move was not checked.",
                      "Run Research on this ticker, then check the plan again.")
    if not todays_move:
        return _check("todays_move", PASS, "No live move is waiting outside the signals: the scores are current.")
    try:
        pct = float(todays_move.get("day_change_pct"))
    except (TypeError, ValueError):
        return _check("todays_move", PASS, "No live move is waiting outside the signals: the scores are current.")
    if todays_move.get("large_move"):
        return _check("todays_move", WARN,
                      f"Today's {pct:+.1f}% move is not yet in QUANTA's signals; the call is re-scored "
                      f"after the close.",
                      "Wait for the close, when QUANTA re-scores with today included, or size down.")
    return _check("todays_move", PASS, f"Today's {pct:+.1f}% move is within its normal range.")


def _fomc(today: date, fomc_dates: Optional[Iterable[date]]) -> Dict[str, str]:
    days = sorted(fomc_dates) if fomc_dates is not None else sorted(fomc.all_dates(today.year, today.year + 1))
    nxt = next((d for d in days if d >= today), None)
    if nxt is None:
        return _check("fomc", SKIPPED, "No FOMC decision date is on the calendar after today.")
    # Weekdays after today up to and including the decision day.
    sessions = int(np.busday_count(today + timedelta(days=1), nxt + timedelta(days=1)))
    tentative = " (tentative date)" if nxt.year in fomc.TENTATIVE_YEARS else ""
    if sessions <= FOMC_WARN_SESSIONS:
        when = "today" if sessions == 0 else f"in {sessions} session{'s' if sessions != 1 else ''}"
        return _check("fomc", WARN,
                      f"Fed decision on {nxt.isoformat()}{tentative}, {when}. Rate news can move the whole market.",
                      "Consider waiting until the decision has passed.")
    return _check("fomc", PASS, f"Next Fed decision {nxt.isoformat()}{tentative}, {sessions} sessions away.")


def _decision(research: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not research:
        return _check("decision", SKIPPED,
                      "No Research read is cached for this ticker in this session, so QUANTA's call was not compared.",
                      "Run Research on this ticker, then check the plan again.")
    action = str(research.get("action") or "").upper()
    new_money = str(research.get("new_money") or "").upper()
    blockers = [b for b in (research.get("blockers") or []) if isinstance(b, dict) and b.get("text")]
    if action in ("AVOID", "INVALID") or new_money == "AVOID":
        text = f"QUANTA's latest Research call is {action or 'AVOID'}."
        if blockers:
            text += f" {blockers[0]['text']}"
        return _check("decision", WARN, text, "Read the Research blockers before you buy.")
    if blockers or new_money in ("WATCH",):
        first = blockers[0]["text"] if blockers else "The Research call is to watch, not to buy yet."
        return _check("decision", WARN, f"QUANTA's latest call is {action or 'unrated'}. {first}",
                      "Read the Research blockers before you buy.")
    return _check("decision", PASS, f"QUANTA's latest call is {action or 'unrated'}, with no active blocker.")


FILINGS_WARN_DAYS = 30  # a HIGH-severity 8-K filed this recently is named as a warning


def _material_filings(filings: Optional[Dict[str, Any]], today: date) -> Dict[str, str]:
    """A WARN when a HIGH-severity 8-K was filed in the last FILINGS_WARN_DAYS days; PASS when none was;
    SKIPPED when the check could not run (not covered, unavailable, or not requested)."""
    if filings is None:
        return _check("material_filings", SKIPPED,
                      "Material filings were not checked for this plan.",
                      "Look up the company's 8-K filings on SEC EDGAR before you buy.")
    status = str(filings.get("status") or "")
    if status == "not_covered":
        return _check("material_filings", SKIPPED,
                      str(filings.get("message") or "Not covered: SEC filings exist only for US-registered issuers."),
                      "Check the company's own filings and news before you buy.")
    if status != "ok":
        return _check("material_filings", SKIPPED,
                      f"The 8-K check is unavailable right now. {filings.get('message') or ''}".strip(),
                      "Check SEC EDGAR for recent 8-K filings before you buy.")
    recent_high = []
    for e in filings.get("events") or []:
        try:
            filed = date.fromisoformat(str(e.get("date")))
        except ValueError:
            continue
        age = (today - filed).days
        if 0 <= age <= FILINGS_WARN_DAYS and e.get("max_severity") == "HIGH":
            recent_high.append((filed, e))
    if recent_high:
        recent_high.sort(key=lambda p: p[0], reverse=True)
        filed, e = recent_high[0]
        top = next((i for i in e.get("items") or [] if i.get("severity") == "HIGH"), None) or {}
        label = f"Item {top.get('code')} {top.get('title')}" if top else "a HIGH-severity item"
        more = f" {len(recent_high) - 1} more in the window." if len(recent_high) > 1 else ""
        return _check("material_filings", WARN,
                      f"A HIGH-severity 8-K was filed {filed.isoformat()}: {label}.{more}",
                      "Read the filing before you buy.")
    return _check("material_filings", PASS,
                  f"No HIGH-severity 8-K in the last {FILINGS_WARN_DAYS} days.")


def check_plan(*, ticker: str, shares: float, entry: float, stop: float, target: float,
               plan_date: date, today: date, account_value: Optional[float] = None,
               cash: Optional[float] = None, horizon_sessions: int = 63,
               research: Optional[Dict[str, Any]] = None, next_earnings: Optional[date] = None,
               holdings: Optional[Dict[str, Any]] = None, sector: Optional[str] = None,
               fomc_dates: Optional[Iterable[date]] = None, side: str = "buy",
               material_filings: Optional[Dict[str, Any]] = None,
               last_earnings: Optional[date] = None,
               todays_move: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run every check and return ``{overall, summary, checks, notes, fingerprint, plan}``.

    ``holdings`` is None when My Portfolio is not connected; otherwise
    ``{"positions": {SYM: {"market_value": float|None, "sector": str}}}``.
    ``research`` is the cached Decision dict (not triggered here).
    ``last_earnings`` is the last report date; ``todays_move`` is the Research
    read's ``technical.live_preview`` (None when every bar was scored).
    """
    if side not in SUPPORTED_SIDES:
        raise ValueError("only buy plans are checked")
    sym = str(ticker or "").strip().upper()
    india = sym.endswith((".NS", ".BO"))
    shares, entry, stop, target = float(shares), float(entry), float(stop), float(target)
    cost = round_trip_cost_frac(sym)
    account = float(account_value) if account_value else None
    if account is None and holdings and holdings.get("account_value"):
        account = float(holdings["account_value"])
    value = shares * entry

    checks: List[Dict[str, str]] = [_geometry(entry, stop, target)]
    geometry_ok = checks[0]["status"] != BLOCK
    if geometry_ok:
        checks.append(_rr(entry, stop, target, cost))
    else:
        checks.append(_check("rr", SKIPPED, "Net R/R needs a valid stop and target.",
                             "Fix the geometry first."))
    if geometry_ok:
        checks.append(_dollar_risk(shares, entry, stop, account))
    else:
        checks.append(_check("dollar_risk", SKIPPED, "Risk needs a valid stop.", "Fix the geometry first."))
    checks.append(_position_size(shares, entry, account, cash))
    checks.append(_concentration_name(sym, value, account, holdings))
    checks.append(_concentration_sector(sym, sector, value, account, holdings))
    checks.append(_earnings(next_earnings, today, int(horizon_sessions), last_earnings=last_earnings,
                            india=india, todays_move=todays_move))
    checks.append(_fomc(today, fomc_dates))
    checks.append(_decision(research))
    checks.append(_material_filings(material_filings, today))
    checks.append(_todays_move(research, todays_move))

    overall = max((c["status"] for c in checks), key=lambda s: _RANK[s])
    evidence = str(((research or {}).get("evidence") or {}).get("status") or "").upper()
    notes = [NOTE_NOT_ADVICE]
    if evidence != "VALIDATED":
        notes.append(EVIDENCE_NOTE)
    incomplete = any(c["status"] == SKIPPED for c in checks)
    return {
        "ticker": sym,
        "side": side,
        "fingerprint": plan_fingerprint(sym, shares, entry, stop, target, plan_date),
        "plan": {"ticker": sym, "shares": shares, "entry": entry, "stop": stop, "target": target,
                 "date": str(plan_date)[:10], "position_value": round(value, 2),
                 "cost_frac": cost},
        "overall": overall,
        "summary": _summary(checks),
        "incomplete": incomplete,
        "sizing": _max_shares(entry, stop, account, cash) if geometry_ok else None,
        "checks": checks,
        "notes": notes,
        "as_of": today.isoformat(),
    }
