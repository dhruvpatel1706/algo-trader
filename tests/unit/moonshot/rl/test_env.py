"""Unit tests for the moonshot RL trading environment."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from src.moonshot.rl.env import (
    ACTION_FLAT,
    ACTION_LONG,
    ACTION_SHORT,
    TradingEnv,
    TradingEnvConfig,
)


def _sinusoidal_bars(
    n: int = 300,
    *,
    amplitude: float = 5.0,
    period: int = 30,
    base: float = 100.0,
    start: str = "2024-01-02",
) -> pd.DataFrame:
    """Deterministic OHLCV from a sine wave; tests don't need real data."""
    idx = pd.bdate_range(start=start, periods=n)
    t = np.arange(n)
    close = base + amplitude * np.sin(2 * np.pi * t / period)
    high = close + 0.5
    low = close - 0.5
    open_ = close.copy()
    volume = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _crash_bars(n: int = 200, start: str = "2024-01-02") -> pd.DataFrame:
    """Bars that monotonically crash — used to trip the drawdown halt."""
    idx = pd.bdate_range(start=start, periods=n)
    close = np.linspace(100.0, 5.0, n)  # 95% drawdown, very smooth
    high = close + 0.1
    low = close - 0.1
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )


# -------- reset --------------------------------------------------------------


def test_reset_returns_valid_observation():
    bars = _sinusoidal_bars()
    env = TradingEnv(bars)
    obs, info = env.reset()

    assert isinstance(obs, np.ndarray)
    assert obs.shape == (env.n_features,)
    assert np.all(np.isfinite(obs))
    assert info["equity"] == env.config.initial_equity
    assert info["position"] == 0
    assert info["bar_idx"] == env.config.warmup_bars


def test_reset_is_deterministic_with_seed():
    bars = _sinusoidal_bars()
    env_a = TradingEnv(bars, seed=7)
    env_b = TradingEnv(bars, seed=7)
    obs_a, _ = env_a.reset()
    obs_b, _ = env_b.reset()
    np.testing.assert_array_equal(obs_a, obs_b)


# -------- step ---------------------------------------------------------------


def test_step_returns_5_tuple():
    bars = _sinusoidal_bars()
    env = TradingEnv(bars)
    env.reset()
    out = env.step(ACTION_FLAT)
    assert isinstance(out, tuple) and len(out) == 5
    obs, reward, terminated, truncated, info = out
    assert isinstance(obs, np.ndarray)
    assert math.isfinite(reward)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert isinstance(info, dict)
    assert "equity" in info


def test_step_reward_is_in_floating_range():
    """Reward should be a finite log-return; we can bound it loosely."""
    bars = _sinusoidal_bars()
    env = TradingEnv(bars)
    env.reset()

    # Walk a few hundred bars and assert all rewards stay finite + bounded.
    for _ in range(100):
        _, reward, terminated, truncated, _ = env.step(ACTION_LONG)
        assert math.isfinite(reward)
        assert -1.0 <= reward <= 1.0
        if terminated or truncated:
            break


def test_invalid_action_raises():
    bars = _sinusoidal_bars()
    env = TradingEnv(bars)
    env.reset()
    try:
        env.step(99)
    except ValueError:
        return
    raise AssertionError("expected ValueError for out-of-range action")


def test_short_blocked_when_not_allowed():
    """Action=2 should be ignored / coerced when allow_short=False."""
    bars = _sinusoidal_bars()
    cfg = TradingEnvConfig(allow_short=False)
    env = TradingEnv(bars, config=cfg)
    # n_actions should reflect long-only setup.
    assert env.n_actions == 2
    env.reset()
    try:
        env.step(ACTION_SHORT)  # action=2 is out of range when allow_short=False
    except ValueError:
        return
    raise AssertionError("expected short action to be rejected when allow_short=False")


def test_short_allowed_when_configured():
    bars = _sinusoidal_bars()
    cfg = TradingEnvConfig(allow_short=True)
    env = TradingEnv(bars, config=cfg)
    assert env.n_actions == 3
    env.reset()
    _, _, _, _, info = env.step(ACTION_SHORT)
    assert info["position"] == -1


# -------- drawdown halt ------------------------------------------------------


def test_drawdown_halt_terminates_episode():
    bars = _crash_bars(n=200)
    cfg = TradingEnvConfig(max_drawdown_halt=0.05, max_position_size_pct=1.0)
    env = TradingEnv(bars, config=cfg)
    env.reset()

    terminated = False
    for _ in range(len(bars)):
        _, _, terminated, truncated, info = env.step(ACTION_LONG)
        if terminated:
            assert info["drawdown"] >= cfg.max_drawdown_halt
            break
        if truncated:
            break

    assert terminated, "expected drawdown halt to fire on a 95% crash"


# -------- transaction cost ---------------------------------------------------


def test_transaction_cost_is_applied_when_position_changes():
    """Compare flat-only vs flip-flopping equity. Costs MUST hurt the latter."""
    flat_bars = _sinusoidal_bars(n=200)
    cfg_costly = TradingEnvConfig(transaction_cost_bps=50.0, slippage_bps=50.0)

    env_a = TradingEnv(flat_bars, config=cfg_costly)
    env_a.reset()
    for _ in range(100):
        env_a.step(ACTION_FLAT)
    flat_equity = env_a.equity

    env_b = TradingEnv(flat_bars, config=cfg_costly)
    env_b.reset()
    # Flip every bar -> incur transaction cost on every step.
    for i in range(100):
        env_b.step(ACTION_LONG if i % 2 == 0 else ACTION_FLAT)
    flipping_equity = env_b.equity

    # Flat equity should be exactly initial (no position, no cost).
    assert flat_equity == cfg_costly.initial_equity
    # Flipping should bleed equity through fees.
    assert flipping_equity < cfg_costly.initial_equity


def test_transaction_cost_zero_when_no_position_change():
    bars = _sinusoidal_bars(n=200)
    env = TradingEnv(bars, config=TradingEnvConfig(transaction_cost_bps=100.0))
    env.reset()
    _, _, _, _, info = env.step(ACTION_FLAT)
    assert info["transaction_cost"] == 0.0


# -------- input validation ---------------------------------------------------


def test_missing_close_column_raises():
    bars = pd.DataFrame({"open": [1.0, 2.0]})
    try:
        TradingEnv(bars)
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing close column")


def test_too_few_bars_raises():
    bars = pd.DataFrame({"close": [100.0]})
    try:
        TradingEnv(bars)
    except ValueError:
        return
    raise AssertionError("expected ValueError for <2 bars")
