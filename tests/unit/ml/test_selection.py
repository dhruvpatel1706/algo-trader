"""Tests for src.ml.selection."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.ml.selection import mrmr_select, mutual_information_scores


def _build_dataset(n: int = 400, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    informative = rng.standard_normal(n)
    y = (informative > 0).astype(np.int64)
    # Two informative features (the second is informative-but-redundant copy).
    f_informative = informative + rng.normal(0, 0.05, n)
    f_redundant = informative + rng.normal(0, 0.05, n)
    f_noise_a = rng.standard_normal(n)
    f_noise_b = rng.standard_normal(n)
    f_noise_c = rng.standard_normal(n)
    X = pd.DataFrame(
        {
            "informative": f_informative,
            "redundant_copy": f_redundant,
            "noise_a": f_noise_a,
            "noise_b": f_noise_b,
            "noise_c": f_noise_c,
        }
    )
    return X, pd.Series(y, name="y")


def test_mutual_information_scores_ranks_informative_first():
    X, y = _build_dataset()
    scores = mutual_information_scores(X, y)
    assert scores.index[0] in {"informative", "redundant_copy"}
    # Both informative-ish features should beat the noise features.
    assert scores["informative"] > scores["noise_a"]
    assert scores["redundant_copy"] > scores["noise_b"]


def test_mutual_information_scores_handles_empty_frame():
    scores = mutual_information_scores(pd.DataFrame(), pd.Series(dtype="int64"))
    assert scores.empty


def test_mrmr_select_avoids_picking_two_redundant_features():
    X, y = _build_dataset(n=500, seed=3)
    selected = mrmr_select(X, y, n=2)
    # First pick is the most informative; second pick should NOT be the
    # near-duplicate informative feature.
    assert len(selected) == 2
    assert not (
        "informative" in selected and "redundant_copy" in selected
    ), f"mRMR shouldn't pick both highly-correlated informatives: {selected}"


def test_mrmr_select_returns_n_features_or_all_if_fewer():
    X, y = _build_dataset()
    chosen = mrmr_select(X, y, n=3)
    assert len(chosen) == 3
    chosen_all = mrmr_select(X, y, n=99)
    assert len(chosen_all) == X.shape[1]


def test_mrmr_select_handles_zero_n():
    X, y = _build_dataset()
    assert mrmr_select(X, y, n=0) == []


def test_mrmr_select_handles_empty_frame():
    assert mrmr_select(pd.DataFrame(), pd.Series(dtype="int64"), n=5) == []
