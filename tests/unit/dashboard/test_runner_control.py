"""Tests for `dashboard.api.runner_control`.

We don't actually exec `scripts/run_bot.py` — it boots APScheduler and tries
to import the agents. Instead, monkeypatch the constants so the supervisor
spawns a tiny script that we control.
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest


@pytest.fixture
def runtime_paths(tmp_path: Path, monkeypatch):
    """Redirect supervisor to a tmp runtime dir and a stub run_bot script."""
    from dashboard.api import runner_control as rc

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(rc, "_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(rc, "_PIDFILE", runtime_dir / "runner.pid")
    monkeypatch.setattr(rc, "_LOGFILE", runtime_dir / "runner.log")
    # Reset the module-level singleton so each test gets a fresh supervisor.
    monkeypatch.setattr(rc, "_supervisor", None)
    return rc, runtime_dir


def _fake_runner_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake_run_bot.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_status_when_not_started_is_stopped(runtime_paths):
    rc, _ = runtime_paths
    sup = rc.RunnerSupervisor()
    s = sup.status()
    assert s.state == "stopped"
    assert s.pid is None
    assert s.uptime_sec is None


def test_start_then_stop_writes_pidfile_and_clears_it(runtime_paths, tmp_path, monkeypatch):
    rc, runtime_dir = runtime_paths
    # A fake runner that prints a banner and waits for SIGTERM.
    script = _fake_runner_script(
        tmp_path,
        """
        import signal, sys, time
        print('runner up', flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while True:
            time.sleep(0.1)
        """,
    )
    monkeypatch.setattr(rc, "_RUN_BOT", script)

    sup = rc.RunnerSupervisor()
    started = sup.start()
    assert started.state == "running"
    assert started.pid is not None
    pidfile = runtime_dir / "runner.pid"
    assert pidfile.exists()
    assert pidfile.read_text(encoding="utf-8").strip() == str(started.pid)

    # Wait for the banner to flush so log_tail isn't empty.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if "runner up" in (runtime_dir / "runner.log").read_text(encoding="utf-8"):
            break
        time.sleep(0.05)

    status = sup.status()
    assert status.state == "running"
    assert any("runner up" in line for line in status.log_tail)

    stopped = sup.stop()
    assert stopped.state in ("stopped", "crashed")  # exit code 0 → stopped
    assert stopped.exit_code == 0
    assert not pidfile.exists()


def test_start_is_idempotent_when_already_running(runtime_paths, tmp_path, monkeypatch):
    rc, _ = runtime_paths
    script = _fake_runner_script(
        tmp_path,
        """
        import signal, sys, time
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while True:
            time.sleep(0.1)
        """,
    )
    monkeypatch.setattr(rc, "_RUN_BOT", script)
    sup = rc.RunnerSupervisor()
    a = sup.start()
    b = sup.start()
    try:
        assert a.pid == b.pid  # same process, no second spawn
    finally:
        sup.stop()


def test_stop_when_not_running_is_safe(runtime_paths):
    rc, _ = runtime_paths
    sup = rc.RunnerSupervisor()
    s = sup.stop()
    assert s.state == "stopped"


def test_crashed_runner_reports_crashed_state(runtime_paths, tmp_path, monkeypatch):
    rc, _ = runtime_paths
    # A runner that immediately exits non-zero — simulates a startup error.
    script = _fake_runner_script(
        tmp_path,
        """
        import sys
        print('boom', flush=True)
        sys.exit(7)
        """,
    )
    monkeypatch.setattr(rc, "_RUN_BOT", script)
    sup = rc.RunnerSupervisor()
    sup.start()
    # Give the process a moment to die.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        s = sup.status()
        if s.state in ("crashed", "stopped"):
            break
        time.sleep(0.05)
    s = sup.status()
    assert s.state == "crashed"
    assert s.exit_code == 7
    assert any("boom" in line for line in s.log_tail)


def test_get_supervisor_returns_singleton(runtime_paths):
    rc, _ = runtime_paths
    a = rc.get_supervisor()
    b = rc.get_supervisor()
    assert a is b


def test_adopt_orphan_when_pidfile_points_at_running_runner(
    runtime_paths, tmp_path, monkeypatch
):
    """Recovery: a backend restart while the runner is up must NOT spawn
    a second runner. Construct a 'pretend orphan' subprocess whose cmdline
    contains run_bot.py, write its PID to the pidfile, then construct a
    fresh supervisor and verify it adopts."""
    rc, runtime_dir = runtime_paths
    # Write a fake script named run_bot.py so the cmdline check matches.
    run_bot_script = tmp_path / "run_bot.py"
    run_bot_script.write_text(
        textwrap.dedent(
            """
            import signal, sys, time
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            while True:
                time.sleep(0.1)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rc, "_RUN_BOT", run_bot_script)

    sup = rc.RunnerSupervisor()
    started = sup.start()
    pid = started.pid
    assert pid is not None

    # Drop the supervisor instance — simulate a backend reload. The runner
    # process is still alive, the pidfile is still on disk.
    pidfile = runtime_dir / "runner.pid"
    assert pidfile.read_text(encoding="utf-8").strip() == str(pid)

    try:
        # New supervisor, fresh in-process state. It should adopt the orphan.
        recovered = rc.RunnerSupervisor()
        s = recovered.status()
        assert s.state == "running"
        assert s.pid == pid
        assert s.adopted is True

        # Calling start() on the recovered supervisor MUST NOT spawn a second
        # process — the orphan adoption already counts as alive.
        before_pid = s.pid
        again = recovered.start()
        assert again.pid == before_pid

        # Stop should still work via the adopted handle.
        stopped = recovered.stop()
        assert stopped.state in ("stopped", "crashed")
    finally:
        # Best-effort cleanup if the orphan is still alive.
        try:
            sup.stop()
        except Exception:  # noqa: S110 — cleanup, not silencing logic
            pass


def test_adopt_orphan_rejects_unrelated_pid(runtime_paths, monkeypatch):
    """If the pidfile points at a PID whose cmdline doesn't mention
    run_bot.py, refuse to adopt and clear the stale pidfile."""
    rc, runtime_dir = runtime_paths
    # PID 1 (init) is alive but its cmdline is not run_bot.py.
    (runtime_dir / "runner.pid").write_text("1", encoding="utf-8")

    sup = rc.RunnerSupervisor()
    s = sup.status()
    assert s.state == "stopped"
    assert s.adopted is False
    # Stale pidfile must be cleaned up.
    assert not (runtime_dir / "runner.pid").exists()


def test_adopt_orphan_handles_missing_pidfile(runtime_paths):
    rc, _ = runtime_paths
    # Pidfile doesn't exist — adoption is a no-op, status reports stopped.
    sup = rc.RunnerSupervisor()
    s = sup.status()
    assert s.state == "stopped"
    assert s.adopted is False
