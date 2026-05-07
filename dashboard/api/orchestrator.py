"""Orchestrator state endpoint.

GET /api/orchestrator/state — read-only view of all multi-Claude session states.
Returns staleness, lock status, brief excerpt, and latest artifact path for each role.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from src.config import PROJECT_ROOT

log = logging.getLogger(__name__)

router = APIRouter()

ORCHESTRATOR_DIR = PROJECT_ROOT / "live" / "orchestrator"

# Seconds between expected writes for each role.
_CADENCE: dict[str, int] = {
    "watcher": 15 * 60,
    "researcher": 4 * 60 * 60,
    "backtester": 24 * 60 * 60,
    "improver": 7 * 24 * 60 * 60,
    "operator": 4 * 60 * 60,
}

# Role → subdirectory where that session writes its primary output.
_ROLE_DIR: dict[str, str] = {
    "watcher": "watcher",
    "researcher": "research",
    "backtester": "backtests",
    "improver": "improver",
    "operator": "handoff/operator",
}

ROLES = list(_CADENCE)


class RoleState(BaseModel):
    last_update_iso: str | None
    lock_held: bool
    lock_pid: int | None
    latest_verdict_path: str | None
    brief_excerpt: str | None
    staleness: str  # "fresh" | "warn" | "stale"


class OrchestratorStateResponse(BaseModel):
    roles: dict[str, RoleState]
    as_of: str


def _dir_latest_mtime(directory: Path) -> datetime | None:
    if not directory.exists():
        return None
    mtimes = [
        datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
        for f in directory.iterdir()
        if f.is_file()
    ]
    return max(mtimes, default=None)


def _staleness(last: datetime | None, cadence: int, now: datetime) -> str:
    if last is None:
        return "stale"
    age = (now - last).total_seconds()
    if age <= cadence:
        return "fresh"
    if age <= cadence * 2:
        return "warn"
    return "stale"


def _latest_file(directory: Path) -> str | None:
    if not directory.exists():
        return None
    files = sorted(
        (f for f in directory.iterdir() if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return str(files[0]) if files else None


def _brief_excerpt(role: str) -> str | None:
    path = ORCHESTRATOR_DIR / "handoff" / role / "brief.md"
    if not path.exists():
        return None
    try:
        text = path.read_text()
        excerpt = text[:200].strip()
        return excerpt or None
    except OSError:
        return None


def _lock_info(role: str) -> tuple[bool, int | None]:
    path = ORCHESTRATOR_DIR / "locks" / f"{role}.lock"
    if not path.exists():
        return False, None
    try:
        data: dict = json.loads(path.read_text())
        return True, data.get("pid")
    except (json.JSONDecodeError, OSError):
        return False, None


@router.get("/api/orchestrator/state", response_model=OrchestratorStateResponse)
async def orchestrator_state() -> OrchestratorStateResponse:
    """Read-only snapshot of all orchestrator session states."""
    now = datetime.now(UTC)
    roles: dict[str, RoleState] = {}

    for role in ROLES:
        output_dir = ORCHESTRATOR_DIR / _ROLE_DIR[role]
        handoff_dir = ORCHESTRATOR_DIR / "handoff" / role

        output_mtime = _dir_latest_mtime(output_dir)
        handoff_mtime = _dir_latest_mtime(handoff_dir)
        candidates = [t for t in (output_mtime, handoff_mtime) if t is not None]
        last_update = max(candidates) if candidates else None

        lock_held, lock_pid = _lock_info(role)

        roles[role] = RoleState(
            last_update_iso=last_update.isoformat() if last_update else None,
            lock_held=lock_held,
            lock_pid=lock_pid,
            latest_verdict_path=_latest_file(output_dir),
            brief_excerpt=_brief_excerpt(role),
            staleness=_staleness(last_update, _CADENCE[role], now),
        )

    return OrchestratorStateResponse(roles=roles, as_of=now.isoformat())
