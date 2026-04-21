"""Parameter sensitivity sweep for any Strategy.

Cartesian product across {universes x timeframes x param_grid}. Cells with
unwired timeframes (anything other than daily in v1) are emitted as `skipped`
rows so the output table is rectangular and the operator can see what's missing.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from src.backtest.metrics import summarize
from src.backtest.walk_forward import run_walk_forward
from src.data.loader import load_daily_bars
from src.strategies.base import Strategy

log = logging.getLogger(__name__)


StrategyFactory = Callable[[dict[str, Any]], Strategy]


@dataclass(frozen=True, slots=True)
class SweepCell:
    config: dict[str, Any]
    metrics: dict[str, Any]
    warnings: tuple[str, ...] = ()
    skipped: bool = False
    skip_reason: str | None = None


_DAILY_TIMEFRAMES = ("1d", "daily", "d")


def _is_daily(tf: str) -> bool:
    return tf.lower() in _DAILY_TIMEFRAMES


def _grid(**params: list) -> Iterable[dict[str, Any]]:
    keys = list(params.keys())
    for combo in itertools.product(*params.values()):
        yield dict(zip(keys, combo, strict=True))


_EMPTY_METRICS: dict[str, Any] = {
    "sharpe": 0.0,  # joined-equity (Option B)
    "sortino": 0.0,
    "calmar": 0.0,
    "max_dd": 0.0,
    "profit_factor": 0.0,
    "expectancy": 0.0,
    "win_rate": 0.0,
    "n_trades": 0,
    "start_equity": 0.0,
    "end_equity": 0.0,
    "total_return": 0.0,
    "per_window_sharpe_mean": 0.0,  # stability check (Option A mean)
    "per_window_sharpe_std": 0.0,
    "n_windows": 0,
}


def run_sweep(
    factory: StrategyFactory,
    universes: dict[str, tuple[str, ...]],
    param_grid: dict[str, list[Any]],
    *,
    start: date,
    end: date,
    timeframes: tuple[str, ...] = ("1d",),
    train_bars: int = 252,
    test_bars: int = 63,
) -> list[SweepCell]:
    """Run a strategy across all (universe x timeframe x params) combinations.

    Non-daily timeframes are emitted as `skipped` cells (intraday loader
    not wired in v1). Bar-load failures and per-cell backtest exceptions
    are captured into the cell rather than aborting the whole sweep.
    """
    cells: list[SweepCell] = []

    for uname, tickers in universes.items():
        for tf in timeframes:
            if not _is_daily(tf):
                for params in _grid(**param_grid):
                    cells.append(
                        SweepCell(
                            config={"universe": uname, "timeframe": tf, **params},
                            metrics=dict(_EMPTY_METRICS),
                            warnings=(
                                f"intraday timeframe '{tf}' not yet wired in src/data/loader.py",
                            ),
                            skipped=True,
                            skip_reason=f"intraday-{tf}-not-wired",
                        )
                    )
                continue

            log.info(
                "loading bars for sweep",
                extra={"universe": uname, "n_tickers": len(tickers)},
            )
            try:
                bars = load_daily_bars(tickers, start, end)
            except Exception as e:
                for params in _grid(**param_grid):
                    cells.append(
                        SweepCell(
                            config={"universe": uname, "timeframe": tf, **params},
                            metrics=dict(_EMPTY_METRICS),
                            warnings=(f"bar load failed: {e}",),
                            skipped=True,
                            skip_reason="bar-load-error",
                        )
                    )
                continue

            if not bars:
                for params in _grid(**param_grid):
                    cells.append(
                        SweepCell(
                            config={"universe": uname, "timeframe": tf, **params},
                            metrics=dict(_EMPTY_METRICS),
                            warnings=(f"no bars loaded for universe '{uname}'",),
                            skipped=True,
                            skip_reason="no-bars",
                        )
                    )
                continue

            for params in _grid(**param_grid):
                strat = factory(params)
                try:
                    result = run_walk_forward(
                        strat, bars, train_bars=train_bars, test_bars=test_bars
                    )
                    metrics = summarize(result.equity, result.returns, result.trades)
                    metrics["per_window_sharpe_mean"] = round(result.per_window_sharpe_mean, 3)
                    metrics["per_window_sharpe_std"] = round(result.per_window_sharpe_std, 3)
                    metrics["n_windows"] = result.n_windows
                    cells.append(
                        SweepCell(
                            config={"universe": uname, "timeframe": tf, **params},
                            metrics=metrics,
                            warnings=tuple(result.warnings[:5]),
                        )
                    )
                except Exception as e:
                    cells.append(
                        SweepCell(
                            config={"universe": uname, "timeframe": tf, **params},
                            metrics=dict(_EMPTY_METRICS),
                            warnings=(f"backtest error: {type(e).__name__}: {e}",),
                            skipped=True,
                            skip_reason="backtest-error",
                        )
                    )
    return cells


def cells_to_dataframe(cells: list[SweepCell]) -> pd.DataFrame:
    """Flatten cells into a long-format DataFrame, one row per (universe x tf x params)."""
    rows: list[dict[str, Any]] = []
    for c in cells:
        row: dict[str, Any] = {**c.config, **c.metrics}
        row["skipped"] = c.skipped
        row["skip_reason"] = c.skip_reason or ""
        row["warnings"] = "; ".join(c.warnings)
        rows.append(row)
    return pd.DataFrame(rows)
