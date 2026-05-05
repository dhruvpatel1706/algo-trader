"""macro_regime_filter - SPY-based daily regime CLASSIFIER (not a Strategy).

Outputs a daily regime label (`risk_on`, `risk_off`, `transition`) intended to
be consumed by other strategies (notably `mr_etf` and `ma_pullback_trend`) so
they can scale exposure or skip entries when the broad regime is hostile.

This module is deliberately not a `Strategy` subclass: it does not emit
`Signal` objects, has no entry/stop/target, no I/O, no state. It is a pure
function `classify_regime(bars)` that returns a `RegimeClassification`.

Inputs (computed from SPY bars; falls back to first ticker if SPY missing):
  - VIX proxy  : annualized 20-day realized vol of SPY closes, in percent
  - SMA distance: (close - SMA200) / SMA200
  - SMA slope  : SMA200 today vs SMA200 21 bars ago (rising or not)

Decision (matches the spec):
  - risk_on   : above 200 SMA AND SMA slope rising AND vix_proxy < 20
  - risk_off  : below 200 SMA AND SMA slope NOT rising AND vix_proxy > 30
  - transition: anything in between (default), low confidence
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Literal

import numpy as np
import pandas as pd

from src.signals.indicators import sma

# --- Configuration constants (kept inline; this is a single-file utility) -----

_SMA_PERIOD = 200
_SLOPE_LOOKBACK = 20  # SMA200(t) vs SMA200(t - SLOPE_LOOKBACK)
_VOL_LOOKBACK = 20
_TRADING_DAYS = 252
_VIX_LOW = 20.0  # below this counts as low-vol
_VIX_HIGH = 30.0  # above this counts as high-vol
# Need at least the SMA period plus the slope lookback plus a couple of bars
# of vol history to produce a meaningful classification.
_MIN_BARS = _SMA_PERIOD + _SLOPE_LOOKBACK + 2


@dataclass(frozen=True, slots=True)
class RegimeClassification:
    label: Literal["risk_on", "risk_off", "transition"]
    confidence: float  # 0..1
    vix_proxy: float  # annualized realized vol %
    sma_distance: float  # fraction (close - SMA200) / SMA200
    sma_slope_positive: bool
    timestamp: pd.Timestamp


def _realized_vol_annualized_pct(close: pd.Series, lookback: int = _VOL_LOOKBACK) -> float:
    """Annualized realized vol over `lookback` daily log returns, in percent."""
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < lookback:
        return float("nan")
    sigma = float(log_ret.iloc[-lookback:].std(ddof=1))
    return sigma * sqrt(_TRADING_DAYS) * 100.0


def _pick_spy(bars: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame] | None:
    """Pick SPY if present; otherwise the first ticker (deterministic fallback)."""
    if not bars:
        return None
    if "SPY" in bars:
        return "SPY", bars["SPY"]
    sym = next(iter(bars))
    return sym, bars[sym]


def classify_regime(bars: dict[str, pd.DataFrame]) -> RegimeClassification:
    """Classify the current macro regime from SPY-like daily bars.

    Pure function. Reads only the latest values; no side effects.
    """
    picked = _pick_spy(bars)

    # Empty input or unusable frame -> low-confidence transition with NaN diagnostics.
    if picked is None:
        return RegimeClassification(
            label="transition",
            confidence=0.2,
            vix_proxy=float("nan"),
            sma_distance=float("nan"),
            sma_slope_positive=False,
            timestamp=pd.Timestamp.now(tz="UTC").normalize(),
        )

    _, df = picked

    if "close" not in df.columns or len(df) < _MIN_BARS:
        ts = df.index[-1] if len(df) else pd.Timestamp.now(tz="UTC").normalize()
        return RegimeClassification(
            label="transition",
            confidence=0.2,
            vix_proxy=float("nan"),
            sma_distance=float("nan"),
            sma_slope_positive=False,
            timestamp=ts,
        )

    close = df["close"]
    sma200 = sma(close, period=_SMA_PERIOD)

    close_now = float(close.iloc[-1])
    sma_now = float(sma200.iloc[-1])
    sma_then = float(sma200.iloc[-1 - _SLOPE_LOOKBACK])
    vix_proxy = _realized_vol_annualized_pct(close, _VOL_LOOKBACK)
    ts = df.index[-1]

    # Defensive: any NaN means we cannot decide -> low-confidence transition.
    if any(np.isnan(v) for v in (sma_now, sma_then, vix_proxy)) or sma_now <= 0:
        return RegimeClassification(
            label="transition",
            confidence=0.3,
            vix_proxy=vix_proxy if not np.isnan(vix_proxy) else float("nan"),
            sma_distance=float("nan"),
            sma_slope_positive=False,
            timestamp=ts,
        )

    sma_distance = (close_now - sma_now) / sma_now
    above_200 = close_now > sma_now
    slope_positive = sma_now > sma_then

    if above_200 and slope_positive and vix_proxy < _VIX_LOW:
        label: Literal["risk_on", "risk_off", "transition"] = "risk_on"
        confidence = 0.8
    elif (not above_200) and (not slope_positive) and vix_proxy > _VIX_HIGH:
        label = "risk_off"
        confidence = 0.8
    else:
        label = "transition"
        confidence = 0.5

    return RegimeClassification(
        label=label,
        confidence=confidence,
        vix_proxy=vix_proxy,
        sma_distance=sma_distance,
        sma_slope_positive=slope_positive,
        timestamp=ts,
    )
