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


# ---------------------------------------------------------------------------
# Out-of-spec session output fallbacks (top-level files)
# ---------------------------------------------------------------------------


def test_top_level_json_brief_surfaced_when_canonical_missing(
    client: TestClient,
):
    """Researcher session that wrote ``researcher_brief.json`` at the top
    level (not the canonical handoff path) should still be surfaced in
    the dashboard's brief_excerpt — postel's law on read."""
    from dashboard.api import orchestrator as m

    payload = {
        "session": 1,
        "last_run_utc": "2026-05-07T02:51:17Z",
        "notes": "First researcher session. DOGE leads watchlist at 0.59.",
        "watchlist": ["DOGEUSDT", "ETHUSDT"],
    }
    (m.ORCHESTRATOR_DIR / "researcher_brief.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    body = client.get("/api/orchestrator/state").json()
    state = body["roles"]["researcher"]
    assert state["brief_excerpt"] is not None
    assert "DOGE" in state["brief_excerpt"]
    assert state["latest_verdict_path"] is not None
    assert state["latest_verdict_path"].endswith("researcher_brief.json")
    assert state["staleness"] == "fresh"


def test_top_level_md_brief_surfaced_when_canonical_missing(client: TestClient):
    from dashboard.api import orchestrator as m

    (m.ORCHESTRATOR_DIR / "improver_brief.md").write_text(
        "# Improver session 1\nProposed: tighten ETH stop to 3% from 5%."
    )
    body = client.get("/api/orchestrator/state").json()
    state = body["roles"]["improver"]
    assert state["brief_excerpt"] is not None
    assert "Improver" in state["brief_excerpt"]


def test_canonical_brief_wins_over_top_level_fallback(client: TestClient):
    """Both files exist → canonical handoff/<role>/brief.md is preferred so
    operators can override out-of-spec session outputs by hand."""
    from dashboard.api import orchestrator as m

    (m.ORCHESTRATOR_DIR / "researcher_brief.json").write_text(
        json.dumps({"notes": "FALLBACK PATH WRITE"})
    )
    (m.ORCHESTRATOR_DIR / "handoff" / "researcher" / "brief.md").write_text(
        "# CANONICAL PATH WRITE — should win"
    )
    body = client.get("/api/orchestrator/state").json()
    excerpt = body["roles"]["researcher"]["brief_excerpt"]
    assert excerpt is not None
    assert "CANONICAL" in excerpt
    assert "FALLBACK" not in excerpt


def test_top_level_lock_file_recognised_when_pid_alive(client: TestClient):
    """``lock_<role>.json`` at top level should be treated as a held lock
    only when the recorded PID is still alive — using our own pid for the
    test guarantees liveness."""
    from dashboard.api import orchestrator as m

    own_pid = os.getpid()
    payload = {
        "role": "backtester",
        "pid": own_pid,
        "acquired_at": "2026-05-07T02:58:00+00:00",
    }
    (m.ORCHESTRATOR_DIR / "lock_backtester.json").write_text(json.dumps(payload))
    body = client.get("/api/orchestrator/state").json()
    state = body["roles"]["backtester"]
    assert state["lock_held"] is True
    assert state["lock_pid"] == own_pid


def test_stale_top_level_lock_with_dead_pid_not_held(client: TestClient):
    """A stale lock file (PID is dead, file wasn't cleaned up) must NOT
    report lock_held=True or we lose the ability to acquire fresh."""
    from dashboard.api import orchestrator as m

    payload = {
        "role": "backtester",
        "pid": 99999999,  # almost certainly not alive
        "acquired_at": "2026-05-07T02:58:00+00:00",
    }
    (m.ORCHESTRATOR_DIR / "lock_backtester.json").write_text(json.dumps(payload))
    body = client.get("/api/orchestrator/state").json()
    state = body["roles"]["backtester"]
    assert state["lock_held"] is False
    assert state["lock_pid"] is None
