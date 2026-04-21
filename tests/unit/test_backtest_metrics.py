"""Backtest metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.backtest.metrics import (
    TradeRecord,
    annualized_sharpe,
    annualized_sortino,
    calmar,
    expectancy,
    max_drawdown,
    profit_factor,
    summarize,
    win_rate,
)


def _trades(pnls):
    return [
        TradeRecord(
            symbol="SPY",
            side="buy",
            entry_ts=pd.Timestamp("2024-01-01"),
            exit_ts=pd.Timestamp("2024-01-02"),
            qty=10,
            entry_price=100,
            exit_price=100 + p / 10,
            pnl=p,
            strategy_tag="t",
        )
        for p in pnls
    ]


def test_max_drawdown_basic():
    eq = pd.Series([100, 105, 110, 90, 95, 100], index=pd.date_range("2024-01-01", periods=6))
    assert abs(max_drawdown(eq) - (110 - 90) / 110) < 1e-9


def test_max_drawdown_empty():
    assert max_drawdown(pd.Series(dtype=float)) == 0.0


def test_sharpe_zero_for_constant_returns():
    r = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0])
    assert annualized_sharpe(r) == 0.0


def test_sharpe_positive_for_positive_drift():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.001, 0.01, 100))
    assert annualized_sharpe(r) > 0


def test_sortino_zero_when_no_negatives():
    r = pd.Series([0.01, 0.02, 0.005, 0.0])
    assert annualized_sortino(r) == 0.0


def test_sortino_positive_for_drift():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.001, 0.01, 200))
    assert annualized_sortino(r) > 0


def test_calmar_zero_for_empty():
    assert calmar(pd.Series(dtype=float)) == 0.0


def test_profit_factor():
    assert abs(profit_factor(_trades([100, -50, 75, -25])) - (175 / 75)) < 1e-9


def test_profit_factor_no_losses():
    assert profit_factor(_trades([10, 20, 30])) == float("inf")


def test_profit_factor_no_trades():
    assert profit_factor([]) == 0.0


def test_expectancy():
    assert expectancy(_trades([10, -10, 20, -5])) == 3.75


def test_expectancy_empty():
    assert expectancy([]) == 0.0


def test_win_rate():
    assert win_rate(_trades([10, -10, 20, -5])) == 0.5


def test_win_rate_empty():
    assert win_rate([]) == 0.0


def test_summarize_returns_all_keys():
    eq = pd.Series(
        [100000, 100100, 100200, 100150],
        index=pd.date_range("2024-01-01", periods=4),
    )
    r = eq.pct_change().fillna(0)
    s = summarize(eq, r, _trades([10, -5, 20]))
    expected = {
        "sharpe",
        "sortino",
        "calmar",
        "max_dd",
        "profit_factor",
        "expectancy",
        "win_rate",
        "n_trades",
        "start_equity",
        "end_equity",
        "total_return",
    }
    assert set(s.keys()) == expected
    assert s["n_trades"] == 3
