# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/AtreshSoma/quanta-equity-research/security/advisories/new)
for this repository, not in a public issue. Include what you found, how to reproduce it, and the
commit you tested. You should get a reply within a week.

## What is in scope

QUANTA is a single-user tool that runs on your own computer:

- The web app serves on a loopback address only and refuses any other host.
- State-changing requests need a same-origin header (`webapp/request_guard.py`).
- The broker integration is read-only: only the read tools listed in `core/brokers/robinhood.py`
  can be called, and anything that looks like an order is refused (`core/brokers/mcp_http.py`).
  Tokens are held in memory and redacted from logs (`core/brokers/redact.py`).
- API keys and contact details belong in `.env`, which is git-ignored. Never commit one.

Reports about any of these protections, credential handling, the published track record
(`track_record/`), or dependencies are welcome.

## What the published track record contains

`track_record/` lists the calls QUANTA made and how they turned out. Calls made while an
account's holdings were known are kept in the local ledger and never published
(`core/tracking/publish.py`, `is_private`).

## Not covered

Investment results. QUANTA is educational software with no demonstrated edge; see the README.
