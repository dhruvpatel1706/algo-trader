"""Property-based tests for src.backtest.metrics.

These metrics feed the promotion gate, so a NaN/inf leak here is a path
straight to a flaky promotion decision. Hard invariants:

- max_drawdown is always in [0, 1].
- annualized_sharpe / annualized_sortino are finite for any well-formed input
  and 0.0 for zero-variance / degenerate cases.
- profit_factor is non-negative.
- win_rate is in [0, 1].
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays
from src.backtest.metrics import (
    TradeRecord,
    annualized_sharpe,
    annualized_sortino,
    expectancy,
    max_drawdown,
    profit_factor,
    summarize,
    win_rate,
)

pytestmark = pytest.mark.property


_returns_arrays = arrays(
    dtype=np.float64,
    shape=st.integers(min_value=2, max_value=300),
    elements=st.floats(
        min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False
    ),
)


def _to_returns(arr: np.ndarray) -> pd.Series:
    return pd.Series(arr, index=pd.date_range("2024-01-01", periods=len(arr), freq="B"))


def _to_equity(arr: np.ndarray) -> pd.Series:
    """Strictly positive equity from compounding the returns from a $1 base."""
    eq = (1.0 + arr).cumprod()
    # Guarantee positivity even if the random walk dipped into negative territory.
    eq = np.maximum(eq, 1e-9)
    return pd.Series(eq, index=pd.date_range("2024-01-01", periods=len(arr), freq="B"))


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_returns_arrays)
def test_max_drawdown_in_zero_to_one(arr):
    """max_drawdown is a fraction of peak — must be in [0, 1]."""
    eq = _to_equity(arr)
    dd = max_drawdown(eq)
    assert 0.0 <= dd <= 1.0 + 1e-9
    assert not math.isnan(dd)
    assert not math.isinf(dd)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_returns_arrays)
def test_max_drawdown_zero_when_monotonic(arr):
    """Monotonically non-decreasing equity → drawdown is 0."""
    # Build a strictly increasing equity curve.
    eq = pd.Series(
        np.cumsum(np.abs(arr)) + 1.0,
        index=pd.date_range("2024-01-01", periods=len(arr), freq="B"),
    )
    dd = max_drawdown(eq)
    assert dd == 0.0


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_returns_arrays)
def test_sharpe_is_finite(arr):
    """Sharpe must be a finite float — no NaN, no inf — for well-formed returns."""
    s = annualized_sharpe(_to_returns(arr))
    assert isinstance(s, float)
    assert not math.isnan(s)
    assert not math.isinf(s)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(min_value=2, max_value=300))
def test_sharpe_zero_variance_returns_zero(n):
    """All-zero returns → sharpe is 0.0, never NaN."""
    s = annualized_sharpe(pd.Series([0.0] * n))
    assert s == 0.0


def test_sharpe_zero_variance_constant_nonzero_returns_zero():
    """Hypothesis-found edge case: constant non-zero returns slip past `sd == 0` guard.

    Originally skipped after this property test surfaced the bug: a `pd.Series`
    of `[0.001]*n` is mathematically constant (std = 0), but pandas computes
    `std(ddof=1) ~= 2e-19` due to floating-point error. The old `if sd == 0`
    guard missed it and the function returned ~7e+16 instead of 0.0.

    Fix landed in `src/backtest/metrics.py` — replaced the equality test with a
    1e-12 std floor (well below any real-world return std, well above f64 noise).
    Same fix applied to `annualized_sortino`. This test now serves as a
    regression guard.
    """
    s2 = annualized_sharpe(pd.Series([0.001] * 10))
    assert s2 == 0.0


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(min_value=0, max_value=1))
def test_sharpe_too_few_samples_returns_zero(n):
    """Length < 2 must short-circuit to 0.0, never raise."""
    s = annualized_sharpe(pd.Series([0.05] * n))
    assert s == 0.0


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_returns_arrays)
def test_sortino_is_finite(arr):
    s = annualized_sortino(_to_returns(arr))
    assert isinstance(s, float)
    assert not math.isnan(s)
    assert not math.isinf(s)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(min_value=2, max_value=200))
def test_sortino_no_downside_returns_zero(n):
    """If no downside observations, sortino is 0.0 (not NaN/inf)."""
    s = annualized_sortino(pd.Series([0.01] * n))
    assert s == 0.0


def _trade(pnl: float) -> TradeRecord:
    ts = pd.Timestamp("2024-01-01")
    return TradeRecord(
        symbol="X",
        side="buy",
        entry_ts=ts,
        exit_ts=ts,
        qty=1,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        strategy_tag="t",
    )


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    pnls=st.lists(
        st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        min_size=0,
        max_size=100,
    )
)
def test_profit_factor_non_negative(pnls):
    """Profit factor is wins/losses with both >= 0 → result >= 0 (or +inf)."""
    pf = profit_factor([_trade(p) for p in pnls])
    assert pf >= 0.0


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    pnls=st.lists(
        st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        min_size=0,
        max_size=100,
    )
)
def test_win_rate_in_unit_interval(pnls):
    wr = win_rate([_trade(p) for p in pnls])
    assert 0.0 <= wr <= 1.0


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    pnls=st.lists(
        st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=100,
    )
)
def test_expectancy_is_finite(pnls):
    e = expectancy([_trade(p) for p in pnls])
    assert isinstance(e, float)
    assert not math.isnan(e)
    assert not math.isinf(e)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_returns_arrays)
def test_summarize_returns_finite_keys(arr):
    """summarize() must produce floats (not NaN/inf) for the canonical metrics."""
    eq = _to_equity(arr)
    rs = _to_returns(arr)
    out = summarize(eq, rs, [])
    for key in ("sharpe", "sortino", "calmar", "max_dd"):
        v = out[key]
        assert isinstance(v, float)
        assert not math.isnan(v), f"{key} is NaN"
        assert not math.isinf(v), f"{key} is inf"
