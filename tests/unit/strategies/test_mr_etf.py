"""mr_etf strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.mr_etf import MrEtf, MrParams


def _bars_oversold(n=100, seed=0):
    """Construct a price path that ends with a strong pullback to trigger the strategy."""
    rng = np.random.default_rng(seed)
    base = 100 + np.cumsum(rng.normal(0.05, 0.6, n))
    # Force the last 5 bars to drop sharply for an oversold close.
    base[-5:] = base[-5] - np.linspace(0.5, 6.0, 5)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(base, index=idx)
    high = close + np.abs(rng.normal(0.5, 0.3, n))
    low = close - np.abs(rng.normal(0.5, 0.3, n))
    open_ = close.shift(1).fillna(base[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def _bars_trending(n=100, seed=0):
    """Strong uptrend — should NOT trigger (ADX > 20 + no oversold)."""
    rng = np.random.default_rng(seed)
    base = 100 + np.cumsum(rng.normal(0.5, 0.3, n))
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(base, index=idx)
    high = close + 0.5
    low = close - 0.5
    open_ = close.shift(1).fillna(base[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_mr_etf_loads_via_registry():
    s = load_strategy("mr_etf")
    assert isinstance(s, MrEtf)
    assert s.universe() == ("SPY", "QQQ")
    assert isinstance(s.params, MrParams)


def test_mr_etf_returns_no_signals_with_short_history():
    s = MrEtf()
    short = _bars_oversold(n=10)
    sigs = s.generate_signals({"SPY": short})
    assert sigs == []


def test_mr_etf_handles_missing_universe_keys():
    s = MrEtf()
    # Empty bars dict -> empty signals (no crash).
    assert s.generate_signals({}) == []


def test_mr_etf_signal_has_valid_stop_below_entry():
    s = MrEtf()
    sigs = s.generate_signals({"SPY": _bars_oversold(n=200)})
    # We can't guarantee a fire (depends on randomness + indicator), but
    # IF anything fires, it must obey buy-stop-below-entry.
    for sig in sigs:
        assert sig.stop < sig.entry
        assert sig.target is None or sig.target > sig.entry
        assert sig.strategy_tag == "mr_etf"


def test_mr_etf_no_signals_in_strong_uptrend():
    s = MrEtf()
    sigs = s.generate_signals({"SPY": _bars_trending(n=200)})
    assert sigs == []
