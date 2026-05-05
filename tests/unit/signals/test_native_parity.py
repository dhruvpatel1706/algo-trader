"""Pure-Python ↔ Rust parity tests for the indicator engine.

Skipped when the Rust extension hasn't been built. When it has been built,
every function in the native engine MUST produce numerically identical output
to the pandas/Python path within tight tolerances. The Rust scaffold's whole
point is byte-for-byte equivalence — divergence here is a real bug, not an
acceptable optimization trade-off.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

native = pytest.importorskip("signal_engine_native")
if not native.HAVE_NATIVE:
    pytest.skip("native extension built but HAVE_NATIVE flag is False", allow_module_level=True)


def _equal(a: np.ndarray, b: np.ndarray, *, rtol: float = 1e-9, atol: float = 1e-12) -> None:
    """Compare two arrays treating NaN as equal."""
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    assert a_arr.shape == b_arr.shape, f"shape mismatch: {a_arr.shape} vs {b_arr.shape}"
    nan_a = np.isnan(a_arr)
    nan_b = np.isnan(b_arr)
    assert np.array_equal(nan_a, nan_b), "NaN positions differ between native and Python"
    mask = ~nan_a
    assert np.allclose(a_arr[mask], b_arr[mask], rtol=rtol, atol=atol)


@pytest.fixture
def synthetic_close() -> pd.Series:
    rng = np.random.default_rng(20260505)
    n = 500
    # Geometric brownian motion-ish: starts near 100, daily ~1% vol.
    log_returns = rng.normal(0.0003, 0.012, n)
    return pd.Series(100.0 * np.exp(np.cumsum(log_returns)))


@pytest.fixture
def synthetic_ohlc(synthetic_close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(20260506)
    spread = rng.uniform(0.001, 0.02, len(synthetic_close)) * synthetic_close
    high = synthetic_close + spread
    low = synthetic_close - spread
    return high, low, synthetic_close


@pytest.mark.parametrize("period", [3, 14, 20, 50])
def test_sma_matches_pandas(synthetic_close: pd.Series, period: int) -> None:
    arr = np.ascontiguousarray(synthetic_close.to_numpy(dtype=np.float64))
    native_out = native.sma(arr, period)
    python_out = synthetic_close.rolling(period, min_periods=period).mean().to_numpy()
    _equal(native_out, python_out)


@pytest.mark.parametrize("period", [3, 12, 26])
def test_ema_matches_pandas(synthetic_close: pd.Series, period: int) -> None:
    arr = np.ascontiguousarray(synthetic_close.to_numpy(dtype=np.float64))
    native_out = native.ema(arr, period)
    # The Rust path seeds EMA with a simple mean of the first `period` values
    # and then applies the Wilder-style recurrence. To match this exactly,
    # slice off the warm-up region from pandas' .ewm output (which uses an
    # exponentially-weighted seed) and replace it with the equivalent simple
    # mean — that's the documented contract on both sides.
    python_seed = float(synthetic_close.iloc[:period].mean())
    alpha = 2.0 / (period + 1.0)
    python_out = np.full(len(synthetic_close), np.nan)
    python_out[period - 1] = python_seed
    val = python_seed
    closes = synthetic_close.to_numpy(dtype=np.float64)
    for i in range(period, len(synthetic_close)):
        val = alpha * closes[i] + (1.0 - alpha) * val
        python_out[i] = val
    _equal(native_out, python_out)


@pytest.mark.parametrize("period", [5, 14, 22])
def test_atr_matches_wilder(synthetic_ohlc, period: int) -> None:
    high, low, close = synthetic_ohlc
    h = np.ascontiguousarray(high.to_numpy(dtype=np.float64))
    l_ = np.ascontiguousarray(low.to_numpy(dtype=np.float64))
    c = np.ascontiguousarray(close.to_numpy(dtype=np.float64))
    native_out = native.atr(h, l_, c, period)

    # Reference Wilder-smoothed ATR (matches what `ta.AverageTrueRange` does).
    n = len(close)
    tr = np.full(n, np.nan)
    tr[0] = h[0] - l_[0]
    for i in range(1, n):
        tr[i] = max(
            h[i] - l_[i],
            abs(h[i] - c[i - 1]),
            abs(l_[i] - c[i - 1]),
        )
    expected = np.full(n, np.nan)
    expected[period - 1] = float(np.mean(tr[:period]))
    val = expected[period - 1]
    p = float(period)
    for i in range(period, n):
        val = (val * (p - 1.0) + tr[i]) / p
        expected[i] = val
    _equal(native_out, expected)


@pytest.mark.parametrize("period", [10, 22])
def test_williams_vix_fix_matches_python(synthetic_ohlc, period: int) -> None:
    _, low, close = synthetic_ohlc
    c = np.ascontiguousarray(close.to_numpy(dtype=np.float64))
    l_ = np.ascontiguousarray(low.to_numpy(dtype=np.float64))
    native_out = native.williams_vix_fix(c, l_, period)
    highest = close.rolling(period, min_periods=period).max()
    python_out = ((highest - low) / highest * 100).to_numpy()
    _equal(native_out, python_out)


def test_facade_uses_native_when_env_set(monkeypatch, synthetic_close: pd.Series) -> None:
    """When ALGOTRADER_NATIVE_INDICATORS=1, the facade routes to Rust."""
    monkeypatch.setenv("ALGOTRADER_NATIVE_INDICATORS", "1")
    # Re-import so the env var is picked up.
    import importlib

    import src.signals.indicators as ind
    ind = importlib.reload(ind)
    try:
        assert ind._NATIVE_AVAILABLE
        out = ind.sma(synthetic_close, 20)
        # Compare native facade output to pandas reference at non-NaN positions.
        ref = synthetic_close.rolling(20, min_periods=20).mean()
        _equal(out.to_numpy(), ref.to_numpy())
    finally:
        # Reset the module so other tests see the default (no-native) path.
        monkeypatch.delenv("ALGOTRADER_NATIVE_INDICATORS", raising=False)
        importlib.reload(ind)
