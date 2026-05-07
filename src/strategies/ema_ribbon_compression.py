"""ema_ribbon_compression - long-only Fibonacci-EMA ribbon compression breakout.

Origin: Researcher session 2026-05-07 (see
``docs/improvements/strategies/ema_ribbon_compression_breakout.md``). Idea:
Daryl Guppy's Multiple Moving Average system uses a "ribbon" of Fibonacci EMAs
(8, 13, 21, 34, 55). When the ribbon compresses — all five EMAs cluster into a
tight band — the next directional move tends to be impulsive. We buy the
upside breakout from the compression.

The Researcher proposal also mentions a SHORT path. Repo policy is long-only
for v1 (see ``src/risk/limits.py`` and the original failed_breakout module),
so we ship the long path only and leave the short variant as a follow-up.

Entry:
  1. Compression confirmed: for the last `compression_bars` bars,
     max(EMAs) - min(EMAs) < `compression_max_spread_pct` * close.
  2. Breakout: latest close > ribbon_max * (1 + `breakout_pct`).
  3. Trend gate: ADX(period) >= `adx_min` so we don't fade chop.

Exit (rules at signal time; engine has no trailing logic, so static):
  - Stop: ribbon midpoint at entry. Compression collapses on a structure break
    so the midpoint is the natural invalidation level.
  - Target: 3R from entry — compression breakouts tend to run.

Universe is resolved through ``Universe.for_strategy(self.name)`` and is keyed
to ``crypto_majors`` in ``docs/universes.yaml``. The strategy works on equity
bars too if you remap the universe; default per the Researcher spec is crypto
4h bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import adx, ema
from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class EmaRibbonCompressionParams:
    """Tunables for the ribbon-compression breakout. Reasonable ranges in
    the docstring of each field — the defaults match the Researcher's
    proposal verbatim so future backtests can reproduce that spec."""

    # Fibonacci EMA periods. Range: typically 5/8/13/21/34/55. Fewer EMAs
    # blurs the "ribbon" notion; more makes compression too rare.
    ema_periods: tuple[int, ...] = (8, 13, 21, 34, 55)

    # Maximum (ribbon_max - ribbon_min) / close to count as "compressed".
    # Default 0.5%. Range: 0.3-0.8%. Tighter = rarer + stronger setups;
    # looser = more signals but more whipsaw.
    compression_max_spread_pct: float = 0.005

    # How many consecutive bars must satisfy the compression criterion
    # before we count it as confirmed. Default 5. Range: 3-8. Shorter
    # window picks up false starts; longer window misses the move.
    compression_bars: int = 5

    # Breakout buffer: latest close must exceed ribbon_max by this fraction
    # of price. Default 0.8%. Range: 0.5-1.2%. Tighter window catches the
    # break sooner but also catches more shakeouts.
    breakout_pct: float = 0.008

    # Trend filter so we don't fade dead-flat regimes.
    adx_period: int = 14
    adx_min: float = 18.0

    # R-multiple target. Compression breakouts tend to be impulsive so
    # 3R is the Researcher's recommendation; engine has no trailing stop
    # support yet, so this is a static bracket.
    target_r: float = 3.0

    # Per-signal confidence. Independent of the rule trigger; the runtime
    # reasoner can scale this further before the risk gate sees it.
    confidence: float = 0.55


class EmaRibbonCompression(Strategy):
    """Long-only EMA ribbon compression breakout. Crypto-tuned defaults."""

    name = "ema_ribbon_compression"

    def __init__(self, params: EmaRibbonCompressionParams | None = None) -> None:
        self.params = params if params is not None else EmaRibbonCompressionParams()

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []
        p = self.params

        # Warm-up: longest EMA needs at least period bars to be defined,
        # plus the compression-window lookback, plus a buffer.
        warm_up = max(p.ema_periods) * 2 + p.compression_bars + p.adx_period + 5

        for sym, df in bars.items():
            if len(df) < warm_up:
                continue
            sig = self._evaluate_symbol(sym, df, p)
            if sig is not None:
                signals.append(sig)
        return signals

    def _evaluate_symbol(
        self,
        sym: str,
        df: pd.DataFrame,
        p: EmaRibbonCompressionParams,
    ) -> Signal | None:
        """Return a long-entry Signal for ``sym`` or ``None`` when no setup."""
        high, low, close = df["high"], df["low"], df["close"]
        emas = [ema(close, period=period) for period in p.ema_periods]
        adx_ser = adx(high, low, close, period=p.adx_period)

        adx_now = float(adx_ser.iloc[-1])
        close_now = float(close.iloc[-1])
        if pd.isna(adx_now) or adx_now < p.adx_min:
            return None
        if close_now <= 0:
            return None

        # Compression check across the last `compression_bars` bars: every
        # one must have the ribbon spread within compression_max_spread_pct
        # of that bar's close. Even one bar above the threshold breaks the
        # streak — we're testing for a sustained squeeze, not just one bar.
        compressed = self._is_ribbon_compressed(emas, close, p)
        if not compressed:
            return None

        # Breakout: latest close exceeds the ribbon max by breakout_pct of
        # the close. Use the CURRENT bar's ribbon for the threshold so the
        # breakout reads against where the ribbon is now, not where it was
        # during the compression window.
        ribbon_now = [float(e.iloc[-1]) for e in emas]
        if any(pd.isna(v) for v in ribbon_now):
            return None
        ribbon_max = max(ribbon_now)
        ribbon_min = min(ribbon_now)
        ribbon_mid = (ribbon_max + ribbon_min) / 2.0
        breakout_threshold = ribbon_max * (1.0 + p.breakout_pct)

        if close_now <= breakout_threshold:
            return None

        # Stop = ribbon midpoint (structure break invalidates the breakout).
        # Refuse degenerate setups where the stop is at or above the entry
        # — that would be a zero-risk trade, which collapses sizing math.
        stop = ribbon_mid
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
                f"ribbon_max={ribbon_max:.4f} ribbon_min={ribbon_min:.4f} "
                f"ADX={adx_now:.1f}"
            ),
        )

    @staticmethod
    def _is_ribbon_compressed(
        emas: list[pd.Series],
        close: pd.Series,
        p: EmaRibbonCompressionParams,
    ) -> bool:
        """True iff every bar in the last ``compression_bars`` window has
        the ribbon spread tighter than ``compression_max_spread_pct *
        close``. NaN in any EMA at any required bar disqualifies — we'd
        rather miss a setup than fire one off partial data."""
        n_required = p.compression_bars
        if any(len(e) < n_required for e in emas):
            return False
        for i in range(-n_required, 0):
            bar_close = float(close.iloc[i])
            if bar_close <= 0:
                return False
            spread_threshold = p.compression_max_spread_pct * bar_close
            ribbon_at_i = [float(e.iloc[i]) for e in emas]
            if any(pd.isna(v) for v in ribbon_at_i):
                return False
            spread = max(ribbon_at_i) - min(ribbon_at_i)
            if spread >= spread_threshold:
                return False
        return True


def _to_decimal(value: float) -> Decimal:
    """Round to 4 decimal places before stringifying. Crypto majors trade
    sub-dollar (DOGE, GRT) so 2dp like the equity strategies would lose
    precision; 4dp covers everything in our universe without pretending
    to track 8-decimal satoshi precision the broker won't honour."""
    return Decimal(f"{value:.4f}")
