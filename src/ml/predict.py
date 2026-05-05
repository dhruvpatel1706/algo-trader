"""Pure inference helpers for the ML overlay.

The runtime path is intentionally trivial: the strategy emits a signal, the
overlay loads its blessed model once at startup, and ``predict_score`` is
called per signal to produce a probability that the signal will close
profitable. The downstream caller multiplies ``signal.confidence`` by this
probability — the overlay is a FILTER, never a generator.

Pickle integrity: the loader expects a ``.sha256`` sidecar next to every
model file. Loading without the sidecar, or with a hash mismatch, raises
``ModelIntegrityError`` before the unpickle runs. ``save_model`` writes both
the pickle and the sidecar atomically so the pair is always consistent.
This protects against an attacker who can write to the model directory but
not compute and write the matching digest in the same step (and against
silent disk corruption that flips bits in the artifact).
"""

from __future__ import annotations

import hashlib
import os
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

_DIGEST_SUFFIX = ".sha256"
_HASH_CHUNK = 1 << 16  # 64 KiB per read


class ModelIntegrityError(RuntimeError):
    """Raised when a model file's hash does not match its sidecar digest."""


def _digest_path(model_path: Path) -> Path:
    return model_path.with_name(model_path.name + _DIGEST_SUFFIX)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def load_model(path: Path) -> lgb.LGBMClassifier:
    """Load a trained LightGBM model from a pickle file written by training.

    We use ``pickle`` rather than ``Booster.save_model`` because the wrapped
    ``LGBMClassifier`` carries useful metadata (``feature_name_``, classes,
    sklearn parameters) that the runtime relies on.

    Pickle is RCE-on-load if the bytes are attacker-controlled. We require a
    matching ``.sha256`` sidecar — written by ``save_model`` — and verify it
    before unpickling. Loading without the sidecar, or against a mismatched
    digest, raises ``ModelIntegrityError``.
    """
    p = Path(path)
    digest_path = _digest_path(p)
    if not digest_path.exists():
        raise ModelIntegrityError(
            f"missing sha256 sidecar for {p!s}; refusing to unpickle untrusted bytes"
        )
    expected = digest_path.read_text(encoding="utf-8").strip()
    actual = _hash_file(p)
    if expected.lower() != actual.lower():
        raise ModelIntegrityError(
            f"sha256 mismatch for {p!s}: sidecar={expected!r}, file={actual!r}"
        )
    with p.open("rb") as fh:
        model = pickle.load(fh)  # noqa: S301 — verified by sha256 sidecar above
    if not isinstance(model, lgb.LGBMClassifier):
        raise TypeError(f"Expected LGBMClassifier, got {type(model).__name__}")
    return model


def save_model(model: lgb.LGBMClassifier, path: Path) -> None:
    """Write the model pickle and a SHA256 sidecar atomically.

    Symmetric counterpart to ``load_model``. The sidecar is written *after*
    the pickle bytes have hit disk, so a partial write leaves the loader
    refusing to load (sidecar missing or stale) rather than running a
    half-written pickle.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as fh:
        pickle.dump(model, fh)
        fh.flush()
        os.fsync(fh.fileno())
    digest = _hash_file(p)
    digest_path = _digest_path(p)
    digest_path.write_text(digest + "\n", encoding="utf-8")


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


__all__ = ["ModelIntegrityError", "load_model", "predict_score", "save_model"]
