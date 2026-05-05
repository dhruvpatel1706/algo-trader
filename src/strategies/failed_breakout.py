"""failed_breakout - daily failed-breakdown fade.

This is the codeable, long-only version of the Power of Stocks / ORB-failure
idea: price pierces an important low, fails to follow through, and closes back
inside the range. The strategy only buys failed downside breaks because the v1
engine is long-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import adx, atr, williams_vix_fix
from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class FailedBreakoutParams:
    # Donchian boundary: prior N-day low/high. Range: 10-55. Breaks: too short
    # catches noise; too long produces too few trades.
    channel_period: int = 20

    # ADX>20 allows only active tapes where stop-runs and rejections are more
    # meaningful. Range: 15-30. Breaks: too high means very late exhaustion.
    adx_period: int = 14
    adx_min: float = 20.0

    # WVF spike threshold uses a rolling quantile so it adapts across assets.
    wvf_period: int = 22
    wvf_quantile_lookback: int = 126
    wvf_min_quantile: float = 0.80

    # Stop sits beyond the rejected wick. Range: 0.5-2.0 ATR.
    atr_period: int = 14
    atr_stop_mult: float = 1.0

    # Avoid entries where the opposite side of the range is not worth the risk.
    min_reward_r: float = 1.2


class FailedBreakout(Strategy):
    """Buy failed breakdowns back toward the opposite Donchian range edge."""

    name = "failed_breakout"

    def __init__(self, params: FailedBreakoutParams | None = None) -> None:
        self.params = params if params is not None else FailedBreakoutParams()

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params
        warm_up = max(
            p.channel_period,
            p.adx_period * 3,
            p.atr_period * 2,
            p.wvf_period + p.wvf_quantile_lookback,
        ) + 5

        for sym, df in bars.items():
            if len(df) < warm_up:
                continue
            high, low, close = df["high"], df["low"], df["close"]

            channel_low = (
                low.rolling(p.channel_period, min_periods=p.channel_period).min().shift(1)
            )
            channel_high = (
                high.rolling(p.channel_period, min_periods=p.channel_period).max().shift(1)
            )
            adx_ser = adx(high, low, close, period=p.adx_period)
            atr_ser = atr(high, low, close, period=p.atr_period)
            wvf = williams_vix_fix(close, low=low, period=p.wvf_period)
            wvf_gate = wvf.rolling(
                p.wvf_quantile_lookback, min_periods=p.wvf_quantile_lookback
            ).quantile(p.wvf_min_quantile)

            close_now = float(close.iloc[-1])
            low_now = float(low.iloc[-1])
            range_low = float(channel_low.iloc[-1])
            range_high = float(channel_high.iloc[-1])
            adx_now = float(adx_ser.iloc[-1])
            atr_now = float(atr_ser.iloc[-1])
            wvf_now = float(wvf.iloc[-1])
            wvf_threshold = float(wvf_gate.iloc[-1])

            if any(
                pd.isna(v)
                for v in (range_low, range_high, adx_now, atr_now, wvf_now, wvf_threshold)
            ):
                continue
            if atr_now <= 0 or range_high <= close_now:
                continue

            pierced_low = low_now < range_low
            closed_back_inside = close_now > range_low
            exhausted = wvf_now >= wvf_threshold
            active_tape = adx_now >= p.adx_min
            if not (pierced_low and closed_back_inside and exhausted and active_tape):
                continue

            stop = low_now - p.atr_stop_mult * atr_now
            target = range_high
            risk = close_now - stop
            reward = target - close_now
            if stop <= 0 or risk <= 0 or reward / risk < p.min_reward_r:
                continue

            signals.append(
                Signal(
                    symbol=sym,
                    side="buy",
                    entry=Decimal(str(round(close_now, 2))),
                    stop=Decimal(str(round(stop, 2))),
                    target=Decimal(str(round(target, 2))),
                    confidence=0.58,
                    strategy_tag=self.name,
                    timestamp=df.index[-1],
                    notes=f"failed_breakdown ADX={adx_now:.1f} WVF={wvf_now:.1f}",
                )
            )

        return signals
