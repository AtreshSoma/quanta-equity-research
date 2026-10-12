# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One owner-investor running QUANTA locally (Flask on 127.0.0.1) to research US and Indian stocks for a small personal account (scans are typically run with $2,500 of capital). The job on the Picks page: set a capital budget, universe, mandate and position limit, run a scan, and leave with a short, trustworthy list of what to buy, at what entry, to what target, sized for that capital, and why every other candidate is not a buy today.

## Product Purpose

QUANTA scans a universe of stocks, scores each name, applies screening and risk rules, and proposes a hypothetical book sized to the user's capital. Success on Picks means the recommendation reads like a professional stock-picks list (Seeking Alpha, Yahoo Finance, Zacks, TipRanks are the owner's references): one ranked table, one decision per stock, entry, target, upside and allocation at a glance, with the reasoning available on demand.

## Positioning

Every rating is the same single Decision the Research tear sheet and My Portfolio show (one decision core, no second verdict beside it), and every unfunded name states the one rule that kept it out. Sizing comes from the stop distance and the user's own capital, not from a generic watchlist.

## Operating Context

- Local single-user web app; the owner runs scans on demand, mostly on a desktop browser, sometimes on a phone.
- Data: free public providers (yfinance), SEC EDGAR for corporate events; scans take one to several minutes and can be rate-limited.
- Read-only: QUANTA never places orders; the book is hypothetical and paper-traded before any real money.

## Capabilities and Constraints

- Scan Setup options: market (US, India), capital budget, universe/scan type, client mandate (Balanced, Conservative), max positions, diversify across sectors. Each option must change the result in the way its label says.
- Funding rules are fixed owner decisions: corporate-event checks and unavailable checks fail closed (India included), net reward-to-risk at least 1.8 after costs, the cost-viability gate fails closed, grade D at half size, Research levels govern. Never loosen a funding threshold.
- No hard-coded tickers, thresholds or fact sentences in source: thresholds come from `engine/thresholds.py` and reach the page through `/api/thresholds` or the scan payload.
- Do not touch `core/SP_ELITE_PRO_Main.py` (reference only).

## Brand Commitments

- Name: QUANTA Elite. Existing app shell, navigation and light/dark themes stay.
- Picks must be minimal, easy to understand, well structured and professional (owner, 2026-10-05).

## Evidence on Hand

- Real scan output from the engine (synthetic fixtures for tests; live public data on scratch servers).
- No validated model track record yet: evidence status "Not yet tested" must stay visible and no performance claim may be invented.

## Product Principles

1. One decision per stock, stated once, everywhere.
2. The table answers buy what, at what price, to what target, how much; everything else is on demand.
3. Every number on the page comes from config or the scan payload, never from page copy.
4. Fail closed and say so plainly: an unchecked name is never a Buy or Accumulate, and the page names it.
5. Each Scan Setup option visibly does what its label promises.
