"""Unit tests for the RL training harness."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.moonshot.rl.train import RlTrainResult, train_agent


def _trending_bars(
    n: int, start: str, *, slope: float = 0.05, noise: float = 0.0, seed: int = 0
) -> pd.DataFrame:
    """Deterministic synthetic price series with optional Gaussian noise."""
    idx = pd.bdate_range(start=start, periods=n)
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100.0 + slope * t + (rng.standard_normal(n) * noise if noise > 0 else 0.0)
    close = np.maximum(close, 1.0)
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )


def test_train_agent_returns_result_object():
    train = _trending_bars(150, "2023-01-02")
    val = _trending_bars(100, "2024-01-02", seed=1)
    result = train_agent(train, val, n_episodes=5, seed=0)

    assert isinstance(result, RlTrainResult)
    assert len(result.train_returns) == 5
    assert isinstance(result.val_returns, list)
    assert isinstance(result.train_sharpe, float)
    assert isinstance(result.val_sharpe, float)
    assert isinstance(result.blessed, bool)


def test_train_agent_assert_no_overlap():
    a = _trending_bars(100, "2024-01-02")
    b = _trending_bars(100, "2024-01-02")  # same start - overlaps!
    with pytest.raises(ValueError):
        train_agent(a, b, n_episodes=2)


def test_blessed_false_when_validation_diverges_wildly():
    """Force overfit: train on trending, evaluate on noisy stagnation.

    The validation regime is statistically different from training, so any
    learned policy will not generalize and the blessed gate should refuse it.
    """
    train = _trending_bars(150, "2023-01-02", slope=0.5, noise=0.0, seed=0)
    # Same trend then stagnation flips into a different regime in val.
    val_idx = pd.bdate_range(start="2024-06-01", periods=120)
    rng = np.random.default_rng(99)
    # Wild noise around a flat / slightly negative drift -> val Sharpe ~0 or negative.
    val_close = 100.0 - 0.05 * np.arange(120) + rng.standard_normal(120) * 5.0
    val_close = np.maximum(val_close, 1.0)
    val = pd.DataFrame(
        {
            "open": val_close,
            "high": val_close + 0.5,
            "low": val_close - 0.5,
            "close": val_close,
            "volume": 1.0,
        },
        index=val_idx,
    )
    result = train_agent(train, val, n_episodes=10, seed=0)
    # Either val Sharpe is non-positive (so blessed=False) OR val/train ratio
    # collapses below the gate. The test asserts the GATE rejects, which is
    # the contract.
    assert result.blessed is False


def test_train_agent_runs_with_default_episodes_smaller():
    """Smoke test that the full default config works on small synthetic data."""
    train = _trending_bars(80, "2023-01-02")
    val = _trending_bars(80, "2024-01-02", seed=1)
    result = train_agent(train, val, n_episodes=3, seed=42)
    assert result.agent.n_features > 0
    # Reward stream should be finite.
    assert all(np.isfinite(result.val_returns))
