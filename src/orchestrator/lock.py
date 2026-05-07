"""File-based advisory locks for multi-Claude session coordination.

Lock file: live/orchestrator/locks/<role>.lock
Format:    JSON {pid, started_at, role, ttl_seconds}

A lock is stale (and may be reclaimed) when:
  - the recorded PID is no longer alive, OR
  - now - started_at > ttl_seconds
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from src.config import PROJECT_ROOT

LOCKS_DIR = PROJECT_ROOT / "live" / "orchestrator" / "locks"


def _lock_path(role: str) -> Path:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return LOCKS_DIR / f"{role}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def is_locked(role: str) -> tuple[bool, dict]:
    """Return (held, lock_data). Stale locks return (False, data)."""
    path = _lock_path(role)
    if not path.exists():
        return False, {}
    try:
        data: dict = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False, {}

    pid = data.get("pid")
    if pid is None or not _pid_alive(int(pid)):
        return False, data

    started_at = data.get("started_at", "")
    ttl = int(data.get("ttl_seconds", 1800))
    if started_at:
        try:
            start = datetime.fromisoformat(started_at)
            if (datetime.now(UTC) - start).total_seconds() > ttl:
                return False, data
        except ValueError:
            pass

    return True, data


def acquire(role: str, ttl_seconds: int = 1800) -> bool:
    """Try to acquire the advisory lock for *role*.

    Returns True on success. Returns False if a live (non-stale) lock exists.
    Stale locks (dead PID or TTL expired) are silently reclaimed.
    """
    locked, _ = is_locked(role)
    if locked:
        return False

    path = _lock_path(role)
    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
        "role": role,
        "ttl_seconds": ttl_seconds,
    }
    tmp = path.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.rename(path)
    return True


def release(role: str) -> None:
    """Release the advisory lock for *role*. No-op if not held."""
    path = _lock_path(role)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
