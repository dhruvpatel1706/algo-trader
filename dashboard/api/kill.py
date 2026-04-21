"""Kill-switch logic. Cancel all orders, close all positions, halt strategies, write incident."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.config import PROJECT_ROOT

INCIDENTS_DIR = PROJECT_ROOT / "live" / "incidents"


def execute_kill(broker_proxy, state, *, reason: str, requested_by: str = "dashboard") -> dict:
    """Cancel orders, close positions, halt strategies, persist incident record."""
    started = datetime.now(UTC)
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)

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
