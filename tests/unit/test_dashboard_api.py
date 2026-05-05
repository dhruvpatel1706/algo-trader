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
    monkeypatch.setattr(kill, "JOURNAL_DIR", journal)

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


def test_kill_writes_journal_intent_and_complete(tmp_path, monkeypatch):
    """execute_kill must write `kill_intent` BEFORE the broker calls and
    `kill_complete` after — both fsync'd. Recovery and audit rely on the
    intent record existing even if the process crashes mid-kill.
    """
    from dashboard.api import kill as kill_mod
    from src.journal.writer import JournalWriter

    journal_dir = tmp_path / "journal"
    journal = JournalWriter(journal_dir)
    incidents = tmp_path / "incidents"
    incidents.mkdir()
    monkeypatch.setattr(kill_mod, "INCIDENTS_DIR", incidents)

    class _FakeBroker:
        def cancel_all_orders(self):
            return ["o1", "o2"]

        def close_all_positions(self):
            return ["AAPL", "TLT"]

    class _FakeState:
        def halt(self, reason):
            self.halted_with = reason

        def list_strategies(self):
            return [{"name": "mr_etf"}, {"name": "wheel_etf"}]

    out = kill_mod.execute_kill(
        _FakeBroker(),
        _FakeState(),
        reason="unit-test",
        requested_by="ci",
        journal=journal,
    )
    assert out["orders_cancelled"] == ["o1", "o2"]
    assert out["positions_flattened"] == ["AAPL", "TLT"]

    today = journal.path_for()
    lines = today.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in lines]
    # `kill_intent` must come before `kill_complete` and both must be present.
    assert events == ["kill_intent", "kill_complete"]


def test_kill_refuses_with_live_trading_set(tmp_path, monkeypatch):
    """Hard guard: even though BrokerProxy is paper-only, refuse to run if
    LIVE_TRADING=1 is in the environment. Defense in depth."""
    from dashboard.api import kill as kill_mod
    from src.journal.writer import JournalWriter

    journal = JournalWriter(tmp_path / "journal")
    monkeypatch.setattr(kill_mod, "INCIDENTS_DIR", tmp_path / "incidents")

    monkeypatch.setenv("LIVE_TRADING", "1")
    # Force settings to re-read the env.
    from src.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    class _NoBroker:
        def cancel_all_orders(self):  # pragma: no cover - never reached
            raise AssertionError("broker must not be touched when LIVE_TRADING=1")

        def close_all_positions(self):  # pragma: no cover
            raise AssertionError("broker must not be touched when LIVE_TRADING=1")

    class _NoState:
        def halt(self, _):  # pragma: no cover
            raise AssertionError("state must not be halted when LIVE_TRADING=1")

        def list_strategies(self):  # pragma: no cover
            return []

    with pytest.raises(RuntimeError, match="LIVE_TRADING=1"):
        kill_mod.execute_kill(_NoBroker(), _NoState(), reason="x", journal=journal)
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
