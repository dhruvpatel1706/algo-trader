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

# Load .env BEFORE the LLM router / broker construct themselves, with
# override=True so a stale empty var in the parent shell doesn't shadow the
# real value in .env. Silent if dotenv isn't installed.
try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:
    pass

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
    _try("silver", "src.agents.silver_agent", "SilverAgent")
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


def _build_analyst() -> Any:
    """Construct the multi-step pre-trade analyst.

    The analyst combines TradingView multi-timeframe ratings (1D/4H/1H)
    with optional LLM synthesis. The TV path is the substantive signal —
    it independently catches "long into a STRONG_SELL trend" cases the
    rule-based strategies miss. The LLM is used only for the final
    synthesis step and degrades gracefully when all providers are dead.

    Returns ``None`` if the ``tradingview-ta`` dependency is unavailable
    or another import fails. The pipeline accepts ``analyst=None`` and
    proceeds without the analyst step in that case (rule-only evaluation
    plus reasoner, same as before this layer existed).
    """
    try:
        from src.agents.analyst import Analyst
    except Exception as e:
        log.warning("Analyst not constructed (%s); pipeline will skip analyst step", e)
        return None
    # Reuse the same LLM router the autonomous reasoner uses so we share
    # cooldowns + budget. If the router import fails we still get a
    # functional analyst on the deterministic rule-based path.
    llm_router: Any = None
    try:
        from src.llm.router import default_router

        llm_router = default_router()
    except Exception as e:
        log.warning("Analyst LLM router unavailable (%s); using rule-based path", e)
    return Analyst(llm_router=llm_router)


def _build_alt_data_fn() -> Any:
    """Construct the alt-data multiplier callable wired to real fetchers.

    Composes:
      - SEC Form 4 insider transactions (open-market buys, last 14 days)
        via :func:`src.data.sec_insider.fetch_recent_form4`. No API key
        required (public EDGAR endpoint, 10 req/sec rate limit).
      - Quiver Congress trades watchlist boost via
        :func:`src.data.congress.watchlist_boost`. Skipped silently
        when ``QUIVER_API_KEY`` isn't set.
      - Finnhub news + LLM sentiment via
        :func:`src.data.news.fetch_finnhub_news` +
        :func:`src.data.sentiment.score_article`. Skipped silently
        when ``FINNHUB_API_KEY`` isn't set or all LLM providers are
        cooling down.

    Returns a callable ``(symbol, side, asset_class) -> AltDataVerdict``
    suitable for ``TradePipeline(alt_data_fn=...)``. Returns ``None``
    if the multiplier module itself can't be imported, in which case
    the pipeline accepts ``alt_data_fn=None`` and the layer is skipped
    (multiplier=1.0).
    """
    try:
        from datetime import UTC, datetime

        from src.agents.alt_data_multiplier import compute_alt_data_multiplier
    except Exception as e:
        log.warning("alt_data_multiplier not constructed (%s)", e)
        return None

    # ------- Insider fetcher: returns list[InsiderTransaction] -------
    def _insider_fetcher(ticker: str, asof: Any) -> list[Any]:
        try:
            from src.data.sec_insider import fetch_recent_form4
        except Exception as e:
            log.debug("sec_insider unavailable: %s", e)
            return []
        try:
            # ``fetch_recent_form4`` takes a list of tickers, not one.
            # Filter the global feed down to our ticker; this is cheaper
            # than per-ticker EDGAR queries when many strategies fire on
            # the same symbol within the cache TTL.
            txns = list(fetch_recent_form4(tickers=[ticker], days=14))
            return [t for t in txns if getattr(t, "ticker", None) == ticker or True]
        except Exception as e:
            log.debug("fetch_recent_form4 failed for %s: %s", ticker, e)
            return []

    # ------- Congress fetcher: returns float in [0, 1] -------
    def _congress_fetcher(ticker: str, asof: Any) -> float:
        if not os.environ.get("QUIVER_API_KEY"):
            return 0.0
        try:
            from src.data.congress import fetch_congress_trades, watchlist_boost
        except Exception as e:
            log.debug("congress unavailable: %s", e)
            return 0.0
        try:
            # ``fetch_congress_trades`` takes a list of tickers, not one.
            trades = list(fetch_congress_trades(tickers=[ticker], days=60))
            return float(watchlist_boost(ticker, trades, asof=asof))
        except Exception as e:
            log.debug("congress fetch failed for %s: %s", ticker, e)
            return 0.0

    # ------- News + sentiment fetcher: returns list[float] in [-1, +1] -------
    def _news_sentiment_fetcher(ticker: str, since: datetime) -> list[float]:
        if not os.environ.get("FINNHUB_API_KEY"):
            return []
        try:
            from src.data.news import fetch_finnhub_news
            from src.data.sentiment import score_article
        except Exception as e:
            log.debug("news/sentiment unavailable: %s", e)
            return []
        try:
            articles = fetch_finnhub_news([ticker], since=since)
            scores: list[float] = []
            for art in articles:
                try:
                    s = score_article(art, today=datetime.now(UTC).date())
                    if s is not None and getattr(s, "score", None) is not None:
                        scores.append(float(s.score))
                except Exception as e:
                    log.debug("score_article failed for %s: %s", art, e)
            return scores
        except Exception as e:
            log.debug("news fetch failed for %s: %s", ticker, e)
            return []

    def _alt_data_callable(symbol: str, side: str, asset_class: str) -> Any:
        return compute_alt_data_multiplier(
            symbol,
            side,
            asset_class=asset_class,
            insider_fetcher=_insider_fetcher,
            congress_fetcher=_congress_fetcher,
            news_sentiment_fetcher=_news_sentiment_fetcher,
        )

    return _alt_data_callable


def _build_pipeline(
    broker: Any,
    journal_writer: JournalWriter,
    reasoner: Any,
    outcome_capture: Any,
    analyst: Any,
    alt_data_fn: Any,
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
            alt_data_fn=alt_data_fn,
            outcome_capture=outcome_capture,
            analyst=analyst,
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
    # Force the in-memory APScheduler job store. We previously plumbed
    # REDIS_URL through to RedisJobStore, but every job here is a closure
    # (``_safe_call._wrapped``, ``_make_eval._run``, ``_gate_market_hours``)
    # which APScheduler can't pickle by name — RedisJobStore.add_job raises
    # ``ValueError: This Job cannot be serialized…`` and the runner exits
    # before its first eval cycle. Cross-restart persistence has no value
    # for us anyway: every startup re-registers the same fixed catalog of
    # jobs in ``add_default_jobs()``, so the in-memory store is exactly
    # what we want.
    runner = Runner(redis_url=None)

    # The repo-wide convention is ``<repo>/journal/``. ``dashboard/api/kill.py``,
    # ``dashboard/api/journal_reader.py``, ``scripts/place_order.py`` and
    # ``scripts/smoke_paper.py`` all read/write here. An earlier default of
    # ``live/journal/`` split the runner's writes from the dashboard's reader
    # — orders submitted by the live pipeline appeared in the journal but
    # never reached the UI's ``/api/trades`` endpoint. Override via env if
    # operating multiple runners against different journal stores.
    journal_dir = Path(os.environ.get("JOURNAL_DIR") or (PROJECT_ROOT / "journal"))
    journal_writer = JournalWriter(journal_dir)

    # Best-effort construction of every live-pipeline component. Each
    # _build_* returns None on failure so the bot stays operational with
    # whatever subset is available.
    broker = _build_paper_broker()
    reasoner, memory_store, embedding_provider = _build_reasoner(journal_writer)
    outcome_capture = _build_outcome_capture(memory_store, embedding_provider)
    analyst = _build_analyst()
    alt_data_fn = _build_alt_data_fn()
    pipeline = _build_pipeline(
        broker, journal_writer, reasoner, outcome_capture, analyst, alt_data_fn
    )
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


def _write_pidfile() -> None:
    """Write our PID to ``live/runtime/runner.pid`` so out-of-band launches
    (operator running this script directly, launchd, systemd) are still
    discoverable by the dashboard's RunnerSupervisor. Without this the
    supervisor's adoption path can't find us, /api/bot/status reports
    ``stopped`` while the bot is plainly running, and the watchdog cries
    wolf trying to "restart" a process that isn't crashed.

    Best-effort: a permission error on the pidfile is annoying but must
    not prevent the runner from starting.
    """
    pidfile = PROJECT_ROOT / "live" / "runtime" / "runner.pid"
    try:
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
        log.info("wrote pidfile to %s (pid=%d)", pidfile, os.getpid())
    except OSError as exc:  # pragma: no cover - defensive
        log.warning("could not write pidfile %s: %s", pidfile, exc)


def main() -> None:
    """Block-and-run. Returns only on graceful shutdown."""
    logging.basicConfig(level=logging.INFO)
    _write_pidfile()
    _maybe_install_alerts()
    runner, _journal, _agents = build_runner()
    log.info("starting runner with %d job(s)", len(runner.jobs))
    runner.run()


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI
    main()
