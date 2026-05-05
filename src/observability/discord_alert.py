"""Discord webhook alert handler.

A ``logging.Handler`` that posts WARNING+ messages to a Discord webhook.
Reads webhook URL from env ``DISCORD_WEBHOOK_URL``. If unset/empty, the
handler is a graceful no-op (does NOT raise) per the project's defaults
policy.

Usage::

    from src.observability.discord_alert import install_discord_alerts
    install_discord_alerts()  # idempotent; respects DISCORD_WEBHOOK_URL env

Because the project's :mod:`src.observability.logging` module configures the
stdlib root logger via ``logging.basicConfig`` and structlog routes through
stdlib logging, attaching this handler to the project root logger
(``"algo_trader"`` by default) will receive structlog-rendered messages
automatically. If a custom processor chain is used that bypasses stdlib,
attach the handler to whichever logger the chain ultimately writes through.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from src.net import UnsafeUrlError, safe_urlopen

# Discord enforces a 2000-character limit on message ``content``.
_DISCORD_MAX_CONTENT = 2000
_HTTP_TIMEOUT_SEC = 3.0


class DiscordWebhookHandler(logging.Handler):
    """logging.Handler that posts WARNING+ to a Discord webhook.

    Reads webhook URL from env ``DISCORD_WEBHOOK_URL``. If unset or empty,
    the handler is a no-op (does NOT raise) — graceful default per the
    project's defaults policy.

    Throttles to one POST per N seconds per ``(level, logger_name)`` tuple
    to avoid spam. Truncates messages > 2000 chars (Discord limit).
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        level: int = logging.WARNING,
        throttle_seconds: int = 30,
    ) -> None:
        super().__init__(level=level)
        self._webhook = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self._throttle = throttle_seconds
        self._last_post: dict[tuple[int, str], float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if not self._webhook:
            return  # graceful no-op
        # throttle
        key = (record.levelno, record.name)
        now = time.time()
        last = self._last_post.get(key)
        if last is not None and (now - last) < self._throttle:
            return
        # POST to Discord — best-effort. Network errors must never crash the
        # logger that produced the record being posted.
        try:
            self._post(self.format(record))
            self._last_post[key] = now
        except Exception:
            self.handleError(record)

    def _post(self, message: str) -> None:
        """POST ``{"content": message[:2000]}`` to ``self._webhook``.

        Uses urllib (stdlib) with a 3-second timeout. Catches and swallows
        any error so logging never propagates network failures.
        """
        truncated = message[:_DISCORD_MAX_CONTENT]
        payload: dict[str, Any] = {"content": truncated}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — scheme guarded by safe_urlopen below
            self._webhook,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with safe_urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
                # Drain a tiny bit so the connection can be cleanly closed.
                resp.read(1)
        except (urllib.error.URLError, TimeoutError, OSError, UnsafeUrlError):
            # Swallow network errors — alerting is best-effort.
            pass


def install_discord_alerts(
    logger_name: str = "algo_trader",
    level: int = logging.WARNING,
) -> DiscordWebhookHandler | None:
    """Install the Discord handler on the named logger if ``DISCORD_WEBHOOK_URL`` is set.

    Returns the handler if installed, ``None`` if no webhook URL.

    Idempotent: calling it twice does not register a second handler — the
    second call returns the existing :class:`DiscordWebhookHandler`.

    Note on structlog integration: the project's structlog setup writes
    through stdlib logging (via ``logging.basicConfig``), so attaching this
    handler to a stdlib logger is sufficient — structlog-rendered records
    flow through. If a project uses a structlog processor chain that
    bypasses stdlib (e.g. its own ``LoggerFactory`` that writes directly to
    a stream), this handler will not see those records; in that case attach
    to whichever stdlib logger the chain ultimately routes to, or wire a
    structlog processor that calls into this handler.
    """
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        return None

    logger = logging.getLogger(logger_name)
    # Idempotency: return any already-installed handler instead of stacking.
    for existing in logger.handlers:
        if isinstance(existing, DiscordWebhookHandler):
            return existing

    handler = DiscordWebhookHandler(level=level)
    logger.addHandler(handler)
    return handler
