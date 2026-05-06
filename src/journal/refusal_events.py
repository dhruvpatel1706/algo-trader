"""First-class refusal events.

We log fills today, but until now we had no first-class observability into
**REFUSALS** — the trades the bot deliberately did NOT take. A refusal is a
first-class governance audit row: "the bot didn't take 47 trades today, here
are the reason codes."

A refusal can come from many places in the pipeline:

  - The autonomous LLM reasoner returned ``halt=True`` (see
    ``src/agents/autonomous_reasoner.py``).
  - The reasoner returned a multiplier far below 1.0 (signal still flowed
    through, but the dampening is worth recording for audit).
  - The risk gate hit a single-position or portfolio cap.
  - The correlation alarm tripped vs an existing live strategy.
  - The coherence monitor halted the strategy (live_WR/backtest_WR < 0.5).
  - The daily-loss circuit breaker tripped.
  - News blackout (sentiment < -0.5).
  - The operator manually halted.
  - The broker rejected an order.
  - The signal arrived outside market hours.
  - The reasoner context builder failed (we record this so a chronic ETL
    outage is visible in the dashboard, not silently swallowed).

Refusals are written through the existing :class:`JournalWriter` so they go
through the same redaction + fsync path as every other event.

Failure semantics: ``log_refusal`` MUST swallow exceptions raised by the
journal writer. A broken journal must not crash trading — at most we
degrade observability.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

logger = logging.getLogger("algo_trader.refusals")


RefusalReason = Literal[
    "reasoner_halt",
    "reasoner_dampened",
    "risk_cap_position",
    "risk_cap_portfolio",
    "correlation_alarm",
    "coherence_halt",
    "daily_loss_breach",
    "news_blackout",
    "manual_stop",
    "broker_rejected",
    "outside_market_hours",
    "context_builder_failed",
]


# Frozen tuple of every literal value above — let callers and tests
# enumerate the reason space without re-typing the strings.
REFUSAL_REASONS: tuple[RefusalReason, ...] = (
    "reasoner_halt",
    "reasoner_dampened",
    "risk_cap_position",
    "risk_cap_portfolio",
    "correlation_alarm",
    "coherence_halt",
    "daily_loss_breach",
    "news_blackout",
    "manual_stop",
    "broker_rejected",
    "outside_market_hours",
    "context_builder_failed",
)


@dataclass(slots=True, frozen=True)
class RefusalEvent:
    """Structured refusal event.

    Fields:
        ts: ISO8601 UTC timestamp.
        reason: one of :data:`RefusalReason`.
        symbol: ticker, if applicable. ``None`` for portfolio-level refusals.
        side: ``"buy"`` / ``"sell"`` if the refused signal had a direction.
        strategy: strategy tag (e.g. ``"failed_breakout"``).
        agent: agent name (e.g. ``"equity_agent"``).
        signal_id: optional ULID/UUID linking back to the originating signal.
        detail: free-form, human-readable explanation. Audit summary.
        extra: arbitrary structured payload for reason-specific context.
    """

    ts: str
    reason: RefusalReason
    symbol: str | None
    side: str | None
    strategy: str | None
    agent: str | None
    signal_id: str | None
    detail: str
    extra: dict[str, Any] | None = None

    def to_journal_dict(self) -> dict[str, Any]:
        """Render for :meth:`JournalWriter.write`.

        Wraps the refusal in an ``event="refusal"`` envelope. ``None``
        values are kept (the journal preserves shape across days so
        downstream queries don't need to special-case missing keys).
        """
        payload = asdict(self)
        return {"event": "refusal", **payload}


class JournalLike(Protocol):
    """Structural type for any object exposing :meth:`write`.

    Tests can pass a stub; production passes a :class:`JournalWriter`.
    """

    def write(self, event: dict[str, Any]) -> None: ...


def log_refusal(
    writer: JournalLike,
    *,
    reason: RefusalReason,
    symbol: str | None = None,
    side: str | None = None,
    strategy: str | None = None,
    agent: str | None = None,
    signal_id: str | None = None,
    detail: str,
    extra: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> RefusalEvent:
    """Build a :class:`RefusalEvent` and emit it through ``writer``.

    Args:
        writer: any object exposing ``.write(dict)``. A broken writer is
            tolerated — the exception is logged via stdlib ``logging`` and
            swallowed so callers never crash on observability failures.
        reason: one of the :data:`RefusalReason` literals.
        symbol: ticker if relevant. Pass ``None`` for portfolio-level
            refusals (e.g. daily-loss breach).
        side: ``"buy"`` or ``"sell"`` if the refused signal had one.
        strategy: strategy tag if known.
        agent: agent name if known.
        signal_id: stable id linking back to the originating signal.
        detail: short human-readable explanation. Required.
        extra: arbitrary structured context (e.g. multiplier value, cap
            limits, correlation coefficient).
        ts: explicit timestamp, primarily for tests. Defaults to
            ``datetime.now(UTC)``.

    Returns:
        The :class:`RefusalEvent` that was built. Returned regardless of
        whether the journal write succeeded — callers can still react to
        the refusal in-memory.
    """
    timestamp = (ts or datetime.now(UTC)).isoformat()
    event = RefusalEvent(
        ts=timestamp,
        reason=reason,
        symbol=symbol,
        side=side,
        strategy=strategy,
        agent=agent,
        signal_id=signal_id,
        detail=detail,
        extra=extra,
    )
    try:
        writer.write(event.to_journal_dict())
    except Exception as exc:
        logger.error(
            "refusal journal write failed: reason=%s symbol=%s err=%s",
            reason,
            symbol,
            exc,
        )
    return event


__all__ = [
    "REFUSAL_REASONS",
    "JournalLike",
    "RefusalEvent",
    "RefusalReason",
    "log_refusal",
]
