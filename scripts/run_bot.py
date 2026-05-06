"""Entry point for the 24/7 runner.

Boots the scheduler, constructs the live trading pipeline (broker +
bars cache + reasoner + memory + outcome capture), wires the default
job calendar, and blocks on :meth:`Runner.run`.

Designed so every external dependency is best-effort:
  - Alpaca paper credentials missing → broker is None, agent evals
    fall back to the legacy stub path, bot keeps running for free
    (no real trades — the surface is just observability).
  - Redis unavailable → APScheduler falls back to in-memory store.
  - Discord webhook unset → alerts are no-op.
  - LLM keys missing → reasoner constructs but every evaluate() goes
    fail-open (identity multiplier).

The runner itself is the only thing that must work.

Usage
-----

    uv run python scripts/run_bot.py

This is what launchd / systemd should exec. Production deployments
should also run :mod:`scripts.watchdog` as a sibling supervisor.
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
    """Construct each agent with its DEFAULTS (real strategies + universe).

    Earlier rounds passed ``strategies=[], universe=[]`` here, which
    reduced agents to no-op shells. With Round 8 wiring the real
    pipeline, we want the agents constructed exactly as their __init__
    defaults specify — that's where the tested strategy lists live.

    Each agent class is loaded behind a try/except so a single
    construction failure (e.g. a missing universe entry) doesn't take
    out the others. Agents that fail to load are skipped silently with
    a warning; the runner registers jobs only for what's available.
    """
    agents: dict[str, Any] = {}

    def _try(name: str, path: str, cls_name: str) -> None:
        try:
            mod = __import__(path, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            agents[name] = cls()  # default strategies + default universe
        except Exception as e:
            log.warning("agent %s not loaded: %s", name, e)

    _try("equity", "src.agents.equity_agent", "EquityAgent")
    _try("gold", "src.agents.gold_agent", "GoldAgent")
    _try("bonds", "src.agents.bonds_agent", "BondsAgent")
    _try("crypto", "src.agents.crypto_agent", "CryptoAgent")
    _try("governance", "src.agents.governance_agent", "GovernanceAgent")
    return agents


def _build_paper_broker() -> Any:
    """Construct the Alpaca paper broker. Returns None on any failure.

    The runner stays operational without a broker — agent evals fall
    back to the legacy stub. This is the right behavior during early
    bring-up when credentials may be intentionally absent.
    """
    try:
        from src.execution.broker import PaperBroker

        return PaperBroker()
    except Exception as e:
        log.warning("PaperBroker not constructed (%s); pipeline will be a no-op", e)
        return None


def _build_reasoner(journal_writer: JournalWriter) -> Any:
    """Construct the autonomous reasoner with episodic memory wired in.

    Memory uses ``DeterministicHashProvider`` by default so it works
    without paid embedding credits (recall quality is reduced vs OpenAI
    embeddings but the loop is still functional, and OutcomeCapture
    populates the store on every closed trade). Operators with an
    OpenAI API key can switch by setting MEMORY_EMBEDDING_PROVIDER=openai.
    """
    try:
        from src.agents.autonomous_reasoner import AutonomousReasoner
        from src.memory.embeddings import get_default_provider
        from src.memory.store import MemoryStore

        memory_store = MemoryStore()  # defaults to live/memory.db
        embedding_provider = get_default_provider()
        return AutonomousReasoner(
            journal_writer=journal_writer,
            memory_store=memory_store,
            embedding_provider=embedding_provider,
        ), memory_store, embedding_provider
    except Exception as e:
        log.warning("AutonomousReasoner not constructed (%s); pipeline will run rule-only", e)
        return None, None, None


def _build_outcome_capture(memory_store: Any, embedding_provider: Any) -> Any:
    """Construct the outcome-capture pipeline; None if memory is unavailable."""
    if memory_store is None or embedding_provider is None:
        return None
    try:
        from src.memory.outcome_capture import OutcomeCapture

        return OutcomeCapture(memory_store, embedding_provider)
    except Exception as e:
        log.warning("OutcomeCapture not constructed (%s)", e)
        return None


def _build_pipeline(
    broker: Any,
    journal_writer: JournalWriter,
    reasoner: Any,
    outcome_capture: Any,
) -> Any:
    """Construct TradePipeline + BrokerSnapshotProvider; None on broker outage."""
    if broker is None:
        return None
    try:
        from dashboard.api.broker_proxy import get_broker_proxy
        from src.runtime.trade_pipeline import (
            BrokerSnapshotProvider,
            TradePipeline,
        )

        # The snapshot provider needs get_account/get_positions which
        # PaperBroker doesn't expose; the dashboard's BrokerProxy does.
        snapshot_provider = BrokerSnapshotProvider(get_broker_proxy())
        return TradePipeline(
            broker=broker,
            journal_writer=journal_writer,
            snapshot_provider=snapshot_provider,
            reasoner=reasoner,
            outcome_capture=outcome_capture,
        )
    except Exception as e:
        log.warning("TradePipeline not constructed (%s); falling back to stub evals", e)
        return None


def _build_bars_cache() -> Any:
    """Construct BarsCache with default loaders (load_daily_bars + load_crypto_bars)."""
    try:
        from src.runtime.bars_cache import BarsCache

        return BarsCache()
    except Exception as e:
        log.warning("BarsCache not constructed (%s); pipeline will be a no-op", e)
        return None


def _prime_cache(bars_cache: Any, agents: dict[str, Any]) -> None:
    """Refresh each agent's bars once before the scheduler starts.

    APScheduler's IntervalTrigger fires the first time AFTER the
    interval, not immediately. Without priming, the first equity_agent
    eval at T+5min would see an empty cache and skip every signal.
    """
    if bars_cache is None:
        return
    for name, agent in agents.items():
        if name == "governance":
            continue
        try:
            stats = bars_cache.refresh(agent.asset_class, agent.universe)
            log.info(
                "primed bars for %s (%s symbols, %s rows/sym)",
                name,
                stats.n_symbols,
                stats.rows_per_symbol_max,
            )
        except Exception as e:
            log.warning("prime_cache for %s failed: %s", name, e)


def build_runner() -> tuple[Runner, JournalWriter, dict[str, Any]]:
    """Construct (but don't start) the runner. Exposed for tests/integration."""
    redis_url = os.environ.get("REDIS_URL")
    runner = Runner(redis_url=redis_url)

    journal_dir = Path(os.environ.get("JOURNAL_DIR") or (PROJECT_ROOT / "live" / "journal"))
    journal_writer = JournalWriter(journal_dir)

    # Best-effort construction of every live-pipeline component. Each
    # _build_* returns None on failure so the bot stays operational with
    # whatever subset is available.
    broker = _build_paper_broker()
    reasoner, memory_store, embedding_provider = _build_reasoner(journal_writer)
    outcome_capture = _build_outcome_capture(memory_store, embedding_provider)
    pipeline = _build_pipeline(broker, journal_writer, reasoner, outcome_capture)
    bars_cache = _build_bars_cache()

    agents = _load_agents()

    # Prime the cache so the very first eval cycle has data.
    _prime_cache(bars_cache, agents)

    runner.add_default_jobs(
        agents=agents,
        journal_writer=journal_writer,
        bars_cache=bars_cache,
        trade_pipeline=pipeline,
    )

    if pipeline is None or bars_cache is None:
        log.warning(
            "running in STUB MODE — agent evals will be no-ops "
            "(broker=%s, pipeline=%s, bars_cache=%s)",
            broker is not None,
            pipeline is not None,
            bars_cache is not None,
        )
    else:
        log.info(
            "live pipeline armed: %d agents, broker=PaperBroker, reasoner=%s, memory=%s",
            len(agents),
            "on" if reasoner is not None else "off",
            "on" if memory_store is not None else "off",
        )

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
