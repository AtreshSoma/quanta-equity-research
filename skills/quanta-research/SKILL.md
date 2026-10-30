---
name: quanta-research
description: Research one stock with QUANTA and explain the call faithfully. Use when the user asks what QUANTA thinks of a ticker, whether to buy it, or why a call is blocked. Needs the QUANTA MCP server.
---

# QUANTA research, explained faithfully

QUANTA builds one `Decision` per ticker. Your job is to fetch it and say what it
says: no upgrades, no invented numbers, no claims of an edge the model has not shown.

## Steps

1. Call `get_decision(ticker)`. It returns `decision` (the engine's own object) and
   `interpretation` (sentences that each cite the decision fields they came from).
2. Open your answer with the label and the evidence status, **word for word**:
   `decision.label` and `decision.evidence.status_text`.
   Example: "QUANTA: WATCHLIST. Model evidence: Tested out of sample: no demonstrated edge."
3. If `decision.primary_blocker` is set, say what blocks a stronger call (`text`) and
   what would change it (`remedy`). Read `decision.levels.rr_hurdles`: each hurdle has
   `min`, `passes` and, when it fails, `clear_entry` (the highest spot price that clears it).
4. Give the levels from `decision.levels` (entry, stop, target, `reward_risk` net of
   costs, `target_horizon_days`). Mention `risk_grade` as a sizing input, not a direction.
5. For recent company news from the SEC, call `material_filings(ticker, days=90)`. Name every
   HIGH-severity 8-K by its date and item title. For each item with history, quote the past reaction
   with its `n`, the median and the mean, and state "past reactions, not a forecast". Below 5 filings
   QUANTA gives no statistics; say "too few to say". Non-US tickers come back "not covered"; say so,
   never "no filings".
6. Before you show any summary you wrote yourself, call
   `verify_narrative(text, decision_json)` with the `decision` object. If `grounded` is
   false, fix every finding (or quote the `interpretation` text instead) and check again.

## Rules

- Never turn WATCHLIST, HOLD or AVOID into a buy, and never say "strong buy" unless that
  is the label.
- Never call a signal proven, validated or reliable unless
  `decision.evidence.permits_recommendation` is true. Today it is false for every model.
- A score is a ranking input on its engine's own scale (`score_basis`), not a probability.
- `decision.shadow_model` (when present) is the other Research model version, scored in shadow
  with no gates. It is not QUANTA's call. Never present its score, band or key as the label,
  the action or the recommendation. Say "shadow model, untested, not used for this call" if
  you mention it at all.
- Research gates reward/risk at 1.5; Picks funds only at 1.8. Both appear in `rr_hurdles`.
- Not financial advice. QUANTA never places orders.
