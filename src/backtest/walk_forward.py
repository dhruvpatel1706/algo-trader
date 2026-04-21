"""Walk-forward harness: rolling out-of-sample evaluation across non-overlapping test windows."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.backtest.engine import BacktestEngine, BacktestResult
from src.strategies.base import Strategy


@dataclass(frozen=True)
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_windows(
    index: pd.DatetimeIndex, train_bars: int = 252, test_bars: int = 63
) -> list[Window]:
    """Roll [train, test] windows along `index`. Test windows do not overlap."""
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars and test_bars must be positive")
    windows: list[Window] = []
    n = len(index)
    start = 0
    ts = list(index)
    while start + train_bars + test_bars <= n:
        windows.append(
            Window(
                train_start=ts[start],
                train_end=ts[start + train_bars - 1],
                test_start=ts[start + train_bars],
                test_end=ts[start + train_bars + test_bars - 1],
            )
        )
        start += test_bars
    return windows


def run_walk_forward(
    strategy: Strategy,
    bars: dict[str, pd.DataFrame],
    *,
    train_bars: int = 252,
    test_bars: int = 63,
) -> BacktestResult:
    """Run `strategy` on each test window. Concatenate test-window results.

    v1 uses the strategy's default parameters — no fitting on the train window.
    The train window exists so indicators get a full warm-up before the test slice.
    """
    if not bars:
        raise ValueError("no bars provided")
    primary = next(iter(bars.values()))
    windows = make_windows(primary.index, train_bars=train_bars, test_bars=test_bars)
    if not windows:
        need = train_bars + test_bars
        raise ValueError(f"not enough bars to form a window (need >= {need}, have {len(primary)})")

    all_equity: list[pd.Series] = []
    all_returns: list[pd.Series] = []
    all_trades = []
    all_warnings: list[str] = []

    for w in windows:
        sliced = {sym: df.loc[w.train_start : w.test_end] for sym, df in bars.items()}
        engine = BacktestEngine(strategy)
        result = engine.run(sliced)
        all_equity.append(result.equity.loc[w.test_start : w.test_end])
        all_returns.append(result.returns.loc[w.test_start : w.test_end])
        all_trades.extend(t for t in result.trades if t.entry_ts >= w.test_start)
        all_warnings.extend(result.warnings)

    return BacktestResult(
        equity=pd.concat(all_equity),
        returns=pd.concat(all_returns),
        trades=all_trades,
        warnings=all_warnings,
    )
