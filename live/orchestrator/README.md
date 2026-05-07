# Orchestrator Coordination Layer

Multi-Claude session coordination via filesystem primitives. No databases, no sockets,
no network — everything in `live/orchestrator/` so any session can read/write safely.

## Directory layout

```
live/orchestrator/
  watcher/          # Watcher session verdicts (health snapshots, anomaly alerts)
  research/         # Researcher session output (strategy scouting, market analysis)
  backtests/        # Backtester session artifacts (run dirs, metrics.json)
  improver/         # Improver session output (patch proposals, param tuning notes)
  handoff/
    watcher/        # Brief passed to / from the Watcher session
    researcher/     # Brief passed to / from the Researcher session
    backtester/     # Brief passed to / from the Backtester session
    improver/       # Brief passed to / from the Improver session
    operator/       # Brief passed to / from the Operator session
  locks/            # Advisory lock files (<role>.lock JSON)
```

## Advisory lock protocol

Lock file: `locks/<role>.lock`
Format:
```json
{"pid": 12345, "started_at": "2026-05-06T10:00:00+00:00", "role": "watcher", "ttl_seconds": 1800}
```

A lock is **stale** and may be reclaimed by any session if:
- the PID is no longer alive (`os.kill(pid, 0)` raises `ProcessLookupError`), OR
- `now - started_at > ttl_seconds`

Use `src.orchestrator.lock.acquire(role)` / `.release(role)` / `.is_locked(role)`.
Locks are advisory only — they signal intent, not hard mutual exclusion.

## Handoff brief protocol

Path: `handoff/<role>/brief.md`
Written atomically via write-temp-rename so readers never see a partial file.

Each session should:
1. Read its own `handoff/<role>/brief.md` at startup for context from the prior session.
2. `write_brief(role, body)` before handing off to summarize state, decisions, and next steps.

Use `src.orchestrator.handoff.read_brief(role)` / `.write_brief(role, body)`.

## Role schedules and staleness cadences

| Role       | Expected cadence | Dir           |
|------------|-----------------|---------------|
| watcher    | 15 min          | watcher/      |
| researcher | 4 h             | research/     |
| backtester | 24 h            | backtests/    |
| improver   | 7 d             | improver/     |
| operator   | 4 h             | handoff/operator/ |

The dashboard panel at `/api/orchestrator/state` colors staleness based on these cadences:
- **green (fresh)**: last update within 1× cadence
- **yellow (warn)**: last update within 2× cadence
- **red (stale)**: older or no data

## Invariants

- The orchestrator layer is **read-only on the trading codebase**.
  It MUST NOT touch `src/runtime/`, `src/strategies/`, or `src/risk/`.
- The orchestrator MUST NOT place trades, modify risk caps, or kill the bot process.
- Writes are confined to `live/orchestrator/`, `src/orchestrator/`, and
  `dashboard/api/orchestrator.py` / the matching test files.

## Spin-up checklist per session

```
watcher    : read handoff/watcher/brief.md → acquire lock → write verdicts to watcher/
researcher : read handoff/researcher/brief.md → acquire lock → write reports to research/
backtester : read handoff/backtester/brief.md → acquire lock → write run dirs to backtests/
improver   : read handoff/improver/brief.md → acquire lock → write proposals to improver/
operator   : read handoff/operator/brief.md → (no lock needed; already running)
```
