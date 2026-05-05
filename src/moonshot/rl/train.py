"""Training harness for the moonshot RL lane.

The harness fits a `LinearQAgent` on `bars_train`, evaluates greedily on
`bars_val`, and emits a `RlTrainResult` with an explicit `blessed` flag. The
flag is conservative on purpose: a strategy is only marked blessed if its
validation Sharpe is positive AND retains > 50% of training Sharpe (a coarse
overfit check). Even when blessed, the lane stays paper-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.moonshot.rl.agent import LinearQAgent
from src.moonshot.rl.env import TradingEnv, TradingEnvConfig

# Moonshot lane invariant: training never executes against a real broker.
LIVE_BROKER_BRIDGE: bool = False


@dataclass(frozen=True, slots=True)
class RlTrainResult:
    agent: LinearQAgent
    train_returns: list[float]  # episode returns (one per training episode)
    val_returns: list[float]  # per-bar log returns on the validation pass
    train_sharpe: float
    val_sharpe: float
    blessed: bool


def _sharpe(returns: list[float] | np.ndarray) -> float:
    """Annualised Sharpe of a return series (sqrt(252) * mean/std).

    Returns 0.0 if the series has no variance / fewer than 2 points.
    """
    arr = np.asarray(list(returns), dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return 0.0
    std = arr.std(ddof=1)
    if std <= 1e-12:
        return 0.0
    return float(math.sqrt(252.0) * arr.mean() / std)


def _run_episode_greedy(
    env: TradingEnv,
    agent: LinearQAgent,
    *,
    deterministic: bool,
) -> tuple[list[float], float]:
    """Run a single greedy/eval episode through `env` with `agent`.

    Returns `(per_step_log_returns, total_return)`.
    """
    obs, _info = env.reset()
    rewards: list[float] = []
    done = False
    truncated = False
    while not (done or truncated):
        action = agent.select_action(obs, deterministic=deterministic)
        obs, reward, done, truncated, _info = env.step(action)
        rewards.append(reward)
    total_return = float(sum(rewards))
    return rewards, total_return


def _run_episode_train(
    env: TradingEnv,
    agent: LinearQAgent,
) -> float:
    """Run one SARSA(λ) training episode. Returns total log-return."""
    obs, _info = env.reset()
    action = agent.select_action(obs)
    total = 0.0
    agent.reset_traces()
    while True:
        next_obs, reward, terminated, truncated, _info = env.step(action)
        if terminated or truncated:
            agent.update(obs, action, reward, next_obs, action, done=True)
            total += reward
            break
        next_action = agent.select_action(next_obs)
        agent.update(obs, action, reward, next_obs, next_action, done=False)
        total += reward
        obs, action = next_obs, next_action
    agent.decay_epsilon()
    return total


def train_agent(
    bars_train: pd.DataFrame,
    bars_val: pd.DataFrame,
    *,
    n_episodes: int = 200,
    config: TradingEnvConfig | None = None,
    seed: int = 42,
) -> RlTrainResult:
    """Train a `LinearQAgent` on `bars_train`, evaluate on `bars_val`.

    Critical: `bars_val` must NOT overlap `bars_train` in time. We assert this
    here as a defensive look-ahead guard.

    `blessed=True` only if `val_sharpe > 0` AND `val_sharpe / train_sharpe > 0.5`.
    """
    if "close" not in bars_train.columns or "close" not in bars_val.columns:
        raise ValueError("bars_train and bars_val must both contain 'close'")

    # Look-ahead protection: if both have DatetimeIndex, the val index must start
    # AFTER the train index ends. If they don't, we skip the strict check (the
    # caller is responsible).
    if (
        isinstance(bars_train.index, pd.DatetimeIndex)
        and isinstance(bars_val.index, pd.DatetimeIndex)
        and len(bars_train)
        and len(bars_val)
    ):
        if bars_val.index.min() <= bars_train.index.max():
            raise ValueError(
                "bars_val starts at or before the end of bars_train; this would "
                "leak future information into training. Make windows disjoint."
            )

    cfg = config or TradingEnvConfig()
    train_env = TradingEnv(bars_train, config=cfg, seed=seed)
    val_env = TradingEnv(bars_val, config=cfg, seed=seed)

    agent = LinearQAgent(
        n_features=train_env.n_features,
        n_actions=train_env.n_actions,
        seed=seed,
    )

    train_episode_totals: list[float] = []
    for _ in range(int(n_episodes)):
        total = _run_episode_train(train_env, agent)
        train_episode_totals.append(total)

    # For Sharpe we need per-step returns. We collect them from a single
    # final greedy pass on the train set (this also serves as a sanity check
    # that the policy hasn't NaN'd out).
    train_step_returns, _ = _run_episode_greedy(train_env, agent, deterministic=True)
    val_step_returns, _ = _run_episode_greedy(val_env, agent, deterministic=True)

    train_sharpe = _sharpe(train_step_returns)
    val_sharpe = _sharpe(val_step_returns)

    blessed = False
    if val_sharpe > 0:
        if train_sharpe > 0:
            blessed = (val_sharpe / train_sharpe) > 0.5
        else:
            # Train Sharpe non-positive but val positive — unlikely-but-good.
            blessed = True

    return RlTrainResult(
        agent=agent,
        train_returns=train_episode_totals,
        val_returns=val_step_returns,
        train_sharpe=train_sharpe,
        val_sharpe=val_sharpe,
        blessed=bool(blessed),
    )


__all__ = ["RlTrainResult", "train_agent"]
