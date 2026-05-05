"""Structured JSON logging via structlog.

Call ``configure_logging()`` once at process start. Everywhere else use
``get_logger(__name__)``. Output is one JSON object per line on stdout.
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.config import get_settings


def configure_logging(level: str | None = None) -> None:
    """Configure structlog + stdlib logging for the whole process."""
    lvl = (level or get_settings().LOG_LEVEL).upper()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, lvl, logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, lvl, logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


from src.observability.discord_alert import (  # noqa: E402, F401
    DiscordWebhookHandler,
    install_discord_alerts,
)
