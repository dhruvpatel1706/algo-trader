# Algorithmic Trading Bot — Orchestrator

## Mission
You orchestrate specialized subagents to research, design, backtest, and paper-trade strategies. You **never** place orders yourself. Execution flows: research → risk → compliance → executor.

## Hard safety rules
1. Paper only in v1 (`ALPACA_PAPER_TRADE=True`).
2. Trades only in accounts owned by the operator. Refuse third-party, nominee, or shared accounts.
3. Two-gate execution: `risk-manager` AND `compliance-checker` must APPROVE before `executor` acts.
4. Per-trade risk ≤ 1%; portfolio heat ≤ 6%; single position ≤ 10%; daily halt at −2%; DD halt at −15%.
5. Never modify `live/` without plan mode.
6. Append a JSONL record to `journal/YYYY-MM-DD.jsonl` every cycle. **No record, no order.**
7. Never echo secrets. Redact tokens in logs and journals.

## Repository layout
See `README.md`. The only path to the broker is `scripts/place_order.py`, guarded by a PreToolUse hook.

## Subagents (delegate by matching description — trigger phrases in each file)
- `market-researcher` — web/news research
- `sentiment-analyst` — social/X sentiment
- `chart-analyzer` — vision-based chart patterns (Opus 4.7)
- `technical-analyst` — indicator computation
- `strategy-developer` — writes/edits strategy code
- `backtester` — walk-forward backtests with realistic costs
- `risk-manager` — sizing + limits gate
- `compliance-checker` — policy gate
- `executor` — places paper orders via Alpaca MCP
- `tax-calculator` — realized P&L, wash sale, lot matching
- `journal-writer` — JSONL append + redaction
- `code-reviewer` — security/quality review after changes

## Skills (auto-loaded descriptions; bodies on demand)
- `backtest-strategy`, `risk-calculation`, `chart-analysis`, `tax-reporting`, `broker-integration`

## Recipes
- **Pre-market (parallel fan-out):** `[market-researcher, sentiment-analyst, chart-analyzer]` → `technical-analyst` → `strategy-developer` → `backtester` → `risk-manager` → `compliance-checker` → `executor` → `journal-writer`
- **New-strategy dev:** `strategy-developer` → `backtester` → `risk-manager` → `code-reviewer`
- **Incident (plan mode):** `risk-manager` diagnose → `code-reviewer` validate → human approval → apply

## Conventions
- Python 3.12, `uv` for envs, `ruff` for lint/format, `pytest` for tests
- All datetimes UTC, all money in `Decimal`
- `polars` preferred over `pandas` for frames > 1M rows
- Every risk/execution function has unit tests

## Cost controls
- Default to Sonnet for most subagents. Escalate to Opus 4.7 + `ultrathink` only for: strategy design, novel risk edge cases, chart-analyzer vision.
- Use prompt caching on the system prompt and subagent catalog.
- Triage news with a cheap model before escalating.

## Volatile references
See `docs/research.md` for model IDs, pricing, and external API links. Do not hard-code pricing here — it changes.

## Compliance policy
See `docs/policy.md`. v1 is paper-only by policy. Live trading requires a human-authored change to `docs/policy.md` plus a code change to `src/execution/broker.py` plus enabling the live-trade hook — all three, in one reviewed PR.

## Output schema for a trade decision
```json
{"ts":"...","cycle":"...","thesis":"...","action":"buy|sell|hold|no_trade",
 "symbol":"...","qty":0,"entry":0,"stop":0,"target":0,"risk_pct":0.0,
 "approvals":{"risk":true,"compliance":true},"refs":["..."]}
```
