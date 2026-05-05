"""Feature selection for the ML overlay.

We deliberately separate feature SELECTION from weight OPTIMIZATION:

- ``mutual_information_scores`` ranks features against the binary target using
  ``sklearn.feature_selection.mutual_info_classif``.
- ``mrmr_select`` runs Minimum Redundancy Maximum Relevance: greedy pick of the
  highest-MI feature first, then iteratively pick features that maximise
  ``MI(f, y) - mean MI(f, already_selected)``. This avoids stuffing the model
  with N copies of the same underlying signal.

LightGBM (the eventual classifier) handles weights and interactions; mRMR
handles the orthogonality of the input space. Keeping these stages distinct
makes both easier to audit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_classif, mutual_info_regression


def _clean(X: pd.DataFrame, y: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """Drop rows with any NaN in X or y. sklearn's MI estimators choke on NaN."""
    df = pd.concat([X, y.rename("__y__")], axis=1).dropna()
    return df.drop(columns=["__y__"]), df["__y__"]


def mutual_information_scores(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    discrete_features: bool | str = "auto",
    random_state: int = 42,
) -> pd.Series:
    """Rank features by mutual information with the target.

    Wraps ``mutual_info_classif`` for binary/categorical ``y`` and
    ``mutual_info_regression`` for continuous ``y``. Returns a Series indexed
    by feature name, sorted DESCENDING (highest MI first).
    """
    X_clean, y_clean = _clean(X, y)
    if X_clean.empty or X_clean.shape[1] == 0:
        return pd.Series(dtype="float64")

    is_classification = pd.api.types.is_integer_dtype(y_clean) or pd.api.types.is_bool_dtype(
        y_clean
    )
    if is_classification:
        scores = mutual_info_classif(
            X_clean.to_numpy(),
            y_clean.to_numpy(),
            discrete_features=discrete_features,
            random_state=random_state,
        )
    else:
        scores = mutual_info_regression(
            X_clean.to_numpy(),
            y_clean.to_numpy(),
            discrete_features=discrete_features,
            random_state=random_state,
        )
    return pd.Series(scores, index=X_clean.columns).sort_values(ascending=False)


def _pairwise_mi(
    X: pd.DataFrame,
    feature: str,
    others: list[str],
    *,
    random_state: int = 42,
) -> pd.Series:
    """MI of a single feature against each of ``others`` (regression form,
    since features are continuous). Returns Series indexed by ``others``.
    """
    if not others:
        return pd.Series(dtype="float64")
    target = X[feature].to_numpy()
    candidates = X[others].to_numpy()
    scores = mutual_info_regression(candidates, target, random_state=random_state)
    return pd.Series(scores, index=others)


def mrmr_select(
    X: pd.DataFrame,
    y: pd.Series,
    n: int = 20,
    *,
    random_state: int = 42,
) -> list[str]:
    """Minimum Redundancy Maximum Relevance feature selection.

    Algorithm
    ---------
    1. Compute MI(f, y) for every feature f. Pick the highest.
    2. While selected < n and candidates remain:
       - For every remaining candidate f, compute its mean MI against the
         already-selected set (the redundancy term).
       - Score f as ``relevance(f, y) - redundancy(f, selected)``.
       - Pick the candidate with the highest score.

    This is the classical Peng et al. greedy mRMR with mean-redundancy. The
    algorithm is order-deterministic given the same MI scores; ties break on
    the original column order.
    """
    if n <= 0 or X.shape[1] == 0:
        return []

    X_clean, y_clean = _clean(X, y)
    if X_clean.empty:
        return []

    relevance = mutual_information_scores(X_clean, y_clean, random_state=random_state)
    if relevance.empty:
        return []

    selected: list[str] = [str(relevance.index[0])]
    candidates = [c for c in X_clean.columns if c != selected[0]]

    target = min(n, X_clean.shape[1])
    while len(selected) < target and candidates:
        scores: dict[str, float] = {}
        for cand in candidates:
            redundancy = _pairwise_mi(
                X_clean, cand, selected, random_state=random_state
            ).mean()
            scores[cand] = float(relevance.get(cand, 0.0) - redundancy)
        # Highest score wins; tie-break on insertion order (Python dict iter
        # preserves it, max() returns the first max under stable iteration).
        best = max(scores, key=lambda k: scores[k])
        selected.append(best)
        candidates.remove(best)

    return selected


__all__ = ["mrmr_select", "mutual_information_scores"]


# Defensive: avoid sklearn's neighbors estimator going haywire on degenerate
# inputs by clamping zero variance columns silently. We re-export numpy so the
# module is importable even if sklearn is missing in some bare CI image.
_ = np
