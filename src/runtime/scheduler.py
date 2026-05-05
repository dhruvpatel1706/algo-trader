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
    ) -> None:
        """Wire up the default job calendar from the multi-asset plan.

        Jobs registered (when the matching agent is supplied):

        - ``equity_agent.eval``     - every 5 min, gated to NYSE hours
        - ``gold_agent.eval``       - every 5 min, gated to NYSE hours
        - ``bonds_agent.eval``      - every 5 min, gated to NYSE hours
        - ``crypto_agent.eval``     - every 15 min, 24/7
        - ``governance_agent.eval`` - hourly
        - ``data_refresh``          - every 60s during equity hours
        - ``position_reconcile``    - every 30s
        - ``eod_summary``           - 16:05 ET daily
        - ``nightly_backtest``      - 23:00 ET daily
        - ``weekly_walkforward``    - Sunday 02:00 ET
        - ``monthly_retrain``       - first Sunday of month, 03:00 ET
        - ``coherence_check``       - hourly
        - ``heartbeat``             - every 15s, always

        Jobs whose agent is missing from ``agents`` are skipped silently;
        this lets early bring-up wire only what's ready.
        """
        # --- Per-asset agent eval jobs ------------------------------------
        equity = agents.get("equity")
        if equity is not None:
            self.register(
                "equity_agent.eval",
                _gate_market_hours("equity", lambda a=equity: a.evaluate({})),
                IntervalTrigger(minutes=5),
            )
        gold = agents.get("gold")
        if gold is not None:
            self.register(
                "gold_agent.eval",
                _gate_market_hours("gold", lambda a=gold: a.evaluate({})),
                IntervalTrigger(minutes=5),
            )
        bonds = agents.get("bonds")
        if bonds is not None:
            self.register(
                "bonds_agent.eval",
                _gate_market_hours("bonds", lambda a=bonds: a.evaluate({})),
                IntervalTrigger(minutes=5),
            )
        crypto = agents.get("crypto")
        if crypto is not None:
            self.register(
                "crypto_agent.eval",
                lambda a=crypto: a.evaluate({}),
                IntervalTrigger(minutes=15),
            )
        governance = agents.get("governance")
        if governance is not None:
            self.register(
                "governance_agent.eval",
                lambda a=governance: a.evaluate({}),
                IntervalTrigger(hours=1),
            )

        # --- Operational jobs --------------------------------------------
        # data_refresh is gated to equity hours (the equity universe drives
        # the cadence; crypto agents fetch on their own schedule).
        self.register(
            "data_refresh",
            _gate_market_hours(
                "equity",
                lambda jw=journal_writer: jw.write({"event": "data_refresh"}),
            ),
            IntervalTrigger(seconds=60),
        )
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
