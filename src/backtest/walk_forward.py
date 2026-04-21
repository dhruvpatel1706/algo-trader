"""Walk-forward harness — joined-equity reporting (Option B canonical).

# Walk-forward reporting conventions

Two ways to roll up per-window backtest results into one set of headline metrics:

**Option A — per-window standalone.** Each window resets to `start_equity`. The headline
metric is the **mean across windows**. Useful for "is the edge stable across regimes".
This convention loses the compounding view: nothing tells you what a continuous trader
would actually have made.

**Option B — joined / compounded series (canonical here).** Each window's test slice is
**rebased** so that its first value equals the cumulative equity at the end of the prior
window's test slice. The joined series is then continuous (no boundary discontinuities),
and `Sharpe` / `Sortino` / `max_dd` / `total_return` computed on the joined returns are
the numbers a single trader would have experienced.

This module returns Option B as the canonical equity/returns. Per-window Sharpe mean
and std are exposed via `BacktestResult.per_window_sharpe_mean` / `_std` as a stability
check (high std = inconsistent edge). `BacktestResult.n_windows` reports how many test
windows produced metrics.

The strategy's parameters are held constant across windows in v1 — no fitting on the
train slice. Train slices serve only to give indicators a full warm-up before the test
slice begins.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

from src.backtest.engine import BacktestEngine, BacktestResult
from src.backtest.metrics import annualized_sharpe
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
    start_equity: float = 100_000.0,
) -> BacktestResult:
    """Joined-equity walk-forward (Option B). See module docstring for the convention.

    Returns a `BacktestResult` whose `equity` and `returns` are the joined / compounded
    series across all test windows. `per_window_sharpe_mean` / `_std` are stability
    metrics computed from the per-window standalone Sharpes (Option A).
    """
    if not bars:
        raise ValueError("no bars provided")
    primary = next(iter(bars.values()))
    windows = make_windows(primary.index, train_bars=train_bars, test_bars=test_bars)
    if not windows:
        need = train_bars + test_bars
        raise ValueError(f"not enough bars to form a window (need >= {need}, have {len(primary)})")

    rebased_pieces: list[pd.Series] = []
    per_window_sharpes: list[float] = []
    all_trades = []
    all_warnings: list[str] = []
    cumulative_equity = float(start_equity)

    for w in windows:
        sliced = {sym: df.loc[w.train_start : w.test_end] for sym, df in bars.items()}
        engine = BacktestEngine(strategy)  # internal engine always starts at $100k
        result = engine.run(sliced)

        test_slice = result.equity.loc[w.test_start : w.test_end]
        if test_slice.empty:
            continue

        # Per-window standalone Sharpe (Option A) — used for stability stats only.
        per_window_returns = test_slice.pct_change().fillna(0.0)
        per_window_sharpes.append(float(annualized_sharpe(per_window_returns)))

        # Rebase: scale the test slice so its first value equals cumulative_equity.
        # This eliminates the boundary discontinuity that was depressing joined Sharpe.
        scale = cumulative_equity / float(test_slice.iloc[0])
        rebased = test_slice * scale
        rebased_pieces.append(rebased)
        cumulative_equity = float(rebased.iloc[-1])

        all_trades.extend(t for t in result.trades if t.entry_ts >= w.test_start)
        all_warnings.extend(result.warnings)

    if not rebased_pieces:
        raise ValueError("walk-forward produced no test-window equity")

    joined_equity = pd.concat(rebased_pieces)
    joined_returns = joined_equity.pct_change().fillna(0.0)

    pw_mean = float(statistics.fmean(per_window_sharpes)) if per_window_sharpes else 0.0
    pw_std = float(statistics.pstdev(per_window_sharpes)) if len(per_window_sharpes) > 1 else 0.0

    return BacktestResult(
        equity=joined_equity,
        returns=joined_returns,
        trades=all_trades,
        warnings=all_warnings,
        per_window_sharpe_mean=pw_mean,
        per_window_sharpe_std=pw_std,
        n_windows=len(rebased_pieces),
    )
