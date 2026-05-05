"""Tests for src.ml.train."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.ml.train import TrainResult, purged_walk_forward_splits, train_model


def test_purged_walk_forward_splits_non_overlapping_with_embargo():
    splits = purged_walk_forward_splits(n_samples=500, n_splits=5, embargo_days=5)
    assert len(splits) >= 1
    for train_idx, test_idx in splits:
        assert train_idx.size > 0
        assert test_idx.size > 0
        # No overlap.
        assert not set(train_idx.tolist()) & set(test_idx.tolist())
        # Embargo: train must end at least embargo_days before test starts.
        gap = test_idx.min() - train_idx.max()
        assert gap >= 5, f"embargo violated: gap={gap}"


def test_purged_walk_forward_splits_handles_zero_samples():
    assert purged_walk_forward_splits(0) == []


def _synthetic_xy(n: int = 600, seed: int = 4, signal_strength: float = 1.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    z = rng.standard_normal(n)
    X = pd.DataFrame(
        {
            "signal": z * signal_strength + rng.normal(0, 0.1, n),
            "noise_1": rng.standard_normal(n),
            "noise_2": rng.standard_normal(n),
            "noise_3": rng.standard_normal(n),
        },
        index=idx,
    )
    y = pd.Series((z > 0).astype(np.int64), index=idx, name="y")
    return X, y


def test_train_model_returns_train_result():
    X, y = _synthetic_xy()
    result = train_model(X, y, n_features=3, n_splits=3, embargo_days=5)
    assert isinstance(result, TrainResult)
    assert len(result.feature_names) <= 3
    assert "sharpe" in result.train_metrics
    assert "sharpe" in result.holdout_metrics
    # On a real signal the model should learn something — but we only assert
    # the structural contract here, not predictive performance, to keep the
    # test deterministic across LightGBM versions.


def test_train_model_overfit_case_not_blessed():
    """When holdout is pure noise but train signal is strong, the gate must
    refuse to bless the model."""
    rng = np.random.default_rng(0)
    n = 400
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    # Train block (first 80%): X[0] perfectly predicts y; rest is noise.
    z = rng.standard_normal(n)
    train_y = (z > 0).astype(np.int64)
    train_signal = z + rng.normal(0, 0.01, n)
    # Overwrite the holdout tail's relationship: scramble y vs X.
    cutoff = int(n * 0.8)
    y_arr = train_y.copy()
    rng.shuffle(y_arr[cutoff:])
    X = pd.DataFrame(
        {
            "signal": train_signal,
            "noise_1": rng.standard_normal(n),
            "noise_2": rng.standard_normal(n),
        },
        index=idx,
    )
    y = pd.Series(y_arr, index=idx)
    result = train_model(X, y, n_features=3, n_splits=3, overfit_ratio_threshold=2.0)
    # Heavy overfit: train sharpe positive, holdout near zero or negative ->
    # ratio explodes and gate rejects.
    if result.train_metrics["sharpe"] > 0 and result.holdout_metrics["sharpe"] < 0.5:
        assert result.blessed is False, (
            f"expected blessed=False, got "
            f"train_sharpe={result.train_metrics['sharpe']:.2f} "
            f"holdout_sharpe={result.holdout_metrics['sharpe']:.2f}"
        )


def test_train_model_raises_on_all_nan():
    X = pd.DataFrame({"a": [np.nan, np.nan, np.nan]})
    y = pd.Series([1, 0, 1])
    try:
        train_model(X, y, n_features=1, n_splits=2)
    except ValueError as exc:
        assert "no usable rows" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for all-NaN input")
