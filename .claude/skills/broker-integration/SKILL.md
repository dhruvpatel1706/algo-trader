---
name: broker-integration
description: Runs a paper-broker smoke test against Alpaca MCP — auth, clock, account, positions, submit + cancel a tiny marketable limit. Use whenever broker setup, "is Alpaca working", or connection issues come up.
---

# Alpaca paper smoke test

## Steps

1. **Verify env.** `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER_TRADE=True` are set. Refuse if any are missing.
2. **Confirm MCP.** Run `claude mcp list` and confirm `alpaca` shows as connected. If not, restart Claude Code after `claude mcp add` (config is in `.mcp.json`).
3. **Auth.** Call `mcp__alpaca__get_account`. Expect `account_blocked: false` and `trading_blocked: false`.
4. **Clock.** Call `mcp__alpaca__get_clock`. Print `is_open` and `next_open` / `next_close`.
5. **Positions.** Call `mcp__alpaca__get_positions`. Print count.
6. **Submit + cancel.** Submit a marketable limit far from the inside (e.g. `SPY buy 1 share at last - $20.00`). Capture order id. Cancel within 5 s.
7. **Print summary.** Account equity, buying power, # positions, # orders, smoke-test result.

## Failure handling
- **Auth failure** → check `.env` and rotate paper keys at `https://alpaca.markets` if needed.
- **MCP not connected** → confirm `uvx alpaca-mcp-server serve` is on PATH; reinstall with `uvx --reinstall alpaca-mcp-server`.
- Never leave a real (non-cancelled) order from a smoke test.
