"""Crypto agent — placeholder container for crypto-class strategies.

v1 ships with no strategies wired in. Universe is the crypto majors list;
strategies will land here as the crypto variants of trend/pullback are
productionised.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy


class CryptoAgent(Agent):
    """Container for crypto-class strategies (empty in v1)."""

    name = "crypto_agent"
    asset_class = AssetClass.CRYPTO

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            strategies = []
        if universe is None:
            universe = Universe.named("crypto_majors")
        super().__init__(strategies=strategies, universe=universe, heat_allocation=heat_allocation)

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
