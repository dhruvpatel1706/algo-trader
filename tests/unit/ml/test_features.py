"""Tests for src.ml.features."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.ml.features import BASE_FEATURE_COLUMNS, build_features


def _synthetic_ohlcv(n: int = 200, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 + rng.standard_normal(n).cumsum(), index=idx)
    high = close + np.abs(rng.standard_normal(n))
    low = close - np.abs(rng.standard_normal(n))
    volume = pd.Series(rng.integers(1_000_000, 5_000_000, n), index=idx)
    return pd.DataFrame(
        {"close": close, "high": high, "low": low, "volume": volume}, index=idx
    )


def test_build_features_basic_columns():
    bars = {"SPY": _synthetic_ohlcv()}
    feats = build_features(bars)
    assert not feats.empty
    assert feats.index.names == ["symbol", "timestamp"]
    for col in BASE_FEATURE_COLUMNS:
        assert col in feats.columns, f"missing feature column: {col}"


def test_build_features_asof_truncates():
    bars = {"SPY": _synthetic_ohlcv()}
    cutoff = pd.Timestamp("2024-04-01")
    feats = build_features(bars, asof=cutoff)
    timestamps = feats.index.get_level_values("timestamp")
    assert timestamps.max() <= cutoff


def test_build_features_optional_alt_data_columns():
    bars = {"SPY": _synthetic_ohlcv(n=120)}

    def sentiment(symbol, ts):
        return 0.5

    def insider(symbol, ts):
        return 1.0

    def regime(symbol, ts):
        return "bull" if ts.month >= 3 else "bear"

    feats = build_features(
        bars,
        sentiment_lookup=sentiment,
        insider_lookup=insider,
        regime_lookup=regime,
    )
    assert "sentiment_24h" in feats.columns
    assert "insider_buy_score" in feats.columns
    assert "regime_bull" in feats.columns
    assert "regime_bear" in feats.columns
    # One-hot rows sum to 1 wherever a regime label was returned.
    one_hot_sum = feats[["regime_bull", "regime_bear"]].sum(axis=1)
    assert (one_hot_sum == 1).all()


def test_build_features_missing_high_low_falls_back_to_close():
    df = _synthetic_ohlcv(n=100).drop(columns=["high", "low"])
    feats = build_features({"SPY": df})
    assert not feats.empty
    # Indicators that need high/low should still produce numbers (or warmup NaN).
    assert "atr" in feats.columns


def test_build_features_handles_empty_input():
    feats = build_features({})
    assert feats.empty
    feats = build_features({"SPY": pd.DataFrame()})
    assert feats.empty


def test_build_features_alt_lookup_failure_becomes_nan():
    bars = {"SPY": _synthetic_ohlcv(n=80)}

    def boom(symbol, ts):
        raise RuntimeError("source unreachable")

    feats = build_features(bars, sentiment_lookup=boom)
    assert "sentiment_24h" in feats.columns
    assert feats["sentiment_24h"].isna().all()


def test_build_features_warmup_nan_then_values():
    bars = {"SPY": _synthetic_ohlcv(n=300, seed=2)}
    feats = build_features(bars)
    # rsi (14-period) should have NaN at the start and finite at the end.
    rsi_series = feats["rsi"].dropna()
    assert len(rsi_series) > 0
    assert np.isfinite(rsi_series).all()
