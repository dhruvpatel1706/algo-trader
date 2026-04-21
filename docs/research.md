# Volatile references

Anything in this file changes outside the codebase's control: model IDs, pricing, external API quotas, vendor URLs. Do not hard-code these values in `src/`. Read them here when you need them.

## LLM model IDs (Claude family)

| Model | When to use |
| --- | --- |
| `claude-opus-4-7` | strategy design, chart vision (chart-analyzer), novel risk reasoning |
| `claude-sonnet-4-6` | default for most subagents — good reasoning at moderate cost |
| `claude-haiku-4-5-20251001` | mechanical work (journal writes, indicator math, executor) |

When a subagent file omits `model:`, it inherits `model` from `.claude/settings.json` (currently `sonnet`).

## Alpaca

- Paper trading API: `https://paper-api.alpaca.markets`
- Market data API: `https://data.alpaca.markets`
- Free tier: 200 req/min, IEX-only data feed. Upgrade for SIP feed.
- Docs: `https://docs.alpaca.markets/`
- MCP server: install via `uvx alpaca-mcp-server serve`. Configured in `.mcp.json` (paper-only).

## Polygon (optional, **not enabled in v1**)

- Add via `claude mcp add polygon -- uvx polygon-mcp serve` after exporting `POLYGON_API_KEY`.
- Free tier rate limits are restrictive; consider paid tier for high-frequency intraday.

## Pricing notes

LLM token pricing changes — check `https://anthropic.com/pricing` before estimating. The dashboard cost counter computes spend live from the `agents.cost_tokens` event stream rather than from a hard-coded table.
