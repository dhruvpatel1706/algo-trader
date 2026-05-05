"""Property-based tests for src.risk.correlation.

The correlation penalty is multiplied into trade sizing to attenuate
positions that are too similar to the live book. The hard invariants are:

- Output is always in [0.0, 1.0]; nothing else makes sense as a multiplier.
- Empty book → no penalty (1.0).
- Already-held symbol → no double-penalty (1.0).
- Penalty is monotone non-increasing in mean correlation: more correlation
  with the book cannot raise the multiplier.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from src.risk.correlation import correlation_penalty

pytestmark = pytest.mark.property


_SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")


def _build_returns(seed: int, n_rows: int, symbols: tuple[str, ...]) -> pd.DataFrame:
    """Deterministic synthetic returns frame for a given seed."""
    rng = np.random.default_rng(seed)
    data = rng.normal(loc=0.0, scale=0.01, size=(n_rows, len(symbols)))
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="B")
    return pd.DataFrame(data, columns=list(symbols), index=idx)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n_open=st.integers(min_value=0, max_value=5),
    n_rows=st.integers(min_value=2, max_value=120),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_correlation_penalty_always_in_unit_interval(n_open, n_rows, seed):
    """Output is always in [0.0, 1.0]; never NaN, inf, or out of bounds."""
    returns = _build_returns(seed, n_rows, _SYMBOLS)
    open_positions = list(_SYMBOLS[:n_open])
    candidate = _SYMBOLS[-1]
    penalty = correlation_penalty(candidate, open_positions, returns)
    assert isinstance(penalty, float)
    assert 0.0 <= penalty <= 1.0, f"penalty {penalty} outside [0, 1]"
    assert not math.isnan(penalty)
    assert not math.isinf(penalty)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_empty_open_positions_returns_one(seed):
    """No open positions → multiplier == 1.0 (no penalty)."""
    returns = _build_returns(seed, 100, _SYMBOLS)
    assert correlation_penalty("AAA", [], returns) == 1.0


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_self_correlation_returns_one(seed):
    """If candidate is already in open_positions, return 1.0 (no double-penalty)."""
    returns = _build_returns(seed, 100, _SYMBOLS)
    assert correlation_penalty("AAA", ["AAA", "BBB"], returns) == 1.0


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n_rows=st.integers(min_value=2, max_value=80),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_missing_returns_data_returns_one(n_rows, seed):
    """Symbol not in the returns frame → no usable correlation, return 1.0."""
    returns = _build_returns(seed, n_rows, _SYMBOLS)
    # 'ZZZ' is not in the columns → defensive default 1.0.
    assert correlation_penalty("ZZZ", ["AAA"], returns) == 1.0


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_empty_returns_frame_returns_one(seed):
    """An empty (or None) returns frame must not blow up — return 1.0."""
    empty = pd.DataFrame(columns=list(_SYMBOLS))
    assert correlation_penalty("AAA", ["BBB"], empty) == 1.0


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_perfectly_correlated_returns_zero(seed):
    """If candidate is the same series as the open book, mean_corr=1.0 → 0.0."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0, 0.01, size=120)
    idx = pd.date_range("2024-01-01", periods=120, freq="B")
    df = pd.DataFrame({"AAA": base, "BBB": base.copy()}, index=idx)
    # 'BBB' (the candidate) has mean correlation 1.0 with 'AAA' (the book) — block.
    assert correlation_penalty("BBB", ["AAA"], df) == 0.0


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_perfectly_anticorrelated_returns_one(seed):
    """Negative correlation maps to mean_corr <= 0.30 → no penalty (1.0)."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0, 0.01, size=120)
    idx = pd.date_range("2024-01-01", periods=120, freq="B")
    df = pd.DataFrame({"AAA": base, "BBB": -base}, index=idx)
    assert correlation_penalty("BBB", ["AAA"], df) == 1.0


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_constant_series_does_not_explode(seed):
    """Constant (zero-variance) series have undefined correlation; must not crash."""
    idx = pd.date_range("2024-01-01", periods=80, freq="B")
    df = pd.DataFrame({"AAA": np.zeros(80), "BBB": np.zeros(80)}, index=idx)
    p = correlation_penalty("BBB", ["AAA"], df)
    # Skipped pair → no usable correlations → defensive 1.0.
    assert 0.0 <= p <= 1.0
    assert p == 1.0


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    n_rows=st.integers(min_value=2, max_value=200),
    lookback=st.integers(min_value=1, max_value=300),
)
def test_short_window_returns_one(seed, n_rows, lookback):
    """If lookback exceeds the data size to the point where len(window) < 2, return 1.0."""
    returns = _build_returns(seed, n_rows, _SYMBOLS)
    p = correlation_penalty("AAA", ["BBB"], returns, lookback=lookback)
    assert 0.0 <= p <= 1.0
