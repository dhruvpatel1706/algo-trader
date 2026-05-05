"""range_shift_pullback strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.range_shift_pullback import (
    RangeShiftPullback,
    RangeShiftPullbackParams,
)


def _shift_then_pullback(
    n: int = 220,
    pullback_offset: int = 4,
    p_donchian: int = 20,
) -> pd.DataFrame:
    """Synthetic series: long uptrend, sharp shift, then pullback to 20 EMA.

    `pullback_offset` controls how many bars after the shift breakout the
    pullback bar lands at the end of the series.
    """
    idx = pd.date_range("2024-01-02", periods=n, freq="B")

    # Long, gentle uptrend so 20 EMA is rising and ADX has plenty to chew on.
    close = pd.Series(100.0 + np.arange(n) * 0.4, index=idx, dtype=float)
    high = close + 0.6
    low = close - 0.6

    # The "shift bar": pop well above the prior 20-day Donchian high. The
    # bar's CLOSE needs to be above the new Donchian high (which equals the
    # shift bar's high), so the high is set just at the close level and the
    # close has to clear the prior Donchian high comfortably.
    shift_bar = n - 1 - pullback_offset
    prior_high = float(high.iloc[shift_bar - p_donchian:shift_bar].max())
    shift_close = prior_high + 12.0
    close.iloc[shift_bar] = shift_close
    high.iloc[shift_bar] = shift_close  # high == close so new Donchian high = close
    low.iloc[shift_bar] = shift_close - 0.6

    # Hold above the new high for several bars so the
    # bars_above_after_shift_min check passes.
    new_donchian_high = shift_close
    for k in range(shift_bar + 1, min(shift_bar + 5, n - 1)):
        c = new_donchian_high + 0.4 + (k - shift_bar) * 0.1
        close.iloc[k] = c
        high.iloc[k] = c + 0.2
        low.iloc[k] = c - 0.4

    # Drag the LAST bar back so its close lands right at/above the 20 EMA but
    # its low dips into the EMA + pullback band. We compute the actual EMA so
    # we can land the close JUST above it (pullback + held above EMA).
    closes_so_far = close.iloc[:-1].copy()
    # Replicate the indicator's ewm(span=20, adjust=False) on closes-so-far,
    # then use the second-to-last EMA value as the bar-N reference. The bar-N
    # EMA itself depends on close[-1], so we iterate once to converge.
    target_ema_n_minus_1 = closes_so_far.ewm(span=20, adjust=False).mean().iloc[-1]
    # Recurrence: last_ema = alpha * close_last + (1-alpha) * prev_ema.
    # We want last_close >= last_ema, so pick close_last = prev_ema + small_offset:
    # then last_close - last_ema = (1 - alpha) * offset > 0 for any positive offset.
    last_close = float(target_ema_n_minus_1) + 0.50
    close.iloc[-1] = last_close
    high.iloc[-1] = last_close + 0.4
    low.iloc[-1] = float(target_ema_n_minus_1) - 0.10  # dips just below EMA

    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def _params(**overrides) -> RangeShiftPullbackParams:
    base = dict(
        adx_min=0.0,
        bars_above_after_shift_min=2,
        pullback_atr_mult=2.0,
        atr_stop_mult=1.5,
    )
    base.update(overrides)
    return RangeShiftPullbackParams(**base)


def test_range_shift_pullback_loads_via_registry():
    s = load_strategy("range_shift_pullback")
    assert isinstance(s, RangeShiftPullback)
    universe = s.universe()
    assert len(universe) > 0


def test_range_shift_pullback_first_pullback_emits_signal():
    df = _shift_then_pullback(pullback_offset=4)
    s = RangeShiftPullback(_params())
    sigs = s.generate_signals({"SPY": df})
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.symbol == "SPY"
    assert sig.strategy_tag == "range_shift_pullback"
    assert sig.stop < sig.entry
    assert sig.target is not None and sig.target > sig.entry
    assert 0.0 <= sig.confidence <= 1.0


def test_range_shift_pullback_late_pullback_no_signal():
    # Pullback lands 8 bars after the shift -> exceeds max_bars_since_shift=5.
    df = _shift_then_pullback(n=240, pullback_offset=8)
    s = RangeShiftPullback(_params())
    sigs = s.generate_signals({"SPY": df})
    assert sigs == []


def test_range_shift_pullback_target_is_2r():
    df = _shift_then_pullback(pullback_offset=4)
    s = RangeShiftPullback(_params(target_r=2.0))
    sigs = s.generate_signals({"SPY": df})
    assert len(sigs) == 1
    sig = sigs[0]
    risk = float(sig.entry) - float(sig.stop)
    reward = float(sig.target) - float(sig.entry)
    assert risk > 0
    # Allow rounding slack from Decimal serialization.
    assert abs(reward - 2.0 * risk) < 0.05
