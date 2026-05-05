"""Governance agent — meta-agent that supervises the trading agents.

The governance agent does NOT execute trades. It reads the runtime state of
trading agents (coherence, P&L, drawdowns, halts) and emits
GovernanceRecommendation objects that an operator (or a downstream automation
layer) can act on: kill a strategy, promote it from paper to live, halt or
unhalt an agent, or flag an agent for human investigation.

v1 implements the simplest possible policy:
  - Coherence below `coherence_kill_threshold` -> kill
  - Halted state -> investigate
  - No data yet (NaN coherence) -> no recommendation

The policy is intentionally conservative: better to surface a question than to
act on a thin signal. Promotion logic stays manual until we have enough live
data to define a quantitative rule.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from src.agents.base import Agent, AssetClass
from src.strategies.base import Signal

GovernanceAction = Literal["kill", "promote", "halt", "unhalt", "investigate"]


@dataclass(frozen=True, slots=True)
class GovernanceRecommendation:
    """Single recommendation about a target strategy or agent.

    `confidence` is the governance layer's own confidence in the
    recommendation, on [0, 1]. It is independent of strategy signal confidence.
    """

    target_strategy: str
    action: GovernanceAction
    reason: str
    confidence: float
    ts: datetime

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be in [0, 1]")


class GovernanceAgent(Agent):
    """Non-trading meta-agent that emits governance recommendations.

    `evaluate(state)` differs from the trading agents: it consumes a state
    object (typically a list/iterable of AgentStatus snapshots) rather than
    OHLCV bars, and returns recommendations rather than signals.

    Inheriting from Agent keeps the registry/lookup uniform. The trading-style
    `evaluate(bars)` returns an empty list — governance never produces signals.
    """

    name = "governance_agent"
    asset_class = AssetClass.GOVERNANCE

    def __init__(
        self,
        strategies: list | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
        coherence_kill_threshold: float = 0.5,
    ) -> None:
        super().__init__(
            strategies=strategies or [],
            universe=universe or (),
            heat_allocation=heat_allocation,
        )
        self.coherence_kill_threshold = coherence_kill_threshold

    def evaluate(self, state: Any = None) -> list[Signal] | list[GovernanceRecommendation]:  # type: ignore[override]
        """Apply policy over a state object, return list[GovernanceRecommendation].

        The signature is intentionally permissive: when called with bars (the
        trading-agent contract) we return an empty signal list; when called
        with an iterable of AgentStatus we return recommendations.
        """
        self._last_eval_ts = datetime.now(UTC)

        # No state, or bars-shaped input — governance has nothing to say.
        if state is None or isinstance(state, dict):
            return []

        if not isinstance(state, Iterable):
            return []

        recs: list[GovernanceRecommendation] = []
        now = datetime.now(UTC)
        for status in state:
            # Accept either an AgentStatus or a duck-typed object with the
            # same attributes. Tests use a SimpleNamespace.
            name = getattr(status, "name", None)
            coherence = getattr(status, "coherence", None)
            agent_state = getattr(status, "state", None)
            if name is None:
                continue

            if agent_state == "halted":
                recs.append(
                    GovernanceRecommendation(
                        target_strategy=name,
                        action="investigate",
                        reason="agent is halted; verify cause and decide on unhalt or kill",
                        confidence=0.6,
                        ts=now,
                    )
                )
                continue

            if isinstance(coherence, (int, float)) and not math.isnan(float(coherence)):
                if coherence < self.coherence_kill_threshold:
                    recs.append(
                        GovernanceRecommendation(
                            target_strategy=name,
                            action="kill",
                            reason=(
                                f"coherence {coherence:.2f} below threshold "
                                f"{self.coherence_kill_threshold:.2f}"
                            ),
                            confidence=0.7,
                            ts=now,
                        )
                    )
        return recs
