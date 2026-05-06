"""Tests for `src/journal/refusal_events.py`.

Pinned properties:
  - The journal envelope shape is stable (`event="refusal"` plus the dataclass
    fields). Downstream dashboards assume this shape.
  - `log_refusal` swallows ANY exception from `writer.write` — a broken
    journal cannot crash trading.
  - Explicit `ts` overrides the default; default is current UTC.
  - Every `RefusalReason` literal is a valid string.
"""

from __future__ import annotations

import logging
import typing
from datetime import UTC, datetime, timedelta

import pytest
from src.journal.refusal_events import (
    REFUSAL_REASONS,
    RefusalEvent,
    RefusalReason,
    log_refusal,
)


class _StubWriter:
    """Captures every event passed to `write`."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


class _BrokenWriter:
    """`write` always raises — used to verify error swallowing."""

    def write(self, event: dict) -> None:
        raise OSError("disk full")


# ---------------------------------------------------------------------------
# RefusalEvent / to_journal_dict
# ---------------------------------------------------------------------------


def test_refusal_event_to_journal_dict_shape_is_stable() -> None:
    """Pin the journal envelope. Dashboard endpoints depend on this exact shape."""
    ev = RefusalEvent(
        ts="2026-05-06T15:42:13.234567+00:00",
        reason="reasoner_halt",
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        agent="equity_agent",
        signal_id="01HX01HX01HX01HX01HX01HX01",
        detail="Regime mismatch — high VIX",
        extra={"multiplier": 1.0},
    )
    out = ev.to_journal_dict()

    assert out["event"] == "refusal"
    assert out["reason"] == "reasoner_halt"
    assert out["symbol"] == "SPY"
    assert out["side"] == "buy"
    assert out["strategy"] == "failed_breakout"
    assert out["agent"] == "equity_agent"
    assert out["signal_id"] == "01HX01HX01HX01HX01HX01HX01"
    assert out["detail"] == "Regime mismatch — high VIX"
    assert out["extra"] == {"multiplier": 1.0}
    assert out["ts"] == "2026-05-06T15:42:13.234567+00:00"
    # No surprise extra keys.
    assert set(out.keys()) == {
        "event",
        "ts",
        "reason",
        "symbol",
        "side",
        "strategy",
        "agent",
        "signal_id",
        "detail",
        "extra",
    }


def test_refusal_event_to_journal_dict_preserves_none_fields() -> None:
    """A portfolio-level refusal has no symbol/side/strategy — keys still exist."""
    ev = RefusalEvent(
        ts="2026-05-06T00:00:00+00:00",
        reason="daily_loss_breach",
        symbol=None,
        side=None,
        strategy=None,
        agent=None,
        signal_id=None,
        detail="daily loss circuit breaker tripped",
    )
    out = ev.to_journal_dict()
    for key in ("symbol", "side", "strategy", "agent", "signal_id"):
        assert out[key] is None
    # Default extra is None.
    assert out["extra"] is None


def test_refusal_event_is_frozen() -> None:
    """Immutability prevents mutation drift in audit records."""
    ev = RefusalEvent(
        ts="2026-05-06T00:00:00+00:00",
        reason="manual_stop",
        symbol=None,
        side=None,
        strategy=None,
        agent=None,
        signal_id=None,
        detail="ok",
    )
    with pytest.raises((AttributeError, TypeError)):
        ev.detail = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# log_refusal
# ---------------------------------------------------------------------------


def test_log_refusal_builds_and_writes_event() -> None:
    """Happy path: kwargs become a journal envelope, sent to writer.write."""
    writer = _StubWriter()
    out = log_refusal(
        writer,
        reason="reasoner_halt",
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        agent="equity_agent",
        signal_id="abc-123",
        detail="bad regime",
        extra={"multiplier": 1.0, "provider": "anthropic"},
    )
    assert isinstance(out, RefusalEvent)
    assert out.reason == "reasoner_halt"
    assert out.symbol == "SPY"

    assert len(writer.events) == 1
    rec = writer.events[0]
    assert rec["event"] == "refusal"
    assert rec["reason"] == "reasoner_halt"
    assert rec["symbol"] == "SPY"
    assert rec["agent"] == "equity_agent"
    assert rec["signal_id"] == "abc-123"
    assert rec["detail"] == "bad regime"
    assert rec["extra"] == {"multiplier": 1.0, "provider": "anthropic"}
    # ts is auto-set to a parseable ISO8601 string.
    parsed = datetime.fromisoformat(rec["ts"])
    # Should be very recent (within the last 10s).
    assert (datetime.now(UTC) - parsed) < timedelta(seconds=10)


def test_log_refusal_with_minimal_kwargs() -> None:
    """Only `reason` and `detail` are required; everything else is optional."""
    writer = _StubWriter()
    out = log_refusal(writer, reason="manual_stop", detail="operator hit kill")
    assert out.reason == "manual_stop"
    assert out.symbol is None
    assert out.side is None
    assert out.strategy is None
    assert out.agent is None
    assert out.signal_id is None
    assert out.extra is None
    assert len(writer.events) == 1
    assert writer.events[0]["reason"] == "manual_stop"


def test_log_refusal_explicit_ts_overrides_default() -> None:
    """Tests/replay use cases need a deterministic timestamp."""
    writer = _StubWriter()
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    out = log_refusal(
        writer,
        reason="news_blackout",
        detail="sentiment < -0.5",
        ts=fixed,
    )
    assert out.ts == fixed.isoformat()
    assert writer.events[0]["ts"] == fixed.isoformat()


def test_log_refusal_swallows_writer_exception(caplog: pytest.LogCaptureFixture) -> None:
    """A broken journal must NOT crash the caller. Error is logged."""
    writer = _BrokenWriter()
    with caplog.at_level(logging.ERROR, logger="algo_trader.refusals"):
        out = log_refusal(
            writer,
            reason="risk_cap_position",
            symbol="NVDA",
            detail="position cap hit at 5%",
        )
    # The event was still constructed and returned, even though write blew up.
    assert isinstance(out, RefusalEvent)
    assert out.reason == "risk_cap_position"
    # The error was logged, not raised.
    assert any("refusal journal write failed" in r.message for r in caplog.records)


def test_log_refusal_swallows_arbitrary_exception() -> None:
    """Any exception type — not just OSError — must be swallowed."""

    class WeirdError(Exception):
        pass

    class _W:
        def write(self, event: dict) -> None:
            raise WeirdError("anything")

    out = log_refusal(_W(), reason="broker_rejected", detail="rejected by venue")
    assert out.reason == "broker_rejected"


def test_log_refusal_returns_event_even_on_writer_failure() -> None:
    """In-memory consumers can still react even if the journal write failed."""
    out = log_refusal(_BrokenWriter(), reason="correlation_alarm", detail="r=0.85")
    assert out.reason == "correlation_alarm"
    assert out.detail == "r=0.85"


# ---------------------------------------------------------------------------
# RefusalReason literal coverage
# ---------------------------------------------------------------------------


def test_refusal_reasons_tuple_matches_literal() -> None:
    """REFUSAL_REASONS must enumerate every value in the RefusalReason Literal."""
    literal_args = set(typing.get_args(RefusalReason))
    assert set(REFUSAL_REASONS) == literal_args
    # Also check ordering is deterministic (tuple, not set).
    assert isinstance(REFUSAL_REASONS, tuple)


@pytest.mark.parametrize("reason", REFUSAL_REASONS)
def test_every_refusal_reason_is_loggable(reason: RefusalReason) -> None:
    """Smoke check: every literal value works through the public API."""
    writer = _StubWriter()
    out = log_refusal(writer, reason=reason, detail=f"detail for {reason}")
    assert out.reason == reason
    assert writer.events[0]["reason"] == reason


def test_refusal_reasons_includes_expected_set() -> None:
    """Pin the reason vocabulary so we don't accidentally remove a code."""
    assert "reasoner_halt" in REFUSAL_REASONS
    assert "reasoner_dampened" in REFUSAL_REASONS
    assert "risk_cap_position" in REFUSAL_REASONS
    assert "risk_cap_portfolio" in REFUSAL_REASONS
    assert "correlation_alarm" in REFUSAL_REASONS
    assert "coherence_halt" in REFUSAL_REASONS
    assert "daily_loss_breach" in REFUSAL_REASONS
    assert "news_blackout" in REFUSAL_REASONS
    assert "manual_stop" in REFUSAL_REASONS
    assert "broker_rejected" in REFUSAL_REASONS
    assert "outside_market_hours" in REFUSAL_REASONS
    assert "context_builder_failed" in REFUSAL_REASONS
