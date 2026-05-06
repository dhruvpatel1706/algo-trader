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
from src.strategies.base import Signal

logger = logging.getLogger(__name__)


ContextBuilder = Callable[[Signal], SignalContext]


def apply_reasoner_to_signals(
    signals: list[Signal],
    reasoner: AutonomousReasoner,
    context_builder: ContextBuilder,
) -> tuple[list[Signal], list[SignalJudgment]]:
    """Run the reasoner over each signal; return (filtered_signals, judgments).

    The judgments list is the SAME LENGTH as the input ``signals`` and aligned
    by index — even for signals that get halted/dropped, the judgment is in
    the list so callers can journal the full audit trail.
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
            continue

        judgment = reasoner.evaluate(ctx)
        out_judgments.append(judgment)
        if judgment.halt:
            logger.info(
                "reasoner_filter: %s halted by reasoner: %s",
                sig.symbol,
                judgment.reasoning,
            )
            continue
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


__all__ = ["ContextBuilder", "apply_reasoner_to_signals"]
