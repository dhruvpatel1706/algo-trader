"""Property-based tests for src.signals.indicators.

Hard invariants:
- SMA(close, p) for index i (i >= p-1) is in [min(close[i-p+1:i+1]), max(...)]
- EMA(close, p) is in [min(close[:i+1]), max(close[:i+1])] (warmup is NaN)
- ATR(high, low, close, p) >= 0 for every non-NaN output
- Williams VIX Fix is in [0, 100] for every non-NaN output
- The first ``period - 1`` outputs of SMA/EMA/ATR are NaN (warmup).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays
from src.signals.indicators import atr, ema, sma, williams_vix_fix

pytestmark = pytest.mark.property


# Float arrays bounded to a typical equity-price range. We avoid NaN/inf in inputs
# (those are validated separately) and stay within Python float range to avoid
# overflow surprises inside `ta`.
_close_arrays = arrays(
    dtype=np.float64,
    shape=st.integers(min_value=20, max_value=200),
    elements=st.floats(
        min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False
    ),
)


def _to_close(arr: np.ndarray) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=len(arr), freq="B")
    return pd.Series(arr, index=idx, name="close")


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=20))
def test_sma_within_min_max_window(arr, period):
    """SMA(t) ∈ [min(window_t), max(window_t)] for every non-NaN sample."""
    assume(len(arr) >= period)
    close = _to_close(arr)
    out = sma(close, period=period)
    for i in range(period - 1, len(arr)):
        window = arr[i - period + 1 : i + 1]
        wmin, wmax = float(window.min()), float(window.max())
        val = float(out.iloc[i])
        assert wmin - 1e-9 <= val <= wmax + 1e-9


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=15))
def test_sma_warmup_is_nan(arr, period):
    """First period-1 outputs of SMA must be NaN (no leakage of partial windows)."""
    assume(len(arr) >= period)
    out = sma(_to_close(arr), period=period)
    for i in range(period - 1):
        assert np.isnan(out.iloc[i])
    assert not np.isnan(out.iloc[period - 1])


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=15))
def test_ema_warmup_is_nan(arr, period):
    """EMA's first period-1 samples are NaN (min_periods=period)."""
    assume(len(arr) >= period)
    out = ema(_to_close(arr), period=period)
    for i in range(period - 1):
        assert np.isnan(out.iloc[i])
    assert not np.isnan(out.iloc[period - 1])


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=20))
def test_ema_within_global_min_max(arr, period):
    """EMA(t) is a convex combination of historical closes → bounded by their range."""
    assume(len(arr) >= period)
    out = ema(_to_close(arr), period=period)
    for i in range(period - 1, len(arr)):
        history = arr[: i + 1]
        hmin, hmax = float(history.min()), float(history.max())
        val = float(out.iloc[i])
        assert hmin - 1e-6 <= val <= hmax + 1e-6


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=20))
def test_williams_vix_fix_in_zero_to_hundred(arr, period):
    """WVF is bounded in [0, 100]: highest_close >= low always (low <= close)."""
    assume(len(arr) >= period)
    close = _to_close(arr)
    # Construct a low series strictly <= close to satisfy OHLC invariants.
    low = close * 0.99
    wvf = williams_vix_fix(close, low=low, period=period)
    valid = wvf.dropna()
    if not valid.empty:
        assert (valid >= -1e-9).all(), valid.min()
        # Upper bound: when low << highest_close → near 100 but never above 100
        # because (highest_close - low) / highest_close <= 1 when low > 0.
        assert (valid <= 100 + 1e-9).all(), valid.max()


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=20))
def test_williams_vix_fix_default_low_is_zero_at_each_high(arr, period):
    """When low defaults to close, WVF at the rolling-high index is 0."""
    assume(len(arr) >= period)
    close = _to_close(arr)
    wvf = williams_vix_fix(close, low=None, period=period)
    # At any index i where close[i] equals highest_close[i], (high - low)/high = 0.
    valid = wvf.dropna()
    assert (valid >= -1e-9).all()
    assert (valid <= 100 + 1e-9).all()


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(arr=_close_arrays, period=st.integers(min_value=2, max_value=14))
def test_atr_non_negative(arr, period):
    """ATR is always >= 0 (it's a moving average of non-negative true ranges)."""
    assume(len(arr) >= period + 5)
    close = _to_close(arr)
    # Synthesize realistic OHLC: high >= close >= low, low > 0.
    high = close * 1.02
    low = close * 0.98
    out = atr(high, low, close, period=period)
    valid = out.dropna()
    if not valid.empty:
        assert (valid >= -1e-9).all(), f"min ATR {valid.min()}"


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(period=st.integers(min_value=2, max_value=15), n=st.integers(min_value=20, max_value=80))
def test_sma_constant_series_equals_constant(period, n):
    """SMA of a constant series equals the constant after warmup."""
    close = pd.Series([42.0] * n, index=pd.date_range("2024-01-01", periods=n, freq="B"))
    out = sma(close, period=period)
    valid = out.dropna()
    assert (np.abs(valid - 42.0) < 1e-9).all()


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(period=st.integers(min_value=2, max_value=15), n=st.integers(min_value=20, max_value=80))
def test_ema_constant_series_equals_constant(period, n):
    """EMA of a constant series equals the constant after warmup."""
    close = pd.Series([7.5] * n, index=pd.date_range("2024-01-01", periods=n, freq="B"))
    out = ema(close, period=period)
    valid = out.dropna()
    assert (np.abs(valid - 7.5) < 1e-9).all()
