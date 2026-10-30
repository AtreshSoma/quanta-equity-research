# Agent skills for QUANTA

Three Claude skills that drive the QUANTA MCP server (`mcp_server/quanta_mcp_server.py`)
and keep an AI agent honest about what QUANTA says:

| Skill | Use it for | MCP tools |
|---|---|---|
| `quanta-research` | "What does QUANTA think of NVDA?" | `get_decision`, `verify_narrative` |
| `quanta-trade-plan` | "I want to buy 20 AAPL at 230 with a stop at 220" | `plan_check`, `plan_simulate` |
| `quanta-portfolio-risk` | "How risky is my portfolio?" | `portfolio_tail_risk`, `portfolio_risk_check` |

Each skill states the same rules: repeat the label and evidence status word for word,
never upgrade a call, check any self-written summary with `verify_narrative`, and never
place an order.

## Install

Register the MCP server first (see the README, "MCP server"). Then copy the folders:

```bash
# project-local
mkdir -p .claude/skills && cp -r skills/quanta-* .claude/skills/
# or for every project
cp -r skills/quanta-* ~/.claude/skills/
```

Without Claude Code, paste a `SKILL.md` at the top of a chat that has the MCP server attached.
