"""Equity agent — runs the long-only US equity/ETF strategies.

v1 wires the three production-ready equity strategies: mean-reversion ETF,
20/200 MA pullback trend, and failed-breakout fade. Universe pulls from the
liquid_etfs_top20 list — wide enough for regime diversity, narrow enough that
all members have continuous price history on free-tier daily data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy
from src.strategies.failed_breakout import FailedBreakout
from src.strategies.ma_pullback_trend import MaPullbackTrend
from src.strategies.mr_etf import MrEtf
from src.strategies.vwap_open_retest import VwapOpenRetest


class EquityAgent(Agent):
    """Container for US equity/ETF strategies."""

    name = "equity_agent"
    asset_class = AssetClass.EQUITY

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            strategies = [MrEtf(), MaPullbackTrend(), FailedBreakout(), VwapOpenRetest()]
        if universe is None:
            universe = Universe.named("liquid_etfs_top20")
        super().__init__(strategies=strategies, universe=universe, heat_allocation=heat_allocation)

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        """Run every strategy on the supplied bars and merge their signals."""
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
