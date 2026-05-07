"""ema_ribbon_compression strategy unit tests.

The strategy fires when:
  1. All five Fibonacci EMAs (8/13/21/34/55) compress into a band tighter
     than 0.5% of price for ≥5 consecutive bars,
  2. the next bar closes ≥0.8% above the ribbon's max, AND
  3. ADX ≥ 18.

We synthesise three regimes:
  - flat-to-rising-to-spike: ribbon compresses on a flat run, then a final
    spike pushes close above ribbon_max → expect a long signal.
  - flat without breakout: ribbon compressed but no breakout bar → no
    signal.
  - choppy: spread too wide for a compression streak → no signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.ema_ribbon_compression import (
    EmaRibbonCompression,
    EmaRibbonCompressionParams,
)


def _flat_then_breakout(n: int = 240, breakout_pct: float = 0.015) -> pd.DataFrame:
    """Slow steady drift + single final-bar breakout.

    The strategy must satisfy three conditions simultaneously:
      - All five EMAs tightly grouped (compressed ribbon, < 0.5% spread).
      - ADX above 18 (sustained directionality).
      - Final close above ribbon_max + breakout_pct.

    A naive "fast trend → flat consolidation → breakout" series fails: ADX
    decays in the flat zone (Wilder's smoothing has a ~14-bar half-life), so
    by the time EMA-55 catches up to the shorter EMAs, the trend gate is
    already dead. We use a very gentle steady drift (~0.005/bar) for the
    full warm-up: EMA-55's steady-state lag is tiny (~0.14) so the ribbon
    spread sits well under the 0.5% threshold, and every bar is +DM so ADX
    climbs to ~99 and stays there. The final bar then breaks out above the
    ribbon by ``breakout_pct``.
    """
    idx = pd.date_range("2024-01-02", periods=n, freq="B")

    slope = 0.005
    closes = np.zeros(n)
    closes[: n - 1] = 100.0 + np.arange(n - 1) * slope

    # Single final-bar breakout. Keeping it to one bar means the ribbon at
    # evaluation time is still tight (the new sample only nudges EMA-8 a
    # little; longer EMAs barely move).
    last_pre_break = closes[n - 2]
    closes[n - 1] = last_pre_break * (1.0 + breakout_pct)

    close = pd.Series(closes, index=idx, dtype=float)
    high = close + 0.4
    low = close - 0.4
    volume = pd.Series(np.full(n, 1_000_000.0), index=idx)
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume}
    )


def _flat_without_breakout(n: int = 240) -> pd.DataFrame:
    """Same flat regime but the final bar stays inside the ribbon."""
    df = _flat_then_breakout(n=n, breakout_pct=0.0001)
    return df


def _choppy_regime(n: int = 240) -> pd.DataFrame:
    """Sustained whippy regime: ribbon never compresses tightly."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    rng = np.random.default_rng(99)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.5, n))
    close = pd.Series(closes, index=idx, dtype=float)
    high = close + 0.6
    low = close - 0.6
    volume = pd.Series(np.full(n, 1_000_000.0), index=idx)
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume}
    )


# ---------------------------------------------------------------------------
# Trigger / no-trigger
# ---------------------------------------------------------------------------


def test_compression_breakout_emits_long_signal():
    df = _flat_then_breakout()
    s = EmaRibbonCompression()
    sigs = s.generate_signals({"BTCUSDT": df})
    assert len(sigs) == 1, f"expected exactly 1 signal, got {sigs}"
    sig = sigs[0]
    assert sig.symbol == "BTCUSDT"
    assert sig.side == "buy"
    # Stop is the ribbon midpoint, must be below entry.
    assert float(sig.stop) < float(sig.entry)
    # Target is 3R by default.
    risk = float(sig.entry) - float(sig.stop)
    expected_target = float(sig.entry) + 3.0 * risk
    assert abs(float(sig.target) - expected_target) < 0.01
    assert sig.strategy_tag == "ema_ribbon_compression"


def test_no_breakout_no_signal():
    df = _flat_without_breakout()
    s = EmaRibbonCompression()
    sigs = s.generate_signals({"BTCUSDT": df})
    assert sigs == []


def test_choppy_regime_no_signal():
    df = _choppy_regime()
    s = EmaRibbonCompression()
    sigs = s.generate_signals({"BTCUSDT": df})
    assert sigs == []


def test_short_history_no_signal():
    """Series shorter than the warm-up returns nothing rather than crashing."""
    short_df = _flat_then_breakout(n=50)
    s = EmaRibbonCompression()
    sigs = s.generate_signals({"BTCUSDT": short_df})
    assert sigs == []


# ---------------------------------------------------------------------------
# Parameter sensitivity (sanity checks, not full coverage)
# ---------------------------------------------------------------------------


def test_tighter_breakout_pct_filters_marginal_breaks():
    """A breakout that just barely clears the default threshold should NOT
    fire when breakout_pct is raised above the actual move."""
    df = _flat_then_breakout(breakout_pct=0.012)  # 1.2% breakout
    # Default 0.8% threshold catches it.
    assert len(EmaRibbonCompression().generate_signals({"BTCUSDT": df})) == 1
    # Raising the threshold to 1.5% rejects it.
    strict = EmaRibbonCompression(
        EmaRibbonCompressionParams(breakout_pct=0.015)
    )
    assert strict.generate_signals({"BTCUSDT": df}) == []


def test_tighter_compression_threshold_filters_loose_squeezes():
    """If compression_max_spread_pct is set tighter than the synthesized
    flat region's natural spread, the rule rejects it."""
    df = _flat_then_breakout()
    # Force an unrealistically tight compression criterion.
    paranoid = EmaRibbonCompression(
        EmaRibbonCompressionParams(compression_max_spread_pct=0.00001)
    )
    assert paranoid.generate_signals({"BTCUSDT": df}) == []


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_ema_ribbon_compression_loads_via_registry():
    s = load_strategy("ema_ribbon_compression")
    assert isinstance(s, EmaRibbonCompression)
    universe = s.universe()
    # Wired to crypto_majors in docs/universes.yaml; structure assertion so
    # universe expansions don't ripple back to this test.
    assert "BTCUSDT" in universe
    assert "ETHUSDT" in universe
    assert all(sym.endswith("USDT") for sym in universe)


def test_ema_ribbon_compression_default_params_match_proposal():
    """Pin the defaults to the Researcher session's proposal in
    docs/improvements/strategies/ema_ribbon_compression_breakout.md so
    backtest results stay reproducible."""
    p = EmaRibbonCompressionParams()
    assert p.ema_periods == (8, 13, 21, 34, 55)
    assert p.compression_max_spread_pct == 0.005
    assert p.compression_bars == 5
    assert p.breakout_pct == 0.008
    assert p.target_r == 3.0
    assert p.adx_min == 18.0
