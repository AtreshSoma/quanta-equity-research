"""The daily briefing for My list: what changed on each saved name.

For every symbol in My list: the last close and the day's change, QUANTA's last
recorded call on it (from the track-record ledger) and the move since, its
active alerts and how far away they are, alerts that fired since the last
briefing, and an earnings report due within two weeks. Every number comes from
the data (Yahoo bars, the ledger, the alert store); nothing is written by a model.

    python webapp/briefing.py            # print it and save logs/daily/briefing-<date>.md
    python webapp/briefing.py --email    # ... and email it when SMTP is set (see my_list.py)

The daily job (scripts/quanta_daily.ps1, quanta_daily.sh) runs it after the report.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
for p in (str(_HERE), str(ROOT / "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from my_list_store import MyListStore  # noqa: E402

EARNINGS_WINDOW_DAYS = 14

QuoteFn = Callable[[str], Optional[Dict[str, Any]]]
CallFn = Callable[[str], Optional[Dict[str, Any]]]
EarningsFn = Callable[[str], Optional[date]]


def build_briefing(store: MyListStore, quote: QuoteFn, last_call: CallFn, earnings: EarningsFn,
                   today: Optional[date] = None) -> Dict[str, Any]:
    """The briefing as data: one entry per saved symbol, plus the fired alerts."""
    today = today or date.today()
    active = store.alerts(active_only=True)
    rows: List[Dict[str, Any]] = []
    for item in store.symbols():
        sym = item["symbol"]
        q = _safe(quote, sym) or {}
        price, prev = q.get("price"), q.get("previous_close")
        call = _safe(last_call, sym)
        since = None
        if call and price and call.get("price"):
            since = (price / call["price"] - 1) * 100
        alerts = []
        for a in (x for x in active if x["symbol"] == sym):
            away = (a["level"] / price - 1) * 100 if price else None
            alerts.append({"direction": a["direction"], "level": a["level"], "label": a["label"], "away_pct": away})
        report = _safe(earnings, sym)
        if report and not (today <= report <= today + timedelta(days=EARNINGS_WINDOW_DAYS)):
            report = None
        rows.append({
            "symbol": sym, "price": price,
            "change_pct": (price / prev - 1) * 100 if price and prev else None,
            "call": call, "since_call_pct": since, "alerts": alerts,
            "earnings": report.isoformat() if report else None,
        })
    return {"date": today.isoformat(), "rows": rows, "fired": store.inbox()}


def format_text(b: Dict[str, Any]) -> str:
    """The briefing as Markdown, short enough to read in an email."""
    lines = [f"# QUANTA briefing, {b['date']}", ""]
    if not b["rows"]:
        lines += ["My list is empty. Save names from a tear sheet, or with the bell in the top bar."]
    if b["fired"]:
        lines += ["## Fired alerts", ""]
        for a in b["fired"]:
            verb = "rose to" if a["direction"] == "above" else "fell to"
            lines.append(f"- {a['symbol']} {verb} {a['triggered_price']:,.2f} "
                         f"({a['label'] or 'alert'} at {a['level']:,.2f}), {str(a['triggered_at'])[:16]} UTC")
        lines.append("")
    if b["rows"]:
        lines += ["## My list", ""]
        for r in b["rows"]:
            head = f"- **{r['symbol']}**"
            if r["price"] is not None:
                head += f" {r['price']:,.2f}"
                if r["change_pct"] is not None:
                    head += f" ({r['change_pct']:+.2f}% today)"
            else:
                head += " (no price today)"
            lines.append(head)
            c = r["call"]
            if c and c.get("label"):
                since = f", {r['since_call_pct']:+.2f}% since" if r["since_call_pct"] is not None else ""
                lines.append(f"  - Last call: {c['label']} ({c.get('engine') or 'QUANTA'}, {c.get('as_of')}){since}")
            for a in r["alerts"]:
                away = f", {abs(a['away_pct']):.1f}% away" if a["away_pct"] is not None else ""
                lines.append(f"  - Alert at or {a['direction']} {a['level']:,.2f}"
                             + (f" ({a['label']})" if a["label"] else "") + away)
            if r["earnings"]:
                lines.append(f"  - Earnings report due {r['earnings']}")
        lines.append("")
    lines.append("Information only, not investment advice. No QUANTA model has shown an edge out of sample yet.")
    return "\n".join(lines)


def _safe(fn: Callable[[str], Any], sym: str) -> Any:
    try:
        return fn(sym)
    except Exception:  # noqa: BLE001 - one symbol's gap must not stop the briefing
        return None


# ---- the real sources, for the command line ---------------------------------

def yahoo_quote(sym: str) -> Optional[Dict[str, Any]]:
    """The last two daily closes."""
    import yfinance as yf
    closes = yf.Ticker(sym).history(period="5d", interval="1d")["Close"].dropna()
    if closes.empty:
        return None
    return {"price": float(closes.iloc[-1]), "previous_close": float(closes.iloc[-2]) if len(closes) > 1 else None}


def yahoo_earnings(sym: str) -> Optional[date]:
    import yfinance as yf
    cal = yf.Ticker(sym).calendar
    when = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if isinstance(when, list):
        when = when[0] if when else None
    if isinstance(when, datetime):
        return when.date()
    return when if isinstance(when, date) else None


def ledger_call(sym: str) -> Optional[Dict[str, Any]]:
    from tracking.ledger import SignalLedger
    rows = SignalLedger().rows(ticker=sym)
    if not rows:
        return None
    last = rows[-1]
    engine = {"elite": "Research", "elite_pro": "Picks", "shortterm": "Short-term scanner"}.get(
        last.get("engine"), last.get("engine"))
    raw = str(last.get("label") or last.get("action") or "")
    # The ledger stores codes (STRONG_BUY, WATCHLIST); show them as the page names them.
    label = " ".join(w.capitalize() for w in raw.replace("_", " ").split()) if raw.isupper() else raw
    return {"label": label, "engine": engine, "as_of": last.get("as_of_date"),
            "price": last.get("reference_price"), "stop": last.get("stop_price"),
            "target": last.get("target_price"), "evidence": last.get("evidence_status")}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Print and save the My list briefing.")
    ap.add_argument("--email", action="store_true", help="also email it when SMTP is configured")
    ap.add_argument("--out-dir", default=str(ROOT / "logs" / "daily"))
    args = ap.parse_args(argv)
    briefing = build_briefing(MyListStore(), yahoo_quote, ledger_call, yahoo_earnings)
    text = format_text(briefing)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"briefing-{briefing['date']}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nSaved {path}")
    if args.email:
        from my_list import send_email
        sent = send_email(f"QUANTA briefing {briefing['date']}", text)
        print("Emailed." if sent else "Not emailed: set QUANTA_SMTP_HOST and QUANTA_ALERT_EMAIL in .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
