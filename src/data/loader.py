"""Market-data loader (Alpaca daily bars + parquet cache, yfinance fallback).

yfinance is for prototyping only. Sizing / risk decisions in production must use
broker-grade data. Loud warning when the fallback fires.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import PROJECT_ROOT, get_settings

_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(symbol: str, timeframe: str) -> Path:
    return _CACHE_DIR / f"{symbol.upper()}_{timeframe}.parquet"


def load_daily_bars(
    symbols: Iterable[str],
    start: date,
    end: date,
    *,
    use_cache: bool = True,
    fallback_to_yfinance: bool = True,
) -> dict[str, pd.DataFrame]:
    """Daily OHLCV per symbol between `start` and `end`. Tries Alpaca, then yfinance."""
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = _cache_path(sym, "1d")
        if use_cache and path.exists():
            df = pd.read_parquet(path)
            df = df.loc[(df.index.date >= start) & (df.index.date <= end)]
            if not df.empty:
                out[sym] = df
                continue

        df = _fetch_alpaca(sym, start, end)
        if (df is None or df.empty) and fallback_to_yfinance:
            warnings.warn(
                f"Falling back to yfinance for {sym}. Not for production sizing.",
                stacklevel=2,
            )
            df = _fetch_yfinance(sym, start, end)
        if df is not None and not df.empty:
            df.to_parquet(path)
            out[sym] = df
    return out


def _fetch_alpaca(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    s = get_settings()
    if not (s.ALPACA_API_KEY and s.ALPACA_SECRET_KEY):
        return None
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        return None

    client = StockHistoricalDataClient(s.ALPACA_API_KEY, s.ALPACA_SECRET_KEY)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time()),
        end=datetime.combine(end, datetime.min.time()),
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if df is None or df.empty:
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level=0)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def _fetch_yfinance(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    import yfinance as yf

    # auto_adjust=True applies the split/dividend adjustment factor backward through history,
    # so a historical close pre-split is divided by the split ratio. Without this, a 10:1
    # split prints as a ~-90% gap that triggers ATR-based stops on every affected day.
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index, utc=True)
    return df
