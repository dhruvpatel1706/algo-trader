"""Drift detection for the ML overlay.

Two complementary metrics:

- ``population_stability_index`` (PSI): per-feature distribution drift between
  the training distribution (``expected``) and a recent live window
  (``actual``). PSI < 0.1 = stable, 0.1-0.2 = moderate, > 0.2 = significant.
- ``coherence_ratio``: live win-rate divided by backtested win-rate. The
  champion/challenger gate uses this to decide whether a model behaves in
  production the way it did in training.

``drift_alert`` combines both into a single human-readable banner string the
runtime can surface in the dashboard.
"""

from __future__ import annotations

import math

import numpy as np


def population_stability_index(
    expected: np.ndarray,
    actual: np.ndarray,
    bins: int = 10,
) -> float:
    """Population Stability Index. Equal-frequency bin edges from ``expected``;
    proportions in each bin compared between the two distributions.

    Empty bins are floored to a small constant so the log term is finite.
    Returns 0.0 when the inputs are empty (no data to drift on).
    """
    expected_arr = np.asarray(expected, dtype="float64")
    actual_arr = np.asarray(actual, dtype="float64")
    expected_arr = expected_arr[~np.isnan(expected_arr)]
    actual_arr = actual_arr[~np.isnan(actual_arr)]
    if expected_arr.size == 0 or actual_arr.size == 0:
        return 0.0

    # Equal-frequency edges from the expected distribution.
    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.quantile(expected_arr, quantiles)
    # Collapse duplicate edges (constant or near-constant features).
    edges = np.unique(edges)
    if edges.size < 2:
        return 0.0
    edges[0] = -np.inf
    edges[-1] = np.inf

    expected_hist, _ = np.histogram(expected_arr, bins=edges)
    actual_hist, _ = np.histogram(actual_arr, bins=edges)
    expected_pct = expected_hist / expected_arr.size
    actual_pct = actual_hist / actual_arr.size
    floor = 1e-6
    expected_pct = np.where(expected_pct == 0, floor, expected_pct)
    actual_pct = np.where(actual_pct == 0, floor, actual_pct)

    psi = float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))
    return psi


def coherence_ratio(live_wins: int, live_total: int, backtest_wr: float) -> float:
    """``live_WR / backtest_WR``. NaN when there are no live trades yet, or
    when the backtest WR is zero (undefined ratio)."""
    if live_total <= 0:
        return float("nan")
    if backtest_wr <= 0.0:
        return float("nan")
    live_wr = live_wins / live_total
    return live_wr / backtest_wr


def drift_alert(
    feature_psi: dict[str, float],
    coherence: float,
    psi_threshold: float = 0.2,
    coherence_threshold: float = 0.5,
) -> str | None:
    """Compose a human-readable alert. Returns ``None`` when nothing breached
    its threshold; otherwise a single line listing the worst offenders.

    The coherence threshold is interpreted as a LOWER bound — alert when live
    win-rate is below ``threshold * backtest_wr`` (default 50%).
    """
    parts: list[str] = []
    drifted = sorted(
        ((name, psi) for name, psi in feature_psi.items() if psi > psi_threshold),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if drifted:
        details = ", ".join(f"{name}={psi:.2f}" for name, psi in drifted)
        parts.append(f"feature drift: {details} (PSI > {psi_threshold:.2f})")
    if not math.isnan(coherence) and coherence < coherence_threshold:
        parts.append(f"coherence={coherence:.2f} below {coherence_threshold:.2f}")
    if not parts:
        return None
    return "; ".join(parts)


__all__ = ["coherence_ratio", "drift_alert", "population_stability_index"]
