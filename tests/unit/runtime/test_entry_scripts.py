"""Smoke tests for ``scripts/run_bot.py`` and ``scripts/watchdog.py``.

We don't actually start the runner or the watchdog poll loop - we just
verify that the modules import cleanly and that the documented entry
function (``main`` / ``build_runner`` / ``check_once``) is callable.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# scripts/ isn't a package; add it to sys.path so we can import run_bot/watchdog.
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def test_run_bot_imports_and_exposes_main() -> None:
    mod = importlib.import_module("run_bot")
    assert callable(mod.main)
    assert callable(mod.build_runner)


def test_watchdog_imports_and_exposes_main() -> None:
    mod = importlib.import_module("watchdog")
    assert callable(mod.main)
    assert callable(mod.check_once)


def test_run_bot_build_runner_does_not_crash() -> None:
    """The construction path should work even with no Redis and no agents."""
    run_bot = importlib.import_module("run_bot")
    runner, journal_writer, agents = run_bot.build_runner()
    # At minimum the operational jobs should be wired up.
    assert "heartbeat" in runner.jobs
    assert journal_writer is not None
    assert isinstance(agents, dict)


def test_watchdog_check_once_with_no_redis(monkeypatch) -> None:
    """check_once must tolerate a None client (no heartbeat to read)."""
    watchdog = importlib.import_module("watchdog")
    # _redis_client returns None when REDIS_URL unset; we still want check_once
    # itself to handle a degraded/None scenario gracefully via its callers,
    # so we exercise it directly with None and expect "missing".
    monkeypatch.setattr(watchdog, "flatten_all", lambda reason="": False)
    result = watchdog.check_once(None)
    assert result == "missing"
