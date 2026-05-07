"""Process supervisor for the trading runner.

Owns the lifecycle of `scripts/run_bot.py` started from the dashboard:

* `start()` spawns the runner as a subprocess and stores its PID.
* `stop()` sends SIGTERM, waits up to a grace period, then SIGKILL.
* `status()` reports `running | stopped | crashed`, plus pid, uptime, last log
  line, and (when crashed) the exit code.

Recovery across FastAPI restarts: on construction, the supervisor reads the
pidfile and checks whether that PID is still alive AND was launched from
`scripts/run_bot.py`. If so it adopts the orphan rather than spawning a
second runner — protecting against the failure mode where a backend reload
silently produces two parallel scheduler processes both placing orders.

The runner pidfile + log file live under `<repo>/live/runtime/`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from src.config import PROJECT_ROOT

_RUNTIME_DIR = PROJECT_ROOT / "live" / "runtime"
_PIDFILE = _RUNTIME_DIR / "runner.pid"
_LOGFILE = _RUNTIME_DIR / "runner.log"
_RUN_BOT = PROJECT_ROOT / "scripts" / "run_bot.py"
_TAIL_LINES = 40
_STOP_GRACE_SEC = 8.0


RunnerState = Literal["running", "stopped", "crashed"]


@dataclass(frozen=True)
class RunnerStatus:
    state: RunnerState
    pid: int | None = None
    started_at: str | None = None
    uptime_sec: float | None = None
    exit_code: int | None = None
    log_tail: list[str] = field(default_factory=list)
    adopted: bool = False  # true when supervisor inherited an orphan via pidfile


class _AdoptedProc:
    """Adapter so adopted orphan PIDs share `_proc`'s narrow surface.

    We only ever call `.poll()`, `.pid`, `.wait()`, and `.returncode`; mimic
    those four. After we adopt, we can SIGTERM by PID (we never had a
    subprocess.Popen handle for the orphan, so we can't `terminate()` it).
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        try:
            os.kill(self.pid, 0)
        except (ProcessLookupError, PermissionError):
            self.returncode = -1  # sentinel: process gone, exit code unknown
            return self.returncode
        return None

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (timeout or 0)
        while True:
            if self.poll() is not None:
                return self.returncode or 0
            if timeout is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("adopted-pid", timeout)
            time.sleep(0.1)


class RunnerSupervisor:
    """Single-instance subprocess manager for the trading runner."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._proc: subprocess.Popen | _AdoptedProc | None = None
        self._started_at: datetime | None = None
        self._exit_code: int | None = None
        self._adopted: bool = False
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self._adopt_orphan_if_present()

    def _adopt_orphan_if_present(self) -> None:
        """If a pidfile exists and that PID is still our runner, adopt it.

        Runs once at construction. Verifies the PID's cmdline mentions
        `scripts/run_bot.py`, so a stale pidfile pointing at an unrelated
        reused PID can't be mistaken for the runner.
        """
        if not _PIDFILE.exists():
            return
        try:
            pid = int(_PIDFILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            self._clear_pidfile()
            return
        if pid <= 0:
            self._clear_pidfile()
            return
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            self._clear_pidfile()
            return
        if not _pid_matches_runner(pid):
            self._clear_pidfile()
            return
        # Skip if we're already tracking this exact PID — re-adoption from
        # the periodic status() path must be a no-op when nothing changed.
        if (
            self._proc is not None
            and getattr(self._proc, "pid", None) == pid
            and self._is_alive()
        ):
            return
        # Adopt: we don't know the exact start time, so use the log file's
        # mtime as a conservative lower bound. Clear the prior exit code so
        # a stale "crashed" verdict from an earlier dead process doesn't
        # bleed into the freshly-adopted runner's status.
        try:
            mtime = _LOGFILE.stat().st_mtime
            self._started_at = datetime.fromtimestamp(mtime, tz=UTC)
        except OSError:
            self._started_at = datetime.now(UTC)
        self._proc = _AdoptedProc(pid)
        self._exit_code = None
        self._adopted = True

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> RunnerStatus:
        """Start the runner if it isn't already up. Idempotent."""
        with self._lock:
            if self._is_alive():
                return self._status_locked()

            # Truncate log so the first poll after Start shows a clean buffer.
            _LOGFILE.write_text("", encoding="utf-8")
            log_handle = _LOGFILE.open("a", encoding="utf-8", buffering=1)

            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            # The runner is invoked as a script (not `python -m`), so the
            # project root is NOT automatically on sys.path. Without an
            # editable install, `from src.config import ...` ModuleNotFounds
            # immediately. Prepending PROJECT_ROOT to PYTHONPATH makes the
            # spawn robust regardless of whether the venv has the project
            # installed editable.
            existing_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{PROJECT_ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(PROJECT_ROOT)
            )
            cmd = [sys.executable, str(_RUN_BOT)]
            self._proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=env,
                close_fds=True,
                start_new_session=True,
            )
            self._started_at = datetime.now(UTC)
            self._exit_code = None
            self._adopted = False
            _PIDFILE.write_text(str(self._proc.pid), encoding="utf-8")
            return self._status_locked()

    def stop(self) -> RunnerStatus:
        """SIGTERM the runner; escalate to SIGKILL after grace period.

        Releases the supervisor lock during the grace-period wait so that a
        concurrent ``GET /api/bot/status`` request from the UI doesn't block
        for up to 11 seconds.
        """
        with self._lock:
            if not self._is_alive():
                self._clear_pidfile()
                return self._status_locked()
            proc = self._proc
            assert proc is not None
            try:
                _signal_group(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        # Wait WITHOUT the supervisor lock so concurrent status() calls work.
        # `proc` is the local copy; ownership transfer guarded by `_proc is None`.
        deadline = time.monotonic() + _STOP_GRACE_SEC
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)

        with self._lock:
            if proc.poll() is None:
                try:
                    _signal_group(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass
            self._exit_code = proc.returncode
            self._proc = None
            self._adopted = False
            self._clear_pidfile()
            return self._status_locked()

    # -- status -------------------------------------------------------------

    def status(self) -> RunnerStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> RunnerStatus:
        # Re-adopt before reporting. ``_adopt_orphan_if_present`` is only
        # called once in __init__, so when an out-of-band restart happens
        # (e.g. operator runs ``scripts/run_bot.py`` from a shell, or the
        # adopted process exits and a fresh one is spawned by something
        # else), our cached ``_proc`` becomes stale. The watchdog then
        # asks /api/bot/status, sees ``state=crashed`` from our stale
        # _AdoptedProc.returncode==-1 sentinel, and tries to restart a
        # bot that is in fact running. Reconciling on every status read
        # is cheap (one os.kill(pid, 0) when a pidfile exists) and keeps
        # the supervisor honest about reality.
        if not self._is_alive():
            self._adopt_orphan_if_present()

        if self._is_alive():
            assert self._proc is not None
            assert self._started_at is not None
            uptime = (datetime.now(UTC) - self._started_at).total_seconds()
            return RunnerStatus(
                state="running",
                pid=self._proc.pid,
                started_at=self._started_at.isoformat(),
                uptime_sec=uptime,
                log_tail=_read_tail(_LOGFILE, _TAIL_LINES),
                adopted=self._adopted,
            )

        # Either we never started, OR the process exited. If it exited, surface
        # the exit code (whether we captured it via stop() or via poll()).
        if self._proc is not None:
            code = self._proc.poll()
            if code is not None and self._exit_code is None:
                self._exit_code = code

        if self._exit_code is not None:
            state: RunnerState = (
                "stopped" if self._exit_code in (0, -signal.SIGTERM) else "crashed"
            )
            return RunnerStatus(
                state=state,
                pid=None,
                started_at=self._started_at.isoformat() if self._started_at else None,
                uptime_sec=None,
                exit_code=self._exit_code,
                log_tail=_read_tail(_LOGFILE, _TAIL_LINES),
            )

        return RunnerStatus(state="stopped", log_tail=_read_tail(_LOGFILE, _TAIL_LINES))

    # -- helpers ------------------------------------------------------------

    def _is_alive(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def _clear_pidfile(self) -> None:
        try:
            _PIDFILE.unlink()
        except FileNotFoundError:
            pass


def _read_tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        # Soft cap to keep the dev-server preview snappy. For multi-MB logs we
        # seek to the last 256 KiB rather than reading from byte 0; for small
        # logs we just read the whole file. Either way, we deque to keep the
        # last `n` lines.
        with path.open("rb") as fh:
            if size > _LOG_TAIL_MAX_BYTES:
                fh.seek(-_LOG_TAIL_MAX_BYTES, os.SEEK_END)
                fh.readline()  # discard partial leading line
            data = fh.read().decode("utf-8", errors="replace")
        tail = deque(data.splitlines(), maxlen=n)
        return list(tail)
    except OSError:
        return []


def _pid_matches_runner(pid: int) -> bool:
    """Check via `ps -p <pid> -o command=` that the PID's cmdline mentions
    `scripts/run_bot.py`. Returns False on any error so we fail closed
    rather than adopting an unrelated reused PID.
    """
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    return "run_bot.py" in out.stdout


def _signal_group(pid: int, sig: int) -> None:
    """Send `sig` to the process group of `pid`. Falls back to single-PID
    signal if the process is not a group leader (rare but possible if
    `start_new_session=True` failed)."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
    except ProcessLookupError:
        raise
    except OSError:
        # Last-resort fallback to direct PID signal.
        os.kill(pid, sig)


_LOG_TAIL_MAX_BYTES = 256 * 1024  # read at most 256 KiB to compute tail


_supervisor: RunnerSupervisor | None = None


def get_supervisor() -> RunnerSupervisor:
    """FastAPI dependency. Module-level singleton — one runner per backend."""
    global _supervisor
    if _supervisor is None:
        _supervisor = RunnerSupervisor()
    return _supervisor


def status_to_dict(status: RunnerStatus) -> dict:
    return asdict(status)
