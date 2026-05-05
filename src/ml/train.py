"""Training pipeline for the ML overlay classifier.

Pipeline (matches the system plan):

1. Drop NaN rows from ``X``/``y``.
2. Carve out a contiguous tail holdout (or use ``holdout_dates`` if supplied).
3. On the non-holdout block, run feature selection (mRMR, ``n_features``).
4. Run PURGED + EMBARGOED walk-forward CV to pick a model and produce the
   in-sample sharpe estimate. Embargo defaults to 5 days (López de Prado).
5. Refit the final LightGBM classifier on the full non-holdout block with the
   selected features.
6. Score the holdout block once. ``blessed = True`` iff
   ``train_sharpe / max(holdout_sharpe, 0.01) <= overfit_ratio_threshold``.

The dollar-PnL series we use to compute Sharpe is the predicted-probability
projected onto the realised label (``2*y - 1``) — i.e. high probability + win
contributes positively, high probability + loss contributes negatively. This
gives us a target-aware risk-adjusted metric without ever needing realised
returns.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from src.ml.selection import mrmr_select


@dataclass(frozen=True, slots=True)
class TrainResult:
    """Outcome of a single training run."""

    model: lgb.LGBMClassifier
    feature_names: tuple[str, ...]
    train_metrics: dict
    holdout_metrics: dict
    blessed: bool


def purged_walk_forward_splits(
    n_samples: int,
    n_splits: int = 5,
    embargo_days: int = 5,
    test_fraction: float = 0.2,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """PURGED + EMBARGOED walk-forward CV (López de Prado).

    Splits a time-ordered sample into ``n_splits`` contiguous test folds at the
    tail. For each fold, the train set is everything strictly before the test
    region MINUS an ``embargo_days`` buffer on both sides. (For chronological
    walk-forward the post-test region is naturally not in train, but we still
    purge the gap on the leading edge so models don't peek at samples whose
    labels overlap the test region.)

    Returns a list of ``(train_idx, test_idx)`` numpy arrays.
    """
    if n_samples <= 0 or n_splits <= 0:
        return []

    test_size = max(round(n_samples * test_fraction / n_splits), 1)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(n_splits):
        test_end = n_samples - k * test_size
        test_start = max(test_end - test_size, 0)
        if test_start <= 0:
            break
        train_end = max(test_start - embargo_days, 0)
        if train_end <= 0:
            continue
        train_idx = np.arange(0, train_end, dtype=np.int64)
        test_idx = np.arange(test_start, test_end, dtype=np.int64)
        splits.append((train_idx, test_idx))
    # Return in chronological order (oldest fold first) for readability.
    splits.reverse()
    return splits


def _sharpe(pnl: np.ndarray) -> float:
    """Annualised Sharpe of a daily PnL series. Returns 0.0 for zero-vol or
    empty series — never NaN, never Inf. Annualisation factor 252."""
    if pnl.size == 0:
        return 0.0
    sd = float(np.std(pnl, ddof=1)) if pnl.size > 1 else 0.0
    if sd <= 0.0:
        return 0.0
    return float(np.mean(pnl) / sd * np.sqrt(252))


def _proxy_pnl(proba: np.ndarray, y_true: np.ndarray) -> np.ndarray:
    """Per-sample proxy PnL: ``(proba - 0.5) * (2 * y_true - 1)``. Positive when
    the model is confident AND right; negative when confident AND wrong."""
    return (proba - 0.5) * (2.0 * y_true - 1.0)


def _safe_auc(y_true: np.ndarray, proba: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, proba))


def _split_holdout(
    X: pd.DataFrame,
    y: pd.Series,
    holdout_dates: tuple[pd.Timestamp, pd.Timestamp] | None,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Split into (train_X, train_y, holdout_X, holdout_y)."""
    if holdout_dates is None:
        # Default: last 20% of samples are holdout.
        cutoff = int(len(X) * 0.8)
        return (
            X.iloc[:cutoff],
            y.iloc[:cutoff],
            X.iloc[cutoff:],
            y.iloc[cutoff:],
        )
    start, end = holdout_dates
    # Use the timestamp level if multi-indexed; otherwise the index itself.
    timestamps = (
        X.index.get_level_values("timestamp")
        if "timestamp" in (X.index.names or [])
        else X.index
    )
    holdout_mask = (timestamps >= start) & (timestamps <= end)
    train_mask = ~holdout_mask
    return X[train_mask], y[train_mask], X[holdout_mask], y[holdout_mask]


def train_model(
    X: pd.DataFrame,
    y: pd.Series,
    holdout_dates: tuple[pd.Timestamp, pd.Timestamp] | None = None,
    n_features: int = 20,
    overfit_ratio_threshold: float = 2.0,
    *,
    n_splits: int = 5,
    embargo_days: int = 5,
    random_state: int = 42,
) -> TrainResult:
    """Run the full overlay training pipeline.

    See module docstring for the step-by-step contract. The returned
    ``TrainResult.blessed`` flag is the ONLY signal the runtime should consult
    when deciding whether a freshly trained model is allowed into the
    champion/challenger pool.
    """
    # 1) Clean.
    df = pd.concat([X, y.rename("__y__")], axis=1).dropna()
    if df.empty:
        raise ValueError("train_model received no usable rows after dropna")
    X_clean = df.drop(columns=["__y__"])
    y_clean = df["__y__"].astype("int64")

    # 2) Holdout split.
    X_train, y_train, X_hold, y_hold = _split_holdout(X_clean, y_clean, holdout_dates)
    if len(X_train) == 0:
        raise ValueError("train_model: empty train split")

    # 3) Feature selection on TRAIN ONLY (no peeking into holdout).
    selected = mrmr_select(X_train, y_train, n=n_features, random_state=random_state)
    if not selected:
        # Fall back to all columns; selection failure shouldn't block training.
        selected = list(X_train.columns)
    X_train_sel = X_train[selected]
    X_hold_sel = X_hold[selected] if not X_hold.empty else X_hold

    # 4) PURGED + EMBARGOED walk-forward CV for the in-sample sharpe estimate.
    cv_pnls: list[float] = []
    cv_aucs: list[float] = []
    splits = purged_walk_forward_splits(
        len(X_train_sel),
        n_splits=n_splits,
        embargo_days=embargo_days,
    )
    for train_idx, test_idx in splits:
        if len(np.unique(y_train.iloc[train_idx])) < 2:
            continue
        fold_model = lgb.LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            objective="binary",
            random_state=random_state,
            verbose=-1,
        )
        fold_model.fit(X_train_sel.iloc[train_idx], y_train.iloc[train_idx])
        proba = fold_model.predict_proba(X_train_sel.iloc[test_idx])[:, 1]
        cv_pnls.extend(_proxy_pnl(proba, y_train.iloc[test_idx].to_numpy()).tolist())
        cv_aucs.append(_safe_auc(y_train.iloc[test_idx].to_numpy(), proba))

    train_pnl_arr = np.asarray(cv_pnls, dtype="float64")
    train_sharpe = _sharpe(train_pnl_arr)
    train_auc = float(np.nanmean(cv_aucs)) if cv_aucs else float("nan")

    # 5) Refit on full train block.
    final_model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        objective="binary",
        random_state=random_state,
        verbose=-1,
    )
    final_model.fit(X_train_sel, y_train)

    # 6) Score holdout.
    if not X_hold_sel.empty and len(np.unique(y_hold)) >= 1:
        hold_proba = final_model.predict_proba(X_hold_sel)[:, 1]
        hold_pnl = _proxy_pnl(hold_proba, y_hold.to_numpy())
        holdout_sharpe = _sharpe(hold_pnl)
        holdout_auc = _safe_auc(y_hold.to_numpy(), hold_proba)
    else:
        holdout_sharpe = 0.0
        holdout_auc = float("nan")

    # Blessed iff overfit ratio is within bounds. The ``max(_, 0.01)`` clamp
    # avoids division-by-zero blowing up the gate when holdout sharpe is small
    # negative or zero. With holdout_sharpe<=0 the ratio is >>threshold so the
    # gate correctly rejects.
    ratio = train_sharpe / max(holdout_sharpe, 0.01)
    blessed = bool(ratio <= overfit_ratio_threshold and train_sharpe > 0.0)

    train_metrics = {
        "sharpe": train_sharpe,
        "auc_mean": train_auc,
        "n_folds": len(splits),
        "n_samples": len(X_train_sel),
    }
    holdout_metrics = {
        "sharpe": holdout_sharpe,
        "auc": holdout_auc,
        "n_samples": len(X_hold_sel) if not X_hold_sel.empty else 0,
        "overfit_ratio": ratio,
    }

    return TrainResult(
        model=final_model,
        feature_names=tuple(selected),
        train_metrics=train_metrics,
        holdout_metrics=holdout_metrics,
        blessed=blessed,
    )


__all__ = ["TrainResult", "purged_walk_forward_splits", "train_model"]
