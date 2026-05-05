"""Silver agent — placeholder container for silver-class strategies.

v1 ships with no strategies wired in. Universe is the silver ETF list; strategies
will land here as silver variants of trend/pullback are productionised.

Activity hint: watching SLV/SIVR/SIL/PSLV for failed-breakdown rejection +
20/200 SMA pullback (paper-only commodities exposure).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy


class SilverAgent(Agent):
    """Container for silver-class strategies (empty in v1)."""

    name = "silver_agent"
    asset_class = AssetClass.SILVER

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            strategies = []
        if universe is None:
            universe = Universe.named("silver")
        super().__init__(strategies=strategies, universe=universe, heat_allocation=heat_allocation)

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
