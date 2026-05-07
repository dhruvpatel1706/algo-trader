"""24/7 runner backed by APScheduler.

Wraps :class:`apscheduler.schedulers.blocking.BlockingScheduler` with a small
amount of glue so that:

- Construction never crashes if Redis is unavailable; we fall back to the
  default in-memory job store with a warning. APScheduler itself is the only
  hard dependency.
- The job calendar from the multi-asset plan is wired up in
  :meth:`Runner.add_default_jobs`. Each job is registered with a wrapper that
  consults :mod:`src.runtime.calendar` so equity-class jobs early-return
  outside of NYSE regular hours.
- Tests can inject a fake scheduler via the constructor; the wrapper makes no
  assumptions about the scheduler beyond ``add_job`` / ``start`` / ``shutdown``.

The runner is intentionally a thin layer; per-job logic lives in the agents
themselves.
"""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import redis as _redis
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.runtime.calendar import is_open

if TYPE_CHECKING:
    from src.agents.base import Agent

ET = ZoneInfo("America/New_York")

log = logging.getLogger(__name__)


def _build_scheduler(redis_url: str | None) -> Any:
    """Construct a :class:`BlockingScheduler` with a Redis job store if possible.

    Falls back to the default in-memory job store if ``redis_url`` is None or
    the Redis connection probe fails. Any failure logs a warning rather than
    raising.
    """
    timezone = ET

    if redis_url:
        try:
            client = _redis.Redis.from_url(redis_url)
            client.ping()  # surface bad URLs early
            jobstores = {
                "default": RedisJobStore(
                    jobs_key="apsched:jobs",
                    run_times_key="apsched:run_times",
                )
            }
            return BlockingScheduler(jobstores=jobstores, timezone=timezone)
        except Exception as e:
            log.warning("Redis job store unavailable (%s); using in-memory store", e)

    return BlockingScheduler(timezone=timezone)


def _gate_market_hours(asset_class: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``fn`` so it early-returns when the asset class isn't tradeable.

    Used for equity-class jobs (equity / gold / bonds). Crypto jobs are 24/7
    and don't need this guard.
    """

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_open(asset_class):  # type: ignore[arg-type]
            return None
        return fn(*args, **kwargs)

    _wrapped.__name__ = getattr(fn, "__name__", "wrapped") + f"__{asset_class}_gated"
    _wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
    return _wrapped


def _safe_call(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``fn`` so any exception is logged but not propagated.

    The scheduler will keep running; without this a single buggy job kills
    the whole loop.
    """

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.exception("scheduled job %s failed", name)
            return None

    _wrapped.__name__ = name
    _wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
    return _wrapped


class Runner:
    """APScheduler-backed 24/7 runner.

    Parameters
    ----------
    redis_url:
        Optional Redis URL. If provided, the scheduler uses a
        :class:`RedisJobStore` so jobs survive process restarts. If ``None``
        or unreachable, falls back to the default in-memory store and logs
        a warning.
    scheduler:
        Optional pre-built scheduler (used by tests). If supplied, the
        ``redis_url`` argument is ignored.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        scheduler: Any | None = None,
    ) -> None:
        self._scheduler = scheduler if scheduler is not None else _build_scheduler(redis_url)
        self._jobs: dict[str, Callable[..., Any]] = {}

    @property
    def scheduler(self) -> Any:
        return self._scheduler

    @property
    def jobs(self) -> dict[str, Callable[..., Any]]:
        """Mapping of registered job name -> wrapped callable."""
        return dict(self._jobs)

    def register(self, name: str, fn: Callable[..., Any], trigger: Any) -> None:
        """Register a job with the underlying scheduler.

        ``fn`` is wrapped in a logging shim so a job exception never tears
        down the runner.
        """
        if self._scheduler is None:
            log.warning("scheduler unavailable; skipping registration of %s", name)
            return

        wrapped = _safe_call(name, fn)
        self._jobs[name] = wrapped
        self._scheduler.add_job(
            wrapped,
            trigger=trigger,
            id=name,
            name=name,
            replace_existing=True,
        )

    def add_default_jobs(
        self,
        agents: dict[str, Agent],
        journal_writer: Any,  # JournalWriter, but kept loose for testability
        *,
        bars_cache: Any = None,        # `BarsCache | None` — when supplied, real data refresh
        trade_pipeline: Any = None,    # `TradePipeline | None` — when supplied, real signal flow
    ) -> None:
        """Wire up the default job calendar from the multi-asset plan.

        Jobs registered (when the matching agent is supplied):

        - ``equity_agent.eval``     - every 5 min, gated to NYSE hours
        - ``gold_agent.eval``       - every 5 min, gated to NYSE hours
        - ``bonds_agent.eval``      - every 5 min, gated to NYSE hours
        - ``crypto_agent.eval``     - every 5 min, 24/7
        - ``governance_agent.eval`` - hourly
        - ``data_refresh``          - every 60s during equity hours
        - ``crypto_data_refresh``   - every 2 min, 24/7 (only with bars_cache)
        - ``position_reconcile``    - every 30s
        - ``eod_summary``           - 16:05 ET daily
        - ``nightly_backtest``      - 23:00 ET daily
        - ``weekly_walkforward``    - Sunday 02:00 ET
        - ``monthly_retrain``       - first Sunday of month, 03:00 ET
        - ``coherence_check``       - hourly
        - ``heartbeat``             - every 15s, always

        Jobs whose agent is missing from ``agents`` are skipped silently;
        this lets early bring-up wire only what's ready.

        When BOTH ``bars_cache`` and ``trade_pipeline`` are supplied, the
        agent eval jobs run the real pipeline: refresh bars if stale,
        evaluate strategies, route signals through the reasoner + risk
        gate, submit approved orders to the broker, journal the report.
        When either is None, the eval lambdas fall back to the legacy
        no-op stub (``agent.evaluate({})``) so existing tests + the dry
        bring-up path keep working.
        """
        live_pipeline = bars_cache is not None and trade_pipeline is not None

        self._register_agent_evals(
            agents,
            journal_writer=journal_writer,
            bars_cache=bars_cache,
            trade_pipeline=trade_pipeline,
            live_pipeline=live_pipeline,
        )
        self._register_data_refresh_jobs(
            agents,
            journal_writer=journal_writer,
            bars_cache=bars_cache,
            live_pipeline=live_pipeline,
        )
        self._register_operational_jobs(journal_writer=journal_writer)

    # -- helpers for add_default_jobs --------------------------------------

    def _register_agent_evals(
        self,
        agents: dict[str, Agent],
        *,
        journal_writer: Any,
        bars_cache: Any,
        trade_pipeline: Any,
        live_pipeline: bool,
    ) -> None:
        """Register the per-asset agent eval jobs.

        Builds the per-agent eval closure (live pipeline when both cache +
        pipeline are wired, no-op stub otherwise) and registers each agent
        on its own cadence + market-hours gate.
        """

        def _make_eval(agent: Agent) -> Callable[[], Any]:
            if not live_pipeline:
                return lambda a=agent: a.evaluate({})

            def _run() -> Any:
                try:
                    if bars_cache.is_stale_for(agent.asset_class):
                        bars_cache.refresh(agent.asset_class, agent.universe)
                except Exception:
                    log.exception(
                        "%s eval: bars refresh failed; using whatever's cached",
                        agent.name,
                    )
                bars = bars_cache.get_for(agent.universe)
                report = trade_pipeline.run_for(agent, bars)
                # Journal a compact summary so the dashboard sees activity
                # even when no signals fired (n_signals == 0 is normal).
                try:
                    journal_writer.write(
                        {
                            "event": "agent_eval_complete",
                            "agent": agent.name,
                            "n_signals": report.n_signals,
                            "n_submitted": report.n_submitted,
                            "n_refused": report.n_refused,
                            "n_bars_cached": len(bars),
                        }
                    )
                except Exception:
                    log.warning("%s eval: post-eval journal write failed", agent.name)
                return report

            _run.__name__ = f"{agent.name}_eval_pipeline"
            return _run

        # NYSE-gated equity-class agents (5-min cadence).
        for name in ("equity", "gold", "bonds"):
            agent = agents.get(name)
            if agent is None:
                continue
            self.register(
                f"{name}_agent.eval",
                _gate_market_hours(name, _make_eval(agent)),
                IntervalTrigger(minutes=5),
            )

        # 24/7 crypto, 5-min cadence. Tuned for paper-tier "constant
        # signal flow" rather than live cost-control: every 5 min x 11
        # crypto pairs x 4 strategies -> up to 264 signal evaluations / hr,
        # any of which can fire a trade if rules align. The data-refresh
        # job runs every 2 min so bars are fresh when eval fires.
        crypto = agents.get("crypto")
        if crypto is not None:
            self.register(
                "crypto_agent.eval",
                _make_eval(crypto),
                IntervalTrigger(minutes=5),
            )

        # Governance agent never trades; runs hourly via legacy stub path.
        governance = agents.get("governance")
        if governance is not None:
            self.register(
                "governance_agent.eval",
                lambda a=governance: a.evaluate({}),
                IntervalTrigger(hours=1),
            )

    def _register_data_refresh_jobs(
        self,
        agents: dict[str, Agent],
        *,
        journal_writer: Any,
        bars_cache: Any,
        live_pipeline: bool,
    ) -> None:
        """Register the data-refresh jobs.

        With a live pipeline, drives equity-class cache warming + a 24/7
        crypto cache refresh. Without a pipeline, falls back to the legacy
        journal-only stub on the equity-hours cadence.
        """
        if live_pipeline:

            def _equity_refresh() -> None:
                for cls_name in ("equity", "gold", "bonds"):
                    agent = agents.get(cls_name)
                    if agent is None:
                        continue
                    try:
                        bars_cache.refresh(agent.asset_class, agent.universe)
                    except Exception:
                        log.exception("data_refresh: %s refresh failed", cls_name)
                try:
                    journal_writer.write({"event": "data_refresh", "scope": "equity_class"})
                except Exception:
                    log.warning("data_refresh: post-refresh journal write failed")

            self.register(
                "data_refresh",
                _gate_market_hours("equity", _equity_refresh),
                IntervalTrigger(seconds=60),
            )

            crypto_agent = agents.get("crypto")
            if crypto_agent is not None:

                def _crypto_refresh(_a: Agent = crypto_agent) -> None:
                    try:
                        bars_cache.refresh(_a.asset_class, _a.universe)
                        journal_writer.write({"event": "crypto_data_refresh"})
                    except Exception:
                        log.exception("crypto_data_refresh failed")

                self.register(
                    "crypto_data_refresh",
                    _crypto_refresh,
                    IntervalTrigger(minutes=2),
                )
        else:
            self.register(
                "data_refresh",
                _gate_market_hours(
                    "equity",
                    lambda jw=journal_writer: jw.write({"event": "data_refresh"}),
                ),
                IntervalTrigger(seconds=60),
            )

    def _register_operational_jobs(self, *, journal_writer: Any) -> None:
        """Register the operational (non-agent) cadence jobs."""
        self.register(
            "position_reconcile",
            lambda jw=journal_writer: jw.write({"event": "position_reconcile"}),
            IntervalTrigger(seconds=30),
        )
        self.register(
            "eod_summary",
            lambda jw=journal_writer: jw.write({"event": "eod_summary"}),
            CronTrigger(hour=16, minute=5, timezone=ET),
        )
        self.register(
            "nightly_backtest",
            lambda jw=journal_writer: jw.write({"event": "nightly_backtest"}),
            CronTrigger(hour=23, minute=0, timezone=ET),
        )
        self.register(
            "weekly_walkforward",
            lambda jw=journal_writer: jw.write({"event": "weekly_walkforward"}),
            CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=ET),
        )
        # First Sunday of the month: APScheduler doesn't have a direct flag,
        # so we run every Sunday at 03:00 ET and the wrapper checks the day-
        # of-month is in [1, 7].
        self.register(
            "monthly_retrain",
            _first_sunday_only(
                lambda jw=journal_writer: jw.write({"event": "monthly_retrain"}),
            ),
            CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=ET),
        )
        self.register(
            "coherence_check",
            lambda jw=journal_writer: jw.write({"event": "coherence_check"}),
            IntervalTrigger(hours=1),
        )
        self.register(
            "heartbeat",
            lambda jw=journal_writer: jw.write(
                {"event": "heartbeat", "ts": datetime.now(ET).isoformat()}
            ),
            IntervalTrigger(seconds=15),
        )

    def run(self) -> None:
        """Block, run scheduler. Catches SIGTERM/SIGINT gracefully."""
        if self._scheduler is None:
            log.warning("scheduler unavailable; runner.run() is a no-op")
            return

        def _shutdown(_signum: int, _frame: Any) -> None:
            log.info("received shutdown signal; stopping scheduler")
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                log.exception("scheduler shutdown failed")

        # Best-effort signal handlers; some environments (e.g. threads in
        # tests) don't allow installing them, so we swallow ValueError.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _shutdown)
            except (ValueError, OSError):  # pragma: no cover - depends on host env
                pass

        try:
            self._scheduler.start()
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover - normal exit path
            log.info("interrupted; shutting scheduler down")
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                log.exception("scheduler shutdown failed during exit")


def _first_sunday_only(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap so the call only runs on the first Sunday of the month."""

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        # Day-of-month <= 7 => it's the first occurrence of whatever weekday
        # we're being called on. The cron trigger constrains it to Sunday.
        if datetime.now(ET).day > 7:
            return None
        return fn(*args, **kwargs)

    _wrapped.__name__ = getattr(fn, "__name__", "wrapped") + "__first_sunday_only"
    _wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
    return _wrapped
