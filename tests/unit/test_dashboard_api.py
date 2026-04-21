"""Dashboard API: route smoke tests via FastAPI TestClient."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    # Point JOURNAL_DIR + INCIDENTS_DIR at a tmp dir so tests don't touch real data.
    journal = tmp_path / "journal"
    journal.mkdir()
    incidents = tmp_path / "live" / "incidents"
    incidents.mkdir(parents=True)

    from dashboard.api import journal_reader, kill

    monkeypatch.setattr(journal_reader, "JOURNAL_DIR", journal)
    monkeypatch.setattr(kill, "INCIDENTS_DIR", incidents)

    # Reset state singleton between tests.
    from dashboard.api import state as state_module

    monkeypatch.setattr(state_module, "_state", None)

    from dashboard.api.main import app

    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_strategies_default(client):
    r = client.get("/api/strategies")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert names == {"mr_etf", "wheel_etf"}
    assert all(s["enabled"] for s in r.json())


def test_pause_resume(client):
    r = client.post("/api/strategies/mr_etf/pause")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = client.get("/api/strategies")
    mr = next(s for s in r.json() if s["name"] == "mr_etf")
    assert mr["enabled"] is False
    r = client.post("/api/strategies/mr_etf/resume")
    assert r.json()["enabled"] is True


def test_pause_unknown_strategy(client):
    r = client.post("/api/strategies/does_not_exist/pause")
    assert r.status_code == 404


def test_kill_requires_confirm_token(client):
    r = client.post("/api/kill", json={"confirm": "yes please", "reason": "test"})
    assert r.status_code == 400


def test_halt_status_and_reset(client):
    assert client.get("/api/halt").json()["halted"] is False
    # We can't run kill without alpaca creds, but we can simulate halt via state.
    from dashboard.api.state import get_state

    get_state().halt("manual test")
    assert client.get("/api/halt").json()["halted"] is True
    r = client.post("/api/halt/reset")
    assert r.json()["halted"] is False


def test_trades_reads_journal(client, tmp_path):
    from dashboard.api import journal_reader

    today = (
        journal_reader.JOURNAL_DIR
        / f"{__import__('datetime').date.today().strftime('%Y-%m-%d')}.jsonl"
    )
    today.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00Z",
                "event": "submit",
                "subject": "SPY",
                "qty": 1,
                "side": "buy",
            }
        )
        + "\n"
    )
    r = client.get("/api/trades")
    assert r.status_code == 200
    assert any(e.get("subject") == "SPY" for e in r.json())


def test_costs_starts_zero(client):
    r = client.get("/api/costs")
    body = r.json()
    assert body["llm_input_tokens"] == 0
    assert body["api_requests"] == 0
