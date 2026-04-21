# algo-trader

Paper-first algorithmic trading system (v1). Built around a Claude Code orchestrator with specialized subagents.

> **v1 is paper-only by policy.** The system trades only in accounts owned by the operator. No third-party / nominee / shared accounts. Re-enabling live trading requires a coordinated change to `docs/policy.md`, `src/execution/broker.py`, and the `guard_live_order.sh` hook — all in one reviewed PR. See `docs/policy.md`.

## Quick start

```bash
# 1. Install Python deps (uv manages the venv)
uv sync --extra dev

# 2. Bring up Postgres + Redis
docker compose up -d postgres redis

# 3. Configure secrets
cp .env.example .env
# edit .env with your Alpaca *paper* keys (free at https://alpaca.markets)

# 4. Run the dashboard
uv run uvicorn dashboard.api.main:app --reload &
cd dashboard/web && pnpm install && pnpm dev

# 5. Drive it from Claude Code
claude
# > Run the broker-integration skill to smoke-test Alpaca paper.
# > Run the pre-market cycle on docs/universe.yaml.
```

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/` | Core library: config, data, signals, strategies, risk, execution, backtest, agents, journal |
| `dashboard/api` / `dashboard/web` | FastAPI backend + Next.js 15 frontend (P&L, positions, trades, kill switch) |
| `backtests/` | Per-run output: `metrics.json`, `equity.png`, parquet trades |
| `journal/` | Append-only JSONL decision log per day |
| `live/` | Protected runtime state. Edits require plan mode. |
| `tests/` | pytest suite (≥90% on `src/risk/` and `src/execution/`) |
| `docs/` | `policy.md` (compliance), `research.md` (volatile refs), `universe.yaml` |
| `scripts/` | `place_order.py` is the only path to the broker, guarded by a PreToolUse hook |
| `.claude/` | Orchestrator subagents, skills, hooks, settings |
| `OPERATIONS.md` | Runbook (added in Phase 9) |

## Hard safety rules (immutable in v1)

1. Paper-only (`ALPACA_PAPER_TRADE=True`).
2. Two-gate execution: `risk-manager` AND `compliance-checker` must both APPROVE before `executor` may submit.
3. Per-trade risk ≤ 1% · portfolio heat ≤ 6% · single position ≤ 10% · daily halt at −2% · drawdown halt at −15%.
4. Every cycle appends one redacted JSONL record to `journal/YYYY-MM-DD.jsonl`. **No record, no order.**
5. `live/` is protected. Edits require plan mode.
6. No third-party / nominee / shared accounts. Refuse on sight.

## Strategies in v1

- `wheel_etf` — cash-secured puts on SPY/QQQ at ~30Δ, 30–45 DTE, managed at 50% max profit, IVR>30 filter, 21-DTE roll.
- `mr_etf` — Bollinger(20,2) + RSI(2)<10 mean reversion on SPY/QQQ, gated by ADX(14)<20.

Both ship with realistic-cost backtests and an honest write-up of expected drawdowns and known failure modes. **No win-rate promises anywhere.**

## License

Proprietary. Operator use only.
