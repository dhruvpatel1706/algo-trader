"""range_shift_pullback - first-pullback continuation after a Donchian range shift.

Power-of-Stocks-derived idea: a "range shift" is a fresh breakout where the
current 20-day Donchian high prints meaningfully above the previous one. The
edge tends to live in the *first* pullback after the shift; later pullbacks
look more like exhaustion than continuation. This strategy buys the first
pullback to the 20 EMA in the new direction.

Long-only per repo policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import adx, atr, ema
from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class RangeShiftPullbackParams:
    # Donchian period for the range definition. Range: 15-30. Breaks: too short
    # makes "shifts" too frequent; too long misses regime changes.
    donchian_period: int = 20

    # Minimum height of the new range above the prior, in ATR units. Range:
    # 0.25-1.0. Breaks: too low confuses noise for shifts; too high misses
    # legitimate continuations.
    shift_atr_threshold: float = 0.5

    # Bars price must hold above the new high before a pullback counts as
    # confirmed. Range: 2-5.
    bars_above_after_shift_min: int = 3

    # Hard cap on bars between the shift breakout and the entry bar. Range:
    # 3-8. Breaks: a longer window catches second pullbacks that historically
    # show poorer continuation.
    max_bars_since_shift: int = 5

    # 20 EMA is the pullback line used by the original Power-of-Stocks rule.
    ema_period: int = 20

    # How close the pullback must come to the EMA, in ATR units. Range:
    # 0.25-1.0.
    pullback_atr_mult: float = 0.5

    # Trend filter so we only buy in active tapes.
    adx_period: int = 14
    adx_min: float = 18.0

    # Stop sits below the EMA. Range: 1.0-2.5 ATR. Engine has no trailing
    # stops yet, so the bracket must be static.
    atr_period: int = 14
    atr_stop_mult: float = 1.5

    # Donchian top is too close after a fresh shift, so target by R-multiple.
    target_r: float = 2.0


class RangeShiftPullback(Strategy):
    """Buy the first pullback to 20 EMA after a Donchian range shift."""

    name = "range_shift_pullback"

    def __init__(self, params: RangeShiftPullbackParams | None = None) -> None:
        self.params = params if params is not None else RangeShiftPullbackParams()

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:  # noqa: PLR0912, PLR0915 — flat per-symbol pipeline mirrors failed_breakout pattern; refactoring into smaller functions would split the early-return guards from their checks
        signals: list[Signal] = []
        p = self.params
        warm_up = max(
            p.donchian_period * 2,
            p.ema_period * 2,
            p.adx_period * 3,
            p.atr_period * 2,
        ) + p.max_bars_since_shift + p.bars_above_after_shift_min + 5

        for sym, df in bars.items():
            if len(df) < warm_up:
                continue

            high, low, close = df["high"], df["low"], df["close"]

            # Donchian high *excluding* current bar (rolling N bars BEFORE i).
            prior_donchian_high = high.rolling(
                p.donchian_period, min_periods=p.donchian_period
            ).max().shift(1)
            ema_ser = ema(close, period=p.ema_period)
            atr_ser = atr(high, low, close, period=p.atr_period)
            adx_ser = adx(high, low, close, period=p.adx_period)

            close_now = float(close.iloc[-1])
            low_now = float(low.iloc[-1])
            ema_now = float(ema_ser.iloc[-1])
            atr_now = float(atr_ser.iloc[-1])
            adx_now = float(adx_ser.iloc[-1])

            if any(pd.isna(v) for v in (ema_now, atr_now, adx_now)):
                continue
            if atr_now <= 0:
                continue

            # Find the most recent shift breakout within the search window.
            # A shift breakout is a bar whose HIGH exceeds the prior N-day
            # Donchian high (excluding itself) by at least
            # shift_atr_threshold * ATR. Walk backward from the latest bar so
            # we get the FIRST (most recent) shift, then verify the pullback
            # has not exceeded max_bars_since_shift.
            shift_idx: int | None = None
            search_start = max(p.donchian_period, len(df) - 1 - p.max_bars_since_shift)
            for i in range(len(df) - 1, search_start - 1, -1):
                cur_bar_high = float(high.iloc[i])
                prev_high = prior_donchian_high.iloc[i]
                atr_at_i = atr_ser.iloc[i]
                if pd.isna(prev_high) or pd.isna(atr_at_i):
                    continue
                if atr_at_i <= 0:
                    continue
                if (cur_bar_high - float(prev_high)) >= p.shift_atr_threshold * float(atr_at_i):
                    shift_idx = i
                    break

            if shift_idx is None:
                continue

            bars_since_shift = (len(df) - 1) - shift_idx
            if bars_since_shift > p.max_bars_since_shift:
                continue

            # Confirm that the close held above the prior range for at least
            # bars_above_after_shift_min consecutive bars beginning at the
            # shift bar. We compare against the prior Donchian high (not the
            # shift bar's own high) so the shift bar's own close counts as
            # "above the prior range".
            prior_range_high = float(prior_donchian_high.iloc[shift_idx])
            held_count = 0
            check_end = min(
                shift_idx + p.bars_above_after_shift_min, len(df)
            )
            for j in range(shift_idx, check_end):
                if float(close.iloc[j]) > prior_range_high:
                    held_count += 1
                else:
                    break
            if held_count < p.bars_above_after_shift_min:
                continue

            # Pullback condition: latest bar's low is within
            # pullback_atr_mult * ATR of the EMA, but close held above EMA.
            pulled_into_ema = low_now <= ema_now + p.pullback_atr_mult * atr_now
            held_ema = close_now >= ema_now
            if not (pulled_into_ema and held_ema):
                continue

            if adx_now < p.adx_min:
                continue

            # Stop = tighter of (low of pullback bar) and (1.5 ATR below EMA).
            stop_low = low_now
            stop_ema = ema_now - p.atr_stop_mult * atr_now
            stop = max(stop_low, stop_ema)
            if stop <= 0 or stop >= close_now:
                continue

            risk = close_now - stop
            if risk <= 0:
                continue
            target = close_now + p.target_r * risk

            signals.append(
                Signal(
                    symbol=sym,
                    side="buy",
                    entry=Decimal(str(round(close_now, 2))),
                    stop=Decimal(str(round(stop, 2))),
                    target=Decimal(str(round(target, 2))),
                    confidence=0.56,
                    strategy_tag=self.name,
                    timestamp=df.index[-1],
                    notes=(
                        f"shift+{bars_since_shift}b EMA={ema_now:.2f} "
                        f"ADX={adx_now:.1f}"
                    ),
                )
            )

        return signals
