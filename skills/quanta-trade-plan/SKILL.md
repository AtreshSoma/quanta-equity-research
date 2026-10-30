---
name: quanta-trade-plan
description: Check and stress-test a buy plan (shares, entry, stop, target) with QUANTA before the user places it themselves. Use when the user describes a trade they are considering. Needs the QUANTA MCP server.
---

# Check a plan before risking money

QUANTA never places orders. This skill tells the user whether *their* plan fits
their limits and how often plans shaped like it have worked, so they decide with
the numbers in front of them.

## Steps

1. Collect: ticker, shares, entry, stop, target. Ask for account value (and cash)
   if the user is willing; without it the dollar-risk and size checks are SKIPPED.
2. Call `plan_check(ticker, shares, entry, stop, target, account_value, cash,
   use_research=True)`. Report `overall` first, then every check that is BLOCK or WARN
   with its `message` and `next_step`. Say which checks were SKIPPED and why.
   Quote the `fingerprint`: it identifies this exact plan.
3. Call `plan_simulate(ticker, entry, stop, target)`. Report, in this order:
   - `verdict.label`, word for word;
   - target-first, stop-first and time-out shares (`simulation.p_target`, `p_stop`,
     `p_timeout`) and the break-even share (`simulation.break_even_share`);
   - the historical replay: `historical.decided`, `historical.effective_n` and the
     Wilson interval `historical.wilson95_effective`.
4. If the interval contains the break-even share, say plainly that history cannot tell
   this plan from a coin flip at its reward/risk.

## Rules

- The simulation is not a forecast. Say once that it is built from the stock's own past
  sessions, with the past trend removed (`drift: zero`) unless the user asked otherwise,
  and that earnings, halts and news can do things those sessions never did.
- Never round "not distinguishable from break-even" into an edge.
- A BLOCK means QUANTA's limits would refuse the plan; the user can still choose. Never
  pressure either way, and never place, route or draft an order.
- Exits and risk-reducing trades are never blocked by these checks.
