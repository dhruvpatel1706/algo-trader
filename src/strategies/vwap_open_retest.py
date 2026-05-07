"""vwap_open_retest — intraday VWAP retest long on 5-minute equity bars.

Thesis
======
Large-cap equities frequently open above the intraday VWAP in early-session
momentum, then retrace into VWAP as the initial burst fades. When that
retest arrives with neutral RSI (40-60 — not overbought or oversold) and
healthy volume, the VWAP level acts as dynamic support and price tends to
bounce back toward the opening range high.

Signal conditions (all must be true)
=====================================
1. Time gate: 09:45-10:30 US/Eastern (skip the first 15 min of chaotic price
   discovery; cut off at 10:30 before the mid-morning drift regime takes over).
2. VWAP retest: the latest close is within +-0.15% of the intraday VWAP
   *and* the first bar of the day closed above VWAP (confirming the open
   was above VWAP so this is a retest from above, not an initial tag).
3. RSI gate: RSI(14) on 5-minute bars is between 40 and 60 -- a neutral read
   that rules out parabolic momentum chases and deeply oversold washouts.
4. Volume confirmation: volume on the retest bar is >= 0.8x the 20-bar
   average volume. Thin-volume retests are untrustworthy.

Confidence scoring
==================
- Base: 0.60
- +0.10 if RSI < 50 (momentum leaning lower -- stronger bounce setup)
- +0.10 if volume > 1.2x the 20-bar average (above-average participation)

Exit parameters
===============
- Stop: 0.5% below the VWAP value at the time of entry (VWAP break = thesis
  invalidated).
- Target: None (engine manages exits; no fixed R-target for intraday VWAP
  trades — the exit is typically the session high or a time-based close).

Universe: SPY, QQQ, AAPL, MSFT, NVDA, META, GOOGL, AMZN — the eight most
liquid US large-caps where VWAP is widely watched by institutional desks.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.data.universe import Universe
from src.signals.indicators import rsi, vwap
from src.strategies.base import Signal, Strategy

# Minimum number of 5-minute bars needed before the strategy can fire.
# RSI(14) needs ~28 bars to converge; VWAP just needs bars since open;
# 20-bar volume average needs 20 bars. Use 30 as the practical warm-up.
_MIN_BARS: int = 30

# How close to VWAP counts as a "retest" (fraction of price).
_RETEST_BAND: float = 0.0015  # 0.15 %

# Stop distance below VWAP as a fraction of VWAP.
_STOP_BAND: float = 0.005  # 0.50 %

# RSI bounds for a valid neutral-momentum retest.
_RSI_LOW: float = 40.0
_RSI_HIGH: float = 60.0

# Volume ratio floor: retest bar must exceed this fraction of 20-bar avg.
_VOL_MIN_RATIO: float = 0.8

# Time window — only generate signals inside this half-open interval.
_WINDOW_START_H, _WINDOW_START_M = 9, 45
_WINDOW_END_H, _WINDOW_END_M = 10, 30


@dataclass(frozen=True, slots=True)
class VwapOpenRetestParams:
    """All tunables in one frozen dataclass. The defaults match the
    module-level constants so strategy behaviour is deterministic."""

    retest_band: float = _RETEST_BAND
    stop_band: float = _STOP_BAND
    rsi_period: int = 14
    rsi_low: float = _RSI_LOW
    rsi_high: float = _RSI_HIGH
    vol_avg_period: int = 20
    vol_min_ratio: float = _VOL_MIN_RATIO
    # Confidence bonuses applied on top of the 0.60 base.
    confidence_base: float = 0.60
    confidence_rsi_bonus: float = 0.10   # if RSI < 50
    confidence_vol_bonus: float = 0.10   # if vol > 1.2x avg


class VwapOpenRetest(Strategy):
    """Long-only intraday VWAP retest on 5-minute equity bars.

    Fires between 09:45 and 10:30 US/Eastern. One signal per symbol per
    call — the caller is responsible for deduplification across bar snapshots
    intraday.
    """

    name = "vwap_open_retest"

    def __init__(self, params: VwapOpenRetestParams | None = None) -> None:
        self.params: VwapOpenRetestParams = (
            params if params is not None else VwapOpenRetestParams()
        )

    def universe(self) -> tuple[str, ...]:
        return Universe.for_strategy(self.name)

    def generate_signals(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        """Evaluate each symbol and return qualifying long signals.

        ``bars`` is a dict mapping ticker -> DataFrame with columns
        ``open``, ``high``, ``low``, ``close``, ``volume`` and a
        timezone-aware DatetimeIndex (5-minute bars, US/Eastern preferred).
        """
        now = pd.Timestamp.now(tz="US/Eastern")
        if not _in_window(now):
            return []

        signals: list[Signal] = []
        p = self.params

        for sym, df in bars.items():
            sig = _evaluate_symbol(sym, df, p, now)
            if sig is not None:
                signals.append(sig)

        return signals


def scan(bars: dict[str, pd.DataFrame]) -> list[Signal]:
    """Module-level entry point — thin wrapper around VwapOpenRetest.

    Matches the ``scan(bars) -> list[Signal]`` convention used by the
    trade engine when calling strategy modules directly.
    """
    return VwapOpenRetest().generate_signals(bars)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _in_window(ts: pd.Timestamp) -> bool:
    """True iff ``ts`` is within the 09:45-10:30 US/Eastern trading window."""
    h, m = ts.hour, ts.minute
    after_start = (h, m) >= (_WINDOW_START_H, _WINDOW_START_M)
    before_end = (h, m) < (_WINDOW_END_H, _WINDOW_END_M)
    return after_start and before_end


def _opened_above_vwap(
    close: pd.Series,
    vwap_ser: pd.Series,
    df_index: pd.DatetimeIndex,
    today: object,
) -> bool:
    """Return True iff the first bar of *today* closed above VWAP.

    Localises the index to US/Eastern if it is tz-naive so that ``.date``
    comparisons work regardless of how the caller constructed the DataFrame.
    """
    idx = df_index
    if idx.tz is None:
        idx = idx.tz_localize("US/Eastern")
    today_mask = idx.date == today
    if not today_mask.any():
        return False
    first_pos = int(today_mask.argmax())
    first_close = float(close.iloc[first_pos])
    first_vwap = float(vwap_ser.iloc[first_pos])
    if pd.isna(first_vwap) or first_vwap <= 0:
        return False
    return first_close > first_vwap


def _compute_vol_ratio(volume: pd.Series, avg_period: int) -> float:
    """Return current-bar volume divided by the trailing avg of prior bars."""
    avg_vol = float(volume.iloc[-(avg_period + 1) : -1].mean())
    if avg_vol <= 0 or pd.isna(avg_vol):
        return 0.0
    return float(volume.iloc[-1]) / avg_vol


def _score_confidence(
    rsi_now: float,
    vol_ratio: float,
    p: VwapOpenRetestParams,
) -> float:
    """Return confidence capped at 1.0 with applicable bonuses applied."""
    confidence = p.confidence_base
    if rsi_now < 50.0:
        confidence += p.confidence_rsi_bonus
    if vol_ratio > 1.2:
        confidence += p.confidence_vol_bonus
    return min(confidence, 1.0)


def _evaluate_symbol(
    sym: str,
    df: pd.DataFrame,
    p: VwapOpenRetestParams,
    now: pd.Timestamp,
) -> Signal | None:
    """Return a Signal for ``sym`` if all conditions are met, else None."""
    if len(df) < _MIN_BARS:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # VWAP resets each calendar day (groupby in the indicator).
    vwap_ser = vwap(high, low, close, volume)
    vwap_now = float(vwap_ser.iloc[-1])
    if pd.isna(vwap_now) or vwap_now <= 0:
        return None

    close_now = float(close.iloc[-1])
    if close_now <= 0:
        return None

    # Confirm the session opened above VWAP (retest from above, not below).
    if not _opened_above_vwap(close, vwap_ser, df.index, now.date()):
        return None

    # Retest proximity: price within retest_band of VWAP.
    distance_pct = abs(close_now - vwap_now) / vwap_now
    if distance_pct > p.retest_band:
        return None

    # RSI gate: neutral momentum -- not overbought or oversold.
    rsi_ser = rsi(close, period=p.rsi_period)
    rsi_now = float(rsi_ser.iloc[-1])
    if pd.isna(rsi_now) or not (p.rsi_low <= rsi_now <= p.rsi_high):
        return None

    # Volume gate: retest bar must have meaningful participation.
    vol_ratio = _compute_vol_ratio(volume, p.vol_avg_period)
    if vol_ratio < p.vol_min_ratio:
        return None

    confidence = _score_confidence(rsi_now, vol_ratio, p)

    # Stop: stop_band below VWAP at entry.
    stop_price = vwap_now * (1.0 - p.stop_band)
    if stop_price <= 0 or stop_price >= close_now:
        return None

    return Signal(
        symbol=sym,
        side="buy",
        entry=Decimal(str(round(close_now, 2))),
        stop=Decimal(str(round(stop_price, 2))),
        target=None,
        confidence=round(confidence, 2),
        strategy_tag=VwapOpenRetest.name,
        timestamp=df.index[-1],
        asset_class="equity",
        notes=(
            f"VWAP={vwap_now:.4f} RSI={rsi_now:.1f} "
            f"vol_ratio={vol_ratio:.2f} dist_pct={distance_pct * 100:.3f}%"
        ),
    )
