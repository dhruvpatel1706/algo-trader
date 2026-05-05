"""failed_breakout_crypto strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.failed_breakout import FailedBreakout, FailedBreakoutParams
from src.strategies.failed_breakout_crypto import FailedBreakoutCrypto


def _range_then_failed_breakdown(n=170):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.sin(np.linspace(0, 12, n)) * 2, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)

    # Prior 14-day low sits around 97. Last bar pierces it and closes back inside.
    high.iloc[-5] = 108.0
    low.iloc[-1] = 94.0
    high.iloc[-1] = 100.5
    close.iloc[-1] = 99.0
    open_.iloc[-1] = 96.5
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_failed_breakout_crypto_loads_via_registry():
    s = load_strategy("failed_breakout_crypto")
    assert isinstance(s, FailedBreakoutCrypto)
    # Crypto variant is also a FailedBreakout (subclass relationship preserved).
    assert isinstance(s, FailedBreakout)
    universe = s.universe()
    assert universe == ("BTCUSDT", "ETHUSDT")


def test_failed_breakout_crypto_uses_crypto_tuned_params():
    s = FailedBreakoutCrypto()
    equity_defaults = FailedBreakoutParams()
    # Confirm at least the headline tuning differences vs equity defaults.
    assert s.params.channel_period == 14
    assert s.params.channel_period != equity_defaults.channel_period
    assert s.params.adx_min == 18.0
    assert s.params.adx_min != equity_defaults.adx_min
    assert s.params.wvf_min_quantile == 0.85
    assert s.params.wvf_min_quantile != equity_defaults.wvf_min_quantile
    assert s.params.atr_stop_mult == 1.5
    assert s.params.atr_stop_mult != equity_defaults.atr_stop_mult
    assert s.params.min_reward_r == 1.5
    assert s.params.min_reward_r != equity_defaults.min_reward_r


def test_failed_breakout_crypto_inherits_signal_generation():
    # Loosen gates so the synthetic bar set produces a signal regardless of
    # crypto-tuned defaults; this exercises the inherited generate_signals.
    s = FailedBreakoutCrypto(
        FailedBreakoutParams(
            adx_min=0.0,
            atr_stop_mult=0.5,
            wvf_quantile_lookback=30,
            min_reward_r=0.5,
        )
    )
    sigs = s.generate_signals({"BTCUSDT": _range_then_failed_breakdown()})
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.symbol == "BTCUSDT"
    assert sig.stop < sig.entry
    assert sig.target is not None and sig.target > sig.entry
    assert sig.strategy_tag == "failed_breakout_crypto"


def test_failed_breakout_crypto_accepts_explicit_params():
    explicit = FailedBreakoutParams(channel_period=21, atr_stop_mult=1.2)
    s = FailedBreakoutCrypto(explicit)
    assert s.params is explicit
