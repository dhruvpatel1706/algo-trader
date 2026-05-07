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


# ---------------------------------------------------------------------------
# /api/orchestrator/research_proposals
# ---------------------------------------------------------------------------


def _write_researcher_brief(orch_root: Path, payload: dict) -> None:
    (orch_root / "researcher_brief.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_research_proposals_empty_when_no_brief(client: TestClient):
    """No researcher brief on disk → empty proposals + watchlist, never crash."""
    body = client.get("/api/orchestrator/research_proposals").json()
    assert body["proposals"] == []
    assert body["watchlist"] == []
    assert body["top_confluence"] == []
    assert body["regime"] is None


def test_research_proposals_marks_implemented_strategies(client: TestClient):
    """A proposal whose slug maps to an existing src/strategies/<slug>.py
    must surface as status='implemented'. The pre-existing path is
    ema_ribbon_compression (shipped 2026-05-07); suffix-stripping must
    pick it up from the proposal slug 'ema_ribbon_compression_breakout'.
    The other slug deliberately doesn't exist on disk to verify the
    'proposed' branch."""
    from dashboard.api import orchestrator as m

    _write_researcher_brief(
        m.ORCHESTRATOR_DIR,
        {
            "last_run_utc": "2026-05-07T02:51:17Z",
            "new_strategy_proposals": {
                "priority_order": [
                    "1. ema_ribbon_compression_breakout — shipped today",
                    "2. on_chain_whale_flow — needs new data source",
                ],
            },
        },
    )
    body = client.get("/api/orchestrator/research_proposals").json()
    by_slug = {p["slug"]: p for p in body["proposals"]}
    assert by_slug["ema_ribbon_compression_breakout"]["status"] == "implemented"
    assert by_slug["on_chain_whale_flow"]["status"] == "proposed"
    # rank preserved from priority_order
    assert by_slug["ema_ribbon_compression_breakout"]["rank"] == 1
    # rationale extracted from the em-dash split
    assert "shipped today" in by_slug["ema_ribbon_compression_breakout"]["rationale"]


def test_research_proposals_surfaces_watchlist_with_triggers(client: TestClient):
    from dashboard.api import orchestrator as m

    _write_researcher_brief(
        m.ORCHESTRATOR_DIR,
        {
            "last_run_utc": "2026-05-07T02:51:17Z",
            "market_observations": {
                "regime": "correction / consolidation",
                "watchlist_for_next_session": ["DOGEUSDT", "ETHUSDT"],
                "key_levels": {
                    "DOGEUSDT": {"rsi": 28.81, "adx": 38.64, "bb_pct_b": 0.083},
                    "ETHUSDT": {"rsi": 29.92, "adx": 22.01, "bb_pct_b": 0.098},
                },
                "trigger_conditions": {
                    "DOGEUSDT": "MACD histogram crosses zero from below",
                    "ETHUSDT": "ADX expands above 25 with RSI <35",
                },
            },
        },
    )
    body = client.get("/api/orchestrator/research_proposals").json()
    assert body["regime"] == "correction / consolidation"
    by_sym = {w["symbol"]: w for w in body["watchlist"]}
    assert by_sym["DOGEUSDT"]["adx"] == 38.64
    assert by_sym["DOGEUSDT"]["trigger"].startswith("MACD")
    assert by_sym["ETHUSDT"]["rsi"] == 29.92


def test_research_proposals_top_confluence_ranked(client: TestClient):
    from dashboard.api import orchestrator as m

    _write_researcher_brief(
        m.ORCHESTRATOR_DIR,
        {
            "threshold": 0.70,
            "scan_results_ranked": [
                {"symbol": "DOGEUSDT", "confluence": 0.59, "direction": "long"},
                {"symbol": "ETHUSDT", "confluence": 0.45, "direction": "long"},
                {"symbol": "AVAXUSDT", "confluence": 0.30, "direction": "long"},
                {"symbol": "BTCUSDT", "confluence": 0.225, "direction": "long"},
                {"symbol": "LTCUSDT", "confluence": 0.225, "direction": "long"},
                {"symbol": "EXTRA", "confluence": 0.0, "direction": "neutral"},
            ],
        },
    )
    body = client.get("/api/orchestrator/research_proposals").json()
    assert body["threshold"] == 0.70
    # Only top 5 returned even when more entries exist.
    assert len(body["top_confluence"]) == 5
    assert body["top_confluence"][0]["symbol"] == "DOGEUSDT"
    assert body["top_confluence"][0]["confluence"] == 0.59
