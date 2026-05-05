"""Out-of-sample evaluation for RL policies.

This is a research helper: run an agent through `TradingEnv` over `bars_test`
and emit the standard metric bundle (Sharpe, Sortino, max-drawdown, total
return, n_trades, win-rate). The lane is paper-only — these metrics never feed
into a live execution decision without going through `live_readiness.py`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.moonshot.rl.agent import LinearQAgent
from src.moonshot.rl.env import TradingEnv, TradingEnvConfig


def _sharpe(rs: np.ndarray) -> float:
    rs = rs[np.isfinite(rs)]
    if rs.size < 2:
        return 0.0
    std = rs.std(ddof=1)
    if std <= 1e-12:
        return 0.0
    return float(math.sqrt(252.0) * rs.mean() / std)


def _sortino(rs: np.ndarray) -> float:
    rs = rs[np.isfinite(rs)]
    if rs.size < 2:
        return 0.0
    downside = rs[rs < 0]
    if downside.size == 0:
        return 0.0
    dd_std = downside.std(ddof=0)
    if dd_std <= 1e-12:
        return 0.0
    return float(math.sqrt(252.0) * rs.mean() / dd_std)


def _max_drawdown(equity_curve: np.ndarray) -> float:
    if equity_curve.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity_curve)
    safe_peaks = np.where(peaks > 0, peaks, 1.0)
    dds = 1.0 - (equity_curve / safe_peaks)
    return float(dds.max())


def evaluate_policy(
    agent: LinearQAgent,
    bars_test: pd.DataFrame,
    config: TradingEnvConfig | None = None,
    *,
    deterministic: bool = True,
) -> dict[str, Any]:
    """Run `agent` through `TradingEnv` on `bars_test` and return metrics.

    Metrics returned:
        sharpe, sortino, max_dd, total_return, n_trades, win_rate, final_equity.

    `deterministic=True` disables epsilon-greedy randomness, so calling this
    twice on the same agent + bars yields identical results (used by tests).
    """
    cfg = config or TradingEnvConfig()
    env = TradingEnv(bars_test, config=cfg, seed=0)
    obs, _info = env.reset()

    rewards: list[float] = []
    equity_curve: list[float] = [cfg.initial_equity]
    n_trades = 0
    wins = 0
    trade_pnls: list[float] = []

    done = False
    truncated = False
    while not (done or truncated):
        action = agent.select_action(obs, deterministic=deterministic)
        obs, reward, done, truncated, info = env.step(action)
        rewards.append(reward)
        equity_curve.append(float(info.get("equity", equity_curve[-1])))
        n_trades = int(info.get("n_trades", n_trades))
        wins = int(info.get("wins", wins))
        trade_pnls = list(info.get("trade_pnls", trade_pnls))

    rs = np.asarray(rewards, dtype=np.float64)
    eq = np.asarray(equity_curve, dtype=np.float64)

    total_return = (
        float((eq[-1] / eq[0]) - 1.0) if eq[0] > 0 and eq.size >= 2 else 0.0
    )
    closed_trades = [p for p in trade_pnls]
    win_rate = (
        float(sum(1 for p in closed_trades if p > 0) / len(closed_trades))
        if closed_trades
        else 0.0
    )

    return {
        "sharpe": _sharpe(rs),
        "sortino": _sortino(rs),
        "max_dd": _max_drawdown(eq),
        "total_return": total_return,
        "n_trades": int(n_trades),
        "win_rate": float(win_rate),
        "final_equity": float(eq[-1]) if eq.size else float(cfg.initial_equity),
    }


__all__ = ["evaluate_policy"]
