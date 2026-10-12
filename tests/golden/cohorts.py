"""The golden-master ticker cohort.

Tickers are not chosen for interest; each one exists to hold a specific code
path open so a refactor cannot quietly change it. The ``why`` field names the
branch, and is what makes a failure diagnosable: "AAPL changed" is noise,
"the only recently-split fixture changed" is a lead.

Recording all of these hits Yahoo hard, so ``record.py`` takes an explicit list
or a cohort name. ``PRIORITY`` is the minimum set worth having -- it covers
every distinct branch at least once.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenTicker:
    symbol: str
    cohort: str
    why: str


COHORT: tuple[GoldenTicker, ...] = (
    # --- baseline happy path -------------------------------------------------
    GoldenTicker("AAPL", "mega_cap", "Baseline. Full data on every provider."),
    GoldenTicker("MSFT", "mega_cap", "Second baseline; high quality score."),
    GoldenTicker("JNJ", "mega_cap", "Mature, dividend, low beta."),
    # --- valuation branches --------------------------------------------------
    GoldenTicker("NVDA", "high_pe", "P/E > 40: DCF analyst-anchor branch (4345)."),
    GoldenTicker("TSLA", "high_pe", "P/E > 40 plus extreme vol."),
    GoldenTicker("KO", "moderate_pe", "P/E 25-40: DCF price-anchor branch (4354)."),
    GoldenTicker("RIVN", "negative_earnings", "Negative FCF and EPS: DCF FAILED path."),
    GoldenTicker("PLUG", "negative_earnings", "Negative margins; fundamental floor."),
    # --- sector bias (D9) ----------------------------------------------------
    GoldenTicker("NEE", "utility", "Utility: low ROE penalised by absolute threshold."),
    GoldenTicker("DUK", "utility", "Second utility; terminal growth 0.018."),
    GoldenTicker("JPM", "bank", "Bank: D/E and current-ratio checks misfire."),
    GoldenTicker("BAC", "bank", "Second bank."),
    GoldenTicker("XOM", "energy", "Energy: cyclical, terminal growth 0.020."),
    GoldenTicker("PG", "staples", "Consumer defensive: terminal growth 0.020."),
    # --- ETFs ----------------------------------------------------------------
    GoldenTicker("SPY", "etf", "ETF branch: _analyze_etf, expense ratio, net assets."),
    GoldenTicker("QQQ", "etf", "ETF, growth tilt."),
    GoldenTicker("VNQ", "etf", "ETF, real estate; dividend yield formatting."),
    GoldenTicker("TLT", "etf", "Bond ETF: equity metrics absent entirely."),
    # --- dividends -----------------------------------------------------------
    GoldenTicker("T", "high_dividend", "High yield: normalize_dividend_yield path."),
    GoldenTicker("MO", "high_dividend", "Very high yield; yield-cell formatting."),
    # --- risk grade D / F (override cascade) ---------------------------------
    GoldenTicker("MSTR", "high_risk", "Extreme vol: risk grade F -> HOLD override."),
    GoldenTicker("GME", "high_risk", "Meme vol: D-grade one-notch trim."),
    GoldenTicker("COIN", "high_risk", "High beta, high drawdown."),
    # --- international / suffix probing --------------------------------------
    GoldenTicker("TSM", "adr", "ADR: currency and ownership fields."),
    GoldenTicker("BABA", "adr", "ADR, non-US filings; thin insider data."),
    GoldenTicker("RELIANCE.NS", "india", ".NS suffix probing; INR currency symbol."),
    GoldenTicker("TCS.NS", "india", "Second NSE name."),
    GoldenTicker("SHEL.L", "london", ".L suffix probing; GBP currency."),
    # --- data-quality edges --------------------------------------------------
    GoldenTicker("BRK-B", "symbol_format", "Hyphenated class share; symbol normalisation."),
    GoldenTicker("GOOG", "dual_class", "Dual class; peer comparison picks the sibling."),
    GoldenTicker("SMCI", "volatile_midcap", "Large drawdown; Altman/Piotroski stress."),
    GoldenTicker("F", "low_price", "Low nominal price: share-count and min-position math."),
    GoldenTicker("BRK-A", "high_price", "Very high price: min_shares=1 fallback (5696)."),
    # --- thin coverage / small cap ------------------------------------------
    GoldenTicker("CRSP", "small_cap", "Small cap biotech: thin analyst coverage weight."),
    GoldenTicker("BLNK", "micro_cap", "Micro cap: liquidity score and volume cap."),
    GoldenTicker("BGFV", "micro_cap", "Micro cap, minimal provider coverage."),
    # --- structural ----------------------------------------------------------
    GoldenTicker("SPGI", "quality", "High ROE, low debt: quality-score ceiling."),
    GoldenTicker("BA", "distressed", "Negative equity: D/E and Altman edge cases."),
    GoldenTicker("INTC", "turnaround", "Falling margins; deteriorating sentiment."),
    GoldenTicker("PFE", "declining", "Revenue contraction: negative growth branch."),
)

# One representative per distinct branch. Record these first.
PRIORITY: tuple[str, ...] = (
    "AAPL",        # baseline
    "NVDA",        # high P/E analyst anchor
    "KO",          # moderate P/E price anchor
    "RIVN",        # DCF FAILED
    "NEE",         # utility sector bias
    "JPM",         # bank sector bias
    "SPY",         # ETF branch
    "TLT",         # bond ETF, equity metrics absent
    "MSTR",        # risk grade F override
    "RELIANCE.NS", # suffix probing + non-USD
    "BRK-B",       # symbol normalisation
    "BRK-A",       # min-position fallback
)

BY_SYMBOL = {t.symbol: t for t in COHORT}


def resolve(names: list[str]) -> list[str]:
    """Expand cohort names, 'priority' or 'all' into a symbol list."""
    if not names:
        return list(PRIORITY)
    out: list[str] = []
    for name in names:
        low = name.lower()
        if low == "all":
            out.extend(t.symbol for t in COHORT)
        elif low == "priority":
            out.extend(PRIORITY)
        elif any(t.cohort == low for t in COHORT):
            out.extend(t.symbol for t in COHORT if t.cohort == low)
        else:
            out.append(name)
    # de-duplicate, preserving order
    seen: set[str] = set()
    return [s for s in out if not (s in seen or seen.add(s))]
