"""Pure inference helpers for the ML overlay.

The runtime path is intentionally trivial: the strategy emits a signal, the
overlay loads its blessed model once at startup, and ``predict_score`` is
called per signal to produce a probability that the signal will close
profitable. The downstream caller multiplies ``signal.confidence`` by this
probability — the overlay is a FILTER, never a generator.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd


def load_model(path: Path) -> lgb.LGBMClassifier:
    """Load a trained LightGBM model from a pickle file written by training.

    We use ``pickle`` rather than ``Booster.save_model`` because the wrapped
    ``LGBMClassifier`` carries useful metadata (``feature_name_``, classes,
    sklearn parameters) that the runtime relies on.
    """
    p = Path(path)
    with p.open("rb") as fh:
        model = pickle.load(fh)  # noqa: S301 - models are written by us
    if not isinstance(model, lgb.LGBMClassifier):
        raise TypeError(f"Expected LGBMClassifier, got {type(model).__name__}")
    return model


def save_model(model: lgb.LGBMClassifier, path: Path) -> None:
    """Symmetric counterpart to ``load_model``. Kept here so the round-trip
    contract is testable without dragging the train module into runtime."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as fh:
        pickle.dump(model, fh)


def predict_score(
    model: lgb.LGBMClassifier,
    feature_row: pd.Series,
) -> float:
    """Return P(profitable) for a single signal's feature row.

    The row's index is matched against ``model.feature_name_`` so columns can
    be passed in any order. Any missing feature falls back to NaN — LightGBM
    handles NaN natively. The result is clipped to ``[0, 1]`` defensively.
    """
    feature_names = list(getattr(model, "feature_name_", feature_row.index))
    aligned = feature_row.reindex(feature_names)
    # Pass as a one-row DataFrame so LightGBM keeps feature-name validation
    # active (and emits a clear error when names mismatch instead of warning).
    frame = pd.DataFrame([aligned.to_numpy(dtype="float64")], columns=feature_names)
    proba = model.predict_proba(frame)[0, 1]
    return float(np.clip(proba, 0.0, 1.0))


__all__ = ["load_model", "predict_score", "save_model"]
