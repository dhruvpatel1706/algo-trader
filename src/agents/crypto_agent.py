"""Crypto agent — runs the crypto-tuned long-only strategies.

Default loadout (paper-validation phase):
  - failed_breakout_crypto      — Donchian rejection on crypto bars (1d)
  - ma_pullback_trend_crypto    — 20/200 SMA pullback (1d)
  - ema_ribbon_compression      — Fibonacci EMA compression breakout (4h)
  - funding_rate_divergence     — crowded-shorts mean reversion (1d)

Parameters are tuned for crypto's faster ranges, higher volatility, and
thinner liquidity — see each strategy's docstring. All four are long-only
per repo policy.

Multi-timeframe support: each strategy declares ``bar_interval`` (default
``"1d"``). Strategies that need a different interval (e.g.
``ema_ribbon_compression`` on ``"4h"``) get fresh bars fetched on demand
and merged into the strategy's eval input. The bars dict the agent
receives is treated as the daily baseline; non-daily intervals are
loaded inline by :meth:`evaluate`.

The on-demand fetch goes through ``load_crypto_bars``, which parquet-caches
and falls through Alpaca → Binance → Coinbase per the loader chain.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.agents.base import Agent, AssetClass
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy
from src.strategies.ema_ribbon_compression import EmaRibbonCompression
from src.strategies.failed_breakout_crypto import FailedBreakoutCrypto
from src.strategies.funding_rate_divergence import FundingRateDivergence
from src.strategies.ma_pullback_trend_crypto import MaPullbackTrendCrypto

log = logging.getLogger("algo_trader.agents.crypto")

# How many calendar days of intra-day history to fetch per non-daily
# interval. The 4h ribbon-compression strategy needs ~120 days for the
# 55-EMA warmup + compression-window lookback; 180 gives slack.
_INTRADAY_LOOKBACK_DAYS: int = 180


class CryptoAgent(Agent):
    """Container for crypto-class strategies."""

    name = "crypto_agent"
    asset_class = AssetClass.CRYPTO

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        universe: tuple[str, ...] | None = None,
        heat_allocation: float = 0.0,
        bars_loader: Any = None,
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
        # Lazy-bound: in production this is ``src.data.loader.load_crypto_bars``;
        # tests inject a stub that returns canned frames so the unit tests
        # never touch the network.
        self._bars_loader = bars_loader

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        """Evaluate each strategy at its declared ``bar_interval``.

        For strategies with the default interval (``"1d"``), the ``bars``
        dict passed in by the runner is used as-is. For strategies on a
        different interval (e.g. ``"4h"``), the bars are fetched on
        demand via the crypto loader and cached by interval for the
        duration of this eval cycle so multiple 4h strategies share one
        fetch.
        """
        out: list[Signal] = []
        # Pin the default-interval bars in the per-cycle cache so daily
        # strategies still work without re-fetching.
        per_interval: dict[str, dict[str, Any]] = {"1d": dict(bars)}

        for strat in self.strategies:
            interval = getattr(strat, "bar_interval", "1d")
            strat_bars = per_interval.get(interval)
            if strat_bars is None:
                strat_bars = self._fetch_bars_for_interval(interval)
                # Cache even the empty-fetch result so we don't re-attempt
                # the same fetch within this eval cycle.
                per_interval[interval] = strat_bars
            if not strat_bars:
                # Empty fetch (loader unavailable, network down, no symbols
                # in cache yet) — skip the strategy gracefully rather than
                # passing an empty dict that would silently produce zero
                # signals indistinguishably from a real "no setup" outcome.
                log.debug(
                    "crypto_agent: no %s bars available for %s; skipping this cycle",
                    interval, strat.name,
                )
                continue
            out.extend(strat.generate_signals(strat_bars))

        self._last_eval_ts = datetime.now(UTC)
        return out

    def _fetch_bars_for_interval(self, interval: str) -> dict[str, Any]:
        """On-demand fetch of crypto bars at a non-default interval.

        Uses the injected ``bars_loader`` (production: load_crypto_bars,
        tests: a stub). Loader failures are caught and converted to an
        empty dict so :meth:`evaluate` can degrade gracefully — better
        to skip a 4h-strategy on one cycle than fail the whole agent.
        """
        loader = self._bars_loader
        if loader is None:
            try:
                from src.data.loader import load_crypto_bars  # noqa: PLC0415

                loader = load_crypto_bars
            except Exception:
                log.exception("crypto_agent: load_crypto_bars import failed")
                return {}
        end_d = datetime.now(UTC).date()
        start_d = end_d - timedelta(days=_INTRADAY_LOOKBACK_DAYS)
        try:
            return loader(list(self.universe), start_d, end_d, interval=interval)
        except Exception:
            log.exception(
                "crypto_agent: bars fetch failed for interval=%s; skipping",
                interval,
            )
            return {}
