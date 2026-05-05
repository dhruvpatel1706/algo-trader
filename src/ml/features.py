"""Point-in-time feature builder for the ML overlay.

The ML overlay scores signals after the mechanical strategies emit them; it does
not generate signals. The feature frame produced here feeds both training
(``src.ml.train``) and live inference (``src.ml.predict``).

All features are POINT-IN-TIME. When ``asof`` is provided, every series is
truncated to the last bar with timestamp <= asof BEFORE indicators are computed.
This keeps the look-ahead surface zero. Indicators that need a look-back window
return NaN until enough history accumulates (matching the underlying ``ta``
library's ``min_periods`` semantics) — downstream code is expected to drop NaN
rows before training/inference.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from src.signals.indicators import (
    adx,
    atr,
    bollinger_bands,
    ema,
    rsi,
    sma,
    williams_vix_fix,
)

# Public column schema (excluding the optional alt-data and regime one-hot
# columns, which are appended only when their lookups are supplied).
TECH_FEATURES: tuple[str, ...] = (
    "rsi",
    "adx",
    "atr",
    "bb_width",
    "ema_distance_pct",
    "sma_distance_pct",
    "wvf",
)
RETURN_FEATURES: tuple[str, ...] = ("ret_1d", "ret_5d", "ret_21d", "ret_63d")
VOL_FEATURES: tuple[str, ...] = ("realized_vol_21d", "realized_vol_63d")
CALENDAR_FEATURES: tuple[str, ...] = ("dow", "month", "days_to_eom", "fomc_proximity")

BASE_FEATURE_COLUMNS: tuple[str, ...] = (
    *TECH_FEATURES,
    *RETURN_FEATURES,
    *VOL_FEATURES,
    *CALENDAR_FEATURES,
)


def _days_to_eom(idx: pd.DatetimeIndex) -> np.ndarray:
    """Calendar days from each timestamp to the end of its month."""
    eom = idx + pd.offsets.MonthEnd(0)
    return (eom.normalize() - idx.normalize()).days.to_numpy()


def _fomc_proximity(idx: pd.DatetimeIndex) -> np.ndarray:
    """Coarse FOMC proximity proxy: distance (in days) to the closest mid-month
    third Wednesday clamped to [0, 7]. We avoid bundling a real FOMC calendar
    here — strategies can override via the ``regime_lookup`` callable when more
    precision is needed.
    """
    # Third Wednesday of every month in the index range.
    months = pd.date_range(
        start=idx.min().to_period("M").to_timestamp(),
        end=(idx.max() + pd.offsets.MonthBegin(1)).to_period("M").to_timestamp(),
        freq="MS",
    )
    third_weds: list[pd.Timestamp] = []
    for m in months:
        # Find the first Wednesday on/after the 1st, then add 14 days.
        first_wed_offset = (2 - m.weekday()) % 7
        third_weds.append(m + pd.Timedelta(days=first_wed_offset + 14))
    third_weds_arr = np.array([t.value for t in third_weds], dtype="int64")
    out = np.empty(len(idx), dtype="float64")
    idx_vals = idx.asi8
    for i, t in enumerate(idx_vals):
        diff_days = np.abs(third_weds_arr - t) / (24 * 3600 * 1_000_000_000)
        out[i] = float(min(diff_days.min(), 7.0))
    return out


def _frame_for_symbol(
    df: pd.DataFrame,
    asof: pd.Timestamp | None,
) -> pd.DataFrame:
    """Compute the per-symbol feature block. Internal helper."""
    if df.empty:
        return pd.DataFrame()

    # Coerce required OHLCV columns; fall back gracefully when low/high missing.
    work = df.copy()
    if asof is not None:
        work = work.loc[work.index <= asof]
    if work.empty:
        return pd.DataFrame()

    close = work["close"].astype("float64")
    high = work["high"].astype("float64") if "high" in work.columns else close
    low = work["low"].astype("float64") if "low" in work.columns else close

    out = pd.DataFrame(index=work.index)

    # Technical indicators -- all return NaN until min_periods reached.
    out["rsi"] = rsi(close, period=14)
    out["adx"] = adx(high, low, close, period=14)
    out["atr"] = atr(high, low, close, period=14)
    bb = bollinger_bands(close, period=20, std=2.0)
    out["bb_width"] = (bb["bb_upper"] - bb["bb_lower"]) / bb["bb_mid"].replace(0, np.nan)
    ema_20 = ema(close, period=20)
    sma_50 = sma(close, period=50)
    out["ema_distance_pct"] = (close - ema_20) / ema_20
    out["sma_distance_pct"] = (close - sma_50) / sma_50
    out["wvf"] = williams_vix_fix(close, low, period=22)

    # Returns at multiple horizons.
    log_close = np.log(close.replace(0, np.nan))
    out["ret_1d"] = log_close.diff(1)
    out["ret_5d"] = log_close.diff(5)
    out["ret_21d"] = log_close.diff(21)
    out["ret_63d"] = log_close.diff(63)

    # Realized volatility.
    daily_ret = log_close.diff(1)
    out["realized_vol_21d"] = daily_ret.rolling(21, min_periods=21).std() * np.sqrt(252)
    out["realized_vol_63d"] = daily_ret.rolling(63, min_periods=63).std() * np.sqrt(252)

    # Calendar features.
    idx = work.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx)
    out["dow"] = idx.dayofweek.astype("int64")
    out["month"] = idx.month.astype("int64")
    out["days_to_eom"] = _days_to_eom(idx)
    out["fomc_proximity"] = _fomc_proximity(idx)

    return out


def build_features(
    bars: dict[str, pd.DataFrame],
    asof: pd.Timestamp | None = None,
    sentiment_lookup: Callable[[str, pd.Timestamp], float] | None = None,
    insider_lookup: Callable[[str, pd.Timestamp], float] | None = None,
    regime_lookup: Callable[[str, pd.Timestamp], str] | None = None,
) -> pd.DataFrame:
    """Build a feature frame indexed by ``(symbol, timestamp)``.

    Parameters
    ----------
    bars
        Mapping of symbol -> OHLCV DataFrame. Each frame must have a
        ``DatetimeIndex`` and at minimum a ``close`` column. ``high``/``low``
        fall back to ``close`` when absent (degraded mode).
    asof
        If provided, every per-symbol block is truncated to ``index <= asof``
        BEFORE indicator computation, preserving point-in-time semantics.
    sentiment_lookup, insider_lookup, regime_lookup
        Optional alt-data callables. Each receives ``(symbol, timestamp)`` and
        returns a numeric value (sentiment/insider) or a regime label string
        (regime). The regime callable produces one-hot ``regime_<label>``
        columns. ``NaN``/``None`` returns are tolerated and propagated as NaN.

    Returns
    -------
    pd.DataFrame
        Multi-indexed (``symbol``, ``timestamp``) frame whose columns include
        the technical, return, volatility, calendar features and optional
        alt-data columns. NaN warm-up rows are PRESERVED — callers decide their
        own dropna policy.
    """
    blocks: list[pd.DataFrame] = []
    regime_labels: set[str] = set()

    for symbol, df in bars.items():
        block = _frame_for_symbol(df, asof)
        if block.empty:
            continue

        # Optional alt-data columns are evaluated row-wise so the lookup sees
        # the per-bar timestamp and can serve point-in-time values from its own
        # store.
        if sentiment_lookup is not None:
            block["sentiment_24h"] = [
                _safe_call(sentiment_lookup, symbol, ts) for ts in block.index
            ]
        if insider_lookup is not None:
            block["insider_buy_score"] = [
                _safe_call(insider_lookup, symbol, ts) for ts in block.index
            ]
        if regime_lookup is not None:
            labels = [_safe_call(regime_lookup, symbol, ts) for ts in block.index]
            block["__regime"] = pd.Series(labels, index=block.index, dtype="object")
            regime_labels.update(lbl for lbl in labels if isinstance(lbl, str))

        block.insert(0, "symbol", symbol)
        block.index.name = "timestamp"
        blocks.append(block.reset_index().set_index(["symbol", "timestamp"]))

    if not blocks:
        return pd.DataFrame()

    out = pd.concat(blocks, axis=0).sort_index()

    if regime_lookup is not None and regime_labels:
        for lbl in sorted(regime_labels):
            col = f"regime_{lbl}"
            out[col] = (out["__regime"] == lbl).astype("int64")
        out = out.drop(columns=["__regime"])
    elif "__regime" in out.columns:
        out = out.drop(columns=["__regime"])

    return out


def _safe_call(fn: Callable[[str, pd.Timestamp], Any], symbol: str, ts: pd.Timestamp) -> Any:
    """Call an alt-data lookup; turn errors and ``None`` into NaN."""
    try:
        v = fn(symbol, ts)
    except Exception:
        return np.nan
    if v is None:
        return np.nan
    return v
