"""Tests for src.ml.predict."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.ml.predict import load_model, predict_score, save_model
from src.ml.train import train_model


@pytest.fixture
def trained_model(tmp_path):
    rng = np.random.default_rng(7)
    n = 400
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    z = rng.standard_normal(n)
    X = pd.DataFrame(
        {
            "signal": z + rng.normal(0, 0.05, n),
            "noise_1": rng.standard_normal(n),
            "noise_2": rng.standard_normal(n),
        },
        index=idx,
    )
    y = pd.Series((z > 0).astype(np.int64), index=idx)
    result = train_model(X, y, n_features=2, n_splits=3)
    return result, X


def test_save_and_load_model_roundtrip(tmp_path, trained_model):
    result, X = trained_model
    path = tmp_path / "overlay_v1.pkl"
    save_model(result.model, path)
    assert path.exists()

    reloaded = load_model(path)
    # Reloaded model should produce the same predictions on the same row.
    row = X[list(result.feature_names)].iloc[-1]
    s1 = predict_score(result.model, row)
    s2 = predict_score(reloaded, row)
    assert abs(s1 - s2) < 1e-9


def test_predict_score_in_unit_interval(trained_model):
    result, X = trained_model
    row = X[list(result.feature_names)].iloc[100]
    score = predict_score(result.model, row)
    assert 0.0 <= score <= 1.0


def test_predict_score_handles_reordered_columns(trained_model):
    result, X = trained_model
    cols = list(result.feature_names)
    row_normal = X[cols].iloc[42]
    row_shuffled = row_normal.iloc[::-1]
    s1 = predict_score(result.model, row_normal)
    s2 = predict_score(result.model, row_shuffled)
    assert abs(s1 - s2) < 1e-9


def test_load_model_rejects_wrong_type(tmp_path):
    import pickle

    path = tmp_path / "bogus.pkl"
    with path.open("wb") as fh:
        pickle.dump({"not": "a model"}, fh)
    with pytest.raises(TypeError):
        load_model(path)
