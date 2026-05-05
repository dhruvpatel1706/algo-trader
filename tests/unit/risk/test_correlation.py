"""Unit tests for `src.risk.correlation.correlation_penalty`."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.risk.correlation import correlation_penalty


def _make_returns(n: int = 200, seed: int = 0, **named_series: np.ndarray) -> pd.DataFrame:
    """Build a wide returns DataFrame from named numpy arrays."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(named_series, index=idx)


def test_empty_open_positions_returns_one():
    rng = np.random.default_rng(0)
    df = _make_returns(SPY=rng.normal(0, 0.01, 200), QQQ=rng.normal(0, 0.01, 200))
    assert correlation_penalty("AAPL", [], df) == 1.0


def test_symbol_already_in_open_positions_returns_one():
    rng = np.random.default_rng(0)
    df = _make_returns(SPY=rng.normal(0, 0.01, 200), QQQ=rng.normal(0, 0.01, 200))
    # Even though SPY is in the book, sizing it again should not double-penalize
    # (the engine will reject the duplicate via "one position per symbol" rules).
    assert correlation_penalty("SPY", ["SPY", "QQQ"], df) == 1.0


def test_fully_correlated_returns_zero():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, 200)
    df = _make_returns(SPY=base, IVV=base, VOO=base)  # perfect 1:1 clones
    penalty = correlation_penalty("VOO", ["SPY", "IVV"], df)
    assert penalty == 0.0


def test_fully_uncorrelated_returns_one():
    # Independent normal draws => mean correlation ~= 0.0, well under the 0.30 floor.
    rng = np.random.default_rng(42)
    df = _make_returns(
        n=500,
        A=rng.normal(0, 0.01, 500),
        B=rng.normal(0, 0.01, 500),
        C=rng.normal(0, 0.01, 500),
    )
    penalty = correlation_penalty("C", ["A", "B"], df)
    assert penalty == pytest.approx(1.0, abs=1e-9)


def test_mid_correlation_is_linear_ramp():
    # Construct a candidate that is exactly 0.5 correlated with the open book
    # by mixing the open-book series with independent noise.
    rng = np.random.default_rng(7)
    n = 1000
    open_series = rng.normal(0, 0.01, n)
    noise = rng.normal(0, 0.01, n)
    candidate = 0.5 * open_series + np.sqrt(1 - 0.5**2) * noise
    df = _make_returns(n=n, OPEN=open_series, CAND=candidate)

    penalty = correlation_penalty("CAND", ["OPEN"], df, lookback=n)
    # Expected: at corr=0.5 -> 1.0 + (0.5-0.3)*(-1.25) = 0.75 (with sampling noise).
    assert 0.6 < penalty < 0.9


def test_missing_data_treated_as_uncorrelated():
    # Candidate column is entirely NaN => should fall through to 1.0.
    idx = pd.date_range("2024-01-02", periods=100, freq="B")
    df = pd.DataFrame(
        {"SPY": np.random.default_rng(0).normal(0, 0.01, 100), "BAD": [np.nan] * 100},
        index=idx,
    )
    assert correlation_penalty("BAD", ["SPY"], df) == 1.0


def test_missing_symbol_in_dataframe_returns_one():
    rng = np.random.default_rng(0)
    df = _make_returns(SPY=rng.normal(0, 0.01, 200))
    # The candidate is not present in the returns DataFrame at all.
    assert correlation_penalty("UNKNOWN", ["SPY"], df) == 1.0


def test_too_few_observations_returns_one():
    # Only one row => can't compute correlation => no penalty.
    idx = pd.date_range("2024-01-02", periods=1, freq="B")
    df = pd.DataFrame({"SPY": [0.01], "QQQ": [0.005]}, index=idx)
    assert correlation_penalty("QQQ_NEW", ["SPY"], df, lookback=63) == 1.0


def test_output_clamped_between_zero_and_one():
    # Sweep a range of synthetic correlations and confirm the output never escapes [0, 1].
    rng = np.random.default_rng(13)
    n = 500
    base = rng.normal(0, 0.01, n)
    for rho in (-0.8, -0.2, 0.0, 0.2, 0.5, 0.85, 0.99):
        noise = rng.normal(0, 0.01, n)
        cand = rho * base + np.sqrt(max(1 - rho**2, 0.0)) * noise
        df = _make_returns(n=n, OPEN=base, CAND=cand)
        penalty = correlation_penalty("CAND", ["OPEN"], df, lookback=n)
        assert 0.0 <= penalty <= 1.0


def test_constant_series_treated_as_uncorrelated():
    # An all-zero (constant) candidate has undefined correlation; should be 1.0.
    idx = pd.date_range("2024-01-02", periods=100, freq="B")
    df = pd.DataFrame(
        {"SPY": np.random.default_rng(0).normal(0, 0.01, 100), "FLAT": [0.0] * 100},
        index=idx,
    )
    assert correlation_penalty("FLAT", ["SPY"], df) == 1.0
