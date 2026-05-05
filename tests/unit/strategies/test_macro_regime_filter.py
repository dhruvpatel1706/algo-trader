"""macro_regime_filter classifier unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies.macro_regime_filter import (
    RegimeClassification,
    classify_regime,
)


def _frame_from_close(close: pd.Series) -> pd.DataFrame:
    """Build a minimal OHLCV frame from a close series (matches fixture style
    used in test_ma_pullback_trend.py)."""
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=close.index)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def _strong_uptrend_low_vol(n: int = 260) -> pd.DataFrame:
    """Steady upward drift with no noise -> well above 200 SMA, rising slope,
    near-zero realized vol -> risk_on."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    # Tiny drift compounded daily keeps vol near zero while keeping a rising trend.
    close = pd.Series(100.0 * (1.0005 ** np.arange(n)), index=idx, dtype=float)
    return _frame_from_close(close)


def _strong_downtrend_high_vol(n: int = 260) -> pd.DataFrame:
    """Long uptrend followed by a sharp, noisy crash so:
      - close < SMA200
      - SMA200 has rolled over (today < 21 bars ago)
      - 20-day realized vol annualizes well above 30%.
    """
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    rng = np.random.default_rng(42)

    # Phase 1: gentle uptrend so SMA200 lifts to a high level.
    n_up = n - 60
    up = 100.0 + np.arange(n_up) * 0.2

    # Phase 2: violent crash with large daily moves to push realized vol above 30%.
    n_down = n - n_up
    shocks = rng.normal(loc=-0.04, scale=0.05, size=n_down)  # ~4% daily losses, 5% sigma
    last_up = up[-1]
    down = last_up * np.cumprod(1.0 + shocks)

    close = pd.Series(np.concatenate([up, down]), index=idx, dtype=float)
    return _frame_from_close(close)


_MIXED_TAIL = 30  # length of the chop phase used by _mixed_regime


def _mixed_regime(n: int = 260) -> pd.DataFrame:
    """Long benign uptrend (above SMA200, rising) followed by a short, mild
    chop that lifts annualized vol just over the 20% risk_on threshold but
    keeps price above the 200 SMA. That mix matches no clean branch -> transition."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")

    # Phase 1: smooth uptrend so price ends well above SMA200 and slope is rising.
    n_up = n - _MIXED_TAIL
    up = 100.0 + np.arange(n_up) * 0.5

    # Phase 2: deterministic alternating +/- moves so 20-day annualized vol
    # lands above 20% but below 30% (between the two thresholds). Mean drift
    # is zero, so price stays close to the level reached in phase 1.
    last_up = up[-1]
    shocks = np.array(
        [0.015 if i % 2 == 0 else -0.015 for i in range(_MIXED_TAIL)],
        dtype=float,
    )
    tail = last_up * np.cumprod(1.0 + shocks)

    close = pd.Series(np.concatenate([up, tail]), index=idx, dtype=float)
    return _frame_from_close(close)


def _too_few_bars(n: int = 50) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.arange(n) * 0.2, index=idx, dtype=float)
    return _frame_from_close(close)


# --- tests --------------------------------------------------------------------


def test_classify_regime_returns_dataclass_with_all_fields():
    """Smoke test: every documented field is populated and well-typed."""
    res = classify_regime({"SPY": _strong_uptrend_low_vol()})
    assert isinstance(res, RegimeClassification)
    assert res.label in {"risk_on", "risk_off", "transition"}
    assert 0.0 <= res.confidence <= 1.0
    assert isinstance(res.vix_proxy, float)
    assert isinstance(res.sma_distance, float)
    assert isinstance(res.sma_slope_positive, (bool, np.bool_))
    assert isinstance(res.timestamp, pd.Timestamp)


def test_strong_uptrend_low_vol_classifies_risk_on():
    res = classify_regime({"SPY": _strong_uptrend_low_vol()})
    assert res.label == "risk_on"
    assert res.confidence >= 0.7
    assert res.sma_slope_positive is True
    assert res.sma_distance > 0
    assert res.vix_proxy < 20.0


def test_strong_downtrend_high_vol_classifies_risk_off():
    res = classify_regime({"SPY": _strong_downtrend_high_vol()})
    assert res.label == "risk_off"
    assert res.confidence >= 0.7
    assert res.sma_slope_positive is False
    assert res.sma_distance < 0
    assert res.vix_proxy > 30.0


def test_mixed_regime_classifies_transition():
    res = classify_regime({"SPY": _mixed_regime()})
    assert res.label == "transition"
    # Transition is the medium-confidence default.
    assert res.confidence < 0.8


def test_insufficient_bars_returns_transition_low_confidence():
    res = classify_regime({"SPY": _too_few_bars()})
    assert res.label == "transition"
    assert res.confidence <= 0.3
    # Diagnostics should still be present (NaN allowed when undecidable).
    assert isinstance(res.timestamp, pd.Timestamp)


def test_empty_bars_returns_transition_low_confidence():
    res = classify_regime({})
    assert res.label == "transition"
    assert res.confidence <= 0.3


def test_falls_back_to_first_ticker_when_spy_missing():
    """No SPY key -> use the first available frame deterministically."""
    res = classify_regime({"QQQ": _strong_uptrend_low_vol()})
    assert res.label == "risk_on"
