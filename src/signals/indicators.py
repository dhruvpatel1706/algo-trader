"""Indicator helpers (thin wrappers over the `ta` library).

System of record is pandas/Python here. An OPTIONAL Rust hot-path mirror lives
at `crates/signal-engine/` and produces byte-for-byte identical results when
built (`maturin develop --release`). Set `ALGOTRADER_NATIVE_INDICATORS=1` to
opt in if profiling shows indicator computation as a real bottleneck — see
`docs/performance.md`. Default behavior is identical with or without Rust.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands

_USE_NATIVE = os.environ.get("ALGOTRADER_NATIVE_INDICATORS", "0") == "1"
try:
    if _USE_NATIVE:
        from signal_engine_native import HAVE_NATIVE  # type: ignore[import-not-found]
        from signal_engine_native import ema as _native_ema  # type: ignore[import-not-found]
        from signal_engine_native import sma as _native_sma  # type: ignore[import-not-found]

        # NOTE: `atr` and `williams_vix_fix` Rust ports exist (see
        # crates/signal-engine/src/lib.rs) but are not yet wired into the Python
        # facade because their Python implementations call into `ta` (ATR) and
        # already vectorized pandas (WVF) respectively. Wire when profiling shows
        # them as a hot path. The crate-side unit tests already verify numerical
        # equivalence so the wiring will be a no-op behavioural change.
        _NATIVE_AVAILABLE = HAVE_NATIVE
    else:
        _NATIVE_AVAILABLE = False
except ImportError:  # pragma: no cover - extension is optional
    _NATIVE_AVAILABLE = False


def sma(close: pd.Series, period: int = 20) -> pd.Series:
    if _NATIVE_AVAILABLE:
        arr = np.ascontiguousarray(close.to_numpy(dtype=np.float64))
        return pd.Series(_native_sma(arr, period), index=close.index)
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int = 20) -> pd.Series:
    if _NATIVE_AVAILABLE:
        arr = np.ascontiguousarray(close.to_numpy(dtype=np.float64))
        return pd.Series(_native_ema(arr, period), index=close.index)
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    return RSIIndicator(close=close, window=period, fillna=False).rsi()


def bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    bb = BollingerBands(close=close, window=period, window_dev=std, fillna=False)
    return pd.DataFrame(
        {
            "bb_mid": bb.bollinger_mavg(),
            "bb_upper": bb.bollinger_hband(),
            "bb_lower": bb.bollinger_lband(),
        }
    )


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    m = MACD(close=close, window_slow=slow, window_fast=fast, window_sign=signal, fillna=False)
    return pd.DataFrame(
        {"macd": m.macd(), "macd_signal": m.macd_signal(), "macd_hist": m.macd_diff()}
    )


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return ADXIndicator(high=high, low=low, close=close, window=period, fillna=False).adx()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return AverageTrueRange(
        high=high, low=low, close=close, window=period, fillna=False
    ).average_true_range()


def williams_vix_fix(close: pd.Series, low: pd.Series | None = None, period: int = 22) -> pd.Series:
    """Williams VIX Fix, a price-only volatility/exhaustion proxy.

    Formula: `(highest_close(period) - low) / highest_close(period) * 100`.
    A spike means price has probed far below the recent high, which is useful as
    a capitulation filter for failed-breakdown long setups.
    """
    low_ser = close if low is None else low
    highest_close = close.rolling(period, min_periods=period).max()
    return ((highest_close - low_ser) / highest_close) * 100


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Anchored intraday VWAP. Resets each calendar day from the index."""
    typical = (high + low + close) / 3
    pv = (typical * volume).groupby(volume.index.date).cumsum()
    vol = volume.groupby(volume.index.date).cumsum()
    return pv / vol
