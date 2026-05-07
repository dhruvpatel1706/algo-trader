# Operator Handoff Brief — Orchestrator Layer

**Written by:** orchestrator-builder session  
**Date:** 2026-05-06  
**Status:** All deliverables complete, 24 tests passing, lint clean.

---

## What's Ready

| Deliverable | Path | Notes |
|---|---|---|
| Directory layout | `live/orchestrator/{watcher,research,backtests,improver,handoff/<role>,locks}/` | All dirs exist |
| Lock primitives | `src/orchestrator/lock.py` | `acquire / release / is_locked` |
| Handoff primitives | `src/orchestrator/handoff.py` | `read_brief / write_brief` (atomic rename) |
| Dashboard API | `dashboard/api/orchestrator.py` | `GET /api/orchestrator/state` |
| Dashboard UI | `dashboard/web/components/OrchestratorPanel.tsx` | Mounted in PortfolioView |
| Tests | `tests/unit/orchestrator/` + `tests/unit/dashboard/test_orchestrator_api.py` | 24 tests |
| Protocol docs | `live/orchestrator/README.md` | Full protocol spec |

---

## What to Import

```python
# In any Claude session script:
from src.orchestrator.lock import acquire, release, is_locked
from src.orchestrator.handoff import read_brief, write_brief
```

Lock files live at `live/orchestrator/locks/<role>.lock` (JSON, auto-created).  
Handoff briefs live at `live/orchestrator/handoff/<role>/brief.md`.

---

## Invariants (Must Not Break)

- Sessions MUST NOT place trades, edit risk caps, or invoke the kill endpoint.
- Write ONLY to `live/orchestrator/` and session-owned output dirs.
- Always call `release(role)` in a `finally` block when done.

---

## Spin-Up Checklist — Other Sessions

### Watcher Session
```
1. cd /Users/dhruvpatel/Desktop/algo-trader
2. from src.orchestrator.lock import acquire, release
3. acquire("watcher")  # returns False if already held — exit
4. Write verdicts to live/orchestrator/watcher/health_<YYYYMMDD>.md
5. write_brief("watcher", "...") after each cycle
6. release("watcher") in finally block
7. Cadence: every 15 min
```

### Researcher Session
```
1. cd /Users/dhruvpatel/Desktop/algo-trader
2. acquire("researcher")  # returns False if already held — exit
3. Write research output to live/orchestrator/research/<slug>.md
4. write_brief("researcher", "...") summarising findings
5. release("researcher") in finally block
6. Cadence: every 4 h
```

### Backtester / Improver Session
```
1. cd /Users/dhruvpatel/Desktop/algo-trader
2. acquire("backtester") / acquire("improver")
3. Backtester writes to live/orchestrator/backtests/; Improver to live/orchestrator/improver/
4. write_brief("<role>", "...") after each run
5. release("<role>") in finally block
6. Cadence: backtester 24 h, improver 7 d
7. DO NOT touch src/runtime/, src/strategies/, src/risk/
```

---

## Dashboard

- Panel visible at http://localhost:3000 under AnalyticsPanel.
- API: `GET http://localhost:8000/api/orchestrator/state`
- Auto-refreshes every 30 s; green = fresh, yellow = warn, red = stale.
