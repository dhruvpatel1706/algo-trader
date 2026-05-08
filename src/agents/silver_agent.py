"""Silver agent — runs the silver-class trend + reversal strategies.

Default loadout (gold-tuned variants applied to the silver universe):
  - ma_pullback_trend_gold  — 30/200 SMA pullback, wider stops
  - failed_breakout_gold    — Donchian rejection with extra ATR cushion

Silver behaves like a high-beta amplification of gold — same macro
drivers (DXY, real rates), but with ~1.5-2x the volatility. The "gold"
parameter set is already tuned for that volatility profile, so we reuse
it on the silver universe rather than maintain near-duplicate
parameter classes. Universe (SLV, SIVR, SIL, PSLV) is resolved through
:func:`Universe.named("silver")`.

If a real silver-specific tuning emerges from a backtest (e.g. wider
ATR multiplier on SIL since it's miners not bullion), break it out
into a dedicated ``*_silver`` strategy module — same pattern as the
gold and bonds files.

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


class SilverAgent(Agent):
    """Container for silver-class strategies."""

    name = "silver_agent"
    asset_class = AssetClass.SILVER

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            # Reuse the gold tuning — same macro driver, similar vol profile.
            strategies = [MaPullbackTrendGold(), FailedBreakoutGold()]
        if universe is None:
            universe = Universe.named("silver")
        super().__init__(strategies=strategies, universe=universe, heat_allocation=heat_allocation)

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
