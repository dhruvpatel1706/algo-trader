"""Gold agent — runs the gold-class trend + reversal strategies.

Default loadout:
  - ma_pullback_trend_gold  — 30/200 SMA pullback, wider stops for gold's macro shocks
  - failed_breakout_gold    — Donchian rejection with extra ATR cushion

Gold is the cleanest macro-regime hedge in the loadout: when equity drops
on a Fed surprise, gold often runs in the opposite direction. Pairing
trend + failed-breakdown gives both regime exposures (a trending bull
gold market via ma_pullback, and an oversold-rebound entry via
failed_breakout). Universe (GLD, IAU, GDX) is resolved through
:func:`Universe.named("gold")`.

Heat allocation defaults to 0; the multi-agent runner sets it from
coherence in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy
from src.strategies.failed_breakout_gold import FailedBreakoutGold
from src.strategies.ma_pullback_trend_gold import MaPullbackTrendGold


class GoldAgent(Agent):
    """Container for gold-class strategies."""

    name = "gold_agent"
    asset_class = AssetClass.GOLD

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            strategies = [MaPullbackTrendGold(), FailedBreakoutGold()]
        if universe is None:
            universe = Universe.named("gold")
        super().__init__(strategies=strategies, universe=universe, heat_allocation=heat_allocation)

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
