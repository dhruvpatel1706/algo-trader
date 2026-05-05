"""ma_pullback_trend_crypto strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.ma_pullback_trend import MaPullbackTrend, MaPullbackTrendParams
from src.strategies.ma_pullback_trend_crypto import MaPullbackTrendCrypto


def _uptrend_pullback(n=240):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.arange(n) * 0.2, index=idx, dtype=float)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_ma_pullback_trend_crypto_loads_via_registry():
    s = load_strategy("ma_pullback_trend_crypto")
    assert isinstance(s, MaPullbackTrendCrypto)
    assert isinstance(s, MaPullbackTrend)
    universe = s.universe()
    assert universe == ("BTCUSDT", "ETHUSDT")


def test_ma_pullback_trend_crypto_uses_crypto_tuned_params():
    s = MaPullbackTrendCrypto()
    equity_defaults = MaPullbackTrendParams()
    assert s.params.fast_period == 10
    assert s.params.fast_period != equity_defaults.fast_period
    assert s.params.slow_period == 50
    assert s.params.slow_period != equity_defaults.slow_period
    assert s.params.slope_lookback == 3
    assert s.params.slope_lookback != equity_defaults.slope_lookback
    assert s.params.pullback_atr_mult == 0.7
    assert s.params.pullback_atr_mult != equity_defaults.pullback_atr_mult
    assert s.params.atr_stop_mult == 2.5
    assert s.params.atr_stop_mult != equity_defaults.atr_stop_mult
    assert s.params.target_r == 2.5
    assert s.params.target_r != equity_defaults.target_r


def test_ma_pullback_trend_crypto_inherits_signal_generation():
    # Use a pullback_atr_mult wide enough that the synthetic linear uptrend
    # qualifies; this exercises the inherited generate_signals path.
    s = MaPullbackTrendCrypto(MaPullbackTrendParams(pullback_atr_mult=3.0))
    sigs = s.generate_signals({"BTCUSDT": _uptrend_pullback()})
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.symbol == "BTCUSDT"
    assert sig.stop < sig.entry
    assert sig.target is not None and sig.target > sig.entry
    assert sig.strategy_tag == "ma_pullback_trend_crypto"


def test_ma_pullback_trend_crypto_accepts_explicit_params():
    explicit = MaPullbackTrendParams(fast_period=15, slow_period=100)
    s = MaPullbackTrendCrypto(explicit)
    assert s.params is explicit
