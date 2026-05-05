"""failed_breakout strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.failed_breakout import FailedBreakout, FailedBreakoutParams


def _range_then_failed_breakdown(n=170):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.sin(np.linspace(0, 12, n)) * 2, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)

    # Prior 20-day low sits around 97. Last bar pierces it and closes back inside.
    high.iloc[-5] = 108.0
    low.iloc[-1] = 94.0
    high.iloc[-1] = 100.5
    close.iloc[-1] = 99.0
    open_.iloc[-1] = 96.5
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_failed_breakout_loads_via_registry():
    s = load_strategy("failed_breakout")
    assert isinstance(s, FailedBreakout)
    assert "GLD" in s.universe()


def test_failed_breakout_signal_has_stop_below_entry_and_target_above():
    s = FailedBreakout(
        FailedBreakoutParams(
            adx_min=0.0,
            atr_stop_mult=0.5,
            wvf_quantile_lookback=30,
            min_reward_r=0.5,
        )
    )
    sigs = s.generate_signals({"GLD": _range_then_failed_breakdown()})
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.symbol == "GLD"
    assert sig.stop < sig.entry
    assert sig.target is not None and sig.target > sig.entry
    assert sig.strategy_tag == "failed_breakout"


def test_failed_breakout_requires_close_back_inside_range():
    df = _range_then_failed_breakdown()
    df.loc[df.index[-1], "close"] = 93.0
    s = FailedBreakout(
        FailedBreakoutParams(adx_min=0.0, wvf_quantile_lookback=30, min_reward_r=0.5)
    )
    assert s.generate_signals({"GLD": df}) == []
