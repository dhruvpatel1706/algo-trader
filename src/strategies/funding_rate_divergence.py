"""funding_rate_divergence — long-only funding-rate mean-reversion (crypto).

Origin: Researcher session 2026-05-07 (see
``docs/improvements/strategies/funding_rate_divergence.md``). Idea: when
crypto perpetual funding rates flip sharply negative, shorts are crowded
and over-paying to maintain their positions; the next bounce tends to be
swift. Pair the funding extreme with an oversold RSI and proximity to the
lower Bollinger band to filter out garden-variety dips.

The Researcher proposal also describes a SHORT path (positive funding
extreme + RSI > 65 + price near upper BB). Repo policy is long-only for
v1, so we ship the long path only.

Entry (long):
  1. Funding rate at most recent print is below ``funding_threshold``
     (default -0.0003 = -0.03%/8h, ≈ -33%/yr — a meaningful crowd).
  2. RSI(14) < ``rsi_max`` (default 35) — we're already oversold.
  3. close <= bb_lower * (1 + ``bb_buffer_pct``) — within 1.5% above the
     lower BB. We're not chasing; we're catching a coiled spring.

Exit (static; engine has no trailing logic):
  - Stop: ``bb_lower`` from the entry bar (structure break = setup
    invalidated).
  - Target: 2R from entry — ``2 * (entry - stop)`` above the close.

Funding data is NOT in the standard `bars` dict. The strategy accepts a
``funding_fetcher`` callable in its constructor; default is
``src.data.funding.fetch_funding_rate`` so production code Just Works.
Tests inject a mock to keep the strategy pure-by-injection. Funding is
re-fetched on every signal eval but the underlying ``funding.py`` module
hits 8h-cadence venues, so the practical query rate is governed by how
often the engine calls us (5 min for crypto_agent), not by funding prints.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd

from src.data.funding import fetch_funding_rate
from src.data.universe import Universe
from src.signals.indicators import bollinger_bands, rsi
from src.strategies.base import Signal, Strategy

log = logging.getLogger(__name__)

# Type alias for the funding fetcher dependency. Same signature as
# src.data.funding.fetch_funding_rate so the default is a drop-in pass.
FundingFetcher = Callable[[str, date, date], pd.DataFrame]


@dataclass(frozen=True, slots=True)
class FundingRateDivergenceParams:
    """Tunables for the long path. Defaults match the Researcher's
    proposal verbatim so backtests stay reproducible."""

    # Entry threshold on the most recent 8h funding print. NEGATIVE values
    # indicate shorts paying longs — i.e. shorts crowded. Default -0.0003
    # = -0.03%/8h ≈ -33% APR, which is the lower edge of "actually
    # crowded" on BTC/ETH per the proposal's literature review. Range:
    # -0.0001 (loose, more signals) to -0.001 (tight, rarer).
    funding_threshold: float = -0.0003

    # RSI ceiling for entry. Default 35; the oversold band most crypto
    # mean-reversion literature treats as actionable.
    rsi_period: int = 14
    rsi_max: float = 35.0

    # Bollinger band geometry + how close to the lower band counts as
    # "near". Default 1.5% above bb_lower.
    bb_period: int = 20
    bb_std: float = 2.0
    bb_buffer_pct: float = 0.015

    # Profit-target multiple of risk. Proposal says 2R. Range: 1.5-2.5R.
    target_r: float = 2.0

    # Funding lookback for the fetcher: how many days of history we ask
    # for. Need at least one print, so default to 7 days for safety
    # against weekend/outage gaps.
    funding_lookback_days: int = 7

    # Per-signal confidence; reasoner can scale further before risk gate.
    confidence: float = 0.55


class FundingRateDivergence(Strategy):
    """Long-only funding-rate divergence mean reversion. Crypto-tuned."""

    name = "funding_rate_divergence"

    def __init__(
        self,
        params: FundingRateDivergenceParams | None = None,
        *,
        funding_fetcher: FundingFetcher | None = None,
    ) -> None:
        self.params = params if params is not None else FundingRateDivergenceParams()
        # Default to the production fetcher; tests inject a mock.
        self._funding_fetcher: FundingFetcher = funding_fetcher or fetch_funding_rate

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params

        # Warm-up: BB and RSI both want at least their lookback window.
        warm_up = max(p.bb_period, p.rsi_period) + 5

        # Funding lookback (calendar days, not bars).
        as_of = datetime.now(UTC).date()
        funding_start = as_of - timedelta(days=p.funding_lookback_days)
        funding_end = as_of + timedelta(days=1)

        for sym, df in bars.items():
            if len(df) < warm_up:
                continue
            sig = self._evaluate_symbol(sym, df, p, funding_start, funding_end)
            if sig is not None:
                signals.append(sig)
        return signals

    def _evaluate_symbol(
        self,
        sym: str,
        df: pd.DataFrame,
        p: FundingRateDivergenceParams,
        funding_start: date,
        funding_end: date,
    ) -> Signal | None:
        close = df["close"]
        close_now = float(close.iloc[-1])
        if close_now <= 0:
            return None

        # Indicator gates (cheap; check before the funding fetch which is
        # potentially network I/O).
        rsi_ser = rsi(close, period=p.rsi_period)
        rsi_now = float(rsi_ser.iloc[-1])
        if pd.isna(rsi_now) or rsi_now >= p.rsi_max:
            return None

        bb = bollinger_bands(close, period=p.bb_period, std=p.bb_std)
        bb_lower = float(bb["bb_lower"].iloc[-1])
        if pd.isna(bb_lower) or bb_lower <= 0:
            return None
        # Within bb_buffer_pct of the lower band: equivalent to
        # close <= bb_lower * (1 + bb_buffer_pct).
        if close_now > bb_lower * (1.0 + p.bb_buffer_pct):
            return None

        # Now the funding gate.
        try:
            funding_df = self._funding_fetcher(sym, funding_start, funding_end)
        except Exception:
            # Fetcher errors must not halt other symbols. Network blips,
            # parse errors, geo-blocks all flow through here.
            log.warning("funding fetch failed for %s; skipping", sym, exc_info=True)
            return None
        if funding_df is None or funding_df.empty:
            return None
        if "funding_rate" not in funding_df.columns:
            return None
        latest_funding = float(funding_df["funding_rate"].iloc[-1])
        if pd.isna(latest_funding) or latest_funding >= p.funding_threshold:
            return None

        # Stop = bb_lower (entry-bar level). Refuse degenerate setups
        # where the stop is at or above entry; risk math collapses.
        stop = bb_lower
        if stop <= 0 or stop >= close_now:
            return None

        risk = close_now - stop
        if risk <= 0:
            return None
        target = close_now + p.target_r * risk

        return Signal(
            symbol=sym,
            side="buy",
            entry=_to_decimal(close_now),
            stop=_to_decimal(stop),
            target=_to_decimal(target),
            confidence=p.confidence,
            strategy_tag=self.name,
            timestamp=df.index[-1],
            notes=(
                f"funding={latest_funding:.6f} rsi={rsi_now:.1f} "
                f"bb_lower={bb_lower:.4f}"
            ),
        )


def _to_decimal(value: float) -> Decimal:
    """4dp matches the rest of the crypto strategies (DOGE-grade precision
    without pretending to track satoshi-level moves the broker won't
    honour)."""
    return Decimal(f"{value:.4f}")
