# algo-trader dashboard (Next.js 15 + React 19)

## Run locally

```bash
# 1. backend (FastAPI on :8000)
uv run uvicorn dashboard.api.main:app --reload --port 8000

# 2. frontend (Next.js on :3000) — proxied to /api/* and /ws/*
cd dashboard/web
pnpm install
pnpm dev
```

Open http://localhost:3000.

## What's wired
- **Top bar**: equity, cash, buying power, day P&L, halt-status chip, kill switch.
- **Equity chart** (TradingView Lightweight Charts v5): v1 sketch from broker `equity` + `last_equity`. Full history needs the TimescaleDB hypertable from Phase 8.
- **Positions, trade log (journal-backed), strategies (pause/resume), cost counter, agent stream**.
- **Kill switch**: red top-right button; double-confirm modal requires typing `FLATTEN`. POSTs `/api/kill` → cancels all orders, flattens all positions, halts strategies, writes `live/incidents/<UTC-ts>_kill.json`.

## What's intentionally lightweight in v1
- No `shadcn/ui` setup; raw Tailwind primitives only. Add shadcn later if you want the named component library.
- No SSE tape (covered by `/ws/stream` + REST polling in v1).
- No symbol-detail modal yet — wire to TradingView Lightweight Charts when bar history lands.
- No auth — the dashboard is operator-local-only by design.
