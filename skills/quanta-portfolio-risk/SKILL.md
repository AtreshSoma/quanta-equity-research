---
name: quanta-portfolio-risk
description: Measure a portfolio's tail risk and concentration with QUANTA (VaR, CVaR, single-name and sector limits). Use when the user shares holdings and asks how risky they are. Needs the QUANTA MCP server.
---

# Portfolio tail risk

## Steps

1. Build a JSON array of `{"ticker", "value"}` (market value per holding). Use the
   user's numbers only; never invent a position.
2. Call `portfolio_tail_risk(positions_json)`. Report:
   - historical 1-day VaR and CVaR (expected shortfall) at `confidence`, as a share of
     the measured book, and in money if the user gave values;
   - `coverage` (share of value measured) and `n_days` (aligned sessions);
   - every entry in `excluded` with its reason.
3. For concentration, call `portfolio_risk_check` with shares, average cost and sector.
   It flags names at or above QUANTA's 10% single-name limit and sectors at or above 30%,
   the same limits the My Portfolio page uses.

## Rules

- A null VaR means "not measured", never "no risk". Say so.
- VaR is historical: the loss the book would have exceeded on about 1 day in 20 over the
  sample, at today's weights. It is not a forecast and it understates crash risk when the
  sample was calm.
- CVaR is always at least as large as VaR; if you see otherwise, report a data problem.
- The limits are QUANTA's defaults, not a target for the user's goals or tax position.
