"""Entry point for the 24/7 runner.

Boots the scheduler, wires the default job calendar, and blocks on
:meth:`Runner.run`. Designed so that *every* external dependency
(Discord, Redis, agent imports) is optional - if it's unavailable we log a
warning and keep going. The runner itself is the only thing that must work.

Usage
-----

    uv run python scripts/run_bot.py

This is what launchd / systemd should exec. Production deployments should
also run :mod:`scripts.watchdog` as a sibling supervisor.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT
from src.journal.writer import JournalWriter
from src.runtime.scheduler import Runner

log = logging.getLogger("algo_trader.run_bot")


def _maybe_install_alerts() -> None:
    """Best-effort: install the Discord alert handler if the module exists."""
    try:
        from src.observability.discord_alert import install_discord_alerts

        install_discord_alerts()
    except Exception as e:
        log.warning("Discord alerts not installed (%s)", e)


def _load_agents() -> dict[str, Any]:
    """Best-effort: load whatever agents are importable.

    Each agent class is constructed with empty strategy / universe lists so
    the runner can wire job triggers even before the strategy registry is
    populated. The job wrappers only call ``agent.evaluate`` - they don't
    require any particular signal output.
    """
    agents: dict[str, Any] = {}

    def _try(name: str, path: str, cls_name: str) -> None:
        try:
            mod = __import__(path, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            agents[name] = cls(strategies=[], universe=[], heat_allocation=0.0)
        except Exception as e:
            log.warning("agent %s not loaded: %s", name, e)

    _try("equity", "src.agents.equity_agent", "EquityAgent")
    _try("gold", "src.agents.gold_agent", "GoldAgent")
    _try("bonds", "src.agents.bonds_agent", "BondsAgent")
    _try("crypto", "src.agents.crypto_agent", "CryptoAgent")
    _try("governance", "src.agents.governance_agent", "GovernanceAgent")
    return agents


def build_runner() -> tuple[Runner, JournalWriter, dict[str, Any]]:
    """Construct (but don't start) the runner. Exposed for tests/integration."""
    redis_url = os.environ.get("REDIS_URL")
    runner = Runner(redis_url=redis_url)

    journal_dir = Path(os.environ.get("JOURNAL_DIR") or (PROJECT_ROOT / "journal"))
    journal_writer = JournalWriter(journal_dir)

    agents = _load_agents()
    runner.add_default_jobs(agents=agents, journal_writer=journal_writer)
    return runner, journal_writer, agents


def main() -> None:
    """Block-and-run. Returns only on graceful shutdown."""
    logging.basicConfig(level=logging.INFO)
    _maybe_install_alerts()
    runner, _journal, _agents = build_runner()
    log.info("starting runner with %d job(s)", len(runner.jobs))
    runner.run()


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI
    main()
