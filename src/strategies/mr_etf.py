"""mr_etf — Bollinger + RSI(2) mean-reversion on SPY/QQQ, ADX-gated.

Thesis: in low-trend (ADX<20) regimes, highly liquid index ETFs that close at
or below the lower Bollinger Band with RSI(2)<10 tend to mean-revert toward the
20-period SMA over 1-5 trading days. Exit on touch of the middle band; stop is
2*ATR(14) below entry to bound the loss.

Why this might work
===================
- Index ETFs have no idiosyncratic news risk to extreme degree.
- Short-term mean reversion is well-documented on US equity indices in
  low-trend regimes (Connors / Larry Connors RSI-2 literature).
- The ADX gate filters out trending tapes where mean reversion bleeds.

When this fails (be honest)
===========================
- Regime shift mid-trade: ADX rising above 20 → mean reversion stops working.
- Vol shocks (VIX>30): ATR widens, the stop is far away, the bounce never comes.
- Gap-down opens: signal triggered on close, but next-day open gaps below stop;
  fill drifts well below the stop, costing more than 2*ATR.
- Trend persistence: in a true downtrend, "oversold" stays oversold.

Past metrics on free-tier daily SPY/QQQ data are typically modest:
  Sharpe 0.4-0.8, max DD 15-25%, win rate 55-65%, profit factor 1.1-1.4.
Numbers are not a forward-looking promise. See backtests/<ts>/metrics.json
for the most recent run on your machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import adx, atr, bollinger_bands, rsi
from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class MrParams:
    """Mean-reversion parameters; each carries its rationale + typical range + failure mode."""

    # 20d SMA = regression mean for daily index ETFs. Range: 14-30. Breaks: too
    # short = noise; too long = lags too far for short-horizon mean reversion.
    bb_period: int = 20
    bb_std: float = 2.0

    # RSI(2) — Connors-style hyper-responsive oversold detector. Range: 2-4.
    # Threshold 10 is the classic Connors trigger. Breaks: longer RSI smooths
    # the signal away.
    rsi_period: int = 2
    rsi_oversold: float = 10.0

    # ADX<20 = low-trend (range/chop) regime. Range: 15-25. Breaks: a higher
    # threshold lets in trending tape where mean reversion bleeds.
    adx_period: int = 14
    adx_max: float = 20.0

    # ATR(14) for vol-adapted stops. 2x ATR balances room-to-breathe vs.
    # capping the loss. Range: 1.5-3.0. Breaks: very low ATR → tight stop → noise.
    atr_period: int = 14
    atr_stop_mult: float = 2.0


class MrEtf(Strategy):
    """Bollinger(20,2) + RSI(2)<10 mean-reversion on SPY/QQQ, gated by ADX(14)<20."""

    name = "mr_etf"

    def __init__(self, params: MrParams | None = None) -> None:
        self.params: MrParams = params if params is not None else MrParams()

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params
        # ta's ADX computation needs roughly 2x the window of valid bars to bootstrap.
        # Bump well past that so we don't hand it slices that crash internally.
        warm_up = max(p.bb_period * 2, p.adx_period * 3, p.atr_period * 2) + 10
        for sym, df in bars.items():
            if len(df) < warm_up:
                continue
            close, high, low = df["close"], df["high"], df["low"]

            bb = bollinger_bands(close, period=p.bb_period, std=p.bb_std)
            rsi_ser = rsi(close, period=p.rsi_period)
            adx_ser = adx(high, low, close, period=p.adx_period)
            atr_ser = atr(high, low, close, period=p.atr_period)

            close_now = float(close.iloc[-1])
            bb_lower = float(bb["bb_lower"].iloc[-1])
            bb_mid = float(bb["bb_mid"].iloc[-1])
            rsi_now = float(rsi_ser.iloc[-1])
            adx_now = float(adx_ser.iloc[-1])
            atr_now = float(atr_ser.iloc[-1])

            if any(pd.isna(v) for v in (bb_lower, bb_mid, rsi_now, adx_now, atr_now)):
                continue
            if atr_now <= 0:
                continue

            in_oversold = close_now <= bb_lower and rsi_now < p.rsi_oversold
            in_low_trend = adx_now < p.adx_max
            if not (in_oversold and in_low_trend):
                continue

            stop = close_now - p.atr_stop_mult * atr_now
            if stop <= 0 or stop >= close_now:
                continue

            signals.append(
                Signal(
                    symbol=sym,
                    side="buy",
                    entry=Decimal(str(round(close_now, 2))),
                    stop=Decimal(str(round(stop, 2))),
                    target=Decimal(str(round(bb_mid, 2))),
                    confidence=0.6,
                    strategy_tag=self.name,
                    timestamp=df.index[-1],
                    notes=f"RSI2={rsi_now:.1f} ADX={adx_now:.1f}",
                )
            )
        return signals
