"""Backtest metrics. Pure functions on equity / returns series and trade records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class TradeRecord:
    symbol: str
    side: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    qty: int
    entry_price: float
    exit_price: float
    pnl: float
    strategy_tag: str


_TRADING_DAYS = 252


# Numerical floor for std-deviation comparisons. A constant non-zero series
# (e.g. pd.Series([0.001]*n)) mathematically has std=0 but pandas' ddof=1
# computation produces ~2e-19 from floating-point error, slipping past
# `sd == 0` and yielding a Sharpe of ~7e+16. Found by property-based test in
# tests/property/test_metrics_properties.py. Floor at 1e-12 — well below any
# real-world return std (which is ~1e-3 for daily) and well above f64 noise.
_STD_FLOOR = 1e-12


def annualized_sharpe(returns: pd.Series, rf: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf / _TRADING_DAYS
    sd = excess.std(ddof=1)
    if not np.isfinite(sd) or sd < _STD_FLOOR:
        return 0.0
    return float(excess.mean() / sd * np.sqrt(_TRADING_DAYS))


def annualized_sortino(returns: pd.Series, rf: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf / _TRADING_DAYS
    downside = excess[excess < 0]
    if len(downside) < 2:
        return 0.0
    sd = downside.std(ddof=1)
    if not np.isfinite(sd) or sd < _STD_FLOOR:
        return 0.0
    return float(excess.mean() / sd * np.sqrt(_TRADING_DAYS))


def max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown as a positive fraction. 0.20 == 20% peak-to-trough."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (peak - equity) / peak
    return float(dd.max())


def calmar(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return 0.0
    total_return = float(equity.iloc[-1] / equity.iloc[0]) - 1.0
    annual_return = (1 + total_return) ** (365.0 / days) - 1
    dd = max_drawdown(equity)
    if dd == 0:
        return 0.0
    return annual_return / dd


def profit_factor(trades: Sequence[TradeRecord]) -> float:
    if not trades:
        return 0.0
    wins = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def expectancy(trades: Sequence[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return sum(t.pnl for t in trades) / len(trades)


def win_rate(trades: Sequence[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.pnl > 0) / len(trades)


def summarize(equity: pd.Series, returns: pd.Series, trades: Sequence[TradeRecord]) -> dict:
    """Bundle the standard backtest metrics into one dict for JSON output."""
    return {
        "sharpe": round(annualized_sharpe(returns), 3),
        "sortino": round(annualized_sortino(returns), 3),
        "calmar": round(calmar(equity), 3),
        "max_dd": round(max_drawdown(equity), 4),
        "profit_factor": round(profit_factor(trades), 3),
        "expectancy": round(expectancy(trades), 4),
        "win_rate": round(win_rate(trades), 3),
        "n_trades": len(trades),
        "start_equity": float(equity.iloc[0]) if not equity.empty else 0.0,
        "end_equity": float(equity.iloc[-1]) if not equity.empty else 0.0,
        "total_return": (float(equity.iloc[-1] / equity.iloc[0] - 1) if not equity.empty else 0.0),
    }
