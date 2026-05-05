"""Kill-switch logic. Cancel all orders, close all positions, halt strategies, write incident.

A kill is the only mutating broker action reachable from the dashboard. Even
though the dashboard's `BrokerProxy` is hard-coded to `paper=True`, this code
re-asserts the v1 invariant `LIVE_TRADING != "1"` so that flipping a single
config knob can never land an unattended broker mutation against real capital.

The flow is intentionally:
  1. Write `kill_intent` to the JournalWriter BEFORE any broker call (fsync'd).
  2. Cancel orders, close positions, halt strategies.
  3. Write `kill_complete` to the journal AND the incidents JSON.

The journal-of-record is the source of truth for recovery; the incident JSON
is for the dashboard's incident view.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.config import PROJECT_ROOT, get_settings
from src.journal.writer import JournalWriter

INCIDENTS_DIR = PROJECT_ROOT / "live" / "incidents"
JOURNAL_DIR = PROJECT_ROOT / "journal"


def execute_kill(
    broker_proxy,
    state,
    *,
    reason: str,
    requested_by: str = "dashboard",
    journal: JournalWriter | None = None,
) -> dict:
    """Cancel orders, close positions, halt strategies, persist incident record.

    Re-checks the v1 paper-only invariant before mutating anything. Tests can
    inject a `JournalWriter` pointing at a tmp dir; production callers omit it
    and the function uses the repo's journal/.
    """
    settings = get_settings()
    if settings.LIVE_TRADING == "1":
        raise RuntimeError(
            "execute_kill refuses to run with LIVE_TRADING=1; v1 is paper-only"
        )

    started = datetime.now(UTC)
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    writer = journal or JournalWriter(JOURNAL_DIR)

    # Audit-of-record BEFORE any broker mutation. Process crash between this
    # fsync and the broker calls leaves a journal record of intent — recovery
    # logic flags it and the operator investigates.
    writer.write(
        {
            "event": "kill_intent",
            "ts_started": started.isoformat(),
            "requested_by": requested_by,
            "reason": reason,
        }
    )

    cancelled = broker_proxy.cancel_all_orders()
    flattened = broker_proxy.close_all_positions()
    state.halt(reason)

    finished = datetime.now(UTC)
    incident = {
        "ts_started": started.isoformat(),
        "ts_finished": finished.isoformat(),
        "requested_by": requested_by,
        "reason": reason,
        "orders_cancelled": cancelled or [],
        "positions_flattened": flattened or [],
        "halted_strategies": [s["name"] for s in state.list_strategies()],
        "duration_ms": int((finished - started).total_seconds() * 1000),
    }

    writer.write({"event": "kill_complete", **incident})

    fname = INCIDENTS_DIR / f"{started.strftime('%Y-%m-%dT%H-%M-%S')}_kill.json"
    fname.write_text(json.dumps(incident, indent=2, default=str))
    return incident


def list_incidents(limit: int = 50) -> list[dict]:
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(INCIDENTS_DIR.glob("*.json"), reverse=True)[:limit]
    return [json.loads(f.read_text()) for f in files]


def latest_incident_path() -> Path | None:
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(INCIDENTS_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None
