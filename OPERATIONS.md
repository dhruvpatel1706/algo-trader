# OPERATIONS.md — algo-trader runbook

> v1 is **paper-only**. See `docs/policy.md` for the full compliance basis.

## Start everything

```bash
# 1. Python deps + venv
uv sync --extra dev

# 2. Postgres + Redis
docker compose up -d postgres redis

# 3. Configure secrets (paper keys only)
cp .env.example .env
# edit .env — paste your Alpaca paper key + secret

# 4. Backend (port 8000)
uv run uvicorn dashboard.api.main:app --reload --port 8000

# 5. Frontend (port 3000)
cd dashboard/web && pnpm install && pnpm dev

# 6. Smoke test the paper pipeline (dry-run, no broker call)
uv run python scripts/smoke_paper.py

# 7. Run a backtest (any of the seven strategies)
uv run python -m src.backtest.cli --strategy mr_etf --start 2022-01-01 --end 2024-12-31
uv run python -m src.backtest.cli --strategy failed_breakout --start 2018-01-01 --end 2025-12-31
uv run python -m src.backtest.cli --strategy ma_pullback_trend --start 2018-01-01 --end 2025-12-31
uv run python -m src.backtest.cli --strategy range_shift_pullback --start 2018-01-01 --end 2025-12-31
uv run python -m src.backtest.cli --strategy momentum_xs --start 2018-01-01 --end 2025-12-31

# 8. Start the 24/7 runner (wires up all 5 agents + scheduled jobs)
uv run python scripts/run_bot.py

# 9. Start the dead-man-switch watchdog (separate process; flatten on heartbeat loss)
uv run python scripts/watchdog.py

# 10. Audit live-readiness for a strategy
uv run python scripts/check_live_ready.py --strategy mr_etf
uv run python scripts/check_live_ready.py --portfolio
```

## Default behavior with no API keys

The bot runs out of the box with **no paid API keys**. Defaults:
- News: returns `[]` until `FINNHUB_API_KEY` is set
- Sentiment: returns neutral score until `ANTHROPIC_API_KEY` is set
- SEC Form 4: works without key (EDGAR is public; respect 10 req/sec + User-Agent header)
- Congress: returns `[]` until `QUIVER_API_KEY` is set
- Crypto wallets: returns `[]` until `NANSEN_API_KEY` is set
- Crypto broker: `SimulatedCryptoBroker` simulates fills entirely in-process
- Discord alerts: silent until `DISCORD_WEBHOOK_URL` is set
- Polygon options data: `wheel_etf` stays stubbed until `POLYGON_OPTIONS_KEY` is set

## Multi-agent architecture

| Agent | Asset class | Strategies | Universe yaml key |
|---|---|---|---|
| `equity_agent` | Equities/ETFs | `mr_etf`, `ma_pullback_trend`, `failed_breakout` | `liquid_etfs_top20` |
| `gold_agent` | Commodities | (stub — wire failed_breakout/ma_pullback_trend with `_gold` suffix) | `gold` |
| `bonds_agent` | Bonds | (stub — wire `ma_pullback_trend_bonds` + `macro_regime_filter`) | `bonds` |
| `crypto_agent` | Crypto | (stub — wire `failed_breakout_crypto`, `ma_pullback_trend_crypto`, `funding_filter`) | `crypto_majors` |
| `governance_agent` | n/a | LLM-driven kill/promote/halt — does not execute trades | n/a |

Each agent gets a slice of total portfolio heat (≤6%). Initial allocation: equity 40%, gold 15%, bonds 15%, crypto 30%. Rebalanced monthly based on rolling 90-day risk-adjusted return. Hard ceilings enforced regardless of allocation math.

## Walk-forward results (2018-2025)

| Strategy | Sharpe | PF | Max DD | n_trades | Status |
|---|---|---|---|---|---|
| `failed_breakout` | 0.61 | 1.04 | 20.4% | 337 | research-only (PF below 1.2) |
| `ma_pullback_trend` | 1.00 | 1.43 | 21.3% | 378 | marginal (DD slightly over 20%) |

See [`docs/edge_research_2026.md`](docs/edge_research_2026.md) for full notes. Per the charter: weak strategies are documented and kept in research, NOT tuned to push borderline metrics over the line.

## Moonshot lanes (paper-only, gated)

| Lane | Module | Purpose |
|---|---|---|
| HFT | `src/moonshot/hft_sandbox.py` | Simulated-fill latency-sensitive research; never bridges to live |
| $100→$2M | `src/moonshot/aspirational_account.py` | Aggressive paper account with log-scaled compounding tracker |
| Copy-trading | `src/moonshot/copy_shadow.py` | Shadow-copy politicians/insiders/wallets; ≥50 trades + slippage gate before promotion |
| LLM-discretionary | `src/moonshot/llm_discretionary.py` | Sandboxed Claude paper agent; subject to standard risk gates |
| RL research | `src/moonshot/rl/` | LinearQAgent SARSA(λ); paper-only |

All four lanes carry a `LIVE_BROKER_BRIDGE = False` invariant enforced by tests.

Open `http://localhost:3000` for the dashboard. Kill switch is the red button (top right). Confirms by typing `FLATTEN`.

## Add a strategy

1. Create `src/strategies/<name>.py`, subclass `Strategy`, add a `<Name>Params` dataclass.
2. Each parameter gets a comment naming its **economic rationale**, **typical range**, **how it can break**.
3. Add tests in `tests/unit/strategies/test_<name>.py`.
4. Backtest: `uv run python -m src.backtest.cli --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD`.
5. Update `backtests/README.md` with honest expected metrics + failure modes.
6. Add `<name>` to `dashboard/api/state.py:_strategies` so it appears on the dashboard.

## Change risk caps

Risk caps live in `src/config.py:Settings`. The validators reject any value above the v1 ceiling — to relax beyond that, edit `src/config.py` directly. To lower (always safe), set the env var in `.env`:

```
MAX_PER_TRADE_RISK=0.005
DAILY_LOSS_HALT=-0.01
```

Restart the backend to pick up changes.

## Read the journal

`journal/YYYY-MM-DD.jsonl` — one JSON record per line, UTC dates. Records are append-only and redacted before write. Useful filters:

```bash
# All risk-gate APPROVE/REJECT today
grep '"gate":"risk"' journal/$(date -u +%F).jsonl | jq

# All submits across all days
cat journal/*.jsonl | jq 'select(.event=="submit")'
```

Daily files are gitignored — operator-private.

## Smoke the paper pipeline

```bash
uv run python scripts/smoke_paper.py        # dry-run, no broker call
uv run python scripts/smoke_paper.py --live # real Alpaca paper submit
```

## Enable live trading

**Two coordinated changes in one reviewed PR — there is no shortcut.**

1. Edit `docs/policy.md` to remove the paper-only restriction and document the policy basis.
2. Replace `LiveBroker` `NotImplementedError` in `src/execution/broker.py` with a real implementation.

Re-run `pytest`, re-run the smoke. Then and only then can `--paper` come off the executor command.
