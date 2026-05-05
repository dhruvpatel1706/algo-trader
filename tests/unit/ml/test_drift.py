"""Tests for src.ml.drift."""

from __future__ import annotations

import math

import numpy as np
from src.ml.drift import coherence_ratio, drift_alert, population_stability_index


def test_psi_zero_when_distributions_match():
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000)
    b = rng.standard_normal(2000)
    psi = population_stability_index(a, b, bins=10)
    assert psi < 0.05


def test_psi_grows_when_distribution_shifts():
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000)
    b = rng.standard_normal(2000) + 2.0  # mean shift
    psi = population_stability_index(a, b, bins=10)
    assert psi > 0.2


def test_psi_handles_empty_inputs():
    assert population_stability_index(np.array([]), np.array([1.0, 2.0])) == 0.0
    assert population_stability_index(np.array([1.0, 2.0]), np.array([])) == 0.0


def test_psi_handles_constant_expected():
    a = np.ones(100)
    b = np.ones(100) + 0.01
    # When edges collapse, PSI returns 0 (no informative bins).
    assert population_stability_index(a, b) == 0.0


def test_coherence_ratio_nan_when_no_live_trades():
    assert math.isnan(coherence_ratio(0, 0, 0.55))


def test_coherence_ratio_basic():
    # 10 wins out of 20 = 0.5 live, vs 0.55 backtest -> ratio < 1.
    ratio = coherence_ratio(10, 20, 0.55)
    assert abs(ratio - (0.5 / 0.55)) < 1e-9


def test_coherence_ratio_nan_when_backtest_wr_zero():
    assert math.isnan(coherence_ratio(5, 10, 0.0))


def test_drift_alert_none_when_clean():
    out = drift_alert({"rsi": 0.05, "atr": 0.1}, coherence=1.1)
    assert out is None


def test_drift_alert_lists_psi_offenders():
    out = drift_alert({"rsi": 0.5, "atr": 0.1}, coherence=1.0)
    assert out is not None
    assert "rsi" in out
    assert "atr" not in out  # below threshold


def test_drift_alert_flags_low_coherence():
    out = drift_alert({"rsi": 0.05}, coherence=0.3, coherence_threshold=0.5)
    assert out is not None
    assert "coherence" in out
