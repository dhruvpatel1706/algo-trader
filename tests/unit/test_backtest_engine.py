"""BacktestEngine + walk_forward smoke tests on synthetic data."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from src.backtest.engine import BacktestEngine
from src.backtest.walk_forward import make_windows, run_walk_forward
from src.strategies.base import Signal, Strategy


class _BuyOnceAtBar5(Strategy):
    """Buy SPY exactly once when the slice has 5 bars. Target +5%, stop -2%."""

    name = "_buy_once_at_bar_5"

    def universe(self):
        return ("SPY",)

    def generate_signals(self, bars):
        df = bars["SPY"]
        if len(df) != 5:
            return []
        last = df.iloc[-1]
        return [
            Signal(
                symbol="SPY",
                side="buy",
                entry=Decimal(str(last["close"])),
                stop=Decimal(str(float(last["close"]) * 0.98)),
                target=Decimal(str(float(last["close"]) * 1.05)),
                confidence=0.6,
                strategy_tag="test",
                timestamp=last.name,
            )
        ]


class _Mute(Strategy):
    name = "mute"

    def universe(self):
        return ("SPY",)

    def generate_signals(self, bars):
        return []


def _bars(n=30, start=100.0, drift=0.05, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(start + np.cumsum(rng.normal(drift, 0.5, n)), index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(start)
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_engine_runs_and_records_a_trade():
    engine = BacktestEngine(_BuyOnceAtBar5(), starting_equity=Decimal("100000"))
    result = engine.run({"SPY": _bars(30)})
    assert not result.equity.empty
    assert len(result.trades) == 1


def test_engine_zero_signals_yields_flat_equity():
    engine = BacktestEngine(_Mute(), starting_equity=Decimal("100000"))
    result = engine.run({"SPY": _bars(30)})
    assert (result.equity == 100000).all()
    assert result.trades == []


def test_engine_rejects_empty_bars():
    engine = BacktestEngine(_Mute())
    with pytest.raises(ValueError):
        engine.run({})


def test_make_windows_basic():
    idx = pd.date_range("2024-01-02", periods=400, freq="B")
    windows = make_windows(idx, train_bars=252, test_bars=63)
    assert len(windows) == 2  # 0..314, 63..377; 126+315=441>400 stops it
    for w in windows:
        assert w.train_start < w.train_end < w.test_start <= w.test_end


def test_make_windows_rejects_negative():
    idx = pd.date_range("2024-01-02", periods=10, freq="B")
    with pytest.raises(ValueError):
        make_windows(idx, train_bars=0, test_bars=10)


def test_walk_forward_runs_on_long_series():
    # Generate enough bars (>315) for at least one walk-forward window.
    bars = {"SPY": _bars(n=400)}
    result = run_walk_forward(_Mute(), bars, train_bars=252, test_bars=63)
    assert not result.equity.empty


def test_walk_forward_rejects_short_series():
    bars = {"SPY": _bars(n=100)}
    with pytest.raises(ValueError):
        run_walk_forward(_Mute(), bars)
