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

# 7. Run a backtest
uv run python -m src.backtest.cli --strategy mr_etf --start 2022-01-01 --end 2024-12-31
```

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
