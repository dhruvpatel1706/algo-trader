# algo-trader

**24/7 paper-first multi-agent multi-asset algorithmic trading system.** A self-contained Python + Next.js stack for researching, backtesting, and paper-executing equity, ETF, options, and crypto strategies — with a risk/compliance gate in front of every order, an append-only JSONL journal behind every decision, and an LLM as governance (not oracle).

> **v1 is paper-only by policy.** The system trades only in accounts owned by the operator. No third-party / nominee / shared accounts. Re-enabling live trading requires a coordinated change to `docs/policy.md` and `src/execution/broker.py` — both in one reviewed PR. See [`docs/policy.md`](docs/policy.md).

---

## Why this exists

Most retail algo projects either (a) lie about their drawdowns, (b) couple strategy code to a specific broker API, (c) skip the paper-vs-live boundary entirely, or (d) treat an LLM as a strategy oracle. This repo avoids all four:

- **Honest backtests.** The engine models commission, slippage, and spread. Reports joined-equity curves across walk-forward windows, not cherry-picked best windows. Walk-forward CV is purged + embargoed (López de Prado).
- **Broker-agnostic execution.** `src/execution/broker.py` and `src/execution/crypto_broker.py` define common interfaces; Alpaca/Coinbase/Binance/SimulatedCrypto adapters are thin wrappers.
- **Hard separation between research and trading.** `live/` is protected. Every decision is gated by `risk` + `compliance` checks before reaching the broker, and every gate decision lands in `journal/YYYY-MM-DD.jsonl` as a signed-off record.
- **LLMs as governance, not oracles.** Claude scores news sentiment (with ticker anonymization), recommends kill/promote actions, but never invents a trading rule. Strategies are mechanical and unit-tested.
- **Moonshot lanes are paper-only.** HFT, $100→$2M aspirational, copy-trading, and LLM-discretionary research all run in a sandboxed lane. None can bridge to a live broker without passing the standard live-readiness gate (`scripts/check_live_ready.py`).

## What's in it

### Multi-agent architecture
Five independent agents share the same risk/journal/dashboard infrastructure but trade their own asset classes:
- `equity_agent` — ETFs + large caps
- `gold_agent` — GLD/IAU/GDX
- `bonds_agent` — TLT/IEF/AGG/BND
- `crypto_agent` — BTC/ETH (24/7 via Coinbase Advanced / Alpaca Crypto / Binance testnet / simulated)
- `governance_agent` — LLM-driven kill/promote/halt recommendations (never executes orders)

### Strategies (mechanical, hand-coded, walk-forward validated)
- `mr_etf` — Bollinger(20,2) + RSI(2)<10 mean reversion, ADX(14)<20 gate
- `failed_breakout` — Donchian rejection + Williams VIX Fix + ADX gate, R-multiple guard (research-only after 2018-2025 walk-forward)
- `ma_pullback_trend` — 20/200 SMA pullback in trend (marginal — 1.0 Sharpe, 21% DD)
- `range_shift_pullback` — first pullback after Donchian range shift
- `momentum_xs` — Asness/Moskowitz 12-1 cross-sectional momentum
- `macro_regime_filter` — VIX + 200-DMA regime classifier (used as exposure scalar)
- `wheel_etf` — cash-secured put wheel (stub — needs Polygon options data)

### Alternative-data layer (legal, public sources)
- **SEC Form 4** insider transactions — XML parser with cluster + repeat-buyer scoring; uses filing-date for look-ahead protection
- **Congress trades** — Quiver-gated; watchlist boost only, never direct entry
- **Crypto smart-money wallets** — Nansen-gated; shadow-copy first, never auto-execute
- **News sentiment** — Finnhub free tier + Claude Haiku 4.5 with ticker anonymization

### ML + RL overlay (paper-only)
- LightGBM gradient boosting on engineered features, monthly retrain, embargoed walk-forward CV
- Champion/challenger model promotion; auto-reject if `train_sharpe > 2 × holdout_sharpe`
- Linear Q-agent (SARSA-λ) RL research lane in `src/moonshot/rl/` — paper sandbox only

### 24/7 runner
- APScheduler-based runner with Redis job store (fakeredis fallback)
- Market-hours predicates per asset class (NYSE for equities, always-open for crypto)
- Dead-man-switch watchdog (90s threshold during equity RTH, 5min for crypto)
- Heartbeat to Redis, journal-replay reconciliation on boot
- Scheduled retraining (monthly) — NOT continuous self-retraining

### Risk + compliance + journal
- Hard caps in `src/config.py`: 1% per trade, 6% portfolio heat, 10% single position, -2% daily halt, -15% drawdown halt — validators forbid loosening
- Two-gate execution (risk + compliance) before any broker submission
- Append-only JSONL journal with fsync + secret redaction
- Promotion gate (`src/backtest/promotion.py`) enforces 7 criteria before a strategy reaches paper; live-readiness gate (`scripts/check_live_ready.py`) enforces 9 before a strategy reaches real capital

### Dashboard
- FastAPI backend with 11 multi-agent endpoints + WebSocket fan-out (signal/fill/coherence/halt events)
- Next.js 15 frontend with 6 views: Portfolio, Agents, Strategies, Signals stream, Alt-data feed, Backtest explorer
- Kill switch (typing `FLATTEN` confirms)

### Observability
- `structlog` JSON logs, Prometheus metrics, Discord alerts on WARNING+ (`DISCORD_WEBHOOK_URL` env)
- Cost counter (data subs + LLM tokens + commissions) per agent

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

1. Paper-only (`ALPACA_PAPER_TRADE=True`). Crypto defaults to `SimulatedCryptoBroker` (no API keys, no external bridge).
2. Two-gate execution: the risk check **and** the compliance check must both APPROVE before the executor may submit.
3. Per-trade risk ≤ 1% · portfolio heat ≤ 6% · single position ≤ 10% · daily halt at −2% · drawdown halt at −15%.
4. Every cycle appends one redacted JSONL record to `journal/YYYY-MM-DD.jsonl`. **No record, no order.**
5. `live/` is protected. Edits require plan mode.
6. No third-party / nominee / shared accounts. Refuse on sight.
7. **Moonshot lanes never bridge to a live broker.** HFT sandbox, copy-trading shadow, LLM-discretionary, and $100→$2M aspirational accounts are paper-only by design — `LIVE_BROKER_BRIDGE = False` is a class invariant enforced by tests.
8. **Promotion gates** (paper) and **live-readiness gates** (real capital) are two distinct, hard chokepoints with measurable criteria. No strategy reaches paper or live by intuition.

## Promotion gates (strategy → paper)

Enforced by `src/backtest/promotion.py:gate()`:
- ≥ 30 daily / 100 intraday trades
- profit factor > 1.2 after fees + slippage
- max DD ≤ 20%
- Sharpe ≥ 0.5
- per-window Sharpe std/|mean| ≤ 0.5 (regime stability)
- correlation with all live strategies ≤ 0.5 preferred (alarm at > 0.7 → kill weaker)
- no single trade > 20% of total P&L

## Live-readiness gates (paper → small real capital)

Enforced by `scripts/check_live_ready.py`:
1. ≥ 6 months forward paper duration
2. live Sharpe ≥ 0.7 × backtest Sharpe
3. live max DD ≤ 1.3 × backtest max DD
4. ≥ 150 trades total
5. slippage MAE ≤ 5 bps vs backtest assumption
6. 0 risk-cap breaches in journal in last 90 days
7. coherence (live_WR/backtest_WR) ≥ 0.5 in last 30 days
8. 0 drift-detector halts in last 30 days
9. pairwise correlation with all live strategies ≤ 0.7

**Real-capital ladder:** $1k → $2.5k after 30 days clean → $5k → $10k. Cannot exceed without coordinated PR change.

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
