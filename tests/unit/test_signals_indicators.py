"""Indicator smoke tests on synthetic OHLCV."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.signals.indicators import adx, atr, bollinger_bands, macd, rsi


def _ohlcv(n=200, seed=0):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + rng.standard_normal(n).cumsum(), name="close")
    high = close + np.abs(rng.standard_normal(n))
    low = close - np.abs(rng.standard_normal(n))
    volume = pd.Series(rng.integers(1_000_000, 5_000_000, n))
    return high, low, close, volume


def test_rsi_in_zero_to_hundred():
    _, _, c, _ = _ohlcv()
    r = rsi(c, period=14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_bollinger_bands_columns_and_ordering():
    _, _, c, _ = _ohlcv()
    bb = bollinger_bands(c).dropna()
    assert set(bb.columns) == {"bb_mid", "bb_upper", "bb_lower"}
    assert (bb["bb_upper"] >= bb["bb_mid"]).all()
    assert (bb["bb_lower"] <= bb["bb_mid"]).all()


def test_macd_columns():
    _, _, c, _ = _ohlcv()
    m = macd(c)
    assert set(m.columns) == {"macd", "macd_signal", "macd_hist"}


def test_adx_in_zero_to_hundred():
    h, low, c, _ = _ohlcv()
    a = adx(h, low, c).dropna()
    assert (a >= 0).all() and (a <= 100).all()


def test_atr_non_negative_and_positive_after_warmup():
    # ta library returns 0.0 for the warm-up period instead of NaN; check both regions.
    h, low, c, _ = _ohlcv()
    a = atr(h, low, c)
    assert (a >= 0).all()
    assert (a.iloc[20:] > 0).all()  # past warm-up, ATR must be strictly positive
