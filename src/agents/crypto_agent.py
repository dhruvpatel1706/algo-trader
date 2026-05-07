"""Crypto agent — runs the crypto-tuned long-only strategies.

Default loadout (paper-validation phase):
  - failed_breakout_crypto      — Donchian rejection on crypto bars
  - ma_pullback_trend_crypto    — 20/200 SMA pullback
  - ema_ribbon_compression      — Fibonacci EMA compression breakout
  - funding_rate_divergence     — crowded-shorts mean reversion

Parameters are tuned for crypto's faster ranges, higher volatility, and
thinner liquidity — see each strategy's docstring. All four are long-only
per repo policy. The two new entries (ema_ribbon, funding_rate) are in
paper-validation; their signals flow through the same risk gate as the
older two so they can't violate the per-symbol or portfolio-heat caps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy
from src.strategies.ema_ribbon_compression import EmaRibbonCompression
from src.strategies.failed_breakout_crypto import FailedBreakoutCrypto
from src.strategies.funding_rate_divergence import FundingRateDivergence
from src.strategies.ma_pullback_trend_crypto import MaPullbackTrendCrypto


class CryptoAgent(Agent):
    """Container for crypto-class strategies."""

    name = "crypto_agent"
    asset_class = AssetClass.CRYPTO

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
    ) -> None:
        if strategies is None:
            strategies = [
                FailedBreakoutCrypto(),
                MaPullbackTrendCrypto(),
                EmaRibbonCompression(),
                FundingRateDivergence(),
            ]
        if universe is None:
            universe = Universe.named("crypto_majors")
        super().__init__(
            strategies=strategies,
            universe=universe,
            heat_allocation=heat_allocation,
        )

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        for strat in self.strategies:
            out.extend(strat.generate_signals(bars))
        self._last_eval_ts = datetime.now(UTC)
        return out
