"""Unit tests for `evaluate_policy`."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.moonshot.rl.agent import LinearQAgent
from src.moonshot.rl.env import TradingEnv, TradingEnvConfig
from src.moonshot.rl.evaluate import evaluate_policy


def _bars(n: int = 200, start: str = "2024-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=n)
    t = np.arange(n)
    close = 100.0 + 5.0 * np.sin(2 * np.pi * t / 25)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


def _make_agent(env: TradingEnv) -> LinearQAgent:
    return LinearQAgent(
        n_features=env.n_features,
        n_actions=env.n_actions,
        seed=11,
    )


def test_evaluate_returns_expected_keys():
    bars = _bars()
    env = TradingEnv(bars)
    agent = _make_agent(env)
    metrics = evaluate_policy(agent, bars, deterministic=True)

    assert set(metrics.keys()) == {
        "sharpe",
        "sortino",
        "max_dd",
        "total_return",
        "n_trades",
        "win_rate",
        "final_equity",
    }


def test_evaluate_metric_types_and_ranges():
    bars = _bars()
    env = TradingEnv(bars)
    agent = _make_agent(env)
    m = evaluate_policy(agent, bars, deterministic=True)

    assert isinstance(m["sharpe"], float)
    assert isinstance(m["sortino"], float)
    assert isinstance(m["max_dd"], float)
    assert isinstance(m["total_return"], float)
    assert isinstance(m["n_trades"], int)
    assert isinstance(m["win_rate"], float)
    assert isinstance(m["final_equity"], float)
    assert 0.0 <= m["max_dd"] <= 1.0
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["n_trades"] >= 0


def test_evaluate_deterministic_is_repeatable():
    """Two deterministic evaluations on the same agent + bars must match."""
    bars = _bars()
    env = TradingEnv(bars)
    agent = _make_agent(env)
    # Train one update so the agent is non-trivial but still deterministic.
    s = np.zeros(agent.n_features)
    agent.update(s, action=0, reward=0.1, next_state=s, next_action=0, done=False)

    a = evaluate_policy(agent, bars, deterministic=True)
    b = evaluate_policy(agent, bars, deterministic=True)

    assert a == b


def test_evaluate_handles_custom_config():
    bars = _bars()
    cfg = TradingEnvConfig(
        initial_equity=5000.0,
        max_position_size_pct=0.05,
        transaction_cost_bps=0.5,
    )
    env = TradingEnv(bars, config=cfg)
    agent = _make_agent(env)
    m = evaluate_policy(agent, bars, config=cfg, deterministic=True)
    # final_equity is centered on initial_equity with small drifts.
    assert m["final_equity"] > 0
    assert m["final_equity"] < cfg.initial_equity * 100
