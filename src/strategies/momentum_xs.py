"""momentum_xs - cross-sectional 12-1 momentum (Asness/Moskowitz factor).

Standard academic formulation: rank a fixed universe by trailing 12-month return
excluding the most recent ~21 trading days (the "skip-most-recent-month" guard
against 1-month reversal). Top decile gets long signals. Rebalance is monthly:
the strategy only fires on the first trading day of each month.

This is a long-only equity factor. The engine is long-only and the universe is
`large_caps_50`, so the long leg of the classic long/short factor is what we
trade. Stops use ATR sizing (wider than swing strategies because the holding
horizon is monthly), and targets are an R-multiple of the stop distance.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import atr
from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class MomentumXsParams:
    # Trailing months in the momentum window. Standard 12-1 uses 12 months
    # (252 trading days) and skips the most recent ~21 trading days.
    lookback_months: int = 12

    # Skip the most recent ~21 trading days to dodge 1-month reversal.
    skip_most_recent_days: int = 21

    # Top decile (10%) of the cross-section gets a long signal.
    top_decile_frac: float = 0.10

    # Stop sizing. Monthly horizon -> wider stop than swing strategies.
    atr_period: int = 14
    atr_stop_mult: float = 3.0

    # 4R target. The factor's edge is durable monthly drift, not a quick rip.
    target_r: float = 4.0


# One trading month ~= 21 bars. Used to convert lookback_months into bars.
_BARS_PER_MONTH = 21


def _is_month_boundary(index: pd.DatetimeIndex) -> bool:
    """True if the latest bar is the first trading bar of its month.

    Cleaner than tracking `smallest day seen`: just compare the month of the
    latest bar to the month of the prior bar. With a single bar we cannot
    determine a boundary, so return False to avoid a spurious first-bar fire.
    """
    if len(index) < 2:
        return False
    return index[-1].month != index[-2].month


class MomentumXs(Strategy):
    """Cross-sectional 12-1 momentum, long the top decile, monthly rebalance."""

    name = "momentum_xs"

    def __init__(self, params: MomentumXsParams | None = None) -> None:
        self.params = params if params is not None else MomentumXsParams()

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params

        if not bars:
            return signals

        # Rebalance gate: only fire on the first trading day of a new month.
        # All symbols share the same trading calendar in this engine, so checking
        # any one symbol is sufficient. Pick the longest series for safety.
        ref_df = max(bars.values(), key=len)
        if not _is_month_boundary(ref_df.index):
            return signals

        # Bars needed to compute r_12_1: skip + lookback*21 + 1 (the index at
        # `-skip-lookback*21-1` must exist) plus a few warmup bars for ATR.
        lookback_bars = p.lookback_months * _BARS_PER_MONTH
        warm_up = p.skip_most_recent_days + lookback_bars + p.atr_period * 2 + 5

        # Step 1: compute r_12_1 for every symbol with enough history.
        scored: list[tuple[str, float, pd.DataFrame]] = []
        for sym, df in bars.items():
            if len(df) < warm_up:
                continue
            close = df["close"]
            recent = float(close.iloc[-p.skip_most_recent_days - 1])
            past = float(close.iloc[-p.skip_most_recent_days - lookback_bars - 1])
            if past <= 0 or pd.isna(recent) or pd.isna(past):
                continue
            r_12_1 = recent / past - 1.0
            scored.append((sym, r_12_1, df))

        if not scored:
            return signals

        # Step 2: rank by r_12_1 descending (best momentum first).
        scored.sort(key=lambda t: t[1], reverse=True)

        # Step 3: pick top decile. Floor to 1 so a tiny universe still emits.
        n_top = max(1, round(len(scored) * p.top_decile_frac))
        top = scored[:n_top]

        # Step 4: build a Signal for each top-decile name.
        for rank_idx, (sym, r_12_1, df) in enumerate(top):
            high, low, close = df["high"], df["low"], df["close"]
            atr_ser = atr(high, low, close, period=p.atr_period)

            close_now = float(close.iloc[-1])
            atr_now = float(atr_ser.iloc[-1])
            if pd.isna(close_now) or pd.isna(atr_now) or atr_now <= 0:
                continue

            stop = close_now - p.atr_stop_mult * atr_now
            risk = close_now - stop
            target = close_now + p.target_r * risk
            if stop <= 0 or risk <= 0:
                continue

            # Confidence: 0.55-0.70 across the decile, decaying with rank.
            # rank 0 -> 0.70, last rank in decile -> 0.55. Scale linearly.
            if n_top == 1:
                confidence = 0.65
            else:
                confidence = 0.70 - (0.15 * rank_idx / (n_top - 1))

            signals.append(
                Signal(
                    symbol=sym,
                    side="buy",
                    entry=Decimal(str(round(close_now, 2))),
                    stop=Decimal(str(round(stop, 2))),
                    target=Decimal(str(round(target, 2))),
                    confidence=round(confidence, 4),
                    strategy_tag=self.name,
                    timestamp=df.index[-1],
                    notes=f"r_12_1={r_12_1:+.2%} rank={rank_idx + 1}/{n_top}",
                )
            )

        return signals
