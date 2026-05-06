"""Glue: apply the autonomous LLM reasoner to a batch of strategy signals.

Used by Agents (or any orchestrator) as a post-processing filter on the
output of `Strategy.generate_signals()`. The strategy stays a pure
deterministic function; the LLM reasoning happens here, after.

Contract:

    rule_signals = strategy.generate_signals(bars)
    judged = apply_reasoner_to_signals(rule_signals, reasoner, ctx_builder)

For each rule-based signal:
  1. Build a SignalContext (regime, recent bars, news, etc) via the
     caller-supplied builder.
  2. Run the reasoner; get back a SignalJudgment.
  3. If `judgment.halt`, drop the signal entirely.
  4. Otherwise, return a new Signal with `confidence *= judgment.multiplier`,
     clamped to [0, 1].

Failure modes:
  - context builder raises -> signal passes through with multiplier=1.0,
    we log the exception. The reasoner itself never runs in this case.
  - reasoner returns fail-open (LLM unavailable) -> identity multiplier,
    signal passes through unchanged.

Refusal observability:
  When a ``journal_writer`` is supplied, this module also emits first-class
  refusal events (via ``src.journal.refusal_events.log_refusal``) so the
  dashboard can surface "we declined N signals today, here's why":

    * ``reasoner_halt`` — judgment.halt was True; signal dropped.
    * ``reasoner_dampened`` — judgment.multiplier < DAMPENED_REFUSAL_THRESHOLD.
      The signal still flows through (this is purely an observability hook,
      NOT a block).
    * ``context_builder_failed`` — context builder raised; signal passed
      through with identity multiplier; we record the failure so chronic
      ETL outages are visible in the dashboard, not silently swallowed.

  Without ``journal_writer`` the function is byte-identical to the
  pre-refusal-events implementation (all existing tests still pass).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from src.agents.autonomous_reasoner import (
    AutonomousReasoner,
    SignalContext,
    SignalJudgment,
)
from src.journal.refusal_events import JournalLike, log_refusal
from src.strategies.base import Signal

logger = logging.getLogger(__name__)


ContextBuilder = Callable[[Signal], SignalContext]


# A reasoner multiplier strictly below this threshold is considered
# "significant dampening" and emits a refusal event for visibility. Tuned
# at 0.7 because the reasoner's safe band is [0.5, 1.2]; anything below 0.7
# is firmly in the "the LLM is uncomfortable with this setup" half of the
# range — worth surfacing in audit, but the signal is NOT blocked.
DAMPENED_REFUSAL_THRESHOLD: float = 0.7


def apply_reasoner_to_signals(
    signals: list[Signal],
    reasoner: AutonomousReasoner,
    context_builder: ContextBuilder,
    *,
    journal_writer: JournalLike | None = None,
    agent_name: str | None = None,
) -> tuple[list[Signal], list[SignalJudgment]]:
    """Run the reasoner over each signal; return (filtered_signals, judgments).

    The judgments list is the SAME LENGTH as the input ``signals`` and aligned
    by index — even for signals that get halted/dropped, the judgment is in
    the list so callers can journal the full audit trail.

    Args:
        signals: rule-based signals to evaluate.
        reasoner: an :class:`AutonomousReasoner` (or duck-typed equivalent
            with ``.evaluate(SignalContext) -> SignalJudgment``).
        context_builder: callable that turns a :class:`Signal` into a
            :class:`SignalContext`. May raise — failures are caught and
            recorded as a ``context_builder_failed`` refusal.
        journal_writer: optional journal target for refusal events. When
            ``None`` (the default), no refusal events are emitted and this
            function behaves exactly like the pre-refusal-events version,
            preserving existing test behavior.
        agent_name: optional agent identifier (e.g. ``"equity_agent"``)
            attached to refusal events for dashboard filtering.

    Returns:
        ``(filtered_signals, judgments)``. Halted signals are missing from
        ``filtered_signals`` but their judgment is in ``judgments`` (aligned
        by input index for the full audit trail).
    """
    out_signals: list[Signal] = []
    out_judgments: list[SignalJudgment] = []
    for sig in signals:
        try:
            ctx = context_builder(sig)
        except Exception as e:
            logger.warning(
                "reasoner_filter: context builder failed for %s: %s — passing through",
                sig.symbol,
                e,
            )
            out_signals.append(sig)
            # Record an identity judgment so the audit log still has a row.
            out_judgments.append(_identity_judgment_for_audit(reason=f"context build failed: {e}"))
            if journal_writer is not None:
                log_refusal(
                    journal_writer,
                    reason="context_builder_failed",
                    symbol=sig.symbol,
                    side=sig.side,
                    strategy=sig.strategy_tag,
                    agent=agent_name,
                    detail=f"context builder raised: {e}",
                    extra={"exception_type": type(e).__name__},
                )
            continue

        judgment = reasoner.evaluate(ctx)
        out_judgments.append(judgment)
        if judgment.halt:
            logger.info(
                "reasoner_filter: %s halted by reasoner: %s",
                sig.symbol,
                judgment.reasoning,
            )
            if journal_writer is not None:
                log_refusal(
                    journal_writer,
                    reason="reasoner_halt",
                    symbol=sig.symbol,
                    side=sig.side,
                    strategy=sig.strategy_tag,
                    agent=agent_name,
                    detail=judgment.reasoning,
                    extra={
                        "multiplier": judgment.multiplier,
                        "provider": judgment.provider,
                        "fail_open": judgment.fail_open,
                    },
                )
            continue
        if (
            journal_writer is not None
            and judgment.multiplier < DAMPENED_REFUSAL_THRESHOLD
        ):
            log_refusal(
                journal_writer,
                reason="reasoner_dampened",
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent=agent_name,
                detail=(
                    f"reasoner dampened multiplier={judgment.multiplier:.3f} "
                    f"below threshold={DAMPENED_REFUSAL_THRESHOLD}: {judgment.reasoning}"
                ),
                extra={
                    "multiplier": judgment.multiplier,
                    "threshold": DAMPENED_REFUSAL_THRESHOLD,
                    "provider": judgment.provider,
                    "fail_open": judgment.fail_open,
                },
            )
        new_confidence = max(0.0, min(1.0, sig.confidence * judgment.multiplier))
        out_signals.append(replace(sig, confidence=new_confidence))
    return out_signals, out_judgments


def _identity_judgment_for_audit(reason: str) -> SignalJudgment:
    from datetime import UTC, datetime

    return SignalJudgment(
        multiplier=1.0,
        halt=False,
        reasoning=reason,
        provider=None,
        elapsed_ms=0,
        asof=datetime.now(UTC).isoformat(),
        fail_open=True,
    )


__all__ = ["DAMPENED_REFUSAL_THRESHOLD", "ContextBuilder", "apply_reasoner_to_signals"]
