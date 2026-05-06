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
    r = client.post("/api/strategies/mr_etf/pause", json={"confirm": "PAUSE"})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = client.get("/api/strategies")
    mr = next(s for s in r.json() if s["name"] == "mr_etf")
    assert mr["enabled"] is False
    r = client.post("/api/strategies/mr_etf/resume", json={"confirm": "RESUME"})
    assert r.json()["enabled"] is True


def test_pause_requires_confirm_token(client):
    # Defense in depth: a missing/wrong confirm token must reject.
    r = client.post("/api/strategies/mr_etf/pause", json={"confirm": "nope"})
    assert r.status_code == 400
    r = client.post("/api/strategies/mr_etf/resume", json={"confirm": "nope"})
    assert r.status_code == 400


def test_pause_unknown_strategy(client):
    r = client.post("/api/strategies/does_not_exist/pause", json={"confirm": "PAUSE"})
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
    r = client.post("/api/halt/reset", json={"confirm": "RESET"})
    assert r.json()["halted"] is False


def test_halt_reset_requires_confirm_token(client):
    from dashboard.api.state import get_state

    get_state().halt("manual test")
    # Without confirm, must 400 — the halt is a manual safety brake.
    r = client.post("/api/halt/reset", json={"confirm": "wrong"})
    assert r.status_code == 400
    assert client.get("/api/halt").json()["halted"] is True
    # Cleanup
    r = client.post("/api/halt/reset", json={"confirm": "RESET"})
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


def test_bot_status_returns_stopped_initially(client, monkeypatch, tmp_path):
    """GET /api/bot/status before any start returns state=stopped.

    Isolates pidfile/log paths — without this, the supervisor's
    ``_adopt_orphan_if_present()`` reads the real ``live/runtime/runner.pid``
    on this machine and adopts whatever bot is actually running. That's
    correct production behavior but breaks test reproducibility on a dev
    box that happens to have an overnight bot up.
    """
    from dashboard.api import runner_control as rc
    monkeypatch.setattr(rc, "_supervisor", None)
    monkeypatch.setattr(rc, "_PIDFILE", tmp_path / "runner.pid")
    monkeypatch.setattr(rc, "_LOGFILE", tmp_path / "runner.log")
    monkeypatch.setattr(rc, "_RUNTIME_DIR", tmp_path)
    r = client.get("/api/bot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "stopped"
    assert body["pid"] is None
    assert body["adopted"] is False


def test_bot_start_requires_confirm_token(client):
    """Empty body fails Pydantic validation (422). A wrong-string confirm
    fails our own check (400). Either way the supervisor is not called."""
    r = client.post("/api/bot/start", json={})
    assert r.status_code == 422  # Pydantic validation error
    r = client.post("/api/bot/start", json={"confirm": "yes please"})
    assert r.status_code == 400  # our HTTPException


def test_bot_stop_requires_confirm_token(client):
    r = client.post("/api/bot/stop", json={})
    assert r.status_code == 422
    r = client.post("/api/bot/stop", json={"confirm": "GO"})
    assert r.status_code == 400


def test_bot_start_with_correct_token_calls_supervisor(client, monkeypatch):
    """With the correct confirm token, /api/bot/start delegates to the
    RunnerSupervisor. We monkeypatch the supervisor's `start` so the test
    doesn't actually exec scripts/run_bot.py."""
    from dashboard.api import runner_control as rc
    from dashboard.api.runner_control import RunnerStatus

    fake_status = RunnerStatus(state="running", pid=4242, started_at="2026-05-05T00:00:00+00:00")
    calls = {"n": 0}

    class _Fake:
        def status(self):
            return RunnerStatus(state="stopped")
        def start(self):
            calls["n"] += 1
            return fake_status
        def stop(self):
            return RunnerStatus(state="stopped", exit_code=0)

    monkeypatch.setattr(rc, "_supervisor", _Fake())

    r = client.post("/api/bot/start", json={"confirm": "START"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "running"
    assert body["pid"] == 4242
    assert calls["n"] == 1


def test_bot_stop_with_correct_token_calls_supervisor(client, monkeypatch):
    from dashboard.api import runner_control as rc
    from dashboard.api.runner_control import RunnerStatus

    calls = {"n": 0}

    class _Fake:
        def status(self):
            return RunnerStatus(state="running", pid=99)
        def start(self):
            return RunnerStatus(state="running", pid=99)
        def stop(self):
            calls["n"] += 1
            return RunnerStatus(state="stopped", exit_code=0)

    monkeypatch.setattr(rc, "_supervisor", _Fake())

    r = client.post("/api/bot/stop", json={"confirm": "STOP"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "stopped"
    assert calls["n"] == 1


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
