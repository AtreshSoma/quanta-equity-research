"""Transaction-cost and execution model.

Per-trade friction has three parts on each side (entry and exit):
  * commission: max(per_share * shares, min_commission), as a fraction of notional.
  * half-spread: half the quoted bid/ask spread, approximated from a bps assumption.
  * slippage: market impact that grows with volatility and shrinks with
    liquidity. We model it as
        slippage_bps = base_bps + impact_k * atr_pct_bps * (target_notional / adv_dollars)
    so larger orders relative to average dollar volume pay more, and more
    volatile names pay more.

Defaults are calibrated for a small retail account at a discount broker
(commission-free per-share, but spread + slippage still apply). Override via the
constructor for a different account/broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CostModel:
    commission_per_share: float = 0.0      # discount broker, commission-free
    min_commission: float = 0.0
    half_spread_bps: float = 3.0           # ~6 bps round-trip quoted spread
    base_slippage_bps: float = 2.0
    impact_k: float = 0.5                  # impact coefficient on participation

    def side_cost_pct(
        self, price: float, shares: int, atr_pct: float, adv_dollars: float
    ) -> float:
        """Cost of ONE side (buy or sell) as a fraction of that side's notional.

        ``atr_pct``     : ATR / price (volatility proxy).
        ``adv_dollars`` : average daily dollar volume (liquidity proxy).
        """
        if price <= 0 or shares <= 0:
            return 0.0
        notional = price * shares
        commission = max(self.commission_per_share * shares, self.min_commission)
        commission_pct = commission / notional if notional > 0 else 0.0

        half_spread_pct = self.half_spread_bps / 10_000.0

        participation = notional / adv_dollars if adv_dollars > 0 else 1.0
        participation = min(participation, 1.0)
        slippage_bps = self.base_slippage_bps + self.impact_k * (
            atr_pct * 10_000.0
        ) * participation
        slippage_pct = slippage_bps / 10_000.0

        return commission_pct + half_spread_pct + slippage_pct

    def round_trip_pct(
        self, price: float, shares: int, atr_pct: float, adv_dollars: float
    ) -> float:
        """Total entry+exit cost as a fraction of notional (approx, same price)."""
        return 2.0 * self.side_cost_pct(price, shares, atr_pct, adv_dollars)


@dataclass
class IndiaEquityDeliveryCostModel(CostModel):
    """India (NSE/BSE) equity DELIVERY cost model, alongside the US one.

    Statutory charges as commonly published for delivery trades (verify against
    the broker's current schedule; rates change by circular):
      * STT 0.1% on BOTH buy and sell value (delivery).
      * Exchange transaction charge ~0.00297% of value (NSE equity), both sides.
      * SEBI turnover fee 0.0001% (Rs 10 per crore), both sides.
      * Stamp duty 0.015% on the BUY side only.
      * GST 18% on (brokerage + exchange charge + SEBI fee) -- not on STT/stamp.
      * DP (depository) charge: flat Rs ~15.93 per scrip per SELL, modelled as
        ``dp_charge_flat`` in rupees (price is assumed to be in INR).
      * Brokerage defaults to 0 (discount delivery); set ``brokerage_pct`` (a
        fraction of value) for a full-service broker.
    Plus the same half-spread and volatility/liquidity slippage as the US
    model. ``adv_dollars`` is read as average daily traded VALUE in INR.

    Called without ``side`` it returns the buy/sell average, so a simulator that
    charges ``side_cost_pct`` on both legs gets exactly the round-trip total.
    """

    stt_pct: float = 0.1 / 100.0
    exchange_txn_pct: float = 0.00297 / 100.0
    sebi_pct: float = 0.0001 / 100.0
    stamp_buy_pct: float = 0.015 / 100.0
    gst_pct: float = 0.18
    dp_charge_flat: float = 15.93
    brokerage_pct: float = 0.0

    def side_cost_pct(
        self, price: float, shares: int, atr_pct: float, adv_dollars: float,
        side: Optional[str] = None,
    ) -> float:
        if price <= 0 or shares <= 0:
            return 0.0
        if side is None:
            return 0.5 * (
                self.side_cost_pct(price, shares, atr_pct, adv_dollars, "buy")
                + self.side_cost_pct(price, shares, atr_pct, adv_dollars, "sell")
            )
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        notional = price * shares
        market = super().side_cost_pct(price, shares, atr_pct, adv_dollars)  # commission+spread+slippage
        brokerage = self.brokerage_pct
        taxable_fees = brokerage + self.exchange_txn_pct + self.sebi_pct
        statutory = self.stt_pct + taxable_fees * self.gst_pct + self.exchange_txn_pct + self.sebi_pct
        statutory += brokerage
        if side == "buy":
            statutory += self.stamp_buy_pct
        else:
            statutory += self.dp_charge_flat / notional
        return market + statutory


def cost_model_for_symbol(symbol: str, **overrides) -> CostModel:
    """India model for .NS / .BO symbols, the US default otherwise."""
    if str(symbol).upper().endswith((".NS", ".BO")):
        return IndiaEquityDeliveryCostModel(**overrides)
    return CostModel(**overrides)
