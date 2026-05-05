"""Tests for src.observability.discord_alert."""

from __future__ import annotations

import json
import logging
import urllib.error
from unittest import mock

import pytest
from src.observability.discord_alert import (
    DiscordWebhookHandler,
    install_discord_alerts,
)


def _make_record(
    name: str = "algo_trader",
    level: int = logging.WARNING,
    msg: str = "boom",
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_no_webhook_url_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler with no webhook URL is a no-op (call emit, no error, no post)."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    h = DiscordWebhookHandler()
    rec = _make_record()
    with mock.patch("urllib.request.urlopen") as urlopen:
        h.emit(rec)  # must not raise
        urlopen.assert_not_called()


def test_emit_posts_with_correct_content() -> None:
    """Handler with webhook URL calls POST with correct content."""
    h = DiscordWebhookHandler(webhook_url="https://discord.test/hook")
    rec = _make_record(msg="hello world")
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        h.emit(rec)
        assert urlopen.call_count == 1
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://discord.test/hook"
        assert req.get_method() == "POST"
        body = json.loads(req.data.decode("utf-8"))
        assert body == {"content": "hello world"}
        assert req.headers.get("Content-type") == "application/json"


def test_throttle_suppresses_rapid_repeats() -> None:
    """Rapid emit of same level/logger only posts once within window."""
    h = DiscordWebhookHandler(
        webhook_url="https://discord.test/hook", throttle_seconds=30
    )
    rec = _make_record()
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        for _ in range(5):
            h.emit(rec)
        assert urlopen.call_count == 1


def test_different_keys_do_not_throttle_each_other() -> None:
    """Different (level, logger) keys do NOT throttle each other."""
    h = DiscordWebhookHandler(
        webhook_url="https://discord.test/hook", throttle_seconds=30
    )
    r1 = _make_record(name="logger.a", level=logging.WARNING)
    r2 = _make_record(name="logger.b", level=logging.WARNING)
    r3 = _make_record(name="logger.a", level=logging.ERROR)
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        h.emit(r1)
        h.emit(r2)
        h.emit(r3)
        assert urlopen.call_count == 3


def test_message_is_truncated_to_2000_chars() -> None:
    """Message > 2000 chars is truncated to the Discord limit."""
    h = DiscordWebhookHandler(webhook_url="https://discord.test/hook")
    big = "x" * 5000
    rec = _make_record(msg=big)
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        h.emit(rec)
        req = urlopen.call_args.args[0]
        body = json.loads(req.data.decode("utf-8"))
        assert len(body["content"]) == 2000
        assert body["content"] == "x" * 2000


def test_network_exception_is_swallowed() -> None:
    """Network exception in POST is swallowed (no crash to caller)."""
    h = DiscordWebhookHandler(webhook_url="https://discord.test/hook")
    rec = _make_record()
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("network down"),
    ):
        h.emit(rec)  # must not raise


def test_install_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """install_discord_alerts is idempotent."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    name = "algo_trader_test_idem"
    # Clean any pre-existing handlers on this logger.
    logger = logging.getLogger(name)
    for h in list(logger.handlers):
        logger.removeHandler(h)

    h1 = install_discord_alerts(logger_name=name)
    h2 = install_discord_alerts(logger_name=name)
    assert h1 is not None
    assert h2 is not None
    assert h1 is h2
    discord_handlers = [
        h for h in logger.handlers if isinstance(h, DiscordWebhookHandler)
    ]
    assert len(discord_handlers) == 1


def test_install_no_env_var_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """install_discord_alerts with no env var returns None."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert install_discord_alerts(logger_name="algo_trader_test_none") is None
