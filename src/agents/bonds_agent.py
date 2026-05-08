"""Bonds agent — runs the fixed-income trend strategies.

Default loadout:
  - ma_pullback_trend_bonds — 50/200 SMA pullback tuned for slow trends

Bonds trend on a multi-month cadence (Fed cycles), so the equity-default
20/200 SMA pullback is too noisy on TLT-style instruments. The bond
variant runs a 50/200 system with a tighter ATR stop and a longer slope
confirmation window. Universe (TLT, IEF, SHY, AGG, BND, HYG, LQD) is
resolved through :func:`Universe.named("bonds")`.

This agent gives the portfolio its rates exposure leg — the structural
diversifier that compresses overall Sharpe variance when equity / crypto
both regime-shift at once. Heat allocation defaults to 0; the
multi-agent runner sets it from coherence in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy
from src.strategies.ma_pullback_trend_bonds import MaPullbackTrendBonds


class BondsAgent(Agent):
    """Container for bond-class strategies."""

    name = "bonds_agent"
    asset_class = AssetClass.BONDS

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            strategies = [MaPullbackTrendBonds()]
        if universe is None:
            universe = Universe.named("bonds")
        super().__init__(strategies=strategies, universe=universe, heat_allocation=heat_allocation)

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
