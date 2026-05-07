"""Unit tests for GET /api/orchestrator/state — read-only, filesystem-isolated."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    orch = tmp_path / "live" / "orchestrator"
    for sub in ("watcher", "research", "backtests", "improver", "locks"):
        (orch / sub).mkdir(parents=True)
    for role in ("watcher", "researcher", "backtester", "improver", "operator"):
        (orch / "handoff" / role).mkdir(parents=True)

    from dashboard.api import orchestrator as m

    monkeypatch.setattr(m, "ORCHESTRATOR_DIR", orch)

    from dashboard.api.main import app

    return TestClient(app)


def test_state_returns_all_roles(client: TestClient):
    r = client.get("/api/orchestrator/state")
    assert r.status_code == 200
    body = r.json()
    assert set(body["roles"].keys()) == {
        "watcher", "researcher", "backtester", "improver", "operator"
    }
    assert "as_of" in body


def test_state_shape_per_role(client: TestClient):
    body = client.get("/api/orchestrator/state").json()
    for role_state in body["roles"].values():
        assert "last_update_iso" in role_state
        assert "lock_held" in role_state
        assert "lock_pid" in role_state
        assert "latest_verdict_path" in role_state
        assert "brief_excerpt" in role_state
        assert "staleness" in role_state


def test_empty_dirs_are_stale(client: TestClient):
    body = client.get("/api/orchestrator/state").json()
    for role_state in body["roles"].values():
        assert role_state["staleness"] == "stale"
        assert role_state["lock_held"] is False
        assert role_state["lock_pid"] is None
        assert role_state["brief_excerpt"] is None


def test_lock_file_reflected(client: TestClient, tmp_path: Path):
    from dashboard.api import orchestrator as m

    lock_dir = m.ORCHESTRATOR_DIR / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "watcher.lock").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "role": "watcher",
                "started_at": "2026-01-01T00:00:00+00:00",
                "ttl_seconds": 1800,
            }
        )
    )
    body = client.get("/api/orchestrator/state").json()
    assert body["roles"]["watcher"]["lock_held"] is True
    assert body["roles"]["watcher"]["lock_pid"] == os.getpid()
    # Others unaffected
    assert body["roles"]["researcher"]["lock_held"] is False


def test_brief_excerpt_shown(client: TestClient, tmp_path: Path):
    from dashboard.api import orchestrator as m

    brief_dir = m.ORCHESTRATOR_DIR / "handoff" / "researcher"
    brief_dir.mkdir(parents=True, exist_ok=True)
    (brief_dir / "brief.md").write_text("# Researcher Handoff\nKey findings here.\n")
    body = client.get("/api/orchestrator/state").json()
    excerpt = body["roles"]["researcher"]["brief_excerpt"]
    assert excerpt is not None
    assert "Researcher" in excerpt


def test_latest_verdict_path_shown(client: TestClient, tmp_path: Path):
    from dashboard.api import orchestrator as m

    verdict_dir = m.ORCHESTRATOR_DIR / "watcher"
    verdict_dir.mkdir(parents=True, exist_ok=True)
    (verdict_dir / "health_20260506.md").write_text("all clear")
    body = client.get("/api/orchestrator/state").json()
    assert body["roles"]["watcher"]["latest_verdict_path"] is not None
    assert "health_20260506" in body["roles"]["watcher"]["latest_verdict_path"]


def test_fresh_file_staleness_is_fresh(client: TestClient, tmp_path: Path):
    from dashboard.api import orchestrator as m

    watcher_dir = m.ORCHESTRATOR_DIR / "watcher"
    watcher_dir.mkdir(parents=True, exist_ok=True)
    (watcher_dir / "latest.md").write_text("recent verdict")
    body = client.get("/api/orchestrator/state").json()
    assert body["roles"]["watcher"]["staleness"] == "fresh"
