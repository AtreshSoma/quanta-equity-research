"""Portfolio-level measurements, as pure functions.

Every measure reports its own coverage (the share of the portfolio's value it
could actually be computed on). A metric computed on 40% of the book is shown
as such, never extrapolated, and a metric with no inputs is ``None`` — not 0.

Thresholds come from :class:`PortfolioPolicy`; the defaults match the engine's
existing limits (``modules/portfolio_risk_manager.RiskLimits``: 10% single name,
30% sector, 0.70 correlation; ``engine.allocation``: 6% portfolio risk cap).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from engine.thresholds import PORTFOLIO as _PF

TRADING_DAYS = 252
MIN_RETURN_DAYS = 60          # below this, volatility/correlation are not reported


@dataclass(frozen=True)
class PortfolioPolicy:
    # Defaults come from engine.thresholds.PORTFOLIO (audit F9); a caller may
    # still pass its own policy.
    max_single_position: float = field(default_factory=lambda: _PF.MAX_SINGLE_POSITION)
    trim_review_position: float = field(default_factory=lambda: _PF.TRIM_REVIEW_POSITION)
    max_sector: float = field(default_factory=lambda: _PF.MAX_SECTOR)
    correlation_threshold: float = field(default_factory=lambda: _PF.CORRELATION_THRESHOLD)
    earnings_window_days: int = field(default_factory=lambda: _PF.EARNINGS_WINDOW_DAYS)
    stop_risk_budget: float = field(default_factory=lambda: _PF.STOP_RISK_BUDGET)
    min_add_value: float = field(default_factory=lambda: _PF.MIN_ADD_VALUE)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Holding:
    """One position joined with the market/QUANTA facts the analytics need."""
    symbol: str
    quantity: float
    value: Optional[float]                 # market value (broker quote)
    price: Optional[float] = None
    sector: str = ""
    industry: str = ""
    beta: Optional[float] = None           # market data
    earnings_date: Optional[str] = None    # ISO, next scheduled report
    stop: Optional[float] = None           # QUANTA model stop (not a placed order)
    unrealized_pnl: Optional[float] = None


def _weights(holdings: Sequence[Holding], total: float) -> Dict[str, float]:
    if not total or total <= 0:
        return {}
    return {h.symbol: (h.value or 0.0) / total for h in holdings if h.value is not None}


def total_value(holdings: Sequence[Holding], cash: Optional[float],
                reported_total: Optional[float]) -> Optional[float]:
    """The broker's total when it gave one; else positions + cash when complete."""
    if reported_total is not None and reported_total > 0:
        return reported_total
    if cash is None or any(h.value is None for h in holdings):
        return None
    return sum(h.value for h in holdings) + cash


def sector_allocation(holdings: Sequence[Holding], total: float,
                      cash: Optional[float]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for h in holdings:
        if h.value is None:
            continue
        key = h.sector or "Unclassified"
        b = buckets.setdefault(key, {"sector": key, "value": 0.0, "symbols": []})
        b["value"] += h.value
        b["symbols"].append(h.symbol)
    rows = sorted(buckets.values(), key=lambda r: -r["value"])
    if cash is not None and cash > 0:
        rows.append({"sector": "Cash", "value": cash, "symbols": []})
    for r in rows:
        r["weight"] = r["value"] / total if total else None
    return rows


def concentration(holdings: Sequence[Holding], total: float,
                  policy: PortfolioPolicy) -> Dict[str, Any]:
    weights = sorted(_weights(holdings, total).items(), key=lambda kv: -kv[1])
    invested = sum(w for _, w in weights)
    if not weights or invested <= 0:
        return {"top": None, "top5_weight": None, "hhi": None, "effective_names": None,
                "over_cap": [], "label": "No holdings"}
    # HHI on the invested book, so a large cash balance does not flatter it.
    shares = [w / invested for _, w in weights]
    hhi = sum(s * s for s in shares)
    eff = 1.0 / hhi if hhi else None
    if eff is None:
        label = "No holdings"
    elif eff < 5:
        label = "Concentrated"
    elif eff < 10:
        label = "Moderately diversified"
    else:
        label = "Diversified"
    return {
        "top": {"symbol": weights[0][0], "weight": weights[0][1]},
        "top5_weight": sum(w for _, w in weights[:5]),
        "hhi": hhi,
        "effective_names": eff,
        "over_cap": [{"symbol": s, "weight": w} for s, w in weights
                     if w >= policy.max_single_position],
        "label": label,
    }


def beta_exposure(holdings: Sequence[Holding], total: float) -> Dict[str, Any]:
    """Value-weighted beta over the holdings that have one; cash counts as 0."""
    covered = [h for h in holdings if h.value is not None and h.beta is not None]
    invested = sum(h.value for h in holdings if h.value is not None)
    cov_value = sum(h.value for h in covered)
    if not covered or cov_value <= 0:
        return {"equity_beta": None, "portfolio_beta": None, "coverage": 0.0}
    equity_beta = sum(h.value * h.beta for h in covered) / cov_value
    return {
        "equity_beta": equity_beta,
        # Beta of the whole account, cash at zero. Only meaningful at full coverage.
        "portfolio_beta": equity_beta * invested / total if total else None,
        "coverage": cov_value / invested if invested else 0.0,
    }


def volatility_and_correlation(holdings: Sequence[Holding], returns: Any,
                               policy: PortfolioPolicy) -> Dict[str, Any]:
    """Annualised volatility of the covered book and highly correlated pairs.

    ``returns`` is a pandas DataFrame of daily returns, one column per symbol
    (or None). Only columns with at least MIN_RETURN_DAYS overlapping days are
    used; coverage says how much of the invested value that is.
    """
    out: Dict[str, Any] = {"volatility": None, "coverage": 0.0, "days": 0,
                           "correlated_pairs": [], "avg_correlation": None}
    if returns is None or getattr(returns, "empty", True):
        return out
    priced = {h.symbol: h.value for h in holdings if h.value is not None and h.value > 0}
    cols = [c for c in returns.columns if c in priced and returns[c].notna().sum() >= MIN_RETURN_DAYS]
    if not cols:
        return out
    frame = returns[cols].dropna()
    if len(frame) < MIN_RETURN_DAYS:
        return out
    invested = sum(priced.values())
    cov_value = sum(priced[c] for c in cols)
    w = [priced[c] / cov_value for c in cols]
    port = (frame * w).sum(axis=1)
    vol = float(port.std(ddof=1) * math.sqrt(TRADING_DAYS))
    out.update(volatility=vol if math.isfinite(vol) else None,
               coverage=cov_value / invested if invested else 0.0, days=int(len(frame)))
    if len(cols) >= 2:
        corr = frame.corr()
        pairs, vals = [], []
        for i, a in enumerate(cols):
            for b in cols[i + 1:]:
                c = float(corr.loc[a, b])
                if not math.isfinite(c):
                    continue
                vals.append(c)
                if c >= policy.correlation_threshold:
                    pairs.append({"a": a, "b": b, "correlation": c})
        out["correlated_pairs"] = sorted(pairs, key=lambda p: -p["correlation"])
        out["avg_correlation"] = sum(vals) / len(vals) if vals else None
    return out


def correlated_with(symbol: str, pairs: Sequence[Dict[str, Any]]) -> List[str]:
    out = []
    for p in pairs:
        if p["a"] == symbol:
            out.append(p["b"])
        elif p["b"] == symbol:
            out.append(p["a"])
    return out


def stop_exposure(holdings: Sequence[Holding], total: float) -> Dict[str, Any]:
    """Loss if every holding fell to its QUANTA stop from today's price.

    Stops are the model's levels, not orders placed at the broker, and a gap
    through a stop loses more. Holdings already below their stop contribute 0
    and are listed.
    """
    covered = [h for h in holdings if h.value is not None and h.price and h.stop]
    invested = sum(h.value for h in holdings if h.value is not None)
    loss, below = 0.0, []
    for h in covered:
        if h.stop >= h.price:
            below.append(h.symbol)
            continue
        loss += h.quantity * (h.price - h.stop)
    cov_value = sum(h.value for h in covered)
    return {
        "loss": loss if covered else None,
        "loss_pct": (loss / total) if covered and total else None,
        "coverage": cov_value / invested if invested else 0.0,
        "below_stop": below,
    }


def earnings_exposure(holdings: Sequence[Holding], total: float, today: date,
                      window_days: int = 14) -> Dict[str, Any]:
    upcoming = []
    for h in holdings:
        if not h.earnings_date or h.value is None:
            continue
        try:
            d = date.fromisoformat(h.earnings_date[:10])
        except ValueError:
            continue
        days = (d - today).days
        if 0 <= days <= window_days:
            upcoming.append({"symbol": h.symbol, "date": d.isoformat(), "days": days,
                             "weight": h.value / total if total else None})
    upcoming.sort(key=lambda r: r["days"])
    known = sum(1 for h in holdings if h.earnings_date)
    return {
        "window_days": window_days,
        "upcoming": upcoming,
        "weight": sum(r["weight"] or 0 for r in upcoming) if total else None,
        "dates_known": known,
    }


def risk_level(conc: Dict[str, Any], sectors: List[Dict[str, Any]], beta: Dict[str, Any],
               vol: Dict[str, Any], stops: Dict[str, Any]) -> Dict[str, Any]:
    """A summary label with every driver that produced it, so it can be audited."""
    drivers: List[str] = []
    points = 0
    top = (conc.get("top") or {}).get("weight")
    if top is not None:
        if top >= 0.20:
            points += 2
            drivers.append(f"Largest position is {top:.0%} of the portfolio")
        elif top >= 0.10:
            points += 1
            drivers.append(f"Largest position is {top:.0%} of the portfolio")
    equity_sectors = [s for s in sectors if s["sector"] not in ("Cash", "Unclassified")]
    if equity_sectors and equity_sectors[0].get("weight") is not None:
        sw = equity_sectors[0]["weight"]
        if sw >= 0.40:
            points += 2
            drivers.append(f"{equity_sectors[0]['sector']} is {sw:.0%} of the portfolio")
        elif sw >= 0.30:
            points += 1
            drivers.append(f"{equity_sectors[0]['sector']} is {sw:.0%} of the portfolio")
    b = beta.get("equity_beta")
    if b is not None and beta.get("coverage", 0) >= 0.5:
        if b >= 1.3:
            points += 2
            drivers.append(f"Holdings beta {b:.2f}")
        elif b >= 1.1:
            points += 1
            drivers.append(f"Holdings beta {b:.2f}")
    v = vol.get("volatility")
    if v is not None and vol.get("coverage", 0) >= 0.5:
        if v >= 0.30:
            points += 2
            drivers.append(f"Annualised volatility {v:.0%}")
        elif v >= 0.20:
            points += 1
            drivers.append(f"Annualised volatility {v:.0%}")
    sl = stops.get("loss_pct")
    if sl is not None and sl >= 0.10:
        points += 1
        drivers.append(f"Loss to stops would be {sl:.0%} of the portfolio")
    assessed = (top is not None) and (b is not None or v is not None)
    if not assessed:
        return {"level": "Not assessed", "points": None, "drivers": drivers,
                "note": "Not enough beta or volatility data to grade portfolio risk."}
    level = ("Low" if points <= 1 else "Moderate" if points <= 3
             else "Elevated" if points <= 5 else "High")
    return {"level": level, "points": points, "drivers": drivers, "note": ""}


def change_impact(holdings: Sequence[Holding], total: float, cash: Optional[float],
                  symbol: str, delta_value: float) -> Dict[str, Any]:
    """What adding (``delta_value`` > 0) or reducing (< 0) one holding does.

    Buying is funded from cash and selling adds to cash, so the total is
    unchanged; only the mix moves.
    """
    target = next((h for h in holdings if h.symbol == symbol), None)
    if target is None or target.value is None or not total:
        return {}
    new_value = max(0.0, target.value + delta_value)
    sector = target.sector or "Unclassified"
    sector_now = sum(h.value or 0 for h in holdings if (h.sector or "Unclassified") == sector)
    sector_new = sector_now - target.value + new_value
    out = {
        "symbol": symbol,
        "delta_value": new_value - target.value,
        "weight_before": target.value / total,
        "weight_after": new_value / total,
        "sector": sector,
        "sector_weight_before": sector_now / total,
        "sector_weight_after": sector_new / total,
        "cash_after": None if cash is None else cash - (new_value - target.value),
    }
    betas = [(h, h.beta) for h in holdings if h.value is not None and h.beta is not None]
    if target.beta is not None and betas:
        before = sum(h.value * b for h, b in betas) / total
        after = before + (new_value - target.value) * target.beta / total
        out["portfolio_beta_before"] = before
        out["portfolio_beta_after"] = after
    return out


@dataclass
class PortfolioMetrics:
    total_value: Optional[float]
    invested_value: Optional[float]
    cash: Optional[float]
    cash_weight: Optional[float]
    weights: Dict[str, float]
    sectors: List[Dict[str, Any]]
    concentration: Dict[str, Any]
    beta: Dict[str, Any]
    volatility: Dict[str, Any]
    stops: Dict[str, Any]
    earnings: Dict[str, Any]
    risk: Dict[str, Any]
    unrealized_pnl: Dict[str, Any]
    day_change: Optional[float] = None
    policy: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_metrics(holdings: Sequence[Holding], cash: Optional[float],
                    reported_total: Optional[float], returns: Any = None,
                    policy: Optional[PortfolioPolicy] = None,
                    today: Optional[date] = None,
                    day_change: Optional[float] = None) -> PortfolioMetrics:
    policy = policy or PortfolioPolicy()
    today = today or date.today()
    total = total_value(holdings, cash, reported_total)
    priced = [h for h in holdings if h.value is not None]
    invested = sum(h.value for h in priced) if priced else (0.0 if not holdings else None)
    t = total or 0.0
    sectors = sector_allocation(holdings, t, cash)
    conc = concentration(holdings, t, policy)
    beta = beta_exposure(holdings, t)
    vol = volatility_and_correlation(holdings, returns, policy)
    stops = stop_exposure(holdings, t)
    earnings = earnings_exposure(holdings, t, today)
    with_basis = [h for h in holdings if h.unrealized_pnl is not None]
    pnl_value = sum(h.value or 0 for h in with_basis)
    unrealized = {
        "total": sum(h.unrealized_pnl for h in with_basis) if with_basis else None,
        "coverage": pnl_value / invested if invested else 0.0,
        "positions_missing_basis": [h.symbol for h in holdings if h.unrealized_pnl is None],
    }
    return PortfolioMetrics(
        total_value=total,
        invested_value=invested,
        cash=cash,
        cash_weight=(cash / total) if (cash is not None and total) else None,
        weights=_weights(holdings, t),
        sectors=sectors,
        concentration=conc,
        beta=beta,
        volatility=vol,
        stops=stops,
        earnings=earnings,
        risk=risk_level(conc, sectors, beta, vol, stops),
        unrealized_pnl=unrealized,
        day_change=day_change,
        policy=policy.to_dict(),
    )
