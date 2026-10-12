"""Why a stock is on the Picks table: the evidence the scan measured, in words.

The scanner reads analyst consensus, SEC 8-K and headline catalysts, estimate
revisions, insider trades, valuation, growth, quality and the price trend for
every rated name, then used to fold all of it into one score and discard it.
:func:`build` turns what was measured for one name into short, sourced
statements on both sides, so the page can say why the name was picked and what
counts against it. Nothing here is inferred: an item appears only when its
input was measured, and every cut-off comes from ``thresholds.WHY`` (or the
style screen's own rules).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.thresholds import WHY

_BULL_RATINGS = {"strong_buy": "Strong Buy", "buy": "Buy"}
_BEAR_RATINGS = {"sell": "Sell", "strong_sell": "Strong Sell", "underperform": "Underperform"}
_CATALYST_WORDS = {
    "PRODUCT_LAUNCH": "Product launch", "CONTRACT": "New contract", "MNA": "Deal / M&A",
    "REGULATORY": "Regulatory", "EARNINGS": "Earnings", "PARTNERSHIP": "Partnership",
    "MANAGEMENT": "Management change", "FDA": "FDA", "SEC_8K": "SEC 8-K filing",
}


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _pct(x: float, digits: int = 1, sign: bool = False) -> str:
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _item(kind: str, label: str, text: str, short: str, **extra: Any) -> Dict[str, Any]:
    out = {"kind": kind, "label": label, "text": text, "short": short}
    out.update({k: v for k, v in extra.items() if v})
    return out


def _measured(fm: Dict[str, Any], key: str) -> Optional[float]:
    avail = fm.get("data_available") or {}
    if avail.get(key) is False:
        return None
    v = _f(fm.get(key))
    if v is None or (not avail.get(key) and v == 0.0):
        return None
    return v


def _style_item(screen: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not screen or not screen.get("passed") or not screen.get("checks"):
        return None
    parts = []
    for c in screen["checks"]:
        if not c.get("ok"):
            continue
        v = c.get("value")
        label = c["label"].split(" (")[0]
        label = label[0].lower() + label[1:] if label[1:2].islower() else label
        if c["key"] == "aristocrat":
            parts.append("on the S&P 500 Dividend Aristocrats list (25+ years of increases)")
        elif c["key"] == "profitable":
            parts.append("profitable (positive earnings and free cash flow)")
        elif c["key"] == "forward_eps":
            parts.append(f"EPS expected to rise (forward P/E {v:.1f}, {c['rule']})" if v is not None
                         else "EPS expected to rise")
        elif v is None:
            continue
        elif c.get("unit") == "pct":
            parts.append(f"{label} {_pct(v)} ({c['rule']})")
        else:
            parts.append(f"{label} {v:.1f} ({c['rule']})")
    if not parts:
        return None
    return _item("style", screen.get("label") or "Style screen", "Passed: " + "; ".join(parts) + ".",
                 f"passes the {str(screen.get('style') or 'style')} screen")


def build(rec: Dict[str, Any], research: Any = None) -> Dict[str, Any]:
    """``{"summary", "for": [...], "against": [...], "news": [...], "notes": [...]}``.

    ``rec`` is a scanner result as a dict (``StockAnalysis.to_dict()``);
    ``research`` the Research engine's result for the name when the scan ran
    it (funded and watch rows), else None.
    """
    pro: List[Dict[str, Any]] = []
    con: List[Dict[str, Any]] = []
    notes: List[str] = []
    ti = rec.get("technical_indicators") or {}
    fm = rec.get("fundamental_metrics") or {}
    price = _f(ti.get("current_price")) or _f(rec.get("entry_price")) or 0.0
    fund = bool(rec.get("is_fund"))

    style = _style_item(rec.get("style_screen"))
    if style:
        pro.append(style)

    # Catalysts: SEC 8-K filings and company headlines the catalyst engine classified.
    for c in rec.get("catalysts") or []:
        word = _CATALYST_WORDS.get(str(c.get("type") or "").upper(), str(c.get("type") or "Catalyst").title())
        summary = str(c.get("summary") or "").strip()
        if not summary:
            continue
        action = str(c.get("action") or "").upper()
        if action == "BUY":
            pro.append(_item("catalyst", word, summary, f"a {word.lower()} catalyst"))
        elif action in ("AVOID", "SELL"):
            con.append(_item("catalyst", word, summary, f"a negative {word.lower()} catalyst"))

    # Wall Street consensus.
    rating = str(rec.get("analyst_rating") or "").lower()
    count = int(_f(rec.get("analyst_count")) or 0)
    target = _f(rec.get("analyst_target")) or 0.0
    if rating and count >= WHY.ANALYST_MIN_COUNT:
        up = (target / price - 1.0) if target > 0 and price > 0 else None
        tgt = f"; mean target {_money(target)} ({_pct(up, 1, True)} from the price)" if up is not None else ""
        if rating in _BULL_RATINGS:
            pro.append(_item("analysts", "Analysts", f"Wall Street consensus is {_BULL_RATINGS[rating]} "
                             f"from {count} analysts{tgt}.", f"a {_BULL_RATINGS[rating]} analyst consensus",
                             source="Analyst consensus via Yahoo Finance"))
        elif rating in _BEAR_RATINGS:
            con.append(_item("analysts", "Analysts", f"Wall Street consensus is {_BEAR_RATINGS[rating]} "
                             f"from {count} analysts{tgt}.", "a bearish analyst consensus",
                             source="Analyst consensus via Yahoo Finance"))
        if up is not None and up < 0:
            con.append(_item("analysts", "Analyst target", f"The analysts' mean target {_money(target)} "
                             f"is {_pct(-up)} below the price.", "a mean target below the price"))

    # Estimate revisions and earnings surprises (Zacks-style).
    rev = str(rec.get("revision_sentiment") or "").upper()
    net = int(_f(rec.get("net_revisions_30d")) or 0)
    trend = _f(rec.get("eps_trend_pct_30d"))
    if rev == "UPGRADING":
        ups = f"{net} more upward than downward revision{'s' if net != 1 else ''} in 30 days" if net > 0 else ""
        if trend is not None and trend < 0:
            # More analysts raised than cut, yet the average estimate slipped:
            # say both, and do not call it rising.
            pro.append(_item("earnings", "Revisions mostly up",
                             (ups[0].upper() + ups[1:] + ", " if ups else "")
                             + f"though the average forward EPS estimate moved {trend:+.1f}%.",
                             "more upward than downward estimate revisions"))
        else:
            bits = [ups, f"forward EPS estimate {trend:+.1f}%" if trend else ""]
            pro.append(_item("earnings", "Estimates rising", "Analysts are raising earnings estimates"
                             + (": " + ", ".join(b for b in bits if b) if any(bits) else "") + ".",
                             "rising earnings estimates"))
    elif rev == "DOWNGRADING":
        con.append(_item("earnings", "Estimates falling", "Analysts are cutting earnings estimates"
                         + (f" (forward EPS estimate {trend:+.1f}% in 30 days)" if trend else "") + ".",
                         "falling earnings estimates"))
    surprise = _f(rec.get("avg_surprise_pct"))
    if surprise and abs(surprise) >= WHY.SURPRISE_MIN_PCT:
        (pro if surprise > 0 else con).append(_item(
            "earnings", "Earnings record", f"Reported earnings {'beat' if surprise > 0 else 'missed'} "
            f"estimates by {abs(surprise):.1f}% on average in recent quarters.",
            "a record of earnings beats" if surprise > 0 else "a record of earnings misses"))
    days = rec.get("days_to_earnings")
    if isinstance(days, (int, float)) and 0 <= days <= WHY.EARNINGS_NEAR_DAYS:
        con.append(_item("earnings", "Earnings soon", f"Earnings are due in {int(days)} day"
                         f"{'' if int(days) == 1 else 's'}: results can move the price sharply.",
                         "earnings due soon"))

    if not fund:
        # Valuation, growth, quality and dividend: company data only.
        vs, qs = _f(rec.get("value_score")), _f(rec.get("quality_score"))
        pe, fpe = _measured(fm, "pe_ratio"), _measured(fm, "forward_pe")
        evx = _measured(fm, "ev_ebitda")
        fcf, cap = _measured(fm, "free_cash_flow"), _measured(fm, "market_cap")
        fy = fcf / cap if fcf is not None and cap else None
        val_bits = [f"forward P/E {fpe:.1f}" if fpe and fpe > 0 else (f"P/E {pe:.1f}" if pe and pe > 0 else ""),
                    f"EV/EBITDA {evx:.1f}" if evx and evx > 0 else "",
                    f"free-cash-flow yield {_pct(fy)}" if fy is not None else ""]
        val_text = ", ".join(b for b in val_bits if b)
        if vs is not None and vs >= WHY.STRONG_SUBSCORE and val_text and not style:
            pro.append(_item("valuation", "Valuation", f"Attractively valued (value score {vs:.1f}/10): {val_text}.",
                             "an attractive valuation"))
        elif vs is not None and 0 < vs <= WHY.WEAK_SUBSCORE and val_text:
            con.append(_item("valuation", "Valuation", f"Richly valued (value score {vs:.1f}/10): {val_text}.",
                             "a rich valuation"))
        rg, eg = _measured(fm, "revenue_growth"), _measured(fm, "earnings_growth")
        growth_scan = str((rec.get("style_screen") or {}).get("style") or "") == "growth"
        if rg is not None and rg >= WHY.REVENUE_GROWTH_NOTABLE and not growth_scan:
            pro.append(_item("growth", "Growth", f"Revenue {_pct(rg, 1, True)} year over year"
                             + (f", earnings {_pct(eg, 1, True)}" if eg is not None else "") + ".",
                             "double-digit revenue growth"))
        elif rg is not None and rg < 0:
            con.append(_item("growth", "Growth", f"Revenue {_pct(rg, 1, True)} year over year.", "shrinking revenue"))
        roe, om, de = _measured(fm, "roe"), _measured(fm, "operating_margin"), _measured(fm, "debt_to_equity")
        q_bits = [f"return on equity {_pct(roe)}" if roe is not None else "",
                  f"operating margin {_pct(om)}" if om is not None else "",
                  f"debt/equity {de:.2f}" if de is not None else ""]
        q_text = ", ".join(b for b in q_bits if b)
        if qs is not None and qs >= WHY.STRONG_SUBSCORE and q_text:
            pro.append(_item("quality", "Quality", f"High-quality business (quality score {qs:.1f}/10): {q_text}.",
                             "a high-quality business"))
        elif qs is not None and 0 < qs <= WHY.WEAK_SUBSCORE and q_text:
            con.append(_item("quality", "Quality", f"Weak quality (quality score {qs:.1f}/10): {q_text}.",
                             "weak profitability or balance sheet"))
        dy, payout = _measured(fm, "dividend_yield"), _measured(fm, "payout_ratio")
        if dy and dy >= WHY.DIVIDEND_YIELD_NOTABLE and not style:
            pro.append(_item("dividend", "Dividend", f"Dividend yield {_pct(dy, 2)}"
                             + (f", paying out {_pct(payout, 0)} of earnings" if payout else "") + ".",
                             "a solid dividend"))
    else:
        notes.append("A fund is scored on trend and momentum only: it has no earnings, margins or balance sheet.")

    # Insiders.
    ins = str(rec.get("insider_sentiment") or "").upper()
    note = str(rec.get("insider_note") or "").strip()
    if ins == "BULLISH":
        pro.append(_item("insider", "Insiders buying", note or "Insiders were net buyers in the last 90 days.",
                         "insider buying"))
    elif ins == "BEARISH":
        con.append(_item("insider", "Insiders selling", note or "Insiders were net sellers in the last 90 days.",
                         "insider selling"))

    # Trend and momentum.
    sma50, sma200, rsi = _f(ti.get("sma_50")), _f(ti.get("sma_200")), _f(ti.get("rsi"))
    if price and sma50 and sma200:
        if price > sma50 > sma200:
            pro.append(_item("technical", "Uptrend", f"Price {_money(price)} is above its 50-day "
                             f"({_money(sma50)}) and 200-day ({_money(sma200)}) averages.", "an established uptrend"))
        elif price < sma200:
            con.append(_item("technical", "Below trend", f"Price {_money(price)} is under its 200-day average "
                             f"({_money(sma200)}).", "a price under its 200-day average"))
    if rsi is not None and rsi >= WHY.RSI_STRETCHED:
        con.append(_item("technical", "Overbought", f"RSI {rsi:.0f}: the recent run is stretched.", "a stretched run"))
    m3, m6 = _f(ti.get("momentum_3m")), _f(ti.get("momentum_6m"))
    ms = _f(rec.get("momentum_score"))
    if ms is not None and ms >= WHY.STRONG_SUBSCORE and (m3 or m6):
        pro.append(_item("momentum", "Momentum", f"Strong momentum (momentum score {ms:.1f}/10): "
                         + ", ".join(b for b in [f"{_pct(m3, 1, True)} over 3 months" if m3 else "",
                                                  f"{_pct(m6, 1, True)} over 6 months" if m6 else ""] if b) + ".",
                         "strong price momentum"))
    em = [str(s) for s in (rec.get("early_momentum_signals") or []) if s]
    if len(em) >= WHY.EARLY_MOMENTUM_MIN:
        pro.append(_item("technical", "Early momentum", "; ".join(em[:4]) + ".", "an early-momentum setup"))

    # Research check (funded and watch rows): fair value and news tone, when measured.
    if research is not None:
        val = getattr(research, "valuation", None)
        if val is not None and str(getattr(val, "dcf_status", "")) == "measured":
            fv, up = _f(getattr(val, "dcf_fair_value", 0)), _f(getattr(val, "dcf_upside", 0))
            if fv and up is not None:
                (pro if up > 0 else con).append(_item(
                    "research", "Fair value", f"Research's discounted-cash-flow fair value is {_money(fv)}, "
                    f"{abs(up):.1f}% {'above' if up > 0 else 'below'} the price.",
                    "a fair value above the price" if up > 0 else "a fair value below the price"))
        sen = getattr(research, "sentiment", None)
        if sen is not None and getattr(sen, "measured", False):
            label = str(getattr(sen, "sentiment_label", "") or "").upper()
            n = int(_f(getattr(sen, "direct_news_count", 0)) or 0)
            if "POSITIVE" in label or "BULL" in label:
                pro.append(_item("news", "News tone", f"Company news reads positive ({n} articles scored).",
                                 "positive news flow"))
            elif "NEGATIVE" in label or "BEAR" in label:
                con.append(_item("news", "News tone", f"Company news reads negative ({n} articles scored).",
                                 "negative news flow"))

    if str(rec.get("risk_grade") or "").upper() in ("D", "F"):
        con.append(_item("risk", "Risk grade", f"Risk grade {rec.get('risk_grade')}: volatile or deep drawdowns, "
                         "so the position is sized down.", "a high risk grade"))

    pro, con = pro[:WHY.MAX_ITEMS], con[:WHY.MAX_ITEMS]
    shorts = [p["short"] for p in pro[:3]]
    summary = ""
    if shorts:
        joined = shorts[0] if len(shorts) == 1 else ", ".join(shorts[:-1]) + " and " + shorts[-1]
        summary = joined[0].upper() + joined[1:] + "."
    news = [n for n in (rec.get("news") or []) if isinstance(n, dict) and n.get("title")][:WHY.NEWS_ITEMS]
    return {"summary": summary, "for": pro, "against": con, "news": news, "notes": notes}
