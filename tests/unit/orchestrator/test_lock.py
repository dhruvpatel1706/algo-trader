"""Unit tests for src.orchestrator.lock — file-based advisory locks."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    from src.orchestrator import lock as m

    monkeypatch.setattr(m, "LOCKS_DIR", tmp_path / "locks")


def test_acquire_creates_lock_file(tmp_path: Path):
    from src.orchestrator.lock import LOCKS_DIR, acquire

    assert acquire("watcher") is True
    path = LOCKS_DIR / "watcher.lock"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["pid"] == os.getpid()
    assert data["role"] == "watcher"
    assert "started_at" in data


def test_acquire_returns_false_when_already_held():
    from src.orchestrator.lock import acquire

    acquire("watcher")
    assert acquire("watcher") is False


def test_release_removes_lock_file():
    from src.orchestrator.lock import LOCKS_DIR, acquire, release

    acquire("watcher")
    release("watcher")
    assert not (LOCKS_DIR / "watcher.lock").exists()


def test_release_noop_when_no_lock():
    from src.orchestrator.lock import release

    release("watcher")  # must not raise


def test_is_locked_false_when_no_file():
    from src.orchestrator.lock import is_locked

    locked, data = is_locked("watcher")
    assert locked is False
    assert data == {}


def test_is_locked_true_when_held():
    from src.orchestrator.lock import acquire, is_locked

    acquire("watcher")
    locked, data = is_locked("watcher")
    assert locked is True
    assert data["pid"] == os.getpid()


def test_stale_lock_dead_pid_returns_false(tmp_path: Path):
    from src.orchestrator.lock import LOCKS_DIR, is_locked

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": 99_999_999,
        "started_at": "2020-01-01T00:00:00+00:00",
        "role": "watcher",
        "ttl_seconds": 1800,
    }
    (LOCKS_DIR / "watcher.lock").write_text(json.dumps(payload))
    locked, _ = is_locked("watcher")
    assert locked is False


def test_stale_lock_expired_ttl_returns_false(tmp_path: Path):
    from src.orchestrator.lock import LOCKS_DIR, is_locked

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": "2020-01-01T00:00:00+00:00",
        "role": "watcher",
        "ttl_seconds": 1,
    }
    (LOCKS_DIR / "watcher.lock").write_text(json.dumps(payload))
    locked, _ = is_locked("watcher")
    assert locked is False


def test_acquire_reclaims_stale_lock(tmp_path: Path):
    from src.orchestrator.lock import LOCKS_DIR, acquire, is_locked

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    (LOCKS_DIR / "watcher.lock").write_text(
        json.dumps(
            {
                "pid": 99_999_999,
                "started_at": "2020-01-01T00:00:00+00:00",
                "role": "watcher",
                "ttl_seconds": 1800,
            }
        )
    )
    assert acquire("watcher") is True
    locked, data = is_locked("watcher")
    assert locked is True
    assert data["pid"] == os.getpid()


def test_different_roles_independent():
    from src.orchestrator.lock import acquire, is_locked

    assert acquire("watcher") is True
    assert acquire("researcher") is True
    locked_w, _ = is_locked("watcher")
    locked_r, _ = is_locked("researcher")
    assert locked_w is True
    assert locked_r is True


def test_malformed_lock_file_returns_false(tmp_path: Path):
    from src.orchestrator.lock import LOCKS_DIR, is_locked

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    (LOCKS_DIR / "watcher.lock").write_text("not json {{{")
    locked, _data = is_locked("watcher")
    assert locked is False
