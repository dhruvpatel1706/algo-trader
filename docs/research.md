# Volatile references

Anything in this file changes outside the codebase's control: model IDs, pricing, external API quotas, vendor URLs. Do not hard-code these values in `src/`. Read them here when you need them.

## Alpaca

- Paper trading API: `https://paper-api.alpaca.markets`
- Market data API: `https://data.alpaca.markets`
- Free tier: 200 req/min, IEX-only data feed. Upgrade for SIP feed.
- Docs: `https://docs.alpaca.markets/`
- MCP server: install via `uvx alpaca-mcp-server serve`. Configured in `.mcp.json` (paper-only).

## Polygon (optional, **not enabled in v1**)

- Install via `uvx polygon-mcp serve` after exporting `POLYGON_API_KEY`.
- Free tier rate limits are restrictive; consider paid tier for high-frequency intraday.

## Pricing notes

LLM token pricing changes — check the provider's pricing page before estimating. The dashboard cost counter computes spend live from the `agents.cost_tokens` event stream rather than from a hard-coded table.
