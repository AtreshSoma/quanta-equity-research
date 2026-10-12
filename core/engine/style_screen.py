"""The fundamental screen behind each style scan (Growth / Value / Dividend).

Before this, "Value Compounders" ranked names by how far they had fallen from
their six-month high and "Dividend Aristocrats" by low volatility; neither read
a valuation or a dividend. A style scan now rates only names that pass its
screen, and every check is kept (measure, value, rule, pass) so the page can
show why a name qualified. Thresholds live in ``thresholds.STYLE``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.thresholds import STYLE

STYLE_OF_SCAN = {"focused_growth": "growth", "focused_value": "value", "focused_dividend": "dividend",
                 "focused_momentum": "momentum",
                 "india_growth": "growth", "india_value": "value", "india_dividend": "dividend",
                 "india_momentum": "momentum"}
LABELS = {"growth": "Growth screen", "value": "Value screen", "dividend": "Dividend screen",
          "momentum": "Momentum screen"}


def _num(fm: Any, name: str) -> Optional[float]:
    """The metric, or None when the provider did not report it."""
    avail = getattr(fm, "data_available", None) or {}
    if avail and avail.get(name) is False:
        return None
    try:
        v = float(getattr(fm, name, 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    return v if (avail.get(name) or v != 0.0) else None


def _check(key: str, label: str, value: Optional[float], rule: str, ok: bool, unit: str) -> Dict[str, Any]:
    return {"key": key, "label": label, "value": value, "rule": rule, "ok": bool(ok), "unit": unit}


def fcf_yield(fm: Any) -> Optional[float]:
    fcf, cap = _num(fm, "free_cash_flow"), _num(fm, "market_cap")
    if fcf is None or not cap or cap <= 0:
        return None
    return fcf / cap


def screen(style: str, fm: Any, sector: str = "", on_list: Optional[bool] = None,
           closes: Optional[Any] = None) -> Dict[str, Any]:
    """``{"style", "label", "passed", "checks": [...], "reason"}`` for one name.

    ``on_list`` is the dividend-growth list membership where the market has one
    (the S&P 500 Aristocrats); None means no such list, and a minimum yield is
    asked instead. ``closes`` (daily closes, oldest first) feeds the momentum screen.
    """
    checks: List[Dict[str, Any]] = []
    if style == "growth":
        rev, eps = _num(fm, "revenue_growth"), _num(fm, "earnings_growth")
        pe, fpe = _num(fm, "pe_ratio"), _num(fm, "forward_pe")
        checks.append(_check("revenue_growth", "Revenue growth", rev,
                             f"at least {STYLE.GROWTH_MIN_REVENUE_GROWTH:.0%}",
                             rev is not None and rev >= STYLE.GROWTH_MIN_REVENUE_GROWTH, "pct"))
        eps_ok = eps is not None and eps >= STYLE.GROWTH_MIN_EARNINGS_GROWTH
        fwd_ok = bool(pe and fpe and pe > 0 and 0 < fpe < pe)
        checks.append(_check("earnings_growth", "Earnings growth", eps,
                             f"at least {STYLE.GROWTH_MIN_EARNINGS_GROWTH:.0%}", eps_ok, "pct"))
        checks.append(_check("forward_eps", "Forward P/E below trailing (EPS expected to rise)",
                             fpe, f"below trailing {pe:.1f}" if pe else "trailing P/E needed", fwd_ok, "x"))
        passed = checks[0]["ok"] and (eps_ok or fwd_ok)
        reason = ("" if passed else
                  "revenue growth under the floor" if not checks[0]["ok"] else
                  "neither earnings growth nor forward EPS clears its rule")
    elif style == "value":
        pe, fpe = _num(fm, "pe_ratio"), _num(fm, "forward_pe")
        evx, pb = _num(fm, "ev_ebitda"), _num(fm, "pb_ratio")
        fy = fcf_yield(fm)
        earn = (fpe or 0) > 0 or (pe or 0) > 0
        # Free cash flow counts when the provider reports it; Yahoo reports none
        # for Indian companies or for banks, which used to fail every one of them
        # (ONGC at a forward P/E of 5). Unreported, profitability rests on earnings
        # and the free-cash-flow yield simply does not count as a cheap measure.
        profitable = earn and (fy is None or fy > 0)
        checks.append(_check("profitable", "Positive earnings and free cash flow"
                             if fy is not None else "Positive earnings (free cash flow not reported)",
                             fy, "positive", profitable, "pct"))
        use_pe = fpe if fpe and fpe > 0 else pe
        cheap = [
            _check("forward_pe", "Forward P/E" if fpe and fpe > 0 else "P/E", use_pe,
                   f"at most {STYLE.VALUE_MAX_FORWARD_PE:g}",
                   use_pe is not None and 0 < use_pe <= STYLE.VALUE_MAX_FORWARD_PE, "x"),
            _check("ev_ebitda", "EV/EBITDA", evx, f"at most {STYLE.VALUE_MAX_EV_EBITDA:g}",
                   evx is not None and 0 < evx <= STYLE.VALUE_MAX_EV_EBITDA, "x"),
            _check("fcf_yield", "Free-cash-flow yield", fy, f"at least {STYLE.VALUE_MIN_FCF_YIELD:.0%}",
                   fy is not None and fy >= STYLE.VALUE_MIN_FCF_YIELD, "pct"),
            _check("pb_ratio", "Price/book", pb, f"at most {STYLE.VALUE_MAX_PB:g}",
                   pb is not None and 0 < pb <= STYLE.VALUE_MAX_PB, "x"),
        ]
        checks.extend(cheap)
        hits = sum(1 for c in cheap if c["ok"])
        passed = profitable and hits >= STYLE.VALUE_MIN_PASSES
        reason = ("" if passed else "not profitable on earnings and free cash flow" if not profitable
                  else f"cheap on {hits} of 4 measures (needs {STYLE.VALUE_MIN_PASSES})")
    elif style == "dividend":
        dy, payout = _num(fm, "dividend_yield"), _num(fm, "payout_ratio")
        reit = "real estate" in str(sector or "").lower()
        if on_list is not None:
            checks.append(_check("aristocrat", "S&P 500 Dividend Aristocrat (25+ years of increases)",
                                 None, "on the list", bool(on_list), "flag"))
            checks.append(_check("dividend_yield", "Dividend yield", dy, "pays a dividend now",
                                 dy is not None and dy > 0, "pct"))
        else:
            checks.append(_check("dividend_yield", "Dividend yield", dy,
                                 f"at least {STYLE.DIVIDEND_MIN_YIELD_NO_LIST:.1%}",
                                 dy is not None and dy >= STYLE.DIVIDEND_MIN_YIELD_NO_LIST, "pct"))
        payout_ok = reit or payout is None or 0 <= payout <= STYLE.DIVIDEND_MAX_PAYOUT
        checks.append(_check("payout_ratio", "Payout ratio", payout,
                             "not used for REITs" if reit else f"at most {STYLE.DIVIDEND_MAX_PAYOUT:.0%} of earnings",
                             payout_ok, "pct"))
        passed = all(c["ok"] for c in checks)
        failed = next((c for c in checks if not c["ok"]), None)
        reason = "" if passed else f"{failed['label'].split(' (')[0].lower()}: {failed['rule']}"
    elif style == "momentum":
        series = [float(x) for x in (list(closes) if closes is not None else []) if x == x]
        n = STYLE.MOMENTUM_SESSIONS_3M
        if len(series) >= 200:
            last = series[-1]
            sma50, sma200 = sum(series[-50:]) / 50, sum(series[-200:]) / 200
            ret = last / series[-n - 1] - 1 if len(series) > n and series[-n - 1] > 0 else None
        else:
            last = sma50 = sma200 = ret = None
        trend_ok = bool(last and sma50 and sma200 and last > sma50 > sma200)
        checks.append(_check("trend", "Price above the 50-day average, above the 200-day", last,
                             "price > 50-day > 200-day", trend_ok, "price"))
        checks.append(_check("return_3m", "3-month return", ret,
                             f"at least {STYLE.MOMENTUM_MIN_RETURN_3M:.0%}",
                             ret is not None and ret >= STYLE.MOMENTUM_MIN_RETURN_3M, "pct"))
        passed = all(c["ok"] for c in checks)
        reason = ("" if passed else "not in an established uptrend" if not trend_ok
                  else "3-month return under the floor")
    else:
        return {"style": style, "label": "", "passed": True, "checks": [], "reason": ""}
    return {"style": style, "label": LABELS[style], "passed": bool(passed), "checks": checks, "reason": reason}
