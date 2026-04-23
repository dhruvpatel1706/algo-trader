# algo-trader

**Paper-first algorithmic trading system.** A self-contained Python + Next.js stack for researching, backtesting, and paper-executing equity and ETF options strategies — with a risk/compliance gate in front of every order and an append-only JSONL journal behind every decision.

> **v1 is paper-only by policy.** The system trades only in accounts owned by the operator. No third-party / nominee / shared accounts. Re-enabling live trading requires a coordinated change to `docs/policy.md` and `src/execution/broker.py` — both in one reviewed PR. See [`docs/policy.md`](docs/policy.md).

---

## Why this exists

Most retail algo projects I've looked at either (a) lie about their drawdowns, (b) couple strategy code to a specific broker API, or (c) skip the paper-vs-live boundary entirely. This repo tries to avoid all three:

- **Honest backtests.** The engine models commission, slippage, and spread. Reports joined-equity curves across walk-forward windows, not cherry-picked best windows.
- **Broker-agnostic execution.** `src/execution/broker.py` defines a `PaperBroker` and a `LiveBroker` stub; the Alpaca adapter is a thin wrapper. Swapping brokers is a file, not a refactor.
- **Hard separation between research and trading.** `live/` is protected. Every decision is gated by `risk` + `compliance` checks before reaching the broker, and every gate decision lands in `journal/YYYY-MM-DD.jsonl` as a signed-off record.

## What's in it

- **Strategies.** Two production strategies shipped:
  - `wheel_etf` — cash-secured puts on SPY/QQQ at ~30Δ, 30–45 DTE, managed at 50% max profit, IVR > 30 filter, 21-DTE roll.
  - `mr_etf` — Bollinger(20,2) + RSI(2) < 10 mean reversion on SPY/QQQ, gated by ADX(14) < 20.
- **Backtest engine.** Walk-forward with train/test splits, realistic cost modeling, parameter sweeps, per-cell audits.
- **Risk module.** ATR-based sizing, Kelly-capped, portfolio-heat aware. Hard caps enforced at the config layer (`src/config.py:Settings`).
- **Dashboard.** FastAPI backend + Next.js 15 frontend with P&L, positions, live trade feed, and a kill switch.
- **Observability.** `structlog` JSON logs, Prometheus metrics, live cost counter (token spend + commission).
- **Paper-broker integration.** Alpaca paper via `alpaca-py`; smoke-test script submits + cancels a marketable limit.

## Tech stack

| Layer | Tools |
| --- | --- |
| Language | Python 3.12, TypeScript 5 |
| Data | Polars · pandas · yfinance · PostgreSQL · Redis |
| Quant | NumPy · SciPy · statsmodels · vectorbt-style walk-forward |
| Serving | FastAPI · Next.js 15 (App Router) · pnpm |
| Broker | Alpaca (paper) via `alpaca-py` |
| Observability | structlog · Prometheus client · Grafana-ready |
| Tooling | uv · ruff · pytest · pre-commit · Docker Compose |

## Quick start

```bash
# 0. Clone
git clone https://github.com/dhruvpatel1706/algo-trader.git && cd algo-trader

# 1. Install Python deps (uv manages the venv)
uv sync --extra dev

# 2. Bring up Postgres + Redis
docker compose up -d postgres redis

# 3. Configure secrets (paper-only keys)
cp .env.example .env
# edit .env with your Alpaca paper key + secret — free at https://alpaca.markets

# 4. Run the dashboard
uv run uvicorn dashboard.api.main:app --reload &
cd dashboard/web && pnpm install && pnpm dev

# 5. Smoke-test the paper pipeline (no broker call)
uv run python scripts/smoke_paper.py

# 6. Run a backtest
uv run python -m src.backtest.cli --strategy mr_etf --start 2022-01-01 --end 2024-12-31
```

Open `http://localhost:3000` for the dashboard. Kill switch is top-right (confirms by typing `FLATTEN`). Full runbook in [`OPERATIONS.md`](OPERATIONS.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/` | Core library: config, data, signals, strategies, risk, execution, backtest, agents, journal |
| `dashboard/api` / `dashboard/web` | FastAPI backend + Next.js 15 frontend (P&L, positions, trades, kill switch) |
| `backtests/` | Per-run output: `metrics.json`, `equity.png`, parquet trades |
| `journal/` | Append-only JSONL decision log per day (gitignored — operator-private) |
| `live/` | Protected runtime state |
| `tests/` | pytest suite (≥90% coverage on `src/risk/` and `src/execution/`) |
| `docs/` | `policy.md` (compliance), `research.md` (volatile refs), `universe.yaml` |
| `scripts/` | `place_order.py` is the only path to the broker |
| `OPERATIONS.md` | Runbook |

## Hard safety rules (immutable in v1)

1. Paper-only (`ALPACA_PAPER_TRADE=True`).
2. Two-gate execution: the risk check **and** the compliance check must both APPROVE before the executor may submit.
3. Per-trade risk ≤ 1% · portfolio heat ≤ 6% · single position ≤ 10% · daily halt at −2% · drawdown halt at −15%.
4. Every cycle appends one redacted JSONL record to `journal/YYYY-MM-DD.jsonl`. **No record, no order.**
5. `live/` is protected. Edits require plan mode.
6. No third-party / nominee / shared accounts. Refuse on sight.

## Honest limitations

- **Alpaca free-tier data is IEX-only.** For production-grade fills you'd want SIP or a paid feed.
- **Options pricing in backtest is model-based** (Black-Scholes with IV surface interpolation), not tick-level. Good enough to reject obviously broken strategies; not good enough to trust the last 5 bps of edge.
- **No margin, no naked calls, no portfolio margin.** Enforced at the compliance layer.
- **Single-machine.** Not designed for colocation or sub-ms latency. This is research-to-paper, not HFT.

## Roadmap

- [ ] v1.1 — options chain cache + IV surface builder (Polygon optional)
- [ ] v1.2 — simple ML overlay for signal filtering (gradient boosting on feature residuals)
- [ ] v2.0 — multi-strategy portfolio optimizer

## License

Proprietary. Operator use only.
