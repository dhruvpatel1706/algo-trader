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
    """Read the role's handoff brief, with a top-level fallback.

    Canonical path: ``live/orchestrator/handoff/<role>/brief.md``.
    Fallback path: ``live/orchestrator/<role>_brief.{md,json}`` —
    permissive on read because some session implementations don't use
    the ``src.orchestrator.handoff`` primitives and write to the top
    level instead. Excerpt format adapts: markdown shows the first 200
    characters; JSON is summarized to a one-line key:value digest.
    """
    md = ORCHESTRATOR_DIR / "handoff" / role / "brief.md"
    if md.exists():
        try:
            text = md.read_text()
            excerpt = text[:200].strip()
            return excerpt or None
        except OSError:
            pass

    fallback_md = ORCHESTRATOR_DIR / f"{role}_brief.md"
    if fallback_md.exists():
        try:
            text = fallback_md.read_text()
            excerpt = text[:200].strip()
            return excerpt or None
        except OSError:
            pass

    fallback_json = ORCHESTRATOR_DIR / f"{role}_brief.json"
    if fallback_json.exists():
        try:
            data = json.loads(fallback_json.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        # Summarize a few well-known top-level keys; fall through to the
        # raw notes / first-string-value for unknown shapes.
        for key in ("notes", "summary", "tldr", "headline"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        # No known summary key — show last_run + a short shape preview.
        last_run = data.get("last_run_utc") or data.get("ts") or ""
        keys = ", ".join(list(data.keys())[:6])
        excerpt = f"{last_run} | keys: {keys}".strip(" |")
        return excerpt[:200] or None
    return None


def _lock_info(role: str) -> tuple[bool, int | None]:
    """Check both canonical and top-level lock paths.

    Canonical:  ``live/orchestrator/locks/<role>.lock``
    Fallback:   ``live/orchestrator/lock_<role>.json``

    Both shapes contain JSON ``{pid, acquired_at OR started_at, role, ...}``.
    Returns held=True only when the JSON parses AND the recorded pid is
    still alive; otherwise False (so a stale lock file from a crashed
    session doesn't leave us reporting "held" forever).
    """
    candidates = [
        ORCHESTRATOR_DIR / "locks" / f"{role}.lock",
        ORCHESTRATOR_DIR / f"lock_{role}.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data: dict = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int):
            continue
        # Verify pid is still alive — stale locks (process died, file
        # not cleaned up) shouldn't report as held.
        try:
            import os  # noqa: PLC0415

            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        return True, pid
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
        # Top-level fallbacks (out-of-spec session outputs).
        fallback_mtime: datetime | None = None
        for tail in (f"{role}_brief.md", f"{role}_brief.json", f"lock_{role}.json"):
            f = ORCHESTRATOR_DIR / tail
            if f.exists():
                ts = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
                fallback_mtime = max(fallback_mtime, ts) if fallback_mtime else ts
        candidates = [
            t
            for t in (output_mtime, handoff_mtime, fallback_mtime)
            if t is not None
        ]
        last_update = max(candidates) if candidates else None

        lock_held, lock_pid = _lock_info(role)

        verdict_path = _latest_file(output_dir)
        if verdict_path is None:
            # Surface the top-level fallback file path when no canonical
            # output exists, so the dashboard can still link to it.
            for tail in (f"{role}_brief.md", f"{role}_brief.json"):
                f = ORCHESTRATOR_DIR / tail
                if f.exists():
                    verdict_path = str(f)
                    break

        roles[role] = RoleState(
            last_update_iso=last_update.isoformat() if last_update else None,
            lock_held=lock_held,
            lock_pid=lock_pid,
            latest_verdict_path=verdict_path,
            brief_excerpt=_brief_excerpt(role),
            staleness=_staleness(last_update, _CADENCE[role], now),
        )

    return OrchestratorStateResponse(roles=roles, as_of=now.isoformat())
