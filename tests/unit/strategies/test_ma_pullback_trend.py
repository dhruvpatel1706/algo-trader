"""ma_pullback_trend strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.ma_pullback_trend import MaPullbackTrend, MaPullbackTrendParams


def _uptrend_pullback(n=240):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.arange(n) * 0.2, index=idx, dtype=float)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_ma_pullback_trend_loads_via_registry():
    s = load_strategy("ma_pullback_trend")
    assert isinstance(s, MaPullbackTrend)
    # Strategy is now keyed to large_caps_50 in docs/universes.yaml.
    # Bonds get a sibling strategy `ma_pullback_trend_bonds`.
    universe = s.universe()
    assert len(universe) > 0
    assert "AAPL" in universe


def test_ma_pullback_trend_signal_has_valid_bracket():
    s = MaPullbackTrend(MaPullbackTrendParams(pullback_atr_mult=3.0))
    sigs = s.generate_signals({"SPY": _uptrend_pullback()})
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.stop < sig.entry
    assert sig.target is not None and sig.target > sig.entry
    assert sig.strategy_tag == "ma_pullback_trend"


def test_ma_pullback_trend_rejects_price_below_200_sma():
    df = _uptrend_pullback()
    df.loc[df.index[-1], "close"] = 80.0
    df.loc[df.index[-1], "low"] = 79.0
    s = MaPullbackTrend(MaPullbackTrendParams(pullback_atr_mult=3.0))
    assert s.generate_signals({"SPY": df}) == []
