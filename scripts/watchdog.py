"""Dead-man switch supervisor.

Polls the runner's Redis heartbeat. If it goes stale - >threshold seconds
during equity RTH or >5 minutes any time (covering crypto-only periods) -
the watchdog calls the dashboard's flatten path. Designed to run as a
launchd / systemd unit independent of the runner so it survives a runner
crash.

Operation is best-effort:

- If Redis is unavailable, log and exit cleanly. (Without Redis we have no
  signal; nothing useful to do.)
- If ``dashboard.api.kill`` / ``dashboard.api.broker_proxy`` aren't
  importable, log a "would-flatten" message and exit instead of crashing.

CLI::

    uv run python scripts/watchdog.py [--check-interval-seconds 30]

Run with the same ``REDIS_URL`` env var the runner uses.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any

from src.runtime.calendar import is_open
from src.runtime.heartbeat import read_heartbeat

log = logging.getLogger("algo_trader.watchdog")

# Stale-thresholds. Equity hours = stricter because we're paying alpha for
# tighter loops; off-hours mostly cover crypto, which is fine on 5m.
_STALE_RTH_SEC = 90
_STALE_OFFHOURS_SEC = 300


def _redis_client() -> Any | None:
    url = os.environ.get("REDIS_URL")
    if not url:
        log.warning("REDIS_URL not set; watchdog cannot read heartbeat")
        return None
    try:
        import redis as _redis

        client = _redis.Redis.from_url(url)
        client.ping()
        return client
    except Exception as e:
        log.warning("Redis unavailable (%s); watchdog cannot read heartbeat", e)
        return None


def _stale_threshold(now_age: float | None) -> float:
    """Return the age threshold (seconds) appropriate for *right now*."""
    if is_open("equity"):
        return _STALE_RTH_SEC
    return _STALE_OFFHOURS_SEC


def flatten_all(reason: str = "watchdog: heartbeat stale") -> bool:
    """Best-effort: cancel orders + close positions via the dashboard kill path.

    Returns True if the kill path executed, False if it wasn't importable
    (in which case we log a "would-flatten" record and let the supervisor
    restart us / page a human).
    """
    try:
        from dashboard.api.broker_proxy import get_broker_proxy
        from dashboard.api.kill import execute_kill
        from dashboard.api.state import get_state  # type: ignore[import-not-found]
    except Exception as e:
        log.error("would-flatten (kill path unavailable: %s)", e)
        return False

    try:
        execute_kill(get_broker_proxy(), get_state(), reason=reason, requested_by="watchdog")
    except Exception as e:
        log.exception("flatten failed: %s", e)
        return False
    return True


def check_once(client: Any, role: str = "primary") -> str:
    """One iteration of the watchdog loop. Returns a status string for tests."""
    last = read_heartbeat(client, role=role)
    if last is None:
        threshold = _stale_threshold(None)
        log.warning("no heartbeat present (threshold=%ds)", threshold)
        flatten_all(reason=f"watchdog: no heartbeat (threshold={threshold}s)")
        return "missing"

    age = time.time() - last
    threshold = _stale_threshold(age)
    if age > threshold:
        log.error("heartbeat stale: age=%.0fs threshold=%ds", age, threshold)
        flatten_all(reason=f"watchdog: heartbeat stale {age:.0f}s > {threshold}s")
        return "stale"
    return "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner watchdog / dead-man switch.")
    parser.add_argument(
        "--check-interval-seconds",
        type=int,
        default=30,
        help="How often to poll the heartbeat (default: 30s).",
    )
    parser.add_argument(
        "--role",
        type=str,
        default="primary",
        help="Which heartbeat key to monitor (default: primary).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check and exit (used by tests).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    client = _redis_client()
    if client is None:
        return 0  # nothing to do; exit cleanly

    if args.once:
        check_once(client, role=args.role)
        return 0

    log.info("watchdog polling every %ds", args.check_interval_seconds)
    while True:
        check_once(client, role=args.role)
        time.sleep(args.check_interval_seconds)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
