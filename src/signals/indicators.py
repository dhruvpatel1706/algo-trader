"""Indicator helpers (thin wrappers over the `ta` library)."""

from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands


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


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Anchored intraday VWAP. Resets each calendar day from the index."""
    typical = (high + low + close) / 3
    pv = (typical * volume).groupby(volume.index.date).cumsum()
    vol = volume.groupby(volume.index.date).cumsum()
    return pv / vol
