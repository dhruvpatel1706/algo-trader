"""Tests for the APScheduler-backed runner.

We avoid actually starting the scheduler in unit tests; we just verify that
job registration, market-hours gating, and Redis fallback all behave.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from src.journal.writer import JournalWriter
from src.runtime import scheduler as _sched_mod
from src.runtime.scheduler import (
    Runner,
    _first_sunday_only,
    _gate_market_hours,
    _safe_call,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeScheduler:
    """Minimal stand-in for BlockingScheduler."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, fn, trigger=None, id=None, name=None, replace_existing=True):
        self.jobs.append({"fn": fn, "trigger": trigger, "id": id, "name": name})

    def start(self) -> None:  # pragma: no cover - run() not exercised in unit tests
        self.started = True

    def shutdown(self, wait: bool = False) -> None:  # pragma: no cover
        self.started = False


def _make_agent_stub(name: str) -> MagicMock:
    a = MagicMock()
    a.name = name
    a.evaluate = MagicMock(return_value=[])
    return a


# ---------------------------------------------------------------------------
# Runner construction
# ---------------------------------------------------------------------------


def test_runner_initializes_without_redis() -> None:
    """Construction must not raise when Redis is unavailable."""
    runner = Runner(redis_url=None)
    assert runner.scheduler is not None


def test_runner_falls_back_when_redis_url_invalid() -> None:
    """A bogus Redis URL falls through to the in-memory store with a warning."""
    runner = Runner(redis_url="redis://nonexistent.invalid.localhost.fake:1/0")
    assert runner.scheduler is not None


def test_runner_accepts_injected_scheduler() -> None:
    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    assert runner.scheduler is fake


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------


def test_register_adds_job_to_scheduler() -> None:
    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    runner.register("my_job", lambda: 1, trigger=None)
    assert len(fake.jobs) == 1
    assert fake.jobs[0]["id"] == "my_job"


def test_default_jobs_registers_expected_count(tmp_path) -> None:
    """add_default_jobs should register one job per asset agent
    plus the operational jobs (data_refresh, position_reconcile, eod_summary,
    nightly_backtest, weekly_walkforward, monthly_retrain, coherence_check,
    heartbeat) - 13 total when all agents present."""
    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    agents = {
        "equity": _make_agent_stub("equity"),
        "gold": _make_agent_stub("gold"),
        "bonds": _make_agent_stub("bonds"),
        "crypto": _make_agent_stub("crypto"),
        "governance": _make_agent_stub("governance"),
    }
    journal = JournalWriter(tmp_path)
    runner.add_default_jobs(agents=agents, journal_writer=journal)

    names = {j["id"] for j in fake.jobs}
    expected = {
        "equity_agent.eval",
        "gold_agent.eval",
        "bonds_agent.eval",
        "crypto_agent.eval",
        "governance_agent.eval",
        "data_refresh",
        "position_reconcile",
        "eod_summary",
        "nightly_backtest",
        "weekly_walkforward",
        "monthly_retrain",
        "coherence_check",
        "heartbeat",
    }
    assert expected.issubset(names)
    assert len(fake.jobs) == len(expected)


def test_default_jobs_skips_missing_agents(tmp_path) -> None:
    """If an agent is missing the corresponding eval job is skipped."""
    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    journal = JournalWriter(tmp_path)
    runner.add_default_jobs(agents={}, journal_writer=journal)

    names = {j["id"] for j in fake.jobs}
    assert "equity_agent.eval" not in names
    assert "crypto_agent.eval" not in names
    # Operational/journal jobs should still be there.
    assert "heartbeat" in names
    assert "position_reconcile" in names


# ---------------------------------------------------------------------------
# Market-hours gating
# ---------------------------------------------------------------------------


def test_equity_eval_early_returns_outside_market_hours(tmp_path, monkeypatch) -> None:
    """Equity-class jobs must early-return when is_open is False."""
    monkeypatch.setattr(_sched_mod, "is_open", lambda asset: False)

    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    equity = _make_agent_stub("equity")
    journal = JournalWriter(tmp_path)
    runner.add_default_jobs(agents={"equity": equity}, journal_writer=journal)

    equity_job = next(j for j in fake.jobs if j["id"] == "equity_agent.eval")
    result = equity_job["fn"]()
    assert result is None
    equity.evaluate.assert_not_called()


def test_equity_eval_runs_during_market_hours(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_sched_mod, "is_open", lambda asset: True)

    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    equity = _make_agent_stub("equity")
    journal = JournalWriter(tmp_path)
    runner.add_default_jobs(agents={"equity": equity}, journal_writer=journal)

    equity_job = next(j for j in fake.jobs if j["id"] == "equity_agent.eval")
    equity_job["fn"]()
    equity.evaluate.assert_called_once()


def test_crypto_eval_runs_24_7(tmp_path, monkeypatch) -> None:
    """The crypto job must NOT be wrapped with a market-hours gate."""
    monkeypatch.setattr(_sched_mod, "is_open", lambda asset: False)

    fake = _FakeScheduler()
    runner = Runner(scheduler=fake)
    crypto = _make_agent_stub("crypto")
    journal = JournalWriter(tmp_path)
    runner.add_default_jobs(agents={"crypto": crypto}, journal_writer=journal)

    crypto_job = next(j for j in fake.jobs if j["id"] == "crypto_agent.eval")
    crypto_job["fn"]()
    crypto.evaluate.assert_called_once()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_gate_market_hours_swallows_when_closed(monkeypatch) -> None:
    monkeypatch.setattr(_sched_mod, "is_open", lambda asset: False)
    called: list[int] = []
    gated = _gate_market_hours("equity", lambda: called.append(1))
    assert gated() is None
    assert called == []


def test_safe_call_logs_and_returns_none_on_exception(caplog) -> None:
    def boom() -> None:
        raise RuntimeError("nope")

    wrapped = _safe_call("boom", boom)
    assert wrapped() is None
    assert any("scheduled job boom failed" in r.message for r in caplog.records)


def test_first_sunday_only_runs_on_first_seven_days(monkeypatch) -> None:
    class _FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 6, 1, 3, 0, tzinfo=ET)

    monkeypatch.setattr(_sched_mod, "datetime", _FakeDateTime)
    called: list[int] = []
    wrapped = _first_sunday_only(lambda: called.append(1))
    wrapped()
    assert called == [1]


def test_first_sunday_only_skips_after_first_week(monkeypatch) -> None:
    class _FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 6, 8, 3, 0, tzinfo=ET)

    monkeypatch.setattr(_sched_mod, "datetime", _FakeDateTime)
    called: list[int] = []
    wrapped = _first_sunday_only(lambda: called.append(1))
    assert wrapped() is None
    assert called == []
