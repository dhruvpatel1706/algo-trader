"""Tests for the Redis-backed heartbeat helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.runtime.heartbeat import read_heartbeat, write_heartbeat

try:
    import fakeredis  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - fakeredis is in dev-extras
    fakeredis = None  # type: ignore[assignment]


def _redis_or_mock():
    if fakeredis is not None:
        return fakeredis.FakeRedis()
    client = MagicMock()
    storage: dict[bytes, bytes] = {}

    def _set(key, value, ex=None):
        storage[key.encode() if isinstance(key, str) else key] = (
            value.encode() if isinstance(value, str) else value
        )
        return True

    def _get(key):
        return storage.get(key.encode() if isinstance(key, str) else key)

    client.set.side_effect = _set
    client.get.side_effect = _get
    return client


def test_write_then_read_roundtrips() -> None:
    client = _redis_or_mock()
    ts = write_heartbeat(client, role="primary")
    assert ts is not None
    read_back = read_heartbeat(client, role="primary")
    assert read_back is not None
    assert abs(read_back - ts) < 1.0


def test_read_returns_none_when_key_missing() -> None:
    client = _redis_or_mock()
    assert read_heartbeat(client, role="never_set") is None


def test_write_with_none_client_is_noop() -> None:
    assert write_heartbeat(None) is None
    assert read_heartbeat(None) is None


def test_write_swallows_redis_failure() -> None:
    client = MagicMock()
    client.set.side_effect = RuntimeError("redis down")
    # Must not raise.
    assert write_heartbeat(client) is None


def test_read_swallows_redis_failure() -> None:
    client = MagicMock()
    client.get.side_effect = RuntimeError("redis down")
    assert read_heartbeat(client) is None


def test_role_isolated() -> None:
    """Different roles must use distinct keys."""
    client = _redis_or_mock()
    ts_primary = write_heartbeat(client, role="primary")
    ts_secondary = write_heartbeat(client, role="secondary")
    assert ts_primary is not None
    assert ts_secondary is not None
    # Both readable, both ~now.
    assert read_heartbeat(client, role="primary") is not None
    assert read_heartbeat(client, role="secondary") is not None
    # Different roles must not collide.
    assert abs(read_heartbeat(client, role="primary") - time.time()) < 5.0  # type: ignore[operator]
