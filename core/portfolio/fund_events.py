"""My Portfolio's fund event exemption; never used to fund Picks."""
from copy import deepcopy

FUND_INSTRUMENT_TYPES = frozenset({"ETF", "MUTUALFUND", "CLOSED-END-FUND", "CEF"})
NOT_APPLICABLE = {
    "status": "not_applicable", "code": "", "source": "instrument_type",
    "text": "Corporate-event check: not applicable to funds.",
}


def portfolio_decision(result):
    """Only a typed fund with a pre-gate verdict gets the exemption.

    Unknown types, broad is_etf flags (which include indices), and older cached
    results without a pre-gate verdict retain the fail-closed shared decision.
    Copy the contract so portfolio consumers cannot modify the Picks cache.
    """
    kind = str(result.get("quote_type") or "").strip().upper()
    candidate = result.get("portfolio_fund_decision")
    if (kind in FUND_INSTRUMENT_TYPES and isinstance(candidate, dict)
            and (candidate.get("event_check") or {}).get("status") == "not_applicable"):
        return deepcopy(candidate)
    return deepcopy(result.get("decision"))
