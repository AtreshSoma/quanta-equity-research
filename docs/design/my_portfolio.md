# My Portfolio (Robinhood, read-only)

The **My Portfolio** tab reads your Robinhood holdings through the official
Robinhood Trading MCP server (`https://agent.robinhood.com/mcp/trading`). It
runs every holding through the same research Decision the Research page uses,
then adds portfolio context: size, sector, correlation, cash, earnings and
risk budget.

## Flow

```
Robinhood MCP ──► brokers/mcp_http.py   read-tool allowlist; write tools refused in code
              ──► brokers/robinhood.py  live tools/list schema check, normalizer, opaque account handles
              ──► portfolio/models.py   Portfolio / Position / TaxLot (missing = None, never 0)
              ──► /api/analyze path     authoritative Decision per holding (full mode, real account size)
              ──► portfolio/analytics.py weights, HHI, beta, volatility, correlation, stops, earnings
              ──► portfolio/guidance.py stance | new-money action | existing-position action
              ──► webapp/portfolio_routes.py + static/my-portfolio.js
```

`portfolio/execution.py` is a placeholder for review → confirm → place. It is
disabled by a code constant, and the research client cannot call order tools.

## Connecting

1. Start QUANTA as usual, open **My Portfolio**, and click **Connect Robinhood**.
2. Sign in on robinhood.com. You return to `/#holdings`.
3. If you have more than one account, pick one. QUANTA defaults to an account
   the agent cannot trade in, which is your main account.

The access token is held in server memory only. **Disconnect** forgets it, and
so does a restart. Robinhood grants a single `internal` scope that is not
read-only, so read-only is enforced by QUANTA's tool allowlist. Nothing about
the connection is stored on disk or in the browser, and there is no API key or
client secret to configure: every user registers their own public client
(PKCE, no secret) when they first click **Connect**.

If sign-in does not complete, the page shows Robinhood's reason (for example
`invalid_redirect_uri` or `invalid_client`) instead of a generic failure.
**Start sign-in again** registers afresh. If Robinhood cannot be reached, the
message says whether it was the connection, the proxy or the certificate.

### Hide amounts

The eye icon in the My Portfolio header hides every account-specific figure:
dollar amounts, share counts, cost, gain and loss, including those inside
guidance text. It does this at render time, so the numbers are not in the page
at all, not merely blurred. Weights, market prices and the research stay
visible, so the page can be shown to someone. The setting is remembered in this
browser (a single boolean in `localStorage`), so a reload does not reveal the
numbers.

## Tabs (2026-09-29, redesigned the same evening)

**Overview · Holdings · Guidance · Activity · Review · News & Events.**

- **Overview**: a KPI strip where each figure has its own visual: total value with the session's move and a 30-day line of today's shares; return on cost as a value-against-cost bullet; realized P&L with Robinhood's buckets as gain/loss bars; cash (no target range exists, so none is drawn); and QUANTA's risk rule as a four-band gauge at its points (0-1 Low, 2-3 Moderate, 4-5 Elevated, 6+ High) with each driver as a tag that jumps to its visual. Then the *Action queue* (top three decisions, "See all" opens Guidance) beside *Movers* (best and worst three by session, and the account against the indexes when their quotes are from the same session); *Holdings vs S&P 500* (indexed return from the range start, the gap shaded green where the holdings lead and red where they lag, end labels, your open-lot purchases on the line; 1D uses 5-minute bars with extended hours shaded, and opens by default while the market is live, else 1M) beside *Allocation* (Sector / Asset / Region donut with the 30% limit marked on over-limit slices, or a Treemap sized by weight and coloured by the session; hover lists a group's holdings, click pins them with a Filter link, Esc unpins); and *Holdings* (concentration bar with the 10% limit and 20% review level, sortable table with a sticky header) beside *What drove return* (unrealized gain by holding, with the total). Pointing at a holding anywhere highlights it everywhere.
- **Holdings**: the full table. The row detail panel is unchanged.
- **Guidance**: filter chips per group; the largest sized decision as a hero (weight before and after against the limit and review level, cash freed, and its effect on the largest position, the sector, cash and the risk rule); decisions that share a reason as one card with the reason and next step stated once; new money on hold grouped by reason; and *What if you act*, which previews any combination of the scenarios QUANTA defines (trim to the limit, or sell in full as the outer bound of an unsized trim) through `POST /whatif`.
- **Activity**: realized P&L by Day, Week, Month, 3M, Year, YTD or All, from Robinhood's realized-P&L buckets (`get_realized_pnl` with `span`, or `start_date`/`end_date` for YTD). A bucket without a figure is a gap, never 0. Transactions and lots are listed below.
- **Review**: a plain reading, results by source (realized / unrealized / dividends / fees / deposits), attribution on open lots, outcome records (trades and QUANTA calls, never combined), lessons, the decision journal and a Markdown export.
- **News & Events**: earnings (confirmed vs tentative), declared dividends, SEC filings (EDGAR), Fed and BLS dates (`stock-analysis/data/official_calendar.json`, retrieved 2026-09-29; refresh it yearly), and headlines matched to holdings, deduplicated. Watch conditions are checked **on this page only**; there is no background monitoring or notification.

### What the chart is

The performance line is a **current holdings simulation**: today's share counts valued at past closes, compared with SPY on the same price-return basis (no dividends). A holding without a price on the first day of the range is left out and named, never held flat. An actual return is shown only if Robinhood's order history, replayed, reaches today's exact share counts (time-weighted, equity sleeve only). No whole-account return is possible: daily account values and cash flows are not available.

### Routes

`/performance`, `/intraday` (1D: today's shares over the latest session's 5-minute bars from the previous close, a simulation), `/whatif` (POST: `compute_metrics` re-run with the chosen sales moved to cash, before and after from the same call), `/exposure` (POST), `/events`, `/outcomes`, `/activity?period=`, `/records`, `/journal`. A server started before these existed answers them with 404. The page then shows one restart banner, falls back to the data the view carries (last session vs indices, sectors and holdings, the risk strip), and prints no error paragraphs.

The decision journal is `ui/stock-analysis/webapp/data/portfolio_journal.json` (git-ignored). It holds dated calls only: no amounts, quantities or account details, and it never backfills.

## Confirming the field mapping on a real account

Robinhood does not publish its tool schemas. After connecting and pressing
**Refresh**, open `http://127.0.0.1:5500/api/portfolio/diagnostics`. It shows:

- `read_tools`: each tool's live inputs, and whether QUANTA can call it;
- `field_maps`: which payload key each value was read from;
- `response_shapes`: key names and types only, never values;
- `write_tools_offered`: order and watchlist tools the server offers, which QUANTA never calls.

Check that `portfolio.reconciliation.status` in `/api/portfolio` is `ok`, meaning
positions plus cash equal Robinhood's total. Then spot-check a few quantities and
cost bases against the Robinhood app. A `mismatch` is shown on the page and is
never corrected silently.

## Tests

- `tests/test_my_portfolio.py`: guidance, analytics, redaction, the read-only
  boundary, OAuth, normalization against a fake MCP transport, and route security.
- `webapp/tests/unit/my_portfolio.test.cjs`: rendering against a fixture built by
  the real `build_view` (`webapp/tests/tools/make_my_portfolio_fixture.py`).
- `webapp/tests/e2e/my_portfolio.spec.ts`: axe, overflow, and keyboard checks, on
  any server.
- `tests/test_portfolio_whatif_intraday.py`: the 1W range, the intraday simulation and
  the what-if. Visual review: `tests/tools/portfolio-preview.cjs` (`BOOK=18` serves an
  18-holding book from `make_book18_fixture.py`), `portfolio-shots.cjs` and
  `portfolio-interact.cjs`.

The fakes prove logic, not the live integration.

## Live validation status (2026-09-26)

**Not production verified. Authentication and real-account reconciliation are pending.**

The current [official Robinhood instructions](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
confirm the endpoint above and read access to existing accounts. Direct HTTPS
checks verified protected-resource metadata, authorization-server metadata,
S256 PKCE, the `internal` scope, successful dynamic client registration, and
HTTP 401 for an unauthenticated MCP initialize request. These checks do not
verify authenticated tools, inputs, outputs, pagination, or account fields.

The adapter's candidate tool names and field mappings remain provisional until
authenticated `tools/list` and account reads have been inspected. No replacement
field mappings were invented during this validation. A separate current-code
server was started at `http://127.0.0.1:5501/#holdings`; the existing server on
5500 returned 404 for the portfolio status route.

### Network policy (revised 2026-09-27)

The 2026-09-26 hardening refused every redirect, pinned OAuth endpoints to
three exact hostnames, and ignored the system proxy and CA settings. Each of
those turns a routine change on Robinhood's side, or an ordinary home or office
network, into a sign-in failure for every user. The policy is now:

- **MCP resource** (receives the bearer token on every tool call): pinned to
  exactly `https://agent.robinhood.com/mcp/trading`.
- **OAuth endpoints**, read from Robinhood's own TLS-verified metadata:
  `robinhood.com` or any `*.robinhood.com` host. Look-alikes such as
  `robinhood.com.evil.example` or `evilrobinhood.com` are refused. So are
  `http`, non-443 ports, userinfo and fragments.
- **Redirects** are followed by hand, at most 3, and every hop is re-checked
  against the same policy. A request carrying a credential (token call, MCP
  call) may be redirected only within its own origin. A POST is never replayed
  across a 301/302/303.
- **Discovery** follows the MCP authorization spec: RFC 9728 well-known paths,
  then the `resource_metadata` hint in the MCP server's 401, then the RFC 8414
  and OpenID Connect authorization-server locations. The callback's `iss`
  (RFC 9207) must match the issuer.
- **Proxy and CA bundle**: `HTTPS_PROXY`, the Windows Internet Options proxy and
  `REQUESTS_CA_BUNDLE` are honoured. TLS verification is always on; every call
  passes `verify=True`, which also overrides an insecure injected session.
  `~/.netrc` is never read.
- **Redirect URI**: `http://127.0.0.1:<port>/api/portfolio/oauth/callback`.
  RFC 8252 prefers the loopback IP literal, so a page opened as `localhost`
  still registers `127.0.0.1`.
- **MCP protocol**: later requests carry the version the server chose in
  `initialize`, as the Streamable HTTP transport requires.

`tests/test_my_portfolio.py::TestConnectResilience` simulates a Robinhood whose
sign-in and token hosts moved, whose metadata is only reachable through the
401 hint and OpenID paths, and which uses redirects and a newer protocol. It
connects end to end, while look-alike hosts, cross-origin credential redirects,
method-changing redirects, redirect loops and a mismatched `iss` are all still
refused. Post-authentication log/storage inspection remains pending.

Before accepting production readiness, validate provider pagination, duplicate
symbols, field-level reconciliation, and guidance under partial/stale data with
real responses. The existing aggregate reconciliation tolerance is 0.5%; that
alone is not proof of the requested field-by-field rounding reconciliation.
Measure refresh and analysis duration only after the real holdings are available.
