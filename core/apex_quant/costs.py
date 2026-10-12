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
