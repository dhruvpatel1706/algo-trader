"""Backtest package — public surface."""

from src.backtest.costs import DEFAULT_COSTS, CostModel
from src.backtest.engine import BacktestEngine, BacktestResult
from src.backtest.metrics import TradeRecord, summarize
from src.backtest.walk_forward import Window, make_windows, run_walk_forward

__all__ = [
    "DEFAULT_COSTS",
    "BacktestEngine",
    "BacktestResult",
    "CostModel",
    "TradeRecord",
    "Window",
    "make_windows",
    "run_walk_forward",
    "summarize",
]
