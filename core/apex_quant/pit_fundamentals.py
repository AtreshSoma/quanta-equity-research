"""Point-in-time fundamentals from SEC EDGAR XBRL filings.

Why this exists
---------------
Every out-of-sample test before this one held the fundamental, valuation and
quality legs at a neutral constant, because yfinance only serves *today's*
figures. Scoring a 2015 bar with 2026 fundamentals is look-ahead bias, so those
legs were left out, and the "no edge" verdicts covered only the price legs.

SEC's ``companyfacts`` API carries every figure a company has tagged in its
10-K and 10-Q filings, each stamped with the date it was **filed**. Reading only
facts filed before a given day reconstructs what the market could have known on
that day, including restatements that had been filed by then and none after.

What it produces
----------------
``PITFundamentals.info_at(day, price)`` returns a dict in yfinance ``info``
units (fractions for margins and growth, percent for debt/equity), holding
only the keys that were actually reported, so the engines' own scoring code
reads it exactly as it reads a live payload. ``statements_at(day)`` returns the
annual balance-sheet and income rows the ELITE quality leg's Altman Z reads.

Definitions (stated, because each is a modelling choice)
--------------------------------------------------------
* A fact filed on day F is usable from day F + 1 (filings often land after the close).
* Flow figures are rebuilt as discrete quarters from the cumulative year-to-date
  values companies report (Q2 = H1 - Q1, Q4 = FY - 9M). TTM is the sum of the
  last four contiguous quarters.
* ``revenueGrowth``, ``earningsGrowth`` and ``earningsQuarterlyGrowth`` are the
  latest quarter against the same quarter a year earlier, as yfinance defines them.
* Figures whose latest period ended more than ``MAX_STALENESS_DAYS`` before the
  day are treated as unreported, never carried forward indefinitely.
* Shares are diluted weighted-average shares (as-converted, so multi-class
  companies are counted consistently), converted to today's split-adjusted units
  using the splits after each figure's filing date, to match split-adjusted prices.
* Debt is long-term debt plus current debt and short-term borrowings. Operating
  leases are excluded. Negative equity leaves ROE and debt/equity unreported,
  as the live engine already does for a negative yfinance figure.

Not reconstructible here, and so still held neutral by the callers: analyst
targets and ratings, forward EPS, short interest, news sentiment and insider flow.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import time
import urllib.request
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

SEC_CACHE_DIR = Path(__file__).resolve().parent / "_cache_research" / "sec"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
# A cached companyfacts file is reused for this long; history before it never changes.
FACTS_MAX_AGE_DAYS = 7

MAX_STALENESS_DAYS = 200
QUARTER_DAYS = (70, 110)
YEAR_DAYS = (350, 380)

# Concept aliases, highest priority first. Companies switch tags over the years
# (Apple: SalesRevenueNet -> Revenues -> RevenueFromContractWithCustomer...).
FLOW_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RevenuesNetOfInterestExpense",
        "SalesRevenueGoodsNet",
    ),
    "net_income": ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "operating_income": ("OperatingIncomeLoss",),
    "cfo": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
}
SHARE_CONCEPTS: Tuple[str, ...] = ("WeightedAverageNumberOfDilutedSharesOutstanding",)
# No company in a large-cap universe has fewer shares than this; a smaller
# figure is a filing tagged in millions or thousands, not a share count.
MIN_PLAUSIBLE_SHARES = 1e6
# Companies whose earlier filings sit under another CIK (a new holding company).
PREDECESSOR_CIKS: Dict[str, Tuple[int, ...]] = {
    "GOOGL": (1288776,),   # Google Inc., before Alphabet Inc. (October 2015)
    "GOOG": (1288776,),
}
INSTANT_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "equity": ("StockholdersEquity",),
    "equity_total": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                     "StockholdersEquity"),
    "assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "liabilities": ("Liabilities",),
    "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "lt_debt_noncurrent": ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"),
    "lt_debt_total": ("LongTermDebt",),
    "lt_debt_current": ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"),
    "debt_current": ("DebtCurrent",),
    "short_borrowings": ("ShortTermBorrowings",),
    "commercial_paper": ("CommercialPaper",),
}


# ---------------------------------------------------------------------------
# Fact parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    start: Optional[pd.Timestamp]
    end: pd.Timestamp
    val: float
    filed: pd.Timestamp
    priority: int  # index of the concept in its alias list (0 = preferred)

    @property
    def days(self) -> int:
        return (self.end - self.start).days if self.start is not None else 0


def _facts_for(facts: Dict[str, Any], concepts: Iterable[str], unit: str,
               taxonomy: str = "us-gaap") -> List[Fact]:
    out: List[Fact] = []
    tax = (facts.get("facts") or {}).get(taxonomy) or {}
    for priority, concept in enumerate(concepts):
        rows = ((tax.get(concept) or {}).get("units") or {}).get(unit) or []
        for r in rows:
            try:
                val = float(r["val"])
                end = pd.Timestamp(r["end"])
                filed = pd.Timestamp(r["filed"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(val):
                continue
            start = pd.Timestamp(r["start"]) if r.get("start") else None
            out.append(Fact(start, end, val, filed, priority))
    out.sort(key=lambda f: f.filed)
    return out


def _known(facts: List[Fact], cutoff: pd.Timestamp) -> List[Fact]:
    """Facts filed strictly before ``cutoff`` (facts are sorted by filing date)."""
    filed = [f.filed for f in facts]
    return facts[: bisect_left(filed, cutoff)]


def _latest_by_period(facts: List[Fact]) -> Dict[Tuple[Optional[pd.Timestamp], pd.Timestamp], Fact]:
    """One value per (start, end): the preferred concept, then the latest filing.

    The latest filing wins so a restatement filed before the cutoff replaces the
    original figure, as it did for anyone reading the filings at the time.
    """
    best: Dict[Tuple[Optional[pd.Timestamp], pd.Timestamp], Fact] = {}
    for f in facts:
        key = (f.start, f.end)
        cur = best.get(key)
        if cur is None or (f.priority, -f.filed.value) < (cur.priority, -cur.filed.value):
            best[key] = f
    return best


def quarterly_series(facts: List[Fact]) -> pd.Series:
    """Discrete quarterly values indexed by quarter end, from YTD and quarterly facts.

    Facts sharing a start date are one fiscal year's cumulative figures; their
    successive differences are the quarters. A directly reported three-month
    figure wins over a derived one for the same quarter.
    """
    periods = _latest_by_period([f for f in facts if f.start is not None])
    by_start: Dict[pd.Timestamp, List[Fact]] = {}
    for f in periods.values():
        by_start.setdefault(f.start, []).append(f)  # type: ignore[arg-type]
    direct: Dict[pd.Timestamp, float] = {}
    derived: Dict[pd.Timestamp, float] = {}
    for group in by_start.values():
        group.sort(key=lambda f: f.end)
        prev: Optional[Fact] = None
        for f in group:
            if QUARTER_DAYS[0] <= f.days <= QUARTER_DAYS[1]:
                direct[f.end] = f.val
            elif prev is not None:
                gap = (f.end - prev.end).days
                if QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1]:
                    derived.setdefault(f.end, f.val - prev.val)
            prev = f
    merged = dict(derived)
    merged.update(direct)
    return pd.Series(merged, dtype=float).sort_index()


def _ttm(q: pd.Series) -> Optional[Tuple[pd.Timestamp, float]]:
    """(latest quarter end, sum of the last four contiguous quarters)."""
    if len(q) < 4:
        return None
    last4 = q.iloc[-4:]
    span = (last4.index[-1] - last4.index[0]).days
    if not (250 <= span <= 300):
        return None
    return last4.index[-1], float(last4.sum())


def _year_ago(q: pd.Series) -> Optional[Tuple[float, float]]:
    """(latest quarter, same quarter a year earlier)."""
    if len(q) < 2:
        return None
    end = q.index[-1]
    target = end - pd.Timedelta(days=364)
    diffs = [abs((d - target).days) for d in q.index[:-1]]
    if not diffs:
        return None
    i = min(range(len(diffs)), key=diffs.__getitem__)
    if diffs[i] > 20:
        return None
    return float(q.iloc[-1]), float(q.iloc[i])


def _latest_annual(facts: List[Fact]) -> Optional[Fact]:
    periods = [f for f in _latest_by_period([f for f in facts if f.start is not None]).values()
               if YEAR_DAYS[0] <= f.days <= YEAR_DAYS[1]]
    return max(periods, key=lambda f: f.end) if periods else None


def _latest_instant(facts: List[Fact], at: Optional[pd.Timestamp] = None) -> Optional[Fact]:
    """The latest balance-sheet value (or the one at period end ``at``)."""
    periods = _latest_by_period([f for f in facts if f.start is None])
    if not periods:
        return None
    if at is not None:
        hits = [f for (s, e), f in periods.items() if e == at]
        return hits[0] if hits else None
    return max(periods.values(), key=lambda f: f.end)


# ---------------------------------------------------------------------------
# Splits: price series are split-adjusted, filings are not
# ---------------------------------------------------------------------------


class SplitAdjuster:
    """Converts a share count reported in a filing to today's split-adjusted units."""

    def __init__(self, splits: Dict[str, float]):
        self._splits = sorted((pd.Timestamp(d), float(r)) for d, r in splits.items() if r and r > 0)

    def factor_after(self, day: pd.Timestamp) -> float:
        """Product of split ratios for splits strictly after ``day``."""
        out = 1.0
        for d, r in self._splits:
            if d > day:
                out *= r
        return out

    def shares(self, fact: Fact) -> float:
        # A filing states shares in the units of its own filing date: splits
        # before it are already applied (restated comparatives included).
        return fact.val * self.factor_after(fact.filed)


# ---------------------------------------------------------------------------
# One company's reconstruction
# ---------------------------------------------------------------------------


class PITFundamentals:
    """Point-in-time fundamentals for one company."""

    def __init__(self, ticker: str, companyfacts: Dict[str, Any], splits: Dict[str, float],
                 sector: str = "", industry: str = ""):
        self.ticker = ticker
        self.sector = sector or ""
        self.industry = industry or ""
        self.splits = SplitAdjuster(splits)
        self._flows = {k: _facts_for(companyfacts, c, "USD") for k, c in FLOW_CONCEPTS.items()}
        self._shares = [f for f in _facts_for(companyfacts, SHARE_CONCEPTS, "shares")
                        if f.val >= MIN_PLAUSIBLE_SHARES]
        # Cover-page shares outstanding, summed across share classes per filing:
        # the fallback when diluted shares are only tagged per class (Alphabet).
        self._cover_shares = _cover_shares(companyfacts)
        # Diluted EPS, to imply diluted shares as net income / EPS for a period
        # when the share count itself is only tagged per class.
        self._eps = _facts_for(companyfacts, ("EarningsPerShareDiluted",), "USD/shares")
        self._balance_shares = [f for f in _facts_for(companyfacts, ("CommonStockSharesOutstanding",),
                                                      "shares") if f.val >= MIN_PLAUSIBLE_SHARES]
        self._instants = {k: _facts_for(companyfacts, c, "USD") for k, c in INSTANT_CONCEPTS.items()}
        filed = sorted({f.filed for fs in list(self._flows.values()) + list(self._instants.values())
                        for f in fs})
        self._filing_dates = filed
        self._cache: Dict[Optional[pd.Timestamp], Dict[str, Any]] = {}

    @property
    def first_filed(self) -> Optional[pd.Timestamp]:
        return self._filing_dates[0] if self._filing_dates else None

    def _state(self, day: pd.Timestamp) -> Optional[pd.Timestamp]:
        """The latest filing date usable on ``day`` (the cache key)."""
        i = bisect_left(self._filing_dates, pd.Timestamp(day).normalize())
        return self._filing_dates[i - 1] if i > 0 else None

    def _base(self, day: pd.Timestamp) -> Dict[str, Any]:
        """Price-independent figures known on ``day``, cached per filing state."""
        state = self._state(day)
        if state in self._cache:
            return self._cache[state]
        base = self._compute_base(state + pd.Timedelta(days=1)) if state is not None else {}
        self._cache[state] = base
        return base

    def _compute_base(self, cutoff: pd.Timestamp) -> Dict[str, Any]:
        """Everything that does not depend on price, from facts filed before ``cutoff``."""
        limit = cutoff - pd.Timedelta(days=MAX_STALENESS_DAYS)
        base: Dict[str, Any] = {"_cutoff": cutoff}

        flows: Dict[str, pd.Series] = {}
        for key, facts in self._flows.items():
            q = quarterly_series(_known(facts, cutoff))
            if len(q) and q.index[-1] >= limit:
                flows[key] = q
        ttm = {k: _ttm(q) for k, q in flows.items()}
        ttm = {k: v for k, v in ttm.items() if v is not None}
        latest_q = max((v[0] for v in ttm.values()), default=None)
        # Every TTM figure must describe the same latest quarter.
        ttm = {k: v[1] for k, v in ttm.items() if v[0] == latest_q}
        base["ttm"] = ttm
        base["latest_quarter"] = latest_q

        yoy = {k: _year_ago(flows[k]) for k in ("revenue", "net_income") if k in flows}
        base["yoy"] = {k: v for k, v in yoy.items()
                       if v is not None and flows[k].index[-1] == latest_q}

        shares_known = [f for f in _known(self._shares, cutoff) if f.end >= limit]
        if shares_known:
            latest_end = max(f.end for f in shares_known)
            at_end = [f for f in shares_known if f.end == latest_end]
            # Prefer the quarter's own average over a year-to-date average.
            pick = min(at_end, key=lambda f: (f.days if f.days >= QUARTER_DAYS[0] else 10_000,
                                              f.priority, -f.filed.value))
            base["shares"] = self.splits.shares(pick)
            year_ago = [f for f in _known(self._shares, cutoff)
                        if abs((f.end - (latest_end - pd.Timedelta(days=364))).days) <= 20
                        and QUARTER_DAYS[0] <= f.days <= QUARTER_DAYS[1]]
            if year_ago:
                prior = max(year_ago, key=lambda f: f.filed)
                base["shares_year_ago"] = self.splits.shares(prior)
        else:
            implied = self._implied_shares(cutoff, limit)
            if implied is not None:
                base["shares"], base["shares_year_ago"] = implied
                if base["shares_year_ago"] is None:
                    del base["shares_year_ago"]
            else:
                for source in (self._cover_shares, self._balance_shares):
                    known = [f for f in _known(source, cutoff) if f.end >= limit]
                    if known:
                        base["shares"] = self.splits.shares(max(known, key=lambda f: (f.end, f.filed)))
                        break

        inst: Dict[str, Fact] = {}
        for key, facts in self._instants.items():
            f = _latest_instant(_known(facts, cutoff))
            if f is not None and f.end >= limit:
                inst[key] = f
        base["instant"] = {k: f.val for k, f in inst.items()}
        base["instant_end"] = {k: f.end for k, f in inst.items()}

        # Annual statements for Altman Z, as yfinance's annual balance_sheet /
        # financials expose them: the latest fiscal year's figures.
        annual: Dict[str, float] = {}
        rev_y = _latest_annual(_known(self._flows["revenue"], cutoff))
        if rev_y is not None and rev_y.end >= cutoff - pd.Timedelta(days=MAX_STALENESS_DAYS + 365):
            fy_end = rev_y.end
            annual["revenue"] = rev_y.val
            op_y = _latest_annual(_known(self._flows["operating_income"], cutoff))
            if op_y is not None and op_y.end == fy_end:
                annual["ebit"] = op_y.val
            for key in ("assets", "current_assets", "current_liabilities", "retained_earnings",
                        "equity", "liabilities", "liabilities_and_equity", "equity_total"):
                f = _latest_instant(_known(self._instants[key], cutoff), at=fy_end)
                if f is not None:
                    annual[key] = f.val
            if "liabilities" not in annual and {"liabilities_and_equity", "equity_total"} <= annual.keys():
                annual["liabilities"] = annual["liabilities_and_equity"] - annual["equity_total"]
        base["annual"] = annual
        return base

    def _implied_shares(self, cutoff: pd.Timestamp,
                        limit: pd.Timestamp) -> Optional[Tuple[float, Optional[float]]]:
        """Diluted shares as net income / diluted EPS over the same period.

        Returns (latest, a year earlier or None), in today's split-adjusted units.
        """
        eps = _latest_by_period(_known(self._eps, cutoff))
        ni = _latest_by_period(_known(self._flows["net_income"], cutoff))
        pairs = []
        for key, e in eps.items():
            n = ni.get(key)
            if n is None or e.start is None or abs(e.val) < 0.01 or e.end < limit - pd.Timedelta(days=400):
                continue
            shares = n.val / e.val
            if shares < MIN_PLAUSIBLE_SHARES:
                continue
            # EPS is stated in the units of its own filing; scale the count to today's.
            pairs.append((e.end, e.days, shares * self.splits.factor_after(e.filed)))
        if not pairs:
            return None
        latest_end = max(p[0] for p in pairs)
        if latest_end < limit:
            return None
        latest = min((p for p in pairs if p[0] == latest_end), key=lambda p: p[1])[2]
        target = latest_end - pd.Timedelta(days=364)
        prior = [p for p in pairs if abs((p[0] - target).days) <= 20
                 and QUARTER_DAYS[0] <= p[1] <= QUARTER_DAYS[1]]
        return latest, (prior[0][2] if prior else None)

    # -- public -------------------------------------------------------------

    def filing_state(self, day: pd.Timestamp) -> Optional[str]:
        """The latest filing date in use on ``day``, for audit columns."""
        s = self._state(day)
        return s.date().isoformat() if s is not None else None

    def info_at(self, day: pd.Timestamp, price: float) -> Dict[str, Any]:
        """A yfinance-style ``info`` dict as it could have been built on ``day``.

        ``price`` must be in split-adjusted units (the research price series).
        Only reported keys are present; an empty dict means nothing was known.
        """
        day = pd.Timestamp(day).normalize()
        base = self._base(day)
        ttm, inst = base.get("ttm") or {}, base.get("instant") or {}
        if not ttm and not inst:
            return {}
        # A company that stopped filing must not keep its last figures forever.
        newest = max([base.get("latest_quarter") or pd.Timestamp.min]
                     + list((base.get("instant_end") or {}).values()))
        if (day - newest).days > MAX_STALENESS_DAYS:
            return {}
        info: Dict[str, Any] = {"quoteType": "EQUITY", "symbol": self.ticker}
        if self.sector:
            info["sector"] = self.sector
        if self.industry:
            info["industry"] = self.industry

        def put(key: str, value: Optional[float]) -> None:
            if value is not None and math.isfinite(value):
                info[key] = float(value)

        rev, ni = ttm.get("revenue"), ttm.get("net_income")
        put("totalRevenue", rev)
        put("netIncomeToCommon", ni)
        if rev and rev > 0:
            if ni is not None:
                put("profitMargins", ni / rev)
            gp = ttm.get("gross_profit")
            if gp is None and ttm.get("cost_of_revenue") is not None:
                gp = rev - ttm["cost_of_revenue"]
            if gp is not None:
                put("grossMargins", gp / rev)
            if ttm.get("operating_income") is not None:
                put("operatingMargins", ttm["operating_income"] / rev)

        equity = inst.get("equity")
        if ni is not None and equity is not None and equity > 0:
            put("returnOnEquity", ni / equity)
        if ni is not None and inst.get("assets"):
            put("returnOnAssets", ni / inst["assets"])

        cfo = ttm.get("cfo")
        put("operatingCashflow", cfo)
        if cfo is not None and ttm.get("capex") is not None:
            put("freeCashflow", cfo - abs(ttm["capex"]))

        if inst.get("current_liabilities"):
            if inst.get("current_assets") is not None:
                put("currentRatio", inst["current_assets"] / inst["current_liabilities"])

        debt = _total_debt(inst)
        if debt is not None and equity is not None and equity > 0:
            put("debtToEquity", debt / equity * 100.0)  # yfinance reports percent
        put("totalDebt", debt)

        yoy = base.get("yoy") or {}
        if "revenue" in yoy and yoy["revenue"][1] > 0:
            put("revenueGrowth", yoy["revenue"][0] / yoy["revenue"][1] - 1.0)
        if "net_income" in yoy and yoy["net_income"][1] > 0:
            now_q, then_q = yoy["net_income"]
            put("earningsQuarterlyGrowth", now_q / then_q - 1.0)
            if base.get("shares") and base.get("shares_year_ago"):
                eps_now = now_q / base["shares"]
                eps_then = then_q / base["shares_year_ago"]
                if eps_then > 0:
                    put("earningsGrowth", eps_now / eps_then - 1.0)

        shares = base.get("shares")
        if shares and shares > 0 and price and price > 0:
            put("sharesOutstanding", shares)
            mcap = price * shares
            put("marketCap", mcap)
            if ni is not None:
                eps = ni / shares
                put("trailingEps", eps)
                if eps > 0:
                    put("trailingPE", price / eps)
            if equity is not None and equity > 0:
                bvps = equity / shares
                put("bookValue", bvps)
                put("priceToBook", price / bvps)
            if rev and rev > 0:
                put("priceToSalesTrailing12Months", mcap / rev)
        return info

    def statements_at(self, day: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """(balance_sheet, financials) for the latest fiscal year known on ``day``,
        shaped like yfinance's annual frames (rows named as it names them)."""
        a = (self._base(day).get("annual") or {})
        bs_rows = {
            "Total Assets": a.get("assets"),
            "Current Assets": a.get("current_assets"),
            "Current Liabilities": a.get("current_liabilities"),
            "Retained Earnings": a.get("retained_earnings"),
            "Stockholders Equity": a.get("equity"),
            "Total Liabilities Net Minority Interest": a.get("liabilities"),
        }
        fin_rows = {"Operating Income": a.get("ebit"), "Total Revenue": a.get("revenue")}

        def frame(rows: Dict[str, Optional[float]]) -> pd.DataFrame:
            rows = {k: v for k, v in rows.items() if v is not None}
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame({"latest": rows})

        return frame(bs_rows), frame(fin_rows)


def _cover_shares(companyfacts: Dict[str, Any]) -> List[Fact]:
    """dei EntityCommonStockSharesOutstanding, one total per filing (classes summed)."""
    per_filing: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rows = ((((companyfacts.get("facts") or {}).get("dei") or {})
             .get("EntityCommonStockSharesOutstanding") or {}).get("units") or {}).get("shares") or []
    for r in rows:
        try:
            key = (str(r["accn"]), str(r["end"]))
            val = float(r["val"])
        except (KeyError, TypeError, ValueError):
            continue
        slot = per_filing.setdefault(key, {"end": r["end"], "filed": r.get("filed"), "val": 0.0})
        slot["val"] += val
    out = []
    for slot in per_filing.values():
        if slot["val"] >= MIN_PLAUSIBLE_SHARES and slot["filed"]:
            out.append(Fact(None, pd.Timestamp(slot["end"]), slot["val"],
                            pd.Timestamp(slot["filed"]), 0))
    out.sort(key=lambda f: f.filed)
    return out


def merge_companyfacts(primary: Dict[str, Any], *older: Dict[str, Any]) -> Dict[str, Any]:
    """Facts of a company and its predecessors, concept by concept."""
    merged: Dict[str, Any] = {"facts": {}}
    for doc in (primary, *older):
        for tax, concepts in (doc.get("facts") or {}).items():
            mt = merged["facts"].setdefault(tax, {})
            for concept, body in concepts.items():
                mc = mt.setdefault(concept, {"units": {}})
                for unit, rows in (body.get("units") or {}).items():
                    mc["units"].setdefault(unit, []).extend(rows)
    return merged


def _total_debt(inst: Dict[str, float]) -> Optional[float]:
    long_part = inst.get("lt_debt_noncurrent")
    if long_part is None and inst.get("lt_debt_total") is not None:
        long_part = inst["lt_debt_total"] - inst.get("lt_debt_current", 0.0)
    if inst.get("debt_current") is not None:
        short_part: Optional[float] = inst["debt_current"]
    else:
        parts = [inst.get(k) for k in ("lt_debt_current", "short_borrowings", "commercial_paper")]
        parts = [p for p in parts if p is not None]
        short_part = sum(parts) if parts else None
    if long_part is None and short_part is None:
        return None
    return max(0.0, (long_part or 0.0) + (short_part or 0.0))


# ---------------------------------------------------------------------------
# Fetching and caching
# ---------------------------------------------------------------------------


def _sec_get(url: str) -> bytes:
    """GET from SEC with the configured contact (required) and SEC's pacing."""
    from portfolio.sources import SSL_CONTEXT, _agent, _pace

    req = urllib.request.Request(url, headers={"User-Agent": _agent(True),
                                               "Accept": "application/json"})
    last: Optional[Exception] = None
    for attempt in range(3):
        _pace()
        try:
            with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:  # noqa: S310
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - retried, then raised
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"SEC request failed: {url}: {last}")


def _fresh(path: Path, max_age_days: float) -> bool:
    return path.is_file() and (time.time() - path.stat().st_mtime) < max_age_days * 86400


def _ticker_map(cache_dir: Path, get: Callable[[str], bytes]) -> Dict[str, int]:
    path = cache_dir / "company_tickers.json"
    if not _fresh(path, 30):
        path.write_bytes(get(TICKER_MAP_URL))
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}


def _sec_symbol(ticker: str) -> str:
    return ticker.upper().replace(".", "-")


def _yf_meta(ticker: str, cache_dir: Path) -> Dict[str, Any]:
    """Splits and sector/industry from yfinance, cached. Splits are history, not forecasts."""
    path = cache_dir / f"{ticker.upper()}_meta.json"
    if _fresh(path, FACTS_MAX_AGE_DAYS):
        return json.loads(path.read_text(encoding="utf-8"))
    import yfinance as yf

    t = yf.Ticker(ticker)
    splits = {}
    try:
        s = t.splits
        splits = {pd.Timestamp(d).tz_localize(None).date().isoformat(): float(r)
                  for d, r in s.items() if r and r > 0}
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: split history unavailable: %s", ticker, exc)
    sector = industry = ""
    try:
        info = t.info or {}
        sector, industry = info.get("sector") or "", info.get("industry") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: sector unavailable: %s", ticker, exc)
    meta = {"splits": splits, "sector": sector, "industry": industry}
    path.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def load_pit_fundamentals(ticker: str, cache_dir: Path = SEC_CACHE_DIR,
                          get: Callable[[str], bytes] = _sec_get) -> Optional[PITFundamentals]:
    """Fetch (or read cached) SEC facts and split history for ``ticker``.

    Returns None when SEC has no XBRL facts for it (a fund, a foreign filer).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cik = _ticker_map(cache_dir, get).get(_sec_symbol(ticker))
    if cik is None:
        logger.warning("%s: no SEC CIK", ticker)
        return None
    def facts_for(number: int) -> Dict[str, Any]:
        path = cache_dir / f"CIK{number:010d}.json.gz"
        if not _fresh(path, FACTS_MAX_AGE_DAYS):
            path.write_bytes(gzip.compress(get(FACTS_URL.format(cik=number))))
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))

    facts = facts_for(cik)
    if not (facts.get("facts") or {}).get("us-gaap"):
        return None
    older = [facts_for(c) for c in PREDECESSOR_CIKS.get(ticker.upper(), ())]
    if older:
        facts = merge_companyfacts(facts, *older)
    meta = _yf_meta(ticker, cache_dir)
    return PITFundamentals(ticker, facts, meta.get("splits") or {},
                           sector=meta.get("sector", ""), industry=meta.get("industry", ""))


# ---------------------------------------------------------------------------
# Engine adapters
# ---------------------------------------------------------------------------


class _PITStock:
    """The few ``yf.Ticker`` attributes ``FundamentalAnalyzer`` reads."""

    def __init__(self, balance_sheet: pd.DataFrame, financials: pd.DataFrame):
        self.balance_sheet = balance_sheet
        self.financials = financials
        self.quarterly_financials = pd.DataFrame()  # 3y CAGR is display-only
        self.dividends = pd.Series(dtype=float)     # dividend growth is display-only


def elite_fundamental(pit: PITFundamentals, day: pd.Timestamp, price: float):
    """``FundamentalData`` as the live ELITE analyzer would have built it on ``day``.

    Runs the live ``FundamentalAnalyzer.analyze_info`` on the reconstructed
    payload, so the scoring is the engine's own code, not a copy. Returns None
    when nothing was known on that day (the caller keeps the neutral default).
    """
    import TradingAnalysis_ELITE as elite

    info = pit.info_at(day, price)
    if not info:
        return None
    bs, fin = pit.statements_at(day)
    result = elite.FundamentalAnalyzer().analyze_info(info, _PITStock(bs, fin))
    if pit.sector:
        result._sector = pit.sector
    if pit.industry:
        result._industry = pit.industry
    return result
