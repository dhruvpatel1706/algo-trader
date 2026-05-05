"""ma_pullback_trend - 20/200 moving-average trend pullback.

This is the daily-bar version of the 20/200 MA trend-capture idea: trade only
when the medium trend is rising, price is above the long-term regime filter, and
the latest bar pulls back into the 20 SMA without breaking the structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import atr, sma
from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class MaPullbackTrendParams:
    # 20 SMA as the trend-pullback line. Range: 10-30.
    fast_period: int = 20

    # 200 SMA as the broad risk-on/risk-off filter. Range: 100-250.
    slow_period: int = 200

    # Require the fast MA to rise over this many bars. Range: 3-10.
    slope_lookback: int = 5

    # Pullback must come close to the fast SMA. Range: 0.25-1.0 ATR.
    pullback_atr_mult: float = 0.5

    # Stop below the fast SMA. Range: 1.0-3.0 ATR.
    atr_period: int = 14
    atr_stop_mult: float = 2.0

    # Backtest engine has fixed targets/stops, not trailing exits yet. Use a
    # conservative R target until trailing-stop support exists.
    target_r: float = 3.0


class MaPullbackTrend(Strategy):
    """Long pullbacks to rising 20 SMA while price is above 200 SMA."""

    name = "ma_pullback_trend"

    def __init__(self, params: MaPullbackTrendParams | None = None) -> None:
        self.params = params if params is not None else MaPullbackTrendParams()

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params
        warm_up = max(p.slow_period + p.slope_lookback, p.fast_period, p.atr_period * 2) + 5

        for sym, df in bars.items():
            if len(df) < warm_up:
                continue

            high, low, close = df["high"], df["low"], df["close"]
            fast = sma(close, period=p.fast_period)
            slow = sma(close, period=p.slow_period)
            atr_ser = atr(high, low, close, period=p.atr_period)

            close_now = float(close.iloc[-1])
            low_now = float(low.iloc[-1])
            fast_now = float(fast.iloc[-1])
            fast_prev = float(fast.iloc[-1 - p.slope_lookback])
            slow_now = float(slow.iloc[-1])
            atr_now = float(atr_ser.iloc[-1])

            if any(pd.isna(v) for v in (fast_now, fast_prev, slow_now, atr_now)):
                continue
            if atr_now <= 0:
                continue

            trend_ok = fast_now > fast_prev and close_now > slow_now
            pulled_into_fast = low_now <= fast_now + p.pullback_atr_mult * atr_now
            held_fast = close_now >= fast_now
            if not (trend_ok and pulled_into_fast and held_fast):
                continue

            stop = fast_now - p.atr_stop_mult * atr_now
            risk = close_now - stop
            target = close_now + p.target_r * risk
            if stop <= 0 or risk <= 0:
                continue

            signals.append(
                Signal(
                    symbol=sym,
                    side="buy",
                    entry=Decimal(str(round(close_now, 2))),
                    stop=Decimal(str(round(stop, 2))),
                    target=Decimal(str(round(target, 2))),
                    confidence=0.57,
                    strategy_tag=self.name,
                    timestamp=df.index[-1],
                    notes=f"20SMA={fast_now:.2f} 200SMA={slow_now:.2f}",
                )
            )

        return signals
