"""Liveness heartbeat written to Redis.

The runner emits a heartbeat every 15s; an external watchdog
(``scripts/watchdog.py``) reads it and triggers a flatten if the heartbeat
is stale. Keeping the contract minimal lets us swap implementations
(local file, prometheus pushgateway) later without touching callers.

Key shape:  ``runner:heartbeat:{role}``  (string, value = unix epoch seconds)
TTL:        60 seconds. If we miss four consecutive 15s writes the key
            disappears, which the watchdog interprets as "process is gone".
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_HEARTBEAT_TTL_SEC = 60
_KEY_PREFIX = "runner:heartbeat:"


def _key(role: str) -> str:
    return f"{_KEY_PREFIX}{role}"


def write_heartbeat(redis_client: Any, role: str = "primary") -> float | None:
    """Write the current unix timestamp to Redis with a 60s TTL.

    Returns the timestamp written, or ``None`` if Redis is unavailable.
    Never raises - heartbeat failures must not crash the runner.
    """
    if redis_client is None:
        return None
    ts = time.time()
    try:
        redis_client.set(_key(role), str(ts), ex=_HEARTBEAT_TTL_SEC)
    except Exception as e:
        log.warning("heartbeat write failed: %s", e)
        return None
    return ts


def read_heartbeat(redis_client: Any, role: str = "primary") -> float | None:
    """Return the last heartbeat timestamp, or ``None`` if missing/expired."""
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(_key(role))
    except Exception as e:
        log.warning("heartbeat read failed: %s", e)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
